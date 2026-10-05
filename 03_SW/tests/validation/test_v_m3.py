"""Level C — M3 SW application against the lock-step simulator (SW_test_plan §3.2, §3.4, §3.5, §3.7, §3.9, §3.10).

Stimuli through the public API (``harness`` M3 verbs) and the simulator vocabulary (``weight``, ``afe``, ``specimen``,
``estop``); expected values from the SRS acceptance criteria, ``params.yaml`` and F's oracle ``f_ref`` (fit, thresholds,
force), never from the production function under test. The simulated cell: 3285 counts/N, offset 50 000 counts,
noise 45 counts, 80 SPS × 1.005.

Verifies: SAF-SW-001, SAF-SW-002, SAF-SW-004, SAF-SW-005, SAF-SW-006, SW-LIM-001, SW-LIM-002, SW-LIM-003, SW-LIM-004,
SW-META-001, SW-META-002, SW-ACQ-002, SW-ACQ-003, SW-ACQ-004, SW-CAL-001, SW-CAL-002, SW-CAL-004, SW-CAL-005,
SW-CAL-006, SW-CAL-007, SW-CAL-008, SW-CAL-009, SW-TARE-001, SW-TARE-002, SW-TARE-003, SW-RT-004, SW-RT-005, SW-MAN-006
"""
from __future__ import annotations

import json
import math
import os

import pytest

import harness as H
import ref_codec as rc
from oracle import f_ref

MS = 1_000_000
CPN = 3285.0                                  # simulated cell counts per N
OFFSET = 50_000


def _ev(be, since, code):
    return H.events(be, since, code)


def _stopped(be, since):
    return [w for w in _ev(be, since, "STOPPED")]


# ============================================================================================ tare

@pytest.mark.req("SW-TARE-002", "SW-TARE-001", "SAF-SW-002")
def test_tc_sw_tare_002_02_tare_result_and_session_only(lockstep, tmp_path):
    """TC-SW-TARE-002-02: stream off → tare (10 s window): the stream is started and restored; tare_raw within
    3σ/√N of the cell offset (50 000 counts); N = round(10 s × measured rate) ± 1; a new Backend has no tare and no tare
    file is written anywhere in the data folder (session-only, D-29 j)."""
    # Verifies: SW-TARE-002, SW-TARE-001, SAF-SW-002
    be = lockstep()
    H.result(be, H.stream(be, False))
    H.advance(be, 100)
    st = H.tare(be)
    assert st.phase == "DONE", st
    r = st.result
    assert abs(r.n_used + r.n_rejected - round(10.0 * 80 * 1.005)) <= 1
    assert abs(r.tare_raw - OFFSET) <= 3 * 45 / math.sqrt(r.n_used) + 1.0, r
    H.advance(be, 300)
    assert not H.status(be).stream.on                          # restored
    assert H.status(be).tare.state == "ACTIVE" and H.status(be).tare.tare_raw == pytest.approx(r.tare_raw)
    data = os.environ["BEND_STAND_DATA_DIR"]
    files = [f for _, _, fs in os.walk(data) for f in fs]
    assert not [f for f in files if "tare" in f.lower()], files
    be2 = lockstep()
    assert be2.status().tare.state == "NONE" and be2.status().tare.tare_raw is None


@pytest.mark.req("SW-TARE-003")
@pytest.mark.parametrize("case", ["moving", "after_move", "halt", "saturated", "drift"])
def test_tc_sw_tare_003_01_refusals(lockstep, case):
    """TC-SW-TARE-003-01 (one case each): moving → refused; < 1 s after a move (device time) → refused; HALT latched
    → refused; a saturated sample in the window → refused after the capture; drift beyond the rule → refused."""
    # Verifies: SW-TARE-003
    be = lockstep()
    if case in ("moving", "after_move"):
        H.forced_enable_home(be)
        assert H.wait_idle(be)
        H.advance(be, 1200)
        m0 = H.wire_mark(be)
        H.result(be, H.forced_move_abs(be, 20_000 if case == "moving" else 2_000, 10_000))
        if case == "after_move":
            assert H.until(be, lambda: bool(_ev(be, m0, "MOVE_DONE")), 5000)
            H.advance(be, 300)
        else:
            H.advance(be, 300)
        g = be.tare()
        assert not g.ok, g
        codes = {str(i.code) for i in g.items}
        assert codes & ({"MOTION_ACTIVE"} if case == "moving" else {"MOVED_RECENTLY"}), codes
        if case == "after_move":
            H.advance(be, 1000)
            assert be.tare().ok                                  # ≥ 1 s after the move: accepted
        return
    if case == "halt":
        H.halt(be)
        H.advance(be, 200)
        g = be.tare()
        assert not g.ok and "HALT" in {str(i.code) for i in g.items}
        return
    if case == "saturated":
        H.act(be, "on_frame", cmd="STREAM_START", delay_us=0, then={"action": "afe", "saturate": "pos"}) \
            if False else None
        g = be.tare()
        assert g.ok
        H.advance(be, 3000)
        H.act(be, "afe", saturate="pos")
        H.advance(be, 30)
        H.act(be, "afe", saturate=None)
    else:
        be.sim.set_afe_drift(2000.0)                             # 2000 counts/min ≫ the drift rule
        g = be.tare()
        assert g.ok
    st = H.wait_engine(be, be.tare_engine, lambda s: s.phase in ("DONE", "REFUSED", "ABORTED"))
    # a rail sample also trips the FW load limit (FAULT_SET LOAD_LIMIT) → the tare is aborted by that fault (SW-STOP-003
    # terminate rule) before the SW saturation rule: either outcome is a refusal with a reason
    assert (st.phase == "REFUSED" and st.errors) or (st.phase == "ABORTED" and st.abort_reason), st
    assert H.status(be).tare.state != "ACTIVE"


