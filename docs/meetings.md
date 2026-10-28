# Recording Zoom, Teams and Meet meetings

The laptop app can record a call straight from Windows. You don't share a screen or tick "Share audio", and it
doesn't matter which browser tab or window is in front.

## How to use it

1. Open **Record live** and choose **Meeting**.
2. Optionally set a title and subject, then press **Start recording**. Or tell Pip: "record my meeting".
3. Join your call as usual. Text appears about 10–20 seconds behind, and each line says **You** or **Others**.
4. Press **Stop and save** when the call ends. The notes follow a few minutes later.

Tell everyone on the call that you're recording. Zoom also announces it if you use its own recording.

## What you get

- **A transcript with You and Others.** A line is "You" when your microphone carried it, and "Others" when the
  call's sound did. This is decided by which source was loud, never by listening to or learning anyone's voice.
  You can rename the labels on the recording like any speaker names.
- **Meeting notes:** summary, decisions, action items with owners and deadlines, open questions.
- **Attendees:** the names the transcript gives. "You" and "Others" are never listed as names.
- **A follow-up message** you could send after the meeting, with the decisions and who does what.
- **Both tracks are kept:** your microphone and the call's sound are saved separately with the recording, so
  lines can be labelled again later (for example after re-transcribing).

## What it records

- The sound that plays through your **default speakers or headphones** (the people on the call), plus your
  **default microphone**. Any other sound playing on the PC at the same time is recorded too, so close music and
  videos, or mute notification sounds.
- **Use headphones.** With speakers, the call leaks into your microphone. Labels still work (the call's sound is
  louder on its own track), but a loud room can mislabel a few lines.
- If you talk over the call, the line is labelled **You**.

## If it won't start

- *"Windows wouldn't let me open the microphone or speakers":* in Windows Settings, Privacy & security,
  Microphone, turn on **Let desktop apps access your microphone**.
- *Meeting is greyed out:* the app couldn't find speakers or a microphone. Check Windows' Sound settings, then
  reopen the Record screen.
- Only one meeting can be recorded at a time. Stop the current one first.

## Under the hood

- `transcriber/meeting.py` reads the speakers' loopback and the microphone (PyAudioWPatch), keeps them in step with
  the clock, and feeds the live transcription that the browser recorder already uses.
- `transcriber/channels.py` labels each line You or Others from the two tracks.
- Windows only for now. On a Mac (the plan in `docs/future/live-recorder-native-app.md`) it would use a
  different capture.
