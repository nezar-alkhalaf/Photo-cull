"""Run one step, or all three, with the same calls the individual buttons use (plan section 9).

Used by the GUI and by `python -m photocull all`. No GUI imports here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import Config
from .paths import CoverageLayout
from .runner import CancelToken, LogCb, ProgressCb, StepResult, noop_log, noop_progress

STEPS = ("cull", "edit", "split")
STEP_TITLES = {"cull": "Cull", "edit": "Edit in Photoshop", "split": "Split into posts"}
# What the user may answer when a step finds earlier results (first = the usual one).
CHOICES = {"cull": ("clear",), "edit": ("reprocess",), "split": ("clear", "new")}

# decide(step, result_with_needs_decision, choices) -> chosen value, "keep", or None (cancel)
Decide = Callable[[str, StepResult, tuple[str, ...]], "str | None"]
ConfirmPreview = Callable[[StepResult], bool]
StepHook = Callable[[str, str, "StepResult | None"], None]   # step, "running"/"finished", result


@dataclass
class RunParams:
    mode: str = "portrait"
    style: str | None = None
    action: tuple[str, str] | None = None
    exposure: bool | None = None            # None = config default
    preview: int = 0
    max_per_post: int = 20
    cull_input: str | None = None
    edit_input: str | None = None
    split_input: str | None = None


def _call(step: str, layout: CoverageLayout, cfg: Config, p: RunParams, existing: str,
          preview: int, progress_cb: ProgressCb, log_cb: LogCb,
          cancel: CancelToken) -> StepResult:
    if step == "cull":
        from .cull import run_cull
        return run_cull(layout, cfg, p.mode, p.cull_input, existing,
                        progress_cb=progress_cb, log_cb=log_cb, cancel=cancel)
    if step == "edit":
        from .edit import run_edit
        return run_edit(layout, cfg, p.style, p.action, p.edit_input, p.exposure, preview,
                        existing, progress_cb=progress_cb, log_cb=log_cb, cancel=cancel)
    if step == "split":
        from .split import run_split
        return run_split(layout, p.max_per_post, p.split_input, existing,
                         progress_cb=progress_cb, log_cb=log_cb, cancel=cancel)
    raise ValueError(f"Unknown step: {step}")


def run_step(step: str, layout: CoverageLayout, cfg: Config, p: RunParams,
             progress_cb: ProgressCb = noop_progress, log_cb: LogCb = noop_log,
             cancel: CancelToken | None = None, decide: Decide | None = None,
             allow_keep: bool = False, preview: int | None = None) -> StepResult:
    """One step. When it finds earlier results it asks `decide` what to do."""
    cancel = cancel or CancelToken()
    preview = p.preview if preview is None else preview
    res = _call(step, layout, cfg, p, "ask", preview, progress_cb, log_cb, cancel)
    if not res.data.get("needs_decision"):
        return res
    choices = CHOICES[step] + (("keep",) if allow_keep else ())
    choice = decide(step, res, choices) if decide else None
    if choice == "keep":
        return StepResult(True, "Kept the earlier results.", data={"kept": True})
    if choice not in CHOICES[step]:
        return StepResult(False, "Nothing was changed: the earlier results are still there.",
                          data={"declined": True})
    return _call(step, layout, cfg, p, choice, preview, progress_cb, log_cb, cancel)


def run_all(layout: CoverageLayout, cfg: Config, p: RunParams,
            progress_cb: ProgressCb = noop_progress, log_cb: LogCb = noop_log,
            cancel: CancelToken | None = None, decide: Decide | None = None,
            confirm_preview: ConfirmPreview | None = None,
            on_step: StepHook | None = None) -> StepResult:
    """Step 1 -> 2 -> 3. Stops at the first step that fails, is stopped, or is declined."""
    cancel = cancel or CancelToken()
    hook = on_step or (lambda *_: None)
    last = StepResult(True, "")
    for n, step in enumerate(STEPS, start=1):
        if cancel.is_set():
            return StepResult(False, "Stopped.", True)
        hook(step, "running", None)
        res = run_step(step, layout, cfg, p, progress_cb, log_cb, cancel, decide,
                       allow_keep=step != "split")
        if step == "edit" and res.data.get("preview_done"):
            hook(step, "finished", res)
            if not (confirm_preview and confirm_preview(res)):
                return StepResult(False, res.message + " Run All paused here.",
                                  data={"preview_done": True, "paused": True})
            hook(step, "running", None)
            res = run_step(step, layout, cfg, p, progress_cb, log_cb, cancel, decide,
                           preview=0)
        hook(step, "finished", res)
        if not res.ok:
            msg = res.message if res.cancelled or res.data.get("declined") else \
                f"Step {n} ({STEP_TITLES[step]}) did not finish. {res.message}"
            return StepResult(False, msg, res.cancelled, res.done, res.total, res.errors,
                              {**res.data, "failed_step": step})
        last = res
    return StepResult(True, "All three steps finished. " + last.message)
