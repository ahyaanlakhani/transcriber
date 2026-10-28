"""Privacy through the whole app: refused requests send nothing, approvals open exactly what they say, private
material stays out of shared lists and cross-recording answers, and errors and logs carry no content."""
import logging
import sqlite3
import subprocess

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from transcriber import privacy
from transcriber.app import create_app
from transcriber.brain.index import Brain
from transcriber.config import Config
from transcriber.live import LiveManager
from transcriber.practice import PracticeManager
from transcriber.notes.model import notes_from_writer
from transcriber.llm.claude_code import ClaudeCodeClient
from transcriber.llm.openai_compat import map_error
from transcriber.privacy import Gateway
from transcriber.providers.base import PermanentError, Segment
from transcriber.store import Store

from .helpers import FakeLLM, FakeTranscriber, synth_audio, undo_privacy_migration
from .test_app import CHECK_REPLY, NOTES_REPLY
from .test_app_brain import seed

WHISPER, WRITER = "groq/whisper-large-v3", "groq:openai/gpt-oss-120b"
SECRET = "SECRET-LECTURE-WORDS-4417"


def whisper():
    fake = FakeTranscriber()
    fake.name = WHISPER
    return fake


def build_real(tmp_path, *, transcriber=None, writer=None, checker=None, ask_engine=None):
    """The real privacy gateway: nothing is approved until the test approves it."""
    config = Config(groq_api_key=None, data_dir=tmp_path / "data", port=0)
    return create_app(config, transcriber=transcriber, writer=writer, checker=checker, ask_engine=ask_engine,
                      voice=None, practice_llm=None, start_worker=False)


