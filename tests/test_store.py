from .helpers import undo_privacy_migration
import sqlite3

import pytest

from transcriber.notes.model import NoteItem, Notes
from transcriber.providers.base import Segment
from transcriber.store import SCHEMA_V1, DuplicateName, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


def add(store, rid, title="Standup", subject=None, language="en"):
    subject_id = store.get_or_create_subject(subject) if subject else None
    return store.create_recording(rid, title, subject_id, language, f"{rid}.m4a")


def ids(recordings):
    return [r.id for r in recordings]


# --- recordings (v1 behaviour)

def test_create_and_get(store):
    rec = add(store, "a1", subject="Team")
    assert (rec.id, rec.title, rec.subject, rec.language, rec.status) == ("a1", "Standup", "Team", "en", "queued")
    assert (rec.progress_done, rec.progress_total, rec.duration_sec, rec.notes_status) == (0, 0, None, None)
    assert store.get_recording("a1") == rec


def test_get_missing_returns_none(store):
    assert store.get_recording("nope") is None


def test_list_newest_first(store):
    add(store, "a1"), add(store, "a2"), add(store, "a3")
    assert ids(store.list_recordings()) == ["a3", "a2", "a1"]


def test_search_title_transcript_notes_and_my_notes(store):
    for rid in ("a1", "a2", "a3", "a4", "a5"):
        add(store, rid, title="Client Review" if rid == "a1" else "Standup")
    store.save_transcript("a2", "We discussed the Zorvath launch.", [], "fake")
    store.queue_notes("a3")
    store.save_notes("a3", Notes(decisions=[NoteItem("D1", "Hire a Quillbrook designer")]),
                     writer="w", checker=None, check_detail=None)
    store.set_my_notes("a4", "remember the pellucid budget")
    assert ids(store.list_recordings(q="client")) == ["a1"]
    assert ids(store.list_recordings(q="zorvath")) == ["a2"]
    assert ids(store.list_recordings(q="quillbrook")) == ["a3"]
    assert ids(store.list_recordings(q="pellucid")) == ["a4"]


def test_search_escapes_like_wildcards(store):
    add(store, "a1", title="100% done"), add(store, "a2", title="Other")
    assert ids(store.list_recordings(q="%")) == ["a1"]


def test_update_recording(store):
    add(store, "a1")
    store.update_recording("a1", status="transcribing", progress_done=1, progress_total=3, duration_sec=61.5)
    rec = store.get_recording("a1")
    assert (rec.status, rec.progress_done, rec.progress_total, rec.duration_sec) == ("transcribing", 1, 3, 61.5)


def test_update_rejects_unknown_fields(store):
    add(store, "a1")
    with pytest.raises(ValueError):
        store.update_recording("a1", id="hacked")
    with pytest.raises(ValueError):
        store.update_recording("a1", subject="names are not stored on recordings")


def test_delete_cascades(store):
    add(store, "a1")
    store.save_transcript("a1", "text", [], "fake")
    store.queue_notes("a1")
    store.add_topic("a1", "planning")
    assert store.delete_recording("a1") is True
    assert store.get_transcript("a1") is None and store.get_notes("a1") is None
    assert store.list_topics() == []
    assert store.delete_recording("a1") is False


def test_next_queued_is_oldest_queued(store):
    add(store, "a1"), add(store, "a2"), add(store, "a3")
    store.update_recording("a1", status="done")
    assert store.next_queued().id == "a2"


def test_reset_interrupted_resets_recordings_and_notes(store):
    for rid, status in [("a1", "converting"), ("a2", "transcribing"), ("a3", "waiting"),
                        ("a4", "done"), ("a5", "failed"), ("a6", "queued")]:
        add(store, rid)
        store.update_recording(rid, status=status, status_detail="x")
    for rid, status in [("a4", "writing"), ("a5", "checking"), ("a6", "ready")]:
        store.queue_notes(rid)
        store.update_notes_status(rid, status, "busy")
    assert store.reset_interrupted() == 3
    assert [store.get_recording(r).status for r in ("a1", "a2", "a3", "a4", "a5", "a6")] == [
        "queued", "queued", "queued", "done", "failed", "queued"]
    assert [store.get_notes(r).status for r in ("a4", "a5", "a6")] == ["queued", "queued", "ready"]


