"""DataView — thread-safe plot data for the GUI (SW_design §7.5, §15.6, GRQ-B-08/09).

``snapshot(keys, window_s, px_width)`` → ``PlotSnapshot``: a **common** column time axis of exactly ``px_width``
columns relative to the newest sample (``t_col_s ≤ 0``), per key ``lo``/``hi`` float32 (NaN = no sample in that
column) and a worst-of ``vstate`` per column, built from the coarsest adequate pyramid level of the ring buffer
(render cost bounded by the width, 5–600 s windows). ``xy`` → stride-decimated pairs with per-stride extrema;
``latest(key)`` → value + state (n/a / STALE / SATURATED / INVALID / OK).

Implements: SW-RT-003, SW-RT-005, NFR-001 (backend part), SW-CAL-008 / SW-CAL-009 (force states)
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

import numpy as np

from bend_stand.core.model import LatestSample, PlotSnapshot, SeriesMinMax, XYSnapshot
from bend_stand.core.pipeline import RAW_STATE_NO_DATA, RAW_STATE_SATURATED, RAW_STATE_SETTLING, Pipeline
from bend_stand.core.scaling import (
    AFE_MISMATCH, EXTRAPOLATED, NO_CAL, NO_DATA, NO_TARE, SATURATED, SETTLING, SYNTHETIC,
)

#: channels computed from the force (their state follows the scale reasons, SW-RT-005 / SW-CAL-008 / SW-CAL-009)
FORCE_KEYS = frozenset({"F_N", "F_kgf", "force_rate_n_s", "k_tan_n_mm", "k_sec_n_mm", "work_nmm", "peak_n",
                        "sigma_mpa", "eps"})

STALE_NS = 500_000_000


def _fill_vstate(vs: np.ndarray, t_map: np.ndarray, col_w: float, window_s: float, t_end: float) -> np.ndarray:
    """Worst-of vstate per column; a column without a sample inherits its left neighbour's state when it only
    falls between two samples (narrow columns), and is NO_DATA (3) where the data really stops (a gap longer
    than twice the typical sample spacing, or before the first sample)."""
    out = np.where(np.isnan(vs), 3, vs).astype(np.uint8)
    if len(t_map) < 2:
        return out
    spacing = float(np.median(np.diff(t_map))) if len(t_map) > 2 else col_w
    filled = ~np.isnan(vs)
    centres = t_end - window_s + (np.arange(len(vs)) + 0.5) * col_w
    nxt = np.searchsorted(t_map, centres)
    for c in np.flatnonzero(~filled):
        i = nxt[c]
        if 0 < i < len(t_map) and t_map[i] - t_map[i - 1] <= 2.0 * max(spacing, 1e-12) + col_w:
            prev = c - 1
            while prev >= 0 and not filled[prev]:
                prev -= 1
            out[c] = out[prev] if prev >= 0 else 3
    return out


def minmax_select(y: np.ndarray, stride: int) -> np.ndarray:
    """Indices of the per-segment minimum and maximum of ``y`` (segments of ``stride`` samples), each segment's pair in
    ascending index order, one index when both coincide; NaN ignored; a segment without a finite extreme contributes
    its first index. Vectorised (NFR-009: the former per-segment Python loop dominated the refresh with 600 s
    windows); identical to the loop version (unit test ``test_minmax_select_equals_the_loop``)."""
    # Implements: SW-RT-003, NFR-001 (x–y decimation)
    n = len(y)
    stride = max(1, int(stride))
    n_seg = -(-n // stride)
    pad = n_seg * stride - n
    yy = np.asarray(y, dtype=np.float64)
    if pad:
        yy = np.concatenate([yy, np.full(pad, np.nan)])
    seg = yy.reshape(n_seg, stride)
    nan = np.isnan(seg)
    lo_src = np.where(nan, np.inf, seg)
    hi_src = np.where(nan, -np.inf, seg)
    lo_v = lo_src.min(axis=1)
    hi_v = hi_src.max(axis=1)
    i_lo = np.where(np.isfinite(lo_v), lo_src.argmin(axis=1), 0)
    i_hi = np.where(np.isfinite(hi_v), hi_src.argmax(axis=1), 0)
    base = np.arange(n_seg, dtype=np.int64) * stride
    a = base + np.minimum(i_lo, i_hi)
    b = base + np.maximum(i_lo, i_hi)
    pairs = np.stack([a, b], axis=1)
    keep = np.ones_like(pairs, dtype=bool)
    keep[:, 1] = a != b
    return pairs[keep].astype(np.int64)


class DataView:
    def __init__(self, pipeline: Pipeline, now_ns: Callable[[], int]) -> None:
        self.pipeline = pipeline
        self.now_ns = now_ns
        self.trace_provider: Callable[[int], tuple[np.ndarray, np.ndarray]] | None = None   # sequencer (M4)

    def snapshot(self, keys: Sequence[str], window_s: float, px_width: int) -> PlotSnapshot:
        px = max(1, int(px_width))
        keys = list(keys)
        ring = self.pipeline.ring
        want = keys + (["vstate"] if "vstate" not in keys else [])
        snap = ring.snapshot(want, float(window_s), px)
        t_end = ring.latest_t()
        col_w = float(window_s) / px
        t_col = (np.arange(px, dtype=np.float64) - (px - 1)) * col_w - col_w / 2.0
        series: dict[str, SeriesMinMax] = {}
        empty = np.full(px, np.nan, np.float32)
        if t_end is None or len(snap) == 0:
            for k in keys:
                series[k] = SeriesMinMax(empty.copy(), empty.copy(), np.full(px, 3, np.uint8))
            return PlotSnapshot(float("nan") if t_end is None else t_end, t_col, series, snap.level)
        t_map = snap.t
        if snap.level > 1 or snap.group_starts is not None:
            ends = np.append(snap.t[1:], t_end)
            t_map = (snap.t + ends) / 2.0          # a group (bucket) is placed at its midpoint
        rel = t_map - t_end
        idx = np.floor((rel + window_s) / col_w).astype(np.int64)
        idx = np.clip(idx, 0, px - 1)
        ok = rel >= -window_s - 1e-12
        idx_ok = idx[ok]
        vs_hi = np.full(px, np.nan, np.float32)
        np.fmax.at(vs_hi, idx_ok, snap.ymax["vstate"][ok])
        vstate = _fill_vstate(vs_hi, t_map[ok], col_w, window_s, t_end)
        for k in keys:
            lo = empty.copy()
            hi = empty.copy()
            np.fmin.at(lo, idx_ok, snap.ymin[k][ok])
            np.fmax.at(hi, idx_ok, snap.ymax[k][ok])
            series[k] = SeriesMinMax(lo, hi, vstate)
        return PlotSnapshot(t_end, t_col, series, snap.level)

    def xy(self, x_key: str, y_key: str, window_s: float | None = None, max_points: int = 4000, *,
           since: Literal["window", "record", "sequence"] = "window") -> XYSnapshot:
        t, cols = self.pipeline.ring.raw_window([x_key, y_key, "vstate"], window_s)
        x, y, vs = cols[x_key], cols[y_key], cols["vstate"]
        n = len(x)
        if n > max_points > 0:
            stride = -(-n // max(1, max_points // 2))
            sel_a = minmax_select(y, stride)
            x, y, vs = x[sel_a], y[sel_a], vs[sel_a]
        t_end = float(t[-1]) if len(t) else float("nan")
        return XYSnapshot(x.copy(), y.copy(), np.nan_to_num(vs, nan=3).astype(np.uint8), t_end)

    def latest(self, key: str) -> LatestSample:
        lt = self.pipeline.latest_copy()
        if key not in lt.values:
            return LatestSample(key, float("nan"), "n/a", lt.t_dev_s)
        v = lt.values[key]
        if np.isnan(lt.t_dev_s):
            return LatestSample(key, float("nan"), "n/a", lt.t_dev_s)
        if self.now_ns() - lt.t_host_ns > STALE_NS:
            return LatestSample(key, v, "STALE", lt.t_dev_s)
        if key == "raw":
            if lt.raw_state == RAW_STATE_NO_DATA:
                return LatestSample(key, v, "n/a", lt.t_dev_s)
            if lt.raw_state == RAW_STATE_SATURATED:
                return LatestSample(key, v, "SATURATED", lt.t_dev_s)
            if lt.raw_state == RAW_STATE_SETTLING:
                return LatestSample(key, v, "INVALID", lt.t_dev_s)
        if key in FORCE_KEYS:                       # M3: state of the force-derived channels from the scale reasons
            r = lt.calc_reason
            if r & SATURATED:
                return LatestSample(key, v, "SATURATED", lt.t_dev_s)
            if r & (NO_CAL | NO_TARE | NO_DATA | SYNTHETIC) or np.isnan(v):
                return LatestSample(key, v, "n/a", lt.t_dev_s)
            if r & (AFE_MISMATCH | SETTLING):
                return LatestSample(key, v, "INVALID", lt.t_dev_s)
            if r & EXTRAPOLATED:
                return LatestSample(key, v, "EXTRAPOLATED", lt.t_dev_s)
        if np.isnan(v):
            return LatestSample(key, v, "n/a", lt.t_dev_s)
        return LatestSample(key, v, "OK", lt.t_dev_s)

    def sequence_trace(self, max_points: int = 20000) -> XYSnapshot:
        """Measured (x, F) of the running / last sequence in the sequence coordinate (SW-SCH-002, B6-11)."""
        tp = self.trace_provider
        if tp is None:
            z = np.zeros(0, np.float32)
            return XYSnapshot(z, z.copy(), np.zeros(0, np.uint8), float("nan"))
        x, f = tp(max_points)
        vs = np.where(np.isfinite(f), 0, 3).astype(np.uint8)
        return XYSnapshot(x, f, vs, self.pipeline.latest_copy().t_dev_s)
