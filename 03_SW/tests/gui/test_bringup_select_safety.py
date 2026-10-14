"""SW-BRU-004 (Validator E's review): selecting a bring-up step — sidebar click, [◀ Previous] / [Next ▶] — never
starts an action and sends no command frame (only the backend's own background polls may appear on the wire); the
toolbar STOP, the Pause/Break path and the stop banners stay reachable with the assistant docked and floating.

Verifies: SW-BRU-004, SW-STOP-001, SW-STOP-002
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

backend_mod = pytest.importorskip("bend_stand.core.backend")
pytest.importorskip("bend_stand.core.bringup")

from bend_stand.core import protocol_gen as pg  # noqa: E402
from test_sim_manual import run_until, step  # noqa: E402
from bend_stand.core.testing import isolated_settings  # noqa: E402  B-SAFE-01: never the real user folders

#: frames the backend sends by itself (supervision), independent of the GUI
BACKGROUND = {int(pg.Cmd.PING), int(pg.Cmd.GET_STATUS)}


def _tx(be) -> list:
    return [r for r in be.test_hooks.wire_log() if r.direction == "TX"]


@pytest.mark.req("SW-BRU-004", "SW-STOP-001", "SW-STOP-002")
def test_selecting_steps_sends_nothing_and_stops_stay_reachable(make_window, tmp_path, monkeypatch, qtbot) -> None:
    """Verifies: SW-BRU-004, SW-STOP-001, SW-STOP-002 on B's engine + simulator (wire log): every step selected by
    sidebar click and by Next / Previous → no TX frame except PING / GET_STATUS, no engine action started, no step
    state changed; the toolbar STOP stays visible / enabled and a HALT banner is shown with the assistant docked and
    floating; the Pause/Break app shortcut (global hotkey off) is application-wide and sends HALT while the floating
    assistant is the active window."""
    monkeypatch.setenv("BEND_STAND_BRINGUP_DIR", str(tmp_path / "pk"))
    be = backend_mod.Backend(backend_mod.BackendSettings(**isolated_settings(
        clock="lockstep", test_hooks=True, wire_log=True, hotkey="off", sim_nvm_path=str(tmp_path / "nvm.json"),
        recordings_root=str(tmp_path / "rec"))))
    be.start()
    try:
        be.test_hooks.result(be.connect_async("sim"), 5000)
        win = make_window(be)
        step(win, be, 100)
        dock = win.open_bringup()
        qtbot.mouseClick(dock.start_button, Qt.MouseButton.LeftButton)
        assert run_until(win, be, lambda: be.bringup.status().active, 3000)
        step(win, be, 200)
        states0 = {s.id: (s.state, s.action_state) for s in be.bringup.steps()}
        n0 = len(_tx(be))
        # sidebar clicks on every step
        tree = dock.tree

        def item_of(sid):                       # fresh lookup: the sidebar is rebuilt when a state changes
            for i in range(tree.topLevelItemCount()):
                top = tree.topLevelItem(i)
                for j in range(top.childCount()):
                    if top.child(j).data(0, Qt.ItemDataRole.UserRole) == sid:
                        return top.child(j)
            return None
        for sid in [s.id for s in be.bringup.steps()]:
            it = item_of(sid)
            tree.scrollToItem(it)
            qtbot.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualItemRect(it).center())
            assert dock.current == sid
            step(win, be, 20)
        assert dock.current == be.bringup.steps()[-1].id
        # Previous all the way, then Next all the way
        while dock.prev_button.isEnabled():
            qtbot.mouseClick(dock.prev_button, Qt.MouseButton.LeftButton)
            step(win, be, 20)
        while dock.next_button.isEnabled():
            qtbot.mouseClick(dock.next_button, Qt.MouseButton.LeftButton)
            step(win, be, 20)
        sent = [r for r in _tx(be)[n0:] if r.frame[2] not in BACKGROUND]
        assert sent == [], [pg.Cmd(r.frame[2]).name for r in sent]
        assert not be.status().motion.moving and not be.bringup.status().action_running
        changed = {s.id for s in be.bringup.steps() if (s.state, s.action_state) != states0[s.id]
                   and s.state != "PASS"}                     # automatic checks may pass; nothing may start / fail
        assert changed == set(), changed
        # stops on top of the docked and the floating assistant
        for floating in (False, True):
            dock.setFloating(floating)
            if floating:
                dock.activateWindow()
            QApplication.processEvents()
            step(win, be)
            assert win.stop_button.isVisible() and win.stop_button.isEnabled()
            assert dock.stop_button.isVisible()
            r = be.halt("hotkey")
            win._show_stop_result(r)                                                # noqa: SLF001
            assert run_until(win, be, lambda: be.status().indicators.halt.state == "ON", 3000)
            step(win, be)
            assert win.stop_banner.isVisible() and "HALT latched" in win.stop_banner.top_text()
            be.test_hooks.result(be.clear_stop_async(), 3000)
            assert run_until(win, be, lambda: be.status().indicators.halt.state == "OFF", 3000)
        # Pause/Break app shortcut (global hotkey off → UNAVAILABLE): application-wide, works from the floating dock
        scs = win.app_shortcuts()
        assert scs and all(sc.context() == Qt.ShortcutContext.ApplicationShortcut for sc in scs)
        n_halt = len([r for r in _tx(be) if r.frame[2] == int(pg.Cmd.HALT)])
        scs[0].activated.emit()
        step(win, be)
        assert len([r for r in _tx(be) if r.frame[2] == int(pg.Cmd.HALT)]) > n_halt
        dock.setFloating(False)
    finally:
        be.shutdown()
