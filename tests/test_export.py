from transcriber.export import format_duration, format_timestamp, to_md, to_txt
from transcriber.notes.model import NoteItem, Notes
from transcriber.postprocess import Paragraph

PARAGRAPHS = [Paragraph(0.0, "Hello team."), Paragraph(75.4, "Next item.")]


def test_short_timestamp():
    assert format_timestamp(75.4, long_form=False) == "[01:15]"


def test_long_timestamp():
    assert format_timestamp(3725.0, long_form=True) == "[1:02:05]"


def test_format_duration():
    assert format_duration(45) == "45s"
    assert format_duration(725) == "12m 05s"
    assert format_duration(3725) == "1h 02m"


def test_txt_has_title_and_paragraphs_without_timestamps():
    assert to_txt("Standup", PARAGRAPHS) == "Standup\n\nHello team.\n\nNext item.\n"


def test_md_has_header_metadata_and_timestamps():
    assert to_md("Standup", "2026-10-03", "Team A", 125.0, PARAGRAPHS) == (
        "# Standup\n\n2026-10-03 · Team A · 2m 05s\n\n[00:00] Hello team.\n\n[01:15] Next item.\n"
    )


def test_md_without_subject_uses_long_timestamps_for_long_recordings():
    assert to_md("X", "2026-10-03", None, 3700.0, [Paragraph(3661, "Late.")]) == (
        "# X\n\n2026-10-03 · 1h 01m\n\n[1:01:01] Late.\n"
    )



def test_md_includes_ai_notes_when_present():
    notes = Notes(summary=[NoteItem("S1", "Gist.", [0.0])],
                  action_items=[NoteItem("A1", "Review PRs.", [75.0, 80.0], owner="Jordan", due="Wednesday")])
    assert to_md("Standup", "2026-10-03", None, 125.0, PARAGRAPHS, notes) == (
        "# Standup\n\n2026-10-03 · 2m 05s\n\n## Notes\n\n**Summary**\n\n- Gist. [00:00]\n\n"
        "**Action items**\n\n- Review PRs. — owner: Jordan — due: Wednesday [01:15] [01:20]\n\n"
        "## Transcript\n\n[00:00] Hello team.\n\n[01:15] Next item.\n"
    )
