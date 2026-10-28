"""The privacy gateway: nothing reaches a cloud model or transcription service without passing through here.

Every recording has a class (standard / private / local only). Every cloud configuration (a provider and model) is
either approved for a class or it is not. A request is allowed only when the strictest class among the recordings it
draws on is covered by that configuration's approval. Anything unknown (a provider, a recording, a class, where a
combined recording came from, no scope at all) is refused. Refusals are plain messages, never a quiet fallback, and
nothing is written to the log except which feature, which configuration, which recordings and why.
"""
import contextvars
import re
import sys
import traceback
from contextlib import contextmanager
from dataclasses import dataclass

from .privacy_store import CLASSES, LOCAL_ONLY, PRIVATE, RANK, STANDARD, strictest
from .providers.base import PermanentError

CLASS_LABELS = {STANDARD: "Standard", PRIVATE: "Private", LOCAL_ONLY: "Local only"}
RETENTION_VERIFIED = "zero_retention_verified"


@dataclass(frozen=True)
class ProviderFacts:
    label: str
    locality: str  # cloud | self_hosted
    private_possible: bool  # can this ever be approved for Private recordings?
    note: str


# Written by hand from each provider's own terms (checked 2026-10-14). The app never assumes more than this.
PROVIDERS = {
    "groq": ProviderFacts("Groq", "cloud", True,
                          "No training on API data and no retention of requests by default; a stricter zero-retention "
                          "switch may exist under Data Controls. Check your console before approving for Private."),
    "cerebras": ProviderFacts("Cerebras", "cloud", True, "Check its current data terms before approving."),
    "openrouter": ProviderFacts("OpenRouter", "cloud", True,
                                "Passes requests on to other companies: their terms apply too."),
    "openai": ProviderFacts("OpenAI", "cloud", True, "API data is not used for training by default; retention applies."),
    "gemini": ProviderFacts("Google Gemini", "cloud", False,
                            "Free-tier terms allow product improvement and human review, so it can be approved for "
                            "Standard recordings only and never for Private ones."),
    "claude-code": ProviderFacts("Claude (your subscription)", "cloud", False,
                                 "Runs through your Claude account. It can be set not to train, but conversations "
                                 "are not zero-retention, so it can never be approved for Private."),
    "local": ProviderFacts("This laptop", "local", True, "Runs on this computer. Nothing leaves it."),
    "remote": ProviderFacts("Your own transcription server", "self_hosted", True,
                            "Your server's operator decides what is kept. Approve it only if that is you."),
}
NAME_PREFIX = {"server": "remote"}  # a client's name starts with this; the table above uses the right-hand word


def log_failure(logger, message: str, *args) -> None:
    """Log an unexpected failure with where it happened but not what the exception says: an error message can quote
    the text that was being processed."""
    exc = sys.exc_info()[1]
    frames = "".join(traceback.format_tb(exc.__traceback__)).rstrip() if exc else ""
    logger.error(message + " (%s)\n%s", *args, type(exc).__name__ if exc else "?", frames)


def provider_of(config_id: str) -> str:
    head = re.split(r"[:/]", config_id, maxsplit=1)[0].strip().lower()
    return NAME_PREFIX.get(head, head)


