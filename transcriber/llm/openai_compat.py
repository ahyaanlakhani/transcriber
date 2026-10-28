"""Any OpenAI-compatible chat API: Cerebras (default notes writer), Groq, OpenRouter, OpenAI."""
import openai

from ..providers.base import PermanentError, RateLimited, TransientError, why
from .base import parse_json_object

BASE_URLS = {
    "cerebras": "https://api.cerebras.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
}
LABELS = {"cerebras": "Cerebras", "groq": "Groq", "openrouter": "OpenRouter", "openai": "OpenAI"}
MAX_OUTPUT_TOKENS = 16_000
# Free tiers with small tokens-per-minute limits: (transcript chars per request, max output tokens).
# Groq free allows ~8k tokens/minute for gpt-oss-120b, counting input and requested output.
FREE_TIER_LIMITS = {"groq": (14_000, 3_500)}
DEFAULT_RETRY_AFTER = 60.0


class OpenAICompatClient:
    def __init__(self, provider: str, model: str, api_key: str, client=None):
        self.name = f"{provider}:{model}"
        self.provider = provider
        self._model = model
        self.max_input_chars, self._max_output = FREE_TIER_LIMITS.get(provider, (None, MAX_OUTPUT_TOKENS))
        # The notes pipeline owns retries so it can show status; the SDK must not retry on its own.
        self._client = client or openai.OpenAI(api_key=api_key, base_url=BASE_URLS[provider],
                                               max_retries=0, timeout=300)

    def complete_json(self, *, system: str, user: str, schema: dict, schema_name: str,
                      effort: str | None = None) -> dict:
        extra = {}
        if effort and "gpt-oss" in self._model:
            # gpt-oss's thinking counts toward max_completion_tokens; "low" leaves room for long answers.
            extra["reasoning_effort"] = effort
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
                max_completion_tokens=self._max_output,
                **extra,
            )
        except openai.APIError as exc:
            kept = _schema_only_rejection(exc)
            if kept is not None:
                return kept
            raise map_error(exc, self.provider) from exc
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise PermanentError("The notes model ran out of output space")
        return parse_json_object(choice.message.content or "")


def _schema_only_rejection(exc: openai.APIError) -> dict | None:
    """Groq checks replies against the schema and rejects the whole reply for one stray value (gpt-oss once wrote
    "kind": "action" in otherwise good lecture notes). It sends the reply back as failed_generation: when that is a
    complete JSON object, keep it; the notes parsers already tidy unknown values. Cut-off or empty replies still fail."""
    if not isinstance(exc, openai.BadRequestError):
        return None
    body = exc.body if isinstance(exc.body, dict) else {}
    body = body.get("error", body) if isinstance(body.get("error"), dict) else body
    if body.get("code") != "json_validate_failed" or not body.get("failed_generation"):
        return None
    try:
        return parse_json_object(str(body["failed_generation"]))
    except TransientError:
        return None


def map_error(exc: openai.APIError, provider: str) -> Exception:
    label = LABELS.get(provider, provider)
    if isinstance(exc, openai.RateLimitError):
        return RateLimited(_retry_after(exc.response), f"{label} rate limit")
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return PermanentError(f"Invalid {label} API key (check .env)")
    if isinstance(exc, openai.InternalServerError):
        return TransientError(f"{label} server error ({why(exc)})")
    if isinstance(exc, openai.APIConnectionError):  # includes APITimeoutError
        return TransientError(f"Could not reach {label} ({why(exc)})")
    if isinstance(exc, openai.APIStatusError):
        return PermanentError(f"{label} rejected the request ({why(exc)})")
    return PermanentError(f"{label} error ({why(exc)})")


def _retry_after(response) -> float:
    try:
        return max(1.0, float(response.headers.get("retry-after")))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
