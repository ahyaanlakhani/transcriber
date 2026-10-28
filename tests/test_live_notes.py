"""Real notes models on the v1 meeting script (run with: pytest -m live -s). Needs Groq + Cerebras + Gemini keys."""
import sys
import uuid

import pytest

from transcriber.config import load_config
from transcriber.llm.factory import make_llm
from transcriber.notes.pipeline import NotesPipeline
from transcriber.pipeline import Pipeline
from transcriber.providers.groq import GroqTranscriber
from transcriber.store import Store

from .test_live import CHUNKING, SENTENCES, VOCABULARY, synthesize

pytestmark = pytest.mark.live


@pytest.mark.skipif(sys.platform != "win32", reason="uses Windows speech synthesis")
def test_live_notes(tmp_path):
    config = load_config()
    writer = make_llm(config.notes_writer, config.api_keys)
    checker = make_llm(config.notes_checker, config.api_keys)
    if not (config.groq_api_key and writer and checker):
        pytest.skip("needs GROQ_API_KEY, a notes writer key and a notes checker key")
    store = Store(tmp_path / "app.db")
    store.update_settings({"vocabulary": "\n".join(VOCABULARY)})
    rid = uuid.uuid4().hex
    rec_dir = tmp_path / "recordings" / rid
    rec_dir.mkdir(parents=True)
    synthesize(SENTENCES, tmp_path / "script.wav")
    (tmp_path / "script.wav").replace(rec_dir / "upload.wav")
    store.create_recording(rid, "Monday planning", None, "en", "script.wav")
    Pipeline(store, GroqTranscriber(config.groq_api_key), tmp_path / "recordings", **CHUNKING).process(rid)
    assert store.get_recording(rid).status == "done"

    NotesPipeline(store, writer, checker).process(rid)

    record = store.get_notes(rid)
    assert record.status == "ready", record.status_detail
    notes = record.notes
    for section in ("summary", "decisions", "action_items", "open_questions"):
        for item in getattr(notes, section):
            print(f"{item.id} [{item.verdict}] {item.text} owner={item.owner} due={item.due} cites={item.cites}"
                  + (f"\n    reason: {item.reason}" if item.reason else ""))
    print("missed:", [m.text for m in notes.missed], "| topics:", notes.topics, "| check:", record.check_detail)
    decisions = " ".join(i.text.lower() for i in notes.decisions + notes.action_items)
    assert "saturday" in decisions
    assert any("jordan" in (i.text + " " + (i.owner or "")).lower() and "wednesday" in
               (i.text + " " + (i.due or "")).lower() for i in notes.action_items)
    assert record.check_detail is None, record.check_detail
    assert all(item.verdict is not None for item in notes.items())
    assert all(item.cites for item in notes.items())
    store.close()
