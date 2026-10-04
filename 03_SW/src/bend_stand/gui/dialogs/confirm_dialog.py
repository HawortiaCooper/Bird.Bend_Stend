"""``ConfirmDialog``: the one confirmation dialog class for C-01…C-13 (SW_design_GUI §5.5).

Keyboard rules (SAF-SW-004):

* no default button (``setDefault(False)`` / ``setAutoDefault(False)`` on every button);
* the confirm button has ``Qt.NoFocus`` and no mnemonic;
* an event filter on the dialog and every child swallows ``Return``, ``Enter`` and ``Space`` (press and release),
  so they never reach a button; Space still ticks the assertion checkbox (harmless, it only asserts a fact);
* ``Esc`` = Cancel; the initial focus is on Cancel — confirming needs a **mouse click** on the confirm button;
* where the SRS asks the operator to assert a fact, the confirm button stays disabled until the checkbox is ticked;
* STOP (top bar, from :class:`SafeDialog`) is reachable; pressing it also closes the dialog unconfirmed;
* live re-check: every 0.5 s ``gate_provider()`` is read — if the CONFIRM item (``confirm_code``) disappears the
  dialog closes as "not needed", if a REFUSE item appears it closes with that text.

The dialog never decides anything: the caller repeats the backend call with ``confirmed=True`` from the
``confirmed`` signal (B §15.4 rule 6). It is shown with :meth:`open` (non-blocking, application-modal).

Implements: SAF-SW-004 (Enter/Space never confirm, STOP reachable), SW-CFG-004 (C-04), SW-CFG-003 (C-11)
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bend_stand.gui.dialogs.safe_dialog import SafeDialog

log = logging.getLogger(__name__)

_SWALLOW = {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}

#: Outcomes reported by :attr:`ConfirmDialog.outcome`
OUTCOMES = ("pending", "confirmed", "cancelled", "not_needed", "refused", "stopped")

#: GUI-side confirmation texts (C-04, C-09, C-11 are GUI cautions; the others show the backend's CONFIRM text and
#: use these only as fallback titles). (title, text, confirm label, assertion or None)
TEXTS: dict[str, tuple[str, str, str, str | None]] = {
    "C-03": ("Clear E-STOP",
             "Clear E-STOP: red E-stop button released (input closed ≥ 100 ms) and the area safe? After "
             "clearing, the driver stays disabled: ENABLE and HOME are required. No motion restarts.",
             "Clear E-STOP", "E-stop button released, area safe"),
    "C-04": ("Restore defaults",
             "Restore all parameters to defaults (RAM; NVM unchanged until Save to NVM). Session load thresholds "
             "are re-sent by the PC.", "Restore defaults", None),
    "C-09": ("Close the application",
             "Closing sends STOP, ends the recording and leaves the driver enabled (holding).", "Close", None),
    "C-11": ("Save & reboot",
             "Save all parameters to NVM and reboot the board? The stream restarts, the axis is NOT homed "
             "afterwards, the driver keeps holding (D-13).", "Save & reboot", None),
    "C-13": ("Clear stop ends the paused sequence",
             "Clear stop ends the paused sequence (STOPPED, reason CLEARED) and clears HALT and PAUSE. No motion "
             "restarts. To continue the sequence use Resume instead.", "Clear stop", None),
}


def make_confirm(parent: QWidget | None, cid: str, *, text: str | None = None, **kwargs: Any) -> "ConfirmDialog":
    """Build the ConfirmDialog of ``cid`` from :data:`TEXTS` (``text`` overrides with the backend's text)."""
    title, default_text, label, assertion = TEXTS[cid]
    return ConfirmDialog(parent, cid=cid, title=title, text=text or default_text, confirm_label=label,
                         assertion=assertion, **kwargs)


class _KeyGuard(QObject):
    """Swallows Return / Enter / Space before they reach any button (Space allowed on checkboxes)."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
            key = event.key()  # type: ignore[attr-defined]
            if key in _SWALLOW:
                if key == Qt.Key.Key_Space and isinstance(obj, QCheckBox):
                    return False
                event.accept()
                return True
        return False


class ConfirmDialog(SafeDialog):
    """Confirmation for one CONFIRM situation (``cid`` = C-01…C-13)."""

    confirmed = Signal()
    closedUnconfirmed = Signal(str)          # outcome: cancelled / not_needed / refused / stopped

    RECHECK_MS = 500

    def __init__(self, parent: QWidget | None = None, *, cid: str, title: str, text: str,
                 confirm_label: str = "Confirm", assertion: str | None = None,
                 gate_provider: Callable[[], Any] | None = None, confirm_code: str | None = None,
                 live_text: Callable[[], str] | None = None) -> None:
        super().__init__(parent, title=f"{cid}: {title}")
        self.cid = cid
        self.setObjectName(f"confirm_{cid}")
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.outcome = "pending"
        self.outcome_text = ""
        self._gate_provider = gate_provider
        self._confirm_code = confirm_code
        self._live_text = live_text

        lay = QVBoxLayout()
        self.text_label = QLabel(text, self)
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.text_label)
        self.live_label = QLabel("", self)
        self.live_label.setWordWrap(True)
        self.live_label.setStyleSheet("color: #404040;")
        self.live_label.setVisible(live_text is not None)
        lay.addWidget(self.live_label)

        self.assertion_box: QCheckBox | None = None
        if assertion:
            self.assertion_box = QCheckBox(assertion, self)
            self.assertion_box.toggled.connect(self._update_confirm_enabled)
            lay.addWidget(self.assertion_box)

        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.confirm_button = QPushButton(confirm_label.replace("&", "&&"), self)
        self.confirm_button.setObjectName("confirmButton")
        self.confirm_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for b in (self.cancel_button, self.confirm_button):
            b.setAutoDefault(False)
            b.setDefault(False)
        self.cancel_button.clicked.connect(self._on_cancel)
        self.confirm_button.clicked.connect(self._on_confirm)
        row.addWidget(self.cancel_button)
        row.addWidget(self.confirm_button)
        lay.addLayout(row)
        self.setLayout(lay)

        self.stop_button.pressed.connect(self._on_stop_pressed)
        self._guard = _KeyGuard(self)
        self._install_guard()
        self._update_confirm_enabled()

        self._timer = QTimer(self)
        self._timer.setInterval(self.RECHECK_MS)
        self._timer.timeout.connect(self.recheck)
        self._refresh_live()

    # ------------------------------------------------------------------ keyboard guard
    def _install_guard(self) -> None:
        self.installEventFilter(self._guard)
        for w in self.findChildren(QWidget):
            w.installEventFilter(self._guard)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._install_guard()
        super().showEvent(event)
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
        if self._gate_provider is not None or self._live_text is not None:
            self._timer.start()

    # ------------------------------------------------------------------ state
    def _update_confirm_enabled(self, *_a: Any) -> None:
        ok = self.assertion_box is None or self.assertion_box.isChecked()
        self.confirm_button.setEnabled(ok)

    def _refresh_live(self) -> None:
        if self._live_text is None:
            return
        try:
            self.live_label.setText(self._live_text())
        except Exception:  # noqa: BLE001 - presentation only
            log.debug("live text failed", exc_info=True)

    def recheck(self) -> None:
        """Re-read the gate (0.5 s): CONFIRM item gone → "not needed"; REFUSE item → closed with its text."""
        self._refresh_live()
        if self._gate_provider is None or self.outcome != "pending":
            return
        try:
            gate = self._gate_provider()
        except Exception:  # noqa: BLE001
            log.debug("gate provider failed", exc_info=True)
            return
        if gate is None:
            return
        refused = tuple(getattr(gate, "refused", ()))
        if refused:
            self._finish("refused", "; ".join(i.text for i in refused))
            return
        if self._confirm_code is not None:
            codes = {i.code for i in getattr(gate, "confirm_items", ())}
            if self._confirm_code not in codes:
                self._finish("not_needed", "confirmation no longer needed")

    # ------------------------------------------------------------------ actions
    def _on_confirm(self) -> None:
        if not self.confirm_button.isEnabled() or self.outcome != "pending":
            return
        self.outcome = "confirmed"
        self._timer.stop()
        self.accept()
        self.confirmed.emit()

    def _on_cancel(self) -> None:
        self._finish("cancelled", "")

    def _on_stop_pressed(self) -> None:
        self._finish("stopped", "STOP pressed")

    def reject(self) -> None:  # Esc / window close
        if self.outcome == "pending":
            self.outcome = "cancelled"
            self._timer.stop()
            super().reject()
            self.closedUnconfirmed.emit("cancelled")
            return
        super().reject()

    def _finish(self, outcome: str, text: str) -> None:
        if self.outcome != "pending":
            return
        self.outcome = outcome
        self.outcome_text = text
        self._timer.stop()
        super().reject()
        self.closedUnconfirmed.emit(outcome)
