"""PhotoCull main window (plan section 10).

All heavy work runs in a Worker thread; this module only touches widgets from
the main thread, and talks to the worker through its event queue.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import __version__, updater
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
from . import i18n
from . import palette as P
from .i18n import anchor, justify, mcol, px, side, sticky, t, tr_msg
from .settings import (DEFAULTS, SettingsError, UserSettings, build_config, max_per_post,
                       preview_count)
from .thumbs import LRU, ThumbLoader
from .widgets import (FONT, GridItem, StatCard, StepCard, ThumbGrid, Tooltip, ask_choice,
                      card as make_card, icon, set_text_scale)

MODE_KEYS = {"portrait": "Portrait (studio)", "action": "Action (matches)"}


def mode_labels() -> dict[str, str]:
    return {k: t(v) for k, v in MODE_KEYS.items()}


def choose_style() -> str:
    return t("— choose a style —")


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
NAV = {"home": "Home", "review": "Review photos", "settings": "Settings",
       "activity": "Activity", "updates": "Updates"}
TEXT_STEPS = (1.0, 1.15, 1.3, 1.5, 1.7)       # sizes the A- / A+ buttons step through
MACHINE_KEYS = {"advanced_open", "last_update_check", "skipped_version", "main_folder", "cull_input", "edit_input", "split_input"}
PREVIEW_H = 240


def _count(folder: Path | None) -> int:
    try:
        return len(list_jpegs(folder)) if folder and folder.is_dir() else 0
    except OSError:
        return 0


def _open_path(path: Path) -> None:
    try:
        os.startfile(str(path))                       # Windows: opens in Explorer / the viewer
    except OSError as exc:
        messagebox.showerror("PhotoCull", t("Could not open {path}\n\n{exc}").format(
            path=path, exc=exc))


class App(ctk.CTk):
    def __init__(self, cfg: Config, settings: UserSettings | None = None):
        self.settings = settings or UserSettings()
        i18n.set_lang(self.settings["language"])
        ctk.set_appearance_mode(self.settings["appearance"])
        ctk.set_default_color_theme("blue")
        P.apply()
        self._text_scale = self._clean_scale(self.settings["text_scale"])
        ctk.set_widget_scaling(self._text_scale)
        set_text_scale(self._text_scale)
        ctk.ThemeManager.theme["CTkFont"]["family"] = FONT        # one font everywhere
        super().__init__()
        self.cfg_base = cfg
        self.title(f"PhotoCull {__version__}")
        self._fit_window()
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
        self._update_rel = None                  # newest release, when newer than this program
        self._update_dismissed = False           # the user closed the banner (this session)
        self._upd_state = "idle"                 # idle / checking / downloading
        self._upd_error = ""
        self._upd_checked = False
        self._upd_q: queue.Queue = queue.Queue()
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
        self.after(5000, self._maybe_auto_check)
        self.after(600, lambda: threading.Thread(target=_warm_up, daemon=True,
                                                 name="warm-up").start())

    # ================================================================== layout
    # Left sidebar with four pages (the way Lightroom / Capture One split their work):
    #   Home      - the 3 things you do every time: folder, options, run
    #   Review    - the picked photos and swapping them
    #   Settings  - thresholds, folders and tools, each with a one-line explanation
    #   Activity  - what the program did, step by step
    def _build(self) -> None:
        self.page_key = getattr(self, "page_key", "home")
        for c in range(2):
            self.columnconfigure(c, weight=0)
        self.columnconfigure(mcol(1, 2), weight=1)
        self.rowconfigure(0, weight=1)
        self._build_sidebar().grid(row=0, column=mcol(0, 2), rowspan=2, sticky="ns")
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=mcol(1, 2), sticky="nsew", padx=px(12, 14), pady=(12, 0))
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=0)
        content.rowconfigure(1, weight=1)
        self.banner = self._build_banner(content)         # row 0, hidden until an update exists
        self.pages: dict[str, ctk.CTkBaseClass] = {}
        for key, builder in (("home", self._build_home), ("review", self._build_review),
                             ("settings", self._build_settings_page),
                             ("activity", self._build_activity),
                             ("updates", self._build_updates)):
            page = builder(content)
            page.grid(row=1, column=0, sticky="nsew")
            self.pages[key] = page
        self._build_footer().grid(row=1, column=mcol(1, 2), sticky="ew", padx=px(12, 14),
                                  pady=(6, 10))
        self.show_page(self.page_key)
        self._refresh_update_ui()

    # ---- small building blocks
    def _page_header(self, parent, title: str, subtitle: str = "") -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(f, text=title, font=(FONT, 22, "bold"), anchor=anchor()).pack(fill="x")
        if subtitle:
            ctk.CTkLabel(f, text=subtitle, font=(FONT, 12), anchor=anchor(), justify=justify(),
                         text_color=P.MUTED, wraplength=640).pack(fill="x")
        return f

    def _section(self, parent, title: str, number: int | None = None):
        """A titled card. Returns (card, body) - put the widgets in `body`."""
        card = make_card(parent)
        card.columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        if number is not None:
            ctk.CTkLabel(head, text=str(number), width=26, height=26, corner_radius=13,
                         fg_color=P.ACCENT_SOFT, text_color=P.ACCENT_TEXT,
                         font=(FONT, 13, "bold")).pack(side=side("left"), padx=px(0, 10))
        ctk.CTkLabel(head, text=title, font=(FONT, 15, "bold"), anchor=anchor()).pack(
            side=side("left"))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
        card.head = head
        return card, body

    def _grey_button(self, parent, text: str, command, width: int = 140):
        return ctk.CTkButton(parent, text=text, width=width, height=30, command=command,
                             fg_color=P.NEUTRAL, hover_color=P.NEUTRAL_HOVER,
                             text_color=P.TEXT, text_color_disabled=P.FAINT)

    # ---- sidebar
    def _nav_text(self, key: str) -> str:
        text = t(NAV[key])
        if key == "review" and self.review and (self.review.selected or self.review.alternates):
            text += f"  ({len(self.review.selected)})"
        if key == "updates" and getattr(self, "_update_rel", None):
            text += "  ●"
        return text

    def _build_sidebar(self):
        f = ctk.CTkFrame(self, width=224, corner_radius=0, fg_color=P.SIDEBAR)
        f.grid_propagate(False)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1 + len(NAV), weight=1)
        brand = ctk.CTkFrame(f, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=16, pady=(18, 18))
        logo = icon("camera", P.WHITE, 20)
        ctk.CTkLabel(brand, text="" if logo else "P", image=logo, width=38, height=38,
                     corner_radius=10, fg_color=P.ACCENT, text_color="white",
                     font=(FONT, 18, "bold")).pack(side=side("left"))
        name = ctk.CTkFrame(brand, fg_color="transparent")
        name.pack(side=side("left"), padx=10)
        ctk.CTkLabel(name, text="PhotoCull", font=(FONT, 18, "bold"), height=22,
                     anchor=anchor()).pack(fill="x")
        ctk.CTkLabel(name, text=f"v{__version__}", font=(FONT, 11), height=14, anchor=anchor(),
                     text_color=P.MUTED).pack(fill="x")
        self.nav_btns: dict[str, ctk.CTkButton] = {}
        for i, key in enumerate(NAV):
            b = ctk.CTkButton(f, text=self._nav_text(key), anchor=anchor(), height=40,
                              font=(FONT, 14), corner_radius=10, compound=side("left"),
                              command=lambda k=key: self.show_page(k))
            b.grid(row=1 + i, column=0, sticky="ew", padx=12, pady=2)
            self.nav_btns[key] = b
        # bottom block: Photoshop status, theme, language, text size
        bottom = ctk.CTkFrame(f, fg_color="transparent")
        bottom.grid(row=2 + len(NAV), column=0, sticky="ew", padx=12, pady=(0, 14))
        bottom.columnconfigure(0, weight=1)
        ps = make_card(bottom, P.CARD2, 10)
        ps.grid(row=0, column=0, sticky="ew")
        ps.columnconfigure(mcol(1, 2), weight=1)
        self.ps_dot = ctk.CTkLabel(ps, text="●", font=(FONT, 14), width=16)
        self.ps_dot.grid(row=0, column=mcol(0, 2), sticky="n", padx=px(10, 0), pady=(8, 0))
        self.ps_text = ctk.CTkLabel(ps, text="", font=(FONT, 11), anchor=anchor(),
                                    justify=justify(), wraplength=140, text_color=P.MUTED)
        self.ps_text.grid(row=0, column=mcol(1, 2), sticky="ew", padx=px(6, 10), pady=6)
        self.theme_switch = ctk.CTkSwitch(bottom, text=t("Dark"), width=70,
                                          command=self._toggle_theme)
        if self.settings["appearance"] == "dark":
            self.theme_switch.select()
        self.theme_switch.grid(row=1, column=0, sticky=sticky("w"), pady=(12, 10))
        tools = ctk.CTkFrame(bottom, fg_color="transparent")
        tools.grid(row=2, column=0, sticky="ew")
        self.btn_lang = self._grey_button(tools, "English" if i18n.is_ar() else "العربية",
                                          self._toggle_language, 86)
        self.btn_lang.pack(side=side("left"))
        Tooltip(self.btn_lang, t("Switch the window between English and Arabic"))
        for text, delta, tip in (("A−", -1, "Smaller text"), ("A+", 1, "Bigger text")):
            b = self._grey_button(tools, text, lambda d=delta: self._change_text_size(d), 40)
            b.configure(font=(FONT, 13, "bold"))
            b.pack(side=side("left"), padx=px(6, 0))
            Tooltip(b, t(tip))
        return f

    def show_page(self, key: str) -> None:
        self.page_key = key
        self.pages[key].lift()
        for k, b in self.nav_btns.items():
            on = k == key
            b.configure(fg_color=P.ACCENT_SOFT if on else "transparent",
                        text_color=P.ACCENT_TEXT if on else P.TEXT,
                        hover_color=P.ACCENT_SOFT if on else P.NEUTRAL,
                        font=(FONT, 14, "bold" if on else "normal"),
                        image=icon(k, P.ACCENT_TEXT if on else P.MUTED))

    # ---- page 1: Home
    def _build_home(self, parent):
        f = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        f.columnconfigure((0, 1), weight=1, uniform="top")

        # 1. folder (one half of the top row)
        card, body = self._section(f, t("Coverage folder"), 1)
        card.grid(row=0, column=mcol(0, 2), sticky="nsew", pady=(0, 10), padx=px(0, 5))
        body.columnconfigure(0, weight=1)
        self.btn_folder = ctk.CTkButton(body, text=t("Choose folder…"), width=160, height=38,
                                        font=(FONT, 13, "bold"), image=icon("folder"),
                                        compound=side("left"), command=self._choose_folder)
        self.btn_folder.grid(row=0, column=0, sticky=sticky("w"))
        self.lbl_folder = ctk.CTkLabel(body, text=t("No folder chosen"), anchor=anchor(),
                                       font=(FONT, 13, "bold"), wraplength=360,
                                       justify=justify())
        self.lbl_folder.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ctk.CTkLabel(body, text=t("Pick any folder that has your photos. They are never changed: the results are "
                                  "saved in a new folder next to it."),
                     font=(FONT, 11), anchor=anchor(), justify=justify(), wraplength=360,
                     text_color=P.MUTED).grid(row=2, column=0, sticky="ew", pady=(4, 0))

        # 2. options (the other half)
        card, body = self._section(f, t("Options"), 2)
        card.grid(row=0, column=mcol(1, 2), sticky="nsew", pady=(0, 10), padx=px(5, 0))
        body.columnconfigure(mcol(1, 2), weight=1)
        ctk.CTkButton(card.head, text=t("Open Styles folder"), width=120, height=24,
                      font=(FONT, 11, "underline"), fg_color="transparent", hover=False,
                      text_color=P.ACCENT_TEXT,
                      command=self._open_styles).pack(side=side("right"))
        for r, text in enumerate((t("Type of shoot"), t("Style"), t("Max images per post"))):
            ctk.CTkLabel(body, text=text, font=(FONT, 12), text_color=P.MUTED,
                         anchor=anchor()).grid(row=r, column=mcol(0, 2), sticky=sticky("w"),
                                               padx=px(0, 14), pady=4)
        self.mode_menu = ctk.CTkOptionMenu(body, values=list(mode_labels().values()), width=170,
                                           command=self._on_mode)
        self.mode_menu.set(mode_labels()[self.settings["mode"]])
        self.mode_menu.grid(row=0, column=mcol(1, 2), sticky="ew", pady=4)
        Tooltip(self.mode_menu, t("Portrait: studio and class photos, eyes matter.\n"
                                  "Action: matches and events, sharpness matters most."))
        self.style_menu = ctk.CTkOptionMenu(body, values=[choose_style()], width=170,
                                            command=lambda _v: self._on_setting())
        self.style_menu.grid(row=1, column=mcol(1, 2), sticky="ew", pady=4)
        self.var_mpp = ctk.StringVar(value=str(self.settings["max_per_post"]
                                               or self.cfg_base.split.max_per_post))
        mpp = ctk.CTkFrame(body, fg_color="transparent")
        mpp.grid(row=2, column=mcol(1, 2), sticky="ew", pady=4)
        ctk.CTkEntry(mpp, textvariable=self.var_mpp, width=64, justify="center").pack(
            side=side("left"))
        self.var_mpp.trace_add("write", lambda *_: self._on_setting())
        exp = self.settings["exposure"]
        self.var_exposure = ctk.BooleanVar(
            value=self.cfg_base.exposure_correction.enabled if exp is None else bool(exp))
        cb = ctk.CTkCheckBox(mpp, text=t("Fix exposure"), variable=self.var_exposure,
                             command=self._on_setting)
        cb.pack(side=side("right"))
        Tooltip(cb, t("Brightens dark photos before Photoshop. ON for night matches, "
                      "OFF for studio."))

        # 3. run
        card, body = self._section(f, t("Run"), 3)
        card.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        body.columnconfigure((0, 1, 2), weight=1, uniform="card")
        self.btn_all = ctk.CTkButton(body, text=t("▶  Run All"), height=50, corner_radius=12,
                                     font=(FONT, 19, "bold"), fg_color=P.GO,
                                     text_color_disabled=("#BFE8CF", "#9BD3AF"),
                                     hover_color=P.GO_HOVER,
                                     command=lambda: self._start("all"))
        self.btn_all.grid(row=0, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 4))
        self.tip_all = Tooltip(self.btn_all)
        self.lbl_predict = ctk.CTkLabel(body, text="", font=(FONT, 13, "bold"),
                                        text_color=P.MUTED)
        self.lbl_predict.grid(row=1, column=0, columnspan=3, pady=(0, 6))
        self.cards = {
            "cull": StepCard(body, t("1. Cull"), t("Pick the best photos"),
                             t("Reads Full_Coverage, keeps the best frame of each moment."),
                             lambda: self._start("cull"), lambda: self._open_step("cull")),
            "edit": StepCard(body, t("2. Edit in Photoshop"), t("Edit photos"),
                             t("Reads Selected, applies your style, saves to Edited."),
                             lambda: self._start("edit"), lambda: self._open_step("edit")),
            "split": StepCard(body, t("3. Split into posts"), t("Split photos"),
                              t("Reads Edited, copies into Post_1, Post_2… folders."),
                              lambda: self._start("split"), lambda: self._open_step("split")),
        }
        for i, step in enumerate(STEPS):
            self.cards[step].grid(row=2, column=mcol(i, 3), sticky="nsew", padx=4)

        # results summary
        card, body = self._section(f, t("Summary"))
        card.grid(row=2, column=0, columnspan=2, sticky="ew")
        for i in range(5):
            body.columnconfigure(i, weight=1, uniform="stat")
        self.card_total = StatCard(body, t("Photos"))
        self.card_groups = StatCard(body, t("Moments"))
        self.card_selected = StatCard(body, t("Selected"), P.GO)
        self.card_alts = StatCard(body, t("Alternates"))
        self.card_rejected = StatCard(body, t("Rejected"), P.BAD_TEXT)
        for i, sc in enumerate((self.card_total, self.card_groups, self.card_selected,
                                self.card_alts, self.card_rejected)):
            sc.grid(row=0, column=mcol(i, 5), sticky="nsew", padx=4)
        self.btn_go_review = ctk.CTkButton(card.head, text=t("Review the picked photos →"), height=24,
                                           fg_color="transparent", hover=False,
                                           font=(FONT, 12, "underline"),
                                           text_color=P.ACCENT_TEXT,
                                           command=lambda: self.show_page("review"))
        self.btn_go_review.pack(side=side("right"))
        return f

    # ---- page 2: Review photos
    def _build_review(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        self._page_header(page, t("Review photos"),
                          t("Check what the program picked. You can swap any photo with "
                            "another frame of the same moment.") + "\n" +
                          t("Click a photo to see why it was picked. Double-click opens it. "
                            "Arrow keys move.")).grid(
            row=0, column=0, sticky="ew", pady=(0, 8))
        f = make_card(page)
        f.grid(row=1, column=0, sticky="nsew")
        f.columnconfigure(0, weight=0)
        f.columnconfigure(mcol(0, 2), weight=1)
        f.rowconfigure(1, weight=1)
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=mcol(0, 2), sticky="ew", padx=12, pady=(12, 6))
        self.seg = ctk.CTkSegmentedButton(top, values=[t("Selected"), t("Alternates")],
                                          command=self._on_view)
        self.seg.set(t("Selected"))
        self.seg.pack(side=side("left"))
        self.grid_view = ThumbGrid(f, self.loader, self.cache, self._on_select, self._open_item)
        self.grid_view.grid(row=1, column=mcol(0, 2), sticky="nsew", padx=px(10, 4), pady=(0, 12))
        self.detail = self._build_detail(f)
        self.detail.grid(row=0, column=mcol(1, 2), rowspan=2, sticky="ns", padx=px(4, 12),
                         pady=12)
        return page

    def _build_detail(self, master):
        f = make_card(master, P.CARD2, 12, width=330)
        f.grid_propagate(False)
        f.columnconfigure(0, weight=1)
        self.preview = ctk.CTkLabel(f, text="", width=300, height=PREVIEW_H, text_color=P.MUTED)
        self.preview.grid(row=0, column=0, padx=10, pady=(12, 4))
        self.lbl_name = ctk.CTkLabel(f, text=t("Select a photo"), font=(FONT, 13, "bold"),
                                     wraplength=300, justify=justify(), anchor=anchor())
        self.lbl_name.grid(row=1, column=0, sticky="ew", padx=12)
        self.btn_a = ctk.CTkButton(f, text="", height=34, command=lambda: self._act("a"))
        self.btn_b = ctk.CTkButton(f, text="", height=32, command=lambda: self._act("b"),
                                   fg_color=P.NEUTRAL, hover_color=P.NEUTRAL_HOVER,
                                   text_color=P.TEXT, text_color_disabled=P.FAINT)
        self.btn_a.grid(row=2, column=0, sticky="ew", padx=12, pady=(4, 3))
        self.btn_b.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 4))
        self.tip_a, self.tip_b = Tooltip(self.btn_a), Tooltip(self.btn_b)
        self.btn_a.grid_remove()
        self.btn_b.grid_remove()
        self.lbl_info = ctk.CTkLabel(f, text="", font=(FONT, 12), wraplength=300,
                                     text_color=P.MUTED,
                                     justify=justify(), anchor="n" + anchor())
        self.lbl_info.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 6))
        f.rowconfigure(4, weight=1)
        return f

    # ---- page 3: Settings
    def _setting_row(self, body, row: int, label: str, explain: str, key: str | None = None,
                     placeholder: str = "", width: int = 90, var=None, browse: bool = False):
        """label + one-line explanation on the left, the entry on the right."""
        text = ctk.CTkFrame(body, fg_color="transparent")
        text.grid(row=row, column=mcol(0, 2), sticky="ew", pady=5)
        ctk.CTkLabel(text, text=label, font=(FONT, 13, "bold"), anchor=anchor()).pack(fill="x")
        if placeholder and key != "preview":
            explain += "  ·  " + t("Normal value: {v}").format(v=placeholder)
        ctk.CTkLabel(text, text=explain, font=(FONT, 11), anchor=anchor(), justify=justify(),
                     wraplength=520, text_color=P.MUTED).pack(fill="x")
        if var is None:
            var = ctk.StringVar(value=str(self.settings[key]))
            var.trace_add("write", lambda *_: self._on_setting())
            self.adv_vars[key] = var
        ent = ctk.CTkEntry(body, textvariable=var, width=width, justify="center",
                           placeholder_text=placeholder)
        ent.grid(row=row, column=mcol(1, 2), sticky=sticky("e"), padx=px(12, 0))
        if browse:
            ent.configure(justify="left")
            ctk.CTkButton(body, text=t("Browse…"), width=80, height=26,
                          command=lambda v=var: self._browse_into(v)).grid(
                row=row, column=2, padx=(6, 0))
        return ent

    def _build_settings_page(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        self._page_header(page, t("Settings"),
                          t("Most people never need to change these. The normal values work well. "
                            "An empty box means the normal value (written under each setting).")).grid(
            row=0, column=0, sticky="ew", pady=(0, 8))
        scroll = ctk.CTkScrollableFrame(page, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.columnconfigure(0, weight=1)
        self.adv_vars: dict[str, ctk.StringVar] = {}
        d = self.cfg_base

        def section(title):
            card, body = self._section(scroll, title)
            card.grid(row=section.n, column=0, sticky="ew", pady=(0, 10))
            body.columnconfigure(mcol(0, 2), weight=1)
            section.n += 1
            return body
        section.n = 0

        body = section(t("Grouping photos into moments"))
        self.var_gap = ctk.StringVar()
        self._setting_row(body, 0, t("Group gap (s)"),
                          t("Photos taken closer together than this many seconds count as "
                            "one moment."), var=self.var_gap, width=70)
        self.var_gap.trace_add("write", lambda *_: self._on_setting())
        self._setting_row(body, 1, t("Alternates per moment"),
                          t("How many runner-up photos to keep next to each pick."),
                          "keep_alternates", f"{d.grouping.keep_alternates}")
        self.var_skip = ctk.BooleanVar(
            value=d.grouping.skip_blurry_groups if self.settings["skip_blurry_groups"] is None
            else bool(self.settings["skip_blurry_groups"]))
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=5)
        ctk.CTkCheckBox(row, text=t("Skip blurry moments"), variable=self.var_skip,
                        command=self._on_setting, font=(FONT, 13, "bold")).pack(anchor=anchor())
        ctk.CTkLabel(row, text=t("If even the sharpest photo of a moment is blurry, leave the "
                                 "whole moment out."),
                     font=(FONT, 11), text_color=P.MUTED, anchor=anchor(),
                     justify=justify(), wraplength=620).pack(fill="x", padx=(28, 28))

        body = section(t("Spotting weak photos"))
        self._setting_row(body, 0, t("Blur threshold"),
                          t("Photos sharper than this count as sharp. Higher = stricter."),
                          "blurry_sharpness", f"{d.detection.blurry_sharpness:g}")
        self._setting_row(body, 1, t("Closed-eye threshold"),
                          t("Eyes narrower than this count as closed. Higher = stricter."),
                          "ear_closed_threshold", f"{d.detection.ear_closed_threshold:g}")

        body = section(t("Photoshop"))
        self._setting_row(body, 0, t("JPEG quality"),
                          t("Quality of the edited photos saved by Photoshop (1 to 100)."),
                          "jpeg_quality", f"{d.photoshop.jpeg_quality}")
        self._setting_row(body, 1, t("Photoshop Action"),
                          t("Optional: an Action to run after the style, written Set/Action."),
                          "action", "", width=160)
        self._setting_row(body, 2, t("Preview first photos"),
                          t("Edit only this many photos, then pause so you can check the look. "
                            "Empty = off."), "preview", "0")

        body = section(t("Folders"))
        ctk.CTkLabel(body, text=t("Leave these empty to use the normal folders. Change them only "
                                  "to work on photos kept somewhere else."),
                     font=(FONT, 11), text_color=P.MUTED, anchor=anchor(),
                     justify=justify(), wraplength=620).grid(row=0, column=0, columnspan=3,
                                                             sticky="ew")
        body.columnconfigure(1, weight=1)
        body.columnconfigure(mcol(1, 3), weight=1)
        for r, (label, key, ph) in enumerate((
                (t("Cull reads from (empty = Full_Coverage)"), "cull_input", "Full_Coverage"),
                (t("Edit reads from (empty = Selected)"), "edit_input", "Selected"),
                (t("Split reads from (empty = Edited)"), "split_input", "Edited")), start=1):
            ctk.CTkLabel(body, text=label, font=(FONT, 12), anchor=anchor()).grid(
                row=r, column=mcol(0, 3), sticky=sticky("w"), pady=4, padx=px(0, 10))
            var = ctk.StringVar(value=str(self.settings[key]))
            var.trace_add("write", lambda *_: self._on_setting())
            self.adv_vars[key] = var
            ctk.CTkEntry(body, textvariable=var, placeholder_text=ph).grid(
                row=r, column=mcol(1, 3), sticky="ew")
            ctk.CTkButton(body, text=t("Browse…"), width=80, height=26,
                          command=lambda v=var: self._browse_into(v)).grid(
                row=r, column=mcol(2, 3), padx=px(6, 0))

        body = section(t("Tools"))
        bar = ctk.CTkFrame(body, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=2, sticky=sticky("w"))
        for i, (text, cmd) in enumerate(((t("Check this PC"), self._open_doctor),
                                         (t("Open logs folder"), lambda: _open_path(LOGS_DIR)),
                                         (t("Export settings…"), self._export_settings),
                                         (t("Import settings…"), self._import_settings),
                                         (t("Reset advanced values"), self._reset_advanced))):
            (ctk.CTkButton(bar, text=text, width=150, height=30, command=cmd) if i == 0 else
             self._grey_button(bar, text, cmd, 150)).grid(
                row=i // 3, column=mcol(i % 3, 3), padx=px(0, 8), pady=4, sticky=sticky("w"))
        self._fill_gap_var()
        return page

    # ---- page 4: Activity
    def _build_activity(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.columnconfigure(mcol(0, 3), weight=1)
        self._page_header(head, t("Activity"),
                          t("What PhotoCull did, step by step. Warnings and errors are in red.")
                          ).grid(row=0, column=mcol(0, 3), sticky="ew")
        self._grey_button(head, t("Clear"), self._clear_log, 90).grid(
            row=0, column=mcol(1, 3), padx=4)
        self._grey_button(head, t("Open logs folder"), lambda: _open_path(LOGS_DIR), 150).grid(
            row=0, column=mcol(2, 3), padx=4)
        self.log = ctk.CTkTextbox(page, font=("Consolas", 12), wrap="none", state="disabled",
                                  corner_radius=P.RADIUS)
        self.log.grid(row=1, column=0, sticky="nsew")
        self.log._textbox.tag_config("err", foreground="#ff7b72")
        return page

    # ---- updates: banner, page, background check and download
    def _build_banner(self, parent):
        """Slim bar above the pages: only visible when a newer version exists."""
        f = ctk.CTkFrame(parent, corner_radius=12, fg_color=P.ACCENT)
        self.lbl_banner = ctk.CTkLabel(f, text="", font=(FONT, 13, "bold"), text_color="white",
                                       anchor=anchor())
        self.lbl_banner.pack(side=side("left"), padx=14, pady=8)
        ctk.CTkButton(f, text="✕", width=30, height=28, fg_color="transparent", hover=False,
                      text_color="white", font=(FONT, 14, "bold"),
                      command=self._dismiss_update).pack(side=side("right"), padx=(0, 8))
        self.btn_banner_update = ctk.CTkButton(f, text=t("Update now"), width=110, height=28,
                                               fg_color="white", hover_color=("gray90", "gray90"),
                                               text_color=("#4338CA", "#4338CA"),
                                               command=self._start_update)
        self.btn_banner_update.pack(side=side("right"), padx=4)
        ctk.CTkButton(f, text=t("Details"), width=70, height=28, fg_color="transparent",
                      hover=False, text_color="white", font=(FONT, 12, "underline"),
                      command=lambda: self.show_page("updates")).pack(side=side("right"), padx=4)
        return f

    def _build_updates(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)
        self._page_header(page, t("Updates"),
                          t("PhotoCull can update itself. Updating keeps your settings and "
                            "styles.")).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        card, body = self._section(page, t("Version"))
        card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        body.columnconfigure(mcol(0, 2), weight=1)
        ctk.CTkLabel(body, text=t("Installed: {v}").format(v=__version__), font=(FONT, 13),
                     anchor=anchor()).grid(row=0, column=mcol(0, 2), sticky="ew")
        self.lbl_upd_status = ctk.CTkLabel(body, text="", font=(FONT, 14, "bold"),
                                           anchor=anchor(), justify=justify(), wraplength=640)
        self.lbl_upd_status.grid(row=1, column=mcol(0, 2), sticky="ew", pady=(6, 4))
        self.upd_progress = ctk.CTkProgressBar(body)
        self.upd_progress.set(0)
        self.upd_progress.grid(row=2, column=mcol(0, 2), sticky="ew", pady=(2, 6))
        bar = ctk.CTkFrame(body, fg_color="transparent")
        bar.grid(row=3, column=mcol(0, 2), sticky=sticky("w"))
        self.btn_check = ctk.CTkButton(bar, text=t("Check now"), width=130,
                                       command=lambda: self._check_updates(manual=True))
        self.btn_check.grid(row=0, column=mcol(0, 3), padx=px(0, 8))
        self.btn_update = ctk.CTkButton(bar, text=t("Update now"), width=130,
                                        fg_color=P.GO,
                                        hover_color=P.GO_HOVER,
                                        command=self._start_update)
        self.btn_update.grid(row=0, column=mcol(1, 3), padx=px(0, 8))
        self.btn_skip = self._grey_button(bar, t("Skip this version"), self._skip_update, 150)
        self.btn_skip.grid(row=0, column=mcol(2, 3))
        self.var_autoupd = ctk.BooleanVar(value=bool(self.settings["auto_update_check"]))
        ctk.CTkCheckBox(body, text=t("Check for updates automatically once a day"),
                        variable=self.var_autoupd, command=self._on_autoupd).grid(
            row=4, column=mcol(0, 2), sticky=sticky("w"), pady=(10, 0))
        if not updater.can_self_update():
            ctk.CTkLabel(body, text=t("This copy was installed with Git. Update it with "
                                      "“Update PhotoCull.bat” in the program folder."),
                         font=(FONT, 12), text_color=("#8a5a00", "#ffd27a"), anchor=anchor(),
                         justify=justify(), wraplength=640).grid(
                row=5, column=mcol(0, 2), sticky="ew", pady=(8, 0))
        card, body = self._section(page, t("What's new"))
        card.grid(row=2, column=0, sticky="nsew")
        card.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        body.grid_configure(sticky="nsew")
        self.upd_notes = ctk.CTkTextbox(body, font=(FONT, 12), wrap="word", state="disabled",
                                        height=160)
        self.upd_notes.grid(row=0, column=0, sticky="nsew")
        return page

    def _on_autoupd(self) -> None:
        self.settings["auto_update_check"] = bool(self.var_autoupd.get())
        self.settings.save()

    def _set_notes(self, text: str) -> None:
        self.upd_notes.configure(state="normal")
        self.upd_notes.delete("1.0", "end")
        self.upd_notes.insert("1.0", text)
        self.upd_notes.configure(state="disabled")

    def _refresh_update_ui(self) -> None:
        """Redraw banner, sidebar dot and the Updates page from the current state."""
        rel = self._update_rel
        busy = self._upd_state != "idle"
        show_banner = bool(rel and not self._update_dismissed)
        if show_banner:
            self.lbl_banner.configure(text=t("A new version ({v}) is available.").format(v=rel.version))
            self.banner.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        else:
            self.banner.grid_remove()
        self.nav_btns["updates"].configure(text=self._nav_text("updates"))
        can = updater.can_self_update()
        self.btn_banner_update.configure(state="normal" if can and not busy else "disabled")
        self.btn_check.configure(state="disabled" if busy else "normal")
        self.btn_update.configure(state="normal" if rel and can and not busy else "disabled")
        self.btn_skip.configure(state="normal" if rel and not busy else "disabled")
        if rel:
            self._set_notes(rel.notes or t("No details were written for this version."))
        if self._upd_state == "downloading":
            pass                                       # the status line shows the percentage
        elif self._upd_state == "checking":
            self.lbl_upd_status.configure(text=t("Checking for updates…"))
        elif self._upd_error:
            self.lbl_upd_status.configure(
                text=t("Could not check for updates: {msg}").format(msg=t(self._upd_error)))
        elif rel:
            self.lbl_upd_status.configure(text=t("New version {v} is available (you have {cur}).")
                                          .format(v=rel.version, cur=__version__))
        elif self._upd_checked:
            self.lbl_upd_status.configure(text=t("You have the newest version."))
        else:
            self.lbl_upd_status.configure(text=t("Not checked yet."))
        if self._upd_state != "downloading":
            self.upd_progress.grid_remove()

    def _set_update(self, rel) -> None:
        self._update_rel = rel
        self._update_dismissed = False
        self._refresh_update_ui()

    def _dismiss_update(self) -> None:
        self._update_dismissed = True
        self._refresh_update_ui()

    def _skip_update(self) -> None:
        if self._update_rel:
            self.settings["skipped_version"] = self._update_rel.version
            self.settings.save()
        self._update_rel = None
        self._refresh_update_ui()
        self.lbl_upd_status.configure(text=t("This version will not be offered again."))

    def _maybe_auto_check(self) -> None:
        if (self.settings["auto_update_check"] and
                time.time() - float(self.settings["last_update_check"] or 0) > 20 * 3600):
            self._check_updates(manual=False)

    def _check_updates(self, manual: bool) -> None:
        if self._upd_state != "idle":
            return
        self._upd_state, self._upd_error = "checking", ""
        self._refresh_update_ui()

        def work():
            try:
                self._upd_q.put(("checked", updater.check(), "", manual))
            except updater.UpdateError as exc:
                self._upd_q.put(("checked", None, str(exc), manual))
            except Exception as exc:
                self._upd_q.put(("checked", None, f"{type(exc).__name__}: {exc}", manual))

        threading.Thread(target=work, daemon=True, name="update-check").start()

    def _start_update(self) -> None:
        rel = self._update_rel
        if not rel or self._upd_state != "idle" or not updater.can_self_update():
            return
        if self.worker and self.worker.running:
            messagebox.showinfo("PhotoCull", t("Wait for the current step to finish."), parent=self)
            return
        self._upd_state = "downloading"
        self.upd_progress.set(0)
        self.upd_progress.grid()
        self.lbl_upd_status.configure(text=t("Downloading… {pct}%").format(pct=0))
        self._refresh_update_ui()
        self.show_page("updates")

        def work():
            try:
                path = updater.download(
                    rel, lambda got, total: self._upd_q.put(("dl", got, total)))
                self._upd_q.put(("dl_done", path))
            except updater.UpdateError as exc:
                self._upd_q.put(("dl_err", str(exc)))
            except Exception as exc:
                self._upd_q.put(("dl_err", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True, name="update-download").start()

    def _drain_updates(self) -> None:
        while True:
            try:
                ev = self._upd_q.get_nowait()
            except queue.Empty:
                return
            kind = ev[0]
            if kind == "checked":
                _, rel, err, manual = ev
                self._upd_state, self._upd_error = "idle", err
                self._upd_checked = not err
                if not err:
                    self.settings["last_update_check"] = time.time()
                    self.settings.save()
                skipped = self.settings["skipped_version"]
                if rel and (manual or rel.version != skipped):
                    self._set_update(rel)
                else:
                    self._update_rel = None
                    self._refresh_update_ui()
            elif kind == "dl":
                _, got, total = ev
                frac = got / total if total else 0
                self.upd_progress.set(frac)
                self.lbl_upd_status.configure(
                    text=t("Downloading… {pct}%").format(pct=int(frac * 100)))
            elif kind == "dl_err":
                self._upd_state, self._upd_error = "idle", ev[1]
                self._refresh_update_ui()
            elif kind == "dl_done":
                self._upd_state = "idle"
                self._refresh_update_ui()
                self._finish_update(ev[1])

    def _finish_update(self, path) -> None:
        rel = self._update_rel
        ans = ask_choice(
            self, t("Update PhotoCull"),
            t("Version {v} is ready. PhotoCull will close, update (a few minutes, it needs "
              "internet) and open again. Your settings and styles are kept.").format(
                v=rel.version if rel else ""),
            [(t("Update now"), "go")], cancel_label=t("Later"))
        if ans != "go":
            return
        try:
            updater.launch_installer(path)
        except OSError as exc:
            messagebox.showerror("PhotoCull", t("Could not start the update:\n{exc}").format(exc=exc),
                                 parent=self)
            return
        self._destroy()

    # ---- always-visible bottom bar
    def _build_footer(self):
        f = make_card(self)
        f.columnconfigure(0, weight=1)
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 2))
        row.columnconfigure(mcol(0, 2), weight=1)
        self.progress = ctk.CTkProgressBar(row, height=8)
        self.progress.set(0)
        self.progress.grid(row=0, column=mcol(0, 2), sticky="ew", padx=px(0, 10))
        self.btn_stop = ctk.CTkButton(row, text=t("Stop"), width=90, height=30,
                                      fg_color=P.DANGER, hover_color=P.DANGER_HOVER,
                                      command=self._stop, state="disabled")
        self.btn_stop.grid(row=0, column=mcol(1, 2))
        line = ctk.CTkFrame(f, fg_color="transparent")
        line.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        line.columnconfigure(mcol(0, 2), weight=1)
        self.lbl_progress = ctk.CTkLabel(line, text=t("Ready."), anchor=anchor(),
                                         font=(FONT, 12), justify=justify(), wraplength=900)
        self.lbl_progress.grid(row=0, column=mcol(0, 2), sticky="ew")
        ctk.CTkButton(line, text=t("Details ›"), width=70, height=22, fg_color="transparent",
                      hover=False, font=(FONT, 12, "underline"),
                      text_color=P.ACCENT_TEXT,
                      command=lambda: self.show_page("activity")).grid(row=0, column=mcol(1, 2))
        return f

    # ================================================================== settings
    def _load_initial_folder(self) -> None:
        self._refresh_styles()
        saved = self.settings["main_folder"]
        if saved and Path(saved).is_dir():
            self._set_folder(Path(saved), ask_create=False)
        else:
            if saved:
                self.lbl_folder.configure(text=t("The last folder is gone. Choose it again."))
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
        return "" if v == choose_style() else v

    def _fill_gap_var(self) -> None:
        s = self.settings
        key = "gap_action" if s["mode"] == "action" else "gap_portrait"
        g = self.cfg_base.grouping
        default = g.action_group_gap_seconds if s["mode"] == "action" else g.group_gap_seconds
        self._suppress = True
        self.var_gap.set(str(s[key]) if str(s[key]).strip() else f"{default:g}")
        self._suppress = False

    def _on_mode(self, label: str) -> None:
        self.settings["mode"] = next(k for k, v in mode_labels().items() if v == label)
        self._fill_gap_var()
        self.settings.save()

    @staticmethod
    def _clean_scale(value) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 1.3
        return min(TEXT_STEPS, key=lambda s: abs(s - v))

    def _fit_window(self) -> None:
        """Open big enough for the current text size, but never larger than the screen."""
        s = self._text_scale
        dpi = max(1.0, self.winfo_fpixels("1i") / 96)
        w = min(int(1100 * s), int(self.winfo_screenwidth() / dpi * 0.96))
        h = min(int(780 * s), int(self.winfo_screenheight() / dpi * 0.9))
        self.geometry(f"{w}x{h}+10+10")
        self.minsize(min(900, w), min(600, h))

    def _change_text_size(self, delta: int) -> None:
        if self.worker and self.worker.running:
            messagebox.showinfo("PhotoCull", t("Wait for the current step to finish."),
                                parent=self)
            return
        i = TEXT_STEPS.index(self._text_scale) + delta
        if not 0 <= i < len(TEXT_STEPS):
            return
        self._text_scale = TEXT_STEPS[i]
        self.settings["text_scale"] = self._text_scale
        self.settings.save()
        ctk.set_widget_scaling(self._text_scale)
        set_text_scale(self._text_scale)
        self._fit_window()
        self._rebuild_ui()

    def _toggle_language(self) -> None:
        """Switch English <-> Arabic and redraw the whole window in the new language."""
        if self.worker and self.worker.running:
            messagebox.showinfo("PhotoCull", t("Wait for the current step to finish."),
                                parent=self)
            return
        i18n.set_lang("en" if i18n.is_ar() else "ar")
        self.settings["language"] = i18n.LANG
        self.settings.save()
        self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        """Destroy and redraw every widget (new language or text size); keeps page and log."""
        log_text = self.log._textbox.get("1.0", "end-1c")
        for job in (self._detail_job, self._refresh_job):
            if job:
                self.after_cancel(job)
        self._detail_job = self._refresh_job = None
        self._detail_item = None
        self._detail_token += 1
        for child in self.winfo_children():
            child.destroy()
        self._build()
        self._refresh_styles()
        self.lbl_folder.configure(text=self._folder_text(self.layout) if self.layout
                                  else t("No folder chosen"))
        self._set_ps(*self.ps_state)
        if log_text.strip():
            self._append_log(log_text)
        self._refresh_all()
        self._refresh_update_ui()

    def _toggle_theme(self) -> None:
        dark = bool(self.theme_switch.get())
        self.settings["appearance"] = "dark" if dark else "light"
        ctk.set_appearance_mode("dark" if dark else "light")
        self.settings.save()
        self.grid_view.refresh_colors()

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
        d = filedialog.askdirectory(parent=self, title=t("Choose a folder"),
                                    initialdir=str(self.layout.main) if self.layout else None)
        if d:
            var.set(str(Path(d)))

    def _refresh_styles(self) -> None:
        names = [p.name for p in list_styles(self.cfg_base)]
        want = self._style_value() or self.settings["style"] or self.cfg_base.photoshop.default_style_file
        if names:
            self.style_menu.configure(values=[choose_style()] + names)
            self.style_menu.set(want if want in names else (names[0] if len(names) == 1
                                                           else choose_style()))
        else:
            self.style_menu.configure(values=[choose_style()])
            self.style_menu.set(choose_style())
        self.settings["style"] = self._style_value()

    def _open_styles(self) -> None:
        d = styles_dir(self.cfg_base)
        d.mkdir(exist_ok=True)
        _open_path(d)

    # ================================================================== folder
    def _choose_folder(self) -> None:
        d = filedialog.askdirectory(parent=self, title=t("Choose the main folder of this coverage"),
                                    initialdir=self.settings["main_folder"] or None)
        if d:
            self._set_folder(Path(d), ask_create=True)

    def _set_folder(self, main: Path, ask_create: bool) -> None:
        if self.worker and self.worker.running:
            return
        layout = CoverageLayout.for_main(main)
        if ask_create and layout.source is None and not layout.full_coverage.is_dir():
            if list_jpegs(layout.main):
                # Any folder with photos works: results go to a new folder beside it.
                layout = CoverageLayout.for_photos_folder(layout.main)
                existing = CoverageLayout.for_main(layout.main)
                if existing.source is not None:
                    layout = existing
                try:
                    layout.remember_source()
                except OSError as exc:
                    messagebox.showerror("PhotoCull", t("Could not create the folder:\n{exc}").format(
                        exc=exc), parent=self)
                    return
            elif messagebox.askyesno(
                    "PhotoCull", t("This folder has no photos yet.\n\nCreate a Full_Coverage "
                                   "folder in it now? Then copy the photos of the coverage "
                                   "into it."),
                    parent=self):
                try:
                    layout.full_coverage.mkdir()
                except OSError as exc:
                    messagebox.showerror("PhotoCull", t("Could not create the folder:\n{exc}").format(exc=exc))
        self.layout = layout
        self.settings["main_folder"] = str(layout.main)
        self.settings.save()
        self.transient.clear()
        try:
            set_coverage_log(layout.log)
        except OSError:
            pass
        self.lbl_folder.configure(text=self._folder_text(layout))
        self._clear_log()
        self._refresh_all()

    @staticmethod
    def _folder_text(layout: CoverageLayout) -> str:
        if layout.source is None:
            return str(layout.main)
        return t("Photos: {src}\nResults go to: {main}").format(src=layout.source,
                                                                  main=layout.main)

    def _open_doctor(self) -> None:
        from .doctor_dialog import DoctorDialog
        DoctorDialog(self, self.cfg_base, self.settings["main_folder"] or None)

    def _export_settings(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title=t("Save my settings"), defaultextension=".json",
            initialfile="PhotoCull settings.json", filetypes=[("Settings", "*.json")])
        if not path:
            return
        try:
            if self.settings.path.is_file():
                shutil.copyfile(self.settings.path, path)
            else:
                Path(path).write_text("{}", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("PhotoCull", t("Could not save the settings:\n{exc}").format(exc=exc),
                                 parent=self)

    def _import_settings(self) -> None:
        if self.worker and self.worker.running:
            return
        path = filedialog.askopenfilename(parent=self, title=t("Load settings from a file"),
                                          filetypes=[("Settings", "*.json")])
        if not path:
            return
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("not a PhotoCull settings file")
        except (OSError, ValueError) as exc:
            messagebox.showerror("PhotoCull", t("This is not a usable settings file:\n{exc}").format(exc=exc),
                                 parent=self)
            return
        for k, v in loaded.items():
            if k in DEFAULTS and k not in MACHINE_KEYS:
                self.settings[k] = v
        self.settings.save()
        messagebox.showinfo("PhotoCull", t("Settings loaded. Close and open PhotoCull again "
                                           "to see them all."), parent=self)

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
            parts = [f"{t('blurry')} {by.get('blurry', 0)}",
                     f"{t('eyes')} {by.get('closed_eyes', 0)}", f"{t('exposure')} {expo}"]
            self.card_rejected.set(rej, " · ".join(p for p in parts if not p.endswith(" 0")) or " ")
        else:
            self.card_rejected.set("–")
        try:
            mpp = max_per_post(self.settings, self.cfg_base)
            sel = n["selected"]
            if sel == 0:
                self.lbl_predict.configure(
                    text=t("Run step 1 to see how many posts you will get."))
            else:
                sizes = distribute(sel, mpp)
                tpl = ("{sel} selected  →  {n} post  →  {sizes}" if len(sizes) == 1
                       else "{sel} selected  →  {n} posts  →  {sizes}")
                self.lbl_predict.configure(text=t(tpl).format(
                    sel=sel, n=len(sizes), sizes="/".join(map(str, sizes))))
        except SettingsError:
            self.lbl_predict.configure(text=t("Type a whole number for “Max images per post”."))

    def _refresh_cards(self) -> None:
        status = {}
        if self.layout and self.layout.state.is_file():
            steps = peek(self.layout.state)["steps"]
            status = {s: steps[s].get("status", "not_started") for s in STEPS}
        for step in STEPS:
            text, kind = self.transient.get(step) or STATE_TEXT.get(
                status.get(step, "not_started"), ("Not started", "idle"))
            self.cards[step].set_status(t(text), kind)
        lay = self.layout
        for step, folder in (("cull", lay.selected if lay else None),
                             ("edit", lay.edited if lay else None),
                             ("split", lay.posts if lay else None)):
            ok = bool(folder and folder.is_dir())
            self.cards[step].set_folder_available(ok, t("Not created yet: run this step first."))

    def _update_buttons(self) -> None:
        running = bool(self.worker and self.worker.running)
        n = getattr(self, "_n", dict(full=0, selected=0, edit_in=0, split_in=0))
        lay = self.layout
        ps = self.ps_state[0]
        busy = t("Wait for the current step to finish, or press Stop.")
        no_folder = t("Choose a main folder first.")

        def cull_why():
            if running: return busy
            if not lay: return no_folder
            if n["full"] == 0:
                return (t("No photos found. Choose a folder that has photos in it.")
                        if not str(self.settings["cull_input"]).strip()
                        else t("No photos in the folder chosen under Settings → Folders → Cull reads from."))
            return ""

        def edit_why():
            if running: return busy
            if not lay: return no_folder
            if not self._style_value():
                return t("Choose a style first (a .psd file in the Styles folder).")
            if ps == "missing": return t("Photoshop is not installed on this PC.")
            if ps == "no_pywin32":
                return t("The Photoshop link is missing. Run First-Time Setup again.")
            if n["edit_in"] == 0: return t("Nothing to edit yet. Run step 1 first.")
            return ""

        def split_why():
            if running: return busy
            if not lay: return no_folder
            # No edited photos yet? The picked photos can be split as they are (the window
            # asks first), so only "nothing picked either" blocks the button.
            if n["split_in"] == 0 and not (n["selected"] and not self._split_override()):
                return t("Nothing to split yet. Run step 1 (or step 2) first.")
            return ""

        def all_why():
            # Run All starts at step 1, so it only needs what step 1 needs.
            return cull_why()

        for step, why in (("cull", cull_why()), ("edit", edit_why()), ("split", split_why())):
            self.cards[step].set_enabled(not why, why)
        why = all_why()
        if not why and not self._style_value():
            why = t("Choose a style first (a .psd file in the Styles folder).")
        # Run All starts from step 1, so only step 1's needs and Photoshop/style must hold.
        if not why and ps in ("missing", "no_pywin32"):
            why = t("Photoshop is not ready, so step 2 cannot run.")
        if not why and running:
            why = busy
        self.btn_all.configure(state="disabled" if why else "normal")
        self.tip_all.text = why
        self.btn_folder.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        if running:
            self.btn_stop.grid()
        else:
            self.btn_stop.grid_remove()                  # nothing to stop: keep the bar calm
        self._sync_detail_buttons()

    # ================================================================== review grid
    def _seg_labels(self) -> None:
        rv = self.review
        sel = t("Selected ({n})").format(n=len(rv.selected))
        alt = t("Alternates ({n})").format(n=len(rv.alternates))
        self.seg.configure(values=[sel, alt])
        self.seg.set(sel if self.view == "selected" else alt)
        self.nav_btns["review"].configure(text=self._nav_text("review"))

    def _on_view(self, label: str) -> None:
        self.view = "selected" if label.startswith(t("Selected")) else "alternates"
        self._fill_grid(keep=False)

    def _fill_grid(self, keep: bool) -> None:
        self._seg_labels()
        items = self.review.selected if self.view == "selected" else self.review.alternates
        gi = []
        for it in items:
            badge, kind = "", "warn"
            if USER_PICK in it.row.get("reasons", ""):
                badge, kind = t("your pick"), "done"
            elif it.row.get("weak_group") == "yes":
                badge = t("check me")
            gi.append(GridItem(str(it.path), it.path, it.path.name, badge, kind))
        self.grid_view.empty_text = (
            t("No photos here yet. Run step 1 (Cull).") if self.view == "selected"
            else t("No alternates (they appear after Cull)."))
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
            self.lbl_name.configure(text=t("Select a photo"))
            self.lbl_info.configure(text="")
            self._sync_detail_buttons()
            return
        r = it.row
        self.lbl_name.configure(text=it.path.name)
        lines = []
        if r:
            lines.append(t("Moment {g}: {size} photo(s), this one ranks #{rank}").format(
                g=r.get("group_id", "?"), size=r.get("group_size", "?"),
                rank=r.get("rank_in_group", "?")))
            lines.append(t("Sharpness {sharp}   Brightness {bright}   Score {score}").format(
                sharp=_num(r.get("sharpness")), bright=_num(r.get("mean_brightness")),
                score=_num(r.get("total_score"), 2)))
            lines.append(t("Face: {face}   Eyes: {eyes}").format(
                face=t(str(r.get("face_found", "?"))), eyes=t(str(r.get("eyes_state") or "?"))))
            why = [t(REASON_TEXT.get(code, code)) for code in r.get("reasons", "").split(";")
                   if code]
            if why:
                lines.append(t("Notes: ") + "; ".join(why))
            if r.get("weak_group") == "yes":
                lines.append(t("The program was not sure about this moment. Please check it."))
        else:
            lines.append(t("No score: this photo was not made by the Cull step."))
        self.lbl_info.configure(text="\n".join(lines))
        self.preview.configure(image=None, text=t("loading…"))
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
            self.preview.configure(image=None, text=t("(cannot show this photo)"))
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
            wait = t("Wait for the current step to finish.")
            self.btn_a.configure(text=t("Swap with another frame of this moment"))
            self.btn_b.configure(text=t("Remove from Selected"))
            a_why = wait if running else ("" if alts else t("This moment has no alternates."))
            b_why = wait if running else ""
        else:
            sel = self.review.selected_of(it)
            wait = t("Wait for the current step to finish.")
            self.btn_a.configure(text=t("Use this instead of the pick") if sel
                                 else t("Add to Selected"))
            self.btn_b.configure(text=t("Also add to Selected"))
            a_why = wait if running else ""
            b_why = wait if running else (
                "" if sel else t("It is the only one, use the button above."))
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
            messagebox.showerror("PhotoCull", t("Could not change the pick:\n{exc}").format(exc=exc))
            self._refresh_all()
            return
        stale = stale_edits(lay, demoted)
        if stale:
            ans = ask_choice(
                self, t("Edited photo"),
                t("{names} was already edited, but is no longer picked. Delete the edited copy "
                  "so it does not end up in your posts?\n(Photos in Full_Coverage and "
                  "Alternates are not touched.)").format(
                    names=", ".join(Path(s).name for s in stale)),
                [(t("Delete the edited copy"), "delete")], cancel_label=t("Keep it"))
            if ans == "delete":
                drop_edits(lay, demoted)
        self._append_log(t("Pick changed. Run step 2 again to edit the new photo."))
        self._detail_item = None
        self._refresh_all()
        self.grid_view.select(None)

    def _choose_alternate(self, alts: list[PickItem]) -> PickItem | None:
        picked: dict = {"item": None}
        dlg = ctk.CTkToplevel(self)
        dlg.title(t("Choose the photo to use"))
        dlg.transient(self)
        ctk.CTkLabel(dlg, text=t("Click the photo you want, then press Use this photo."),
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

        ctk.CTkButton(dlg, text=t("Use this photo"), command=ok).pack(pady=(4, 14))
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
                raise SettingsError(t("Photoshop Action: {exc}").format(exc=exc)) from None
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

    def _split_override(self) -> bool:
        return bool(str(self.settings["split_input"]).strip())

    def _start(self, kind: str) -> None:
        if self.worker and self.worker.running or not self.layout:
            return
        try:
            cfg, params = self._collect(kind)
        except SettingsError as exc:
            messagebox.showwarning("PhotoCull", str(exc), parent=self)
            return
        lay = self.layout
        if (kind == "split" and not params.split_input and self._n["split_in"] == 0
                and self._n["selected"] > 0):
            ans = ask_choice(
                self, t(STEP_TITLES["split"]),
                t("There are no edited photos yet (step 2).\n\nSplit the {n} picked photos as "
                  "they are, without Photoshop editing?").format(n=self._n["selected"]),
                [(t("Split the picked photos"), "go")])
            if ans != "go":
                return
            params = replace(params, split_input=str(lay.selected))
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
        self.lbl_progress.configure(text=self._starting_text(kind), text_color=P.TEXT)
        self._append_log(f"--- {t('Run All') if kind == 'all' else t(STEP_TITLES[kind])} ---")
        self._refresh_cards()
        self._update_buttons()
        self.worker.start()

    @staticmethod
    def _starting_text(kind: str) -> str:
        if kind in ("edit", "all"):
            return t("Starting… (Photoshop can take up to 30 seconds to open)")
        return t("Starting…")

    def _stop(self) -> None:
        if self.worker and self.worker.running:
            self.worker.cancel.set()
            self.btn_stop.configure(state="disabled")
            self.lbl_progress.configure(text=t("Stopping after the current photo…"))

    def _poll(self) -> None:
        if self._destroyed:
            return
        try:
            self._drain()
        except Exception:
            logging.getLogger("photocull").exception("UI poll failed")
        self.after(80, self._poll)

    def _drain(self) -> None:
        self._drain_updates()
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
        w.window_active = self._window_in_front()
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
        step = t("Step {i}/3 · {title}  —  ").format(
            i=STEPS.index(self.cur_step) + 1, title=t(STEP_TITLES[self.cur_step])) \
            if self.run_kind == "all" and self.cur_step else ""
        text = f"{step}{min(done + (1 if name else 0), total)} / {total}" if total else step + "…"
        self.lbl_progress.configure(text=f"{text}   {name}".rstrip(),
                                    text_color=P.TEXT)

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
        msg = tr_msg(res.message) or (t("Done.") if res.ok else t("Something went wrong."))
        color = ("#1b6b2f", "#8fe3a5") if res.ok else (
            ("#8a5a00", "#ffd27a") if res.cancelled or res.data.get("preview_done")
            or res.data.get("declined") else ("#9b1c1c", "#ff9b9b"))
        self.lbl_progress.configure(text=msg, text_color=color)
        soft = res.cancelled or res.data.get("preview_done") or res.data.get("declined")
        self._append_log(("✓ " if res.ok else "• " if soft else "✗ ") + msg)
        for err in res.errors[:5]:
            self._append_log("  " + err, error=True)
        if len(res.errors) > 5:
            self._append_log(t("  …and {n} more (see the log).").format(n=len(res.errors) - 5),
                             error=True)
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
            msg = t("Selected and Alternates already hold {n} photo(s) from an earlier "
                    "run.\n\nReplace them with the new picks? (The photos in Full_Coverage "
                    "are never touched.)").format(n=n)
            opts = [(t("Replace them"), "clear")]
        elif step == "edit":
            msg = t("{n} photo(s) were already edited with different settings (style, "
                    "action or exposure).\n\nEdit them again with the new settings? The old "
                    "edited copies will be replaced.").format(n=n)
            opts = [(t("Edit them again"), "reprocess")]
        else:
            msg = t("Posts already has {n} post folder(s).\n\nDelete the old Post_N folders "
                    "and start again, or add new posts after them?").format(n=n)
            opts = [(t("Delete the old posts"), "clear"), (t("Add new posts"), "new")]
        if "keep" in choices:
            opts.append((t("Keep them and continue"), "keep"))
        return ask_choice(self, t(STEP_TITLES[step]), msg, opts)

    def _preview_dialog(self, res: StepResult) -> bool:
        while True:
            ans = ask_choice(
                self, t("Preview ready"),
                t("{message}\n\nOpen the Edited folder and check how the photos look. "
                  "Continue with the rest?").format(message=tr_msg(res.message)),
                [(t("Open Edited folder"), "open"), (t("Continue"), "go")],
                cancel_label=t("Stop here"))
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
        text = t(text)
        if state == "open" and detail:
            text = t("Photoshop {v} is ready").format(v=detail)
        self.ps_dot.configure(text_color=color)
        self.ps_text.configure(text=text)
        self._update_buttons()

    # ================================================================== misc
    def _window_in_front(self) -> bool:
        """True while the user is looking at this window (not minimised, not in another app)."""
        try:
            return self.state() != "iconic" and self.focus_displayof() is not None
        except Exception:
            return True

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
            self._append_log(t("Something went wrong in the window: {err}. "
                               "Details are in the logs folder.").format(err=val), error=True)
        except Exception:
            pass

    def _on_close(self) -> None:
        if self.worker and self.worker.running:
            if not messagebox.askyesno("PhotoCull", t("A step is still running.\n\n"
                                                      "Stop it and close the window?"),
                                       parent=self):
                return
            self._closing = True
            self.worker.cancel.set()
            self.lbl_progress.configure(
                text=t("Stopping after the current photo, then closing…"))
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
    from ..runner import disable_power_throttling
    disable_power_throttling()
    setup_logging()
    app = App(cfg or load_config())
    app.mainloop()
    return 0
