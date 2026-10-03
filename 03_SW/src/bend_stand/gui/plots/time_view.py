"""Pure helpers of the time plot: ``PlotSnapshot`` min/max columns → polyline, ``vstate`` styles
(SW_design_GUI §4.3, §4.6). Used by :class:`~bend_stand.gui.plots.plot_pane.PlotPane` (since D-38 the panes replace
the single time view of M1).

* Relative time axis: x = ``PlotSnapshot.t_col_s`` ∈ [−window, 0] (constant X range).
* ``vstate`` styles: 0 OK solid, 1 EXTRAPOLATED dashed, 2 INVALID grey, 3 NO_DATA gap.

Implements: SW-RT-003 (time view data path), SW-CAL-008 (extrapolated style), NFR-001 (no copies beyond the
interleave)
"""
from __future__ import annotations

import numpy as np

VSTATE_OK, VSTATE_EXTRAPOLATED, VSTATE_INVALID, VSTATE_NO_DATA = 0, 1, 2, 3


def interleave(t_col: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pure: 2 points per pixel column (lo then hi) — the min/max envelope drawn as one polyline."""
    x = np.repeat(np.asarray(t_col, dtype=np.float64), 2)
    y = np.empty(2 * len(lo), dtype=np.float64)
    y[0::2] = lo
    y[1::2] = hi
    return x, y


def split_vstate(y: np.ndarray, vstate: np.ndarray) -> dict[int, np.ndarray]:
    """Pure: per style (OK / EXTRAPOLATED / INVALID) the interleaved y with NaN where the column has another
    state; NO_DATA columns are NaN everywhere (gap). Only states that occur are returned (OK always)."""
    vs = np.repeat(np.asarray(vstate, dtype=np.uint8), 2)
    out: dict[int, np.ndarray] = {}
    for st in (VSTATE_OK, VSTATE_EXTRAPOLATED, VSTATE_INVALID):
        m = vs == st
        if st != VSTATE_OK and not m.any():
            continue
        out[st] = y if (st == VSTATE_OK and m.all()) else np.where(m, y, np.nan)
    return out
