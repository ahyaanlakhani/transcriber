"""Recording the screen with a meeting: the ffmpeg command, restarts, the offset and black fill, joining the pieces,
leftovers, the microphone option, keeping the computer awake, and the API."""
import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from transcriber import screen
from transcriber.awake import ES_CONTINUOUS, ES_DISPLAY_REQUIRED, ES_SYSTEM_REQUIRED, KeepAwake
from transcriber.live import LiveManager
from transcriber.meeting import MeetingCapture, MeetingManager
from transcriber.screen import FRAGMENT_PATTERN, OFFSET_FILE, PID_FILE, SCREEN_FILE, ScreenCapture
from transcriber.store import Store

from .helpers import FakeTranscriber
from .test_meeting import Clock, FakeDevices, make_app, play


def wait_until(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


class FakeProc:
    """Stands in for ffmpeg: writes its first piece when started (unless told not to), ends when it gets "q"."""

    next_pid = 1000

    def __init__(self, folder, index=0, exit_code=None, writes=True):
        self.folder, self.exit_code, self.sent, self.killed = folder, exit_code, b"", False
        self.stdin = self
        FakeProc.next_pid += 1
        self.pid = FakeProc.next_pid
        if writes:
            (folder / (FRAGMENT_PATTERN % index)).write_bytes(b"video")

    def poll(self):
        return self.exit_code

    def write(self, data):
        self.sent += data
        self.exit_code = 0

    def flush(self):
        pass

    def close(self):
        pass

    def wait(self, timeout=None):
        return self.exit_code

    def kill(self):
        self.killed = True
        self.exit_code = -9


def start_number(cmd):
    return int(cmd[cmd.index("-segment_start_number") + 1])


def capture_with(folder, clock=None, behaviours=None, **options):
    """A ScreenCapture whose ffmpeg is a FakeProc. behaviours: for each successive start, a dict of FakeProc options."""
    made = {"cmds": [], "procs": []}
    queue = list(behaviours or [{}])

    def popen(cmd, **kwargs):
        made["cmds"].append(cmd)
        made["kwargs"] = kwargs
        proc = FakeProc(folder, start_number(cmd), **(queue.pop(0) if len(queue) > 1 else queue[0]))
        made["procs"].append(proc)
        made["proc"] = proc
        return proc
    options.setdefault("free_bytes", lambda: 50 * 1024**3)
    options.setdefault("retry_wait", lambda n: 0.0)
    recorder = ScreenCapture(folder, clock=clock or Clock(), popen=popen, ffmpeg="ffmpeg", size=lambda: (1920, 1080), **options)
    return recorder, made


# --- the command

def test_the_command_grabs_the_primary_screen_small_in_crash_safe_pieces(tmp_path):
    cmd = screen.capture_command("ffmpeg", tmp_path, (2560, 1440), start_number=7)
    assert cmd[cmd.index("-f") + 1] == "gdigrab" and cmd[cmd.index("-video_size") + 1] == "2560x1440"
    assert cmd[cmd.index("-framerate") + 1] == "5" and "scale='min(1280,iw)':-2" in cmd[cmd.index("-vf") + 1]
    assert "-an" in cmd  # no sound: the audio is recorded separately
    assert cmd[cmd.index("-segment_time") + 1] == "600" and cmd[-1].endswith(FRAGMENT_PATTERN)
    assert "frag_keyframe" in cmd[cmd.index("-segment_format_options") + 1]  # a cut-off piece is still playable
    assert start_number(cmd) == 7  # a restart carries on numbering after the pieces it already has
    assert "-video_size" not in screen.capture_command("ffmpeg", tmp_path, None)  # size unknown: the whole desktop


# --- recording and the offset

def test_the_offset_is_how_long_after_the_audio_the_first_picture_arrived(tmp_path):
    clock = Clock()
    clock.now = 101.5
    recorder, made = capture_with(tmp_path, clock)
    recorder.start(100.0)
    assert wait_until(lambda: recorder.runs)
    assert recorder.offset == 1.5 and recorder.runs == [{"start_index": 0, "offset": 1.5}]
    data = json.loads((tmp_path / OFFSET_FILE).read_text())
    assert data["offset"] == 1.5 and data["runs"] == recorder.runs
    assert recorder.error is None and recorder.notice is None and recorder.running
    assert (tmp_path / PID_FILE).read_text() == str(made["proc"].pid)
    recorder.stop()
    assert made["proc"].sent == b"q\n" and not made["proc"].killed  # asked to finish, so the last piece is closed
    assert not (tmp_path / PID_FILE).exists()


def test_the_offset_is_the_real_first_picture_not_the_early_guess(tmp_path):
    clock = Clock()
    made = {}

    def slow_start(cmd, **kwargs):  # ffmpeg takes a while to deliver its first picture
        made["proc"] = FakeProc(tmp_path, start_number(cmd), writes=False)
        return made["proc"]
    recorder = ScreenCapture(tmp_path, clock=clock, popen=slow_start, ffmpeg="ffmpeg", size=lambda: None,
                             free_bytes=lambda: 50 * 1024**3)
    clock.now = 0.2
    recorder.start(0.0)
    assert recorder.offset == 0.2  # the guess
    clock.now = 1.4
    (tmp_path / (FRAGMENT_PATTERN % 0)).write_bytes(b"video")  # the first picture arrives
    assert wait_until(lambda: recorder.runs)
    assert recorder.offset == 1.4 and json.loads((tmp_path / OFFSET_FILE).read_text())["offset"] == 1.4
    recorder.stop()


def test_a_recorder_that_will_not_stop_is_killed(tmp_path):
    recorder, made = capture_with(tmp_path)
    recorder.start(0.0)
    proc = made["proc"]

    def stubborn(timeout=None):
        if not proc.killed:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
    proc.wait = stubborn
    proc.write = lambda data: None  # ignores the "q"
    recorder.stop()
    assert proc.killed


def test_if_ffmpeg_cannot_even_start_the_meeting_just_says_so(tmp_path):
    def broken(cmd, **kwargs):
        raise FileNotFoundError("no ffmpeg")
    recorder = ScreenCapture(tmp_path, popen=broken, size=lambda: None)
    recorder.start(0.0)
    assert "couldn't be recorded" in recorder.error and not recorder.running
    recorder.stop()


# --- ffmpeg stopping by itself

def test_a_recorder_that_stops_is_started_again_and_the_restart_is_noted(tmp_path):
    clock = Clock()
    recorder, made = capture_with(tmp_path, clock, behaviours=[{}, {"exit_code": 1, "writes": False}, {}])
    recorder.start(0.0)
    assert wait_until(lambda: len(recorder.runs) == 1)
    clock.now = 30.0
    made["procs"][0].exit_code = 1  # the screen was locked: Windows refused to hand over a picture
    (tmp_path / "screen.log").write_text("gdigrab: Failed to capture image (error 5)\n")
    assert wait_until(lambda: len(made["procs"]) >= 3 and len(recorder.runs) == 2)
    assert start_number(made["cmds"][-1]) == 1  # numbering carries on after piece 0
    assert recorder.runs[1]["start_index"] == 1 and recorder.runs[1]["offset"] == 30.0
    assert recorder.notice is None and recorder.error is None  # recovered, so nothing to report
    recorder.stop()


def test_while_it_is_trying_again_the_notice_says_why(tmp_path):
    recorder, made = capture_with(tmp_path, behaviours=[{}, {"exit_code": 1, "writes": False}], retry_wait=lambda n: 0.05)
    recorder.start(0.0)
    assert wait_until(lambda: recorder.runs)
    (tmp_path / "screen.log").write_text("gdigrab: Failed to capture image (error 5)\n")
    made["procs"][0].exit_code = 1
    assert wait_until(lambda: recorder.notice is not None)
    assert "paused" in recorder.notice and "error 5" in recorder.notice and "meeting is still being recorded" in recorder.notice
    assert recorder.error is None  # not given up
    recorder.stop()


def test_a_recorder_that_never_works_gives_up_after_a_few_tries(tmp_path):
    recorder, made = capture_with(tmp_path, behaviours=[{"exit_code": 1, "writes": False}])
    recorder.start(0.0)
    assert wait_until(lambda: recorder.error is not None)
    assert "couldn't be recorded" in recorder.error and len(made["procs"]) == screen.MAX_STARTUP_FAILURES
    recorder.stop()


def test_a_recorder_that_worked_keeps_trying_through_a_long_lock(tmp_path):
    recorder, made = capture_with(tmp_path, behaviours=[{}, {"exit_code": 1, "writes": False}])
    recorder.start(0.0)
    assert wait_until(lambda: recorder.runs)
    made["procs"][0].exit_code = 1
    assert wait_until(lambda: len(made["procs"]) > screen.MAX_STARTUP_FAILURES + 2)  # well past the limit for a new recorder
    assert recorder.error is None
    recorder.stop()


def test_a_nearly_full_disk_stops_the_video_but_not_the_meeting(tmp_path):
    free = {"bytes": 50 * 1024**3}
    clock = Clock()
    recorder, made = capture_with(tmp_path, clock, free_bytes=lambda: free["bytes"])
    recorder.start(0.0)
    assert wait_until(lambda: recorder.runs)
    assert not recorder.low_disk()
    free["bytes"] = 3 * 1024**3
    assert recorder.low_disk() and recorder.error is None  # only a warning at 3 GB
    free["bytes"] = 500 * 1024**2
    clock.now = 100.0  # time passes, so the next disk check happens
    assert wait_until(lambda: recorder.error is not None)
    assert "disk is almost full" in recorder.error and "audio is still being recorded" in recorder.error
    assert made["proc"].sent == b"q\n"  # stopped properly, so what was recorded is kept
    recorder.stop()


# --- leftovers from an earlier run

def test_a_recorder_the_last_run_left_behind_is_stopped_at_start_up(tmp_path):
    (tmp_path / PID_FILE).write_text("4242")
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        out = '"ffmpeg-win-x86_64-v7.1.exe","4242","Console","1","20,000 K"\n' if cmd[0] == "tasklist" else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    assert screen.stop_stale(tmp_path, run=run) is True
    assert ["taskkill", "/PID", "4242", "/F"] in calls and not (tmp_path / PID_FILE).exists()


def test_only_an_ffmpeg_process_is_ever_stopped(tmp_path):
    (tmp_path / PID_FILE).write_text("4242")
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)  # the number now belongs to some other program
        out = '"notepad.exe","4242","Console","1","20,000 K"\n' if cmd[0] == "tasklist" else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    assert screen.stop_stale(tmp_path, run=run) is False
    assert not any(c[0] == "taskkill" for c in calls) and not (tmp_path / PID_FILE).exists()
    assert screen.stop_stale(tmp_path / "nothing", run=run) is False


