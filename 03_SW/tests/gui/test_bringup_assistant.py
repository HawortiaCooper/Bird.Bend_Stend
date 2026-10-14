"""Bring-up assistant (Tools ▸ Bring-up assistant…; D-56 a, SRS SW-BRU-001…004) against the fake ``BringupAPI``
(``fake_bringup.py``, the contract proposed to B as GRQ-B-40) until B's engine is handed back.

Verifies: SW-BRU-001, SW-BRU-003, SW-BRU-004, SAF-SW-004, SW-STOP-001
"""
from __future__ import annotations

import pytest
from fakes import confirm, refuse, tick
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from bend_stand.core.api import GateId
from bend_stand.gui.widgets import bringup_dock as bd
from bend_stand.gui.widgets.stop_button import StopButton


@pytest.fixture
def dock(window):
    d = window.open_bringup()
    QApplication.processEvents()
    return d


def _stops(w) -> list[StopButton]:
    return [b for b in w.findChildren(StopButton) if b.isVisibleTo(w)]


def _started(dock, fake):
    fake.bringup.start()
    dock.refresh(force=True)


@pytest.mark.req("SW-BRU-001", "SW-STOP-001")
def test_menu_opens_dock_empty_state_has_stop(window, dock, connected_fake) -> None:
    """Verifies: SW-BRU-001, SW-STOP-001 — Tools ▸ Bring-up assistant… opens the dock; without a session it says how
    to start; STOP is in its title bar and in the view; the toolbar STOP and banners stay in the main window."""
    acts = [a.text() for m in window.menuBar().actions() if m.menu() for a in m.menu().actions()]
    assert "Bring-up assistant…" in acts
    assert dock.isVisible() and "No bring-up session" in dock.progress_label.text()
    assert dock.empty_label.isVisible() and not dock.package_button.isEnabled()
    assert dock.stop_button.isVisible() and dock.stop_big.isVisibleTo(dock)
    assert window.stop_button.isVisible()


@pytest.mark.req("SW-BRU-001")
def test_session_steps_sidebar_and_step_view(window, dock, connected_fake) -> None:
    """Verifies: SW-BRU-001 — after start: sections with progress, steps with state marks; the first step shows id +
    title, instruction, expected, the STOP-if-fail note, the automatic result (PASS styled green) and the gate note."""
    qtbot_click = dock.start_button.click
    qtbot_click()
    QApplication.processEvents()
    assert "0 / 8 steps done" in dock.progress_label.text() and "board 0039ABCD" in dock.progress_label.text()
    tops = [dock.tree.topLevelItem(i).text(0) for i in range(dock.tree.topLevelItemCount())]
    assert tops[0].startswith("6 PC software") and dock.tree.topLevelItem(0).text(1) == "0/2"
    assert dock.title_label.text() == "6.3  Device line"
    assert dock.instruction_label.text().startswith("Instruction: ") and "Expected: FW 0.8.6" in dock.expected_label.text()
    assert dock.stop_if_fail_label.isVisibleTo(dock) and "HIL S-00" in dock.hg_label.text()
    assert dock.auto_label.text().startswith("Automatic check: PASS") and "d8f3d8" in dock.auto_label.styleSheet()
    assert dock.state_label.text() == "State: not done"
    assert not dock.action_button.isVisibleTo(dock) and not dock.entries_box.isVisibleTo(dock)


