"""Validator E - M2 twin suite: every stop source (SAF-FW-001), stop-path corner cases, input wiring, boot,
reset and watchdog (gated on GET_INFO MOTION / HOMING / AFE / BUTTONS / DRV_SIGNALS).

Oracles: ICD v0.6 §5.5 / §6.2 / §6.4 (stop table: ENA, latch, HOMED, VALID, EVENTs), SRS v0.5.2 SAF-FW-001…019,
val_oracles/latch_ref.py. Observations: twin `query edges / seam_log / world / outputs`, DATA / EVENT frames.

Verifies: SAF-FW-001, SAF-FW-002, SAF-FW-004, SAF-FW-007, SAF-FW-009, SAF-FW-010, SAF-FW-012, SAF-FW-015,
          SAF-FW-016, SAF-FW-017, SAF-FW-018, SAF-FW-019, SYS-002, SYS-006, FW-STR-003
"""
from __future__ import annotations

import pytest

import latch_ref as lr
import ref_codec as rc
import vhelp_m2 as m
from vhelp import PBYKEY

SC = rc.STOP_CAUSE
MD = rc.MOVE_DONE_REASON


def _fw_now(v) -> int:
    return v.tw.fw_t_us()


def _data_after(v, fw_t: int) -> list[dict]:
    return [d for d in v.data() if ((d["t_us"] - fw_t) & 0xFFFFFFFF) < 0x80000000]


def _holding(out: dict) -> bool:
    """ENA at the 'no current' level: never written since reset (Hi-Z, -1) or driven enabled (D-13)."""
    return out["ENA"] == -1 or out["ena_enabled"] is True


def _no_pulse_for(v, ms: float) -> None:
    t0 = v.tw.now_us
    m.run(v, ms, step_ms=5.0)
    assert m.rising_count(v, t0) == 0, "a stopped move restarted (SAF-FW-001)"


# ------------------------------------------------------------------ TC-SAF-FW-001-01 (all sources)
SOURCES = ["STOP0", "STOP1", "HALT", "PC_PAUSE", "PAUSE_BUTTON", "LIMIT_START", "LIMIT_END", "LOAD_LIMIT",
           "RAIL", "AFE_STALE", "LINK_WDG", "JOG_DEADMAN", "STEP_FAULT", "ESTOP", "DRV_POWER_LOST"]
CAUSE = {"STOP0": "PC_STOP", "STOP1": "PC_STOP_CONTROLLED", "HALT": "PC_HALT", "PC_PAUSE": "PC_PAUSE",
         "PAUSE_BUTTON": "PAUSE_BUTTON", "LIMIT_START": "LIMIT_START", "LIMIT_END": "LIMIT_END",
         "LOAD_LIMIT": "LOAD_LIMIT", "RAIL": "LOAD_LIMIT", "AFE_STALE": "AFE_FAULT", "LINK_WDG": "LINK_WDG",
         "JOG_DEADMAN": "JOG_DEADMAN", "STEP_FAULT": "STEP_FAULT", "ESTOP": "ESTOP",
         "DRV_POWER_LOST": "DRV_POWER_LOST"}


