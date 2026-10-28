import pytest

from transcriber.notes.checker import check_notes
from transcriber.notes.model import CHECKER_SCHEMA, WRITER_SCHEMA, NoteItem, Notes
from transcriber.notes.prompts import CHECKER_SYSTEM, MERGE_SYSTEM, WRITER_SYSTEM
from transcriber.notes.writer import write_notes
from transcriber.postprocess import Paragraph
from transcriber.providers.base import PermanentError

from .helpers import FakeLLM

DIRECT = lambda fn: fn()  # noqa: E731

WRITER_REPLY = {
    "summary": [{"text": "Planned the launch.", "cites": ["[00:00]"]}],
    "decisions": [{"text": "Migrate on Saturday.", "cites": ["[01:15]"]}],
    "action_items": [{"text": "Review PRs.", "owner": "Jordan", "due": "Wednesday", "cites": ["[01:15]"]}],
    "open_questions": [],
    "topics": ["launch"],
}


def test_short_transcript_is_one_call():
    llm = FakeLLM(replies=[WRITER_REPLY])
    paragraphs = [Paragraph(0.0, "We plan the launch."), Paragraph(75.0, "Migration on Saturday.")]
    notes = write_notes(llm, title="Planning", paragraphs=paragraphs, long_form=False,
                        existing_topics=["launch", "hiring"], call=DIRECT)
    assert [i.text for i in notes.decisions] == ["Migrate on Saturday."]
    assert notes.action_items[0].owner == "Jordan"
    [call] = llm.calls
    assert call["system"] == WRITER_SYSTEM and call["schema"] is WRITER_SCHEMA
    assert "Meeting title: Planning" in call["user"]
    assert "Existing topics: launch, hiring" in call["user"]
    assert "[00:00] We plan the launch.\n\n[01:15] Migration on Saturday." in call["user"]


def test_empty_transcript_makes_no_calls():
    llm = FakeLLM()
    assert write_notes(llm, title="t", paragraphs=[], long_form=False, existing_topics=[], call=DIRECT) == Notes()
    assert llm.calls == []


def test_long_transcript_is_split_then_merged(monkeypatch):
    monkeypatch.setattr("transcriber.notes.transcript.PART_MAX_CHARS", 100)
    monkeypatch.setattr("transcriber.notes.writer.split_parts",
                        lambda paragraphs, max_chars: [paragraphs[:1], paragraphs[1:]])
    part = {"summary": [{"text": "part", "cites": []}], "decisions": [], "action_items": [],
            "open_questions": [], "topics": []}
    llm = FakeLLM(replies=[part, part, WRITER_REPLY])
    notes = write_notes(llm, title="Long", paragraphs=[Paragraph(0.0, "a"), Paragraph(5.0, "b")],
                        long_form=False, existing_topics=[], call=DIRECT)
    assert len(llm.calls) == 3
    assert "part 1 of 2" in llm.calls[0]["user"] and "part 2 of 2" in llm.calls[1]["user"]
    assert llm.calls[2]["system"] == MERGE_SYSTEM
    assert '"text": "part"' in llm.calls[2]["user"]
    assert notes.summary[0].text == "Planned the launch."


def test_writer_calls_go_through_the_retry_wrapper():
    wrapped = []

    def call(fn):
        wrapped.append(fn)
        return fn()

    write_notes(FakeLLM(replies=[WRITER_REPLY]), title="t", paragraphs=[Paragraph(0.0, "x")], long_form=False,
                existing_topics=[], call=call)
    assert len(wrapped) == 1


def make_notes():
    return Notes(summary=[NoteItem("S1", "Planned the launch.", [0.0])],
                 decisions=[NoteItem("D1", "Migrate on Sunday.", [75.0])],
                 action_items=[NoteItem("A1", "Review PRs.", [75.0], owner="Jordan", due="Wednesday")])


def test_checker_applies_verdicts_and_missed_without_editing_text():
    llm = FakeLLM(replies=[{
        "verdicts": [
            {"id": "S1", "verdict": "supported", "reason": "Said at the start."},
            {"id": "D1", "verdict": "partial", "reason": "The transcript says Saturday, not Sunday."},
            {"id": "ZZ", "verdict": "supported", "reason": "unknown id"},
            {"id": "A1", "verdict": "maybe", "reason": "invalid verdict"},
        ],
        "missed": [{"text": "Launch depends on testing.", "cites": ["[02:00]"]}],
    }])
    notes = make_notes()
    checked = check_notes(llm, notes=notes, transcript_text="[00:00] ...", long_form=False, call=DIRECT)
    assert (checked.summary[0].verdict, checked.decisions[0].verdict) == ("supported", "partial")
    assert checked.decisions[0].reason == "The transcript says Saturday, not Sunday."
    assert checked.decisions[0].text == "Migrate on Sunday."
    assert checked.action_items[0].verdict is None
    assert [(m.id, m.text, m.cites) for m in checked.missed] == [("M1", "Launch depends on testing.", [120.0])]
    assert notes.summary[0].verdict is None  # the input is not mutated
    [call] = llm.calls
    assert call["system"] == CHECKER_SYSTEM and call["schema"] is CHECKER_SCHEMA
    assert "D1: Migrate on Sunday. [cites: [01:15]]" in call["user"]
    assert "A1: Review PRs. (owner: Jordan) (due: Wednesday) [cites: [01:15]]" in call["user"]
    assert call["user"].startswith("TRANSCRIPT:\n[00:00] ...")


