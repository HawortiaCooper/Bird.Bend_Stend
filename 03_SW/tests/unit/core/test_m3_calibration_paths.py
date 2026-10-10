"""M3 calibration — error, abort and restore paths (coverage task after the M4 gate): the calibration store field
checks and file handling, the load-calibration wizard's input / phase / capture / fit / accept errors, and the
travel-calibration wizard's refusals, failed moves, read-back mismatch, accept failure and the restore rule
(§9.3.1) incl. the reconnect resolution and the operator decisions.

Verifies: SW-CAL-001, SW-CAL-002, SW-CAL-003, SW-CAL-004, SW-CAL-005, SW-CAL-006, SW-CAL-007, SW-CAL-009
"""
from __future__ import annotations

import json
import os
from concurrent.futures import Future
from pathlib import Path

import pytest
from bbs_support import lockstep_backend, tx_frames

from bend_stand.calc.motion import f32
from bend_stand.core import protocol_gen as pg
from bend_stand.core.calibration import store as S
from bend_stand.core.calibration.travel import KEY
from bend_stand.core.capture import CaptureResult
from bend_stand.core.errors import BendStandError, FileFormatError, GateRefused
from bend_stand.core.model import GateItem, GateResult, MoveDone, MoveOutcome, Severity

from .test_m3_calibration import _fast, _wait_phase, calibrate
from .test_m3_travel_cal import WORLD_SPM, _ready, _to_move2, _wait, _wx

Cmd = pg.Cmd
LOAD_REC = {"created_utc": "2026-10-05T10:00:00Z", "sensor": {"serial": "A 1/2"},
            "afe": {"channel": "A", "gain": 128, "rate_sps": 80},
            "points": [{"raw_mean": 50_000.0, "force_n": 0.0}, {"raw_mean": 82_850.0, "force_n": 9.80665}],
            "fit": {"k_n_per_count": 1 / 3285, "status": "PASS"}}


# ================================================================================================ store
@pytest.mark.req("SW-CAL-009", "SW-CAL-004")
def test_store_field_checks(tmp_path) -> None:
    S.validate_load(LOAD_REC)
    bad = [({"fit": {"k_n_per_count": 0.0, "status": "PASS"}}, "non-zero"),
           ({"fit": {"k_n_per_count": float("nan"), "status": "PASS"}}, "non-zero"),
           ({"fit": {"k_n_per_count": 1.0, "status": "FAIL"}}, "cannot be active"),
           ({"points": [LOAD_REC["points"][0]]}, "two points"),
           ({"points": "x"}, "two points"),
           ({"points": [{"raw_mean": 1.0}, {"raw_mean": 2.0}]}, "force_n"),
           ({"afe": {"channel": "A", "gain": 128}}, "rate_sps"),
           ({"fit": None}, "invalid")]
    for patch, needle in bad:
        with pytest.raises(FileFormatError, match=needle):
            S.validate_load({**LOAD_REC, **patch})
    S.validate_travel({"spm2": 800.0})
    for rec in ({}, {"spm2": 0.0}, {"spm2": "x"}, {"spm2": float("inf")}):
        with pytest.raises(FileFormatError, match="travel calibration record invalid"):
            S.validate_travel(rec)


