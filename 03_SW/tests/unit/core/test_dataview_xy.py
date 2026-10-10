"""NFR-009 / SW-RT-003: the vectorised x–y decimation (``dataview.minmax_select``) equals the former per-segment loop
on random data with NaN runs, ties, infinities and a short last segment.

Verifies: SW-RT-003, NFR-001
"""
from __future__ import annotations

import numpy as np
import pytest

from bend_stand.core.dataview import minmax_select


def _loop(y: np.ndarray, stride: int) -> np.ndarray:
    """The pre-NFR-009 implementation (reference)."""
    n = len(y)
    starts = np.arange(0, n, stride)
    ia = np.fmin.reduceat(np.where(np.isnan(y), np.inf, y), starts)
    imx = np.fmax.reduceat(np.where(np.isnan(y), -np.inf, y), starts)
    sel: list[int] = []
    for s0, lo_v, hi_v in zip(starts, ia, imx, strict=True):
        seg = y[s0:s0 + stride]
        i_lo = s0 + int(np.argmin(np.where(np.isnan(seg), np.inf, seg))) if np.isfinite(lo_v) else s0
        i_hi = s0 + int(np.argmax(np.where(np.isnan(seg), -np.inf, seg))) if np.isfinite(hi_v) else s0
        sel.extend(sorted({i_lo, i_hi}))
    return np.array(sel, np.int64)


@pytest.mark.req("SW-RT-003", "NFR-001")
@pytest.mark.parametrize("seed", range(12))
def test_minmax_select_equals_the_loop(seed) -> None:
    rnd = np.random.default_rng(seed)
    n = int(rnd.integers(1, 5000))
    y = rnd.normal(size=n).astype(np.float32 if seed % 2 else np.float64)
    y[rnd.random(n) < 0.1] = np.nan
    if n > 50:
        y[10:40] = np.nan                                          # an all-NaN segment
        y[45:50] = 1.0                                             # ties
    if seed % 3 == 0 and n > 60:
        y[55] = np.inf
        y[56] = -np.inf
    if seed % 4 == 0:
        y = np.round(y, 1)                                         # many ties
    stride = int(rnd.integers(1, 40))
    assert np.array_equal(minmax_select(y, stride), _loop(y, stride))
