"""Exception hierarchy of the backend (SW_design §14). Every class carries ``user_text``.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/errors.py @37c87471 (adapted: bend-stand classes,
NACK fields, HardwareAccessForbidden for the D-06 test guard).

Implements: SW-PLT-003, FW-CMD-003 (NACK text shown verbatim)
"""
from __future__ import annotations

from typing import Any


class BendStandError(Exception):
    """Base class; ``user_text`` is shown by the GUI (§15.4 rule 9)."""

    def __init__(self, user_text: str = "", *args: Any) -> None:
        super().__init__(user_text, *args)
        self.user_text = user_text or self.__class__.__name__

    def __str__(self) -> str:
        return self.user_text


class LinkError(BendStandError):
    pass


class NotConnected(LinkError):
    def __init__(self, user_text: str = "not connected") -> None:
        super().__init__(user_text)


class TransportError(LinkError):
    pass


class CommandTimeout(LinkError):
    pass


class CommandOutcomeUnknown(LinkError):
    """A VERIFY command timed out and GET_STATUS could not resolve its outcome."""


class CommandNotExecuted(LinkError):
    """A VERIFY command timed out and GET_STATUS shows it was not executed (no automatic re-send)."""


class IncompatibleFirmware(LinkError):
    pass


class ConfigReadOnly(LinkError):
    def __init__(self, user_text: str = "configuration is read-only (dictionary hash / version mismatch)") -> None:
        super().__init__(user_text)


class NackError(LinkError):
    """The FW refused a command (ICD §4.1: a NACKed command had no effect)."""

    def __init__(self, cmd: str, status: int, name: str, detail: int, detail_text: str) -> None:
        super().__init__(f"{cmd} refused: {name} — {detail_text}")
        self.cmd = cmd
        self.status = status
        self.name = name
        self.detail = detail
        self.detail_text = detail_text


class PreconditionError(BendStandError):
    """A gate refused the action locally (nothing was sent)."""

    def __init__(self, gate: Any, user_text: str = "") -> None:
        text = user_text
        if not text and gate is not None:
            text = "; ".join(i.text for i in getattr(gate, "items", ()) if i.severity != "WARN") or "refused"
        super().__init__(text or "refused")
        self.gate = gate


class GateRefused(PreconditionError):
    pass


class ConfirmationRequired(PreconditionError):
    pass


class CalibrationError(BendStandError):
    pass


class SequenceError(BendStandError):
    pass


class FileFormatError(BendStandError):
    pass


class RecorderError(BendStandError):
    pass


class HardwareAccessForbidden(BendStandError):
    """Raised by the D-06 test guard when a test tries to open a COM port."""

    def __init__(self, user_text: str = "D-06: opening a hardware port is forbidden in tests") -> None:
        super().__init__(user_text)


__all__ = [
    "BendStandError", "LinkError", "NotConnected", "TransportError", "CommandTimeout",
    "CommandOutcomeUnknown", "CommandNotExecuted", "IncompatibleFirmware", "ConfigReadOnly", "NackError",
    "PreconditionError", "GateRefused", "ConfirmationRequired", "CalibrationError", "SequenceError",
    "FileFormatError", "RecorderError", "HardwareAccessForbidden",
]
