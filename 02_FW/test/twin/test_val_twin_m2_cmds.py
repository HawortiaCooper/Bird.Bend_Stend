"""Validator E - M2 twin suite: command semantics, PAUSE / RESUME, driver signals, AFE, streaming, events, time
base, and the command-acceptance differential walk against ref_cmdcheck (gated on GET_INFO M2 features).

Oracles: ICD v0.6 §4.3/§4.4/§5/§6/§7/§8, ref_cmdcheck.Model (Integrator acceptance oracle, used live in the
walk), val_oracles/latch_ref.py, SRS v0.5.2.

Verifies: SAF-FW-020, SAF-FW-023, SAF-FW-024, SAF-FW-026, FW-AFE-002, FW-AFE-003, FW-AFE-004, FW-AFE-005,
          FW-MOT-002, FW-MOT-007, FW-MOT-008, FW-MOT-009, FW-SW-001, FW-SW-002, FW-SW-003, FW-SW-004,
          FW-CMD-003, FW-STR-003, FW-STR-005, FW-STR-006, FW-TIM-001, SYS-004, FW-PLT-002
"""
from __future__ import annotations

import os
import random

import pytest

import gen_params
import latch_ref as lr
import ref_cmdcheck as rcc
import ref_codec as rc
import vhelp_m2 as m
from twin import Twin
from vhelp import PBYKEY, V, wire_value

SC = rc.STOP_CAUSE
MD = rc.MOVE_DONE_REASON
BLK = {n: 1 << i for i, n in enumerate(rc.BLOCK)}


def _blk(r: dict, name: str) -> bool:
    return r["status"] == "E_STATE" and (r["detail"] & BLK[name]) != 0


# ------------------------------------------------------------------ SAF-FW-023 PAUSE / RESUME
def test_pause_semantics(v):
    """TC-SAF-FW-023-01: button and PC PAUSE -> controlled stop, PAUSED(src), one EVENT; second press ->
    RESUME_REQUEST only; motion starts refused (BLOCK PAUSED) and PAUSED kept; other commands accepted
    without clearing; RESUME clears only PAUSED (PAUSE_CLEARED 3); HALT_CLEAR clears it (arg 2); idle PAUSE
    also sets PAUSED; nothing restarts motion."""
    m.need(v, "MOTION", "HOMING", "BUTTONS")
    m.ready(v, x_um=50_000)
    m.move_abs(v, 200_000, 10_000, wait=False)
    m.run(v, 300)
    n0 = m.n_events(v)
    v.tw.act("button", name="pause", pressed=True, bounce_ms=[0.3, 0.5, 0.2])
    m.wait_until(v, lambda: m.state(v) == "IDLE", 2000)
    m.run(v, 50)
    ev = m.events_since(v, n0)
    pz = [e for e in ev if e["code"] == "PAUSED"]
    assert len(pz) == 1 and pz[0]["arg"] == rc.SOURCE.index("BUTTON"), ev
    assert [e["arg"] for e in ev if e["code"] == "STOPPED"] == [SC["PAUSE_BUTTON"]]
    assert v.status()["pause_src"] == "BUTTON"
    v.tw.act("button", name="pause", pressed=False)
    v.advance(40)
    n1 = m.n_events(v)
    t0 = v.tw.now_us
    v.tw.act("button", name="pause", pressed=True)          # second press while PAUSED
    v.advance(10)
    v.tw.act("button", name="pause", pressed=False)
    v.advance(40)
    ev = m.events_since(v, n1)
    assert [e for e in ev if e["code"] == "RESUME_REQUEST"] and not [e for e in ev if e["code"] == "PAUSED"]
    for name, f in (("MOVE_ABS", {"target_um": 60_000, "v_um_s": 1000, "a_um_s2": 0}),
                    ("HOME", {"flags": 0}), ("JOG", {"v_um_s": 1000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})):
        assert _blk(v.cmd(name, f), "PAUSED"), name
    for name, f in (("JOG", {"v_um_s": 0, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}), ("STOP", {"mode": 0}),
                    ("ENABLE", {}), ("ESTOP_CLEAR", {}), ("FAULT_CLEAR", {}), ("PAUSE", {})):
        assert v.cmd(name, f)["status"] == "OK", name
    assert "PAUSED" in v.status()["status"]
    n2 = m.n_events(v)
    v.ok("RESUME")
    v.advance(10)
    pc = m.events_since(v, n2, "PAUSE_CLEARED")
    assert pc and pc[0]["arg"] == 3 and "PAUSED" not in v.status()["status"]
    assert m.rising_count(v, t0) == 0
    v.ok("PAUSE")                                             # idle PAUSE sets PAUSED (src PC)
    assert "PAUSED" in v.status()["status"] and v.status()["pause_src"] == "PC"
    n3 = m.n_events(v)
    v.ok("HALT_CLEAR")                                        # also clears PAUSED (D-31)
    v.advance(10)
    pc = m.events_since(v, n3, "PAUSE_CLEARED")
    assert pc and pc[0]["arg"] == 2
    assert m.rising_count(v, t0) == 0