def test_tying_to_the_app_never_breaks_the_recording(tmp_path):
    screen.tie_to_app(FakeProc(tmp_path))  # a process with no Windows handle: nothing to do, no error


# --- joining the pieces

PROBES = {}


def fake_run(rec_dir, probes, fail_copy=False, calls=None):
    """A stand-in for subprocess.run: answers "ffmpeg -i" probes from a table and 'makes' the files other commands write."""
    def run(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        if "-i" in cmd and "-f" not in cmd:  # a probe of one piece
            name = cmd[cmd.index("-i") + 1].replace("\\", "/").rsplit("/", 1)[-1]
            seconds = probes.get(name)
            if seconds is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="moov atom not found\nInvalid data found")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=(
                f"  Duration: 00:{int(seconds // 60):02d}:{seconds % 60:05.2f}, start: 0.0\n"
                "  Stream #0:0: Video: h264 (High), yuv420p, 1280x720, 5 fps"))
        if "lavfi" in cmd:  # a black clip
            out = cmd[-1]
            open(out, "wb").write(b"black")
            return subprocess.CompletedProcess(cmd, 0)
        if "concat" in cmd:
            if fail_copy and "copy" in cmd:
                return subprocess.CompletedProcess(cmd, 1)
            (rec_dir / SCREEN_FILE).write_bytes(b"joined")
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 0)
    return run


