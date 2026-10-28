"""Live recording: pieces of browser audio in, text out as Groq transcribes pause-cut windows."""
import math
import struct
import subprocess

import pytest

from transcriber import audio
from transcriber.live import FRAMES_PER_SEC, LiveManager, choose_cut, frame_rms
from transcriber.providers.base import PermanentError, RateLimited, Segment
from transcriber.store import Store

from .helpers import FakeClock, FakeTranscriber, synth_audio


def pcm(parts):
    """16 kHz mono s16le: ("tone", seconds) is loud, ("silence", seconds) is quiet."""
    out = bytearray()
    for kind, seconds in parts:
        for i in range(int(seconds * 16000)):
            value = int(8000 * math.sin(2 * math.pi * 440 * i / 16000)) if kind == "tone" else 0
            out += struct.pack("<h", value)
    return bytes(out)


# --- cutting

def test_frame_rms_measures_loudness():
    rms = frame_rms(pcm([("tone", 0.1), ("silence", 0.1)]))
    assert len(rms) == 10 and min(rms[:5]) > 4000 and max(rms[5:]) < 1


def test_waits_for_enough_audio():
    rms = frame_rms(pcm([("tone", 5.0)]))
    assert choose_cut(rms, min_sec=8, max_sec=15) is None


def test_cuts_in_the_middle_of_a_pause():
    rms = frame_rms(pcm([("tone", 9.0), ("silence", 0.6), ("tone", 2.0)]))
    cut = choose_cut(rms, min_sec=8, max_sec=15)
    assert 9.0 * FRAMES_PER_SEC < cut < 9.6 * FRAMES_PER_SEC


def test_waits_for_a_pause_until_the_maximum_then_cuts_there():
    assert choose_cut(frame_rms(pcm([("tone", 12.0)])), min_sec=8, max_sec=15) is None
    assert choose_cut(frame_rms(pcm([("tone", 16.0)])), min_sec=8, max_sec=15) == 15 * FRAMES_PER_SEC


def test_final_takes_everything():
    rms = frame_rms(pcm([("tone", 3.0)]))
    assert choose_cut(rms, min_sec=8, max_sec=15, final=True) == len(rms)


# --- sessions

@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "app.db")
    yield type("Env", (), {"store": store, "tmp": tmp_path})
    store.close()


def webm_bytes(tmp_path, parts):
    wav = synth_audio(tmp_path / "speech.wav", parts)
    out = tmp_path / "speech.webm"
    subprocess.run([audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libopus",
                    "-b:a", "32k", str(out)], check=True)
    return out.read_bytes()


def pieces(data, size=4000):
    return [data[i:i + size] for i in range(0, len(data), size)]


def make(env, transcriber, clock=None):
    clock = clock or FakeClock()
    return LiveManager(env.store, transcriber, env.tmp / "live", env.tmp / "recordings", sleep=clock.sleep,
                       clock=clock.time, start_thread=False)


SPEECH = [("tone", 9.0), ("silence", 0.8), ("tone", 9.0), ("silence", 0.8), ("tone", 3.0)]


def run_live(manager, sid, data):
    for seq, piece in enumerate(pieces(data)):
        manager.add_chunk(sid, seq, piece)
    manager.wait_for_audio(sid)
    while manager.step():
        pass


def test_live_text_then_finish_into_a_recording(env):
    fake = FakeTranscriber()
    manager = make(env, fake)
    subject = env.store.create_subject("Machine Learning")
    sid = manager.create(title="Lecture 5", subject_id=subject.id, language="en")
    run_live(manager, sid, webm_bytes(env.tmp, SPEECH))
    live = manager.poll(sid, after=0)
    assert live["status"] == "recording" and len(live["segments"]) == 2  # two full windows; the tail waits
    assert live["segments"][1]["start"] > 9.0
    assert manager.poll(sid, after=2)["segments"] == []

    rid = manager.finish(sid)
    rec = env.store.get_recording(rid)
    assert (rec.title, rec.subject, rec.status, rec.original_filename) == ("Lecture 5", "Machine Learning", "done",
                                                                          "Live recording")
    transcript = env.store.get_transcript(rid)
    assert transcript.model == "fake/model (live)" and len(transcript.segments) == 3
    assert 22.0 <= rec.duration_sec <= 23.2
    assert rec.recorded_at and rec.recorded_at.endswith("+00:00")  # when recording started, in UTC
    assert (env.tmp / "recordings" / rid / "audio.flac").exists()
    assert env.store.get_notes(rid).status == "queued"
    assert not (env.tmp / "live" / sid).exists()
    assert env.store.usage_seconds("fake", since="2000-01-01T00:00:00+00:00") > 20


