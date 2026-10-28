import pytest

from transcriber.notes.model import notes_from_writer
from transcriber.providers.base import PermanentError
from transcriber.recap.script import RECAP_SCHEMA, RECAP_SYSTEM, spoken_paragraphs, spoken_when, write_script

from .helpers import FakeLLM

DIRECT = lambda fn: fn()  # noqa: E731
LONG = " ".join(["word"] * 120)


def notes():
    n = notes_from_writer({
        "summary": [{"text": "Autonomy closes the loop.", "cites": []}],
        "lecture": [{"heading": "Autonomy", "kind": "definition", "term": "Autonomous function",
                     "text": "Senses, decides and acts alone.", "cites": []},
                    {"heading": "Autonomy", "kind": "stressed", "term": None, "text": "This is on the exam.",
                     "cites": []},
                    {"heading": "Robots", "kind": "fact", "term": None, "text": "Made up number.", "cites": []}],
        "action_items": [{"text": "Read chapter 3.", "owner": None, "due": "Monday", "cites": []}],
        "open_questions": [], "topics": []})
    n.lecture[2].verdict = "unsupported"
    return n


def test_script_request_uses_checked_notes_low_effort():
    llm = FakeLLM(replies=[{"script": LONG}])
    script = write_script(llm, title="Machine Learning", subject="Machine Learning", when="Wednesday morning, 7 October",
                          notes=notes(), instructions="Call me Sam.", call=DIRECT)
    assert script == LONG
    [call] = llm.calls
    assert call["system"] == RECAP_SYSTEM and call["schema"] is RECAP_SCHEMA and call["effort"] == "low"
    user = call["user"]
    assert "Wednesday morning, 7 October" in user and "Subject: Machine Learning" in user
    assert "Autonomy — definition of Autonomous function: Senses, decides and acts alone." in user
    assert "stressed: This is on the exam." in user
    assert "Read chapter 3. (due Monday)" in user
    assert "Made up number" not in user  # the checker said unsupported
    assert "Call me Sam." in user


def test_prompt_forbids_made_up_questions():
    assert "Never make up questions" in RECAP_SYSTEM


def test_too_short_is_retried_once_then_fails():
    llm = FakeLLM(replies=[{"script": "Too short."}, {"script": LONG}])
    assert write_script(llm, title="t", subject=None, when="today", notes=notes(), instructions="", call=DIRECT) == LONG
    llm = FakeLLM(replies=[{"script": "Short."}, {"script": ""}])
    with pytest.raises(PermanentError, match="usable script"):
        write_script(llm, title="t", subject=None, when="today", notes=notes(), instructions="", call=DIRECT)


def test_spoken_paragraphs_strip_what_cannot_be_read_aloud():
    text = "# Recap\n\n- First **point** [12:34] see https://x.y\n* Second\n\n\nThird  paragraph."
    assert spoken_paragraphs(text) == ["Recap", "First point see", "Second", "Third paragraph."]


def test_spoken_when(monkeypatch):
    assert spoken_when("2026-10-07T05:02:16+00:00", tz="Asia/Tashkent") == "Wednesday morning, the 7th of October"
    assert spoken_when("2026-10-05T11:58:05+00:00", tz="Asia/Tashkent") == "Monday afternoon, the 5th of October"


def test_coffee_chat_recaps_use_the_coaching():
    from transcriber.recap.script import notes_for_recap
    chat = notes_from_writer({"summary": [{"text": "Sara moved into product.", "cites": []}],
                              "about_them": [{"text": "Leads product at a fintech.", "cites": []}],
                              "feedback": [{"text": "Ask open questions.", "kind": "improve", "cites": []}],
                              "missed_followups": [{"text": "She mentioned users.", "suggestion": "How did you find them?",
                                                    "cites": []}]})
    text = notes_for_recap(chat)
    assert "About them:\n- Leads product at a fintech." in text
    assert "Coaching:\n- Work on: Ask open questions." in text
    assert "- She mentioned users. You could have asked: How did you find them?" in text
