import hashlib
import json
import struct
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from photocull import styles
from photocull.config import Config
from photocull.edit import parse_action, run_edit
from photocull.paths import CoverageLayout, list_jpegs
from photocull.photoshop import PhotoshopError, jpeg_quality_12
from photocull.runner import CancelToken
from synth import save_jpeg, texture

T0 = datetime(2026, 10, 3, 18, 0, 0)


# ---------- a Photoshop stand-in ----------

class FakeStyle:
    def __init__(self, name):
        self.name, self.layer_count, self.closed = name, 2, False

    def close(self):
        self.closed = True


class FakePhotoshop:
    """Behaves like photoshop.Photoshop: 'applies' the style by inverting the photo."""

    def __init__(self, fail_on=(), die_before=None, actions=None):
        self.fail_on, self.die_before = set(fail_on), die_before
        self.alive = False
        self.dialogs = 2
        self.applied, self.opened_styles, self.connects = [], 0, 0
        self.actions = actions or {}

    def connect(self):
        self.connects += 1
        self.alive = True
        return "27.10"

    def is_alive(self):
        return self.alive

    def ensure(self):
        if not self.alive:
            self.connect()

    def close(self):
        pass

    @contextmanager
    def no_dialogs(self):
        old, self.dialogs = self.dialogs, 3
        try:
            yield
        finally:
            self.dialogs = old

    def open_style(self, psd):
        self.opened_styles += 1
        return FakeStyle(Path(psd).name)

    def loaded_action_sets(self):
        return self.actions

    def load_actions(self, atn, set_name):
        return self.actions[set_name]

    def apply(self, style, working, quality, action=None):
        name = Path(working).name
        if name == self.die_before:                  # user closes Photoshop now
            self.die_before = None
            self.alive = False
            raise PhotoshopError("Opening the photo failed: RPC server unavailable")
        if name in self.fail_on:
            raise PhotoshopError("Saving the photo failed: disk busy")
        assert self.dialogs == 3
        with Image.open(working) as im:
            out = im.convert("RGB").point(lambda v: 255 - v)
        out.save(working, "JPEG", quality=90)
        self.applied.append((name, style.name, action))


@pytest.fixture
def cov(tmp_path, monkeypatch):
    lay = CoverageLayout.for_main(tmp_path / "فعالية")
    lay.full_coverage.mkdir(parents=True)
    lay.selected.mkdir()
    for i in range(5):
        save_jpeg(lay.selected / f"P{i}.jpg", texture(i), T0 + timedelta(seconds=i))
    sdir = tmp_path / "Styles"
    sdir.mkdir()
    (sdir / "Warm.psd").write_bytes(b"8BPS fake")
    monkeypatch.setattr(styles, "ROOT_DIR", tmp_path)
    return lay


def md5s(folder):
    return {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in list_jpegs(folder)}


def state_of(lay):
    return json.loads(lay.state.read_text(encoding="utf-8"))["steps"]["edit"]


# ---------- tests ----------

def test_edits_all_and_never_touches_selected(cov):
    before = md5s(cov.selected)
    ps = FakePhotoshop()
    res = run_edit(cov, Config(), "Warm.psd", exposure=False, ps=ps)
    assert res.ok, res.message
    assert sorted(md5s(cov.edited)) == sorted(before)
    assert md5s(cov.selected) == before
    assert all(md5s(cov.edited)[n] != before[n] for n in before)     # style applied
    assert ps.dialogs == 2                                           # restored
    st = state_of(cov)
    assert st["status"] == "done" and st["outputs"]["Selected/P0.jpg"] == "Edited/P0.jpg"


def test_exposure_fix_runs_before_photoshop(cov):
    p = cov.selected / "dark.jpg"
    Image.new("RGB", (300, 200), (40, 40, 40)).save(p, "JPEG")
    run_edit(cov, Config(), "Warm.psd", ps=FakePhotoshop())
    with Image.open(cov.edited / "dark.jpg") as im:          # brightened, then inverted
        mean = float(np.asarray(im.convert("L")).mean())
    assert mean < 255 - 50                                     # 40 -> ~57 (+1 stop cap)


def test_failed_photo_recorded_and_others_continue(cov):
    res = run_edit(cov, Config(), "Warm.psd", exposure=False, ps=FakePhotoshop(fail_on={"P2.jpg"}))
    assert not res.ok and "4 of 5" in res.message
    assert not (cov.edited / "P2.jpg").exists()              # half-made copy removed
    assert "Selected/P2.jpg" in state_of(cov)["failed_files"]
    ps = FakePhotoshop()
    res = run_edit(cov, Config(), "Warm.psd", exposure=False, ps=ps)  # retry only the failed one
    assert res.ok and [a[0] for a in ps.applied] == ["P2.jpg"]


