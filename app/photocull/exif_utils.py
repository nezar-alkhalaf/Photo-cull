"""Capture time from EXIF (with sub-second precision) and a safe fallback."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

_EXIF_IFD = 0x8769
_DATETIME_ORIGINAL = 36867
_SUBSEC_ORIGINAL = 37521
_DATETIME = 306


def read_exif_datetime(path: Path | str) -> datetime | None:
    """DateTimeOriginal (+SubSecTimeOriginal) as a datetime, or None."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            ifd = exif.get_ifd(_EXIF_IFD)
            raw = ifd.get(_DATETIME_ORIGINAL) or exif.get(_DATETIME)
            if not raw:
                return None
            dt = datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S")
            sub = str(ifd.get(_SUBSEC_ORIGINAL, "")).strip().strip("\x00")
            if sub.isdigit():
                dt += timedelta(seconds=int(sub) / 10 ** len(sub))
            return dt
    except Exception:
        return None


def capture_time(path: Path | str) -> tuple[datetime, bool]:
    """(time, from_exif). Falls back to the file's modified time."""
    dt = read_exif_datetime(path)
    if dt is not None:
        return dt, True
    return datetime.fromtimestamp(Path(path).stat().st_mtime), False


def sort_by_capture_time(paths: list[Path]) -> tuple[list[Path], int]:
    """Sort by capture time then filename. Returns (sorted, count_without_exif)."""
    keyed = []
    missing = 0
    for p in paths:
        t, ok = capture_time(p)
        missing += not ok
        keyed.append((t, p.name.lower(), p))
    keyed.sort(key=lambda k: (k[0], k[1]))
    return [k[2] for k in keyed], missing
