"""WP-D7: the GUI on B's real ``Backend`` + in-process FW simulator (endpoint "sim"; D-06: no port is opened).

G-36 (M1 part: connect → stream → plot → config), G-17 / G-18 / G-41 simulator variants (write statuses incl.
MISMATCH via ``inject_store_mismatch`` and REJECTED via ``inject_nack``, REBOOT_REQUIRED, NVM save, file round
trip), STOP / PAUSE / Resume / Clear stop on the simulator, perf smoke (informative, offscreen).

The module is skipped while ``bend_stand.core.backend`` is not importable (B's WP-B11).

Verifies: SYS-008, SW-PLT-003, SW-CFG-001, SW-CFG-002, SW-CFG-003, SW-CFG-004, SW-ACQ-001, SW-RT-002, SW-RT-005,
SW-STOP-001, SW-STOP-003, SW-STOP-004, SAF-SW-005, NFR-001
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading

import numpy as np
import pytest
from fakes import tick
from PySide6.QtCore import Qt

backend_mod = pytest.importorskip("bend_stand.core.backend")

from bend_stand.core import protocol_gen as pg  # noqa: E402
from bend_stand.core.api import BackendAPI, GateId  # noqa: E402
from bend_stand.gui.dialogs import safe_dialog  # noqa: E402

T_CONNECT_MS = 10000


@pytest.fixture
def sim_backend(tmp_path):
    # hotkey "off": no system-wide key hook in GUI tests; the GUI then uses its app-level shortcut (§5.3)
    be = backend_mod.Backend(backend_mod.BackendSettings(sim_nvm_path=str(tmp_path / "nvm.json"),
                                                         recordings_root=str(tmp_path / "rec"), hotkey="off"))
    assert isinstance(be, BackendAPI)
    be.start()
    yield be
    be.shutdown()


@pytest.fixture
def sim_window(make_window, sim_backend, qtbot):
    win = make_window(sim_backend)
    win.refresh.start()                                   # real 33 ms timer against the real backend
    tab = win.connection_tab
    tab.selector.set_endpoint("sim")
    tab.connect_button.click()
    qtbot.waitUntil(lambda: str(sim_backend.status().link.state.value) == "CONNECTED", timeout=T_CONNECT_MS)
    qtbot.waitUntil(lambda: sim_backend.status().stream.on and sim_backend.status().board is not None,
                    timeout=T_CONNECT_MS)
    qtbot.waitUntil(lambda: tab.form.board_value("afe.rate_sps") is not None, timeout=T_CONNECT_MS)
    return win


@pytest.fixture
def oop_sim_endpoint():
    """MC3-4: the out-of-process simulator (``python -m bend_stand.io.sim.server``) on ephemeral loopback ports, so
    a stall of the test process cannot starve the simulated board. Started and stopped by this fixture (own PID)."""
    import os
    from pathlib import Path

    import bend_stand
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(Path(bend_stand.__file__).resolve().parents[1])] + ([os.environ["PYTHONPATH"]]
                                                                if os.environ.get("PYTHONPATH") else [])))
    proc = subprocess.Popen([sys.executable, "-m", "bend_stand.io.sim.server", "--port", "0", "--ctl", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.append(proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(20.0)
    try:
        if not lines or "tcp://127.0.0.1:" not in lines[0]:
            pytest.fail(f"out-of-process simulator did not start: {lines!r}")
        port = lines[0].split("tcp://127.0.0.1:", 1)[1].split()[0]
        yield f"tcp://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)


@pytest.fixture
def oop_sim_window(make_window, sim_backend, oop_sim_endpoint, qtbot):
    win = make_window(sim_backend)
    win.refresh.start()
    win.connection_tab.connect_to(oop_sim_endpoint)
    qtbot.waitUntil(lambda: str(sim_backend.status().link.state.value) == "CONNECTED", timeout=T_CONNECT_MS)
    qtbot.waitUntil(lambda: sim_backend.status().stream.on and sim_backend.status().board is not None,
                    timeout=T_CONNECT_MS)
    return win


def _status_of(form, key):
    return form.status_of(key)


@pytest.mark.req("SYS-008", "SW-PLT-003", "SW-ACQ-001", "SW-RT-002", "SW-RT-005")
def test_connect_stream_plot(sim_window, sim_backend, qtbot) -> None:
    """Verifies: SYS-008, SW-PLT-003, SW-ACQ-001, SW-RT-002, SW-RT-005 (G-36 M1) — connect through the GUI, the
    stream runs, Plot 1 draws raw + status bits from data.snapshot, readouts show raw / rate, link counters grow,
    the device line names the simulator FW; Stream toolbar button stops and restarts the stream."""
    win = sim_window
    tab = win.connection_tab
    assert "Device: FW" in tab.device_label.text() and "✓" in tab.device_label.text()
    assert tab.version_label.text().startswith("Version check: ✓")
    plot = win.plot_dock
    assert "bit.valid" in plot.tree.all_keys() and not plot.tree.is_available("F_N")
    plot.tree.set_checked("raw", True)
    plot.tree.set_checked("bit.moving", True)
    def finite_points() -> int:
        _x, y = plot.pane_of("raw").curve("raw").getData()
        return 0 if y is None else int(np.isfinite(y).sum())
    qtbot.waitUntil(lambda: plot.updates > 5 and finite_points() > 40, timeout=5000)   # ≥ 0.25 s of 80 Hz data
    assert plot.pane_of("bit.moving") is not plot.pane_of("raw")                     # quantity grouping (D-38)
    assert plot.pane_of("bit.moving").bit_keys() == ["bit.moving"]
    qtbot.waitUntil(lambda: win.readout_dock.state_of("raw") == "OK", timeout=3000)
    rate = float(win.readout_dock.value_text("rate_sps"))
    assert 70.0 < rate < 90.0
    qtbot.waitUntil(lambda: "frames" in tab.link_label.text() and "frames 0 " not in tab.link_label.text(),
                    timeout=3000)
    assert win.stream_button.isChecked()
    win.stream_button.click()
    qtbot.waitUntil(lambda: not sim_backend.status().stream.on and not win.stream_button.isChecked(), timeout=5000)
    win.stream_button.click()
    qtbot.waitUntil(lambda: sim_backend.status().stream.on and win.stream_button.isChecked(), timeout=5000)
    assert win.indicator_bar.chip("LINK").level == "ok"
    assert win.indicator_bar.chip("ESTOP").level == "ok"            # live data → known, not grey


@pytest.mark.req("SW-CFG-001", "SW-CFG-003", "SW-CFG-004")
def test_config_write_verify_nvm(sim_window, sim_backend, qtbot) -> None:
    """Verifies: SW-CFG-001, SW-CFG-003, SW-CFG-004 (G-17 / G-41 sim) — board values equal the simulator; write &
    verify → OK; injected store mismatch → MISMATCH; injected NACK → REJECTED; CFG dirty → Save to NVM → clean."""
    win = sim_window
    tab = win.connection_tab
    form = tab.form
    from bend_stand.gui.widgets.param_form import normalize
    values = sim_backend.config.values()
    metas = {m.key: m for m in sim_backend.config.metas()}
    assert set(values) == set(form.keys())
    for k, v in values.items():
        assert form.board_value(k) == normalize(metas[k], v), k

    def write(key, value, want):
        form.set_edit_value(key, value)
        qtbot.waitUntil(lambda: not tab._rule_timer.isActive(), timeout=2000)
        tick(win)
        assert tab.write_button.isEnabled(), tab.write_button.toolTip()
        tab.write_button.click()
        qtbot.waitUntil(lambda: form.status_of(key) == want, timeout=5000)

    write("afe.settle_discard", 5, "OK")
    assert form.board_value("afe.settle_discard") == 5
    sim_backend.sim.inject_store_mismatch("afe.settle_discard", 7)
    write("afe.settle_discard", 6, "MISMATCH")
    sim_backend.sim.inject_nack("SET_PARAM", int(pg.Status.E_RANGE), 0x0104, 1)
    write("afe.settle_discard", 4, "REJECTED")
    assert "outside" in form.item_for("afe.settle_discard").toolTip(6)
    qtbot.waitUntil(lambda: "dirty" in tab.cfg_label.text(), timeout=5000)
    tab.save_nvm_button.click()
    qtbot.waitUntil(lambda: tab.cfg_label.text() == "CFG: ● clean (RAM = NVM)", timeout=5000)


@pytest.mark.req("SW-CFG-003")
def test_live_rule_check_from_backend(sim_window, qtbot) -> None:
    """Verifies: SW-CFG-003 (G-41 sim) — the backend's config.check marks a violation and blocks Write & verify."""
    tab = sim_window.connection_tab
    travel = tab.form.board_value("motion.v_max_travel_um_s")
    tab.form.set_edit_value("motion.v_max_load_um_s", min(travel + 1000, 250000))
    qtbot.waitUntil(lambda: "✗" in tab.rule_label.text(), timeout=3000)
    tick(sim_window)
    assert not tab.write_button.isEnabled()
    tab.form.revert_edits()
    qtbot.waitUntil(lambda: tab.rule_label.text() == "Rule check: ✓", timeout=3000)


