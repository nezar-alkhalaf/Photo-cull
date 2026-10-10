"""Plan section 17: the project and every coverage folder must move to another PC/path."""

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from photocull.config import Config
from photocull.cull import run_cull
from photocull.paths import CoverageLayout
from photocull.picks import load_review
from photocull.split import run_split
from photocull.state import State
from synth import FakeDetector, blurred, save_jpeg, texture, with_eyes

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
DRIVE_PATH = re.compile(r"""(?<![A-Za-z0-9])[A-Za-z]:[\/](?=\S)""")
# the two places where an absolute-looking example is deliberate: tests and URL-like text
SKIP_PARTS = {".venv", "__pycache__", "tests", ".pytest_cache", "logs"}


def _owned_files():
    for pattern in ("photocull/**/*.py", "*.toml", "*.bat", "*.txt"):
        for p in APP.glob(pattern):
            if not SKIP_PARTS & set(p.relative_to(APP).parts):
                yield p
    for p in ROOT.glob("*.bat"):
        yield p
    yield from ROOT.glob("*.txt")


def test_no_absolute_windows_paths_in_app_files():
    hits = []
    for p in _owned_files():
        for n, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), 1):
            code = line.split("#")[0] if p.suffix in (".py", ".toml") else line
            if DRIVE_PATH.search(code) and "http" not in code:
                hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not hits, "absolute paths found:\n" + "\n".join(hits)


def test_app_never_reads_appdata_or_home():
    for p in (APP / "photocull").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"APPDATA|LOCALAPPDATA|USERPROFILE", text.split('"""', 2)[-1]
                             .replace("%APPDATA%", "")), p
        assert "expanduser" not in text and "Path.home" not in text, p


def test_root_and_app_paths_are_relative_to_the_code():
    from photocull import paths

    assert paths.APP_DIR == Path(paths.__file__).resolve().parents[1]
    assert paths.ROOT_DIR == paths.APP_DIR.parent
    assert paths.LOGS_DIR.parent == paths.APP_DIR


def test_batch_files_exist_and_use_crlf():
    for p in (ROOT / "Start PhotoCull.bat", ROOT / "First-Time Setup.bat",
              APP / "setup.bat", APP / "run.bat"):
        raw = p.read_bytes()
        assert raw, p
        assert b"\n" not in raw.replace(b"\r\n", b""), f"{p.name} must use CRLF line endings"
        assert all(b < 128 for b in raw), f"{p.name} must be plain ASCII"


def test_requirements_are_pinned_and_runtime_only():
    lines = [ln.split("#")[0].strip() for ln in
             (APP / "requirements.txt").read_text(encoding="utf-8").splitlines()]
    pkgs = [ln for ln in lines if ln]
    assert pkgs and all("==" in ln for ln in pkgs), pkgs
    assert any(ln.startswith("mediapipe==") for ln in pkgs)
    assert not any(ln.lower().startswith("pytest") for ln in pkgs)


@pytest.fixture
def coverage(tmp_path):
    lay = CoverageLayout.for_main(tmp_path / "PC one" / "مباراة")
    lay.full_coverage.mkdir(parents=True)
    base, t = texture(1), datetime(2026, 10, 3, 18, 30)
    for i, sigma in enumerate((0, 1.5, 3)):
        save_jpeg(lay.full_coverage / f"p{i}.jpg", with_eyes(blurred(base, sigma), "open"), t)
        t += timedelta(seconds=0.4)
    t += timedelta(seconds=30)
    save_jpeg(lay.full_coverage / "q.jpg", with_eyes(texture(2), "open"), t)
    return lay


def test_coverage_folder_moves_to_another_path_and_keeps_working(coverage, tmp_path):
    assert run_cull(coverage, Config(), "portrait", detector=FakeDetector()).ok
    old_main = str(coverage.main)
    for f in (coverage.state, coverage.report):
        text = f.read_text(encoding="utf-8-sig")
        assert old_main not in text and old_main.replace("\\", "/") not in text
        assert not DRIVE_PATH.search(text), f"{f.name} stores an absolute path"

    moved = CoverageLayout.for_main(tmp_path / "other drive (D)" / "نسخة")
    moved.main.parent.mkdir(parents=True)
    shutil.copytree(coverage.main, moved.main)
    shutil.rmtree(coverage.main)

    review = load_review(moved)
    assert len(review.selected) == 2
    assert all(i.row for i in review.selected), "report.csv rows must still match after the move"
    assert State(moved.state).step("cull")["status"] == "done"
    assert run_split(moved, 20, input_dir=moved.selected).ok
    assert len(list((moved.posts / "Post_1").glob("*.jpg"))) == 2


def test_shortcut_script_and_icon_ship_with_the_app():
    ps1 = APP / "shortcuts.ps1"
    assert ps1.is_file() and "IShellLinkW" in ps1.read_text(encoding="utf-8")
    assert all(b < 128 for b in ps1.read_bytes()), "keep the script ASCII so any PowerShell reads it"
    ico = (APP / "assets" / "photocull.ico").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00" and int.from_bytes(ico[4:6], "little") >= 5  # several sizes
