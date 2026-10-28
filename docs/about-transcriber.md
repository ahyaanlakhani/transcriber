# Transcriber

**A free, private lecture and meeting transcriber that runs on your own laptop.** Record or drop in a lecture, a
Zoom call or a voice memo and get an accurate transcript, study-ready notes that cite where each point was said,
and a calendar-aware assistant that keeps your classes organised. Your recordings and notes stay on your computer.

It was built for a university student who records every class and wants to revise from the notes, not re-listen
to the audio.

## Who it's for

- Students who record lectures and want notes they can actually study from.
- Anyone who sits in meetings or calls and wants decisions, action items and a follow-up message without paying
  for a hosted note-taking service.
- People who prefer their recordings stay on their own machine.

## Key features

### Capture from anywhere

- **Upload** audio or video files, including long lectures. Recording dates are read from the file itself.
- **Record live** in the browser: microphone, computer sound, or both. Text appears about 10–20 seconds behind.
- **Record a Zoom, Teams or Meet call straight from Windows.** No screen-sharing prompt, no bot joining the call,
  and it works with the browser in the background. Your microphone and the call's sound are kept as separate
  tracks, so every line is labelled **You** or **Others**.
- **Voice memos from an iPhone** with a one-tap "Send to Transcriber" shortcut (needs a private connection such as
  Tailscale; see `docs/phone-setup.md`).
- Finds **phone recordings already sitting in your laptop's Downloads folder** and shows which classes they
  belong to.

### Transcripts

- Powered by Whisper large-v3 through Groq's free tier, about 8 hours of audio per day at no cost.
- Handles **English, any language you add, automatic language detection and mixed English-plus-your-language** lectures (mixed needs a free
  Gemini key).
- Cuts audio at natural pauses, uses your vocabulary list for names and jargon, filters the phantom lines speech
  models invent in silence, and picks up where it left off after rate limits or network trouble.
- Optional speaker labels on uploaded recordings through your own GPU server.

### Notes you can trust

- Every note item carries **timestamps** that jump to the moment in the audio.
- Optional **fact-checking**: when a checker model is set up (Gemini's free tier by default, or another provider),
  a second model checks the notes against the transcript, marks each item *supported*, *partial* or *unsupported*,
  and lists important points the notes missed. It stays off until you add a key for it.
- Notes are editable, and you can add your own. **Ask** answers questions about a recording ("what's on the exam?",
  "draft a follow-up email").
- A live **usage bar** shows how much of the free allowance is left and exactly when it refills.

### For lectures

- **Notes organised by topic**, not by the clock: each item is a point, definition, example, fact or a point the
  lecturer *stressed* ("this will be in the exam"), followed by a **Key terms** list.
- **Audio recaps:** a spoken five-minute summary of a lecture, voiced on the laptop, for the walk to class.
- A **midterm export**: every class of a subject in one document with notes first and transcripts underneath, plus
  a one-page exam sheet of stressed points, key terms and facts.
- **Classes recorded in several parts** are detected and can be combined in one click. The originals are always
  kept, and a **Combined / Separate** switch shows either view.

### For meetings

- Summary, decisions, action items with owners and deadlines, open questions, a list of attendees (by name only
  when the transcript gives one), and a **ready-to-send follow-up message**.

### For conversations and self-improvement

- **Coffee chats** get their own notes: what you learned about the person, what to follow up on, coaching on your
  questions, your speaking pace and unclear moments, and a thank-you draft. Speakers are told apart **from the
  words, never from their voices**.
- **People pages** with a brief to read before you meet someone again, and a **progress** view across chats.
- A **highlights reel** of the key moments, cut directly from the original recording.
- A **practice room**: talk with an AI playing an interviewer or a customer, then get coached.

### Your calendar and Pip, the assistant

- A **class calendar** from your timetable shows which classes were recorded, which exist only as a file on your
  laptop, and which are missing.
- **Pip**, the mascot, greets you each time the app opens with your week, and nudges you when a class is about to
  start and nothing is recording. It can also send **Windows notifications**, even with the browser closed.
- Tell Pip what you want in plain language: skip a class or a whole week, move a class, set a reminder, rename
  recordings from their notes, combine classes, file unsorted recordings under the right subject, or start a
  meeting recording. Small changes happen at once, bigger ones show a plan first, and everything has **Undo**.
  Pip never deletes recordings.

### Your second brain, learning and projects

- **Brain:** one page per concept gathering everything said about it across your classes, with links to the exact
  moments, an Ask box across every recording (each answer cites the passages it used), a graph, and an export to
  Obsidian that never overwrites a page you edited.
- **Today:** your classes, anything that failed, what's still open from your notes (with who, by when, where it came
  from and how sure the notes are), what your lecturers stressed lately, and what Pip is keeping in mind. It runs on your
  laptop with no AI model.
