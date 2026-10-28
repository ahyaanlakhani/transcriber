import uuid

import pytest

from transcriber import audio
from transcriber.pipeline import AUDIO_FILE, WORK_DIR, Pipeline
from transcriber.providers.base import PermanentError, RateLimited, Segment, TransientError
from transcriber.store import Store

from .helpers import FakeClock, FakeTranscriber, synth_audio

# 25 s: silences at ~8-9.5 s and ~17.5-19 s. With target 10 / window 3 / max tail 12
# the plan is 3 chunks, cut inside each silence.
THREE_CHUNKS = [("tone", 8.0), ("silence", 1.5), ("tone", 8.0), ("silence", 1.5), ("tone", 6.0)]


class Crash(BaseException):
    """Simulates the process dying (not caught by the pipeline)."""


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "app.db")
    yield type("Env", (), {"store": store, "dir": tmp_path / "recordings", "tmp": tmp_path})
    store.close()


def add_recording(env, parts=THREE_CHUNKS, language="en"):
    rid = uuid.uuid4().hex
    rec_dir = env.dir / rid
    rec_dir.mkdir(parents=True)
    synth_audio(rec_dir / "upload.wav", parts)
    env.store.create_recording(rid, "Test", None, language, "meeting.wav")
    return rid


def make_pipeline(env, transcriber, clock=None, **kwargs):
    clock = clock or FakeClock()
    return Pipeline(env.store, transcriber, env.dir, target_sec=10.0, window_sec=3.0, max_tail_sec=12.0,
                    sleep=clock.sleep, clock=clock.time, **kwargs)


def record_statuses(store):
    seen = []
    original = store.update_recording

    def spy(rid, **fields):
        if "status" in fields:
            seen.append((fields["status"], fields.get("status_detail")))
        original(rid, **fields)

    store.update_recording = spy
    return seen


def test_happy_path(env):
    rid = add_recording(env)
    fake = FakeTranscriber()
    make_pipeline(env, fake).process(rid)

    rec = env.store.get_recording(rid)
    assert (rec.status, rec.progress_done, rec.progress_total) == ("done", 3, 3)
    assert rec.duration_sec == pytest.approx(25.0, abs=0.1)
    transcript = env.store.get_transcript(rid)
    assert transcript.text == "Chunk 1.\n\nChunk 2.\n\nChunk 3."
    assert transcript.model == "fake/model"
    starts = [s.start for s in transcript.segments]
    assert starts[0] == 0.0
    assert 8.0 < starts[1] < 9.5      # first cut is inside the first silence
    assert 17.5 < starts[2] < 19.0    # second cut is inside the second silence
    assert [c.language for c in fake.calls] == ["en", "en", "en"]
    rec_dir = env.dir / rid
    assert (rec_dir / AUDIO_FILE).exists()
    assert not (rec_dir / WORK_DIR).exists()
    assert not list(rec_dir.glob("upload.*"))
    assert env.store.get_notes(rid).status == "queued"


def test_prompts_carry_vocabulary_and_previous_text(env):
    env.store.update_settings({"vocabulary": "Zorvath"})
    rid = add_recording(env)
    fake = FakeTranscriber()
    make_pipeline(env, fake).process(rid)
    assert [c.prompt for c in fake.calls] == [
        "Glossary: Zorvath.", "Glossary: Zorvath. Chunk 1.", "Glossary: Zorvath. Chunk 2."]


def test_auto_language_sends_none(env):
    rid = add_recording(env, parts=[("tone", 3.0)], language="auto")
    fake = FakeTranscriber()
    make_pipeline(env, fake).process(rid)
    assert fake.calls[0].language is None


def test_hallucinations_are_filtered(env):
    rid = add_recording(env, parts=[("tone", 3.0)])
    fake = FakeTranscriber(behaviors=[[
        Segment(0.0, 1.0, "Real words.", -0.2, 0.01, 1.0),
        Segment(1.0, 2.0, "Thanks for watching!", -1.5, 0.9, 1.0),
    ]])
    make_pipeline(env, fake).process(rid)
    assert env.store.get_transcript(rid).text == "Real words."


def test_rate_limit_waits_then_resumes(env):
    rid = add_recording(env, parts=[("tone", 3.0)])
    clock = FakeClock()
    statuses = record_statuses(env.store)
    make_pipeline(env, FakeTranscriber(behaviors=[RateLimited(120.0)]), clock=clock).process(rid)
    assert env.store.get_recording(rid).status == "done"
    assert sum(clock.sleeps) == 120.0 and max(clock.sleeps) <= 5.0
    waiting = [detail for status, detail in statuses if status == "waiting"]
    assert len(waiting) == 1 and waiting[0].startswith("Rate limit reached — resuming at ")


