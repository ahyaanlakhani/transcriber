"""Live recording: the browser sends audio pieces while you speak; text comes back window by window.

Pieces (WebM/Opus from MediaRecorder) are fed, in order, into a long-running ffmpeg that turns them into raw
16 kHz mono PCM on disk. A background thread cuts that audio at natural pauses and sends each window to Groq.
When you stop, the audio and the live transcript become a normal recording.
"""
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from array import array
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import audio, privacy
from .channels import NAMES, OTHERS_FILE, YOU_FILE, label_segments
from .config import LANGUAGES, MIXED
from .pipeline import AUDIO_FILE
from .postprocess import filter_hallucinations, offset, to_paragraphs
from .prompting import build_prompt
from .providers.base import PermanentError, RateLimited, Segment, Transcriber, TransientError
from .retry import call_with_retries, resume_message
from .store import Store

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320  # 20 ms
FRAME_BYTES = FRAME_SAMPLES * 2
FRAMES_PER_SEC = SAMPLE_RATE // FRAME_SAMPLES
MIN_SEC, MAX_SEC = 8.0, 15.0
BUSY_MIN_SEC, BUSY_MAX_SEC = 25.0, 30.0  # on heavy days, fewer and longer requests
BUSY_REQUESTS = 1500  # Groq's free tier allows 2,000 transcription requests a day
PAUSE_SEC = 0.3
SILENCE_RMS = 250.0  # a window whose loudest 20 ms stays under this (about -42 dBFS) is not sent
PAUSE_RATIO = 0.25  # a pause is quieter than a quarter of the window's typical loudness
CONTEXT_SEGMENTS = 20
LIVE_ORIGINAL = "Live recording"
MEETING_ORIGINAL = "Meeting recording"
NO_GROQ = ("Live text needs a Groq key (GROQ_API_KEY in .env). Keep going: the recording is saved and "
           "transcribed when you stop.")


