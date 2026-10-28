"""Render transcripts (and AI notes) as downloadable text (pure logic)."""
from .notes.model import SECTIONS, Notes
from .postprocess import Paragraph

SECTION_TITLES = {"summary": "Summary", "lecture": "Lecture notes", "about_them": "About them",
                  "intro": "Your intro", "my_questions": "Your questions", "missed_followups": "Missed follow-ups",
                  "feedback": "Coaching","decisions": "Decisions", "action_items": "Action items",
                  "open_questions": "Open questions"}


def format_timestamp(seconds: float, long_form: bool) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"[{hours}:{minutes:02d}:{secs:02d}]" if long_form else f"[{minutes:02d}:{secs:02d}]"


def format_duration(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def to_txt(title: str, paragraphs: list[Paragraph]) -> str:
    body = "\n\n".join(f"{p.speaker}: {p.text}" if p.speaker else p.text for p in paragraphs)
    return f"{title}\n\n{body}\n"


def lecture_text(item) -> str:
    """One lecture item as a line of study notes."""
    if item.kind == "definition" and item.term:
        return f"*{item.term}*: {item.text}"
    prefix = {"example": "Example: ", "stressed": "★ "}.get(item.kind or "", "")
    return f"{prefix}{item.text}"


def notes_to_md(notes: Notes, long_form: bool) -> str:
    lines = ["## Notes", ""]
    if notes.attendees:
        lines += [f"**Attendees:** {', '.join(notes.attendees)}", ""]
    for section in SECTIONS:
        items = getattr(notes, section)
        if not items:
            continue
        lines += [f"**{SECTION_TITLES[section]}**", ""]
        heading = None
        for item in items:
            if section == "lecture":
                if item.heading and item.heading != heading:
                    heading = item.heading
                    lines += ["", f"### {heading}", ""]
                cites = " ".join(format_timestamp(c, long_form) for c in item.cites)
                lines.append(f"- {lecture_text(item)}" + (f" {cites}" if cites else ""))
                continue
            extra = "".join(f" — {label}: {value}" for label, value in (("owner", item.owner), ("due", item.due))
                            if value)
            cites = " ".join(format_timestamp(c, long_form) for c in item.cites)
            tag = {"open": "(open) ", "closed": "(closed) ", "strength": "Keep: ", "improve": "Work on: "}.get(
                item.kind or "", "") if section in ("my_questions", "feedback") else ""
            lines.append(f"- {tag}{item.text}{extra}" + (f" {cites}" if cites else ""))
            if item.suggestion:
                lead = {"intro": "Try", "my_questions": "Sharper", "missed_followups": "Could ask"}[section]
                lines.append(f"  - {lead}: {item.suggestion}")
        lines.append("")
    if notes.follow_up_draft:
        lines += ["**Follow-up message**", "", notes.follow_up_draft, ""]
    return "\n".join(lines)


def to_md(title: str, date: str, subject: str | None, duration_sec: float, paragraphs: list[Paragraph],
          notes: Notes | None = None) -> str:
    long_form = duration_sec >= 3600
    meta = " · ".join(part for part in (date, subject, format_duration(duration_sec)) if part)
    lines = [f"# {title}", "", meta, ""]
    if notes is not None and notes.items():
        lines += [notes_to_md(notes, long_form), "## Transcript", ""]
    for paragraph in paragraphs:
        who = f"**{paragraph.speaker}:** " if paragraph.speaker else ""
        lines += [f"{format_timestamp(paragraph.start, long_form)} {who}{paragraph.text}", ""]
    return "\n".join(lines)
