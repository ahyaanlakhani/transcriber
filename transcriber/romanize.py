"""Write a second language in Roman letters, leaving English exactly as it is.

For a mixed lecture (English plus your other language) Gemini writes the other language in its own script, which reads
badly next to English. Lines with any non-Latin script are converted by the notes AI into Roman letters, the way
people type that language in chats. Lines are sent in batches; a batch whose reply doesn't match line for line is
split and asked again, and a line that still fails keeps its original text, so nothing is lost.

Which language this is comes from the SECOND_LANGUAGE setting (see .env.example); it only names the language in the
instruction to the model.
"""
import json
import time
from collections.abc import Callable

from .providers.base import PermanentError, Segment
from .retry import call_with_retries

LATIN_LAST = 0x24F  # Basic Latin through Latin Extended-B: letters above this belong to another script
BATCH = 40
DEFAULT_LANGUAGE_NAME = "the speaker's other language"

SYSTEM = """You convert {language}, written in its own script, into Roman (Latin) letters, the way people type it in
chats and notes. Rules:
- Keep English words and English sentences exactly as they are, in Latin letters.
- Keep the meaning and the order. Do not translate, summarise, correct or add anything.
- Keep names, numbers and technical terms as spoken.
- Return one output line for each input line, in the same order. Never merge or drop lines."""

SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"],
    "additionalProperties": False,
}


def has_non_latin_script(text: str) -> bool:
    """True when the text has letters in a script other than Latin (Arabic, Cyrillic, Greek, CJK...)."""
    return any(char.isalpha() and ord(char) > LATIN_LAST for char in text)


class Romanizer:
    def __init__(self, llm, wait: Callable[[float], None] = time.sleep, language: str = ""):
        self.llm = llm
        self.wait = wait
        self.language = language.strip() or DEFAULT_LANGUAGE_NAME
        self._system = SYSTEM.replace("{language}", self.language)
        self._refused = False

    def __call__(self, segments: list[Segment]) -> list[Segment]:
        self._refused = False
        todo = [i for i, s in enumerate(segments) if has_non_latin_script(s.text)]
        out = list(segments)
        for start in range(0, len(todo), BATCH):
            indices = todo[start:start + BATCH]
            converted = self._convert_all([segments[i].text for i in indices])
            for index, text in zip(indices, converted):
                out[index] = segments[index] if text is None else _replace_text(segments[index], text)
        return out

    def _convert_all(self, lines: list[str]) -> list[str | None]:
        """A batch the model can't answer (its reply is cut off or doesn't match) is split and asked again, down to
        single lines; a line that still fails stays None and keeps its original script."""
        converted = self._convert(lines)
        if converted is not None:
            return converted
        if len(lines) == 1:
            return [None]
        middle = len(lines) // 2
        return self._convert_all(lines[:middle]) + self._convert_all(lines[middle:])

    def _convert(self, lines: list[str]) -> list[str] | None:
        if self._refused:
            return None
        user = "Input lines (one per line, in order):\n" + json.dumps(lines, ensure_ascii=False)
        try:
            reply = call_with_retries(
                lambda: self.llm.complete_json(system=self._system, user=user, schema=SCHEMA, schema_name="lines",
                                               effort="low"),  # less thinking leaves room for the lines
                wait=self.wait, on_rate_limited=lambda seconds: None, on_resumed=lambda: None)
        except PermanentError as exc:
            # a size problem is cured by smaller batches; anything else (a rejected key, a privacy block) will
            # fail the same way every time, so stop asking and keep the original script
            if not any(code in str(exc) for code in ("HTTP 400", "HTTP 413")):
                self._refused = True
            return None
        except Exception:
            return None
        converted = reply.get("lines") if isinstance(reply, dict) else None
        if not isinstance(converted, list) or len(converted) != len(lines):
            return None
        if not all(isinstance(line, str) and line.strip() for line in converted):
            return None
        return [line.strip() for line in converted]


def _replace_text(segment: Segment, text: str) -> Segment:
    from dataclasses import replace
    return replace(segment, text=text)