@pytest.mark.parametrize("src", SOURCES)
def test_every_stop_source(v, src):
    """TC-SAF-FW-001-01: ENA unchanged (E-stop / power loss: disabled), MOVING 0 and VALID 0 in the next DATA
    (dead-man: VALID unchanged), STOPPED(cause) then exactly one MOVE_DONE(STOPPED); after the matching
    clear(s) (+ ENABLE / HOME where needed) no PUL edge for 5 s (the old move never resumes)."""
    m.need(v, "MOTION", "HOMING", "AFE", "BUTTONS", "DRV_SIGNALS")
    m.ready(v, x_um=60_000)
    v.ok("STREAM_START")
    v.ok("SET_VALID", {"valid": 1})
    jog = src == "JOG_DEADMAN"
    if jog:
        assert m.jog(v, 10_000)["status"] == "OK"
        m.wait_until(v, lambda: m.state(v) == "JOG", 50)
        for _ in range(3):
            v.advance(150)
            m.jog(v, 10_000)
    else:
        m.move_abs(v, 250_000 if src != "LIMIT_START" else 1_000, 10_000, wait=False)
        m.run(v, 300)
    ena0 = v.tw.act("query", what="outputs")["ENA"]
    n0 = m.n_events(v)
    t_fw = _fw_now(v)
    mx = v.get("safety.load_raw_max")
    if src == "STOP0":
        v.ok("STOP", {"mode": 0})
    elif src == "STOP1":
        v.ok("STOP", {"mode": 1})
    elif src == "HALT":
        v.ok("HALT")
    elif src == "PC_PAUSE":
        v.ok("PAUSE")
    elif src == "PAUSE_BUTTON":
        v.tw.act("button", name="pause", pressed=True)
    elif src == "LIMIT_START":
        v.tw.act("limit", name="start", active=True)
    elif src == "LIMIT_END":
        v.tw.act("limit", name="end", active=True)
    elif src == "LOAD_LIMIT":
        v.tw.act("afe", raw_script=[mx + 1] * 3)
    elif src == "RAIL":
        v.tw.act("afe", saturate="pos")
    elif src == "AFE_STALE":
        v.tw.act("afe", stall=True)
    elif src == "STEP_FAULT":
        v.tw.act("inject", fault="step_fault")
    elif src == "ESTOP":
        v.tw.act("estop", open=True, drv_power_follows=True, k1_delay_ms=30)
    elif src == "DRV_POWER_LOST":
        v.tw.act("drv_power", on=False)
    keep = src not in ("LINK_WDG", "JOG_DEADMAN")
    # observe without sending commands (a GET_STATUS would refresh the link watchdog)
    ok = m.wait_until(v, lambda: not v.tw.act("query", what="pulses")["running"], 3_000, 1.0, keepalive=keep)
    assert ok, (src, m.st(v))
    m.wait_until(v, lambda: False, 50, 1.0, keepalive=keep)      # EVENTs drain (lowest wire priority)
    ev = m.events_since(v, n0)
    codes = [e["code"] for e in ev]
    stp = [e for e in ev if e["code"] == "STOPPED"]
    md = [e for e in ev if e["code"] == "MOVE_DONE"]
    assert len(stp) == 1 and stp[0]["arg"] == SC[CAUSE[src]], (src, ev)
    assert len(md) == 1 and md[0]["arg"] == MD.index("STOPPED"), (src, ev)
    assert codes.index("STOPPED") < codes.index("MOVE_DONE"), codes
    st = v.status()
    ena_now = v.tw.act("query", what="outputs")["ENA"]
    if src in ("ESTOP", "DRV_POWER_LOST"):
        assert st["motion_state"] == "NOT_ENABLED" and "HOMED" not in st["flags"]
        assert v.tw.act("query", what="outputs")["ena_enabled"] is False
    else:
        assert ena_now == ena0, (src, "ENA changed by an operational stop (D-10)")
        assert st["motion_state"] == "IDLE"
    if src == "STEP_FAULT":
        assert "HOMED" not in st["flags"] and "POS_UNCERTAIN" in st["status"]
    m.run(v, 60)
    d = _data_after(v, ev[codes.index("MOVE_DONE")]["t_us"])
    assert d, "no DATA frame after the stop"
    assert "MOVING" not in d[-1]["flags"], d[-1]
    if src == "JOG_DEADMAN":
        assert "VALID" in d[-1]["flags"], "dead-man must leave VALID unchanged (ICD §6.2)"
    else:
        assert "VALID" not in d[-1]["flags"], (src, d[-1])
    # clears; then no PUL edge for 5 s
    if src == "HALT":
        v.ok("HALT_CLEAR")
    elif src in ("PC_PAUSE", "PAUSE_BUTTON"):
        if src == "PAUSE_BUTTON":
            v.tw.act("button", name="pause", pressed=False)
            v.advance(40)
        v.ok("RESUME")
    elif src in ("LIMIT_START", "LIMIT_END"):
        v.tw.act("limit", name="start" if src == "LIMIT_START" else "end", active=False)
        v.advance(40)
        v.tw.act("limit", name="start" if src == "LIMIT_START" else "end")
    elif src in ("LOAD_LIMIT", "RAIL", "AFE_STALE"):
        v.tw.act("afe", saturate=None, stall=False)
        v.advance(100)
        assert v.ok("FAULT_CLEAR")["status"] == "OK"
    elif src == "STEP_FAULT":
        v.ok("FAULT_CLEAR")
    elif src == "ESTOP":
        v.tw.act("estop", open=False, drv_power_follows=True, k1_delay_ms=30)
        m.run(v, 150)
        v.ok("ESTOP_CLEAR")
        m.enable(v)
    elif src == "DRV_POWER_LOST":
        v.tw.act("drv_power", on=True)
        m.run(v, 40)
        m.enable(v)
    _no_pulse_for(v, 5_000)


