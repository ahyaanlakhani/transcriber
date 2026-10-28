"""Render a transcript for the notes models (with [mm:ss] markers), and split long ones."""
from ..export import format_timestamp
from ..postprocess import Paragraph

PART_MAX_CHARS = 60_000  # ≈ 15k tokens: fits Cerebras' free 30k-tokens-per-minute limit with room to spare


def render(paragraphs: list[Paragraph], long_form: bool, part_starts: list[float] | None = None) -> str:
    """Paragraphs with [mm:ss] markers; for a combined session, '— Part N —' where each recording begins."""
    blocks: list[str] = []
    upcoming = list(enumerate(part_starts or [], 1))
    for paragraph in paragraphs:
        marker = None
        while upcoming and paragraph.start >= upcoming[0][1] - 0.5:
            marker = upcoming.pop(0)[0]
        if marker is not None:
            blocks.append(f"— Part {marker} —")
        who = f"{paragraph.speaker}: " if paragraph.speaker else ""
        blocks.append(f"{format_timestamp(paragraph.start, long_form)} {who}{paragraph.text}")
    return "\n\n".join(blocks)


def split_parts(paragraphs: list[Paragraph], max_chars: int = PART_MAX_CHARS) -> list[list[Paragraph]]:
    parts: list[list[Paragraph]] = []
    current: list[Paragraph] = []
    size = 0
    for paragraph in paragraphs:
        length = len(paragraph.text) + 12  # timestamp marker and separators
        if current and size + length > max_chars:
            parts.append(current)
            current, size = [], 0
        current.append(paragraph)
        size += length
    if current:
        parts.append(current)
    return parts
