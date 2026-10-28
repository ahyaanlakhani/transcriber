# Learn and Projects

## Learn (sidebar)

**Flashcards.** Cards are made from your lecture notes: each definition ("What is OAuth?") and each point your lecturer
stressed. Making them needs no AI model and costs nothing. Every card links to the recording and moment it came from.
You answer **Again / Hard / Good / Easy** (keys 1–4, Space shows the answer) and each button says when you'd see the card
next. A card you forget comes back in ten minutes; ones you remember wait longer each time. Up to 10 new cards a day.
"Never ask me this one" retires a card. A card whose note was reworded before you ever answered it follows the note; ones
you've answered are kept.

The schedule follows the **FSRS-5** memory model with its default weights, written from memory of the reference and tested
for sensible behaviour (gaps grow when you remember, shrink when you forget), not checked against the reference library.
Target retention is 90%.

**Teach-back.** Pick a concept, explain it in your own words, and see what you got right, what your notes also say, and what
doesn't match. This sends your notes about that concept and your explanation to your notes writer (Groq), under your privacy
settings: if any recording behind the concept is Private (and Groq isn't verified for that) or Local only, nothing is sent
and you're told why. The checker is told the notes are the only source of truth, so it won't mark something wrong just because
your lectures didn't cover it.

Code: `transcriber/fsrs.py`, `learn.py`, `learn_store.py` (schema 21), `static/learn.js`. Tests: `tests/test_learn.py`.

## Projects (sidebar)

A project is a named place that ties together recordings, people, concepts and documents or web links, with its own tasks.
Its page also gathers what the linked recordings decided and which follow-ups are still open, from your notes, with no AI
model. A recording's sheet data lists the projects it belongs to. **Deleting a project removes only the project, its links and
its tasks, never a recording.**

Limits: a concept link follows the concept's id, so if you merge that concept into another it shows as "merged or removed".
Documents are a label plus a path or address; the app doesn't open or read them.

Code: `transcriber/projects.py`, `projects_store.py` (schema 22), `static/projects.js`. Tests: `tests/test_projects.py`.
