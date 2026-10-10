"""Entry point used by Start PhotoCull.bat (pythonw, so there is no console to show errors).

Any failure while the window starts is written to app/logs/crash.log and shown
in a plain message box instead of vanishing.
"""

from __future__ import annotations

import sys
import time
import traceback

from .paths import LOGS_DIR


def _report(text: str) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOGS_DIR / "crash.log", "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + text + "\n")
    except OSError:
        pass
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(
            "PhotoCull could not start",
            "Something is missing or broken on this PC.\n\n"
            "Run 'First-Time Setup' once, then try again.\n\n"
            f"Details were saved in:\n{LOGS_DIR / 'crash.log'}\n\n" + text.strip().splitlines()[-1])
        root.destroy()
    except Exception:
        pass


def main() -> int:
    try:
        from .cli import main as cli_main
        return cli_main(["gui"])
    except SystemExit:
        raise
    except BaseException:
        _report(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
