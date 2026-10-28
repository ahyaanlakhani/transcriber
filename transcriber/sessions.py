"""Class sessions: a lecture the phone saved as several recordings in a row. They read better as one session (one set
of notes, topics that run across the cut), so each part is marked and can be combined. Nothing is combined unasked."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .combine import is_combined

MAX_GAP = timedelta(minutes=45)       # pauses between parts, at most this long
MAX_OVERLAP = timedelta(seconds=60)   # more overlap than this means two devices at once, not parts in a row
NOT_CLASSES = {"Practice session"}


def when_label(iso: str, tz: str) -> str:
    local = datetime.fromisoformat(iso).astimezone(ZoneInfo(tz))
    return f"{local:%a} {local.day} {local:%b}"


def class_sessions(recordings, tz: str) -> dict[str, dict]:
    """Recording id -> {"index", "count", "ids", "minutes", "combined", "title"} for every part of a session.
    Parts are recordings of one subject, on one day, each starting within 45 minutes of the previous one ending.
    Pass recordings that aren't in the bin."""
    zone = ZoneInfo(tz)
    usable, combined = [], []
    for rec in recordings:
        if rec.deleted_at:
            continue
        start = datetime.fromisoformat(rec.recorded_at or rec.created_at)
        if is_combined(rec.original_filename):
            combined.append((rec.subject_id, start, rec.id, rec.title))
        elif rec.original_filename not in NOT_CLASSES and rec.duration_sec is not None:
            usable.append((start, rec))
    usable.sort(key=lambda pair: pair[0])

    groups, current = [], []
    for start, rec in usable:
        if current:
            before_start, before = current[-1]
            gap = start - (before_start + timedelta(seconds=before.duration_sec))
            same_day = start.astimezone(zone).date() == before_start.astimezone(zone).date()
            if rec.subject_id == before.subject_id and same_day and -MAX_OVERLAP <= gap <= MAX_GAP:
                current.append((start, rec))
                continue
            groups.append(current)
        current = [(start, rec)]
    if current:
        groups.append(current)

    sessions: dict[str, dict] = {}
    for group in groups:
        if len(group) < 2:
            continue
        first_start, first = group[0]
        when = when_label(first_start.isoformat(), tz)
        joined = next(((cid, ctitle) for subject, start, cid, ctitle in combined
                       if subject == first.subject_id and start == first_start), None)
        info = {
            "count": len(group), "ids": [rec.id for _, rec in group], "start": first_start.isoformat(),
            "minutes": round(sum(rec.duration_sec for _, rec in group) / 60),
            "combined": joined is not None, "combined_id": joined[0] if joined else None,
            "combined_title": joined[1] if joined else None,
            "title": f"{first.subject} — {when}" if first.subject else f"Session on {when}",
        }
        for index, (_, rec) in enumerate(group, 1):
            sessions[rec.id] = {**info, "index": index}
    return sessions


def unique_sessions(sessions: dict[str, dict]) -> list[dict]:
    """One entry per class (not per part), newest first."""
    seen, classes = set(), []
    for info in sessions.values():
        key = tuple(info["ids"])
        if key not in seen:
            seen.add(key)
            classes.append({name: value for name, value in info.items() if name != "index"})
    return sorted(classes, key=lambda info: info["start"], reverse=True)
