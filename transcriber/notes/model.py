"""AI meeting notes: data shapes, the JSON schemas the models must follow, and parsing."""
import re
from dataclasses import asdict, dataclass, field

SECTIONS = ("summary", "lecture", "about_them", "intro", "my_questions", "missed_followups", "feedback",
            "decisions", "action_items", "open_questions")
ID_PREFIX = {"summary": "S", "lecture": "L", "about_them": "P", "intro": "I", "my_questions": "Y",
             "missed_followups": "F", "feedback": "K", "decisions": "D", "action_items": "A", "open_questions": "Q"}
STYLES = ("lecture", "meeting", "conversation")
# Lecture notes: each item sits under a topic heading and is one of these.
LECTURE_KINDS = ("point", "definition", "example", "fact", "stressed")
# The kinds each section allows; the first is the default.
SECTION_KINDS = {"lecture": LECTURE_KINDS, "my_questions": ("open", "closed"), "feedback": ("improve", "strength")}
# Sections whose items can carry a better version: a sharper intro or question, or the follow-up to ask.
SUGGESTING = {"intro", "my_questions", "missed_followups"}
VERDICTS = ("supported", "partial", "unsupported")
MAX_SUMMARY = 5
MAX_TOPICS = 5
MAX_MISSED = 3

_TIMESTAMP = re.compile(r"\[?\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*\]?")
_EMPTY_VALUES = {"", "null", "none", "n/a", "unknown", "-"}


@dataclass
class NoteItem:
    id: str
    text: str
    cites: list[float] = field(default_factory=list)  # seconds into the recording
    owner: str | None = None   # action items only
    due: str | None = None     # action items only
    verdict: str | None = None  # supported | partial | unsupported | None (unchecked)
    reason: str | None = None
    edited: bool = False  # changed by hand or through Ask; the checker's verdict no longer applies
    heading: str | None = None  # lecture items: the topic it belongs to
    kind: str | None = None     # lecture items: one of LECTURE_KINDS
    term: str | None = None     # lecture definitions: the term being defined
    suggestion: str | None = None  # coaching: a better intro or question, or the follow-up you could have asked


@dataclass
class Notes:
    summary: list[NoteItem] = field(default_factory=list)
    lecture: list[NoteItem] = field(default_factory=list)  # lecture notes, in the order taught
    decisions: list[NoteItem] = field(default_factory=list)
    action_items: list[NoteItem] = field(default_factory=list)
    open_questions: list[NoteItem] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    missed: list[NoteItem] = field(default_factory=list)  # added by the checker
    # conversations (coffee chats): who you talked with, and a thank-you message to send
    about_them: list[NoteItem] = field(default_factory=list)
    intro: list[NoteItem] = field(default_factory=list)
    my_questions: list[NoteItem] = field(default_factory=list)
    missed_followups: list[NoteItem] = field(default_factory=list)
    feedback: list[NoteItem] = field(default_factory=list)
    person: str | None = None
    follow_up_draft: str | None = None
    attendees: list[str] = field(default_factory=list)  # meetings: who took part, by name

    def items(self) -> list[NoteItem]:
        return [item for section in SECTIONS for item in getattr(self, section)]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Notes":
        lists = {name: [NoteItem(**item) for item in data.get(name, [])] for name in (*SECTIONS, "missed")}
        return cls(**lists, topics=list(data.get("topics", [])), person=data.get("person"),
                   follow_up_draft=data.get("follow_up_draft"), attendees=list(data.get("attendees") or []))

    def plain_text(self) -> str:
        """Flattened text used for search."""
        return "\n".join([item.text for item in self.items()] + [item.text for item in self.missed] + self.topics
                         + self.attendees)


def parse_timestamp(text: str) -> float | None:
    match = _TIMESTAMP.fullmatch(str(text).strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return float(int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds))


def normalize_topics(topics) -> list[str]:
    names: list[str] = []
    for topic in topics or []:
        name = " ".join(str(topic).lower().split())
        if name and name not in names:
            names.append(name)
    return names[:MAX_TOPICS]


def normalize_attendees(names) -> list[str]:
    """Names only: no empties or repeats, and never the You and Others labels of a recorded call."""
    seen: list[str] = []
    for name in names or []:
        clean = " ".join(str(name).split())
        if clean and clean.lower() not in {"you", "others", "other", "speaker", *(s.lower() for s in seen)}:
            seen.append(clean)
    return seen[:12]


