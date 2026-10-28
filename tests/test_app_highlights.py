"""Highlights reel through the API."""
from transcriber.notes.model import notes_from_writer
from transcriber.pipeline import AUDIO_FILE
from transcriber.providers.base import Segment

from .helpers import synth_audio
from .test_app import env  # noqa: F401  (fixture)


def add_lecture(env, notes=True):
    store = env.store
    store.create_recording("h", "Machine Learning", None, "en", "h.m4a")
    folder = env.data_dir / "recordings" / "h"
    folder.mkdir(parents=True)
    synth_audio(folder / AUDIO_FILE, [("tone", 40.0)])
    store.save_transcript("h", "x", [Segment(t, t + 5, "words", -0.1, 0.01, 1.0) for t in range(0, 40, 5)], "groq")
    store.update_recording("h", duration_sec=40.0)
    if notes:
        store.queue_notes("h")
        store.save_notes("h", notes_from_writer({"lecture": [
            {"heading": "Robots", "kind": "stressed", "term": None, "text": "On the exam.", "cites": ["[00:10]"]}]}),
            writer="w", checker=None, check_detail=None)


def test_make_and_play_highlights(env):
    add_lecture(env)
    body = env.client.post("/api/recordings/h/highlights").json()
    assert body["clips"] == [{"start": 10.0, "end": 30.0, "label": "On the exam."}]
    assert 19 < body["duration_sec"] < 21
    assert env.client.get("/api/recordings/h").json()["highlights"]["clips"][0]["label"] == "On the exam."
    mp3 = env.client.get("/api/recordings/h/highlights.mp3")
    assert mp3.status_code == 200 and mp3.headers["content-type"] == "audio/mpeg"


def test_highlights_need_notes_with_moments(env):
    add_lecture(env, notes=False)
    assert env.client.post("/api/recordings/h/highlights").status_code == 400
    assert env.client.get("/api/recordings/h/highlights.mp3").status_code == 404
