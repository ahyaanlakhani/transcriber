"""Merge devices through the API and the worker."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from transcriber.pipeline import AUDIO_FILE
from transcriber.providers.base import Segment

from .helpers import FakeLLM, FakeTranscriber, synth_audio
from .test_app import CHECK_REPLY, NOTES_REPLY, build
from .test_merge import SENTENCES, source


@pytest.fixture
def env(tmp_path):
    writer = FakeLLM("groq:gpt-oss", [NOTES_REPLY] * 10)
    app = build(tmp_path, FakeTranscriber(), writer, FakeLLM("gemini:x", [CHECK_REPLY] * 10))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, store=app.state.store, worker=app.state.worker,
                              dir=app.state.recordings_dir, tmp=tmp_path, writer=writer)


def add_done(env, rid, title, segments, seconds):
    env.store.create_recording(rid, title, None, "en", f"{rid}.m4a")
    (env.dir / rid).mkdir(parents=True)
    synth_audio(env.dir / rid / AUDIO_FILE, [("tone", seconds)])
    env.store.update_recording(rid, duration_sec=seconds)
    env.store.save_transcript(rid, " ".join(s.text for s in segments), segments, "groq/whisper-large-v3")


def test_merge_devices_end_to_end(env):
    garbled = list(SENTENCES)
    garbled[2] = "Then we will trace it the car camera image image every single day."
    laptop = source(garbled)
    laptop[2] = Segment(12.0, 17.0, garbled[2], -1.2, 0.01, 1.2)
    add_done(env, "lap", "Lecture (laptop)", laptop, 40.0)
    add_done(env, "pho", "Lecture (phone)", source(SENTENCES, start=-4.0), 34.0)
    env.store.update_recording("pho", recorded_at="2026-10-07T05:02:16+00:00")

    rec = env.client.post("/api/recordings/merge", json={"ids": ["pho", "lap"]}).json()
    assert (rec["title"], rec["status"], rec["original_filename"]) == ("Merged: Lecture (laptop)", "queued",
                                                                      "Merged recording")
    while env.worker.run_once():
        pass
    body = env.client.get(f"/api/recordings/{rec['id']}").json()
    assert body["status"] == "done" and body["duration_sec"] == 40.0
    text = " ".join(p["text"] for p in body["paragraphs"])
    assert "factories use this data" in text and "camera image" not in text
    assert {m["id"] for m in body["merged_from"]} == {"lap", "pho"}
    assert sum(m["share"] for m in body["merged_from"]) == pytest.approx(1.0, abs=0.01)
    assert (env.dir / rec["id"] / AUDIO_FILE).exists()
    assert body["notes_status"] == "ready"
    assert body["recorded_at"] == "2026-10-07T05:02:16+00:00"  # the phone's stamp carries over
    # the originals are untouched
    assert env.client.get("/api/recordings/lap").json()["status"] == "done"


def test_merge_needs_two_to_five_finished_recordings(env):
    add_done(env, "a", "A", source(SENTENCES), 36.0)
    assert env.client.post("/api/recordings/merge", json={"ids": ["a"]}).status_code == 400
    assert env.client.post("/api/recordings/merge", json={"ids": ["a", "nope"]}).status_code == 400
    env.store.create_recording("q", "Queued", None, "en", "q.m4a")
    assert env.client.post("/api/recordings/merge", json={"ids": ["a", "q"]}).status_code == 400
    for rid in "bcdef":
        add_done(env, rid, rid, source(SENTENCES), 36.0)
    assert env.client.post("/api/recordings/merge", json={"ids": list("abcdef")}).status_code == 400


def test_unrelated_recordings_fail_with_a_clear_message(env):
    add_done(env, "a", "A", source(SENTENCES), 36.0)
    add_done(env, "b", "B", source(["Completely different words about cooking pasta at home tonight."] * 6), 36.0)
    rec = env.client.post("/api/recordings/merge", json={"ids": ["a", "b"], "title": "Oops"}).json()
    env.worker.run_once()
    failed = env.client.get(f"/api/recordings/{rec['id']}").json()
    assert failed["status"] == "failed" and "same session" in failed["status_detail"]
