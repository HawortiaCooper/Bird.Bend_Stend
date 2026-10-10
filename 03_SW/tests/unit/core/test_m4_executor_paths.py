"""M4 sequence executor — error, stop / abort and restore paths on the lock-step simulator (SW_design §10.3–§10.5,
§22c; coverage task after the M4 gate): the step-timeout guard end to end (simulator ``inject step_stall``),
link loss / internal errors / NACKs / local refusals / unexpected MOVE_DONE reasons in every motion phase, stop /
abort / terminate in every phase, pause / resume refusal and confirmation paths, load-step refusals (no force, no
samples, trim at the travel bound, k_est fallback), tare-step failures, report failures, and the status / result
cleanups (label + phase published together, ``t_reached`` of a step ended before its capture window).

Verifies: SW-SEQ-002, SW-SEQ-003, SW-SEQ-004, SW-SEQ-006, SW-SEQ-007, SW-STOP-003, SW-STOP-004, SW-ACQ-004,
SW-SCH-002, SW-REP-002
"""
from __future__ import annotations

from concurrent.futures import Future

import pytest
from seq_rig import X_ZERO_MM, load_seq, move_targets, mul_frames, ready, run_to_end, start, stop_modes, wire

from bend_stand.calc import trim as T
from bend_stand.core import protocol_gen as pg
from bend_stand.core.errors import GateRefused, LinkError
from bend_stand.core.model import GateItem, GateResult, Severity
from bend_stand.core.report import Recording
from bend_stand.core.sequencer import executor as X
from bend_stand.core.sequencer.model import Sequence, Step, StepKind

Cmd = pg.Cmd
EV = pg.Event
END = ("FINISHED", "STOPPED", "ABORTED", "ERROR")


def _st(be):
    return be.sequencer.status()


def _phase(be, phase: str, ms: float = 60_000, exec_idx: int | None = None) -> None:
    assert be.test_hooks.run_until(lambda: _st(be).phase == phase and (exec_idx is None or
                                                                      _st(be).exec_idx == exec_idx), ms), _st(be)


def _travel(*targets: float, speed: float = 2.0, **kw) -> Sequence:
    return Sequence(steps=[Step(f"t{i}", StepKind.TRAVEL, float(x), speed_mm_s=speed, **kw)
                           for i, x in enumerate(targets)])


def _refused(codes: tuple[str, ...]) -> Future:
    f: Future = Future()
    f.set_exception(GateRefused(GateResult(tuple(GateItem(c, Severity.REFUSE, f"refused {c}") for c in codes))))
    return f


# ================================================================================================ TIMEOUT (stall)
@pytest.mark.req("SW-SEQ-007")
def test_step_timeout_guard_end_to_end_with_a_stalled_move(tmp_path) -> None:
    """The simulator freezes the step output of the running TRAVEL move (``inject step_stall``, no other stop cause):
    no MOVE_DONE → after 1.2 · T_planned + 10 s the guard sends the priority STOP and the sequence ends STOPPED
    (TIMEOUT); the step is flagged, nothing else is commanded, the report lists the guard stop."""
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        seq = _travel(5.0, 0.0, speed=2.0)
        start(be, seq)
        assert h.run_until(lambda: be.status().motion.moving and _st(be).phase == "MOVING", 3000)
        h.advance(300)
        r = be.sim.act("inject", fault="step_stall")                          # until the move ends
        assert r == {"ok": True, "armed": False}
        t_stall = be.clock.monotonic_ns()
        n_moves = len(move_targets(be))
        lim = be.motion.limits()
        timeout_s = T.step_timeout_s(5.0, 2.0, lim.a_max_mm_s2)                # the guard's own formula (§10.5)
        h.advance(1000 * (timeout_s - 0.3) - 1000)
        assert _st(be).state == "RUNNING" and be.status().motion.moving and not stop_modes(be)
        st = run_to_end(be)
        t_end = be.clock.monotonic_ns()
        assert st.state == "STOPPED" and st.end_reason == "TIMEOUT", st
        assert "no MOVE_DONE within" in st.message and stop_modes(be) == [int(pg.StopMode.IMMEDIATE)]
        assert (t_end - t_stall) / 1e9 >= timeout_s - 0.3 - 0.05
        assert len(move_targets(be)) == n_moves                                    # no next step, no retry
        res = be.sequencer.results()
        assert [x.uid for x in res] == ["t0"] and "TIMEOUT" in res[0].flags and res[0].t_reached_s is None
        assert not be.status().safety.trips                                        # not a SW-limit trip
        rep = Recording(st.recording_folder)
        assert any("SEQ_GUARD" in e[2] and "TIMEOUT" in e[2] for e in rep.events)
        from bend_stand.core.report import load_result
        assert "sequence stopped by TIMEOUT" in load_result(st.report_folder).warnings
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007")
def test_capture_start_without_data_ends_timeout_with_t_reached(tmp_path, monkeypatch) -> None:
    """A dwell that does not get its device-time sample before the host deadline (3 × planned + 10 s) ends TIMEOUT;
    the step had reached its target, so the failed result carries ``t_reached_s`` (M4 note: aborted in SETTLE)."""
    be = ready(tmp_path, contact_mm=None)
    try:
        monkeypatch.setattr(T, "hold_timeout_s", lambda dwell_s: 0.0)
        start(be, _travel(1.0, speed=5.0, settle_s=0.5, capture_s=0.5))
        st = run_to_end(be)
        assert st.end_reason == "TIMEOUT" and "no data while waiting for the capture start" in st.message
        r = be.sequencer.results()[-1]
        assert "TIMEOUT" in r.flags and r.t_reached_s is not None and r.n == 0
    finally:
        be.shutdown()


