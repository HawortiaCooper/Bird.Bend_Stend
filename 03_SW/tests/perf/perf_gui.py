"""Reference-PC performance runs PR-1…PR-5 (SW_test_plan §6.1; SRS NFR-001…004, SAF-SW-001, SW-STOP-001/002).

Runs the **real GUI** (``MainWindow`` on a real display, built exactly like ``bend_stand.gui.app.run``: native dialogs
off, pyqtgraph options, backend started before ``show()``, GUI GC policy) against the out-of-process simulator
behind the wire sniffer (``perf_sim.py``). Stimuli come from a separate process (``perf_stim.py``), host load from a
separate sampler (``perf_hostload.py``). Every process started here is recorded by PID in ``processes.log`` and
stopped by its own handle.

Setup (all phases): connect → load calibration with the simulator's weights (0 / 1 / 10 kg) → tare → wide SW load
limits (±1000 N) → ENABLE → HOME → spring specimen 10 N/mm from 20 mm → recording on → plot layout (``--plots``) →
optional motion loop (10 ↔ 60 mm at 10 mm/s, re-issued after every stop).

Phases (each optional, in this order):
* PR-1 (``--pr1-s``)   TC-NFR-001-01/-04, TC-NFR-009-01 (``--plots all600``, ``--gl off|on``, all 4 windows; v0.5.5:
  judged on content changes — refresh-tick interval + change → paint latency per pane, see ``change_metrics``): paint-to-paint interval of every pane viewport (Paint events), GUI
  refresh-tick interval, event-loop lateness (20 ms probe timer), refresh stage times.
* PR-2 (``--pr2 N``)   TC-NFR-002-01: N posted mouse presses alternating toolbar STOP / Plot-1 dock STOP;
  latency = stimulus stamp → first STOP frame through the sniffer.
* PR-3 (``--pr3 N``)   TC-NFR-003-01: N Pause presses via SendInput (real Win32 hotkey path when the backend
  reports REGISTERED / LL_HOOK; otherwise the in-process hotkey callback, flagged ``hook``); latency = stamp → first
  HALT frame through the sniffer; HALT cleared between presses.
* PR-5 (``--pr5 N``)   TC-SAF-SW-001-01 rt: N SW-limit trips (50 % PULL, 30 % PUSH, 20 % predicted TRAVEL_MAX);
  latency = Reader stamp of the tripping DATA frame → STOP write (PC wire log) and DATA → STOP through the sniffer.
* SOAK (``--soak-s``)  TC-NFR-004-01: link / recorder / pipeline loss counters, frames on the wire vs rows written,
  working set every 30 s (growth from ``--soak-ref-min`` to the end), LINK LOST diagnostics.

Usage (PowerShell, repo root):
  .venv\\Scripts\\python 03_SW\\tests\\perf\\perf_gui.py --out <dir> --plots all --pr1-s 600 --pr2 100 --pr3 100
  .venv\\Scripts\\python 03_SW\\tests\\perf\\perf_gui.py --out <dir> --plots all600 --pr1-s 600 --gl off   # TC-NFR-009-01
  .venv\\Scripts\\python 03_SW\\tests\\perf\\perf_gui.py --out <dir> --plots all600 --pr1-s 600 --gl on    # TC-NFR-009-01, GPU
  .venv\\Scripts\\python 03_SW\\tests\\perf\\perf_gui.py --out <dir> --plots four --pr1-s 600
  .venv\\Scripts\\python 03_SW\\tests\\perf\\perf_gui.py --out <dir> --plots all --pr5 100 --wire-log
  .venv\\Scripts\\python 03_SW\\tests\\perf\\perf_gui.py --out <dir> --plots all --soak-s 3600
Results: ``<dir>/results.json`` (+ ``summary.txt``, ``sniff.jsonl``, ``hostload.jsonl``, ``app.log``).

D-06: no COM port is opened (endpoint = loopback TCP sniffer). Never edits ``03_SW/src``.

Verifies: NFR-001, NFR-002, NFR-003, NFR-004, NFR-009, SAF-SW-001, SW-RT-006
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SW_ROOT = HERE.parents[1]
REPO = SW_ROOT.parent
for _p in (SW_ROOT / "src", REPO / "00_System" / "tools", SW_ROOT / "tests" / "validation", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np  # noqa: E402

PY = sys.executable
MS = 1_000_000


def now_ns() -> int:
    return time.monotonic_ns()


def pct(a: list[float] | np.ndarray, q: float) -> float:
    a = np.asarray(a, float)
    return float(np.percentile(a, q)) if a.size else float("nan")


def dist(a: list[float]) -> dict[str, float]:
    a = np.asarray(a, float)
    if not a.size:
        return {"n": 0}
    return {"n": int(a.size), "min": round(float(a.min()), 2), "p50": round(pct(a, 50), 2),
            "p95": round(pct(a, 95), 2), "p99": round(pct(a, 99), 2), "max": round(float(a.max()), 2),
            "mean": round(float(a.mean()), 2)}


# ============================================================================================== processes


def apply_render_setting(gl: str) -> dict[str, Any]:
    """TC-NFR-009-01 (D-54 b): select CPU or GPU (OpenGL) plot rendering **before** the main window exists.

    Preference: a GUI-side setting of D (``plot_dock.set_opengl`` / ``configure_pyqtgraph(<…gl…>=)``) — then the result
    counts for the product; otherwise pyqtgraph's global ``useOpenGL`` set by this harness (``via = "harness"``: the
    result is informative until the GUI offers the setting, SW_test_plan TC-NFR-009-01)."""
    import inspect  # noqa: PLC0415

    import pyqtgraph as pg  # noqa: PLC0415
    from bend_stand.gui.plots import plot_dock  # noqa: PLC0415

    on = gl == "on"
    via = "default"
    setter = getattr(plot_dock, "set_opengl", None)
    if callable(setter):
        setter(on)
        via = "gui:plot_dock.set_opengl"
    else:
        params = inspect.signature(plot_dock.configure_pyqtgraph).parameters
        name = next((n for n in params if "gl" in n.lower() or "gpu" in n.lower()), None)
        if name is not None:
            setattr(plot_dock, "_CONFIGURED", False)
            plot_dock.configure_pyqtgraph(**{name: on})
            via = f"gui:configure_pyqtgraph({name})"
        elif on:
            pg.setConfigOptions(useOpenGL=True)
            via = "harness:pg.useOpenGL"
    return {"gl": gl, "via": via, "pg_useOpenGL": bool(pg.getConfigOption("useOpenGL"))}

class Procs:
    """Child processes of this run, each stopped through its own handle (PID recorded)."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.items: dict[str, subprocess.Popen] = {}

    def log(self, name: str, pid: int, what: str) -> None:
        with open(self.out / "processes.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {name} pid {pid} {what}\n")

    def start(self, name: str, args: list[str], stdout: Any = subprocess.DEVNULL) -> subprocess.Popen:
        p = subprocess.Popen([PY, *args], stdin=subprocess.PIPE, stdout=stdout, stderr=open(self.out / f"{name}.err",
                             "a", encoding="utf-8"), text=True, cwd=str(HERE))
        self.items[name] = p
        self.log(name, p.pid, "started " + " ".join(args[:2]))
        return p

    def stop(self, name: str, timeout: float = 10.0) -> None:
        p = self.items.pop(name, None)
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)
        self.log(name, p.pid, f"stopped rc={p.returncode}")

    def stop_all(self) -> None:
        for n in list(self.items):
            self.stop(n)