def frame_rms(pcm: bytes) -> list[float]:
    """Loudness of each whole 20 ms frame of 16-bit mono PCM."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % FRAME_BYTES])
    if sys.byteorder == "big":
        samples.byteswap()
    return [math.sqrt(sum(v * v for v in samples[i:i + FRAME_SAMPLES]) / FRAME_SAMPLES)
            for i in range(0, len(samples), FRAME_SAMPLES)]


def choose_cut(rms, *, min_sec: float = MIN_SEC, max_sec: float = MAX_SEC, final: bool = False) -> int | None:
    """How many frames of untranscribed audio to send now, or None to wait for more.

    Cuts in the middle of the quietest pause between min_sec and max_sec; at max_sec when nobody pauses."""
    n = len(rms)
    lo, hi, pause = int(min_sec * FRAMES_PER_SEC), int(max_sec * FRAMES_PER_SEC), int(PAUSE_SEC * FRAMES_PER_SEC)
    if final:  # whatever is left, but never more than one window (Groq caps uploads at 25 MB)
        return min(n, hi) or None
    if n < lo:
        return None
    upper = min(n, hi)
    prefix = [0.0]
    for value in rms[:upper]:
        prefix.append(prefix[-1] + value)
    best_end, best = None, math.inf
    for end in range(max(lo, pause), upper + 1):
        mean = (prefix[end] - prefix[end - pause]) / pause
        if mean < best:
            best_end, best = end, mean
    typical = sorted(rms[:upper])[upper // 2]
    if best_end is not None and best <= max(SILENCE_RMS, PAUSE_RATIO * typical):
        return best_end - pause // 2
    return hi if n >= hi else None


class LiveSession:
    def __init__(self, sid: str, folder: Path, meta: dict, raw: bool = False):
        """raw: the audio arrives as ready-made 16 kHz PCM (a meeting captured from Windows, with your microphone
        and the call's sound as separate tracks) instead of WebM pieces from the browser."""
        self.id, self.folder, self.meta, self.raw = sid, folder, meta, raw
        self.you_rms, self.others_rms = array("f"), array("f")
        self.pcm_path = folder / "audio.pcm"
        self.lock = threading.Lock()  # guards the fields below
        self.work = threading.Lock()  # one transcription at a time per session
        self.write_lock = threading.Lock()
        self.next_seq = 0
        self.held: dict[int, bytes] = {}
        self.rms = array("f")
        self._partial = b""
        self.cursor = 0  # frames transcribed or skipped
        self.segments: list[Segment] = []
        self.status = "recording"  # recording | finishing | done
        self.notice: str | None = None
        self.fallback = False  # live text gave up: the recording goes to the normal pipeline
        self.recording_id: str | None = None
        self.decoder = self.reader = None
        if not raw:
            self.decoder = subprocess.Popen(
                [audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-probesize", "32768", "-analyzeduration", "0",
                 "-i", "pipe:0", "-vn", "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.reader = threading.Thread(target=self._read, name=f"live-{sid[:6]}", daemon=True)
            self.reader.start()

    def add_pcm(self, mixed: bytes, you: bytes, others: bytes) -> None:
        """A meeting's next slice: the mix (what gets transcribed) and each track on its own."""
        with self.write_lock:
            if self.status != "recording":
                return
            for name, data in (("audio.pcm", mixed), ("you.pcm", you), ("others.pcm", others)):
                with open(self.folder / name, "ab") as out:
                    out.write(data)
            with self.lock:
                self.rms.extend(frame_rms(mixed))
                self.you_rms.extend(frame_rms(you))
                self.others_rms.extend(frame_rms(others))

    def _read(self) -> None:
        fd = self.decoder.stdout.fileno()
        with open(self.pcm_path, "ab") as out:
            while chunk := os.read(fd, 65536):
                out.write(chunk)
                out.flush()
                data = self._partial + chunk
                whole = len(data) - len(data) % FRAME_BYTES
                levels = frame_rms(data[:whole])
                with self.lock:
                    self.rms.extend(levels)
                    self._partial = data[whole:]

    def add_chunk(self, seq: int, data: bytes) -> None:
        if self.raw:
            return
        with self.write_lock:
            with self.lock:
                if seq < self.next_seq or seq in self.held or self.status != "recording":
                    return
                self.held[seq] = data
                ready = []
                while self.next_seq in self.held:
                    ready.append(self.held.pop(self.next_seq))
                    self.next_seq += 1
            for piece in ready:  # outside self.lock: the reader needs it while ffmpeg drains
                try:
                    self.decoder.stdin.write(piece)
                    self.decoder.stdin.flush()
                except OSError:
                    log.warning("Live decoder for %s stopped accepting audio", self.id)

    def close_input(self) -> None:
        """No more audio: write any pieces held back by a gap, then let ffmpeg finish."""
        if self.raw:
            return
        with self.write_lock:
            with self.lock:
                leftovers = [self.held[seq] for seq in sorted(self.held)]
                self.held.clear()
            try:
                for piece in leftovers:
                    self.decoder.stdin.write(piece)
                self.decoder.stdin.close()
            except OSError:
                pass
        self.reader.join(timeout=60)
        self.decoder.wait(timeout=30)

    def kill(self) -> None:
        if self.raw:
            return
        try:
            self.decoder.kill()
        except OSError:
            pass
        self.reader.join(timeout=10)

    def pcm_slice(self, start_frame: int, frames: int) -> bytes:
        with open(self.pcm_path, "rb") as f:
            f.seek(start_frame * FRAME_BYTES)
            return f.read(frames * FRAME_BYTES)


class LiveManager:
    def __init__(self, store: Store, transcriber: Transcriber | None, live_dir: Path, recordings_dir: Path, *,
                 on_recording: Callable[[str], None] = lambda rid: None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 start_thread: bool = True):
        self.store, self.transcriber = store, transcriber
        self.live_dir, self.recordings_dir = live_dir, recordings_dir
        self.on_recording = on_recording
        self._sleep, self._clock = sleep, clock
        self.sessions: dict[str, LiveSession] = {}
        self.finished: dict[str, str | None] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        if start_thread:
            self._thread = threading.Thread(target=self._run, name="live", daemon=True)
            self._thread.start()

    # --- lifecycle

    def stop(self) -> None:
        """App shutdown: sessions stay on disk and become recordings at the next start."""
        self._stop.set()
        for session in list(self.sessions.values()):
            session.kill()

    def recover(self) -> list[str]:
        """Turn audio left by a crash or shutdown into recordings for the normal pipeline."""
        created = []
        if not self.live_dir.exists():
            return created
        for folder in sorted(p for p in self.live_dir.iterdir() if p.is_dir()):
            if folder.name in self.sessions:
                continue
            try:
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            pcm = folder / "audio.pcm"
            if pcm.exists() and pcm.stat().st_size >= FRAME_BYTES:
                created.append(self._save_recording(folder, meta, pcm, segments=None))
            shutil.rmtree(folder, ignore_errors=True)
        return created

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.step()
            except Exception:
                privacy.log_failure(log, "Live transcription error")
                worked = False
            if not worked:
                self._stop.wait(0.5)

    # --- API

    def create(self, *, title: str, subject_id: int | None, language: str, meeting: bool = False,
               privacy: str | None = None) -> str:
        sid = uuid.uuid4().hex
        folder = self.live_dir / sid
        folder.mkdir(parents=True)
        meta = {"title": " ".join(title.split()), "subject_id": subject_id, "language": language,
                "started_at": datetime.now().isoformat(timespec="minutes"),
                "started_utc": datetime.now(UTC).isoformat(timespec="seconds")}
        if meeting:
            meta["meeting"] = True
        if privacy in ("standard", "private", "local_only"):  # decided before the first audio leaves the laptop
            meta["privacy"] = privacy
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        session = LiveSession(sid, folder, meta, raw=meeting)
        if self.transcriber is None:
            session.notice = NO_GROQ
        with self._lock:
            self.sessions[sid] = session
        return sid

    def add_chunk(self, sid: str, seq: int, data: bytes) -> None:
        self._get(sid).add_chunk(seq, data)

    def add_pcm(self, sid: str, mixed: bytes, you: bytes, others: bytes) -> None:
        self._get(sid).add_pcm(mixed, you, others)

    def poll(self, sid: str, after: int = 0) -> dict:
        if sid in self.finished:
            return {"status": "done", "recording_id": self.finished[sid], "segments": [], "next": after}
        session = self._get(sid)
        with session.lock:
            segments = session.segments[after:]
            return {"status": session.status, "duration_sec": round(len(session.rms) / FRAMES_PER_SEC, 2),
                    "transcribed_sec": round(session.cursor / FRAMES_PER_SEC, 2),
                    "segments": [{"start": s.start, "end": s.end, "text": s.text,
                                  **({"speaker": NAMES.get(s.speaker)} if session.raw else {})} for s in segments],
                    "next": after + len(segments), "notice": session.notice, "recording_id": None}

    def finish(self, sid: str) -> str | None:
        """Stop recording and save it. Returns the recording id (None if nothing was heard).

        Never waits on a backlog: if the live text is behind by more than one window (recorded offline, or Groq
        was failing), the recording is saved at once for the normal pipeline, which transcribes long audio in
        proper chunks and resumes after errors."""
        if sid in self.finished:
            return self.finished[sid]
        session = self._get(sid)
        with session.lock:
            session.status = "finishing"
        session.close_input()
        with session.work:  # let a window already in flight finish first
            segments = self._final_transcript(session)
        rid = None
        if session.pcm_path.exists() and session.pcm_path.stat().st_size >= FRAME_BYTES:
            rid = self._save_recording(session.folder, session.meta, session.pcm_path, segments=segments)
        shutil.rmtree(session.folder, ignore_errors=True)
        with self._lock:
            self.sessions.pop(sid, None)
            self.finished[sid] = rid
        session.status, session.recording_id = "done", rid
        return rid

    def _final_transcript(self, session: LiveSession) -> list[Segment] | None:
        """The complete live transcript, after at most one quick try at the last few seconds; None when the
        recording should go to the normal pipeline instead."""
        if self.transcriber is None or session.fallback or session.meta.get("language") == MIXED:
            return None  # mixed English/Urdu gets its final transcript from Gemini, in the normal pipeline
        with session.lock:
            behind = len(session.rms) - session.cursor
            troubled = session.notice is not None
        if troubled or behind > MAX_SEC * FRAMES_PER_SEC:
            return None
        if behind > 0 and self._transcribe_next(session, final=True, patient=False) == "retry":
            return None
        with session.lock:
            return list(session.segments) if session.cursor >= len(session.rms) else None

    def discard(self, sid: str) -> None:
        session = self.sessions.get(sid)
        if session is None:
            return
        session.kill()
        with self._lock:
            self.sessions.pop(sid, None)
            self.finished[sid] = None
        shutil.rmtree(session.folder, ignore_errors=True)

    def wait_for_audio(self, sid: str, settle: float = 0.6, limit: float = 15.0) -> None:
        """Wait (real time) until the decoder has caught up with the audio sent so far. For tests and finish."""
        session = self._get(sid)
        deadline = time.monotonic() + limit
        last, since = -1, time.monotonic()
        while time.monotonic() < deadline:
            with session.lock:
                frames = len(session.rms)
            if frames != last:
                last, since = frames, time.monotonic()
            elif time.monotonic() - since >= settle:
                return
            time.sleep(0.05)

    def abandon_for_test(self, sid: str) -> None:
        session = self.sessions.pop(sid)
        session.kill()

    # --- transcription

    def step(self) -> bool:
        """Transcribe at most one window from any recording session. False when there's nothing to do."""
        if self.transcriber is None:
            return False
        for session in list(self.sessions.values()):
            if session.status != "recording" or session.fallback:
                continue
            if not session.work.acquire(blocking=False):
                continue
            try:
                if self._transcribe_next(session, final=False) != "idle":
                    return True
            finally:
                session.work.release()
        return False

    def _window_limits(self) -> tuple[float, float]:
        since = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
        if self.store.usage_requests(self._provider(), since) >= BUSY_REQUESTS:
            return BUSY_MIN_SEC, BUSY_MAX_SEC
        return MIN_SEC, MAX_SEC

    def _provider(self) -> str:
        return self.transcriber.name.split("/")[0]

    def _transcribe_next(self, session: LiveSession, *, final: bool, patient: bool = True) -> str:
        """One window: "sent", "skipped" (silence), "idle" (wait for more audio) or "retry" (trouble).

        patient=False makes a single attempt: no waiting out rate limits or network errors."""
        with session.lock:
            start = session.cursor
            pending = session.rms[start:]
        min_sec, max_sec = self._window_limits()
        cut = choose_cut(pending, min_sec=min_sec, max_sec=max_sec, final=final)
        if not cut:
            return "idle"
        if max(pending[:cut]) < SILENCE_RMS:
            with session.lock:
                session.cursor = start + cut
            return "skipped"
        path = session.folder / "window.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(session.pcm_slice(start, cut))
        with session.lock:
            context = " ".join(s.text for s in session.segments[-CONTEXT_SEGMENTS:])
        prompt = build_prompt(self.store.get_settings()["vocabulary"], context)
        language = LANGUAGES.get(session.meta.get("language"), "en")

        def set_notice(text):
            with session.lock:
                session.notice = text

        with privacy.scope("live", classification=self._session_class(session)):
            return self._send_window(session, path, start, cut, language, prompt, patient, set_notice)

    def _session_class(self, session: LiveSession) -> str:
        """The class a live session was started with, known before its first audio window leaves the laptop."""
        chosen = session.meta.get("privacy")
        return chosen if chosen in ("standard", "private", "local_only") else self._default_privacy()

    def _default_privacy(self) -> str:
        chosen = self.store.get_settings().get("default_privacy")
        return chosen if chosen in ("standard", "private") else "standard"

    def _send_window(self, session, path, start, cut, language, prompt, patient, set_notice) -> str:
        if not patient:
            try:
                raw = self.transcriber.transcribe(path, language=language, prompt=prompt)
            except (RateLimited, TransientError, PermanentError) as exc:
                log.info("Last live window not transcribed (%s); saving for the normal pipeline", exc)
                return "retry"
        else:
            try:
                raw = call_with_retries(
                    lambda: self.transcriber.transcribe(path, language=language, prompt=prompt),
                    wait=self._sleep, on_rate_limited=lambda seconds: set_notice(resume_message(seconds)),
                    on_resumed=lambda: set_notice(None))
            except PermanentError as exc:
                set_notice(f"Live text stopped: {exc}. Keep going: the recording is transcribed normally when you stop.")
                session.fallback = True
                return "retry"
            except TransientError:
                set_notice("Can't reach Groq right now. The text will catch up when the connection is back.")
                self._sleep(10.0)
                return "retry"
        segments = offset(filter_hallucinations(raw), start / FRAMES_PER_SEC)
        if session.raw:  # a meeting: You when your microphone carried the line, Others when the call did
            with session.lock:
                segments = label_segments(segments, session.you_rms, session.others_rms)
        with session.lock:
            session.segments.extend(segments)
            session.cursor = start + cut
            session.notice = None
        self.store.record_usage(cut / FRAMES_PER_SEC, self._provider())
        return "sent"

    # --- saving

    def _save_recording(self, folder: Path, meta: dict, pcm: Path, *, segments: list[Segment] | None) -> str:
        rid = uuid.uuid4().hex
        rec_dir = self.recordings_dir / rid
        rec_dir.mkdir(parents=True)
        audio.pcm_to_flac(pcm, rec_dir / AUDIO_FILE, SAMPLE_RATE)
        meeting = bool(meta.get("meeting"))
        started = meta.get("started_at", datetime.now().isoformat(timespec="minutes")).replace("T", " ")
        title = meta.get("title") or f"{'Meeting' if meeting else LIVE_ORIGINAL} — {started}"
        subject_id = meta.get("subject_id")
        if subject_id is not None and self.store.get_subject(subject_id) is None:
            subject_id = None
        self.store.create_recording(rid, title, subject_id, meta.get("language") or "en",
                                    MEETING_ORIGINAL if meeting else LIVE_ORIGINAL)
        self.store.update_recording(rid, duration_sec=round(pcm.stat().st_size / (SAMPLE_RATE * 2), 2),
                                    recorded_at=meta.get("started_utc"))
        if meta.get("privacy") in ("standard", "private", "local_only"):
            self.store.set_privacy(rid, meta["privacy"])
        if meeting:  # keep the two tracks: they label the lines now, or after the normal pipeline transcribes
            for source, name in ((folder / "you.pcm", YOU_FILE), (folder / "others.pcm", OTHERS_FILE)):
                if source.exists() and source.stat().st_size >= FRAME_BYTES:
                    audio.pcm_to_flac(source, rec_dir / name, SAMPLE_RATE)
            self.store.update_recording(rid, notes_style="meeting")
            self.store.set_speaker_names(rid, NAMES)
        if segments is not None:
            text = "\n\n".join(p.text for p in to_paragraphs(segments))
            self.store.save_transcript(rid, text, segments, f"{self.transcriber.name} (live)")
            self.store.queue_notes(rid)
        self.on_recording(rid)
        return rid

    def _get(self, sid: str) -> LiveSession:
        session = self.sessions.get(sid)
        if session is None:
            raise KeyError(sid)
        return session
