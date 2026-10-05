"""M4 GUI on B's real ``Backend`` + in-process simulator, lock-step clock (D-06: no port): a travel sequence built
in the Sequence tab (typed steps, edits, loop) is started through C-07, runs to FINISHED with only absolute targets on
the wire, the run line / chart follow the executor, the report is built at the end and listed in the Report tab
(G-30, G-31, G-32, G-36 M4 part). Skipped while B's sequencer is not wired into the Backend.

Verifies: SW-SEQ-001, SW-SEQ-002, SW-SEQ-003, SW-SEQ-005, SW-SCH-001, SW-SCH-002, SW-REP-001, SYS-008, SAF-SW-004
"""
from __future__ import annotations

import os
import struct

import pytest
from fakes import tick
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

backend_mod = pytest.importorskip("bend_stand.core.backend")
pytest.importorskip("bend_stand.core.sequencer.executor")

from test_sim_manual import Cmd, lock_be, ready, run_until, step, tx  # noqa: E402, F401  (fixture re-used)


@pytest.fixture
def seq_win(make_window, lock_be):
    if not hasattr(lock_be.sequencer, "check_start"):
        pytest.skip("B's sequencer not wired into the Backend yet")
    win = make_window(lock_be)
    win.tabs.setCurrentWidget(win.manual_tab)
    step(win, lock_be, 50)
    return win


def _set(tab, row, field, text):
    m = tab.model
    assert m.setData(m.index(row, m.column_of(field)), text, Qt.ItemDataRole.EditRole), (row, field)


@pytest.mark.req("SW-SEQ-001", "SW-SEQ-002", "SW-SEQ-003", "SW-SEQ-005", "SW-SCH-002", "SW-REP-001", "SYS-008",
                 "SAF-SW-004")
def test_travel_sequence_runs_on_simulator(seq_win, lock_be, qtbot) -> None:
    """Verifies: SW-SEQ-001/002/003/005, SW-SCH-002, SW-REP-001, SYS-008 — editor → C-07 → start(confirmed) → run
    to FINISHED on the simulator; MOVE_ABS targets = x_zero + test targets (loop ×2); run line, active step and
    live marker followed; report built and listed in the Report tab."""
    win, be = seq_win, lock_be
    ready(win, be, qtbot)
    tab = win.sequence_tab
    win.tabs.setCurrentWidget(tab)
    step(win, be)
    for k in ("travel", "travel", "travel"):
        tab.table.clearSelection()
        assert tab.add_step(k)
    for row, tgt in ((0, "2"), (1, "4"), (2, "1")):
        _set(tab, row, "target", tgt)
        _set(tab, row, "speed_mm_s", "5")
        _set(tab, row, "settle_s", "0.2")
        _set(tab, row, "capture_s", "0.5" if row < 2 else "0")
    tab.select_rows([0, 1])
    assert tab.loop_selected(count=2)
    tab.revalidate()
    assert tab.valid_badge.text().startswith("● valid"), (tab.valid_badge.text(), [(i.uid, i.field, i.text) for i in tab.issues])
    assert tab.summary is not None and tab.summary.n_exec == 5
    x_zero = be.status().motion.position_mm
    n_moves = len(tx(be, Cmd.MOVE_ABS))
    assert run_until(win, be, lambda: tab.start_button.isEnabled(), 3000), tab.start_button.toolTip()
    qtbot.mouseClick(tab.start_button, Qt.MouseButton.LeftButton)
    dlg = tab.confirm_dialog
    if dlg is not None and dlg.outcome == "pending":                 # C-07: e.g. Pause/Break key unavailable
        assert dlg.cid == "C-07"
        qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: tab.view.active, 3000), tab.message_texts()
    seen_rows: set[int] = set()
    marker0 = tab.chart.marker_updates

    def done() -> bool:
        if tab.model.active_row is not None:
            seen_rows.add(tab.model.active_row)
        return tab.view.state in ("FINISHED", "STOPPED", "ABORTED", "ERROR")
    assert run_until(win, be, done, 120_000), tab.run_label.text()
    assert tab.view.state == "FINISHED", (tab.run_label.text(), tab.message_texts())
    assert seen_rows >= {0, 1, 2}
    assert tab.chart.marker_updates - marker0 > 10
    targets = [struct.unpack_from("<i", r.frame, 6)[0] for r in tx(be, Cmd.MOVE_ABS)[n_moves:]]
    want = [round((x_zero + d) * 1000) for d in (2, 4, 2, 4, 1)]
    assert targets == want, (targets, want)
    assert any("Sequence ended: FINISHED" in m for m in tab.message_texts())
    # report built at the end (recording started by the sequence) → listed in the Report tab
    rep = win.report_tab

    def listed() -> bool:
        rep.refresh_list()
        return any(r.get("has_report") for r in rep.recordings)
    assert run_until(win, be, listed, 30_000), rep.recordings
    folder = next(r["folder"] for r in rep.recordings if r.get("has_report"))
    win.tabs.setCurrentWidget(rep)
    rep.select_folder(folder)
    QApplication.processEvents()
    assert rep.result_table.rowCount() >= 4, rep.sel_label.text()
    assert os.path.exists(os.path.join(folder, "report.html"))
    tick(win)


@pytest.mark.req("SW-STOP-004", "SW-SEQ-007")
def test_sequence_pause_resume_and_stop_on_simulator(seq_win, lock_be, qtbot) -> None:
    """Verifies: SW-STOP-004, SW-SEQ-007 — Sequence Pause → FW PAUSE, state PAUSED (PC), editor read-only;
    Resume → RESUME on the wire, the run continues; Stop (controlled) ends it STOPPED."""
    win, be = seq_win, lock_be
    ready(win, be, qtbot)
    tab = win.sequence_tab
    win.tabs.setCurrentWidget(tab)
    step(win, be)
    for k in ("travel", "travel"):
        tab.table.clearSelection()
        assert tab.add_step(k)
    for row, tgt in ((0, "20"), (1, "2")):
        _set(tab, row, "target", tgt)
        _set(tab, row, "speed_mm_s", "2")
    tab.revalidate()
    assert run_until(win, be, lambda: tab.start_button.isEnabled(), 3000), tab.start_button.toolTip()
    qtbot.mouseClick(tab.start_button, Qt.MouseButton.LeftButton)
    dlg = tab.confirm_dialog
    if dlg is not None and dlg.outcome == "pending":
        qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: be.status().motion.moving, 5000)
    assert run_until(win, be, lambda: tab.pause_button.isEnabled(), 2000)
    qtbot.mouseClick(tab.pause_button, Qt.MouseButton.LeftButton)
    assert tx(be, Cmd.PAUSE)
    assert run_until(win, be, lambda: tab.view.state == "PAUSED", 5000), tab.run_label.text()
    assert "PAUSED" in tab.run_label.text() and tab.model.read_only
    assert run_until(win, be, lambda: tab.resume_button.isEnabled() and not be.status().motion.moving, 5000)
    qtbot.mouseClick(tab.resume_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: tab.view.state == "RUNNING", 5000), tab.run_label.text()
    assert tx(be, Cmd.RESUME)
    qtbot.mouseClick(tab.stop_seq_button, Qt.MouseButton.LeftButton)
    assert run_until(win, be, lambda: tab.view.state == "STOPPED", 10_000), tab.run_label.text()
    assert not tab.model.read_only or run_until(win, be, lambda: not tab.model.read_only, 2000)
