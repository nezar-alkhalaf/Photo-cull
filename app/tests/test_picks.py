from datetime import datetime, timedelta

import pytest

from photocull.config import Config
from photocull.cull import run_cull
from photocull.paths import CoverageLayout, SafetyError, list_jpegs
from photocull.picks import (USER_PICK, drop_edits, load_review, move_pick, stale_edits,
                             swap_pick)
from photocull.report import read_report
from photocull.state import State
from synth import FakeDetector, blurred, save_jpeg, texture, with_eyes

T0 = datetime(2026, 10, 3, 18, 30, 0)


@pytest.fixture
def culled(tmp_path):
    lay = CoverageLayout.for_main(tmp_path / "تغطية")
    fc = lay.full_coverage
    fc.mkdir(parents=True)
    base = texture(1)
    t = T0
    for name, sigma in (("a", 0), ("b", 1.2), ("c", 2.5)):          # one burst
        save_jpeg(fc / f"{name}.jpg", with_eyes(blurred(base, sigma), "open"), t)
        t += timedelta(seconds=0.5)
    t += timedelta(seconds=20)
    save_jpeg(fc / "d.jpg", with_eyes(texture(2), "open"), t)        # a second moment
    res = run_cull(lay, Config(), "portrait", detector=FakeDetector())
    assert res.ok, res.message
    return lay


def names(folder):
    return [p.name for p in list_jpegs(folder)]


def test_review_joins_files_with_report(culled):
    rv = load_review(culled)
    assert [i.path.name for i in rv.selected] == ["a.jpg", "d.jpg"]
    assert [i.path.name for i in rv.alternates] == ["b.jpg", "c.jpg"]
    a = rv.selected[0]
    assert a.row["result"] == "selected"
    assert [x.path.name for x in rv.alternates_of(a)] == ["b.jpg", "c.jpg"]


def test_swap_moves_files_and_updates_records(culled):
    rv = load_review(culled)
    swap_pick(culled, rv.selected[0].path, rv.alternates[0].path)
    assert names(culled.selected) == ["b.jpg", "d.jpg"]
    assert names(culled.alternates) == ["a.jpg", "c.jpg"]
    rows = {r["filename"]: r for r in read_report(culled.report)}
    assert rows["b.jpg"]["result"] == "selected" and USER_PICK in rows["b.jpg"]["reasons"]
    assert rows["a.jpg"]["result"] == "alternate"
    assert "not_best_in_group" in rows["a.jpg"]["reasons"]
    cull = State(culled.state).step("cull")
    assert cull["summary"]["selected"] == 2 and cull["summary"]["alternates"] == 2
    assert cull["outputs"]["Full_Coverage/b.jpg"] == "Selected/b.jpg"
    rv2 = load_review(culled)                      # the review still finds the right rows
    assert [i.source_name for i in rv2.selected] == ["b.jpg", "d.jpg"]
    assert rv2.selected[0].row["result"] == "selected"


def test_originals_are_never_touched(culled):
    before = {p.name: p.read_bytes() for p in list_jpegs(culled.full_coverage)}
    rv = load_review(culled)
    swap_pick(culled, rv.selected[0].path, rv.alternates[0].path)
    move_pick(culled, rv.selected[1].path, "alternate")
    after = {p.name: p.read_bytes() for p in list_jpegs(culled.full_coverage)}
    assert before == after


def test_remove_then_add_back(culled):
    rv = load_review(culled)
    p = move_pick(culled, rv.selected[1].path, "alternate")          # d.jpg out of Selected
    assert names(culled.selected) == ["a.jpg"] and "d.jpg" in names(culled.alternates)
    move_pick(culled, p, "selected")
    assert names(culled.selected) == ["a.jpg", "d.jpg"]


def test_refuses_files_outside_the_two_folders(culled):
    with pytest.raises(SafetyError):
        move_pick(culled, culled.full_coverage / "a.jpg", "selected")
    with pytest.raises(SafetyError):                                  # wrong direction
        move_pick(culled, culled.selected / "a.jpg", "selected")
    assert names(culled.full_coverage) == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]


def test_stale_edit_copy_can_be_dropped(culled):
    culled.edited.mkdir()
    (culled.edited / "a.jpg").write_bytes(b"x")
    st = State(culled.state)
    st.start_step("edit", {"p": 1})
    st.step("edit").setdefault("outputs", {})["Selected/a.jpg"] = "Edited/a.jpg"
    st.mark_done("edit", "Selected/a.jpg")
    st.set_status("edit", "done")
    st.save()
    assert stale_edits(culled, ["a.jpg", "d.jpg"]) == ["Edited/a.jpg"]
    assert drop_edits(culled, ["a.jpg"]) == ["Edited/a.jpg"]
    assert not (culled.edited / "a.jpg").exists()
    e = State(culled.state).step("edit")
    assert e["done_files"] == [] and e["status"] == "partial"
