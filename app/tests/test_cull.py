import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from photocull.config import Config
from photocull.cull import run_cull
from photocull.paths import CoverageLayout, list_jpegs
from photocull.report import COLUMNS, read_report
from photocull.runner import CancelToken
from synth import FakeDetector, blurred, flat, save_jpeg, texture, with_eyes

APP = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 3, 18, 30, 0)


def snapshot(folder: Path) -> dict:
    return {p.name: (hashlib.md5(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(folder.iterdir())}


def names(folder: Path) -> list[str]:
    return [p.name for p in list_jpegs(folder)]


@pytest.fixture
def coverage(tmp_path):
    """Arabic-named coverage with three bursts and one corrupt file.

    burst 1 (portrait-style): B1_a closed eyes + sharpest, B1_b open + a bit soft,
                              B1_c open + soft, B1_d open + very soft
    burst 2: two sharpness levels, no face
    burst 3: flat grey frames -> weak group (blurry, low contrast)
    """
    lay = CoverageLayout.for_main(tmp_path / "مباراة 2026")
    fc = lay.full_coverage
    fc.mkdir(parents=True)
    base = texture(1)
    t = T0
    for name, img in [("B1_a", with_eyes(base, "closed")),
                      ("B1_b", with_eyes(blurred(base, 1.2), "open")),
                      ("B1_c", with_eyes(blurred(base, 2.5), "open")),
                      ("B1_d", with_eyes(blurred(base, 5), "open"))]:
        save_jpeg(fc / f"{name}.jpg", img, t)
        t += timedelta(seconds=0.5)
    t += timedelta(seconds=10)
    b2 = texture(2)
    for name, img in [("B2_a", blurred(b2, 3)), ("B2_b", b2)]:
        save_jpeg(fc / f"{name}.jpg", img, t)
        t += timedelta(seconds=1)
    t += timedelta(seconds=10)
    for name in ("B3_a", "B3_b"):
        save_jpeg(fc / f"{name}.jpg", flat(128), t)
        t += timedelta(seconds=1)
    (fc / "broken.jpg").write_bytes(b"\xff\xd8\xff not really a jpeg")
    old = (T0 - timedelta(days=1)).timestamp()     # no EXIF: file date makes it the oldest
    os.utime(fc / "broken.jpg", (old, old))
    return lay


def rows_by_name(lay):
    return {r["filename"]: r for r in read_report(lay.report)}


def test_portrait_cull_end_to_end(coverage):
    lay = coverage
    before = snapshot(lay.full_coverage)
    res = run_cull(lay, Config(), "portrait", detector=FakeDetector())
    assert res.ok, res.message

    assert names(lay.selected) == ["B1_b.jpg", "B2_b.jpg", "B3_a.jpg"]
    # badly blurred / flat frames never take an Alternates slot
    assert names(lay.alternates) == ["B1_c.jpg"]
    assert snapshot(lay.full_coverage) == before          # source untouched

    rows = rows_by_name(lay)
    assert list(read_report(lay.report)[0].keys()) == COLUMNS
    assert rows["B1_b.jpg"]["result"] == "selected"
    # portrait: a blink ranks below even a soft frame with open eyes
    assert rows["B1_a.jpg"]["eyes_state"] == "closed"
    assert rows["B1_a.jpg"]["result"] == "not_selected"
    assert rows["B1_a.jpg"]["reasons"].split(";") == ["closed_eyes", "not_best_in_group"]
    assert rows["B1_a.jpg"]["group_size"] == "4" and rows["B1_a.jpg"]["rank_in_group"] == "4"
    assert "blurry" in rows["B1_d.jpg"]["reasons"].split(";")
    assert rows["B1_d.jpg"]["result"] == rows["B3_b.jpg"]["result"] == "not_selected"
    assert rows["B3_a.jpg"]["weak_group"] == "yes" and rows["B1_b.jpg"]["weak_group"] == "no"
    assert rows["broken.jpg"]["result"] == "unreadable"

    s = res.data
    assert (s["total"], s["groups"], s["selected"], s["alternates"], s["unreadable"]) == \
        (9, 3, 3, 1, 1)
    assert s["weak_groups"] == 1
    assert s["rejected_by_reason"]["closed_eyes"] == 1

    state = json.loads(lay.state.read_text(encoding="utf-8"))
    cull = state["steps"]["cull"]
    assert cull["status"] == "done" and state["mode"] == "portrait"
    assert cull["outputs"]["Full_Coverage/B1_b.jpg"] == "Selected/B1_b.jpg"
    assert all(not k.startswith(("C:", "/")) for k in cull["measurements"])  # portable


def test_action_mode_prefers_sharpest_even_with_closed_eyes(coverage):
    res = run_cull(coverage, Config(), "action", detector=FakeDetector())
    assert res.ok
    assert "B1_a.jpg" in names(coverage.selected)


def test_rerun_asks_then_clear_replaces_without_duplicates(coverage):
    fd = FakeDetector()
    run_cull(coverage, Config(), "portrait", detector=fd)
    first_calls = fd.calls

    res = run_cull(coverage, Config(), "action", detector=fd)
    assert not res.ok and res.data["needs_decision"]

    res = run_cull(coverage, Config(), "action", existing="clear", detector=fd)
    assert res.ok, res.message
    assert fd.calls == first_calls                  # re-scored from the cache
    assert "B1_a.jpg" in names(coverage.selected)
    assert "B1_b.jpg" not in names(coverage.selected)
    assert not any("_2" in n for n in names(coverage.selected) + names(coverage.alternates))


def test_changed_file_is_measured_again(coverage):
    fd = FakeDetector()
    run_cull(coverage, Config(), detector=fd)
    n = fd.calls
    save_jpeg(coverage.full_coverage / "B2_a.jpg", texture(9), T0 + timedelta(seconds=11))
    run_cull(coverage, Config(), existing="clear", detector=fd)
    assert fd.calls == n + 1


def test_stop_then_resume(coverage):
    fd = FakeDetector()
    cancel = CancelToken()

    def progress(done, total, name):
        if done == 3:
            cancel.set()

    res = run_cull(coverage, Config(), progress_cb=progress, cancel=cancel, detector=fd)
    assert res.cancelled and not res.ok
    assert not coverage.selected.exists() and not coverage.report.exists()
    state = json.loads(coverage.state.read_text(encoding="utf-8"))
    assert state["steps"]["cull"]["status"] == "partial"
    cull = state["steps"]["cull"]
    # finished the current image: broken.jpg (oldest file date) + 3 measured
    assert len(cull["done_files"]) + len(cull["failed_files"]) == 4

    fd2 = FakeDetector()
    res = run_cull(coverage, Config(), detector=fd2)
    assert res.ok
    assert fd2.calls == 5                          # 8 readable photos, 3 already measured
    assert len(names(coverage.selected)) == 3


def test_detection_params_change_invalidates_cache(coverage):
    fd = FakeDetector()
    run_cull(coverage, Config(), detector=fd)
    cfg = Config()
    cfg.detection.analysis_max_side = 320
    fd2 = FakeDetector()
    run_cull(coverage, cfg, existing="clear", detector=fd2)
    assert fd2.calls == 8


def test_gap_setting_changes_groups(coverage):
    cfg = Config()
    cfg.grouping.group_gap_seconds = 30
    res = run_cull(coverage, cfg, detector=FakeDetector())
    assert res.data["groups"] == 1


def test_keep_alternates_zero(coverage):
    cfg = Config()
    cfg.grouping.keep_alternates = 0
    run_cull(coverage, cfg, detector=FakeDetector())
    assert names(coverage.alternates) == []


def test_input_override_outside_coverage(tmp_path):
    other = tmp_path / "hand picked"
    other.mkdir()
    for i in range(3):
        save_jpeg(other / f"x{i}.jpg", blurred(texture(), i), T0 + timedelta(seconds=i * 0.3))
    lay = CoverageLayout.for_main(tmp_path / "Main")
    lay.main.mkdir()
    res = run_cull(lay, Config(), input_dir=other, detector=FakeDetector())
    assert res.ok, res.message
    assert names(lay.selected) == ["x0.jpg"]
    assert len(list_jpegs(other)) == 3


def test_refuses_output_inside_input(coverage):
    save_jpeg(coverage.full_coverage / "z.jpg", texture(), T0)
    res = run_cull(coverage, Config(), input_dir=coverage.main, detector=FakeDetector())
    assert not res.ok and "inside" in res.message


def test_empty_and_bad_mode(tmp_path):
    lay = CoverageLayout.for_main(tmp_path / "E")
    lay.full_coverage.mkdir(parents=True)
    assert run_cull(lay, Config(), detector=FakeDetector()).ok
    assert not run_cull(lay, Config(), mode="sports", detector=FakeDetector()).ok


def test_cli_cull(coverage):
    r = subprocess.run([sys.executable, "-m", "photocull", "cull", str(coverage.main),
                        "--mode", "action"], cwd=APP, capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert coverage.report.exists() and len(names(coverage.selected)) == 3
    r = subprocess.run([sys.executable, "-m", "photocull", "cull", str(coverage.main)],
                       cwd=APP, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1 and "--existing clear" in r.stderr
