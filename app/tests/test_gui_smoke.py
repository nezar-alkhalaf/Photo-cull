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

    # step 3 straight from the window: Edited is empty, so point it at Selected via Settings
    app.adv_vars["split_input"].set(str(lay.selected))
    assert pump(app, lambda: app._n["split_in"] == 1)
    app._start("split")
    assert app.worker is not None
    assert pump(app, lambda: app.worker is None)
    assert [p.name for p in (lay.posts / "Post_1").iterdir()] == ["q.jpg"]
    assert app.cards["split"].badge.cget("text") == "Done ✓"


def test_language_button_redraws_the_window_in_arabic_and_back(app, tmp_path):
    from photocull.gui import i18n

    lay = make_coverage(tmp_path)
    assert run_cull(lay, Config(), "portrait", detector=FakeDetector()).ok
    app._set_folder(lay.main, ask_create=False)
    assert pump(app, lambda: app._n["selected"] == 2)
    try:
        assert app.btn_lang.cget("text") == "العربية"
        app._toggle_language()
        app.update()
        assert i18n.LANG == "ar" and app.settings["language"] == "ar"
        assert app.btn_lang.cget("text") == "English"
        assert "اختر" in app.btn_folder.cget("text")
        assert str(lay.main) == app.lbl_folder.cget("text")
        assert pump(app, lambda: len(app.grid_view.items) == 2)
        assert "مختارة" in app.lbl_predict.cget("text")
        assert app.seg.get().endswith("(2)")
        app._toggle_language()
        app.update()
        assert i18n.LANG == "en" and app.btn_folder.cget("text") == "Choose folder…"
        assert "selected" in app.lbl_predict.cget("text")
    finally:
        i18n.set_lang("en")


def test_sidebar_pages_switch_and_survive_a_language_change(app, tmp_path):
    from photocull.gui import i18n

    assert set(app.pages) == {"home", "review", "settings", "activity"}
    assert app.page_key == "home"
    for key in ("review", "settings", "activity", "home"):
        app.show_page(key)
        app.update()
        assert app.page_key == key
        on = [k for k, b in app.nav_btns.items() if b.cget("fg_color") != "transparent"]
        assert on == [key]
    app.show_page("settings")
    try:
        app._toggle_language()
        app.update()
        assert app.page_key == "settings"
        assert "الإعدادات" in app.nav_btns["settings"].cget("text")
        # settings typed on the Settings page are kept across the redraw
        app.adv_vars["blurry_sharpness"].set("12")
        app._toggle_language()
        app.update()
        assert app.adv_vars["blurry_sharpness"].get() == "12"
    finally:
        i18n.set_lang("en")
        app.show_page("home")


def test_arabic_mirrors_the_layout(app, tmp_path):
    from photocull.gui import i18n

    lay = make_coverage(tmp_path)
    assert run_cull(lay, Config(), "portrait", detector=FakeDetector()).ok
    app._set_folder(lay.main, ask_create=False)
    assert pump(app, lambda: len(app.grid_view.items) == 2)
    sidebar = app.nav_btns["home"].master
    try:
        app.update()
        assert int(sidebar.grid_info()["column"]) == 0
        cull_col = int(app.cards["cull"].grid_info()["column"])
        assert cull_col == 0
        app._toggle_language()
        app.update()
        assert int(app.nav_btns["home"].master.grid_info()["column"]) == 1      # sidebar on the right
        assert int(app.cards["cull"].grid_info()["column"]) == 2                # steps start at the right
        assert int(app.grid_view.grid_info()["column"]) == 1                    # photos right, details left
        assert int(app.detail.grid_info()["column"]) == 0
        assert pump(app, lambda: len(app.grid_view.items) == 2)
        app.show_page("review")
        app.update()
        gv = app.grid_view
        gv._paint()
        first = gv.items[0]
        cw, ch, _ = gv._metrics()
        # a click on the right-most cell picks the first photo
        class E: x = gv._width() - cw // 2; y = ch // 2
        assert gv.items[gv._index_at(E)] is first
    finally:
        i18n.set_lang("en")
        app._toggle_language() if app.btn_lang.cget("text") == "English" else None


def test_run_all_only_needs_photos_in_full_coverage(app, tmp_path, monkeypatch):
    lay = make_coverage(tmp_path)                 # nothing culled yet: Selected is empty
    monkeypatch.setattr(app, "_style_value", lambda: "x.psd")
    app._set_folder(lay.main, ask_create=False)
    assert pump(app, lambda: app._n["full"] == 4)
    app._update_buttons()
    assert app._n["selected"] == 0
    assert str(app.btn_all.cget("state")) == "normal", app.tip_all.text
    assert str(app.cards["cull"].button.cget("state")) == "normal"
    assert str(app.cards["edit"].button.cget("state")) == "disabled"      # needs step 1
    assert str(app.cards["split"].button.cget("state")) == "disabled"     # nothing picked yet


def test_split_works_straight_after_cull_by_asking_first(app, tmp_path, monkeypatch):
    from photocull.gui import app as appmod

    lay = make_coverage(tmp_path)
    assert run_cull(lay, Config(), "portrait", detector=FakeDetector()).ok
    app._set_folder(lay.main, ask_create=False)
    assert pump(app, lambda: app._n["selected"] == 2)
    assert app._n["split_in"] == 0                                         # nothing edited
    app._update_buttons()
    assert str(app.cards["split"].button.cget("state")) == "normal"

    asked = []
    monkeypatch.setattr(appmod, "ask_choice",
                        lambda *a, **k: asked.append(a[2]) or None)       # user presses Cancel
    app._start("split")
    app.update()
    assert asked and "2" in asked[0] and app.worker is None
    assert not (lay.posts / "Post_1").exists()

    monkeypatch.setattr(appmod, "ask_choice", lambda *a, **k: a[3][0][1])  # user agrees
    app._start("split")
    assert pump(app, lambda: app.worker is None)
    assert sorted(p.name for p in (lay.posts / "Post_1").iterdir()) == \
        sorted(p.name for p in lay.selected.iterdir())
    assert not any(lay.edited.iterdir()) if lay.edited.exists() else True
