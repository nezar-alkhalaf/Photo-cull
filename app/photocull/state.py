"""state.json: progress for resume. Written atomically, relative filenames only."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

STATE_VERSION = 1
STEPS = ("cull", "edit", "split")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "mode": "portrait",
        "steps": {s: {"status": "not_started"} for s in STEPS},
    }


def atomic_write_json(path: Path | str, data: Any) -> None:
    """Write to a temp file in the same folder, then rename over the target."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class State:
    """Thin wrapper over state.json. Call save() to persist."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.data = _default()
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if loaded.get("version") == STATE_VERSION and "steps" in loaded:
                    self.data = loaded
                    for s in STEPS:
                        self.data["steps"].setdefault(s, {"status": "not_started"})
            except (json.JSONDecodeError, OSError):
                # Corrupt file: keep it for inspection, start fresh.
                self.path.replace(self.path.with_suffix(".json.corrupt"))

    def save(self) -> None:
        atomic_write_json(self.path, self.data)

    def step(self, name: str) -> dict[str, Any]:
        return self.data["steps"][name]

    def set_status(self, name: str, status: str, **extra: Any) -> None:
        st = self.step(name)
        st["status"] = status
        st.update(extra)
        if status == "done":
            st["finished_at"] = _now()

    def start_step(self, name: str, params: dict[str, Any]) -> None:
        """Begin (or continue) a step. Resets progress if params changed."""
        st = self.step(name)
        if st.get("params") != params:
            self.data["steps"][name] = {"status": "running", "params": params,
                                        "done_files": [], "failed_files": {}}
        else:
            st["status"] = "running"
            st.setdefault("done_files", [])
            st.setdefault("failed_files", {})

    def params_changed(self, name: str, params: dict[str, Any]) -> bool:
        """True if the step has saved progress made with different parameters."""
        st = self.step(name)
        return bool(st.get("done_files")) and st.get("params") != params

    def is_done(self, name: str, rel_file: str) -> bool:
        return rel_file in self.step(name).get("done_files", [])

    def mark_done(self, name: str, rel_file: str) -> None:
        st = self.step(name)
        st.setdefault("done_files", [])
        if rel_file not in st["done_files"]:
            st["done_files"].append(rel_file)
        st.get("failed_files", {}).pop(rel_file, None)

    def mark_failed(self, name: str, rel_file: str, reason: str) -> None:
        self.step(name).setdefault("failed_files", {})[rel_file] = reason
