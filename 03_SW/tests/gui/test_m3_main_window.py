"""M3 additions to the main window: View ▸ Units N / kgf (G-25), readouts with force and EXTRAPOLATED (G-24 M3),
X-Y pane with force once calibrated (SW-RT-003), K1 chip hidden while ``drv.k1_check_enable`` is false (M2 gate
condition), Pause/Break key test dialog (G-42), STOP in every M3 window (G-02 M3), last tab restored (GQ-01).

Verifies: SYS-003, SW-RT-003, SW-RT-005, SW-CAL-008, SAF-SW-005, SW-STOP-001, SW-STOP-002, NFR-003, SW-RT-001
"""
from __future__ import annotations

import dataclasses

import pytest
from fakes import refuse, tick
from PySide6.QtCore import Qt

from bend_stand.core.api import GateId, HotkeyStatus
from bend_stand.gui.indicator_map import KL01_TEXT
from bend_stand.gui.widgets.stop_button import StopButton


def visible_stops(w):
    return [b for b in w.findChildren(StopButton) if b.isVisibleTo(w)]


# --------------------------------------------------------------------------------------------- units

@pytest.mark.req("SYS-003", "SW-RT-005", "SW-CAL-008")
def test_units_menu_switches_readouts(window, connected_fake) -> None:
    """Verifies: SYS-003, SW-RT-005, SW-CAL-008 — View ▸ Units kgf: the force readout reads F_kgf (backend
    channel) with its state (EXTRAPOLATED shown); N switches back; the choice goes to the session."""
    specs = connected_fake.channels.specs
    for i, s in enumerate(specs):
        if s.key in ("F_N", "F_kgf"):
            specs[i] = dataclasses.replace(s, available=True, reason=None)
    connected_fake.events.emit("channels.changed")
    tick(window)
    connected_fake.data.latest_values["F_N"] = (400.0, "EXTRAPOLATED")
    connected_fake.data.latest_values["F_kgf"] = (40.789, "EXTRAPOLATED")
    window._reload_channels()                                     # noqa: SLF001 - as on channels.changed
    window.readout_dock.show()
    window.readout_dock.refresh(connected_fake.data, {"F_N", "F_kgf", "raw"})
    assert window.readout_dock.value_text("F_N") == "400.00" and window.readout_dock.state_of("F_N") == "EXTRAPOLATED"
    window.unit_actions["kgf"].trigger()
    assert window.unit_actions["kgf"].isChecked() and connected_fake.session.get().display_unit == "kgf"
    window.readout_dock.refresh(connected_fake.data, {"F_N", "F_kgf", "raw"})
    assert "F_kgf" in window.readout_dock.keys() and window.readout_dock.value_text("F_kgf") == "40.789"
    window.unit_actions["N"].trigger()
    assert "F_N" in window.readout_dock.keys()


@pytest.mark.req("SW-RT-003", "SYS-003")
def test_xy_pane_uses_force_when_calibrated(window, connected_fake) -> None:
    """Verifies: SW-RT-003, SYS-003 — the X-Y pane plots raw counts until a force channel is available, then force
    in the display unit (kgf after View ▸ Units), unless the operator picked y himself."""
    dock = window.plot_dock
    pane = dock.add_xy_pane()
    assert pane.y_key == "raw"
    specs = connected_fake.channels.specs
    for i, s in enumerate(specs):
        if s.key in ("F_N", "F_kgf"):
            specs[i] = dataclasses.replace(s, available=True, reason=None)
    window._reload_channels()                                     # noqa: SLF001
    assert pane.y_key == "F_N"
    window.set_force_unit("kgf")
    assert pane.y_key == "F_kgf"
    window.set_force_unit("N")


# --------------------------------------------------------------------------------------------- K1 chip

@pytest.mark.req("SAF-SW-005")
def test_k1_chip_hidden_while_check_disabled(window, connected_fake) -> None:
    """Verifies: SAF-SW-005 (M2 gate condition, cosmetic) — drv.k1_check_enable false → K1 chip hidden; true →
    shown with the K1_WELDED state; an active K1_WELDED is never hidden."""
    from fakes import ind
    chip = window.indicator_bar.chip("K1")
    connected_fake.config.board["drv.k1_check_enable"] = False
    window._status_n = 0                                          # noqa: SLF001 - re-read the board values now
    tick(window)
    assert not chip.isVisibleTo(window.indicator_bar)
    connected_fake.set_indicators(k1_welded=ind("ON"))
    tick(window)
    assert chip.isVisibleTo(window.indicator_bar) and chip.level == "alarm"
    connected_fake.set_indicators(k1_welded=ind("OFF"))
    connected_fake.config.board["drv.k1_check_enable"] = True
    window._status_n = 0                                          # noqa: SLF001
    tick(window)
    assert chip.isVisibleTo(window.indicator_bar) and chip.level == "ok"


# --------------------------------------------------------------------------------------------- hotkey test (G-42)