def test_save_transcript_marks_done_and_round_trips(store):
    add(store, "a1")
    segments = [Segment(0.0, 1.5, "Hi.", -0.1, 0.02, 1.0)]
    store.save_transcript("a1", "Hi.", segments, "groq/whisper-large-v3")
    transcript = store.get_transcript("a1")
    assert (transcript.text, transcript.segments, transcript.model) == ("Hi.", segments, "groq/whisper-large-v3")
    assert store.get_recording("a1").status == "done"


def test_settings_defaults_and_update(store):
    defaults = {"vocabulary": "", "default_language": "en", "prefer_server": "0",
                "groq_hour_limit_sec": "7200", "groq_day_limit_sec": "28800",
                "recaps_on": "1", "recap_voice": "af_heart", "recap_speed": "1.1",
                "time_zone": "UTC", "scan_folders": "", "class_reminders": "1", "vault_folder": "",
                "default_privacy": "standard"}
    assert store.get_settings() == defaults
    store.update_settings({"vocabulary": "Zorvath", "default_language": "ur"})
    assert store.get_settings() == {**defaults, "vocabulary": "Zorvath", "default_language": "ur"}
    with pytest.raises(ValueError):
        store.update_settings({"bogus": "1"})


# --- migration

def test_migrates_v1_database(tmp_path):
    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_V1)
    rows = [("a1", "Team "), ("a2", "team"), ("a3", None), ("a4", "Client")]
    for rid, subject in rows:
        conn.execute("INSERT INTO recordings (id, title, subject, language, original_filename, status, created_at,"
                     " updated_at) VALUES (?, 'T', ?, 'en', 'x.m4a', 'done', '2026-10-03T00:00:00+00:00',"
                     " '2026-10-03T00:00:00+00:00')", (rid, subject))
    conn.execute("INSERT INTO transcripts VALUES ('a4', 'hello', '[]', 'groq', '2026-10-03T00:00:00+00:00')")
    conn.commit()
    conn.close()

    store = Store(db)
    assert [(s.name, s.count) for s in store.list_subjects()] == [("Client", 1), ("Team", 2)]
    assert [store.get_recording(r).subject for r in ("a1", "a2", "a3", "a4")] == ["Team", "Team", None, "Client"]
    assert store.get_my_notes("a1") == ""
    assert store.get_notes("a4").status == "queued" and store.get_notes("a1") is None
    store.close()
    store = Store(db)  # opening again is a no-op
    assert len(store.list_subjects()) == 2
    store.close()


# --- subjects

def test_subjects_crud_and_counts(store):
    team = store.create_subject("  Team   Sync ", "weekly")
    assert (team.name, team.description, team.count) == ("Team Sync", "weekly", 0)
    with pytest.raises(DuplicateName):
        store.create_subject("team sync")
    assert store.get_or_create_subject("TEAM SYNC") == team.id
    add(store, "a1", subject="Team Sync")
    client = store.create_subject("Client")
    assert [(s.name, s.count) for s in store.list_subjects()] == [("Client", 0), ("Team Sync", 1)]
    assert store.update_subject(team.id, name="Team", description="all hands") is True
    with pytest.raises(DuplicateName):
        store.update_subject(team.id, name="client")
    assert store.get_subject(team.id).description == "all hands"
    assert store.update_subject(999, name="x") is False
    assert store.delete_subject(team.id) is True
    assert store.get_recording("a1").subject_id is None
    assert [s.name for s in store.list_subjects()] == [client.name]


