"""Class sessions (parts of one lecture), name suggestions, and keeping the phone's original name."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from transcriber.naming import clean, compose, original_stem, suggest_names
from transcriber.sessions import class_sessions

from .helpers import FakeLLM, FakeTranscriber
from .test_app import build

TZ = "Asia/Tashkent"


def rec(id, start, minutes, subject="Machine Learning", subject_id=1, original="New Recording 1.m4a", deleted=False, title="T"):
    return SimpleNamespace(id=id, title=title, subject=subject, subject_id=subject_id, recorded_at=start,
                           created_at=start, duration_sec=minutes * 60, original_filename=original,
                           deleted_at="x" if deleted else None)


def test_parts_in_a_row_on_one_day_are_one_session():
    sessions = class_sessions([
        rec("a", "2026-09-28T05:05:00+00:00", 28),   # ends 05:33
        rec("b", "2026-09-28T05:35:00+00:00", 13),   # 2 min pause
        rec("c", "2026-09-28T05:50:00+00:00", 3),    # 2 min pause
    ], TZ)
    assert [sessions[i]["index"] for i in "abc"] == [1, 2, 3]
    assert sessions["a"]["count"] == 3 and sessions["a"]["ids"] == ["a", "b", "c"] and sessions["a"]["minutes"] == 44
    assert sessions["b"]["title"] == "Machine Learning — Mon 28 Sep" and sessions["b"]["combined"] is False


def test_a_long_pause_another_subject_another_day_or_two_devices_at_once_are_not_parts():
    day = "2026-09-28T05:00:00+00:00"
    assert class_sessions([rec("a", day, 10), rec("b", "2026-09-28T06:30:00+00:00", 10)], TZ) == {}            # 80 min pause
    assert class_sessions([rec("a", day, 10), rec("b", "2026-09-28T05:12:00+00:00", 10, subject_id=2)], TZ) == {}
    assert class_sessions([rec("a", day, 10), rec("b", "2026-09-29T05:12:00+00:00", 10)], TZ) == {}
    assert class_sessions([rec("a", day, 30), rec("b", "2026-09-28T05:05:00+00:00", 30)], TZ) == {}           # overlap


def test_practice_deleted_and_combined_recordings_are_left_out_and_a_combined_session_is_marked():
    start = "2026-09-28T05:00:00+00:00"
    parts = [rec("a", start, 10), rec("b", "2026-09-28T05:12:00+00:00", 10)]
    assert class_sessions(parts + [rec("p", "2026-09-28T05:23:00+00:00", 5, original="Practice session")], TZ)["a"]["count"] == 2
    assert class_sessions(parts + [rec("d", "2026-09-28T05:23:00+00:00", 5, deleted=True)], TZ)["a"]["count"] == 2
    joined = rec("c", start, 23, original="2 combined: New Recording 1.m4a, New Recording 2.m4a")
    sessions = class_sessions(parts + [joined], TZ)
    assert "c" not in sessions and sessions["a"]["combined"] is True


def test_compose_and_clean_and_original_stem():
    assert compose("Neural networks") == "Neural networks"
    assert compose("Neural networks", 2) == "Neural networks (part 2)"
    assert clean('  “Intro to   perceptrons.” ') == "Intro to perceptrons"
    assert original_stem("New Recording 99.m4a") == "New Recording 99"
    assert original_stem("Live recording") is None and original_stem("Practice session") is None


def test_suggestions_come_from_the_notes_with_the_original_name_last(tmp_path):
    app = build(tmp_path, FakeTranscriber(), FakeLLM("groq:x", []), None)
    store = app.state.store
    store.create_recording("r1", "New Recording 99 — 2026-09-28", None, "en", "New Recording 99.m4a")
    store.update_recording("r1", recorded_at="2026-09-28T05:05:00+00:00", duration_sec=600)
    store.save_transcript("r1", "Today we start with perceptrons.", [], "fake")
    writer = FakeLLM("groq:x", [{"titles": ["How perceptrons learn", "Weights and bias", "Third"]}])
    out = suggest_names(store, writer, store.get_recording("r1"), TZ)
    assert [s["title"] for s in out] == ["How perceptrons learn", "Weights and bias", "Mon 28 Sep", "New Recording 99"]
    assert out[0]["source"] == "from your notes" and out[-1]["source"] == "original name"
    assert "perceptrons" in writer.calls[0]["user"]


def test_suggestions_still_work_without_a_model_and_mark_the_parts(tmp_path):
    app = build(tmp_path, FakeTranscriber(), FakeLLM("groq:x", []), None)
    store = app.state.store
    store.create_recording("r1", "Old name", None, "en", "x.m4a")
    store.update_recording("r1", recorded_at="2026-09-28T05:05:00+00:00", duration_sec=600)
    session = {"index": 2, "count": 3, "combined": False}
    out = suggest_names(store, None, store.get_recording("r1"), TZ, session)
    assert [s["title"] for s in out] == ["Mon 28 Sep (part 2)", "x"]


def test_the_api_suggests_names_searches_the_original_name_and_keeps_it_when_combining(tmp_path):
    app = build(tmp_path, FakeTranscriber(), FakeLLM("groq:x", []), None)
    store = app.state.store
    for rid, start, original in (("a", "2026-09-28T05:05:00+00:00", "New Recording 99.m4a"),
                                 ("b", "2026-09-28T05:35:00+00:00", "University of Northfield 21.m4a")):
        store.create_recording(rid, f"Class {rid}", None, "en", original)
        store.update_recording(rid, recorded_at=start, duration_sec=1500)
    with TestClient(app) as client:
        listed = {r["id"]: r for r in client.get("/api/recordings").json()}
        assert listed["a"]["session"]["count"] == 2 and listed["b"]["session"]["index"] == 2
        assert [r["id"] for r in client.get("/api/recordings", params={"q": "Northfield 21"}).json()] == ["b"]
        body = client.get("/api/recordings/a/name-suggestions").json()
        assert body["original"] == "New Recording 99.m4a"
        assert body["suggestions"][0]["title"] == "Mon 28 Sep (part 1)"
        assert client.get("/api/recordings/a").json()["session"]["ids"] == ["a", "b"]
        assert client.get("/api/recordings/nope/name-suggestions").status_code == 404


def test_a_joined_class_remembers_which_combined_recording_holds_it_and_each_class_is_listed_once():
    from transcriber.sessions import unique_sessions
    start = "2026-09-28T05:00:00+00:00"
    parts = [rec("a", start, 10), rec("b", "2026-09-28T05:12:00+00:00", 10)]
    joined = rec("c", start, 23, original="2 combined: New Recording 1.m4a, New Recording 2.m4a", title="Machine Learning — Mon 28 Sep")
    sessions = class_sessions(parts + [joined], TZ)
    assert sessions["a"]["combined_id"] == "c" and sessions["b"]["combined_title"] == "Machine Learning — Mon 28 Sep"
    classes = unique_sessions(sessions)
    assert len(classes) == 1 and classes[0]["ids"] == ["a", "b"] and "index" not in classes[0]


def test_the_api_lists_classes_skips_unfinished_ones_and_shows_the_parts_of_a_joined_class(tmp_path):
    app = build(tmp_path, FakeTranscriber(), FakeLLM("groq:x", []), None)
    store = app.state.store
    for rid, start in (("a", "2026-09-28T05:05:00+00:00"), ("b", "2026-09-28T05:35:00+00:00")):
        store.create_recording(rid, f"Part {rid}", None, "en", f"{rid}.m4a")
        store.update_recording(rid, recorded_at=start, duration_sec=1500)
    with TestClient(app) as client:
        listed = client.get("/api/class-sessions").json()["sessions"]
        assert len(listed) == 1 and listed[0]["count"] == 2 and listed[0]["ready"] is False  # still queued
        result = client.post("/api/recordings/combine-classes").json()
        assert result["combined"] == [] and "hasn't finished" in result["skipped"][0]["reason"]
        # a recording that was joined from them shows its parts, and the parts point back to it
        store.create_recording("j", "Mon 28 Sep", None, "en", "2 combined: a.m4a, b.m4a")
        store.update_recording("j", recorded_at="2026-09-28T05:05:00+00:00", duration_sec=3100)
        joined = client.get("/api/recordings/j").json()
        assert [p["id"] for p in joined["parts"]] == ["a", "b"]
        part = client.get("/api/recordings/b").json()
        assert part["session"]["combined"] is True and part["session"]["combined_id"] == "j"
        assert client.get("/api/class-sessions").json()["sessions"][0]["combined"] is True


def test_name_all_classes_suggests_a_topic_name_for_each_joined_class_only(tmp_path):
    writer = FakeLLM("groq:x", [{"titles": ["How perceptrons learn", "Weights and bias"]}])
    app = build(tmp_path, FakeTranscriber(), writer, None)
    store = app.state.store
    store.create_recording("plain", "A normal recording", None, "en", "x.m4a")
    store.create_recording("j", "Machine Learning — Mon 28 Sep", None, "en", "2 combined: a.m4a, b.m4a")
    store.update_recording("j", recorded_at="2026-09-28T05:05:00+00:00", duration_sec=3000)
    store.save_transcript("j", "Today we start with perceptrons.", [], "fake")
    with TestClient(app) as client:
        classes = client.get("/api/class-name-suggestions").json()["classes"]
        assert [c["id"] for c in classes] == ["j"]
        assert classes[0]["suggestion"] == "How perceptrons learn" and classes[0]["source"] == "from your notes"
        assert classes[0]["title"] == "Machine Learning — Mon 28 Sep"


# --- "Rename" for many recordings at once: all of them, or only the ones that still have a standard name

import pytest  # noqa: E402

from transcriber.naming import is_standard_name, suggest_bulk_names  # noqa: E402
from transcriber.providers.base import RateLimited  # noqa: E402


@pytest.mark.parametrize("title", [
    "New Recording 99", "New Recording 56 — 2026-03-02", "Recording 33", "recording", "Untitled", "Voice Memo 7",
    "University Road", "University Rd", "123 Main Street", "Park Ave", "University", "Shahrah-e-Faisal Road",
    "Fri 25 Sep", "Machine Learning — Mon 28 Sep", "Machine Learning, Wed 23 Sep",
])
def test_standard_names_and_addresses_are_recognised(title):
    assert is_standard_name(title)


@pytest.mark.parametrize("title", [
    "Business model canvas", "Midterm review: six topics", "Why the king wore no clothes",
    "How university admissions shape the labour market over time",  # a real topic that happens to mention a place word
    "University admissions", "Campus safety review", "Drive thru operations", "Dr Smith on ERP", "Lane departure systems",
    "Street food economics", "Highway toll analytics", "St Louis case study", "3D printing", "5 whys analysis",
    "Wed 23 Strategy", "3 Phase Power", "5G Networks",
])
def test_real_topic_names_are_left_alone(title):
    assert not is_standard_name(title)


def done_recording(store, rid, title, subject="Operations"):
    store.create_recording(rid, title, store.get_or_create_subject(subject) if subject else None, "en", f"{rid}.m4a")
    store.save_transcript(rid, f"words of {rid}", [], "groq")
    store.update_recording(rid, status="done", recorded_at="2026-10-07T05:00:00+00:00", duration_sec=1800.0)


def test_bulk_names_ask_the_model_once_per_batch_and_fall_back_when_it_cannot(tmp_path):
    from transcriber.store import Store
    store = Store(tmp_path / "app.db")
    for n in range(3):
        done_recording(store, f"r{n}", f"New Recording {n}")
    recs = store.list_recordings()
    llm = FakeLLM("groq:x", [{"titles": ["Supply chains", "Inventory", "Demand planning"]}])
    out, problem = suggest_bulk_names(store, llm, recs, TZ)
    assert problem is None and len(llm.calls) == 1 and sorted(o["suggestion"] for o in out) == ["Demand planning", "Inventory", "Supply chains"]
    assert all(o["source"] == "from your notes" for o in out)
    broken, problem = suggest_bulk_names(store, FakeLLM("groq:x", [{"titles": ["only one"]}]), recs, TZ)
    assert len(broken) == 3 and all(o["source"] != "from your notes" for o in broken)  # subject and date instead
    assert "couldn't use" in problem
    limited, problem = suggest_bulk_names(store, FakeLLM("groq:x", [RateLimited(60.0, "Groq rate limit")]), recs, TZ)
    assert len(limited) == 3 and "usage limit" in problem  # the dialog can say why the names are plain
    store.close()


def test_the_review_can_cover_all_recordings_or_only_standard_names(tmp_path):
    app = build(tmp_path, FakeTranscriber(), FakeLLM("groq:x", [{"titles": ["A topic"]}] * 4), None)
    store = app.state.store
    done_recording(store, "a", "New Recording 5")
    done_recording(store, "b", "Midterm review")
    done_recording(store, "c", "University Rd", subject="Law")
    with TestClient(app) as client:
        ids = lambda **params: sorted(c["id"] for c in client.get("/api/class-name-suggestions", params=params).json()["classes"])  # noqa: E731
        assert ids(scope="standard") == ["a", "c"]
        assert ids(scope="all") == ["a", "b", "c"]
        law = store.get_or_create_subject("Law")
        assert ids(scope="all", subject_id=law) == ["c"]
        assert ids() == []  # the old default: joined classes only, and there are none


def test_a_list_of_joined_files_is_not_an_original_name_and_a_date_name_is_not_replaced_by_a_date_name(tmp_path):
    assert original_stem("3 combined: New Recording 27.m4a, New Recording 26.m4a") is None
    from transcriber.store import Store
    store = Store(tmp_path / "app.db")
    done_recording(store, "d", "Machine Learning, Wed 30 Sep", subject="Machine Learning")
    store.update_recording("d", recorded_at="2026-09-30T05:00:00+00:00")
    out, problem = suggest_bulk_names(store, None, store.list_recordings(), TZ)
    assert out == [] and problem is None
    store.close()