# ================================================================================================ status cleanup
@pytest.mark.req("SW-SCH-002", "SW-SEQ-003")
def test_status_never_pairs_a_new_label_with_the_previous_phase(tmp_path) -> None:
    """M4 note (v1.1 §7.2): at a step change the published ``SeqStatus`` carries the new step's label together with
    its own first phase (COMMAND), never with the previous step's SETTLE / CAPTURE / HOLD."""
    be = ready(tmp_path, contact_mm=None)
    try:
        seq = Sequence(steps=[Step("a", StepKind.TRAVEL, 1.0, speed_mm_s=5.0, settle_s=0.3, capture_s=0.3,
                                   label="A"),
                              Step("b", StepKind.TRAVEL, 2.0, speed_mm_s=5.0, settle_s=0.3, capture_s=0.3,
                                   label="B"),
                              Step("h", StepKind.HOLD, settle_s=0.2, capture_s=0.2, label="H"),
                              Step("m", StepKind.MARK, label="M")])
        n0 = len(be.events.history("seq.status"))
        start(be, seq)
        assert run_to_end(be).state == "FINISHED"
        sts = [r.payload for r in be.events.history("seq.status")[n0:]]
        changes = [(a, b) for a, b in zip(sts, sts[1:]) if b.exec_idx is not None and a.exec_idx != b.exec_idx]
        assert len(changes) == 4
        for _a, b in changes:
            assert b.phase == "COMMAND" and b.label == seq.steps[b.step_idx].label, b
        for s in sts:
            if s.phase in ("SETTLE", "CAPTURE", "HOLD", "MOVING") and s.step_idx is not None:
                assert s.label == seq.steps[s.step_idx].label
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007", "SW-REP-002")
def test_break_in_settle_result_carries_t_reached(tmp_path) -> None:
    """D-49 a break while the load step settles (before its window): the failed result has ``t_reached_s`` = the
    reached time of the step (``SEQ_REACHED``), the report lists it."""
    be = ready(tmp_path)
    try:
        h = be.test_hooks
        start(be, load_seq(150.0, settle=2.0, capture=1.0, ret=False))
        _phase(be, "SETTLE", exec_idx=1)
        h.advance(400)
        be.sim.act("specimen", kind="none")
        st = run_to_end(be)
        assert st.end_reason == "BREAK_DETECTED", st
        r = be.sequencer.results()[-1]
        assert r.uid == "l0" and "BREAK_DETECTED" in r.flags and r.n == 0
        reached = [e for e in Recording(st.recording_folder).events if e[2].startswith("SEQ_REACHED")]
        t_r = int(reached[-1][2].split("t_reached=")[1].split()[0])
        assert r.t_reached_s == pytest.approx(t_r / 1e6, abs=1e-9)
        from bend_stand.core.report import load_result
        rep = load_result(st.report_folder).results[-1]
        assert rep.t_reached_s == r.t_reached_s and "BREAK_DETECTED" in rep.flags
    finally:
        be.shutdown()


