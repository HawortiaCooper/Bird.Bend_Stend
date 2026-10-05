"""Derived channels (R4 §10, TV-D; SRS SW-RT-004, SW-REP-004) — pure functions on numpy arrays.

Array functions (``centered`` results, NaN where undefined; the live pipeline evaluates the same functions on its
trailing window, so the live value is the value of the window ending at the newest sample):

* ``speed`` — central difference of x over ±2 samples using the device time.
* ``force_rate`` — derivative over 9 contiguous samples (Savitzky–Golay, order 2 = OLS slope for a symmetric window).
* ``stiffness_ols`` → ``(k, b)``; ``stiffness_tangent`` — OLS dF/dx over the shortest trailing window with
  ``|Δx| ≥ 0.05 mm`` (NaN at standstill: no division by noise); ``stiffness_secant`` — ``F/x`` (NaN for
  ``|x| < 0.05 mm``).
* ``work_trapz`` / ``work_cumulative`` — trapezoid ∫F dx (N·mm = mJ).
* ``running_peak`` (max |F|), ``break_index`` (first drop > 20 % below the running peak).
* ``rolling_std``, ``sample_rate``.
* ``bend3p_stress`` σ = 3FL/(2bh²), ``bend3p_strain`` ε = 6δh/L², ``bend3p_modulus`` E_f = L³·m/(4bh³) (MPa with N,
  mm; ASTM D790 / ISO 178).

Implements: SW-RT-004, SW-REP-004
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

NAN = float("nan")
MIN_DX_MM = 0.05
FORCE_RATE_N = 9
SPEED_HALF = 2
BREAK_DROP = 0.20


def _v(x: Sequence[float] | np.ndarray) -> np.ndarray:
    return np.asarray(x, dtype=np.float64).ravel()


def ols_slope(x: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray) -> float:
    xv, yv = _v(x), _v(y)
    if xv.size < 2 or not (np.isfinite(xv).all() and np.isfinite(yv).all()):
        return NAN
    dx = xv - xv.mean()
    sxx = float(np.dot(dx, dx))
    return float(np.dot(dx, yv - yv.mean())) / sxx if sxx > 0 else NAN


def stiffness_ols(x_mm: Sequence[float], f_n: Sequence[float]) -> tuple[float, float]:
    """OLS ``F = k·x + b`` → ``(k [N/mm], b [N])`` (TV-D: k = 49.9, b = 0.04)."""
    xv, fv = _v(x_mm), _v(f_n)
    k = ols_slope(xv, fv)
    return k, float(fv.mean() - k * xv.mean()) if math.isfinite(k) else NAN


def speed(t_s: Sequence[float], x_mm: Sequence[float], half: int = SPEED_HALF) -> np.ndarray:
    """Central difference over ±``half`` samples (NaN at the edges / zero time span)."""
    t, x = _v(t_s), _v(x_mm)
    out = np.full(t.size, np.nan)
    if t.size > 2 * half:
        dt = t[2 * half:] - t[:-2 * half]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[half:-half] = np.where(dt > 0, (x[2 * half:] - x[:-2 * half]) / dt, np.nan)
    return out


def force_rate(t_s: Sequence[float], f_n: Sequence[float], n: int = FORCE_RATE_N) -> np.ndarray:
    """Centred derivative over ``n`` samples (OLS slope; NaN if the window holds a NaN)."""
    t, f = _v(t_s), _v(f_n)
    out = np.full(t.size, np.nan)
    h = n // 2
    for i in range(h, t.size - h):
        out[i] = ols_slope(t[i - h:i + h + 1], f[i - h:i + h + 1])
    return out


def stiffness_tangent(x_mm: Sequence[float], f_n: Sequence[float], min_dx: float = MIN_DX_MM,
                      max_n: int = 400) -> np.ndarray:
    """Trailing-window tangent stiffness (N/mm): the shortest window ending at ``i`` with ``|x_i − x_j| ≥ min_dx``."""
    x, f = _v(x_mm), _v(f_n)
    out = np.full(x.size, np.nan)
    for i in range(x.size):
        j0 = max(0, i - max_n)
        for j in range(i - 1, j0 - 1, -1):
            if abs(x[i] - x[j]) >= min_dx:
                out[i] = ols_slope(x[j:i + 1], f[j:i + 1])
                break
    return out


def stiffness_secant(f_n: float | np.ndarray, x_test_mm: float | np.ndarray,
                     min_x: float = MIN_DX_MM) -> float | np.ndarray:
    """``F / x`` from the test zero; NaN for ``|x| < min_x``."""
    f = np.asarray(f_n, dtype=np.float64)
    x = np.asarray(x_test_mm, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(np.abs(x) >= min_x, f / np.where(x == 0, 1.0, x), np.nan)
    return float(r) if r.ndim == 0 else r


def work_trapz(x_mm: Sequence[float], f_n: Sequence[float]) -> float:
    """Trapezoid ∫F dx (N·mm) (TV-D: 4.01)."""
    x, f = _v(x_mm), _v(f_n)
    if x.size < 2:
        return 0.0
    return float(np.sum(0.5 * (f[1:] + f[:-1]) * np.diff(x)))


def work_cumulative(x_mm: Sequence[float], f_n: Sequence[float]) -> np.ndarray:
    x, f = _v(x_mm), _v(f_n)
    out = np.zeros(x.size)
    if x.size > 1:
        out[1:] = np.cumsum(0.5 * (f[1:] + f[:-1]) * np.diff(x))
    return out


def running_peak(f_n: Sequence[float]) -> np.ndarray:
    """Running maximum of |F| (NaN samples skipped)."""
    a = np.abs(_v(f_n))
    a = np.where(np.isfinite(a), a, 0.0)
    return np.maximum.accumulate(a) if a.size else a


def break_index(f_n: Sequence[float], drop: float = BREAK_DROP, min_peak_n: float = 0.0) -> int | None:
    """Index of the first sample whose |F| is more than ``drop`` below the running peak (peak ≥ ``min_peak_n``)."""
    a = np.abs(_v(f_n))
    peak = 0.0
    for i, v in enumerate(a):
        if not math.isfinite(v):
            continue
        if v > peak:
            peak = v
        elif peak > min_peak_n and peak > 0 and v < (1.0 - drop) * peak:
            return i
    return None


def rolling_std(x: Sequence[float], n: int) -> np.ndarray:
    """Trailing rolling std (ddof = 1) over ``n`` samples (NaN until ``n`` samples)."""
    v = _v(x)
    out = np.full(v.size, np.nan)
    if n >= 2 and v.size >= n:
        w = np.lib.stride_tricks.sliding_window_view(v, n)
        out[n - 1:] = w.std(axis=1, ddof=1)
    return out


def sample_rate(t_s: Sequence[float]) -> np.ndarray:
    """``1/Δt`` per sample (first NaN)."""
    t = _v(t_s)
    out = np.full(t.size, np.nan)
    if t.size > 1:
        d = np.diff(t)
        with np.errstate(divide="ignore"):
            out[1:] = np.where(d > 0, 1.0 / d, np.nan)
    return out


def bend3p_stress(f_n: float, span_mm: float, width_mm: float, thick_mm: float) -> float:
    """Flexural stress σ = 3FL/(2bh²) [MPa]."""
    return 3.0 * f_n * span_mm / (2.0 * width_mm * thick_mm * thick_mm)


def bend3p_strain(deflection_mm: float, span_mm: float, thick_mm: float) -> float:
    """Flexural strain ε = 6δh/L² [-]."""
    return 6.0 * deflection_mm * thick_mm / (span_mm * span_mm)


def bend3p_modulus(span_mm: float, width_mm: float, thick_mm: float, slope_n_mm: float) -> float:
    """Flexural modulus E_f = L³·m/(4bh³) [MPa]."""
    return span_mm ** 3 * slope_n_mm / (4.0 * width_mm * thick_mm ** 3)


__all__ = ["ols_slope", "stiffness_ols", "speed", "force_rate", "stiffness_tangent", "stiffness_secant", "work_trapz",
           "work_cumulative", "running_peak", "break_index", "rolling_std", "sample_rate", "bend3p_stress",
           "bend3p_strain", "bend3p_modulus", "MIN_DX_MM", "FORCE_RATE_N", "BREAK_DROP"]
