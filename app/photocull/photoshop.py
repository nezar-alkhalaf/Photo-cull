"""Photoshop through COM (plan section 7). Verified on Photoshop 2026 (27.10).

COM objects belong to the thread that created them: create one Photoshop
object per thread (the GUI status probe and the edit worker each get their own).

Style method (plan 7.2, primary method, works on 27.10): open the style PSD
once per batch, Duplicate() its STYLE group (or its top-level adjustment
layers) into each photo, flatten, save. Layer order, opacity and blend modes
travel with the duplicated layers.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

log = logging.getLogger("photocull")

NO_DIALOGS = 3                 # psDisplayNoDialogs
DO_NOT_SAVE = 2                # psDoNotSaveChanges
PLACE_AT_BEGINNING = 1         # psPlaceAtBeginning
STYLE_GROUP = "STYLE"
# ArtLayer.Kind values that carry no pixels and work on any photo size:
# fills (3-5), adjustments (6-16), photo filter, exposure, B&W, vibrance.
ADJUSTMENT_KINDS = set(range(3, 17)) | {18, 19, 22, 23}
KIND_NAMES = {1: "pixel layer", 2: "text layer", 17: "smart object", 20: "3D layer",
              21: "video layer"}
# Photoshop needs 10-30 s to start and refuses COM ("Server execution failed")
# while its licence check runs, so the first connection is retried.
CONNECT_TIMEOUT = 120.0


class PhotoshopError(RuntimeError):
    """A clear, user-facing problem with Photoshop or a style file."""


def jpeg_quality_12(percent: int) -> int:
    """Config quality (1-100 %) to Photoshop's 0-12 scale: 92 % -> 11."""
    return max(0, min(12, round(percent / 100 * 12)))


def _com():
    try:
        import pythoncom
        import pywintypes
        import win32com.client
    except ImportError as exc:
        raise PhotoshopError("pywin32 is not installed; run First-Time Setup again.") from exc
    return pythoncom, pywintypes, win32com.client


def probe() -> tuple[str, str]:
    """Is Photoshop usable? Never launches it. Safe to call from any thread.

    Returns (state, detail): "open" + version, "closed" (installed, not running),
    "missing" (not installed) or "no_pywin32".
    """
    try:
        pythoncom, pywintypes, client = _com()
    except PhotoshopError:
        return "no_pywin32", ""
    pythoncom.CoInitialize()
    try:
        try:
            app = client.GetActiveObject("Photoshop.Application")
        except pywintypes.com_error:
            return ("closed", "") if _installed() else ("missing", "")
        try:
            return "open", str(app.Version)
        except Exception:
            return "open", ""                  # running but busy (a dialog is open)
    finally:
        pythoncom.CoUninitialize()


def _installed() -> bool:
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Photoshop.Application"))
        return True
    except OSError:
        return False


