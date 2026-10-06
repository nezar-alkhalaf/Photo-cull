"""The Styles/ folder: style PSDs and optional action files (plan 7.2)."""

from __future__ import annotations

import struct
from pathlib import Path

from .config import Config
from .paths import ROOT_DIR


def styles_dir(cfg: Config) -> Path:
    """Styles/ next to app/ (relative in config, so the project stays portable)."""
    return ROOT_DIR / cfg.photoshop.styles_folder


def list_styles(cfg: Config) -> list[Path]:
    d = styles_dir(cfg)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".psd"),
                  key=lambda p: p.name.lower())


def list_action_files(cfg: Config) -> list[Path]:
    d = styles_dir(cfg)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".atn"),
                  key=lambda p: p.name.lower())


def resolve_style(cfg: Config, name: str | None) -> Path:
    """A style by file name (looked up in Styles/) or by full path."""
    name = name or cfg.photoshop.default_style_file
    if not name:
        raise FileNotFoundError("No style chosen.")
    p = Path(name)
    if p.is_file():
        return p.resolve()
    cand = styles_dir(cfg) / p.name
    if cand.suffix == "":
        cand = cand.with_suffix(".psd")
    if cand.is_file():
        return cand
    raise FileNotFoundError(f"Style file not found: {name}")


def read_atn_set_name(path: Path | str) -> str:
    """Name of the action set stored in an .atn file (not the file name).

    Header: uint32 version (16), then a Unicode string: uint32 length in
    UTF-16 units (counting a trailing NUL) and big-endian UTF-16 text. The
    action names come after variable-length recorded steps, so those are read
    from Photoshop once the file is loaded (Photoshop.load_actions).
    """
    data = Path(path).read_bytes()[:4 + 4 + 2 * 512]
    if len(data) < 8:
        raise ValueError(f"{Path(path).name} is not an action file")
    version, n = struct.unpack(">II", data[:8])
    if version != 16 or not 0 < n <= 512 or len(data) < 8 + 2 * n:
        raise ValueError(f"{Path(path).name} is not a Photoshop action file")
    return data[8:8 + 2 * n].decode("utf-16-be").rstrip("\x00")
