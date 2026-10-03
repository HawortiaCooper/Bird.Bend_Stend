"""Process-wide display state of the no-specimen mode for the NO-SPECIMEN tags (SW_design_GUI §2.8).

The main window copies ``status().safety.no_specimen_mode`` into :func:`no_specimen` on every refresh tick; every
:class:`~bend_stand.gui.widgets.safe_dock.SafeDock` title bar and every
:class:`~bend_stand.gui.dialogs.safe_dialog.SafeDialog` top bar follows it, so a window on a second monitor never
hides the mode. Display state only — the mode itself is the backend's (B §6.7).

Implements: SW-LIM-004 (tag in every floating window and dialog)
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class NoSpecimenState(QObject):
    changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._on = False

    @property
    def on(self) -> bool:
        return self._on

    def set(self, on: bool) -> None:
        on = bool(on)
        if on != self._on:
            self._on = on
            self.changed.emit(on)


_STATE: NoSpecimenState | None = None


def no_specimen() -> NoSpecimenState:
    """The singleton (created on first use, in the GUI thread)."""
    global _STATE
    if _STATE is None:
        _STATE = NoSpecimenState()
    return _STATE
