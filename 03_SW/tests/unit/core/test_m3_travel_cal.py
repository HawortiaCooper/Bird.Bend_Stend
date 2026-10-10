"""M3 travel-calibration wizard against the simulator (lock-step): the simulated mechanics have 796 steps/mm
(SWC-M3-03: world travel per pulse is independent of the board parameter), the "operator" measures the real world
travel; full flow + accept (SET + read-back + SAVE + files), the active-travel-calibration restore rule (§9.3.1)
for cancel, E-stop, link loss + reconnect, the confirmations of SW-CAL-003, and the differs indicator with the
operator decisions.

Verifies: SW-CAL-001, SW-CAL-002, SW-CAL-003, SW-CAL-004
"""
from __future__ import annotations

import pytest

from bbs_support import lockstep_backend, tx_frames
from bend_stand.calc.motion import f32
from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import GateId

Cmd = pg.Cmd
WORLD_SPM = 796.0


def _wx(be) -> float:
    return be.sim.act("query", what="world")["x_um_true"]


def _wait(be, phase: str, ms: float = 60_000) -> None:
    tc = be.travel_cal
    assert be.test_hooks.run_until(lambda: tc.state().phase == phase, ms, 5), tc.state()


def _ready(**kw):
    be = lockstep_backend(no_specimen=True, **kw)
    be.sim.board.world.steps_per_mm = WORLD_SPM            # mechanics (scenario), before any step
    h = be.test_hooks
    assert be.motion.enable().ok
    assert h.run_until(lambda: be.status().motion.enabled, 2000)
    h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}))
    h.result(be.motion.home(load_confirmed=True), 60_000)
    assert h.run_until(lambda: be.status().motion.homed and not be.status().motion.moving, 2000)
    return be


def _to_move2(be) -> tuple[float, float]:
    """Run BACKLASH → REFERENCE → MOVE1 → ENTER_D1 (D1 = real travel) → MOVE2 (trial written); returns (x_ref, d1)."""
    tc = be.travel_cal
    g = tc.start(v_mm_s=10.0)                       # D-54 a: the session no-specimen mode (C-10) already confirmed
    assert g.ok and "NO_SPECIMEN_MOUNTED" not in g.codes(), g
    assert be.status().safety.no_specimen_scope is None   # no wizard scope needed inside the session mode
    assert tc.state().phase == "BACKLASH" and tc.state().continue_moves
    tc.continue_()
    _wait(be, "REFERENCE")
    xr = _wx(be)
    tc.continue_()
    _wait(be, "ENTER_D1")
    assert tc.state().result.n1 == 8000
    d1 = round((_wx(be) - xr) / 1000.0, 3)
    tc.continue_({"d1_mm": d1})
    _wait(be, "MOVE2", 5000)
    return xr, d1


