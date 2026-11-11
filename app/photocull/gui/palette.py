"""The look of the window: one palette, (light, dark) pairs, used by every widget.

apply() rewrites customtkinter's theme so plain widgets get these colours without
each one having to ask. Call it before the first widget is made.
"""

from __future__ import annotations

import customtkinter as ctk

BG = ("#F3F4F8", "#0E1014")              # the window behind the cards
SIDEBAR = ("#FFFFFF", "#14171D")
CARD = ("#FFFFFF", "#181C23")
CARD2 = ("#F6F7FA", "#1F242D")           # a tile inside a card
BORDER = ("#E2E5EC", "#2A303B")
TEXT = ("#151922", "#EEF0F4")
MUTED = ("#667085", "#98A2B3")
ACCENT = ("#4F46E5", "#6366F1")
ACCENT_HOVER = ("#4338CA", "#7C83F7")
ACCENT_SOFT = ("#EEF0FF", "#252A52")     # active page, number chips, quiet buttons
ACCENT_SOFT_HOVER = ("#DFE3FF", "#30366A")
ACCENT_TEXT = ("#4338CA", "#A5B4FC")     # links and text on ACCENT_SOFT
GO = ("#12A150", "#16A34A")              # the one "start" button
GO_HOVER = ("#0E8A43", "#22B95C")
DANGER = ("#DC2626", "#DC2626")
DANGER_HOVER = ("#B91C1C", "#EF4444")
BAD_TEXT = ("#B42318", "#F97066")
NEUTRAL = ("#E9EBF0", "#2A303B")         # secondary buttons
NEUTRAL_HOVER = ("#DCDFE6", "#363D4A")
INPUT = ("#FFFFFF", "#12151A")
INPUT_BORDER = ("#D5D9E2", "#3A4150")
FAINT = ("#A8AEBD", "#5E6677")           # disabled text
WHITE = ("#FFFFFF", "#FFFFFF")
RADIUS = 14


def apply() -> None:
    th = ctk.ThemeManager.theme

    def put(widget: str, **values) -> None:
        th[widget].update({k: list(v) if isinstance(v, tuple) else v for k, v in values.items()})

    put("CTk", fg_color=BG)
    put("CTkToplevel", fg_color=BG)
    put("CTkFrame", fg_color=CARD, top_fg_color=CARD2, border_color=BORDER, corner_radius=RADIUS)
    put("CTkButton", fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=WHITE,
        text_color_disabled=("#C7CAF6", "#A3A7E8"), corner_radius=9)
    put("CTkLabel", text_color=TEXT)
    put("CTkEntry", fg_color=INPUT, border_color=INPUT_BORDER, text_color=TEXT,
        placeholder_text_color=FAINT, border_width=2, corner_radius=8)
    put("CTkCheckBox", fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=INPUT_BORDER,
        checkmark_color=WHITE, text_color=TEXT, border_width=2, corner_radius=5)
    put("CTkSwitch", fg_color=("#D5D9E2", "#4A5265"), progress_color=ACCENT,
        button_color=("#5B6475", "#FFFFFF"), button_hover_color=("#3F4756", "#E5E7EB"),
        text_color=TEXT)
    put("CTkProgressBar", fg_color=BORDER, progress_color=ACCENT)
    put("CTkOptionMenu", fg_color=NEUTRAL, button_color=NEUTRAL_HOVER,
        button_hover_color=INPUT_BORDER, text_color=TEXT, text_color_disabled=FAINT,
        corner_radius=8)
    put("CTkScrollbar", button_color=INPUT_BORDER, button_hover_color=MUTED)
    put("CTkSegmentedButton", fg_color=NEUTRAL, selected_color=("#FFFFFF", "#4A5265"),
        selected_hover_color=("#FFFFFF", "#4A5265"), unselected_color=NEUTRAL,
        unselected_hover_color=NEUTRAL_HOVER, text_color=TEXT, text_color_disabled=FAINT,
        corner_radius=8)
    put("CTkTextbox", fg_color=CARD, border_color=BORDER, text_color=TEXT, border_width=1,
        scrollbar_button_color=INPUT_BORDER, scrollbar_button_hover_color=MUTED,
        corner_radius=RADIUS)
    put("CTkScrollableFrame", label_fg_color=CARD2)
    put("DropdownMenu", fg_color=CARD, hover_color=NEUTRAL, text_color=TEXT)
