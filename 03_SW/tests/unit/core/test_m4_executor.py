"""M4 sequence execution engine on the lock-step simulator (SW_design §10.3–§10.5, §22c).

Verifies: SW-SEQ-002, SW-SEQ-003, SW-SEQ-004, SW-SEQ-005, SW-SEQ-006, SW-SEQ-007, SW-STOP-004, SW-ACQ-004,
SW-SCH-002, SW-REP-002, FW-MOT-006
"""
from __future__ import annotations

import errno
from dataclasses import replace
from pathlib import Path

import pytest
from seq_rig import X_ZERO_MM, load_seq, move_targets, mul_frames, ready, run_to_end, start, stop_modes, wire

from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import GateId
from bend_stand.core.report import Recording
from bend_stand.core.sequencer.model import Loop, Sequence, Step, StepKind

Cmd = pg.Cmd
FRAME_US = 12_500


def _valid_inside_windows(folder: str) -> tuple[int, int, list[dict]]:
    rec = Recording(folder)
    wins = [w for r in rec.runs for w in r["windows"]]
    v = (rec.flags & 1) == 1
    outside = sum(1 for t in rec.t[v] if not any(w["t0_us_u"] <= t <= w["t1_us_u"] for w in wins))
    return int(v.sum()), outside, wins


# ------------------------------------------------------------------------------------------------ load steps
@pytest.mark.req("SW-SEQ-006", "SW-SEQ-003", "SW-SEQ-004", "SW-SEQ-002", "FW-MOT-006", "SW-REP-002")
def test_load_sequence_k50_kest40_on_target_windows_inside_plan(tmp_path) -> None:
    be = ready(tmp_path)
    try:
        h = be.test_hooks
        seq = load_seq(100.0, 200.0, loops=[Loop(1, 2, 2)])
        start(be, seq)
        assert be.status().gates[GateId.MOVE].codes().count("OWNER_CONFLICT") == 1    # manual motion refused
        assert not be.status().gates[GateId.SEQUENCE_EDIT].ok
        st = run_to_end(be)
        assert st.state == "FINISHED" and st.end_reason == "COMPLETED" and st.remaining_s == 0.0
        res = be.sequencer.results()
        assert [(r.uid, r.loop_iters) for r in res] == [("l0", (1,)), ("l1", (1,)), ("l0", (2,)), ("l1", (2,))]
        for r in res:
            assert "ON_TARGET" in r.flags and "INCOMPLETE" not in r.flags and r.n >= 64, r
            assert abs(r.f_mean_n - r.target) <= 2.0
            it = [int(f.split("_")[-1]) for f in r.flags if f.startswith("TRIM_ITER_")]
            assert not it or it[0] <= 10
        assert abs(st.k_est_n_mm - 50.0) < 2.0                                     # measured in the approach
        mul = mul_frames(be)
        # loading toward + (pull_dir +1): bound = soft max, cmp GE; 200 → 100 N unloads: bound = soft min, cmp LE
        assert [(m[0], m[4]) for m in mul] == [(290_000, 0), (290_000, 0), (500, 1), (290_000, 0)]
        n_valid, outside, wins = _valid_inside_windows(st.recording_folder)
        assert n_valid > 0 and outside == 0 and len(wins) == 4
        for w in wins:                                                             # SW-SEQ-003 ±1 frame
            assert 0 <= w["t_on_us_u"] - w["t0_us_u"] <= FRAME_US + 2000
            assert 0 <= w["t1_us_u"] - w["t_off_us_u"] <= FRAME_US + 2000
        assert be.owner == "MANUAL" and be.status().gates[GateId.SEQUENCE_EDIT].ok
        assert (Path(st.report_folder) / "report.html").is_file()
        trace = be.data.sequence_trace(500)
        assert 0 < len(trace.x) <= 500 and h.run_until(lambda: True, 1)
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-006", "SW-SEQ-007")
def test_not_reached_at_sw_travel_limit_bound_no_sw_trip(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=5.0, k_n_mm=2.0)                  # too soft: 200 N needs 100 mm
    try:
        cfg = be.limits.get()
        assert not be.limits.set(replace(cfg, travel_max_enabled=True, travel_max_mm=X_ZERO_MM + 12.0))
        start(be, load_seq(200.0, speed=5.0, ret=False))
        st = run_to_end(be)
        assert st.state == "STOPPED" and st.end_reason == "NOT_REACHED"
        assert mul_frames(be)[0][0] == int((X_ZERO_MM + 12.0) * 1000)             # the nearer bound (SW limit)
        r = be.sequencer.results()[-1]
        assert r.flags == ("NOT_REACHED",) and r.x_end_mm == pytest.approx(12.0, abs=0.01)
        assert be.safety.trip is None and not be.events.history("safety.trip")    # not a SW-limit trip
        assert stop_modes(be) == []                                                # the axis holds at the bound
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-006")
def test_bound_not_ahead_and_trim_exhausted(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=1.0)
    try:
        cfg = be.limits.get()
        assert not be.limits.set(replace(cfg, travel_max_enabled=True, travel_max_mm=X_ZERO_MM))
        start(be, load_seq(100.0, ret=False))
        st = run_to_end(be)
        assert st.end_reason == "BOUND_NOT_AHEAD" and "BOUND_NOT_AHEAD" in be.sequencer.results()[-1].flags
        assert not mul_frames(be)
        assert not be.limits.set(replace(cfg, travel_max_enabled=False))
        s = be.session.get()
        assert not be.session.set(replace(s, trim_max_iter=1, trim_max_step_mm=0.01))
        start(be, load_seq(150.0, k_est=4000.0, ret=False, tol=0.5))               # k_est far too high: tiny moves
        st = run_to_end(be)
        assert st.state == "STOPPED" and st.end_reason == "NOT_REACHED"
        assert any(f.startswith("TRIM_ITER_") for f in be.sequencer.results()[-1].flags)
    finally:
        be.shutdown()


