"""Level G — SW-RT-006 plot panes (D-38; Thrust_Stand SW-RT-004 / D-62 / D-63) — Validator F, SW_test_plan v0.3
§3.6 TC-SW-RT-006-01…14.

Offscreen GUI on B's real Backend (lock-step simulator). Expected results come from the SRS v0.5.1 SW-RT-006 text and
the D-63 rules (1)–(5), never from D's placement code. Widgets are reached through the public members of
``PlotDock`` / ``PlotPane`` / ``ChannelTree`` / ``MainWindow`` named in the module docs of D's implementation; a pane
is identified by object identity, a channel by its registry key.

Verifies: SW-RT-006, SW-RT-001, SW-RT-003, NFR-001
"""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bend_stand.gui.dialogs import safe_dialog  # noqa: E402

safe_dialog.disable_native_dialogs()

import harness as H  # noqa: E402

BITS = ("bit.valid", "bit.moving", "bit.homed", "bit.paused")


@pytest.fixture(autouse=True)
def _gui_isolation(tmp_path, monkeypatch):
    from bend_stand.gui import stop as stop_mod

    monkeypatch.setenv("BEND_STAND_GUI_SETTINGS", str(tmp_path / "gui.ini"))
    prev = stop_mod.stop_handler()
    yield
    stop_mod.set_stop_handler(prev)


@pytest.fixture
def window(qtbot, tmp_path):
    from bend_stand.gui.main_window import MainWindow
    from bend_stand.gui.settings import make_settings

    made = []

    def make(be, ini="mw.ini", show=True):
        win = MainWindow(be, settings=make_settings(tmp_path / ini), start_refresh=False)
        qtbot.addWidget(win)
        made.append(win)
        if show:
            win.show()
            qtbot.waitExposed(win)
        win.refresh.tick()
        return win

    yield make
    for w in made:
        try:
            w._force_close = True
            w.close()
        except RuntimeError:
            pass


def _dock(win):
    return win.plot_docks[0]


def _fresh(dock):
    """Untick everything → one (emptied) pane remains; return it."""
    dock.tree.set_checked_many(dock.tree.checked_keys(), False)
    for p in dock.panes()[1:]:
        dock.close_pane(p)
    assert len(dock.panes()) == 1 and not dock.panes()[0].keys()
    return dock.panes()[0]


def _keys(dock):
    return [list(p.keys()) for p in dock.panes()]


# ============================================================================================ grid / panes

@pytest.mark.req("SW-RT-006")
def test_tc_sw_rt_006_01_column_switch_keeps_pane_order(vbe, window):
    """Grid of 1/2/3/4 columns per window; switching keeps the pane order (row-major flow)."""
    # Verifies: SW-RT-006
    d = _dock(window(vbe))
    _fresh(d)
    panes = [d.panes()[0]] + [d.add_pane() for _ in range(4)]
    for n in (2, 3, 4, 1, 3):
        d.set_columns(n)
        assert d.columns == n and d.panes() == panes
        assert [d.grid.position_of(p) for p in panes] == [divmod(i, n) for i in range(5)], n
    with pytest.raises(ValueError):
        d.set_columns(5)


@pytest.mark.req("SW-RT-006")
def test_tc_sw_rt_006_02_add_pane_plot_in_and_move_to_pane_n(vbe, window):
    """'New pane' adds an empty pane; 'Plot in pane N' ticks a channel directly into pane N; 'Move to pane N' moves
    an existing curve (the channel appears in exactly one pane of the window); 'Plot in new pane'."""
    # Verifies: SW-RT-006
    d = _dock(window(vbe))
    p1 = _fresh(d)
    p2 = d.add_pane()
    assert d.panes() == [p1, p2] and not p2.keys()
    d.plot_in_pane("raw", p2)
    assert "raw" in d.checked_keys() and p2.keys() == ["raw"] and not p1.keys()
    assert d.move_curve("raw", p1) and p1.keys() == ["raw"] and not p2.keys()
    p3 = d.plot_in_new_pane("x_mm")
    assert p3 in d.panes() and p3.keys() == ["x_mm"]
    assert sum(k == "raw" for ks in _keys(d) for k in ks) == 1


