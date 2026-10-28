"""Learn: the spaced-repetition scheduler, cards made from notes, the review flow, and teach-back."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from transcriber import fsrs, learn
from transcriber.notes.model import notes_from_writer
from transcriber.store import Store

from .helpers import FakeLLM, FakeTranscriber
from .test_app import build
from .test_app_brain import seed

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
TZ = "Asia/Tashkent"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


# ---- the scheduler

def test_first_answers_set_how_long_before_you_see_it_again():
    again, hard, good, easy = (fsrs.review(fsrs.Memory(), r, NOW) for r in (1, 2, 3, 4))
    assert again.due - NOW == timedelta(minutes=10)
    gaps = [(m.due - NOW).days for m in (hard, good, easy)]
    assert gaps == sorted(gaps) and gaps[0] >= 1 and gaps[2] > gaps[1]  # easier answers wait longer
    assert easy.difficulty < good.difficulty < hard.difficulty < again.difficulty  # and mark the card as easier


def test_remembering_stretches_the_gap_and_forgetting_cuts_it():
    memory = fsrs.review(fsrs.Memory(), fsrs.GOOD, NOW)
    gaps = [(memory.due - NOW).days]
    for _ in range(4):  # answer Good each time it comes due
        memory = fsrs.review(memory, fsrs.GOOD, memory.due)
        gaps.append((memory.due - memory.last_review).days)
    assert gaps == sorted(gaps) and gaps[-1] > 3 * gaps[0]
    stable = memory.stability
    forgot = fsrs.review(memory, fsrs.AGAIN, memory.due)
    assert forgot.stability < stable and forgot.lapses == 1 and forgot.due - memory.due == timedelta(minutes=10)


def test_retrievability_falls_with_time_and_is_ninety_percent_at_the_stability():
    assert fsrs.retrievability(0, 5.0) == 1.0
    assert fsrs.retrievability(5.0, 5.0) == pytest.approx(0.9, abs=1e-9)
    assert fsrs.retrievability(20.0, 5.0) < fsrs.retrievability(5.0, 5.0)
    assert fsrs.interval_days(5.0, 0.9) == pytest.approx(5.0, abs=1e-9)  # the gap for 90% retention is the stability
    with pytest.raises(ValueError):
        fsrs.review(fsrs.Memory(), 7)


def test_buttons_preview_what_each_answer_means():
    shown = fsrs.preview(fsrs.Memory(), NOW)
    assert shown[1] == "10 min" and shown[3].endswith("days") and set(shown) == {1, 2, 3, 4}


# ---- cards from notes

LECTURE = [
    {"heading": "OAuth", "kind": "definition", "term": "OAuth", "text": "A way to let an app act for you without your password.", "cites": ["[02:10]"]},
    {"heading": "OAuth", "kind": "stressed", "term": None, "text": "Tokens expire; on the exam.", "cites": ["[04:00]"]},
    {"heading": "OAuth", "kind": "point", "term": None, "text": "A side remark.", "cites": ["[05:00]"]},
]


def add_lecture(store, rid="a", title="Security week", items=LECTURE):
    store.create_recording(rid, title, store.get_or_create_subject("Machine Learning"), "en", f"{rid}.m4a")
    store.update_recording(rid, recorded_at=(NOW - timedelta(days=1)).isoformat(timespec="seconds"), duration_sec=1800.0)
    store.queue_notes(rid)
    store.save_notes(rid, notes_from_writer({"lecture": items}), writer="w", checker=None, check_detail=None)


def test_cards_come_only_from_definitions_and_stressed_points_and_never_repeat(store):
    add_lecture(store)
    assert learn.sync_cards(store, TZ) == {"added": 2, "removed": 0}
    assert learn.sync_cards(store, TZ) == {"added": 0, "removed": 0}
    cards = {c["kind"]: c for c in store.due_cards(NOW, 10)}
    assert cards["definition"]["front"] == "What is OAuth?" and cards["definition"]["start"] == 130.0
    assert cards["stressed"]["front"].startswith("OAuth:") and cards["stressed"]["recording"] == "Security week"


def test_an_unanswered_card_follows_its_note_but_an_answered_one_stays(store):
    add_lecture(store)
    learn.sync_cards(store, TZ)
    answered = learn.next_card(store, TZ, NOW)
    learn.answer(store, answered["id"], fsrs.GOOD, NOW)
    reworded = [dict(LECTURE[0], text="Delegated access with tokens."), LECTURE[1], LECTURE[2]]
    store.save_notes("a", notes_from_writer({"lecture": reworded}), writer="w", checker=None, check_detail=None)
    result = learn.sync_cards(store, TZ)
    assert result == {"added": 1, "removed": 0}  # the new wording is a new card; the answered one stays
    assert store.get_card(answered["id"])["reps"] == 1  # what you answered is never thrown away


def test_the_review_flow_respects_the_daily_limit_for_new_cards(store):
    items = [{"heading": "T", "kind": "definition", "term": f"Term {n}", "text": f"Meaning {n}.", "cites": ["[01:00]"]} for n in range(5)]
    add_lecture(store, items=items)
    learn.sync_cards(store, TZ)
    assert learn.summary(store, TZ, NOW, new_per_day=2)["to_do"] == 2
    first = learn.next_card(store, TZ, NOW, new_per_day=2)
    learn.answer(store, first["id"], fsrs.GOOD, NOW)
    second = learn.next_card(store, TZ, NOW, new_per_day=2)
    learn.answer(store, second["id"], fsrs.AGAIN, NOW)
    assert learn.next_card(store, TZ, NOW + timedelta(minutes=1), new_per_day=2) is None  # today's two new cards are done
    back = learn.next_card(store, TZ, NOW + timedelta(minutes=11), new_per_day=2)
    assert back["id"] == second["id"]  # the one you forgot comes back after ten minutes


def test_suspended_and_binned_cards_are_not_asked(store):
    add_lecture(store)
    learn.sync_cards(store, TZ)
    card = learn.next_card(store, TZ, NOW)
    store.set_card_suspended(card["id"], True)
    assert card["id"] not in [c["id"] for c in store.due_cards(NOW, 10)]
    store.soft_delete("a")
    assert store.due_cards(NOW, 10) == [] and store.card_counts(NOW)["total"] == 0


# ---- through the app, and teach-back

def test_the_api_runs_a_review_and_teach_back(tmp_path):
    writer = FakeLLM("groq:x", [{"right": ["It lets apps act for you"], "missing": ["Tokens expire"], "wrong": [],
                                 "feedback": "Good start: add that tokens expire."}])
    app = build(tmp_path, FakeTranscriber(), writer, None)
    seed(app.state.store)
    with TestClient(app) as client:
        assert client.get("/api/learn").json()["total"] >= 0  # making the cards is part of looking
        store = app.state.store
        add_lecture(store, "z", "Zed", [LECTURE[0], LECTURE[1]])
        summary = client.get("/api/learn").json()
        assert summary["new"] >= 2 and summary["to_do"] >= 1
        card = client.get("/api/learn/next").json()["card"]
        assert card["front"] and card["preview"]["1"] == "10 min"
        reviewed = client.post(f"/api/learn/cards/{card['id']}/review", json={"rating": 3}).json()
        assert reviewed["due"] > datetime.now(UTC).isoformat(timespec="seconds")
        assert client.post(f"/api/learn/cards/{card['id']}/review", json={"rating": 9}).status_code == 400
        assert client.post("/api/learn/cards/99999/review", json={"rating": 3}).status_code == 404
        brain = client.get("/api/brain").json()
        oauth = next(c for c in brain["concepts"] if c["name"] == "OAuth")
        checked = client.post("/api/learn/teach-back", json={"concept_id": oauth["id"],
                                                             "explanation": "It lets an app act for you."}).json()
        assert checked["right"] == ["It lets apps act for you"] and "tokens expire" in checked["feedback"].lower()
        sent = writer.calls[-1]["user"]
        assert "OAuth tokens expire" in sent and "It lets an app act for you." in sent  # the notes, then the student's words
        assert client.post("/api/learn/teach-back", json={"concept_id": oauth["id"], "explanation": "  "}).status_code == 400
        assert client.post("/api/learn/teach-back", json={"concept_id": 999, "explanation": "x"}).status_code == 404


def test_teach_back_only_uses_notes_the_model_may_see(tmp_path):
    from transcriber.app import create_app
    from transcriber.config import Config

    writer = FakeLLM("groq:openai/gpt-oss-120b", [{"right": [], "missing": [], "wrong": [], "feedback": "ok"}])
    app = create_app(Config(groq_api_key=None, data_dir=tmp_path / "data", port=0), transcriber=None, writer=writer,
                     checker=None, ask_engine=None, voice=None, practice_llm=None, start_worker=False)
    seed(app.state.store)
    app.state.store.set_privacy("b", "private")
    with TestClient(app) as client:
        client.put("/api/privacy/providers", json={"config_id": "groq:openai/gpt-oss-120b", "standard": True})
        oauth = next(c for c in client.get("/api/brain").json()["concepts"] if c["name"] == "OAuth")
        blocked = client.post("/api/learn/teach-back", json={"concept_id": oauth["id"], "explanation": "Tokens."})
        assert blocked.status_code == 403 and writer.calls == []  # one of its notes is Private: nothing was sent
