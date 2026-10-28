"""Thin wrappers around the ffmpeg binary bundled by imageio-ffmpeg."""
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import imageio_ffmpeg

from .providers.base import PermanentError

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SILENCE_NOISE = "-35dB"
SILENCE_MIN_SEC = 0.4

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
_CREATION_TIME = re.compile(r"creation_time\s*:\s*(\S+(?:[ T]\d\d:\d\d:\d\d\S*)?)")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [FFMPEG, "-hide_banner", "-nostdin", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _to_flac(args: list[str], dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    result = _run(["-y", *args, "-c:a", "flac", "-f", "flac", str(tmp)])
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise PermanentError(_describe_failure(result.stderr))
    tmp.replace(dst)


def normalize(src: Path, dst: Path) -> None:
    """Convert any audio or video file to 16 kHz mono FLAC (audio only)."""
    _to_flac(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000"], dst)


def parse_recorded_at(ffmpeg_output: str) -> str | None:
    """The first creation_time in ffmpeg's description of a file, as UTC ISO; None if absent or a dummy date."""
    match = _CREATION_TIME.search(ffmpeg_output)
    if not match:
        return None
    try:
        stamp = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.year < 2000:  # 1904/1970: the device never set it
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).replace(microsecond=0).isoformat()


def recorded_at(src: Path) -> str | None:
    """When the file says it was recorded (phones stamp this; Voice Memos does), or None."""
    if not src.exists():
        return None
    return parse_recorded_at(_run(["-i", str(src)]).stderr)


def pcm_to_flac(src: Path, dst: Path, sample_rate: int = 16000) -> None:
    """Raw 16-bit mono PCM (from a live recording) to FLAC."""
    _to_flac(["-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", str(src)], dst)


def pcm_to_mp3(pcm: bytes, sample_rate: int, dst: Path, bitrate: str = "64k") -> None:
    """Raw 16-bit mono PCM to MP3 (recaps)."""
    tmp = dst.with_name(dst.name + ".tmp")
    result = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-y", "-f", "s16le", "-ar", str(sample_rate),
                             "-ac", "1", "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", bitrate, "-f", "mp3", str(tmp)],
                            input=pcm, capture_output=True)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise PermanentError(_describe_failure(result.stderr.decode("utf-8", "replace")))
    tmp.replace(dst)


def to_mp3(src: Path, dst: Path, bitrate: str = "64k") -> None:
    """Any audio file to mono MP3 (highlights reels), loudness-evened so quiet moments are still clear."""
    tmp = dst.with_name(dst.name + ".tmp")
    result = _run(["-y", "-i", str(src), "-af", "loudnorm=I=-18:TP=-2:LRA=11", "-ac", "1", "-ar", "24000",
                   "-c:a", "libmp3lame", "-b:a", bitrate, "-f", "mp3", str(tmp)])
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise PermanentError(_describe_failure(result.stderr))
    tmp.replace(dst)


def cut(src: Path, start: float, end: float, dst: Path) -> None:
    """Extract [start, end) seconds of src into dst."""
    _to_flac(["-i", str(src), "-ss", f"{start:.3f}", "-to", f"{end:.3f}"], dst)


def concat(srcs: list[Path], dst: Path, gap_sec: float) -> None:
    """Join 16 kHz mono files in order, with gap_sec of silence between them."""
    inputs: list[str] = []
    for index, src in enumerate(srcs):
        if index:
            inputs += ["-f", "lavfi", "-t", f"{gap_sec:.3f}", "-i", "anullsrc=r=16000:cl=mono"]
        inputs += ["-i", str(src)]
    # Inputs alternate file, silence, file, …; concatenate them in that order.
    count = 2 * len(srcs) - 1
    labels = "".join(f"[{i}:a]" for i in range(count))
    _to_flac([*inputs, "-filter_complex", f"{labels}concat=n={count}:v=0:a=1[out]", "-map", "[out]",
              "-ac", "1", "-ar", "16000"], dst)


def probe_duration(path: Path) -> float:
    result = _run(["-i", str(path)])  # exits non-zero (no output file) but prints the header
    match = _DURATION.search(result.stderr)
    if not match:
        raise PermanentError("Could not determine the audio duration")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def detect_silences(path: Path, duration: float) -> list[tuple[float, float]]:
    result = _run(["-i", str(path), "-af", f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_MIN_SEC}",
                   "-f", "null", "-"])
    return parse_silences(result.stderr, duration)


def parse_silences(stderr: str, duration: float) -> list[tuple[float, float]]:
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for line in stderr.splitlines():
        if match := _SILENCE_START.search(line):
            start = max(0.0, float(match.group(1)))
        elif (match := _SILENCE_END.search(line)) and start is not None:
            silences.append((start, float(match.group(1))))
            start = None
    if start is not None:  # silence runs to the end of the file
        silences.append((start, duration))
    return silences


def _describe_failure(stderr: str) -> str:
    if "does not contain any stream" in stderr or "matches no streams" in stderr:
        return "This file has no audio track"
    if "Invalid data found" in stderr:
        return "Unsupported or corrupt file"
    lines = stderr.strip().splitlines()
    return f"Could not read this file ({lines[-1] if lines else 'unknown ffmpeg error'})"