def test_photoshop_closed_mid_run_reconnects_and_retries(cov):
    ps = FakePhotoshop(die_before="P1.jpg")
    res = run_edit(cov, Config(), "Warm.psd", exposure=False, ps=ps)
    assert res.ok, res.message
    assert ps.connects == 2 and ps.opened_styles == 2
    assert len(list_jpegs(cov.edited)) == 5


def test_stop_and_resume(cov):
    cancel = CancelToken()
    res = run_edit(cov, Config(), "Warm.psd", exposure=False, ps=FakePhotoshop(),
                   cancel=cancel, progress_cb=lambda d, t, n: d == 2 and cancel.set())
    assert res.cancelled and res.done == 3
    ps = FakePhotoshop()
    res = run_edit(cov, Config(), "Warm.psd", exposure=False, ps=ps)
    assert res.ok and len(ps.applied) == 2 and len(list_jpegs(cov.edited)) == 5


def test_preview_then_continue(cov):
    res = run_edit(cov, Config(), "Warm.psd", exposure=False, preview=2, ps=FakePhotoshop())
    assert res.data["preview_done"] and res.data["remaining"] == 3
    assert len(list_jpegs(cov.edited)) == 2
    res = run_edit(cov, Config(), "Warm.psd", exposure=False, ps=FakePhotoshop())
    assert res.ok and len(list_jpegs(cov.edited)) == 5


def test_changed_style_asks_then_reprocesses_without_duplicates(cov, tmp_path):
    run_edit(cov, Config(), "Warm.psd", exposure=False, ps=FakePhotoshop())
    (tmp_path / "Styles" / "Cool.psd").write_bytes(b"8BPS cool")
    res = run_edit(cov, Config(), "Cool.psd", exposure=False, ps=FakePhotoshop())
    assert not res.ok and res.data["needs_decision"]
    ps = FakePhotoshop()
    res = run_edit(cov, Config(), "Cool.psd", exposure=False, existing="reprocess", ps=ps)
    assert res.ok and len(ps.applied) == 5
    assert sorted(p.name for p in list_jpegs(cov.edited)) == [f"P{i}.jpg" for i in range(5)]


def test_existing_unrelated_file_is_not_overwritten(cov):
    (cov.edited).mkdir()
    (cov.edited / "P0.jpg").write_bytes(b"user's own file")
    run_edit(cov, Config(), "Warm.psd", exposure=False, ps=FakePhotoshop())
    assert (cov.edited / "P0.jpg").read_bytes() == b"user's own file"
    assert (cov.edited / "P0_2.jpg").exists()


def test_action_checked_before_any_photo(cov):
    ps = FakePhotoshop(actions={"Finish": ["Sharpen"]})
    res = run_edit(cov, Config(), "Warm.psd", ("Finish", "Glow"), exposure=False, ps=ps)
    assert not res.ok and "Glow" in res.message and ps.applied == []
    res = run_edit(cov, Config(), "Warm.psd", ("Finish", "Sharpen"), exposure=False, ps=ps)
    assert res.ok and ps.applied[0][2] == ("Finish", "Sharpen")


def test_missing_style_and_empty_input(cov, tmp_path):
    res = run_edit(cov, Config(), "Nope.psd", ps=FakePhotoshop())
    assert not res.ok and "Styles" in res.message
    lay = CoverageLayout.for_main(tmp_path / "empty")
    lay.selected.mkdir(parents=True)
    assert run_edit(lay, Config(), "Warm.psd", ps=FakePhotoshop()).ok


def test_refuses_output_inside_input(cov):
    res = run_edit(cov, Config(), "Warm.psd", input_dir=cov.main, ps=FakePhotoshop())
    assert not res.ok and "inside" in res.message


# ---------- small helpers ----------

def test_parse_action():
    assert parse_action("My Set/Sharpen") == ("My Set", "Sharpen")
    assert parse_action("") is None and parse_action("None") is None
    with pytest.raises(ValueError):
        parse_action("Sharpen")


def test_jpeg_quality_mapping():
    assert jpeg_quality_12(92) == 11 and jpeg_quality_12(100) == 12 and jpeg_quality_12(1) == 0


def test_atn_set_name(tmp_path):
    name = "تشطيب Finish"
    units = (name + "\x00").encode("utf-16-be")
    atn = tmp_path / "x.atn"
    atn.write_bytes(struct.pack(">II", 16, len(units) // 2) + units + b"\x00" + b"\x00" * 20)
    assert styles.read_atn_set_name(atn) == name
    (tmp_path / "bad.atn").write_bytes(b"hello world")
    with pytest.raises(ValueError):
        styles.read_atn_set_name(tmp_path / "bad.atn")
