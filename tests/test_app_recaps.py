from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from transcriber.app import recap_filename

from .helpers import FakeLLM, FakeTranscriber, synth_audio
from .test_app import CHECK_REPLY, build
from .test_recap_pipeline import NOTES, SCRIPT, FakeVoice


@pytest.fixture
def env(tmp_path):
    voice = FakeVoice()
    writer = FakeLLM("groq:gpt-oss", [NOTES, SCRIPT] * 5)
    config_app = build(tmp_path, FakeTranscriber(), writer, FakeLLM("gemini:x", [CHECK_REPLY] * 10), voice=voice)
    with TestClient(config_app) as client:
        yield SimpleNamespace(client=client, store=config_app.state.store, worker=config_app.state.worker,
                              dir=config_app.state.recordings_dir, voice=voice, tmp=tmp_path)


def add_lecture(env, rid="a", subject="Machine Learning", seconds=200.0, when="2026-10-07T05:02:16+00:00"):
    store = env.store
    store.create_recording(rid, f"Lecture {rid}", store.get_or_create_subject(subject) if subject else None, "en",
                           f"{rid}.m4a")
    (env.dir / rid).mkdir(parents=True)
    synth_audio(env.dir / rid / "audio.flac", [("tone", 1.0)])
    from transcriber.providers.base import Segment
    store.save_transcript(rid, "Hi", [Segment(0.0, 1.0, "Hi", -0.1, 0.01, 1.0)], "groq")
    store.update_recording(rid, status="done", duration_sec=seconds, recorded_at=when)
    store.queue_notes(rid)
    while env.worker.run_once():
        pass


def test_recap_made_after_notes_and_served(env):
    add_lecture(env)
    body = env.client.get("/api/recordings/a").json()
    assert body["recap"]["status"] == "ready" and body["recap"]["duration_sec"] == 301.5
    mp3 = env.client.get("/api/recordings/a/recap.mp3", headers={"Range": "bytes=0-1"})
    assert mp3.status_code == 206 and mp3.content == b"mp"


def test_remake(env):
    add_lecture(env)
    assert env.client.post("/api/recordings/a/recap").json()["status"] == "queued"
    env.worker.run_once()
    assert len(env.voice.calls) == 2


def test_remake_needs_notes(env):
    env.store.create_recording("x", "X", None, "en", "x.m4a")
    assert env.client.post("/api/recordings/x/recap").status_code == 400


def test_latest_per_subject_with_phone_filenames(env):
    add_lecture(env, "a", when="2026-10-05T05:00:00+00:00")
    add_lecture(env, "b", when="2026-10-07T05:02:16+00:00")
    add_lecture(env, "c", subject=None)
    latest = env.client.get("/api/recaps/latest").json()
    assert [item["url"] for item in latest] == ["/api/recordings/b/recap.mp3"]
    assert latest[0]["subject"] == "Machine Learning" and latest[0]["filename"].endswith(".mp3")


def test_recap_filename():
    assert recap_filename('Law: "Torts"', "2026-10-07T05:02:16+00:00", tz="Asia/Tashkent") == "Law Torts – Wed 7 Oct.mp3"


def test_recap_settings(env):
    settings = env.client.put("/api/settings", json={"recaps_on": False, "recap_voice": "bm_george",
                                                      "recap_speed": 1.2}).json()
    assert (settings["recaps_on"], settings["recap_voice"], settings["recap_speed"]) == ("0", "bm_george", "1.2")
    assert "af_heart" in settings["recap_voices"]
    assert env.client.put("/api/settings", json={"recap_speed": 2.0}).status_code == 400
    assert env.client.put("/api/settings", json={"recap_voice": "nobody"}).status_code == 400
