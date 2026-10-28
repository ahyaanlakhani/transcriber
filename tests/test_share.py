"""Endpoints the iPhone "Send to Transcriber" shortcut uses."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from .helpers import synth_audio
from .test_app import build


@pytest.fixture
def env(tmp_path):
    app = build(tmp_path)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, store=app.state.store)


@pytest.fixture
def m4a(tmp_path):
    return synth_audio(tmp_path / "memo.wav", [("tone", 1.0)]).read_bytes()


def test_subject_names_as_plain_lines_with_no_subject_first(env):
    env.store.create_subject("Machine Learning")
    env.store.create_subject("Team standup")
    response = env.client.get("/api/share/subjects")
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "No subject\nMachine Learning\nTeam standup"


def test_share_creates_recording_and_replies_in_plain_words(env, m4a):
    env.store.create_subject("Machine Learning")
    response = env.client.post("/api/share", files={"file": ("Lecture 4.m4a", m4a, "audio/mp4")},
                               data={"subject": "Machine Learning"})
    assert response.status_code == 201
    assert response.text == "Sent “Lecture 4” to Machine Learning. The transcript and notes will follow."
    [rec] = env.store.list_recordings()
    assert (rec.title, rec.subject, rec.language, rec.status) == ("Lecture 4", "Machine Learning", "en", "queued")


def test_share_without_subject_and_with_title(env, m4a):
    response = env.client.post("/api/share", files={"file": ("New Recording 3.m4a", m4a)},
                               data={"subject": "No subject", "title": "  Call with Sara "})
    assert response.text == "Sent “Call with Sara” to Transcriber. The transcript and notes will follow."
    [rec] = env.store.list_recordings()
    assert (rec.title, rec.subject) == ("Call with Sara", None)


def test_share_rejects_missing_file(env):
    assert env.client.post("/api/share", data={"subject": "x"}).status_code == 422
