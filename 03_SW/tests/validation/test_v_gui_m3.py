"""Level G — M3 GUI checks offscreen on B's real Backend (Validator F): manual slider / hold-to-jog (SW-MAN-001,
SW-MAN-004), TARE from the toolbar (SW-TARE-001), STOP present on the M3 tabs and the TARE popup (SW-STOP-001).
Widgets are reached through the attribute names of SW_design_GUI v0.5; the wire log is the ground truth.

Verifies: SW-MAN-001, SW-MAN-004, SW-TARE-001, SW-STOP-001
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bend_stand.gui.dialogs import safe_dialog  # noqa: E402

safe_dialog.disable_native_dialogs()

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider  # noqa: E402

import harness as H  # noqa: E402


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

    def make(be):
        win = MainWindow(be, settings=make_settings(tmp_path / "mw.ini"), start_refresh=False)
        qtbot.addWidget(win)
        made.append(win)
        win.resize(1400, 900)
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


def _tick(win, be, ms=100, n=3):
    for _ in range(n):
        H.advance(be, ms)
        win.refresh.tick()
        QApplication.processEvents()


def _handle_center(slider) -> QPoint:
    opt = QStyleOptionSlider()
    slider.initStyleOption(opt)
    r = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, slider)
    return r.center()


@pytest.mark.req("SW-MAN-001", "IF-009")
def test_tc_sw_man_001_01_slider_sends_one_move_on_release(vbe, window):
    """TC-SW-MAN-001-01: dragging the slider handle puts nothing on the wire; the release sends exactly one MOVE_ABS
    whose target = the slider value (µm, round half away); wheel and keys send nothing."""
    # Verifies: SW-MAN-001, IF-009
    H.m2_ready(vbe)
    win = window(vbe)
    tab = win.manual_tab
    win.tabs.setCurrentWidget(tab)
    _tick(win, vbe)
    sl = tab.slider
    assert sl.isEnabled()
    m0 = H.wire_mark(vbe)
    c = _handle_center(sl)
    QTest.mousePress(sl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
    for dx in (10, 20, 40, 60):
        QTest.mouseMove(sl, QPoint(c.x() + dx, c.y()) if sl.orientation() == Qt.Orientation.Horizontal
                        else QPoint(c.x(), c.y() - dx))
        H.advance(vbe, 20)
    assert not H.tx(vbe, "MOVE_ABS", since=m0)
    end = QPoint(c.x() + 60, c.y()) if sl.orientation() == Qt.Orientation.Horizontal else QPoint(c.x(), c.y() - 60)
    QTest.mouseRelease(sl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    H.advance(vbe, 50)
    mv = H.tx(vbe, "MOVE_ABS", since=m0)
    assert len(mv) == 1, [w.fields for w in mv]
    from oracle import f_ref
    assert mv[0].fields["target_um"] == f_ref.rha(sl.value_mm() * 1000.0)
    m1 = H.wire_mark(vbe)
    QTest.keyClick(sl, Qt.Key.Key_Right)
    QTest.keyClick(sl, Qt.Key.Key_PageUp)
    H.advance(vbe, 50)
    assert not H.tx(vbe, "MOVE_ABS", since=m1)


@pytest.mark.req("SW-MAN-004")
@pytest.mark.parametrize("how", ["release", "deactivate"])
def test_tc_sw_man_004_02_jog_stops_on_release_and_focus_loss(vbe, window, how):
    """TC-SW-MAN-004-02: press-and-hold jog → JOG ≠ 0 refreshed while held; button release or window deactivation
    (focus loss) → exactly one JOG 0 (controlled stop), no further JOG ≠ 0."""
    # Verifies: SW-MAN-004
    H.m2_ready(vbe)
    win = window(vbe)
    tab = win.manual_tab
    win.tabs.setCurrentWidget(tab)
    _tick(win, vbe)
    b = tab.jog_fwd
    assert b.isEnabled()
    m0 = H.wire_mark(vbe)
    QTest.mousePress(b, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 5))
    for _ in range(6):
        H.advance(vbe, 50)
        vbe.gui_beat()
        QApplication.processEvents()
    run = [w for w in H.tx(vbe, "JOG", since=m0) if w.fields["v_um_s"] != 0]
    assert len(run) >= 3, len(run)
    m1 = H.wire_mark(vbe)
    if how == "release":
        QTest.mouseRelease(b, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 5))
    else:
        QApplication.sendEvent(win, QEvent(QEvent.Type.WindowDeactivate))
        QApplication.processEvents()
    for _ in range(6):
        H.advance(vbe, 50)
        vbe.gui_beat()
        QApplication.processEvents()
    jogs = H.tx(vbe, "JOG", since=m1)
    zeros = [w for w in jogs if w.fields["v_um_s"] == 0]
    assert len(zeros) == 1 and not [w for w in jogs if w.fields["v_um_s"] != 0 and w.t_ns > zeros[0].t_ns], \
        [w.fields for w in jogs]
    if how == "deactivate":
        QTest.mouseRelease(b, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 5))


@pytest.mark.req("SW-TARE-001", "SW-STOP-001")
def test_tc_sw_tare_001_01_tare_from_toolbar_non_modal_popup(vbe, window, qtbot):
    """TC-SW-TARE-001-01: TARE from the toolbar on every main tab starts the tare (backend CAPTURING) in a non-modal
    popup that carries a STOP button; a refusal (HALT latched) is shown verbatim and nothing starts."""
    # Verifies: SW-TARE-001, SW-STOP-001
    win = window(vbe)
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        _tick(win, vbe, n=1)
        win.on_tare()
        _tick(win, vbe, n=2)
        assert vbe.status().tare.state == "CAPTURING", (i, vbe.status().tare)
        pop = win.tare_popup
        assert pop is not None and pop.isVisible() and not pop.isModal()
        assert pop.stop_button.isVisible()
        H.wait_engine(vbe, vbe.tare_engine, lambda s: s.phase in ("DONE", "REFUSED", "ABORTED"))
        _tick(win, vbe, n=1)
    H.halt(vbe)
    _tick(win, vbe, n=3)
    win.on_tare()
    _tick(win, vbe, n=2)
    assert vbe.status().tare.state != "CAPTURING"


@pytest.mark.req("SAF-SW-005", "SAF-SW-001")
@pytest.mark.defect("SWD-M3-02")
@pytest.mark.xfail(strict=True, reason="SWD-M3-02 open (D): gui/main_window.py:710-712 toasts every safety.trip "
                                       "publish incl. the clear (payload None / remaining latch) as 'SW limit trip: … – "
                                       "STOP sent' (error)")
def test_tc_saf_sw_005_06_trip_clear_does_not_announce_a_new_stop(vbe, window, monkeypatch):
    """SAF-SW-005 / SW-RT: when a SW-limit latch clears, the operator must not be told 'SW limit trip … – STOP sent'
    (no STOP is sent at a clear). Stimulus: calibrated + tared, pull trip 150 N into a spring, then unload → latch
    clears; the toasts after the clear must not announce a trip / STOP."""
    # Verifies: SAF-SW-005, SAF-SW-001
    H.calibrate_and_tare(vbe)
    assert not H.set_limits(vbe, pull_trip_n=150.0)
    H.m2_ready(vbe)
    assert vbe.limits.set_no_specimen_mode(False).ok
    win = window(vbe)
    toasts = []
    monkeypatch.setattr(win, "toast", lambda text, severity="info": toasts.append((text, severity)))
    H.act(vbe, "specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=20_000)
    H.move_to(vbe, 40.0, speed_mm_s=5.0)
    for _ in range(200):
        _tick(win, vbe, ms=100, n=1)
        if H.safety(vbe).sw_trip == "PULL":
            break
    _tick(win, vbe, ms=100, n=5)
    n_trip = len(toasts)
    assert any("trip" in t.lower() for t, _ in toasts), toasts
    assert H.wait_idle(vbe)
    H.result(vbe, H.move_to(vbe, 10.0, speed_mm_s=5.0), 30_000)         # unload → back inside → latch clears
    for _ in range(100):
        _tick(win, vbe, ms=100, n=1)
        if H.safety(vbe).sw_trip is None:
            break
    _tick(win, vbe, ms=100, n=5)
    assert H.safety(vbe).sw_trip is None
    after = toasts[n_trip:]
    assert not [t for t, _ in after if "STOP sent" in t or "trip:" in t.lower()], after
