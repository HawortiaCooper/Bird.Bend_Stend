"""Load-target steps: approach + trim arithmetic (R4 §8.4, SRS SW-SEQ-006/007, D-32, D-33 d) — pure functions.

* ``approach_band(tol, k_est, v)`` = max(tol, k_est·v·0.065 s) — the overshoot budget of the FW-timed stop (R4 §4.6).
* ``force_to_raw_stop(F_stop, K, tare, sgn)`` → ``(raw_stop, cmp)``: the raw threshold of MOVE_UNTIL_LOAD, rounded so
  the FW stops **no later** than F_stop; ``cmp`` 0 (stop when raw ≥ raw_stop) when the raw value rises toward the
  target (sgn·sign(K) > 0), else 1 (raw ≤ raw_stop) (ICD §5.4, F-B-04).
* ``trim_step(F̄, F_target, k_est, kp, max_step)`` = clamp(kp·(F_target − F̄)/k_est, ±max_step) (R4 TV-C).
* ``k_est_from_approach(x, F, pull_dir, …)`` — OLS slope dF/dx of the loaded part of the approach (x span ≥ 0.1 mm),
  sign-normalised by ``pull_dir``, clamped to [k_min, k_max]; fallback the user value.
* ``step_timeout_s(distance, v, a)`` = 1.2 × trapezoid travel time + 10 s (SRS vector: 160 mm at 2 mm/s, 100 mm/s²
  → 80.02 s → 106.024 s); ``hold_timeout_s(planned)`` = 3 × planned + 10 s.

Implements: SW-SEQ-006, SW-SEQ-007 (timeouts)
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from bend_stand.calc.motion import move_duration_s

T_REACT_S = 0.065                       # force crossing → stop (R4 §4.6, SRS A-13)
KP_DEFAULT = 0.5
MAX_STEP_MM = 0.2
TRIM_V_MM_S = 0.2
MAX_ITER = 10
SETTLE_AFTER_DONE_S = 0.100             # trim: MOVE_DONE + 100 ms (HX711 filter settled)
MEAN_SAMPLES = 4                        # mean of the last 4 samples (50 ms at 80 SPS)
K_SPAN_MM = 0.1                         # k_est: OLS over ≥ 0.1 mm of the approach
RAW_STOP_MIN, RAW_STOP_MAX = -8_388_608, 8_388_607
CMP_GE, CMP_LE = 0, 1


def approach_band(tol_n: float, k_est_n_mm: float, v_mm_s: float) -> float:
    return max(float(tol_n), float(k_est_n_mm) * float(v_mm_s) * T_REACT_S)


def force_to_raw(f_n: float, k: float, tare_raw: float) -> float:
    """Inverse of F = K·(raw − tare)."""
    return float(tare_raw) + float(f_n) / float(k)


def force_to_raw_stop(f_stop_n: float, k: float, tare_raw: float, sgn: int) -> tuple[int, int]:
    """MOVE_UNTIL_LOAD threshold for a force change of sign ``sgn`` (+1: F rises toward the target)."""
    if k == 0 or not math.isfinite(k):
        raise ValueError("calibration factor K must be finite and non-zero")
    raw = force_to_raw(f_stop_n, k, tare_raw)
    rising = sgn * (1 if k > 0 else -1) > 0
    if rising:
        cmp_, r = CMP_GE, math.floor(raw)       # raw ≥ floor(raw): triggers at or before F_stop
    else:
        cmp_, r = CMP_LE, math.ceil(raw)
    return max(RAW_STOP_MIN, min(RAW_STOP_MAX, int(r))), cmp_


def trim_step(f_meas_n: float, f_target: float, k_est: float, kp: float = KP_DEFAULT,
              max_step: float = MAX_STEP_MM) -> float:
    """Travel correction in the direction that raises F (multiply by ``pull_dir`` for the machine axis)."""
    if not k_est > 0:
        raise ValueError("k_est must be > 0")
    dx = float(kp) * (float(f_target) - float(f_meas_n)) / float(k_est)
    return max(-float(max_step), min(float(max_step), dx))


def k_est_from_approach(x_mm: Sequence[float] | np.ndarray, f_n: Sequence[float] | np.ndarray, *, pull_dir: int,
                        k_min: float, k_max: float, fallback: float,
                        min_span_mm: float = K_SPAN_MM) -> tuple[float, bool]:
    """→ ``(k_est, measured)``. Uses the samples of the approach in contact (|F − F_first| ≥ 25 % of the total force
    change) when they span ≥ ``min_span_mm``; otherwise the trailing ``min_span_mm``; ``measured`` False = fallback."""
    x = np.asarray(x_mm, dtype=np.float64)
    f = np.asarray(f_n, dtype=np.float64)
    m = np.isfinite(x) & np.isfinite(f)
    x, f = x[m], f[m]
    if x.size < 3:
        return float(fallback), False
    total = f[-1] - f[0]
    sel = np.abs(f - f[0]) >= 0.25 * abs(total) if abs(total) > 0 else np.ones(x.size, bool)
    xs, fs = x[sel], f[sel]
    if xs.size < 3 or float(np.ptp(xs)) < min_span_mm:
        j = x.size - 1
        while j > 0 and abs(x[-1] - x[j]) < min_span_mm:
            j -= 1
        xs, fs = x[j:], f[j:]
    if xs.size < 3 or float(np.ptp(xs)) < min_span_mm:
        return float(fallback), False
    dx = xs - xs.mean()
    sxx = float(np.dot(dx, dx))
    if sxx <= 0:
        return float(fallback), False
    k = float(np.dot(dx, fs - fs.mean())) / sxx * (1 if pull_dir >= 0 else -1)
    if not math.isfinite(k) or k <= 0:
        return float(fallback), False
    return min(max(k, float(k_min)), float(k_max)), True


def move_time_s(distance_mm: float, v_mm_s: float, a_mm_s2: float) -> float:
    """Trapezoid travel time (s) in SW units."""
    if not v_mm_s > 0 or not a_mm_s2 > 0:
        raise ValueError("speed and acceleration must be > 0")
    return move_duration_s(abs(distance_mm) * 1000.0, v_mm_s * 1000.0, a_mm_s2 * 1000.0)


def step_timeout_s(distance_mm: float, v_mm_s: float, a_mm_s2: float) -> float:
    """SW-SEQ-007: 1.2 × planned travel time from the start position to the target / approach bound + 10 s."""
    return 1.2 * move_time_s(distance_mm, v_mm_s, a_mm_s2) + 10.0


def hold_timeout_s(planned_s: float) -> float:
    """SW-SEQ-007: hold steps 3 × planned + 10 s."""
    return 3.0 * float(planned_s) + 10.0


__all__ = ["approach_band", "force_to_raw", "force_to_raw_stop", "trim_step", "k_est_from_approach", "move_time_s",
           "step_timeout_s", "hold_timeout_s", "T_REACT_S", "KP_DEFAULT", "MAX_STEP_MM", "TRIM_V_MM_S", "MAX_ITER",
           "SETTLE_AFTER_DONE_S", "MEAN_SAMPLES", "CMP_GE", "CMP_LE"]
