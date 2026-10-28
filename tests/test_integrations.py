"""Files for other apps: the class timetable as a calendar file, and flashcards for Anki."""
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from transcriber import learn
from transcriber.integrations import anki_file, calendar_file
from transcriber.notes.model import notes_from_writer
from transcriber.store import Store

from .helpers import FakeTranscriber
from .test_app import build

TZ = "Asia/Tashkent"
NOW = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # Monday 09:00 at UTC+5
SCHEDULE = {"days": [0, 2], "start": "10:00", "minutes": 90, "from": "2026-09-14", "until": "2026-12-31",
            "skip": ["2026-10-21"], "extra": [{"date": "2026-10-24", "start": "14:00", "minutes": 60}]}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


def unfold(text: str) -> list[str]:
    lines = []
    for line in text.split("\r\n"):
        if line.startswith(" ") and lines:
            lines[-1] += line[1:]
        elif line:
            lines.append(line)
    return lines


def test_the_calendar_file_has_classes_in_utc_skips_cancelled_ones_and_adds_reminders(store):
    subject = store.create_subject("Machine Learning")
    store.update_subject(subject.id, schedule=SCHEDULE)
    store.add_reminder("2026-10-20T05:00:00+00:00", "Record Machine Learning; bring the laptop, charger")
    store.add_reminder("2026-10-01T05:00:00+00:00", "Already past")
    text = calendar_file(store, TZ, today=date(2026, 10, 19), now=NOW)
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    lines = unfold(text)
    starts = [line for line in lines if line.startswith("DTSTART:")]
    assert "DTSTART:20261019T050000Z" in starts  # Monday 10:00 at UTC+5 is 05:00 UTC
    assert "DTSTART:20261021T050000Z" not in starts  # the skipped Wednesday
    assert "DTSTART:20261024T090000Z" in starts  # the one-off Saturday class at 14:00 local
    assert r"SUMMARY:Record Machine Learning\; bring the laptop\, charger" in lines  # special characters escaped
    assert "SUMMARY:Already past" not in lines
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


def test_long_lines_are_folded_without_splitting_a_character(store):
    store.add_reminder("2026-10-20T05:00:00+00:00", "Réunion " * 30)
    text = calendar_file(store, TZ, today=date(2026, 10, 19), now=NOW)
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert ("SUMMARY:" + ("Réunion " * 30)) in unfold(text)


def test_the_anki_file_has_the_source_and_skips_unlisted_cards(store):
    store.create_recording("a", "Security week", store.get_or_create_subject("Machine Learning"), "en", "a.m4a")
    store.update_recording("a", recorded_at="2026-10-18T05:00:00+00:00", duration_sec=1800.0)
    store.queue_notes("a")
    store.save_notes("a", notes_from_writer({"lecture": [
        {"heading": "OAuth", "kind": "definition", "term": "OAuth", "text": "Delegated <access> & tokens.", "cites": ["[02:10]"]},
        {"heading": "OAuth", "kind": "stressed", "term": None, "text": "Tokens expire.", "cites": ["[04:00]"]}]}),
        writer="w", checker=None, check_detail=None)
    learn.sync_cards(store, TZ)
    text, count = anki_file(store, TZ)
    rows = text.splitlines()
    assert rows[:3] == ["#separator:tab", "#html:true", "#tags column:3"] and count == 2
    definition = next(r for r in rows[3:] if r.startswith("What is OAuth?"))
    front, back, tags = definition.split("\t")
    assert "Delegated &lt;access&gt; &amp; tokens." in back and "From Security week at 02:10" in back
    assert tags == "Transcriber definition Machine_Learning"
    card = next(c for c in store.due_cards(NOW, 10) if c["kind"] == "stressed")
    store.set_card_suspended(card["id"], True)
    assert anki_file(store, TZ)[1] == 1  # a card you retired isn't exported


def test_the_downloads(tmp_path):
    app = build(tmp_path, FakeTranscriber(), None, None)
    with TestClient(app) as client:
        ics = client.get("/api/export/classes.ics")
        assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
        assert "attachment" in ics.headers["content-disposition"] and ics.text.startswith("BEGIN:VCALENDAR")
        assert client.get("/api/export/flashcards-anki.txt").status_code == 404  # no cards yet: a clear answer, not an empty file