@pytest.mark.req("SW-RT-006")
def test_tc_sw_rt_006_03_drag_reorder_inserts_at_the_drop_cell(vbe, window):
    """Drag & drop of a pane by its title strip: the drop handler of the grid inserts the pane at the target cell,
    the others shift (pane MIME round trip identifies the dragged pane)."""
    # Verifies: SW-RT-006
    from bend_stand.gui.plots.plot_pane import pane_from_mime, pane_mime

    d = _dock(window(vbe))
    a = _fresh(d)
    b, c, e = d.add_pane(), d.add_pane(), d.add_pane()
    d.set_columns(2)
    assert pane_from_mime(pane_mime(c)) is c
    assert d.grid.drop_handler(c, 0)
    assert d.panes() == [c, a, b, e]
    assert d.grid.drop_handler(c, 3)
    assert d.panes() == [a, b, e, c] and d.columns == 2


@pytest.mark.req("SW-RT-006", "SW-RT-001")
def test_tc_sw_rt_006_04_move_pane_to_another_window_keeps_curves(vbe, window):
    """Move a pane with its curves to another plot window ("Move to window…" / drop on the other window): the curves
    arrive unchanged, the source window unticks them, the target ticks them; the source keeps ≥ 1 pane."""
    # Verifies: SW-RT-006, SW-RT-001
    win = window(vbe)
    d1 = _dock(win)
    p = _fresh(d1)
    d1.tree.set_checked_many(["raw", "x_mm"])
    moving = d1.pane_of("raw")
    keys = list(moving.keys())
    d2 = win.new_plot_window()
    assert d2 is not None
    d1.move_pane_to(moving, d2)
    assert moving in d2.panes() and moving.keys() == keys and moving not in d1.panes()
    assert set(keys) <= set(d2.checked_keys()) and not set(keys) & set(d1.checked_keys())
    assert d1.panes() and all(d2.pane_of(k) is moving for k in keys)
    del p


@pytest.mark.req("SW-RT-006")
def test_tc_sw_rt_006_05_rename_and_close_panes(vbe, window):
    """Rename: the user title replaces the automatic quantity title and survives curve changes; 'Automatic title'
    restores it. Close: the pane's channels are unticked; the last pane of a window cannot be closed."""
    # Verifies: SW-RT-006
    d = _dock(window(vbe))
    p1 = _fresh(d)
    d.tree.set_checked("raw", True)
    assert "Raw counts" in p1.title_text()
    d.rename_pane(p1, "Load cell")
    d.tree.set_checked_many(["raw"], False)
    d.tree.set_checked("raw", True)
    assert p1.title_text().endswith("Load cell") or p1.title_text() == "Load cell", p1.title_text()
    d.rename_pane(p1, None)
    assert "Raw counts" in p1.title_text()
    p2 = d.plot_in_new_pane("x_mm")
    d.close_pane(p2)
    assert p2 not in d.panes() and "x_mm" not in d.checked_keys()
    d.close_pane(p1)
    assert d.panes() == [p1]


# ============================================================================================ D-63 rules

@pytest.mark.req("SW-RT-006")
def test_tc_sw_rt_006_06_rule1_same_quantity_joins_first_pane(vbe, window):
    """D-63 (1): a ticked channel joins the first pane (grid order) that already shows a channel of the same quantity
    — all status bits together; travel and raw counts in panes of their own."""
    # Verifies: SW-RT-006
    d = _dock(window(vbe))
    _fresh(d)
    d.tree.set_checked(BITS[0], True)
    d.tree.set_checked("raw", True)
    d.tree.set_checked("x_mm", True)
    for b in BITS[1:]:
        d.tree.set_checked(b, True)
    pb = d.pane_of(BITS[0])
    assert all(d.pane_of(b) is pb for b in BITS)
    assert len({id(d.pane_of(k)) for k in (BITS[0], "raw", "x_mm")}) == 3