@pytest.mark.req("SW-CAL-009", "SW-CAL-004", "SW-CAL-001")
def test_store_files_unique_names_history_and_invalid_records(tmp_path) -> None:
    st = S.CalibrationStore(tmp_path / "cal")
    assert st.history("load") == [] and st.active_load() is None and st.restore_pending() is None
    st.dir.mkdir()
    p1 = st.save_load(LOAD_REC)
    p2 = st.save_load(LOAD_REC)                                                  # same second → unique name
    assert p1.name == "load_A-1-2_20261005T100000Z.json" and p2.name == "load_A-1-2_20261005T100000Z_2.json"
    os.utime(p1, ns=(1, 1))
    assert [Path(x).name for x in st.history("load")] == [p2.name, p1.name] and st.history("bogus") == []
    assert st.active_load()["file"] == p2.name
    with pytest.raises(FileFormatError):
        st.save_load({**LOAD_REC, "fit": {"k_n_per_count": 0.0}})               # never written
    (st.dir / S.ACTIVE_LOAD).write_text(json.dumps({"schema": "bird.bend.cal.load", "schema_version": 1,
                                                    **LOAD_REC, "points": []}), encoding="utf-8")
    with pytest.raises(FileFormatError):
        st.active_load()                                                         # invalid active copy is reported
    t = st.save_travel({"created_utc": "2026-10-05T10:00:00Z", "spm2": 796.0})
    assert t.name == "travel_20261005T100000Z.json" and st.active_travel()["spm2"] == 796.0
    (st.dir / S.ACTIVE_TRAVEL).write_text(json.dumps({"schema": "bird.bend.cal.travel", "schema_version": 1,
                                                      "spm2": -1}), encoding="utf-8")
    with pytest.raises(FileFormatError):
        st.active_travel()
    (st.dir / S.RESTORE_FILE).write_text("{broken", encoding="utf-8")
    assert st.restore_pending() is None                                          # unreadable record: ignored
    st.delete_restore()
    st.delete_restore()                                                          # already gone: no error
    st.write_restore("UID", 800.0, None, "2026-10-05T10:00:00Z")
    assert st.restore_pending()["spm_trial"] is None


# ================================================================================================ load wizard
@pytest.fixture
def be():
    b = lockstep_backend()
    _fast(b)
    yield b
    b.shutdown()


@pytest.mark.req("SW-CAL-005", "SW-CAL-006")
def test_load_wizard_input_phase_and_capture_errors(be, monkeypatch) -> None:
    lc = be.load_cal
    g = lc.start(n_points=1)
    assert not g.ok and "RANGE" in g.codes() and not lc.active
    assert lc.start(n_points=3).ok
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=0)
    lc.finish_early()
    assert "finish early needs" in lc.state().errors[0]
    lc.retake(7)                                                                 # out of range: ignored
    assert lc.state().phase == "AWAIT_OPERATOR" and lc.state().step_index == 0
    monkeypatch.setattr(be.motion, "fw_moving", lambda: True)
    lc.continue_()
    assert lc.state().errors == ("the axis is moving — wait for standstill",)
    monkeypatch.undo()
    be.capture.start("load_cal", 1.0, on_done=lambda _r: None)                   # a capture of that kind runs
    lc.continue_()
    assert "already running" in lc.state().errors[0] and lc.state().phase == "AWAIT_OPERATOR"
    be.capture.abort("load_cal", "test")
    lc._point_done(CaptureResult.__new__(CaptureResult))  # noqa: SLF001       # not capturing: ignored
    lc._progress(0.5)  # noqa: SLF001
    assert lc.state().phase == "AWAIT_OPERATOR"
    be.sim.act("weight", n=0.0)
    lc.continue_()
    assert lc.state().phase in ("PRESETTLE", "CAPTURE")
    lc.continue_()                                                               # no-ops while capturing
    lc.repeat()
    lc.finish_early()
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=1)
    lc.continue_({"mass_kg": "abc"})
    assert lc.state().errors == ("enter the mass of the weight",)
    be.sim.act("weight", kg=1.0)
    lc.continue_({"mass_kg": 1.0})
    assert be.test_hooks.run_until(lambda: lc.state().phase == "CAPTURE", 5000)
    be.capture.abort("load_cal", "operator stop")                               # capture ends not ok → ABORTED
    assert lc.state().phase == "ABORTED" and lc.state().abort_reason == "operator stop"
    lc.cancel()
    lc.terminate("late")
    assert lc.state().abort_reason == "operator stop"


@pytest.mark.req("SW-CAL-006")
def test_load_point_without_afe_data_is_rejected(be) -> None:
    lc = be.load_cal
    assert lc.start(n_points=2).ok
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=0)
    be.sim.act("weight", n=0.0)
    lc.continue_()
    assert be.test_hooks.run_until(lambda: lc.state().phase == "CAPTURE", 5000)
    be.sim.act("afe", stall=True)
    _wait_phase(be, lc, "AWAIT_OPERATOR", 20_000, index=0)
    errs = lc.state().errors
    assert errs and lc.state().can_repeat, lc.state()
    assert any("without AFE data" in e for e in errs), errs
    be.sim.act("afe", stall=False)
    lc.cancel()


