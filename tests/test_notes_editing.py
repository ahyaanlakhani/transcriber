import pytest

from transcriber.notes.editing import add_item, apply_proposal, delete_item, update_item
from transcriber.notes.model import NoteItem, Notes


def make_notes():
    return Notes(
        summary=[NoteItem("S1", "Planned the launch.", [0.0], verdict="supported", reason="ok"),
                 NoteItem("S2", "Talked about hiring.", [30.0], verdict="partial", reason="only briefly")],
        action_items=[NoteItem("A1", "Send the report.", [60.0], owner="Sara", due="Friday", verdict="supported")],
        topics=["launch"], missed=[NoteItem("M1", "Budget was approved.", [90.0])])


def test_update_item_marks_edited_and_unchecked_without_mutating():
    notes = make_notes()
    edited = update_item(notes, "A1", text=" Send the final report. ", owner="Jamie", due="")
    item = edited.action_items[0]
    assert (item.text, item.owner, item.due, item.edited, item.verdict, item.reason) == (
        "Send the final report.", "Jamie", None, True, None, None)
    assert item.cites == [60.0]
    assert notes.action_items[0].text == "Send the report."


def test_update_unknown_item_raises():
    with pytest.raises(KeyError):
        update_item(make_notes(), "D9", text="x")


def test_update_rejects_empty_text():
    with pytest.raises(ValueError):
        update_item(make_notes(), "S1", text="  ")


def test_delete_item():
    notes = delete_item(make_notes(), "S1")
    assert [i.id for i in notes.summary] == ["S2"]
    with pytest.raises(KeyError):
        delete_item(notes, "S1")


def test_add_item_gets_next_id():
    notes = add_item(make_notes(), "summary", text="Agreed on November.")
    assert [(i.id, i.edited) for i in notes.summary] == [("S1", False), ("S2", False), ("S3", True)]
    notes = add_item(Notes(), "decisions", text="Delay launch.", owner="ignored")
    assert notes.decisions == [NoteItem("D1", "Delay launch.", [], edited=True)]
    notes = add_item(Notes(), "action_items", text="Book room.", owner="Sam", due="Monday")
    assert (notes.action_items[0].owner, notes.action_items[0].due) == ("Sam", "Monday")
    with pytest.raises(ValueError):
        add_item(Notes(), "missed", text="x")


def test_apply_proposal_keeps_checks_on_unchanged_items():
    old = make_notes()
    proposal = {
        "summary": [{"text": "Planned the launch.", "cites": ["[00:00]"]},
                    {"text": "Agreed to launch in November.", "cites": ["[00:45]"]}],
        "decisions": [],
        "action_items": [{"text": "Send the report.", "owner": "Sara", "due": "Friday", "cites": ["[01:00]"]}],
        "open_questions": [],
        "topics": ["something else"],
    }
    new = apply_proposal(old, proposal)
    assert [(i.text, i.verdict, i.edited) for i in new.summary] == [
        ("Planned the launch.", "supported", False), ("Agreed to launch in November.", None, True)]
    assert (new.action_items[0].verdict, new.action_items[0].edited) == ("supported", False)
    assert new.topics == ["launch"] and new.missed == old.missed


def test_apply_proposal_with_changed_owner_counts_as_edit():
    proposal = {"summary": [], "decisions": [], "open_questions": [], "topics": [],
                "action_items": [{"text": "Send the report.", "owner": "Jamie", "due": "Friday", "cites": []}]}
    item = apply_proposal(make_notes(), proposal).action_items[0]
    assert (item.owner, item.edited, item.verdict) == ("Jamie", True, None)
