"""Keep Windows awake while a meeting is being recorded.

Capture stops when the computer sleeps, and a screen recording can't be taken while the display is off, so the
computer is asked not to sleep for as long as a recording runs, and to let the display stay on if the screen is being
recorded. It is released the moment the recording ends (or the app dies), and it does nothing on other systems."""
import logging
import sys
import threading

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def _windows_setter():
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
    return lambda flags: kernel32.SetThreadExecutionState(flags)


class KeepAwake:
    """Holds the request from its own thread (Windows ties the request to the thread that made it)."""

    def __init__(self, *, display: bool = False, setter=None):
        self.display = display
        self._setter = setter
        self._halt = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self.active = False

    def start(self) -> None:
        if self._setter is None:
            if sys.platform != "win32":
                return
            try:
                self._setter = _windows_setter()
            except Exception:
                log.warning("Couldn't ask Windows to stay awake", exc_info=True)
                return
        self._thread = threading.Thread(target=self._hold, name="keep-awake", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2)

    def _hold(self) -> None:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | (ES_DISPLAY_REQUIRED if self.display else 0)
        try:
            self.active = bool(self._setter(flags))
        except Exception:
            log.warning("Couldn't ask Windows to stay awake", exc_info=True)
        self._ready.set()
        self._halt.wait()
        try:
            self._setter(ES_CONTINUOUS)  # back to normal
        except Exception:
            pass
        self.active = False

    def stop(self) -> None:
        self._halt.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
