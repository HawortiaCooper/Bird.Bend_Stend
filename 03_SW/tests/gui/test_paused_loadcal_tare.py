"""Bring-up guide v0.2 §10.2: PAUSE before the load calibration (hands in the load path); the backend's start gates
allow the load wizard and tare while PAUSED. The GUI must not grey them out (it follows the gates only, P1).

Verifies: SW-CAL-005, SW-TARE-001, SW-STOP-004
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from bend_stand.core.api import GateId
from bend_stand.core.testing import isolated_settings  # noqa: E402  B-SAFE-01: never the real user folders


@pytest.mark.req("SW-CAL-005", "SW-TARE-001", "SW-STOP-004")
def test_load_wizard_and_tare_enabled_while_paused(make_window, tmp_path, qtbot) -> None:
    """Verifies: SW-CAL-005, SW-TARE-001, SW-STOP-004 on B's backend + simulator: with PAUSED latched the Calibration
    tab's [Start load calibration wizard…], the wizard's [Start], the toolbar TARE and the tab's TARE are enabled
    exactly when the backend gates allow them (they do while PAUSED)."""
    backend_mod = pytest.importorskip("bend_stand.core.backend")
    from test_sim_manual import run_until, step
    be = backend_mod.Backend(backend_mod.BackendSettings(**isolated_settings(clock="lockstep", test_hooks=True, hotkey="off",
                                                         sim_nvm_path=str(tmp_path / "nvm.json"),
                                                         recordings_root=str(tmp_path / "rec"))))
    be.start()
    try:
        be.test_hooks.result(be.connect_async("sim"), 5000)
        win = make_window(be)
        step(win, be, 200)
        r = be.pause("toolbar")
        assert getattr(r, "sent", True)
        assert run_until(win, be, lambda: be.status().indicators.paused.state == "ON", 3000)
        cal = win.calibration_tab
        win.tabs.setCurrentWidget(cal)
        step(win, be)
        gates = be.status().gates
        assert gates[GateId.CAL_LOAD_START].ok, gates[GateId.CAL_LOAD_START].text()     # backend: allowed
        assert gates[GateId.TARE].ok, gates[GateId.TARE].text()
        assert cal.load_wizard_button.isEnabled(), cal.load_wizard_button.toolTip()
        assert cal.tare_button.isEnabled() and win.tare_button.isEnabled(), win.tare_button.toolTip()
        qtbot.mouseClick(cal.load_wizard_button, Qt.MouseButton.LeftButton)
        w = win.wizards["load_cal"]
        assert run_until(win, be, lambda: w.start_button.isEnabled(), 2000), w.checklist.text()
        w.close()
    finally:
        be.shutdown()
