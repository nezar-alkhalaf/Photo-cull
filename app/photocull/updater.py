"""Updates: ask GitHub for the newest release, download its installer, run it.

Core module (no GUI). The release page is public, so nothing here needs a login or sends any
personal data: it is one plain GET of the public "latest release" address.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__
from .paths import ROOT_DIR
from .runner import CancelToken

UPDATE_REPO = "nezar-alkhalaf/Photo-cull"
API_URL = "https://api.github.com/repos/{repo}/releases/latest"
TRUSTED_HOSTS = ("github.com", "githubusercontent.com")
ASSET_RE = re.compile(r"^PhotoCull-Setup.*\.exe$", re.IGNORECASE)
USER_AGENT = f"PhotoCull/{__version__}"


class UpdateError(RuntimeError):
    """Something went wrong while checking or downloading; the message is for the user."""


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    asset_url: str
    asset_name: str
    size: int
    sha256: str | None
    page: str


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.3.1' -> (0, 3, 1); anything unreadable -> ()."""
    nums = re.findall(r"\d+", str(text).split("-")[0])
    return tuple(int(n) for n in nums[:4])


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return bool(a) and a > b


def _trusted(url: str) -> bool:
    m = re.match(r"^https://([^/:]+)/", url)
    return bool(m) and any(m.group(1) == h or m.group(1).endswith("." + h) for h in TRUSTED_HOSTS)


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def parse_release(data: dict) -> Release | None:
    """The newest release as a Release, or None when it has no installer attached."""
    version = str(data.get("tag_name") or "").lstrip("vV")
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if ASSET_RE.match(name) and _trusted(url):
            digest = str(asset.get("digest") or "")
            return Release(version, str(data.get("body") or "").strip(), url, name,
                           int(asset.get("size") or 0),
                           digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None,
                           str(data.get("html_url") or ""))
    return None


def fetch_latest(repo: str = UPDATE_REPO, timeout: float = 8.0) -> Release | None:
    """The latest published release (None if there is none). Raises UpdateError offline."""
    try:
        with _open(API_URL.format(repo=repo), timeout) as resp:
            return parse_release(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:                       # no release published yet
            return None
        raise UpdateError(f"GitHub answered with error {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise UpdateError("Could not reach GitHub. Check the internet connection.") from exc


def check(repo: str = UPDATE_REPO) -> Release | None:
    """The newest release only if it is newer than this program, else None."""
    rel = fetch_latest(repo)
    return rel if rel and is_newer(rel.version) else None


def can_self_update() -> bool:
    """A copy made with `git clone` updates with git; only installer copies update themselves."""
    return not (ROOT_DIR / ".git").exists()


def download(rel: Release, progress_cb: Callable[[int, int], None] | None = None,
             cancel: CancelToken | None = None, dest_dir: Path | None = None,
             timeout: float = 20.0) -> Path:
    """Download the installer into a temp folder; verify size and checksum; return its path."""
    dest_dir = Path(dest_dir or tempfile.mkdtemp(prefix="PhotoCull-update-"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / rel.asset_name
    part = dest.with_suffix(".part")
    sha = hashlib.sha256()
    got = 0
    try:
        with _open(rel.asset_url, timeout) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or rel.size or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError("The download was stopped.")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                got += len(chunk)
                if progress_cb:
                    progress_cb(got, total)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        part.unlink(missing_ok=True)
        raise UpdateError("The download failed. Check the internet connection and try again.") from exc
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    if (rel.size and got != rel.size) or (rel.sha256 and sha.hexdigest() != rel.sha256):
        part.unlink(missing_ok=True)
        raise UpdateError("The downloaded file is damaged. Please try again.")
    os.replace(part, dest)
    return dest


def launch_installer(path: Path) -> None:
    """Start the installer so it updates THIS folder; the caller must then close the program."""
    env = dict(os.environ, PHOTOCULL_DEST=str(ROOT_DIR), PHOTOCULL_UPDATING="1")
    subprocess.Popen([str(path)], env=env, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                     close_fds=True)
