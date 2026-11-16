"""Record the whole screen alongside a meeting, on this laptop only.

One ffmpeg process grabs the primary screen with Windows' own screen capture (gdigrab) and writes it in ten-minute
pieces. The pieces are "fragmented" MP4 with a keyframe every 2 seconds, so they stay playable (all but the last
couple of seconds) even if the process is cut off mid-piece. If ffmpeg stops
(the screen was locked, the display changed, ...) it is started again after a moment, and the time it missed is filled
with black when the pieces are joined, so the video stays in step with the audio. When the meeting is saved the pieces
are joined into one screen.mp4 next to the recording's audio, with the number of seconds the video starts after the
audio, so that clicking a transcript line can show the screen as it was then. Nothing here is sent anywhere.

The ffmpeg process is tied to this app (a Windows job object, plus a record of its process id that the next start
checks), so it can never be left recording after the app is closed, killed or crashes."""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import audio

log = logging.getLogger(__name__)

FRAME_RATE = 5
MAX_WIDTH = 1280
SEGMENT_SEC = int(os.environ.get("TRANSCRIBER_SCREEN_SEGMENT_SEC", "600"))
FRAGMENT_PATTERN = "screen-%03d.mp4"
OFFSET_FILE = "screen.json"
SCREEN_FILE = "screen.mp4"
PID_FILE = "screen.pid"
STOP_WAIT_SEC = 15
LOW_DISK_BYTES = 5 * 1024**3  # warn
STOP_DISK_BYTES = 1 * 1024**3  # stop the video (the audio carries on) rather than fill the disk
MIN_GAP_SEC = 0.5
MAX_STARTUP_FAILURES = 5  # in a row, before any picture was ever recorded: something is wrong, don't keep trying
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def primary_screen_size() -> tuple[int, int] | None:
    """The primary monitor's size in real pixels (not scaled by Windows' display scaling), or None if unknown."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        old = None
        try:
            old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor aware, for this thread only
        except Exception:
            pass
        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        if old:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
        return (width, height) if width > 0 and height > 0 else None
    except Exception:
        return None


def capture_command(ffmpeg: str, folder: Path, size: tuple[int, int] | None, start_number: int = 0) -> list[str]:
    grab = ["-offset_x", "0", "-offset_y", "0", "-video_size", f"{size[0]}x{size[1]}"] if size else []
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "gdigrab", "-framerate", str(FRAME_RATE), *grab, "-i", "desktop",
            "-vf", f"scale='min({MAX_WIDTH},iw)':-2,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-g", str(FRAME_RATE * 2), "-an",
            "-f", "segment", "-segment_time", str(SEGMENT_SEC), "-reset_timestamps", "1", "-segment_format", "mp4",
            "-segment_format_options", "movflags=frag_keyframe+empty_moov+default_base_moof:flush_packets=1",
            "-segment_start_number", str(start_number), str(folder / FRAGMENT_PATTERN)]


# --- tying the recorder to this app

_job = None


def _kill_on_close_job():
    """A Windows job object that kills what is in it when this app's handle to it closes, which includes the app being
    killed. None when that isn't possible (other systems)."""
    global _job
    if _job is not None:
        return _job or None
    _job = False
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        handle = kernel32.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not handle or not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        _job = (kernel32, handle)
    except Exception:
        log.warning("Couldn't tie the screen recorder to this app", exc_info=True)
        return None
    return _job


def tie_to_app(proc) -> None:
    """Make the recorder die with this app, however the app ends. Best effort: the pid check at start-up is the backup."""
    job = _kill_on_close_job()
    handle = getattr(proc, "_handle", None)
    if job and handle:
        try:
            job[0].AssignProcessToJobObject(job[1], int(handle))
        except Exception:
            log.warning("Couldn't tie the screen recorder to this app", exc_info=True)


def stop_stale(folder: Path, *, run=subprocess.run) -> bool:
    """A recorder left behind by an earlier run of the app (the app was killed before it could stop it): end it.
    Only a process whose name is ffmpeg is ever touched. Returns True if one was stopped."""
    pid_file = folder / PID_FILE
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    stopped = False
    try:
        if sys.platform == "win32":
            listing = run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                          creationflags=CREATE_NO_WINDOW).stdout.lower()
            if "ffmpeg" in listing:
                run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)
                stopped = True
    except Exception:
        log.warning("Couldn't check for a leftover screen recorder", exc_info=True)
    pid_file.unlink(missing_ok=True)
    return stopped


