import json

import pytest

from photocull.config import Config
from photocull.gui.settings import (DEFAULTS, SettingsError, UserSettings, build_config,
                                    max_per_post, preview_count)
from photocull.gui.thumbs import LRU, file_key, make_thumb
from synth import save_jpeg, texture


def test_settings_roundtrip_and_damaged_file(tmp_path):
    p = tmp_path / "settings.json"
    s = UserSettings(p)
    assert s["exposure"] is None and s["mode"] == "portrait"
    s["main_folder"] = r"D:\تغطية"
    s["exposure"] = False
    s.save()
    again = UserSettings(p)
    assert again["main_folder"] == r"D:\تغطية" and again["exposure"] is False
    p.write_text("{ not json", encoding="utf-8")
    assert UserSettings(p)["mode"] == "portrait"                      # damaged -> defaults
    p.write_text(json.dumps({"mode": "action", "bogus": 1}), encoding="utf-8")
    s = UserSettings(p)
    assert s["mode"] == "action" and "bogus" not in s.data


def test_settings_unwritable_location_does_not_crash(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    UserSettings(blocker / "sub" / "settings.json").save()


def test_build_config_overrides_only_what_was_typed():
    base = Config()
    s = dict(DEFAULTS)
    assert build_config(base, s) == base
    s.update(mode="action", gap_action="0.8", blurry_sharpness="20", keep_alternates="1",
             skip_blurry_groups=False, jpeg_quality="85")
    cfg = build_config(base, s)
    assert cfg.grouping.action_group_gap_seconds == 0.8
    assert cfg.grouping.group_gap_seconds == base.grouping.group_gap_seconds
    assert cfg.detection.blurry_sharpness == 20 and cfg.grouping.keep_alternates == 1
    assert cfg.grouping.skip_blurry_groups is False and cfg.photoshop.jpeg_quality == 85
    assert base.grouping.action_group_gap_seconds == 0.6              # base untouched


@pytest.mark.parametrize("key,val", [("gap_portrait", "abc"), ("gap_portrait", "0"),
                                     ("jpeg_quality", "500"), ("keep_alternates", "-1")])
def test_bad_values_give_a_friendly_error(key, val):
    s = dict(DEFAULTS)
    s[key] = val
    with pytest.raises(SettingsError):
        build_config(Config(), s)


def test_max_per_post_and_preview_parsing():
    cfg = Config()
    s = dict(DEFAULTS)
    assert max_per_post(s, cfg) == 20 and preview_count(s) == 0
    s.update(max_per_post="15", preview="5")
    assert max_per_post(s, cfg) == 15 and preview_count(s) == 5
    for bad in ("0", "x", "-3"):
        s["max_per_post"] = bad
        with pytest.raises(SettingsError):
            max_per_post(s, cfg)


def test_lru_evicts_oldest():
    c = LRU(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")
    c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1 and len(c) == 2


def test_thumbnail_is_small_and_changes_key_when_file_changes(tmp_path):
    p = save_jpeg(tmp_path / "ص.jpg", texture(1))                    # 640x480
    im = make_thumb(p, 160)
    assert im.size == (160, 120)
    k1 = file_key(p, 160)
    p.write_bytes(p.read_bytes() + b"\0")
    assert file_key(p, 160) != k1                                    # edited in place -> new key


def test_damaged_values_in_the_settings_file_fall_back_to_defaults(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"mode": "x", "appearance": 7, "main_folder": 5, "max_per_post": 15, '
                 '"last_update_check": "soon", "language": "ar"}', encoding="utf-8")
    s = UserSettings(p)
    assert s["mode"] == "portrait" and s["appearance"] == "dark"
    assert s["main_folder"] == "5" and s["max_per_post"] == "15"     # numbers become text
    assert s["last_update_check"] == 0.0 and s["language"] == "ar"
    s["mode"] = "nonsense"
    assert s["mode"] == "portrait"
