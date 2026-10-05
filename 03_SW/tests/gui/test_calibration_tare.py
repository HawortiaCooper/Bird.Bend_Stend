"""Calibration & Tare tab, travel / load wizards and the TARE popup on scripted fake engines (SW_design_GUI §3.5,
§6.1–§6.4): G-26 / G-27 (wizard frame and phases, C-05 / C-06 / C-08 / C-12, finish early, re-take, abort pages,
phase-view completeness incl. the real backend's PHASES), G-28 (tare popup), G-38 (travel restore actions on the
tab).

Verifies: SW-CAL-001, SW-CAL-002, SW-CAL-003, SW-CAL-004, SW-CAL-005, SW-CAL-006, SW-CAL-007, SW-CAL-008,
SW-CAL-009, SW-TARE-001, SW-TARE-002, SW-TARE-003, SAF-SW-004, SW-STOP-001, SW-LIM-004
"""
from __future__ import annotations

import dataclasses

import pytest
from fakes import confirm, refuse, tick
from PySide6.QtCore import Qt

from bend_stand.core.api import (
    GATE_OK, CalibrationStatus, ConfirmRequest, GateId, GateItem, GateResult, InputSpec, Severity, TareStatus,
    TravelDiffState,
)
from bend_stand.gui.widgets.stop_button import StopButton
from bend_stand.gui.wizards import phase_views as pv


def calls(fake, name):
    return fake.calls_of(name)


@pytest.fixture
def travel(window, connected_fake, qtbot):
    window.tabs.setCurrentWidget(window.calibration_tab)
    tick(window)
    qtbot.mouseClick(window.calibration_tab.travel_wizard_button, Qt.MouseButton.LeftButton)
    w = window.wizards["travel_cal"]
    qtbot.waitExposed(w)
    return w


@pytest.fixture
def load(window, connected_fake, qtbot):
    window.tabs.setCurrentWidget(window.calibration_tab)
    tick(window)
    qtbot.mouseClick(window.calibration_tab.load_wizard_button, Qt.MouseButton.LeftButton)
    w = window.wizards["load_cal"]
    qtbot.waitExposed(w)
    return w


# --------------------------------------------------------------------------------------------- completeness

@pytest.mark.req("SW-CAL-001")
def test_every_engine_phase_has_a_view(fake) -> None:
    """Verifies: SW-CAL-001 (G-26 / G-27) — every name of the engines' PHASES (fake and B's real Backend) has a
    wizard view; terminal phases are page states, not strip items."""
    engines = {"travel_cal": fake.travel_cal, "load_cal": fake.load_cal, "tare": fake.tare_engine}
    try:
        from bend_stand.core.backend import Backend, BackendSettings  # noqa: PLC0415
        be = Backend(BackendSettings(hotkey="off"))
    except ImportError:                                # backend not importable: fake only
        be = None
    real = {} if be is None else {"travel_cal": be.travel_cal, "load_cal": be.load_cal, "tare": be.tare_engine}
    for source in (engines, real):
        for kind, eng in source.items():
            assert pv.missing_views(kind, tuple(eng.PHASES)) == [], (kind, eng.PHASES)
    if be is not None:
        be.shutdown()
    assert "ABORTED" not in pv.strip_phases(("CHECK", "ABORTED", "RESTORING", "DONE"))


@pytest.mark.req("SW-CAL-007")
def test_result_parts_generic() -> None:
    """Verifies: SW-CAL-007 — the generic result display: scalars as summary, the first list of mappings as table;
    fit points for the mini plot."""
    res = {"K": 3.0435e-5, "B": -0.01, "status": "PASS", "points": [
        {"mass_kg": 0.0, "f_ref_n": 0.0, "raw_mean": 10.0}, {"mass_kg": 1.0, "f_ref_n": 9.80665, "raw_mean": 322_000.0}]}
    summary, headers, rows = pv.result_parts(res)
    assert ("status", "PASS") in summary and headers == ["mass_kg", "f_ref_n", "raw_mean"] and len(rows) == 2
    xs, ys, k, b = pv.fit_points(res)
    assert xs == [10.0, 322_000.0] and ys[1] == pytest.approx(9.80665) and k == pytest.approx(3.0435e-5)


# --------------------------------------------------------------------------------------------- travel wizard

