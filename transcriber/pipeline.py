"""Process one recording: normalize → (the user's server, or chunks sent to Groq) → filter → save."""
import json
import re
import logging
import shutil
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import audio
from .chunking import MAX_TAIL_SEC, TARGET_SEC, WINDOW_SEC, plan_chunks
from .channels import NAMES, label_recording
from .duplicates import describe, find_duplicates
from .merge import NotSameSession, merge_words, to_segments
from .config import LANGUAGES, MIXED
from .postprocess import filter_hallucinations, offset, to_paragraphs
from .prompting import build_prompt
from . import privacy
from .providers.base import PermanentError, Segment, Transcriber, TransientError
from .providers.remote import RemoteServer, segments_from
from .retry import call_with_retries, resume_message, wait_interruptibly
from .store import Recording, Store

log = logging.getLogger(__name__)

AUDIO_FILE = "audio.flac"
WORK_DIR = "work"
UPLOAD_STEM = "upload"
MAX_UPLOAD_BYTES = 24 * 1024 * 1024  # Groq free tier accepts 25 MB
MIN_SPLIT_SEC = 1.0
REMOTE_JOB_FILE = "remote_job.json"
POLL_SEC = 3.0
LOST_AFTER_SEC = 120.0  # how long the server may be unreachable mid-job before Groq takes over
SERVER_OFF = "Your server isn't available, so Groq is transcribing this one."


class _Abandoned(Exception):
    """The recording was deleted while it was being processed."""


