import pytest

from transcriber.providers.base import PermanentError, RateLimited, TransientError
from transcriber.retry import call_with_retries, resume_message, wait_interruptibly


class Script:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def run(fn):
    log = []
    result = call_with_retries(fn, wait=lambda s: log.append(("wait", s)),
                               on_rate_limited=lambda s: log.append(("limited", s)),
                               on_resumed=lambda: log.append(("resumed",)))
    return result, log


def test_returns_first_success():
    assert run(Script("ok")) == ("ok", [])


def test_waits_out_rate_limits():
    result, log = run(Script(RateLimited(30.0), "ok"))
    assert result == "ok"
    assert log == [("limited", 30.0), ("wait", 30.0), ("resumed",)]


def test_retries_transient_errors_with_backoff():
    script = Script(TransientError("a"), TransientError("b"), "ok")
    result, log = run(script)
    assert result == "ok" and script.calls == 3
    assert log == [("wait", 2.0), ("wait", 8.0)]


def test_gives_up_after_three_transient_retries():
    script = Script(*[TransientError("down")] * 4)
    with pytest.raises(TransientError):
        run(script)
    assert script.calls == 4


def test_permanent_errors_pass_straight_through():
    script = Script(PermanentError("bad key"))
    with pytest.raises(PermanentError):
        run(script)
    assert script.calls == 1


def test_resume_message():
    assert resume_message(60).startswith("Rate limit reached — resuming at ")


def test_wait_interruptibly_sleeps_in_steps_and_checks():
    now = [0.0]
    sleeps, checks = [], []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    wait_interruptibly(12.0, sleep=sleep, clock=lambda: now[0], check=lambda: checks.append(now[0]))
    assert sleeps == [5.0, 5.0, 2.0]
    assert len(checks) == 4


def test_wait_interruptibly_stops_when_check_raises():
    class Stop(Exception):
        pass

    def check():
        raise Stop()

    with pytest.raises(Stop):
        wait_interruptibly(10.0, sleep=lambda s: None, clock=lambda: 0.0, check=check)
