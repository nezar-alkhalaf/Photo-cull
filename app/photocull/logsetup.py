"""Logging: app log in app/logs/, optional per-coverage log, optional UI handler."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .paths import LOGS_DIR

LOGGER_NAME = "photocull"
_FMT = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")


class CallbackHandler(logging.Handler):
    """Forwards formatted records to a callable (e.g. a thread-safe UI queue put)."""

    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self.callback = callback
        self.setFormatter(_FMT)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(self.format(record))
        except Exception:
            self.handleError(record)


def _add_file_handler(logger: logging.Logger, path: Path) -> None:
    path = Path(path).resolve()
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler) and Path(h.baseFilename) == path:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(_FMT)
    logger.addHandler(fh)


def setup_logging(coverage_log: Path | str | None = None,
                  ui_callback: Callable[[str], None] | None = None,
                  console: bool = False) -> logging.Logger:
    """Configure the 'photocull' logger. Safe to call repeatedly."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _add_file_handler(logger, LOGS_DIR / "photocull.log")
    if coverage_log:
        _add_file_handler(logger, Path(coverage_log))
    if ui_callback and not any(isinstance(h, CallbackHandler) for h in logger.handlers):
        logger.addHandler(CallbackHandler(ui_callback))
    if console and not any(type(h) is logging.StreamHandler for h in logger.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(_FMT)
        logger.addHandler(sh)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def set_coverage_log(path: Path | str | None) -> None:
    """Log to this coverage's photocull.log only (the window can switch folders)."""
    logger = logging.getLogger(LOGGER_NAME)
    keep = (LOGS_DIR / "photocull.log").resolve()
    for h in list(logger.handlers):
        if isinstance(h, logging.FileHandler) and Path(h.baseFilename) != keep:
            logger.removeHandler(h)
            h.close()
    if path:
        _add_file_handler(logger, Path(path))
