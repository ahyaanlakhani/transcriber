# Using Transcriber on your iPhone and iPad

Record in **Voice Memos** as usual: it records with the screen off and barely touches the battery. When you're
done, send the memo to Transcriber with one tap from the Share menu. To read your notes, ask questions or browse
your library, open Transcriber from your Home Screen.

Your laptop does the work, so it has to be on and running the app when you send a memo. If it isn't, the shortcut
says it couldn't connect. Nothing is lost: the memo is still in Voice Memos, so send it again later.

## 1. Connect the phone and the laptop with Tailscale (once, about 10 minutes)

Tailscale is a free private network for your own devices. Your phone reaches the laptop from anywhere (lecture
hall, café, mobile data), and nobody else can. The app itself still only listens on the laptop.

1. **On the laptop**, install Tailscale from tailscale.com/download and sign in. Google, Microsoft or Apple
   accounts all work.
2. Open the Tailscale admin page at login.tailscale.com/admin/dns:
   - Check that **MagicDNS** is on (it usually is).
   - Under **HTTPS Certificates**, press **Enable HTTPS**. The iPhone only allows the microphone and the Home
     Screen app on https addresses, and this gives the laptop a real one.
3. With the Transcriber app running, open a terminal on the laptop and run:

   ```
   tailscale serve --bg 8000
   ```

   It prints your address, something like `https://my-laptop.tail1234.ts.net`. Write it down. You only do this
   once: it keeps working after restarts.
4. **Tell the app its phone address.** The app only answers to this computer by default, so that a web page you
   visit can never reach it. Add your laptop's Tailscale name (the one `tailscale serve` printed, like
   `my-laptop.tail1234.ts.net`) to the `.env` file, then restart the app:

   ```
   TRANSCRIBER_ALLOWED_HOSTS=my-laptop.tail1234.ts.net
   ```
5. **On the iPhone and iPad**, install Tailscale from the App Store and sign in with the **same account**. Leave
   it connected. It uses very little battery.
6. On the phone, open your address in **Safari**. Transcriber should load.

## 2. Put Transcriber on your Home Screen

In Safari, with Transcriber open: tap **Share**, then **Add to Home Screen**, then **Add**. It opens full-screen
like an app, with its own icon.

## 3. Make the "Send to Transcriber" shortcut (once, about 5 minutes)

In the **Shortcuts** app, tap **+** and name the shortcut **Send to Transcriber**. Then:

1. Tap the **ⓘ** (details) button and turn on **Show in Share Sheet**. Back in the editor, tap the input line at
   the top ("Receive … from Share Sheet") and keep only **Audio** and **Files**.
2. Add **Get Contents of URL**, with the URL `https://YOUR-ADDRESS/api/share/subjects`. This fetches your list of
   subjects.
3. Add **Split Text**: split *Contents of URL* by **New Lines**.
4. Add **Choose from List**: choose from *Split Text*, with the prompt "Subject".
5. Add a second **Get Contents of URL**:
   - URL: `https://YOUR-ADDRESS/api/share`
   - Tap **Show More** and set **Method** to **POST** and **Request Body** to **Form**.
   - Add a field named `file` with the type **File**, and choose **Shortcut Input** as its value.
   - Add a field named `subject` with the type **Text**, and choose **Chosen Item** as its value.
6. Add **Show Notification** with *Contents of URL* as the text. It reads like "Sent “Lecture 4” to Emerging
   Tech. The transcript and notes will follow."

Replace `YOUR-ADDRESS` with the address from step 1.3, without a slash at the end.

## Every day

1. Record in Voice Memos, screen off.
2. Afterwards, open the memo, tap **…** or **Share**, then **Send to Transcriber**, then pick the subject.
3. The memo's name becomes the title, so rename it in Voice Memos first if you like, e.g. "Lecture 4". You can
   also rename it later in the app.
4. A few minutes later, open Transcriber from your Home Screen: transcript, then notes.

## Good to know

- **Long recordings:** a 1-hour memo is about 30–60 MB. Send long ones on Wi-Fi if you can.
- **Sending the same memo twice** makes two copies. Delete one from its ⋯ menu in the app.
- **Speaker labels** need your own server (see `server/README.md`). Without it, memos are transcribed by Groq as
  usual. To get speakers later, re-transcribe from the app with "Speakers" ticked.
- **Privacy:** only devices signed in to your Tailscale account can open the address. Don't share your Tailscale
  network with other people unless you're happy for them to see your library.
