from transcriber.postprocess import Paragraph, filter_hallucinations, named, offset, speaker_names, to_paragraphs
from transcriber.providers.base import Segment


def seg(start, end, text, logprob=-0.2, nospeech=0.01):
    return Segment(start, end, text, logprob, nospeech, 1.2)


def test_drops_probable_silence():
    segments = [seg(0, 2, "Hello team."), seg(2, 4, "Thanks for watching!", logprob=-1.4, nospeech=0.8)]
    assert [s.text for s in filter_hallucinations(segments)] == ["Hello team."]


def test_keeps_high_no_speech_when_confident():
    segments = [seg(0, 2, "Yes.", logprob=-0.3, nospeech=0.9)]
    assert filter_hallucinations(segments) == segments


def test_keeps_segments_without_scores():
    segments = [Segment(0, 1, "Hi.")]
    assert filter_hallucinations(segments) == segments


def test_drops_blank_segments():
    assert filter_hallucinations([seg(0, 1, "  "), seg(1, 2, "Ok.")]) == [seg(1, 2, "Ok.")]


def test_collapses_runs_of_three_or_more_repeats():
    segments = [seg(0, 1, "Okay."), seg(1, 2, "Thank you."), seg(2, 3, "thank you"),
                seg(3, 4, "Thank you!"), seg(4, 5, "Bye.")]
    assert [s.text for s in filter_hallucinations(segments)] == ["Okay.", "Thank you.", "Bye."]


def test_keeps_pairs_of_repeats():
    segments = [seg(0, 1, "Yes."), seg(1, 2, "Yes.")]
    assert filter_hallucinations(segments) == segments


def test_offset_shifts_times_without_mutating():
    original = [seg(1, 2, "a")]
    shifted = offset(original, 600)
    assert (shifted[0].start, shifted[0].end) == (601, 602)
    assert original[0].start == 1


def test_paragraph_breaks_on_gap():
    segments = [seg(0, 2, "One."), seg(2.5, 4, "Two."), seg(6.5, 8, "Three.")]
    assert to_paragraphs(segments) == [Paragraph(0, "One. Two."), Paragraph(6.5, "Three.")]


def test_paragraph_breaks_after_sixty_seconds():
    segments = [seg(i * 10, i * 10 + 9.5, f"S{i}.") for i in range(8)]
    assert to_paragraphs(segments) == [
        Paragraph(0, "S0. S1. S2. S3. S4. S5."),
        Paragraph(60, "S6. S7."),
    ]


def test_no_segments_no_paragraphs():
    assert to_paragraphs([]) == []


def test_paragraph_breaks_on_speaker_change():
    segments = [Segment(0, 1, "Hi.", speaker="SPEAKER_00"), Segment(1.2, 2, "Hello.", speaker="SPEAKER_01"),
                Segment(2.1, 3, "How are you?", speaker="SPEAKER_01")]
    assert to_paragraphs(segments) == [Paragraph(0, "Hi.", "SPEAKER_00"),
                                       Paragraph(1.2, "Hello. How are you?", "SPEAKER_01")]


def test_speaker_names_default_by_first_appearance_and_custom_wins():
    names = speaker_names(["SPEAKER_01", "SPEAKER_00", "SPEAKER_01"], {"SPEAKER_00": "Dr. Khan"})
    assert names == {"SPEAKER_01": "Speaker 1", "SPEAKER_00": "Dr. Khan"}


def test_named_replaces_labels():
    paragraphs = [Paragraph(0, "Hi.", "SPEAKER_00"), Paragraph(5, "Ok.", None)]
    assert named(paragraphs, {"SPEAKER_00": "Ana"}) == [Paragraph(0, "Hi.", "Ana"), Paragraph(5, "Ok.", None)]