@pytest.mark.req("SW-RT-006")
def test_tc_sw_rt_006_07_rule2_empty_pane_reused_before_new(vbe, window):
    """D-63 (2): no pane shows the quantity → the first empty pane (grid order) is reused before a new pane is
    created."""
    # Verifies: SW-RT-006
    d = _dock(window(vbe))
    p1 = _fresh(d)
    d.tree.set_checked("raw", True)
    e1, e2 = d.add_pane(), d.add_pane()
    n = len(d.panes())
    d.tree.set_checked("x_mm", True)
    assert d.pane_of("x_mm") is e1 and len(d.panes()) == n
    d.tree.set_checked(BITS[0], True)
    assert d.pane_of(BITS[0]) is e2 and len(d.panes()) == n
    d.tree.set_checked("rate_sps", True)
    assert len(d.panes()) == n + 1 and d.pane_of("rate_sps") is d.panes()[-1]
    del p1


@pytest.mark.req("SW-RT-006")
def test_tc_sw_rt_006_08_rule3_untick_keeps_pane_for_reuse(vbe, window):
    """D-63 (3): unticking removes only the curve; a pane with curves left stays; an emptied pane is kept and reused
    by rule (2) for the next new quantity."""
    # Verifies: SW-RT-006
    d = _dock(window(vbe))
    _fresh(d)
    d.tree.set_checked_many(["raw", "x_mm", BITS[0], BITS[1]])
    pb, px = d.pane_of(BITS[0]), d.pane_of("x_mm")
    n = len(d.panes())
    d.tree.set_checked(BITS[0], False)
    assert pb in d.panes() and pb.keys() == [BITS[1]]
    d.tree.set_checked("x_mm", False)
    assert px in d.panes() and not px.keys() and len(d.panes()) == n
    d.tree.set_checked("rate_sps", True)
    assert d.pane_of("rate_sps") is px and len(d.panes()) == n


@pytest.mark.req("SW-RT-006")
def test_tc_sw_rt_006_09_rule4_manual_placement_wins(vbe, window):
    """D-63 (4): a channel moved by the user stays where it was put; a later channel of that quantity joins the
    first pane (grid order) showing the quantity; the 'selected pane' does not redirect new ticks."""
    # Verifies: SW-RT-006
    d = _dock(window(vbe))
    p1 = _fresh(d)
    d.tree.set_checked(BITS[0], True)
    p_new = d.move_curve_to_new_pane(BITS[0])
    assert p_new is not None and d.pane_of(BITS[0]) is p_new
    d.tree.set_checked(BITS[1], True)
    first_with_bits = next(p for p in d.panes() if p.keys() and p.shows_quantity("Status bits"))
    assert d.pane_of(BITS[1]) is first_with_bits
    d.tree.set_checked("raw", True)
    d.select_pane(p_new)
    d.tree.set_checked(BITS[2], True)
    assert d.pane_of(BITS[2]) is first_with_bits and d.pane_of(BITS[0]) is p_new
    del p1


@pytest.mark.req("SW-RT-006", "SW-RT-002")
def test_tc_sw_rt_006_10_rule5_group_tick_only_available_children(vbe, window):
    """D-63 (5): ticking a tree group ticks only its available children; greyed channels (force / derived without a
    calibration) stay unticked and the group shows 'partially checked'."""
    # Verifies: SW-RT-006, SW-RT-002
    from PySide6.QtCore import Qt

    d = _dock(window(vbe))
    _fresh(d)
    specs = {s.key: s for s in vbe.channels.channels()}
    group = specs["raw"].group
    members = [k for k, s in specs.items() if s.group == group]
    unavailable = [k for k in members if not getattr(specs[k], "available", True)]
    assert unavailable, "precondition: the load group has unavailable members without a calibration"
    d.tree.set_group_checked(group, True)
    checked = set(d.checked_keys())
    assert {k for k in members if k not in unavailable} <= checked and not set(unavailable) & checked
    gi = d.tree._groups[group]  # noqa: SLF001 - check state of the group row (display only)
    assert gi.checkState(0) == Qt.CheckState.PartiallyChecked


