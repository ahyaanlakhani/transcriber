"""Flashcards and your reviews of them. A card always says which recording and moment it came from."""
from datetime import UTC, datetime

from .fsrs import Memory


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat(timespec="seconds") if moment else None


def _parse(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


class LearnStoreMixin:
    def add_card(self, key: str, recording_id: str, item_id: str, kind: str, front: str, back: str,
                 start: float | None) -> bool:
        """A new card, unless one with the same key exists. True when it was added."""
        return self._execute(
            "INSERT OR IGNORE INTO cards (key, recording_id, item_id, kind, front, back, start, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key, recording_id, item_id, kind, front, back, start, _iso(datetime.now(UTC)))) > 0

    def drop_unreviewed_cards_except(self, keys: set[str]) -> int:
        """Cards whose note was reworded or removed, if you never answered them. Answered cards are kept."""
        stale = [r["id"] for r in self._query("SELECT id, key FROM cards WHERE reps = 0") if r["key"] not in keys]
        for card_id in stale:
            self._execute("DELETE FROM cards WHERE id = ?", (card_id,))
        return len(stale)

    def get_card(self, card_id: int) -> dict | None:
        rows = self._query(self._CARD_SELECT + " WHERE c.id = ?", (card_id,))
        return dict(rows[0]) if rows else None

    _CARD_SELECT = ("SELECT c.*, r.title AS recording, COALESCE(r.recorded_at, r.created_at) AS recorded"
                    " FROM cards c JOIN recordings r ON r.id = c.recording_id AND r.deleted_at IS NULL")

    def card_memory(self, card: dict) -> Memory:
        return Memory(card["stability"], card["difficulty"], _parse(card["due"]), _parse(card["last_review"]),
                      card["reps"], card["lapses"])

    def due_cards(self, now: datetime, new_allowed: int) -> list[dict]:
        """Cards due now (oldest first), then up to `new_allowed` cards you haven't seen."""
        stamp = _iso(now)
        due = [dict(r) for r in self._query(
            self._CARD_SELECT + " WHERE c.suspended = 0 AND c.reps > 0 AND c.due <= ? ORDER BY c.due", (stamp,))]
        fresh = [dict(r) for r in self._query(
            self._CARD_SELECT + " WHERE c.suspended = 0 AND c.reps = 0 ORDER BY recorded DESC, c.id LIMIT ?",
            (max(new_allowed, 0),))]
        return due + fresh

    def new_started_since(self, since: datetime) -> int:
        """Cards first answered since then (a day's allowance of new cards is counted against this)."""
        return self._query("SELECT COUNT(*) AS n FROM cards WHERE first_review >= ?", (_iso(since),))[0]["n"]

    def save_review(self, card_id: int, memory: Memory, rating: int, now: datetime) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE cards SET stability = ?, difficulty = ?, due = ?, last_review = ?, reps = ?, lapses = ?,"
                " first_review = COALESCE(first_review, ?) WHERE id = ?",
                (memory.stability, memory.difficulty, _iso(memory.due), _iso(memory.last_review), memory.reps,
                 memory.lapses, _iso(now), card_id))
            conn.execute("INSERT INTO reviews (card_id, at, rating) VALUES (?, ?, ?)", (card_id, _iso(now), rating))

    def set_card_suspended(self, card_id: int, suspended: bool) -> bool:
        return self._execute("UPDATE cards SET suspended = ? WHERE id = ?", (int(suspended), card_id)) > 0

    def card_counts(self, now: datetime) -> dict:
        stamp = _iso(now)
        row = self._query(
            "SELECT COUNT(*) AS total,"
            " SUM(c.suspended = 0 AND c.reps > 0 AND c.due <= ?) AS due,"
            " SUM(c.suspended = 0 AND c.reps = 0) AS new,"
            " SUM(c.reps > 0) AS learned, MIN(CASE WHEN c.suspended = 0 AND c.reps > 0 AND c.due > ? THEN c.due END) AS next_due"
            " FROM cards c JOIN recordings r ON r.id = c.recording_id AND r.deleted_at IS NULL", (stamp, stamp))[0]
        return {"total": row["total"] or 0, "due": row["due"] or 0, "new": row["new"] or 0, "learned": row["learned"] or 0,
                "next_due": row["next_due"]}

    def reviews_since(self, since: datetime) -> list[dict]:
        return [dict(r) for r in self._query("SELECT * FROM reviews WHERE at >= ? ORDER BY at", (_iso(since),))]
