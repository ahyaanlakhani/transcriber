"""Practice room: talk with an AI playing a coffee-chat partner, an interviewer or a customer, then get coached."""
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from transcriber import audio
from transcriber.notes.coach import ME, THEM, conversation_stats
from transcriber.notes.model import notes_from_writer
from transcriber.practice import SCENARIOS, PracticeManager, persona_prompt
from transcriber.providers.base import Segment
from transcriber.store import Store

from .helpers import FakeLLM, FakeTranscriber, synth_audio
from .test_app import build


def webm(tmp_path, seconds=2.0, name="turn"):
    wav = synth_audio(tmp_path / f"{name}.wav", [("tone", seconds)])
    out = tmp_path / f"{name}.webm"
    subprocess.run([audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libopus",
                    "-b:a", "24k", str(out)], check=True)
    return out.read_bytes()


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "app.db")
    transcriber = FakeTranscriber([[Segment(0.0, 2.0, "I'd love to hear how you got started.", -0.2, 0.01, 1.0)],
                                   [Segment(0.0, 1.5, "Um, what would you do differently?", -1.1, 0.01, 1.0)]])
    llm = FakeLLM("groq:gpt-oss-20b", [{"reply": "I started as an engineer, actually.", "done": False},
                                       {"reply": "Honestly, I'd talk to users sooner. Anything else?", "done": False}])
    manager = PracticeManager(store, transcriber, llm, tmp_path / "practice", tmp_path / "recordings")
    yield SimpleNamespace(store=store, manager=manager, llm=llm, transcriber=transcriber, tmp=tmp_path)
    store.close()


# --- scenarios

def test_every_scenario_has_what_it_needs():
    assert {"coffee_chat", "interview", "sell_pen", "elevator", "person"} <= set(SCENARIOS)
    assert SCENARIOS["sell_pen"].time_limit_sec == 60
    for scenario in SCENARIOS.values():
        assert scenario.name and scenario.coach_brief and scenario.persona


def test_persona_prompt_sets_role_difficulty_and_rules():
    prompt = persona_prompt(SCENARIOS["interview"], role="a hiring manager at a bank", difficulty="tough")
    assert "a hiring manager at a bank" in prompt and "tough" in prompt.lower()
    assert "Never break character" in prompt and "1 to 3 sentences" in prompt


def test_practising_with_someone_uses_what_they_said(env):
    person = env.store.get_or_create_person("Professor Rivera")
    env.store.create_recording("c", "Chat", None, "en", "c.m4a")
    env.store.update_recording("c", person_id=person)
    env.store.queue_notes("c")
    env.store.save_notes("c", notes_from_writer({"about_them": [{"text": "Recommends Power Automate.", "cites": []}]}),
                         writer="w", checker=None, check_detail=None)
    session = env.manager.start("person", person_id=person)
    assert session["persona"] == "Professor Rivera (practice)"
    assert "Recommends Power Automate." in env.manager._sessions[session["id"]].system
    assert "not the real person" in env.manager._sessions[session["id"]].system


# --- a session

def test_turns_then_finish_into_a_coached_recording(env):
    session = env.manager.start("coffee_chat", role="a product manager at a fintech")
    assert session["opener"] is None and session["time_limit_sec"] is None
    first = env.manager.turn(session["id"], webm(env.tmp), ".webm", start_sec=1.0)
    assert first == {"you": "I'd love to hear how you got started.", "reply": "I started as an engineer, actually.",
                     "done": False}
    assert "a product manager at a fintech" in env.llm.calls[0]["system"]
    assert "You: I'd love to hear how you got started." in env.llm.calls[0]["user"]
    env.manager.turn(session["id"], webm(env.tmp), ".webm", start_sec=6.0)
    assert "Them: I started as an engineer, actually." in env.llm.calls[1]["user"]

    rid = env.manager.finish(session["id"], webm(env.tmp, 9.0, "whole"), ".webm")
    rec = env.store.get_recording(rid)
    assert rec.title.startswith("Practice: Coffee chat") and rec.notes_style == "conversation"
    assert (env.tmp / "recordings" / rid / "audio.webm").exists()  # your voice, compressed
    segments = env.store.get_transcript(rid).segments
    assert [(s.speaker, s.start) for s in segments] == [(ME, 1.0), (THEM, 4.0), (ME, 6.0), (THEM, 8.5)]
    assert env.store.get_speaker_names(rid) == {ME: "You", THEM: "Product manager at a fintech (practice)"}
    assert "practice coffee chat" in env.store.notes_instructions(rid)[1].lower()
    assert env.store.get_notes(rid).status == "queued"
    assert not (env.tmp / "practice" / session["id"]).exists()


