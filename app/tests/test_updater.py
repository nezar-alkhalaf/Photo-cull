import hashlib
import json
from pathlib import Path

import pytest

from photocull import updater
from photocull.runner import CancelToken
from photocull.updater import Release, UpdateError, is_newer, parse_release, parse_version


def release_json(tag="v0.3.0", name="PhotoCull-Setup-0.3.0.exe",
                 url="https://github.com/o/r/releases/download/v0.3.0/PhotoCull-Setup-0.3.0.exe"):
    return {"tag_name": tag, "body": "- new things", "html_url": "https://github.com/o/r/releases/tag/v0.3.0",
            "assets": [{"name": "notes.txt", "browser_download_url": "https://github.com/x/notes.txt", "size": 5},
                       {"name": name, "browser_download_url": url, "size": 10,
                        "digest": "sha256:" + "AB" * 32}]}


def test_version_parsing_and_comparison():
    assert parse_version("v1.2.3") == (1, 2, 3) and parse_version("") == ()
    assert is_newer("0.3.0", "0.2.0") and is_newer("v1.0", "0.9.9")
    assert not is_newer("0.2.0", "0.2.0") and not is_newer("0.1.9", "0.2.0")
    assert not is_newer("garbage", "0.2.0")


def test_release_picks_the_installer_asset_and_reads_the_checksum():
    rel = parse_release(release_json())
    assert rel.version == "0.3.0" and rel.asset_name == "PhotoCull-Setup-0.3.0.exe"
    assert rel.size == 10 and rel.sha256 == "ab" * 32 and rel.notes == "- new things"


def test_release_without_installer_or_from_an_untrusted_host_is_ignored():
    assert parse_release({"tag_name": "v9", "assets": []}) is None
    assert parse_release(release_json(url="https://evil.example.com/PhotoCull-Setup.exe")) is None
    assert parse_release(release_json(url="http://github.com/x/PhotoCull-Setup.exe")) is None
    assert parse_release(release_json(name="other.exe")) is None


def test_check_returns_only_newer_releases(monkeypatch):
    import io
    data = {"v": release_json("v0.3.0")}

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(updater, "_open", lambda url, timeout: Resp(json.dumps(data["v"]).encode()))
    assert updater.check().version == "0.3.0"
    data["v"] = release_json("v0.0.1")
    assert updater.check() is None


def test_offline_and_missing_release_messages(monkeypatch):
    import urllib.error

    def offline(url, timeout): raise urllib.error.URLError("no network")
    monkeypatch.setattr(updater, "_open", offline)
    with pytest.raises(UpdateError, match="Could not reach GitHub"):
        updater.fetch_latest()

    def none(url, timeout): raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    monkeypatch.setattr(updater, "_open", none)
    assert updater.fetch_latest() is None


def make_file_release(tmp_path, payload=b"hello installer", good_sha=True, size=None):
    src = tmp_path / "src.exe"
    src.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest() if good_sha else "0" * 64
    return Release("0.3.0", "", src.as_uri(), "PhotoCull-Setup-0.3.0.exe",
                   len(payload) if size is None else size, sha, "")


def test_download_checks_size_and_checksum(tmp_path):
    seen = []
    path = updater.download(make_file_release(tmp_path), lambda g, t: seen.append((g, t)),
                            dest_dir=tmp_path / "dl")
    assert path.read_bytes() == b"hello installer" and seen[-1][0] == len(b"hello installer")
    assert not list((tmp_path / "dl").glob("*.part"))
    with pytest.raises(UpdateError, match="damaged"):
        updater.download(make_file_release(tmp_path, good_sha=False), dest_dir=tmp_path / "d2")
    with pytest.raises(UpdateError, match="damaged"):
        updater.download(make_file_release(tmp_path, size=3), dest_dir=tmp_path / "d3")
    assert not list((tmp_path / "d2").glob("*"))        # nothing left behind


def test_download_can_be_cancelled(tmp_path):
    cancel = CancelToken()
    cancel.set()
    with pytest.raises(UpdateError, match="stopped"):
        updater.download(make_file_release(tmp_path), cancel=cancel, dest_dir=tmp_path / "dl")


def test_installer_updates_the_folder_it_runs_from(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kw: calls.update(args=args, **kw))
    updater.launch_installer(tmp_path / "x.exe")
    assert calls["env"]["PHOTOCULL_DEST"] == str(updater.ROOT_DIR)
    assert calls["env"]["PHOTOCULL_UPDATING"] == "1"
