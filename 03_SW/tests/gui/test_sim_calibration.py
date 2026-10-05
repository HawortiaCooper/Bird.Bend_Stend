"""M3 wizards and tare through the GUI on B's real engines + in-process simulator, lock-step clock (G-26, G-27,
G-28 simulator variants; G-36 M3 part: no-specimen mode → enable → home → travel calibration → load calibration →
tare → forces in readouts / X-Y pane). D-06: no port. The load cell is driven with the simulator's test-only
``set_cell_load`` (B5-17).

Verifies: SW-CAL-001, SW-CAL-002, SW-CAL-004, SW-CAL-005, SW-CAL-007, SW-CAL-008, SW-TARE-001, SW-TARE-002,
SW-LIM-004, SW-RT-003, SW-RT-005, SYS-008
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from test_sim_manual import lock_be, lock_win, ready, run_until, step  # noqa: F401  (fixtures re-used)

from bend_stand.gui.wizards import phase_views as pv

INPUTS = {"d1_mm": 10.0, "dtot_mm": 60.0}


def drive(win, be, qtbot, w, inputs: dict[str, float], until: str = "DONE", max_steps: int = 4000,
          on_point=None) -> list[str]:
    """Run a wizard through the GUI: confirm dialogs (mouse), fill the generated inputs, click Continue (mouse)."""
    seen: list[str] = []
    for _ in range(max_steps):
        st = w.state
        ph = st.phase if st is not None else ""
        if not seen or seen[-1] != ph:
            seen.append(ph)
        if ph == until or ph in ("ABORTED", "CANCELLED"):
            return seen
        dlg = w.confirm_dialog
        if dlg is not None and getattr(dlg, "outcome", "") == "pending" and dlg.isVisible():
            if dlg.assertion_box is not None:
                dlg.assertion_box.setChecked(True)
            qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
            step(win, be)
            continue
        if ph == "AWAIT_OPERATOR" and on_point is not None:
            on_point(st)
        for key, spin in w._inputs.items():                       # noqa: SLF001 - generated editors
            if key in inputs and abs(spin.value() - inputs[key]) > 1e-9:
                spin.setValue(inputs[key])
        if st is not None and st.can_continue and not st.errors and w.continue_button.isVisible() \
                and w.continue_button.isEnabled() and ph not in pv.TERMINAL_PHASES:
            if st.continue_moves:
                assert w.continue_button.focusPolicy() == Qt.FocusPolicy.NoFocus
            qtbot.mouseClick(w.continue_button, Qt.MouseButton.LeftButton)
        step(win, be, 50.0)
    return seen


@pytest.mark.req("SW-CAL-001", "SW-CAL-002", "SW-CAL-004", "SW-LIM-004")
def test_travel_wizard_on_simulator(lock_win, lock_be, qtbot) -> None:
    """Verifies: SW-CAL-001/002/004, SW-LIM-004 — in the no-specimen mode the travel wizard runs through every
    phase via the GUI (moves on mouse-only Continue, D1 / D_tot entered in the generated fields) and the accepted
    steps/mm is on the board; cancelling a second run restores spm0."""
    win, be = lock_win, lock_be
    ready(win, be, qtbot)
    w = win.open_travel_wizard()
    assert run_until(win, be, lambda: w.start_button.isEnabled(), 3000), w.checklist.text()
    qtbot.mouseClick(w.start_button, Qt.MouseButton.LeftButton)
    seen = drive(win, be, qtbot, w, INPUTS)
    assert seen[-1] == "DONE", (seen, w.state)
    for ph in ("BACKLASH", "REFERENCE", "ENTER_D1", "ENTER_DTOT", "RESULT"):
        assert ph in seen, seen
    assert be.config.values()["motion.steps_per_mm"] == pytest.approx(800.0, rel=2e-3)
    assert be.calibrations.active_travel() is not None
    w.close()
    w = win.open_travel_wizard()
    assert run_until(win, be, lambda: w.start_button.isEnabled() or w.page.isVisible(), 3000)
    if not w.started:
        qtbot.mouseClick(w.start_button, Qt.MouseButton.LeftButton)
    seen = drive(win, be, qtbot, w, {"d1_mm": 10.5}, until="MOVE2")
    assert "ENTER_D1" in seen, (seen, w.start_msg.text(), w.checklist.text())
    qtbot.mouseClick(w.cancel_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: w.state.phase in ("ABORTED", "CANCELLED", "IDLE", "DONE"), 30_000)
    assert be.config.values()["motion.steps_per_mm"] == pytest.approx(800.0, rel=2e-3)   # spm0 restored
    w.close()


@pytest.mark.req("SW-CAL-005", "SW-CAL-007", "SW-CAL-008", "SW-TARE-001", "SW-TARE-002", "SW-RT-003", "SW-RT-005")
def test_load_wizard_tare_force_on_simulator(lock_win, lock_be, qtbot) -> None:
    """Verifies: SW-CAL-005/007/008, SW-TARE-001/002, SW-RT-003/005, SYS-008 — zero + 1 kg + 10 kg through the GUI
    (simulated weights), fit accepted (LOW_SPAN shown), toolbar TARE → popup DONE, then the force channels become
    available: readout F in N, the X-Y pane plots force."""
    win, be = lock_win, lock_be
    sim = be.sim
    if not hasattr(sim, "set_cell_load"):
        pytest.skip("simulator cell-load model not available (B5-17)")
    sim.set_cell_load(0.0)
    w = win.open_load_wizard()
    assert run_until(win, be, lambda: w.start_button.isEnabled(), 3000), w.checklist.text()
    w.capture.setValue(5.0)
    qtbot.mouseClick(w.start_button, Qt.MouseButton.LeftButton)
    masses = {1: 1.0, 2: 10.0}

    def on_point(st) -> None:
        sim.set_cell_load(mass_kg=masses.get(int(st.step_index or 0), 0.0))
    def on_point_gui(st) -> None:
        on_point(st)
        spin = w._inputs.get("mass_kg")                           # noqa: SLF001 - generated mass field
        if spin is not None:
            spin.setValue(masses.get(int(st.step_index or 0), 0.0))
    seen = drive(win, be, qtbot, w, {}, until="FIT", on_point=on_point_gui)
    assert seen[-1] == "FIT", (seen, w.state)
    assert "CAPTURE" in seen and "AWAIT_OPERATOR" in seen
    assert w.summary_label.text() and w.plot.isVisibleTo(w)
    assert "LOW_SPAN" in w.messages.text() or "low_span = yes" in w.summary_label.text()
    seen = drive(win, be, qtbot, w, {}, until="DONE")
    assert seen[-1] == "DONE", (seen, w.state)
    assert run_until(win, be, lambda: be.status().calibration.load_status is not None, 3000)
    w.close()
    sim.set_cell_load(0.0)
    step(win, be, 1500)
    win.on_tare(None)
    p = win.tare_popup
    assert run_until(win, be, lambda: p.state_name in ("DONE", "EVALUATE", "ABORTED", "REFUSED"), 30_000)
    assert p.state_name == "DONE", (p.state_name, p.info_label.text(), p.refusal)
    assert run_until(win, be, lambda: be.status().tare.state not in (None, "NONE"), 3000)
    sim.set_cell_load(mass_kg=10.0)
    step(win, be, 2000)
    win.readout_dock.refresh(be.data, {s.key for s in be.channels.channels()})
    assert win.readout_dock.state_of("F_N") in ("OK", "EXTRAPOLATED")
    assert float(win.readout_dock.value_text("F_N").replace(" ", "")) == pytest.approx(98.07, abs=2.0)
    pane = win.plot_dock.add_xy_pane()
    win._reload_channels()                                        # noqa: SLF001 - as on channels.changed
    assert pane.y_key == "F_N"
