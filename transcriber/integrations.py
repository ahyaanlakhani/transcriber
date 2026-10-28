"""Taking your data to other apps, as plain files you choose to open: your class timetable for a calendar app, and your
flashcards for Anki. Both are built on this laptop; nothing is sent anywhere."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .briefing import visible_recordings
from .calendar import class_slots

CALENDAR_DAYS_AHEAD = 120


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n"))


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> list[str]:
    """Calendar files limit lines to 75 bytes; longer ones continue on the next line after a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    parts, current = [], b""
    for char in line:
        piece = char.encode("utf-8")
        if len(current) + len(piece) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += piece
    parts.append(current.decode("utf-8"))
    return [parts[0], *(" " + p for p in parts[1:])]


def calendar_file(store, tz: str, today: date | None = None, now: datetime | None = None) -> str:
    """The timetable for the next few months as one-off events (so skipped weeks, moved classes and daylight-saving
    changes are all right), plus your coming reminders. Cancelled classes are left out."""
    now = now or datetime.now(UTC)
    today = today or now.astimezone(ZoneInfo(tz)).date()
    last = today + timedelta(days=CALENDAR_DAYS_AHEAD)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Transcriber//Classes//EN", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:Transcriber classes"]
    stamp = _stamp(now)
    for subject in store.list_subjects():
        if not subject.schedule:
            continue
        for slot in class_slots(subject.schedule, tz, today, last, subject.id, subject.name):
            if slot.cancelled:
                continue
            lines += ["BEGIN:VEVENT", f"UID:class-{subject.id}-{slot.start.date().isoformat()}-{slot.start:%H%M}@transcriber",
                      f"DTSTAMP:{stamp}", f"DTSTART:{_stamp(slot.start)}", f"DTEND:{_stamp(slot.end)}",
                      f"SUMMARY:{_escape(subject.name)}", "DESCRIPTION:Record this class in Transcriber.", "END:VEVENT"]
    for reminder in store.list_reminders():
        when = datetime.fromisoformat(reminder["at"])
        if when < now:
            continue
        lines += ["BEGIN:VEVENT", f"UID:reminder-{reminder['id']}@transcriber", f"DTSTAMP:{stamp}", f"DTSTART:{_stamp(when)}",
                  f"DTEND:{_stamp(when + timedelta(minutes=15))}", f"SUMMARY:{_escape(reminder['text'])}",
                  "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reminder", "TRIGGER:PT0S", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"


def anki_file(store, tz: str) -> tuple[str, int]:
    """Your flashcards as a text file Anki imports (File > Import): front, back and tags, separated by tabs. The back says
    which recording and moment the card came from. Returns (text, how many cards)."""
    title_ok = {r.id for r in visible_recordings(store, tz)}
    rows = [dict(r) for r in store.query(
        "SELECT c.*, r.title AS recording, s.name AS subject FROM cards c JOIN recordings r ON r.id = c.recording_id"
        " LEFT JOIN subjects s ON s.id = r.subject_id WHERE c.suspended = 0 ORDER BY c.id")]
    cards = [c for c in rows if c["recording_id"] in title_ok]

    def field(text: str) -> str:
        return " ".join(text.split()).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    out = ["#separator:tab", "#html:true", "#tags column:3"]
    for card in cards:
        where = card["recording"] + (f" at {int(card['start'] // 60):02d}:{int(card['start'] % 60):02d}" if card["start"] is not None else "")
        tags = " ".join(t for t in ("Transcriber", card["kind"], (card["subject"] or "").replace(" ", "_")) if t)
        out.append(f"{field(card['front'])}\t{field(card['back'])}<br><small>From {field(where)}</small>\t{tags}")
    return "\n".join(out) + "\n", len(cards)
