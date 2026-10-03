"""Channel registry (SW_design §7.4, SW-RT-002): every plottable channel with key, label, unit, group,
availability + reason. Status-bit channels iterate the generated ``DATA_FLAGS_BITS`` / ``DATA_STATUS_BITS``
(no hand-listed names, GF-08); their keys are ``bit.<name lower-cased>`` and their labels the generated names.

M1: raw counts, setpoint (machine mm), measured sample rate, cumulative lost frames, display state, status
bits. Force / test-travel / derived channels are registered unavailable (reason) until calibration (M3).

Implements: SW-RT-002, SW-RT-004 (registry part)
"""
from __future__ import annotations

import threading
from collections.abc import Callable

from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import ChannelSpec

BIT_PREFIX = "bit."


def bit_key(name: str) -> str:
    return BIT_PREFIX + name.lower()


BIT_CHANNELS: tuple[tuple[str, str, int, str], ...] = tuple(
    [(bit_key(n), n, i, "flags") for i, n in enumerate(pg.DATA_FLAGS_BITS) if n]
    + [(bit_key(n), n, i, "status") for i, n in enumerate(pg.DATA_STATUS_BITS) if n])

#: ring-buffer columns of the M1 pipeline (order = storage order)
RING_KEYS: tuple[str, ...] = ("raw", "x_mm", "rate_sps", "lost_frames", "vstate") + tuple(k for k, *_ in BIT_CHANNELS)

_BASE: tuple[ChannelSpec, ...] = (
    ChannelSpec("raw", "HX711 raw", "counts", "load"),
    ChannelSpec("x_mm", "Setpoint (machine)", "mm", "travel"),
    ChannelSpec("rate_sps", "Sample rate (measured)", "SPS", "link"),
    ChannelSpec("lost_frames", "Lost frames (cumulative)", "", "link"),
    ChannelSpec("vstate", "Display state", "", "link"),
)
_UNAVAILABLE: tuple[ChannelSpec, ...] = (
    ChannelSpec("F_N", "Force", "N", "load", False, "no load calibration (M3)"),
    ChannelSpec("F_kgf", "Force", "kgf", "load", False, "no load calibration (M3)"),
    ChannelSpec("x_test_mm", "Test travel", "mm", "travel", False, "test travel zero not set (M3)"),
    ChannelSpec("speed_mm_s", "Speed", "mm/s", "travel", False, "derived channels: M3"),
    ChannelSpec("force_rate_n_s", "Force rate", "N/s", "load", False, "no load calibration (M3)"),
)


class ChannelRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        bits = tuple(ChannelSpec(k, n, "", f"status.{grp}") for k, n, _i, grp in BIT_CHANNELS)
        self._specs: dict[str, ChannelSpec] = {c.key: c for c in _BASE + bits + _UNAVAILABLE}
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
        with self._lock:
            c = self._specs[key]
            if c.available == available and c.reason == reason:
                return
            self._specs[key] = ChannelSpec(c.key, c.label, c.unit, c.group, available, reason)
        cb = self.on_change
        if cb is not None:
            cb()
