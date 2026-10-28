"""Lecture or meeting notes, chosen per subject and per recording, through the API."""
from transcriber.notes.prompts import LECTURE_SYSTEM, WRITER_SYSTEM

from .test_app import detail, drain, env, upload, wav  # noqa: F401  (fixtures)


def test_notes_are_lecture_notes_by_default(env, wav):
    rec = upload(env, wav, subject="Machine Learning")
    drain(env)
    assert env.writer.calls[0]["system"] == LECTURE_SYSTEM
    body = detail(env, rec["id"])
    assert (body["notes_style"], body["effective_notes_style"]) == (None, "lecture")
    assert "lecture" in body["notes"]["content"]


def test_a_meeting_subject_gets_meeting_notes(env, wav):
    rec = upload(env, wav, subject="Debate Society")
    subject_id = detail(env, rec["id"])["subject_id"]
    response = env.client.patch(f"/api/subjects/{subject_id}", json={"notes_style": "meeting"})
    assert response.status_code == 200 and response.json()["notes_style"] == "meeting"
    drain(env)
    assert env.writer.calls[0]["system"] == WRITER_SYSTEM
    assert detail(env, rec["id"])["effective_notes_style"] == "meeting"


def test_a_recording_can_choose_its_own_style_and_go_back(env, wav):
    rec = upload(env, wav)
    url = f"/api/recordings/{rec['id']}"
    assert env.client.patch(url, json={"notes_style": "meeting"}).json()["notes_style"] == "meeting"
    assert detail(env, rec["id"])["effective_notes_style"] == "meeting"
    assert env.client.patch(url, json={"notes_style": ""}).json()["notes_style"] is None  # follow the subject
    assert detail(env, rec["id"])["effective_notes_style"] == "lecture"


def test_unknown_style_is_refused(env, wav):
    rec = upload(env, wav)
    assert env.client.patch(f"/api/recordings/{rec['id']}", json={"notes_style": "poem"}).status_code == 400


def test_conversation_style_shows_your_numbers(env, wav):
    from transcriber.notes.coach import ME, THEM
    from transcriber.providers.base import Segment
    rec = upload(env, wav)
    drain(env)
    assert env.client.patch(f"/api/recordings/{rec['id']}", json={"notes_style": "conversation"}).status_code == 200
    env.store.save_transcript(rec["id"], "chat", [Segment(0, 4, "How did you start?", speaker=ME),
                                                  Segment(4, 20, "As an engineer.", speaker=THEM)], "groq")
    body = detail(env, rec["id"])
    assert body["effective_notes_style"] == "conversation"
    assert body["conversation_stats"]["my_questions"] == 1 and body["conversation_stats"]["my_share"] == 0.2


def test_swap_speakers_flips_you_and_them_and_rewrites(env, wav):
    from transcriber.notes.coach import ME, THEM
    from transcriber.providers.base import Segment
    rec = upload(env, wav)
    drain(env)
    env.store.save_transcript(rec["id"], "chat", [Segment(0, 4, "How did you start?", speaker=THEM),
                                                  Segment(4, 20, "As an engineer.", speaker=ME)], "groq")
    env.store.set_speaker_names(rec["id"], {ME: "You", THEM: "Sara"})
    response = env.client.post(f"/api/recordings/{rec['id']}/speakers/swap")
    assert response.status_code == 200
    assert [s.speaker for s in env.store.get_transcript(rec["id"]).segments] == [ME, THEM]
    assert env.store.get_notes(rec["id"]).status == "queued"
