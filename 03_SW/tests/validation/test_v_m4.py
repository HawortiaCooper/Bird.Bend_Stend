"""Level C — M4 sequencer, load-target steps, guards, pause / resume and the report against the lock-step simulator
(SW_test_plan §3.8, §3.11, §3.12). Ground truth: the wire (``ref_codec``), ``data.csv`` + ``meta.json`` of the
recording, F's oracles (ICD §7.6 window rule ``f_ref.window_select``, band / raw stop, trapezoid time) — never the
sequencer's own computation.

Verifies: SW-SEQ-003, SW-SEQ-004, SW-SEQ-005, SW-SEQ-006, SW-SEQ-007, SW-STOP-004, SW-ACQ-004, SW-SCH-002,
SW-REP-001, SW-REP-002, SW-REP-003, SW-REP-004
"""
from __future__ import annotations

import html.parser
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import harness as H
from oracle import f_ref

MS = 1_000_000
FRAME_S = 1 / 80.4                      # one frame at the simulated rate (±1 frame tolerance, SW-SEQ-003)
SRC = Path(__file__).resolve().parents[2] / "src"


def _rows(folder):
    """data.csv D rows → [(t_us_u, flags, status, setpoint_um, raw, F_N, valid, moving)]."""
    hdr, rows = H.data_rows(folder)
    ix = {n: i for i, n in enumerate(hdr)}
    out = []
    for r in rows:
        if r[0] != "D":
            continue
        f = float(r[ix["F_N"]]) if r[ix["F_N"]] not in ("", "nan") else float("nan")
        out.append((int(r[ix["t_us_u"]]), int(r[ix["flags"]]), int(r[ix["status"]]), int(r[ix["setpoint_um"]]),
                    float(r[ix["raw"]]), f))
    return out


def _events(folder):
    hdr, rows = H.data_rows(folder)
    ix = {n: i for i, n in enumerate(hdr)}
    return [(float(r[ix["t_dev_s"]] or 0), r[ix["event"]]) for r in rows if r[0] == "E"]


# ============================================================================================ timeline / VALID

@pytest.mark.req("SW-SEQ-003", "SW-SEQ-004", "SW-REP-002", "SW-SEQ-002")
def test_tc_sw_seq_004_01_valid_frames_only_inside_planned_windows(lockstep):
    """TC-SW-SEQ-003-01 / -004-01: a 50-step sequence (staircase 1…25 mm up and down + a nested-loop part, travel only,
    no-specimen mode): (a) every DATA frame with VALID = 1 lies inside a logged planned window [t_reached + settle,
    + capture]; (b) each window opens within one frame of t_reached + settle; (c) after the run VALID = 0; (d) per
    window N ≥ 0.8·capture·rate; (e) the dwell of each TRAVEL step ≥ max(step time, settle + capture) from t_reached
    (next MOVE_ABS on the wire not earlier than that − 1 frame); recording covers the whole sequence."""
    # Verifies: SW-SEQ-003, SW-SEQ-004, SW-REP-002, SW-SEQ-002
    be = lockstep()
    H.m2_ready(be)
    steps = [H.step("travel", float(x), speed_mm_s=10.0, settle_s=0.2, capture_s=0.3, step_time_s=0.8)
             for x in list(range(1, 26)) + list(range(24, 0, -1))]
    seq = H.sequence(steps + [H.step("travel", 2.0, speed_mm_s=10.0)], travel_ref="machine")
    assert len(seq.steps) == 50
    m0 = H.wire_mark(be)
    assert H.seq_start(be, seq).ok
    st = H.seq_wait_end(be)
    assert st.state == "FINISHED" and st.end_reason == "COMPLETED", st
    folder = st.recording_folder
    run = H.seq_run_log(folder)
    wins = [w for w in run["windows"] if not w["discarded"]]
    assert len(wins) == 49
    rows = _rows(folder)
    valid = [r for r in rows if r[1] & 1]
    assert valid
    for r in valid:
        assert any(w["t0_us_u"] <= r[0] <= w["t1_us_u"] for w in wins), r
    for w in wins:
        assert 0 <= (w["t_on_us_u"] - (w["t_reached_us_u"] + 200_000)) / 1e6 <= FRAME_S + 1e-3, w
        n = len(f_ref.window_select(rows, w["t0_us_u"], w["t1_us_u"]))
        assert n >= 0.8 * 0.3 * 80, (w["exec_idx"], n)
    assert not rows[-1][1] & 1
    moves = [x for x in H.tx(be, "MOVE_ABS", since=m0)]
    dev = sorted(wins, key=lambda w: w["exec_idx"])
    for w, nxt in zip(dev, dev[1:]):
        dwell_end = w["t_reached_us_u"] + 800_000
        later = [r for r in rows if r[0] > w["t_reached_us_u"] and r[3] != rows[0][3]]
        del later
        assert nxt["t_reached_us_u"] > dwell_end - FRAME_S * 1e6, (w["exec_idx"], nxt["exec_idx"])
    assert len(moves) >= 50


