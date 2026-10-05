"""``HoldButton``: hold-to-jog button of the Manual tab (SW_design_GUI §3.4, SW-MAN-004, B §15.4 rule 5).

* Mouse press → ``jogStarted`` (the tab calls ``motion.jog_start``); the 100 ms JOG refresh is the backend's
  (Supervisor, only while ``gui_beat()`` is young).
* ``jogStopped`` (→ ``motion.jog_stop`` = JOG 0) **exactly once** per press, on whichever comes first: mouse
  release, the application becoming inactive (focus loss to another application), the window deactivating or
  hiding (tab change, minimise), the button becoming disabled (the ``jog`` gate closed), or :meth:`force_release`
  (the backend ended the jog session after a STOP / PAUSE: the button then shows released even if still held, and
  a new press is needed).
* ``NoFocus``: Space / Enter can never start a jog (P3); no auto-repeat.

Implements: SW-MAN-004 (JOG while pressed, JOG 0 on release / focus loss)
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QPushButton, QWidget


class HoldButton(QPushButton):
    jogStarted = Signal(int)            # direction +1 / -1
    jogStopped = Signal(str)            # reason: release / focus / hidden / disabled / forced

    def __init__(self, text: str, direction: int, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.direction = 1 if direction > 0 else -1
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setAutoRepeat(False)
        self.held = False                 # a jog session started by this button is active
        self.stop_reasons: list[str] = []
        self.pressed.connect(self._on_pressed)
        self.released.connect(lambda: self._stop("release"))
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state)
        self._win: QObject | None = None

    # ------------------------------------------------------------------ session
    def _on_pressed(self) -> None:
        if self.held or not self.isEnabled():
            return
        self.held = True
        self._watch_window()
        self.jogStarted.emit(self.direction)

    def _stop(self, reason: str) -> None:
        if not self.held:
            return
        self.held = False
        self.stop_reasons.append(reason)
        self.jogStopped.emit(reason)

    def force_release(self, reason: str = "forced") -> None:
        """End the session without a mouse release (backend ended the jog, e.g. STOP / PAUSE)."""
        if self.held:
            self.setDown(False)
            self._stop(reason)

    # ------------------------------------------------------------------ focus loss / hide / disable
    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        if state != Qt.ApplicationState.ApplicationActive:
            self._release_now("focus")

    def _release_now(self, reason: str) -> None:
        if self.held:
            self.setDown(False)
            self._stop(reason)

    def _watch_window(self) -> None:
        win = self.window()
        if win is not self._win:
            if self._win is not None:
                try:
                    self._win.removeEventFilter(self)
                except RuntimeError:
                    pass
            self._win = win
            win.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if obj is self._win and event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide):
            self._release_now("focus" if event.type() == QEvent.Type.WindowDeactivate else "hidden")
        return False

    def hideEvent(self, event) -> None:  # noqa: N802 - tab change / window hidden
        self._release_now("hidden")
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._release_now("disabled")
        super().changeEvent(event)
