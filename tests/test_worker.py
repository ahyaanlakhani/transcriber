import time

import pytest

from transcriber.store import Store
from transcriber.worker import Worker


class StubPipeline:
    def __init__(self, store):
        self.store, self.processed = store, []

    def process(self, rid):
        self.processed.append(rid)
        self.store.update_recording(rid, status="done")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


def add(store, rid):
    store.create_recording(rid, "T", None, "en", "a.wav")


def test_run_once_processes_oldest_queued(store):
    add(store, "a1"), add(store, "a2")
    pipeline = StubPipeline(store)
    worker = Worker(store, pipeline)
    assert worker.run_once() is True
    assert worker.run_once() is True
    assert worker.run_once() is False
    assert pipeline.processed == ["a1", "a2"]


def test_background_thread_picks_up_new_work(store):
    pipeline = StubPipeline(store)
    worker = Worker(store, pipeline, poll_interval=30.0)
    worker.start()
    try:
        add(store, "a1")
        worker.wake()
        deadline = time.monotonic() + 5
        while not pipeline.processed and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pipeline.processed == ["a1"]
    finally:
        worker.stop()
    assert not worker.is_alive()


def test_stop_without_start_is_safe(store):
    Worker(store, StubPipeline(store)).stop()


class StubNotes:
    def __init__(self, store):
        self.store, self.processed = store, []

    def process(self, rid):
        self.processed.append(rid)
        self.store.update_notes_status(rid, "ready")


def test_notes_run_after_transcription_queue_is_empty(store):
    add(store, "a1"), add(store, "a2")
    store.save_transcript("a1", "t", [], "fake")
    store.queue_notes("a1")
    pipeline, notes = StubPipeline(store), StubNotes(store)
    worker = Worker(store, pipeline, notes)
    assert worker.run_once() and pipeline.processed == ["a2"] and notes.processed == []
    assert worker.run_once() and notes.processed == ["a1"]
    assert worker.run_once() is False


def test_worker_with_only_notes(store):
    add(store, "a1")
    store.save_transcript("a1", "t", [], "fake")
    store.queue_notes("a1")
    notes = StubNotes(store)
    assert Worker(store, None, notes).run_once() and notes.processed == ["a1"]
