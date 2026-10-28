# Taking your data to other apps

Everything here is a plain file made on this laptop. Nothing is sent to any service, and the app asks for no account.

## Obsidian (Brain → Export to Obsidian)

Writes your second brain as a folder of linked Markdown pages (Home, Concepts, Classes, People, Subjects). It is safe to
export again and again:

- It remembers which pages it wrote **and what they said**. Pages of yours in the same folder are never touched.
- If a page of yours has the name the app wants, the app's page takes another name.
- **If you edit a page the app wrote, that page becomes yours.** It is left exactly as you left it, and the app writes its
  new version beside it under another name, like `Agents week (2).md`. The app tells you how many pages it left alone.
- A page you deleted comes back. Pages that disappeared from your library are removed only if you hadn't edited them.

Folders written before this version don't have the content record, so the first export after upgrading can't tell edits
apart: it behaves as before once, then records everything.

## Calendar (Settings → Take it with you → Classes for your calendar)

A `.ics` file with your class timetable for the next 120 days, as individual events (so skipped weeks, moved classes and
extra sessions are right), plus your coming Pip reminders with an alert. Cancelled classes are left out. Import it into
Google Calendar, Apple Calendar or Outlook. It is a snapshot: export again when your timetable changes, and delete the old
import first so you don't get duplicates. Times are saved in UTC and show in your calendar app's own time zone.

## Anki (Settings → Take it with you → Flashcards for Anki)

A text file of your flashcards (front, back and tags). In Anki, use **File → Import**. The back says which recording and
moment each card came from, and the tags are `Transcriber`, the card kind and the subject. Cards you retired are left out.
Anki keeps its own review schedule, separate from the app's.

Code: `transcriber/integrations.py`, `transcriber/brain/vault.py`. Tests: `tests/test_integrations.py`, `tests/test_brain.py`.

## Not built

- **Zotero** (references): the app holds no references, so there is nothing to send yet; importing a Zotero library as
  links on a project would be the useful direction.
- **Importing documents** (PDFs, slides, readings) into the brain: they would need their own notes and search handling,
  and PDF text extraction needs a library the app doesn't have. A separate piece of work.
- A live two-way calendar connection: it would need your Google or Microsoft account signed in.
