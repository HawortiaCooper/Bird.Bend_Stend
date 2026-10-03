"""``GateBinder``: enables controls from ``status().gates`` only (SW_design_GUI §2.7, P1).

Rules (display mapping only — the backend re-checks every call):

* any REFUSE item → control disabled; the tooltip lists the REFUSE texts and their ``clear_hint``;
* CONFIRM items → the action opens a ``ConfirmDialog`` and is repeated with ``confirmed=True`` (caller's job);
* WARN items → control enabled, the warning is appended to the tooltip (and returned for a warning line);
* **missing gate = fail-safe for motion:** a motion-related control whose gate is absent is disabled; a
  non-motion control without its gate stays enabled (its call returns the refusal).
* "Clear stop first": REFUSE items whose ``code`` is a generated ``BLOCK_BITS`` name in
  :data:`CLEAR_FIRST_CODES` get a [Clear stop…] hint (B3-16).

Implements: SW-PLT-002 (no rule logic in the GUI), SW-STOP-004 ("Clear stop first" display mapping)
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from PySide6.QtWidgets import QWidget

from bend_stand.core import protocol_gen as pg
from bend_stand.core.api import GateId, GateItem, GateResult, Severity

#: REFUSE codes for which the GUI offers [Clear stop…] (subset of the generated BLOCK_BITS names, G-39)
CLEAR_FIRST_CODES: frozenset[str] = frozenset({"HALT", "ESTOP", "FAULT"})
assert CLEAR_FIRST_CODES <= set(pg.BLOCK_BITS)

#: gates of controls that can start or affect motion: missing → disabled (fail-safe)
MOTION_GATES: frozenset[GateId] = frozenset({
    GateId.MOVE, GateId.JOG, GateId.HOME, GateId.ENABLE, GateId.DISABLE, GateId.PAUSE, GateId.RESUME,
    GateId.CAL_TRAVEL_START, GateId.CAL_LOAD_START, GateId.SEQUENCE_START, GateId.VALID_TOGGLE,
    GateId.TEST_ZERO, GateId.NO_SPECIMEN, GateId.HOTKEY_TEST,
})


@dataclass(frozen=True)
class ControlState:
    enabled: bool
    tooltip: str
    warning: str = ""
    clear_first: tuple[GateItem, ...] = ()


def gate_of(status: Any, gid: GateId | str) -> GateResult | None:
    """The precomputed gate of ``gid`` or None when the backend does not provide it."""
    gates = getattr(status, "gates", None)
    if gates is None:
        return None
    try:
        return gates.get(GateId(gid))
    except (ValueError, AttributeError):
        return None


def _item_text(i: GateItem) -> str:
    return i.text + (f" — {i.clear_hint}" if i.clear_hint else "")


def clear_first_items(gate: GateResult | None) -> tuple[GateItem, ...]:
    if gate is None:
        return ()
    return tuple(i for i in gate.refused if i.code in CLEAR_FIRST_CODES)


def control_state(gate: GateResult | None, *, motion: bool, base_tooltip: str = "") -> ControlState:
    """Pure mapping gate → enabled / tooltip / warning (unit-tested)."""
    lines = [base_tooltip] if base_tooltip else []
    if gate is None:
        if motion:
            return ControlState(False, "\n".join(lines + ["not available (backend gate missing)"]))
        return ControlState(True, "\n".join(lines))
    refused = gate.refused
    warns = gate.warnings
    if refused:
        lines += ["Refused:"] + [f"• {_item_text(i)}" for i in refused]
    if warns:
        lines += [f"⚠ {_item_text(i)}" for i in warns]
    confirm = gate.confirm_items
    if confirm and not refused:
        lines += [f"needs confirmation: {i.text}" for i in confirm]
    return ControlState(not refused, "\n".join(lines), "; ".join(i.text for i in warns),
                        clear_first_items(gate))


def refusal_text(gate: GateResult | None) -> str:
    if gate is None:
        return ""
    return "; ".join(_item_text(i) for i in gate.refused)


class GateBinder:
    """Binds widgets to gate ids; :meth:`update` is called once per refresh tick (change-only updates)."""

    def __init__(self) -> None:
        self._bindings: list[tuple[QWidget, GateId, bool, str, Callable[[Any], bool] | None]] = []

    def bind(self, widget: QWidget, gid: GateId, *, base_tooltip: str = "", motion: bool | None = None,
             extra: Callable[[Any], bool] | None = None) -> None:
        """``extra(status) -> bool`` may further disable the widget (e.g. Stream: wrong direction)."""
        self._bindings.append((widget, gid, gid in MOTION_GATES if motion is None else motion, base_tooltip,
                               extra))

    def bindings(self) -> Iterable[tuple[QWidget, GateId]]:
        return [(w, g) for w, g, *_ in self._bindings]

    def update(self, status: Any) -> None:
        for widget, gid, motion, tip, extra in self._bindings:
            cs = control_state(gate_of(status, gid), motion=motion, base_tooltip=tip)
            enabled = cs.enabled and (extra(status) if extra is not None else True)
            if widget.isEnabled() != enabled:
                widget.setEnabled(enabled)
            if widget.toolTip() != cs.tooltip:
                widget.setToolTip(cs.tooltip)


def is_refused(gate: GateResult | None, *, motion: bool = False) -> bool:
    if gate is None:
        return motion
    return bool(gate.refused)


def severity_of(item: GateItem) -> str:
    return str(item.severity.value if isinstance(item.severity, Severity) else item.severity)