# ================================================================================================ refusals / NACKs
@pytest.mark.req("SW-SEQ-007", "SW-STOP-004")
def test_refused_paused_without_pause_indication_stops_instead_of_hanging(tmp_path) -> None:
    """MOVE_ABS NACKed with BLOCK PAUSED (D-33 k) but no PAUSED indication follows within 2 s: the step ends
    STOPPED (STEP_REFUSED) — the run must not wait for a Resume that the gate can never accept."""
    be = ready(tmp_path, contact_mm=None)
    try:
        n0 = len(move_targets(be))
        be.sim.inject_nack("MOVE_ABS", int(pg.Status.E_STATE), int(pg.Block.PAUSED))
        start(be, _travel(2.0))
        st = run_to_end(be, 10_000)
        assert st.state == "STOPPED" and st.end_reason == "STEP_REFUSED" and "BLOCK PAUSED" in st.message
        assert len(move_targets(be)) == n0 + 1
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-004")
def test_refused_paused_then_paused_reissues_after_resume(tmp_path) -> None:
    """D-33 k: the re-issue is refused with BLOCK PAUSED because the operator paused (PAUSE button) in between →
    the sequence stays PAUSED (step unchanged); Resume re-issues the same absolute target and completes."""
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        n0 = len(move_targets(be))
        be.sim.inject_nack("MOVE_ABS", int(pg.Status.E_STATE), int(pg.Block.PAUSED))
        start(be, _travel(2.0))
        assert h.run_until(lambda: len(move_targets(be)) == n0 + 1, 3000)
        h.advance(200)
        be.sim.act("button", name="pause", pressed=True)
        h.advance(100)
        be.sim.act("button", name="pause", pressed=False)
        assert h.run_until(lambda: _st(be).state == "PAUSED", 3000), _st(be)
        assert _st(be).exec_idx == 0
        h.advance(300)
        assert be.resume().ok
        st = run_to_end(be)
        assert st.state == "FINISHED" and move_targets(be)[n0:] == [int((X_ZERO_MM + 2.0) * 1000)] * 2
        assert len(wire(be, Cmd.RESUME)) == 1
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007")
def test_move_nack_and_local_gate_refusals_end_step_refused(tmp_path, monkeypatch) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        be.sim.inject_nack("MOVE_ABS", int(pg.Status.E_RANGE), 0)                   # FW NACK (not PAUSED)
        start(be, _travel(2.0))
        st = run_to_end(be, 10_000)
        assert st.end_reason == "STEP_REFUSED" and "move not executed: REFUSED" in st.message
        for codes, ms in ((("SW_TRIP",), 1000), (("PAUSED",), 4000)):              # refused locally (nothing sent)
            n = len(move_targets(be))
            monkeypatch.setattr(be.motion, "move_to", lambda *a, codes=codes, **k: _refused(codes))
            start(be, _travel(2.0))
            st = run_to_end(be, ms)
            assert st.end_reason == "STEP_REFUSED" and f"refused {codes[0]}" in st.message
            assert len(move_targets(be)) == n
            monkeypatch.undo()
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007", "SW-STOP-003")
@pytest.mark.parametrize("exc, state, reason", [(LinkError("wire gone"), "ABORTED", "LINK_LOST"),
                                                (RuntimeError("defect"), "ERROR", "ERROR")])
def test_link_error_and_internal_error_in_a_step(tmp_path, monkeypatch, exc, state, reason) -> None:
    """§10.3: a LinkError ends the run ABORTED / LINK_LOST (nothing more sent); any other exception is a defect →
    ERROR and a controlled STOP."""
    be = ready(tmp_path, contact_mm=None)
    try:
        def boom(*_a, **_k):
            raise exc

        monkeypatch.setattr(be.motion, "move_to", boom)
        n_stop = len(stop_modes(be))
        start(be, _travel(2.0))
        st = run_to_end(be, 10_000)
        assert (st.state, st.end_reason) == (state, reason), st
        stops = stop_modes(be)[n_stop:]
        assert stops == ([] if state == "ABORTED" else [int(pg.StopMode.CONTROLLED)])
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007")
@pytest.mark.parametrize("phase, kind", [("MOVING", "travel_bound"), ("MOVING", "travel_stopped"),
                                         ("APPROACH", "approach_target"), ("HOME", "home_bound")])
