"""G-02 (widgets and every M1 dialog / dock), G-03 (dispatcher): STOP everywhere, never focus, never default, fires
on press; NO-SPECIMEN tag in dialogs and docks; native dialogs off (SW_design_GUI §5.2, §5.4).

Verifies: SW-STOP-001, SW-RT-001, SW-LIM-004, SYS-010
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QMainWindow, QMessageBox, QPushButton

from bend_stand.gui import stop as stop_mod
from bend_stand.gui.dialogs import safe_dialog
from bend_stand.gui.mode_state import no_specimen
from bend_stand.gui.widgets.stop_button import STOP_OBJECT_NAME, StopButton


def _stops(w) -> list[QPushButton]:
    return [b for b in w.findChildren(QPushButton, STOP_OBJECT_NAME)]


def _assert_one_stop(w) -> StopButton:
    btns = [b for b in _stops(w) if b.isVisibleTo(w)]
    assert len(btns) == 1, f"{type(w).__name__}: {len(btns)} visible STOP buttons"
    b = btns[0]
    assert b.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert not b.isDefault() and not b.autoDefault()
    return b


@pytest.fixture
def stop_calls():
    calls: list[str] = []
    prev = stop_mod.set_stop_handler(calls.append)
    yield calls
    stop_mod.set_stop_handler(prev)


@pytest.mark.req("SW-STOP-001")
def test_stop_button_fires_on_press_not_release(qtbot, stop_calls) -> None:
    """Verifies: SW-STOP-001 — the press alone stops (GQ-20); no focus, never default."""
    b = StopButton(source="t")
    qtbot.addWidget(b)
    b.show()
    qtbot.mousePress(b, Qt.MouseButton.LeftButton)
    assert stop_calls == ["t"]                       # synchronous, before any event processing
    qtbot.mouseRelease(b, Qt.MouseButton.LeftButton)
    assert stop_calls == ["t"]
    assert b.focusPolicy() == Qt.FocusPolicy.NoFocus and not b.isDefault() and b.text() == "STOP"


@pytest.mark.req("SW-STOP-001")
def test_trigger_stop_never_raises(caplog) -> None:
    """Verifies: SW-STOP-001 — a failing handler is logged, the press never raises into Qt; history kept."""
    prev = stop_mod.set_stop_handler(lambda s: 1 / 0)
    try:
        stop_mod.trigger_stop("x")
        stop_mod.set_stop_handler(None)
        stop_mod.trigger_stop("y")
    finally:
        stop_mod.set_stop_handler(prev)
    assert [s for _t, s in stop_mod.stop_history()[-2:]] == ["x", "y"]
    assert "STOP handler failed" in caplog.text


@pytest.mark.req("SW-STOP-001")
def test_native_dialogs_disabled() -> None:
    """Verifies: SW-STOP-001 — AA_DontUseNativeDialogs is set (file dialogs carry STOP)."""
    assert QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    assert safe_dialog.native_dialogs_disabled()


def _dialogs(fake, parent):
    from bend_stand.gui.bridge import QtBridge
    from bend_stand.gui.dialogs.about import AboutDialog
    from bend_stand.gui.dialogs.clear_stop_dialog import ClearStopDialog
    from bend_stand.gui.dialogs.confirm_dialog import TEXTS, make_confirm
    from bend_stand.gui.dialogs.file_report import FileReportDialog
    from bend_stand.gui.dialogs.link_stats import LinkStatsDialog
    from bend_stand.gui.dialogs.status_help import StatusHelpDialog
    bridge = QtBridge(fake, parent)
    out = [safe_dialog.SafeDialog(parent, "plain"), AboutDialog(fake.status(), parent),
           ClearStopDialog(fake, bridge, parent), FileReportDialog(None, parent, error="x"),
           LinkStatsDialog(fake, parent), StatusHelpDialog(fake.status(), None, parent)]
    out += [make_confirm(parent, cid) for cid in TEXTS]
    return out


@pytest.mark.req("SW-STOP-001", "SW-LIM-004")
def test_every_m1_dialog_has_one_stop_and_nospec_tag(qtbot, fake, stop_calls) -> None:
    """Verifies: SW-STOP-001, SW-LIM-004 — exactly one STOP per dialog (top bar), press stops; the NO-SPECIMEN tag
    follows the mode."""
    host = QMainWindow()
    qtbot.addWidget(host)
    for dlg in _dialogs(fake, host):
        dlg.show()
        b = _assert_one_stop(dlg)
        qtbot.mousePress(b, Qt.MouseButton.LeftButton)
        assert stop_calls[-1].startswith("dialog:"), stop_calls
        assert not dlg.nospec_tag.isVisibleTo(dlg)
        no_specimen().set(True)
        assert dlg.nospec_tag.isVisibleTo(dlg)
        no_specimen().set(False)
        dlg.close()


@pytest.mark.req("SW-STOP-001")
def test_message_box_stop_gives_no_answer(qtbot, stop_calls) -> None:
    """Verifies: SW-STOP-001 — STOP in a message box stops and closes it with NoButton; never escape/default."""
    box = safe_dialog.SafeMessageBox(QMessageBox.Icon.Question, "q", "really?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    qtbot.addWidget(box)
    box.show()
    b = _assert_one_stop(box)
    assert box.escapeButton() is not b and box.defaultButton() is not b
    qtbot.mousePress(b, Qt.MouseButton.LeftButton)
    assert stop_calls == ["messagebox"]
    assert box.result_button() == QMessageBox.StandardButton.NoButton
    assert not box.isVisible()


@pytest.mark.req("SW-STOP-001")
def test_file_dialog_has_stop(qtbot, stop_calls) -> None:
    """Verifies: SW-STOP-001 — the non-native file dialog carries STOP."""
    dlg = safe_dialog.SafeFileDialog(None, "open")
    qtbot.addWidget(dlg)
    dlg.show()
    b = _assert_one_stop(dlg)
    qtbot.mousePress(b, Qt.MouseButton.LeftButton)
    assert stop_calls == ["filedialog"]


@pytest.mark.req("SW-STOP-001", "SW-RT-001", "SW-LIM-004")
def test_safe_dock_stop_docked_and_floating(qtbot, stop_calls) -> None:
    """Verifies: SW-RT-001, SW-STOP-001, SW-LIM-004 — dock title bar with STOP stays when floating; tag follows."""
    from bend_stand.gui.widgets.safe_dock import SafeDock
    host = QMainWindow()
    qtbot.addWidget(host)
    dock = SafeDock("Plot X", host)
    host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    host.show()
    for floating in (False, True, False):
        dock.setFloating(floating)
        b = _assert_one_stop(dock)
        qtbot.mousePress(b, Qt.MouseButton.LeftButton)
        assert stop_calls[-1] == "dock:Plot X"
    no_specimen().set(True)
    assert dock.title_bar.nospec_tag.isVisibleTo(dock)
    dock.title_bar.float_button.click()
    assert dock.isFloating() and dock.title_bar.float_button.text() == "Dock"
