"""Calendar: class timetables, which classes were recorded, and old phone recordings still sitting on the laptop."""
import subprocess
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from transcriber import audio
from transcriber.calendar import build_calendar, class_slots, scan_folders
from transcriber.store import Store

from .helpers import FakeLLM, FakeTranscriber, synth_audio, undo_privacy_migration
from .test_app import build

TZ = "Asia/Tashkent"
SCHEDULE = {"days": [0, 2], "start": "10:00", "minutes": 60, "from": "2026-09-28", "until": None}  # Mon, Wed


def stamped(tmp_path, name, stamp, seconds=3.0):
    wav = synth_audio(tmp_path / f"{name}.wav", [("tone", seconds)])
    out = tmp_path / name
    subprocess.run([audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-metadata",
                    f"creation_time={stamp}", "-c:a", "aac", str(out)], check=True)
    wav.unlink()
    return out


def test_class_slots_follow_the_timetable_in_your_time_zone():
    slots = class_slots(SCHEDULE, TZ, date(2026, 9, 28), date(2026, 10, 8))
    assert [s.start.strftime("%a %d %H:%M") for s in slots] == ["Mon 28 10:00", "Wed 30 10:00", "Mon 05 10:00",
                                                                 "Wed 07 10:00"]
    assert slots[0].start.utcoffset().total_seconds() == 5 * 3600  # UTC+5, whatever the laptop's clock says
    assert (slots[0].end - slots[0].start).total_seconds() == 3600


def test_until_ends_the_term():
    assert len(class_slots({**SCHEDULE, "until": "2026-09-30"}, TZ, date(2026, 9, 1), date(2026, 12, 31))) == 2


def test_scan_reads_stamps_and_remembers(tmp_path):
    folder = tmp_path / "Downloads"
    (folder / "sub").mkdir(parents=True)
    stamped(folder, "a.m4a", "2026-10-05T05:05:18Z")
    stamped(folder / "sub", "b.m4a", "2026-10-07T05:02:16Z")
    (folder / "notes.txt").write_text("not audio")
    cache = tmp_path / "scan.json"
    files = scan_folders([folder], cache)
    assert sorted(f["name"] for f in files) == ["a.m4a", "b.m4a"]
    assert {f["recorded_at"] for f in files} == {"2026-10-05T05:05:18+00:00", "2026-10-07T05:02:16+00:00"}
    assert all(f["duration_sec"] == pytest.approx(3.0, abs=0.2) for f in files)
    assert cache.exists()
    assert scan_folders([folder], cache) == files  # second time from the cache


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


def test_calendar_marks_recorded_laptop_only_and_missing(store):
    tech = store.create_subject("Machine Learning")
    store.update_subject(tech.id, schedule=SCHEDULE)
    # Mon 28 Sep: filed under the subject
    store.create_recording("filed", "Lecture", tech.id, "en", "a.m4a")
    store.update_recording("filed", recorded_at="2026-09-28T05:02:00+00:00", duration_sec=3000)
    # Wed 30 Sep: at class time but in no subject
    store.create_recording("loose", "New Recording 24", None, "en", "b.m4a")
    store.update_recording("loose", recorded_at="2026-09-30T05:10:00+00:00", duration_sec=2800)
    files = [  # Mon 5 Oct only on the laptop; the 30 Sep file is already in the app
        {"path": "C:/x/New Recording 102.m4a", "name": "New Recording 102.m4a", "recorded_at": "2026-10-05T05:05:18+00:00",
         "duration_sec": 3168.0, "size": 1},
        {"path": "C:/x/dup.m4a", "name": "dup.m4a", "recorded_at": "2026-09-30T05:10:30+00:00", "duration_sec": 2801.0,
         "size": 1}]
    cal = build_calendar(store, TZ, date(2026, 9, 28), date(2026, 10, 8), files, today=date(2026, 10, 6))
    classes = {c["date"]: c for c in cal["classes"]}
    assert classes["2026-09-28"]["status"] == "recorded" and classes["2026-09-28"]["recording_ids"] == ["filed"]
    assert classes["2026-09-30"]["status"] == "recorded" and classes["2026-09-30"]["unfiled_ids"] == ["loose"]
    assert classes["2026-10-05"]["status"] == "laptop" and classes["2026-10-05"]["files"][0]["name"] == "New Recording 102.m4a"
    assert classes["2026-10-07"]["status"] == "upcoming"
    assert [f["name"] for f in cal["files"]] == ["New Recording 102.m4a"]  # dup is already in the app
    assert cal["days"]["2026-09-28"]["recordings"][0]["id"] == "filed"
    [summary] = cal["subjects"]
    assert (summary["recorded"], summary["laptop"], summary["missing"], summary["past"]) == (2, 1, 0, 3)