def test_unexpected_move_done_reasons_end_the_step(tmp_path, phase, kind) -> None:
    """A MOVE_DONE that does not fit the step (TRAVEL ended BOUND, STOPPED without a terminating stop event,
    approach ended TARGET, homing ended BOUND) ends the sequence STOPPED with the reason; nothing more is sent."""
    be = ready(tmp_path, contact_mm=None if kind != "approach_target" else 5.0)
    try:
        h = be.test_hooks
        if kind == "approach_target":
            seq = load_seq(150.0, ret=False)
        elif kind == "home_bound":
            seq = Sequence(steps=[Step("H", StepKind.HOME)])
        else:
            seq = _travel(20.0, speed=2.0)
        g = be.sequencer.start(seq, confirmed=True)
        assert g.ok, g.items
        _phase(be, phase, 20_000)
        h.advance(200)
        reason = {"travel_bound": pg.MoveDoneReason.BOUND, "travel_stopped": pg.MoveDoneReason.STOPPED,
                  "approach_target": pg.MoveDoneReason.TARGET, "home_bound": pg.MoveDoneReason.BOUND}[kind]
        b = be.sim.board
        be.sim.emit_event(int(EV.MOVE_DONE), int(reason), b.pos_um, b.pos_steps)
        st = run_to_end(be, 10_000)
        exp = {"travel_bound": ("STEP_REFUSED", "travel step ended BOUND"),
               "travel_stopped": ("STEP_REFUSED", "move stopped"),
               "approach_target": ("STEP_REFUSED", "approach ended TARGET"),
               "home_bound": ("HOME_FAILED", "homing ended BOUND")}[kind]
        assert st.state == "STOPPED" and st.end_reason == exp[0] and exp[1] in st.message, st
        be.stop("test")
        h.advance(500)
    finally:
        be.shutdown()


# ================================================================================================ start / prepare
@pytest.mark.req("SW-SEQ-005", "SW-ACQ-004")
def test_recording_cannot_start_ends_error(tmp_path, monkeypatch) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        n0 = len(move_targets(be))
        monkeypatch.setattr(be, "record_start_with", lambda *_a, **_k: GateResult(
            (GateItem("FILE", Severity.REFUSE, "folder not writable"),)))
        start(be, _travel(1.0))
        st = run_to_end(be, 5000)
        assert st.state == "ERROR" and st.end_reason == "RECORDING" and "folder not writable" in st.message
        assert len(move_targets(be)) == n0
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007", "SW-STOP-003")
def test_alarm_and_terminate_before_the_first_step(tmp_path) -> None:
    """ALM / termination while the run is still PREPARING: nothing is commanded, no step result, the run ends."""
    be = ready(tmp_path, contact_mm=None)
    try:
        n0 = len(move_targets(be))
        start(be, _travel(1.0))
        be.seq.on_alm()                                                           # before the runner ran
        st = run_to_end(be, 5000)
        assert (st.state, st.end_reason) == ("STOPPED", "DRIVER_ALARM") and be.sequencer.results() == ()
        assert stop_modes(be)[-1] == int(pg.StopMode.CONTROLLED) and len(move_targets(be)) == n0
        start(be, _travel(1.0))
        be.seq.terminate("test")
        st = run_to_end(be, 5000)
        assert (st.state, st.end_reason) == ("ABORTED", "test") and len(move_targets(be)) == n0
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-002")
def test_plan_too_long_runs_without_a_plan(tmp_path) -> None:
    """A sequence whose plan exceeds MAX_PLAN_STEPS (normally refused by the start gate) started directly on the
    executor: no plan (status totals None), the steps still run in loop order and can be stopped."""
    from bend_stand.core.sequencer.model import Loop

    be = ready(tmp_path, contact_mm=None)
    try:
        steps = [Step("a", StepKind.TRAVEL, 1.0, speed_mm_s=5.0), Step("b", StepKind.TRAVEL, 0.0, speed_mm_s=5.0)]
        steps += [Step(f"m{i}", StepKind.MARK) for i in range(19)]
        seq = Sequence(steps=steps, loops=[Loop(0, 20, 10_000)])                 # 210 000 executed steps
        be.seq.start(seq, plan_ctx=be.sequencer.plan_context(seq), home_confirmed=False)
        assert be.test_hooks.run_until(lambda: (_st(be).loop_iters or (0,))[0] >= 2, 20_000)
        st0 = _st(be)
        assert st0.plan_total_s is None and st0.plan_len is None and st0.windows_total is None
        assert st0.remaining_s is None and st0.plan_t_s is None
        be.sequencer.stop()
        st = run_to_end(be, 10_000)
        assert (st.state, st.end_reason) == ("STOPPED", "OPERATOR_STOP")
    finally:
        be.shutdown()


