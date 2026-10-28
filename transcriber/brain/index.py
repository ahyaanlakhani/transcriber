"""The second brain's index: concepts and their mentions across every recording, and small text chunks for Ask.

Everything here is built from the notes, transcripts and your own notes, so it can be thrown away and rebuilt
(Rebuild does exactly that). Concept names are matched by plain rules first; when a notes model is set up, each
newly indexed recording's new names get one small "same as which existing concept?" check."""
import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from .. import privacy
from ..notes.model import ID_PREFIX, Notes
from ..postprocess import named_paragraphs
from ..providers.base import PermanentError, RateLimited, TransientError
from ..sessions import NOT_CLASSES, class_sessions
from ..store import Store, now_iso
from . import quality
from .names import KNOWN_ALIASES, acronym, initials, key_of, mention_pattern, split_alias, tidy, usable

log = logging.getLogger(__name__)

SECTION_OF = {prefix: section for section, prefix in ID_PREFIX.items()} | {"M": "missed"}
CHUNK_CHARS = 700
MAX_EXISTING_FOR_MERGE = 400
FTS_SETUP = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS brain_fts USING fts5(text, content='brain_chunks', content_rowid='id',"
    " tokenize='porter unicode61 remove_diacritics 2')",
    "CREATE TRIGGER IF NOT EXISTS brain_chunks_ai AFTER INSERT ON brain_chunks BEGIN"
    " INSERT INTO brain_fts (rowid, text) VALUES (new.id, new.text); END",
    "CREATE TRIGGER IF NOT EXISTS brain_chunks_ad AFTER DELETE ON brain_chunks BEGIN"
    " INSERT INTO brain_fts (brain_fts, rowid, text) VALUES ('delete', old.id, old.text); END",
]

