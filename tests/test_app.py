from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from transcriber.app import create_app
from transcriber.config import Config
from transcriber.recap.voice import VOICES

from transcriber.providers.base import PermanentError

from .helpers import FakeLLM, FakeTranscriber, synth_audio

NOTES_REPLY = {
    "summary": [{"text": "Planned the week.", "cites": ["[00:00]"]}],
    "decisions": [], "action_items": [{"text": "Send the report.", "owner": "Sara", "due": None, "cites": []}],
    "open_questions": [], "topics": ["planning"],
}
CHECK_REPLY = {"verdicts": [{"id": "S1", "verdict": "supported", "reason": "ok"}], "missed": []}


@pytest.fixture
def wav(tmp_path):
    return synth_audio(tmp_path / "standup.wav", [("tone", 2.0)]).read_bytes()


def build(tmp_path, transcriber=None, writer=None, checker=None, ask_engine=None, voice=None, practice_llm=None):
    config = Config(groq_api_key=None, data_dir=tmp_path / "data", port=0)
    return create_app(config, transcriber=transcriber, writer=writer, checker=checker, ask_engine=ask_engine,
                      voice=voice, practice_llm=practice_llm, start_worker=False, privacy_allow_all=True)


@pytest.fixture
def env(tmp_path):
    fake = FakeTranscriber()
    writer = FakeLLM("cerebras:gpt-oss-120b", [NOTES_REPLY] * 10)
    checker = FakeLLM("gemini:gemini-3.8-flash", [CHECK_REPLY] * 10)
    app = build(tmp_path, fake, writer, checker)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, fake=fake, writer=writer, checker=checker,
                              data_dir=tmp_path / "data", worker=app.state.worker, store=app.state.store)


def upload(env, wav, name="standup.wav", **data):
    response = env.client.post("/api/recordings", files=[("files", (name, wav, "audio/wav"))], data=data)
    assert response.status_code == 201, response.text
    return response.json()[0]


def drain(env):
    while env.worker.run_once():
        pass


def detail(env, rid):
    return env.client.get(f"/api/recordings/{rid}").json()


# --- status and uploads

def without_languages(status: dict) -> dict:
    """The language menu is checked in test_config; the rest of the status is what these tests are about."""
    return {key: value for key, value in status.items() if key not in ("languages", "second_language")}


def test_status_reports_transcriber_and_notes_models(env):
    assert without_languages(env.client.get("/api/status").json()) == {
        "api_key_configured": True,
        "notes": {"writer": "cerebras:gpt-oss-120b", "checker": "gemini:gemini-3.8-flash"},
        "ask": "cerebras:gpt-oss-120b", "mixed": False}  # no Claude here, so Ask falls back to the notes writer


def test_without_any_models_uploads_wait_in_queue(tmp_path, wav):
    app = build(tmp_path)
    with TestClient(app) as client:
        assert without_languages(client.get("/api/status").json()) == {
            "api_key_configured": False, "notes": {"writer": None, "checker": None}, "ask": None, "mixed": False}
        response = client.post("/api/recordings", files=[("files", ("a.wav", wav, "audio/wav"))])
        assert response.json()[0]["status"] == "queued"
        assert app.state.worker is None


def test_upload_creates_queued_recording_and_subject(env, wav):
    rec = upload(env, wav, titles=["Daily standup"], subject="Team", language="es")
    assert (rec["title"], rec["subject"], rec["language"], rec["status"]) == ("Daily standup", "Team", "es", "queued")
    assert isinstance(rec["subject_id"], int)
    assert (env.data_dir / "recordings" / rec["id"] / "upload.wav").exists()
    assert env.client.get("/api/subjects").json() == [
        {"id": rec["subject_id"], "name": "Team", "description": "", "count": 1, "instructions": "",
             "notes_style": "lecture", "schedule": None}]


def test_upload_defaults(env, wav):
    env.client.put("/api/settings", json={"default_language": "es"})
    rec = upload(env, wav)
    assert rec["title"] == f"standup — {date.today():%Y-%m-%d}"
    assert (rec["language"], rec["subject"], rec["subject_id"]) == ("es", None, None)