class Pipeline:
    def __init__(
        self,
        store: Store,
        transcriber: Transcriber | None,
        recordings_dir: Path,
        *,
        remote: RemoteServer | None = None,
        merge_judge=None,
        mixed_transcriber=None,
        romanizer=None,
        local_transcriber=None,
        poll_sec: float = POLL_SEC,
        lost_after_sec: float = LOST_AFTER_SEC,
        target_sec: float = TARGET_SEC,
        window_sec: float = WINDOW_SEC,
        max_tail_sec: float = MAX_TAIL_SEC,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.store = store
        self.transcriber = transcriber
        self.recordings_dir = recordings_dir
        self.target_sec, self.window_sec, self.max_tail_sec = target_sec, window_sec, max_tail_sec
        self.max_upload_bytes = max_upload_bytes
        self.remote = remote
        self.merge_judge = merge_judge
        self.mixed_transcriber = mixed_transcriber  # Gemini, for "English + Urdu (mixed)" recordings
        self.romanizer = romanizer
        self.local_transcriber = local_transcriber  # runs on this laptop: the only engine allowed for Local-only recordings
        self._engine = transcriber  # the engine for the chunks being transcribed right now
        self.poll_sec, self.lost_after_sec = poll_sec, lost_after_sec
        self._sleep, self._clock = sleep, clock

    def process(self, recording_id: str) -> None:
        rec = self.store.get_recording(recording_id)
        if rec is None:
            return
        rec_dir = self.recordings_dir / recording_id
        try:
            sources = self.store.get_merge_sources(recording_id)
            with privacy.scope("transcribe", [recording_id, *(sources or [])]):
                if sources:
                    self._merge(rec, rec_dir, sources)
                else:
                    self._process(rec, rec_dir)
        except _Abandoned:
            pass
        except (PermanentError, TransientError) as exc:
            self._fail(recording_id, str(exc))
        except Exception as exc:
            privacy.log_failure(log, "Unexpected error while processing %s", recording_id)
            self._fail(recording_id, f"Unexpected error ({type(exc).__name__}); details are in the app log")
        if self.store.get_recording(recording_id) is None:
            shutil.rmtree(rec_dir, ignore_errors=True)

    def _process(self, rec: Recording, rec_dir: Path) -> None:
        rid = rec.id
        audio_path = rec_dir / AUDIO_FILE
        work = rec_dir / WORK_DIR
        self._update(rid, status="converting", status_detail=None)
        if not audio_path.exists():
            upload = find_upload(rec_dir)
            if upload is None:
                raise PermanentError("The uploaded file is missing")
            if rec.recorded_at is None:
                stamp = audio.recorded_at(upload)  # lost in the conversion, so read it first
                if stamp:
                    self._update(rid, recorded_at=stamp, title=dated_title(rec.title, rec.original_filename, stamp))
            audio.normalize(upload, audio_path)
            upload.unlink()
        duration = audio.probe_duration(audio_path)
        if duration <= 0:
            raise PermanentError("This recording contains no audio")
        self._update(rid, duration_sec=duration)
        work.mkdir(parents=True, exist_ok=True)
        vocabulary = self.store.get_settings()["vocabulary"]
        language = LANGUAGES[rec.language]

        mixed = rec.language == MIXED
        note = None
        local_only = self.store.effective_privacy([rid]).get(rid, ("standard", True))[0] == "local_only"
        if self._wants_server(rec) and not mixed and not local_only:  # the user's server is Whisper: not for mixed English/Urdu
            health = self.remote.health()
            if health:
                segments = self._server_segments(rec, audio_path, work, language, build_prompt(vocabulary, ""),
                                                 speakers=rec.want_speakers and bool(health.get("speakers")))
                if segments is not None:
                    self._save(rid, segments, self.remote.name, work)
                    return
            note = SERVER_OFF
        engine = self.mixed_transcriber if mixed and self.mixed_transcriber is not None else self.transcriber
        if local_only:
            engine = self.local_transcriber
            if engine is None:
                raise PermanentError("This recording is Local only, so it can't go to a cloud service. To transcribe it here, "
                                     "set LOCAL_TRANSCRIBER in .env (see docs/offline.md), or change its privacy class.")
            note = "Transcribing on this laptop (Local only): this can be slow"
        if engine is None:
            raise PermanentError("Your server isn't available and there is no Groq key to fall back on")
        self._engine = engine
        try:
            plan = self._load_or_make_plan(audio_path, duration, work)
            self._update(rid, status="transcribing", status_detail=note, progress_done=0, progress_total=len(plan))

            segments: list[Segment] = []
            previous_text = ""
            for index, (start, end) in enumerate(plan):
                self._ensure_exists(rid)
                prompt = build_prompt(vocabulary, previous_text)
                raw = self._chunk_segments(rid, index, start, end, audio_path, work, language, prompt)
                kept = filter_hallucinations(raw)
                previous_text = " ".join(s.text for s in kept)
                segments.extend(offset(kept, start))
                self._update(rid, progress_done=index + 1)
        finally:
            self._engine = self.transcriber
        if mixed and self.romanizer is not None:
            self._update(rid, status_detail="Writing Urdu in Roman Urdu…")
            segments = self.romanizer(segments)
        self._save(rid, segments, engine.name, work)

    def _save(self, rid: str, segments: list[Segment], model: str, work: Path) -> None:
        self._ensure_exists(rid)
        labelled = label_recording(self.recordings_dir / rid, segments)  # a meeting: You and Others, from its two tracks
        if labelled is not None:
            segments = labelled
            self.store.set_speaker_names(rid, NAMES)
        text = "\n\n".join(p.text for p in to_paragraphs(segments))
        self.store.save_transcript(rid, text, segments, model)
        self.store.notify("transcribed", rid)
        twin = find_duplicates(self.store.list_recordings()).get(rid)
        if twin:
            self.store.notify("duplicate", rid, describe(twin))
        self.store.queue_notes(rid)
        shutil.rmtree(work, ignore_errors=True)

    # --- merge devices: one best-of transcript from several recordings of the same session

    def _merge(self, rec: Recording, rec_dir: Path, source_ids: list[str]) -> None:
        rid = rec.id
        self._update(rid, status="transcribing", status_detail="Merging recordings…", progress_done=0,
                     progress_total=0)
        sources = []
        for sid in source_ids:
            source = self.store.get_recording(sid)
            transcript = self.store.get_transcript(sid)
            if source is None or transcript is None:
                raise PermanentError("One of the recordings to merge was deleted or re-transcribed. Merge them again.")
            sources.append((source, transcript))
        durations = [s.duration_sec or (s_t.segments[-1].end if s_t.segments else 0.0) for s, s_t in sources]
        try:
            merged, shares = merge_words([t.segments for _, t in sources], self.merge_judge, durations)
        except NotSameSession as exc:
            raise PermanentError(str(exc)) from exc
        ref = max(range(len(sources)), key=lambda i: durations[i])
        rec_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.recordings_dir / sources[ref][0].id / AUDIO_FILE, rec_dir / AUDIO_FILE)
        self._ensure_exists(rid)
        self._update(rid, duration_sec=durations[ref])
        self.store.set_merged_from(rid, [{"id": s.id, "title": s.title, "share": round(share, 3)}
                                         for (s, _), share in zip(sources, shares)])
        self._save(rid, to_segments(merged), f"merged: {sources[ref][1].model}", rec_dir / WORK_DIR)

    # --- the user's own server

    def _wants_server(self, rec: Recording) -> bool:
        return self.remote is not None and (rec.want_speakers or self.store.get_settings()["prefer_server"] == "1")

    def _server_segments(self, rec: Recording, audio_path: Path, work: Path, language: str | None, prompt: str,
                         *, speakers: bool) -> list[Segment] | None:
        """The whole recording transcribed on the user's server, or None when Groq should take over."""
        rid = rec.id
        job_path = work / REMOTE_JOB_FILE
        job_id = json.loads(job_path.read_text(encoding="utf-8"))["id"] if job_path.exists() else None
        if job_id is None:
            self._update(rid, status="transcribing", status_detail="Uploading to your server…",
                         progress_done=0, progress_total=0)
            try:
                job_id = self.remote.submit(audio_path, language=language, prompt=prompt, speakers=speakers)
            except (TransientError, PermanentError) as exc:
                log.warning("Server upload failed for %s: %s", rid, exc)
                return None
            write_atomic(job_path, json.dumps({"id": job_id}))
        lost_since: float | None = None
        shown = None
        while True:
            self._ensure_exists(rid)
            try:
                job = self.remote.poll(job_id)
                lost_since = None
            except TransientError as exc:
                lost_since = self._clock() if lost_since is None else lost_since
                if self._clock() - lost_since >= self.lost_after_sec:
                    log.warning("Lost the server while processing %s: %s", rid, exc)
                    job_path.unlink(missing_ok=True)
                    return None
                self._wait(rid, self.poll_sec)
                continue
            except PermanentError as exc:
                log.warning("Server refused %s: %s", rid, exc)
                job_path.unlink(missing_ok=True)
                return None
            status = job.get("status")
            if status == "done":
                try:
                    self.remote.delete(job_id)
                except (TransientError, PermanentError):
                    pass  # the server drops finished jobs after a day anyway
                job_path.unlink(missing_ok=True)
                return filter_hallucinations(segments_from(job))
            if status == "failed":
                log.warning("Server job failed for %s: %s", rid, job.get("error"))
                job_path.unlink(missing_ok=True)
                return None
            detail = ("Waiting on your server" if status == "queued"
                      else f"On your server: {job.get('stage') or 'working'} {round(100 * (job.get('progress') or 0))}%")
            if detail != shown:
                self._update(rid, status="transcribing", status_detail=detail)
                shown = detail
            self._wait(rid, self.poll_sec)

    def _load_or_make_plan(self, audio_path: Path, duration: float, work: Path) -> list[tuple[float, float]]:
        plan_path = work / "plan.json"
        if plan_path.exists():
            return [tuple(chunk) for chunk in json.loads(plan_path.read_text(encoding="utf-8"))]
        silences = audio.detect_silences(audio_path, duration)
        plan = plan_chunks(duration, silences, target=self.target_sec, window=self.window_sec,
                           max_tail=self.max_tail_sec)
        write_atomic(plan_path, json.dumps(plan))
        return plan

    def _chunk_segments(self, rid: str, index: int, start: float, end: float, audio_path: Path,
                        work: Path, language: str | None, prompt: str) -> list[Segment]:
        """Raw (unfiltered) segments for one chunk, from the cache when available."""
        result_path = work / f"chunk_{index:03d}.json"
        if result_path.exists():
            return [Segment.from_dict(d) for d in json.loads(result_path.read_text(encoding="utf-8"))]
        chunk_path = work / f"chunk_{index:03d}.flac"
        if not chunk_path.exists():
            audio.cut(audio_path, start, end, chunk_path)
        segments = self._transcribe_file(rid, chunk_path, end - start, language, prompt)
        write_atomic(result_path, json.dumps([s.to_dict() for s in segments]))
        chunk_path.unlink(missing_ok=True)
        return segments

    def _transcribe_file(self, rid: str, path: Path, length: float, language: str | None,
                         prompt: str) -> list[Segment]:
        """Transcribe one file, halving it first if it is over the upload limit."""
        if path.stat().st_size <= self.max_upload_bytes:
            segments = self._call_with_retries(rid, path, language, prompt)
            self.store.record_usage(length, self._engine.name.split("/")[0])
            return segments
        if length < MIN_SPLIT_SEC:
            raise PermanentError("Audio chunk is too large to upload")
        half = length / 2
        first = path.with_name(f"{path.stem}a.flac")
        second = path.with_name(f"{path.stem}b.flac")
        audio.cut(path, 0.0, half, first)
        audio.cut(path, half, length, second)
        segments = self._transcribe_file(rid, first, half, language, prompt)
        segments += offset(self._transcribe_file(rid, second, length - half, language, prompt), half)
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)
        return segments

    def _call_with_retries(self, rid: str, path: Path, language: str | None, prompt: str) -> list[Segment]:
        return call_with_retries(
            lambda: self._engine.transcribe(path, language=language, prompt=prompt),
            wait=lambda seconds: self._wait(rid, seconds),
            on_rate_limited=lambda seconds: self._update(rid, status="waiting",
                                                         status_detail=resume_message(seconds)),
            on_resumed=lambda: self._update(rid, status="transcribing", status_detail=None),
        )

    def _wait(self, rid: str, seconds: float) -> None:
        wait_interruptibly(seconds, sleep=self._sleep, clock=self._clock, check=lambda: self._ensure_exists(rid))

    def _ensure_exists(self, rid: str) -> None:
        if self.store.get_recording(rid) is None:
            raise _Abandoned()

    def _update(self, rid: str, **fields) -> None:
        self.store.update_recording(rid, **fields)

    def _fail(self, rid: str, message: str) -> None:
        self.store.update_recording(rid, status="failed", status_detail=message)
        self.store.notify("failed", rid, message)


def find_upload(rec_dir: Path) -> Path | None:
    for path in rec_dir.glob(f"{UPLOAD_STEM}.*"):
        if not path.name.endswith(".tmp"):
            return path
    return None


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def dated_title(title: str, original_filename: str, recorded_at: str) -> str:
    """Swap the upload day in an automatic title ("New Recording 28 — 2026-10-10") for the day it was recorded.
    Titles the user typed or edited are returned unchanged."""
    if not re.fullmatch(re.escape(Path(original_filename).stem) + r" — \d{4}-\d{2}-\d{2}", title):
        return title
    day = datetime.fromisoformat(recorded_at).astimezone().strftime("%Y-%m-%d")
    return f"{Path(original_filename).stem} — {day}"
