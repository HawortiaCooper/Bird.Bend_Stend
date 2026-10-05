"""Safety limits tab (SW_design_GUI §3.2).

* **Travel limits** (machine mm): min / max each with enable; the FW soft limits are shown read-only (from the
  board values; edited on the Config tab).
* **Load limits** (display unit N / kgf, View ▸ Units): pull max (> 0) and push max (< 0) each with enable, the
  warning level in % of the trip level, and the FW load-limit level (≤ 110 % FS, ≥ the SW trip). Helper labels
  ("= 100.0 % FS", "→ warning at …") are display conversions only.
* [Apply] → ``limits.set(LimitConfig)`` → ``list[Issue]`` shown next to each field (the backend checks the soft
  limits, the 110 % FS cap, FW level ≥ SW trip, moving…); empty list = applied. [Revert] re-reads ``limits.get()``.
* **FW thresholds** (SAF-SW-002): ``ThresholdState`` (state, raw min / max, zero_raw, ids, clamped + effective
  levels, text); [Re-send & verify] → ``limits.recheck_async()``; M2 bring-up paths **manual raw thresholds**
  (``set_manual_thresholds_async``) and **default thresholds** (``set_default_thresholds_async``) when the backend
  offers them (B4-04).
* Motion-disabled notice when the ``move`` gate carries the SAF-SW-001/002 REFUSE item.
* **No-specimen mode** (SW-LIM-004): state; [Enter no-specimen mode…] → gate ``no_specimen`` (REFUSE shown) → C-10
  (Enter / Space never confirm, assertion checkbox) → ``set_no_specimen_mode(True, confirmed=True)``; [Leave].
* Live: force (with % of the pull trip, display only) and travel.

Implements: SW-LIM-001, SW-LIM-002, SW-LIM-003 (limits applied in the backend / session), SW-LIM-004, SAF-SW-001
(motion-disabled notice), SAF-SW-002 (threshold display, re-send, manual / default), SAF-SW-004 (C-10)
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bend_stand.calc.units import FS_N, FW_LIMIT_MAX_N, kgf_to_n, n_to_kgf
from bend_stand.core.api import GateId, LimitConfig
from bend_stand.gui import gating
from bend_stand.gui.dialogs.confirm_dialog import make_confirm
from bend_stand.gui.format import NA, fmt_value
from bend_stand.gui.units_state import FORCE_KEY, force_unit

log = logging.getLogger(__name__)

RAW_RANGE = 8_388_607                   # i24 (HX711) — editor range only; the backend / FW check the values
ERR_STYLE = "background-color: #ffc8c8;"
MOTION_DISABLED_CODES = ("LOAD_INPUT_INVALID", "THRESHOLDS_UNVERIFIED")


def _err_text(exc: BaseException) -> str:
    gate = getattr(exc, "gate", None)
    if gate is not None and hasattr(gate, "items"):
        return gating.refusal_text(gate) or gate.text()
    return str(getattr(exc, "user_text", "") or exc or type(exc).__name__)


def pct_fs(f_n: float | None) -> str:
    return NA if f_n is None else f"{100.0 * f_n / FS_N:.1f} % FS"


class LimitsTab(QWidget):
    message = Signal(str, str)

    def __init__(self, backend: Any, bridge: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("limitsTab")
        self._backend = backend
        self._bridge = bridge
        self._status: Any = None
        self._n = 0
        self.confirm_dialog: Any = None
        self.issue_labels: dict[str, QLabel] = {}
        self.fields: dict[str, QWidget] = {}
        self.last_issues: list[Any] = []

        lay = QVBoxLayout(self)
        lay.addWidget(self._build_travel())
        lay.addWidget(self._build_load())
        lay.addWidget(self._build_thresholds())
        lay.addWidget(self._build_nospec())
        lay.addWidget(self._build_live())
        lay.addStretch(1)
        self.revert()
        force_unit().changed.connect(self._on_unit_changed)
        bridge.safetyEvent.connect(lambda _r: self._refresh_thresholds())

    # ================================================================== construction
    def _issue_label(self, key: str, parent: QWidget) -> QLabel:
        lab = QLabel("", parent)
        lab.setObjectName(f"issue_{key}")
        lab.setStyleSheet("color: #a00000;")
        lab.setWordWrap(True)
        self.issue_labels[key] = lab
        return lab

    def _mm_spin(self, parent: QWidget, name: str) -> QDoubleSpinBox:
        s = QDoubleSpinBox(parent)
        s.setObjectName(name)
        s.setDecimals(3)
        s.setRange(-10000.0, 10000.0)
        s.setSuffix(" mm")
        s.setKeyboardTracking(False)
        s.valueChanged.connect(self._update_helpers)
        return s

    def _force_spin(self, parent: QWidget, name: str, lo: float, hi: float) -> QDoubleSpinBox:
        s = QDoubleSpinBox(parent)
        s.setObjectName(name)
        s.setDecimals(2)
        s.setRange(lo, hi)
        s.setKeyboardTracking(False)
        s.valueChanged.connect(self._update_helpers)
        return s

    def _build_travel(self) -> QGroupBox:
        box = QGroupBox("Travel limits (machine coordinate)", self)
        g = QGridLayout(box)
        self.soft_label = QLabel("FW soft limits (board, read-only): n/a   — edit on the Config tab", box)
        self.soft_label.setObjectName("softLimits")
        g.addWidget(self.soft_label, 0, 0, 1, 4)
        self.min_en = QCheckBox("Min", box)
        self.min_en.setObjectName("travelMinEnabled")
        self.min_spin = self._mm_spin(box, "travelMin")
        self.max_en = QCheckBox("Max", box)
        self.max_en.setObjectName("travelMaxEnabled")
        self.max_spin = self._mm_spin(box, "travelMax")
        g.addWidget(self.min_en, 1, 0)
        g.addWidget(self.min_spin, 1, 1)
        g.addWidget(self.max_en, 1, 2)
        g.addWidget(self.max_spin, 1, 3)
        g.addWidget(self._issue_label("travel_min_mm", box), 2, 0, 1, 2)
        g.addWidget(self._issue_label("travel_max_mm", box), 2, 2, 1, 2)
        self.fields.update(travel_min_mm=self.min_spin, travel_max_mm=self.max_spin)
        return box

    def _build_load(self) -> QGroupBox:
        box = QGroupBox("Load limits (units per View ▸ Units)", self)
        g = QGridLayout(box)
        self.pull_en = QCheckBox("Pull max (> 0)", box)
        self.pull_en.setObjectName("pullEnabled")
        self.pull_spin = self._force_spin(box, "pullTrip", 0.0, 100000.0)
        self.push_en = QCheckBox("Push max (< 0)", box)
        self.push_en.setObjectName("pushEnabled")
        self.push_spin = self._force_spin(box, "pushTrip", -100000.0, 0.0)
        self.warn_spin = QSpinBox(box)
        self.warn_spin.setObjectName("warnPct")
        self.warn_spin.setRange(1, 100)
        self.warn_spin.setSuffix(" %")
        self.warn_spin.valueChanged.connect(self._update_helpers)
        self.fw_spin = self._force_spin(box, "fwLevel", 0.0, 100000.0)
        self.pull_help = QLabel("", box)
        self.push_help = QLabel("", box)
        self.fw_help = QLabel("", box)
        self.fw_help.setObjectName("fwHelp")
        g.addWidget(self.pull_en, 0, 0)
        g.addWidget(self.pull_spin, 0, 1)
        g.addWidget(self.pull_help, 0, 2)
        g.addWidget(self._issue_label("pull_trip_n", box), 1, 0, 1, 3)
        g.addWidget(self.push_en, 2, 0)
        g.addWidget(self.push_spin, 2, 1)
        g.addWidget(self.push_help, 2, 2)
        g.addWidget(self._issue_label("push_trip_n", box), 3, 0, 1, 3)
        g.addWidget(QLabel("warning at", box), 4, 0)
        g.addWidget(self.warn_spin, 4, 1)
        g.addWidget(self._issue_label("warn_pct", box), 4, 2)
        g.addWidget(QLabel("FW load-limit level", box), 5, 0)
        g.addWidget(self.fw_spin, 5, 1)
        g.addWidget(self.fw_help, 5, 2)
        g.addWidget(self._issue_label("fw_level_n", box), 6, 0, 1, 3)
        self.fields.update(pull_trip_n=self.pull_spin, push_trip_n=self.push_spin, warn_pct=self.warn_spin,
                           fw_level_n=self.fw_spin)
        row = QHBoxLayout()
        self.apply_button = QPushButton("Apply", box)
        self.apply_button.setObjectName("limitsApply")
        self.apply_button.setAutoDefault(False)
        self.apply_button.clicked.connect(self.apply)
        self.revert_button = QPushButton("Revert", box)
        self.revert_button.setObjectName("limitsRevert")
        self.revert_button.setAutoDefault(False)
        self.revert_button.clicked.connect(self.revert)
        self.apply_result = QLabel("", box)
        self.apply_result.setObjectName("applyResult")
        self.apply_result.setWordWrap(True)
        row.addWidget(self.apply_button)
        row.addWidget(self.revert_button)
        row.addWidget(self.apply_result, 1)
        g.addLayout(row, 7, 0, 1, 3)
        g.addWidget(self._issue_label("_general", box), 8, 0, 1, 3)
        g.addWidget(QLabel("Limits are saved with the session and recorded in the recording metadata.", box),
                    9, 0, 1, 3)
        return box

    def _build_thresholds(self) -> QGroupBox:
        box = QGroupBox("FW load thresholds (SAF-SW-002, board read-back)", self)
        g = QGridLayout(box)
        self.thr_label = QLabel("state UNVERIFIED", box)
        self.thr_label.setObjectName("thresholdState")
        self.thr_label.setWordWrap(True)
        g.addWidget(self.thr_label, 0, 0, 1, 4)
        self.clamped_label = QLabel("", box)
        self.clamped_label.setObjectName("clampedLine")
        self.clamped_label.setStyleSheet("color: #805000;")
        g.addWidget(self.clamped_label, 1, 0, 1, 4)
        self.recheck_button = QPushButton("Re-send && verify", box)
        self.recheck_button.setObjectName("recheckButton")
        self.recheck_button.setAutoDefault(False)
        self.recheck_button.clicked.connect(self.recheck)
        g.addWidget(self.recheck_button, 2, 0)
        self.default_thr_button = QPushButton("Use default thresholds", box)
        self.default_thr_button.setObjectName("defaultThresholds")
        self.default_thr_button.setAutoDefault(False)
        self.default_thr_button.clicked.connect(self.set_default_thresholds)
        g.addWidget(self.default_thr_button, 2, 1)
        g.addWidget(QLabel("Manual raw thresholds (bring-up): min", box), 3, 0)
        self.raw_min = QSpinBox(box)
        self.raw_min.setObjectName("rawMin")
        self.raw_max = QSpinBox(box)
        self.raw_max.setObjectName("rawMax")
        self.raw_zero = QSpinBox(box)
        self.raw_zero.setObjectName("rawZero")
        for s in (self.raw_min, self.raw_max, self.raw_zero):
            s.setRange(-RAW_RANGE, RAW_RANGE)
            s.setGroupSeparatorShown(True)
            s.setKeyboardTracking(False)
        self.raw_min.setValue(-7_022_271)
        self.raw_max.setValue(7_022_271)
        g.addWidget(self.raw_min, 3, 1)
        g.addWidget(QLabel("max", box), 3, 2)
        g.addWidget(self.raw_max, 3, 3)
        g.addWidget(QLabel("zero_raw", box), 4, 0)
        g.addWidget(self.raw_zero, 4, 1)
        self.manual_thr_button = QPushButton("Set manual thresholds", box)
        self.manual_thr_button.setObjectName("manualThresholds")
        self.manual_thr_button.setAutoDefault(False)
        self.manual_thr_button.clicked.connect(self.set_manual_thresholds)
        g.addWidget(self.manual_thr_button, 4, 2, 1, 2)
        has_manual = hasattr(self._backend.limits, "set_manual_thresholds_async")
        has_default = hasattr(self._backend.limits, "set_default_thresholds_async")
        self.manual_thr_button.setEnabled(has_manual)
        self.default_thr_button.setEnabled(has_default)
        self.motion_notice = QLabel("", box)
        self.motion_notice.setObjectName("motionDisabledNotice")
        self.motion_notice.setStyleSheet("color: #a00000; font-weight: bold;")
        self.motion_notice.setWordWrap(True)
        g.addWidget(self.motion_notice, 5, 0, 1, 4)
        return box

    def _build_nospec(self) -> QGroupBox:
        box = QGroupBox("No-specimen mode (SW-LIM-004: first use, travel / load calibration without a specimen)",
                        self)
        row = QHBoxLayout(box)
        self.nospec_state = QLabel("State: ● OFF", box)
        self.nospec_state.setObjectName("nospecState")
        self.nospec_enter = QPushButton("Enter no-specimen mode…", box)
        self.nospec_enter.setObjectName("nospecEnter")
        self.nospec_enter.setAutoDefault(False)
        self.nospec_enter.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nospec_enter.clicked.connect(self.enter_no_specimen)
        self.nospec_leave = QPushButton("Leave no-specimen mode", box)
        self.nospec_leave.setObjectName("nospecLeave")
        self.nospec_leave.setAutoDefault(False)
        self.nospec_leave.clicked.connect(self.leave_no_specimen)
        row.addWidget(self.nospec_state, 1)
        row.addWidget(self.nospec_enter)
        row.addWidget(self.nospec_leave)
        return box

    def _build_live(self) -> QGroupBox:
        box = QGroupBox("Live", self)
        row = QHBoxLayout(box)
        self.live_label = QLabel(NA, box)
        self.live_label.setObjectName("limitsLive")
        row.addWidget(self.live_label)
        return box

    # ================================================================== model ↔ fields
    def _unit(self) -> str:
        return force_unit().unit

    def _to_disp(self, f_n: float) -> float:
        v = force_unit().from_n(f_n)
        return 0.0 if v is None else v

    def _to_n(self, v: float) -> float:
        """Field value → N in the unit the fields currently show (display conversion, SYS-003)."""
        return kgf_to_n(v) if getattr(self, "_unit_shown", "N") == "kgf" else float(v)

    def revert(self) -> None:
        try:
            cfg = self._backend.limits.get()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"limits.get failed: {exc}", "error")
            return
        self.show_config(cfg)
        self._show_issues([])

    def show_config(self, cfg: LimitConfig) -> None:
        self._cfg = cfg
        widgets = (self.min_en, self.min_spin, self.max_en, self.max_spin, self.pull_en, self.pull_spin, self.push_en,
                   self.push_spin, self.warn_spin, self.fw_spin)
        for w in widgets:
            w.blockSignals(True)
        self.min_en.setChecked(cfg.travel_min_enabled)
        self.min_spin.setValue(cfg.travel_min_mm if cfg.travel_min_mm is not None else 0.0)
        self.max_en.setChecked(cfg.travel_max_enabled)
        self.max_spin.setValue(cfg.travel_max_mm if cfg.travel_max_mm is not None else 0.0)
        u = self._unit()
        self._unit_shown = u
        for s in (self.pull_spin, self.push_spin, self.fw_spin):
            s.setSuffix(f" {u}")
            s.setDecimals(3 if u == "kgf" else 2)
        self.pull_en.setChecked(cfg.pull_enabled)
        self.pull_spin.setValue(self._to_disp(cfg.pull_trip_n))
        self.push_en.setChecked(cfg.push_enabled)
        self.push_spin.setValue(self._to_disp(cfg.push_trip_n))
        self.warn_spin.setValue(int(round(cfg.warn_pct)))
        self.fw_spin.setValue(self._to_disp(cfg.fw_level_n))
        for w in widgets:
            w.blockSignals(False)
        self._update_helpers()

    def edited_config(self) -> LimitConfig:
        """The fields as a ``LimitConfig`` (N, mm) — conversions only (SYS-003)."""
        to_n = self._to_n
        base = getattr(self, "_cfg", LimitConfig())
        return dataclasses.replace(
            base,
            travel_min_mm=float(self.min_spin.value()), travel_min_enabled=self.min_en.isChecked(),
            travel_max_mm=float(self.max_spin.value()), travel_max_enabled=self.max_en.isChecked(),
            pull_trip_n=to_n(float(self.pull_spin.value())), pull_enabled=self.pull_en.isChecked(),
            push_trip_n=to_n(float(self.push_spin.value())), push_enabled=self.push_en.isChecked(),
            warn_pct=float(self.warn_spin.value()), fw_level_n=to_n(float(self.fw_spin.value())))

    def _update_helpers(self, *_a: Any) -> None:
        to_n = self._to_n
        u = getattr(self, "_unit_shown", "N")
        w = float(self.warn_spin.value()) / 100.0
        for spin, lab in ((self.pull_spin, self.pull_help), (self.push_spin, self.push_help)):
            f = to_n(float(spin.value()))
            lab.setText(f"= {pct_fs(abs(f))}    warning at → {fmt_value(float(spin.value()) * w, u)} {u}")
        fw = to_n(float(self.fw_spin.value()))
        self.fw_help.setText(f"= {pct_fs(fw)}   (allowed: ≥ SW trip, ≤ 110 % FS = "
                             f"{fmt_value(n_to_kgf(FW_LIMIT_MAX_N) if u == 'kgf' else FW_LIMIT_MAX_N, u)} {u})")

    def _on_unit_changed(self, _u: str) -> None:
        if hasattr(self, "_cfg"):
            # re-express the (unchanged, not yet applied) edits in the new unit
            self.show_config(self.edited_config())

    # ================================================================== apply / issues
    def apply(self) -> None:
        cfg = self.edited_config()
        try:
            issues = list(self._backend.limits.set(cfg))
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Limits not applied: {exc}", "error")
            return
        self._show_issues(issues)
        errors = [i for i in issues if str(getattr(i, "severity", "ERROR")) == "ERROR"]
        if errors:
            self.apply_result.setText("✗ not applied: " + "; ".join(i.text for i in errors))
            self.apply_result.setStyleSheet("color: #a00000;")
            self.message.emit("Limits not applied: " + "; ".join(i.text for i in errors), "warn")
            return
        self.apply_result.setText("✓ applied" + ("  — " + "; ".join(i.text for i in issues) if issues else ""))
        self.apply_result.setStyleSheet("color: #206020;")
        self._cfg = cfg
        try:
            self.show_config(self._backend.limits.get())
        except Exception:  # noqa: BLE001
            pass

    def _show_issues(self, issues: list[Any]) -> None:
        self.last_issues = list(issues)
        by_key: dict[str, list[str]] = {}
        for i in issues:
            key = i.key if i.key in self.issue_labels else "_general"
            by_key.setdefault(key, []).append(i.text)
        for key, lab in self.issue_labels.items():
            text = "; ".join(by_key.get(key, ()))
            lab.setText(text)
            lab.setVisible(bool(text))
            f = self.fields.get(key)
            if f is not None:
                f.setStyleSheet(ERR_STYLE if text else "")

    # ================================================================== thresholds
    def recheck(self) -> None:
        try:
            fut = self._backend.limits.recheck_async()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Re-send refused: {_err_text(exc)}", "warn")
            return
        self._bridge.watch(fut, lambda st: self._show_thresholds(st),
                           lambda e: self.message.emit(f"Threshold re-send failed: {_err_text(e)}", "error"),
                           "limits.recheck")

    def set_manual_thresholds(self) -> None:
        fn = getattr(self._backend.limits, "set_manual_thresholds_async", None)
        if fn is None:
            return
        try:
            fut = fn(int(self.raw_min.value()), int(self.raw_max.value()), int(self.raw_zero.value()))
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Manual thresholds refused: {_err_text(exc)}", "warn")
            return
        self._bridge.watch(fut, self._show_thresholds,
                           lambda e: self.message.emit(f"Manual thresholds refused: {_err_text(e)}", "warn"),
                           "limits.manual")

    def set_default_thresholds(self) -> None:
        fn = getattr(self._backend.limits, "set_default_thresholds_async", None)
        if fn is None:
            return
        try:
            fut = fn()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Default thresholds refused: {_err_text(exc)}", "warn")
            return
        self._bridge.watch(fut, self._show_thresholds,
                           lambda e: self.message.emit(f"Default thresholds failed: {_err_text(e)}", "error"),
                           "limits.default")

    def _refresh_thresholds(self) -> None:
        try:
            self._show_thresholds(self._backend.limits.thresholds())
        except Exception:  # noqa: BLE001
            log.debug("thresholds() failed", exc_info=True)

    def _show_thresholds(self, thr: Any) -> None:
        if thr is None:
            return
        u = self._unit()
        parts = [f"state {thr.state}"]
        if thr.raw_min is not None or thr.raw_max is not None:
            parts.append(f"raw min {fmt_value(thr.raw_min, 'counts')}  max {fmt_value(thr.raw_max, 'counts')} "
                         f"counts  (zero_raw {fmt_value(thr.zero_raw, 'counts')})")
        if thr.fw_level_n is not None:
            parts.append(f"FW level {fmt_value(force_unit().from_n(thr.fw_level_n), u)} {u}")
        if thr.cal_id or thr.tare_id:
            parts.append(f"cal {thr.cal_id or '-'}, tare {thr.tare_id or '-'}")
        if thr.text:
            parts.append(thr.text)
        text = "   ".join(parts)
        if self.thr_label.text() != text:
            self.thr_label.setText(text)
        clamped = ""
        if thr.clamped:
            clamped = (f"(!) clamped inward: effective +{fmt_value(force_unit().from_n(thr.eff_pull_n), u)} {u} / "
                       f"{fmt_value(force_unit().from_n(thr.eff_push_n), u)} {u}")
        if self.clamped_label.text() != clamped:
            self.clamped_label.setText(clamped)
        self.clamped_label.setVisible(bool(clamped))

    # ================================================================== no-specimen mode
    def enter_no_specimen(self) -> None:
        st = self._status if self._status is not None else self._backend.status()
        gate = gating.gate_of(st, GateId.NO_SPECIMEN)
        if gate is not None and gate.refused:
            self.message.emit(f"No-specimen mode refused: {gating.refusal_text(gate)}", "warn")
            return
        item = next(iter(gate.confirm_items), None) if gate is not None else None
        dlg = make_confirm(self, "C-10", text=item.text if item is not None else None,
                           gate_provider=lambda: gating.gate_of(self._backend.status(), GateId.NO_SPECIMEN),
                           confirm_code=item.code if item is not None else None,
                           live_text=self._live_nospec_text)
        dlg.confirmed.connect(self._confirmed_no_specimen)
        self.confirm_dialog = dlg
        dlg.open()

    def _live_nospec_text(self) -> str:
        thr = getattr(getattr(self._backend.status(), "safety", None), "thresholds", None)
        return f"Board thresholds: {getattr(thr, 'state', '?')}"

    def _confirmed_no_specimen(self) -> None:
        g = self._backend.limits.set_no_specimen_mode(True, confirmed=True)
        if not g.ok:
            self.message.emit(f"No-specimen mode refused: {gating.refusal_text(g)}", "warn")

    def leave_no_specimen(self) -> None:
        g = self._backend.limits.set_no_specimen_mode(False)
        if not g.ok:
            self.message.emit(f"Leave no-specimen mode refused: {gating.refusal_text(g)}", "warn")

    # ================================================================== status
    def update_status(self, st: Any) -> None:
        self._status = st
        self._n += 1
        safety = st.safety
        on = bool(safety.no_specimen_mode)
        text = "State: ● ON — PC load limits OFF for this session; board limit active" if on else "State: ● OFF"
        if self.nospec_state.text() != text:
            self.nospec_state.setText(text)
        cs = gating.control_state(gating.gate_of(st, GateId.NO_SPECIMEN), motion=True,
                                  base_tooltip="C-10: switch the PC load limits off for this session")
        self.nospec_enter.setVisible(not on)
        self.nospec_enter.setEnabled(cs.enabled)
        self.nospec_enter.setToolTip(cs.tooltip)
        self.nospec_leave.setVisible(on)
        self._show_thresholds(safety.thresholds)
        move = gating.gate_of(st, GateId.MOVE)
        items = [i for i in (move.refused if move is not None else ()) if i.code in MOTION_DISABLED_CODES]
        notice = "; ".join(i.text for i in items)
        if notice:
            notice = "(!) " + notice + " → motion disabled (SAF-SW-001)"
        if self.motion_notice.text() != notice:
            self.motion_notice.setText(notice)
        self.motion_notice.setVisible(bool(notice))
        if self._n % 30 == 1:
            self._update_soft_limits()
        if self._n % 3 == 1:
            self._update_live(st)

    def _update_soft_limits(self) -> None:
        try:
            vals = self._backend.config.values()
        except Exception:  # noqa: BLE001
            vals = {}
        lo, hi = vals.get("limits.soft_min_um"), vals.get("limits.soft_max_um")
        if lo is None or hi is None:
            text = "FW soft limits (board, read-only): n/a (not connected)   — edit on the Config tab"
        else:
            text = (f"FW soft limits (board, read-only): {fmt_value(lo / 1000.0, 'mm')} … "
                    f"{fmt_value(hi / 1000.0, 'mm')} mm   — edit on the Config tab")
        if self.soft_label.text() != text:
            self.soft_label.setText(text)

    def _update_live(self, st: Any) -> None:
        u = self._unit()
        f_txt = f"F = {NA}"
        try:
            s = self._backend.data.latest(FORCE_KEY[u])
            f_txt = f"F = {fmt_value(s.value, u)} {u}" + ("" if s.state == "OK" else f" [{s.state}]")
        except Exception:  # noqa: BLE001
            pass
        x = st.motion.position_mm
        self.live_label.setText(f"{f_txt}    x = {fmt_value(x, 'mm')} mm (machine)")
