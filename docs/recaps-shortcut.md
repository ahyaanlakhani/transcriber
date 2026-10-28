# Recaps on your iPhone: the "Get my recaps" shortcut

After each lecture's notes are ready, the laptop makes a **recap**: a spoken summary of about five minutes. This
shortcut copies the newest recap of every subject to your iPhone each morning, so you can listen on the way to
class with no signal needed.

The recaps land in **Files → Transcriber Recaps**, named like `Machine Learning – Wed 7 Oct.mp3`. Each run replaces
a subject's older recap with the newest one, so the folder holds one file per subject.

**Before you start:** phone access must already work (Tailscale, step 1 of [phone-setup.md](phone-setup.md)).
Below, `https://YOUR-LAPTOP.ts.net` stands for your address, for example
`https://my-laptop.tail1234.ts.net`.

Only recordings that are in a **subject** are included. Recordings without one still get a recap on their
sheet, where you can play or download it.

## 1. Make the folder (once)

In the **Files** app, go to **On My iPhone**, long-press an empty area, and choose **New Folder**. Name it
**Transcriber Recaps**.

## 2. Build the shortcut (once, about 5 minutes)

In **Shortcuts**, tap **+** and name it **Get my recaps**. Add these actions in order:

1. **Get Contents of URL**: `https://YOUR-LAPTOP.ts.net/api/recaps/latest`
2. **Repeat with Each** item in *Contents of URL*. Inside the repeat:
   1. **Get Dictionary Value**: get the value for `url` in *Repeat Item*.
   2. **Text**: `https://YOUR-LAPTOP.ts.net` immediately followed by the *Dictionary Value* variable (no space).
   3. **Get Contents of URL**: the *Text* from the step before. This downloads the MP3.
   4. **Get Dictionary Value**: get the value for `filename` in *Repeat Item*.
   5. **Set Name**: set the name of *Contents of URL* to the *Dictionary Value* from step 4.
   6. **Save File**: save *Renamed Item* to **Transcriber Recaps**. Turn **Ask Where to Save off** and
      **Overwrite If File Exists on**.
   7. **Get Dictionary Value**: get the value for `subject` in *Repeat Item*.
   8. **Get Contents of Folder**: **Transcriber Recaps**.
   9. **Filter Files**: where **Name begins with** the *subject* value from step 7 **and Name is not** the
      *filename* value from step 4.
   10. **Delete Files**: the filtered files. Open the action's options and turn **Confirm Before Deleting off**.
3. **End Repeat**, then **Show Notification**: "Recaps ready".

Tap the play button to test it. The first time, iOS asks for permission to reach your laptop and to use the
folder. Allow both.

## 3. Run it every morning by itself

In Shortcuts, go to **Automation → +**:

1. Choose **Time of Day**, for example **7:30 AM**, repeating on **weekdays**.
2. Choose **Run Immediately** and turn **Notify When Run** off.
3. For the action, pick **Get my recaps**.

It only works when **Tailscale is on** on the phone and the **laptop is on and awake** at that time. If either is
off, the run fails quietly and your previous recaps stay in the folder.

## Listening

Open **Files → On My iPhone → Transcriber Recaps** and tap a file. Audio keeps playing with the screen locked. To
change the voice or speed of future recaps, use **Settings → Recaps** in the app. To remake one recap, use
**Remake** on its sheet.
