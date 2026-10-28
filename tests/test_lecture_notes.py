"""Lecture notes: study notes by topic (points, definitions, examples, facts, what was stressed)."""
from transcriber.export import notes_to_md
from transcriber.notes.checker import check_notes
from transcriber.notes.editing import add_item, apply_proposal, update_item
from transcriber.notes.model import LECTURE_SCHEMA, WRITER_SCHEMA, Notes, notes_from_writer
from transcriber.notes.prompts import LECTURE_SYSTEM, MERGE_SYSTEM, WRITER_SYSTEM
from transcriber.notes.writer import write_notes
from transcriber.postprocess import Paragraph
from transcriber.providers.base import PermanentError
from transcriber.store import Store

from .helpers import FakeLLM, undo_privacy_migration

DIRECT = lambda fn: fn()  # noqa: E731


def lecture_item(heading, text, kind="point", term=None, cites=("[04:11]",)):
    return {"heading": heading, "kind": kind, "term": term, "text": text, "cites": list(cites)}


LECTURE_REPLY = {
    "summary": [{"text": "Autonomous systems and how they differ from automation.", "cites": ["[04:11]"]}],
    "lecture": [
        lecture_item("Autonomous systems", "They sense, think, decide and act without a person in the loop."),
        lecture_item("Autonomous systems", "Finds patterns in data it is given.", "definition", "Classical AI",
                     ["[07:52]"]),
        lecture_item("Autonomous systems", "China's dark factories run with almost no workers.", "example",
                     cites=["[06:56]"]),
        lecture_item("Autonomous systems", "Know the difference between automation and autonomy.", "stressed",
                     cites=["[09:30]"]),
    ],
    "action_items": [{"text": "Read chapter 3.", "owner": None, "due": "Monday", "cites": ["[40:00]"]}],
    "open_questions": [],
    "topics": ["autonomous systems"],
}


# --- the data

def test_lecture_items_keep_heading_kind_and_term():
    notes = notes_from_writer(LECTURE_REPLY)
    assert [i.id for i in notes.lecture] == ["L1", "L2", "L3", "L4"]
    definition = notes.lecture[1]
    assert (definition.heading, definition.kind, definition.term, definition.cites) == (
        "Autonomous systems", "definition", "Classical AI", [472.0])
    assert notes.decisions == []  # lectures have no decisions section
    assert notes.lecture[0].term is None


def test_unknown_kind_becomes_a_point_and_missing_heading_is_kept_empty():
    notes = notes_from_writer({"lecture": [{"heading": "  ", "kind": "rant", "term": "x", "text": "Hello",
                                            "cites": []}]})
    item = notes.lecture[0]
    assert (item.kind, item.heading, item.term) == ("point", None, None)  # a term only belongs to a definition


def test_old_notes_without_lecture_still_load():
    old = {"summary": [{"id": "S1", "text": "Gist", "cites": [1.0]}], "decisions": [], "action_items": [],
           "open_questions": [], "topics": [], "missed": []}
    notes = Notes.from_dict(old)
    assert notes.lecture == [] and notes.summary[0].heading is None


# --- writing them

def test_lecture_style_asks_for_lecture_notes():
    llm = FakeLLM(replies=[LECTURE_REPLY])
    notes = write_notes(llm, title="Machine Learning", paragraphs=[Paragraph(251.0, "Autonomous systems...")],
                        long_form=False, existing_topics=[], call=DIRECT, style="lecture")
    [call] = llm.calls
    assert call["system"] == LECTURE_SYSTEM and call["schema"] is LECTURE_SCHEMA
    assert "Lecture title: Machine Learning" in call["user"]
    assert len(notes.lecture) == 4 and notes.action_items[0].due == "Monday"


def test_meeting_style_is_unchanged():
    llm = FakeLLM(replies=[{"summary": [], "decisions": [], "action_items": [], "open_questions": [],
                            "topics": []}])
    write_notes(llm, title="Standup", paragraphs=[Paragraph(0.0, "Hi")], long_form=False, existing_topics=[],
                call=DIRECT, style="meeting")
    assert llm.calls[0]["system"] == WRITER_SYSTEM and llm.calls[0]["schema"] is WRITER_SCHEMA


def test_long_lecture_joins_topic_notes_in_order_and_merges_the_rest(monkeypatch):
    monkeypatch.setattr("transcriber.notes.writer.split_parts",
                        lambda paragraphs, max_chars: [paragraphs[:1], paragraphs[1:]])
    part1 = {**LECTURE_REPLY, "lecture": [lecture_item("Intro", "First idea.")]}
    part2 = {**LECTURE_REPLY, "lecture": [lecture_item("Intro", "Second idea."),
                                          lecture_item("Robots", "Third idea.")]}
    merged = {"summary": [{"text": "Whole lecture.", "cites": []}], "decisions": [], "action_items": [],
              "open_questions": [], "topics": ["robots"]}
    llm = FakeLLM(replies=[part1, part2, merged])
    notes = write_notes(llm, title="Long", paragraphs=[Paragraph(0.0, "a"), Paragraph(900.0, "b")],
                        long_form=False, existing_topics=[], call=DIRECT, style="lecture")
    assert [c["system"] for c in llm.calls] == [LECTURE_SYSTEM, LECTURE_SYSTEM, MERGE_SYSTEM]
    assert "part 1 of 2" in llm.calls[0]["user"]
    assert '"lecture"' not in llm.calls[2]["user"]  # topic notes aren't squeezed through the merge
    assert llm.calls[2]["effort"] == "low"
    assert [i.text for i in notes.lecture] == ["First idea.", "Second idea.", "Third idea."]
    assert [i.id for i in notes.lecture] == ["L1", "L2", "L3"]
    assert notes.summary[0].text == "Whole lecture."


