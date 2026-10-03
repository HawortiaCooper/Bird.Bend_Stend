"""Validator E - M2 twin suite: homing (PRE-WRITTEN, gated on GET_INFO MOTION + HOMING).

Oracle: val_oracles/homing_ref.py (ICD v0.5 §5.4 HOME, SRS FW-HOM-001…004, SAF-FW-021) on the twin world
model (x_true = count·1000/spm + shift; START active <=> x_true <= start_switch_um).

Verifies: FW-HOM-001, FW-HOM-002, FW-HOM-004, SAF-FW-021
"""
from __future__ import annotations

import homing_ref as hr
import ref_codec as rc
import vhelp_m2 as m

S_UM = -1500.0          # tools/README default world start_switch_um
SPM = 800.0


def _phases_during(v, n0, timeout_ms=120_000) -> list[str]:
    seen: list[str] = []

    def done():
        ph = v.status()["home_phase"]
        if not seen or seen[-1] != ph:
            seen.append(ph)
        return bool(m.events_since(v, n0, "MOVE_DONE"))
    assert m.wait_until(v, done, timeout_ms, 5.0), seen
    return seen


def test_home_default_world(v):
    """TC-FW-HOM-001-01: phase order, final x_true = edge + offset (± 1 step), HOMED, EVENT HOMED(0),
    MOVE_DONE TARGET at 0, expected START edges do not set LIMIT_START, POS_UNCERTAIN cleared."""
    m.need(v, "MOTION", "HOMING")
    m.enable(v)
    exp = hr.expect_home(m.world(v)["x_um_true"], S_UM, SPM, hr.HomeParams())
    n0 = m.n_events(v)
    v.ok("HOME", {"flags": 0})
    seen = _phases_during(v, n0)
    assert hr.phase_order_ok([p for p in seen if p != "NONE"], exp.phases), seen
    md = m.events_since(v, n0, "MOVE_DONE")[0]
    assert md["arg"] == rc.MOVE_DONE_REASON.index("TARGET") and md["value"] == 0
    homed = m.events_since(v, n0, "HOMED")
    assert len(homed) == 1 and homed[0]["value"] == 0
    assert not m.events_since(v, n0, "LIMIT_SET")
    lo, hi = exp.x_final_um
    assert lo <= m.world(v)["x_um_true"] <= hi
    s = v.status()
    assert "HOMED" in s["flags"] and "POS_UNCERTAIN" not in s["status"] and s["pos_um"] == 0


def test_home_with_start_active(v):
    """TC-FW-HOM-001-01: START active at HOME -> RELEASE first."""
    m.need(v, "MOTION", "HOMING")
    v.tw.act("world_shift", um=-2_000)              # x_true = -2000 < S: START active
    v.advance(5)
    m.enable(v)
    exp = hr.expect_home(m.world(v)["x_um_true"], S_UM, SPM, hr.HomeParams())
    assert exp.phases[1] == "RELEASE"
    n0 = m.n_events(v)
    v.ok("HOME", {"flags": 0})
    seen = _phases_during(v, n0)
    assert "RELEASE" in seen and hr.phase_order_ok([p for p in seen if p != "NONE"], exp.phases), seen
    lo, hi = exp.x_final_um
    assert lo <= m.world(v)["x_um_true"] <= hi


