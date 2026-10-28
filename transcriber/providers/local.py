"""Transcription on this laptop with faster-whisper, for recordings set to Local only. Nothing leaves the computer.

It is optional: the library and a model file are not installed with the app. Set LOCAL_TRANSCRIBER in .env (for example
`small` or `base.en`; `faster-whisper:small` also works) and install the library (`pip install faster-whisper`). The first
use downloads the model once. On a CPU without a graphics card this is much slower than the cloud (a small model may take
about as long as the recording itself), so it is only used where the cloud is not allowed."""
from pathlib import Path

from .base import PermanentError, Segment

NEEDS_LIBRARY = ("Local transcription needs the faster-whisper library. Install it with: pip install faster-whisper "
                 "(then restart the app).")


def model_size(spec: str) -> str:
    """'small', 'faster-whisper:small' and 'local:small' all mean the model called small."""
    return spec.strip().split(":", 1)[-1].strip() or "small"


class LocalWhisperTranscriber:
    def __init__(self, size: str = "small", *, device: str = "cpu", compute_type: str = "int8", model_factory=None):
        self.size = size
        self.name = f"local/faster-whisper-{size}"
        self._device, self._compute_type = device, compute_type
        self._factory = model_factory
        self._model = None  # loaded on first use: a model is hundreds of megabytes of memory

    def _load(self):
        if self._model is None:
            factory = self._factory
            if factory is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise PermanentError(NEEDS_LIBRARY) from exc
                factory = WhisperModel
            try:
                self._model = factory(self.size, device=self._device, compute_type=self._compute_type)
            except Exception as exc:  # a missing model file, no disk space, no memory
                raise PermanentError(f"The local model '{self.size}' couldn't be loaded ({type(exc).__name__})") from exc
        return self._model

    def transcribe(self, audio_path: Path, *, language: str | None, prompt: str) -> list[Segment]:
        model = self._load()
        try:
            found, _info = model.transcribe(str(audio_path), language=language or None, initial_prompt=prompt or None,
                                            vad_filter=True, temperature=0.0)
            return [Segment(start=float(s.start), end=float(s.end), text=s.text.strip(),
                            avg_logprob=getattr(s, "avg_logprob", None), no_speech_prob=getattr(s, "no_speech_prob", None),
                            compression_ratio=getattr(s, "compression_ratio", None)) for s in found]
        except PermanentError:
            raise
        except Exception as exc:
            raise PermanentError(f"Local transcription failed ({type(exc).__name__})") from exc
