"""Load and validate config.toml into typed dataclasses.

Unknown keys are rejected so typos never silently fall back to defaults.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = APP_DIR / "config.toml"


class ConfigError(ValueError):
    """Raised when config.toml is malformed or has invalid values."""


@dataclass
class Grouping:
    group_gap_seconds: float = 2.0
    max_group_size: int = 12
    keep_alternates: int = 2


@dataclass
class Weights:
    """Weights and penalties of one scoring mode (portrait or action)."""
    w_sharpness: float = 0.35
    w_exposure: float = 0.25
    w_eyes: float = 0.30
    w_contrast: float = 0.10
    closed_eyes_penalty: float = 0.30
    blown_penalty: float = 0.15
    crushed_penalty: float = 0.05


@dataclass
class Scoring:
    portrait: Weights = field(default_factory=Weights)
    action: Weights = field(
        default_factory=lambda: Weights(0.55, 0.25, 0.10, 0.10, closed_eyes_penalty=0.05)
    )


@dataclass
class Detection:
    analysis_max_side: int = 1280
    min_face_fraction: float = 0.08
    ear_closed_threshold: float = 0.16
    eye_focus_ratio: float = 0.5
    target_brightness_low: float = 70
    target_brightness_high: float = 170
    max_blown_pct: float = 3.0
    max_crushed_pct: float = 10.0
    face_confidence: float = 0.6
    max_eye_faces: int = 12
    blurry_sharpness: float = 15.0
    blurry_relative: float = 0.5
    min_contrast: float = 25.0
    exposure_falloff: float = 60.0
    exposure_flag_margin: float = 30.0


@dataclass
class ExposureCorrection:
    enabled: bool = True
    target_brightness: float = 120
    max_stops: float = 1.0
    tolerance: float = 8


@dataclass
class Photoshop:
    styles_folder: str = "Styles"
    default_style_file: str = ""
    default_action: str = ""
    styles: dict[str, str] = field(default_factory=dict)
    jpeg_quality: int = 92
    retry_attempts: int = 3


@dataclass
class Split:
    max_per_post: int = 20


@dataclass
class Config:
    grouping: Grouping = field(default_factory=Grouping)
    scoring: Scoring = field(default_factory=Scoring)
    detection: Detection = field(default_factory=Detection)
    exposure_correction: ExposureCorrection = field(default_factory=ExposureCorrection)
    photoshop: Photoshop = field(default_factory=Photoshop)
    split: Split = field(default_factory=Split)


def _build(cls: type, data: dict[str, Any], where: str):
    if not isinstance(data, dict):
        raise ConfigError(f"[{where}] must be a table")
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"[{where}] unknown key(s): {', '.join(sorted(unknown))}")
    return cls(**data)


def _build_scoring(data: dict[str, Any]) -> Scoring:
    unknown = set(data) - {"portrait", "action"}
    if unknown:
        raise ConfigError(f"[scoring] unknown key(s): {', '.join(sorted(unknown))}")
    defaults = Scoring()
    return Scoring(
        portrait=_build(Weights, data["portrait"], "scoring.portrait")
        if "portrait" in data else defaults.portrait,
        action=_build(Weights, data["action"], "scoring.action")
        if "action" in data else defaults.action,
    )


def _num(where: str, name: str, value: Any, lo: float | None = None,
         hi: float | None = None, integer: bool = False) -> None:
    ok_type = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, ok_type):
        raise ConfigError(f"[{where}] {name} must be {'an integer' if integer else 'a number'}")
    if lo is not None and value < lo:
        raise ConfigError(f"[{where}] {name} must be >= {lo} (got {value})")
    if hi is not None and value > hi:
        raise ConfigError(f"[{where}] {name} must be <= {hi} (got {value})")


def validate(cfg: Config) -> Config:
    g = cfg.grouping
    _num("grouping", "group_gap_seconds", g.group_gap_seconds, lo=0.05)
    _num("grouping", "max_group_size", g.max_group_size, lo=1, integer=True)
    _num("grouping", "keep_alternates", g.keep_alternates, lo=0, integer=True)

    for name in ("portrait", "action"):
        w = getattr(cfg.scoring, name)
        vals = [w.w_sharpness, w.w_exposure, w.w_eyes, w.w_contrast]
        for f in fields(w):
            _num(f"scoring.{name}", f.name, getattr(w, f.name), lo=0, hi=10)
        if sum(vals) <= 0:
            raise ConfigError(f"[scoring.{name}] weights must not all be zero")

    d = cfg.detection
    _num("detection", "analysis_max_side", d.analysis_max_side, lo=64, integer=True)
    _num("detection", "min_face_fraction", d.min_face_fraction, lo=0, hi=1)
    _num("detection", "ear_closed_threshold", d.ear_closed_threshold, lo=0, hi=1)
    _num("detection", "eye_focus_ratio", d.eye_focus_ratio, lo=0, hi=1)
    _num("detection", "target_brightness_low", d.target_brightness_low, lo=0, hi=255)
    _num("detection", "target_brightness_high", d.target_brightness_high, lo=0, hi=255)
    if d.target_brightness_low > d.target_brightness_high:
        raise ConfigError("[detection] target_brightness_low must be <= target_brightness_high")
    _num("detection", "max_blown_pct", d.max_blown_pct, lo=0, hi=100)
    _num("detection", "max_crushed_pct", d.max_crushed_pct, lo=0, hi=100)
    _num("detection", "face_confidence", d.face_confidence, lo=0.05, hi=1)
    _num("detection", "max_eye_faces", d.max_eye_faces, lo=0, integer=True)
    _num("detection", "blurry_sharpness", d.blurry_sharpness, lo=0)
    _num("detection", "blurry_relative", d.blurry_relative, lo=0, hi=1)
    _num("detection", "min_contrast", d.min_contrast, lo=0, hi=255)
    _num("detection", "exposure_falloff", d.exposure_falloff, lo=1, hi=255)
    _num("detection", "exposure_flag_margin", d.exposure_flag_margin, lo=0, hi=255)

    e = cfg.exposure_correction
    if not isinstance(e.enabled, bool):
        raise ConfigError("[exposure_correction] enabled must be true or false")
    _num("exposure_correction", "target_brightness", e.target_brightness, lo=0, hi=255)
    _num("exposure_correction", "max_stops", e.max_stops, lo=0, hi=3)
    _num("exposure_correction", "tolerance", e.tolerance, lo=0, hi=255)

    p = cfg.photoshop
    _num("photoshop", "jpeg_quality", p.jpeg_quality, lo=1, hi=100, integer=True)
    _num("photoshop", "retry_attempts", p.retry_attempts, lo=1, hi=10, integer=True)
    for key in ("styles_folder", "default_style_file", "default_action"):
        if not isinstance(getattr(p, key), str):
            raise ConfigError(f"[photoshop] {key} must be a string")
    if Path(p.styles_folder).is_absolute() or Path(p.styles_folder).drive:
        raise ConfigError("[photoshop] styles_folder must be a relative path (portability)")

    _num("split", "max_per_post", cfg.split.max_per_post, lo=1, integer=True)
    return cfg


def load_config(path: Path | str | None = None) -> Config:
    """Load config.toml (defaults if the file is absent) and validate it."""
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        return validate(Config())
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: invalid TOML: {exc}") from exc

    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown section(s): {', '.join(sorted(unknown))}")

    kwargs: dict[str, Any] = {}
    for name in known - {"scoring"}:
        if name in raw:
            cls = next(f for f in fields(Config) if f.name == name).default_factory
            kwargs[name] = _build(cls, raw[name], name)
    if "scoring" in raw:
        kwargs["scoring"] = _build_scoring(raw["scoring"])
    return validate(Config(**kwargs))
