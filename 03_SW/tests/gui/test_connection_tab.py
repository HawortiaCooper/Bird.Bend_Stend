"""Connection & Config tab (WP-D4, WP-D5): G-18 (compat / read-only / link counters), G-17 (dictionary form,
locked session rows, write statuses, file round trip, C-04), G-41 (live rule check, REBOOT_REQUIRED, C-11,
CFG states), connect part of G-36 against the fake.

Verifies: SW-PLT-003, IF-008, SW-CFG-001, SW-CFG-002, SW-CFG-003, SW-CFG-004, FW-CFG-004
"""
from __future__ import annotations

import json

import pytest
from fakes import refuse, tick
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox

from bend_stand.core import params_gen
from bend_stand.core.api import (
    BoardConfigFile, Compat, GateId, Issue, IssueSeverity, LinkStats, LinkStatus, NotConnected, WriteStatus,
)
from bend_stand.gui.dialogs import safe_dialog
from bend_stand.gui.dialogs.file_report import FileReportDialog, report_lines
from bend_stand.gui.widgets.param_form import LOCKED_TEXT, U32Edit

LOCKED = {"safety.load_raw_min", "safety.load_raw_max", "safety.zero_raw"}


@pytest.fixture
def tab(window):
    return window.connection_tab


def wait_rules(qtbot, tab) -> None:
    qtbot.waitUntil(lambda: not tab._rule_timer.isActive(), timeout=2000)


# --------------------------------------------------------------------------------------------- connection

@pytest.mark.req("SW-PLT-003")
def test_endpoints_listed_without_connecting(make_window, fake) -> None:
    """Verifies: SW-PLT-003, D-06 — endpoints from backend.endpoints(); nothing connects until the click."""
    fake.start()
    win = make_window(fake)
    tab = win.connection_tab
    assert tab.selector.endpoints() == ["sim", "tcp://127.0.0.1:5760"]
    assert "connect_async" not in fake.call_names()
    tick(win)
    assert tab.connect_button.isEnabled() and not tab.disconnect_button.isEnabled()
    tab.selector.set_endpoint("tcp://127.0.0.1:5760")
    tab.connect_button.click()
    assert fake.calls_of("connect_async")[-1].args == ("tcp://127.0.0.1:5760",)


@pytest.mark.req("SW-PLT-003", "FW-CFG-004")
def test_device_and_link_lines(window, connected_fake, tab) -> None:
    """Verifies: SW-PLT-003, FW-CFG-004 — device line (FW, hash ✓, generated feature names, AFE_SYNTHETIC note),
    link counters incl. dup / seq anomalies (B3-10)."""
    t = tab.device_label.text()
    assert "FW 0.1.0" in t and f"0x{params_gen.PARAM_DICT_HASH:08X} ✓" in t
    assert "AFE_SYNTHETIC (M1 placeholder samples)" in t and "NVM" in t
    assert tab.version_label.text() == "Version check: ✓ compatible"
    st = connected_fake.status()
    connected_fake.set_status(link=LinkStatus(st.link.state, "", "sim", Compat.OK, st.link.info,
                                              LinkStats(frames_ok=500, dup_frames=3, seq_anomalies=2, crc_errors=1)))
    tab.update_link_line(connected_fake.status())
    assert "frames 500" in tab.link_label.text() and "dup 3" in tab.link_label.text()
    assert "seq anomalies 2" in tab.link_label.text() and "CRC 1" in tab.link_label.text()


@pytest.mark.req("SW-PLT-003")
def test_connect_error_shown(make_window, fake, qtbot) -> None:
    """Verifies: SW-PLT-003 — a failing connect shows the backend's user_text."""
    fake.start()
    fake.connect_error = NotConnected("port busy")
    win = make_window(fake)
    win.connection_tab.on_connect()
    qtbot.waitUntil(lambda: "Connect failed: port busy" in win.toast_label.text(), timeout=2000)