# --- recording

class ScreenCapture:
    """The screen recorder for one meeting. Failing never stops the meeting: `error` says why recording gave up,
    and `notice` says it is trying again."""

    def __init__(self, folder: Path, *, clock=time.monotonic, popen=subprocess.Popen, ffmpeg: str | None = None,
                 size=primary_screen_size, free_bytes=None, retry_wait=lambda n: min(2.0 * n, 10.0)):
        self.folder, self._clock, self._popen = folder, clock, popen
        self._ffmpeg = ffmpeg or audio.FFMPEG
        self._size = size
        self._free = free_bytes or (lambda: shutil.disk_usage(folder).free)
        self._retry_wait = retry_wait
        self.error: str | None = None
        self.notice: str | None = None
        self.offset: float | None = None
        self.runs: list[dict] = []
        self._t0 = 0.0
        self._proc = None
        self._thread = None
        self._halt = threading.Event()
        self._log = None
        self._run_index = 0
        self._run_seen = False
        self._ever_seen = False
        self._failures = 0

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # --- one ffmpeg run

    def _next_index(self) -> int:
        taken = [int(m.group(1)) for p in self.folder.glob("screen-*.mp4") if (m := re.match(r"screen-(\d+)\.mp4$", p.name))]
        return max(taken) + 1 if taken else 0

    def _launch(self) -> bool:
        self._run_index = self._next_index()
        self._run_seen = False
        try:
            if self._log is None:
                self._log = open(self.folder / "screen.log", "ab")
            self._proc = self._popen(capture_command(self._ffmpeg, self.folder, self._size(), self._run_index),
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._log,
                                     creationflags=CREATE_NO_WINDOW)
        except Exception as exc:
            self._proc = None
            self.error = f"The screen couldn't be recorded ({type(exc).__name__}). The meeting is still being recorded."
            log.warning("Screen capture failed to start: %s", exc)
            return False
        tie_to_app(self._proc)
        try:
            (self.folder / PID_FILE).write_text(str(getattr(self._proc, "pid", "")), encoding="utf-8")
        except OSError:
            pass
        return True

    def _reason(self) -> str:
        try:
            if self._log:
                self._log.flush()
            lines = (self.folder / "screen.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()
            return lines[-1][:140] if lines else ""
        except OSError:
            return ""

    def _write_json(self) -> None:
        (self.folder / OFFSET_FILE).write_text(json.dumps({"offset": self.offset, "runs": self.runs}), encoding="utf-8")

    def _note_first_picture(self) -> None:
        """The first piece of a run exists, so the video has really started: note how long after the audio that was."""
        seconds = round(max(self._clock() - self._t0, 0.0), 2)
        if not self.runs:
            self.offset = seconds  # replaces the early guess made when ffmpeg was started
        self.runs.append({"start_index": self._run_index, "offset": seconds})
        self._write_json()
        self._run_seen = self._ever_seen = True
        self._failures = 0
        self.notice = None

    # --- start, watch, stop

    def start(self, t0: float) -> None:
        """t0: the clock reading when the meeting's audio started, so the video can say how late it began."""
        self._t0 = t0
        self.folder.mkdir(parents=True, exist_ok=True)
        stop_stale(self.folder)
        if not self._launch():
            return
        self.offset = round(max(self._clock() - t0, 0.0), 2)  # a first guess, so a crash can still be recovered
        self._write_json()
        self._thread = threading.Thread(target=self._supervise, name="screen-watch", daemon=True)
        self._thread.start()

    def _supervise(self) -> None:
        last_disk_check = 0.0
        while not self._halt.wait(0.1):
            proc = self._proc
            if proc is None:
                return
            if proc.poll() is None:
                if not self._run_seen and (self.folder / (FRAGMENT_PATTERN % self._run_index)).exists():
                    self._note_first_picture()
                now = self._clock()
                if now - last_disk_check >= 5.0:
                    last_disk_check = now
                    if self._disk_full():
                        return
                continue
            if self._halt.is_set():
                return
            if not self._recover():
                return

    def _disk_full(self) -> bool:
        try:
            if self._free() >= STOP_DISK_BYTES:
                return False
        except OSError:
            return False
        self.error = ("The screen recording was stopped because the disk is almost full. "
                      "The audio is still being recorded.")
        log.warning("Screen capture stopped: disk almost full")
        self._end_process()
        return True

    def _recover(self) -> bool:
        """ffmpeg stopped by itself. Say so, wait a moment and start it again. False when it is not worth trying again."""
        reason = self._reason()
        if not self._run_seen and not self._ever_seen:
            self._failures += 1
            if self._failures >= MAX_STARTUP_FAILURES:
                self.error = ("The screen couldn't be recorded" + (f" ({reason})" if reason else "")
                              + ". The meeting is still being recorded.")
                log.warning("Screen capture never started: %s", reason)
                return False
        else:
            self._failures += 1
        self.notice = ("The screen recording paused" + (f" ({reason})" if reason else "")
                       + ". Trying again; the meeting is still being recorded.")
        log.warning("Screen capture stopped, restarting: %s", reason)
        if self._halt.wait(self._retry_wait(min(self._failures, 5))):
            return False
        return self._launch()

    def check(self) -> None:
        """Called while recording: nothing to do now (the watcher thread handles problems); kept for callers."""

    def _end_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.stdin.write(b"q\n")  # ffmpeg's own way to finish: the last piece is closed properly
                proc.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                proc.wait(timeout=STOP_WAIT_SEC)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        try:
            proc.stdin.close()
        except (OSError, ValueError, AttributeError):
            pass
        (self.folder / PID_FILE).unlink(missing_ok=True)

    def stop(self) -> None:
        self._halt.set()
        thread, self._thread = self._thread, None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=STOP_WAIT_SEC + 5)
        self._end_process()
        if self._log:
            self._log.close()
            self._log = None

    def low_disk(self) -> bool:
        try:
            return self._free() < LOW_DISK_BYTES
        except OSError:
            return False