def test_upload_rejects_unknown_language(env, wav):
    response = env.client.post("/api/recordings", files=[("files", ("a.wav", wav, "audio/wav"))],
                               data={"language": "fr"})
    assert response.status_code == 400


def test_unknown_recording_is_404(env):
    assert env.client.get("/api/recordings/nope").status_code == 404


# --- transcription then notes

def test_transcript_then_checked_notes(env, wav):
    rec = upload(env, wav)
    assert env.worker.run_once() is True  # transcription
    after_transcript = detail(env, rec["id"])
    assert after_transcript["status"] == "done"
    assert after_transcript["paragraphs"] == [{"start": 0.0, "text": "Chunk 1.", "speaker": None, "speaker_label": None}]
    assert after_transcript["notes"]["status"] == "queued"
    assert env.worker.run_once() is True  # notes
    body = detail(env, rec["id"])
    assert body["notes_status"] == "ready"
    notes = body["notes"]
    assert (notes["status"], notes["writer"], notes["checker"], notes["check_detail"]) == (
        "ready", "cerebras:gpt-oss-120b", "gemini:gemini-3.8-flash", None)
    assert notes["content"]["summary"][0]["verdict"] == "supported"
    assert notes["content"]["action_items"][0]["owner"] == "Sara"
    assert body["topics"] == [{"id": body["topics"][0]["id"], "name": "planning", "state": "suggested"}]
    assert body["my_notes"] == ""


def test_regenerate_notes(env, wav):
    rec = upload(env, wav)
    assert env.client.post(f"/api/recordings/{rec['id']}/notes/regenerate").status_code == 409  # no transcript
    drain(env)
    response = env.client.post(f"/api/recordings/{rec['id']}/notes/regenerate")
    assert response.json()["status"] == "queued"
    assert response.json()["content"]["summary"][0]["text"] == "Planned the week."  # old notes stay visible
    drain(env)
    assert len(env.writer.calls) == 2


def test_retranscribe_drops_old_notes(env, wav):
    rec = upload(env, wav)
    drain(env)
    response = env.client.post(f"/api/recordings/{rec['id']}/retranscribe", json={"language": "auto"})
    assert (response.json()["status"], response.json()["notes_status"]) == ("queued", None)
    assert detail(env, rec["id"])["notes"] is None
    drain(env)
    assert detail(env, rec["id"])["notes"]["status"] == "ready"
    assert env.fake.calls[-1].language is None


# --- organizing

def test_list_search_and_filters(env, wav):
    a = upload(env, wav, titles=["Client review"], subject="Client")
    b = upload(env, wav, titles=["Standup"], subject="Team")
    drain(env)

    def ids(**params):
        return [x["id"] for x in env.client.get("/api/recordings", params=params).json()]

    assert ids() == [b["id"], a["id"]]
    assert ids(q="client") == [a["id"]]
    assert ids(q="chunk 2") == [b["id"]]
    assert ids(q="send the report") == [b["id"], a["id"]]  # AI notes are searchable
    assert ids(subject_id=b["subject_id"]) == [b["id"]]
    topic = env.client.post(f"/api/recordings/{a['id']}/topics", json={"name": "Pricing"}).json()
    pricing = next(t["id"] for t in topic if t["name"] == "Pricing")
    assert ids(topic_id=pricing) == [a["id"]]


def test_patch_title_and_subject(env, wav):
    rec = upload(env, wav, subject="Team")
    response = env.client.patch(f"/api/recordings/{rec['id']}", json={"title": "  Renamed ", "subject": "Acme"})
    assert (response.json()["title"], response.json()["subject"]) == ("Renamed", "Acme")
    response = env.client.patch(f"/api/recordings/{rec['id']}", json={"subject": ""})
    assert response.json()["subject"] is None
    assert env.client.patch(f"/api/recordings/{rec['id']}", json={"title": " "}).status_code == 400


def test_move_recordings(env, wav):
    a, b = upload(env, wav), upload(env, wav)
    assert env.client.post("/api/recordings/move", json={"ids": [a["id"], b["id"]], "subject": "Acme"}).json() == {
        "moved": 2}
    assert {detail(env, r["id"])["subject"] for r in (a, b)} == {"Acme"}
    env.client.post("/api/recordings/move", json={"ids": [a["id"]], "subject": ""})
    assert detail(env, a["id"])["subject"] is None


