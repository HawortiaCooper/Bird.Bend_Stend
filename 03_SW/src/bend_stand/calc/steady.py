"""Steady-state extraction per capture window (R4 §8.3, SRS SW-REP-002, ICD §7.6) — pure functions, numpy only.

A frame belongs to a steady-state window only if **VALID = 1, MOVING = 0** and none of DATA ``flags`` bits 4–7
(ESTOP, HALT, FAULT, OVERRUN) and ``status`` bits 0–8, 13, 14 (PAUSED, LIMIT_START/END, LOAD_LIMIT, AFE_STALE /
SATURATED / SETTLING / RATE_MISMATCH, LINK_WDG, POS_UNCERTAIN, NO_AFE_DATA) is set (ICD §7.6, unchanged by D-33 b), its
``setpoint_um`` equals the value at window start and its device time lies in ``[t0, t1]``. Per quantity (raw, F, x):
N, mean, std (ddof 1), min, max, SE(N_eff) (AR(1), ``calc.stats.se_ar1``), drift = OLS slope · window length.
``INCOMPLETE`` if N < 0.8 · capture · rate; LOAD steps ``ON_TARGET`` if ``|mean F − target| ≤ tol`` (else
``NOT_ON_TARGET``). Capture-during-move windows (linear ramp) give a ramp result instead (OLS stiffness, ranges).

The same function serves the live step results (executor) and the offline report (``core.report``), so a
regenerated report equals the live numbers (SW-REP-003).

Implements: SW-REP-002, SW-REP-003 (one extraction for live and offline)
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from bend_stand.calc.stats import drift_slope, se_ar1

NAN = float("nan")
#: DATA ``flags`` bits that exclude a frame: 4 ESTOP, 5 HALT, 6 FAULT, 7 OVERRUN (ICD §7.6)
STEADY_FLAGS_MASK = 0xF0
#: DATA ``status`` bits that exclude a frame: 0–8, 13 POS_UNCERTAIN, 14 NO_AFE_DATA (ICD §7.6)
STEADY_STATUS_MASK = 0x01FF | (1 << 13) | (1 << 14)
VALID, MOVING = 0x01, 0x02
INCOMPLETE_FRAC = 0.8
NOMINAL_RATE_SPS = 80.0


def steady_frame(flags: int, status: int = 0) -> bool:
    """ICD §7.6 window rule for one frame (without the setpoint / time conditions)."""
    return bool(flags & VALID) and not flags & MOVING and not flags & STEADY_FLAGS_MASK \
        and not status & STEADY_STATUS_MASK


@dataclass(frozen=True)
class QStats:
    """Statistics of one quantity over a window."""

    n: int = 0
    mean: float = NAN
    std: float = NAN
    min: float = NAN
    max: float = NAN
    se: float = NAN
    n_eff: float = NAN
    drift: float = NAN

    def as_dict(self) -> dict[str, float]:
        return {"n": float(self.n), "mean": self.mean, "std": self.std, "min": self.min, "max": self.max,
                "se": self.se, "n_eff": self.n_eff, "drift": self.drift}


def qstats(t_s: np.ndarray, v: np.ndarray, length_s: float) -> QStats:
    """N, mean, std (ddof 1), min, max, SE(N_eff), drift = slope · ``length_s`` over the finite values."""
    m = np.isfinite(v)
    tv, vv = t_s[m], v[m]
    n = int(vv.size)
    if n == 0:
        return QStats()
    _rho, ne, se = se_ar1(vv)
    slope = drift_slope(tv, vv)
    return QStats(n, float(vv.mean()), float(vv.std(ddof=1)) if n > 1 else NAN, float(vv.min()), float(vv.max()),
                  se, ne, slope * float(length_s) if math.isfinite(slope) else NAN)


@dataclass(frozen=True)
class SteadyStats:
    """TV-SS form (``steady_state``): statistics of the raw value of the selected frames."""

    n: int
    mean: float
    std: float
    min: float
    max: float


def steady_state(frames: Sequence[tuple[int, int, int, int]] | Sequence[tuple[int, int, int, int, int]],
                 pos_um: int, t_from_us: int, t_to_us: int) -> SteadyStats:
    """R4 §12 TV-SS: ``frames`` = ``(t_us, flags, pos_um, raw[, status])``; VALID ∧ ¬MOVING ∧ pos == ``pos_um`` ∧
    no excluded flag ∧ t in [from, to]."""
    vals = []
    for fr in frames:
        t, fl, pos, raw = fr[0], fr[1], fr[2], fr[3]
        st = fr[4] if len(fr) > 4 else 0
        if steady_frame(fl, st) and pos == pos_um and t_from_us <= t <= t_to_us:
            vals.append(float(raw))
    if not vals:
        return SteadyStats(0, NAN, NAN, NAN, NAN)
    a = np.asarray(vals)
    return SteadyStats(len(vals), float(a.mean()), float(a.std(ddof=1)) if a.size > 1 else NAN, float(a.min()),
                       float(a.max()))


@dataclass(frozen=True)
class WindowStats:
    """Result of one capture window (SW-REP-002)."""

    n: int
    n_expected: float
    raw: QStats
    f: QStats
    x: QStats
    flags: tuple[str, ...]
    pos_um: int | None
    ramp: dict[str, float] | None = None
    stats: dict[str, dict[str, float]] = field(default_factory=dict)


def window_stats(t_us: Sequence[int] | np.ndarray, flags: Sequence[int] | np.ndarray,
                 status: Sequence[int] | np.ndarray, setpoint_um: Sequence[int] | np.ndarray,
                 raw: Sequence[float] | np.ndarray, f_n: Sequence[float] | np.ndarray, *, t0_us: int, t1_us: int,
                 capture_s: float | None = None, rate_sps: float | None = None, target_n: float | None = None,
                 tol_n: float | None = None, ramp: bool = False) -> WindowStats:
    """Window extraction over parallel columns (device time ``t_us`` unwrapped, µs). ``f_n`` may hold NaN (force not
    computable: no calibration / tare). ``rate_sps`` = measured sample rate (None → nominal 80 SPS)."""
    t = np.asarray(t_us, dtype=np.int64)
    fl = np.asarray(flags, dtype=np.int64)
    st = np.asarray(status, dtype=np.int64)
    sp = np.asarray(setpoint_um, dtype=np.int64)
    rw = np.asarray(raw, dtype=np.float64)
    fv = np.asarray(f_n, dtype=np.float64)
    length_s = (t1_us - t0_us) / 1e6 if capture_s is None else float(capture_s)
    rate = float(rate_sps) if rate_sps and math.isfinite(rate_sps) and rate_sps > 0 else NOMINAL_RATE_SPS
    n_exp = INCOMPLETE_FRAC * length_s * rate
    in_t = (t >= t0_us) & (t <= t1_us)
    base = in_t & ((fl & VALID) != 0) & ((fl & STEADY_FLAGS_MASK) == 0) & ((st & STEADY_STATUS_MASK) == 0)
    if ramp:                                       # capture during move: VALID frames incl. MOVING, no steady state
        sel = base
        ts = t[sel] / 1e6
        xs = sp[sel] / 1000.0
        fs = fv[sel]
        r = QStats(int(sel.sum()))
        out: dict[str, float] = {"n": float(sel.sum())}
        okm = np.isfinite(fs)
        if okm.sum() >= 2 and float(np.ptp(xs[okm])) > 0:
            dx = xs[okm] - xs[okm].mean()
            k = float(np.dot(dx, fs[okm] - fs[okm].mean()) / np.dot(dx, dx))
            out.update(k_n_mm=k, b_n=float(fs[okm].mean() - k * xs[okm].mean()))
        else:
            out.update(k_n_mm=NAN, b_n=NAN)
        out.update(f_min_n=float(np.nanmin(fs)) if okm.any() else NAN, f_max_n=float(np.nanmax(fs)) if okm.any() else NAN,
                   x_min_mm=float(xs.min()) if xs.size else NAN, x_max_mm=float(xs.max()) if xs.size else NAN)
        flags_out = ("RAMP",) + (("INCOMPLETE",) if sel.sum() < n_exp else ())
        return WindowStats(int(sel.sum()), n_exp, r, QStats(), QStats(), flags_out, None, out,
                           {"ramp": out, "t": {"n": float(ts.size)}})
    cand = base & ((fl & MOVING) == 0)
    idx = np.nonzero(cand)[0]
    pos = int(sp[idx[0]]) if idx.size else None
    sel = cand & (sp == pos) if pos is not None else cand
    ts = t[sel] / 1e6
    rs = qstats(ts, rw[sel], length_s)
    fs = qstats(ts, fv[sel], length_s)
    xs = qstats(ts, sp[sel] / 1000.0, length_s)
    n = int(sel.sum())
    out_flags: list[str] = []
    if n < n_exp:
        out_flags.append("INCOMPLETE")
    if target_n is not None and tol_n is not None:
        ok = math.isfinite(fs.mean) and abs(fs.mean - target_n) <= tol_n
        out_flags.append("ON_TARGET" if ok else "NOT_ON_TARGET")
    return WindowStats(n, n_exp, rs, fs, xs, tuple(out_flags), pos, None,
                       {"raw": rs.as_dict(), "F": fs.as_dict(), "x": xs.as_dict()})


def median_rate_sps(t_us: Sequence[int] | np.ndarray) -> float | None:
    """Measured sample rate from the median frame period (None with < 5 frames)."""
    t = np.asarray(t_us, dtype=np.int64)
    if t.size < 5:
        return None
    d = np.diff(t)
    d = d[d > 0]
    if d.size < 4:
        return None
    return 1e6 / float(np.median(d))


__all__ = ["STEADY_FLAGS_MASK", "STEADY_STATUS_MASK", "steady_frame", "QStats", "qstats", "SteadyStats",
           "steady_state", "WindowStats", "window_stats", "median_rate_sps", "INCOMPLETE_FRAC"]
