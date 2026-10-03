"""Reader: bytes → frames → dispatch (SW_design §4.3). Never writes to the port.

``transport.read()`` (≤ 4096 B, 2 ms timeout), ``FrameDecoder.feed()``; the host time is stamped **before** the
read, the 20 ms inter-byte timeout is applied only after an **empty** read (TS SWD-M2-01). Then:

* responses (``0x81..0xBF``) → ``on_response(frame)`` (CommandChannel resolves the future);
* DATA / EVENT (``0xC0``/``0xC1``) → ``on_async(frame)`` in arrival order (Pipeline queue);
* reserved async ``0xC2..0xCF`` → ignored without an error count; any other TYPE → ``unknown_type``.

Every completed frame is logged in ``transport.log_rx`` (wire log, hook b) and in ``rx_log`` (hook c:
``RxRecord(t_host_ns, type, seq, frame_seq, event_code)``). ``line_ns`` (instant up to which the line was
observed) is published after dispatch (TS SWD-M2-07). The loop body is ``step(now_ns)`` so the Reader runs in its
own thread on the real clock or inside ``test_hooks.advance()`` on the lockstep clock.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/reader.py @37c87471 (adapted: step() body for lockstep,
rx_log, wire log, bend-stand TYPE classes).

Implements: IF-003, IF-011 (receive stamps), SAF-SW-003 (reader liveness beat)
"""
from __future__ import annotations

import collections
import logging
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass

from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import Clock
from bend_stand.core.errors import TransportError
from bend_stand.io.framing import Frame, FrameDecoder, fw_to_pc_type_class
from bend_stand.io.transport import READ_TIMEOUT_S, Transport

log = logging.getLogger("bend_stand.io.reader")


@dataclass(frozen=True, slots=True)
class RxRecord:
    t_host_ns: int
    type: int
    seq: int
    frame_seq: int | None = None
    event_code: int | None = None


@dataclass
class ReaderStats:
    bytes_rx: int = 0
    reads: int = 0
    responses: int = 0
    async_frames: int = 0
    reserved_async: int = 0
    unknown_type: int = 0
    dispatch_errors: int = 0


class Reader:
    def __init__(self, transport: Transport, clock: Clock, *, on_response: Callable[[Frame], None],
                 on_async: Callable[[Frame], None], on_error: Callable[[Exception], None] | None = None,
                 beat: Callable[[int], None] | None = None, decoder: FrameDecoder | None = None,
                 rx_log_len: int = 100_000, read_timeout_s: float = READ_TIMEOUT_S,
                 thread_init: Callable[[], object] | None = None) -> None:
        self.thread_init = thread_init
        self.transport = transport
        self.clock = clock
        self.decoder = decoder or FrameDecoder()
        self.on_response = on_response
        self.on_async = on_async
        self.on_error = on_error
        self.beat = beat
        self.read_timeout_s = read_timeout_s
        self.stats = ReaderStats()
        self.rx_log: collections.deque[RxRecord] = collections.deque(maxlen=rx_log_len)
        self.line_ns: int | None = None
        self.last_rx_ns: int | None = None       # time of the newest received frame
        self.failed: Exception | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.paused = threading.Event()          # test seam: simulate a blocked reader

    # ---- dispatch ---------------------------------------------------------------------------------
    def feed_pending(self, data: bytes) -> None:
        """Bytes pending at open: parsed like any other bytes (stale responses have no pending SEQ)."""
        now = self.clock.monotonic_ns()
        for fr in self.decoder.feed(data, now, check_timeout=False):
            self._dispatch(fr)

    def _dispatch(self, fr: Frame) -> None:
        self.transport.log_rx(fr.raw, fr.t_ns)
        kind = fw_to_pc_type_class(fr.type)
        fseq = code = None
        if fr.type == pg.AsyncType.DATA and len(fr.payload) >= 16:
            fseq = struct.unpack_from("<H", fr.payload, 14)[0]
        elif fr.type == pg.AsyncType.EVENT and len(fr.payload) >= 6:
            code = struct.unpack_from("<H", fr.payload, 4)[0]
        self.rx_log.append(RxRecord(fr.t_ns, fr.type, fr.seq, fseq, code))
        self.last_rx_ns = fr.t_ns
        try:
            if kind == "response":
                self.stats.responses += 1
                self.on_response(fr)
            elif kind == "async":
                self.stats.async_frames += 1
                self.on_async(fr)
            elif kind == "reserved":
                self.stats.reserved_async += 1
            else:
                self.stats.unknown_type += 1
        except Exception:  # noqa: BLE001 — a handler error must not kill the link
            self.stats.dispatch_errors += 1
            log.exception("frame dispatch failed (TYPE 0x%02X)", fr.type)

    # ---- loop body --------------------------------------------------------------------------------
    def step(self, now_ns: int | None = None, timeout_s: float | None = None) -> int:
        """One read + dispatch. Returns the number of frames dispatched; transport errors → ``on_error``."""
        if self.failed is not None or self.paused.is_set():
            return 0
        t_before = self.clock.monotonic_ns() if now_ns is None else now_ns
        to = (0.0 if self.clock.is_lockstep else self.read_timeout_s) if timeout_s is None else timeout_s
        try:
            data = self.transport.read(4096, to)
        except TransportError as exc:
            self.failed = exc
            if self.on_error is not None and not self._stop.is_set():
                self.on_error(exc)
            return 0
        if data:
            now = self.clock.monotonic_ns()
            self.stats.bytes_rx += len(data)
            self.stats.reads += 1
            frames = self.decoder.feed(data, now, check_timeout=False)
            line = now
        else:
            frames = self.decoder.poll(t_before)
            line = t_before
        for fr in frames:
            self._dispatch(fr)
        self.line_ns = line if self.line_ns is None else max(self.line_ns, line)
        if self.beat is not None:
            self.beat(line)
        return len(frames)

    # ---- thread -----------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="bend-reader", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout)

    def _run(self) -> None:
        if self.thread_init is not None:
            self.thread_init()                   # e.g. core.timing.raise_thread_priority (ABOVE_NORMAL)
        while not self._stop.is_set() and self.failed is None:
            if self.paused.is_set():
                self._stop.wait(0.005)
                continue
            self.step()
