"""Provider-neutral interface for the language models that write and check notes."""
import json
import re
from typing import Protocol

from ..providers.base import TransientError


class LLMClient(Protocol):
    name: str  # "provider:model", shown in the UI

    def complete_json(self, *, system: str, user: str, schema: dict, schema_name: str,
                      effort: str | None = None) -> dict:
        """Return the model's reply as a JSON object that follows `schema`."""
        ...


def parse_json_object(text: str) -> dict:
    """Parse a model reply; garbled output is retried like any other transient failure."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise TransientError(f"The model returned invalid JSON ({exc.msg})") from exc
    if not isinstance(value, dict):
        raise TransientError("The model returned JSON that is not an object")
    return value
