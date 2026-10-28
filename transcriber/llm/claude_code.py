"""Claude through the locally installed Claude Code CLI, signed in with the user's Claude subscription.

Each call runs `claude -p` fully isolated: an empty working folder, no tools, none of the user's settings,
plugins, hooks or MCP servers, and our own system prompt. The meeting material goes in through stdin.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from ..providers.base import PermanentError, TransientError, why
from .base import parse_json_object

SANDBOX = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "meeting-transcriber" / "claude-sandbox"
TIMEOUT_SEC = 300
LOGIN_HINT = "Claude isn't signed in. Open a terminal and run: claude auth login"
JSON_INSTRUCTION = "\n\nReply with only a JSON object that matches the requested schema."


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    return str(candidate) if candidate.exists() else None


class ClaudeCodeClient:
    max_input_chars = None  # Claude handles whole transcripts

    def __init__(self, executable: str, model: str | None = None, *, run=subprocess.run, sandbox: Path = SANDBOX):
        self.name = f"claude-code:{model}" if model else "claude-code"
        self._executable, self._model = executable, model
        self._run, self._sandbox = run, sandbox

    def complete_json(self, *, system: str, user: str, schema: dict, schema_name: str,
                      effort: str | None = None) -> dict:
        args = [self._executable, "-p", "Answer using the material provided on standard input.",
                "--output-format", "json", "--system-prompt", system + JSON_INSTRUCTION,
                "--json-schema", json.dumps(schema), "--tools", "", "--setting-sources", "",
                "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
        if self._model:
            args += ["--model", self._model]
        self._sandbox.mkdir(parents=True, exist_ok=True)
        try:
            proc = self._run(args, input=user, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", cwd=self._sandbox, timeout=TIMEOUT_SEC)
        except subprocess.TimeoutExpired as exc:
            raise TransientError("Claude took too long to answer") from exc
        except OSError as exc:
            raise PermanentError(f"Could not start Claude Code ({why(exc)})") from exc
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise TransientError("Claude Code returned unexpected output") from exc
        if data.get("is_error"):
            message = str(data.get("result") or data.get("subtype") or "unknown error")
            lowered = message.lower()
            if "login" in lowered or "logged in" in lowered:
                raise PermanentError(LOGIN_HINT)
            if "limit" in lowered:
                raise PermanentError("Claude usage limit reached")
            raise TransientError("Claude Code reported an error")
        structured = data.get("structured_output")
        if isinstance(structured, dict):
            return structured
        return parse_json_object(str(data.get("result") or ""))
