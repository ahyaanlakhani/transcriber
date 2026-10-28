"""Conversation coach: who's speaking (from the words, no voice training), coaching notes, and your numbers."""
import pytest

from transcriber.export import notes_to_md
from transcriber.notes.coach import (ATTRIBUTION_SYSTEM, COACH_FINAL_SYSTEM, COACH_PART_SYSTEM, ME, THEM,
                                     attribute_speakers, conversation_stats, write_coaching)
from transcriber.notes.model import Notes, notes_from_writer
from transcriber.notes.pipeline import NotesPipeline
from transcriber.postprocess import Paragraph
from transcriber.providers.base import Segment
from transcriber.store import Store

from .helpers import FakeLLM

DIRECT = lambda fn: fn()  # noqa: E731


def seg(start, end, text, speaker=None):
    return Segment(start, end, text, -0.1, 0.01, 1.0, speaker)


CHAT = [seg(0, 4, "Hi, I'm Sam, a second-year student at Northfield University."),
        seg(4, 8, "I'm curious, how did you get into product management?"),
        seg(8, 30, "I started as an engineer at a fintech, then moved to product after five years."),
        seg(30, 34, "Um, did you like it?"),
        seg(34, 60, "Yes. My advice is to build things and talk to users early."),
        seg(60, 64, "That's great, thank you. I'll send you my portfolio.")]


# --- who is speaking

def test_attribution_labels_each_line_from_the_turns():
    llm = FakeLLM(replies=[{"turns": [{"line": 1, "who": "me"}, {"line": 3, "who": "them"},
                                      {"line": 4, "who": "me"}, {"line": 5, "who": "them"},
                                      {"line": 6, "who": "me"}], "their_name": "Sara"}])
    labeled, name = attribute_speakers(llm, CHAT, call=DIRECT, title="Coffee with Sara")
    assert [s.speaker for s in labeled] == [ME, ME, THEM, ME, THEM, ME]
    assert name == "Sara"
    assert llm.calls[0]["user"].startswith("Recording title: Coffee with Sara")
    [call] = llm.calls
    assert call["system"] == ATTRIBUTION_SYSTEM and call["effort"] == "low"
    assert "1| Hi, I'm Sam" in call["user"] and "6| That's great" in call["user"]
    assert [s.text for s in labeled] == [s.text for s in CHAT]  # only the labels change


def test_attribution_runs_per_part_with_line_numbers_continuing(monkeypatch):
    monkeypatch.setattr("transcriber.notes.coach.ATTRIBUTION_PART_CHARS", 220)
    llm = FakeLLM(replies=[{"turns": [{"line": 1, "who": "me"}, {"line": 3, "who": "them"}], "their_name": None},
                           {"turns": [{"line": 4, "who": "me"}, {"line": 5, "who": "them"}, {"line": 6, "who": "me"}],
                            "their_name": "Sara"}])
    labeled, name = attribute_speakers(llm, CHAT, call=DIRECT)
    assert len(llm.calls) == 2 and "4| Um, did you like it?" in llm.calls[1]["user"]
    assert [s.speaker for s in labeled] == [ME, ME, THEM, ME, THEM, ME] and name == "Sara"


def test_a_speaker_tools_labels_are_passed_as_hints():
    llm = FakeLLM(replies=[{"turns": [{"line": 1, "who": "them"}], "their_name": None}])
    attribute_speakers(llm, [seg(0, 1, "Hello", "SPEAKER_01")], call=DIRECT)
    assert "1| [SPEAKER_01] Hello" in llm.calls[0]["user"]


def test_missing_or_bad_turns_fall_back_to_the_previous_speaker():
    llm = FakeLLM(replies=[{"turns": [{"line": 2, "who": "them"}, {"line": 99, "who": "me"},
                                      {"line": 4, "who": "nobody"}], "their_name": ""}])
    labeled, name = attribute_speakers(llm, CHAT, call=DIRECT)
    assert [s.speaker for s in labeled] == [ME, THEM, THEM, THEM, THEM, THEM]  # starts with you until told otherwise
    assert name is None


# --- your numbers

def test_conversation_stats():
    labeled = [seg(0, 4, "Hi, I'm Sam.", ME), seg(4, 8, "Um, how did you start, you know?", ME),
               seg(8, 30, "I was an engineer.", THEM), seg(30, 34, "Did you like it?", ME),
               seg(34, 60, "Yes, I did.", THEM)]
    stats = conversation_stats(labeled)
    assert stats["my_share"] == pytest.approx(12 / 60, abs=0.01)
    assert stats["my_questions"] == 2
    assert stats["longest_turn_sec"] == pytest.approx(8.0)
    assert stats["fillers"] == 2 and stats["filler_words"] == {"um": 1, "you know": 1}


def test_no_stats_without_speakers():
    assert conversation_stats(CHAT) is None


# --- the coaching notes

PART = {"about_them": [{"text": "Moved from engineering to product after five years.", "cites": ["[00:08]"]}],
        "intro": [{"text": "Hi, I'm Sam, a second-year student at Northfield University.",
                   "suggestion": "I'm Sam, a second-year student exploring product roles.", "cites": ["[00:00]"]}],
        "my_questions": [{"text": "Did you like it?", "kind": "closed", "suggestion": "What surprised you most?",
                          "cites": ["[00:30]"]}],
        "missed_followups": [{"text": "She said to talk to users early.",
                              "suggestion": "How did you find your first users?", "cites": ["[00:34]"]}],
        "action_items": [{"text": "Send her my portfolio.", "owner": "You", "due": None, "cites": ["[01:00]"]}]}
