"""Groq-hosted Whisper large-v3."""
from pathlib import Path

import groq

from .base import PermanentError, RateLimited, Segment, TransientError, why

MODEL = "whisper-large-v3"
DEFAULT_RETRY_AFTER = 60.0


class GroqTranscriber:
    name = f"groq/{MODEL}"

    def __init__(self, api_key: str, client=None):
        # The pipeline owns retries so it can show status; the SDK must not retry on its own.
        self._client = client or groq.Groq(api_key=api_key, max_retries=0, timeout=300)

    def transcribe(self, audio_path: Path, *, language: str | None, prompt: str) -> list[Segment]:
        kwargs = {
            "model": MODEL,
            "file": (audio_path.name, audio_path.read_bytes()),
            "response_format": "verbose_json",
            "timestamp_granularities": ["segment"],
            "temperature": 0.0,
        }
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt
        try:
            response = self._client.audio.transcriptions.create(**kwargs)
        except groq.APIError as exc:
            raise map_error(exc) from exc
        return parse_segments(response)


def parse_segments(response) -> list[Segment]:
    data = response.model_dump()  # verbose_json fields arrive as pydantic "extra" fields
    return [
        Segment(
            start=float(s["start"]),
            end=float(s["end"]),
            text=s["text"].strip(),
            avg_logprob=s.get("avg_logprob"),
            no_speech_prob=s.get("no_speech_prob"),
            compression_ratio=s.get("compression_ratio"),
        )
        for s in data.get("segments") or []
    ]


def map_error(exc: groq.APIError) -> Exception:
    if isinstance(exc, groq.RateLimitError):
        return RateLimited(_retry_after(exc.response), "Groq rate limit")
    if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)):
        return PermanentError("Invalid Groq API key (check GROQ_API_KEY in .env)")
    if isinstance(exc, groq.InternalServerError):
        return TransientError(f"Groq server error ({why(exc)})")
    if isinstance(exc, groq.APIConnectionError):  # includes APITimeoutError
        return TransientError(f"Could not reach Groq ({why(exc)})")
    if isinstance(exc, groq.APIStatusError):
        return PermanentError(f"Groq rejected the request ({why(exc)})")
    return PermanentError(f"Groq error ({why(exc)})")


def _retry_after(response) -> float:
    try:
        return max(1.0, float(response.headers.get("retry-after")))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
