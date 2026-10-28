# Working offline, and moving to another computer

## Local transcription (for Local-only recordings)

A recording set to **Local only** never goes to a cloud service. To get it transcribed anyway, the app can use a Whisper
model on this laptop, through the faster-whisper library. It is **optional and not installed yet**:

1. `pip install faster-whisper` in the app's Python environment.
2. Add `LOCAL_TRANSCRIBER=small` to `.env` (`base.en` is faster and English-only; `small` is a better balance; `medium` is
   more accurate and slower still). Restart the app.
3. The first use downloads the model once (a few hundred megabytes), then it works with no internet.

How it behaves:

- Only Local-only recordings use it. Every other recording keeps using Groq as before. Nothing switches over silently.
- Without it set up, a Local-only recording fails with a message that says what to set, and nothing is sent to Groq even
  though Groq is approved for other recordings.
- On a laptop with no graphics card it is slow: roughly as long as the recording, or longer for bigger models.
- A model on this laptop isn't a cloud service, so it needs no approval and isn't listed in the privacy activity log.
- **Notes:** there is no local notes model (it would be a large download and too slow on this hardware), so a Local-only
  recording gets a transcript you can read and search but no AI notes.
- Live recording text still needs the cloud, so a Local-only live recording saves and is transcribed afterwards.
- Mixed English–Urdu: Whisper handles it less well than Gemini does, and Gemini isn't allowed for Local-only. Expect rougher text.

Not done: Parakeet and other models. The reminder for Monday 19 October is to try Parakeet on your own lectures first.

## Backing up your library (Settings → Take it with you → Back up my library)

Makes a folder in `Documents/Transcriber backups` with a consistent copy of your database, made with SQLite's own backup so it
is safe while the app is running, and optionally your audio. It checks the copy before it keeps it, never writes into an
earlier backup, and the folder has a README with the steps to restore. To move to another computer (for example the Mac):
back up with the audio, copy the folder across, and follow its README.

Never copy `data/app.db` by hand while the app is open, and don't let a cloud folder sync it while the app is using it.

## Not built (needs hardware or downloads you haven't approved)

- **Mac system-audio capture** (via ScreenCaptureKit) and **Metal/MLX acceleration**: they can only be written and tested on a Mac.
- **A local search model** (embeddings) for the second brain: a new dependency; the SQLite full-text search already works.
- **A common interface for capture, transcription, notifications and storage**: the pieces are already separate classes, but
  they were not reshaped without a second platform to test against.

Code: `transcriber/providers/local.py`, `transcriber/backup.py`. Tests: `tests/test_offline.py`.
