"""Main window (WP-D2): toolbar order and STOP (G-02 toolbar part, G-03), Stream / Record / TARE / Sample on every
tab (G-11), Pause/Resume (G-09 M1 part), Clear stop dialog incl. B31-01 NOT_CONFIRMED (G-07 M1 part), hotkey
fallback (G-10 M1 part), banners / notices / mode banner, refresh timer, close rules.

Verifies: SW-STOP-001, SW-STOP-002, SW-STOP-003, SW-STOP-004, SW-ACQ-001, SW-ACQ-002, SW-ACQ-003, SW-TARE-001,
SAF-SW-005, IF-008, SW-LIM-004, NFR-001, SW-PLT-003
"""
from __future__ import annotations

import pytest
from fakes import confirm, ind, refuse, tick, warn
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton

from bend_stand.core.api import (
    GATE_OK, ClearResult, Compat, EventRecord, FwEvent, GateId, HotkeyStatus, LinkStatus, RecordingStatus,
    ResumeIgnored, StopResult,
)
from bend_stand.gui.widgets.stop_button import StopButton


def toolbar_widgets(win):
    tb = win.toolbar
    return [tb.widgetForAction(a) for a in tb.actions()]


# --------------------------------------------------------------------------------------------- toolbar / STOP

@pytest.mark.req("SW-STOP-001")
def test_toolbar_fixed_and_stop_first(window) -> None:
    """Verifies: SW-STOP-001 — STOP is the first toolbar item, then the safety/acquisition items in design order;
    the toolbar is not movable / floatable / hideable and has no context menu."""
    ws = toolbar_widgets(window)
    assert isinstance(ws[0], StopButton) and ws[0].minimumHeight() >= 48
    names = [w.objectName() for w in ws]
    order = ["stopButton", "pauseButton", "clearStopButton", "tareButton", "streamButton", "recordButton",
             "sampleButton"]
    assert names[:len(order)] == order
    tb = window.toolbar
    assert not tb.isMovable() and not tb.isFloatable() and not tb.toggleViewAction().isVisible()
    assert window.createPopupMenu() is None
    for w in ws[1:7]:
        assert w.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert isinstance(w, QToolButton) and not w.shortcut().toString()


