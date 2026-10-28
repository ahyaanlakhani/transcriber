from pathlib import Path

from transcriber.notes.model import notes_from_writer
from transcriber.notes.pipeline import NotesPipeline
from transcriber.providers.base import PermanentError, RateLimited, Segment
from transcriber.recap.pipeline import RECAP_FILE, RecapPipeline
from transcriber.store import Store
from transcriber.worker import Worker

from .helpers import FakeClock, FakeLLM

SCRIPT = {"script": "Your last class covered autonomy.\n\n" + " ".join(["word"] * 100)}
NOTES = {"summary": [{"text": "Autonomy.", "cites": []}], "lecture": [], "decisions": [], "action_items": [],
         "open_questions": [], "topics": []}


class FakeVoice:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def speak(self, paragraphs, *, voice, speed, dst, on_status=None):
        if self.error:
            raise self.error
        self.calls.append((paragraphs, voice, speed))
        Path(dst).write_bytes(b"mp3")
        return 301.5


def setup(tmp_path, duration=2700.0):
    store = Store(tmp_path / "app.db")
    store.create_recording("a", "Machine Learning", store.get_or_create_subject("Machine Learning"), "en", "a.m4a")
    store.update_recording("a", duration_sec=duration, status="done", recorded_at="2026-10-07T05:02:16+00:00")
    (tmp_path / "recordings" / "a").mkdir(parents=True)
    store.save_transcript("a", "Hello", [Segment(0.0, 1.0, "Hello", -0.1, 0.01, 1.0)], "groq")
    return store


def ready_notes(store):
    store.queue_notes("a")
    store.save_notes("a", notes_from_writer(NOTES), writer="w", checker=None, check_detail=None)


def test_makes_a_recap(tmp_path):
    store = setup(tmp_path)
    ready_notes(store)
    store.queue_recap("a")
    voice = FakeVoice()
    RecapPipeline(store, FakeLLM(replies=[SCRIPT]), voice, tmp_path / "recordings").process("a")
    recap = store.get_recap("a")
    assert (recap.status, recap.duration_sec, recap.voice, recap.speed) == ("ready", 301.5, "af_heart", 1.1)
    assert recap.script.startswith("Your last class")
    assert voice.calls[0][0][0] == "Your last class covered autonomy."
    assert (tmp_path / "recordings" / "a" / RECAP_FILE).read_bytes() == b"mp3"


def test_notes_job_queues_a_recap_for_long_recordings_only(tmp_path):
    for duration, expected in ((2700.0, "queued"), (60.0, None)):
        store = setup(tmp_path / str(duration), duration)
        store.queue_notes("a")
        NotesPipeline(store, FakeLLM(replies=[NOTES])).process("a")
        assert (store.get_recap("a").status if store.get_recap("a") else None) == expected


def test_recaps_off_means_none_are_queued(tmp_path):
    store = setup(tmp_path)
    store.update_settings({"recaps_on": "0"})
    store.queue_notes("a")
    NotesPipeline(store, FakeLLM(replies=[NOTES])).process("a")
    assert store.get_recap("a") is None


def test_voice_failure_is_shown(tmp_path):
    store = setup(tmp_path)
    ready_notes(store)
    store.queue_recap("a")
    RecapPipeline(store, FakeLLM(replies=[SCRIPT]), FakeVoice(PermanentError("The recap voice isn't set up")),
                  tmp_path / "recordings").process("a")
    assert (store.get_recap("a").status, store.get_recap("a").status_detail) == ("failed", "The recap voice isn't set up")


def test_needs_notes_first(tmp_path):
    store = setup(tmp_path)
    store.queue_recap("a")
    RecapPipeline(store, FakeLLM(), FakeVoice(), tmp_path / "recordings").process("a")
    assert store.get_recap("a").status_detail == "Needs notes first"


def test_rate_limit_is_waited_out(tmp_path):
    store = setup(tmp_path)
    ready_notes(store)
    store.queue_recap("a")
    clock = FakeClock()
    RecapPipeline(store, FakeLLM(replies=[RateLimited(30, "slow down"), SCRIPT]), FakeVoice(),
                  tmp_path / "recordings", sleep=clock.sleep, clock=clock.time).process("a")
    assert store.get_recap("a").status == "ready"


def test_worker_runs_recaps_after_notes(tmp_path):
    store = setup(tmp_path)
    store.queue_notes("a")
    worker = Worker(store, None, NotesPipeline(store, FakeLLM(replies=[NOTES])),
                    RecapPipeline(store, FakeLLM(replies=[SCRIPT]), FakeVoice(), tmp_path / "recordings"))
    assert worker.run_once() and store.get_recap("a").status == "queued"
    assert worker.run_once() and store.get_recap("a").status == "ready"
    assert not worker.run_once()
