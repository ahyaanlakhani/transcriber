"""Record a meeting (Zoom, Teams, Meet) straight from Windows: no browser sharing prompt, no tab to keep open.

The laptop reads two things at once: what comes out of the speakers (the other people on the call) and your
microphone. They stay separate, so each line can be labelled You or Others by which one was loud (see
channels.py), and they are mixed into the one stream that the live transcription already understands."""
import logging
import threading
import time

import numpy as np

from .channels import FRAME_SAMPLES, SAMPLE_RATE, frame_levels

log = logging.getLogger(__name__)

TICK_SEC = 0.1
MAX_BACKLOG_SEC = 2.0  # a device delivering faster than the clock never builds up more than this


class MeetingError(RuntimeError):
    """The meeting couldn't be captured; the message says what to do about it."""


class Resampler:
    """Any sample rate to 16 kHz mono, one device buffer at a time (the tail and position carry over)."""

    def __init__(self, rate_in: int, rate_out: int = SAMPLE_RATE):
        self.step = rate_in / rate_out
        self.width = max(1, round(self.step))
        self.tail = np.zeros(0, np.float32)
        self.pos = 0.0

    def feed(self, samples: np.ndarray) -> np.ndarray:
        data = np.concatenate([self.tail, samples.astype(np.float32)])
        if self.width > 1:
            smooth = np.convolve(data, np.ones(self.width, np.float32) / self.width, mode="valid")  # a crude low-pass
            self.tail = data[len(data) - (self.width - 1):]
        else:
            smooth, self.tail = data, data[:0]
        count = int((len(smooth) - 1 - self.pos) // self.step) + 1 if len(smooth) - 1 >= self.pos else 0
        out = np.interp(self.pos + self.step * np.arange(max(count, 0)), np.arange(len(smooth)), smooth) if count > 0 else np.zeros(0)
        self.pos = self.pos + max(count, 0) * self.step - len(smooth)
        return np.clip(out, -32768, 32767).astype(np.int16)


def to_mono(data: bytes, channels: int) -> np.ndarray:
    samples = np.frombuffer(data, dtype=np.int16)
    if channels > 1:
        samples = samples[: len(samples) // channels * channels].reshape(-1, channels).astype(np.float32).mean(axis=1)
    return samples.astype(np.float32)


class WasapiDevices:
    """Windows: the default speakers' loopback plus the default microphone (PyAudioWPatch)."""

    def __init__(self):
        self._lib = None

    def _pyaudio(self):
        if self._lib is None:
            try:
                import pyaudiowpatch as lib
            except ImportError as exc:
                raise MeetingError("Meeting capture needs Windows and the PyAudioWPatch package.") from exc
            self._lib = lib
        return self._lib

    def _pick(self, p):
        lib = self._lib
        try:
            api = p.get_host_api_info_by_type(lib.paWASAPI)
            out = p.get_device_info_by_index(api["defaultOutputDevice"])
            mic = p.get_device_info_by_index(api["defaultInputDevice"])
        except (OSError, KeyError) as exc:
            raise MeetingError("I couldn't find your speakers or microphone. Check Windows' Sound settings.") from exc
        if not out.get("isLoopbackDevice"):
            loop = next((d for d in p.get_loopback_device_info_generator() if out["name"] in d["name"]), None)
            if loop is None:
                raise MeetingError("Windows didn't offer a way to listen to your speakers.")
            out = loop
        return out, mic

    def info(self) -> dict:
        try:
            lib = self._pyaudio()
            p = lib.PyAudio()
        except (MeetingError, OSError) as exc:
            return {"available": False, "reason": str(exc), "output": None, "input": None}
        try:
            out, mic = self._pick(p)
            return {"available": True, "reason": None, "output": out["name"].replace(" [Loopback]", ""), "input": mic["name"]}
        except MeetingError as exc:
            return {"available": False, "reason": str(exc), "output": None, "input": None}
        finally:
            p.terminate()

    def open(self, on_you, on_others):
        """Start both streams. Each callback gets (bytes, sample rate, channels). Returns a function that stops them."""
        lib = self._pyaudio()
        p = lib.PyAudio()
        streams = []
        try:
            out, mic = self._pick(p)
            for device, callback in ((out, on_others), (mic, on_you)):
                channels = max(1, min(int(device["maxInputChannels"]), 2))
                rate = int(device["defaultSampleRate"])
                streams.append(p.open(
                    format=lib.paInt16, channels=channels, rate=rate, input=True, input_device_index=device["index"],
                    frames_per_buffer=rate // 20,
                    stream_callback=lambda data, frames, info, status, cb=callback, r=rate, c=channels: (cb(data, r, c), lib.paContinue)))
        except OSError as exc:
            for stream in streams:
                stream.close()
            p.terminate()
            raise MeetingError("Windows wouldn't let me open the microphone or speakers. In Settings, Privacy & security, "
                               "Microphone, turn on “Let desktop apps access your microphone”.") from exc
        except MeetingError:
            p.terminate()
            raise

        def stop() -> None:
            for stream in streams:
                try:
                    stream.stop_stream()
                    stream.close()
                except OSError:
                    pass
            p.terminate()
        return stop


class MeetingCapture:
    """Feeds one live session: every tick, the next slice of both tracks, kept in step with the clock.
    Windows delivers nothing while no sound is playing, so a quiet track is padded with silence."""

    def __init__(self, live, sid: str, devices, clock=time.monotonic):
        self.live, self.sid, self.devices, self._clock = live, sid, devices, clock
        self.levels = {"you": 0.0, "others": 0.0}
        self._lock = threading.Lock()
        self._buffers = {"you": np.zeros(0, np.int16), "others": np.zeros(0, np.int16)}
        self._resamplers: dict[str, Resampler] = {}
        self._produced = 0
        self._t0 = 0.0
        self._stop_devices = None
        self._thread = None
        self._halt = threading.Event()

    def _feed(self, which: str):
        def callback(data: bytes, rate: int, channels: int) -> None:
            resampler = self._resamplers.setdefault(which, Resampler(rate))
            samples = resampler.feed(to_mono(data, channels))
            with self._lock:
                self._buffers[which] = np.concatenate([self._buffers[which], samples])
        return callback

    def start(self) -> None:
        self._t0 = self._clock()
        self._stop_devices = self.devices.open(self._feed("you"), self._feed("others"))
        self._thread = threading.Thread(target=self._run, name=f"meeting-{self.sid[:6]}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._halt.wait(TICK_SEC):
            try:
                self.pump()
            except Exception:
                log.exception("Meeting capture error")

    def pump(self, now: float | None = None) -> int:
        """Send everything the clock says has happened since last time. Returns the samples sent per track."""
        now = self._clock() if now is None else now
        target = int((now - self._t0) * SAMPLE_RATE) // FRAME_SAMPLES * FRAME_SAMPLES
        count = target - self._produced
        if count <= 0:
            return 0
        slices = {}
        with self._lock:
            for which, buffer in self._buffers.items():
                head = buffer[:count]
                if len(head) < count:
                    head = np.concatenate([head, np.zeros(count - len(head), np.int16)])  # silence
                rest = buffer[count:]
                self._buffers[which] = rest[-int(MAX_BACKLOG_SEC * SAMPLE_RATE):]  # never fall far behind the clock
                slices[which] = head
        mixed = np.clip(slices["you"].astype(np.int32) + slices["others"].astype(np.int32), -32768, 32767).astype(np.int16)
        self.live.add_pcm(self.sid, mixed.tobytes(), slices["you"].tobytes(), slices["others"].tobytes())
        for which, head in slices.items():
            levels = frame_levels(head)
            self.levels[which] = round(float(levels.max()) / 8000.0, 3) if len(levels) else 0.0
        self._produced += count
        return count

    def stop(self) -> None:
        self._halt.set()
        if self._stop_devices:
            self._stop_devices()
            self._stop_devices = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        try:
            self.pump()  # the last slice
        except KeyError:
            pass


class MeetingManager:
    """Starts and stops meeting captures; each one is a live recording session fed from Windows."""

    def __init__(self, live, devices, clock=time.monotonic):
        self.live, self.devices, self._clock = live, devices, clock
        self.captures: dict[str, MeetingCapture] = {}

    def info(self) -> dict:
        return {**self.devices.info(), "recording": bool(self.captures)}

    def start(self, *, title: str, subject_id: int | None, language: str, privacy: str | None = None) -> str:
        if self.captures:
            raise MeetingError("A meeting is already being recorded.")
        sid = self.live.create(title=title, subject_id=subject_id, language=language, meeting=True, privacy=privacy)
        capture = MeetingCapture(self.live, sid, self.devices, self._clock)
        try:
            capture.start()
        except MeetingError:
            self.live.discard(sid)
            raise
        self.captures[sid] = capture
        return sid

    def poll(self, sid: str, after: int = 0) -> dict:
        body = self.live.poll(sid, after)
        capture = self.captures.get(sid)
        return {**body, "levels": dict(capture.levels) if capture else None}

    def finish(self, sid: str) -> str | None:
        capture = self.captures.pop(sid, None)
        if capture:
            capture.stop()
        return self.live.finish(sid)

    def discard(self, sid: str) -> None:
        capture = self.captures.pop(sid, None)
        if capture:
            capture.stop()
        self.live.discard(sid)

    def stop_all(self) -> None:
        """App shutdown: stop listening. The audio so far becomes a recording at the next start."""
        for capture in list(self.captures.values()):
            capture.stop()
        self.captures.clear()
