"""Ask across everything: find the few passages that fit a question (SQLite full-text search), then have a model
answer from those passages only, citing them as [1], [2]. Small on purpose: about 2-3k tokens per question."""
import re
from dataclasses import dataclass

from .. import privacy
from ..providers.base import PermanentError, RateLimited, TransientError

TOP_CHUNKS = 8
PER_RECORDING = 3
STOPWORDS = set("""a an and are as at be but by can did do does for from had has have how i if in into is it its me my
of on or our so that the their them then there these they this to was we were what when where which who why will with
you your about tell say said explain mean means whats lecture lectures class classes talk talks chat chats we us""".split())

SYSTEM = """You answer a student's question from their own recorded lectures, coffee chats and talks. You get \
numbered passages that a search found for the question; each says which recording it is from. Answer only from \
these passages, and cite each fact with the number of the passage it came from, like [2] or [1][3]. If the \
passages don't contain the answer, say so plainly instead of guessing. Keep it short and direct: a few sentences \
or a short list, unless the question asks for more. Plain English."""
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"],
          "additionalProperties": False}
SYNTHESIS = """You help a student see how ideas fit together across their own recorded lectures, coffee chats and \
talks. You get numbered passages a search found for the question; each says which recording it is from. Compare and \
connect what the different recordings say: where they agree, where they add to each other, and where they differ. Use \
only these passages and cite every claim with the number of its passage, like [2] or [1][3]. If the passages don't say \
enough to connect, say so plainly. Plain English, short paragraphs or a short list."""
APPLICATION = """You help a student put what they learned to use. You get numbered passages from their own recorded \
lectures, coffee chats and talks, found for their question. First, in a few sentences, say what the passages say that \
matters here, citing each fact like [2]. Then write a line that says exactly "Applying it:" and give two to four concrete \
suggestions for the student's situation. Those suggestions are your own ideas, not from the recordings: do not cite \
them, and do not present them as something a lecturer said. Plain English."""
MODES = {"retrieval": SYSTEM, "synthesis": SYNTHESIS, "application": APPLICATION}
NO_MODEL = "No AI model is set up to write an answer, so here are the passages that match best."
NOTHING = "Nothing in your recordings matches that yet. Try other words, or a concept's name."


@dataclass
class Source:
    n: int
    chunk_id: int
    recording_id: str
    title: str
    subject: str | None
    person: str | None
    at: str
    source: str  # notes | transcript | mine
    start: float | None
    long_form: bool
    text: str

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("n", "recording_id", "title", "subject", "person", "at", "source",
                                              "start", "long_form", "text")}


def terms(question: str) -> list[str]:
    words = re.findall(r"[\w']+", question.lower())
    found = []
    for word in words:
        word = word.replace("'", "")
        if word and word not in STOPWORDS and (len(word) > 1 or word.isdigit()) and word not in found:
            found.append(word)
    return found[:16]


def search(brain, question: str, limit: int = TOP_CHUNKS, allowed: set[str] | None = None) -> list[Source]:
    """`allowed`: only passages from these recordings (chosen before any text is picked for a model)."""
    words = terms(question)
    if not words or allowed is not None and not allowed:
        return []
    only, only_params = (" AND b.recording_id IN (" + ",".join("?" * len(allowed)) + ")", tuple(sorted(allowed))) \
        if allowed is not None else ("", ())
    select = ("SELECT b.id, b.recording_id, b.source, b.start, b.text, r.title, s.name AS subject, p.name AS person,"
              " r.duration_sec, COALESCE(r.recorded_at, r.created_at) AS at FROM brain_chunks b"
              " JOIN recordings r ON r.id = b.recording_id AND r.deleted_at IS NULL"
              " LEFT JOIN subjects s ON s.id = r.subject_id LEFT JOIN people p ON p.id = r.person_id")
    if brain.fts:
        match = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
        # notes say it most clearly; your own notes next; then the transcript
        rows = brain.store.query(select + " JOIN brain_fts f ON f.rowid = b.id WHERE brain_fts MATCH ?" + only +
                                 " ORDER BY bm25(brain_fts) * CASE b.source WHEN 'notes' THEN 1.3 WHEN 'mine' THEN 1.15"
                                 " ELSE 1.0 END LIMIT 80", (match, *only_params))
    else:
        like = " OR ".join("lower(b.text) LIKE ?" for _ in words)
        rows = brain.store.query(select + f" WHERE ({like})" + only + " LIMIT 400",
                                 (*(f"%{w}%" for w in words), *only_params))
        rows = sorted(rows, key=lambda r: -sum(r["text"].lower().count(w) for w in words))
    picked, per = [], {}  # at most three passages from one recording, so several recordings get a say
    for row in rows:
        if per.get(row["recording_id"], 0) >= PER_RECORDING:
            continue
        per[row["recording_id"]] = per.get(row["recording_id"], 0) + 1
        picked.append(row)
        if len(picked) >= limit:
            break
    return [Source(n, row["id"], row["recording_id"], row["title"], row["subject"], row["person"], row["at"],
                   row["source"], row["start"], (row["duration_sec"] or 0) >= 3600, row["text"])
            for n, row in enumerate(picked, 1)]


