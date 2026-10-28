import pytest

from transcriber.notes.model import (NoteItem, Notes, missed_from_checker, normalize_topics, notes_from_writer,
                                     parse_timestamp)
from transcriber.notes.transcript import render, split_parts
from transcriber.postprocess import Paragraph


@pytest.mark.parametrize("text, seconds", [
    ("[00:16]", 16.0), ("12:34", 754.0), ("[1:02:03]", 3723.0), (" [10:00] ", 600.0),
    ("soon", None), ("[1:2]", None), ("", None),
])
def test_parse_timestamp(text, seconds):
    assert parse_timestamp(text) == seconds


def test_notes_from_writer_assigns_ids_parses_cites_and_caps():
    data = {
        "summary": [{"text": f"Point {i}", "cites": ["[00:10]"]} for i in range(7)],
        "decisions": [{"text": "Ship on Friday", "cites": ["[01:00]", "[00:30]", "[01:00]", "later"]}],
        "action_items": [{"text": "Review PRs", "owner": "Jordan", "due": "Wednesday", "cites": ["[02:00]"]},
                         {"text": "Write notes", "owner": "null", "due": None, "cites": []}],
        "open_questions": [{"text": "  ", "cites": []}],
        "topics": ["Search", "search ", "Database   Migration", "a", "b", "c", "d"],
    }
    notes = notes_from_writer(data)
    assert [i.id for i in notes.summary] == ["S1", "S2", "S3", "S4", "S5"]
    assert notes.decisions == [NoteItem("D1", "Ship on Friday", [30.0, 60.0])]
    assert (notes.action_items[0].owner, notes.action_items[0].due) == ("Jordan", "Wednesday")
    assert (notes.action_items[1].id, notes.action_items[1].owner, notes.action_items[1].due) == ("A2", None, None)
    assert notes.open_questions == []
    assert notes.topics == ["search", "database migration", "a", "b", "c"]


def test_notes_from_writer_tolerates_missing_sections():
    assert notes_from_writer({}) == Notes()


def test_missed_from_checker_caps_at_three():
    missed = missed_from_checker([{"text": f"m{i}", "cites": ["[00:01]"]} for i in range(5)] + ["junk"])
    assert [m.id for m in missed] == ["M1", "M2", "M3"]


def test_normalize_topics():
    assert normalize_topics(["Q4  Launch", "q4 launch", ""]) == ["q4 launch"]


def test_notes_round_trip_and_plain_text():
    notes = Notes(summary=[NoteItem("S1", "Gist", [1.0], verdict="supported", reason="ok")],
                  action_items=[NoteItem("A1", "Do it", [], owner="Sam")],
                  topics=["planning"], missed=[NoteItem("M1", "Forgot this", [5.0])])
    assert Notes.from_dict(notes.to_dict()) == notes
    assert notes.plain_text() == "Gist\nDo it\nForgot this\nplanning"
    assert [i.id for i in notes.items()] == ["S1", "A1"]


def test_render_marks_each_paragraph():
    paragraphs = [Paragraph(0.0, "Hello."), Paragraph(75.0, "Next.")]
    assert render(paragraphs, long_form=False) == "[00:00] Hello.\n\n[01:15] Next."
    assert render([Paragraph(3725.0, "Late.")], long_form=True) == "[1:02:05] Late."


def test_split_parts_on_paragraph_boundaries():
    paragraphs = [Paragraph(float(i), "x" * 40) for i in range(10)]  # 52 chars each with overhead
    parts = split_parts(paragraphs, max_chars=200)
    assert [len(p) for p in parts] == [3, 3, 3, 1]
    assert [p for part in parts for p in part] == paragraphs
    assert split_parts([]) == []


def test_render_marks_recording_parts():
    paragraphs = [Paragraph(0.0, "Intro."), Paragraph(5.0, "More."), Paragraph(63.0, "After the pause."),
                  Paragraph(130.0, "Last part.")]
    assert render(paragraphs, long_form=False, part_starts=[0.0, 63.0, 130.0]) == (
        "— Part 1 —\n\n[00:00] Intro.\n\n[00:05] More.\n\n— Part 2 —\n\n[01:03] After the pause.\n\n"
        "— Part 3 —\n\n[02:10] Last part.")
    # a chunk that starts mid-session only marks the parts that begin inside it
    assert render(paragraphs[2:], long_form=False, part_starts=[0.0, 63.0, 130.0]).startswith("— Part 2 —")
