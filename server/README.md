# Your own transcription server

This folder runs on a **different machine** from the one with the Meeting Transcriber app: ideally a PC with an
NVIDIA graphics card. It transcribes with Whisper large-v3 (the same model Groq runs) and labels who is speaking
with pyannote "community-1".

When "Prefer my server" is on in the app's Settings, or when you tick "Label speakers" on a recording, the app
sends the recording here. If this machine is off or busy failing, the app quietly uses Groq instead.

Rough speed for 8 hours of audio:
- NVIDIA card with 8 GB or more of memory: 10–20 minutes.
- A recent CPU only: several hours. It works, but leave it running overnight.

## Setup (once)

1. **Install Python 3.12** from python.org. Tick "Add python.exe to PATH". Use 3.12, not newer: PyTorch and
   pyannote publish packages for it first.

2. **Copy this `server` folder** to the machine, for example to `C:\transcribe-server`.

3. **Create the environment.** Open a terminal in that folder and run:

   ```
   python -m venv venv
   venv\Scripts\activate
   ```

   On Linux or macOS, use `source venv/bin/activate` instead of the second line.

4. **Install PyTorch.**
   - With an NVIDIA card: `pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128`
   - CPU only: `pip install torch torchaudio`

5. **Install the rest:** `pip install -r requirements.txt`

6. **Speaker labels need a free HuggingFace token.** Skip this step if you only want faster transcription.
   1. Make an account at huggingface.co.
   2. Open huggingface.co/pyannote/speaker-diarization-community-1 and accept the conditions.
   3. Go to huggingface.co/settings/tokens and create a token of type "Read".

7. **Settings.** Copy `.env.example` to `.env` and fill in:
   - `TRANSCRIBE_SERVER_TOKEN`: a long random text. Make one with
     `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
   - `HF_TOKEN`: the HuggingFace token from step 6.

8. **Start it:** double-click `start.bat`, or run `./start.sh` on Linux or macOS.
   - The first start downloads the models (about 3 GB), so give it a few minutes.
   - It's ready when it prints `Ready on port 8765`.
   - If Windows asks about the firewall, allow **Private networks only**.

9. **Find this machine's address.** On Windows, run `ipconfig` and look for the "IPv4 Address", for example
   `192.168.1.20`.

## Connect the app (on your laptop)

Add two lines to the app's `.env` file, then restart the app:

```
TRANSCRIBE_SERVER_URL=http://192.168.1.20:8765
TRANSCRIBE_SERVER_TOKEN=<the same token as in this folder's .env>
```

In the app, open Settings and press **Test** next to "Your server". Then turn on **Prefer my server** if you want
every recording to go there, or tick **Label speakers** only on the recordings where you want speakers.

## Start it automatically

- **Windows:** open Task Scheduler and choose "Create Basic Task…".
  - Trigger: "When I log on".
  - Action: "Start a program", pointing to `start.bat` in this folder.
- **Linux:** create a systemd service that runs `start.sh`.

## Safety

- Only machines that know the token can use the server.
- Keep it on your home or office network: don't forward port 8765 on your router. To reach it from outside, use a
  private VPN such as Tailscale and use the machine's Tailscale address in `TRANSCRIBE_SERVER_URL`.
- Uploaded audio is deleted as soon as it's transcribed. Results are kept in memory for up to 24 hours, or until
  the app collects them.

## Troubleshooting

- **"Could not load library cudnn…"**: run `pip install nvidia-cudnn-cu12 nvidia-cublas-cu12`, then start again.
- **Speakers are off in the app:** the server prints "No HF_TOKEN" at startup. Check step 6, and that you accepted
  the model's conditions.
- **The app says "Token rejected":** the two `.env` files have different `TRANSCRIBE_SERVER_TOKEN` values.
- **Out of memory on the graphics card:** set `WHISPER_MODEL=large-v3-turbo` in `.env`. It's lighter and almost as
  accurate.
