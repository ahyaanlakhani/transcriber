"""Pipeline with the user's own server: used when preferred or when speakers are wanted; Groq is the fallback."""
import json
import uuid

import pytest

from transcriber.pipeline import WORK_DIR, Pipeline
from transcriber.providers.base import PermanentError, TransientError
from transcriber.store import Store

from .helpers import FakeClock, FakeTranscriber, synth_audio

SHORT = [("tone", 6.0)]
TWO_SPEAKERS = [
    {"start": 0.0, "end": 2.0, "text": "Welcome back.", "speaker": "SPEAKER_00", "avg_logprob": -0.1,
     "no_speech_prob": 0.01, "compression_ratio": 1.1},
    {"start": 2.5, "end": 4.0, "text": "Thanks.", "speaker": "SPEAKER_01"},
]


class FakeRemote:
    """Scripted server. `polls` is a list of job dicts or exceptions, consumed one per poll."""

    def __init__(self, *, health=None, polls=None, submit_error=None, speakers=True):
        self._health = health if health is not None else {"ok": True, "model": "large-v3", "speakers": speakers}
        self.polls = list(polls if polls is not None else [{"status": "done", "segments": TWO_SPEAKERS}])
        self.submit_error = submit_error
        self.submitted: list[dict] = []
        self.deleted: list[str] = []
        self.name = "server/large-v3"
        self.last_error = None

    def health(self):
        return self._health or None

    def submit(self, path, *, language, prompt, speakers):
        if self.submit_error:
            raise self.submit_error
        self.submitted.append({"path": path, "language": language, "speakers": speakers})
        return f"job{len(self.submitted)}"

    def poll(self, job_id):
        item = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def delete(self, job_id):
        self.deleted.append(job_id)


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "app.db")
    yield type("Env", (), {"store": store, "dir": tmp_path / "recordings"})
    store.close()


def add_recording(env, want_speakers=False):
    rid = uuid.uuid4().hex
    rec_dir = env.dir / rid
    rec_dir.mkdir(parents=True)
    synth_audio(rec_dir / "upload.wav", SHORT)
    env.store.create_recording(rid, "Lecture", None, "en", "lecture.wav", want_speakers=want_speakers)
    return rid


def make(env, remote, transcriber=None, clock=None):
    clock = clock or FakeClock()
    return Pipeline(env.store, transcriber, env.dir, remote=remote, sleep=clock.sleep, clock=clock.time,
                    poll_sec=3.0, lost_after_sec=120.0)


def prefer_server(env):
    env.store.update_settings({"prefer_server": "1"})


def test_speakers_wanted_uses_server_and_keeps_labels(env):
    rid = add_recording(env, want_speakers=True)
    remote = FakeRemote(polls=[{"status": "running", "progress": 0.5, "stage": "transcribing"},
                               {"status": "done", "segments": TWO_SPEAKERS}])
    groq = FakeTranscriber()
    make(env, remote, groq).process(rid)
    transcript = env.store.get_transcript(rid)
    assert env.store.get_recording(rid).status == "done"
    assert transcript.model == "server/large-v3"
    assert [s.speaker for s in transcript.segments] == ["SPEAKER_00", "SPEAKER_01"]
    assert remote.submitted[0]["speakers"] is True and remote.submitted[0]["language"] == "en"
    assert remote.deleted == ["job1"] and groq.calls == []
    assert not (env.dir / rid / WORK_DIR).exists()


def test_prefer_server_without_speakers_asks_for_none(env):
    prefer_server(env)
    rid = add_recording(env)
    remote = FakeRemote()
    make(env, remote, FakeTranscriber()).process(rid)
    assert remote.submitted[0]["speakers"] is False


def test_speakers_not_requested_when_server_lacks_them(env):
    rid = add_recording(env, want_speakers=True)
    remote = FakeRemote(speakers=False)
    make(env, remote, FakeTranscriber()).process(rid)
    assert remote.submitted[0]["speakers"] is False


def test_server_not_preferred_and_no_speakers_uses_groq(env):
    rid = add_recording(env)
    remote = FakeRemote()
    groq = FakeTranscriber()
    make(env, remote, groq).process(rid)
    assert remote.submitted == [] and len(groq.calls) == 1
    assert env.store.get_transcript(rid).model == "fake/model"


@pytest.mark.parametrize("remote", [
    FakeRemote(health=False),
    FakeRemote(submit_error=TransientError("refused")),
    FakeRemote(polls=[{"status": "failed", "error": "CUDA out of memory"}]),
    FakeRemote(polls=[PermanentError("token rejected")]),
])
def test_falls_back_to_groq(env, remote):
    rid = add_recording(env, want_speakers=True)
    groq = FakeTranscriber()
    make(env, remote, groq).process(rid)
    assert env.store.get_recording(rid).status == "done"
    assert env.store.get_transcript(rid).model == "fake/model" and len(groq.calls) == 1


def test_lost_server_falls_back_after_window(env):
    rid = add_recording(env, want_speakers=True)
    remote = FakeRemote(polls=[TransientError("gone")])
    clock = FakeClock()
    groq = FakeTranscriber()
    make(env, remote, groq, clock).process(rid)
    assert len(groq.calls) == 1
    assert 120.0 <= clock.now <= 130.0


def test_brief_outage_is_tolerated(env):
    rid = add_recording(env, want_speakers=True)
    remote = FakeRemote(polls=[TransientError("blip"), {"status": "done", "segments": TWO_SPEAKERS}])
    groq = FakeTranscriber()
    make(env, remote, groq).process(rid)
    assert groq.calls == [] and env.store.get_transcript(rid).model == "server/large-v3"


def test_status_shows_server_progress(env):
    rid = add_recording(env, want_speakers=True)
    seen = []
    original = env.store.update_recording

    def spy(r, **fields):
        if "status_detail" in fields:
            seen.append(fields["status_detail"])
        original(r, **fields)

    env.store.update_recording = spy
    remote = FakeRemote(polls=[{"status": "queued"}, {"status": "running", "progress": 0.42, "stage": "speakers"},
                               {"status": "done", "segments": TWO_SPEAKERS}])
    make(env, remote, FakeTranscriber()).process(rid)
    assert "Waiting on your server" in seen
    assert "On your server: speakers 42%" in seen


def test_resumes_stored_job_without_resubmitting(env):
    rid = add_recording(env, want_speakers=True)
    work = env.dir / rid / WORK_DIR
    work.mkdir(parents=True)
    (work / "remote_job.json").write_text(json.dumps({"id": "job-from-before"}), encoding="utf-8")
    remote = FakeRemote()
    make(env, remote, FakeTranscriber()).process(rid)
    assert remote.submitted == [] and remote.deleted == ["job-from-before"]


def test_no_groq_and_no_server_fails_clearly(env):
    rid = add_recording(env, want_speakers=True)
    make(env, FakeRemote(health=False), None).process(rid)
    rec = env.store.get_recording(rid)
    assert rec.status == "failed" and "server" in rec.status_detail


def test_groq_usage_recorded_per_call(env):
    rid = add_recording(env)
    make(env, None, FakeTranscriber()).process(rid)
    used = env.store.usage_seconds("fake", since="2000-01-01T00:00:00+00:00")
    assert 5.5 <= used <= 6.5