def approve(client, config_id, *, private=False):
    body = {"config_id": config_id, "standard": True, "private": private, "verified": private}
    response = client.put("/api/privacy/providers", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def upload(client, tmp_path):
    wav = synth_audio(tmp_path / "talk.wav", [("tone", 2.0)]).read_bytes()
    response = client.post("/api/recordings", files=[("files", ("talk.wav", wav, "audio/wav"))])
    assert response.status_code == 201, response.text
    return response.json()[0]["id"]


# ---- nothing is sent until you approve it

def test_upload_is_refused_before_anything_is_sent_until_approved(tmp_path):
    transcriber = whisper()
    app = build_real(tmp_path, transcriber=transcriber)
    with TestClient(app) as client:
        rid = upload(client, tmp_path)
        app.state.worker.run_once()
        rec = client.get(f"/api/recordings/{rid}").json()
        assert rec["status"] == "failed" and "approved" in rec["status_detail"]
        assert transcriber.calls == []  # zero uploads
        log = client.get("/api/privacy").json()["log"]
        assert log[0]["decision"] == "blocked" and log[0]["reason"] == "not_approved" and log[0]["recording_ids"] == [rid]


def test_after_approval_it_goes_through_and_is_logged(tmp_path):
    transcriber = whisper()
    app = build_real(tmp_path, transcriber=transcriber)
    with TestClient(app) as client:
        approve(client, WHISPER)
        rid = upload(client, tmp_path)
        app.state.worker.run_once()
        assert client.get(f"/api/recordings/{rid}").json()["status"] == "done" and len(transcriber.calls) == 1
        assert client.get("/api/privacy").json()["log"][0]["decision"] == "allowed"


def test_a_fallback_provider_is_asked_too_and_nothing_downgrades_silently(tmp_path):
    claude = FakeLLM("claude-code", [{"answer": "x", "proposal": None}])
    groq = FakeLLM(WRITER, [])
    app = build_real(tmp_path, writer=groq, ask_engine=claude)
    store = app.state.store
    seed(store)
    store.set_privacy("a", "private")
    with TestClient(app) as client:
        approve(client, "claude-code")  # Claude may see Standard, never Private
        approve(client, WRITER)
        client.post("/api/recordings/a/chat", json={"message": "What is OAuth?"})
        assert claude.calls == [] and groq.calls == []  # Claude can't take Private; Groq isn't approved for it
        providers = {p["config_id"]: p for p in client.get("/api/privacy").json()["providers"]}
        assert providers["claude-code"]["private_possible"] is False


def test_private_recording_needs_a_verified_provider_and_then_works(tmp_path):
    writer = FakeLLM(WRITER, [NOTES_REPLY] * 4)
    checker = FakeLLM("groq:openai/gpt-oss-20b", [CHECK_REPLY] * 4)
    app = build_real(tmp_path, writer=writer, checker=checker)
    store = app.state.store
    seed(store)
    store.set_privacy("a", "private")
    with TestClient(app) as client:
        approve(client, WRITER)
        approve(client, "groq:openai/gpt-oss-20b")
        store.queue_notes("a")
        app.state.worker.run_once()
        assert store.get_notes("a").status == "failed" and writer.calls == []
        assert "zero-retention" in store.get_notes("a").status_detail
        approve(client, WRITER, private=True)
        approve(client, "groq:openai/gpt-oss-20b", private=True)
        store.queue_notes("a")
        app.state.worker.run_once()
        assert store.get_notes("a").status == "ready" and len(writer.calls) == 1


def test_you_cannot_approve_a_provider_the_app_does_not_use_or_know(tmp_path):
    app = build_real(tmp_path, writer=FakeLLM("mystery:model", []))
    with TestClient(app) as client:
        assert client.put("/api/privacy/providers", json={"config_id": "groq:other", "standard": True}).status_code == 404
        assert client.put("/api/privacy/providers", json={"config_id": "mystery:model", "standard": True}).status_code == 400


def test_private_approval_requires_your_confirmation(tmp_path):
    app = build_real(tmp_path, writer=FakeLLM(WRITER, []))
    with TestClient(app) as client:
        body = {"config_id": WRITER, "standard": True, "private": True, "verified": False}
        assert client.put("/api/privacy/providers", json=body).status_code == 400


# ---- shared material: names, topics, answers across recordings

def test_global_ask_leaves_out_private_recordings_before_choosing_passages(tmp_path):
    writer = FakeLLM(WRITER, [{"answer": "OAuth tokens are short-lived. [1]"}])
    app = build_real(tmp_path, writer=writer)
    store = app.state.store
    seed(store)
    store.set_privacy("b", "private")  # b is the one that says "tokens expire"
    with TestClient(app) as client:
        approve(client, WRITER)
        answer = client.post("/api/brain/ask", json={"question": "When do OAuth tokens expire?"}).json()
        sent = writer.calls[-1]["user"]
        assert "on the exam" not in sent and "Security week" not in sent
        assert {s["recording_id"] for s in answer["sources"]} == {"a"}
        assert "left out" in (answer["note"] or "")


def test_private_concept_names_and_topics_never_reach_a_model_that_may_not_see_them(tmp_path):
    store = Store(tmp_path / "app.db")
    seed(store)
    store.add_topic("a", "Agent loops")
    store.add_topic("b", "Secret clinic topic")
    store.set_privacy("b", "private")
    store.set_policy(WRITER, standard=True, private=False)
    gateway = Gateway(store)
    brain = Brain(store, FakeLLM(WRITER, []), gateway=gateway)
    brain.catch_up()
    concepts = {c["name"]: c["id"] for c in brain.list_concepts()}
    visible = brain.visible_concepts(WRITER, concepts.values())
    assert concepts["AI agent"] in visible and concepts["OAuth"] not in visible  # OAuth is also mentioned in b
    assert gateway.visible_topic_names(WRITER) == ["Agent loops"]
    store.close()


def test_concept_name_check_sends_only_names_the_model_may_see(tmp_path):
    store = Store(tmp_path / "app.db")
    seed(store)
    store.create_recording("c", "Workflows week", None, "en", "c.m4a")
    store.save_transcript("c", "words", [Segment(0, 5, "Loops.")], "groq")
    store.update_recording("c", recorded_at="2026-10-12T05:00:00+00:00", duration_sec=2700.0)
    store.queue_notes("c")
    store.save_notes("c", notes_from_writer({"lecture": [
        {"heading": "Agentic workflow", "kind": "point", "term": None, "text": "A loop of plan and act.",
         "cites": ["[01:00]"]}]}), writer="w", checker=None, check_detail=None)
    store.set_privacy("b", "private")
    store.set_policy(WRITER, standard=True, private=False)
    llm = FakeLLM(WRITER, [{"matches": []}] * 6)
    brain = Brain(store, Gateway(store).guard_llm(llm), gateway=Gateway(store))
    brain.catch_up()
    for _ in range(6):
        brain.step()
    assert llm.calls  # the check did run, on the names it was allowed to see
    for call in llm.calls:
        assert "OAuth" not in call["user"]
    assert any("Agentic workflow" in call["user"] or "AI agent" in call["user"] for call in llm.calls)
    store.close()


# ---- live sessions: the class is fixed before the first audio window leaves

def test_private_live_session_sends_nothing_without_a_verified_provider(tmp_path):
    store = Store(tmp_path / "app.db")
    store.set_policy(WHISPER, standard=True, private=False)
    fake = whisper()
    manager = LiveManager(store, Gateway(store).guard_transcriber(fake), tmp_path / "live", tmp_path / "recordings",
                          start_thread=False)
    sid = manager.create(title="Chat", subject_id=None, language="en", privacy="private")
    from tests.test_live_recording import SPEECH, pieces, webm_bytes
    data = webm_bytes(tmp_path, SPEECH)
    for seq, piece in enumerate(pieces(data)):
        manager.add_chunk(sid, seq, piece)
    manager.wait_for_audio(sid)
    while manager.step():
        pass
    assert fake.calls == []  # not one window was uploaded
    rid = manager.finish(sid)
    assert store.get_recording(rid).privacy_classification == "private"
    store.close()


# ---- the definition cache

def test_definition_goes_stale_on_same_count_edits_trash_restore_and_class_change(tmp_path):
    store = Store(tmp_path / "app.db")
    seed(store)
    brain = Brain(store, FakeLLM(WRITER, []))
    brain.catch_up()
    cid = next(c["id"] for c in brain.list_concepts() if c["name"] == "OAuth")
    name, texts, count, fingerprint = brain.definition_material(cid)
    assert brain.save_definition(cid, "An open standard.", count, fingerprint) is True
    assert brain.concept(cid)["definition_stale"] is False
    store._execute("UPDATE concept_mentions SET text = 'OAuth tokens last an hour.' WHERE concept_id = ? AND recording_id = 'b'",
                   (cid,))  # same number of mentions, different words
    assert brain.concept(cid)["definition_stale"] is True
    brain.save_definition(cid, "Rewritten.", count)
    assert brain.concept(cid)["definition_stale"] is False
    store.soft_delete("b")
    assert brain.concept(cid)["definition_stale"] is True  # one mention fewer: stale
    brain.save_definition(cid, "Rewritten again.", 1)
    store.restore("b")
    assert brain.concept(cid)["definition_stale"] is True
    brain.save_definition(cid, "Back to both.", 2)
    store.set_privacy("b", "private")
    assert brain.concept(cid)["definition_stale"] is True  # a class change counts too
    store.close()


def test_a_definition_written_while_the_notes_changed_is_thrown_away(tmp_path):
    store = Store(tmp_path / "app.db")
    seed(store)
    brain = Brain(store, FakeLLM(WRITER, []))
    brain.catch_up()
    cid = next(c["id"] for c in brain.list_concepts() if c["name"] == "OAuth")
    _, _, count, fingerprint = brain.definition_material(cid)
    store._execute("UPDATE concept_mentions SET text = 'Changed while the model was writing.' WHERE concept_id = ?", (cid,))
    assert brain.save_definition(cid, "Out of date.", count, fingerprint) is False
    assert brain.concept(cid)["definition"] != "Out of date."
    store.close()


# ---- errors and logs carry no content

def api_error(cls, status, message):
    request = httpx.Request("POST", "https://api.example.com/v1")
    response = httpx.Response(status, request=request, headers={"retry-after": "5"})
    return cls(message, response=response, body={"error": {"message": message}})


@pytest.mark.parametrize("cls, status", [(openai.RateLimitError, 429), (openai.InternalServerError, 500),
                                         (openai.BadRequestError, 400), (openai.AuthenticationError, 401)])
def test_provider_errors_never_quote_what_was_sent(cls, status):
    mapped = map_error(api_error(cls, status, f"bad request near {SECRET} and key sk-abc123"), "groq")
    assert SECRET not in str(mapped) and "sk-abc123" not in str(mapped)


def test_claude_code_errors_never_quote_output(tmp_path):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=f"not json {SECRET}", stderr=f"trace {SECRET}")

    client = ClaudeCodeClient("claude", run=run, sandbox=tmp_path / "sandbox")
    with pytest.raises(Exception) as err:
        client.complete_json(system="s", user="u", schema={}, schema_name="x")
    assert SECRET not in str(err.value)