def test_transient_errors_retry_then_succeed(env):
    rid = add_recording(env, parts=[("tone", 3.0)])
    clock = FakeClock()
    fake = FakeTranscriber(behaviors=[TransientError("503"), TransientError("503")])
    make_pipeline(env, fake, clock=clock).process(rid)
    assert env.store.get_recording(rid).status == "done"
    assert len(fake.calls) == 3
    assert sum(clock.sleeps) == 10.0  # waits of 2 s and 8 s


def test_transient_errors_exhaust_retries(env):
    rid = add_recording(env, parts=[("tone", 3.0)])
    clock = FakeClock()
    fake = FakeTranscriber(behaviors=[TransientError("Groq server error: 503")] * 4)
    make_pipeline(env, fake, clock=clock).process(rid)
    rec = env.store.get_recording(rid)
    assert (rec.status, rec.status_detail) == ("failed", "Groq server error: 503")
    assert len(fake.calls) == 4
    assert sum(clock.sleeps) == 40.0  # 2 + 8 + 30


def test_permanent_error_fails_immediately(env):
    rid = add_recording(env, parts=[("tone", 3.0)])
    clock = FakeClock()
    fake = FakeTranscriber(behaviors=[PermanentError("Invalid Groq API key")])
    make_pipeline(env, fake, clock=clock).process(rid)
    rec = env.store.get_recording(rid)
    assert (rec.status, rec.status_detail) == ("failed", "Invalid Groq API key")
    assert len(fake.calls) == 1 and clock.sleeps == []


def test_bad_file_fails_and_keeps_upload_for_retry(env):
    rid = uuid.uuid4().hex
    rec_dir = env.dir / rid
    rec_dir.mkdir(parents=True)
    (rec_dir / "upload.txt").write_text("not audio", encoding="utf-8")
    env.store.create_recording(rid, "Bad", None, "en", "notes.txt")
    make_pipeline(env, FakeTranscriber()).process(rid)
    rec = env.store.get_recording(rid)
    assert (rec.status, rec.status_detail) == ("failed", "Unsupported or corrupt file")
    assert (rec_dir / "upload.txt").exists()


def test_unexpected_error_is_reported(env):
    rid = add_recording(env, parts=[("tone", 3.0)])
    make_pipeline(env, FakeTranscriber(behaviors=[ValueError("weird")])).process(rid)
    rec = env.store.get_recording(rid)
    assert (rec.status, rec.status_detail) == ("failed", "Unexpected error (ValueError); details are in the app log")
    assert "weird" not in rec.status_detail


def test_crash_resumes_without_repeating_finished_chunks(env):
    rid = add_recording(env)
    first = FakeTranscriber(behaviors=[None, None, Crash()])
    with pytest.raises(Crash):
        make_pipeline(env, first).process(rid)
    work = env.dir / rid / WORK_DIR
    assert (work / "chunk_000.json").exists() and (work / "chunk_001.json").exists()
    assert env.store.get_recording(rid).status == "transcribing"

    env.store.reset_interrupted()
    second = FakeTranscriber()
    make_pipeline(env, second).process(rid)
    assert len(second.calls) == 1                       # only chunk 3 was sent again
    assert second.calls[0].prompt == "Chunk 2."         # continuity from the cached chunk
    # The second fake numbers its own calls from 1, so chunk 3 reads "Chunk 1." —
    # proof that chunks 1-2 came from the cache.
    assert env.store.get_transcript(rid).text == "Chunk 1.\n\nChunk 2.\n\nChunk 1."


def test_deleted_mid_job_abandons_and_cleans_up(env):
    rid = add_recording(env)
    fake = FakeTranscriber(on_call=lambda n: env.store.delete_recording(rid))
    make_pipeline(env, fake).process(rid)
    assert len(fake.calls) == 1
    assert not (env.dir / rid).exists()


def test_deleted_during_rate_limit_wait(env):
    rid = add_recording(env, parts=[("tone", 3.0)])
    clock = FakeClock(on_sleep=lambda n: env.store.delete_recording(rid))
    make_pipeline(env, FakeTranscriber(behaviors=[RateLimited(600.0)]), clock=clock).process(rid)
    assert clock.sleeps == [5.0]
    assert not (env.dir / rid).exists()


def test_oversized_chunk_is_split_in_half(env):
    rid = add_recording(env, parts=[("tone", 6.0)])
    probe = env.tmp / "probe.flac"
    audio.normalize(env.dir / rid / "upload.wav", probe)
    fake = FakeTranscriber()
    make_pipeline(env, fake, max_upload_bytes=int(probe.stat().st_size * 0.7)).process(rid)
    assert len(fake.calls) == 2
    starts = [s.start for s in env.store.get_transcript(rid).segments]
    assert starts[0] == 0.0
    assert starts[1] == pytest.approx(3.0, abs=0.05)
