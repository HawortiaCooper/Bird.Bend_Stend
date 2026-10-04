"""Validator E - self-check of the M2 oracles (runs now, no FW M2 code needed).

Anchors the validator's independent oracles (val_oracles/ramp_ref.py, homing_ref.py, latch_ref.py) to
the normative numbers before they are used to judge Implementer A's M2 code:
- R4 §12 TV-M (exact sqrt ramp with fractional carry, planner), ICD §6.5 clean-halt statements,
  FW_design §5.6.1/§5.6.2 pulse-timing statements (DEF-P1-04 c1 value);
- vectors/motion_vectors.json when the Integrator publishes it (OI-FW-20): every case compared;
- SRS SAF-FW-006/008/009/011/013/024/025 and FW-SW-005 rule tables.
Negative controls prove that the comparison helpers can fail.

Verifies: FW-MOT-001, FW-MOT-003, SAF-FW-003, FW-HOM-001, FW-HOM-004, SAF-FW-006, SAF-FW-008,
          SAF-FW-009, SAF-FW-011, SAF-FW-013, SAF-FW-024, SAF-FW-025, FW-SW-005 (oracle level)
TC: TC-FW-MOT-003-01 (oracle anchor), TC-FW-MOT-003-02 (oracle), TC-FW-HOM-001-01 / -004-01 (oracle),
    TC-SAF-FW-008-02 (oracle)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import homing_ref as hr
import latch_ref as lr
import ramp_ref as rr
import vhelp as VH

VEC = Path(__file__).resolve().parents[3] / "00_System" / "tools" / "vectors"
F = rr.F_TICK_TARGET
A_TVM = 100_000 * 640 / 1000      # 64 000 steps/s²
V_TVM = 20_000 * 640 / 1000       # 12 800 steps/s


# --------------------------------------------------------------------------- R4 TV-M anchors
def test_tvm_constants():
    c = rr.ramp_constant(F, A_TVM)
    assert c == pytest.approx(503115.2949374527, rel=1e-12)
    assert F / V_TVM == 7031.25


def test_tvm_trapezoid_5s2():
    p = rr.ramp_periods(64000, F, V_TVM, A_TVM, A_TVM)
    assert p[:5] == [503115, 208397, 159909, 134809, 118770]
    assert sum(p) == 468_000_000
    assert sum(1 for c in p if c > 7032) == 2559
    assert (p.count(7031), p.count(7032)) == (46080, 15361)
    assert p[30000:30008] == [7031, 7031, 7031, 7032, 7031, 7031, 7031, 7032]


def test_tvm_triangles():
    p = rr.ramp_periods(1000, F, V_TVM, A_TVM, A_TVM)
    assert sum(p) == 22_500_000 and p[-3:] == [159909, 208397, 503116] and min(p) == 11255
    p = rr.ramp_periods(1000, F, V_TVM, A_TVM, 2 * A_TVM)
    assert sum(p) == 19_485_570 and p[-1] == 355756 and min(p) == 9744


def test_tvm_planner():
    t = rr.plan_trapezoid(64000, V_TVM, A_TVM, A_TVM)
    assert (t["kind"], t["n_acc"], t["n_cruise"], t["n_dec"]) == ("trap", 1280, 61440, 1280)
    assert t["t"] == pytest.approx(5.2)
    t = rr.plan_trapezoid(1000, V_TVM, A_TVM, 2 * A_TVM)
    assert (t["kind"], t["n_acc"], t["n_dec"]) == ("tri", 667, 333)
    assert t["t"] == pytest.approx(0.21650635094610965)
    # duration from the integer periods equals the continuous profile to the tick (R4 §1.5)
    assert rr.move_ticks(64000, F, V_TVM, A_TVM, A_TVM) == round(5.2 * F)


def _motion_vectors() -> dict:
    f = VEC / "motion_vectors.json"
    if not f.exists():
        pytest.skip("vectors/motion_vectors.json not published yet (Integrator, OI-FW-20)")
    return json.loads(f.read_text(encoding="utf-8"))


def test_motion_vectors_cases_vs_independent_oracle():
    """OI-FW-20: every case of the Integrator's motion vectors re-derived from its wire parameters and
    events by the validator's independent implementation; binary64 on both sides -> exact."""
    mv = _motion_vectors()
    cases = mv["cases"]
    assert len(cases) >= 9
    executed = 0
    for c in cases:
        p = rr.case_periods(c)
        assert len(p) == c["n_periods"] == len(c["periods"]), c["name"]
        assert sum(p) == c["sum_ticks"] == sum(c["periods"]), c["name"]
        ok, msg = rr.check_periods(p, c["periods"], tol_each=0)
        assert ok, (c["name"], msg)
        assert c["tolerance"]["period_ticks"] == 1
        # D-40 b: total tolerance ±ceil(N/1000) ticks (31 280 periods -> 32), SRS FW-MOT-003 aligned
        assert c["tolerance"]["sum_ticks"] == max(1, math.ceil(c["n_periods"] / 1000)), c["name"]
        executed += 1
    assert executed == len(cases)                               # anti-skip


