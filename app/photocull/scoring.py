"""Per-image measurements and per-group scoring (plan sections 6.3 and 6.4).

Measurements are raw numbers (cached in state.json) so thresholds and weights
can be tuned and re-scored without analysing the photos again.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .config import Detection, Weights
from .faces import Face, FaceDetector

MODES = ("portrait", "action")
REASONS = ("blurry", "closed_eyes", "overexposed", "underexposed", "low_contrast",
           "not_best_in_group")


# ---------------------------------------------------------------- measuring

@dataclass
class FaceInfo:
    height_frac: float                    # face box height / frame height
    ear_left: float | None = None
    ear_right: float | None = None

    @property
    def ear(self) -> float | None:
        if self.ear_left is None or self.ear_right is None:
            return None
        return (self.ear_left + self.ear_right) / 2


@dataclass
class Measurement:
    sharpness: float                      # Laplacian variance over the subject region
    mean_brightness: float                # centre-weighted, 0-255
    blown_pct: float                      # % of subject pixels >= 250
    crushed_pct: float                    # % of subject pixels <= 5
    contrast: float                       # std of brightness, whole frame
    face_count: int = 0
    faces: list[FaceInfo] = field(default_factory=list)   # faces whose eyes were judged

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Measurement":
        d = dict(d)
        d["faces"] = [FaceInfo(**f) for f in d.get("faces", [])]
        return cls(**d)


def load_analysis_image(path: Path | str, max_side: int) -> np.ndarray:
    """Upright RGB uint8 array, longest side <= max_side.

    draft() lets the JPEG decoder skip most of the full-size work, so a 24 MP
    photo never sits in memory at full resolution.
    """
    with Image.open(path) as img:
        img.draft("RGB", (max_side, max_side))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
        return np.asarray(img).copy()


def subject_region(faces: list[Face], w: int, h: int) -> tuple[int, int, int, int]:
    """x0, y0, x1, y1 of the subject: main faces padded 20% (10% a side), else central 50%."""
    if faces:
        largest = max(f.box[3] for f in faces)
        main = [f for f in faces if f.box[3] >= 0.5 * largest]
        x0 = min(f.box[0] for f in main)
        y0 = min(f.box[1] for f in main)
        x1 = max(f.box[0] + f.box[2] for f in main)
        y1 = max(f.box[1] + f.box[3] for f in main)
        px, py = 0.1 * (x1 - x0), 0.1 * (y1 - y0)
        x0, y0, x1, y1 = x0 - px, y0 - py, x1 + px, y1 + py
        # Tiny faces give a noisy sharpness value: keep at least 15% of the frame.
        min_side = 0.15 * min(w, h)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        hw, hh = max((x1 - x0) / 2, min_side / 2), max((y1 - y0) / 2, min_side / 2)
        x0, x1, y0, y1 = cx - hw, cx + hw, cy - hh, cy + hh
        return (int(max(0, x0)), int(max(0, y0)), int(min(w, x1)), int(min(h, y1)))
    return (w // 4, h // 4, w - w // 4, h - h // 4)


def _centre_weights(h: int, w: int) -> np.ndarray:
    gy = np.exp(-0.5 * ((np.arange(h) - (h - 1) / 2) / (0.3 * h)) ** 2)
    gx = np.exp(-0.5 * ((np.arange(w) - (w - 1) / 2) / (0.3 * w)) ** 2)
    return np.outer(gy, gx)


def measure(rgb: np.ndarray, detector: FaceDetector, det: Detection) -> Measurement:
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    faces = detector.detect(rgb, max(1, math.ceil(det.min_face_fraction * h)))

    x0, y0, x1, y1 = subject_region(faces, w, h)
    region = gray[y0:y1, x0:x1]
    # A light blur first so high-ISO noise does not read as detail.
    lap = cv2.Laplacian(cv2.GaussianBlur(region, (3, 3), 0), cv2.CV_64F)
    wts = _centre_weights(h, w)

    return Measurement(
        sharpness=round(float(lap.var()), 3),
        mean_brightness=round(float((gray * wts).sum() / wts.sum()), 3),
        blown_pct=round(float((region >= 250).mean() * 100), 3),
        crushed_pct=round(float((region <= 5).mean() * 100), 3),
        contrast=round(float(gray.std()), 3),
        face_count=len(faces),
        faces=[FaceInfo(round(f.box[3] / h, 4),
                        None if f.ear_left is None else round(f.ear_left, 4),
                        None if f.ear_right is None else round(f.ear_right, 4))
               for f in faces if f.ear is not None],
    )


# ---------------------------------------------------------------- scoring

@dataclass
class Score:
    total: float
    eyes_state: str                       # open / closed / unknown
    reasons: list[str]
    sub: dict[str, float]


def eyes_open_fraction(m: Measurement, ear_threshold: float) -> float | None:
    judged = [f.ear for f in m.faces if f.ear is not None]
    if not judged:
        return None
    return sum(e >= ear_threshold for e in judged) / len(judged)


def exposure_score(mean: float, det: Detection) -> float:
    """1 inside the target band, falling linearly to 0 `exposure_falloff` outside it."""
    d = max(det.target_brightness_low - mean, mean - det.target_brightness_high, 0.0)
    return max(0.0, 1.0 - d / det.exposure_falloff)


def flags(m: Measurement, det: Detection, eyes: float | None,
          group_max_sharpness: float) -> list[str]:
    out = []
    if (m.sharpness < det.blurry_sharpness
            or m.sharpness < det.blurry_relative * group_max_sharpness):
        out.append("blurry")
    if eyes is not None and eyes < 1.0:
        out.append("closed_eyes")
    if (m.mean_brightness > det.target_brightness_high + det.exposure_flag_margin
            or m.blown_pct > det.max_blown_pct):
        out.append("overexposed")
    if (m.mean_brightness < det.target_brightness_low - det.exposure_flag_margin
            or m.crushed_pct > det.max_crushed_pct):
        out.append("underexposed")
    if m.contrast < det.min_contrast:
        out.append("low_contrast")
    return out


def score_group(ms: list[Measurement], wts: Weights, det: Detection) -> list[Score]:
    """Score frames of one burst against each other.

    Sharpness and contrast are ratios to the best frame in the group (not
    min-max, which would blow up tiny differences between near-identical
    frames). Exposure is absolute. Eyes = share of judged faces with open eyes;
    frames with no judged face count as open (no penalty).
    """
    if not ms:
        return []
    root = [math.sqrt(max(m.sharpness, 0.0)) for m in ms]   # ~linear in edge strength
    rmax = max(root) or 1.0
    cmax = max(m.contrast for m in ms) or 1.0
    smax = max(m.sharpness for m in ms)
    eyes = [eyes_open_fraction(m, det.ear_closed_threshold) for m in ms]
    best_eyes = max((e for e in eyes if e is not None), default=None)
    wsum = wts.w_sharpness + wts.w_exposure + wts.w_eyes + wts.w_contrast

    scores = []
    for m, r, e in zip(ms, root, eyes):
        sub = {
            "sharpness": r / rmax,
            "exposure": exposure_score(m.mean_brightness, det),
            "eyes": 1.0 if e is None else e,
            "contrast": m.contrast / cmax,
        }
        total = (wts.w_sharpness * sub["sharpness"] + wts.w_exposure * sub["exposure"]
                 + wts.w_eyes * sub["eyes"] + wts.w_contrast * sub["contrast"]) / wsum
        # Closed eyes: penalised in proportion to how much better the best frame
        # of the group is, so a group where everyone blinks everywhere is not hit.
        if e is not None and best_eyes is not None:
            total -= wts.closed_eyes_penalty * (best_eyes - e)
        if m.blown_pct > det.max_blown_pct:
            total -= wts.blown_penalty
        if m.crushed_pct > det.max_crushed_pct:
            total -= wts.crushed_penalty
        state = "unknown" if e is None else ("open" if e >= 1.0 else "closed")
        scores.append(Score(round(total, 4), state, flags(m, det, e, smax),
                            {k: round(v, 4) for k, v in sub.items()}))
    return scores


def is_weak(m: Measurement, s: Score, det: Detection) -> bool:
    """The best frame of a group still has a real problem: flag the group for review.

    Uses absolute blur only; "blurry compared with a sharper frame that has
    closed eyes" is a normal trade-off, not a weak group."""
    return (m.sharpness < det.blurry_sharpness
            or bool({"closed_eyes", "overexposed", "underexposed"} & set(s.reasons)))


def rank_group(ms: list[Measurement], scores: list[Score]) -> list[int]:
    """Indices best-first: total score, then sharpness, then capture order."""
    return sorted(range(len(ms)), key=lambda i: (-scores[i].total, -ms[i].sharpness, i))