def piece_files(folder, *indexes):
    for i in indexes:
        (folder / (FRAGMENT_PATTERN % i)).write_bytes(b"v")


def test_probe_reads_the_length_and_size_and_rejects_a_broken_piece(tmp_path):
    run = fake_run(tmp_path, {"screen-000.mp4": 61.5})
    assert screen.probe(tmp_path / "screen-000.mp4", run=run, ffmpeg="ffmpeg") == (61.5, (1280, 720))
    assert screen.probe(tmp_path / "screen-001.mp4", run=run, ffmpeg="ffmpeg") is None


def test_the_timeline_fills_the_time_a_restart_missed_with_black(tmp_path):
    pieces = [tmp_path / f"screen-{i:03d}.mp4" for i in range(3)]
    runs = [{"start_index": 0, "offset": 1.0}, {"start_index": 2, "offset": 100.0}]
    durations = {pieces[0]: 60.0, pieces[1]: 30.0, pieces[2]: 600.0}  # the first run covered 1.0 .. 91.0
    plan = screen.plan_timeline(pieces, runs, 1.0, durations)
    assert plan == [("piece", pieces[0]), ("piece", pieces[1]), ("black", 9.0), ("piece", pieces[2])]
    no_gap = screen.plan_timeline(pieces, [{"start_index": 0, "offset": 1.0}, {"start_index": 2, "offset": 91.2}], 1.0, durations)
    assert [kind for kind, _ in no_gap] == ["piece", "piece", "piece"]  # a few tenths of a second isn't worth a clip


