"""Test-only hooks of the backend (SW_design §12.4 hooks a–h), present only with
``BackendSettings(test_hooks=True)``; Validator F and the Integrator use the same hooks.

(a) ``advance(ms, step_ms=1)`` — lockstep clock: simulator → Reader → Pipeline → Supervisor → Worker → Recorder
    per step, deterministic. (b) ``wire_log()`` — copy of the PC transport's wire log (``wire_log=True``).
(c) ``rx_log()`` — Reader receive stamps. (d)–(f) per-command faults, sent-frame log and wire-timed world actions
are on ``backend.sim`` (``SimControl.act``). (g) ``fail_recorder(exc, after_rows)`` (any ``OSError``, e.g.
ENOSPC). (h) ``set_free_space(bytes)`` (stored; the free-space probe is M3). ``stall_thread(name, ms)`` stops a
thread's liveness beats (lockstep: the named component is skipped for ``ms``).

Implements: SW-PLT-003 (testability), SAF-SW-003 / NFR-002 test support (wire timestamps)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from bend_stand.core.backend import Backend

MS = 1_000_000


class TestHooks:
    __test__ = False

    def __init__(self, backend: Backend) -> None:
        self._be = backend
        self.free_space: int | None = None
        self._stalled: dict[str, int] = {}

    # (a)
    def advance(self, ms: float, step_ms: float = 1.0) -> None:
        be = self._be
        if not be.lockstep:
            raise RuntimeError("advance() needs BackendSettings(clock='lockstep')")
        step_ns = max(1, int(round(step_ms * MS)))
        end = be.clock.monotonic_ns() + int(round(ms * MS))
        while be.clock.monotonic_ns() < end:
            be.clock.advance(ns=min(step_ns, end - be.clock.monotonic_ns()))  # type: ignore[attr-defined]
            now = be.clock.monotonic_ns()
            if self._stalled:
                self._step_with_stalls(now)
            else:
                be._step_all(now)  # noqa: SLF001

    def _step_with_stalls(self, now: int) -> None:
        be = self._be
        for name, until in list(self._stalled.items()):
            if now >= until:
                del self._stalled[name]
        if be.sim_endpoint is not None:
            be.sim_endpoint.step(now)
        rd = be.device.reader
        if rd is not None and "reader" not in self._stalled:
            rd.step(now)
        if "pipeline" not in self._stalled:
            be.pipeline.step(now)
        if "supervisor" not in self._stalled:
            be.device.tick(now)
        if "worker" not in self._stalled:
            be.worker.step(now)
        if "recorder" not in self._stalled:
            be.recorder.step(now)

    def run_until(self, predicate: Any, timeout_ms: float = 5000.0, step_ms: float = 1.0) -> bool:
        """Advance until ``predicate()`` is true (lockstep); returns False at the timeout."""
        waited = 0.0
        while not predicate():
            if waited >= timeout_ms:
                return False
            self.advance(step_ms, step_ms)
            waited += step_ms
        return True

    def result(self, fut: Any, timeout_ms: float = 5000.0) -> Any:
        """Advance until a future is done and return its result (raises its exception)."""
        if not self.run_until(fut.done, timeout_ms):
            raise TimeoutError(f"future not done after {timeout_ms} ms of virtual time")
        return fut.result()

    # (b), (c)
    def wire_log(self) -> list[Any]:
        tr = self._be.device.transport
        return list(tr.wire_log) if tr is not None and tr.wire_log is not None else []

    def rx_log(self) -> list[Any]:
        rd = self._be.device.reader
        return list(rd.rx_log) if rd is not None else []

    # (g), (h)
    def fail_recorder(self, exc: BaseException, after_rows: int = 0) -> None:
        self._be.recorder.fail(exc, after_rows)

    def set_free_space(self, nbytes: int | None) -> None:
        self.free_space = nbytes

    def stall_thread(self, name: str, ms: float) -> None:
        be = self._be
        if be.lockstep:
            self._stalled[name] = be.clock.monotonic_ns() + int(ms * MS)
            return
        if name == "reader" and be.device.reader is not None:
            be.device.reader.paused.set()
            import threading  # noqa: PLC0415

            threading.Timer(ms / 1000.0, be.device.reader.paused.clear).start()
