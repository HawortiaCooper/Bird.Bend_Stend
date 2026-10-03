"""Generator jobs and the Worker (SW_design §3.1 "Worker", §12.4 hook (a)).

Every operation that waits for the board (connect, parameter read/write/verify, NVM, VERIFY resolution …) is
written **once** as a generator that ``yield``s what it waits for:

* a ``concurrent.futures.Future`` → resumed with its result (or the exception is thrown into the generator);
* ``Sleep(ns)`` → resumed after that much clock time;
* ``Poll(predicate, timeout_ns)`` → resumed with ``True`` when the predicate holds, ``False`` at the timeout.

The ``Worker`` drives such jobs either in its own thread (real clock; blocking waits) or, on the lockstep
clock, non-blocking from ``step(now_ns)`` inside ``test_hooks.advance()`` — the same code, fully
deterministic. Futures are resolved by the Reader (responses) and the Supervisor tick (timeouts), never by
the Worker itself, so a job can never deadlock its own wait.

Implements: SW-PLT-003 (asynchronous device API), NFR-004 (bounded queues)
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import logging
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

from bend_stand.core.clock import Clock
from bend_stand.core.observers import ReleasingFuture

log = logging.getLogger("bend_stand.core.jobs")


@dataclass(frozen=True)
class Sleep:
    ns: int


@dataclass(frozen=True)
class Poll:
    predicate: Callable[[], bool]
    timeout_ns: int


Job = Generator[Any, Any, Any]


def drive_blocking(gen: Job, clock: Clock, stop: threading.Event | None = None) -> Any:
    """Run a job to completion in the calling thread (real clock only)."""
    if clock.is_lockstep:
        raise RuntimeError("blocking job execution is not possible on the lockstep clock")
    send_val: Any = None
    exc: BaseException | None = None
    while True:
        try:
            item = gen.throw(exc) if exc is not None else gen.send(send_val)
        except StopIteration as si:
            return si.value
        send_val, exc = None, None
        if isinstance(item, cf.Future):
            while True:
                try:
                    send_val = item.result(timeout=0.05)
                    break
                except cf.TimeoutError:
                    if stop is not None and stop.is_set():
                        exc = RuntimeError("worker stopped")
                        break
                except BaseException as e:  # noqa: BLE001 — delivered into the job
                    exc = e
                    break
        elif isinstance(item, Sleep):
            ev = stop or threading.Event()
            ev.wait(max(0, item.ns) / 1e9)
        elif isinstance(item, Poll):
            deadline = clock.monotonic_ns() + item.timeout_ns
            ev = stop or threading.Event()
            ok = bool(item.predicate())
            while not ok and clock.monotonic_ns() < deadline and not ev.is_set():
                ev.wait(0.001)
                ok = bool(item.predicate())
            send_val = ok
        elif item is None:
            pass
        else:
            exc = TypeError(f"job yielded {item!r}")


class _Running:
    __slots__ = ("gen", "fut", "wait", "until", "pred")

    def __init__(self, gen: Job, fut: ReleasingFuture) -> None:
        self.gen = gen
        self.fut = fut
        self.wait: Any = None          # Future being awaited
        self.until: int | None = None  # Sleep / Poll deadline
        self.pred: Callable[[], bool] | None = None


class Worker:
    """Runs submitted jobs one at a time (FIFO). ``threaded=True``: own thread; else ``step(now)``."""

    def __init__(self, clock: Clock, name: str = "worker", *, threaded: bool | None = None,
                 maxlen: int = 256) -> None:
        self.clock = clock
        self.name = name
        self.threaded = (not clock.is_lockstep) if threaded is None else threaded
        self._q: collections.deque[tuple[Callable[[], Job], ReleasingFuture]] = collections.deque()
        self._maxlen = maxlen
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cur: _Running | None = None
        self.jobs_done = 0
        self.beat_ns = 0

    # ---- submission -------------------------------------------------------------------------------
    def submit(self, fn: Callable[..., Job], *args: Any, **kwargs: Any) -> ReleasingFuture:
        fut = ReleasingFuture()
        with self._cv:
            if len(self._q) >= self._maxlen:
                fut.set_exception(RuntimeError(f"{self.name}: job queue full"))
                return fut
            self._q.append((lambda: fn(*args, **kwargs), fut))
            self._cv.notify()
        return fut

    @property
    def busy(self) -> bool:
        return self._cur is not None or bool(self._q)

    # ---- threaded mode ----------------------------------------------------------------------------
    def start(self) -> None:
        if not self.threaded or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"bend-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout)
        while self._q:
            _fn, fut = self._q.popleft()
            if not fut.done():
                fut.cancel()

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                while not self._q and not self._stop.is_set():
                    self._cv.wait(0.25)
                    self.beat_ns = self.clock.monotonic_ns()
                if self._stop.is_set():
                    return
                fn, fut = self._q.popleft()
            self.beat_ns = self.clock.monotonic_ns()
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                res = drive_blocking(fn(), self.clock, self._stop)
            except BaseException as exc:  # noqa: BLE001 — delivered through the future
                fut.set_exception(exc)
            else:
                fut.set_result(res)
            self.jobs_done += 1
            del fn, fut

    # ---- lockstep mode ----------------------------------------------------------------------------
    def step(self, now_ns: int) -> bool:
        """Advance jobs without blocking; returns True if anything progressed."""
        self.beat_ns = now_ns
        progressed = False
        for _ in range(10_000):
            if self._cur is None:
                if not self._q:
                    return progressed
                fn, fut = self._q.popleft()
                if not fut.set_running_or_notify_cancel():
                    continue
                try:
                    self._cur = _Running(fn(), fut)
                except BaseException as exc:  # noqa: BLE001
                    fut.set_exception(exc)
                    continue
                if not self._resume(None, None):
                    return True
                progressed = True
                continue
            r = self._cur
            if r.wait is not None:
                if not r.wait.done():
                    return progressed
                w, r.wait = r.wait, None
                try:
                    val, exc = w.result(), None
                except BaseException as e:  # noqa: BLE001
                    val, exc = None, e
                progressed = True
                if not self._resume(val, exc):
                    return True
                continue
            if r.pred is not None:
                ok = bool(r.pred())
                if not ok and now_ns < (r.until or 0):
                    return progressed
                r.pred, r.until = None, None
                progressed = True
                if not self._resume(ok, None):
                    return True
                continue
            if r.until is not None:
                if now_ns < r.until:
                    return progressed
                r.until = None
                progressed = True
                if not self._resume(None, None):
                    return True
                continue
            if not self._resume(None, None):     # pragma: no cover - defensive
                return True
        return progressed  # pragma: no cover

    def _resume(self, val: Any, exc: BaseException | None) -> bool:
        """Send into the current job; set up its next wait. Returns False when the job ended."""
        r = self._cur
        assert r is not None
        while True:
            try:
                item = r.gen.throw(exc) if exc is not None else r.gen.send(val)
            except StopIteration as si:
                r.fut.set_result(si.value)
                self._cur = None
                self.jobs_done += 1
                return False
            except BaseException as e:  # noqa: BLE001
                r.fut.set_exception(e)
                self._cur = None
                self.jobs_done += 1
                return False
            val, exc = None, None
            if isinstance(item, cf.Future):
                r.wait = item
                return True
            if isinstance(item, Sleep):
                r.until = self.clock.monotonic_ns() + item.ns
                return True
            if isinstance(item, Poll):
                if item.predicate():
                    val = True
                    continue
                r.pred, r.until = item.predicate, self.clock.monotonic_ns() + item.timeout_ns
                return True
            if item is None:
                continue
            exc = TypeError(f"job yielded {item!r}")
