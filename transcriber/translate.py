"""Translate a transcript into English, paragraph by paragraph, so every English paragraph keeps its timestamp.

The notes writer does the work. Paragraphs go in batches; a batch whose reply doesn't match line for line is split and
asked again, down to single paragraphs. A paragraph that still can't be translated keeps its original words, so
nothing is dropped. Another language in Roman letters uses about twice the tokens of English for the same characters, so batches are small.
"""
import json
import time
from collections.abc import Callable

from .providers.base import PermanentError
from .retry import call_with_retries

BATCH_CHARS = 4_000
# Groq answers 413 for a request over its limit, and 400 (json_validate_failed, empty) when the model's thinking and the
# English fill the output cap. Both mean the batch is too big.
TOO_LARGE = ("HTTP 413", "HTTP 400")

SYSTEM = """You translate lecture and conversation transcripts into plain, natural English.
The input is spoken language: often a language other than English, written in Roman letters or in its own script,
mixed with English. Rules:
- Translate the meaning faithfully. Do not summarise, explain, correct, censor or add anything.
- Keep English words and sentences as they are. Keep names, numbers and technical terms.
- Keep each paragraph as one paragraph; the speaker's sentences stay in order.
- Return exactly one output string for each input string, in the same order. Never merge, split or drop paragraphs."""

SCHEMA = {
    "type": "object",
    "properties": {"paragraphs": {"type": "array", "items": {"type": "string"}}},
    "required": ["paragraphs"],
    "additionalProperties": False,
}


class Translator:
    def __init__(self, llm, wait: Callable[[float], None] = time.sleep,
                 on_rate_limited: Callable[[float], None] = lambda seconds: None,
                 on_resumed: Callable[[], None] = lambda: None):
        self.llm = llm
        self.wait = wait
        self.on_rate_limited, self.on_resumed = on_rate_limited, on_resumed
        self.kept = 0  # paragraphs that couldn't be translated and kept their original words

    @property
    def name(self) -> str:
        return self.llm.name

    def __call__(self, texts: list[str], progress: Callable[[int, int], None] = lambda done, total: None) -> list[str]:
        out: list[str] = []
        self.kept = 0
        for batch in batches(texts):
            out.extend(self._translate(batch))
            progress(len(out), len(texts))
        return out

    def _translate(self, lines: list[str]) -> list[str]:
        try:
            reply = self._ask(lines)
        except PermanentError as exc:
            if not any(code in str(exc) for code in TOO_LARGE):
                raise
            reply = None  # too big for the model: fall through to asking in halves
        if reply is not None:
            return reply
        if len(lines) == 1:
            self.kept += 1
            return lines  # keep the original words rather than lose them
        middle = len(lines) // 2
        return self._translate(lines[:middle]) + self._translate(lines[middle:])

    def _ask(self, lines: list[str]) -> list[str] | None:
        user = "Paragraphs to translate (a JSON list, in order):\n" + json.dumps(lines, ensure_ascii=False)
        reply = call_with_retries(
            lambda: self.llm.complete_json(system=SYSTEM, user=user, schema=SCHEMA, schema_name="translation",
                                        effort="low"),
            wait=self.wait, on_rate_limited=self.on_rate_limited, on_resumed=self.on_resumed)
        done = reply.get("paragraphs") if isinstance(reply, dict) else None
        if not isinstance(done, list) or len(done) != len(lines):
            return None
        if not all(isinstance(text, str) and text.strip() for text in done):
            return None
        return [text.strip() for text in done]


def batches(texts: list[str]) -> list[list[str]]:
    out: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        if current and size + len(text) > BATCH_CHARS:
            out.append(current)
            current, size = [], 0
        current.append(text)
        size += len(text)
    if current:
        out.append(current)
    return out
