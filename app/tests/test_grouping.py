from datetime import datetime, timedelta

import pytest

from photocull.exif_utils import read_exif_datetime
from photocull.grouping import group_by_gap
from synth import flat, save_jpeg

T0 = datetime(2026, 10, 3, 12, 0, 0)


def times(*offsets):
    return [T0 + timedelta(seconds=o) for o in offsets]


def sizes(offsets, gap=2.0, max_size=12):
    items = list(range(len(offsets)))
    return [len(g) for g in group_by_gap(items, times(*offsets), gap, max_size)]


def test_gap_boundary_is_exclusive():
    assert sizes([0, 2.0]) == [2]              # exactly the gap: same burst
    assert sizes([0, 2.001]) == [1, 1]         # just over: new burst


def test_gap_is_measured_to_previous_frame_not_first():
    assert sizes([0, 1.5, 3.0, 4.5, 6.0]) == [5]


def test_identical_timestamps_stay_together():
    assert sizes([0, 0, 0, 0, 10, 10]) == [4, 2]


@pytest.mark.parametrize("gap,expected", [(1.0, [1, 1, 1]), (2.0, [2, 1]), (3.0, [3])])
def test_gap_choices(gap, expected):
    assert sizes([0, 1.5, 4.0], gap=gap) == expected


@pytest.mark.parametrize("n,expected", [
    (12, [12]), (13, [7, 6]), (24, [12, 12]), (25, [9, 8, 8]),
])
def test_long_burst_split_into_even_chunks(n, expected):
    assert sizes([i * 0.1 for i in range(n)]) == expected


def test_max_size_chunks_keep_order():
    items = list(range(13))
    groups = group_by_gap(items, times(*[i * 0.1 for i in range(13)]), 2.0, 12)
    assert [x for g in groups for x in g] == items


def test_empty_and_validation():
    assert group_by_gap([], [], 2.0, 12) == []
    with pytest.raises(ValueError):
        group_by_gap([1], [], 2.0, 12)
    with pytest.raises(ValueError):
        group_by_gap([1], times(0), 2.0, 0)


def test_subsecond_orders_frames_within_one_second(tmp_path):
    # 10 fps burst: same EXIF second, order only visible through SubSecTimeOriginal
    a = save_jpeg(tmp_path / "a.jpg", flat(), T0, "70")
    b = save_jpeg(tmp_path / "b.jpg", flat(), T0, "05")
    c = save_jpeg(tmp_path / "c.jpg", flat(), T0, "4")
    order = sorted([a, b, c], key=read_exif_datetime)
    assert [p.name for p in order] == ["b.jpg", "c.jpg", "a.jpg"]