# ============================================================================================ X-Y / X link

@pytest.mark.req("SW-RT-006", "SW-RT-003")
def test_tc_sw_rt_006_11_xy_pane_type(vbe, window):
    """The X-Y (travel–load) view is a pane type: x choices = travel channels, y = force then raw counts; it pulls
    ``data.xy`` (not the time snapshot), is not time-linked, and is saved as ``{"type": "xy"}``."""
    # Verifies: SW-RT-006, SW-RT-003
    d = _dock(window(vbe))
    _fresh(d)
    xy = d.add_xy_pane(x="x_mm", y="raw")
    assert xy in d.xy_panes() and xy not in d.time_panes() and xy.kind == "xy"
    xs, ys = d.xy_candidates()
    assert "x_mm" in [k for k, _ in xs] and "raw" in [k for k, _ in ys]
    calls = []

    class _Data:
        def xy(self, x, y, window_s):
            calls.append((x, y, window_s))
            return vbe.data.xy(x, y, window_s)

    H.advance(vbe, 500)
    assert d.refresh_xy(_Data()) == 1 and calls == [("x_mm", "raw", d.window_s)]
    entry = d.layout_state()["panes"][d.panes().index(xy)]
    assert isinstance(entry, dict) and entry.get("type") == "xy"


@pytest.mark.req("SW-RT-006", "SW-RT-003")
def test_tc_sw_rt_006_12_time_axis_linked_within_a_window(vbe, window):
    """Zoom / pan of the time axis in one pane → the same range in every time pane of that window (not in another
    window)."""
    # Verifies: SW-RT-006, SW-RT-003
    win = window(vbe)
    d = _dock(win)
    _fresh(d)
    d.tree.set_checked_many(["raw", "x_mm", BITS[0]])
    tp = d.time_panes()
    assert len(tp) >= 3
    d2 = win.new_plot_window()
    d2.tree.set_checked("raw", True)
    r2 = d2.time_panes()[0].x_range()
    tp[1].plot_item.vb.setXRange(-12.0, -2.0, padding=0)
    for p in tp:
        lo, hi = p.x_range()
        assert lo == pytest.approx(-12.0, abs=1e-6) and hi == pytest.approx(-2.0, abs=1e-6)
    assert d2.time_panes()[0].x_range() == pytest.approx(r2)


# ============================================================================================ persistence / refresh

@pytest.mark.req("SW-RT-006", "SW-RT-001")
def test_tc_sw_rt_006_13_layout_restored_after_restart(vbe, window):
    """Columns, pane order, curves per pane, user titles, X-Y panes and the second window are restored by a new
    MainWindow on the same settings file (GUI restart)."""
    # Verifies: SW-RT-006, SW-RT-001
    win = window(vbe, ini="restart.ini")
    d = _dock(win)
    p1 = _fresh(d)
    d.tree.set_checked_many(["raw", "x_mm", BITS[0], BITS[1]])
    d.add_pane()                                         # an empty pane is kept and saved
    d.add_xy_pane(x="x_mm", y="raw")
    d.set_columns(3)
    d.rename_pane(d.pane_of("x_mm"), "Crosshead")
    d.move_pane(d.pane_of(BITS[0]), 0)
    d2 = win.new_plot_window()
    d2.tree.set_checked("rate_sps", True)
    d2.set_columns(2)
    before = win.layout_state()
    win.save_layout()
    win._force_close = True
    win.close()
    win2 = window(vbe, ini="restart.ini")
    after = win2.layout_state()
    assert len(after["docks"]) == len(before["docks"]) == 2
    for a, b in zip(after["docks"], before["docks"]):
        for k in ("title", "columns", "panes", "titles", "window_s"):
            assert a[k] == b[k], (k, a[k], b[k])
    del p1


