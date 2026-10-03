"""Pipeline (M1 subset) with synthetic frames, ring buffer / DataView, channel registry.

Verifies: IF-006, IF-007, SW-ACQ-004, SW-RT-002, SW-RT-003, SW-RT-005, NFR-001, NFR-004
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from bend_stand.core import protocol_gen as pg
from bend_stand.core.channels import BIT_CHANNELS, RING_KEYS, ChannelRegistry, bit_key
from bend_stand.core.clock import LockstepClock
from bend_stand.core.dataview import DataView
from bend_stand.core.events import EventBus
from bend_stand.core.liveness import LivenessMonitor
from bend_stand.core.pipeline import QUEUE_LEN, Pipeline
from bend_stand.core.ringbuffer import ColumnRingBuffer
from bend_stand.io import protocol as P
from bend_stand.io.framing import Frame

DS, DF = pg.DataStatus, pg.DataFlags


class Rig:
    def __init__(self, capacity: int = 86_400) -> None:
        self.c = LockstepClock(start_ns=0)
        self.bus = EventBus(self.c)
        self.fw: list = []
        self.lost_calls = 0
        self.p = Pipeline(self.c, self.bus, LivenessMonitor(self.c), on_fw_event=self.fw.append,
                          on_events_lost=self._lost, capacity=capacity)
        self.rows: list = []
        self.p.sinks.append(self.rows.append)
        self.view = DataView(self.p, self.c.monotonic_ns)

    def _lost(self) -> None:
        self.lost_calls += 1

    def data(self, seq: int, t_us: int, raw: int = 1000, flags: int = 0, status: int = 0, sp: int = 5000,
             version: int = 1) -> None:
        s = P.DataSample(t_us & 0xFFFFFFFF, version, flags, raw, sp, seq & 0xFFFF, status)
        self.p.put(Frame(pg.AsyncType.DATA, seq & 0xFF, P.encode_data(s), self.c.monotonic_ns()))

    def event(self, seq: int, code: int, arg: int = 0) -> None:
        self.p.put(Frame(pg.AsyncType.EVENT, seq & 0xFF, P.encode_event(P.EventPayload(0, code, arg, 0, 0)),
                         self.c.monotonic_ns()))


@pytest.mark.req("IF-006", "IF-007")
def test_wrap_unwrap_and_continuous_display_time() -> None:
    r = Rig()
    t = 0xFFFFFFFF - 25_000
    for i in range(5):
        r.data(i, t + i * 12_500)
    r.p.step()
    assert [row.t_us_u for row in r.rows] == [t + i * 12_500 for i in range(5)]   # crosses 2³² without a jump
    assert r.rows[-1].t_dev_s == pytest.approx(0.05)
    assert r.p.counters.data_frames == 5 and r.p.counters.epochs == 0


@pytest.mark.req("IF-006")
def test_boot_and_backward_step_start_new_epoch() -> None:
    r = Rig()
    for i in range(10):
        r.data(i, 5_000_000 + i * 12_500)
    r.event(0, pg.Event.BOOT, 1)
    for i in range(3):
        r.data(i, 1_000 + i * 12_500)                 # seq restarts after BOOT: no losses / anomalies
    r.data(3, 500)                                     # backward step without BOOT → FW_RESET epoch
    r.p.step()
    c = r.p.counters
    assert c.epochs == 2 and c.seq_anomalies == 0 and c.frames_lost_link == 0
    t = [row.t_dev_s for row in r.rows]
    assert all(b > a for a, b in zip(t, t[1:], strict=False))     # display time stays monotonic
    assert r.fw[0].code == pg.Event.BOOT
    assert any("FW_RESET" in str(e.payload) for e in r.bus.history("log"))


@pytest.mark.req("IF-007", "SW-ACQ-004")
def test_duplicates_losses_and_anomalies() -> None:
    r = Rig()
    r.data(10, 0)
    r.data(11, 12_500)
    r.data(11, 12_500)                                 # duplicate: dropped before processing
    r.data(14, 50_000)                                 # 2 lost on the link
    r.data(17, 87_500, flags=DF.OVERRUN)               # 2 lost in the FW (OVERRUN)
    r.data(3, 100_000)                                 # backward seq: anomaly, re-base, no loss
    r.data(4, 112_500)
    r.p.step()
    c = r.p.counters
    assert (c.dup_frames, c.frames_lost_link, c.frames_lost_fw, c.seq_anomalies) == (1, 2, 2, 1)
    assert len(r.rows) == 6 and [row.seq_lost for row in r.rows] == [0, 0, 2, 2, 0, 0]
    assert c.as_linkstats().dup_frames == 1


@pytest.mark.req("IF-007")
def test_missed_conversions_fallback_and_bad_payload() -> None:
    r = Rig()
    for i in range(6):
        r.data(i, i * 12_500)
    r.data(6, 6 * 12_500 + 25_000)                     # one conversion missed without a seq gap
    r.data(7, 8 * 12_500 + 5_000, raw=pg.AFE_NO_DATA, status=DS.NO_AFE_DATA | DS.AFE_STALE)
    r.data(8, 9 * 12_500, raw=pg.RAW_MAX, status=DS.AFE_SATURATED)
    r.data(9, 10 * 12_500, status=DS.AFE_SETTLING)
    r.data(10, 0, version=2)                           # unknown payload version: not decoded
    r.p.step()
    c = r.p.counters
    assert c.afe_missed == 2 and c.bad_payload == 1                 # Δt = 3 periods → 2 missed
    assert [row.raw_state for row in r.rows[-3:]] == [2, 1, 3] and [row.vstate for row in r.rows[-3:]] == [3, 2, 2]
    assert r.p.latest_copy().rate_sps == pytest.approx(80.0)


@pytest.mark.req("IF-007")
def test_event_seq_gap_counts_lost_events() -> None:
    r = Rig()
    r.event(5, pg.Event.STOPPED, 1)
    r.event(6, pg.Event.MOVE_DONE, 5)
    r.event(9, pg.Event.HALT_SET, 1)
    r.p.step()
    assert r.p.counters.events_lost == 2 and r.lost_calls == 1
    assert [e.name for e in r.bus.history() if e.topic == "fw.event" for e in [e.payload]] == \
        ["STOPPED", "MOVE_DONE", "HALT_SET"]
    assert r.fw[0].name == "STOPPED"
    r.p.put(Frame(pg.AsyncType.EVENT, 10, b"\x00", 0))
    r.p.step()
    assert r.p.counters.bad_payload == 1


@pytest.mark.req("NFR-004")
def test_queue_overflow_is_a_liveness_fault() -> None:
    r = Rig()
    for i in range(QUEUE_LEN + 3):
        r.data(i, i * 12_500)
    assert r.p.counters.async_overflow == 3
    assert r.p.liveness.faults()[-1].thread == "pipeline"
    assert r.p.queued() == QUEUE_LEN


@pytest.mark.req("SW-RT-003", "NFR-001")
@pytest.mark.parametrize("window_s", [5, 30, 120, 600])
def test_snapshot_exact_px_columns(window_s: float) -> None:
    r = Rig()
    n = int(window_s * 80 * 1.2)
    t_us = (np.arange(n) * 12_500).tolist()
    for i, t in enumerate(t_us):
        r.data(i, t, raw=i % 1000, status=DS.AFE_SETTLING if i == n - 1 else 0)
        if i % 4096 == 4095:
            r.p.step()
    r.p.step()
    snap = r.view.snapshot(["raw", "bit.paused"], window_s, 640)
    assert snap.t_col_s.shape == (640,) and snap.t_col_s[-1] <= 0 and snap.t_col_s[0] >= -window_s
    s = snap.series["raw"]
    assert s.lo.shape == s.hi.shape == s.vstate.shape == (640,)
    assert np.nanmax(s.hi) <= 999 and np.nanmin(s.lo) >= 0
    assert s.vstate[-1] == 2                           # worst-of (settling sample in the last column)
    assert (s.vstate != 3).all()                       # no empty column inside a filled window
    assert snap.t_end_dev_s == pytest.approx((n - 1) * 0.0125)


@pytest.mark.req("SW-RT-003")
def test_snapshot_empty_and_xy() -> None:
    r = Rig()
    snap = r.view.snapshot(["raw"], 10, 100)
    assert np.isnan(snap.series["raw"].lo).all() and (snap.series["raw"].vstate == 3).all()
    for i in range(10_000):
        r.data(i, i * 12_500, raw=i, sp=i)
        if i % 4000 == 3999:
            r.p.step()
    r.p.step()
    xy = r.view.xy("x_mm", "raw", None, max_points=400)
    assert len(xy.x) <= 400 and xy.y.max() == 9999 and xy.y.min() == 0
    xy2 = r.view.xy("x_mm", "raw", 1.0)
    assert len(xy2.x) == 81
    assert len(r.view.sequence_trace().x) == 0


@pytest.mark.req("SW-RT-005")
def test_latest_states() -> None:
    r = Rig()
    assert r.view.latest("raw").state == "n/a"
    r.data(0, 0, raw=42)
    r.p.step()
    lt = r.view.latest("raw")
    assert (lt.value, lt.state) == (42.0, "OK")
    assert r.view.latest("nope").state == "n/a"
    assert r.view.latest("rate_sps").state == "n/a"    # not enough periods yet
    r.data(1, 12_500, raw=pg.RAW_MAX, status=DS.AFE_SATURATED)
    r.p.step()
    assert r.view.latest("raw").state == "SATURATED"
    r.data(2, 25_000, status=DS.AFE_SETTLING)
    r.p.step()
    assert r.view.latest("raw").state == "INVALID"
    r.data(3, 37_500, raw=pg.AFE_NO_DATA, status=DS.NO_AFE_DATA)
    r.p.step()
    assert r.view.latest("raw").state == "n/a"
    r.c.advance(ns=600_000_000)
    assert r.view.latest("x_mm").state == "STALE"


@pytest.mark.req("SW-RT-002")
def test_channel_registry_generated_bits() -> None:
    reg = ChannelRegistry()
    bits = [c.label for c in reg.channels() if c.group.startswith("status.")]
    assert bits == [n for n in pg.DATA_FLAGS_BITS if n] + [n for n in pg.DATA_STATUS_BITS if n]
    assert reg.get(bit_key("PAUSED")).key == "bit.paused"
    assert not reg.get("F_N").available and reg.get("F_N").reason
    hits: list = []
    reg.on_change = lambda: hits.append(1)
    reg.set_available("F_N", True)
    reg.set_available("F_N", True)
    assert hits == [1] and reg.get("F_N").available
    n_bits = sum(1 for n in (*pg.DATA_FLAGS_BITS, *pg.DATA_STATUS_BITS) if n)      # retired bits excluded (v0.5)
    assert set(RING_KEYS) <= set(reg.keys()) and len(BIT_CHANNELS) == n_bits == 23
    assert reg.get("raw").dimension == "counts" and reg.get(bit_key("PAUSED")).dimension == "bits"   # GRQ-B-21
    assert reg.get("F_N").dimension == "force"


@pytest.mark.req("NFR-001")
def test_pipeline_per_frame_cost() -> None:
    r = Rig()
    n = 4000
    for i in range(n):
        r.data(i, i * 12_500)
    t0 = time.perf_counter()
    r.p.step()
    per = (time.perf_counter() - t0) / n
    assert per < 1e-3                                  # ≤ 1 ms per frame (§16)


@pytest.mark.req("NFR-004")
def test_ring_buffer_bounded() -> None:
    rb = ColumnRingBuffer(("a",), 1000)
    for k in range(5):
        rb.write(np.arange(700) + k * 700.0, {"a": np.arange(700, dtype=np.float32)})
    assert rb.size == rb.capacity == 1024 and rb.n_total == 3500
    rb.write(np.arange(5000) + 1e5, np.zeros((5000, 1)))
    assert rb.size == 1024
    rb.clear()
    assert rb.latest_t() is None and rb.latest(["a"])["a"] != rb.latest(["a"])["a"]
    t, cols = rb.raw_window(["a"])
    assert len(t) == 0 and len(cols["a"]) == 0


@pytest.mark.req("IF-006")
def test_pipeline_thread_mode() -> None:
    from bend_stand.core.clock import MONOTONIC

    p = Pipeline(MONOTONIC, EventBus(), LivenessMonitor())
    p.start()
    p.start()
    try:
        s = P.DataSample(0, 1, 0, 7, 0, 0, 0)
        p.put(Frame(pg.AsyncType.DATA, 0, P.encode_data(s), time.monotonic_ns()))
        for _ in range(200):
            if p.counters.data_frames:
                break
            time.sleep(0.005)
    finally:
        p.stop()
    assert p.counters.data_frames == 1 and p.info()["queued"] == 0
