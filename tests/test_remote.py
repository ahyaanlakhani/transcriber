import json

import httpx
import pytest

from transcriber.providers.base import PermanentError, Segment, TransientError
from transcriber.providers.remote import RemoteServer, segments_from

URL = "http://192.168.1.20:8765"


def server(handler):
    return RemoteServer(URL, "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_health_sends_token_and_returns_body():
    def handler(request):
        assert request.url.path == "/health"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"ok": True, "model": "large-v3", "speakers": True})

    assert server(handler).health() == {"ok": True, "model": "large-v3", "speakers": True}


@pytest.mark.parametrize("response", [httpx.Response(500), httpx.Response(401), httpx.Response(200, text="nope")])
def test_health_is_none_when_unusable(response):
    assert server(lambda request: response).health() is None


def test_health_is_none_when_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    assert server(handler).health() is None


def test_health_error_explains_rejected_token():
    remote = server(lambda request: httpx.Response(403))
    assert remote.health() is None
    assert "token" in remote.last_error


def test_submit_uploads_file_and_fields(tmp_path):
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"fLaC")

    def handler(request):
        assert request.method == "POST" and request.url.path == "/jobs"
        body = request.read()
        assert b"fLaC" in body and b'name="speakers"\r\n\r\n1' in body and b'name="language"\r\n\r\nen' in body
        return httpx.Response(200, json={"id": "job1"})

    assert server(handler).submit(audio, language="en", prompt="Zorvath", speakers=True) == "job1"


def test_poll_and_delete():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"status": "running", "progress": 0.4, "stage": "transcribing"})
        return httpx.Response(204)

    remote = server(handler)
    assert remote.poll("job1")["progress"] == 0.4
    remote.delete("job1")
    assert calls == [("GET", "/jobs/job1"), ("DELETE", "/jobs/job1")]


def test_error_mapping():
    with pytest.raises(PermanentError, match="token"):
        server(lambda r: httpx.Response(401)).poll("x")
    with pytest.raises(PermanentError):
        server(lambda r: httpx.Response(404, json={"detail": "Unknown job"})).poll("x")
    with pytest.raises(TransientError):
        server(lambda r: httpx.Response(503)).poll("x")

    def unreachable(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(TransientError):
        server(unreachable).poll("x")


def test_segments_from_job():
    job = {"segments": [{"start": 0, "end": 1.5, "text": " Hi. ", "speaker": "SPEAKER_00", "avg_logprob": -0.2,
                         "no_speech_prob": None, "compression_ratio": None, "extra": 1}]}
    assert segments_from(job) == [Segment(0.0, 1.5, "Hi.", -0.2, None, None, "SPEAKER_00")]


def test_name_includes_model():
    remote = server(lambda r: httpx.Response(200, json={"ok": True, "model": "large-v3"}))
    remote.health()
    assert remote.name == "server/large-v3"
    assert json.dumps(remote.name)
