"""Load calibration and FW load-threshold helpers (SW_design §6.3, §9.4; R4 §4.4, §6) — pure functions.

* ``load_calibration(raw_means, masses_kg)`` (R4 TV-LC): OLS ``F = K·raw + B`` over the zero point and the known
  weights (``F = m·g``), residuals, R² (informative only), ``NL_span = 100·max|r|/F_span``, ``NL_FS`` and the status
  **PASS** (≤ 0.1 % span) / **WARN** (≤ 0.5 %) / **FAIL** (> 0.5 %, K ≈ 0 or non-monotonic points — "points are not
  linear") / **UNVERIFIED_LINEARITY** (2 points). Negative K is accepted (the sign is carried by K, R4 §0.2).
  All raw means equal → ``ValueError`` (degenerate).
* ``mass_rules`` (SW-CAL-006): weight masses > 0, increasing, each ≥ 1.5 × the previous weight; the first weight
  < 2 % FS is a warning only (D-22: 1 kg + 10 kg accepted).
* ``low_span`` / ``extrapolated`` (SW-CAL-008): LOW_SPAN when the largest reference force < 20 % FS; forces beyond
  3 × the largest calibration force are marked extrapolated.
* ``afe_block`` / ``afe_matches`` (SW-CAL-009, D-29 l): the AFE configuration recorded with a calibration.
* **Plausibility (D-50 a, SRS v0.6.5 SW-CAL-007)**, for any number of points: ``weight_detected`` — a weight point
  whose raw change from the zero point **and** from the previous point is < 10 × std_zero (min 50 counts) is refused
  ("weight not detected", re-take); ``nominal_k`` — the nominal |K| of the configured cell / AFE:
  ``|K_nom| = FS_N / (S/1000 · gain · 2^24)`` N/count (S = cell sensitivity mV/V, SRS A-02 3.0 mV/V; FS_N = rated
  capacity 200 kg · g0; gain = HX711 PGA gain of ``afe.gain_channel``; the HX711 is ratiometric — bridge excitation
  = its reference — so the reference voltage cancels: full scale ±0.5·VREF/gain = ±2^23 counts). At gain 128:
  3284.7 counts/N. ``k_plausibility`` — |K| outside 0.5…2 × |K_nom| → ``IMPLAUSIBLE`` (explicit operator
  confirmation, recorded); no derivable nominal (unknown gain) → ``NOMINAL_UNKNOWN`` (check skipped, recorded).
  No sign check: a negative K stays legal (wiring polarity).
* ``fw_raw_limits`` (R4 TV-T): ``a = tare + f_hi/k``, ``b = tare + f_lo/k`` → ``(ceil(min(a, b)), floor(max(a, b)))``,
  rounded toward the tare (the FW trips no later than the force level); ``clamp_raw_limits`` clamps inward to the
  dictionary range (D-29 g, SAF-SW-002: safe side, earlier trip).

Implements: SW-CAL-006, SW-CAL-007, SW-CAL-008, SW-CAL-009 (AFE block), SAF-SW-002 (threshold computation and
clamp), SAF-FW-010 (range)
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bend_stand.calc.units import CELL_SENS_MV_V, FS_N, G0, HX711_FULL_RANGE_COUNTS

PASS_PCT = 0.1
WARN_PCT = 0.5
LOW_SPAN_FRAC = 0.20
EXTRAPOLATION_FACTOR = 3.0
M1_MIN_FRAC_FS = 0.02
MASS_RATIO_MIN = 1.5
K_ZERO_FRAC = 0.01            # fit explains < 1 % of the force span → "K ≈ 0"
WEIGHT_DETECT_STD = 10.0      # D-50 a: a weight point must move the raw value by ≥ 10 × std_zero …
WEIGHT_DETECT_MIN_COUNTS = 50.0   # … and by at least 50 counts
K_PLAUSIBLE_LO, K_PLAUSIBLE_HI = 0.5, 2.0   # D-50 a: |K| / |K_nom| outside → "K implausible" (confirmation)

#: ``afe.gain_channel`` code → (channel, gain); ``afe.rate_sps`` code → SPS (params dict 5)
AFE_GAIN = {0: ("A", 128), 2: ("A", 64), 1: ("B", 32)}
AFE_RATE = {0: 10, 1: 80}


@dataclass(frozen=True)
class LoadCalResult:
    K: float                       # N/count
    B: float                       # N (diagnostic; the tare replaces it at runtime)
    residuals: tuple[float, ...]   # N, per point
    r2: float
    nl_pct_span: float
    nl_pct_fs: float
    status: str                    # PASS | WARN | FAIL | UNVERIFIED_LINEARITY
    f_span_n: float
    low_span: bool
    linear_within_noise: bool | None
    texts: tuple[str, ...] = ()
    points: tuple[tuple[float, float, float, float], ...] = ()   # (mass kg, reference force N, raw mean, residual N)

    @property
    def k(self) -> float:
        return self.K

    @property
    def b(self) -> float:
        return self.B

    def as_fit(self) -> dict[str, Any]:
        """``fit`` block of the calibration file (R4 §6.4)."""
        return {"k_n_per_count": self.K, "b_n": self.B, "residuals_n": list(self.residuals), "r2": self.r2,
                "nl_pct_span": self.nl_pct_span, "nl_pct_fs": self.nl_pct_fs, "status": self.status,
                "f_span_n": self.f_span_n, "low_span": self.low_span,
                "linear_within_noise": self.linear_within_noise}

    def point_table(self) -> list[dict[str, float]]:
        """Per-point table for the wizard / report (GRQ-B-25)."""
        return [{"mass_kg": m, "force_n": f, "raw_mean": r, "residual_n": e} for m, f, r, e in self.points]


def load_calibration(raw_means: Sequence[float], masses_kg: Sequence[float], *, g: float = G0,
                     point_se_counts: Sequence[float] | None = None) -> LoadCalResult:
    """OLS fit and linearity classification (R4 §6.2, TV-LC). ``point_se_counts`` (optional) → u_r = |K|·SE for
    the "linear within noise" note."""
    x = np.asarray(raw_means, dtype=np.float64).ravel()
    m = np.asarray(masses_kg, dtype=np.float64).ravel()
    if x.size != m.size:
        raise ValueError("raw means and masses differ in length")
    if x.size < 2:
        raise ValueError("at least two points (zero + one weight) are needed")
    if not (np.isfinite(x).all() and np.isfinite(m).all()):
        raise ValueError("non-finite calibration value")
    y = m * float(g)
    xm, ym = float(x.mean()), float(y.mean())
    dx, dy = x - xm, y - ym
    sxx = float(np.dot(dx, dx))
    if sxx == 0.0:
        raise ValueError("all raw means are equal — the cell does not respond (degenerate calibration)")
    k = float(np.dot(dx, dy)) / sxx
    b = ym - k * xm
    res = y - (k * x + b)
    ss_res = float(np.dot(res, res))
    ss_tot = float(np.dot(dy, dy))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    f_span = float(np.max(np.abs(y)))
    max_r = float(np.max(np.abs(res)))
    nl_span = 100.0 * max_r / f_span if f_span > 0 else float("inf")
    nl_fs = 100.0 * max_r / FS_N
    texts: list[str] = []
    order = np.argsort(y, kind="stable")
    steps = np.diff(x[order])
    monotonic = bool(np.all(steps * math.copysign(1.0, k) > 0)) if k != 0 else False
    k_zero = k == 0.0 or abs(k) * float(np.ptp(x)) < K_ZERO_FRAC * max(f_span, 1e-300)
    if k_zero:
        status = "FAIL"
        texts.append("K ≈ 0: the cell output does not follow the load — points are not linear")
    elif not monotonic:
        status = "FAIL"
        texts.append("points are not monotonic — points are not linear (check fixture, weights, hook)")
    elif x.size == 2:
        status = "UNVERIFIED_LINEARITY"
        texts.append("2-point calibration: linearity not verified")
    elif nl_span <= PASS_PCT:
        status = "PASS"
    elif nl_span <= WARN_PCT:
        status = "WARN"
        texts.append(f"non-linearity {nl_span:.3f} % of span (> 0.1 %): accept only if expected")
    else:
        status = "FAIL"
        texts.append(f"non-linearity {nl_span:.3f} % of span > 0.5 %: points are not linear")
    within: bool | None = None
    if point_se_counts is not None and len(point_se_counts) == x.size and x.size > 2:
        se = np.asarray(point_se_counts, dtype=np.float64)
        if np.isfinite(se).all():
            within = bool(max_r <= 3.0 * abs(k) * float(np.max(se)))
            if within and status in ("PASS", "WARN"):
                texts.append("linear within noise")
    low = low_span(f_span)
    if low:
        texts.append(f"LOW_SPAN: largest reference force {f_span:.1f} N < 20 % FS — forces above "
                     f"{EXTRAPOLATION_FACTOR * f_span:.0f} N are extrapolated")
    pts = tuple((float(mi), float(yi), float(xi), float(ri)) for mi, yi, xi, ri in zip(m, y, x, res, strict=True))
    return LoadCalResult(k, b, tuple(float(r) for r in res), r2, nl_span, nl_fs, status, f_span, low, within,
                         tuple(texts), pts)


def weight_threshold_counts(std_zero_counts: float | None) -> float:
    """D-50 a: minimum raw change of a weight point = max(10 × std_zero, 50 counts) (std unknown → 50)."""
    s = float(std_zero_counts) if std_zero_counts is not None and math.isfinite(float(std_zero_counts)) else 0.0
    return max(WEIGHT_DETECT_STD * abs(s), WEIGHT_DETECT_MIN_COUNTS)


def weight_detected(raw_point: float, raw_zero: float, raw_prev: float | None,
                    std_zero_counts: float | None) -> tuple[bool, str]:
    """D-50 a (SW-CAL-006/007): ``(ok, text)`` — the weight point must differ from the zero point and from the
    previous point (``raw_prev``; None or the zero point itself for the first weight) by ≥ the threshold."""
    # Implements: SW-CAL-006, SW-CAL-007 (plausibility, D-50 a)
    thr = weight_threshold_counts(std_zero_counts)
    d_zero = abs(float(raw_point) - float(raw_zero))
    d_prev = d_zero if raw_prev is None else abs(float(raw_point) - float(raw_prev))
    if d_zero < thr or d_prev < thr:
        which = "the zero point" if d_zero < thr else "the previous point"
        return False, (f"weight not detected: raw change {min(d_zero, d_prev):.0f} counts from {which} < "
                       f"{thr:.0f} counts (10 × std_zero, min 50) — hang the weight and re-take the point")
    return True, ""


def nominal_k(gain: int | float | None, *, sens_mv_v: float = CELL_SENS_MV_V, fs_n: float = FS_N) -> float | None:
    """Nominal |K| (N/count) of the configured cell + HX711: ``fs_n / (sens/1000 · gain · 2^24)``; None when the
    gain (or sensitivity / capacity) is unknown."""
    try:
        g, s, f = float(gain), float(sens_mv_v), float(fs_n)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(g) and g > 0 and math.isfinite(s) and s > 0 and math.isfinite(f) and f > 0):
        return None
    return f / (s / 1000.0 * g * HX711_FULL_RANGE_COUNTS)


@dataclass(frozen=True)
class KPlausibility:
    status: str                    # OK | IMPLAUSIBLE | NOMINAL_UNKNOWN
    k_nominal: float | None        # |K_nom| N/count
    ratio: float | None            # |K| / |K_nom|
    text: str = ""

    def as_dict(self, confirmed: bool = False) -> dict[str, Any]:
        """``plausibility`` block of the calibration file (D-50 a: the confirmation is recorded)."""
        return {"k_status": self.status, "k_nominal_n_per_count": self.k_nominal, "k_ratio": self.ratio,
                "k_range": [K_PLAUSIBLE_LO, K_PLAUSIBLE_HI], "confirmed": bool(confirmed),
                "nominal_formula": "FS_N / (S_mV_V/1000 * gain * 2^24)", "text": self.text}


def k_plausibility(k: float, k_nominal: float | None) -> KPlausibility:
    """D-50 a (SW-CAL-007): |K| within 0.5…2 × |K_nom| → OK, else IMPLAUSIBLE (operator confirmation); no nominal →
    NOMINAL_UNKNOWN ("nominal unknown", check skipped). The sign of K is not checked."""
    # Implements: SW-CAL-007 (K plausibility, D-50 a)
    if k_nominal is None or not math.isfinite(k_nominal) or k_nominal == 0:
        return KPlausibility("NOMINAL_UNKNOWN", None, None, "nominal unknown: K plausibility not checked")
    r = abs(float(k)) / abs(float(k_nominal))
    if not math.isfinite(r) or not K_PLAUSIBLE_LO <= r <= K_PLAUSIBLE_HI:
        return KPlausibility("IMPLAUSIBLE", abs(float(k_nominal)), r,
                             f"K implausible: |K| = {abs(k):.6g} N/count is {r:.3g} × the nominal "
                             f"{abs(k_nominal):.6g} N/count of the configured cell / AFE (expected 0.5…2 ×) — "
                             "check the weights, the cell and the AFE gain")
    return KPlausibility("OK", abs(float(k_nominal)), r)


def mass_rules(masses_kg: Sequence[float]) -> tuple[list[str], list[str]]:
    """Weight masses of the points after the zero point → ``(errors, warnings)`` (SW-CAL-006, D-22)."""
    errors: list[str] = []
    warnings: list[str] = []
    w = [float(v) for v in masses_kg]
    for i, v in enumerate(w):
        if not math.isfinite(v) or v <= 0:
            errors.append(f"weight {i + 1}: mass must be > 0 kg")
    if errors:
        return errors, warnings
    for i in range(1, len(w)):
        if w[i] <= w[i - 1]:
            errors.append(f"weight {i + 1}: masses must increase ({w[i]:g} kg ≤ {w[i - 1]:g} kg)")
        elif w[i] < MASS_RATIO_MIN * w[i - 1]:
            errors.append(f"weight {i + 1}: {w[i]:g} kg < 1.5 × {w[i - 1]:g} kg")
    if w and w[0] * G0 < M1_MIN_FRAC_FS * FS_N:
        warnings.append(f"first weight {w[0]:g} kg < 2 % FS ({M1_MIN_FRAC_FS * FS_N / G0:g} kg): accepted (D-22), "
                        "lower accuracy near zero")
    return errors, warnings


def low_span(f_span_n: float) -> bool:
    """SW-CAL-008: largest reference force < 20 % FS."""
    return abs(f_span_n) < LOW_SPAN_FRAC * FS_N


def extrapolated(f_n: float, f_cal_max_n: float | None) -> bool:
    """SW-CAL-008: displayed force beyond 3 × the largest calibration force."""
    if f_cal_max_n is None or not math.isfinite(f_n):
        return False
    return abs(f_n) > EXTRAPOLATION_FACTOR * abs(f_cal_max_n)


def afe_block(gain_code: int | None, rate_code: int | None) -> dict[str, Any]:
    """AFE configuration block of a calibration file from the board parameters (R4 §6.4)."""
    ch, gain = AFE_GAIN.get(int(gain_code) if gain_code is not None else -1, ("?", 0))
    return {"type": "HX711", "channel": ch, "gain": gain,
            "rate_sps": AFE_RATE.get(int(rate_code) if rate_code is not None else -1, 0)}


def afe_matches(cal_afe: Mapping[str, Any] | None, board_afe: Mapping[str, Any]) -> bool:
    """Same channel, gain and rate (D-29 l: a mismatch invalidates the calibration for the load limits)."""
    if not cal_afe:
        return False
    return all(cal_afe.get(k) == board_afe.get(k) for k in ("channel", "gain", "rate_sps"))


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


__all__ = ["LoadCalResult", "load_calibration", "mass_rules", "low_span", "extrapolated", "afe_block", "afe_matches",
           "fw_raw_limits", "clamp_raw_limits", "effective_force_n", "PASS_PCT", "WARN_PCT", "AFE_GAIN", "AFE_RATE",
           "weight_threshold_counts", "weight_detected", "nominal_k", "k_plausibility", "KPlausibility"]
