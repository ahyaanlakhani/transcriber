"""English translations of transcripts. One row per recording; the paragraphs are kept with their start times, so the
English text lines up with the original paragraph at the same moment even if the speaker labels change."""
import json
from datetime import UTC, datetime


class TranslationStoreMixin:
    def get_translation(self, recording_id: str) -> dict | None:
        rows = self._query("SELECT status, status_detail, paragraphs_json, model, progress_done, progress_total,"
                           " updated_at FROM translations WHERE recording_id = ?", (recording_id,))
        if not rows:
            return None
        row = rows[0]
        return {"status": row["status"], "detail": row["status_detail"], "model": row["model"],
                "progress_done": row["progress_done"], "progress_total": row["progress_total"],
                "paragraphs": json.loads(row["paragraphs_json"]) if row["paragraphs_json"] else [],
                "updated_at": row["updated_at"]}

    def start_translation(self, recording_id: str, total: int) -> bool:
        """False when one is already running. One statement, so two quick requests can't both start."""
        return self._execute(
            "INSERT INTO translations (recording_id, status, progress_total, updated_at) VALUES (?, 'working', ?, ?)"
            " ON CONFLICT(recording_id) DO UPDATE SET status = 'working', status_detail = NULL, paragraphs_json = NULL,"
            " model = NULL, progress_done = 0, progress_total = excluded.progress_total,"
            " updated_at = excluded.updated_at WHERE translations.status != 'working'",
            (recording_id, total, _now())) == 1

    def translation_progress(self, recording_id: str, done: int | None = None, detail: str | None = None) -> None:
        """Progress and/or a line about what it is waiting for (None clears it)."""
        if done is None:
            self._execute("UPDATE translations SET status_detail = ?, updated_at = ? WHERE recording_id = ?"
                          " AND status = 'working'", (detail, _now(), recording_id))
        else:
            self._execute("UPDATE translations SET progress_done = ?, status_detail = NULL, updated_at = ?"
                          " WHERE recording_id = ? AND status = 'working'", (done, _now(), recording_id))

    def save_translation(self, recording_id: str, paragraphs: list[dict], model: str, detail: str | None = None) -> None:
        self._execute("UPDATE translations SET status = 'ready', status_detail = ?, paragraphs_json = ?, model = ?,"
                      " progress_done = progress_total, updated_at = ? WHERE recording_id = ?",
                      (detail, json.dumps(paragraphs, ensure_ascii=False), model, _now(), recording_id))

    def fail_translation(self, recording_id: str, detail: str) -> None:
        self._execute("UPDATE translations SET status = 'failed', status_detail = ?, updated_at = ?"
                      " WHERE recording_id = ?", (detail, _now(), recording_id))

    def delete_translation(self, recording_id: str) -> None:
        self._execute("DELETE FROM translations WHERE recording_id = ?", (recording_id,))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
