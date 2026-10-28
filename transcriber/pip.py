"""Pip: turns what you type into small, reversible changes (skip or add a class, set a reminder), and keeps class
reminders going in the background. Pip never deletes recordings; every change here returns the steps that undo it."""
import logging
import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .calendar import build_calendar, class_slots
from .memory_store import KINDS as MEMORY_KINDS, MAX_ACTIVE as MEMORY_SHOWN
from .combine import is_combined
from .naming import original_stem, suggest_names
from .sessions import NOT_CLASSES, class_sessions, unique_sessions

log = logging.getLogger(__name__)

SMALL_TOOLS = ("skip_classes", "restore_classes", "add_class", "remind", "remember", "forget")  # done at once, each with an undo
BIG_TOOLS = ("rename_recordings", "combine_classes", "file_recordings")    # shown as a plan first
BRAIN_TOOLS = ("ask_brain", "open_concept")  # answered or opened at once: they change nothing
LLM_TOOLS = SMALL_TOOLS + BIG_TOOLS + ("open_view", "start_meeting") + BRAIN_TOOLS
UNDO_TOOLS = ("unskip_dates", "reskip_dates", "remove_class", "delete_reminder", "restore_titles", "restore_subjects",
              "delete_memory", "restore_memory")
MAX_RENAMES = 40
OPEN_TARGETS = ("calendar", "record", "progress", "practice", "starred", "trash", "all", "brain")
MAX_BREAK_DAYS = 120
CLASS_REMINDER_LEAD = timedelta(minutes=10)

ACTION_SCHEMA = {
    "type": "object",
    "properties": {"say": {"type": "string"}, "actions": {"type": "array", "items": {
        "type": "object",
        "properties": {"tool": {"type": "string", "enum": list(LLM_TOOLS)}, "subject": {"type": "string"},
                       "from": {"type": "string"}, "to": {"type": "string"}, "start": {"type": "string"},
                       "minutes": {"type": "integer"}, "when": {"type": "string"}, "text": {"type": "string"}},
        "required": ["tool", "subject", "from", "to", "start", "minutes", "when", "text"],
        "additionalProperties": False}}},
    "required": ["say", "actions"],
    "additionalProperties": False,
}
SYSTEM = (
    "You are Pip, the friendly microphone mascot of a lecture-transcribing app for a university student. The student "
    "types a request and you turn it into actions the app can perform. Calendar: skip_classes (cancel the classes "
    "between two dates, inclusive; leave subject empty for every subject that has a timetable), restore_classes (undo "
    "a cancellation), add_class (one extra or moved class on a date, with start time and minutes). Reminders: remind "
    "(a one-off reminder: `when` is the local date and time like 2026-10-19T09:45, `text` is short and in the second "
    "person, like 'Record Machine Learning'). Recordings: rename_recordings (give recordings short topic names from "
    "their notes; `text` is 'unnamed' for recordings still carrying their phone names, 'all', or 'joined' for combined "
    "classes; `subject` can limit it to one subject), combine_classes (join the parts of classes that were recorded in "
    "several pieces), file_recordings (put recordings that have no subject into the subject whose class they were "
    "recorded in). Going places: open_view (`text` is calendar, record, progress, practice, starred, trash, brain or "
    "all; or put a subject name in `subject` and leave `text` empty). Your second brain: ask_brain (a question about "
    "what was said in the student's lectures, chats or talks, like 'what did we say about OAuth?'; `text` is the "
    "question), open_concept (open one idea's page, like 'show me RAG'; `text` is the concept's name). Memory: "
    "remember (the student asks you to remember something about them, like a goal, a preference, what they're working "
    "on or a person; `text` is the fact in a few words, `subject` is its kind: goal, preference, ongoing or "
    "relationship), forget (they ask you to forget something; `text` is a few words from it). Use what you already "
    "know about the student, listed below, when it helps; never invent memories. start_meeting (start recording the student's meeting or "
    "call right now, for Zoom, Teams or Meet; `text` is an optional title, `subject` an optional subject). The app "
    "shows the student a plan before it renames, "
    "combines or files anything, so just ask for it. Use ISO dates like 2026-10-19 and 24-hour times like 09:45. Weeks "
    "run Monday to Sunday; 'this week' is the one that contains today. To move a class, skip the old date and add the "
    "new one. Leave fields an action doesn't need as an empty string, and minutes as 0. If the request needs something "
    "you can't do (deleting anything), return no actions and say so kindly. "
    "Never say you have already done something: the app reports what happened. Keep `say` to one or two short, "
    "friendly sentences in the first person.")