def test_motion_vectors_ctrl_stop_paths_and_planner():
    mv = _motion_vectors()
    for row in mv["ctrl_stop_paths"]:
        a_stop = row["a_stop_um_s2"] * rr.binary32(row["steps_per_mm"]) / 1000.0
        assert rr.ctrl_stop_path(row["p_ticks"], F, a_stop) == row["path"], row
        assert rr.stop_distance_steps(F / row["p_ticks"], a_stop) == pytest.approx(row["d_steps"], rel=1e-12)
    for row in mv["planner"]:
        t = rr.plan_trapezoid(row["n_steps"], row["v_steps_s"], row["a_steps_s2"], row["d_steps_s2"])
        assert (t["kind"], t["n_acc"], t["n_dec"]) == (row["kind"], row["n_acc"], row["n_dec"]), row
        assert t["t"] == pytest.approx(row["t"], rel=1e-12)
    assert mv["icd_version"] == VH.gen_define("PROTO_ICD_VERSION").strip('"')     # OI-FW-36


# --------------------------------------------------------------------------- helper controls
def test_check_periods_negative_controls():
    p = rr.ramp_periods(1000, F, V_TVM, A_TVM, A_TVM)
    assert rr.check_periods(p, p)[0]
    q = list(p)
    q[10] += 2
    assert not rr.check_periods(q, p)[0], "a 2-tick error must fail"
    assert not rr.check_periods(p[:-1], p)[0], "a lost step must fail"
    q = [x + 1 for x in p]                              # every period +1: each ok, total 1000 ticks off
    assert not rr.check_periods(q, p)[0], "a systematic +1 tick must fail the N/1000 total"


def test_random_cases_are_well_formed():
    for name, n, f, v, a, d in rr.random_cases(200, 7):
        p = rr.ramp_periods(n, f, v, a, d)
        assert len(p) == n and min(p) >= int(f / v) - 1 and max(p) < 2**32, name
        # symmetric case: first and last period mirror within the carry (±1 tick)
        if a == d and n > 2:
            assert abs(p[0] - p[-1]) <= 1, name


# ------------------------------------------------------------------- pulse timing / c1 claims
def test_pulse_timing_defaults():
    pt = rr.pulse_timing(10000, 10000, 50000, 20)
    assert (pt.pw_ticks, pt.c_min_ticks, pt.dir_setup_ticks) == (900, 1800, 1800)
    pt = rr.pulse_timing(2500, 2500, 100000, 5)          # range ends: 100 kHz cap reachable
    assert pt.c_min_ticks == 900 and pt.pw_ticks == 225


def test_first_period_c1_def_p1_04():
    """FW_design §5.6.2 item 4 / DEF-P1-04: c1 ≈ 45 µs at a_max 10 m/s², spm 100 000."""
    alpha = rr.steps_per_s(10_000_000, 100_000)
    assert rr.ramp_constant(F, alpha) / F * 1e6 == pytest.approx(44.72, abs=0.01)
    assert rr.ramp_constant(F, rr.steps_per_s(10_000_000, 800)) / F * 1e3 == pytest.approx(0.5, abs=0.01)


# --------------------------------------------------------------- controlled stop / clean halt
def test_clean_halt_condition_icd_6_5():
    a_def = rr.steps_per_s(1_000_000, 800)                       # 800 000 steps/s²
    assert rr.clean_halt_allowed(1 / 499, 499, a_def)            # P > 2 ms, d = 0.156 step
    assert not rr.clean_halt_allowed(1 / 500, 500, a_def)        # P = 2 ms exactly: condition 1 fails
    assert rr.stop_distance_steps(500, rr.steps_per_s(1_000_000, 160)) == pytest.approx(0.78125)
    assert rr.clean_halt_allowed(1 / 499, 499, 125_000)          # d = 0.996
    assert not rr.clean_halt_allowed(1 / 499, 499, 124_000)      # d = 1.004 > 1 -> decelerate
    assert rr.stop_index(V_TVM, A_TVM) == 1280                   # R4: 2 mm at 640 spm


