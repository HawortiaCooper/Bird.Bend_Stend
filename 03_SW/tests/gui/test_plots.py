"""Minimal realtime plot (WP-D6): G-21 (channel tree from the registry, greyed prerequisites, channels.changed),
G-22 (float / re-attach / close / reopen, STOP), G-23 (window, freeze, Y modes, PlotSnapshot interleave, vstate
styles, autorange vectors), G-24 (readout states), perf smoke (informative, offscreen).

Verifies: SW-RT-001, SW-RT-002, SW-RT-003, SW-RT-004, SW-RT-005, SW-CAL-008, NFR-001
"""
from __future__ import annotations

import numpy as np
import pytest
from fakes import tick
from PySide6.QtCore import Qt

from bend_stand.core.api import ChannelSpec, EventRecord
from bend_stand.gui.plots.autorange import AutoRange, data_bounds, padded
from bend_stand.gui.plots.time_view import (
    VSTATE_EXTRAPOLATED, VSTATE_INVALID, VSTATE_NO_DATA, VSTATE_OK, interleave, split_vstate,
)
from bend_stand.gui.widgets.stop_button import StopButton


@pytest.fixture
def plot(window):
    return window.plot_dock


# --------------------------------------------------------------------------------------------- pure functions

@pytest.mark.req("SW-RT-003")
def test_interleave_two_points_per_column() -> None:
    """Verifies: SW-RT-003 — min/max columns become a 2-points-per-column polyline."""
    x, y = interleave(np.array([-2.0, -1.0, 0.0]), np.array([1, 2, 3], np.float32), np.array([4, 5, 6], np.float32))
    assert x.tolist() == [-2, -2, -1, -1, 0, 0] and y.tolist() == [1, 4, 2, 5, 3, 6]


@pytest.mark.req("SW-RT-003", "SW-CAL-008")
def test_split_vstate_styles() -> None:
    """Verifies: SW-CAL-008 — OK solid, EXTRAPOLATED dashed, INVALID grey, NO_DATA gap; only present states."""
    y = np.arange(8, dtype=float)
    parts = split_vstate(y, np.array([VSTATE_OK, VSTATE_EXTRAPOLATED, VSTATE_INVALID, VSTATE_NO_DATA], np.uint8))
    assert set(parts) == {VSTATE_OK, VSTATE_EXTRAPOLATED, VSTATE_INVALID}
    assert np.isnan(parts[VSTATE_OK][2:]).all() and parts[VSTATE_OK][:2].tolist() == [0, 1]
    assert parts[VSTATE_EXTRAPOLATED][2:4].tolist() == [2, 3]
    assert parts[VSTATE_INVALID][4:6].tolist() == [4, 5]
    assert all(np.isnan(p[6:]).all() for p in parts.values())          # NO_DATA = gap in every style
    ok_only = split_vstate(y, np.zeros(4, np.uint8))
    assert set(ok_only) == {VSTATE_OK} and ok_only[VSTATE_OK] is y        # no copy when all OK


@pytest.mark.req("SW-RT-003", "NFR-001")
def test_autorange_hysteresis_vectors() -> None:
    """Verifies: SW-RT-003, NFR-001 — 5 % margin; expand at once; shrink only after the span stayed < 70 % for 1 s;
    NaN-only data keeps the range."""
    a = AutoRange()
    assert a.update((0.0, 100.0), 0.0) == pytest.approx((-5.0, 105.0))
    assert a.update((10.0, 90.0), 0.1) == pytest.approx((-5.0, 105.0))        # 80/110 > 70 %: keep
    r = a.update((0.0, 150.0), 0.2)                                          # expand top immediately
    assert r[0] == pytest.approx(-5.0) and r[1] == pytest.approx(157.75)       # 5 % of the new extent 155
    assert a.update((40.0, 60.0), 1.0) == r                                  # small, timer starts
    assert a.update((40.0, 60.0), 1.5) == r                                  # 0.5 s: still kept
    assert a.update((40.0, 60.0), 2.01) == pytest.approx((39.0, 61.0))        # ≥ 1 s: shrink
    assert a.update(None, 3.0) == pytest.approx((39.0, 61.0))
    assert data_bounds([np.array([np.nan, 1.0]), np.array([-2.0, np.nan])]) == (-2.0, 1.0)
    assert data_bounds([np.array([np.nan])]) is None
    assert padded(5.0, 5.0) == pytest.approx((4.5, 5.5))


