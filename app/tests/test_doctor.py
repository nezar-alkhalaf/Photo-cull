from pathlib import Path

from photocull import doctor
from photocull.config import Config
from photocull.doctor import FAIL, OK, WARN, Check, format_report, pinned_versions, run_doctor


def by_name(checks):
    return {c.name: c for c in checks}


def test_pinned_versions_reads_requirements(tmp_path):
    req = tmp_path / "r.txt"
    req.write_text("# comment\nPillow==1.2.3  # x\nMediaPipe==9.9\nfoo>=1\n", encoding="utf-8")
    assert pinned_versions(req) == {"pillow": "1.2.3", "mediapipe": "9.9"}
    assert pinned_versions(tmp_path / "missing.txt") == {}


def test_doctor_runs_and_reports_the_main_areas():
    checks = run_doctor(Config())
    names = {c.name for c in checks}
    assert {"Python", "Package mediapipe", "Package pywin32", "Photoshop", "Styles folder",
            "Write access"} <= names
    assert any(n.startswith("Disk space") for n in names)
    assert all(c.status in (OK, WARN, FAIL) for c in checks)
    assert all(c.fix for c in checks if c.status == FAIL)


def test_missing_styles_folder_is_a_failure_with_a_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "styles_dir", lambda cfg: tmp_path / "nope")
    c = doctor._check_styles(Config())
    assert c.status == FAIL and "Styles" in c.fix


def test_empty_styles_folder_is_only_a_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "styles_dir", lambda cfg: tmp_path)
    monkeypatch.setattr(doctor, "list_styles", lambda cfg: [])
    assert doctor._check_styles(Config()).status == WARN


def test_missing_model_files_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "APP_DIR", tmp_path)
    c = doctor._check_models()
    assert c.status == FAIL and "face_landmarker.task" in c.detail


def test_low_disk_space(monkeypatch, tmp_path):
    class Usage:
        free = 100 * 2**20
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda p: Usage)
    out = doctor._check_disk(None)
    assert out and out[0].status == FAIL


def test_missing_last_coverage_folder_asks_to_pick_again(tmp_path):
    checks = by_name(run_doctor(Config(), tmp_path / "gone"))
    assert checks["Last coverage folder"].status == WARN


def test_a_crashing_check_does_not_break_the_list(monkeypatch):
    def boom(*a):
        raise RuntimeError("x")
    monkeypatch.setattr(doctor, "_check_styles", boom)
    checks = run_doctor(Config())
    assert any(c.status == FAIL and "crashed" in c.detail for c in checks)
    assert any(c.name == "Write access" for c in checks)


def test_report_text_lists_fix_only_for_problems():
    text = format_report([Check("A", OK, "fine", "ignore me"), Check("B", FAIL, "bad", "do this")])
    assert "do this" in text and "ignore me" not in text
    assert "1 problem" in text


def test_unwritable_logs_folder_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "_writable", lambda p: "denied")
    c = doctor._check_write()
    assert c.status == FAIL and "Desktop" in c.fix
