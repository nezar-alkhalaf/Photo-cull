import sys

import pytest

from photocull import runner
from photocull.runner import StepResult, Worker


def test_worker_is_low_priority_only_while_the_window_is_in_front(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "_set_background", lambda on: calls.append(on))
    monkeypatch.setattr(runner, "disable_power_throttling", lambda: True)
    holder = {}

    def job(progress, log, cancel):
        progress(1, 3, "a")                      # window in front: low priority
        holder["w"].window_active = False        # user switches to another program
        progress(2, 3, "b")
        progress(3, 3, "c")                      # no change: no extra call
        holder["w"].window_active = True         # user comes back
        progress(3, 3, "")
        return StepResult(True, "ok")

    w = Worker(job)
    holder["w"] = w
    w.start()
    w.thread.join(5)
    assert calls == [True, False, True]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_power_throttling_opt_out_is_accepted_by_windows():
    assert runner.disable_power_throttling() is True