def test_prompt_carries_previous_text_and_vocabulary(env):
    env.store.update_settings({"vocabulary": "Zorvath"})
    fake = FakeTranscriber()
    manager = make(env, fake)
    sid = manager.create(title="", subject_id=None, language="en")
    run_live(manager, sid, webm_bytes(env.tmp, SPEECH))
    assert "Zorvath" in fake.calls[0].prompt and "Chunk 1." in fake.calls[1].prompt


def test_pieces_are_assembled_in_order_and_duplicates_ignored(env):
    manager = make(env, FakeTranscriber())
    sid = manager.create(title="", subject_id=None, language="en")
    chunks = pieces(webm_bytes(env.tmp, [("tone", 4.0)]))
    order = list(range(len(chunks)))
    order[0], order[1] = order[1], order[0]
    for seq in order + [0, 1]:
        manager.add_chunk(sid, seq, chunks[seq])
    manager.wait_for_audio(sid)
    assert 3.8 <= manager.poll(sid, after=0)["duration_sec"] <= 4.2


def test_silent_windows_are_not_sent(env):
    fake = FakeTranscriber()
    manager = make(env, fake)
    sid = manager.create(title="", subject_id=None, language="en")
    run_live(manager, sid, webm_bytes(env.tmp, [("silence", 17.0)]))
    assert fake.calls == []


def test_rate_limit_shows_waiting_and_catches_up(env):
    clock = FakeClock()
    fake = FakeTranscriber([RateLimited(30.0)])
    manager = make(env, fake, clock)
    sid = manager.create(title="", subject_id=None, language="en")
    run_live(manager, sid, webm_bytes(env.tmp, SPEECH))
    assert clock.now >= 30.0 and len(manager.poll(sid, after=0)["segments"]) == 2


def test_without_groq_the_recording_is_queued_for_normal_transcription(env):
    manager = make(env, None)
    sid = manager.create(title="Seminar", subject_id=None, language="en")
    run_live(manager, sid, webm_bytes(env.tmp, [("tone", 3.0)]))
    assert "Groq" in manager.poll(sid, after=0)["notice"]
    rec = env.store.get_recording(manager.finish(sid))
    assert rec.status == "queued" and env.store.get_transcript(rec.id) is None


def test_groq_refusing_mid_session_falls_back_to_normal_transcription(env):
    manager = make(env, FakeTranscriber([PermanentError("Invalid Groq API key")]))
    sid = manager.create(title="", subject_id=None, language="en")
    run_live(manager, sid, webm_bytes(env.tmp, SPEECH))
    assert "Invalid Groq API key" in manager.poll(sid, after=0)["notice"]
    assert env.store.get_recording(manager.finish(sid)).status == "queued"


def test_discard_removes_everything(env):
    manager = make(env, FakeTranscriber())
    sid = manager.create(title="", subject_id=None, language="en")
    run_live(manager, sid, webm_bytes(env.tmp, [("tone", 2.0)]))
    manager.discard(sid)
    assert not (env.tmp / "live" / sid).exists() and env.store.list_recordings() == []


