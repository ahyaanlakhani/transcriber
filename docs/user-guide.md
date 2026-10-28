# Transcriber user guide

Accurate transcripts of your meetings and voice notes, on your own laptop. Drop in a recording and get
timestamped text you can search, check against the audio, and export.

Transcription uses **Whisper large-v3** through Groq's free tier — about 8 hours of audio per day at no cost.
Optional **AI notes** (summary, decisions, action items, open questions) are written by the open-source
`gpt-oss-120b` model and fact-checked against the transcript by Gemini — both on free tiers.

## Setup (once)

1. Install **Python 3.11 or newer** from https://www.python.org/downloads/ and tick
   **"Add python.exe to PATH"** in the installer.
2. Create a free Groq account at https://console.groq.com and make an API key under **API Keys**.
3. Double-click **`start.bat`**. The first run sets up its Python environment (a minute or two), then opens
   `.env` in Notepad: paste your key after `GROQ_API_KEY=`, save, and close Notepad.

The app opens at http://127.0.0.1:8000. After that, just double-click `start.bat`.

## Start automatically

`scripts/run_in_background.cmd` keeps the app running in the background and restarts it if it stops. Its output
goes to `%LOCALAPPDATA%\meeting-transcriber\app.log`. To start it at every login, put a `Transcriber.vbs` file in
your Startup folder (Win+R, `shell:startup`) containing:

```
CreateObject("WScript.Shell").Run """C:\path\to\this\folder\scripts\run_in_background.cmd""", 0, False

```

Delete that file to stop starting at login.

## Daily use

- **Add recordings:** drag files onto the page, or click *Add recordings*. Phone voice memos (.m4a),
  Zoom/Teams/Meet recordings (.mp4), .mp3, .wav and other common formats all work.
- Give each one a title, an optional **subject** (e.g. "Standup", "Client X") and a language — **English** by
  default, or Auto-detect, or **English + your other language** for a mixed lecture. Other languages are added in `.env`
  (see *Your own language* below).
- Progress shows live in the library. When a transcript is ready, click any **timestamp** to hear that moment —
  the quickest way to check a word.
- **Copy transcript**, **Download .txt** (clean text) or **Download .md** (AI notes + timestamped transcript).
- **Search** looks through titles, transcripts, AI notes and your own notes.
- **Re-transcribe** after adding names to your vocabulary, or to switch language.

## Organizing: subjects, topics and your own notes

- **Subjects** (left column) group recordings, e.g. "Client Acme" or "Team standup". Each recording has one
  subject. Create one with **+**, edit its name or description with ✎, or pick one from the menu on a
  recording ("New subject…" creates one on the spot).
- **Topics** are tags across subjects, e.g. "pricing" or "hiring". After each meeting the AI suggests topics
  (dashed chips): **✓** accepts, **×** dismisses. Type in **+ topic** to add your own. Renaming a topic to an
  existing name merges them.
- **Move several recordings** at once: tick their boxes in the list, choose a subject, click **Move**.
- **My notes** (third tab on a recording) is your own scratchpad. It saves as you type; the AI never touches it.

## Ask about a meeting, and fix the notes

- **Ask tab:** chat about one meeting — "What did they decide about pricing?", "Draft a follow-up email",
  "Make the summary three bullets". Answers cite timestamps you can click. The conversation is saved.
- When you ask for a change to the notes, the answer comes with a **proposed change** (new points highlighted,
  removed points struck through). Nothing changes until you click **Apply to notes**.
