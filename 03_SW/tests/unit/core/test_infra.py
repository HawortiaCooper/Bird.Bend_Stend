"""Core infrastructure: clock, Ticker, weak observers / releasing futures, EventBus, liveness, generator jobs.

Verifies: NFR-004 (no leaks, bounded history), SAF-SW-003 (liveness gate), SW-PLT-003 (async jobs)
"""
from __future__ import annotations

import gc
import threading
import weakref

import pytest

from bend_stand.core import timing
from bend_stand.core.clock import MONOTONIC, LockstepClock, ms, wall_utc_iso
from bend_stand.core.events import EventBus
from bend_stand.core.jobs import Poll, Sleep, Worker, drive_blocking
from bend_stand.core.liveness import LivenessMonitor
from bend_stand.core.observers import ObserverList, ReleasingFuture, done_future, failed_future


@pytest.mark.req("NFR-004")
def test_lockstep_clock() -> None:
    c = LockstepClock(start_ns=0)
    assert c.is_lockstep and not MONOTONIC.is_lockstep
    c.advance(0.5)
    c.advance(ns=1)
    assert c.monotonic_ns() == 500_000_001 and ms(c.monotonic_ns()) == 500
    c.sleep(0.001)
    c.set(c.monotonic_ns() + 5)
    with pytest.raises(ValueError):
        c.advance(ns=-1)
    with pytest.raises(ValueError):
        c.set(0)
    assert wall_utc_iso(c).endswith("Z")
    assert MONOTONIC.monotonic_ns() > 0 and MONOTONIC.wall_ns() > 0
    MONOTONIC.sleep(0)


@pytest.mark.req("SAF-SW-003")
def test_ticker_absolute_deadlines() -> None:
    c = LockstepClock(start_ns=0)
    t = timing.Ticker(5_000_000, c)
    assert t.due() == 0
    c.advance(ns=5_000_000)
    assert t.due() == 1
    c.advance(ns=17_000_000)                      # 3 periods late → overruns counted, no drift
    assert t.due() == 3 and t.overruns == 2
    assert t.next_ns == 25_000_000
    with pytest.raises(ValueError):
        timing.Ticker(0, c)
    stop = threading.Event()
    stop.set()
    assert timing.Ticker(1_000_000).wait(stop) is False


@pytest.mark.req("NFR-002")
def test_timing_init_with_fake_winmm() -> None:
    class Fake:
        calls: list[str] = []

        def timeBeginPeriod(self, n: int) -> int:  # noqa: N802
            self.calls.append("begin")
            return 0

        def timeEndPeriod(self, n: int) -> int:  # noqa: N802
            self.calls.append("end")
            return 0

    timing.shutdown()
    f = Fake()
    assert timing.init(f) and timing.is_active() and timing.init(f)
    timing.shutdown()
    assert f.calls == ["begin", "end"] and not timing.is_active()
    assert timing.measure_sleep_granularity(3) > 0
    assert isinstance(timing.raise_thread_priority(1), bool)


class _Sub:
    def __init__(self) -> None:
        self.got: list = []

    def cb(self, *a) -> None:
        self.got.append(a)


@pytest.mark.req("NFR-004")
def test_observer_list_weak_lifetime() -> None:
    ol: ObserverList = ObserverList()
    s = _Sub()
    ol.add(s.cb)
    ref = weakref.ref(s)
    strong: list = []
    ol.add(strong.append)
    assert ol.call_all(1) == 2 and s.got == [(1,)] and strong == [1]
    del s
    gc.collect()
    assert ref() is None and len(ol) == 1          # the backend never keeps a subscriber alive
    with pytest.raises(TypeError):
        ol.add(lambda x: None, weak=True)
    errs: list = []
    tok = ol.add(lambda x: 1 / 0)
    ol.call_all(0, on_error=errs.append)
    assert isinstance(errs[0], ZeroDivisionError)
    ol.remove_token(tok)
    ol.clear()
    assert len(ol) == 0


@pytest.mark.req("NFR-004")
def test_releasing_future_drops_callbacks() -> None:
    f = ReleasingFuture()
    hits: list = []
    f.add_done_callback(lambda fut: hits.append(fut.result()))
    f.add_done_callback(lambda fut: 1 / 0)          # logged, not raised
    f.set_result(5)
    assert hits == [5] and f._done_callbacks == []  # noqa: SLF001
    assert done_future(3).result() == 3
    with pytest.raises(KeyError):
        failed_future(KeyError("x")).result()