@pytest.mark.req("SW-CAL-007", "SW-CAL-009")
def test_load_fit_degenerate_accept_moving_and_file_error(be, monkeypatch) -> None:
    import bend_stand.core.calibration.load as L

    def degenerate(*_a, **_k):
        raise ValueError("all raw means are equal")

    lc = be.load_cal
    monkeypatch.setattr(L, "load_calibration", degenerate)
    assert lc.start(n_points=2).ok
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=0)
    be.sim.act("weight", n=0.0)
    lc.continue_()
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=1)
    be.sim.act("weight", kg=5.0)
    lc.continue_({"mass_kg": 5.0})
    _wait_phase(be, lc, "FIT")
    st = lc.state()
    assert st.result is None and not st.can_continue and "points are not linear" in st.errors[0]
    lc.continue_()
    assert "re-take a point" in lc.state().errors[0] and be.calibrations.active_load() is None
    lc.cancel()
    monkeypatch.undo()
    st = calibrate(be)
    assert st.result.status == "PASS"
    monkeypatch.setattr(be.motion, "fw_moving", lambda: True)
    be.load_cal.continue_()
    assert be.load_cal.state().errors == ("accept only while the axis stands still",)
    monkeypatch.undo()

    def disk_full(_rec):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(be.calibration_store, "save_load", disk_full)
    be.load_cal.continue_()
    st = be.load_cal.state()
    assert st.phase == "FIT" and st.can_continue and "not written" in st.errors[0]
    assert be.calibrations.active_load() is None
    monkeypatch.undo()
    be.load_cal.continue_()
    _wait_phase(be, be.load_cal, "DONE", 2000)
    assert be.calibrations.active_load() is not None


@pytest.mark.req("SW-CAL-005")
def test_load_wizard_link_down_leaves_the_stream_setting(be) -> None:
    h = be.test_hooks
    h.result(be.stream_stop_async())
    h.advance(200)
    assert be.load_cal.start(n_points=2).ok
    h.advance(300)
    assert be.device.stream_on
    be.sim.act("inject", fault="hang", duration_ms=2500)                         # link LOST → terminate
    assert h.run_until(lambda: be.load_cal.state().phase == "ABORTED", 3000)
    assert not tx_frames(be, Cmd.STREAM_STOP)[1:]                                # no STREAM_STOP while down


# ================================================================================================ travel wizard
@pytest.mark.req("SW-CAL-002", "SW-CAL-001")
def test_travel_start_refusals_and_phase_guards(monkeypatch) -> None:
    be = _ready()
    try:
        tc = be.travel_cal
        monkeypatch.setattr(be.device.params, "get", lambda *_a, **_k: None)
        g = tc.start(v_mm_s=10.0, confirmed=True)
        assert not g.ok and "NO_PARAMS" in g.codes()
        monkeypatch.undo()

        def no_file(*_a, **_k):
            raise OSError("read-only folder")

        monkeypatch.setattr(be.calibration_store, "write_restore", no_file)
        g = tc.start(v_mm_s=10.0, confirmed=True)
        assert not g.ok and "FILE" in g.codes() and not tc.active
        monkeypatch.undo()
        assert tc.start(v_mm_s=10.0, confirmed=True).ok
        assert not tc.start(v_mm_s=10.0, confirmed=True).ok                       # busy
        assert tc.repeat() is None
        h = be.test_hooks
        diff = h.result(be._job(tc.post_sync_job))  # noqa: SLF001              # active: diff unchanged
        assert not diff.differs
        tc.cancel()                                                               # no trial written yet
        assert tc.state().phase == "ABORTED" and be.calibration_store.restore_pending() is None
        assert be.owner == "MANUAL"
        assert tc.start(v_mm_s=10.0, confirmed=True).ok
        monkeypatch.setattr(be.motion, "position_mm", lambda: None)
        tc.continue_()
        assert tc.state().phase == "ABORTED" and tc.state().abort_reason == "position unknown"
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-002")
@pytest.mark.parametrize("outcome, needle", [
    ("gate", "refused SW_TRIP"),
    ("refused", "move ended: nack text"),
    ("bound", "move ended: BOUND"),
    ("stopped", "move ended: STOPPED (PC_STOP)")])