@pytest.mark.req("IF-008")
@pytest.mark.parametrize("compat, text", [
    (Compat.MAJOR_MISMATCH, "READ-ONLY, motion disabled"),
    (Compat.PAYLOAD_MISMATCH, "READ-ONLY, motion disabled"),
    (Compat.PARAM_HASH_MISMATCH, "configuration read-only"),
])
def test_compat_read_only(make_window, fake, compat, text) -> None:
    """Verifies: IF-008 (G-18) — major/payload mismatch → read-only state; hash mismatch → configuration
    read-only (form disabled, write and NVM buttons disabled, notice)."""
    fake.start()
    fake.compat = compat
    fake.connect_async("sim")
    win = make_window(fake)
    tick(win)
    tab = win.connection_tab
    assert text in tab.version_label.text()
    assert tab.form.read_only and tab.form.banner.isVisibleTo(tab.form)
    assert not tab.save_nvm_button.isEnabled() and not tab.defaults_button.isEnabled()
    tab.form.set_read_only(False)            # even if edited programmatically, the gate keeps writes off
    tick(win)
    assert tab.form.read_only
    assert win.notice_strip.keys()[0] in ("compat", "config_ro")
    if compat.read_only:
        assert win.indicator_bar.chip("RO").level == "alarm"


# --------------------------------------------------------------------------------------------- form (G-17)

@pytest.mark.req("SW-CFG-001")
def test_every_parameter_shown_with_typed_editor(window, connected_fake, tab) -> None:
    """Verifies: SW-CFG-001 (G-17) — every dictionary parameter has a row with the editor of its type, unit,
    range, default, flags; board values equal the backend's values()."""
    form = tab.form
    assert set(form.keys()) == {p.key for p in params_gen.PARAMS}
    kinds = {"BOOL": QCheckBox, "ENUM": QComboBox, "F32": QDoubleSpinBox, "U32": U32Edit}
    for p in params_gen.PARAMS:
        ed = form.editor_for(p.key)
        if p.key in LOCKED:
            assert isinstance(ed, QLineEdit) and not ed.isEnabled()
            continue
        assert isinstance(ed, kinds.get(p.type.name, QSpinBox)), (p.key, type(ed))
        assert form.board_value(p.key) == connected_fake.config.board[p.key] or p.type.name == "F32"
        item = form.item_for(p.key)
        flags = item.text(7).split()
        assert ("R" in flags) == p.reboot_required and ("N" in flags) == p.nvm and ("M" in flags) == p.moving_ok
        if p.type.name == "ENUM":
            assert [ed.itemText(i) for i in range(ed.count())] == list(p.enum.values())
    assert form.item_for("motion.steps_per_mm").text(3) == "800.000" or "800" in form.item_for(
        "motion.steps_per_mm").text(3)


@pytest.mark.req("SW-CFG-003")
def test_locked_session_rows(window, tab) -> None:
    """Verifies: SW-CFG-003 — session parameters are read-only rows, never edited, written or saved."""
    form = tab.form
    assert form.locked_keys() == LOCKED
    for k in LOCKED:
        assert form.status_text(k) == LOCKED_TEXT and not form.is_editable(k)
        with pytest.raises(PermissionError):
            form.set_edit_value(k, 1)
        assert k not in form.file_values() and k not in form.edited_config()


@pytest.mark.req("SW-CFG-003")
@pytest.mark.parametrize("status", [WriteStatus.OK, WriteStatus.REJECTED, WriteStatus.MISMATCH, WriteStatus.BUSY,
                                    WriteStatus.TIMEOUT, WriteStatus.NOT_ATTEMPTED])
def test_write_and_verify_statuses(window, connected_fake, tab, qtbot, status) -> None:
    """Verifies: SW-CFG-003 (G-17) — per-parameter status after Write & verify is shown in the row."""
    connected_fake.config.script["afe.settle_discard"] = status
    tab.form.set_edit_value("afe.settle_discard", 5)
    wait_rules(qtbot, tab)
    tick(window)
    assert tab.write_button.isEnabled()
    tab.write_button.click()
    assert connected_fake.calls_of("config.write_and_verify_async")[-1].args == ({"afe.settle_discard": 5},)
    qtbot.waitUntil(lambda: tab.form.status_of("afe.settle_discard") == status.value, timeout=2000)
    assert status.value in tab.form.status_text("afe.settle_discard")
    assert "Write & verify" in window.toast_label.text()