@pytest.mark.req("SW-BRU-001")
def test_entries_limits_required_and_states(window, dock, connected_fake) -> None:
    """Verifies: SW-BRU-001 — generated entry form (confirm + number with unit and limits); a missing confirmation is
    shown and nothing is saved; a value outside the limits → FAIL; Pass / Fail buttons; the step does not change by
    itself (no auto-advance)."""
    _started(dock, connected_fake)
    dock.select("8.4")
    assert set(dock._editors) == {"free", "m1"}                                     # noqa: SLF001
    box = dock.entries_box
    assert any("limits 0.5 … 0.9 V" in w.text() for w in box.findChildren(type(dock.issue_label)))
    dock._editors["m1"].setValue(0.7)                                              # noqa: SLF001
    issues = dock.on_save()
    assert issues and "entry required" in dock.issue_label.text()
    assert connected_fake.bringup.step("8.4").state == "NOT_DONE"
    dock._editors["free"].setChecked(True)                                         # noqa: SLF001
    dock._editors["m1"].setValue(1.2)                                              # noqa: SLF001
    assert dock.on_save() == []
    assert connected_fake.bringup.step("8.4").state == "FAIL" and dock.state_label.text() == "State: FAIL"
    assert dock.current == "8.4"                                                     # no auto-advance
    dock.pass_button.click()
    assert connected_fake.bringup.step("8.4").state == "PASS" and dock.current == "8.4"
    dock.next_button.click()
    assert dock.current == "9.4"
    dock.prev_button.click()
    assert dock.current == "8.4"


@pytest.mark.req("SW-BRU-001", "SW-STOP-001")
def test_skip_needs_reason_dialog_with_stop(window, dock, connected_fake, qtbot) -> None:
    """Verifies: SW-BRU-001, SW-STOP-001 — [Skip…] asks for a reason in a dialog with STOP; [Skip step] is disabled
    until a reason is typed; the step becomes SKIPPED with the reason."""
    _started(dock, connected_fake)
    dock.select("6.9")
    dock.skip_button.click()
    dlg = dock.skip_dialog
    assert dlg.findChildren(StopButton) and not dlg.skip_button.isEnabled()
    qtbot.keyClick(dlg.reason_edit, Qt.Key.Key_Return)
    assert connected_fake.bringup.step("6.9").state == "NOT_DONE"
    dlg.reason_edit.setText("no weights at hand")
    dlg.skip_button.click()
    s = connected_fake.bringup.step("6.9")
    assert s.state == "SKIPPED" and s.skip_reason == "no weights at hand"
    assert "skipped — no weights at hand" in dock.state_label.text()


@pytest.mark.req("SW-BRU-004", "SAF-SW-004")
def test_motion_action_uses_normal_gate_and_confirmation(window, dock, connected_fake, qtbot) -> None:
    """Verifies: SW-BRU-004, SAF-SW-004 — the HOME step's action button follows the HOME gate (refusal text shown,
    disabled); enabled, a click runs the Manual tab's HOME path: the C-01 confirmation appears and nothing is sent
    before the operator confirms it (no bypass); the button is mouse-only."""
    _started(dock, connected_fake)
    dock.select("9.4")
    connected_fake.set_gate(GateId.HOME, refuse("NOT_ENABLED", "driver not enabled"))
    tick(window)
    assert dock.action_button.text() == "HOME ▶" and not dock.action_button.isEnabled()
    assert "Not possible now" in dock.action_note.text() and "driver not enabled" in dock.action_note.text()
    assert dock.action_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    connected_fake.set_gate(GateId.HOME, confirm("HOME_LOAD_CONFIRM", "load unknown — confirm homing"))
    tick(window)
    assert dock.action_button.isEnabled()
    n = len(connected_fake.calls_of("motion.home"))
    qtbot.mouseClick(dock.action_button, Qt.MouseButton.LeftButton)
    dlg = window.manual_tab.confirm_dialog
    assert dlg is not None and dlg.cid == "C-01"
    assert len(connected_fake.calls_of("motion.home")) == n                    # nothing before the confirmation
    assert dock.current == "9.4"


@pytest.mark.req("SW-BRU-004")
def test_show_tab_action_and_action_table(window, dock, connected_fake) -> None:
    """Verifies: SW-BRU-004 — jog steps only bring the Manual tab to the front (no duplicated motion control); every
    action id maps to a normal GUI handler."""
    _started(dock, connected_fake)
    dock.select("9.2")
    assert dock.action_button.text() == "Show Manual tab"
    dock.action_button.click()
    assert window.tabs.currentWidget() is window.manual_tab
    for aid, (label, _gid, _motion, handler) in bd.ACTIONS.items():
        assert label and callable(handler), aid
    assert bd.action_spec("show:nowhere") is None and bd.action_spec(None) is None