def test_travel_move_failures_abort(monkeypatch, outcome, needle) -> None:
    be = _ready()
    try:
        tc = be.travel_cal
        assert tc.start(v_mm_s=10.0, confirmed=True).ok
        f: Future = Future()
        if outcome == "gate":
            f.set_exception(GateRefused(GateResult((GateItem("SW_TRIP", Severity.REFUSE, "refused SW_TRIP"),))))
        elif outcome == "refused":
            f.set_result(MoveOutcome("REFUSED", text="nack text"))
        else:
            reason, cause = ("BOUND", None) if outcome == "bound" else ("STOPPED", "PC_STOP")
            f.set_result(MoveOutcome("DONE", MoveDone(reason, 1.0, 800, 0, cause)))
        monkeypatch.setattr(be.motion, "move_to", lambda *_a, **_k: f)
        tc.continue_()
        st = tc.state()
        assert st.phase == "ABORTED" and needle in st.abort_reason, st
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-003", "SW-CAL-001")
def test_travel_entry_errors_readback_mismatch_and_cancel_during_trial_write(monkeypatch) -> None:
    be = _ready()
    try:
        h = be.test_hooks
        tc = be.travel_cal
        assert tc.start(v_mm_s=10.0, confirmed=True).ok
        tc.continue_()
        tc.continue_()                                                           # while moving: ignored
        _wait(be, "REFERENCE")
        tc.continue_()
        _wait(be, "ENTER_D1")
        tc.continue_({})
        assert tc.state().errors == ("enter D1 in mm",)
        tc.continue_({"d1_mm": 100.0})                                           # 80 steps/mm < 100: rejected
        assert "outside" in tc.state().errors[0] and tc.state().phase == "ENTER_D1"

        def no_file(*_a, **_k):
            raise OSError("read-only folder")

        monkeypatch.setattr(be.calibration_store, "write_restore", no_file)
        tc.continue_({"d1_mm": 8000 / WORLD_SPM})
        assert tc.state().phase == "ABORTED" and "restore record not written" in tc.state().abort_reason
        assert be.sim.board.params[KEY] == 800.0                                 # nothing written to the board
        monkeypatch.undo()
        # read-back mismatch → the trial is not confirmed → abort → restore spm0
        assert tc.start(v_mm_s=10.0, confirmed=True).ok
        tc.continue_()
        _wait(be, "REFERENCE")
        tc.continue_()
        _wait(be, "ENTER_D1")
        be.sim.inject_store_mismatch(KEY, 123.0)
        tc.continue_({"d1_mm": 8000 / WORLD_SPM})
        assert h.run_until(lambda: tc.state().phase == "ABORTED", 5000), tc.state()
        assert "trial steps/mm not set" in tc.state().abort_reason
        assert h.run_until(lambda: be.calibration_store.restore_pending() is None, 5000)
        # cancel while the trial value is being written: the late callback is ignored, spm0 restored
        assert tc.start(v_mm_s=10.0, confirmed=True).ok
        tc.continue_()
        _wait(be, "REFERENCE")
        tc.continue_()
        _wait(be, "ENTER_D1")
        tc.continue_({"d1_mm": 8000 / WORLD_SPM})
        tc.cancel()
        assert tc.state().phase == "RESTORING"
        tc.cancel()                                                              # ignored while RESTORING
        tc.continue_()
        _wait(be, "ABORTED", 5000)
        assert be.sim.board.params[KEY] == 800.0 and be.calibration_store.restore_pending() is None
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-003", "SW-CAL-004", "SW-CAL-001")
def test_travel_dtot_errors_accept_guards_and_accept_failure(monkeypatch) -> None:
    be = _ready()
    try:
        h = be.test_hooks
        h.result(be.config.save_async(), 10_000)                                 # no other unsaved changes
        h.advance(100)
        xr, d1 = _to_move2(be)
        tc = be.travel_cal
        tc.continue_()
        _wait(be, "ENTER_DTOT")
        tc.continue_({})
        assert tc.state().errors == ("enter D_tot in mm",)
        tc.continue_({"dtot_mm": d1})
        assert tc.state().errors == ("the total distance must exceed D1",)
        tc.continue_({"dtot_mm": 600.0})                                         # 80 steps/mm: rejected
        assert "outside" in tc.state().errors[0] and tc.state().phase == "ENTER_DTOT"
        dtot = round((_wx(be) - xr) / 1000.0, 3)
        tc.continue_({"dtot_mm": dtot})
        _wait(be, "RESULT", 1000)
        assert tc.state().needs_confirmation is None                             # nothing to confirm (saved before)
        monkeypatch.setattr(be.motion, "fw_moving", lambda: True)
        tc.continue_()
        assert tc.state().errors == ("accept only while the axis stands still",)
        monkeypatch.undo()
        be.sim.inject_nack("SAVE_PARAMS", int(pg.Status.E_NVM), 0)
        tc.continue_()
        assert tc.state().phase == "ACCEPT"
        tc.continue_()                                                           # ignored while accepting
        tc.cancel()
        assert h.run_until(lambda: tc.state().phase == "ABORTED", 10_000), tc.state()
        assert "accept failed" in tc.state().abort_reason
        assert h.run_until(lambda: be.sim.board.params[KEY] == 800.0, 5000)       # spm0 restored (RAM)
        assert be.calibrations.active_travel() is None
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-001")
def test_travel_restore_failure_raises_the_indicator(monkeypatch) -> None:
    be = _ready()
    try:
        h = be.test_hooks
        _to_move2(be)
        be.sim.inject_nack("SET_PARAM", int(pg.Status.E_BUSY), int(pg.BusyDetail.MOTION))
        be.travel_cal.cancel()
        assert h.run_until(lambda: be.travel_cal.state().phase == "ABORTED", 10_000)
        st = be.status()
        assert "restore failed" in be.travel_cal.state().instruction
        assert st.calibration.travel_diff.restore_pending and be.calibration_store.restore_pending()
        d = h.result(be.calibrations.restore_travel_async())                     # operator: restore
        assert not d.differs and be.sim.board.params[KEY] == 800.0
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-001", "SW-CAL-004")
def test_post_sync_resolution_and_operator_decisions() -> None:
    be = _ready()
    try:
        h = be.test_hooks
        tc = be.travel_cal
        store = be.calibration_store
        uid = be.device.info.uid

        def sync():
            return h.result(be._job(tc.post_sync_job))  # noqa: SLF001

        store.write_restore(uid, 800.0, 790.0, "2026-10-05T10:00:00Z")           # board = spm0 → record dropped
        assert not sync().differs and store.restore_pending() is None
        store.write_restore("OTHER-BOARD", 700.0, 790.0, "2026-10-05T10:00:00Z")  # another board: not applied
        assert not sync().differs and store.restore_pending() is not None
        store.write_restore(uid, 790.0, 800.0, "2026-10-05T10:00:00Z")           # board = trial → spm0 written…
        be.sim.inject_nack("SET_PARAM", int(pg.Status.E_BUSY), int(pg.BusyDetail.MOTION))
        d = sync()                                                               # … but refused → indicator
        assert d.differs and d.restore_pending and d.session_spm == 790.0
        h.result(be.calibrations.resolve_travel_difference_async("keep_board"))
        assert store.restore_pending() is None and not tc.diff.differs
        store.write_restore(uid, 790.0, 800.0, "2026-10-05T10:00:00Z")
        d = h.result(be.calibrations.restore_travel_async())                     # restore from the record
        assert not d.differs and f32(be.sim.board.params[KEY]) == f32(790.0)
        (store.dir / S.ACTIVE_TRAVEL).write_text(json.dumps({"schema": "bird.bend.cal.travel", "schema_version": 1,
                                                             "spm2": -1}), encoding="utf-8")
        assert not sync().differs                                                # invalid active file: ignored
        (store.dir / S.ACTIVE_TRAVEL).unlink()
        f = be._job(tc.resolve_job, "restore")  # noqa: SLF001
        h.advance(50)
        assert isinstance(f.exception(), BendStandError) and "nothing to restore" in str(f.exception())
        f = be._job(tc.resolve_job, "bogus")  # noqa: SLF001
        h.advance(50)
        assert isinstance(f.exception(), ValueError)
        store.write_restore(uid, 800.0, None, "2026-10-05T10:00:00Z")
        be.motion.move_to(30.0, speed_mm_s=5.0)
        assert h.run_until(lambda: be.status().motion.moving, 2000)
        f = be._job(tc.resolve_job, "restore")  # noqa: SLF001
        h.advance(30)
        assert "moving" in str(f.exception())
    finally:
        be.shutdown()


