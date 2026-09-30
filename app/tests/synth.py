"""Synthetic test photos and a fake face detector (no real photos or models needed)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from photocull.faces import Face

W, H = 640, 480
FACE_BOX = (240, 140, 160, 200)          # x, y, w, h: large, centred "face"
MARK = 48                                # top-left marker block tells FakeDetector the eyes


def texture(seed: int = 1) -> np.ndarray:
    """Blocky random texture: lots of hard edges, mid-grey mean, good contrast."""
    rng = np.random.default_rng(seed)
    small = rng.integers(30, 226, (H // 8, W // 8), dtype=np.uint8)
    g = cv2.resize(small, (W, H), interpolation=cv2.INTER_NEAREST)
    return np.dstack([g, g, g])


def blurred(img: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(img, (0, 0), sigma) if sigma > 0 else img.copy()


def flat(level: int = 128) -> np.ndarray:
    return np.full((H, W, 3), level, np.uint8)


def with_eyes(img: np.ndarray, eyes: str | None) -> np.ndarray:
    """Paint the marker: 'open' -> black block, 'closed' -> white block, None -> no face."""
    img = img.copy()
    if eyes == "open":
        img[:MARK, :MARK] = 0
    elif eyes == "closed":
        img[:MARK, :MARK] = 255
    return img


def save_jpeg(path: Path, arr: np.ndarray, when: datetime | None = None,
              sub: str | None = None) -> Path:
    img = Image.fromarray(arr)
    if when is None:
        img.save(path, "JPEG", quality=95)
        return path
    exif = Image.Exif()
    ifd = exif.get_ifd(0x8769)
    ifd[36867] = when.strftime("%Y:%m:%d %H:%M:%S")
    if sub:
        ifd[37521] = sub
    img.save(path, "JPEG", quality=95, exif=exif)
    return path


class FakeDetector:
    """Reads the marker block painted by with_eyes(). Counts calls."""

    name = "fake"

    def __init__(self):
        self.calls = 0

    def detect(self, rgb: np.ndarray, min_eye_face_px: int) -> list[Face]:
        self.calls += 1
        h, w = rgb.shape[:2]
        s = w / W                                    # analysis image may be scaled
        mark = float(rgb[: int(MARK * s) - 4, : int(MARK * s) - 4].mean())
        if 60 < mark < 200:
            return []
        box = tuple(int(v * s) for v in FACE_BOX)
        ear = 0.30 if mark <= 60 else 0.10
        if box[3] < min_eye_face_px:
            return [Face(box)]
        return [Face(box, 1.0, ear, ear)]
