"""Your own transcription server: Whisper large-v3 (faster-whisper) plus speaker labels (pyannote community-1).

Run on the machine that does the work:   python transcribe_server.py      (settings in server/.env)
The Meeting Transcriber app on your laptop sends recordings here when "Prefer my server" is on, or when you
ask for speaker labels. See README.md in this folder.
"""
import logging
import os
import queue
import secrets
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response

try:
    from .assign import assign_speakers
except ImportError:  # run as a script from this folder
    from assign import assign_speakers

log = logging.getLogger("transcribe_server")

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
KEEP_FINISHED_SEC = 24 * 3600
SAMPLE_RATE = 16000


class Engine(Protocol):
    model: str
    device: str
    speakers_available: bool

    def transcribe(self, path: Path, *, language: str | None, prompt: str, speakers: bool,
                   progress) -> list[dict]:
        """Segments [{start, end, text, speaker, avg_logprob, no_speech_prob, compression_ratio}].
        Calls progress(stage, fraction) as it goes."""
        ...


class Job:
    def __init__(self, job_id: str, path: Path, language: str | None, prompt: str, speakers: bool):
        self.id, self.path, self.language, self.prompt, self.speakers = job_id, path, language, prompt, speakers
        self.status, self.stage, self.progress = "queued", "queued", 0.0
        self.error: str | None = None
        self.segments: list[dict] | None = None
        self.finished_at: float | None = None

    def payload(self) -> dict:
        return {"id": self.id, "status": self.status, "stage": self.stage, "progress": round(self.progress, 3),
                "error": self.error, "segments": self.segments}


