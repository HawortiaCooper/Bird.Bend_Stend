"""Process-wide dispatcher for the on-screen STOP buttons (SW_design_GUI §5.2).

Every STOP button (toolbar, dock title bars, every SafeDialog / SafeMessageBox / SafeFileDialog) calls
:func:`trigger_stop`. The main window installs the handler, which calls ``backend.stop(source)`` **directly in
the GUI thread** (B §15.4 rule 7, non-blocking ≤ 5 ms, priority TX path). The system-wide Pause/Break key does
not use this path (backend hotkey thread, §5.3).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/estop.py @37c87471 (renamed E-STOP → STOP: on this stand the
on-screen button is an operational stop, the E-stop is the hardware power cut, GQ-18 / D-36).

Implements: SW-STOP-001 (on-screen STOP path), IF-011 (GUI part: synchronous, never queued)
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

StopHandler = Callable[[str], None]

_handler: StopHandler | None = None
_history: list[tuple[int, str]] = []          # (monotonic_ns, source) of the last presses (tests, log)
_HISTORY_MAX = 100


def set_stop_handler(handler: StopHandler | None) -> StopHandler | None:
    """Install the handler (``handler(source)``); returns the previous one."""
    global _handler
    prev, _handler = _handler, handler
    return prev


def stop_handler() -> StopHandler | None:
    return _handler


def trigger_stop(source: str = "button") -> None:
    """Request a STOP. Never raises (a failing handler is logged at CRITICAL)."""
    _history.append((time.monotonic_ns(), source))
    del _history[:-_HISTORY_MAX]
    handler = _handler
    if handler is None:
        log.critical("STOP pressed (%s) but no backend handler is installed", source)
        return
    try:
        handler(source)
    except Exception:  # noqa: BLE001 - a STOP press must never raise into Qt
        log.critical("STOP handler failed (source %s)", source, exc_info=True)


def stop_history() -> list[tuple[int, str]]:
    """Copy of the recent presses (monotonic_ns, source)."""
    return list(_history)
