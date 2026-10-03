"""Sample pipeline, M1 subset (SW_design §7.1–§7.3, §5.5.1): decode → time unwrap → frame gaps / duplicates /
anomalies → classify → ring buffer + sinks; FW EVENTs dispatched **in arrival order with DATA**.

The Reader puts frames into a bounded queue (4096); a full queue is a liveness fault, never a silent drop
(counter ``async_overflow``). The Pipeline drains it in its own thread (real clock) or from ``step(now)``
(lockstep). Only the Pipeline writes the ring buffer and the latest-sample record.

Rules (SWD-P1-12 b): ``d = (seq − prev) & 0xFFFF``; ``d = 0`` duplicate → dropped before processing
(``dup_frames``); ``1 ≤ d < 0x8000`` → ``d − 1`` lost (FW when the frame carries OVERRUN, else link);
``d ≥ 0x8000`` → ``seq_anomalies`` + re-base, no loss counted. A backward ``t_us`` step (≤ 2³¹) or EVENT BOOT
starts a new time epoch (display time stays continuous). Missed conversion: Δt > 1.5 × median period without
a seq gap. EVENT header SEQ gap → ``events_lost`` + immediate GET_STATUS.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/pipeline.py @37c87471 (stage structure, bounded queue,
liveness beat; rewritten for the 18-byte DATA payload and the bend-stand rules).

Implements: IF-006, IF-007, SW-ACQ-004 (loss counting), NFR-001/004 (backend part), SW-RT-005 (latest)
"""
from __future__ import annotations

import collections
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from typing import Any

import numpy as np

from bend_stand.calc.timebase import frame_gap, unwrap_step
from bend_stand.core import protocol_gen as pg
from bend_stand.core.channels import BIT_CHANNELS, RING_KEYS
from bend_stand.core.clock import Clock
from bend_stand.core.events import EventBus
from bend_stand.core.liveness import LivenessMonitor
from bend_stand.core.model import FwEvent, LinkStats
from bend_stand.core.ringbuffer import ColumnRingBuffer
from bend_stand.io import protocol as P
from bend_stand.io.framing import Frame

log = logging.getLogger("bend_stand.core.pipeline")
QUEUE_LEN = 4096
RING_CAPACITY = 86_400                 # 900 s × 96 SPS (§7.5)
RAW_STATE_OK, RAW_STATE_SATURATED, RAW_STATE_NO_DATA, RAW_STATE_SETTLING = 0, 1, 2, 3
VSTATE_OK, VSTATE_EXTRAPOLATED, VSTATE_INVALID, VSTATE_NO_DATA = 0, 1, 2, 3


@dataclass
class PipelineCounters:
    data_frames: int = 0
    event_frames: int = 0
    frames_lost_fw: int = 0
    frames_lost_link: int = 0
    dup_frames: int = 0
    seq_anomalies: int = 0
    events_lost: int = 0
    afe_missed: int = 0
    bad_payload: int = 0
    async_overflow: int = 0
    epochs: int = 0

    def as_linkstats(self) -> LinkStats:
        return LinkStats(**{f.name: getattr(self, f.name) for f in fields(self)
                            if f.name in LinkStats.__dataclass_fields__})


@dataclass(frozen=True)
class DataRow:
    """One processed DATA frame (recorder / capture sinks)."""

    t_host_ns: int
    t_us: int
    t_us_u: int
    t_dev_s: float
    frame_seq: int
    seq_lost: int
    flags: int
    status: int
    raw: int
    raw_state: int
    setpoint_um: int
    vstate: int
    epoch: int


@dataclass
class Latest:
    t_dev_s: float = float("nan")
    t_host_ns: int = 0
    raw: float = float("nan")
    raw_state: int = RAW_STATE_NO_DATA
    x_mm: float = float("nan")
    rate_sps: float | None = None
    flags: int = 0
    status: int = 0
    values: dict[str, float] = field(default_factory=dict)


