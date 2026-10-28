"""Practice room: you talk, an AI plays the other person (coffee-chat partner, interviewer, customer), and the
session becomes an ordinary recording that the conversation coach scores. Your side is kept as compressed audio."""
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import audio, privacy
from .llm.base import LLMClient
from .notes.coach import ME, PRACTICE_ORIGINAL, THEM
from .postprocess import filter_hallucinations, offset, to_paragraphs
from .prompting import build_prompt
from .providers.base import Segment, Transcriber
from .store import Store

SPEECH_WORDS_PER_SEC = 2.6  # to place the AI's spoken replies on the session's timeline
REPLY_GAP_SEC = 1.0
PRACTICE_AUDIO = "audio.webm"  # Opus: about 0.18 MB a minute at 24 kbps
DIFFICULTIES = {"friendly": "Be warm and encouraging, and easy to talk to.",
                "neutral": "Be polite and realistic: you have other things to do, but you are open.",
                "tough": "Be tough but fair: busy, a little skeptical, push back and ask follow-up questions when an "
                         "answer is vague."}


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    description: str
    persona: str        # who the AI plays; {role} is filled in
    default_role: str
    coach_brief: str    # sent to the coach as this recording's notes instructions
    opener: str | None = None
    time_limit_sec: int | None = None


SCENARIOS = {s.id: s for s in (
    Scenario("coffee_chat", "Coffee chat", "Practise your intro, questions and follow-ups with a professional.",
             "You are {role}, having a relaxed coffee chat with a university student who asked to learn from you. "
             "Answer their questions with a believable career story, opinions and advice. Let them lead; only ask "
             "a question back now and then.", "a professional in the field the student is curious about",
             "This was a practice coffee chat with an AI playing the other person. Coach the intro, the open questions, "
             "the follow-ups and how much the user listened."),
    Scenario("interview", "Behavioral interview", "Answer interview questions; scored on STAR, specifics and length.",
             "You are {role}, interviewing a university student for an internship. Ask one interview question at a "
             "time: start with 'tell me about yourself', then behavioral questions (a time you led, failed, handled "
             "conflict). Ask a short follow-up when an answer is vague, then move on.",
             "a hiring manager for a business internship",
             "This was a practice behavioral interview with an AI interviewer. Coach each answer on STAR structure "
             "(situation, task, action, result), specific examples and numbers, and length (about one to two minutes). "
             "Treat the interviewer's questions as theirs, not the user's.",
             opener="Thanks for coming in. Let's start simple: tell me a little about yourself."),
    Scenario("sell_pen", "Sell me this pen", "One minute to sell a pen to a customer.",
             "You are {role}. The student must sell you an ordinary pen. Don't make it easy: you don't think you need a "
             "pen. Answer their questions honestly about your work and habits, raise one realistic objection, and only "
             "agree to buy if they connect the pen to something you need.", "a busy office manager",
             "This was a 'sell me this pen' practice with an AI customer. Coach whether the user asked about the "
             "customer's needs before pitching, tied the pen to those needs, handled the objection and asked for the "
             "sale, within one minute.", opener="Alright, here's the pen. Sell it to me. You've got one minute.",
             time_limit_sec=60),
    Scenario("elevator", "Elevator pitch", "Introduce yourself to someone at an event.",
             "You are {role} at a networking event. A student walks up to you. Be friendly but short on time: react "
             "naturally to their introduction and ask one or two questions about what they want.",
             "a senior professional", "This was a practice elevator pitch with an AI at a networking event. Coach "
             "clarity, a memorable hook, keeping it under 45 seconds, and a clear ask.",
             opener="Hi there, I don't think we've met. What brings you here?"),
    Scenario("person", "Practise with someone you know", "Rehearse your next chat with someone from People.",
             "{role}", "", "This was a practice run with an AI playing someone the user has met before. Coach the intro, "
             "the open questions, the follow-ups and how much the user listened."),
)}

RULES = """Rules:
- Speak like a real person in a spoken conversation: 1 to 3 sentences, natural and plain. No lists, headings, \
emojis or stage directions.
- Never break character and never coach or grade the user during the conversation.
- Set done to true only when the conversation has clearly ended (goodbyes, or the time is up); otherwise false."""

REPLY_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}, "done": {"type": "boolean"}},
                "required": ["reply", "done"], "additionalProperties": False}


def persona_prompt(scenario: Scenario, role: str = "", difficulty: str = "neutral") -> str:
    persona = scenario.persona.format(role=role.strip() or scenario.default_role)
    return f"{persona}\n{DIFFICULTIES.get(difficulty, DIFFICULTIES['neutral'])}\n\n{RULES}"


def _persona_name(role: str) -> str:
    words = role.strip().split()
    if words and words[0].lower() in ("a", "an", "the"):
        words = words[1:]
    name = " ".join(words) or "Practice partner"
    return f"{name[0].upper()}{name[1:]} (practice)"


@dataclass
class _Session:
    id: str
    scenario: Scenario
    system: str
    persona: str
    folder: Path
    turns: list[dict] = field(default_factory=list)  # {who, start, end, text, segments?}
    sources: tuple[str, ...] = ()  # recordings the persona was written from: they set how private its prompts are


