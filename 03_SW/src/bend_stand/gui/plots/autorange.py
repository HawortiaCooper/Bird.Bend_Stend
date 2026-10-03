"""Y-range auto-scaling with hysteresis — a pure display function (SW_design_GUI §4.6 rule 3).

Expand immediately when the data leaves the range; shrink only when the data span has been < 70 % of the range
span for ≥ 1 s. 5 % margin. The GUI then calls ``setYRange(lo, hi, padding=0, update=False)`` with auto-range
disabled, which removes pyqtgraph's second paint per refresh (Thrust_Stand SWD-PM3-05 cause 2).

Implements: SW-RT-003 (auto Y), NFR-001 (no lazy auto-range repaint)
"""
from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np

MARGIN = 0.05
SHRINK_RATIO = 0.70
SHRINK_HOLD_S = 1.0


def data_bounds(arrays: Iterable[np.ndarray]) -> tuple[float, float] | None:
    """NaN-aware (min, max) over arrays; None when no finite value exists."""
    lo, hi = math.inf, -math.inf
    for a in arrays:
        if a is None or len(a) == 0:
            continue
        f = a[np.isfinite(a)]
        if f.size:
            lo = min(lo, float(f.min()))
            hi = max(hi, float(f.max()))
    if lo > hi:
        return None
    return lo, hi


def padded(lo: float, hi: float, margin: float = MARGIN) -> tuple[float, float]:
    span = hi - lo
    if span <= 0:
        span = max(abs(hi) * 0.1, 1.0)
        return lo - span / 2, hi + span / 2
    return lo - margin * span, hi + margin * span


class AutoRange:
    """Hysteresis state for one axis. ``update(lo, hi, now)`` returns the range to apply (or the unchanged one)."""

    def __init__(self, margin: float = MARGIN, shrink_ratio: float = SHRINK_RATIO,
                 shrink_hold_s: float = SHRINK_HOLD_S) -> None:
        self.margin = margin
        self.shrink_ratio = shrink_ratio
        self.shrink_hold_s = shrink_hold_s
        self.range: tuple[float, float] | None = None
        self._small_since: float | None = None

    def reset(self) -> None:
        self.range = None
        self._small_since = None

    def update(self, bounds: tuple[float, float] | None, now: float) -> tuple[float, float] | None:
        if bounds is None:
            return self.range
        lo, hi = bounds
        if self.range is None:
            self.range = padded(lo, hi, self.margin)
            self._small_since = None
            return self.range
        rlo, rhi = self.range
        if lo < rlo or hi > rhi:                      # expand immediately; the side still inside is kept
            span = max(hi, rhi) - min(lo, rlo)
            nlo = lo - self.margin * span if lo < rlo else rlo
            nhi = hi + self.margin * span if hi > rhi else rhi
            self.range = (nlo, nhi)
            self._small_since = None
            return self.range
        span = hi - lo
        rspan = rhi - rlo
        if rspan > 0 and span < self.shrink_ratio * rspan:
            if self._small_since is None:
                self._small_since = now
            elif now - self._small_since >= self.shrink_hold_s:
                self.range = padded(lo, hi, self.margin)
                self._small_since = None
        else:
            self._small_since = None
        return self.range