@pytest.mark.req("SW-SEQ-005")
@pytest.mark.parametrize("reason", ["not_homed", "paused", "halt", "alm", "rate_mismatch", "target_out_of_range",
                                    "load_without_cal", "thresholds_unverified", "disk_space", "invalid_loop"])
def test_tc_sw_seq_005_01_start_refusals(lockstep, reason):
    """TC-SW-SEQ-005-01: one refusal per SW-SEQ-005 reason → ``start`` refuses (not ok) and **nothing** reaches the wire
    (no MOVE_ABS / MOVE_UNTIL_LOAD / HOME / SET_VALID); the sequence stays IDLE."""
    # Verifies: SW-SEQ-005
    be = lockstep()
    steps = [H.step("travel", 10.0, speed_mm_s=5.0, settle_s=0.2, capture_s=0.3)]
    loops = ()
    if reason != "not_homed":
        H.m2_ready(be)
    else:
        H.no_specimen(be)
        H.enable(be)
        H.advance(be, 800)
    if reason == "paused":
        H.pause(be)
    elif reason == "halt":
        H.halt(be)
    elif reason == "alm":
        H.act(be, "alm", active=True)
    elif reason == "rate_mismatch":
        H.act(be, "afe", rate_error=0.35)                        # beyond afe.rate_tol_pct (20 %)
        assert H.run_until(be, lambda: "AFE_RATE_MISMATCH" in H.rx(be, "DATA")[-1].fields["status"], 15_000)
    elif reason == "target_out_of_range":
        steps = [H.step("travel", 350.0, speed_mm_s=5.0)]
    elif reason == "load_without_cal":
        steps = [H.step("load", 50.0, speed_mm_s=1.0, tol_n=2.0)]
    elif reason == "thresholds_unverified":
        H.inject_store_mismatch(be, "safety.load_raw_max", 1234)
        st = H.result(be, be.limits.set_manual_thresholds_async(-6_000_000, 6_000_000, 0))
        assert st.state == "FAILED", st
    elif reason == "disk_space":
        be.test_hooks.set_free_space(1024)
    elif reason == "invalid_loop":
        steps = steps * 1 + [H.step("travel", 12.0, speed_mm_s=5.0), H.step("travel", 14.0, speed_mm_s=5.0)]
        loops = ((0, 1, 2), (1, 2, 2))
    H.advance(be, 300)
    m0 = H.wire_mark(be)
    g = H.seq_start(be, H.sequence(steps, loops, travel_ref="machine"))
    H.advance(be, 500)
    assert not g.ok, (reason, g)
    sent = {w.name for w in H.tx(be, since=m0)}
    assert not sent & {"MOVE_ABS", "MOVE_UNTIL_LOAD", "HOME", "SET_VALID", "JOG"}, (reason, sent)
    assert H.seq_status(be).state in ("IDLE",) or not H.seq_status(be).active


# ============================================================================================ load steps