@pytest.mark.req("SW-CFG-003")
def test_reboot_required_flow(sim_window, sim_backend, qtbot) -> None:
    """Verifies: SW-CFG-003 (G-41 sim) — reboot-required write → REBOOT_REQUIRED + notice; Save & reboot (C-11) →
    save then reboot; the GUI follows the board until the flag clears."""
    win = sim_window
    tab = win.connection_tab
    tab.form.show_advanced.setChecked(True)
    tab.form.set_edit_value("motion.pul_invert", True)
    qtbot.waitUntil(lambda: not tab._rule_timer.isActive(), timeout=2000)
    tick(win)
    tab.write_button.click()
    qtbot.waitUntil(lambda: tab.form.status_of("motion.pul_invert") == "REBOOT_REQUIRED", timeout=5000)
    qtbot.waitUntil(lambda: "reboot_pending" in win.notice_strip.keys() and tab.reboot_button.isEnabled(),
                    timeout=5000)
    tab.reboot_button.click()
    qtbot.mouseClick(tab.confirm_dialog.confirm_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim_backend.status().reboot_pending is False and
                    str(sim_backend.status().link.state.value) == "CONNECTED", timeout=15000)
    qtbot.waitUntil(lambda: "reboot_pending" not in win.notice_strip.keys(), timeout=3000)


