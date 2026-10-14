"""Shared pieces of the step contract (plan section 4):

    run_step(params, progress_cb, log_cb, cancel_token) -> StepResult

Worker runs such a function on a background thread for the GUI. It never
touches a widget: everything it has to say goes into a queue that the main
thread drains (plan section 4, "Progress / cancel contract").
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Callable

ProgressCb = Callable[[int, int, str], None]   # done, total, current name
LogCb = Callable[[str], None]


class CancelToken:
    """Thread-safe stop flag; steps check is_set() between images."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


@dataclass
class StepResult:
    ok: bool
    message: str = ""
    cancelled: bool = False
    done: int = 0
    total: int = 0
    errors: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def noop_progress(done: int, total: int, name: str) -> None:
    pass


def noop_log(msg: str) -> None:
    pass


THREAD_MODE_BACKGROUND_BEGIN = 0x10000    # low CPU and disk priority for the calling thread
THREAD_MODE_BACKGROUND_END = 0x20000


def _set_background(on: bool) -> None:
    """Switch the CALLING thread in or out of Windows background mode (ignored elsewhere)."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentThread.restype = ctypes.c_void_p
        k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        k32.SetThreadPriority(k32.GetCurrentThread(), THREAD_MODE_BACKGROUND_BEGIN if on
                              else THREAD_MODE_BACKGROUND_END)
    except Exception:
        pass


def disable_power_throttling() -> bool:
    """Ask Windows 11 not to put this process in "efficiency mode" while its window is in the
    background (that mode makes batch work crawl on a slow PC). Ignored elsewhere."""
    try:
        import ctypes

        class State(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong),
                        ("StateMask", ctypes.c_ulong)]

        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                              ctypes.c_ulong]
        # version 1, control the "execution speed" bit (1), leave it off (0) = never throttle
        state = State(1, 1, 0)
        return bool(k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(state),
                                              ctypes.sizeof(state)))   # 4 = ProcessPowerThrottling
    except Exception:
        return False


class Worker:
    """Run fn(progress_cb, log_cb, cancel) on a background thread.

    Events for the owner to drain with poll() from its own thread:
      ("progress", done, total, name)   ("log", text)
      ("ask", Question)                 ("done", StepResult)
    """

    def __init__(self, fn: Callable[[ProgressCb, LogCb, CancelToken], StepResult]):
        self._fn = fn
        self.cancel = CancelToken()
        self.events: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.finished = threading.Event()
        # The window sets this. While the user is looking at the window the work runs at low
        # priority so the window stays smooth; once they switch to another program it runs
        # at normal priority so it keeps going at full speed in the background.
        self.window_active = True
        self._bg: bool | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="photocull-worker", daemon=True)
        self.thread.start()

    def _fit_priority(self) -> None:
        """Called from the worker thread only (background mode is per calling thread)."""
        want = bool(self.window_active)
        if want != self._bg:
            self._bg = want
            _set_background(want)

    def _run(self) -> None:
        disable_power_throttling()
        self._fit_priority()
        try:
            res = self._fn(self._progress, self._log, self.cancel)
        except BaseException as exc:                       # never let the thread die silently
            logging.getLogger("photocull").exception("Unexpected error in a step")
            res = StepResult(False, f"Unexpected error: {type(exc).__name__}: {exc}. "
                             "Details are in the logs folder.", errors=[str(exc)])
        self.finished.set()
        self.events.put(("done", res))

    def _progress(self, done: int, total: int, name: str) -> None:
        self._fit_priority()
        self.events.put(("progress", done, total, name))

    def _log(self, msg: str) -> None:
        self.events.put(("log", msg))

    def ask(self, kind: str, payload: dict | None = None) -> object:
        """Called from the worker: block until the main thread answers (None on Stop)."""
        q = Question(kind, payload or {})
        self.events.put(("ask", q))
        while not q.answered.wait(0.2):
            if self.cancel.is_set():
                return None
        return q.answer

    def poll(self) -> list[tuple]:
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    @property
    def running(self) -> bool:
        return self.thread is not None and not self.finished.is_set()


class Question:
    """A decision the worker needs from the user; the main thread calls reply()."""

    def __init__(self, kind: str, payload: dict):
        self.kind, self.payload = kind, payload
        self.answer: object = None
        self.answered = threading.Event()

    def reply(self, answer: object) -> None:
        self.answer = answer
        self.answered.set()
