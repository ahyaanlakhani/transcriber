"""Learn: flashcards made from your own notes, asked back on a spaced schedule, and teach-back, where you explain an idea
in your own words and are checked against what your lectures actually said.

The cards come from what your notes already mark as definitions and as stressed by the lecturer, so making them needs no
AI model and costs nothing. Each card links to the recording and moment it came from."""
import hashlib
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import fsrs
from .briefing import visible_recordings

NEW_PER_DAY = 10
TEACH_SYSTEM = """You check a student's explanation of one concept against what their own lectures said about it. You get \
the student's explanation and numbered facts from their notes. The notes are the only source of truth: do not add facts \
from anywhere else. List what the student got right, what the notes say that they left out, and anything that \
contradicts the notes. Then give one or two kind, specific sentences of feedback. If the notes don't cover something \
the student said, say it isn't in the notes rather than calling it wrong. Be brief."""
TEACH_SCHEMA = {"type": "object", "properties": {
    "right": {"type": "array", "items": {"type": "string"}}, "missing": {"type": "array", "items": {"type": "string"}},
    "wrong": {"type": "array", "items": {"type": "string"}}, "feedback": {"type": "string"}},
    "required": ["right", "missing", "wrong", "feedback"], "additionalProperties": False}
MAX_EXPLANATION = 2500
STRESSED_FRONT = "What did your lecturer stress about this?"


def card_key(recording_id: str, kind: str, text: str) -> str:
    return hashlib.sha1(f"{recording_id}|{kind}|{' '.join(text.lower().split())}".encode()).hexdigest()[:16]


def card_for(item) -> tuple[str, str, str] | None:
    """(kind, front, back) for a note item worth a card, or None."""
    text = " ".join((item.text or "").split())
    if not text:
        return None
    if item.kind == "definition" and item.term:
        return "definition", f"What is {item.term.strip()}?", text
    if item.kind == "stressed":
        topic = (item.heading or "").strip()
        return "stressed", f"{topic}: {STRESSED_FRONT}" if topic else STRESSED_FRONT, text
    return None


def sync_cards(store, tz: str) -> dict:
    """Make cards for lecture notes that don't have them yet; drop unanswered cards whose note changed."""
    keys, added = set(), 0
    for rec in visible_recordings(store, tz):
        record = store.get_notes(rec.id)
        if not record or record.status != "ready" or record.notes is None or store.notes_style(rec.id) != "lecture":
            continue
        for item in record.notes.lecture:
            made = card_for(item)
            if made is None:
                continue
            kind, front, back = made
            key = card_key(rec.id, kind, back)
            keys.add(key)
            if store.add_card(key, rec.id, item.id, kind, front, back, item.cites[0] if item.cites else None):
                added += 1
    return {"added": added, "removed": store.drop_unreviewed_cards_except(keys)}


def start_of_day(now: datetime, tz: str) -> datetime:
    local = now.astimezone(ZoneInfo(tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def next_card(store, tz: str, now: datetime | None = None, new_per_day: int = NEW_PER_DAY) -> dict | None:
    """The card to ask now, with what each answer would mean, or None when you're done for today."""
    now = now or datetime.now(UTC)
    allowance = new_per_day - store.new_started_since(start_of_day(now, tz))
    queue = store.due_cards(now, allowance)
    if not queue:
        return None
    card = queue[0]
    memory = store.card_memory(card)
    return {**card, "preview": fsrs.preview(memory, now), "left": len(queue)}


def answer(store, card_id: int, rating: int, now: datetime | None = None, retention: float = fsrs.DEFAULT_RETENTION) -> dict:
    now = now or datetime.now(UTC)
    card = store.get_card(card_id)
    if card is None:
        raise KeyError(card_id)
    updated = fsrs.review(store.card_memory(card), rating, now, retention)
    store.save_review(card_id, updated, rating, now)
    return {"due": updated.due.isoformat(timespec="seconds"), "stability": round(updated.stability, 2)}


def summary(store, tz: str, now: datetime | None = None, new_per_day: int = NEW_PER_DAY) -> dict:
    now = now or datetime.now(UTC)
    counts = store.card_counts(now)
    allowance = max(new_per_day - store.new_started_since(start_of_day(now, tz)), 0)
    streak_cut = now - timedelta(days=7)
    return {**counts, "new_today": min(counts["new"], allowance), "reviewed_this_week": len(store.reviews_since(streak_cut)),
            "to_do": counts["due"] + min(counts["new"], allowance)}


def teach_back(llm, name: str, facts: list[str], explanation: str) -> dict:
    """One model call. The caller sets the privacy scope: it must name every recording the facts came from."""
    explanation = " ".join(explanation.split())[:MAX_EXPLANATION]
    if not explanation:
        raise ValueError("Explain the idea first")
    user = (f"CONCEPT: {name}\n\nWHAT THE STUDENT'S NOTES SAY:\n" + "\n".join(f"{n}. {t}" for n, t in enumerate(facts, 1))
            + f"\n\nTHE STUDENT'S EXPLANATION:\n{explanation}")
    raw = llm.complete_json(system=TEACH_SYSTEM, user=user, schema=TEACH_SCHEMA, schema_name="teach_back", effort="low")
    clean = lambda key: [" ".join(str(x).split()) for x in (raw.get(key) or []) if str(x).strip()][:8]  # noqa: E731
    return {"right": clean("right"), "missing": clean("missing"), "wrong": clean("wrong"),
            "feedback": " ".join(str(raw.get("feedback") or "").split())}
