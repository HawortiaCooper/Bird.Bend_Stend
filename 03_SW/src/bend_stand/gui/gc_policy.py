"""GUI-thread garbage collection policy (SWD-PM3-07; SW_design_GUI §9.4, threading rule T8).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/gc_policy.py @37c87471 (copied unchanged except this
header and the module references: installed by ``bend_stand.gui.app.run``; ``defer_full`` = "the axis moves or an
operation runs", from ``status()``).

Rule: **no Qt object may become garbage in a non-GUI thread.**

Why: CPython's cyclic GC runs in whichever thread happens to cross the allocation threshold, i.e. very often a
backend thread (link supervisor, reader, simulator, pipeline). If a cycle holds a Python-owned Qt object
(pyqtgraph items, widgets / menus without a parent, lambdas connected to signals), the GC runs
``QObject::~QObject`` in that thread. The destructor takes Qt's shared signal/slot mutex (a pool hashed by
object address) and calls back into Python (``disconnectNotify`` override → ``PyGILState_Ensure``), while the
GUI thread holds the GIL and waits for the same Qt mutex (e.g. in ``QMenu::~QMenu``): the whole process
deadlocks (SWD-PM3-07, py-spy dumps in the pre-M3 report).

Policy (installed by :func:`bend_stand.gui.app.run` after start-up; headless backend use keeps the normal
automatic GC):

* ``gc.collect()`` once, optionally ``gc.freeze()`` (start-up objects: modules, main window, registry are
  moved to the permanent generation, so later collections do not scan them), then ``gc.disable()``: the
  interpreter no longer starts collections by itself, in any thread;
* a GUI-thread ``QTimer`` (default every 100 ms) runs the collections: generation 0 when the allocation
  count has crossed the normal threshold, generation 1 every second and a full collection every 10 s. So every
  cycle, and every Qt object in it, is finalised in the GUI thread;
* while ``defer_full()`` is true (the application passes "a motor is armed, an ARM is in progress or a self-test
  owns an output", ``MainWindow.motors_active``) **no long collection runs** (SWD-M4R1-07): the full collection and
  the thaw are postponed for the whole period (no cap by default, ``max_defer_s`` = None), and every generation-1
  collection is followed by ``gc.freeze()`` (an O(1) list merge), so the unfrozen heap never grows and the pauses
  stay those of a young collection (< 1 ms on REF-PC). Objects frozen this way that die later are reclaimed by
  the first thaw after the period;
* the pause stays short over a long session: a timer full collection slower than ``refreeze_ms`` (5 ms), or the end
  of a deferred period with freezes, schedules a **thaw** (``gc.unfreeze()`` -> full collection -> ``gc.freeze()``):
  frozen objects that died since (closed panes / windows / dialogs) are reclaimed, the live survivors are frozen
  again and the next full collection is short again (SWD-M3G-01: a bare re-freeze kept cyclic garbage in the
  permanent generation in proportion to the GUI churn). The thaw is the only long pause (an unfrozen full
  collection, 29-58 ms with the M4 heap): it runs only after ``defer_full()`` has been false for ``thaw_quiet_s``
  (2 s), in its own tick. A pause over ``warn_ms`` (16 ms) logs a warning, and an INFO summary (pauses, frozen
  objects, ``gc.get_stats()``) is logged every ``log_s`` (600 s), so heap growth over a session is visible;
* someone re-enabling the automatic GC is detected on the next tick (warning, disabled again).

The pauses are measured (``stats()``) and must fit the 33 ms refresh budget (SW-PLT-003); measured values are
in SW_design §13.10.

Implements: NFR-001 (GUI refresh budget: bounded, scheduled GC pauses), NFR-004 (GUI part), SW-STOP-001 (no
process deadlock that would stall the on-screen STOP path)
"""
from __future__ import annotations

import gc
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer

log = logging.getLogger(__name__)

TICK_MS = 100            # check period (GUI thread)
GEN1_S = 1.0             # generation-1 collection period
FULL_S = 10.0            # full collection period (≈ 0.1 ms after the start-up freeze, SW_design §13.10)
REFREEZE_MS = 5.0        # a timer full collection slower than this triggers thaw-collect-freeze (SWD-M3G-01)
WARN_MS = 16.0           # pause warning (half of the 33 ms refresh period), at most once per minute
LOG_S = 600.0            # period of the INFO summary (pauses, gc.get_stats(), frozen objects)
THAW_QUIET_S = 2.0       # the thaw (the one long pause) waits until defer_full() has been false this long