# --------------------------------------------------------------------------------------------- channel tree

@pytest.mark.req("SW-RT-002", "SW-RT-004")
def test_tree_lists_registry_and_greys_prerequisites(plot, connected_fake) -> None:
    """Verifies: SW-RT-002/004 (G-21) — every registry channel listed; unavailable channels greyed with reason;
    status-bit tooltips carry the generated description."""
    tree = plot.tree
    assert tree.all_keys() == [s.key for s in connected_fake.channels.channels()]
    assert not tree.is_available("F_N") and "needs load calibration + tare" in tree.item("F_N").toolTip(0)
    assert tree.is_available("raw")
    assert "data validity" in tree.item("bit.valid").toolTip(0)


@pytest.mark.req("SW-RT-002")
def test_channels_changed_rereads_registry_without_polling(window, plot, connected_fake, qtbot) -> None:
    """Verifies: SW-RT-002 (G-21) — the tree re-reads the registry on channels.changed (no polling); a ticked
    channel that becomes available keeps its tick."""
    calls = {"n": 0}
    orig = connected_fake.channels.channels

    def counting():
        calls["n"] += 1
        return orig()
    connected_fake.channels.channels = counting
    tick(window, 10)
    assert calls["n"] == 0
    plot.tree.set_checked("F_N", True)
    connected_fake.channels.specs = [s if s.key != "F_N" else ChannelSpec("F_N", "force", "N", "Force")
                                     for s in connected_fake.channels.specs]
    connected_fake.events.emit("channels.changed", None)
    qtbot.waitUntil(lambda: calls["n"] == 1, timeout=2000)
    assert plot.tree.is_available("F_N") and "F_N" in plot.tree.checked_keys()


@pytest.mark.req("SW-RT-002", "SW-RT-006")
def test_toggle_channels_curves_and_bit_lanes(window, plot, connected_fake) -> None:
    """Verifies: SW-RT-002, SW-RT-006 — ticking adds a curve, unticking removes only the curve; status bits are
    digital lanes (generated names as ticks, fixed range) in their own quantity pane."""
    tree = plot.tree
    tree.set_checked("bit.valid", True)
    tree.set_checked("bit.moving", True)
    raw_pane, bit_pane = plot.pane_of("raw"), plot.pane_of("bit.valid")
    assert bit_pane is plot.pane_of("bit.moving") and bit_pane is not raw_pane
    assert raw_pane.units() == ("counts", None) and bit_pane.units() == ("bit", None)
    assert bit_pane.bit_keys() == ["bit.valid", "bit.moving"]
    ticks = bit_pane.plot_item.getAxis("left")._tickLevels[0]
    assert [t[1] for t in ticks] == ["VALID", "MOVING"]
    assert bit_pane.y_range() == pytest.approx((-0.2, 2.4))
    tick(window)
    x, y = bit_pane.curve("bit.moving").getData()
    fin = y[np.isfinite(y)]
    assert fin.min() >= 1.2 and fin.max() <= 2.2                       # lane 2 = offset 1.2
    tree.set_checked("raw", False)
    assert plot.pane_of("raw") is None and raw_pane in plot.panes()    # emptied pane kept


