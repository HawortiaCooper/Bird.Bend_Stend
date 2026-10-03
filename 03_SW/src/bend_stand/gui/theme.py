"""Safety palette and style sheets (SW_design_GUI §2, P5: colour always comes with text).

Levels used by chips, banners and status cells:
``unknown`` grey "?" (never green, P5) · ``ok`` green · ``warn`` amber · ``alarm`` red · ``info`` blue ·
``neutral`` light grey (OFF without meaning "bad").

Implements: SAF-SW-005 (UNKNOWN drawn grey), SW-STOP-001 (STOP red)
"""
from __future__ import annotations

from PySide6.QtGui import QColor

LEVELS = ("unknown", "ok", "warn", "alarm", "info", "neutral")

#: LED colour per level (StatusLed colour names)
LED_COLOR = {
    "unknown": "off",
    "ok": "green",
    "warn": "yellow",
    "alarm": "red",
    "info": "blue",
    "neutral": "off",
}

STOP_RED = "#c00000"
STOP_RED_DARK = "#600000"
AMBER = "#e0a000"
AMBER_BG = "#fff0c0"
RED_BG = "#ffc8c8"
GREY_BG = "#e8e8e8"
GREEN_BG = "#d8f0d8"
INFO_BG = "#d8e4f8"

BANNER_STYLE = {
    "alarm": f"background-color: {RED_BG}; color: #400000; border: 1px solid #a00000;",
    "warn": f"background-color: {AMBER_BG}; color: #402800; border: 1px solid {AMBER};",
    "info": f"background-color: {GREY_BG}; color: #202020; border: 1px solid #a0a0a0;",
}

MODE_BANNER_STYLE = (
    f"background-color: {AMBER_BG}; color: #000000; font-weight: bold; "
    "border-left: 10px solid #000000; padding: 4px;"
)

NOSPEC_TAG_STYLE = f"background-color: {AMBER}; color: #000000; font-weight: bold; padding: 1px 4px;"

CELL_BG = {
    "ok": QColor(0, 0, 0, 0),
    "dirty": QColor("#fff3b0"),
    "error": QColor(RED_BG),
    "warn": QColor("#ffe0b0"),
    "locked": QColor("#eeeeee"),
}

# pyqtgraph pens (colour per channel index; the colour swatch in the channel tree replaces a legend)
CURVE_COLORS = (
    "#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#e377c2",
    "#7f7f7f", "#bcbd22",
)


def curve_color(index: int) -> str:
    return CURVE_COLORS[index % len(CURVE_COLORS)]
