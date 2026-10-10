"""NFR-009 (D-54 b): plot render backend selectable (View ▸ OpenGL rendering, ``gui.ini`` / env override), raster
default, fallback to raster when OpenGL cannot be used; X-Y pane point budget from its width.

Verifies: NFR-009, NFR-001
"""
from __future__ import annotations

import pytest
from fakes import tick
from PySide6.QtWidgets import QApplication

from bend_stand.gui.plots import render


@pytest.fixture(autouse=True)
def _reset_render(monkeypatch):
    monkeypatch.delenv(render.ENV, raising=False)
    saved = dict(render.STATE)
    yield
    render.STATE.clear()
    render.STATE.update(saved)


@pytest.mark.req("NFR-009")
def test_wanted_env_overrides_setting(monkeypatch) -> None:
    """Verifies: NFR-009 — default off; the stored preference turns it on; the env variable wins both ways."""
    assert render.wanted(None) is False and render.wanted("false") is False and render.wanted("true") is True
    monkeypatch.setenv(render.ENV, "0")
    assert render.wanted("true") is False
    monkeypatch.setenv(render.ENV, "1")
    assert render.wanted(False) is True


@pytest.mark.req("NFR-009", "NFR-001")
def test_xy_point_budget() -> None:
    """Verifies: NFR-009 — ≈ 2 points per pixel column, bounded 400…4000."""
    assert render.xy_points(0) == 400 and render.xy_points(600) == 1200 and render.xy_points(5000) == 4000


@pytest.mark.req("NFR-009")
def test_default_is_raster(window) -> None:
    """Verifies: NFR-009, NFR-001 — without a preference every pane uses the raster viewport; the menu item is off."""
    assert not window.opengl_action.isChecked()
    assert all(p.render_mode == "raster" for d in window.plot_docks for p in d.panes())


@pytest.mark.req("NFR-009")
def test_switch_failure_falls_back_to_raster(window, monkeypatch) -> None:
    """Verifies: NFR-009 — if the GL viewport cannot be created the pane stays raster, OpenGL is marked failed with the
    reason, the menu item unticks and a message says so; the preference is stored."""
    import pyqtgraph as pg

    def boom(self, b=True):
        if b:
            raise RuntimeError("no OpenGL driver")
        return None
    monkeypatch.setattr(pg.GraphicsView, "useOpenGL", boom)
    render.STATE.update(opengl=False, failed=False, reason="")
    window.set_opengl(True)
    assert render.STATE["failed"] and "no OpenGL driver" in render.STATE["reason"]
    assert all(p.render_mode == "raster" for d in window.plot_docks for p in d.panes())
    assert not window.opengl_action.isChecked()
    assert "OpenGL rendering not available" in window.toast_label.text()
    assert str(window.settings.value("ui/opengl")).lower() in ("true", "1")


@pytest.mark.req("NFR-009")
def test_gl_viewport_checked_after_show(window) -> None:
    """Verifies: NFR-009 — with OpenGL on, a pane gets the GL viewport; after the first show an invalid context
    (e.g. offscreen) switches it back to raster; a valid one keeps GL. Off again → raster."""
    render.STATE.update(opengl=False, failed=False, reason="")
    window.set_opengl(True)
    panes = [p for d in window.plot_docks for p in d.panes()]
    QApplication.processEvents()
    for p in panes:
        p._check_render()                                                 # noqa: SLF001 - as the deferred check
    for p in panes:
        vp = p.plot_widget.viewport()
        gl = type(vp).__name__ == "GraphicsViewGLWidget"
        assert p.render_mode == ("opengl" if gl else "raster")
        if gl:
            assert vp.isValid()
    if render.STATE["failed"]:
        assert all(p.render_mode == "raster" for p in panes)
    window.set_opengl(False)
    assert all(p.render_mode == "raster" for p in panes)
    tick(window)


@pytest.mark.req("NFR-009")
def test_xy_pane_requests_width_budget(window, connected_fake) -> None:
    """Verifies: NFR-009 — the X-Y pane asks ``data.xy`` for max_points = 2 × its width (bounded)."""
    calls: list[dict] = []
    orig = connected_fake.data.xy

    def spy(*a, **k):
        calls.append(k)
        return orig(*a, **k)
    connected_fake.data.xy = spy
    pane = window.plot_dock.add_xy_pane()
    window._reload_channels()                                             # noqa: SLF001
    if not (pane.x_key and pane.y_key):
        pytest.skip("fake registry offers no X-Y candidates")
    tick(window)
    assert calls and calls[-1].get("max_points") == render.xy_points(pane.px_width())