@pytest.mark.req("SW-STOP-002", "NFR-003")
def test_hotkey_test_dialog(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-002, NFR-003 (G-42) — Tools ▸ Test Pause/Break key opens a non-modal dialog with STOP and
    the KL-01 text; [Start] → hotkey_test_start(10); the hotkey.test delay (or timeout) is displayed; a REFUSE of
    the hotkey_test gate disables Start."""
    window.hotkey_test_action.trigger()
    dlg = window.dialogs["hotkey"]
    qtbot.waitExposed(dlg)
    assert not dlg.isModal() and len(visible_stops(dlg)) == 1 and KL01_TEXT in dlg.findChild(type(dlg.result),
                                                                                              "kl01").text()
    assert "REGISTERED" in dlg.mode_label.text()
    qtbot.mouseClick(dlg.start_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls[-1].name == "hotkey_test_start" and connected_fake.calls[-1].args == (10.0,)
    assert dlg.running and "Press Pause/Break now" in dlg.prompt.text()
    connected_fake.events.emit("hotkey.test", 3.14)
    qtbot.waitUntil(lambda: "3.1 ms" in dlg.result.text(), timeout=2000)
    qtbot.mouseClick(dlg.start_button, Qt.MouseButton.LeftButton)
    connected_fake.events.emit("hotkey.test", None)
    qtbot.waitUntil(lambda: "no key press" in dlg.result.text(), timeout=2000)
    connected_fake.set_gate(GateId.HOTKEY_TEST, refuse("MOTION_ACTIVE", "axis moving"))
    connected_fake.set_status(hotkey=HotkeyStatus("LL_HOOK", "fallback hook", False))
    tick(window)
    assert not dlg.start_button.isEnabled() and "axis moving" in dlg.start_button.toolTip()
    assert "LL_HOOK" in dlg.mode_label.text()
    connected_fake.results["hotkey_test_start"] = refuse("HOTKEY_UNAVAILABLE", "no global key")
    dlg.start_test()
    assert "no global key" in dlg.result.text()
    qtbot.mousePress(visible_stops(dlg)[0], Qt.MouseButton.LeftButton)
    assert connected_fake.calls[-1].name == "stop"


# --------------------------------------------------------------------------------------------- G-02 M3

@pytest.mark.req("SW-STOP-001")
def test_stop_in_every_m3_window(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-001 (G-02 M3) — the toolbar STOP is visible on every tab (the Manual tab adds its own);
    both wizards, the tare popup, the hotkey test and every new confirmation carry exactly one STOP; each press
    reaches backend.stop on the press."""
    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        assert window.stop_button.isVisible()
    hosts = [window.open_travel_wizard(), window.open_load_wizard(), window.open_hotkey_test()]
    window.on_tare(None)
    hosts.append(window.tare_popup)
    window.tabs.setCurrentWidget(window.manual_tab)
    window.manual_tab.on_disable()                      # gate without CONFIRM: no dialog
    window.limits_tab.enter_no_specimen()
    hosts.append(window.limits_tab.confirm_dialog)
    for h in hosts:
        qtbot.waitExposed(h)
        stops = visible_stops(h)
        assert len(stops) == 1, h.objectName()
        b = stops[0]
        assert b.focusPolicy() == Qt.FocusPolicy.NoFocus and not b.isDefault() and not b.autoDefault()
        connected_fake.calls.clear()
        qtbot.mousePress(b, Qt.MouseButton.LeftButton)
        assert connected_fake.call_names()[:1] == ["stop"], h.objectName()


@pytest.mark.req("SW-STOP-001")
def test_wizards_non_modal_toolbar_usable(window, connected_fake, qtbot) -> None:
    """Verifies: SW-STOP-001, SW-CAL-001 (GQ-10) — wizards are non-modal: the toolbar STOP and the tabs stay
    usable while one is open; reopening raises the same window."""
    w = window.open_travel_wizard()
    assert not w.isModal() and w.windowModality() == Qt.WindowModality.NonModal
    assert window.open_travel_wizard() is w
    connected_fake.calls.clear()
    qtbot.mousePress(window.stop_button, Qt.MouseButton.LeftButton)
    assert connected_fake.call_names()[0] == "stop"


@pytest.mark.req("SW-RT-001")
def test_last_tab_and_unit_restored(make_window, connected_fake, qtbot, tmp_path) -> None:
    """Verifies: SW-RT-001 (GQ-01) — the last active tab and the display unit are restored by a new window."""
    from bend_stand.gui.main_window import MainWindow
    from bend_stand.gui.settings import make_settings
    path = tmp_path / "restore.ini"
    w1 = MainWindow(connected_fake, settings=make_settings(path), start_refresh=False)
    qtbot.addWidget(w1)
    w1.tabs.setCurrentWidget(w1.manual_tab)
    w1.set_force_unit("kgf")
    w1._force_close = True                                         # noqa: SLF001
    w1.close()
    w2 = MainWindow(connected_fake, settings=make_settings(path), start_refresh=False)
    qtbot.addWidget(w2)
    assert w2.tabs.currentWidget() is w2.manual_tab and w2.unit_actions["kgf"].isChecked()
    w2.set_force_unit("N")
    w2._force_close = True                                         # noqa: SLF001
    w2.close()


# --------------------------------------------------------------------------------------------- session / events

@pytest.mark.req("SW-LIM-003")
def test_session_open_save(window, connected_fake, qtbot, tmp_path) -> None:
    """Verifies: SW-LIM-003 — File ▸ Save session as… / Open session… go through SafeFileDialog (STOP inside;
    hook in tests) to session.save / session.load; the limits tab and the display unit follow the loaded session."""
    from bend_stand.gui.dialogs import safe_dialog
    path = str(tmp_path / "a.bbsession.json")
    safe_dialog.FILE_DIALOG_HOOK[0] = lambda kind, caption, flt: path
    saved = []
    connected_fake.session.save = lambda p: saved.append(p)
    window.save_session()
    assert saved == [path] and "Session saved" in window.toast_label.text()
    s = dataclasses.replace(connected_fake.session.get(), display_unit="kgf")
    connected_fake.session.load = lambda p: (connected_fake.session.set(s), s)[1]
    window.open_session()
    assert window.unit_actions["kgf"].isChecked() and "Session loaded" in window.toast_label.text()


@pytest.mark.req("SW-ACQ-003", "SW-LIM-004", "SAF-SW-001")
def test_event_toasts_sample_mode_trip(window, connected_fake, qtbot) -> None:
    """Verifies: SW-ACQ-003 (sample.taken SampleRow toast), SW-LIM-004 (safety.no_specimen bool payload, B5-02),
    SAF-SW-001 (safety.trip text + banner row from SafetyStatus.trip)."""
    from types import SimpleNamespace

    from bend_stand.core.api import TestMarks
    row = SimpleNamespace(utc="t", t_dev_s=1.0, window_s=1.0, n=80, f_mean=123.41, f_std=0.05, f_n=80, x_mean=12.3,
                          x_std=0.0, raw_mean=239512.0, raw_std=3.0, raw_n=80, marks=TestMarks(), file="samples.csv")
    connected_fake.events.emit("sample.taken", row)
    qtbot.waitUntil(lambda: "F̄ = 123.41 N" in window.toast_label.text(), timeout=2000)
    assert "samples.csv" in window.toast_label.text()
    connected_fake.events.emit("safety.no_specimen", False)
    qtbot.waitUntil(lambda: "No-specimen mode ended" in window.toast_label.text(), timeout=2000)
    trip = SimpleNamespace(limit="PULL", text="F 1 812.4 N > pull max 1 765.2 N")
    connected_fake.set_status(safety=dataclasses.replace(connected_fake.status().safety, sw_trip="PULL", trip=trip))
    tick(window)
    assert any("1 812.4 N > pull max" in r.text for r in window.stop_banner.rows)
    connected_fake.events.emit("safety.trip", trip)
    qtbot.waitUntil(lambda: "SW limit trip" in window.toast_label.text(), timeout=2000)


@pytest.mark.req("SW-REP-004")
def test_bend3p_geometry_to_session(window, connected_fake, qtbot) -> None:
    """Verifies: SW-REP-004 — the Test-marks 3-point-bend group writes SessionSettings.bend3p (B5-11 / B5-19)."""
    tab = window.marks_tab
    assert tab.bend_apply.isEnabled()
    tab.bend_en.setChecked(True)
    tab.bend_spins["span_mm"].setValue(80.0)
    assert tab.apply_bend3p()
    assert connected_fake.session.get().bend3p.span_mm == 80.0
    tab.bend_en.setChecked(False)
    assert tab.apply_bend3p() and connected_fake.session.get().bend3p is None


@pytest.mark.req("SAF-SW-003")
def test_link_lost_diagnostics_logged(window, connected_fake, caplog) -> None:
    """Verifies: SAF-SW-003 (MC3-4 diagnostics) — every LINK LOST shown logs the longest GUI refresh-tick gap and the
    last / longest GC collection (once per transition)."""
    import gc
    import logging

    from bend_stand.core.api import LinkState, LinkStatus
    gc.collect(0)                                                  # at least one traced collection
    tick(window, 3)
    caplog.set_level(logging.WARNING, logger="bend_stand.gui.main_window")
    connected_fake.set_status(link=LinkStatus(LinkState.LOST, "no DATA for 600 ms", "sim"))
    tick(window)
    tick(window)
    assert len(window.link_lost_diagnostics) == 1
    text = window.link_lost_diagnostics[0]
    assert "longest tick gap" in text and "last GC: gen" in text and "no DATA for 600 ms" in text
    assert any("LINK LOST shown" in r.getMessage() for r in caplog.records)
    connected_fake.set_status(link=LinkStatus(LinkState.CONNECTED, "", "sim"))
    tick(window)
    connected_fake.set_status(link=LinkStatus(LinkState.LOST, "again", "sim"))
    tick(window)
    assert len(window.link_lost_diagnostics) == 2
