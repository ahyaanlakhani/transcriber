from types import SimpleNamespace

import groq
import httpx
import pytest
from groq.types.audio import Transcription

from transcriber.providers.base import PermanentError, RateLimited, Segment, TransientError
from transcriber.providers.groq import GroqTranscriber, map_error

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/audio/transcriptions")


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.kwargs = result, error, None
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.result


def response(*segments):
    return Transcription(text=" ".join(s["text"] for s in segments), segments=list(segments))


def status_error(cls, code, headers=None):
    return cls("boom", response=httpx.Response(code, headers=headers or {}, request=REQUEST), body=None)


@pytest.fixture
def chunk(tmp_path):
    path = tmp_path / "chunk_000.flac"
    path.write_bytes(b"fake-flac")
    return path


def test_sends_accuracy_settings(chunk):
    client = FakeClient(result=response())
    GroqTranscriber("key", client=client).transcribe(chunk, language="en", prompt="Glossary: Zorvath.")
    assert client.kwargs == {
        "model": "whisper-large-v3",
        "file": ("chunk_000.flac", b"fake-flac"),
        "response_format": "verbose_json",
        "timestamp_granularities": ["segment"],
        "temperature": 0.0,
        "language": "en",
        "prompt": "Glossary: Zorvath.",
    }


def test_omits_language_and_prompt_when_not_given(chunk):
    client = FakeClient(result=response())
    GroqTranscriber("key", client=client).transcribe(chunk, language=None, prompt="")
    assert "language" not in client.kwargs and "prompt" not in client.kwargs


def test_parses_segments(chunk):
    client = FakeClient(result=response(
        {"id": 0, "start": 0.0, "end": 2.5, "text": " Hello team.", "avg_logprob": -0.2,
         "no_speech_prob": 0.01, "compression_ratio": 1.1, "tokens": [1], "seek": 0, "temperature": 0.0},
    ))
    segments = GroqTranscriber("key", client=client).transcribe(chunk, language="en", prompt="")
    assert segments == [Segment(0.0, 2.5, "Hello team.", -0.2, 0.01, 1.1)]


def test_no_segments_returns_empty_list(chunk):
    client = FakeClient(result=Transcription(text=""))
    assert GroqTranscriber("key", client=client).transcribe(chunk, language="en", prompt="") == []


def test_errors_are_mapped(chunk):
    client = FakeClient(error=status_error(groq.RateLimitError, 429, {"retry-after": "12"}))
    with pytest.raises(RateLimited) as caught:
        GroqTranscriber("key", client=client).transcribe(chunk, language="en", prompt="")
    assert caught.value.retry_after == 12.0


def test_rate_limit_without_header_defaults_to_60_seconds():
    assert map_error(status_error(groq.RateLimitError, 429)).retry_after == 60.0


@pytest.mark.parametrize("cls, code", [(groq.AuthenticationError, 401), (groq.PermissionDeniedError, 403)])
def test_auth_errors_are_permanent(cls, code):
    error = map_error(status_error(cls, code))
    assert isinstance(error, PermanentError) and "Invalid Groq API key" in str(error)


def test_bad_request_is_permanent():
    assert isinstance(map_error(status_error(groq.BadRequestError, 400)), PermanentError)


@pytest.mark.parametrize("error", [
    status_error(groq.InternalServerError, 503),
    groq.APIConnectionError(request=REQUEST),
    groq.APITimeoutError(request=REQUEST),
])
def test_server_and_network_errors_are_transient(error):
    assert isinstance(map_error(error), TransientError)