- **Learn:** flashcards made from your notes' definitions and stressed points on a spaced-repetition schedule, each linked
  to its source moment, plus **teach-back**: explain a concept in your own words and see what you got right and left out.
- **Projects:** tie recordings, people, concepts and documents to one piece of work, with tasks, and see its decisions
  and open follow-ups in one place.
- **Pip remembers** only what you tell it to or approve, shows you all of it, and lets it fade.
- **Take it with you:** your classes as a calendar file, your flashcards for Anki, and a safe backup of the whole library.

### A library that stays tidy

- Subjects, topics, people and starred recordings; full-text search across transcripts and notes.
- **Topic-style names** suggested from the notes. The **original phone name is always kept** and searchable.
- **Duplicate detection** flags exact copies and recordings with the same start time and length.
- A **notification bell** for what was added, transcribed or failed. A trash with 30 days to change your mind.
- Exports to Markdown and plain text. Works in light and dark, and on a phone as a Home Screen web app.

## What makes it different

| | Most hosted note-takers | Transcriber |
|---|---|---|
| Cost | Monthly fee or a free tier with tight limits | Free, on the free tiers of Groq and Gemini |
| Where your recordings live | Their servers | Your laptop |
| Joining calls | A bot joins your meeting | Captures sound from your own computer; nothing joins the call |
| Lecture notes | General summaries | Topic-grouped study notes with stressed points, key terms and an exam sheet |
| Trusting the notes | You hope they're right | Each item cites a timestamp, and a second model can fact-check the notes against the transcript |
| Speaker identification | Often voice-based | From the words, or from which audio source was loud. No voiceprints or voice training |
| Your classes | Not aware of them | Timetable-aware: missing classes, combined parts, reminders |
| Doing things for you | Fixed buttons | An assistant that skips classes, sets reminders and tidies the library, with Undo |
| Languages | Mostly English | English, any language you add, and mixed English-plus-your-language |

## How it works

A small Python web app (FastAPI and SQLite) runs on your laptop and serves the interface at
`http://127.0.0.1:8000`. Transcription uses Groq's Whisper; notes are written by the open-weight `gpt-oss-120b`
model and, if you set one up, fact-checked by a second model (Gemini by default); audio recaps are voiced locally. Everything is saved in a folder on your machine.

## Privacy

- Recordings, transcripts, notes and the database live on your laptop. Nothing is shared unless you share it.
- Every recording has a **privacy class** (Standard, Private or Local only), and a cloud service is used for it only when
  you have approved that exact setup for that class. Nothing is approved until you do it, in Settings → Privacy. A request
  built from several recordings counts as the strictest of them, and refusals are plain messages, never a quiet switch to
  another service. See `docs/privacy.md`.
- To transcribe and write notes, the app sends **audio and text to Groq** under your own free account. Groq's
  documentation says it does not retain inference inputs or outputs by default and does not train on customer
  data. It does keep usage metadata, and may keep data briefly to investigate abuse or reliability problems. Groq's
  *Data Controls* page has an **Inference APIs ZDR** switch that opts out of that storage; turn it on, then confirm it in
  the app before using Groq for Private recordings. It is a policy, not a guarantee, so read it yourself.
- **Ask** uses the Groq notes writer. Claude through your own account is available but off by default, and can never be used
  for Private recordings because its conversations are not zero-retention.
- **Gemini is optional** (fact-checking and mixed-language lectures) and is not set up by default. Google's
  free tier may use submitted content to improve its products, and humans may review it, so don't use a free
  Gemini key for private recordings such as coffee chats.
- The app never trains on, stores voiceprints of, or imitates anyone's voice.
- The app has no login, because it only listens on your own laptop. If you ever want to reach it from a phone,
  use a private connection (Tailscale), or add an access code first. Never expose it as it is.
- Tell people when you record them.

## Honest limits

- Depends on free-tier limits. About 8 hours of audio per day, and very long lectures take a few minutes to write
  up.
- Meeting capture works on **Windows**. It records everything playing through your speakers, so close other
  sounds, and use headphones so the call doesn't echo into your microphone.
- Accuracy in other languages, and in mixed-language lectures, is lower than in English. Speaker labels on uploads need your own GPU server.
- **Local only** recordings can be transcribed on the laptop with an optional local model, which is slow without a graphics
  card, and they get no AI notes.
- The spaced-repetition schedule follows the FSRS-5 model from memory of its reference, tested for sensible behaviour but
  not against the reference code.
- It's a single-user app, built around one student's workflow.

## Where it's headed

- A **master notes document** per subject, merged by topic across lectures.
- A **native Mac and iPhone app** with live transcription and local models.
