import pytest

from transcriber import audio
from transcriber.providers.base import PermanentError

from .helpers import ffmpeg_info, make_video, synth_audio


def test_normalize_produces_16k_mono_flac(tmp_path):
    src = synth_audio(tmp_path / "in.wav", [("tone", 2.0)], sample_rate=44100, channels=2)
    dst = tmp_path / "out.flac"
    audio.normalize(src, dst)
    info = ffmpeg_info(dst)
    assert "flac" in info and "16000 Hz" in info and "mono" in info
    assert not (tmp_path / "out.flac.tmp").exists()


def test_normalize_extracts_audio_from_video(tmp_path):
    dst = tmp_path / "out.flac"
    audio.normalize(make_video(tmp_path / "v.mp4", 2.0), dst)
    assert audio.probe_duration(dst) == pytest.approx(2.0, abs=0.1)


def test_normalize_rejects_video_without_audio(tmp_path):
    with pytest.raises(PermanentError, match="no audio track"):
        audio.normalize(make_video(tmp_path / "v.mp4", with_audio=False), tmp_path / "out.flac")
    assert not (tmp_path / "out.flac").exists()


def test_normalize_rejects_non_media(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("not audio", encoding="utf-8")
    with pytest.raises(PermanentError, match="Unsupported or corrupt"):
        audio.normalize(notes, tmp_path / "out.flac")


def test_probe_duration(tmp_path):
    flac = tmp_path / "a.flac"
    audio.normalize(synth_audio(tmp_path / "a.wav", [("tone", 3.0), ("silence", 2.0)]), flac)
    assert audio.probe_duration(flac) == pytest.approx(5.0, abs=0.05)


def test_detect_silences(tmp_path):
    flac = tmp_path / "a.flac"
    audio.normalize(synth_audio(tmp_path / "a.wav", [("tone", 3.0), ("silence", 2.0), ("tone", 3.0)]), flac)
    [(start, end)] = audio.detect_silences(flac, audio.probe_duration(flac))
    assert start == pytest.approx(3.0, abs=0.1)
    assert end == pytest.approx(5.0, abs=0.1)


def test_trailing_silence_runs_to_end(tmp_path):
    flac = tmp_path / "a.flac"
    audio.normalize(synth_audio(tmp_path / "a.wav", [("tone", 2.0), ("silence", 2.0)]), flac)
    silences = audio.detect_silences(flac, 4.0)
    assert silences[-1][1] == 4.0


def test_parse_silences():
    stderr = (
        "[silencedetect @ 0x1] silence_start: 3\n"
        "[silencedetect @ 0x1] silence_end: 5.000063 | silence_duration: 2.000062\n"
        "[silencedetect @ 0x1] silence_start: -0.01\n"
    )
    assert audio.parse_silences(stderr, 9.0) == [(3.0, 5.000063), (0.0, 9.0)]


def test_cut_extracts_range(tmp_path):
    flac = tmp_path / "a.flac"
    audio.normalize(synth_audio(tmp_path / "a.wav", [("tone", 8.0)]), flac)
    piece = tmp_path / "piece.flac"
    audio.cut(flac, 1.5, 4.0, piece)
    assert audio.probe_duration(piece) == pytest.approx(2.5, abs=0.05)
