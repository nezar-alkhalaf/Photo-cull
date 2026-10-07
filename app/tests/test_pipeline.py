import time

import pytest

from photocull import pipeline
from photocull.config import Config
from photocull.paths import CoverageLayout
from photocull.pipeline import RunParams, run_all, run_step
from photocull.runner import CancelToken, StepResult, Worker


class Fake:
    """Replaces pipeline._call: scripted results per step, records every call."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def __call__(self, step, layout, cfg, p, existing, preview, progress_cb, log_cb, cancel):
        self.calls.append((step, existing, preview))
        out = self.script[step]
        return out.pop(0) if isinstance(out, list) else out


@pytest.fixture
def lay(tmp_path):
    return CoverageLayout.for_main(tmp_path)


def ok(msg="ok"):
    return StepResult(True, msg)


def needs(n=3):
    return StepResult(False, "earlier results", data={"needs_decision": True, "existing": n})


def patch(monkeypatch, script):
    fake = Fake(script)
    monkeypatch.setattr(pipeline, "_call", fake)
    return fake


def test_all_three_steps_in_order(monkeypatch, lay):
    fake = patch(monkeypatch, {"cull": ok(), "edit": ok(), "split": ok("Copied 5 of 5")})
    seen = []
    res = run_all(lay, Config(), RunParams(), on_step=lambda s, w, r: seen.append((s, w)))
    assert res.ok and "Copied 5 of 5" in res.message
    assert [c[0] for c in fake.calls] == ["cull", "edit", "split"]
    assert seen == [("cull", "running"), ("cull", "finished"), ("edit", "running"),
                    ("edit", "finished"), ("split", "running"), ("split", "finished")]


def test_failure_stops_the_chain(monkeypatch, lay):
    fake = patch(monkeypatch, {"cull": ok(), "edit": StepResult(False, "Photoshop did not respond"),
                               "split": ok()})
    res = run_all(lay, Config(), RunParams())
    assert not res.ok and "Step 2" in res.message and "Photoshop did not respond" in res.message
    assert [c[0] for c in fake.calls] == ["cull", "edit"]
    assert res.data["failed_step"] == "edit"


def test_decision_replace_reruns_with_the_answer(monkeypatch, lay):
    fake = patch(monkeypatch, {"cull": [needs(), ok()], "edit": ok(), "split": ok()})
    asked = []

    def decide(step, res, choices):
        asked.append((step, choices, res.data["existing"]))
        return choices[0]

    res = run_all(lay, Config(), RunParams(), decide=decide)
    assert res.ok
    assert [c[:2] for c in fake.calls[:2]] == [("cull", "ask"), ("cull", "clear")]
    assert asked == [("cull", ("clear", "keep"), 3)]        # Run All offers "keep" for step 1


def test_decision_keep_skips_the_step(monkeypatch, lay):
    fake = patch(monkeypatch, {"cull": needs(), "edit": ok(), "split": ok()})
    res = run_all(lay, Config(), RunParams(), decide=lambda s, r, c: "keep")
    assert res.ok
    assert [c[1] for c in fake.calls if c[0] == "cull"] == ["ask"]     # never re-run


def test_decision_declined_changes_nothing(monkeypatch, lay):
    fake = patch(monkeypatch, {"cull": needs(), "edit": ok(), "split": ok()})
    res = run_all(lay, Config(), RunParams(), decide=lambda s, r, c: None)
    assert not res.ok and res.data["declined"]
    assert len(fake.calls) == 1


def test_split_offers_clear_or_new_but_not_keep(monkeypatch, lay):
    patch(monkeypatch, {"split": [needs(2), ok()]})
    got = []
    run_step("split", lay, Config(), RunParams(),
             decide=lambda s, r, c: got.append(c) or "new")
    assert got == [("clear", "new")]


def test_preview_pause_and_continue(monkeypatch, lay):
    prev = StepResult(False, "Preview ready", data={"preview_done": True})
    fake = patch(monkeypatch, {"cull": ok(), "edit": [prev, ok()], "split": ok()})
    res = run_all(lay, Config(), RunParams(preview=5), confirm_preview=lambda r: True)
    assert res.ok
    assert [(c[0], c[2]) for c in fake.calls] == [("cull", 5), ("edit", 5), ("edit", 0),
                                                  ("split", 5)]


def test_preview_pause_stops_run_all(monkeypatch, lay):
    prev = StepResult(False, "Preview ready", data={"preview_done": True})
    fake = patch(monkeypatch, {"cull": ok(), "edit": prev, "split": ok()})
    res = run_all(lay, Config(), RunParams(preview=5), confirm_preview=lambda r: False)
    assert not res.ok and res.data["paused"]
    assert [c[0] for c in fake.calls] == ["cull", "edit"]


def test_cancel_between_steps(monkeypatch, lay):
    cancel = CancelToken()
    fake = patch(monkeypatch, {"cull": ok(), "edit": ok(), "split": ok()})
    run_all(lay, Config(), RunParams(), cancel=cancel,
            on_step=lambda s, w, r: cancel.set() if (s, w) == ("cull", "finished") else None)
    assert [c[0] for c in fake.calls] == ["cull"]


def test_worker_events_and_unexpected_error():
    def fn(progress, log, cancel):
        progress(1, 2, "a.jpg")
        log("hello")
        return StepResult(True, "fine")

    w = Worker(fn)
    w.start()
    w.thread.join(5)
    assert [e[0] for e in w.poll()] == ["progress", "log", "done"]

    def boom(progress, log, cancel):
        raise RuntimeError("kaput")

    w = Worker(boom)
    w.start()
    w.thread.join(5)
    done = [e for e in w.poll() if e[0] == "done"][0][1]
    assert not done.ok and "kaput" in done.message


def test_worker_ask_roundtrip_and_stop():
    holder = {}

    def fn(progress, log, cancel):
        return StepResult(True, str(holder["w"].ask("decide", {"x": 1})))

    w = holder["w"] = Worker(fn)
    w.start()
    for _ in range(100):
        q = next((e[1] for e in w.poll() if e[0] == "ask"), None)
        if q:
            q.reply("clear")
            break
        time.sleep(0.02)
    w.thread.join(5)
    assert [e for e in w.poll() if e[0] == "done"][0][1].message == "clear"

    w = holder["w"] = Worker(fn)                   # Stop while waiting for an answer
    w.start()
    time.sleep(0.1)
    w.cancel.set()
    w.thread.join(5)
    assert [e for e in w.poll() if e[0] == "done"][0][1].message == "None"
