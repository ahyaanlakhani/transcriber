"""The user's own transcription server (server/), with the models faked."""
import pytest
from fastapi.testclient import TestClient

from server.assign import assign_speakers, speaker_for
from server.transcribe_server import create_server

AUTH = {"Authorization": "Bearer s3cret"}
TURNS = [(0.0, 3.0, "SPEAKER_00"), (3.2, 6.0, "SPEAKER_01")]


def words(*items):
    return [{"start": s, "end": e, "text": t} for s, e, t in items]


def test_speaker_by_most_overlap_then_nearest():
    assert speaker_for(2.5, 3.4, TURNS) == "SPEAKER_00"  # 0.5 s vs 0.2 s
    assert speaker_for(3.12, 3.18, TURNS) == "SPEAKER_01"  # in the gap, nearer the second turn
    assert speaker_for(1.0, 2.0, []) is None


def test_groups_words_by_speaker_and_pause():
    result = assign_speakers(words((0.0, 0.5, " Welcome"), (0.5, 1.0, " back."), (3.3, 3.8, " Thanks."),
                                   (5.0, 5.4, " Sure.")), TURNS)
    assert [(s["speaker"], s["text"]) for s in result] == [
        ("SPEAKER_00", "Welcome back."), ("SPEAKER_01", "Thanks."), ("SPEAKER_01", "Sure.")]
    assert (result[0]["start"], result[0]["end"]) == (0.0, 1.0)


def test_splits_long_runs():
    long_words = words(*[(i * 0.5, i * 0.5 + 0.4, f" w{i}") for i in range(80)])
    result = assign_speakers(long_words, [(0.0, 100.0, "SPEAKER_00")], max_length=10.0)
    assert len(result) == 4 and all(s["end"] - s["start"] <= 10.0 for s in result)


class FakeEngine:
    model, device = "large-v3", "cpu"

    def __init__(self, speakers_available=True, fail=False):
        self.speakers_available = speakers_available
        self.fail = fail
        self.calls = []

    def transcribe(self, path, *, language, prompt, speakers, progress):
        self.calls.append({"bytes": path.read_bytes(), "language": language, "prompt": prompt, "speakers": speakers})
        progress("transcribing", 0.5)
        if self.fail:
            raise RuntimeError("CUDA out of memory")
        return [{"start": 0.0, "end": 1.0, "text": "Hi.", "speaker": "SPEAKER_00" if speakers else None}]


def client_for(tmp_path, engine):
    app = create_server("s3cret", engine, tmp_path, start_worker=False)
    return app, TestClient(app)


def test_requires_token(tmp_path):
    _, client = client_for(tmp_path, FakeEngine())
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_health(tmp_path):
    _, client = client_for(tmp_path, FakeEngine(speakers_available=False))
    assert client.get("/health", headers=AUTH).json() == {"ok": True, "model": "large-v3", "device": "cpu",
                                                         "speakers": False, "busy": False}


def test_job_lifecycle(tmp_path):
    engine = FakeEngine()
    app, client = client_for(tmp_path, engine)
    job_id = client.post("/jobs", headers=AUTH, files={"file": ("audio.flac", b"fLaC", "audio/flac")},
                         data={"language": "en", "prompt": "Zorvath", "speakers": "1"}).json()["id"]
    assert client.get(f"/jobs/{job_id}", headers=AUTH).json()["status"] == "queued"
    assert client.get("/health", headers=AUTH).json()["busy"] is True
    app.state.run_next()
    job = client.get(f"/jobs/{job_id}", headers=AUTH).json()
    assert job["status"] == "done" and job["segments"][0]["speaker"] == "SPEAKER_00"
    assert engine.calls == [{"bytes": b"fLaC", "language": "en", "prompt": "Zorvath", "speakers": True}]
    assert not (tmp_path / "jobs" / job_id).exists()  # upload removed after the work
    assert client.delete(f"/jobs/{job_id}", headers=AUTH).status_code == 204
    assert client.get(f"/jobs/{job_id}", headers=AUTH).status_code == 404


def test_speakers_skipped_when_unavailable_and_failures_reported(tmp_path):
    engine = FakeEngine(speakers_available=False, fail=True)
    app, client = client_for(tmp_path, engine)
    job_id = client.post("/jobs", headers=AUTH, files={"file": ("a.flac", b"x")}, data={"speakers": "1"}).json()["id"]
    app.state.run_next()
    job = client.get(f"/jobs/{job_id}", headers=AUTH).json()
    assert engine.calls[0]["speakers"] is False and engine.calls[0]["language"] is None
    assert job["status"] == "failed" and "CUDA out of memory" in job["error"]


def test_refuses_to_start_without_token(tmp_path):
    with pytest.raises(SystemExit):
        create_server("", FakeEngine(), tmp_path, start_worker=False)
