"""Command path and link supervision (SW_design §4.4–§4.6, ICD §2.4, §9).

``FrameWriter``     the only writer of the port; a two-level lock: a priority writer waits at most for the one
                    frame already being written (IF-011, NFR-002/003).
``CommandChannel``  SEQ allocation (incrementing, wraps, skips pending values, randomised start), response
                    matching by ``(TYPE | 0x80, SEQ)``, lanes SAFETY / CONTROL / GENERAL with ≤ 4 outstanding,
                    token bucket ≤ 100 frames/s, retry classes from ``protocol_gen.CMD_RETRY`` (JOG 0 → RETRY):
                    RETRY = ≤ 2 retries with a new SEQ and the newest payload (latest-wins ``key``), VERIFY = never
                    retried (the caller resolves by GET_STATUS), CONFIRM = priority path + ``StopConfirmer``.
                    Priority commands (``CMD_PRIORITY``: STOP, HALT, PAUSE, HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR)
                    are written at once through the priority lock, outside lanes, outstanding limit and bucket.
                    Motion epoch: a request carrying an older epoch is dropped before it is written (KD-04).
                    Only response **timeouts** count toward DEGRADED; a NACK is an answer (D-33 k).
``StopConfirmer``   repeats STOP/HALT/PAUSE every 50 ms (≤ 20 attempts in 1 s) until ACK or the FW indication
                    (STOP: MOVING = 0, HALT: flags.HALT, PAUSE: status.PAUSED); polls GET_STATUS with the stream
                    off; ``stop.unconfirmed`` after 1 s.
``link_state_for``  DEGRADED after 3 consecutive timeouts or 300 ms without any frame while streaming; LOST
                    after 1 s without frames.

No own threads: ``CommandChannel.tick(now)`` and ``StopConfirmer.tick(now)`` run on the Supervisor tick (5 ms)
or inside ``test_hooks.advance()`` on the lockstep clock.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/link.py @37c87471 (adapted: FrameWriter priority lock,
lanes/retry classes of ICD v0.4.1, motion epoch, StopConfirmer from EstopConfirmer, no dispatcher thread).

Implements: IF-005, IF-011, SAF-SW-003 (link part), SW-STOP-001/002 (priority path), NFR-002, NFR-003
"""
from __future__ import annotations

import collections
import logging
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import MONOTONIC, Clock
from bend_stand.core.errors import CommandTimeout, LinkError, NackError, TransportError
from bend_stand.core.model import LinkState
from bend_stand.core.observers import ReleasingFuture
from bend_stand.io import protocol as P
from bend_stand.io.framing import Frame, encode_frame
from bend_stand.io.transport import Transport

log = logging.getLogger("bend_stand.core.link")
MS = 1_000_000
Cmd = pg.Cmd

DEFAULT_TIMEOUT_NS = 100 * MS
CONNECT_INFO_TIMEOUT_NS = 200 * MS
NVM_TIMEOUT_NS = 3000 * MS
REBOOT_TIMEOUT_NS = 3000 * MS
RETRIES = 2
MAX_OUTSTANDING = 4
MAX_FRAMES_PER_S = 100.0
TOKEN_BURST = 8.0
HEARTBEAT_IDLE_NS = 150 * MS
SUPERVISOR_TICK_NS = 5 * MS
CONFIRM_REPEAT_NS = 50 * MS
CONFIRM_MAX_ATTEMPTS = 20
CONFIRM_WINDOW_NS = 1000 * MS
DEGRADED_TIMEOUTS = 3
DEGRADED_RX_NS = 300 * MS
LOST_RX_NS = 1000 * MS
FW_TX_BACKLOG_NS = 25 * MS


class Lane(IntEnum):
    SAFETY = 0
    CONTROL = 1
    GENERAL = 2


#: default lane per command (SW_design §4.4 lane table); SET_VALID / JOG / SET_PARAM chosen by the caller
DEFAULT_LANE: dict[int, Lane] = {
    Cmd.PING: Lane.SAFETY,
    Cmd.RESUME: Lane.CONTROL, Cmd.MOVE_ABS: Lane.CONTROL, Cmd.MOVE_UNTIL_LOAD: Lane.CONTROL,
    Cmd.JOG: Lane.CONTROL, Cmd.HOME: Lane.CONTROL, Cmd.ENABLE: Lane.CONTROL, Cmd.DISABLE: Lane.CONTROL,
}
LANE_CAP: dict[Lane, int] = {Lane.SAFETY: MAX_OUTSTANDING, Lane.CONTROL: 1, Lane.GENERAL: 2}


