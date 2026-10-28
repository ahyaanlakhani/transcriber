"""Ask a second model to check each note item against the transcript. It never edits the notes."""
from ..llm.base import LLMClient
from .model import CHECKER_SCHEMA, VERDICTS, Notes, missed_from_checker
from .prompts import CHECKER_SYSTEM, checker_user
from .writer import Call


def check_notes(llm: LLMClient, *, notes: Notes, transcript_text: str, long_form: bool, call: Call,
                instructions: str = "", part_count: int = 0) -> Notes:
    raw = call(lambda: llm.complete_json(system=CHECKER_SYSTEM,
                                         user=checker_user(transcript_text, notes, long_form, instructions,
                                                           part_count),
                                         schema=CHECKER_SCHEMA, schema_name="notes_check"))
    checked = Notes.from_dict(notes.to_dict())
    by_id = {item.id: item for item in checked.items()}
    for verdict in raw.get("verdicts") or []:
        if not isinstance(verdict, dict):
            continue
        item = by_id.get(str(verdict.get("id", "")).strip())
        if item is not None and verdict.get("verdict") in VERDICTS:
            item.verdict = verdict["verdict"]
            item.reason = " ".join(str(verdict.get("reason") or "").split()) or None
    checked.missed = missed_from_checker(raw.get("missed"))
    return checked