def test_filter_and_move_by_subject(store):
    add(store, "a1", subject="Team"), add(store, "a2", subject="Client"), add(store, "a3")
    client_id = store.get_or_create_subject("Client")
    assert ids(store.list_recordings(subject_id=client_id)) == ["a2"]
    assert store.move_recordings(["a1", "a3", "missing"], client_id) == 2
    assert ids(store.list_recordings(subject_id=client_id)) == ["a3", "a2", "a1"]
    store.move_recordings(["a1"], None)
    assert store.get_recording("a1").subject is None


# --- topics

def test_topics_accept_suggest_and_counts(store):
    add(store, "a1"), add(store, "a2")
    planning = store.add_topic("a1", "Planning")
    store.suggest_topics("a1", ["planning", "hiring", " "])
    store.suggest_topics("a2", ["hiring"])
    assert store.recording_topics("a1") == [
        {"id": planning, "name": "Planning", "state": "accepted"},
        {"id": store.get_or_create_topic("hiring"), "name": "hiring", "state": "suggested"},
    ]
    assert [(t.name, t.count) for t in store.list_topics()] == [("Planning", 1)]  # suggestions don't count
    assert store.list_topic_names() == ["Planning"]
    hiring = store.get_or_create_topic("hiring")
    assert store.accept_topic("a2", hiring) is True
    assert [(t.name, t.count) for t in store.list_topics()] == [("hiring", 1), ("Planning", 1)]
    assert ids(store.list_recordings(topic_id=hiring)) == ["a2"]
    assert store.remove_topic("a1", hiring) is True
    assert [t["name"] for t in store.recording_topics("a1")] == ["Planning"]


def test_rename_topic_merges_on_clash(store):
    add(store, "a1"), add(store, "a2")
    old = store.add_topic("a1", "recruiting")
    store.add_topic("a2", "recruiting", "suggested")
    hiring = store.add_topic("a2", "hiring")
    assert store.rename_topic(old, "Hiring") == hiring
    assert [(t.name, t.count) for t in store.list_topics()] == [("hiring", 2)]
    assert store.recording_topics("a2") == [{"id": hiring, "name": "hiring", "state": "accepted"}]
    assert store.rename_topic(hiring, "people") == hiring
    assert store.list_topic_names() == ["people"]
    assert store.rename_topic(999, "x") is None
    assert store.delete_topic(hiring) is True
    assert store.recording_topics("a1") == []


# --- my notes and AI notes

def test_my_notes(store):
    add(store, "a1")
    assert store.set_my_notes("a1", "call Sara back") is True
    assert store.get_my_notes("a1") == "call Sara back"
    assert store.set_my_notes("missing", "x") is False
    assert store.get_my_notes("missing") is None


def test_notes_queue_lifecycle(store):
    add(store, "a1"), add(store, "a2")
    store.queue_notes("a1")
    assert store.next_notes_queued() is None  # waits for a transcript
    store.save_transcript("a1", "t", [], "fake")
    assert store.next_notes_queued() == "a1"
    assert store.get_recording("a1").notes_status == "queued"
    store.update_notes_status("a1", "writing", "Rate limit reached")
    record = store.get_notes("a1")
    assert (record.status, record.status_detail, record.notes) == ("writing", "Rate limit reached", None)
    notes = Notes(summary=[NoteItem("S1", "Gist", [3.0], verdict="supported", reason="ok")], topics=["x"])
    store.save_notes("a1", notes, writer="cerebras:gpt-oss-120b", checker="gemini:flash", check_detail=None)
    record = store.get_notes("a1")
    assert (record.status, record.notes, record.writer, record.checker) == (
        "ready", notes, "cerebras:gpt-oss-120b", "gemini:flash")
    assert store.next_notes_queued() is None
    store.queue_notes("a1")  # regenerate keeps the old notes until the new ones are saved
    assert store.get_notes("a1").notes == notes
    store.delete_notes("a1")
    assert store.get_notes("a1") is None


# --- v3: chat and hand-edited notes

