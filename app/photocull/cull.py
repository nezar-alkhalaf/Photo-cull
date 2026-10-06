"""Step 1: pick the best frame of each burst (plan section 6).

Phases:
  1. analyse  - one photo at a time; raw measurements are cached in state.json,
                so Stop + re-run continues where it stopped, and re-scoring with
                new weights (another mode, tuning) skips the analysis entirely.
  2. score    - group bursts by time gap, rank frames inside each group.
  3. output   - copy the best frame to Selected, the next ones to Alternates,
                write report.csv. Full_Coverage is only ever read.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from PIL import UnidentifiedImageError

from .config import Config
from .exif_utils import capture_time
from .faces import FaceDetector, make_detector
from .grouping import group_by_gap
from .logsetup import get_logger
from .paths import (CoverageLayout, SafetyError, check_not_in_source, check_output_safe,
                    list_jpegs, preflight, relative_name, safe_copy, state_key)
from .report import write_report
from .runner import CancelToken, LogCb, ProgressCb, StepResult, noop_log, noop_progress
from .scoring import (MODES, REASONS, Measurement, is_weak, load_analysis_image, measure,
                      rank_group, score_group)
from .state import State

SAVE_EVERY = 10
MEASURE_VERSION = 3          # bump when measure() changes, to drop cached measurements


def _say(log_cb: LogCb, msg: str, level: int = logging.INFO) -> None:
    get_logger().log(level, msg)
    log_cb(msg)


def existing_outputs(layout: CoverageLayout) -> list[Path]:
    """JPEGs already in Selected / Alternates (a previous cull run)."""
    return [p for d in (layout.selected, layout.alternates) if d.is_dir()
            for p in list_jpegs(d)]


def run_cull(layout: CoverageLayout, cfg: Config, mode: str = "portrait",
             input_dir: Path | str | None = None, existing: str = "ask",
             progress_cb: ProgressCb = noop_progress, log_cb: LogCb = noop_log,
             cancel: CancelToken | None = None,
             detector: FaceDetector | None = None) -> StepResult:
    """Cull the input folder (default Full_Coverage) into Selected/Alternates.

    existing: what to do when Selected/Alternates already hold photos
      "ask"   -> do nothing, return ok=False with data["needs_decision"]=True
      "clear" -> delete the JPEGs directly inside Selected and Alternates
                 (only after the analysis finished), then write the new picks
    """
    cancel = cancel or CancelToken()
    if mode not in MODES:
        return StepResult(False, f"Unknown mode '{mode}' (use {' or '.join(MODES)}).")
    if existing not in ("ask", "clear"):
        return StepResult(False, f"Unknown option for existing photos: {existing}")
    src = Path(input_dir).resolve() if input_dir else layout.full_coverage
    if not src.is_dir():
        return StepResult(False, f"Input folder not found: {src}")
    try:
        for d in (layout.selected, layout.alternates):
            check_output_safe(src, d)
            if layout.full_coverage.is_dir():
                check_output_safe(layout.full_coverage, d)
    except SafetyError as exc:
        return StepResult(False, str(exc))
    problems = preflight(layout, need_full_coverage=input_dir is None)
    if problems:
        return StepResult(False, "Cannot continue: " + " ".join(problems))

    files = list_jpegs(src)
    if not files:
        return StepResult(True, f"No photos found in {src.name}; nothing to do.")

    old = existing_outputs(layout)
    if old and existing == "ask":
        return StepResult(False, f"Selected/Alternates already hold {len(old)} photo(s) "
                          "from an earlier run. Choose to clear them first.",
                          data={"needs_decision": True, "existing": len(old)})

    det = cfg.detection
    own_detector = detector is None
    if own_detector:
        detector = make_detector(det.face_confidence, det.max_eye_faces)
    try:
        an, cancelled = _analyse(layout, cfg, src, files, detector,
                                 progress_cb, log_cb, cancel)
    finally:
        if own_detector and hasattr(detector, "close"):
            detector.close()
    if cancelled:
        an.state.set_status("cull", "partial")
        an.state.save()
        return StepResult(False, f"Stopped. Analysed {an.analysed} of {len(files)}; "
                          "run again to continue.", True, an.analysed, len(files))

    return _score_and_copy(layout, cfg, mode, input_dir, existing, old, an,
                           progress_cb, log_cb, cancel)


class _Analysis:
    def __init__(self, state: State):
        self.state = state
        self.items: list[tuple[Path, datetime, Measurement | None]] = []  # time order
        self.analysed = 0


def _analyse(layout, cfg, src, files, detector, progress_cb, log_cb, cancel):
    det = cfg.detection
    params = {"measure_version": MEASURE_VERSION,
              "input": state_key(src, layout.main), "detector": detector.name,
              "analysis_max_side": det.analysis_max_side,
              "min_face_fraction": det.min_face_fraction,
              "face_confidence": det.face_confidence, "max_eye_faces": det.max_eye_faces}
    state = State(layout.state)
    state.start_step("cull", params)
    cache: dict = state.step("cull").setdefault("measurements", {})
    out = _Analysis(state)

    timed, no_exif = [], 0
    for p in files:
        t, from_exif = capture_time(p)
        no_exif += not from_exif
        timed.append((t, p.name.lower(), p))
    timed.sort(key=lambda k: (k[0], k[1]))
    if no_exif:
        _say(log_cb, f"Warning: {no_exif} of {len(files)} photo(s) have no EXIF capture "
             "time; using the file date instead (bursts may group less accurately).",
             logging.WARNING)
    if detector.name == "none":
        _say(log_cb, "Warning: face detection is off; sharpness is measured on the frame "
             "centre and eyes are not judged.", logging.WARNING)

    keys = set()
    total = len(timed)
    cancelled = False
    for i, (t, _, p) in enumerate(timed):
        if cancel.is_set():
            cancelled = True
            break
        progress_cb(i, total, p.name)
        key = state_key(p, layout.main)
        keys.add(key)
        st = p.stat()
        sig = [st.st_size, st.st_mtime_ns]
        hit = cache.get(key)
        if hit is None or hit.get("sig") != sig:
            try:
                rgb = load_analysis_image(p, det.analysis_max_side)
                m = measure(rgb, detector, det)
                del rgb
                hit = cache[key] = {"sig": sig, "m": m.to_dict()}
                state.mark_done("cull", key)
            except Exception as exc:
                why = ("not a readable image" if isinstance(exc, UnidentifiedImageError)
                       else f"{type(exc).__name__}: {exc}")
                hit = cache[key] = {"sig": sig, "error": why}
                state.mark_failed("cull", key, hit["error"])
                _say(log_cb, f"Unreadable, skipped: {p.name} ({hit['error']})",
                     logging.WARNING)
            if (i + 1) % SAVE_EVERY == 0:
                state.save()
        out.analysed += 1
        out.items.append((p, t, Measurement.from_dict(hit["m"]) if "m" in hit else None))
    if not cancelled:
        progress_cb(total, total, "")
        for stale in set(cache) - keys:          # photos removed since the last run
            del cache[stale]
    state.save()
    return out, cancelled


def _row(p: Path, t, **kw) -> dict:
    return {"filename": p.name, "capture_time": t.isoformat(timespec="milliseconds"), **kw}


def _score_and_copy(layout, cfg, mode, input_dir, existing, old, an: _Analysis,
                    progress_cb, log_cb, cancel) -> StepResult:
    det, g = cfg.detection, cfg.grouping
    wts = getattr(cfg.scoring, mode)
    state = an.state

    readable = [(p, t, m) for p, t, m in an.items if m is not None]
    groups = group_by_gap(readable, [t for _, t, _ in readable],
                          g.action_group_gap_seconds if mode == "action"
                          else g.group_gap_seconds, g.max_group_size)
    rows: dict[Path, dict] = {}
    picks: list[tuple[Path, Path]] = []            # (source, output folder)
    weak_groups = skipped_groups = 0
    for gid, grp in enumerate(groups, start=1):
        ms = [m for _, _, m in grp]
        scores = score_group(ms, wts, det)
        order = rank_group(ms, scores)
        weak = is_weak(ms[order[0]], scores[order[0]], det)
        weak_groups += weak
        # User decision (overrides plan 6.4 "every group yields a Selected"):
        # when even the sharpest frame is blurry, the moment is dropped.
        skip = g.skip_blurry_groups and ms[order[0]].sharpness < det.blurry_sharpness
        skipped_groups += skip
        for rank, idx in enumerate(order, start=1):
            p, t, m = grp[idx]
            s = scores[idx]
            if skip:
                result, dest = ("skipped" if rank == 1 else "not_selected"), None
            elif rank == 1:
                result, dest = "selected", layout.selected
            elif rank <= 1 + g.keep_alternates and m.sharpness >= det.blurry_sharpness:
                # a black frame (flash did not fire) or a badly blurred one is
                # never worth an Alternates slot
                result, dest = "alternate", layout.alternates
            else:
                result, dest = "not_selected", None
            if dest is not None:
                picks.append((p, dest))
            reasons = s.reasons + (["not_best_in_group"] if rank > 1 else [])
            rows[p] = _row(p, t, group_id=gid, group_size=len(grp), rank_in_group=rank,
                           sharpness=m.sharpness, mean_brightness=m.mean_brightness,
                           blown_pct=m.blown_pct, crushed_pct=m.crushed_pct,
                           contrast=m.contrast, face_found="yes" if m.face_count else "no",
                           eyes_state=s.eyes_state, total_score=s.total, result=result,
                           reasons=";".join(reasons), weak_group="yes" if weak else "no")
    for p, t, m in an.items:
        if m is None:
            rows[p] = _row(p, t, result="unreadable", reasons="unreadable", weak_group="")

    needed = sum(p.stat().st_size for p, _ in picks)
    problems = preflight(layout, needed, need_full_coverage=input_dir is None)
    if problems:
        state.set_status("cull", "partial")
        state.save()
        return StepResult(False, "Cannot continue: " + " ".join(problems))

    if old and existing == "clear":
        for f in old:
            # Only files directly inside our two output folders, never the source.
            check_not_in_source(f, layout)
            if f.parent.resolve() not in (layout.selected.resolve(),
                                          layout.alternates.resolve()):
                raise SafetyError(f"Refusing to delete outside Selected/Alternates: {f}")
            f.unlink(missing_ok=True)
            _say(log_cb, f"Removed old {relative_name(f, layout.main)}")

    for d in (layout.selected, layout.alternates):
        d.mkdir(exist_ok=True)
    outputs: dict[str, str] = {}
    errors: list[str] = []
    cancelled = False
    for k, (p, dest) in enumerate(picks):
        if cancel.is_set():
            cancelled = True
            break
        progress_cb(k, len(picks), p.name)
        try:
            dst = safe_copy(p, dest, layout)
            outputs[state_key(p, layout.main)] = relative_name(dst, layout.main)
            _say(log_cb, f"Copied {p.name} -> {relative_name(dst, layout.main)}")
        except Exception as exc:
            errors.append(f"{p.name}: {exc}")
            _say(log_cb, f"ERROR copying {p.name}: {exc}", logging.ERROR)
    progress_cb(len(picks), len(picks), "")

    ordered_rows = [rows[p] for p, _, _ in an.items]
    write_report(layout.report, ordered_rows)

    counts = Counter(r["result"] for r in ordered_rows)
    rejected = Counter(tok for r in ordered_rows if r["result"] == "not_selected"
                       for tok in r["reasons"].split(";") if tok)
    summary = {
        "total": len(ordered_rows), "groups": len(groups),
        "selected": counts["selected"], "alternates": counts["alternate"],
        "not_selected": counts["not_selected"], "unreadable": counts["unreadable"],
        "weak_groups": weak_groups, "skipped_groups": skipped_groups,
        "rejected_by_reason": {r: rejected[r] for r in REASONS},
    }
    ok = not errors and not cancelled
    state.data["mode"] = mode
    state.set_status("cull", "done" if ok else "partial", outputs=outputs,
                     summary=summary,
                     score_params={"mode": mode, "grouping": asdict(g),
                                   "weights": asdict(wts), "detection": asdict(det)})
    state.save()

    msg = (f"{summary['total']} photos, {summary['groups']} groups: "
           f"{summary['selected']} selected, {summary['alternates']} alternates"
           + (f", {summary['unreadable']} unreadable" if summary["unreadable"] else "")
           + (f", {skipped_groups} blurry group(s) skipped" if skipped_groups else "")
           + (f", {weak_groups - skipped_groups} weak group(s) to review"
              if weak_groups > skipped_groups else "") + ".")
    if cancelled:
        msg = f"Stopped while copying. {msg} Output is incomplete; run again."
    elif errors:
        msg += f" {len(errors)} copy error(s); check the log."
    return StepResult(ok, msg, cancelled, len(outputs), len(picks), errors, summary)
