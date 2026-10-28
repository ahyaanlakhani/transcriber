"""Second brain: concepts and backlinks from the notes, name merging, Ask across everything, the graph and the
Obsidian export. Everything here runs without the network."""
import json

import pytest

from transcriber.brain import Brain
from transcriber.brain.ask import ask_brain, clean_citations, search, terms
from transcriber.brain.graph import graph
from transcriber.brain.names import key_of, mention_pattern, split_alias
from transcriber.brain.vault import MANIFEST, export_vault
from transcriber.notes.model import notes_from_writer
from transcriber.providers.base import RateLimited, Segment
from transcriber.store import Store

from .helpers import FakeLLM


def lecture(*items, summary=()):
    return notes_from_writer({"summary": [{"text": t, "cites": []} for t in summary],
                              "lecture": [{"heading": h, "kind": k, "term": term, "text": text, "cites": [c]}
                                          for h, k, term, text, c in items]})


def add(store, rid, title, notes=None, *, subject=None, when="2026-10-01T05:00:00+00:00", words="Hello class.",
        person=None, my_notes=""):
    store.create_recording(rid, title, store.get_or_create_subject(subject) if subject else None, "en", f"{rid}.m4a")
    store.save_transcript(rid, words, [Segment(0, 5, words), Segment(30, 35, "And that is the end.")], "groq")
    store.update_recording(rid, recorded_at=when, duration_sec=2700.0)
    if person:
        store.update_recording(rid, person_id=store.get_or_create_person(person))
    if notes is not None:
        store.queue_notes(rid)
        store.save_notes(rid, notes, writer="w", checker=None, check_detail=None)
    if my_notes:
        store.set_my_notes(rid, my_notes)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


@pytest.fixture
def library(store):
    add(store, "a", "Agents week", lecture(
        ("AI agents", "definition", "AI agent", "Software that plans and acts on its own toward a goal.", "[02:10]"),
        ("AI agents", "stressed", None, "Agents will be on the midterm.", "[05:00]"),
        ("Retrieval augmented generation (RAG)", "point", None, "RAG grounds the model in your documents.", "[12:30]"),
    ), subject="Machine Learning", when="2026-10-07T05:00:00+00:00",
        words="Today we talk about AI agents and how they use tools.")
    add(store, "b", "Canvas week", lecture(
        ("Business model canvas", "definition", "Business model canvas", "A one-page map of how a business makes money.", "[01:00]"),
        ("Business model canvas", "example", None, "Uber's canvas puts drivers among key partners.", "[08:00]"),
        ("Strategy", "point", None, "AI agents could fill some key activities.", "[20:00]"),
    ), subject="Machine Learning", when="2026-10-01T05:00:00+00:00")
    add(store, "c", "Coffee with Sara", notes_from_writer({"about_them": [
        {"text": "Sara builds RAG systems at a bank.", "cites": ["[03:00]"]}]}), person="Sara Khan",
        when="2026-10-05T05:00:00+00:00", my_notes="Ask Sara about vector databases next time.")
    return store


def concept_named(brain, name):
    cid = brain.find(name)
    assert cid, f"no concept {name}"
    return brain.concept(cid)


# --- names

def test_names_meet_on_one_key():
    assert key_of("AI Agents") == key_of("ai agent") == "ai agent"
    assert key_of("Technologies") == "technology" and key_of("Analysis") == "analysis"
    assert key_of("Retrieval-Augmented Generation") == "retrieval augmented generation"
    assert split_alias("Retrieval augmented generation (RAG)") == ("Retrieval augmented generation", "RAG")
    pattern = mention_pattern(["ai agent", "internet of thing", "technology"])
    assert [m.group(0) for m in pattern.finditer("Our AI-agents talk to the Internet of Things; technologies too")] == [
        "AI-agents", "Internet of Things", "technologies"]
    assert not pattern.search("chair agents")  # whole words only: "ai" inside another word doesn't count


# --- concepts and backlinks