def test_upsert_notes_creates_or_replaces_ready_notes(store):
    add(store, "a1")
    notes = Notes(summary=[NoteItem("S1", "Mine", [], edited=True)])
    store.upsert_notes("a1", notes, writer="you", checker=None, check_detail=None)
    record = store.get_notes("a1")
    assert (record.status, record.notes, record.writer) == ("ready", notes, "you")
    store.update_notes_status("a1", "failed", "x")
    store.upsert_notes("a1", Notes(), writer="w", checker="c", check_detail="d")
    record = store.get_notes("a1")
    assert (record.status, record.status_detail, record.checker, record.check_detail) == ("ready", None, "c", "d")


def test_chat_messages(store):
    add(store, "a1"), add(store, "a2")
    first = store.add_chat_message("a1", "user", "What did Sara say?")
    second = store.add_chat_message("a1", "assistant", "She sends the report [00:10].", engine="claude-code",
                                    proposal={"summary": []})
    store.add_chat_message("a2", "user", "other recording")
    chat = store.list_chat("a1")
    assert [(m["id"], m["role"], m["content"]) for m in chat] == [
        (first["id"], "user", "What did Sara say?"), (second["id"], "assistant", "She sends the report [00:10].")]
    assert (chat[1]["engine"], chat[1]["proposal"], chat[1]["proposal_state"]) == ("claude-code", {"summary": []},
                                                                                 "pending")
    assert chat[0]["proposal"] is None and chat[0]["proposal_state"] is None
    assert store.get_chat_message("a2", second["id"]) is None
    store.set_proposal_state(second["id"], "applied")
    assert store.get_chat_message("a1", second["id"])["proposal_state"] == "applied"
    store.clear_chat("a1")
    assert store.list_chat("a1") == [] and len(store.list_chat("a2")) == 1
    store.delete_recording("a2")
    assert store.list_chat("a2") == []


# --- v4: notes instructions and combined sessions

def test_notes_instructions(store):
    add(store, "a1", subject="Machine Learning")
    subject_id = store.get_recording("a1").subject_id
    assert store.update_subject(subject_id, instructions="  Main speaker is the instructor.  ") is True
    assert store.get_subject(subject_id).instructions == "Main speaker is the instructor."
    assert store.set_recording_instructions("a1", "Focus on the assignment.") is True
    assert store.notes_instructions("a1") == ("Main speaker is the instructor.", "Focus on the assignment.")
    add(store, "a2")
    assert store.notes_instructions("a2") == ("", "")
    assert store.notes_instructions("missing") == ("", "")


def test_part_starts(store):
    add(store, "a1")
    assert store.get_part_starts("a1") is None
    store.set_part_starts("a1", [0.0, 301.5, 610.0])
    assert store.get_part_starts("a1") == [0.0, 301.5, 610.0]


# --- v5: speakers and usage

def test_want_speakers_and_speaker_names(store):
    rec = store.create_recording("s1", "Lecture", None, "en", "x.m4a", want_speakers=True)
    assert rec.want_speakers is True
    assert add(store, "s2").want_speakers is False
    store.set_want_speakers("s2", True)
    assert store.get_recording("s2").want_speakers is True
    assert store.get_speaker_names("s1") == {}
    store.set_speaker_names("s1", {"SPEAKER_00": "Dr. Khan"})
    assert store.get_speaker_names("s1") == {"SPEAKER_00": "Dr. Khan"}


def test_usage_sums_inside_window(store):
    store.record_usage(600.0, "groq", at="2026-10-05T08:00:00+00:00")
    store.record_usage(300.0, "groq", at="2026-10-05T09:30:00+00:00")
    store.record_usage(999.0, "server", at="2026-10-05T09:30:00+00:00")
    assert store.usage_seconds("groq", since="2026-10-05T09:00:00+00:00") == 300.0
    assert store.usage_seconds("groq", since="2026-10-05T00:00:00+00:00") == 900.0
    assert store.usage_seconds("groq", since="2026-10-06T00:00:00+00:00") == 0.0


