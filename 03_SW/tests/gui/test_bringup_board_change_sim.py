"""SWD-P3-04 on B's engine + simulator: a session of board A, the application restarts with board B connected →
the assistant shows the red board banner, results are refused with [BOARD_CHANGED], [New session for this board]
asks with both UIDs and starts a session for board B. Isolated folders (B-SAFE-01).

Verifies: SW-BRU-002, SAF-SW-004
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

backend_mod = pytest.importorskip("bend_stand.core.backend")
pytest.importorskip("bend_stand.core.bringup")

from bend_stand.core.testing import isolated_settings  # noqa: E402
from test_sim_manual import run_until, step  # noqa: E402


def _be(tmp_path):
    be = backend_mod.Backend(backend_mod.BackendSettings(**isolated_settings(
        clock="lockstep", test_hooks=True, hotkey="off", sim_nvm_path=str(tmp_path / "nvm.json"))))
    be.start()
    be.test_hooks.result(be.connect_async("sim"), 5000)
    be.test_hooks.advance(200)
    return be


@pytest.mark.req("SW-BRU-002", "SAF-SW-004")
def test_other_board_on_open_session(make_window, tmp_path, monkeypatch, qtbot) -> None:
    """Verifies: SW-BRU-002, SAF-SW-004 — see the module docstring."""
    import bend_stand.io.sim.board as simboard
    be = _be(tmp_path)
    try:
        assert be.bringup.start().ok
        uid_a = be.bringup.status().board_uid
    finally:
        be.shutdown()
    other = "0123456789ABCDEF01234567"
    monkeypatch.setattr(simboard, "SIM_UID", other)
    be = _be(tmp_path)
    try:
        win = make_window(be)
        step(win, be, 100)
        dock = win.open_bringup()
        assert run_until(win, be, lambda: bool(be.bringup.status().board_mismatch), 3000), be.bringup.status()
        step(win, be)
        assert dock.board_banner.isVisibleTo(dock) and other in dock.board_label.text()
        assert uid_a in dock.board_label.text()
        dock.select("6.9")
        assert not dock.on_mark("PASS") and "[BOARD_CHANGED]" in dock.issue_label.text()
        dock.board_new_button.click()
        dlg = dock.confirm_dialog
        assert dlg is not None and other in dlg.text_label.text() and uid_a in dlg.text_label.text()
        qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
        assert run_until(win, be, lambda: be.bringup.status().board_uid == other
                         and not be.bringup.status().board_mismatch, 3000), be.bringup.status()
        step(win, be)
        assert not dock.board_banner.isVisibleTo(dock)
    finally:
        be.shutdown()