def notes_from_writer(data: dict) -> Notes:
    notes = Notes()
    for section in SECTIONS:
        raw_items = [raw for raw in data.get(section) or [] if isinstance(raw, dict) and _clean(raw.get("text"))]
        if section == "summary":
            raw_items = raw_items[:MAX_SUMMARY]
        items = [_item(f"{ID_PREFIX[section]}{n}", raw) for n, raw in enumerate(raw_items, 1)]
        if section == "action_items":
            for item, raw in zip(items, raw_items, strict=True):
                item.owner, item.due = _clean(raw.get("owner")), _clean(raw.get("due"))
        if section in SECTION_KINDS and section != "lecture":
            for item, raw in zip(items, raw_items, strict=True):
                kind = (_clean(raw.get("kind")) or "").lower()
                item.kind = kind if kind in SECTION_KINDS[section] else SECTION_KINDS[section][0]
        if section in SUGGESTING:
            for item, raw in zip(items, raw_items, strict=True):
                item.suggestion = _clean(raw.get("suggestion"))
        if section == "lecture":
            spelling: dict[str, str] = {}  # one spelling per topic, so "classical ai" and "classical AI" group
            for item, raw in zip(items, raw_items, strict=True):
                kind = _clean(raw.get("kind"))
                item.kind = kind.lower() if kind and kind.lower() in LECTURE_KINDS else "point"
                heading = _clean(raw.get("heading"))
                if heading:
                    heading = spelling.setdefault(heading.lower(), heading[0].upper() + heading[1:])
                item.heading = heading
                item.term = _clean(raw.get("term")) if item.kind == "definition" else None
        setattr(notes, section, items)
    notes.topics = normalize_topics(data.get("topics"))
    notes.person = _clean(data.get("person"))
    draft = str(data.get("follow_up_draft") or "").strip()
    notes.follow_up_draft = draft or None
    notes.attendees = normalize_attendees(data.get("attendees"))
    return notes


def missed_from_checker(raw_items) -> list[NoteItem]:
    valid = [raw for raw in raw_items or [] if isinstance(raw, dict) and _clean(raw.get("text"))][:MAX_MISSED]
    return [_item(f"M{n}", raw) for n, raw in enumerate(valid, 1)]


def _item(item_id: str, raw: dict) -> NoteItem:
    cites = {seconds for cite in raw.get("cites") or [] if (seconds := parse_timestamp(cite)) is not None}
    return NoteItem(item_id, _clean(raw.get("text")) or "", sorted(cites))


def _clean(value) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return None if text.lower() in _EMPTY_VALUES else text


_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_CITES = {"type": "array", "items": {"type": "string"}}
_POINT = {"type": "object", "properties": {"text": {"type": "string"}, "cites": _CITES},
          "required": ["text", "cites"], "additionalProperties": False}
_ACTION = {"type": "object",
           "properties": {"text": {"type": "string"}, "owner": _NULLABLE_STRING, "due": _NULLABLE_STRING,
                          "cites": _CITES},
           "required": ["text", "owner", "due", "cites"], "additionalProperties": False}

WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": _POINT},
        "decisions": {"type": "array", "items": _POINT},
        "action_items": {"type": "array", "items": _ACTION},
        "open_questions": {"type": "array", "items": _POINT},
        "topics": {"type": "array", "items": {"type": "string"}},
        "attendees": {"type": "array", "items": {"type": "string"}},
        "follow_up_draft": {"type": "string"},
    },
    "required": ["summary", "decisions", "action_items", "open_questions", "topics", "attendees", "follow_up_draft"],
    "additionalProperties": False,
}

_LECTURE_ITEM = {"type": "object",
                 "properties": {"heading": {"type": "string"}, "kind": {"type": "string", "enum": list(LECTURE_KINDS)},
                                "term": _NULLABLE_STRING, "text": {"type": "string"}, "cites": _CITES},
                 "required": ["heading", "kind", "term", "text", "cites"], "additionalProperties": False}

LECTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": _POINT},
        "lecture": {"type": "array", "items": _LECTURE_ITEM},
        "action_items": {"type": "array", "items": _ACTION},
        "open_questions": {"type": "array", "items": _POINT},
        "topics": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "lecture", "action_items", "open_questions", "topics"],
    "additionalProperties": False,
}

_SUGGESTED = {"type": "object", "properties": {"text": {"type": "string"}, "suggestion": _NULLABLE_STRING, "cites": _CITES},
              "required": ["text", "suggestion", "cites"], "additionalProperties": False}
_QUESTION = {"type": "object",
             "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": list(SECTION_KINDS["my_questions"])},
                            "suggestion": _NULLABLE_STRING, "cites": _CITES},
             "required": ["text", "kind", "suggestion", "cites"], "additionalProperties": False}
_FEEDBACK = {"type": "object",
             "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": list(SECTION_KINDS["feedback"])},
                            "cites": _CITES},
             "required": ["text", "kind", "cites"], "additionalProperties": False}
CONVERSATION_PROPERTIES = {
    "about_them": {"type": "array", "items": _POINT},
    "intro": {"type": "array", "items": _SUGGESTED},
    "my_questions": {"type": "array", "items": _QUESTION},
    "missed_followups": {"type": "array", "items": _SUGGESTED},
    "feedback": {"type": "array", "items": _FEEDBACK},
}

# Everything any style can hold: used when Ask proposes changes to the notes.
FULL_SCHEMA = {
    "type": "object",
    "properties": {**WRITER_SCHEMA["properties"], "lecture": LECTURE_SCHEMA["properties"]["lecture"],
                   **CONVERSATION_PROPERTIES},
    "required": [*WRITER_SCHEMA["required"], "lecture", *CONVERSATION_PROPERTIES],
    "additionalProperties": False,
}

CHECKER_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "verdict": {"type": "string", "enum": list(VERDICTS)},
                           "reason": {"type": "string"}},
            "required": ["id", "verdict", "reason"]}},
        "missed": {"type": "array", "items": {
            "type": "object", "properties": {"text": {"type": "string"}, "cites": _CITES},
            "required": ["text", "cites"]}},
    },
    "required": ["verdicts", "missed"],
}
