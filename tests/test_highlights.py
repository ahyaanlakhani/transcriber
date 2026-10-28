"""Highlights reel: the moments that matter, cut from the real recording, in the speaker's own voice."""
import pytest

from transcriber import audio
from transcriber.notes.model import notes_from_writer
from transcriber.providers.base import Segment
from transcriber.recap.highlights import GAP_SEC, Clip, make_highlights, pick_clips

from .helpers import synth_audio


def seg(start, end, text="words"):
    return Segment(start, end, text, -0.1, 0.01, 1.0)


# a 10-minute lecture in 5-second sentences
SEGMENTS = [seg(t, t + 5) for t in range(0, 600, 5)]


def lecture(*items):
    return notes_from_writer({"lecture": [{"heading": "Topic", "kind": kind, "term": "T" if kind == "definition" else None,
                                           "text": text, "cites": [cite]} for kind, text, cite in items]})


def test_most_important_moments_first_then_in_order():
    notes = lecture(("point", "A point.", "[01:00]"), ("stressed", "On the exam.", "[05:00]"),
                    ("definition", "Means this.", "[02:00]"), ("example", "Like that.", "[08:00]"))
    clips = pick_clips(notes, SEGMENTS, max_total=60)
    # room for three 20 s clips: stressed, definition, then example; played in the order they were said
    assert [c.start for c in clips] == [120.0, 300.0, 480.0]
    assert [c.label for c in clips] == ["T: Means this.", "On the exam.", "Like that."]
    assert all(c.end - c.start == pytest.approx(20.0) for c in clips)


def test_clips_end_on_a_sentence_and_overlaps_merge():
    notes = lecture(("stressed", "First.", "[01:00]"), ("stressed", "Right after.", "[01:12]"))
    clips = pick_clips(notes, SEGMENTS)
    assert len(clips) == 1 and clips[0].start == 60.0 and clips[0].end == 95.0  # 60-80 and 70-95, joined
    assert clips[0].label == "First. / Right after."


def test_nothing_to_cut_without_cited_items():
    assert pick_clips(notes_from_writer({"summary": [{"text": "Gist", "cites": []}]}), SEGMENTS) == []


def test_conversations_use_their_advice():
    notes = notes_from_writer({"about_them": [{"text": "Build things early.", "cites": ["[03:00]"]}]})
    assert [c.start for c in pick_clips(notes, SEGMENTS)] == [180.0]


def test_make_highlights_joins_the_clips(tmp_path):
    source = tmp_path / "audio.flac"
    audio.normalize(synth_audio(tmp_path / "a.wav", [("tone", 30.0)]), source)
    dst = tmp_path / "highlights.mp3"
    seconds = make_highlights(source, [Clip(2.0, 7.0, "a"), Clip(10.0, 14.0, "b")], dst, tmp_path / "work")
    assert seconds == pytest.approx(9.0 + GAP_SEC, abs=0.2)
    assert audio.probe_duration(dst) == pytest.approx(seconds, abs=0.2)
    assert not (tmp_path / "work").exists()


def test_moments_cited_twice_dont_use_up_the_time():
    notes = lecture(("stressed", "Same moment.", "[01:00]"), ("definition", "Same moment again.", "[01:00]"),
                    ("fact", "Elsewhere.", "[04:00]"), ("example", "Further on.", "[07:00]"))
    clips = pick_clips(notes, SEGMENTS, max_total=60)
    assert [c.start for c in clips] == [60.0, 240.0, 420.0]
