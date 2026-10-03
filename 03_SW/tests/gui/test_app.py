"""G-43: entry point ``gui.app.run(backend, args) -> int`` (SW_design_GUI §8.1, B3-20).

``run()`` executes the Qt event loop and quits the application, so every scenario runs in a fresh offscreen
process (running ``QApplication.exec()`` inside the pytest-qt process would stop later tests' event loops).

Verifies: SW-PLT-001, SW-STOP-001, SW-STOP-002, SYS-008
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SW = Path(__file__).resolve().parents[2]

SCRIPT = r"""
import argparse, json, sys
sys.path[:0] = [r'%(src)s', r'%(gui)s']
from PySide6 import QtWidgets
from PySide6.QtCore import QCoreApplication, Qt
native_off_at_qapp = []
class App(QtWidgets.QApplication):
    def __init__(self, argv):
        native_off_at_qapp.append(QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs))
        super().__init__(argv)
QtWidgets.QApplication = App
from fakes import FakeBackend
from bend_stand.gui import app, main_window
fake = FakeBackend()
started_at_show = []
orig_show = main_window.MainWindow.show
def show(self):
    started_at_show.append(fake.started)
    orig_show(self)
main_window.MainWindow.show = show
endpoint = %(endpoint)r
rc = app.run(fake, argparse.Namespace(endpoint=endpoint, quit_after_ms=150))
print("RESULT " + json.dumps({"rc": rc, "native_off_at_qapp": native_off_at_qapp,
                               "started_at_show": started_at_show, "calls": fake.call_names(),
                               "shutdowns": fake.shutdowns, "connect": [c.args for c in fake.calls_of("connect_async")]}))
"""


def _run(tmp_path: Path, endpoint: str | None) -> dict:
    code = SCRIPT % {"src": SW / "src", "gui": SW / "tests" / "gui", "endpoint": endpoint}
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", BEND_STAND_GUI_SETTINGS=str(tmp_path / "gui.ini"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, r.stdout + r.stderr
    return json.loads(line[0][len("RESULT "):])


@pytest.mark.req("SW-PLT-001", "SW-STOP-002", "SW-STOP-001")
def test_run_starts_before_show_and_shuts_down(tmp_path) -> None:
    """Verifies: SW-PLT-001, SW-STOP-002, SW-STOP-001 — native dialogs disabled before the QApplication exists;
    backend.start() before the window is shown (hotkey active before Connect); no connect without args.endpoint
    (D-06); shutdown on close; returns the exit code."""
    res = _run(tmp_path, None)
    assert res["rc"] == 0
    assert res["native_off_at_qapp"] == [True]
    assert res["started_at_show"] == [True]
    assert res["calls"][0] == "start" and res["connect"] == []
    assert res["shutdowns"] >= 1


@pytest.mark.req("SW-PLT-001", "SYS-008")
def test_run_connects_to_endpoint_after_show(tmp_path) -> None:
    """Verifies: SYS-008 — ``--sim`` (args.endpoint = "sim") connects after the window is shown."""
    res = _run(tmp_path, "sim")
    assert res["rc"] == 0 and res["connect"] == [["sim"]]
    assert res["calls"].index("start") < res["calls"].index("connect_async")
