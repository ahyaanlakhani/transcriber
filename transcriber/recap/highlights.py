"""Highlights reel: the moments the notes say matter most, cut from the real recording and joined, so you hear
them in the speaker's own voice (no voice cloning). Fast: just cutting audio."""
import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import audio
from ..notes.model import Notes
from ..providers.base import Segment

HIGHLIGHTS_FILE = "highlights.mp3"
CLIP_SEC = 20.0      # each moment runs about this long, then stops at the end of a sentence
MAX_CLIP_SEC = 35.0
MAX_TOTAL_SEC = 300.0
GAP_SEC = 1.0
MIN_NEW_SEC = 5.0  # a clip must add at least this much that isn't already in the reel
# What to keep first: what was stressed, then definitions, facts, examples, advice, the gist, then other points.
PRIORITY = {("lecture", "stressed"): 0, ("lecture", "definition"): 1, ("lecture", "fact"): 2,
            ("lecture", "example"): 3, ("about_them", None): 3, ("summary", None): 4, ("lecture", "point"): 5,
            ("decisions", None): 4, ("action_items", None): 4}


@dataclass
class Clip:
    start: float
    end: float
    label: str


def _label(section: str, item) -> str:
    return f"{item.term}: {item.text}" if section == "lecture" and item.kind == "definition" and item.term else item.text


def _clip_end(start: float, segments: list[Segment]) -> float:
    end = start
    for segment in segments:
        if segment.end <= start:
            continue
        end = segment.end
        if end - start >= CLIP_SEC:
            break
    return min(end, start + MAX_CLIP_SEC)


def pick_clips(notes: Notes, segments: list[Segment], max_total: float = MAX_TOTAL_SEC) -> list[Clip]:
    candidates = []
    for section in ("lecture", "about_them", "summary", "decisions", "action_items"):
        for item in getattr(notes, section):
            rank = PRIORITY.get((section, item.kind if section == "lecture" else None))
            if rank is None or not item.cites or item.verdict == "unsupported":
                continue
            start = min(item.cites)
            candidates.append((rank, start, Clip(start, _clip_end(start, segments), _label(section, item))))
    chosen: list[Clip] = []
    total = 0.0
    for _, _, clip in sorted(candidates, key=lambda c: (c[0], c[1])):
        # Several notes often cite the same moment: count only the seconds a clip adds to the reel.
        covered = sum(max(0.0, min(clip.end, c.end) - max(clip.start, c.start)) for c in chosen)
        adds = (clip.end - clip.start) - covered
        if adds < MIN_NEW_SEC or total + adds > max_total + 0.01:
            continue
        chosen.append(clip)
        total += adds
    merged: list[Clip] = []
    for clip in sorted(chosen, key=lambda c: c.start):
        if merged and clip.start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = Clip(last.start, max(last.end, clip.end), f"{last.label} / {clip.label}")
        else:
            merged.append(clip)
    return merged


def make_highlights(source: Path, clips: list[Clip], dst: Path, work: Path) -> float:
    """Cut the clips from `source`, join them with short pauses, save as MP3. Returns the length in seconds."""
    work.mkdir(parents=True, exist_ok=True)
    try:
        pieces = []
        for index, clip in enumerate(clips):
            piece = work / f"clip{index:03d}.flac"
            audio.cut(source, clip.start, clip.end, piece)
            pieces.append(piece)
        joined = work / "joined.flac"
        audio.concat(pieces, joined, GAP_SEC)
        audio.to_mp3(joined, dst)
        return audio.probe_duration(dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)
