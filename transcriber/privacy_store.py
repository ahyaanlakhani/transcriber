"""Storage for the privacy gateway: each recording's class, where combined recordings came from, which cloud
configurations you approved, and a safe log of what was sent (never any audio or text)."""
import json
from datetime import UTC, datetime

STANDARD, PRIVATE, LOCAL_ONLY = "standard", "private", "local_only"
CLASSES = (STANDARD, PRIVATE, LOCAL_ONLY)
RANK = {STANDARD: 0, PRIVATE: 1, LOCAL_ONLY: 2}
def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


MAX_TRANSMISSIONS = 2000
POLICY_REVISION_KEY = "privacy_policy_revision"


def strictest(classes) -> str:
    """The strictest of several classes; an unknown value counts as local-only, so it can never loosen anything."""
    worst = STANDARD
    for value in classes:
        value = value if value in RANK else LOCAL_ONLY
        if RANK[value] > RANK[worst]:
            worst = value
    return worst


class PrivacyStoreMixin:
    # ---- a recording's class and where it came from

    def privacy_info(self, ids) -> dict[str, dict]:
        """For each recording that exists: its class, the floor inherited from what it was made of, and whether its
        sources are known. A missing id is simply absent from the result."""
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self._query(f"SELECT id, privacy_classification AS cls, privacy_floor AS floor, privacy_provenance AS prov"
                           f" FROM recordings WHERE id IN ({marks})", tuple(ids))
        return {r["id"]: {"class": r["cls"], "floor": r["floor"], "known": r["prov"] == "known"} for r in rows}

    def effective_privacy(self, ids) -> dict[str, tuple[str, bool]]:
        """id -> (the strictest class that applies, whether its provenance is fully known). Follows the lineage, so a
        class raised later on a source reaches what was made from it. Missing recordings are left out."""
        result = {}
        for rid in dict.fromkeys(ids):
            cls, known, seen, todo = STANDARD, True, set(), [rid]
            exists = False
            while todo:
                current = todo.pop()
                if current in seen:
                    continue
                seen.add(current)
                info = self.privacy_info([current]).get(current)
                if info is None:
                    if current != rid:  # a source that was erased for good: the floor already holds what it was
                        continue
                    break
                if current == rid:
                    exists = True
                cls = strictest([cls, info["class"], info["floor"]])
                known = known and info["known"]
                todo += [r["source_id"] for r in self._query(
                    "SELECT source_id FROM recording_sources WHERE recording_id = ?", (current,))]
            if exists:
                result[rid] = (cls, known)
        return result

    def set_privacy(self, id: str, classification: str) -> bool:
        if classification not in RANK:
            raise ValueError("Unknown privacy class")
        return self._execute("UPDATE recordings SET privacy_classification = ? WHERE id = ?",
                             (classification, id)) > 0

    def confirm_provenance(self, id: str) -> bool:
        """You checked where this recording came from, so it may be sent to approved providers."""
        return self._execute("UPDATE recordings SET privacy_provenance = 'known' WHERE id = ?", (id,)) > 0

    def record_sources(self, id: str, source_ids: list[str]) -> None:
        """A recording was made from others (combined, merged): it can never be less private than they are, even after
        they are erased. A source whose own history is unknown makes this one unknown too."""
        sources = [s for s in dict.fromkeys(source_ids) if s != id]
        if not sources:
            return
        effective = self.effective_privacy(sources)
        known = len(effective) == len(sources) and all(ok for _, ok in effective.values())
        floor = strictest(cls for cls, _ in effective.values())
        if len(effective) != len(sources):  # a source that is already gone: we can't say what it was
            floor = strictest([floor, PRIVATE])
        with self.transaction() as conn:
            for source in sources:
                conn.execute("INSERT OR IGNORE INTO recording_sources (recording_id, source_id) VALUES (?, ?)",
                             (id, source))
            current = conn.execute("SELECT privacy_floor FROM recordings WHERE id = ?", (id,)).fetchone()
            if current is None:
                return
            conn.execute("UPDATE recordings SET privacy_floor = ? WHERE id = ?",
                         (strictest([current["privacy_floor"], floor]), id))
            if not known:
                conn.execute("UPDATE recordings SET privacy_provenance = 'unknown' WHERE id = ?", (id,))

    # ---- which cloud configurations you approved

    def get_policies(self) -> dict[str, dict]:
        rows = self._query("SELECT * FROM provider_policies")
        return {r["config_id"]: dict(r) for r in rows}

    def set_policy(self, config_id: str, *, standard: bool, private: bool, retention: str = "unverified",
                   note: str = "") -> None:
        now = now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO provider_policies (config_id, approved_standard, approved_private, retention, note,"
                " verified_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(config_id) DO UPDATE SET approved_standard = excluded.approved_standard,"
                " approved_private = excluded.approved_private, retention = excluded.retention, note = excluded.note,"
                " verified_at = excluded.verified_at, updated_at = excluded.updated_at",
                (config_id, int(standard), int(private), retention, note, now if private else None, now))
            self._bump_revision(conn)

    def clear_policy(self, config_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM provider_policies WHERE config_id = ?", (config_id,))
            self._bump_revision(conn)

    @staticmethod
    def _bump_revision(conn) -> None:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, '1') ON CONFLICT(key) DO UPDATE SET"
                     " value = CAST(value AS INTEGER) + 1", (POLICY_REVISION_KEY,))

    def policy_revision(self) -> int:
        rows = self._query("SELECT value FROM settings WHERE key = ?", (POLICY_REVISION_KEY,))
        return int(rows[0]["value"]) if rows else 0

    # ---- what was sent (metadata only)

    def log_transmission(self, *, feature: str, config_id: str, recording_ids, decision: str, reason: str) -> None:
        revision = self.policy_revision()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO transmissions (at, feature, config_id, recording_ids, decision, reason, policy_revision)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now_iso(), feature, config_id, json.dumps(sorted(set(recording_ids))), decision, reason, revision))
            conn.execute("DELETE FROM transmissions WHERE id <= (SELECT MAX(id) FROM transmissions) - ?",
                         (MAX_TRANSMISSIONS,))

    def list_transmissions(self, limit: int = 100) -> list[dict]:
        rows = self._query("SELECT * FROM transmissions ORDER BY id DESC LIMIT ?", (limit,))
        return [{**dict(r), "recording_ids": json.loads(r["recording_ids"])} for r in rows]
