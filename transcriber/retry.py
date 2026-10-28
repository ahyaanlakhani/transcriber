"""Retry policy shared by transcription and notes jobs."""
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TypeVar

from .providers.base import RateLimited, TransientError

T = TypeVar("T")

TRANSIENT_WAITS_SEC = (2.0, 8.0, 30.0)
WAIT_STEP_SEC = 5.0


def resume_message(seconds: float) -> str:
    resume_at = datetime.now() + timedelta(seconds=seconds)
    return f"Rate limit reached — resuming at {resume_at:%H:%M}"


def call_with_retries(fn: Callable[[], T], *, wait: Callable[[float], None],
                      on_rate_limited: Callable[[float], None], on_resumed: Callable[[], None]) -> T:
    """Run fn: wait out rate limits (no cap) and retry transient errors up to 3 times."""
    failures = 0
    while True:
        try:
            return fn()
        except RateLimited as exc:
            on_rate_limited(exc.retry_after)
            wait(exc.retry_after)
            on_resumed()
        except TransientError:
            if failures >= len(TRANSIENT_WAITS_SEC):
                raise
            wait(TRANSIENT_WAITS_SEC[failures])
            failures += 1


def wait_interruptibly(seconds: float, *, sleep: Callable[[float], None], clock: Callable[[], float],
                       check: Callable[[], None]) -> None:
    """Sleep in short steps, calling check() before each one; check() raises to abort the wait."""
    deadline = clock() + seconds
    while (remaining := deadline - clock()) > 0:
        check()
        sleep(min(WAIT_STEP_SEC, remaining))
    check()
