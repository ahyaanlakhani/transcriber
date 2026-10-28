"""Runtime configuration from environment variables and the project's .env file."""
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Language stored on a recording -> language code sent to the provider (None = auto-detect).
# "mixed": English + Urdu in one lecture, transcribed by Gemini 3.5 Transcribe (Groq auto-detect if no Gemini key).
MIXED = "mixed"
LANGUAGES: dict[str, str | None] = {"en": "en", "ur": "ur", "auto": None, MIXED: None}
DEFAULT_LANGUAGE = "en"

# Notes model providers -> the .env variable holding their API key.
PROVIDER_KEY_VARS = {
    "cerebras": "CEREBRAS_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
DEFAULT_NOTES_WRITER = "groq:openai/gpt-oss-120b"
DEFAULT_NOTES_CHECKER = "gemini:gemini-3.8-flash"
DEFAULT_ASK_MODEL = "claude-code"  # the user's Claude subscription via Claude Code; no API key
DEFAULT_PRACTICE_MODEL = "groq:openai/gpt-oss-20b"  # quick replies in the Practice room; its own Groq limits


@dataclass(frozen=True)
class Config:
    groq_api_key: str | None
    data_dir: Path
    port: int
    api_keys: Mapping[str, str] = field(default_factory=dict)
    notes_writer: str = DEFAULT_NOTES_WRITER
    notes_checker: str = DEFAULT_NOTES_CHECKER
    ask_model: str = DEFAULT_ASK_MODEL
    practice_model: str = DEFAULT_PRACTICE_MODEL
    server_url: str | None = None  # the user's own transcription server (see server/README.md)
    server_token: str | None = None
    local_transcriber: str | None = None  # e.g. "small": transcribe Local-only recordings on this laptop
    allowed_hosts: tuple[str, ...] = ()  # extra host names the app answers to (your Tailscale name, for the phone)


def load_config(env_file: Path | None = None, environ: Mapping[str, str] | None = None) -> Config:
    """Real environment variables win over the .env file; blank values count as unset."""
    file_values = dotenv_values(env_file or PROJECT_ROOT / ".env")
    env = os.environ if environ is None else environ

    def get(name: str) -> str:
        return (env.get(name) or file_values.get(name) or "").strip()

    data_dir = get("TRANSCRIBER_DATA_DIR")
    return Config(
        groq_api_key=get("GROQ_API_KEY") or None,
        data_dir=Path(data_dir) if data_dir else PROJECT_ROOT / "data",
        port=int(get("TRANSCRIBER_PORT") or 8000),
        api_keys={provider: key for provider, var in PROVIDER_KEY_VARS.items() if (key := get(var))},
        notes_writer=get("NOTES_WRITER") or DEFAULT_NOTES_WRITER,
        notes_checker=get("NOTES_CHECKER") or DEFAULT_NOTES_CHECKER,
        ask_model=get("ASK_MODEL") or DEFAULT_ASK_MODEL,
        practice_model=get("PRACTICE_MODEL") or DEFAULT_PRACTICE_MODEL,
        server_url=get("TRANSCRIBE_SERVER_URL") or None,
        server_token=get("TRANSCRIBE_SERVER_TOKEN") or None,
        local_transcriber=get("LOCAL_TRANSCRIBER") or None,
        allowed_hosts=tuple(h.strip().lower() for h in get("TRANSCRIBER_ALLOWED_HOSTS").split(",") if h.strip()),
    )