@pytest.mark.req("NFR-004", "SW-PLT-002")
def test_eventbus_topics_history_and_weak() -> None:
    c = LockstepClock()
    bus = EventBus(c, maxlen=5)
    s = _Sub()
    t1 = bus.subscribe("a", s.cb)
    allr: list = []
    bus.subscribe("*", allr.append)
    for i in range(8):
        bus.publish("a" if i % 2 else "b", i)
    assert len(bus.history()) == 5 and bus.published == 8
    assert [r.payload for r in bus.history("a")] == [3, 5, 7]
    assert bus.history(limit=2)[-1].payload == 7
    assert len(s.got) == 4 and len(allr) == 8
    bus.unsubscribe(t1)
    bus.unsubscribe(9999)
    bus.publish("a", 9)
    assert len(s.got) == 4
    bus.subscribe("x", lambda r: 1 / 0)
    bus.publish("x", 1)                             # observer error is logged, never propagated
    rec = bus.log("hello", 20, k=1)
    assert rec.topic == "log" and rec.payload["text"] == "hello"


@pytest.mark.req("SAF-SW-003")
def test_liveness_gate_and_faults() -> None:
    c = LockstepClock(start_ns=0)
    lv = LivenessMonitor(c)
    assert lv.gate_open() and lv.age_ms("reader") is None
    lv.beat("reader")
    lv.beat("pipeline")
    c.advance(ns=250_000_000)
    assert not lv.gate_open()                      # reader > 200 ms
    lv.beat("reader")
    assert lv.gate_open() and lv.stale("pipeline", 200)
    lv.forget("pipeline")
    seen: list = []
    lv.on_fault = seen.append
    f = lv.record_fault("x", "boom")
    assert seen == [f] and lv.faults()[-1].reason == "boom"


@pytest.mark.req("SAF-SW-003")
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_excepthook_records_thread_fault() -> None:
    lv = LivenessMonitor()
    lv.install_excepthook()
    lv.install_excepthook()                        # idempotent
    try:
        t = threading.Thread(target=lambda: 1 / 0, name="bad")
        t.start()
        t.join()
    finally:
        lv.uninstall_excepthook()
    assert any(f.thread == "bad" and "ZeroDivisionError" in f.reason for f in lv.faults())


def _job(fut, out: list):
    out.append("start")
    v = yield fut
    out.append(v)
    yield Sleep(10_000_000)
    ok = yield Poll(lambda: len(out) > 99, 5_000_000)
    out.append(ok)
    yield None
    try:
        yield failed_future(KeyError("k"))
    except KeyError:
        out.append("caught")
    return "done"


@pytest.mark.req("SW-PLT-003")
def test_worker_lockstep_drives_generator_jobs() -> None:
    c = LockstepClock(start_ns=0)
    w = Worker(c)
    assert not w.threaded
    gate = ReleasingFuture()
    out: list = []
    res = w.submit(_job, gate, out)
    w.step(c.monotonic_ns())
    assert out == ["start"] and not res.done() and w.busy
    gate.set_result(42)
    w.step(c.monotonic_ns())
    assert out == ["start", 42]
    c.advance(ns=10_000_000)
    w.step(c.monotonic_ns())
    c.advance(ns=5_000_000)
    w.step(c.monotonic_ns())
    assert res.result() == "done" and out[-2:] == [False, "caught"] and not w.busy
    bad = w.submit(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    w.step(0)
    with pytest.raises(RuntimeError):
        bad.result()

    def yields_junk():
        yield 123
    junk = w.submit(yields_junk)
    w.step(0)
    with pytest.raises(TypeError):
        junk.result()
    with pytest.raises(RuntimeError):
        drive_blocking(_job(gate, []), c)


@pytest.mark.req("SW-PLT-003")
def test_worker_threaded_and_queue_full() -> None:
    w = Worker(MONOTONIC, threaded=True, maxlen=2)
    gate = ReleasingFuture()
    out: list = []
    w.start()
    try:
        f = w.submit(_job, gate, out)
        threading.Timer(0.02, gate.set_result, (1,)).start()
        assert f.result(timeout=5) == "done" and out[1] == 1 and out[2] is False
        g = ReleasingFuture()
        f1 = w.submit(lambda: (yield g))
        w.submit(lambda: (yield g))
        w.submit(lambda: (yield g))
        f4 = w.submit(lambda: (yield g))
        with pytest.raises(RuntimeError):
            f4.result(timeout=1)
        g.set_result(0)
        f1.result(timeout=5)
    finally:
        w.stop()