@pytest.mark.req("NFR-009", "NFR-001")
def test_unchanged_curve_data_is_not_repainted(qapp) -> None:
    """Verifies: NFR-009 — identical data under the same view range is skipped (no geometry change / update);
    changed data, or a NaN pattern change, is applied."""
    import numpy as np

    from bend_stand.gui.plots.plot_pane import FastCurve
    c = FastCurve(pen="k", name="lane")
    x = np.linspace(-600.0, 0.0, 400)
    y = np.zeros(400)
    c.set_xy(x, y)
    n = FastCurve.skipped
    c.set_xy(x.copy(), y.copy())
    assert FastCurve.skipped == n + 1
    y2 = y.copy()
    y2[-1] = 1.0
    c.set_xy(x, y2)
    assert FastCurve.skipped == n + 1 and c.yData[-1] == 1.0
    y3 = y2.copy()
    y3[5] = np.nan
    c.set_xy(x, y3)
    assert FastCurve.skipped == n + 1 and c.opts["connect"] == "finite"
    c.set_xy(x, y3.copy())
    assert FastCurve.skipped == n + 2                                  # NaN == NaN for the comparison


@pytest.mark.req("NFR-009")
def test_plot_dock_set_opengl_hook(window) -> None:
    """Verifies: NFR-009 — ``plot_dock.set_opengl`` (perf harness hook) switches every live plot window; off → raster."""
    from bend_stand.gui.plots import plot_dock
    render.STATE.update(opengl=False, failed=False, reason="")
    modes = plot_dock.set_opengl(True)
    assert modes and set(modes) <= {"opengl", "raster"}
    assert set(plot_dock.set_opengl(False)) == {"raster"}


@pytest.mark.req("NFR-009", "NFR-001")
def test_changed_thin_curve_repaints_pane_within_one_tick(window) -> None:
    """Verifies: NFR-009 (OBS-F-NFR9-01) — a constant curve (thin bounding rect) whose gap pattern changes makes its
    pane paint at the next event-loop pass (whole viewport scheduled by commit_view); an unchanged snapshot schedules
    no paint of that pane."""
    import numpy as np
    from PySide6.QtCore import QEvent, QObject

    from bend_stand.core.api import PlotSnapshot, SeriesMinMax
    pane = window.plot_dock.time_panes()[0]
    assert pane.add_curve("vstate", "Display state", "")
    window.plot_dock.show()
    QApplication.processEvents()
    paints: list[int] = []

    class Probe(QObject):
        def eventFilter(self, obj, ev):  # noqa: N802
            if ev.type() == QEvent.Type.Paint:
                paints.append(1)
            return False
    probe = Probe()
    vp = pane.plot_widget.viewport()
    vp.installEventFilter(probe)
    t = np.linspace(-600.0, 0.0, 200)

    def snap(gap_at: int | None) -> PlotSnapshot:
        lo = np.zeros(200, np.float32)
        if gap_at is not None:
            lo[gap_at] = np.nan
        return PlotSnapshot(100.0, t, {"vstate": SeriesMinMax(lo, lo.copy(), np.zeros(200, np.uint8))}, 0)
    for _ in range(3):                                # steady state: autoscale range settled, data applied
        pane.set_snapshot(snap(None))
        pane.commit_view()
        for _ in range(3):
            QApplication.processEvents()
    paints.clear()
    pane.set_snapshot(snap(None))                     # unchanged → skipped, no paint scheduled
    pane.commit_view()
    QApplication.processEvents()
    assert paints == []
    pane.set_snapshot(snap(150))                      # a gap appears in a constant lane → repaint now
    pane.commit_view()
    QApplication.processEvents()
    assert paints, "changed pane was not repainted"
    vp.removeEventFilter(probe)


@pytest.mark.req("NFR-009")
def test_commit_view_schedules_viewport_update_only_when_changed(window) -> None:
    """Verifies: NFR-009 (OBS-F-NFR9-01) — commit_view() calls viewport().update() iff a curve of the pane was really
    updated since the last commit (skipped / unchanged curves do not)."""
    import numpy as np

    from bend_stand.core.api import PlotSnapshot, SeriesMinMax
    pane = window.plot_dock.time_panes()[0]
    assert pane.add_curve("vstate", "Display state", "")
    vp = pane.plot_widget.viewport()
    calls: list[int] = []
    orig = vp.update
    vp.update = lambda *a: (calls.append(1), orig(*a))[1]          # instance-level spy on the kept wrapper
    t = np.linspace(-600.0, 0.0, 100)
    lo = np.zeros(100, np.float32)
    snap = PlotSnapshot(1.0, t, {"vstate": SeriesMinMax(lo, lo.copy(), np.zeros(100, np.uint8))}, 0)
    for _ in range(3):
        pane.set_snapshot(snap)
        pane.commit_view()
    calls.clear()
    pane.set_snapshot(snap)
    pane.commit_view()
    assert calls == []
    lo2 = lo.copy()
    lo2[50] = np.nan
    pane.set_snapshot(PlotSnapshot(1.1, t, {"vstate": SeriesMinMax(lo2, lo2.copy(), np.zeros(100, np.uint8))}, 0))
    pane.commit_view()
    assert calls == [1]