def test_instructions_and_parts_reach_writer_and_checker():
    writer = FakeLLM(replies=[WRITER_REPLY])
    paragraphs = [Paragraph(0.0, "Welcome."), Paragraph(40.0, "Back after the pause.")]
    notes = write_notes(writer, title="Lecture", paragraphs=paragraphs, long_form=False, existing_topics=[],
                        call=DIRECT, instructions="The main speaker is the instructor.", part_starts=[0.0, 40.0])
    user = writer.calls[0]["user"]
    assert "Instructions from the user: The main speaker is the instructor." in user
    assert "recorded in 2 parts" in user and "— Part 2 —\n\n[00:40] Back after the pause." in user
    checker = FakeLLM(replies=[{"verdicts": [], "missed": []}])
    check_notes(checker, notes=notes, transcript_text="T", long_form=False, call=DIRECT,
                instructions="The main speaker is the instructor.", part_count=2)
    assert "Instructions from the user: The main speaker is the instructor." in checker.calls[0]["user"]
    assert "recorded in 2 parts" in checker.calls[0]["user"]


def test_combining_step_asks_for_low_effort_and_parts_do_not():
    """Long lectures: the step combining part notes used to run out of output tokens on gpt-oss (its thinking
    counts toward the cap), so Groq returned 'json_validate_failed' / 'max completion tokens reached'."""
    from transcriber.notes.prompts import MERGE_SYSTEM
    from transcriber.notes.writer import write_notes
    from transcriber.postprocess import Paragraph

    from .helpers import FakeLLM
    reply = {"summary": [], "decisions": [], "action_items": [], "open_questions": [], "topics": []}
    llm = FakeLLM("groq:openai/gpt-oss-120b", [reply] * 5)
    llm.max_input_chars = 100
    paragraphs = [Paragraph(i * 10.0, "word " * 30) for i in range(4)]
    write_notes(llm, title="Long", paragraphs=paragraphs, long_form=False, existing_topics=[], call=lambda fn: fn())
    merges = [c for c in llm.calls if c["system"] == MERGE_SYSTEM]
    assert len(merges) == 1 and merges[0]["effort"] == "low"
    assert all(c["effort"] is None for c in llm.calls if c["system"] != MERGE_SYSTEM)


def test_a_request_the_model_calls_too_large_is_retried_in_smaller_parts():
    # Roman Urdu takes about twice the tokens of English for the same characters, so a part sized for English can be
    # too big for a small-limit model (Groq answers HTTP 413). The writer halves the part size and carries on.
    part = {"summary": [{"text": "part", "cites": []}], "decisions": [], "action_items": [],
            "open_questions": [], "topics": []}
    llm = FakeLLM(replies=[PermanentError("Groq rejected the request (HTTP 413)"), part, part, WRITER_REPLY])
    llm.max_input_chars = 14_000
    paragraphs = [Paragraph(float(n * 10), "x" * 2_300) for n in range(6)]
    notes = write_notes(llm, title="Urdu", paragraphs=paragraphs, long_form=False, existing_topics=[], call=DIRECT)
    assert len(llm.calls) == 4
    assert len(llm.calls[0]["user"]) > len(llm.calls[1]["user"])
    assert [i.text for i in notes.decisions] == ["Migrate on Saturday."]


def test_a_too_large_request_that_cannot_get_smaller_still_fails():
    llm = FakeLLM(replies=[PermanentError("Groq rejected the request (HTTP 413)")] * 12)
    llm.max_input_chars = 14_000
    with pytest.raises(PermanentError):
        write_notes(llm, title="Urdu", paragraphs=[Paragraph(0.0, "x" * 600)], long_form=False, existing_topics=[],
                    call=DIRECT)


def test_a_refused_part_is_split_alone_and_the_parts_already_written_are_kept(monkeypatch):
    part = {"summary": [{"text": "part", "cites": []}], "decisions": [], "action_items": [],
            "open_questions": [], "topics": []}
    big = [Paragraph(float(n * 10), "x" * 2_300) for n in range(8)]
    monkeypatch.setattr("transcriber.notes.writer.split_parts", lambda paragraphs, max_chars: [paragraphs[:2], paragraphs[2:]])
    # part 1 is fine; part 2 is refused, then its two halves are written; then the merge
    llm = FakeLLM(replies=[part, PermanentError("Groq rejected the request (HTTP 413)"), part, part, WRITER_REPLY])
    notes = write_notes(llm, title="Urdu", paragraphs=big, long_form=False, existing_topics=[], call=DIRECT)
    assert len(llm.calls) == 5  # not 3 + 5: part 1 was not written twice
    assert [i.text for i in notes.decisions] == ["Migrate on Saturday."]