def create_server(token: str, engine: Engine, data_dir: Path, *, start_worker: bool = True) -> FastAPI:
    if not token:
        raise SystemExit("Set TRANSCRIBE_SERVER_TOKEN in server/.env first (any long random text).")
    jobs: dict[str, Job] = {}
    pending: queue.Queue[str] = queue.Queue()
    lock = threading.Lock()
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    def check_token(authorization: Annotated[str, Header()] = "") -> None:
        if not secrets.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Wrong or missing token")

    def run(job: Job) -> None:
        def progress(stage: str, fraction: float) -> None:
            job.stage, job.progress = stage, max(0.0, min(1.0, fraction))

        job.status = "running"
        try:
            job.segments = engine.transcribe(job.path, language=job.language, prompt=job.prompt,
                                             speakers=job.speakers and engine.speakers_available, progress=progress)
            job.status, job.progress, job.stage = "done", 1.0, "done"
        except Exception as exc:  # report every failure to the app, which falls back to Groq
            log.exception("Job %s failed", job.id)
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = time.time()
            shutil.rmtree(job.path.parent, ignore_errors=True)

    def work() -> None:
        while True:
            job_id = pending.get()
            with lock:
                job = jobs.get(job_id)
            if job is not None:
                run(job)

    def forget_old_jobs() -> None:
        cutoff = time.time() - KEEP_FINISHED_SEC
        with lock:
            for job_id in [j.id for j in jobs.values() if j.finished_at and j.finished_at < cutoff]:
                del jobs[job_id]

    app = FastAPI(title="Transcription server", dependencies=[Depends(check_token)])
    app.state.run_next = lambda: run(jobs[pending.get_nowait()])  # for tests without the worker thread
    if start_worker:
        threading.Thread(target=work, name="jobs", daemon=True).start()

    @app.get("/health")
    def health():
        with lock:
            busy = any(j.status in ("queued", "running") for j in jobs.values())
        return {"ok": True, "model": engine.model, "device": engine.device,
                "speakers": engine.speakers_available, "busy": busy}

    @app.post("/jobs")
    def submit(file: Annotated[UploadFile, File()], language: Annotated[str, Form()] = "",
               prompt: Annotated[str, Form()] = "", speakers: Annotated[str, Form()] = "0"):
        forget_old_jobs()
        job_id = uuid.uuid4().hex
        job_dir = jobs_dir / job_id
        job_dir.mkdir()
        path = job_dir / ("audio" + Path(file.filename or "audio.flac").suffix[:8])
        with open(path, "wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        job = Job(job_id, path, language or None, prompt, speakers == "1")
        with lock:
            jobs[job_id] = job
        pending.put(job_id)
        return {"id": job_id}

    @app.get("/jobs/{job_id}")
    def status(job_id: str):
        with lock:
            job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job (the server may have restarted)")
        return job.payload()

    @app.delete("/jobs/{job_id}", status_code=204)
    def delete(job_id: str):
        with lock:
            job = jobs.pop(job_id, None)
        if job is not None and job.status in ("done", "failed"):
            shutil.rmtree(job.path.parent, ignore_errors=True)
        return Response(status_code=204)

    return app


class WhisperEngine:
    """faster-whisper for the words; pyannote for who said them. Models load once, at startup."""

    def __init__(self, model_name: str = "large-v3", hf_token: str | None = None):
        import ctranslate2
        from faster_whisper import WhisperModel

        cuda = ctranslate2.get_cuda_device_count() > 0
        self.model, self.device = model_name, "cuda" if cuda else "cpu"
        log.info("Loading Whisper %s on %s…", model_name, self.device)
        self._whisper = WhisperModel(model_name, device=self.device, compute_type="float16" if cuda else "int8")
        self._diarizer = None
        if hf_token:
            import torch
            from pyannote.audio import Pipeline

            log.info("Loading %s…", DIARIZATION_MODEL)
            self._diarizer = Pipeline.from_pretrained(DIARIZATION_MODEL, token=hf_token)
            if cuda:
                self._diarizer.to(torch.device("cuda"))
        self.speakers_available = self._diarizer is not None

    def transcribe(self, path: Path, *, language: str | None, prompt: str, speakers: bool, progress) -> list[dict]:
        from faster_whisper import decode_audio

        progress("loading audio", 0.0)
        audio = decode_audio(str(path), sampling_rate=SAMPLE_RATE)
        duration = max(len(audio) / SAMPLE_RATE, 1.0)
        pieces, _info = self._whisper.transcribe(
            audio, language=language, initial_prompt=prompt or None, word_timestamps=speakers,
            vad_filter=True, beam_size=5, condition_on_previous_text=False)
        segments, words = [], []
        share = 0.7 if speakers else 1.0
        for piece in pieces:
            segments.append({"start": piece.start, "end": piece.end, "text": piece.text.strip(), "speaker": None,
                             "avg_logprob": piece.avg_logprob, "no_speech_prob": piece.no_speech_prob,
                             "compression_ratio": piece.compression_ratio})
            words += [{"start": w.start, "end": w.end, "text": w.word} for w in piece.words or []]
            progress("transcribing", share * piece.end / duration)
        if not speakers or not words:
            return segments
        progress("speakers", 0.75)
        turns = self._turns(audio)
        progress("speakers", 0.98)
        return [{**s, "avg_logprob": None, "no_speech_prob": None, "compression_ratio": None}
                for s in assign_speakers(words, turns)]

    def _turns(self, audio) -> list[tuple[float, float, str]]:
        import torch

        output = self._diarizer({"waveform": torch.from_numpy(audio)[None, :], "sample_rate": SAMPLE_RATE})
        annotation = getattr(output, "exclusive_speaker_diarization", None) or getattr(
            output, "speaker_diarization", output)
        return [(turn.start, turn.end, str(label)) for turn, _, label in annotation.itertracks(yield_label=True)]


def main() -> None:
    import uvicorn
    from dotenv import load_dotenv

    here = Path(__file__).resolve().parent
    load_dotenv(here / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("TRANSCRIBE_SERVER_TOKEN", "").strip()
    hf_token = os.environ.get("HF_TOKEN", "").strip() or None
    if not hf_token:
        log.warning("No HF_TOKEN in .env: transcribing works, speaker labels are off.")
    engine = WhisperEngine(os.environ.get("WHISPER_MODEL", "large-v3"), hf_token)
    app = create_server(token, engine, Path(os.environ.get("DATA_DIR") or here / "data"))
    port = int(os.environ.get("PORT") or 8765)
    log.info("Ready on port %s (speakers %s)", port, "on" if engine.speakers_available else "off")
    uvicorn.run(app, host=os.environ.get("HOST") or "0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
