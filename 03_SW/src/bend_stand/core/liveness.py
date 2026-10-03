"""Per-thread liveness beats and uncaught-exception capture (SW_design §4.6).

``beat(name)`` stamps a thread; ``age_ms(name)`` / ``stale(name, limit_ms)`` read it on the clock; the heartbeat
gate (Reader < 200 ms, Pipeline < 300 ms, DATA < 500 ms while streaming) and liveness faults use it.
``install_excepthook()`` turns an uncaught exception in any backend thread into a recorded fault (the
Supervisor reacts: STOP if moving, event).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/liveness.py @37c87471 (simplified: clock-based, fault list).

Implements: SAF-SW-003 (liveness-gated heartbeat), NFR-004
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from bend_stand.core.clock import MONOTONIC, Clock

#: thresholds of §4.6 (ms): fault when exceeded (while moving: STOP)
FAULT_LIMITS_MS: dict[str, int] = {"reader": 100, "pipeline": 200, "runner": 300, "gui": 2000}
#: heartbeat gate (ms)
GATE_LIMITS_MS: dict[str, int] = {"reader": 200, "pipeline": 300}


@dataclass(frozen=True)
class LivenessFault:
    thread: str
    reason: str
    t_ns: int


class LivenessMonitor:
    def __init__(self, clock: Clock = MONOTONIC) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._beats: dict[str, int] = {}
        self._faults: list[LivenessFault] = []
        self._prev_hook: Callable | None = None
        self.on_fault: Callable[[LivenessFault], None] | None = None

    def beat(self, name: str, now_ns: int | None = None) -> None:
        t = self.clock.monotonic_ns() if now_ns is None else now_ns
        with self._lock:
            self._beats[name] = t

    def forget(self, name: str) -> None:
        with self._lock:
            self._beats.pop(name, None)

    def age_ms(self, name: str, now_ns: int | None = None) -> float | None:
        with self._lock:
            t = self._beats.get(name)
        if t is None:
            return None
        now = self.clock.monotonic_ns() if now_ns is None else now_ns
        return (now - t) / 1e6

    def stale(self, name: str, limit_ms: float, now_ns: int | None = None) -> bool:
        """True when the thread has beaten before and its last beat is older than ``limit_ms``."""
        a = self.age_ms(name, now_ns)
        return a is not None and a > limit_ms

    def gate_open(self, now_ns: int | None = None) -> bool:
        """Heartbeat gate: Reader and Pipeline alive (threads that never beat are not required)."""
        return not any(self.stale(n, lim, now_ns) for n, lim in GATE_LIMITS_MS.items())

    def record_fault(self, thread: str, reason: str) -> LivenessFault:
        f = LivenessFault(thread, reason, self.clock.monotonic_ns())
        with self._lock:
            self._faults.append(f)
            del self._faults[:-100]
        cb = self.on_fault
        if cb is not None:
            try:
                cb(f)
            except Exception:  # noqa: BLE001 pragma: no cover
                pass
        return f

    def faults(self) -> list[LivenessFault]:
        with self._lock:
            return list(self._faults)

    def install_excepthook(self) -> None:
        """Uncaught exceptions in any thread become liveness faults (§4.6)."""
        if self._prev_hook is not None:
            return
        self._prev_hook = threading.excepthook

        def hook(args: threading.ExceptHookArgs) -> None:
            name = args.thread.name if args.thread is not None else "?"
            self.record_fault(name, f"uncaught {args.exc_type.__name__}: {args.exc_value}")
            if self._prev_hook is not None:
                self._prev_hook(args)

        threading.excepthook = hook

    def uninstall_excepthook(self) -> None:
        if self._prev_hook is not None:
            threading.excepthook = self._prev_hook
            self._prev_hook = None