def test_a_blocked_request_is_logged_without_any_text(tmp_path, caplog):
    store = Store(tmp_path / "app.db")
    store.create_recording("a", "Rec", None, "en", "a.m4a")
    store.save_transcript("a", SECRET, [Segment(0, 1, SECRET)], "groq")
    llm = FakeLLM(WRITER, [])
    gateway = Gateway(store)
    with caplog.at_level(logging.DEBUG):
        with privacy.scope("notes", ["a"]):
            with pytest.raises(privacy.PrivacyBlocked) as err:
                gateway.guard_llm(llm).complete_json(system="s", user=SECRET, schema={}, schema_name="x")
    dumped = repr(store.list_transmissions()) + str(err.value) + caplog.text
    assert SECRET not in dumped and llm.calls == []
    store.close()


def test_unexpected_failures_are_logged_with_where_but_not_what(tmp_path, caplog):
    try:
        raise ValueError(f"cannot parse {SECRET}")
    except ValueError:
        with caplog.at_level(logging.ERROR):
            privacy.log_failure(logging.getLogger("test"), "Unexpected error while processing %s", "abc")
    assert "ValueError" in caplog.text and SECRET not in caplog.text and "abc" in caplog.text


# ---- the upgrade from the schema before this one

def old_database(path):
    """A real v17 database, with a merged recording, a combined one and a plain one."""
    store = Store(path)
    for rid in ("p1", "p2", "m1", "c1", "x"):
        store.create_recording(rid, f"Rec {rid}", None, "en", f"{rid}.m4a" if rid != "c1" else "2 combined: p1.m4a, p2.m4a")
    store.save_transcript("x", "words", [Segment(0, 1, "words")], "groq")
    store._execute("UPDATE recordings SET merge_sources = ? WHERE id = 'm1'", ('["p1", "p2"]',))
    store._execute("UPDATE recordings SET part_starts = '[0, 100]' WHERE id = 'c1'")
    undo_privacy_migration(store)
    store._execute("PRAGMA user_version = 17")
    store.close()


