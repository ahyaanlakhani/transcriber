"""When a recording was actually made (the phone's own date stamp), kept apart from when it was uploaded."""
import subprocess
import uuid

import pytest

from transcriber import audio
from transcriber.combine import combine_recordings
from transcriber.pipeline import AUDIO_FILE
from transcriber.providers.base import Segment
from transcriber.store import Store

from .helpers import FakeTranscriber, synth_audio, undo_privacy_migration
from .test_pipeline import make_pipeline


def stamped_m4a(tmp_path, stamp, name="memo.m4a"):
    """An m4a like the ones Voice Memos makes, with creation_time set to `stamp`."""
    wav = synth_audio(tmp_path / "memo.wav", [("tone", 2.0)])
    out = tmp_path / name
    args = ["-metadata", f"creation_time={stamp}"] if stamp else []
    subprocess.run([audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), *args,
                    "-c:a", "aac", str(out)], check=True)
    return out


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "app.db")
    yield type("Env", (), {"store": store, "dir": tmp_path / "recordings", "tmp": tmp_path})
    store.close()


# --- reading the stamp

def test_reads_the_phones_creation_time(tmp_path):
    assert audio.recorded_at(stamped_m4a(tmp_path, "2026-10-07T05:02:16.000000Z")) == "2026-10-07T05:02:16+00:00"


def test_no_stamp_means_unknown(tmp_path):
    assert audio.recorded_at(stamped_m4a(tmp_path, None)) is None
    assert audio.recorded_at(tmp_path / "missing.m4a") is None


@pytest.mark.parametrize("text, expected", [
    ("    creation_time   : 2026-10-07T05:02:16.000000Z\n", "2026-10-07T05:02:16+00:00"),
    ("    creation_time   : 2026-10-07 05:02:16\n", "2026-10-07T05:02:16+00:00"),
    ("    creation_time   : 1970-01-01T00:00:00.000000Z\n", None),  # an unset clock, not a date
    ("    creation_time   : 1904-01-01T00:00:00.000000Z\n", None),
    ("    creation_time   : nonsense\n", None),
    ("    encoder         : Lavf61\n", None),
])
def test_parse_creation_time(text, expected):
    assert audio.parse_recorded_at(text) == expected


# --- storing it

def test_store_keeps_recorded_at_and_sorts_by_it(env):
    env.store.create_recording("old", "Recorded 7 Oct, uploaded 9 Oct", None, "en", "a.m4a")
    env.store.create_recording("new", "Uploaded 8 Oct, no stamp", None, "en", "b.m4a")
    env.store._execute("UPDATE recordings SET created_at = '2026-10-09T09:00:00+00:00' WHERE id = 'old'")
    env.store._execute("UPDATE recordings SET created_at = '2026-10-08T09:00:00+00:00' WHERE id = 'new'")
    assert [r.id for r in env.store.list_recordings()] == ["old", "new"]  # by upload time, until stamped
    env.store.update_recording("old", recorded_at="2026-10-07T05:02:16+00:00")
    assert env.store.get_recording("old").recorded_at == "2026-10-07T05:02:16+00:00"
    assert env.store.get_recording("new").recorded_at is None
    assert [r.id for r in env.store.list_recordings()] == ["new", "old"]  # newest first by when it was recorded


def test_migrates_v8_database(tmp_path):
    db = tmp_path / "app.db"
    store = Store(db)
    store.create_recording("k1", "Kept", None, "en", "k1.m4a")
    store._execute("PRAGMA user_version = 8")
    store._execute("ALTER TABLE recordings DROP COLUMN content_hash")
    undo_privacy_migration(store)
    store._execute("DROP TABLE reminders")
    store._execute("ALTER TABLE recordings DROP COLUMN recorded_at")
    store._execute("ALTER TABLE recordings DROP COLUMN notes_style")
    store._execute("ALTER TABLE subjects DROP COLUMN notes_style")
    store._execute("DROP TABLE recaps")
    store._execute("ALTER TABLE recordings DROP COLUMN person_id")
    store._execute("DROP TABLE people")
    store._execute("ALTER TABLE subjects DROP COLUMN schedule_json")
    store._execute("DROP TABLE notifications")
    store.close()
    store = Store(db)
    assert store.get_recording("k1").recorded_at is None
    store.close()


def test_upload_takes_the_date_from_the_file(env):
    rid = uuid.uuid4().hex
    rec_dir = env.dir / rid
    rec_dir.mkdir(parents=True)
    stamped_m4a(env.tmp, "2026-10-07T05:02:16Z", name="upload.m4a").replace(rec_dir / "upload.m4a")
    env.store.create_recording(rid, "Machine Learning", None, "en", "New Recording 103.m4a")
    make_pipeline(env, FakeTranscriber()).process(rid)
    rec = env.store.get_recording(rid)
    assert rec.status == "done" and rec.recorded_at == "2026-10-07T05:02:16+00:00"


def test_combined_recording_keeps_the_earliest_date(env):
    for rid, stamp in (("p1", "2026-10-07T05:02:16+00:00"), ("p2", None)):
        (env.dir / rid).mkdir(parents=True)
        audio.normalize(synth_audio(env.tmp / f"{rid}.wav", [("tone", 2.0)]), env.dir / rid / AUDIO_FILE)
        env.store.create_recording(rid, rid, None, "en", f"{rid}.m4a")
        env.store.update_recording(rid, duration_sec=2.0, status="done", recorded_at=stamp)
        env.store.save_transcript(rid, rid, [Segment(0.0, 1.0, rid, -0.1, 0.01, 1.0)], "groq")
    rec = combine_recordings(env.store, env.dir, ["p1", "p2"])
    assert rec.recorded_at == "2026-10-07T05:02:16+00:00"


# --- automatic titles carry the recorded date, not the upload day

@pytest.mark.parametrize("title, expected", [
    ("New Recording 28 — 2026-10-10", "New Recording 28 — 2026-09-25"),  # the automatic title
    ("Machine Learning", "Machine Learning"),  # typed by the user: left alone
    ("New Recording 28 — 2026-10-10 laptop", "New Recording 28 — 2026-10-10 laptop"),  # edited: left alone
])
def test_dated_title(title, expected):
    from transcriber.pipeline import dated_title
    assert dated_title(title, "New Recording 28.m4a", "2026-09-25T03:54:01+00:00") == expected


def test_upload_with_automatic_title_gets_the_recorded_date(env):
    rid = uuid.uuid4().hex
    rec_dir = env.dir / rid
    rec_dir.mkdir(parents=True)
    stamped_m4a(env.tmp, "2026-09-25T09:54:01Z", name="upload.m4a").replace(rec_dir / "upload.m4a")
    env.store.create_recording(rid, "New Recording 28 — 2026-10-10", None, "en", "New Recording 28.m4a")
    make_pipeline(env, FakeTranscriber()).process(rid)
    assert env.store.get_recording(rid).title == "New Recording 28 — 2026-09-25"
