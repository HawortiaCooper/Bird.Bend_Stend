"""Calibration & Tare tab (SW_design_GUI §3.5).

* **Load calibration (active)**: from ``status().calibration`` (K, status, LOW_SPAN, date, valid for limits — an
  AFE-configuration mismatch shows "calibration invalid for load limits", SW-CAL-009) and
  ``calibrations.active_load()`` (file, points, fit, AFE, ``push_calibrated``) — shown verbatim; [Start load
  calibration wizard…] (gate ``cal_load_start``); [History…] lists the previous files read-only.
* **Travel calibration (active)**: active / board steps/mm, difference + restore state (``travel_diff``) with one
  button per ``travel_diff.actions`` → ``calibrations.resolve_travel_difference_async(action)`` (B3-19), expected
  steps/mm; [Start travel calibration wizard…] (gate ``cal_travel_start``); [History…].
* **Tare** (session only): ``status().tare`` (state, tare_raw, age, id), window field (2–60 s), [TARE] (same
  handler as the toolbar → non-modal TarePopup), [Undo tare] while ``tare.can_undo`` (A-16), FW thresholds state.

The wizards and the popup are opened by the main window (one operation at a time is the backend's rule, B §3.5).

Implements: SW-CAL-001 (launchers, travel restore state), SW-CAL-004 (active travel calibration shown), SW-CAL-008
(LOW_SPAN shown), SW-CAL-009 (active calibration, AFE mismatch, history), SW-TARE-001 (TARE also here),
SW-TARE-002 (tare state, Undo)
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bend_stand.core.api import GateId
from bend_stand.gui import gating
from bend_stand.gui.dialogs.safe_dialog import SafeMessageBox
from bend_stand.gui.format import NA, fmt_age_s, fmt_value
from bend_stand.gui.wizards.phase_views import fmt_any

log = logging.getLogger(__name__)

ACTION_LABELS = {"restore": "Restore", "keep_board": "Keep board value", "ignore_session": "Ignore for this session"}


class CalibrationTab(QWidget):
    message = Signal(str, str)
    travelWizardRequested = Signal()
    loadWizardRequested = Signal()
    tareRequested = Signal(float)
    travelActionRequested = Signal(str)

    def __init__(self, backend: Any, bridge: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calibrationTab")
        self._backend = backend
        self._bridge = bridge
        self._n = 0
        self._actions: tuple[str, ...] = ()
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(self._build_load(), 1)
        top.addWidget(self._build_travel(), 1)
        lay.addLayout(top)
        lay.addWidget(self._build_tare())
        lay.addStretch(1)
        self.gates = gating.GateBinder()
        self.gates.bind(self.load_wizard_button, GateId.CAL_LOAD_START, base_tooltip="Load calibration wizard")
        self.gates.bind(self.travel_wizard_button, GateId.CAL_TRAVEL_START, base_tooltip="Travel calibration wizard")
        self.gates.bind(self.tare_button, GateId.TARE, base_tooltip="Tare (session only)")
        bridge.travelRestore.connect(lambda r: self.message.emit(f"Travel calibration: {r.payload}", "info"))

    # ================================================================== construction
    def _btn(self, parent: QWidget, text: str, name: str, slot: Any) -> QPushButton:
        b = QPushButton(text, parent)
        b.setObjectName(name)
        b.setAutoDefault(False)
        b.clicked.connect(slot)
        return b

    def _build_load(self) -> QGroupBox:
        box = QGroupBox("Load calibration (active)", self)
        g = QVBoxLayout(box)
        self.load_label = QLabel(NA, box)
        self.load_label.setObjectName("activeLoad")
        self.load_label.setWordWrap(True)
        self.load_warn = QLabel("", box)
        self.load_warn.setObjectName("loadWarnings")
        self.load_warn.setWordWrap(True)
        self.load_warn.setStyleSheet("color: #805000;")
        g.addWidget(self.load_label)
        g.addWidget(self.load_warn)
        row = QHBoxLayout()
        self.load_wizard_button = self._btn(box, "Start load calibration wizard…", "loadWizardButton",
                                            self.loadWizardRequested.emit)
        self.load_history_button = self._btn(box, "History…", "loadHistory", lambda: self.show_history("load"))
        row.addWidget(self.load_wizard_button)
        row.addWidget(self.load_history_button)
        row.addStretch(1)
        g.addLayout(row)
        return box

    def _build_travel(self) -> QGroupBox:
        box = QGroupBox("Travel calibration (active)", self)
        g = QVBoxLayout(box)
        self.travel_label = QLabel(NA, box)
        self.travel_label.setObjectName("activeTravel")
        self.travel_label.setWordWrap(True)
        self.travel_diff = QLabel("", box)
        self.travel_diff.setObjectName("travelDiff")
        self.travel_diff.setWordWrap(True)
        self.travel_diff.setStyleSheet("color: #805000;")
        g.addWidget(self.travel_label)
        g.addWidget(self.travel_diff)
        self.action_row = QHBoxLayout()
        self.action_buttons: dict[str, QPushButton] = {}
        for action, label in ACTION_LABELS.items():
            b = self._btn(box, label + ("…" if action == "restore" else ""), f"travel_{action}",
                          lambda _c=False, a=action: self.travelActionRequested.emit(a))
            b.hide()
            self.action_row.addWidget(b)
            self.action_buttons[action] = b
        self.action_row.addStretch(1)
        g.addLayout(self.action_row)
        row = QHBoxLayout()
        self.travel_wizard_button = self._btn(box, "Start travel calibration wizard…", "travelWizardButton",
                                              self.travelWizardRequested.emit)
        self.travel_history_button = self._btn(box, "History…", "travelHistory", lambda: self.show_history("travel"))
        row.addWidget(self.travel_wizard_button)
        row.addWidget(self.travel_history_button)
        row.addStretch(1)
        g.addLayout(row)
        return box

    def _build_tare(self) -> QGroupBox:
        box = QGroupBox("Tare (session only; repeat after every application start)", self)
        g = QGridLayout(box)
        self.tare_label = QLabel("no tare", box)
        self.tare_label.setObjectName("tareInfo")
        self.tare_label.setWordWrap(True)
        g.addWidget(self.tare_label, 0, 0, 1, 5)
        g.addWidget(QLabel("window", box), 1, 0)
        self.window_spin = QDoubleSpinBox(box)
        self.window_spin.setObjectName("tareWindow")
        self.window_spin.setDecimals(1)
        self.window_spin.setRange(2.0, 60.0)
        self.window_spin.setValue(10.0)
        self.window_spin.setSuffix(" s")
        g.addWidget(self.window_spin, 1, 1)
        self.tare_button = self._btn(box, "TARE", "calTareButton",
                                     lambda: self.tareRequested.emit(float(self.window_spin.value())))
        f = self.tare_button.font()
        f.setBold(True)
        self.tare_button.setFont(f)
        g.addWidget(self.tare_button, 1, 2)
        self.undo_button = self._btn(box, "Undo tare", "calUndoTare", self.on_undo)
        g.addWidget(self.undo_button, 1, 3)
        self.thr_label = QLabel("FW thresholds ● ?", box)
        self.thr_label.setObjectName("calThresholds")
        g.addWidget(self.thr_label, 1, 4)
        return box

    # ================================================================== actions
    def on_undo(self) -> None:
        g = self._backend.tare_engine.undo()
        if g.ok:
            self.message.emit("Tare undone", "info")
        else:
            self.message.emit(f"Undo tare refused: {gating.refusal_text(g)}", "warn")

    def show_history(self, kind: str) -> SafeMessageBox:
        try:
            files = list(self._backend.calibrations.history(kind))
        except Exception as exc:  # noqa: BLE001
            files = [f"(history unavailable: {exc})"]
        text = "\n".join(files) if files else "(no previous calibration files)"
        self.history_box = SafeMessageBox.show_message(self, f"{kind.capitalize()} calibration history",
                                                       text + "\n\nActivating an old file is not offered "
                                                              "(SW-CAL-009: the active copy only).")
        return self.history_box

    # ================================================================== status
    def update_status(self, st: Any) -> None:
        self._n += 1
        self.gates.update(st)
        cal = st.calibration
        tare = st.tare
        self.undo_button.setEnabled(bool(tare.can_undo))
        thr = st.safety.thresholds
        self._set(self.thr_label, f"FW thresholds ● {thr.state}" + (" (clamped)" if thr.clamped else ""))
        if tare.state in (None, "NONE"):
            ttxt = "no tare in this session — motion with enabled PC load limits needs a tare (SW-TARE-002)"
        else:
            ttxt = (f"tare_raw {fmt_value(tare.tare_raw, 'counts')} counts  ({tare.state}), age "
                    f"{fmt_age_s(tare.age_s)}" + (f", id {tare.tare_id}" if tare.tare_id else ""))
        self._set(self.tare_label, ttxt)
        diff = cal.travel_diff
        actions = tuple(diff.actions) if (cal.travel_cal_differs or diff.differs) else ()
        for a, b in self.action_buttons.items():
            b.setVisible(a in actions)
        if cal.travel_cal_differs or diff.differs:
            dtxt = (f"(!) board steps/mm {fmt_value(diff.board_spm if diff.board_spm is not None else cal.board_spm, 'mm')}"
                    f" differs from the active calibration {fmt_value(diff.session_spm if diff.session_spm is not None else cal.travel_spm, 'mm')}")
            if cal.restore_pending or diff.restore_pending:
                dtxt += " — restoring after an aborted calibration…"
        else:
            dtxt = ""
        self._set(self.travel_diff, dtxt)
        self.travel_diff.setVisible(bool(dtxt))
        if self._n % 15 == 1:
            self._update_records(st)

    def _update_records(self, st: Any) -> None:
        cal = st.calibration
        try:
            rec = self._backend.calibrations.active_load()
        except Exception:  # noqa: BLE001
            rec = None
        lines = []
        if cal.load_status is None and rec is None:
            lines.append("no active load calibration — forces unavailable; PC load limits need one "
                         "(or the no-specimen mode, SW-LIM-004)")
        else:
            lines.append(f"Fit {cal.load_status or '?'}" + (f"   K {cal.load_k:.6g} N/count" if cal.load_k is not None
                                                             else "") + (f"   date {cal.load_date}" if cal.load_date
                                                                         else ""))
            if rec:
                for k, v in rec.items():
                    if isinstance(v, (str, int, float, bool)) or v is None:
                        lines.append(f"{k}: {fmt_any(v)}")
        self._set(self.load_label, "\n".join(lines[:14]))
        warns = []
        if cal.low_span:
            warns.append("LOW_SPAN: largest reference force < 20 % FS — forces beyond 3× the largest calibration "
                         "force are shown as 'extrapolated' (SW-CAL-008)")
        if cal.load_status is not None and not cal.load_valid_for_limits:
            warns.append("calibration invalid for load limits (AFE configuration mismatch, SW-CAL-009) — forces "
                         "marked invalid; motion disabled while PC load limits are enabled")
        self._set(self.load_warn, "\n".join(warns))
        self.load_warn.setVisible(bool(warns))
        try:
            expected = float(self._backend.session.get().expected_spm)
        except Exception:  # noqa: BLE001
            expected = 800.0
        board = cal.board_spm
        same = board is not None and cal.travel_spm is not None and not cal.travel_cal_differs   # backend decides
        ttxt = (f"active {fmt_value(cal.travel_spm, 'mm')} steps/mm" + (f" (date {cal.travel_date})"
                                                                        if cal.travel_date else "")
                + f"\nboard  ● {fmt_value(board, 'mm')}" + ("  (= active)" if same else "")
                + f"\nexpected {fmt_value(expected, 'mm')} (4000 p/rev closed loop, D-27)")
        self._set(self.travel_label, ttxt)

    @staticmethod
    def _set(label: QLabel, text: str) -> None:
        if label.text() != text:
            label.setText(text)
