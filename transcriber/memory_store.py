"""Pip's memory: things you told Pip to remember, or approved when Pip suggested them. Each has a kind, where it came
from, and an expiry, so what Pip knows about you is always something you chose and can see, and it fades."""
from datetime import UTC, datetime, timedelta

KINDS = ("goal", "preference", "ongoing", "relationship")
KIND_DAYS = {"goal": 180, "preference": 180, "relationship": 180, "ongoing": 30}  # how long each stays useful
MAX_TEXT = 300
MAX_ACTIVE = 40  # what Pip is shown at most, newest first


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def memory_key(text: str) -> str:
    return " ".join(text.lower().split())


class MemoryStoreMixin:
    def add_memory(self, kind: str, text: str, *, source_recording_id: str | None = None, status: str = "approved",
                   days: int | None = None, now: datetime | None = None) -> int:
        """Remember something. The same words again (while still current) just return the existing item."""
        text = " ".join(text.split())[:MAX_TEXT]
        if kind not in KINDS or not text:
            raise ValueError("Pip remembers a goal, a preference, something ongoing or a relationship, in a few words")
        if status not in ("approved", "proposed"):
            raise ValueError("A memory is approved or proposed")
        now = now or _now()
        for item in self.list_memory(now)["active"] + self.list_memory(now)["proposed"]:
            if memory_key(item["text"]) == memory_key(text):
                return item["id"]
        expires = _iso(now + timedelta(days=days or KIND_DAYS[kind])) if status == "approved" else None
        return self._insert(
            "INSERT INTO pip_memory (kind, text, status, source_recording_id, created_at, approved_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, text, status, source_recording_id, _iso(now), _iso(now) if status == "approved" else None, expires))

    def get_memory(self, memory_id: int) -> dict | None:
        rows = self._query("SELECT * FROM pip_memory WHERE id = ?", (memory_id,))
        return dict(rows[0]) if rows else None

    def list_memory(self, now: datetime | None = None) -> dict[str, list[dict]]:
        """{"active": approved and current, "proposed": waiting for you, "expired": approved but past their date}."""
        stamp = _iso(now or _now())
        out = {"active": [], "proposed": [], "expired": []}
        for row in self._query("SELECT * FROM pip_memory ORDER BY id DESC"):
            item = dict(row)
            if item["status"] == "proposed":
                out["proposed"].append(item)
            elif item["expires_at"] and item["expires_at"] <= stamp:
                out["expired"].append(item)
            else:
                out["active"].append(item)
        return out

    def approve_memory(self, memory_id: int, now: datetime | None = None) -> bool:
        item = self.get_memory(memory_id)
        if item is None:
            return False
        now = now or _now()
        return self._execute("UPDATE pip_memory SET status = 'approved', approved_at = ?, expires_at = ? WHERE id = ?",
                             (_iso(now), _iso(now + timedelta(days=KIND_DAYS[item["kind"]])), memory_id)) > 0

    def renew_memory(self, memory_id: int, now: datetime | None = None) -> bool:
        return self.approve_memory(memory_id, now)

    def delete_memory(self, memory_id: int) -> bool:
        return self._execute("DELETE FROM pip_memory WHERE id = ?", (memory_id,)) > 0

    def restore_memory(self, item: dict) -> int:
        """Put back an item exactly as it was (undoing a 'forget')."""
        return self._insert(
            "INSERT INTO pip_memory (kind, text, status, source_recording_id, created_at, approved_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item["kind"], item["text"], item["status"], item.get("source_recording_id"), item["created_at"],
             item.get("approved_at"), item.get("expires_at")))

    def find_memory(self, words: str, now: datetime | None = None) -> list[dict]:
        """Current or proposed items whose text contains what you asked Pip to forget."""
        wanted = memory_key(words)
        if not wanted:
            return []
        found = self.list_memory(now)
        return [i for i in found["active"] + found["proposed"] if wanted in memory_key(i["text"])]
