"""Capture-window statistics (R4 §6.1, §7, §12 TV-RS) — pure functions, numpy only.

* ``robust_window_stats`` — median, MAD, σ_MAD = 1.4826·MAD; samples with ``|x − med| > k·σ_MAD`` are rejected
  (k = 5); mean / std (ddof = 1) of the kept samples. A MAD floor (default 1 count: HX711 counts are integers)
  keeps a constant signal from rejecting a 1-count step; the reported ``mad`` is the true MAD.
* ``lag1_autocorr`` / ``se_ar1`` — lag-1 autocorrelation and the AR(1) standard error ``SE = std/√N_eff`` with
  ``N_eff = N·(1 − ρ₁)/(1 + ρ₁)``, ρ₁ clipped to [0, 0.99] for N_eff (S6 §9.1).
* ``drift_slope`` — OLS slope of x vs t (units of x per unit of t).
* ``window_acceptance`` — the acceptance rules shared by calibration points (SW-CAL-006) and tare (SW-TARE-003):
  saturated samples, outlier fraction > 2 %, too few samples, ``|drift| > max(2·std, 20)``,
  ``std > max(3·std_ref, 50)``, lost frames > 1 %.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/calc/stats.py @37c87471 (task pin; read from the TS working tree, HEAD
e14a29d): lag1_autocorr / n_eff / OLS-slope pattern, adapted to the bend-stand R4 §6.1 rules; MAD rejection new.

Implements: SW-CAL-006 (point statistics), SW-TARE-002/003 (tare statistics), SW-ACQ-003 (sample statistics)
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

MAD_TO_SIGMA = 1.4826
NAN = float("nan")


def _vec(x: Sequence[float] | np.ndarray) -> np.ndarray:
    return np.asarray(x, dtype=np.float64).ravel()


@dataclass(frozen=True)
class RobustStats:
    median: float
    mad: float
    sigma_mad: float
    mean: float
    std: float
    n_used: int
    n_rejected: int
    kept: np.ndarray = field(repr=False, compare=False, default_factory=lambda: np.zeros(0, bool))

    @property
    def n(self) -> int:
        return self.n_used + self.n_rejected

    @property
    def rejected_frac(self) -> float:
        return self.n_rejected / self.n if self.n else 0.0


def robust_window_stats(x: Sequence[float] | np.ndarray, k: float = 5.0, *, mad_floor: float = 1.0) -> RobustStats:
    """R4 §6.1 steps 2–3 (TV-RS). NaN samples are ignored. ``ValueError`` for an empty window."""
    v = _vec(x)
    v = v[np.isfinite(v)]
    if v.size == 0:
        raise ValueError("empty capture window")
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    sigma = MAD_TO_SIGMA * mad
    thr = float(k) * MAD_TO_SIGMA * max(mad, float(mad_floor))
    kept = np.abs(v - med) <= thr
    used = v[kept]
    mean = float(used.mean())
    std = float(used.std(ddof=1)) if used.size > 1 else NAN
    return RobustStats(med, mad, sigma, mean, std, int(used.size), int(v.size - used.size), kept)


def lag1_autocorr(x: Sequence[float] | np.ndarray) -> float:
    """Lag-1 autocorrelation (NaN for n < 3 or zero variance)."""
    v = _vec(x)
    v = v[np.isfinite(v)]
    if v.size < 3:
        return NAN
    d = v - v.mean()
    den = float(np.dot(d, d))
    return float(np.dot(d[:-1], d[1:])) / den if den > 0 else NAN


def n_eff(n: int, rho1: float) -> float:
    r = 0.0 if math.isnan(rho1) else min(max(float(rho1), 0.0), 0.99)
    return float(n) * (1.0 - r) / (1.0 + r)


def se_ar1(y: Sequence[float] | np.ndarray) -> tuple[float, float, float]:
    """``(ρ₁, N_eff, SE)`` of the mean under an AR(1) model (TV-RS ``test_se_ar1``)."""
    v = _vec(y)
    v = v[np.isfinite(v)]
    rho1 = lag1_autocorr(v)
    ne = n_eff(int(v.size), rho1)
    if v.size < 2 or ne <= 0:
        return rho1, ne, NAN
    return rho1, ne, float(v.std(ddof=1)) / math.sqrt(ne)


def drift_slope(t: Sequence[float] | np.ndarray, x: Sequence[float] | np.ndarray) -> float:
    """OLS slope of ``x`` vs ``t`` (NaN with fewer than 2 distinct times)."""
    tv, xv = _vec(t), _vec(x)
    m = np.isfinite(tv) & np.isfinite(xv)
    tv, xv = tv[m], xv[m]
    if tv.size < 2:
        return NAN
    dt = tv - tv.mean()
    stt = float(np.dot(dt, dt))
    return float(np.dot(dt, xv - xv.mean())) / stt if stt > 0 else NAN


#: acceptance limits (R4 §6.1 / §7)
OUTLIER_FRAC_MAX = 0.02
MIN_FRAC_OF_NOMINAL = 0.95
DRIFT_FLOOR_COUNTS = 20.0
STD_FLOOR_COUNTS = 50.0
LOST_FRAC_MAX = 0.01


@dataclass(frozen=True)
class WindowResult:
    """Statistics + verdict of one capture window (calibration point, tare, take-sample)."""

    stats: RobustStats | None
    se: float
    drift: float                   # counts over the window (slope × window length)
    n_nominal: int
    n_saturated: int
    n_lost: int
    reasons: tuple[str, ...]       # empty = accepted
    texts: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.reasons


def window_acceptance(raw: Sequence[float] | np.ndarray, t_s: Sequence[float] | np.ndarray, *,
                      window_s: float, n_nominal: int, n_saturated: int = 0, n_lost: int = 0,
                      std_ref: float | None = None, require_count: bool = True,
                      lost_limit: bool = False, k: float = 5.0) -> WindowResult:
    """Apply the R4 §6.1 / §7 rules to a capture window.

    ``require_count`` (calibration points): N ≥ 95 % of ``n_nominal``. ``lost_limit`` (tare): lost frames ≤ 1 % of the
    received + lost frames. ``std_ref`` = std of the zero point (calibration) / of the active calibration's zero
    point (tare); ``None`` → only the 50-count floor applies."""
    reasons: list[str] = []
    texts: list[str] = []
    v = _vec(raw)
    if n_saturated > 0:
        reasons.append("SATURATED")
        texts.append(f"{n_saturated} saturated sample(s) in the window")
    if v.size == 0:
        reasons.append("NO_SAMPLES")
        texts.append("no samples captured")
        return WindowResult(None, NAN, NAN, n_nominal, n_saturated, n_lost, tuple(reasons), tuple(texts))
    st = robust_window_stats(v, k)
    if st.rejected_frac > OUTLIER_FRAC_MAX:
        reasons.append("OUTLIERS")
        texts.append(f"{100 * st.rejected_frac:.1f} % outliers (> 2 %) — interference or glitches, repeat")
    if require_count and v.size < MIN_FRAC_OF_NOMINAL * n_nominal:
        reasons.append("TOO_FEW")
        texts.append(f"{v.size} samples < 95 % of the nominal {n_nominal}")
    if lost_limit and n_lost > LOST_FRAC_MAX * (v.size + n_lost):
        reasons.append("LOST_FRAMES")
        texts.append(f"{n_lost} lost frames (> 1 %)")
    fin = np.isfinite(v)
    tv = _vec(t_s)
    kept_x = v[fin][st.kept]
    kept_t = tv[fin][st.kept] if tv.size == v.size else np.zeros(0)
    slope = drift_slope(kept_t, kept_x) if kept_t.size == kept_x.size else NAN
    drift = 0.0 if math.isnan(slope) else slope * float(window_s)
    std = 0.0 if math.isnan(st.std) else st.std
    if abs(drift) > max(2.0 * std, DRIFT_FLOOR_COUNTS):
        reasons.append("DRIFT")
        texts.append(f"drift {drift:.0f} counts over the window > max(2·std, 20) — load still settling, repeat")
    std_lim = max(3.0 * std_ref, STD_FLOOR_COUNTS) if std_ref is not None and math.isfinite(std_ref) else None
    if std_lim is not None and std > std_lim:
        reasons.append("NOISY")
        texts.append(f"std {std:.1f} counts > {std_lim:.1f} (vibration / swinging weight), repeat")
    _rho, _ne, se = se_ar1(kept_x)
    return WindowResult(st, se, drift, n_nominal, n_saturated, n_lost, tuple(reasons), tuple(texts))


__all__ = ["RobustStats", "robust_window_stats", "lag1_autocorr", "n_eff", "se_ar1", "drift_slope", "WindowResult",
           "window_acceptance", "MAD_TO_SIGMA", "OUTLIER_FRAC_MAX", "MIN_FRAC_OF_NOMINAL", "DRIFT_FLOOR_COUNTS",
           "STD_FLOOR_COUNTS", "LOST_FRAC_MAX"]
