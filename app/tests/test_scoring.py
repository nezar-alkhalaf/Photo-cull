import numpy as np
import pytest
from PIL import Image

from photocull.config import Config, Weights
from photocull.faces import Face, NullDetector, eye_aspect_ratio
from photocull.scoring import (FaceInfo, Measurement, exposure_score, load_analysis_image,
                               measure, rank_group, score_group, subject_region)
from synth import FakeDetector, blurred, flat, texture, with_eyes

CFG = Config()
DET = CFG.detection


def m_of(img, detector=None):
    return measure(img, detector or NullDetector(), DET)


def m(sharp=400.0, mean=120.0, blown=0.0, crushed=0.0, contrast=60.0, ears=()):
    return Measurement(sharp, mean, blown, crushed, contrast, len(ears),
                       [FaceInfo(0.3, e, e) for e in ears])


# ---------- measurements ----------

def test_sharp_beats_blurred():
    base = texture()
    vals = [m_of(blurred(base, s)).sharpness for s in (0, 1.5, 4)]
    assert vals[0] > vals[1] > vals[2]


def test_brightness_flags():
    assert m_of(flat(30)).mean_brightness == pytest.approx(30, abs=1)
    assert m_of(flat(255)).blown_pct == 100
    assert m_of(flat(0)).crushed_pct == 100
    assert m_of(flat(128)).contrast == pytest.approx(0, abs=0.01)
    assert m_of(texture()).contrast > 40


def test_sharpness_measured_on_face_region_when_found():
    # Sharp face area, blurred everywhere else vs. the opposite.
    sharp, soft = texture(), blurred(texture(), 4)
    x, y, w, h = 240, 140, 160, 200
    face_sharp = soft.copy(); face_sharp[y:y + h, x:x + w] = sharp[y:y + h, x:x + w]
    face_soft = sharp.copy(); face_soft[y:y + h, x:x + w] = soft[y:y + h, x:x + w]
    fd = FakeDetector()
    a = m_of(with_eyes(face_sharp, "open"), fd)
    b = m_of(with_eyes(face_soft, "open"), fd)
    assert a.sharpness > 2 * b.sharpness
    assert a.face_count == 1 and a.faces[0].ear == pytest.approx(0.30)


def test_small_face_not_judged_for_eyes():
    cfg = Config()
    cfg.detection.min_face_fraction = 0.9            # face box is 200/480 = 0.42
    res = measure(with_eyes(texture(), "closed"), FakeDetector(), cfg.detection)
    assert res.face_count == 1 and res.faces == []


def test_subject_region():
    assert subject_region([], 640, 480) == (160, 120, 480, 360)
    x0, y0, x1, y1 = subject_region([Face((100, 100, 100, 100))], 640, 480)
    assert (x0, y0, x1, y1) == (90, 90, 210, 210)    # padded 10% a side
    # a tiny face is widened to at least 15% of the short side
    x0, y0, x1, y1 = subject_region([Face((300, 200, 4, 4))], 640, 480)
    assert x1 - x0 >= 71 and y1 - y0 >= 71
    # background faces (< half the largest) do not stretch the region
    r = subject_region([Face((100, 100, 100, 100)), Face((500, 400, 20, 20))], 640, 480)
    assert r[2] <= 210


def test_ear():
    open_eye = np.array([(0, 0), (3, -2), (7, -2), (10, 0), (7, 2), (3, 2)], float)
    shut_eye = np.array([(0, 0), (3, -0.3), (7, -0.3), (10, 0), (7, 0.3), (3, 0.3)], float)
    assert eye_aspect_ratio(open_eye) == pytest.approx(0.4)
    assert eye_aspect_ratio(shut_eye) < DET.ear_closed_threshold


def test_measurement_roundtrip():
    x = m(ears=[0.3, 0.1])
    assert Measurement.from_dict(x.to_dict()) == x


