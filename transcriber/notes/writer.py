"""Ask the writer model for notes: one call, or one call per part plus a merge."""
from collections.abc import Callable

from ..llm.base import LLMClient
from ..postprocess import Paragraph
from ..providers.base import PermanentError
from .model import LECTURE_SCHEMA, WRITER_SCHEMA, Notes, notes_from_writer
from .prompts import LECTURE_SYSTEM, MERGE_SYSTEM, WRITER_SYSTEM, merge_user, writer_user
from .transcript import PART_MAX_CHARS, render, split_parts

Call = Callable[[Callable[[], dict]], dict]
OUT_OF_ROOM = "ran out of output space"
TOO_LARGE = "HTTP 413"
SMALLEST_PART = 2_000  # characters; below this, shrinking the request further would not help


def write_notes(llm: LLMClient, *, title: str, paragraphs: list[Paragraph], long_form: bool,
                existing_topics: list[str], call: Call, instructions: str = "",
                part_starts: list[float] | None = None, style: str = "meeting") -> Notes:
    parts = split_parts(paragraphs, getattr(llm, "max_input_chars", None) or PART_MAX_CHARS)
    if not parts:
        return Notes()
    part_count = len(part_starts or [])
    lecture = style == "lecture"
    system, schema = (LECTURE_SYSTEM, LECTURE_SCHEMA) if lecture else (WRITER_SYSTEM, WRITER_SCHEMA)

    def ask(system: str, user: str, schema: dict, effort: str | None = None) -> dict:
        return call(lambda: llm.complete_json(system=system, user=user, schema=schema,
                                              schema_name="meeting_notes", effort=effort))

    def write(part: list[Paragraph], position: tuple[int, int] | None) -> dict:
        user = writer_user(title, render(part, long_form, part_starts), existing_topics, part=position,
                           instructions=instructions, part_count=part_count, style=style)
        try:
            return ask(system, user, schema)
        except PermanentError as exc:
            # Thorough lecture notes plus gpt-oss's thinking can fill the output cap; think less, keep the notes.
            if OUT_OF_ROOM not in str(exc):
                raise
            return ask(system, user, schema, effort="low")

    def write_part(part: list[Paragraph], position: tuple[int, int] | None) -> list[dict]:
        """Parts are sized in characters, which assumes English. Text in Roman letters for a non-English language takes about twice the tokens for the same
        characters, so a model with a small request limit (Groq answers HTTP 413) can refuse a part. Only that part is
        split in two and written again; the parts already written are kept, so nothing is paid for twice."""
        try:
            return [write(part, position)]
        except PermanentError as exc:
            if TOO_LARGE not in str(exc) or len(part) < 2 or sum(len(p.text) for p in part) // 2 < SMALLEST_PART:
                raise
            middle = len(part) // 2
            return write_part(part[:middle], position) + write_part(part[middle:], position)

    part_notes: list[dict] = []
    for n, part in enumerate(parts, 1):
        part_notes += write_part(part, (n, len(parts)) if len(parts) > 1 else None)
    if len(part_notes) == 1:
        return notes_from_writer(part_notes[0])
    # Lecture notes are joined in order, not merged: squeezing them through one reply would cut them short.
    lecture_items = [item for notes in part_notes for item in notes.pop("lecture", None) or []]
    # Combining is bookkeeping, not deep thinking. With the default effort, gpt-oss's thinking used up the
    # output cap on long lectures and Groq returned an empty or cut-off reply ("json_validate_failed").
    merged = ask(MERGE_SYSTEM, merge_user(part_notes), WRITER_SCHEMA, effort="low")
    if lecture:
        merged = {**merged, "decisions": [], "lecture": lecture_items}
    return notes_from_writer(merged)