MERGE_SYSTEM = """You keep a student's list of concepts from their lecture notes tidy. You get the concept \
names already in the list, and new names from one more lecture. For each new name, say which existing name means \
exactly the same idea (an abbreviation, another spelling, singular or plural, or a longer way of saying it), or \
null if none does. Related but different ideas are NOT the same: "machine learning" is not "deep learning", \
"business model" is not "business model canvas". Copy existing names exactly as given."""
MERGE_SCHEMA = {
    "type": "object",
    "properties": {"matches": {"type": "array", "items": {
        "type": "object",
        "properties": {"new": {"type": "string"}, "same_as": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        "required": ["new", "same_as"], "additionalProperties": False}}},
    "required": ["matches"],
    "additionalProperties": False,
}


@dataclass
class Mention:
    item_id: str
    kind: str | None
    heading: str | None
    text: str
    start: float | None
    names: set[str]  # keys that this item is about by its heading or term, before text matching


def item_text(item) -> str:
    if item.kind == "definition" and item.term:
        return f"{item.term}: {item.text}"
    return item.text


def chunked(pieces: list[tuple[float | None, str]], limit: int = CHUNK_CHARS) -> list[tuple[float | None, str]]:
    """Join short pieces (start, text) into chunks of about `limit` characters, each starting where its first piece did."""
    chunks, start, texts, size = [], None, [], 0
    for at, text in pieces:
        text = text.strip()
        if not text:
            continue
        if texts and size + len(text) > limit:
            chunks.append((start, "\n".join(texts)))
            texts, size = [], 0
        if not texts:
            start = at
        texts.append(text)
        size += len(text) + 1
    if texts:
        chunks.append((start, "\n".join(texts)))
    return chunks


class Brain:
    """One per app. Thread-safe: the worker indexes in the background while the API reads and rebuilds."""

    def __init__(self, store: Store, llm=None, *, clock=time.monotonic, gateway=None):
        self.store = store
        self.llm = llm  # the notes writer: merges names and writes definitions; None works without a model
        self.gateway = gateway  # decides which recordings a model may see; None means no limit (tests)
        self._lock = threading.RLock()
        self._clock = clock
        self._paused_until = 0.0  # the model is rate-limited: no merge checks until then
        self.fts = self._setup_fts()

    def _setup_fts(self) -> bool:
        try:
            with self.store.transaction() as conn:
                for statement in FTS_SETUP:
                    conn.execute(statement)
            return True
        except Exception:  # an SQLite without FTS5: Ask falls back to plain word matching
            log.warning("SQLite has no FTS5; Ask across everything uses simple word search")
            return False

    # ---- which recordings, and whether they changed

    def eligible(self) -> dict:
        """Recordings the brain reads: finished, not in the bin, not practice sessions, and not the parts of a class
        that was combined (or the device copies of a merged recording), so nothing is counted twice."""
        everything = self.store.list_recordings()
        sessions = class_sessions(everything, self.store.get_settings()["time_zone"])
        hidden = set()
        for rid, info in sessions.items():
            if info.get("combined") and info.get("combined_id") != rid:
                hidden.add(rid)
        for rec in everything:
            for source in self.store.get_merged_from(rec.id) or []:
                hidden.add(source.get("id"))
        have_transcript = {row["recording_id"] for row in self.store.query("SELECT recording_id FROM transcripts")}
        return {rec.id: rec for rec in everything if rec.id in have_transcript and rec.id not in hidden
                and rec.original_filename not in NOT_CLASSES}

    def fingerprints(self, ids) -> dict[str, str]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.store.query(
            "SELECT r.id, n.notes_json AS notes, t.created_at AS t,"
            " r.my_notes, (SELECT group_concat(tp.name, '|') FROM (SELECT t2.name FROM recording_topics rt"
            "   JOIN topics t2 ON t2.id = rt.topic_id WHERE rt.recording_id = r.id AND rt.state = 'accepted'"
            "   ORDER BY t2.name) tp) AS topics"
            f" FROM recordings r JOIN transcripts t ON t.recording_id = r.id LEFT JOIN ai_notes n ON n.recording_id = r.id"
            f" WHERE r.id IN ({marks})", tuple(ids))
        return {row["id"]: hashlib.sha1("\x1f".join(str(row[k] or "") for k in ("notes", "t", "my_notes", "topics"))
                                        .encode("utf-8")).hexdigest() for row in rows}

    def stale(self) -> list[str]:
        """Recordings to index again (new or changed), and ones to drop (now in the bin or combined away)."""
        eligible = self.eligible()
        current = self.fingerprints(list(eligible))
        indexed = {row["recording_id"]: row["fingerprint"] for row in self.store.query(
            "SELECT recording_id, fingerprint FROM brain_recordings")}
        changed = [rid for rid, fp in current.items() if indexed.get(rid) != fp]
        gone = [rid for rid in indexed if rid not in current]
        return gone + sorted(changed, key=lambda rid: eligible[rid].recorded_at or eligible[rid].created_at)

    # ---- background work (the worker calls step when it has nothing better to do)

    def step(self) -> bool:
        """Index one changed recording, or run one pending name check. False when there is nothing to do."""
        with self._lock:
            pending = self.stale()
            if pending:
                try:
                    self.index_recording(pending[0])
                except Exception:  # one bad recording mustn't stall the rest: note it as done and move on
                    privacy.log_failure(log, "Couldn't index %s for the brain", pending[0])
                    self._skip(pending[0])
                return True
            if self.llm is not None and self._clock() >= self._paused_until:
                rows = self.store.query("SELECT recording_id FROM brain_recordings WHERE merged = 0 LIMIT 1")
                if rows:
                    self.merge_names(rows[0]["recording_id"])
                    return True
        return False

    def _skip(self, rid: str) -> None:
        fingerprint = self.fingerprints([rid]).get(rid, "")
        with self.store.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO brain_recordings (recording_id, fingerprint, merged, indexed_at)"
                         " SELECT ?, ?, 1, ? WHERE EXISTS (SELECT 1 FROM recordings WHERE id = ?)",
                         (rid, fingerprint, now_iso(), rid))

    def catch_up(self) -> int:
        """Index everything that changed, without the model (fast): the Brain screen calls this before showing."""
        with self._lock:
            pending = self.stale()
            for rid in pending:
                try:
                    self.index_recording(rid)
                except Exception:
                    privacy.log_failure(log, "Couldn't index %s for the brain", rid)
                    self._skip(rid)
            return len(pending)

    def rebuild(self) -> int:
        """Throw the index away and build it again from the notes. Merged names and their aliases are kept."""
        with self._lock:
            with self.store.transaction() as conn:
                conn.execute("DELETE FROM concept_mentions")
                conn.execute("DELETE FROM brain_chunks")
                conn.execute("DELETE FROM brain_recordings")
                if self.fts:
                    conn.execute("INSERT INTO brain_fts (brain_fts) VALUES ('rebuild')")
            count = self.catch_up()
            self._drop_orphans()
            return count

    # ---- indexing one recording

    def index_recording(self, rid: str) -> None:
        with self._lock:
            eligible = self.eligible()
            rec = eligible.get(rid)
            if rec is None:  # deleted, in the bin, or combined into a class: forget it
                with self.store.transaction() as conn:
                    for table in ("concept_mentions", "brain_chunks", "brain_recordings"):
                        conn.execute(f"DELETE FROM {table} WHERE recording_id = ?", (rid,))
                self._drop_orphans()
                return
            notes = self._notes(rid)
            topics = [t["name"] for t in self.store.recording_topics(rid) if t["state"] == "accepted"]
            transcript = self.store.get_transcript(rid)
            paragraphs = named_paragraphs(transcript.segments, self.store.get_speaker_names(rid)) if transcript else []
            mine = self.store.get_my_notes(rid) or ""
            fingerprint = self.fingerprints([rid]).get(rid, "")

            mentions = self._mentions(notes, topics)
            candidates = self._candidates(notes, topics)
            chunks = self._chunks(rec, notes, paragraphs, mine)
            with self.store.transaction() as conn:
                known_before = set(self._lookup(conn))
                created = []
                for name, alias in candidates:
                    concept_id, new = self._find_or_create(conn, name)
                    if new:
                        created.append(concept_id)
                    if alias:
                        self._add_alias(conn, alias, concept_id)
                lookup = self._lookup(conn)
                new_keys = set(lookup) - known_before
                pattern = mention_pattern(lookup)
                conn.execute("DELETE FROM concept_mentions WHERE recording_id = ?", (rid,))
                for mention in mentions:
                    found = {lookup[k] for k in mention.names if k in lookup}
                    if pattern:
                        found |= {lookup[k] for m in pattern.finditer(mention.text)
                                  if (k := key_of(m.group(0))) in lookup}
                    for concept_id in found:
                        conn.execute("INSERT OR IGNORE INTO concept_mentions (concept_id, recording_id, item_id, kind,"
                                     " heading, text, start) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                     (concept_id, rid, mention.item_id, mention.kind, mention.heading, mention.text,
                                      mention.start))
                conn.execute("DELETE FROM brain_chunks WHERE recording_id = ?", (rid,))
                conn.executemany("INSERT INTO brain_chunks (recording_id, source, start, text) VALUES (?, ?, ?, ?)",
                                 [(rid, source, start, text) for source, start, text in chunks])
                previous = conn.execute("SELECT merged, new_concepts FROM brain_recordings WHERE recording_id = ?",
                                        (rid,)).fetchone()
                waiting = json.loads(previous["new_concepts"] or "[]") if previous and not previous["merged"] else []
                created = sorted(set(created) | set(waiting))
                conn.execute("INSERT INTO brain_recordings (recording_id, fingerprint, merged, new_concepts, indexed_at)"
                             " VALUES (?, ?, ?, ?, ?) ON CONFLICT(recording_id) DO UPDATE SET fingerprint ="
                             " excluded.fingerprint, merged = excluded.merged, new_concepts = excluded.new_concepts,"
                             " indexed_at = excluded.indexed_at",
                             (rid, fingerprint, 0 if created else 1, json.dumps(created), now_iso()))
            if new_keys:
                self._backfill(new_keys, [other for other in eligible if other != rid])
            self._drop_orphans()

    def _backfill(self, keys: set[str], rids: list[str]) -> None:
        """New names reach back: recordings indexed earlier that mention them in passing get those mentions too."""
        indexed = {row["recording_id"] for row in self.store.query("SELECT recording_id FROM brain_recordings")}
        pattern = mention_pattern(keys)
        if pattern is None:
            return
        found = []
        for rid in rids:
            if rid not in indexed:  # not indexed yet: it matches every name on its own turn
                continue
            notes = self._notes(rid)
            topics = [t["name"] for t in self.store.recording_topics(rid) if t["state"] == "accepted"]
            for mention in self._mentions(notes, topics):
                hits = {key_of(m.group(0)) for m in pattern.finditer(mention.text)} | (mention.names & keys)
                if hits:
                    found.append((rid, mention, hits))
        if not found:
            return
        with self.store.transaction() as conn:
            lookup = self._lookup(conn)
            for rid, mention, hits in found:
                for concept_id in {lookup[k] for k in hits if k in lookup}:
                    conn.execute("INSERT OR IGNORE INTO concept_mentions (concept_id, recording_id, item_id, kind,"
                                 " heading, text, start) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (concept_id, rid, mention.item_id, mention.kind, mention.heading, mention.text,
                                  mention.start))

    def _notes(self, rid: str) -> Notes:
        """The recording's notes; while they are being written again, the previous ones still count."""
        record = self.store.get_notes(rid)
        return record.notes if record and record.notes else Notes()

    @staticmethod
    def _candidates(notes: Notes, topics: list[str]) -> list[tuple[str, str | None]]:
        """Concept names this recording offers: defined terms, lecture topic headings, accepted topics."""
        names = [item.term for item in notes.lecture if item.kind == "definition" and item.term]
        names += [item.heading for item in notes.lecture if item.heading]
        names += topics
        found, seen = [], set()
        for raw in names:
            long, short = split_alias(raw)
            if not usable(long) or key_of(long) in seen:
                continue
            seen.add(key_of(long))
            found.append((tidy(long), tidy(short) if short and usable(short) else None))
        return found

    @staticmethod
    def _mentions(notes: Notes, topics: list[str]) -> list[Mention]:
        mentions = []
        for item in [*notes.items(), *notes.missed]:
            if not item.text:
                continue
            section = SECTION_OF.get(item.id[:1], "")
            names = {key_of(split_alias(n)[0]) for n in (item.heading, item.term) if n}
            names |= {key_of(split_alias(n)[1]) for n in (item.heading, item.term) if n and split_alias(n)[1]}
            mentions.append(Mention(item.id, item.kind or section, item.heading, item_text(item),
                                    item.cites[0] if item.cites else None, names))
        for topic in topics:
            mentions.append(Mention(f"topic:{key_of(topic)}", "topic", None, f"Tagged #{topic}", None, {key_of(topic)}))
        return mentions

    @staticmethod
    def _chunks(rec, notes: Notes, paragraphs, mine: str) -> list[tuple[str, float | None, str]]:
        header = f"{rec.title}" + (f" ({rec.subject})" if rec.subject else "") + (f" with {rec.person}" if rec.person else "")
        note_pieces = []
        for item in notes.items():
            label = None if item.kind == "definition" and item.term else item.heading or {
                "S": "Summary", "D": "Decision", "A": "To do", "Q": "Open question", "P": "About them"}.get(item.id[:1])
            note_pieces.append((item.cites[0] if item.cites else None, f"{label}: {item_text(item)}" if label else item_text(item)))
        chunks = [("notes", start, f"[{header}, notes]\n{text}") for start, text in chunked(note_pieces)]
        transcript_pieces = [(p.start, f"{p.speaker}: {p.text}" if p.speaker else p.text) for p in paragraphs]
        chunks += [("transcript", start, text) for start, text in chunked(transcript_pieces)]
        mine_pieces = [(None, block) for block in mine.split("\n\n")]
        chunks += [("mine", None, f"[{header}, my notes]\n{text}") for _, text in chunked(mine_pieces)]
        return chunks

    # ---- the concept dictionary (inside a transaction)

    @staticmethod
    def _lookup(conn) -> dict[str, int]:
        """Every key that names a concept: its own name and its aliases."""
        lookup = {row["key"]: row["id"] for row in conn.execute("SELECT id, key FROM concepts")}
        for row in conn.execute("SELECT key, concept_id FROM concept_aliases"):
            lookup.setdefault(row["key"], row["concept_id"])
        return lookup

    def _find_or_create(self, conn, name: str) -> tuple[int, bool]:
        key = key_of(name)
        lookup = self._lookup(conn)
        if key in lookup:
            return lookup[key], False
        # a known short form or long form already in the list: "AI" and "artificial intelligence"
        known = KNOWN_ALIASES.get(key) or next((short for short, long in KNOWN_ALIASES.items() if long == key), None)
        if known and known in lookup:
            self._add_alias(conn, name, lookup[known])
            return lookup[known], False
        # an acronym (written in capitals, like BMC) of a name already in the list, or the reverse
        names = [(row["key"], row["name"], row["concept_id"]) for row in conn.execute(
            "SELECT key, name, id AS concept_id FROM concepts UNION ALL SELECT key, alias, concept_id FROM concept_aliases")]
        for other_key, other_name, concept_id in names:
            if (acronym(name) and initials(other_key) == key) or (acronym(other_name) and initials(key) == other_key):
                self._add_alias(conn, name, concept_id)
                return concept_id, False
        concept_id = conn.execute("INSERT INTO concepts (name, key, created_at) VALUES (?, ?, ?)",
                                  (name, key, now_iso())).lastrowid
        if known:
            self._add_alias(conn, known.upper() if len(known) <= 5 else known, concept_id)
        return concept_id, True

    @staticmethod
    def _add_alias(conn, alias: str, concept_id: int) -> None:
        key = key_of(alias)
        own = conn.execute("SELECT id FROM concepts WHERE key = ?", (key,)).fetchone()
        if not key or (own and own["id"] != concept_id):
            return
        conn.execute("INSERT OR IGNORE INTO concept_aliases (key, alias, concept_id) VALUES (?, ?, ?)",
                     (key, alias, concept_id))

    def _drop_orphans(self) -> None:
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM concepts WHERE id NOT IN (SELECT DISTINCT concept_id FROM concept_mentions)"
                         " AND note = '' AND visibility IS NULL")  # a note or a show/hide choice waits for its mentions to come back

    # ---- merging names

    def merge(self, source_id: int, into_id: int) -> bool:
        """Make two concepts one (the "these are the same" button, and the model's check). The merged name is kept
        as an alias, so it lands on the same page next time too."""
        if source_id == into_id:
            return False
        with self._lock, self.store.transaction() as conn:
            source = conn.execute("SELECT name, key FROM concepts WHERE id = ?", (source_id,)).fetchone()
            if source is None or conn.execute("SELECT 1 FROM concepts WHERE id = ?", (into_id,)).fetchone() is None:
                return False
            conn.execute("INSERT OR IGNORE INTO concept_mentions (concept_id, recording_id, item_id, kind, heading, text,"
                         " start) SELECT ?, recording_id, item_id, kind, heading, text, start FROM concept_mentions"
                         " WHERE concept_id = ?", (into_id, source_id))
            conn.execute("UPDATE concept_aliases SET concept_id = ? WHERE concept_id = ?", (into_id, source_id))
            conn.execute("DELETE FROM concepts WHERE id = ?", (source_id,))
            conn.execute("INSERT OR REPLACE INTO concept_aliases (key, alias, concept_id) VALUES (?, ?, ?)",
                         (source["key"], source["name"], into_id))
            conn.execute("UPDATE concepts SET definition_n = NULL WHERE id = ?", (into_id,))  # more to define from
            return True

    def merge_names(self, rid: str) -> None:
        """One small model call: are any of this recording's new concept names the same as ones already there?"""
        rows = self.store.query("SELECT new_concepts FROM brain_recordings WHERE recording_id = ?", (rid,))
        new_ids = set(json.loads(rows[0]["new_concepts"] or "[]")) if rows else set()
        names = {row["id"]: row["name"] for row in self.store.query(
            "SELECT c.id, c.name, COUNT(m.id) AS n FROM concepts c LEFT JOIN concept_mentions m ON m.concept_id = c.id"
            " GROUP BY c.id ORDER BY n DESC")}
        visible = self.visible_concepts(self.llm.name, names) if self.llm is not None else set()
        names = {cid: name for cid, name in names.items() if cid in visible}
        fresh = {cid: names[cid] for cid in new_ids if cid in names}
        waiting = set()  # names other recordings are still waiting to check: they'll be compared with these then
        for row in self.store.query("SELECT new_concepts FROM brain_recordings WHERE merged = 0 AND recording_id != ?",
                                    (rid,)):
            waiting |= set(json.loads(row["new_concepts"] or "[]"))
        existing = [(cid, name) for cid, name in names.items()
                    if cid not in new_ids and cid not in waiting][:MAX_EXISTING_FOR_MERGE]
        if fresh and existing:
            user = ("Existing names:\n" + "\n".join(f"- {n}" for _, n in existing)
                    + "\n\nNew names:\n" + "\n".join(f"- {n}" for n in fresh.values()))
            try:
                with privacy.scope("brain-merge", self.concept_recordings([*fresh, *(cid for cid, _ in existing)])):
                    raw = self.llm.complete_json(system=MERGE_SYSTEM, user=user, schema=MERGE_SCHEMA,
                                                 schema_name="concept_merge", effort="low")
            except RateLimited as exc:
                self._paused_until = self._clock() + max(exc.retry_after, 5.0)
                return  # stays pending; tried again once the limit lifts
            except (TransientError, PermanentError) as exc:
                log.info("Concept name check skipped for %s: %s", rid, type(exc).__name__)
                raw = {"matches": []}
            by_key = {key_of(n): cid for cid, n in existing}
            fresh_by_key = {key_of(n): cid for cid, n in fresh.items()}
            for match in raw.get("matches") or []:
                if not isinstance(match, dict) or not match.get("same_as"):
                    continue
                source, target = fresh_by_key.get(key_of(match.get("new", ""))), by_key.get(key_of(match["same_as"]))
                if source and target:
                    self.merge(source, target)
        with self.store.transaction() as conn:
            conn.execute("UPDATE brain_recordings SET merged = 1 WHERE recording_id = ?", (rid,))

    # ---- reading

    def counts(self) -> dict:
        rows = self.store.query(
            "SELECT (SELECT COUNT(DISTINCT m.concept_id) FROM concept_mentions m JOIN recordings r ON r.id = m.recording_id"
            "  AND r.deleted_at IS NULL) AS concepts,"
            " (SELECT COUNT(*) FROM brain_recordings) AS recordings,"
            " (SELECT COUNT(*) FROM brain_recordings WHERE merged = 0) AS checking")
        return {**dict(rows[0]), "checking": rows[0]["checking"] if self.llm is not None else 0}

    def list_concepts(self, q: str = "", subject_id: int | None = None, include_hidden: bool = False) -> list[dict]:
        """Concepts, most widespread first. Generic headings and course admin (and any you hid) are left out unless asked
        for; a search finds them anyway, since you typed their name."""
        sql = ("SELECT c.id, c.name, c.visibility, COUNT(m.id) AS mentions, COUNT(DISTINCT m.recording_id) AS recordings,"
               " MAX(COALESCE(r.recorded_at, r.created_at)) AS last_at,"
               " group_concat(DISTINCT s.name) AS subjects,"
               " SUM(m.kind = 'definition') AS definitions, SUM(m.kind = 'stressed') AS stressed"
               " FROM concepts c JOIN concept_mentions m ON m.concept_id = c.id"
               " JOIN recordings r ON r.id = m.recording_id AND r.deleted_at IS NULL"
               " LEFT JOIN subjects s ON s.id = r.subject_id")
        where, params = [], []
        if q.strip():
            where.append("(c.key LIKE ? OR c.id IN (SELECT concept_id FROM concept_aliases WHERE key LIKE ?))")
            params += [f"%{key_of(q) or q.lower()}%"] * 2
        if subject_id is not None:
            where.append("c.id IN (SELECT m2.concept_id FROM concept_mentions m2 JOIN recordings r2 ON r2.id = m2.recording_id"
                         " WHERE r2.subject_id = ? AND r2.deleted_at IS NULL)")
            params.append(subject_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY c.id ORDER BY recordings DESC, mentions DESC, c.name COLLATE NOCASE"
        found = [{**dict(row), "subjects": sorted(set((row["subjects"] or "").split(",")) - {""}),
                  "quality": quality.classify(row["name"]), "hidden": quality.hidden(row["name"], row["visibility"])}
                 for row in self.store.query(sql, tuple(params))]
        return found if include_hidden or q.strip() else [c for c in found if not c["hidden"]]

    def find(self, name: str, fuzzy: bool = True) -> int | None:
        """The concept a name refers to: its exact name or an alias, else (fuzzy) the most widespread one containing it."""
        key = key_of(split_alias(name)[0])
        if not key:
            return None
        rows = self.store.query("SELECT id FROM concepts WHERE key = ? UNION SELECT concept_id FROM concept_aliases"
                                " WHERE key = ?", (key, key))
        if rows or not fuzzy:
            return rows[0]["id"] if rows else None
        found = self.list_concepts(q=name)
        return found[0]["id"] if found else None

    def concept(self, concept_id: int) -> dict | None:
        rows = self.store.query("SELECT * FROM concepts WHERE id = ?", (concept_id,))
        if not rows:
            return None
        row = rows[0]
        mentions = self.store.query(
            "SELECT m.*, r.title, r.subject_id, s.name AS subject, r.person_id, p.name AS person, r.duration_sec,"
            " COALESCE(r.recorded_at, r.created_at) AS at FROM concept_mentions m"
            " JOIN recordings r ON r.id = m.recording_id AND r.deleted_at IS NULL"
            " LEFT JOIN subjects s ON s.id = r.subject_id LEFT JOIN people p ON p.id = r.person_id"
            " WHERE m.concept_id = ? ORDER BY at DESC, m.recording_id, m.start IS NULL, m.start, m.id", (concept_id,))
        groups: dict[str, dict] = {}
        for m in mentions:
            group = groups.setdefault(m["recording_id"], {
                "recording_id": m["recording_id"], "title": m["title"], "subject": m["subject"],
                "subject_id": m["subject_id"], "person": m["person"], "person_id": m["person_id"], "at": m["at"],
                "long_form": (m["duration_sec"] or 0) >= 3600, "items": []})
            if m["kind"] != "topic" or not group["items"]:
                group["items"].append({"item_id": m["item_id"], "kind": m["kind"], "heading": m["heading"],
                                       "text": m["text"], "start": m["start"]})
        for group in groups.values():  # an accepted topic only stands in when nothing else was said
            if len(group["items"]) > 1:
                group["items"] = [i for i in group["items"] if i["kind"] != "topic"]
        recordings = list(groups.values())
        subjects = defaultdict(int)
        people = {}
        for group in recordings:
            if group["subject"]:
                subjects[(group["subject_id"], group["subject"])] += 1
            if group["person"]:
                people[group["person_id"]] = group["person"]
        aliases = [r["alias"] for r in self.store.query("SELECT alias FROM concept_aliases WHERE concept_id = ?"
                                                         " ORDER BY alias COLLATE NOCASE", (concept_id,))]
        count = sum(len(g["items"]) for g in recordings)
        return {"id": row["id"], "name": row["name"], "aliases": aliases, "note": row["note"] or "",
                "quality": quality.classify(row["name"]), "visibility": row["visibility"],
                "hidden": quality.hidden(row["name"], row["visibility"]),
                "definition": row["definition"] or self.fallback_definition(recordings),
                "definition_by": "model" if row["definition"] else "notes",
                "definition_stale": self.llm is not None and count > 0
                and row["definition_fp"] != self.definition_fingerprint(concept_id),
                "mention_count": count, "recordings": recordings,
                "classes": [{"id": sid, "name": name, "count": n} for (sid, name), n in
                            sorted(subjects.items(), key=lambda pair: -pair[1])],
                "people": [{"id": pid, "name": name} for pid, name in people.items()],
                "related": self.related(concept_id)}

    @staticmethod
    def fallback_definition(recordings: list[dict]) -> str | None:
        """Without a model: the clearest thing the notes said, a definition first."""
        items = [i for g in recordings for i in g["items"]]
        for kind in ("definition", "stressed", "fact", "point"):
            found = [i["text"] for i in items if i["kind"] == kind]
            if found:  # a definition reads "term: what it is"; the page already shows the term
                return min(found, key=len) if kind != "definition" else found[0].split(": ", 1)[-1]
        return None

    def related(self, concept_id: int, limit: int = 8) -> list[dict]:
        """Ideas that come up in the same recordings, stronger when under the same topic heading. No model."""
        rows = self.store.query(
            "SELECT c.id, c.name, c.visibility, COUNT(DISTINCT o.recording_id) AS together,"
            " SUM(o.heading IS NOT NULL AND o.heading = m.heading) AS same_heading"
            " FROM concept_mentions m JOIN concept_mentions o ON o.recording_id = m.recording_id AND o.concept_id != m.concept_id"
            " JOIN concepts c ON c.id = o.concept_id JOIN recordings r ON r.id = m.recording_id AND r.deleted_at IS NULL"
            " WHERE m.concept_id = ? GROUP BY c.id ORDER BY together * 3 + MIN(same_heading, 6) DESC, c.name LIMIT ?",
            (concept_id, limit * 3))
        return [{"id": r["id"], "name": r["name"], "together": r["together"]} for r in rows
                if not quality.hidden(r["name"], r["visibility"])][:limit]

    def definition_material(self, concept_id: int) -> tuple[str, list[str], int, str] | None:
        """(name, the best sentences, how many mentions, fingerprint of exactly what this was read from)."""
        page = self.concept(concept_id)
        if page is None:
            return None
        items = [i for g in page["recordings"] for i in g["items"] if i["kind"] != "topic"]
        items.sort(key=lambda i: {"definition": 0, "stressed": 1, "fact": 2, "example": 3}.get(i["kind"], 4))
        return page["name"], [i["text"] for i in items[:14]], page["mention_count"], self.definition_fingerprint(concept_id)

    def definition_fingerprint(self, concept_id: int) -> str:
        """Changes whenever anything a definition depends on changes: the name or an alias, the wording of a note it was
        drawn from, a recording going to the bin or coming back, being erased, or its privacy class."""
        rows = self.store.query(
            "SELECT m.recording_id, m.item_id, m.kind, m.text, m.start FROM concept_mentions m"
            " JOIN recordings r ON r.id = m.recording_id AND r.deleted_at IS NULL WHERE m.concept_id = ?"
            " ORDER BY m.recording_id, m.item_id", (concept_id,))
        head = self.store.query("SELECT name, key FROM concepts WHERE id = ?", (concept_id,))
        aliases = [r["key"] for r in self.store.query("SELECT key FROM concept_aliases WHERE concept_id = ? ORDER BY key",
                                                       (concept_id,))]
        classes = self.store.effective_privacy(sorted({r["recording_id"] for r in rows}))
        digest = hashlib.sha256()
        for part in [head[0]["name"] if head else "", head[0]["key"] if head else "", *aliases]:
            digest.update(part.encode("utf-8") + b"\x1f")
        for r in rows:
            digest.update("\x1e".join([r["recording_id"], r["item_id"], r["kind"] or "", r["text"], str(r["start"]),
                                       classes.get(r["recording_id"], ("?", False))[0]]).encode("utf-8") + b"\x1f")
        return digest.hexdigest()

    def save_definition(self, concept_id: int, text: str, count: int, fingerprint: str | None = None) -> bool:
        """Keep a written definition, unless what it was written from changed while it was being written."""
        current = self.definition_fingerprint(concept_id)
        if fingerprint is not None and fingerprint != current:
            return False
        with self.store.transaction() as conn:
            conn.execute("UPDATE concepts SET definition = ?, definition_n = ?, definition_fp = ? WHERE id = ?",
                         (text, count, current, concept_id))
        return True

    # ---- which concepts and recordings a model is allowed to see

    def concept_recordings(self, concept_ids) -> list[str]:
        """Every recording behind these concepts' names, aliases and mentions."""
        ids = list(dict.fromkeys(concept_ids))
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return [r["recording_id"] for r in self.store.query(
            f"SELECT DISTINCT recording_id FROM concept_mentions WHERE concept_id IN ({marks})", tuple(ids))]

    def visible_concepts(self, config_id: str, concepts) -> set[int]:
        """The concepts a model may see by name: every recording behind one must be allowed. A concept with no
        mentions left has no known source, so it stays out."""
        if self.gateway is None:
            return set(concepts)
        rows = self.store.query("SELECT concept_id, recording_id FROM concept_mentions")
        by_concept = defaultdict(set)
        for r in rows:
            by_concept[r["concept_id"]].add(r["recording_id"])
        allowed = self.gateway.eligible_ids(config_id, {rid for ids in by_concept.values() for rid in ids})
        return {cid for cid in concepts if by_concept.get(cid) and by_concept[cid] <= allowed}

    def allowed_recordings(self, config_id: str) -> set[str] | None:
        """The recordings this model may be sent passages from (None: no limit)."""
        if self.gateway is None:
            return None
        ids = [r["recording_id"] for r in self.store.query("SELECT DISTINCT recording_id FROM brain_chunks")]
        return self.gateway.eligible_ids(config_id, ids)

    def set_visibility(self, concept_id: int, visibility: str | None) -> None:
        """'shown' or 'hidden' is your own choice; None hands it back to the rules."""
        if visibility not in ("shown", "hidden", None):
            raise ValueError("Show it, hide it, or leave it to the app")
        with self.store.transaction() as conn:
            conn.execute("UPDATE concepts SET visibility = ? WHERE id = ?", (visibility, concept_id))

    def set_note(self, concept_id: int, note: str) -> None:
        with self.store.transaction() as conn:
            conn.execute("UPDATE concepts SET note = ? WHERE id = ?", (note.strip()[:4000], concept_id))

    def add_alias(self, concept_id: int, alias: str) -> None:
        """Another name for this concept, so notes that use it land on this page. A name that is already another
        concept's must be merged instead, so two pages never claim the same name."""
        alias = tidy(alias)
        key = key_of(alias)
        if not key:
            raise ValueError("Type the other name")
        with self._lock:
            owner = self.find(alias, fuzzy=False)
            if owner is not None and owner != concept_id:
                other = self.store.query("SELECT name FROM concepts WHERE id = ?", (owner,))
                raise ValueError(f"“{other[0]['name'] if other else alias}” is its own concept. Merge the two instead.")
            if owner == concept_id:
                return
            with self.store.transaction() as conn:
                conn.execute("INSERT OR REPLACE INTO concept_aliases (key, alias, concept_id) VALUES (?, ?, ?)",
                             (key, alias, concept_id))
                conn.execute("DELETE FROM brain_recordings")  # index again so mentions under this name are found

    def remove_alias(self, concept_id: int, alias: str) -> bool:
        with self.store.transaction() as conn:
            return conn.execute("DELETE FROM concept_aliases WHERE concept_id = ? AND alias = ?",
                                (concept_id, alias)).rowcount > 0

    def rename(self, concept_id: int, name: str) -> int | None:
        """Give a concept a better name; a name another concept already has merges the two."""
        name = tidy(name)
        key = key_of(name)
        if not key:
            raise ValueError("A concept needs a name")
        with self._lock:
            other = self.find(name, fuzzy=False)
            if other and other != concept_id:
                self.merge(concept_id, other)
                return other
            with self.store.transaction() as conn:
                old = conn.execute("SELECT name, key FROM concepts WHERE id = ?", (concept_id,)).fetchone()
                if old is None:
                    return None
                conn.execute("DELETE FROM concept_aliases WHERE key = ?", (key,))
                conn.execute("UPDATE concepts SET name = ?, key = ? WHERE id = ?", (name, key, concept_id))
                if old["key"] != key:
                    conn.execute("INSERT OR REPLACE INTO concept_aliases (key, alias, concept_id) VALUES (?, ?, ?)",
                                 (old["key"], old["name"], concept_id))
            return concept_id
