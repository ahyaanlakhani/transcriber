"""Build the Whisper `prompt`: a glossary of names plus the end of the previous chunk.

Whisper reads at most 224 prompt tokens. These caps keep us comfortably under that.
"""
import re

VOCAB_MAX_CHARS = 450  # length of the comma-joined terms
TAIL_MAX_CHARS = 150


def parse_terms(vocabulary: str) -> list[str]:
    return [term.strip() for term in re.split(r"[,\n]", vocabulary) if term.strip()]


def build_prompt(vocabulary: str, previous_tail: str) -> str:
    pieces = [_glossary(parse_terms(vocabulary)), _tail(previous_tail)]
    return " ".join(piece for piece in pieces if piece)


def _glossary(terms: list[str]) -> str:
    kept: list[str] = []
    for term in terms:
        if len(", ".join([*kept, term])) > VOCAB_MAX_CHARS:
            break
        kept.append(term)
    return f"Glossary: {', '.join(kept)}." if kept else ""


def _tail(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= TAIL_MAX_CHARS:
        return text
    start = len(text) - TAIL_MAX_CHARS
    if text[start - 1] != " ":  # we landed mid-word: skip to the next word
        next_space = text.find(" ", start)
        start = len(text) if next_space == -1 else next_space + 1
    return text[start:]
