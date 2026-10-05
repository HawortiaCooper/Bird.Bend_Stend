"""M3 GUI on B's real ``Backend`` + in-process simulator, lock-step clock (deterministic; D-06: no port): the Manual
tab drives the real ``MotionController`` (enable, HOME with C-01, step buttons → MOVE_ABS targets, slider release →
exactly one MOVE_ABS, Go to, hold-to-jog → JOG / JOG 0, test zero), the Safety-limits tab applies SW travel limits
to the real ``limits.set`` (slider range follows), and the M3 actions whose engines are not yet delivered by B
(tare, calibration wizards, no-specimen mode) show the backend's refusal verbatim (or run, once delivered).

Verifies: SW-MAN-001, SW-MAN-002, SW-MAN-003, SW-MAN-004, SW-MAN-006, SAF-SW-004, SW-LIM-001, SW-TARE-001,
SW-CAL-001, SW-STOP-001
"""
from __future__ import annotations

import struct

import pytest
from fakes import tick
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

backend_mod = pytest.importorskip("bend_stand.core.backend")

from bend_stand.core import protocol_gen as pg  # noqa: E402
from bend_stand.core.api import GateId  # noqa: E402
from bend_stand.gui import gating  # noqa: E402

Cmd = pg.Cmd


@pytest.fixture
def lock_be(tmp_path):
    be = backend_mod.Backend(backend_mod.BackendSettings(
        clock="lockstep", test_hooks=True, wire_log=True, hotkey="off", sim_nvm_path=str(tmp_path / "nvm.json"),
        recordings_root=str(tmp_path / "rec")))
    be.start()
    h = be.test_hooks
    h.result(be.connect_async("sim"), 5000)
    h.advance(50)
    h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}), 5000)
    yield be
    be.shutdown()


@pytest.fixture
def lock_win(make_window, lock_be):
    win = make_window(lock_be)
    win.tabs.setCurrentWidget(win.manual_tab)
    step(win, lock_be, 50)
    return win


def step(win, be, ms: float = 33.0) -> None:
    """Advance the lock-step backend, deliver queued signals, run one GUI refresh tick."""
    be.test_hooks.advance(ms)
    QApplication.processEvents()
    tick(win)


def run_until(win, be, pred, ms: float = 60_000.0) -> bool:
    t = 0.0
    while t < ms:
        if pred():
            return True
        step(win, be, 50.0)
        t += 50.0
    return pred()


def tx(be, cmd) -> list:
    return [r for r in be.test_hooks.wire_log() if r.direction == "TX" and r.frame[2] == int(cmd)]


def move_targets_um(be) -> list[int]:
    return [struct.unpack_from("<i", r.frame, 6)[0] for r in tx(be, Cmd.MOVE_ABS)]


def enter_no_specimen(win, be, qtbot) -> None:
    """M3 (B5-03): without calibration + tare the motion gates refuse LOAD_INPUT_INVALID → enter the no-specimen
    mode through the GUI (Safety-limits tab, C-10) first."""
    move = be.status().gates[GateId.MOVE]
    if "LOAD_INPUT_INVALID" not in move.codes():
        return
    g = be.status().gates[GateId.NO_SPECIMEN]
    if "NOT_IMPLEMENTED" in g.codes():
        pytest.skip("backend no-specimen mode not delivered yet (B M3 in progress, B5-02)")
    lt = win.limits_tab
    win.tabs.setCurrentWidget(lt)
    step(win, be)
    qtbot.mouseClick(lt.nospec_enter, Qt.MouseButton.LeftButton)
    dlg = lt.confirm_dialog
    assert dlg is not None and dlg.cid == "C-10"
    dlg.assertion_box.setChecked(True)
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: be.status().safety.no_specimen_mode, 2000)
    assert run_until(win, be, lambda: "LOAD_INPUT_INVALID" not in be.status().gates[GateId.MOVE].codes(), 5000)
    step(win, be)
    assert win.mode_banner.isVisible() and win.indicator_bar.chip("NOSPEC").isVisibleTo(win.indicator_bar)
    win.tabs.setCurrentWidget(win.manual_tab)
    step(win, be)


