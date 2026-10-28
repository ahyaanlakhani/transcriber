"""Decide where to cut a long recording into chunks (pure logic, no I/O)."""

TARGET_SEC = 600.0   # aim for ~10-minute chunks
WINDOW_SEC = 60.0    # look this far back from each target boundary for a silence
MAX_TAIL_SEC = 660.0  # never leave a final chunk shorter than MAX_TAIL_SEC - TARGET_SEC


def plan_chunks(
    duration: float,
    silences: list[tuple[float, float]],
    target: float = TARGET_SEC,
    window: float = WINDOW_SEC,
    max_tail: float = MAX_TAIL_SEC,
) -> list[tuple[float, float]]:
    """Return contiguous (start, end) chunks covering [0, duration].

    Each cut lands at the middle of the longest silence inside the window
    [boundary - window, boundary], or exactly at the boundary if there is none.
    """
    chunks: list[tuple[float, float]] = []
    start = 0.0
    while duration - start > max_tail:
        boundary = start + target
        cut = _best_cut(silences, boundary - window, boundary)
        chunks.append((start, cut))
        start = cut
    chunks.append((start, duration))
    return chunks


def _best_cut(silences: list[tuple[float, float]], lo: float, hi: float) -> float:
    best: tuple[float, float] | None = None
    for silence_start, silence_end in silences:
        clipped_start, clipped_end = max(silence_start, lo), min(silence_end, hi)
        if clipped_end > clipped_start and (best is None or clipped_end - clipped_start > best[1] - best[0]):
            best = (clipped_start, clipped_end)
    return hi if best is None else (best[0] + best[1]) / 2
