"""``PlotDock(SafeDock)``: one realtime plot window (SW_design_GUI §4.1; M1: time view + status lanes).

```
+-- Plot 1 ----------------------------------- [NO-SPECIMEN] [Float] [Close] [## STOP ##] -+
| Window [30 s v] [Freeze] | Y (o) auto ( ) manual [min] .. [max]                         |
| channels tree | time view (one GraphicsLayoutWidget: time PlotItem + lanes PlotItem)     |
```

* One ``pg.GraphicsLayoutWidget`` per window → one ``QGraphicsView`` paints per tick (§4.6, SWD-PM3-04/05).
* The dock pulls ``data.snapshot(keys, window_s, px_width)`` from the refresh timer; it is skipped while hidden
  (tabified behind another dock, closed) or frozen. Freeze stops data updates and enables pan/zoom.
* Channels come from the registry (``backend.channels``); bit channels go to the lanes strip; a third analog unit
  is refused with "open another plot window".
* The X-Y view and layout persistence are M3 (SW-RT-001/-003 complete).

Implements: SW-RT-001 (dock/float/re-attach, own STOP), SW-RT-002, SW-RT-003 (time view, 5–600 s, freeze,
auto/manual Y), NFR-001 (perf design §4.6)
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from bend_stand.gui.plots.lanes import StatusLanes
from bend_stand.gui.plots.time_view import TimeView
from bend_stand.gui.widgets.channel_tree import ChannelTree, bit_name
from bend_stand.gui.widgets.safe_dock import SafeDock

log = logging.getLogger(__name__)

WINDOWS_S = (5, 10, 30, 60, 120, 300, 600)
DEFAULT_WINDOW_S = 30
MIN_PX = 64
MAX_PX = 4096

_CONFIGURED = False


def configure_pyqtgraph() -> None:
    """Binding pyqtgraph settings (§4.6 rule 1), once per process."""
    global _CONFIGURED
    if not _CONFIGURED:
        pg.setConfigOptions(antialias=False, useOpenGL=False, background="w", foreground="k")
        _CONFIGURED = True


class PlotDock(SafeDock):
    infoMessage = Signal(str)

    def __init__(self, title: str = "Plot 1", parent: QWidget | None = None, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        configure_pyqtgraph()
        super().__init__(title, parent, source=f"plot:{title}")
        self._clock = clock
        self.frozen = False
        self.window_s = float(DEFAULT_WINDOW_S)
        self.updates = 0
        self.last_snapshot: Any = None

        body = QWidget(self)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(2, 2, 2, 2)
        ctl = QHBoxLayout()
        ctl.addWidget(QLabel("Window", body))
        self.window_combo = QComboBox(body)
        self.window_combo.setEditable(True)
        for w in WINDOWS_S:
            self.window_combo.addItem(f"{w} s", float(w))
        self.window_combo.setCurrentIndex(WINDOWS_S.index(DEFAULT_WINDOW_S))
        self.window_combo.activated.connect(self._on_window_combo)
        self.window_combo.lineEdit().editingFinished.connect(self._on_window_text)
        ctl.addWidget(self.window_combo)
        self.freeze_box = QCheckBox("Freeze", body)
        self.freeze_box.toggled.connect(self.set_frozen)
        ctl.addWidget(self.freeze_box)
        ctl.addSpacing(12)
        ctl.addWidget(QLabel("Y", body))
        self.y_auto = QRadioButton("auto", body)
        self.y_manual = QRadioButton("manual", body)
        self.y_auto.setChecked(True)
        grp = QButtonGroup(body)
        grp.addButton(self.y_auto)
        grp.addButton(self.y_manual)
        self._ygrp = grp
        self.y_min = QDoubleSpinBox(body)
        self.y_max = QDoubleSpinBox(body)
        for sb, v in ((self.y_min, -10.0), (self.y_max, 10.0)):
            sb.setRange(-1e9, 1e9)
            sb.setDecimals(3)
            sb.setValue(v)
            sb.setKeyboardTracking(False)
            sb.valueChanged.connect(self._on_y_mode)
        self.y_auto.toggled.connect(self._on_y_mode)
        for w in (self.y_auto, self.y_manual, self.y_min, QLabel("..", body), self.y_max):
            ctl.addWidget(w)
        ctl.addStretch(1)
        self.status_label = QLabel("", body)
        self.status_label.setStyleSheet("color: #606060;")
        ctl.addWidget(self.status_label)
        outer.addLayout(ctl)

        split = QSplitter(Qt.Orientation.Horizontal, body)
        self.tree = ChannelTree(split)
        self.tree.checkedChanged.connect(self._on_checked)
        self.graphics = pg.GraphicsLayoutWidget(split)
        self.time_view = TimeView(self.graphics, row=0)
        self.lanes = StatusLanes(self.graphics, self.time_view.plot, row=1)
        split.addWidget(self.tree)
        split.addWidget(self.graphics)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([200, 600])
        outer.addWidget(split, 1)
        self.setWidget(body)
        self.analog_keys: list[str] = []
        self.bit_keys: list[str] = []

    # ---------------------------------------------------------------- channels
    def set_channels(self, specs: Any, checked: Any = ()) -> None:
        self.tree.set_channels(specs, checked)

    def _on_checked(self, keys: list[str]) -> None:
        analog: list[tuple[str, str, str, str]] = []
        bits: list[tuple[str, str, str]] = []
        for k in keys:
            spec = self.tree.spec(k)
            bn = bit_name(spec)
            if bn:
                bits.append((k, bn, self.tree.color(k)))
            else:
                analog.append((k, getattr(spec, "label", k), getattr(spec, "unit", ""), self.tree.color(k)))
        refused = self.time_view.set_channels(analog)
        if refused:
            with self.tree.batch():
                for k in refused:
                    self.tree.set_checked(k, False)
            self.infoMessage.emit(f"{', '.join(refused)}: a third unit is not shown in one window – "
                                  "open another plot window")
        self.analog_keys = [a[0] for a in analog if a[0] not in refused]
        self.bit_keys = [b[0] for b in bits]
        self.lanes.set_bits(bits)

    def checked_keys(self) -> list[str]:
        return self.analog_keys + self.bit_keys

    # ---------------------------------------------------------------- controls
    def _on_window_combo(self, idx: int) -> None:
        self.set_window_s(float(self.window_combo.itemData(idx)))

    def _on_window_text(self) -> None:
        text = self.window_combo.currentText().replace("s", "").strip()
        try:
            self.set_window_s(float(text))
        except ValueError:
            self.window_combo.setEditText(f"{self.window_s:g} s")

    def set_window_s(self, seconds: float) -> None:
        """5–600 s (SW-RT-003); out-of-range values are clamped to the bounds."""
        s = min(max(float(seconds), float(WINDOWS_S[0])), float(WINDOWS_S[-1]))
        self.window_s = s
        self.time_view.set_window(s)
        self.window_combo.setEditText(f"{s:g} s")

    def set_frozen(self, frozen: bool) -> None:
        """Freeze: no data updates for this window; pan/zoom become active. Live resumes updates."""
        self.frozen = bool(frozen)
        if self.freeze_box.isChecked() != self.frozen:
            self.freeze_box.setChecked(self.frozen)
        self.time_view.plot.setMouseEnabled(x=self.frozen, y=self.frozen)
        self.freeze_box.setText("Freeze (frozen)" if self.frozen else "Freeze")

    def _on_y_mode(self, *_a: Any) -> None:
        manual = (self.y_min.value(), self.y_max.value())
        if manual[0] >= manual[1]:
            return
        self.time_view.set_y_mode(self.y_auto.isChecked(), {"L": manual, "R": manual})

    # ---------------------------------------------------------------- refresh (GUI thread, refresh timer)
    def is_shown(self) -> bool:
        """Visible and not covered (a tabified dock behind another one is hidden by Qt)."""
        if not self.isVisible():
            return False
        return self.isFloating() or not self.visibleRegion().isEmpty()

    def px_width(self) -> int:
        w = int(self.time_view.plot.vb.width()) if self.time_view.plot.vb.width() > 0 else self.graphics.width()
        return max(MIN_PX, min(MAX_PX, w))

    def refresh(self, data: Any) -> bool:
        """Pull one snapshot and update the curves; returns True when the window was updated."""
        if self.frozen or not self.is_shown():
            return False
        keys = self.checked_keys()
        if not keys:
            return False
        try:
            snap = data.snapshot(keys, self.window_s, self.px_width())
        except Exception as exc:  # noqa: BLE001 - not connected / not implemented yet
            text = f"no data: {exc}"
            if self.status_label.text() != text:
                self.status_label.setText(text)
            return False
        self.last_snapshot = snap
        self.time_view.update(snap, self._clock())
        self.lanes.update(snap)
        self.updates += 1
        text = f"t_end {snap.t_end_dev_s:.1f} s"
        if self.status_label.text() != text:
            self.status_label.setText(text)
        return True
