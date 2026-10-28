"""People you've had coffee chats with: storage, linking, the pre-chat brief and your progress over time."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from transcriber.notes.coach import ME, THEM
from transcriber.notes.model import notes_from_writer
from transcriber.notes.people import BRIEF_SYSTEM, write_brief
from transcriber.providers.base import Segment
from transcriber.store import Store

from .helpers import FakeLLM, undo_privacy_migration
from .test_app import build

DIRECT = lambda fn: fn()  # noqa: E731


# --- storage

def test_people_are_created_once_and_linked(tmp_path):
    store = Store(tmp_path / "app.db")
    sara = store.get_or_create_person("Sara Khan")
    assert store.get_or_create_person("  sara   khan ") == sara  # same person, any spacing or case
    store.create_recording("a", "Coffee 1", None, "en", "a.m4a")
    store.create_recording("b", "Coffee 2", None, "en", "b.m4a")
    store.update_recording("a", person_id=sara, recorded_at="2026-10-01T05:00:00+00:00")
    store.update_recording("b", person_id=sara, recorded_at="2026-10-08T05:00:00+00:00")
    [person] = store.list_people()
    assert (person.name, person.count, person.last_at) == ("Sara Khan", 2, "2026-10-08T05:00:00+00:00")
    assert store.get_recording("a").person == "Sara Khan"
    assert [r.id for r in store.list_recordings(person_id=sara)] == ["b", "a"]
    store.update_person(sara, name="Dr. Sara Khan", note="Met at the fintech fair")
    assert store.get_person(sara).note == "Met at the fintech fair"
    store.set_person_brief(sara, {"who": "x"})
    assert store.get_person(sara).brief == {"who": "x"}
    store.delete_person(sara)
    assert store.get_recording("a").person_id is None
    store.close()


def test_conversation_recordings_follow_the_style(tmp_path):
    store = Store(tmp_path / "app.db")
    chats = store.create_subject("Coffee chats")
    store.update_subject(chats.id, notes_style="conversation")
    store.create_recording("a", "Chat", chats.id, "en", "a.m4a")
    store.create_recording("b", "Lecture", None, "en", "b.m4a")
    store.create_recording("c", "One-off chat", None, "en", "c.m4a")
    store.update_recording("c", notes_style="conversation")
    assert sorted(r.id for r in store.conversation_recordings()) == ["a", "c"]
    store.close()


def test_migrates_v11_database(tmp_path):
    db = tmp_path / "app.db"
    store = Store(db)
    store.create_recording("k1", "Kept", None, "en", "k1.m4a")
    store._execute("PRAGMA user_version = 11")
    store._execute("ALTER TABLE recordings DROP COLUMN content_hash")
    undo_privacy_migration(store)
    store._execute("DROP TABLE reminders")
    store._execute("ALTER TABLE recordings DROP COLUMN person_id")
    store._execute("DROP TABLE people")
    store._execute("ALTER TABLE subjects DROP COLUMN schedule_json")
    store._execute("DROP TABLE notifications")
    store.close()
    store = Store(db)
    assert store.get_recording("k1").person_id is None and store.list_people() == []
    store.close()


# --- the pre-chat brief

def test_brief_uses_past_chats_and_your_coaching():
    chat = notes_from_writer({
        "about_them": [{"text": "Leads product at a fintech.", "cites": []}],
        "action_items": [{"text": "Send Sara my portfolio.", "owner": "You", "due": None, "cites": []}],
        "my_questions": [{"text": "How did you start?", "kind": "open", "suggestion": None, "cites": []}],
        "feedback": [{"text": "Ask fewer yes/no questions.", "kind": "improve", "cites": []}]})
    reply = {"who": "Sara leads product.", "last_time": "On 1 Oct you talked about her path.",
             "promises": ["Send your portfolio."], "questions": ["What would you do differently?"],
             "reminder": "Ask open questions."}
    llm = FakeLLM(replies=[reply])
    brief = write_brief(llm, name="Sara", note="Met at the fintech fair",
                        chats=[("Coffee 1", "Wednesday 1 October", chat)], call=DIRECT)
    assert brief == reply
    [call] = llm.calls
    assert call["system"] == BRIEF_SYSTEM
    for text in ("Sara", "Met at the fintech fair", "Coffee 1 (Wednesday 1 October)", "Leads product at a fintech.",
                 "Send Sara my portfolio.", "How did you start?", "Ask fewer yes/no questions."):
        assert text in call["user"]


# --- through the API

@pytest.fixture
def env(tmp_path):
    writer = FakeLLM("groq:x", [])
    app = build(tmp_path, None, writer, None)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, store=app.state.store, writer=writer)


def chat(env, rid, person, when, my_share_segments, notes):
    store = env.store
    store.create_recording(rid, f"Coffee {rid}", None, "en", f"{rid}.m4a")
    store.update_recording(rid, notes_style="conversation", recorded_at=when, duration_sec=60.0,
                           person_id=store.get_or_create_person(person))
    store.save_transcript(rid, "x", my_share_segments, "groq")
    store.queue_notes(rid)
    store.save_notes(rid, notes_from_writer(notes), writer="w", checker=None, check_detail=None)


NOTES = {"about_them": [{"text": "Leads product.", "cites": []}],
         "my_questions": [{"text": "How?", "kind": "open", "suggestion": None, "cites": []}],
         "feedback": [{"text": "Ask open questions.", "kind": "improve", "cites": []},
                      {"text": "Great listening.", "kind": "strength", "cites": []}],
         "action_items": [{"text": "Send portfolio.", "owner": "You", "due": None, "cites": []}]}


def segments(mine, theirs):
    return [Segment(0, mine, "Um, how did you start?", speaker=ME), Segment(mine, mine + theirs, "I...", speaker=THEM)]


def test_people_api_and_link(env):
    chat(env, "a", "Sara", "2026-10-01T05:00:00+00:00", segments(30, 30), NOTES)
    [person] = env.client.get("/api/people").json()
    detail = env.client.get(f"/api/people/{person['id']}").json()
    assert detail["name"] == "Sara" and [c["id"] for c in detail["chats"]] == ["a"]
    assert detail["about"][0]["text"] == "Leads product." and detail["promises"][0]["text"] == "Send portfolio."
    moved = env.client.patch("/api/recordings/a", json={"person": "Ali"}).json()
    assert moved["person"] == "Ali"
    assert env.client.patch("/api/recordings/a", json={"person": ""}).json()["person"] is None


def test_brief_endpoint(env):
    chat(env, "a", "Sara", "2026-10-01T05:00:00+00:00", segments(30, 30), NOTES)
    person_id = env.client.get("/api/people").json()[0]["id"]
    env.writer.replies.append({"who": "w", "last_time": "l", "promises": [], "questions": ["q"], "reminder": "r"})
    body = env.client.post(f"/api/people/{person_id}/brief").json()
    assert body["brief"]["questions"] == ["q"] and body["brief_at"]


def test_progress_over_time(env):
    chat(env, "a", "Sara", "2026-10-01T05:00:00+00:00", segments(30, 30), NOTES)
    chat(env, "b", "Ali", "2026-10-08T05:00:00+00:00", segments(15, 45), NOTES)
    body = env.client.get("/api/coach/progress").json()
    assert [c["id"] for c in body["chats"]] == ["a", "b"]  # oldest first, for the trend
    assert [c["my_share"] for c in body["chats"]] == [0.5, 0.25]
    assert body["chats"][1]["questions"] == 1 and body["chats"][1]["person"] == "Ali"
    assert body["work_on"][0]["text"] == "Ask open questions." and body["work_on"][0]["recording_id"] == "b"
    assert body["keep"][0]["text"] == "Great listening."
