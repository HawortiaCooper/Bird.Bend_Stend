"""SW-RT-006 / D-38: plot panes as in Thrust_Stand (G-45…G-52, SW_design_GUI §4.7, §10.2).

Offscreen: drag & drop is exercised with synthetic QDragEnter / QDragMove / QDrop events carrying the pane MIME
data (a real ``QDrag.exec()`` would block offscreen) — technique of Thrust_Stand_HAW/03_SW/tests/gui/
test_plot_grid.py @37c8747.

Verifies: SW-RT-006, SW-RT-001, SW-RT-002, SW-RT-003, SW-STOP-001
"""
from __future__ import annotations

import numpy as np
import pytest
from fakes import tick
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from bend_stand.core.api import ChannelSpec
from bend_stand.gui.plots.plot_pane import XYPane, pane_from_mime, pane_mime
from bend_stand.gui.plots.quantity import quantity_group
from bend_stand.gui.widgets.stop_button import StopButton

EXTRA = [ChannelSpec("x_mm", "Setpoint", "mm", "travel"), ChannelSpec("x2_mm", "Travel 2", "mm", "travel"),
         ChannelSpec("F_kgf", "Force", "kgf", "Force")]


@pytest.fixture
def plot(window, connected_fake, qtbot):
    connected_fake.channels.specs = connected_fake.channels.specs + EXTRA
    connected_fake.events.emit("channels.changed", None)
    qtbot.waitUntil(lambda: "x_mm" in window.plot_dock.tree.all_keys(), timeout=2000)
    window.plot_dock.resize(900, 600)
    return window.plot_dock


def _drag_drop(target, mime: QMimeData, pos: QPoint, drop: bool = True):
    actions = Qt.DropAction.MoveAction
    enter = QDragEnterEvent(pos, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, enter)
    move = QDragMoveEvent(pos, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, move)
    if drop:
        ev = QDropEvent(QPointF(pos), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, ev)
        return ev
    return move


def _titles(dock) -> list[str]:
    return [p.title_text() for p in dock.panes()]


# --------------------------------------------------------------------------------------------- quantity / placement

@pytest.mark.req("SW-RT-006")
def test_quantity_groups_of_the_registry() -> None:
    """Verifies: SW-RT-006 — quantity groups from unit / key / generated bit names (dimension when B adds it)."""
    q = {s.key: quantity_group(s) for s in [ChannelSpec("raw", "raw", "counts", "load"),
                                            ChannelSpec("x_mm", "x", "mm", "travel"),
                                            ChannelSpec("setpoint_um", "x", "µm", "DATA"),
                                            ChannelSpec("F_N", "F", "N", "load"),
                                            ChannelSpec("F_kgf", "F", "kgf", "load"),
                                            ChannelSpec("bit.valid", "VALID", "", "status.flags"),
                                            ChannelSpec("bit.drv_pwr", "DRV_PWR", "", "status.status"),
                                            ChannelSpec("lost_frames", "lost", "", "link"),
                                            ChannelSpec("rate_sps", "rate", "SPS", "link")]}
    assert q == {"raw": "Raw counts", "x_mm": "Travel", "setpoint_um": "Travel", "F_N": "Force", "F_kgf": "Force",
                 "bit.valid": "Status bits", "bit.drv_pwr": "Status bits", "lost_frames": "Link counters",
                 "rate_sps": "Sample rate"}

    class WithDim:
        key, unit, group, dimension = "k", "N", "", "length"
    assert quantity_group(WithDim()) == "Travel"