@pytest.mark.req("SW-CFG-002")
def test_board_file_round_trip(sim_window, tmp_path, qtbot) -> None:
    """Verifies: SW-CFG-002 (sim) — save the Edit column through the backend, reload into the Edit column only."""
    tab = sim_window.connection_tab
    path = str(tmp_path / "board.bbboard.json")
    safe_dialog.FILE_DIALOG_HOOK[0] = lambda kind, cap, flt: path
    tab.form.set_edit_value("afe.settle_discard", 9)
    tab.save_file_button.click()
    data = open(path, encoding="utf-8").read()
    assert "afe.settle_discard" in data and "safety.zero_raw" not in json.dumps(json.loads(data))
    tab.form.revert_edits()
    tab.load_file_button.click()
    assert tab.form.editor_value("afe.settle_discard") == 9 and tab.form.is_dirty("afe.settle_discard")
    assert tab.form.board_value("afe.settle_discard") != 9


@pytest.mark.req("SW-STOP-001", "SW-STOP-003", "SW-STOP-004", "SAF-SW-005")
def test_stop_pause_resume_clear_on_simulator(sim_window, sim_backend, qtbot) -> None:
    """Verifies: SW-STOP-001/003/004, SAF-SW-005 — toolbar STOP is sent; Pause → PAUSED chip + Resume button;
    Resume clears it; app-shortcut HALT (global hotkey unavailable in M1) → HALT banner; Clear stop clears it."""
    win = sim_window
    qtbot.mousePress(win.stop_button, Qt.MouseButton.LeftButton)
    # M3: the first-use row (no calibration / tare, SAF-SW-001) outranks the "sent" row (§2.3 severity order)
    assert any("STOP sent (toolbar" in r.text for r in win.stop_banner.rows)
    qtbot.waitUntil(lambda: win.pause_button.isEnabled(), timeout=3000)
    qtbot.mousePress(win.pause_button, Qt.MouseButton.LeftButton)
    qtbot.mouseRelease(win.pause_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.pause_button.text() == "▶ Resume", timeout=5000)
    assert win.indicator_bar.chip("PAUSED").level == "warn"
    qtbot.mouseClick(win.pause_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.pause_button.text() == "‖ Pause", timeout=5000)
    assert win.app_shortcuts(), "hotkey off → UNAVAILABLE → app shortcut expected (§5.3)"
    win.app_shortcuts()[0].activated.emit()
    qtbot.waitUntil(lambda: win.indicator_bar.chip("HALT").level == "alarm", timeout=5000)
    assert "HALT latched" in " ".join(r.text for r in win.stop_banner.rows)
    win.open_clear_stop()
    dlg = win.dialogs["clear"]
    qtbot.waitUntil(lambda: dlg.halt_button.isEnabled(), timeout=3000)
    qtbot.mouseClick(dlg.halt_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.indicator_bar.chip("HALT").level == "ok", timeout=5000)
    assert dlg.halt_result.text().startswith("cleared")
    dlg.close()


