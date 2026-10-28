"""One recording's recap job: write the script from the notes, voice it, save recap.mp3."""
import logging
import time
from collections.abc import Callable
from pathlib import Path

from .. import privacy
from ..llm.base import LLMClient
from ..providers.base import PermanentError, TransientError
from ..retry import call_with_retries, resume_message, wait_interruptibly
from ..store import Recording, Store
from .script import spoken_paragraphs, spoken_when, write_script

log = logging.getLogger(__name__)
RECAP_FILE = "recap.mp3"
MIN_DURATION_SEC = 180


def wants_recap(store: Store, rec: Recording) -> bool:
    return store.get_settings()["recaps_on"] == "1" and (rec.duration_sec or 0) >= MIN_DURATION_SEC


class _Abandoned(Exception):
    pass


class RecapPipeline:
    def __init__(self, store: Store, writer: LLMClient, voice, recordings_dir: Path, *,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.store, self.writer, self.voice, self.recordings_dir = store, writer, voice, recordings_dir
        self._sleep, self._clock = sleep, clock

    def process(self, rid: str) -> None:
        try:
            with privacy.scope("recap", [rid]):
                self._process(rid)
        except _Abandoned:
            pass
        except (PermanentError, TransientError) as exc:
            self.store.update_recap(rid, status="failed", status_detail=str(exc))
        except Exception as exc:
            privacy.log_failure(log, "Unexpected error while making the recap for %s", rid)
            self.store.update_recap(rid, status="failed", status_detail=f"Unexpected error ({type(exc).__name__}); details are in the app log")

    def _process(self, rid: str) -> None:
        rec = self.store.get_recording(rid)
        record = self.store.get_notes(rid)
        if rec is None:
            raise _Abandoned()
        if record is None or record.status != "ready" or record.notes is None or not record.notes.items():
            raise PermanentError("Needs notes first")
        settings = self.store.get_settings()
        voice, speed = settings["recap_voice"], float(settings["recap_speed"])
        self.store.update_recap(rid, status="writing", status_detail=None)
        script = write_script(self.writer, title=rec.title, subject=rec.subject,
                              when=spoken_when(rec.recorded_at or rec.created_at), notes=record.notes,
                              instructions="\n".join(t for t in self.store.notes_instructions(rid) if t),
                              call=self._caller(rid))
        self._ensure_exists(rid)
        self.store.update_recap(rid, status="speaking", status_detail=None, script=script, voice=voice, speed=speed)
        seconds = self.voice.speak(spoken_paragraphs(script), voice=voice, speed=speed,
                                   dst=self.recordings_dir / rid / RECAP_FILE,
                                   on_status=lambda detail: self.store.update_recap(rid, status_detail=detail))
        self._ensure_exists(rid)
        self.store.update_recap(rid, status="ready", status_detail=None, duration_sec=round(seconds, 1))
        self.store.notify("recap_ready", rid)

    def _caller(self, rid: str):
        def call(fn):
            return call_with_retries(
                fn,
                wait=lambda seconds: wait_interruptibly(seconds, sleep=self._sleep, clock=self._clock,
                                                        check=lambda: self._ensure_exists(rid)),
                on_rate_limited=lambda seconds: self.store.update_recap(rid, status_detail=resume_message(seconds)),
                on_resumed=lambda: self.store.update_recap(rid, status_detail=None))
        return call

    def _ensure_exists(self, rid: str) -> None:
        if self.store.get_recording(rid) is None or self.store.get_recap(rid) is None:
            raise _Abandoned()
