"""``ReadoutDock``: numeric readouts with state (SW_design_GUI §4.3): Channel | Value | Unit | Min | Max | State,
refreshed at 10 Hz from ``backend.data.latest(key)``. State = ``LatestSample.state`` (n/a / STALE / SATURATED /
INVALID / EXTRAPOLATED / OK, A-14) shown verbatim — the GUI does not classify samples. A channel that is not in
the registry (e.g. force before M3) reads "n/a".

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/widgets/readout.py @37c87471 (adapted: fixed channel list,
state from the backend, no EMA in M1, SafeDock base).

Implements: SW-RT-005 (readouts with state: force, travel, raw, sample rate), SW-CAL-008 (EXTRAPOLATED shown),
SYS-003 (force in N or kgf)
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QPushButton, QTableWidget, QTableWidgetItem, \
    QVBoxLayout, QWidget

from bend_stand.gui.format import NA, fmt_value
from bend_stand.gui.widgets.safe_dock import SafeDock

#: (key, label, unit) — force, travel (test, machine), raw, sample rate (SW-RT-005)
DEFAULT_READOUTS: tuple[tuple[str, str, str], ...] = (
    ("F_N", "Force", "N"),
    ("x_test_mm", "Travel (test)", "mm"),
    ("x_mm", "Travel (machine)", "mm"),
    ("raw", "Raw", "counts"),
    ("rate_sps", "Sample rate", "SPS"),
)
COLUMNS = ("Channel", "Value", "Unit", "Min", "Max", "State")
STATE_COLORS = {"OK": None, "n/a": "#e0e0e0", "STALE": "#ffe0b0", "INVALID": "#ffe0b0",
                "EXTRAPOLATED": "#fff3b0", "SATURATED": "#ffb0b0"}


class ReadoutDock(SafeDock):
    def __init__(self, parent: QWidget | None = None,
                 readouts: Sequence[tuple[str, str, str]] = DEFAULT_READOUTS) -> None:
        super().__init__("Readouts", parent, source="dock:readouts")
        body = QWidget(self)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(2, 2, 2, 2)
        self.table = QTableWidget(len(readouts), len(COLUMNS), body)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        f = self.table.font()
        f.setPointSize(f.pointSize() + 2)
        self.table.setFont(f)
        self._readouts = tuple(readouts)
        self._minmax: dict[str, tuple[float, float]] = {}
        for r, (key, label, unit) in enumerate(readouts):
            for c, text in enumerate((label, NA, unit, NA, NA, "n/a")):
                item = QTableWidgetItem(text)
                item.setToolTip(key)
                self.table.setItem(r, c, item)
        lay.addWidget(self.table)
        self.reset_button = QPushButton("Reset min/max", body)
        self.reset_button.setAutoDefault(False)
        self.reset_button.clicked.connect(self._minmax.clear)
        lay.addWidget(self.reset_button)
        self.setWidget(body)

    def keys(self) -> list[str]:
        return [k for k, _l, _u in self._readouts]

    def set_force_unit(self, unit: str) -> None:
        """View ▸ Units (SYS-003): the force row reads ``F_N`` or ``F_kgf`` (backend channels, no conversion here)."""
        key = {"N": "F_N", "kgf": "F_kgf"}.get(unit, "F_N")
        rows = list(self._readouts)
        for r, (k, label, _u) in enumerate(rows):
            if k in ("F_N", "F_kgf") and k != key:
                rows[r] = (key, label, unit)
                self._minmax.pop(k, None)
                self.table.item(r, 2).setText(unit)
                for c in range(self.table.columnCount()):
                    self.table.item(r, c).setToolTip(key)
        self._readouts = tuple(rows)

    def refresh(self, data: Any, available: set[str] | None = None) -> None:
        for r, (key, _label, unit) in enumerate(self._readouts):
            sample = None
            if available is None or key in available:
                try:
                    sample = data.latest(key)
                except Exception:  # noqa: BLE001 - not connected / unknown key → n/a
                    sample = None
            if sample is None:
                self._set(r, NA, NA, NA, "n/a")
                continue
            v = float(sample.value)
            state = str(sample.state)
            if math.isfinite(v) and state != "n/a":
                lo, hi = self._minmax.get(key, (v, v))
                self._minmax[key] = (min(lo, v), max(hi, v))
            mm = self._minmax.get(key)
            self._set(r, fmt_value(v, unit), fmt_value(mm[0], unit) if mm else NA,
                      fmt_value(mm[1], unit) if mm else NA, state)

    def _set(self, r: int, value: str, lo: str, hi: str, state: str) -> None:
        for c, text in ((1, value), (3, lo), (4, hi), (5, state)):
            item = self.table.item(r, c)
            if item.text() != text:
                item.setText(text)
        color = STATE_COLORS.get(state, "#ffe0b0")
        brush = QBrush(QColor(color)) if color else QBrush()
        self.table.item(r, 5).setBackground(brush)

    def state_of(self, key: str) -> str:
        r = self.keys().index(key)
        return self.table.item(r, 5).text()

    def value_text(self, key: str) -> str:
        r = self.keys().index(key)
        return self.table.item(r, 1).text()