def test_upgrade_keeps_everything_backs_up_and_marks_what_it_cannot_trace(tmp_path):
    db = tmp_path / "data" / "app.db"
    old_database(db)
    store = Store(db)
    assert store._query("PRAGMA user_version")[0][0] == 25
    assert {r.id for r in store.list_recordings()} == {"p1", "p2", "m1", "c1", "x"}
    assert store.get_transcript("x").text == "words"
    assert all(r.privacy_classification == "standard" for r in store.list_recordings())  # everything starts Standard
    assert sorted(r["source_id"] for r in store._query("SELECT source_id FROM recording_sources WHERE recording_id = 'm1'")) == ["p1", "p2"]
    assert store.effective_privacy(["m1"])["m1"][1] is True  # traced through its sources
    assert store.effective_privacy(["c1"])["c1"][1] is False  # a combined recording whose parts were never kept
    assert store.effective_privacy(["x"])["x"] == ("standard", True)
    assert list((tmp_path / "data" / "backups").glob("app-v17-*.db"))  # a copy from before the upgrade
    store.close()


def test_a_failed_upgrade_changes_nothing_and_can_be_retried(tmp_path):
    db = tmp_path / "data" / "app.db"
    old_database(db)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE transmissions (id INTEGER)")  # in the way: the upgrade fails part-way
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.OperationalError):
        Store(db)
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
    columns = [row[1] for row in conn.execute("PRAGMA table_info(recordings)")]
    assert "privacy_classification" not in columns  # the columns added before the failure were rolled back
    assert conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 5
    conn.execute("DROP TABLE transmissions")
    conn.commit()
    conn.close()
    store = Store(db)  # retry works
    assert store._query("PRAGMA user_version")[0][0] == 25
    store.close()