# ================================================================================================ tare engine
@pytest.mark.req("SW-TARE-001", "SW-TARE-002")
def test_tare_stream_start_failure_cancel_and_terminate_in_check(be, monkeypatch) -> None:
    from bend_stand.core.errors import LinkError

    h = be.test_hooks
    te = be.tare_engine
    h.result(be.stream_stop_async())
    h.advance(200)

    def broken(_on):
        raise LinkError("stream refused")
        yield None  # pragma: no cover

    monkeypatch.setattr(be.device, "stream_job", broken)
    assert be.tare(window_s=2.0).ok and te.state().phase == "CHECK"
    assert not be.tare(window_s=2.0).ok                                          # busy while CHECK
    h.advance(50)
    assert te.state().phase == "ABORTED" and "stream could not be started" in te.state().abort_reason
    monkeypatch.undo()
    for how in ("cancel", "terminate"):
        assert be.tare(window_s=2.0).ok and te.state().phase == "CHECK"
        te.cancel() if how == "cancel" else te.terminate("link down")
        assert te.state().phase == "ABORTED"
        h.advance(300)
        assert te.state().phase == "ABORTED" and not be.device.stream_on          # late stream start ignored


@pytest.mark.req("SW-TARE-001", "SW-TARE-003")
def test_tare_capture_errors_no_data_and_terminate(be, monkeypatch) -> None:
    h = be.test_hooks
    te = be.tare_engine

    def busy(*_a, **_k):
        raise RuntimeError("a tare capture is already running")

    monkeypatch.setattr(be.capture, "start", busy)
    be.tare(window_s=2.0)
    assert te.state().phase == "ABORTED" and "already running" in te.state().abort_reason
    monkeypatch.undo()
    assert be.tare(window_s=2.0).ok and te.state().phase == "CAPTURE"
    h.advance(300)
    te.terminate("STOP")
    assert te.state().phase == "ABORTED" and te.state().abort_reason == "STOP"
    te.terminate("again")                                                        # idle: nothing to do
    assert be.tare(window_s=2.0).ok
    h.advance(300)
    be.sim.act("afe", stall=True)
    _wait_phase(be, te, "REFUSED", 10_000)
    assert any("without AFE data" in e for e in te.state().errors), te.state().errors
    be.sim.act("afe", stall=False)


