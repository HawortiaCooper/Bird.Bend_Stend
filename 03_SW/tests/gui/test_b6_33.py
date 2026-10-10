"""B's SW review fixes, GUI part (SW_code_review v1.1 §7.2, SW_design v0.6.6 §15.5f B6-33):

* topic ``device.board_changed`` → acknowledgement C-15 (STOP in the dialog, Enter / Space never confirm, nothing
  is sent), toast;
* motion REFUSE ``PARAM_INVALID`` → red stop-banner row with [Connection tab];
* sequence start REFUSE ``PULL_DIR_MISMATCH`` → message row with both values + the pull-dir field marked red;
* ``limits.set`` issue ``LOAD_LIMITS_BOTH_OFF`` → shown with a pointer to the highlighted [Enter no-specimen mode…];
* new ``LOAD_INPUT_INVALID`` text ("no valid load input: …") on the first-use banner;
* ``session.load_issues`` (``LOAD_LIMITS_RESTORED``, a broken file kept as ``.bad``) at start and after File ▸ Open;
* hotkey UNAVAILABLE "hotkey thread not responding" → KEY chip red "NOT RESPONDING", error toast, app shortcut.

Verifies: SAF-SW-004, SAF-SW-005, SAF-SW-001, SW-LIM-001, SW-LIM-003, SW-SEQ-005, SW-STOP-002, SW-PLT-002
"""
from __future__ import annotations

import pytest
from fakes import FakeBackend, refuse, tick
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from bend_stand.core.api import GateId, GateItem, GateResult, HotkeyStatus, Issue, IssueSeverity, Severity
from bend_stand.gui.dialogs import safe_dialog
from bend_stand.gui.widgets.stop_button import StopButton


def _events(win) -> None:
    QApplication.processEvents()
    tick(win)


# --------------------------------------------------------------------------------------------- board changed
@pytest.mark.req("SAF-SW-005", "SAF-SW-004", "SW-LIM-001", "SW-PLT-002")
def test_board_changed_acknowledgement(window, connected_fake, qtbot) -> None:
    """Verifies: SAF-SW-005, SAF-SW-004, SW-LIM-001 — ``device.board_changed`` opens C-15 naming the UID; it has
    STOP, Enter does not acknowledge, Close / Acknowledge only close it (no backend call); a second event while it
    is open updates the same dialog."""
    connected_fake.events.emit("device.board_changed", "0039ABCD")
    _events(window)
    dlg = window.dialogs.get("board")
    assert dlg is not None and dlg.cid == "C-15" and dlg.isVisible()
    assert "0039ABCD" in dlg.text_label.text() and "test travel zero was reset" in dlg.text_label.text()
    assert dlg.findChildren(StopButton) and dlg.cancel_button.text() == "Close"
    assert dlg.confirm_button.text() == "Acknowledge"
    assert "Different board connected" in window.toast_label.text()
    n = len(connected_fake.calls)
    qtbot.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.outcome == "pending" and dlg.isVisible()
    connected_fake.events.emit("device.board_changed", "0039EEEE")
    _events(window)
    assert window.dialogs["board"] is dlg and "0039EEEE" in dlg.text_label.text()
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert dlg.outcome == "confirmed"
    sent = [c.name for c in connected_fake.calls[n:] if not c.name.endswith((".check", "status", ".values"))]
    assert sent == [], sent                       # an acknowledgement sends nothing (read-only queries of timers aside)


# --------------------------------------------------------------------------------------------- refusals
@pytest.mark.req("SAF-SW-005", "SW-LIM-001")
def test_param_invalid_banner_points_to_config(window, connected_fake, qtbot) -> None:
    """Verifies: SAF-SW-005 — a motion REFUSE PARAM_INVALID is a red banner row naming the parameters with
    [Connection tab]."""
    text = "board parameters outside the dictionary range: motion.v_max_load_um_s — read / write the configuration"
    connected_fake.set_gate(GateId.MOVE, refuse("PARAM_INVALID", text))
    window.tabs.setCurrentWidget(window.manual_tab)
    tick(window)
    assert window.stop_banner.top_text() == "Motion refused: " + text
    assert window.stop_banner.action_button.text() == "Connection tab"
    qtbot.mouseClick(window.stop_banner.action_button, Qt.MouseButton.LeftButton)
    assert window.tabs.currentIndex() == 0


@pytest.mark.req("SAF-SW-001")
def test_load_input_invalid_new_text_on_banner(window, connected_fake) -> None:
    """Verifies: SAF-SW-001 (D-53 a) — the new gate text is shown verbatim with its hint."""
    connected_fake.set_gate(GateId.MOVE, refuse("LOAD_INPUT_INVALID", "no valid load input: no load calibration",
                                                "Calibrate + Tare, or enter the no-specimen mode"))
    tick(window)
    assert window.stop_banner.top_text() == ("no valid load input: no load calibration – Calibrate + Tare, or enter "
                                             "the no-specimen mode")
    assert window.stop_banner.action_button.text() == "Enter no-specimen mode…"


