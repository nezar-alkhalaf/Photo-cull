import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from photocull.exif_utils import read_exif_datetime
from photocull.paths import CoverageLayout, list_jpegs
from photocull.runner import CancelToken
from photocull.split import distribute, existing_posts, plan_split, run_split

APP = Path(__file__).resolve().parents[1]


# ---------- distribution math ----------

@pytest.mark.parametrize("total,n,expected", [
    (0, 20, []),
    (1, 20, [1]),
    (19, 20, [19]),
    (20, 20, [20]),
    (21, 20, [11, 10]),
    (30, 20, [15, 15]),
    (41, 20, [14, 14, 13]),
    (45, 20, [15, 15, 15]),
    (30, 10, [10, 10, 10]),
    (41, 10, [9, 8, 8, 8, 8]),
    (400, 20, [20] * 20),
    (400, 10, [10] * 40),
])
def test_distribution_examples(total, n, expected):
    assert distribute(total, n) == expected


def test_distribution_invariants():
    for n in (1, 7, 10, 20):
        for total in range(0, 500):
            sizes = distribute(total, n)
            assert sum(sizes) == total
            assert all(1 <= s <= n for s in sizes)
            if sizes:
                assert max(sizes) - min(sizes) <= 1
                assert sizes == sorted(sizes, reverse=True)
                assert len(sizes) == -(-total // n)


def test_distribution_validation():
    with pytest.raises(ValueError):
        distribute(10, 0)
    with pytest.raises(ValueError):
        distribute(-1, 5)


def test_plan_is_contiguous_and_complete():
    files = list(range(41))
    chunks = plan_split(files, 20)
    assert [len(c) for c in chunks] == [14, 14, 13]
    assert [x for c in chunks for x in c] == files


# ---------- helpers ----------

def make_jpeg(path: Path, when: datetime | None = None, sub: str | None = None):
    img = Image.new("RGB", (16, 16), (120, 120, 120))
    if when is None:
        img.save(path, "JPEG")
        return
    exif = Image.Exif()
    ifd = exif.get_ifd(0x8769)
    ifd[36867] = when.strftime("%Y:%m:%d %H:%M:%S")
    if sub:
        ifd[37521] = sub
    img.save(path, "JPEG", exif=exif)


def make_coverage(tmp_path, count, name="Match"):
    lay = CoverageLayout.for_main(tmp_path / name)
    lay.full_coverage.mkdir(parents=True)
    lay.ensure()
    for i in range(count):
        make_jpeg(lay.edited / f"IMG_{i:03d}.jpg",
                  datetime(2026, 10, 3, 12, i // 60, i % 60))
    return lay


def post_sizes(lay):
    return [len(list_jpegs(p)) for p in existing_posts(lay.posts)]


# ---------- end-to-end ----------

@pytest.mark.parametrize("count,expected", [
    (30, [15, 15]), (41, [14, 14, 13]), (45, [15, 15, 15]), (5, [5]),
])
def test_run_split_folder_sizes(tmp_path, count, expected):
    lay = make_coverage(tmp_path, count)
    res = run_split(lay)
    assert res.ok, res.message
    assert post_sizes(lay) == expected
    assert f"Copied {count} of {count}" in res.message
    assert len(list_jpegs(lay.edited)) == count          # source untouched


def test_split_is_chronological_by_exif_not_filename(tmp_path):
    lay = CoverageLayout.for_main(tmp_path / "C")
    lay.full_coverage.mkdir(parents=True)
    lay.ensure()
    # filename order is the REVERSE of capture order
    for i in range(6):
        make_jpeg(lay.edited / f"A_{5 - i}.jpg", datetime(2026, 1, 1, 10, 0, i))
    run_split(lay, max_per_post=3)
    first = [p.name for p in list_jpegs(lay.posts / "Post_1")]
    assert first == ["A_3.jpg", "A_4.jpg", "A_5.jpg"]


def test_subsecond_is_fraction_of_second(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    t = datetime(2026, 1, 1, 10, 0, 0)
    make_jpeg(a, t, "9")      # .9 s
    make_jpeg(b, t, "25")     # .25 s
    assert read_exif_datetime(b) < read_exif_datetime(a)


def test_no_exif_falls_back_to_file_time_then_name(tmp_path):
    lay = CoverageLayout.for_main(tmp_path / "C")
    lay.full_coverage.mkdir(parents=True)
    lay.ensure()
    for name in ("b.jpg", "a.jpg", "c.jpg"):
        make_jpeg(lay.edited / name)
    import os
    for name in ("a.jpg", "b.jpg", "c.jpg"):          # identical mtime -> name order
        os.utime(lay.edited / name, (1_700_000_000, 1_700_000_000))
    run_split(lay, max_per_post=10)
    assert [p.name for p in list_jpegs(lay.posts / "Post_1")] == ["a.jpg", "b.jpg", "c.jpg"]


def test_empty_input_does_nothing(tmp_path):
    lay = make_coverage(tmp_path, 0)
    res = run_split(lay)
    assert res.ok and "nothing to do" in res.message
    assert existing_posts(lay.posts) == []


def test_non_jpeg_files_ignored(tmp_path):
    lay = make_coverage(tmp_path, 3)
    (lay.edited / "notes.txt").write_text("x")
    (lay.edited / "raw.cr2").write_bytes(b"x")
    assert run_split(lay).ok
    assert post_sizes(lay) == [3]


def test_invalid_max_per_post(tmp_path):
    lay = make_coverage(tmp_path, 3)
    res = run_split(lay, max_per_post=0)
    assert not res.ok
    assert existing_posts(lay.posts) == []


def test_missing_input_folder(tmp_path):
    lay = make_coverage(tmp_path, 1)
    assert not run_split(lay, input_dir=tmp_path / "nope").ok


def test_custom_input_override(tmp_path):
    lay = make_coverage(tmp_path, 2)
    hand = tmp_path / "hand_edited"
    hand.mkdir()
    for i in range(4):
        make_jpeg(hand / f"h{i}.jpg", datetime(2026, 1, 1, 9, 0, i))
    assert run_split(lay, input_dir=hand).ok
    assert post_sizes(lay) == [4]


def test_input_inside_posts_refused(tmp_path):
    lay = make_coverage(tmp_path, 2)
    inner = lay.posts / "Post_1"
    inner.mkdir(parents=True)
    make_jpeg(inner / "x.jpg")
    res = run_split(lay, input_dir=inner)
    assert not res.ok and "inside" in res.message


# ---------- re-run policy ----------

def test_rerun_asks_and_changes_nothing(tmp_path):
    lay = make_coverage(tmp_path, 30)
    run_split(lay)
    res = run_split(lay)
    assert not res.ok and res.data["needs_decision"]
    assert post_sizes(lay) == [15, 15]


def test_rerun_clear_replaces_only_post_folders(tmp_path):
    lay = make_coverage(tmp_path, 30)
    run_split(lay)
    keep = lay.posts / "my_notes"
    keep.mkdir()
    (keep / "keep.txt").write_text("mine")
    res = run_split(lay, max_per_post=10, existing="clear")
    assert res.ok
    assert post_sizes(lay) == [10, 10, 10]
    assert (keep / "keep.txt").read_text() == "mine"


def test_rerun_new_numbers_after_highest(tmp_path):
    lay = make_coverage(tmp_path, 30)
    run_split(lay)
    res = run_split(lay, existing="new")
    assert res.ok
    assert [p.name for p in existing_posts(lay.posts)] == ["Post_1", "Post_2", "Post_3", "Post_4"]
    assert post_sizes(lay) == [15, 15, 15, 15]


# ---------- cancel, arabic, state ----------

def test_cancel_stops_cleanly(tmp_path):
    lay = make_coverage(tmp_path, 20)
    token = CancelToken()
    seen = []

    def progress(done, total, name):
        seen.append(done)
        if done == 5:
            token.set()

    res = run_split(lay, progress_cb=progress, cancel=token)
    assert res.cancelled and not res.ok
    # stop requested while image #6 is starting: it finishes, nothing after it is copied
    assert sum(post_sizes(lay)) == 6
    from photocull.state import State
    assert State(lay.state).step("split")["status"] == "partial"


def test_arabic_folder_and_names(tmp_path):
    lay = CoverageLayout.for_main(tmp_path / "مباراة الأحد")
    lay.full_coverage.mkdir(parents=True)
    lay.ensure()
    for i in range(3):
        make_jpeg(lay.edited / f"صورة {i}.jpg", datetime(2026, 1, 1, 9, 0, i))
    assert run_split(lay).ok
    assert [p.name for p in list_jpegs(lay.posts / "Post_1")] == ["صورة 0.jpg", "صورة 1.jpg", "صورة 2.jpg"]


def test_state_records_success_with_relative_names(tmp_path):
    lay = make_coverage(tmp_path, 4)
    run_split(lay, max_per_post=2)
    from photocull.state import State
    st = State(lay.state).step("split")
    assert st["status"] == "done" and st["sizes"] == [2, 2]
    assert all(f.startswith("Posts/Post_") for f in st["done_files"])


def test_never_touches_full_coverage(tmp_path):
    lay = make_coverage(tmp_path, 4)
    make_jpeg(lay.full_coverage / "orig.jpg")
    before = sorted(p.name for p in lay.full_coverage.iterdir())
    run_split(lay)
    assert sorted(p.name for p in lay.full_coverage.iterdir()) == before


# ---------- CLI ----------

def test_cli_split(tmp_path):
    lay = make_coverage(tmp_path, 41)
    r = subprocess.run([sys.executable, "-m", "photocull", "split", str(lay.main)],
                       cwd=APP, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert post_sizes(lay) == [14, 14, 13]
    r2 = subprocess.run([sys.executable, "-m", "photocull", "split", str(lay.main)],
                        cwd=APP, capture_output=True, text=True, encoding="utf-8")
    assert r2.returncode == 1 and "--existing" in r2.stderr
