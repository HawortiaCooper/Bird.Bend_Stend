"""Column ring buffer with a min/max decimation pyramid (SW_design §3.4, F-31, N-07).

* Single writer (the pipeline), many readers (GUI timer, readout): writes and snapshots take a lock; readers
  get copies. All storage is preallocated numpy (SW-PLT-004: no growth after fill). Data channels are float32
  columns of one 2-D array (time is a separate float64 column), so every pyramid update is vectorised over all
  channels.
* Pyramid: per channel min/max envelopes at decimation factors **4, 16, 64, 256**, updated incrementally per
  batch (``np.fmin/fmax.reduceat`` — NaN-only buckets stay NaN, gaps preserved).
* ``snapshot(keys, window_s, px_width)`` takes the coarsest level that still has ≥ ``px_width`` buckets in the
  window (raw samples if none) and merges consecutive buckets into exactly ``px_width`` min/max pairs, so the
  render cost is bounded by the screen width, not by the window length (≤ 2·px_width points).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/ringbuffer.py @37c87471 (as-is).

Implements: NFR-001, NFR-004 (preallocated, no growth), SW-RT-003 (window / envelope)
"""
from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

PYRAMID_LEVELS = (4, 16, 64, 256)


@dataclass
class Snapshot:
    level: int                          # 1 = raw samples, else decimation factor used
    t: np.ndarray                       # float64, start time of each point / group
    ymin: dict[str, np.ndarray]
    ymax: dict[str, np.ndarray]
    first_index: int = 0                # global sample index of the first sample covered
    last_index: int = 0                 # global index (exclusive)
    group_starts: np.ndarray | None = None   # source-bucket index of each returned group (tests)

    def __len__(self) -> int:
        return len(self.t)


