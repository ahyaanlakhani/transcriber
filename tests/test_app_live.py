"""Live recording API."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from .helpers import FakeLLM, FakeTranscriber
from .test_app import CHECK_REPLY, NOTES_REPLY, build
from .test_live_recording import SPEECH, pieces, webm_bytes


@pytest.fixture
def env(tmp_path):
    app = build(tmp_path, FakeTranscriber(), FakeLLM("groq:gpt-oss", [NOTES_REPLY] * 5),
                FakeLLM("gemini:x", [CHECK_REPLY] * 5))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, live=app.state.live, store=app.state.store, tmp=tmp_path)


def test_record_live_then_finish(env):
    sid = env.client.post("/api/live", json={"title": "Lecture 6", "subject": "Physics", "language": "en"}).json()["id"]
    for seq, piece in enumerate(pieces(webm_bytes(env.tmp, SPEECH))):
        assert env.client.put(f"/api/live/{sid}/chunks/{seq}", content=piece).status_code == 200
    env.live.wait_for_audio(sid)
    while env.live.step():
        pass
    body = env.client.get(f"/api/live/{sid}?after=0").json()
    assert body["status"] == "recording" and len(body["segments"]) == 2 and body["duration_sec"] > 20

    rid = env.client.post(f"/api/live/{sid}/finish").json()["recording_id"]
    rec = env.client.get(f"/api/recordings/{rid}").json()
    assert (rec["title"], rec["subject"], rec["status"]) == ("Lecture 6", "Physics", "done")
    assert len(rec["paragraphs"]) >= 2
    assert env.client.get(f"/api/live/{sid}").json() == {"status": "done", "recording_id": rid, "segments": [],
                                                          "next": 0}


def test_unknown_session_and_bad_language(env):
    assert env.client.get("/api/live/nope").status_code == 404
    assert env.client.put("/api/live/nope/chunks/0", content=b"x").status_code == 404
    assert env.client.post("/api/live", json={"language": "xx"}).status_code == 400


def test_finish_with_nothing_recorded(env):
    sid = env.client.post("/api/live", json={}).json()["id"]
    assert env.client.post(f"/api/live/{sid}/finish").status_code == 400


def test_discard(env):
    sid = env.client.post("/api/live", json={}).json()["id"]
    env.client.put(f"/api/live/{sid}/chunks/0", content=webm_bytes(env.tmp, [("tone", 1.0)]))
    assert env.client.delete(f"/api/live/{sid}").status_code == 204
    assert env.store.list_recordings() == []


def test_leftover_session_becomes_a_recording_at_startup(tmp_path):
    first = build(tmp_path, FakeTranscriber())
    with TestClient(first) as client:
        sid = client.post("/api/live", json={"title": "Cut short"}).json()["id"]
        client.put(f"/api/live/{sid}/chunks/0", content=webm_bytes(tmp_path, [("tone", 2.0)]))
        first.state.live.wait_for_audio(sid)
    second = build(tmp_path, FakeTranscriber())
    with TestClient(second) as client:
        [rec] = client.get("/api/recordings").json()
        assert (rec["title"], rec["status"]) == ("Cut short", "queued")
