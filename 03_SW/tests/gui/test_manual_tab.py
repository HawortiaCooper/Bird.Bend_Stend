"""Manual tab on the FakeBackend (SW_design_GUI §3.4): slider (G-12), go-to / steps (G-13), hold-to-jog (G-14),
speed / accel caps (G-15), enable / HOME / test zero / VALID (G-16), C-01 / C-02 confirmations (G-05), PAUSED line
and Resume (G-09 manual part), ticket outcomes (B3-04), STOP on the tab (G-02).

Verifies: SW-MAN-001, SW-MAN-002, SW-MAN-003, SW-MAN-004, SW-MAN-005, SW-MAN-006, SAF-SW-004, SAF-SW-006,
SW-STOP-001, SW-STOP-004, SW-LIM-001, SW-CAL-008
"""
from __future__ import annotations

import pytest
from fakes import confirm, done, failed, ind, refuse, tick, warn
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider

from bend_stand.core.api import (
    GATE_OK, ConfirmationRequired, GateId, GateItem, GateRefused, MoveDone, MoveOutcome, Severity,
)
from bend_stand.gui.widgets.stop_button import StopButton


@pytest.fixture
def manual(window, connected_fake):
    window.tabs.setCurrentWidget(window.manual_tab)
    tick(window, 3)
    connected_fake.calls.clear()
    return window.manual_tab


def motion_calls(fake, name=None):
    return [c for c in fake.calls if c.name.startswith("motion.") and (name is None or c.name == name)]


def handle_center(slider) -> QPoint:
    opt = QStyleOptionSlider()
    slider.initStyleOption(opt)
    r = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, slider)
    return r.center()


# --------------------------------------------------------------------------------------------- G-12 slider