class GuiGcPolicy(QObject):
    """Runs the cyclic garbage collector only in the GUI thread (see module doc). One instance per process."""

    def __init__(self, parent: QObject | None = None, *, tick_ms: int = TICK_MS, gen1_s: float = GEN1_S,
                 full_s: float = FULL_S, freeze: bool = True, young_threshold: int | None = None,
                 initial_collect: bool = True, defer_full: Callable[[], bool] | None = None,
                 max_defer_s: float | None = None, refreeze_ms: float = REFREEZE_MS, warn_ms: float = WARN_MS,
                 log_s: float = LOG_S, thaw_quiet_s: float = THAW_QUIET_S, freeze_while_deferred: bool = True
                 ) -> None:
        super().__init__(parent)
        self._freeze = freeze
        self._initial_collect = initial_collect or freeze
        self._defer_full = defer_full               # "motors active": no long collection (SWD-M4R1-07)
        # cap of the full-collection deferral (None = the whole deferred period). The thaw is never capped.
        self._max_defer_s = None if max_defer_s is None else float(max_defer_s)
        self._thaw_quiet_s = float(thaw_quiet_s)
        self._freeze_while_deferred = bool(freeze_while_deferred)
        self._last_deferred: float | None = None    # monotonic time of the last tick with defer_full() true
        self.deferred_freezes = 0                    # gc.freeze() after a young collection while deferred
        self._full_due_since: float | None = None
        self._refreeze_ms = float(refreeze_ms)
        self._warn_ms = float(warn_ms)
        self._log_s = float(log_s)
        self._next_log = 0.0
        self._last_warn = -1e9
        self.refreezes = 0
        self._thaw_due = False                       # SWD-M3G-01: thaw-collect-freeze on the next tick
        self.thaw_reclaimed = 0
        self._gen1_s = float(gen1_s)
        self._full_s = float(full_s)
        self._young = int(young_threshold if young_threshold is not None else max(1, gc.get_threshold()[0]))
        self._installed = False
        self._was_enabled = True
        self._frozen = False
        self._warned = False
        self._next_gen1 = 0.0
        self._next_full = 0.0
        self._thread_id: int | None = None
        self._stats: dict[int, dict[str, float]] = {g: {"n": 0, "max_ms": 0.0, "sum_ms": 0.0, "last_ms": 0.0,
                                                        "collected": 0} for g in (0, 1, 2)}
        self._timer = QTimer(self)
        self._timer.setInterval(int(tick_ms))
        self._timer.timeout.connect(self.tick)

    # ---------------------------------------------------------------- install / uninstall
    @property
    def installed(self) -> bool:
        return self._installed

    def install(self) -> None:
        """Take over the GC (call in the GUI thread, after start-up). Idempotent."""
        if self._installed:
            return
        self._check_thread()
        self._thread_id = threading.get_ident()
        self._was_enabled = gc.isenabled()
        gc.disable()                                  # first: no backend-thread collection from here on
        if self._initial_collect:
            self._collect(2)
        if self._freeze:
            gc.freeze()
            self._frozen = True
        now = time.monotonic()
        self._next_gen1 = now + self._gen1_s
        self._next_full = now + self._full_s
        self._next_log = now + self._log_s
        self._installed = True
        self._timer.start()
        log.info("GUI-thread GC policy installed (automatic GC off, tick %d ms, gen1 %.1f s, full %.0f s%s)",
                 self._timer.interval(), self._gen1_s, self._full_s, ", start-up objects frozen" if self._frozen
                 else "")

    def uninstall(self) -> None:
        """Give the GC back (tests; application exit keeps the policy until the process ends)."""
        if not self._installed:
            return
        self._timer.stop()
        self._installed = False
        self._thaw_due = False
        if self._frozen:
            gc.unfreeze()
            self._frozen = False
        self._collect(2)                              # still in the GUI thread
        if self._was_enabled:
            gc.enable()

    # ---------------------------------------------------------------- GUI-thread collections
    def tick(self) -> int | None:
        """Timer slot: runs at most one collection; returns the generation collected (None: nothing to do)."""
        if not self._installed:
            return None
        if threading.get_ident() != self._thread_id:      # defensive: never collect off the GUI thread
            return None
        if gc.isenabled():
            if not self._warned:
                log.warning("automatic GC was re-enabled by someone; disabled again (SWD-PM3-07)")
                self._warned = True
            gc.disable()
        now = time.monotonic()
        deferred = self._defer_now()
        if deferred:
            self._last_deferred = now
        if self._thaw_due and not deferred and self._quiet(now):
            self._thaw()
            return 2
        if now >= self._next_full and self._full_deferred(now, deferred):
            self._next_full = now + self._gen1_s      # re-check soon; gen 1 keeps running meanwhile
        if now >= self._next_full:
            gen = 2
            self._full_due_since = None
        elif now >= self._next_gen1:
            gen = 1
        elif gc.get_count()[0] >= self._young:
            gen = 0
        else:
            return None
        if gen >= 1:
            self._next_gen1 = now + self._gen1_s
        if gen == 2:
            self._next_full = now + self._full_s
        self._collect(gen)
        if deferred and gen >= 1 and self._frozen and self._freeze_while_deferred:
            # SWD-M4R1-07: keep the unfrozen heap at the young survivors while motors are active (O(1) merge); the
            # garbage frozen here is reclaimed by the thaw after the period
            gc.freeze()
            self.deferred_freezes += 1
            self._thaw_due = True
        elif gen == 2 and self._frozen and self._stats[2]["last_ms"] > self._refreeze_ms:
            self._thaw_due = True                     # next quiet tick: thaw, collect everything, freeze survivors
        if now >= self._next_log:
            self._next_log = now + self._log_s
            log.info("%s", self.summary())
        return gen

    def _defer_now(self) -> bool:
        if self._defer_full is None:
            return False
        try:
            return bool(self._defer_full())
        except Exception:  # noqa: BLE001 - a broken predicate must not stop the collections
            return False

    def _quiet(self, now: float) -> bool:
        """The thaw (an unfrozen full collection) runs only ``thaw_quiet_s`` after the last deferred tick."""
        return self._last_deferred is None or now - self._last_deferred >= self._thaw_quiet_s

    @property
    def thaw_due(self) -> bool:
        return self._thaw_due

    def _thaw(self) -> None:
        """SWD-M3G-01: thaw -> full collection -> freeze. Objects frozen earlier (start-up, a previous re-freeze or
        a freeze while deferred) that have died since - closed panes, windows, dialogs in reference cycles - are
        reclaimed here; the live survivors go back to the permanent generation, so the following timer full
        collections are short again. Runs in its own tick after a slow full collection or after a deferred period
        (never in the same tick as another collection, so two pauses do not add up), never while ``defer_full()`` is
        true and only ``thaw_quiet_s`` after it (SWD-M4R1-07). The pause is that of an unfrozen full collection
        (SW_design §13.10: 29-58 ms with the M4 heap)."""
        self._thaw_due = False
        gc.unfreeze()
        self._collect(2)
        gc.freeze()
        self.refreezes += 1
        self.thaw_reclaimed = self._stats[2]["collected"]
        log.info("full GC was slow: thawed, collected and re-froze the survivors in %.1f ms (%d objects frozen, "
                 "re-freeze #%d)", self._stats[2]["last_ms"], gc.get_freeze_count(), self.refreezes)

    def _full_deferred(self, now: float, deferred: bool) -> bool:
        """True while the full collection is postponed: ``defer_full()`` is true (for the whole period, or at most
        ``max_defer_s`` when a cap is configured)."""
        if not deferred:
            self._full_due_since = None
            return False
        if self._full_due_since is None:
            self._full_due_since = now
        return self._max_defer_s is None or now - self._full_due_since < self._max_defer_s

    def collect_now(self, generation: int = 2) -> int:
        """Immediate collection in the calling (GUI) thread; returns the number of unreachable objects."""
        self._check_thread()
        return self._collect(generation)

    def _collect(self, gen: int) -> int:
        t0 = time.perf_counter()
        n = gc.collect(gen)
        dt = (time.perf_counter() - t0) * 1e3
        s = self._stats[gen]
        s["n"] += 1
        s["sum_ms"] += dt
        s["last_ms"] = dt
        s["max_ms"] = max(s["max_ms"], dt)
        s["collected"] += n
        if dt > self._warn_ms and self._installed and time.monotonic() - self._last_warn > 60.0:
            self._last_warn = time.monotonic()
            log.warning("GC generation %d paused the GUI thread (and, through the GIL, the backend threads) for "
                        "%.1f ms (> %.1f ms); %s", gen, dt, self._warn_ms, self.summary())
        return n

    def summary(self) -> str:
        """One-line state for the log: pauses per generation, frozen objects, re-freezes, ``gc.get_stats()``."""
        per = ", ".join(f"gen{g} n={s['n']} mean={s['mean_ms']:.2f} max={s['max_ms']:.2f} ms"
                        for g, s in self.stats().items())
        return (f"GUI GC: {per}; frozen={gc.get_freeze_count()} refreezes={self.refreezes} "
                f"count={gc.get_count()} gc.get_stats={gc.get_stats()}")

    def stats(self) -> dict[int, dict[str, Any]]:
        """Per generation: collections ``n``, pause ``max_ms`` / ``mean_ms`` / ``last_ms``, ``collected``."""
        return {g: {**s, "mean_ms": (s["sum_ms"] / s["n"]) if s["n"] else 0.0} for g, s in self._stats.items()}

    @staticmethod
    def _check_thread() -> None:
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() != app.thread():
            raise RuntimeError("GuiGcPolicy must be used in the GUI thread")
        if app is None and threading.current_thread() is not threading.main_thread():
            raise RuntimeError("GuiGcPolicy must be used in the GUI thread")


_POLICY: GuiGcPolicy | None = None


def install_gui_gc_policy(parent: QObject | None = None, **kwargs: Any) -> GuiGcPolicy:
    """Create (once) and install the process-wide policy; ``parent`` is normally the ``QApplication``."""
    global _POLICY
    if _POLICY is None:
        _POLICY = GuiGcPolicy(parent, **kwargs)
    _POLICY.install()
    return _POLICY


def current_policy() -> GuiGcPolicy | None:
    return _POLICY
