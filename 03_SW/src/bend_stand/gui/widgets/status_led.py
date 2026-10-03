"""Small round status LED (colour + optional text next to it is the caller's job).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/widgets/status_led.py @37c87471 (unchanged except the
docstring).

Implements: SAF-SW-005 (indicator LED; colour always paired with text by the caller), SW-PLT-003 (link LED)
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

COLORS = {
    "off": QColor("#808080"),
    "green": QColor("#20a020"),
    "yellow": QColor("#e0b000"),
    "orange": QColor("#e07000"),
    "red": QColor("#d00000"),
    "blue": QColor("#2060d0"),
}


class StatusLed(QWidget):
    def __init__(self, parent: QWidget | None = None, color: str = "off", diameter: int = 12) -> None:
        super().__init__(parent)
        self._color = color
        self._d = diameter
        self.setFixedSize(QSize(diameter + 2, diameter + 2))

    @property
    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        if color not in COLORS:
            raise ValueError(color)
        if color != self._color:
            self._color = color
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(COLORS[self._color])
        p.drawEllipse(1, 1, self._d, self._d)
