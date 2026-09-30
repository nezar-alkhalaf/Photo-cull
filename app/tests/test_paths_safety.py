import pytest

from photocull.paths import (CoverageLayout, SafetyError, check_not_in_source,
                             check_output_safe, preflight, relative_name,
                             safe_copy, unique_path)


def make_main(tmp_path, name="Match_2026"):
    main = tmp_path / name
    (main / "Full_Coverage").mkdir(parents=True)
    return main


def test_refuses_output_inside_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(SafetyError):
        check_output_safe(src, src / "out")


def test_refuses_output_equal_or_containing_source(tmp_path):
    src = tmp_path / "a" / "src"
    src.mkdir(parents=True)
    with pytest.raises(SafetyError):
        check_output_safe(src, src)
    with pytest.raises(SafetyError):
        check_output_safe(src, tmp_path / "a")


def test_sibling_output_ok(tmp_path):
    (tmp_path / "src").mkdir()
    check_output_safe(tmp_path / "src", tmp_path / "out")


def test_dotdot_escape_is_resolved(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(SafetyError):
        check_output_safe(src, tmp_path / "other" / ".." / "src" / "x")


def test_layout_ensure_creates_outputs_not_in_source(tmp_path):
    lay = CoverageLayout.for_main(make_main(tmp_path))
    lay.ensure()
    for d in lay.output_dirs:
        assert d.is_dir()
        assert lay.full_coverage not in d.parents


def test_layout_requires_full_coverage(tmp_path):
    with pytest.raises(SafetyError):
        CoverageLayout.for_main(tmp_path).ensure()


def test_never_writes_into_full_coverage(tmp_path):
    lay = CoverageLayout.for_main(make_main(tmp_path))
    src = tmp_path / "x.jpg"
    src.write_bytes(b"data")
    with pytest.raises(SafetyError):
        safe_copy(src, lay.full_coverage, lay)
    assert list(lay.full_coverage.iterdir()) == []
    with pytest.raises(SafetyError):
        check_not_in_source(lay.full_coverage / "sub" / "a.jpg", lay)


def test_unique_path_suffixes(tmp_path):
    f = tmp_path / "IMG_001.jpg"
    assert unique_path(f) == f
    f.write_bytes(b"1")
    assert unique_path(f).name == "IMG_001_2.jpg"
    (tmp_path / "IMG_001_2.jpg").write_bytes(b"2")
    assert unique_path(f).name == "IMG_001_3.jpg"


def test_safe_copy_never_overwrites(tmp_path):
    src = tmp_path / "a.jpg"
    src.write_bytes(b"new")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"old")
    dst = safe_copy(src, out)
    assert dst.name == "a_2.jpg"
    assert (out / "a.jpg").read_bytes() == b"old"
    assert src.read_bytes() == b"new"


def test_arabic_and_spaces_in_paths(tmp_path):
    main = make_main(tmp_path, "مباراة الأحد 2026")
    lay = CoverageLayout.for_main(main)
    lay.ensure()
    src = lay.full_coverage / "صورة 1.jpg"
    src.write_bytes(b"abc")
    dst = safe_copy(src, lay.selected, lay)
    assert dst.read_bytes() == b"abc"
    assert relative_name(dst, lay.main) == "Selected/صورة 1.jpg"


def test_preflight_reports_problems(tmp_path):
    assert preflight(CoverageLayout.for_main(tmp_path / "missing"))
    lay = CoverageLayout.for_main(make_main(tmp_path))
    assert preflight(lay) == []
    assert any("disk space" in p for p in preflight(lay, needed_bytes=10**18))