def test_analysis_image_downscaled_and_upright(tmp_path):
    p = tmp_path / "big.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6                                  # rotate 90 CW on display
    Image.new("RGB", (4000, 3000), (90, 90, 90)).save(p, "JPEG", exif=exif)
    arr = load_analysis_image(p, 1280)
    assert arr.shape[:2] == (1280, 960)               # portrait after transpose


# ---------- scoring ----------

def test_exposure_score_band():
    assert exposure_score(120, DET) == 1
    assert exposure_score(100, DET) == 1
    assert exposure_score(70, DET) == pytest.approx(0.5)
    assert exposure_score(20, DET) == 0
    assert exposure_score(200, DET) == 0


def test_sharpest_wins_in_action_mode():
    ms = [m(sharp=100), m(sharp=900), m(sharp=400)]
    s = score_group(ms, CFG.scoring.action, DET)
    assert rank_group(ms, s) == [1, 2, 0]


def test_near_identical_frames_score_close():
    s = score_group([m(sharp=500), m(sharp=505)], CFG.scoring.portrait, DET)
    assert abs(s[0].total - s[1].total) < 0.01


def test_closed_eyes_lose_in_portrait_but_not_action():
    ms = [m(sharp=900, ears=[0.1]), m(sharp=400, ears=[0.3])]
    portrait = score_group(ms, CFG.scoring.portrait, DET)
    action = score_group(ms, CFG.scoring.action, DET)
    assert rank_group(ms, portrait)[0] == 1
    assert rank_group(ms, action)[0] == 0
    assert portrait[0].eyes_state == "closed" and "closed_eyes" in portrait[0].reasons
    assert portrait[1].eyes_state == "open"


def test_all_closed_group_not_penalised():
    ms = [m(sharp=900, ears=[0.1]), m(sharp=700, ears=[0.1])]
    s = score_group(ms, CFG.scoring.portrait, DET)
    assert rank_group(ms, s)[0] == 0
    no_eyes = score_group([m(sharp=900), m(sharp=700)], CFG.scoring.portrait, DET)
    assert s[0].total == pytest.approx(no_eyes[0].total - CFG.scoring.portrait.w_eyes
                                       / sum([.35, .25, .30, .10]), abs=1e-3)


def test_group_photo_fewest_blinks_wins():
    ms = [m(ears=[0.3] * 9 + [0.1]), m(ears=[0.3] * 7 + [0.1] * 3), m(ears=[0.3] * 10)]
    s = score_group(ms, CFG.scoring.portrait, DET)
    assert rank_group(ms, s) == [2, 0, 1]


def test_unknown_eyes_are_not_penalised():
    ms = [m(sharp=900), m(sharp=700, ears=[0.3])]
    s = score_group(ms, CFG.scoring.portrait, DET)
    assert s[0].eyes_state == "unknown" and rank_group(ms, s)[0] == 0


def test_blown_highlights_penalised():
    ms = [m(sharp=900, blown=20), m(sharp=800)]
    s = score_group(ms, CFG.scoring.action, DET)
    assert rank_group(ms, s)[0] == 1
    assert "overexposed" in s[0].reasons


@pytest.mark.parametrize("kw,reason", [
    (dict(sharp=5), "blurry"),
    (dict(mean=40), "underexposed"),
    (dict(crushed=50), "underexposed"),
    (dict(mean=220), "overexposed"),
    (dict(contrast=10), "low_contrast"),
])
def test_reason_tokens(kw, reason):
    [s] = score_group([m(**kw)], CFG.scoring.portrait, DET)
    assert reason in s.reasons


def test_relatively_blurry_frame_flagged():
    s = score_group([m(sharp=1000), m(sharp=300)], CFG.scoring.action, DET)
    assert "blurry" not in s[0].reasons and "blurry" in s[1].reasons


def test_zero_weight_metric_is_ignored():
    w = Weights(1, 0, 0, 0, 0, 0, 0)
    ms = [m(sharp=900, mean=20), m(sharp=100)]
    assert rank_group(ms, score_group(ms, w, DET))[0] == 0
