"""``StatusLanes``: compact digital lanes for ticked status bits under the time view (SW_design_GUI §4.1, §4.6
rule 10). One ``PlotCurveItem`` per bit with a constant offset (bit · 1.2); fixed Y range (no axis repaint);
generated names as static ticks. The lanes item exists only while at least one bit is ticked.

Implements: SW-RT-002 (each status bit plottable), SW-SEQ-004 (VALID lane)
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pyqtgraph as pg

from bend_stand.gui.plots.axes import setup_axis
from bend_stand.gui.plots.time_view import interleave

LANE_STEP = 1.2
LANE_PX = 16


class StatusLanes:
    def __init__(self, layout: pg.GraphicsLayoutWidget, x_link: pg.PlotItem, row: int = 1) -> None:
        self._layout = layout
        self._row = row
        self.plot: pg.PlotItem = layout.addPlot(row=row, col=0)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.disableAutoRange()
        self.plot.setXLink(x_link)
        for name in ("left", "bottom"):
            setup_axis(self.plot.getAxis(name))
        self.plot.showAxis("right")
        setup_axis(self.plot.getAxis("right"))
        self.plot.getAxis("right").setStyle(showValues=False)
        self.keys: list[str] = []
        self.curves: dict[str, pg.PlotCurveItem] = {}
        self._set_visible(False)

    def _set_visible(self, on: bool) -> None:
        self.plot.setVisible(on)
        lay = self._layout.ci.layout
        h = (LANE_PX * max(1, len(self.keys)) + 30) if on else 0
        lay.setRowMaximumHeight(self._row, h)
        lay.setRowMinimumHeight(self._row, h)

    @property
    def visible(self) -> bool:
        return self.plot.isVisible()

    def set_bits(self, bits: Sequence[tuple[str, str, str]]) -> None:
        """``bits`` = (key, generated name, colour) in tick order."""
        keys = [b[0] for b in bits]
        if keys == self.keys:
            return
        for key in list(self.curves):
            self.plot.removeItem(self.curves.pop(key))
        self.keys = keys
        ticks = []
        for i, (key, name, color) in enumerate(bits):
            item = pg.PlotCurveItem(pen=pg.mkPen(color, width=1), antialias=False)
            self.plot.addItem(item)
            self.curves[key] = item
            ticks.append((i * LANE_STEP + 0.5, name))
        self.plot.getAxis("left").setTicks([ticks, []])
        n = len(bits)
        self.plot.setYRange(-0.2, max(1, n) * LANE_STEP, padding=0)
        self._set_visible(n > 0)

    def update(self, snapshot: Any) -> None:
        if not self.keys:
            return
        for i, key in enumerate(self.keys):
            s = snapshot.series.get(key)
            item = self.curves[key]
            if s is None:
                item.setData([], [])
                continue
            x, y = interleave(snapshot.t_col_s, s.lo, s.hi)
            y = np.where(np.isfinite(y), np.clip(y, 0.0, 1.0), np.nan) + i * LANE_STEP
            finite = bool(np.isfinite(y).all())
            item.setData(x, y, connect="all" if finite else "finite", skipFiniteCheck=finite)
