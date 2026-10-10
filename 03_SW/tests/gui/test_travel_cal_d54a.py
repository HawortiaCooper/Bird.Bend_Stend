"""D-54 a / SRS v0.6.7 SW-CAL-002: the travel calibration is independent of the load calibration. Without a valid load
input the wizard start asks "no specimen mounted" (C-12, SAF-SW-004 rules) and runs in a wizard-scoped no-specimen
state (``status().safety.no_specimen_scope``, topic ``safety.no_specimen_scope``): banner + NOSPEC chip + window tags
while it runs, no [Leave] button (the scope ends with the wizard); an un-homed axis is homed as the wizard's first step
(phase HOME, a move page).

Verifies: SW-CAL-002, SW-LIM-004, SAF-SW-004, SW-CAL-001
"""
from __future__ import annotations

import dataclasses

import pytest
from fakes import tick
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from bend_stand.core.api import GATE_OK, GateItem, GateResult, Severity
from bend_stand.gui import indicator_map
from bend_stand.gui.mode_state import no_specimen
from bend_stand.gui.wizards import phase_views as pv

from test_calibration_tare import travel  # noqa: F401  (fixture re-used)


def _scope(fake, scope):
    st = fake.status()
    fake.set_status(safety=dataclasses.replace(st.safety, no_specimen_scope=scope))
    fake.events.emit("safety.no_specimen_scope", scope)


@pytest.mark.req("SW-CAL-002", "SW-LIM-004")
def test_wizard_scope_banner_chip_tags(window, connected_fake) -> None:
    """Verifies: SW-CAL-002, SW-LIM-004 — scope on: banner "NO SPECIMEN – travel calibration …" without [Leave],
    NOSPEC chip "NO-SPECIMEN (wizard)", NO-SPECIMEN tags, toast; scope off: all gone, "PC load limits apply again"."""
    _scope(connected_fake, "TRAVEL_CAL")
    QApplication.processEvents()
    tick(window)
    mb = window.mode_banner
    assert mb.isVisible() and "NO SPECIMEN – travel calibration" in mb.label.text()
    assert "Board load limit active" in mb.label.text() and not mb.leave_button.isVisible()
    v = indicator_map.chip_by_key(indicator_map.evaluate_chips(connected_fake.status()))["NOSPEC"]
    assert v.visible and v.level == "warn" and v.text == "NO-SPECIMEN (wizard)"
    assert no_specimen().on
    assert "No specimen (travel cal)" in window.toast_label.text()
    _scope(connected_fake, None)
    QApplication.processEvents()
    tick(window)
    assert not mb.isVisible() and not no_specimen().on
    assert "PC load limits apply again" in window.toast_label.text()


@pytest.mark.req("SW-CAL-002", "SAF-SW-004", "SW-CAL-001")
def test_start_confirm_then_home_page(travel, window, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-002, SAF-SW-004 — the start gate's CONFIRM "no specimen mounted" opens C-12 with the
    assertion (Enter does not confirm); confirmed → start(confirmed=True); the HOME phase is a move page (progress,
    live line, Continue disabled) and is part of the step strip."""
    eng = connected_fake.travel_cal
    assert "HOME" in pv.strip_phases(eng.PHASES) and pv.VIEWS["travel_cal"]["HOME"] == "move"
    eng.start_result = GateResult((GateItem("NO_SPECIMEN_MOUNTED", Severity.CONFIRM,           # B6-35 code / text
                                            "load unknown: confirm that no specimen is mounted — the travel "
                                            "calibration moves 62 mm in the + direction with the PC load limits off "
                                            "for its own moves (the board load limit stays active)"),))
    travel.refresh()
    qtbot.mouseClick(travel.start_button, Qt.MouseButton.LeftButton)
    dlg = travel.confirm_dialog
    assert dlg is not None and dlg.cid == "C-12" and dlg.assertion_box is not None
    qtbot.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.outcome == "pending"
    dlg.assertion_box.setChecked(True)
    eng.start_result = GATE_OK
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    starts = connected_fake.calls_of("travel_cal.start")
    assert starts and starts[-1].kwargs.get("confirmed") is True
    eng.set_state(phase="HOME", step_index=1, title="Travel calibration", can_continue=False, continue_moves=True,
                  instruction="The axis is not homed: homing first (no specimen mounted), then the calibration "
                              "moves follow.", progress=0.3)
    travel.refresh()
    assert "[HOME]" in travel.strip.text()
    assert not travel.continue_button.isEnabled()
    assert "homing first" in travel.instruction.text()
    assert travel.progress.isVisibleTo(travel) and travel.live_label.isVisibleTo(travel)   # move page


@pytest.mark.req("SW-CAL-002", "SW-CAL-001", "SW-LIM-004")
def test_travel_wizard_without_load_calibration_on_simulator(make_window, tmp_path, qtbot) -> None:
    """Verifies: SW-CAL-002 (D-54 a) on B's real backend + simulator: no load calibration, no tare, axis not homed,
    no session no-specimen mode → the start gate is not refused; C-12 → HOME → the measurement phases → DONE; the
    wizard scope banner is shown while it runs and gone afterwards."""
    backend_mod = pytest.importorskip("bend_stand.core.backend")
    from test_sim_calibration import drive
    from test_sim_manual import run_until, step
    be = backend_mod.Backend(backend_mod.BackendSettings(
        clock="lockstep", test_hooks=True, hotkey="off", sim_nvm_path=str(tmp_path / "nvm.json"),
        recordings_root=str(tmp_path / "rec")))
    be.start()
    try:
        h = be.test_hooks
        h.result(be.connect_async("sim"), 5000)
        h.advance(50)
        h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}), 5000)
        win = make_window(be)
        step(win, be, 50)
        assert not be.status().safety.no_specimen_mode and not be.status().motion.homed
        g = be.motion.enable()
        assert g.ok, g
        assert run_until(win, be, lambda: be.status().motion.enabled, 3000)
        w = win.open_travel_wizard()
        assert run_until(win, be, lambda: w.start_button.isEnabled(), 3000), w.checklist.text()
        qtbot.mouseClick(w.start_button, Qt.MouseButton.LeftButton)
        dlg = w.confirm_dialog                                  # C-12 "no specimen mounted" (CONFIRM of the gate)
        assert dlg is not None and dlg.cid == "C-12" and dlg.assertion_box is not None
        dlg.assertion_box.setChecked(True)
        qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
        assert run_until(win, be, lambda: bool(be.status().safety.no_specimen_scope), 3000)
        step(win, be)
        assert win.mode_banner.isVisible() and "NO SPECIMEN – travel calibration" in win.mode_banner.label.text()
        assert run_until(win, be, lambda: w.state is not None and w.state.phase == "HOME", 3000), w.state
        seen = drive(win, be, qtbot, w, {"d1_mm": 10.0, "dtot_mm": 60.0}, until="DONE")
        assert seen[-1] == "DONE", (seen, w.state)
        assert "HOME" in seen, seen
        assert be.status().motion.homed
        assert run_until(win, be, lambda: not be.status().safety.no_specimen_scope, 3000)
        step(win, be)
        assert not win.mode_banner.isVisible()
        w.close()
    finally:
        be.shutdown()
