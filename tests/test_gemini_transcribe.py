"""Gemini 3.5 Transcribe provider, conversion of a second language to Roman letters, and mixed routing."""
from types import SimpleNamespace

import pytest

from transcriber.providers.base import PermanentError, RateLimited, Segment, TransientError
from transcriber.providers.gemini_transcribe import GeminiTranscriber, group_words, map_error, segments_from
from transcriber.romanize import Romanizer, has_non_latin_script

from .helpers import FakeLLM


def word(text, start, end, type_="word_info"):
    return {"type": type_, "text": text, "start_offset": f"{start:.3f}s", "end_offset": f"{end:.3f}s",
            "speaker": "spk_1"}


def interaction(*words):
    return SimpleNamespace(output_text="", steps=[SimpleNamespace(content=[SimpleNamespace(annotations=list(words))])])


class FakeFiles:
    def __init__(self):
        self.uploaded, self.deleted = [], []

    def upload(self, file):
        self.uploaded.append(file)
        return SimpleNamespace(uri="files/abc", mime_type="audio/flac", name="files/abc")

    def delete(self, name):
        self.deleted.append(name)


class FakeInteractions:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def client(result=None, error=None):
    return SimpleNamespace(files=FakeFiles(), interactions=FakeInteractions(result, error))


def test_words_become_segments_at_pauses_and_sentence_ends(tmp_path):
    audio = tmp_path / "a.flac"
    audio.write_bytes(b"x")
    words = [word("Okay", 0.0, 0.4), word("let's", 0.5, 0.8), word("start.", 0.9, 1.3),
             word("Yeh", 3.0, 3.3), word("bandha", 3.4, 3.8), word("kya", 3.9, 4.1)]
    c = client(interaction(*words))
    segments = GeminiTranscriber("k", client=c).transcribe(audio, language=None, prompt="Zorvath")
    assert [s.text for s in segments] == ["Okay let's start.", "Yeh bandha kya"]
    assert segments[1].start == pytest.approx(3.0) and segments[1].end == pytest.approx(4.1)
    request = c.interactions.calls[0]
    config = request["generation_config"]["transcription_config"]
    assert request["model"] == "gemini-3.5-transcribe" and config["language_codes"] == []
    assert config["mode"] == {"type": "verbatim", "timestamp_granularities": ["word"]}
    assert "custom_vocabulary" not in str(request)  # not allowed together with word timestamps
    assert c.files.deleted == ["files/abc"]  # tidied up after the call


def test_other_annotations_are_ignored():
    result = interaction(word("Hi.", 0.0, 0.3), word("", 0.3, 0.3, type_="speaker_change"))
    assert [s.text for s in segments_from(result)] == ["Hi."]