@pytest.mark.req("SW-CFG-003")
def test_rule_check_live_disables_write(window, connected_fake, tab, qtbot) -> None:
    """Verifies: SW-CFG-003 (G-41) — config.check runs debounced on edits; an ERROR marks the row and disables
    Write & verify; fixing the value re-enables it."""
    travel = connected_fake.config.board["motion.v_max_travel_um_s"]
    tab.form.set_edit_value("motion.v_max_load_um_s", travel + 1000)
    assert tab._rule_timer.isActive()
    wait_rules(qtbot, tab)
    tick(window)
    assert "H4" in tab.rule_label.text() and not tab.write_button.isEnabled()
    assert "H4" in tab.form.status_text("motion.v_max_load_um_s")
    assert connected_fake.calls_of("config.check")[-1].args[0] == {"motion.v_max_load_um_s": travel + 1000}
    tab.form.set_edit_value("motion.v_max_load_um_s", travel - 1000)
    wait_rules(qtbot, tab)
    tick(window)
    assert tab.rule_label.text() == "Rule check: ✓" and tab.write_button.isEnabled()


@pytest.mark.req("SW-CFG-003")
def test_reboot_required_and_save_reboot(window, connected_fake, tab, qtbot) -> None:
    """Verifies: SW-CFG-003 (G-41) — a reboot-required key → REBOOT_REQUIRED status, R flag, reboot-pending notice;
    Save & reboot needs C-11 and calls save_async then reboot_async."""
    key = "motion.pul_invert"
    tab.form.show_advanced.setChecked(True)
    tab.form.set_edit_value(key, True)
    wait_rules(qtbot, tab)
    tick(window)
    tab.write_button.click()
    qtbot.waitUntil(lambda: tab.form.status_of(key) == "REBOOT_REQUIRED", timeout=2000)
    assert "REBOOT_REQUIRED" in tab.form.status_text(key) and "R" in tab.form.item_for(key).text(7).split()
    tick(window)
    assert "reboot_pending" in window.notice_strip.keys() and tab.reboot_button.isEnabled()
    assert "reboot pending" in tab.cfg_label.text()
    tab.reboot_button.click()
    dlg = tab.confirm_dialog
    assert dlg.cid == "C-11"
    qtbot.keyClick(dlg, Qt.Key.Key_Return)
    assert "config.save_async" not in connected_fake.call_names()
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "config.reboot_async" in connected_fake.call_names(), timeout=2000)
    names = connected_fake.call_names()
    assert names.index("config.save_async") < names.index("config.reboot_async")


@pytest.mark.req("SW-CFG-004")
def test_nvm_buttons_and_restore_defaults_confirmation(window, connected_fake, tab, qtbot) -> None:
    """Verifies: SW-CFG-004 (G-17) — Save to NVM / Reload from NVM call the backend; Restore defaults only after C-04
    (Esc / keyboard cancel, mouse confirms); CFG line follows CFG_DIRTY."""
    tab.save_nvm_button.click()
    assert "config.save_async" in connected_fake.call_names()
    tick(window)
    assert tab.cfg_label.text() == "CFG: ● clean (RAM = NVM)"
    tab.load_nvm_button.click()
    assert "config.load_async" in connected_fake.call_names()
    tab.defaults_button.click()
    dlg = tab.confirm_dialog
    assert dlg.cid == "C-04"
    qtbot.keyClick(dlg, Qt.Key.Key_Escape)
    assert "config.defaults_async" not in connected_fake.call_names()
    tab.defaults_button.click()
    qtbot.mouseClick(tab.confirm_dialog.confirm_button, Qt.MouseButton.LeftButton)
    assert "config.defaults_async" in connected_fake.call_names()
    tick(window)
    assert "dirty" in tab.cfg_label.text()


@pytest.mark.req("SW-CFG-003", "SW-CFG-004")
def test_config_write_gate_refuse(window, connected_fake, tab) -> None:
    """Verifies: SW-CFG-003 — gate config_write REFUSE disables writes / NVM with the backend text."""
    connected_fake.set_gate(GateId.CONFIG_WRITE, refuse("LINK_DOWN", "not connected"))
    tab.form.set_edit_value("afe.settle_discard", 7)
    tick(window)
    for b in (tab.write_button, tab.save_nvm_button, tab.load_nvm_button, tab.defaults_button):
        assert not b.isEnabled()
    assert "not connected" in tab.save_nvm_button.toolTip()


