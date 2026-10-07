"""Manual review of the picks: move photos between Selected and Alternates (plan 6.6).

Safety rule 4: a move is copy -> verify -> delete our own copy. Only files that
sit directly inside Selected/ or Alternates/ can be moved; Full_Coverage is
never touched. report.csv and state.json are kept in step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .logsetup import get_logger
from .paths import (CoverageLayout, SafetyError, list_jpegs, relative_name, safe_copy,
                    state_key)
from .report import read_report, write_report
from .state import State, peek

KINDS = ("selected", "alternate")
USER_PICK = "chosen_by_user"
NOT_BEST = "not_best_in_group"


@dataclass
class PickItem:
    path: Path
    kind: str                                  # "selected" | "alternate"
    source_name: str                           # file name in Full_Coverage (report key)
    row: dict = field(default_factory=dict)    # report.csv row ({} for files added by hand)

    @property
    def group_id(self) -> str:
        return str(self.row.get("group_id", ""))


@dataclass
class Review:
    selected: list[PickItem]
    alternates: list[PickItem]

    def alternates_of(self, item: PickItem) -> list[PickItem]:
        if not item.group_id:
            return []
        return [a for a in self.alternates if a.group_id == item.group_id]

    def selected_of(self, item: PickItem) -> list[PickItem]:
        if not item.group_id:
            return []
        return [s for s in self.selected if s.group_id == item.group_id]


def _folder_of(layout: CoverageLayout, kind: str) -> Path:
    return layout.selected if kind == "selected" else layout.alternates


def _source_names(layout: CoverageLayout) -> dict[str, str]:
    """{output relative path: file name in the input folder}, from the cull state."""
    outs = peek(layout.state)["steps"]["cull"].get("outputs", {})
    return {rel: Path(key).name for key, rel in outs.items()}


def load_review(layout: CoverageLayout) -> Review:
    """Selected and Alternates files joined with their report.csv rows."""
    rows: dict[str, dict] = {}
    if layout.report.is_file():
        try:
            rows = {r["filename"]: r for r in read_report(layout.report)}
        except (OSError, KeyError):
            rows = {}
    src = _source_names(layout)

    def build(kind: str) -> list[PickItem]:
        folder = _folder_of(layout, kind)
        if not folder.is_dir():
            return []
        items = []
        for p in list_jpegs(folder):
            name = src.get(relative_name(p, layout.main), p.name)
            items.append(PickItem(p, kind, name, rows.get(name, {})))
        items.sort(key=lambda i: (i.row.get("capture_time", "~"), i.path.name.lower()))
        return items

    return Review(build("selected"), build("alternate"))


def move_pick(layout: CoverageLayout, path: Path | str, to: str) -> Path:
    """Move one photo between Selected and Alternates. Returns its new path."""
    if to not in KINDS:
        raise ValueError(f"Unknown target: {to}")
    path = Path(path).resolve()
    src_dir, dst_dir = ((layout.alternates, layout.selected) if to == "selected"
                        else (layout.selected, layout.alternates))
    if path.parent != src_dir.resolve() or not path.is_file():
        raise SafetyError(f"{path.name} is not in {src_dir.name}; nothing was moved.")
    old_rel = relative_name(path, layout.main)
    new = safe_copy(path, dst_dir, layout)          # verified copy first
    path.unlink()                                   # then remove our own copy
    get_logger().info("Moved %s -> %s", old_rel, relative_name(new, layout.main))
    _update_records(layout, old_rel, relative_name(new, layout.main), to)
    return new


def swap_pick(layout: CoverageLayout, selected: Path | str, alternate: Path | str) -> tuple[Path, Path]:
    """Alternate becomes the pick, the old pick goes to Alternates. Returns (new pick, demoted)."""
    new_pick = move_pick(layout, alternate, "selected")
    demoted = move_pick(layout, selected, "alternate")
    return new_pick, demoted


def _update_records(layout: CoverageLayout, old_rel: str, new_rel: str, to: str) -> None:
    state = State(layout.state)
    cull = state.step("cull")
    outputs: dict = cull.get("outputs", {})
    name = None
    for key, rel in outputs.items():
        if rel == old_rel:
            outputs[key] = new_rel
            name = Path(key).name
    if name is None:
        name = Path(old_rel).name

    if layout.report.is_file():
        rows = read_report(layout.report)
        for r in rows:
            if r.get("filename") != name:
                continue
            toks = [t for t in r.get("reasons", "").split(";") if t]
            if to == "selected":
                r["result"] = "selected"
                toks = [t for t in toks if t not in (NOT_BEST, USER_PICK)] + [USER_PICK]
            else:
                r["result"] = "alternate"
                toks = [t for t in toks if t not in (NOT_BEST, USER_PICK)] + [NOT_BEST]
            r["reasons"] = ";".join(toks)
        write_report(layout.report, rows)
        summary = cull.get("summary")
        if summary is not None:
            summary["selected"] = sum(r.get("result") == "selected" for r in rows)
            summary["alternates"] = sum(r.get("result") == "alternate" for r in rows)
    state.save()


def stale_edits(layout: CoverageLayout, names: list[str]) -> list[str]:
    """Files in Edited/ that were made from photos that are no longer picked (for a warning)."""
    outs = peek(layout.state)["steps"]["edit"].get("outputs", {})
    found = []
    for n in names:
        rel = outs.get(f"Selected/{n}")
        if rel and (layout.main / rel).is_file():
            found.append(rel)
    return found


def drop_edits(layout: CoverageLayout, names: list[str]) -> list[str]:
    """Delete our Edited/ copies made from these photos and forget them in state.json,
    so a later Edit run makes them again. Only files directly inside Edited/."""
    state = State(layout.state)
    edit = state.step("edit")
    outs: dict = edit.get("outputs", {})
    removed = []
    for n in names:
        key = f"Selected/{n}"
        rel = outs.pop(key, None)
        if key in edit.get("done_files", []):
            edit["done_files"].remove(key)
        if rel:
            f = layout.main / rel
            if f.parent.resolve() == layout.edited.resolve() and f.is_file():
                f.unlink()
                removed.append(rel)
                get_logger().info("Removed %s (its photo is no longer picked)", rel)
    if removed and edit.get("status") == "done":
        edit["status"] = "partial"
    state.save()
    return removed