@pytest.mark.req("SW-RT-006", "NFR-001")
def test_tc_sw_rt_006_14_one_snapshot_per_time_window_with_four_panes(vbe, window, monkeypatch, qtbot):
    """Shared refresh (D-38 / TS D-62): with 4 panes in Plot 1 and a floating Plot 2 there is exactly ONE
    ``data.snapshot`` per refresh tick for one window length and exactly two for two distinct lengths — never one per
    pane / window; every pane gets data from it."""
    # Verifies: SW-RT-006, NFR-001
    win = window(vbe)
    d = _dock(win)
    _fresh(d)
    d.tree.set_checked_many(["raw", "x_mm", BITS[0], "rate_sps"])
    assert len([p for p in d.time_panes() if p.keys()]) == 4
    d2 = win.new_plot_window()
    d2.setFloating(True)
    d2.show()
    d2.tree.set_checked("raw", True)
    d.raise_()
    qtbot.wait(100)
    assert d.is_shown() and d2.is_shown(), "precondition: both windows shown (Plot 2 floating)"
    calls = []
    real = vbe.data.snapshot

    def spy(keys, window_s, px):
        calls.append((tuple(keys), float(window_s)))
        return real(keys, window_s, px)

    monkeypatch.setattr(vbe.data, "snapshot", spy)
    H.advance(vbe, 1000)
    calls.clear()
    win.refresh.tick()
    assert len(calls) == 1, calls
    assert {"raw", "x_mm", BITS[0], "rate_sps"} <= set(calls[0][0])
    d2.set_window_s(60)
    calls.clear()
    win.refresh.tick()
    assert sorted(w for _, w in calls) == [30.0, 60.0], calls


@pytest.mark.rt
@pytest.mark.req("NFR-001", "SW-RT-006")
def test_tc_nfr_001_04_refresh_with_four_panes_dev_smoke(qtbot, tmp_path):
    """NFR-001 with 4 panes (DEV smoke, informative; acceptance on the REF PC, PR-1): real-clock simulator at 80 Hz,
    Plot 1 with 4 panes (raw, travel, 6 status bits, rate) + X-Y pane, 30 s window, 4 s of refresh → paint-to-paint
    interval p95 ≤ 50 ms and no refresh-stage error."""
    # Verifies: NFR-001, SW-RT-006
    from bend_stand.gui.main_window import MainWindow
    from bend_stand.gui.settings import make_settings

    be = H.realtime_backend(recordings_root=str(tmp_path / "rec"))
    try:
        H.connect(be, "sim").result(10)
        assert H.wait_rt(lambda: H.status(be).stream.on, 5)
        win = MainWindow(be, settings=make_settings(tmp_path / "perf.ini"), start_refresh=False)
        qtbot.addWidget(win)
        win.show()
        qtbot.waitExposed(win)
        d = _dock(win)
        _fresh(d)
        d.tree.set_checked_many(["raw", "x_mm", "rate_sps", *BITS, "bit.enabled", "bit.estop"])
        d.add_xy_pane(x="x_mm", y="raw")
        assert len([p for p in d.time_panes() if p.keys()]) == 4
        win.refresh.start()
        t_end = time.monotonic() + 4.0
        while time.monotonic() < t_end:
            qtbot.wait(50)
        win.refresh.stop()
        ps = win.refresh.perf_stats()
        assert ps["ticks"] > 60 and not ps["errors"], ps
        assert ps["interval_p95_ms"] <= 50.0, ps
        win._force_close = True
        win.close()
    finally:
        be.shutdown()