def ready(win, be, qtbot) -> None:
    """Enable (checkbox) and HOME (C-01: load unknown without calibration) through the Manual tab."""
    enter_no_specimen(win, be, qtbot)
    tab = win.manual_tab
    assert run_until(win, be, lambda: tab.enable_box.isEnabled(), 2000)
    qtbot.mouseClick(tab.enable_box, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: be.status().motion.enabled and tab.enable_box.isChecked(), 3000)
    assert run_until(win, be, lambda: tab.home_button.isEnabled(), 3000)
    qtbot.mouseClick(tab.home_button, Qt.MouseButton.LeftButton)
    dlg = tab.confirm_dialog
    assert dlg is not None and dlg.cid == "C-01" and not tx(be, Cmd.HOME)       # nothing sent before the confirm
    dlg.assertion_box.setChecked(True)
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: be.status().motion.homed and not be.status().motion.moving, 60_000)
    assert tx(be, Cmd.HOME)[0].frame[6] == 1                                    # load_confirmed flag
    assert run_until(win, be, lambda: "HOMED" in tab.home_state.text(), 1000)


@pytest.mark.req("SW-MAN-006", "SAF-SW-004", "SW-MAN-003", "SW-MAN-002")
def test_manual_enable_home_steps_goto(lock_win, lock_be, qtbot) -> None:
    """Verifies: SW-MAN-006, SAF-SW-004, SW-MAN-002, SW-MAN-003 — enable + HOME (C-01) through the tab; three +1 mm
    clicks while idle → the wire carries absolute targets accumulating on the commanded target; Go to distance /
    absolute → absolute targets only."""
    win, be = lock_win, lock_be
    tab = win.manual_tab
    ready(win, be, qtbot)
    x0 = be.status().motion.commanded_target_mm or be.status().motion.position_mm
    plus1 = next(b for b in tab.step_buttons if b.text() == "+1")
    for _ in range(3):
        assert run_until(win, be, lambda: plus1.isEnabled() and not be.status().motion.moving, 20_000)
        qtbot.mouseClick(plus1, Qt.MouseButton.LeftButton)
        assert run_until(win, be, lambda: be.status().motion.moving or not be.motion.busy, 2000)
        assert run_until(win, be, lambda: not be.status().motion.moving and not be.motion.busy, 20_000)
    t = move_targets_um(be)
    assert t[-3:] == [round((x0 + d) * 1000) for d in (1, 2, 3)], t
    assert abs(be.status().motion.commanded_target_mm - (x0 + 3)) < 1e-6
    tab.goto_spin.setValue(-2.0)
    qtbot.mouseClick(tab.go_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: len(move_targets_um(be)) == 4 and not be.motion.busy, 20_000)
    assert move_targets_um(be)[-1] == round((x0 + 1) * 1000)
    tab.abs_radio.setChecked(True)
    tab.goto_spin.setValue(20.0)
    qtbot.mouseClick(tab.go_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: len(move_targets_um(be)) == 5 and not be.motion.busy, 30_000)
    assert move_targets_um(be)[-1] == 20_000
    assert run_until(win, be, lambda: "MOVE_DONE TARGET" in tab.last_line.text(), 2000)