@pytest.mark.req("SW-TARE-002", "SW-TARE-003")
def test_tare_offset_warning_and_undo_rules(be, monkeypatch) -> None:
    from seq_rig import CAL

    h = be.test_hooks
    te = be.tare_engine
    be.activate_load_calibration(CAL)
    g = te.undo()
    assert not g.ok and "NOTHING_TO_CLEAR" in g.codes()
    be.sim.act("weight", n=0.0)
    h.advance(300)
    assert be.tare(window_s=2.0).ok
    g = te.undo()
    assert not g.ok and "OPERATION_RUNNING" in g.codes()
    _wait_phase(be, te, "DONE", 10_000)
    first = te.state().result
    assert first.warnings == ()
    be.sim.act("weight", n=250.0)                                                # > 10 % FS off the cal zero
    h.advance(1200)
    assert be.tare(window_s=2.0).ok
    _wait_phase(be, te, "DONE", 10_000)
    assert any("large offset" in w for w in te.state().warnings)
    monkeypatch.setattr(be.motion, "fw_moving", lambda: True)
    g = te.undo()
    assert not g.ok and "MOTION_ACTIVE" in g.codes()
    monkeypatch.undo()
    assert te.undo().ok and be.load_input.tare.tare_raw == first.tare_raw       # back to the first tare
    g = te.undo()                                                                # one level of history only
    assert not g.ok and "NOTHING_TO_CLEAR" in g.codes() and be.load_input.tare.tare_raw == first.tare_raw
