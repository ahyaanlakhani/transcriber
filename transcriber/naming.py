"""Names for recordings. The name the file had on the phone is stored when it's added and never changes, so you can
rename freely and still match it to the phone; this suggests better ones from what was taught."""
import re
from pathlib import Path

from . import privacy
from .privacy import PrivacyBlocked
from .providers.base import RateLimited, why
from .sessions import when_label

TITLE_SCHEMA = {
    "type": "object",
    "properties": {"titles": {"type": "array", "items": {"type": "string"}}},
    "required": ["titles"],
    "additionalProperties": False,
}
TITLE_SYSTEM = (
    "You name recordings of university lectures and meetings so they are easy to find later. Reply with three "
    "different titles, most specific first. Each is a short noun phrase of at most seven words that says what was "
    "taught or discussed. No dates, no subject name, no quotes, no final full stop, and never start with 'Lecture on' "
    "or 'Introduction to the lecture'.")
MAX_MATERIAL = 3000
COMBINED_FILE = re.compile(r"^\d+ combined:", re.IGNORECASE)


def clean(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().strip("\"'“”‘’").rstrip(".").strip()[:80]


def compose(name: str, part: int | None = None) -> str:
    """Names are the topic alone: the list already shows the date and the subject beside each recording."""
    return f"{name} (part {part})" if part else name


def original_stem(original_filename: str) -> str | None:
    """The phone's name without its extension, or None when there was no real file (live and practice recordings)."""
    if COMBINED_FILE.match(original_filename):  # "3 combined: New Recording 1.m4a, …" is a list of files, not a name
        return None
    path = Path(original_filename)
    return path.stem if path.suffix and len(path.suffix) <= 6 else None


def _material(store, rec) -> str:
    record = store.get_notes(rec.id)
    if record and record.notes:
        lines = [item.text for item in record.notes.summary[:10]]
        if record.notes.topics:
            lines.append("Topics: " + ", ".join(record.notes.topics[:8]))
        if lines:
            return "\n".join(lines)[:MAX_MATERIAL]
    transcript = store.get_transcript(rec.id)
    return transcript.text[:1500] if transcript else ""


def _model_titles(writer, subject: str | None, material: str) -> list[str]:
    if writer is None or not material:
        return []
    try:
        raw = writer.complete_json(system=TITLE_SYSTEM, user=f"Subject: {subject or 'unknown'}\n\n{material}",
                                   schema=TITLE_SCHEMA, schema_name="recording_titles", effort="low")
    except Exception:  # the plain suggestions below still work without the model
        return []
    return [t for t in (clean(t) for t in raw.get("titles", []) if isinstance(t, str)) if t]


def suggest_names(store, writer, rec, tz: str, session: dict | None = None) -> list[dict]:
    """[{"title", "source"}], best first, never the current title. Parts of a class get "(part 2)"."""
    when = when_label(rec.recorded_at or rec.created_at, tz)
    part = session["index"] if session else None
    with privacy.scope("naming", [rec.id]):
        titles = _model_titles(writer, rec.subject, _material(store, rec))[:2]
    options = [(compose(what, part), "from your notes") for what in titles]
    topics = [t["name"] for t in store.recording_topics(rec.id) if t["state"] == "accepted"]
    if topics:
        options.append((compose(", ".join(topics[:2]), part), "from your topics"))
    options.append((compose(f"{rec.subject}, {when}" if rec.subject else when, part), "subject and date"))
    original = original_stem(rec.original_filename)
    if original:
        options.append((original, "original name"))
    seen, out = {rec.title.lower()}, []
    for title, source in options:
        if title.lower() not in seen:
            seen.add(title.lower())
            out.append({"title": title, "source": source})
    return out


def suggest_class_names(store, writer, joined, tz: str) -> list[dict]:
    """The best new name for each joined class, for the 'Name all classes' review."""
    out = []
    for rec in joined:
        options = suggest_names(store, writer, rec, tz)
        if options:
            out.append({"id": rec.id, "title": rec.title, "suggestion": options[0]["title"], "source": options[0]["source"]})
    return out


# --- many recordings at once: "Rename all" and "Rename the ones with a standard name"

DEFAULT_NAME = re.compile(
    r"^(new\s+)?(recording|voice\s+(memo|note|recording)|audio|untitled|memo|lecture|class)\s*#?\d*$", re.IGNORECASE)
# The "subject and date" fallback name ("Fri 25 Sep", "Machine Learning, Wed 23 Sep") is a placeholder too.
DATE_NAME = re.compile(r"^(.+?\s*[,—–-]\s*)?(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*$", re.IGNORECASE)
TRAILING_DATE = re.compile(r"\s*[—–-]\s*\d{4}-\d{2}-\d{2}.*$")
# A phone names a recording after where it was made. Only names that really read as an address count: ending in a
# street type ("University Rd", "123 Main Street"), or a bare place ("University", "University of Northfield 5"). A topic
# that merely mentions one of these words ("Campus safety review", "Bus Lane Policy") is a name you wrote.
STREET_TYPES = {"road", "rd", "street", "st", "avenue", "ave", "lane", "ln", "drive", "dr", "boulevard", "blvd",
                "highway", "hwy"}
PLACE_NAME = re.compile(r"^(university|univ|college|campus)(\s+of\s+[a-z .'-]+?)?(\s+\d+)?$", re.IGNORECASE)
MAX_ADDRESS_WORDS = 6
BULK_BATCH = 8
BULK_MATERIAL = 700
BULK_SYSTEM = (
    "You name recordings of university lectures and meetings so they are easy to find later. You get several "
    "numbered recordings, each with its subject and a summary. Reply with one title for each, in the same order: a "
    "short noun phrase of at most seven words that says what was taught or discussed. No dates, no subject name, no "
    "quotes, no final full stop, and never start with 'Lecture on'. Return exactly one title per recording.")
BULK_SCHEMA = TITLE_SCHEMA


def is_standard_name(title: str, original_filename: str | None = None) -> bool:
    """True for a name nobody chose: the phone's default ("New Recording 33", "Voice Memo 7", "Untitled") or an
    address the phone made up from where it was recorded ("University Rd"). Anything you wrote yourself is a topic."""
    name = TRAILING_DATE.sub("", title).strip()
    if not name or DEFAULT_NAME.match(name) or DATE_NAME.match(name):
        return True
    if PLACE_NAME.match(name):
        return True
    parts = name.split()
    last = re.sub(r"[^a-z]", "", parts[-1].lower())
    return 2 <= len(parts) <= MAX_ADDRESS_WORDS and last in STREET_TYPES


def _bulk_titles(writer, items: list[tuple[str | None, str]]) -> tuple[list[str] | None, str | None]:
    """One title for each (subject, material), or (None, why) when the model can't give exactly that."""
    numbered = "\n\n".join(f"{n}. Subject: {subject or 'unknown'}\n{material}" for n, (subject, material) in enumerate(items, 1))
    try:
        raw = writer.complete_json(system=BULK_SYSTEM, user=numbered, schema=BULK_SCHEMA, schema_name="recording_titles",
                                   effort="low")
    except RateLimited:
        return None, "Your notes model has hit its usage limit for now"
    except PrivacyBlocked as exc:  # the message is safe to show: it says what was refused, never any content
        return None, str(exc)
    except Exception as exc:
        return None, f"Your notes model didn't answer ({why(exc)})"
    titles = [clean(t) for t in (raw.get("titles", []) if isinstance(raw, dict) else []) if isinstance(t, str)]
    if len(titles) == len(items) and all(titles):
        return titles, None
    return None, "Your notes model gave an answer the app couldn't use"


def suggest_bulk_names(store, writer, recs, tz: str, sessions: dict | None = None) -> tuple[list[dict], str | None]:
    """A new name for each recording, for the review dialog: written from the notes, several recordings to a model
    call. When the model can't (or has nothing to read) the name comes from topics, or the subject and date, and the
    second value says why, so a list of date names isn't mistaken for the best the notes could do."""
    sessions = sessions or {}
    out, problem = [], None
    for start in range(0, len(recs), BULK_BATCH):
        batch = recs[start:start + BULK_BATCH]
        materials = [(rec.subject, _material(store, rec)[:BULK_MATERIAL]) for rec in batch]
        readable = [i for i, (_, material) in enumerate(materials) if material and writer is not None]
        written: dict[int, str] = {}
        if readable:
            with privacy.scope("naming", [batch[i].id for i in readable]):
                titles, why_not = _bulk_titles(writer, [materials[i] for i in readable])
            if titles:
                written = dict(zip(readable, titles))
            else:
                problem = problem or why_not
        for i, rec in enumerate(batch):
            session = sessions.get(rec.id)
            part = session["index"] if session and not session.get("combined") else None
            if i in written:
                suggestion, source = compose(written[i], part), "from your notes"
            else:
                options = [o for o in suggest_names(store, None, rec, tz, session) if o["source"] != "original name"]
                if not options:  # going back to the phone's own name would be a step backwards
                    continue
                suggestion, source = options[0]["title"], options[0]["source"]
                if source == "subject and date" and DATE_NAME.match(TRAILING_DATE.sub("", rec.title).strip()):
                    continue  # already named by its date: another date name is no help
            if suggestion.lower() != rec.title.lower():
                out.append({"id": rec.id, "title": rec.title, "suggestion": suggestion, "source": source})
    return out, problem