def default_timeout_ns(cmd: int) -> int:
    if cmd in (Cmd.SAVE_PARAMS, Cmd.LOAD_PARAMS, Cmd.DEFAULT_PARAMS):
        return NVM_TIMEOUT_NS + FW_TX_BACKLOG_NS
    if cmd == Cmd.REBOOT:
        return REBOOT_TIMEOUT_NS
    return DEFAULT_TIMEOUT_NS


class CommandDropped(LinkError):
    """A motion request of an older motion epoch was dropped before it was written (KD-04)."""


# ============================================================================== writer

class FrameWriter:
    """Serialised frame output. ``write(..., priority=True)`` makes normal writers yield, so a priority frame
    waits at most for the one frame already being written (SW_design §4.5)."""

    def __init__(self, transport: Transport, clock: Clock = MONOTONIC) -> None:
        self.transport = transport
        self.clock = clock
        self._cv = threading.Condition()
        self._busy = False
        self._prio_waiting = 0
        self.last_tx_ns = clock.monotonic_ns()
        self.frames_tx = 0
        self.on_write: Callable[[int, int, bytes, int], None] | None = None   # test seam

    def write(self, ftype: int, seq: int, payload: bytes = b"", *, priority: bool = False) -> int:
        frame = encode_frame(ftype, seq, payload)
        with self._cv:
            if priority:
                self._prio_waiting += 1
            try:
                while self._busy or (not priority and self._prio_waiting):
                    self._cv.wait(0.01)
                self._busy = True
            finally:
                if priority:
                    self._prio_waiting -= 1
        try:
            self.transport.write(frame)
            t = self.clock.monotonic_ns()
            self.last_tx_ns = t
            self.frames_tx += 1
        finally:
            with self._cv:
                self._busy = False
                self._cv.notify_all()
        hook = self.on_write
        if hook is not None:
            hook(ftype, seq, payload, t)
        return t


# ============================================================================== requests / responses

@dataclass(frozen=True)
class Response:
    cmd: int
    seq: int
    status: int
    detail: int
    body: bytes
    t_host_ns: int
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status == 0


Payload = bytes | Callable[[], bytes | None]


@dataclass(eq=False)
class Request:
    cmd: int
    lane: Lane
    payload: Payload
    timeout_ns: int
    retries_left: int
    retry_class: pg.RetryClass
    future: ReleasingFuture
    priority: bool = False
    epoch: int | None = None
    key: str | None = None             # latest-wins slot (SET_VALID, threshold SET_PARAMs, JOG 0, …)
    attempts: int = 0
    seq: int = -1
    sent_ns: int = 0
    deadline_ns: int = 0
    generation: int = 0

    @property
    def name(self) -> str:
        try:
            return Cmd(self.cmd).name
        except ValueError:
            return f"0x{self.cmd:02X}"

    def build(self) -> bytes | None:
        return self.payload() if callable(self.payload) else self.payload


@dataclass
class ChannelStats:
    sent: int = 0
    retries_sent: int = 0
    responses: int = 0
    nacks: int = 0
    timeouts: int = 0
    late_responses: int = 0
    unknown_seq: int = 0
    dropped_retries: int = 0
    dropped_epoch: int = 0
    coalesced: int = 0
    priority_sent: int = 0
    max_outstanding_seen: int = 0
    tx_errors: int = 0


