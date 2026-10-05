"""``SequenceChart``: the sequence chart x = travel (mm), y = load (N / kgf) (SW_design_GUI §3.6; SW-SCH-001/002).

* **Planned path** (SW-SCH-001) from the backend's ``planned_path`` (``PathPoint(x_mm, f_n, exec_idx, label,
  known)``): one polyline, broken at HOME (non-finite point); segments that end at an estimated coordinate
  (``known`` ≠ "both", placed via k_est by the backend) are dashed; capture points green; step labels as text items
  (at most :data:`MAX_LABELS`, thinned evenly beyond that).
* **During execution** (SW-SCH-002), called from the main refresh tick (33 ms, i.e. ≈ 30 Hz ≥ 10 Hz): a vertical
  line marker at the current travel and a point at (x, F) from ``data.latest``; the active step's planned point
  highlighted (amber); the measured trace ``data.sequence_trace()`` overlaid; a step that ended **NOT_REACHED**
  (D-32) drawn as a red cross where the axis stopped.
* Ranges are explicit (§4.6): computed from the plan (+ trace) with a margin, recomputed only on plan changes or
  when the live point / trace leaves them.

Display only — the path, the active step and the trace come from the backend (P1).

Implements: SW-SCH-001 (planned path with step labels), SW-SCH-002 (moving line marker, live point, active step,
measured trace, ≥ 10 Hz), SYS-003 (N / kgf axis)
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from bend_stand.gui.plots.axes import setup_axis
from bend_stand.gui.seq_access import PathPt
from bend_stand.gui.units_state import force_unit

MAX_LABELS = 60
PATH_PEN = pg.mkPen("#1f4e9c", width=2)
EST_PEN = pg.mkPen("#1f4e9c", width=2, style=Qt.PenStyle.DashLine)
TRACE_PEN = pg.mkPen("#7f7f7f", width=1)
MARKER_PEN = pg.mkPen("#d62728", width=2)
MARGIN = 0.08


class SequenceChart(QWidget):
    """See module docstring. ``marker_updates`` counts live-marker repaints (SW-SCH-002 rate test)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sequenceChart")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.plot_widget = pg.PlotWidget(self, background="w")
        lay.addWidget(self.plot_widget)
        pi: pg.PlotItem = self.plot_widget.getPlotItem()
        pi.setMenuEnabled(False)
        pi.hideButtons()
        pi.showGrid(x=True, y=True, alpha=0.25)
        setup_axis(pi.getAxis("bottom"), "travel", "mm")
        setup_axis(pi.getAxis("left"), "load", force_unit().unit)
        self.plot_item = pi
        self.vb = pi.getViewBox()
        self.vb.disableAutoRange()
        self.path_curve = pg.PlotDataItem(pen=PATH_PEN, connect="finite")
        self.est_curve = pg.PlotDataItem(pen=EST_PEN, connect="pairs")
        self.capture_points = pg.ScatterPlotItem(size=9, brush=pg.mkBrush("#2ca02c"), pen=pg.mkPen("#1a6b1a"))
        self.step_points = pg.ScatterPlotItem(size=6, brush=pg.mkBrush("#1f4e9c"), pen=None)
        self.active_point = pg.ScatterPlotItem(size=16, brush=pg.mkBrush(255, 190, 0, 160), pen=pg.mkPen("#a06000"))
        self.trace_curve = pg.PlotDataItem(pen=TRACE_PEN)
        self.not_reached = pg.ScatterPlotItem(size=14, symbol="x", brush=pg.mkBrush("#d00000"),
                                              pen=pg.mkPen("#d00000", width=2))
        self.marker_line = pg.InfiniteLine(angle=90, movable=False, pen=MARKER_PEN)
        self.live_point = pg.ScatterPlotItem(size=10, brush=pg.mkBrush("#d62728"), pen=pg.mkPen("#600000"))
        for it in (self.trace_curve, self.path_curve, self.est_curve, self.step_points, self.capture_points,
                   self.active_point, self.not_reached, self.live_point):
            pi.addItem(it)
        pi.addItem(self.marker_line, ignoreBounds=True)
        self.marker_line.hide()
        self.labels: list[pg.TextItem] = []
        self.points: list[PathPt] = []
        self._by_exec: dict[int, tuple[float, float]] = {}
        self._nr: list[tuple[float, float]] = []
        self._range: tuple[float, float, float, float] | None = None
        self.marker_updates = 0
        self.live_xy: tuple[float, float] | None = None
        self.active_exec: int | None = None
        force_unit().changed.connect(self._on_unit)

    # ------------------------------------------------------------------ units
    def _f(self, f_n: float) -> float:
        v = force_unit().from_n(f_n)
        return math.nan if v is None else float(v)

    def _on_unit(self, unit: str) -> None:
        setup_axis(self.plot_item.getAxis("left"), "load", unit)
        self.set_path(self.points)

    # ------------------------------------------------------------------ planned path (SW-SCH-001)
    def set_path(self, points: Sequence[PathPt]) -> None:
        self.points = list(points)
        xs = np.array([p.x for p in self.points], dtype=float)
        fs = np.array([self._f(p.f) if math.isfinite(p.f) else math.nan for p in self.points], dtype=float)
        self.path_curve.setData(xs, fs) if len(xs) else self.path_curve.setData([], [])
        # dashed overlay: segments ending at an estimated coordinate
        ex, ef = [], []
        for a, b in zip(self.points, self.points[1:], strict=False):
            if b.known and b.known != "both" and math.isfinite(a.x) and math.isfinite(b.x):
                ex += [a.x, b.x]
                ef += [self._f(a.f), self._f(b.f)]
        self.est_curve.setData(np.array(ex), np.array(ef))
        fin = [(p, f) for p, f in zip(self.points, fs, strict=True) if math.isfinite(p.x) and math.isfinite(f)]
        self.step_points.setData([p.x for p, _f in fin], [f for _p, f in fin])
        caps = [(p.x, f) for p, f in fin if p.capture]
        self.capture_points.setData([c[0] for c in caps], [c[1] for c in caps])
        self._by_exec = {p.exec_idx: (p.x, f) for p, f in fin if p.exec_idx is not None}
        for t in self.labels:
            self.plot_item.removeItem(t)
        self.labels = []
        labelled = [(p, f) for p, f in fin if p.label]
        step = max(1, math.ceil(len(labelled) / MAX_LABELS))
        for p, f in labelled[::step]:
            t = pg.TextItem(p.label, color="#303030", anchor=(0, 1))
            t.setPos(p.x, f)
            self.plot_item.addItem(t, ignoreBounds=True)
            self.labels.append(t)
        self._range = None
        self._fit([(p.x, f) for p, f in fin])
        self._set_active(self.active_exec)

    def clear_run(self) -> None:
        self.trace_curve.setData([], [])
        self._nr = []
        self.not_reached.setData([], [])
        self.marker_line.hide()
        self.live_point.setData([], [])
        self.live_xy = None
        self._set_active(None)

    # ------------------------------------------------------------------ execution (SW-SCH-002)
    def update_live(self, x: float | None, f_n: float | None, active_exec: int | None,
                    trace: tuple[np.ndarray, np.ndarray] | None = None) -> None:
        """One refresh: live marker (vertical line + point), active step, trace (x mm, F in N)."""
        if trace is not None and len(trace[0]):
            ty = np.asarray(trace[1], dtype=float)
            if force_unit().unit != "N":
                ty = np.array([self._f(v) for v in ty])
            self.trace_curve.setData(np.asarray(trace[0], dtype=float), ty)
        if x is not None and math.isfinite(x):
            self.marker_line.setValue(float(x))
            if not self.marker_line.isVisible():
                self.marker_line.show()
            fy = self._f(f_n) if f_n is not None and math.isfinite(f_n) else math.nan
            if math.isfinite(fy):
                self.live_point.setData([float(x)], [fy])
                self.live_xy = (float(x), fy)
            else:
                self.live_point.setData([], [])
                self.live_xy = (float(x), math.nan)
            self._grow((float(x), fy))
        self.marker_updates += 1
        self._set_active(active_exec)

    def mark_not_reached(self, x: float | None, f_n: float | None, exec_idx: int | None = None) -> None:
        """A load step ended NOT_REACHED (D-32): red cross where the axis stopped (planned point if unknown)."""
        if x is None or not math.isfinite(x):
            if exec_idx is None or exec_idx not in self._by_exec:
                return
            x, fy = self._by_exec[exec_idx]
        else:
            fy = self._f(f_n) if f_n is not None and math.isfinite(f_n) else self._by_exec.get(exec_idx, (0, 0))[1]
        self._nr.append((float(x), float(fy)))
        self.not_reached.setData([p[0] for p in self._nr], [p[1] for p in self._nr])

    def not_reached_points(self) -> list[tuple[float, float]]:
        return list(self._nr)

    def _set_active(self, exec_idx: int | None) -> None:
        self.active_exec = exec_idx
        pt = self._by_exec.get(exec_idx) if exec_idx is not None else None
        if pt is None:
            self.active_point.setData([], [])
        else:
            self.active_point.setData([pt[0]], [pt[1]])

    # ------------------------------------------------------------------ ranges (explicit, §4.6)
    def _fit(self, pts: list[tuple[float, float]]) -> None:
        pts = [p for p in pts if math.isfinite(p[0]) and math.isfinite(p[1])]
        if not pts:
            self._apply_range(0.0, 10.0, -10.0, 10.0)
            return
        xs = [p[0] for p in pts] + [0.0]
        fs = [p[1] for p in pts] + [0.0]
        self._apply_range(min(xs), max(xs), min(fs), max(fs))

    def _grow(self, p: tuple[float, float]) -> None:
        if self._range is None:
            return
        x0, x1, y0, y1 = self._range
        x, y = p
        if x0 <= x <= x1 and (not math.isfinite(y) or y0 <= y <= y1):
            return
        nx0, nx1 = min(x0, x), max(x1, x)
        ny0, ny1 = (min(y0, y), max(y1, y)) if math.isfinite(y) else (y0, y1)
        self._apply_range(nx0, nx1, ny0, ny1)

    def _apply_range(self, x0: float, x1: float, y0: float, y1: float) -> None:
        dx = max(x1 - x0, 1.0) * MARGIN
        dy = max(y1 - y0, 1.0) * MARGIN
        self._range = (x0 - dx, x1 + dx, y0 - dy, y1 + dy)
        self.vb.setRange(xRange=self._range[:2], yRange=self._range[2:], padding=0.0)

    def view_range(self) -> tuple[float, float, float, float] | None:
        return self._range

    def path_xy(self) -> tuple[np.ndarray, np.ndarray]:
        x, y = self.path_curve.getData()
        return (np.array([]) if x is None else x), (np.array([]) if y is None else y)
