import pytest

from transcriber.notes.pipeline import NotesPipeline
from transcriber.providers.base import PermanentError, RateLimited, Segment, TransientError
from transcriber.store import Store

from .helpers import FakeClock, FakeLLM

WRITER_REPLY = {
    "summary": [{"text": "Planned the launch.", "cites": ["[00:00]"]}],
    "decisions": [{"text": "Migrate on Saturday.", "cites": ["[00:05]"]}],
    "action_items": [], "open_questions": [], "topics": ["launch", "database"],
}
CHECKER_REPLY = {"verdicts": [{"id": "S1", "verdict": "supported", "reason": "ok"},
                              {"id": "D1", "verdict": "unsupported", "reason": "never said"}],
                 "missed": []}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


def ready_recording(store, rid="r1"):
    store.create_recording(rid, "Planning", None, "en", "a.wav")
    store.update_recording(rid, duration_sec=30.0)
    store.save_transcript(rid, "t", [Segment(0.0, 2.0, "We plan the launch."),
                                     Segment(5.0, 7.0, "Migration is on Saturday.")], "fake")
    store.queue_notes(rid)
    return rid


def make(store, writer, checker=None, clock=None):
    clock = clock or FakeClock()
    return NotesPipeline(store, writer, checker, sleep=clock.sleep, clock=clock.time)


def test_writes_checks_saves_and_suggests_topics(store):
    rid = ready_recording(store)
    store.add_topic(rid, "Launch")
    writer, checker = FakeLLM("cerebras:gpt-oss-120b", [WRITER_REPLY]), FakeLLM("gemini:flash", [CHECKER_REPLY])
    make(store, writer, checker).process(rid)
    record = store.get_notes(rid)
    assert (record.status, record.writer, record.checker, record.check_detail) == (
        "ready", "cerebras:gpt-oss-120b", "gemini:flash", None)
    assert [(i.id, i.verdict) for i in record.notes.items()] == [("S1", "supported"), ("D1", "unsupported")]
    assert "[00:00] We plan the launch.\n\n[00:05] Migration is on Saturday." in writer.calls[0]["user"]
    assert "Existing topics: Launch" in writer.calls[0]["user"]
    assert store.recording_topics(rid) == [
        {"id": store.get_or_create_topic("launch"), "name": "Launch", "state": "accepted"},
        {"id": store.get_or_create_topic("database"), "name": "database", "state": "suggested"},
    ]


def test_without_checker_notes_are_saved_unchecked(store):
    rid = ready_recording(store)
    make(store, FakeLLM("w", [WRITER_REPLY]), None).process(rid)
    record = store.get_notes(rid)
    assert (record.status, record.checker, record.check_detail) == ("ready", None, "No checker configured")
    assert all(item.verdict is None for item in record.notes.items())


def test_checker_failure_never_blocks_notes(store):
    rid = ready_recording(store)
    make(store, FakeLLM("w", [WRITER_REPLY]), FakeLLM("c", [PermanentError("Invalid Gemini API key")])).process(rid)
    record = store.get_notes(rid)
    assert (record.status, record.check_detail) == ("ready", "Not checked: Invalid Gemini API key")
    assert record.notes.decisions[0].text == "Migrate on Saturday."


def test_writer_rate_limit_waits_then_succeeds(store):
    rid = ready_recording(store)
    clock = FakeClock()
    seen = []
    original = store.update_notes_status

    def spy(r, status, detail=None):
        seen.append((status, detail))
        original(r, status, detail)

    store.update_notes_status = spy
    make(store, FakeLLM("w", [RateLimited(20.0), WRITER_REPLY]), None, clock=clock).process(rid)
    assert store.get_notes(rid).status == "ready"
    assert sum(clock.sleeps) == 20.0
    assert any(status == "writing" and detail and detail.startswith("Rate limit reached") for status, detail in seen)


def test_writer_failure_marks_notes_failed(store):
    rid = ready_recording(store)
    make(store, FakeLLM("w", [TransientError("Cerebras server error")] * 4), None).process(rid)
    record = store.get_notes(rid)
    assert (record.status, record.status_detail) == ("failed", "Cerebras server error")


def test_unexpected_error_is_reported(store):
    rid = ready_recording(store)
    make(store, FakeLLM("w", [ValueError("boom")]), None).process(rid)
    assert store.get_notes(rid).status_detail == "Unexpected error (ValueError); details are in the app log"
    assert "boom" not in store.get_notes(rid).status_detail  # the error's own text is never shown


def test_recording_deleted_while_writing(store):
    rid = ready_recording(store)

    def delete_then_reply(call):
        store.delete_recording(rid)
        return WRITER_REPLY

    make(store, FakeLLM("w", [delete_then_reply]), FakeLLM("c", [CHECKER_REPLY])).process(rid)
    assert store.get_recording(rid) is None and store.get_notes(rid) is None


def test_missing_transcript_drops_the_job(store):
    store.create_recording("r1", "t", None, "en", "a.wav")
    store.queue_notes("r1")
    writer = FakeLLM("w")
    make(store, writer).process("r1")
    assert store.get_notes("r1") is None and writer.calls == []
