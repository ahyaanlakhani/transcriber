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

## An online class you only listen to

In **Meeting**, untick **Also record my microphone**. The recording is then only the sound that plays through your
speakers (the teacher and classmates), every line is labelled **Others**, and your microphone isn't opened at all. Tick
**Also record my screen** as well to keep the slides and shared screen. Both choices are remembered.

While a meeting is recording the app asks Windows not to go to sleep (and to keep the display on if the screen is being
recorded), and lets go the moment you stop. Sleep would otherwise end the recording.

## Recording your screen too

Tick **Also record my screen** before you press Start. It is off unless you tick it, and it works with every way of
recording: Meeting, Microphone, Computer sound and Both. The screen is recorded by the laptop app itself, so it doesn't
matter how the audio is captured.

- **What it records:** the whole of your main screen, at 5 frames a second and at most 1280 pixels wide, so slides and
  shared screens stay readable. While it runs, the recording bar says **Recording your screen**.
- **Where it goes:** the video is saved on this laptop next to the recording's audio. It is never sent to a
  transcription or notes provider, and nothing in the app reads it except your own browser, to play it back.
- **Watching it:** on the recording's page the video sits above the transcript. Click a transcript line and the
  video jumps to the same moment. It plays with the audio and has no sound of its own.
- **Size:** about 40–100 MB an hour, more on a busy screen. The recording page shows the video's size. If the disk gets
  under 5 GB free while you record, the app warns you, and under 1 GB it stops the video (the audio carries on) rather
  than fill the disk.
- **Download it:** **Download video with sound** on the recording gives you one MP4 with the picture and the sound
  together, in step, that plays with sound and skips around in any video player. It is put together when you click
  (a few seconds) and not kept, so it takes no space in the app. The video you watch in the app has no sound of its
  own; the audio player below it plays the sound.
- **Delete it separately:** **Delete video** on the recording removes only the video. The audio, transcript and notes
  stay. Deleting the whole recording removes its video too.
- **If it stops:** the meeting is still recorded. If the screen recorder stops (the screen was locked, the display
  changed), the app starts it again after a few seconds, says so under the transcript, and fills the missing time with
  black so the video stays in step with the audio. If it can never start, the bar says why.
- **It can't be left running.** The recorder is tied to the app, so if the app is closed, killed or crashes the
  recorder ends with it. Pieces are saved so that a cut-off loses only the last few seconds.
- **Everything on the screen is recorded**, including notifications, passwords you type and other windows. Close what
  is private first, and tell the people on the call, as with the audio. Some workplaces don't allow screen
  recording.
- **Pause** isn't available in Microphone, Computer sound or Both while the screen is recorded, because a paused audio
  track would drift out of step with the video. (Meeting has no pause.)
- Only the primary monitor is recorded, and only on Windows for now.

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
- `transcriber/screen.py` runs one ffmpeg process (Windows screen capture, H.264) that writes the screen in ten-minute
  pieces, so a crash loses at most the piece being written. When the meeting is saved the pieces are joined, with no
  re-encoding, into `screen.mp4` next to the audio, along with `screen.json`: how many seconds the video starts after
  the audio, which is what keeps the two in step.
- Windows only for now. On a Mac (the plan in `docs/future/live-recorder-native-app.md`) it would use a
  different capture.