@pytest.mark.req("SW-RT-006")
def test_d63_rules_quantity_reuse_untick_manual(plot) -> None:
    """Verifies: SW-RT-006 (D-63 rules 1–4) — same quantity joins the first pane showing it; an empty pane is
    reused before a new one is created; unticking keeps the pane; a manual move wins and later channels of that
    quantity join the first pane (grid order) that shows it."""
    tree = plot.tree
    tree.set_checked("setpoint_um", True)
    tree.set_checked("x_mm", True)                                  # rule 1: µm + mm in the Travel pane
    travel = plot.pane_of("setpoint_um")
    assert plot.pane_of("x_mm") is travel and travel.units() == ("µm", "mm")
    empty = plot.add_pane()
    n = len(plot.panes())
    tree.set_checked("rate_sps", True)                               # rule 2: empty pane reused
    assert plot.pane_of("rate_sps") is empty and len(plot.panes()) == n
    tree.set_checked("rate_sps", False)                              # rule 3: pane kept
    assert empty in plot.panes() and not empty.keys()
    tree.set_checked("lost_frames", True)
    assert plot.pane_of("lost_frames") is empty                      # ... and reused again
    new = plot.move_curve_to_new_pane("x_mm")                        # rule 4: manual placement wins
    assert plot.pane_of("x_mm") is new
    tree.set_checked("x2_mm", True)
    assert plot.pane_of("x2_mm") is travel                           # first pane showing Travel in grid order
    plot.move_pane(new, 0)
    tree.set_checked("x2_mm", False)
    tree.set_checked("x2_mm", True)
    assert plot.pane_of("x2_mm") is new                              # now the first Travel pane
    assert "Travel" in travel.title_text()


@pytest.mark.req("SW-RT-006", "SW-RT-002")
def test_group_tick_only_available_children(plot, connected_fake, qtbot) -> None:
    """Verifies: SW-RT-006 (D-63 rule 5) — a group tick ticks only the available children; greyed ones stay
    unticked and the group shows partially checked."""
    tree = plot.tree
    tree.set_group_checked("Force", True)
    assert "F_kgf" in tree.checked_keys() and "F_N" not in tree.checked_keys()
    group_item = tree.item("F_N").parent()
    assert group_item.checkState(0) == Qt.CheckState.PartiallyChecked


@pytest.mark.req("SW-RT-006")
def test_third_unit_goes_to_another_pane(plot) -> None:
    """Verifies: SW-RT-006 — a pane holds at most two units; an explicit target without a free axis falls back to
    the default rule with an info text."""
    p = plot.pane_of("raw")
    plot.plot_in_pane("setpoint_um", p)
    assert plot.pane_of("setpoint_um") is p and p.units() == ("counts", "µm")
    plot.plot_in_pane("rate_sps", p)
    assert plot.pane_of("rate_sps") is not p and "two units" in plot.status_label.text()


# --------------------------------------------------------------------------------------------- grid / panes

