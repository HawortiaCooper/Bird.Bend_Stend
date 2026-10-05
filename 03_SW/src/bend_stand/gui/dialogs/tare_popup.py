"""``TarePopup``: non-modal tare progress (SW_design_GUI §6.4, B §9.5), started by the toolbar TARE (every tab)
or the Calibration-tab TARE.

* :meth:`start` → ``backend.tare(window_s) → GateResult``: REFUSE items → state REFUSED with the texts
  **verbatim** (moving or < 1 s after a move, latch, sequence capture window, not connected, AFE synthetic …).
* Otherwise the popup follows ``tare_engine.state()`` (``EngineState``, phases CHECK, CAPTURE, EVALUATE, DONE +
  REFUSED, ABORTED): progress over the window, live stats (mean raw, std, drift, N, outliers, lost), the stream
  note; refused at the end (``errors``) → [Repeat] / [Close]; ABORTED (STOP / HALT / PAUSE) → reason + [Close];
  DONE → result + thresholds re-check (THR chip), auto-close after 5 s without warnings; DONE with a warning
  (large offset) → [Keep] / **[Undo tare]** → ``tare_engine.undo() → GateResult`` (A-16, GQ-15).
* [Cancel] while capturing → ``tare_engine.cancel()``. STOP in the top bar (SafeDialog). Never blocks the GUI.

Implements: SW-TARE-001 (non-modal progress, verbatim refusal reasons), SW-TARE-002 (result, thresholds re-sent
— backend), SW-TARE-003 (refusal / warning states shown), SW-STOP-001 (STOP in the popup)
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from bend_stand.gui import gating
from bend_stand.gui.dialogs.safe_dialog import SafeDialog
from bend_stand.gui.wizards import phase_views as pv

log = logging.getLogger(__name__)

AUTO_CLOSE_MS = 5000
RUNNING = ("CHECK", "CAPTURE", "EVALUATE")
#: TareResult fields shown in the DONE line (B5-07), display only
RESULT_KEYS = ("tare_raw", "std", "drift", "n_used", "n_rejected", "n_lost", "window_s")


class TarePopup(SafeDialog):
    message = Signal(str, str)

    def __init__(self, backend: Any, bridge: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="TARE")
        self.setObjectName("tarePopup")
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setModal(False)
        self.stop_button._source = "tare"                     # noqa: SLF001
        self._backend = backend
        self.state_name = "IDLE"              # IDLE | REFUSED | <engine phase>
        self.refusal = ""
        self.engine_state: Any = None
        self._window_s: float | None = None

        lay = QVBoxLayout()
        self.phase_label = QLabel("", self)
        self.phase_label.setObjectName("tarePhase")
        f = self.phase_label.font()
        f.setBold(True)
        self.phase_label.setFont(f)
        self.phase_label.setWordWrap(True)
        lay.addWidget(self.phase_label)
        self.progress = QProgressBar(self)
        self.progress.setObjectName("tareProgress")
        self.progress.setRange(0, 1000)
        lay.addWidget(self.progress)
        self.stats_label = QLabel("", self)
        self.stats_label.setObjectName("tareStats")
        self.stats_label.setWordWrap(True)
        lay.addWidget(self.stats_label)
        self.info_label = QLabel("", self)
        self.info_label.setObjectName("tareInfo")
        self.info_label.setWordWrap(True)
        self.info_label.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.info_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_button = self._button("Cancel", "tareCancel", self.on_cancel)
        self.repeat_button = self._button("Repeat", "tareRepeat", self.on_repeat)
        self.undo_button = self._button("Undo tare", "tareUndo", self.on_undo)
        self.keep_button = self._button("Keep", "tareKeep", self.close)
        self.close_button = self._button("Close", "tareClose", self.close)
        for b in (self.cancel_button, self.repeat_button, self.undo_button, self.keep_button, self.close_button):
            row.addWidget(b)
        lay.addLayout(row)
        self.setLayout(lay)
        self._auto = QTimer(self)
        self._auto.setSingleShot(True)
        self._auto.timeout.connect(self.close)
        bridge.engineState.connect(self._on_engine_event)
        self.resize(460, 200)
        self._render()

    def _button(self, text: str, name: str, slot: Any) -> QPushButton:
        b = QPushButton(text, self)
        b.setObjectName(name)
        b.setAutoDefault(False)
        b.setDefault(False)
        b.clicked.connect(slot)
        return b

    # ------------------------------------------------------------------ actions
    def start(self, window_s: float | None = None) -> Any:
        self._window_s = window_s
        self._auto.stop()
        try:
            g = self._backend.tare(window_s)
        except Exception as exc:  # noqa: BLE001 - never raises by contract
            self._refused(str(exc))
            return None
        if not g.ok:
            self._refused(gating.refusal_text(g))
        else:
            self.state_name = "CHECK"
            self.refusal = ""
            self.refresh()
        return g

    def _refused(self, text: str) -> None:
        self.state_name = "REFUSED"
        self.refusal = text
        self._render()

    def on_cancel(self) -> None:
        try:
            self._backend.tare_engine.cancel()
        except Exception as exc:  # noqa: BLE001
            log.warning("tare cancel failed: %s", exc)
        self.refresh()

    def on_repeat(self) -> None:
        self.start(self._window_s)

    def on_undo(self) -> None:
        try:
            g = self._backend.tare_engine.undo()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Undo tare failed: {exc}", "error")
            return
        if g.ok:
            self.message.emit("Tare undone (previous tare state restored)", "info")
            self.close()
        else:
            self.message.emit(f"Undo tare refused: {gating.refusal_text(g)}", "warn")

    # ------------------------------------------------------------------ rendering
    def _on_engine_event(self, record: Any) -> None:
        if record.topic == "tare.state":
            self.refresh()

    def refresh(self) -> None:
        if self.state_name in ("IDLE", "REFUSED"):
            self._render()
            return
        try:
            st = self._backend.tare_engine.state()
        except Exception:  # noqa: BLE001
            return
        self.engine_state = st
        if st.phase and st.phase not in pv.IDLE_PHASES:
            self.state_name = st.phase
        self._render()

    def _render(self) -> None:
        st = self.engine_state
        name = self.state_name
        running = name in RUNNING and not (name == "EVALUATE" and st is not None and st.errors)
        if name == "REFUSED":
            reason = self.refusal or (" ".join(st.errors) if st is not None else "")
            self.phase_label.setText("Tare refused")
            self.info_label.setText(f"<span style='color:#a00000'>✗ {reason}</span>")
        elif name == "IDLE":
            self.phase_label.setText("Tare")
            self.info_label.setText("")
        else:
            title = (st.title if st is not None and st.title else name)
            self.phase_label.setText(f"Taring… {title}" if running else title)
            lines = []
            if st is not None:
                if st.instruction:
                    lines.append(st.instruction)
                if name == "DONE" and st.result is not None:
                    summary, _h, _r = pv.result_parts(st.result)
                    keep = [(k, v) for k, v in summary if k in RESULT_KEYS] or summary[:6]
                    lines.append("   ".join(f"{k} {v}" for k, v in keep))
                lines += [f"<span style='color:#a00000'>✗ {e}</span>" for e in st.errors]
                lines += [f"<span style='color:#805000'>⚠ {w}</span>" for w in st.warnings]
                if st.abort_reason:
                    lines.append(f"<span style='color:#a00000'>Aborted: {st.abort_reason}</span>")
            self.info_label.setText("<br>".join(lines))
        prog = st.progress if st is not None and name not in ("REFUSED", "IDLE") else None
        self.progress.setVisible(prog is not None)
        if prog is not None:
            self.progress.setValue(int(round(max(0.0, min(1.0, float(prog))) * 1000)))
        stats = st.stats if st is not None and name not in ("REFUSED", "IDLE") else None
        self.stats_label.setText("   ".join(f"{k} {pv.fmt_any(v)}" for k, v in (stats or {}).items()))
        self.stats_label.setVisible(bool(stats))
        evaluate_failed = name == "EVALUATE" and st is not None and bool(st.errors)
        done = name == "DONE"
        warned = done and st is not None and bool(st.warnings)
        can_undo = bool(getattr(getattr(self._backend.status(), "tare", None), "can_undo", False))
        self.cancel_button.setVisible(running)
        self.repeat_button.setVisible(evaluate_failed or name == "REFUSED")
        self.undo_button.setVisible(warned)
        self.undo_button.setEnabled(can_undo)
        self.keep_button.setVisible(warned)
        self.close_button.setVisible(not running and not warned)
        if done and not warned and not self._auto.isActive():
            self._auto.start(AUTO_CLOSE_MS)
