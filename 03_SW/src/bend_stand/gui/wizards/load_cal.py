"""Load calibration wizard (SW_design_GUI §6.3) over ``backend.load_cal`` (B §9.4, B5-08).

Phases from the engine: CONFIG, AWAIT_OPERATOR, PRESETTLE, CAPTURE, EVALUATE, FIT, ACCEPT, DONE (+ ABORTED);
AWAIT_OPERATOR … EVALUATE repeat per point (``step_index`` = point, 0 = zero point). Start configuration: number
of points (zero + weights, default 3), pre-settle and capture (session defaults 2 s / 10 s) →
``start(n_points=, presettle_s=, capture_s=)``; the masses are entered per point (``InputSpec mass_kg``). The info
box states the LOW_SPAN consequence of the 1 kg + 10 kg weights (D-22, SW-CAL-008). During PRESETTLE / CAPTURE the
progress bar and the live stats are shown; a rejected point stays in AWAIT_OPERATOR with the engine's reasons and
[Repeat]; FIT shows K / B / residuals / NL_span / status (``LoadCalResult``) and a residual plot; WARN → C-06;
FAIL → Accept disabled (``can_continue`` False). Extra buttons: [Finish with 2 points] (``finish_early()``,
UNVERIFIED_LINEARITY) and [Re-take point ▾] (``retake(i)``).

Implements: SW-CAL-005, SW-CAL-006 (point result shown), SW-CAL-007 (fit view, C-06, finish early, re-take),
SW-CAL-008 (LOW_SPAN info), SW-CAL-009 (accept → active calibration, engine)
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QToolButton

from bend_stand.core.api import GateId
from bend_stand.gui.wizards import phase_views as pv
from bend_stand.gui.wizards.safe_wizard import SafeWizard, retake_menu

LOW_SPAN_INFO = ("Weights 1 kg + 10 kg (D-22) cover only 5 % FS → the calibration carries LOW_SPAN; forces above "
                 "3 × the largest calibration force are shown as 'extrapolated' (SW-CAL-008). The active "
                 "calibration is replaced only when you accept the fit.")


def _spin(parent: Any, name: str, value: float, lo: float, hi: float, suffix: str) -> QDoubleSpinBox:
    s = QDoubleSpinBox(parent)
    s.setObjectName(name)
    s.setDecimals(1)
    s.setRange(lo, hi)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class LoadCalWizard(SafeWizard):
    KIND = "load_cal"
    TITLE = "Load calibration"
    START_GATE = GateId.CAL_LOAD_START
    CONFIRM_CID = "C-06"                                       # WARN linearity (SW-CAL-007)
    # Implements: SW-CAL-007 (D-50 a, SRS v0.6.5) — K plausibility: the engine's FIT confirmation K_IMPLAUSIBLE gets
    # its own dialog (C-14, assertion "weights, cell and AFE gain checked"); "weight not detected" is an engine error
    # on the point page (AWAIT_OPERATOR, Repeat / Continue with another mass) and needs no GUI rule.
    CONFIRM_CIDS = {"K_IMPLAUSIBLE": "C-14"}
    INFO_PREFIXES = ("nominal unknown",)                       # "nominal unknown: K plausibility not checked"

    def build_start_config(self, form: QFormLayout) -> None:
        try:
            s = self._backend.session.get()
            pre, cap = float(getattr(s, "cal_presettle_s", 2.0)), float(getattr(s, "cal_capture_s", 10.0))
        except Exception:  # noqa: BLE001
            pre, cap = 2.0, 10.0
        self.points = QSpinBox(self)
        self.points.setObjectName("nPoints")
        self.points.setRange(2, 10)
        self.points.setValue(3)
        self.presettle = _spin(self, "presettleS", pre, 0.0, 60.0, " s")
        self.capture = _spin(self, "captureS", cap, 1.0, 120.0, " s")
        form.addRow("points (zero + weights)", self.points)
        form.addRow("pre-settle", self.presettle)
        form.addRow("capture", self.capture)
        self.span_info = QLabel(LOW_SPAN_INFO, self)
        self.span_info.setObjectName("lowSpanInfo")
        self.span_info.setWordWrap(True)
        self.span_info.setStyleSheet("color: #805000;")
        form.addRow(self.span_info)

    def start_config(self) -> dict[str, Any]:
        return {"n_points": int(self.points.value()), "presettle_s": float(self.presettle.value()),
                "capture_s": float(self.capture.value())}

    def build_extra_buttons(self, row: QHBoxLayout) -> None:
        self.finish_button = QPushButton("Finish with 2 points", self)
        self.finish_button.setObjectName("finishEarly")
        self.finish_button.setAutoDefault(False)
        self.finish_button.clicked.connect(self.on_finish_early)
        self.retake_button = QToolButton(self)
        self.retake_button.setObjectName("retakePoint")
        self.retake_button.setText("Re-take point ▾")
        self.retake_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        row.addWidget(self.retake_button)
        row.addWidget(self.finish_button)
        self.finish_button.hide()
        self.retake_button.hide()

    def point_count(self, st: Any) -> int:
        """Number of points for the re-take menu: the engine's ``step_count`` (n_points), else the fit rows."""
        if st.step_count:
            return int(st.step_count)
        _s, _h, rows = pv.result_parts(st.result)
        if rows:
            return len(rows)
        _x, res = pv.residual_points(st.result)
        return len(res)

    def update_extra(self, st: Any) -> None:
        # display mapping only: the engine refuses an early finish / re-take that is not allowed (B §9.4)
        await_op = st.phase == "AWAIT_OPERATOR"
        self.finish_button.setVisible(await_op and int(st.step_index or 0) >= 2)
        n = self.point_count(st)
        show = st.phase in ("FIT", "AWAIT_OPERATOR") and n > 0
        self.retake_button.setVisible(show)
        if show:
            retake_menu(self.retake_button, n, self.on_retake)

    def on_finish_early(self) -> None:
        try:
            self.engine.finish_early()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"{self.TITLE}: {getattr(exc, 'user_text', exc)}", "warn")
        self.refresh()

    def on_retake(self, i: int) -> None:
        try:
            self.engine.retake(i)
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"{self.TITLE}: {getattr(exc, 'user_text', exc)}", "warn")
        self.refresh()

