"""Run the app with fake transcription and fake AI notes, to try the UI without any API keys.

Usage:  python scripts/demo_server.py [folder]   →  http://127.0.0.1:8001  (data in ./demo-data by default)
For a library full of made-up lectures first run:  python scripts/seed_demo.py
"""
import itertools
import sys
import time
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from transcriber.app import create_app  # noqa: E402
from transcriber.config import Config  # noqa: E402
from transcriber.providers.base import Segment  # noqa: E402

SENTENCES = [
    "Okay, let's get started with the standup.",
    "Yesterday I finished the onboarding flow and opened a pull request.",
    "Today I'm picking up the billing bug that support flagged.",
    "No blockers on my side, but I need a review from Priya.",
    "Quick update on the client call: they want the report by Friday.",
    "Let's sync after lunch to split the remaining tasks.",
]


class DemoTranscriber:
    name = "groq/whisper-large-v3"

    def __init__(self):
        self._sentences = itertools.cycle(SENTENCES)

    def transcribe(self, audio_path, *, language, prompt):
        time.sleep(1.5)  # so progress is visible in the UI
        segments, t = [], 0.0
        count = 6
        if Path(audio_path).suffix == ".wav":  # a live-recording window: one sentence per ~5 s
            import wave
            with wave.open(str(audio_path)) as w:
                count = max(1, round(w.getnframes() / w.getframerate() / 5))
        for i in range(count):
            segments.append(Segment(t, t + 4.0, next(self._sentences), -0.2, 0.01, 1.3))
            t += 7.0 if i % 3 == 2 else 4.5
        return segments


class DemoWriter:
    name = "groq:openai/gpt-oss-120b"

    def complete_json(self, *, system, user, schema, schema_name, effort=None):
        time.sleep(1.5)
        return {
            "summary": [
                {"text": "Daily standup: onboarding flow is done and the billing bug is next.", "cites": ["[00:00]"]},
                {"text": "The client wants the report by Friday.", "cites": ["[00:16]"]},
            ],
            "decisions": [{"text": "Split the remaining tasks after lunch.", "cites": ["[00:16]"]}],
            "action_items": [{"text": "Review the onboarding pull request.", "owner": "Priya", "due": None,
                              "cites": ["[00:00]"]},
                             {"text": "Send the client report.", "owner": None, "due": "Friday", "cites": ["[00:16]"]}],
            "open_questions": [{"text": "Who owns the billing bug fix after today?", "cites": []}],
            "topics": ["onboarding", "billing", "client report"],
        }


class DemoChecker:
    name = "gemini:gemini-3.8-flash"

    def complete_json(self, *, system, user, schema, schema_name, effort=None):
        time.sleep(1.0)
        return {
            "verdicts": [
                {"id": "S1", "verdict": "supported", "reason": "Both updates are stated."},
                {"id": "S2", "verdict": "supported", "reason": "Said in the client update."},
                {"id": "D1", "verdict": "partial", "reason": "It was proposed as a sync, not clearly decided."},
                {"id": "A1", "verdict": "supported", "reason": "A review from Priya was requested."},
                {"id": "A2", "verdict": "partial", "reason": "Friday is stated, but no one was named to send it."},
                {"id": "Q1", "verdict": "unsupported", "reason": "Nobody asked who owns the fix."},
            ],
            "missed": [{"text": "No blockers were reported.", "cites": ["[00:09]"]}],
        }



class DemoAsker:
    name = "claude-code"

    def complete_json(self, *, system, user, schema, schema_name, effort=None):
        time.sleep(1.0)
        question = user.rsplit("LATEST MESSAGE:", 1)[-1].strip().lower()
        if any(word in question for word in ("change", "shorter", "add", "remove", "make")):
            notes = DemoWriter().complete_json(system="", user="", schema={}, schema_name="")
            notes["summary"] = notes["summary"][:1]
            notes["action_items"].append({"text": "Book a room for the sync.", "owner": "Priya", "due": "Today",
                                          "cites": ["[00:16]"]})
            return {"answer": "I shortened the summary to one point and added the room booking.",
                    "proposed_notes": notes}
        return {"answer": "Priya was asked to review the pull request [00:00]; the client report is due Friday [00:16].",
                "proposed_notes": None}


class DemoRemote:
    """Pretend "own server": answers instantly, with two speakers taking turns."""

    name = "server/large-v3"
    last_error = None

    def __init__(self):
        self._jobs: dict[str, int] = {}

    def health(self):
        return {"ok": True, "model": "large-v3", "device": "cuda", "speakers": True}

    def submit(self, path, *, language, prompt, speakers):
        job_id = f"demo{len(self._jobs) + 1}"
        self._jobs[job_id] = 0
        self._speakers = speakers
        return job_id

    def poll(self, job_id):
        self._jobs[job_id] += 1
        if self._jobs[job_id] < 3:
            return {"status": "running", "stage": "speakers" if self._jobs[job_id] == 2 else "transcribing",
                    "progress": self._jobs[job_id] / 3}
        segments, t = [], 0.0
        for i, text in enumerate(SENTENCES):
            speaker = (f"SPEAKER_0{0 if i in (0, 4, 5) else 1}") if self._speakers else None
            segments.append({"start": t, "end": t + 4.0, "text": text, "speaker": speaker})
            t += 5.0
        return {"status": "done", "segments": segments}

    def delete(self, job_id):
        self._jobs.pop(job_id, None)


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "demo-data"  # run scripts/seed_demo.py first for a full library
    config = Config(groq_api_key=None, data_dir=folder, port=8001)
    app = create_app(config, transcriber=DemoTranscriber(), writer=DemoWriter(), checker=DemoChecker(),
                     ask_engine=DemoAsker(), remote=DemoRemote())
    print("Demo UI at http://127.0.0.1:8001 (fake transcripts and notes)")
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