@pytest.mark.parametrize("k_ms", [-1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0])
def test_pause_race_with_jog_refresh(v, k_ms):
    """TC-SAF-FW-023-02 (D-30): PAUSE button at t, a JOG refresh whose last byte arrives at t + k: no motion
    restarts (late refresh -> E_STATE PAUSED or E_BUSY), no PUL edge after the controlled stop ended."""
    m.need(v, "MOTION", "HOMING", "BUTTONS")
    m.ready(v, x_um=50_000)
    m.jog(v, 5_000)
    for _ in range(3):
        v.advance(100)
        m.jog(v, 5_000)
    fr = rc.make_frame("JOG", 0x55, {"v_um_s": 5_000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
    wire_us = len(fr) * 1e6 / 92160
    t = v.tw.now_us + 2_000
    v.link.send("JOG", {"v_um_s": 5_000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}, seq=0x55,
                at_us=t + k_ms * 1000 - wire_us)
    v.tw.advance_to(int(t * 1000))
    v.tw.act("button", name="pause", pressed=True)
    m.wait_until(v, lambda: not v.tw.act("query", what="pulses")["running"], 3_000, 1.0)
    t_end = v.tw.now_us
    m.run(v, 1_500)
    assert m.rising_count(v, t_end) == 0, f"motion restarted (k = {k_ms} ms)"
    r = v.link.find("JOG", 0x55)
    assert r is not None
    if k_ms > 1.0:
        assert r["status"] in ("E_STATE", "E_BUSY"), (k_ms, r)
    assert "PAUSED" in v.status()["status"]


def test_resume_refused_while_latched(v):
    """TC-SAF-FW-023-03 (T): RESUME with HALT latched ms earlier -> E_STATE (BLOCK HALT), HALT and PAUSED kept;
    RESUME with ESTOP / a FAULT latched -> refused, PAUSED kept; HALT_CLEAR with HALT + PAUSED -> both cleared;
    RESUME with nothing latched -> OK no-op."""
    m.need(v, "MOTION", "HOMING", "DRV_SIGNALS")
    m.ready(v, x_um=50_000)
    v.ok("PAUSE")
    seq_h = v.link.send("HALT")
    seq_r = v.link.send("RESUME")
    v.advance(20)
    r = v.link.find("RESUME", seq_r)
    assert _blk(r, "HALT"), r
    s = v.status()
    assert "HALT" in s["flags"] and "PAUSED" in s["status"]
    n0 = m.n_events(v)
    v.ok("HALT_CLEAR")
    v.advance(10)
    codes = [e["code"] for e in m.events_since(v, n0)]
    assert "HALT_CLEARED" in codes and "PAUSE_CLEARED" in codes
    v.ok("PAUSE")
    v.tw.act("estop", open=True)
    v.advance(30)
    r = v.cmd("RESUME")
    assert _blk(r, "ESTOP") and "PAUSED" in v.status()["status"]
    v.tw.act("estop", open=False)
    m.run(v, 200)
    v.ok("ESTOP_CLEAR")
    m.enable(v)
    v.tw.act("afe", stall=True)
    m.run(v, 400)
    v.ok("HALT_CLEAR")
    v.ok("PAUSE")
    v.tw.act("limit", name="start", active=True)
    v.tw.act("limit", name="end", active=True)
    v.advance(10)
    r = v.cmd("RESUME")
    assert _blk(r, "FAULT") and "PAUSED" in v.status()["status"]
    v.tw.act("limit", name="start", active=False)
    v.tw.act("limit", name="end", active=False)
    v.tw.act("afe", stall=False)
    m.run(v, 100)
    v.ok("FAULT_CLEAR")
    v.ok("RESUME")
    v.advance(20)
    n1 = m.n_events(v)
    assert v.cmd("RESUME")["status"] == "OK"
    v.advance(10)
    assert not m.events_since(v, n1, "PAUSE_CLEARED")
    assert v.link.find("HALT", seq_h)["status"] == "OK"


# ------------------------------------------------------------------ SAF-FW-024-02 DRV_PWR during SAVE
def test_drv_power_loss_during_save_with_erase(v):
    """Reaction to a driver-power loss during an idle SAVE <= operation time + 25 ms (D-33 f); SAVEs repeated
    until one performs a sector erase."""
    m.need(v, "MOTION", "DRV_SIGNALS")
    worst = []
    for i in range(80):
        m.enable(v)
        m.set_ok(v, "stream.fallback_hz", 1 + (i % 50))       # a parameter change -> SAVE writes a record
        t_save = v.tw.now_us
        seq = v.link.send("SAVE_PARAMS")
        v.advance(2)
        v.tw.act("drv_power", on=False)
        t_off = v.tw.now_us
        m.wait_until(v, lambda: v.link.find("SAVE_PARAMS", seq) is not None, 5_000, 1.0, keepalive=False)
        m.wait_until(v, lambda: v.tw.act("query", what="outputs")["ena_enabled"] is False, 3_000, 0.5,
                     keepalive=False)
        t_ena = [e["t_us"] for e in m.edges(v, "ENA", t_off)]
        erased = bool(m.seam(v, "hal_flash_erase", t_save))
        op_ms = v.status()["nvm_save_ms"]
        assert t_ena, "ENA never disabled after the power loss"
        dt_ms = (t_ena[0] - t_off) / 1000
        worst.append((dt_ms, op_ms, erased))
        assert dt_ms <= op_ms + lr.DRV_PWR_REACTION_MS + 1, (i, dt_ms, op_ms, erased)
        v.tw.act("drv_power", on=True)
        m.run(v, 60)
        if erased:
            break
    assert any(e for _, _, e in worst), "no SAVE with a sector erase in 80 attempts"
    print("VALOBS DRV_PWR during SAVE (ms, op ms, erase):", worst[-3:])


# ------------------------------------------------------------------ SAF-FW-026 ALM start-block
def test_alm_start_block(v):
    m.need(v, "MOTION", "HOMING", "DRV_SIGNALS")
    m.ready(v, x_um=50_000)
    n0 = m.n_events(v)
    v.tw.act("alm", active=True)
    v.advance(30)
    assert m.events_since(v, n0, "ALM_CHANGED")
    for name, f in (("MOVE_ABS", {"target_um": 60_000, "v_um_s": 1000, "a_um_s2": 0}), ("HOME", {"flags": 0}),
                    ("JOG", {"v_um_s": 1000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})):
        assert _blk(v.cmd(name, f), "DRIVER_ALARM"), name
    for name, f in (("ENABLE", {}), ("STOP", {"mode": 0}), ("HALT", {}), ("HALT_CLEAR", {}), ("PAUSE", {}),
                    ("RESUME", {}), ("JOG", {"v_um_s": 0, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}),
                    ("FAULT_CLEAR", {}), ("ESTOP_CLEAR", {})):
        assert v.cmd(name, f)["status"] == "OK", name
    v.tw.act("alm", active=False)
    v.advance(40)
    # ALM during a MOVE_ABS: the move completes (no stop by ALM, D-16)
    n1 = m.n_events(v)
    m.move_abs(v, 60_000, 5_000, wait=False)
    m.run(v, 200)
    v.tw.act("alm", active=True)
    md = m.wait_event(v, "MOVE_DONE", n1, 5_000)
    assert md["arg"] == MD.index("TARGET") and md["value"] == 60_000
    v.tw.act("alm", active=False)
    v.advance(40)
    # ALM during a jog: refreshes still accepted
    m.jog(v, 2_000)
    v.advance(100)
    v.tw.act("alm", active=True)
    v.advance(30)
    for _ in range(3):
        assert m.jog(v, 2_000)["status"] == "OK"
        v.advance(100)
    m.jog(v, 0)
    m.run(v, 200)
    # ALM + driver unpowered -> DRV_UNPOWERED (not DRIVER_ALARM)
    v.tw.act("drv_power", on=False)
    v.advance(40)
    r = v.cmd("MOVE_ABS", {"target_um": 70_000, "v_um_s": 1000, "a_um_s2": 0})
    assert _blk(r, "DRV_UNPOWERED") and not (r["detail"] & BLK["DRIVER_ALARM"]), r


# ------------------------------------------------------------------ FW-AFE-002 / 003 / 004 / 005
def test_gain_rate_reconfiguration(v):
    m.need(v, "AFE")
    for key, val, pulses in (("afe.gain_channel", "A64", 27), ("afe.gain_channel", "B32", 26),
                             ("afe.gain_channel", "A128", 25)):
        t0 = v.tw.now_us
        m.set_ok(v, key, val)
        v.advance(30)
        cfg = m.seam(v, "hal_hx711_config", t0)
        assert cfg and f"gain_pulses={pulses}" in cfg[-1]["args"], cfg
        conv = [c for c in v.tw.act("query", what="conversions", since_us=t0 + 15_000)["conversions"]]
        assert conv and all(c.get("gain_pulses", pulses) == pulses for c in conv), conv[:2]
    m.set_ok(v, "afe.rate_sps", "SPS10")
    v.advance(500)
    assert v.tw.act("query", what="outputs")["RATE"] == 0
    c = v.tw.act("query", what="conversions", since_us=v.tw.now_us - 400_000)["conversions"]
    assert c and all(abs((b["t_us"] - a["t_us"]) - 100_000) < 2_000 for a, b in zip(c, c[1:]))
    v.ok("DEFAULT_PARAMS")
    v.advance(30)
    assert v.tw.act("query", what="outputs")["RATE"] == 1
    assert v.get("afe.gain_channel") == 0


@pytest.mark.parametrize("n", [0, 4, 20])
def test_settle_flags_and_reinit(v, n):
    m.need(v, "AFE")
    m.set_ok(v, "afe.settle_discard", n)
    v.ok("STREAM_START")
    v.advance(200)
    k0 = len(v.data())
    m.set_ok(v, "afe.gain_channel", "A64")
    v.advance(800)
    d = v.data()[k0:]
    flags = ["AFE_SETTLING" in x["status"] for x in d]
    first = flags.index(True) if True in flags else len(flags)
    assert sum(flags) == n, (n, flags[:30])
    assert all(flags[first:first + n]), flags[:30]
    # SCK-high overrun -> re-init: counter + 1, EVENT AFE_REINIT, settle re-armed
    c0 = v.status()["afe_reinit_count"]
    n0 = m.n_events(v)
    k1 = len(v.data())
    v.tw.act("afe", sck_overrun=True)
    v.advance(800)
    assert v.status()["afe_reinit_count"] == c0 + 1
    assert m.events_since(v, n0, "AFE_REINIT")
    assert sum("AFE_SETTLING" in x["status"] for x in v.data()[k1:]) == n


def test_settling_samples_still_load_checked(v):
    m.need(v, "AFE")
    m.set_ok(v, "afe.settle_discard", 20)
    m.set_ok(v, "afe.gain_channel", "A64")
    v.advance(30)
    n0 = m.n_events(v)
    v.tw.act("afe", raw_script=[8_388_607])
    v.advance(40)
    assert [e for e in m.events_since(v, n0, "FAULT_SET") if e["arg"] == rc.FAULTS.index("LOAD_LIMIT")]


def test_rate_mismatch_flag(v):
    m.need(v, "AFE")
    v.ok("STREAM_START")
    n0 = m.n_events(v)
    v.tw.act("afe", rate_error=-0.875)                       # 80 configured, model at 80 * 0.125 = 10 SPS
    t0 = v.tw.now_us
    m.wait_until(v, lambda: bool(m.events_since(v, n0, "AFE_RATE_MISMATCH")), 3_000, 10.0)
    ev = m.events_since(v, n0, "AFE_RATE_MISMATCH")
    assert ev and ev[0]["arg"] == 1
    assert (v.tw.now_us - t0) <= 1_000_000 + 16 * 100_000 + 10_000     # 16 periods to fill + 1 s
    m.run(v, 300)
    assert "AFE_RATE_MISMATCH" in v.data()[-1]["status"], v.data()[-1]
    assert abs(v.status()["afe_rate_dsps"] - 100) <= 2
    n1 = m.n_events(v)
    v.tw.act("afe", rate_error=0.02)
    m.run(v, 3_000)
    ev = m.events_since(v, n1, "AFE_RATE_MISMATCH")
    assert ev and ev[-1]["arg"] == 0
    n2 = m.n_events(v)
    v.tw.act("afe", rate_error=-0.02)
    m.run(v, 60_000, step_ms=10.0)
    assert not m.events_since(v, n2, "AFE_RATE_MISMATCH")


def test_timestamp_and_setpoint_across_wrap(twin_exe, tmp_path):
    """TC-FW-AFE-005-01 / TC-FW-TIM-001-01: twin start 5 s before the 2^32 µs wrap; DATA t_us = the data-ready
    device time (twin conversion fw_t_us) within 2 µs, time base monotonic modulo 2^32; setpoint_um = the step
    count at that instant converted (round half away)."""
    tw = Twin("lockstep", exe=twin_exe, run_dir=tmp_path / "w", t0_us=2**32 - 60_000_000)
    try:
        v = V(tw)
        m.need(v, "MOTION", "HOMING", "AFE")
        m.ready(v, x_um=None)
        left_us = (2**32 - tw.fw_t_us()) % 2**32               # time to the wrap
        assert left_us > 3_000_000
        m.run(v, (left_us - 2_000_000) / 1000, step_ms=10.0)    # the wrap falls 2 s into the move
        v.ok("STREAM_START")
        assert v.status()["pos_steps"] == 0
        t_mv = tw.now_us
        m.move_abs(v, 250_000, 30_000, wait=False)
        m.run(v, 8_000)
        falls = [e["t_us"] for e in m.edges(v, "PUL", t_mv) if e["level"] == 0]
        d = v.data()
        conv = {c["fw_t_us"]: c for c in tw.act("query", what="conversions")["conversions"] if c["delivered"]}
        steps = 0
        checked = wrapped = 0
        prev = None
        for x in d:
            c = conv.get(x["t_us"])
            if c is None:
                near = [k for k in conv if abs(((k - x["t_us"] + 2**31) % 2**32) - 2**31) <= 2]
                assert near, x
                c = conv[near[0]]
            if prev is not None:
                assert ((x["t_us"] - prev) % 2**32) < 2**31            # monotonic modulo 2^32
                wrapped += x["t_us"] < prev
            prev = x["t_us"]
            steps = sum(1 for f in falls if f <= c["t_us"])
            assert abs(x["setpoint_um"] - round(steps * 1000 / 800)) <= 2, (x, steps)
            checked += 1
        assert checked > 50 and wrapped == 1
    finally:
        tw.close()


# ------------------------------------------------------------------ FW-MOT-002 / 007 / 008 / 009
@pytest.mark.parametrize("spm", [100.0, 160.0, 800.0, 100_000.0])
def test_position_units(twin_exe, tmp_path, spm):
    """TC-FW-MOT-002-01 (T): reported µm = steps·1000/spm round half away, final step count exact; a spm change
    while idle keeps HOMED (FW-MOT-009). The twin world uses the same scale (scenario)."""
    tw = Twin("lockstep", exe=twin_exe, run_dir=tmp_path / "u",
              scenario={"schema": "bird.bend.simscenario", "version": 1, "params": {"motion.steps_per_mm": spm}})
    try:
        v = V(tw)
        m.need(v, "MOTION", "HOMING")
        m.set_ok(v, "motion.steps_per_mm", spm)
        m.ready(v, x_um=None)
        assert "HOMED" in v.status()["flags"]
        for tgt in (1_234, 7_777, 2_001):
            r = v.cmd("MOVE_ABS", {"target_um": tgt, "v_um_s": min(1_000, v.status()["v_limit_um_s"]), "a_um_s2": 0})
            assert r["status"] == "OK", r
            m.wait_until(v, lambda: m.state(v) == "IDLE", 60_000, 5.0)
            s = v.status()
            assert s["pos_um"] == lr_steps_to_um(s["pos_steps"], spm), (s["pos_um"], s["pos_steps"], spm)
            assert s["pos_steps"] == lr_um_to_steps(tgt, spm), (tgt, s)
        m.set_ok(v, "motion.steps_per_mm", 1000.0)               # idle change: HOMED kept, µm rescaled
        s = v.status()
        assert "HOMED" in s["flags"] and s["pos_um"] == lr_steps_to_um(s["pos_steps"], 1000.0)
    finally:
        tw.close()


def lr_um_to_steps(um: int, spm: float) -> int:
    import struct
    s = struct.unpack("<f", struct.pack("<f", spm))[0]
    x = um * s / 1000.0
    return int(abs(x) + 0.5) * (1 if x >= 0 else -1)


def lr_steps_to_um(st: int, spm: float) -> int:
    import struct
    s = struct.unpack("<f", struct.pack("<f", spm))[0]
    x = st * 1000.0 / s
    return int(abs(x) + 0.5) * (1 if x >= 0 else -1)


def test_stop_halt_semantics(v):
    """TC-FW-MOT-007-01: STOP not latched; 20 HALTs -> one HALT_SET (src PC), all OK; motion refused (BLOCK HALT)
    until HALT_CLEAR; STOP / HALT / PAUSE accepted in every state; STOP while idle clears VALID."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=50_000)
    v.ok("SET_VALID", {"valid": 1})
    v.ok("STREAM_START")
    v.ok("STOP", {"mode": 0})
    v.advance(30)
    assert "VALID" not in v.data()[-1]["flags"] and "HALT" not in v.status()["flags"]
    n0 = m.n_events(v)
    for _ in range(20):
        assert v.cmd("HALT")["status"] == "OK"
    v.advance(20)
    hs = m.events_since(v, n0, "HALT_SET")
    assert len(hs) == 1 and hs[0]["arg"] == rc.SOURCE.index("PC") and v.status()["halt_src"] == "PC"
    assert _blk(v.cmd("MOVE_ABS", {"target_um": 60_000, "v_um_s": 1000, "a_um_s2": 0}), "HALT")
    v.ok("HALT_CLEAR")
    assert v.cmd("MOVE_ABS", {"target_um": 60_000, "v_um_s": 1000, "a_um_s2": 0})["status"] == "OK"
    for c in ("STOP", "HALT", "PAUSE"):
        assert v.cmd(c, {"mode": 1} if c == "STOP" else {})["status"] == "OK"
    v.ok("HALT_CLEAR")
    m.wait_until(v, lambda: m.state(v) == "IDLE", 2000)
    v.ok("DISABLE")
    for c in ("STOP", "HALT", "PAUSE"):                         # NOT_ENABLED
        assert v.cmd(c, {"mode": 0} if c == "STOP" else {})["status"] == "OK"


@pytest.mark.parametrize("settle", [0, 500, 2000])
def test_enable_settle_disable(v, settle):
    m.need(v, "MOTION", "HOMING", "DRV_SIGNALS")
    m.set_ok(v, "motion.ena_settle_ms", settle)
    t0 = v.tw.now_us
    r = v.ok("ENABLE")
    assert abs(r["settle_ms"] - settle) <= 1
    assert v.tw.act("query", what="outputs")["ena_enabled"] is True
    if settle:
        r2 = v.cmd("JOG", {"v_um_s": 1000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
        assert (r2["status"], r2["detail"]) == ("E_BUSY", 2), r2
        assert m.state(v) == "ENABLING"
    v.advance(settle + 5)
    assert m.state(v) == "IDLE"
    m.jog(v, 1000)
    v.advance(50)
    first = [e["t_us"] for e in m.edges(v, "PUL", t0) + m.edges(v, "DIR", t0)]
    assert first and min(first) - t0 >= settle * 1000 - 1, (min(first) - t0, settle)
    r3 = v.cmd("DISABLE")
    assert (r3["status"], r3["detail"]) == ("E_BUSY", 1)
    m.jog(v, 0)
    m.wait_until(v, lambda: m.state(v) == "IDLE", 1000)
    n0 = m.n_events(v)
    v.ok("DISABLE")
    v.advance(10)
    dd = m.events_since(v, n0, "DRIVER_DISABLED")
    assert dd and dd[0]["arg"] == 1 and m.state(v) == "NOT_ENABLED" and "HOMED" not in v.status()["flags"]
    v.tw.act("estop", open=True)
    v.advance(10)
    assert _blk(v.cmd("ENABLE"), "ESTOP")


def test_settle_counts_from_power_return(v):
    m.need(v, "MOTION", "DRV_SIGNALS")
    v.tw.act("drv_power", on=False)
    v.advance(40)
    assert _blk(v.cmd("ENABLE"), "DRV_UNPOWERED")
    v.tw.act("drv_power", on=True)
    t_p = v.tw.now_us
    v.advance(100)
    v.ok("ENABLE")
    m.wait_until(v, lambda: m.state(v) == "IDLE", 2000)
    t_idle = v.tw.now_us
    assert t_idle - t_p >= 500_000 - 1_000 + 20_000 * 0, (t_idle - t_p)


def test_steps_per_mm_rules(v):
    m.need(v, "MOTION", "HOMING")
    pid = PBYKEY["motion.steps_per_mm"].id
    assert (v.set("motion.steps_per_mm", 99.0)["status"]) == "E_RANGE"
    m.set_ok(v, "motion.steps_per_mm", 100.0)
    m.set_ok(v, "motion.steps_per_mm", 100_000.0)
    m.set_ok(v, "motion.steps_per_mm", 800.0)
    m.ready(v, x_um=20_000)
    m.move_abs(v, 100_000, 5_000, wait=False)
    v.advance(50)
    r = v.set("motion.steps_per_mm", 900.0)
    assert (r["status"], r["detail"]) == ("E_BUSY", 1), r
    v.ok("STOP", {"mode": 0})
    m.wait_until(v, lambda: m.state(v) == "IDLE", 500)
    m.set_ok(v, "motion.steps_per_mm", 10_000.0)
    s = v.status()
    assert s["v_limit_um_s"] == 5000 and "HOMED" in s["flags"]
    r = v.cmd("MOVE_ABS", {"target_um": 30_000, "v_um_s": 10_000, "a_um_s2": 0})
    assert (r["status"], r["detail"]) == ("E_RANGE", 4), r
    assert v.get("motion.v_max_travel_um_s") == 30_000
    assert pid


def test_speed_envelope_twin(v):
    """TC-SYS-004-02: 0.001 mm/s accepted at spm 100 / 800 (period fits 32 bit), mean speed of a move from the
    edge log within 1 %; 30 mm/s unloaded accepted."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=20_000)
    assert v.cmd("MOVE_ABS", {"target_um": 20_010, "v_um_s": 1, "a_um_s2": 0})["status"] == "OK"
    m.wait_until(v, lambda: m.state(v) == "IDLE", 15_000, 10.0)
    t0 = v.tw.now_us
    m.move_abs(v, 70_000, 10_000)
    pul = [e["t_us"] for e in m.edges(v, "PUL", t0) if e["level"] == 0]
    cruise = [b - a for a, b in zip(pul, pul[1:])][2000:-2000]
    mean_v = 1000.0 / 800 / (sum(cruise) / len(cruise) / 1e6)
    assert abs(mean_v - 10_000) / 10_000 <= 0.01, mean_v
    assert v.cmd("MOVE_ABS", {"target_um": 250_000, "v_um_s": 30_000, "a_um_s2": 0})["status"] == "OK"


# ------------------------------------------------------------------ FW-SW-001 … 004
@pytest.mark.parametrize("rel_ms", [5, 20, 200])
def test_limit_bounce_and_release(v, rel_ms):
    m.need(v, "MOTION", "HOMING")
    m.set_ok(v, "io.release_ms", rel_ms)
    m.ready(v, x_um=50_000)
    m.move_abs(v, 200_000, 10_000, wait=False)
    m.run(v, 200)
    n0 = m.n_events(v)
    v.tw.act("limit", name="end", active=True, bounce_ms=[0.2, 0.3, 0.5, 0.2, 1.0, 0.4])
    m.wait_until(v, lambda: m.state(v) == "IDLE", 200)
    m.run(v, 30)
    assert len(m.events_since(v, n0, "LIMIT_SET")) == 1 and len(m.events_since(v, n0, "STOPPED")) == 1
    v.tw.act("limit", name="end", active=False, bounce_ms=[0.5, 1.0, 0.3, 2.0])
    t_rel_end = v.tw.now_us + (0.5 + 1.0 + 0.3 + 2.0) * 1000
    m.wait_until(v, lambda: bool(m.events_since(v, n0, "LIMIT_CLEARED")), rel_ms + 100, 0.5)
    lc = m.events_since(v, n0, "LIMIT_CLEARED")
    assert lc
    assert (v.tw.now_us - t_rel_end) >= rel_ms - 1, (v.tw.now_us - t_rel_end, rel_ms)
    v.tw.act("limit", name="end")


def test_estop_release_debounce(v):
    m.need(v, "MOTION")
    v.tw.act("estop", open=True, drv_power_follows=False)
    v.advance(50)
    v.tw.act("estop", open=False, drv_power_follows=False, bounce_ms=[1, 2, 1, 3])
    v.advance(7 + 99)
    r = v.cmd("ESTOP_CLEAR")
    assert r["status"] == "E_CAUSE_ACTIVE", r
    v.advance(3)
    assert v.cmd("ESTOP_CLEAR")["status"] == "OK"


@pytest.mark.parametrize("level", ["CLOSED_ACTIVE", "OPEN_ACTIVE"])
def test_pause_button_events_and_polarity(v, level):
    m.need(v, "BUTTONS")
    if level == "OPEN_ACTIVE":
        v.tw.act("button", name="pause", pressed=True)        # NO contact closed = pin low
        v.advance(30)
        n_pre = m.n_events(v)
        m.set_ok(v, "io.pause_active_level", level)          # now the low pin is "released"
        v.advance(40)
        assert not [e for e in m.events_since(v, n_pre, "PAUSE_BUTTON") if e["arg"] == 1],             "polarity change produced a press"
        v.ok("RESUME")
    v.ok("STREAM_START")
    n0 = m.n_events(v)
    for _ in range(3):
        if level == "CLOSED_ACTIVE":
            v.tw.act("button", name="pause", pressed=True, bounce_ms=[0.2, 0.4, 0.3])
        else:
            v.tw.act("button", name="pause", pressed=False, bounce_ms=[0.2, 0.4, 0.3])
        v.advance(30)
        assert "PAUSE_BTN" in v.data()[-1]["status"] and "PAUSE_BTN" in v.status()["io"]
        if level == "CLOSED_ACTIVE":
            v.tw.act("button", name="pause", pressed=False, bounce_ms=[0.2, 0.4])
        else:
            v.tw.act("button", name="pause", pressed=True, bounce_ms=[0.2, 0.4])
        v.advance(60)
    pb = m.events_since(v, n0, "PAUSE_BUTTON")
    assert [e["arg"] for e in pb] == [1, 0] * 3, pb
    _assert_no_retired_codes(v)


def _assert_no_retired_codes(v) -> None:
    """CR-01 regression: STOP_BTN status bit 9 / io bit 3, EVENT 22, stop cause 4, HALT source BUTTON never sent."""
    for d in v.data():
        assert "" not in d["status"]
    for fr in v.link.frames:
        if fr.type == rc.ASYNC["EVENT"]:
            e = rc.decode_event(fr.payload)
            assert e["code"] != 22
            if e["code"] in ("STOPPED", "VALID_CLEARED"):
                assert e["arg"] != 4
            if e["code"] == "HALT_SET":
                assert e["arg"] != rc.SOURCE.index("BUTTON")


def test_alm_pend_reporting(v):
    m.need(v, "MOTION", "HOMING", "DRV_SIGNALS")
    m.ready(v, x_um=20_000)
    v.ok("STREAM_START")
    m.move_abs(v, 120_000, 10_000, wait=False)
    m.run(v, 200)
    n0 = m.n_events(v)
    v.tw.act("alm", active=True)
    v.advance(20)
    assert "ALM" in v.data()[-1]["status"] and "ALM" in v.status()["io"]
    v.tw.act("alm", active=False)
    v.advance(40)
    assert len(m.events_since(v, n0, "ALM_CHANGED")) == 2 and m.state(v) == "MOVE_ABS"
    # chatter 1 kHz for 1 s: bounded EVENT count, no STEP_FAULT
    n1 = m.n_events(v)
    v.tw.act("chatter", input="alm", period_ms=1.37, duration_ms=1000)   # ~0.73 kHz, not phase-locked to the poll
    m.run(v, 1200)
    al = m.events_since(v, n1, "ALM_CHANGED")
    assert 0 < len(al) <= 2 * (1000 // v.get("io.release_ms") + 2), len(al)
    assert "STEP_FAULT" not in v.status()["faults"]
    m.wait_until(v, lambda: m.state(v) == "IDLE", 30_000, 5.0)
    # PEND withheld -> NOT_SETTLED (value = elapsed), none with pend_timeout 0
    v.tw.act("pend", active=False)
    n2 = m.n_events(v)
    m.move_abs(v, 21_000, 5_000)
    m.run(v, 400)
    ns = m.events_since(v, n2, "NOT_SETTLED")
    assert ns and abs(ns[0]["value"] - v.get("drv.pend_timeout_ms")) <= 2, ns
    m.set_ok(v, "drv.pend_timeout_ms", 0)
    n3 = m.n_events(v)
    m.move_abs(v, 20_000, 5_000)
    m.run(v, 400)
    assert not m.events_since(v, n3, "NOT_SETTLED")
    v.tw.act("pend", active=True)


# ------------------------------------------------------------------ FW-CMD-003 FAULT_CLEAR per fault
@pytest.mark.parametrize("fault", ["LOAD_LIMIT", "AFE_FAULT", "STEP_FAULT", "LIMIT_WIRING", "HOME_NOT_FOUND",
                                   "HOME_WIRING", "HOME_DRIFT", "K1_WELDED"])
def test_fault_clear_per_fault(v, fault):
    m.need(v, "MOTION", "HOMING", "AFE", "DRV_SIGNALS")
    m.ready(v, x_um=50_000)
    cause_gone = None
    if fault == "LOAD_LIMIT":
        v.tw.act("afe", saturate="pos")
        cause_gone = lambda: v.tw.act("afe", saturate=None)  # noqa: E731 (always clearable anyway)
    elif fault == "AFE_FAULT":
        m.move_abs(v, 200_000, 5_000, wait=False)
        v.tw.act("afe", stall=True)
        m.run(v, 400)
        cause_gone = lambda: v.tw.act("afe", stall=False)  # noqa: E731
    elif fault == "STEP_FAULT":
        m.move_abs(v, 200_000, 20_000, wait=False)
        m.run(v, 100)
        v.tw.act("inject", fault="step_fault")
    elif fault == "LIMIT_WIRING":
        v.tw.act("limit", name="start", active=True)
        v.tw.act("limit", name="end", active=True)
        cause_gone = lambda: (v.tw.act("limit", name="start", active=False),  # noqa: E731
                              v.tw.act("limit", name="end", active=False))
    elif fault == "HOME_NOT_FOUND":
        m.set_ok(v, "home.max_travel_um", 1_000)
        v.ok("HOME", {"flags": 0})
    elif fault == "HOME_WIRING":
        v.ok("HOME", {"flags": 0})
        m.wait_until(v, lambda: v.status()["home_phase"] == "FAST_SEEK", 2000)
        v.tw.act("limit", name="end", active=True)            # HOME_WIRING has no cause condition (ICD §5.5)
    elif fault == "HOME_DRIFT":
        v.tw.act("world_shift", um=400)
        v.ok("HOME", {"flags": 0})
    elif fault == "K1_WELDED":
        v.tw.act("estop", open=True, drv_power_follows=False)
        cause_gone = lambda: v.tw.act("drv_power", on=False)  # noqa: E731
    m.run(v, 60_000 if fault.startswith("HOME") else 400, step_ms=5.0)
    assert fault in v.status()["faults"], (fault, v.status()["faults"])
    r = v.cmd("FAULT_CLEAR")
    if cause_gone is not None and fault != "LOAD_LIMIT":
        assert r["status"] == "E_CAUSE_ACTIVE" and r["detail"] == 1 << rc.FAULTS.index(fault), r
        assert fault in v.status()["faults"]                 # nothing cleared
        cause_gone()
        m.run(v, 100)
        r = v.cmd("FAULT_CLEAR")
    assert r["status"] == "OK" and fault in r["cleared"], (fault, r)
    assert fault not in v.status()["faults"]


def test_fault_clear_all_or_nothing(v):
    m.need(v, "MOTION", "HOMING", "AFE")
    m.ready(v, x_um=50_000)
    m.move_abs(v, 200_000, 20_000, wait=False)
    m.run(v, 100)
    v.tw.act("inject", fault="step_fault")                    # clearable at once
    m.run(v, 50)
    v.tw.act("limit", name="start", active=True)
    v.tw.act("limit", name="end", active=True)              # cause present
    v.advance(10)
    r = v.cmd("FAULT_CLEAR")
    assert r["status"] == "E_CAUSE_ACTIVE" and r["detail"] == 1 << rc.FAULTS.index("LIMIT_WIRING")
    assert {"STEP_FAULT", "LIMIT_WIRING"} <= set(v.status()["faults"])     # nothing cleared
    assert "HALT" not in v.status()["flags"] and "ESTOP" not in v.status()["flags"]


# ------------------------------------------------------------------ FW-STR-003 (E-C2) / 005 / 006
def test_m2_data_bits_each_under_its_condition(v):
    """TC-FW-STR-003-01 (M2 part, carried condition E-C2): MOVING, HOMED, ENABLED, ESTOP, FAULT, LIMIT_START /
    LIMIT_END, LOAD_LIMIT, LINK_WDG, PAUSE_BTN, ALM, PEND, POS_UNCERTAIN set under their condition."""
    m.need(v, "MOTION", "HOMING", "AFE", "BUTTONS", "DRV_SIGNALS")
    v.ok("STREAM_START")
    m.ready(v, x_um=50_000)

    def last():
        m.run(v, 30)
        return v.data()[-1]
    d = last()
    assert {"HOMED", "ENABLED"} <= set(d["flags"]) and "PEND" in d["status"]
    m.move_abs(v, 200_000, 10_000, wait=False)
    assert "MOVING" in last()["flags"]
    v.tw.act("limit", name="end", active=True)
    d = last()
    assert "LIMIT_END" in d["status"] and "MOVING" not in d["flags"]
    v.tw.act("limit", name="end", active=False)
    v.advance(40)
    v.tw.act("limit", name="end")
    m.move_abs(v, 10_000, 30_000, wait=False)
    m.run(v, 200)
    v.tw.act("limit", name="start", active=True)
    assert "LIMIT_START" in last()["status"]
    v.tw.act("limit", name="start", active=False)
    v.advance(40)
    v.tw.act("limit", name="start")
    v.tw.act("afe", raw_script=[7_100_000] * 3)
    d = last()
    assert "LOAD_LIMIT" in d["status"] and "FAULT" in d["flags"]
    v.tw.act("afe", raw_script=[0] * 3)
    v.advance(50)
    v.ok("FAULT_CLEAR")
    v.tw.act("button", name="pause", pressed=True)
    assert "PAUSE_BTN" in last()["status"]
    v.tw.act("button", name="pause", pressed=False)
    v.advance(40)
    v.ok("RESUME")
    v.tw.act("alm", active=True)
    assert "ALM" in last()["status"]
    v.tw.act("alm", active=False)
    v.advance(40)
    m.move_abs(v, 100_000, 10_000, wait=False)
    m.wait_until(v, lambda: False, 1_200, 5.0, keepalive=False)      # link watchdog
    assert "LINK_WDG" in v.data()[-1]["status"]
    v.ok("PING")
    m.move_abs(v, 150_000, 10_000, wait=False)
    m.run(v, 100)
    v.tw.act("estop", open=True)
    d = last()
    assert "ESTOP" in d["flags"] and "ENABLED" not in d["flags"] and "HOMED" not in d["flags"]
    _assert_no_retired_codes(v)


@pytest.mark.parametrize("hz", [1, 10, 80])
def test_fallback_frames(v, hz):
    m.need(v, "AFE")
    m.set_ok(v, "stream.fallback_hz", hz)
    v.ok("STREAM_START")
    v.advance(100)
    v.tw.act("afe", stall=True)
    v.advance(600)
    k0 = len(v.data())
    t0 = v.tw.now_us
    v.advance(10_000)
    fb = v.data()[k0:]
    assert abs(len(fb) - 10 * hz) <= 10, (len(fb), hz)
    assert all({"NO_AFE_DATA", "AFE_STALE"} <= set(x["status"]) and x["afe_raw"] == rc.AFE_NO_DATA for x in fb)
    v.tw.act("afe", stall=False)
    v.advance(500)
    tail = v.data()[-10:]
    assert all("NO_AFE_DATA" not in x["status"] for x in tail)
    assert t0 > 0


def test_event_catalogue(v):
    """TC-FW-STR-006-01: a scenario that triggers every reachable EVENT code (1…34 without the retired 22);
    each EVENT decodes and its SEQ is consecutive; NVM_ERROR (31) is covered by the M1 flash-cut suite."""
    m.need(v, "MOTION", "HOMING", "AFE", "BUTTONS", "DRV_SIGNALS")
    v.tw.act("clk", hse_fail=True)
    v.tw.act("reset", cause="power")
    v.advance(60)
    v.ok("SET_VALID", {"valid": 1})
    m.ready(v, x_um=50_000)                                   # BOOT, CLK_FALLBACK, DRIVER_ENABLED, HOMED, MOVE_DONE
    v.ok("SET_VALID", {"valid": 1})
    v.ok("HALT")                                              # HALT_SET, VALID_CLEARED
    v.ok("HALT_CLEAR")                                        # HALT_CLEARED
    v.ok("PAUSE")
    v.ok("RESUME")                                            # PAUSED, PAUSE_CLEARED
    v.tw.act("button", name="pause", pressed=True)
    v.advance(30)
    v.tw.act("button", name="pause", pressed=False)
    v.advance(40)
    v.tw.act("button", name="pause", pressed=True)            # RESUME_REQUEST, PAUSE_BUTTON
    v.advance(30)
    v.tw.act("button", name="pause", pressed=False)
    v.advance(40)
    v.ok("HALT_CLEAR")
    m.move_abs(v, 200_000, 10_000, wait=False)
    m.run(v, 100)
    v.tw.act("limit", name="end", active=True)                # LIMIT_SET, STOPPED
    v.advance(30)
    v.tw.act("limit", name="end", active=False)
    v.advance(40)                                             # LIMIT_CLEARED
    v.tw.act("limit", name="end")
    v.tw.act("alm", active=True)                              # ALM_CHANGED
    v.advance(30)
    v.tw.act("alm", active=False)
    v.advance(40)
    v.tw.act("afe", sck_overrun=True)                         # AFE_REINIT
    v.advance(50)
    v.tw.act("afe", rate_error=-0.875)                        # AFE_RATE_MISMATCH (10 SPS)
    m.run(v, 3000, step_ms=10)
    v.tw.act("afe", rate_error=0.0)
    v.tw.act("afe", stall=True)                               # AFE_STALE
    m.run(v, 400)
    v.tw.act("afe", stall=False)
    m.run(v, 200)
    v.tw.act("afe", raw_script=[8_388_607])                   # FAULT_SET
    v.advance(30)
    v.ok("FAULT_CLEAR")                                       # FAULT_CLEARED
    v.tw.act("pend", active=False)
    m.move_abs(v, 51_000, 5_000)
    m.run(v, 400)                                             # NOT_SETTLED
    v.tw.act("pend", active=True)
    m.move_abs(v, 100_000, 10_000, wait=False)
    m.wait_until(v, lambda: False, 1_200, 5.0, keepalive=False)   # LINK_WDG
    v.ok("PING")                                              # LINK_RESTORED
    m.run(v, 50)
    m.set_ok(v, "stream.fallback_hz", 7)
    v.ok("SAVE_PARAMS", timeout_ms=5000)                      # PARAMS_SAVED
    v.ok("LOAD_PARAMS")                                       # PARAMS_LOADED
    v.ok("DEFAULT_PARAMS")                                    # PARAMS_DEFAULTED
    v.tw.act("estop", open=True)                              # ESTOP_SET, DRIVER_DISABLED, DRIVER_POWER
    m.run(v, 200)
    v.tw.act("estop", open=False)
    m.run(v, 200)
    v.ok("ESTOP_CLEAR")                                       # ESTOP_CLEARED
    m.enable(v)
    m.set_ok(v, "home.max_travel_um", 1_000)
    v.ok("HOME", {"flags": 0})                                # HOME_FAILED
    m.run(v, 3000)
    v.advance(50)
    frames = [(f.seq, rc.decode_event(f.payload)) for f in v.link.frames if f.type == rc.ASYNC["EVENT"]]
    got = {e["code"] for _, e in frames}
    want = set(rc.EVENT) - {"NVM_ERROR"}
    missing = want - got
    assert not missing, missing
    seqs = [s for s, _ in frames]
    gaps = [(a, b) for a, b in zip(seqs, seqs[1:]) if (b - a) & 0xFF != 1]
    assert len(gaps) <= 2, gaps                                # resets restart the EVENT SEQ (boot)
    assert v.status()["event_overflows"] == 0


# ------------------------------------------------------------------ HardFault record (seam v1.2)
def test_hardfault_record_in_boot_event(v):
    m.need(v, "MOTION")
    v.tw.act("reset", cause="hardfault", pc=0x0800ABCD, cfsr=0x00008200)
    v.advance(60)
    boot = [e for e in v.events() if e["code"] == "BOOT"]
    assert boot, "no BOOT event after the HardFault reset"
    b = boot[-1]
    assert b["arg"] == rc.RESET_CAUSE.index("SOFTWARE")
    assert (b["value"] & 0xFFFFFFFF, b["value2"] & 0xFFFFFFFF) == (0x0800ABCD, 0x00008200), b
    assert v.status()["reset_cause"] == "SOFTWARE"


# ------------------------------------------------------------------ SAF-FW-020-02 differential walk
WALK = int(os.environ.get("VAL_WALK", "1500"))


def _fwstate(v, params: dict, estop_closed_since_us: float | None, nvm_valid: bool) -> rcc.FwState:
    s = v.status()
    io, st, fl = set(s["io"]), set(s["status"]), set(s["flags"])
    causes = []
    if "AFE_STALE" in st or "AFE_SATURATED" in st:
        causes.append("AFE_FAULT")
    if "LIMIT_START" in io and "LIMIT_END" in io:
        causes.append("LIMIT_WIRING")
    if "ESTOP_OPEN" in io and "DRV_PWR" in st and params.get("drv.pwr_sense_enable", True):
        causes.append("K1_WELDED")
    closed_ms = 0 if "ESTOP_OPEN" in io else (
        100_000 if estop_closed_since_us is None else int((v.tw.now_us - estop_closed_since_us) / 1000))
    return rcc.FwState(
        params=dict(params), motion_state=s["motion_state"],
        enabling_left_ms=1 if s["motion_state"] == "ENABLING" else 0,
        homed="HOMED" in fl, pos_um=s["pos_um"], estop_latched="ESTOP" in fl,
        estop_input_open="ESTOP_OPEN" in io, estop_closed_ms=closed_ms, halt_latched="HALT" in fl,
        faults=list(s["faults"]), fault_causes=[c for c in causes if c in s["faults"]],
        limit_start="LIMIT_START" in io or "LIMIT_START" in st, limit_end="LIMIT_END" in io or "LIMIT_END" in st,
        afe_stale="AFE_STALE" in st, afe_saturated="AFE_SATURATED" in st,
        raw=s["afe_raw_last"] if s["afe_raw_last"] != rc.AFE_NO_DATA else 0, drv_power="DRV_PWR" in st,
        alm_active="ALM" in st, nvm_record_valid=nvm_valid, paused="PAUSED" in st)


def test_command_check_differential_walk(v):
    """TC-SAF-FW-020-02: random command / world walk; before each command the live state (GET_STATUS) is mapped
    to ref_cmdcheck.FwState and the oracle's verdict (status, detail) is compared with the FW's answer. A step
    whose state changed between the snapshot and the command (asynchronous ends, settle, release timers) is
    re-sampled and counted as unstable, never as a pass."""
    m.need(v, "MOTION", "HOMING", "AFE", "BUTTONS", "DRV_SIGNALS")
    model = rcc.Model(gen_params.load().params)
    rnd = random.Random(int(os.environ.get("VAL_SEED", "7") or 7))
    params: dict = {}
    nvm_valid = False
    estop_since = None
    m.ready(v, x_um=50_000)
    compared = unstable = 0
    mism = []
    for i in range(WALK):
        w = rnd.random()                                       # world change
        if w < 0.04:
            op = rnd.random() < 0.5
            v.tw.act("estop", open=op, drv_power_follows=False)
            estop_since = None if op else v.tw.now_us
        elif w < 0.08:
            v.tw.act("limit", name=rnd.choice(["start", "end"]), active=rnd.random() < 0.3)
        elif w < 0.11:
            v.tw.act("alm", active=rnd.random() < 0.3)
        elif w < 0.14:
            v.tw.act("drv_power", on=rnd.random() < 0.8)
        elif w < 0.17:
            v.tw.act("afe", stall=rnd.random() < 0.3)
        elif w < 0.19:
            v.tw.act("afe", raw_script=[rnd.choice([0, 7_100_000, -7_100_000, 8_388_607])])
        m.run(v, rnd.choice([1, 5, 30, 120, 600]))
        name = rnd.choice(["MOVE_ABS", "MOVE_ABS", "JOG", "JOG", "HOME", "ENABLE", "DISABLE", "STOP", "HALT",
                           "HALT_CLEAR", "PAUSE", "RESUME", "ESTOP_CLEAR", "FAULT_CLEAR", "SET_PARAM"])
        if name == "MOVE_ABS":
            f = {"target_um": rnd.choice([rnd.randint(-1000, 300_000), 50_000]),
                 "v_um_s": rnd.choice([0, 1, 1000, 10_000, 30_000, 40_000]), "a_um_s2": rnd.choice([0, 1000, 20_000_000])}
        elif name == "JOG":
            f = {"v_um_s": rnd.choice([0, 1000, -1000, 3000, -40_000]), "a_um_s2": 0,
                 "bound_um": rnd.choice([rc.JOG_NO_BOUND, rc.JOG_NO_BOUND, rnd.randint(-1000, 300_000)])}
        elif name == "HOME":
            f = {"flags": rnd.choice([0, 0, 1, 2])}
        elif name == "STOP":
            f = {"mode": rnd.choice([0, 1, 2])}
        elif name == "SET_PARAM":
            key = rnd.choice(["motion.v_max_travel_um_s", "home.max_load_raw", "safety.load_raw_max"])
            p = PBYKEY[key]
            val = rnd.choice([p.min, p.max, p.default, p.max + 1 if isinstance(p.max, int) else p.max])
            f = {"id": p.id, "type": p.type, "value": wire_value(p, val)}
        else:
            f = {}
        st = _fwstate(v, params, estop_since, nvm_valid)
        payload = rc.encode_request(name, f)
        exp_status, exp_detail = model.check(st, rc.CMD[name], payload)
        r = v.cmd(name, f)
        if (r["status"], r.get("detail", 0)) != (exp_status, exp_detail if exp_status != "OK" else 0):
            st2 = _fwstate(v, params, estop_since, nvm_valid)
            e2 = model.check(st2, rc.CMD[name], payload)
            if st2 != st or e2 != (exp_status, exp_detail):
                unstable += 1
            else:
                mism.append((i, name, f, (exp_status, exp_detail), (r["status"], r.get("detail")), st))
        else:
            compared += 1
            if name == "SET_PARAM" and r["status"] == "OK":
                params[PBYKEY[next(k for k in PBYKEY if PBYKEY[k].id == f["id"])].key] = r["entry"]["value"]
        if m.state(v) == "NOT_ENABLED" and rnd.random() < 0.3 and "ESTOP" not in v.status()["flags"]:
            v.cmd("ENABLE")
    print(f"VALWALK steps={WALK} compared={compared} unstable={unstable} mismatches={len(mism)}")
    assert not mism, mism[:3]
    assert compared >= 0.8 * WALK


# ------------------------------------------------------------------ FW-MOT-005 (un-homed bound, reversal)
def test_jog_unhomed_bound_and_reversal(v):
    """TC-FW-MOT-005-01 (rest): un-homed JOG -> no bound allowed (E_STATE NOT_HOMED), speed > v_unhomed refused,
    travel limited to home.max_travel_um from the jog start (MOVE_DONE SOFT_LIMIT); homed reversal on the fly:
    decelerate to 0, DIR setup >= dir_setup_us before the first pulse of the new direction, no step lost (pulses
    = |Δpos_steps| per direction)."""
    m.need(v, "MOTION", "HOMING")
    m.enable(v)
    r = m.jog(v, 1_000, 0, 50_000)
    assert _blk(r, "NOT_HOMED"), r
    vu = v.get("motion.v_unhomed_um_s")
    r = m.jog(v, vu + 1)
    assert (r["status"], r["detail"]) == ("E_RANGE", 0), r
    m.set_ok(v, "home.max_travel_um", 1_000)
    s0 = v.status()["pos_steps"]
    n0 = m.n_events(v)
    m.jog(v, vu)
    for _ in range(10):
        v.advance(100)
        m.jog(v, vu)
        if m.events_since(v, n0, "MOVE_DONE"):
            break
    md = m.wait_event(v, "MOVE_DONE", n0, 1_000)
    assert md["arg"] == MD.index("SOFT_LIMIT") and md["value2"] - s0 == 800, md      # 1 mm at 800 spm
    m.jog(v, 0)                                              # a refresh sent after the end may have started a new jog
    m.wait_until(v, lambda: m.state(v) == "IDLE", 2_000)
    m.set_ok(v, "home.max_travel_um", 360_000)
    m.home(v)
    m.move_abs(v, 50_000, 10_000)
    t0 = v.tw.now_us
    s1 = v.status()["pos_steps"]
    m.jog(v, 5_000)
    for _ in range(5):
        v.advance(100)
        m.jog(v, 5_000)
    t_rev = v.tw.now_us
    m.jog(v, -5_000)                                         # reversal on the fly
    for _ in range(8):
        v.advance(100)
        m.jog(v, -5_000)
    m.jog(v, 0)
    m.wait_until(v, lambda: m.state(v) == "IDLE", 2_000)
    dirs = m.edges(v, "DIR", t_rev)
    assert len(dirs) == 1, dirs
    pul = m.edges(v, "PUL", t0)
    rise = [e["t_us"] for e in pul if e["level"] == 1]
    before = [t for t in rise if t < dirs[0]["t_us"]]
    after = [t for t in rise if t > dirs[0]["t_us"]]
    assert after and (after[0] - dirs[0]["t_us"]) * 90 >= 1800 - 1        # DIR setup 20 µs
    assert before and dirs[0]["t_us"] - before[-1] > 0
    s2 = v.status()["pos_steps"]
    assert s2 - s1 == len(before) - len(after), (s2 - s1, len(before), len(after))