# ================================================================================================ controls
@pytest.mark.req("SW-SEQ-007", "SW-STOP-003")
def test_control_calls_outside_their_state_are_no_ops(tmp_path, monkeypatch) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        ex = be.seq
        x, f = ex.trace()
        assert x.size == 0 and f.size == 0                                         # no run yet
        assert not ex.request_resume("gui") and not ex.paused and not ex.capture_open
        ex.continue_()
        ex.end_cleared()
        ex.on_pause_cleared()
        ex.build_pending_report()
        assert not ex.stop().sent and _st(be).state == "IDLE"
        r = ex.abort("idle")                                                       # HALT only
        assert r.cmd == "HALT"
        h.advance(100)
        h.result(be.clear_stop_async())
        h.advance(100)
        start(be, _travel(3.0, speed=1.0))
        assert h.run_until(lambda: be.status().motion.moving, 3000)

        def broken_stop(*_a, **_k):
            raise RuntimeError("tx failed")

        monkeypatch.setattr(be.device, "stop", broken_stop)
        r = ex.stop()
        assert not r.sent and "tx failed" in r.error                               # never raises
        assert _st(be).state == "STOPPING"
        run = ex.run
        ex.terminate("late")                                                       # already ending: kept
        ex._guard_trip(run, "SLIP", "late")  # noqa: SLF001
        assert run.end[1] == "OPERATOR_STOP" and "SLIP" not in run.fail_flags
        monkeypatch.undo()
        be.stop("test")
        st = run_to_end(be, 10_000)
        assert st.state == "STOPPED" and st.end_reason == "OPERATOR_STOP"
        start(be, _travel(3.0, speed=1.0))
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        ex.shutdown()                                                              # app exit while running
        assert ex.run.end[1] == "shutdown"
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007", "SW-STOP-003")
def test_alm_from_the_data_status_bit_stops_the_run(tmp_path) -> None:
    """D-33 c: ALM seen first in the DATA status (valid feature bit), before / without EVENT ALM_CHANGED."""
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        start(be, _travel(4.0, speed=1.0))
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        be.sim.override_status(set_bits=int(pg.DataStatus.ALM), duration_ms=300)
        st = run_to_end(be, 10_000)
        assert (st.state, st.end_reason) == ("STOPPED", "DRIVER_ALARM")
        assert stop_modes(be)[-1] == int(pg.StopMode.CONTROLLED)
    finally:
        be.shutdown()


