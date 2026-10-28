# Privacy in Transcriber

Phase A of the privacy design (`docs/superpowers/specs/2026-10-15-privacy-design.md`, and the handover document of
15 October 2026) is built. This page says what it does, how to use it, how to undo it, and what it does **not** do.

## What it does

Every recording has a **class**:

| Class | Which services may see it |
|---|---|
| **Standard** (the default) | Any service you have approved |
| **Private** | Only services you approved *and* confirmed keep nothing and don't train. Claude through your subscription can never qualify |
| **Local only** | None. It never leaves this laptop |

Every cloud setup the app uses (for example `groq:openai/gpt-oss-120b`, `groq/whisper-large-v3`) is **unapproved until you
approve it** in the **Privacy** screen. Approval belongs to the exact provider and model, so changing a model in `.env`
asks again. Nothing is sent to an unapproved setup, and nothing is sent to a provider the app doesn't know.

All sending goes through one gateway (`transcriber/privacy.py`), wrapped around the seven places that can send content:
the Groq, Cerebras, OpenRouter, OpenAI, Gemini and Claude Code language models, and the Groq, Gemini and your-own-server
transcription clients. It checks at the moment of sending, so every retry and every fallback is checked again, and a
change of class or approval takes effect on the next attempt.

Rules it enforces:

- **Unknown means no.** An unknown provider, recording, class, or a request with no stated source is refused.
- **The strictest source wins.** A request built from several recordings has the strictest class among them.
  Combined and merged recordings keep the strictest class of their parts for good, even after the parts are erased.
- **Derived things are covered.** Concept names and aliases, shared topic lists, people briefs, practice personas,
  and Ask across everything only include material from recordings the model may see. Excluded recordings are
  counted ("1 recording was left out because of its privacy setting"), never named.
- **Live sessions** have their class fixed before the first audio window is sent.
- **No quiet switching.** A refusal is a plain message. Fallback to another provider happens only if that provider is
  also approved for the recording, and the answer says which one replied.
- **Local features keep working:** reading, search, the calendar, graph, backlinks and exports need no cloud service.

The **Recent activity** list in Privacy shows what was sent or held back: time, feature, service, how many
recordings, and the reason. It stores no audio and no text.

## Errors and logs

Provider errors used to quote what the provider said (which can include your text). They now show only a short reason
such as "Groq server error (HTTP 500)". Unexpected failures in the transcription, notes and recap jobs and the
brain are logged with where they happened and the kind of error, not the error's message.

## Concept definitions

A definition is now remembered with a fingerprint of exactly what it was written from: the concept's name and aliases,
the wording of each note behind it, which recordings are in the bin, and their classes. Editing a note (even with the
same number of mentions), trashing or restoring a recording, renaming or merging a concept, or changing a class makes
it stale. A definition that finishes writing after its notes changed is thrown away.

## Upgrading the database

The first start after this update upgrades the database to version 18 in one all-or-nothing step. Before it
starts, the app copies the current database to `data/backups/app-v<old version>-<date>.db`.

- Existing recordings become **Standard**.
- Recordings made by merging devices keep their link to their parts.
- Recordings combined before this update didn't keep their parts' ids. They are marked "can't be traced" and stay
  off cloud services until you open them and press **I checked this**. (On your copy of the database on
  15 October 2026 there were none.)
- Old concept definitions are marked stale and are rewritten the next time you open the concept.

**After the first start, cloud features are paused until you approve services** in Privacy. That is deliberate.

### Rolling back

1. Close the app.
2. Copy the backup over `data/app.db` (use `data/backups/app-v16-….db`, or `exports/backup-2026-10-15/app.db`).
3. Use the earlier source (`exports/Transcriber-source-2026-10-15.zip` is from just before this update).

If an upgrade fails part-way, nothing is changed and the database stays at its old version.

## Tests

```
python -m pytest tests/test_privacy.py tests/test_app_privacy.py
```

They use synthetic recordings, fake providers and temporary databases, with no network. They prove a refused request
makes zero calls, and cover approvals, Private and Local only, combined recordings, fallback and retry, live sessions,
Ask across everything, concept names and topics, the practice room, error and log hygiene, the definition cache,
and the upgrade (including a failed upgrade).

## What you still have to do

- **Groq:** open the Groq console and look for Data Controls or a zero-retention setting. Only tick "allow for Private"
  in Privacy if you checked that it covers your account. The app can't verify it.
- **Claude Code (Ask):** it runs under your Claude account. It can be set not to train, but it is not
  zero-retention, so the app never lets it see Private recordings.
- **Gemini:** there is no key set. If you add one, don't approve it for Private while on the free tier.

## What it does not do

- It can't prove what a provider does after receiving your content. Approval records what you checked.
- There is no local transcription or notes model. This laptop has no GPU, so **Local only** means store, search and
  read; it doesn't make notes.
- Private recordings' text is still in your local database and in an Obsidian export if you make one. Exports are
  local files, so don't put the vault somewhere that syncs if that matters to you.
- Only a call that goes through the guarded clients is checked. A new feature that sends content must set a scope
  (`privacy.scope(...)`); without one it is refused rather than sent.
- Pip's typed requests carry your words and your timetable (subject names), no recording content.
- Pip memory, source-backed knowledge claims and the later releases are not started.
