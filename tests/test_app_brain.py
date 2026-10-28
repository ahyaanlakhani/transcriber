"""Second brain through the API: the Brain screen, concept pages, Ask across everything, the graph, the Obsidian
export, and Pip's ask_brain / open_concept."""
from fastapi.testclient import TestClient

from transcriber.notes.model import notes_from_writer
from transcriber.providers.base import Segment

from .helpers import FakeLLM, FakeTranscriber
from .test_app import build


def seed(store):
    for rid, title, when, items in (
        ("a", "Agents week", "2026-10-07T05:00:00+00:00", [
            {"heading": "AI agents", "kind": "definition", "term": "AI agent", "text": "Software that acts on its own.",
             "cites": ["[02:10]"]},
            {"heading": "OAuth", "kind": "point", "term": None, "text": "OAuth lets an agent act for you.",
             "cites": ["[09:00]"]}]),
        ("b", "Security week", "2026-10-01T05:00:00+00:00", [
            {"heading": "OAuth", "kind": "stressed", "term": None, "text": "OAuth tokens expire; on the exam.",
             "cites": ["[04:00]"]}])):
        store.create_recording(rid, title, store.get_or_create_subject("Machine Learning"), "en", f"{rid}.m4a")
        store.save_transcript(rid, "words", [Segment(0, 5, "Tokens are short-lived passwords.")], "groq")
        store.update_recording(rid, recorded_at=when, duration_sec=2700.0)
        store.queue_notes(rid)
        store.save_notes(rid, notes_from_writer({"lecture": items}), writer="w", checker=None, check_detail=None)


def test_brain_screen_concept_page_and_definition(tmp_path):
    writer = FakeLLM("groq:x", [{"definition": "An open standard that lets an app act for you without your password."}])
    app = build(tmp_path, FakeTranscriber(), writer, None)
    seed(app.state.store)
    with TestClient(app) as client:
        overview = client.get("/api/brain").json()
        names = [c["name"] for c in overview["concepts"]]
        assert names[0] == "OAuth" and "AI agent" in names and overview["recordings"] == 2
        assert overview["vault_folder"].endswith("Transcriber vault")
        oauth = next(c for c in overview["concepts"] if c["name"] == "OAuth")
        page = client.get(f"/api/brain/concepts/{oauth['id']}").json()
        assert [g["recording_id"] for g in page["recordings"]] == ["a", "b"]
        assert page["recordings"][1]["items"][0] == {"item_id": "L1", "kind": "stressed", "heading": "OAuth",
                                                     "text": "OAuth tokens expire; on the exam.", "start": 240.0}
        assert page["definition_by"] == "notes" and page["definition_stale"] is True
        defined = client.post(f"/api/brain/concepts/{oauth['id']}/definition").json()
        assert defined["definition"].startswith("An open standard") and defined["definition_stale"] is False
        assert "OAuth tokens expire" in writer.calls[-1]["user"]
        assert client.get("/api/brain/find", params={"name": "oauth"}).json() == {"id": oauth["id"]}
        assert client.get("/api/brain/concepts/999").status_code == 404


def test_rename_merge_rebuild_and_graph(tmp_path):
    app = build(tmp_path, FakeTranscriber(), None, None)
    seed(app.state.store)
    with TestClient(app) as client:
        concepts = {c["name"]: c["id"] for c in client.get("/api/brain").json()["concepts"]}
        renamed = client.patch(f"/api/brain/concepts/{concepts['AI agent']}", json={"name": "Agents"}).json()
        assert renamed["name"] == "Agents" and "AI agent" in renamed["aliases"]
        merged = client.post(f"/api/brain/concepts/{concepts['AI agent']}/merge", json={"into": concepts["OAuth"]}).json()
        assert merged["name"] == "OAuth" and "Agents" in merged["aliases"]
        assert client.get(f"/api/brain/concepts/{concepts['AI agent']}").status_code == 404  # merged away
        assert client.post(f"/api/brain/concepts/{concepts['OAuth']}/merge", json={"into": concepts["OAuth"]}).status_code == 400
        assert client.post("/api/brain/rebuild").json()["indexed"] == 2
        assert client.get("/api/brain/find", params={"name": "agents"}).json()["id"] == concepts["OAuth"]
        data = client.get("/api/brain/graph").json()
        assert {n["type"] for n in data["nodes"]} == {"concept", "class"} and data["edges"]


