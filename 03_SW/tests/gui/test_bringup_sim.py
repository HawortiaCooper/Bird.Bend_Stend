"""Bring-up assistant on B's real engine (``backend.bringup``, B's 28-step example file) + in-process simulator,
lock-step clock (D-06: no port): start a session, an automatic step passes by itself, a measure step with a value
outside its limits fails, a step is skipped with a reason, the package is created and its dialog shown (SW-BRU-003).

Verifies: SW-BRU-001, SW-BRU-002, SW-BRU-003, SW-BRU-004
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtCore import Qt

backend_mod = pytest.importorskip("bend_stand.core.backend")
pytest.importorskip("bend_stand.core.bringup")

from test_sim_manual import run_until, step  # noqa: E402
from bend_stand.core.testing import isolated_settings  # noqa: E402  B-SAFE-01: never the real user folders


@pytest.fixture
def bu_win(make_window, tmp_path, monkeypatch):
    monkeypatch.setenv("BEND_STAND_BRINGUP_DIR", str(tmp_path / "packages"))
    be = backend_mod.Backend(backend_mod.BackendSettings(**isolated_settings(
        clock="lockstep", test_hooks=True, hotkey="off", sim_nvm_path=str(tmp_path / "nvm.json"),
        recordings_root=str(tmp_path / "rec"))))
    be.start()
    be.test_hooks.result(be.connect_async("sim"), 5000)
    win = make_window(be)
    step(win, be, 100)
    yield win, be, tmp_path
    be.shutdown()


@pytest.mark.req("SW-BRU-001", "SW-BRU-002", "SW-BRU-003", "SW-BRU-004")
def test_walkthrough_on_real_engine(bu_win, qtbot) -> None:
    """Verifies: SW-BRU-001/002/003/004 — start (C: none open) → 6.5 link + rate PASS by itself while selected;
    8.8 measure M-1 = 1.0 V (limits 0.5…0.9) → FAIL; 9.4 skipped with a reason (dialog); package → dialog with the zip
    in the bring-up folder, [Open folder]; nothing moved (no MOVE / HOME / JOG sent by the assistant)."""
    win, be, tmp = bu_win
    dock = win.open_bringup()
    qtbot.mouseClick(dock.start_button, Qt.MouseButton.LeftButton)
    if dock.confirm_dialog is not None and getattr(dock.confirm_dialog, "outcome", "") == "pending":
        qtbot.mouseClick(dock.confirm_dialog.confirm_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: be.bringup.status().active, 3000), be.bringup.status()
    step(win, be)
    assert dock.tree.topLevelItemCount() >= 5 and "steps done" in dock.progress_label.text()
    # automatic step
    dock.select("6.5")                                   # link CONNECTED + 76…84 SPS: the simulator passes it
    assert run_until(win, be, lambda: be.bringup.step("6.5").state == "PASS", 5000), be.bringup.step("6.5")
    step(win, be)
    assert "PASS" in dock.auto_label.text() and dock.state_label.text().startswith("State: PASS")
    assert dock.current == "6.5"                                                    # no auto-advance
    # measure step out of limits
    dock.select("8.8")
    dock._editors["m1"].setValue(1.0)                                               # noqa: SLF001
    dock._editors["m5"].setValue(5.0)                                               # noqa: SLF001
    issues = dock.on_save()
    assert not [i for i in issues if str(i.severity) == "ERROR"], [i.text for i in issues]
    assert run_until(win, be, lambda: be.bringup.step("8.8").state == "FAIL", 2000), be.bringup.step("8.8")
    step(win, be)
    assert dock.state_label.text().startswith("State: FAIL")
    # skip with a reason
    dock.select("9.4")
    dock.skip_button.click()
    dlg = dock.skip_dialog
    dlg.reason_edit.setText("caliper not at hand")
    dlg.skip_button.click()
    assert run_until(win, be, lambda: be.bringup.step("9.4").state == "SKIPPED", 2000)
    assert be.bringup.step("9.4").skip_reason == "caliper not at hand"
    # package
    qtbot.mouseClick(dock.package_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: dock.package_dialog is not None, 60_000), win.toast_label.text()
    pdlg = dock.package_dialog
    path = pdlg.path_label.text()
    assert path.endswith(".zip") and os.path.isfile(path) and path.startswith(str(tmp / "packages"))
    assert pdlg.send_label.text()
    st = be.status()
    assert not st.motion.moving