def test_concepts_come_from_terms_headings_and_mentions_link_back(library):
    brain = Brain(library)
    assert brain.catch_up() == 3
    names = {c["name"]: c for c in brain.list_concepts()}
    assert {"AI agent", "AI agents", "Business model canvas", "Retrieval augmented generation", "Strategy"} \
        >= set(names) >= {"Business model canvas", "Retrieval augmented generation", "Strategy"}
    agents = concept_named(brain, "AI agents")
    assert [g["recording_id"] for g in agents["recordings"]] == ["a", "b"]  # newest first
    first = agents["recordings"][0]
    assert first["subject"] == "Machine Learning" and [i["start"] for i in first["items"]] == [130.0, 300.0]
    assert agents["recordings"][1]["items"][0]["text"] == "AI agents could fill some key activities."  # found in the text
    assert agents["classes"] == [{"id": first["subject_id"], "name": "Machine Learning", "count": 2}]
    assert agents["definition"] == "Software that plans and acts on its own toward a goal."  # no model: the notes


def test_an_acronym_lands_on_the_long_name_and_people_show_up(library):
    brain = Brain(library)
    brain.catch_up()
    rag = concept_named(brain, "RAG")
    assert rag["name"] == "Retrieval augmented generation" and "RAG" in rag["aliases"]
    assert [g["recording_id"] for g in rag["recordings"]] == ["a", "c"]  # Sara's chat said "RAG"
    assert rag["people"] == [{"id": library.list_people()[0].id, "name": "Sara Khan"}]
    assert brain.find("retrieval-augmented generation") == brain.find("rag")


def test_related_concepts_come_from_the_same_recordings(library):
    brain = Brain(library)
    brain.catch_up()
    related = [r["name"] for r in concept_named(brain, "Business model canvas")["related"]]
    assert "Strategy" in related and "Retrieval augmented generation" not in related


def test_the_index_follows_changes_and_the_bin(library):
    brain = Brain(library)
    brain.catch_up()
    assert brain.stale() == []
    library.add_topic("b", "Value proposition")  # an accepted topic is a concept too
    assert brain.stale() == ["b"]
    brain.catch_up()
    assert concept_named(brain, "value proposition")["recordings"][0]["items"][0]["text"] == "Tagged #Value proposition"
    library.soft_delete("b")
    brain.catch_up()
    assert brain.find("Business model canvas") is None  # its only recording is in the bin
    assert [g["recording_id"] for g in concept_named(brain, "AI agents")["recordings"]] == ["a"]
    library.restore("b")
    brain.catch_up()
    assert brain.find("Business model canvas")


def test_parts_of_a_combined_class_and_practice_are_left_out(store):
    add(store, "p1", "Part 1", lecture(("Robots", "point", None, "Robots weld cars.", "[00:10]")), subject="Tech",
        when="2026-10-01T05:00:00+00:00")
    add(store, "p2", "Part 2", lecture(("Robots", "point", None, "Robots paint cars.", "[00:10]")), subject="Tech",
        when="2026-10-01T05:46:00+00:00")
    store.update_recording("p1", duration_sec=60.0)
    store.update_recording("p2", duration_sec=60.0)
    store.create_recording("j", "Tech class", store.get_or_create_subject("Tech"), "en", "2 combined: p1.m4a, p2.m4a")
    store.save_transcript("j", "x", [Segment(0, 5, "Robots.")], "groq")
    store.update_recording("j", recorded_at="2026-10-01T05:00:00+00:00", duration_sec=120.0)
    store.queue_notes("j")
    store.save_notes("j", lecture(("Robots", "point", None, "Robots weld and paint cars.", "[00:10]")), writer="w",
                     checker=None, check_detail=None)
    store.create_recording("x", "Practice", None, "en", "Practice session")
    store.save_transcript("x", "x", [Segment(0, 5, "Robots.")], "groq")
    brain = Brain(store)
    brain.catch_up()
    assert set(brain.eligible()) == {"j"}
    assert [g["recording_id"] for g in concept_named(brain, "Robots")["recordings"]] == ["j"]


