"""Meetings recorded from Windows: your microphone and the call's sound kept apart, lines labelled You or Others."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from transcriber.app import create_app
from transcriber.channels import NAMES, OTHERS, YOU, label_recording, label_segments, label_speaker
from transcriber.config import Config
from transcriber.live import LiveManager
from transcriber.meeting import MeetingCapture, MeetingError, MeetingManager, Resampler, to_mono
from transcriber.providers.base import Segment
from transcriber.store import Store

from .helpers import FakeLLM, FakeTranscriber


def tone(seconds, rate=48000, channels=2, amplitude=8000, hz=440):
    """16-bit interleaved PCM bytes of a sine wave."""
    t = np.arange(int(seconds * rate)) / rate
    wave = (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.int16)
    return np.repeat(wave, channels).tobytes()


class FakeDevices:
    def __init__(self, fail=None):
        self.fail, self.on_you, self.on_others, self.stopped = fail, None, None, False

    def info(self):
        return {"available": True, "reason": None, "output": "Fake speakers", "input": "Fake microphone"}

    def open(self, on_you, on_others):
        if self.fail:
            raise MeetingError(self.fail)
        self.on_you, self.on_others = on_you, on_others
        return self.close

    def close(self):
        self.stopped = True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def play(devices, clock, capture, seconds, you=False, others=False):
    """Time passes 0.1 s at a time; whoever is speaking is heard on their track."""
    for _ in range(int(round(seconds * 10))):
        if you:
            devices.on_you(tone(0.1), 48000, 2)
        if others:
            devices.on_others(tone(0.1), 48000, 2)
        clock.now = round(clock.now + 0.1, 6)  # exact: float drift would stop a frame short
        capture.pump()


# --- 16 kHz mono from any device

def test_stereo_48k_becomes_16k_mono_with_the_pitch_intact_even_in_odd_sized_buffers():
    resampler = Resampler(48000)
    data = tone(1.0)
    out = np.concatenate([resampler.feed(to_mono(data[i:i + 3996], 2)) for i in range(0, len(data), 3996)])
    assert abs(len(out) - 16000) <= 3
    spectrum = np.abs(np.fft.rfft(out.astype(np.float32)))
    assert abs(np.argmax(spectrum) * 16000 / len(out) - 440) < 5


def test_44k_input_is_resampled_too():
    resampler = Resampler(44100)
    out = resampler.feed(to_mono(tone(1.0, rate=44100), 2))
    assert abs(len(out) - 16000) <= 3


# --- You and Others, by which source was loud

def test_a_line_is_you_when_your_microphone_carried_it_and_others_when_the_call_did():
    loud, quiet = [3000.0] * 100, [0.0] * 100
    assert label_speaker(loud, quiet, 0, 1) == YOU
    assert label_speaker(quiet, loud, 0, 1) == OTHERS
    assert label_speaker(quiet, quiet, 0, 1) == OTHERS                 # nothing heard: not you
    assert label_speaker([1000.0] * 100, [3000.0] * 100, 0, 1) == OTHERS  # the call leaking into your mic through speakers
    assert label_speaker([3000.0] * 100, [3000.0] * 100, 0, 1) == YOU     # you talking over the call
    segments = label_segments([Segment(0, 1, "a"), Segment(1, 2, "b")], [3000.0] * 50 + [0.0] * 50, [0.0] * 50 + [3000.0] * 50)
    assert [s.speaker for s in segments] == [YOU, OTHERS]


# --- the capture keeps both tracks in step with the clock

class Sink:
    def __init__(self):
        self.calls = []

    def add_pcm(self, sid, mixed, you, others):
        self.calls.append((mixed, you, others))


def test_a_silent_track_is_padded_with_silence_and_the_mix_adds_both():
    devices, clock, sink = FakeDevices(), Clock(), Sink()
    capture = MeetingCapture(sink, "s1", devices, clock)
    capture.start()
    capture._halt.set()  # drive the ticks by hand
    play(devices, clock, capture, 1.0, you=True)       # only you speak: the call's track gets no data at all
    mixed, you, others = (np.frombuffer(b"".join(c[i] for c in sink.calls), dtype=np.int16) for i in range(3))
    assert len(mixed) == len(you) == len(others) == 16000
    assert np.abs(others).max() == 0 and np.abs(you[2000:]).max() > 4000 and np.array_equal(mixed, you)
    assert capture.levels["you"] > 0.3 and capture.levels["others"] == 0.0
    clock.now += 0.5
    capture.pump()
    assert sum(len(c[0]) for c in sink.calls) // 2 == 16000 + 8000  # half a second later, half a second more


# --- a whole meeting through the live recorder

@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "app.db")
    yield type("Env", (), {"store": store, "tmp": tmp_path})
    store.close()


def make_manager(env, transcriber):
    live = LiveManager(env.store, transcriber, env.tmp / "live", env.tmp / "recordings", start_thread=False)
    devices, clock = FakeDevices(), Clock()
    return live, devices, clock, MeetingManager(live, devices, clock)


def test_a_meeting_is_transcribed_live_with_you_and_others_and_saved_with_both_tracks(env):
    transcriber = FakeTranscriber([[Segment(0.0, 9.0, "I speak first.")], [Segment(0.4, 9.4, "They answer.")]])
    live, devices, clock, manager = make_manager(env, transcriber)
    sid = manager.start(title="Team call", subject_id=None, language="en")
    capture = manager.captures[sid]
    capture._halt.set()
    play(devices, clock, capture, 9.0, you=True)
    play(devices, clock, capture, 0.8)
    play(devices, clock, capture, 9.0, others=True)
    play(devices, clock, capture, 1.0)
    while live.step():
        pass
    shown = manager.poll(sid)
    assert shown["segments"][0]["speaker"] == "You" and shown["segments"][0]["text"] == "I speak first."
    assert shown["levels"] == {"you": 0.0, "others": 0.0}

    rid = manager.finish(sid)
    rec = env.store.get_recording(rid)
    assert (rec.title, rec.original_filename) == ("Team call", "Meeting recording")
    assert env.store.notes_style(rid) == "meeting" and env.store.get_speaker_names(rid) == NAMES
    transcript = env.store.get_transcript(rid)
    assert [s.speaker for s in transcript.segments] == [YOU, OTHERS]
    folder = env.tmp / "recordings" / rid
    assert (folder / "you.flac").exists() and (folder / "others.flac").exists() and (folder / "audio.flac").exists()
    assert devices.stopped is True and manager.captures == {}
    # the two saved tracks are enough to label a transcript later (when live text wasn't available)
    labelled = label_recording(folder, [Segment(1.0, 8.0, "x"), Segment(11.0, 18.0, "y")])
    assert [s.speaker for s in labelled] == [YOU, OTHERS]


def test_a_meeting_that_couldnt_be_transcribed_live_is_labelled_when_the_pipeline_catches_up(env):
    live, devices, clock, manager = make_manager(env, None)  # no Groq key: nothing is transcribed while recording
    sid = manager.start(title="", subject_id=None, language="en")
    capture = manager.captures[sid]
    capture._halt.set()
    play(devices, clock, capture, 3.0, you=True)
    play(devices, clock, capture, 3.0, others=True)
    rid = manager.finish(sid)
    assert env.store.get_transcript(rid) is None and env.store.get_recording(rid).title.startswith("Meeting — ")
    from transcriber.pipeline import Pipeline
    pipeline = Pipeline(env.store, FakeTranscriber([[Segment(0.5, 2.5, "me"), Segment(3.5, 5.5, "them")]]), env.tmp / "recordings")
    pipeline._save(rid, [Segment(0.5, 2.5, "me"), Segment(3.5, 5.5, "them")], "fake/model", env.tmp / "work")
    assert [s.speaker for s in env.store.get_transcript(rid).segments] == [YOU, OTHERS]
    assert env.store.get_speaker_names(rid) == NAMES


def test_only_one_meeting_at_a_time_and_a_failed_start_leaves_nothing_behind(env):
    live, devices, clock, manager = make_manager(env, FakeTranscriber())
    sid = manager.start(title="", subject_id=None, language="en")
    with pytest.raises(MeetingError, match="already"):
        manager.start(title="", subject_id=None, language="en")
    manager.discard(sid)
    assert manager.captures == {} and live.sessions == {}
    devices.fail = "Windows wouldn't let me open the microphone."
    with pytest.raises(MeetingError, match="microphone"):
        manager.start(title="", subject_id=None, language="en")
    assert live.sessions == {} and not any((env.tmp / "live").iterdir())


# --- the API

def make_app(tmp_path, devices, writer=None):
    config = Config(groq_api_key=None, data_dir=tmp_path / "data", port=0)
    return create_app(config, transcriber=FakeTranscriber(), writer=writer, checker=None, ask_engine=None, remote=None,
                      meeting_devices=devices, start_worker=False, privacy_allow_all=True,
                      meeting_clock=lambda: 0.0)  # time stands still, so "nothing was heard yet" is not a race


def test_the_api_starts_polls_and_finishes_a_meeting_and_reports_problems(tmp_path):
    devices = FakeDevices()
    app = make_app(tmp_path, devices)
    with TestClient(app) as client:
        assert client.get("/api/meeting/devices").json() == {"available": True, "reason": None, "output": "Fake speakers",
                                                             "input": "Fake microphone", "recording": False}
        sid = client.post("/api/meeting/start", json={"title": "Standup", "subject": "", "language": "en"}).json()["id"]
        assert client.get("/api/meeting/devices").json()["recording"] is True
        assert client.post("/api/meeting/start", json={}).status_code == 400
        shown = client.get(f"/api/meeting/{sid}").json()
        assert shown["status"] == "recording" and shown["levels"] == {"you": 0.0, "others": 0.0}
        assert client.post(f"/api/meeting/{sid}/finish").status_code == 400  # nothing was heard yet
        assert client.get("/api/meeting/nope").status_code == 404
        devices.fail = "Windows wouldn't let me open the microphone."
        client.delete(f"/api/meeting/{sid}")
        failed = client.post("/api/meeting/start", json={})
        assert failed.status_code == 400 and "microphone" in failed.json()["detail"]


def test_pip_can_start_a_meeting_for_you(tmp_path):
    reply = {"say": "Starting your meeting recording.", "actions": [
        {"tool": "start_meeting", "subject": "", "from": "", "to": "", "start": "", "minutes": 0, "when": "", "text": "Team call"}]}
    app = make_app(tmp_path, FakeDevices(), FakeLLM("groq:x", [reply]))
    with TestClient(app) as client:
        said = client.post("/api/pip/command", json={"text": "record my zoom meeting"}).json()
        assert said["open"] == {"view": "meeting", "start": True, "title": "Team call", "subject": ""}
        assert said["done"] == [] and client.get("/api/meeting/devices").json()["recording"] is False  # the app starts it, not the server


# --- the meeting extras in the notes

def test_attendees_are_names_only_and_the_follow_up_message_is_kept():
    from transcriber.export import notes_to_md
    from transcriber.notes.model import Notes, normalize_attendees, notes_from_writer
    assert normalize_attendees(["Dr. Khan", "you", "Others", "  Sara  Ali ", "dr. khan", "", "Speaker"]) == ["Dr. Khan", "Sara Ali"]
    notes = notes_from_writer({"summary": [], "decisions": [], "action_items": [], "open_questions": [], "topics": [],
                               "attendees": ["Dr. Khan", "You", "Sara"], "follow_up_draft": "Thanks all. Sara will send the slides by Friday."})
    assert notes.attendees == ["Dr. Khan", "Sara"] and notes.follow_up_draft.startswith("Thanks all")
    again = Notes.from_dict(notes.to_dict())
    assert again.attendees == ["Dr. Khan", "Sara"] and "Dr. Khan" in again.plain_text()
    text = notes_to_md(again, False)
    assert "**Attendees:** Dr. Khan, Sara" in text and "**Follow-up message**" in text
    assert Notes.from_dict({}).attendees == []  # notes written before this existed still open
