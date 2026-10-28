"""Duplicate recordings: exact copies (same bytes) and possible duplicates (same start time and length)."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from transcriber.duplicates import describe, find_duplicates

from .helpers import FakeLLM, FakeTranscriber, synth_audio
from .test_app import build


def rec(id, title="Lecture", content_hash=None, recorded_at=None, duration_sec=None):
    return SimpleNamespace(id=id, title=title, content_hash=content_hash, recorded_at=recorded_at,
                           duration_sec=duration_sec)


def test_same_bytes_is_an_exact_copy_even_without_a_time():
    twins = find_duplicates([rec("a", content_hash="h1"), rec("b", content_hash="h1"), rec("c", content_hash="h2")])
    assert twins == {"a": {"kind": "exact", "of": [{"id": "b", "title": "Lecture"}]},
                     "b": {"kind": "exact", "of": [{"id": "a", "title": "Lecture"}]}}


def test_same_start_and_length_is_a_possible_duplicate():
    twins = find_duplicates([
        rec("a", recorded_at="2026-10-05T05:05:00+00:00", duration_sec=605),
        rec("b", recorded_at="2026-10-05T05:06:00+00:00", duration_sec=610),  # a minute later, 5 s longer
    ])
    assert twins["a"]["kind"] == "possible" and twins["b"]["of"][0]["id"] == "a"


def test_different_start_or_length_is_not_a_duplicate():
    assert find_duplicates([rec("a", recorded_at="2026-10-05T05:00:00+00:00", duration_sec=605),
                            rec("b", recorded_at="2026-10-05T05:10:00+00:00", duration_sec=605)]) == {}
    assert find_duplicates([rec("a", recorded_at="2026-10-05T05:00:00+00:00", duration_sec=605),
                            rec("b", recorded_at="2026-10-05T05:00:00+00:00", duration_sec=640)]) == {}


def test_an_exact_copy_wins_over_a_possible_one_and_an_empty_hash_is_ignored():
    twins = find_duplicates([
        rec("a", content_hash="h", recorded_at="2026-10-05T05:00:00+00:00", duration_sec=600),
        rec("b", content_hash="h", recorded_at="2026-10-05T05:00:00+00:00", duration_sec=600),
        rec("c", content_hash="", recorded_at="2026-10-05T05:00:00+00:00", duration_sec=600),
    ])
    # a and b are byte-identical; c has no hash, so it only matches them by start and length
    assert twins["a"]["kind"] == "exact" and {o["id"] for o in twins["a"]["of"]} == {"b", "c"}
    assert twins["c"]["kind"] == "possible" and {o["id"] for o in twins["c"]["of"]} == {"a", "b"}


def test_describe_names_the_twin():
    assert describe({"kind": "exact", "of": [{"id": "b", "title": "Lecture 3"}]}) == "Exact copy of “Lecture 3”"
    assert describe({"kind": "possible", "of": [{"id": "b", "title": "A"}, {"id": "c", "title": "B"}]}) == \
        "Possible duplicate of “A”, “B”"


def test_uploading_the_same_file_twice_flags_both_in_the_list(tmp_path):
    wav = synth_audio(tmp_path / "lecture.wav", [("tone", 3.0)]).read_bytes()
    app = build(tmp_path, FakeTranscriber(), FakeLLM("groq:x", []), None)
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post("/api/recordings", files=[("files", ("lecture.wav", wav, "audio/wav"))], data={})
            assert response.status_code == 201
        rows = client.get("/api/recordings").json()
        assert len(rows) == 2
        assert all(row["duplicate"]["kind"] == "exact" for row in rows)
        first = rows[0]
        assert client.get(f"/api/recordings/{first['id']}").json()["duplicate"]["of"][0]["id"] == rows[1]["id"]


def test_a_possible_duplicate_is_flagged_and_the_bell_says_so(tmp_path):
    app = build(tmp_path, FakeTranscriber(), FakeLLM("groq:x", []), None)
    store = app.state.store
    for rid, start in (("one", "2026-10-05T05:05:00+00:00"), ("two", "2026-10-05T05:06:30+00:00")):
        store.create_recording(rid, "Lecture", None, "en", "x.m4a")
        store.update_recording(rid, recorded_at=start, duration_sec=605)
    with TestClient(app) as client:
        rows = {row["id"]: row for row in client.get("/api/recordings").json()}
        assert rows["one"]["duplicate"]["kind"] == "possible"
        store.notify("duplicate", "two", describe(rows["two"]["duplicate"]))
        note = client.get("/api/notifications").json()["items"][0]
        assert note["kind"] == "duplicate" and note["detail"].startswith("Possible duplicate of")
