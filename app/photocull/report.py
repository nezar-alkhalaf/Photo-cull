"""report.csv: one row per photo with scores and reasons (plan section 6.5)."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

COLUMNS = ["filename", "capture_time", "group_id", "group_size", "rank_in_group",
           "sharpness", "mean_brightness", "blown_pct", "crushed_pct", "contrast",
           "face_found", "eyes_state", "total_score", "result", "reasons", "weak_group"]
RESULTS = ("selected", "alternate", "not_selected", "unreadable")


def write_report(path: Path | str, rows: list[dict]) -> None:
    """Write atomically. UTF-8 with BOM so Excel shows Arabic names correctly."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in COLUMNS})
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_report(path: Path | str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))