def test_a_part_that_runs_out_of_room_is_retried_with_less_thinking():
    llm = FakeLLM(replies=[PermanentError("The notes model ran out of output space"), LECTURE_REPLY])
    notes = write_notes(llm, title="t", paragraphs=[Paragraph(0.0, "a")], long_form=False, existing_topics=[],
                        call=DIRECT, style="lecture")
    assert [c["effort"] for c in llm.calls] == [None, "low"]
    assert len(notes.lecture) == 4


# --- checking, editing, Ask proposals, export

def test_checker_sees_lecture_items_with_their_heading_and_term():
    notes = notes_from_writer(LECTURE_REPLY)
    llm = FakeLLM(replies=[{"verdicts": [{"id": "L2", "verdict": "partial", "reason": "Said differently."}],
                            "missed": []}])
    checked = check_notes(llm, notes=notes, transcript_text="[07:52] ...", long_form=False, call=DIRECT)
    assert "L2: [Autonomous systems] definition of Classical AI: Finds patterns" in llm.calls[0]["user"]
    assert checked.lecture[1].verdict == "partial"


def test_editing_a_lecture_item_keeps_its_place():
    notes = notes_from_writer(LECTURE_REPLY)
    edited = update_item(notes, "L2", text="Learns patterns from given data.")
    item = edited.lecture[1]
    assert (item.text, item.heading, item.kind, item.term, item.edited) == (
        "Learns patterns from given data.", "Autonomous systems", "definition", "Classical AI", True)


def test_a_new_lecture_point_goes_under_the_last_topic():
    notes = add_item(notes_from_writer(LECTURE_REPLY), "lecture", text="My own point.")
    item = notes.lecture[-1]
    assert (item.id, item.heading, item.kind) == ("L5", "Autonomous systems", "point")


def test_ask_proposals_can_change_lecture_notes():
    old = notes_from_writer(LECTURE_REPLY)
    proposal = {**LECTURE_REPLY, "decisions": [],
                "lecture": LECTURE_REPLY["lecture"][:2] + [lecture_item("Robots", "New point.")]}
    new = apply_proposal(old, proposal)
    assert [i.text for i in new.lecture][-1] == "New point." and new.lecture[-1].edited
    assert not new.lecture[0].edited


def test_markdown_export_groups_by_topic():
    md = notes_to_md(notes_from_writer(LECTURE_REPLY), long_form=False)
    assert "**Lecture notes**" in md
    assert "### Autonomous systems" in md
    assert "- *Classical AI*: Finds patterns in data it is given. [07:52]" in md
    assert "- Example: China's dark factories" in md
    assert "- ★ Know the difference" in md


# --- choosing the style

def test_style_comes_from_the_subject_unless_the_recording_sets_its_own(tmp_path):
    store = Store(tmp_path / "app.db")
    lecture_subject = store.create_subject("Machine Learning")
    society = store.create_subject("Debate Society")
    store.update_subject(society.id, notes_style="meeting")
    assert store.get_subject(lecture_subject.id).notes_style == "lecture"
    store.create_recording("a", "Class", lecture_subject.id, "en", "a.m4a")
    store.create_recording("b", "Session", society.id, "en", "b.m4a")
    store.create_recording("c", "Loose", None, "en", "c.m4a")
    assert [store.notes_style(r) for r in "abc"] == ["lecture", "meeting", "lecture"]
    store.update_recording("a", notes_style="meeting")
    assert store.notes_style("a") == "meeting"
    store.update_recording("a", notes_style=None)  # back to the subject's
    assert store.notes_style("a") == "lecture"
    store.close()


def test_migrates_v9_database(tmp_path):
    db = tmp_path / "app.db"
    store = Store(db)
    subject = store.create_subject("Old")
    store.create_recording("k1", "Kept", subject.id, "en", "k1.m4a")
    store._execute("PRAGMA user_version = 9")
    store._execute("ALTER TABLE recordings DROP COLUMN content_hash")
    undo_privacy_migration(store)
    store._execute("DROP TABLE reminders")
    store._execute("ALTER TABLE recordings DROP COLUMN notes_style")
    store._execute("ALTER TABLE subjects DROP COLUMN notes_style")
    store._execute("DROP TABLE recaps")
    store._execute("ALTER TABLE recordings DROP COLUMN person_id")
    store._execute("DROP TABLE people")
    store._execute("ALTER TABLE subjects DROP COLUMN schedule_json")
    store._execute("DROP TABLE notifications")
    store.close()
    store = Store(db)
    assert store.notes_style("k1") == "lecture"
    store.close()


def test_headings_that_differ_only_in_case_become_one_topic():
    notes = notes_from_writer({"lecture": [lecture_item("classical ai", "One."), lecture_item("classical AI", "Two."),
                                           lecture_item("gen AI", "Three.")]})
    assert [i.heading for i in notes.lecture] == ["Classical ai", "Classical ai", "Gen AI"]