class Photoshop:
    """One COM connection. Use from a single thread."""

    def __init__(self, retry_attempts: int = 3, connect_timeout: float = CONNECT_TIMEOUT,
                 sleep: Callable[[float], None] = time.sleep):
        self.retry_attempts = retry_attempts
        self.connect_timeout = connect_timeout
        self._sleep = sleep
        self.app = None
        self.version = ""
        self._com_inited = False

    # ---------------------------------------------------------------- connection
    def connect(self) -> str:
        """Launch or attach to Photoshop; returns its version. Raises PhotoshopError."""
        pythoncom, pywintypes, client = _com()
        if not self._com_inited:
            pythoncom.CoInitialize()
            self._com_inited = True
        deadline = time.monotonic() + self.connect_timeout
        last: Exception | None = None
        while True:
            try:
                self.app = client.Dispatch("Photoshop.Application")
                self.version = str(self.app.Version)
                log.info("Photoshop %s connected", self.version)
                return self.version
            except pywintypes.com_error as exc:
                last = exc
                if exc.args and exc.args[0] == -2147221005:      # invalid class string
                    raise PhotoshopError("Photoshop is not installed on this PC.") from exc
            if time.monotonic() >= deadline:
                raise PhotoshopError(
                    "Photoshop did not respond. If it shows a sign-in or licence window, "
                    f"finish that and try again. ({last})")
            self._sleep(3.0)

    def is_alive(self) -> bool:
        if self.app is None:
            return False
        try:
            _ = self.app.Name
            return True
        except Exception:
            return False

    def ensure(self) -> None:
        """Reconnect (relaunching Photoshop) if the user closed it."""
        if not self.is_alive():
            log.info("Photoshop connection lost; reconnecting")
            self.app = None
            self.connect()

    def close(self) -> None:
        """Release COM. Photoshop itself stays open (plan 7.1)."""
        self.app = None
        if self._com_inited:
            pythoncom, _, _ = _com()
            pythoncom.CoUninitialize()
            self._com_inited = False

    def retry(self, what: str, fn: Callable[[], object]):
        """Run fn, retrying COM 'busy' errors with a short backoff."""
        _, pywintypes, _ = _com()
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return fn()
            except pywintypes.com_error as exc:
                if attempt == self.retry_attempts:
                    raise PhotoshopError(f"{what} failed: {_com_message(exc)}") from exc
                log.warning("%s: Photoshop busy (%s), retry %d", what, _com_message(exc), attempt)
                self._sleep(1.5 * attempt)

    @contextmanager
    def no_dialogs(self):
        """DisplayDialogs = no dialogs for the run; previous value restored even on error."""
        old = self.app.DisplayDialogs
        self.app.DisplayDialogs = NO_DIALOGS
        try:
            yield
        finally:
            try:
                self.app.DisplayDialogs = old
            except Exception:
                log.warning("Could not restore Photoshop's dialog setting")

    # ---------------------------------------------------------------- styles
    def open_style(self, psd: Path) -> "StyleDoc":
        if not Path(psd).is_file():
            raise PhotoshopError(f"Style file not found: {psd}")
        doc = self.retry("Opening the style", lambda: self.app.Open(str(Path(psd).resolve())))
        try:
            return StyleDoc(self, doc)
        except PhotoshopError:
            doc.Close(DO_NOT_SAVE)
            raise

    # ---------------------------------------------------------------- actions
    def loaded_action_sets(self) -> dict[str, list[str]]:
        """{set name: [action names]} of the sets loaded in Photoshop's Actions panel."""
        js = r"""
        var out = [], i = 1;
        while (true) {
          var r = new ActionReference(); r.putIndex(charIDToTypeID('ASet'), i);
          try { var d = executeActionGet(r); } catch (e) { break; }
          var name = d.getString(charIDToTypeID('Nm  ')), acts = [];
          var n = d.hasKey(charIDToTypeID('NmbC')) ? d.getInteger(charIDToTypeID('NmbC')) : 0;
          for (var j = 1; j <= n; j++) {
            var r2 = new ActionReference();
            r2.putIndex(charIDToTypeID('Actn'), j); r2.putIndex(charIDToTypeID('ASet'), i);
            acts.push(executeActionGet(r2).getString(charIDToTypeID('Nm  ')));
          }
          out.push(name + '\u0001' + acts.join('\u0002'));
          i++;
        }
        out.join('\u0003');
        """
        raw = str(self.retry("Reading actions", lambda: self.app.DoJavaScript(js)) or "")
        sets: dict[str, list[str]] = {}
        for chunk in filter(None, raw.split("\u0003")):
            name, _, acts = chunk.partition("\u0001")
            sets[name] = [a for a in acts.split("\u0002") if a]
        return sets

    def load_actions(self, atn: Path, set_name: str) -> list[str]:
        """Load an .atn once (no duplicate sets); return the set's action names."""
        sets = self.loaded_action_sets()
        if set_name not in sets:
            self.retry("Loading the action file", lambda: self.app.Load(str(Path(atn).resolve())))
            sets = self.loaded_action_sets()
        if set_name not in sets:
            raise PhotoshopError(f"Action set '{set_name}' did not load from {Path(atn).name}.")
        return sets[set_name]

    # ---------------------------------------------------------------- per photo
    def apply(self, style: "StyleDoc", working: Path, quality_percent: int,
              action: tuple[str, str] | None = None) -> None:
        """Open the working JPEG, add the style, run the action, flatten, overwrite it."""
        _, _, client = _com()
        path = str(Path(working).resolve())
        doc = self.retry("Opening the photo", lambda: self.app.Open(path))
        try:
            style.duplicate_into(doc)
            if action:
                self.app.ActiveDocument = doc
                self.retry(f"Running action '{action[1]}'",
                           lambda: self.app.DoAction(action[1], action[0]))
            doc.Flatten()
            opts = client.Dispatch("Photoshop.JPEGSaveOptions")
            opts.Quality = jpeg_quality_12(quality_percent)
            opts.EmbedColorProfile = True
            self.retry("Saving the photo", lambda: doc.SaveAs(path, opts, True))
        finally:
            try:
                doc.Close(DO_NOT_SAVE)               # memory: close right after saving
            except Exception:
                log.warning("Could not close %s in Photoshop", Path(path).name)


class StyleDoc:
    """An open style PSD, kept open for the whole batch."""

    def __init__(self, ps: Photoshop, doc):
        self.ps, self.doc = ps, doc
        self.name = str(doc.Name)
        self.group = None
        self.layers = []
        try:
            self.group = doc.LayerSets.Item(STYLE_GROUP)
        except Exception:
            pass
        if self.group is None:
            bad = []
            for i in range(1, doc.ArtLayers.Count + 1):
                lyr = doc.ArtLayers.Item(i)
                if lyr.IsBackgroundLayer:
                    continue
                if lyr.Kind in ADJUSTMENT_KINDS:
                    self.layers.append(lyr)
                else:
                    bad.append(f"'{lyr.Name}' ({KIND_NAMES.get(lyr.Kind, 'not an adjustment')})")
            if bad:
                raise PhotoshopError(
                    f"{self.name}: only adjustment layers are allowed (or put them in a group "
                    f"named {STYLE_GROUP}). Problem layers: {', '.join(bad)}")
            if not self.layers:
                raise PhotoshopError(f"{self.name} has no adjustment layers and no "
                                     f"'{STYLE_GROUP}' group.")
        self.layer_count = (self.group.Layers.Count if self.group is not None
                            else len(self.layers))

    def duplicate_into(self, target) -> None:
        app = self.ps.app

        def run():
            app.ActiveDocument = self.doc        # Duplicate works from the source document
            if self.group is not None:
                self.group.Duplicate(target, PLACE_AT_BEGINNING)
            else:
                for lyr in reversed(self.layers):   # bottom-most first keeps the stack order
                    lyr.Duplicate(target, PLACE_AT_BEGINNING)
            app.ActiveDocument = target

        self.ps.retry("Applying the style", run)

    def close(self) -> None:
        try:
            self.doc.Close(DO_NOT_SAVE)
        except Exception:
            pass


def _com_message(exc) -> str:
    """Readable text from a pywintypes.com_error."""
    try:
        hr, msg, info, _ = exc.args
        if info and len(info) > 2 and info[2]:
            return str(info[2]).strip()
        return f"{msg} ({hr})"
    except Exception:
        return str(exc)
