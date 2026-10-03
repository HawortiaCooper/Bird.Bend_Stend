"""Pause / Resume toolbar button (SW_design_GUI §2.2 item 2, §5.9).

* Text follows ``indicators.paused`` only (P4: no optimistic change): OFF / UNKNOWN → "‖ Pause", ON → "▶ Resume".
* Pause acts on the mouse **press** (same path as STOP, §5.1): ``backend.pause("toolbar") → StopResult`` (FW PAUSE
  0x3B, A-01), shown in the stop banner like a STOP result.
* Resume acts on click: ``backend.resume("toolbar") → GateResult`` (RESUME 0x3C, D-31, B3-01). A REFUSE (HALT /
  ESTOP / FAULT latched, B3-03) is reported as "Clear stop first: …" (display mapping on ``BLOCK_BITS`` codes).
* Enabled from the gate ``pause`` resp. ``resume`` (missing gate → disabled, fail-safe §2.7). ``NoFocus``.

Implements: SW-STOP-004 (GUI Pause / Resume), SAF-SW-005 (PAUSED state shown), IF-011 (Pause synchronous)
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QToolButton, QWidget

from bend_stand.core.api import GateId
from bend_stand.gui import gating

PAUSE_TEXT = "‖ Pause"
RESUME_TEXT = "▶ Resume"


class PauseButton(QToolButton):
    stopResult = Signal(object)          # StopResult of a pause
    resumeResult = Signal(object)        # GateResult of a resume

    def __init__(self, backend: Any, parent: QWidget | None = None, source: str = "toolbar") -> None:
        super().__init__(parent)
        self.setObjectName("pauseButton")
        self._backend = backend
        self._source = source
        self.mode = "pause"
        self._press_mode: str | None = None
        self.setText(PAUSE_TEXT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumHeight(40)
        self.pressed.connect(self._on_pressed)
        self.clicked.connect(self._on_clicked)

    def update_status(self, status: Any) -> None:
        paused = getattr(getattr(status, "indicators", None), "paused", None)
        mode = "resume" if getattr(paused, "state", "UNKNOWN") == "ON" else "pause"
        if mode != self.mode:
            self.mode = mode
            self.setText(RESUME_TEXT if mode == "resume" else PAUSE_TEXT)
        gid = GateId.RESUME if mode == "resume" else GateId.PAUSE
        gate = gating.gate_of(status, gid)
        cs = gating.control_state(gate, motion=True)
        tip = cs.tooltip
        if mode == "resume" and cs.clear_first:
            tip = "Clear stop first: " + "; ".join(i.text for i in cs.clear_first) + "\n" + tip
        if self.isEnabled() != cs.enabled:
            self.setEnabled(cs.enabled)
        if self.toolTip() != tip:
            self.setToolTip(tip)

    def _on_pressed(self) -> None:
        self._press_mode = self.mode
        if self.mode == "pause":
            try:
                result = self._backend.pause(self._source)
            except Exception as exc:  # noqa: BLE001 - never raises by contract; defensive
                result = _NotSent("PAUSE", self._source, str(exc))
            self.stopResult.emit(result)

    def _on_clicked(self) -> None:
        if self._press_mode != "resume":
            return
        self._press_mode = None
        try:
            gate = self._backend.resume(self._source)
        except Exception as exc:  # noqa: BLE001
            gate = None
            self.resumeResult.emit(_Refused(str(exc)))
            return
        self.resumeResult.emit(gate)


class _NotSent:
    def __init__(self, cmd: str, source: str, reason: str) -> None:
        self.cmd, self.source, self.sent, self.reason, self.error = cmd, source, False, reason, reason


class _Refused:
    """Stand-in GateResult for an exception from ``resume`` (never expected)."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.ok = False
        self.refused = ()

    def text(self) -> str:
        return self._text
