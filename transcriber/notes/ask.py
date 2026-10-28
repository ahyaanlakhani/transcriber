"""Answer follow-up questions about one meeting, optionally proposing changes to its AI notes."""
import re
from dataclasses import dataclass

from ..llm.base import LLMClient
from ..postprocess import Paragraph
from ..providers.base import PermanentError, RateLimited, TransientError
from .model import FULL_SCHEMA, Notes
from .prompts import context_lines, format_items
from .transcript import render

HISTORY_MESSAGES = 10

ASK_SYSTEM = """You help the user with one meeting they recorded. You are given the meeting's transcript \
(each paragraph starts with its timestamp), the current AI notes (each item has an id), the user's own notes, \
and the conversation so far.

- Answer the user's latest message using only this material. Cite timestamps like [12:34] for facts from the \
transcript. If the material does not contain the answer, say so plainly.
- Keep answers short and direct unless the user asks for something longer, such as a follow-up email.
- When the user asks to change the AI notes (shorten, reword, add, remove or fix items), set proposed_notes to \
the complete updated notes: copy every unchanged item word for word with its cites, and apply only the \
requested changes. Say briefly in answer what you changed. Otherwise set proposed_notes to null."""

ASK_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"},
                   "proposed_notes": {"anyOf": [FULL_SCHEMA, {"type": "null"}]}},
    "required": ["answer", "proposed_notes"],
    "additionalProperties": False,
}
CITE = re.compile(r"[ \t]*\[(?:(\d+):)?(\d{1,2}):(\d{2})\]")
CITE_SLACK_SEC = 5.0


def drop_impossible_cites(answer: str, duration_sec: float | None) -> tuple[str, int]:
    """A model can cite a moment that was never recorded. Cut those timestamps (a bit of slack past the end is allowed)
    and say how many, so a citation that is shown always points at something that exists."""
    if not duration_sec:
        return answer, 0
    removed = 0

    def check(match: re.Match) -> str:
        nonlocal removed
        hours, minutes, seconds = int(match.group(1) or 0), int(match.group(2)), int(match.group(3))
        if seconds >= 60 or hours * 3600 + minutes * 60 + seconds > duration_sec + CITE_SLACK_SEC:
            removed += 1
            return ""
        return match.group(0)

    return CITE.sub(check, answer), removed


TOO_LONG = "The transcript was too long for this model, so the answer is based on the notes."


@dataclass
class AskResult:
    answer: str
    proposal: dict | None
    engine: str
    note: str | None  # why a fallback model answered, or what it could not see


def ask_user(*, title: str, transcript: str | None, notes: Notes, long_form: bool, my_notes: str,
             history: list[dict], question: str, context: str = "") -> str:
    transcript_part = transcript if transcript is not None else f"({TOO_LONG.split(',')[0]}; answer from the notes.)"
    past = "\n".join(f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
                     for m in history[-HISTORY_MESSAGES:]) or "(none)"
    return (f"Meeting title: {title}\n{context}\nTRANSCRIPT:\n{transcript_part}\n\nAI NOTES:\n{format_items(notes, long_form)}"
            f"\n\nMY NOTES:\n{my_notes.strip() or '(none)'}\n\nCONVERSATION SO FAR:\n{past}"
            f"\n\nLATEST MESSAGE:\n{question}")


def ask(engines: list[LLMClient], *, title: str, paragraphs: list[Paragraph], long_form: bool, notes: Notes,
        my_notes: str, history: list[dict], question: str, instructions: str = "",
        part_starts: list[float] | None = None, duration_sec: float | None = None) -> AskResult:
    """Try each engine in order (Claude first, then the free notes model) and return the first answer."""
    transcript = render(paragraphs, long_form, part_starts)
    context = context_lines(instructions, len(part_starts or []))
    problems: list[str] = []
    for llm in engines:
        limit = getattr(llm, "max_input_chars", None)
        fits = limit is None or len(transcript) <= limit
        user = ask_user(title=title, transcript=transcript if fits else None, notes=notes, long_form=long_form,
                        my_notes=my_notes, history=history, question=question, context=context)
        try:
            raw = _call(llm, user)
        except (PermanentError, TransientError, RateLimited) as exc:
            problems.append(f"{llm.name}: {exc}")
            continue
        proposal = raw.get("proposed_notes")
        answer, removed = drop_impossible_cites(str(raw.get("answer") or "").strip(), duration_sec)
        notes_used = [*problems, *([] if fits else [TOO_LONG]),
                      *([f"{removed} timestamp{'s' if removed != 1 else ''} the model cited aren't in this recording and "
                         "were removed"] if removed else [])]
        return AskResult(answer or "(no answer)", proposal if isinstance(proposal, dict) else None, llm.name,
                         "; ".join(notes_used) or None)
    raise PermanentError("No AI model could answer. " + ("; ".join(problems) or "None is configured."))


def _call(llm: LLMClient, user: str) -> dict:
    try:
        return llm.complete_json(system=ASK_SYSTEM, user=user, schema=ASK_SCHEMA, schema_name="meeting_answer")
    except TransientError:  # one quick retry; the user is waiting
        return llm.complete_json(system=ASK_SYSTEM, user=user, schema=ASK_SCHEMA, schema_name="meeting_answer")