class ColumnRingBuffer:
    def __init__(self, keys: Sequence[str], capacity: int, *, levels: Sequence[int] = PYRAMID_LEVELS) -> None:
        top = max(levels) if levels else 1
        cap = max(int(capacity), top)
        cap = ((cap + top - 1) // top) * top
        self.capacity = cap
        self.keys = tuple(keys)
        self.col = {k: i for i, k in enumerate(self.keys)}
        self.levels = tuple(sorted(levels))
        nk = len(self.keys)
        self._lock = threading.Lock()
        self.t = np.full(cap, np.nan, np.float64)
        self.x = np.full((cap, nk), np.nan, np.float32)
        self.n_total = 0
        self._pt = {lv: np.full(cap // lv, np.nan, np.float64) for lv in self.levels}
        self._pmin = {lv: np.full((cap // lv, nk), np.nan, np.float32) for lv in self.levels}
        self._pmax = {lv: np.full((cap // lv, nk), np.nan, np.float32) for lv in self.levels}

    # -- write (pipeline thread) --------------------------------------------------------------------
    def write(self, t: np.ndarray, cols: Mapping[str, np.ndarray] | np.ndarray) -> None:
        """Append samples. ``cols`` = {key: array} (missing keys → NaN) or an (n, len(keys)) array.

        Only *completed* pyramid buckets are stored; the partial bucket at the head of each level is computed
        from the raw rows on demand in ``snapshot`` (exact, and ~1 numpy reduction per level and write)."""
        t = np.asarray(t, np.float64)
        n = len(t)
        if n == 0:
            return
        if isinstance(cols, np.ndarray):
            X = np.asarray(cols, np.float32).reshape(n, len(self.keys))
        else:
            X = np.full((n, len(self.keys)), np.nan, np.float32)
            for k, v in cols.items():
                i = self.col.get(k)
                if i is not None:
                    X[:, i] = v
        with self._lock:
            if n > self.capacity:                     # keep the newest samples, bucket-aligned
                skip = n - self.capacity
                top = self.levels[-1] if self.levels else 1
                skip += (-(self.n_total + skip)) % top
                self.n_total += skip
                t, X, n = t[skip:], X[skip:], n - skip
            cap = self.capacity
            g0 = self.n_total
            a = g0 % cap
            first = min(n, cap - a)
            self.t[a:a + first] = t[:first]
            self.x[a:a + first] = X[:first]
            if first < n:
                self.t[:n - first] = t[first:]
                self.x[:n - first] = X[first:]
            g1 = g0 + n
            self.n_total = g1
            for lv in self.levels:
                b_first = g0 // lv                     # buckets completed by this write: end in (g0, g1]
                b_end = g1 // lv
                if b_end > b_first:
                    self._store_buckets(lv, b_first, b_end)

    def _store_buckets(self, lv: int, b_first: int, b_end: int) -> None:
        cap = self.capacity
        nb = cap // lv
        nk = len(self.keys)
        b = b_first
        while b < b_end:                               # contiguous ring segments (bucket-aligned)
            p0 = (b * lv) % cap
            cnt = min(b_end - b, (cap - p0) // lv)
            rows = self.x[p0:p0 + cnt * lv].reshape(cnt, lv, nk)
            pb = (b % nb)
            self._pmin[lv][pb:pb + cnt] = np.fmin.reduce(rows, axis=1)
            self._pmax[lv][pb:pb + cnt] = np.fmax.reduce(rows, axis=1)
            self._pt[lv][pb:pb + cnt] = self.t[p0:p0 + cnt * lv:lv]
            b += cnt

    def _partial_bucket(self, lv: int, b: int, g1: int, cols: list[int]) -> tuple[float, np.ndarray, np.ndarray]:
        """Head bucket b (not yet complete) from the raw rows."""
        cap = self.capacity
        p0 = (b * lv) % cap
        cnt = g1 - b * lv
        rows = self.x[p0:p0 + cnt][:, cols]
        return float(self.t[p0]), np.fmin.reduce(rows, axis=0), np.fmax.reduce(rows, axis=0)

    # -- read helpers --------------------------------------------------------------------------------
    @property
    def size(self) -> int:
        return min(self.n_total, self.capacity)

    def latest_t(self) -> float | None:
        with self._lock:
            if self.n_total == 0:
                return None
            return float(self.t[(self.n_total - 1) % self.capacity])

    def latest(self, keys: Sequence[str]) -> dict[str, float]:
        with self._lock:
            if self.n_total == 0:
                return {k: float("nan") for k in keys}
            row = self.x[(self.n_total - 1) % self.capacity]
            return {k: float(row[self.col[k]]) for k in keys}

    def _ordered_idx(self, g0: int, g1: int) -> np.ndarray:
        return np.arange(g0, g1) % self.capacity

    def _first_index_ge(self, tmin: float, g_lo: int, g_hi: int) -> int:
        """Smallest global index in [g_lo, g_hi) with t ≥ tmin (times are non-decreasing)."""
        lo, hi = g_lo, g_hi
        t = self.t
        cap = self.capacity
        while lo < hi:
            mid = (lo + hi) // 2
            if t[mid % cap] < tmin:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def raw_window(self, keys: Sequence[str], window_s: float | None = None
                   ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        with self._lock:
            g1 = self.n_total
            if g1 == 0:
                return np.zeros(0), {k: np.zeros(0, np.float32) for k in keys}
            g_lo = max(0, g1 - self.capacity)
            g0 = g_lo
            if window_s is not None:
                g0 = self._first_index_ge(self.t[(g1 - 1) % self.capacity] - window_s, g_lo, g1)
            idx = self._ordered_idx(g0, g1)
            cols = [self.col[k] for k in keys]
            sub = self.x[idx][:, cols]
            return self.t[idx], {k: sub[:, j].copy() for j, k in enumerate(keys)}

    def snapshot(self, keys: Sequence[str], window_s: float, px_width: int) -> Snapshot:
        px = max(1, int(px_width))
        cols = [self.col[k] for k in keys]
        with self._lock:
            g1 = self.n_total
            if g1 == 0:
                z = {k: np.zeros(0, np.float32) for k in keys}
                return Snapshot(1, np.zeros(0), z, dict(z))
            g_lo = max(0, g1 - self.capacity)
            g0 = self._first_index_ge(self.t[(g1 - 1) % self.capacity] - window_s, g_lo, g1)
            n_win = g1 - g0
            level = 1
            for lv in reversed(self.levels):
                if n_win // lv >= px:
                    level = lv
                    break
            if level == 1:
                idx = self._ordered_idx(g0, g1)
                t = self.t[idx]
                mn = self.x[idx][:, cols]
                mx = mn
                first = g0
            else:
                nb = self.capacity // level
                b_lo = (g_lo + level - 1) // level      # oldest bucket fully retained
                b0 = max(g0 // level, b_lo)
                b1 = (g1 - 1) // level
                idx = np.arange(b0, b1 + 1) % nb
                t = self._pt[level][idx]
                mn = self._pmin[level][idx][:, cols]
                mx = self._pmax[level][idx][:, cols]
                if g1 % level:                         # head bucket still filling: compute from raw rows
                    tp, pmn, pmx = self._partial_bucket(level, b1, g1, cols)
                    t[-1] = tp
                    mn[-1] = pmn
                    mx[-1] = pmx
                first = b0 * level
            count = len(t)
            starts = None
            if count > px:
                starts = (np.arange(px) * count) // px
                t = t[starts]
                mn = np.fmin.reduceat(mn, starts, axis=0)
                mx = np.fmax.reduceat(mx, starts, axis=0)
            return Snapshot(level, t.copy(), {k: mn[:, j].copy() for j, k in enumerate(keys)},
                            {k: mx[:, j].copy() for j, k in enumerate(keys)}, first, g1, starts)

    def clear(self) -> None:
        with self._lock:
            self.n_total = 0
            self.t[:] = np.nan
            self.x[:] = np.nan
            for lv in self.levels:
                self._pt[lv][:] = np.nan
                self._pmin[lv][:] = np.nan
                self._pmax[lv][:] = np.nan
