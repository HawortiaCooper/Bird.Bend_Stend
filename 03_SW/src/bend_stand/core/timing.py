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


# ============================================================================================ GC timing (MC3-4)

class GcWatch:
    """``gc.callbacks`` timing of every collection (MC3-4 / OBS-M3-R1: diagnose a false LINK LOST under host load).
    Keeps the last ``maxlen`` collections ``(t_end_ns, generation, duration_ms, collected)`` on the real clock."""

    def __init__(self, maxlen: int = 256) -> None:
        import collections  # noqa: PLC0415

        self.events: collections.deque[tuple[int, int, float, int]] = collections.deque(maxlen=maxlen)
        self._t0: int | None = None
        self._installed = False

    def _cb(self, phase: str, info: dict[str, Any]) -> None:
        t = time.perf_counter_ns()
        if phase == "start":
            self._t0 = t
        elif self._t0 is not None:
            self.events.append((time.monotonic_ns(), int(info.get("generation", -1)), (t - self._t0) / 1e6,
                                int(info.get("collected", 0))))
            self._t0 = None

    def install(self) -> None:
        import gc  # noqa: PLC0415

        if not self._installed:
            gc.callbacks.append(self._cb)
            self._installed = True

    def uninstall(self) -> None:
        import gc  # noqa: PLC0415

        if self._installed:
            try:
                gc.callbacks.remove(self._cb)
            except ValueError:  # pragma: no cover
                pass
            self._installed = False

    def summary(self, window_ns: int, now_ns: int | None = None) -> dict[str, Any]:
        """Collections in the last ``window_ns`` (monotonic): count, total and longest pause (generation, age)."""
        now = time.monotonic_ns() if now_ns is None else now_ns
        ev = [e for e in list(self.events) if now - e[0] <= window_ns]
        if not ev:
            return {"gc_count": 0, "gc_total_ms": 0.0, "gc_max_ms": 0.0}
        m = max(ev, key=lambda e: e[2])
        return {"gc_count": len(ev), "gc_total_ms": round(sum(e[2] for e in ev), 3), "gc_max_ms": round(m[2], 3),
                "gc_max_gen": m[1], "gc_max_age_ms": round((now - m[0]) / 1e6, 1)}
