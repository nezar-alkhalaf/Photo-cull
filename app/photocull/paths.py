"""Folder layout, safety checks and collision-free file copying.

Safety rules enforced here (plan section 12):
  * Full_Coverage is never written to.
  * Output folders may not equal, contain, or sit inside the source folder.
  * Existing files are never overwritten (numeric suffix instead).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = APP_DIR.parent          # the PhotoCull project folder
LOGS_DIR = APP_DIR / "logs"

JPEG_EXTS = {".jpg", ".jpeg"}


class SafetyError(RuntimeError):
    """A requested operation would violate a hard safety rule."""


SOURCE_FILE = "photos_folder.txt"     # inside a work folder: where the photos really are


@dataclass(frozen=True)
class CoverageLayout:
    """Folder layout of one coverage (plan section 5).

    Normally the photos are in main/Full_Coverage. When the user picked any folder that
    simply holds photos, `source` is that folder and `main` is a new work folder next to it
    that holds the results (the photos folder itself is never written to).
    """

    main: Path
    source: Path | None = None

    @classmethod
    def for_main(cls, main: Path | str, source: Path | str | None = None) -> "CoverageLayout":
        main = Path(main).resolve()
        if source is None:
            try:
                text = (main / SOURCE_FILE).read_text(encoding="utf-8").strip()
                source = text or None
            except OSError:
                source = None
        return cls(main, Path(source).resolve() if source else None)

    @classmethod
    def for_photos_folder(cls, photos: Path | str) -> "CoverageLayout":
        """Layout for a plain folder of photos: results go to '<name> - PhotoCull' beside it."""
        photos = Path(photos).resolve()
        return cls(photos.parent / f"{photos.name} - PhotoCull", photos)

    def remember_source(self) -> None:
        """Write the pointer file so the work folder can be opened again by itself."""
        if self.source is not None:
            self.main.mkdir(exist_ok=True)
            (self.main / SOURCE_FILE).write_text(str(self.source), encoding="utf-8")

    @property
    def full_coverage(self) -> Path:
        return self.source or self.main / "Full_Coverage"
    @property
    def selected(self) -> Path: return self.main / "Selected"
    @property
    def alternates(self) -> Path: return self.main / "Alternates"
    @property
    def edited(self) -> Path: return self.main / "Edited"
    @property
    def posts(self) -> Path: return self.main / "Posts"
    @property
    def report(self) -> Path: return self.main / "report.csv"
    @property
    def state(self) -> Path: return self.main / "state.json"
    @property
    def log(self) -> Path: return self.main / "photocull.log"

    @property
    def output_dirs(self) -> tuple[Path, ...]:
        return (self.selected, self.alternates, self.edited, self.posts)

    def ensure(self) -> None:
        """Create the output folders. Full_Coverage must already exist."""
        if not self.full_coverage.is_dir():
            raise SafetyError(
                f"The photos folder was not found: {self.full_coverage}. "
                "Choose it again."
            )
        for d in self.output_dirs:
            check_output_safe(self.full_coverage, d)
            d.mkdir(exist_ok=True)


def is_within(child: Path | str, parent: Path | str) -> bool:
    """True if child equals or sits inside parent (real, resolved paths)."""
    c = Path(child).resolve()
    p = Path(parent).resolve()
    return c == p or p in c.parents


def check_output_safe(source: Path | str, output: Path | str) -> None:
    """Refuse if output equals, contains, or sits inside source."""
    if is_within(output, source):
        raise SafetyError(f"Output folder {output} is inside the source folder {source}.")
    if is_within(source, output):
        raise SafetyError(f"Output folder {output} contains the source folder {source}.")


def check_not_in_source(target: Path | str, layout: CoverageLayout) -> None:
    """Guard for any write: the target may never be inside Full_Coverage."""
    if is_within(target, layout.full_coverage):
        raise SafetyError(f"Refusing to write inside Full_Coverage: {target}")


def unique_path(dest: Path | str) -> Path:
    """Return dest, or dest with _2, _3... before the extension if it exists."""
    dest = Path(dest)
    if not dest.exists():
        return dest
    n = 2
    while True:
        cand = dest.with_name(f"{dest.stem}_{n}{dest.suffix}")
        if not cand.exists():
            return cand
        n += 1


def safe_copy(src: Path | str, dst_dir: Path | str, layout: CoverageLayout | None = None) -> Path:
    """Copy src into dst_dir without overwriting; verify size; return new path."""
    src, dst_dir = Path(src), Path(dst_dir)
    if layout is not None:
        check_not_in_source(dst_dir, layout)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = unique_path(dst_dir / src.name)
    shutil.copy2(src, dst)
    if dst.stat().st_size != src.stat().st_size:
        dst.unlink(missing_ok=True)
        raise OSError(f"Copy verification failed for {src.name}")
    return dst


def list_jpegs(folder: Path | str) -> list[Path]:
    """JPEG files directly inside folder, sorted by name (case-insensitive)."""
    folder = Path(folder)
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in JPEG_EXTS),
        key=lambda p: p.name.lower(),
    )


def relative_name(path: Path | str, main: Path | str) -> str:
    """Path relative to the coverage folder, forward slashes (portable state)."""
    return Path(path).resolve().relative_to(Path(main).resolve()).as_posix()


def state_key(path: Path | str, main: Path | str) -> str:
    """relative_name() when path is inside the coverage folder, else the absolute path."""
    try:
        return relative_name(path, main)
    except ValueError:
        return Path(path).resolve().as_posix()


def preflight(layout: CoverageLayout, needed_bytes: int = 0,
              need_full_coverage: bool = True) -> list[str]:
    """Return a list of problems (empty = OK): folders, write access, free space."""
    problems: list[str] = []
    if not layout.main.is_dir():
        return [f"Main folder does not exist: {layout.main}"]
    if need_full_coverage and not layout.full_coverage.is_dir():
        problems.append("Full_Coverage folder is missing.")
    if not os.access(layout.main, os.W_OK):
        problems.append(f"No write permission in {layout.main}")
    free = shutil.disk_usage(layout.main).free
    if needed_bytes and free < needed_bytes * 1.5:
        problems.append(
            f"Not enough free disk space: need about {int(needed_bytes * 1.5) // 2**20} MB, "
            f"have {free // 2**20} MB."
        )
    return problems
