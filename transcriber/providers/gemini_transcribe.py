"""Gemini 3.5 Transcribe: one model that follows English/Urdu switching within a sentence.

Used for recordings set to "English + Urdu (mixed)". Word timestamps come back as annotations; they're grouped into
segments here, so the rest of the app sees the same Segment objects as from Groq.
"""
from pathlib import Path

from .base import PermanentError, RateLimited, Segment, TransientError, why

MODEL = "gemini-3.5-transcribe"
GAP_SEC = 0.8  # a pause this long starts a new segment
MAX_SEGMENT_SEC = 25.0
SENTENCE_END = (".", "?", "!", "؟", "۔")


class GeminiTranscriber:
    name = f"gemini/{MODEL}"

    def __init__(self, api_key: str, client=None):
        if client is None:
            from google import genai
            client = genai.Client(api_key=api_key)
        self._client = client

    def transcribe(self, audio_path: Path, *, language: str | None, prompt: str) -> list[Segment]:
        # The prompt (vocabulary) is not sent: Google rejects custom vocabulary together with word timestamps.
        uploaded = None
        try:
            uploaded = self._client.files.upload(file=str(audio_path))
            interaction = self._client.interactions.create(
                model=MODEL,
                input=[{"type": "audio", "uri": uploaded.uri, "mime_type": uploaded.mime_type or "audio/flac"}],
                generation_config={"transcription_config": {
                    "language_codes": [],  # detect each part of the audio: English, Urdu, or both
                    "mode": {"type": "verbatim", "timestamp_granularities": ["word"]}}},
            )
        except Exception as exc:
            raise map_error(exc) from exc
        finally:
            if uploaded is not None:
                try:
                    self._client.files.delete(name=uploaded.name)
                except Exception:  # best effort: the file expires on its own
                    pass
        return segments_from(interaction)


def segments_from(interaction) -> list[Segment]:
    words = [w for w in _annotations(interaction) if _get(w, "type") == "word_info"]
    return group_words([
        (str(_get(w, "text") or "").strip(), _seconds(_get(w, "start_offset")), _seconds(_get(w, "end_offset")))
        for w in words
    ])


def group_words(words: list[tuple[str, float, float]]) -> list[Segment]:
    segments: list[Segment] = []
    current: list[tuple[str, float, float]] = []
    for word in words:
        text, start, end = word
        if not text:
            continue
        if current:
            previous_end = current[-1][2]
            too_long = end - current[0][1] > MAX_SEGMENT_SEC
            paused = start - previous_end >= GAP_SEC
            sentence_done = current[-1][0].endswith(SENTENCE_END) and start - previous_end >= 0.3
            if paused or too_long or sentence_done:
                segments.append(_segment(current))
                current = []
        current.append(word)
    if current:
        segments.append(_segment(current))
    return segments


def _segment(words: list[tuple[str, float, float]]) -> Segment:
    return Segment(start=words[0][1], end=words[-1][2], text=" ".join(text for text, _, _ in words))


def _annotations(interaction):
    for step in _get(interaction, "steps") or []:
        for content in _get(step, "content") or []:
            yield from _get(content, "annotations") or []


def _get(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _seconds(value) -> float:
    """"1.250s" -> 1.25 (Google writes offsets as decimal seconds with an 's')."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).rstrip("s") or 0.0)


def map_error(exc: Exception) -> Exception:
    text = str(exc)
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429 or "RESOURCE_EXHAUSTED" in text:
        return RateLimited(60.0, "Gemini rate limit")
    if code in (400, 404) and "api key" not in text.lower():
        return PermanentError(f"Gemini rejected the audio or request ({why(exc)})")
    if code in (401, 403) or "API_KEY_INVALID" in text or "api key" in text.lower():
        return PermanentError("Invalid Gemini API key (check GEMINI_API_KEY in .env)")
    return TransientError(f"Gemini error ({why(exc)})")