def test_subjects_api(env):
    created = env.client.post("/api/subjects", json={"name": "Acme", "description": "Q4 rollout"})
    assert created.status_code == 201
    sid = created.json()["id"]
    assert env.client.post("/api/subjects", json={"name": "acme"}).status_code == 409
    assert env.client.post("/api/subjects", json={"name": " "}).status_code == 400
    env.client.post("/api/subjects", json={"name": "Beta"})
    patched = env.client.patch(f"/api/subjects/{sid}", json={"name": "Acme Corp", "description": "renewal"})
    assert (patched.json()["name"], patched.json()["description"]) == ("Acme Corp", "renewal")
    assert env.client.patch(f"/api/subjects/{sid}", json={"name": "beta"}).status_code == 409
    assert env.client.patch("/api/subjects/999", json={"name": "x"}).status_code == 404
    assert env.client.delete(f"/api/subjects/{sid}").status_code == 204
    assert [s["name"] for s in env.client.get("/api/subjects").json()] == ["Beta"]
    assert env.client.delete(f"/api/subjects/{sid}").status_code == 404


def test_topics_api(env, wav):
    rec = upload(env, wav)
    drain(env)  # AI suggested "planning"
    topics = detail(env, rec["id"])["topics"]
    planning = topics[0]["id"]
    assert env.client.get("/api/topics").json() == []  # suggestions are not listed
    assert env.client.put(f"/api/recordings/{rec['id']}/topics/{planning}").json()[0]["state"] == "accepted"
    assert env.client.get("/api/topics").json() == [{"id": planning, "name": "planning", "count": 1}]
    added = env.client.post(f"/api/recordings/{rec['id']}/topics", json={"name": "Budget"})
    assert added.status_code == 201
    budget = next(t["id"] for t in added.json() if t["name"] == "Budget")
    assert env.client.post(f"/api/recordings/{rec['id']}/topics", json={"name": " "}).status_code == 400
    assert env.client.patch(f"/api/topics/{budget}", json={"name": "Planning"}).json() == {"id": planning}
    assert env.client.get("/api/topics").json() == [{"id": planning, "name": "planning", "count": 1}]
    assert env.client.delete(f"/api/recordings/{rec['id']}/topics/{planning}").json() == []
    assert env.client.delete(f"/api/recordings/{rec['id']}/topics/{planning}").status_code == 404
    assert env.client.put(f"/api/recordings/{rec['id']}/topics/{planning}").status_code == 404
    assert env.client.delete(f"/api/topics/{planning}").status_code == 204
    assert env.client.patch(f"/api/topics/{planning}", json={"name": "x"}).status_code == 404


def test_my_notes(env, wav):
    rec = upload(env, wav)
    assert env.client.put(f"/api/recordings/{rec['id']}/my-notes", json={"text": "Call Sara"}).json() == {
        "my_notes": "Call Sara"}
    assert detail(env, rec["id"])["my_notes"] == "Call Sara"
    assert env.client.put("/api/recordings/nope/my-notes", json={"text": "x"}).status_code == 404


# --- v1 behaviour kept

def test_delete_forever_removes_files(env, wav):
    rec = upload(env, wav)
    assert env.client.delete(f"/api/recordings/{rec['id']}?forever=1").status_code == 204
    assert env.client.get(f"/api/recordings/{rec['id']}").status_code == 404
    assert not (env.data_dir / "recordings" / rec["id"]).exists()


def test_retry_only_for_failed(env, wav):
    rec = upload(env, wav)
    assert env.client.post(f"/api/recordings/{rec['id']}/retry").status_code == 409
    env.store.update_recording(rec["id"], status="failed", status_detail="boom")
    response = env.client.post(f"/api/recordings/{rec['id']}/retry")
    assert (response.json()["status"], response.json()["status_detail"]) == ("queued", None)