@pytest.mark.req("SW-SEQ-005")
def test_pull_dir_mismatch_marks_the_field(window, connected_fake) -> None:
    """Verifies: SW-SEQ-005 (D-53 b) — PULL_DIR_MISMATCH: one message row with both values, the pull-dir field
    red with the text as tooltip; changing the pull dir clears the mark."""
    tab = window.sequence_tab
    window.tabs.setCurrentWidget(tab)
    tick(window)
    tab.table.clearSelection()
    assert tab.add_step("travel")
    tab.revalidate()
    text = ("sequence pull direction -1 differs from the session pull direction +1 (machine setting) — adjust the "
            "sequence")
    connected_fake.sequencer.check_result = GateResult((GateItem("PULL_DIR_MISMATCH", Severity.REFUSE, text),))
    tab.on_start()
    assert any("PULL_DIR_MISMATCH" in m and "-1" in m and "+1" in m for m in tab.message_texts())
    assert "#ffd0d0" in tab.pull_combo.styleSheet() and text in tab.pull_combo.toolTip()
    tab.pull_combo.activated.emit(0)
    assert tab.pull_combo.styleSheet() == "" and text not in tab.pull_combo.toolTip()


@pytest.mark.req("SW-LIM-001", "SW-LIM-004")
def test_both_load_limits_off_points_to_no_specimen(window, connected_fake, qtbot) -> None:
    """Verifies: SW-LIM-001 — LOAD_LIMITS_BOTH_OFF is shown verbatim with a pointer, the [Enter no-specimen mode…]
    button is highlighted; a later successful apply removes the highlight."""
    lt = window.limits_tab
    window.tabs.setCurrentWidget(lt)
    tick(window)
    connected_fake.limits.next_issues = [Issue("pull_enabled", IssueSeverity.ERROR, "LOAD_LIMITS_BOTH_OFF",
                                               "both PC load limits cannot be switched off — use the no-specimen mode")]
    lt.apply()
    assert "both PC load limits cannot be switched off" in lt.apply_result.text()
    assert "[Enter no-specimen mode…] below" in lt.apply_result.text()
    assert "#ffd060" in lt.nospec_enter.styleSheet()
    lt.apply()
    assert lt.apply_result.text().startswith("✓ applied") and lt.nospec_enter.styleSheet() == ""


# --------------------------------------------------------------------------------------------- session issues
@pytest.mark.req("SW-LIM-003")
def test_session_issues_shown_at_start_and_after_open(make_window, tmp_path) -> None:
    """Verifies: SW-LIM-003 — ``session.load_issues`` at start (LOAD_LIMITS_RESTORED, warn) and after
    File ▸ Open session (a FILE error, error) are shown as a toast and Event-log rows."""
    fake = FakeBackend()
    fake.start()
    fake.connect_async("sim")
    restored = Issue("limits", IssueSeverity.WARN, "LOAD_LIMITS_RESTORED",
                     "both PC load limits were off in the session file — both switched on again")
    fake.session.load_issues = [restored]
    win = make_window(fake)
    assert "Session at start: both PC load limits were off" in win.toast_label.text()
    assert any("LOAD_LIMITS_RESTORED" in r for r in win.event_log.texts())
    p = tmp_path / "s.bbsession.json"
    p.write_text("{}", encoding="utf-8")
    fake.session.load_issues = [Issue(None, IssueSeverity.ERROR, "FILE", "session file ignored (bad JSON); defaults "
                                                                          "in use; the file was kept as s.bad")]
    safe_dialog.FILE_DIALOG_HOOK[0] = lambda *a, **k: str(p)
    win.open_session()
    assert "kept as s.bad" in win.toast_label.text() and "Session loaded" not in win.toast_label.text()


# --------------------------------------------------------------------------------------------- hotkey
@pytest.mark.req("SW-STOP-002")
def test_hotkey_not_responding_on_key_chip(window, connected_fake) -> None:
    """Verifies: SW-STOP-002 — "hotkey thread not responding": KEY chip red "NOT RESPONDING", error toast on the
    ``hotkey.state`` edge, the app-level Pause / Ctrl+Break shortcuts are installed."""
    st = HotkeyStatus("UNAVAILABLE", "Pause/Break key unavailable: hotkey thread not responding")
    connected_fake.set_status(hotkey=st)
    connected_fake.events.emit("hotkey.state", st)
    _events(window)
    chip = window.indicator_bar.chip("KEY")
    assert chip.value.text() == "NOT RESPONDING" and chip.level == "alarm"
    from bend_stand.gui import indicator_map
    v = indicator_map.chip_by_key(indicator_map.evaluate_chips(connected_fake.status()))["KEY"]
    assert v.text == "NOT RESPONDING" and v.level == "alarm" and "not responding" in v.tooltip
    assert "Pause/Break key not responding" in window.toast_label.text()
    assert window.app_shortcuts()
    plain = HotkeyStatus("UNAVAILABLE", "disabled (BEND_STAND_HOTKEY=off)")
    assert indicator_map.chip_by_key(indicator_map.evaluate_chips(
        connected_fake.set_status(hotkey=plain)))["KEY"].text == "UNAVAILABLE"
