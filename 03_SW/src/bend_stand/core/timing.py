"""Windows timer resolution, thread priority and absolute-deadline ticking (SW_design §1, §3.4).

* ``init()`` → ``sys.setswitchinterval(0.001)`` + ``winmm.timeBeginPeriod(1)`` (paired with ``shutdown()``), so
  waits get ~1 ms granularity instead of 15.6 ms (TS-SWD §3.1). Per process, released at exit.
* ``Ticker(period_ns, clock)`` — absolute deadlines ``t0 + k·period`` (no drift); overruns skip and are counted.
* ``measure_sleep_granularity()`` — start-up self-test (p95 of ``sleep(1 ms)``; > 3 ms → warning event).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/timing.py @37c87471 (trimmed: Ticker simplified).

Implements: NFR-002/003 (timer resolution), SAF-SW-003 (heartbeat period accuracy)
"""
from __future__ import annotations

import atexit
import ctypes
import sys
import threading
import time
from typing import Any

from bend_stand.core.clock import MONOTONIC, Clock

TIMER_P95_WARN_MS = 3.0
SWITCH_INTERVAL_S = 0.001
_state_lock = threading.Lock()
_active = False
_winmm: Any = None
_prev_switch: float | None = None


def init(winmm: Any = None) -> bool:
    """Request 1 ms timer resolution (idempotent). Returns True if active. ``winmm`` injectable for tests."""
    global _active, _winmm, _prev_switch
    with _state_lock:
        if _prev_switch is None:
            _prev_switch = sys.getswitchinterval()
            sys.setswitchinterval(SWITCH_INTERVAL_S)
        if _active:
            return True
        lib = winmm
        if lib is None and sys.platform == "win32":
            try:
                lib = ctypes.WinDLL("winmm")  # type: ignore[attr-defined]
            except OSError:  # pragma: no cover
                lib = None
        if lib is None or lib.timeBeginPeriod(1) != 0:
            return False
        _winmm, _active = lib, True
        atexit.register(shutdown)
        return True


def shutdown() -> None:
    global _active, _winmm, _prev_switch
    with _state_lock:
        if _prev_switch is not None:
            sys.setswitchinterval(_prev_switch)
            _prev_switch = None
        if _active and _winmm is not None:
            _winmm.timeEndPeriod(1)
        _active, _winmm = False, None


def is_active() -> bool:
    return _active


def raise_thread_priority(level: int = 1) -> bool:
    """Raise the calling thread's OS priority (1 = ABOVE_NORMAL, 2 = HIGHEST); never raises."""
    if sys.platform != "win32":  # pragma: no cover
        return False
    try:
        k32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
        k32.GetCurrentThread.restype = ctypes.c_void_p
        k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        return bool(k32.SetThreadPriority(k32.GetCurrentThread(), level))
    except (OSError, AttributeError):  # pragma: no cover
        return False


def measure_sleep_granularity(n: int = 50) -> float:
    """p95 of ``time.sleep(0.001)`` in ms (start-up self-test, §3.4)."""
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        time.sleep(0.001)
        samples.append((time.perf_counter() - t0) * 1e3)
    samples.sort()
    return samples[min(len(samples) - 1, int(0.95 * len(samples)))]


class Ticker:
    """Absolute-deadline periodic schedule. ``due(now)`` → number of periods elapsed since the last call (0 = not
    yet); overruns (> 1 period missed) are counted in ``overruns``. ``wait(stop_event)`` sleeps to the next
    deadline on the real clock (threaded mode)."""

    def __init__(self, period_ns: int, clock: Clock = MONOTONIC) -> None:
        if period_ns <= 0:
            raise ValueError("period must be > 0")
        self.period_ns = int(period_ns)
        self.clock = clock
        self.next_ns = clock.monotonic_ns() + self.period_ns
        self.overruns = 0
        self.ticks = 0

    def due(self, now_ns: int | None = None) -> int:
        now = self.clock.monotonic_ns() if now_ns is None else now_ns
        if now < self.next_ns:
            return 0
        k = 1 + (now - self.next_ns) // self.period_ns
        if k > 1:
            self.overruns += int(k - 1)
        self.next_ns += k * self.period_ns
        self.ticks += 1
        return int(k)

    def wait(self, stop: threading.Event) -> bool:
        """Wait until the next deadline (real clock). Returns False when ``stop`` was set."""
        delay = (self.next_ns - self.clock.monotonic_ns()) / 1e9
        if delay > 0 and stop.wait(delay):
            return False
        return not stop.is_set()
