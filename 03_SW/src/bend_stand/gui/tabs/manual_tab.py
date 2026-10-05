"""Manual tab (SW_design_GUI §3.4) on the real ``MotionController`` (B §5.4, B4-01).

* **Position**: test / machine travel, commanded and pending target (latest wins, D-29 i), force (N / kgf, state
  incl. EXTRAPOLATED), raw, motion state, owner, last result (``motion.done`` / ticket outcome).
* **Axis**: driver enable checkbox bound to the FW ENABLED indicator (P4; ENABLING countdown), DISABLE via C-02,
  HOME (C-01 when the ``home`` gate has a CONFIRM item → ``home(load_confirmed=True)``), set / reset test zero,
  VALID toggle (FW state shown).
* **Target slider** (one ``move_to`` on release), **Go to** absolute (machine mm) / distance (``move_by``), step
  buttons −10 … +10 mm (``move_by``, the backend accumulates on the commanded target), **hold-to-jog** (``jog_start``
  on press, ``jog_stop`` on release / focus loss / hide / gate close), jog speed.
* **Motion parameters**: speed / acceleration fields checked with ``motion.check`` on every edit (above the caps →
  red, not applied); the caps label shows travel / loaded / step-rate / un-homed caps; SAF-SW-006 and no-specimen
  WARN items shown verbatim.
* PAUSED line with [Resume] while the ``move`` gate carries the PAUSED item; STOP (large) on the tab.

No target arithmetic, no limits, no rules in the GUI (P1): every enable state comes from ``status().gates``; the
backend refuses with ``GateRefused`` / ``ConfirmationRequired`` (shown verbatim).

Implements: SW-MAN-001, SW-MAN-002, SW-MAN-003, SW-MAN-004, SW-MAN-005, SW-MAN-006, SAF-SW-004 (C-01, C-02),
SAF-SW-006 (margin WARN shown), SW-STOP-001 (STOP on the tab), SW-STOP-004 (PAUSED line + Resume), SW-CAL-008
(EXTRAPOLATED force state)
"""
from __future__ import annotations

import dataclasses
import logging
import math
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from bend_stand.core.api import ConfirmationRequired, GateId, MotionKind
from bend_stand.gui import gating
from bend_stand.gui.dialogs.confirm_dialog import make_confirm
from bend_stand.gui.format import NA, fmt_value
from bend_stand.gui.units_state import FORCE_KEY, force_unit
from bend_stand.gui.widgets.hold_button import HoldButton
from bend_stand.gui.widgets.stop_button import StopButton
from bend_stand.gui.widgets.target_slider import TargetSlider

log = logging.getLogger(__name__)

STEP_SIZES_MM = (-10.0, -1.0, -0.1, 0.1, 1.0, 10.0)
FIELD_STYLE = {"ok": "", "warn": "background-color: #fff0c0;", "error": "background-color: #ffc8c8;"}
INFO_STYLE = {"info": "color: #606060;", "ok": "color: #206020;", "warn": "color: #805000;",
              "error": "color: #a00000; font-weight: bold;"}


def _err_text(exc: BaseException) -> str:
    gate = getattr(exc, "gate", None) or getattr(exc, "result", None)
    if gate is not None and hasattr(gate, "items"):
        txt = gating.refusal_text(gate) or "; ".join(i.text for i in gate.items)
        if txt:
            return txt
    return str(getattr(exc, "user_text", "") or exc or type(exc).__name__)


def _gate_from_exc(exc: BaseException) -> Any:
    for attr in ("gate", "result", "gate_result"):
        g = getattr(exc, attr, None)
        if g is not None and hasattr(g, "items"):
            return g
    args = getattr(exc, "args", ())
    if args and hasattr(args[0], "items"):
        return args[0]
    return None


def outcome_text(outcome: Any) -> tuple[str, str]:
    """(text, level) of a resolved ``MoveTicket`` (display mapping only, B3-04)."""
    kind = str(getattr(outcome, "kind", ""))
    done = getattr(outcome, "done", None)
    if kind == "DONE" and done is not None:
        return done_text(done), "ok" if done.reason == "TARGET" else "warn"
    if kind == "REFUSED_PAUSED":
        return "move not started: PAUSED (Resume clears PAUSE)", "info"
    if kind == "CANCELLED":
        return f"move cancelled: {getattr(outcome, 'text', '')}".rstrip(": "), "info"
    if kind == "NOT_EXECUTED":
        return f"move not executed: {getattr(outcome, 'text', '')}".rstrip(": "), "warn"
    return f"move refused: {getattr(outcome, 'text', '') or kind}", "error"


