# Pip's memory

Pip can remember things about you so its help gets more personal. The rule is simple: **Pip only remembers what you
asked for or approved, you can always see it, and it fades on its own.**

## How it works

- **Tell Pip:** type "remember that I want an A in Machine Learning" or "remember I prefer short bullet notes". Pip saves it
  and shows an Undo. Say "forget the Machine Learning thing" to remove it (also with an Undo).
- **Suggestions:** the Pip panel's **Suggest from my notes** button turns your own recent action items (from meeting
  notes, where the owner is you or blank) into suggestions under "Want me to remember these?". Nothing is used until you
  press **Yes**. No AI model is involved in making suggestions.
- **Where it shows:** the Pip panel lists what Pip remembers, with each item's kind, where it came from ("you told me" or
  the recording's name), and when it fades.
- **Fading:** goals, preferences and people stay for 180 days, things you're working on for 30. Faded items stay in a
  "Faded" list where you can **Keep** (renew) or forget them. They are never used while faded.
- **Kinds:** goal, preference, ongoing, relationship.

## Privacy

Approved, current memories are added to Pip's typed requests so it can use them. A memory that came from a recording is
as private as that recording: it is only shown to a model that may see the recording (see `docs/privacy.md`), and the
request counts as drawing on that recording. Memories you typed yourself carry no recording.

## Where it lives

The `pip_memory` table (schema 19), `transcriber/memory_store.py`, and Pip's tools in `transcriber/pip.py`
(`remember`, `forget`, with undo steps). The API is `/api/pip/memory`. Tests: `tests/test_pip_memory.py`.

## Not built yet

A daily briefing, a combined action center, and suggestions drawn from conversations (those would need a model and the
privacy gateway's rules). Memory is for you to read and edit; nothing is shared anywhere.
