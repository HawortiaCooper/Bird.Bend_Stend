"""``RefreshScheduler``: the single GUI refresh timer (SW_design_GUI §4.6 rule 9, §9.3 T6).

One ``QTimer`` with ``Qt.PreciseTimer`` (33 ms → 30 fps nominal; a coarse timer fires every 46.9 ms on Windows,
SWD-PM3-06). Each tick: ``backend.gui_beat()`` (liveness, T6), one ``backend.status()`` read, then the registered
stages (indicators/banners/gates every tick, plots every tick, readouts every 3rd tick, link line every 30th).
Frame intervals and per-stage durations go into ring buffers; :meth:`perf_stats` exposes p50/p95 for the
performance overlay and the perf tests.

Implements: NFR-001 (refresh budget measurement), SAF-SW-005 (indicators refreshed every tick ≤ 200 ms),
SW-MAN-004 (``gui_beat`` on every tick, backend jog dead-man)
"""
from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer

log = logging.getLogger(__name__)

TICK_MS = 33
RING = 900


class RefreshScheduler(QObject):
    def __init__(self, backend: Any, parent: QObject | None = None, interval_ms: int = TICK_MS) -> None:
        super().__init__(parent)
        self._backend = backend
        self._stages: list[tuple[str, Callable[[Any], None], int]] = []
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(int(interval_ms))
        self._timer.timeout.connect(self.tick)
        self._n = 0
        self._last_t: float | None = None
        self._intervals: deque[float] = deque(maxlen=RING)
        self._tick_times: deque[float] = deque(maxlen=RING)       # perf_counter of each tick (gap diagnostics)
        self._durations: dict[str, deque[float]] = {}
        self.last_status: Any = None
        self._errors: dict[str, int] = {}

    def add_stage(self, name: str, fn: Callable[[Any], None], every: int = 1) -> None:
        self._stages.append((name, fn, max(1, int(every))))
        self._durations[name] = deque(maxlen=RING)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    @property
    def interval_ms(self) -> int:
        return self._timer.interval()

    def timer_type(self) -> Qt.TimerType:
        return self._timer.timerType()

    def tick(self) -> None:
        now = time.perf_counter()
        if self._last_t is not None:
            self._intervals.append((now - self._last_t) * 1e3)
        self._last_t = now
        self._tick_times.append(now)
        self._n += 1
        try:
            self._backend.gui_beat()
        except Exception:  # noqa: BLE001 - T9
            log.debug("gui_beat failed", exc_info=True)
        try:
            status = self._backend.status()
        except Exception:  # noqa: BLE001
            log.warning("status() failed", exc_info=True)
            return
        self.last_status = status
        for name, fn, every in self._stages:
            if self._n % every:
                continue
            t0 = time.perf_counter()
            try:
                fn(status)
            except Exception:  # noqa: BLE001 - T9: a display defect never stops the refresh
                n = self._errors.get(name, 0) + 1
                self._errors[name] = n
                if n <= 3:
                    log.exception("refresh stage %s failed", name)
            self._durations[name].append((time.perf_counter() - t0) * 1e3)

    def max_gap(self, window_s: float = 10.0, now: float | None = None) -> tuple[float, float]:
        """(longest refresh-tick gap in ms within the last ``window_s``, seconds since that gap ended); the gap
        still open since the last tick counts too (MC3-4 diagnostics)."""
        now = time.perf_counter() if now is None else now
        ts = list(self._tick_times)
        best, ago = 0.0, 0.0
        for a, b in zip(ts, ts[1:], strict=False):
            if now - b <= window_s and (b - a) * 1e3 > best:
                best, ago = (b - a) * 1e3, now - b
        if self._tick_times and (now - self._tick_times[-1]) * 1e3 > best:
            best, ago = (now - self._tick_times[-1]) * 1e3, 0.0
        return best, ago

    def perf_stats(self) -> dict[str, Any]:
        def pct(d: deque[float]) -> tuple[float, float, float]:
            if not d:
                return (float("nan"),) * 3
            a = np.fromiter(d, float)
            return float(np.percentile(a, 50)), float(np.percentile(a, 95)), float(a.max())
        p50, p95, mx = pct(self._intervals)
        out: dict[str, Any] = {"ticks": self._n, "interval_p50_ms": p50, "interval_p95_ms": p95,
                               "interval_max_ms": mx,
                               "fps_p50": 1e3 / p50 if p50 == p50 and p50 > 0 else float("nan"),
                               "errors": dict(self._errors)}
        for name, d in self._durations.items():
            s50, s95, smx = pct(d)
            out[f"{name}_p95_ms"] = s95
            out[f"{name}_max_ms"] = smx
        return out
