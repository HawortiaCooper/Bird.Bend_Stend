"""Level G — M1 GUI checks offscreen on B's real Backend (Validator F). Widgets are reached only by their public
object names / attributes the GUI design names (SW_design_GUI §2, §5); the wire log is the ground truth for
"STOP sent".

* STOP everywhere: toolbar first item, every dock (docked and floating), every M1 dialog class incl. the
  message box and the non-native file dialog; NoFocus, never default, fires on mouse PRESS (GQ-20);
  ``AA_DontUseNativeDialogs`` (SW-STOP-001 is an M3 requirement — checked early because the M1 GUI has it).
* ConfirmDialog keyboard rules (SAF-SW-004 basis, C-04 / C-11): Return / Enter / Space never confirm from any
  focusable widget, Esc cancels, a mouse click confirms.
* Indicators UNKNOWN (grey) when disconnected / stale, never "ok" (SAF-SW-005 basis, GRQ-B-02).
* Config tab (TC-SW-CFG-001-02, TC-SW-CFG-003-02), read-only on IF-008 (TC-IF-008-02), Stream on every tab
  (TC-SW-ACQ-001-01 G part), offscreen ``--sim`` smoke (TC-NFR-001-02 informative, SW-PLT-001 start).

Verifies: SW-STOP-001, SAF-SW-004, SAF-SW-005, SW-CFG-001, SW-CFG-003, SW-CFG-004, SW-ACQ-001, IF-008,
SW-PLT-001, NFR-001
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bend_stand.gui.dialogs import safe_dialog  # noqa: E402

safe_dialog.disable_native_dialogs()

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QPushButton, QWidget  # noqa: E402

import harness as H  # noqa: E402
from oracle.fboard import FBoard  # noqa: E402

GUI_PKG = Path(__file__).resolve().parents[2] / "src" / "bend_stand" / "gui"


@pytest.fixture(autouse=True)
def _gui_isolation(tmp_path, monkeypatch):
    from bend_stand.gui import stop as stop_mod

    monkeypatch.setenv("BEND_STAND_GUI_SETTINGS", str(tmp_path / "gui.ini"))
    prev = stop_mod.stop_handler()
    yield
    safe_dialog.FILE_DIALOG_HOOK[0] = None
    stop_mod.set_stop_handler(prev)


@pytest.fixture
def window(qtbot, tmp_path):
    from bend_stand.gui.main_window import MainWindow
    from bend_stand.gui.settings import make_settings

    made = []

    def make(be, show=True):
        win = MainWindow(be, settings=make_settings(tmp_path / "mw.ini"), start_refresh=False)
        qtbot.addWidget(win)
        made.append(win)
        if show:
            win.show()
            qtbot.waitExposed(win)
        win.refresh.tick()
        return win

    yield make
    for w in made:
        try:
            w._force_close = True
            w.close()
        except RuntimeError:
            pass


def _press(btn: QWidget) -> None:
    QTest.mousePress(btn, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 5))


def _release(btn: QWidget) -> None:
    QTest.mouseRelease(btn, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 5))


def _assert_stop_widget(btn: QPushButton) -> None:
    assert btn.objectName() == "stopButton" and btn.text() == "STOP"
    assert btn.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert not btn.isDefault() and not btn.autoDefault()


def _stop_on_press(be, btn) -> None:
    """Mouse PRESS alone (no release) puts exactly one STOP on the wire at once (lock-step: same instant)."""
    m0 = H.wire_mark(be)
    _press(btn)
    st = H.tx(be, "STOP", since=m0)
    assert len(st) == 1, [w.name for w in H.tx(be, since=m0)]
    _release(btn)
    assert len(H.tx(be, "STOP", since=m0)) == 1


# ============================================================================================ STOP everywhere

@pytest.mark.req("SW-STOP-001", "IF-011", "NFR-002")
def test_stop_first_toolbar_item_fires_on_press(vbe, window):
    """Toolbar STOP is the first item, NoFocus, never default, fires on the mouse press (GQ-20)."""
    # Verifies: SW-STOP-001, IF-011, NFR-002
    win = window(vbe)
    actions = win.toolbar.actions()
    assert win.toolbar.widgetForAction(actions[0]) is win.stop_button
    _assert_stop_widget(win.stop_button)
    _stop_on_press(vbe, win.stop_button)


@pytest.mark.req("SW-STOP-001")
def test_stop_in_every_dock_docked_and_floating(vbe, window, qtbot):
    """Plot, Readouts and Event log docks carry STOP in their title bar, also when floating."""
    # Verifies: SW-STOP-001
    win = window(vbe)
    for dock in (win.plot_dock, win.readout_dock, win.event_log):
        dock.show()
        _assert_stop_widget(dock.stop_button)
        _stop_on_press(vbe, dock.stop_button)
        dock.setFloating(True)
        qtbot.wait(20)
        assert dock.isFloating() and dock.stop_button.isVisible()
        _stop_on_press(vbe, dock.stop_button)
        dock.setFloating(False)


@pytest.mark.req("SW-STOP-001")
def test_stop_in_every_m1_dialog(vbe, window, qtbot, tmp_path):
    """Every dialog the M1 GUI opens carries a working STOP: Clear stop, Link statistics, Status help, About,
    Restore defaults (C-04), Save & reboot (C-11), keyboard message box, file report, and the non-native
    board-config Save/Open file dialogs."""
    # Verifies: SW-STOP-001
    from bend_stand.gui.dialogs.file_report import FileReportDialog

    win = window(vbe)
    tab = win.connection_tab
    opened = []
    win.open_clear_stop()
    opened.append(win.dialogs["clear"])
    win.open_link_stats()
    opened.append(win.dialogs["link"])
    win.open_status_help(None)
    opened.append(win.dialogs["help"])
    win.open_about()
    opened.append(win.dialogs["about"])
    win.open_keyboard_help()
    opened.append(win.dialogs["keys"])
    tab.on_defaults()
    opened.append(tab.confirm_dialog)
    tab.confirm_dialog.reject()
    vbe.status()
    tab.on_save_reboot()
    opened.append(tab.confirm_dialog)
    rep = FileReportDialog(None, win, error="crafted")
    rep.show()
    opened.append(rep)
    for dlg in opened:
        if not dlg.isVisible():
            dlg.show()
        qtbot.waitExposed(dlg)
        btns = [b for b in dlg.findChildren(QPushButton) if b.objectName() == "stopButton"]
        assert len(btns) == 1, type(dlg).__name__
        _assert_stop_widget(btns[0])
        _stop_on_press(vbe, btns[0])
        dlg.close()
    for kind in ("save", "open"):
        fd = safe_dialog.SafeFileDialog(win, f"{kind} board configuration", str(tmp_path), "*.json")
        fd.show()
        qtbot.waitExposed(fd)
        assert fd.testOption(fd.Option.DontUseNativeDialog)
        btns = [b for b in fd.findChildren(QPushButton) if b.objectName() == "stopButton"]
        assert len(btns) == 1
        _assert_stop_widget(btns[0])
        _stop_on_press(vbe, btns[0])
        fd.close()
    assert safe_dialog.native_dialogs_disabled()


@pytest.mark.req("SW-STOP-001")
def test_every_gui_dialog_class_derives_from_a_safe_base():
    """Inspection: every QDialog / QMessageBox / QFileDialog subclass and every instantiation in the GUI package
    goes through SafeDialog / SafeMessageBox / SafeFileDialog (so every dialog carries STOP)."""
    # Verifies: SW-STOP-001
    bad = []
    for p in GUI_PKG.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
                if bases & {"QDialog", "QMessageBox", "QFileDialog", "QInputDialog", "QColorDialog"} and \
                        node.name not in ("SafeDialog", "SafeMessageBox", "SafeFileDialog"):
                    bad.append(f"{p.name}:{node.name}")
            if isinstance(node, ast.Call):
                f = node.func
                name = getattr(f, "attr", getattr(f, "id", ""))
                owner = getattr(getattr(f, "value", None), "id", "")
                if owner in ("QMessageBox", "QFileDialog", "QInputDialog") and name not in ("Icon", "StandardButton",
                                                                                           "ButtonRole", "Option",
                                                                                           "AcceptMode", "FileMode"):
                    bad.append(f"{p.name}:{owner}.{name}")
                if name in ("QMessageBox", "QFileDialog", "QDialog", "QInputDialog") and isinstance(f, ast.Name):
                    bad.append(f"{p.name}:{name}()")
    assert not bad, bad


# ============================================================================================ dialog keyboard

@pytest.mark.req("SAF-SW-004", "SW-CFG-004")
@pytest.mark.parametrize("cid", ["C-04", "C-11"])
def test_confirm_dialog_keyboard_rules(vbe, window, qtbot, cid):
    """Return / Enter / Space on every focusable widget never confirm; Esc cancels; only a mouse click on the
    confirm button confirms (then the action reaches the wire: DEFAULT_PARAMS for C-04)."""
    # Verifies: SAF-SW-004, SW-CFG-004
    win = window(vbe)
    tab = win.connection_tab
    open_fn = tab.on_defaults if cid == "C-04" else tab.on_save_reboot
    open_fn()
    dlg = tab.confirm_dialog
    qtbot.waitExposed(dlg)
    confirmed = []
    dlg.confirmed.connect(lambda: confirmed.append(1))
    focusables = [w for w in dlg.findChildren(QWidget) if w.focusPolicy() != Qt.FocusPolicy.NoFocus
                  and w.isVisible() and w.isEnabled()]
    assert focusables
    for w in focusables + [dlg]:
        w.setFocus()
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            QTest.keyClick(w, key)
            assert not confirmed and dlg.outcome == "pending", (w, key)
    QTest.keyClick(dlg, Qt.Key.Key_Escape)
    assert dlg.outcome == "cancelled" and not confirmed
    open_fn()
    dlg = tab.confirm_dialog
    qtbot.waitExposed(dlg)
    dlg.confirmed.connect(lambda: confirmed.append(1))
    assert dlg.confirm_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    m0 = H.wire_mark(vbe)
    assert dlg.confirm_button.isEnabled()
    QTest.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert confirmed and dlg.outcome == "confirmed"
    for _ in range(60):
        qtbot.wait(5)
        H.advance(vbe, 50)
    if cid == "C-04":
        assert len(H.tx(vbe, "DEFAULT_PARAMS", since=m0)) == 1
    else:
        assert len(H.tx(vbe, "SAVE_PARAMS", since=m0)) == 1 and len(H.tx(vbe, "REBOOT", since=m0)) == 1


# ============================================================================================ indicators

UNKNOWN_CHIPS = ("ESTOP", "HALT", "PAUSED", "LIM_S", "LIM_E", "LOAD", "FAULT", "DRV", "AFE", "WDG", "HOMED", "ENA",
                 "MOV", "VALID", "CFG")


@pytest.mark.req("SAF-SW-005")
def test_indicators_unknown_when_disconnected_or_stale(lockstep, window):
    """Disconnected → every state chip grey/unknown (never ok); connected + fresh → known; board silent for 2 s
    (DATA and STATUS stale) → unknown again (GRQ-B-02, SAF-SW-005 'never drawn as OK')."""
    # Verifies: SAF-SW-005
    be = lockstep(connect=False)
    win = window(be)
    bar = win.indicator_bar
    for c in UNKNOWN_CHIPS:
        assert bar.chip(c).level == "unknown", (c, bar.chip(c).level)
    H.result(be, H.connect(be))
    H.advance(be, 1500)
    win.refresh.tick()
    known = [c for c in UNKNOWN_CHIPS if bar.chip(c).level != "unknown"]
    assert set(known) >= {"ESTOP", "HALT", "PAUSED", "ENA", "MOV"}, known
    H.act(be, "inject", fault="hang", duration_ms=5000)
    H.advance(be, 2000)
    win.refresh.tick()
    for c in UNKNOWN_CHIPS:
        assert bar.chip(c).level != "ok", c
    for c in ("ESTOP", "HALT", "PAUSED", "MOV", "ENA"):
        assert bar.chip(c).level == "unknown", c


@pytest.mark.req("SAF-SW-005", "SW-STOP-003")
def test_indicator_halt_with_source_and_clear_hint(vbe, window):
    """HALT latched from the PC → HALT chip alarm with source PC; status help names the clear procedure."""
    # Verifies: SAF-SW-005, SW-STOP-003
    win = window(vbe)
    H.halt(vbe)
    H.advance(vbe, 1200)
    win.refresh.tick()
    chip = win.indicator_bar.chip("HALT")
    assert chip.level == "alarm"
    ind = H.indicator(vbe, "halt")
    assert ind.state == "ON" and ind.source == "PC" and ind.clear_hint
    assert "PC" in chip.value.text() or "PC" in chip.toolTip()


# ============================================================================================ config tab

@pytest.mark.req("SW-CFG-001", "SW-CFG-003")
def test_tc_sw_cfg_001_02_config_tab_rows_values_editors(vbe, window, pdict):
    """Every parameter has a row with its board value (= the board's, read over the wire) and a typed editor;
    session rows are locked (not editable)."""
    # Verifies: SW-CFG-001, SW-CFG-003
    from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox

    win = window(vbe)
    form = win.connection_tab.form
    win.connection_tab.reload_board_values()
    vals = H.config_values(vbe)
    assert set(form.keys()) == {p.key for p in pdict.params}
    for p in pdict.params:
        assert form.board_value(p.key) == pytest.approx(vals[p.key]), p.key
        ed = form.editor_for(p.key)
        if not p.nvm:
            assert not form.is_editable(p.key), p.key
            continue
        assert form.is_editable(p.key), p.key
        if p.type == "bool":
            assert isinstance(ed, QCheckBox)
        elif p.type == "enum":
            assert isinstance(ed, QComboBox) and [ed.itemText(i) for i in range(ed.count())] == [e.name for e in p.enum]
        elif p.type == "f32":
            assert isinstance(ed, QDoubleSpinBox)
    assert form.locked_keys() == {p.key for p in pdict.params if not p.nvm}


@pytest.mark.req("SW-CFG-003")
def test_tc_sw_cfg_003_02_per_row_status_ok_rejected_mismatch(vbe, window, qtbot, pdict):
    """Write & verify from the Config tab: per-row status OK / REJECTED / MISMATCH shown (injected NACK and
    store mismatch); the wire carries exactly the edited SET_PARAMs."""
    # Verifies: SW-CFG-003
    win = window(vbe)
    tab = win.connection_tab
    tab.reload_board_values()
    rel = next(p for p in pdict.params if p.key == "io.release_ms")
    H.inject_nack(vbe, "SET_PARAM", "E_RANGE", rel.id)
    H.inject_store_mismatch(vbe, "io.estop_release_ms", 150)
    tab.form.set_edit_value("io.release_ms", 50)
    tab.form.set_edit_value("io.estop_release_ms", 140)
    tab.form.set_edit_value("stream.fallback_hz", 20)
    tab.run_rule_check()
    m0 = H.wire_mark(vbe)
    tab.on_write()
    for _ in range(100):
        H.advance(vbe, 20)
        qtbot.wait(2)
        if tab.form.status_of("stream.fallback_hz"):
            break
    assert tab.form.status_of("io.release_ms") == "REJECTED"
    assert tab.form.status_of("io.estop_release_ms") == "MISMATCH"
    assert tab.form.status_of("stream.fallback_hz") == "OK"
    ids = sorted({w.fields["id"] for w in H.tx(vbe, "SET_PARAM", since=m0)})
    by = {p.key: p.id for p in pdict.params}
    assert ids == sorted({by["io.release_ms"], by["io.estop_release_ms"], by["stream.fallback_hz"]})


@pytest.mark.req("IF-008", "SW-CFG-003")
def test_tc_if_008_02_gui_read_only_on_hash_mismatch(pdict, window, qtbot, tmp_path):
    """IF-008 in the GUI: dictionary-hash mismatch → form read-only with the reason, Write / NVM buttons
    disabled, version line warns."""
    # Verifies: IF-008, SW-CFG-003
    fb = FBoard(pdict, dict_hash=pdict.hash ^ 0xFFFF)
    be = H.realtime_backend(recordings_root=str(tmp_path))
    try:
        win = window(be)
        H.connect(be, fb.endpoint).result(10)
        assert H.wait_rt(lambda: H.status(be).board is not None, 5)
        win.refresh.tick()
        tab = win.connection_tab
        assert tab.form.read_only
        assert not tab.write_button.isEnabled() and not tab.save_nvm_button.isEnabled()
        assert not tab.defaults_button.isEnabled()
        assert "✓" not in tab.version_label.text()
        assert all(not tab.form.is_editable(k) for k in tab.form.keys())
    finally:
        be.shutdown()
        fb.close()


@pytest.mark.req("SW-ACQ-001")
def test_tc_sw_acq_001_01_stream_button_on_every_tab(vbe, window, qtbot):
    """The Stream toggle is on the global toolbar: visible and usable whichever tab is current; its checked state
    follows the backend (not the click)."""
    # Verifies: SW-ACQ-001
    win = window(vbe)
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        win.refresh.tick()
        assert win.stream_button.isVisible() and win.stream_button.isEnabled()
        on = H.status(vbe).stream.on
        assert win.stream_button.isChecked() == on
        win.stream_button.click()
        for _ in range(40):
            H.advance(vbe, 25)
            qtbot.wait(1)
        win.refresh.tick()
        assert H.status(vbe).stream.on == (not on)
        assert win.stream_button.isChecked() == (not on)


# ============================================================================================ smoke

@pytest.mark.rt
@pytest.mark.req("SW-PLT-001", "SYS-008", "NFR-001")
def test_gui_sim_smoke_offscreen(tmp_path):
    """`python -m bend_stand --sim` offscreen, closed after 8 s: exit code 0, link CONNECTED, stream ~80 Hz,
    0 lost frames, refresh p95 recorded (informative NFR-001 smoke on DEV)."""
    # Verifies: SW-PLT-001, SYS-008, NFR-001
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", BEND_STAND_GUI_QUIT_AFTER_MS="8000",
               BEND_STAND_GUI_SETTINGS=str(tmp_path / "gui.ini"), APPDATA=str(tmp_path))
    src = Path(__file__).resolve().parents[2] / "src"
    p = subprocess.Popen([sys.executable, "-m", "bend_stand", "--sim", "--log-level", "INFO"], cwd=str(src), env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    H.log_process("gui smoke", p.pid, "started")
    try:
        out, _ = p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        pytest.fail("GUI smoke did not exit")
    H.log_process("gui smoke", p.pid, f"exited rc={p.returncode}")
    assert p.returncode == 0, out[-2000:]
    line = next((ln for ln in out.splitlines() if "GUI exit rc=0" in ln), "")
    assert "link CONNECTED" in line and "lost fw 0 link 0" in line, out[-2000:]
    (tmp_path / "smoke.txt").write_text(line, encoding="utf-8")


@pytest.mark.req("SW-CFG-004", "SW-CFG-003")
def test_cfg_dirty_indicator_and_save_reboot_offer(vbe, window):
    """SW-CFG-004: the CFG indicator follows sys_flags (CFG_DIRTY / REBOOT_PENDING) and Save to NVM clears the
    dirty state; SW-CFG-003: after a reboot-required write the GUI offers Save & reboot (enabled only then)."""
    # Verifies: SW-CFG-004, SW-CFG-003
    win = window(vbe)
    tab = win.connection_tab
    H.result(vbe, H.save_nvm(vbe))                                 # start from a clean NVM image
    H.advance(vbe, 1200)
    win.refresh.tick()
    assert "clean" in tab.cfg_label.text() and win.indicator_bar.chip("CFG").level not in ("warn", "alarm",
                                                                                           "unknown")
    assert not tab.reboot_button.isEnabled()
    assert H.result(vbe, H.write_verify(vbe, {"motion.pul_invert": True})).ok
    H.advance(vbe, 200)
    win.refresh.tick()
    assert "dirty" in tab.cfg_label.text() and "reboot pending" in tab.cfg_label.text()
    assert win.indicator_bar.chip("CFG").level == "warn"
    assert tab.reboot_button.isEnabled()
    H.result(vbe, H.save_nvm(vbe))
    H.advance(vbe, 200)
    win.refresh.tick()
    assert "dirty" not in tab.cfg_label.text() and "reboot pending" in tab.cfg_label.text()


@pytest.mark.req("SW-STOP-002", "SAF-SW-005")
@pytest.mark.defect("SWD-M1-06")
@pytest.mark.parametrize("dropped", [0, 100], ids=["confirmed", "unconfirmed"])
def test_stop_banner_names_the_command_of_the_confirmation(vbe, window, qtbot, dropped):
    """The stop banner reports the state of the command actually sent: Pause/Break (app shortcut) → HALT; its
    confirmation / 'NOT CONFIRMED after 1 s' alarm must name HALT, not STOP (SW-STOP-002, ICD §9.4)."""
    # Verifies: SW-STOP-002, SAF-SW-005
    win = window(vbe)
    if dropped:
        H.act(vbe, "inject", fault="drop_next", cmd="HALT", what="request", n=dropped)
    win._on_app_halt()                         # the app-level Pause/Break handler (global hotkey: M3)
    for _ in range(30):
        H.advance(vbe, 50)
        qtbot.wait(2)
    win.refresh.tick()
    rec = win.stop_banner.recent
    assert rec is not None and rec.cmd == "HALT", rec
    assert rec.state == ("unconfirmed" if dropped else "confirmed"), rec
