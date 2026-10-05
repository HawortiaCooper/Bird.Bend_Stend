"""Implementer B's Backend facade (SW_design §15.1, the public API) against the FW host twin.

Connect sequence (§5.2 / ICD §9.5), config write-verify + NVM save / reboot / restore incl. a power cut
during SAVE, stream start/stop with gap-free DATA and frame-gap detection (FW attribution), HALT / PAUSE /
RESUME / clears through the priority path. The twin's wire log is the ground truth.

OBS-M2-09 (M3): the backend and the twin run in **one lock-step virtual time** (``twin_rig.Rig``: B's
``clock="lockstep"`` + ``TestHooks.advance``, the twin advanced to the same instant every 1 ms step), so host
load can no longer change a result (the realtime ``test_backend_stream_gap_free_and_gap_detection`` flaked
under parallel load: 5 dropped frames were counted against a wall-clock sleep). One realtime smoke over B's
``TcpTransport`` (``tcp://``) stays, with event-driven waits only.

Verifies: SW-PLT-003, SW-CFG-003, SW-CFG-004, SW-ACQ-001, IF-005, IF-007, IF-008, IF-011, SW-STOP-002,
          SW-STOP-003, D-31, D-34, FW-NVM-002, SYS-008
"""
from __future__ import annotations

import dataclasses
import time

import pytest

pytestmark = [pytest.mark.twin, pytest.mark.needs_b("bend_stand.core.backend", "Backend")]

EDITS = {"stream.fallback_hz": 20, "io.release_ms": 77, "safety.link_timeout_ms": 1500}
TIMEOUT = 5.0


@pytest.fixture
def rig(twin_exe, tmp_path):
    from twin_rig import Rig

    r = Rig(twin_exe, tmp_path / "twin", recordings=tmp_path / "rec")
    r.connect()
    yield r
    r.close()


def _counter(obj, *words: str) -> int | None:
    """First integer field of a (nested) dataclass whose name contains all `words` (status layout not frozen)."""
    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            v = getattr(obj, f.name)
            if isinstance(v, int) and all(w in f.name for w in words):
                return v
            r = _counter(v, *words)
            if r is not None:
                return r
    return None


def _connected(be) -> bool:
    return "CONNECTED" in str(be.status().link.state)


def _check_connect_sequence(rx: list[int], wire_log, be) -> None:
    from bend_stand.core import params_gen as pgen

    assert rx[0] == 0x02                                         # GET_INFO first
    assert rx.index(0x03) < rx.index(0x10)                       # GET_STATUS, then GET_ALL_PARAMS (ICD §9.5)
    # session values written (if they differ) and verified by read-back (SAF-SW-002; M1: defaults, no calibration)
    acc = [w for w in wire_log if w["dir"] == "rx" and w["type"] in (0x11, 0x12)]
    ids = {int.from_bytes(bytes.fromhex(w["hex"])[6:8], "little") for w in acc}
    assert {pgen.BY_KEY[k].id for k in ("safety.zero_raw", "safety.load_raw_min", "safety.load_raw_max")} <= ids
    assert not [t for t in rx if t in (0x30, 0x32, 0x33, 0x34, 0x35)]   # never enables / homes / moves on connect
    assert 0x20 in rx                                            # stream on by default (SW-ACQ-001)
    assert set(be.config.values()) == {m.key for m in pgen.PARAMS}


@pytest.mark.req("SW-PLT-003", "IF-008")
def test_backend_connect_sequence(rig):
    _check_connect_sequence(rig.rx_types(), rig.tw.wire_log, rig.be)


@pytest.mark.rt
@pytest.mark.req("SW-PLT-003", "IF-008", "SYS-008")
def test_backend_connect_sequence_tcp_realtime(twin_rt):
    """Realtime smoke over B's production TcpTransport (the only test here on the wall clock; event-driven waits)."""
    from bend_stand.core.backend import Backend

    b = Backend()
    b.start()
    try:
        b.connect_async(twin_rt.endpoint).result(TIMEOUT)
        _check_connect_sequence([w["type"] for w in twin_rt.wire_log if w["dir"] == "rx"], twin_rt.wire_log, b)
        deadline = time.monotonic() + TIMEOUT                    # DATA arrives (any rate: wall clock not asserted)
        while time.monotonic() < deadline and not (_counter(b.status(), "data", "frames") or 0):
            time.sleep(0.05)
        assert (_counter(b.status(), "data", "frames") or 0) > 0
    finally:
        b.shutdown()


