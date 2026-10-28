"""Before you meet someone again: a short brief from your past coffee chats with them."""
from ..llm.base import LLMClient
from .model import Notes
from .writer import Call

BRIEF_SYSTEM = """You prepare the user for their next conversation with someone they have talked with before. You \
are given the user's past coffee chats with this person: what the user learned about them, what was promised, \
the questions the user already asked, and the coaching points the user got.

Write a short brief:
- who: one or two sentences on who this person is and what they care about.
- last_time: one or two sentences on when they last talked and what about.
- promises: what the user said they would do (send a CV, read a book...), so they can follow through or mention it. \
Empty if nothing was promised.
- questions: 3 to 5 good open questions for the next chat that build on what they already discussed, go deeper \
than before and do not repeat questions already asked.
- reminder: one sentence from the user's own coaching to keep in mind (for example, "Ask open questions and let \
them talk").
Use only what you are given. Plain, warm English."""

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {"who": {"type": "string"}, "last_time": {"type": "string"},
                   "promises": {"type": "array", "items": {"type": "string"}},
                   "questions": {"type": "array", "items": {"type": "string"}}, "reminder": {"type": "string"}},
    "required": ["who", "last_time", "promises", "questions", "reminder"],
    "additionalProperties": False,
}


def _chat_lines(title: str, when: str, notes: Notes) -> list[str]:
    lines = [f"Chat: {title} ({when})"]
    for label, items in (("Learned about them", notes.about_them), ("What you learned", notes.summary),
                         ("Promised", notes.action_items), ("You asked", notes.my_questions),
                         ("Coaching", notes.feedback)):
        if items:
            lines.append(f"{label}:")
            lines += [f"- {item.text}" + (f" (owner {item.owner})" if item.owner else "") for item in items]
    return lines


def write_brief(llm: LLMClient, *, name: str, note: str, chats: list[tuple[str, str, Notes]], call: Call) -> dict:
    """chats: (title, when, notes) for each past chat, oldest first."""
    lines = [f"Person: {name}"] + ([f"Your note about them: {note}"] if note.strip() else [])
    for title, when, notes in chats:
        lines += ["", *_chat_lines(title, when, notes)]
    raw = call(lambda: llm.complete_json(system=BRIEF_SYSTEM, user="\n".join(lines), schema=BRIEF_SCHEMA,
                                         schema_name="pre_chat_brief", effort="low"))
    return {key: raw.get(key) or ([] if key in ("promises", "questions") else "") for key in BRIEF_SCHEMA["required"]}
