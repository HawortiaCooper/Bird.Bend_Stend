"""``TimeView``: time plot of ``PlotSnapshot`` columns (SW_design_GUI §4.1, §4.3, §4.6).

* Relative time axis: x = ``PlotSnapshot.t_col_s`` ∈ [−window, 0]; the X range stays constant (no axis
  regeneration per tick).
* Explicit Y ranges, auto-range disabled: auto mode uses :class:`~.autorange.AutoRange` (hysteresis), manual mode
  fixed min/max. Left axis = first unit, right axis (own ViewBox) = second unit; a third unit is refused
  ("open another plot window").
* Each analog key is drawn from the backend's min/max columns, interleaved into 2 points per pixel column
  (``PlotCurveItem``, no copies beyond the interleave). ``vstate`` styles: 0 OK solid, 1 EXTRAPOLATED dashed,
  2 INVALID grey, 3 NO_DATA gap; the dashed / grey curves are created only when such a column exists.

Implements: SW-RT-003 (time view, window, freeze, auto/manual Y), SW-RT-002 (per-channel on/off), SW-CAL-008
(extrapolated style), NFR-001 (one view per window, explicit ranges, relative axis)
"""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt

from bend_stand.gui.plots.autorange import AutoRange, data_bounds
from bend_stand.gui.plots.axes import setup_axis

VSTATE_OK, VSTATE_EXTRAPOLATED, VSTATE_INVALID, VSTATE_NO_DATA = 0, 1, 2, 3


