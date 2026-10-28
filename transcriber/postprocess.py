"""Clean up and shape transcribed segments (pure logic)."""
import re
from dataclasses import dataclass, replace

from .providers.base import Segment

NO_SPEECH_THRESHOLD = 0.6
LOGPROB_THRESHOLD = -1.0
REPEAT_RUN = 3
PARAGRAPH_GAP_SEC = 2.0
PARAGRAPH_MAX_SEC = 60.0


@dataclass
class Paragraph:
    start: float
    text: str
    speaker: str | None = None  # raw label from the transcriber, or a display name after `named()`


def filter_hallucinations(segments: list[Segment]) -> list[Segment]:
    """Drop text Whisper invented over silence, and collapse runaway repeats."""
    kept = [s for s in segments if s.text.strip() and not _probably_silence(s)]
    return _collapse_repeats(kept)


def _probably_silence(segment: Segment) -> bool:
    # Whisper's own rule: likely no speech AND low confidence.
    return (
        segment.no_speech_prob is not None
        and segment.avg_logprob is not None
        and segment.no_speech_prob > NO_SPEECH_THRESHOLD
        and segment.avg_logprob < LOGPROB_THRESHOLD
    )


def _normalized(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())


def _collapse_repeats(segments: list[Segment]) -> list[Segment]:
    result: list[Segment] = []
    i = 0
    while i < len(segments):
        key = _normalized(segments[i].text)
        j = i
        while j + 1 < len(segments) and _normalized(segments[j + 1].text) == key:
            j += 1
        run = segments[i : j + 1]
        result.extend(run[:1] if len(run) >= REPEAT_RUN else run)
        i = j + 1
    return result


def offset(segments: list[Segment], seconds: float) -> list[Segment]:
    return [replace(s, start=s.start + seconds, end=s.end + seconds) for s in segments]


def to_paragraphs(segments: list[Segment]) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    texts: list[str] = []
    start = previous_end = 0.0
    speaker: str | None = None
    for segment in segments:
        if texts and (
            segment.start - previous_end >= PARAGRAPH_GAP_SEC
            or segment.start - start >= PARAGRAPH_MAX_SEC
            or segment.speaker != speaker
        ):
            paragraphs.append(Paragraph(start, " ".join(texts), speaker))
            texts = []
        if not texts:
            start, speaker = segment.start, segment.speaker
        texts.append(segment.text.strip())
        previous_end = segment.end
    if texts:
        paragraphs.append(Paragraph(start, " ".join(texts), speaker))
    return paragraphs


def speaker_names(labels: list[str | None], custom: dict[str, str]) -> dict[str, str]:
    """Display name per raw label: the user's name if set, else "Speaker N" in order of first appearance."""
    names: dict[str, str] = {}
    for label in labels:
        if label and label not in names:
            names[label] = custom.get(label) or f"Speaker {len(names) + 1}"
    return names


def named(paragraphs: list[Paragraph], names: dict[str, str]) -> list[Paragraph]:
    return [replace(p, speaker=names.get(p.speaker, p.speaker)) if p.speaker else p for p in paragraphs]


def named_paragraphs(segments: list[Segment], custom: dict[str, str]) -> list[Paragraph]:
    """Paragraphs with speakers shown by display name, ready for notes, Ask and exports."""
    paragraphs = to_paragraphs(segments)
    return named(paragraphs, speaker_names([p.speaker for p in paragraphs], custom))
