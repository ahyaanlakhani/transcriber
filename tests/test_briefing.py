"""Today: the briefing and the follow-up list, built only from local notes and the calendar."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from transcriber.briefing import briefing, follow_up_key, follow_ups, revision_priorities
from transcriber.notes.model import notes_from_writer
from transcriber.store import Store

from .helpers import FakeTranscriber
from .test_app import build

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)  # a Monday
TZ = "Asia/Tashkent"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


def add(store, rid, title, *, days_ago=1, subject=None, **sections):
    sid = store.get_or_create_subject(subject) if subject else None
    store.create_recording(rid, title, sid, "en", f"{rid}.m4a")
    store.update_recording(rid, recorded_at=(NOW - timedelta(days=days_ago)).isoformat(timespec="seconds"), duration_sec=1800.0)
    store.queue_notes(rid)
    store.save_notes(rid, notes_from_writer(sections), writer="w", checker=None, check_detail=None)


ITEMS = [
    {"text": "Send the survey to the class.", "owner": "me", "due": "Friday", "cites": ["[01:00]"]},
    {"text": "Book the room.", "owner": "Lee", "due": None, "cites": ["[02:00]"]},
    {"text": "Share the slides.", "owner": None, "due": None, "cites": ["[03:00]"]},
]


def test_follow_ups_say_who_where_and_how_sure_and_yours_come_first(store):
    add(store, "m1", "Standup", action_items=ITEMS, open_questions=[{"text": "Who pays for the venue?", "cites": ["[05:00]"]}])
    store.update_recording("m1", notes_style="meeting")  # a meeting's open questions are yours; a lecture's are not
    items = follow_ups(store, TZ, now=NOW)
    assert [i["text"] for i in items][:2] == ["Send the survey to the class.", "Share the slides."]  # yours first
    first = items[0]
    assert (first["owner"], first["due"], first["recording"], first["kind"], first["certainty"]) == (
        "me", "Friday", "Standup", "action", "not checked")
    assert first["start"] == 60.0 and first["mine"] is True
    assert {i["kind"] for i in items} == {"action", "question"} and next(i for i in items if i["text"] == "Book the room.")["mine"] is False


def test_ticked_off_items_stay_gone_even_after_the_notes_are_rewritten(store):
    add(store, "m1", "Standup", action_items=ITEMS)
    key = follow_up_key("m1", "action_items", "Send the survey to the class.")
    store.set_follow_up(key, "done")
    assert "Send the survey to the class." not in [i["text"] for i in follow_ups(store, TZ, now=NOW)]
    store.save_notes("m1", notes_from_writer({"action_items": ITEMS}), writer="w2", checker=None, check_detail=None)
    assert "Send the survey to the class." not in [i["text"] for i in follow_ups(store, TZ, now=NOW)]
    shown = follow_ups(store, TZ, now=NOW, include_done=True)
    assert next(i for i in shown if i["key"] == key)["state"] == "done"
    store.set_follow_up(key, None)
    assert "Send the survey to the class." in [i["text"] for i in follow_ups(store, TZ, now=NOW)]
    with pytest.raises(ValueError):
        store.set_follow_up(key, "maybe")


def test_old_trashed_and_unfinished_recordings_are_left_out(store):
    add(store, "old", "Old", days_ago=60, action_items=ITEMS)
    add(store, "bin", "Binned", action_items=ITEMS)
    store.soft_delete("bin")
    add(store, "fresh", "Fresh", action_items=ITEMS[:1])
    store.update_notes_status("fresh", "writing")
    assert follow_ups(store, TZ, now=NOW) == []


def test_revision_priorities_are_what_was_stressed_recently(store):
    lecture = [{"heading": "OAuth", "kind": "stressed", "term": None, "text": "Tokens expire; on the exam.", "cites": ["[04:00]"]},
               {"heading": "OAuth", "kind": "point", "term": None, "text": "A side remark.", "cites": ["[05:00]"]}]
    add(store, "a", "Security week", days_ago=2, subject="Machine Learning", lecture=lecture)
    add(store, "b", "Older", days_ago=30, lecture=lecture)
    found = revision_priorities(store, TZ, now=NOW)
    assert [(i["text"], i["recording"], i["start"]) for i in found] == [("Tokens expire; on the exam.", "Security week", 240.0)]


def test_the_briefing_pulls_it_together(store):
    add(store, "m1", "Standup", action_items=ITEMS, subject="Machine Learning")
    store.add_memory("ongoing", "Finishing the FYP proposal", now=NOW)
    store.add_memory("goal", "Get an A", now=NOW)
    store.add_reminder((NOW + timedelta(hours=3)).isoformat(timespec="seconds"), "Record Machine Learning")
    store.add_reminder((NOW + timedelta(days=3)).isoformat(timespec="seconds"), "Not today")
    store.create_recording("bad", "Broken", None, "en", "bad.m4a")
    store.update_recording("bad", status="failed", duration_sec=0.0)
    day = briefing(store, TZ, now=NOW)
    assert day["weekday"] == "Monday" and day["date"] == "2026-10-19"
    assert [r["text"] for r in day["reminders"]] == ["Record Machine Learning"]
    assert day["follow_ups"]["total"] == 3 and day["follow_ups"]["mine"] == 2
    assert [m["text"] for m in day["remembered"]] == ["Finishing the FYP proposal"]  # only what is ongoing
    assert [a["title"] for a in day["needs_attention"]] == ["Broken"]


def test_the_api(tmp_path):
    app = build(tmp_path, FakeTranscriber(), None, None)
    store = app.state.store
    add(store, "m1", "Standup", days_ago=0, action_items=ITEMS)
    with TestClient(app) as client:
        assert client.get("/api/briefing").status_code == 200
        items = client.get("/api/follow-ups").json()["items"]
        key = items[0]["key"]
        assert client.post(f"/api/follow-ups/{key}/done").json() == {"key": key, "state": "done"}
        assert key not in [i["key"] for i in client.get("/api/follow-ups").json()["items"]]
        assert key in [i["key"] for i in client.get("/api/follow-ups", params={"done": True}).json()["items"]]
        assert client.post(f"/api/follow-ups/{key}/open").json()["state"] is None
        assert client.post(f"/api/follow-ups/{key}/whatever").status_code == 400


def test_a_lectures_own_questions_are_not_follow_ups(store):
    add(store, "l1", "Lecture", open_questions=[{"text": "How do robots know where to drill?", "cites": ["[05:00]"]}],
        action_items=[{"text": "Review the videos.", "owner": None, "due": None, "cites": ["[01:00]"]}])
    assert [i["text"] for i in follow_ups(store, TZ, now=NOW)] == ["Review the videos."]
