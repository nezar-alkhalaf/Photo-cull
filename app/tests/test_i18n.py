import ast
import re
from pathlib import Path

import pytest

from photocull.gui import i18n

GUI = Path(__file__).resolve().parents[1] / "photocull" / "gui"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.fixture(autouse=True)
def _restore_language():
    yield
    i18n.set_lang("en")


def _t_literals():
    """Every literal string passed to t(...) in the GUI modules."""
    found = []
    for path in GUI.glob("*.py"):
        if path.name == "i18n.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "t" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.append((path.name, node.lineno, node.args[0].value))
    return found


def test_every_text_shown_through_t_has_an_arabic_version():
    literals = _t_literals()
    assert len(literals) > 80
    missing = [f"{f}:{n} {s!r}" for f, n, s in literals if s not in i18n.AR]
    assert not missing, "no Arabic for:\n" + "\n".join(missing)


def test_arabic_keeps_the_same_placeholders_as_english():
    bad = [k for k, v in i18n.AR.items() if set(PLACEHOLDER.findall(k)) != set(PLACEHOLDER.findall(v))]
    assert not bad, bad


def test_language_switch_and_unknown_text():
    assert i18n.t("Stop") == "Stop"
    i18n.set_lang("ar")
    assert i18n.t("Stop").endswith("إيقاف") and i18n.t("Stop").startswith(i18n.RLM)
    assert i18n.t("never seen before") == "never seen before"
    assert i18n.anchor() == "e" and i18n.justify() == "right"
    i18n.set_lang("fr")
    assert i18n.LANG == "en" and i18n.anchor() == "w"


def test_core_messages_are_translated_when_arabic_and_untouched_in_english():
    msg = "90 photos, 12 groups: 10 selected, 20 alternates, 3 blurry group(s) skipped."
    assert i18n.tr_msg(msg) == msg
    i18n.set_lang("ar")
    out = i18n.tr_msg(msg)
    assert "10" in out and "12" in out and "photos" not in out
    assert i18n.tr_msg("Some message nobody translated") == "Some message nobody translated"
    assert i18n.tr_msg("Edited 5 of 5 with Warm.") .endswith("Warm.")


def test_settings_remember_the_language(tmp_path):
    from photocull.gui.settings import UserSettings

    s = UserSettings(tmp_path / "s.json")
    assert s["language"] == "en"
    s["language"] = "ar"
    s.save()
    assert UserSettings(tmp_path / "s.json")["language"] == "ar"


def test_layout_helpers_mirror_only_in_arabic():
    assert (i18n.side("left"), i18n.sticky("nsw"), i18n.px(1, 9), i18n.mcol(0, 5)) == \
        ("left", "nsw", (1, 9), 0)
    i18n.set_lang("ar")
    assert (i18n.side("left"), i18n.side("right"), i18n.side("top")) == ("right", "left", "top")
    assert i18n.sticky("nsw") == "nse" and i18n.sticky("ew") == "we" and i18n.sticky("n") == "n"
    assert i18n.px(1, 9) == (9, 1)
    assert [i18n.mcol(i, 3) for i in range(3)] == [2, 1, 0]
