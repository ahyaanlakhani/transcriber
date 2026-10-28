import pytest

from transcriber.notes.ask import ASK_SCHEMA, ASK_SYSTEM, ask
from transcriber.notes.model import NoteItem, Notes
from transcriber.postprocess import Paragraph
from transcriber.providers.base import PermanentError, RateLimited

from .helpers import FakeLLM

PARAGRAPHS = [Paragraph(0.0, "Sara will send the pricing report on Friday."), Paragraph(20.0, "Launch moves to November.")]
NOTES = Notes(summary=[NoteItem("S1", "Launch moved.", [20.0])],
              action_items=[NoteItem("A1", "Send pricing report.", [0.0], owner="Sara", due="Friday")])
HISTORY = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello."}]


def run(engines, question="Who sends the report?", paragraphs=PARAGRAPHS):
    return ask(engines, title="Planning", paragraphs=paragraphs, long_form=False, notes=NOTES,
               my_notes="Remember budget.", history=HISTORY, question=question)


def test_answers_with_full_context():
    llm = FakeLLM("claude-code", [{"answer": "Sara, on Friday [00:00].", "proposed_notes": None}])
    result = run([llm])
    assert (result.answer, result.proposal, result.engine, result.note) == (
        "Sara, on Friday [00:00].", None, "claude-code", None)
    [call] = llm.calls
    assert call["system"] == ASK_SYSTEM and call["schema"] is ASK_SCHEMA
    user = call["user"]
    for expected in ("Meeting title: Planning", "[00:00] Sara will send the pricing report on Friday.",
                     "A1: Send pricing report. (owner: Sara) (due: Friday) [cites: [00:00]]",
                     "MY NOTES:\nRemember budget.", "User: Hi\nAssistant: Hello.",
                     "LATEST MESSAGE:\nWho sends the report?"):
        assert expected in user


def test_returns_a_proposal():
    proposal = {"summary": [], "decisions": [], "action_items": [], "open_questions": [], "topics": []}
    result = run([FakeLLM("c", [{"answer": "Cleared.", "proposed_notes": proposal}])], "Clear the notes")
    assert result.proposal == proposal


def test_falls_back_and_explains_why():
    claude = FakeLLM("claude-code", [PermanentError("Claude isn't signed in. Open a terminal and run: claude auth login")])
    groq = FakeLLM("groq:openai/gpt-oss-120b", [{"answer": "Sara.", "proposed_notes": None}])
    result = run([claude, groq])
    assert (result.answer, result.engine) == ("Sara.", "groq:openai/gpt-oss-120b")
    assert "claude auth login" in result.note


def test_transcript_left_out_when_too_long_for_the_engine():
    groq = FakeLLM("groq", [{"answer": "From the notes.", "proposed_notes": None}])
    groq.max_input_chars = 30
    result = run([groq])
    assert "Sara will send" not in groq.calls[0]["user"]
    assert "too long for this model" in groq.calls[0]["user"] and "too long" in result.note


def test_all_engines_failing_raises():
    with pytest.raises(PermanentError, match="No AI model could answer"):
        run([FakeLLM("a", [RateLimited(30)]), FakeLLM("b", [PermanentError("bad key")])])


def test_no_engines():
    with pytest.raises(PermanentError):
        run([])


def test_instructions_and_parts_reach_ask():
    llm = FakeLLM("c", [{"answer": "ok", "proposed_notes": None}])
    ask([llm], title="Lecture", paragraphs=PARAGRAPHS, long_form=False, notes=NOTES, my_notes="", history=[],
        question="Q", instructions="I'm the student.", part_starts=[0.0, 20.0])
    user = llm.calls[0]["user"]
    assert "Instructions from the user: I'm the student." in user and "— Part 2 —" in user


# --- citations that point at nothing

def test_impossible_timestamps_are_cut_and_counted():
    from transcriber.notes.ask import drop_impossible_cites
    text, removed = drop_impossible_cites("It starts at [02:10] and ends [99:59], also [1:30:00].", 600.0)
    assert text == "It starts at [02:10] and ends, also." and removed == 2
    assert drop_impossible_cites("Fine [10:02].", 600.0) == ("Fine [10:02].", 0)  # inside the slack past the end
    assert drop_impossible_cites("Anything [99:59]", None) == ("Anything [99:59]", 0)  # length unknown: leave alone