# --- joining the pieces

def fragments_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("screen-*.mp4") if p.stat().st_size > 0)


def _index_of(path: Path) -> int:
    return int(re.search(r"screen-(\d+)\.mp4$", path.name).group(1))


def probe(path: Path, *, run=subprocess.run, ffmpeg: str | None = None) -> tuple[float, tuple[int, int] | None] | None:
    """(duration in seconds, (width, height)) of a video piece, or None if it can't be played."""
    result = run([ffmpeg or audio.FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                 creationflags=CREATE_NO_WINDOW)
    text = (result.stderr or "") + (result.stdout or "")
    found = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not found or "Invalid data" in text or "moov atom not found" in text:
        return None
    seconds = int(found.group(1)) * 3600 + int(found.group(2)) * 60 + float(found.group(3))
    size = re.search(r"Video:.*?, (\d{2,5})x(\d{2,5})", text)
    return seconds, ((int(size.group(1)), int(size.group(2))) if size else None)


def _black_clip(dest: Path, seconds: float, size: tuple[int, int], *, run, ffmpeg: str) -> bool:
    result = run([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
                  "-i", f"color=c=black:s={size[0]}x{size[1]}:r={FRAME_RATE}:d={seconds:.2f}",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-g", str(FRAME_RATE * 2),
                  "-pix_fmt", "yuv420p", str(dest)], capture_output=True, creationflags=CREATE_NO_WINDOW)
    return result.returncode == 0 and dest.exists()


def _read_runs(folder: Path) -> tuple[float, list[dict]]:
    try:
        data = json.loads((folder / OFFSET_FILE).read_text(encoding="utf-8"))
        offset = float(data.get("offset", 0.0))
        runs = [{"start_index": int(r["start_index"]), "offset": float(r["offset"])} for r in data.get("runs", [])]
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return 0.0, []
    return offset, sorted(runs, key=lambda r: r["start_index"])


def plan_timeline(pieces: list[Path], runs: list[dict], first_offset: float, durations: dict[Path, float | None],
                  segment_sec: float = SEGMENT_SEC) -> list[tuple[str, object]]:
    """The order to join things in: ("piece", path) for video that exists and ("black", seconds) for time with no
    video (the recorder was stopped and restarted, or a middle piece is unplayable), so later video stays in step."""
    plan: list[tuple[str, object]] = []
    run_starts = {r["start_index"]: r["offset"] for r in runs}
    ordered = sorted(pieces, key=_index_of)

    def ends_its_run(position: int) -> bool:
        return position + 1 >= len(ordered) or _index_of(ordered[position + 1]) in run_starts

    cursor = first_offset  # where the video timeline has got to, in audio time
    for position, path in enumerate(ordered):
        index = _index_of(path)
        if index in run_starts and plan:  # a new run began: whatever time the old one didn't cover is black
            gap = run_starts[index] - cursor
            if gap >= MIN_GAP_SEC:
                plan.append(("black", round(gap, 2)))
                cursor += gap
        duration = durations.get(path)
        if duration is None:  # can't be played
            if not ends_its_run(position):
                plan.append(("black", float(segment_sec)))
                cursor += segment_sec
            continue
        plan.append(("piece", path))
        cursor += duration
    return plan


def join_into(folder: Path, rec_dir: Path, *, run=subprocess.run, ffmpeg: str | None = None) -> bool:
    """Join a finished meeting's pieces into rec_dir/screen.mp4 and keep the offset beside it. Missing stretches are
    filled with black so the video stays in step with the audio. Returns False, leaving nothing behind, when there was
    no usable video."""
    ffmpeg = ffmpeg or audio.FFMPEG
    pieces = fragments_in(folder)
    if not pieces:
        return False
    probed = {p: probe(p, run=run, ffmpeg=ffmpeg) for p in pieces}
    durations = {p: (info[0] if info else None) for p, info in probed.items()}
    good = [p for p in pieces if durations[p] is not None]
    if not good:
        return False
    first_offset, runs = _read_runs(folder)
    size = next((probed[p][1] for p in good if probed[p][1]), None)
    plan = plan_timeline(pieces, runs, first_offset, durations)
    dest = rec_dir / SCREEN_FILE
    entries: list[Path] = []
    for n, (kind, value) in enumerate(plan):
        if kind == "piece":
            entries.append(value)
        elif size:
            clip = folder / f"screen-black-{n:03d}.mp4"
            if _black_clip(clip, float(value), size, run=run, ffmpeg=ffmpeg):
                entries.append(clip)
    if len(entries) == 1:
        shutil.copyfile(entries[0], dest)
    else:
        listing = folder / "screen-list.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in entries), encoding="utf-8")
        base = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                "-i", str(listing)]
        done = False
        for codec in (["-c", "copy"], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p"]):
            result = run([*base, *codec, "-movflags", "+faststart", str(dest)], capture_output=True,
                         creationflags=CREATE_NO_WINDOW)
            if result.returncode == 0:
                done = True
                break
            dest.unlink(missing_ok=True)
            log.warning("Joining the screen recording with %s failed", codec[1])
        if not done:
            return False
    (rec_dir / OFFSET_FILE).write_text(json.dumps({"offset": first_offset}), encoding="utf-8")
    return True


def screen_info(rec_dir: Path) -> dict | None:
    """What the recording page needs to show the video: how late it starts and how big it is. None when there is none."""
    video = rec_dir / SCREEN_FILE
    if not video.exists():
        return None
    try:
        offset = float(json.loads((rec_dir / OFFSET_FILE).read_text(encoding="utf-8")).get("offset", 0.0))
    except (OSError, ValueError, AttributeError):
        offset = 0.0
    return {"offset": offset, "bytes": video.stat().st_size}


def make_download(rec_dir: Path, audio_file: Path | None, dest: Path, *, run=subprocess.run,
                  ffmpeg: str | None = None) -> bool:
    """One MP4 with the meeting's sound and its screen video together, in step, for taking out of the app: it plays with
    sound and can be skipped around in any video player. The picture is copied as it is (nothing is re-encoded); the sound
    becomes AAC. False when there is no screen video or no audio, or ffmpeg failed (nothing is left at dest)."""
    info = screen_info(rec_dir)
    if info is None or audio_file is None:
        return False
    result = run([ffmpeg or audio.FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                  "-i", str(audio_file), "-itsoffset", f"{info['offset']:.2f}", "-i", str(rec_dir / SCREEN_FILE),
                  "-map", "1:v", "-map", "0:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "64k",
                  "-movflags", "+faststart", str(dest)], capture_output=True, creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        return False
    return True


def remove(rec_dir: Path) -> bool:
    gone = False
    for name in (SCREEN_FILE, OFFSET_FILE):
        path = rec_dir / name
        if path.exists():
            path.unlink()
            gone = True
    return gone