# ------------------------------------------------------------------------------------------------ guards
@pytest.mark.req("SW-SEQ-007")
def test_break_detected_during_the_approach(tmp_path) -> None:
    be = ready(tmp_path, specimen={"f_break_n": 120.0})
    try:
        start(be, load_seq(200.0, ret=False))
        st = run_to_end(be)
        assert st.state == "STOPPED" and st.end_reason == "BREAK_DETECTED"
        assert 0 in stop_modes(be)                                                 # priority STOP (immediate)
        assert "BREAK_DETECTED" in be.sequencer.results()[-1].flags
        assert any("SEQ_GUARD" in e[2] for e in Recording(st.recording_folder).events)
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007")
def test_slip_guard(tmp_path) -> None:
    be = ready(tmp_path, specimen={"slip_at_n": 150.0, "slip_mm": 0.3})   # 15 N drop: > 5 % of 200 N, < 20 % of 150 N
    try:
        start(be, load_seq(200.0, ret=False))
        st = run_to_end(be)
        assert st.end_reason == "SLIP" and "SLIP" in be.sequencer.results()[-1].flags
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007")
def test_timeout_guard(tmp_path, monkeypatch) -> None:
    from bend_stand.calc import trim as T

    be = ready(tmp_path, contact_mm=None)
    try:
        monkeypatch.setattr(T, "step_timeout_s", lambda d, v, a: 0.3)
        seq = Sequence(steps=[Step("a", StepKind.TRAVEL, 5.0, speed_mm_s=2.0)])
        start(be, seq)
        st = run_to_end(be)
        assert st.end_reason == "TIMEOUT" and 0 in stop_modes(be)
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007")
def test_alm_mid_sequence_controlled_stop_driver_alarm_no_retry(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        seq = Sequence(steps=[Step("a", StepKind.TRAVEL, 5.0, speed_mm_s=1.0), Step("b", StepKind.TRAVEL, 0.0)])
        start(be, seq)
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        n_move = len(move_targets(be))
        be.sim.act("alm", active=True)
        st = run_to_end(be)
        assert st.state == "STOPPED" and st.end_reason == "DRIVER_ALARM"
        assert stop_modes(be)[-1] == int(pg.StopMode.CONTROLLED)
        assert len(move_targets(be)) == n_move                                    # no retry, no next step
        assert "DRIVER_ALARM" in be.sequencer.results()[-1].flags
        g = be.sequencer.check_start(seq)
        assert "ALM" in g.codes() and not g.ok                                    # start refused while ALM
    finally:
        be.shutdown()


@pytest.mark.req("SW-ACQ-004")
def test_recording_failure_stops_the_sequence(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        seq = Sequence(steps=[Step("a", StepKind.TRAVEL, 5.0, speed_mm_s=1.0), Step("b", StepKind.TRAVEL, 0.0)])
        start(be, seq)
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        h.fail_recorder(OSError(errno.ENOSPC, "disk full"))
        st = run_to_end(be)
        assert st.state == "STOPPED" and st.end_reason == "RECORDING_FAILED"
        assert stop_modes(be)[-1] == int(pg.StopMode.CONTROLLED)
    finally:
        be.shutdown()


# ------------------------------------------------------------------------------------------------ pause / resume
@pytest.mark.req("SW-STOP-004", "SW-SEQ-004")
def test_pause_resume_travel_and_load_steps(tmp_path) -> None:
    be = ready(tmp_path)
    try:
        h = be.test_hooks
        seq = load_seq(150.0, capture=2.0)
        seq.steps[0] = Step("s0", StepKind.TRAVEL, 2.0, speed_mm_s=1.0, settle_s=0.2, capture_s=1.0)
        start(be, seq)
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        r = be.pause()
        assert r.sent and h.run_until(lambda: be.sequencer.status().state == "PAUSED", 1000)
        assert be.sequencer.status().paused_source == "PC" and not be.status().gates[GateId.SEQUENCE_START].ok
        h.advance(1500)
        assert not be.status().motion.moving
        n_moves = len(move_targets(be))
        h.advance(500)
        assert len(move_targets(be)) == n_moves                                   # nothing sent while PAUSED
        g = be.resume()
        assert g.ok
        assert h.run_until(lambda: be.sequencer.status().state == "RUNNING", 2000)
        assert len(wire(be, Cmd.RESUME)) == 1 and move_targets(be)[-1] == int((X_ZERO_MM + 2.0) * 1000)
        assert h.run_until(lambda: be.sequencer.status().phase == "CAPTURE" and be.sequencer.status().exec_idx == 1,
                           60_000)
        be.pause("sequence")                                                      # pause inside the load capture
        assert h.run_until(lambda: be.sequencer.status().state == "PAUSED", 1000)
        h.advance(300)
        assert be.sequencer.resume().ok
        st = run_to_end(be)
        assert st.state == "FINISHED"
        n_valid, outside, wins = _valid_inside_windows(st.recording_folder)
        assert outside == 0 and any(w["discarded"] for w in wins)
        res = be.sequencer.results()
        assert [r.uid for r in res] == ["s0", "l0"] and "ON_TARGET" in res[-1].flags
        rec = Recording(st.recording_folder)                                      # VALID never 1 while PAUSED
        paused = (rec.status & int(pg.DataStatus.PAUSED)) != 0
        assert paused.any() and not ((rec.flags & 1) == 1)[paused].any()
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-004")
def test_pause_button_and_resume_request(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        start(be, Sequence(steps=[Step("a", StepKind.TRAVEL, 4.0, speed_mm_s=1.0)]))
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        be.sim.act("button", name="pause", pressed=True)
        h.advance(100)
        be.sim.act("button", name="pause", pressed=False)
        assert h.run_until(lambda: be.sequencer.status().state == "PAUSED", 1000)
        assert be.sequencer.status().paused_source == "BUTTON"
        h.advance(500)
        be.sim.act("button", name="pause", pressed=True)                          # RESUME_REQUEST
        h.advance(100)
        be.sim.act("button", name="pause", pressed=False)
        assert h.run_until(lambda: be.sequencer.status().state in ("RUNNING", "FINISHED"), 2000)
        assert len(wire(be, Cmd.RESUME)) == 1
        assert run_to_end(be).state == "FINISHED"
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-004")
def test_resume_refused_and_halt_terminates(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        start(be, Sequence(steps=[Step("a", StepKind.TRAVEL, 4.0, speed_mm_s=1.0)]))
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        be.pause()
        assert h.run_until(lambda: be.sequencer.status().state == "PAUSED", 1000)
        h.advance(300)
        be.sim.inject_nack("RESUME", int(pg.Status.E_STATE), int(pg.Block.HALT))
        n = len(move_targets(be))
        assert be.resume().ok
        st = run_to_end(be)
        assert st.state == "ABORTED" and st.end_reason == "RESUME_REFUSED" and len(move_targets(be)) == n
        # a HALT latched while paused terminates the sequence; Resume is refused by the gate, never sent
        start_g = be.sequencer.check_start(Sequence(steps=[Step("a", StepKind.TRAVEL, 1.0)]))
        assert "PAUSED" in start_g.codes()
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-004", "SW-SEQ-007")
def test_clear_stop_while_paused_ends_cleared_and_halt_while_paused(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        start(be, Sequence(steps=[Step("a", StepKind.TRAVEL, 4.0, speed_mm_s=1.0)]))
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        be.pause()
        assert h.run_until(lambda: be.sequencer.status().state == "PAUSED", 1000)
        assert "SEQUENCE_PAUSED" in be.status().gates[GateId.CLEAR_STOP].codes()
        fut = be.clear_stop_async(confirmed=True)
        assert h.result(fut).outcome == "OK"
        st = run_to_end(be)
        assert st.state == "STOPPED" and st.end_reason == "CLEARED"
        start(be, Sequence(steps=[Step("b", StepKind.TRAVEL, 3.0, speed_mm_s=1.0)]))
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        be.pause()
        assert h.run_until(lambda: be.sequencer.status().state == "PAUSED", 1000)
        be.halt("test")
        st = run_to_end(be)
        assert st.state == "ABORTED"
        assert not be.status().gates[GateId.RESUME].ok                            # HALT latched: no RESUME
    finally:
        be.shutdown()


# ------------------------------------------------------------------------------------------------ controls
@pytest.mark.req("SW-SEQ-007")
def test_stop_controlled_and_abort_halt(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        seq = Sequence(steps=[Step("a", StepKind.TRAVEL, 5.0, speed_mm_s=1.0), Step("b", StepKind.TRAVEL, 0.0)])
        start(be, seq)
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        r = be.sequencer.stop()
        assert r.sent and stop_modes(be)[-1] == int(pg.StopMode.CONTROLLED)
        st = run_to_end(be)
        assert st.state == "STOPPED" and st.end_reason == "OPERATOR_STOP"
        start(be, seq)
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        r = be.sequencer.abort("test")
        assert r.cmd == "HALT" and r.sent
        st = run_to_end(be)
        assert st.state == "ABORTED" and st.end_reason == "OPERATOR_ABORT"
        assert be.status().indicators["halt"].state == "ON"
        assert not be.sequencer.stop().sent                                       # nothing running
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007")
def test_red_stop_terminates_the_sequence(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        start(be, Sequence(steps=[Step("a", StepKind.TRAVEL, 5.0, speed_mm_s=1.0)]))
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        be.stop("gui")
        st = run_to_end(be)
        assert st.state == "ABORTED" and st.end_reason == "STOP"
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-001", "SW-SEQ-002", "SW-SCH-002")
def test_infinite_loop_mark_wait_operator_status_rate(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        seq = Sequence(steps=[Step("m", StepKind.MARK, wait_operator=True, label="go"),
                              Step("a", StepKind.TRAVEL, 1.0, speed_mm_s=5.0),
                              Step("b", StepKind.TRAVEL, 0.0, speed_mm_s=5.0)], loops=[Loop(1, 2, 0)])
        start(be, seq)
        assert h.run_until(lambda: be.sequencer.status().state == "WAITING_OPERATOR", 2000)
        st = be.sequencer.status()
        assert st.phase == "WAIT_OPERATOR" and st.remaining_s is None and st.plan_len is None
        be.sequencer.continue_()
        n0 = len(be.events.history("seq.status"))
        h.advance(2000)
        assert len(be.events.history("seq.status")) - n0 >= 20                   # ≥ 10 Hz (20 Hz design)
        assert h.run_until(lambda: (be.sequencer.status().loop_iters or (0,))[0] >= 3, 20_000)
        st = be.sequencer.status()
        assert st.state == "RUNNING" and st.remaining_s is None and st.marker_x_mm == st.marker_x_mm
        be.sequencer.stop()
        assert run_to_end(be).state == "STOPPED"
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-001", "SW-SEQ-003")
def test_tare_home_hold_ramp_steps_and_machine_reference(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        seq = Sequence(travel_ref="machine", steps=[
            Step("t", StepKind.TARE), Step("H", StepKind.HOME),
            Step("x0", StepKind.TRAVEL, 2.0, speed_mm_s=5.0),
            Step("ramp", StepKind.TRAVEL, 4.0, speed_mm_s=1.0, capture_during_move=True),
            Step("hold", StepKind.HOLD, settle_s=0.2, capture_s=0.5, step_time_s=1.0)])
        tare0 = be.status().tare.tare_id
        g = be.sequencer.start(seq)
        assert g.ok and g.needs_confirmation and be.sequencer.status().state == "IDLE"   # CONFIRM first (B3-20)
        start(be, seq)
        st = run_to_end(be, 120_000)
        assert st.state == "FINISHED", st.message
        assert be.status().tare.tare_id != tare0 and len(wire(be, Cmd.HOME)) == 2
        assert move_targets(be)[-2:] == [2000, 4000]                               # machine coordinate
        res = be.sequencer.results()
        assert [r.uid for r in res] == ["ramp", "hold"] and res[0].flags[0] == "RAMP"
        assert res[0].ramp["x_max_mm"] == pytest.approx(4.0) and res[1].n >= 32
        n_valid, outside, wins = _valid_inside_windows(st.recording_folder)
        assert outside == 0 and n_valid >= 100
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-004")
def test_fifty_step_sequence_valid_only_inside_planned_windows(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        steps = [Step(f"s{i}", StepKind.TRAVEL, 0.05 * (i % 10), speed_mm_s=5.0, capture_s=0.1) for i in range(50)]
        start(be, Sequence(steps=steps))
        st = run_to_end(be, 120_000)
        assert st.state == "FINISHED" and st.windows_done == 50
        n_valid, outside, wins = _valid_inside_windows(st.recording_folder)
        assert len(wins) == 50 and n_valid >= 300 and outside == 0
    finally:
        be.shutdown()


# ------------------------------------------------------------------------------------------------ start gate
@pytest.mark.req("SW-SEQ-005")
def test_start_refusals_one_per_reason(tmp_path) -> None:
    be = ready(tmp_path, home=False, contact_mm=None)
    try:
        h = be.test_hooks
        travel = Sequence(steps=[Step("a", StepKind.TRAVEL, 1.0)])
        g = be.sequencer.start(travel, confirmed=True)
        assert not g.ok and "NOT_HOMED" in g.codes() and be.sequencer.status().state == "IDLE"
        h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}))
        h.result(be.motion.home(load_confirmed=True), 60_000)
        assert h.run_until(lambda: be.status().motion.homed and not be.status().motion.moving, 2000)
        h.result(be.motion.move_to(10.0, speed_mm_s=10.0), 10_000)
        h.advance(100)
        be.motion.set_test_zero()
        assert be.sequencer.check_start(travel).ok

        def refused(code: str, seq: Sequence = travel) -> None:
            g = be.sequencer.check_start(seq)
            assert not g.ok and code in g.codes(), (code, g.items)

        DS = pg.DataStatus
        for bit, code in ((DS.POS_UNCERTAIN, "POS_UNCERTAIN"), (DS.AFE_RATE_MISMATCH, "AFE_RATE_MISMATCH"),
                          (DS.PAUSED, "PAUSED")):
            be.sim.override_status(set_bits=int(bit), duration_ms=300)
            h.advance(60)
            refused(code)
            h.advance(400)
        be.sim.override_status(clear_bits=int(DS.DRV_PWR), duration_ms=300)
        h.advance(60)
        if be.device.valid_status_mask() & int(DS.DRV_PWR):
            refused("DRV_PWR_OFF")
        h.advance(400)
        be.sim.act("alm", active=True)
        h.advance(60)
        refused("ALM")
        be.sim.act("alm", active=False)
        h.advance(200)
        refused("TARGET_OUT_OF_RANGE", Sequence(steps=[Step("a", StepKind.TRAVEL, 500.0)]))
        refused("SEQ_INVALID", Sequence(steps=[Step("a", StepKind.TRAVEL, None)]))
        load = Sequence(steps=[Step("l", StepKind.LOAD, 50.0, tol_n=1.0)])
        assert be.sequencer.check_start(load).ok
        assert be.limits.set_no_specimen_mode(True, confirmed=True).ok
        refused("NO_SPECIMEN_MODE", load)                                         # load steps need the PC limits
        assert be.limits.set_no_specimen_mode(False).ok
        be.halt("test")
        h.advance(60)
        refused("HALT")
        h.result(be.clear_stop_async())
        h.advance(60)
        be.test_hooks.set_free_space(1024)
        refused("DISK_SPACE")
        be.test_hooks.set_free_space(None)
        assert be.sequencer.check_start(travel).ok
        assert be.motion.disable(confirmed=True).ok
        h.advance(100)
        refused("NOT_ENABLED")
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-005", "SW-LIM-004")
def test_load_steps_need_calibration_and_no_specimen_confirm(tmp_path) -> None:
    be = ready(tmp_path, calibrate=False, no_specimen=True, contact_mm=None)
    try:
        load = Sequence(steps=[Step("l", StepKind.LOAD, 50.0, tol_n=1.0)])
        g = be.sequencer.check_start(load)
        assert "NO_SPECIMEN_MODE" in g.codes() and not g.ok
        g = be.sequencer.check_start(Sequence(steps=[Step("a", StepKind.TRAVEL, 1.0), Step("h", StepKind.HOME)]))
        assert g.ok and {"NO_SPECIMEN_TRAVEL_ONLY", "HOME_LOAD_CONFIRM"} <= set(g.codes())
        assert be.limits.set_no_specimen_mode(False).ok
        g = be.sequencer.check_start(load)
        assert "LOAD_INPUT_INVALID" in g.codes()
        assert be.sequencer.start(Sequence(steps=[Step("a", StepKind.TRAVEL, 1.0)])).codes()[0] in (
            "LOAD_INPUT_INVALID",)
    finally:
        be.shutdown()


@pytest.mark.req("SW-REP-001")
def test_sequence_inside_operator_recording_builds_report_at_record_stop(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        assert be.record_start().ok
        start(be, Sequence(steps=[Step("a", StepKind.TRAVEL, 1.0, speed_mm_s=5.0, capture_s=0.3)]))
        st = run_to_end(be)
        assert st.state == "FINISHED" and be.recorder.state == "RECORDING" and st.report_folder is None
        folder = be.recorder.folder
        assert be.record_stop().ok
        assert h.run_until(lambda: (Path(folder) / "report.json").is_file(), 3000)
        assert any(r.payload.folder == str(folder) for r in be.events.history("report.ready"))
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-004", "SW-STOP-003")
def test_link_loss_during_capture_aborts_and_clears_valid(tmp_path) -> None:
    """SWC-M4-03: PC → FW silence while a capture window is open → ABORTED / LINK_LOST (not ERROR), and VALID is
    cleared on the board once the link returns (no SET_VALID 1 and no motion afterwards)."""
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        start(be, Sequence(steps=[Step("a", StepKind.TRAVEL, 1.0, speed_mm_s=5.0, capture_s=1.0),
                                  Step("b", StepKind.TRAVEL, 0.0, speed_mm_s=5.0)]))
        assert h.run_until(lambda: be.device.last_flags & 1, 10_000)               # VALID = 1 on the wire
        n_moves, n_on = len(move_targets(be)), sum(1 for r in wire(be, Cmd.SET_VALID) if r.frame[6] == 1)
        be.sim.act("inject", fault="link_silence", duration_ms=3000)
        st = run_to_end(be, 20_000)
        assert st.state == "ABORTED" and st.end_reason == "LINK_LOST", st
        h.advance(2000)
        assert not be.device.last_flags & 1                                       # VALID 0 on the board again
        assert len(move_targets(be)) == n_moves
        assert sum(1 for r in wire(be, Cmd.SET_VALID) if r.frame[6] == 1) == n_on
    finally:
        be.shutdown()


@pytest.mark.req("SW-REP-002")
def test_step_results_carry_t_reached(tmp_path) -> None:
    """SWC-M4-04: StepResult.t_reached_s = device time of t_reached (TRAVEL: MOVE_DONE; LOAD: last trim sample)."""
    be = ready(tmp_path)
    try:
        start(be, load_seq(100.0))
        st = run_to_end(be)
        assert st.state == "FINISHED"
        for r in be.sequencer.results():
            assert r.t_reached_s is not None and r.window_s[0] == pytest.approx(r.t_reached_s + 0.5, abs=1e-6)
        from bend_stand.core.report import load_result
        rep = load_result(st.report_folder)
        assert [r.t_reached_s for r in rep.results] == [r.t_reached_s for r in be.sequencer.results()]
    finally:
        be.shutdown()