def test_retranscribe_validation(env, wav):
    rec = upload(env, wav)
    assert env.client.post(f"/api/recordings/{rec['id']}/retranscribe", json={}).status_code == 409
    drain(env)
    assert env.client.post(f"/api/recordings/{rec['id']}/retranscribe", json={"language": "xx"}).status_code == 400


def test_audio_supports_range_requests(env, wav):
    rec = upload(env, wav)
    assert env.client.get(f"/api/recordings/{rec['id']}/audio").status_code == 404
    drain(env)
    response = env.client.get(f"/api/recordings/{rec['id']}/audio", headers={"Range": "bytes=0-99"})
    assert response.status_code == 206 and len(response.content) == 100
    assert response.headers["content-type"] == "audio/flac"


def test_export(env, wav):
    rec = upload(env, wav, titles=["Daily standup"], subject="Team")
    assert env.client.get(f"/api/recordings/{rec['id']}/export").status_code == 404
    drain(env)
    txt = env.client.get(f"/api/recordings/{rec['id']}/export", params={"format": "txt"})
    assert txt.text == "Daily standup\n\nChunk 1.\n"
    assert txt.headers["content-disposition"] == 'attachment; filename="Daily standup.txt"'
    md = env.client.get(f"/api/recordings/{rec['id']}/export", params={"format": "md"}).text
    assert md.startswith("# Daily standup\n\n") and "Team" in md
    assert "## Notes" in md and "- Planned the week. [00:00]" in md and "[00:00] Chunk 1." in md
    assert env.client.get(f"/api/recordings/{rec['id']}/export", params={"format": "pdf"}).status_code == 422


def test_settings(env):
    assert env.client.get("/api/settings").json() == {
        "vocabulary": "", "default_language": "en", "vocabulary_max_chars": 450, "prefer_server": "0",
        "groq_hour_limit_sec": "7200", "groq_day_limit_sec": "28800",
        "recaps_on": "1", "recap_voice": "af_heart", "recap_speed": "1.1", "recap_voices": VOICES,
        "time_zone": "UTC", "scan_folders": "", "class_reminders": "1", "vault_folder": "", "default_privacy": "standard"}
    response = env.client.put("/api/settings", json={"vocabulary": "Zorvath", "default_language": "auto"})
    assert response.json()["vocabulary"] == "Zorvath"
    assert env.client.put("/api/settings", json={"default_language": "xx"}).status_code == 400


def test_restart_requeues_interrupted_jobs(tmp_path, wav):
    first = build(tmp_path, FakeTranscriber())
    with TestClient(first) as client:
        rid = client.post("/api/recordings", files=[("files", ("a.wav", wav, "audio/wav"))]).json()[0]["id"]
        first.state.store.update_recording(rid, status="transcribing")
    second = build(tmp_path, FakeTranscriber())
    with TestClient(second) as client:
        assert client.get(f"/api/recordings/{rid}").json()["status"] == "queued"


def test_index_page_is_served(env):
    response = env.client.get("/")
    assert response.status_code == 200
    assert "<title>Transcriber</title>" in response.text


def test_static_assets_are_served(env):
    assert env.client.get("/static/app.js").status_code == 200
    assert env.client.get("/static/styles.css").status_code == 200


def test_ui_files_are_revalidated_so_updates_show_up(env):
    assert env.client.get("/").headers["cache-control"] == "no-cache"
    assert env.client.get("/static/app.js").headers["cache-control"] == "no-cache"


# --- Ask chat and editing notes

ASK_REPLY = {"answer": "Sara sends it [00:00].", "proposed_notes": None}
PROPOSAL = {"summary": [{"text": "Planned the week.", "cites": ["[00:00]"]}, {"text": "Short week.", "cites": []}],
            "decisions": [], "action_items": [], "open_questions": [], "topics": []}


@pytest.fixture
def ask_env(tmp_path):
    asker = FakeLLM("claude-code", [ASK_REPLY, {"answer": "Added a point.", "proposed_notes": PROPOSAL}])
    writer = FakeLLM("groq:openai/gpt-oss-120b", [NOTES_REPLY] * 10)
    config = Config(groq_api_key=None, data_dir=tmp_path / "data", port=0)
    app = create_app(config, transcriber=FakeTranscriber(), writer=writer, checker=FakeLLM("c", [CHECK_REPLY] * 10),
                     ask_engine=asker, start_worker=False, privacy_allow_all=True)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, asker=asker, writer=writer, worker=app.state.worker,
                              store=app.state.store)