@pytest.mark.req("SW-RT-006")
def test_columns_keep_order(plot) -> None:
    """Verifies: SW-RT-006 — 1/2/3/4 columns, row-major, pane order kept across switches; time labels only in
    the bottom pane of each column."""
    for k in ("bit.valid", "setpoint_um", "rate_sps", "lost_frames"):
        plot.tree.set_checked(k, True)
    order = plot.panes()
    assert len(order) == 5
    for n in (2, 3, 4, 1, 2):
        plot.column_buttons[n].click()
        assert plot.columns == n and plot.panes() == order
        assert [plot.grid.position_of(p) for p in order] == [(i // n, i % n) for i in range(5)]
        assert [p.x_labels for p in order] == [i + n >= 5 for i in range(5)]
    with pytest.raises(ValueError):
        plot.set_columns(5)


@pytest.mark.req("SW-RT-006")
def test_add_rename_close_pane(plot, qtbot) -> None:
    """Verifies: SW-RT-006 — "+ Pane", inline rename (Enter applies, empty = automatic title), close unticks the
    pane's channels; the last pane cannot be closed."""
    plot.add_pane_button.click()
    assert len(plot.panes()) == 2 and "(empty)" in plot.pane(1).title_text()
    p = plot.pane_of("raw")
    p.title.begin_rename()
    p.title.editor.setText("Load cell")
    qtbot.keyClick(p.title.editor, Qt.Key.Key_Return)
    assert p.custom_title == "Load cell" and p.title_text() == "Pane 1 · Load cell"
    plot.rename_pane(p, "")
    assert p.custom_title is None and "Raw counts" in p.title_text()
    plot.close_pane(p)
    assert "raw" not in plot.tree.checked_keys() and p not in plot.panes()
    last = plot.panes()[0]
    assert not last.title.close_button.isEnabled()
    plot.close_pane(last)
    assert plot.panes() == [last]


@pytest.mark.req("SW-RT-006")
def test_channel_and_pane_menus(plot) -> None:
    """Verifies: SW-RT-006 — channel menu "Plot in pane N" / "New pane" / "Move to pane"; pane menu "Move curve",
    "Move to window…", "Close pane"."""
    plot.add_pane()
    menu = plot.build_channel_menu("rate_sps")
    sub = next(a.menu() for a in menu.actions() if a.text() == "Plot in pane")
    [a for a in sub.actions() if a.text() == "Pane 2"][0].trigger()
    assert plot.pane_of("rate_sps") is plot.pane(1)
    menu = plot.build_channel_menu("rate_sps")
    sub = next(a.menu() for a in menu.actions() if a.text() == "Move to pane")
    texts = [a.text() for a in sub.actions() if a.text()]
    assert "Pane 2 (current)" in texts and "New pane" in texts
    [a for a in sub.actions() if a.text() == "New pane"][0].trigger()
    assert plot.pane_of("rate_sps") is plot.pane(2)
    pm = plot.build_pane_menu(plot.pane(2))
    names = [a.text() for a in pm.actions()]
    assert {"Rename…", "Move curve", "Move to window…", "Close pane"} <= set(names)


@pytest.mark.req("SW-RT-006")
def test_drag_drop_reorder_and_title_drag(plot, qtbot) -> None:
    """Verifies: SW-RT-006 — dragging a pane onto another cell inserts it there (indicator while hovering); the
    title-strip gesture starts the pane drag; the pane MIME resolves to the live pane."""
    plot.tree.set_checked("bit.valid", True)
    plot.tree.set_checked("rate_sps", True)
    plot.set_columns(3)
    qtbot.waitExposed(plot)
    qtbot.wait(20)                                                   # grid layout applied
    p1, p2, p3 = plot.panes()
    _drag_drop(plot.grid, pane_mime(p3), p1.geometry().center(), drop=False)
    assert plot.grid.indicator.isVisible() and plot.grid.drop_index == 0
    _drag_drop(plot.grid, pane_mime(p3), p1.geometry().center())
    assert plot.panes() == [p3, p1, p2] and not plot.grid.indicator.isVisible()
    assert pane_from_mime(pane_mime(p2)) is p2 and pane_from_mime(QMimeData()) is None
    started = []
    p2.drag_handler = started.append
    start = p2.title.rect().center()
    qtbot.mousePress(p2.title, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(p2.title, start + QPoint(40, 0))
    assert started == [p2]


@pytest.mark.req("SW-RT-006")
def test_x_axis_linked_within_window(plot) -> None:
    """Verifies: SW-RT-006 — time panes of one window share the X range (relative [−window, 0]; a user zoom in one
    pane is applied to all); the X-Y pane is not time-linked."""
    plot.tree.set_checked("rate_sps", True)
    xy = plot.add_xy_pane()
    a, b = plot.time_panes()
    a.plot_item.vb.setXRange(-12.0, -2.0, padding=0)
    assert b.x_range() == pytest.approx((-12.0, -2.0))
    assert xy.x_range() != pytest.approx((-12.0, -2.0))
    plot.set_window_s(60)
    assert a.x_range() == pytest.approx((-60.0, 0.0)) and b.x_range() == pytest.approx((-60.0, 0.0))


@pytest.mark.req("SW-RT-006", "SW-RT-003")
def test_xy_pane(window, plot, connected_fake) -> None:
    """Verifies: SW-RT-006, SW-RT-003 — X-Y (travel–load) pane: x from the travel channels, y force (when
    available) else raw; data.xy(x, y, window) each refresh; live point = newest sample; never a default target."""
    xy = plot.add_xy_pane()
    assert isinstance(xy, XYPane) and xy.x_key in ("setpoint_um", "x_mm") and xy.y_key in ("F_kgf", "raw")
    assert xy.y_combo.findData("F_N") < 0                            # unavailable channel not offered
    tick(window)
    assert connected_fake.data.xy_calls[-1] == (xy.x_key, xy.y_key, plot.window_s)
    x, y = xy.xy_curve.getData()
    assert len(x) == 200 and xy.live_point.data["x"][0] == pytest.approx(10.0)
    plot.tree.set_checked("lost_frames", True)
    assert plot.pane_of("lost_frames") is not xy and not xy.keys()
    xy.x_combo.setCurrentIndex(xy.x_combo.findData("x_mm"))
    xy.y_combo.setCurrentIndex(xy.y_combo.findData("raw"))
    assert xy.layout_entry() == {"type": "xy", "x": "x_mm", "y": "raw"} and "X-Y" in xy.title_text()


# --------------------------------------------------------------------------------------------- windows

@pytest.mark.req("SW-RT-006", "SW-RT-001", "SW-STOP-001")
def test_move_pane_to_other_window_menu_and_drop(window, plot, qtbot) -> None:
    """Verifies: SW-RT-006, SW-RT-001 — "Move to window…" and dropping a pane onto another plot window move it
    with its curves (ticked there, unticked here); every new window carries STOP; max. 4 windows."""
    plot.tree.set_checked("setpoint_um", True)
    other = window.new_plot_window()
    assert other is not None and other.windowTitle() == "Plot 2"
    assert len([b for b in other.findChildren(StopButton) if b.isVisibleTo(other)]) == 1
    moving = plot.pane_of("setpoint_um")
    menu = plot.build_pane_menu(moving)
    win_menu = next(a.menu() for a in menu.actions() if a.text() == "Move to window…")
    [a for a in win_menu.actions() if a.text() == "Plot 2"][0].trigger()
    assert other.pane_of("setpoint_um") is moving and plot.pane_of("setpoint_um") is None
    assert "setpoint_um" in other.tree.checked_keys() and "setpoint_um" not in plot.tree.checked_keys()
    assert len(other.panes()) == 1                                   # the empty placeholder was replaced
    other.setFloating(True)
    other.show()
    qtbot.waitExposed(other)
    qtbot.wait(20)
    raw_pane = plot.pane_of("raw")
    _drag_drop(other.grid, pane_mime(raw_pane), other.pane(0).geometry().center())
    assert other.panes()[0] is raw_pane and other.pane_of("raw") is raw_pane
    assert "raw" in other.tree.checked_keys() and plot.pane_of("raw") is None
    assert len(plot.panes()) == 1                                    # a window always keeps one pane
    for _ in range(3):
        window.new_plot_window()
    assert len(window.plot_docks) == 4 and "At most 4" in window.toast_label.text()


@pytest.mark.req("SW-RT-006", "NFR-001")
def test_one_snapshot_per_time_window(window, plot, connected_fake, qtbot) -> None:
    """Verifies: SW-RT-006, NFR-001 — one data.snapshot per distinct time window for all panes of all shown,
    non-frozen windows (union of keys, widest px); a frozen or hidden window takes no part."""
    for k in ("bit.valid", "setpoint_um", "rate_sps"):
        plot.tree.set_checked(k, True)
    plot.set_columns(2)
    assert len(plot.time_panes()) == 4
    other = window.new_plot_window()
    other.setFloating(True)
    other.show()
    qtbot.waitExposed(other)
    other.tree.set_checked("lost_frames", True)
    qtbot.wait(20)                                                   # Plot 1 visible again (other floats)
    assert plot.is_shown() and other.is_shown()
    data = connected_fake.data
    data.snapshot_args.clear()
    tick(window)
    assert len(data.snapshot_args) == 1
    keys, w, _px = data.snapshot_args[0]
    assert set(keys) == {"raw", "bit.valid", "setpoint_um", "rate_sps", "lost_frames"} and w == 30.0
    other.set_window_s(60)
    data.snapshot_args.clear()
    tick(window)
    assert sorted(a[1] for a in data.snapshot_args) == [30.0, 60.0]
    other.set_frozen(True)
    data.snapshot_args.clear()
    tick(window)
    assert [a[1] for a in data.snapshot_args] == [30.0]
    other.hide()
    other.set_frozen(False)
    data.snapshot_args.clear()
    tick(window)
    assert [a[1] for a in data.snapshot_args] == [30.0]


@pytest.mark.req("SW-RT-006", "SW-RT-001")
def test_layout_saved_and_restored(make_window, connected_fake, qtbot) -> None:
    """Verifies: SW-RT-006, SW-RT-001 — columns, pane order, curves per pane (incl. empty panes), user titles,
    X-Y panes, window length and a second plot window are restored at the next start."""
    win = make_window(connected_fake)
    d = win.plot_dock
    for k in ("bit.valid", "setpoint_um"):
        d.tree.set_checked(k, True)
    d.add_pane()
    d.add_xy_pane()
    d.set_columns(3)
    d.move_pane(d.pane_of("setpoint_um"), 0)
    d.rename_pane(d.pane_of("raw"), "Load")
    d.set_window_s(60)
    other = win.new_plot_window()
    other.tree.set_checked("rate_sps", True)
    before = win.layout_state()
    win._force_close = True
    win.close()
    connected_fake.shutdowns = 0
    win2 = make_window(connected_fake)
    after = win2.layout_state()
    assert after == before
    d2 = win2.plot_dock
    assert d2.columns == 3 and d2.window_s == 60.0 and isinstance(d2.panes()[-1], XYPane)
    assert [p.keys() for p in d2.time_panes()] == [["setpoint_um"], ["raw"], ["bit.valid"], []]
    assert d2.pane_of("raw").custom_title == "Load"
    assert len(win2.plot_docks) == 2 and win2.plot_docks[1].pane_of("rate_sps") is not None


@pytest.mark.req("SW-RT-006")
def test_corrupt_layout_gives_default(make_window, connected_fake, tmp_path) -> None:
    """Verifies: SW-RT-006 — a corrupt settings entry is ignored (default layout, no exception)."""
    from bend_stand.gui.settings import make_settings
    s = make_settings(tmp_path / "mw.ini")
    s.setValue("plots/layout", "{not json")
    s.sync()
    win = make_window(connected_fake)
    assert win.plot_dock.checked_keys() == ["raw"] and len(win.plot_docks) == 1


@pytest.mark.req("SW-RT-006", "SW-RT-003")
def test_pane_vstate_and_autoscale_per_pane(window, plot, connected_fake) -> None:
    """Verifies: SW-RT-006, SW-RT-003 — every pane autoscales its own axes from the shared snapshot; a mouse zoom
    on Y pauses the autoscale of that pane only."""
    plot.tree.set_checked("setpoint_um", True)
    tick(window)
    a, b = plot.pane_of("raw"), plot.pane_of("setpoint_um")
    assert a is not b and a.autoscale and b.autoscale
    a._on_manual_range([False, True])
    assert not a.autoscale and b.autoscale
    rng = a.y_range()
    connected_fake.data.vstate["raw"] = np.full(plot.px_width(), 1, np.uint8)
    tick(window)
    assert a.y_range() == rng and 1 in a.style_curves("raw")