class PipError(ValueError):
    """Something Pip can't do; the message is shown to the student."""


def pretty(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d:%a} {d.day} {d:%b}"


def _day(text: str, what: str) -> date:
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError as exc:
        raise PipError(f"I couldn't read the {what} date") from exc


def _clock(text: str) -> tuple[int, int]:
    try:
        hour, minute = (int(part) for part in (text or "").strip().split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError
    except ValueError as exc:
        raise PipError("I couldn't read that class time; it should look like 10:00") from exc
    return hour, minute


class Actions:
    """Runs one action against the store. Each returns {"summary", "undo"}: the words, and the steps that reverse it."""

    def __init__(self, store, tz: str):
        self.store = store
        self.tz = tz

    def run(self, action: dict, allowed: tuple[str, ...] = SMALL_TOOLS) -> dict:
        tool = action.get("tool")
        if tool not in allowed:
            raise PipError("I can't do that yet")
        return getattr(self, f"_{tool}")(action)

    def run_all(self, actions: list[dict], allowed: tuple[str, ...] = SMALL_TOOLS) -> dict:
        done, problems, undo = [], [], []
        for action in actions:
            try:
                result = self.run(action, allowed)
            except PipError as exc:
                problems.append(str(exc))
                continue
            done.append({"summary": result["summary"]})
            undo += result["undo"]
        return {"done": done, "problems": problems, "undo": undo}

    # ---- calendar

    def _subjects(self, name: str, need_timetable: bool = True) -> list:
        subjects = self.store.list_subjects()
        if need_timetable:
            subjects = [s for s in subjects if s.schedule]
        wanted = (name or "").strip().lower()
        if not wanted:
            if not subjects:
                raise PipError("None of your subjects has a class timetable yet")
            return subjects
        found = [s for s in subjects if s.name.lower() == wanted] or [
            s for s in subjects if wanted in s.name.lower() or s.name.lower() in wanted]
        if not found:
            raise PipError(f"I couldn't find “{name}”" + (" with a timetable" if need_timetable else ""))
        return found

    def _range(self, action: dict) -> tuple[date, date]:
        first = _day(action.get("from", ""), "start")
        last = _day(action.get("to") or action.get("from", ""), "end")
        if last < first:
            first, last = last, first
        if (last - first).days > MAX_BREAK_DAYS:
            raise PipError("That's a very long break. I can change up to four months at a time")
        return first, last

    def _class_dates(self, subject, first: date, last: date) -> list[str]:
        regular = {**subject.schedule, "skip": [], "extra": []}
        return [slot.start.date().isoformat() for slot in class_slots(regular, self.tz, first, last, subject.id, subject.name)]

    def _save(self, subject, **changes) -> None:
        schedule = {**subject.schedule, **changes}
        for key in ("skip", "extra"):
            if not schedule.get(key):
                schedule.pop(key, None)
        self.store.update_subject(subject.id, schedule=schedule)

    def _skip_classes(self, action: dict) -> dict:
        first, last = self._range(action)
        done, undo, empty = [], [], []
        for subject in self._subjects(action.get("subject", "")):
            skipped = set(subject.schedule.get("skip") or [])
            dates = [d for d in self._class_dates(subject, first, last) if d not in skipped]
            if not dates:
                empty.append(subject.name)
                continue
            self._save(subject, skip=sorted(skipped | set(dates)))
            done.append(f"{subject.name} on {', '.join(pretty(d) for d in dates)}")
            undo.append({"tool": "unskip_dates", "subject_id": subject.id, "dates": dates})
        if not done:
            raise PipError(f"{' and '.join(empty)} {'has' if len(empty) == 1 else 'have'} no classes to skip "
                           f"between {pretty(first.isoformat())} and {pretty(last.isoformat())}")
        return {"summary": "No class: " + "; ".join(done), "undo": undo}

    def _restore_classes(self, action: dict) -> dict:
        first, last = self._range(action)
        done, undo = [], []
        for subject in self._subjects(action.get("subject", "")):
            skipped = set(subject.schedule.get("skip") or [])
            dates = sorted(d for d in skipped if first <= date.fromisoformat(d) <= last)
            if not dates:
                continue
            self._save(subject, skip=sorted(skipped - set(dates)))
            done.append(f"{subject.name} on {', '.join(pretty(d) for d in dates)}")
            undo.append({"tool": "reskip_dates", "subject_id": subject.id, "dates": dates})
        if not done:
            raise PipError("No skipped classes in that range to bring back")
        return {"summary": "Class is back on: " + "; ".join(done), "undo": undo}

    def _add_class(self, action: dict) -> dict:
        if not (action.get("subject") or "").strip():
            raise PipError("Which subject is the extra class for?")
        subject = self._subjects(action["subject"], need_timetable=False)[0]
        day = _day(action.get("from", ""), "class")
        usual = subject.schedule or {}
        hour, minute = _clock(action.get("start") or usual.get("start") or "")
        minutes = int(action.get("minutes") or usual.get("minutes") or 60)
        if not 5 <= minutes <= 600:
            raise PipError("Check the class length")
        entry = {"date": day.isoformat(), "start": f"{hour:02d}:{minute:02d}", "minutes": minutes}
        base = subject.schedule or {"days": [], "start": entry["start"], "minutes": minutes, "from": None, "until": None}
        extras = [e for e in base.get("extra") or [] if e["date"] != entry["date"]] + [entry]
        subject = replace(subject, schedule=base)
        self._save(subject, extra=sorted(extras, key=lambda e: e["date"]))
        return {"summary": f"Added a {subject.name} class on {pretty(entry['date'])} at {entry['start']}",
                "undo": [{"tool": "remove_class", "subject_id": subject.id, "date": entry["date"], "start": entry["start"]}]}

    # ---- memory: only what the student asks for; each is shown to them and can be forgotten

    def _remember(self, action: dict) -> dict:
        kind = (action.get("subject") or "").strip().lower()
        kind = kind if kind in MEMORY_KINDS else "preference"
        try:
            memory_id = self.store.add_memory(kind, action.get("text") or "")
        except ValueError as exc:
            raise PipError(str(exc)) from exc
        return {"summary": f"I'll remember that ({kind}): {self.store.get_memory(memory_id)['text']}",
                "undo": [{"tool": "delete_memory", "id": memory_id}]}

    def _forget(self, action: dict) -> dict:
        found = self.store.find_memory(action.get("text") or "")
        if not found:
            raise PipError("I don't have anything remembered like that")
        for item in found:
            self.store.delete_memory(item["id"])
        return {"summary": f"Forgot {len(found)} thing{'s' if len(found) != 1 else ''}: "
                           + "; ".join(i["text"] for i in found),
                "undo": [{"tool": "restore_memory", "item": item} for item in found]}

    def _delete_memory(self, action: dict) -> dict:
        self.store.delete_memory(int(action.get("id") or 0))
        return {"summary": "Forgot it again", "undo": []}

    def _restore_memory(self, action: dict) -> dict:
        item = action.get("item") or {}
        try:
            self.store.restore_memory(item)
        except (KeyError, ValueError) as exc:
            raise PipError("I couldn't bring that memory back") from exc
        return {"summary": f"Remembered again: {item.get('text', '')}", "undo": []}

    # ---- reminders

    def _remind(self, action: dict) -> dict:
        text = " ".join((action.get("text") or "").split())
        try:
            when = datetime.fromisoformat((action.get("when") or "").strip())
        except ValueError as exc:
            raise PipError("I couldn't read the reminder time") from exc
        zone = ZoneInfo(self.tz)
        when = when.replace(tzinfo=zone) if when.tzinfo is None else when.astimezone(zone)
        if when <= datetime.now(zone):
            raise PipError("That time has already passed")
        subject = self._subjects(action["subject"], need_timetable=False)[0] if (action.get("subject") or "").strip() else None
        text = text or (f"Record {subject.name}" if subject else "Reminder from Pip")
        rid = self.store.add_reminder(when.astimezone(UTC).isoformat(timespec="seconds"), text, subject.id if subject else None)
        return {"summary": f"I'll remind you on {pretty(when.date().isoformat())} at {when:%H:%M}: {text}",
                "undo": [{"tool": "delete_reminder", "id": rid}]}

    # ---- undo steps

    def _unskip_dates(self, action: dict) -> dict:
        subject = self._exact_subject(action)
        remaining = sorted(set(subject.schedule.get("skip") or []) - set(action.get("dates") or []))
        self._save(subject, skip=remaining)
        return {"summary": f"Brought back {subject.name}", "undo": []}

    def _reskip_dates(self, action: dict) -> dict:
        subject = self._exact_subject(action)
        self._save(subject, skip=sorted(set(subject.schedule.get("skip") or []) | set(action.get("dates") or [])))
        return {"summary": f"Skipped {subject.name} again", "undo": []}

    def _remove_class(self, action: dict) -> dict:
        subject = self._exact_subject(action)
        keep = [e for e in subject.schedule.get("extra") or []
                if not (e["date"] == action.get("date") and e["start"] == action.get("start"))]
        self._save(subject, extra=keep)
        return {"summary": f"Removed the extra {subject.name} class", "undo": []}

    def _delete_reminder(self, action: dict) -> dict:
        self.store.delete_reminder(int(action.get("id") or 0))
        return {"summary": "Reminder removed", "undo": []}

    def _restore_titles(self, action: dict) -> dict:
        for item in action.get("titles") or []:
            if self.store.get_recording(str(item.get("id"))):
                self.store.update_recording(str(item["id"]), title=str(item.get("title") or ""))
        return {"summary": "Names restored", "undo": []}

    def _restore_subjects(self, action: dict) -> dict:
        for item in action.get("items") or []:
            if self.store.get_recording(str(item.get("id"))):
                self.store.update_recording(str(item["id"]), subject_id=item.get("subject_id"))
        return {"summary": "Subjects restored", "undo": []}

    def apply_plan(self, kind: str, payload: dict) -> dict:
        """Carry out a plan the student approved (renames, or filing recordings under subjects)."""
        done, undo = 0, []
        if kind == "rename":
            for item in payload.get("renames") or []:
                rec = self.store.get_recording(str(item.get("id")))
                new = " ".join(str(item.get("new") or "").split())[:200]
                if rec is None or rec.deleted_at or not new or new == rec.title:
                    continue
                self.store.update_recording(rec.id, title=new)
                undo.append({"id": rec.id, "title": rec.title})
                done += 1
            undo_step = [{"tool": "restore_titles", "titles": undo}] if undo else []
            summary = f"Renamed {done} {'recording' if done == 1 else 'recordings'}. Each keeps its original phone name."
        elif kind == "file":
            for item in payload.get("files") or []:
                rec = self.store.get_recording(str(item.get("id")))
                if rec is None or rec.deleted_at or rec.subject_id is not None or self.store.get_subject(int(item.get("subject_id") or 0)) is None:
                    continue
                self.store.update_recording(rec.id, subject_id=int(item["subject_id"]))
                undo.append({"id": rec.id, "subject_id": None})
                done += 1
            undo_step = [{"tool": "restore_subjects", "items": undo}] if undo else []
            summary = f"Filed {done} {'recording' if done == 1 else 'recordings'} under their subjects"
        else:
            raise PipError("I don't know that plan")
        if not done:
            return {"done": [], "problems": ["Nothing needed changing"], "undo": []}
        return {"done": [{"summary": summary}], "problems": [], "undo": undo_step}

    def _exact_subject(self, action: dict):
        subject = self.store.get_subject(int(action.get("subject_id") or 0))
        if subject is None or not subject.schedule:
            raise PipError("That subject is gone")
        return subject


def _unnamed(rec) -> bool:
    """Still carries the name the phone gave it."""
    stem = original_stem(rec.original_filename)
    return bool(stem) and rec.title.lower().startswith(stem.lower())


class Planner:
    """Works out what the bigger changes would be (renaming, combining, filing) without changing anything."""

    def __init__(self, store, writer, tz: str):
        self.store = store
        self.writer = writer
        self.tz = tz

    def plan(self, action: dict) -> dict:
        tool = action.get("tool")
        if tool not in BIG_TOOLS:
            raise PipError("I can't plan that")
        return getattr(self, f"_{tool}")(action)

    def _rename_recordings(self, action: dict) -> dict:
        if self.writer is None:
            raise PipError("I need the notes writer (a Groq key) to write names")
        scope = (action.get("text") or "unnamed").strip().lower()
        wanted = (action.get("subject") or "").strip().lower()
        everything = self.store.list_recordings()
        sessions = class_sessions(everything, self.tz)
        pool = [r for r in everything if r.original_filename not in NOT_CLASSES and (not wanted or (r.subject or "").lower() == wanted)]
        if scope == "joined":
            pool = [r for r in pool if is_combined(r.original_filename)]
        elif scope != "all":
            pool = [r for r in pool if _unnamed(r)]
        if not pool:
            raise PipError("Every recording already has a name of its own" if scope not in ("all", "joined") else "No recordings match that")
        items = []
        for rec in sorted(pool, key=lambda r: r.recorded_at or r.created_at)[:MAX_RENAMES]:
            options = suggest_names(self.store, self.writer, rec, self.tz, sessions.get(rec.id))
            if options:
                items.append({"id": rec.id, "old": rec.title, "new": options[0]["title"]})
        if not items:
            raise PipError("I couldn't think of better names")
        more = f" (the first {MAX_RENAMES}; ask again for the rest)" if len(pool) > MAX_RENAMES else ""
        return {"kind": "rename", "title": f"Rename {len(items)} recordings{more}",
                "items": [{"label": f"{i['old']} → {i['new']}"} for i in items],
                "apply": {"renames": [{"id": i["id"], "new": i["new"]} for i in items]}}

    def _combine_classes(self, action: dict) -> dict:
        everything = self.store.list_recordings()
        status = {r.id: r.status for r in everything}
        waiting = [s for s in unique_sessions(class_sessions(everything, self.tz)) if not s["combined"]]
        ready = [s for s in waiting if all(status.get(i) == "done" for i in s["ids"])]
        if not waiting:
            raise PipError("No classes are waiting to be combined")
        if not ready:
            raise PipError("The parts of those classes are still transcribing")
        return {"kind": "combine", "title": f"Combine {len(ready)} {'class' if len(ready) == 1 else 'classes'}",
                "items": [{"label": f"{s['title']}: {s['count']} parts, {s['minutes']} min"} for s in ready],
                "note": "Each gets new notes written (this uses some of your Groq allowance). Your originals are kept.",
                "apply": {}}

    def _file_recordings(self, action: dict) -> dict:
        loose = [r for r in self.store.list_recordings()
                 if r.subject_id is None and r.original_filename not in NOT_CLASSES and r.recorded_at]
        if not loose:
            raise PipError("Every recording already has a subject")
        zone = ZoneInfo(self.tz)
        today = datetime.now(zone).date()
        first = max(min(datetime.fromisoformat(r.recorded_at).astimezone(zone).date() for r in loose), today - timedelta(days=400))
        titles = {r.id: r.title for r in loose}
        items, seen = [], set()
        for c in build_calendar(self.store, self.tz, first, today, [], today=today)["classes"]:
            for rid in c["unfiled_ids"]:
                if rid in titles and rid not in seen:
                    seen.add(rid)
                    items.append({"id": rid, "subject_id": c["subject_id"], "label": f"{titles[rid]} → {c['subject']}"})
        if not items:
            raise PipError("None of your unsorted recordings line up with a class in your timetable")
        return {"kind": "file", "title": f"File {len(items)} {'recording' if len(items) == 1 else 'recordings'} under their subjects",
                "items": [{"label": i["label"]} for i in items],
                "note": "Matched by when each was recorded against your class timetable.",
                "apply": {"files": [{"id": i["id"], "subject_id": i["subject_id"]} for i in items]}}


def parse_request(llm, text: str, store, tz: str, now: datetime | None = None, memory: list[str] | None = None) -> dict:
    """Ask the notes model what the student wants, as actions from the fixed list above. Nothing runs here."""
    zone = ZoneInfo(tz)
    now = now or datetime.now(zone)
    monday = now.date() - timedelta(days=now.weekday())
    lines = [f"Today is {now:%A %Y-%m-%d}, {now:%H:%M} ({tz}).",
             f"This week: {monday} to {monday + timedelta(days=6)}. Next week: {monday + timedelta(days=7)} to "
             f"{monday + timedelta(days=13)}.", "Subjects:"]
    for subject in store.list_subjects():
        schedule = subject.schedule
        if schedule:
            days = ", ".join(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d] for d in schedule.get("days") or [])
            skipped = f"; no class on {', '.join(schedule['skip'])}" if schedule.get("skip") else ""
            lines.append(f"- {subject.name}: every {days or 'no regular day'} at {schedule.get('start')} for "
                         f"{schedule.get('minutes')} min{skipped}")
        else:
            lines.append(f"- {subject.name}: no timetable")
    if memory:
        lines += ["", "What you know about the student (they approved each):", *[f"- {m}" for m in memory[:MEMORY_SHOWN]]]
    lines += ["", f"Request: {text}"]
    raw = llm.complete_json(system=SYSTEM, user="\n".join(lines), schema=ACTION_SCHEMA, schema_name="pip_request", effort="low")
    actions = [{key: a.get(key, "" if key != "minutes" else 0) for key in ACTION_SCHEMA["properties"]["actions"]["items"]["properties"]}
               for a in raw.get("actions") or [] if isinstance(a, dict) and a.get("tool") in LLM_TOOLS][:6]
    return {"say": str(raw.get("say") or "").strip(), "actions": actions}


