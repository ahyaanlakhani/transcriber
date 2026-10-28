from transcriber.providers.base import RateLimited, Segment


def test_segment_round_trips_through_dict():
    segment = Segment(1.0, 2.5, "Hello.", -0.2, 0.01, 1.3)
    assert Segment.from_dict(segment.to_dict()) == segment


def test_rate_limited_carries_retry_after():
    error = RateLimited(12.5)
    assert error.retry_after == 12.5
