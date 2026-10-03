"""Clock abstraction (SW_design §12.6, §3.3 rule 8). All backend time comes from ``Clock.monotonic_ns()``
(integer ns, never accumulated floats); ``time.*`` is used only here and in ``timing`` (grep test).

* ``MonotonicClock`` / ``MONOTONIC`` — the real clock.
* ``LockstepClock`` — virtual time advanced by the test (``advance``); the whole backend runs on it with
  ``BackendSettings(clock="lockstep")`` (hook (a) of §12.4), deterministic, no sleeps, no wall clock.
  ``FakeClock`` is the same class (unit tests).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/clock.py @37c87471 (adapted: lockstep flag, wait on a
condition for real-time waits, ``is_lockstep``).

Implements: SW-PLT-002 (testability), NFR-004 (integer time base)
"""
from __future__ import annotations

import threading
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    is_lockstep: bool

    def monotonic_ns(self) -> int: ...

    def wall_ns(self) -> int: ...


class MonotonicClock:
    """Real clock: ``time.monotonic_ns`` / ``time.time_ns``."""

    is_lockstep = False

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def wall_ns(self) -> int:
        return time.time_ns()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


MONOTONIC = MonotonicClock()


class LockstepClock:
    """Manually advanced virtual clock. ``sleep()`` advances the clock instead of waiting."""

    is_lockstep = True

    def __init__(self, start_ns: int = 1_000_000_000, wall_start_ns: int = 1_790_000_000_000_000_000) -> None:
        self._t = int(start_ns)
        self._wall0 = int(wall_start_ns) - int(start_ns)
        self._lock = threading.Lock()

    def monotonic_ns(self) -> int:
        with self._lock:
            return self._t

    def wall_ns(self) -> int:
        with self._lock:
            return self._t + self._wall0

    def advance(self, seconds: float = 0.0, *, ns: int | None = None) -> int:
        step = int(ns) if ns is not None else int(round(seconds * 1e9))
        if step < 0:
            raise ValueError("clock cannot go backwards")
        with self._lock:
            self._t += step
            return self._t

    def set(self, t_ns: int) -> None:
        with self._lock:
            if t_ns < self._t:
                raise ValueError("clock cannot go backwards")
            self._t = int(t_ns)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.advance(seconds)


FakeClock = LockstepClock


def ms(ns: int) -> int:
    """ns → integer ms (floor)."""
    return ns // 1_000_000


def wall_utc_iso(clock: Clock) -> str:
    """ISO-8601 UTC time of the clock's wall time (file names, sidecars)."""
    import datetime as _dt  # noqa: PLC0415

    return _dt.datetime.fromtimestamp(clock.wall_ns() / 1e9, tz=_dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
