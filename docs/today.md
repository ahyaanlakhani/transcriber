# Today

**Today** (in the sidebar, above Calendar) is your day on one page. It is built on this laptop from your calendar and
your own notes. No AI model is involved, so it opens instantly and nothing about it is sent anywhere.

- **Classes:** today's classes with whether each is recorded, still to come, or missed, and a **Record now** button.
- **Needs a look:** recordings whose transcription or notes failed.
- **Reminders today:** Pip's reminders for today.
- **What's still open:** the action items in your notes, plus follow-ups you missed in coffee chats and open questions from
  meetings. Each one shows who it is for, by when, which recording and moment it came from (click to open it), and how
  sure the notes are ("AI-written, not checked", or what the checker said). Tick one when it's done, or dismiss it as not
  relevant. Your ticks survive the notes being rewritten. Yours (no owner, or "me") come first, items with a date before
  those without, then the newest. Notes older than 45 days are left out.
- **Worth revising:** what your lecturers stressed in the last two weeks.
- **Pip is keeping in mind:** the ongoing things you told Pip to remember (see `docs/pip-memory.md`).
- **New notes since yesterday.**

A lecture's own open questions are not listed as follow-ups: they were asked in class, and there is nothing for you to do.

Code: `transcriber/briefing.py`, `transcriber/followups_store.py` (schema 20), `static/today.js`. API: `/api/briefing`,
`/api/follow-ups`. Tests: `tests/test_briefing.py`.

Not built yet: a "while you were away" summary that remembers when you last opened the app, and follow-ups drawn from
conversations with a model (those must go through the privacy gateway).
