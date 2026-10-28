from types import SimpleNamespace

import httpx2
import openai
import pytest
from google.genai import errors

from transcriber.llm.base import parse_json_object
from transcriber.llm.factory import make_llm
from transcriber.llm.gemini import GeminiClient
from transcriber.llm.gemini import map_error as map_gemini_error
from transcriber.llm.openai_compat import OpenAICompatClient
from transcriber.llm.openai_compat import map_error as map_openai_error
from transcriber.providers.base import PermanentError, RateLimited, TransientError

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
          "additionalProperties": False}
REQUEST = httpx2.Request("POST", "https://api.cerebras.ai/v1/chat/completions")


def openai_status_error(cls, code, headers=None):
    return cls("boom", response=httpx2.Response(code, headers=headers or {}, request=REQUEST), body=None)


class FakeOpenAI:
    def __init__(self, content='{"ok": true}', finish_reason="stop", error=None):
        self.kwargs = None
        self._content, self._finish, self._error = content, finish_reason, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if self._error:
            raise self._error
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=self._finish)])


class FakeGenai:
    def __init__(self, text='{"ok": true}', error=None):
        self.kwargs = None
        self._text, self._error = text, error
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.kwargs = kwargs
        if self._error:
            raise self._error
        return SimpleNamespace(text=self._text)


def ask(client):
    return client.complete_json(system="SYS", user="USER", schema=SCHEMA, schema_name="check")


def test_parse_json_object_strips_code_fences():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


@pytest.mark.parametrize("text", ["not json", "[1, 2]"])
def test_parse_json_object_rejects_garbage_as_transient(text):
    with pytest.raises(TransientError):
        parse_json_object(text)


def test_openai_compat_request_shape():
    fake = FakeOpenAI()
    client = OpenAICompatClient("cerebras", "gpt-oss-120b", "key", client=fake)
    assert ask(client) == {"ok": True}
    assert client.name == "cerebras:gpt-oss-120b"
    assert fake.kwargs["model"] == "gpt-oss-120b"
    assert fake.kwargs["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert fake.kwargs["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "check", "strict": True, "schema": SCHEMA}}


def test_openai_compat_truncated_output_is_permanent():
    with pytest.raises(PermanentError, match="ran out of output"):
        ask(OpenAICompatClient("cerebras", "m", "k", client=FakeOpenAI(finish_reason="length")))


def test_openai_compat_maps_errors():
    limited = map_openai_error(openai_status_error(openai.RateLimitError, 429, {"retry-after": "7"}), "cerebras")
    assert isinstance(limited, RateLimited) and limited.retry_after == 7.0
    auth = map_openai_error(openai_status_error(openai.AuthenticationError, 401), "cerebras")
    assert isinstance(auth, PermanentError) and "Invalid Cerebras API key" in str(auth)
    assert isinstance(map_openai_error(openai_status_error(openai.InternalServerError, 503), "cerebras"),
                      TransientError)
    assert isinstance(map_openai_error(openai.APIConnectionError(request=REQUEST), "cerebras"), TransientError)
    assert isinstance(map_openai_error(openai_status_error(openai.BadRequestError, 400), "cerebras"),
                      PermanentError)


def test_openai_compat_raises_mapped_error():
    fake = FakeOpenAI(error=openai_status_error(openai.RateLimitError, 429))
    with pytest.raises(RateLimited):
        ask(OpenAICompatClient("cerebras", "m", "k", client=fake))


def test_gemini_request_shape():
    fake = FakeGenai()
    client = GeminiClient("gemini-3.8-flash", "key", client=fake)
    assert ask(client) == {"ok": True}
    assert client.name == "gemini:gemini-3.8-flash"
    assert fake.kwargs["model"] == "gemini-3.8-flash"
    assert fake.kwargs["contents"] == "USER"
    config = fake.kwargs["config"]
    assert config.system_instruction == "SYS"
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == SCHEMA


def gemini_error(code, message, details=None):
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": message, "status": "X", "details": details or []}})