@pytest.mark.req("SW-TARE-003")
def test_tc_sw_tare_003_01_large_offset_warning(lockstep):
    """TC-SW-TARE-003-01 offset case: a tare with a load > 10 % FS on the cell (calibrated) is accepted with the warning
    'large offset — specimen loaded?'."""
    # Verifies: SW-TARE-003
    be = lockstep()
    H.load_calibration(be, (1.0, 10.0))
    H.act(be, "weight", n=250.0)                               # > 10 % FS (196 N)
    H.advance(be, 2000)
    st = H.tare(be)
    assert st.phase == "DONE" and any("offset" in w.lower() for w in st.warnings), st


# ============================================================================================ load calibration

@pytest.mark.req("SW-CAL-005", "SW-CAL-006", "SW-CAL-007", "SW-CAL-008", "SW-CAL-009", "SAF-SW-002")
def test_tc_sw_cal_005_01_load_wizard_1kg_10kg(lockstep):
    """TC-SW-CAL-005-01 / -007 / -008 / -009 / TC-SAF-SW-002-02: zero + 1 kg + 10 kg with the simulator's weights:
    each point N = round(10 s × rate) ± 1 after a pre-settle; the fit equals F's OLS on the point raw means (K, status
    PASS); K ≈ 1/3285 N/count (simulated cell); LOW_SPAN set; accept writes ``active_load.json`` + the history file
    (schema with points / fit / afe); after a tare the FW thresholds = ``f_ref.fw_raw_limits(±fw_level, K, tare)``
    (+ zero_raw = rha(tare)), written and read back (VERIFIED with this calibration and tare)."""
    # Verifies: SW-CAL-005, SW-CAL-006, SW-CAL-007, SW-CAL-008, SW-CAL-009, SAF-SW-002
    be = lockstep()
    fit, tare_st = H.calibrate_and_tare(be, (1.0, 10.0))
    r = fit.result
    raws = [p[2] for p in r.points]
    o = f_ref.load_calibration(raws, [p[0] for p in r.points])
    assert r.status == o.status == "PASS"
    assert r.K == pytest.approx(o.K, rel=1e-12) and r.K == pytest.approx(1 / CPN, rel=2e-3)
    assert r.low_span is True
    cal = H.status(be).calibration
    assert cal.load_k == pytest.approx(r.K) and cal.load_valid_for_limits and cal.f_cal_max_n == pytest.approx(98.0665)
    data = os.environ["BEND_STAND_DATA_DIR"]
    act = [os.path.join(d, f) for d, _, fs in os.walk(data) for f in fs if f == "active_load.json"]
    assert act
    doc = json.load(open(act[0], encoding="utf-8"))
    assert "points" in json.dumps(doc) and "afe" in json.dumps(doc)
    t = tare_st.result
    lim = be.limits.get()
    lo, hi = f_ref.fw_raw_limits(lim.fw_level_n, -lim.fw_level_n, r.K, t.tare_raw)
    th = H.safety(be).thresholds
    assert th.state == "VERIFIED" and th.tare_id == t.tare_id, th
    assert (th.raw_min, th.raw_max, th.zero_raw) == (lo, hi, f_ref.rha(t.tare_raw)), (th, lo, hi)
    vals = H.config_values(be)
    assert (vals["safety.load_raw_min"], vals["safety.load_raw_max"]) == (lo, hi)


@pytest.mark.req("SW-CAL-005")
def test_tc_sw_cal_005_02_point_sample_count(lockstep):
    """Each capture uses N = round(capture × measured rate) ± 1 samples after the pre-settle (2 s excluded): with 80 SPS
    × 1.005 → 804 ± 1 for 10 s (SWD-P1-10 interpretation of '800 ± 1 nominal')."""
    # Verifies: SW-CAL-005
    be = lockstep()
    lc = be.load_cal
    assert lc.start(confirmed=True).ok
    H.advance(be, 500)
    lc.continue_()
    st = H.wait_engine(be, lc, lambda s: s.phase == "AWAIT_OPERATOR" and s.step_index == 1)
    assert abs(st.stats["n"] - 804) <= 1, st.stats
    lc.cancel()