def test_long_runs_are_split():
    words = [(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(100)]
    segments = group_words(words)
    assert len(segments) >= 2 and all(s.end - s.start <= 26 for s in segments)


@pytest.mark.parametrize("error, expected", [
    (Exception("429 RESOURCE_EXHAUSTED quota"), RateLimited),
    (Exception("API_KEY_INVALID: key not valid"), PermanentError),
    (Exception("connection reset"), TransientError),
])
def test_errors_map_to_retry_policy(error, expected):
    assert isinstance(map_error(error), expected)


def test_api_error_during_transcription_is_mapped(tmp_path):
    audio = tmp_path / "a.flac"
    audio.write_bytes(b"x")
    with pytest.raises(RateLimited):
        GeminiTranscriber("k", client=client(error=Exception("429 RESOURCE_EXHAUSTED"))).transcribe(
            audio, language=None, prompt="")


def test_non_latin_script_detection():
    assert has_non_latin_script("ERP в этом chassis number") and not has_non_latin_script("only English here")


def test_any_non_latin_script_counts_and_accented_latin_does_not():
    assert has_non_latin_script("Привет, world") and has_non_latin_script("你好") and has_non_latin_script("Καλημέρα")
    assert not has_non_latin_script("café, señor, Ærø, ñandú") and not has_non_latin_script("numbers 123 and, punctuation!")


def test_romanizer_converts_only_non_latin_lines_in_order():
    segments = [Segment(0, 1, "The car is ordered."), Segment(1, 2, "ERP этот номер зачем был"),
                Segment(2, 3, "Okay.")]
    llm = FakeLLM("groq:x", [{"lines": ["ERP etot nomer zachem byl"]}])
    out = Romanizer(llm, wait=lambda s: None)(segments)
    assert [s.text for s in out] == ["The car is ordered.", "ERP etot nomer zachem byl", "Okay."]
    assert "ERP этот" in llm.calls[0]["user"]  # the model saw the line in the other script


def test_romanizer_keeps_script_when_the_reply_does_not_match():
    segments = [Segment(0, 1, "это один"), Segment(1, 2, "и вторая строка")]
    llm = FakeLLM("groq:x", [{"lines": ["only one line"]}])
    assert [s.text for s in Romanizer(llm, wait=lambda s: None)(segments)] == ["это один", "и вторая строка"]


def test_romanizer_splits_a_batch_the_model_cannot_answer_and_keeps_only_the_lines_that_still_fail():
    segments = [Segment(i, i + 1, f"строка {i}") for i in range(4)]
    llm = FakeLLM("groq:x", [PermanentError("Groq rejected the request (HTTP 400)"), {"lines": ["line 0", "line 1"]},
                             {"lines": ["only one"]}, {"lines": ["line 2"]}, PermanentError("HTTP 400")])
    out = Romanizer(llm, wait=lambda s: None)(segments)
    assert [s.text for s in out] == ["line 0", "line 1", "line 2", "строка 3"]
    assert all(call["effort"] == "low" for call in llm.calls)


def test_romanizer_keeps_script_when_the_model_fails():
    segments = [Segment(0, 1, "это один")]
    llm = FakeLLM("groq:x", [TransientError("down")] * 5)
    assert Romanizer(llm, wait=lambda s: None)(segments)[0].text == "это один"


def test_romanizer_batches_long_transcripts():
    segments = [Segment(i, i + 1, f"строка {i}") for i in range(45)]
    replies = [{"lines": [f"line {i}" for i in range(40)]}, {"lines": [f"line {i}" for i in range(40, 45)]}]
    llm = FakeLLM("groq:x", replies)
    out = Romanizer(llm, wait=lambda s: None)(segments)
    assert len(llm.calls) == 2 and out[0].text == "line 0" and out[44].text == "line 44"


# --- routing in the pipeline

from .helpers import FakeTranscriber  # noqa: E402
ONE_CHUNK = [("tone", 6.0)]
from .test_pipeline import add_recording, env, make_pipeline  # noqa: E402,F401


class FakeGemini:
    name = "gemini/gemini-3.5-transcribe"

    def __init__(self):
        self.calls = []

    def transcribe(self, path, *, language, prompt):
        self.calls.append(language)
        return [Segment(0.0, 2.0, "The car is ordered.", -0.1, 0.01, 1.0),
                Segment(2.5, 4.5, "ERP этот номер зачем был", -0.1, 0.01, 1.0)]


def test_mixed_recording_uses_gemini_and_romanizes(env):
    from transcriber.config import MIXED
    rid = add_recording(env, parts=ONE_CHUNK, language=MIXED)
    gemini, groq = FakeGemini(), FakeTranscriber()
    romanizer = Romanizer(FakeLLM("groq:x", [{"lines": ["ERP etot nomer zachem byl"]}]), wait=lambda s: None)
    make_pipeline(env, groq, mixed_transcriber=gemini, romanizer=romanizer).process(rid)
    transcript = env.store.get_transcript(rid)
    assert groq.calls == [] and gemini.calls == [None]  # auto-detect language for Gemini
    assert transcript.model == "gemini/gemini-3.5-transcribe"
    assert [s.text for s in transcript.segments] == ["The car is ordered.", "ERP etot nomer zachem byl"]
    assert env.store.get_recording(rid).status == "done"


def test_mixed_without_gemini_key_falls_back_to_groq(env):
    from transcriber.config import MIXED
    rid = add_recording(env, parts=ONE_CHUNK, language=MIXED)
    groq = FakeTranscriber()
    make_pipeline(env, groq, mixed_transcriber=None).process(rid)
    assert len(groq.calls) == 1 and env.store.get_transcript(rid).model == "fake/model"


def test_english_recordings_never_touch_gemini(env):
    rid = add_recording(env, parts=ONE_CHUNK, language="en")
    gemini, groq = FakeGemini(), FakeTranscriber()
    make_pipeline(env, groq, mixed_transcriber=gemini).process(rid)
    assert gemini.calls == [] and len(groq.calls) == 1


def test_mixed_live_recording_goes_to_the_normal_pipeline(tmp_path):
    from transcriber.config import MIXED
    from transcriber.live import LiveManager
    from transcriber.store import Store
    from .test_live_recording import SPEECH, pieces, webm_bytes
    store = Store(tmp_path / "app.db")
    try:
        manager = LiveManager(store, FakeTranscriber(), tmp_path / "live", tmp_path / "recordings",
                              start_thread=False)
        sid = manager.create(title="Mixed lecture", subject_id=None, language=MIXED)
        for seq, piece in enumerate(pieces(webm_bytes(tmp_path, SPEECH))):
            manager.add_chunk(sid, seq, piece)
        manager.wait_for_audio(sid)
        while manager.step():
            pass
        rec = store.get_recording(manager.finish(sid))
        assert rec.language == MIXED and rec.status == "queued" and store.get_transcript(rec.id) is None
    finally:
        store.close()


def test_romanizer_stops_asking_once_the_provider_refuses_everything():
    segments = [Segment(i, i + 1, f"строка {i}") for i in range(100)]
    llm = FakeLLM("groq:x", [PermanentError("Invalid Groq API key (check .env)")] * 300)
    out = Romanizer(llm, wait=lambda s: None)(segments)
    assert len(llm.calls) == 1 and out[0].text == segments[0].text  # one try, not hundreds, and nothing lost


def test_the_romanizer_names_the_language_it_was_told_about():
    llm = FakeLLM("groq:x", [{"lines": ["privet"]}])
    Romanizer(llm, wait=lambda s: None, language="Russian")([Segment(0, 1, "привет")])
    assert "Russian" in llm.calls[0]["system"]
    llm = FakeLLM("groq:x", [{"lines": ["privet"]}])
    Romanizer(llm, wait=lambda s: None)([Segment(0, 1, "привет")])
    assert "other language" in llm.calls[0]["system"]  # no language set: the instruction stays general
