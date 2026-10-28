"""FastAPI app: JSON API plus the static UI."""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .brain import Brain
from . import privacy
from .brain.ask import ask_brain, write_definition
from .briefing import briefing as build_briefing, follow_ups as list_follow_ups
from . import learn as learning
from .backup import backup_library
from .integrations import anki_file, calendar_file
from .projects import project_detail
from .privacy import Gateway
from .brain.graph import graph as brain_graph
from .brain.vault import export_vault
from .calendar import AUDIO_EXTENSIONS, build_calendar, scan_folders
from .desktop import toast
from .pip import (BIG_TOOLS, SMALL_TOOLS, UNDO_TOOLS, Actions, ClassReminders, Planner, PipError, ReminderLoop,
                  memory_for_pip, parse_request, suggest_memory)
from .duplicates import file_hash, find_duplicates
from .naming import is_standard_name, suggest_bulk_names, suggest_class_names, suggest_names
from .sessions import class_sessions, unique_sessions
from .usage import bucket
from .combine import combine_recordings, is_combined
from .config import LANGUAGE_NAMES, LANGUAGES, Config, load_config, register_languages
from .export import to_md, to_txt
from .live import LiveManager
from .meeting import MeetingError, MeetingManager, WasapiDevices
from .merge import llm_judge
from .providers.gemini_transcribe import GeminiTranscriber
from .romanize import Romanizer
from .translate import Translator
from .llm.base import LLMClient
from .llm.factory import make_llm
from .notes.ask import ask as ask_meeting
from .notes.coach import ME, THEM, conversation_stats
from .notes.people import write_brief
from .recap.script import spoken_when
from .notes.editing import add_item, apply_proposal, delete_item, update_item
from .notes.model import STYLES, Notes
from .notes.pipeline import NotesPipeline
from .pipeline import AUDIO_FILE, UPLOAD_STEM, WORK_DIR, Pipeline
from .practice import PRACTICE_AUDIO, SCENARIOS, PracticeManager
from .postprocess import named_paragraphs, speaker_names, to_paragraphs
from .prompting import VOCAB_MAX_CHARS
from .providers.base import PermanentError, RateLimited, Transcriber, TransientError
from .retry import resume_message
from .providers.remote import RemoteServer
from .recap.highlights import HIGHLIGHTS_FILE, make_highlights, pick_clips
from .recap.pipeline import RECAP_FILE, RecapPipeline
from .recap.voice import VOICES, KokoroVoice
from .store import DuplicateName, NotesRecord, Recap, Recording, Store
from .worker import Worker

STATIC_DIR = Path(__file__).parent / "static"
FROM_CONFIG = object()  # sentinel: build the notes model from config
MAX_LIVE_CHUNK = 10 * 1024 * 1024
MAX_MERGE = 5
KEEP_DELETED_DAYS = 30
NO_SUBJECT = "No subject"  # the "none" choice in the iPhone shortcut's subject list
SERVER_CACHE_SEC = 30.0


class RecordingPatch(BaseModel):
    title: str | None = None
    subject: str | None = None  # a subject name; "" removes the subject
    starred: bool | None = None
    notes_style: str | None = None  # lecture | meeting; "" follows the subject
    person: str | None = None  # who a coffee chat was with; "" unlinks


class RetranscribeRequest(BaseModel):
    language: str | None = None
    speakers: bool | None = None


class ProjectNew(BaseModel):
    name: str
    description: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class ProjectLink(BaseModel):
    kind: str
    ref: str
    label: str = ""


class ProjectTask(BaseModel):
    text: str


class TaskDone(BaseModel):
    done: bool


class BackupRequest(BaseModel):
    include_audio: bool = False


class CardRating(BaseModel):
    rating: int


class TeachBack(BaseModel):
    concept_id: int
    explanation: str = Field(max_length=8000)


class MemoryNew(BaseModel):
    kind: str = "preference"
    text: str = Field(max_length=1000)


class PrivacyClass(BaseModel):
    classification: str


class ProviderApproval(BaseModel):
    config_id: str
    standard: bool = False
    private: bool = False  # approved for Private recordings: needs `verified`
    verified: bool = False  # you checked that this exact setup keeps nothing (zero retention) and doesn't train


class SettingsUpdate(BaseModel):
    default_privacy: str | None = None
    vocabulary: str | None = None
    default_language: str | None = None
    prefer_server: bool | None = None
    groq_hour_limit_sec: float | None = None
    groq_day_limit_sec: float | None = None
    recaps_on: bool | None = None
    recap_voice: str | None = None
    recap_speed: float | None = None
    time_zone: str | None = None
    scan_folders: str | None = None
    class_reminders: bool | None = None
    vault_folder: str | None = None


class BrainQuestion(BaseModel):
    question: str = Field(max_length=2000)
    mode: str = "retrieval"  # retrieval | synthesis | application


class ConceptPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=20000)
    visibility: str | None = None  # shown | hidden | auto


class ConceptAlias(BaseModel):
    alias: str = Field(max_length=200)


class ConceptMerge(BaseModel):
    into: int


class VaultExport(BaseModel):
    folder: str | None = None  # remembered for next time; empty keeps the current folder


class PipCommand(BaseModel):
    text: str


class PipActions(BaseModel):
    actions: list[dict]


class PipUndo(BaseModel):
    undo: list[dict]


class PipPlanRequest(BaseModel):
    kind: str
    scope: str = ""
    subject: str = ""


class PipApply(BaseModel):
    kind: str
    apply: dict = {}


class CalendarImport(BaseModel):
    path: str
    subject_id: int | None = None


class CalendarOpen(BaseModel):
    path: str
    reveal: bool = False  # True: show it in its folder instead of playing it


class LiveStart(BaseModel):
    title: str = ""
    subject: str = ""
    language: str | None = None
    privacy: str | None = None  # standard | private | local_only; None follows the default setting


class SpeakerNames(BaseModel):
    names: dict[str, str]


class MyNotes(BaseModel):
    text: str


class TopicName(BaseModel):
    name: str


