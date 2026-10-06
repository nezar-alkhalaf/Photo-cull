"""Step 2: exposure fix + Photoshop style for each photo (plan section 7.3).

Per photo: the working copy is written into Edited/ first (exposure-corrected
or a plain copy), and Photoshop only ever opens and overwrites that copy.
Selected/ and Full_Coverage/ are only read.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import asdict
from pathlib import Path

from .config import Config
from .exposure import correct_exposure
from .faces import make_detector
from .logsetup import get_logger
from .paths import (CoverageLayout, SafetyError, check_not_in_source, check_output_safe,
                    list_jpegs, preflight, relative_name, state_key, unique_path)
from .photoshop import Photoshop, PhotoshopError
from .runner import CancelToken, LogCb, ProgressCb, StepResult, noop_log, noop_progress
from .state import State
from .styles import list_action_files, read_atn_set_name, resolve_style


def _say(log_cb: LogCb, msg: str, level: int = logging.INFO) -> None:
    get_logger().log(level, msg)
    log_cb(msg)


def parse_action(text: str | None) -> tuple[str, str] | None:
    """'Set/Action' -> ('Set', 'Action'); empty or 'None' -> None."""
    if not text or text.strip().lower() == "none":
        return None
    set_name, sep, action = text.partition("/")
    if not sep or not set_name.strip() or not action.strip():
        raise ValueError("Write the action as  Set name/Action name")
    return set_name.strip(), action.strip()


def run_edit(layout: CoverageLayout, cfg: Config, style: str | None = None,
             action: tuple[str, str] | None = None, input_dir: Path | str | None = None,
             exposure: bool | None = None, preview: int = 0, existing: str = "ask",
             progress_cb: ProgressCb = noop_progress, log_cb: LogCb = noop_log,
             cancel: CancelToken | None = None, ps=None, detector=None) -> StepResult:
    """Edit the input folder (default Selected) into Edited.

    preview:  stop after this many newly edited photos (0 = no preview);
              running again continues with the rest.
    existing: when photos were already edited with DIFFERENT settings
      "ask"       -> do nothing, return data["needs_decision"]=True
      "reprocess" -> delete those earlier outputs in Edited/ and start over
    """
    cancel = cancel or CancelToken()
    if existing not in ("ask", "reprocess"):
        return StepResult(False, f"Unknown option for earlier edits: {existing}")
    exposure = cfg.exposure_correction.enabled if exposure is None else exposure
    src = Path(input_dir).resolve() if input_dir else layout.selected
    if not src.is_dir():
        return StepResult(False, f"Input folder not found: {src}")
    try:
        check_output_safe(src, layout.edited)
        if layout.full_coverage.is_dir():
            check_output_safe(layout.full_coverage, layout.edited)
    except SafetyError as exc:
        return StepResult(False, str(exc))
    try:
        style_path = resolve_style(cfg, style)
    except FileNotFoundError as exc:
        return StepResult(False, f"{exc} Put a .psd in the Styles folder and choose it.")

    files = list_jpegs(src)
    if not files:
        return StepResult(True, f"No photos found in {src.name}; nothing to do.")

    st = style_path.stat()
    params = {"input": state_key(src, layout.main), "style": style_path.name,
              "style_sig": [st.st_size, st.st_mtime_ns],
              "action": "/".join(action) if action else None,
              "jpeg_quality": cfg.photoshop.jpeg_quality,
              "exposure": asdict(cfg.exposure_correction) if exposure else False}
    state = State(layout.state)
    if state.params_changed("edit", params):
        old_outputs = state.step("edit").get("outputs", {})
        if existing == "ask":
            return StepResult(False, f"{len(old_outputs)} photo(s) were already edited with "
                              "different settings (style, action or exposure). "
                              "Choose to edit them again.",
                              data={"needs_decision": True, "existing": len(old_outputs)})
        for rel in old_outputs.values():
            f = layout.main / rel
            if f.parent.resolve() == layout.edited.resolve():   # only our own outputs
                f.unlink(missing_ok=True)
                _say(log_cb, f"Removed old {rel}")
    state.start_step("edit", params)
    step = state.step("edit")
    outputs: dict = step.setdefault("outputs", {})
    state.data["steps"]["edit"]["style"] = style_path.stem
    state.save()

    pending = [f for f in files if not state.is_done("edit", state_key(f, layout.main))]
    if not pending:
        state.set_status("edit", "done")
        state.save()
        return StepResult(True, f"All {len(files)} photos were already edited.",
                          done=len(files), total=len(files))
    problems = preflight(layout, sum(f.stat().st_size for f in pending),
                         need_full_coverage=False)
    if problems:
        return StepResult(False, "Cannot continue: " + " ".join(problems))
    layout.edited.mkdir(exist_ok=True)

    own_ps = ps is None
    ps = ps or Photoshop(cfg.photoshop.retry_attempts)
    own_det = exposure and detector is None
    errors: list[str] = []
    done = cancelled = 0
    preview_stop = False
    try:
        try:
            ps.connect()
        except PhotoshopError as exc:
            return StepResult(False, str(exc))
        if own_det:
            detector = make_detector(cfg.detection.face_confidence, cfg.detection.max_eye_faces)
        with ps.no_dialogs():
            try:
                sdoc = ps.open_style(style_path)
                _say(log_cb, f"Style {style_path.name}: {sdoc.layer_count} layer(s)")
                if action:
                    _check_action(ps, cfg, action)
            except PhotoshopError as exc:
                return StepResult(False, str(exc))
            try:
                for i, f in enumerate(pending):
                    if cancel.is_set():
                        cancelled = True
                        break
                    if preview and done >= preview:
                        preview_stop = True
                        break
                    progress_cb(i, len(pending), f.name)
                    if not ps.is_alive():                 # user closed Photoshop mid-run
                        _say(log_cb, "Photoshop was closed; opening it again", logging.WARNING)
                        ps.ensure()
                        sdoc = ps.open_style(style_path)
                    ok = _edit_one(f, layout, cfg, ps, sdoc, action, exposure, detector,
                                   state, outputs, errors, log_cb)
                    if not ok and not ps.is_alive():      # closed WHILE editing: retry once
                        _say(log_cb, "Photoshop was closed; opening it again", logging.WARNING)
                        ps.ensure()
                        sdoc = ps.open_style(style_path)
                        errors.pop()
                        ok = _edit_one(f, layout, cfg, ps, sdoc, action, exposure, detector,
                                       state, outputs, errors, log_cb)
                    done += ok
                    state.save()
            finally:
                sdoc.close()
    except PhotoshopError as exc:
        errors.append(str(exc))
        _say(log_cb, f"ERROR: {exc}", logging.ERROR)
    finally:
        if own_det and hasattr(detector, "close"):
            detector.close()
        if own_ps:
            ps.close()                                    # Photoshop itself stays open
    progress_cb(len(pending), len(pending), "")

    remaining = sum(1 for f in files if not state.is_done("edit", state_key(f, layout.main)))
    status = "done" if remaining == 0 else "partial"
    state.set_status("edit", status)
    state.save()
    total = len(files)
    finished = total - remaining
    if preview_stop:
        return StepResult(False, f"Preview ready: {done} photo(s) in Edited. Check them, then "
                          f"continue to edit the other {remaining}.", False, finished, total,
                          errors, {"preview_done": True, "remaining": remaining})
    if cancelled:
        return StepResult(False, f"Stopped. Edited {finished} of {total}; run again to continue.",
                          True, finished, total, errors)
    if errors:
        return StepResult(False, f"Edited {finished} of {total}; {len(errors)} failed "
                          "(see the log). Run again to retry them.", False, finished, total, errors)
    return StepResult(True, f"Edited {total} of {total} with {style_path.stem}.",
                      False, total, total)


def _check_action(ps, cfg: Config, action: tuple[str, str]) -> None:
    set_name, act = action
    for atn in list_action_files(cfg):
        try:
            if read_atn_set_name(atn) == set_name:
                names = ps.load_actions(atn, set_name)
                break
        except ValueError:
            continue
    else:
        names = ps.loaded_action_sets().get(set_name)
        if names is None:
            raise PhotoshopError(f"Action set '{set_name}' not found: put its .atn file in "
                                 "the Styles folder.")
    if act not in names:
        raise PhotoshopError(f"Action '{act}' is not in set '{set_name}' "
                             f"(it has: {', '.join(names) or 'no actions'}).")


def _edit_one(f: Path, layout, cfg, ps, sdoc, action, exposure, detector, state,
              outputs, errors, log_cb) -> bool:
    key = state_key(f, layout.main)
    dst = unique_path(layout.edited / f.name)
    check_not_in_source(dst, layout)
    try:
        if exposure:
            r = correct_exposure(f, dst, cfg, detector)
            note = f"exposure {r.stops:+.2f} stop" if r.changed else "exposure OK"
        else:
            shutil.copy2(f, dst)
            note = "no exposure fix"
        ps.apply(sdoc, dst, cfg.photoshop.jpeg_quality, action)
        rel = relative_name(dst, layout.main)
        outputs[key] = rel
        state.mark_done("edit", key)
        _say(log_cb, f"Edited {f.name} -> {rel} ({note})")
        return True
    except Exception as exc:
        dst.unlink(missing_ok=True)                       # our own half-made working copy
        msg = str(exc) if isinstance(exc, PhotoshopError) else f"{type(exc).__name__}: {exc}"
        state.mark_failed("edit", key, msg)
        errors.append(f"{f.name}: {msg}")
        _say(log_cb, f"ERROR editing {f.name}: {msg}", logging.ERROR)
        return False