def test_an_unplayable_piece_in_the_middle_is_black_and_one_at_the_end_is_dropped(tmp_path):
    pieces = [tmp_path / f"screen-{i:03d}.mp4" for i in range(3)]
    runs = [{"start_index": 0, "offset": 0.5}]
    middle = screen.plan_timeline(pieces, runs, 0.5, {pieces[0]: 600.0, pieces[1]: None, pieces[2]: 600.0}, segment_sec=600)
    assert middle == [("piece", pieces[0]), ("black", 600.0), ("piece", pieces[2])]
    end = screen.plan_timeline(pieces, runs, 0.5, {pieces[0]: 600.0, pieces[1]: 600.0, pieces[2]: None})
    assert end == [("piece", pieces[0]), ("piece", pieces[1])]


def test_pieces_are_joined_with_black_where_the_video_was_missing(tmp_path):
    live, rec = tmp_path / "live", tmp_path / "rec"
    live.mkdir(), rec.mkdir()
    piece_files(live, 0, 1)
    (live / OFFSET_FILE).write_text(json.dumps({"offset": 2.25, "runs": [
        {"start_index": 0, "offset": 2.25}, {"start_index": 1, "offset": 72.25}]}))
    calls = []
    run = fake_run(rec, {"screen-000.mp4": 60.0, "screen-001.mp4": 30.0}, calls=calls)
    assert screen.join_into(live, rec, run=run, ffmpeg="ffmpeg")
    black = [c for c in calls if "lavfi" in c]
    assert len(black) == 1 and "d=10.00" in black[0][black[0].index("-i") + 1] and "s=1280x720" in black[0][black[0].index("-i") + 1]
    listing = (live / "screen-list.txt").read_text().splitlines()
    assert [line.split("/")[-1].rstrip("'") for line in listing] == ["screen-000.mp4", "screen-black-001.mp4", "screen-001.mp4"]
    assert screen.screen_info(rec) == {"offset": 2.25, "bytes": len(b"joined")}


