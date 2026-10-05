"""Safety limits tab (G-19, G-37 no-specimen part, SAF-SW-002 thresholds) and Test marks tab (G-20) on the
FakeBackend (SW_design_GUI §3.2, §3.3).

Verifies: SW-LIM-001, SW-LIM-002, SW-LIM-003, SW-LIM-004, SAF-SW-001, SAF-SW-002, SAF-SW-004, SW-META-001,
SW-META-002, SYS-003
"""
from __future__ import annotations

import dataclasses

import pytest
from fakes import confirm, refuse, tick
from PySide6.QtCore import Qt

from bend_stand.core.api import (
    GateId, Issue, IssueSeverity, LimitConfig, RecordingStatus, TestMarks, ThresholdState,
)
from bend_stand.gui.dialogs import safe_dialog


@pytest.fixture
def limits(window, connected_fake):
    window.tabs.setCurrentWidget(window.limits_tab)
    tick(window, 2)
    return window.limits_tab


@pytest.fixture
def marks(window, connected_fake):
    window.tabs.setCurrentWidget(window.marks_tab)
    tick(window, 2)
    return window.marks_tab


# --------------------------------------------------------------------------------------------- G-19

@pytest.mark.req("SW-LIM-001", "SW-LIM-002")
def test_limits_apply_sends_config(limits, connected_fake, qtbot) -> None:
    """Verifies: SW-LIM-001, SW-LIM-002 — the fields show limits.get(); [Apply] → limits.set(LimitConfig) with the
    edited values (N, mm); empty issue list = applied."""
    assert limits.pull_spin.value() == pytest.approx(1961.33) and limits.fw_spin.value() == pytest.approx(2157.46)
    assert "100.0 % FS" in limits.pull_help.text() and "110.0 % FS" in limits.fw_help.text()
    limits.min_en.setChecked(True)
    limits.min_spin.setValue(10.0)
    limits.max_en.setChecked(True)
    limits.max_spin.setValue(250.0)
    limits.pull_spin.setValue(1500.0)
    limits.warn_spin.setValue(80)
    qtbot.mouseClick(limits.apply_button, Qt.MouseButton.LeftButton)
    cfg = connected_fake.calls_of("limits.set")[-1].args[0]
    assert (cfg.travel_min_enabled, cfg.travel_min_mm, cfg.travel_max_mm) == (True, 10.0, 250.0)
    assert cfg.pull_trip_n == pytest.approx(1500.0) and cfg.warn_pct == 80.0
    assert "✓ applied" in limits.apply_result.text()
    assert connected_fake.limits.get().pull_trip_n == pytest.approx(1500.0)


@pytest.mark.req("SW-LIM-001", "SW-LIM-002")
def test_limits_issues_shown_per_field(limits, connected_fake, qtbot) -> None:
    """Verifies: SW-LIM-001, SW-LIM-002 — refusals (outside soft limits, FW level > 110 % FS / < SW trip) appear
    next to their field; nothing applied; [Revert] re-reads the backend limits."""
    E = IssueSeverity.ERROR
    connected_fake.limits.next_issues = [
        Issue("travel_max_mm", E, "OUTSIDE_SOFT_LIMITS", "travel_max_mm = 400 mm outside the FW soft limits"),
        Issue("fw_level_n", E, "FW_LEVEL", "FW load-limit level above 110 % FS (SW-LIM-002)")]
    limits.fw_spin.setValue(2500.0)
    qtbot.mouseClick(limits.apply_button, Qt.MouseButton.LeftButton)
    assert "outside the FW soft limits" in limits.issue_labels["travel_max_mm"].text()
    assert "above 110 % FS" in limits.issue_labels["fw_level_n"].text()
    assert "ffc8c8" in limits.fw_spin.styleSheet() and "not applied" in limits.apply_result.text()
    assert connected_fake.limits.get() == LimitConfig()
    qtbot.mouseClick(limits.revert_button, Qt.MouseButton.LeftButton)
    assert limits.fw_spin.value() == pytest.approx(2157.46) and limits.issue_labels["fw_level_n"].text() == ""


@pytest.mark.req("SYS-003", "SW-LIM-002")
def test_limits_in_kgf(limits, window, connected_fake, qtbot) -> None:
    """Verifies: SYS-003 — View ▸ Units kgf re-expresses the load fields; Apply still sends N."""
    window.set_force_unit("kgf")
    assert limits.pull_spin.suffix() == " kgf" and limits.pull_spin.value() == pytest.approx(200.0, abs=1e-3)
    qtbot.mouseClick(limits.apply_button, Qt.MouseButton.LeftButton)
    cfg = connected_fake.calls_of("limits.set")[-1].args[0]
    assert cfg.pull_trip_n == pytest.approx(1961.33, abs=0.01)
    window.set_force_unit("N")
    assert limits.pull_spin.value() == pytest.approx(1961.33, abs=0.01)


