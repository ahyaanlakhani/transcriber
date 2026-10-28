"""Projects: links between recordings, people, concepts and documents, tasks, and what a project gathers from its notes."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from transcriber.notes.model import notes_from_writer
from transcriber.projects import project_detail
from transcriber.store import Store

from .helpers import FakeTranscriber
from .test_app import build
from .test_app_brain import seed

TZ = "Asia/Tashkent"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


def add_meeting(store, rid="m1", title="Kickoff"):
    store.create_recording(rid, title, None, "en", f"{rid}.m4a")
    store.update_recording(rid, recorded_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds"), duration_sec=1800.0)
    store.queue_notes(rid)
    store.save_notes(rid, notes_from_writer({
        "decisions": [{"text": "Use Postgres.", "cites": ["[03:00]"]}],
        "action_items": [{"text": "Draft the schema.", "owner": "me", "due": "Friday", "cites": ["[04:00]"]}]}),
        writer="w", checker=None, check_detail=None)


def test_a_project_ties_things_together_and_gathers_what_matters(store):
    add_meeting(store)
    person = store.get_or_create_person("Dana")
    project = store.create_project("  FYP   proposal ", "The final year project")
    store.add_project_link(project, "recording", "m1")
    store.add_project_link(project, "person", str(person))
    store.add_project_link(project, "document", "C:/docs/proposal.docx", "Proposal draft")
    assert store.add_project_link(project, "recording", "m1") is False  # linked once
    store.add_project_task(project, "Email the supervisor")
    detail = project_detail(store, project, TZ)
    assert detail["name"] == "FYP proposal" and [r["title"] for r in detail["recordings"]] == ["Kickoff"]
    assert detail["people"][0]["name"] == "Dana" and detail["documents"] == [{"ref": "C:/docs/proposal.docx", "label": "Proposal draft"}]
    assert [d["text"] for d in detail["decisions"]] == ["Use Postgres."]  # decisions from the linked recordings
    assert [i["text"] for i in detail["follow_ups"]] == ["Draft the schema."]  # and what is still open
    assert [t["text"] for t in detail["tasks"]] == ["Email the supervisor"]
    assert store.projects_of("recording", "m1") == [{"id": project, "name": "FYP proposal"}]


def test_binned_or_erased_recordings_do_not_feed_the_project(store):
    add_meeting(store)
    project = store.create_project("P")
    store.add_project_link(project, "recording", "m1")
    store.add_project_link(project, "recording", "ghost")
    store.soft_delete("m1")
    detail = project_detail(store, project, TZ)
    assert detail["decisions"] == [] and detail["follow_ups"] == []
    assert {r["ref"]: (r["in_bin"] if not r["missing"] else "gone") for r in detail["recordings"]} == {"m1": True, "ghost": "gone"}


def test_deleting_a_project_never_touches_a_recording(store):
    add_meeting(store)
    project = store.create_project("P")
    store.add_project_link(project, "recording", "m1")
    store.add_project_task(project, "x")
    assert store.delete_project(project) is True
    assert store.get_recording("m1") is not None and store.project_links(project) == [] and store.project_tasks(project) == []


def test_bad_input_is_refused(store):
    with pytest.raises(ValueError):
        store.create_project("   ")
    project = store.create_project("P")
    with pytest.raises(ValueError):
        store.add_project_link(project, "spaceship", "x")
    with pytest.raises(ValueError):
        store.add_project_link(project, "person", "  ")
    with pytest.raises(ValueError):
        store.update_project(project, status="finished")
    with pytest.raises(ValueError):
        store.add_project_task(project, " ")


def test_the_api(tmp_path):
    app = build(tmp_path, FakeTranscriber(), None, None)
    seed(app.state.store)
    with TestClient(app) as client:
        made = client.post("/api/projects", json={"name": "Security paper", "description": "OAuth"}).json()
        pid = made["id"]
        assert client.post("/api/projects", json={"name": " "}).status_code == 400
        assert client.post(f"/api/projects/{pid}/links", json={"kind": "recording", "ref": "a"}).json()["recordings"][0]["title"] == "Agents week"
        concept = next(c for c in client.get("/api/brain").json()["concepts"] if c["name"] == "OAuth")
        linked = client.post(f"/api/projects/{pid}/links", json={"kind": "concept", "ref": str(concept["id"])}).json()
        assert linked["concepts"][0]["name"] == "OAuth"
        assert client.post(f"/api/projects/{pid}/links", json={"kind": "nope", "ref": "x"}).status_code == 400
        assert client.get("/api/recordings/a").json()["projects"] == [{"id": pid, "name": "Security paper"}]
        with_task = client.post(f"/api/projects/{pid}/tasks", json={"text": "Read the RFC"}).json()
        tid = with_task["tasks"][0]["id"]
        assert client.patch(f"/api/project-tasks/{tid}", json={"done": True}).json() == {"id": tid, "done": True}
        assert client.patch(f"/api/projects/{pid}", json={"status": "done"}).json()["status"] == "done"
        assert client.get("/api/projects").json()["projects"][0]["links"] == 2
        unlinked = client.delete(f"/api/projects/{pid}/links", params={"kind": "recording", "ref": "a"}).json()
        assert unlinked["recordings"] == []
        assert client.delete(f"/api/projects/{pid}").status_code == 204
        assert client.get(f"/api/projects/{pid}").status_code == 404
        assert client.get("/api/recordings/a").status_code == 200  # the recording is untouched
