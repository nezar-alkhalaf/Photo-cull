"""Reusable widgets: tooltip, stat card, step card, lazy thumbnail grid, choice dialog."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import ImageTk

from .i18n import anchor, is_ar, justify, mcol, px, side, sticky, t
from .thumbs import LRU, ThumbLoader, file_key

FONT = "Segoe UI"
TEXT_SCALE = 1.0          # the window's text-size setting; the canvas and tooltips follow it


def set_text_scale(scale: float) -> None:
    global TEXT_SCALE
    TEXT_SCALE = scale


def pt(size: int) -> int:
    """A point size for tk-native text (canvas, tooltip) that follows the text-size setting."""
    return max(1, round(size * TEXT_SCALE))

# badge colour per status kind: (text colour, fill colour) for light / dark mode
BADGE_COLORS = {
    "idle": (("#555555", "#e4e4e4"), ("#bdbdbd", "#3a3a3a")),
    "running": (("#0b4f8a", "#d6e9fb"), ("#9ccbff", "#173a5c")),
    "done": (("#1b6b2f", "#d8f1de"), ("#8fe3a5", "#17421f")),
    "warn": (("#8a5a00", "#fdecc8"), ("#ffd27a", "#4d3a0a")),
    "error": (("#9b1c1c", "#fbdada"), ("#ff9b9b", "#5a1c1c")),
}


def mode_index() -> int:
    return 1 if ctk.get_appearance_mode() == "Dark" else 0


def pick(pair) -> str:
    """Colour for the current light/dark mode from a CTk (light, dark) pair."""
    return pair if isinstance(pair, str) else pair[mode_index()]


def theme(widget: str, key: str) -> str:
    return pick(ctk.ThemeManager.theme[widget][key])


class Tooltip:
    """Hover text. `text` may be changed later; empty text shows nothing."""

    def __init__(self, widget, text: str = "", delay: int = 450):
        self.widget, self.text, self.delay = widget, text, delay
        self._tip: tk.Toplevel | None = None
        self._job = None
        for ev, fn in (("<Enter>", self._enter), ("<Leave>", self._leave),
                       ("<ButtonPress>", self._leave)):
            widget.bind(ev, fn, add="+")

    def _enter(self, _e=None) -> None:
        self._cancel()
        if self.text:
            self._job = self.widget.after(self.delay, self._show)

    def _leave(self, _e=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _cancel(self) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None

    def _show(self) -> None:
        self._job = None
        if not self.text or self._tip is not None:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        x = self.widget.winfo_pointerx() + 12
        y = self.widget.winfo_pointery() + 18
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(tip, text=self.text, justify=justify(), wraplength=int(320 * TEXT_SCALE), padx=8, pady=5,
                 bg="#2b2b2b", fg="#f2f2f2", relief="solid", bd=1,
                 font=(FONT, pt(10))).pack()
        self._tip = tip


class StatCard(ctk.CTkFrame):
    """A number with a caption and an optional small second line."""

    def __init__(self, master, title: str):
        super().__init__(master, corner_radius=10)
        self.value = ctk.CTkLabel(self, text="–", font=(FONT, 22, "bold"), height=28)
        self.value.pack(padx=14, pady=(4, 0))
        ctk.CTkLabel(self, text=title, font=(FONT, 12), height=16).pack(padx=14)
        self.sub = ctk.CTkLabel(self, text=" ", font=(FONT, 10), height=14,
                                text_color=("gray35", "gray65"))
        self.sub.pack(padx=14, pady=(0, 4))

    def set(self, value, sub: str = " ") -> None:
        self.value.configure(text=str(value))
        self.sub.configure(text=sub or " ")


class StepCard(ctk.CTkFrame):
    """'1. Cull' etc: one big button, a one-line description, a status badge, Open folder."""

    def __init__(self, master, title: str, button_text: str, description: str,
                 on_run: Callable[[], None], on_open: Callable[[], None]):
        super().__init__(master, corner_radius=12)
        self.columnconfigure(0, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(6, 0))
        top.columnconfigure(mcol(0, 2), weight=1)
        ctk.CTkLabel(top, text=title, font=(FONT, 15, "bold"), anchor=anchor()).grid(
            row=0, column=mcol(0, 2), sticky=sticky("w"))
        self.badge = ctk.CTkLabel(top, text=t("Not started"), font=(FONT, 11, "bold"),
                                  corner_radius=8, width=96, height=24)
        self.badge.grid(row=0, column=mcol(1, 2), sticky=sticky("e"))
        self.button = ctk.CTkButton(self, text=button_text, height=36, font=(FONT, 14, "bold"),
                                    command=on_run)
        self.button.grid(row=1, column=0, sticky="ew", padx=14, pady=(6, 2))
        self.desc = ctk.CTkLabel(self, text=description, font=(FONT, 11), anchor=anchor(),
                                 justify=justify(), wraplength=250,
                                 text_color=("gray30", "gray70"))
        self.desc.grid(row=2, column=0, sticky="ew", padx=14)
        self.link = ctk.CTkButton(self, text=t("Open folder"), width=90, height=22,
                                  font=(FONT, 11, "underline"), fg_color="transparent",
                                  hover=False, text_color=("#1a62b8", "#7ab7ff"),
                                  command=on_open)
        self.link.grid(row=3, column=0, sticky=sticky("w"), padx=10, pady=(0, 4))
        self.tip = Tooltip(self.button)
        self.link_tip = Tooltip(self.link)
        self.set_status(t("Not started"), "idle")

    def set_status(self, text: str, kind: str) -> None:
        light, dark = BADGE_COLORS[kind]
        self.badge.configure(text=text, text_color=(light[0], dark[0]),
                             fg_color=(light[1], dark[1]))

    def set_enabled(self, enabled: bool, why: str = "") -> None:
        self.button.configure(state="normal" if enabled else "disabled")
        self.tip.text = "" if enabled else why

    def set_folder_available(self, available: bool, why: str = "") -> None:
        self.link.configure(state="normal" if available else "disabled")
        self.link_tip.text = "" if available else why


# ------------------------------------------------------------------ thumbnail grid
@dataclass
class GridItem:
    key: str
    path: Path
    caption: str
    badge: str = ""              # short flag drawn on the thumbnail, e.g. "weak"
    badge_kind: str = "warn"


class ThumbGrid(ctk.CTkFrame):
    """Scrollable grid of thumbnails on ONE canvas: only visible cells are drawn and decoded."""

    BASE_W, BASE_H, BASE_THUMB = 176, 206, 160

    def __init__(self, master, loader: ThumbLoader, cache: LRU,
                 on_select: Callable[[GridItem | None], None],
                 on_open: Callable[[GridItem], None] | None = None):
        super().__init__(master, fg_color="transparent")
        self.loader, self.cache = loader, cache
        self.on_select, self.on_open = on_select, on_open
        self.items: list[GridItem] = []
        self.selected_key: str | None = None
        self._pending: dict[tuple, object] = {}
        self._paint_job = None
        self._poll_job = None
        self.columnconfigure(0, weight=0)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.columnconfigure(mcol(0, 2), weight=1)
        self.columnconfigure(mcol(1, 2), weight=0)
        self.canvas.grid(row=0, column=mcol(0, 2), sticky="nsew")
        self.scroll = ctk.CTkScrollbar(self, command=self.canvas.yview)
        self.scroll.grid(row=0, column=mcol(1, 2), sticky="ns")
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.canvas.bind("<Configure>", lambda e: self._relayout())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Double-Button-1>", self._double)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self.empty_text = t("Nothing here yet.")
        self._scale = 1.0
        self._cols = 1

    # ---- public
    def set_items(self, items: list[GridItem], keep_selection: bool = True) -> None:
        self.items = items
        keys = {i.key for i in items}
        if not (keep_selection and self.selected_key in keys):
            self.selected_key = None
            self.on_select(None)
        self._relayout()

    def selected(self) -> GridItem | None:
        return next((i for i in self.items if i.key == self.selected_key), None)

    def select(self, key: str | None) -> None:
        self.selected_key = key
        self.on_select(self.selected())
        self._schedule_paint()

    def refresh_colors(self) -> None:
        self._schedule_paint()

    def destroy(self) -> None:
        for job in (self._paint_job, self._poll_job):
            if job:
                self.after_cancel(job)
        super().destroy()

    # ---- layout
    def _metrics(self):
        self._scale = max(1.0, self.winfo_fpixels("1i") / 96) * TEXT_SCALE
        s = self._scale
        return int(self.BASE_W * s), int(self.BASE_H * s), int(self.BASE_THUMB * s)

    def _relayout(self, reset_scroll: bool = False) -> None:
        cw, ch, _ = self._metrics()
        width = max(self.canvas.winfo_width(), cw)
        self._cols = max(1, width // cw)
        rows = -(-len(self.items) // self._cols)
        self.canvas.configure(scrollregion=(0, 0, width, max(rows * ch, 1)))
        if reset_scroll:
            self.canvas.yview_moveto(0)
        self.refresh_colors()

    def _width(self) -> int:
        """Width of the drawing area (the right edge is where Arabic cells start)."""
        return max(self.canvas.winfo_width(), self._metrics()[0])

    def _on_yscroll(self, lo, hi) -> None:
        self.scroll.set(lo, hi)
        self._schedule_paint()

    def _wheel(self, e) -> None:
        self.canvas.yview_scroll(int(-e.delta / 120) * 2, "units")

    def _schedule_paint(self) -> None:
        if self._paint_job is None:
            self._paint_job = self.after(15, self._paint)

    # ---- drawing
    def _paint(self) -> None:
        self._paint_job = None
        c = self.canvas
        c.delete("all")
        cw, ch, th = self._metrics()
        card = theme("CTkFrame", "fg_color")
        bg = pick(("gray90", "gray13"))
        c.configure(bg=bg)
        if not self.items:
            c.create_text(c.winfo_width() / 2, 60, text=self.empty_text,
                          fill=pick(("gray40", "gray60")), font=(FONT, pt(12)))
            return
        top = c.canvasy(0)
        first_row = max(0, int(top // ch))
        last_row = int((top + c.winfo_height()) // ch)
        visible_keys = set()
        for row in range(first_row, last_row + 1):
            for col in range(self._cols):
                i = row * self._cols + col
                if i >= len(self.items):
                    break
                x = (self._width() - (col + 1) * cw) if is_ar() else col * cw
                visible_keys.add(self._paint_cell(i, x, row * ch, cw, ch, th, card))
        for key in [k for k in self._pending if k not in visible_keys]:
            self._pending.pop(key).cancel()                  # scrolled away before it started
        if self._pending and self._poll_job is None:
            self._poll_job = self.after(40, self._poll)

    def _paint_cell(self, i: int, x: int, y: int, cw: int, ch: int, th: int, card: str):
        c, item, s = self.canvas, self.items[i], self._scale
        pad = int(4 * s)
        sel = item.key == self.selected_key
        c.create_rectangle(x + pad, y + pad, x + cw - pad, y + ch - pad, fill=card,
                           outline=pick(("#1a62b8", "#4aa3ff")) if sel else card,
                           width=3 if sel else 1)
        cx, cy = x + cw // 2, y + pad + int(6 * s) + th // 2
        key = file_key(item.path, th)
        photo = self.cache.get(key)
        if photo is not None:
            c.create_image(cx, cy, image=photo)
        else:
            c.create_text(cx, cy, text="…", fill=pick(("gray50", "gray55")), font=(FONT, pt(20)))
            if key not in self._pending:
                self._pending[key] = self.loader.submit(item.path, th)
        c.create_text(cx, y + ch - int(22 * s), text=_shorten(item.caption, 24),
                      fill=pick(("gray15", "gray85")), font=(FONT, pt(9)))
        if item.badge:
            light, dark = BADGE_COLORS[item.badge_kind]
            col = (light, dark)[mode_index()]
            c.create_rectangle(x + pad + 8, y + pad + 8, x + pad + 8 + int(7 * s * len(item.badge)) + 10,
                               y + pad + 8 + int(18 * s), fill=col[1], outline=col[0])
            c.create_text(x + pad + 13, y + pad + 8 + int(9 * s), text=item.badge, anchor="w",
                          fill=col[0], font=(FONT, pt(9), "bold"))
        return key

    def _poll(self) -> None:
        self._poll_job = None
        changed = False
        for key, fut in list(self._pending.items()):
            if fut.done():
                del self._pending[key]
                if fut.cancelled() or fut.exception() is not None:
                    self.cache.put(key, _broken_photo())     # unreadable: show a blank tile
                else:
                    self.cache.put(key, ImageTk.PhotoImage(fut.result()))
                changed = True
        if changed:
            self._schedule_paint()
        elif self._pending:
            self._poll_job = self.after(40, self._poll)

    # ---- mouse
    def _index_at(self, e) -> int | None:
        cw, ch, _ = self._metrics()
        cx = self.canvas.canvasx(e.x)
        col = int((self._width() - cx) // cw) if is_ar() else int(cx // cw)
        row = int(self.canvas.canvasy(e.y) // ch)
        if col >= self._cols or col < 0 or row < 0:
            return None
        i = row * self._cols + col
        return i if i < len(self.items) else None

    def _click(self, e) -> None:
        i = self._index_at(e)
        if i is not None:
            self.select(self.items[i].key)

    def _double(self, e) -> None:
        i = self._index_at(e)
        if i is not None and self.on_open:
            self.on_open(self.items[i])


_BROKEN = None


def _broken_photo():
    global _BROKEN
    if _BROKEN is None:
        from PIL import Image
        _BROKEN = ImageTk.PhotoImage(Image.new("RGB", (8, 8), (90, 90, 90)))
    return _BROKEN


def _shorten(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 9] + "…" + text[-8:]


# ------------------------------------------------------------------ dialogs
def ask_choice(parent, title: str, message: str, options: list[tuple[str, str]],
               cancel_label: str | None = None) -> str | None:
    """Modal dialog with one button per option; returns the option's value or None."""
    dlg = ctk.CTkToplevel(parent)
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.transient(parent)
    result: dict = {"value": None}
    ctk.CTkLabel(dlg, text=message, font=(FONT, 13), justify=justify(), wraplength=440).pack(
        padx=22, pady=(20, 14))
    row = ctk.CTkFrame(dlg, fg_color="transparent")
    row.pack(padx=18, pady=(0, 18))

    def choose(v):
        result["value"] = v
        dlg.destroy()

    for label, value in options:
        ctk.CTkButton(row, text=label, command=lambda v=value: choose(v), height=34).pack(
            side=side("left"), padx=5)
    ctk.CTkButton(row, text=cancel_label or t("Cancel"), command=lambda: choose(None), height=34,
                  fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"),
                  text_color=("gray10", "gray95")).pack(side=side("left"), padx=5)
    dlg.protocol("WM_DELETE_WINDOW", lambda: choose(None))
    dlg.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - dlg.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - dlg.winfo_reqheight()) // 3
    dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    dlg.after(120, lambda: (dlg.winfo_exists() and (dlg.grab_set(), dlg.focus_force())))
    parent.wait_window(dlg)
    return result["value"]