@pytest.mark.req("SW-RT-002", "SW-RT-006")
def test_group_tick_is_one_update(plot) -> None:
    """Verifies: SW-RT-002, SW-RT-006 (rule 5) — ticking the status-bit group ticks every available bit at once
    (one checkedChanged) into one pane."""
    seen = []
    plot.tree.checkedChanged.connect(seen.append)
    n_layout = []
    plot.panesChanged.connect(n_layout.append)
    plot.tree.set_group_checked("Status bits", True)
    bits = [k for k in plot.tree.all_keys() if k.startswith("bit.")]
    assert len(seen) == 1 and len(n_layout) == 1
    assert {plot.pane_of(k) for k in bits} == {plot.pane_of("bit.valid")}


# --------------------------------------------------------------------------------------------- dock / refresh

@pytest.mark.req("SW-RT-001", "SW-STOP-001")
def test_float_reattach_close_reopen(window, plot, connected_fake, qtbot) -> None:
    """Verifies: SW-RT-001 (G-22 M1) — float / re-attach / close / reopen; STOP present in every state."""
    for floating in (True, False):
        plot.title_bar.float_button.click()
        assert plot.isFloating() == floating
        assert len([b for b in plot.findChildren(StopButton) if b.isVisibleTo(plot)]) == 1
    plot.title_bar.close_button.click()
    assert not plot.isVisible()
    plot.toggleViewAction().trigger()
    assert plot.isVisible()


@pytest.mark.req("SW-RT-003")
def test_refresh_pulls_snapshot_and_freeze_stops(window, plot, connected_fake) -> None:
    """Verifies: SW-RT-003 (G-23) — refresh pulls data.snapshot(keys, window, px) and draws; Freeze stops updates;
    a hidden window is not updated; no keys → no snapshot."""
    assert plot.tree.checked_keys() == ["raw"]                       # first-start default
    plot.tree.set_checked("raw", False)
    n0, u0 = connected_fake.data.snapshot_calls, plot.updates
    tick(window)
    assert connected_fake.data.snapshot_calls == n0                   # nothing ticked → no snapshot
    plot.tree.set_checked("raw", True)
    tick(window)
    assert connected_fake.data.snapshot_calls == n0 + 1 and plot.updates == u0 + 1
    x, y = plot.pane_of("raw").curve("raw").getData()
    assert len(x) == 2 * plot.px_width() and x.min() >= -plot.window_s - 1e-9 and x.max() <= 0.0
    plot.set_frozen(True)
    tick(window, 3)
    assert connected_fake.data.snapshot_calls == n0 + 1
    plot.set_frozen(False)
    plot.hide()
    tick(window)
    assert connected_fake.data.snapshot_calls == n0 + 1
    plot.show()
    tick(window)
    assert connected_fake.data.snapshot_calls == n0 + 2


@pytest.mark.req("SW-RT-003")
def test_window_length_and_y_modes(window, plot, connected_fake) -> None:
    """Verifies: SW-RT-003 (G-23) — window 5–600 s (clamped), constant X range; manual Y range applied; auto Y
    from the snapshot with margin."""
    plot.set_window_s(1)
    assert plot.window_s == 5
    plot.set_window_s(10_000)
    assert plot.window_s == 600
    plot.set_window_s(60)
    pane = plot.pane_of("raw")
    x0, x1 = pane.x_range()
    assert x0 == pytest.approx(-60) and x1 == pytest.approx(0)
    tick(window)
    lo, hi = pane.y_range()
    assert lo < -100 < 100 < hi                                      # fake raw ±101 + 5 %
    plot.autoscale_check.setChecked(False)                           # manual Y: the range stays as set
    pane.plot_item.vb.setYRange(-1000.0, 2000.0, padding=0)
    tick(window, 2)
    assert pane.y_range() == pytest.approx((-1000.0, 2000.0))
    plot.autoscale_check.setChecked(True)
    tick(window)
    assert pane.y_range()[1] < 2000.0


