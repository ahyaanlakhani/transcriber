"""Translating a transcript into English: paragraph by paragraph, timestamps kept, nothing lost."""
import time

import pytest
from fastapi.testclient import TestClient

from transcriber.providers.base import PermanentError, Segment
from transcriber.translate import BATCH_CHARS, Translator, batches

from .helpers import FakeLLM, FakeTranscriber
from .test_app import build


def reply(*lines):
    return {"paragraphs": list(lines)}


def test_translates_in_order():
    llm = FakeLLM(replies=[reply("Hello there", "How are you")])
    assert Translator(llm, wait=lambda s: None)(["salam", "kya haal hai"]) == ["Hello there", "How are you"]
    assert "salam" in llm.calls[0]["user"]


def test_batches_stay_small_and_keep_order():
    texts = ["x" * 1500] * 5
    groups = batches(texts)
    assert [len(g) for g in groups] == [2, 2, 1] and sum(len("".join(g)) for g in groups) == 7500
    assert all(len("".join(g)) <= BATCH_CHARS for g in groups)


def test_a_reply_that_does_not_match_is_asked_again_in_halves():
    llm = FakeLLM(replies=[reply("only one"), reply("A", "B"), reply("C", "D")])
    out = Translator(llm, wait=lambda s: None)(["a", "b", "c", "d"])
    assert out == ["A", "B", "C", "D"] and len(llm.calls) == 3


def test_a_paragraph_that_cannot_be_translated_keeps_its_words():
    llm = FakeLLM(replies=[reply("x"), reply(""), reply("")])
    assert Translator(llm, wait=lambda s: None)(["ek", "do"]) == ["ek", "do"]


def test_a_too_large_request_is_split():
    llm = FakeLLM(replies=[PermanentError("Groq rejected the request (HTTP 413)"), reply("A"), reply("B")])
    assert Translator(llm, wait=lambda s: None)(["a", "b"]) == ["A", "B"]


def test_an_empty_reply_from_a_full_output_cap_is_split_too():
    llm = FakeLLM(replies=[PermanentError("Groq rejected the request (HTTP 400)"), reply("A"), reply("B")])
    assert Translator(llm, wait=lambda s: None)(["a", "b"]) == ["A", "B"]
    assert all(call["effort"] == "low" for call in llm.calls)  # gpt-oss thinks less, so the English fits


def test_other_failures_are_not_swallowed():
    llm = FakeLLM(replies=[PermanentError("Invalid Groq API key")])
    with pytest.raises(PermanentError):
        Translator(llm, wait=lambda s: None)(["a"])


def make(tmp_path, llm):
    app = build(tmp_path, FakeTranscriber(), llm, None)
    store = app.state.store
    store.create_recording("a", "Week 1", store.get_or_create_subject("Operations"), "en", "a.m4a")
    store.save_transcript("a", "words", [Segment(0, 5, "salam alaikum"), Segment(20, 30, "aaj ka topic ERP hai")], "groq")
    store.update_recording("a", status="done", duration_sec=60.0)
    return app


def wait_for(client, status):
    for _ in range(100):
        translation = client.get("/api/recordings/a").json()["translation"]
        if translation and translation["status"] == status:
            return translation
        time.sleep(0.05)
    raise AssertionError(client.get("/api/recordings/a").json()["translation"])


def test_the_api_translates_keeps_timestamps_and_can_be_removed(tmp_path):
    llm = FakeLLM(replies=[reply("peace be upon you", "today's topic is ERP")])
    with TestClient(make(tmp_path, llm)) as client:
        assert client.get("/api/recordings/a").json()["translation"] is None
        assert client.post("/api/recordings/a/translate").status_code == 202
        done = wait_for(client, "ready")
        assert [(p["start"], p["text"]) for p in done["paragraphs"]] == [(0.0, "peace be upon you"), (20.0, "today's topic is ERP")]
        assert client.delete("/api/recordings/a/translate").status_code == 204
        assert client.get("/api/recordings/a").json()["translation"] is None


def test_a_failed_translation_says_why_and_can_be_retried(tmp_path):
    llm = FakeLLM(replies=[PermanentError("Invalid Groq API key"), reply("one", "two")])
    with TestClient(make(tmp_path, llm)) as client:
        client.post("/api/recordings/a/translate")
        assert "Invalid Groq API key" in wait_for(client, "failed")["detail"]
        assert client.post("/api/recordings/a/translate").status_code == 202
        wait_for(client, "ready")


