import pytest

from transcriber import audio
from transcriber.combine import GAP_SEC, combine_recordings
from transcriber.pipeline import AUDIO_FILE
from transcriber.providers.base import Segment
from transcriber.store import Store

from .helpers import synth_audio


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "app.db")
    yield type("Env", (), {"store": store, "dir": tmp_path / "recordings", "tmp": tmp_path})
    store.close()


def add_done(env, rid, seconds, text, subject=None):
    rec_dir = env.dir / rid
    rec_dir.mkdir(parents=True)
    audio.normalize(synth_audio(env.tmp / f"{rid}.wav", [("tone", seconds)]), rec_dir / AUDIO_FILE)
    subject_id = env.store.get_or_create_subject(subject) if subject else None
    env.store.create_recording(rid, f"Class part {rid}", subject_id, "en", f"{rid}.m4a")
    env.store.update_recording(rid, duration_sec=seconds)
    env.store.save_transcript(rid, text, [Segment(0.0, 1.0, text, -0.1, 0.01, 1.0),
                                          Segment(seconds - 1.0, seconds, f"{text} end", -0.1, 0.01, 1.0)], "groq")


def test_concat_inserts_gaps(env):
    srcs = []
    for name, seconds in (("a", 2.0), ("b", 3.0), ("c", 1.0)):
        dst = env.tmp / f"{name}.flac"
        audio.normalize(synth_audio(env.tmp / f"{name}.wav", [("tone", seconds)]), dst)
        srcs.append(dst)
    out = env.tmp / "all.flac"
    audio.concat(srcs, out, gap_sec=1.0)
    assert audio.probe_duration(out) == pytest.approx(8.0, abs=0.1)


def test_combines_in_recording_order(env):
    add_done(env, "p1", 4.0, "Part one", subject="Machine Learning")
    add_done(env, "p2", 3.0, "Part two")
    add_done(env, "p3", 2.0, "Part three")
    rec = combine_recordings(env.store, env.dir, ["p3", "p1", "p2"])  # selection order does not matter
    assert (rec.title, rec.status, rec.subject, rec.language) == ("Class part p1 (combined)", "done", "Machine Learning", "en")
    starts = [0.0, 4.0 + GAP_SEC, 7.0 + 2 * GAP_SEC]
    assert env.store.get_part_starts(rec.id) == pytest.approx(starts)
    assert rec.duration_sec == pytest.approx(9.0 + 2 * GAP_SEC)
    assert audio.probe_duration(env.dir / rec.id / AUDIO_FILE) == pytest.approx(9.0 + 2 * GAP_SEC, abs=0.15)
    transcript = env.store.get_transcript(rec.id)
    assert [round(s.start, 2) for s in transcript.segments] == pytest.approx(
        [0.0, 3.0, starts[1], starts[1] + 2.0, starts[2], starts[2] + 1.0])
    assert transcript.text.startswith("Part one") and "Part three end" in transcript.text
    assert env.store.get_notes(rec.id).status == "queued"
    assert all(env.store.get_recording(r) is not None for r in ("p1", "p2", "p3"))  # originals kept


def test_custom_title(env):
    add_done(env, "p1", 2.0, "a")
    add_done(env, "p2", 2.0, "b")
    assert combine_recordings(env.store, env.dir, ["p1", "p2"], title="  Week 3 lecture ").title == "Week 3 lecture"


def test_needs_two_finished_recordings(env):
    add_done(env, "p1", 2.0, "a")
    with pytest.raises(ValueError, match="at least two"):
        combine_recordings(env.store, env.dir, ["p1"])
    env.store.create_recording("busy", "Busy", None, "en", "x.m4a")
    with pytest.raises(ValueError, match="finished"):
        combine_recordings(env.store, env.dir, ["p1", "busy"])
    with pytest.raises(ValueError, match="no longer exists"):
        combine_recordings(env.store, env.dir, ["p1", "nope"])