@pytest.mark.req("SW-MAN-001", "SW-MAN-004", "SW-MAN-006")
def test_manual_slider_jog_test_zero(lock_win, lock_be, qtbot) -> None:
    """Verifies: SW-MAN-001, SW-MAN-004, SW-MAN-006 — slider drag sends nothing, release exactly one MOVE_ABS;
    hold-to-jog → JOG frames (refreshed by the backend) and JOG 0 on release; set test zero."""
    win, be = lock_win, lock_be
    tab = win.manual_tab
    ready(win, be, qtbot)
    n0 = len(tx(be, Cmd.MOVE_ABS))
    s = tab.slider
    s.setSliderDown(True)
    for v in (30_000, 40_000, 50_000):
        s.setSliderPosition(v)
        step(win, be)
    assert len(tx(be, Cmd.MOVE_ABS)) == n0
    s.setSliderDown(False)
    step(win, be)
    assert len(tx(be, Cmd.MOVE_ABS)) == n0 + 1 and move_targets_um(be)[-1] == 50_000
    assert run_until(win, be, lambda: not be.status().motion.moving and not be.motion.busy, 30_000)
    j0 = len(tx(be, Cmd.JOG))
    qtbot.mousePress(tab.jog_fwd, Qt.MouseButton.LeftButton)
    for _ in range(10):
        step(win, be, 33.0)
    assert len(tx(be, Cmd.JOG)) >= j0 + 3                         # start + backend refreshes (< 100 ms)
    assert be.status().motion.jogging
    qtbot.mouseRelease(tab.jog_fwd, Qt.MouseButton.LeftButton)
    step(win, be)
    last = tx(be, Cmd.JOG)[-1]
    assert struct.unpack_from("<i", last.frame, 6)[0] == 0          # JOG 0 = controlled stop
    assert run_until(win, be, lambda: not be.status().motion.moving, 10_000)
    qtbot.mouseClick(tab.zero_button, Qt.MouseButton.LeftButton)
    step(win, be)
    m = be.status().motion
    assert abs(m.x_zero_mm - m.position_mm) < 1e-6 and abs(m.test_position_mm) < 1e-6


@pytest.mark.req("SW-LIM-001")
def test_limits_tab_applies_travel_limits(lock_win, lock_be, qtbot) -> None:
    """Verifies: SW-LIM-001 — the Safety-limits tab writes SW travel limits through the real limits.set; outside
    the FW soft limits → per-field issue; the Manual slider range follows the applied limits."""
    win, be = lock_win, lock_be
    lt = win.limits_tab
    win.tabs.setCurrentWidget(lt)
    step(win, be)
    lt.min_en.setChecked(True)
    lt.min_spin.setValue(10.0)
    lt.max_en.setChecked(True)
    lt.max_spin.setValue(5000.0)
    qtbot.mouseClick(lt.apply_button, Qt.MouseButton.LeftButton)
    assert "outside the FW soft limits" in lt.issue_labels["travel_max_mm"].text()
    assert not be.limits.get().travel_min_enabled
    lt.max_spin.setValue(250.0)
    qtbot.mouseClick(lt.apply_button, Qt.MouseButton.LeftButton)
    cfg = be.limits.get()
    assert (cfg.travel_min_mm, cfg.travel_max_mm) == (10.0, 250.0) and "✓ applied" in lt.apply_result.text()
    win.tabs.setCurrentWidget(win.manual_tab)
    step(win, be)
    assert win.manual_tab.slider.range_mm() == (10.0, 250.0)


@pytest.mark.req("SW-TARE-001", "SW-CAL-001", "SW-STOP-001")
def test_m3_actions_follow_the_real_backend(lock_win, lock_be, qtbot) -> None:
    """Verifies: SW-TARE-001, SW-CAL-001 — TARE (popup) and both wizard starts show the real backend's result
    verbatim (refusal while B's engines are not delivered, a running engine afterwards); STOP in each window."""
    win, be = lock_win, lock_be
    win.on_tare(None)
    p = win.tare_popup
    if p.state_name == "REFUSED":                        # verbatim refusal (e.g. engine not delivered / gate)
        assert p.refusal and p.refusal in p.info_label.text()
    else:                                                # B's tare engine runs: progress, then a result
        assert p.state_name in be.tare_engine.PHASES
        assert run_until(win, be, lambda: p.state_name in ("DONE", "EVALUATE", "ABORTED", "REFUSED"), 30_000)
        assert p.state_name == be.tare_engine.state().phase
    for w in (win.open_travel_wizard(), win.open_load_wizard()):
        step(win, be)
        gate = gating.gate_of(be.status(), w.START_GATE)
        assert w.start_page.isVisible() or w.page.isVisible()
        if gate is not None and gate.refused:
            assert not w.start_button.isEnabled()
            assert gate.refused[0].text in w.checklist.text()
        n = len([c for c in be.test_hooks.wire_log() if c.direction == "TX" and c.frame[2] == int(Cmd.STOP)])
        qtbot.mousePress(w.stop_button, Qt.MouseButton.LeftButton)
        step(win, be)
        assert len(tx(be, Cmd.STOP)) == n + 1
        w.close()
    assert gating.gate_of(be.status(), GateId.NO_SPECIMEN) is not None
