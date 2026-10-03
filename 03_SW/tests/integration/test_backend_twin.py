"""Implementer B's Backend facade (SW_design §15.1, the public API) against the FW host twin over tcp://.

Connect sequence (§5.2 / ICD §9.5), config write-verify + NVM save / reboot / restore incl. a power cut
during SAVE, stream start/stop with gap-free DATA and frame-gap detection (FW attribution), HALT / PAUSE /
RESUME / clears through the priority path. The twin's wire log is the ground truth.

xfail (not run) until ``bend_stand.core.backend.Backend`` exists (B M1 WP-B11). Names follow SW_design v0.3.1
§15.1; a difference found when B delivers is an Integrator finding for this file, never a change to B's code.
The wire-level equivalents with B's io layer run today in test_twin_link_io.py.

Verifies: SW-PLT-003, SW-CFG-003, SW-CFG-004, SW-ACQ-001, IF-005, IF-007, IF-008, IF-011, SW-STOP-002,
          SW-STOP-003, D-31, D-34, FW-NVM-002, SYS-008
"""
from __future__ import annotations

import dataclasses
import time

import pytest

pytestmark = [pytest.mark.twin, pytest.mark.rt, pytest.mark.needs_b("bend_stand.core.backend", "Backend")]

EDITS = {"stream.fallback_hz": 20, "io.release_ms": 77, "safety.link_timeout_ms": 1500}
TIMEOUT = 5.0


@pytest.fixture
def be(twin_rt):
    from bend_stand.core.backend import Backend

    b = Backend()
    b.start()
    b.connect_async(twin_rt.endpoint).result(TIMEOUT)
    yield b
    b.shutdown()


def _rx_types(tw) -> list[int]:
    return [w["type"] for w in tw.wire_log if w["dir"] == "rx"]


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


@pytest.mark.req("SW-PLT-003", "IF-008")
def test_backend_connect_sequence(be, twin_rt):
    from bend_stand.core import params_gen as pgen

    rx = _rx_types(twin_rt)
    assert rx[0] == 0x02                                         # GET_INFO first
    assert rx.index(0x03) < rx.index(0x10)                       # GET_STATUS, then GET_ALL_PARAMS (ICD §9.5)
    # session values written (if they differ) and verified by read-back (SAF-SW-002; M1: defaults, no calibration)
    acc = [w for w in twin_rt.wire_log if w["dir"] == "rx" and w["type"] in (0x11, 0x12)]
    ids = {int.from_bytes(bytes.fromhex(w["hex"])[6:8], "little") for w in acc}
    assert {pgen.BY_KEY[k].id for k in ("safety.zero_raw", "safety.load_raw_min", "safety.load_raw_max")} <= ids
    assert not [t for t in rx if t in (0x30, 0x32, 0x33, 0x34, 0x35)]   # never enables / homes / moves on connect
    assert 0x20 in rx                                            # stream on by default (SW-ACQ-001)
    vals = be.config.values()
    assert set(vals) == {m.key for m in pgen.PARAMS}


@pytest.mark.req("SW-CFG-003", "SW-CFG-004", "FW-NVM-001")
def test_backend_write_verify_save_reboot_restore(be, twin_rt):
    rep = be.config.write_and_verify_async(EDITS).result(TIMEOUT)
    assert all("OK" in str(i.status) for i in rep.items), rep
    be.config.save_async().result(TIMEOUT)
    be.config.reboot_async().result(TIMEOUT)
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and "CONNECTED" not in str(be.status().link):
        time.sleep(0.05)
    assert [r["cause"] for r in twin_rt.resets] == ["software"]
    vals = be.config.read_all_async().result(TIMEOUT)
    assert all(vals[k] == v for k, v in EDITS.items())
    rx = _rx_types(twin_rt)
    assert rx.count(0x04) == 1 and rx.count(0x13) == 1           # REBOOT / SAVE (VERIFY class) never re-sent


@pytest.mark.req("FW-NVM-002", "SW-CFG-004")
def test_backend_power_cut_during_save(be, twin_rt):
    be.config.write_and_verify_async({"stream.fallback_hz": 12}).result(TIMEOUT)
    be.config.save_async().result(TIMEOUT)
    be.config.write_and_verify_async({"stream.fallback_hz": 13}).result(TIMEOUT)
    assert twin_rt.act("flash", cut_after_word=3)["ok"]
    with pytest.raises(Exception):                               # VERIFY: timeout resolved as not executed
        be.config.save_async().result(TIMEOUT)
    assert twin_rt.resets[-1]["cause"] == "power"
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and "CONNECTED" not in str(be.status().link):
        time.sleep(0.05)
    assert be.config.read_all_async().result(TIMEOUT)["stream.fallback_hz"] == 12


@pytest.mark.req("SW-ACQ-001", "IF-007", "FW-STR-004")
def test_backend_stream_gap_free_and_gap_detection(be, twin_rt):
    time.sleep(2.0)
    lost0 = _counter(be.status(), "lost") or 0
    assert lost0 == 0
    twin_rt.act("inject", fault="tx_congestion", duration_ms=5 * 12.5 - 1)
    time.sleep(1.0)
    dropped = [s for s in twin_rt.act("query", what="sent")["sent"] if s["dropped"]]
    assert len(dropped) == 5
    assert (_counter(be.status(), "lost") or 0) == 5             # attributed to the FW (OVERRUN) by B's pipeline
    be.stream_stop_async().result(TIMEOUT)
    n = len([w for w in twin_rt.wire_log if w["dir"] == "tx" and w["type"] == 0xC0])
    time.sleep(0.3)
    assert len([w for w in twin_rt.wire_log if w["dir"] == "tx" and w["type"] == 0xC0]) == n


@pytest.mark.req("SW-STOP-002", "SW-STOP-003", "IF-011", "D-31", "D-34")
def test_backend_halt_pause_resume_clears(be, twin_rt):
    assert be.pause("test").sent
    assert be.halt("test").sent
    time.sleep(0.3)
    rx0 = _rx_types(twin_rt)
    assert rx0.count(0x37) >= 1 and rx0.count(0x3B) >= 1
    c = be.clear_stop_async(confirmed=True).result(TIMEOUT)
    assert "OK" in str(c.outcome)
    time.sleep(0.3)
    ev = [(int.from_bytes(bytes.fromhex(w["hex"])[10:12], "little"), int.from_bytes(bytes.fromhex(w["hex"])[12:14], "little"))
          for w in twin_rt.wire_log if w["dir"] == "tx" and w["type"] == 0xC1]
    assert (7, 0) in ev and (9, 2) in ev                         # HALT_CLEARED, PAUSE_CLEARED(HALT_CLEAR) (D-31)
    assert _rx_types(twin_rt).count(0x38) == 1                   # HALT_CLEAR is VERIFY: one frame (D-34)