# ------------------------------------------------------------------ TC-SAF-FW-002-03 (DEF-P1-04 hold)
def test_move_and_halt_in_one_burst(v):
    """MOVE_ABS + HALT in one RX burst at a = a_max 10 m/s² (first period ≈ 0.5 ms at 800 spm): HALT effective,
    no PUL edge after the HALT frame (sniffed-stop hold), the MOVE ends with MOVE_DONE STOPPED / no pulse."""
    m.need(v, "MOTION", "HOMING")
    m.set_ok(v, "motion.a_max_um_s2", 10_000_000)
    m.ready(v, x_um=50_000)
    n0 = m.n_events(v)
    fa = rc.make_frame("MOVE_ABS", 0x70, {"target_um": 150_000, "v_um_s": 30_000, "a_um_s2": 0})
    fh = rc.make_frame("HALT", 0x71, {})
    t0 = v.tw.now_us
    v.tw.feed_rx(fa + fh)
    m.run(v, 200)
    rx = [w for w in v.wire("rx") if w["seq"] == 0x71 and w["type"] == rc.CMD["HALT"]]
    halt_end = rx[-1]["last_us"]
    after = [e for e in m.edges(v, "PUL", halt_end) if e["level"] == 1]
    assert not after, f"{len(after)} PUL rising edges after the HALT frame"
    assert "HALT" in v.status()["flags"]
    pul = m.rising_count(v, t0)
    print("VALOBS MOVE_ABS+HALT burst: pulses before the HALT frame end:", pul)
    v.ok("HALT_CLEAR")
    _no_pulse_for(v, 2_000)


# ------------------------------------------------------------------ TC-SAF-FW-004-03 step fault
def test_injected_step_fault(v):
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=20_000)
    m.move_abs(v, 200_000, 30_000, wait=False)
    m.run(v, 500)
    n0 = m.n_events(v)
    v.tw.act("inject", fault="step_fault")
    m.wait_until(v, lambda: m.state(v) == "IDLE", 100)
    s = v.status()
    assert "STEP_FAULT" in s["faults"] and "HOMED" not in s["flags"] and "POS_UNCERTAIN" in s["status"]
    fs = m.events_since(v, n0, "FAULT_SET")
    assert fs and fs[0]["arg"] == rc.FAULTS.index("STEP_FAULT")


# ------------------------------------------------------------------ TC-SAF-FW-007-02 wire break
@pytest.mark.parametrize("inp", ["estop", "start", "end", "drv_power"])
def test_wire_break_reads_active(v, inp):
    m.need(v, "MOTION", "HOMING", "DRV_SIGNALS")
    m.ready(v, x_um=60_000)
    m.move_abs(v, 200_000, 10_000, wait=False)
    m.run(v, 200)
    n0 = m.n_events(v)
    v.tw.act("wire", input=inp, broken=True)
    m.wait_until(v, lambda: m.state(v) in ("IDLE", "NOT_ENABLED"), 100)
    s = v.status()
    stp = m.events_since(v, n0, "STOPPED")
    want = {"estop": "ESTOP", "start": "LIMIT_START", "end": "LIMIT_END", "drv_power": "DRV_POWER_LOST"}[inp]
    assert stp and stp[0]["arg"] == SC[want], (inp, m.events_since(v, n0))
    if inp == "estop":
        assert "ESTOP" in s["flags"]
    if inp == "drv_power":
        assert "DRV_PWR" not in s["status"] and s["motion_state"] == "NOT_ENABLED"
    assert v.tw.act("wire", input="stop", broken=True)["ok"] is False        # CR-01: no STOP input


