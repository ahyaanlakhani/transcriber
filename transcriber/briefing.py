"""Today: the day's classes, what needs attention, what you still owe, and what to revise. Built from your own notes
and calendar on this laptop, with no AI model, so it is instant and nothing about it leaves the app."""
import hashlib
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .calendar import build_calendar
from .sessions import NOT_CLASSES, class_sessions

MY_ITEMS = ("", "me", "you", "i", "myself", "student")
CERTAINTY = {"supported": "checked against the recording", "partial": "only partly supported by the recording",
             "unsupported": "not supported by the recording", None: "not checked"}
KINDS = (("action_items", "action"), ("missed_followups", "follow-up"), ("open_questions", "question"))
FOLLOW_UP_DAYS = 45  # older notes are not nagging you
REVISE_DAYS = 14
MAX_FOLLOW_UPS = 200


def follow_up_key(recording_id: str, section: str, text: str) -> str:
    """Stable while the item's words are: rewriting the notes keeps what you already ticked off."""
    words = " ".join(text.lower().split())
    return hashlib.sha1(f"{recording_id}|{section}|{words}".encode()).hexdigest()[:16]


def visible_recordings(store, tz: str) -> list:
    """Finished recordings that stand on their own: not in the bin, not practice, not the parts of a combined class and
    not the device copies of a merged recording, so nothing is listed twice."""
    everything = store.list_recordings()
    hidden = set()
    for rid, info in class_sessions(everything, tz).items():
        if info.get("combined") and info.get("combined_id") != rid:
            hidden.add(rid)
    for rec in everything:
        hidden.update(s.get("id") for s in store.get_merged_from(rec.id) or [])
    return [r for r in everything if not r.deleted_at and r.id not in hidden and r.original_filename not in NOT_CLASSES]


def follow_ups(store, tz: str, *, now: datetime | None = None, include_done: bool = False) -> list[dict]:
    """Things from your notes that someone has to do or answer, with who, by when, where it came from, and how sure the
    notes are. Yours come first, then the newest."""
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=FOLLOW_UP_DAYS)).isoformat(timespec="seconds")
    states = store.follow_up_states()
    out = []
    for rec in visible_recordings(store, tz):
        when = rec.recorded_at or rec.created_at
        record = store.get_notes(rec.id)
        if when < cutoff or not record or record.status != "ready" or record.notes is None:
            continue
        lecture = store.notes_style(rec.id) == "lecture"
        for section, kind in KINDS:
            if lecture and section == "open_questions":  # a lecture's questions were asked in class: nothing for you to do
                continue
            for item in getattr(record.notes, section):
                key = follow_up_key(rec.id, section, item.text)
                state = states.get(key)
                if state and not include_done:
                    continue
                owner = (item.owner or "").strip()
                out.append({"key": key, "kind": kind, "text": item.text, "owner": owner or None,
                            "mine": owner.lower() in MY_ITEMS, "due": item.due, "state": state,
                            "recording_id": rec.id, "recording": rec.title, "at": when,
                            "start": item.cites[0] if item.cites else None,
                            "certainty": CERTAINTY.get(item.verdict, CERTAINTY[None])})
    out.sort(key=lambda i: i["at"], reverse=True)
    out.sort(key=lambda i: not i["due"])  # with a date first
    out.sort(key=lambda i: not i["mine"])
    return out[:MAX_FOLLOW_UPS]


def revision_priorities(store, tz: str, *, now: datetime | None = None, limit: int = 6) -> list[dict]:
    """What the lecturer stressed in the last two weeks (the notes' "stressed" items), newest first."""
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=REVISE_DAYS)).isoformat(timespec="seconds")
    found = []
    for rec in visible_recordings(store, tz):
        when = rec.recorded_at or rec.created_at
        record = store.get_notes(rec.id)
        if when < cutoff or not record or record.status != "ready" or record.notes is None:
            continue
        for item in record.notes.lecture:
            if item.kind == "stressed":
                found.append({"text": item.text, "heading": item.heading, "recording_id": rec.id, "recording": rec.title,
                              "subject": rec.subject, "at": when, "start": item.cites[0] if item.cites else None})
    found.sort(key=lambda i: i["at"], reverse=True)
    return found[:limit]


def briefing(store, tz: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    today = local.date()
    calendar = build_calendar(store, tz, today, today, [], today=today)
    classes = [{"subject": c["subject"], "start": c["start"], "end": c["end"], "status": c["status"],
                "recording_ids": c["recording_ids"]} for c in calendar["classes"] if c["date"] == today.isoformat()]
    classes.sort(key=lambda c: c["start"])
    reminders = [r for r in store.list_reminders()
                 if datetime.fromisoformat(r["at"]).astimezone(zone).date() == today]
    day_ago = (now - timedelta(days=1)).isoformat(timespec="seconds")
    recent = [n for n in store.list_notifications(100) if n["at"] >= day_ago]
    ready = [{"recording_id": n["recording_id"], "title": n["title"]} for n in recent if n["kind"] == "notes_ready"]
    attention = [{"id": r.id, "title": r.title, "why": "Transcription failed" if r.status == "failed" else "Notes failed"}
                 for r in visible_recordings(store, tz)
                 if r.status == "failed" or (r.notes_status == "failed")]
    items = follow_ups(store, tz, now=now)
    memory = store.list_memory(now)["active"]
    return {"date": today.isoformat(), "weekday": f"{local:%A}", "classes": classes, "reminders": reminders,
            "notes_ready": ready, "needs_attention": attention,
            "follow_ups": {"total": len(items), "mine": sum(1 for i in items if i["mine"]), "top": items[:6]},
            "revise": revision_priorities(store, tz, now=now),
            "remembered": [m for m in memory if m["kind"] == "ongoing"][:5]}