def test_if_copying_the_pieces_together_fails_they_are_re_encoded_instead(tmp_path):
    live, rec = tmp_path / "live", tmp_path / "rec"
    live.mkdir(), rec.mkdir()
    piece_files(live, 0, 1)
    calls = []
    run = fake_run(rec, {"screen-000.mp4": 60.0, "screen-001.mp4": 60.0}, fail_copy=True, calls=calls)
    assert screen.join_into(live, rec, run=run, ffmpeg="ffmpeg")
    joins = [c for c in calls if "concat" in c]
    assert "copy" in joins[0] and "libx264" in joins[1]
    assert (rec / SCREEN_FILE).exists()


def test_a_single_piece_is_just_kept_and_no_usable_video_leaves_nothing(tmp_path):
    live, rec = tmp_path / "live", tmp_path / "rec"
    live.mkdir(), rec.mkdir()
    assert screen.join_into(live, rec) is False  # no pieces
    (live / "screen-000.mp4").write_bytes(b"only")
    (live / OFFSET_FILE).write_text(json.dumps({"offset": 1.0}))
    run = fake_run(rec, {"screen-000.mp4": 12.0})
    assert screen.join_into(live, rec, run=run, ffmpeg="ffmpeg")
    assert (rec / SCREEN_FILE).read_bytes() == b"only" and screen.screen_info(rec)["offset"] == 1.0
    (rec / SCREEN_FILE).unlink()
    assert screen.join_into(live, rec, run=fake_run(rec, {}), ffmpeg="ffmpeg") is False  # the only piece is broken
    assert not (rec / SCREEN_FILE).exists()


# --- keeping the computer awake

def test_the_computer_is_kept_awake_while_recording_and_released_after():
    calls = []
    awake = KeepAwake(display=True, setter=lambda flags: calls.append(flags) or 1)
    awake.start()
    assert awake.active and calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED]
    awake.stop()
    assert calls[-1] == ES_CONTINUOUS and not awake.active  # back to Windows' own idea of sleep
    quiet = []
    only_system = KeepAwake(display=False, setter=lambda flags: quiet.append(flags) or 1)
    only_system.start()
    only_system.stop()
    assert quiet[0] == ES_CONTINUOUS | ES_SYSTEM_REQUIRED  # the display may switch off when no screen is being recorded


# --- the microphone option

class MicDevices(FakeDevices):
    def __init__(self):
        super().__init__()
        self.opened_with = None

    def open(self, on_you, on_others, mic=True):
        self.opened_with = mic
        return super().open(on_you, on_others)


class FakeAwake:
    def __init__(self):
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def test_a_meeting_can_leave_the_microphone_off_and_keeps_the_computer_awake(tmp_path):
    store = Store(tmp_path / "app.db")
    live = LiveManager(store, FakeTranscriber(), tmp_path / "live", tmp_path / "recordings", start_thread=False)
    awakes = []
    devices, clock = MicDevices(), Clock()

    def awake_factory(display):
        awakes.append((display, FakeAwake()))
        return awakes[-1][1]
    manager = MeetingManager(live, devices, clock=clock, awake_factory=awake_factory)
    sid = manager.start(title="Class", subject_id=None, language="en", mic=False)
    assert devices.opened_with is False and awakes[0][1].started and awakes[0][0] is False
    manager.discard(sid)
    assert awakes[0][1].stopped
    manager.start(title="Call", subject_id=None, language="en")
    assert devices.opened_with is True  # the default is unchanged: your microphone is recorded too
    store.close()


def test_the_api_passes_the_microphone_choice_through(tmp_path):
    devices = MicDevices()
    app = make_app(tmp_path, devices)
    with TestClient(app) as client:
        sid = client.post("/api/meeting/start", json={"title": "Class", "mic": False}).json()["id"]
        assert devices.opened_with is False
        client.delete(f"/api/meeting/{sid}")
        client.post("/api/meeting/start", json={"title": "Call"})
        assert devices.opened_with is True


# --- the meeting and the screen together

