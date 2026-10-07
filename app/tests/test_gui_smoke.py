"""Opens the real window briefly. Skipped when no display is available."""

import time
from datetime import datetime, timedelta

import pytest

from photocull.config import Config
from photocull.cull import run_cull
from photocull.paths import CoverageLayout
from synth import FakeDetector, blurred, save_jpeg, texture, with_eyes

ctk = pytest.importorskip("customtkinter")


@pytest.fixture(scope="module")
def _window(tmp_path_factory):
    """One window for the whole module: Tk does not like many roots in one process."""
    from photocull.gui import app as appmod
    from photocull.gui.settings import UserSettings

    s = UserSettings(tmp_path_factory.mktemp("ui") / "settings.json")
    try:
        win = appmod.App(Config(), s)
    except Exception as exc:                       # no display / no Tk
        pytest.skip(f"cannot open a window here: {exc}")
    win._stop_probe.set()                           # no Photoshop probing in tests
    yield win
    win._destroy()


@pytest.fixture
def app(_window, tmp_path):
    from photocull.gui.settings import UserSettings

    win = _window
    win.settings = UserSettings(tmp_path / "settings.json")
    win.layout = None
    win.view = "selected"
    win.var_mpp.set("20")
    for var in win.adv_vars.values():
        var.set("")
    win._refresh_all()
    win.update()
    return win


def pump(win, cond, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        win.update()
        if cond():
            return True
        time.sleep(0.02)
    return False


def make_coverage(tmp_path) -> CoverageLayout:
    lay = CoverageLayout.for_main(tmp_path / "تغطية")
    lay.full_coverage.mkdir(parents=True)
    base, t = texture(1), datetime(2026, 10, 3, 18, 30)
    for i, sigma in enumerate((0, 1.5, 3)):
        save_jpeg(lay.full_coverage / f"p{i}.jpg", with_eyes(blurred(base, sigma), "open"), t)
        t += timedelta(seconds=0.4)
    t += timedelta(seconds=30)
    save_jpeg(lay.full_coverage / "q.jpg", with_eyes(texture(2), "open"), t)
    return lay


def test_empty_window_has_disabled_buttons_with_reasons(app):
    app.update()
    assert str(app.btn_all.cget("state")) == "disabled"
    assert "main folder" in app.tip_all.text
    for card in app.cards.values():
        assert str(card.button.cget("state")) == "disabled" and card.tip.text
    assert str(app.btn_stop.cget("state")) == "disabled"


def test_dashboard_prediction_updates_with_max_per_post(app, tmp_path):
    lay = make_coverage(tmp_path)
    assert run_cull(lay, Config(), "portrait", detector=FakeDetector()).ok
    app._set_folder(lay.main, ask_create=False)
    assert pump(app, lambda: app._n["selected"] == 2)
    assert app.card_total.value.cget("text") == "4"
    assert "2 selected" in app.lbl_predict.cget("text") and "1 post" in app.lbl_predict.cget("text")
    app.var_mpp.set("1")
    app.update()
    assert "2 posts" in app.lbl_predict.cget("text") and "1/1" in app.lbl_predict.cget("text")
    app.var_mpp.set("abc")
    app.update()
    assert "whole number" in app.lbl_predict.cget("text")
    assert len(app.grid_view.items) == 2


def test_swap_from_the_window_and_split_runs_through_the_worker(app, tmp_path, monkeypatch):
    from photocull.gui import app as appmod

    lay = make_coverage(tmp_path)
    assert run_cull(lay, Config(), "portrait", detector=FakeDetector()).ok
    monkeypatch.setattr(appmod, "ask_choice", lambda *a, **k: a[3][0][1])
    monkeypatch.setattr(appmod.messagebox, "showerror", lambda *a, **k: None)
    app._set_folder(lay.main, ask_create=False)
    assert pump(app, lambda: len(app.grid_view.items) == 2)
    app.grid_view.select(app.grid_view.items[0].key)
    app.update()
    assert str(app.btn_a.cget("state")) == "normal"          # p0 has alternates in its moment
    app._act("b")                                            # remove from Selected
    assert pump(app, lambda: len(app.grid_view.items) == 1)

    # step 3 straight from the window: Edited is empty, so point it at Selected via Advanced
    app.adv_vars["split_input"].set(str(lay.selected))
    assert pump(app, lambda: app._n["split_in"] == 1)
    app._start("split")
    assert app.worker is not None
    assert pump(app, lambda: app.worker is None)
    assert [p.name for p in (lay.posts / "Post_1").iterdir()] == ["q.jpg"]
    assert app.cards["split"].badge.cget("text") == "Done ✓"
