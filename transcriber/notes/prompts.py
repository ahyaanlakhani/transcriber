"""Instructions for the notes writer and checker."""
import json

from ..export import format_timestamp
from .model import Notes

WRITER_SYSTEM = """You write concise, faithful notes of a meeting from its transcript.

Rules:
- Use only what is said in the transcript. Never guess names, dates, numbers or owners.
- Every item cites the timestamp(s) of the transcript paragraph(s) it comes from, copied exactly as \
they appear, for example "[12:34]".
- summary: 3 to 5 short bullets with the gist of the meeting.
- decisions: things that were agreed or decided.
- action_items: concrete tasks. owner is the person responsible if named, otherwise null. due is the \
deadline if one was said, otherwise null.
- open_questions: things raised but not resolved.
- attendees: the people who took part, as names. Use a name only when the transcript gives one. "You" and \
"Others" are labels for who was speaking, not names, so leave them out. An empty list is fine.
- follow_up_draft: a short message (under 150 words) the user could send after the meeting: thank people, \
recap the decisions, and list the action items with owners and deadlines. Plain text. Use an empty string \
when there is nothing to follow up.
- Leave a section as an empty list when it has nothing. Do not pad.
- One sentence per item. Plain English.
- topics: 1 to 5 short lowercase labels (1-3 words) for what the meeting covered. Reuse an existing \
topic when one fits.
- Lines may start with a speaker's name ("Dr. Khan: ..."). Use those names for owners when the speaker \
clearly takes on the task. Generic labels like "Speaker 2" are fine as owners when no name is known.
- If the user gives instructions (who is speaking, what to focus on, how to phrase things), follow them \
within these rules and sections. Never invent content to satisfy them."""

LECTURE_SYSTEM = """You write study notes of a lecture from its transcript, for a student who will revise \
from them before an exam. They must let the student relearn the lecture without listening to it again.

Rules:
- Use only what is said in the transcript. Never add textbook knowledge, and never guess names, dates, \
numbers or formulas.
- Every item cites the timestamp(s) of the transcript paragraph(s) it comes from, copied exactly as \
they appear, for example "[12:34]".
- summary: 3 to 5 short bullets with the gist of the lecture.
- lecture: the main notes, in the order things were taught. Group them under short topic headings \
(2-6 words, e.g. "Autonomous systems"); every item in the same topic uses exactly the same heading. Be \
thorough: cover every idea the lecturer explains, roughly 4 to 10 items per topic. Each item has a kind:
  - point: an idea explained in 1-3 clear sentences, keeping the lecturer's reasoning (why and how), not just \
the claim.
  - definition: a term the lecturer defines; put the term in "term" and its meaning in "text".
  - example: a case, story, company or example used to illustrate an idea; say what it illustrates.
  - fact: a number, date, statistic, name or formula, exactly as said.
  - stressed: something the lecturer repeats or flags as important ("this will come in the exam", \
"remember this"); say what was stressed.
  "term" is null unless the kind is definition.
- action_items: assignments, readings, quizzes, submissions and deadlines. owner is null unless a person or \
group is named. due is the deadline if one was said, otherwise null.
- open_questions: questions raised in the lecture and left open. If the lecturer answered a student's \
question, write the answer as a point instead.
- Leave a section as an empty list when it has nothing. Do not pad.
- Plain English. If the lecture switches into another language, write the notes in English.
- topics: 1 to 5 short lowercase labels (1-3 words) for what the lecture covered. Reuse an existing topic \
when one fits.
- If the user gives instructions (who is speaking, what to focus on, how to phrase things), follow them \
within these rules and sections. Never invent content to satisfy them."""

MERGE_SYSTEM = """You combine notes written for consecutive parts of one long meeting into one set of notes.

Rules:
- Use only the part notes you are given. Do not add anything new.
- Merge duplicates. Keep every cite, copied exactly.
- summary: 3 to 5 bullets for the whole meeting.
- Keep all distinct decisions and action items. Drop an open question if a later part resolved it.
- attendees: every distinct name from the parts. follow_up_draft: one message for the whole meeting.
- topics: at most 5, reusing the part topics."""

CHECKER_SYSTEM = """You fact-check meeting notes against the meeting's transcript. You never rewrite the notes.

For every note item, identified by its id, give a verdict:
- supported: the transcript clearly says this.
- partial: mostly right, but a detail (name, owner, date, number or scope) is wrong, missing or overstated.
- unsupported: the transcript does not say this.
Give a one-sentence reason that points to what the transcript says.

Then list up to 3 important points the notes missed (decisions, commitments, deadlines or risks; for a \
lecture also key ideas, definitions and anything the lecturer stressed), each \
with the timestamp(s) where it is said, copied exactly like "[12:34]". List none if nothing important \
is missing."""


def context_lines(instructions: str = "", part_count: int = 0) -> str:
    """User-supplied context, plus a note when the session was recorded in several parts."""
    lines = []
    if instructions.strip():
        lines.append(f"Instructions from the user: {instructions.strip()}")
    if part_count > 1:
        lines.append(f"This session was recorded in {part_count} parts: the recorder was paused between them "
                     "(marked '— Part N —'). A pause means the speaker stopped or the user was busy; nothing is "
                     "missing. Write one set of notes for the whole session.")
    return "".join(f"{line}\n" for line in lines)


def writer_user(title: str, transcript_text: str, existing_topics: list[str],
                part: tuple[int, int] | None = None, instructions: str = "", part_count: int = 0,
                style: str = "meeting") -> str:
    topics = ", ".join(existing_topics) if existing_topics else "(none yet)"
    kind = "lecture" if style == "lecture" else "meeting"
    scope = f"This is part {part[0]} of {part[1]} of a long {kind}.\n" if part else ""
    return (f"{kind.capitalize()} title: {title}\nExisting topics: {topics}\n{context_lines(instructions, part_count)}{scope}"
            f"\nTranscript:\n{transcript_text}")


def merge_user(part_notes: list[dict]) -> str:
    parts = "\n\n".join(f"Part {n} notes:\n{json.dumps(notes, ensure_ascii=False)}"
                        for n, notes in enumerate(part_notes, 1))
    return f"Combine these part notes into one set of meeting notes.\n\n{parts}"


def format_items(notes: Notes, long_form: bool) -> str:
    lines = []
    for item in notes.items():
        if item.kind:  # a lecture item: say where it sits and what it is
            what = f"definition of {item.term}" if item.kind == "definition" and item.term else item.kind
            cites = ", ".join(format_timestamp(c, long_form) for c in item.cites) or "none"
            lines.append(f"{item.id}: [{item.heading or 'General'}] {what}: {item.text} [cites: {cites}]")
            continue
        extra = "".join(f" (owner: {value})" if label == "owner" else f" (due: {value})"
                        for label, value in (("owner", item.owner), ("due", item.due)) if value)
        cites = ", ".join(format_timestamp(c, long_form) for c in item.cites) or "none"
        lines.append(f"{item.id}: {item.text}{extra} [cites: {cites}]")
    return "\n".join(lines) or "(no items)"


def checker_user(transcript_text: str, notes: Notes, long_form: bool, instructions: str = "",
                 part_count: int = 0) -> str:
    context = context_lines(instructions, part_count)
    return (f"{context + chr(10) if context else ''}TRANSCRIPT:\n{transcript_text}"
            f"\n\nNOTES TO CHECK:\n{format_items(notes, long_form)}")
