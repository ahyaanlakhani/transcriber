"""Which follow-ups you ticked off or dismissed. The key is made from the recording and the item's words, so it survives
the notes being rewritten, and nothing here changes the notes themselves."""
from datetime import UTC, datetime

STATES = ("done", "dismissed")


class FollowUpStoreMixin:
    def follow_up_states(self) -> dict[str, str]:
        return {r["key"]: r["state"] for r in self._query("SELECT key, state FROM follow_up_state")}

    def set_follow_up(self, key: str, state: str | None) -> None:
        """state None opens it again."""
        if state is None:
            self._execute("DELETE FROM follow_up_state WHERE key = ?", (key,))
            return
        if state not in STATES:
            raise ValueError("A follow-up is done or dismissed")
        self._execute("INSERT INTO follow_up_state (key, state, at) VALUES (?, ?, ?)"
                      " ON CONFLICT(key) DO UPDATE SET state = excluded.state, at = excluded.at",
                      (key, state, datetime.now(UTC).isoformat(timespec="seconds")))