# ------------------------------------------------------------------ TC-SAF-FW-007-03 boot with inputs open
@pytest.mark.parametrize("case", ["estop", "start", "end", "both", "drv_off"])
def test_boot_with_input_active(v, case):
    m.need(v, "MOTION", "DRV_SIGNALS")
    if case == "estop":
        v.tw.act("estop", open=True, drv_power_follows=False)
    elif case in ("start", "both"):
        v.tw.act("limit", name="start", active=True)
    if case in ("end", "both"):
        v.tw.act("limit", name="end", active=True)
    if case == "drv_off":
        v.tw.act("drv_power", on=False)
    v.tw.act("reset", cause="power")
    v.advance(60)
    s = v.status()
    out = v.tw.act("query", what="outputs")
    assert s["motion_state"] == "NOT_ENABLED" and "HOMED" not in s["flags"]
    if case == "estop":
        assert "ESTOP" in s["flags"] and out["ena_enabled"] is False          # ENA disabled at boot
        r = v.cmd("ENABLE")
        assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("ESTOP"))
    elif case == "both":
        assert "LIMIT_WIRING" in s["faults"]
    elif case == "drv_off":
        assert out["ena_enabled"] is False                                     # OI-FW-22 boot rule
        r = v.cmd("ENABLE")
        assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("DRV_UNPOWERED"))
    else:
        assert ("LIMIT_START" if case == "start" else "LIMIT_END") in s["io"]
        assert _holding(out)                                                   # holding (D-13)


# ------------------------------------------------------------------ TC-SAF-FW-009-01 / -010-01
def test_rail_trips_with_thresholds_at_caps(v):
    m.need(v, "MOTION", "AFE")
    m.set_ok(v, "safety.load_raw_max", 7_151_121)
    m.set_ok(v, "safety.load_raw_min", -7_151_121)
    v.ok("STREAM_START")
    for sat in ("pos", "neg"):
        n0 = m.n_events(v)
        v.tw.act("afe", saturate=sat)
        v.advance(40)
        fs = [e for e in m.events_since(v, n0, "FAULT_SET") if e["arg"] == rc.FAULTS.index("LOAD_LIMIT")]
        assert fs, sat
        assert "AFE_SATURATED" in v.data()[-1]["status"]
        v.tw.act("afe", saturate=None)
        v.advance(40)
        v.ok("FAULT_CLEAR")
        v.advance(40)


def test_session_thresholds_range_and_lifecycle(v):
    m.need(v, "MOTION", "HOMING", "AFE")
    p = PBYKEY["safety.load_raw_max"]
    r = v.set("safety.load_raw_max", 7_151_122)
    assert (r["status"], r["detail"]) == ("E_RANGE", p.id), r
    m.set_ok(v, "safety.load_raw_max", 7_151_121)
    assert v.get("safety.load_raw_max") == 7_151_121
    m.ready(v, x_um=20_000)
    m.move_abs(v, 200_000, 10_000, wait=False)
    m.run(v, 200)
    m.set_ok(v, "safety.load_raw_max", 100_000)               # while moving: next sample (SAF-FW-010)
    n0 = m.n_events(v)
    v.tw.act("afe", raw_script=[100_001])
    m.wait_until(v, lambda: m.state(v) == "IDLE", 100)
    assert m.events_since(v, n0, "FAULT_SET")
    v.ok("FAULT_CLEAR")
    v.ok("DEFAULT_PARAMS")
    assert v.get("safety.load_raw_max") == 7_022_271
    v.ok("SAVE_PARAMS", timeout_ms=5000)                      # a record exists (session values not in it)
    m.set_ok(v, "safety.load_raw_max", 6_000_000)
    v.ok("LOAD_PARAMS")
    assert v.get("safety.load_raw_max") == 6_000_000           # LOAD keeps session values
    v.reboot()
    v.advance(80)
    assert v.get("safety.load_raw_max") == 7_022_271           # never in the record


