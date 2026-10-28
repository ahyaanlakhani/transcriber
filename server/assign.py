"""Give each transcribed word a speaker, then group words into speaker-attributed segments (pure logic)."""

PAUSE_SEC = 1.0  # a silence this long starts a new segment
MAX_SEGMENT_SEC = 30.0


def speaker_for(start: float, end: float, turns: list[tuple[float, float, str]]) -> str | None:
    """The speaker whose turn overlaps [start, end] most; the nearest turn when none overlaps."""
    best, best_overlap = None, 0.0
    for turn_start, turn_end, label in turns:
        overlap = min(end, turn_end) - max(start, turn_start)
        if overlap > best_overlap:
            best, best_overlap = label, overlap
    if best is not None or not turns:
        return best
    middle = (start + end) / 2
    return min(turns, key=lambda t: 0.0 if t[0] <= middle <= t[1] else min(abs(t[0] - middle), abs(t[1] - middle)))[2]


def assign_speakers(words: list[dict], turns: list[tuple[float, float, str]], *, pause: float = PAUSE_SEC,
                    max_length: float = MAX_SEGMENT_SEC) -> list[dict]:
    """words: [{start, end, text}] in order (text keeps Whisper's leading space). Returns segments
    [{start, end, text, speaker}], split on speaker change, a pause, or a long run."""
    segments: list[dict] = []
    current: dict | None = None
    for word in words:
        speaker = speaker_for(word["start"], word["end"], turns)
        if current and (speaker != current["speaker"] or word["start"] - current["end"] >= pause
                        or word["end"] - current["start"] > max_length):
            segments.append(current)
            current = None
        if current is None:
            current = {"start": word["start"], "end": word["end"], "text": "", "speaker": speaker}
        current["text"] += word["text"]
        current["end"] = word["end"]
    if current:
        segments.append(current)
    for segment in segments:
        segment["text"] = segment["text"].strip()
    return [s for s in segments if s["text"]]