def test_interviewer_opens_and_pen_has_a_timer(env):
    interview = env.manager.start("interview")
    assert interview["opener"] and interview["persona"].endswith("(practice)")
    assert env.manager.start("sell_pen")["time_limit_sec"] == 60


def test_silence_is_not_sent_to_the_ai(env):
    env.transcriber.behaviors = [[]]
    session = env.manager.start("coffee_chat")
    assert env.manager.turn(session["id"], webm(env.tmp), ".webm", start_sec=0.0) == {
        "you": "", "reply": None, "done": False}
    assert env.llm.calls == []


def test_discard_and_unknown_session(env):
    session = env.manager.start("elevator")
    env.manager.discard(session["id"])
    with pytest.raises(KeyError):
        env.manager.turn(session["id"], b"", ".webm", start_sec=0.0)


# --- clarity and pace

def test_stats_include_pace_and_unclear_moments():
    segments = [Segment(0, 30, " ".join(["word"] * 75), -0.2, 0.01, 1.0, ME),
                Segment(30, 40, "Mumbled bit here", -1.2, 0.01, 1.0, ME),
                Segment(40, 70, "Their answer.", None, None, None, THEM)]
    stats = conversation_stats(segments)
    assert stats["wpm"] == pytest.approx(78 / (40 / 60), abs=1)
    assert stats["unclear"] == [{"start": 30.0, "text": "Mumbled bit here"}]


# --- through the API

def test_practice_api(tmp_path):
    transcriber = FakeTranscriber([[Segment(0.0, 2.0, "Hello, I'm Sam.", -0.2, 0.01, 1.0)]])
    practice_llm = FakeLLM("groq:gpt-oss-20b", [{"reply": "Nice to meet you.", "done": False}])
    app = build(tmp_path, transcriber, FakeLLM("groq:x", []), None, practice_llm=practice_llm)
    with TestClient(app) as client:
        assert {s["id"] for s in client.get("/api/practice/scenarios").json()} >= {"interview", "sell_pen"}
        session = client.post("/api/practice/sessions", json={"scenario": "elevator"}).json()
        turn = client.post(f"/api/practice/sessions/{session['id']}/turn",
                           files={"audio": ("t.webm", webm(tmp_path), "audio/webm")}, data={"start_sec": "0.5"}).json()
        assert turn["reply"] == "Nice to meet you."
        done = client.post(f"/api/practice/sessions/{session['id']}/finish",
                           files={"audio": ("s.webm", webm(tmp_path, 4.0, "s"), "audio/webm")}).json()
        rec = client.get(f"/api/recordings/{done['id']}").json()
        assert rec["has_audio"] and rec["effective_notes_style"] == "conversation"
        played = client.get(f"/api/recordings/{done['id']}/audio")
        assert played.status_code == 200 and played.headers["content-type"].startswith("audio/webm")


def test_practice_partners_dont_become_people(env):
    from transcriber.notes.pipeline import NotesPipeline
    session = env.manager.start("coffee_chat", role="a product manager at a fintech")
    env.manager.turn(session["id"], webm(env.tmp), ".webm", start_sec=1.0)
    rid = env.manager.finish(session["id"], None, ".webm")
    part = {"about_them": [], "intro": [], "my_questions": [], "missed_followups": [], "action_items": []}
    final = {"summary": [], "feedback": [], "follow_up_draft": "", "topics": []}
    NotesPipeline(env.store, FakeLLM(replies=[part, final])).process(rid)
    assert env.store.get_notes(rid).status == "ready"
    assert env.store.get_recording(rid).person_id is None and env.store.list_people() == []
