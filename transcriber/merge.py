"""Merge recordings of the same session made on several devices into one best-of transcript.

The recordings are lined up by matching their words (each device started at a different moment), then compared
passage by passage. Where they disagree: the version most devices agree on wins; otherwise the version Whisper was
clearly more confident about; close calls go to a judge (the notes AI), and confidence decides if it can't.
"""
import json
import re
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from .providers.base import Segment
from .retry import call_with_retries

CONF_MARGIN = 0.25  # avg_logprob difference that counts as "clearly more confident"
DEFAULT_CONF = -0.5  # for transcripts without confidence scores
MIN_MATCH_WORDS = 6
MIN_SHARE = 0.25  # below this share of matching words, they aren't the same session
JUDGE_BATCH = 15
CONTEXT_WORDS = 25
NEIGHBOURHOOD = 12  # words either side used for a device's typical confidence
JOIN_GAP = 4  # differences this few words apart are judged as one phrase

Judge = Callable[[list[dict]], list[int]]


class NotSameSession(ValueError):
    def __init__(self, share: float):
        super().__init__(f"These recordings don't look like the same session (only {share:.0%} of their words match)")
        self.share = share


@dataclass
class Word:
    text: str  # as written, with case and punctuation
    norm: str  # for comparing
    t: float  # start, on the reference recording's clock once merged
    end: float
    conf: float
    source: int
    segment: int  # index of the segment it came from, to keep natural sentence boundaries


def words_from_segments(segments: Sequence[Segment], source: int, shift: float = 0.0) -> list[Word]:
    words = []
    for index, segment in enumerate(segments):
        tokens = segment.text.split()
        span = (segment.end - segment.start) / max(1, len(tokens))
        conf = segment.avg_logprob if segment.avg_logprob is not None else DEFAULT_CONF
        for i, token in enumerate(tokens):
            norm = re.sub(r"[^\w']", "", token.lower())
            if norm:
                start = segment.start + i * span + shift
                words.append(Word(token, norm, start, start + span, conf, source, index))
    return words


def estimate_offset(ref: list[Word], other: list[Word]) -> tuple[float, float]:
    """(seconds to add to the other device's times to land on the reference clock... negated), match share.

    Returns other_time - ref_time for the same words, and the share of words that matched."""
    matcher = SequenceMatcher(None, [w.norm for w in ref], [w.norm for w in other], autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size >= MIN_MATCH_WORDS]
    share = sum(b.size for b in blocks) / max(1, min(len(ref), len(other)))
    if not blocks or share < MIN_SHARE:
        raise NotSameSession(share)
    deltas = [other[b.b + k].t - ref[b.a + k].t for b in blocks for k in range(b.size)]
    return statistics.median(deltas), share