def test_rebuild_starts_over_and_keeps_merges(library):
    brain = Brain(library)
    brain.catch_up()
    agent, strategy = brain.find("AI agents"), brain.find("Strategy")
    assert brain.merge(strategy, agent)
    assert brain.find("Strategy") == agent  # the old name is an alias now
    assert brain.rebuild() == 3
    assert brain.find("Strategy") == brain.find("AI agents")
    assert "Strategy" in concept_named(brain, "AI agents")["aliases"]


def test_rename_and_renaming_onto_another_merges(library):
    brain = Brain(library)
    brain.catch_up()
    cid = brain.find("Strategy")
    assert brain.rename(cid, "Business strategy") == cid
    assert brain.find("Strategy") == cid and concept_named(brain, "business strategy")["name"] == "Business strategy"
    assert brain.rename(cid, "AI agents") == brain.find("AI agents")
    with pytest.raises(ValueError):
        brain.rename(brain.find("AI agents"), "  ")


# --- the model's merge check

def test_new_names_are_checked_once_against_the_existing_ones(store):
    add(store, "a", "One", lecture(("Large language models", "point", None, "LLMs predict the next word.", "[00:10]")),
        when="2026-10-01T05:00:00+00:00")
    llm = FakeLLM("fake:llm", [{"matches": [{"new": "Foundation models", "same_as": "Large language models"}]}])
    brain = Brain(store, llm)
    while brain.step():
        pass
    assert llm.calls == []  # nothing to compare the first lecture's names with
    add(store, "b", "Two", lecture(("Foundation models", "point", None, "Foundation models are trained once.", "[00:10]")),
        when="2026-10-08T05:00:00+00:00")
    while brain.step():
        pass
    [call] = llm.calls
    assert "Existing names:\n- Large language models" in call["user"] and "New names:\n- Foundation models" in call["user"]
    page = concept_named(brain, "foundation model")
    assert page["name"] == "Large language models" and [g["recording_id"] for g in page["recordings"]] == ["b", "a"]
    assert not brain.step()  # done: no second call


def test_a_rate_limit_waits_instead_of_retrying_at_once(store):
    add(store, "a", "One", lecture(("Cloud", "point", None, "Cloud is rented computers.", "[00:10]")))
    add(store, "b", "Two", lecture(("Edge computing", "point", None, "Compute near the device.", "[00:10]")),
        when="2026-10-09T05:00:00+00:00")
    now = [0.0]
    llm = FakeLLM("fake:llm", [RateLimited(60.0), {"matches": []}])
    brain = Brain(store, llm, clock=lambda: now[0])
    while brain.step():
        pass
    assert len(llm.calls) == 1 and brain.counts()["checking"] == 1
    now[0] = 61.0
    while brain.step():
        pass
    assert len(llm.calls) == 2 and brain.counts()["checking"] == 0


# --- Ask across everything

def test_search_finds_passages_across_recordings(library):
    brain = Brain(library)
    brain.catch_up()
    assert terms("What did we say about the vector databases?") == ["vector", "databases"]
    sources = search(brain, "vector databases")
    assert sources[0].recording_id == "c" and sources[0].source == "mine"
    tools = search(brain, "how do agents use tools")
    assert any(s.source == "transcript" and s.recording_id == "a" and s.start == 0 for s in tools)


def test_ask_answers_from_the_passages_and_only_cites_what_it_got(library):
    brain = Brain(library)
    brain.catch_up()
    llm = FakeLLM("fake:llm", [{"answer": "It grounds the model in your documents [1], and Sara builds it [2][9]."}])
    result = ask_brain(brain, [llm], "what is RAG")
    assert result["engine"] == "fake:llm" and result["answer"].endswith("[2].")  # [9] wasn't retrieved
    assert "[1] " in llm.calls[0]["user"] and "QUESTION: what is RAG" in llm.calls[0]["user"]
    assert {s["recording_id"] for s in result["sources"]} >= {"a", "c"}
    assert clean_citations("a [1] b [3]", 2) == "a [1] b"