@pytest.mark.req("SW-CAL-008", "SW-RT-003")
def test_vstate_curves_created_only_when_present(window, plot, connected_fake) -> None:
    """Verifies: SW-CAL-008 — dashed (EXTRAPOLATED) and grey (INVALID) curves appear only when such columns exist."""
    tick(window)
    pane = plot.pane_of("raw")
    assert set(pane.style_curves("raw")) == {VSTATE_OK}
    vs = np.zeros(plot.px_width(), np.uint8)
    vs[:10] = VSTATE_EXTRAPOLATED
    vs[10:20] = VSTATE_INVALID
    vs[20:30] = VSTATE_NO_DATA
    connected_fake.data.vstate["raw"] = vs
    tick(window)
    items = pane.style_curves("raw")
    assert set(items) == {VSTATE_OK, VSTATE_EXTRAPOLATED, VSTATE_INVALID}
    assert items[VSTATE_EXTRAPOLATED].opts["pen"].style() == Qt.PenStyle.DashLine
    _x, y = items[VSTATE_OK].getData()
    assert np.isnan(y[40:60]).all()                                  # NO_DATA columns 20..29 = gap


@pytest.mark.req("SW-RT-003")
def test_snapshot_error_does_not_break_refresh(window, plot, connected_fake) -> None:
    """Verifies: SW-RT-003 — a snapshot error (e.g. not connected) is shown in the dock, the refresh goes on."""
    connected_fake.data.fail = RuntimeError("not connected")
    tick(window, 2)
    assert "no data: not connected" in plot.status_label.text()
    assert window.perf_stats()["errors"] == {}


# --------------------------------------------------------------------------------------------- readouts (G-24)

@pytest.mark.req("SW-RT-005", "SW-CAL-008")
@pytest.mark.parametrize("state", ["OK", "STALE", "SATURATED", "INVALID", "EXTRAPOLATED", "n/a"])
def test_readout_states(window, connected_fake, state) -> None:
    """Verifies: SW-RT-005 (G-24 M1: raw, rate) — LatestSample.state shown verbatim; unknown channel → n/a."""
    connected_fake.data.latest_values["raw"] = (239512.0, state)
    tick(window, 3)
    r = window.readout_dock
    assert r.state_of("raw") == state
    assert r.value_text("raw").replace(" ", "") == "239512"
    assert r.state_of("rate_sps") == "OK" and r.value_text("rate_sps") == "80.0"
    assert r.state_of("F_N") == "n/a" and r.value_text("F_N") == "n/a"


# --------------------------------------------------------------------------------------------- perf smoke

@pytest.mark.req("NFR-001", "SW-RT-006")
def test_perf_smoke_offscreen(window, plot, connected_fake, capsys) -> None:
    """Verifies: NFR-001, SW-RT-006 (smoke, informative offscreen) — 4 time panes (raw, status bits, travel, rate)
    in a 2-column grid + an X-Y pane, 30 s, 200 ticks: one snapshot per tick for all panes; the plot stage stays
    below the 30 ms tick budget (§4.6) on this machine."""
    plot.tree.set_group_checked("Status bits", True)
    for k in ("raw", "setpoint_um", "rate_sps"):
        plot.tree.set_checked(k, True)
    plot.add_xy_pane()
    plot.set_columns(2)
    assert len(plot.time_panes()) == 4
    n0 = window.snapshot_calls
    tick(window, 200)
    ps = window.perf_stats()
    with capsys.disabled():
        print(f"\n[perf smoke offscreen, 4 time panes + X-Y] plots p95 {ps['plots_p95_ms']:.2f} ms max "
              f"{ps['plots_max_ms']:.2f} ms; status p95 {ps['status_p95_ms']:.2f} ms; readouts p95 "
              f"{ps['readouts_p95_ms']:.2f} ms")
    assert window.snapshot_calls - n0 == 200
    assert ps["plots_p95_ms"] < 30.0 and ps["status_p95_ms"] < 30.0
    connected_fake.events.emit("log", EventRecord("log", 0, "x"))
