import json
import subprocess
from types import SimpleNamespace

import pytest

from transcriber.llm.claude_code import LOGIN_HINT, ClaudeCodeClient
from transcriber.llm.factory import make_llm
from transcriber.providers.base import PermanentError, TransientError

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


class FakeRun:
    def __init__(self, stdout="", error=None):
        self.stdout, self.error, self.calls = stdout, error, []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(stdout=self.stdout, stderr="", returncode=0)


def client(run, tmp_path, model=None):
    return ClaudeCodeClient("claude.exe", model, run=run, sandbox=tmp_path / "sandbox")


def ask(c):
    return c.complete_json(system="SYS", user="MEETING AND QUESTION", schema=SCHEMA, schema_name="answer")


def result(**fields):
    return json.dumps({"type": "result", "is_error": False, "result": "", **fields})


def test_runs_isolated_with_stdin_and_reads_structured_output(tmp_path):
    run = FakeRun(result(structured_output={"answer": "Sara, on Friday [00:00]."}))
    assert ask(client(run, tmp_path, model="sonnet")) == {"answer": "Sara, on Friday [00:00]."}
    [(args, kwargs)] = run.calls
    assert args[:2] == ["claude.exe", "-p"]
    for flag in ("--tools", "--setting-sources", "--strict-mcp-config", "--disable-slash-commands",
                 "--no-session-persistence"):
        assert flag in args
    assert args[args.index("--tools") + 1] == "" and args[args.index("--setting-sources") + 1] == ""
    assert args[args.index("--output-format") + 1] == "json"
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    assert args[args.index("--system-prompt") + 1].startswith("SYS")
    assert args[args.index("--model") + 1] == "sonnet"
    assert kwargs["input"] == "MEETING AND QUESTION"
    assert kwargs["cwd"] == tmp_path / "sandbox" and (tmp_path / "sandbox").is_dir()


def test_falls_back_to_json_in_result_text(tmp_path):
    run = FakeRun(result(result='```json\n{"answer": "yes"}\n```'))
    assert ask(client(run, tmp_path)) == {"answer": "yes"}
    assert "--model" not in run.calls[0][0]


@pytest.mark.parametrize("message, error, text", [
    ("Not logged in · Please run /login", PermanentError, LOGIN_HINT),
    ("Claude AI usage limit reached|1791110000", PermanentError, "usage limit"),
    ("Overloaded", TransientError, "reported an error"),
])
def test_errors(tmp_path, message, error, text):
    run = FakeRun(json.dumps({"type": "result", "is_error": True, "result": message}))
    with pytest.raises(error, match=text.split("|")[0][:20].replace("(", r"\(")):
        ask(client(run, tmp_path))


def test_garbled_output_is_transient(tmp_path):
    with pytest.raises(TransientError):
        ask(client(FakeRun("panic: something"), tmp_path))


def test_timeout_is_transient(tmp_path):
    with pytest.raises(TransientError, match="too long"):
        ask(client(FakeRun(error=subprocess.TimeoutExpired("claude", 300)), tmp_path))


def test_missing_executable_is_permanent(tmp_path):
    with pytest.raises(PermanentError, match="Could not start Claude Code"):
        ask(client(FakeRun(error=FileNotFoundError("claude.exe")), tmp_path))


def test_factory(monkeypatch):
    monkeypatch.setattr("transcriber.llm.factory.find_claude", lambda: "C:/claude.exe")
    assert make_llm("claude-code", {}).name == "claude-code"
    assert make_llm("claude-code:sonnet", {}).name == "claude-code:sonnet"
    monkeypatch.setattr("transcriber.llm.factory.find_claude", lambda: None)
    assert make_llm("claude-code", {}) is None