def test_ask_without_a_model_or_a_match(library):
    brain = Brain(library)
    brain.catch_up()
    assert ask_brain(brain, [], "RAG")["sources"] and ask_brain(brain, [], "RAG")["engine"] is None
    assert ask_brain(brain, [], "photosynthesis")["sources"] == []


# --- graph

def test_graph_joins_concepts_to_subjects_and_people(library):
    brain = Brain(library)
    brain.catch_up()
    data = graph(brain)
    kinds = {n["type"] for n in data["nodes"]}
    assert kinds == {"concept", "class", "person"}
    rag = next(n for n in data["nodes"] if n["label"] == "Retrieval augmented generation")
    targets = {e["target"] for e in data["edges"] if e["source"] == rag["id"]}
    assert len(targets) == 2 and rag["weight"] == 2
    small = graph(brain, limit=1)
    assert small["shown_concepts"] == 1 and small["total_concepts"] == data["total_concepts"]


# --- Obsidian export

def test_export_writes_a_linked_vault_and_leaves_your_files_alone(library, tmp_path):
    brain = Brain(library)
    vault = tmp_path / "vault"
    (vault / "Concepts").mkdir(parents=True)
    (vault / "Concepts" / "Strategy.md").write_text("my own page", encoding="utf-8")
    (vault / "Daily.md").write_text("mine", encoding="utf-8")
    result = export_vault(brain, vault)
    assert result["recordings"] == 3 and (vault / "Home.md").exists()
    assert (vault / "Concepts" / "Strategy.md").read_text(encoding="utf-8") == "my own page"
    assert (vault / "Concepts" / "Strategy (2).md").exists()
    canvas = (vault / "Concepts" / "Business model canvas.md").read_text(encoding="utf-8")
    assert "[[Classes/Canvas week|Canvas week]]" in canvas and "- [01:00] Business model canvas: A one-page map" in canvas
    rag = (vault / "Concepts" / "Retrieval augmented generation.md").read_text(encoding="utf-8")
    assert 'aliases: ["RAG"]' in rag and "[[People/Sara Khan|Sara Khan]]" in rag
    assert "[[Subjects/Machine Learning|Machine Learning]]" in (vault / "Classes" / "Agents week.md").read_text(encoding="utf-8")

    library.update_recording("b", title="Canvas class")
    export_vault(brain, vault)
    assert not (vault / "Classes" / "Canvas week.md").exists() and (vault / "Classes" / "Canvas class.md").exists()
    assert (vault / "Daily.md").read_text(encoding="utf-8") == "mine"
    assert "Classes/Canvas class.md" in json.loads((vault / MANIFEST).read_text(encoding="utf-8"))["files"]


def test_a_page_you_edited_is_never_overwritten(library, tmp_path):
    brain = Brain(library)
    vault = tmp_path / "vault"
    export_vault(brain, vault)
    mine = vault / "Classes" / "Agents week.md"
    mine.write_text(mine.read_text(encoding="utf-8") + "\nMy own thoughts: ask about tokens.\n", encoding="utf-8")
    kept = mine.read_text(encoding="utf-8")
    result = export_vault(brain, vault)
    assert result["kept_edited"] == ["Classes/Agents week.md"]
    assert mine.read_text(encoding="utf-8") == kept  # exactly as you left it
    fresh = vault / "Classes" / "Agents week (2).md"
    assert fresh.exists() and "My own thoughts" not in fresh.read_text(encoding="utf-8")  # the app's version, beside it
    again = export_vault(brain, vault)  # steady from here on: yours stays yours, the app's copy keeps its name
    assert again["kept_edited"] == [] and mine.read_text(encoding="utf-8") == kept and fresh.exists()
    assert "Classes/Agents week.md" not in json.loads((vault / MANIFEST).read_text(encoding="utf-8"))["files"]
    (vault / "Home.md").unlink()  # a page you simply deleted comes back
    export_vault(brain, vault)
    assert (vault / "Home.md").exists()
