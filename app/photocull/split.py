"""Step 3: split photos into Instagram post folders (plan section 8)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .exif_utils import sort_by_capture_time
from .logsetup import get_logger
from .paths import (CoverageLayout, SafetyError, check_output_safe, list_jpegs,
                    relative_name, safe_copy)
from .runner import CancelToken, LogCb, ProgressCb, StepResult, noop_log, noop_progress
from .state import State

POST_RE = re.compile(r"^Post_(\d+)$")


def _say(log_cb: LogCb, msg: str) -> None:
    get_logger().info(msg)
    log_cb(msg)


def distribute(total: int, max_per_post: int) -> list[int]:
    """Sizes of each post: as even as possible, larger posts first."""
    if max_per_post < 1:
        raise ValueError("max_per_post must be at least 1")
    if total < 0:
        raise ValueError("total must not be negative")
    if total == 0:
        return []
    posts = -(-total // max_per_post)          # ceil
    base, extra = divmod(total, posts)
    return [base + 1] * extra + [base] * (posts - extra)


def plan_split(files: list, max_per_post: int) -> list[list]:
    """Contiguous chunks of files following distribute()."""
    chunks, i = [], 0
    for size in distribute(len(files), max_per_post):
        chunks.append(files[i:i + size])
        i += size
    return chunks


def existing_posts(posts_dir: Path) -> list[Path]:
    """Post_N folders inside posts_dir, sorted by number."""
    if not posts_dir.is_dir():
        return []
    found = [(int(m.group(1)), p) for p in posts_dir.iterdir()
             if p.is_dir() and (m := POST_RE.match(p.name))]
    return [p for _, p in sorted(found)]


def run_split(layout: CoverageLayout, max_per_post: int = 20,
              input_dir: Path | str | None = None, existing: str = "ask",
              progress_cb: ProgressCb = noop_progress, log_cb: LogCb = noop_log,
              cancel: CancelToken | None = None) -> StepResult:
    """Copy the input JPEGs into Posts/Post_1.. .

    existing: what to do when Posts/ already holds Post_N folders
      "ask"   -> do nothing, return ok=False with data["needs_decision"]=True
      "clear" -> delete only those Post_N folders, then start at Post_1
      "new"   -> keep them and number the new posts after the highest one
    """
    cancel = cancel or CancelToken()
    if max_per_post < 1:
        return StepResult(False, "Max images per post must be at least 1.")
    src = Path(input_dir) if input_dir else layout.edited
    if not src.is_dir():
        return StepResult(False, f"Input folder not found: {src}")
    try:
        check_output_safe(src, layout.posts)
        check_output_safe(layout.full_coverage, layout.posts)
    except SafetyError as exc:
        return StepResult(False, str(exc))

    files = list_jpegs(src)
    if not files:
        return StepResult(True, f"No photos found in {src.name}; nothing to do.")

    old = existing_posts(layout.posts)
    if old and existing == "ask":
        return StepResult(False, f"Posts already has {len(old)} post folder(s). "
                          "Choose to clear them or write new ones.",
                          data={"needs_decision": True, "existing": len(old)})
    if old and existing not in ("clear", "new"):
        return StepResult(False, f"Unknown option for existing posts: {existing}")

    files, missing = sort_by_capture_time(files)
    if missing:
        _say(log_cb, f"Warning: {missing} file(s) have no EXIF time; using file date.")

    if old and existing == "clear":
        for p in old:
            shutil.rmtree(p)
            _say(log_cb, f"Removed old {p.name}")
        start = 1
    elif old:
        start = int(POST_RE.match(old[-1].name).group(1)) + 1
    else:
        start = 1

    params = {"max_per_post": max_per_post, "input": src.name}
    state = State(layout.state)
    state.start_step("split", params)
    state.data["steps"]["split"]["done_files"] = []
    state.data["steps"]["split"]["failed_files"] = {}
    state.save()

    layout.posts.mkdir(exist_ok=True)
    chunks = plan_split(files, max_per_post)
    total, done, errors, sizes = len(files), 0, [], []
    cancelled = False
    for n, chunk in enumerate(chunks, start=start):
        folder = layout.posts / f"Post_{n}"
        copied = 0
        for f in chunk:
            if cancel.is_set():
                cancelled = True
                break
            progress_cb(done, total, f.name)
            try:
                dst = safe_copy(f, folder, layout)
                _say(log_cb, f"Copied {f.name} -> {relative_name(dst, layout.main)}")
                state.mark_done("split", relative_name(dst, layout.main))
                copied += 1
            except Exception as exc:
                errors.append(f"{f.name}: {exc}")
                state.mark_failed("split", f.name, str(exc))
                _say(log_cb, f"ERROR copying {f.name}: {exc}")
            done += 1
        sizes.append(copied)
        if cancelled:
            break
    progress_cb(done, total, "")

    copied_total = sum(sizes)
    verified = sum(len(list_jpegs(layout.posts / f"Post_{n}"))
                   for n in range(start, start + len(sizes)))
    ok = not errors and not cancelled and copied_total == total and verified == total
    status = "done" if ok else "partial"
    state.set_status("split", status, sizes=sizes)
    state.save()

    if cancelled:
        msg = f"Stopped. Copied {copied_total} of {total}."
    elif ok:
        msg = f"Copied {total} of {total} into {len(sizes)} post(s): " + "/".join(map(str, sizes))
    else:
        msg = f"Copied {copied_total} of {total}; verified {verified}. Check the log."
    return StepResult(ok, msg, cancelled, done, total, errors, {"sizes": sizes})