class SimCtl:
    def __init__(self, port: int) -> None:
        self.port = port

    def act(self, action: str, **args: Any) -> dict:
        import socket  # noqa: PLC0415
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall((json.dumps({"action": action, **args}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        r = json.loads(buf.decode() or "{}")
        if not r.get("ok"):
            raise RuntimeError(f"sim {action} {args}: {r}")
        return r


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


# ============================================================================================== runner

class Runner:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.out = Path(a.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.procs = Procs(self.out)
        self.results: dict[str, Any] = {"args": vars(a), "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.motion_on = False
        self._motion_target = 60.0
        self._motion_hold_until = 0.0
        self.paint: dict[str, list[int]] = {}
        self.changes: dict[str, list[int]] = {}       # TC-NFR-001-01 / TC-NFR-009-01: content-change stamps per pane
        self.change_ticks = 0                          # refresh ticks seen by the change probe
        self.change_kinds: dict[str, dict[str, int]] = {}   # diagnostic: what changed (view / items / data)
        self.pane_keys: dict[str, list[str]] = {}            # channels shown per probed pane (diagnostics)
        self.loop_late: list[float] = []
        self.loop_late_t: list[tuple[int, float]] = []
        self.tick_intervals: list[float] = []
        self._tick_n = 0
        self.notes: list[str] = []
        self.tick_hist = np.zeros(2000, np.int64)          # 1 ms bins, soak tick intervals already folded

    # ---------------------------------------------------------------------------------------------- Qt helpers
    def wait_s(self, s: float) -> None:
        from PySide6.QtCore import QEventLoop, QTimer  # noqa: PLC0415
        loop = QEventLoop()
        QTimer.singleShot(max(0, int(s * 1000)), loop.quit)
        loop.exec()

    def wait_until(self, pred, timeout_s: float, step_s: float = 0.05, what: str = "") -> bool:
        t_end = time.monotonic() + timeout_s
        while time.monotonic() < t_end:
            try:
                if pred():
                    return True
            except Exception:  # noqa: BLE001
                pass
            self.wait_s(step_s)
        if what:
            self.note(f"timeout waiting for {what}")
        return False

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"[perf] {text}", flush=True)

    # ---------------------------------------------------------------------------------------------- setup
    def start_processes(self) -> None:
        args = ["perf_sim.py", "--log", str(self.out / "sniff.jsonl")]
        if self.a.log_data or self.a.pr5 or self.a.soak_s:
            args.append("--log-data")
        p = self.procs.start("sim", args, stdout=subprocess.PIPE)
        line = p.stdout.readline()
        info = json.loads(line)
        self.sim_info = info
        self.endpoint = f"tcp://127.0.0.1:{info['data']}"
        self.ctl = SimCtl(info["ctl"])
        hl = self.procs.start("hostload", ["perf_hostload.py", "--out", str(self.out / "hostload.jsonl"),
                                           "--period", str(self.a.load_period), "--own",
                                           f"{os.getpid()},{p.pid}"])
        self.results["pids"] = {"gui": os.getpid(), "sim": p.pid, "hostload": hl.pid}

    def setup_gui(self) -> None:
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        from bend_stand.core.backend import Backend, BackendSettings  # noqa: PLC0415
        from bend_stand.gui.app import _busy  # noqa: PLC0415
        from bend_stand.gui.dialogs.safe_dialog import disable_native_dialogs  # noqa: PLC0415
        from bend_stand.gui.gc_policy import GuiGcPolicy  # noqa: PLC0415

        disable_native_dialogs()
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.app.setApplicationName("Bird Bend Stand (perf)")
        from bend_stand.gui.main_window import MainWindow  # noqa: PLC0415
        from bend_stand.gui.plots.plot_dock import configure_pyqtgraph  # noqa: PLC0415
        from bend_stand.gui.settings import make_settings  # noqa: PLC0415
        configure_pyqtgraph()
        self.results["render"] = apply_render_setting(self.a.gl)   # TC-NFR-009-01: GPU off / on
        data = self.out / "data"
        data.mkdir(exist_ok=True)
        os.environ["BEND_STAND_DATA_DIR"] = str(data)
        self.be = Backend(BackendSettings(wire_log=self.a.wire_log, test_hooks=self.a.wire_log,
                                          recordings_root=str(self.out / "rec"), data_dir=str(data),
                                          hotkey=self.a.hotkey))
        self.be.start()
        self.win = MainWindow(self.be, settings=make_settings(self.out / "gui.ini"))
        self.win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)   # another app keeps the focus
        scr = self.app.primaryScreen()
        self.dpr = float(scr.devicePixelRatio())
        w, h = int(self.a.win_px[0] / self.dpr), int(self.a.win_px[1] / self.dpr)
        g = scr.availableGeometry()
        self.win.setGeometry(g.x() + 40, g.y() + 40, w, h)
        self.win.show()
        self.policy = GuiGcPolicy(self.app, defer_full=lambda: _busy(self.win))
        self.policy.install()
        self.results["display"] = {"screen": scr.name(), "logical": [scr.size().width(), scr.size().height()],
                                   "dpr": self.dpr, "window_logical": [w, h],
                                   "window_px": [round(w * self.dpr), round(h * self.dpr)],
                                   "screens": len(self.app.screens())}
        self.wait_s(0.5)
        self.win.connection_tab.connect_to(self.endpoint)
        ok = self.wait_until(lambda: str(self.be.status().link.state.value) == "CONNECTED"
                             and self.be.status().stream.on, 15, what="connect")
        if not ok:
            raise RuntimeError("connect failed")
        self.results["hotkey"] = {"mode": self.be.status().hotkey.mode, "reason": self.be.status().hotkey.reason}
        self.note(f"connected {self.endpoint}; hotkey {self.results['hotkey']}")
        logging.getLogger().setLevel(logging.WARNING)

    def calibrate(self) -> None:
        be = self.be
        lc = be.load_cal

        def wait_eng(eng, pred, timeout=60.0):
            self.wait_until(lambda: pred(eng.state()), timeout)
            return eng.state()

        g = lc.start(n_points=3, confirmed=True)
        if not g.ok:
            raise RuntimeError(f"load cal start: {g}")
        self.ctl.act("weight", kg=0.0)
        self.wait_s(3.0)
        lc.continue_()
        for i, m in enumerate((1.0, 10.0), start=1):
            wait_eng(lc, lambda s, i=i: (s.phase == "AWAIT_OPERATOR" and s.step_index == i)
                     or s.phase in ("FIT", "ABORTED") or bool(s.errors))
            self.ctl.act("weight", kg=m)
            self.wait_s(3.0)
            lc.continue_({"mass_kg": m})
        fit = wait_eng(lc, lambda s: s.phase in ("FIT", "ABORTED"))
        if fit.phase != "FIT":
            raise RuntimeError(f"load cal: {fit}")
        lc.continue_(confirmed=True)
        done = wait_eng(lc, lambda s: s.phase in ("DONE", "ABORTED"))
        self.ctl.act("weight", kg=0.0)
        self.wait_s(3.0)
        g = be.tare(None)
        if not g.ok:
            raise RuntimeError(f"tare: {g}")
        t = wait_eng(be.tare_engine, lambda s: s.phase in ("DONE", "REFUSED", "ABORTED"))
        self.wait_s(2.0)
        self.k = float(fit.result.K)
        self.tare_raw = float(t.result.tare_raw)
        self.note(f"calibrated K={self.k:.6g} N/count tare={self.tare_raw:.1f} ({done.phase}/{t.phase}); "
                  f"thresholds {be.status().safety.thresholds.state}")

    def set_limits(self, **changes: Any) -> None:
        from dataclasses import replace  # noqa: PLC0415
        issues = self.be.limits.set(replace(self.be.limits.get(), **changes))
        if issues:
            self.note(f"limits.set issues: {issues}")

    def ready_motion(self) -> None:
        be = self.be
        self.set_limits(pull_trip_n=1000.0, push_trip_n=-1000.0, pull_enabled=True, push_enabled=True)
        g = be.motion.enable()
        if not g.ok:
            raise RuntimeError(f"enable: {g}")
        self.wait_until(lambda: be.status().motion.enabled, 5, what="enabled")
        be.motion.home(load_confirmed=True)
        self.wait_until(lambda: be.status().motion.homed and not be.status().motion.moving
                        and be.device.board is not None and be.device.board.motion == "IDLE", 180, what="homed")
        self.note(f"homed; safety {be.status().safety.load_input_valid} {be.status().safety.thresholds.state}")

    def start_recording(self) -> None:
        from bend_stand.core.api import TestMarks  # noqa: PLC0415
        self.be.marks.set(TestMarks(specimen="perf", number=time.strftime("%H%M%S")))
        g = self.be.record_start()
        if not g.ok:
            raise RuntimeError(f"record_start: {g}")
        self.wait_until(lambda: self.be.status().recording.state == "RECORDING", 5, what="recording")

    # ---------------------------------------------------------------------------------------------- plots
    def configure_plots(self) -> None:
        from PySide6.QtCore import Qt  # noqa: PLC0415
        win = self.win
        p1 = win.plot_dock
        tree = p1.tree
        avail = [k for k in tree.all_keys() if tree.is_available(k)]
        unavailable = [k for k in tree.all_keys() if not tree.is_available(k)]
        mode = self.a.plots
        if mode in ("all", "all600"):
            tree.set_checked_many(avail, True)
            while len(p1.time_panes()) < 4:
                p1.add_pane()
            p1.add_xy_pane()
            p1.set_columns(2)
        elif mode == "four":
            for g in tree.group_names():
                if g.startswith("status"):
                    tree.set_group_checked(g, True)
            for k in ("raw", "x_mm", "rate_sps"):
                tree.set_checked(k, True)
            p1.add_xy_pane()
            p1.set_columns(2)
        window_s = 600.0 if mode == "all600" else 30.0
        extra = []
        n_extra = 3 if mode == "all600" else (1 if mode == "all" else 0)
        for i in range(n_extra):
            d = win.new_plot_window(f"Plot {i + 2}")
            if d is None:
                break
            keys = [k for k in ("F_N", "raw", "x_mm", "v_mm_s", "rate_sps") if k in avail][: 2 + i]
            d.tree.set_checked_many(keys, True)
            d.setFloating(True)
            pw, ph = int(self.a.win_px[0] / 2 / self.dpr), int(self.a.win_px[1] / 2 / self.dpr)
            d.resize(pw, ph)
            d.move(80 + 60 * i, 80 + 60 * i)
            d.show()
            extra.append(d)
        for d in [p1, *extra]:
            d.set_window_s(window_s)
        win.plot_dock.raise_()
        self.wait_s(1.0)
        self.docks = [p1, *extra]
        if not self.a.soak_s or self.a.pr1_s:
            self.install_paint_probes()
        self.results["plots"] = {
            "mode": mode, "window_s": window_s, "channels_available": len(avail), "channels_unavailable": unavailable,
            "plot1_checked": len(p1.checked_keys()), "plot1_panes": len(p1.panes()),
            "plot1_time_panes": len(p1.time_panes()), "plot1_xy_panes": len(p1.xy_panes()),
            "plot1_columns": p1.columns, "plot_windows": len(self.docks),
            "floating": [d.isFloating() for d in self.docks],
            "pane_px": [[round(p.width() * self.dpr), round(p.height() * self.dpr)] for p in p1.panes()],
        }
        _ = Qt
        self.note(f"plots: {self.results['plots']}")

    def install_paint_probes(self) -> None:
        from PySide6.QtCore import QEvent, QObject  # noqa: PLC0415
        runner = self

        class Probe(QObject):
            def __init__(self, key: str) -> None:
                super().__init__()
                self.key = key
                runner.paint.setdefault(key, [])

            def eventFilter(self, obj, ev) -> bool:  # noqa: N802
                if ev.type() == QEvent.Type.Paint:
                    runner.paint[self.key].append(time.perf_counter_ns())
                return False

        self._probes = []
        targets: list[tuple[str, Any]] = []
        for di, d in enumerate(self.docks):
            for pi, pane in enumerate(d.panes()):
                key = f"P{di + 1}.{pi}{'xy' if pane in d.xy_panes() else ''}"
                pr = Probe(key)
                pane.plot_widget.viewport().installEventFilter(pr)
                self._probes.append(pr)
                targets.append((key, pane))
                self.changes.setdefault(key, [])
                try:
                    self.pane_keys[key] = list(pane.keys()) if callable(getattr(pane, "keys", None)) else []
                except Exception:  # noqa: BLE001
                    self.pane_keys[key] = []
        self.install_change_probe(targets)

    def install_change_probe(self, targets: list[tuple[str, Any]]) -> None:
        """Content-change probe (TC-NFR-001-01 / TC-NFR-009-01, v0.5.5): a refresh stage registered **after** the GUI's
        own stages compares, per pane, the data arrays of every plot item and the view range with the previous tick by
        **object identity** (the previous arrays are kept referenced, so an id can never be reused). A pane whose content
        changed gets a stamp; its next viewport Paint gives the change → paint latency. Independent of the GUI's own
        skip decision: an unchanged pane (D's redraw skip, SW_design_GUI §4.8) has nothing to paint; a changed pane that
        does not repaint shows up as a long latency."""
        prev: dict[str, tuple] = {}
        runner = self

        def signature(pane: Any) -> tuple:
            items = []
            for it in pane.plot_item.items:
                xd, yd = getattr(it, "xData", None), getattr(it, "yData", None)
                if (xd is None and yd is None) or not it.isVisible():
                    continue                    # a hidden item's data needs no repaint; showing / hiding changes the set
                items.append((it, xd, yd, True))
            vb = pane.plot_item.vb
            vr = (tuple(vb.state["viewRange"][0]), tuple(vb.state["viewRange"][1]))
            return (vr, tuple(items))

        def same(a: tuple, b: tuple) -> bool:
            if a[0] != b[0] or len(a[1]) != len(b[1]):
                return False
            return all(x[0] is y[0] and x[1] is y[1] and x[2] is y[2] and x[3] == y[3] for x, y in zip(a[1], b[1]))

        def why(a: tuple, b: tuple) -> str:
            """Diagnostic kind of a change (counted per pane in ``panes_change[*].kinds``)."""
            if a[0][0] != b[0][0]:
                return "view_x"
            if a[0][1] != b[0][1]:
                return "view_y"
            if len(a[1]) != len(b[1]) or any(x[0] is not y[0] for x, y in zip(a[1], b[1])):
                return "item_set"
            for x, y in zip(a[1], b[1]):
                if x[1] is not y[1] or x[2] is not y[2]:
                    import numpy as _np  # noqa: PLC0415
                    eq = (x[1] is not None and y[1] is not None and x[2] is not None and y[2] is not None
                          and len(x[1]) == len(y[1]) and _np.array_equal(x[1], y[1])
                          and _np.array_equal(x[2], y[2], equal_nan=True))
                    return f"data_{type(y[0]).__name__}{'_same_content' if eq else ''}"
            return "other"

        def stage(_status: Any) -> None:
            t = time.perf_counter_ns()
            runner.change_ticks += 1
            for key, pane in targets:
                try:
                    sig = signature(pane)
                except Exception:  # noqa: BLE001 - pane closed
                    continue
                old = prev.get(key)
                if old is None or not same(old, sig):
                    runner.changes[key].append(t)
                    if old is not None:
                        k2 = runner.change_kinds.setdefault(key, {})
                        w = why(old, sig)
                        k2[w] = k2.get(w, 0) + 1
                prev[key] = sig

        self.win.refresh.add_stage("nfr_change_probe", stage, every=1)

    def start_loop_probe(self) -> None:
        from PySide6.QtCore import Qt, QTimer  # noqa: PLC0415
        self._lp_last = time.perf_counter()
        t = QTimer()
        t.setTimerType(Qt.TimerType.PreciseTimer)
        t.setInterval(20)

        def on() -> None:
            now = time.perf_counter()
            late = (now - self._lp_last) * 1e3 - 20.0
            self.loop_late.append(late)
            self.loop_late_t.append((time.perf_counter_ns(), round(late, 2)))
            self._lp_last = now
        t.timeout.connect(on)
        t.start()
        self._loop_timer = t

    def harvest_ticks(self) -> None:
        r = self.win.refresh
        n = r._n  # noqa: SLF001
        new = n - self._tick_n
        if new > 0:
            iv = list(r._intervals)  # noqa: SLF001
            self.tick_intervals.extend(iv[-min(new, len(iv)):])
            if self.a.soak_s and len(self.tick_intervals) > 200_000:      # bounded during a soak (memory metric)
                arr = np.asarray(self.tick_intervals, float)
                self.tick_hist += np.histogram(np.clip(arr, 0, 1999), bins=2000, range=(0, 2000))[0]
                self.tick_intervals = []
        self._tick_n = n

    def reset_measurements(self) -> None:
        for k in self.paint:
            self.paint[k] = []
        for k in self.changes:
            self.changes[k] = []
        self.change_ticks = 0
        self.change_kinds = {}
        self.loop_late = []
        self.loop_late_t = []
        self.tick_intervals = []
        self._tick_n = self.win.refresh._n  # noqa: SLF001

    def measure_window(self, seconds: float, label: str) -> dict:
        self.reset_measurements()
        t0 = now_ns()
        st0 = self.be.status()
        t_end = time.monotonic() + seconds
        perf_snaps = []
        next_snap = time.monotonic() + 60
        while time.monotonic() < t_end:
            self.wait_s(min(5.0, max(0.05, t_end - time.monotonic())))
            self.harvest_ticks()
            if time.monotonic() >= next_snap:
                perf_snaps.append(self.win.perf_stats())
                next_snap += 60
        self.harvest_ticks()
        perf_snaps.append(self.win.perf_stats())
        st1 = self.be.status()
        res: dict[str, Any] = {"label": label, "duration_s": round((now_ns() - t0) / 1e9, 1)}
        panes = {}
        for k, ts in self.paint.items():
            a = np.diff(np.asarray(ts, np.int64)) / 1e6
            panes[k] = dist(list(a))
            panes[k]["paints"] = len(ts)
            panes[k]["fps"] = round(len(ts) / max(1e-9, seconds), 1)
        res["paint_interval_ms"] = panes
        try:                                     # raw stamps (perf_counter_ns = monotonic_ns base on Windows)
            (self.out / f"raw_{label.split()[0].lower().replace('-', '')}.json").write_text(json.dumps(
                {"paint_ns": self.paint, "tick_ms": self.tick_intervals, "loop_late": self.loop_late_t}),
                encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.note(f"raw dump failed: {exc}")
        p1 = [v for k, v in panes.items() if k.startswith("P1.") and v.get("n", 0)]
        res["plot1_worst_pane_p95_ms"] = max((v["p95"] for v in p1), default=float("nan"))
        res["plot1_min_fps"] = min((v["fps"] for v in p1), default=float("nan"))
        # TC-NFR-009-01 (NFR-009): every pane of every plot window counts, the worst one decides
        allp = [v for v in panes.values() if v.get("n", 0)]
        res["all_worst_pane_p95_ms"] = max((v["p95"] for v in allp), default=float("nan"))
        res["all_min_fps"] = min((v["fps"] for v in allp), default=float("nan"))
        res["windows_measured"] = sorted({k.split(".")[0] for k, v in panes.items() if v.get("n", 0)})
        res["render"] = self.results.get("render")
        res.update(self.change_metrics(seconds))
        res["tick_interval_ms"] = dist(self.tick_intervals)
        res["event_loop_late_ms"] = dist(self.loop_late)
        res["stage_p95_ms_last"] = {k: round(v, 2) for k, v in perf_snaps[-1].items() if k.endswith("_p95_ms")}
        res["stage_max_ms_last"] = {k: round(v, 2) for k, v in perf_snaps[-1].items() if k.endswith("_max_ms")}
        res["refresh_errors"] = perf_snaps[-1].get("errors")
        res["link"] = self.link_delta(st0, st1)
        res["link_lost_diag"] = list(self.win.link_lost_diagnostics)
        res["gc"] = self.gc_text()
        return res

    def change_metrics(self, seconds: float) -> dict[str, Any]:
        """TC-NFR-001-01 / TC-NFR-009-01 (v0.5.5): judged on content changes, not on paints of unchanged panes.
        * ``change_to_paint_ms``: for every content change of every pane, the time to the pane's next viewport Paint
          (a change without any later paint in the window counts as ``unpainted``);
        * ``changing_panes``: panes whose content changed in >= 90 % of the refresh ticks — their paint-to-paint
          interval is the classic fps figure (``changing_worst_pane_p95_ms``);
        * the refresh-tick interval bounds how often changes can be pushed (``tick_interval_ms``)."""
        lat: list[float] = []
        per: dict[str, Any] = {}
        unpainted = 0
        ticks = max(1, self.change_ticks)
        changing: list[str] = []
        for key, cs in self.changes.items():
            ps = np.asarray(self.paint.get(key, []), np.int64)
            ls = []
            for t in cs:
                i = int(np.searchsorted(ps, t, side="left"))
                if i >= len(ps):
                    unpainted += 1
                    continue
                ls.append((int(ps[i]) - t) / 1e6)
            lat += ls
            frac = len(cs) / ticks
            per[key] = {"changes": len(cs), "change_frac": round(frac, 3), "changes_per_s": round(len(cs) / max(1e-9, seconds), 1),
                        "change_to_paint_ms": dist(ls), "kinds": dict(self.change_kinds.get(key, {})),
                        "channels": self.pane_keys.get(key, [])}
            if frac >= 0.9:
                changing.append(key)
        a = {k: np.diff(np.asarray(self.paint.get(k, []), np.int64)) / 1e6 for k in changing}
        worst = max((pct(np.asarray(v, float), 95) for v in a.values() if len(v)), default=float("nan"))
        judged = {k: v for k, v in per.items() if v["changes"] >= 10 and v["change_to_paint_ms"].get("n", 0)}
        wk = max(judged, key=lambda k: judged[k]["change_to_paint_ms"]["p95"], default=None)
        return {"change_to_paint_worst_pane": wk,
                "change_to_paint_worst_pane_p95_ms": judged[wk]["change_to_paint_ms"]["p95"] if wk else float("nan"),
                "change_to_paint_ms": dist(lat), "change_unpainted": unpainted, "change_ticks": self.change_ticks,
                "changing_panes": changing, "changing_worst_pane_p95_ms": round(float(worst), 2) if worst == worst
                else float("nan"), "panes_change": per}

    def gc_text(self) -> str:
        try:
            from bend_stand.gui.gc_policy import GC_TRACE  # noqa: PLC0415
            return GC_TRACE.describe()
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    @staticmethod
    def link_delta(s0, s1) -> dict:
        a, b = s0.link.stats, s1.link.stats
        keys = ("frames_ok", "data_frames", "frames_lost_fw", "frames_lost_link", "dup_frames", "crc_errors",
                "timeout_drops", "command_timeouts", "late_responses", "retries", "async_overflow")
        d = {k: getattr(b, k) - getattr(a, k) for k in keys}
        d["state"] = str(s1.link.state.value)
        d["rec_rows"] = s1.recording.rows
        d["rec_rows_lost"] = s1.recording.rows_lost
        return d

    # ---------------------------------------------------------------------------------------------- motion loop
    def motion_loop(self, on: bool) -> None:
        from PySide6.QtCore import QTimer  # noqa: PLC0415
        self.motion_on = on
        if on and not hasattr(self, "_mtimer"):
            t = QTimer()
            t.setInterval(400)
            t.timeout.connect(self._motion_tick)
            t.start()
            self._mtimer = t

    def _motion_tick(self) -> None:
        if not self.motion_on or time.monotonic() < self._motion_hold_until:
            return
        st = self.be.status()
        m = st.motion
        if m.moving or not m.enabled or not m.homed or st.safety.trips:
            return
        b = self.be.device.board
        if b is not None and (getattr(b, "motion", "IDLE") != "IDLE"):
            return
        try:
            pos = m.position_mm or 0.0
            self._motion_target = 10.0 if pos > 35.0 else 60.0
            self.be.motion.move_to(self._motion_target, speed_mm_s=self.a.speed)
        except Exception as exc:  # noqa: BLE001
            self._motion_hold_until = time.monotonic() + 2.0
            if len(self.notes) < 200:
                self.note(f"motion loop: {type(exc).__name__}: {exc}")

    # ---------------------------------------------------------------------------------------------- sniffer
    def sniff(self, since_ns: int = 0) -> list[dict]:
        return [r for r in read_jsonl(self.out / "sniff.jsonl") if r.get("t", 0) >= since_ns]

    # ---------------------------------------------------------------------------------------------- PR-2
    def stop_targets(self, quiet: bool = False) -> list[list[int]]:
        out = []
        btns = [self.win.stop_button, self.win.plot_dock.title_bar.stop_button]
        btns += [d.title_bar.stop_button for d in self.docks[1:2]]       # floating Plot 2 (own top-level window)
        self.stop_target_info = []
        for btn in btns:
            top = btn.window()
            c = btn.rect().center()
            p = btn.mapTo(top, c)
            hit = top.childAt(p)
            ok = hit is btn or (hit is not None and btn.isAncestorOf(hit))
            dpr = top.devicePixelRatioF()
            info = {"source": btn.source, "visible": btn.isVisible(), "hit_ok": ok,
                    "hit": type(hit).__name__ if hit is not None else None, "top": top.objectName() or type(top).__name__}
            self.stop_target_info.append(info)
            if ok and btn.isVisible():
                out.append([int(top.winId()), round(p.x() * dpr), round(p.y() * dpr)])
        if not quiet:
            self.note(f"STOP targets: {self.stop_target_info}")
        return out

    def run_pr2(self, n: int) -> dict:
        from bend_stand.gui import stop as gstop  # noqa: PLC0415, F401
        targets = self.stop_targets()
        self.stop_target_info_ok = [i["source"] for i in self.stop_target_info if i["hit_ok"] and i["visible"]]
        issued: list[tuple[int, str]] = []
        tok = self.be.events.subscribe("stop.issued", lambda r: issued.append((now_ns(), str(getattr(r, "payload", "")))),
                                       weak=False)
        t0 = now_ns()
        st0 = self.be.status()
        self.reset_measurements()
        out = self.out / "pr2_clicks.json"
        gate = self.out / "pr2_gate"
        tmp = self.out / "pr2_gate.tmp"
        p = self.procs.start("stim_click", ["perf_stim.py", "click", "--n", str(n), "--period", "0.5", "--jitter",
                                            "0.15", "--gate-file", str(gate), "--start-delay", "0.2", "--out", str(out)])
        written: list[list[int]] = []
        if self.a.debug_events:
            from PySide6.QtCore import QEvent, QObject  # noqa: PLC0415
            dbg: list[str] = []

            class AppF(QObject):
                def eventFilter(self, obj, ev) -> bool:  # noqa: N802
                    if ev.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                        nm = obj.objectName() if hasattr(obj, "objectName") else ""
                        dbg.append(f"{time.monotonic_ns()} {ev.type().name} {type(obj).__name__}:{nm} "
                                   f"{getattr(ev, 'position', lambda: None)()}")
                    return False
            self._appf = AppF()
            self.app.installEventFilter(self._appf)
            self.results["pr2_debug_events"] = dbg
        t_end = time.monotonic() + n * 3 + 60
        for i in range(n):                       # target re-computed right before every click (layout may move)
            tg = self.stop_targets(quiet=True)
            k = i % len(tg)
            written.append([*tg[k], k])
            tmp.write_text(json.dumps(written[-1]), encoding="utf-8")
            os.replace(tmp, gate)
            while gate.exists() and p.poll() is None and time.monotonic() < t_end:
                self.wait_s(0.01)
            self.wait_s(0.75)                    # the stimulus waits 0.35…0.65 s, then clicks
        self.wait_until(lambda: p.poll() is not None, 30)
        self.procs.stop("stim_click")
        self.results["pr2_written_targets"] = written
        self.wait_s(1.0)
        self.harvest_ticks()
        self.be.events.unsubscribe(tok)
        hist = [(t, s) for t, s in gstop.stop_history() if t >= t0]
        clicks = json.load(open(out, encoding="utf-8"))
        stops = [r for r in self.sniff(t0) if r.get("d") == "TX" and r.get("n") == "STOP"]
        lat, missing = [], 0
        to_handler: list[float] = []
        handler_to_wire: list[float] = []
        per_target: dict[int, list[float]] = {k: [] for k in range(len(targets))}
        for c in clicks:
            nxt = [s for s in stops if s["t"] >= c["t"]]
            if not nxt or (nxt[0]["t"] - c["t"]) > 1_000 * MS:
                missing += 1
                continue
            d = (nxt[0]["t"] - c["t"]) / MS
            lat.append(d)
            per_target[c["target"]].append(d)
            hp = next((t for t, _s in hist if t >= c["t"]), None)       # GUI handler entry (trigger_stop stamp)
            if hp is not None and hp <= nxt[0]["t"]:
                to_handler.append((hp - c["t"]) / MS)
                handler_to_wire.append((nxt[0]["t"] - hp) / MS)
        res = {"clicks": len(clicks), "stop_frames": len(stops), "missing": missing, "latency_ms": dist(lat),
               "per_target_ms": {self.stop_target_info_ok[k]: dist(v) for k, v in per_target.items()},
               "target_info": self.stop_target_info,
               "stop_issued_events": len(issued), "targets": targets,
               "click_to_gui_handler_ms": dist(to_handler), "gui_handler_to_wire_ms": dist(handler_to_wire),
               "gui_presses": {s: sum(1 for _t, x in hist if x == s) for s in {x for _t, x in hist}},
               "fg_is_gui": sum(1 for c in clicks if c.get("fg_pid") == os.getpid()),
               "tick_interval_ms": dist(self.tick_intervals), "link": self.link_delta(st0, self.be.status())}
        self.note(f"PR-2: {res['latency_ms']} missing {missing}")
        return res

    # ---------------------------------------------------------------------------------------------- PR-3
    def run_pr3(self, n: int) -> dict:
        from bend_stand.core import protocol_gen as pg  # noqa: PLC0415
        mode = self.be.status().hotkey.mode
        real = mode in ("REGISTERED", "LL_HOOK")
        gate = self.out / "pr3_gate"
        out = self.out / "pr3_keys.json"
        t0 = now_ns()
        st0 = self.be.status()
        self.reset_measurements()
        stamps: list[dict] = []
        halt_flag = int(pg.DataFlags.HALT)

        def halted() -> bool:
            return bool(self.be.device.last_flags & halt_flag)

        def clear_halt() -> None:
            if halted():
                fut = self.be.clear_stop_async(confirmed=True)
                self.wait_until(lambda: fut.done(), 5)
                self.wait_until(lambda: not halted(), 5)

        if real:
            gate.write_text("go")
            p = self.procs.start("stim_key", ["perf_stim.py", "key", "--n", str(n), "--period", "0.6", "--jitter",
                                              "0.2", "--gate-file", str(gate), "--out", str(out)])
            done = 0
            t_end = time.monotonic() + n * 4 + 60
            while p.poll() is None and time.monotonic() < t_end:
                if not gate.exists():          # the stimulus consumed the gate → a press follows
                    self.wait_until(halted, 2.5)
                    self.wait_s(0.3)
                    clear_halt()
                    done += 1
                    gate.write_text("go")
                self.wait_s(0.02)
            self.procs.stop("stim_key")
            stamps = json.load(open(out, encoding="utf-8"))
        else:                                  # fallback: the hotkey thread's callback, in-process
            hk = self.be.hotkey
            for i in range(n):
                self.wait_s(0.6)
                th_t: list[int] = []

                def press() -> None:
                    th_t.append(now_ns())
                    hk.on_press("hotkey")
                threading.Thread(target=press, daemon=True).start()
                self.wait_until(halted, 2.5)
                stamps.append({"i": i, "t": th_t[0] if th_t else 0, "ok": True, "hook": True})
                self.wait_s(0.3)
                clear_halt()
        try:
            gate.unlink()
        except OSError:
            pass
        self.wait_s(1.0)
        self.harvest_ticks()
        halts = [r for r in self.sniff(t0) if r.get("d") == "TX" and r.get("n") == "HALT"]
        lat, missing = [], 0
        for c in stamps:
            if not c.get("t"):
                missing += 1
                continue
            nxt = [s for s in halts if s["t"] >= c["t"]]
            if not nxt or (nxt[0]["t"] - c["t"]) > 1_000 * MS:
                missing += 1
                continue
            lat.append((nxt[0]["t"] - c["t"]) / MS)
        res = {"path": "SendInput → Win32 " + mode if real else "in-process hotkey callback (test hook)",
               "hotkey_mode": mode, "presses": len(stamps), "halt_frames": len(halts), "missing": missing,
               "latency_ms": dist(lat), "fg_is_gui": sum(1 for c in stamps if c.get("fg_pid") == os.getpid()),
               "fg_pids": sorted({c.get("fg_pid") for c in stamps if c.get("fg_pid")}),
               "tick_interval_ms": dist(self.tick_intervals), "link": self.link_delta(st0, self.be.status())}
        self.note(f"PR-3: {res['path']} {res['latency_ms']} missing {missing}")
        return res

    # ---------------------------------------------------------------------------------------------- PR-5
    def run_pr5(self, n: int) -> dict:
        import harness as H  # noqa: PLC0415
        from oracle import f_ref  # noqa: PLC0415

        from bend_stand.core import protocol_gen as pg  # noqa: PLC0415
        be = self.be
        self.motion_loop(False)
        self.wait_until(lambda: not be.status().motion.moving, 30)
        n_pull, n_push = round(n * 0.5), round(n * 0.3)
        kinds = ["PULL"] * n_pull + ["PUSH"] * n_push + ["TRAVEL_MAX"] * (n - n_pull - n_push)
        trials: list[dict] = []
        trip_events: list[Any] = []
        tok = be.events.subscribe("safety.trip", lambda r: trip_events.append(r.payload), weak=False)

        def idle() -> bool:
            st = be.status()
            b = be.device.board
            return not st.motion.moving and b is not None and b.motion == "IDLE"

        def move(x: float, v: float = 5.0) -> None:
            be.motion.move_to(x, speed_mm_s=v)
            self.wait_s(0.2)
            self.wait_until(idle, 60)

        cur = None
        for i, kind in enumerate(kinds):
            if kind != cur:                      # neutral first: no specimen, back to the start, latches clear
                self.wait_until(idle, 30)
                self.ctl.act("specimen", kind="none")
                self.set_limits(travel_max_enabled=False)
                self.wait_until(lambda: not be.status().safety.trips, 10)
                move({"PULL": 15.0, "PUSH": 35.0, "TRAVEL_MAX": 40.0}[kind])
                self.wait_until(lambda: not be.status().safety.trips, 10)
                if kind == "PULL":
                    self.set_limits(pull_trip_n=150.0, push_trip_n=-150.0, travel_max_enabled=False)
                    self.ctl.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=20_000, side="pull")
                elif kind == "PUSH":
                    self.ctl.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=30_000, side="push")
                else:
                    self.ctl.act("specimen", kind="none")
                    self.set_limits(travel_max_mm=50.0, travel_max_enabled=True)
                cur = kind
            start, end = {"PULL": (15.0, 40.0), "PUSH": (35.0, 10.0), "TRAVEL_MAX": (40.0, 60.0)}[kind]
            self.wait_until(lambda: not be.status().safety.trips, 10)
            move(start)
            self.wait_s(0.3)
            m0 = H.wire_mark(be)
            ne = len(trip_events)
            try:
                if kind == "TRAVEL_MAX":         # other-client move beyond the limit (forced path, no gate)
                    d = be.device
                    d.request(pg.Cmd.MOVE_ABS, H.rc.encode_request("MOVE_ABS", {"target_um": int(end * 1000),
                              "v_um_s": 5000, "a_um_s2": 0}), epoch=d.channel.motion_epoch)
                else:
                    be.motion.move_to(end, speed_mm_s=5.0)
            except Exception as exc:  # noqa: BLE001
                trials.append({"i": i, "kind": kind, "error": f"{type(exc).__name__}: {exc}"})
                continue
            ok = self.wait_until(lambda: len(trip_events) > ne and trip_events[-1].limit == kind, 15)
            self.wait_s(0.5)
            self.wait_until(idle, 30)
            tr = trip_events[-1] if ok else None
            rec: dict[str, Any] = {"i": i, "kind": kind, "tripped": ok}
            if tr is not None:
                w = H.wire(be, m0)
                data = [x for x in w if x.dir == "RX" and x.name == "DATA"]
                trip_frame = next((x for x in data if x.fields.get("t_us") == tr.t_us), None)
                stops = [x for x in w if x.dir == "TX" and x.name == "STOP"]
                if kind in ("PULL", "PUSH"):
                    thr = 150.0 if kind == "PULL" else -150.0
                    over = next((x for x in data if (f_ref.force_n(x.fields["afe_raw"], self.k, self.tare_raw) > thr
                                                     if kind == "PULL" else
                                                     f_ref.force_n(x.fields["afe_raw"], self.k, self.tare_raw) < thr)),
                                None)
                    rec["oracle_first_violation_is_trip_frame"] = (over is not None and trip_frame is not None
                                                                   and over.fields["frame_seq"]
                                                                   == trip_frame.fields["frame_seq"])
                    rec["value"] = round(float(tr.value), 1)
                if trip_frame is not None:
                    st = next((s for s in stops if s.t_ns >= trip_frame.t_ns), None)
                    rec["frame_seq"] = trip_frame.fields["frame_seq"]
                    rec["t_us"] = tr.t_us
                    rec["rx_t_ns"] = trip_frame.t_ns
                    if st is not None:
                        rec["pc_ms"] = round((st.t_ns - trip_frame.t_ns) / MS, 3)
                        rec["stop_t_ns"] = st.t_ns
                        rec["stop_mode"] = st.fields.get("mode")
            trials.append(rec)
            # back off: reduce the violation (allowed direction), latch clears
            try:
                move(start)
            except Exception as exc:  # noqa: BLE001
                self.note(f"PR-5 back-off {i}: {exc}")
            if (i + 1) % 10 == 0:
                self.note(f"PR-5 {i + 1}/{n}")
        be.events.unsubscribe(tok)
        # wire-to-wire through the sniffer: DATA frame (by frame_seq near the PC stamp) → next STOP
        sn = [r for r in read_jsonl(self.out / "sniff.jsonl") if r.get("d") in ("TX", "RX")]
        data_by = {}
        for r in sn:
            if r.get("n") == "DATA":
                data_by.setdefault(r["fs"], []).append(r)
        stops = [r for r in sn if r.get("d") == "TX" and r.get("n") == "STOP"]
        for rec in trials:
            if "frame_seq" not in rec:
                continue
            cands = [r for r in data_by.get(rec["frame_seq"], []) if abs(r["t"] - rec["rx_t_ns"]) < 2_000 * MS]
            if not cands:
                continue
            dt = cands[0]
            s = next((x for x in stops if x["t"] >= dt["t"]), None)
            if s is not None:
                rec["wire_ms"] = round((s["t"] - dt["t"]) / MS, 3)
        self.set_limits(pull_trip_n=1000.0, push_trip_n=-1000.0, travel_max_enabled=False)
        self.ctl.act("specimen", kind="spring", k_n_per_mm=10.0, x_contact_um=20_000, side="pull")
        pc = [t["pc_ms"] for t in trials if "pc_ms" in t]
        wire = [t["wire_ms"] for t in trials if "wire_ms" in t]
        res = {"trials": len(trials), "tripped": sum(1 for t in trials if t.get("tripped")),
               "with_stop": len(pc), "pc_reader_to_stop_write_ms": dist(pc), "wire_data_to_stop_ms": dist(wire),
               "by_kind": {k: dist([t["pc_ms"] for t in trials if t["kind"] == k and "pc_ms" in t])
                           for k in ("PULL", "PUSH", "TRAVEL_MAX")},
               "oracle_mismatch": [t["i"] for t in trials if t.get("oracle_first_violation_is_trip_frame") is False],
               "errors": [t for t in trials if "error" in t], "stop_modes": sorted({t.get("stop_mode") for t in trials
                                                                                   if "stop_mode" in t})}
        (self.out / "pr5_trials.json").write_text(json.dumps(trials, indent=1), encoding="utf-8")
        self.note(f"PR-5: pc {res['pc_reader_to_stop_write_ms']} wire {res['wire_data_to_stop_ms']}")
        return res

    # ---------------------------------------------------------------------------------------------- soak
    def soak(self, seconds: float) -> dict:
        import perf_hostload as HL  # noqa: PLC0415
        be = self.be
        if be.status().recording.state == "RECORDING":   # a fresh recording for exactly the soak window
            be.record_stop()
            self.wait_s(2.0)
        self.soak_rec_t0 = now_ns()
        self.start_recording()
        t0 = now_ns()
        st0 = be.status()
        samples = []
        self.reset_measurements()
        t_end = time.monotonic() + seconds
        next_s = time.monotonic()
        lost_seen = 0
        while time.monotonic() < t_end:
            if time.monotonic() >= next_s:
                st = be.status()
                s = st.link.stats
                ps = self.win.perf_stats()
                samples.append({"t_s": round((now_ns() - t0) / 1e9, 1), "ws_mb": round(HL.working_set() / 2**20, 2),
                                "data": s.data_frames, "lost_link": s.frames_lost_link, "lost_fw": s.frames_lost_fw,
                                "async_overflow": s.async_overflow, "rows": st.recording.rows,
                                "rows_lost": st.recording.rows_lost, "rec": st.recording.state,
                                "queue_fill": st.recording.queue_fill_pct, "link": str(st.link.state.value),
                                "tick_p95": round(ps["interval_p95_ms"], 1), "tick_max": round(ps["interval_max_ms"], 1)})
                if len(self.win.link_lost_diagnostics) > lost_seen:
                    lost_seen = len(self.win.link_lost_diagnostics)
                    self.note(f"LINK LOST during soak: {self.win.link_lost_diagnostics[-1]}")
                with open(self.out / "soak_samples.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(samples[-1]) + "\n")
                next_s += self.a.soak_sample_s
            self.wait_s(1.0)
            self.harvest_ticks()
        st1 = be.status()
        rec_folder = st1.recording.folder
        g = be.record_stop()
        self.wait_s(3.0)
        st2 = be.status()
        ref_t = self.a.soak_ref_min * 60
        ref20 = [x for x in samples if x["t_s"] >= 20 * 60]
        ref = [x for x in samples if x["t_s"] >= ref_t]
        growth = (max(x["ws_mb"] for x in ref) - ref[0]["ws_mb"]) if ref else float("nan")
        end_growth = (ref[-1]["ws_mb"] - ref[0]["ws_mb"]) if ref else float("nan")
        res = {"duration_s": round((now_ns() - t0) / 1e9, 1), "samples": len(samples),
               "link": self.link_delta(st0, st1), "record_stop_ok": bool(g.ok), "rec_folder": rec_folder,
               "rows_final": st2.recording.rows, "rows_lost_final": st2.recording.rows_lost,
               "ws_mb_start": samples[0]["ws_mb"] if samples else None,
               "ws_mb_ref": ref[0]["ws_mb"] if ref else None, "ws_mb_end": samples[-1]["ws_mb"] if samples else None,
               "ws_mb_max_after_ref": max((x["ws_mb"] for x in ref), default=None),
               "growth_max_after_ref_mb": round(growth, 2), "growth_end_vs_ref_mb": round(end_growth, 2),
               "growth_after_20min_mb": round(max(x["ws_mb"] for x in ref20) - ref20[0]["ws_mb"], 2) if ref20 else None,
               "probes_installed": bool(self._probes) if hasattr(self, "_probes") else False,
               "tick_interval_ms": hist_dist(self.tick_hist, self.tick_intervals),
               "link_lost_diag": list(self.win.link_lost_diagnostics),
               "gc": self.gc_text()}
        self.soak_rec_folder = rec_folder
        self.note(f"SOAK: {res}")
        return res

    def soak_wire_check(self, res: dict) -> None:
        """Rows in data.csv vs DATA frames through the sniffer during the recording (frame_seq set)."""
        folder = getattr(self, "soak_rec_folder", None)
        if not folder:
            return
        import csv  # noqa: PLC0415
        rows: list[tuple[int, int, int]] = []           # (t_us_u, frame_seq, t_host_ns) of every D row
        ev_rows: list[str] = []
        try:
            with open(os.path.join(folder, "data.csv"), encoding="utf-8", newline="") as fh:
                rd = csv.reader(ln for ln in fh if not ln.startswith("#"))
                hdr = next(rd)
                it, ifs, ih = hdr.index("t_us_u"), hdr.index("frame_seq"), hdr.index("t_host")
                for r in rd:
                    if r and r[0] == "D":
                        rows.append((int(r[it]), int(r[ifs]), int(r[ih])))
                    elif r and r[0] == "E":
                        ev_rows.append(",".join(r[-3:]))
            res["csv_d_rows"] = len(rows)
            res["csv_e_rows"] = len(ev_rows)
            res["csv_link_lost_rows"] = [e for e in ev_rows if "LINK" in e.upper()][:10]
        except Exception as exc:  # noqa: BLE001
            res["csv_error"] = str(exc)
        # frames on the wire (sniffer) vs D rows, keyed by the device time t_us (unique per frame within 71 min):
        # every wire frame between the record start and the last row must be in the file, and no row may be older
        # than the record start (rows of the previous recording leaking into this one)
        t0 = getattr(self, "soak_rec_t0", 0)
        wire = [(r["tu"], r["t"]) for r in read_jsonl(self.out / "sniff.jsonl") if r.get("n") == "DATA"
                and r.get("d") == "RX"]
        if rows and wire:
            keys = {k for k, _fs, _h in rows}
            after = [k for k, _fs, th in rows if th >= t0]
            first_k = after[0] if after else rows[0][0]
            last_k = rows[-1][0]
            in_win = [k for k, t in wire if first_k <= k <= last_k]
            res["wire_vs_csv"] = {"wire_frames_in_window": len(in_win), "missing_in_csv": len(set(in_win) - keys),
                                  "rows_not_on_wire": len(keys - {k for k, _t in wire}),
                                  "rows_before_record_start": sum(1 for _k, _fs, th in rows if th < t0),
                                  "first_rows": [[fs, round((th - t0) / 1e6, 1)] for _k, fs, th in rows[:4]]}

    # ---------------------------------------------------------------------------------------------- main
    def run(self) -> int:
        logging.basicConfig(level=logging.WARNING, filename=str(self.out / "app.log"),
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        rc = 0
        try:
            self.start_processes()
            self.setup_gui()
            self.calibrate()
            self.ready_motion()
            self.ctl.act("specimen", kind="spring", k_n_per_mm=10.0, x_contact_um=20_000, side="pull")
            self.start_recording()
            self.configure_plots()
            if not self.a.soak_s or self.a.pr1_s:
                self.start_loop_probe()
            self.motion_loop(self.a.motion)
            self.wait_s(3.0)
            if self.a.pr1_s:
                self.results["pr1"] = self.measure_window(self.a.pr1_s, f"PR-1 {self.a.plots}")
                self.note(f"PR-1: worst Plot-1 pane p95 {self.results['pr1']['plot1_worst_pane_p95_ms']} ms; "
                          f"tick {self.results['pr1']['tick_interval_ms']}; loop {self.results['pr1']['event_loop_late_ms']}")
            if self.a.pr2:
                self.results["pr2"] = self.run_pr2(self.a.pr2)
            if self.a.pr3:
                self.results["pr3"] = self.run_pr3(self.a.pr3)
            if self.a.pr5:
                self.results["pr5"] = self.run_pr5(self.a.pr5)
            if self.a.soak_s:
                self.results["soak"] = self.soak(self.a.soak_s)
                self.soak_wire_check(self.results["soak"])
        except Exception as exc:  # noqa: BLE001
            import traceback  # noqa: PLC0415
            self.results["error"] = f"{type(exc).__name__}: {exc}"
            self.results["traceback"] = traceback.format_exc()
            self.note(self.results["error"])
            rc = 1
        finally:
            self.results["notes"] = self.notes
            self.results["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            try:
                st = self.be.status()
                self.results["final_link"] = {"state": str(st.link.state.value), **vars(st.link.stats)}
                self.results["link_lost_diag_all"] = list(self.win.link_lost_diagnostics)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.motion_on = False
                if self.be.status().recording.state == "RECORDING":
                    self.be.record_stop()
                    self.wait_s(1.0)
                self.win._force_close = True  # noqa: SLF001
                self.win.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.be.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.policy.uninstall()
            except Exception:  # noqa: BLE001
                pass
            self.procs.stop_all()
            self.results["hostload"] = summarize_load(read_jsonl(self.out / "hostload.jsonl"))
            (self.out / "results.json").write_text(json.dumps(self.results, indent=1, default=str), encoding="utf-8")
            print(json.dumps({k: v for k, v in self.results.items() if k in ("error",)}, default=str), flush=True)
        return rc


def hist_dist(hist: np.ndarray, rest: list[float]) -> dict:
    """Distribution of folded 1-ms histogram + the not yet folded values (percentiles at 1 ms resolution)."""
    h = hist.copy()
    if rest:
        h += np.histogram(np.clip(np.asarray(rest, float), 0, 1999), bins=2000, range=(0, 2000))[0]
    n = int(h.sum())
    if not n:
        return {"n": 0}
    c = np.cumsum(h)
    q = {f"p{p}": float(np.searchsorted(c, n * p / 100.0) + 1) for p in (50, 95, 99)}
    return {"n": n, **q, "max": float(np.nonzero(h)[0].max() + 1), "resolution_ms": 1}


def summarize_load(samples: list[dict]) -> dict:
    if not samples:
        return {}
    cpu = [s["cpu_total_pct"] for s in samples]
    oth = [s["others_cpu_pct_of_host"] for s in samples]
    npy = [s["others_python"] for s in samples]
    own = {}
    for s in samples:
        for o in s.get("own", []):
            own.setdefault(o["name"] + ":" + str(o["pid"]), []).append(o["cpu_pct_of_host"] or 0.0)
    tops: dict[str, int] = {}
    for s in samples:
        for t in s.get("top", [])[:3]:
            name = t.split(":")[0]
            tops[name] = tops.get(name, 0) + 1
    return {"samples": len(samples), "cpu_total_pct": dist(cpu), "others_watch_cpu_pct": dist(oth),
            "other_python_procs": {"min": min(npy), "max": max(npy), "mean": round(sum(npy) / len(npy), 1)},
            "own_cpu_pct_of_host": {k: dist(v) for k, v in own.items()},
            "mem_avail_mb_min": min(s["mem_avail_mb"] for s in samples), "top_cpu_names": tops}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--plots", choices=("all", "all600", "four"), default="all")
    ap.add_argument("--gl", choices=("off", "on"), default="off",
                    help="TC-NFR-009-01: plot rendering through OpenGL (the GUI setting if it exists, else pyqtgraph's "
                         "useOpenGL forced by the harness; recorded in results.json 'render')")
    ap.add_argument("--pr1-s", type=float, default=0.0)
    ap.add_argument("--pr2", type=int, default=0)
    ap.add_argument("--pr3", type=int, default=0)
    ap.add_argument("--pr5", type=int, default=0)
    ap.add_argument("--soak-s", type=float, default=0.0)
    ap.add_argument("--soak-ref-min", type=float, default=15.0)
    ap.add_argument("--soak-sample-s", type=float, default=30.0)
    ap.add_argument("--wire-log", action="store_true", help="PC wire log + test hooks (PR-5)")
    ap.add_argument("--log-data", action="store_true")
    ap.add_argument("--hotkey", default="win32", choices=("win32", "fake", "off", "auto"))
    ap.add_argument("--motion", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--speed", type=float, default=10.0)
    ap.add_argument("--win-px", type=int, nargs=2, default=(1920, 1040))
    ap.add_argument("--load-period", type=float, default=5.0)
    ap.add_argument("--debug-events", action="store_true")
    a = ap.parse_args(argv)
    if a.wire_log is False and a.pr5:
        a.wire_log = True
    return Runner(a).run()


if __name__ == "__main__":
    raise SystemExit(main())
