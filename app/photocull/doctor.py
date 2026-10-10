"""Doctor: checks that this PC can run PhotoCull and says how to fix what cannot.

Core module (no GUI imports). `python -m photocull doctor` prints the list; the
window's "Check this PC" button shows the same list.
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .paths import APP_DIR, LOGS_DIR, ROOT_DIR
from .styles import list_styles, styles_dir

OK, WARN, FAIL = "ok", "warn", "fail"
MIN_PYTHON = (3, 11)
MODEL_FILES = ("face_landmarker.task", "face_detection_yunet_2023mar.onnx")
FREE_WARN_MB = 2048
FREE_FAIL_MB = 300

# (pip name, import name). Versions come from app/requirements.txt.
PACKAGES = (
    ("Pillow", "PIL"),
    ("numpy", "numpy"),
    ("opencv-contrib-python", "cv2"),
    ("mediapipe", "mediapipe"),
    ("pywin32", "win32com.client"),
    ("customtkinter", "customtkinter"),
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str            # OK, WARN or FAIL
    detail: str
    fix: str = ""


def pinned_versions(req: Path | None = None) -> dict[str, str]:
    """{package name (lower case): pinned version} from requirements.txt."""
    req = req or APP_DIR / "requirements.txt"
    out: dict[str, str] = {}
    try:
        lines = req.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.split("#")[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            out[name.strip().lower()] = ver.strip()
    return out


def _check_python() -> Check:
    v = sys.version_info
    text = f"Python {v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < MIN_PYTHON:
        return Check("Python", FAIL, f"{text} is too old.",
                     "Install Python 3.12 from python.org (tick 'Add Python to PATH'), "
                     "then run First-Time Setup again.")
    inside = Path(sys.prefix).resolve() == (APP_DIR / ".venv").resolve()
    if not inside:
        return Check("Python", WARN, f"{text}, but not the PhotoCull environment.",
                     "Start the program with Start PhotoCull, or run First-Time Setup.")
    return Check("Python", OK, text)


def _check_package(pip_name: str, module: str, pins: dict[str, str]) -> Check:
    label = f"Package {pip_name}"
    try:
        importlib.import_module(module)
    except ImportError as exc:
        fix = "Run First-Time Setup again (it reinstalls everything)."
        if pip_name == "pywin32":
            fix = ("Run First-Time Setup again. If it keeps failing, in a command window run:\n"
                   "    app\\.venv\\Scripts\\python app\\.venv\\Scripts\\pywin32_postinstall.py -install")
        return Check(label, FAIL, f"cannot be loaded ({exc}).", fix)
    except Exception as exc:                          # a broken DLL raises more than ImportError
        return Check(label, FAIL, f"is installed but broken ({exc}).",
                     "Run First-Time Setup again.")
    try:
        have = md.version(pip_name)
    except md.PackageNotFoundError:
        have = "?"
    want = pins.get(pip_name.lower())
    if want and have != want:
        return Check(label, WARN, f"version {have}, expected {want}.",
                     "Run First-Time Setup again to get the tested version.")
    return Check(label, OK, f"version {have}")


class _quiet_stderr:
    """MediaPipe's C++ code prints log lines straight to the OS-level stderr; hide them."""

    def __enter__(self):
        self.saved = None
        try:
            sys.stderr.flush()
            self.saved = os.dup(2)
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, 2)
            os.close(null)
        except (OSError, ValueError, AttributeError):
            self.saved = None
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            os.dup2(self.saved, 2)
            os.close(self.saved)


def _check_models() -> Check:
    missing = [m for m in MODEL_FILES if not (APP_DIR / "models" / m).is_file()]
    if missing:
        return Check("Face detection files", FAIL, "missing: " + ", ".join(missing),
                     "The app folder is incomplete. Copy the whole PhotoCull folder again.")
    try:
        from .faces import MediaPipeDetector
        with _quiet_stderr():
            MediaPipeDetector().close()
    except Exception as exc:
        return Check("Face detection", WARN,
                     f"MediaPipe could not start ({exc}). Culling still works but "
                     "cannot judge closed eyes.",
                     "Run First-Time Setup again. If it still fails, tell the developer.")
    return Check("Face detection", OK, "MediaPipe starts and the model files are there")


