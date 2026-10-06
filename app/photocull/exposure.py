"""Automatic exposure correction before Photoshop (plan 7.3 step 2).

Brightness is measured like step 1 does (faces if found, else a
centre-weighted mean), then the photo is pushed toward target_brightness by
a gain in linear light, capped at +/- max_stops. The change is a 256-entry
lookup table applied with Pillow, so a 26 MP photo never becomes a float
array (low-power PC). A soft shoulder keeps brightened highlights from
clipping hard.
"""

from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .config import Config
from .faces import FaceDetector
from .scoring import load_analysis_image, measure

KNEE = 0.8                  # linear level where the highlight shoulder starts


def srgb_to_linear(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64) / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(v: np.ndarray) -> np.ndarray:
    v = np.clip(v, 0.0, 1.0)
    s = np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)
    return s * 255.0


def stops_needed(mean: float, target: float, max_stops: float, tolerance: float) -> float:
    """Exposure change in stops (0 when already within tolerance), capped."""
    if abs(mean - target) <= tolerance or mean <= 0:
        return 0.0
    lin_m = float(srgb_to_linear(np.array([mean]))[0])
    lin_t = float(srgb_to_linear(np.array([target]))[0])
    if lin_m <= 0:
        return max_stops
    return max(-max_stops, min(max_stops, math.log2(lin_t / lin_m)))


def exposure_lut(stops: float) -> list[int]:
    """256-entry table: gain of 2**stops in linear light with a soft shoulder."""
    lin = srgb_to_linear(np.arange(256)) * (2.0 ** stops)
    if stops > 0:
        over = lin > KNEE
        lin[over] = KNEE + (1 - KNEE) * (1 - np.exp(-(lin[over] - KNEE) / (1 - KNEE)))
    return [int(round(x)) for x in linear_to_srgb(lin)]


@dataclass
class ExposureResult:
    measured: float
    stops: float
    changed: bool


def correct_exposure(src: Path, dst: Path, cfg: Config, detector: FaceDetector,
                     ) -> ExposureResult:
    """Write the working copy dst from src, exposure-corrected if needed.

    src is only read. EXIF, ICC profile and orientation tag are kept.
    """
    ec, det = cfg.exposure_correction, cfg.detection
    rgb = load_analysis_image(src, det.analysis_max_side)
    m = measure(rgb, detector, det)
    del rgb
    stops = stops_needed(m.mean_brightness, ec.target_brightness, ec.max_stops, ec.tolerance)
    if stops == 0.0:
        shutil.copy2(src, dst)
        return ExposureResult(m.mean_brightness, 0.0, False)

    with Image.open(src) as im:
        info = {k: im.info[k] for k in ("exif", "icc_profile") if k in im.info}
        out = im.convert("RGB").point(exposure_lut(stops) * 3)
    out.save(dst, "JPEG", quality=95, subsampling=0, **info)
    shutil.copystat(src, dst)                  # keep the file date (split falls back to it)
    return ExposureResult(m.mean_brightness, stops, True)
