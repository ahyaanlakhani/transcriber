"""Combine recordings of one session (recorded in parts, with pauses) into a single recording.

Nothing is transcribed again: the finished transcripts are stitched together with continuous timestamps,
and the audio is joined with a short silence where each pause was so the timestamps still play correctly.
"""
import uuid
from pathlib import Path

from . import audio
from .pipeline import AUDIO_FILE
from .postprocess import offset, to_paragraphs
from .providers.base import Segment
from .store import Recording, Store

GAP_SEC = 3.0  # silence marking each pause; longer than a paragraph break so parts never merge
COMBINED_MARK = " combined: "  # a combined recording's original name lists its parts: "3 combined: a.m4a, b.m4a, c.m4a"


def is_combined(original_filename: str) -> bool:
    return COMBINED_MARK in original_filename or original_filename.endswith("combined recordings")


def combine_recordings(store: Store, recordings_dir: Path, ids: list[str], title: str | None = None) -> Recording:
    if len(set(ids)) < 2:
        raise ValueError("Pick at least two recordings to combine")
    recordings = []
    for rid in dict.fromkeys(ids):
        rec = store.get_recording(rid)
        if rec is None or rec.deleted_at:
            raise ValueError("One of those recordings no longer exists or is in Recently Deleted")
        transcript = store.get_transcript(rid)
        if rec.status != "done" or transcript is None or not (recordings_dir / rid / AUDIO_FILE).exists():
            raise ValueError(f"'{rec.title}' hasn't finished transcribing yet")
        recordings.append((rec, transcript))
    order = store.in_recording_order([rec.id for rec, _ in recordings])  # the order they were added in
    recordings.sort(key=lambda pair: order.index(pair[0].id))

    starts: list[float] = []
    segments: list[Segment] = []
    position = 0.0
    for rec, transcript in recordings:
        starts.append(position)
        segments.extend(offset(transcript.segments, position))
        position += (rec.duration_sec or audio.probe_duration(recordings_dir / rec.id / AUDIO_FILE)) + GAP_SEC
    duration = position - GAP_SEC

    first = recordings[0][0]
    new_id = uuid.uuid4().hex
    new_dir = recordings_dir / new_id
    new_dir.mkdir(parents=True)
    audio.concat([recordings_dir / rec.id / AUDIO_FILE for rec, _ in recordings], new_dir / AUDIO_FILE, GAP_SEC)

    name = " ".join((title or "").split()) or f"{first.title} (combined)"
    store.create_recording(new_id, name, first.subject_id, first.language,
                           f"{len(recordings)}{COMBINED_MARK}" + ", ".join(rec.original_filename for rec, _ in recordings))
    stamps = [rec.recorded_at for rec, _ in recordings if rec.recorded_at]
    store.update_recording(new_id, duration_sec=duration, progress_done=len(recordings),
                           progress_total=len(recordings), recorded_at=min(stamps, default=None))
    store.set_part_starts(new_id, starts)
    store.record_sources(new_id, [rec.id for rec, _ in recordings])  # as private as its strictest part, for good
    text = "\n\n".join(p.text for p in to_paragraphs(segments))
    store.save_transcript(new_id, text, segments, recordings[0][1].model)
    store.queue_notes(new_id)
    return store.get_recording(new_id)
