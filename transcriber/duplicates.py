"""Duplicate recordings. An exact copy has the same bytes; a possible duplicate starts at the same time and runs for
the same length (within a few seconds), as when the phone or the app saved the same lecture twice."""
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

START_WINDOW = timedelta(minutes=2)  # starts this close together
LENGTH_TOLERANCE_SEC = 10            # and lasts this close to the same time


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_duplicates(recordings) -> dict[str, dict]:
    """Recording id -> {"kind": "exact" | "possible", "of": [{"id", "title"}]} for every recording that has a twin.
    Pass recordings that aren't in the bin."""
    twins: dict[str, dict] = {}

    def link(a, b, kind: str) -> None:
        for one, other in ((a, b), (b, a)):
            entry = twins.setdefault(one.id, {"kind": kind, "of": {}})
            entry["of"][other.id] = other
            if kind == "exact":
                entry["kind"] = "exact"

    by_hash: dict[str, list] = {}
    for rec in recordings:
        if rec.content_hash:  # an empty hash means the audio is gone; nothing to compare
            by_hash.setdefault(rec.content_hash, []).append(rec)
    for same in by_hash.values():
        for i, a in enumerate(same):
            for b in same[i + 1:]:
                link(a, b, "exact")

    timed = sorted((r for r in recordings if r.recorded_at and r.duration_sec),
                   key=lambda r: datetime.fromisoformat(r.recorded_at))
    for i, a in enumerate(timed):
        start = datetime.fromisoformat(a.recorded_at)
        for b in timed[i + 1:]:
            if datetime.fromisoformat(b.recorded_at) - start > START_WINDOW:
                break
            if abs(a.duration_sec - b.duration_sec) <= LENGTH_TOLERANCE_SEC and a.id != b.id:
                link(a, b, "possible")

    return {rid: {"kind": entry["kind"], "of": [{"id": o.id, "title": o.title} for o in entry["of"].values()]}
            for rid, entry in twins.items()}


def describe(twin: dict) -> str:
    """The bell's line for a recording with a twin."""
    names = ", ".join(f"“{other['title']}”" for other in twin["of"][:2])
    word = "Exact copy of" if twin["kind"] == "exact" else "Possible duplicate of"
    return f"{word} {names}"