def test_home_not_found_and_wiring(v):
    """TC-FW-HOM-002-01: no switch within max_travel -> HOME_NOT_FOUND; END during FAST_SEEK -> HOME_WIRING;
    HOMED = 0, MOVE_DONE STOPPED, HOME_FAILED reason; FAULT_CLEAR clears at once."""
    m.need(v, "MOTION", "HOMING")
    m.set_ok(v, "home.max_travel_um", 1_000)
    m.enable(v)
    n0 = m.n_events(v)
    v.ok("HOME", {"flags": 0})
    hf = m.wait_event(v, "HOME_FAILED", n0, 30_000)
    assert hr.expect_home(0, S_UM, SPM, hr.HomeParams(max_travel_um=1_000)).result == "HOME_NOT_FOUND"
    assert rc.HOME_FAIL_REASON[hf["arg"]] == "NOT_FOUND"
    assert "HOME_NOT_FOUND" in v.status()["faults"] and "HOMED" not in v.status()["flags"]
    assert v.ok("FAULT_CLEAR")["cleared"] == ["HOME_NOT_FOUND"]
    m.set_ok(v, "home.max_travel_um", 360_000)
    n0 = m.n_events(v)
    v.ok("HOME", {"flags": 0})
    m.wait_until(v, lambda: v.status()["home_phase"] == "FAST_SEEK", 2_000)
    v.advance(50)
    v.tw.act("limit", name="end", active=True)
    hf = m.wait_event(v, "HOME_FAILED", n0, 2_000)
    assert rc.HOME_FAIL_REASON[hf["arg"]] == "WIRING" and "HOME_WIRING" in v.status()["faults"]
    md = m.events_since(v, n0, "MOVE_DONE")[-1]
    assert md["arg"] == rc.MOVE_DONE_REASON.index("STOPPED")


def test_home_aborted_is_not_a_fault(v):
    """TC-FW-HOM-002-01: STOP during homing -> HOME_FAILED ABORTED, no homing fault latched."""
    m.need(v, "MOTION", "HOMING")
    m.enable(v)
    n0 = m.n_events(v)
    v.ok("HOME", {"flags": 0})
    v.advance(100)
    v.ok("STOP", {"mode": 0})
    hf = m.wait_event(v, "HOME_FAILED", n0, 2_000)
    assert rc.HOME_FAIL_REASON[hf["arg"]] == "ABORTED"
    assert not v.status()["faults"] and "HOMED" not in v.status()["flags"]


def test_home_drift(v):
    """TC-FW-HOM-004-01: lost steps 0.1 mm -> no fault, HOMED value ≈ -100; 0.3 mm -> HOME_DRIFT
    (FAULT_SET 7, value = deviation), HOMED set, motion E_STATE FAULT until FAULT_CLEAR (clears at once)."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=10_000)
    p = hr.HomeParams()
    for shift, drift in ((100, False), (300, True)):
        v.tw.act("world_shift", um=shift)
        exp = hr.expect_home(m.world(v)["x_um_true"], S_UM, SPM, p, homed_before=True,
                             shift_since_last_home_um=shift)
        assert exp.drift_fault is drift
        n0 = m.n_events(v)
        v.ok("HOME", {"flags": 0})
        m.wait_event(v, "MOVE_DONE", n0, 120_000, 10.0)
        homed = m.events_since(v, n0, "HOMED")[0]
        assert exp.deviation_um[0] <= homed["value"] <= exp.deviation_um[1], (shift, homed)
        fs = [e for e in m.events_since(v, n0, "FAULT_SET") if e["arg"] == rc.FAULTS.index("HOME_DRIFT")]
        assert bool(fs) is drift and "HOMED" in v.status()["flags"]
        if drift:
            r = v.cmd("MOVE_ABS", {"target_um": 1_000, "v_um_s": 1_000, "a_um_s2": 0})
            assert r["status"] == "E_STATE"
            assert v.ok("FAULT_CLEAR")["cleared"] == ["HOME_DRIFT"]


def test_home_load_precheck(v):
    """TC-SAF-FW-021-01: |raw - zero_raw| > home.max_load_raw -> E_CONFIRM; with the flag homing runs."""
    m.need(v, "MOTION", "HOMING", "AFE")
    m.enable(v)
    v.tw.act("load_offset", counts=400_000)
    v.advance(100)
    raw = v.status()["afe_raw_last"]
    assert hr.load_precheck(raw, 0, 322_123, False) == "E_CONFIRM"
    r = v.cmd("HOME", {"flags": 0})
    assert r["status"] == "E_CONFIRM", r
    n0 = m.n_events(v)
    v.ok("HOME", {"flags": 1})
    md = m.wait_event(v, "MOVE_DONE", n0, 120_000, 10.0)
    assert md["arg"] == rc.MOVE_DONE_REASON.index("TARGET")
