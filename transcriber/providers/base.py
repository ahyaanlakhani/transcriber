"""Provider-neutral transcription types."""
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Protocol


@dataclass
class Segment:
    start: float  # seconds, relative to the audio that was transcribed
    end: float
    text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    speaker: str | None = None  # raw diarization label, e.g. "SPEAKER_00"; None when speakers weren't detected

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Segment":
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio_path: Path, *, language: str | None, prompt: str) -> list[Segment]:
        """Transcribe one audio file. `language=None` means auto-detect."""
        ...


def why(exc: BaseException) -> str:
    """A short, content-free reason for an error: its HTTP status or its type. Provider error text can quote what was
    sent or what came back, so it is never shown or logged."""
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return f"HTTP {code}" if isinstance(code, int) else type(exc).__name__


class RateLimited(Exception):
    def __init__(self, retry_after: float, message: str = "Rate limited"):
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(Exception):
    """Network errors and server errors: worth retrying."""


class PermanentError(Exception):
    """Bad key, rejected request, unreadable audio: retrying will not help."""