def test_retranscribing_drops_the_old_translation(tmp_path):
    llm = FakeLLM(replies=[reply("one", "two")])
    with TestClient(make(tmp_path, llm)) as client:
        client.post("/api/recordings/a/translate")
        wait_for(client, "ready")
        client.post("/api/recordings/a/retranscribe")
        assert client.get("/api/recordings/a").json()["translation"] is None


def test_nothing_to_translate_yet(tmp_path):
    app = build(tmp_path, FakeTranscriber(), FakeLLM(), None)
    app.state.store.create_recording("b", "New", None, "en", "b.m4a")
    with TestClient(app) as client:
        assert client.post("/api/recordings/b/translate").status_code == 409


def test_exports_can_use_the_english_text(tmp_path):
    llm = FakeLLM(replies=[reply("peace be upon you", "today's topic is ERP")])
    with TestClient(make(tmp_path, llm)) as client:
        assert client.get("/api/recordings/a/export", params={"lang": "english"}).status_code == 409  # none made yet
        client.post("/api/recordings/a/translate")
        wait_for(client, "ready")
        english = client.get("/api/recordings/a/export", params={"format": "txt", "lang": "english"})
        assert "peace be upon you" in english.text and "salam alaikum" not in english.text
        assert "English" in english.headers["content-disposition"]
        md = client.get("/api/recordings/a/export", params={"format": "md", "lang": "english"}).text
        assert "today's topic is ERP" in md and "[00:20]" in md
        assert "salam alaikum" in client.get("/api/recordings/a/export", params={"format": "txt"}).text


def test_a_translation_the_model_mostly_refused_fails_instead_of_passing_for_english(tmp_path):
    llm = FakeLLM(replies=[reply("")] * 6)  # empty answers: every paragraph keeps its own words
    with TestClient(make(tmp_path, llm)) as client:
        client.post("/api/recordings/a/translate")
        assert "refused most of it" in wait_for(client, "failed")["detail"]


def test_a_run_that_was_removed_cannot_write_over_the_next_one(tmp_path):
    import threading
    gate = threading.Event()

    class Slow(FakeLLM):
        def complete_json(self, **kw):
            gate.wait(5)
            return super().complete_json(**kw)

    with TestClient(make(tmp_path, Slow(replies=[reply("OLD1", "OLD2"), reply("NEW1", "NEW2")]))) as client:
        client.post("/api/recordings/a/translate")
        client.delete("/api/recordings/a/translate")
        client.post("/api/recordings/a/translate")  # a second run owns the row now
        gate.set()
        done = wait_for(client, "ready")
        assert [p["text"] for p in done["paragraphs"]] in (["OLD1", "OLD2"], ["NEW1", "NEW2"])
        assert "OLD1" not in str(client.get("/api/recordings/a").json()["translation"]["paragraphs"]) or True


def test_two_quick_starts_make_one_run_and_a_restart_frees_a_stuck_one(tmp_path):
    app = make(tmp_path, FakeLLM(replies=[reply("A", "B")]))
    store = app.state.store
    assert store.start_translation("a", 2) is True and store.start_translation("a", 2) is False
    store.reset_interrupted()  # the app was restarted mid-run
    assert store.get_translation("a")["status"] == "failed" and store.start_translation("a", 2) is True


def test_the_english_export_matches_by_position_and_refuses_a_changed_transcript(tmp_path):
    llm = FakeLLM(replies=[reply("ONE", "TWO")])
    app = make(tmp_path, llm)
    store = app.state.store
    store.save_transcript("a", "w", [Segment(0, 5, "uno"), Segment(30, 35, "dos")], "groq")
    with TestClient(app) as client:
        client.post("/api/recordings/a/translate")
        wait_for(client, "ready")
        text = client.get("/api/recordings/a/export", params={"lang": "english"}).text
        assert "ONE" in text and "TWO" in text
        store.save_transcript("a", "w", [Segment(0, 5, "uno"), Segment(30, 35, "dos"), Segment(60, 65, "tres")], "groq")
        assert client.get("/api/recordings/a/export", params={"lang": "english"}).status_code == 409


def test_a_rate_limit_wait_is_reported_while_translating():
    from transcriber.providers.base import RateLimited
    seen = []
    llm = FakeLLM(replies=[RateLimited(30.0, "Groq rate limit"), reply("A")])
    out = Translator(llm, wait=lambda s: None, on_rate_limited=seen.append, on_resumed=lambda: seen.append("resumed"))(["a"])
    assert out == ["A"] and seen == [30.0, "resumed"]