FINAL = {"summary": [{"text": "Sara moved into product after engineering.", "cites": ["[00:08]"]}],
         "feedback": [{"text": "Open questions got the best answers.", "kind": "strength", "cites": []},
                      {"text": "Swap closed questions for open ones.", "kind": "improve", "cites": ["[00:30]"]}],
         "follow_up_draft": "Hi Sara, thank you for the chat...",
         "topics": ["product management"]}


def test_write_coaching_parts_then_final():
    llm = FakeLLM(replies=[PART, FINAL])
    paragraphs = [Paragraph(0.0, "Hi, I'm Sam.", "You"), Paragraph(8.0, "I started as an engineer.", "Sara")]
    stats = {"my_share": 0.3, "my_questions": 2, "longest_turn_sec": 8.0, "fillers": 1, "filler_words": {"um": 1},
             "my_minutes": 0.2}
    notes = write_coaching(llm, title="Coffee with Sara", paragraphs=paragraphs, long_form=False,
                           existing_topics=[], call=DIRECT, stats=stats, their_name="Sara")
    part_call, final_call = llm.calls
    assert part_call["system"] == COACH_PART_SYSTEM and "You: Hi, I'm Sam." in part_call["user"]
    assert final_call["system"] == COACH_FINAL_SYSTEM and '"my_share": 0.3' in final_call["user"]
    assert "Moved from engineering" in final_call["user"]
    assert notes.intro[0].suggestion.startswith("I'm Sam")
    assert (notes.my_questions[0].kind, notes.my_questions[0].suggestion) == ("closed", "What surprised you most?")
    assert [f.kind for f in notes.feedback] == ["strength", "improve"]
    assert notes.missed_followups[0].suggestion == "How did you find your first users?"
    assert notes.action_items[0].owner == "You"
    assert notes.follow_up_draft.startswith("Hi Sara") and notes.person == "Sara"
    assert [i.id for i in notes.my_questions] == ["Y1"] and notes.topics == ["product management"]


def test_only_the_first_intro_is_kept_across_parts(monkeypatch):
    monkeypatch.setattr("transcriber.notes.coach.split_parts", lambda paragraphs, max_chars: [paragraphs[:1], paragraphs[1:]])
    second = {**PART, "intro": [{"text": "Again, I'm Sam.", "suggestion": None, "cites": []}]}
    llm = FakeLLM(replies=[PART, second, FINAL])
    notes = write_coaching(llm, title="t", paragraphs=[Paragraph(0.0, "a", "You"), Paragraph(900.0, "b", "Sara")],
                           long_form=False, existing_topics=[], call=DIRECT, stats=None, their_name=None)
    assert len(notes.intro) == 1 and len(notes.my_questions) == 2


def test_coaching_notes_survive_a_round_trip_and_export():
    notes = notes_from_writer({**PART, **FINAL})
    notes.follow_up_draft, notes.person = "Hi Sara!", "Sara"
    again = Notes.from_dict(notes.to_dict())
    assert again.follow_up_draft == "Hi Sara!" and again.intro[0].suggestion
    md = notes_to_md(again, long_form=False)
    assert "**Your intro**" in md and "Try: I'm Sam" in md
    assert "**Missed follow-ups**" in md and "Could ask: How did you find your first users?" in md
    assert "**Follow-up message**" in md and "Hi Sara!" in md


# --- the whole job

def test_conversation_style_labels_speakers_then_coaches(tmp_path):
    store = Store(tmp_path / "app.db")
    store.create_recording("c", "Coffee with Sara", None, "en", "c.m4a")
    store.update_recording("c", notes_style="conversation", duration_sec=64.0)
    store.save_transcript("c", "chat", CHAT, "groq")
    store.queue_notes("c")
    attribution = {"turns": [{"line": 1, "who": "me"}, {"line": 3, "who": "them"}, {"line": 4, "who": "me"},
                             {"line": 5, "who": "them"}, {"line": 6, "who": "me"}], "their_name": "Sara"}
    NotesPipeline(store, FakeLLM(replies=[attribution, PART, FINAL])).process("c")
    assert [s.speaker for s in store.get_transcript("c").segments] == [ME, ME, THEM, ME, THEM, ME]
    assert store.get_speaker_names("c") == {ME: "You", THEM: "Sara"}
    record = store.get_notes("c")
    assert record.status == "ready" and record.notes.feedback and record.notes.person == "Sara"
    store.close()


def test_a_coached_chat_is_filed_under_the_person(tmp_path):
    store = Store(tmp_path / "app.db")
    store.create_recording("c", "Coffee with Sara", None, "en", "c.m4a")
    store.update_recording("c", notes_style="conversation", duration_sec=64.0)
    store.save_transcript("c", "chat", [seg(0, 4, "Hi", ME), seg(4, 8, "Hello", THEM)], "groq")
    store.set_speaker_names("c", {ME: "You", THEM: "Sara"})
    store.queue_notes("c")
    NotesPipeline(store, FakeLLM(replies=[PART, FINAL])).process("c")
    assert store.get_recording("c").person == "Sara"
    store.close()
