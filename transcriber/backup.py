"""A backup of your whole library you can keep or move to another computer (for example the Mac): the database, copied
safely while the app runs, and optionally the audio. Copying the live database file by hand, or letting a cloud folder
sync it while the app is open, can corrupt it; this uses SQLite's own backup instead."""
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .store import SCHEMA_VERSION

AUDIO_FILES = ("audio.flac", "you.flac", "others.flac")
README = """Transcriber library backup
==========================

Made {stamp}. Database version {version}. {recordings} recordings{audio_line}.

To use it (on this computer or another one):
 1. Close Transcriber.
 2. Copy app.db into the app's data folder (data/app.db), replacing the file there. Keep a copy of the old one first.
 3. {audio_step}
 4. Start Transcriber. It checks the database and upgrades it if the app is newer than the backup.

Do not put this folder somewhere that syncs while the app is using it, and never copy data/app.db by hand while the
app is open: use the backup button instead.
"""


def backup_library(store, recordings_dir: Path, destination: Path, include_audio: bool, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    destination.mkdir(parents=True, exist_ok=True)
    folder = destination / f"Transcriber-backup-{now:%Y%m%d-%H%M%S}"
    number = 1
    while folder.exists():  # never write into an existing backup
        number += 1
        folder = destination / f"Transcriber-backup-{now:%Y%m%d-%H%M%S}-{number}"
    folder.mkdir()
    store.backup_to(folder / "app.db")
    check = sqlite3.connect(folder / "app.db")
    try:
        verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
        recordings = check.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
        version = check.execute("PRAGMA user_version").fetchone()[0]
    finally:
        check.close()
    if verdict != "ok":
        shutil.rmtree(folder, ignore_errors=True)
        raise RuntimeError("The backup didn't pass its check, so it was removed. Nothing was changed in your library.")
    copied = 0
    if include_audio and recordings_dir.exists():
        for rec_dir in sorted(p for p in recordings_dir.iterdir() if p.is_dir()):
            for name in AUDIO_FILES:
                source = rec_dir / name
                if source.is_file():
                    target = folder / "recordings" / rec_dir.name / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    copied += 1
    size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    (folder / "README.txt").write_text(README.format(
        stamp=f"{now:%Y-%m-%d %H:%M}", version=version, recordings=recordings,
        audio_line=f", with {copied} audio files" if include_audio else ", database only (no audio)",
        audio_step="Copy the recordings folder into data/ too (data/recordings)." if include_audio
        else "Audio isn't in this backup: recordings will show their notes and transcripts but can't be played."),
        encoding="utf-8")
    (folder / "backup.json").write_text(json.dumps({"made": now.isoformat(timespec="seconds"), "schema": version,
                                                    "app_schema": SCHEMA_VERSION, "recordings": recordings,
                                                    "audio_files": copied, "includes_audio": include_audio}, indent=1), encoding="utf-8")
    return {"folder": str(folder), "recordings": recordings, "audio_files": copied, "size_mb": round(size / 1e6, 1),
            "schema": version}
