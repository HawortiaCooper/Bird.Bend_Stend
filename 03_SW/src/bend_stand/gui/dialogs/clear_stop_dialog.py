"""``ClearStopDialog``: latched items and one row per clear command (SW_design_GUI §5.6). Non-modal; STOP in the
top bar. Each row is driven by its precomputed gate (``clear_stop`` / ``estop_clear`` / ``fault_clear``); REFUSE
texts are shown as the waiting condition.

* HALT / PAUSED → ``clear_stop_async()`` (HALT_CLEAR clears HALT **and** PAUSED, D-31; no motion). While a
  sequence is PAUSED the gate carries CONFIRM → C-13 → ``clear_stop_async(confirmed=True)`` (B3-02).
* E-STOP → C-03 assertion checkbox in the row → ``estop_clear_async(confirmed=True)``.
* Faults → ``fault_clear_async()``; ``ClearResult.cleared`` names shown.
* B31-01 (D-34): every clear is one frame, never re-sent; ``ClearResult.outcome`` NOT_CONFIRMED → "Clear not
  confirmed — click again" (the button stays enabled from the gate).
* No motion restarts after a clear; after an E-STOP clear the dialog says "ENABLE and HOME".

All buttons: no default, ``NoFocus`` (keyboard can never trigger a clear).

Implements: SW-STOP-003 (explicit clear, no automatic restart), SAF-SW-004 (C-03, C-13 dialog rules),
SW-STOP-004 (Clear stop of a paused sequence = C-13)
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from bend_stand.core import protocol_gen as pg
from bend_stand.core.api import GateId
from bend_stand.gui import gating
from bend_stand.gui.indicator_map import source_text
from bend_stand.gui.dialogs.confirm_dialog import TEXTS, make_confirm
from bend_stand.gui.dialogs.safe_dialog import SafeDialog

NOT_CONFIRMED_TEXT = "Clear not confirmed — click again"


def clear_result_text(r: Any) -> str:
    """Pure: ``ClearResult`` → row text (B31-01)."""
    outcome = str(getattr(r, "outcome", "OK"))
    if outcome == "NOT_CONFIRMED":
        return NOT_CONFIRMED_TEXT
    if outcome == "REFUSED":
        return f"refused: {getattr(r, 'text', '') or 'cause still present'}"
    cleared = tuple(getattr(r, "cleared", ()) or ())
    return "cleared" + (f": {', '.join(cleared)}" if cleared else "") + \
        (f" — {r.text}" if getattr(r, "text", "") else "")


def latched_lines(status: Any) -> list[str]:
    ind = getattr(status, "indicators", None)
    if ind is None:
        return []
    out = []
    for name in ("ESTOP", "HALT", "PAUSED", *[n for n in pg.FAULTS_BITS if n]):
        it = ind.get(name.lower()) if hasattr(ind, "get") else None
        if it is not None and getattr(it, "state", "") == "ON":
            src = f" ({source_text(it)})" if source_text(it) else ""
            hint = f" — {it.clear_hint}" if it.clear_hint else ""
            out.append(f"{name}{src}{hint}")
    return out


def halt_button_label(status: Any) -> str:
    ind = getattr(status, "indicators", None)
    halt = getattr(ind, "halt", None) if ind is not None else None
    paused = getattr(ind, "paused", None) if ind is not None else None
    h = getattr(halt, "state", "") == "ON"
    p = getattr(paused, "state", "") == "ON"
    if h and p:
        return "Clear HALT + PAUSE"
    if p:
        return "Clear PAUSE"
    return "Clear HALT"


class ClearStopDialog(SafeDialog):
    REFRESH_MS = 200

    def __init__(self, backend: Any, bridge: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="Clear stop")
        self.setModal(False)
        self._backend = backend
        self._bridge = bridge
        self.confirm_dialog = None
        lay = QVBoxLayout()
        self.latched_label = QLabel("", self)
        self.latched_label.setWordWrap(True)
        lay.addWidget(self.latched_label)
        grid = QGridLayout()
        self.halt_button = self._button("Clear HALT")
        self.estop_button = self._button("Clear E-STOP")
        self.fault_button = self._button("Clear faults")
        self.estop_check = QCheckBox(TEXTS["C-03"][3] or "", self)
        self.halt_result = QLabel("", self)
        self.estop_result = QLabel("", self)
        self.fault_result = QLabel("", self)
        estop_text = QLabel(TEXTS["C-03"][1], self)
        estop_text.setWordWrap(True)
        grid.addWidget(QLabel("HALT / PAUSE", self), 0, 0)
        grid.addWidget(self.halt_button, 0, 1)
        grid.addWidget(self.halt_result, 0, 2)
        grid.addWidget(QLabel("E-STOP", self), 1, 0)
        grid.addWidget(self.estop_button, 1, 1)
        grid.addWidget(self.estop_result, 1, 2)
        grid.addWidget(estop_text, 2, 0, 1, 3)
        grid.addWidget(self.estop_check, 3, 0, 1, 3)
        grid.addWidget(QLabel("Faults", self), 4, 0)
        grid.addWidget(self.fault_button, 4, 1)
        grid.addWidget(self.fault_result, 4, 2)
        lay.addLayout(grid)
        self.after_label = QLabel("No motion restarts after a clear.", self)
        self.after_label.setWordWrap(True)
        lay.addWidget(self.after_label)
        self.setLayout(lay)
        self.halt_button.clicked.connect(self._on_halt)
        self.estop_button.clicked.connect(self._on_estop)
        self.fault_button.clicked.connect(self._on_faults)
        self.estop_check.toggled.connect(lambda _v: self.refresh())
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def _button(self, text: str) -> QPushButton:
        b = QPushButton(text, self)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setAutoDefault(False)
        b.setDefault(False)
        return b

    def _status(self) -> Any:
        return self._backend.status()

    def refresh(self) -> None:
        try:
            st = self._status()
        except Exception:  # noqa: BLE001
            return
        lines = latched_lines(st)
        text = "Latched:\n" + "\n".join(lines) if lines else "Nothing latched."
        if self.latched_label.text() != text:
            self.latched_label.setText(text)
        self.halt_button.setText(halt_button_label(st))
        for button, gid, extra in ((self.halt_button, GateId.CLEAR_STOP, True),
                                   (self.estop_button, GateId.ESTOP_CLEAR, self.estop_check.isChecked()),
                                   (self.fault_button, GateId.FAULT_CLEAR, True)):
            cs = gating.control_state(gating.gate_of(st, gid), motion=False)
            button.setEnabled(cs.enabled and bool(extra))
            button.setToolTip(cs.tooltip or button.text())

    # ---------------------------------------------------------------- actions
    def _on_halt(self) -> None:
        gate = gating.gate_of(self._status(), GateId.CLEAR_STOP)
        if gate is not None and gate.needs_confirmation:
            text = "\n".join(i.text for i in gate.confirm_items) or None
            dlg = make_confirm(self, "C-13", text=text,
                               gate_provider=lambda: gating.gate_of(self._status(), GateId.CLEAR_STOP),
                               confirm_code=gate.confirm_items[0].code)
            dlg.confirmed.connect(lambda: self._clear_halt(True))
            self.confirm_dialog = dlg
            dlg.open()
            return
        self._clear_halt(False)

    def _clear_halt(self, confirmed: bool) -> None:
        self.halt_result.setText("sending…")
        self._call(lambda: self._backend.clear_stop_async(confirmed=confirmed), self.halt_result)

    def _on_estop(self) -> None:
        if not self.estop_check.isChecked():
            return
        self.estop_result.setText("sending…")
        self._call(lambda: self._backend.estop_clear_async(confirmed=True), self.estop_result,
                   after="ENABLE the driver and HOME the axis (Manual tab). No motion restarts.")

    def _on_faults(self) -> None:
        self.fault_result.setText("sending…")
        self._call(self._backend.fault_clear_async, self.fault_result)

    def _call(self, fn: Any, label: QLabel, after: str = "") -> None:
        try:
            fut = fn()
        except Exception as exc:  # noqa: BLE001 - PreconditionError etc.
            label.setText(f"refused: {getattr(exc, 'user_text', exc)}")
            return

        def ok(r: Any) -> None:
            label.setText(clear_result_text(r))
            if after and str(getattr(r, "outcome", "")) == "OK":
                self.after_label.setText(after)
            self.refresh()

        def err(exc: BaseException) -> None:
            label.setText(f"failed: {getattr(exc, 'user_text', exc)}")

        self._bridge.watch(fut, ok, err, "clear")

    def done(self, r: int) -> None:  # noqa: D102
        self._timer.stop()
        super().done(r)