def test_the_meeting_starts_and_stops_the_screen_with_the_audio(tmp_path):
    devices, clock = FakeDevices(), Clock()
    store = Store(tmp_path / "app.db")
    live = LiveManager(store, FakeTranscriber(), tmp_path / "live", tmp_path / "recordings", start_thread=False)
    recorders = []

    def screens(folder, clk):
        recorders.append(capture_with(folder, clk)[0])
        return recorders[-1]
    manager = MeetingManager(live, devices, clock=clock, screens=screens, awake_factory=lambda display: FakeAwake())
    sid = manager.start(title="Call", subject_id=None, language="en", screen=True)
    assert recorders[0].running
    shown = manager.poll(sid)["screen"]
    assert shown["error"] is None and shown["notice"] is None and shown["low_disk"] in (True, False)
    manager.discard(sid)
    assert not recorders[0].running

    sid = manager.start(title="Call", subject_id=None, language="en")  # screen off: nothing is recorded
    assert manager.poll(sid)["screen"] is None and len(recorders) == 1
    manager.discard(sid)
    store.close()


def test_a_saved_meeting_keeps_its_video_serves_it_and_can_drop_just_the_video(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "probe", lambda path, **kw: (10.0, (1280, 720)))  # the test's "video" is just bytes

    def screens(folder, clock):
        return capture_with(folder, clock)[0]
    clock = Clock()
    devices = FakeDevices()
    app = make_app(tmp_path, devices)
    app.state.meeting.screens = screens
    with TestClient(app) as client:
        sid = client.post("/api/meeting/start", json={"title": "Call", "screen": True}).json()["id"]
        capture = app.state.meeting.captures[sid]
        capture._clock = clock  # step the meeting's clock by hand, as the other meeting tests do
        capture._t0 = 0.0
        play(devices, clock, capture, 2.0, you=True)
        live_dir = tmp_path / "data" / "live" / sid
        (live_dir / "screen-000.mp4").write_bytes(b"VIDEO-BYTES")
        rid = client.post(f"/api/meeting/{sid}/finish").json()["recording_id"]
        detail = client.get(f"/api/recordings/{rid}").json()
        assert detail["screen"]["bytes"] == len(b"VIDEO-BYTES") and "offset" in detail["screen"]
        served = client.get(f"/api/recordings/{rid}/screen")
        assert served.status_code == 200 and served.content == b"VIDEO-BYTES" and served.headers["content-type"] == "video/mp4"
        assert client.delete(f"/api/recordings/{rid}/screen").status_code == 204
        assert client.get(f"/api/recordings/{rid}/screen").status_code == 404
        assert client.delete(f"/api/recordings/{rid}/screen").status_code == 404
        after = client.get(f"/api/recordings/{rid}").json()
        assert after["screen"] is None and after["has_audio"] is True  # the recording itself is untouched


def test_a_recording_without_a_screen_has_none(tmp_path):
    devices = FakeDevices()
    app = make_app(tmp_path, devices)
    with TestClient(app) as client:
        sid = client.post("/api/meeting/start", json={"title": "Call"}).json()["id"]
        capture = app.state.meeting.captures[sid]
        clock = Clock()
        capture._clock, capture._t0 = clock, 0.0
        play(devices, clock, capture, 2.0, you=True)
        rid = client.post(f"/api/meeting/{sid}/finish").json()["recording_id"]
        assert client.get(f"/api/recordings/{rid}").json()["screen"] is None
        assert client.get(f"/api/recordings/{rid}/screen").status_code == 404


def test_the_video_is_never_given_to_a_provider():
    import pathlib
    for module in ("pipeline.py", "naming.py", "brain/index.py", "notes/writer.py", "recap/script.py"):
        text = (pathlib.Path(screen.__file__).parent / module).read_text(encoding="utf-8")
        assert "screen.mp4" not in text and "SCREEN_FILE" not in text


# --- the screen with the other ways of recording (microphone, computer sound, both)