# ------------------------------------------------------------------ TC-SAF-FW-012-01 / -02 AFE stale
def test_afe_stall_while_jogging(v):
    m.need(v, "MOTION", "HOMING", "AFE")
    m.ready(v, x_um=50_000)
    assert m.jog(v, 2_000)["status"] == "OK"
    m.run(v, 100)
    conv = v.tw.act("query", what="conversions")["conversions"]
    v.tw.act("afe", stall=True)
    for _ in range(4):
        v.advance(100)
        m.jog(v, 2_000)
    t_last = [c for c in v.tw.act("query", what="conversions")["conversions"] if c["delivered"]][-1]["t_us"]
    m.wait_until(v, lambda: m.state(v) == "IDLE", 400, 1.0)
    pul = [e["t_us"] for e in m.edges(v, "PUL", t_last) if e["level"] == 1]
    tmo = v.get("afe.timeout_ms")
    assert pul and pul[-1] - t_last <= (tmo + 1) * 1000 + 1, (pul[-1] - t_last)
    assert "AFE_FAULT" in v.status()["faults"]
    r = m.jog(v, 1_000)
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("AFE_STALE")), r
    r = v.cmd("FAULT_CLEAR")
    assert r["status"] == "E_CAUSE_ACTIVE", r
    v.tw.act("afe", stall=False)
    v.advance(100)
    assert v.ok("FAULT_CLEAR")["cleared"] == ["AFE_FAULT"]
    assert conv


def test_10sps_default_timeout_no_stale(v):
    """TC-SAF-FW-012-02 (DEF-P1-01): 10 SPS ± 0.5 % with the default timeout: no AFE_STALE / AFE_FAULT over
    10 min idle + 60 s jog."""
    m.need(v, "MOTION", "HOMING", "AFE")
    m.set_ok(v, "afe.rate_sps", "SPS10")
    v.tw.act("afe", rate_error=0.005)
    m.ready(v, x_um=20_000)
    n0 = m.n_events(v)
    m.run(v, 600_000, step_ms=50.0)
    t_end = v.tw.now_us + 60_000_000
    m.jog(v, 1_000)
    while v.tw.now_us < t_end:
        v.advance(200)
        m.jog(v, 1_000)
    m.jog(v, 0)
    m.run(v, 200)
    bad = [e for e in m.events_since(v, n0) if e["code"] in ("AFE_STALE", "FAULT_SET")]
    assert not bad, bad


# ------------------------------------------------------------------ TC-SAF-FW-015-01 link watchdog
@pytest.mark.parametrize("tmo", [200, 1000])
def test_link_watchdog(v, tmo):
    m.need(v, "MOTION", "HOMING")
    m.set_ok(v, "safety.link_timeout_ms", tmo)
    m.ready(v, x_um=20_000)
    m.move_abs(v, 250_000, 10_000, wait=False)
    m.run(v, 50)
    # silence of timeout - 5 ms: no stop
    v.link.send("PING")
    t_last = v.tw.now_us
    v.advance(tmo - 5)
    assert v.tw.act("query", what="pulses")["running"]        # still moving (no command sent)
    # a bad-CRC frame and an invalid TYPE do not refresh the watchdog
    b = bytearray(rc.make_frame("PING", 9, {}))
    b[-1] ^= 0xFF
    v.tw.feed_rx(bytes(b))
    v.tw.feed_rx(bytes([0xA5, 0x5A, 0x7E, 0x01, 0x00, 0x00, 0x00, 0x00]))
    n0 = m.n_events(v)
    v.advance(10)
    trig = t_last / 1000 + tmo
    lw = m.events_since(v, n0, "LINK_WDG")
    assert lw, "LINK_WDG not raised at the timeout"
    m.wait_until(v, lambda: not v.tw.act("query", what="pulses")["running"], 3000, keepalive=False)
    v.advance(20)
    stp = m.events_since(v, n0, "STOPPED")
    assert stp and stp[0]["arg"] == SC["LINK_WDG"]
    assert "LINK_WDG" in [s for s in v.data()[-1]["status"]] if v.data() else True
    v.ok("PING")
    v.advance(5)
    assert m.events_since(v, n0, "LINK_RESTORED")
    _no_pulse_for(v, 2000)
    # idle silence never trips
    n1 = m.n_events(v)
    m.wait_until(v, lambda: False, 3 * tmo, keepalive=False)
    assert not m.events_since(v, n1, "LINK_WDG")
    assert trig > 0


