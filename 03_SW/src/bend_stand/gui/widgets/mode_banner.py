"""``ModeBanner``: persistent no-specimen mode banner on every tab (SW_design_GUI §2.8). It belongs to the main
window (not to a tab), cannot be closed and offers [Leave no-specimen mode] (``limits.set_no_specimen_mode(False)``
— adds protection, no confirmation). Entering the mode (C-10) is M3.

Implements: SW-LIM-004 (persistent banner on every tab while the mode is on)
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from bend_stand.gui.theme import MODE_BANNER_STYLE


def scope_of(status: Any) -> str | None:
    """D-54 a: the wizard-scoped no-specimen state (e.g. "TRAVEL_CAL"), None when not active."""
    return getattr(getattr(status, "safety", None), "no_specimen_scope", None) or None


def scope_label(scope: str) -> str:
    return scope.lower().replace("_cal", " calibration").replace("_", " ")


def mode_text(status: Any) -> str:
    thr = getattr(getattr(status, "safety", None), "thresholds", None)
    if getattr(thr, "state", "") == "VERIFIED" and getattr(thr, "eff_pull_n", None) is not None:
        board = f"calibrated: +{thr.eff_pull_n:.1f} N / {thr.eff_push_n:.1f} N"
    else:
        board = "default thresholds"
    scope = scope_of(status)
    if scope and not getattr(getattr(status, "safety", None), "no_specimen_mode", False):
        return (f"NO SPECIMEN – {scope_label(scope)}: PC load limits OFF for the wizard's own moves. Board load limit "
                f"active ({board}). Travel limits active. Do not mount a specimen.")
    return ("NO-SPECIMEN MODE – PC load limits OFF for this session. Board load limit active "
            f"({board}). Travel limits active. Do not mount a specimen.")


class ModeBanner(QFrame):
    leaveRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeBanner")
        self.setStyleSheet(f"QFrame#modeBanner {{{MODE_BANNER_STYLE}}}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        self.label = QLabel("", self)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.leave_button = QPushButton("Leave no-specimen mode", self)
        self.leave_button.setAutoDefault(False)
        self.leave_button.clicked.connect(self.leaveRequested)
        lay.addWidget(self.leave_button)
        self.hide()

    def update_status(self, status: Any) -> None:
        """Implements: SW-LIM-004, SW-CAL-002 (D-54 a) — shown for the session mode and for a wizard scope; the
        [Leave] button only for the session mode (the scope ends with the wizard)."""
        session = bool(getattr(getattr(status, "safety", None), "no_specimen_mode", False))
        on = session or bool(scope_of(status))
        if self.leave_button.isHidden() == session:
            self.leave_button.setVisible(session)
        if on:
            t = mode_text(status)
            if self.label.text() != t:
                self.label.setText(t)
        if self.isHidden() == on:
            self.setVisible(on)