@pytest.mark.req("SW-CAL-001", "SW-LIM-004")
def test_start_page_checklist_and_refusals(travel, window, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-001, SW-LIM-004 — the start page lists the cal_travel_start gate verbatim; REFUSE disables
    [Start]; LOAD_INPUT_INVALID offers [Enter no-specimen mode…] (→ C-10); an engine refusal is shown."""
    gate = GateResult((GateItem("NOT_HOMED", Severity.REFUSE, "axis not homed", "HOME on the Manual tab"),
                       GateItem("LOAD_INPUT_INVALID", Severity.REFUSE, "load input invalid (no calibration / tare)")))
    connected_fake.set_gate(GateId.CAL_TRAVEL_START, gate)
    connected_fake.set_gate(GateId.NO_SPECIMEN, confirm("NO_SPECIMEN_MODE", "No specimen is mounted?"))
    travel.refresh()
    assert travel.start_page.isVisibleTo(travel) and not travel.page.isVisibleTo(travel)
    txt = travel.checklist.text()
    assert "axis not homed" in txt and "HOME on the Manual tab" in txt and "load input invalid" in txt
    assert not travel.start_button.isEnabled() and travel.nospec_button.isVisibleTo(travel)
    assert "800.000 steps/mm" in travel.expected_label.text()
    qtbot.mouseClick(travel.nospec_button, Qt.MouseButton.LeftButton)
    assert window.limits_tab.confirm_dialog.cid == "C-10"
    window.limits_tab.confirm_dialog.reject()
    connected_fake.set_gate(GateId.CAL_TRAVEL_START, GATE_OK)
    travel.refresh()
    assert travel.start_button.isEnabled()
    qtbot.mouseClick(travel.start_button, Qt.MouseButton.LeftButton)       # fake engine: NOT_IMPLEMENTED
    assert "Start refused" in travel.start_msg.text() and "not implemented" in travel.start_msg.text()


@pytest.mark.req("SW-CAL-001", "SAF-SW-004")
def test_start_confirm_c12(travel, connected_fake, qtbot) -> None:
    """Verifies: SAF-SW-004 — engine start CONFIRM items → C-12 (assertion "No specimen is mounted") →
    start(confirmed=True)."""
    eng = connected_fake.travel_cal
    eng.start_result = confirm("NO_SPECIMEN_CONFIRM", "load unknown — no specimen mounted?")
    qtbot.mouseClick(travel.start_button, Qt.MouseButton.LeftButton)
    dlg = travel.confirm_dialog
    assert dlg.cid == "C-12" and "no specimen mounted" in dlg.text_label.text()
    assert dlg.assertion_box is not None and calls(connected_fake, "travel_cal.start")[-1].kwargs == {}
    qtbot.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.outcome == "pending"
    dlg.assertion_box.setChecked(True)
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "travel_cal.start")[-1].kwargs == {"confirmed": True}
    assert eng.state().phase == "CHECK" and travel.page.isVisibleTo(travel)


def _started(wizard, eng, qtbot):
    eng.start_result = GATE_OK
    qtbot.mouseClick(wizard.start_button, Qt.MouseButton.LeftButton)
    assert wizard.started


@pytest.mark.req("SW-CAL-001", "SW-CAL-002", "SW-CAL-003", "SW-CAL-004")
def test_travel_wizard_phases(travel, window, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-001…004 — the page renders EngineState: strip with the current phase, title / instruction,
    mouse-only Continue with continue_label when it moves, generated D1 input → continue_({"D1": v}),
    needs_confirmation → C-05 → continue_(…, confirmed=True), result summary, progress."""
    eng = connected_fake.travel_cal
    _started(travel, eng, qtbot)
    eng.set_state(phase="BACKLASH", title="Backlash", instruction="The axis moves +2 mm at 2 mm/s.",
                  can_continue=True, can_cancel=True, continue_label="Move +2 mm", continue_moves=True)
    travel.refresh()
    assert "<b>[BACKLASH]</b>" in travel.strip.text() and "ABORTED" not in travel.strip.text()
    assert travel.continue_button.text() == "Move +2 mm ▶"
    assert travel.continue_button.focusPolicy() == Qt.FocusPolicy.NoFocus and not travel.continue_button.isDefault()
    assert travel.live_label.isVisibleTo(travel)
    qtbot.mouseClick(travel.continue_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "travel_cal.continue_")[-1].args == (None,)
    eng.set_state(phase="ENTER_D1", title="Enter D1", continue_label="Apply D1", continue_moves=False,
                  inputs=(InputSpec("D1", "Measured distance D1", "mm", 0.001, 100.0, 10.0),), can_repeat=True)
    travel.refresh()
    spin = travel._inputs["D1"]                       # noqa: SLF001 - generated editor of the InputSpec
    assert spin.value() == 10.0 and spin.suffix() == " mm"
    spin.setValue(10.05)
    qtbot.mouseClick(travel.continue_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "travel_cal.continue_")[-1].args == ({"D1": 10.05},)
    qtbot.mouseClick(travel.repeat_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "travel_cal.repeat")
    eng.set_state(needs_confirmation=ConfirmRequest("SPM_CHANGE", "steps/mm 800 → 160 (−80 %): 800 = 4000 p/rev "
                                                                  "(D-27); 160 = 800 p/rev — DIP change not applied?"))
    travel.refresh()
    dlg = travel.confirm_dialog
    assert dlg.cid == "C-05" and "DIP change not applied" in dlg.text_label.text()
    dlg.assertion_box.setChecked(True)
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    last = calls(connected_fake, "travel_cal.continue_")[-1]
    assert last.args == ({"D1": 10.05},) and last.kwargs == {"confirmed": True}
    eng.set_state(needs_confirmation=None, phase="RESULT", inputs=(), result={"spm0": 800.0, "spm1": 796.02,
                                                                             "spm2": 795.093, "N1": 8000},
                  continue_label="Accept & save", warnings=("repeat: incremental value differs by 0.6 %",))
    travel.refresh()
    assert "spm2 = 795.093" in travel.summary_label.text() and "repeat" in travel.messages.text()
    eng.set_state(phase="RESTORING", progress=0.5, title="Restoring steps/mm to 800.000", can_continue=False)
    travel.refresh()
    assert travel.progress.isVisibleTo(travel) and travel.progress.value() == 500
    assert "RESTORING" in travel.strip.text() and not travel.restart_button.isVisibleTo(travel)


@pytest.mark.req("SW-CAL-001", "SW-STOP-001")
def test_wizard_abort_page_and_stop(travel, window, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-001, SW-STOP-001 — STOP in the wizard → backend.stop("wizard:travel_cal"); ABORTED shows
    the reason + [Close] / [Restart wizard] only; Restart → start()."""
    eng = connected_fake.travel_cal
    _started(travel, eng, qtbot)
    stops = [b for b in travel.findChildren(StopButton) if b.isVisibleTo(travel)]
    assert len(stops) == 1
    qtbot.mousePress(stops[0], Qt.MouseButton.LeftButton)
    assert connected_fake.calls[-1].name == "stop" and connected_fake.calls[-1].args == ("wizard:travel_cal",)
    eng.set_state(phase="ABORTED", abort_reason="STOP (wizard) — measurement invalid", can_continue=False)
    travel.refresh()
    assert "measurement invalid" in travel.abort_label.text() and "unchanged" in travel.abort_label.text()
    assert travel.close_button.isVisibleTo(travel) and travel.restart_button.isVisibleTo(travel)
    assert not travel.continue_button.isVisibleTo(travel) and not travel.cancel_button.isVisibleTo(travel)
    n = len(calls(connected_fake, "travel_cal.start"))
    qtbot.mouseClick(travel.restart_button, Qt.MouseButton.LeftButton)
    assert len(calls(connected_fake, "travel_cal.start")) == n + 1


@pytest.mark.req("SW-CAL-001", "SAF-SW-004")
def test_close_is_cancel_with_c08_past_first_phase(travel, window, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-001 — closing in the first phase cancels directly; past it (or Esc) C-08 is asked first;
    confirm → cancel() and the window closes."""
    eng = connected_fake.travel_cal
    _started(travel, eng, qtbot)
    eng.set_state(phase="MOVE1", can_cancel=True)
    travel.refresh()
    qtbot.keyClick(travel, Qt.Key.Key_Escape)
    dlg = travel.confirm_dialog
    assert dlg is not None and dlg.cid == "C-08" and travel.isVisible()
    assert not calls(connected_fake, "travel_cal.cancel")
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "travel_cal.cancel")
    qtbot.waitUntil(lambda: not window.wizards["travel_cal"].isVisible() if _alive(window.wizards["travel_cal"])
                    else True, timeout=2000)


@pytest.mark.req("SW-CAL-001")
def test_close_in_first_phase_cancels(window, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-001 — closing in the first phase cancels the engine without C-08."""
    w = window.open_travel_wizard()
    eng = connected_fake.travel_cal
    eng.start_result = GATE_OK
    w.start()
    w.close()
    assert calls(connected_fake, "travel_cal.cancel") and w.confirm_dialog is None


def _alive(w) -> bool:
    try:
        w.isVisible()
        return True
    except RuntimeError:
        return False


# --------------------------------------------------------------------------------------------- load wizard

@pytest.mark.req("SW-CAL-005", "SW-CAL-008")
def test_load_wizard_start_config(load, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-005, SW-CAL-008 — points / pre-settle / capture (session defaults) go to start() (B5-08,
    masses are entered per point); the LOW_SPAN info for 1 kg + 10 kg is shown."""
    assert "LOW_SPAN" in load.span_info.text() and "5 % FS" in load.span_info.text()
    assert load.presettle.value() == 2.0 and load.capture.value() == 10.0 and load.points.value() == 3
    load.capture.setValue(12.0)
    _started(load, connected_fake.load_cal, qtbot)
    assert calls(connected_fake, "load_cal.start")[-1].kwargs == {"n_points": 3, "presettle_s": 2.0,
                                                                  "capture_s": 12.0}


@pytest.mark.req("SW-CAL-005", "SW-CAL-006", "SW-CAL-007")
def test_load_wizard_capture_evaluate_fit(load, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-005…007 — capture progress + live stats; a rejected point offers only Repeat; FIT shows the
    points table, summary and plot; WARN → C-06 → continue_(confirmed=True); FAIL disables Accept; [Finish with 2
    points] → finish_early(); [Re-take point] → retake(i)."""
    eng = connected_fake.load_cal
    _started(load, eng, qtbot)
    eng.set_state(phase="AWAIT_OPERATOR", step_index=2, title="Point 2", can_continue=True,
                  continue_label="Capture point 2", continue_moves=False,
                  inputs=(InputSpec("mass_kg", "mass m2", "kg", 0.001, 1000.0, 10.0),))
    load.refresh()
    assert load.finish_button.isVisibleTo(load)
    qtbot.mouseClick(load.finish_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "load_cal.finish_early")
    qtbot.mouseClick(load.continue_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "load_cal.continue_")[-1].args == ({"mass_kg": 10.0},)
    eng.set_state(phase="CAPTURE", inputs=(), progress=0.43, stats={"N": 344, "mean": 32251.4, "std": 45.8},
                  can_continue=False)
    load.refresh()
    assert load.progress.value() == 430 and load.stats_table.rowCount() == 3
    assert not load.finish_button.isVisibleTo(load)
    eng.set_state(phase="EVALUATE", progress=None, errors=("REJECTED: drift 160 > max(2·std, 20)",),
                  can_continue=False, can_repeat=True)
    load.refresh()
    assert "drift 160" in load.messages.text() and load.repeat_button.isEnabled()
    assert not load.continue_button.isEnabled()
    pts = [{"mass_kg": m, "f_ref_n": m * 9.80665, "raw_mean": m * 32212.0 + 5.0} for m in (0.0, 1.0, 10.0)]
    eng.set_state(phase="FIT", errors=(), result={"K": 3.0e-5, "B": -0.01, "NL_span_pct": 0.03, "status": "WARN",
                                                  "points": pts},
                  warnings=("LOW_SPAN",), can_continue=True, can_repeat=False, continue_label="Accept & save",
                  needs_confirmation=ConfirmRequest("WARN_FIT", "NL_span 0.3 % (WARN): accept?"))
    load.refresh()
    assert load.result_table.rowCount() == 3 and load.plot.isVisibleTo(load) and "status = WARN" in \
        load.summary_label.text()
    dlg = load.confirm_dialog
    assert dlg.cid == "C-06" and "NL_span" in dlg.text_label.text()
    dlg.assertion_box.setChecked(True)
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "load_cal.continue_")[-1].kwargs == {"confirmed": True}
    assert load.retake_button.isVisibleTo(load)
    actions = load.retake_button.menu().actions()
    assert [a.text() for a in actions] == ["zero point", "point 1", "point 2"]
    actions[1].trigger()
    assert calls(connected_fake, "load_cal.retake")[-1].args == (1,)
    eng.set_state(needs_confirmation=None, can_continue=False, errors=("points are not linear — check fixture",))
    load.refresh()
    assert not load.continue_button.isEnabled() and "not linear" in load.messages.text()


# --------------------------------------------------------------------------------------------- tare (G-28)

@pytest.mark.req("SW-TARE-001", "SW-TARE-003")
def test_tare_popup_refusal_verbatim_every_tab(window, connected_fake, qtbot) -> None:
    """Verifies: SW-TARE-001, SW-TARE-003 — toolbar TARE on every tab opens the non-modal popup; REFUSE texts are
    shown verbatim with [Repeat] / [Close]; STOP inside the popup."""
    connected_fake.results["tare"] = refuse("MOTION_ACTIVE", "moving or < 1 s after a move")
    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        qtbot.mouseClick(window.tare_button, Qt.MouseButton.LeftButton)
        p = window.tare_popup
        assert p.isVisible() and not p.isModal() and p.state_name == "REFUSED"
        assert "moving or < 1 s after a move" in p.info_label.text()
    assert p.repeat_button.isVisibleTo(p) and p.close_button.isVisibleTo(p)
    stops = [b for b in p.findChildren(StopButton) if b.isVisibleTo(p)]
    assert len(stops) == 1
    qtbot.mousePress(stops[0], Qt.MouseButton.LeftButton)
    assert connected_fake.calls[-1].args == ("tare",)


@pytest.mark.req("SW-TARE-001", "SW-TARE-002", "SW-TARE-003")
def test_tare_popup_progress_done_undo(window, connected_fake, qtbot) -> None:
    """Verifies: SW-TARE-001…003 — progress + stats while capturing ([Cancel] → cancel()); EVALUATE refusal →
    [Repeat]; DONE with the large-offset warning → [Keep] / [Undo tare] → tare_engine.undo(); DONE without
    warnings auto-closes."""
    connected_fake.results["tare"] = GATE_OK
    eng = connected_fake.tare_engine
    qtbot.mouseClick(window.tare_button, Qt.MouseButton.LeftButton)
    p = window.tare_popup
    eng.set_state(phase="CAPTURE", progress=0.62, stats={"mean raw": 125008, "std": 44, "N": 496})
    tick(window)
    assert p.progress.value() == 620 and "496" in p.stats_label.text() and p.cancel_button.isVisibleTo(p)
    qtbot.mouseClick(p.cancel_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "tare.cancel")
    eng.set_state(phase="EVALUATE", progress=None, errors=("lost frames 2.1 % > 1 %",))
    tick(window)
    assert "lost frames" in p.info_label.text() and p.repeat_button.isVisibleTo(p)
    connected_fake.set_status(tare=TareStatus("VALID", 125012.0, 1.0, "t1", True))
    eng.set_state(phase="DONE", errors=(), warnings=("large offset: specimen loaded?",))
    tick(window)
    assert p.undo_button.isVisibleTo(p) and p.undo_button.isEnabled() and p.keep_button.isVisibleTo(p)
    qtbot.mouseClick(p.undo_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "tare.undo") and "Tare undone" in window.toast_label.text()
    qtbot.mouseClick(window.tare_button, Qt.MouseButton.LeftButton)
    p = window.tare_popup
    eng.set_state(phase="DONE", warnings=())
    tick(window)
    assert p._auto.isActive()                       # noqa: SLF001 - auto-close after 5 s without warnings


@pytest.mark.req("SW-TARE-001", "SW-TARE-002")
def test_calibration_tab_tare_and_undo(window, connected_fake, qtbot) -> None:
    """Verifies: SW-TARE-001, SW-TARE-002 — the tab's TARE uses its window (2–60 s); tare info; Undo tare enabled
    from tare.can_undo."""
    tab = window.calibration_tab
    window.tabs.setCurrentWidget(tab)
    connected_fake.results["tare"] = GATE_OK
    tab.window_spin.setValue(20.0)
    qtbot.mouseClick(tab.tare_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "tare")[-1].args == (20.0,)
    connected_fake.set_status(tare=TareStatus("VALID", 125012.0, 180.0, "t1", True))
    tick(window)
    assert "125 012" in tab.tare_label.text() and "3 min" in tab.tare_label.text() and tab.undo_button.isEnabled()
    qtbot.mouseClick(tab.undo_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "tare.undo")


@pytest.mark.req("SW-CAL-001", "SW-CAL-008", "SW-CAL-009")
def test_calibration_panels_and_travel_actions(window, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-001 (G-38 tab part), SW-CAL-008, SW-CAL-009 — LOW_SPAN and "invalid for load limits" shown;
    board vs active steps/mm with one button per travel_diff action → resolve_travel_difference_async(action);
    History lists the previous files."""
    tab = window.calibration_tab
    window.tabs.setCurrentWidget(tab)
    diff = TravelDiffState(True, 800.0, 796.02, False, ("restore", "keep_board", "ignore_session"))
    connected_fake.set_status(calibration=CalibrationStatus(
        load_k=4.5666e-4, load_status="PASS", low_span=True, load_valid_for_limits=False, travel_spm=800.0,
        board_spm=796.02, travel_cal_differs=True, travel_diff=diff))
    connected_fake.calibrations.load_record = {"file": "active_load.json", "status": "PASS", "push_calibrated": False}
    connected_fake.calibrations.files["load"] = ["load_x_20261003T120000Z.json"]
    tick(window, 15)
    assert "LOW_SPAN" in tab.load_warn.text() and "invalid for load limits" in tab.load_warn.text()
    assert "active_load.json" in tab.load_label.text() and "push_calibrated: no" in tab.load_label.text()
    assert "796.020" in tab.travel_diff.text() and "800.000" in tab.travel_diff.text()
    assert all(b.isVisibleTo(tab) for b in tab.action_buttons.values())
    qtbot.mouseClick(tab.action_buttons["keep_board"], Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "calibrations.resolve_travel_difference_async")[-1].args == ("keep_board",)
    box = tab.show_history("load")
    assert "load_x_20261003T120000Z.json" in box.text() and box.findChildren(StopButton)
    box.close()
    connected_fake.set_status(calibration=dataclasses.replace(connected_fake.status().calibration,
                                                              travel_cal_differs=False, travel_diff=TravelDiffState()))
    tick(window)
    assert not any(b.isVisibleTo(tab) for b in tab.action_buttons.values())


@pytest.mark.req("SW-CAL-007")
def test_load_fit_points_and_line(load, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-007 — a real ``LoadCalResult`` (B5-20): summary (K, status, …), the per-point table from
    ``point_table()`` and the mini plot with the points and the fitted line; re-take menu from ``step_count``;
    without points the residuals are plotted."""
    from bend_stand.core.api import LoadCalResult
    pts = ((0.0, 0.0, 10.0, 0.01), (1.0, 9.80665, 322_220.0, -0.02), (10.0, 98.0665, 3_221_000.0, 0.01))
    res = LoadCalResult(3.0445e-5, -0.0003, (0.01, -0.02, 0.01), 0.9999999, 0.03, 0.0015, "PASS", 98.07, True,
                        None, (), pts)
    eng = connected_fake.load_cal
    _started(load, eng, qtbot)
    eng.set_state(phase="FIT", step_count=3, step_index=3, can_continue=True, continue_label="Accept calibration ▶",
                  result=res)
    load.refresh()
    assert "status = PASS" in load.summary_label.text() and "points" not in load.summary_label.text()
    assert load.result_table.rowCount() == 3 and load.result_table.horizontalHeaderItem(1).text() == "force_n"
    assert load.plot.isVisibleTo(load) and load.plot.getPlotItem().getAxis("left").labelText == "F [N]"
    xs, ys = load.fit_scatter.getData()
    assert list(xs) == [10.0, 322_220.0, 3_221_000.0] and ys[2] == pytest.approx(98.0665)
    lx, ly = load.fit_line.getData()
    assert len(lx) == 2 and ly[1] == pytest.approx(3.0445e-5 * 3_221_000.0 - 0.0003)
    assert len(load.retake_button.menu().actions()) == 3
    eng.set_state(result={"K": 3.0e-5, "B": -0.01, "residuals": (0.01, -0.02, 0.01), "status": "PASS"})
    load.refresh()
    assert load.plot.getPlotItem().getAxis("left").labelText == "residual [N]"


@pytest.mark.req("SAF-SW-004", "SW-CAL-001")
def test_c12_declined_starts_nothing(travel, connected_fake, qtbot) -> None:
    """Verifies: SAF-SW-004 (B5-18) — a start gate with CONFIRM items starts nothing; declining C-12 sends no
    second start (and no cancel), the start page stays; confirming repeats the start with confirmed=True."""
    eng = connected_fake.travel_cal
    eng.start_result = confirm("NO_SPECIMEN_MOUNTED", "load unknown — no specimen mounted?")
    qtbot.mouseClick(travel.start_button, Qt.MouseButton.LeftButton)
    dlg = travel.confirm_dialog
    assert dlg.cid == "C-12" and eng.state().phase == "IDLE"
    dlg.reject()
    assert len(calls(connected_fake, "travel_cal.start")) == 1 and not calls(connected_fake, "travel_cal.cancel")
    travel.refresh()
    assert travel.start_page.isVisibleTo(travel) and not travel.started
