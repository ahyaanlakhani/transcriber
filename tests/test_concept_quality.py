"""Concept quality: generic headings and course admin stay indexed but out of the list, the map and related concepts,
until you choose to show them."""
import pytest
from fastapi.testclient import TestClient

from transcriber.brain.graph import graph
from transcriber.brain.index import Brain
from transcriber.brain.quality import ADMIN, CONCEPT, GENERIC, classify, hidden
from transcriber.notes.model import notes_from_writer
from transcriber.providers.base import Segment
from transcriber.store import Store

from .helpers import FakeTranscriber
from .test_app import build


@pytest.mark.parametrize("name, kind", [
    ("Process", GENERIC), ("Step", GENERIC), ("Choice", GENERIC), ("Technology", GENERIC), ("Structure", GENERIC),
    ("Assignments", ADMIN), ("Midterm exam", ADMIN), ("Exam logistics", ADMIN), ("Course expectations", ADMIN),
    ("Group activity", ADMIN), ("Business model canvas", CONCEPT), ("Artificial Intelligence", CONCEPT),
    ("Process automation", CONCEPT), ("Assignment problem", CONCEPT), ("Grade inflation", CONCEPT),
    ("Quiz show", CONCEPT), ("Logistics", CONCEPT), ("IoT", CONCEPT), ("Training", CONCEPT),
])
def test_rules_on_names_from_real_notes(name, kind):
    assert classify(name) == kind


def test_your_choice_beats_the_rules():
    assert hidden("Process", None) is True and hidden("Process", "shown") is False
    assert hidden("Business model canvas", "hidden") is True and hidden("Business model canvas", None) is False


def lecture(store, rid, title, headings):
    store.create_recording(rid, title, store.get_or_create_subject("Operations"), "en", f"{rid}.m4a")
    store.save_transcript(rid, "words", [Segment(0, 5, "Words.")], "groq")
    store.update_recording(rid, recorded_at="2026-10-07T05:00:00+00:00", duration_sec=1800.0)
    store.queue_notes(rid)
    items = [{"heading": h, "kind": "point", "term": None, "text": f"{h} matters here.", "cites": ["[01:00]"]} for h in headings]
    store.save_notes(rid, notes_from_writer({"lecture": items}), writer="w", checker=None, check_detail=None)


@pytest.fixture
def brain(tmp_path):
    store = Store(tmp_path / "app.db")
    lecture(store, "a", "Week 1", ["Process", "Assignments", "Business model canvas"])
    lecture(store, "b", "Week 2", ["Process", "Business model canvas", "Value chain"])
    b = Brain(store)
    b.catch_up()
    yield b
    store.close()


def test_the_list_map_and_related_leave_out_generic_and_admin(brain):
    names = [c["name"] for c in brain.list_concepts()]
    assert "Business model canvas" in names and "Process" not in names and "Assignments" not in names
    every = {c["name"]: c for c in brain.list_concepts(include_hidden=True)}
    assert every["Process"]["hidden"] and every["Process"]["quality"] == GENERIC and every["Assignments"]["quality"] == ADMIN
    assert [c["name"] for c in brain.list_concepts(q="process")] == ["Process"]  # a search still finds it
    canvas = every["Business model canvas"]["id"]
    assert "Process" not in [r["name"] for r in brain.related(canvas)]
    assert "Process" not in [n["label"] for n in graph(brain)["nodes"]]


def test_showing_or_hiding_by_hand(brain):
    every = {c["name"]: c["id"] for c in brain.list_concepts(include_hidden=True)}
    brain.set_visibility(every["Process"], "shown")
    brain.set_visibility(every["Value chain"], "hidden")
    names = [c["name"] for c in brain.list_concepts()]
    assert "Process" in names and "Value chain" not in names
    brain.set_visibility(every["Process"], None)  # back to the rules
    assert "Process" not in [c["name"] for c in brain.list_concepts()]
    with pytest.raises(ValueError):
        brain.set_visibility(every["Process"], "maybe")


def test_the_api(tmp_path):
    app = build(tmp_path, FakeTranscriber(), None, None)
    lecture(app.state.store, "a", "Week 1", ["Process", "Business model canvas"])
    with TestClient(app) as client:
        overview = client.get("/api/brain").json()
        assert [c["name"] for c in overview["concepts"]] == ["Business model canvas"] and overview["hidden_count"] == 1
        everything = client.get("/api/brain", params={"hidden": True}).json()["concepts"]
        process = next(c for c in everything if c["name"] == "Process")
        page = client.patch(f"/api/brain/concepts/{process['id']}", json={"visibility": "shown"}).json()
        assert page["hidden"] is False and page["visibility"] == "shown" and page["quality"] == GENERIC
        assert len(client.get("/api/brain").json()["concepts"]) == 2
        assert client.patch(f"/api/brain/concepts/{process['id']}", json={"visibility": "auto"}).json()["hidden"] is True
        assert client.patch(f"/api/brain/concepts/{process['id']}", json={"visibility": "nope"}).status_code == 400