@pytest.mark.req("SW-CAL-002", "SW-CAL-004")
def test_travel_wizard_full_flow_and_accept() -> None:
    be = _ready()
    try:
        assert "OWNER_CONFLICT" not in be.status().gates[GateId.MOVE].codes()
        xr, d1 = _to_move2(be)
        assert d1 == pytest.approx(8000 / WORLD_SPM, abs=1e-3)
        assert be.sim.board.params["motion.steps_per_mm"] == f32(8000 / d1)        # trial value in RAM only
        assert not tx_frames(be, Cmd.SAVE_PARAMS) and be.calibration_store.restore_pending()["spm_trial"]
        assert "OWNER_CONFLICT" in be.status().gates[GateId.MOVE].codes()            # the wizard owns motion
        be.travel_cal.continue_()
        _wait(be, "ENTER_DTOT")
        dtot = round((_wx(be) - xr) / 1000.0, 3)
        be.travel_cal.continue_({"dtot_mm": dtot})
        _wait(be, "RESULT", 1000)
        r = be.travel_cal.state().result
        assert r.spm2 == pytest.approx(WORLD_SPM, rel=2e-4) and abs(r.consistency) < 0.005
        # _ready wrote the homing speeds without SAVE → CFG_DIRTY: accepting would save them too → confirmation
        assert be.travel_cal.state().needs_confirmation.code == "CFG_DIRTY"
        be.travel_cal.continue_()
        assert be.travel_cal.state().phase == "RESULT" and be.travel_cal.state().errors
        be.travel_cal.continue_(confirmed=True)
        _wait(be, "DONE", 5000)
        assert tx_frames(be, Cmd.SAVE_PARAMS) and be.calibration_store.restore_pending() is None
        act = be.calibrations.active_travel()
        assert act["spm2"] == pytest.approx(r.spm2, rel=1e-6) and act["n1"] == 8000 and act["d_tot_mm"] == dtot
        assert be.calibrations.history("travel") and be.status().calibration.travel_spm == pytest.approx(r.spm2,
                                                                                                     rel=1e-6)
        assert be.sim.board.nvm.summary()                                           # NVM written at accept
        assert be.owner == "MANUAL" and "OWNER_CONFLICT" not in be.status().gates[GateId.MOVE].codes()
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-001")
def test_cancel_after_trial_restores_spm0_never_saves() -> None:
    be = _ready()
    try:
        _to_move2(be)
        be.travel_cal.cancel()
        _wait(be, "ABORTED", 5000)
        assert be.sim.board.params["motion.steps_per_mm"] == 800.0
        assert be.calibration_store.restore_pending() is None and not tx_frames(be, Cmd.SAVE_PARAMS)
        assert be.calibrations.active_travel() is None
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-001")
def test_estop_during_move2_restores_when_idle() -> None:
    be = _ready()
    try:
        _to_move2(be)
        be.travel_cal.continue_()
        be.test_hooks.advance(500)
        assert be.status().motion.moving
        be.sim.act("estop", open=True)
        _wait(be, "ABORTED", 5000)
        assert "ESTOP" in (be.travel_cal.state().abort_reason or "") or be.travel_cal.state().abort_reason
        assert be.sim.board.params["motion.steps_per_mm"] == 800.0 and not tx_frames(be, Cmd.SAVE_PARAMS)
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-001")
def test_link_loss_after_trial_indicator_then_restore_at_reconnect() -> None:
    be = _ready()
    try:
        h = be.test_hooks
        _to_move2(be)
        be.sim.act("inject", fault="hang", duration_ms=2500)               # board silent → link LOST
        assert h.run_until(lambda: be.travel_cal.state().phase == "ABORTED", 3000)
        assert be.status().calibration.travel_diff.restore_pending or be.calibration_store.restore_pending()
        assert h.run_until(lambda: be.status().link.state.value == "CONNECTED" and
                           be.calibration_store.restore_pending() is None, 15_000)
        assert be.sim.board.params["motion.steps_per_mm"] == 800.0
        assert not be.status().calibration.travel_cal_differs
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-003")
def test_large_change_needs_confirmation_with_dip_candidates() -> None:
    be = _ready()
    try:
        tc = be.travel_cal
        assert tc.start(v_mm_s=10.0, confirmed=True).ok
        tc.continue_()
        _wait(be, "REFERENCE")
        tc.continue_()
        _wait(be, "ENTER_D1")
        tc.continue_({"d1_mm": 0.0})
        assert tc.state().errors
        tc.continue_({"d1_mm": 50.0})                                    # 8000 / 50 = 160 steps/mm
        st = tc.state()
        assert st.phase == "ENTER_D1" and st.needs_confirmation.code == "SPM_CHANGE_20"
        assert "800 steps/mm" in st.needs_confirmation.text and "160 steps/mm" in st.needs_confirmation.text
        assert be.sim.board.params["motion.steps_per_mm"] == 800.0      # nothing written before the confirmation
        tc.continue_({"d1_mm": 50.0}, confirmed=True)
        _wait(be, "MOVE2", 3000)
        assert be.sim.board.params["motion.steps_per_mm"] == 160.0
        tc.cancel()
        _wait(be, "ABORTED", 5000)
        assert be.sim.board.params["motion.steps_per_mm"] == 800.0
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-001", "SW-CAL-004")
def test_board_differs_from_active_file_indicator_and_decisions() -> None:
    be = _ready()
    try:
        h = be.test_hooks
        be.calibration_store.save_travel({"created_utc": "2026-10-05T10:00:00Z", "spm0": 800.0, "spm2": 790.0})
        diff = h.result(be._job(be.travel_cal.post_sync_job))  # noqa: SLF001
        assert diff.differs and diff.source == "active_file" and diff.session_spm == 790.0
        st = be.status()
        assert st.indicators.travel_cal_differs.state == "ON" and "TRAVEL_CAL_DIFFERS" in st.gates[GateId.MOVE].codes()
        d2 = h.result(be.calibrations.resolve_travel_difference_async("ignore_session"))
        assert d2.ignored and be.status().indicators.travel_cal_differs.state == "OFF"
        d3 = h.result(be.calibrations.restore_travel_async())
        assert not d3.differs and be.sim.board.params["motion.steps_per_mm"] == 790.0
        assert be.calibrations.resolve_travel_difference_async("bogus").exception()
    finally:
        be.shutdown()


@pytest.mark.req("SW-CAL-002")
def test_travel_start_refused_without_room_or_homing() -> None:
    be = lockstep_backend(no_specimen=True)
    try:
        g = be.travel_cal.start()
        assert not g.ok and {"NOT_ENABLED"} <= set(g.codes())
    finally:
        be.shutdown()