class ClassReminders:
    """Every few seconds: reminders that have come due, and a nudge shortly before a class that hasn't been recorded.
    Each reaches you in the bell and, if `notify` is given, as a desktop notification (even with no browser open)."""

    def __init__(self, store, notify=None, lead: timedelta = CLASS_REMINDER_LEAD):
        self.store = store
        self.notify = notify
        self.lead = lead

    def tick(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(UTC)
        settings = self.store.get_settings()
        if settings.get("class_reminders") == "1":
            self._class_nudges(now, settings["time_zone"])
        sent = []
        for reminder in self.store.due_reminders(now.astimezone(UTC).isoformat(timespec="seconds")):
            self.store.mark_reminder_fired(reminder["id"])
            self.store.notify_text("reminder", reminder["text"])
            if self.notify:
                try:
                    self.notify("Pip", reminder["text"])
                except Exception:  # a missing desktop notification must never stop the others
                    log.exception("Couldn't show a desktop notification")
            sent.append(reminder["text"])
        return sent

    def _class_nudges(self, now: datetime, tz: str) -> None:
        today = now.astimezone(ZoneInfo(tz)).date()
        for c in build_calendar(self.store, tz, today, today, [], today=today).get("classes", []):
            if c["status"] != "today":  # recorded, cancelled, or not today's: nothing to nudge about
                continue
            start, end = datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"])
            if start - self.lead <= now < end:
                at = now.astimezone(UTC).isoformat(timespec="seconds")
                text = f"{c['subject']} starts at {start:%H:%M}. Open Transcriber and tap Record now."
                if now >= start:
                    text = f"{c['subject']} started at {start:%H:%M} and nothing is recording. Tap Record now."
                self.store.add_reminder(at, text, c["subject_id"], key=f"class:{c['subject_id']}:{c['date']}")


class ReminderLoop(threading.Thread):
    def __init__(self, reminders: ClassReminders, every_sec: float = 20):
        super().__init__(name="pip-reminders", daemon=True)
        self.reminders = reminders
        self.every = every_sec
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self.every):
            try:
                self.reminders.tick()
            except Exception:
                log.exception("Reminder check failed")

    def stop(self) -> None:
        self._stop_event.set()