@pytest.mark.req("NFR-001", "SW-RT-006")
def test_perf_smoke_on_simulator(oop_sim_window, qtbot, capsys) -> None:
    """Verifies: NFR-001, SW-RT-006 (smoke, informative; binding at M3 on the reference PC) — real timer, real backend,
    out-of-process simulator (MC3-4: a stalled test process cannot starve the board), 4 time panes (raw, status bits, travel, rate) + an X-Y pane in 2 columns for 5 s offscreen; one snapshot per
    refresh; frame interval and stage times recorded."""
    win = sim_window = oop_sim_window
    plot = win.plot_dock
    for g in plot.tree.group_names():
        if g.startswith("status"):
            plot.tree.set_group_checked(g, True)
    plot.tree.set_checked("raw", True)
    plot.tree.set_checked("x_mm", True)
    plot.tree.set_checked("rate_sps", True)
    plot.add_xy_pane()
    plot.set_columns(2)
    assert len(plot.panes()) == 5                 # raw · bits · travel · rate · X-Y (4 time panes, SW-RT-006)
    n0 = win.snapshot_calls
    start = win.perf_stats()["ticks"]
    qtbot.wait(5000)
    ps = win.perf_stats()
    with capsys.disabled():
        print(f"\n[perf smoke sim out of process, 4 time panes + X-Y, offscreen, 5 s] ticks {ps['ticks'] - start}, interval p50 "
              f"{ps['interval_p50_ms']:.1f} ms p95 {ps['interval_p95_ms']:.1f} ms max {ps['interval_max_ms']:.1f} ms;"
              f" plots p95 {ps['plots_p95_ms']:.2f} ms; status p95 {ps['status_p95_ms']:.2f} ms")
    assert ps["ticks"] - start > 50 and ps["errors"] == {}
    assert win.snapshot_calls - n0 <= ps["ticks"] - start          # one snapshot per refresh for all panes
    assert sim_window.backend.status().gates[GateId.STREAM_STOP].ok, win.link_lost_diagnostics


@pytest.mark.req("SW-STOP-002")
def test_real_hotkey_status_shown(make_window, tmp_path, qtbot) -> None:
    """Verifies: SW-STOP-002 (B4-07) — the KEY chip follows the backend's real hotkey status; with the key active
    (fake hook) the GUI installs no app-level Pause shortcut (never a double path, GQ-19)."""
    be = backend_mod.Backend(backend_mod.BackendSettings(sim_nvm_path=str(tmp_path / "nvm.json"),
                                                         recordings_root=str(tmp_path / "rec"), hotkey="fake"))
    be.start()
    try:
        win = make_window(be)
        tick(win)
        hk = be.status().hotkey
        chip = win.indicator_bar.chip("KEY")
        assert chip.value.text() in (hk.mode, "test running") and "KL-01" in chip.toolTip()
        assert bool(win.app_shortcuts()) == (hk.mode == "UNAVAILABLE")
        assert hk.mode in ("REGISTERED", "LL_HOOK")
    finally:
        be.shutdown()
