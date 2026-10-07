"""PhotoCull main window (plan section 10).

All heavy work runs in a Worker thread; this module only touches widgets from
the main thread, and talks to the worker through its event queue.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import __version__
from ..config import Config, load_config
from ..edit import parse_action
from ..logsetup import set_coverage_log, setup_logging
from ..paths import LOGS_DIR, CoverageLayout, list_jpegs
from ..picks import (USER_PICK, PickItem, Review, drop_edits, load_review, move_pick,
                     stale_edits, swap_pick)
from ..pipeline import STEP_TITLES, STEPS, RunParams, run_all, run_step
from ..runner import Question, StepResult, Worker
from ..split import distribute
from ..state import peek
from ..styles import list_styles, styles_dir
from .settings import (SettingsError, UserSettings, build_config, max_per_post,
                       preview_count)
from .thumbs import LRU, ThumbLoader
from .widgets import (FONT, GridItem, StatCard, StepCard, ThumbGrid, Tooltip, ask_choice,
                      theme)

MODE_LABELS = {"portrait": "Portrait (studio)", "action": "Action (matches)"}
CHOOSE_STYLE = "— choose a style —"
REASON_TEXT = {
    "blurry": "looks blurry", "closed_eyes": "eyes closed", "overexposed": "too bright",
    "underexposed": "too dark", "low_contrast": "flat, low contrast",
    "not_best_in_group": "another frame of this moment is better",
    USER_PICK: "you chose this one",
}
STATE_TEXT = {"not_started": ("Not started", "idle"), "running": ("Not finished", "warn"),
              "partial": ("Partial", "warn"), "done": ("Done ✓", "done")}
PS_TEXT = {
    "open": ("#2e9e4f", "Photoshop is ready"),
    "closed": ("#d99a00", "Photoshop is closed (it opens by itself, about 30 s)"),
    "missing": ("#d03b3b", "Photoshop not found (steps 1 and 3 still work)"),
    "no_pywin32": ("#d03b3b", "Photoshop link missing: run First-Time Setup again"),
    "unknown": ("#8a8a8a", "Checking Photoshop…"),
    "in_use": ("#2e8bd0", "Photoshop is being used by PhotoCull"),
}
LOG_KEEP_LINES = 1500
PREVIEW_H = 150


def _count(folder: Path | None) -> int:
    try:
        return len(list_jpegs(folder)) if folder and folder.is_dir() else 0
    except OSError:
        return 0


def _open_path(path: Path) -> None:
    try:
        os.startfile(str(path))                       # Windows: opens in Explorer / the viewer
    except OSError as exc:
        messagebox.showerror("PhotoCull", f"Could not open {path}\n\n{exc}")


class App(ctk.CTk):
    def __init__(self, cfg: Config, settings: UserSettings | None = None):
        self.settings = settings or UserSettings()
        ctk.set_appearance_mode(self.settings["appearance"])
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.cfg_base = cfg
        self.title(f"PhotoCull {__version__}")
        self.geometry("1240x940")
        self.minsize(1020, 720)
        self.layout: CoverageLayout | None = None
        self.worker: Worker | None = None
        self.run_kind = ""
        self.cur_step = ""
        self.transient: dict[str, tuple[str, str]] = {}
        self.review: Review | None = None
        self.view = "selected"
        self.ps_state = ("unknown", "")
        self._ps_q: queue.Queue = queue.Queue()
        self._stop_probe = threading.Event()
        self._probe_paused = False
        self._closing = False
        self._destroyed = False
        self._last_progress: tuple | None = None
        self._got_progress = False
        self._detail_job = None
        self._detail_token = 0
        self._detail_item: PickItem | None = None
        self._last_focus_refresh = 0.0
        self._refresh_busy = False
        self._refresh_wanted = False
        self._snap_q: queue.Queue = queue.Queue()
        self._n = dict(full=0, selected=0, alts=0, edit_in=0, split_in=0, edited=0)
        self._inputs_seen = None
        self._refresh_job = None
        self._suppress = False
        self.loader = ThumbLoader()
        self.cache = LRU()
        self.report_callback_exception = self._on_tk_error
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self._load_initial_folder()
        threading.Thread(target=self._probe_loop, daemon=True, name="ps-probe").start()
        self.bind("<FocusIn>", self._on_focus, add="+")
        self.after(80, self._poll)
        self.after(600, lambda: threading.Thread(target=_warm_up, daemon=True,
                                                 name="warm-up").start())

    # ================================================================== layout
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        pad = {"padx": 14}
        self._build_header().grid(row=0, column=0, sticky="ew", pady=(10, 4), **pad)
        self._build_dashboard().grid(row=1, column=0, sticky="ew", pady=4, **pad)
        self._build_settings().grid(row=2, column=0, sticky="ew", pady=4, **pad)
        self.advanced = self._build_advanced()
        self.advanced.grid(row=3, column=0, sticky="ew", pady=0, **pad)
        self._build_actions().grid(row=4, column=0, sticky="ew", pady=4, **pad)
        self._build_review().grid(row=5, column=0, sticky="nsew", pady=4, **pad)
        self._build_footer().grid(row=6, column=0, sticky="ew", pady=(4, 10), **pad)
        self._toggle_advanced(self.settings["advanced_open"])

    def _build_header(self):
        f = ctk.CTkFrame(self, corner_radius=10)
        f.columnconfigure(1, weight=1)
        self.btn_folder = ctk.CTkButton(f, text="Choose folder…", width=140, height=34,
                                        command=self._choose_folder)
        self.btn_folder.grid(row=0, column=0, padx=10, pady=10)
        self.lbl_folder = ctk.CTkLabel(f, text="No folder chosen", anchor="w", font=(FONT, 13))
        self.lbl_folder.grid(row=0, column=1, sticky="ew", padx=4)
        self.ps_dot = ctk.CTkLabel(f, text="●", font=(FONT, 18), width=20)
        self.ps_dot.grid(row=0, column=2, padx=(10, 0))
        self.ps_text = ctk.CTkLabel(f, text="", font=(FONT, 12), anchor="w")
        self.ps_text.grid(row=0, column=3, padx=(2, 12))
        self.mode_menu = ctk.CTkOptionMenu(f, values=list(MODE_LABELS.values()), width=170,
                                           command=self._on_mode)
        self.mode_menu.set(MODE_LABELS[self.settings["mode"]])
        self.mode_menu.grid(row=0, column=4, padx=6)
        Tooltip(self.mode_menu, "Portrait: studio and class photos, eyes matter.\n"
                                "Action: matches and events, sharpness matters most.")
        self.theme_switch = ctk.CTkSwitch(f, text="Dark", width=70, command=self._toggle_theme)
        if self.settings["appearance"] == "dark":
            self.theme_switch.select()
        self.theme_switch.grid(row=0, column=5, padx=(8, 12))
        return f

    def _build_dashboard(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        for i in range(5):
            f.columnconfigure(i, weight=1, uniform="stat")
        self.card_total = StatCard(f, "Photos")
        self.card_groups = StatCard(f, "Moments")
        self.card_selected = StatCard(f, "Selected")
        self.card_alts = StatCard(f, "Alternates")
        self.card_rejected = StatCard(f, "Rejected")
        for i, c in enumerate((self.card_total, self.card_groups, self.card_selected,
                               self.card_alts, self.card_rejected)):
            c.grid(row=0, column=i, sticky="nsew", padx=4)
        self.lbl_predict = ctk.CTkLabel(f, text="", font=(FONT, 15, "bold"))
        self.lbl_predict.grid(row=1, column=0, columnspan=5, pady=(4, 0))
        return f

    def _build_settings(self):
        f = ctk.CTkFrame(self, corner_radius=10)
        c = 0
        ctk.CTkLabel(f, text="Style", font=(FONT, 12)).grid(row=0, column=c, padx=(12, 4), pady=10)
        c += 1
        self.style_menu = ctk.CTkOptionMenu(f, values=[CHOOSE_STYLE], width=210,
                                            command=lambda _v: self._on_setting())
        self.style_menu.grid(row=0, column=c, padx=4)
        c += 1
        link = ctk.CTkButton(f, text="Open Styles folder", width=120, height=22,
                             font=(FONT, 11, "underline"), fg_color="transparent", hover=False,
                             text_color=("#1a62b8", "#7ab7ff"),
                             command=lambda: self._open_styles())
        link.grid(row=0, column=c, padx=(0, 10))
        c += 1
        ctk.CTkLabel(f, text="Max images per post", font=(FONT, 12)).grid(row=0, column=c, padx=(8, 4))
        c += 1
        self.var_mpp = ctk.StringVar(value=str(self.settings["max_per_post"]
                                               or self.cfg_base.split.max_per_post))
        e = ctk.CTkEntry(f, textvariable=self.var_mpp, width=52, justify="center")
        e.grid(row=0, column=c, padx=4)
        self.var_mpp.trace_add("write", lambda *_: self._on_setting())
        c += 1
        ctk.CTkLabel(f, text="Group gap (s)", font=(FONT, 12)).grid(row=0, column=c, padx=(12, 4))
        c += 1
        self.var_gap = ctk.StringVar()
        ge = ctk.CTkEntry(f, textvariable=self.var_gap, width=58, justify="center")
        ge.grid(row=0, column=c, padx=4)
        Tooltip(ge, "Photos taken closer together than this many seconds count as one moment.")
        self.var_gap.trace_add("write", lambda *_: self._on_setting())
        c += 1
        exp = self.settings["exposure"]
        self.var_exposure = ctk.BooleanVar(
            value=self.cfg_base.exposure_correction.enabled if exp is None else bool(exp))
        cb = ctk.CTkCheckBox(f, text="Fix exposure", variable=self.var_exposure,
                             command=self._on_setting)
        cb.grid(row=0, column=c, padx=(16, 4))
        Tooltip(cb, "Brightens dark photos before Photoshop. ON for night matches, "
                    "OFF for studio.")
        c += 1
        f.columnconfigure(c, weight=1)
        self.btn_adv = ctk.CTkButton(f, text="Advanced ▸", width=100, height=26,
                                     fg_color="transparent", hover=False,
                                     text_color=("#1a62b8", "#7ab7ff"), font=(FONT, 12),
                                     command=lambda: self._toggle_advanced())
        self.btn_adv.grid(row=0, column=c + 1, padx=10, sticky="e")
        self._fill_gap_var()
        return f

    def _build_advanced(self):
        f = ctk.CTkFrame(self, corner_radius=10)
        self.adv_vars: dict[str, ctk.StringVar] = {}
        d = self.cfg_base

        def field(r, c, label, key, hint, width=70):
            ctk.CTkLabel(f, text=f"{label} ({hint})", font=(FONT, 12), anchor="e").grid(
                row=r, column=c * 2, padx=(14, 4), pady=5, sticky="e")
            var = ctk.StringVar(value=str(self.settings[key]))
            ent = ctk.CTkEntry(f, textvariable=var, width=width, justify="center")
            ent.grid(row=r, column=c * 2 + 1, padx=(0, 6), sticky="w")
            var.trace_add("write", lambda *_: self._on_setting())
            self.adv_vars[key] = var
            return ent

        field(0, 0, "Blur threshold", "blurry_sharpness", f"{d.detection.blurry_sharpness:g}")
        field(0, 1, "Closed-eye threshold", "ear_closed_threshold",
              f"{d.detection.ear_closed_threshold:g}")
        field(0, 2, "Alternates per moment", "keep_alternates", f"{d.grouping.keep_alternates}")
        field(0, 3, "JPEG quality", "jpeg_quality", f"{d.photoshop.jpeg_quality}")
        field(1, 0, "Photoshop Action", "action", "Set/Action, optional", 130)
        field(1, 1, "Preview first photos", "preview", "0 = off")
        self.var_skip = ctk.BooleanVar(
            value=d.grouping.skip_blurry_groups if self.settings["skip_blurry_groups"] is None
            else bool(self.settings["skip_blurry_groups"]))
        ctk.CTkCheckBox(f, text="Skip blurry moments", variable=self.var_skip,
                        command=self._on_setting).grid(row=1, column=4, columnspan=2,
                                                       padx=14, sticky="w")
        for r, (label, key) in enumerate((("Cull reads from (empty = Full_Coverage)", "cull_input"),
                                          ("Edit reads from (empty = Selected)", "edit_input"),
                                          ("Split reads from (empty = Edited)", "split_input")),
                                         start=2):
            ctk.CTkLabel(f, text=label, font=(FONT, 12), anchor="e").grid(
                row=r, column=0, columnspan=2, padx=(14, 4), pady=3, sticky="e")
            var = ctk.StringVar(value=str(self.settings[key]))
            ent = ctk.CTkEntry(f, textvariable=var)
            ent.grid(row=r, column=2, columnspan=4, sticky="ew", padx=(0, 6))
            var.trace_add("write", lambda *_: self._on_setting())
            self.adv_vars[key] = var
            ctk.CTkButton(f, text="Browse…", width=80, height=26,
                          command=lambda v=var: self._browse_into(v)).grid(
                row=r, column=6, padx=(0, 6))
        f.columnconfigure(5, weight=1)
        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.grid(row=5, column=0, columnspan=8, sticky="ew", pady=(4, 8), padx=10)
        ctk.CTkButton(bar, text="Open logs folder", width=130, height=28,
                      command=lambda: _open_path(LOGS_DIR)).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Reset advanced values", width=150, height=28,
                      fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"),
                      text_color=("gray10", "gray95"),
                      command=self._reset_advanced).pack(side="left", padx=4)
        return f

    def _build_actions(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.columnconfigure((0, 1, 2), weight=1, uniform="card")
        self.btn_all = ctk.CTkButton(f, text="▶  Run All", height=46, font=(FONT, 19, "bold"),
                                     fg_color=("#2e8b57", "#2e8b57"),
                                     hover_color=("#256f46", "#3aa66a"),
                                     command=lambda: self._start("all"))
        self.btn_all.grid(row=0, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 6))
        self.tip_all = Tooltip(self.btn_all)
        self.cards = {
            "cull": StepCard(f, "1. Cull", "Pick the best photos",
                             "Reads Full_Coverage, keeps the best frame of each moment.",
                             lambda: self._start("cull"), lambda: self._open_step("cull")),
            "edit": StepCard(f, "2. Edit in Photoshop", "Edit photos",
                             "Reads Selected, applies your style, saves to Edited.",
                             lambda: self._start("edit"), lambda: self._open_step("edit")),
            "split": StepCard(f, "3. Split into posts", "Split photos",
                              "Reads Edited, copies into Post_1, Post_2… folders.",
                              lambda: self._start("split"), lambda: self._open_step("split")),
        }
        for i, step in enumerate(STEPS):
            self.cards[step].grid(row=1, column=i, sticky="nsew", padx=4)
        return f

    def _build_review(self):
        f = ctk.CTkFrame(self, corner_radius=10)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        self.seg = ctk.CTkSegmentedButton(top, values=["Selected", "Alternates"],
                                          command=self._on_view)
        self.seg.set("Selected")
        self.seg.pack(side="left")
        ctk.CTkLabel(top, text="Click a photo to see why it was picked. Double-click opens it.",
                     font=(FONT, 11), text_color=("gray35", "gray65")).pack(side="left", padx=12)
        self.grid_view = ThumbGrid(f, self.loader, self.cache, self._on_select, self._open_item)
        self.grid_view.grid(row=1, column=0, sticky="nsew", padx=(10, 4), pady=(0, 10))
        self.detail = self._build_detail(f)
        self.detail.grid(row=0, column=1, rowspan=2, sticky="ns", padx=(4, 10), pady=10)
        return f

    def _build_detail(self, master):
        f = ctk.CTkFrame(master, width=330, corner_radius=8)
        f.grid_propagate(False)
        f.columnconfigure(0, weight=1)
        self.preview = ctk.CTkLabel(f, text="", width=300, height=PREVIEW_H)
        self.preview.grid(row=0, column=0, padx=10, pady=(8, 2))
        self.lbl_name = ctk.CTkLabel(f, text="Select a photo", font=(FONT, 13, "bold"),
                                     wraplength=300, justify="left", anchor="w")
        self.lbl_name.grid(row=1, column=0, sticky="ew", padx=12)
        self.btn_a = ctk.CTkButton(f, text="", height=32, command=lambda: self._act("a"))
        self.btn_b = ctk.CTkButton(f, text="", height=28, command=lambda: self._act("b"),
                                   fg_color=("gray70", "gray30"),
                                   hover_color=("gray60", "gray40"),
                                   text_color=("gray10", "gray95"))
        self.btn_a.grid(row=2, column=0, sticky="ew", padx=12, pady=(4, 3))
        self.btn_b.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 4))
        self.tip_a, self.tip_b = Tooltip(self.btn_a), Tooltip(self.btn_b)
        self.btn_a.grid_remove()
        self.btn_b.grid_remove()
        self.lbl_info = ctk.CTkLabel(f, text="", font=(FONT, 12), wraplength=300,
                                     justify="left", anchor="nw")
        self.lbl_info.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 6))
        f.rowconfigure(4, weight=1)
        return f

    def _build_footer(self):
        f = ctk.CTkFrame(self, corner_radius=10)
        f.columnconfigure(0, weight=1)
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        row.columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(row)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.btn_stop = ctk.CTkButton(row, text="Stop", width=90, height=30,
                                      fg_color=("#c0392b", "#a93226"),
                                      hover_color=("#a93226", "#c0392b"),
                                      command=self._stop, state="disabled")
        self.btn_stop.grid(row=0, column=1)
        self.lbl_progress = ctk.CTkLabel(f, text="Ready.", anchor="w", font=(FONT, 12))
        self.lbl_progress.grid(row=1, column=0, sticky="ew", padx=12)
        self.log = ctk.CTkTextbox(f, height=60, font=("Consolas", 11), wrap="none",
                                  state="disabled")
        self.log.grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 10))
        self.log._textbox.tag_config("err", foreground="#ff7b72")
        return f

    # ================================================================== settings
    def _load_initial_folder(self) -> None:
        self._refresh_styles()
        saved = self.settings["main_folder"]
        if saved and Path(saved).is_dir():
            self._set_folder(Path(saved), ask_create=False)
        else:
            if saved:
                self.lbl_folder.configure(text="The last folder is gone. Choose it again.")
            self._refresh_all()

    def _on_setting(self, *_a) -> None:
        if self._suppress:
            return
        s = self.settings
        s["max_per_post"] = self.var_mpp.get().strip()
        s["exposure"] = bool(self.var_exposure.get())
        s["skip_blurry_groups"] = bool(self.var_skip.get())
        s["style"] = self._style_value()
        gap_key = "gap_action" if s["mode"] == "action" else "gap_portrait"
        s[gap_key] = self.var_gap.get().strip()
        for key, var in self.adv_vars.items():
            s[key] = var.get().strip()
        s.save()
        inputs = tuple(str(s[k]) for k in ("cull_input", "edit_input", "split_input"))
        if inputs != self._inputs_seen:
            self._inputs_seen = inputs
            if self._refresh_job:
                self.after_cancel(self._refresh_job)
            self._refresh_job = self.after(500, self._refresh_all)
        self._refresh_dashboard()
        self._update_buttons()

    def _style_value(self) -> str:
        v = self.style_menu.get()
        return "" if v == CHOOSE_STYLE else v

    def _fill_gap_var(self) -> None:
        s = self.settings
        key = "gap_action" if s["mode"] == "action" else "gap_portrait"
        g = self.cfg_base.grouping
        default = g.action_group_gap_seconds if s["mode"] == "action" else g.group_gap_seconds
        self._suppress = True
        self.var_gap.set(str(s[key]) if str(s[key]).strip() else f"{default:g}")
        self._suppress = False

    def _on_mode(self, label: str) -> None:
        self.settings["mode"] = next(k for k, v in MODE_LABELS.items() if v == label)
        self._fill_gap_var()
        self.settings.save()

    def _toggle_theme(self) -> None:
        dark = bool(self.theme_switch.get())
        self.settings["appearance"] = "dark" if dark else "light"
        ctk.set_appearance_mode("dark" if dark else "light")
        self.settings.save()
        self.grid_view.refresh_colors()

    def _toggle_advanced(self, show: bool | None = None) -> None:
        if show is None:
            show = not self.advanced.winfo_viewable()
        if show:
            self.advanced.grid()
            self.btn_adv.configure(text="Advanced ▾")
        else:
            self.advanced.grid_remove()
            self.btn_adv.configure(text="Advanced ▸")
        self.settings["advanced_open"] = show
        self.settings.save()

    def _reset_advanced(self) -> None:
        self._suppress = True
        for var in self.adv_vars.values():
            var.set("")
        self.var_skip.set(self.cfg_base.grouping.skip_blurry_groups)
        self.settings["skip_blurry_groups"] = None
        self._suppress = False
        self._on_setting()
        self.settings["skip_blurry_groups"] = None
        self.settings.save()

    def _browse_into(self, var: ctk.StringVar) -> None:
        d = filedialog.askdirectory(parent=self, title="Choose a folder",
                                    initialdir=str(self.layout.main) if self.layout else None)
        if d:
            var.set(str(Path(d)))

    def _refresh_styles(self) -> None:
        names = [p.name for p in list_styles(self.cfg_base)]
        want = self._style_value() or self.settings["style"] or self.cfg_base.photoshop.default_style_file
        if names:
            self.style_menu.configure(values=[CHOOSE_STYLE] + names)
            self.style_menu.set(want if want in names else (names[0] if len(names) == 1
                                                           else CHOOSE_STYLE))
        else:
            self.style_menu.configure(values=[CHOOSE_STYLE])
            self.style_menu.set(CHOOSE_STYLE)
        self.settings["style"] = self._style_value()

    def _open_styles(self) -> None:
        d = styles_dir(self.cfg_base)
        d.mkdir(exist_ok=True)
        _open_path(d)

    # ================================================================== folder
    def _choose_folder(self) -> None:
        d = filedialog.askdirectory(parent=self, title="Choose the main folder of this coverage",
                                    initialdir=self.settings["main_folder"] or None)
        if d:
            self._set_folder(Path(d), ask_create=True)

    def _set_folder(self, main: Path, ask_create: bool) -> None:
        if self.worker and self.worker.running:
            return
        layout = CoverageLayout.for_main(main)
        if ask_create and not layout.full_coverage.is_dir():
            if messagebox.askyesno(
                    "PhotoCull", "This folder has no Full_Coverage folder yet.\n\n"
                    "Create it now? Then copy all the photos of the coverage into it.",
                    parent=self):
                try:
                    layout.full_coverage.mkdir()
                except OSError as exc:
                    messagebox.showerror("PhotoCull", f"Could not create the folder:\n{exc}")
        self.layout = layout
        self.settings["main_folder"] = str(layout.main)
        self.settings.save()
        self.transient.clear()
        try:
            set_coverage_log(layout.log)
        except OSError:
            pass
        self.lbl_folder.configure(text=str(layout.main))
        self._clear_log()
        self._refresh_all()

    def _open_step(self, step: str) -> None:
        if self.layout:
            _open_path({"cull": self.layout.selected, "edit": self.layout.edited,
                        "split": self.layout.posts}[step])

    # ================================================================== refresh
    def _refresh_all(self) -> None:
        """Re-read folders and results in the background, then redraw (never blocks the window)."""
        self._refresh_styles()
        if not self.layout:
            self._n = dict(full=0, selected=0, alts=0, edit_in=0, split_in=0, edited=0)
            self.review = Review([], [])
            self._apply_snapshot(None)
            return
        self._refresh_wanted = True
        if self._refresh_busy:
            return
        self._refresh_busy, self._refresh_wanted = True, False
        s = self.settings
        lay = self.layout
        override = {k: str(s[k]).strip() for k in ("cull_input", "edit_input", "split_input")}

        def work():
            try:
                full = Path(override["cull_input"]) if override["cull_input"] else lay.full_coverage
                sel = Path(override["edit_input"]) if override["edit_input"] else lay.selected
                ed = Path(override["split_input"]) if override["split_input"] else lay.edited
                review = load_review(lay)
                n = dict(full=_count(full), selected=len(review.selected),
                         alts=len(review.alternates), edit_in=_count(sel),
                         split_in=_count(ed), edited=_count(lay.edited))
                self._snap_q.put((lay, n, review))
            except Exception:
                logging.getLogger("photocull").exception("Refresh failed")
                self._snap_q.put((lay, None, None))

        threading.Thread(target=work, daemon=True, name="refresh").start()

    def _apply_snapshot(self, snap) -> None:
        if snap is not None:
            lay, n, review = snap
            if lay != self.layout or n is None:
                return
            self._n, self.review = n, review
        self._refresh_dashboard()
        self._refresh_cards()
        self._fill_grid(keep=True)
        self._update_buttons()

    def _refresh_dashboard(self) -> None:
        n = self._n
        summary = {}
        if self.layout and self.layout.state.is_file():
            summary = peek(self.layout.state)["steps"]["cull"].get("summary") or {}
        self.card_total.set(n["full"])
        self.card_groups.set(summary.get("groups", "–"))
        self.card_selected.set(n["selected"] if summary or n["selected"] else "–")
        self.card_alts.set(n["alts"] if summary or n["alts"] else "–")
        if summary:
            rej = max(0, summary.get("total", 0) - n["selected"] - n["alts"])
            by = summary.get("rejected_by_reason", {})
            expo = by.get("overexposed", 0) + by.get("underexposed", 0)
            parts = [f"blurry {by.get('blurry', 0)}", f"eyes {by.get('closed_eyes', 0)}",
                     f"exposure {expo}"]
            self.card_rejected.set(rej, " · ".join(p for p in parts if not p.endswith(" 0")) or " ")
        else:
            self.card_rejected.set("–")
        try:
            mpp = max_per_post(self.settings, self.cfg_base)
            sel = n["selected"]
            if sel == 0:
                self.lbl_predict.configure(text="Run step 1 to see how many posts you will get.")
            else:
                sizes = distribute(sel, mpp)
                self.lbl_predict.configure(
                    text=f"{sel} selected  →  {len(sizes)} post{'s' if len(sizes) != 1 else ''}"
                         f"  →  {'/'.join(map(str, sizes))}")
        except SettingsError:
            self.lbl_predict.configure(text="Type a whole number for “Max images per post”.")

    def _refresh_cards(self) -> None:
        status = {}
        if self.layout and self.layout.state.is_file():
            steps = peek(self.layout.state)["steps"]
            status = {s: steps[s].get("status", "not_started") for s in STEPS}
        for step in STEPS:
            text, kind = self.transient.get(step) or STATE_TEXT.get(
                status.get(step, "not_started"), ("Not started", "idle"))
            self.cards[step].set_status(text, kind)
        lay = self.layout
        for step, folder in (("cull", lay.selected if lay else None),
                             ("edit", lay.edited if lay else None),
                             ("split", lay.posts if lay else None)):
            ok = bool(folder and folder.is_dir())
            self.cards[step].set_folder_available(ok, "Not created yet: run this step first.")

    def _update_buttons(self) -> None:
        running = bool(self.worker and self.worker.running)
        n = getattr(self, "_n", dict(full=0, selected=0, edit_in=0, split_in=0))
        lay = self.layout
        ps = self.ps_state[0]
        busy = "Wait for the current step to finish, or press Stop."
        no_folder = "Choose a main folder first."

        def cull_why():
            if running: return busy
            if not lay: return no_folder
            if n["full"] == 0:
                return ("No photos found. Copy the photos into the Full_Coverage folder first."
                        if not str(self.settings["cull_input"]).strip()
                        else "No photos in the folder chosen under Advanced → Cull reads from.")
            return ""

        def edit_why():
            if running: return busy
            if not lay: return no_folder
            if not self._style_value(): return "Choose a style first (a .psd file in the Styles folder)."
            if ps == "missing": return "Photoshop is not installed on this PC."
            if ps == "no_pywin32": return "The Photoshop link is missing. Run First-Time Setup again."
            if n["edit_in"] == 0: return "Nothing to edit yet. Run step 1 first."
            return ""

        def split_why():
            if running: return busy
            if not lay: return no_folder
            if n["split_in"] == 0: return "Nothing to split yet. Run step 2 first."
            return ""

        def all_why():
            return cull_why() or (edit_why() if n["full"] else "") or ""

        for step, why in (("cull", cull_why()), ("edit", edit_why()), ("split", split_why())):
            self.cards[step].set_enabled(not why, why)
        why = all_why()
        if not why and not self._style_value():
            why = "Choose a style first (a .psd file in the Styles folder)."
        # Run All starts from step 1, so only step 1's needs and Photoshop/style must hold.
        if not why and ps in ("missing", "no_pywin32"):
            why = "Photoshop is not ready, so step 2 cannot run."
        if not why and running:
            why = busy
        self.btn_all.configure(state="disabled" if why else "normal")
        self.tip_all.text = why
        self.btn_folder.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self._sync_detail_buttons()

    # ================================================================== review grid
    def _seg_labels(self) -> None:
        rv = self.review
        self.seg.configure(values=[f"Selected ({len(rv.selected)})",
                                   f"Alternates ({len(rv.alternates)})"])
        self.seg.set(f"Selected ({len(rv.selected)})" if self.view == "selected"
                     else f"Alternates ({len(rv.alternates)})")

    def _on_view(self, label: str) -> None:
        self.view = "selected" if label.startswith("Selected") else "alternates"
        self._fill_grid(keep=False)

    def _fill_grid(self, keep: bool) -> None:
        self._seg_labels()
        items = self.review.selected if self.view == "selected" else self.review.alternates
        gi = []
        for it in items:
            badge, kind = "", "warn"
            if USER_PICK in it.row.get("reasons", ""):
                badge, kind = "your pick", "done"
            elif it.row.get("weak_group") == "yes":
                badge = "check me"
            gi.append(GridItem(str(it.path), it.path, it.path.name, badge, kind))
        self.grid_view.empty_text = (
            "No photos here yet. Run step 1 (Cull)." if self.view == "selected"
            else "No alternates (they appear after Cull).")
        self.grid_view.set_items(gi, keep_selection=keep)

    def _find(self, key: str | None) -> PickItem | None:
        if not key or not self.review:
            return None
        for it in self.review.selected + self.review.alternates:
            if str(it.path) == key:
                return it
        return None

    def _on_select(self, gi: GridItem | None) -> None:
        self._detail_item = self._find(gi.key) if gi else None
        self._show_detail()

    def _open_item(self, gi: GridItem) -> None:
        _open_path(gi.path)

    def _show_detail(self) -> None:
        it = self._detail_item
        self._detail_token += 1
        if self._detail_job:
            self.after_cancel(self._detail_job)
            self._detail_job = None
        if it is None:
            self.preview.configure(image=None, text="")
            self.lbl_name.configure(text="Select a photo")
            self.lbl_info.configure(text="")
            self._sync_detail_buttons()
            return
        r = it.row
        self.lbl_name.configure(text=it.path.name)
        lines = []
        if r:
            lines.append(f"Moment {r.get('group_id', '?')}: {r.get('group_size', '?')} photo(s), "
                         f"this one ranks #{r.get('rank_in_group', '?')}")
            lines.append(f"Sharpness {_num(r.get('sharpness'))}   Brightness "
                         f"{_num(r.get('mean_brightness'))}   Score {_num(r.get('total_score'), 2)}")
            lines.append(f"Face: {r.get('face_found', '?')}   Eyes: {r.get('eyes_state') or '?'}")
            why = [REASON_TEXT.get(t, t) for t in r.get("reasons", "").split(";") if t]
            if why:
                lines.append("Notes: " + "; ".join(why))
            if r.get("weak_group") == "yes":
                lines.append("The program was not sure about this moment. Please check it.")
        else:
            lines.append("No score: this photo was not made by the Cull step.")
        self.lbl_info.configure(text="\n".join(lines))
        self.preview.configure(image=None, text="loading…")
        token = self._detail_token
        fut = self.loader.submit(it.path, 560)
        self._detail_job = self.after(40, lambda: self._poll_detail(fut, token))
        self._sync_detail_buttons()

    def _poll_detail(self, fut, token: int) -> None:
        self._detail_job = None
        if token != self._detail_token:
            return
        if not fut.done():
            self._detail_job = self.after(40, lambda: self._poll_detail(fut, token))
            return
        if fut.exception() is not None:
            self.preview.configure(image=None, text="(cannot show this photo)")
            return
        im = fut.result()
        scale = min(1.0, 300 / im.width, PREVIEW_H / im.height)
        size = (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
        self._preview_img = ctk.CTkImage(light_image=im, dark_image=im, size=size)
        self.preview.configure(image=self._preview_img, text="", width=300, height=size[1])

    def _sync_detail_buttons(self) -> None:
        it = self._detail_item
        running = bool(self.worker and self.worker.running)
        if it is None or not self.review:
            self.btn_a.grid_remove()
            self.btn_b.grid_remove()
            return
        self.btn_a.grid()
        self.btn_b.grid()
        if it.kind == "selected":
            alts = self.review.alternates_of(it)
            self.btn_a.configure(text="Swap with another frame of this moment")
            self.btn_b.configure(text="Remove from Selected")
            a_why = "Wait for the current step to finish." if running else (
                "" if alts else "This moment has no alternates.")
            b_why = "Wait for the current step to finish." if running else ""
        else:
            sel = self.review.selected_of(it)
            self.btn_a.configure(text="Use this instead of the pick" if sel else "Add to Selected")
            self.btn_b.configure(text="Also add to Selected")
            a_why = "Wait for the current step to finish." if running else ""
            b_why = "Wait for the current step to finish." if running else (
                "" if sel else "It is the only one, use the button above.")
            if not sel:
                self.btn_b.grid_remove()
        for btn, tip, why in ((self.btn_a, self.tip_a, a_why), (self.btn_b, self.tip_b, b_why)):
            btn.configure(state="disabled" if why else "normal")
            tip.text = why

    def _act(self, which: str) -> None:
        it = self._detail_item
        if it is None or not self.layout or (self.worker and self.worker.running):
            return
        lay = self.layout
        try:
            demoted: list[str] = []
            if it.kind == "selected":
                if which == "a":
                    alts = self.review.alternates_of(it)
                    pick = alts[0] if len(alts) == 1 else self._choose_alternate(alts)
                    if pick is None:
                        return
                    swap_pick(lay, it.path, pick.path)
                    demoted.append(it.source_name)
                else:
                    move_pick(lay, it.path, "alternate")
                    demoted.append(it.source_name)
            else:
                sel = self.review.selected_of(it)
                if which == "a" and sel:
                    swap_pick(lay, sel[0].path, it.path)
                    demoted += [s.source_name for s in sel[:1]]
                else:
                    move_pick(lay, it.path, "selected")
        except Exception as exc:
            logging.getLogger("photocull").exception("Swap failed")
            messagebox.showerror("PhotoCull", f"Could not change the pick:\n{exc}")
            self._refresh_all()
            return
        stale = stale_edits(lay, demoted)
        if stale:
            ans = ask_choice(
                self, "Edited photo",
                f"{', '.join(Path(s).name for s in stale)} was already edited, but is no longer "
                "picked. Delete the edited copy so it does not end up in your posts?\n"
                "(Photos in Full_Coverage and Alternates are not touched.)",
                [("Delete the edited copy", "delete")], cancel_label="Keep it")
            if ans == "delete":
                drop_edits(lay, demoted)
        self._append_log("Pick changed. Run step 2 again to edit the new photo.")
        self._detail_item = None
        self._refresh_all()
        self.grid_view.select(None)

    def _choose_alternate(self, alts: list[PickItem]) -> PickItem | None:
        picked: dict = {"item": None}
        dlg = ctk.CTkToplevel(self)
        dlg.title("Choose the photo to use")
        dlg.transient(self)
        ctk.CTkLabel(dlg, text="Click the photo you want, then press Use this photo.",
                     font=(FONT, 13)).pack(padx=16, pady=(14, 6))
        sel: dict = {"gi": None}
        grid = ThumbGrid(dlg, self.loader, self.cache, lambda gi: sel.update(gi=gi))
        grid.pack(fill="both", expand=True, padx=12, pady=4)
        grid.set_items([GridItem(str(a.path), a.path, a.path.name) for a in alts])
        dlg.geometry("620x380")

        def ok():
            gi = sel["gi"]
            picked["item"] = next((a for a in alts if str(a.path) == gi.key), None) if gi else None
            if picked["item"]:
                dlg.destroy()

        ctk.CTkButton(dlg, text="Use this photo", command=ok).pack(pady=(4, 14))
        dlg.after(120, lambda: dlg.winfo_exists() and (dlg.grab_set(), dlg.focus_force()))
        self.wait_window(dlg)
        return picked["item"]

    # ================================================================== running
    def _collect(self, kind: str):
        """Settings -> (cfg, RunParams). Raises SettingsError with a friendly message."""
        s = self.settings
        cfg = build_config(self.cfg_base, s)
        action = None
        if kind in ("edit", "all"):
            try:
                action = parse_action(str(s["action"]) or cfg.photoshop.default_action)
            except ValueError as exc:
                raise SettingsError(f"Photoshop Action: {exc}") from None
        mpp = max_per_post(s, cfg)

        def folder(key):
            v = str(s[key]).strip()
            return v or None

        params = RunParams(mode=s["mode"], style=self._style_value() or None, action=action,
                           exposure=bool(self.var_exposure.get()),
                           preview=preview_count(s) if kind in ("edit", "all") else 0,
                           max_per_post=mpp, cull_input=folder("cull_input"),
                           edit_input=folder("edit_input"), split_input=folder("split_input"))
        return cfg, params

    def _start(self, kind: str) -> None:
        if self.worker and self.worker.running or not self.layout:
            return
        try:
            cfg, params = self._collect(kind)
        except SettingsError as exc:
            messagebox.showwarning("PhotoCull", str(exc), parent=self)
            return
        lay = self.layout
        steps = STEPS if kind == "all" else (kind,)
        for s in steps:
            self.transient[s] = ("Queued", "idle") if kind == "all" else ("Running…", "running")
        self.run_kind, self.cur_step = kind, ""
        self._got_progress = False
        self._last_progress = None
        set_coverage_log(lay.log)

        def job(progress, log, cancel):
            w = self.worker

            def decide(step, res, choices):
                return w.ask("decide", {"step": step, "res": res, "choices": choices})

            def confirm_preview(res):
                return bool(w.ask("preview", {"res": res}))

            def hook(step, what, res):
                w.events.put(("step", step, what, res))

            if kind == "all":
                return run_all(lay, cfg, params, progress, log, cancel, decide,
                               confirm_preview, hook)
            hook(kind, "running", None)
            res = run_step(kind, lay, cfg, params, progress, log, cancel, decide)
            hook(kind, "finished", res)
            return res

        self.worker = Worker(job)
        self._probe_paused = kind in ("edit", "all")
        if self._probe_paused:
            self._set_ps("in_use", "")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.lbl_progress.configure(text=self._starting_text(kind), text_color=("gray10", "gray90"))
        self._append_log(f"--- {'Run All' if kind == 'all' else STEP_TITLES[kind]} ---")
        self._refresh_cards()
        self._update_buttons()
        self.worker.start()

    @staticmethod
    def _starting_text(kind: str) -> str:
        if kind in ("edit", "all"):
            return "Starting… (Photoshop can take up to 30 seconds to open)"
        return "Starting…"

    def _stop(self) -> None:
        if self.worker and self.worker.running:
            self.worker.cancel.set()
            self.btn_stop.configure(state="disabled")
            self.lbl_progress.configure(text="Stopping after the current photo…")

    def _poll(self) -> None:
        if self._destroyed:
            return
        try:
            self._drain()
        except Exception:
            logging.getLogger("photocull").exception("UI poll failed")
        self.after(80, self._poll)

    def _drain(self) -> None:
        while True:                                   # Photoshop status from the probe thread
            try:
                st, detail = self._ps_q.get_nowait()
            except queue.Empty:
                break
            if not self._probe_paused:
                self._set_ps(st, detail)
        while True:                                   # folder snapshots from the refresh thread
            try:
                snap = self._snap_q.get_nowait()
            except queue.Empty:
                break
            self._refresh_busy = False
            self._apply_snapshot(snap)
            if self._refresh_wanted:
                self._refresh_all()
        w = self.worker
        if w is None:
            return
        done_res = None
        logs: list[str] = []
        for ev in w.poll():
            kind = ev[0]
            if kind == "progress":
                self._last_progress = ev[1:]
                self._got_progress = True
            elif kind == "log":
                logs.append(ev[1])
            elif kind == "step":
                self._on_step(ev[1], ev[2], ev[3])
            elif kind == "ask":
                self._answer(ev[1])
            elif kind == "done":
                done_res = ev[1]
        if logs:
            self._append_log("\n".join(logs))
        if self._last_progress is not None and w.running:
            self._show_progress(*self._last_progress)
        if done_res is not None:
            self._finished(done_res)

    def _show_progress(self, done: int, total: int, name: str) -> None:
        if total > 0:
            if self.progress.cget("mode") == "indeterminate":
                self.progress.stop()
                self.progress.configure(mode="determinate")
            self.progress.set(min(1.0, done / total))
        step = f"Step {STEPS.index(self.cur_step) + 1}/3 · {STEP_TITLES[self.cur_step]}  —  " \
            if self.run_kind == "all" and self.cur_step else ""
        text = f"{step}{min(done + (1 if name else 0), total)} / {total}" if total else step + "…"
        self.lbl_progress.configure(text=f"{text}   {name}".rstrip(),
                                    text_color=("gray10", "gray90"))

    def _on_step(self, step: str, what: str, res: StepResult | None) -> None:
        if what == "running":
            self.cur_step = step
            self.transient[step] = ("Running…", "running")
            self._last_progress = None
            if self.progress.cget("mode") != "indeterminate":
                self.progress.configure(mode="indeterminate")
                self.progress.start()
            self.lbl_progress.configure(text=self._starting_text(step))
        elif res is not None:
            self.transient[step] = self._badge_for(res)
            if res.data.get("declined"):
                self.transient.pop(step, None)
            self._refresh_all()
        self._refresh_cards()

    @staticmethod
    def _badge_for(res: StepResult) -> tuple[str, str]:
        if res.ok:
            return ("Done ✓", "done")
        if res.data.get("preview_done"):
            return ("Preview ready", "warn")
        if res.cancelled:
            return ("Stopped", "warn")
        return ("Error", "error")

    def _finished(self, res: StepResult) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1.0 if res.ok else self.progress.get())
        if self.run_kind != "all":
            step = self.run_kind
            if res.data.get("declined"):
                self.transient.pop(step, None)
            else:
                self.transient[step] = self._badge_for(res)
        for step in list(self.transient):                 # steps that never started
            if self.transient[step][0] == "Queued":
                self.transient.pop(step)
        self._probe_paused = False
        self._ps_q.put(("unknown", ""))
        msg = res.message or ("Done." if res.ok else "Something went wrong.")
        color = ("#1b6b2f", "#8fe3a5") if res.ok else (
            ("#8a5a00", "#ffd27a") if res.cancelled or res.data.get("preview_done")
            or res.data.get("declined") else ("#9b1c1c", "#ff9b9b"))
        self.lbl_progress.configure(text=msg, text_color=color)
        soft = res.cancelled or res.data.get("preview_done") or res.data.get("declined")
        self._append_log(("✓ " if res.ok else "• " if soft else "✗ ") + msg)
        for err in res.errors[:5]:
            self._append_log("  " + err, error=True)
        if len(res.errors) > 5:
            self._append_log(f"  …and {len(res.errors) - 5} more (see the log).", error=True)
        self.worker_result = res
        self.worker = None
        self._refresh_all()
        if self._closing:
            self._destroy()
            return
        if not (res.ok or res.cancelled or res.data.get("preview_done")
                or res.data.get("declined")):
            messagebox.showerror("PhotoCull", msg, parent=self)

    # ---- questions from the worker
    def _answer(self, q: Question) -> None:
        if q.kind == "decide":
            q.reply(self._decide(q.payload))
        elif q.kind == "preview":
            q.reply(self._preview_dialog(q.payload["res"]))
        else:
            q.reply(None)

    def _decide(self, p: dict) -> str | None:
        step, res, choices = p["step"], p["res"], p["choices"]
        n = res.data.get("existing", 0)
        if step == "cull":
            msg = (f"Selected and Alternates already hold {n} photo(s) from an earlier run.\n\n"
                   "Replace them with the new picks? (The photos in Full_Coverage are never "
                   "touched.)")
            opts = [("Replace them", "clear")]
        elif step == "edit":
            msg = (f"{n} photo(s) were already edited with different settings (style, action "
                   "or exposure).\n\nEdit them again with the new settings? The old edited "
                   "copies will be replaced.")
            opts = [("Edit them again", "reprocess")]
        else:
            msg = (f"Posts already has {n} post folder(s).\n\nDelete the old Post_N folders "
                   "and start again, or add new posts after them?")
            opts = [("Delete the old posts", "clear"), ("Add new posts", "new")]
        if "keep" in choices:
            opts.append(("Keep them and continue", "keep"))
        return ask_choice(self, STEP_TITLES[step], msg, opts)

    def _preview_dialog(self, res: StepResult) -> bool:
        while True:
            ans = ask_choice(
                self, "Preview ready",
                f"{res.message}\n\nOpen the Edited folder and check how the photos look. "
                "Continue with the rest?",
                [("Open Edited folder", "open"), ("Continue", "go")], cancel_label="Stop here")
            if ans == "open" and self.layout:
                _open_path(self.layout.edited)
                continue
            return ans == "go"

    # ================================================================== log
    def _append_log(self, text: str, error: bool = False) -> None:
        tb = self.log._textbox
        self.log.configure(state="normal")
        for line in text.splitlines() or [""]:
            bad = error or line.startswith(("ERROR", "Warning", "Unreadable"))
            tb.insert("end", line + "\n", "err" if bad else ())
        lines = int(tb.index("end-1c").split(".")[0])
        if lines > LOG_KEEP_LINES:
            tb.delete("1.0", f"{lines - LOG_KEEP_LINES + 300}.0")
        self.log.configure(state="disabled")
        tb.see("end")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log._textbox.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ================================================================== photoshop probe
    def _probe_loop(self) -> None:
        from ..photoshop import probe
        while not self._stop_probe.is_set():
            if not self._probe_paused:
                try:
                    self._ps_q.put(probe())
                except Exception:
                    self._ps_q.put(("closed", ""))
            self._stop_probe.wait(4.0)

    def _set_ps(self, state: str, detail: str) -> None:
        self.ps_state = (state, detail)
        color, text = PS_TEXT[state]
        if state == "open" and detail:
            text = f"Photoshop {detail} is ready"
        self.ps_dot.configure(text_color=color)
        self.ps_text.configure(text=text)
        self._update_buttons()

    # ================================================================== misc
    def _on_focus(self, e=None) -> None:
        if e is not None and e.widget is not self:
            return
        now = time.monotonic()
        if now - self._last_focus_refresh < 2 or (self.worker and self.worker.running):
            return
        self._last_focus_refresh = now
        self._refresh_all()                           # new photos or styles dropped in meanwhile

    def _on_tk_error(self, exc, val, tb) -> None:
        text = "".join(traceback.format_exception(exc, val, tb))
        logging.getLogger("photocull").error("UI error:\n%s", text)
        try:
            LOGS_DIR.mkdir(exist_ok=True)
            with open(LOGS_DIR / "crash.log", "a", encoding="utf-8") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + text + "\n")
        except OSError:
            pass
        try:
            self._append_log(f"Something went wrong in the window: {val}. "
                             "Details are in the logs folder.", error=True)
        except Exception:
            pass

    def _on_close(self) -> None:
        if self.worker and self.worker.running:
            if not messagebox.askyesno("PhotoCull", "A step is still running.\n\n"
                                       "Stop it and close the window?", parent=self):
                return
            self._closing = True
            self.worker.cancel.set()
            self.lbl_progress.configure(text="Stopping after the current photo, then closing…")
            self.after(20000, self._destroy)              # never hang forever
            return
        self._destroy()

    def _destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._stop_probe.set()
        self.loader.shutdown()
        try:
            self.destroy()
        except Exception:
            pass


def _warm_up() -> None:
    """Import the heavy libraries (OpenCV, MediaPipe) while the user is still looking at the
    window, so pressing Cull does not freeze it for a couple of seconds."""
    try:
        from .. import cull, edit, faces, scoring  # noqa: F401
    except Exception:
        logging.getLogger("photocull").exception("Warm-up import failed")


def _num(v, digits: int = 0) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "?"


def main(cfg: Config | None = None) -> int:
    setup_logging()
    app = App(cfg or load_config())
    app.mainloop()
    return 0