# ---- the practice room: a persona written from private chats is as private as they are

def test_practice_persona_from_a_private_chat_is_not_sent_unverified(tmp_path):
    store = Store(tmp_path / "app.db")
    seed(store)
    person = store.get_or_create_person("Dana")
    store.update_recording("a", person_id=person)
    store.set_privacy("a", "private")
    store.set_policy(WRITER, standard=True, private=False)
    store.set_policy(WHISPER, standard=True, private=False)
    gateway = Gateway(store)
    llm = FakeLLM(WRITER, [{"reply": "Hello", "done": False}])
    transcriber = whisper()
    practice = PracticeManager(store, gateway.guard_transcriber(transcriber), gateway.guard_llm(llm), tmp_path / "practice",
                               tmp_path / "recordings")
    session = practice.start("person", person_id=person)
    wav = synth_audio(tmp_path / "turn.wav", [("tone", 1.5)]).read_bytes()
    with pytest.raises(privacy.PrivacyBlocked):
        practice.turn(session["id"], wav, ".wav", start_sec=0.0)
    assert llm.calls == []  # the persona prompt, built from the private chat, never went out
    store.close()


def test_gemini_can_be_approved_for_standard_but_never_private():
    from transcriber import privacy
    assert privacy.PROVIDERS["gemini"].private_possible is False  # the free tier allows human review of what it is sent
    assert privacy.PROVIDERS["claude-code"].private_possible is False


def test_only_this_computer_may_use_the_app(tmp_path):
    from .helpers import FakeTranscriber
    from .test_app import build
    with TestClient(build(tmp_path, FakeTranscriber(), None, None)) as client:
        assert client.get("/api/status", headers={"Host": "localhost:8000"}).status_code == 200
        assert client.get("/api/status", headers={"Host": "evil.example.com"}).status_code == 403  # a rebound host name
        mine = {"Host": "localhost:8000", "Origin": "http://localhost:8000"}
        assert client.post("/api/projects", json={"name": "x"}, headers=mine).status_code in (200, 201)
        other = {"Host": "localhost:8000", "Origin": "https://evil.example.com"}
        assert client.post("/api/projects", json={"name": "y"}, headers=other).status_code == 403  # another site's form
        assert client.get("/api/status", headers=other).status_code == 200  # reading is still plain


def test_the_vault_folder_stays_out_of_system_folders_and_inputs_have_a_size_limit(tmp_path):
    from .helpers import FakeTranscriber
    from .test_app import build
    with TestClient(build(tmp_path, FakeTranscriber(), None, None)) as client:
        assert client.put("/api/settings", json={"vault_folder": r"C:\Windows\System32"}).status_code == 400
        assert client.put("/api/settings", json={"vault_folder": "C:\\"}).status_code == 400
        mine = tmp_path / "my vault"
        assert client.put("/api/settings", json={"vault_folder": str(mine)}).status_code == 200
        assert client.post("/api/learn/teach-back", json={"concept_id": 1, "explanation": "x" * 9000}).status_code == 422
        assert client.post("/api/brain/ask", json={"question": "q" * 3000}).status_code == 422


def test_a_phone_link_host_must_be_listed_before_the_app_answers_to_it(tmp_path):
    from transcriber.app import create_app
    from transcriber.config import Config, load_config
    assert load_config(environ={"TRANSCRIBER_ALLOWED_HOSTS": "My-Laptop.tail1234.ts.net, other.example"}).allowed_hosts == (
        "my-laptop.tail1234.ts.net", "other.example")
    config = Config(groq_api_key=None, data_dir=tmp_path / "data", port=0, allowed_hosts=("my-laptop.tail1234.ts.net",))
    app = create_app(config, start_worker=False, privacy_allow_all=True)
    with TestClient(app) as client:
        assert client.get("/api/status", headers={"Host": "my-laptop.tail1234.ts.net"}).status_code == 200
        assert client.get("/api/status", headers={"Host": "someone-else.ts.net"}).status_code == 403