class Pipeline:
    def __init__(self, clock: Clock, events: EventBus, liveness: LivenessMonitor, *,
                 on_fw_event: Callable[[P.EventPayload], None] | None = None,
                 on_events_lost: Callable[[], None] | None = None, capacity: int = RING_CAPACITY) -> None:
        self.clock = clock
        self.events = events
        self.liveness = liveness
        self.on_fw_event = on_fw_event
        self.on_events_lost = on_events_lost
        self.ring = ColumnRingBuffer(RING_KEYS, capacity)
        self._q: collections.deque[Frame] = collections.deque()
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sinks: list[Callable[[DataRow], None]] = []
        self.event_sinks: list[Callable[[FwEvent], None]] = []
        self.counters = PipelineCounters()
        self.latest = Latest()
        self._lock = threading.Lock()
        self.reset_link()

    # ---- state ----------------------------------------------------------------------------------------
    def reset_link(self) -> None:
        """New connection: frame_seq / time references restart (the display time stays continuous)."""
        self.prev_seq: int | None = None
        self.prev_t_raw: int | None = None
        self.prev_t_u: int | None = None
        self.epoch = 0
        self.t0_us: int | None = None
        self.dev_offset_us = 0
        self.last_dev_us: int | None = None
        self.periods: collections.deque[int] = collections.deque(maxlen=16)
        self.prev_sample_t_u: int | None = None
        self.prev_event_seq: int | None = None
        self.lost_total = 0

    def new_epoch(self) -> None:
        """EVENT BOOT or a backward time step: next frame starts a new time epoch; seq reference resets."""
        self.prev_t_raw = None
        self.prev_t_u = None
        self.prev_seq = None
        self.prev_sample_t_u = None
        self.epoch += 1
        self.counters.epochs += 1

    # ---- input (Reader thread) --------------------------------------------------------------------------
    def put(self, fr: Frame) -> bool:
        with self._cv:
            if len(self._q) >= QUEUE_LEN:
                self.counters.async_overflow += 1
                overflow = True
            else:
                self._q.append(fr)
                overflow = False
                self._cv.notify()
        if overflow:
            self.liveness.record_fault("pipeline", "async queue full")
        return not overflow

    def queued(self) -> int:
        with self._cv:
            return len(self._q)

    # ---- thread -------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None or self.clock.is_lockstep:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="bend-pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(1.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                if not self._q:
                    self._cv.wait(0.010)
            try:
                self.step()
            except Exception:  # noqa: BLE001 — a bad frame must not stop the pipeline
                log.exception("pipeline step failed")

    # ---- processing ---------------------------------------------------------------------------------------
    def step(self, now_ns: int | None = None) -> int:
        n = 0
        while True:
            with self._cv:
                if not self._q:
                    break
                batch = []
                while self._q and len(batch) < 64:
                    batch.append(self._q.popleft())
            for fr in batch:
                if fr.type == pg.AsyncType.DATA:
                    self._data(fr)
                elif fr.type == pg.AsyncType.EVENT:
                    self._event(fr)
                n += 1
        self.liveness.beat("pipeline", self.clock.monotonic_ns() if now_ns is None else now_ns)
        return n

    def _event(self, fr: Frame) -> None:
        try:
            ev = P.decode_event(fr.payload)
        except ValueError:
            self.counters.bad_payload += 1
            return
        self.counters.event_frames += 1
        if self.prev_event_seq is not None:
            gap = (fr.seq - self.prev_event_seq - 1) & 0xFF
            if gap and ev.code != pg.Event.BOOT:
                self.counters.events_lost += gap
                cb = self.on_events_lost
                if cb is not None:
                    cb()
        self.prev_event_seq = fr.seq
        if ev.code == pg.Event.BOOT:
            self.new_epoch()
        cb2 = self.on_fw_event
        if cb2 is not None:
            cb2(ev)
        fe = FwEvent(fr.seq, ev.t_us, ev.code, ev.name, ev.arg, P.event_arg_name(ev.code, ev.arg), ev.value,
                     ev.value2, fr.t_ns)
        for s in self.event_sinks:
            s(fe)
        self.events.publish("fw.event", fe)

    def _data(self, fr: Frame) -> None:
        p = fr.payload
        if len(p) < pg.DATA_LEN or p[4] != pg.PAYLOAD_VERSION:
            self.counters.bad_payload += 1
            return
        s = P.decode_data(p)
        # frame sequence (SWD-P1-12 b)
        g = frame_gap(self.prev_seq, s.frame_seq)
        if g.kind == "dup":
            self.counters.dup_frames += 1
            return
        lost = 0
        if g.kind == "lost":
            lost = g.lost
            if s.flags & pg.DataFlags.OVERRUN:
                self.counters.frames_lost_fw += lost
            else:
                self.counters.frames_lost_link += lost
            self.lost_total += lost
        elif g.kind == "anomaly":
            self.counters.seq_anomalies += 1
            self.events.log(f"FRAME_SEQ_ANOMALY {self.prev_seq} → {s.frame_seq}", logging.WARNING)
        self.prev_seq = s.frame_seq
        # time base
        u = unwrap_step(self.prev_t_raw, self.prev_t_u, s.t_us)
        if u.new_epoch:
            self.new_epoch()
            self.prev_seq = s.frame_seq
            self.events.log("FW_RESET: device time stepped backwards", logging.WARNING)
            u = unwrap_step(None, None, s.t_us)
        if self.prev_t_u is None:                      # first frame of an epoch: keep display time continuous
            if self.t0_us is None or self.last_dev_us is None:
                self.t0_us = u.t_u
                self.dev_offset_us = -u.t_u
            else:
                med = int(np.median(self.periods)) if self.periods else 12_500
                self.dev_offset_us = self.last_dev_us + med - u.t_u
        self.prev_t_raw, self.prev_t_u = s.t_us, u.t_u
        dev_us = u.t_u + self.dev_offset_us
        self.last_dev_us = dev_us
        t_dev = dev_us / 1e6
        # classify
        no_data = bool(s.status & pg.DataStatus.NO_AFE_DATA) or s.afe_raw == pg.AFE_NO_DATA
        if no_data:
            raw_state, vstate = RAW_STATE_NO_DATA, VSTATE_NO_DATA
        elif s.status & pg.DataStatus.AFE_SATURATED or s.afe_raw in (pg.RAW_MIN, pg.RAW_MAX):
            raw_state, vstate = RAW_STATE_SATURATED, VSTATE_INVALID
        elif s.status & pg.DataStatus.AFE_SETTLING:
            raw_state, vstate = RAW_STATE_SETTLING, VSTATE_INVALID
        else:
            raw_state, vstate = RAW_STATE_OK, VSTATE_OK
        # sample rate / missed conversions (fallback frames excluded)
        if not no_data:
            if self.prev_sample_t_u is not None and g.kind in ("next", "lost"):
                dt = u.t_u - self.prev_sample_t_u
                if g.kind == "next" and self.periods and dt > 1.5 * float(np.median(self.periods)):
                    self.counters.afe_missed += max(1, round(dt / float(np.median(self.periods))) - 1)
                elif g.kind == "next" and dt > 0:
                    self.periods.append(dt)
            self.prev_sample_t_u = u.t_u
        rate = 1e6 / float(np.median(self.periods)) if len(self.periods) >= 4 else None
        self.counters.data_frames += 1
        # ring row
        row = np.full(len(RING_KEYS), np.nan, np.float32)
        row[0] = np.nan if no_data else s.afe_raw
        row[1] = s.setpoint_um / 1000.0
        row[2] = np.nan if rate is None else rate
        row[3] = self.lost_total
        row[4] = vstate
        for j, (_k, _n, bit, grp) in enumerate(BIT_CHANNELS):
            v = s.flags if grp == "flags" else s.status
            row[5 + j] = (v >> bit) & 1
        self.ring.write(np.array([t_dev]), row.reshape(1, -1))
        with self._lock:
            lt = self.latest
            lt.t_dev_s, lt.t_host_ns = t_dev, fr.t_ns
            lt.raw, lt.raw_state = (float("nan") if no_data else float(s.afe_raw)), raw_state
            lt.x_mm, lt.rate_sps, lt.flags, lt.status = s.setpoint_um / 1000.0, rate, s.flags, s.status
            lt.values = {k: float(row[i]) for i, k in enumerate(RING_KEYS)}
        dr = DataRow(fr.t_ns, s.t_us, u.t_u, t_dev, s.frame_seq, lost, s.flags, s.status,
                     0 if no_data else s.afe_raw, raw_state, s.setpoint_um, vstate, self.epoch)
        for sink in self.sinks:
            try:
                sink(dr)
            except Exception:  # noqa: BLE001
                log.exception("pipeline sink failed")

    def latest_copy(self) -> Latest:
        with self._lock:
            lt = self.latest
            return Latest(lt.t_dev_s, lt.t_host_ns, lt.raw, lt.raw_state, lt.x_mm, lt.rate_sps, lt.flags, lt.status,
                          dict(lt.values))

    def info(self) -> dict[str, Any]:
        return {"queued": self.queued(), "epoch": self.epoch}
