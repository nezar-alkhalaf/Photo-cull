"""User settings: last folder, mode, style, exposure switch, thresholds.

Stored in app/settings.json (never in %APPDATA%) so the whole project moves
as one folder (plan section 17). Everything here works without a display.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ..config import Config, ConfigError, validate
from ..paths import APP_DIR
from ..state import atomic_write_json

SETTINGS_PATH = APP_DIR / "settings.json"

DEFAULTS: dict = {
    "main_folder": "",
    "mode": "portrait",
    "style": "",
    "exposure": None,            # None = config default; the checkbox remembers the last choice
    "max_per_post": "",          # "" = config default
    "appearance": "dark",
    "gap_portrait": "",          # "" = config default (seconds)
    "gap_action": "",
    "blurry_sharpness": "",
    "ear_closed_threshold": "",
    "keep_alternates": "",
    "skip_blurry_groups": None,
    "jpeg_quality": "",
    "action": "",
    "preview": "",
    "cull_input": "",
    "edit_input": "",
    "split_input": "",
    "advanced_open": False,
    "text_scale": 1.3,          # A- / A+ buttons in the sidebar; 1.0 = the old, smaller size
    "auto_update_check": True,   # look for a newer version once a day (Updates page)
    "last_update_check": 0.0,
    "skipped_version": "",
    "language": "en",           # "en" or "ar": the button at the top of the window
}


class SettingsError(ValueError):
    """A setting the user typed is not usable; the message says which and why."""


class UserSettings:
    def __init__(self, path: Path | str = SETTINGS_PATH):
        self.path = Path(path)
        self.data = dict(DEFAULTS)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update({k: v for k, v in loaded.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass                                  # missing or damaged: start from defaults

    def __getitem__(self, key: str):
        return self.data[key]

    def __setitem__(self, key: str, value) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        self.data[key] = value

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, self.data)
        except OSError:
            pass                                  # a read-only drive must not stop the app


def _number(label: str, raw, kind: type, lo: float | None = None):
    text = str(raw).strip().replace(",", ".")
    try:
        value = kind(float(text)) if kind is int else float(text)
    except ValueError:
        raise SettingsError(f"{label}: '{raw}' is not a number.") from None
    if lo is not None and value < lo:
        raise SettingsError(f"{label} must be at least {lo:g}.")
    return value


def max_per_post(s: UserSettings | dict, cfg: Config) -> int:
    raw = s["max_per_post"]
    if str(raw).strip() == "":
        return cfg.split.max_per_post
    return _number("Max images per post", raw, int, 1)


def preview_count(s: UserSettings | dict) -> int:
    raw = s["preview"]
    return 0 if str(raw).strip() == "" else _number("Preview", raw, int, 0)


def build_config(base: Config, s: UserSettings | dict) -> Config:
    """The config for this run: config.toml values with the user's overrides on top."""
    g, d, p = base.grouping, base.detection, base.photoshop
    gap_key = "gap_action" if s["mode"] == "action" else "gap_portrait"
    if str(s[gap_key]).strip() != "":
        gap = _number("Group time gap", s[gap_key], float, 0.05)
        g = replace(g, **{"action_group_gap_seconds" if s["mode"] == "action"
                          else "group_gap_seconds": gap})
    if str(s["keep_alternates"]).strip() != "":
        g = replace(g, keep_alternates=_number("Alternates per moment", s["keep_alternates"], int, 0))
    if s["skip_blurry_groups"] is not None:
        g = replace(g, skip_blurry_groups=bool(s["skip_blurry_groups"]))
    if str(s["blurry_sharpness"]).strip() != "":
        d = replace(d, blurry_sharpness=_number("Blur threshold", s["blurry_sharpness"], float, 0))
    if str(s["ear_closed_threshold"]).strip() != "":
        d = replace(d, ear_closed_threshold=_number("Closed-eye threshold",
                                                    s["ear_closed_threshold"], float, 0))
    if str(s["jpeg_quality"]).strip() != "":
        p = replace(p, jpeg_quality=_number("JPEG quality", s["jpeg_quality"], int, 1))
    cfg = replace(base, grouping=g, detection=d, photoshop=p)
    try:
        return validate(cfg)
    except ConfigError as exc:
        raise SettingsError(str(exc)) from None