@pytest.mark.req("SW-CFG-003", "SW-CFG-004", "FW-NVM-001")
def test_backend_write_verify_save_reboot_restore(rig):
    be = rig.be
    rep = rig.result(be.config.write_and_verify_async(EDITS))
    assert all("OK" in str(i.status) for i in rep.items), rep
    rig.result(be.config.save_async())
    rig.result(be.config.reboot_async())
    assert rig.run_until(lambda: _connected(be) and rig.tw.resets, 5000)
    assert [r["cause"] for r in rig.tw.resets] == ["software"]
    vals = rig.result(be.config.read_all_async())
    assert all(vals[k] == v for k, v in EDITS.items())
    rx = rig.rx_types()
    assert rx.count(0x04) == 1 and rx.count(0x13) == 1           # REBOOT / SAVE (VERIFY class) never re-sent


@pytest.mark.req("FW-NVM-002", "SW-CFG-004")
def test_backend_power_cut_during_save(rig):
    be = rig.be
    rig.result(be.config.write_and_verify_async({"stream.fallback_hz": 12}))
    rig.result(be.config.save_async())
    rig.result(be.config.write_and_verify_async({"stream.fallback_hz": 13}))
    rig.act("flash", cut_after_word=3)
    with pytest.raises(Exception):                               # VERIFY: timeout resolved as not executed
        rig.result(be.config.save_async(), 10_000)
    assert rig.tw.resets[-1]["cause"] == "power"
    assert rig.run_until(lambda: _connected(be), 10_000)
    assert rig.result(be.config.read_all_async())["stream.fallback_hz"] == 12


@pytest.mark.req("SW-ACQ-001", "IF-007", "FW-STR-004")
def test_backend_stream_gap_free_and_gap_detection(rig):
    """Lock-step (OBS-M2-09): 2 s gap-free, then TX congestion for 5 sample periods minus 1 ms → exactly the frames
    the twin dropped are counted lost and attributed to the FW (OVERRUN), never to the link."""
    be = rig.be
    rig.advance(2000)
    assert (_counter(be.status(), "lost") or 0) == 0
    rig.act("inject", fault="tx_congestion", duration_ms=5 * 12.5 - 1)
    rig.advance(1000)
    dropped = [s for s in rig.tw.act("query", what="sent")["sent"] if s["dropped"]]
    assert len(dropped) in (4, 5)                                # 61.5 ms window: 4 or 5 conversions by phase
    assert (_counter(be.status(), "lost", "fw") or 0) == len(dropped)
    assert (_counter(be.status(), "lost", "link") or 0) == 0
    rig.result(be.stream_stop_async())
    n = len([w for w in rig.tw.wire_log if w["dir"] == "tx" and w["type"] == 0xC0])
    rig.advance(300)
    assert len([w for w in rig.tw.wire_log if w["dir"] == "tx" and w["type"] == 0xC0]) == n


@pytest.mark.req("SW-STOP-002", "SW-STOP-003", "IF-011", "D-31", "D-34")
def test_backend_halt_pause_resume_clears(rig):
    be = rig.be
    assert be.pause("test").sent
    assert be.halt("test").sent
    rig.advance(300)
    rx0 = rig.rx_types()
    assert rx0.count(0x37) >= 1 and rx0.count(0x3B) >= 1
    c = rig.result(be.clear_stop_async(confirmed=True))
    assert "OK" in str(c.outcome)
    rig.advance(300)
    ev = rig.tx_events()
    assert (7, 0) in ev and (9, 2) in ev                         # HALT_CLEARED, PAUSE_CLEARED(HALT_CLEAR) (D-31)
    assert rig.rx_types().count(0x38) == 1                       # HALT_CLEAR is VERIFY: one frame (D-34)