@pytest.mark.req("SW-SCH-002")
def test_trace_is_decimated_and_bounded(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(X, "TRACE_MAX", 40)
    be = ready(tmp_path, contact_mm=None)
    try:
        start(be, _travel(1.0, speed=2.0))
        assert run_to_end(be).state == "FINISHED"
        run = be.seq.run
        assert run.trace_stride >= 4 and len(run.trace_x) < 40
        x, f = be.seq.trace(max_points=10)
        assert 0 < x.size <= 10 and x.size == f.size
        x0, _f0 = be.seq.trace(max_points=0)
        assert x0.size == len(run.trace_x)
    finally:
        be.shutdown()


# ================================================================================================ pause / resume
@pytest.mark.req("SW-STOP-004")
def test_pause_in_settle_discards_nothing_and_stop_while_paused(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        start(be, _travel(1.0, speed=5.0, settle_s=1.0, capture_s=0.5))
        _phase(be, "SETTLE")
        be.pause()
        assert h.run_until(lambda: _st(be).state == "PAUSED", 2000)
        h.advance(300)
        assert not [e for e in be.seq.run.events if e["name"] == "SEQ_WINDOW_DISCARDED"]   # no window opened yet
        be.sequencer.stop()                                                       # stop while PAUSED
        st = run_to_end(be, 5000)
        assert (st.state, st.end_reason) == ("STOPPED", "OPERATOR_STOP") and not wire(be, Cmd.RESUME)
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-004")
def test_resume_not_confirmed_then_resumed(tmp_path) -> None:
    """RESUME frame lost (VERIFY → GET_STATUS shows PAUSED) → 'press Resume again', still PAUSED; then the PAUSED
    bit stays set > 500 ms after an OK (override) → still PAUSED; the third Resume completes the step."""
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        start(be, _travel(3.0, speed=1.0))
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        be.pause()
        assert h.run_until(lambda: _st(be).state == "PAUSED", 2000)
        h.advance(1500)
        be.sim.act("inject", fault="drop_next", cmd="RESUME", what="request")
        assert be.resume().ok
        assert h.run_until(lambda: "again" in _st(be).message, 5000), _st(be)
        assert _st(be).state == "PAUSED" and be.device.last_status & int(pg.DataStatus.PAUSED)
        be.sim.override_status(set_bits=int(pg.DataStatus.PAUSED), duration_ms=1500)
        h.advance(30)
        assert be.resume().ok
        assert h.run_until(lambda: _st(be).phase == "PAUSED" and len(wire(be, Cmd.RESUME)) == 2, 3000)
        h.advance(800)
        assert _st(be).state == "PAUSED" and "again" in _st(be).message
        h.advance(1000)
        assert be.resume().ok
        st = run_to_end(be)
        assert st.state == "FINISHED" and len(wire(be, Cmd.RESUME)) == 3
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-004", "SW-STOP-003")
@pytest.mark.parametrize("when", ["in_flight", "confirming"])
def test_termination_while_resuming(tmp_path, monkeypatch, when) -> None:
    """The run is terminated while the RESUME is in flight / while PAUSED = 0 is awaited: it ends ABORTED and no
    motion is re-issued."""
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        start(be, _travel(3.0, speed=1.0))
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        be.pause()
        assert h.run_until(lambda: _st(be).state == "PAUSED", 2000)
        h.advance(1500)
        n = len(move_targets(be))
        orig = be.device.clear_async

        def wrapped(cmd, *a, **k):
            fut = orig(cmd, *a, **k)
            if when == "in_flight":
                be.seq.terminate("during resume")
            else:
                be.sim.override_status(set_bits=int(pg.DataStatus.PAUSED), duration_ms=2000)
                fut.add_done_callback(lambda _f: be.seq.terminate("during resume"))
            return fut

        monkeypatch.setattr(be.device, "clear_async", wrapped)
        assert be.resume().ok
        st = run_to_end(be, 5000)
        assert (st.state, st.end_reason) == ("ABORTED", "during resume") and len(move_targets(be)) == n
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-004")
def test_pause_cleared_outside_the_sequence_stops_it(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        start(be, _travel(3.0, speed=1.0))
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        be.pause()
        assert h.run_until(lambda: _st(be).state == "PAUSED", 2000)
        h.advance(500)
        be.sim.emit_event(int(EV.PAUSE_CLEARED), int(pg.PauseClearedReason.HALT_CLEAR))   # another client
        st = run_to_end(be, 5000)
        assert (st.state, st.end_reason) == ("STOPPED", "PAUSE_CLEARED")
    finally:
        be.shutdown()


# ================================================================================================ load steps
@pytest.mark.req("SW-SEQ-006")
def test_load_step_refused_without_force_and_without_valid_samples(tmp_path) -> None:
    be = ready(tmp_path)
    try:
        h = be.test_hooks
        seq = Sequence(steps=[Step("h", StepKind.HOLD, settle_s=1.5),
                              Step("l", StepKind.LOAD, 100.0, tol_n=2.0, speed_mm_s=1.0)], k_est_n_mm=40.0)
        start(be, seq)
        _phase(be, "HOLD", 5000)
        be.load_input.clear_tare()                                                # tare gone mid-sequence
        be.on_scale_changed()
        st = run_to_end(be, 10_000)
        assert st.end_reason == "STEP_REFUSED" and "without a valid force" in st.message
        assert not mul_frames(be)
    finally:
        be.shutdown()
    be = ready(tmp_path / "b")
    try:
        h = be.test_hooks
        seq = Sequence(steps=[Step("h", StepKind.HOLD, settle_s=2.5),
                              Step("l", StepKind.LOAD, 100.0, tol_n=2.0, speed_mm_s=1.0)], k_est_n_mm=40.0)
        start(be, seq)
        _phase(be, "HOLD", 5000)
        be.sim.act("afe", stall=True)                                             # no AFE data any more
        st = run_to_end(be, 10_000)
        assert st.end_reason == "STEP_REFUSED" and "no valid force samples" in st.message, st
        assert not mul_frames(be)
        be.sim.act("afe", stall=False)
        h.advance(200)
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-006")
def test_trim_without_approach_reaches_the_travel_bound(tmp_path, monkeypatch) -> None:
    """F already inside the approach band (k_est high → wide band): no MOVE_UNTIL_LOAD, the trim starts at once; the
    trim target lies beyond the travel range → NOT_REACHED (not a limit trip), the result is flagged."""
    be = ready(tmp_path)
    try:
        n0 = len(move_targets(be))
        lo, _hi = be.motion.travel_range_mm()
        hi = X_ZERO_MM + 8.6 + 0.0005
        monkeypatch.setattr(be.motion, "travel_range_mm", lambda: (lo, hi))
        seq = Sequence(steps=[Step("s0", StepKind.TRAVEL, 8.6, speed_mm_s=2.0),       # spring: F ≈ 180 N
                              Step("l", StepKind.LOAD, 200.0, tol_n=2.0, speed_mm_s=1.0)], k_est_n_mm=400.0)
        start(be, seq)
        st = run_to_end(be)
        assert (st.state, st.end_reason) == ("STOPPED", "NOT_REACHED") and "travel bound" in st.message, st
        assert not mul_frames(be) and len(move_targets(be)) == n0 + 1
        r = be.sequencer.results()[-1]
        assert "NOT_REACHED" in r.flags and r.t_reached_s is None
        assert not be.status().safety.trips
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-006")
def test_short_approach_keeps_the_sequence_k_est(tmp_path) -> None:
    """An approach shorter than 0.1 mm cannot measure k_est: the sequence value is kept (fallback), the trim still
    reaches the target."""
    be = ready(tmp_path)
    try:
        seq = Sequence(steps=[Step("s0", StepKind.TRAVEL, 8.9, speed_mm_s=2.0),       # spring: F ≈ 195 N
                              Step("l", StepKind.LOAD, 200.0, tol_n=1.0, speed_mm_s=1.0, capture_s=0.3)],
                       k_est_n_mm=40.0)
        start(be, seq)
        st = run_to_end(be)
        assert st.state == "FINISHED", st.message
        assert len(mul_frames(be)) == 1 and st.k_est_n_mm == 40.0
        assert "ON_TARGET" in be.sequencer.results()[-1].flags
    finally:
        be.shutdown()


# ================================================================================================ tare / mark steps
@pytest.mark.req("SW-SEQ-003")
@pytest.mark.parametrize("how", ["refused", "cancelled", "thresholds"])
def test_tare_step_failures_stop_the_sequence(tmp_path, monkeypatch, how) -> None:
    from bend_stand.core.engine import refuse

    be = ready(tmp_path, contact_mm=None)
    try:
        h = be.test_hooks
        seq = Sequence(steps=[Step("m", StepKind.MARK, label="no wait"), Step("t", StepKind.TARE),
                              Step("a", StepKind.TRAVEL, 1.0)])
        n0 = len(move_targets(be))
        if how == "refused":
            monkeypatch.setattr(be.tare_engine, "start_tare", lambda _w: refuse("AFE", "AFE not ready"))
        start(be, seq)
        if how == "thresholds":                                                   # after the start gate
            monkeypatch.setattr(be.device.threshold_mgr, "matches", lambda *_a, **_k: False)
        if how == "cancelled":
            assert h.run_until(lambda: be.tare_engine.active, 10_000)
            be.tare_engine.cancel()
        st = run_to_end(be, 30_000)
        assert (st.state, st.end_reason) == ("STOPPED", "TARE_REFUSED"), st
        assert {"refused": "AFE not ready", "cancelled": "tare failed",
                "thresholds": "thresholds not verified"}[how] in st.message
        assert len(move_targets(be)) == n0
        assert any(e["name"] == "SEQ_MARK" for e in be.seq.run.events)
    finally:
        be.shutdown()


# ================================================================================================ report
@pytest.mark.req("SW-REP-001")
def test_report_failure_keeps_the_run_result(tmp_path, monkeypatch) -> None:
    import bend_stand.core.report as R

    be = ready(tmp_path, contact_mm=None)
    try:
        def broken(*_a, **_k):
            raise OSError("disk gone")

        monkeypatch.setattr(R, "build_report", broken)
        start(be, _travel(1.0, speed=5.0, capture_s=0.2))
        st = run_to_end(be)
        assert st.state == "FINISHED" and st.report_folder is None and be.sequencer.results()
        assert any("report not built: disk gone" in str(r.payload) for r in be.events.history())
    finally:
        be.shutdown()


# ================================================================================================ helpers (fake backend)
class _Clock:
    def __init__(self) -> None:
        self.ns = 0
        self.is_lockstep = True

    def monotonic_ns(self) -> int:
        return self.ns


class _Dev:
    def __init__(self) -> None:
        self.connected = False
        self.fail = 0
        self.calls = 0

    def set_valid_job(self, flag: bool):
        self.calls += 1
        if self.fail:
            self.fail -= 1
            raise LinkError("no answer")
        yield None
        return 1234


class _Be:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.device = _Dev()
        self.rows: list[tuple[str, str]] = []

    def record_event(self, name: str, text: str) -> None:
        self.rows.append((name, text))


def _drive(gen, be: _Be, max_steps: int = 10_000):
    """Run an executor job against the fake backend: Sleeps advance the fake clock."""
    val = None
    for _ in range(max_steps):
        try:
            item = gen.send(val)
        except StopIteration as si:
            return si.value
        val = None
        if isinstance(item, X.Sleep):
            be.clock.ns += item.ns
    raise AssertionError("job did not end")


@pytest.mark.req("SW-SEQ-004", "SW-STOP-003")
def test_valid_clear_after_link_loss_retries_until_the_board_answers() -> None:
    """SWC-M4-03 helper: board not connected → wait; SET_VALID 0 not answered → retry; answered → VALID_OFF row; a
    board that never returns gives up after 60 s (the reconnect hook takes over)."""
    be = _Be()
    ex = X.SequenceExecutor(be)  # type: ignore[arg-type]
    run = X._Run(Sequence(), None, 0.0, 0.0, False, 0)  # noqa: SLF001
    run.valid_on = True
    gen = ex._clear_valid_after_link_loss(run)  # noqa: SLF001
    assert isinstance(next(gen), X.Sleep)                                          # not connected: wait
    be.clock.ns += 250 * X.MS
    be.device.connected, be.device.fail = True, 1
    item = gen.send(None)                                                          # LinkError → retry later
    assert isinstance(item, X.Sleep) and be.device.calls == 1
    be.clock.ns += 250 * X.MS
    _drive(gen, be)
    assert not run.valid_on and be.rows[-1][0] == "VALID_OFF" and be.device.calls == 2
    run2 = X._Run(Sequence(), None, 0.0, 0.0, False, 0)  # noqa: SLF001
    run2.valid_on = True
    be.device.connected = False
    _drive(ex._clear_valid_after_link_loss(run2), be)  # noqa: SLF001
    assert run2.valid_on                                                           # gave up after 60 s
    ex.runner.stop()


@pytest.mark.req("SW-SEQ-004", "SW-STOP-003")
def test_set_valid_link_failure_after_the_end_keeps_the_end_reason() -> None:
    be = _Be()
    ex = X.SequenceExecutor(be)  # type: ignore[arg-type]
    ex._publish = lambda: None  # type: ignore[method-assign]  # noqa: SLF001
    run = X._Run(Sequence(), None, 0.0, 0.0, False, 0)  # noqa: SLF001
    run.end = ("STOPPED", "OPERATOR_STOP", "stopped")
    be.device.fail = 1
    with pytest.raises(X._Ended):  # noqa: SLF001
        _drive(ex._set_valid(run, True), be)  # noqa: SLF001
    assert run.end[1] == "OPERATOR_STOP" and run.valid_on                          # FW may have applied it
    assert ex._unwrap(run, 77) == 77  # noqa: SLF001                                  # no device time yet
    run.last_t_u, run.last_t32 = (1 << 32) + 10, 10
    assert ex._unwrap(run, 0xFFFFFFF0) == (1 << 32) - 16  # noqa: SLF001             # wraps backwards
    ex.runner.stop()
