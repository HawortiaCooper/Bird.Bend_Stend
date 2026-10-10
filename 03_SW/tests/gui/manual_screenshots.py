"""Screenshots for the operator manual (``03_SW/docs/USER_MANUAL.md``), generated offscreen from the real GUI on B's
real ``Backend`` + in-process FW simulator (lock-step clock; D-06: no COM port is opened).

Not a pytest module (no ``test_`` prefix). Run from the repository root::

    .venv\\Scripts\\python 03_SW\\tests\\gui\\manual_screenshots.py            # → 03_SW/docs/img/*.png
    .venv\\Scripts\\python 03_SW\\tests\\gui\\manual_screenshots.py --out <dir>

The Qt ``offscreen`` platform has no font database on Windows; the script loads Segoe UI / Segoe UI Symbol /
Consolas from ``C:\\Windows\\Fonts`` (read-only) with ``QFontDatabase.addApplicationFont``. Without them the
screenshots are layout-only (glyph boxes) and the script says so.

All data (settings, calibration files, recordings) go to a temporary folder that is deleted at the end; the
operator's ``%APPDATA%`` is never touched. The simulator's test-only load model (``set_cell_load`` /
``set_specimen``) stands in for the weights and the specimen.

Implements: (documentation support) SW-PLT-001 operator documentation screenshots
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
HERE = Path(__file__).resolve().parent
SW = HERE.parents[1]
sys.path.insert(0, str(SW / "src"))
sys.path.insert(0, str(HERE))

FONT_FILES = ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf", "consola.ttf", "arial.ttf")


def load_fonts() -> bool:
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    ok = 0
    for f in FONT_FILES:
        p = windir / f
        if p.exists() and QFontDatabase.addApplicationFont(str(p)) >= 0:
            ok += 1
    if ok:
        QApplication.instance().setFont(QFont("Segoe UI", 9))
    return ok > 0


class Shooter:
    def __init__(self, out: Path, tmp: Path) -> None:
        from bend_stand.core import backend as backend_mod
        from bend_stand.gui.dialogs import safe_dialog
        from bend_stand.gui.main_window import MainWindow
        from bend_stand.gui.plots.plot_dock import configure_pyqtgraph
        from bend_stand.gui.settings import make_settings

        safe_dialog.disable_native_dialogs()
        configure_pyqtgraph()
        self.out = out
        self.shots: list[str] = []
        self.be = backend_mod.Backend(backend_mod.BackendSettings(
            clock="lockstep", test_hooks=True, wire_log=False, hotkey="off", sim_nvm_path=str(tmp / "nvm.json"),
            recordings_root=str(tmp / "rec")))
        self.be.start()
        self.win = MainWindow(self.be, settings=make_settings(tmp / "gui.ini"), start_refresh=False)
        self.win.resize(1600, 960)
        self.win.show()

    # ------------------------------------------------------------------ helpers
    def step(self, ms: float = 33.0) -> None:
        from PySide6.QtWidgets import QApplication
        self.be.test_hooks.advance(ms)
        QApplication.processEvents()
        self.win.refresh.tick()

    def run_until(self, pred, ms: float = 60_000.0) -> bool:
        t = 0.0
        while t < ms:
            if pred():
                return True
            self.step(50.0)
            t += 50.0
        return bool(pred())

    def settle(self, n: int = 6) -> None:
        for _ in range(n):
            self.step(33.0)

    def shot(self, widget, name: str) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        widget.repaint()
        QApplication.processEvents()
        path = self.out / f"{name}.png"
        widget.grab().save(str(path))
        self.shots.append(path.name)
        print("  saved", path.name)

    def click(self, button) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.step()

    def confirm(self, dlg) -> None:
        if dlg is None:
            return
        if getattr(dlg, "assertion_box", None) is not None:
            dlg.assertion_box.setChecked(True)
        self.click(dlg.confirm_button)

    def drive(self, w, inputs: dict, until: str, on_point=None, shoot_at: dict | None = None,
              max_steps: int = 4000) -> list[str]:
        """Wizard driver (same rules as tests/gui/test_sim_calibration.drive): confirm dialogs, fill inputs,
        mouse-click Continue; ``shoot_at`` = {phase or (phase, step_index): name} → one screenshot each."""
        from bend_stand.gui.wizards import phase_views as pv
        seen: list[str] = []
        shoot_at = dict(shoot_at or {})
        for _ in range(max_steps):
            st = w.state
            ph = st.phase if st is not None else ""
            if not seen or seen[-1] != ph:
                seen.append(ph)
            if ph == until or ph in ("ABORTED", "CANCELLED"):
                return seen
            if ph == "AWAIT_OPERATOR" and on_point is not None:
                on_point(st)
            for key, spin in w._inputs.items():                       # noqa: SLF001 - generated editors
                if key in inputs and abs(spin.value() - inputs[key]) > 1e-9:
                    spin.setValue(inputs[key])
            key2 = (ph, int(st.step_index or 0)) if st is not None else None
            for k in (key2, ph):
                if k in shoot_at:
                    self.settle(3)
                    self.shot(w, shoot_at.pop(k))
                    break
            dlg = w.confirm_dialog
            if dlg is not None and getattr(dlg, "outcome", "") == "pending" and dlg.isVisible():
                self.confirm(dlg)
                continue
            if st is not None and st.can_continue and not st.errors and w.continue_button.isVisible() \
                    and w.continue_button.isEnabled() and ph not in pv.TERMINAL_PHASES:
                self.click(w.continue_button)
            self.step(50.0)
        return seen

    # ------------------------------------------------------------------ scenes
    def run(self) -> None:
        from PySide6.QtCore import Qt

        from bend_stand.core.api import GateId
        be, win = self.be, self.win
        h = be.test_hooks
        tab = win.connection_tab
        tab.selector.set_endpoint("sim")
        self.settle()
        print("01 start (disconnected)")
        self.shot(win, "01_main_disconnected")

        print("02 connect to the simulator")
        self.click(tab.connect_button)
        assert self.run_until(lambda: str(be.status().link.state.value) == "CONNECTED", 10_000)
        assert self.run_until(lambda: be.status().stream.on and tab.form.board_value("afe.rate_sps") is not None,
                              10_000)
        h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}), 5000)
        self.settle(40)
        self.shot(win, "02_connection_config")
        h.result(be.config.save_async(), 10_000)                       # first start: Save to NVM (notice clears)
        self.settle(20)

        print("03 first use: motion refused without calibration / tare")
        win.tabs.setCurrentWidget(win.manual_tab)
        self.settle(10)
        self.shot(win, "03_first_use_banner")

        print("04 no-specimen mode (C-10)")
        lt = win.limits_tab
        win.tabs.setCurrentWidget(lt)
        self.settle()
        self.click(lt.nospec_enter)
        dlg = lt.confirm_dialog
        dlg.assertion_box.setChecked(True)
        self.settle(2)
        self.shot(dlg, "04_confirm_no_specimen")
        self.click(dlg.confirm_button)
        assert self.run_until(lambda: be.status().safety.no_specimen_mode, 3000)
        assert self.run_until(lambda: "LOAD_INPUT_INVALID" not in be.status().gates[GateId.MOVE].codes(), 5000)
        self.settle(10)
        self.shot(win, "05_limits_no_specimen")

        print("06 enable + HOME (C-01)")
        mt = win.manual_tab
        win.tabs.setCurrentWidget(mt)
        assert self.run_until(lambda: mt.enable_box.isEnabled(), 3000)
        self.click(mt.enable_box)
        assert self.run_until(lambda: be.status().motion.enabled, 3000)
        assert self.run_until(lambda: mt.home_button.isEnabled(), 3000)
        self.click(mt.home_button)
        dlg = mt.confirm_dialog
        dlg.assertion_box.setChecked(True)
        self.settle(2)
        self.shot(dlg, "06_confirm_home_under_load")
        self.click(dlg.confirm_button)
        assert self.run_until(lambda: be.status().motion.homed and not be.status().motion.moving, 60_000)
        plus10 = next(b for b in mt.step_buttons if b.text() == "+10")
        for _ in range(2):
            assert self.run_until(lambda: plus10.isEnabled() and not be.status().motion.moving, 20_000)
            self.click(plus10)
            self.run_until(lambda: be.status().motion.moving, 2000)
            assert self.run_until(lambda: not be.status().motion.moving and not be.motion.busy, 20_000)
        self.settle(20)
        self.shot(win, "07_manual_homed")

        print("08 travel calibration wizard")
        w = win.open_travel_wizard()
        w.resize(860, 640)
        assert self.run_until(lambda: w.start_button.isEnabled(), 3000)
        self.settle(3)
        self.shot(w, "08_travel_wizard_start")
        self.click(w.start_button)
        seen = self.drive(w, {"d1_mm": 10.02, "dtot_mm": 60.05}, until="DONE",
                          shoot_at={"ENTER_D1": "09_travel_wizard_enter_d1", "RESULT": "10_travel_wizard_result"})
        print("   phases", seen)
        w.close()
        self.settle()

        print("11 load calibration wizard (1 kg + 10 kg)")
        sim = be.sim                                                    # in-process simulator control (test-only)
        sim.set_cell_load(0.0)
        w = win.open_load_wizard()
        w.resize(900, 680)
        assert self.run_until(lambda: w.start_button.isEnabled(), 3000)
        w.capture.setValue(5.0)
        self.settle(3)
        self.shot(w, "11_load_wizard_config")
        self.click(w.start_button)
        masses = {1: 1.0, 2: 10.0}

        def on_point(st) -> None:
            m = masses.get(int(st.step_index or 0), 0.0)
            sim.set_cell_load(mass_kg=m)
            spin = w._inputs.get("mass_kg")                            # noqa: SLF001
            if spin is not None:
                spin.setValue(m)
        seen = self.drive(w, {}, until="FIT", on_point=on_point,
                          shoot_at={("AWAIT_OPERATOR", 1): "12_load_wizard_weight1", "CAPTURE": "13_load_wizard_capture"})
        self.settle(5)
        self.shot(w, "14_load_wizard_fit")
        seen += self.drive(w, {}, until="DONE")
        print("   phases", seen)
        self.settle(5)
        w.close()
        sim.set_cell_load(0.0)
        self.step(1500)

        print("15 tare")
        win.on_tare(None)
        p = win.tare_popup
        self.run_until(lambda: p.state_name == "CAPTURE", 5000)
        self.step(3000)
        self.settle(3)
        self.shot(p, "15_tare_popup_running")
        assert self.run_until(lambda: p.state_name in ("DONE", "EVALUATE", "ABORTED", "REFUSED"), 30_000)
        self.settle(3)
        self.shot(p, "16_tare_popup_done")
        p.close()
        self.settle()

        print("17 calibration tab")
        win.tabs.setCurrentWidget(win.calibration_tab)
        self.settle(10)
        self.shot(win, "17_calibration_tab")

        print("18 leave no-specimen mode; safety limits")
        self.click(win.mode_banner.leave_button)
        assert self.run_until(lambda: not be.status().safety.no_specimen_mode, 3000)
        win.tabs.setCurrentWidget(lt)
        self.settle(20)
        self.shot(win, "18_limits_tab")

        print("19 test marks")
        mk = win.marks_tab
        win.tabs.setCurrentWidget(mk)
        self.settle()
        _fill_marks(mk)
        self.settle(10)
        self.shot(win, "19_marks_tab")

        print("20 specimen + plots")
        x_now = be.status().motion.position_mm
        x_true_um = float(sim.act("query", what="world")["x_um_true"])
        sim.set_specimen("spring", k_n_per_mm=20.0, x_contact_um=int(x_true_um), side="pull")
        win.tabs.setCurrentWidget(mt)
        self.settle()
        self.click(mt.zero_button)
        self.settle(5)
        dock = win.plot_dock
        _setup_plot(win, dock)
        plus1 = next(b for b in mt.step_buttons if b.text() == "+1")
        for _ in range(4):
            assert self.run_until(lambda: plus1.isEnabled() and not be.status().motion.moving, 20_000)
            self.click(plus1)
            self.run_until(lambda: be.status().motion.moving, 2000)
            assert self.run_until(lambda: not be.status().motion.moving and not be.motion.busy, 20_000)
            self.step(1500)
        minus = next(b for b in mt.step_buttons if b.text() == "-1")

        def back(n: int) -> None:
            for _ in range(n):
                assert self.run_until(lambda: minus.isEnabled() and not be.status().motion.moving, 20_000)
                self.click(minus)
                self.run_until(lambda: be.status().motion.moving, 2000)
                assert self.run_until(lambda: not be.status().motion.moving and not be.motion.busy, 20_000)
                self.step(1000)
        back(1)
        self.settle(30)
        self.shot(win, "20_manual_with_plots")
        self.shot(dock, "21_plot_window")
        self.shot(win.indicator_bar, "22_indicator_bar")
        self.shot(win.toolbar, "23_toolbar")
        self.shot(win.readout_dock, "24_readouts")
        print("   x", x_now, "->", be.status().motion.position_mm)
        back(3)                                                         # unloaded at the test zero again

        print("25 sequence: generator wizard")
        st = win.sequence_tab
        win.tabs.setCurrentWidget(st)
        self.settle(5)
        # back to the test zero (unloaded) before the sequence
        g = st.open_generator()
        g.choose("staircase")
        self.settle(2)
        self.shot(g, "25_generator_choose")
        self.click(g.next_button)
        f = g.form
        for k, v in (("kind", "load"), ("start", 20.0), ("end", 60.0), ("increment", 20.0), ("speed_mm_s", 1.0),
                     ("settle_s", 1.0), ("capture_s", 2.0), ("tol_n", 2.0)):
            f.set_value(k, v)
        f.update_visibility()
        self.settle(2)
        self.shot(g, "26_generator_parameters")
        self.click(g.next_button)
        self.settle(3)
        self.shot(g, "27_generator_preview")
        self.click(g.insert_button)
        self.settle(3)
        rg = st.open_generator()
        rg.choose("return_")
        self.click(rg.next_button)
        self.click(rg.next_button)
        self.click(rg.insert_button)
        st.revalidate()
        self.settle(10)
        self.shot(win, "28_sequence_editor")

        print("29 sequence run, pause, resume")
        assert self.run_until(lambda: st.start_button.isEnabled(), 5000), st.start_button.toolTip()
        self.click(st.start_button)
        dlg = st.confirm_dialog
        if dlg is not None and dlg.outcome == "pending":
            self.settle(2)
            self.shot(dlg, "29_confirm_sequence_start")
            self.click(dlg.confirm_button)
        assert self.run_until(lambda: st.view.active, 5000), st.message_texts()
        self.run_until(lambda: st.view.phase in ("CAPTURE", "SETTLE") if hasattr(st.view, "phase") else False, 60_000)
        self.step(3000)
        self.settle(10)
        self.shot(win, "30_sequence_running")
        assert self.run_until(lambda: st.pause_button.isEnabled(), 5000)
        self.click(st.pause_button)
        assert self.run_until(lambda: st.view.state == "PAUSED", 10_000), st.run_label.text()
        self.settle(10)
        self.shot(win, "31_sequence_paused")
        assert self.run_until(lambda: st.resume_button.isEnabled() and not be.status().motion.moving, 10_000)
        self.click(st.resume_button)
        assert self.run_until(lambda: st.view.state in ("FINISHED", "STOPPED", "ABORTED", "ERROR"), 300_000), \
            st.run_label.text()
        self.settle(10)
        self.shot(win, "32_sequence_finished")
        print("   end", st.view.state, st.message_texts()[-3:])

        print("33 report tab")
        rep = win.report_tab
        win.tabs.setCurrentWidget(rep)

        def listed() -> bool:
            rep.refresh_list()
            return any(r.get("has_report") for r in rep.recordings)
        self.run_until(listed, 30_000)
        if rep.recordings:
            folder = next((r["folder"] for r in rep.recordings if r.get("has_report")), rep.recordings[0]["folder"])
            rep.select_folder(folder)
        self.settle(10)
        self.shot(win, "33_report_tab")

        print("34 guard: BREAK_DETECTED")
        x_true_um = float(sim.act("query", what="world")["x_um_true"])
        sim.set_specimen("spring", k_n_per_mm=20.0, x_contact_um=int(x_true_um), side="pull", f_break_n=45.0)
        win.tabs.setCurrentWidget(st)
        st.new_sequence(confirm=False)
        self.settle(3)
        st.table.clearSelection()
        st.add_step("load")
        m = st.model
        m.setData(m.index(0, m.column_of("target")), "80", Qt.ItemDataRole.EditRole)
        st.revalidate()
        self.settle(5)
        assert self.run_until(lambda: st.start_button.isEnabled(), 5000), st.start_button.toolTip()
        self.click(st.start_button)
        dlg = st.confirm_dialog
        if dlg is not None and dlg.outcome == "pending":
            self.click(dlg.confirm_button)
        assert self.run_until(lambda: st.view.state in ("FINISHED", "STOPPED", "ABORTED", "ERROR"), 120_000),             st.run_label.text()
        self.settle(10)
        self.shot(win, "34_sequence_break_detected")
        print("   end", st.view.state, st.message_texts()[-3:])
        sim.set_specimen("none")
        self.settle(5)

        print("35 HALT (Pause/Break) + Clear stop")
        r = be.halt("hotkey")
        win._show_stop_result(r)                                        # noqa: SLF001 - as the hotkey path
        assert self.run_until(lambda: be.status().indicators.halt.state == "ON", 3000)
        self.settle(10)
        self.shot(win, "35_halt_banner")
        win.open_clear_stop()
        cd = win.dialogs["clear"]
        cd.resize(720, 360)
        self.settle(5)
        self.shot(cd, "36_clear_stop_halt")
        self.click(cd.halt_button)
        assert self.run_until(lambda: be.status().indicators.halt.state == "OFF", 3000)
        cd.close()
        self.settle()

        print("37 E-stop + Clear stop")
        sim.set_estop(True)
        assert self.run_until(lambda: be.status().indicators.estop.state == "ON", 3000)
        self.settle(10)
        self.shot(win, "37_estop_banner")
        sim.set_estop(False)
        self.step(500)
        win.open_clear_stop()
        cd = win.dialogs["clear"]
        cd.resize(720, 380)
        cd.estop_check.setChecked(True)
        self.run_until(lambda: (cd.refresh() or True) and cd.estop_button.isEnabled(), 5000)
        self.settle(5)
        self.shot(cd, "38_clear_stop_estop")
        self.click(cd.estop_button)
        self.run_until(lambda: be.status().indicators.estop.state == "OFF", 3000)
        self.settle(5)
        cd.close()

        print("39 help dialogs")
        win.open_status_help(None)
        dlg = win.dialogs["help"]
        dlg.resize(760, 560)
        self.settle(2)
        self.shot(dlg, "39_status_help")
        dlg.close()
        hk = win.open_hotkey_test()
        self.settle(3)
        self.shot(hk, "40_hotkey_test")
        hk.close()
        self.settle()

    def close(self) -> None:
        try:
            self.win._force_close = True                                # noqa: SLF001
            self.win.close()
        finally:
            self.be.shutdown()


def _fill_marks(mk) -> None:
    """Fill the marks fields if the tab exposes them (display only; the backend stores them)."""
    for attr, text in (("specimen", "Wing bracket, PA12"), ("number", "17"), ("operator", "O. Operator")):
        w = getattr(mk, attr, None)
        if w is not None and hasattr(w, "setText"):
            w.setText(text)
            w.editingFinished.emit()


def _setup_plot(win, dock) -> None:
    """Plot 1: force + travel time panes and an X-Y pane (as an operator would tick them)."""
    try:
        keys = {s.key for s in win.backend.channels.channels() if getattr(s, "available", True)}
        for k in ("F_N", "x_test_mm"):
            if k in keys:
                dock.tree.set_checked(k, True)
        dock.add_xy_pane()
        dock.set_columns(2)
        win._reload_channels()                                          # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - screenshots only
        print("   plot setup:", exc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(SW / "docs" / "img"))
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Bird Bend Stand")
    fonts = load_fonts()
    print("fonts loaded" if fonts else "NO FONTS: screenshots are layout-only")
    with tempfile.TemporaryDirectory(prefix="bbs_manual_") as td:
        tmp = Path(td)
        os.environ["BEND_STAND_DATA_DIR"] = str(tmp / "data")
        os.environ["BEND_STAND_GUI_SETTINGS"] = str(tmp / "gui.ini")
        s = Shooter(out, tmp)
        try:
            s.run()
        finally:
            s.close()
            app.processEvents()
    print(f"{len(s.shots)} screenshots in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
