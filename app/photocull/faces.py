"""Face boxes and eye openness.

Two models, both loaded from bytes (OpenCV and MediaPipe cannot open files
whose path contains Arabic characters on Windows):

  * OpenCV YuNet finds face boxes. It works at any input size, so it finds
    the small faces of class and match photos. MediaPipe's own detector is a
    short-range model that misses faces smaller than ~15% of the frame.
  * MediaPipe FaceLandmarker (Tasks API, mediapipe==1.0.1) runs on a crop
    around each face big enough to judge, and gives the eye landmarks for EAR.

If either model or package is missing, detect() degrades: no faces (subject
falls back to the frame centre) or faces without eye data (eyes "unknown").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .paths import APP_DIR

MODELS_DIR = APP_DIR / "models"
YUNET_MODEL = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
LANDMARKER_MODEL = MODELS_DIR / "face_landmarker.task"

# FaceMesh indices (unchanged in the 478-point FaceLandmarker topology):
# p1, p2, p3, p4, p5, p6 with p1/p4 the eye corners.
LEFT_EYE = (33, 160, 158, 133, 153, 144)
RIGHT_EYE = (362, 385, 387, 263, 373, 380)

log = logging.getLogger("photocull")


@dataclass
class Face:
    box: tuple[int, int, int, int]       # x, y, w, h in analysis-image pixels
    score: float = 1.0
    ear_left: float | None = None        # None = eyes not judged
    ear_right: float | None = None

    @property
    def ear(self) -> float | None:
        if self.ear_left is None or self.ear_right is None:
            return None
        return (self.ear_left + self.ear_right) / 2


class FaceDetector(Protocol):
    name: str

    def detect(self, rgb: np.ndarray, min_eye_face_px: int) -> list[Face]:
        """Faces in an RGB uint8 image. Eyes are judged only for faces whose
        box height is >= min_eye_face_px."""
        ...


class NullDetector:
    name = "none"

    def detect(self, rgb: np.ndarray, min_eye_face_px: int) -> list[Face]:
        return []


def eye_aspect_ratio(pts: np.ndarray) -> float:
    """EAR = (|p2-p6| + |p3-p5|) / (2 |p1-p4|) for six (x, y) points."""
    p1, p2, p3, p4, p5, p6 = pts
    horiz = np.linalg.norm(p1 - p4)
    if horiz <= 1e-6:
        return 0.0
    return float((np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)) / (2.0 * horiz))


class MediaPipeDetector:
    """YuNet boxes + FaceLandmarker eyes. Create once per batch."""

    name = "yunet+mediapipe-1.0.1"

    def __init__(self, confidence: float = 0.6, max_eye_faces: int = 12):
        import cv2

        self._cv2 = cv2
        self.confidence = confidence
        self.max_eye_faces = max_eye_faces
        buf = np.frombuffer(YUNET_MODEL.read_bytes(), dtype=np.uint8)
        self._yunet = cv2.FaceDetectorYN.create(
            "onnx", buf, np.empty(0, np.uint8), (320, 320), confidence, 0.3, 5000)
        self._landmarker = None
        try:
            self._landmarker = self._make_landmarker()
        except Exception as exc:          # eyes become "unknown", boxes still work
            log.warning("Eye detection unavailable (%s); eyes will be 'unknown'.", exc)
            self.name = "yunet-only"

    @staticmethod
    def _make_landmarker():
        from mediapipe.tasks.python import BaseOptions, vision

        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=LANDMARKER_MODEL.read_bytes()),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.4,
            min_face_presence_confidence=0.4,
        )
        return vision.FaceLandmarker.create_from_options(opts)

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def detect(self, rgb: np.ndarray, min_eye_face_px: int) -> list[Face]:
        h, w = rgb.shape[:2]
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        self._yunet.setInputSize((w, h))
        _, rows = self._yunet.detect(bgr)
        faces: list[Face] = []
        if rows is None:
            return faces
        for r in rows:
            x, y, fw, fh = (int(round(v)) for v in r[:4])
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + fw), min(h, y + fh)
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            faces.append(Face((x0, y0, x1 - x0, y1 - y0), float(r[-1])))
        faces.sort(key=lambda f: f.box[3], reverse=True)

        if self._landmarker is not None:
            judged = 0
            for f in faces:
                if f.box[3] < min_eye_face_px or judged >= self.max_eye_faces:
                    break
                judged += 1
                try:
                    f.ear_left, f.ear_right = self._eyes(rgb, f.box)
                except Exception as exc:
                    log.debug("landmarker failed on a face: %s", exc)
        return faces

    def _eyes(self, rgb: np.ndarray, box) -> tuple[float | None, float | None]:
        """Landmark a square crop (2x the face box) so the face fills it."""
        import mediapipe as mp

        h, w = rgb.shape[:2]
        x, y, fw, fh = box
        cx, cy = x + fw / 2, y + fh / 2
        half = max(fw, fh)
        x0, y0 = int(max(0, cx - half)), int(max(0, cy - half))
        x1, y1 = int(min(w, cx + half)), int(min(h, cy + half))
        crop = rgb[y0:y1, x0:x1]
        side = max(crop.shape[:2])
        if side < 192:                    # landmarker works best on >=192 px faces
            s = 192 / side
            crop = self._cv2.resize(crop, None, fx=s, fy=s,
                                    interpolation=self._cv2.INTER_CUBIC)
        crop = np.ascontiguousarray(crop)
        res = self._landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=crop))
        if not res.face_landmarks:
            return None, None
        ch, cw = crop.shape[:2]
        lm = res.face_landmarks[0]
        pts = np.array([(p.x * cw, p.y * ch) for p in lm], dtype=np.float64)
        return (eye_aspect_ratio(pts[list(LEFT_EYE)]),
                eye_aspect_ratio(pts[list(RIGHT_EYE)]))


def make_detector(confidence: float = 0.6, max_eye_faces: int = 12) -> FaceDetector:
    """Best available detector; NullDetector (with a warning) if none loads."""
    try:
        if not YUNET_MODEL.is_file():
            raise FileNotFoundError(f"missing model file {YUNET_MODEL.name}")
        return MediaPipeDetector(confidence, max_eye_faces)
    except Exception as exc:
        log.warning("Face detection unavailable (%s); using the frame centre instead.", exc)
        return NullDetector()
