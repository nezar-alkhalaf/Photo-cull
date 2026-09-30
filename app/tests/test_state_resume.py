import json

from photocull.state import State, atomic_write_json

P1 = {"style": "Warm", "quality": 92}
P2 = {"style": "Cool", "quality": 92}


def test_fresh_state_defaults(tmp_path):
    s = State(tmp_path / "state.json")
    assert s.step("cull")["status"] == "not_started"
    assert s.step("edit")["status"] == "not_started"


def test_atomic_write_leaves_no_temp_and_roundtrips(tmp_path):
    p = tmp_path / "state.json"
    atomic_write_json(p, {"a": "مرحبا"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": "مرحبا"}
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_failure_keeps_old_file(tmp_path):
    p = tmp_path / "state.json"
    atomic_write_json(p, {"ok": 1})
    try:
        atomic_write_json(p, {"bad": object()})
    except TypeError:
        pass
    assert json.loads(p.read_text()) == {"ok": 1}
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"]


def test_resume_skips_done_files_when_params_same(tmp_path):
    p = tmp_path / "state.json"
    s = State(p)
    s.start_step("edit", P1)
    s.mark_done("edit", "Selected/IMG_001.jpg")
    s.mark_failed("edit", "Selected/IMG_007.jpg", "COM error")
    s.save()

    s2 = State(p)
    assert not s2.params_changed("edit", P1)
    s2.start_step("edit", P1)
    assert s2.is_done("edit", "Selected/IMG_001.jpg")
    assert not s2.is_done("edit", "Selected/IMG_007.jpg")
    assert "Selected/IMG_007.jpg" in s2.step("edit")["failed_files"]


def test_param_change_is_detected_and_resets(tmp_path):
    p = tmp_path / "state.json"
    s = State(p)
    s.start_step("edit", P1)
    s.mark_done("edit", "Selected/IMG_001.jpg")
    s.save()

    s2 = State(p)
    assert s2.params_changed("edit", P2)
    s2.start_step("edit", P2)
    assert not s2.is_done("edit", "Selected/IMG_001.jpg")


def test_mark_done_clears_failure_and_no_duplicates(tmp_path):
    s = State(tmp_path / "state.json")
    s.start_step("edit", P1)
    s.mark_failed("edit", "a.jpg", "x")
    s.mark_done("edit", "a.jpg")
    s.mark_done("edit", "a.jpg")
    assert s.step("edit")["done_files"] == ["a.jpg"]
    assert s.step("edit")["failed_files"] == {}


def test_corrupt_state_starts_fresh(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json", encoding="utf-8")
    s = State(p)
    assert s.step("cull")["status"] == "not_started"
    assert (tmp_path / "state.json.corrupt").exists()


def test_done_status_gets_timestamp(tmp_path):
    s = State(tmp_path / "state.json")
    s.set_status("cull", "done")
    assert "finished_at" in s.step("cull")