@pytest.mark.req("SAF-SW-002", "SAF-SW-001")
def test_thresholds_display_resend_manual_default(limits, window, connected_fake, qtbot) -> None:
    """Verifies: SAF-SW-002, SAF-SW-001 — ThresholdState shown incl. clamped effective levels; [Re-send & verify]
    → recheck_async; manual raw / default thresholds (B4-04); the motion-disabled notice from the move gate."""
    thr = ThresholdState("VERIFIED", "cal7", "tare3", 2157.46, -6_897_259, 7_147_283, 125_012, True, 2101.7, -2157.5)
    connected_fake.set_status(safety=dataclasses.replace(connected_fake.status().safety, thresholds=thr))
    tick(window)
    assert "VERIFIED" in limits.thr_label.text() and "7\u2009147\u2009283" in limits.thr_label.text()
    assert "clamped inward" in limits.clamped_label.text() and "2\u2009101.70" in limits.clamped_label.text()
    qtbot.mouseClick(limits.recheck_button, Qt.MouseButton.LeftButton)
    assert connected_fake.call_names()[-1] == "limits.recheck_async"
    limits.raw_min.setValue(-100_000)
    limits.raw_max.setValue(200_000)
    limits.raw_zero.setValue(5)
    qtbot.mouseClick(limits.manual_thr_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("limits.set_manual_thresholds_async")[-1].args == (-100_000, 200_000, 5)
    tick(window)
    assert "manual-raw" in limits.thr_label.text()
    qtbot.mouseClick(limits.default_thr_button, Qt.MouseButton.LeftButton)
    tick(window)
    assert "DEFAULT_ONLY" in limits.thr_label.text()
    connected_fake.set_gate(GateId.MOVE, refuse("LOAD_INPUT_INVALID", "PC load limit enabled, no valid calibration / tare"))
    tick(window)
    assert "no valid calibration" in limits.motion_notice.text() and "SAF-SW-001" in limits.motion_notice.text()


# --------------------------------------------------------------------------------------------- G-37

@pytest.mark.req("SW-LIM-004", "SAF-SW-004")
def test_no_specimen_mode_c10(limits, window, connected_fake, qtbot) -> None:
    """Verifies: SW-LIM-004, SAF-SW-004 — [Enter no-specimen mode…] → C-10 (assertion, Enter / Space never
    confirm) → set_no_specimen_mode(True, confirmed=True); banner on every tab; [Leave] → (False)."""
    connected_fake.set_gate(GateId.NO_SPECIMEN, confirm("NO_SPECIMEN_MODE", "No specimen is mounted? PC load limits "
                                                                         "off for this session"))
    tick(window)
    qtbot.mouseClick(limits.nospec_enter, Qt.MouseButton.LeftButton)
    dlg = limits.confirm_dialog
    assert dlg.cid == "C-10" and "PC load limits off" in dlg.text_label.text()
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
        qtbot.keyClick(dlg, key)
    assert dlg.outcome == "pending" and not connected_fake.calls_of("limits.set_no_specimen_mode")
    dlg.assertion_box.setChecked(True)
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    c = connected_fake.calls_of("limits.set_no_specimen_mode")[-1]
    assert c.args == (True,) and c.kwargs == {"confirmed": True}
    tick(window)
    assert "ON" in limits.nospec_state.text() and limits.nospec_leave.isVisibleTo(limits)
    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        assert window.mode_banner.isVisible()
    window.tabs.setCurrentWidget(limits)
    qtbot.mouseClick(limits.nospec_leave, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("limits.set_no_specimen_mode")[-1].args == (False,)


@pytest.mark.req("SW-LIM-004")
def test_no_specimen_refused_and_first_use_banner(limits, window, connected_fake, qtbot) -> None:
    """Verifies: SW-LIM-004, SAF-SW-001 — a REFUSE of the no_specimen gate disables the button; the first-use stop
    banner (move gate LOAD_INPUT_INVALID) offers [Enter no-specimen mode…] → C-10."""
    connected_fake.set_gate(GateId.NO_SPECIMEN, refuse("MOTION_ACTIVE", "axis moving"))
    tick(window)
    assert not limits.nospec_enter.isEnabled() and "axis moving" in limits.nospec_enter.toolTip()
    connected_fake.set_gate(GateId.NO_SPECIMEN, confirm("NO_SPECIMEN_MODE", "No specimen is mounted?"))
    connected_fake.set_gate(GateId.MOVE, refuse("LOAD_INPUT_INVALID", "No load calibration / tare: enabled PC load "
                                                                      "limits block motion"))
    tick(window)
    assert "No load calibration" in window.stop_banner.top_text()
    assert window.stop_banner.action_button.text() == "Enter no-specimen mode…"
    qtbot.mouseClick(window.stop_banner.action_button, Qt.MouseButton.LeftButton)
    assert limits.confirm_dialog is not None and limits.confirm_dialog.cid == "C-10"
    limits.confirm_dialog.reject()


# --------------------------------------------------------------------------------------------- G-20 marks

@pytest.mark.req("SW-META-001")
def test_marks_fields_and_custom(marks, connected_fake, qtbot) -> None:
    """Verifies: SW-META-001 — fixed marks and custom key/value fields (add, rename, remove) reach marks.set;
    duplicate / empty keys are marked and not sent."""
    qtbot.keyClicks(marks.specimen, "Wing bracket, PA12")
    qtbot.keyClicks(marks.number, "17")
    marks.operator.setText("O. Bam")
    marks.notes.setPlainText("first run")
    qtbot.mouseClick(marks.add_button, Qt.MouseButton.LeftButton)
    marks.custom.item(0, 0).setText("batch")
    marks.custom.item(0, 1).setText("2026-09")
    assert marks.apply()
    m = connected_fake.marks.get()
    assert (m.specimen, m.number, m.operator, m.notes) == ("Wing bracket, PA12", "17", "O. Bam", "first run")
    assert m.custom == (("batch", "2026-09"),)
    qtbot.mouseClick(marks.add_button, Qt.MouseButton.LeftButton)
    marks.custom.item(1, 0).setText("batch")
    n = len(connected_fake.calls_of("marks.set"))
    assert not marks.apply() and "duplicate key 'batch'" in marks.custom_issue.text()
    assert len(connected_fake.calls_of("marks.set")) == n
    marks.custom.item(1, 0).setText("print orientation")              # rename (in place)
    assert marks.apply() and dict(connected_fake.marks.get().custom)["print orientation"] == ""
    marks.custom.setCurrentCell(0, 0)
    qtbot.mouseClick(marks.remove_button, Qt.MouseButton.LeftButton)
    assert marks.apply() and connected_fake.marks.get().custom == (("print orientation", ""),)


@pytest.mark.req("SW-META-001")
def test_marks_debounced_apply(marks, connected_fake, qtbot) -> None:
    """Verifies: SW-META-001 — typing applies the marks after the debounce (no button needed)."""
    qtbot.keyClicks(marks.operator, "Oleksandr")
    qtbot.waitUntil(lambda: connected_fake.marks.get().operator == "Oleksandr", timeout=3000)


@pytest.mark.req("SW-META-002")
def test_marks_presets_round_trip(marks, connected_fake, qtbot, tmp_path) -> None:
    """Verifies: SW-META-002 — Save as… (SafeFileDialog with STOP; hook in tests) → marks.save_preset(path); the
    preset list refreshes; Load fills the form and applies it."""
    path = str(tmp_path / "Bracket_A.bbmarks.json")
    safe_dialog.FILE_DIALOG_HOOK[0] = lambda kind, caption, flt: path
    marks.specimen.setText("Bracket A")
    marks.apply()
    qtbot.mouseClick(marks.save_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("marks.save_preset")[-1].args == (path,)
    assert marks.preset_combo.currentText() == "Bracket_A"
    marks.specimen.setText("other")
    marks.apply()
    qtbot.mouseClick(marks.load_button, Qt.MouseButton.LeftButton)
    assert marks.specimen.text() == "Bracket A" and connected_fake.marks.get().specimen == "Bracket A"


@pytest.mark.req("SW-META-002", "SW-LIM-003")
def test_marks_snapshot_and_recording_footer(marks, window, connected_fake) -> None:
    """Verifies: SW-META-002, SW-LIM-003 — read-only snapshot (board config, calibration, tare, limits, versions);
    while recording the footer explains MARK_EDIT rows."""
    tick(window, 15)
    snap = marks.snapshot_label.text()
    assert "Board config" in snap and "Limits" in snap and "pull 1\u2009961.33 N" in snap and "Versions" in snap
    connected_fake.set_status(recording=RecordingStatus("RECORDING", "C:/rec/x", 10))
    tick(window)
    assert "MARK_EDIT" in marks.footer.text() and marks.footer.isVisibleTo(marks)
    connected_fake.marks.set(TestMarks("S", "1"))
    marks.reload()
    assert marks.specimen.text() == "S"
