"""You and Others: who is speaking in a recorded call, decided by which audio source was loud, never by the voice.

A meeting recording keeps your microphone and the call's sound (what comes out of your speakers) as two separate
tracks. A line is "You" when your microphone carried it and "Others" when the call's sound did. Nothing here
listens to, learns or compares voices."""
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np

from . import audio
from .providers.base import Segment

YOU, OTHERS = "SPEAKER_00", "SPEAKER_01"
NAMES = {YOU: "You", OTHERS: "Others"}
YOU_FILE, OTHERS_FILE = "you.flac", "others.flac"
SAMPLE_RATE = 16000
FRAME_SAMPLES = 320  # 20 ms
FRAMES_PER_SEC = SAMPLE_RATE // FRAME_SAMPLES
QUIET = 150.0   # a frame quieter than this (16-bit loudness) is silence
YOU_SHARE = 0.8  # you are speaking when your mic is at least this much as loud as the call; through speakers the
#                  call leaks into your mic more quietly than your own voice


def _loudness(levels, start: float, end: float) -> float:
    first = int(start * FRAMES_PER_SEC)
    last = max(int(end * FRAMES_PER_SEC), first + 1)
    chunk = levels[first:last]
    return sum(chunk) / len(chunk) if len(chunk) else 0.0


def label_speaker(you_levels, others_levels, start: float, end: float) -> str:
    you, others = _loudness(you_levels, start, end), _loudness(others_levels, start, end)
    if others < QUIET:
        return YOU if you >= QUIET else OTHERS
    if you < QUIET:
        return OTHERS
    return YOU if you >= YOU_SHARE * others else OTHERS


def label_segments(segments: list[Segment], you_levels, others_levels) -> list[Segment]:
    return [replace(s, speaker=label_speaker(you_levels, others_levels, s.start, s.end)) for s in segments]


def frame_levels(samples: np.ndarray) -> np.ndarray:
    """Loudness (RMS) of each whole 20 ms frame of 16-bit mono audio."""
    whole = len(samples) // FRAME_SAMPLES * FRAME_SAMPLES
    frames = samples[:whole].astype(np.float32).reshape(-1, FRAME_SAMPLES)
    return np.sqrt((frames * frames).mean(axis=1)) if len(frames) else np.zeros(0, np.float32)


def levels_of_file(path: Path) -> np.ndarray:
    done = subprocess.run([audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-f", "s16le",
                           "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"], capture_output=True, check=True)
    return frame_levels(np.frombuffer(done.stdout, dtype=np.int16))


def label_recording(folder: Path, segments: list[Segment]) -> list[Segment] | None:
    """The segments labelled You or Others from the recording's two tracks, or None if it has only one."""
    you, others = folder / YOU_FILE, folder / OTHERS_FILE
    if not (you.exists() and others.exists()):
        return None
    return label_segments(segments, levels_of_file(you), levels_of_file(others))