@pytest.mark.req("SW-MAN-001", "SW-LIM-001")
def test_slider_drag_sends_nothing_release_sends_one_move(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SW-MAN-001, SW-LIM-001 — range = SW travel range (limits), handle at the commanded target;
    dragging only previews; release → exactly one move_to(value)."""
    s = manual.slider
    assert s.range_mm() == (0.5, 290.0)
    assert abs(s.value_mm() - 100.0) < 1e-9 and s.focusPolicy() == Qt.FocusPolicy.NoFocus
    s.setSliderDown(True)
    for v in (120_000, 140_000, 150_000):
        s.setSliderPosition(v)
    assert "150.000" in manual.preview_label.text()
    assert motion_calls(connected_fake) == []
    s.setSliderDown(False)
    calls = motion_calls(connected_fake, "motion.move_to")
    assert len(calls) == 1 and calls[0].args == (150.0,)
    assert calls[0].kwargs["speed_mm_s"] == manual.speed()


@pytest.mark.req("SW-MAN-001")
def test_slider_mouse_handle_drag_and_groove_click(manual, connected_fake, qtbot) -> None:
    """Verifies: SW-MAN-001 (GQ-07) — a groove click, the wheel and keys move nothing; a mouse drag of the handle
    sends one move on release."""
    s = manual.slider
    s.resize(600, 30)
    v0 = s.value()
    far = QPoint(s.width() - 5 if handle_center(s).x() < s.width() // 2 else 5, s.height() // 2)
    qtbot.mouseClick(s, Qt.MouseButton.LeftButton, pos=far)
    qtbot.keyClick(s, Qt.Key.Key_Right)
    qtbot.keyClick(s, Qt.Key.Key_PageUp)
    assert s.value() == v0 and motion_calls(connected_fake) == []
    c = handle_center(s)
    qtbot.mousePress(s, Qt.MouseButton.LeftButton, pos=c)
    qtbot.mouseMove(s, c + QPoint(60, 0))
    assert motion_calls(connected_fake) == []
    qtbot.mouseRelease(s, Qt.MouseButton.LeftButton, pos=c + QPoint(60, 0))
    assert len(motion_calls(connected_fake, "motion.move_to")) == 1


@pytest.mark.req("SW-MAN-001")
def test_slider_disabled_by_move_gate(manual, window, connected_fake) -> None:
    """Verifies: SW-MAN-001 — the slider, Go and the step buttons follow the move gate (not homed, PAUSED …)."""
    connected_fake.set_gate(GateId.MOVE, refuse("NOT_HOMED", "axis not homed — HOME first"))
    tick(window)
    for w in (manual.slider, manual.go_button, *manual.step_buttons):
        assert not w.isEnabled()
    assert "not homed" in manual.slider.toolTip() and "not homed" in manual.gate_line.text()
    connected_fake.set_gate(GateId.MOVE, warn("PC_LOAD_LIMITS_OFF", "PC load limits off"))
    tick(window)
    assert manual.slider.isEnabled() and "PC load limits off" in manual.gate_line.text()


# --------------------------------------------------------------------------------------------- G-13

@pytest.mark.req("SW-MAN-002", "SW-MAN-003")
def test_goto_and_step_buttons(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SW-MAN-002, SW-MAN-003 — distance → move_by, absolute → move_to (the GUI does no target
    arithmetic); Enter in the field sends nothing; three +1 clicks = three move_by(1); pending target shown."""
    manual.goto_spin.setValue(5.0)
    manual.goto_spin.setFocus()
    qtbot.keyClick(manual.goto_spin, Qt.Key.Key_Return)
    assert motion_calls(connected_fake, "motion.move_by") == []
    qtbot.mouseClick(manual.go_button, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.move_by")[-1].args == (5.0,)
    manual.abs_radio.setChecked(True)
    manual.goto_spin.setValue(42.0)
    qtbot.mouseClick(manual.go_button, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.move_to")[-1].args == (42.0,)
    connected_fake.calls.clear()
    plus1 = next(b for b in manual.step_buttons if b.text() == "+1")
    for _ in range(3):
        qtbot.mouseClick(plus1, Qt.MouseButton.LeftButton)
    assert [c.args for c in motion_calls(connected_fake, "motion.move_by")] == [(1.0,)] * 3
    assert [b.text() for b in manual.step_buttons] == ["-10", "-1", "-0.1", "+0.1", "+1", "+10"]
    connected_fake.set_motion(commanded_target_mm=11.0, pending_target_mm=13.0)
    tick(window)
    assert manual.pos_labels["pending"].text() == "13.000 mm"
    assert manual.pos_labels["target"].text() == "11.000 mm"


@pytest.mark.req("SW-MAN-002", "SW-STOP-004")
def test_ticket_outcomes_shown(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SW-MAN-002, SW-STOP-004 (B3-04) — REFUSED_PAUSED is info (no error toast), REFUSED an error,
    a local GateRefused shows its text, MOVE_DONE TARGET the reached position."""
    plus1 = next(b for b in manual.step_buttons if b.text() == "+1")
    connected_fake.motion.tickets["move_by"] = done(MoveOutcome("REFUSED_PAUSED", None, "BLOCK PAUSED"))
    qtbot.mouseClick(plus1, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "PAUSED" in manual.last_line.text(), timeout=2000)
    assert "error" not in window.toast_label.styleSheet()
    connected_fake.motion.tickets["move_by"] = done(MoveOutcome("REFUSED", None, "E_RANGE offset 4"))
    qtbot.mouseClick(plus1, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "E_RANGE" in manual.last_line.text(), timeout=2000)
    gate = refuse("TARGET_OUT_OF_RANGE", "target 300 mm outside the travel range 0.5…290 mm")
    connected_fake.motion.tickets["move_by"] = failed(GateRefused(gate))
    qtbot.mouseClick(plus1, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "outside the travel range" in window.toast_label.text(), timeout=2000)
    connected_fake.motion.tickets["move_by"] = done(MoveOutcome("DONE", MoveDone("TARGET", 12.5, 10000, 5)))
    qtbot.mouseClick(plus1, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "MOVE_DONE TARGET at 12.500" in manual.last_line.text(), timeout=2000)
    connected_fake.events.emit("motion.done", MoveDone("STOPPED", 7.25, 1, 2, "PC_STOP"))
    qtbot.waitUntil(lambda: "STOPPED: PC_STOP" in manual.last_line.text(), timeout=2000)


# --------------------------------------------------------------------------------------------- G-14 jog

def _jog_stops(fake) -> int:
    return len(motion_calls(fake, "motion.jog_stop"))


@pytest.mark.req("SW-MAN-004")
def test_hold_to_jog_press_release(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SW-MAN-004 — press → jog_start(direction, jog speed); release → exactly one jog_stop; NoFocus."""
    b = manual.jog_fwd
    assert b.focusPolicy() == Qt.FocusPolicy.NoFocus and manual.jog_rear.focusPolicy() == Qt.FocusPolicy.NoFocus
    qtbot.mousePress(b, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.jog_start")[-1].args == (1, 2.0)
    qtbot.mouseRelease(b, Qt.MouseButton.LeftButton)
    assert _jog_stops(connected_fake) == 1
    qtbot.mousePress(manual.jog_rear, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.jog_start")[-1].args == (-1, 2.0)
    qtbot.mouseRelease(manual.jog_rear, Qt.MouseButton.LeftButton)
    assert _jog_stops(connected_fake) == 2


@pytest.mark.req("SW-MAN-004")
@pytest.mark.parametrize("how", ["app_inactive", "tab_change", "gate_closed", "backend_ended", "window_hidden"])
def test_jog_stops_once_on_focus_loss(manual, window, connected_fake, qtbot, how) -> None:
    """Verifies: SW-MAN-004 — while held: application inactive, tab change, jog gate closing, the backend ending
    the jog session (STOP / PAUSE) or the window hiding → exactly one jog_stop; the later mouse release sends no
    second one and a new press is needed."""
    b = manual.jog_fwd
    qtbot.mousePress(b, Qt.MouseButton.LeftButton)
    assert b.held
    if how == "app_inactive":
        QApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)
    elif how == "tab_change":
        window.tabs.setCurrentIndex(0)
    elif how == "gate_closed":
        connected_fake.set_gate(GateId.JOG, refuse("PAUSED", "PAUSED — Resume clears PAUSE"))
        tick(window)
    elif how == "backend_ended":
        connected_fake.set_motion(jogging=True)
        tick(window)
        connected_fake.set_motion(jogging=False)
        tick(window)
    else:
        window.hide()
    assert _jog_stops(connected_fake) == 1 and not b.held
    qtbot.mouseRelease(b, Qt.MouseButton.LeftButton)
    assert _jog_stops(connected_fake) == 1


@pytest.mark.req("SW-MAN-004")
def test_gui_beat_every_tick(manual, window, connected_fake) -> None:
    """Verifies: SW-MAN-004 — gui_beat() on every refresh tick (the backend's jog refresh depends on it)."""
    b0 = connected_fake.beats
    tick(window, 5)
    assert connected_fake.beats == b0 + 5


# --------------------------------------------------------------------------------------------- G-15 caps

@pytest.mark.req("SW-MAN-005", "SAF-SW-006")
def test_speed_accel_caps_and_margin_warning(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SW-MAN-005, SAF-SW-006 — caps label from MotionLimits; speed / accel above the caps → red field,
    not applied (no move sent); the SAF-SW-006 WARN item of motion.check is shown verbatim."""
    tick(window, 3)
    caps = manual.caps_label.text()
    assert "travel 30.000" in caps and "loaded 20.000" in caps and "step rate 50.000" in caps
    manual.speed_spin.setValue(50.0)
    assert "above the cap" in manual.param_msg.text() and "ffc8c8" in manual.speed_spin.styleSheet()
    qtbot.mouseClick(next(b for b in manual.step_buttons if b.text() == "+1"), Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.move_by") == []
    assert "not applied" in window.toast_label.text()
    manual.speed_spin.setValue(5.0)
    manual.accel_spin.setValue(500.0)
    assert "acceleration 500" in manual.param_msg.text()
    manual.accel_spin.setValue(0.0)                    # 0 = FW default (accel None)
    assert manual.accel() is None and manual.param_msg.text() == ""
    assert connected_fake.session.get().manual_speed_mm_s == 5.0
    connected_fake.motion.results["check_extra"] = (
        GateItem("SAF_SW_006_MARGIN", Severity.WARN, "speed too high for the load-limit margin"),)
    manual.speed_spin.setValue(6.0)
    assert "load-limit margin" in manual.param_msg.text()
    qtbot.mouseClick(next(b for b in manual.step_buttons if b.text() == "+1"), Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.move_by")[-1].kwargs["speed_mm_s"] == 6.0


@pytest.mark.req("SW-MAN-005")
def test_jog_speed_above_unhomed_cap_refused(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SW-MAN-005, SW-MAN-004 — un-homed jog cap (2 mm/s): a jog speed above it is red and the press sends
    no jog_start."""
    connected_fake.set_indicators(homed=ind("OFF"))
    manual.jog_spin.setValue(5.0)
    assert "ffc8c8" in manual.jog_spin.styleSheet()
    qtbot.mousePress(manual.jog_fwd, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.jog_start") == [] and not manual.jog_fwd.held
    qtbot.mouseRelease(manual.jog_fwd, Qt.MouseButton.LeftButton)


@pytest.mark.req("SW-MAN-005", "SW-STOP-001")
def test_stop_on_manual_tab(manual, connected_fake, qtbot) -> None:
    """Verifies: SW-MAN-005, SW-STOP-001 — a large STOP on the tab calls backend.stop("manual") on press."""
    stops = manual.findChildren(StopButton)
    assert len(stops) == 1 and stops[0].minimumHeight() >= 48
    qtbot.mousePress(stops[0], Qt.MouseButton.LeftButton)
    assert connected_fake.calls[-1].name == "stop" and connected_fake.calls[-1].args == ("manual",)


# --------------------------------------------------------------------------------------------- G-16 / G-05

@pytest.mark.req("SW-MAN-006")
def test_enable_checkbox_follows_fw(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SW-MAN-006 — the checkbox shows the FW ENABLED state only (P4); click → enable(); ENABLING
    countdown shown."""
    connected_fake.set_indicators(enabled=ind("OFF"))
    tick(window)
    assert not manual.enable_box.isChecked()
    qtbot.mouseClick(manual.enable_box, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.enable")
    assert not manual.enable_box.isChecked()                           # no optimistic change
    connected_fake.set_motion(enabling_left_ms=340)
    tick(window)
    assert "ENABLING 340 ms" in manual.enable_state.text()
    connected_fake.set_motion(enabling_left_ms=0)
    connected_fake.set_indicators(enabled=ind("ON"))
    tick(window)
    assert manual.enable_box.isChecked() and "ENABLED" in manual.enable_state.text()
    connected_fake.motion.results["enable"] = refuse("DRV_UNPOWERED", "driver unpowered")


@pytest.mark.req("SW-MAN-006", "SAF-SW-004")
def test_disable_needs_c02(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SAF-SW-004, SW-MAN-006 — DISABLE with the gate's CONFIRM item → C-02 (assertion "specimen is
    unloaded", keyboard never confirms) → disable(confirmed=True)."""
    connected_fake.set_indicators(enabled=ind("ON"))
    connected_fake.set_gate(GateId.DISABLE, confirm("DISABLE_CONFIRM", "Specimen unloaded? holding torque is lost"))
    tick(window)
    qtbot.mouseClick(manual.enable_box, Qt.MouseButton.LeftButton)
    dlg = manual.confirm_dialog
    assert dlg is not None and dlg.cid == "C-02" and "holding torque" in dlg.text_label.text()
    assert motion_calls(connected_fake, "motion.disable")[-1].kwargs == {"confirmed": False}
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Space):
        qtbot.keyClick(dlg, key)
    assert dlg.outcome == "pending" and not dlg.confirm_button.isEnabled()
    dlg.assertion_box.setChecked(True)
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.disable")[-1].kwargs == {"confirmed": True}


@pytest.mark.req("SW-MAN-006", "SAF-SW-004")
def test_home_confirm_c01(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SAF-SW-004, SW-MAN-006 — HOME without a CONFIRM item → home(load_confirmed=False); with the
    HOME_LOAD_CONFIRM item → C-01 → home(load_confirmed=True); a ConfirmationRequired from the backend also opens
    C-01."""
    qtbot.mouseClick(manual.home_button, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.home")[-1].kwargs == {"load_confirmed": False}
    gate = confirm("HOME_LOAD_CONFIRM", "load 132.4 N (6.8 % FS > 5 %) — homing under load")
    connected_fake.set_gate(GateId.HOME, gate)
    tick(window)
    connected_fake.calls.clear()
    qtbot.mouseClick(manual.home_button, Qt.MouseButton.LeftButton)
    dlg = manual.confirm_dialog
    assert dlg.cid == "C-01" and "6.8 % FS" in dlg.text_label.text() and motion_calls(connected_fake) == []
    dlg.assertion_box.setChecked(True)
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.home")[-1].kwargs == {"load_confirmed": True}
    connected_fake.set_gate(GateId.HOME, GATE_OK)
    tick(window)
    connected_fake.motion.tickets["home"] = failed(ConfirmationRequired(gate))
    qtbot.mouseClick(manual.home_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: manual.confirm_dialog is not dlg and manual.confirm_dialog.cid == "C-01", timeout=2000)
    manual.confirm_dialog.reject()


@pytest.mark.req("SW-MAN-006")
def test_test_zero_and_valid(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SW-MAN-006 — set / reset test zero, VALID 0/1 buttons follow the FW VALID bit (refusal shown)."""
    qtbot.mouseClick(manual.zero_button, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.set_test_zero") and "Test travel zero set" in window.toast_label.text()
    qtbot.mouseClick(manual.zero_reset, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.reset_test_zero")
    connected_fake.set_motion(x_zero_mm=100.0)
    tick(window)
    assert manual.zero_label.text() == "x0 = 100.000 mm"
    connected_fake.set_indicators(valid=ind("OFF"))
    tick(window)
    v0, v1 = manual.valid_buttons
    assert v0.isChecked() and not v1.isChecked()
    qtbot.mouseClick(v1, Qt.MouseButton.LeftButton)
    assert motion_calls(connected_fake, "motion.set_valid")[-1].args == (True,)
    assert not v1.isChecked()                                          # FW state only (P4)
    connected_fake.set_indicators(valid=ind("ON"))
    tick(window)
    assert v1.isChecked() and "FW: ● 1" in manual.valid_state.text()
    connected_fake.motion.results["set_valid"] = refuse("SEQUENCE_RUNNING", "VALID belongs to the sequence")
    qtbot.mouseClick(v0, Qt.MouseButton.LeftButton)
    assert "VALID belongs to the sequence" in window.toast_label.text()
    connected_fake.set_gate(GateId.TEST_ZERO, refuse("NOT_HOMED", "not homed"))
    tick(window)
    assert not manual.zero_button.isEnabled()


@pytest.mark.req("SW-STOP-004", "SW-MAN-006")
def test_paused_line_resume_without_motion(manual, window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-004 — while the move gate carries PAUSED: line + [Resume] → backend.resume("manual");
    no motion command follows (D-31)."""
    connected_fake.set_gate(GateId.MOVE, refuse("PAUSED", "PAUSED — motion blocked", "Resume clears PAUSE"))
    tick(window)
    assert manual.paused_row.isVisibleTo(manual) and "Resume clears PAUSE" in manual.paused_label.text()
    assert not manual.slider.isEnabled()
    qtbot.mouseClick(manual.resume_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls[-1].name == "resume" and connected_fake.calls[-1].args == ("manual",)
    assert motion_calls(connected_fake) == []


@pytest.mark.req("SW-MAN-006", "SW-CAL-008")
def test_position_force_readouts(manual, window, connected_fake) -> None:
    """Verifies: SW-MAN-006, SW-CAL-008 — test / machine travel, force with its state (EXTRAPOLATED), raw."""
    connected_fake.set_motion(position_mm=112.345, test_position_mm=12.345, motion_state="MOVE_ABS", owner="MANUAL")
    connected_fake.data.latest_values["F_N"] = (400.0, "EXTRAPOLATED")
    tick(window, 3)
    p = manual.pos_labels
    assert p["test"].text() == "12.345 mm" and p["machine"].text() == "112.345 mm"
    assert "400.00 N" in p["force"].text() and "EXTRAPOLATED" in p["force"].text()
    assert "123 456" in p["raw"].text() and "MOVE_ABS" in p["state"].text()
