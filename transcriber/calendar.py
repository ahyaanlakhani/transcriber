"""Calendar: class timetables per subject, which classes were recorded, and phone recordings still sitting on the
laptop (found by their own date stamps, without uploading them)."""
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import audio
from .duplicates import find_duplicates
from .store import Store

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".caf", ".ogg", ".opus", ".mp4", ".mov", ".webm", ".flac", ".amr"}
MAX_FILES = 3000
SLACK = timedelta(minutes=20)        # a recording that ends a bit before class still counts
SAME_FILE_SEC = 180                  # a laptop file whose stamp is this close to a recording's is that recording
SAME_LENGTH_SEC = 10


@dataclass
class Slot:
    subject_id: int
    subject: str
    start: datetime
    end: datetime
    cancelled: bool = False  # skipped in the timetable (a holiday, a break, "no class this week")


def class_slots(schedule: dict, tz: str, first: date, last: date, subject_id: int = 0, subject: str = "") -> list[Slot]:
    """Every class in [first, last] for a timetable {days: [0=Mon..6], start: "HH:MM", minutes, from, until}."""
    zone = ZoneInfo(tz)
    days = set(schedule.get("days") or [])
    skipped = set(schedule.get("skip") or [])
    begin = max(first, date.fromisoformat(schedule["from"])) if schedule.get("from") else first
    end = min(last, date.fromisoformat(schedule["until"])) if schedule.get("until") else last
    hour, minute = (int(part) for part in (schedule.get("start") or "09:00").split(":"))
    length = timedelta(minutes=int(schedule.get("minutes") or 60))
    slots = []
    day = begin
    while day <= end:
        if day.weekday() in days:
            start = datetime.combine(day, time(hour, minute), zone)
            slots.append(Slot(subject_id, subject, start, start + length, cancelled=day.isoformat() in skipped))
        day += timedelta(days=1)
    for extra in schedule.get("extra") or []:  # one-off classes: a moved class, a make-up session
        when = date.fromisoformat(extra["date"])
        if first <= when <= last:
            hour, minute = (int(part) for part in extra["start"].split(":"))
            begin = datetime.combine(when, time(hour, minute), zone)
            slots.append(Slot(subject_id, subject, begin, begin + timedelta(minutes=int(extra["minutes"]))))
    return sorted(slots, key=lambda slot: slot.start)


def scan_folders(folders: list[Path], cache_path: Path) -> list[dict]:
    """Audio files in these folders (and below) with their recorded-at stamps. Remembers files it has read."""
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    found, fresh = [], {}
    for folder in folders:
        if not folder.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "AppData")]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                key = f"{path}|{stat.st_size}|{int(stat.st_mtime)}"
                info = cache.get(key)
                if info is None:
                    try:
                        duration = audio.probe_duration(path)
                    except Exception:
                        continue
                    info = {"path": str(path), "name": name, "recorded_at": audio.recorded_at(path),
                            "duration_sec": round(duration, 1), "size": stat.st_size}
                fresh[key] = info
                found.append(info)
                if len(found) >= MAX_FILES:
                    break
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(fresh), encoding="utf-8")
    return found


def _span(start_iso: str, seconds: float | None) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(start_iso)
    return start, start + timedelta(seconds=seconds or 0)


def _overlaps(a: tuple[datetime, datetime], slot: Slot) -> bool:
    """Started before the class ended, and was still going (or started) within SLACK of its start."""
    return a[0] < slot.end and a[1] > slot.start - SLACK


def build_calendar(store: Store, tz: str, first: date, last: date, files: list[dict],
                   today: date | None = None) -> dict:
    zone = ZoneInfo(tz)
    today = today or datetime.now(zone).date()
    recordings = [r for r in store.list_recordings() if r.status != "failed" or r.duration_sec]
    twins = find_duplicates(recordings)
    in_app = [(r, _span(r.recorded_at or r.created_at, r.duration_sec)) for r in recordings]
    # laptop files that aren't in the app yet (same stamp and length as a recording means it's that recording)
    stamped_recs = [(datetime.fromisoformat(r.recorded_at), r.duration_sec or 0) for r in recordings if r.recorded_at]
    laptop = []
    for info in files:
        if not info.get("recorded_at"):
            continue
        stamp = datetime.fromisoformat(info["recorded_at"])
        if any(abs((stamp - when).total_seconds()) <= SAME_FILE_SEC and abs(info["duration_sec"] - length) <= SAME_LENGTH_SEC
               for when, length in stamped_recs):
            continue
        laptop.append((info, _span(info["recorded_at"], info["duration_sec"])))

    def local_day(moment: datetime) -> str:
        return moment.astimezone(zone).date().isoformat()

    days: dict[str, dict] = {}
    for rec, span in in_app:
        day = local_day(span[0])
        if first.isoformat() <= day <= last.isoformat():
            days.setdefault(day, {"recordings": [], "files": []})["recordings"].append(
                {"id": rec.id, "title": rec.title, "subject": rec.subject, "start": span[0].isoformat(), "duplicate": twins.get(rec.id),
                 "minutes": round((rec.duration_sec or 0) / 60)})
    visible_files = []
    for info, span in laptop:
        day = local_day(span[0])
        if first.isoformat() <= day <= last.isoformat():
            days.setdefault(day, {"recordings": [], "files": []})["files"].append(info)
            visible_files.append(info)

    classes, subjects = [], []
    for subject in store.list_subjects():
        if not subject.schedule:
            continue
        counts = {"recorded": 0, "laptop": 0, "missing": 0}
        missing_dates = []
        for slot in class_slots(subject.schedule, tz, first, last, subject.id, subject.name):
            day = slot.start.date().isoformat()
            covering = [rec for rec, span in in_app if rec.recorded_at and _overlaps(span, slot)]
            filed = [rec.id for rec, span in in_app if rec.subject_id == subject.id and local_day(span[0]) == day]
            ids = list(dict.fromkeys(filed + [rec.id for rec in covering if rec.subject_id == subject.id]))
            unfiled = [rec.id for rec in covering if rec.subject_id is None]
            on_laptop = [info for info, span in laptop if _overlaps(span, slot)]
            if ids or unfiled:
                status = "recorded"
            elif on_laptop:
                status = "laptop"
            elif slot.cancelled:
                status = "cancelled"
            elif slot.start.date() > today:
                status = "upcoming"
            elif slot.start.date() == today:
                status = "today"
            else:
                status = "missing"
                missing_dates.append(day)
            if status in counts:
                counts[status] += 1
            classes.append({"subject_id": subject.id, "subject": subject.name, "date": day,
                            "start": slot.start.isoformat(), "end": slot.end.isoformat(), "status": status,
                            "recording_ids": ids, "unfiled_ids": unfiled, "files": on_laptop})
        subjects.append({"id": subject.id, "name": subject.name, **counts, "past": sum(counts.values()),
                         "missing_dates": missing_dates})
    return {"time_zone": tz, "days": days, "classes": classes, "subjects": subjects, "files": visible_files}