def test_jog_ramp_accel_cruise_stop():
    j = rr.JogRamp(F, A_TVM, A_TVM, A_TVM)
    j.set_target(V_TVM)
    seq = [j.next_period() for _ in range(3000)]
    assert seq[:5] == [503115, 208397, 159909, 134809, 118770]
    assert seq[-1] in (7031, 7032)
    j.set_target(0)
    stop = []
    while (c := j.next_period()) is not None:
        stop.append(c)
    assert abs(len(stop) - rr.stop_distance_steps(V_TVM, A_TVM)) <= 1           # SAF-FW-003 ±1 step
    assert all(b + 1 >= a for a, b in zip(stop, stop[1:])), "stop periods non-decreasing"
    assert rr.jog_properties(seq + stop, F, V_TVM, A_TVM, A_TVM) == []
    bad = seq + stop
    bad[-5] = bad[-6] - 50                                        # negative control: speed-up while stopping
    assert rr.jog_properties(bad, F, V_TVM, A_TVM, A_TVM)


def test_jog_speed_change_down_then_up():
    j = rr.JogRamp(F, A_TVM, A_TVM)
    j.set_target(V_TVM)
    for _ in range(3000):
        j.next_period()
    j.set_target(V_TVM / 2)
    down = [j.next_period() for _ in range(2000)]
    assert down[-1] in (14062, 14063)                             # f / 6400 = 14062.5
    assert all(b + 1 >= a for a, b in zip(down[:900], down[1:900]))
    j.set_target(V_TVM)
    up = [j.next_period() for _ in range(2000)]
    assert up[-1] in (7031, 7032)


# --------------------------------------------------------------------------- homing oracle
def test_homing_default_world():
    p = hr.HomeParams()
    e = hr.expect_home(0.0, -1500.0, 800.0, p)
    assert e.result == "HOMED" and e.phases[-1] == "DONE" and "RELEASE" not in e.phases
    lo, hi = e.x_final_um
    assert lo < -500.0 <= hi and hi - lo == pytest.approx(1.25, abs=0.01)     # one step at 800 spm
    assert e.deviation_um == (0.0, 0.0) and e.drift_fault is False


def test_homing_drift_rules():
    p = hr.HomeParams()
    assert hr.expect_home(0, -1500, 800, p, homed_before=True, shift_since_last_home_um=100).drift_fault is False
    e = hr.expect_home(0, -1500, 800, p, homed_before=True, shift_since_last_home_um=300)
    assert e.drift_fault is True and e.fault_set == "HOME_DRIFT"
    assert e.deviation_um[0] <= -300 <= e.deviation_um[1]
    assert hr.expect_home(0, -1500, 800, p, homed_before=True, shift_since_last_home_um=200).drift_fault is None


def test_homing_failures():
    p = hr.HomeParams(max_travel_um=1000)
    assert hr.expect_home(0, -1500, 800, p).result == "HOME_NOT_FOUND"
    p = hr.HomeParams()
    assert hr.expect_home(0, -1500, 800, p, end_forced_in_seek=True).result == "HOME_WIRING"
    assert hr.expect_home(-2000, -1500, 800, p, start_stuck=True).result == "HOME_WIRING"
    e = hr.expect_home(-2000, -1500, 800, p)
    assert e.phases[:3] == ["PRECHECK", "RELEASE", "FAST_SEEK"]
    assert hr.HOME_RELEASE_MAX_UM == 10000 and hr.slow_approach_bound_um(p) == 12000
    assert hr.load_precheck(322124, 0, 322123, False) == "E_CONFIRM"
    assert hr.load_precheck(322123, 0, 322123, False) == "OK"
    assert hr.load_precheck(400000, 0, 322123, True) == "OK"
    assert hr.phase_order_ok(["FAST_SEEK", "SLOW_APPROACH", "DONE"], e.phases)
    assert not hr.phase_order_ok(["SLOW_APPROACH", "FAST_SEEK", "DONE"], e.phases)


