"""API for the user's server, speaker labels and Groq usage."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from transcriber.app import create_app
from transcriber.config import Config, load_config
from transcriber.export import to_md, to_txt
from transcriber.notes.transcript import render
from transcriber.postprocess import Paragraph

from .helpers import FakeLLM, FakeTranscriber, synth_audio
from .test_app import CHECK_REPLY, NOTES_REPLY
from .test_pipeline_remote import FakeRemote


@pytest.fixture
def wav(tmp_path):
    return synth_audio(tmp_path / "lecture.wav", [("tone", 3.0)]).read_bytes()


def make_env(tmp_path, remote):
    config = Config(groq_api_key=None, data_dir=tmp_path / "data", port=0)
    writer = FakeLLM("groq:gpt-oss", [NOTES_REPLY] * 10)
    app = create_app(config, transcriber=FakeTranscriber(), writer=writer,
                     checker=FakeLLM("gemini:x", [CHECK_REPLY] * 10), ask_engine=None, remote=remote,
                     start_worker=False, privacy_allow_all=True)
    return app, writer


@pytest.fixture
def env(tmp_path):
    remote = FakeRemote()
    app, writer = make_env(tmp_path, remote)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, remote=remote, writer=writer, worker=app.state.worker,
                              store=app.state.store)


def upload(env, wav, **data):
    response = env.client.post("/api/recordings", files=[("files", ("lecture.wav", wav, "audio/wav"))], data=data)
    assert response.status_code == 201, response.text
    return response.json()[0]


def drain(env):
    while env.worker.run_once():
        pass


def test_config_reads_server_settings(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TRANSCRIBE_SERVER_URL=http://10.0.0.5:8765/\nTRANSCRIBE_SERVER_TOKEN=abc\n",
                        encoding="utf-8")
    config = load_config(env_file, environ={})
    assert (config.server_url, config.server_token) == ("http://10.0.0.5:8765/", "abc")
    assert load_config(tmp_path / "none.env", environ={}).server_url is None


def test_server_status(env):
    body = env.client.get("/api/server").json()
    assert body == {"configured": True, "reachable": True, "speakers": True, "model": "large-v3",
                    "device": None, "error": None}


def test_server_status_when_not_configured(tmp_path):
    app, _ = make_env(tmp_path, None)
    with TestClient(app) as client:
        assert client.get("/api/server").json()["configured"] is False


def test_speakers_upload_names_and_rename(env, wav):
    rec = upload(env, wav, speakers="1")
    assert rec["want_speakers"] is True
    drain(env)
    body = env.client.get(f"/api/recordings/{rec['id']}").json()
    assert body["transcribed_by"] == "server/large-v3"
    assert body["speakers"] == [{"label": "SPEAKER_00", "name": "Speaker 1"},
                                {"label": "SPEAKER_01", "name": "Speaker 2"}]
    assert [(p["speaker"], p["speaker_label"]) for p in body["paragraphs"]] == [
        ("Speaker 1", "SPEAKER_00"), ("Speaker 2", "SPEAKER_01")]

    response = env.client.put(f"/api/recordings/{rec['id']}/speakers", json={"names": {"SPEAKER_00": " Dr. Khan "}})
    assert response.json() == [{"label": "SPEAKER_00", "name": "Dr. Khan"}, {"label": "SPEAKER_01", "name": "Speaker 2"}]
    assert env.client.put(f"/api/recordings/{rec['id']}/speakers",
                          json={"names": {"SPEAKER_09": "Nobody"}}).status_code == 400
    # an empty name goes back to the default
    names = env.client.put(f"/api/recordings/{rec['id']}/speakers", json={"names": {"SPEAKER_00": ""}}).json()
    assert names[0]["name"] == "Speaker 1"

    env.client.put(f"/api/recordings/{rec['id']}/speakers", json={"names": {"SPEAKER_00": "Dr. Khan"}})
    text = env.client.get(f"/api/recordings/{rec['id']}/export?format=txt").text
    assert "Dr. Khan: Welcome back." in text


def test_notes_writer_sees_speaker_names(env, wav):
    rec = upload(env, wav, speakers="1")
    env.store.set_speaker_names(rec["id"], {"SPEAKER_00": "Dr. Khan"})
    drain(env)
    prompt = env.writer.calls[0]["user"]
    assert "Dr. Khan: Welcome back." in prompt and "Speaker 2: Thanks." in prompt


def test_retranscribe_can_turn_speakers_on(env, wav):
    rec = upload(env, wav)
    drain(env)
    assert env.client.get(f"/api/recordings/{rec['id']}").json()["transcribed_by"] == "fake/model"
    response = env.client.post(f"/api/recordings/{rec['id']}/retranscribe", json={"speakers": True})
    assert response.json()["want_speakers"] is True


def test_usage_and_limits(env, wav):
    env.store.record_usage(1800.0, "groq")
    env.store.record_usage(600.0, "groq", at="2026-01-01T00:00:00+00:00")
    body = env.client.get("/api/usage").json()
    assert {k: body[k] for k in ("hour_sec", "day_sec", "week_sec", "month_sec", "hour_limit_sec", "day_limit_sec")} == {
        "hour_sec": 1800.0, "day_sec": 1800.0, "week_sec": 1800.0, "month_sec": 1800.0,
        "hour_limit_sec": 7200.0, "day_limit_sec": 28800.0}
    assert body["day"]["used_sec"] == pytest.approx(1800, abs=5) and body["day"]["full_at"]  # back in about 1.5 h
    response = env.client.put("/api/settings", json={"groq_day_limit_sec": 36000, "prefer_server": True})
    assert response.json()["groq_day_limit_sec"] == "36000" and response.json()["prefer_server"] == "1"
    assert env.client.get("/api/usage").json()["day_limit_sec"] == 36000.0
    assert env.client.put("/api/settings", json={"groq_hour_limit_sec": 0}).status_code == 400


def test_renderers_include_speaker_names():
    paragraphs = [Paragraph(0, "Welcome back.", "Dr. Khan"), Paragraph(5, "Thanks.", None)]
    assert render(paragraphs, False) == "[00:00] Dr. Khan: Welcome back.\n\n[00:05] Thanks."
    assert to_txt("T", paragraphs) == "T\n\nDr. Khan: Welcome back.\n\nThanks.\n"
    assert "[00:00] **Dr. Khan:** Welcome back." in to_md("T", "2026-10-05", None, 10, paragraphs)