@pytest.mark.req("SW-BRU-001", "SAF-SW-004", "SW-STOP-001")
def test_new_session_over_existing_needs_confirmation(window, dock, connected_fake, qtbot) -> None:
    """Verifies: SW-BRU-001, SAF-SW-004 — starting a session while one exists asks first (dialog with STOP, Enter
    does not confirm); confirmed → start(confirmed=True)."""
    _started(dock, connected_fake)
    dock.start_button.click()
    dlg = dock.confirm_dialog
    assert dlg is not None and dlg.findChildren(StopButton)
    qtbot.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.outcome == "pending"
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("bringup.start")[-1].kwargs == {"confirmed": True}


@pytest.mark.req("SW-BRU-003", "SW-STOP-001")
def test_create_package_dialog_open_folder(window, dock, connected_fake, qtbot) -> None:
    """Verifies: SW-BRU-003, SW-STOP-001 — [Create bring-up package…] → the dialog names the zip, its size, the "send
    this file to …" text and opens the folder; it has STOP; a failure is reported."""
    _started(dock, connected_fake)
    opened: list[str] = []
    bd.OPEN_FOLDER_HOOK[0] = opened.append
    try:
        dock.package_button.click()
        qtbot.waitUntil(lambda: dock.package_dialog is not None, timeout=2000)
        dlg = dock.package_dialog
        assert dlg.path_label.text().endswith(".zip") and "product owner" in dlg.send_label.text()
        assert dlg.findChildren(StopButton)
        dlg.open_button.click()
        assert opened and opened[0].endswith("bringup")
        dlg.close()
        connected_fake.bringup.package_error = OSError("disk full")
        dock.package_button.click()
        qtbot.waitUntil(lambda: dock.package_future is None, timeout=2000)
        assert "Bring-up package not created: disk full" in window.toast_label.text()
    finally:
        bd.OPEN_FOLDER_HOOK[0] = None


@pytest.mark.req("SW-BRU-004", "SW-STOP-001")
def test_stop_reachable_from_every_assistant_view(window, dock, connected_fake, qtbot) -> None:
    """Verifies: SW-BRU-004, SW-STOP-001 — STOP is reachable from the docked and floating assistant, from every step
    view (each step selected), from the skip / start / package dialogs, and a press calls backend.stop."""
    _started(dock, connected_fake)
    for sid in connected_fake.bringup._order:                                       # noqa: SLF001
        dock.select(sid)
        assert dock.stop_big.isVisibleTo(dock) and dock.stop_button.isVisible(), sid
    dock.setFloating(True)
    QApplication.processEvents()
    assert dock.stop_button.isVisible()
    n = len(connected_fake.calls_of("stop"))
    qtbot.mousePress(dock.stop_big, Qt.MouseButton.LeftButton)
    qtbot.mouseRelease(dock.stop_big, Qt.MouseButton.LeftButton)
    assert len(connected_fake.calls_of("stop")) == n + 1 and connected_fake.calls_of("stop")[-1].args == ("bringup",)
    dock.setFloating(False)
    dock.skip_button.click()
    assert dock.skip_dialog.findChildren(StopButton)
    dock.skip_dialog.close()