class PrivacyBlocked(PermanentError):
    """A request was refused before anything was sent. The message is safe to show and carries no content."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Scope:
    feature: str
    recording_ids: tuple[str, ...]
    classification: str | None  # a class that applies on its own (a live session before it has a recording)


_scope: contextvars.ContextVar[Scope | None] = contextvars.ContextVar("privacy_scope", default=None)


@contextmanager
def scope(feature: str, recording_ids=(), *, classification: str | None = None, no_recording_content: bool = False):
    """Say what a block of work draws on. Everything sent while inside is checked against exactly this.

    `no_recording_content=True` is a promise that the request carries nothing taken from any recording."""
    ids = tuple(dict.fromkeys(recording_ids))
    if no_recording_content and not ids and classification is None:
        classification = STANDARD
    token = _scope.set(Scope(feature, ids, classification))
    try:
        yield
    finally:
        _scope.reset(token)


def current_scope() -> Scope | None:
    return _scope.get()


class Gateway:
    def __init__(self, store, *, allow_all: bool = False):
        self.store = store
        self._allow_all = allow_all  # for tests with fake providers only; nothing in the app sets it

    # ---- the decision

    def decide(self, config_id: str, scope_: Scope | None) -> tuple[bool, str, str]:
        """(allowed, reason code, message). Pure: nothing is logged or sent."""
        if self._allow_all:
            return True, "allowed", ""
        facts = PROVIDERS.get(provider_of(config_id))
        if facts is None:
            return False, "unknown_provider", "This provider isn't one the app knows how to protect, so nothing was sent."
        if facts.locality == "local":
            return True, "allowed", ""  # nothing is sent anywhere
        if scope_ is None:
            return False, "no_scope", "The app couldn't tell which recordings this request draws on, so nothing was sent."
        if scope_.classification is not None and scope_.classification not in RANK:
            return False, "invalid_class", "A recording has an unrecognised privacy class, so nothing was sent."
        classes = [scope_.classification] if scope_.classification else []
        known = True
        if scope_.recording_ids:
            found = self.store.effective_privacy(scope_.recording_ids)
            if len(found) != len(set(scope_.recording_ids)):
                return False, "unknown_source", "One of the recordings behind this request no longer exists, so nothing was sent."
            for cls, ok in found.values():
                classes.append(cls)
                known = known and ok
        if not classes:
            return False, "no_context", "The app couldn't tell how private this is, so nothing was sent."
        if not known:
            return False, "unknown_provenance", (
                "This recording was combined from parts the app can't trace. Open it and confirm where it came from "
                "before it can be sent to a cloud provider.")
        cls = strictest(classes)
        if cls == LOCAL_ONLY:
            return False, "local_only", "This recording is set to Local only, so it never leaves this laptop."
        policy = self.store.get_policies().get(config_id)
        if policy is None or not (policy["approved_standard"] or policy["approved_private"]):
            return False, "not_approved", (f"{facts.label} ({config_id}) hasn't been approved yet. "
                                           "Approve it in Privacy to use it.")
        if cls == PRIVATE:
            if not facts.private_possible:
                return False, "private_impossible", f"{facts.label} can't be used for Private recordings."
            if not (policy["approved_private"] and policy["retention"] == RETENTION_VERIFIED):
                return False, "private_not_verified", (
                    f"This is a Private recording and {facts.label} hasn't been verified as zero-retention "
                    f"for you. Confirm that in Privacy first.")
        return True, "allowed", ""

    def authorize(self, config_id: str) -> None:
        """Called by the guarded clients immediately before anything is sent. Raises PrivacyBlocked."""
        sc = current_scope()
        allowed, reason, message = self.decide(config_id, sc)
        facts = PROVIDERS.get(provider_of(config_id))
        if allowed and facts is not None and facts.locality == "local":
            return  # nothing to record: it never left the laptop
        try:
            self.store.log_transmission(feature=sc.feature if sc else "unknown", config_id=config_id,
                                        recording_ids=sc.recording_ids if sc else (),
                                        decision="allowed" if allowed else "blocked", reason=reason)
        except Exception:  # a log that can't be written must not decide what is sent, in either direction
            pass
        if not allowed:
            raise PrivacyBlocked(reason, message)

    def can_use(self, config_id: str, recording_ids) -> bool:
        """Would a request drawing on exactly these recordings be allowed? Used to choose material before building it."""
        return self.decide(config_id, Scope("check", tuple(recording_ids), None))[0] if recording_ids else False

    def eligible_ids(self, config_id: str, recording_ids) -> set[str]:
        """The recordings among these that this configuration may see, each judged on its own."""
        return {rid for rid in dict.fromkeys(recording_ids) if self.can_use(config_id, [rid])}

    def visible_topic_names(self, config_id: str) -> list[str]:
        """The topic names this configuration may be shown: each one only if every recording that gave rise to it
        may be sent there. A topic with no known recording stays out."""
        rows = self.store.query("SELECT t.name, rt.recording_id FROM topics t LEFT JOIN recording_topics rt"
                                " ON rt.topic_id = t.id ORDER BY t.name COLLATE NOCASE")
        sources: dict[str, set[str]] = {}
        for r in rows:
            sources.setdefault(r["name"], set())
            if r["recording_id"]:
                sources[r["name"]].add(r["recording_id"])
        if self._allow_all:
            return list(sources)
        allowed = self.eligible_ids(config_id, {rid for ids in sources.values() for rid in ids})
        return [name for name, ids in sources.items() if ids and ids <= allowed]

    # ---- wrappers

    def guard_llm(self, client):
        return None if client is None else GuardedLLM(client, self)

    def guard_transcriber(self, client):
        return None if client is None else GuardedTranscriber(client, self)

    def guard_remote(self, client):
        return None if client is None else GuardedRemote(client, self)


class GuardedLLM:
    def __init__(self, inner, gateway: Gateway):
        self._inner, self._gateway = inner, gateway
        self.name = inner.name
        self.max_input_chars = getattr(inner, "max_input_chars", None)

    def complete_json(self, **kwargs):
        self._gateway.authorize(self.name)  # every attempt, including each retry and each fallback, asks again
        return self._inner.complete_json(**kwargs)


class GuardedTranscriber:
    def __init__(self, inner, gateway: Gateway):
        self._inner, self._gateway = inner, gateway
        self.name = inner.name

    def transcribe(self, *args, **kwargs):
        self._gateway.authorize(self.name)
        return self._inner.transcribe(*args, **kwargs)


class GuardedRemote:
    """Your own server: only the calls that send audio are gated; health, polling and deleting carry nothing."""

    def __init__(self, inner, gateway: Gateway):
        self._inner, self._gateway = inner, gateway

    @property
    def name(self) -> str:
        return self._inner.name

    def submit(self, *args, **kwargs):
        self._gateway.authorize(self._inner.name)
        return self._inner.submit(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._inner, item)


__all__ = ["CLASSES", "CLASS_LABELS", "Gateway", "PROVIDERS", "PrivacyBlocked", "RETENTION_VERIFIED", "current_scope",
           "provider_of", "scope"]
