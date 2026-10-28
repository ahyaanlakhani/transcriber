"""Pip's memory: only what you asked for or approved, always visible, fading on its own, and as private as its source."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from transcriber.notes.model import notes_from_writer
from transcriber.pip import Actions, PipError, memory_for_pip, suggest_memory
from transcriber.privacy import Gateway
from transcriber.store import Store

from .helpers import FakeLLM, FakeTranscriber
from .test_app import build

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


def add_notes(store, rid, items, *, title="Standup"):
    store.create_recording(rid, title, None, "en", f"{rid}.m4a")
    store.update_recording(rid, recorded_at=NOW.isoformat(timespec="seconds"))
    store.queue_notes(rid)
    store.save_notes(rid, notes_from_writer({"action_items": items}), writer="w", checker=None, check_detail=None)


# ---- the store

def test_memory_fades_and_comes_back_only_when_renewed(store):
    ongoing = store.add_memory("ongoing", "Finishing the FYP proposal", now=NOW)
    goal = store.add_memory("goal", "Get an A in Machine Learning", now=NOW)
    assert [i["text"] for i in store.list_memory(NOW + timedelta(days=29))["active"]] == [
        "Get an A in Machine Learning", "Finishing the FYP proposal"]
    later = store.list_memory(NOW + timedelta(days=31))  # ongoing items fade after 30 days, goals after 180
    assert [i["id"] for i in later["active"]] == [goal] and [i["id"] for i in later["expired"]] == [ongoing]
    store.renew_memory(ongoing, NOW + timedelta(days=31))
    assert {i["id"] for i in store.list_memory(NOW + timedelta(days=40))["active"]} == {goal, ongoing}


def test_the_same_words_are_remembered_once_and_bad_input_is_refused(store):
    first = store.add_memory("preference", "Short bullet notes", now=NOW)
    assert store.add_memory("preference", "  short   BULLET notes ", now=NOW) == first
    with pytest.raises(ValueError):
        store.add_memory("secret", "x")
    with pytest.raises(ValueError):
        store.add_memory("goal", "   ")


def test_a_suggestion_is_not_used_until_you_approve_it(store):
    memory_id = store.add_memory("ongoing", "Send the survey", status="proposed", now=NOW)
    assert store.list_memory(NOW)["active"] == [] and store.list_memory(NOW)["proposed"][0]["expires_at"] is None
    assert memory_for_pip(store)[0] == []
    store.approve_memory(memory_id, NOW)
    assert store.list_memory(NOW)["active"][0]["expires_at"] is not None


# ---- Pip's actions

def test_remember_and_forget_each_come_with_an_undo(store):
    actions = Actions(store, "Asia/Tashkent")
    saved = actions.run({"tool": "remember", "subject": "goal", "text": "Get an A in Machine Learning"}, ("remember",))
    assert store.list_memory()["active"][0]["kind"] == "goal"
    forgot = actions.run({"tool": "forget", "text": "machine learning"}, ("forget",))
    assert store.list_memory()["active"] == []
    actions.run(forgot["undo"][0], ("restore_memory",))
    assert [i["text"] for i in store.list_memory()["active"]] == ["Get an A in Machine Learning"]
    actions.run(saved["undo"][0], ("delete_memory",))
    assert store.list_memory()["active"] == []
    with pytest.raises(PipError):
        actions.run({"tool": "forget", "text": "never said that"}, ("forget",))


def test_an_unknown_kind_becomes_a_preference(store):
    Actions(store, "Asia/Tashkent").run({"tool": "remember", "subject": "whatever", "text": "No calls before 10"}, ("remember",))
    assert store.list_memory()["active"][0]["kind"] == "preference"


# ---- suggestions from your own notes

def test_suggestions_come_only_from_your_own_action_items_and_never_repeat(store):
    add_notes(store, "m1", [
        {"text": "Send the survey to the class.", "owner": "me", "due": "Friday", "cites": ["[01:00]"]},
        {"text": "Book the room.", "owner": "Lee", "due": None, "cites": ["[02:00]"]},
        {"text": "Share the slides.", "owner": None, "due": None, "cites": ["[03:00]"]}])
    assert suggest_memory(store, now=NOW) == 2
    assert suggest_memory(store, now=NOW) == 0  # again: nothing new
    texts = [i["text"] for i in store.list_memory()["proposed"]]
    assert any("Send the survey" in t and "by Friday" in t for t in texts) and not any("Book the room" in t for t in texts)
    assert store.list_memory()["active"] == []  # nothing is remembered until approved


# ---- privacy follows the memory

def test_a_memory_from_a_private_recording_is_not_shown_to_a_model_that_may_not_see_it(store):
    add_notes(store, "m1", [{"text": "Prepare the offer.", "owner": "me", "due": None, "cites": ["[01:00]"]}])
    store.set_privacy("m1", "private")
    store.add_memory("ongoing", "Prepare the offer (Standup)", source_recording_id="m1", now=datetime.now(UTC))
    store.add_memory("preference", "Short bullet notes", now=datetime.now(UTC))
    store.set_policy("groq:openai/gpt-oss-120b", standard=True, private=False)
    gateway = Gateway(store)
    lines, sources = memory_for_pip(store, gateway, "groq:openai/gpt-oss-120b")
    assert lines == ["preference: Short bullet notes"] and sources == []
    store.set_policy("groq:openai/gpt-oss-120b", standard=True, private=True, retention="zero_retention_verified")
    lines, sources = memory_for_pip(store, gateway, "groq:openai/gpt-oss-120b")
    assert len(lines) == 2 and sources == ["m1"]  # now it may be, and its recording sets the request's class


# ---- through the app

def command(client, text):
    response = client.post("/api/pip/command", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


def test_pip_remembers_what_you_say_and_uses_it_next_time(tmp_path):
    remember = {"say": "Noted!", "actions": [{"tool": "remember", "subject": "goal", "from": "", "to": "", "start": "",
                                                "minutes": 0, "when": "", "text": "Get an A in Machine Learning"}]}
    writer = FakeLLM("groq:x", [remember, {"say": "Hi", "actions": []}])
    app = build(tmp_path, FakeTranscriber(), writer, None)
    with TestClient(app) as client:
        reply = command(client, "remember that I want an A in Machine Learning")
        assert "remember" in reply["done"][0]["summary"].lower() and reply["undo"][0]["tool"] == "delete_memory"
        memory = client.get("/api/pip/memory").json()
        assert [i["text"] for i in memory["active"]] == ["Get an A in Machine Learning"]
        command(client, "what's up")
        assert "Get an A in Machine Learning" in writer.calls[-1]["user"]  # shown to Pip the next time
        undone = client.post("/api/pip/undo", json={"undo": reply["undo"]}).json()
        assert undone["done"] and client.get("/api/pip/memory").json()["active"] == []


def test_memory_panel_api_add_approve_and_forget(tmp_path):
    app = build(tmp_path, FakeTranscriber(), None, None)
    store = app.state.store
    add_notes(store, "m1", [{"text": "Send the survey.", "owner": "", "due": None, "cites": ["[01:00]"]}])
    with TestClient(app) as client:
        added = client.post("/api/pip/memory", json={"kind": "relationship", "text": "Dana is my mentor"}).json()
        assert added["active"][0]["kind"] == "relationship"
        assert client.post("/api/pip/memory", json={"kind": "mood", "text": "x"}).status_code == 400
        suggested = client.get("/api/pip/memory", params={"suggest": True}).json()
        proposal = suggested["proposed"][0]
        assert proposal["source"] == "Standup"  # where it came from, by name
        approved = client.post(f"/api/pip/memory/{proposal['id']}/approve").json()
        assert {i["id"] for i in approved["active"]} >= {proposal["id"]} and approved["proposed"] == []
        gone = client.delete(f"/api/pip/memory/{proposal['id']}").json()
        assert all(i["id"] != proposal["id"] for i in gone["active"])
        assert client.post("/api/pip/memory/999/approve").status_code == 404