class PracticeManager:
    def __init__(self, store: Store, transcriber: Transcriber | None, llm: LLMClient | None, folder: Path,
                 recordings_dir: Path):
        self.store, self.transcriber, self.llm = store, transcriber, llm
        self.folder, self.recordings_dir = folder, recordings_dir
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def start(self, scenario_id: str, *, role: str = "", difficulty: str = "neutral",
              person_id: int | None = None) -> dict:
        scenario = SCENARIOS[scenario_id]
        if scenario_id == "person":
            person = self.store.get_person(person_id) if person_id is not None else None
            if person is None:
                raise KeyError("person")
            facts = []
            sources = [rec.id for rec in self.store.list_recordings(person_id=person.id)]
            for rec in self.store.list_recordings(person_id=person.id):
                record = self.store.get_notes(rec.id)
                if record and record.notes:
                    facts += [item.text for item in record.notes.about_them]
            role = (f"You are playing a practice version of {person.name}, not the real person, in a follow-up coffee "
                    f"chat with a student who has met them before. Base your background, views and advice only on "
                    f"these notes from their real chats, and stay consistent with them:\n"
                    + "\n".join(f"- {fact}" for fact in facts or ["(no notes yet: be a believable professional)"]))
            persona = f"{person.name} (practice)"
            system = persona_prompt(scenario, role, difficulty)
        else:
            sources = []
            persona = _persona_name(role or scenario.default_role) if scenario_id != "interview" or role \
                else "Interviewer (practice)"
            system = persona_prompt(scenario, role, difficulty)
        sid = uuid.uuid4().hex
        session = _Session(sid, scenario, system, persona, self.folder / sid, sources=tuple(sources))
        session.folder.mkdir(parents=True)
        if scenario.opener:
            session.turns.append(self._ai_turn(scenario.opener, 0.0))
        with self._lock:
            self._sessions[sid] = session
        return {"id": sid, "scenario": scenario.id, "name": scenario.name, "persona": persona,
                "opener": scenario.opener, "time_limit_sec": scenario.time_limit_sec}

    def _get(self, sid: str) -> _Session:
        with self._lock:
            return self._sessions[sid]

    @staticmethod
    def _ai_turn(text: str, start: float) -> dict:
        return {"who": THEM, "start": start, "end": start + max(1.0, len(text.split()) / SPEECH_WORDS_PER_SEC),
                "text": text}

    def _class(self) -> str:
        chosen = self.store.get_settings().get("default_privacy")
        return chosen if chosen in ("standard", "private") else "standard"

    def turn(self, sid: str, data: bytes, suffix: str, *, start_sec: float) -> dict:
        """One thing you said (audio), then the AI's reply. start_sec is where it sits in the session recording."""
        session = self._get(sid)
        raw = session.folder / f"turn{len(session.turns):03d}{suffix or '.webm'}"
        raw.write_bytes(data)
        flac = raw.with_suffix(".flac")
        audio.normalize(raw, flac)
        prompt = build_prompt(self.store.get_settings()["vocabulary"], "")
        with privacy.scope("practice", classification=self._class()):
            segments = filter_hallucinations(self.transcriber.transcribe(flac, language="en", prompt=prompt))
        self.store.record_usage(audio.probe_duration(flac), self.transcriber.name.split("/")[0])
        segments = offset(segments, start_sec)
        text = " ".join(s.text.strip() for s in segments).strip()
        if not text:
            return {"you": "", "reply": None, "done": False}
        session.turns.append({"who": ME, "start": segments[0].start, "end": segments[-1].end, "text": text,
                              "segments": segments})
        history = "\n".join(f"{'You' if t['who'] == ME else 'Them'}: {t['text']}" for t in session.turns)
        with privacy.scope("practice", session.sources, classification=self._class()):
            raw_reply = self.llm.complete_json(system=session.system, user=f"The conversation so far:\n{history}\n\n"
                                               "Reply with your next line as Them.", schema=REPLY_SCHEMA,
                                               schema_name="practice_reply", effort="low")
        reply = " ".join(str(raw_reply.get("reply") or "").split()) or "Sorry, could you say that again?"
        session.turns.append(self._ai_turn(reply, segments[-1].end + REPLY_GAP_SEC))
        return {"you": text, "reply": reply, "done": bool(raw_reply.get("done"))}

    def finish(self, sid: str, data: bytes | None, suffix: str) -> str:
        """Save the session as a recording (your audio, both sides' words) and queue the coach."""
        session = self._get(sid)
        rid = uuid.uuid4().hex
        rec_dir = self.recordings_dir / rid
        rec_dir.mkdir(parents=True)
        duration = None
        if data:
            (rec_dir / PRACTICE_AUDIO).write_bytes(data)
            try:
                duration = audio.probe_duration(rec_dir / PRACTICE_AUDIO)
            except Exception:
                duration = None
        segments: list[Segment] = []
        for turn in sorted(session.turns, key=lambda t: t["start"]):
            if turn["who"] == ME:
                segments += [Segment(s.start, s.end, s.text, s.avg_logprob, s.no_speech_prob, s.compression_ratio, ME)
                             for s in turn["segments"]]
            else:
                segments.append(Segment(turn["start"], turn["end"], turn["text"], speaker=THEM))
        title = f"Practice: {session.scenario.name} — {datetime.now():%Y-%m-%d %H:%M}"
        self.store.create_recording(rid, title, None, "en", PRACTICE_ORIGINAL)
        self.store.update_recording(rid, notes_style="conversation",
                                    duration_sec=duration or (segments[-1].end if segments else 0.0),
                                    recorded_at=datetime.now(UTC).isoformat(timespec="seconds"))
        text = "\n\n".join(p.text for p in to_paragraphs(segments))
        self.store.save_transcript(rid, text, segments, f"{self.transcriber.name} (practice)")
        self.store.set_speaker_names(rid, {ME: "You", THEM: session.persona})
        self.store.set_recording_instructions(rid, session.scenario.coach_brief)
        if any(s.speaker == ME for s in segments):
            self.store.queue_notes(rid)
        self.discard(sid)
        return rid

    def discard(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.pop(sid, None)
        if session:
            shutil.rmtree(session.folder, ignore_errors=True)