class CommandChannel:
    """Futures-based command channel (module doc). ``submit()`` never blocks; it may write in the caller's
    thread when a slot and a token are free."""

    def __init__(self, writer: FrameWriter, clock: Clock = MONOTONIC, *, seed: int | None = None,
                 max_rate: float = MAX_FRAMES_PER_S, burst: float = TOKEN_BURST) -> None:
        self.writer = writer
        self.clock = clock
        self._lock = threading.RLock()
        self._queues: dict[Lane, collections.deque[Request]] = {ln: collections.deque() for ln in Lane}
        self._inflight: dict[tuple[int, int], Request] = {}
        self._abandoned: collections.OrderedDict[tuple[int, int], int] = collections.OrderedDict()
        self._seq = random.Random(seed).randrange(256)           # randomised first SEQ (TS SWD-M2-06)
        self._key_gen: dict[str, int] = {}
        self._gen = 0
        self.stats = ChannelStats()
        self.consecutive_timeouts = 0
        self.motion_epoch = 0
        self._rate = float(max_rate)
        self._burst = float(burst)
        self._tokens = float(burst)
        self._tokens_t = clock.monotonic_ns()
        self.on_timeout: Callable[[Request], None] | None = None
        self.on_tx_error: Callable[[Exception], None] | None = None
        self.closed: Exception | None = None

    # ---- lifecycle ----------------------------------------------------------------------------------
    def close(self, exc: Exception | None = None) -> None:
        exc = exc or LinkError("link closed")
        with self._lock:
            self.closed = exc
            reqs = [r for q in self._queues.values() for r in q] + list(self._inflight.values())
            for q in self._queues.values():
                q.clear()
            self._inflight.clear()
        for r in reqs:
            self._fail(r, exc)

    def bump_epoch(self) -> int:
        """New motion epoch (STOP/HALT/PAUSE sent, FW stop indication): queued motion of older epochs is dropped."""
        with self._lock:
            self.motion_epoch += 1
            stale = []
            for q in self._queues.values():
                for r in list(q):
                    if r.epoch is not None and r.epoch != self.motion_epoch:
                        q.remove(r)
                        stale.append(r)
            self.stats.dropped_epoch += len(stale)
        for r in stale:
            self._fail(r, CommandDropped(f"{r.name} dropped: a stop intervened"))
        return self.motion_epoch

    # ---- SEQ ----------------------------------------------------------------------------------------
    def _alloc_seq(self) -> int:
        pending = {s for (_t, s) in self._inflight}
        for _ in range(256):
            self._seq = (self._seq + 1) & 0xFF
            if self._seq not in pending:
                return self._seq
        raise LinkError("no free SEQ")  # pragma: no cover

    # ---- submit -------------------------------------------------------------------------------------
    def submit(self, cmd: int, payload: Payload = b"", *, lane: Lane | None = None, timeout_ns: int | None = None,
               epoch: int | None = None, key: str | None = None) -> ReleasingFuture:
        cmd = int(Cmd(cmd))
        fut = ReleasingFuture()
        if self.closed is not None:
            fut.set_exception(self.closed)
            return fut
        first = payload() if callable(payload) else payload
        rclass = P.retry_class(cmd, first or b"")
        priority = cmd in pg.CMD_PRIORITY
        req = Request(cmd=cmd, lane=DEFAULT_LANE.get(cmd, Lane.GENERAL) if lane is None else lane,
                      payload=payload, timeout_ns=default_timeout_ns(cmd) if timeout_ns is None else timeout_ns,
                      retries_left=RETRIES if rclass == pg.RetryClass.RETRY else 0, retry_class=rclass,
                      future=fut, priority=priority, epoch=epoch, key=key)
        with self._lock:
            self._gen += 1
            req.generation = self._gen
            if key is not None:
                self._key_gen[key] = req.generation
                for q in self._queues.values():          # a queued, unsent request of the same slot is replaced
                    for old in list(q):
                        if old.key == key and old.attempts == 0:
                            q.remove(old)
                            self._chain(fut, old.future)     # the replaced request resolves with the newer
                            self.stats.coalesced += 1
            if priority:
                self._send_locked(req, self.clock.monotonic_ns())
            else:
                self._queues[req.lane].append(req)
                self._pump_locked()
        return fut

    def send_priority(self, cmd: int, payload: bytes = b"") -> tuple[ReleasingFuture, int | None, str | None]:
        """Write a priority command now. Returns ``(future, t_write_ns | None, error | None)``; never raises."""
        fut = self.submit(cmd, payload)
        if fut.done() and fut.exception() is not None:
            return fut, None, str(fut.exception())
        with self._lock:
            req = next((r for r in self._inflight.values() if r.future is fut), None)
        return fut, (req.sent_ns if req else None), None

    # ---- slots / tokens -----------------------------------------------------------------------------
    def _counts(self) -> dict[Lane, int]:
        n = {ln: 0 for ln in Lane}
        for r in self._inflight.values():
            if not r.priority:
                n[r.lane] += 1
        return n

    def outstanding(self) -> int:
        with self._lock:
            return sum(1 for r in self._inflight.values() if not r.priority)

    def queued(self) -> int:
        with self._lock:
            return sum(len(q) for q in self._queues.values())

    def _take_token(self, now: int) -> bool:
        dt = (now - self._tokens_t) / 1e9
        self._tokens_t = now
        self._tokens = min(self._burst, self._tokens + dt * self._rate)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def _pump_locked(self) -> None:
        now = self.clock.monotonic_ns()
        for lane in Lane:
            q = self._queues[lane]
            while q:
                n = self._counts()
                if sum(n.values()) >= MAX_OUTSTANDING or n[lane] >= LANE_CAP[lane]:
                    break
                if lane != Lane.SAFETY and not self._take_token(now):
                    return
                req = q.popleft()
                self._send_locked(req, now)

    def _send_locked(self, req: Request, now: int) -> None:
        if req.epoch is not None and req.epoch != self.motion_epoch:
            self.stats.dropped_epoch += 1
            self._fail(req, CommandDropped(f"{req.name} dropped: a stop intervened"))
            return
        try:
            payload = req.build()
        except Exception as exc:  # noqa: BLE001
            self._fail(req, exc)
            return
        if payload is None:                      # the builder decided: nothing to send any more
            if not req.future.done():
                req.future.set_result(None)
            return
        req.seq = self._alloc_seq()
        req.attempts += 1
        try:
            t = self.writer.write(req.cmd, req.seq, payload, priority=req.priority)
        except (TransportError, OSError) as exc:
            self.stats.tx_errors += 1
            self._fail(req, TransportError(str(exc)))
            cb = self.on_tx_error
            if cb is not None:
                cb(exc)
            return
        req.sent_ns = t
        req.deadline_ns = t + req.timeout_ns
        self._inflight[(req.cmd | pg.RESP_BIT, req.seq)] = req
        self.stats.sent += 1
        if req.priority:
            self.stats.priority_sent += 1
        if req.attempts > 1:
            self.stats.retries_sent += 1
        self.stats.max_outstanding_seen = max(self.stats.max_outstanding_seen, self.outstanding())

    # ---- responses ----------------------------------------------------------------------------------
    def on_response(self, fr: Frame) -> None:
        key = (fr.type, fr.seq)
        with self._lock:
            req = self._inflight.pop(key, None)
            if req is None:
                if key in self._abandoned:
                    self.stats.late_responses += 1
                else:
                    self.stats.unknown_seq += 1
                return
            self.stats.responses += 1
            self.consecutive_timeouts = 0
        try:
            r = P.split_response(fr.type, fr.seq, fr.payload)
        except ValueError as exc:
            self._fail(req, exc)
        else:
            resp = Response(req.cmd, fr.seq, r.status, r.detail, r.body, fr.t_ns or self.clock.monotonic_ns(),
                            req.attempts)
            if r.status != 0:
                self.stats.nacks += 1
                self._fail(req, NackError(req.name, r.status, r.status_name, r.detail,
                                          P.nack_text(req.cmd, r.status, r.detail)))
            elif not req.future.done():
                req.future.set_result(resp)
        with self._lock:
            self._pump_locked()

    # ---- timeouts ------------------------------------------------------------------------------------
    def tick(self, now: int | None = None) -> None:
        now = self.clock.monotonic_ns() if now is None else now
        timed_out: list[Request] = []
        with self._lock:
            for k, r in list(self._inflight.items()):
                if now >= r.deadline_ns:
                    del self._inflight[k]
                    self._abandoned[k] = now
                    timed_out.append(r)
            while len(self._abandoned) > 128:
                self._abandoned.popitem(last=False)
            for r in timed_out:
                self._handle_timeout_locked(r)
            self._pump_locked()
        for r in timed_out:
            if r.future.done() and isinstance(r.future.exception(), CommandTimeout):
                cb = self.on_timeout
                if cb is not None:
                    cb(r)

    def _handle_timeout_locked(self, r: Request) -> None:
        if not r.priority:
            self.consecutive_timeouts += 1
        if r.retries_left > 0 and r.retry_class == pg.RetryClass.RETRY:
            if r.key is not None and self._key_gen.get(r.key, 0) > r.generation:
                newer = next((q for lq in self._queues.values() for q in lq if q.key == r.key), None)
                newer = newer or next((q for q in self._inflight.values() if q.key == r.key), None)
                self.stats.dropped_retries += 1
                if newer is not None:
                    self._chain(newer.future, r.future)
                else:                               # the newer request already completed
                    self._fail(r, CommandTimeout(f"{r.name}: superseded"))
                return
            r.retries_left -= 1
            self._queues[r.lane].appendleft(r)       # retry with a new SEQ and the newest payload
            return
        self.stats.timeouts += 1
        self._fail(r, CommandTimeout(f"{r.name}: no response after {r.attempts} attempt(s)"))

    # ---- helpers --------------------------------------------------------------------------------------
    @staticmethod
    def _fail(req: Request, exc: BaseException) -> None:
        if not req.future.done():
            req.future.set_exception(exc)

    @staticmethod
    def _chain(src: ReleasingFuture, dst: ReleasingFuture) -> None:
        """Resolve ``dst`` with the outcome of ``src``."""
        def done(f: ReleasingFuture) -> None:
            if dst.done():
                return
            e = f.exception()
            if e is not None:
                dst.set_exception(e)
            else:
                dst.set_result(f.result())
        src.add_done_callback(done)

    def inflight_snapshot(self) -> list[tuple[str, int, Lane, bool]]:
        with self._lock:
            return [(r.name, r.seq, r.lane, r.priority) for r in self._inflight.values()]


