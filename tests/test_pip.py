"""Pip: skipping and adding classes, reminders (with undo), typed requests, and the background reminders."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from transcriber.calendar import build_calendar, class_slots
from transcriber.pip import UNDO_TOOLS, Actions, ClassReminders, parse_request
from transcriber.store import Store

from .helpers import FakeLLM, FakeTranscriber
from .test_app import build

TZ = "Asia/Tashkent"
SCHEDULE = {"days": [0, 2], "start": "10:00", "minutes": 60, "from": None, "until": None}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    s.update_settings({"time_zone": TZ})  # the tests' clock is UTC+5
    yield s
    s.close()


@pytest.fixture
def tech(store):
    subject = store.create_subject("Machine Learning")
    store.update_subject(subject.id, schedule=dict(SCHEDULE))
    return store.get_subject(subject.id)


def statuses(store, first, last, today):
    return {(c["date"]): c["status"] for c in build_calendar(store, TZ, first, last, [], today=today)["classes"]}


def test_skipped_dates_are_cancelled_and_extra_classes_are_added():
    schedule = {**SCHEDULE, "skip": ["2026-10-12"], "extra": [{"date": "2026-10-16", "start": "14:30", "minutes": 45}]}
    slots = class_slots(schedule, TZ, date(2026, 10, 12), date(2026, 10, 18))
    assert [(s.start.date().isoformat(), s.cancelled) for s in slots] == [
        ("2026-10-12", True), ("2026-10-14", False), ("2026-10-16", False)]
    assert slots[-1].start.hour == 14 and slots[-1].end - slots[-1].start == timedelta(minutes=45)


def test_no_class_this_week_cancels_every_class_and_undo_brings_them_back(store, tech):
    actions = Actions(store, TZ)
    result = actions.run_all([{"tool": "skip_classes", "subject": "machine learning", "from": "2026-10-12", "to": "2026-10-18"}])
    assert result["problems"] == [] and "Mon 12 Oct" in result["done"][0]["summary"] and "Wed 14 Oct" in result["done"][0]["summary"]
    assert store.get_subject(tech.id).schedule["skip"] == ["2026-10-12", "2026-10-14"]
    assert statuses(store, date(2026, 10, 12), date(2026, 10, 18), date(2026, 10, 14)) == {
        "2026-10-12": "cancelled", "2026-10-14": "cancelled"}
    actions.run_all(result["undo"], allowed=UNDO_TOOLS)
    assert "skip" not in store.get_subject(tech.id).schedule
    assert statuses(store, date(2026, 10, 12), date(2026, 10, 18), date(2026, 10, 14)) == {
        "2026-10-12": "missing", "2026-10-14": "today"}


def test_skipping_with_no_subject_named_covers_every_timetable_and_nothing_to_skip_is_explained(store, tech):
    actions = Actions(store, TZ)
    assert actions.run_all([{"tool": "skip_classes", "subject": "", "from": "2026-10-14", "to": "2026-10-14"}])["done"]
    again = actions.run_all([{"tool": "skip_classes", "subject": "", "from": "2026-10-14", "to": "2026-10-14"}])
    assert again["done"] == [] and "no classes to skip" in again["problems"][0]
    missing = actions.run_all([{"tool": "skip_classes", "subject": "Physics", "from": "2026-10-14", "to": "2026-10-14"}])
    assert "couldn't find" in missing["problems"][0]


def test_restore_and_add_a_class_and_their_undo(store, tech):
    actions = Actions(store, TZ)
    actions.run_all([{"tool": "skip_classes", "subject": "Machine Learning", "from": "2026-10-14", "to": "2026-10-14"}])
    moved = actions.run_all([{"tool": "add_class", "subject": "Machine Learning", "from": "2026-10-16", "start": "11:30", "minutes": 0}])
    assert "Fri 16 Oct at 11:30" in moved["done"][0]["summary"]
    assert store.get_subject(tech.id).schedule["extra"] == [{"date": "2026-10-16", "start": "11:30", "minutes": 60}]
    actions.run_all(moved["undo"], allowed=UNDO_TOOLS)
    assert "extra" not in store.get_subject(tech.id).schedule
    back = actions.run_all([{"tool": "restore_classes", "subject": "Machine Learning", "from": "2026-10-12", "to": "2026-10-18"}])
    assert back["done"] and "skip" not in store.get_subject(tech.id).schedule
    assert actions.run_all([{"tool": "restore_classes", "subject": "", "from": "2026-10-12", "to": "2026-10-18"}])["problems"]


def test_reminders_are_stored_in_utc_and_the_past_is_refused(store, tech):
    actions = Actions(store, TZ)
    ok = actions.run_all([{"tool": "remind", "subject": "Machine Learning", "when": "2030-03-04T09:45", "text": "Record Machine Learning"}])
    assert "Mon 4 Mar at 09:45" in ok["done"][0]["summary"]
    [reminder] = store.list_reminders()
    assert reminder["at"] == "2030-03-04T04:45:00+00:00" and reminder["subject"] == "Machine Learning"
    past = actions.run_all([{"tool": "remind", "subject": "", "when": "2020-01-01T09:00", "text": "x"}])
    assert "already passed" in past["problems"][0]
    actions.run_all(ok["undo"], allowed=UNDO_TOOLS)
    assert store.list_reminders() == []


def test_only_the_listed_tools_can_run_from_a_request_and_undo_steps_cannot(store, tech):
    actions = Actions(store, TZ)
    assert actions.run_all([{"tool": "delete_reminder", "id": 1}])["problems"] == ["I can't do that yet"]
    assert actions.run_all([{"tool": "skip_classes", "subject": "", "from": "2026-10-14", "to": "2026-10-14"}], allowed=UNDO_TOOLS)["done"] == []
    assert actions.run_all([{"tool": "rm -rf", "subject": ""}])["problems"]


def test_a_typed_request_is_given_todays_date_and_your_timetables(store, tech):
    store.update_subject(tech.id, schedule={**SCHEDULE, "skip": ["2026-10-05"]})
    llm = FakeLLM("groq:x", [{"say": "No problem!", "actions": [
        {"tool": "skip_classes", "subject": "Machine Learning", "from": "2026-10-12", "to": "2026-10-18", "start": "", "minutes": 0,
         "when": "", "text": ""},
        {"tool": "format_disk", "subject": ""}]}])
    now = datetime(2026, 10, 14, 21, 30, tzinfo=ZoneInfo(TZ))
    plan = parse_request(llm, "no machine learning this week", store, TZ, now=now)
    prompt = llm.calls[0]["user"]
    assert "Wednesday 2026-10-14" in prompt and "This week: 2026-10-12 to 2026-10-18" in prompt
    assert "Machine Learning: every Mon, Wed at 10:00" in prompt and "no class on 2026-10-05" in prompt
    assert plan["say"] == "No problem!" and [a["tool"] for a in plan["actions"]] == ["skip_classes"]
    assert plan["actions"][0]["minutes"] == 0


def test_due_reminders_reach_the_bell_and_the_desktop_once(store):
    shown = []
    reminders = ClassReminders(store, notify=lambda title, body: shown.append((title, body)))
    store.add_reminder("2026-10-19T04:45:00+00:00", "Record Machine Learning")
    store.add_reminder("2026-10-19T09:00:00+00:00", "Later")
    now = datetime(2026, 10, 19, 4, 46, tzinfo=UTC)
    assert reminders.tick(now) == ["Record Machine Learning"]
    assert reminders.tick(now) == [] and shown == [("Pip", "Record Machine Learning")]
    [note] = store.list_notifications()
    assert note["kind"] == "reminder" and note["title"] == "Record Machine Learning" and note["recording_id"] is None


def test_a_class_is_nudged_once_shortly_before_it_starts_unless_recorded_or_cancelled(store, tech):
    shown = []
    reminders = ClassReminders(store, notify=lambda title, body: shown.append(body))
    assert reminders.tick(datetime(2026, 10, 19, 4, 30, tzinfo=UTC)) == []            # 09:30 local: too early
    assert reminders.tick(datetime(2026, 10, 19, 4, 52, tzinfo=UTC))[0].startswith("Machine Learning starts at 10:00")
    assert reminders.tick(datetime(2026, 10, 19, 4, 58, tzinfo=UTC)) == []            # already nudged
    store.update_subject(tech.id, schedule={**SCHEDULE, "skip": ["2026-10-21"]})
    assert reminders.tick(datetime(2026, 10, 21, 4, 55, tzinfo=UTC)) == []            # cancelled
    store.update_subject(tech.id, schedule=dict(SCHEDULE))
    store.update_settings({"class_reminders": "0"})
    assert reminders.tick(datetime(2026, 10, 26, 4, 55, tzinfo=UTC)) == []            # switched off


def test_api_typed_request_quick_action_undo_and_reminders(tmp_path):
    writer = FakeLLM("groq:x", [{"say": "Enjoy the break!", "actions": [
        {"tool": "skip_classes", "subject": "Machine Learning", "from": "2026-10-12", "to": "2026-10-18", "start": "", "minutes": 0,
         "when": "", "text": ""}]}])
    app = build(tmp_path, FakeTranscriber(), writer, None)
    with TestClient(app) as client:
        subject = client.post("/api/subjects", json={"name": "Machine Learning"}).json()
        client.patch(f"/api/subjects/{subject['id']}", json={"schedule": SCHEDULE})
        said = client.post("/api/pip/command", json={"text": "no machine learning this week"}).json()
        assert said["say"] == "Enjoy the break!" and said["done"] and said["problems"] == []
        shown = client.get("/api/subjects").json()
        assert next(s for s in shown if s["id"] == subject["id"])["schedule"]["skip"] == ["2026-10-12", "2026-10-14"]
        # saving the timetable dialog again keeps the skipped dates
        client.patch(f"/api/subjects/{subject['id']}", json={"schedule": {**SCHEDULE, "start": "10:30"}})
        kept = next(s for s in client.get("/api/subjects").json() if s["id"] == subject["id"])["schedule"]
        assert kept["start"] == "10:30" and kept["skip"] == ["2026-10-12", "2026-10-14"]
        undone = client.post("/api/pip/undo", json={"undo": said["undo"]}).json()
        assert undone["say"] == "Undone." and "skip" not in next(
            s for s in client.get("/api/subjects").json() if s["id"] == subject["id"])["schedule"]
        # a quick button, a reminder, and cancelling it
        acted = client.post("/api/pip/act", json={"actions": [
            {"tool": "remind", "subject": "Machine Learning", "when": "2030-03-04T09:45", "text": "Record Machine Learning"}]}).json()
        [reminder] = client.get("/api/reminders").json()["reminders"]
        assert acted["done"] and reminder["text"] == "Record Machine Learning"
        assert client.delete(f"/api/reminders/{reminder['id']}").status_code == 204
        assert client.get("/api/reminders").json()["reminders"] == []
        assert client.post("/api/pip/command", json={"text": "   "}).status_code == 400
        # undo cannot be used to run ordinary actions
        sneaky = client.post("/api/pip/undo", json={"undo": [{"tool": "skip_classes", "subject": "", "from": "2026-10-14", "to": "2026-10-14"}]}).json()
        assert sneaky["done"] == []


def test_without_a_notes_writer_pip_says_so(tmp_path):
    app = build(tmp_path, FakeTranscriber(), None, None)
    with TestClient(app) as client:
        said = client.post("/api/pip/command", json={"text": "hello"}).json()
        assert "notes writer" in said["say"] and said["done"] == []


# ---- the bigger changes: planned first, done only when approved, each with an undo

from transcriber.pip import BIG_TOOLS, SMALL_TOOLS, Planner  # noqa: E402


def make_recording(store, rid, title, original, start=None, minutes=25, subject_id=None, text=None):
    store.create_recording(rid, title, subject_id, "en", original)
    store.update_recording(rid, recorded_at=start, duration_sec=minutes * 60, status="done")
    if text:
        store.save_transcript(rid, text, [], "fake")


def test_a_rename_plan_names_only_recordings_that_still_have_their_phone_names(store):
    make_recording(store, "a", "New Recording 7 — 2026-10-01", "New Recording 7.m4a", "2026-10-01T05:05:00+00:00", text="Perceptrons learn weights.")
    make_recording(store, "b", "Already named well", "New Recording 8.m4a", "2026-10-02T05:05:00+00:00", text="Something else.")
    llm = FakeLLM("groq:x", [{"titles": ["How perceptrons learn", "Weights and bias"]}])
    plan = Planner(store, llm, TZ).plan({"tool": "rename_recordings", "text": "", "subject": ""})
    assert plan["kind"] == "rename" and plan["items"] == [{"label": "New Recording 7 — 2026-10-01 → How perceptrons learn"}]
    assert store.get_recording("a").title == "New Recording 7 — 2026-10-01"        # planning changed nothing
    actions = Actions(store, TZ)
    done = actions.apply_plan("rename", plan["apply"])
    assert "Renamed 1 recording" in done["done"][0]["summary"] and store.get_recording("a").title == "How perceptrons learn"
    assert store.get_recording("a").original_filename == "New Recording 7.m4a"      # the phone name stays
    actions.run_all(done["undo"], allowed=UNDO_TOOLS)
    assert store.get_recording("a").title == "New Recording 7 — 2026-10-01"


def test_rename_needs_the_notes_writer_and_has_nothing_to_do_when_all_are_named(store):
    from transcriber.pip import PipError
    make_recording(store, "a", "New Recording 7 — 2026-10-01", "New Recording 7.m4a", "2026-10-01T05:05:00+00:00")
    with pytest.raises(PipError, match="notes writer"):
        Planner(store, None, TZ).plan({"tool": "rename_recordings", "text": "", "subject": ""})
    store.update_recording("a", title="Perceptrons")
    with pytest.raises(PipError, match="already has a name"):
        Planner(store, FakeLLM("groq:x", []), TZ).plan({"tool": "rename_recordings", "text": "", "subject": ""})


def test_a_filing_plan_matches_unsorted_recordings_to_the_class_they_were_recorded_in(store, tech):
    make_recording(store, "mon", "Lecture", "a.m4a", "2026-10-12T05:05:00+00:00", 25)      # Monday 10:05 local
    make_recording(store, "sat", "Weekend thing", "b.m4a", "2026-10-17T05:05:00+00:00", 25)  # no class on Saturday
    plan = Planner(store, None, TZ).plan({"tool": "file_recordings"})
    assert plan["items"] == [{"label": "Lecture → Machine Learning"}]
    actions = Actions(store, TZ)
    done = actions.apply_plan("file", plan["apply"])
    assert store.get_recording("mon").subject == "Machine Learning" and store.get_recording("sat").subject is None
    assert actions.apply_plan("file", plan["apply"])["problems"] == ["Nothing needed changing"]  # already filed
    actions.run_all(done["undo"], allowed=UNDO_TOOLS)
    assert store.get_recording("mon").subject_id is None


def test_a_combine_plan_lists_the_classes_that_are_ready(store):
    from transcriber.pip import PipError
    make_recording(store, "p1", "Part one", "a.m4a", "2026-09-28T05:05:00+00:00", 28)
    make_recording(store, "p2", "Part two", "b.m4a", "2026-09-28T05:35:00+00:00", 13)
    plan = Planner(store, None, TZ).plan({"tool": "combine_classes"})
    assert plan["items"] == [{"label": "Session on Mon 28 Sep: 2 parts, 41 min"}] and "originals are kept" in plan["note"]
    store.update_recording("p2", status="transcribing")
    with pytest.raises(PipError, match="still transcribing"):
        Planner(store, None, TZ).plan({"tool": "combine_classes"})


def test_the_api_plans_then_applies_and_never_runs_big_tools_from_a_quick_action(tmp_path):
    writer = FakeLLM("groq:x", [{"titles": ["How perceptrons learn", "Weights"]}])
    app = build(tmp_path, FakeTranscriber(), writer, None)
    store = app.state.store
    make_recording(store, "a", "New Recording 7 — 2026-10-01", "New Recording 7.m4a", "2026-10-01T05:05:00+00:00", text="Perceptrons.")
    with TestClient(app) as client:
        planned = client.post("/api/pip/plan", json={"kind": "rename"}).json()
        assert planned["plans"][0]["title"] == "Rename 1 recordings" and store.get_recording("a").title.startswith("New Recording")
        applied = client.post("/api/pip/apply", json={"kind": "rename", "apply": planned["plans"][0]["apply"]}).json()
        assert applied["done"] and store.get_recording("a").title == "How perceptrons learn"
        undone = client.post("/api/pip/undo", json={"undo": applied["undo"]}).json()
        assert undone["say"] == "Undone." and store.get_recording("a").title.startswith("New Recording")
        sneaky = client.post("/api/pip/act", json={"actions": [{"tool": "rename_recordings", "text": "all"}]}).json()
        assert sneaky["done"] == [] and sneaky["problems"] and store.get_recording("a").title.startswith("New Recording")
        assert client.post("/api/pip/plan", json={"kind": "delete"}).status_code == 400
        assert client.post("/api/pip/apply", json={"kind": "delete", "apply": {}}).status_code == 400


def test_a_typed_request_returns_plans_and_where_to_go_without_changing_anything(tmp_path):
    reply = {"say": "Here's my plan.", "actions": [
        {"tool": "rename_recordings", "subject": "", "from": "", "to": "", "start": "", "minutes": 0, "when": "", "text": "unnamed"},
        {"tool": "open_view", "subject": "", "from": "", "to": "", "start": "", "minutes": 0, "when": "", "text": "calendar"}]}
    writer = FakeLLM("groq:x", [reply, {"titles": ["How perceptrons learn"]}])
    app = build(tmp_path, FakeTranscriber(), writer, None)
    store = app.state.store
    make_recording(store, "a", "New Recording 7 — 2026-10-01", "New Recording 7.m4a", "2026-10-01T05:05:00+00:00", text="Perceptrons.")
    with TestClient(app) as client:
        said = client.post("/api/pip/command", json={"text": "name my recordings and show me the calendar"}).json()
        assert said["done"] == [] and said["plans"][0]["kind"] == "rename" and said["open"] == {"view": "calendar", "subject": ""}
        assert store.get_recording("a").title.startswith("New Recording")
    assert set(BIG_TOOLS).isdisjoint(SMALL_TOOLS)