def memory_for_pip(store, gateway=None, config_id: str = "") -> tuple[list[str], list[str]]:
    """(lines Pip may be shown, the recordings behind them). Only approved, current items; an item that came from a
    recording is left out unless the model may see that recording, and the recordings used set the request's class."""
    items = store.list_memory()["active"][:MEMORY_SHOWN]
    from_recordings = [i["source_recording_id"] for i in items if i["source_recording_id"]]
    allowed = gateway.eligible_ids(config_id, from_recordings) if gateway is not None and from_recordings else set(from_recordings)
    lines, used = [], []
    for item in items:
        rid = item["source_recording_id"]
        if rid and rid not in allowed:
            continue
        lines.append(f"{item['kind']}: {item['text']}")
        if rid:
            used.append(rid)
    return lines, list(dict.fromkeys(used))


MY_ITEMS = ("", "me", "you", "i", "myself", "student")


def suggest_memory(store, days: int = 14, now: datetime | None = None) -> int:
    """Turn your own recent action items (from meeting notes) into suggestions, waiting for your approval. Nothing is
    remembered until you approve it. Returns how many new suggestions there are."""
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=days)).isoformat(timespec="seconds")
    added = 0
    for rec in store.list_recordings():
        record = store.get_notes(rec.id)
        if rec.deleted_at or not record or record.status != "ready" or record.notes is None:
            continue
        if (rec.recorded_at or rec.created_at) < cutoff:
            continue
        for item in record.notes.action_items:
            if (item.owner or "").strip().lower() not in MY_ITEMS:
                continue
            text = f"{item.text.rstrip('. ')} ({rec.title})" + (f", by {item.due}" if item.due else "")
            before = len(store.list_memory()["proposed"])
            store.add_memory("ongoing", text, source_recording_id=rec.id, status="proposed")
            added += len(store.list_memory()["proposed"]) - before
    return added
