"""Travel calibration wizard (SW_design_GUI §6.2) over ``backend.travel_cal`` (B §9.3, §9.3.1).

Phases from the engine: CHECK, BACKLASH, REFERENCE, MOVE1, ENTER_D1, MOVE2, ENTER_DTOT, RESULT, ACCEPT, DONE
(+ ABORTED, RESTORING). The start page shows the ``cal_travel_start`` gate and the expected steps/mm (session
``expected_spm``, 800 = 4000 p/rev closed loop, D-27 closed); 160 steps/mm appears only in the engine's C-05 text
("DIP change not applied"). Continue buttons that move (``continue_moves``) are mouse-only. Any exit other than
ACCEPT runs the engine's RESTORING (board RAM back to spm0, read-back), shown as a page.

Implements: SW-CAL-001, SW-CAL-002, SW-CAL-003 (C-05 shown with the engine's text), SW-CAL-004 (result / accept)
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLabel

from bend_stand.core.api import GateId
from bend_stand.gui.format import fmt_value
from bend_stand.gui.wizards.safe_wizard import SafeWizard


class TravelCalWizard(SafeWizard):
    KIND = "travel_cal"
    TITLE = "Travel calibration"
    START_GATE = GateId.CAL_TRAVEL_START
    CONFIRM_CID = "C-05"

    def build_start_config(self, form: QFormLayout) -> None:
        try:
            spm = float(self._backend.session.get().expected_spm)
        except Exception:  # noqa: BLE001
            spm = 800.0
        self.expected_label = QLabel(
            f"expected {fmt_value(spm, 'mm')} steps/mm (4000 pulses/rev closed loop on the 5 mm lead, D-27). "
            "The axis moves +2 mm (backlash), then 10 mm and 50 mm in one direction; you measure with a caliper / "
            "dial gauge from a reference you set. No specimen may be mounted.", self)
        self.expected_label.setObjectName("expectedSpm")
        self.expected_label.setWordWrap(True)
        form.addRow(self.expected_label)

    def start_config(self) -> dict[str, Any]:
        return {}
