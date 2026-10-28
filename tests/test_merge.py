"""Merging recordings of the same session made on several devices."""
import pytest

from transcriber.merge import NotSameSession, estimate_offset, merge_words, to_segments, words_from_segments
from transcriber.providers.base import Segment

SENTENCES = [
    "Okay everyone let's get started with today's lecture on the internet of things.",
    "First we will look at sensors and how they send data to the cloud.",
    "Then we will talk about how factories use this data every single day.",
    "Your assignment is due next Friday so please start early this time.",
    "Any questions before we move on to the case study about electric cars.",
    "The case study looks at how Chinese companies scaled their production lines.",
]


def source(texts, start=0.0, conf=-0.2, step=6.0):
    """Segments 5 s long every `step` seconds, starting at `start` on that device's clock."""
    return [Segment(start + i * step, start + i * step + 5.0, text, conf, 0.01, 1.2) for i, text in enumerate(texts)]


def text_of(words):
    return " ".join(w.text for w in words)


def test_offset_between_devices_that_started_at_different_times():
    ref = words_from_segments(source(SENTENCES), 0)
    phone = words_from_segments(source(SENTENCES, start=-52.0), 1)  # the phone started 52 s later
    offset, share = estimate_offset(ref, phone)
    assert offset == pytest.approx(-52.0, abs=0.5) and share > 0.9


def test_unrelated_recordings_are_refused():
    ref = words_from_segments(source(SENTENCES), 0)
    other = words_from_segments(source(["Completely different words about cooking pasta at home tonight."] * 6), 1)
    with pytest.raises(NotSameSession):
        estimate_offset(ref, other)


def test_agreement_passes_through():
    merged, shares = merge_words([source(SENTENCES), source(SENTENCES, start=-10.0)], judge=None)
    assert text_of(merged) == " ".join(SENTENCES)
    assert sum(shares) == pytest.approx(1.0)


def test_confident_version_wins_and_times_are_on_the_reference_clock():
    garbled = list(SENTENCES)
    garbled[2] = "Then we will trace it the car camera image image every single day."
    ref = source(garbled, conf=-0.2)
    ref[2] = Segment(12.0, 17.0, garbled[2], -1.1, 0.01, 1.2)  # Whisper was unsure about the garbled line
    phone = source(SENTENCES, start=-30.0, conf=-0.2)
    merged, _ = merge_words([ref, phone], judge=None)
    assert "factories use this data" in text_of(merged) and "camera image" not in text_of(merged)
    took = [w for w in merged if w.text == "factories"][0]
    assert 12.0 <= took.t <= 17.0  # the phone's words were moved onto the reference's timeline


def test_close_calls_go_to_the_judge_in_batches():
    a, b = list(SENTENCES), list(SENTENCES)
    a[1] = "First we will look at sensors and how they sent data to the crowd."
    a[4] = "Any questions before we move onto the case study about electric cards."
    calls = []

    def judge(items):
        calls.append(items)
        return [1 for _ in items]  # option 1 = the second device

    merged, _ = merge_words([source(a), source(b)], judge=judge)
    assert text_of(merged) == " ".join(SENTENCES)
    assert len(calls) == 1 and len(calls[0]) >= 2  # one batched request
    item = calls[0][0]
    assert set(item) == {"id", "before", "after", "options"} and len(item["options"]) == 2


def test_judge_failure_falls_back_to_confidence():
    a, b = list(SENTENCES), list(SENTENCES)
    a[1] = "First we will look at sensors and how they sent data to the crowd."

    def broken(items):
        raise RuntimeError("model down")

    merged, _ = merge_words([source(a, conf=-0.2), source(b, conf=-0.15)], judge=broken)
    assert "to the cloud" in text_of(merged)  # slightly more confident second device


def test_majority_wins_with_three_devices():
    a, b, c = list(SENTENCES), list(SENTENCES), list(SENTENCES)
    b[3] = "Your assignment is due next Thursday so please start early this time."
    calls = []
    merged, shares = merge_words([source(b, conf=-0.1), source(a, conf=-0.3), source(c, conf=-0.3)],
                                 judge=lambda items: calls.append(items) or [0] * len(items))
    assert "next Friday" in text_of(merged) and calls == []
    assert len(shares) == 3


def test_gaps_are_filled_from_another_device():
    late = SENTENCES[2:]  # this device started late: missed the first two sentences
    merged, shares = merge_words([source(SENTENCES[:4] + ["(noise)"] * 0, conf=-0.2) + [],
                                  source(late, start=12.0, conf=-0.2)], judge=None)
    assert text_of(merged).startswith("Okay everyone")
    assert "electric cars" in text_of(merged) and "production lines" in text_of(merged)


def test_low_confidence_extra_words_are_not_added():
    extra = list(SENTENCES)
    extra.insert(3, "Thank you for watching please subscribe to the channel.")
    noisy = source(extra, conf=-0.2)
    noisy[3] = Segment(18.0, 23.0, extra[3], -1.4, 0.7, 1.2)
    merged, _ = merge_words([source(SENTENCES, conf=-0.2), noisy], judge=lambda items: [0] * len(items))
    assert "subscribe" not in text_of(merged)


def test_segments_rebuilt_from_words():
    merged, _ = merge_words([source(SENTENCES)], judge=None)
    segments = to_segments(merged)
    assert [s.text for s in segments] == SENTENCES
    assert segments[1].start == pytest.approx(6.0)


def test_a_device_has_no_say_before_it_started_recording():
    intro = ["Good morning everybody the paper burden is now off me and on you guys.",
             "So we have got three things to do today in this long session."]
    laptop = source(intro + SENTENCES, conf=-0.4)  # started first: has the intro
    better = list(SENTENCES)
    phone = source(better, start=0.0, conf=-0.1)  # started 12 s later; clearer audio
    merged, _ = merge_words([laptop, phone], judge=None, durations=[50.0, 36.0])
    text = text_of(merged)
    assert text.startswith("Good morning everybody") and "electric cars" in text


def test_llm_judge_waits_out_rate_limits():
    from transcriber.merge import llm_judge
    from transcriber.providers.base import RateLimited

    from .helpers import FakeLLM
    waits = []
    llm = FakeLLM("groq:x", [RateLimited(7.0), {"choices": [{"id": 0, "pick": 1}, {"id": 1, "pick": 0}]}])
    picks = llm_judge(llm, wait=waits.append)([{"id": 0, "options": ["a", "b"]}, {"id": 1, "options": ["c", "d"]}])
    assert picks == [1, 0] and waits == [7.0]


def test_llm_judge_waits_out_rate_limits():
    from transcriber.merge import llm_judge
    from transcriber.providers.base import RateLimited

    from .helpers import FakeLLM
    waits = []
    llm = FakeLLM("groq:x", [RateLimited(7.0), {"choices": [{"id": 0, "pick": 1}, {"id": 1, "pick": 0}]}])
    picks = llm_judge(llm, wait=waits.append)([{"id": 0, "options": ["a", "b"]}, {"id": 1, "options": ["c", "d"]}])
    assert picks == [1, 0] and waits == [7.0]
