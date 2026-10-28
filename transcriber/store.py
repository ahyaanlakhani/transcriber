"""SQLite persistence for recordings, transcripts, subjects, topics, notes and settings."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import DEFAULT_LANGUAGE
from .notes.model import Notes
from .followups_store import FollowUpStoreMixin
from .learn_store import LearnStoreMixin
from .memory_store import MemoryStoreMixin
from .projects_store import ProjectsStoreMixin
from .translation_store import TranslationStoreMixin
from .privacy_store import STANDARD, PrivacyStoreMixin
from .providers.base import Segment

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS recordings (
  id                TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  subject           TEXT,
  language          TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  duration_sec      REAL,
  status            TEXT NOT NULL,
  progress_done     INTEGER NOT NULL DEFAULT 0,
  progress_total    INTEGER NOT NULL DEFAULT 0,
  status_detail     TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transcripts (
  recording_id  TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
  text          TEXT NOT NULL,
  segments_json TEXT NOT NULL,
  model         TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

MIGRATION_V2 = [
    """CREATE TABLE subjects (
         id          INTEGER PRIMARY KEY,
         name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
         description TEXT NOT NULL DEFAULT '',
         created_at  TEXT NOT NULL)""",
    "ALTER TABLE recordings ADD COLUMN subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL",
    "ALTER TABLE recordings ADD COLUMN my_notes TEXT NOT NULL DEFAULT ''",
    """INSERT OR IGNORE INTO subjects (name, created_at)
         SELECT trim(subject), :now FROM recordings WHERE subject IS NOT NULL AND trim(subject) != ''
         ORDER BY rowid""",
    """UPDATE recordings SET subject_id = (SELECT id FROM subjects WHERE subjects.name = trim(recordings.subject))
         WHERE subject IS NOT NULL""",
    "ALTER TABLE recordings DROP COLUMN subject",
    """CREATE TABLE topics (
         id   INTEGER PRIMARY KEY,
         name TEXT NOT NULL UNIQUE COLLATE NOCASE)""",
    """CREATE TABLE recording_topics (
         recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
         topic_id     INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
         state        TEXT NOT NULL,
         PRIMARY KEY (recording_id, topic_id))""",
    """CREATE TABLE ai_notes (
         recording_id  TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
         status        TEXT NOT NULL,
         status_detail TEXT,
         notes_json    TEXT,
         notes_text    TEXT NOT NULL DEFAULT '',
         writer        TEXT,
         checker       TEXT,
         check_detail  TEXT,
         updated_at    TEXT NOT NULL)""",
    # Recordings transcribed before AI notes existed get notes too, once a notes model is configured.
    "INSERT INTO ai_notes (recording_id, status, updated_at) SELECT recording_id, 'queued', :now FROM transcripts",
]
MIGRATION_V3 = [
    """CREATE TABLE chat_messages (
         id             INTEGER PRIMARY KEY,
         recording_id   TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
         role           TEXT NOT NULL,
         content        TEXT NOT NULL,
         engine         TEXT,
         note           TEXT,
         proposal_json  TEXT,
         proposal_state TEXT,
         created_at     TEXT NOT NULL)""",
]
MIGRATION_V4 = [
    "ALTER TABLE subjects ADD COLUMN instructions TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE recordings ADD COLUMN instructions TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE recordings ADD COLUMN part_starts TEXT",  # JSON list of seconds, for combined sessions
]
MIGRATION_V5 = [
    "ALTER TABLE recordings ADD COLUMN want_speakers INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE recordings ADD COLUMN speakers TEXT",  # JSON {raw label: display name}
    """CREATE TABLE usage (
         id       INTEGER PRIMARY KEY,
         at       TEXT NOT NULL,
         seconds  REAL NOT NULL,
         provider TEXT NOT NULL)""",
    "CREATE INDEX usage_at ON usage (provider, at)",
]
MIGRATION_V6 = [
    "ALTER TABLE recordings ADD COLUMN starred INTEGER NOT NULL DEFAULT 0",
]
MIGRATION_V7 = [
    "ALTER TABLE recordings ADD COLUMN merge_sources TEXT",  # JSON list of recording ids (merge devices)
    "ALTER TABLE recordings ADD COLUMN merged_from TEXT",  # JSON [{id, title, share}] once merged
]
MIGRATION_V8 = [
    "ALTER TABLE recordings ADD COLUMN deleted_at TEXT",  # set while in Recently Deleted
]
MIGRATION_V9 = [
    "ALTER TABLE recordings ADD COLUMN recorded_at TEXT",  # when it was recorded, from the file's own stamp
]
MIGRATION_V10 = [
    "ALTER TABLE subjects ADD COLUMN notes_style TEXT NOT NULL DEFAULT 'lecture'",  # lecture | meeting
    "ALTER TABLE recordings ADD COLUMN notes_style TEXT",  # NULL: use the subject's
]
MIGRATION_V11 = [
    """CREATE TABLE recaps (
         recording_id  TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
         status        TEXT NOT NULL,  -- queued | writing | speaking | ready | failed
         status_detail TEXT,
         script        TEXT,
         voice         TEXT,
         speed         REAL,
         duration_sec  REAL,
         created_at    TEXT NOT NULL,
         updated_at    TEXT NOT NULL)""",
]
MIGRATION_V12 = [
    """CREATE TABLE people (
         id         INTEGER PRIMARY KEY,
         name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
         note       TEXT NOT NULL DEFAULT '',
         brief_json TEXT,  -- the latest pre-chat brief
         brief_at   TEXT,
         created_at TEXT NOT NULL)""",
    "ALTER TABLE recordings ADD COLUMN person_id INTEGER REFERENCES people(id) ON DELETE SET NULL",
]
MIGRATION_V13 = [
    "ALTER TABLE subjects ADD COLUMN schedule_json TEXT",  # class timetable: {days, start, minutes, from, until}
]
MIGRATION_V14 = [
    """CREATE TABLE notifications (
         id           INTEGER PRIMARY KEY,
         at           TEXT NOT NULL,
         kind         TEXT NOT NULL,  -- added | transcribed | failed | notes_ready | notes_failed | recap_ready
         recording_id TEXT REFERENCES recordings(id) ON DELETE SET NULL,
         title        TEXT NOT NULL,  -- the title when it happened, shown if the recording is gone
         detail       TEXT,
         seen         INTEGER NOT NULL DEFAULT 0)""",
]
MIGRATION_V15 = [
    "ALTER TABLE recordings ADD COLUMN content_hash TEXT",  # for spotting exact copies
]
MIGRATION_V16 = [
    """CREATE TABLE reminders (
         id         INTEGER PRIMARY KEY,
         at         TEXT NOT NULL,  -- when it comes due (UTC)
         text       TEXT NOT NULL,
         subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
         key        TEXT UNIQUE,    -- set for the automatic class reminders, so each class is nudged once
         fired      INTEGER NOT NULL DEFAULT 0,
         created_at TEXT NOT NULL)""",
]
MIGRATION_V17 = [  # second brain: concepts across every recording, all rebuildable from the notes (IF NOT EXISTS: older
    # databases re-run it in the migration tests)
    """CREATE TABLE IF NOT EXISTS concepts (
         id             INTEGER PRIMARY KEY,
         name           TEXT NOT NULL,
         key            TEXT NOT NULL UNIQUE,  -- normalized name: lowercase, no punctuation, singular
         definition     TEXT,                  -- written on first view, from the mentions
         definition_n   INTEGER,               -- how many mentions it was written from (rewritten when more arrive)
         created_at     TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS concept_aliases (
         key        TEXT PRIMARY KEY,         -- normalized alias, e.g. "rag" for retrieval augmented generation
         alias      TEXT NOT NULL,
         concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS concept_mentions (
         id           INTEGER PRIMARY KEY,
         concept_id   INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
         recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
         item_id      TEXT NOT NULL,          -- the note item, or "topic" for an accepted topic
         kind         TEXT,                   -- point | definition | example | fact | stressed | a section name
         heading      TEXT,
         text         TEXT NOT NULL,
         start        REAL,                   -- seconds into the recording
         UNIQUE (concept_id, recording_id, item_id))""",
    "CREATE INDEX IF NOT EXISTS concept_mentions_recording ON concept_mentions (recording_id)",
    """CREATE TABLE IF NOT EXISTS brain_recordings (
         recording_id TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
         fingerprint  TEXT NOT NULL,          -- what was indexed; a change means index it again
         merged       INTEGER NOT NULL DEFAULT 0,  -- its new concept names went past the model once
         new_concepts TEXT,                   -- JSON ids of the concepts this recording added, for that check
         indexed_at   TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS brain_chunks (
         id           INTEGER PRIMARY KEY,
         recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
         source       TEXT NOT NULL,          -- notes | transcript | mine
         start        REAL,
         text         TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS brain_chunks_recording ON brain_chunks (recording_id)",
]
MIGRATION_V18 = [  # privacy: a class on every recording, where combined ones came from, approvals, a safe send log
    "ALTER TABLE recordings ADD COLUMN privacy_classification TEXT NOT NULL DEFAULT 'standard'"
    " CHECK (privacy_classification IN ('standard', 'private', 'local_only'))",
    "ALTER TABLE recordings ADD COLUMN privacy_floor TEXT NOT NULL DEFAULT 'standard'"
    " CHECK (privacy_floor IN ('standard', 'private', 'local_only'))",  # the strictest class of what it was made from
    "ALTER TABLE recordings ADD COLUMN privacy_provenance TEXT NOT NULL DEFAULT 'known'"
    " CHECK (privacy_provenance IN ('known', 'unknown'))",
    """CREATE TABLE recording_sources (
         recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
         source_id    TEXT NOT NULL,  -- no foreign key: the link outlives an erased source
         PRIMARY KEY (recording_id, source_id))""",
    """CREATE TABLE provider_policies (
         config_id         TEXT PRIMARY KEY,  -- the exact provider and model, e.g. groq:openai/gpt-oss-120b; no keys
         approved_standard INTEGER NOT NULL DEFAULT 0,
         approved_private  INTEGER NOT NULL DEFAULT 0,
         retention         TEXT NOT NULL DEFAULT 'unverified',
         note              TEXT NOT NULL DEFAULT '',
         verified_at       TEXT,
         updated_at        TEXT NOT NULL)""",
    """CREATE TABLE transmissions (
         id              INTEGER PRIMARY KEY,
         at              TEXT NOT NULL,
         feature         TEXT NOT NULL,
         config_id       TEXT NOT NULL,
         recording_ids   TEXT NOT NULL,  -- JSON ids; never any audio or text
         decision        TEXT NOT NULL,  -- allowed | blocked
         reason          TEXT NOT NULL,
         policy_revision INTEGER NOT NULL)""",
    # a concept definition is only fresh while the notes behind it are exactly what it was written from
    "ALTER TABLE concepts ADD COLUMN definition_fp TEXT",
    # lineage we can rebuild from what is stored
    "INSERT OR IGNORE INTO recording_sources (recording_id, source_id) SELECT r.id, j.value FROM recordings r,"
    " json_each(r.merge_sources) j WHERE r.merge_sources IS NOT NULL",
    "INSERT OR IGNORE INTO recording_sources (recording_id, source_id) SELECT r.id, json_extract(j.value, '$.id')"
    " FROM recordings r, json_each(r.merged_from) j WHERE r.merged_from IS NOT NULL"
    " AND json_extract(j.value, '$.id') IS NOT NULL",
    # combined recordings never kept their parts' ids: until you confirm them, they stay off the cloud
    "UPDATE recordings SET privacy_provenance = 'unknown' WHERE part_starts IS NOT NULL"
    " AND id NOT IN (SELECT recording_id FROM recording_sources)",
]
MIGRATION_V19 = [  # Pip's memory: only what you told Pip or approved, with where it came from and when it fades
    """CREATE TABLE pip_memory (
         id                  INTEGER PRIMARY KEY,
         kind                TEXT NOT NULL CHECK (kind IN ('goal', 'preference', 'ongoing', 'relationship')),
         text                TEXT NOT NULL,
         status              TEXT NOT NULL CHECK (status IN ('approved', 'proposed')),
         source_recording_id TEXT,  -- no foreign key: its class still limits where this may be sent
         created_at          TEXT NOT NULL,
         approved_at         TEXT,
         expires_at          TEXT)""",
]
MIGRATION_V20 = [  # follow-ups you ticked off or dismissed, so Today stops showing them
    """CREATE TABLE follow_up_state (
         key   TEXT PRIMARY KEY,
         state TEXT NOT NULL CHECK (state IN ('done', 'dismissed')),
         at    TEXT NOT NULL)""",
]
MIGRATION_V21 = [  # Learn: flashcards made from your notes, with spaced-repetition memory, and your reviews of them
    """CREATE TABLE cards (
         id           INTEGER PRIMARY KEY,
         key          TEXT NOT NULL UNIQUE,  -- from the recording and the note's words: the same note never makes two cards
         recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
         item_id      TEXT,
         kind         TEXT NOT NULL,         -- definition | stressed
         front        TEXT NOT NULL,
         back         TEXT NOT NULL,
         start        REAL,                  -- seconds into the recording, for jumping to the source
         suspended    INTEGER NOT NULL DEFAULT 0,
         created_at   TEXT NOT NULL,
         stability    REAL,
         difficulty   REAL,
         due          TEXT,
         last_review  TEXT,
         first_review TEXT,
         reps         INTEGER NOT NULL DEFAULT 0,
         lapses       INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE reviews (
         id      INTEGER PRIMARY KEY,
         card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
         at      TEXT NOT NULL,
         rating  INTEGER NOT NULL)""",
    "CREATE INDEX cards_due ON cards (due)",
]
MIGRATION_V22 = [  # Projects: recordings, people, concepts and documents tied together, with tasks of their own
    """CREATE TABLE projects (
         id          INTEGER PRIMARY KEY,
         name        TEXT NOT NULL,
         description TEXT NOT NULL DEFAULT '',
         status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'done')),
         created_at  TEXT NOT NULL)""",
    """CREATE TABLE project_links (
         project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         kind       TEXT NOT NULL CHECK (kind IN ('recording', 'person', 'concept', 'document')),
         ref        TEXT NOT NULL,  -- a recording id, a person id, a concept id, or a file path / web address
         label      TEXT NOT NULL DEFAULT '',
         PRIMARY KEY (project_id, kind, ref))""",
    """CREATE TABLE project_tasks (
         id         INTEGER PRIMARY KEY,
         project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         text       TEXT NOT NULL,
         done       INTEGER NOT NULL DEFAULT 0,
         created_at TEXT NOT NULL)""",
]
MIGRATION_V23 = [  # your own note on a concept page; kept when the index is rebuilt
    "ALTER TABLE concepts ADD COLUMN note TEXT NOT NULL DEFAULT ''",
]
MIGRATION_V24 = [  # your choice to show or hide a concept, over the rules that keep generic headings out of the list
    "ALTER TABLE concepts ADD COLUMN visibility TEXT CHECK (visibility IN ('shown', 'hidden'))",
]
MIGRATION_V25 = [  # English translations of transcripts, kept beside the original
    """CREATE TABLE translations (
         recording_id    TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
         status          TEXT NOT NULL CHECK (status IN ('working', 'ready', 'failed')),
         status_detail   TEXT,
         paragraphs_json TEXT,
         model           TEXT,
         progress_done   INTEGER NOT NULL DEFAULT 0,
         progress_total  INTEGER NOT NULL DEFAULT 0,
         updated_at      TEXT NOT NULL)""",
]
SCHEMA_VERSION = 25
MAX_NOTIFICATIONS = 200

_RECORDING_SELECT = """
SELECT r.id, r.title, r.subject_id, s.name AS subject, r.language, r.original_filename, r.duration_sec,
       r.status, r.progress_done, r.progress_total, r.status_detail, r.created_at, r.updated_at,
       n.status AS notes_status, r.want_speakers, r.starred, r.deleted_at, r.recorded_at, r.notes_style,
       r.person_id, p.name AS person, r.content_hash, r.privacy_classification
FROM recordings r
LEFT JOIN subjects s ON s.id = r.subject_id
LEFT JOIN people p ON p.id = r.person_id
LEFT JOIN ai_notes n ON n.recording_id = r.id
"""

DEFAULT_SETTINGS = {"vocabulary": "", "default_language": DEFAULT_LANGUAGE, "prefer_server": "0",
                    # Groq free tier for whisper-large-v3: 7,200 audio-seconds per hour, 28,800 per day.
                    "groq_hour_limit_sec": "7200", "groq_day_limit_sec": "28800",
                    # audio recaps: on after each long recording's notes; Kokoro voice and speed
                    "recaps_on": "1", "recap_voice": "af_heart", "recap_speed": "1.1",
                    # calendar: your time zone (class times and days) and laptop folders to check for phone recordings
                    "time_zone": "UTC", "scan_folders": "",
                    # Pip nudges you shortly before a class that hasn't been recorded (bell and desktop notification)
                    "class_reminders": "1",
                    # second brain: where Export to Obsidian writes its vault ("" = Documents/Transcriber vault)
                    "vault_folder": "",
                    # privacy: the class new recordings start with (standard | private)
                    "default_privacy": "standard"}
_RECAP_FIELDS = {"status", "status_detail", "script", "voice", "speed", "duration_sec"}
_UPDATABLE = {"title", "subject_id", "language", "duration_sec", "status",
              "progress_done", "progress_total", "status_detail", "recorded_at", "notes_style", "person_id", "content_hash"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clean_name(name: str) -> str:
    return " ".join(name.split())


@dataclass
class Recording:
    id: str
    title: str
    subject_id: int | None
    subject: str | None  # the subject's name, for display
    language: str
    original_filename: str
    duration_sec: float | None
    status: str  # queued | converting | transcribing | waiting | done | failed
    progress_done: int
    progress_total: int
    status_detail: str | None
    created_at: str
    updated_at: str
    notes_status: str | None  # queued | writing | checking | ready | failed | None
    want_speakers: bool = False
    starred: bool = False
    deleted_at: str | None = None  # in Recently Deleted since then
    recorded_at: str | None = None  # when it was recorded, if known (created_at is when it reached the app)
    notes_style: str | None = None  # lecture | meeting chosen for this recording; None follows the subject
    person_id: int | None = None  # who a coffee chat was with
    person: str | None = None
    content_hash: str | None = None  # sha256 of the audio file; "" when the file is gone
    privacy_classification: str = STANDARD  # standard | private | local_only: which providers may see it

    def __post_init__(self):
        self.want_speakers = bool(self.want_speakers)
        self.starred = bool(self.starred)


@dataclass
class Person:
    id: int
    name: str
    note: str
    count: int
    last_at: str | None
    brief: dict | None = None
    brief_at: str | None = None


@dataclass
class Recap:
    recording_id: str
    status: str  # queued | writing | speaking | ready | failed
    status_detail: str | None
    script: str | None
    voice: str | None
    speed: float | None
    duration_sec: float | None
    created_at: str
    updated_at: str


@dataclass
class Transcript:
    recording_id: str
    text: str
    segments: list[Segment]
    model: str
    created_at: str


@dataclass
class Subject:
    id: int
    name: str
    description: str
    count: int
    instructions: str = ""
    notes_style: str = "lecture"
    schedule: dict | None = None  # when the class meets, for the calendar


@dataclass
class Topic:
    id: int
    name: str
    count: int


@dataclass
class NotesRecord:
    recording_id: str
    status: str
    status_detail: str | None
    notes: Notes | None
    writer: str | None
    checker: str | None
    check_detail: str | None
    updated_at: str


class DuplicateName(ValueError):
    """A subject or topic with that name already exists."""


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Store(PrivacyStoreMixin, MemoryStoreMixin, FollowUpStoreMixin, LearnStoreMixin, ProjectsStoreMixin,
            TranslationStoreMixin):
    """Thread-safe: one connection shared by the API threads and the worker, guarded by a lock."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._path = db_path
        self._backup_before_upgrade()
        self._migrate()

    def _backup_before_upgrade(self) -> None:
        """An existing database about to be upgraded is copied first (data/backups), so a bad upgrade can be undone."""
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if not 0 < version < SCHEMA_VERSION:
            return
        folder = self._path.parent / "backups"
        folder.mkdir(exist_ok=True)
        target = sqlite3.connect(folder / f"{self._path.stem}-v{version}-{datetime.now():%Y%m%d-%H%M%S}.db")
        try:
            self._conn.backup(target)
        finally:
            target.close()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self._conn.executescript(SCHEMA_V1)
            self._conn.execute("PRAGMA user_version = 1")
        if version < 2:
            self._conn.execute("BEGIN")
            try:
                for statement in MIGRATION_V2:
                    self._conn.execute(statement, {"now": now_iso()} if ":now" in statement else ())
                self._conn.execute("PRAGMA user_version = 2")
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        if version < 3:
            self._conn.execute("BEGIN")
            try:
                for statement in MIGRATION_V3:
                    self._conn.execute(statement)
                self._conn.execute("PRAGMA user_version = 3")
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        for target, statements in ((4, MIGRATION_V4), (5, MIGRATION_V5), (6, MIGRATION_V6),
                                   (7, MIGRATION_V7), (8, MIGRATION_V8), (9, MIGRATION_V9), (10, MIGRATION_V10), (11, MIGRATION_V11), (12, MIGRATION_V12), (13, MIGRATION_V13), (14, MIGRATION_V14), (15, MIGRATION_V15), (16, MIGRATION_V16), (17, MIGRATION_V17),
                                   (18, MIGRATION_V18), (19, MIGRATION_V19), (20, MIGRATION_V20), (21, MIGRATION_V21), (22, MIGRATION_V22), (23, MIGRATION_V23), (24, MIGRATION_V24), (25, MIGRATION_V25)):
            if version >= target:
                continue
            self._conn.execute("BEGIN")
            try:
                for statement in statements:
                    self._conn.execute(statement)
                self._conn.execute(f"PRAGMA user_version = {target}")
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def backup_to(self, target: Path) -> None:
        """A consistent copy of the whole database, safe while the app is using it."""
        copy = sqlite3.connect(target)
        try:
            with self._lock:
                self._conn.backup(copy)
        finally:
            copy.close()

    def _query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def _insert(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).lastrowid

    @contextmanager
    def transaction(self):
        """The connection, locked, inside one transaction: for jobs that rewrite many rows at once (the brain index)."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        """Read-only SQL for modules that keep their own tables (the brain)."""
        return self._query(sql, params)

    # --- recordings

    def create_recording(self, id: str, title: str, subject_id: int | None, language: str,
                         original_filename: str, want_speakers: bool = False) -> Recording:
        ts = now_iso()
        privacy = self.get_settings().get("default_privacy")
        self._execute(
            "INSERT INTO recordings (id, title, subject_id, language, original_filename, status,"
            " created_at, updated_at, want_speakers, privacy_classification) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
            (id, title, subject_id, language, original_filename, ts, ts, int(want_speakers),
             privacy if privacy in ("standard", "private") else "standard"),
        )
        self.notify("added", id)
        return self.get_recording(id)

    def recordings_missing_hash(self) -> list[str]:
        return [row["id"] for row in self._query("SELECT id FROM recordings WHERE content_hash IS NULL")]

    def get_recording(self, id: str) -> Recording | None:
        rows = self._query(_RECORDING_SELECT + " WHERE r.id = ?", (id,))
        return Recording(**dict(rows[0])) if rows else None

    def list_recordings(self, q: str | None = None, subject_id: int | None = None,
                        topic_id: int | None = None, starred: bool = False, deleted: bool = False,
                        no_subject: bool = False, person_id: int | None = None) -> list[Recording]:
        sql = _RECORDING_SELECT + " LEFT JOIN transcripts t ON t.recording_id = r.id"
        where: list[str] = []
        params: list = []
        if q:
            pattern = f"%{_escape_like(q)}%"
            where.append("(r.title LIKE ? ESCAPE '\\' OR t.text LIKE ? ESCAPE '\\'"
                         " OR n.notes_text LIKE ? ESCAPE '\\' OR r.my_notes LIKE ? ESCAPE '\\'"
                         " OR r.original_filename LIKE ? ESCAPE '\\')")
            params += [pattern] * 5
        if subject_id is not None:
            where.append("r.subject_id = ?")
            params.append(subject_id)
        if topic_id is not None:
            where.append("EXISTS (SELECT 1 FROM recording_topics rt WHERE rt.recording_id = r.id"
                         " AND rt.topic_id = ? AND rt.state = 'accepted')")
            params.append(topic_id)
        if starred:
            where.append("r.starred = 1")
        if person_id is not None:
            where.append("r.person_id = ?")
            params.append(person_id)
        if no_subject:
            where.append("r.subject_id IS NULL")
        where.append("r.deleted_at IS NOT NULL" if deleted else "r.deleted_at IS NULL")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(r.recorded_at, r.created_at) DESC, r.rowid DESC"
        return [Recording(**dict(row)) for row in self._query(sql, tuple(params))]

    def update_recording(self, id: str, /, **fields) -> None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"Cannot update {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self._execute(f"UPDATE recordings SET {assignments}, updated_at = ? WHERE id = ?",
                      (*fields.values(), now_iso(), id))

    def set_starred(self, id: str, starred: bool) -> None:
        self._execute("UPDATE recordings SET starred = ? WHERE id = ?", (int(starred), id))

    def recording_counts(self) -> dict[str, int]:
        row = self._query(
            "SELECT COALESCE(SUM(deleted_at IS NULL), 0) AS total,"
            " COALESCE(SUM(starred = 1 AND deleted_at IS NULL), 0) AS starred,"
            " COALESCE(SUM(subject_id IS NULL AND deleted_at IS NULL), 0) AS no_subject,"
            " COALESCE(SUM(deleted_at IS NOT NULL), 0) AS deleted FROM recordings")[0]
        return {"all": row["total"], "starred": row["starred"], "no_subject": row["no_subject"],
                "deleted": row["deleted"]}

    # --- Recently Deleted

    def soft_delete(self, id: str, at: str | None = None) -> bool:
        return self._execute("UPDATE recordings SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                             (at or now_iso(), id)) > 0

    def restore(self, id: str) -> bool:
        return self._execute("UPDATE recordings SET deleted_at = NULL WHERE id = ?", (id,)) > 0

    def purge_deleted(self, before: str | None = None) -> list[str]:
        """Erase recordings deleted before `before` (all deleted ones if None). Returns their ids."""
        sql = "SELECT id FROM recordings WHERE deleted_at IS NOT NULL" + (" AND deleted_at < ?" if before else "")
        ids = [row["id"] for row in self._query(sql, (before,) if before else ())]
        for rid in ids:
            self.delete_recording(rid)
        return ids

    def delete_recording(self, id: str) -> bool:
        return self._execute("DELETE FROM recordings WHERE id = ?", (id,)) > 0

    def move_recordings(self, ids: list[str], subject_id: int | None) -> int:
        moved = 0
        for rid in ids:
            moved += self._execute("UPDATE recordings SET subject_id = ?, updated_at = ? WHERE id = ?",
                                   (subject_id, now_iso(), rid))
        return moved

    def next_queued(self) -> Recording | None:
        rows = self._query(_RECORDING_SELECT + " WHERE r.status = 'queued' AND r.deleted_at IS NULL"
                                               " ORDER BY r.created_at, r.rowid LIMIT 1")
        return Recording(**dict(rows[0])) if rows else None

    def reset_interrupted(self) -> int:
        self._execute("UPDATE ai_notes SET status = 'queued', status_detail = NULL, updated_at = ?"
                      " WHERE status IN ('writing', 'checking')", (now_iso(),))
        self._execute("UPDATE recaps SET status = 'queued', status_detail = NULL, updated_at = ?"
                      " WHERE status IN ('writing', 'speaking')", (now_iso(),))
        self._execute("UPDATE translations SET status = 'failed', status_detail = 'Interrupted: translate it again',"
                      " updated_at = ? WHERE status = 'working'", (now_iso(),))
        return self._execute(
            "UPDATE recordings SET status = 'queued', status_detail = NULL, updated_at = ?"
            " WHERE status IN ('converting', 'transcribing', 'waiting')",
            (now_iso(),),
        )

    # --- transcripts

    def save_transcript(self, id: str, text: str, segments: list[Segment], model: str) -> None:
        ts = now_iso()
        segments_json = json.dumps([s.to_dict() for s in segments])
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO transcripts (recording_id, text, segments_json, model, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (id, text, segments_json, model, ts),
                )
                self._conn.execute(
                    "UPDATE recordings SET status = 'done', status_detail = NULL, updated_at = ? WHERE id = ?",
                    (ts, id),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def get_transcript(self, id: str) -> Transcript | None:
        rows = self._query("SELECT * FROM transcripts WHERE recording_id = ?", (id,))
        if not rows:
            return None
        row = rows[0]
        segments = [Segment.from_dict(d) for d in json.loads(row["segments_json"])]
        return Transcript(row["recording_id"], row["text"], segments, row["model"], row["created_at"])

    def delete_transcript(self, id: str) -> None:
        self._execute("DELETE FROM transcripts WHERE recording_id = ?", (id,))

    # --- subjects

    def list_subjects(self) -> list[Subject]:
        rows = self._query("SELECT s.id, s.name, s.description, s.instructions, s.notes_style, s.schedule_json, COUNT(r.id) AS count"
                           " FROM subjects s"
                           " LEFT JOIN recordings r ON r.subject_id = s.id AND r.deleted_at IS NULL"
                           " GROUP BY s.id ORDER BY s.name COLLATE NOCASE")
        return [Subject(**{k: v for k, v in dict(row).items() if k != "schedule_json"},
                        schedule=json.loads(row["schedule_json"]) if row["schedule_json"] else None) for row in rows]

    def get_subject(self, id: int) -> Subject | None:
        return next((s for s in self.list_subjects() if s.id == id), None)

    def get_or_create_subject(self, name: str) -> int:
        name = clean_name(name)
        self._execute("INSERT OR IGNORE INTO subjects (name, created_at) VALUES (?, ?)", (name, now_iso()))
        return self._query("SELECT id FROM subjects WHERE name = ?", (name,))[0]["id"]

    def create_subject(self, name: str, description: str = "") -> Subject:
        name = clean_name(name)
        if self._query("SELECT 1 FROM subjects WHERE name = ?", (name,)):
            raise DuplicateName(f"A subject called '{name}' already exists")
        new_id = self._insert("INSERT INTO subjects (name, description, created_at) VALUES (?, ?, ?)",
                              (name, description.strip(), now_iso()))
        return self.get_subject(new_id)

    def update_subject(self, id: int, name: str | None = None, description: str | None = None,
                       instructions: str | None = None, notes_style: str | None = None,
                       schedule: dict | None | object = ...) -> bool:
        if not self._query("SELECT 1 FROM subjects WHERE id = ?", (id,)):
            return False
        if name is not None:
            name = clean_name(name)
            if self._query("SELECT 1 FROM subjects WHERE name = ? AND id != ?", (name, id)):
                raise DuplicateName(f"A subject called '{name}' already exists")
            self._execute("UPDATE subjects SET name = ? WHERE id = ?", (name, id))
        if description is not None:
            self._execute("UPDATE subjects SET description = ? WHERE id = ?", (description.strip(), id))
        if instructions is not None:
            self._execute("UPDATE subjects SET instructions = ? WHERE id = ?", (instructions.strip(), id))
        if notes_style is not None:
            self._execute("UPDATE subjects SET notes_style = ? WHERE id = ?", (notes_style, id))
        if schedule is not ...:  # None clears the timetable
            self._execute("UPDATE subjects SET schedule_json = ? WHERE id = ?",
                          (json.dumps(schedule) if schedule else None, id))
        return True

    def delete_subject(self, id: int) -> bool:
        return self._execute("DELETE FROM subjects WHERE id = ?", (id,)) > 0

    # --- topics

    def list_topics(self) -> list[Topic]:
        rows = self._query("SELECT t.id, t.name, COUNT(rt.recording_id) AS count FROM topics t"
                           " JOIN recording_topics rt ON rt.topic_id = t.id AND rt.state = 'accepted'"
                           " JOIN recordings r ON r.id = rt.recording_id AND r.deleted_at IS NULL"
                           " GROUP BY t.id ORDER BY t.name COLLATE NOCASE")
        return [Topic(**dict(row)) for row in rows]

    def list_topic_names(self) -> list[str]:
        return [topic.name for topic in self.list_topics()]

    def get_or_create_topic(self, name: str) -> int:
        name = clean_name(name)
        self._execute("INSERT OR IGNORE INTO topics (name) VALUES (?)", (name,))
        return self._query("SELECT id FROM topics WHERE name = ?", (name,))[0]["id"]

    def rename_topic(self, id: int, name: str) -> int | None:
        """Rename a topic; if another topic already has that name, merge into it. Returns the surviving id."""
        if not self._query("SELECT 1 FROM topics WHERE id = ?", (id,)):
            return None
        name = clean_name(name)
        other = self._query("SELECT id FROM topics WHERE name = ? AND id != ?", (name, id))
        if not other:
            self._execute("UPDATE topics SET name = ? WHERE id = ?", (name, id))
            return id
        target = other[0]["id"]
        for row in self._query("SELECT recording_id, state FROM recording_topics WHERE topic_id = ?", (id,)):
            self._link(row["recording_id"], target, row["state"])
        self._execute("DELETE FROM topics WHERE id = ?", (id,))
        return target

    def delete_topic(self, id: int) -> bool:
        return self._execute("DELETE FROM topics WHERE id = ?", (id,)) > 0

    def recording_topics(self, recording_id: str) -> list[dict]:
        rows = self._query("SELECT t.id, t.name, rt.state FROM recording_topics rt JOIN topics t ON t.id = rt.topic_id"
                           " WHERE rt.recording_id = ? ORDER BY rt.state, t.name COLLATE NOCASE", (recording_id,))
        return [dict(row) for row in rows]

    def _link(self, recording_id: str, topic_id: int, state: str) -> None:
        # Never downgrade an accepted topic back to a suggestion.
        self._execute("INSERT INTO recording_topics (recording_id, topic_id, state) VALUES (?, ?, ?)"
                      " ON CONFLICT(recording_id, topic_id) DO UPDATE SET state ="
                      " CASE WHEN excluded.state = 'accepted' THEN 'accepted' ELSE recording_topics.state END",
                      (recording_id, topic_id, state))

    def add_topic(self, recording_id: str, name: str, state: str = "accepted") -> int:
        topic_id = self.get_or_create_topic(name)
        self._link(recording_id, topic_id, state)
        return topic_id

    def suggest_topics(self, recording_id: str, names: list[str]) -> None:
        for name in names:
            if clean_name(name):
                self.add_topic(recording_id, name, "suggested")

    def accept_topic(self, recording_id: str, topic_id: int) -> bool:
        return self._execute("UPDATE recording_topics SET state = 'accepted' WHERE recording_id = ? AND topic_id = ?",
                             (recording_id, topic_id)) > 0

    def remove_topic(self, recording_id: str, topic_id: int) -> bool:
        return self._execute("DELETE FROM recording_topics WHERE recording_id = ? AND topic_id = ?",
                             (recording_id, topic_id)) > 0

    # --- notes instructions and combined sessions

    def set_recording_instructions(self, recording_id: str, text: str) -> bool:
        return self._execute("UPDATE recordings SET instructions = ? WHERE id = ?",
                             (text.strip(), recording_id)) > 0

    def notes_instructions(self, recording_id: str) -> tuple[str, str]:
        """(subject instructions, recording instructions) used to steer the notes and Ask models."""
        rows = self._query("SELECT COALESCE(s.instructions, '') AS subject_text, r.instructions AS recording_text"
                           " FROM recordings r LEFT JOIN subjects s ON s.id = r.subject_id WHERE r.id = ?",
                           (recording_id,))
        return (rows[0]["subject_text"], rows[0]["recording_text"]) if rows else ("", "")

    def notes_style(self, recording_id: str) -> str:
        """lecture or meeting: the recording's own choice, else its subject's, else lecture."""
        rows = self._query("SELECT COALESCE(r.notes_style, s.notes_style, 'lecture') AS style FROM recordings r"
                           " LEFT JOIN subjects s ON s.id = r.subject_id WHERE r.id = ?", (recording_id,))
        return rows[0]["style"] if rows else "lecture"

    def in_recording_order(self, ids: list[str]) -> list[str]:
        """The given ids sorted by when they were added (insertion order breaks same-second ties)."""
        marks = ",".join("?" * len(ids))
        rows = self._query(f"SELECT id FROM recordings WHERE id IN ({marks}) ORDER BY created_at, rowid", tuple(ids))
        return [row["id"] for row in rows]

    def set_part_starts(self, recording_id: str, starts: list[float]) -> None:
        self._execute("UPDATE recordings SET part_starts = ? WHERE id = ?", (json.dumps(starts), recording_id))

    def get_part_starts(self, recording_id: str) -> list[float] | None:
        rows = self._query("SELECT part_starts FROM recordings WHERE id = ?", (recording_id,))
        return json.loads(rows[0]["part_starts"]) if rows and rows[0]["part_starts"] else None

    # --- my notes

    def get_my_notes(self, recording_id: str) -> str | None:
        rows = self._query("SELECT my_notes FROM recordings WHERE id = ?", (recording_id,))
        return rows[0]["my_notes"] if rows else None

    def set_my_notes(self, recording_id: str, text: str) -> bool:
        return self._execute("UPDATE recordings SET my_notes = ? WHERE id = ?", (text, recording_id)) > 0

    # --- AI notes

    def queue_notes(self, recording_id: str) -> None:
        self._execute("INSERT INTO ai_notes (recording_id, status, updated_at) VALUES (?, 'queued', ?)"
                      " ON CONFLICT(recording_id) DO UPDATE SET status = 'queued', status_detail = NULL,"
                      " updated_at = excluded.updated_at", (recording_id, now_iso()))

    def next_notes_queued(self) -> str | None:
        rows = self._query("SELECT n.recording_id FROM ai_notes n JOIN transcripts t ON t.recording_id = n.recording_id"
                           " JOIN recordings r ON r.id = n.recording_id AND r.deleted_at IS NULL"
                           " WHERE n.status = 'queued' ORDER BY n.updated_at, n.rowid LIMIT 1")
        return rows[0]["recording_id"] if rows else None

    def update_notes_status(self, recording_id: str, status: str, detail: str | None = None) -> None:
        self._execute("UPDATE ai_notes SET status = ?, status_detail = ?, updated_at = ? WHERE recording_id = ?",
                      (status, detail, now_iso(), recording_id))

    def save_notes(self, recording_id: str, notes: Notes, *, writer: str, checker: str | None,
                   check_detail: str | None) -> None:
        self._execute("UPDATE ai_notes SET status = 'ready', status_detail = NULL, notes_json = ?, notes_text = ?,"
                      " writer = ?, checker = ?, check_detail = ?, updated_at = ? WHERE recording_id = ?",
                      (json.dumps(notes.to_dict(), ensure_ascii=False), notes.plain_text(), writer, checker,
                       check_detail, now_iso(), recording_id))

    def upsert_notes(self, recording_id: str, notes: Notes, *, writer: str | None, checker: str | None,
                     check_detail: str | None) -> None:
        """Store notes as ready, creating the notes record if needed (used for hand edits)."""
        self._execute("INSERT INTO ai_notes (recording_id, status, notes_json, notes_text, writer, checker, check_detail,"
                      " updated_at) VALUES (?, 'ready', ?, ?, ?, ?, ?, ?) ON CONFLICT(recording_id) DO UPDATE SET"
                      " status = 'ready', status_detail = NULL, notes_json = excluded.notes_json,"
                      " notes_text = excluded.notes_text, writer = excluded.writer, checker = excluded.checker,"
                      " check_detail = excluded.check_detail, updated_at = excluded.updated_at",
                      (recording_id, json.dumps(notes.to_dict(), ensure_ascii=False), notes.plain_text(), writer,
                       checker, check_detail, now_iso()))

    def get_notes(self, recording_id: str) -> NotesRecord | None:
        rows = self._query("SELECT * FROM ai_notes WHERE recording_id = ?", (recording_id,))
        if not rows:
            return None
        row = rows[0]
        notes = Notes.from_dict(json.loads(row["notes_json"])) if row["notes_json"] else None
        return NotesRecord(row["recording_id"], row["status"], row["status_detail"], notes, row["writer"],
                           row["checker"], row["check_detail"], row["updated_at"])

    def delete_notes(self, recording_id: str) -> None:
        self._execute("DELETE FROM ai_notes WHERE recording_id = ?", (recording_id,))

    # --- Ask chat

    def add_chat_message(self, recording_id: str, role: str, content: str, *, engine: str | None = None,
                         proposal: dict | None = None, note: str | None = None) -> dict:
        message_id = self._insert(
            "INSERT INTO chat_messages (recording_id, role, content, engine, note, proposal_json, proposal_state,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (recording_id, role, content, engine, note, json.dumps(proposal, ensure_ascii=False) if proposal else None,
             "pending" if proposal else None, now_iso()))
        return self.get_chat_message(recording_id, message_id)

    def list_chat(self, recording_id: str) -> list[dict]:
        rows = self._query("SELECT * FROM chat_messages WHERE recording_id = ? ORDER BY id", (recording_id,))
        return [self._chat_row(row) for row in rows]

    def get_chat_message(self, recording_id: str, message_id: int) -> dict | None:
        rows = self._query("SELECT * FROM chat_messages WHERE recording_id = ? AND id = ?", (recording_id, message_id))
        return self._chat_row(rows[0]) if rows else None

    def set_proposal_state(self, message_id: int, state: str) -> None:
        self._execute("UPDATE chat_messages SET proposal_state = ? WHERE id = ?", (state, message_id))

    def clear_chat(self, recording_id: str) -> None:
        self._execute("DELETE FROM chat_messages WHERE recording_id = ?", (recording_id,))

    @staticmethod
    def _chat_row(row: sqlite3.Row) -> dict:
        return {"id": row["id"], "role": row["role"], "content": row["content"], "engine": row["engine"],
                "note": row["note"],
                "proposal": json.loads(row["proposal_json"]) if row["proposal_json"] else None,
                "proposal_state": row["proposal_state"], "created_at": row["created_at"]}

    # --- speakers

    def set_want_speakers(self, recording_id: str, want: bool) -> None:
        self._execute("UPDATE recordings SET want_speakers = ? WHERE id = ?", (int(want), recording_id))

    def get_speaker_names(self, recording_id: str) -> dict[str, str]:
        rows = self._query("SELECT speakers FROM recordings WHERE id = ?", (recording_id,))
        return json.loads(rows[0]["speakers"]) if rows and rows[0]["speakers"] else {}

    def set_speaker_names(self, recording_id: str, names: dict[str, str]) -> None:
        self._execute("UPDATE recordings SET speakers = ? WHERE id = ?", (json.dumps(names), recording_id))

    # --- merge devices

    def set_merge_sources(self, recording_id: str, ids: list[str]) -> None:
        self._execute("UPDATE recordings SET merge_sources = ? WHERE id = ?", (json.dumps(ids), recording_id))
        self.record_sources(recording_id, ids)  # it can never be less private than what it is made from

    def get_merge_sources(self, recording_id: str) -> list[str] | None:
        rows = self._query("SELECT merge_sources FROM recordings WHERE id = ?", (recording_id,))
        return json.loads(rows[0]["merge_sources"]) if rows and rows[0]["merge_sources"] else None

    def set_merged_from(self, recording_id: str, sources: list[dict]) -> None:
        self._execute("UPDATE recordings SET merged_from = ? WHERE id = ?", (json.dumps(sources), recording_id))
        self.record_sources(recording_id, [s["id"] for s in sources if s.get("id")])

    def get_merged_from(self, recording_id: str) -> list[dict] | None:
        rows = self._query("SELECT merged_from FROM recordings WHERE id = ?", (recording_id,))
        return json.loads(rows[0]["merged_from"]) if rows and rows[0]["merged_from"] else None

    # --- usage

    def record_usage(self, seconds: float, provider: str, at: str | None = None) -> None:
        self._insert("INSERT INTO usage (at, seconds, provider) VALUES (?, ?, ?)", (at or now_iso(), seconds, provider))

    def usage_requests(self, provider: str, since: str) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM usage WHERE provider = ? AND at >= ?", (provider, since))
        return int(rows[0]["n"])

    def usage_events(self, provider: str, since: str) -> list[tuple[datetime, float]]:
        rows = self._query("SELECT at, seconds FROM usage WHERE provider = ? AND at >= ?", (provider, since))
        return [(datetime.fromisoformat(row["at"]), float(row["seconds"])) for row in rows]

    def usage_seconds(self, provider: str, since: str) -> float:
        rows = self._query("SELECT COALESCE(SUM(seconds), 0) AS total FROM usage WHERE provider = ? AND at >= ?",
                           (provider, since))
        return float(rows[0]["total"])

    # --- settings

    # --- people (coffee chats)

    _PEOPLE_SELECT = ("SELECT p.id, p.name, p.note, p.brief_json, p.brief_at, COUNT(r.id) AS count,"
                      " MAX(COALESCE(r.recorded_at, r.created_at)) AS last_at FROM people p"
                      " LEFT JOIN recordings r ON r.person_id = p.id AND r.deleted_at IS NULL")

    @staticmethod
    def _person(row) -> Person:
        data = dict(row)
        brief = data.pop("brief_json")
        return Person(**data, brief=json.loads(brief) if brief else None)

    def list_people(self) -> list[Person]:
        rows = self._query(self._PEOPLE_SELECT + " GROUP BY p.id ORDER BY last_at IS NULL, last_at DESC, p.name")
        return [self._person(row) for row in rows]

    def get_person(self, id: int) -> Person | None:
        rows = self._query(self._PEOPLE_SELECT + " WHERE p.id = ? GROUP BY p.id", (id,))
        return self._person(rows[0]) if rows else None

    def get_or_create_person(self, name: str) -> int:
        name = clean_name(name)
        self._execute("INSERT OR IGNORE INTO people (name, created_at) VALUES (?, ?)", (name, now_iso()))
        return self._query("SELECT id FROM people WHERE name = ?", (name,))[0]["id"]

    def update_person(self, id: int, name: str | None = None, note: str | None = None) -> bool:
        if not self._query("SELECT 1 FROM people WHERE id = ?", (id,)):
            return False
        if name is not None:
            name = clean_name(name)
            if self._query("SELECT 1 FROM people WHERE name = ? AND id != ?", (name, id)):
                raise DuplicateName(f"Someone called '{name}' is already in People")
            self._execute("UPDATE people SET name = ? WHERE id = ?", (name, id))
        if note is not None:
            self._execute("UPDATE people SET note = ? WHERE id = ?", (note.strip(), id))
        return True

    def set_person_brief(self, id: int, brief: dict) -> None:
        self._execute("UPDATE people SET brief_json = ?, brief_at = ? WHERE id = ?",
                      (json.dumps(brief, ensure_ascii=False), now_iso(), id))

    def delete_person(self, id: int) -> bool:
        return self._execute("DELETE FROM people WHERE id = ?", (id,)) > 0

    def conversation_recordings(self) -> list[Recording]:
        """Coffee chats: recordings whose notes are in the Conversation style, oldest first."""
        return [Recording(**dict(row)) for row in self._query(
            _RECORDING_SELECT + " WHERE r.deleted_at IS NULL"
            " AND COALESCE(r.notes_style, s.notes_style, 'lecture') = 'conversation'"
            " ORDER BY COALESCE(r.recorded_at, r.created_at), r.rowid")]

    # --- notifications (the bell)

    def notify(self, kind: str, recording_id: str, detail: str | None = None) -> None:
        rows = self._query("SELECT title FROM recordings WHERE id = ?", (recording_id,))
        if not rows:
            return
        self._insert("INSERT INTO notifications (at, kind, recording_id, title, detail) VALUES (?, ?, ?, ?, ?)",
                     (now_iso(), kind, recording_id, rows[0]["title"], detail))
        self._execute("DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT ?)",
                      (MAX_NOTIFICATIONS,))

    def notify_text(self, kind: str, title: str, detail: str | None = None) -> None:
        """A bell entry that isn't about a recording (a reminder from Pip)."""
        self._insert("INSERT INTO notifications (at, kind, recording_id, title, detail) VALUES (?, ?, NULL, ?, ?)",
                     (now_iso(), kind, title, detail))
        self._execute("DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT ?)",
                      (MAX_NOTIFICATIONS,))

    # --- reminders (Pip)

    def add_reminder(self, at: str, text: str, subject_id: int | None = None, key: str | None = None) -> int | None:
        """None when a reminder with this key already exists."""
        if key and self._query("SELECT 1 FROM reminders WHERE key = ?", (key,)):
            return None
        return self._insert("INSERT INTO reminders (at, text, subject_id, key, created_at) VALUES (?, ?, ?, ?, ?)",
                            (at, text, subject_id, key, now_iso()))

    def list_reminders(self) -> list[dict]:
        """The ones you asked for that haven't come due yet, soonest first."""
        rows = self._query("SELECT r.id, r.at, r.text, r.subject_id, s.name AS subject FROM reminders r"
                           " LEFT JOIN subjects s ON s.id = r.subject_id WHERE r.fired = 0 AND r.key IS NULL ORDER BY r.at")
        return [dict(row) for row in rows]

    def due_reminders(self, now: str) -> list[dict]:
        return [dict(row) for row in self._query(
            "SELECT id, at, text, subject_id, key FROM reminders WHERE fired = 0 AND at <= ? ORDER BY at", (now,))]

    def mark_reminder_fired(self, id: int) -> None:
        self._execute("UPDATE reminders SET fired = 1 WHERE id = ?", (id,))

    def delete_reminder(self, id: int) -> bool:
        return self._execute("DELETE FROM reminders WHERE id = ?", (id,)) > 0

    def list_notifications(self, limit: int = 50) -> list[dict]:
        rows = self._query("SELECT n.id, n.at, n.kind, n.recording_id, COALESCE(r.title, n.title) AS title, n.detail,"
                           " n.seen, s.name AS subject FROM notifications n LEFT JOIN recordings r ON r.id = n.recording_id"
                           " LEFT JOIN subjects s ON s.id = r.subject_id ORDER BY n.id DESC LIMIT ?", (limit,))
        return [{**dict(row), "seen": bool(row["seen"])} for row in rows]

    def unseen_notifications(self) -> int:
        return int(self._query("SELECT COUNT(*) AS n FROM notifications WHERE seen = 0")[0]["n"])

    def mark_notifications_seen(self) -> None:
        self._execute("UPDATE notifications SET seen = 1 WHERE seen = 0")

    def delete_notification(self, id: int) -> bool:
        return self._execute("DELETE FROM notifications WHERE id = ?", (id,)) > 0

    def clear_notifications(self) -> None:
        self._execute("DELETE FROM notifications")

    # --- audio recaps

    def queue_recap(self, recording_id: str) -> None:
        ts = now_iso()
        self._execute("INSERT INTO recaps (recording_id, status, created_at, updated_at) VALUES (?, 'queued', ?, ?)"
                      " ON CONFLICT(recording_id) DO UPDATE SET status = 'queued', status_detail = NULL,"
                      " updated_at = excluded.updated_at", (recording_id, ts, ts))

    def get_recap(self, recording_id: str) -> Recap | None:
        rows = self._query("SELECT * FROM recaps WHERE recording_id = ?", (recording_id,))
        return Recap(**dict(rows[0])) if rows else None

    def update_recap(self, recording_id: str, /, **fields) -> None:
        unknown = set(fields) - _RECAP_FIELDS
        if unknown:
            raise ValueError(f"Unknown recap fields {sorted(unknown)}")
        sets = "".join(f"{key} = ?, " for key in fields)
        self._execute(f"UPDATE recaps SET {sets}updated_at = ? WHERE recording_id = ?",
                      (*fields.values(), now_iso(), recording_id))

    def next_recap_queued(self) -> str | None:
        rows = self._query("SELECT c.recording_id FROM recaps c JOIN recordings r ON r.id = c.recording_id"
                           " WHERE c.status = 'queued' AND r.deleted_at IS NULL ORDER BY c.updated_at, c.rowid LIMIT 1")
        return rows[0]["recording_id"] if rows else None

    def latest_recaps(self) -> list[tuple[Recording, Recap]]:
        """The newest ready recap of each subject, newest first (recordings without a subject left out)."""
        rows = self._query("SELECT r.id, r.subject_id FROM recaps c JOIN recordings r ON r.id = c.recording_id"
                           " WHERE c.status = 'ready' AND r.subject_id IS NOT NULL AND r.deleted_at IS NULL"
                           " ORDER BY COALESCE(r.recorded_at, r.created_at) DESC, r.rowid DESC")
        seen: set[int] = set()
        found = []
        for row in rows:
            if row["subject_id"] not in seen:
                seen.add(row["subject_id"])
                found.append((self.get_recording(row["id"]), self.get_recap(row["id"])))
        return found

    def get_settings(self) -> dict[str, str]:
        rows = self._query("SELECT key, value FROM settings")
        return {**DEFAULT_SETTINGS, **{row["key"]: row["value"] for row in rows}}

    def update_settings(self, values: dict[str, str]) -> None:
        unknown = set(values) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError(f"Unknown settings {sorted(unknown)}")
        for key, value in values.items():
            self._execute("INSERT INTO settings (key, value) VALUES (?, ?)"
                          " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
