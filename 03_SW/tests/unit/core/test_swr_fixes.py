"""Implementer B's tests of the SW code-review fixes (SW_code_review v1.1, D-53; F's reproducers are in
``tests/validation/test_v_review.py``): liveness reactions (SWR-01/20), per-reaction isolation (SWR-02), the
D-53 a load-limit rules (SWR-03), the priority path without the channel lock (SWR-15), robust loaders (SWR-25),
shutdown ordering (SWR-07/26), and the S4 items SWR-17/18/19/21/22/28.

Verifies: SAF-SW-001, SAF-SW-003, SW-STOP-003, SW-LIM-003, SW-STOP-001, IF-011, SW-ACQ-002, SW-REP-001,
SW-CFG-001, SW-SEQ-002
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from bbs_support import lockstep_backend, tx_frames

from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import GateId

Cmd = pg.Cmd


def _moving_ready():
    be = lockstep_backend(no_specimen=True)
    h = be.test_hooks
    assert be.motion.enable().ok
    assert h.run_until(lambda: be.status().motion.enabled, 2000)
    h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}))
    h.result(be.motion.home(load_confirmed=True), 60_000)
    assert h.run_until(lambda: be.status().motion.homed and not be.status().motion.moving, 2000)
    return be


# ================================================================================================ SWR-01 / 20
@pytest.mark.req("SAF-SW-001", "SAF-SW-003")
def test_liveness_fault_stops_motion_only_while_moving() -> None:
    be = _moving_ready()
    try:
        h = be.test_hooks
        n0 = len(tx_frames(be, Cmd.STOP))
        be.liveness.record_fault("worker", "uncaught RuntimeError: test")             # idle: warning only
        h.advance(20)
        assert len(tx_frames(be, Cmd.STOP)) == n0
        assert any("liveness fault worker" in str(r.payload) for r in be.events.history("log"))
        be.motion.move_to(80.0, speed_mm_s=5.0)
        assert h.run_until(lambda: be.status().motion.moving, 2000)
        be.liveness.record_fault("worker", "uncaught RuntimeError: test")             # moving: priority STOP
        h.advance(20)
        stops = tx_frames(be, Cmd.STOP)
        assert len(stops) == n0 + 1 and stops[-1].frame[6] == int(pg.StopMode.IMMEDIATE)
        assert h.run_until(lambda: not be.status().motion.moving, 2000)
    finally:
        be.shutdown()


@pytest.mark.req("SAF-SW-001", "SAF-SW-003")
def test_pipeline_stall_one_fault_per_episode_and_supervisor_keeps_ticking() -> None:
    """SWR-01: one STOP per stall episode (not one per tick); SWR-20: a stalled component does not stop the rest of
    the Supervisor body (seq tick, jog refresh, liveness check, its own beat)."""
    be = _moving_ready()
    try:
        h = be.test_hooks
        be.motion.move_to(80.0, speed_mm_s=5.0)
        assert h.run_until(lambda: be.status().motion.moving, 2000)
        n0 = len(tx_frames(be, Cmd.STOP))
        h.stall_thread("pipeline", 1000)
        h.advance(900)
        assert be.liveness.age_ms("supervisor") is not None and be.liveness.age_ms("supervisor") < 5
        assert len(tx_frames(be, Cmd.STOP)) == n0 + 1                                  # once for the episode
        assert any(f.thread == "pipeline" and "not evaluated" in f.reason for f in be.liveness.faults())
        h.advance(300)
        assert "pipeline" not in be._liveness_bad  # noqa: SLF001                     # episode over
    finally:
        be.shutdown()


# ================================================================================================ SWR-02
@pytest.mark.req("SW-STOP-003", "SAF-SW-001")
def test_a_failing_event_reaction_does_not_skip_terminate_all(monkeypatch) -> None:
    be = _moving_ready()
    try:
        h = be.test_hooks

        def broken(_ev):
            raise RuntimeError("device reaction defect")

        monkeypatch.setattr(be.device, "handle_fw_event", broken)
        from bend_stand.core.sequencer.model import Sequence, Step, StepKind
        assert be.sequencer.start(Sequence(steps=[Step("a", StepKind.TRAVEL, 40.0, speed_mm_s=2.0)]),
                                  confirmed=True).ok
        assert h.run_until(lambda: be.status().motion.moving, 3000)
        be.sim.act("estop", open=True)                                                 # only the EVENT path ends it
        assert h.run_until(lambda: be.sequencer.status().state == "ABORTED", 2000), be.sequencer.status()
        assert be.pipeline.counters.dispatch_errors >= 1
    finally:
        monkeypatch.undo()
        be.shutdown()


# ================================================================================================ SWR-03
@pytest.mark.req("SW-LIM-003", "SAF-SW-001")
def test_both_load_limits_off_refused_outside_no_specimen_and_restored_from_a_file(tmp_path) -> None:
    from bend_stand.core import session as S
    from bend_stand.core.model import SessionSettings

    be = lockstep_backend()
    try:
        lim = be.limits.get()
        issues = be.limits.set(replace(lim, pull_enabled=False, push_enabled=False))
        assert "LOAD_LIMITS_BOTH_OFF" in {i.code for i in issues} and be.limits.get().pull_enabled
        p = tmp_path / "s.bbsession.json"
        S.save(p, replace(SessionSettings(), limits=replace(lim, pull_enabled=False, push_enabled=False)))
        s, issues = S.load(p)
        assert s.limits.pull_enabled and s.limits.push_enabled
        assert [i.code for i in issues] == ["LOAD_LIMITS_RESTORED"]
        loaded = be.session.load(str(p))                                               # explicit load: restored too
        assert loaded.limits.pull_enabled and loaded.limits.push_enabled
        assert "LOAD_INPUT_INVALID" in be.status().gates[GateId.MOVE].codes()           # no calibration / tare
    finally:
        be.shutdown()


# ================================================================================================ SWR-15
@pytest.mark.req("SW-STOP-001", "IF-011")
def test_stop_is_written_while_another_thread_writes_lane_frames() -> None:
    """SWR-15: frames are written outside the channel lock — a STOP from another thread does not wait for the lane
    frames queued behind the frame being written (before: all of them were written under the lock first)."""
    from bend_stand.core.link import CommandChannel, FrameWriter, Lane

    order: list[int] = []
    in_write, release = threading.Event(), threading.Event()

    class _Tr:
        def write(self, b: bytes) -> None:
            ftype = b[2]
            if ftype != int(Cmd.STOP) and not in_write.is_set():
                in_write.set()
                release.wait(2.0)
            order.append(ftype)

    ch = CommandChannel(FrameWriter(_Tr()), seed=3)

    def burst() -> None:
        for _ in range(3):
            ch.submit(int(Cmd.PING), lane=Lane.GENERAL)

    a = threading.Thread(target=burst)
    a.start()
    assert in_write.wait(2.0)
    res: list[object] = []
    b = threading.Thread(target=lambda: res.append(ch.send_priority(int(Cmd.STOP), bytes([0]))))
    b.start()
    time.sleep(0.05)
    release.set()
    a.join(2.0)
    b.join(2.0)
    assert order[0] == int(Cmd.PING) and order[1] == int(Cmd.STOP), order
    fut, t_write, err = res[0]
    assert err is None and t_write is not None


# ================================================================================================ SWR-17 / 18 / 19
@pytest.mark.req("SAF-SW-001")
def test_safety_warnings_are_an_immutable_snapshot() -> None:
    be = lockstep_backend()
    try:
        assert isinstance(be.safety.warnings, frozenset)
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-001")
def test_undecodable_status_resolves_the_move_ticket(monkeypatch) -> None:
    import bend_stand.core.motion as M

    be = _moving_ready()
    try:
        h = be.test_hooks
        be.sim.act("inject", fault="drop_next", cmd="MOVE_ABS", what="response")
        real = M.P.decode_status

        def bad(_body):
            raise ValueError("bad STATUS")

        monkeypatch.setattr(M.P, "decode_status", bad)
        t = be.motion.move_to(0.5, speed_mm_s=5.0)
        assert h.run_until(t.done, 5000)
        monkeypatch.setattr(M.P, "decode_status", real)
        out = t.result()
        assert out.kind in ("NOT_EXECUTED", "DONE")
        if out.kind == "NOT_EXECUTED":
            assert "undecodable" in out.text or "unknown" in out.text
    finally:
        monkeypatch.undo()
        be.shutdown()


@pytest.mark.req("SAF-SW-005", "SW-LIM-001")
def test_another_board_after_reconnect_resets_the_test_zero() -> None:
    be = _moving_ready()
    try:
        h = be.test_hooks
        h.result(be.motion.move_to(5.0, speed_mm_s=10.0), 10_000)
        be.motion.set_test_zero()
        assert be.motion.x_zero_mm == pytest.approx(5.0)
        be.device.board_changed = True
        be._on_synced()  # noqa: SLF001
        h.advance(20)
        assert be.motion.x_zero_mm == 0.0 and not be.device.board_changed
        assert be.events.history("device.board_changed")
    finally:
        be.shutdown()


# ================================================================================================ SWR-21 / 22
@pytest.mark.req("SW-REP-001")
def test_reports_use_their_own_worker_on_the_real_clock() -> None:
    from bend_stand.core.backend import Backend, BackendSettings

    rt = Backend(BackendSettings())
    try:
        assert rt.report_worker is not rt.worker
        f = rt._report_job(lambda: (yield None))  # noqa: SLF001
        assert f is not None
    finally:
        rt.report_worker.stop()
        rt.worker.stop()
    ls = lockstep_backend(connect=False)
    try:
        f = ls._report_job(lambda: (yield None))  # noqa: SLF001
        ls.test_hooks.advance(5)
        assert f.done()
    finally:
        ls.shutdown()


@pytest.mark.req("SW-CFG-001")
def test_out_of_range_board_values_refuse_motion_and_nan_metadata_refuses_recording(monkeypatch) -> None:
    from bend_stand.core.params import ParamStore

    st = ParamStore()
    st.set_all({"motion.steps_per_mm": float("nan"), "afe.rate_sps": 1})
    assert list(st.invalid()) == ["motion.steps_per_mm"]
    st.update("motion.steps_per_mm", 800.0)
    assert st.invalid() == {}
    be = _moving_ready()
    try:
        be.device.params.update("motion.steps_per_mm", float("nan"))
        g = be.status().gates[GateId.MOVE]
        assert not g.ok and "PARAM_INVALID" in g.codes()
        be.device.params.update("motion.steps_per_mm", 800.0)

        def nan_start(*_a, **_k):
            raise ValueError("Out of range float values are not JSON compliant")

        monkeypatch.setattr(be.recorder, "start", nan_start)
        g = be.record_start()
        assert not g.ok and "metadata not writable" in g.text()
    finally:
        monkeypatch.undo()
        be.shutdown()


# ================================================================================================ SWR-25
@pytest.mark.req("SW-LIM-003")
def test_broken_session_file_is_kept_as_bad() -> None:
    p = Path(os.environ["BEND_STAND_DATA_DIR"]) / "sessions" / "default.bbsession.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[" * 50_000 + "]" * 50_000, encoding="utf-8")
    be = lockstep_backend(connect=False)
    try:
        assert (p.parent / "default.bbsession.json.bad").is_file() and not p.exists()
        assert any(i.code == "FILE" and ".bad" in i.text for i in be.session.load_issues)
        be.session.set(replace(be.session.get(), manual_speed_mm_s=2.0))             # auto-save: a new file
        assert json.loads(p.read_text(encoding="utf-8"))["manual_speed_mm_s"] == 2.0
        assert (p.parent / "default.bbsession.json.bad").read_text(encoding="utf-8").startswith("[[[")
    finally:
        be.shutdown()


# ================================================================================================ SWR-07 / 26
@pytest.mark.req("SW-ACQ-002", "SW-REP-001")
def test_shutdown_mid_sequence_ends_it_first_and_keeps_the_run_log() -> None:
    from bend_stand.core.report import Recording
    from bend_stand.core.sequencer.model import Sequence, Step, StepKind

    be = _moving_ready()
    h = be.test_hooks
    seq = Sequence(travel_ref="machine", steps=[Step("a", StepKind.TRAVEL, 10.0, speed_mm_s=5.0, capture_s=0.3),
                                                Step("b", StepKind.TRAVEL, 40.0, speed_mm_s=5.0, capture_s=0.3)])
    assert be.sequencer.start(seq, confirmed=True).ok
    assert h.run_until(lambda: be.sequencer.status().exec_idx == 1 and be.status().motion.moving, 30_000)
    folder = be.sequencer.status().recording_folder
    tr = be.device.transport
    n_moves = sum(1 for r in tr.wire_log if r.direction == "TX" and r.frame[2] == int(Cmd.MOVE_ABS))
    be.shutdown()
    assert sum(1 for r in tr.wire_log if r.direction == "TX" and r.frame[2] == int(Cmd.MOVE_ABS)) == n_moves
    meta = json.loads((Path(folder) / "meta.json").read_text(encoding="utf-8"))
    run = meta["sequence_runs"][0]
    assert run["state"] == "ABORTED" and run["end_reason"] == "shutdown" and meta["closed_during_sequence"]
    assert Recording(folder).t.size > 0 and not (Path(folder) / "report.json").exists()


# ================================================================================================ SWR-28
@pytest.mark.req("SW-SEQ-002")
def test_endless_loop_run_log_is_bounded(monkeypatch) -> None:
    from bend_stand.core.sequencer import executor as X
    from bend_stand.core.sequencer.model import Loop, Sequence, Step, StepKind

    monkeypatch.setattr(X, "RUN_LOG_MAX_EVENTS", 50)
    monkeypatch.setattr(X, "RUN_LOG_MAX_ITEMS", 5)
    be = _moving_ready()
    try:
        h = be.test_hooks
        seq = Sequence(travel_ref="machine", steps=[Step("a", StepKind.TRAVEL, 1.0, speed_mm_s=10.0, capture_s=0.1),
                                                    Step("b", StepKind.TRAVEL, 0.5, speed_mm_s=10.0, capture_s=0.1)],
                       loops=[Loop(0, 1, 0)])
        assert be.sequencer.start(seq, confirmed=True).ok
        assert h.run_until(lambda: be.sequencer.status().windows_done >= 12, 60_000)
        be.sequencer.stop()
        assert h.run_until(lambda: be.sequencer.status().phase == "END", 10_000)
        run = be.seq.run
        assert len(run.windows) == 5 and len(run.results) == 5 and len(run.events) == 50
        log = be.seq.run_log(run)
        assert log["truncated"]["windows"] >= 7 and log["truncated"]["events"] > 0
    finally:
        be.shutdown()


@pytest.mark.req("SAF-SW-003")
def test_whole_process_stall_is_not_a_pipeline_fault() -> None:
    """A GC-like pause of the Pipeline and the Supervisor together (frames keep arriving) is not a Pipeline stall:
    after the pause the Pipeline catches up and no liveness STOP is sent (OBS-P3-05 robustness of SWR-01)."""
    be = _moving_ready()
    try:
        h = be.test_hooks
        be.motion.move_to(80.0, speed_mm_s=5.0)
        assert h.run_until(lambda: be.status().motion.moving, 2000)
        n0 = len(tx_frames(be, Cmd.STOP))
        h.stall_thread("pipeline", 500)
        h.stall_thread("supervisor", 500)
        h.advance(800)
        assert len(tx_frames(be, Cmd.STOP)) == n0 and be.status().motion.moving
        assert not [f for f in be.liveness.faults() if f.thread == "pipeline"]
    finally:
        be.shutdown()


# ================================================================================================ OBS-P3-04 logs
@pytest.mark.req("NFR-004")
def test_diagnostic_logs_are_small_outside_test_runs() -> None:
    from bend_stand.core.backend import LOG_LEN_SMALL, RX_LOG_LEN_TEST, SIM_LOG_LEN_TEST, Backend, BackendSettings
    from bend_stand.core.events import EventBus

    small = Backend(BackendSettings(clock="lockstep", test_hooks=True, test_logs=False))
    try:
        small.start()
        small.test_hooks.result(small.connect_async("sim"), 5000)
        assert small.device.reader.rx_log.maxlen == LOG_LEN_SMALL
        assert small.sim_endpoint.board.wire_log.maxlen == LOG_LEN_SMALL == small.sim_endpoint.board.sent_log.maxlen
    finally:
        small.shutdown()
    full = lockstep_backend()                                    # test_hooks → full logs (validation fixtures)
    try:
        assert full.device.reader.rx_log.maxlen == RX_LOG_LEN_TEST
        assert full.sim_endpoint.board.sent_log.maxlen == SIM_LOG_LEN_TEST
    finally:
        full.shutdown()
    bus = EventBus()
    for i in range(3000):
        bus.publish("seq.status", i)
    bus.publish("log", {"text": "rare"})
    assert len(bus.history("seq.status")) == 2000 and bus.history("log")[0].payload["text"] == "rare"
    assert [r.topic for r in bus.history()][-2:] == ["seq.status", "log"]          # publish order kept


# ================================================================================================ SWR-32 / 35 / 36
@pytest.mark.req("SW-STOP-001", "IF-011")
def test_tx_error_callback_may_issue_a_stop_without_deadlock() -> None:
    from bend_stand.core.link import CommandChannel, FrameWriter

    class _Tr:
        def write(self, b: bytes) -> None:
            if b[2] == int(Cmd.MOVE_ABS):
                raise OSError("port gone")

    ch = CommandChannel(FrameWriter(_Tr()), seed=5)
    done = threading.Event()

    def on_err(_exc) -> None:
        ch.bump_epoch()                                          # a stop path from inside the callback
        ch.send_priority(int(Cmd.STOP), bytes([0]))
        done.set()

    ch.on_tx_error = on_err
    t = threading.Thread(target=lambda: ch.submit(int(Cmd.MOVE_ABS), bytes(12), epoch=ch.motion_epoch))
    t.start()
    t.join(2.0)
    assert done.is_set() and not t.is_alive()


@pytest.mark.req("SW-STOP-001")
def test_shutdown_stops_a_just_started_jog() -> None:
    be = _moving_ready()
    tr = be.device.transport
    be.motion.jog_start(1, 2.0)
    assert be.motion.jogging
    be.shutdown()
    assert any(r.direction == "TX" and r.frame[2] == int(Cmd.STOP) for r in tr.wire_log)


@pytest.mark.req("SAF-SW-005")
def test_incident_log_records_threshold_failures_and_lost_events(caplog) -> None:
    import logging

    from bend_stand.core.events import EventBus
    from bend_stand.core.logfile import attach_incident_log
    from bend_stand.core.model import ThresholdState

    bus = EventBus()
    attach_incident_log(bus)
    caplog.set_level(logging.INFO)
    bus.publish("safety.thresholds", ThresholdState("FAILED", text="read-back differs"))
    bus.publish("safety.thresholds", ThresholdState("FAILED"))                    # no repeat
    bus.log("3 FW EVENT(s) lost (EVENT SEQ 1 → 5)", logging.WARNING, kind="FW_EVENTS_LOST")
    bus.log("ordinary text", logging.WARNING)
    inc = [r for r in caplog.records if r.name.endswith("incident")]
    msgs = [r.getMessage() for r in inc]
    assert sum("FW load thresholds FAILED" in m for m in msgs) == 1
    assert any(r.levelno == logging.ERROR and "thresholds FAILED" in r.getMessage() for r in inc)
    assert any("FW EVENT(s) lost" in m for m in msgs) and not any("ordinary text" in m for m in msgs)
