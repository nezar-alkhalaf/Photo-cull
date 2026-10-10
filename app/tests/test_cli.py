import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run([sys.executable, "-m", "photocull", *args], cwd=APP,
                          capture_output=True, text=True, encoding="utf-8")


def test_help_works():
    r = run("--help")
    assert r.returncode == 0
    for cmd in ("init", "cull", "edit", "split", "all", "doctor", "gui"):
        assert cmd in r.stdout


def test_init_creates_folders(tmp_path):
    main = tmp_path / "تغطية"
    (main / "Full_Coverage").mkdir(parents=True)
    r = run("init", str(main))
    assert r.returncode == 0, r.stderr
    for d in ("Selected", "Alternates", "Edited", "Posts"):
        assert (main / d).is_dir()


def test_init_without_full_coverage_fails_cleanly(tmp_path):
    r = run("init", str(tmp_path))
    assert r.returncode == 1 and "Full_Coverage" in r.stderr


def test_doctor_prints_a_checklist():
    r = run("doctor")
    assert r.returncode in (0, 1)
    assert "[ OK ] Python" in r.stdout and "Photoshop" in r.stdout


def test_edit_exposure_switch():
    from photocull.cli import build_parser
    p = build_parser()
    assert p.parse_args(["edit", "x"]).exposure is None            # config default
    assert p.parse_args(["edit", "x", "--exposure"]).exposure is True
    assert p.parse_args(["edit", "x", "--no-exposure"]).exposure is False
