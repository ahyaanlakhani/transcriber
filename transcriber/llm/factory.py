"""Build the notes writer / checker from a "provider:model" setting."""
from collections.abc import Mapping

from .base import LLMClient
from .claude_code import ClaudeCodeClient, find_claude
from .gemini import GeminiClient
from .openai_compat import BASE_URLS, OpenAICompatClient


def make_llm(spec: str | None, api_keys: Mapping[str, str]) -> LLMClient | None:
    """Return None when the spec is empty / "none" or its provider has no API key."""
    if not spec or spec.strip().lower() == "none":
        return None
    provider, sep, model = spec.strip().partition(":")
    provider = provider.lower()
    if provider == "claude-code":  # the user's Claude subscription via the Claude Code CLI; no API key
        executable = find_claude()
        return ClaudeCodeClient(executable, model or None) if executable else None
    if not sep or not model:
        raise ValueError(f"A notes model must look like 'provider:model', got '{spec}'")
    if provider != "gemini" and provider not in BASE_URLS:
        raise ValueError(f"Unknown notes provider '{provider}'")
    key = api_keys.get(provider)
    if not key:
        return None
    if provider == "gemini":
        return GeminiClient(model, key)
    return OpenAICompatClient(provider, model, key)
