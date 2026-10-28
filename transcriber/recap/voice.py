"""The recap voice: Kokoro (open-source text-to-speech) on this laptop's CPU, saved as MP3."""
import contextlib
import sys
import threading
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .. import audio
from ..providers.base import PermanentError

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
PAUSE_SEC = 0.35
VOICES = {"af_heart": "Heart — warm, American", "af_bella": "Bella — bright, American",
          "am_michael": "Michael — calm, American", "bf_emma": "Emma — British", "bm_george": "George — British"}
DOWNLOADING = "Downloading the voice (first time only)"
# Gentle on an old laptop: half the processor, at low priority, so the computer stays usable while a recap is made.
THREADS = 2
_BELOW_NORMAL = 0x4000  # Windows priority classes
_NORMAL = 0x20


def session_options(threads: int = THREADS):
    import onnxruntime as rt
    options = rt.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    return options


def _set_process_priority(level: str):
    """Set this process's priority ('low' or a previous value); returns the previous value. Windows only."""
    if sys.platform != "win32":
        return None
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetCurrentProcess()
    previous = kernel32.GetPriorityClass(handle) or _NORMAL
    kernel32.SetPriorityClass(handle, _BELOW_NORMAL if level == "low" else level)
    return previous


@contextlib.contextmanager
def low_priority(set_priority=_set_process_priority):
    previous = set_priority("low")
    try:
        yield
    finally:
        if previous is not None:
            set_priority(previous)


class VoiceUnavailable(PermanentError):
    """Kokoro isn't installed or its model files couldn't be fetched."""


def _download(url: str, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as response, open(tmp, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    tmp.replace(dst)


class KokoroVoice:
    name = "kokoro"

    def __init__(self, folder: Path, download: Callable[[str, Path], None] | None = None):
        self.folder = folder
        self._download = download or _download
        self._engine = None
        self._lock = threading.Lock()

    def ready(self) -> bool:
        return all((self.folder / name).exists() for name in MODEL_FILES)

    def fetch_model(self, on_status: Callable[[str], None]) -> None:
        missing = [name for name in MODEL_FILES if not (self.folder / name).exists()]
        if not missing:
            return
        on_status(DOWNLOADING)
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            for name in missing:
                self._download(RELEASE + name, self.folder / name)
        except OSError as exc:
            raise VoiceUnavailable(f"The app couldn't download the voice: {exc}") from exc

    def _load(self, on_status: Callable[[str], None]):
        if self._engine is None:
            try:
                from kokoro_onnx import Kokoro
            except ImportError as exc:
                raise VoiceUnavailable("The recap voice isn't set up: kokoro-onnx isn't installed") from exc
            self.fetch_model(on_status)
            import onnxruntime as rt
            session = rt.InferenceSession(str(self.folder / MODEL_FILES[0]), sess_options=session_options(),
                                          providers=["CPUExecutionProvider"])
            self._engine = Kokoro.from_session(session, str(self.folder / MODEL_FILES[1]))
        return self._engine

    def speak(self, paragraphs: list[str], *, voice: str, speed: float, dst: Path,
              on_status: Callable[[str], None] | None = None) -> float:
        import numpy as np
        with self._lock, low_priority():  # one recap at a time, in the background
            engine = self._load(on_status or (lambda _: None))
            pieces, rate = [], 24000
            for index, text in enumerate(paragraphs):
                samples, rate = engine.create(text, voice=voice, speed=speed, lang="en-us")
                pieces.append(np.asarray(samples, dtype=np.float32))
                if index < len(paragraphs) - 1:
                    pieces.append(np.zeros(int(rate * PAUSE_SEC), dtype=np.float32))
        joined = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
        pcm = (np.clip(joined, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        audio.pcm_to_mp3(pcm, rate, dst)
        return len(joined) / rate