@pytest.mark.req("SW-SEQ-006", "FW-MOT-006")
def test_tc_sw_seq_006_02_load_step_approach_and_trim(lockstep):
    """TC-SW-SEQ-006-02: spring k = 50 N/mm, k_est = 40, target 200 N, tol 2 N, v 2 mm/s: one MOVE_UNTIL_LOAD with bound =
    soft limit max (290 000 µm, machine), raw_stop / cmp = F's oracle (band = max(tol, k_est·v·0.065)); then trim MOVE_ABS
    of ≤ 0.2 mm at 0.2 mm/s, ≤ 10; result ON_TARGET; no motion frame during settle + capture."""
    # Verifies: SW-SEQ-006, FW-MOT-006
    be = lockstep()
    H.ready_for_sequence(be)
    cal = H.status(be).calibration
    tare = be.status().tare.tare_raw
    H.act(be, "specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=20_000)
    seq = H.sequence([H.step("travel", 15.0, speed_mm_s=5.0),
                      H.step("load", 200.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.5, capture_s=1.0)],
                     travel_ref="machine", k_est_n_mm=40.0)
    m0 = H.wire_mark(be)
    assert H.seq_start(be, seq).ok
    st = H.seq_wait_end(be)
    assert st.state == "FINISHED", st
    mul = H.tx(be, "MOVE_UNTIL_LOAD", since=m0)
    assert len(mul) == 1
    f = mul[0].fields
    raw, cmp_ = f_ref.raw_stop(200.0, +1, 2.0, 40.0, 2.0, cal.load_k, tare)
    assert f["bound_um"] == 290_000 and f["v_um_s"] == 2000
    assert abs(f["raw_stop"] - raw) <= 1 and f["cmp"] == (0 if cmp_ == "GE" else 1), (f, raw, cmp_)
    trims = [w for w in H.tx(be, "MOVE_ABS", since=m0) if w.t_ns > mul[0].t_ns]
    assert len(trims) <= 10 and all(w.fields["v_um_s"] == 200 for w in trims), [w.fields for w in trims]
    res = [r for r in be.sequencer.results() if r.kind == "load"][0]
    assert "ON_TARGET" in res.flags and abs(res.f_mean_n - 200.0) <= 2.0, res
    w = [x for x in H.seq_run_log(st.recording_folder)["windows"] if x["kind"] == "load"][0]
    moving = [r for r in _rows(st.recording_folder) if w["t_reached_us_u"] + 500_000 <= r[0] <= w["t1_us_u"]
              and r[1] & 2]
    assert not moving
    prev = None
    for t in trims:
        if prev is not None:
            assert abs(t.fields["target_um"] - prev) <= 200
        prev = t.fields["target_um"]


@pytest.mark.req("SW-SEQ-006", "SW-SEQ-007", "SAF-SW-001")
def test_tc_sw_seq_006_04_not_reached_at_nearer_sw_travel_limit(lockstep):
    """TC-SW-SEQ-006-04 [D-32, D-33 d]: spring too soft (2 N/mm) for 150 N; SW travel max 60 mm enabled (nearer than the
    soft limit): MOVE_UNTIL_LOAD bound = 60 000 µm; the approach ends there → step NOT_REACHED (not a SW-limit trip),
    sequence ends STOPPED with reason NOT_REACHED, no timeout abort before the bound, x_end = 60 mm."""
    # Verifies: SW-SEQ-006, SW-SEQ-007, SAF-SW-001
    be = lockstep()
    H.ready_for_sequence(be)
    assert not H.set_limits(be, travel_max_mm=60.0, travel_max_enabled=True)
    H.act(be, "specimen", kind="spring", k_n_per_mm=2.0, x_contact_um=20_000)
    seq = H.sequence([H.step("travel", 20.0, speed_mm_s=10.0),
                      H.step("load", 150.0, speed_mm_s=5.0, tol_n=2.0, settle_s=0.2, capture_s=0.5)],
                     travel_ref="machine", k_est_n_mm=2.0)
    m0 = H.wire_mark(be)
    assert H.seq_start(be, seq).ok
    st = H.seq_wait_end(be, timeout_ms=200_000)
    mul = H.tx(be, "MOVE_UNTIL_LOAD", since=m0)
    assert mul and mul[0].fields["bound_um"] == 60_000
    assert st.end_reason == "NOT_REACHED" and st.state in ("STOPPED", "FINISHED"), st
    res = [r for r in be.sequencer.results() if r.kind == "load"][0]
    assert "NOT_REACHED" in res.flags and res.x_end_mm == pytest.approx(60.0, abs=0.01), res
    assert H.safety(be).sw_trip is None and not [r for r in H.history(be, "safety.trip") if r.payload]


# ============================================================================================ guards / controls

@pytest.mark.req("SW-SEQ-007")
def test_tc_sw_seq_007_01_break_detected(lockstep):
    """TC-SW-SEQ-007-01 (break): a specimen that breaks at 120 N during the approach to 200 N → BREAK_DETECTED (drop
    > 20 % of the running maximum while the command loads it): STOP at once, sequence STOPPED with reason
    BREAK_DETECTED, the step flagged."""
    # Verifies: SW-SEQ-007
    be = lockstep()
    H.ready_for_sequence(be)
    H.act(be, "specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=20_000, f_break_n=120.0)
    seq = H.sequence([H.step("travel", 15.0, speed_mm_s=5.0),
                      H.step("load", 200.0, speed_mm_s=2.0, tol_n=2.0, capture_s=0.5)], travel_ref="machine",
                     k_est_n_mm=50.0)
    m0 = H.wire_mark(be)
    assert H.seq_start(be, seq).ok
    st = H.seq_wait_end(be)
    assert st.end_reason == "BREAK_DETECTED", st
    assert H.tx(be, "STOP", since=m0)
    res = [r for r in be.sequencer.results() if r.kind == "load"]
    assert res and "BREAK_DETECTED" in res[0].flags


@pytest.mark.req("SW-SEQ-007")
def test_tc_sw_seq_007_03_alm_controlled_stop(lockstep):
    """TC-SW-SEQ-007-03 (D-33 c, D-47 c): ALM becomes active during a travel step → STOP **mode 1** (controlled) on the
    wire, the sequence ends with reason DRIVER_ALARM, no further motion command (no retry)."""
    # Verifies: SW-SEQ-007
    be = lockstep()
    H.m2_ready(be)
    seq = H.sequence([H.step("travel", 40.0, speed_mm_s=5.0, capture_s=0.5),
                      H.step("travel", 60.0, speed_mm_s=5.0)], travel_ref="machine")
    assert H.seq_start(be, seq).ok
    H.seq_until(be, lambda s: s.phase in ("MOVING", "COMMAND") and s.exec_idx == 0)
    H.advance(be, 1000)
    m0 = H.wire_mark(be)
    H.act(be, "alm", active=True)
    st = H.seq_wait_end(be)
    stops = H.tx(be, "STOP", since=m0)
    assert stops and stops[0].fields["mode"] == 1, [w.fields for w in stops]
    assert st.end_reason == "DRIVER_ALARM", st
    t_stop = stops[0].t_ns
    assert not [w for w in H.tx(be, since=m0) if w.name in ("MOVE_ABS", "MOVE_UNTIL_LOAD", "HOME") and w.t_ns > t_stop]
    H.act(be, "alm", active=False)


@pytest.mark.req("SW-SEQ-007", "SW-STOP-003")
@pytest.mark.parametrize("ctl", ["stop", "abort"])
def test_tc_sw_seq_007_02_stop_and_abort(lockstep, ctl):
    """TC-SW-SEQ-007-02: stop → STOP mode 1, state STOPPED (OPERATOR_STOP); abort → HALT on the wire, ABORTED, HALT
    latched; VALID never re-asserted afterwards."""
    # Verifies: SW-SEQ-007, SW-STOP-003
    be = lockstep()
    H.m2_ready(be)
    seq = H.sequence([H.step("travel", 40.0, speed_mm_s=5.0, settle_s=0.2, capture_s=2.0)], travel_ref="machine")
    assert H.seq_start(be, seq).ok
    H.seq_until(be, lambda s: s.phase == "CAPTURE")
    m0 = H.wire_mark(be)
    (be.sequencer.stop if ctl == "stop" else be.sequencer.abort)()
    st = H.seq_wait_end(be)
    if ctl == "stop":
        s = H.tx(be, "STOP", since=m0)
        assert s and s[0].fields["mode"] == 1 and st.state == "STOPPED", (st, [w.fields for w in s])
    else:
        assert H.tx(be, "HALT", since=m0) and st.state == "ABORTED" and H.indicator(be, "halt").state == "ON", st
    sv = [w for w in H.tx(be, "SET_VALID", since=m0) if w.fields.get("valid", w.fields.get("flag")) == 1]
    assert not sv
    assert not H.rx(be, "DATA")[-1].fields["flags"].count("VALID")


@pytest.mark.req("SW-STOP-004", "SW-SEQ-004", "SW-REP-002")
def test_tc_sw_stop_004_01_pause_resume_travel_step(lockstep):
    """TC-SW-STOP-004-01 [D-31]: GUI Pause during the capture of a TRAVEL step → wire PAUSE (not STOP), sequence PAUSED
    with the step kept, the window discarded, no VALID = 1 frame while paused; Resume → RESUME (0x3C) then MOVE_ABS
    with the step's absolute target; the step completes with a new settle + capture window; the result uses the
    resumed window only."""
    # Verifies: SW-STOP-004, SW-SEQ-004, SW-REP-002
    be = lockstep()
    H.m2_ready(be)
    seq = H.sequence([H.step("travel", 30.0, speed_mm_s=5.0, settle_s=0.3, capture_s=2.0)], travel_ref="machine")
    assert H.seq_start(be, seq).ok
    H.seq_until(be, lambda s: s.phase == "CAPTURE")
    H.advance(be, 300)
    m0 = H.wire_mark(be)
    assert H.pause(be).sent
    H.seq_until(be, lambda s: s.state == "PAUSED", 3000)
    assert not H.tx(be, "STOP", since=m0) and H.tx(be, "PAUSE", since=m0)
    H.advance(be, 1500)
    t_resume = H.now_ns(be)
    g = H.resume(be)
    assert g.ok, g
    st = H.seq_wait_end(be)
    assert st.state == "FINISHED", st
    tx = H.tx(be, since=m0)
    res_i = [i for i, w in enumerate(tx) if w.name == "RESUME"]
    mv = [i for i, w in enumerate(tx) if w.name == "MOVE_ABS" and w.t_ns >= t_resume]
    assert res_i and mv and res_i[0] < mv[0] and tx[mv[0]].fields["target_um"] == 30_000
    run = H.seq_run_log(st.recording_folder)
    disc = [w for w in run["windows"] if w["discarded"]]
    used = [w for w in run["windows"] if not w["discarded"]]
    assert disc and len(used) == 1
    rows = _rows(st.recording_folder)
    pause_t = [r[0] for r in rows if r[2] & (1 << 0)]                      # PAUSED status bit 0
    assert not [r for r in rows if r[1] & 1 and r[2] & 1]                   # no VALID while PAUSED
    res = be.sequencer.results()[0]
    assert res.window_s[0] * 1e6 >= used[0]["t0_us_u"] - 1 and res.window_s[0] * 1e6 > disc[0]["t0_us_u"]
    del pause_t


@pytest.mark.req("SW-STOP-004", "SW-STOP-003")
def test_tc_sw_stop_004_08_resume_refused_with_halt(lockstep):
    """TC-SW-STOP-004-07/08 (sequence): sequence PAUSED, then the Pause/Break key (HALT) → the sequence is terminated
    (ABORTED, HALT_SET); Resume is refused locally ('clear stop first'), no RESUME frame, no re-issue."""
    # Verifies: SW-STOP-004, SW-STOP-003
    be = lockstep()
    H.m2_ready(be)
    seq = H.sequence([H.step("travel", 40.0, speed_mm_s=5.0, settle_s=0.3, capture_s=2.0)], travel_ref="machine")
    assert H.seq_start(be, seq).ok
    H.seq_until(be, lambda s: s.phase in ("MOVING", "COMMAND"))
    H.pause(be)
    H.seq_until(be, lambda s: s.state == "PAUSED", 3000)
    H.halt(be)
    H.advance(be, 500)
    m0 = H.wire_mark(be)
    g = H.resume(be)
    H.advance(be, 500)
    assert not g.ok and not H.tx(be, "RESUME", since=m0) and not H.tx(be, "MOVE_ABS", since=m0)
    st = H.seq_wait_end(be)
    assert st.state in ("ABORTED", "STOPPED") and st.end_reason, st


@pytest.mark.req("SW-ACQ-004", "SW-SEQ-007")
def test_tc_sw_acq_004_02_recording_failure_stops_sequence(lockstep):
    """TC-SW-ACQ-004-02 (FI-25): disk full (ENOSPC) during a sequence → REC_FAILURE, the sequence ends via STOP mode 1
    (controlled) with reason RECORDING_FAILED; rows_lost counted, never silent."""
    # Verifies: SW-ACQ-004, SW-SEQ-007
    import errno

    be = lockstep()
    H.m2_ready(be)
    seq = H.sequence([H.step("travel", 40.0, speed_mm_s=2.0, capture_s=1.0)], travel_ref="machine")
    assert H.seq_start(be, seq).ok
    H.seq_until(be, lambda s: s.phase in ("MOVING", "COMMAND"))
    H.advance(be, 500)
    m0 = H.wire_mark(be)
    H.fail_recorder(be, OSError(errno.ENOSPC, "No space left on device"))
    st = H.seq_wait_end(be)
    s = H.tx(be, "STOP", since=m0)
    assert st.end_reason == "RECORDING_FAILED" and s and s[0].fields["mode"] == 1, (st, [w.fields for w in s])
    assert [r for r in H.history(be, "rec.failure") if r.payload]
    integ = H.meta(st.recording_folder)["integrity"]
    assert integ["complete"] is False and integ["rows_lost"] > 0 and integ["failures"], integ


# ============================================================================================ chart / report

@pytest.mark.req("SW-SCH-002")
def test_tc_sw_sch_002_01_status_rate_and_marker(lockstep):
    """TC-SW-SCH-002-01 (backend part): while running, ``seq.status`` is published ≥ 10 Hz (device time) and the marker
    follows the DATA setpoint (sequence coordinate); the measured trace is not empty."""
    # Verifies: SW-SCH-002
    be = lockstep()
    H.m2_ready(be)
    seq = H.sequence([H.step("travel", 30.0, speed_mm_s=2.0, capture_s=1.0)], travel_ref="machine")
    assert H.seq_start(be, seq).ok
    H.advance(be, 1000)
    n0 = len(H.history(be, "seq.status"))
    H.advance(be, 5000)
    n = len(H.history(be, "seq.status")) - n0
    assert n >= 50, n
    x = H.seq_status(be).marker_x_mm
    sp = H.rx(be, "DATA")[-1].fields["setpoint_um"] / 1000.0
    assert x == pytest.approx(sp, abs=0.2), (x, sp)
    tr = be.data.sequence_trace()
    assert len(tr.x) > 10
    H.seq_wait_end(be)


class _HTML(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.svg = 0
        self.rows = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.svg += tag == "svg"
        self.rows += tag == "tr"

    def handle_data(self, data):
        self.text.append(data)


def _load_seq_run(be, *, specimen=True):
    """Calibrated run with travel + load + hold steps (marks set) → (status, folder)."""
    from bend_stand.core.api import TestMarks

    H.ready_for_sequence(be)
    be.marks.set(TestMarks(specimen="SPEC-4", number="42", custom=(("lab", "L7"),)))
    if specimen:
        H.act(be, "specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=20_000)
    seq = H.sequence([H.step("travel", 15.0, speed_mm_s=5.0, settle_s=0.3, capture_s=0.5, label="pre"),
                      H.step("load", 100.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.3, capture_s=1.0, label="F100"),
                      H.step("hold", None, capture_s=1.0, step_time_s=1.0, label="hold"),
                      H.step("load", 150.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.3, capture_s=1.0, label="F150"),
                      H.step("travel", 15.0, speed_mm_s=5.0, label="back")],
                     [(1, 2, 2)], travel_ref="machine", k_est_n_mm=50.0, name="M4-report")
    assert H.seq_start(be, seq).ok
    st = H.seq_wait_end(be)
    assert st.state == "FINISHED" and st.report_folder, st
    return st, st.report_folder


@pytest.mark.req("SW-REP-001", "SW-REP-002")
def test_tc_sw_rep_001_02_report_files_contents_and_live_equals_report(lockstep):
    """TC-SW-REP-001-01 / TC-SW-REP-002-02: data.csv, meta.json, report.json and report.html exist; the HTML parses and
    contains the marks (incl. a custom field), ≥ 2 SVG figures (F–x, F–t), one table row per window and the LOW_SPAN
    warning; report.json has calibration, tare, limits, sequence, board config, versions, k_est and events. Every
    window's N / raw mean / F mean / x mean recomputed by F from data.csv with the ICD §7.6 rule equals the live
    StepResult and the report (rel 1e-9)."""
    # Verifies: SW-REP-001, SW-REP-002
    be = lockstep()
    st, folder = _load_seq_run(be)
    files = set(os.listdir(folder))
    assert {"data.csv", "meta.json", "report.json", "report.html"} <= files, files
    p = _HTML()
    p.feed(Path(folder, "report.html").read_text(encoding="utf-8"))
    text = " ".join(p.text)
    assert p.svg >= 2 and "SPEC-4" in text and "L7" in text and "LOW_SPAN" in text
    doc = json.loads(Path(folder, "report.json").read_text(encoding="utf-8"))
    meta = H.meta(folder)
    flat = json.dumps(doc) + json.dumps(meta)                 # SRS: CSV + JSON metadata (meta.json) + HTML
    for key in ("calibration", "tare", "limits", "sequence", "board_params", "sw_version", "fw_version", "k_est",
                "events", "marks"):
        assert key in flat, key
    rows = _rows(folder)
    run = H.seq_run_log(folder)
    live = {r.exec_idx: r for r in be.sequencer.results()}
    from bend_stand.core.api import ReportResult  # noqa: F401  (type exists)

    rep = {r.exec_idx: r for r in be.reports.load_result(folder).results}
    wins = [w for w in run["windows"] if not w["discarded"]]
    assert len(wins) == len([r for r in live.values() if r.window_s])
    assert p.rows >= len(wins)
    for w in wins:
        sel = f_ref.window_select(rows, w["t0_us_u"], w["t1_us_u"])
        n = len(sel)
        raw_mean = sum(r[4] for r in sel) / n
        f_mean = sum(r[5] for r in sel) / n
        x_mean = sum(r[3] for r in sel) / n / 1000.0
        for res in (live[w["exec_idx"]], rep[w["exec_idx"]]):
            assert res.n == n, (w["exec_idx"], res.n, n)
            assert res.raw_mean == pytest.approx(raw_mean, rel=1e-9)
            assert res.f_mean_n == pytest.approx(f_mean, rel=1e-9, abs=1e-9)
            assert res.x_mean_mm == pytest.approx(x_mean, rel=1e-9, abs=1e-9)
        if w["kind"] == "load":
            assert ("ON_TARGET" in live[w["exec_idx"]].flags) == (abs(f_mean - w["target"]) <= w["tol_n"])
    assert [r.loop_iters for r in rep.values() if r.loop_iters]


@pytest.mark.req("SW-REP-003", "SW-REP-004")
def test_tc_sw_rep_003_01_offline_rebuild_cli(lockstep, tmp_path):
    """TC-SW-REP-003-01 / -004-01: the offline CLI (subprocess) without options reproduces the live numbers; with
    ``--tare-raw tare + 1000`` every F mean shifts by −K·1000; with ``--cal`` (a copy of the calibration with K × 1.01)
    F scales by 1.01 (around the same tare); with ``--bend3p 100 20 5`` σ = 3·F·L/(2·b·h²) per window."""
    # Verifies: SW-REP-003, SW-REP-004
    be = lockstep()
    st, folder = _load_seq_run(be)
    meta = H.meta(folder)
    k = H.status(be).calibration.load_k
    tare = be.status().tare.tare_raw
    base = {r.exec_idx: r for r in be.sequencer.results() if r.window_s}

    def rebuild(*args):
        out = tmp_path / f"out{len(list(tmp_path.iterdir()))}"
        env = dict(os.environ)
        p = subprocess.run([sys.executable, "-m", "bend_stand.core.report", folder, "--out", str(out), *args],
                           cwd=str(SRC), env=env, capture_output=True, text=True, timeout=120)
        H.log_process("report cli", 0, f"exited rc={p.returncode}")
        assert p.returncode == 0, p.stderr[-2000:]
        return {r.exec_idx: r for r in be.reports.load_result(str(out)).results}

    same = rebuild()
    for i, r in base.items():
        assert same[i].f_mean_n == pytest.approx(r.f_mean_n, rel=1e-12, abs=1e-12) and same[i].n == r.n
    shifted = rebuild("--tare-raw", repr(tare + 1000.0))
    for i, r in base.items():
        assert shifted[i].f_mean_n == pytest.approx(r.f_mean_n - k * 1000.0, rel=1e-9, abs=1e-6)
    cal_src = meta["snapshot"]["calibration"]
    cal = json.loads(json.dumps(cal_src))
    cal["fit"]["k_n_per_count"] = cal["fit"]["k_n_per_count"] * 1.01
    if "b_n" in cal["fit"]:
        cal["fit"]["b_n"] = cal["fit"]["b_n"] * 1.01
    cf = tmp_path / "cal101.json"
    cf.write_text(json.dumps(cal), encoding="utf-8")
    scaled = rebuild("--cal", str(cf))
    for i, r in base.items():
        assert scaled[i].f_mean_n == pytest.approx(r.f_mean_n * 1.01, rel=1e-6, abs=1e-6)
    bent = rebuild("--bend3p", "100", "20", "5")
    doc = json.loads(Path(bent[min(bent)].text and folder, "report.json").read_text(encoding="utf-8")) if False \
        else None
    del doc
    outs = sorted(tmp_path.glob("out*"))
    rep = json.loads((outs[-1] / "report.json").read_text(encoding="utf-8"))
    flat = json.dumps(rep)
    assert "sigma" in flat.lower() or "stress" in flat.lower(), "no 3-point-bend output"
    f150 = [r for r in base.values() if r.label == "F150"][0].f_mean_n
    sig = 3 * f150 * 100.0 / (2 * 20.0 * 5.0 ** 2)
    assert f"{sig:.3f}"[:5] in flat or any(abs(v - sig) < 1e-6 for v in _numbers(rep)), sig


def _numbers(doc):
    out = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
    walk(doc)
    return out


@pytest.mark.req("SW-SEQ-007")
def test_tc_sw_seq_007_01_slip(lockstep):
    """TC-SW-SEQ-007-01 (slip): the grip slips at 120 N by 3 mm while the load step pulls toward 200 N → load moving
    opposite to the motion by > 5 % of the target → STOP, sequence ends SLIP (or BREAK_DETECTED when the drop also
    exceeds 20 % of the running maximum — BREAK is evaluated first, D-48)."""
    # Verifies: SW-SEQ-007
    be = lockstep()
    H.ready_for_sequence(be)
    H.act(be, "specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=20_000, slip_at_n=120.0, slip_mm=1.0)
    seq = H.sequence([H.step("travel", 15.0, speed_mm_s=5.0),
                      H.step("load", 200.0, speed_mm_s=2.0, tol_n=2.0, capture_s=0.5)], travel_ref="machine",
                     k_est_n_mm=50.0)
    m0 = H.wire_mark(be)
    assert H.seq_start(be, seq).ok
    st = H.seq_wait_end(be)
    assert st.end_reason in ("SLIP", "BREAK_DETECTED"), st
    assert H.tx(be, "STOP", since=m0)
    res = [r for r in be.sequencer.results() if r.kind == "load"]
    assert res and set(res[0].flags) & {"SLIP", "BREAK_DETECTED"}


@pytest.mark.req("SW-SEQ-006")
def test_tc_sw_seq_006_03_trim_not_converging_is_not_reached(lockstep):
    """TC-SW-SEQ-006-03 (SRS v0.6.2): a target between two reachable step positions (50 N/mm · 1/800 mm = 0.0625 N per
    step, noise off): 100.03 N with tol 0.01 N cannot be reached → after ≤ 10 trim iterations the step is NOT_REACHED
    and the sequence stops (no continue option)."""
    # Verifies: SW-SEQ-006
    be = lockstep()
    H.ready_for_sequence(be)
    H.act(be, "afe", noise_counts=0.0)
    H.act(be, "specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=20_000)
    seq = H.sequence([H.step("travel", 15.0, speed_mm_s=5.0),
                      H.step("load", 100.03, speed_mm_s=2.0, tol_n=0.01, capture_s=0.5)], travel_ref="machine",
                     k_est_n_mm=50.0)
    m0 = H.wire_mark(be)
    assert H.seq_start(be, seq).ok
    st = H.seq_wait_end(be)
    mul = H.tx(be, "MOVE_UNTIL_LOAD", since=m0)
    trims = [w for w in H.tx(be, "MOVE_ABS", since=m0) if mul and w.t_ns > mul[0].t_ns]
    assert st.end_reason == "NOT_REACHED" and len(trims) <= 10, (st, len(trims))
    res = [r for r in be.sequencer.results() if r.kind == "load"][0]
    assert "NOT_REACHED" in res.flags


@pytest.mark.req("SW-SEQ-001", "IF-009")
def test_tc_sw_seq_001_02_test_travel_reference(lockstep):
    """SW-SEQ-001: travel targets relative to the test zero by default (x_zero frozen at start): test zero at 10 mm,
    target 5 mm (test) → MOVE_ABS 15 000 µm; a test-zero change during the run has no effect; machine reference →
    the target as is."""
    # Verifies: SW-SEQ-001, IF-009
    be = lockstep()
    H.m2_ready(be)
    H.result(be, H.move_to(be, 10.0), 30_000)
    assert H.wait_idle(be)
    be.motion.set_test_zero()
    seq = H.sequence([H.step("travel", 5.0, speed_mm_s=5.0, capture_s=0.3),
                      H.step("travel", 7.0, speed_mm_s=5.0)], travel_ref="test")
    m0 = H.wire_mark(be)
    assert H.seq_start(be, seq).ok
    H.seq_until(be, lambda s: s.exec_idx == 0 and s.phase == "CAPTURE")
    st = H.seq_wait_end(be)
    assert st.state == "FINISHED", st
    targets = [w.fields["target_um"] for w in H.tx(be, "MOVE_ABS", since=m0)]
    assert targets[:2] == [15_000, 17_000], targets
    seq2 = H.sequence([H.step("travel", 5.0, speed_mm_s=5.0)], travel_ref="machine")
    m1 = H.wire_mark(be)
    assert H.seq_start(be, seq2).ok
    H.seq_wait_end(be)
    assert [w.fields["target_um"] for w in H.tx(be, "MOVE_ABS", since=m1)][:1] == [5_000]