def test_ask_across_everything(tmp_path):
    ask = FakeLLM("claude-code:sonnet", [{"answer": "OAuth lets an agent act for you [1] and its tokens expire [2]."}])
    app = build(tmp_path, FakeTranscriber(), None, None, ask_engine=ask)
    seed(app.state.store)
    with TestClient(app) as client:
        body = client.post("/api/brain/ask", json={"question": "What did we say about OAuth?"}).json()
        assert body["engine"] == "claude-code:sonnet" and "[2]" in body["answer"]
        assert {s["recording_id"] for s in body["sources"]} == {"a", "b"}
        assert all({"n", "title", "start", "source", "text"} <= set(s) for s in body["sources"])
        assert client.post("/api/brain/ask", json={"question": "  "}).status_code == 400


def test_export_to_obsidian_remembers_the_folder(tmp_path):
    app = build(tmp_path, FakeTranscriber(), None, None)
    seed(app.state.store)
    vault = tmp_path / "My vault"
    with TestClient(app) as client:
        result = client.post("/api/brain/export", json={"folder": str(vault)}).json()
        assert result["recordings"] == 2 and (vault / "Home.md").exists() and (vault / "Concepts" / "OAuth.md").exists()
        assert client.get("/api/settings").json()["vault_folder"] == str(vault)
        assert client.post("/api/brain/export").json()["folder"] == str(vault)


def test_pip_answers_questions_and_opens_concepts(tmp_path):
    blank = {"subject": "", "from": "", "to": "", "start": "", "minutes": 0, "when": ""}
    writer = FakeLLM("groq:x", [
        {"say": "Here's what your notes say.", "actions": [{"tool": "ask_brain", **blank, "text": "what about OAuth?"}]},
        {"answer": "Tokens expire [1]."},
        {"say": "Opening it.", "actions": [{"tool": "open_concept", **blank, "text": "oauth"}]},
        {"say": "Opening it.", "actions": [{"tool": "open_concept", **blank, "text": "photosynthesis"}]}])
    app = build(tmp_path, FakeTranscriber(), writer, None)
    seed(app.state.store)
    with TestClient(app) as client:
        said = client.post("/api/pip/command", json={"text": "what did we say about OAuth?"}).json()
        assert said["answer"]["answer"] == "Tokens expire [1]." and said["answer"]["sources"] and said["done"] == []
        opened = client.post("/api/pip/command", json={"text": "show me oauth"}).json()
        oauth = client.get("/api/brain/find", params={"name": "OAuth"}).json()["id"]
        assert opened["open"] == {"view": "concept", "id": oauth, "subject": ""}
        missing = client.post("/api/pip/command", json={"text": "show me photosynthesis"}).json()
        assert missing["open"] is None and "photosynthesis" in missing["problems"][0]


def test_ask_modes_choose_the_instructions_and_come_back_labelled(tmp_path):
    writer = FakeLLM("groq:x", [{"answer": "Tokens expire [1]."}] * 3)
    app = build(tmp_path, FakeTranscriber(), writer, None)
    seed(app.state.store)
    with TestClient(app) as client:
        for mode, words in (("retrieval", "Answer only from"), ("synthesis", "Compare and"), ("application", "Applying it:")):
            answer = client.post("/api/brain/ask", json={"question": "When do OAuth tokens expire?", "mode": mode}).json()
            assert answer["mode"] == mode and words in writer.calls[-1]["system"]
        odd = client.post("/api/brain/ask", json={"question": "OAuth tokens", "mode": "guess"}).json()
        assert odd["mode"] == "retrieval"


def test_aliases_and_a_personal_note_on_a_concept(tmp_path):
    app = build(tmp_path, FakeTranscriber(), None, None)
    seed(app.state.store)
    with TestClient(app) as client:
        concepts = {c["name"]: c["id"] for c in client.get("/api/brain").json()["concepts"]}
        oauth, agent = concepts["OAuth"], concepts["AI agent"]
        page = client.post(f"/api/brain/concepts/{oauth}/aliases", json={"alias": "Open Authorization"}).json()
        assert "Open Authorization" in page["aliases"]
        assert client.get("/api/brain/find", params={"name": "open authorization"}).json() == {"id": oauth}
        clash = client.post(f"/api/brain/concepts/{oauth}/aliases", json={"alias": "AI agent"})
        assert clash.status_code == 400 and "Merge" in clash.json()["detail"]
        assert client.post(f"/api/brain/concepts/{oauth}/aliases", json={"alias": "  "}).status_code == 400
        page = client.delete(f"/api/brain/concepts/{oauth}/aliases", params={"alias": "Open Authorization"}).json()
        assert "Open Authorization" not in page["aliases"]
        noted = client.patch(f"/api/brain/concepts/{agent}", json={"note": "Ask about guardrails in office hours."}).json()
        assert noted["note"] == "Ask about guardrails in office hours." and noted["name"] == "AI agent"
        client.post("/api/brain/rebuild")
        assert client.get(f"/api/brain/concepts/{agent}").json()["note"] == "Ask about guardrails in office hours."
