"""The bell: what happened to your recordings (added, transcribed, notes ready, failed), newest first."""
from fastapi.testclient import TestClient

from transcriber.notes.pipeline import NotesPipeline
from transcriber.providers.base import PermanentError
from transcriber.store import Store

from .helpers import FakeLLM, FakeTranscriber
from .test_app import NOTES_REPLY, build
from .test_pipeline import add_recording, env, make_pipeline  # noqa: F401  (fixture)


def kinds(store):
    return [n["kind"] for n in store.list_notifications()]


def test_adding_a_recording_notifies(tmp_path):
    store = Store(tmp_path / "app.db")
    store.create_recording("a", "Lecture 5", None, "en", "a.m4a")
    [note] = store.list_notifications()
    assert (note["kind"], note["recording_id"], note["title"], note["seen"]) == ("added", "a", "Lecture 5", False)
    store.update_recording("a", title="Machine Learning, 14 Oct")
    assert store.list_notifications()[0]["title"] == "Machine Learning, 14 Oct"  # shows the current title
    store.close()


def test_unseen_count_seen_clear_and_dismiss(tmp_path):
    store = Store(tmp_path / "app.db")
    for rid in "abc":
        store.create_recording(rid, rid, None, "en", f"{rid}.m4a")
    assert store.unseen_notifications() == 3
    assert [n["recording_id"] for n in store.list_notifications()] == ["c", "b", "a"]  # newest first
    store.mark_notifications_seen()
    assert store.unseen_notifications() == 0
    store.delete_notification(store.list_notifications()[0]["id"])
    assert len(store.list_notifications()) == 2
    store.clear_notifications()
    assert store.list_notifications() == []
    store.close()


def test_erasing_a_recording_keeps_its_notification(tmp_path):
    store = Store(tmp_path / "app.db")
    store.create_recording("a", "Gone", None, "en", "a.m4a")
    store.delete_recording("a")
    [note] = store.list_notifications()
    assert note["recording_id"] is None and note["title"] == "Gone"
    store.close()


def test_transcription_done_and_failed_notify(env):
    rid = add_recording(env)
    make_pipeline(env, FakeTranscriber()).process(rid)
    assert kinds(env.store)[:1] == ["transcribed"]
    bad = add_recording(env)
    make_pipeline(env, FakeTranscriber([PermanentError("Groq refused the file")])).process(bad)
    note = env.store.list_notifications()[0]
    assert (note["kind"], note["recording_id"], note["detail"]) == ("failed", bad, "Groq refused the file")


def test_notes_ready_and_failed_notify(env):
    rid = add_recording(env)
    make_pipeline(env, FakeTranscriber()).process(rid)
    NotesPipeline(env.store, FakeLLM(replies=[NOTES_REPLY])).process(rid)
    assert kinds(env.store)[0] == "notes_ready"
    env.store.queue_notes(rid)
    NotesPipeline(env.store, FakeLLM(replies=[PermanentError("Groq rejected the request")])).process(rid)
    note = env.store.list_notifications()[0]
    assert (note["kind"], note["detail"]) == ("notes_failed", "Groq rejected the request")


def test_notifications_api(tmp_path):
    app = build(tmp_path)
    with TestClient(app) as client:
        app.state.store.create_recording("a", "Lecture 5", None, "en", "a.m4a")
        body = client.get("/api/notifications").json()
        assert body["unseen"] == 1 and body["items"][0]["title"] == "Lecture 5"
        assert client.post("/api/notifications/seen").json()["unseen"] == 0
        note_id = body["items"][0]["id"]
        assert client.delete(f"/api/notifications/{note_id}").status_code == 204
        app.state.store.create_recording("b", "Lecture 6", None, "en", "b.m4a")
        assert client.delete("/api/notifications").status_code == 204
        assert client.get("/api/notifications").json() == {"items": [], "unseen": 0}
