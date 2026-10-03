"""Axis helpers (SW_design_GUI §4.6 rules 2, 4): SI prefix off (no "(x0.001)" misreadings, SWD-PM3-01), fixed
axis widths so windows line up, tick font set once.

Implements: NFR-001 (no axis regeneration in steady state)
"""
from __future__ import annotations

import pyqtgraph as pg

AXIS_WIDTH = 58


def setup_axis(axis: pg.AxisItem, label: str = "", units: str = "") -> None:
    axis.enableAutoSIPrefix(False)
    if axis.orientation in ("left", "right"):
        axis.setWidth(AXIS_WIDTH)
    if label or units:
        axis.setLabel(text=label, units=units or None)


def set_label(axis: pg.AxisItem, label: str) -> None:
    if axis.labelText != label:
        axis.setLabel(text=label)
