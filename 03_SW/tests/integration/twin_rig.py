"""Lock-step rig: Implementer B's ``Backend`` (``BackendSettings(clock="lockstep", test_hooks=True)``) driving the FW
host twin (A's firmware, ``Twin("lockstep")``) in **one virtual time** — no wall clock anywhere, so host load
cannot change a result (OBS-M2-09: the realtime ``test_backend_stream_gap_free_and_gap_detection`` flaked under
parallel load). Every backend step of 1 ms first advances the twin to the same virtual instant, then runs B's
Reader → Pipeline → Supervisor → Worker → Recorder (SW_design §12.4 hook a).

The PC side is B's production code end to end (framing, protocol, link, device, pipeline, engines); only the
byte transport is replaced: ``TwinTransport`` hands the bytes to ``Twin.feed_rx`` (paced at 92 160 B/s by the
twin's RX model) and takes ``Twin.read_client()``. Wiring uses B's public seams: ``Device.transport_for``
(constructor argument, public attribute) and the backend's lock-step stepper slot ``sim_endpoint.step(now)``.

Implements: SYS-008 (SW<->twin integration support), OBS-M2-09 (deterministic backend-twin runs)
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from bend_stand.core.backend import Backend, BackendSettings
from bend_stand.io.transport import Transport

from twin import Twin

MS = 1_000_000
ENDPOINT = "tcp://127.0.0.1:5760"          # B's twin endpoint name; the rig substitutes the transport


class TwinTransport(Transport):
    """PC end of the link to an in-process lock-step twin (no socket, no thread)."""

    def __init__(self, tw: Twin, clock) -> None:  # noqa: ANN001 - B's Clock
        super().__init__(clock)
        self.tw = tw
        self._open = False

    @property
    def name(self) -> str:
        return "fw_twin(lockstep)"

    def open(self) -> None:
        self._open = True
        self.tw.read_client()                       # bytes sent before the PC connected are not on this "cable"

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def _read(self, max_bytes: int, timeout_s: float) -> bytes:
        return self.tw.read_client() if self._open else b""

    def _write(self, data: bytes) -> None:
        if self._open:
            self.tw.feed_rx(data)


class _TwinStepper:
    """Occupies the backend's lock-step simulator slot: ``step(now)`` advances the twin to the same instant."""

    def __init__(self, tw: Twin, base_ns: int) -> None:
        self.tw, self.base_ns = tw, base_ns
        self.transport: TwinTransport | None = None

    def step(self, now_ns: int | None = None) -> None:
        if now_ns is not None:
            self.tw.advance_to(now_ns - self.base_ns)

    def stop(self) -> None:          # Backend._stop_sim() at shutdown: the twin is closed by the rig
        pass


class Rig:
    """``rig.be`` (B's Backend), ``rig.tw`` (Twin), ``rig.hooks`` (B's TestHooks); virtual time helpers."""

    def __init__(self, exe: Path, run_dir: Path, *, scenario: dict | None = None, recordings: Path | None = None,
                 settings: dict[str, Any] | None = None) -> None:
        self.tw = Twin("lockstep", exe=exe, run_dir=run_dir, scenario=scenario)
        kw = dict(clock="lockstep", test_hooks=True, wire_log=True, hotkey="off",
                  recordings_root=str(recordings) if recordings else None)
        kw.update(settings or {})
        self.be = Backend(BackendSettings(**kw))
        self.hooks = self.be.test_hooks
        self.stepper = _TwinStepper(self.tw, self.be.clock.monotonic_ns() - self.tw.now)
        self.be.device.transport_for = self._transport_for
        self.be.sim_endpoint = self.stepper
        self.be.start()

    def _transport_for(self, endpoint: str) -> Transport:
        t = TwinTransport(self.tw, self.be.clock)
        self.stepper.transport = t
        return t

    # ---------------------------------------------------------------------------------------- time
    def advance(self, ms: float, step_ms: float = 1.0) -> None:
        self.hooks.advance(ms, step_ms)

    def run_until(self, pred: Callable[[], bool], timeout_ms: float = 5000.0, step_ms: float = 1.0) -> bool:
        return self.hooks.run_until(pred, timeout_ms, step_ms)

    def result(self, fut, timeout_ms: float = 5000.0):  # noqa: ANN001, ANN201
        return self.hooks.result(fut, timeout_ms)

    def connect(self, timeout_ms: float = 5000.0):  # noqa: ANN201
        return self.result(self.be.connect_async(ENDPOINT), timeout_ms)

    @property
    def now_ms(self) -> float:
        return self.tw.now / MS

    def act(self, action: str, **kw: Any) -> dict:
        r = self.tw.act(action, **kw)
        assert r.get("ok"), (action, kw, r)
        return r

    def close(self) -> None:
        try:
            self.be.shutdown()
        finally:
            self.tw.close()

    # ---------------------------------------------------------------------------------------- wire
    def rx_types(self) -> list[int]:
        return [w["type"] for w in self.tw.wire_log if w["dir"] == "rx"]

    def tx_events(self) -> list[tuple[int, int]]:
        out = []
        for w in self.tw.wire_log:
            if w["dir"] == "tx" and w["type"] == 0xC1:
                b = bytes.fromhex(w["hex"])
                out.append((int.from_bytes(b[10:12], "little"), int.from_bytes(b[12:14], "little")))
        return out
