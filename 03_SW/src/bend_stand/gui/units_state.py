"""Process-wide display unit of force (View ▸ Units N / kgf, SW_design_GUI §2.5, §4.3; SYS-003).

A display choice only: the API always uses N (B §15.4 rule 8). Readouts swap the channel ``F_N`` ↔ ``F_kgf``,
the Manual / Safety-limits tabs convert with the ``calc.units`` helpers. The main window stores the choice in the
session (``SessionSettings.display_unit``) and in the GUI settings.

Implements: SYS-003 (N / kgf display)
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from bend_stand.calc.units import kgf_to_n, n_to_kgf

FORCE_UNITS = ("N", "kgf")

#: readout / plot channel of each display unit
FORCE_KEY = {"N": "F_N", "kgf": "F_kgf"}


class ForceUnitState(QObject):
    changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._unit = "N"

    @property
    def unit(self) -> str:
        return self._unit

    def set(self, unit: str) -> None:
        unit = unit if unit in FORCE_UNITS else "N"
        if unit != self._unit:
            self._unit = unit
            self.changed.emit(unit)

    def from_n(self, f_n: float | None) -> float | None:
        if f_n is None:
            return None
        return n_to_kgf(f_n) if self._unit == "kgf" else float(f_n)

    def to_n(self, f: float) -> float:
        return kgf_to_n(f) if self._unit == "kgf" else float(f)


_STATE: ForceUnitState | None = None


def force_unit() -> ForceUnitState:
    """The singleton (created on first use, in the GUI thread)."""
    global _STATE
    if _STATE is None:
        _STATE = ForceUnitState()
    return _STATE