def test_status_reports_ask_engine(ask_env):
    assert ask_env.client.get("/api/status").json()["ask"] == "claude-code"


def test_chat_and_apply_a_proposal(ask_env, wav):
    rid = upload(ask_env, wav)["id"]
    assert ask_env.client.post(f"/api/recordings/{rid}/chat", json={"message": "Who?"}).status_code == 409
    drain(ask_env)
    first = ask_env.client.post(f"/api/recordings/{rid}/chat", json={"message": "Who sends the report?"}).json()
    assert [(m["role"], m["content"]) for m in first] == [("user", "Who sends the report?"),
                                                          ("assistant", "Sara sends it [00:00].")]
    assert (first[1]["engine"], first[1]["proposal"], first[1]["note"]) == ("claude-code", None, None)
    second = ask_env.client.post(f"/api/recordings/{rid}/chat", json={"message": "Add that it was short"}).json()
    proposal_msg = second[1]
    assert proposal_msg["proposal_state"] == "pending"
    assert "User: Who sends the report?\nAssistant: Sara sends it [00:00]." in ask_env.asker.calls[1]["user"]
    applied = ask_env.client.post(f"/api/recordings/{rid}/chat/{proposal_msg['id']}/apply").json()
    summary = applied["notes"]["content"]["summary"]
    assert [(i["text"], i["verdict"], i["edited"]) for i in summary] == [
        ("Planned the week.", "supported", False), ("Short week.", None, True)]
    assert applied["chat"][-1]["proposal_state"] == "applied"
    assert ask_env.client.post(f"/api/recordings/{rid}/chat/{proposal_msg['id']}/apply").status_code == 409
    assert ask_env.client.delete(f"/api/recordings/{rid}/chat").status_code == 204
    assert detail(ask_env, rid)["chat"] == []


def test_dismiss_proposal(ask_env, wav):
    rid = upload(ask_env, wav)["id"]
    drain(ask_env)
    ask_env.asker.replies = [{"answer": "Here.", "proposed_notes": PROPOSAL}]
    msg = ask_env.client.post(f"/api/recordings/{rid}/chat", json={"message": "Change it"}).json()[1]
    body = ask_env.client.post(f"/api/recordings/{rid}/chat/{msg['id']}/dismiss").json()
    assert body["chat"][-1]["proposal_state"] == "dismissed"
    assert len(body["notes"]["content"]["summary"]) == 1


def test_chat_falls_back_or_reports_failure(ask_env, wav):
    rid = upload(ask_env, wav)["id"]
    drain(ask_env)
    login = PermanentError("Claude isn't signed in. Open a terminal and run: claude auth login")
    ask_env.asker.replies = [login]
    ask_env.writer.replies = [{"answer": "From Groq.", "proposed_notes": None}]
    reply = ask_env.client.post(f"/api/recordings/{rid}/chat", json={"message": "Hi"}).json()[1]
    assert (reply["content"], reply["engine"]) == ("From Groq.", "groq:openai/gpt-oss-120b")
    assert "claude auth login" in reply["note"]
    ask_env.asker.replies = [login]
    ask_env.writer.replies = [PermanentError("Groq is down")]
    response = ask_env.client.post(f"/api/recordings/{rid}/chat", json={"message": "Again"})
    assert response.status_code == 502 and "claude auth login" in response.json()["detail"]
    assert len(detail(ask_env, rid)["chat"]) == 2  # the failed exchange is not saved
    assert ask_env.client.post(f"/api/recordings/{rid}/chat", json={"message": "  "}).status_code == 400