def test_recovers_sessions_left_by_a_crash(env):
    manager = make(env, FakeTranscriber())
    sid = manager.create(title="Interrupted lecture", subject_id=None, language="es")
    run_live(manager, sid, webm_bytes(env.tmp, [("tone", 3.0)]))
    manager.abandon_for_test(sid)  # the process "dies": decoder gone, nothing finished

    created = make(env, FakeTranscriber()).recover()
    [rec] = [env.store.get_recording(rid) for rid in created]
    assert (rec.title, rec.language, rec.status) == ("Interrupted lecture", "es", "queued")
    assert (env.tmp / "recordings" / rec.id / "audio.flac").exists()
    assert not (env.tmp / "live" / sid).exists()


def test_untitled_sessions_get_a_dated_title(env):
    manager = make(env, FakeTranscriber())
    sid = manager.create(title="  ", subject_id=None, language="en")
    run_live(manager, sid, webm_bytes(env.tmp, [("tone", 2.0)]))
    assert env.store.get_recording(manager.finish(sid)).title.startswith("Live recording — ")


def test_segments_are_offset_to_the_recording_time(env):
    fake = FakeTranscriber([[Segment(1.0, 2.0, "First.", -0.1, 0.01, 1.0)],
                            [Segment(0.5, 1.5, "Second.", -0.1, 0.01, 1.0)]])
    manager = make(env, fake)
    sid = manager.create(title="", subject_id=None, language="en")
    run_live(manager, sid, webm_bytes(env.tmp, SPEECH))
    first, second = manager.poll(sid, after=0)["segments"]
    assert first["start"] == pytest.approx(1.0, abs=0.05) and second["start"] > 9.5


# --- finishing never blocks on a backlog (e.g. recorded while offline)

class Offline:
    name = "groq/whisper-large-v3"

    def __init__(self):
        self.calls = []

    def transcribe(self, path, *, language, prompt):
        import wave
        with wave.open(str(path)) as w:
            self.calls.append(w.getnframes() / w.getframerate())
        from transcriber.providers.base import TransientError
        raise TransientError("offline")


def add_audio(manager, sid, data):
    for seq, piece in enumerate(pieces(data)):
        manager.add_chunk(sid, seq, piece)
    manager.wait_for_audio(sid)


def test_final_cut_never_exceeds_the_maximum():
    rms = frame_rms(pcm([("tone", 20.0)]))
    assert choose_cut(rms, min_sec=8, max_sec=15, final=True) == 15 * FRAMES_PER_SEC


def test_finish_with_a_backlog_saves_at_once_for_normal_transcription(env):
    clock = FakeClock()
    offline = Offline()
    manager = make(env, offline, clock)
    sid = manager.create(title="Offline lecture", subject_id=None, language="en")
    add_audio(manager, sid, webm_bytes(env.tmp, SPEECH))  # 22 s, nothing transcribed
    rec = env.store.get_recording(manager.finish(sid))
    assert (rec.title, rec.status) == ("Offline lecture", "queued")
    assert env.store.get_transcript(rec.id) is None
    assert offline.calls == [] and clock.now == 0  # no attempt, no waiting


def test_finish_after_groq_trouble_saves_at_once(env):
    clock = FakeClock()
    offline = Offline()
    manager = make(env, offline, clock)
    sid = manager.create(title="", subject_id=None, language="en")
    add_audio(manager, sid, webm_bytes(env.tmp, [("tone", 9.0), ("silence", 0.8), ("tone", 2.0)]))
    manager.step()  # the first window fails: Groq unreachable
    waited = clock.now
    rec = env.store.get_recording(manager.finish(sid))
    assert rec.status == "queued" and clock.now == waited


def test_finish_sends_at_most_one_short_tail_once(env):
    clock = FakeClock()
    fake = FakeTranscriber()
    manager = make(env, fake, clock)
    sid = manager.create(title="", subject_id=None, language="en")
    run_live(manager, sid, webm_bytes(env.tmp, SPEECH))
    calls_before = len(fake.calls)
    fake.behaviors = [__import__("transcriber.providers.base", fromlist=["TransientError"]).TransientError("blip")]
    rec = env.store.get_recording(manager.finish(sid))
    assert len(fake.calls) == calls_before + 1  # one try, no retry loop
    assert rec.status == "queued" and clock.now == 0
