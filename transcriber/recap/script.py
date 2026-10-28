"""The recap script: the notes rewritten as a 5-minute spoken briefing (one request to the notes model)."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..llm.base import LLMClient
from ..notes.model import Notes
from ..notes.writer import Call
from ..providers.base import PermanentError

MIN_WORDS = 80
RECAP_SYSTEM = """You turn a lecture's or meeting's notes into a spoken recap of about 5 minutes (650 to 750 \
words) that a student listens to on the way to the next class. It is read aloud by a text-to-speech voice.

Rules:
- Use only the notes you are given. Never add facts, examples or advice of your own.
- Open with what and when, e.g. "Your last Machine Learning class, on Wednesday morning, covered..."
- Walk through the main ideas in the order they were taught, in plain spoken sentences. Explain definitions \
in a sentence each. Mention the examples that make an idea stick.
- Then what to do before next time ("Before next class you need to..."), only if there is something.
- For a conversation such as a coffee chat, cover what the user learned about the person, then the coaching \
points (what to keep, what to work on, the follow-ups they could have asked), then the promised next steps.
- Then the open questions listed in the notes, and anything the lecturer stressed or said will be on the exam.
- Skip any part the notes don't cover. Never make up questions to think about, tips or conclusions that are not in the notes.
- Close with one takeaway sentence.
- Write for the ear: no lists, headings, bullets, symbols, timestamps or URLs. Short paragraphs separated by \
blank lines. Say numbers, dates and abbreviations the way a person would ("the twenty-first", "A I").
- English only. If the notes contain Roman Urdu, give the meaning in English.
- If the user gives instructions (what to call them, what to focus on), follow them within these rules."""
RECAP_SCHEMA = {"type": "object", "properties": {"script": {"type": "string"}}, "required": ["script"],
                "additionalProperties": False}
_SECTIONS = (("summary", "In short"), ("decisions", "Decisions"), ("action_items", "To do"),
             ("open_questions", "Open questions"))


def _usable(items):
    return [item for item in items if item.verdict != "unsupported"]


def notes_for_recap(notes: Notes) -> str:
    lines = []
    for key, title in _SECTIONS[:1]:
        lines += [f"{title}:", *(f"- {i.text}" for i in _usable(getattr(notes, key)))]
    if notes.lecture:
        lines.append("Lecture notes, in the order taught:")
        for item in _usable(notes.lecture):
            what = f"definition of {item.term}" if item.kind == "definition" and item.term else item.kind or "point"
            lines.append(f"- {item.heading or 'General'} — {what}: {item.text}")
    for key, title in _SECTIONS[1:]:
        items = _usable(getattr(notes, key))
        if items:
            lines.append(f"{title}:")
            for item in items:
                extra = "".join(f" ({label} {value})" for label, value in (("owner", item.owner), ("due", item.due))
                                if value)
                lines.append(f"- {item.text}{extra}")
    if notes.about_them:
        lines += ["About them:", *(f"- {i.text}" for i in _usable(notes.about_them))]
    if notes.feedback:
        tags = {"strength": "Keep", "improve": "Work on"}
        lines += ["Coaching:", *(f"- {tags.get(i.kind, 'Note')}: {i.text}" for i in _usable(notes.feedback))]
    if notes.missed_followups:
        lines += ["Missed follow-ups:", *(f"- {i.text} You could have asked: {i.suggestion}" if i.suggestion
                                          else f"- {i.text}" for i in _usable(notes.missed_followups))]
    if notes.missed:
        lines += ["Also said (the notes missed these):", *(f"- {i.text}" for i in notes.missed)]
    return "\n".join(lines)


def recap_user(*, title: str, subject: str | None, when: str, notes: Notes, instructions: str) -> str:
    head = [f"Title: {title}", f"Subject: {subject or '(none)'}", f"When: {when}"]
    if instructions.strip():
        head.append(f"Instructions from the user: {instructions.strip()}")
    return "\n".join(head) + "\n\nNOTES:\n" + notes_for_recap(notes)


def write_script(llm: LLMClient, *, title: str, subject: str | None, when: str, notes: Notes,
                 instructions: str, call: Call) -> str:
    user = recap_user(title=title, subject=subject, when=when, notes=notes, instructions=instructions)
    for _ in range(2):
        raw = call(lambda: llm.complete_json(system=RECAP_SYSTEM, user=user, schema=RECAP_SCHEMA,
                                             schema_name="recap_script", effort="low"))
        script = str(raw.get("script") or "").strip()
        if len(script.split()) >= MIN_WORDS:
            return script
    raise PermanentError("The AI didn't write a usable script")


_STRIP = [(re.compile(r"\[\d{1,2}(?::\d{2}){1,2}\]"), ""), (re.compile(r"https?://\S+"), ""),
          (re.compile(r"^\s*(?:#+|[-*•]|\d+[.)])\s+", re.M), ""), (re.compile(r"[*_`]+"), "")]


def spoken_paragraphs(script: str) -> list[str]:
    text = script
    for pattern, replacement in _STRIP:
        text = pattern.sub(replacement, text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return [line for line in lines if line]


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def spoken_when(iso: str, tz: str | None = None) -> str:
    moment = datetime.fromisoformat(iso).astimezone(ZoneInfo(tz) if tz else None)
    part = "morning" if moment.hour < 12 else "afternoon" if moment.hour < 17 else "evening"
    return f"{moment:%A} {part}, the {_ordinal(moment.day)} of {moment:%B}"
