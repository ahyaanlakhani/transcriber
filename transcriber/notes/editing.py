"""Change AI notes by hand or by applying a proposal from the Ask chat (pure logic; inputs are not mutated)."""
from dataclasses import replace

from .model import ID_PREFIX, SECTIONS, NoteItem, Notes, notes_from_writer


def _copy(notes: Notes) -> Notes:
    return Notes.from_dict(notes.to_dict())


def _locate(notes: Notes, item_id: str) -> tuple[list[NoteItem], int]:
    for section in SECTIONS:
        items = getattr(notes, section)
        for index, item in enumerate(items):
            if item.id == item_id:
                return items, index
    raise KeyError(item_id)


def _clean(value: str | None) -> str | None:
    text = " ".join((value or "").split())
    return text or None


def update_item(notes: Notes, item_id: str, *, text: str, owner: str | None = None,
                due: str | None = None) -> Notes:
    if not _clean(text):
        raise ValueError("A note cannot be empty")
    notes = _copy(notes)
    items, index = _locate(notes, item_id)
    is_action = item_id.startswith(ID_PREFIX["action_items"])
    items[index] = replace(items[index], text=_clean(text), owner=_clean(owner) if is_action else None,
                           due=_clean(due) if is_action else None, edited=True, verdict=None, reason=None)
    return notes


def delete_item(notes: Notes, item_id: str) -> Notes:
    notes = _copy(notes)
    items, index = _locate(notes, item_id)
    del items[index]
    return notes


def add_item(notes: Notes, section: str, *, text: str, owner: str | None = None, due: str | None = None) -> Notes:
    if section not in SECTIONS:
        raise ValueError(f"Unknown section '{section}'")
    if not _clean(text):
        raise ValueError("A note cannot be empty")
    notes = _copy(notes)
    items = getattr(notes, section)
    prefix = ID_PREFIX[section]
    numbers = [int(item.id[len(prefix):]) for item in items if item.id[len(prefix):].isdigit()]
    is_action = section == "action_items"
    lecture = section == "lecture"
    items.append(NoteItem(f"{prefix}{max(numbers, default=0) + 1}", _clean(text), [],
                          owner=_clean(owner) if is_action else None, due=_clean(due) if is_action else None,
                          edited=True, heading=items[-1].heading if lecture and items else None,
                          kind="point" if lecture else None))
    return notes


def apply_proposal(old: Notes, proposal: dict) -> Notes:
    """Replace the notes with a proposal; unchanged items keep their checker verdicts."""
    new = notes_from_writer({**proposal, "topics": []})
    for section in SECTIONS:
        previous = {_key(item): item for item in getattr(old, section)}
        for item in getattr(new, section):
            match = previous.get(_key(item))
            if match is not None:
                item.verdict, item.reason, item.edited = match.verdict, match.reason, match.edited
            else:
                item.edited = True
    new.topics, new.missed = list(old.topics), list(old.missed)
    return new


def _key(item: NoteItem) -> tuple:
    return (" ".join(item.text.lower().split()), (item.owner or "").lower(), (item.due or "").lower(),
            (item.heading or "").lower(), item.kind or "", (item.term or "").lower())
