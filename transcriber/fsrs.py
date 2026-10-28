"""Spaced repetition: when to ask you a card again. This follows the FSRS-5 memory model: each card has a stability
(days until you'd only remember it 90% of the time) and a difficulty; how well you remembered it sets the next gap.
The weights are the model's published defaults, written from memory of the reference, and are checked here only for
sensible behaviour (gaps grow when you remember, shrink when you forget), not against the reference code."""
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
RATINGS = {AGAIN: "Again", HARD: "Hard", GOOD: "Good", EASY: "Easy"}

W = (0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192, 1.01925, 1.9395, 0.11,
     0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621)
DECAY = -0.5
FACTOR = 19 / 81
DEFAULT_RETENTION = 0.9
MAX_INTERVAL_DAYS = 365
RELEARN_MINUTES = 10  # a card you forgot comes back later the same session


@dataclass
class Memory:
    stability: float | None = None  # None: never reviewed
    difficulty: float | None = None
    due: datetime | None = None
    last_review: datetime | None = None
    reps: int = 0
    lapses: int = 0


def retrievability(elapsed_days: float, stability: float) -> float:
    return (1 + FACTOR * max(elapsed_days, 0.0) / stability) ** DECAY


def interval_days(stability: float, retention: float = DEFAULT_RETENTION) -> float:
    return stability / FACTOR * (retention ** (1 / DECAY) - 1)


def _clamp_difficulty(d: float) -> float:
    return min(10.0, max(1.0, d))


def _initial_difficulty(rating: int) -> float:
    return _clamp_difficulty(W[4] - math.exp(W[5] * (rating - 1)) + 1)


def _next_difficulty(d: float, rating: int) -> float:
    moved = d + (-W[6] * (rating - 3)) * (10 - d) / 9  # harder ratings push it up, with less room near the top
    return _clamp_difficulty(W[7] * _initial_difficulty(EASY) + (1 - W[7]) * moved)


def _recalled_stability(d: float, s: float, r: float, rating: int) -> float:
    hard = W[15] if rating == HARD else 1.0
    easy = W[16] if rating == EASY else 1.0
    return s * (1 + math.exp(W[8]) * (11 - d) * s ** -W[9] * (math.exp(W[10] * (1 - r)) - 1) * hard * easy)


def _forgotten_stability(d: float, s: float, r: float) -> float:
    return min(s, W[11] * d ** -W[12] * ((s + 1) ** W[13] - 1) * math.exp(W[14] * (1 - r)))


def review(memory: Memory, rating: int, now: datetime | None = None, retention: float = DEFAULT_RETENTION) -> Memory:
    """The card after you answered it with `rating` (1 Again, 2 Hard, 3 Good, 4 Easy)."""
    if rating not in RATINGS:
        raise ValueError("Rate a card 1 to 4")
    now = now or datetime.now(UTC)
    if memory.stability is None:
        stability, difficulty = W[rating - 1], _initial_difficulty(rating)
        lapses = memory.lapses + (1 if rating == AGAIN else 0)
    else:
        elapsed = max((now - memory.last_review).total_seconds() / 86400, 0.0) if memory.last_review else 0.0
        r = retrievability(elapsed, memory.stability)
        difficulty = _next_difficulty(memory.difficulty, rating)
        if rating == AGAIN:
            stability = _forgotten_stability(memory.difficulty, memory.stability, r)
            lapses = memory.lapses + 1
        else:
            stability = _recalled_stability(memory.difficulty, memory.stability, r, rating)
            lapses = memory.lapses
    stability = max(stability, 0.01)
    if rating == AGAIN:
        due = now + timedelta(minutes=RELEARN_MINUTES)
    else:
        days = min(MAX_INTERVAL_DAYS, max(1, round(interval_days(stability, retention))))
        due = now + timedelta(days=days)
    return Memory(stability, difficulty, due, now, memory.reps + 1, lapses)


def preview(memory: Memory, now: datetime | None = None) -> dict[int, str]:
    """What each answer would mean, for the buttons: '10 min', '3 days'."""
    now = now or datetime.now(UTC)
    out = {}
    for rating in RATINGS:
        gap = review(memory, rating, now).due - now
        out[rating] = f"{round(gap.total_seconds() / 60)} min" if gap < timedelta(hours=1) else (
            f"{gap.days} day{'s' if gap.days != 1 else ''}" if gap >= timedelta(days=1) else f"{round(gap.total_seconds() / 3600)} h")
    return out
