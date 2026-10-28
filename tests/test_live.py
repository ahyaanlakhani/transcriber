"""End-to-end accuracy check against the real Groq API (run with: pytest -m live -s)."""
import subprocess
import sys
import uuid

import pytest

from transcriber.config import load_config
from transcriber.pipeline import Pipeline
from transcriber.providers.groq import GroqTranscriber
from transcriber.store import Store

from .wer import normalize_words, word_error_rate

pytestmark = pytest.mark.live

VOCABULARY = ["Tessaline Varga", "Quillbrook", "Jordan", "Pellucid", "Okonkwo"]

SENTENCES = [
    "Good morning everyone, this is the Monday planning meeting for the Quillbrook project.",
    "Tessaline Varga will start with an update on the customer research.",
    "We spoke with eight customers last week and most of them asked for better search.",
    "Jordan suggested that we build the search feature before the new dashboard.",
    "The design team agreed, but they want another week to finish the mockups.",
    "Pellucid is the name we picked for the new reporting tool.",
    "The first version of Pellucid should only export simple tables.",
    "Charts and scheduled emails can wait until the second version.",
    "Okonkwo raised a concern about the database migration.",
    "He thinks the migration could slow down the servers during business hours.",
    "We decided to run the migration on Saturday night instead.",
    "Tessaline will write the announcement for the support team.",
    "Jordan will review the pull requests for the search feature by Wednesday.",
    "The marketing team asked whether Quillbrook will be ready for the conference.",
    "We told them it depends on the results of the testing next week.",
    "Okonkwo will set up the test environment and share the results on Thursday.",
    "If testing goes well, we will plan the public launch for the end of the month.",
    "Before we finish, does anyone have any other questions or concerns?",
    "Nobody had any further questions, so the meeting ended a few minutes early.",
    "Thank you all for your time, and have a productive week.",
]

# Small chunks so the test also exercises silence-based cuts between chunks.
CHUNKING = {"target_sec": 45.0, "window_sec": 15.0, "max_tail_sec": 60.0}


def synthesize(sentences: list[str], wav_path) -> None:
    """Windows' built-in speech synthesizer reads the sentences with 0.8 s pauses."""
    text_file = wav_path.with_suffix(".txt")
    text_file.write_text("\n".join(sentences), encoding="utf-8")
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$p = New-Object System.Speech.Synthesis.PromptBuilder;"
        f"foreach ($line in Get-Content -LiteralPath '{text_file}' -Encoding UTF8)"
        " { $p.AppendText($line); $p.AppendBreak([TimeSpan]::FromMilliseconds(800)) };"
        f"$s.SetOutputToWaveFile('{wav_path}'); $s.Speak($p); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True)


@pytest.mark.skipif(sys.platform != "win32", reason="uses Windows speech synthesis")
def test_live_accuracy(tmp_path):
    key = load_config().groq_api_key
    if not key:
        pytest.skip("GROQ_API_KEY not set")
    store = Store(tmp_path / "app.db")
    store.update_settings({"vocabulary": "\n".join(VOCABULARY)})
    rid = uuid.uuid4().hex
    rec_dir = tmp_path / "recordings" / rid
    rec_dir.mkdir(parents=True)
    # Synthesize outside the recording folder: synthesize() leaves a script.txt beside the
    # wav, and the pipeline treats any "upload.*" file in the folder as the upload.
    synthesize(SENTENCES, tmp_path / "script.wav")
    (tmp_path / "script.wav").replace(rec_dir / "upload.wav")
    store.create_recording(rid, "Live test", None, "en", "script.wav")

    Pipeline(store, GroqTranscriber(key), tmp_path / "recordings", **CHUNKING).process(rid)

    rec = store.get_recording(rid)
    assert rec.status == "done", rec.status_detail
    assert rec.progress_total >= 2
    hypothesis = store.get_transcript(rid).text
    reference = " ".join(SENTENCES)
    wer = word_error_rate(reference, hypothesis)
    print(f"\nWER: {wer:.2%} over {len(normalize_words(reference))} words, {rec.progress_total} chunks")
    print(hypothesis)
    assert wer <= 0.05
    hyp_words = " ".join(normalize_words(hypothesis))
    for term in VOCABULARY:
        assert " ".join(normalize_words(term)) in hyp_words, f"{term!r} misspelled"
    store.close()