def test_a_browser_recording_can_record_the_screen_too_and_keeps_the_video(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "probe", lambda path, **kw: (10.0, (1280, 720)))
    from .helpers import FakeClock
    from .test_live_recording import SPEECH, make, run_live, webm_bytes
    env = type("Env", (), {})()
    env.store, env.tmp = Store(tmp_path / "app.db"), tmp_path
    manager = make(env, FakeTranscriber(), FakeClock())
    sid = manager.create(title="Lecture", subject_id=None, language="en")
    recorder, made = capture_with(tmp_path / "live" / sid, Clock())
    manager.start_screen(sid, recorder, 0.0)
    assert manager.poll(sid)["screen"] == {"error": None, "notice": None, "low_disk": recorder.low_disk()}
    run_live(manager, sid, webm_bytes(tmp_path, SPEECH))
    (tmp_path / "live" / sid / "screen-000.mp4").write_bytes(b"LECTURE-VIDEO")
    rid = manager.finish(sid)
    assert made["proc"].sent == b"q\n"  # asked to finish before the recording was saved
    assert (tmp_path / "recordings" / rid / SCREEN_FILE).read_bytes() == b"LECTURE-VIDEO"
    assert screen.screen_info(tmp_path / "recordings" / rid)["bytes"] == len(b"LECTURE-VIDEO")
    assert not (tmp_path / "live" / sid).exists()
    env.store.close()


def test_discarding_or_stopping_a_live_recording_stops_its_screen(tmp_path):
    from .helpers import FakeClock
    from .test_live_recording import make
    env = type("Env", (), {})()
    env.store, env.tmp = Store(tmp_path / "app.db"), tmp_path
    manager = make(env, FakeTranscriber(), FakeClock())
    first = manager.create(title="A", subject_id=None, language="en")
    one, made_one = capture_with(tmp_path / "live" / first, Clock())
    manager.start_screen(first, one, 0.0)
    manager.discard(first)
    assert made_one["proc"].sent == b"q\n" and not one.running

    second = manager.create(title="B", subject_id=None, language="en")
    two, made_two = capture_with(tmp_path / "live" / second, Clock())
    manager.start_screen(second, two, 0.0)
    manager.stop()  # the app is shutting down
    assert made_two["proc"].sent == b"q\n"
    env.store.close()


def test_a_leftover_session_after_a_crash_is_recovered_without_a_runaway_recorder(tmp_path):
    from .helpers import FakeClock
    from .test_live_recording import make
    env = type("Env", (), {})()
    env.store, env.tmp = Store(tmp_path / "app.db"), tmp_path
    manager = make(env, FakeTranscriber(), FakeClock())
    folder = tmp_path / "live" / "abc"
    folder.mkdir(parents=True)
    (folder / PID_FILE).write_text("4242")
    stopped = []
    original = screen.stop_stale
    screen.stop_stale = lambda f, **kw: stopped.append(f.name) or False  # the real one would ask Windows
    try:
        manager.recover()
    finally:
        screen.stop_stale = original
    assert stopped == ["abc"]  # looked for a leftover recorder in every unfinished session
    env.store.close()


def test_the_live_api_starts_the_screen_only_when_asked(tmp_path):
    started = []

    def screens(folder, clock):
        recorder, _ = capture_with(folder, clock)
        started.append(recorder)
        return recorder
    app = make_app(tmp_path, FakeDevices())
    app.state.meeting.screens = screens
    with TestClient(app) as client:
        plain = client.post("/api/live", json={"title": "Mic only"}).json()["id"]
        assert started == [] and client.get(f"/api/live/{plain}").json()["screen"] is None
        sid = client.post("/api/live", json={"title": "With screen", "screen": True}).json()["id"]
        assert len(started) == 1 and started[0].running
        assert client.get(f"/api/live/{sid}").json()["screen"]["error"] is None
        client.delete(f"/api/live/{sid}")
        assert not started[0].running


# --- one MP4 with the picture and the sound, to take out of the app