def test_old_segments_without_speaker_load(store):
    add(store, "o1")
    store._execute("INSERT INTO transcripts VALUES ('o1', 'hi', ?, 'groq', '2026-10-03T00:00:00+00:00')",
                   ('[{"start": 0, "end": 1, "text": "hi", "avg_logprob": null, "no_speech_prob": null,'
                    ' "compression_ratio": null}]',))
    assert store.get_transcript("o1").segments[0].speaker is None


def test_migrates_v4_database_keeping_recordings(tmp_path):
    db = tmp_path / "app.db"
    store = Store(db)
    add(store, "k1")
    store._execute("PRAGMA user_version = 4")
    store._execute("ALTER TABLE recordings DROP COLUMN content_hash")
    undo_privacy_migration(store)
    store._execute("DROP TABLE reminders")
    store._execute("ALTER TABLE recordings DROP COLUMN want_speakers")
    store._execute("ALTER TABLE recordings DROP COLUMN speakers")
    store._execute("ALTER TABLE recordings DROP COLUMN starred")
    store._execute("ALTER TABLE recordings DROP COLUMN merge_sources")
    store._execute("ALTER TABLE recordings DROP COLUMN merged_from")
    store._execute("ALTER TABLE recordings DROP COLUMN deleted_at")
    store._execute("ALTER TABLE recordings DROP COLUMN recorded_at")
    store._execute("ALTER TABLE recordings DROP COLUMN notes_style")
    store._execute("ALTER TABLE subjects DROP COLUMN notes_style")
    store._execute("DROP TABLE recaps")
    store._execute("ALTER TABLE recordings DROP COLUMN person_id")
    store._execute("DROP TABLE people")
    store._execute("ALTER TABLE subjects DROP COLUMN schedule_json")
    store._execute("DROP TABLE notifications")
    store._execute("DROP TABLE usage")
    store.close()
    store = Store(db)
    assert store.get_recording("k1").want_speakers is False and store.get_recording("k1").starred is False
    store.record_usage(10.0, "groq")
    store.close()


# --- v6: starred

def test_star_and_filter(store):
    add(store, "x1")
    add(store, "x2", title="Lecture")
    assert store.get_recording("x1").starred is False
    store.set_starred("x2", True)
    assert ids(store.list_recordings(starred=True)) == ["x2"]
    assert ids(store.list_recordings(q="Lect", starred=True)) == ["x2"]
    assert store.recording_counts() == {"all": 2, "starred": 1, "no_subject": 2, "deleted": 0}
    store.set_starred("x2", False)
    assert store.list_recordings(starred=True) == []


# --- v8: Recently Deleted, and recordings without a subject

def test_soft_delete_hides_everywhere_and_restore_brings_back(store):
    add(store, "k1", subject="Team")
    add(store, "k2", subject="Team")
    add(store, "k3")
    store.queue_notes("k1")
    store.soft_delete("k1")
    assert ids(store.list_recordings()) == ["k3", "k2"]
    assert ids(store.list_recordings(deleted=True)) == ["k1"]
    assert store.get_recording("k1").deleted_at is not None
    assert [s.count for s in store.list_subjects()] == [1]
    assert store.recording_counts() == {"all": 2, "starred": 0, "no_subject": 1, "deleted": 1}
    assert store.next_notes_queued() is None  # no notes for things in the bin
    store.restore("k1")
    assert ids(store.list_recordings()) == ["k3", "k2", "k1"] and store.get_recording("k1").deleted_at is None


def test_queued_recordings_in_the_bin_are_not_transcribed(store):
    add(store, "q1")
    store.soft_delete("q1")
    assert store.next_queued() is None


def test_no_subject_filter(store):
    add(store, "n1", subject="Team")
    add(store, "n2")
    assert ids(store.list_recordings(no_subject=True)) == ["n2"]


def test_purge_returns_only_old_deletions(store):
    add(store, "p1")
    add(store, "p2")
    store.soft_delete("p1", at="2026-01-01T00:00:00+00:00")
    store.soft_delete("p2")
    assert store.purge_deleted(before="2026-06-01T00:00:00+00:00") == ["p1"]
    assert store.get_recording("p1") is None and store.get_recording("p2").deleted_at is not None