def _check_photoshop(connect: bool) -> list[Check]:
    from .photoshop import PhotoshopError, probe

    state, detail = probe()
    if state == "no_pywin32":
        return [Check("Photoshop", FAIL, "the Photoshop link (pywin32) is missing.",
                      "Run First-Time Setup again. Steps 1 and 3 work without it.")]
    if state == "missing":
        return [Check("Photoshop", WARN, "not found on this PC.",
                      "Install Photoshop to use step 2. Steps 1 and 3 work without it.")]
    out = []
    if state == "open":
        out.append(Check("Photoshop", OK, f"found and running (version {detail or '?'})"))
    else:
        out.append(Check("Photoshop", OK, "found (closed). PhotoCull opens it by itself "
                                          "when you start step 2."))
    if connect:
        from .photoshop import Photoshop
        ps = Photoshop()
        try:
            out.append(Check("Photoshop connection", OK, f"connected, version {ps.connect()}"))
        except PhotoshopError as exc:
            out.append(Check("Photoshop connection", FAIL, str(exc),
                             "Close any dialog open in Photoshop and try again. If it is "
                             "still not working, restart Photoshop once."))
        finally:
            ps.close()
    return out


def _check_styles(cfg: Config) -> Check:
    d = styles_dir(cfg)
    try:
        if not d.is_dir():
            return Check("Styles folder", FAIL, f"not found: {d}",
                         "Create a folder named Styles next to the app folder and put "
                         "your style .psd files in it.")
        files = list_styles(cfg)
    except OSError as exc:
        return Check("Styles folder", FAIL, f"cannot be read ({exc}).",
                     "Check the folder permissions.")
    if not files:
        return Check("Styles folder", WARN, "readable, but has no .psd style files yet.",
                     "Copy your style .psd into the Styles folder (see README.txt).")
    return Check("Styles folder", OK, f"{len(files)} style file(s): "
                 + ", ".join(p.name for p in files[:5]) + (" …" if len(files) > 5 else ""))


def _writable(folder: Path) -> str | None:
    """None if a file can be created and removed in folder, else the reason."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".write-test-")
        os.close(fd)
        os.unlink(tmp)
        return None
    except OSError as exc:
        return str(exc)


def _check_write() -> Check:
    bad = [f"{n}: {why}" for n, p in (("app folder", APP_DIR), ("logs folder", LOGS_DIR))
           if (why := _writable(p))]
    if bad:
        return Check("Write access", FAIL, "; ".join(bad),
                     "Move PhotoCull to a folder you own (Desktop or Documents), not "
                     "Program Files.")
    return Check("Write access", OK, "settings and logs can be saved")


def _check_disk(extra: Path | None) -> list[Check]:
    out = []
    seen: set[str] = set()
    for label, p in (("PhotoCull drive", ROOT_DIR), ("Coverage drive", extra)):
        if p is None:
            continue
        try:
            free_mb = shutil.disk_usage(p).free // 2**20
            drive = os.path.splitdrive(str(p.resolve()))[0] or str(p)
        except OSError:
            continue
        if drive.lower() in seen:
            continue
        seen.add(drive.lower())
        text = f"{free_mb / 1024:.1f} GB free on {drive}"
        if free_mb < FREE_FAIL_MB:
            out.append(Check(f"Disk space ({label})", FAIL, text,
                             "Free some space. Each step writes a copy of the photos."))
        elif free_mb < FREE_WARN_MB:
            out.append(Check(f"Disk space ({label})", WARN, text,
                             "Low. A big coverage may not fit; free some space."))
        else:
            out.append(Check(f"Disk space ({label})", OK, text))
    return out


def run_doctor(cfg: Config, coverage: Path | str | None = None,
               connect_photoshop: bool = False) -> list[Check]:
    """All checks, in the order the user cares about. Never raises."""
    pins = pinned_versions()
    cov = Path(coverage) if coverage else None
    checks: list[Check] = []

    def add(fn, *a):
        try:
            res = fn(*a)
            checks.extend(res if isinstance(res, list) else [res])
        except Exception as exc:                       # a check must never break the list
            checks.append(Check(getattr(fn, "__name__", "check"), FAIL,
                                f"the check itself crashed: {exc}"))

    add(_check_python)
    for pip_name, module in PACKAGES:
        add(_check_package, pip_name, module, pins)
    add(_check_models)
    add(_check_photoshop, connect_photoshop)
    add(_check_styles, cfg)
    add(_check_write)
    cov_ok = cov if cov and cov.is_dir() else None
    add(_check_disk, cov_ok)
    if cov and not cov.is_dir():
        checks.append(Check("Last coverage folder", WARN, f"no longer exists: {cov}",
                            "Click 'Choose folder…' and pick it again (normal after moving "
                            "to a new PC)."))
    return checks


MARK = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}


def summary(checks: list[Check]) -> str:
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    if fails:
        return f"{fails} problem(s) to fix" + (f", {warns} warning(s)." if warns else ".")
    if warns:
        return f"Everything needed works. {warns} warning(s) above."
    return "All green. This PC is ready."


def format_report(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        lines.append(f"{MARK[c.status]} {c.name}: {c.detail}")
        if c.fix and c.status != OK:
            lines.append("         Fix: " + c.fix.replace("\n", "\n              "))
    lines += ["", summary(checks)]
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == FAIL for c in checks) else 0
