"""Load-calibration helpers needed by the FW load-threshold manager (SW_design §6.3, R4 §4.4) — pure functions.

``fw_raw_limits`` (R4 TV-T): ``a = tare + f_hi/k``, ``b = tare + f_lo/k`` → ``(ceil(min(a, b)), floor(max(a, b)))``,
i.e. rounded toward the tare (the FW trips no later than the force level). ``clamp_raw_limits`` clamps a computed
pair inward to the dictionary range of ``safety.load_raw_min/max`` (D-29 g, SAF-SW-002: safe side, earlier trip).
The calibration fit itself (OLS, linearity) is M3.

Implements: SAF-SW-002 (threshold computation and clamp), SAF-FW-010 (range)
"""
from __future__ import annotations

import math


def fw_raw_limits(f_hi: float, f_lo: float, k: float, tare_raw: float) -> tuple[int, int]:
    """Raw-count thresholds for the force levels ``f_hi`` / ``f_lo`` (N) with calibration ``k`` (N/count)."""
    if k == 0 or not math.isfinite(k):
        raise ValueError("calibration factor k must be finite and non-zero")
    a = tare_raw + f_hi / k
    b = tare_raw + f_lo / k
    return math.ceil(min(a, b)), math.floor(max(a, b))


def clamp_raw_limits(raw_min: int, raw_max: int, min_lo: int, min_hi: int, max_lo: int,
                     max_hi: int) -> tuple[int, int, bool]:
    """Clamp ``raw_min`` into ``[min_lo, min_hi]`` and ``raw_max`` into ``[max_lo, max_hi]``; returns
    ``(raw_min, raw_max, clamped)``. Raises ``ValueError`` when the clamped pair is empty (``raw_min ≥ raw_max``)."""
    lo = min(max(int(raw_min), min_lo), min_hi)
    hi = min(max(int(raw_max), max_lo), max_hi)
    if lo >= hi:
        raise ValueError(f"threshold pair empty after clamping ({lo} ≥ {hi})")
    return lo, hi, (lo, hi) != (int(raw_min), int(raw_max))


def effective_force_n(raw: int, k: float, tare_raw: float) -> float:
    """Force (N) that a raw threshold corresponds to (``F = k·(raw − tare)``), for the clamp warning text."""
    return k * (raw - tare_raw)
