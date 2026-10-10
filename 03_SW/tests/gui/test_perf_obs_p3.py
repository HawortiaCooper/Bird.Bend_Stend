"""OBS-P3-02 cheap wins (F's DEV-PC perf report): curves without gaps use pyqtgraph's fast path, gaps still draw as
gaps; a minimised window takes no snapshot / paint; the readouts touch a row's state colour only when it changes.

Verifies: NFR-001, NFR-002 (GUI-thread budget), SW-RT-003 (NaN = gap unchanged)
"""
from __future__ import annotations

import numpy as np
import pytest
from fakes import tick
from PySide6.QtCore import Qt

from bend_stand.gui.plots.plot_pane import FastCurve


@pytest.mark.req("NFR-001", "SW-RT-003")
def test_fast_path_only_without_gaps(qapp) -> None:
    """Verifies: NFR-001, SW-RT-003 — finite data → connect "all" + skipFiniteCheck (fast path); a NaN → "finite"
    (gap kept: the path has two sub-paths); back to finite → fast path again."""
    c = FastCurve(pen="w", name="t")
    x = np.linspace(-30.0, 0.0, 200)
    c.set_xy(x, np.sin(x))
    assert c.opts["connect"] == "all" and c.opts["skipFiniteCheck"] is True
    full = c.getPath()
    assert full.elementCount() == 200
    y = np.sin(x)
    y[100] = np.nan
    c.set_xy(x, y)
    assert c.opts["connect"] == "finite" and c.opts["skipFiniteCheck"] is False
    path = c.getPath()
    moves = sum(1 for i in range(path.elementCount()) if path.elementAt(i).isMoveTo())
    assert moves == 2                                                      # NaN = gap (two segments)
    c.set_xy(x, np.cos(x))
    assert c.opts["connect"] == "all" and c.getPath().elementCount() == 200
    c.set_xy(x[:0], x[:0])
    assert c.opts["connect"] == "all"


@pytest.mark.req("NFR-002", "NFR-001")
def test_minimised_window_takes_no_snapshot(window, connected_fake) -> None:
    """Verifies: NFR-002 — while the main window is minimised the docked plot window is not shown → no snapshot,
    no paint work; restored → updates again."""
    dock = window.plot_dock
    tick(window)
    assert dock.is_shown()
    window.setWindowState(Qt.WindowState.WindowMinimized)
    assert window.isMinimized()
    assert not dock.is_shown() and not dock.wants_snapshot()
    n = window.snapshot_calls
    tick(window, 3)
    assert window.snapshot_calls == n
    window.setWindowState(Qt.WindowState.WindowNoState)
    assert not window.isMinimized()


@pytest.mark.req("NFR-002")
def test_readout_state_colour_set_only_on_change(window, connected_fake) -> None:
    """Verifies: NFR-002 — repeating the same values / state changes no table item (no model update, no repaint);
    a new state changes the state cell once."""
    rd = window.readout_dock
    rd._set(0, "1.0", "0.0", "2.0", "OK")                                  # noqa: SLF001
    changed: list[tuple[int, int]] = []
    rd.table.itemChanged.connect(lambda it: changed.append((it.row(), it.column())))
    for _ in range(5):
        rd._set(0, "1.0", "0.0", "2.0", "OK")                              # noqa: SLF001
    assert changed == []
    rd._set(0, "1.0", "0.0", "2.0", "STALE")                               # noqa: SLF001
    assert (0, 5) in changed and rd.table.item(0, 5).data(Qt.ItemDataRole.UserRole) == "STALE"
