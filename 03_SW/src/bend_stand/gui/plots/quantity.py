"""Quantity groups for the default pane placement of the plot windows (SW_design_GUI §4.7, D-38, TS D-63).

A channel ticked in the channel tree goes by default to the first pane that already shows a channel of the **same
quantity group** (all travels together, all forces together, all status bits together, …), otherwise to the first
empty pane, otherwise to a new pane.

The bend-stand ``ChannelSpec`` has no ``dimension`` field yet (requested from B as GRQ-B-21); until it exists the
group is derived from, in this order: a ``dimension`` attribute (future), the key (status bits: generated
``DATA_FLAGS_BITS`` / ``DATA_STATUS_BITS`` names), the unit, the registry group. Every group holds at most two
units, so a quantity pane never overflows (2-units rule of :class:`~bend_stand.gui.plots.plot_pane.PlotPane`).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/plots/quantity.py @37c8747 (adapted: bend-stand quantities,
unit fallback first, status-bit detection by the generated names; pure functions, no Qt).

Implements: SW-RT-006 (default grouping by quantity)
"""
from __future__ import annotations

from typing import Any

from bend_stand.core import protocol_gen as pg

BITS = "Status bits"

#: ChannelSpec.dimension (when B adds it, GRQ-B-21) -> quantity group
GROUP_BY_DIMENSION: dict[str, str] = {
    "force": "Force", "length": "Travel", "position": "Travel", "speed": "Speed", "force_rate": "Force rate",
    "counts": "Raw counts", "rate": "Sample rate", "count": "Link counters", "bits": BITS, "bool": BITS,
    "state": "States", "stress": "Stress", "strain": "Strain", "stiffness": "Stiffness", "work": "Work",
}

#: channel key -> group where the unit alone does not fit
GROUP_BY_KEY: dict[str, str] = {
    "lost_frames": "Link counters",
    "vstate": "States",
}

#: canonical unit -> group
GROUP_BY_UNIT: dict[str, str] = {
    "N": "Force", "kgf": "Force",
    "mm": "Travel", "µm": "Travel", "um": "Travel",
    "mm/s": "Speed",
    "N/s": "Force rate",
    "counts": "Raw counts", "cnt": "Raw counts",
    "SPS": "Sample rate", "Hz": "Sample rate",
    "MPa": "Stress",
    "N/mm": "Stiffness",
    "N·mm": "Work", "Nmm": "Work",
}

OTHER = "Other"
_BIT_NAMES = frozenset(n for n in (*pg.DATA_FLAGS_BITS, *pg.DATA_STATUS_BITS) if n)


def bit_name_of(key: str) -> str | None:
    """Generated bit name of a status-bit channel key (``bit.valid`` → ``VALID``), else None."""
    name = str(key).replace(":", ".").split(".")[-1].upper()
    return name if name in _BIT_NAMES else None


def quantity_group(spec: Any) -> str:
    """Quantity group of a channel spec (``ChannelSpec`` or any object with ``key`` / ``unit`` / ``group``
    and optionally ``dimension``). Never empty."""
    key = str(getattr(spec, "key", "") or "")
    dim = str(getattr(spec, "dimension", "") or "")
    if dim:
        return GROUP_BY_DIMENSION.get(dim, dim.replace("_", " ").capitalize())
    if bit_name_of(key):
        return BITS
    if key in GROUP_BY_KEY:
        return GROUP_BY_KEY[key]
    unit = str(getattr(spec, "unit", "") or "")
    if unit in GROUP_BY_UNIT:
        return GROUP_BY_UNIT[unit]
    if unit:
        return f"[{unit}]"
    group = str(getattr(spec, "group", "") or "")
    return group.capitalize() if group else OTHER