def _edits(ref: list[Word], other: list[Word], covered: tuple[float, float]) -> list[tuple[int, int, list[Word]]]:
    """Where `other` differs from the reference: (ref_start, ref_end, other's words for that stretch).

    Only the part of the reference this device was recording during counts: before it started or after it
    stopped, it has no say."""
    lo = next((i for i, w in enumerate(ref) if w.t >= covered[0]), len(ref))
    hi = next((i for i in range(len(ref) - 1, -1, -1) if ref[i].t <= covered[1]), -1) + 1
    window = ref[lo:max(lo, hi)]
    matcher = SequenceMatcher(None, [w.norm for w in window], [w.norm for w in other], autojunk=False)
    return [(lo + i1, lo + i2, other[j1:j2]) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def _regions(edit_lists: list[list[tuple[int, int, list[Word]]]]) -> list[tuple[int, int]]:
    """Merge every device's edit spans (insertions are zero-width) into non-overlapping regions."""
    spans = sorted((a, b) for edits in edit_lists for a, b, _ in edits)
    regions: list[list[int]] = []
    for a, b in spans:
        if regions and a <= regions[-1][1] + JOIN_GAP:
            regions[-1][1] = max(regions[-1][1], b)
        else:
            regions.append([a, b])
    return [(a, b) for a, b in regions]


def _version(ref: list[Word], edits: list[tuple[int, int, list[Word]]], a: int, b: int) -> list[Word]:
    """One device's words for reference span [a, b]: its own edits there, the reference words elsewhere."""
    out: list[Word] = []
    position = a
    for e_start, e_end, words in edits:
        if e_end < a or e_start > b or (e_start == e_end and not a <= e_start <= b):
            continue
        out.extend(ref[position:e_start])
        out.extend(words)
        position = max(position, e_end)
    out.extend(ref[position:b])
    return out


def _typical_conf(words: list[Word], fallback: float) -> float:
    return statistics.fmean(w.conf for w in words) if words else fallback


def merge_words(sources: Sequence[Sequence[Segment]], judge: Judge | None,
                durations: Sequence[float] | None = None) -> tuple[list[Word], list[float]]:
    """The best-of word sequence on the reference clock, and each device's share of the final words.

    The reference (whose audio and clock are used) is the longest recording."""
    spans = durations or [(s[-1].end - s[0].start) if s else 0.0 for s in sources]
    ref_index = max(range(len(sources)), key=lambda i: spans[i])
    ref = words_from_segments(sources[ref_index], ref_index)
    others: dict[int, list[Word]] = {}
    covered: dict[int, tuple[float, float]] = {}
    for i, segments in enumerate(sources):
        if i == ref_index:
            continue
        raw = words_from_segments(segments, i)
        delta, _ = estimate_offset(ref, raw)
        others[i] = words_from_segments(segments, i, shift=-delta)
        if durations:  # when this device was recording, on the reference clock
            covered[i] = (-delta, durations[i] - delta)
        else:
            covered[i] = (others[i][0].t, others[i][-1].end) if others[i] else (0.0, 0.0)
    edits = {i: _edits(ref, words, covered[i]) for i, words in others.items()}
    regions = _regions(list(edits.values()))

    pieces: list[tuple[str, object]] = []  # ("words", [...]) or ("region", index)
    decisions: list[dict] = []
    position = 0
    for a, b in regions:
        pieces.append(("words", ref[position:a]))
        versions = {ref_index: ref[a:b], **{i: _version(ref, edits[i], a, b) for i in others}}
        decisions.append(_decide(versions, ref, others, a, b))
        pieces.append(("region", len(decisions) - 1))
        position = b
    pieces.append(("words", ref[position:]))

    _settle_close_calls(decisions, ref, judge)
    merged: list[Word] = []
    for kind, value in pieces:
        merged.extend(value if kind == "words" else decisions[value]["chosen"])
    limit = spans[ref_index] if durations else float("inf")
    merged = [w for w in merged if 0 <= w.t <= limit + 1.0]
    counts = [sum(1 for w in merged if w.source == i) for i in range(len(sources))]
    total = max(1, sum(counts))
    return merged, [c / total for c in counts]


def _decide(versions: dict[int, list[Word]], ref: list[Word], others: dict[int, list[Word]],
            a: int, b: int) -> dict:
    """Majority, then confidence; anything still close is left for the judge."""
    options: list[dict] = []  # distinct wordings, in device order
    for source, words in sorted(versions.items()):
        key = " ".join(w.norm for w in words)
        option = next((o for o in options if o["key"] == key), None)
        if option is None:
            option = {"key": key, "votes": 0, "words": words, "conf": None}
            options.append(option)
        option["votes"] += 1
        if words:
            conf = _typical_conf(words, DEFAULT_CONF)
        else:  # this device heard nothing here: as sure as it usually is nearby, minus the margin
            nearby = _nearby(ref if source not in others else others[source], ref, a, b, source)
            conf = _typical_conf(nearby, DEFAULT_CONF) - CONF_MARGIN
        if option["conf"] is None or conf > option["conf"]:
            option["conf"], option["words"] = conf, words if words or option["words"] == [] else option["words"]
    decision = {"options": options, "chosen": None, "a": a, "b": b}
    top_votes = max(o["votes"] for o in options)
    leaders = [o for o in options if o["votes"] == top_votes]
    if len(leaders) == 1 and len(versions) >= 3:
        decision["chosen"] = leaders[0]["words"]
        return decision
    ranked = sorted(leaders, key=lambda o: o["conf"], reverse=True)
    if len(ranked) == 1 or ranked[0]["conf"] - ranked[1]["conf"] >= CONF_MARGIN:
        decision["chosen"] = ranked[0]["words"]
    else:
        decision["candidates"] = ranked
    return decision


def _nearby(words: list[Word], ref: list[Word], a: int, b: int, source: int) -> list[Word]:
    """Words of `source` around reference span [a, b) (by time), for its typical confidence there."""
    if not ref:
        return words[:NEIGHBOURHOOD]
    t0 = ref[max(0, min(a, len(ref) - 1) - NEIGHBOURHOOD)].t
    t1 = ref[min(len(ref) - 1, b + NEIGHBOURHOOD)].t
    return [w for w in words if t0 <= w.t <= t1] or words[:NEIGHBOURHOOD]


def _settle_close_calls(decisions: list[dict], ref: list[Word], judge: Judge | None) -> None:
    close = [d for d in decisions if d["chosen"] is None]
    for start in range(0, len(close), JUDGE_BATCH):
        batch = close[start:start + JUDGE_BATCH]
        picks: list[int] | None = None
        if judge is not None:
            items = [{"id": n, "before": _plain(ref[max(0, d["a"] - CONTEXT_WORDS):d["a"]]),
                      "after": _plain(ref[d["b"]:d["b"] + CONTEXT_WORDS]),
                      "options": [_plain(o["words"]) or "(nothing said here)" for o in d["candidates"]]}
                     for n, d in enumerate(batch)]
            try:
                picks = list(judge(items))
            except Exception:  # the judge is a help, not a requirement: fall back to confidence
                picks = None
        for n, d in enumerate(batch):
            pick = picks[n] if picks and n < len(picks) else None
            if not isinstance(pick, int) or not 0 <= pick < len(d["candidates"]):
                pick = 0  # candidates are sorted by confidence
            d["chosen"] = d["candidates"][pick]["words"]


def _plain(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def to_segments(words: list[Word]) -> list[Segment]:
    """Group merged words back into segments, following each device's original sentence boundaries."""
    segments: list[Segment] = []
    group: list[Word] = []
    for word in words:
        if group and ((word.source, word.segment) != (group[-1].source, group[-1].segment)
                      or word.t - group[-1].end >= 1.5):
            segments.append(_segment(group))
            group = []
        group.append(word)
    if group:
        segments.append(_segment(group))
    return segments


def _segment(words: list[Word]) -> Segment:
    return Segment(words[0].t, words[-1].end, " ".join(w.text for w in words),
                   statistics.fmean(w.conf for w in words), None, None)


# --- the judge: the notes AI picks the wording most likely said

JUDGE_SYSTEM = """You compare transcripts of the same lecture or meeting recorded on different devices.
For each item you get the words just before and after a passage, and two or more versions of the passage.
Pick the version most likely to be what was actually said: coherent wording that fits the context. Recording
errors look like garbled or repeated words, words that don't fit, or text that ignores the context. A version
"(nothing said here)" means that device heard nothing; pick it only if the other versions look invented.
Never write new text: only choose."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"choices": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "integer"}, "pick": {"type": "integer"}},
        "required": ["id", "pick"], "additionalProperties": False}}},
    "required": ["choices"], "additionalProperties": False,
}


def llm_judge(llm, wait: Callable[[float], None] = time.sleep) -> Judge:
    """The notes AI as judge. Free tiers limit requests per minute, so rate limits are waited out, as for notes."""
    def judge(items: list[dict]) -> list[int]:
        user = "Items (pick is the 0-based index of the best version):\n" + json.dumps(items, ensure_ascii=False)
        reply = call_with_retries(
            lambda: llm.complete_json(system=JUDGE_SYSTEM, user=user, schema=JUDGE_SCHEMA, schema_name="choices"),
            wait=wait, on_rate_limited=lambda seconds: None, on_resumed=lambda: None)
        picks = {c["id"]: c["pick"] for c in reply.get("choices", []) if isinstance(c, dict)}
        return [picks.get(item["id"], -1) for item in items]
    return judge
