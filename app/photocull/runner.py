"""Shared pieces of the step contract (plan section 4):

    run_step(params, progress_cb, log_cb, cancel_token) -> StepResult

The worker-thread runner for the GUI is added in Stage 5.
"""

from __future__ import annotations

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
