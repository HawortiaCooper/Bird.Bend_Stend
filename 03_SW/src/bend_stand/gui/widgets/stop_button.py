"""Red on-screen STOP button used everywhere (toolbar, docks, dialogs) — SW_design_GUI §5.2.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/widgets/estop_button.py @37c87471 (changes: label "STOP",
object name ``stopButton``, dispatcher ``gui.stop.trigger_stop``, ``large`` keyword, tooltip per D-36: no physical
STOP/BREAK button any more; the red mushroom button is the E-stop).

Implements: SW-STOP-001 (STOP never takes focus, never default, acts on mouse press — GQ-20)
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from bend_stand.gui.stop import trigger_stop
from bend_stand.gui.theme import STOP_RED, STOP_RED_DARK

STOP_OBJECT_NAME = "stopButton"

_STYLE = f"""
QPushButton#stopButton {{
    background-color: {STOP_RED}; color: white; font-weight: bold;
    border: 2px solid {STOP_RED_DARK}; border-radius: 4px; padding: 2px 10px;
}}
QPushButton#stopButton:hover {{ background-color: #e00000; }}
QPushButton#stopButton:pressed {{ background-color: #800000; }}
"""

STOP_TOOLTIP = ("STOP: immediate stop, driver keeps holding, sequence terminated.\n"
                "Keyboard: Pause/Break (HALT, latched). Hardware: the red E-stop button (MCU stop: pulses off, "
                "driver disabled, re-home needed).")


class StopButton(QPushButton):
    """STOP push button. ``large`` = toolbar / Manual-tab variant, otherwise compact (docks, dialogs).

    Never takes the keyboard focus (Space/Enter never hit it), is never the default button, and calls
    :func:`trigger_stop` on the mouse **press** (no wait for the release; a press dragged off still stops).
    """

    def __init__(self, parent: QWidget | None = None, *, large: bool = False, source: str = "button") -> None:
        super().__init__("STOP", parent)
        self.setObjectName(STOP_OBJECT_NAME)
        self._source = source
        self.setStyleSheet(_STYLE)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setToolTip(STOP_TOOLTIP)
        if large:
            self.setMinimumHeight(48)
            self.setMinimumWidth(140)
            font = self.font()
            font.setPointSize(font.pointSize() + 6)
            self.setFont(font)
        else:
            self.setMinimumHeight(26)
            self.setMinimumWidth(72)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.pressed.connect(self._on_pressed)

    @property
    def source(self) -> str:
        return self._source

    def _on_pressed(self) -> None:
        trigger_stop(self._source)