@pytest.mark.req("SW-STOP-001", "NFR-002")
def test_toolbar_stop_calls_backend_synchronously(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-001 — the press calls backend.stop("toolbar") in the same event-loop turn and the banner
    shows the real StopResult."""
    connected_fake.calls.clear()
    qtbot.mousePress(window.stop_button, Qt.MouseButton.LeftButton)
    assert connected_fake.call_names()[0] == "stop"               # before any event processing
    assert connected_fake.calls[0].args == ("toolbar",)
    assert "STOP sent (toolbar" in window.stop_banner.top_text()
    assert window.stop_banner.isVisibleTo(window)


@pytest.mark.req("SW-STOP-001")
def test_stop_not_sent_when_disconnected(make_window, fake, qtbot) -> None:
    """Verifies: SW-STOP-001 (G-03) — STOP enabled while disconnected; banner says NOT SENT with the reason and
    points to the E-stop (D-36)."""
    win = make_window(fake)
    tick(win)
    assert win.stop_button.isEnabled()
    qtbot.mousePress(win.stop_button, Qt.MouseButton.LeftButton)
    t = win.stop_banner.top_text()
    assert "STOP NOT SENT" in t and "not connected" in t and "E-stop" in t
    assert "STOP/BREAK" not in t


@pytest.mark.req("SW-STOP-001")
def test_stop_unconfirmed_banner(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-001 (G-03) — stop.unconfirmed (STOP / HALT / PAUSE) → red NOT CONFIRMED banner."""
    for cmd in ("STOP", "HALT", "PAUSE"):
        connected_fake.events.emit("stop.issued", StopResult(cmd, "key", True))
        connected_fake.events.emit("stop.unconfirmed", StopResult(cmd, "key", True))
        qtbot.waitUntil(lambda c=cmd: f"{c} NOT CONFIRMED" in window.stop_banner.top_text(), timeout=2000)
        connected_fake.events.emit("stop.issued", StopResult(cmd, "key", True))
        connected_fake.events.emit("stop.confirmed", StopResult(cmd, "key", True))
        qtbot.waitUntil(lambda c=cmd: "Confirmed by the board" in window.stop_banner.top_text(), timeout=2000)


@pytest.mark.req("SW-STOP-001", "SW-RT-001")
def test_stop_everywhere_in_main_window(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-001, SW-RT-001 (G-02 M1) — toolbar, Plot 1 (docked and floating), Readouts and Event log
    each carry exactly one STOP and every press reaches backend.stop."""
    window.event_log.show()
    sources = []
    for host in (window.plot_dock, window.readout_dock, window.event_log):
        for floating in (False, True):
            host.setFloating(floating)
            stops = [b for b in host.findChildren(StopButton) if b.isVisibleTo(host)]
            assert len(stops) == 1, host.objectName()
            connected_fake.calls.clear()
            qtbot.mousePress(stops[0], Qt.MouseButton.LeftButton)
            assert connected_fake.call_names() == ["stop"]
            sources.append(connected_fake.calls[0].args[0])
        host.setFloating(False)
    assert sources[0] == "plot:Plot 1"


# --------------------------------------------------------------------------------------------- G-11

@pytest.mark.req("SW-ACQ-001")
def test_stream_on_every_tab_and_follows_status(window, connected_fake, qtbot) -> None:
    """Verifies: SW-ACQ-001 (G-11) — Stream reachable on every tab; click → stream_start/stop_async; the checked
    state follows status().stream.on only (P4)."""
    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        assert window.stream_button.isVisible() and window.stream_button.isEnabled()
    window.stream_button.click()
    assert connected_fake.call_names()[-1] == "stream_start_async"
    tick(window)
    assert window.stream_button.isChecked() and "■" in window.stream_button.text()
    window.stream_button.click()
    assert connected_fake.call_names()[-1] == "stream_stop_async"
    tick(window)
    assert not window.stream_button.isChecked()
    connected_fake.set_gate(GateId.STREAM_START, refuse("LINK_DOWN", "not connected"))
    tick(window)
    assert not window.stream_button.isEnabled() and "not connected" in window.stream_button.toolTip()


@pytest.mark.req("SW-ACQ-002", "SW-ACQ-003", "SW-TARE-001")
def test_record_tare_sample_on_every_tab(window, connected_fake, qtbot) -> None:
    """Verifies: SW-ACQ-002, SW-ACQ-003, SW-TARE-001 — present on every tab; refusals shown verbatim."""
    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        for b in (window.record_button, window.tare_button, window.sample_button):
            assert b.isVisible()
    window.tare_button.click()
    assert connected_fake.call_names()[-1] == "tare" and "not implemented in M1" in window.toast_label.text()
    window.record_button.click()
    assert connected_fake.call_names()[-1] == "record_start" and "Record start refused" in window.toast_label.text()
    connected_fake.results["record_start"] = warn("MARKS", "marks incomplete")
    window.record_button.click()
    assert "marks incomplete" in window.toast_label.text()
    connected_fake.set_status(recording=RecordingStatus("RECORDING", "C:/x", 10))
    tick(window)
    assert window.record_button.isChecked()
    window.record_button.click()
    assert connected_fake.call_names()[-1] == "record_stop"
    connected_fake.results["take_sample"] = GATE_OK
    window.sample_button.click()
    assert connected_fake.calls[-1].name == "take_sample" and connected_fake.calls[-1].args == (1.0,)
    window.sample_button.menu().actions()[-1].trigger()
    assert connected_fake.calls[-1].args == (10.0,)
    connected_fake.set_gate(GateId.TARE, refuse("STREAM_OFF", "stream is off"))
    tick(window)
    assert not window.tare_button.isEnabled() and "stream is off" in window.tare_button.toolTip()


# --------------------------------------------------------------------------------------------- Pause / Resume

@pytest.mark.req("SW-STOP-004", "SAF-SW-005")
def test_pause_resume_follow_fw_state(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-004 — Pause on press → backend.pause("toolbar"), text unchanged until PAUSED is reported
    (P4); then Resume → backend.resume; a HALT refusal → "Clear stop first"."""
    pb = window.pause_button
    assert pb.text() == "‖ Pause"
    connected_fake.calls.clear()
    qtbot.mousePress(pb, Qt.MouseButton.LeftButton)
    qtbot.mouseRelease(pb, Qt.MouseButton.LeftButton)
    assert connected_fake.call_names() == ["pause"] and connected_fake.calls[0].args == ("toolbar",)
    assert "PAUSE sent" in window.stop_banner.top_text()
    assert pb.text() == "‖ Pause"                                   # no optimistic change
    connected_fake.set_indicators(paused=ind("ON", "PC"))
    tick(window)
    assert pb.text() == "▶ Resume"
    assert "PAUSED (PC)" in " ".join(r.text for r in window.stop_banner.rows)
    connected_fake.calls.clear()
    qtbot.mouseClick(pb, Qt.MouseButton.LeftButton)
    assert connected_fake.call_names() == ["resume"]
    assert "Resume sent" in window.toast_label.text()
    connected_fake.results["resume"] = refuse("HALT", "HALT latched", "Clear stop")
    connected_fake.set_gate(GateId.RESUME, refuse("HALT", "HALT latched", "Clear stop"))
    tick(window)
    assert not pb.isEnabled() and pb.toolTip().startswith("Clear stop first: HALT latched")
    assert "Clear stop first: HALT latched" in " ".join(r.text for r in window.stop_banner.rows)
    window._on_resume_result(connected_fake.resume("banner"))
    assert "Clear stop first: HALT latched" in window.toast_label.text()


@pytest.mark.req("SW-STOP-004")
def test_pause_disabled_without_gate(window, connected_fake) -> None:
    """Verifies: SW-STOP-004 — a missing ``pause`` gate disables the motion-related control (fail-safe §2.7)."""
    connected_fake.set_gate(GateId.PAUSE, None)
    tick(window)
    assert not window.pause_button.isEnabled()


@pytest.mark.req("SW-STOP-004")
def test_resume_ignored_and_resume_request_toasts(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-004 — resume.ignored and RESUME_REQUEST become toasts and Event-log rows."""
    connected_fake.events.emit("resume.ignored", ResumeIgnored("button", refuse("HALT", "HALT latched")))
    qtbot.waitUntil(lambda: "Resume request ignored: HALT latched" in window.toast_label.text(), timeout=2000)
    code = list(__import__("bend_stand.core.protocol_gen", fromlist=["x"]).EVENT_NAMES).index("RESUME_REQUEST")
    connected_fake.events.emit("fw.event", FwEvent(1, 0, code, "RESUME_REQUEST", 0, None, 0, 0))
    qtbot.waitUntil(lambda: "resume requested" in window.toast_label.text(), timeout=2000)
    assert any("RESUME_REQUEST" in t for t in window.event_log.texts())


# --------------------------------------------------------------------------------------------- Clear stop

@pytest.mark.req("SW-STOP-003", "SAF-SW-005")
def test_halt_banner_and_clear_dialog(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-003 — HALT latched → red banner with source + clear_hint; Clear stop dialog row calls
    clear_stop_async(); NOT_CONFIRMED → "click again" (B31-01); no motion call follows."""
    connected_fake.set_indicators(halt=ind("ON", "KEY", hint="Clear stop (HALT_CLEAR)"), paused=ind("ON", "PC"))
    connected_fake.set_gate(GateId.CLEAR_STOP, GATE_OK)
    tick(window)
    t = window.stop_banner.top_text()
    assert "HALT latched – source: KEY" in t and "Clear stop (HALT_CLEAR)" in t
    assert window.clear_button.text() == "Clear stop (2)" and window.clear_button.isEnabled()
    window.clear_button.click()
    dlg = window.dialogs["clear"]
    assert dlg.halt_button.text() == "Clear HALT + PAUSE" and dlg.halt_button.isEnabled()
    assert dlg.halt_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    connected_fake.calls.clear()
    qtbot.mouseClick(dlg.halt_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls[0].name == "clear_stop_async" and connected_fake.calls[0].kwargs == {
        "confirmed": False}
    qtbot.waitUntil(lambda: dlg.halt_result.text().startswith("cleared"), timeout=2000)
    connected_fake.results["clear_stop"] = ClearResult("HALT_CLEAR", True, False, "NOT_CONFIRMED")
    qtbot.mouseClick(dlg.halt_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: dlg.halt_result.text() == "Clear not confirmed — click again", timeout=2000)
    assert not any(n.startswith("motion.") for n in connected_fake.call_names())
    dlg.close()


@pytest.mark.req("SW-STOP-003", "SW-STOP-004", "SAF-SW-004")
def test_clear_paused_sequence_needs_c13(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-004 — clear_stop CONFIRM (sequence PAUSED) → C-13 → clear_stop_async(confirmed=True)."""
    connected_fake.set_indicators(paused=ind("ON", "PC"))
    connected_fake.set_gate(GateId.CLEAR_STOP, confirm("SEQUENCE_PAUSED", "ends the paused sequence"))
    tick(window)
    window.open_clear_stop()
    dlg = window.dialogs["clear"]
    qtbot.mouseClick(dlg.halt_button, Qt.MouseButton.LeftButton)
    c = dlg.confirm_dialog
    assert c is not None and c.cid == "C-13" and "ends the paused sequence" in c.text_label.text()
    assert "clear_stop_async" not in connected_fake.call_names()
    qtbot.keyClick(c, Qt.Key.Key_Return)
    assert "clear_stop_async" not in connected_fake.call_names()
    qtbot.mouseClick(c.confirm_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("clear_stop_async")[-1].kwargs == {"confirmed": True}
    dlg.close()


@pytest.mark.req("SW-STOP-003", "SAF-SW-004")
def test_estop_clear_needs_assertion(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-003, SAF-SW-004 (C-03) — E-STOP clear only after the assertion; "ENABLE and HOME" after."""
    connected_fake.set_indicators(estop=ind("ON", hint="release, RESET K1, Clear stop"))
    tick(window)
    assert window.stop_banner.top_text().startswith("E-STOP active")
    window.open_clear_stop()
    dlg = window.dialogs["clear"]
    assert not dlg.estop_button.isEnabled()
    dlg.estop_check.setChecked(True)
    assert dlg.estop_button.isEnabled()
    qtbot.mouseClick(dlg.estop_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("estop_clear_async")[-1].kwargs == {"confirmed": True}
    qtbot.waitUntil(lambda: "ENABLE the driver and HOME" in dlg.after_label.text(), timeout=2000)
    connected_fake.results["fault_clear"] = ClearResult("FAULT_CLEAR", True, True, "REFUSED", text="LOAD_LIMIT")
    qtbot.mouseClick(dlg.fault_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: dlg.fault_result.text() == "refused: LOAD_LIMIT", timeout=2000)
    dlg.close()


# --------------------------------------------------------------------------------------------- hotkey

@pytest.mark.req("SW-STOP-002")
def test_app_shortcut_only_when_hotkey_unavailable(window, connected_fake) -> None:
    """Verifies: SW-STOP-002 — REGISTERED → no app QShortcut; UNAVAILABLE → Pause / Ctrl+Break shortcuts calling
    backend.halt("app-shortcut"); back to REGISTERED → removed (never a double path, GQ-19)."""
    assert window.app_shortcuts() == []
    connected_fake.set_status(hotkey=HotkeyStatus("UNAVAILABLE", "RegisterHotKey failed"))
    tick(window)
    scs = window.app_shortcuts()
    assert len(scs) == 2 and all(s.context() == Qt.ShortcutContext.ApplicationShortcut for s in scs)
    connected_fake.calls.clear()
    scs[0].activated.emit()
    assert connected_fake.call_names() == ["halt"] and connected_fake.calls[0].args == ("app-shortcut",)
    assert window.indicator_bar.chip("KEY").level == "alarm"
    connected_fake.set_status(hotkey=HotkeyStatus("LL_HOOK", "fallback hook"))
    tick(window)
    assert window.app_shortcuts() == []


# --------------------------------------------------------------------------------------------- banners / notices

@pytest.mark.req("IF-008", "SW-CFG-003", "SW-CFG-004")
def test_notice_strip(window, connected_fake, qtbot) -> None:
    """Verifies: IF-008, SW-CFG-003/004 — read-only, NVM-defaulted and reboot-pending notices with their buttons."""
    tick(window)
    assert window.notice_strip.keys() == []
    st = connected_fake.status()
    connected_fake.set_status(link=LinkStatus(st.link.state, "", "sim", Compat.MAJOR_MISMATCH, st.link.info),
                              nvm_defaulted=True, reboot_pending=True)
    tick(window)
    assert window.notice_strip.keys() == ["compat", "nvm_defaulted", "reboot_pending"]
    assert window.indicator_bar.chip("RO").isVisibleTo(window.indicator_bar)
    window._on_notice_action("save_reboot")
    assert window.connection_tab.confirm_dialog.cid == "C-11"
    window.connection_tab.confirm_dialog.reject()


@pytest.mark.req("SW-LIM-004")
def test_mode_banner_on_every_tab(window, connected_fake, qtbot) -> None:
    """Verifies: SW-LIM-004 — the no-specimen banner belongs to the main window (every tab), NOSPEC chip and dock
    tags follow; [Leave] → set_no_specimen_mode(False)."""
    connected_fake.limits.set_no_specimen_mode(True, confirmed=True)
    tick(window)
    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        assert window.mode_banner.isVisible()
    assert window.plot_dock.title_bar.nospec_tag.isVisibleTo(window.plot_dock)
    assert window.indicator_bar.chip("NOSPEC").isVisibleTo(window.indicator_bar)
    qtbot.mouseClick(window.mode_banner.leave_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("limits.set_no_specimen_mode")[-1].args == (False,)
    tick(window)
    assert not window.mode_banner.isVisible()


@pytest.mark.req("SAF-SW-005")
def test_banner_severity_order(window, connected_fake) -> None:
    """Verifies: SAF-SW-005 — ESTOP outranks HALT outranks PAUSED; "+n more"."""
    connected_fake.set_indicators(estop=ind("ON"), halt=ind("ON", "PC"), paused=ind("ON", "PC"))
    tick(window)
    assert window.stop_banner.top_text().startswith("E-STOP active")
    assert window.stop_banner.more_button.text() == "+2 more"


@pytest.mark.req("SAF-SW-005")
def test_event_log_and_status_help(window, connected_fake, qtbot) -> None:
    """Verifies: SAF-SW-005 — every backend event becomes an Event-log row; chip click opens the help dialog."""
    connected_fake.events.emit("log", EventRecord("x", 0, None))
    qtbot.waitUntil(lambda: any("log" in r.code for r in window.event_log.rows), timeout=2000)
    window.indicator_bar.chipClicked.emit("ESTOP")
    assert window.dialogs["help"].isVisible()


# --------------------------------------------------------------------------------------------- refresh / close

@pytest.mark.req("NFR-001", "SAF-SW-005")
def test_refresh_timer_precise_33ms_and_gui_beat(window, connected_fake) -> None:
    """Verifies: NFR-001 — one PreciseTimer at 33 ms; gui_beat() on every tick; perf counters exposed."""
    assert window.refresh.timer_type() == Qt.TimerType.PreciseTimer and window.refresh.interval_ms == 33
    b0 = connected_fake.beats
    tick(window, 5)
    assert connected_fake.beats == b0 + 5
    ps = window.perf_stats()
    assert ps["ticks"] >= 5 and "status_p95_ms" in ps and "plots_p95_ms" in ps


@pytest.mark.req("SW-STOP-001")
def test_close_shuts_backend_down_and_restores_handler(make_window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-001 — close → backend.shutdown(), STOP handler restored, no DISABLE sent (GQ-14)."""
    from bend_stand.gui import stop as stop_mod
    prev = stop_mod.stop_handler()
    win = make_window(connected_fake)
    assert stop_mod.stop_handler() is not prev
    win.close()
    assert connected_fake.shutdowns == 1 and stop_mod.stop_handler() is prev
    assert "motion.disable" not in connected_fake.call_names()


@pytest.mark.req("SW-STOP-001", "SAF-SW-004")
def test_close_while_recording_needs_c09(make_window, connected_fake, qtbot) -> None:
    """Verifies: SAF-SW-004 — closing while recording asks C-09; cancel keeps the window."""
    win = make_window(connected_fake)
    connected_fake.set_status(recording=RecordingStatus("RECORDING", "C:/x", 1))
    tick(win)
    win.close()
    assert win.isVisible() and win.confirm_dialog.cid == "C-09"
    win.confirm_dialog.reject()
    assert win.isVisible() and connected_fake.shutdowns == 0
    win.close()
    qtbot.mouseClick(win.confirm_dialog.confirm_button, Qt.MouseButton.LeftButton)
    assert not win.isVisible() and connected_fake.shutdowns == 1


@pytest.mark.req("SW-PLT-003")
def test_link_widget(window, connected_fake, qtbot) -> None:
    """Verifies: SW-PLT-003 — link widget shows state + endpoint + rate; Disconnect delegates to the tab."""
    connected_fake.stream_start_async()
    tick(window)
    assert window.link_text.text() == "CONNECTED sim 80.0 Hz" and window.link_led.color == "green"
    assert window.link_button.text() == "Disconnect"
    window.link_button.click()
    assert "disconnect_async" in connected_fake.call_names()


# --------------------------------------------------------------------------------------------- M1 gate fixes

@pytest.mark.req("SW-STOP-002", "SAF-SW-005")
@pytest.mark.parametrize("shape", ["str", "object"])
@pytest.mark.parametrize("cmd", ["HALT", "PAUSE", "STOP"])
def test_confirmation_banner_names_the_command(window, connected_fake, qtbot, shape, cmd) -> None:
    """Verifies: SW-STOP-002, SAF-SW-005 (SWD-M1-06) — stop.confirmed / stop.unconfirmed name the command actually
    sent, whether the payload is the bare command name (M1 backend) or an object with ``.cmd``."""
    from bend_stand.core.api import StopConfirmation      # B4-06: no str equality any more

    def payload():
        return cmd if shape == "str" else StopConfirmation(cmd, "app-shortcut", 3, 0, True)

    if cmd == "HALT":
        window._on_app_halt()
    else:
        connected_fake.events.emit("stop.issued", StopResult(cmd, "toolbar", True))
    connected_fake.events.emit("stop.confirmed", payload())
    qtbot.waitUntil(lambda: window.stop_banner.recent.state == "confirmed", timeout=2000)
    assert window.stop_banner.recent.cmd == cmd
    connected_fake.events.emit("stop.unconfirmed", payload())
    qtbot.waitUntil(lambda: window.stop_banner.recent.state == "unconfirmed", timeout=2000)
    assert window.stop_banner.recent.cmd == cmd
    assert window.stop_banner.top_text().startswith(f"{cmd} NOT CONFIRMED")


@pytest.mark.req("SAF-SW-003", "SAF-SW-005")
def test_link_lost_says_stop_sent_only_after_a_sent_stop(window, connected_fake, qtbot) -> None:
    """Verifies: SAF-SW-003, SAF-SW-005 (SWD-M1-10) — "LINK LOST – STOP sent" only when a STOP was actually sent;
    otherwise "LINK LOST" without the claim; a STOP that was not sent never counts."""
    from bend_stand.core.api import LinkState
    st = connected_fake.status()
    connected_fake.set_status(link=LinkStatus(LinkState.LOST, "timeout", "sim", st.link.compat, st.link.info))
    tick(window)
    texts = [r.text for r in window.stop_banner.rows]
    assert any(t.startswith("LINK LOST") for t in texts) and not any("STOP sent" in t for t in texts)
    connected_fake.events.emit("stop.issued", StopResult("STOP", "link-loss", False, reason="link lost"))
    qtbot.waitUntil(lambda: window.stop_banner.recent is not None, timeout=2000)
    tick(window)
    assert not any("LINK LOST – STOP sent" in r.text for r in window.stop_banner.rows)
    connected_fake.events.emit("stop.issued", StopResult("STOP", "link-loss", True))
    qtbot.waitUntil(lambda: window.stop_banner.recent.sent, timeout=2000)
    tick(window)
    assert any(r.text.startswith("LINK LOST – STOP sent") for r in window.stop_banner.rows)


@pytest.mark.req("SAF-SW-001", "SAF-SW-005")
def test_m2_gate_warning_and_feature_bits(window, connected_fake) -> None:
    """Verifies: SAF-SW-001, SAF-SW-005 (B4-02, B4-05) — the motion gates' WARN PC_LOAD_LIMITS_OFF is shown as a
    notice (hidden in no-specimen mode); feature-dependent items UNKNOWN → grey "?" chips and no "driver power
    lost" banner; MOV shows homing phase / jogging (B4-03)."""
    import dataclasses

    from bend_stand.core.api import MotionStatus
    connected_fake.set_gate(GateId.MOVE, warn("PC_LOAD_LIMITS_OFF", "PC load limits not active until M3"))
    tick(window)
    assert "pc_load_limits_off" in window.notice_strip.keys()
    connected_fake.set_indicators(drv_pwr=ind("UNKNOWN"), alm=ind("UNKNOWN"), pend=ind("UNKNOWN"),
                                  pause_btn=ind("UNKNOWN"), moving=ind("ON"))
    connected_fake.set_status(motion=MotionStatus(moving=True, home_phase="FAST_SEEK"))
    tick(window)
    for chip in ("DRV", "ALM", "PEND", "BTN"):
        assert window.indicator_bar.chip(chip).level == "unknown", chip
    assert not any("Driver power lost" in r.text for r in window.stop_banner.rows)
    assert window.indicator_bar.chip("MOV").value.text() == "homing FAST_SEEK"
    connected_fake.set_status(motion=dataclasses.replace(connected_fake.status().motion, home_phase="DONE",
                                                         jogging=True))
    tick(window)
    assert window.indicator_bar.chip("MOV").value.text() == "jogging"
    connected_fake.limits.set_no_specimen_mode(True, confirmed=True)
    tick(window)
    assert "pc_load_limits_off" not in window.notice_strip.keys()


@pytest.mark.req("SW-STOP-001", "SAF-SW-005")
def test_gui_texts_name_no_physical_stop_button(window, connected_fake) -> None:
    """Verifies: SW-STOP-001, SAF-SW-005 (D-36 / CR-01) — GUI-owned operator texts point to the red E-stop, never to
    a physical STOP/BREAK button (the backend's clear hints are shown verbatim and tracked as GF-19 for B)."""
    from bend_stand.gui import indicator_map as imap
    from bend_stand.gui.dialogs.confirm_dialog import TEXTS
    from bend_stand.gui.widgets.stop_banner import E_STOP_HINT, link_lost_text
    from bend_stand.gui.widgets.stop_button import STOP_TOOLTIP
    texts = [STOP_TOOLTIP, imap.KL01_TEXT, E_STOP_HINT, link_lost_text(None, 0.0)]
    texts += [t for v in TEXTS.values() for t in v if t]
    from bend_stand.core.api import BackendStatus, Indicators
    from bend_stand.gui.widgets.stop_banner import banner_rows
    st = BackendStatus(indicators=Indicators().replace(estop=ind("ON"), k1_welded=ind("ON")))
    texts += [r.text for r in banner_rows(st, None, 0.0)]
    for t in texts:
        import re
        low = t.lower()
        assert "stop/break" not in low and "physical stop" not in low, t
        # D-41: no power-removal contactor — the E-stop is an MCU / FW stop
        assert "power cut" not in low and "power removed" not in low and "k1 reset" not in low, t
        assert "contactor" not in low, t
        assert not re.search(r"(?<!e-)stop button", low), t          # "E-stop button" is the D-36 path
