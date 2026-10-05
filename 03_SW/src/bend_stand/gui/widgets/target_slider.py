"""``TargetSlider``: target position of the Manual tab (SW_design_GUI §3.4, SW-MAN-001, GQ-07).

* Horizontal ``QSlider`` with integer µm resolution over the SW travel range (enabled SW limits ∩ FW soft limits
  from ``status().motion.limits``; the caller sets it with :meth:`set_range_mm`).
* **Only a handle drag edits the value.** A click on the groove is ignored (no page step), the wheel is ignored and
  the slider never takes the keyboard focus, so no key moves the axis.
* While dragging only the preview changes (``previewChanged``) and nothing is sent; on release exactly one
  ``targetReleased(value_mm)`` is emitted — the tab calls ``motion.move_to`` once (one MOVE_ABS on the wire).
* While idle the handle follows the commanded target (:meth:`show_target_mm`); a thin marker shows the live
  position (:meth:`set_live_mm`, painted over the groove).

Implements: SW-MAN-001 (one move on release, nothing while dragging), SW-LIM-001 (range = SW travel range)
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider, QWidget

UM = 1000.0


class TargetSlider(QSlider):
    previewChanged = Signal(float)        # mm, while dragging
    targetReleased = Signal(float)        # mm, once per drag

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("targetSlider")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setTracking(True)
        self.setPageStep(0)
        self.setSingleStep(0)
        self.setRange(0, 0)
        self._live_mm: float | None = None
        self._handle_grab = False
        self.releases = 0
        self.sliderMoved.connect(lambda v: self.previewChanged.emit(v / UM))
        self.sliderReleased.connect(self._on_released)

    # ------------------------------------------------------------------ model
    def set_range_mm(self, lo_mm: float, hi_mm: float) -> None:
        lo, hi = int(round(lo_mm * UM)), int(round(hi_mm * UM))
        if (lo, hi) != (self.minimum(), self.maximum()):
            self.setRange(lo, max(lo, hi))

    def range_mm(self) -> tuple[float, float]:
        return self.minimum() / UM, self.maximum() / UM

    def value_mm(self) -> float:
        return self.value() / UM

    def show_target_mm(self, x_mm: float | None) -> None:
        """Handle at the commanded target while the operator does not drag it."""
        if self.isSliderDown() or x_mm is None:
            return
        v = int(round(x_mm * UM))
        if v != self.value():
            self.blockSignals(True)
            self.setValue(v)
            self.blockSignals(False)

    def set_live_mm(self, x_mm: float | None) -> None:
        if x_mm != self._live_mm:
            self._live_mm = x_mm
            self.update()

    # ------------------------------------------------------------------ input policy
    def _on_handle(self, pos: QPoint) -> bool:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        hit = self.style().hitTestComplexControl(QStyle.ComplexControl.CC_Slider, opt, pos, self)
        return hit == QStyle.SubControl.SC_SliderHandle

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Only a press on the handle starts a drag; groove clicks do nothing (no page step, GQ-07)."""
        if event.button() != Qt.MouseButton.LeftButton or not self._on_handle(event.position().toPoint()):
            event.accept()
            return
        self._handle_grab = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._handle_grab:
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._handle_grab:
            event.accept()
            return
        self._handle_grab = False
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - the wheel never moves the axis
        event.ignore()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - no key moves the axis
        event.ignore()

    def _on_released(self) -> None:
        self.releases += 1
        self.targetReleased.emit(self.value() / UM)

    # ------------------------------------------------------------------ live marker
    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._live_mm is None or self.maximum() <= self.minimum():
            return
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                             QStyle.SubControl.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                             QStyle.SubControl.SC_SliderHandle, self)
        span = groove.width() - handle.width()
        frac = (self._live_mm * UM - self.minimum()) / (self.maximum() - self.minimum())
        frac = min(1.0, max(0.0, frac))
        x = int(groove.x() + handle.width() / 2 + frac * span)
        p = QPainter(self)
        p.setPen(QPen(QColor("#1f77b4"), 2))
        p.drawLine(x, groove.top() - 4, x, groove.bottom() + 4)
        p.end()