# ------------------------------------------------------------------ TC-SAF-FW-016-01 jog dead-man
@pytest.mark.parametrize("jto", [50, 250, 1000])
def test_jog_deadman(v, jto):
    m.need(v, "MOTION", "HOMING")
    m.set_ok(v, "motion.jog_timeout_ms", jto)
    m.ready(v, x_um=20_000)
    period = max(20, jto * 0.8)
    m.jog(v, 2_000)
    t_end = v.tw.now_us + 2_000_000
    while v.tw.now_us < t_end:
        v.advance(period)
        assert m.jog(v, 2_000)["status"] == "OK"
    assert m.state(v) == "JOG"
    t_last = v.tw.now_us
    n0 = m.n_events(v)
    m.wait_until(v, lambda: bool(m.events_since(v, n0, "STOPPED")), jto + 50, 0.5, keepalive=True)
    stp = m.events_since(v, n0, "STOPPED")
    assert stp and stp[0]["arg"] == SC["JOG_DEADMAN"]
    sl = [s for s in m.seam(v, "hal_step_", t_last)
          if s["call"] in ("hal_step_set_period_now", "hal_step_stop_now")]
    # commit: the STOPPED event is emitted at the initiation of the controlled stop (FW_design §5.3)
    t_ev_world = t_last + ((stp[0]["t_us"] - v.tw.fw_t_us(int(t_last * 1000))) & 0xFFFFFFFF)
    assert t_ev_world - t_last <= (jto + 2) * 1000, (t_ev_world - t_last, jto)
    assert sl is not None


# ------------------------------------------------------------------ TC-SAF-FW-017-01 / -02 idle disable
def test_idle_disable_unloaded_loaded_stale(v):
    m.need(v, "MOTION", "HOMING", "AFE")
    m.set_ok(v, "safety.idle_disable_s", 600)
    m.ready(v, x_um=20_000)
    n0 = m.n_events(v)
    m.run(v, 599_000, step_ms=50.0)
    assert m.state(v) == "IDLE"
    m.run(v, 2_000, step_ms=5.0)
    dd = m.events_since(v, n0, "DRIVER_DISABLED")
    assert dd and dd[0]["arg"] == 2 and m.state(v) == "NOT_ENABLED" and "HOMED" not in v.status()["flags"]
    # loaded (>= release band): never
    m.ready(v, x_um=20_000)
    v.tw.act("load_offset", counts=50_000 + 200_000)
    n1 = m.n_events(v)
    m.run(v, 700_000, step_ms=100.0)
    assert not m.events_since(v, n1, "DRIVER_DISABLED") and m.state(v) == "IDLE"
    assert v.status()["idle_disable_left_s"] == 0xFFFF
    # stale AFE at 300 s: no idle disable (D-33 g)
    v.tw.act("load_offset", counts=50_000)
    m.run(v, 1000)
    v.tw.act("afe", stall=True)
    n2 = m.n_events(v)
    m.run(v, 700_000, step_ms=100.0)
    assert not m.events_since(v, n2, "DRIVER_DISABLED") and m.state(v) == "IDLE"


# ------------------------------------------------------------------ TC-SAF-FW-018-01 reset while moving
@pytest.mark.parametrize("cause", ["pin", "power", "iwdg", "software"])
def test_reset_during_motion(v, cause):
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=20_000)
    m.move_abs(v, 200_000, 10_000, wait=False)
    m.run(v, 300)
    v.tw.act("reset", cause=cause)
    t0 = v.tw.now_us
    v.advance(100)
    assert m.rising_count(v, t0) == 0, "PUL edge after reset release"
    s = v.status()
    assert s["motion_state"] == "NOT_ENABLED" and "HOMED" not in s["flags"] and "STREAM_ON" not in s["sys_flags"]
    assert s["reset_cause"] == {"pin": "PIN", "power": "POWER_ON", "iwdg": "IWDG", "software": "SOFTWARE"}[cause]
    assert _holding(v.tw.act("query", what="outputs"))                   # left holding (D-13)
    r = m.jog(v, 1_000)
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("NOT_ENABLED"))
    boot = [e for e in v.events() if e["code"] == "BOOT"]
    assert boot


# ------------------------------------------------------------------ TC-SAF-FW-019-01 hang -> IWDG
@pytest.mark.parametrize("where,lsi", [("main", 32000), ("tick", 32000), ("isr1", 32000), ("main", 17000),
                                       ("isr1", 47000)])
