import struct

import pytest

from transcriber import audio
from transcriber.recap.voice import PAUSE_SEC, KokoroVoice, VoiceUnavailable


def test_pcm_to_mp3(tmp_path):
    pcm = b"".join(struct.pack("<h", 0) for _ in range(24000))  # 1 s at 24 kHz
    dst = tmp_path / "out.mp3"
    audio.pcm_to_mp3(pcm, 24000, dst)
    assert dst.exists() and audio.probe_duration(dst) == pytest.approx(1.0, abs=0.1)


class FakeKokoro:
    def __init__(self):
        self.calls = []

    def create(self, text, voice, speed, lang):
        import numpy as np
        self.calls.append((text, voice, speed, lang))
        return np.zeros(24000, dtype=np.float32), 24000  # 1 s per paragraph


def test_speak_joins_paragraphs_with_pauses(tmp_path):
    pytest.importorskip("numpy")
    voice = KokoroVoice(tmp_path / "kokoro")
    fake = FakeKokoro()
    voice._engine = fake
    seconds = voice.speak(["One.", "Two."], voice="af_heart", speed=1.1, dst=tmp_path / "recap.mp3")
    assert fake.calls == [("One.", "af_heart", 1.1, "en-us"), ("Two.", "af_heart", 1.1, "en-us")]
    assert seconds == pytest.approx(2 + PAUSE_SEC, abs=0.01)  # no pause after the last paragraph
    assert audio.probe_duration(tmp_path / "recap.mp3") == pytest.approx(seconds, abs=0.15)


def test_runs_gently_on_half_the_processor():
    pytest.importorskip("onnxruntime")
    from transcriber.recap.voice import session_options
    options = session_options(threads=2)
    assert (options.intra_op_num_threads, options.inter_op_num_threads) == (2, 1)


def test_low_priority_is_restored_afterwards():
    from transcriber.recap.voice import low_priority
    seen = []
    with low_priority(set_priority=lambda level: seen.append(level) or "before"):
        seen.append("work")
    assert seen == ["low", "work", "before"]


def test_downloads_missing_model_files_once(tmp_path):
    fetched = []

    def download(url, dst):
        fetched.append(dst.name)
        dst.write_bytes(b"x")

    voice = KokoroVoice(tmp_path / "kokoro", download=download)
    statuses = []
    voice.fetch_model(statuses.append)
    voice.fetch_model(statuses.append)
    assert fetched == ["kokoro-v1.0.onnx", "voices-v1.0.bin"] and voice.ready()
    assert statuses == ["Downloading the voice (first time only)"]


def test_failed_download_is_a_clear_error(tmp_path):
    def download(url, dst):
        raise OSError("no internet")

    with pytest.raises(VoiceUnavailable, match="couldn't download the voice"):
        KokoroVoice(tmp_path / "kokoro", download=download).fetch_model(lambda _: None)
