"""Concept names: one normalized key per idea, so "RAG", "rag" and "Retrieval-Augmented Generation (RAG)" meet
(pure logic, no model)."""
import re

# Short forms that come up in business, analytics and AI classes. Both sides are normalized keys.
KNOWN_ALIASES = {
    "ai": "artificial intelligence", "ml": "machine learning", "dl": "deep learning",
    "llm": "large language model", "nlp": "natural language processing", "iot": "internet of thing",
    "rag": "retrieval augmented generation", "bmc": "business model canvas", "bpr": "business process reengineering",
    "kpi": "key performance indicator", "roi": "return on investment", "erp": "enterprise resource planning",
    "crm": "customer relationship management", "api": "application programming interface",
    "ui": "user interface", "ux": "user experience", "ar": "augmented reality", "vr": "virtual reality",
    "genai": "generative ai", "gen ai": "generative ai", "cv": "computer vision", "mvp": "minimum viable product",
    "saas": "software as a service", "b2b": "business to business", "b2c": "business to consumer",
}
_LEADING = re.compile(r"^(?:the|a|an|what is|what are|intro to|introduction to)\s+")
_PAREN = re.compile(r"^(?P<long>.+?)\s*\((?P<short>[^()]{1,24})\)\s*$")
_KEEP_S = ("ss", "us", "is", "os", "as")  # analysis, bus, os, saas
MAX_WORDS = 6  # longer headings are sentences about an idea, not its name
MIN_KEY = 2


def key_of(name: str) -> str:
    """lowercase, punctuation as spaces, no leading article, last word singular."""
    text = re.sub(r"[^\w\s]", " ", str(name).lower().replace("&", " and "))
    text = _LEADING.sub("", " ".join(text.split()))
    words = text.split()
    if not words:
        return ""
    last = words[-1]
    if len(last) > 4 and last.endswith("ies"):
        last = last[:-3] + "y"
    elif len(last) > 3 and last.endswith("s") and not last.endswith(_KEEP_S):
        last = last[:-1]
    words[-1] = last
    return " ".join(words)


def tidy(name: str) -> str:
    """A display name: single spaces, first letter capital unless it is an acronym or a brand like iPhone."""
    text = " ".join(str(name).split()).strip(" .,:;-–—")
    if text and text[0].islower() and not (len(text) > 1 and text[1].isupper()):
        text = text[0].upper() + text[1:]
    return text


def split_alias(name: str) -> tuple[str, str | None]:
    """"Retrieval augmented generation (RAG)" -> ("Retrieval augmented generation", "RAG")."""
    match = _PAREN.match(" ".join(str(name).split()))
    if not match:
        return name, None
    long, short = match.group("long"), match.group("short")
    if len(short.split()) > len(long.split()):  # "RAG (retrieval augmented generation)"
        long, short = short, long
    return long, short


def initials(key: str) -> str:
    words = key.split()
    return "".join(w[0] for w in words) if len(words) >= 2 else ""


def acronym(name: str) -> bool:
    """BMC, KPIs, IoT: one short word with at least two capitals."""
    text = str(name).strip()
    return " " not in text and 3 <= len(text) <= 7 and sum(c.isupper() for c in text) >= 2


def usable(name: str) -> bool:
    key = key_of(name)
    return len(key) >= MIN_KEY and len(key.split()) <= MAX_WORDS and not key.isdigit()


def _key_regex(key: str) -> str:
    words = [re.escape(w) for w in key.split()]
    last = key.split()[-1]
    if last.endswith("y") and len(last) > 3:
        words[-1] = re.escape(last[:-1]) + "(?:y|ies)"
    else:
        words[-1] += "(?:e?s)?"
    return r"[\s\-]+".join(words)


def mention_pattern(keys) -> re.Pattern | None:
    """Matches text that names any of these keys as whole words, any case, singular or plural, spaces or hyphens."""
    parts = sorted({k for k in keys if len(k) >= MIN_KEY}, key=len, reverse=True)
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(_key_regex(k) for k in parts) + r")(?!\w)", re.IGNORECASE)
