"""One recording's AI notes job: write with one model, check with another, save."""
import logging
import time
from collections.abc import Callable

from .. import privacy
from ..llm.base import LLMClient
from ..postprocess import named_paragraphs
from ..providers.base import PermanentError, TransientError
from ..retry import call_with_retries, resume_message, wait_interruptibly
from ..recap.pipeline import wants_recap
from ..store import Recording, Store, Transcript
from .checker import check_notes
from .coach import ME, PRACTICE_ORIGINAL, THEM, attribute_speakers, conversation_stats, write_coaching
from .transcript import render
from .writer import write_notes

log = logging.getLogger(__name__)


class _Abandoned(Exception):
    """The recording (or its notes job) was deleted while the job was running."""


class NotesPipeline:
    def topic_names(self) -> list[str]:
        """The shared topic list for the writer's prompt: only topics from recordings it may see."""
        if self.gateway is None:
            return self.store.list_topic_names()
        return self.gateway.visible_topic_names(self.writer.name)

    def __init__(self, store: Store, writer: LLMClient, checker: LLMClient | None = None, *, gateway=None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.store = store
        self.writer = writer
        self.checker = checker
        self.gateway = gateway
        self._sleep, self._clock = sleep, clock

    def process(self, recording_id: str) -> None:
        rec = self.store.get_recording(recording_id)
        transcript = self.store.get_transcript(recording_id)
        if rec is None or transcript is None:
            self.store.delete_notes(recording_id)
            return
        try:
            with privacy.scope("notes", [recording_id]):
                self._process(rec, transcript)
        except _Abandoned:
            pass
        except (PermanentError, TransientError) as exc:
            self.store.update_notes_status(recording_id, "failed", str(exc))
            self.store.notify("notes_failed", recording_id, str(exc))
        except Exception as exc:
            privacy.log_failure(log, "Unexpected error while writing notes for %s", recording_id)
            self.store.update_notes_status(recording_id, "failed", f"Unexpected error ({type(exc).__name__}); details are in the app log")

    def _process(self, rec: Recording, transcript: Transcript) -> None:
        rid = rec.id
        paragraphs = named_paragraphs(transcript.segments, self.store.get_speaker_names(rid))
        long_form = (rec.duration_sec or 0) >= 3600
        instructions = "\n".join(text for text in self.store.notes_instructions(rid) if text)
        part_starts = self.store.get_part_starts(rid)
        self.store.update_notes_status(rid, "writing")
        if self.store.notes_style(rid) == "conversation":
            paragraphs, notes = self._coach(rec, transcript, long_form, instructions)
        else:
            notes = write_notes(self.writer, title=rec.title, paragraphs=paragraphs, long_form=long_form,
                                existing_topics=self.topic_names(), call=self._caller(rid, "writing"),
                                instructions=instructions, part_starts=part_starts,
                                style=self.store.notes_style(rid))
        check_detail = None
        if self.checker is None:
            check_detail = "No checker configured"
        else:
            self._ensure_exists(rid)
            self.store.update_notes_status(rid, "checking")
            try:
                notes = check_notes(self.checker, notes=notes,
                                    transcript_text=render(paragraphs, long_form, part_starts),
                                    long_form=long_form, call=self._caller(rid, "checking"),
                                    instructions=instructions, part_count=len(part_starts or []))
            except (PermanentError, TransientError) as exc:
                check_detail = f"Not checked: {exc}"
            except _Abandoned:
                raise
            except Exception as exc:
                privacy.log_failure(log, "Unexpected error while checking notes for %s", rid)
                check_detail = f"Not checked: unexpected error ({type(exc).__name__})"
        self._ensure_exists(rid)
        self.store.save_notes(rid, notes, writer=self.writer.name,
                              checker=self.checker.name if self.checker else None, check_detail=check_detail)
        self.store.suggest_topics(rid, notes.topics)
        self.store.notify("notes_ready", rid)
        if wants_recap(self.store, rec):
            self.store.queue_recap(rid)

    def _coach(self, rec: Recording, transcript: Transcript, long_form: bool, instructions: str):
        """Coffee chats: work out who said what (from the words; no voiceprints), then coach."""
        rid = rec.id
        segments = transcript.segments
        names = self.store.get_speaker_names(rid)
        if not all(s.speaker in (ME, THEM) for s in segments):
            segments, their_name = attribute_speakers(self.writer, segments, call=self._caller(rid, "writing"),
                                                       title=rec.title)
            self._ensure_exists(rid)
            self.store.save_transcript(rid, transcript.text, segments, transcript.model)
            names = {ME: "You", THEM: names.get(THEM) or their_name or "Them"}
            self.store.set_speaker_names(rid, names)
        paragraphs = named_paragraphs(segments, names)
        their = names.get(THEM)
        notes = write_coaching(self.writer, title=rec.title, paragraphs=paragraphs, long_form=long_form,
                               existing_topics=self.topic_names(), call=self._caller(rid, "writing"),
                               stats=conversation_stats(segments), their_name=None if their == "Them" else their,
                               instructions=instructions)
        practice = rec.original_filename == PRACTICE_ORIGINAL  # an AI partner, not someone to remember
        if rec.person_id is None and notes.person and not practice:  # file the chat under the person it was with
            self.store.update_recording(rid, person_id=self.store.get_or_create_person(notes.person))
        return paragraphs, notes

    def _caller(self, rid: str, status: str) -> Callable[[Callable[[], dict]], dict]:
        def call(fn: Callable[[], dict]) -> dict:
            return call_with_retries(
                fn,
                wait=lambda seconds: wait_interruptibly(seconds, sleep=self._sleep, clock=self._clock,
                                                        check=lambda: self._ensure_exists(rid)),
                on_rate_limited=lambda seconds: self.store.update_notes_status(rid, status, resume_message(seconds)),
                on_resumed=lambda: self.store.update_notes_status(rid, status),
            )
        return call

    def _ensure_exists(self, rid: str) -> None:
        if self.store.get_recording(rid) is None or self.store.get_notes(rid) is None:
            raise _Abandoned()