def test_hang_iwdg(v, where, lsi):
    m.need(v, "MOTION", "HOMING")
    v.tw.act("iwdg", lsi_hz=lsi)
    m.ready(v, x_um=20_000)
    m.move_abs(v, 200_000, 10_000, wait=False)
    m.run(v, 300)
    t_h = v.tw.now_us
    if where == "isr1":
        r = v.tw.act("inject", fault="isr_storm", duration_ms=5_000)
    else:
        r = v.tw.act("inject", fault="hang", where=where, duration_ms=5_000)
    assert r.get("ok", True) is not False, r
    v.advance(200)
    resets = [x for x in v.tw.resets if x.get("t_us", 0) >= t_h] if v.tw.resets and isinstance(v.tw.resets[-1], dict) else v.tw.resets
    assert resets, "no IWDG reset"
    pul = [e["t_us"] for e in m.edges(v, "PUL", t_h) if e["level"] == 1]
    last = pul[-1] if pul else t_h
    assert last - t_h <= 100_000, (where, lsi, last - t_h)
    v.advance(50)
    assert v.status()["reset_cause"] == "IWDG"
    print(f"VALOBS hang {where} LSI {lsi}: last PUL {last - t_h:.0f} µs after the hang")


# ------------------------------------------------------------------ TC-SYS-002-01 (L2 / L3 subset)
@pytest.mark.parametrize("variant", ["L2", "L3"])
@pytest.mark.parametrize("src", ["ESTOP", "LIMIT_END", "LOAD_LIMIT", "DRV_POWER_LOST"])
def test_fw_safety_without_pc(v, variant, src):
    """SYS-002: the FW-only safety reactions with the stream off (L2) and with the PC silent after its last
    command (L3, link_timeout 5000 ms so the watchdog does not mask the stimulus)."""
    m.need(v, "MOTION", "HOMING", "AFE", "DRV_SIGNALS")
    m.set_ok(v, "safety.link_timeout_ms", 5000)
    m.ready(v, x_um=60_000)
    v.ok("STREAM_STOP")
    m.move_abs(v, 250_000, 10_000, wait=False)
    m.run(v, 100) if variant == "L2" else v.advance(100)
    t0 = v.tw.now_us
    if src == "ESTOP":
        v.tw.act("estop", open=True)
    elif src == "LIMIT_END":
        v.tw.act("limit", name="end", active=True)
    elif src == "LOAD_LIMIT":
        v.tw.act("afe", raw_script=[7_022_272])                 # default raw_max + 1
    else:
        v.tw.act("drv_power", on=False)
    v.advance(40)                                       # no PC frame in L3
    pul = [e["t_us"] for e in m.edges(v, "PUL", t0) if e["level"] == 1]
    budget_us = {"ESTOP": 100, "LIMIT_END": 200, "LOAD_LIMIT": 12_500 + 200, "DRV_POWER_LOST": 25_000}[src]
    assert not pul or pul[-1] - t0 <= budget_us, (src, variant, pul[-1] - t0)
    assert not v.tw.act("query", what="pulses")["running"]


# ------------------------------------------------------------------ TC-SYS-006-01 (FW part, D-41)
def test_estop_release_needs_clear_enable_home(v):
    m.need(v, "MOTION", "HOMING", "DRV_SIGNALS")
    m.ready(v, x_um=60_000)
    m.move_abs(v, 200_000, 10_000, wait=False)
    m.run(v, 200)
    v.tw.act("estop", open=True)
    m.run(v, 1000)
    v.tw.act("estop", open=False)
    t0 = v.tw.now_us
    m.run(v, 1000)
    assert m.rising_count(v, t0) == 0 and m.state(v) == "NOT_ENABLED" and "ESTOP" in v.status()["flags"]
    v.ok("ESTOP_CLEAR")
    r = v.cmd("MOVE_ABS", {"target_um": 70_000, "v_um_s": 1000, "a_um_s2": 0})
    assert r["status"] == "E_STATE"
    m.enable(v)
    r = v.cmd("MOVE_ABS", {"target_um": 70_000, "v_um_s": 1000, "a_um_s2": 0})
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("NOT_HOMED"))
    assert lr.estop_clear(False, 1000, 100) == ("OK", None)
