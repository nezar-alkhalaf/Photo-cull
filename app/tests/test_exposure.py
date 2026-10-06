import numpy as np
import pytest
from PIL import Image

from photocull.config import Config
from photocull.exposure import (correct_exposure, exposure_lut, linear_to_srgb, srgb_to_linear,
                                stops_needed)
from photocull.faces import NullDetector

CFG = Config()


def test_srgb_roundtrip():
    v = np.arange(256)
    assert np.allclose(linear_to_srgb(srgb_to_linear(v)), v, atol=1e-6)


def test_one_stop_doubles_linear_light():
    assert stops_needed(float(linear_to_srgb(np.array([0.1]))[0]),
                        float(linear_to_srgb(np.array([0.2]))[0]), 3, 0) == pytest.approx(1.0)


@pytest.mark.parametrize("mean,expected", [(10, 1.0), (250, -1.0)])
def test_correction_capped_at_max_stops(mean, expected):
    assert stops_needed(mean, 120, 1.0, 8) == expected


def test_within_tolerance_no_change():
    assert stops_needed(125, 120, 1.0, 8) == 0.0
    assert stops_needed(112, 120, 1.0, 8) == 0.0


def test_lut_is_monotonic_and_keeps_ends():
    for stops in (-1.0, -0.3, 0.4, 1.0):
        lut = exposure_lut(stops)
        assert lut == sorted(lut) and lut[0] == 0
    up = exposure_lut(1.0)                         # soft shoulder: highlights still graded
    assert up[150] < up[200] < up[255] == 255 and up[200] < 253
    assert exposure_lut(1.0)[128] > 128 > exposure_lut(-1.0)[128]


def _jpeg(path, level, exif=True):
    img = Image.new("RGB", (400, 300), (level, level, level))
    if exif:
        e = Image.Exif()
        e[0x0112] = 6
        e.get_ifd(0x8769)[36867] = "2026:10:03 12:00:00"
        img.save(path, "JPEG", quality=95, exif=e)
    else:
        img.save(path, "JPEG", quality=95)
    return path


def test_dark_photo_brightened_and_exif_kept(tmp_path):
    src, dst = _jpeg(tmp_path / "dark.jpg", 50), tmp_path / "out.jpg"
    before = src.read_bytes()
    r = correct_exposure(src, dst, CFG, NullDetector())
    assert r.changed and r.stops == pytest.approx(1.0)
    with Image.open(dst) as im:
        mean = np.asarray(im.convert("L")).mean()
        exif = im.getexif()
        assert exif[0x0112] == 6
        assert exif.get_ifd(0x8769)[36867] == "2026:10:03 12:00:00"
    assert 60 < mean < 120
    assert src.read_bytes() == before                     # source never touched


def test_good_photo_copied_unchanged(tmp_path):
    src, dst = _jpeg(tmp_path / "ok.jpg", 122, exif=False), tmp_path / "out.jpg"
    r = correct_exposure(src, dst, CFG, NullDetector())
    assert not r.changed and dst.read_bytes() == src.read_bytes()
