"""GUI entry point ``run(backend, args) -> int`` (SW_design_GUI §8.1, A-25, B3-20).

``bend_stand.__main__.main()`` (owner B) builds the ``Backend`` **unstarted** and calls this function:

1. ``AA_DontUseNativeDialogs`` before the ``QApplication`` exists (every file dialog carries STOP);
2. ``QApplication`` (or the existing instance), pyqtgraph options;
3. ``backend.start()`` **before** the main window is shown (Pause/Break hotkey and liveness active before
   Connect, B §15.4 rule 1);
4. ``MainWindow(backend)``, ``show()``, then the GUI-thread GC policy (SWD-PM3-07);
5. if ``args.endpoint`` (str | None) is set, connect after ``show()``; otherwise the GUI starts disconnected
   (D-06: no port is opened without the operator's choice);
6. ``app.exec()``; ``backend.shutdown()`` from ``closeEvent`` and again (idempotent) after ``exec()``; return code.

Optional ``args.quit_after_ms`` (int) or the environment variable ``BEND_STAND_GUI_QUIT_AFTER_MS`` (smoke runs of
``python -m bend_stand --sim``) close the window after that many ms; the exit summary (link, stream, refresh
statistics) is logged at INFO.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/app.py @37c87471 (adapted: backend handed in by
``__main__`` unstarted, endpoint from ``args.endpoint``, no argparse here).

Implements: SW-PLT-001 (application start), SW-STOP-001 (native dialogs disabled at start), SW-STOP-002 (hotkey
active before Connect), SYS-008 (``--sim`` endpoint connect)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

log = logging.getLogger("bend_stand.gui")


def run(backend: Any, args: argparse.Namespace) -> int:
    """Start the backend, show the main window, run the Qt event loop; returns the exit code."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from bend_stand.gui.dialogs.safe_dialog import disable_native_dialogs
    from bend_stand.gui.gc_policy import GuiGcPolicy

    disable_native_dialogs()                                  # (1) before QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])          # (2)
    app.setApplicationName("Bird Bend Stand")
    app.setOrganizationName("BirdBendStand")
    from bend_stand.gui.resources import app_icon
    icon = app_icon()                                         # F-B-PKG-01: window + taskbar icon (SW-PLT-001)
    if not icon.isNull():
        app.setWindowIcon(icon)

    from bend_stand.gui.main_window import MainWindow
    from bend_stand.gui.plots.plot_dock import configure_pyqtgraph
    from bend_stand.gui.settings import make_settings

    configure_pyqtgraph()
    backend.start()                                           # (3) before the window is shown
    win = MainWindow(backend, settings=make_settings())
    win.closed.connect(app.quit)
    win.show()                                                # (4)
    policy = GuiGcPolicy(app, defer_full=lambda: _busy(win))
    policy.install()
    endpoint = getattr(args, "endpoint", None)
    if endpoint:                                              # (5)
        win.connection_tab.connect_to(str(endpoint))
    quit_after = int(getattr(args, "quit_after_ms", 0) or os.environ.get("BEND_STAND_GUI_QUIT_AFTER_MS") or 0)
    if quit_after > 0:
        QTimer.singleShot(quit_after, win.close)
    try:
        rc = int(app.exec())                                  # (6)
    finally:
        try:
            backend.shutdown()                                # idempotent
        except Exception:  # noqa: BLE001
            log.exception("backend.shutdown failed")
        policy.uninstall()
    log.info("GUI exit rc=%d; %s", rc, getattr(win, "exit_summary", ""))
    return rc


def _busy(win: Any) -> bool:
    """GC deferral predicate: no long collection while the axis moves or an operation runs (SWD-M4R1-07)."""
    st = getattr(win, "last_status", None)
    if st is None:
        return False
    return bool(st.motion.moving or st.operation.owner not in ("MANUAL", None))