def _clock(seconds: float | None, long_form: bool) -> str:
    if seconds is None:
        return ""
    total = int(seconds)
    h, m, s = total // 3600, total % 3600 // 60, total % 60
    return f" at {h}:{m:02d}:{s:02d}" if long_form else f" at {m:02d}:{s:02d}"


def passages(sources: list[Source]) -> str:
    blocks = []
    for src in sources:
        where = src.subject or (f"chat with {src.person}" if src.person else "no subject")
        kind = {"notes": "notes", "mine": "the student's own notes", "transcript": "transcript"}[src.source]
        blocks.append(f"[{src.n}] {src.title} ({where}, {src.at[:10]}, {kind}{_clock(src.start, src.long_form)})\n"
                      f"{src.text}")
    return "\n\n".join(blocks)


def clean_citations(answer: str, count: int) -> str:
    """Only passages that were retrieved can be cited: [9] when there were 6 is removed."""
    return re.sub(r"\[(\d+)\]", lambda m: m.group(0) if 1 <= int(m.group(1)) <= count else "", answer).strip()


def ask_brain(brain, engines: list, question: str, mode: str = "retrieval") -> dict:
    """retrieval: what was said. synthesis: how the recordings relate. application: what was said, then the model's own
    suggestions, kept apart and uncited so they are never mistaken for something a lecturer said."""
    question = " ".join(question.split())
    mode = mode if mode in MODES else "retrieval"
    sources = search(brain, question)  # what matches, for you to read here; it goes nowhere
    payload = {"question": question, "mode": mode, "sources": [s.as_dict() for s in sources]}
    if not sources:
        return {**payload, "answer": NOTHING, "engine": None, "note": None}
    if not engines:
        return {**payload, "answer": NO_MODEL, "engine": None, "note": None}
    problems = []
    for llm in engines:
        # Each model is only shown passages from recordings it is approved for, chosen before any text is picked.
        allowed = brain.allowed_recordings(llm.name)
        use = sources if allowed is None else search(brain, question, allowed=allowed)
        left_out = len({s.recording_id for s in sources} - {s.recording_id for s in use})
        if not use:
            problems.append(f"{llm.name}: none of the matching recordings may be sent to it")
            continue
        user = f"QUESTION: {question}\n\nPASSAGES:\n{passages(use)}"
        try:
            with privacy.scope("ask-all", {s.recording_id for s in use}):
                raw = llm.complete_json(system=MODES[mode], user=user, schema=SCHEMA, schema_name="brain_answer", effort="low")
        except (PermanentError, TransientError, RateLimited) as exc:
            problems.append(f"{llm.name}: {exc}")
            continue
        answer = clean_citations(str(raw.get("answer") or ""), len(use)) or "(no answer)"
        if left_out:
            problems.append(f"{left_out} matching {'recording was' if left_out == 1 else 'recordings were'} "
                            "left out because of its privacy setting")
        return {**payload, "sources": [s.as_dict() for s in use], "answer": answer, "engine": llm.name,
                "note": "; ".join(problems) or None}
    return {**payload, "answer": "No AI model could answer just now; here are the passages that match best.",
            "engine": None, "note": "; ".join(problems)}


DEFINE_SYSTEM = """Write a short definition of one concept for a student's study notes, from what their own lectures \
and chats said about it. One or two plain sentences, at most 45 words, saying what it is (and, if the notes say, \
why it matters). Use only the given notes; do not add facts they don't contain. No heading, no quotes."""
DEFINE_SCHEMA = {"type": "object", "properties": {"definition": {"type": "string"}}, "required": ["definition"],
                 "additionalProperties": False}


def write_definition(llm, name: str, texts: list[str]) -> str:
    """One small model call; the caller keeps the result until new mentions arrive."""
    user = f"CONCEPT: {name}\n\nWHAT THE NOTES SAY:\n" + "\n".join(f"- {t}" for t in texts)
    raw = llm.complete_json(system=DEFINE_SYSTEM, user=user, schema=DEFINE_SCHEMA, schema_name="concept_definition",
                            effort="low")
    return " ".join(str(raw.get("definition") or "").split())