def test_edit_notes_by_hand(ask_env, wav):
    rid = upload(ask_env, wav)["id"]
    drain(ask_env)
    base = f"/api/recordings/{rid}/notes/items"
    edited = ask_env.client.put(f"{base}/S1", json={"text": "Planned the whole week."}).json()["content"]
    assert (edited["summary"][0]["text"], edited["summary"][0]["edited"], edited["summary"][0]["verdict"]) == (
        "Planned the whole week.", True, None)
    added = ask_env.client.post(base, json={"section": "decisions", "text": "Ship on Friday."}).json()["content"]
    assert added["decisions"][0]["id"] == "D1"
    action = ask_env.client.put(f"{base}/A1", json={"text": "Send it.", "owner": "Jamie", "due": "Monday"}).json()
    assert action["content"]["action_items"][0]["owner"] == "Jamie"
    assert ask_env.client.delete(f"{base}/D1").json()["content"]["decisions"] == []
    assert ask_env.client.put(f"{base}/Z9", json={"text": "x"}).status_code == 404
    assert ask_env.client.put(f"{base}/S1", json={"text": " "}).status_code == 400
    assert ask_env.client.post(base, json={"section": "missed", "text": "x"}).status_code == 400


def test_add_notes_by_hand_without_ai_notes(tmp_path, wav):
    app = build(tmp_path, FakeTranscriber())
    with TestClient(app) as client:
        rid = client.post("/api/recordings", files=[("files", ("a.wav", wav, "audio/wav"))]).json()[0]["id"]
        app.state.worker.run_once()
        notes = client.post(f"/api/recordings/{rid}/notes/items", json={"section": "summary", "text": "Mine."}).json()
        assert (notes["status"], notes["writer"], notes["content"]["summary"][0]["text"]) == ("ready", "you", "Mine.")


# --- combining recordings and notes instructions

def test_combine_recordings(env, wav):
    a = upload(env, wav, titles=["Lecture part 1"], subject="Machine Learning")
    b = upload(env, wav, titles=["Lecture part 2"])
    assert env.client.post("/api/recordings/combine", json={"ids": [a["id"], b["id"]]}).status_code == 400  # not done
    drain(env)
    response = env.client.post("/api/recordings/combine", json={"ids": [b["id"], a["id"]], "title": "Week 3"})
    assert response.status_code == 201
    combined = response.json()
    assert (combined["title"], combined["subject"], combined["status"]) == ("Week 3", "Machine Learning", "done")
    body = detail(env, combined["id"])
    assert len(body["part_starts"]) == 2 and body["part_starts"][0] == 0.0
    assert body["notes"]["status"] == "queued"
    assert [p["text"] for p in body["paragraphs"]] == ["Chunk 1.", "Chunk 2."]
    assert detail(env, a["id"])["status"] == "done"  # originals are kept
    assert env.client.post("/api/recordings/combine", json={"ids": [a["id"]]}).status_code == 400


def test_notes_instructions_api(env, wav):
    rec = upload(env, wav, subject="Machine Learning")
    sid = rec["subject_id"]
    patched = env.client.patch(f"/api/subjects/{sid}", json={"instructions": "The main speaker is the instructor."})
    assert patched.json()["instructions"] == "The main speaker is the instructor."
    assert env.client.put(f"/api/recordings/{rec['id']}/instructions", json={"text": "Focus on the assignment."}).json() == {
        "subject": "The main speaker is the instructor.", "recording": "Focus on the assignment."}
    assert detail(env, rec["id"])["instructions"] == {
        "subject": "The main speaker is the instructor.", "recording": "Focus on the assignment."}
    created = env.client.post("/api/subjects", json={"name": "Course B", "instructions": "I am the student."})
    assert created.json()["instructions"] == "I am the student."
    drain(env)
    assert "Instructions from the user: The main speaker is the instructor.\nFocus on the assignment." in \
        env.writer.calls[-1]["user"]


def test_star_via_patch_filter_and_counts(env, wav):
    rec = upload(env, wav)
    assert rec["starred"] is False
    assert env.client.patch(f"/api/recordings/{rec['id']}", json={"starred": True}).json()["starred"] is True
    assert [r["id"] for r in env.client.get("/api/recordings?starred=1").json()] == [rec["id"]]
    assert env.client.get("/api/recordings/counts").json() == {"all": 1, "starred": 1, "no_subject": 1, "deleted": 0}
