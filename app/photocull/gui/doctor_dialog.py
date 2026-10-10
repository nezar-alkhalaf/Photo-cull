"""The "Check this PC" window: the Doctor checklist with a fix under each problem."""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import customtkinter as ctk

from ..config import Config
from ..doctor import FAIL, OK, WARN, Check, format_report, run_doctor, summary
from .i18n import anchor, justify, mcol, side, sticky, t, tr_msg
from .widgets import FONT

DOT = {OK: "#2e9e4f", WARN: "#d99a00", FAIL: "#d03b3b"}


class DoctorDialog(ctk.CTkToplevel):
    def __init__(self, parent, cfg: Config, coverage: Path | str | None):
        super().__init__(parent)
        self.cfg, self.coverage = cfg, coverage
        self.title(t("Check this PC"))
        self.geometry("720x560")
        self.minsize(560, 380)
        self.transient(parent)
        self._q: queue.Queue = queue.Queue()
        self._checks: list[Check] = []
        self._alive = True
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.lbl_sum = ctk.CTkLabel(self, text="", font=(FONT, 15, "bold"), anchor=anchor())
        self.lbl_sum.pack(fill="x", padx=16, pady=(14, 6))
        self.body = ctk.CTkScrollableFrame(self, corner_radius=8)
        self.body.pack(fill="both", expand=True, padx=12, pady=4)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=(6, 12))
        self.btn_again = ctk.CTkButton(bar, text=t("Check again"), width=120, command=self._start)
        self.btn_again.pack(side=side("left"), padx=4)
        self.btn_ps = ctk.CTkButton(bar, text=t("Test Photoshop connection"), width=190,
                                    command=lambda: self._start(connect=True))
        self.btn_ps.pack(side=side("left"), padx=4)
        ctk.CTkButton(bar, text=t("Copy report"), width=110, command=self._copy).pack(
            side=side("left"), padx=4)
        ctk.CTkButton(bar, text=t("Close"), width=90, command=self._close).pack(
            side=side("right"), padx=4)
        self._start()
        self.after(100, self._poll)

    def _start(self, connect: bool = False) -> None:
        self.btn_again.configure(state="disabled")
        self.btn_ps.configure(state="disabled")
        self.lbl_sum.configure(
            text=t("Starting Photoshop and testing… (up to 30 s)") if connect else t("Checking…"))
        for w in self.body.winfo_children():
            w.destroy()

        def work():
            try:
                self._q.put(run_doctor(self.cfg, self.coverage, connect_photoshop=connect))
            except Exception as exc:
                self._q.put([Check("Doctor", FAIL, f"crashed: {exc}")])

        threading.Thread(target=work, daemon=True, name="doctor").start()

    def _poll(self) -> None:
        if not self._alive:
            return
        try:
            self._show(self._q.get_nowait())
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _show(self, checks: list[Check]) -> None:
        self._checks = checks
        self.lbl_sum.configure(text=tr_msg(summary(checks)))
        for c in checks:
            row = ctk.CTkFrame(self.body, fg_color="transparent")
            row.pack(fill="x", pady=3)
            row.columnconfigure(mcol(1, 2), weight=1)
            ctk.CTkLabel(row, text="●", text_color=DOT[c.status], font=(FONT, 18), width=24
                         ).grid(row=0, column=mcol(0, 2), rowspan=2, sticky="n", padx=(2, 6))
            ctk.CTkLabel(row, text=c.name, font=(FONT, 13, "bold"), anchor=anchor()).grid(
                row=0, column=mcol(1, 2), sticky="ew")
            ctk.CTkLabel(row, text=c.detail, font=(FONT, 12), anchor=anchor(),
                         justify=justify(), wraplength=600).grid(
                row=1, column=mcol(1, 2), sticky="ew")
            if c.fix and c.status != OK:
                ctk.CTkLabel(row, text=t("Fix: ") + c.fix, font=(FONT, 12), anchor=anchor(),
                             justify=justify(), wraplength=600, text_color=DOT[c.status]).grid(
                    row=2, column=mcol(1, 2), sticky="ew")
        self.btn_again.configure(state="normal")
        self.btn_ps.configure(state="normal")

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(format_report(self._checks))

    def _close(self) -> None:
        self._alive = False
        self.destroy()