class SubjectCreate(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""


class SubjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    notes_style: str | None = None  # lecture | meeting
    schedule: dict | None = None  # class timetable; {} clears it


class PracticeStart(BaseModel):
    scenario: str
    role: str = ""
    difficulty: str = "neutral"
    person_id: int | None = None


class PersonPatch(BaseModel):
    name: str | None = None
    note: str | None = None


class Instructions(BaseModel):
    text: str


class IdList(BaseModel):
    ids: list[str]


class MergeRequest(BaseModel):
    ids: list[str]
    title: str | None = None


class CombineRequest(BaseModel):
    ids: list[str]
    title: str | None = None


class ChatMessage(BaseModel):
    message: str = Field(max_length=4000)


class NoteItemBody(BaseModel):
    text: str
    owner: str | None = None
    due: str | None = None


class NewNoteItem(NoteItemBody):
    section: str


class MoveRequest(BaseModel):
    ids: list[str]
    subject: str  # a subject name; "" removes the subject


def create_app(config: Config | None = None, transcriber: Transcriber | None = None, *,
               writer: LLMClient | None | object = FROM_CONFIG, checker: LLMClient | None | object = FROM_CONFIG,
               ask_engine: LLMClient | None | object = FROM_CONFIG,
               remote: RemoteServer | None | object = FROM_CONFIG,
               voice=FROM_CONFIG, practice_llm=FROM_CONFIG, meeting_devices=None, start_worker: bool = True,
               privacy_allow_all: bool = False, meeting_clock=None, local_transcriber=FROM_CONFIG) -> FastAPI:
    config = config or load_config()
    register_languages(config.extra_languages, config.second_language)
    if transcriber is None and config.groq_api_key:
        from .providers.groq import GroqTranscriber

        transcriber = GroqTranscriber(config.groq_api_key)
    if writer is FROM_CONFIG:
        writer = make_llm(config.notes_writer, config.api_keys)
    if ask_engine is FROM_CONFIG:
        ask_engine = make_llm(config.ask_model, config.api_keys)
    if checker is FROM_CONFIG:
        checker = make_llm(config.notes_checker, config.api_keys)
    if remote is FROM_CONFIG:
        remote = RemoteServer(config.server_url, config.server_token) if config.server_url and config.server_token \
            else None
    if practice_llm is FROM_CONFIG:
        practice_llm = make_llm(config.practice_model, config.api_keys) or writer
    if voice is FROM_CONFIG:
        voice = KokoroVoice(Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "meeting-transcriber" / "kokoro")
    if local_transcriber is FROM_CONFIG:
        from .providers.local import LocalWhisperTranscriber, model_size

        local_transcriber = LocalWhisperTranscriber(model_size(config.local_transcriber)) if config.local_transcriber else None
    store = Store(config.data_dir / "app.db")
    # Everything that can send your content to a cloud service goes through the privacy gateway (privacy.py).
    # privacy_allow_all exists only so tests can use fake providers.
    gateway = Gateway(store, allow_all=privacy_allow_all)
    transcriber = gateway.guard_transcriber(transcriber)
    writer, checker = gateway.guard_llm(writer), gateway.guard_llm(checker)
    ask_engine, practice_llm = gateway.guard_llm(ask_engine), gateway.guard_llm(practice_llm)
    remote = gateway.guard_remote(remote)
    recordings_dir = config.data_dir / "recordings"
    gemini_key = config.api_keys.get("gemini")
    mixed_transcriber = gateway.guard_transcriber(GeminiTranscriber(gemini_key) if gemini_key else None)
    local_transcriber = gateway.guard_transcriber(local_transcriber)
    romanizer = Romanizer(writer, language=config.second_language) if writer else None
    pipeline = Pipeline(store, transcriber, recordings_dir, remote=remote,
                        merge_judge=llm_judge(writer) if writer else None,
                        mixed_transcriber=mixed_transcriber, romanizer=romanizer, local_transcriber=local_transcriber) \
        if (transcriber or remote or mixed_transcriber or local_transcriber) else None
    notes_pipeline = NotesPipeline(store, writer, checker, gateway=gateway) if writer else None
    # Ask tries Claude (subscription) first, then the free notes writer.
    ask_engines = [engine for engine in (ask_engine, writer) if engine is not None]
    recap_pipeline = RecapPipeline(store, writer, voice, recordings_dir) if writer and voice else None
    brain = Brain(store, writer, gateway=gateway)  # the notes writer merges concept names and writes definitions; works without it
    worker = Worker(store, pipeline, notes_pipeline, recap_pipeline, brain=brain) if (pipeline or notes_pipeline) else None

    reminders = ClassReminders(store, notify=lambda title, body: toast(title, body, f"http://127.0.0.1:{config.port}/"))
    reminder_loop = ReminderLoop(reminders)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.reset_interrupted()
        erase_old_deletions()
        live.recover()
        if worker and start_worker:
            worker.start()
        if start_worker:
            reminder_loop.start()
        yield
        reminder_loop.stop()
        meeting.stop_all()
        live.stop()
        if worker:
            worker.stop()
        if not (worker and worker.is_alive()):
            store.close()

    live = LiveManager(store, transcriber, config.data_dir / "live", recordings_dir,
                       on_recording=lambda rid: worker.wake() if worker else None, start_thread=start_worker)
    meeting = MeetingManager(live, meeting_devices or WasapiDevices(), **({"clock": meeting_clock} if meeting_clock else {}))
    practice = PracticeManager(store, transcriber, practice_llm, config.data_dir / "practice", recordings_dir)
    app = FastAPI(title="Meeting Transcriber", lifespan=lifespan)
    app.state.live = live
    app.state.meeting = meeting
    app.state.store = store
    app.state.gateway = gateway
    app.state.reminders = reminders
    app.state.worker = worker
    app.state.recordings_dir = recordings_dir
    app.state.ask_engines = ask_engines
    app.state.brain = brain

    def get_or_404(rid: str) -> Recording:
        rec = store.get_recording(rid)
        if rec is None:
            raise HTTPException(404, "Recording not found")
        return rec

    def flags_for_all() -> tuple[dict, dict]:
        """Which recordings have a twin, and which are parts of one class, among all the recordings in the app."""
        everything = store.list_recordings()
        return find_duplicates(everything), class_sessions(everything, store.get_settings()["time_zone"])

    def parts_of(rid: str, sessions: dict) -> list[dict]:
        """For a combined class: the recordings it was joined from, so they can be viewed separately."""
        info = next((s for s in sessions.values() if s["combined_id"] == rid), None)
        parts = [store.get_recording(i) for i in info["ids"]] if info else []
        return [{"id": p.id, "title": p.title, "minutes": round((p.duration_sec or 0) / 60)} for p in parts if p]

    def with_duplicates(recs: list) -> list[dict]:
        """Recordings as JSON, each with its twin (if any) and the class session it belongs to (if any)."""
        twins, sessions = flags_for_all()
        return [{**asdict(r), "duplicate": twins.get(r.id), "session": sessions.get(r.id)} for r in recs]

    def backfill_hashes() -> None:
        """Hash the audio of recordings made before exact copies could be spotted."""
        for rid in store.recordings_missing_hash():
            try:
                path = audio_path(rid)
                store.update_recording(rid, content_hash=file_hash(path) if path else "")
            except Exception:  # a bad file shouldn't stop the others
                continue

    def audio_path(rid: str) -> Path | None:
        """The recording's audio: lossless FLAC, or compressed Opus for practice sessions."""
        for name in (AUDIO_FILE, PRACTICE_AUDIO):
            if (recordings_dir / rid / name).exists():
                return recordings_dir / rid / name
        return None

    def wake() -> None:
        if worker:
            worker.wake()

    def subject_id_for(name: str | None) -> int | None:
        name = (name or "").strip()
        return store.get_or_create_subject(name) if name else None

    server_cache: dict = {"at": -1e9, "body": None}

    def server_status(refresh: bool = False) -> dict:
        if remote is None:
            return {"configured": False, "reachable": False, "speakers": False, "model": None, "device": None,
                    "error": None}
        if refresh or time.monotonic() - server_cache["at"] > SERVER_CACHE_SEC:
            health = remote.health()
            server_cache["body"] = {"configured": True, "reachable": health is not None,
                                    "speakers": bool(health and health.get("speakers")),
                                    "model": health.get("model") if health else None,
                                    "device": health.get("device") if health else None,
                                    "error": remote.last_error}
            server_cache["at"] = time.monotonic()
        return server_cache["body"]

    @app.get("/api/server")
    def server(refresh: bool = False):
        return server_status(refresh)

    @app.get("/api/usage")
    def usage():
        now = datetime.now(UTC)

        def since(delta: timedelta) -> float:
            return store.usage_seconds("groq", (now - delta).isoformat(timespec="seconds"))

        settings = store.get_settings()
        events = store.usage_events("groq", (now - timedelta(days=1)).isoformat(timespec="seconds"))

        def refill(limit_key: str, window_sec: int) -> dict:
            state = bucket(events, float(settings[limit_key]), window_sec, now)
            return {**state, "full_at": state["full_at"].isoformat(timespec="seconds") if state["full_at"] else None}

        return {"hour_sec": since(timedelta(hours=1)), "day_sec": since(timedelta(days=1)),
                "week_sec": since(timedelta(days=7)), "month_sec": since(timedelta(days=30)),
                "hour_limit_sec": float(settings["groq_hour_limit_sec"]),
                "day_limit_sec": float(settings["groq_day_limit_sec"]),
                # Groq refills steadily: what's free now and when it's full again
                "day": refill("groq_day_limit_sec", 86_400), "hour": refill("groq_hour_limit_sec", 3_600)}

    @app.get("/api/status")
    def status():
        return {
            "api_key_configured": transcriber is not None,
            "notes": {"writer": writer.name if writer else None, "checker": checker.name if checker else None},
            "ask": ask_engines[0].name if ask_engines else None,
            "mixed": mixed_transcriber is not None,  # English plus another language in one lecture needs a Gemini key
            "languages": [{"code": code, "name": name} for code, name in LANGUAGE_NAMES.items()],
            "second_language": config.second_language,
        }

    # --- recordings

    @app.post("/api/recordings", status_code=201)
    def upload(
        files: Annotated[list[UploadFile], File()],
        titles: Annotated[list[str] | None, Form()] = None,
        subject: Annotated[str, Form()] = "",
        language: Annotated[str, Form()] = "",
        speakers: Annotated[str, Form()] = "",
    ):
        titles = titles or []
        language = language or store.get_settings()["default_language"]
        check_language(language)
        subject_id = subject_id_for(subject)
        created = []
        for index, file in enumerate(files):
            rid = uuid.uuid4().hex
            rec_dir = recordings_dir / rid
            rec_dir.mkdir(parents=True)
            original = Path(file.filename or "recording").name
            with open(rec_dir / f"{UPLOAD_STEM}{safe_suffix(original)}", "wb") as out:
                shutil.copyfileobj(file.file, out, 1024 * 1024)
            title = (titles[index].strip() if index < len(titles) else "") or default_title(original)
            store.create_recording(rid, title, subject_id, language, original, want_speakers=speakers == "1")
            store.update_recording(rid, content_hash=file_hash(rec_dir / f"{UPLOAD_STEM}{safe_suffix(original)}"))
            created.append(asdict(store.get_recording(rid)))
        wake()
        return created

    @app.get("/api/recordings")
    def list_recordings(q: str = "", subject_id: int | None = None, topic_id: int | None = None,
                        starred: bool = False, deleted: bool = False, no_subject: bool = False,
                        person_id: int | None = None):
        if deleted:
            erase_old_deletions()
        return with_duplicates(store.list_recordings(q=q.strip() or None, subject_id=subject_id,
                                                     topic_id=topic_id, starred=starred, deleted=deleted,
                                                     no_subject=no_subject, person_id=person_id))

    @app.get("/api/recordings/counts")
    def recording_counts():
        return store.recording_counts()

    @app.post("/api/recordings/combine", status_code=201)
    def combine(body: CombineRequest):
        try:
            rec = combine_recordings(store, recordings_dir, body.ids, body.title)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        wake()
        return asdict(rec)

    @app.get("/api/class-name-suggestions")
    def class_name_suggestions(scope: Annotated[str, Query(pattern="^(joined|standard|all)$")] = "joined",
                               subject_id: int | None = None, topic_id: int | None = None, starred: bool = False,
                               no_subject: bool = False, person_id: int | None = None):
        """New names for the 'Rename' review. joined: the joined classes. all: every finished recording in the list
        you are looking at. standard: only those still called "New Recording 33" or the address the phone gave them."""
        tz = store.get_settings()["time_zone"]
        if scope == "joined":
            joined = [r for r in store.list_recordings() if is_combined(r.original_filename)]
            return {"classes": suggest_class_names(store, writer, joined, tz)}
        _, sessions = flags_for_all()
        recs = [r for r in store.list_recordings(subject_id=subject_id, topic_id=topic_id, starred=starred,
                                                 no_subject=no_subject, person_id=person_id)
                if r.status == "done" and not sessions.get(r.id, {}).get("combined")]  # parts of a joined class are tucked away
        if scope == "standard":
            recs = [r for r in recs if is_standard_name(r.title, r.original_filename)]
        names, problem = suggest_bulk_names(store, writer, recs, tz, sessions)
        return {"classes": names, "warning": problem}

    # --- Pip: small changes to the calendar and reminders, each with an undo; nothing is ever deleted

    def pip_actions() -> Actions:
        return Actions(store, store.get_settings()["time_zone"])

    @app.post("/api/pip/command")
    def pip_command(body: PipCommand):
        """Turn a typed request into actions, do them, and say what happened."""
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "Tell Pip what you'd like")
        nothing = {"done": [], "problems": [], "undo": [], "plans": [], "open": None}
        if writer is None:
            return {"say": "I need the notes writer (a Groq key) to understand typed requests. The quick buttons still work.",
                    **nothing}
        tz = store.get_settings()["time_zone"]
        memory, sources = memory_for_pip(store, gateway, writer.name)
        try:
            with privacy.scope("pip", sources, no_recording_content=not sources):
                plan = parse_request(writer, text, store, tz, memory=memory)
        except Exception:  # the model was unreachable or garbled: nothing has changed
            return {"say": "I couldn't reach my notes writer just now. Try again in a moment, or use a quick button.", **nothing}
        result = pip_actions().run_all([a for a in plan["actions"] if a["tool"] in SMALL_TOOLS])
        plans, problems = [], list(result["problems"])
        for action in plan["actions"]:  # the bigger changes are only planned: nothing changes until you say so
            if action["tool"] in BIG_TOOLS:
                try:
                    plans.append(Planner(store, writer, tz).plan(action))
                except PipError as exc:
                    problems.append(str(exc))
        opened = next(({"view": a["text"], "subject": a["subject"]} for a in plan["actions"] if a["tool"] == "open_view"), None)
        started = next(({"view": "meeting", "start": True, "title": a["text"], "subject": a["subject"]}
                        for a in plan["actions"] if a["tool"] == "start_meeting"), None)
        opened = started or opened
        answer = None  # a question about the lectures: answered from the brain, with its sources
        question = next((a["text"] for a in plan["actions"] if a["tool"] == "ask_brain" and a["text"].strip()), None)
        if question:
            brain.catch_up()
            answer = ask_brain(brain, ask_engines, question)
        concept = next((a["text"] for a in plan["actions"] if a["tool"] == "open_concept" and a["text"].strip()), None)
        if concept:
            brain.catch_up()
            found = brain.find(concept)
            if found is None:
                problems.append(f"I couldn't find a concept called “{concept}” in your notes yet")
            else:
                opened = {"view": "concept", "id": found, "subject": ""}
        return {"say": plan["say"], **result, "problems": problems, "plans": plans, "open": opened, "answer": answer}

    @app.post("/api/pip/act")
    def pip_act(body: PipActions):
        """The quick buttons: the same actions, already worked out."""
        return {"say": "", "plans": [], "open": None, **pip_actions().run_all(body.actions)}

    @app.post("/api/pip/plan")
    def pip_plan(body: PipPlanRequest):
        """The quick buttons for the bigger changes: what Pip would do, without doing it."""
        tool = {"rename": "rename_recordings", "combine": "combine_classes", "file": "file_recordings"}.get(body.kind)
        if tool is None:
            raise HTTPException(400, "Pip can plan: rename, combine, file")
        try:
            plan = Planner(store, writer, store.get_settings()["time_zone"]).plan(
                {"tool": tool, "text": body.scope, "subject": body.subject})
        except PipError as exc:
            return {"say": "", "done": [], "problems": [str(exc)], "undo": [], "plans": [], "open": None}
        return {"say": "", "done": [], "problems": [], "undo": [], "plans": [plan], "open": None}

    @app.post("/api/pip/apply")
    def pip_apply(body: PipApply):
        """Do a plan the student approved."""
        if body.kind == "combine":
            result = combine_every_class()
            done = [{"summary": f"Combined {len(result['combined'])} {'class' if len(result['combined']) == 1 else 'classes'}. Your originals are kept."}] if result["combined"] else []
            return {"say": "", "done": done, "problems": [f"{s['title']}: {s['reason']}" for s in result["skipped"]]
                    + ([] if done else ["Nothing could be combined"]), "undo": [], "plans": [], "open": None}
        try:
            return {"say": "", "plans": [], "open": None, **pip_actions().apply_plan(body.kind, body.apply)}
        except PipError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/pip/undo")
    def pip_undo(body: PipUndo):
        result = pip_actions().run_all(body.undo, allowed=UNDO_TOOLS)
        return {"say": "Undone." if result["done"] else "", "plans": [], "open": None, **result}

    # --- Pip's memory: what you told Pip or approved, with an expiry, all visible and forgettable

    def memory_payload() -> dict:
        found = store.list_memory()
        title_of = {r.id: r.title for r in store.list_recordings()}

        def show(item):
            return {**item, "source": title_of.get(item["source_recording_id"]) if item["source_recording_id"] else None}

        return {key: [show(i) for i in items] for key, items in found.items()}

    @app.get("/api/pip/memory")
    def pip_memory(suggest: bool = False):
        if suggest:
            suggest_memory(store)
        return memory_payload()

    @app.post("/api/pip/memory", status_code=201)
    def pip_memory_add(body: MemoryNew):
        try:
            store.add_memory(body.kind, body.text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return memory_payload()

    @app.post("/api/pip/memory/{memory_id}/approve")
    def pip_memory_approve(memory_id: int):
        if not store.approve_memory(memory_id):
            raise HTTPException(404, "That memory is gone")
        return memory_payload()

    @app.delete("/api/pip/memory/{memory_id}")
    def pip_memory_forget(memory_id: int):
        store.delete_memory(memory_id)
        return memory_payload()

    # --- Today: the day at a glance, and the follow-ups from your notes (all local, no AI model)

    @app.get("/api/briefing")
    def todays_briefing():
        return build_briefing(store, store.get_settings()["time_zone"])

    @app.get("/api/follow-ups")
    def follow_up_list(done: bool = False):
        return {"items": list_follow_ups(store, store.get_settings()["time_zone"], include_done=done)}

    @app.post("/api/follow-ups/{key}/{state}")
    def follow_up_set(key: str, state: str):
        if state not in ("done", "dismissed", "open"):
            raise HTTPException(400, "A follow-up can be marked done, dismissed or open")
        store.set_follow_up(key, None if state == "open" else state)
        return {"key": key, "state": None if state == "open" else state}

    # --- Learn: flashcards from your notes on a spaced schedule, and teach-back

    def learn_zone() -> str:
        return store.get_settings()["time_zone"]

    @app.get("/api/learn")
    def learn_summary():
        learning.sync_cards(store, learn_zone())
        return learning.summary(store, learn_zone())

    @app.get("/api/learn/next")
    def learn_next():
        card = learning.next_card(store, learn_zone())
        return {"card": card, "summary": learning.summary(store, learn_zone())}

    @app.post("/api/learn/cards/{card_id}/review")
    def learn_review(card_id: int, body: CardRating):
        try:
            result = learning.answer(store, card_id, body.rating)
        except KeyError as exc:
            raise HTTPException(404, "That card is gone") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {**result, "next": learning.next_card(store, learn_zone()), "summary": learning.summary(store, learn_zone())}

    @app.post("/api/learn/cards/{card_id}/suspend")
    def learn_suspend(card_id: int):
        if not store.set_card_suspended(card_id, True):
            raise HTTPException(404, "That card is gone")
        return {"next": learning.next_card(store, learn_zone()), "summary": learning.summary(store, learn_zone())}

    @app.post("/api/learn/teach-back")
    async def learn_teach_back(body: TeachBack):
        if writer is None:
            raise HTTPException(400, "Teach-back needs the notes writer (a Groq key)")
        found = brain.definition_material(body.concept_id)
        if found is None:
            raise HTTPException(404, "No such concept")
        name, texts, _, _ = found
        if not texts:
            raise HTTPException(400, "Your notes don't say enough about this yet")

        def check():
            with privacy.scope("teach-back", brain.concept_recordings([body.concept_id])):
                return learning.teach_back(writer, name, texts, body.explanation)

        try:
            return {"concept": name, **await run_in_threadpool(check)}
        except privacy.PrivacyBlocked as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (PermanentError, TransientError, RateLimited) as exc:
            raise HTTPException(503, f"I couldn't check that just now: {exc}") from exc

    # --- Projects: recordings, people, concepts and documents tied together, with their own tasks

    def project_or_404(project_id: int) -> dict:
        found = project_detail(store, project_id, store.get_settings()["time_zone"])
        if found is None:
            raise HTTPException(404, "Project not found")
        return found

    @app.get("/api/projects")
    def projects_list():
        return {"projects": store.list_projects()}

    @app.post("/api/projects", status_code=201)
    def projects_create(body: ProjectNew):
        try:
            return project_or_404(store.create_project(body.name, body.description))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/projects/{project_id}")
    def projects_get(project_id: int):
        return project_or_404(project_id)

    @app.patch("/api/projects/{project_id}")
    def projects_patch(project_id: int, body: ProjectPatch):
        try:
            if not store.update_project(project_id, name=body.name, description=body.description, status=body.status):
                raise HTTPException(404, "Project not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return project_or_404(project_id)

    @app.delete("/api/projects/{project_id}", status_code=204)
    def projects_delete(project_id: int):
        """Removes the project and its links and tasks. Recordings, people and concepts are never touched."""
        if not store.delete_project(project_id):
            raise HTTPException(404, "Project not found")
        return Response(status_code=204)

    @app.post("/api/projects/{project_id}/links")
    def projects_link(project_id: int, body: ProjectLink):
        project_or_404(project_id)
        try:
            store.add_project_link(project_id, body.kind, body.ref, body.label)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return project_or_404(project_id)

    @app.delete("/api/projects/{project_id}/links")
    def projects_unlink(project_id: int, kind: str, ref: str):
        project_or_404(project_id)
        store.remove_project_link(project_id, kind, ref)
        return project_or_404(project_id)

    @app.post("/api/projects/{project_id}/tasks")
    def projects_add_task(project_id: int, body: ProjectTask):
        project_or_404(project_id)
        try:
            store.add_project_task(project_id, body.text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return project_or_404(project_id)

    @app.patch("/api/project-tasks/{task_id}")
    def projects_task_done(task_id: int, body: TaskDone):
        if not store.set_project_task(task_id, body.done):
            raise HTTPException(404, "Task not found")
        return {"id": task_id, "done": body.done}

    @app.delete("/api/project-tasks/{task_id}", status_code=204)
    def projects_task_delete(task_id: int):
        store.delete_project_task(task_id)
        return Response(status_code=204)

    # --- Take it with you: plain files for a calendar app and for Anki, built here and sent nowhere

    @app.post("/api/backup")
    async def make_backup(body: BackupRequest):
        """A backup of the whole library into Documents/Transcriber backups, in a new folder each time."""
        destination = Path.home() / "Documents" / "Transcriber backups"
        try:
            return await run_in_threadpool(backup_library, store, recordings_dir, destination, body.include_audio)
        except (OSError, RuntimeError) as exc:
            raise HTTPException(500, f"The backup didn't work: {exc}") from exc

    @app.get("/api/export/classes.ics")
    def export_classes_ics():
        body = calendar_file(store, store.get_settings()["time_zone"])
        return Response(body, media_type="text/calendar",
                        headers={"Content-Disposition": 'attachment; filename="transcriber-classes.ics"'})

    @app.get("/api/export/flashcards-anki.txt")
    def export_anki():
        learning.sync_cards(store, store.get_settings()["time_zone"])
        body, count = anki_file(store, store.get_settings()["time_zone"])
        if not count:
            raise HTTPException(404, "No flashcards yet")
        return Response(body, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="transcriber-flashcards.txt"'})

    @app.get("/api/reminders")
    def list_reminders():
        return {"reminders": store.list_reminders()}

    @app.delete("/api/reminders/{reminder_id}", status_code=204)
    def delete_reminder(reminder_id: int):
        store.delete_reminder(reminder_id)
        return Response(status_code=204)

    @app.post("/api/reminders/test")
    def test_reminder():
        """Show a desktop notification now, to check they reach you."""
        return {"sent": toast("Pip", "Hello! This is how I'll remind you to record your classes.", f"http://127.0.0.1:{config.port}/")}

    @app.get("/api/class-sessions")
    def class_session_list():
        """Every class that was recorded in parts, and whether its parts are ready to be joined."""
        _, sessions = flags_for_all()
        status = {r.id: r.status for r in store.list_recordings()}
        return {"sessions": [{**info, "ready": all(status.get(i) == "done" for i in info["ids"])}
                             for info in unique_sessions(sessions)]}

    @app.post("/api/recordings/combine-classes")
    def combine_all_classes():
        """Join the parts of every class that was recorded in parts and isn't joined yet. Originals are kept."""
        return combine_every_class()

    def combine_every_class() -> dict:
        _, sessions = flags_for_all()
        joined, skipped = [], []
        for info in unique_sessions(sessions):
            if info["combined"]:
                continue
            try:
                rec = combine_recordings(store, recordings_dir, info["ids"], info["title"])
            except ValueError as exc:
                skipped.append({"title": info["title"], "reason": str(exc)})
                continue
            joined.append({"id": rec.id, "title": rec.title, "parts": info["count"]})
        if joined:
            wake()
        return {"combined": joined, "skipped": skipped}

    @app.post("/api/recordings/merge", status_code=201)
    def merge_devices(body: MergeRequest):
        ids = list(dict.fromkeys(body.ids))
        if not 2 <= len(ids) <= MAX_MERGE:
            raise HTTPException(400, f"Pick 2 to {MAX_MERGE} recordings of the same session to merge")
        sources = []
        for rid in ids:
            rec = store.get_recording(rid)
            if rec is None or rec.deleted_at:
                raise HTTPException(400, "One of those recordings no longer exists or is in Recently Deleted")
            if rec.status != "done" or store.get_transcript(rid) is None \
                    or not (recordings_dir / rid / AUDIO_FILE).exists():
                raise HTTPException(400, f"'{rec.title}' hasn't finished transcribing yet")
            sources.append(rec)
        longest = max(sources, key=lambda r: r.duration_sec or 0)
        new_id = uuid.uuid4().hex
        (recordings_dir / new_id).mkdir(parents=True)
        title = " ".join((body.title or "").split()) or f"Merged: {longest.title}"
        store.create_recording(new_id, title, longest.subject_id, longest.language, "Merged recording")
        stamps = [rec.recorded_at for rec in sources if rec.recorded_at]
        if stamps:
            store.update_recording(new_id, recorded_at=min(stamps))
        store.set_merge_sources(new_id, ids)
        wake()
        return asdict(store.get_recording(new_id))

    @app.post("/api/recordings/move")
    def move_recordings(body: MoveRequest):
        moved = store.move_recordings(body.ids, subject_id_for(body.subject))
        return {"moved": moved}

    @app.get("/api/recordings/{rid}/name-suggestions")
    def name_suggestions(rid: str):
        """Better names for a recording, and the name its file had on the phone (which never changes)."""
        rec = get_or_404(rid)
        _, sessions = flags_for_all()
        return {"original": rec.original_filename,
                "suggestions": suggest_names(store, writer, rec, store.get_settings()["time_zone"], sessions.get(rid))}

    def privacy_of(rid: str) -> dict:
        """A recording's own class, the class that applies once what it was made from is counted, and whether the app
        can trace where it came from."""
        own = store.privacy_info([rid]).get(rid)
        effective = store.effective_privacy([rid]).get(rid, ("standard", True))
        return {"class": own["class"] if own else "standard", "effective": effective[0], "traced": effective[1]}

    @app.get("/api/recordings/{rid}")
    def get_recording(rid: str):
        rec = get_or_404(rid)
        transcript = store.get_transcript(rid)
        paragraphs = to_paragraphs(transcript.segments) if transcript else []
        names = speaker_names([p.speaker for p in paragraphs], store.get_speaker_names(rid))
        twins, sessions = flags_for_all()
        return {
            **asdict(rec),
            "duplicate": twins.get(rid),
            "session": sessions.get(rid),
            "parts": parts_of(rid, sessions),
            "paragraphs": [{**asdict(p), "speaker": names.get(p.speaker), "speaker_label": p.speaker}
                           for p in paragraphs],
            "speakers": [{"label": label, "name": name} for label, name in names.items()],
            "transcribed_by": transcript.model if transcript else None,
            "privacy": privacy_of(rid),
            "projects": store.projects_of("recording", rid),
            "has_audio": audio_path(rid) is not None,
            "translation": store.get_translation(rid),
            "topics": store.recording_topics(rid),
            "my_notes": store.get_my_notes(rid) or "",
            "notes": notes_payload(store.get_notes(rid)),
            "chat": store.list_chat(rid),
            "instructions": instructions_payload(rid),
            "part_starts": store.get_part_starts(rid),
            "merged_from": store.get_merged_from(rid),
            "effective_notes_style": store.notes_style(rid),
            "recap": recap_payload(store.get_recap(rid)),
            "highlights": highlights_meta(rid),
            "conversation_stats": conversation_stats(transcript.segments)
            if transcript and store.notes_style(rid) == "conversation" else None,
        }

    @app.post("/api/recordings/{rid}/speakers/swap")
    def swap_speakers(rid: str):
        """The coach got you and the other person the wrong way round: flip them and rewrite the coaching."""
        get_or_404(rid)
        transcript = store.get_transcript(rid)
        if transcript is None:
            raise HTTPException(400, "No transcript yet")
        flip = {ME: THEM, THEM: ME}
        store.save_transcript(rid, transcript.text, [replace(s, speaker=flip.get(s.speaker, s.speaker))
                                                     for s in transcript.segments], transcript.model)
        if writer:
            store.queue_notes(rid)
            wake()
        return {"notes": notes_payload(store.get_notes(rid))}

    # --- privacy: what may leave this laptop, for which recordings, and a record of what did

    def configs_in_use() -> list[dict]:
        """The exact cloud setups this app would use right now, each with what it does."""
        found: dict[str, list[str]] = {}
        for role, client in (("Transcription", transcriber), ("Mixed-language transcription", mixed_transcriber),
                             ("Your own server", remote), ("Notes writer", writer), ("Notes checker", checker),
                             ("Ask", ask_engine), ("Practice room", practice_llm)):
            if client is not None and getattr(client, "name", None):
                found.setdefault(client.name, [])
                if role not in found[client.name]:
                    found[client.name].append(role)
        policies = store.get_policies()
        out = []
        for config_id, roles in found.items():
            facts = privacy.PROVIDERS.get(privacy.provider_of(config_id))
            policy = policies.get(config_id) or {}
            out.append({"config_id": config_id, "roles": roles, "label": facts.label if facts else config_id,
                        "known": facts is not None, "locality": facts.locality if facts else "unknown",
                        "private_possible": bool(facts and facts.private_possible),
                        "note": facts.note if facts else "The app doesn't know this provider, so it will never send to it.",
                        "approved_standard": bool(policy.get("approved_standard") or policy.get("approved_private")),
                        "approved_private": bool(policy.get("approved_private")),
                        "verified": policy.get("retention") == privacy.RETENTION_VERIFIED,
                        "verified_at": policy.get("verified_at")})
        return out

    @app.get("/api/privacy")
    def privacy_status():
        counts = {"standard": 0, "private": 0, "local_only": 0}
        untraced = []
        for rec in store.list_recordings():
            cls, traced = store.effective_privacy([rec.id]).get(rec.id, ("standard", True))
            counts[cls] += 1
            if not traced and not rec.deleted_at:
                untraced.append({"id": rec.id, "title": rec.title})
        return {"default": store.get_settings()["default_privacy"], "counts": counts, "untraced": untraced,
                "providers": configs_in_use(), "revision": store.policy_revision(),
                "classes": [{"key": key, "label": privacy.CLASS_LABELS[key]} for key in privacy.CLASSES],
                "log": store.list_transmissions(60)}

    @app.put("/api/privacy/providers")
    def privacy_approve(body: ProviderApproval):
        current = {c["config_id"]: c for c in configs_in_use()}
        info = current.get(body.config_id)
        if info is None:
            raise HTTPException(404, "That isn't a setup this app uses right now")
        if not info["known"]:
            raise HTTPException(400, "The app doesn't know how to protect this provider, so it can't be approved")
        if body.private and not (info["private_possible"] and body.verified):
            raise HTTPException(400, "Private needs a provider that can be zero-retention, and your confirmation that "
                                     "you checked it")
        store.set_policy(body.config_id, standard=body.standard or body.private, private=body.private,
                         retention=privacy.RETENTION_VERIFIED if body.private and body.verified else "unverified")
        return privacy_status()

    @app.delete("/api/privacy/providers")
    def privacy_withdraw(config_id: str):
        store.clear_policy(config_id)
        return privacy_status()

    @app.put("/api/recordings/{rid}/privacy")
    def set_recording_privacy(rid: str, body: PrivacyClass):
        get_or_404(rid)
        if body.classification not in privacy.CLASSES:
            raise HTTPException(400, "Choose Standard, Private or Local only")
        store.set_privacy(rid, body.classification)
        return privacy_of(rid)

    @app.post("/api/recordings/{rid}/privacy/confirm")
    def confirm_recording_sources(rid: str):
        """You've checked where this combined recording came from: it can use approved providers."""
        get_or_404(rid)
        store.confirm_provenance(rid)
        return privacy_of(rid)

    # --- notifications: the bell

    def notifications_payload() -> dict:
        return {"items": store.list_notifications(), "unseen": store.unseen_notifications()}

    @app.get("/api/notifications")
    def notifications():
        return notifications_payload()

    @app.post("/api/notifications/seen")
    def notifications_seen():
        store.mark_notifications_seen()
        return notifications_payload()

    @app.delete("/api/notifications", status_code=204)
    def clear_notifications():
        store.clear_notifications()
        return Response(status_code=204)

    @app.delete("/api/notifications/{note_id}", status_code=204)
    def dismiss_notification(note_id: int):
        store.delete_notification(note_id)
        return Response(status_code=204)

    # --- calendar: which classes were recorded, and phone recordings still on the laptop

    def scan_roots() -> list[Path]:
        lines = [line for line in store.get_settings()["scan_folders"].splitlines() if line.strip()]
        return [Path(line.strip()) for line in lines] or [Path.home() / "Downloads"]

    def laptop_files() -> list[dict]:
        return scan_folders(scan_roots(), config.data_dir / "laptop-files.json")

    @app.get("/api/calendar")
    async def calendar(start: str, end: str, files: bool = True):
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise HTTPException(400, "Dates must look like 2026-10-01") from exc
        found = await run_in_threadpool(laptop_files) if files else []
        tz = store.get_settings()["time_zone"]
        return {**build_calendar(store, tz, first, last, found), "folders": [str(p) for p in scan_roots()]}

    def laptop_file_or_400(raw: str) -> Path:
        path = Path(raw)
        roots = [root.resolve() for root in scan_roots()]
        if (not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS
                or not any(path.resolve().is_relative_to(root) for root in roots)):
            raise HTTPException(400, "That file isn't a recording in one of the folders the calendar checks")
        return path.resolve()

    @app.post("/api/calendar/open", status_code=204)
    def open_laptop_file(body: CalendarOpen):
        """Play a phone recording in the laptop's own player, or show it in its folder. Nothing is uploaded."""
        path = laptop_file_or_400(body.path)
        if body.reveal and sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path}"])
        elif body.reveal:
            subprocess.Popen(["open", "-R", str(path)])
        elif sys.platform == "win32":
            os.startfile(path)  # noqa: S606 - the user's own file, in a folder they chose
        else:
            subprocess.Popen(["open", str(path)])
        return Response(status_code=204)

    @app.post("/api/calendar/import", status_code=201)
    def import_laptop_file(body: CalendarImport):
        """Add a phone recording that's on the laptop: it's copied in and transcribed like an upload."""
        path = laptop_file_or_400(body.path)
        if body.subject_id is not None and store.get_subject(body.subject_id) is None:
            raise HTTPException(400, "Subject not found")
        rid = uuid.uuid4().hex
        rec_dir = recordings_dir / rid
        rec_dir.mkdir(parents=True)
        copy = rec_dir / f"{UPLOAD_STEM}{safe_suffix(path.name)}"
        shutil.copyfile(path, copy)
        store.create_recording(rid, default_title(path.name), body.subject_id,
                               store.get_settings()["default_language"], path.name)
        store.update_recording(rid, content_hash=file_hash(copy))
        wake()
        return asdict(store.get_recording(rid))

    # --- practice room: talk with an AI playing the other person, then get coached

    def practice_or_404(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, "This practice session has ended") from exc

    @app.get("/api/practice/scenarios")
    def practice_scenarios():
        return [{"id": s.id, "name": s.name, "description": s.description, "role": s.default_role,
                 "time_limit_sec": s.time_limit_sec} for s in SCENARIOS.values()]

    @app.post("/api/practice/sessions", status_code=201)
    def start_practice(body: PracticeStart):
        if body.scenario not in SCENARIOS:
            raise HTTPException(400, "Unknown scenario")
        if transcriber is None or practice_llm is None:
            raise HTTPException(400, "Practice needs a Groq key (to hear you and to reply)")
        try:
            return practice.start(body.scenario, role=body.role, difficulty=body.difficulty, person_id=body.person_id)
        except KeyError as exc:
            raise HTTPException(400, "Pick someone from People to practise with") from exc

    @app.post("/api/practice/sessions/{sid}/turn")
    async def practice_turn(sid: str, audio_file: Annotated[UploadFile, File(alias="audio")],
                            start_sec: Annotated[float, Form()] = 0.0):
        data = await audio_file.read()
        suffix = Path(audio_file.filename or "turn.webm").suffix or ".webm"
        try:
            return await run_in_threadpool(practice_or_404, practice.turn, sid, data, suffix, start_sec=start_sec)
        except (PermanentError, TransientError, RateLimited) as exc:
            raise HTTPException(503, f"Couldn't hear or answer that: {exc}") from exc

    @app.post("/api/practice/sessions/{sid}/finish", status_code=201)
    async def finish_practice(sid: str, audio_file: Annotated[UploadFile | None, File(alias="audio")] = None):
        data = await audio_file.read() if audio_file else None
        rid = await run_in_threadpool(practice_or_404, practice.finish, sid, data, ".webm")
        wake()
        return asdict(store.get_recording(rid))

    @app.delete("/api/practice/sessions/{sid}", status_code=204)
    def discard_practice(sid: str):
        practice.discard(sid)
        return Response(status_code=204)

    # --- people you've had coffee chats with, and your progress

    def person_or_404(person_id: int):
        person = store.get_person(person_id)
        if person is None:
            raise HTTPException(404, "Person not found")
        return person

    def chat_notes(rec: Recording):
        record = store.get_notes(rec.id)
        return record.notes if record and record.status == "ready" and record.notes else None

    @app.get("/api/people")
    def people():
        return [asdict(p) | {"brief": None} for p in store.list_people()]

    @app.get("/api/people/{person_id}")
    def person_detail(person_id: int):
        person = person_or_404(person_id)
        chats = store.list_recordings(person_id=person_id)
        about, promises = [], []
        for rec in chats:
            notes = chat_notes(rec)
            for item in notes.about_them if notes else []:
                about.append({"text": item.text, "recording_id": rec.id, "title": rec.title,
                              "start": item.cites[0] if item.cites else None})
            for item in notes.action_items if notes else []:
                promises.append({"text": item.text, "owner": item.owner, "recording_id": rec.id, "title": rec.title})
        return {**asdict(person), "about": about, "promises": promises,
                "chats": [{"id": r.id, "title": r.title, "when": r.recorded_at or r.created_at} for r in chats]}

    @app.patch("/api/people/{person_id}")
    def patch_person(person_id: int, body: PersonPatch):
        person_or_404(person_id)
        if body.name is not None and not body.name.strip():
            raise HTTPException(400, "Name cannot be empty")
        try:
            store.update_person(person_id, body.name, body.note)
        except DuplicateName as exc:
            raise HTTPException(409, str(exc)) from exc
        return person_detail(person_id)

    @app.delete("/api/people/{person_id}", status_code=204)
    def delete_person(person_id: int):
        if not store.delete_person(person_id):
            raise HTTPException(404, "Person not found")
        return Response(status_code=204)

    @app.post("/api/people/{person_id}/brief")
    async def person_brief(person_id: int):
        person = person_or_404(person_id)
        if writer is None:
            raise HTTPException(400, "The brief needs a notes model")
        chats = [(rec.title, spoken_when(rec.recorded_at or rec.created_at), notes)
                 for rec in reversed(store.list_recordings(person_id=person_id)) if (notes := chat_notes(rec))]
        if not chats:
            raise HTTPException(400, "No coached chats with them yet")
        def brief_scoped():
            with privacy.scope("brief", [rec.id for rec in store.list_recordings(person_id=person_id)]):
                return write_brief(writer, name=person.name, note=person.note, chats=chats, call=lambda fn: fn())

        try:
            brief = await run_in_threadpool(brief_scoped)
        except (PermanentError, TransientError, RateLimited) as exc:
            raise HTTPException(503, f"The brief couldn't be written: {exc}") from exc
        store.set_person_brief(person_id, brief)
        return person_detail(person_id)

    @app.get("/api/coach/progress")
    def coach_progress():
        """Your coffee chats over time: how much you talked, questions, filler words; and recurring coaching."""
        chats, work_on, keep = [], [], []
        for rec in store.conversation_recordings():
            transcript = store.get_transcript(rec.id)
            stats = conversation_stats(transcript.segments) if transcript else None
            notes = chat_notes(rec)
            if stats is None or notes is None:
                continue
            chats.append({"id": rec.id, "title": rec.title, "person": rec.person,
                          "when": rec.recorded_at or rec.created_at, "my_share": stats["my_share"],
                          "questions": len(notes.my_questions) or stats["my_questions"],
                          "fillers_per_min": round(stats["fillers"] / stats["my_minutes"], 2) if stats["my_minutes"] else 0,
                          "longest_turn_sec": stats["longest_turn_sec"]})
            for item in notes.feedback:
                (work_on if item.kind == "improve" else keep).append(
                    {"text": item.text, "recording_id": rec.id, "title": rec.title})
        return {"chats": chats, "work_on": list(reversed(work_on))[:8], "keep": list(reversed(keep))[:5]}

    # --- second brain: concepts across everything, Ask across everything, the graph, and the Obsidian export

    def vault_folder() -> Path:
        chosen = store.get_settings()["vault_folder"].strip()
        return Path(chosen).expanduser() if chosen else Path.home() / "Documents" / "Transcriber vault"

    def check_vault_folder(raw: str) -> str:
        """Your notes are written here as .md files, so keep it away from the system and from this app's own data."""
        path = Path(raw).expanduser().resolve()
        protected = [Path(p).resolve() for p in (os.environ.get("SystemRoot"), os.environ.get("ProgramFiles"),
                                                 os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramData"),
                                                 "/usr", "/etc", "/bin", "/System") if p]
        protected.append(config.data_dir.resolve())
        if path == Path(path.anchor) or any(path == p or p in path.parents for p in protected):
            raise HTTPException(400, "Pick a folder of your own for the notes (not a system folder or the app's data)")
        return str(path)

    def concept_or_404(concept_id: int) -> dict:
        page = brain.concept(concept_id)
        if page is None:
            raise HTTPException(404, "That concept isn't in your brain any more")
        return page

    @app.get("/api/brain")
    async def brain_overview(q: str = "", subject_id: int | None = None, hidden: bool = False):
        """Every concept, most widespread first. Anything that changed since the last look is indexed first."""
        await run_in_threadpool(brain.catch_up)
        everything = brain.list_concepts(q, subject_id, include_hidden=True)
        shown = everything if hidden or q.strip() else [c for c in everything if not c["hidden"]]
        return {**brain.counts(), "concepts": shown, "hidden_count": sum(c["hidden"] for c in everything),
                "vault_folder": str(vault_folder()),
                "writer": writer is not None, "ask": ask_engines[0].name if ask_engines else None}

    @app.get("/api/brain/concepts/{concept_id}")
    def brain_concept(concept_id: int):
        return concept_or_404(concept_id)

    @app.get("/api/brain/find")
    def brain_find(name: str):
        """A concept by name or alias (Pip's open_concept, and links typed by hand)."""
        concept_id = brain.find(name)
        if concept_id is None:
            raise HTTPException(404, f"No concept called “{name}” yet")
        return {"id": concept_id}

    @app.post("/api/brain/concepts/{concept_id}/definition")
    async def brain_define(concept_id: int):
        """Write (or rewrite, once new mentions arrived) the short definition at the top of a concept's page."""
        concept_or_404(concept_id)
        if writer is None:
            raise HTTPException(400, "Definitions need the notes writer (a Groq key)")
        name, texts, count, fingerprint = brain.definition_material(concept_id)
        if not texts:
            raise HTTPException(400, "Nothing to define it from yet")

        def define_scoped():
            with privacy.scope("brain-define", brain.concept_recordings([concept_id])):
                return write_definition(writer, name, texts)

        try:
            text = await run_in_threadpool(define_scoped)
        except privacy.PrivacyBlocked as exc:
            raise HTTPException(403, str(exc)) from exc
        except (PermanentError, TransientError, RateLimited) as exc:
            raise HTTPException(503, f"The definition couldn't be written just now: {exc}") from exc
        if text:
            brain.save_definition(concept_id, text, count, fingerprint)  # dropped if the notes changed meanwhile
        return concept_or_404(concept_id)

    @app.patch("/api/brain/concepts/{concept_id}")
    def brain_rename(concept_id: int, body: ConceptPatch):
        concept_or_404(concept_id)
        kept = concept_id
        try:
            if body.note is not None:
                brain.set_note(concept_id, body.note)
            if body.visibility is not None:
                brain.set_visibility(concept_id, None if body.visibility == "auto" else body.visibility)
            if body.name is not None:
                kept = brain.rename(concept_id, body.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return concept_or_404(kept)

    @app.post("/api/brain/concepts/{concept_id}/aliases")
    def brain_add_alias(concept_id: int, body: ConceptAlias):
        concept_or_404(concept_id)
        try:
            brain.add_alias(concept_id, body.alias)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        brain.catch_up()
        return concept_or_404(concept_id)

    @app.delete("/api/brain/concepts/{concept_id}/aliases")
    def brain_remove_alias(concept_id: int, alias: str):
        concept_or_404(concept_id)
        brain.remove_alias(concept_id, alias)
        return concept_or_404(concept_id)

    @app.post("/api/brain/concepts/{concept_id}/merge")
    def brain_merge(concept_id: int, body: ConceptMerge):
        """These two are the same idea: this one's mentions move to `into`, and its name becomes an alias there."""
        concept_or_404(concept_id)
        concept_or_404(body.into)
        if concept_id == body.into or not brain.merge(concept_id, body.into):
            raise HTTPException(400, "Pick a different concept to merge with")
        return concept_or_404(body.into)

    @app.post("/api/brain/ask")
    async def brain_ask(body: BrainQuestion):
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "Type a question first")
        await run_in_threadpool(brain.catch_up)
        return await run_in_threadpool(ask_brain, brain, ask_engines, question, body.mode)

    @app.get("/api/brain/graph")
    async def brain_map(subject_id: int | None = None, since: str | None = None, limit: int = 60):
        await run_in_threadpool(brain.catch_up)
        return brain_graph(brain, subject_id, since, max(5, min(limit, 300)))

    @app.post("/api/brain/rebuild")
    async def brain_rebuild():
        """Throw the index away and build it again from the notes. Names you merged stay merged."""
        indexed = await run_in_threadpool(brain.rebuild)
        wake()  # the name checks run in the background
        return {"indexed": indexed, **brain.counts()}

    @app.post("/api/brain/export")
    async def brain_export(body: VaultExport | None = None):
        if body and body.folder and body.folder.strip():
            store.update_settings({"vault_folder": check_vault_folder(body.folder.strip())})
        folder = vault_folder()
        try:
            return await run_in_threadpool(export_vault, brain, folder)
        except OSError as exc:
            raise HTTPException(400, f"Couldn't write to {folder}: {exc}") from exc

    @app.post("/api/brain/open-vault", status_code=204)
    def brain_open_vault():
        """Show the exported vault in Explorer (or Finder)."""
        folder = vault_folder()
        if not folder.is_dir():
            raise HTTPException(404, "Export first: the folder isn't there yet")
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - the folder the student chose for their vault
        else:
            subprocess.Popen(["open", str(folder)])
        return Response(status_code=204)

    # --- highlights reel: the key moments in the speaker's own voice

    def highlights_meta(rid: str) -> dict | None:
        path = recordings_dir / rid / "highlights.json"
        if not (path.exists() and (recordings_dir / rid / HIGHLIGHTS_FILE).exists()):
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/api/recordings/{rid}/highlights")
    async def make_highlights_reel(rid: str):
        get_or_404(rid)
        record = store.get_notes(rid)
        transcript = store.get_transcript(rid)
        source = recordings_dir / rid / AUDIO_FILE
        clips = pick_clips(record.notes, transcript.segments)             if record and record.notes and transcript and source.exists() else []
        if not clips:
            raise HTTPException(400, "No key moments to cut yet: the notes need points with timestamps")
        try:
            seconds = await run_in_threadpool(make_highlights, source, clips, recordings_dir / rid / HIGHLIGHTS_FILE,
                                              recordings_dir / rid / "highlights-work")
        except PermanentError as exc:
            raise HTTPException(500, str(exc)) from exc
        meta = {"duration_sec": round(seconds, 1), "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "clips": [{"start": c.start, "end": c.end, "label": c.label} for c in clips]}
        (recordings_dir / rid / "highlights.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return meta

    @app.get("/api/recordings/{rid}/highlights.mp3")
    def highlights_audio(rid: str):
        rec = get_or_404(rid)
        path = recordings_dir / rid / HIGHLIGHTS_FILE
        if not path.exists():
            raise HTTPException(404, "No highlights yet")
        name = recap_filename(rec.subject or rec.title, rec.recorded_at or rec.created_at).replace(".mp3", " – highlights.mp3")
        return FileResponse(path, media_type="audio/mpeg", filename=name)

    # --- audio recaps

    @app.post("/api/recordings/{rid}/recap")
    def make_recap(rid: str):
        get_or_404(rid)
        record = store.get_notes(rid)
        if record is None or record.status != "ready" or record.notes is None or not record.notes.items():
            raise HTTPException(400, "Needs notes first")
        if recap_pipeline is None:
            raise HTTPException(400, "Recaps need a notes model")
        store.queue_recap(rid)
        wake()
        return recap_payload(store.get_recap(rid))

    @app.get("/api/recordings/{rid}/recap.mp3")
    def recap_audio(rid: str):
        rec = get_or_404(rid)
        path = recordings_dir / rid / RECAP_FILE
        if not path.exists():
            raise HTTPException(404, "No recap yet")
        return FileResponse(path, media_type="audio/mpeg",
                            filename=recap_filename(rec.subject or rec.title, rec.recorded_at or rec.created_at))

    @app.get("/api/recaps/latest")
    def latest_recaps():
        """The newest recap of each subject, for the phone's morning download."""
        return [{"subject": rec.subject, "title": rec.title, "recorded_at": rec.recorded_at or rec.created_at,
                 "filename": recap_filename(rec.subject, rec.recorded_at or rec.created_at),
                 "url": f"/api/recordings/{rec.id}/recap.mp3"}
                for rec, _ in store.latest_recaps()]

    @app.get("/api/recaps/sample")
    def recap_sample(voice_name: Annotated[str, Query(alias="voice")]):
        if voice_name not in VOICES:
            raise HTTPException(400, "Unknown voice")
        if voice is None:
            raise HTTPException(503, "The recap voice isn't set up")
        speed = float(store.get_settings()["recap_speed"])
        path = config.data_dir / "recap-samples" / f"{voice_name}-{speed:g}.mp3"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                voice.speak(["Hi. This is how your recaps will sound on the way to class."], voice=voice_name,
                            speed=speed, dst=path)
            except PermanentError as exc:
                raise HTTPException(503, str(exc)) from exc
        return FileResponse(path, media_type="audio/mpeg")

    @app.patch("/api/recordings/{rid}")
    def patch_recording(rid: str, body: RecordingPatch):
        get_or_404(rid)
        fields: dict = {}
        if body.title is not None:
            if not body.title.strip():
                raise HTTPException(400, "Title cannot be empty")
            fields["title"] = body.title.strip()
        if body.subject is not None:
            fields["subject_id"] = subject_id_for(body.subject)
        if body.starred is not None:
            store.set_starred(rid, body.starred)
        if body.notes_style is not None:
            fields["notes_style"] = check_style(body.notes_style) if body.notes_style else None
        if body.person is not None:
            fields["person_id"] = store.get_or_create_person(body.person) if body.person.strip() else None
        store.update_recording(rid, **fields)
        return asdict(store.get_recording(rid))

    def erase(rid: str) -> None:
        store.delete_recording(rid)
        shutil.rmtree(recordings_dir / rid, ignore_errors=True)

    def erase_old_deletions() -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=KEEP_DELETED_DAYS)).isoformat(timespec="seconds")
        for rid in store.purge_deleted(before=cutoff):
            shutil.rmtree(recordings_dir / rid, ignore_errors=True)

    @app.delete("/api/recordings/{rid}", status_code=204)
    def delete_recording(rid: str, forever: bool = False):
        """Moves the recording to Recently Deleted; ?forever=1 erases it now, audio included."""
        get_or_404(rid)
        if forever:
            erase(rid)
        else:
            store.soft_delete(rid)
        return Response(status_code=204)

    @app.post("/api/recordings/delete")
    def delete_many(body: IdList):
        moved = sum(1 for rid in dict.fromkeys(body.ids) if store.soft_delete(rid))
        return {"deleted": moved}

    @app.post("/api/recordings/{rid}/restore")
    def restore_recording(rid: str):
        get_or_404(rid)
        store.restore(rid)
        wake()  # it may still have been waiting to be transcribed
        return asdict(store.get_recording(rid))

    @app.delete("/api/trash")
    def empty_trash():
        erased = store.purge_deleted()
        for rid in erased:
            shutil.rmtree(recordings_dir / rid, ignore_errors=True)
        return {"erased": len(erased)}

    @app.post("/api/recordings/{rid}/retry")
    def retry(rid: str):
        if get_or_404(rid).status != "failed":
            raise HTTPException(409, "Only failed recordings can be retried")
        store.update_recording(rid, status="queued", status_detail=None)
        wake()
        return asdict(store.get_recording(rid))

    translation_runs: dict[str, object] = {}  # recording id -> the run that owns its translation row

    def run_translation(rid: str, token: object, texts: list[str], starts: list[float]) -> None:
        """Only the newest run for a recording may write its row: a run that was deleted or replaced stays silent."""
        mine = lambda: translation_runs.get(rid) is token  # noqa: E731
        translator = Translator(
            writer,
            on_rate_limited=lambda seconds: mine() and store.translation_progress(rid, detail=resume_message(seconds)),
            on_resumed=lambda: mine() and store.translation_progress(rid, detail=None))
        try:
            with privacy.scope("translation", [rid]):
                done = translator(texts, lambda count, total: store.translation_progress(rid, count) if mine() else None)
            if not mine():
                return
            if translator.kept * 2 > len(texts):
                store.fail_translation(rid, f"Your notes model refused most of it ({translator.kept} of {len(texts)} "
                                            "paragraphs). Try again later.")
                return
            note = (f"{translator.kept} paragraph{'' if translator.kept == 1 else 's'} couldn't be translated and "
                    "keep their original words") if translator.kept else None
            store.save_translation(rid, [{"start": s, "text": t} for s, t in zip(starts, done)], translator.name, note)
        except (PermanentError, TransientError) as exc:
            if mine():
                store.fail_translation(rid, str(exc))
        except Exception as exc:
            privacy.log_failure(logging.getLogger(__name__), "Unexpected error while translating %s", rid)
            if mine():
                store.fail_translation(rid, f"Unexpected error ({type(exc).__name__}); details are in the app log")
        finally:
            if mine():
                translation_runs.pop(rid, None)

    @app.post("/api/recordings/{rid}/translate", status_code=202)
    def translate(rid: str):
        get_or_404(rid)
        transcript = store.get_transcript(rid)
        if transcript is None:
            raise HTTPException(409, "There's no transcript to translate yet")
        if writer is None:
            raise HTTPException(400, "Translation needs a notes model (set one up in .env)")
        paragraphs = to_paragraphs(transcript.segments)
        if not paragraphs:
            raise HTTPException(409, "No speech was found in this recording")
        if not store.start_translation(rid, len(paragraphs)):
            raise HTTPException(409, "This is already being translated")
        token = translation_runs[rid] = object()
        threading.Thread(target=run_translation, name="translate", daemon=True,
                         args=(rid, token, [p.text for p in paragraphs], [p.start for p in paragraphs])).start()
        return store.get_translation(rid)

    @app.delete("/api/recordings/{rid}/translate", status_code=204)
    def remove_translation(rid: str):
        get_or_404(rid)
        translation_runs.pop(rid, None)  # a run still going stops writing
        store.delete_translation(rid)

    @app.post("/api/recordings/{rid}/retranscribe")
    def retranscribe(rid: str, body: RetranscribeRequest | None = None):
        rec = get_or_404(rid)
        if rec.status not in ("done", "failed"):
            raise HTTPException(409, "Wait until this recording has finished processing")
        language = body.language if body and body.language else rec.language
        check_language(language)
        store.delete_transcript(rid)
        store.delete_notes(rid)
        translation_runs.pop(rid, None)
        store.delete_translation(rid)
        shutil.rmtree(recordings_dir / rid / WORK_DIR, ignore_errors=True)
        if body and body.speakers is not None:
            store.set_want_speakers(rid, body.speakers)
        store.update_recording(rid, status="queued", status_detail=None, language=language,
                               progress_done=0, progress_total=0)
        wake()
        return asdict(store.get_recording(rid))

    @app.get("/api/recordings/{rid}/audio")
    def audio_file(rid: str):
        get_or_404(rid)
        path = audio_path(rid)
        if path is None:
            raise HTTPException(404, "Audio is not ready yet")
        return FileResponse(path, media_type="audio/webm" if path.suffix == ".webm" else "audio/flac")

    @app.get("/api/recordings/{rid}/export")
    def export(rid: str, fmt: Annotated[str, Query(alias="format", pattern="^(txt|md)$")] = "txt",
               lang: Annotated[str, Query(pattern="^(original|english)$")] = "original"):
        rec = get_or_404(rid)
        transcript = store.get_transcript(rid)
        if transcript is None:
            raise HTTPException(404, "No transcript yet")
        paragraphs = named_paragraphs(transcript.segments, store.get_speaker_names(rid))
        if lang == "english":
            translation = store.get_translation(rid)
            if not translation or translation["status"] != "ready":
                raise HTTPException(409, "Translate this recording to English first")
            if len(translation["paragraphs"]) != len(paragraphs):
                raise HTTPException(409, "The transcript changed since it was translated. Translate it again.")
            paragraphs = [replace(p, text=t["text"]) for p, t in zip(paragraphs, translation["paragraphs"])]
        if fmt == "txt":
            content, media_type = to_txt(rec.title, paragraphs), "text/plain"
        else:
            local_date = datetime.fromisoformat(rec.recorded_at or rec.created_at).astimezone().strftime("%Y-%m-%d")
            record = store.get_notes(rid)
            notes = record.notes if record and record.status == "ready" else None
            content = to_md(rec.title, local_date, rec.subject, rec.duration_sec or 0.0, paragraphs, notes)
            media_type = "text/markdown"
        filename = f"{safe_filename(rec.title)}{' (English)' if lang == 'english' else ''}.{fmt}"
        return Response(content, media_type=f"{media_type}; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.put("/api/recordings/{rid}/speakers")
    def put_speakers(rid: str, body: SpeakerNames):
        get_or_404(rid)
        transcript = store.get_transcript(rid)
        labels = [p.speaker for p in to_paragraphs(transcript.segments)] if transcript else []
        unknown = set(body.names) - set(labels)
        if unknown:
            raise HTTPException(400, f"No such speaker in this recording: {', '.join(sorted(unknown))}")
        custom = store.get_speaker_names(rid)
        for label, name in body.names.items():
            name = " ".join(name.split())[:60]
            if name:
                custom[label] = name
            else:
                custom.pop(label, None)
        store.set_speaker_names(rid, custom)
        return [{"label": label, "name": name} for label, name in speaker_names(labels, custom).items()]

    def instructions_payload(rid: str) -> dict:
        subject_text, recording_text = store.notes_instructions(rid)
        return {"subject": subject_text, "recording": recording_text}

    @app.put("/api/recordings/{rid}/instructions")
    def put_instructions(rid: str, body: Instructions):
        get_or_404(rid)
        store.set_recording_instructions(rid, body.text)
        return instructions_payload(rid)

    @app.put("/api/recordings/{rid}/my-notes")
    def put_my_notes(rid: str, body: MyNotes):
        get_or_404(rid)
        store.set_my_notes(rid, body.text)
        return {"my_notes": body.text}

    @app.post("/api/recordings/{rid}/notes/regenerate")
    def regenerate_notes(rid: str):
        get_or_404(rid)
        if store.get_transcript(rid) is None:
            raise HTTPException(409, "Notes can be written once the transcript is ready")
        record = store.get_notes(rid)
        if record and record.status in ("writing", "checking"):
            raise HTTPException(409, "Notes are being written right now")
        store.queue_notes(rid)
        wake()
        return notes_payload(store.get_notes(rid))

    @app.post("/api/recordings/{rid}/topics", status_code=201)
    def add_topic(rid: str, body: TopicName):
        get_or_404(rid)
        if not body.name.strip():
            raise HTTPException(400, "Topic name cannot be empty")
        store.add_topic(rid, body.name)
        return store.recording_topics(rid)

    @app.put("/api/recordings/{rid}/topics/{topic_id}")
    def accept_topic(rid: str, topic_id: int):
        get_or_404(rid)
        if not store.accept_topic(rid, topic_id):
            raise HTTPException(404, "Topic not on this recording")
        return store.recording_topics(rid)

    @app.delete("/api/recordings/{rid}/topics/{topic_id}")
    def remove_topic(rid: str, topic_id: int):
        get_or_404(rid)
        if not store.remove_topic(rid, topic_id):
            raise HTTPException(404, "Topic not on this recording")
        return store.recording_topics(rid)

    # --- Ask chat

    @app.post("/api/recordings/{rid}/chat")
    def chat(rid: str, body: ChatMessage):
        rec = get_or_404(rid)
        question = body.message.strip()
        if not question:
            raise HTTPException(400, "Type a message first")
        transcript = store.get_transcript(rid)
        if transcript is None:
            raise HTTPException(409, "You can ask about a recording once its transcript is ready")
        record = store.get_notes(rid)
        try:
            with privacy.scope("ask", [rid]):
                result = ask_meeting(ask_engines, title=rec.title,
                                 paragraphs=named_paragraphs(transcript.segments, store.get_speaker_names(rid)),
                                 long_form=(rec.duration_sec or 0) >= 3600,
                                 notes=record.notes if record and record.notes else Notes(),
                                 my_notes=store.get_my_notes(rid) or "", history=store.list_chat(rid),
                                 question=question,
                                 instructions="\n".join(t for t in store.notes_instructions(rid) if t),
                                 part_starts=store.get_part_starts(rid), duration_sec=rec.duration_sec)
        except PermanentError as exc:
            raise HTTPException(502, str(exc)) from exc
        user_message = store.add_chat_message(rid, "user", question)
        reply = store.add_chat_message(rid, "assistant", result.answer, engine=result.engine,
                                       proposal=result.proposal, note=result.note)
        return [user_message, reply]

    @app.delete("/api/recordings/{rid}/chat", status_code=204)
    def clear_chat(rid: str):
        get_or_404(rid)
        store.clear_chat(rid)
        return Response(status_code=204)

    def pending_proposal(rid: str, message_id: int) -> dict:
        get_or_404(rid)
        message = store.get_chat_message(rid, message_id)
        if message is None or message["proposal"] is None:
            raise HTTPException(404, "No proposed change here")
        if message["proposal_state"] != "pending":
            raise HTTPException(409, f"This change was already {message['proposal_state']}")
        return message

    def notes_and_chat(rid: str) -> dict:
        return {"notes": notes_payload(store.get_notes(rid)), "chat": store.list_chat(rid)}

    @app.post("/api/recordings/{rid}/chat/{message_id}/apply")
    def apply_change(rid: str, message_id: int):
        message = pending_proposal(rid, message_id)
        record = store.get_notes(rid)
        ensure_notes_idle(record)
        old = record.notes if record and record.notes else Notes()
        save_edited_notes(rid, record, apply_proposal(old, message["proposal"]))
        store.set_proposal_state(message_id, "applied")
        return notes_and_chat(rid)

    @app.post("/api/recordings/{rid}/chat/{message_id}/dismiss")
    def dismiss_change(rid: str, message_id: int):
        pending_proposal(rid, message_id)
        store.set_proposal_state(message_id, "dismissed")
        return notes_and_chat(rid)

    # --- editing AI notes by hand

    def ensure_notes_idle(record: NotesRecord | None) -> None:
        if record and record.status in ("writing", "checking"):
            raise HTTPException(409, "Notes are being written right now; try again in a moment")

    def save_edited_notes(rid: str, record: NotesRecord | None, notes: Notes) -> None:
        store.upsert_notes(rid, notes, writer=record.writer if record and record.writer else "you",
                           checker=record.checker if record else None,
                           check_detail=record.check_detail if record else None)

    def edit_notes(rid: str, change) -> dict:
        get_or_404(rid)
        record = store.get_notes(rid)
        ensure_notes_idle(record)
        try:
            notes = change(record.notes if record and record.notes else Notes())
        except KeyError as exc:
            raise HTTPException(404, "That note no longer exists") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        save_edited_notes(rid, record, notes)
        return notes_payload(store.get_notes(rid))

    @app.post("/api/recordings/{rid}/notes/items", status_code=201)
    def add_note_item(rid: str, body: NewNoteItem):
        return edit_notes(rid, lambda notes: add_item(notes, body.section, text=body.text, owner=body.owner,
                                                      due=body.due))

    @app.put("/api/recordings/{rid}/notes/items/{item_id}")
    def update_note_item(rid: str, item_id: str, body: NoteItemBody):
        return edit_notes(rid, lambda notes: update_item(notes, item_id, text=body.text, owner=body.owner,
                                                         due=body.due))

    @app.delete("/api/recordings/{rid}/notes/items/{item_id}")
    def delete_note_item(rid: str, item_id: str):
        return edit_notes(rid, lambda notes: delete_item(notes, item_id))

    # --- subjects and topics

    @app.get("/api/subjects")
    def subjects():
        return [asdict(s) for s in store.list_subjects()]

    @app.post("/api/subjects", status_code=201)
    def create_subject(body: SubjectCreate):
        if not body.name.strip():
            raise HTTPException(400, "Subject name cannot be empty")
        try:
            subject = store.create_subject(body.name, body.description)
            if body.instructions.strip():
                store.update_subject(subject.id, instructions=body.instructions)
            return asdict(store.get_subject(subject.id))
        except DuplicateName as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.patch("/api/subjects/{subject_id}")
    def patch_subject(subject_id: int, body: SubjectPatch):
        if body.name is not None and not body.name.strip():
            raise HTTPException(400, "Subject name cannot be empty")
        try:
            found = store.update_subject(subject_id, body.name, body.description, body.instructions,
                                         check_style(body.notes_style) if body.notes_style else None,
                                         **({"schedule": keep_pip_changes(check_schedule(body.schedule), store.get_subject(subject_id))}
                                            if body.schedule is not None else {}))
        except DuplicateName as exc:
            raise HTTPException(409, str(exc)) from exc
        if not found:
            raise HTTPException(404, "Subject not found")
        return asdict(store.get_subject(subject_id))

    @app.delete("/api/subjects/{subject_id}", status_code=204)
    def delete_subject(subject_id: int):
        if not store.delete_subject(subject_id):
            raise HTTPException(404, "Subject not found")
        return Response(status_code=204)

    @app.get("/api/topics")
    def topics():
        return [asdict(t) for t in store.list_topics()]

    @app.patch("/api/topics/{topic_id}")
    def rename_topic(topic_id: int, body: TopicName):
        if not body.name.strip():
            raise HTTPException(400, "Topic name cannot be empty")
        surviving = store.rename_topic(topic_id, body.name)
        if surviving is None:
            raise HTTPException(404, "Topic not found")
        return {"id": surviving}

    @app.delete("/api/topics/{topic_id}", status_code=204)
    def delete_topic(topic_id: int):
        if not store.delete_topic(topic_id):
            raise HTTPException(404, "Topic not found")
        return Response(status_code=204)

    # --- live recording: audio pieces in while you speak, text out window by window

    def live_or_404(fn, *args):
        try:
            return fn(*args)
        except KeyError as exc:
            raise HTTPException(404, "This live recording has ended or doesn't exist") from exc

    @app.post("/api/live", status_code=201)
    def start_live(body: LiveStart):
        language = body.language or store.get_settings()["default_language"]
        check_language(language)
        return {"id": live.create(title=body.title, subject_id=subject_id_for(body.subject), language=language,
                                  privacy=body.privacy)}

    @app.put("/api/live/{sid}/chunks/{seq}")
    async def live_chunk(sid: str, seq: int, request: Request):
        data = await request.body()
        if len(data) > MAX_LIVE_CHUNK:
            raise HTTPException(413, "Audio piece too large")
        await run_in_threadpool(live_or_404, live.add_chunk, sid, seq, data)
        return {"received": seq}

    @app.get("/api/live/{sid}")
    def live_status(sid: str, after: int = 0):
        return live_or_404(live.poll, sid, after)

    @app.post("/api/live/{sid}/finish")
    def finish_live(sid: str):
        rid = live_or_404(live.finish, sid)
        if rid is None:
            raise HTTPException(400, "Nothing was recorded")
        return {"recording_id": rid}

    @app.delete("/api/live/{sid}", status_code=204)
    def discard_live(sid: str):
        live.discard(sid)
        return Response(status_code=204)

    # --- meetings: Zoom, Teams or Meet recorded straight from Windows (speakers and microphone, kept apart)

    @app.get("/api/meeting/devices")
    def meeting_devices_info():
        return meeting.info()

    @app.post("/api/meeting/start", status_code=201)
    def start_meeting(body: LiveStart):
        language = body.language or store.get_settings()["default_language"]
        check_language(language)
        try:
            sid = meeting.start(title=body.title, subject_id=subject_id_for(body.subject), language=language,
                                privacy=body.privacy)
        except MeetingError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": sid}

    @app.get("/api/meeting/{sid}")
    def meeting_status(sid: str, after: int = 0):
        return live_or_404(meeting.poll, sid, after)

    @app.post("/api/meeting/{sid}/finish")
    def finish_meeting(sid: str):
        rid = live_or_404(meeting.finish, sid)
        if rid is None:
            raise HTTPException(400, "Nothing was recorded")
        return {"recording_id": rid}

    @app.delete("/api/meeting/{sid}", status_code=204)
    def discard_meeting(sid: str):
        meeting.discard(sid)
        return Response(status_code=204)

    # --- the iPhone "Send to Transcriber" shortcut (plain text in and out, easy for Shortcuts)

    @app.get("/api/share/subjects", response_class=PlainTextResponse)
    def share_subjects():
        return "\n".join([NO_SUBJECT, *(s.name for s in store.list_subjects())])

    @app.post("/api/share", status_code=201, response_class=PlainTextResponse)
    def share(file: Annotated[UploadFile, File()], subject: Annotated[str, Form()] = "",
              title: Annotated[str, Form()] = ""):
        subject = "" if subject.strip() == NO_SUBJECT else subject
        language = store.get_settings()["default_language"]
        rid = uuid.uuid4().hex
        rec_dir = recordings_dir / rid
        rec_dir.mkdir(parents=True)
        original = Path(file.filename or "Voice memo.m4a").name
        with open(rec_dir / f"{UPLOAD_STEM}{safe_suffix(original)}", "wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        rec = store.create_recording(rid, " ".join(title.split()) or Path(original).stem, subject_id_for(subject),
                                     language, original)
        wake()
        return f"Sent “{rec.title}” to {rec.subject or 'Transcriber'}. The transcript and notes will follow."

    # --- settings

    @app.get("/api/settings")
    def get_settings():
        return {**store.get_settings(), "vocabulary_max_chars": VOCAB_MAX_CHARS, "recap_voices": VOICES}

    @app.put("/api/settings")
    def put_settings(body: SettingsUpdate):
        values: dict[str, str] = {}
        if body.vocabulary is not None:
            values["vocabulary"] = body.vocabulary
        if body.default_language is not None:
            check_language(body.default_language)
            values["default_language"] = body.default_language
        if body.prefer_server is not None:
            values["prefer_server"] = "1" if body.prefer_server else "0"
        if body.default_privacy is not None:
            if body.default_privacy not in ("standard", "private"):
                raise HTTPException(400, "New recordings can start as Standard or Private")
            values["default_privacy"] = body.default_privacy
        for key in ("groq_hour_limit_sec", "groq_day_limit_sec"):
            value = getattr(body, key)
            if value is not None:
                if value <= 0:
                    raise HTTPException(400, "Limits must be more than zero")
                values[key] = f"{value:g}"
        if body.recaps_on is not None:
            values["recaps_on"] = "1" if body.recaps_on else "0"
        if body.class_reminders is not None:
            values["class_reminders"] = "1" if body.class_reminders else "0"
        if body.recap_voice is not None:
            if body.recap_voice not in VOICES:
                raise HTTPException(400, "Unknown voice")
            values["recap_voice"] = body.recap_voice
        if body.recap_speed is not None:
            if not 1.0 <= body.recap_speed <= 1.3:
                raise HTTPException(400, "Recap speed must be between 1.0x and 1.3x")
            values["recap_speed"] = f"{body.recap_speed:g}"
        if body.time_zone is not None:
            try:
                ZoneInfo(body.time_zone)
            except (KeyError, ValueError) as exc:
                raise HTTPException(400, "Unknown time zone") from exc
            values["time_zone"] = body.time_zone
        if body.scan_folders is not None:
            values["scan_folders"] = "\n".join(line.strip() for line in body.scan_folders.splitlines() if line.strip())
        if body.vault_folder is not None:
            values["vault_folder"] = check_vault_folder(body.vault_folder.strip()) if body.vault_folder.strip() else ""
        store.update_settings(values)
        return get_settings()

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    local_hosts = {"127.0.0.1", "localhost", "::1", "testserver", *config.allowed_hosts}  # allowed_hosts: your phone link

    @app.middleware("http")
    async def only_this_computer(request, call_next):
        """The app is for the browser on this laptop. A web page you visit must not be able to drive it: not through
        a rebound host name (the Host header says who the page thinks it is talking to), and not by posting a form
        from another site (a browser always says where such a request came from, in Origin)."""
        if (request.url.hostname or "") not in local_hosts:
            return JSONResponse({"detail": "This app only answers on this computer (localhost)."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS") and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Requests from other sites are refused."}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def revalidate_ui_files(request, call_next):
        # Browsers must re-check the UI files so an app update is never hidden by a stale cache.
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    threading.Thread(target=backfill_hashes, name="hash-backfill", daemon=True).start()
    return app


def recap_payload(recap: Recap | None) -> dict | None:
    if recap is None:
        return None
    return {"status": recap.status, "detail": recap.status_detail, "duration_sec": recap.duration_sec,
            "voice": recap.voice, "script": recap.script, "updated_at": recap.updated_at}


def recap_filename(subject: str | None, iso: str, tz: str | None = None) -> str:
    """'Machine Learning – Wed 7 Oct.mp3': the subject and the day it was recorded, safe for Files on the phone."""
    moment = datetime.fromisoformat(iso).astimezone(ZoneInfo(tz) if tz else None)
    name = " ".join(re.sub(r'[\\/:*?"<>|]+', " ", subject or "Recap").split())
    return f"{name} – {moment:%a} {moment.day} {moment:%b}.mp3"


def notes_payload(record: NotesRecord | None) -> dict | None:
    if record is None:
        return None
    return {"status": record.status, "status_detail": record.status_detail, "writer": record.writer,
            "checker": record.checker, "check_detail": record.check_detail,
            "content": record.notes.to_dict() if record.notes else None}


def check_language(code: str) -> None:
    if code not in LANGUAGES:
        raise HTTPException(400, f"Unknown language '{code}'")


def safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".bin"


def check_schedule(schedule: dict) -> dict | None:
    """A class timetable from the subject dialog; {} (no days) clears it."""
    days = sorted({int(d) for d in schedule.get("days") or [] if str(d).isdigit() and 0 <= int(d) <= 6})
    if not days:
        return None
    try:
        hour, minute = (int(part) for part in str(schedule.get("start") or "09:00").split(":"))
        minutes = int(schedule.get("minutes") or 60)
        first = date.fromisoformat(schedule["from"]).isoformat() if schedule.get("from") else None
        until = date.fromisoformat(schedule["until"]).isoformat() if schedule.get("until") else None
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Class times must look like 10:00, with dates like 2026-08-24") from exc
    if not (0 <= hour < 24 and 0 <= minute < 60 and 5 <= minutes <= 600):
        raise HTTPException(400, "Check the class start time and length")
    result = {"days": days, "start": f"{hour:02d}:{minute:02d}", "minutes": minutes, "from": first, "until": until}
    try:  # skipped dates and one-off classes (set by Pip) survive being saved
        skip = sorted({date.fromisoformat(str(d)).isoformat() for d in schedule.get("skip") or []})
        extra = [{"date": date.fromisoformat(str(e["date"])).isoformat(), "start": str(e["start"]), "minutes": int(e["minutes"])}
                 for e in schedule.get("extra") or []]
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, "Skipped dates must look like 2026-10-19") from exc
    if skip:
        result["skip"] = skip
    if extra:
        result["extra"] = extra
    return result


def keep_pip_changes(new: dict | None, subject) -> dict | None:
    """Saving the timetable dialog must not forget the weeks Pip skipped or the one-off classes it added."""
    if new is None or subject is None or not subject.schedule:
        return new
    for key in ("skip", "extra"):
        if key not in new and subject.schedule.get(key):
            new[key] = subject.schedule[key]
    return new


def check_style(style: str) -> str:
    if style not in STYLES:
        raise HTTPException(400, f"Notes style must be one of: {', '.join(STYLES)}")
    return style


def default_title(filename: str) -> str:
    return f"{Path(filename).stem} — {datetime.now():%Y-%m-%d}"


def safe_filename(title: str) -> str:
    name = " ".join(re.sub(r"[^A-Za-z0-9 _-]+", " ", title).split())
    return name or "transcript"