def test_missing_class(store):
    tech = store.create_subject("Machine Learning")
    store.update_subject(tech.id, schedule=SCHEDULE)
    cal = build_calendar(store, TZ, date(2026, 9, 28), date(2026, 9, 28), [], today=date(2026, 10, 1))
    assert cal["classes"][0]["status"] == "missing" and cal["subjects"][0]["missing_dates"] == ["2026-09-28"]


def test_migrates_v12_database(tmp_path):
    db = tmp_path / "app.db"
    s = Store(db)
    s.create_subject("Old")
    s._execute("PRAGMA user_version = 12")
    s._execute("ALTER TABLE subjects DROP COLUMN schedule_json")
    s._execute("ALTER TABLE recordings DROP COLUMN content_hash")
    undo_privacy_migration(s)
    s._execute("DROP TABLE reminders")
    s._execute("DROP TABLE notifications")
    s.close()
    s = Store(db)
    assert s.list_subjects()[0].schedule is None
    s.close()


# --- through the API

def test_calendar_api_and_adding_a_laptop_file(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    stamped(downloads, "New Recording 102.m4a", "2026-10-05T05:05:18Z")
    app = build(tmp_path, FakeTranscriber(), FakeLLM("groq:x", []), None)
    with TestClient(app) as client:
        subject = client.post("/api/subjects", json={"name": "Machine Learning"}).json()
        saved = client.patch(f"/api/subjects/{subject['id']}", json={"schedule": SCHEDULE}).json()
        assert saved["schedule"]["days"] == [0, 2]
        client.put("/api/settings", json={"scan_folders": str(downloads), "time_zone": TZ})
        cal = client.get("/api/calendar", params={"start": "2026-10-01", "end": "2026-10-31"}).json()
        laptop = next(c for c in cal["classes"] if c["date"] == "2026-10-05")
        assert laptop["status"] == "laptop"
        added = client.post("/api/calendar/import", json={"path": laptop["files"][0]["path"],
                                                          "subject_id": subject["id"]}).json()
        assert added["subject"] == "Machine Learning" and added["title"].startswith("New Recording 102")
        assert client.post("/api/calendar/import", json={"path": str(tmp_path / "elsewhere.m4a")}).status_code == 400
        assert client.put("/api/settings", json={"time_zone": "Mars/Base"}).status_code == 400


def test_open_or_show_a_laptop_file_only_inside_the_scanned_folders(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    stamped(downloads, "New Recording 103.m4a", "2026-10-07T05:05:18Z")
    launched = []
    monkeypatch.setattr("transcriber.app.subprocess", SimpleNamespace(Popen=lambda args, **kw: launched.append(args)))
    monkeypatch.setattr("transcriber.app.os.startfile", lambda path: launched.append(path), raising=False)
    app = build(tmp_path, FakeTranscriber(), FakeLLM("groq:x", []), None)
    with TestClient(app) as client:
        client.put("/api/settings", json={"scan_folders": str(downloads), "time_zone": TZ})
        cal = client.get("/api/calendar", params={"start": "2026-10-01", "end": "2026-10-31"}).json()
        path = next(f["path"] for day in cal["days"].values() for f in day["files"])
        assert client.post("/api/calendar/open", json={"path": path}).status_code == 204
        assert client.post("/api/calendar/open", json={"path": path, "reveal": True}).status_code == 204
        assert len(launched) == 2
        outside = str(tmp_path / "elsewhere.m4a")
        assert client.post("/api/calendar/open", json={"path": outside}).status_code == 400
        assert len(launched) == 2


def test_a_recording_that_starts_after_class_doesnt_count(store):
    tech = store.create_subject("Machine Learning")
    store.update_subject(tech.id, schedule=SCHEDULE)
    store.create_recording("after", "Advice", None, "en", "a.m4a")
    store.update_recording("after", recorded_at="2026-09-28T06:14:00+00:00", duration_sec=700)  # 11:14, class ended 11:00
    store.create_recording("early", "Lecture", None, "en", "b.m4a")
    store.update_recording("early", recorded_at="2026-09-30T04:45:00+00:00", duration_sec=4000)  # 09:45, runs into class
    cal = build_calendar(store, TZ, date(2026, 9, 28), date(2026, 9, 30), [], today=date(2026, 10, 6))
    assert [(c["date"], c["status"], c["unfiled_ids"]) for c in cal["classes"]] == [
        ("2026-09-28", "missing", []), ("2026-09-30", "recorded", ["early"])]