def interleave(t_col: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pure: 2 points per pixel column (lo then hi) — the min/max envelope drawn as one polyline."""
    x = np.repeat(np.asarray(t_col, dtype=np.float64), 2)
    y = np.empty(2 * len(lo), dtype=np.float64)
    y[0::2] = lo
    y[1::2] = hi
    return x, y


def split_vstate(y: np.ndarray, vstate: np.ndarray) -> dict[int, np.ndarray]:
    """Pure: per style (OK / EXTRAPOLATED / INVALID) the interleaved y with NaN where the column has another
    state; NO_DATA columns are NaN everywhere (gap). Only states that occur are returned (OK always)."""
    vs = np.repeat(np.asarray(vstate, dtype=np.uint8), 2)
    out: dict[int, np.ndarray] = {}
    for st in (VSTATE_OK, VSTATE_EXTRAPOLATED, VSTATE_INVALID):
        m = vs == st
        if st != VSTATE_OK and not m.any():
            continue
        out[st] = y if (st == VSTATE_OK and m.all()) else np.where(m, y, np.nan)
    return out


@dataclass
class _Curves:
    key: str
    color: str
    axis: str                          # "L" | "R"
    items: dict[int, pg.PlotCurveItem] = field(default_factory=dict)


class TimeView:
    """One ``PlotItem`` (+ right ViewBox) inside the dock's ``GraphicsLayoutWidget``."""

    def __init__(self, layout: pg.GraphicsLayoutWidget, row: int = 0) -> None:
        self.plot: pg.PlotItem = layout.addPlot(row=row, col=0)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.disableAutoRange()
        self.plot.setMouseEnabled(x=False, y=False)
        for name in ("left", "bottom", "right"):
            setup_axis(self.plot.getAxis(name))
        self.plot.getAxis("bottom").setLabel(text="t [s]")
        self.plot.showAxis("right")
        self.vb_right = pg.ViewBox(enableMenu=False)
        self.vb_right.setMouseEnabled(x=False, y=False)
        self.vb_right.disableAutoRange()
        self.plot.scene().addItem(self.vb_right)
        self.plot.getAxis("right").linkToView(self.vb_right)
        self.vb_right.setXLink(self.plot)
        self.plot.vb.sigResized.connect(self._sync_right)
        self.curves: dict[str, _Curves] = {}
        self.units: dict[str, str] = {}            # "L"/"R" -> unit
        self.window_s = 30.0
        self.y_auto = True
        self.y_manual: dict[str, tuple[float, float]] = {"L": (-10.0, 10.0), "R": (-10.0, 10.0)}
        self._auto = {"L": AutoRange(), "R": AutoRange()}
        self.ranges: dict[str, tuple[float, float] | None] = {"L": None, "R": None}
        self.set_window(self.window_s)

    def _sync_right(self) -> None:
        self.vb_right.setGeometry(self.plot.vb.sceneBoundingRect())

    # ---------------------------------------------------------------- configuration
    def set_window(self, window_s: float) -> None:
        self.window_s = float(window_s)
        self.plot.setXRange(-self.window_s, 0.0, padding=0)

    def set_y_mode(self, auto: bool, manual: Mapping[str, tuple[float, float]] | None = None) -> None:
        self.y_auto = bool(auto)
        if manual:
            self.y_manual.update(manual)
        for a in self._auto.values():
            a.reset()
        if not self.y_auto:
            self._apply_ranges(self.y_manual)

    def set_channels(self, channels: Sequence[tuple[str, str, str, str]]) -> list[str]:
        """``channels`` = (key, label, unit, colour) in tick order. Returns the keys refused (third unit)."""
        units: dict[str, str] = {}
        refused: list[str] = []
        wanted: dict[str, tuple[str, str]] = {}
        for key, _label, unit, color in channels:
            side = next((s for s, u in units.items() if u == unit), None)
            if side is None:
                if "L" not in units:
                    side = "L"
                elif "R" not in units:
                    side = "R"
                else:
                    refused.append(key)
                    continue
                units[side] = unit
            wanted[key] = (side, color)
        for key in list(self.curves):
            if key not in wanted or wanted[key][0] != self.curves[key].axis:
                self._remove(key)
        for key, (side, color) in wanted.items():
            if key not in self.curves:
                self.curves[key] = _Curves(key, color, side)
                self._curve(self.curves[key], VSTATE_OK)
        if units != self.units:
            self.units = units
            self.plot.getAxis("left").setLabel(text=units.get("L", ""))
            self.plot.getAxis("right").setLabel(text=units.get("R", ""))
            for a in self._auto.values():
                a.reset()
        return refused

    def _curve(self, c: _Curves, state: int) -> pg.PlotCurveItem:
        item = c.items.get(state)
        if item is None:
            if state == VSTATE_OK:
                pen = pg.mkPen(c.color, width=1)
            elif state == VSTATE_EXTRAPOLATED:
                pen = pg.mkPen(c.color, width=1, style=Qt.PenStyle.DashLine)
            else:
                pen = pg.mkPen("#a0a0a0", width=1)
            item = pg.PlotCurveItem(pen=pen, antialias=False)
            if c.axis == "L":
                self.plot.addItem(item)
            else:
                self.vb_right.addItem(item)
            c.items[state] = item
        return item

    def _remove(self, key: str) -> None:
        c = self.curves.pop(key)
        for item in c.items.values():
            if c.axis == "L":
                self.plot.removeItem(item)
            else:
                self.vb_right.removeItem(item)

    # ---------------------------------------------------------------- data
    def update(self, snapshot: Any, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        t_col = snapshot.t_col_s
        series = snapshot.series
        side_arrays: dict[str, list[np.ndarray]] = {"L": [], "R": []}
        for key, c in self.curves.items():
            s = series.get(key)
            if s is None:
                for item in c.items.values():
                    item.setData([], [])
                continue
            x, y = interleave(t_col, s.lo, s.hi)
            parts = split_vstate(y, s.vstate)
            for st, item in list(c.items.items()):
                if st not in parts:
                    item.setData([], [])
            for st, ys in parts.items():
                item = self._curve(c, st)
                finite = bool(np.isfinite(ys).all())
                item.setData(x, ys, connect="all" if finite else "finite", skipFiniteCheck=finite)
            side_arrays[c.axis].append(s.lo)
            side_arrays[c.axis].append(s.hi)
        if self.y_auto:
            new = {side: self._auto[side].update(data_bounds(arrs), now) for side, arrs in side_arrays.items()}
            self._apply_ranges(new)

    def _apply_ranges(self, ranges: Mapping[str, tuple[float, float] | None]) -> None:
        for side, r in ranges.items():
            if r is None or r == self.ranges.get(side):
                continue
            self.ranges[side] = r
            vb = self.plot.vb if side == "L" else self.vb_right
            vb.setYRange(r[0], r[1], padding=0, update=False)
            vb.update()
