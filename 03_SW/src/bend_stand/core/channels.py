"""Channel registry (SW_design §7.4, SW-RT-002): every plottable channel with key, label, unit, group,
availability + reason. Status-bit channels iterate the generated ``DATA_FLAGS_BITS`` / ``DATA_STATUS_BITS``
(no hand-listed names, GF-08); their keys are ``bit.<name lower-cased>`` and their labels the generated names.

M1: raw counts, setpoint (machine mm), measured sample rate, cumulative lost frames, display state, status
bits. M3: the derived channels of ``core.scaling.DERIVED_KEYS`` (F, kgf, test travel, speed, force rate, tangent /
secant stiffness, work, peak, noise, 3-point-bend stress / strain); the backend sets their availability + reason
from the calibration / tare / geometry state (``apply_scale``), which publishes ``channels.changed``.

Implements: SW-RT-002, SW-RT-004 (registry part)
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace

from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import ChannelSpec
from bend_stand.core.scaling import DERIVED_KEYS

BIT_PREFIX = "bit."


def bit_key(name: str) -> str:
    return BIT_PREFIX + name.lower()


BIT_CHANNELS: tuple[tuple[str, str, int, str], ...] = tuple(
    [(bit_key(n), n, i, "flags") for i, n in enumerate(pg.DATA_FLAGS_BITS) if n]
    + [(bit_key(n), n, i, "status") for i, n in enumerate(pg.DATA_STATUS_BITS) if n])

#: ring-buffer columns of the M1 pipeline (order = storage order)
RING_KEYS: tuple[str, ...] = (("raw", "x_mm", "rate_sps", "lost_frames", "vstate") + tuple(k for k, *_ in BIT_CHANNELS)
                              + DERIVED_KEYS)

_BASE: tuple[ChannelSpec, ...] = (
    ChannelSpec("raw", "HX711 raw", "counts", "load", dimension="counts"),
    ChannelSpec("x_mm", "Setpoint (machine)", "mm", "travel", dimension="length"),
    ChannelSpec("rate_sps", "Sample rate (measured)", "SPS", "link", dimension="rate"),
    ChannelSpec("lost_frames", "Lost frames (cumulative)", "", "link", dimension="count"),
    ChannelSpec("vstate", "Display state", "", "link", dimension="state"),
)
NO_FORCE = "needs a load calibration + tare"
NO_GEOMETRY = "needs the 3-point-bend geometry (session) and a force"
_DERIVED: tuple[ChannelSpec, ...] = (
    ChannelSpec("F_N", "Force", "N", "load", False, NO_FORCE, "force"),
    ChannelSpec("F_kgf", "Force", "kgf", "load", False, NO_FORCE, "force"),
    ChannelSpec("x_test_mm", "Test travel", "mm", "travel", True, None, "length"),
    ChannelSpec("speed_mm_s", "Speed", "mm/s", "travel", True, None, "speed"),
    ChannelSpec("force_rate_n_s", "Force rate", "N/s", "load", False, NO_FORCE, "force_rate"),
    ChannelSpec("k_tan_n_mm", "Stiffness (tangent)", "N/mm", "load", False, NO_FORCE, "stiffness"),
    ChannelSpec("k_sec_n_mm", "Stiffness (secant)", "N/mm", "load", False, NO_FORCE, "stiffness"),
    ChannelSpec("work_nmm", "Work", "N·mm", "load", False, NO_FORCE, "work"),
    ChannelSpec("peak_n", "Peak force", "N", "load", False, NO_FORCE, "force"),
    ChannelSpec("noise_counts", "Noise (1 s std)", "counts", "load", True, None, "counts"),
    ChannelSpec("noise_n", "Noise (1 s std)", "N", "load", False, "needs a load calibration", "force"),
    ChannelSpec("sigma_mpa", "Flexural stress (3-point bend)", "MPa", "bend", False, NO_GEOMETRY, "stress"),
    ChannelSpec("eps", "Flexural strain (3-point bend)", "", "bend", False, NO_GEOMETRY, "strain"),
)
assert tuple(c.key for c in _DERIVED) == DERIVED_KEYS
#: channels that need F (calibration + tare), only the calibration, or F + geometry
NEEDS_FORCE = ("F_N", "F_kgf", "force_rate_n_s", "k_tan_n_mm", "k_sec_n_mm", "work_nmm", "peak_n")
NEEDS_CAL = ("noise_n",)
NEEDS_GEOMETRY = ("sigma_mpa", "eps")


class ChannelRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        bits = tuple(ChannelSpec(k, n, "", f"status.{grp}", dimension="bits") for k, n, _i, grp in BIT_CHANNELS)
        self._specs: dict[str, ChannelSpec] = {c.key: c for c in _BASE + bits + _DERIVED}
        self.on_change: Callable[[], None] | None = None

    def channels(self) -> list[ChannelSpec]:
        with self._lock:
            return list(self._specs.values())

    def get(self, key: str) -> ChannelSpec:
        with self._lock:
            return self._specs[key]

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._specs)

    def set_available(self, key: str, available: bool, reason: str | None = None) -> None:
        self.set_availability({key: (available, reason)})

    def set_availability(self, items: dict[str, tuple[bool, str | None]]) -> bool:
        """Update several channels; one ``channels.changed`` notification when anything changed."""
        changed = False
        with self._lock:
            for key, (available, reason) in items.items():
                c = self._specs[key]
                reason = None if available else reason
                if c.available != available or c.reason != reason:
                    self._specs[key] = replace(c, available=available, reason=reason)
                    changed = True
        cb = self.on_change
        if changed and cb is not None:
            cb()
        return changed

    def apply_scale(self, force: bool, force_reason: str | None, cal: bool, geometry: bool) -> bool:
        """Availability of the derived channels from the scale state (SW-RT-002 greyed state + reason)."""
        items: dict[str, tuple[bool, str | None]] = {k: (force, force_reason or NO_FORCE) for k in NEEDS_FORCE}
        items.update({k: (cal, "needs a load calibration") for k in NEEDS_CAL})
        items.update({k: (force and geometry, NO_GEOMETRY) for k in NEEDS_GEOMETRY})
        return self.set_availability(items)