def test_the_download_joins_the_sound_and_the_picture_in_step(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / SCREEN_FILE).write_bytes(b"video")
    (rec / OFFSET_FILE).write_text(json.dumps({"offset": 1.33}))
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        open(cmd[-1], "wb").write(b"mp4")
        return subprocess.CompletedProcess(cmd, 0)
    dest = tmp_path / "out.mp4"
    assert screen.make_download(rec, tmp_path / "audio.flac", dest, run=run, ffmpeg="ffmpeg")
    cmd = calls[0]
    assert cmd[cmd.index("-itsoffset") + 1] == "1.33"  # the picture starts this long after the sound
    assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[cmd.index("-c:a") + 1] == "aac"  # picture untouched
    assert dest.read_bytes() == b"mp4"


def test_no_download_without_a_screen_or_audio_and_a_failed_join_leaves_nothing(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    dest = tmp_path / "out.mp4"
    assert screen.make_download(rec, tmp_path / "audio.flac", dest) is False  # no screen video
    (rec / SCREEN_FILE).write_bytes(b"video")
    assert screen.make_download(rec, None, dest) is False  # no audio

    def failing(cmd, **kwargs):
        open(cmd[-1], "wb").write(b"half")
        return subprocess.CompletedProcess(cmd, 1)
    assert screen.make_download(rec, tmp_path / "audio.flac", dest, run=failing, ffmpeg="ffmpeg") is False
    assert not dest.exists()


def test_the_real_ffmpeg_makes_one_file_with_both_streams_you_can_skip_around_in(tmp_path):
    from transcriber import audio
    rec = tmp_path / "rec"
    rec.mkdir()
    flac = tmp_path / "audio.flac"
    subprocess.run([audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
                    "-ar", "16000", "-ac", "1", str(flac)], check=True)
    subprocess.run([audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=5:duration=5",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-g", "10", str(rec / SCREEN_FILE)], check=True)
    (rec / OFFSET_FILE).write_text(json.dumps({"offset": 1.0}))
    dest = tmp_path / "joined.mp4"
    assert screen.make_download(rec, flac, dest)
    text = subprocess.run([audio.FFMPEG, "-hide_banner", "-i", str(dest)], capture_output=True, text=True).stderr
    assert "Video: h264" in text and "Audio: aac" in text
    skipped = subprocess.run([audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", "4", "-i", str(dest), "-frames:v", "1", "-f", "null", "-"],
                             capture_output=True)
    assert skipped.returncode == 0


def test_the_download_endpoint_sends_the_file_and_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "probe", lambda path, **kw: (10.0, (1280, 720)))

    def fake_make(folder, sound, dest, **kw):
        dest.write_bytes(b"JOINED")
        return True
    import transcriber.app as app_module
    monkeypatch.setattr(app_module, "make_screen_download", fake_make)
    clock = Clock()
    devices = FakeDevices()
    app = make_app(tmp_path, devices)
    app.state.meeting.screens = lambda folder, clk: capture_with(folder, clk)[0]
    with TestClient(app) as client:
        sid = client.post("/api/meeting/start", json={"title": "Class: part 1", "screen": True}).json()["id"]
        capture = app.state.meeting.captures[sid]
        capture._clock, capture._t0 = clock, 0.0
        play(devices, clock, capture, 2.0, you=True)
        (tmp_path / "data" / "live" / sid / "screen-000.mp4").write_bytes(b"VIDEO")
        rid = client.post(f"/api/meeting/{sid}/finish").json()["recording_id"]
        got = client.get(f"/api/recordings/{rid}/screen/download")
        assert got.status_code == 200 and got.content == b"JOINED" and got.headers["content-type"] == "video/mp4"
        from urllib.parse import unquote
        assert "Class- part 1 - video with sound.mp4" in unquote(got.headers["content-disposition"])  # a name Windows accepts
        assert not list((tmp_path / "data" / "recordings" / rid).glob("download-*"))  # the temporary file is gone
        client.delete(f"/api/recordings/{rid}/screen")
        assert client.get(f"/api/recordings/{rid}/screen/download").status_code == 404
        assert client.get("/api/recordings/nope/screen/download").status_code == 404