def _has_travel_range(st: Any) -> bool:
    """The slider needs a known travel range (soft limits read from the board)."""
    lim = getattr(getattr(st, "motion", None), "limits", None)
    return lim is not None and lim.travel_min_mm is not None and lim.travel_max_mm is not None


def done_text(done: Any) -> str:
    txt = f"last: MOVE_DONE {done.reason} at {fmt_value(done.pos_mm, 'mm')} mm"
    if getattr(done, "stop_cause", None):
        txt += f" (STOPPED: {done.stop_cause})"
    return txt


class ManualTab(QWidget):
    message = Signal(str, str)              # toast (text, severity)
    resumeRequested = Signal()

    def __init__(self, backend: Any, bridge: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("manualTab")
        self._backend = backend
        self._bridge = bridge
        self._status: Any = None
        self._n = 0
        self._saw_jogging = False
        self.confirm_dialog: Any = None
        self._fields_ok = {"speed": True, "accel": True, "jog": True, "goto": True}

        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        outer.addLayout(top)
        top.addWidget(self._build_position(), 1)
        top.addWidget(self._build_axis(), 1)

        self.paused_row = QWidget(self)
        pr = QHBoxLayout(self.paused_row)
        pr.setContentsMargins(4, 0, 4, 0)
        self.paused_label = QLabel("", self.paused_row)
        self.paused_label.setStyleSheet("color: #805000; font-weight: bold;")
        self.resume_button = QPushButton("Resume", self.paused_row)
        self.resume_button.setObjectName("manualResume")
        self.resume_button.setAutoDefault(False)
        self.resume_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.resume_button.clicked.connect(self.resumeRequested)
        pr.addWidget(self.paused_label, 1)
        pr.addWidget(self.resume_button)
        self.paused_row.hide()
        outer.addWidget(self.paused_row)

        self.gate_line = QLabel("", self)
        self.gate_line.setObjectName("manualGateLine")
        self.gate_line.setWordWrap(True)
        self.gate_line.setStyleSheet(INFO_STYLE["warn"])
        outer.addWidget(self.gate_line)

        outer.addWidget(self._build_slider())
        outer.addWidget(self._build_goto())
        outer.addWidget(self._build_params())

        stop_row = QHBoxLayout()
        self.stop_button = StopButton(self, large=True, source="manual")
        stop_row.addWidget(self.stop_button)
        stop_row.addWidget(QLabel("Keyboard: Pause/Break = HALT (system-wide)", self))
        stop_row.addStretch(1)
        outer.addLayout(stop_row)
        outer.addStretch(1)

        self.gates = gating.GateBinder()
        self.gates.bind(self.slider, GateId.MOVE, extra=_has_travel_range)
        for w in (self.go_button, *self.step_buttons):
            self.gates.bind(w, GateId.MOVE)
        for w in (self.jog_rear, self.jog_fwd):
            self.gates.bind(w, GateId.JOG)
        self.gates.bind(self.home_button, GateId.HOME, base_tooltip="Home at the START switch (D-29 b)")
        self.gates.bind(self.zero_button, GateId.TEST_ZERO, base_tooltip="Test travel zero := commanded position")
        for w in self.valid_buttons:
            self.gates.bind(w, GateId.VALID_TOGGLE, base_tooltip="Manual VALID flag (PO-FW-7)")

        bridge.motionEvent.connect(self._on_motion_event)
        force_unit().changed.connect(self._on_unit_changed)        # bound method: disconnected with the tab
        self._load_session_defaults()

    # ================================================================== construction
    def _build_position(self) -> QGroupBox:
        box = QGroupBox("Position", self)
        g = QGridLayout(box)
        self.pos_labels: dict[str, QLabel] = {}
        rows = (("test", "Travel (test)"), ("machine", "Travel (machine)"), ("target", "Commanded target"),
                ("pending", "Pending target"), ("force", "Force"), ("raw", "Raw"), ("state", "Motion state"))
        for r, (key, text) in enumerate(rows):
            g.addWidget(QLabel(text, box), r, 0)
            lab = QLabel(NA, box)
            lab.setObjectName(f"pos_{key}")
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if key in ("test", "force"):
                f = lab.font()
                f.setPointSize(f.pointSize() + 3)
                f.setBold(True)
                lab.setFont(f)
            g.addWidget(lab, r, 1)
            self.pos_labels[key] = lab
        self.last_line = QLabel("", box)
        self.last_line.setObjectName("lastResult")
        self.last_line.setWordWrap(True)
        g.addWidget(self.last_line, len(rows), 0, 1, 2)
        return box

    def _build_axis(self) -> QGroupBox:
        box = QGroupBox("Axis", self)
        g = QGridLayout(box)
        self.enable_box = QCheckBox("Driver enabled", box)
        self.enable_box.setObjectName("enableBox")
        self.enable_box.clicked.connect(self._on_enable_clicked)
        self.enable_state = QLabel("FW: ?", box)
        g.addWidget(self.enable_box, 0, 0)
        g.addWidget(self.enable_state, 0, 1, 1, 2)
        self.home_button = QPushButton("HOME", box)
        self.home_button.setObjectName("homeButton")
        self.home_button.setAutoDefault(False)
        self.home_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.home_button.clicked.connect(self.on_home)
        self.home_state = QLabel("? homed", box)
        g.addWidget(self.home_button, 1, 0)
        g.addWidget(self.home_state, 1, 1, 1, 2)
        self.zero_button = QPushButton("Set test zero here", box)
        self.zero_button.setObjectName("testZeroButton")
        self.zero_button.setAutoDefault(False)
        self.zero_button.clicked.connect(self.on_set_zero)
        self.zero_label = QLabel("x0 = 0.000 mm", box)
        self.zero_reset = QPushButton("Reset", box)
        self.zero_reset.setObjectName("testZeroReset")
        self.zero_reset.setAutoDefault(False)
        self.zero_reset.clicked.connect(self.on_reset_zero)
        g.addWidget(self.zero_button, 2, 0)
        g.addWidget(self.zero_label, 2, 1)
        g.addWidget(self.zero_reset, 2, 2)
        vrow = QHBoxLayout()
        self.valid_buttons = []
        for flag in (False, True):
            b = QPushButton("VALID 1" if flag else "VALID 0", box)
            b.setObjectName(f"valid{int(flag)}")
            b.setAutoDefault(False)
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, f=flag: self.on_valid(f))
            vrow.addWidget(b)
            self.valid_buttons.append(b)
        g.addLayout(vrow, 3, 0)
        self.valid_state = QLabel("FW: ?", box)
        g.addWidget(self.valid_state, 3, 1, 1, 2)
        return box

    def _build_slider(self) -> QGroupBox:
        box = QGroupBox("Target position (moves on release, machine mm)", self)
        lay = QHBoxLayout(box)
        self.slider_lo = QLabel(NA, box)
        self.slider = TargetSlider(box)
        self.slider_hi = QLabel(NA, box)
        self.preview_label = QLabel("", box)
        self.preview_label.setMinimumWidth(140)
        lay.addWidget(self.slider_lo)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.slider_hi)
        lay.addWidget(self.preview_label)
        self.slider.previewChanged.connect(lambda v: self.preview_label.setText(f"preview {fmt_value(v, 'mm')} mm"))
        self.slider.targetReleased.connect(self.on_slider_released)
        return box

    def _build_goto(self) -> QGroupBox:
        box = QGroupBox("Go to / steps / jog", self)
        g = QGridLayout(box)
        self.abs_radio = QRadioButton("absolute (machine mm)", box)
        self.dist_radio = QRadioButton("distance (± mm from the commanded target)", box)
        self.dist_radio.setChecked(True)
        grp = QButtonGroup(box)
        grp.addButton(self.abs_radio)
        grp.addButton(self.dist_radio)
        self._goto_group = grp
        self.goto_spin = QDoubleSpinBox(box)
        self.goto_spin.setObjectName("gotoSpin")
        self.goto_spin.setDecimals(3)
        self.goto_spin.setRange(-1000.0, 1000.0)
        self.goto_spin.setSuffix(" mm")
        self.goto_spin.setKeyboardTracking(False)
        self.goto_spin.valueChanged.connect(lambda _v: self._check_fields())
        self.go_button = QPushButton("Go", box)
        self.go_button.setObjectName("goButton")
        self.go_button.setAutoDefault(False)
        self.go_button.setDefault(False)
        self.go_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.go_button.clicked.connect(self.on_go)
        g.addWidget(self.abs_radio, 0, 0)
        g.addWidget(self.dist_radio, 0, 1)
        g.addWidget(self.goto_spin, 0, 2)
        g.addWidget(self.go_button, 0, 3)
        row = QHBoxLayout()
        row.addWidget(QLabel("Steps:", box))
        self.step_buttons: list[QPushButton] = []
        for d in STEP_SIZES_MM:
            b = QPushButton(f"{d:+g}", box)
            b.setObjectName(f"step{d:+g}")
            b.setAutoDefault(False)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _c=False, dd=d: self.on_step(dd))
            row.addWidget(b)
            self.step_buttons.append(b)
        row.addStretch(1)
        g.addLayout(row, 1, 0, 1, 4)
        jrow = QHBoxLayout()
        jrow.addWidget(QLabel("Jog (hold):", box))
        self.jog_rear = HoldButton("◀◀ rear (−)  hold", -1, box)
        self.jog_rear.setObjectName("jogRear")
        self.jog_fwd = HoldButton("hold  forward (+) ▶▶", +1, box)
        self.jog_fwd.setObjectName("jogForward")
        for b in (self.jog_rear, self.jog_fwd):
            b.jogStarted.connect(self.on_jog_start)
            b.jogStopped.connect(self.on_jog_stop)
            jrow.addWidget(b)
        jrow.addWidget(QLabel("jog speed", box))
        self.jog_spin = QDoubleSpinBox(box)
        self.jog_spin.setObjectName("jogSpeed")
        self.jog_spin.setDecimals(3)
        self.jog_spin.setRange(0.001, 1000.0)
        self.jog_spin.setValue(2.0)
        self.jog_spin.setSuffix(" mm/s")
        self.jog_spin.setKeyboardTracking(False)
        self.jog_spin.valueChanged.connect(lambda _v: self._check_fields())
        jrow.addWidget(self.jog_spin)
        jrow.addStretch(1)
        g.addLayout(jrow, 2, 0, 1, 4)
        g.addWidget(QLabel("(+) = away from the home (START) switch", box), 3, 0, 1, 4)
        return box

    def _build_params(self) -> QGroupBox:
        box = QGroupBox("Motion parameters", self)
        g = QGridLayout(box)
        self.speed_spin = QDoubleSpinBox(box)
        self.speed_spin.setObjectName("speedSpin")
        self.speed_spin.setDecimals(3)
        self.speed_spin.setRange(0.001, 1000.0)
        self.speed_spin.setSuffix(" mm/s")
        self.speed_spin.setKeyboardTracking(False)
        self.accel_spin = QDoubleSpinBox(box)
        self.accel_spin.setObjectName("accelSpin")
        self.accel_spin.setDecimals(1)
        self.accel_spin.setRange(0.0, 100000.0)
        self.accel_spin.setSpecialValueText("FW default")
        self.accel_spin.setSuffix(" mm/s²")
        self.accel_spin.setKeyboardTracking(False)
        self.speed_spin.valueChanged.connect(lambda _v: self._on_param_edit())
        self.accel_spin.valueChanged.connect(lambda _v: self._on_param_edit())
        g.addWidget(QLabel("speed", box), 0, 0)
        g.addWidget(self.speed_spin, 0, 1)
        self.caps_label = QLabel("caps: n/a", box)
        self.caps_label.setObjectName("capsLabel")
        g.addWidget(self.caps_label, 0, 2)
        g.addWidget(QLabel("accel", box), 1, 0)
        g.addWidget(self.accel_spin, 1, 1)
        self.accel_cap_label = QLabel("", box)
        g.addWidget(self.accel_cap_label, 1, 2)
        self.param_msg = QLabel("", box)
        self.param_msg.setObjectName("paramMessages")
        self.param_msg.setWordWrap(True)
        g.addWidget(self.param_msg, 2, 0, 1, 3)
        return box

    def _load_session_defaults(self) -> None:
        try:
            s = self._backend.session.get()
        except Exception:  # noqa: BLE001
            return
        for spin, v in ((self.speed_spin, getattr(s, "manual_speed_mm_s", 5.0)),
                        (self.accel_spin, getattr(s, "manual_accel_mm_s2", None) or 0.0)):
            spin.blockSignals(True)
            spin.setValue(float(v))
            spin.blockSignals(False)

    # ================================================================== field checks (motion.check, A-10)
    def speed(self) -> float:
        return float(self.speed_spin.value())

    def accel(self) -> float | None:
        a = float(self.accel_spin.value())
        return None if a <= 0 else a

    def _check(self, kind: MotionKind, **kw: Any) -> Any:
        try:
            return self._backend.motion.check(kind, **kw)
        except Exception as exc:  # noqa: BLE001 - presentation only
            log.debug("motion.check failed: %s", exc)
            return None

    @staticmethod
    def _field_items(gate: Any, codes: tuple[str, ...]) -> list[Any]:
        if gate is None:
            return []
        return [i for i in gate.items if i.code in codes]

    def _check_fields(self) -> None:
        """Colour the speed / accel / jog / go-to fields from ``motion.check`` (no rules in the GUI)."""
        g = self._check(MotionKind.MOVE, speed_mm_s=self.speed(), accel_mm_s2=self.accel())
        sp = self._field_items(g, ("SPEED_CAP",))
        ac = self._field_items(g, ("ACCEL_CAP",))
        self._fields_ok["speed"] = not any(i.severity == "REFUSE" for i in sp)
        self._fields_ok["accel"] = not any(i.severity == "REFUSE" for i in ac)
        self.speed_spin.setStyleSheet(FIELD_STYLE["ok" if self._fields_ok["speed"] else "error"])
        self.accel_spin.setStyleSheet(FIELD_STYLE["ok" if self._fields_ok["accel"] else "error"])
        jg = self._check(MotionKind.JOG, speed_mm_s=float(self.jog_spin.value()))
        jsp = self._field_items(jg, ("SPEED_CAP",))
        self._fields_ok["jog"] = not any(i.severity == "REFUSE" for i in jsp)
        self.jog_spin.setStyleSheet(FIELD_STYLE["ok" if self._fields_ok["jog"] else "error"])
        msgs = [i.text for i in sp + ac + jsp]
        warns = [i.text for i in (g.items if g is not None else ()) if i.severity == "WARN"]
        if self.abs_radio.isChecked():
            tg = self._check(MotionKind.MOVE, target_mm=float(self.goto_spin.value()))
            ti = self._field_items(tg, ("TARGET_OUT_OF_RANGE", "LIMIT"))
            self._fields_ok["goto"] = not ti
            msgs += [i.text for i in ti]
        else:
            self._fields_ok["goto"] = True
        self.goto_spin.setStyleSheet(FIELD_STYLE["ok" if self._fields_ok["goto"] else "error"])
        lines = [f"✗ {t}" for t in msgs] + [f"⚠ {t}" for t in dict.fromkeys(warns)]
        text = "\n".join(lines)
        if self.param_msg.text() != text:
            self.param_msg.setText(text)
            self.param_msg.setStyleSheet(INFO_STYLE["error" if msgs else "warn"])

    def _on_param_edit(self) -> None:
        self._check_fields()
        if self._fields_ok["speed"] and self._fields_ok["accel"]:
            try:
                s = self._backend.session.get()
                self._backend.session.set(dataclasses.replace(s, manual_speed_mm_s=self.speed(),
                                                              manual_accel_mm_s2=self.accel()))
            except Exception:  # noqa: BLE001 - persistence is a convenience
                log.debug("session.set failed", exc_info=True)

    def fields_valid(self, *names: str) -> bool:
        return all(self._fields_ok[n] for n in names)

    # ================================================================== status (every refresh tick)
    def update_status(self, st: Any) -> None:
        self._status = st
        self._n += 1
        self.gates.update(st)
        m = st.motion
        ind = st.indicators
        lim = m.limits
        # slider range / handle / live marker
        if lim is not None and lim.travel_min_mm is not None and lim.travel_max_mm is not None:
            self.slider.set_range_mm(lim.travel_min_mm, lim.travel_max_mm)
            self.slider_lo.setText(fmt_value(lim.travel_min_mm, "mm"))
            self.slider_hi.setText(fmt_value(lim.travel_max_mm, "mm") + " mm")
        self.slider.show_target_mm(m.pending_target_mm if m.pending_target_mm is not None else
                                   m.commanded_target_mm)
        self.slider.set_live_mm(m.position_mm)
        # enable checkbox bound to the FW state (P4)
        ena = getattr(ind.get("enabled"), "state", "UNKNOWN")
        checked = ena == "ON"
        if self.enable_box.isChecked() != checked:
            self.enable_box.setChecked(checked)
        gid = GateId.DISABLE if checked else GateId.ENABLE
        cs = gating.control_state(gating.gate_of(st, gid), motion=True,
                                  base_tooltip="Disable the driver (C-02)" if checked else "Enable the driver")
        self.enable_box.setEnabled(cs.enabled)
        self.enable_box.setToolTip(cs.tooltip)
        left = int(m.enabling_left_ms or 0)
        ena_text = {"ON": "ENABLED", "OFF": "disabled"}.get(ena, "?")
        if left > 0:
            ena_text = f"ENABLING {left} ms (driver settling)"
        self._set(self.enable_state, f"FW: ● {ena_text}")
        homed = getattr(ind.get("homed"), "state", "UNKNOWN")
        htxt = {"ON": "● HOMED", "OFF": "● NOT homed"}.get(homed, "● ? homed")
        if m.pos_uncertain:
            htxt += " (±1 step)"
        if m.home_phase and m.home_phase not in ("NONE", "DONE"):
            htxt += f"  (homing: {m.home_phase})"
        self._set(self.home_state, htxt)
        self._set(self.zero_label, f"x0 = {fmt_value(m.x_zero_mm, 'mm')} mm")
        valid = getattr(ind.get("valid"), "state", "UNKNOWN")
        self._set(self.valid_state, f"FW: ● {'1' if valid == 'ON' else '0' if valid == 'OFF' else '?'}")
        for b, flag in zip(self.valid_buttons, ("OFF", "ON"), strict=True):
            if b.isChecked() != (valid == flag):
                b.setChecked(valid == flag)
        # PAUSED line / first REFUSE of the move gate
        move = gating.gate_of(st, GateId.MOVE)
        paused = [i for i in (move.refused if move is not None else ()) if i.code == "PAUSED"]
        if paused:
            self._set(self.paused_label, "(!) " + paused[0].text + (f" — {paused[0].clear_hint}"
                                                                   if paused[0].clear_hint else ""))
            rg = gating.gate_of(st, GateId.RESUME)
            self.resume_button.setEnabled(rg is not None and rg.ok)
            self.resume_button.setToolTip(gating.control_state(rg, motion=True).tooltip)
        self.paused_row.setVisible(bool(paused))
        line = ""
        if move is not None and move.refused and not paused:
            line = "Motion refused: " + "; ".join(i.text for i in move.refused)
        elif move is not None and move.warnings:
            line = "⚠ " + "; ".join(i.text for i in move.warnings)
        self._set(self.gate_line, line)
        self.gate_line.setVisible(bool(line))
        # hold-to-jog: the backend ended the session (STOP / PAUSE / latch) → released state, new press needed
        held = self.jog_rear.held or self.jog_fwd.held
        if held and m.jogging:
            self._saw_jogging = True
        if held and self._saw_jogging and not m.jogging:
            for b in (self.jog_rear, self.jog_fwd):
                b.force_release("backend")
        if not held:
            self._saw_jogging = False
        if self._n % 3 == 1:
            self._update_caps(lim)
            self._check_fields()
        self._update_live(st)

    def _update_caps(self, lim: Any) -> None:
        if lim is None:
            self._set(self.caps_label, "caps: n/a (not connected)")
            self._set(self.accel_cap_label, "")
            return
        txt = (f"cap now {fmt_value(lim.v_cap_mm_s, 'mm/s')}: travel {fmt_value(lim.v_travel_mm_s, 'mm/s')} / "
               f"loaded {fmt_value(lim.v_load_mm_s, 'mm/s')} / step rate {fmt_value(lim.v_step_rate_mm_s, 'mm/s')}"
               f" / un-homed jog {fmt_value(lim.v_unhomed_mm_s, 'mm/s')} mm/s" + ("  (loaded)" if lim.loaded else ""))
        self._set(self.caps_label, txt)
        self._set(self.accel_cap_label, f"max {fmt_value(lim.a_max_mm_s2, 'N/s')} mm/s²")

    def _on_unit_changed(self, _unit: str) -> None:
        self._update_live(self._status, force=True)

    def _update_live(self, st: Any, force: bool = False) -> None:
        if st is None:
            return
        m = st.motion
        p = self.pos_labels
        self._set(p["test"], f"{fmt_value(m.test_position_mm, 'mm')} mm")
        self._set(p["machine"], f"{fmt_value(m.position_mm, 'mm')} mm")
        self._set(p["target"], f"{fmt_value(m.commanded_target_mm, 'mm')} mm")
        self._set(p["pending"], f"{fmt_value(m.pending_target_mm, 'mm')} mm" if m.pending_target_mm is not None
                  else "–")
        self._set(p["state"], f"{m.motion_state or NA}  owner {m.owner}" + ("  jogging" if m.jogging else ""))
        if not (force or self._n % 3 == 1):
            return
        unit = force_unit().unit
        self._set(p["force"], self._latest_text(FORCE_KEY[unit], unit))
        self._set(p["raw"], self._latest_text("raw", "counts"))

    def _latest_text(self, key: str, unit: str) -> str:
        try:
            s = self._backend.data.latest(key)
        except Exception:  # noqa: BLE001 - not connected / channel not available
            return f"{NA} {unit}"
        v = float(s.value)
        state = str(s.state)
        txt = f"{fmt_value(v if math.isfinite(v) else None, unit)} {unit}"
        if state not in ("OK",):
            txt += f"  [{state}]"
        return txt

    @staticmethod
    def _set(label: QLabel, text: str) -> None:
        if label.text() != text:
            label.setText(text)

    # ================================================================== motion actions
    def _watch_ticket(self, fut: Any, what: str) -> None:
        self._bridge.watch(fut, self._on_outcome, lambda e: self._on_ticket_error(e, what), f"motion.{what}")

    def _on_outcome(self, outcome: Any) -> None:
        text, level = outcome_text(outcome)
        self._show_last(text, level)
        if level == "error":
            self.message.emit(text, "error")

    def _on_ticket_error(self, exc: BaseException, what: str) -> None:
        if isinstance(exc, ConfirmationRequired) and what == "home":
            self._confirm_home(_gate_from_exc(exc))
            return
        text = f"{what} refused: {_err_text(exc)}"
        self._show_last(text, "error")
        self.message.emit(text, "warn")
        if what == "move_to" and self._status is not None:
            m = self._status.motion
            self.slider.show_target_mm(m.pending_target_mm if m.pending_target_mm is not None
                                       else m.commanded_target_mm)

    def _show_last(self, text: str, level: str) -> None:
        self.last_line.setText(text)
        self.last_line.setStyleSheet(INFO_STYLE.get(level, ""))

    def _params_or_refuse(self) -> tuple[float, float | None] | None:
        if not self.fields_valid("speed", "accel"):
            self.message.emit("Speed / acceleration above the caps — not applied (SW-MAN-005)", "warn")
            return None
        return self.speed(), self.accel()

    def on_slider_released(self, value_mm: float) -> None:
        """Exactly one ``move_to`` per release (SW-MAN-001)."""
        self.preview_label.setText("")
        p = self._params_or_refuse()
        if p is None:
            self.slider.show_target_mm(None)
            return
        try:
            fut = self._backend.motion.move_to(value_mm, speed_mm_s=p[0], accel_mm_s2=p[1])
        except Exception as exc:  # noqa: BLE001 - PreconditionError / ValueError
            self._on_ticket_error(exc, "move_to")
            return
        self._watch_ticket(fut, "move_to")

    def on_go(self) -> None:
        p = self._params_or_refuse()
        if p is None:
            return
        v = float(self.goto_spin.value())
        try:
            if self.abs_radio.isChecked():
                fut = self._backend.motion.move_to(v, speed_mm_s=p[0], accel_mm_s2=p[1])
            else:
                fut = self._backend.motion.move_by(v, speed_mm_s=p[0], accel_mm_s2=p[1])
        except Exception as exc:  # noqa: BLE001
            self._on_ticket_error(exc, "go to")
            return
        self._watch_ticket(fut, "go to")

    def on_step(self, delta_mm: float) -> None:
        """±0.1 / 1 / 10 mm → ``move_by`` (the backend accumulates on the commanded target, latest wins)."""
        p = self._params_or_refuse()
        if p is None:
            return
        try:
            fut = self._backend.motion.move_by(delta_mm, speed_mm_s=p[0], accel_mm_s2=p[1])
        except Exception as exc:  # noqa: BLE001
            self._on_ticket_error(exc, "step")
            return
        self._watch_ticket(fut, "step")

    def on_jog_start(self, direction: int) -> None:
        if not self.fields_valid("jog"):
            self.message.emit("Jog speed above the cap — not applied (SW-MAN-005)", "warn")
            for b in (self.jog_rear, self.jog_fwd):
                b.force_release("speed")
            return
        self._saw_jogging = False
        try:
            self._backend.motion.jog_start(direction, float(self.jog_spin.value()))
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"jog refused: {_err_text(exc)}", "warn")

    def on_jog_stop(self, _reason: str = "") -> None:
        try:
            self._backend.motion.jog_stop()
        except Exception as exc:  # noqa: BLE001 - never raises by contract
            log.warning("jog_stop failed: %s", exc)

    # ================================================================== axis actions
    def _on_enable_clicked(self) -> None:
        st = self._status
        fw_on = st is not None and getattr(st.indicators.get("enabled"), "state", "") == "ON"
        self.enable_box.setChecked(fw_on)                 # P4: the box shows the FW state only
        if fw_on:
            self.on_disable()
        else:
            self.on_enable()

    def on_enable(self) -> None:
        g = self._backend.motion.enable()
        if not g.ok:
            self.message.emit(f"ENABLE refused: {gating.refusal_text(g)}", "warn")

    def on_disable(self, confirmed: bool = False) -> None:
        g = self._backend.motion.disable(confirmed=confirmed)
        if g.ok:
            return
        codes = set(g.codes())
        if not confirmed and ("CONFIRMATION_REQUIRED" in codes or g.confirm_items):
            item = next(iter(g.confirm_items), None)
            dlg = make_confirm(self, "C-02", text=item.text if item is not None else None,
                               gate_provider=lambda: gating.gate_of(self._backend.status(), GateId.DISABLE),
                               confirm_code=item.code if item is not None else None)
            dlg.confirmed.connect(lambda: self.on_disable(confirmed=True))
            self.confirm_dialog = dlg
            dlg.open()
            return
        self.message.emit(f"DISABLE refused: {gating.refusal_text(g)}", "warn")

    def on_home(self) -> None:
        gate = gating.gate_of(self._status, GateId.HOME) if self._status is not None else None
        if gate is not None and gate.ok and gate.confirm_items:
            self._confirm_home(gate)
            return
        self._home(False)

    def _home(self, load_confirmed: bool) -> None:
        try:
            fut = self._backend.motion.home(load_confirmed=load_confirmed)
        except Exception as exc:  # noqa: BLE001
            self._on_ticket_error(exc, "home")
            return
        self._watch_ticket(fut, "home")

    def _confirm_home(self, gate: Any) -> None:
        item = next(iter(getattr(gate, "confirm_items", ()) or ()), None)
        dlg = make_confirm(self, "C-01", text=item.text if item is not None else None,
                           gate_provider=lambda: gating.gate_of(self._backend.status(), GateId.HOME),
                           confirm_code=item.code if item is not None else None,
                           live_text=self._live_force_text)
        dlg.confirmed.connect(lambda: self._home(True))
        self.confirm_dialog = dlg
        dlg.open()

    def _live_force_text(self) -> str:
        unit = force_unit().unit
        return f"Force now: {self._latest_text(FORCE_KEY[unit], unit)}"

    def on_set_zero(self) -> None:
        try:
            x0 = self._backend.motion.set_test_zero()
        except Exception as exc:  # noqa: BLE001 - GateRefused
            self.message.emit(f"Set test zero refused: {_err_text(exc)}", "warn")
            return
        self.message.emit(f"Test travel zero set at {fmt_value(x0, 'mm')} mm (machine)", "info")

    def on_reset_zero(self) -> None:
        try:
            self._backend.motion.reset_test_zero()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Reset test zero failed: {_err_text(exc)}", "warn")

    def on_valid(self, flag: bool) -> None:
        g = self._backend.motion.set_valid(flag)
        if not g.ok:
            self.message.emit(f"VALID {int(flag)} refused: {gating.refusal_text(g)}", "warn")
        if self._status is not None:                    # P4: the buttons show the FW state only
            self.update_status(self._status)

    # ================================================================== events
    def _on_motion_event(self, record: Any) -> None:
        p = record.payload
        if record.topic == "motion.done" and hasattr(p, "reason"):
            self._show_last(done_text(p), "ok" if p.reason == "TARGET" else "warn")
        elif record.topic == "motion.dropped":
            self._show_last(f"pending target dropped: {getattr(p, 'reason', p) if p is not None else ''}", "info")
