"""Shared test helpers: synthetic media files and fakes."""
import subprocess
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg

from transcriber.providers.base import Segment

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], check=True)


def synth_audio(path: Path, parts: list[tuple[str, float]], sample_rate: int = 16000, channels: int = 1) -> Path:
    """Write an audio file made of ("tone", seconds) and ("silence", seconds) parts."""
    inputs: list[str] = []
    labels = ""
    for index, (kind, seconds) in enumerate(parts):
        if kind == "tone":
            source = f"sine=frequency=440:sample_rate={sample_rate}:duration={seconds}"
        else:
            source = f"anullsrc=r={sample_rate}:cl=mono:d={seconds}"
        inputs += ["-f", "lavfi", "-i", source]
        labels += f"[{index}:a]"
    graph = f"{labels}concat=n={len(parts)}:v=0:a=1[out]"
    _ffmpeg(*inputs, "-filter_complex", graph, "-map", "[out]",
            "-ac", str(channels), "-ar", str(sample_rate), str(path))
    return path


def make_video(path: Path, seconds: float = 2.0, with_audio: bool = True) -> Path:
    args = ["-f", "lavfi", "-i", f"color=c=black:s=64x64:d={seconds}"]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest"]
    _ffmpeg(*args, str(path))
    return path


def ffmpeg_info(path: Path) -> str:
    result = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(path)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stderr


@dataclass
class Call:
    path: Path
    language: str | None
    prompt: str


class FakeTranscriber:
    """Scripted stand-in for a provider. `behaviors` are consumed one per call."""

    name = "fake/model"

    def __init__(self, behaviors=None, on_call=None):
        self.behaviors = list(behaviors or [])
        self.on_call = on_call
        self.calls: list[Call] = []

    def transcribe(self, audio_path, *, language, prompt):
        self.calls.append(Call(audio_path, language, prompt))
        if self.on_call:
            self.on_call(len(self.calls))
        behavior = self.behaviors.pop(0) if self.behaviors else None
        if isinstance(behavior, BaseException):
            raise behavior
        if behavior is not None:
            return behavior
        return [Segment(0.0, 1.0, f"Chunk {len(self.calls)}.", -0.1, 0.01, 1.0)]


class FakeClock:
    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.sleeps: list[float] = []
        self.on_sleep = on_sleep

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


class FakeLLM:
    """Scripted stand-in for a notes model. Each reply is a dict, an exception, or callable(call) -> dict."""

    def __init__(self, name="fake:llm", replies=None):
        self.name = name
        self.replies = list(replies or [])
        self.calls: list[dict] = []

    def complete_json(self, *, system, user, schema, schema_name, effort=None):
        call = {"system": system, "user": user, "schema": schema, "schema_name": schema_name, "effort": effort}
        self.calls.append(call)
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, BaseException):
            raise reply
        return reply(call) if callable(reply) else reply


def undo_privacy_migration(store) -> None:
    """Make a database look like it was before the privacy upgrade, for the old-database migration tests."""
    for table in ("recording_sources", "provider_policies", "transmissions", "pip_memory", "follow_up_state", "reviews", "cards", "project_tasks",
                  "project_links", "projects", "translations"):
        store._execute(f"DROP TABLE IF EXISTS {table}")
    for column in ("privacy_classification", "privacy_floor", "privacy_provenance"):
        store._execute(f"ALTER TABLE recordings DROP COLUMN {column}")
    store._execute("ALTER TABLE concepts DROP COLUMN definition_fp")
    store._execute("ALTER TABLE concepts DROP COLUMN note")
    store._execute("ALTER TABLE concepts DROP COLUMN visibility")
