"""Validator E - M2 twin suite: inputs, latches, driver power, load limit (PRE-WRITTEN, gated).

Oracle: val_oracles/latch_ref.py (SRS v0.5.1 SAF-FW-005/006/008/009/011/013/014/024/025, FW-SW-005,
ICD v0.5 §5.5/§6.2). CR-01 / D-36: there is no STOP-button input; `button stop` / `wire stop` are
retired twin names and must answer ok = false (checked here as a CR-01 regression).

Verifies: SAF-FW-005, SAF-FW-006, SAF-FW-008, SAF-FW-009, SAF-FW-011, SAF-FW-013, SAF-FW-014,
          SAF-FW-024, SAF-FW-025, FW-SW-005
"""
from __future__ import annotations

import pytest

import latch_ref as lr
import ref_codec as rc
import vhelp_m2 as m


def _jogging(v, speed=2_000):
    m.ready(v, x_um=100_000)
    assert m.jog(v, speed)["status"] == "OK"
    v.advance(150)
    assert m.state(v) == "JOG"


def test_cr01_stop_button_names_retired(v):
    """CR-01 regression (D-36): no STOP-button input in the twin vocabulary or the protocol tables."""
    assert v.tw.act("button", name="stop", pressed=True)["ok"] is False
    assert v.tw.act("wire", input="stop", broken=True)["ok"] is False
    assert rc.DATA_STATUS[9] == "" and rc.IO[3] == "" and 22 not in rc.EVENT_NAME
    assert "STOP_BUTTON" not in rc.STOP_CAUSE


def test_estop_reaction_same_event(v):
    """TC-SAF-FW-005-01 (T): E-stop open while jogging -> hal_step_abort in the same virtual instant,
    ENA disabled <= 1 ms, ESTOP latched, HOMED/VALID cleared, next DATA ESTOP = 1."""
    m.need(v, "MOTION", "HOMING")
    _jogging(v)
    v.ok("STREAM_START")
    t_e = v.tw.now_us
    v.tw.act("estop", open=True, drv_power_follows=False)
    v.advance(2)
    ab = m.seam(v, "hal_step_abort", t_e)
    assert ab and ab[0]["t_us"] - t_e <= 100.0, ab
    ena = [e for e in m.edges(v, "ENA", t_e)]
    assert ena and ena[0]["t_us"] - t_e <= 1_000.0
    s = v.status()
    assert "ESTOP" in s["flags"] and "HOMED" not in s["flags"] and "VALID" not in s["flags"]
    v.advance(30)
    d = [x for x in v.data() if x["t_us"] >= v.tw.fw_t_us(int(t_e * 1000))]
    assert d and "ESTOP" in d[0]["flags"]


def test_estop_clear_rules(v):
    """TC-SAF-FW-006-01: ESTOP_CLEAR open -> 0xFFFF; closed 50 ms -> E_CAUSE_ACTIVE ms missing; >= 100 ms
    -> OK; then JOG -> E_STATE NOT_ENABLED; ENABLE with DRV_PWR off -> E_STATE DRV_UNPOWERED."""
    m.need(v, "MOTION", "DRV_SIGNALS")
    m.enable(v)
    v.tw.act("estop", open=True, k1_delay_ms=30)
    v.advance(100)
    r = v.cmd("ESTOP_CLEAR")
    assert (r["status"], r["detail"]) == lr.estop_clear(True, 0, 100)
    v.tw.act("estop", open=False, drv_power_follows=False)        # K1 stays open until RESET
    v.advance(50)
    r = v.cmd("ESTOP_CLEAR")
    exp = lr.estop_clear(False, 50, 100)
    assert r["status"] == exp[0] and abs(r["detail"] - exp[1]) <= 2, r
    v.advance(60)
    assert v.cmd("ESTOP_CLEAR")["status"] == "OK"
    r = m.jog(v, 1_000)
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("NOT_ENABLED")), r
    r = v.cmd("ENABLE")
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("DRV_UNPOWERED")), r