@pytest.mark.req("SW-CAL-006")
@pytest.mark.parametrize("case", ["outliers", "saturated", "decreasing"])
def test_tc_sw_cal_006_02_point_rejections(lockstep, case):
    """TC-SW-CAL-006-02: (outliers) 20 spikes of +10 000 counts in an 804-sample capture (2.5 % > 2 %) → the zero point
    is REJECTED with the reason, Repeat offered, the wizard stays at that point; (saturated) a rail sample trips the FW
    load limit → the wizard is aborted by FAULT_SET with the reason, the active calibration unchanged; (decreasing) a
    mass not larger than the previous one → refused before the capture."""
    # Verifies: SW-CAL-006
    be = lockstep()
    lc = be.load_cal
    assert lc.start(confirmed=True).ok
    if case == "outliers":
        lc.continue_()
        H.advance(be, 4000)                                       # pre-settle 2 s, then capture
        H.act(be, "afe", raw_script=[OFFSET + 10_000] * 20)
        st = H.wait_engine(be, lc, lambda s: (s.phase == "AWAIT_OPERATOR" and (s.errors or s.step_index == 1))
                           or s.phase == "ABORTED")
        assert st.phase == "AWAIT_OPERATOR" and st.step_index == 0 and st.errors and st.can_repeat, st
        assert any("outlier" in e.lower() for e in st.errors), st.errors
    elif case == "saturated":
        cal0 = H.status(be).calibration.load_k
        lc.continue_()
        H.advance(be, 4000)
        H.act(be, "afe", saturate="pos")
        H.advance(be, 30)
        H.act(be, "afe", saturate=None)
        st = H.wait_engine(be, lc, lambda s: s.phase == "ABORTED" or (s.phase == "AWAIT_OPERATOR" and
                                                                     (s.errors or s.step_index == 1)))
        assert (st.phase == "ABORTED" and st.abort_reason) or (st.step_index == 0 and st.errors), st
        assert H.status(be).calibration.load_k == cal0
    else:
        lc.continue_()
        H.wait_engine(be, lc, lambda s: s.phase == "AWAIT_OPERATOR" and s.step_index == 1)
        H.weight(be, 10.0)
        H.advance(be, 2000)
        lc.continue_({"mass_kg": 10.0})
        H.wait_engine(be, lc, lambda s: s.phase == "AWAIT_OPERATOR" and s.step_index == 2)
        lc.continue_({"mass_kg": 1.0})
        st = lc.state()
        assert st.step_index == 2 and st.errors, st
    if lc.state().phase not in ("ABORTED", "DONE"):
        lc.cancel()


@pytest.mark.req("SW-CAL-008", "SW-RT-005")
def test_tc_sw_cal_008_02_extrapolated_marking(lockstep):
    """TC-SW-CAL-008-01 (C part): with the 1 kg + 10 kg calibration a force of 300 N is EXTRAPOLATED (> 3 × 98.07 N),
    290 N is OK; F_N = f_ref.force_n(raw, K, tare) within the noise."""
    # Verifies: SW-CAL-008, SW-RT-005
    be = lockstep()
    fit, t = H.calibrate_and_tare(be)
    for n, state in ((290.0, "OK"), (300.0, "EXTRAPOLATED")):
        H.act(be, "weight", n=n)
        H.advance(be, 2500)
        ls = be.data.latest("F_N")
        assert ls.state == state and ls.value == pytest.approx(n, abs=0.2), (n, ls)


@pytest.mark.req("SW-CAL-009", "SAF-SW-001")
def test_tc_sw_cal_009_02_afe_change_invalidates_for_limits(lockstep):
    """TC-SW-CAL-009-02: changing ``afe.gain_channel`` on the board after the calibration → 'calibration invalid'
    reason; load input invalid → the motion gates REFUSE LOAD_INPUT_INVALID (no-specimen mode off)."""
    # Verifies: SW-CAL-009, SAF-SW-001
    be = lockstep()
    H.calibrate_and_tare(be)
    assert H.safety(be).load_input_valid
    other = 1 if H.config_values(be)["afe.gain_channel"] == 0 else 0
    H.result(be, H.write_verify(be, {"afe.gain_channel": other}))
    H.advance(be, 1500)
    cal = H.status(be).calibration
    assert not cal.load_valid_for_limits and cal.load_invalid_reason, cal
    assert not H.safety(be).load_input_valid
    g = H.gate(be, "MOVE")
    assert "LOAD_INPUT_INVALID" in {str(i.code) for i in g.items}, g


# ============================================================================================ safety (SAF-SW-001/002)

@pytest.mark.req("SAF-SW-001", "SW-LIM-004")
def test_tc_saf_sw_001_02_no_calibration_motion_refused(lockstep):
    """TC-SAF-SW-001-02: no calibration / tare, load limits enabled, no-specimen mode off → `move` gate REFUSE
    LOAD_INPUT_INVALID with the reason; ``move_to`` raises and no MOVE_ABS reaches the wire."""
    # Verifies: SAF-SW-001, SW-LIM-004
    be = lockstep()
    H.enable(be)
    H.advance(be, 800)
    g = H.gate(be, "MOVE")
    items = {str(i.code): i.text for i in g.items}
    assert "LOAD_INPUT_INVALID" in items and "calibration" in items["LOAD_INPUT_INVALID"], items
    m0 = H.wire_mark(be)
    with pytest.raises(Exception):  # noqa: B017
        H.result(be, H.move_to(be, 10.0), 2000)
    assert not H.tx(be, "MOVE_ABS", since=m0)


@pytest.mark.req("SAF-SW-001")
def test_tc_saf_sw_001_02_load_input_lost_while_moving_stops(lockstep):
    """Rule 2 of SAF-SW-001: calibrated + tared, moving, then the AFE stalls → STOP on the wire and the move ends
    (load input invalid while moving)."""
    # Verifies: SAF-SW-001
    be = lockstep()
    H.calibrate_and_tare(be)
    H.m2_ready(be)
    assert be.limits.set_no_specimen_mode(False).ok
    H.move_to(be, 100.0, speed_mm_s=5.0)
    H.advance(be, 500)
    assert H.status(be).motion.moving
    m0 = H.wire_mark(be)
    H.act(be, "afe", stall=True)
    H.advance(be, 800)
    assert H.tx(be, "STOP", since=m0) or _stopped(be, m0), "no stop after the load input became invalid"
    H.advance(be, 500)
    assert not H.status(be).motion.moving
    H.act(be, "afe", stall=False)


