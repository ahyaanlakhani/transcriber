"""Background thread: transcription jobs first, then AI notes, recaps, and last the second brain's index, one at a time."""
import logging
import threading

from . import privacy

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, store, pipeline=None, notes_pipeline=None, recap_pipeline=None, poll_interval: float = 2.0,
                 brain=None):
        self.store = store
        self.pipeline = pipeline
        self.notes_pipeline = notes_pipeline
        self.recap_pipeline = recap_pipeline
        self.brain = brain
        self.poll_interval = poll_interval
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="transcriber-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def wake(self) -> None:
        self._wake.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_once(self) -> bool:
        """Run one queued job (transcription, then notes, then recaps, then the brain). False if there was nothing to do."""
        if self.pipeline is not None and (rec := self.store.next_queued()) is not None:
            self.pipeline.process(rec.id)
            return True
        if self.notes_pipeline is not None and (rid := self.store.next_notes_queued()) is not None:
            self.notes_pipeline.process(rid)
            return True
        if self.recap_pipeline is not None and (rid := self.store.next_recap_queued()) is not None:
            self.recap_pipeline.process(rid)
            return True
        if self.brain is not None:  # concepts and Ask stay up to date as notes arrive or change
            return self.brain.step()
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                privacy.log_failure(log, "Worker loop error")
                worked = False
            if not worked:
                self._wake.wait(self.poll_interval)
                self._wake.clear()