def test_limit_hit_latch_and_release(v):
    """TC-SAF-FW-013-01: END hit while jogging -> stop, LIMIT_SET, VALID 0; JOG toward -> E_STATE LIMIT;
    away -> runs; latch kept after 19 ms of release, cleared after >= 20 ms (D-33 h, no position rule)."""
    m.need(v, "MOTION", "HOMING")
    _jogging(v, +2_000)
    n0 = m.n_events(v)
    v.tw.act("limit", name="end", active=True)
    v.advance(5)
    assert m.events_since(v, n0, "LIMIT_SET") and m.state(v) in ("IDLE", "STOPPING")
    m.wait_until(v, lambda: m.state(v) == "IDLE", 100)
    r = m.jog(v, +1_000)
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("LIMIT")), r
    v.tw.act("limit", name="end", active=False)
    v.advance(19)
    assert "LIMIT_END" in v.status()["status"]
    assert lr.limit_latch_cleared(19, 20) is False
    v.advance(3)
    assert "LIMIT_END" not in v.status()["status"]
    v.tw.act("limit", name="end")                                  # back to the position model


def test_both_limits_wiring_fault(v):
    """TC-SAF-FW-014-01 (D-40 a): both active -> LIMIT_WIRING + motion refused; FAULT_CLEAR E_CAUSE_ACTIVE while
    BOTH are active, accepted once they are no longer both active; the input still active keeps acting as a
    normal limit latch (motion toward it refused, away accepted)."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=100_000)
    v.tw.act("limit", name="start", active=True)
    v.tw.act("limit", name="end", active=True)
    v.advance(5)
    assert "LIMIT_WIRING" in v.status()["faults"]
    r = m.jog(v, 1_000)
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("FAULT")), r
    r = v.cmd("FAULT_CLEAR")
    assert r["status"] == "E_CAUSE_ACTIVE" and r["detail"] == 1 << rc.FAULTS.index("LIMIT_WIRING")
    v.tw.act("limit", name="end", active=False)
    v.advance(30)
    assert v.ok("FAULT_CLEAR")["cleared"] == ["LIMIT_WIRING"]            # D-40 a: no longer both active
    r = m.jog(v, -1_000)                                                   # toward the still active START
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("LIMIT")), r
    assert m.jog(v, 1_000)["status"] == "OK"                               # away from it
    v.ok("STOP", {"mode": 0})
    v.tw.act("limit", name="start", active=False)
    v.tw.act("limit", name="start")
    v.tw.act("limit", name="end")


def test_drv_power_loss_while_jogging(v):
    """TC-SAF-FW-024-01: DRV_PWR off (E-stop closed) during a jog -> stop + ENA disabled + NOT_ENABLED +
    HOMED 0 <= 25 ms; DRIVER_POWER(0), STOPPED(DRV_POWER_LOST), DRIVER_DISABLED(4); ENABLE refused."""
    m.need(v, "MOTION", "HOMING", "DRV_SIGNALS")
    _jogging(v)
    n0 = m.n_events(v)
    t0 = v.tw.now_us
    v.tw.act("drv_power", on=False)
    ok = m.wait_until(v, lambda: m.state(v) == "NOT_ENABLED", lr.DRV_PWR_REACTION_MS, 0.5)
    assert ok, "no NOT_ENABLED within 25 ms"
    last_pul = max((e["t_us"] for e in m.edges(v, "PUL", t0)), default=t0)
    assert last_pul - t0 <= lr.DRV_PWR_REACTION_MS * 1000.0
    codes = [e["code"] for e in m.events_since(v, n0)]
    assert "DRIVER_POWER" in codes and "STOPPED" in codes and "DRIVER_DISABLED" in codes, codes
    stp = m.events_since(v, n0, "STOPPED")[0]
    assert stp["arg"] == rc.STOP_CAUSE["DRV_POWER_LOST"]
    assert "HOMED" not in v.status()["flags"]
    r = v.cmd("ENABLE")
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("DRV_UNPOWERED"))


def test_drv_power_short_toggles_ignored(v):
    """TC-FW-SW-005-01: toggles < 20 ms ignored, >= 21 ms accepted (oracle drv_pwr_filtered)."""
    m.need(v, "MOTION", "DRV_SIGNALS")
    m.enable(v)
    for dur, ignored in ((5, True), (19, True), (21, False)):
        n0 = m.n_events(v)
        v.tw.act("drv_power", on=False)
        v.advance(dur)
        v.tw.act("drv_power", on=True)
        v.advance(40)
        got = bool(m.events_since(v, n0, "DRIVER_POWER"))
        assert got is (not ignored), (dur, got)
        assert lr.toggle_ignored(dur) is ignored


def test_k1_weld_window(v):
    """TC-SAF-FW-025-01: E-stop open with DRV_PWR held -> K1_WELDED in (k1, k1 + 25] ms; FAULT_CLEAR
    refused while the cause persists, accepted after power off."""
    m.need(v, "MOTION", "DRV_SIGNALS")
    m.enable(v)
    n0 = m.n_events(v)
    t_e = v.tw.now_us
    v.tw.act("estop", open=True, drv_power_follows=False)
    lo, hi = lr.k1_weld_window(200)
    v.advance(lo - 50)
    assert not [e for e in m.events_since(v, n0, "FAULT_SET") if e["arg"] == rc.FAULTS.index("K1_WELDED")]
    fs = None
    while v.tw.now_us - t_e <= (hi + 2) * 1000:
        v.advance(0.5)
        k = [e for e in m.events_since(v, n0, "FAULT_SET") if e["arg"] == rc.FAULTS.index("K1_WELDED")]
        if k:
            fs = k[0]
            break
    assert fs is not None, "K1_WELDED not latched within k1 + 25 ms"
    t_fault = v.tw.now_us - t_e
    assert lo * 1000 < t_fault <= hi * 1000 + 3_000            # +3 ms EVENT wire latency allowance
    assert v.cmd("FAULT_CLEAR")["status"] == "E_CAUSE_ACTIVE"
    v.tw.act("drv_power", on=False)
    v.advance(40)
    assert "K1_WELDED" in v.ok("FAULT_CLEAR")["cleared"]


def test_load_limit_trip_and_regrow(v):
    """TC-SAF-FW-008-01 / -011-01: raw_max + 1 trips on that sample (raw_max does not) while jogging;
    FAULT_CLEAR always clears; re-trip only beyond regrow (oracle LoadLimit)."""
    m.need(v, "MOTION", "HOMING", "AFE")
    _jogging(v)
    mx = v.get("safety.load_raw_max")
    n0 = m.n_events(v)
    v.tw.act("afe", raw_script=[mx, mx, mx + 1, mx + 1])
    v.advance(60)
    fs = [e for e in m.events_since(v, n0, "FAULT_SET") if e["arg"] == rc.FAULTS.index("LOAD_LIMIT")]
    assert len(fs) == 1 and fs[0]["value"] == mx + 1, fs
    assert lr.first_trip([mx, mx, mx + 1], raw_max=mx) == 2
    m.wait_until(v, lambda: m.state(v) == "IDLE", 50)
    rg = v.get("safety.load_regrow_raw")
    v.tw.act("afe", raw_script=[mx + 1000] * 3 + [mx + 1000 + rg] * 3 + [mx + 1001 + rg] * 3)
    v.advance(13)
    assert v.ok("FAULT_CLEAR")["cleared"] == ["LOAD_LIMIT"]
    v.advance(80)
    assert "LOAD_LIMIT" in v.status()["faults"]                 # grew by more than regrow -> re-trip
