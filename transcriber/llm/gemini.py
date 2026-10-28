"""Google Gemini via the google-genai SDK (the default notes checker)."""
import re

import httpx
import httpx2
from google import genai
from google.genai import errors, types

from ..providers.base import PermanentError, RateLimited, TransientError, why
from .base import parse_json_object

MAX_OUTPUT_TOKENS = 16_000
DEFAULT_RETRY_AFTER = 60.0
NETWORK_ERRORS = (httpx.TransportError, httpx2.TransportError)


class GeminiClient:
    def __init__(self, model: str, api_key: str, client=None):
        self.name = f"gemini:{model}"
        self._model = model
        self._client = client or genai.Client(api_key=api_key)

    def complete_json(self, *, system: str, user: str, schema: dict, schema_name: str,
                      effort: str | None = None) -> dict:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        try:
            response = self._client.models.generate_content(model=self._model, contents=user, config=config)
        except errors.APIError as exc:
            raise map_error(exc) from exc
        except NETWORK_ERRORS as exc:
            raise TransientError(f"Could not reach Gemini ({why(exc)})") from exc
        return parse_json_object(response.text or "")


def map_error(exc: errors.APIError) -> Exception:
    message = exc.message or str(exc)
    if exc.code == 429:
        return RateLimited(_retry_delay(exc), "Gemini rate limit")
    if exc.code in (401, 403) or (exc.code == 400 and "api key" in message.lower()):
        return PermanentError("Invalid Gemini API key (check GEMINI_API_KEY in .env)")
    if exc.code is not None and exc.code >= 500:
        return TransientError(f"Gemini server error ({why(exc)})")
    return PermanentError(f"Gemini rejected the request ({why(exc)})")


def _retry_delay(exc: errors.APIError) -> float:
    details = exc.details if isinstance(exc.details, dict) else {}
    for item in details.get("error", {}).get("details", []) or []:
        if isinstance(item, dict) and (match := re.fullmatch(r"(\d+(?:\.\d+)?)s", str(item.get("retryDelay", "")))):
            return max(1.0, float(match.group(1)))
    return DEFAULT_RETRY_AFTER
