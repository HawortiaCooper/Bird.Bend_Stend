"""``IndicatorBar``: fixed two-row strip of ``IndicatorChip`` at the bottom of the main window
(SW_design_GUI §2.4). Content and levels come from the pure table :mod:`bend_stand.gui.indicator_map`; this
module only paints (change-only updates, ≤ 2 ms per tick budget, §4.6).

Implements: SAF-SW-005 (persistent indicators, UNKNOWN grey "?", refreshed every 33 ms tick)
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from bend_stand.gui import indicator_map as imap
from bend_stand.gui.theme import LED_COLOR
from bend_stand.gui.widgets.status_led import StatusLed


class IndicatorChip(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName(f"chip_{key}")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 1, 4, 1)
        lay.setSpacing(3)
        self.led = StatusLed(self, "off", 10)
        lay.addWidget(self.led)
        self.label = QLabel(label, self)
        self.label.setStyleSheet("font-weight: bold;")
        lay.addWidget(self.label)
        self.value = QLabel("?", self)
        lay.addWidget(self.value)
        self.level = "unknown"
        self.view: imap.ChipView | None = None

    def apply(self, view: imap.ChipView) -> None:
        if view == self.view:
            return
        self.view = view
        self.level = view.level
        self.led.set_color(LED_COLOR[view.level])
        if self.value.text() != view.text:
            self.value.setText(view.text)
        tip = f"{view.label}: {view.text}\n{view.tooltip}"
        if self.toolTip() != tip:
            self.setToolTip(tip)
        if self.isVisibleTo(self.parentWidget()) != view.visible:
            self.setVisible(view.visible)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.clicked.emit(self.key)
        event.accept()


class IndicatorBar(QFrame):
    """Two rows of chips in :data:`indicator_map.CHIP_ORDER`; ``update_status(status)`` once per tick."""

    chipClicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("indicatorBar")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(1)
        self.chips: dict[str, IndicatorChip] = {}
        row = QHBoxLayout()
        row.setSpacing(2)
        for entry in imap.CHIP_ORDER:
            if entry is None:
                row.addStretch(1)
                outer.addLayout(row)
                row = QHBoxLayout()
                row.setSpacing(2)
                continue
            key, label = entry
            chip = IndicatorChip(key, label, self)
            chip.clicked.connect(self.chipClicked)
            self.chips[key] = chip
            row.addWidget(chip)
        row.addStretch(1)
        outer.addLayout(row)
        self.views: dict[str, imap.ChipView] = {}

    def update_status(self, status: Any) -> None:
        for view in imap.evaluate_chips(status):
            self.views[view.key] = view
            chip = self.chips.get(view.key)
            if chip is not None:
                chip.apply(view)

    def chip(self, key: str) -> IndicatorChip:
        return self.chips[key]