# --------------------------------------------------------------------------------------------- files (SW-CFG-002)

@pytest.mark.req("SW-CFG-002", "SW-CFG-003")
def test_board_config_file_round_trip(window, connected_fake, tab, tmp_path, qtbot) -> None:
    """Verifies: SW-CFG-002 — save the Edit column (session rows excluded), load fills the Edit column only and
    reports; nothing is written until Write & verify."""
    path = str(tmp_path / "a.bbboard.json")
    safe_dialog.FILE_DIALOG_HOOK[0] = lambda kind, cap, flt: path
    tab.form.set_edit_value("afe.settle_discard", 9)
    tab.save_file_button.click()
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["values"]["afe.settle_discard"] == 9 and not (LOCKED & set(data["values"]))
    tab.form.revert_edits()
    assert tab.form.editor_value("afe.settle_discard") != 9
    n_writes = len(connected_fake.calls_of("config.write_and_verify_async"))
    tab.load_file_button.click()
    assert tab.form.editor_value("afe.settle_discard") == 9 and tab.form.is_dirty("afe.settle_discard")
    assert len(connected_fake.calls_of("config.write_and_verify_async")) == n_writes
    rep = [d for d in tab.dialogs if isinstance(d, FileReportDialog)][-1]
    assert "Edit column" in rep.text.toPlainText()


@pytest.mark.req("SW-CFG-002")
def test_board_config_file_report_cases(window, connected_fake, tab, tmp_path) -> None:
    """Verifies: SW-CFG-002 — unknown, missing, out-of-range keys and hash mismatch are reported; a corrupt file
    leaves the Edit column unchanged."""
    cfg = BoardConfigFile("x.json", {"afe.settle_discard": 4}, ("foo.bar",), ("afe.rate_sps",), ("afe.timeout_ms",),
                          True, 0x1234, (Issue("afe.timeout_ms", IssueSeverity.WARN, "RANGE", "too big"),))
    lines = "\n".join(report_lines(cfg, ["safety.zero_raw"]))
    for s in ("foo.bar", "afe.rate_sps", "afe.timeout_ms", "hash mismatch", "0x00001234", "safety.zero_raw",
              "too big"):
        assert s in lines, s
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    safe_dialog.FILE_DIALOG_HOOK[0] = lambda kind, cap, flt: str(bad)
    before = tab.form.file_values()
    tab.load_file_button.click()
    assert tab.form.file_values() == before
    rep = [d for d in tab.dialogs if isinstance(d, FileReportDialog)][-1]
    assert "File not loaded" in rep.text.toPlainText() and "unchanged" in rep.text.toPlainText()


@pytest.mark.req("SW-CFG-002", "SW-CFG-003")
def test_load_values_skips_locked_and_out_of_range(window, tab) -> None:
    """Verifies: SW-CFG-002/003 — locked, unknown and out-of-range values never reach the Edit column."""
    skipped = tab.form.load_values({"safety.zero_raw": 5, "no.such": 1, "afe.settle_discard": 100000,
                                    "afe.timeout_ms": 300})
    assert set(skipped) == {"safety.zero_raw", "no.such", "afe.settle_discard"}
    assert tab.form.editor_value("afe.timeout_ms") == 300


@pytest.mark.req("SW-CFG-001")
def test_filter_and_only_changed(window, tab) -> None:
    """Verifies: SW-CFG-001 — filter by key, "only changed", advanced rows hidden unless asked."""
    form = tab.form
    form.filter_edit.setText("afe.")
    assert form.is_row_visible("afe.rate_sps") and not form.is_row_visible("motion.steps_per_mm")
    form.filter_edit.setText("")
    assert not form.is_row_visible("motion.pul_invert")         # advanced
    form.show_advanced.setChecked(True)
    assert form.is_row_visible("motion.pul_invert")
    form.set_edit_value("afe.settle_discard", 6)
    form.only_changed.setChecked(True)
    assert form.is_row_visible("afe.settle_discard") and not form.is_row_visible("afe.rate_sps")
