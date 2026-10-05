"""``HotkeyTestDialog`` — Tools ▸ Test Pause/Break key… (SW_design_GUI §5.3, A-04, B4-07).

* Shows the hotkey mode (``status().hotkey``: REGISTERED / LL_HOOK / UNAVAILABLE + reason) and the **KL-01** text.
* [Start] → gate ``hotkey_test`` (REFUSE shown: moving, operation, unavailable) → ``backend.hotkey_test_start(10)``
  → "Press Pause/Break now (n s)…". During the window every motion gate refuses and the key does **not** send HALT
  (B §15.6); the dialog says so.
* The result arrives on the topic ``hotkey.test`` (bridge ``hotkeyEvent``): measured key → callback delay in ms, or
  ``None`` = no key press within the window.
* Non-modal; STOP in the top bar (SafeDialog).

Implements: SW-STOP-002 (hotkey test mode, KL-01 shown), NFR-003 (delay shown, informative)
"""
from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from bend_stand.core.api import GateId
from bend_stand.gui import gating
from bend_stand.gui.dialogs.safe_dialog import SafeDialog
from bend_stand.gui.indicator_map import KL01_TEXT

TEST_WINDOW_S = 10.0


class HotkeyTestDialog(SafeDialog):
    def __init__(self, backend: Any, bridge: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="Test Pause/Break key")
        self.setObjectName("hotkeyTestDialog")
        self.setModal(False)
        self._backend = backend
        self.running = False
        self.result_text = ""
        self._t0 = 0.0
        lay = QVBoxLayout()
        self.mode_label = QLabel("Mode: ?", self)
        self.mode_label.setObjectName("hotkeyMode")
        self.mode_label.setWordWrap(True)
        lay.addWidget(self.mode_label)
        self.prompt = QLabel("Press [Start], then the Pause/Break key within 10 s. During the test motion is refused "
                             "and the key does NOT send HALT.", self)
        self.prompt.setObjectName("hotkeyPrompt")
        self.prompt.setWordWrap(True)
        lay.addWidget(self.prompt)
        self.result = QLabel("", self)
        self.result.setObjectName("hotkeyResult")
        self.result.setWordWrap(True)
        f = self.result.font()
        f.setBold(True)
        self.result.setFont(f)
        lay.addWidget(self.result)
        kl = QLabel("(i) " + KL01_TEXT, self)
        kl.setObjectName("kl01")
        kl.setWordWrap(True)
        kl.setStyleSheet("color: #404040;")
        lay.addWidget(kl)
        row = QHBoxLayout()
        row.addStretch(1)
        self.start_button = QPushButton("Start", self)
        self.start_button.setObjectName("hotkeyTestStart")
        self.close_button = QPushButton("Close", self)
        self.close_button.setObjectName("hotkeyTestClose")
        for b in (self.start_button, self.close_button):
            b.setAutoDefault(False)
            b.setDefault(False)
            row.addWidget(b)
        self.start_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # Space must not start it while waiting for Pause
        self.start_button.clicked.connect(self.start_test)
        self.close_button.clicked.connect(self.close)
        lay.addLayout(row)
        self.setLayout(lay)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        bridge.hotkeyEvent.connect(self._on_hotkey_event)
        self.resize(480, 220)
        self.update_status(backend.status())

    def update_status(self, st: Any) -> None:
        hk = st.hotkey
        txt = f"Mode: {hk.mode} ({hk.reason})" + ("  — test running" if hk.test_running else "")
        if self.mode_label.text() != txt:
            self.mode_label.setText(txt)
        cs = gating.control_state(gating.gate_of(st, GateId.HOTKEY_TEST), motion=True,
                                  base_tooltip="Arm the 10 s Pause/Break key test")
        self.start_button.setEnabled(cs.enabled and not self.running)
        self.start_button.setToolTip(cs.tooltip)

    def start_test(self) -> None:
        g = self._backend.hotkey_test_start(TEST_WINDOW_S)
        if not g.ok:
            self.result_text = "Test refused: " + gating.refusal_text(g)
            self.result.setText(self.result_text)
            self.result.setStyleSheet("color: #a00000;")
            return
        self.running = True
        self._t0 = time.monotonic()
        self.result_text = ""
        self.result.setText("")
        self.start_button.setEnabled(False)
        self._timer.start()
        self._tick()

    def _tick(self) -> None:
        if not self.running:
            return
        left = TEST_WINDOW_S - (time.monotonic() - self._t0)
        if left < -2.0:                     # the backend's result is late: stop waiting (shown as no result)
            self._finish(None, late=True)
            return
        self.prompt.setText(f"Press Pause/Break now … {max(0.0, left):.0f} s left. During the test motion is refused "
                            "and the key does NOT send HALT.")

    def _on_hotkey_event(self, record: Any) -> None:
        if record.topic != "hotkey.test" or not self.running:
            return
        p = record.payload
        delay = p if isinstance(p, (int, float)) or p is None else getattr(p, "delay_ms", None)
        self._finish(delay)

    def _finish(self, delay_ms: float | None, late: bool = False) -> None:
        self.running = False
        self._timer.stop()
        if delay_ms is None:
            self.result_text = ("Result: no key press within 10 s" if not late
                                else "Result: no result from the backend (test window over)")
            self.result.setStyleSheet("color: #805000;")
        else:
            self.result_text = f"Result: key → callback {float(delay_ms):.1f} ms"
            self.result.setStyleSheet("color: #206020;")
        self.result.setText(self.result_text)
        self.prompt.setText("Test finished. [Start] runs it again.")
        self.update_status(self._backend.status())
