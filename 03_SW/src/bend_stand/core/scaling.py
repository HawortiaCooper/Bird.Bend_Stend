"""Scaling and derived-channel stage of the pipeline (SW_design §7.1 stage 5–6, §7.4; R4 §10; SRS SW-RT-004).

``ScaleConfig`` (immutable, swapped atomically by the backend when the calibration, the tare, the test travel zero
or the 3-point-bend geometry change) holds K, tare_raw, the reasons why F is not computable, the largest calibration
force (extrapolation limit, SW-CAL-008), ``x_zero`` and the geometry. ``DerivedStream.process`` turns one DATA
sample into the derived values (``DERIVED_KEYS`` order), the ``calc_reason`` bits and the display state, using the
``calc.derived`` formulas on its trailing window (live values are causal; their lag is documented in §7.4).

Reason bits (``calc_reason``): NO_CAL, NO_TARE, SATURATED, NO_DATA, AFE_MISMATCH (F shown but invalid, D-29 l),
SYNTHETIC, SETTLING, EXTRAPOLATED (value kept), GAP (force rate after a frame gap / missed conversion).

Implements: SW-RT-004 (derived channels), SW-CAL-008 (extrapolated marking), SW-CAL-009 (forces marked invalid on
an AFE mismatch), SW-REP-004 (3-point bend), SW-RT-005 (value states)
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass

import numpy as np

from bend_stand.calc import derived as D
from bend_stand.calc.loadcal import extrapolated
from bend_stand.calc.units import G0
from bend_stand.core.model import Bend3pGeometry

NAN = float("nan")
# calc_reason bits
NO_CAL, NO_TARE, SATURATED, NO_DATA, AFE_MISMATCH, SYNTHETIC, SETTLING, EXTRAPOLATED, GAP = (
    1, 2, 4, 8, 16, 32, 64, 128, 256)
NOT_COMPUTABLE = NO_CAL | NO_TARE | SATURATED | NO_DATA | SYNTHETIC
REASON_TEXT = {NO_CAL: "no load calibration", NO_TARE: "no tare in this session", SATURATED: "AFE saturated",
               NO_DATA: "no AFE data", AFE_MISMATCH: "calibration invalid: AFE configuration differs",
               SYNTHETIC: "AFE synthetic (placeholder samples)", SETTLING: "AFE settling",
               EXTRAPOLATED: "beyond 3 × the largest calibration force", GAP: "frame gap"}
# raw / display states (same codes as core.pipeline)
RAW_OK, RAW_SATURATED, RAW_NO_DATA, RAW_SETTLING = 0, 1, 2, 3
V_OK, V_EXTRAPOLATED, V_INVALID, V_NO_DATA = 0, 1, 2, 3

DERIVED_KEYS: tuple[str, ...] = ("F_N", "F_kgf", "x_test_mm", "speed_mm_s", "force_rate_n_s", "k_tan_n_mm",
                                 "k_sec_n_mm", "work_nmm", "peak_n", "noise_counts", "noise_n", "sigma_mpa", "eps")
NOISE_WINDOW_US = 1_000_000
K_TAN_MAX_N = 200


def reason_text(bits: int) -> str:
    return ", ".join(t for b, t in REASON_TEXT.items() if bits & b) or "ok"


@dataclass(frozen=True)
class ScaleConfig:
    k: float | None = None
    tare_raw: float | None = None
    reason: int = NO_CAL | NO_TARE           # bits that apply to every sample (calibration / tare / AFE state)
    f_cal_max: float | None = None
    x_zero_mm: float = 0.0
    bend3p: Bend3pGeometry | None = None
    compliance_mm_per_n: float = 0.0
    reset_epoch: int = 0                     # changes → work / peak / windows restart (record start, test zero)

    @property
    def force_available(self) -> bool:
        return not self.reason & NOT_COMPUTABLE and self.k is not None and self.tare_raw is not None


class DerivedStream:
    """Per-sample derived values with streaming state (Pipeline thread only)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._xs: collections.deque[tuple[float, float]] = collections.deque(maxlen=5)       # (t, x)
        self._fr: collections.deque[tuple[float, float]] = collections.deque(maxlen=D.FORCE_RATE_N)
        self._kt: collections.deque[tuple[float, float]] = collections.deque(maxlen=K_TAN_MAX_N)
        self._noise: collections.deque[tuple[int, float]] = collections.deque()
        self._work = 0.0
        self._peak = 0.0
        self._prev_xf: tuple[float, float] | None = None
        self._epoch: int | None = None

    def reset_accumulators(self) -> None:
        self._work = 0.0
        self._peak = 0.0
        self._prev_xf = None

    def process(self, sc: ScaleConfig, t_us_u: int, raw: float, raw_state: int, x_mm: float,
                gap: bool) -> tuple[tuple[float, ...], int, int]:
        """→ (values in ``DERIVED_KEYS`` order, calc_reason bits, vstate)."""
        if self._epoch != sc.reset_epoch:
            self._epoch = sc.reset_epoch
            self.reset_accumulators()
        t_s = t_us_u / 1e6
        reason = sc.reason
        if raw_state == RAW_SATURATED:
            reason |= SATURATED
        elif raw_state == RAW_NO_DATA:
            reason |= NO_DATA
        elif raw_state == RAW_SETTLING:
            reason |= SETTLING
        vstate = {RAW_OK: V_OK, RAW_SATURATED: V_INVALID, RAW_SETTLING: V_INVALID}.get(raw_state, V_NO_DATA)
        # force
        f = NAN
        if not reason & NOT_COMPUTABLE and sc.k is not None and sc.tare_raw is not None:
            f = sc.k * (raw - sc.tare_raw)
            if extrapolated(f, sc.f_cal_max):
                reason |= EXTRAPOLATED
                if vstate == V_OK:
                    vstate = V_EXTRAPOLATED
        f_ok = math.isfinite(f)
        x_test = x_mm - sc.x_zero_mm
        # speed: central difference over ±2 samples, live value = centre of the newest 5
        self._xs.append((t_s, x_mm))
        speed = NAN
        if len(self._xs) == 5:
            (t0, x0), (t4, x4) = self._xs[0], self._xs[-1]
            speed = (x4 - x0) / (t4 - t0) if t4 > t0 else NAN
        # force rate: 9 contiguous valid samples
        if gap or not f_ok or reason & SETTLING:
            self._fr.clear()
            if gap:
                reason |= GAP
        if f_ok and not reason & SETTLING:
            self._fr.append((t_s, f))
        f_rate = NAN
        if len(self._fr) == D.FORCE_RATE_N:
            ts, fs = zip(*self._fr, strict=True)
            f_rate = D.ols_slope(ts, fs)
        # tangent stiffness: shortest trailing window with |Δx| ≥ 0.05 mm
        k_tan = NAN
        if f_ok:
            self._kt.append((x_mm, f))
            n = len(self._kt)
            for j in range(n - 2, -1, -1):
                if abs(x_mm - self._kt[j][0]) >= D.MIN_DX_MM:
                    seg = list(self._kt)[j:]
                    xs, fs2 = zip(*seg, strict=True)
                    k_tan = D.ols_slope(xs, fs2)
                    break
        else:
            self._kt.clear()
        k_sec = float(D.stiffness_secant(f, x_test)) if f_ok else NAN
        # work / peak since the last reset
        if f_ok:
            if self._prev_xf is not None:
                px, pf = self._prev_xf
                self._work += 0.5 * (f + pf) * (x_mm - px)
            self._prev_xf = (x_mm, f)
            self._peak = max(self._peak, abs(f))
        else:
            self._prev_xf = None
        work = self._work if sc.force_available else NAN
        peak = self._peak if sc.force_available else NAN
        # noise: rolling std of raw over 1 s
        noise = NAN
        if raw_state in (RAW_OK, RAW_SETTLING):
            self._noise.append((t_us_u, float(raw)))
        while self._noise and self._noise[0][0] <= t_us_u - NOISE_WINDOW_US:
            self._noise.popleft()
        if len(self._noise) >= 3:
            noise = float(np.std([v for _t, v in self._noise], ddof=1))
        noise_n = abs(sc.k) * noise if sc.k is not None and math.isfinite(noise) else NAN
        # 3-point bend (optional)
        sigma = eps = NAN
        g = sc.bend3p
        if g is not None and f_ok:
            sigma = D.bend3p_stress(f, g.span_mm, g.width_mm, g.thickness_mm)
            eps = D.bend3p_strain(x_test - sc.compliance_mm_per_n * f, g.span_mm, g.thickness_mm)
        vals = (f, f / G0 if f_ok else NAN, x_test, speed, f_rate, k_tan, k_sec, work, peak, noise, noise_n, sigma,
                eps)
        return vals, reason, vstate


__all__ = ["ScaleConfig", "DerivedStream", "DERIVED_KEYS", "reason_text", "NO_CAL", "NO_TARE", "SATURATED",
           "NO_DATA", "AFE_MISMATCH", "SYNTHETIC", "SETTLING", "EXTRAPOLATED", "GAP", "NOT_COMPUTABLE"]