def test_gemini_maps_errors():
    limited = map_gemini_error(gemini_error(429, "quota", [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"}]))
    assert isinstance(limited, RateLimited) and limited.retry_after == 34.0
    assert map_gemini_error(gemini_error(429, "quota")).retry_after == 60.0
    bad_key = map_gemini_error(gemini_error(400, "API key not valid. Please pass a valid API key."))
    assert isinstance(bad_key, PermanentError) and "Invalid Gemini API key" in str(bad_key)
    assert isinstance(map_gemini_error(gemini_error(503, "overloaded")), TransientError)
    assert isinstance(map_gemini_error(gemini_error(400, "schema too deep")), PermanentError)


def test_gemini_network_error_is_transient():
    fake = FakeGenai(error=httpx2.ConnectError("offline"))
    with pytest.raises(TransientError, match="Could not reach Gemini"):
        ask(GeminiClient("m", "k", client=fake))


def test_make_llm():
    keys = {"cerebras": "c", "gemini": "g"}
    assert make_llm("cerebras:gpt-oss-120b", keys).name == "cerebras:gpt-oss-120b"
    assert make_llm("gemini:gemini-3.8-flash", keys).name == "gemini:gemini-3.8-flash"
    assert make_llm("groq:llama", keys) is None          # no key -> not configured
    assert make_llm("none", keys) is None
    assert make_llm("", keys) is None
    with pytest.raises(ValueError):
        make_llm("nonsense", keys)
    with pytest.raises(ValueError):
        make_llm("acme:model", keys)


def test_groq_free_tier_uses_small_parts_and_capped_output():
    fake = FakeOpenAI()
    client = OpenAICompatClient("groq", "openai/gpt-oss-120b", "key", client=fake)
    ask(client)
    assert client.max_input_chars == 14_000 and fake.kwargs["max_completion_tokens"] == 3_500
    assert OpenAICompatClient("cerebras", "m", "k", client=FakeOpenAI()).max_input_chars is None


def test_low_effort_is_sent_to_gpt_oss_reasoning_models():
    fake = FakeOpenAI()
    client = OpenAICompatClient("groq", "openai/gpt-oss-120b", "key", client=fake)
    client.complete_json(system="SYS", user="USER", schema=SCHEMA, schema_name="check", effort="low")
    assert fake.kwargs["reasoning_effort"] == "low"
    client.complete_json(system="SYS", user="USER", schema=SCHEMA, schema_name="check")
    assert "reasoning_effort" not in fake.kwargs  # default effort unless asked


def test_effort_is_not_sent_to_models_without_it():
    fake = FakeOpenAI()
    OpenAICompatClient("groq", "llama-3.3-70b-versatile", "key", client=fake).complete_json(
        system="SYS", user="USER", schema=SCHEMA, schema_name="check", effort="low")
    assert "reasoning_effort" not in fake.kwargs


def test_other_clients_accept_and_ignore_effort():
    from transcriber.llm.gemini import GeminiClient
    assert GeminiClient("gemini-x", "key", client=FakeGenai()).complete_json(
        system="SYS", user="USER", schema=SCHEMA, schema_name="check", effort="low") == {"ok": True}


def json_validate_error(failed_generation):
    body = {"message": "Generated JSON does not match the expected schema.", "type": "invalid_request_error",
            "code": "json_validate_failed", "failed_generation": failed_generation}
    return openai.BadRequestError("Error code: 400", response=httpx2.Response(400, request=REQUEST), body=body)


def test_reply_that_only_breaks_the_schema_is_kept():
    # gpt-oss wrote "kind": "action" where only point/definition/... are allowed; the notes themselves were fine,
    # and the notes parser tidies unknown values itself
    fake = FakeOpenAI(error=json_validate_error('{"ok": true, "lecture": [{"kind": "action"}]}'))
    assert ask(OpenAICompatClient("groq", "openai/gpt-oss-120b", "k", client=fake)) == {
        "ok": True, "lecture": [{"kind": "action"}]}


def test_empty_or_broken_rejected_reply_is_still_an_error():
    for generation in ("", '{"ok": tru'):
        fake = FakeOpenAI(error=json_validate_error(generation))
        with pytest.raises(PermanentError, match="rejected"):
            ask(OpenAICompatClient("groq", "openai/gpt-oss-120b", "k", client=fake))
