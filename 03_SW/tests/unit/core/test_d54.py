"""D-54 (SRS v0.6.7): (a) travel calibration available without a load calibration — CONFIRM "no specimen mounted",
wizard-scoped no-specimen state (PC load limits off for the wizard's own moves only, MANUAL motion still refused by
D-53 a, FW load thresholds not widened), HOME first when not homed, scope ended on every exit; (c) recordings folder
set at runtime (``reports.set_root``).

Verifies: SW-CAL-002, SW-LIM-004, SAF-SW-004, SW-ACQ-002
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from bbs_support import lockstep_backend

from bend_stand.core.model import GateId

from .test_m3_travel_cal import WORLD_SPM, _wait, _wx


def _enabled(home: bool = False):
    be = lockstep_backend()                                   # no calibration, no tare, no no-specimen mode
    be.sim.board.world.steps_per_mm = WORLD_SPM
    h = be.test_hooks
    assert be.motion.enable().ok
    assert h.run_until(lambda: be.status().motion.enabled, 2000)
    h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}))
    return be


def _texts(be) -> str:
    return "\n".join(str(r.payload) for r in be.events.history("log"))


@pytest.mark.req("SW-CAL-002", "SW-LIM-004", "SAF-SW-004")
def test_travel_wizard_without_load_calibration_homes_first_and_keeps_manual_motion_refused() -> None:
    be = _enabled()
    try:
        h = be.test_hooks
        tc = be.travel_cal
        assert "LOAD_INPUT_INVALID" in be.status().gates[GateId.MOVE].codes()
        g = be.status().gates[GateId.CAL_TRAVEL_START]
        assert g.ok and "NO_SPECIMEN_MOUNTED" in g.codes()
        assert not {"LOAD_INPUT_INVALID", "NOT_HOMED"} & set(g.codes())
        thr0 = be.device.thresholds
        g = tc.start(v_mm_s=10.0)
        assert g.needs_confirmation and not tc.active                       # nothing starts without the CONFIRM
        assert tc.start(v_mm_s=10.0, confirmed=True).ok
        assert tc.state().phase == "HOME" and be.status().safety.no_specimen_scope == "TRAVEL_CAL"
        assert not be.status().safety.no_specimen_mode                      # the session mode is not entered
        mv = be.status().gates[GateId.MOVE].codes()
        assert "LOAD_INPUT_INVALID" in mv and "OWNER_CONFLICT" in mv        # MANUAL stays refused (D-53 a)
        _wait(be, "BACKLASH", 60_000)                                       # HOME ran inside the wizard scope
        assert be.status().motion.homed
        tc.continue_()
        _wait(be, "REFERENCE")                                              # wizard move ran, no LOAD_INPUT_INVALID
        assert not be.status().safety.trips
        assert be.device.thresholds.raw_min == thr0.raw_min and be.device.thresholds.raw_max == thr0.raw_max
        assert be.device.thresholds.state == thr0.state                     # FW thresholds not widened
        tc.cancel()
        _wait(be, "ABORTED", 5000)
        assert be.status().safety.no_specimen_scope is None
        lim = be.limits.get()
        assert lim.pull_enabled and lim.push_enabled
        assert "LOAD_INPUT_INVALID" in be.status().gates[GateId.MOVE].codes()   # after the wizard: refused again
        n = len(be.events.history("log"))
        be.motion.jog_start(1, 2.0)
        h.advance(100)
        assert not be.status().motion.moving and len(be.events.history("log")) >= n
        assert "PC load limits off for its own moves" in _texts(be) and "ended" in _texts(be)
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-002", "SW-LIM-004")
@pytest.mark.parametrize("exit_path", ["link_loss", "disconnect", "home_failure"])
def test_wizard_scope_ends_on_every_exit_path(exit_path) -> None:
    be = _enabled()
    try:
        h = be.test_hooks
        tc = be.travel_cal
        if exit_path == "home_failure":
            be.sim.act("limit", name="start", position_um=-10_000_000)        # the home switch is never found
        assert tc.start(v_mm_s=10.0, confirmed=True).ok
        assert be.status().safety.no_specimen_scope == "TRAVEL_CAL"
        assert be.status().indicators["no_specimen_mode"].state == "ON"     # banner (D-54 a)
        if exit_path == "link_loss":
            be.sim.act("inject", fault="hang", duration_ms=2500)
            assert h.run_until(lambda: tc.state().phase == "ABORTED", 4000)
        elif exit_path == "disconnect":
            h.result(be.disconnect_async())
            assert h.run_until(lambda: tc.state().phase == "ABORTED", 2000)
        else:
            assert h.run_until(lambda: tc.state().phase == "ABORTED", 240_000), tc.state()
            assert tc.state().abort_reason
        assert be.status().safety.no_specimen_scope is None
        assert be.owner == "MANUAL" and not be.status().safety.no_specimen_mode
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-002", "SW-CAL-004")
def test_wizard_accept_ends_the_scope() -> None:
    be = _enabled()
    try:
        h = be.test_hooks
        assert be.limits.set_no_specimen_mode(True, confirmed=True).ok            # home in the session mode …
        h.result(be.motion.home(load_confirmed=True), 60_000)
        assert h.run_until(lambda: be.status().motion.homed and not be.status().motion.moving, 2000)
        assert be.limits.set_no_specimen_mode(False).ok                           # … and leave it again
        tc = be.travel_cal
        assert tc.start(v_mm_s=10.0, confirmed=True).ok and tc.state().phase == "BACKLASH"
        tc.continue_()
        _wait(be, "REFERENCE")
        xr = _wx(be)
        tc.continue_()
        _wait(be, "ENTER_D1")
        tc.continue_({"d1_mm": round((_wx(be) - xr) / 1000.0, 3)})
        _wait(be, "MOVE2", 5000)
        tc.continue_()
        _wait(be, "ENTER_DTOT")
        tc.continue_({"dtot_mm": round((_wx(be) - xr) / 1000.0, 3)})
        _wait(be, "RESULT", 1000)
        tc.continue_(confirmed=True)
        _wait(be, "DONE", 5000)
        assert be.status().safety.no_specimen_scope is None and be.owner == "MANUAL"
        assert "LOAD_INPUT_INVALID" in be.status().gates[GateId.MOVE].codes()
    finally:
        be.shutdown()


# ================================================================================================ D-54 c
@pytest.mark.req("SW-ACQ-002")
def test_recordings_folder_set_at_runtime(tmp_path) -> None:
    be = lockstep_backend(recordings_root=str(tmp_path / "cli"))
    try:
        new = tmp_path / "lab" / "recs"
        g = be.reports.set_root(str(new))
        assert g.ok, g
        assert be.reports.root() == str(new.resolve()) and new.is_dir() and not list(new.iterdir())
        assert be.session.get().recordings_root == str(new.resolve())
        sess = Path(be.session.path) if be.session.path else None
        if sess is not None and sess.exists():
            assert json.loads(sess.read_text(encoding="utf-8"))["recordings_root"] == str(new.resolve())
        assert be.record_start().ok
        assert Path(be.recorder.folder).parent == new.resolve()
        g = be.reports.set_root(str(tmp_path / "other"))
        assert not g.ok and "RECORDING_ACTIVE" in g.codes() and be.reports.root() == str(new.resolve())
        assert be.record_stop().ok
        f = tmp_path / "a_file"
        f.write_text("x", encoding="utf-8")
        g = be.reports.set_root(str(f / "sub"))
        assert not g.ok and "FOLDER_NOT_WRITABLE" in g.codes()
        assert not be.reports.set_root("  ").ok
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-002", "SW-STOP-001", "SAF-SW-001")
def test_cancel_during_a_wizard_move_stops_it_before_releasing_the_owner() -> None:
    """SWR-38: Cancel while the wizard's move runs → controlled STOP from the wizard, owner / scope released only at
    standstill, no SW trip; Cancel at standstill sends no STOP."""
    from bbs_support import tx_frames

    from bend_stand.core import protocol_gen as pg

    be = _enabled()
    try:
        h = be.test_hooks
        tc = be.travel_cal
        assert tc.start(v_mm_s=10.0, confirmed=True).ok and tc.state().phase == "HOME"
        h.advance(1000)
        assert be.status().motion.moving
        n_stop, n_trip = len(tx_frames(be, pg.Cmd.STOP)), len(be.events.history("safety.trip"))
        tc.cancel()
        assert be.owner == "TRAVEL_CAL" and be.status().safety.no_specimen_scope == "TRAVEL_CAL"   # not yet released
        stops = tx_frames(be, pg.Cmd.STOP)[n_stop:]
        assert stops and stops[0].frame[6] == int(pg.StopMode.CONTROLLED)
        assert h.run_until(lambda: tc.state().phase == "ABORTED", 5000)
        assert be.owner == "MANUAL" and be.status().safety.no_specimen_scope is None
        assert not be.status().motion.moving and len(be.events.history("safety.trip")) == n_trip
        assert tc.state().abort_reason == "cancelled"
        assert be.limits.set_no_specimen_mode(True, confirmed=True).ok          # home the axis, then a wizard
        h.result(be.motion.home(load_confirmed=True), 60_000)                    # that waits at BACKLASH
        assert h.run_until(lambda: be.status().motion.homed and not be.status().motion.moving, 2000)
        assert be.limits.set_no_specimen_mode(False).ok
        assert tc.start(v_mm_s=10.0, confirmed=True).ok
        assert tc.state().phase == "BACKLASH"
        n_stop = len(tx_frames(be, pg.Cmd.STOP))
        tc.cancel()
        assert tc.state().phase == "ABORTED" and len(tx_frames(be, pg.Cmd.STOP)) == n_stop
    finally:
        be.shutdown()