# --------------------------------------------------------------------------- latch oracles
def test_estop_clear_rule():
    assert lr.estop_clear(True, 0, 100) == ("E_CAUSE_ACTIVE", 0xFFFF)
    assert lr.estop_clear(False, 50, 100) == ("E_CAUSE_ACTIVE", 50)
    assert lr.estop_clear(False, 99, 100) == ("E_CAUSE_ACTIVE", 1)
    assert lr.estop_clear(False, 100, 100) == ("OK", None)


def test_limit_latch_rule_d33h():
    assert lr.limit_latch_cleared(19, 20) is False
    assert lr.limit_latch_cleared(20.5, 20) is None
    assert lr.limit_latch_cleared(21, 20) is True
    assert not lr.limit_motion_allowed("START", set(), -1) and lr.limit_motion_allowed("START", set(), +1)
    assert not lr.limit_motion_allowed(None, {"END"}, +1) and lr.limit_motion_allowed(None, {"END"}, -1)


def test_drv_pwr_filter():
    for dur, ignored in ((5, True), (19, True), (21, False), (40, False)):
        lv = [1] * 10 + [0] * dur + [1] * 60
        f = lr.drv_pwr_filtered(lv, 1)
        assert (min(f) == 1) == ignored, dur
    lv = [1] * 10 + [0] * 100
    f = lr.drv_pwr_filtered(lv, 1)
    t_loss = f.index(0)
    assert t_loss - 10 == lr.DRV_PWR_FILTER_MS - 1 + 1 or t_loss - 10 <= lr.DRV_PWR_REACTION_MS
    assert lr.toggle_ignored(19) is True and lr.toggle_ignored(21) is False and lr.toggle_ignored(20) is None


def test_k1_window():
    assert lr.k1_weld_window(200) == (200.0, 225.0)
    assert lr.k1_cause_present(True, True, True) and not lr.k1_cause_present(True, True, False)


def test_load_limit_rules():
    assert lr.first_trip([0, 7022271, 7022271]) is None                       # threshold itself: no trip
    assert lr.first_trip([0, 7022272]) == 1
    assert lr.first_trip([0, -7022272]) == 1
    assert lr.first_trip([0, 7022272, 0, 7022272, 0], trip_samples=2) is None  # alternating
    assert lr.first_trip([0, 7022272, 7022300], trip_samples=2) == 2           # 2nd consecutive
    assert lr.first_trip([0, lr.RAIL_HI], raw_min=-7151121, raw_max=7151121) == 1   # SAF-FW-009
    assert lr.first_trip([0, lr.RAIL_LO], raw_min=-7151121, raw_max=7151121) == 1
    ll = lr.LoadLimit()
    assert ll.feed(0, 7100000)
    ll.fault_clear(7100000)                      # SAF-FW-011: clearable while beyond, regrow rule
    assert not ll.feed(1, 7100000 + 128849)      # grows by exactly regrow: no re-trip
    assert ll.feed(2, 7100000 + 128850)          # grows by more: re-trip
    ll = lr.LoadLimit()
    ll.feed(0, 7100000)
    ll.fault_clear(7100000)
    assert not ll.feed(1, 6000000)               # unloaded into the band ...
    assert ll.feed(2, 7022272)                   # ... normal threshold again (ASSUMED window end)


def test_loadlim_vectors_vs_independent_oracle():
    """ICD v0.6 loadlim_vectors.json (D-40 d) replayed by the validator's LoadLimit: every trip / window flag."""
    f = VEC / "loadlim_vectors.json"
    if not f.exists():
        pytest.skip("loadlim_vectors.json not published")
    lv = json.loads(f.read_text(encoding="utf-8"))
    n = 0
    for c in lv["cases"]:
        got = lr.run_loadlim_case(c)
        for k, (st, (trip, win)) in enumerate(zip(c["steps"], got)):
            if st["op"] == "sample":
                assert trip == st["trip"], (c["name"], k, st, trip)
            assert win == st["regrow_window"], (c["name"], k, st, win)
        n += 1
    assert n == len(lv["cases"]) >= 12


def test_h5_rule():
    assert lr.timeout_rule_ok(250, 10) and lr.timeout_rule_ok(200, 10) and not lr.timeout_rule_ok(199, 10)
    assert lr.timeout_rule_ok(25, 80) and not lr.timeout_rule_ok(24, 80)
    assert math.isclose(lr.stale_stop_window(1000, 250)[1], 1251)