- **Edit by hand:** hover a note and click ✎ to edit or × to delete; add your own points under the notes.
  Edited points show **edited** (the checker's mark no longer applies). *Regenerate notes* replaces edits.
- **Which AI answers:** your **Claude subscription**, through Claude Code on this computer — no API key.
  Sign it in once: open a terminal and run `claude auth login`. Questions count toward your plan's usage
  limits. If Claude isn't signed in or is over its limit, the free notes model answers instead and says why.
  Set `ASK_MODEL=claude-code:sonnet` in `.env` to pick a model.

## Brain: your second brain across everything

**Brain** in the left column gathers every lecture, coffee chat and talk into one place, like Obsidian for your
recordings.

- **Concepts:** one page per idea (OAuth, RAG, the business model canvas…), made from the key terms, lecture topic
  headings and topics you accepted. Each page has a short definition, every mention newest first with a link to the
  exact moment, related concepts, the people it came up with, and backlinks. Rename a concept with ✎, or merge two
  that are the same idea (the old name keeps working). "RAG" and "retrieval augmented generation" meet on their own.
- **Ask:** one question across all your recordings ("What did we say about OAuth?"). The answer cites numbered
  sources; click one to hear that moment. Pip answers these too.
- **Graph:** a map of concepts (bigger = in more recordings), subjects and people. Click anything to open it.
- **Export to Obsidian:** writes a folder of linked Markdown pages (`Home.md`, `Concepts/`, `Classes/`, `People/`,
  `Subjects/`), by default `Documents\Transcriber vault`. Open that folder in Obsidian as a vault. Exporting again
  replaces only the pages the app wrote; your own notes in the folder are never touched.

The index updates itself in the background as notes arrive, and **Rebuild** recreates it from the notes. With a
Groq key, the notes writer tidies near-duplicate names (one small request per new recording) and writes each
concept's definition the first time you open it.

## AI notes (optional, free)

1. The writer is the open-source `gpt-oss-120b` on **Groq**, using the `GROQ_API_KEY` you already have (no card
   needed). It works through long meetings in pieces, so notes take a few minutes per hour of audio.
2. Get a free **Gemini** key at https://aistudio.google.com/apikey (Gemini checks the notes).
3. Add `GEMINI_API_KEY=…` to `.env` and restart the app.

Every finished transcript then gets concise notes in the **Notes** tab: a summary, decisions, action items
(with owner and due date when said) and open questions. Each point links to the moment it was said. The
checker marks each point **✓** supported, **◐** partly right or **⚠** not found in the transcript, explains
any doubt in one line, and lists up to three important things the notes may have missed. It never changes
the notes, so the two models can't conflict; you decide. **Regenerate notes** writes them again.

Recordings transcribed before you added the keys get notes automatically on the next start.

Privacy: Google uses free-tier Gemini requests to improve its products, so meeting transcripts sent to the
checker are used that way. Set `NOTES_CHECKER=none` in `.env` to turn the checker off. To switch the writer
later (e.g. to ChatGPT), set `NOTES_WRITER=openai:<model>` and `OPENAI_API_KEY=…`.

## Your own language

Transcriber starts with **English**, **Auto-detect** and **English + your other language (mixed)**. Add any language
Whisper supports in `.env`:

```
EXTRA_LANGUAGES=es:Spanish,fr:French
SECOND_LANGUAGE=Spanish
```

- `EXTRA_LANGUAGES` is a comma-separated list of `code:Name` pairs. The code is the two- or three-letter one Whisper
  uses (`es`, `fr`, `de`, `hi`, `ar`, `pt`, `ur` and so on). Each language then appears in the language menus when you
  add or re-transcribe a recording, and in Settings as the default.
- `SECOND_LANGUAGE` names the other language of a **mixed** lecture, for example one that moves between English and
  Spanish within a sentence. Gemini transcribes those, and the notes AI then writes the other language in Roman letters
  next to the English so the transcript reads as one piece. Notes are always written in English.
- Restart the app after changing `.env`.

## Getting the best accuracy

- Open **Settings (⚙)** and fill **Custom vocabulary** with teammates' names, product and client names, and
  acronyms — one per line, most important first (about 50–70 terms fit). This is the biggest single
  accuracy boost for meetings.
- Set the language correctly: pinning English is more accurate than auto-detect.
- Record close to the speakers when you can. Distant or overlapping voices are hard for any model.

## Record live on the laptop

Press **Record live** at the top of your library. Then choose what to record:
- **Microphone:** people in the room.
- **Computer sound:** Zoom, Teams, Meet or a video. Chrome or Edge asks what to share; tick **Share audio**.
- **Both:** online calls, with your own voice included.

The text appears about 10–20 seconds behind you, cut at natural pauses. Silence isn't sent to Groq.
- You can browse your library while it records. A red **Recording** button in the top bar brings you back.
- Use Chrome or Edge, and keep the laptop awake. The app stops it from sleeping while recording, but closing the
  lid still stops it.
- **Stop and save** turns it into a normal sheet: the live text becomes the transcript, then AI notes follow.
- If the app or laptop crashes mid-recording, the audio so far is kept. It's saved and transcribed at the next start.

## Starring and renaming

- **Star** a recording with the star next to its title, or the small star on its row in the list. **Starred** in
  the left rail lists them.
- **Rename** by clicking the title, or with **Rename** in the ⋯ menu.

## On your iPhone and iPad

Record in Voice Memos (screen off, no battery worry), then share each memo with **Send to Transcriber**. Read your
notes and ask questions from the Home Screen app. Setup takes about 15 minutes: Tailscale, then a shortcut. See
[docs/phone-setup.md](phone-setup.md).

Each lecture also gets a five-minute spoken **recap**, made on the laptop from its notes. Play it on the sheet,
or have it waiting on your phone each morning with the **Get my recaps** shortcut: see
[docs/recaps-shortcut.md](recaps-shortcut.md).

## Speaker labels and your own server

Groq's Whisper can't tell voices apart. For "who said what", run the small server in the `server/` folder on
another machine, ideally one with an NVIDIA graphics card. [server/README.md](../server/README.md) walks through
setting it up. Then add its address and token to `.env`:

```
TRANSCRIBE_SERVER_URL=http://192.168.1.20:8765
TRANSCRIBE_SERVER_TOKEN=<same token as the server's .env>
```

- Tick **Label speakers** when adding a recording, or when re-transcribing one from the ⋯ menu. The transcript
  shows **Speaker 1 / Speaker 2** whenever the voice changes. Click a name to rename it (e.g. "Dr. Khan").
  The notes and Ask use those names too.
- Turn on **Prefer my server** in Settings to send *every* recording there. This saves your Groq allowance.
- If the server is off or stops answering, the recording goes to Groq instead, without speaker labels. The sheet
  tells you, so you can re-transcribe later.

## Limits, and what happens when you hit them

- Groq's free tier allows about **2 hours of audio per hour** and **8 hours per day**. The bar at the top right
  shows how much you've used in the last 24 hours. Hover over it for the last hour. Past a limit, the app pauses
  the recording and resumes by itself ("Rate limit reached — resuming at 14:32"). Nothing is lost. If Groq
  changes its limits, adjust them in Settings.
- If the internet drops, it retries a few times, then marks the recording **Failed** with a **Retry** button.
  Retrying continues where it stopped; finished parts are not sent again.
- Closing the app mid-transcription is fine — it picks up where it left off next time.

## Where things are stored

- Everything lives in the `data/` folder next to this README: `app.db` (library and transcripts) and
  `recordings/` (a compact 16 kHz copy of each recording's audio, roughly 50–70 MB per hour). Your original files
  are never changed or moved.
- The Python environment lives in `%LOCALAPPDATA%\meeting-transcriber\venv` (outside this folder, because of
  Windows' path-length limit). You can delete it any time; `start.bat` rebuilds it.
- To keep recordings somewhere else, set `TRANSCRIBER_DATA_DIR` in `.env`.

## Privacy

Audio is sent to Groq for transcription, or to your own server when you use one, and the text comes back to
your laptop. The app only listens on your
own computer (127.0.0.1), and your API key never leaves the app's server process.

## For developers

```bat
set PY=%LOCALAPPDATA%\meeting-transcriber\venv\Scripts\python.exe
python -m venv "%LOCALAPPDATA%\meeting-transcriber\venv"
"%PY%" -m pip install -r requirements-dev.txt
"%PY%" -m pytest                      &REM fast tests, no network
"%PY%" -m pytest -m live -s           &REM real accuracy + notes checks (need the API keys)
"%PY%" scripts\demo_server.py         &REM UI with fake transcripts and notes on port 8001
```

Design: `docs/superpowers/specs/2026-10-03-meeting-transcriber-design.md` ·
Plan: `docs/superpowers/plans/2026-10-03-meeting-transcriber.md`
