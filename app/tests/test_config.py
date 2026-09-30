import pytest

from photocull.config import DEFAULT_CONFIG_PATH, ConfigError, load_config


def write(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_shipped_config_loads_and_matches_plan():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.grouping.group_gap_seconds == 2.0
    assert cfg.scoring.action.w_sharpness == 0.55
    assert cfg.split.max_per_post == 20
    assert cfg.photoshop.jpeg_quality == 92


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml").split.max_per_post == 20


def test_partial_file_keeps_other_defaults(tmp_path):
    cfg = load_config(write(tmp_path, "[split]\nmax_per_post = 10\n"))
    assert cfg.split.max_per_post == 10
    assert cfg.grouping.max_group_size == 12


@pytest.mark.parametrize("text", [
    "[split]\nmax_per_post = 0\n",
    "[split]\nmax_per_post = 2.5\n",
    "[grouping]\ngroup_gap_seconds = -1\n",
    "[split]\nmax_per_pots = 5\n",
    "[nonsense]\nx = 1\n",
    "[scoring.portrait]\nw_sharpness = 0\nw_exposure = 0\nw_eyes = 0\nw_contrast = 0\n",
    "[detection]\ntarget_brightness_low = 200\ntarget_brightness_high = 100\n",
    "[photoshop]\nstyles_folder = 'C:/Styles'\n",
    "[exposure_correction]\nenabled = 'yes'\n",
    "[split\n",
])
def test_invalid_configs_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text))