# ============================================================================== stop confirmation

@dataclass
class _Confirm:
    cmd: int
    payload: bytes
    confirmed_by: Callable[[], bool]
    first_ns: int
    last_ns: int
    attempts: int = 1
    futures: list[ReleasingFuture] = field(default_factory=list)
    reason: str = ""


class StopConfirmer:
    """CONFIRM class (ICD §9.3/§9.4): repeat until ACK or the FW indication; alarm after 1 s."""

    def __init__(self, channel: CommandChannel, clock: Clock = MONOTONIC, *,
                 on_confirmed: Callable[[int, str, int, str], None] | None = None,
                 on_unconfirmed: Callable[[int, str, int, str], None] | None = None,
                 poll_status: Callable[[], None] | None = None,
                 stream_on: Callable[[], bool] | None = None) -> None:
        self.channel = channel
        self.clock = clock
        self.on_confirmed = on_confirmed
        self.on_unconfirmed = on_unconfirmed
        self.poll_status = poll_status
        self.stream_on = stream_on or (lambda: True)
        self._lock = threading.Lock()
        self._active: dict[int, _Confirm] = {}
        self.unconfirmed_count = 0

    def arm(self, cmd: int, payload: bytes, fut: ReleasingFuture, confirmed_by: Callable[[], bool],
            reason: str = "") -> None:
        """Arm (or re-arm) the CONFIRM repetition. A repeated operator command re-arms with the new predicate
        (its freshness reference is the new write)."""
        now = self.clock.monotonic_ns()
        with self._lock:
            c = self._active.get(cmd)
            if c is None:
                c = _Confirm(cmd, payload, confirmed_by, now, now, 1, [fut], reason)
                self._active[cmd] = c
            else:
                c.futures.append(fut)
                c.payload = payload
                c.confirmed_by = confirmed_by
                c.attempts += 1
                c.last_ns = now

    def active(self, cmd: int | None = None) -> bool:
        with self._lock:
            return bool(self._active) if cmd is None else cmd in self._active

    def tick(self, now: int | None = None) -> None:
        now = self.clock.monotonic_ns() if now is None else now
        done: list[tuple[str, _Confirm]] = []
        resend: list[_Confirm] = []
        with self._lock:
            for cmd, c in list(self._active.items()):
                acked = any(f.done() and f.exception() is None for f in c.futures)
                if acked or c.confirmed_by():
                    del self._active[cmd]
                    done.append(("ok", c))
                elif now - c.first_ns >= CONFIRM_WINDOW_NS or c.attempts >= CONFIRM_MAX_ATTEMPTS:
                    del self._active[cmd]
                    done.append(("fail", c))
                elif now - c.last_ns >= CONFIRM_REPEAT_NS:
                    c.attempts += 1
                    c.last_ns = now
                    resend.append(c)
        for c in resend:
            fut, _t, _err = self.channel.send_priority(c.cmd, c.payload)
            with self._lock:
                c.futures.append(fut)
            if not self.stream_on() and self.poll_status is not None:
                self.poll_status()
        for what, c in done:
            name = Cmd(c.cmd).name
            if what == "ok":
                if self.on_confirmed is not None:
                    self.on_confirmed(c.cmd, name, c.attempts, c.reason)
            else:
                self.unconfirmed_count += 1
                if self.on_unconfirmed is not None:
                    self.on_unconfirmed(c.cmd, name, c.attempts, c.reason)


def link_state_for(consecutive_timeouts: int, rx_age_ns: int | None, streaming: bool) -> LinkState:
    """DEGRADED after 3 consecutive timeouts or 300 ms without any frame while streaming; LOST after 1 s
    without frames (ICD §9.3)."""
    age = rx_age_ns if rx_age_ns is not None else 0
    if age >= LOST_RX_NS:
        return LinkState.LOST
    if consecutive_timeouts >= DEGRADED_TIMEOUTS or (streaming and age >= DEGRADED_RX_NS):
        return LinkState.DEGRADED
    return LinkState.CONNECTED