@pytest.mark.req("SAF-SW-001", "SAF-SW-005")
def test_tc_saf_sw_001_01_pull_trip_stops_within_50ms(lockstep):
    """TC-SAF-SW-001-01 (lock-step part; the 100-trial rt statistic is PR-5 on the REF PC): calibrated + tared,
    spring specimen 50 N/mm at x = 20 mm, pull trip 150 N; moving into the spring → STOP mode 0 written ≤ 50 ms after
    the receipt of the first DATA frame whose F > 150 N (F from F's oracle on the wire raw); ``safety.trip`` = PULL
    with the value; indicator ``sw_trip`` ON with a clear hint; a move that increases tension is refused (SW_TRIP),
    one that reduces it is allowed."""
    # Verifies: SAF-SW-001, SAF-SW-005
    be = lockstep()
    fit, t = H.calibrate_and_tare(be)
    k, tare_raw = fit.result.K, t.result.tare_raw
    assert not H.set_limits(be, pull_trip_n=150.0)
    H.m2_ready(be)
    assert be.limits.set_no_specimen_mode(False).ok and H.safety(be).load_input_valid
    H.act(be, "specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=20_000)
    m0 = H.wire_mark(be)
    H.move_to(be, 40.0, speed_mm_s=5.0)
    assert H.until(be, lambda: H.safety(be).sw_trip is not None, 20_000)
    H.advance(be, 200)
    over = [w for w in H.rx(be, "DATA", since=m0) if f_ref.force_n(w.fields["afe_raw"], k, tare_raw) > 150.0]
    stops = H.tx(be, "STOP", since=m0)
    assert over and stops
    dt = (stops[0].t_ns - over[0].t_ns) / MS
    assert 0 <= dt <= 50, dt
    assert H.safety(be).sw_trip == "PULL"
    trip = [r.payload for r in H.history(be, "safety.trip")]
    assert trip and trip[-1].limit == "PULL" and trip[-1].value > 150.0
    ind = H.indicator(be, "sw_trip")
    assert ind.state == "ON" and ind.clear_hint
    H.advance(be, 500)
    up = H.motion_check(be, "MOVE", target_mm=45.0)
    assert "SW_TRIP" in {str(i.code) for i in up.items} and not up.ok, up
    down = H.motion_check(be, "MOVE", target_mm=10.0)
    assert down.ok, down


@pytest.mark.req("SAF-SW-002")
def test_tc_saf_sw_002_03_store_mismatch_fails_and_recheck(lockstep):
    """TC-SAF-SW-002-03: an injected store mismatch on the calibrated threshold rewrite → FAILED, motion refused
    (THRESHOLDS_UNVERIFIED); ``recheck_async`` after the injection is gone → VERIFIED."""
    # Verifies: SAF-SW-002
    be = lockstep()
    H.load_calibration(be)
    H.inject_store_mismatch(be, "safety.load_raw_max", 1234)
    st = H.tare(be)
    assert st.phase == "DONE"
    H.advance(be, 1500)
    assert H.safety(be).thresholds.state == "FAILED", H.safety(be).thresholds
    assert "THRESHOLDS_UNVERIFIED" in {str(i.code) for i in H.gate(be, "MOVE").items}
    st2 = H.result(be, be.limits.recheck_async())
    H.advance(be, 300)
    assert st2.state == "VERIFIED" or H.safety(be).thresholds.state == "VERIFIED", st2


@pytest.mark.req("SAF-SW-002")
def test_tc_saf_sw_002_04_clamp_scenario(lockstep, tmp_path):
    """TC-SAF-SW-002-04 (clamp scenario, offset 125 000 counts = 1.94 % FS): calibration + tare with the FW level
    110 % FS → raw_max computed beyond the dictionary maximum → clamped inward to 7 151 121, warning FW_CLAMPED,
    motion allowed (D-29 g)."""
    # Verifies: SAF-SW-002
    sc = {"schema": "bird.bend.simscenario", "version": 1, "world": {"load_offset_counts": 125_000},
          "params": {"motion.steps_per_mm": 800.0}}
    p = tmp_path / "clamp.simscn.json"
    p.write_text(json.dumps(sc), encoding="utf-8")
    be = lockstep(endpoint=f"sim:{p}")
    fit, t = H.calibrate_and_tare(be)
    th = H.safety(be).thresholds
    lo, hi = f_ref.fw_raw_limits(be.limits.get().fw_level_n, -be.limits.get().fw_level_n, fit.result.K,
                                 t.result.tare_raw)
    assert hi > 7_151_121 and th.raw_max == 7_151_121 and th.clamped and th.raw_min == lo, (th, lo, hi)
    assert th.state == "VERIFIED"
    assert "FW_CLAMPED" in H.safety(be).warnings


# ============================================================================================ limits / no-specimen

@pytest.mark.req("SW-LIM-002", "SW-LIM-001")
@pytest.mark.parametrize("changes", [
    {"fw_level_n": 2160.0},                                   # > 110 % FS
    {"fw_level_n": 1500.0},                                   # below the enabled pull trip 1961.33 N
    {"pull_trip_n": 2200.0},                                  # > 110 % FS
    {"warn_pct": 40.0},
    {"travel_min_mm": 200.0, "travel_min_enabled": True, "travel_max_mm": 100.0, "travel_max_enabled": True},
    {"travel_max_mm": 400.0, "travel_max_enabled": True},     # beyond the FW soft limit 290 mm
], ids=["fw_above_110", "fw_below_trip", "pull_above_110", "warn_pct", "travel_min_ge_max", "travel_beyond_soft"])
def test_tc_sw_lim_002_01_limit_rules_refused(vbe, changes):
    """TC-SW-LIM-002-01 / SW-LIM-001: each invalid limit configuration is refused at edit (ERROR issue) and nothing
    changes (`limits.get()` and the FW thresholds unchanged, no SET_PARAM on the wire)."""
    # Verifies: SW-LIM-002, SW-LIM-001
    before = vbe.limits.get()
    m0 = H.wire_mark(vbe)
    issues = H.set_limits(vbe, **changes)
    assert issues and all(str(i.severity) == "ERROR" for i in issues), issues
    H.advance(vbe, 300)
    assert vbe.limits.get() == before and not H.tx(vbe, "SET_PARAM", since=m0)


@pytest.mark.req("SW-LIM-004", "SAF-SW-004", "SAF-SW-002", "SAF-SW-005")
def test_tc_sw_lim_004_01_no_specimen_mode(lockstep):
    """TC-SW-LIM-004-01: without calibration motion is refused; entering the mode without the confirmation changes
    nothing (CONFIRMATION_REQUIRED); confirmed → mode ON, indicator + WARN item, motion allowed, FW thresholds = the
    nominal defaults (±7 022 271, zero 0, DEFAULT_ONLY, read back); the mode ends at disconnect and is not in the saved
    session; a new Backend starts with it off."""
    # Verifies: SW-LIM-004, SAF-SW-004, SAF-SW-002, SAF-SW-005
    be = lockstep()
    g = be.limits.set_no_specimen_mode(True)
    assert not g.ok and "CONFIRMATION_REQUIRED" in {str(i.code) for i in g.items} and not H.safety(be).no_specimen_mode
    assert be.limits.set_no_specimen_mode(True, confirmed=True).ok
    H.advance(be, 300)
    assert H.safety(be).no_specimen_mode and H.indicator(be, "no_specimen_mode").state == "ON"
    assert "NO_SPECIMEN_MODE" in {str(i.code) for i in H.gate(be, "MOVE").items}
    th = H.safety(be).thresholds
    assert (th.raw_min, th.raw_max, th.zero_raw, th.state) == (-7_022_271, 7_022_271, 0, "DEFAULT_ONLY")
    H.result(be, H.disconnect(be))
    H.advance(be, 200)
    assert not H.safety(be).no_specimen_mode
    data = os.environ["BEND_STAND_DATA_DIR"]
    for d, _, fs in os.walk(data):
        for f in fs:
            if f.endswith(".bbsession.json"):
                assert "no_specimen" not in open(os.path.join(d, f), encoding="utf-8").read()
    be2 = lockstep()
    assert not H.safety(be2).no_specimen_mode


@pytest.mark.req("SW-LIM-004", "SAF-SW-002")
def test_tc_sw_lim_004_02_no_specimen_keeps_calibrated_thresholds(lockstep):
    """TC-SW-LIM-004-02: with a valid calibration + tare the no-specimen mode keeps the **calibrated** FW thresholds."""
    # Verifies: SW-LIM-004, SAF-SW-002
    be = lockstep()
    H.calibrate_and_tare(be)
    th0 = H.safety(be).thresholds
    assert be.limits.set_no_specimen_mode(True, confirmed=True).ok
    H.advance(be, 1500)
    th = H.safety(be).thresholds
    assert (th.raw_min, th.raw_max, th.cal_id, th.state) == (th0.raw_min, th0.raw_max, th0.cal_id, "VERIFIED")


@pytest.mark.req("SW-LIM-003", "SW-META-002")
def test_tc_sw_lim_003_01_session_round_trip_and_in_recording(lockstep, tmp_path):
    """TC-SW-LIM-003-01: limits saved in a session file and loaded into a new Backend are identical and applied; the
    recording's meta.json carries the limits (snapshot)."""
    # Verifies: SW-LIM-003, SW-META-002
    be = lockstep()
    assert not H.set_limits(be, pull_trip_n=800.0, push_trip_n=-600.0, warn_pct=85.0, travel_max_mm=200.0,
                            travel_max_enabled=True)
    path = tmp_path / "s.bbsession.json"
    be.session.save(str(path))
    lim = be.limits.get()
    be2 = lockstep()
    be2.session.load(str(path))
    assert be2.limits.get() == lim
    assert be2.record_start().ok
    H.advance(be2, 1000)
    be2.record_stop()
    H.advance(be2, 300)
    m = H.meta(H.recording_folder(be2))
    s = json.dumps(m["snapshot"]["limits"])
    assert "800" in s and "200" in s and "85" in s, s


# ============================================================================================ marks / recording

@pytest.mark.req("SW-META-001", "SW-META-002", "SW-ACQ-002")
def test_tc_sw_meta_002_01_marks_presets_and_edit_rows(lockstep, tmp_path):
    """TC-SW-META-001-01 / -002-01: marks with custom fields (add / rename / remove = a new tuple) are in meta.json;
    a preset saved and loaded is identical; an edit during the recording writes a MARK_EDIT event row and
    ``marks_final``; duplicate / empty custom keys are refused."""
    # Verifies: SW-META-001, SW-META-002, SW-ACQ-002
    from dataclasses import replace

    from bend_stand.core.api import TestMarks

    be = lockstep()
    m = TestMarks(specimen="S-1", number="7", custom=(("batch", "B42"), ("lab", "L1")))
    be.marks.set(m)
    with pytest.raises(ValueError):
        be.marks.set(replace(m, custom=(("a", "1"), ("a", "2"))))
    with pytest.raises(ValueError):
        be.marks.set(replace(m, custom=(("", "1"),)))
    preset = tmp_path / "p.bbmarks.json"
    be.marks.save_preset(str(preset))
    be.marks.set(TestMarks())
    be.marks.load_preset(str(preset))
    assert be.marks.get() == m
    assert be.record_start().ok
    H.advance(be, 500)
    be.marks.set(replace(m, custom=(("batch", "B43"),)))           # rename value + remove 'lab'
    H.advance(be, 500)
    be.record_stop()
    H.advance(be, 300)
    folder = H.recording_folder(be)
    meta = H.meta(folder)
    assert "B42" in json.dumps(meta["marks_at_start"]) and "B43" in json.dumps(meta["marks_final"])
    hdr, rows = H.data_rows(folder)
    ev = [r[hdr.index("event")] for r in rows if r[0] == "E"]
    assert any("MARK_EDIT" in e for e in ev), ev


@pytest.mark.req("SW-ACQ-002", "SW-RT-004", "SW-TARE-001")
def test_tc_sw_acq_002_03_recording_scaled_columns_and_event_rows(lockstep):
    """TC-SW-ACQ-002 / TC-SW-RT-004-02 (C part): calibrated recording — every D row's F_N = f_ref.force_n(raw, K,
    tare) (rel 1e-6), F_kgf = F_N / g; a TARE event row; the sidecar has calibration, tare, thresholds, versions."""
    # Verifies: SW-ACQ-002, SW-RT-004, SW-TARE-001
    be = lockstep()
    fit, _t = H.calibrate_and_tare(be)
    k = fit.result.K
    H.act(be, "weight", kg=5.0)
    assert be.record_start().ok
    H.advance(be, 2000)
    st = H.tare(be)
    tare2 = st.result.tare_raw
    H.advance(be, 1500)
    be.record_stop()
    H.advance(be, 300)
    folder = H.recording_folder(be)
    hdr, rows = H.data_rows(folder)
    ix = {n: i for i, n in enumerate(hdr)}
    d_after = [r for r in rows if r[0] == "D" and float(r[ix["t_dev_s"]]) > 0][-50:]
    for r in d_after:
        f = float(r[ix["F_N"]])
        assert f == pytest.approx(f_ref.force_n(float(r[ix["raw"]]), k, tare2), rel=1e-6, abs=1e-6)
        assert float(r[ix["F_kgf"]]) == pytest.approx(f / 9.80665, rel=1e-6, abs=1e-6)
    ev = [r[ix["event"]] for r in rows if r[0] == "E"]
    assert any(e.startswith("TARE") for e in ev), ev
    meta = H.meta(folder)
    snap = meta["snapshot"]
    assert snap["calibration"] and snap["tare"] and meta["thresholds"] and meta["sw_version"]


@pytest.mark.req("SW-ACQ-003")
def test_tc_sw_acq_003_02_take_sample(lockstep):
    """TC-SW-ACQ-003-01/-02: take sample (1 s) with 5 kg on the calibrated cell → f_mean = 49.03 N within 3σ/√N, N =
    round(1 s × rate) ± 1; a window of 0.09 s or 10.01 s is refused; the row goes to the daily samples file when not
    recording."""
    # Verifies: SW-ACQ-003
    be = lockstep()
    H.calibrate_and_tare(be)
    H.act(be, "weight", kg=5.0)
    H.advance(be, 2500)
    assert not be.take_sample(0.09).ok and not be.take_sample(10.01).ok
    assert be.take_sample(1.0).ok
    H.advance(be, 1500)
    s = [r.payload for r in H.history(be, "sample.taken")][-1]
    assert abs(s.n - round(80 * 1.005)) <= 1, s
    sigma_n = 45 / CPN
    assert s.f_mean == pytest.approx(5 * 9.80665, abs=3 * sigma_n / math.sqrt(s.n) + 0.05), s
    assert os.path.exists(s.file) and "samples_" in os.path.basename(s.file)


@pytest.mark.req("SW-ACQ-004")
def test_tc_sw_acq_004_04_free_space_refusal(lockstep):
    """TC-SW-ACQ-004-04: free-space probe stubbed below the limit → ``record_start`` refused with the reason."""
    # Verifies: SW-ACQ-004
    be = lockstep()
    be.test_hooks.set_free_space(1024)
    g = be.record_start()
    assert not g.ok and g.items, g


# ============================================================================================ travel calibration

def _travel_until(be, pred, ms=120_000):
    return H.wait_engine(be, be.travel_cal, pred, ms)


@pytest.mark.req("SW-CAL-001", "SW-CAL-002", "SW-CAL-004", "IF-009")
def test_tc_sw_cal_002_02_travel_wizard_measures_true_mechanics(lockstep):
    """TC-SW-CAL-002-02 / -004-01: board steps/mm 796 (session SET) on a mechanism of 800 steps/mm (simulator world):
    +2 mm backlash first, all moves absolute and in +; D1 / D_tot entered = the true distances from the world → result
    = 800 (rel 1e-6), N1 / N2 = the oracle's; accept → SET + read-back + SAVE_PARAMS, ``active_travel.json`` agrees."""
    # Verifies: SW-CAL-001, SW-CAL-002, SW-CAL-004, IF-009
    be = lockstep()
    H.m2_ready(be)
    assert H.result(be, H.write_verify(be, {"motion.steps_per_mm": 796.0})).ok
    H.advance(be, 300)
    tc = be.travel_cal
    assert tc.start(confirmed=True).ok
    xt = lambda: H.sim_query(be, "world")["x_um_true"]  # noqa: E731
    m0 = H.wire_mark(be)
    tc.continue_()
    _travel_until(be, lambda s: s.phase == "REFERENCE" and s.can_continue)
    x0 = xt()
    tc.continue_()
    _travel_until(be, lambda s: s.phase == "ENTER_D1")
    d1 = round((xt() - x0) / 1000.0, 3)
    tc.continue_({"d1_mm": d1})
    st = _travel_until(be, lambda s: s.phase == "MOVE2" or s.needs_confirmation is not None, 5000)
    tc.continue_()
    _travel_until(be, lambda s: s.phase == "ENTER_DTOT")
    dtot = round((xt() - x0) / 1000.0, 3)
    tc.continue_({"dtot_mm": dtot})
    st = _travel_until(be, lambda s: s.phase == "RESULT", 5000)
    r = st.result
    n1, spm1 = f_ref.travel_cal_step1(796.0, 10.0, d1)
    n2, spm2, _inc, _c = f_ref.travel_cal_step2(r.spm1, r.n1, 50.0, dtot, d1)
    assert (r.n1, r.n2) == (n1, n2) and r.spm2 == pytest.approx(spm2, rel=1e-9) and r.spm2 == pytest.approx(800.0,
                                                                                                          rel=1e-6)
    moves = [w.fields["target_um"] for w in H.tx(be, "MOVE_ABS", since=m0)]
    assert moves == sorted(moves) and len(moves) == 3                # backlash, 10 mm, 50 mm — all in +
    tc.continue_(confirmed=True)
    st = _travel_until(be, lambda s: s.phase in ("DONE", "ABORTED"), 10_000)
    assert st.phase == "DONE", st
    assert H.tx(be, "SAVE_PARAMS", since=m0)
    assert H.config_values(be)["motion.steps_per_mm"] == pytest.approx(r.spm2, rel=1e-6)
    assert H.status(be).calibration.travel_spm == pytest.approx(r.spm2, rel=1e-6)


@pytest.mark.req("SW-CAL-001")
@pytest.mark.parametrize("phase", ["REFERENCE", "ENTER_D1", "MOVE2", "RESULT"])
def test_tc_sw_cal_001_02_cancel_restores_spm0_no_save(lockstep, phase):
    """TC-SW-CAL-001-02: Cancel in each phase (incl. after the trial spm1 was written) → board steps/mm read back =
    spm0, **no SAVE_PARAMS**, ``active_travel.json`` not written."""
    # Verifies: SW-CAL-001
    be = lockstep()
    H.m2_ready(be)
    spm0 = H.config_values(be)["motion.steps_per_mm"]
    tc = be.travel_cal
    assert tc.start(confirmed=True).ok
    m0 = H.wire_mark(be)
    order = ["BACKLASH", "REFERENCE", "ENTER_D1", "MOVE2", "ENTER_DTOT", "RESULT"]
    xt = lambda: H.sim_query(be, "world")["x_um_true"]  # noqa: E731
    x0 = None
    while tc.state().phase != phase:
        ph = tc.state().phase
        if ph == "REFERENCE":
            x0 = xt()
        if ph == "ENTER_D1":
            tc.continue_({"d1_mm": round((xt() - x0) / 1000.0, 3)})
        elif ph == "ENTER_DTOT":
            tc.continue_({"dtot_mm": round((xt() - x0) / 1000.0, 3)})
        else:
            tc.continue_()
        nxt = order[order.index(ph) + 1]
        _travel_until(be, lambda s, nxt=nxt: (s.phase == nxt and (s.can_continue or s.inputs))
                      or s.phase == "ABORTED", 60_000)
    tc.cancel()
    st = _travel_until(be, lambda s: s.phase in ("ABORTED", "DONE"), 30_000)
    H.advance(be, 500)
    assert st.phase == "ABORTED"
    assert H.config_values(be)["motion.steps_per_mm"] == pytest.approx(spm0)
    assert not H.tx(be, "SAVE_PARAMS", since=m0)
    data = os.environ["BEND_STAND_DATA_DIR"]
    assert not [f for _, _, fs in os.walk(data) for f in fs if f == "active_travel.json"]


# ============================================================================================ SAF-SW-006, test zero

@pytest.mark.req("SAF-SW-006")
def test_tc_saf_sw_006_02_margin_warning_in_motion_check(lockstep):
    """TC-SAF-SW-006-02 (C part): ``motion.check`` with the session k_est 500 N/mm at 10 mm/s → WARN SAF_SW_006_MARGIN;
    k_est 50 N/mm → none."""
    # Verifies: SAF-SW-006
    from dataclasses import replace

    be = lockstep()
    H.calibrate_and_tare(be)
    H.m2_ready(be)
    assert be.limits.set_no_specimen_mode(False).ok                   # PC limits active → the margin rule applies
    for k_est, warn in ((500.0, True), (50.0, False)):
        be.session.set(replace(be.session.get(), k_est_n_mm=k_est))
        g = H.motion_check(be, "MOVE", speed_mm_s=10.0, target_mm=20.0)
        assert ("SAF_SW_006_MARGIN" in {str(i.code) for i in g.items}) == warn, (k_est, g)


@pytest.mark.req("SW-MAN-006", "SW-RT-004")
def test_tc_sw_man_006_03_test_zero(lockstep):
    """SW-MAN-006 / B5-16: set test zero at x = 12 mm → x_zero 12, ``x_test_mm`` = x − 12 in the data, X_ZERO event
    row in the recording; reset → 0."""
    # Verifies: SW-MAN-006, SW-RT-004
    be = lockstep()
    H.m2_ready(be)
    H.result(be, H.move_to(be, 12.0), 30_000)
    assert H.run_until(be, lambda: not H.motion(be).moving, 30_000)
    assert be.record_start().ok
    H.advance(be, 300)
    assert be.motion.set_test_zero() == pytest.approx(12.0, abs=1e-3)
    H.advance(be, 300)
    assert be.data.latest("x_test_mm").value == pytest.approx(0.0, abs=1e-3)
    be.motion.reset_test_zero()
    H.advance(be, 300)
    assert be.data.latest("x_test_mm").value == pytest.approx(12.0, abs=1e-3)
    be.record_stop()
    H.advance(be, 300)
    hdr, rows = H.data_rows(H.recording_folder(be))
    assert any("X_ZERO" in r[hdr.index("event")] for r in rows if r[0] == "E")


@pytest.mark.req("SAF-SW-001")
def test_tc_saf_sw_001_04_second_limit_supervised_while_a_trip_is_latched(lockstep):
    """TC-SAF-SW-001-04: after a PULL trip the operator may move to reduce the tension; that motion is still supervised
    against the **other** limits — an (unplanned, e.g. another client's) move beyond the enabled travel minimum is
    stopped before the limit (≤ v·75 ms overshoot), although the PULL latch is still set."""
    # Verifies: SAF-SW-001
    be = lockstep()
    H.calibrate_and_tare(be)
    assert not H.set_limits(be, pull_trip_n=150.0, travel_min_mm=15.0, travel_min_enabled=True)
    H.m2_ready(be)
    assert be.limits.set_no_specimen_mode(False).ok
    H.act(be, "specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=20_000)
    H.move_to(be, 40.0, speed_mm_s=5.0)
    assert H.until(be, lambda: H.safety(be).sw_trip == "PULL", 20_000)
    assert H.wait_idle(be)
    m0 = H.wire_mark(be)
    H.result(be, H.forced_move_abs(be, 5_000, 5_000))         # reduces tension, crosses travel min 15 mm
    H.until(be, lambda: bool(_ev(be, m0, "MOVE_DONE")), 20_000)
    x_end = H.rx(be, "DATA")[-1].fields["setpoint_um"] / 1000.0
    assert x_end >= 15.0 - 5.0 * 0.075 - 0.01, x_end


@pytest.mark.req("SAF-SW-001")
@pytest.mark.defect("SWD-M3-01")
def test_tc_saf_sw_001_04_pull_limit_supervised_while_travel_latch_is_set(lockstep):
    """TC-SAF-SW-001-04 (2): a TRAVEL_MIN latch (outward motion below the enabled travel minimum, holds until standstill
    inside) must not suspend the force supervision — moving back in + (allowed: away from the travel limit) into a
    spring must still trip PULL at 150 N and stop (FW level 2157 N is far away)."""
    # Verifies: SAF-SW-001
    be = lockstep()
    fit, t = H.calibrate_and_tare(be)
    assert not H.set_limits(be, pull_trip_n=150.0)
    H.m2_ready(be)
    assert be.limits.set_no_specimen_mode(False).ok
    H.result(be, H.move_to(be, 12.0, speed_mm_s=5.0), 30_000)
    assert H.wait_idle(be)
    assert not H.set_limits(be, travel_min_mm=15.0, travel_min_enabled=True)   # axis now below the limit
    H.act(be, "specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=20_000)
    H.result(be, H.forced_move_abs(be, 10_000, 5_000))         # outward (other client) → TRAVEL_MIN trip
    assert H.until(be, lambda: H.safety(be).sw_trip == "TRAVEL_MIN", 5000)
    assert H.wait_idle(be)
    m0 = H.wire_mark(be)
    H.move_to(be, 40.0, speed_mm_s=5.0)                        # away from the travel limit: allowed
    H.until(be, lambda: H.safety(be).sw_trip == "PULL" or bool(_ev(be, m0, "MOVE_DONE")), 30_000)
    H.advance(be, 500)
    k, tare_raw = fit.result.K, t.result.tare_raw
    f_max = max(f_ref.force_n(w.fields["afe_raw"], k, tare_raw) for w in H.rx(be, "DATA", since=m0))
    assert H.safety(be).sw_trip == "PULL" and f_max < 150.0 + 5.0 * 50.0 * 0.075 + 5.0, (H.safety(be).sw_trip, f_max)
    assert "PULL" in {t.limit for t in H.safety(be).trips}                 # per-class latches (B5-25)
    # direction-aware refusal of the PULL latch still in force: + (more tension) refused, − allowed (unless TRAVEL_MIN)
    assert "SW_TRIP" in {str(i.code) for i in H.motion_check(be, "MOVE", target_mm=45.0).items}


@pytest.mark.req("SW-RT-002", "SW-RT-005")
def test_tc_sw_rt_002_02_derived_channels_availability(lockstep):
    """TC-SW-RT-002-01 (C part) / SW-RT-005: without a calibration the force-based channels are unavailable with a
    reason and their latest value is 'n/a'; after calibration + tare they become available (topic channels.changed)
    and report OK values."""
    # Verifies: SW-RT-002, SW-RT-005
    be = lockstep()
    specs = {c.key: c for c in be.channels.channels()}
    for k in ("F_N", "F_kgf", "force_rate_n_s", "work_nmm", "peak_n"):
        assert not specs[k].available and specs[k].reason, specs[k]
    assert be.data.latest("F_N").state == "n/a"
    n0 = len(H.history(be, "channels.changed"))
    H.calibrate_and_tare(be)
    specs = {c.key: c for c in be.channels.channels()}
    assert all(specs[k].available for k in ("F_N", "F_kgf", "force_rate_n_s", "work_nmm", "peak_n"))
    assert len(H.history(be, "channels.changed")) > n0
    assert be.data.latest("F_N").state == "OK"
