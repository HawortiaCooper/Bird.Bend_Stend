"""Tare and runtime force — pure functions (R4 §7, TV-T; SRS SW-TARE-002/003).

* ``force_n(raw, k, tare_raw) = k·(raw − tare_raw)`` [N] (identical to ``(k·raw + b) − (k·tare + b)``: B drops out).
  Works element-wise on numpy arrays.
* ``tare_acceptance`` — the R4 §7 refusals evaluated on the finished capture window (``calc.stats.window_acceptance``
  with the tare rules: lost frames ≤ 1 %, std vs the calibration's zero-point std, drift, outliers, saturation).
* ``tare_offset_warning`` — ``|k·(tare_raw − raw_zero_cal)| > 10 % FS`` ("large offset — specimen loaded?").

Implements: SW-TARE-002, SW-TARE-003, SW-RT-004 (force channel)
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from bend_stand.calc.stats import WindowResult, window_acceptance
from bend_stand.calc.units import FS_N

OFFSET_WARN_FRAC = 0.10


def force_n(raw: float | np.ndarray, k: float, tare_raw: float) -> float | np.ndarray:
    """Runtime force (N) from raw counts with calibration ``k`` (N/count) and the session tare."""
    return k * (raw - tare_raw)


def tare_acceptance(raw: Sequence[float] | np.ndarray, t_s: Sequence[float] | np.ndarray, *, window_s: float,
                    n_nominal: int, n_saturated: int = 0, n_lost: int = 0,
                    std_zero_cal: float | None = None) -> WindowResult:
    """R4 §7 refusal rules of a finished tare window (``.ok`` → accepted; ``.texts`` = verbatim reasons)."""
    return window_acceptance(raw, t_s, window_s=window_s, n_nominal=n_nominal, n_saturated=n_saturated,
                             n_lost=n_lost, std_ref=std_zero_cal, require_count=False, lost_limit=True)


def tare_offset_warning(k: float | None, tare_raw: float, raw_zero_cal: float | None) -> bool:
    """True when the tare differs from the calibration's zero point by more than 10 % FS in force."""
    if k is None or raw_zero_cal is None:
        return False
    return abs(k * (tare_raw - raw_zero_cal)) > OFFSET_WARN_FRAC * FS_N


__all__ = ["force_n", "tare_acceptance", "tare_offset_warning", "OFFSET_WARN_FRAC"]
