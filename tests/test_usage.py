"""Groq's free tier refills steadily (like a bucket), not all at once: 8 h of audio a day comes back at 1 h every
3 h. These tests pin down what's available now and exactly when it's full again."""
from datetime import UTC, datetime, timedelta

import pytest

from transcriber.usage import bucket

DAY, CAP = 86_400, 8 * 3600
NOON = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_nothing_used_means_full():
    state = bucket([], CAP, DAY, NOON)
    assert state == {"used_sec": 0.0, "available_sec": CAP, "full_at": None}


def test_two_hours_used_at_noon_is_back_by_six_the_next_morning():
    state = bucket([(NOON, 2 * 3600)], CAP, DAY, NOON)
    assert state["used_sec"] == pytest.approx(2 * 3600)
    assert state["full_at"] == NOON + timedelta(hours=6)  # 2 h x 3 h refill per hour


def test_it_refills_while_you_wait():
    later = NOON + timedelta(hours=3)
    state = bucket([(NOON, 2 * 3600)], CAP, DAY, later)
    assert state["used_sec"] == pytest.approx(3600)  # one hour came back
    assert state["available_sec"] == pytest.approx(7 * 3600)
    assert state["full_at"] == NOON + timedelta(hours=6)


def test_use_spread_out_refills_in_between():
    events = [(NOON, 3600), (NOON + timedelta(hours=1.5), 3600)]  # half an hour came back in between
    state = bucket(events, CAP, DAY, NOON + timedelta(hours=1.5))
    assert state["used_sec"] == pytest.approx(1.5 * 3600)
    assert state["full_at"] == NOON + timedelta(hours=1.5 + 4.5)


def test_use_from_days_ago_is_forgotten():
    state = bucket([(NOON - timedelta(days=2), 8 * 3600)], CAP, DAY, NOON)
    assert state["used_sec"] == 0 and state["full_at"] is None


def test_the_hourly_limit_refills_within_the_hour():
    state = bucket([(NOON, 1800)], 7200, 3600, NOON)  # Groq: 2 h of audio per hour
    assert state["full_at"] == NOON + timedelta(minutes=15)


def test_usage_api_says_when_it_is_full_again(tmp_path):
    from fastapi.testclient import TestClient

    from .test_app import build
    app = build(tmp_path)
    with TestClient(app) as client:
        app.state.store.record_usage(7200, "groq")
        day = client.get("/api/usage").json()["day"]
        assert day["used_sec"] == pytest.approx(7200, abs=5) and day["full_at"]
