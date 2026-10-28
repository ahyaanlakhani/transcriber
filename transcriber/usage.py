"""Groq's free-tier limits refill steadily, like a bucket, not all at once at a set time. Its headers show it: with
2,000 requests a day, one used request comes back after 43.2 s (86,400 / 2,000). So 8 h of audio a day comes back
at 1 h every 3 h, and you're full again (window x used / limit) after you stop."""
from datetime import datetime, timedelta


def bucket(events: list[tuple[datetime, float]], capacity: float, window_sec: float, now: datetime) -> dict:
    """events: (when, seconds used), any order. Returns used_sec, available_sec and full_at (None when full)."""
    rate = capacity / window_sec  # seconds of allowance that come back per second
    start = now - timedelta(seconds=window_sec)  # anything older has refilled completely
    level, clock = capacity, start
    for when, seconds in sorted(e for e in events if e[0] > start):
        level = min(capacity, level + (when - clock).total_seconds() * rate) - seconds
        clock = when
    level = min(capacity, level + (now - clock).total_seconds() * rate)
    used = max(0.0, capacity - level)
    full_at = now + timedelta(seconds=used / rate) if used > 0.5 else None
    return {"used_sec": round(used, 1), "available_sec": round(max(0.0, level), 1), "full_at": full_at}
