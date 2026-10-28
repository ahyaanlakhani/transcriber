"""Working without the cloud: a local transcriber for Local-only recordings, and a safe backup of the whole library."""
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from transcriber import privacy
from transcriber.app import create_app
from transcriber.backup import backup_library
from transcriber.config import Config
from transcriber.privacy import Gateway
from transcriber.providers.base import PermanentError, Segment
from transcriber.providers.local import NEEDS_LIBRARY, LocalWhisperTranscriber, model_size
from transcriber.store import Store

from .helpers import FakeTranscriber, synth_audio


class FakeModel:
    loads = 0

    def __init__(self, size, device, compute_type):
        FakeModel.loads += 1
        self.args = (size, device, compute_type)

    def transcribe(self, path, **kwargs):
        self.kwargs = kwargs
        pieces = [SimpleNamespace(start=0.0, end=1.5, text=" Hello there. ", avg_logprob=-0.2, no_speech_prob=0.01, compression_ratio=1.1)]
        return iter(pieces), SimpleNamespace(language="en")


# ---- the local transcriber

def test_the_local_model_loads_once_and_returns_segments():
    FakeModel.loads = 0
    local = LocalWhisperTranscriber("small", model_factory=FakeModel)
    first = local.transcribe(Path("a.flac"), language="en", prompt="Quillbrook")
    local.transcribe(Path("b.flac"), language=None, prompt="")
    assert FakeModel.loads == 1  # loaded on first use, kept after
    assert first == [Segment(0.0, 1.5, "Hello there.", -0.2, 0.01, 1.1)]
    assert local.name == "local/faster-whisper-small" and model_size("faster-whisper:base.en") == "base.en"


def test_a_missing_library_or_model_gives_a_plain_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # as if it were not installed
    with pytest.raises(PermanentError) as err:
        LocalWhisperTranscriber("small").transcribe(Path("a.flac"), language="en", prompt="")
    assert str(err.value) == NEEDS_LIBRARY

    def broken(*args, **kwargs):
        raise OSError("disk full: /secret/path")

    with pytest.raises(PermanentError) as err:
        LocalWhisperTranscriber("small", model_factory=broken).transcribe(Path("a.flac"), language="en", prompt="")
    assert "secret" not in str(err.value) and "OSError" in str(err.value)


def test_the_gateway_never_holds_back_a_model_on_this_laptop(tmp_path):
    store = Store(tmp_path / "app.db")
    store.create_recording("x", "Secret", None, "en", "x.m4a")
    store.set_privacy("x", "local_only")
    gateway = Gateway(store)
    local = gateway.guard_transcriber(LocalWhisperTranscriber("small", model_factory=FakeModel))
    with privacy.scope("transcribe", ["x"]):
        assert local.transcribe(Path("a.flac"), language="en", prompt="")
    assert store.list_transmissions() == []  # it never left the laptop, so there is nothing to record
    cloud = gateway.guard_transcriber(SimpleNamespace(name="groq/whisper-large-v3", transcribe=lambda *a, **k: []))
    store.set_policy("groq/whisper-large-v3", standard=True, private=True, retention=privacy.RETENTION_VERIFIED)
    with privacy.scope("transcribe", ["x"]):
        with pytest.raises(privacy.PrivacyBlocked):
            cloud.transcribe(Path("a.flac"), language="en", prompt="")
    store.close()


# ---- Local-only recordings go through the local model, and nothing else

def build(tmp_path, local):
    cloud = FakeTranscriber()
    cloud.name = "groq/whisper-large-v3"
    config = Config(groq_api_key=None, data_dir=tmp_path / "data", port=0)
    app = create_app(config, transcriber=cloud, writer=None, checker=None, ask_engine=None, voice=None, practice_llm=None,
                     start_worker=False, local_transcriber=local)
    return app, cloud


def upload_local_only(client, tmp_path):
    wav = synth_audio(tmp_path / "talk.wav", [("tone", 2.0)]).read_bytes()
    rid = client.post("/api/recordings", files=[("files", ("talk.wav", wav, "audio/wav"))]).json()[0]["id"]
    assert client.put(f"/api/recordings/{rid}/privacy", json={"classification": "local_only"}).status_code == 200
    return rid


def test_a_local_only_recording_is_transcribed_on_the_laptop_and_never_sent(tmp_path):
    FakeModel.loads = 0
    app, cloud = build(tmp_path, LocalWhisperTranscriber("small", model_factory=FakeModel))
    with TestClient(app) as client:
        client.put("/api/privacy/providers", json={"config_id": "groq/whisper-large-v3", "standard": True})
        rid = upload_local_only(client, tmp_path)
        app.state.worker.run_once()
        rec = client.get(f"/api/recordings/{rid}").json()
        assert rec["status"] == "done" and rec["transcribed_by"] == "local/faster-whisper-small"
        assert cloud.calls == [] and FakeModel.loads == 1
        assert client.get("/api/privacy").json()["log"] == []  # nothing was sent, so nothing is logged as sent


def test_without_a_local_model_a_local_only_recording_waits_with_a_clear_reason(tmp_path):
    app, cloud = build(tmp_path, None)
    with TestClient(app) as client:
        client.put("/api/privacy/providers", json={"config_id": "groq/whisper-large-v3", "standard": True})
        rid = upload_local_only(client, tmp_path)
        app.state.worker.run_once()
        rec = client.get(f"/api/recordings/{rid}").json()
        assert rec["status"] == "failed" and "LOCAL_TRANSCRIBER" in rec["status_detail"]
        assert cloud.calls == []  # even though Groq is approved for everything else


# ---- the library backup

def make_library(tmp_path):
    store = Store(tmp_path / "data" / "app.db")
    recordings = tmp_path / "data" / "recordings"
    for rid in ("a", "b"):
        store.create_recording(rid, f"Rec {rid}", None, "en", f"{rid}.m4a")
        (recordings / rid).mkdir(parents=True)
        (recordings / rid / "audio.flac").write_bytes(b"audio-" + rid.encode())
        (recordings / rid / "upload.m4a").write_bytes(b"not part of the backup")
    return store, recordings


def test_a_backup_is_a_consistent_copy_with_the_audio_if_asked(tmp_path):
    store, recordings = make_library(tmp_path)
    result = backup_library(store, recordings, tmp_path / "out", include_audio=True)
    folder = Path(result["folder"])
    assert result["recordings"] == 2 and result["audio_files"] == 2 and result["schema"] >= 22
    copy = sqlite3.connect(folder / "app.db")
    assert copy.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 2
    copy.close()
    assert (folder / "recordings" / "a" / "audio.flac").read_bytes() == b"audio-a"
    assert not (folder / "recordings" / "a" / "upload.m4a").exists()
    assert json.loads((folder / "backup.json").read_text(encoding="utf-8"))["includes_audio"] is True
    assert "Close Transcriber" in (folder / "README.txt").read_text(encoding="utf-8")
    again = backup_library(store, recordings, tmp_path / "out", include_audio=False)  # never into an old backup
    assert again["folder"] != result["folder"] and again["audio_files"] == 0 and not (Path(again["folder"]) / "recordings").exists()
    store.close()


def test_the_backup_button(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    app, _ = build(tmp_path, None)
    with TestClient(app) as client:
        done = client.post("/api/backup", json={"include_audio": False}).json()
        assert Path(done["folder"]).is_dir() and "Transcriber backups" in done["folder"]