@pytest.mark.req("SW-BRU-004", "SAF-SW-004", "SW-STOP-001")
def test_pulse_integrity_engine_action_confirm_and_cancel(window, dock, connected_fake, qtbot) -> None:
    """Verifies: SW-BRU-004, SAF-SW-004 — the pulse-integrity step (engine action) follows the MOVE gate; a click →
    run_action(step) → CONFIRM → the usual confirmation (assertion, Enter does not confirm, STOP) → run_action(step,
    confirmed=True); while it runs the state is shown, the button is disabled and [Cancel check] → cancel_action();
    a refusal is shown as is (nothing started)."""
    _started(dock, connected_fake)
    dock.select("10.2.3")
    assert bd.is_engine_action(connected_fake.bringup.step("10.2.3"))
    assert dock.action_button.text().startswith("Run pulse-integrity check") and dock.action_button.isEnabled()
    assert "round trips" in dock.action_note.text() and not dock.cancel_action_button.isVisibleTo(dock)
    qtbot.mouseClick(dock.action_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("bringup.run_action")[-1].kwargs == {"confirmed": False}
    dlg = dock.confirm_dialog
    assert dlg is not None and dlg.findChildren(StopButton) and "round trips" in dlg.text_label.text()
    assert dlg.assertion_box is not None and not dlg.confirm_button.isEnabled()
    qtbot.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.outcome == "pending"
    dlg.assertion_box.setChecked(True)
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("bringup.run_action")[-1].kwargs == {"confirmed": True}
    tick(window)
    assert "Check: RUNNING — baseline re-home 1/3" in dock.action_state_label.text()
    assert not dock.action_button.isEnabled() and dock.cancel_action_button.isVisibleTo(dock)
    dock.cancel_action_button.click()
    assert connected_fake.calls_of("bringup.cancel_action")
    assert "ABORTED" in dock.action_state_label.text() and not dock.cancel_action_button.isVisibleTo(dock)
    connected_fake.set_gate(GateId.MOVE, refuse("NOT_HOMED", "axis not homed"))
    tick(window)
    assert not dock.action_button.isEnabled() and "axis not homed" in dock.action_note.text()


@pytest.mark.req("SW-BRU-004")
def test_engine_refusal_shown_nothing_started(window, dock, connected_fake) -> None:
    """Verifies: SW-BRU-004 — a REFUSE from run_action (e.g. the engine's own gate check) is shown verbatim."""
    from bend_stand.core.api import GateItem, GateResult, Severity
    _started(dock, connected_fake)
    dock.select("10.2.3")
    connected_fake.bringup.run_refusal = GateResult((GateItem("SPEED_CAP", Severity.REFUSE,
                                                              "speed above the travel cap"),))
    dock.run_engine_action()
    assert "speed above the travel cap" in dock.issue_label.text() and dock.confirm_dialog is None


@pytest.mark.req("SW-BRU-002")
def test_fake_has_b_additions(connected_fake) -> None:
    """Verifies: SW-BRU-002 — the fake carries B's five additions (run_action, cancel_action, close_session, resume,
    sessions) so it conforms to ``api.BringupAPI``."""
    from bend_stand.core.api import BringupAPI
    bu = connected_fake.bringup
    assert isinstance(bu, BringupAPI)
    bu.start()
    assert bu.close_session("test").ok and not bu.status().active
    sid = bu.sessions()[-1]["session_id"]
    assert bu.resume(sid).ok and bu.status().active
    assert not bu.resume("nope").ok


@pytest.mark.req("SW-BRU-001", "SW-BRU-003")
def test_b_bru_08_required_when_origin_codes_and_stop_banner(window, dock, connected_fake) -> None:
    """Verifies: SW-BRU-001 (B-BRU-08) — a field with ``required_when`` says when it is required; origin cli-test is
    red "NOT operator evidence" (step view and sidebar); refusal codes SPEED_BELOW_QUALIFICATION / STOP_REPORTED /
    BAD_ORIGIN are shown with their code; a step whose result says "STOP the bring-up and report" gets a red banner
    and the header names it."""
    from fake_bringup import BuField
    from bend_stand.core.api import GateItem, GateResult, Severity
    bu = connected_fake.bringup
    _started(dock, connected_fake)
    bu.set_step("6.9", fields=(BuField("sign", "Raw on pull", "choice", choices=("increases", "decreases")),
                               BuField("note", "Why reversed", "text", required=False,
                                       required_when={"field": "sign", "op": "eq", "value": "decreases"})))
    dock.select("6.9")
    rw = dock.entries_box.findChild(type(dock.issue_label), "bu_note_required_when")
    assert rw is not None and rw.text() == "required only when “Raw on pull” is decreases"
    bu.set_step("6.3", origin="cli-test", state="PASS")
    dock.select("6.3")
    dock.refresh(force=True)
    assert "NOT operator evidence" in dock.origin_label.text() and "b00000" in dock.origin_label.styleSheet()
    top = dock.tree.topLevelItem(0)
    item = next(top.child(j) for j in range(top.childCount())
                if top.child(j).data(0, Qt.ItemDataRole.UserRole) == "6.3")
    assert item.foreground(0).color().name() == "#b00000"
    dock.select("10.2.3")
    bu.run_refusal = GateResult((GateItem("SPEED_BELOW_QUALIFICATION", Severity.REFUSE,
                                          "the qualification needs 30 mm/s; the PC/FW speed limit is 20 mm/s"),))
    dock.run_engine_action()
    assert "[SPEED_BELOW_QUALIFICATION]" in dock.issue_label.text() and not dock.stop_report_label.isVisibleTo(dock)
    bu.run_refusal = GateResult((GateItem("STOP_REPORTED", Severity.REFUSE, "stop condition — report"),))
    dock.run_engine_action()
    assert "[STOP_REPORTED]" in dock.issue_label.text() and dock.stop_report_label.isVisibleTo(dock)
    bu.set_step("10.2.3", state="FAIL", action_state="FAIL",
                action_text="FAIL — unexpected board restart (BOOT) during the check — STOP the bring-up and report")
    dock.refresh(force=True)
    assert dock.stop_report_label.isVisibleTo(dock) and "STOP the bring-up and report" in dock.stop_report_label.text()
    assert dock.progress_label.text().startswith("■ STOP the bring-up and report (step 10.2.3)")
    orig = bu.mark
    bu.mark = lambda *a, **k: GateResult((GateItem("BAD_ORIGIN", Severity.REFUSE, "unknown origin 'x'"),))
    try:
        assert not dock.on_mark("PASS")
        assert "[BAD_ORIGIN]" in dock.issue_label.text()
    finally:
        bu.mark = orig


@pytest.mark.req("SW-BRU-002", "SW-BRU-003", "SAF-SW-004")
def test_board_changed_banner_new_session_and_package(window, dock, connected_fake, qtbot) -> None:
    """Verifies: SW-BRU-002 (SWD-P3-04) — another board on an open session: red banner with the engine's text and
    [New session for this board] / [Create package]; results refused with [BOARD_CHANGED]; the new session needs the
    usual confirmation naming both boards (Enter does not confirm); then the banner is gone."""
    bu = connected_fake.bringup
    _started(dock, connected_fake)
    bu.connect_other_board("0123456789ABCDEF01234567")
    dock.refresh()
    assert dock.board_banner.isVisibleTo(dock) and "BOARD CHANGED" in dock.board_label.text()
    dock.select("6.9")
    dock._editors["sign"].setCurrentIndex(1)                                        # noqa: SLF001
    dock.on_save()
    assert dock.issue_label.text().startswith("[BOARD_CHANGED] BOARD CHANGED")
    assert not dock.on_mark("PASS") and "[BOARD_CHANGED]" in dock.issue_label.text()
    dock.select("10.2.3")
    dock.run_engine_action()
    assert "[BOARD_CHANGED]" in dock.issue_label.text()
    dock.board_package_button.click()
    assert connected_fake.calls_of("bringup.create_package_async")
    qtbot.waitUntil(lambda: dock.package_future is None, timeout=2000)
    dock.board_new_button.click()
    dlg = dock.confirm_dialog
    assert dlg is not None and "0123456789ABCDEF01234567" in dlg.text_label.text() and "0039ABCD" in dlg.text_label.text()
    assert dlg.findChildren(StopButton)
    qtbot.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.outcome == "pending"
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("bringup.start")[-1].kwargs == {"confirmed": True}
    dock.refresh()
    assert not dock.board_banner.isVisibleTo(dock) and "0123456789ABCDEF01234567" in dock.progress_label.text()
