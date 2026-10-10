"""Validator F — D-54 a (SRS v0.6.7 SW-CAL-002): travel calibration without a load calibration, pre-written.

The travel wizard starts without a valid load input after the "no specimen mounted" confirmation (SAF-SW-004) and runs
in a **wizard-scoped** no-specimen state: PC load limits off only for the wizard's own moves, FW load limit at its
nominal defaults, banner + event, a non-homed axis homed as the wizard's first step, both PC load limits restored on
every exit (finish / cancel / abort — the SWR-29 rule); all other motion (MANUAL, SEQUENCE) keeps D-53 a (no motion
without a valid load input outside the no-specimen mode). Safety-relevant: the wizard's scope must never leak.

Pre-written as the pending group D54A (SW_test_plan §2.6); **armed 2026-10-10** after B's D-54 a / c hand-back
(SW_design §9.3, §15.5f B6-35) — the pending markers are removed, the tests run in every validation run. v0.5.6 adds
F's cases 05–09 (other owner during the wizard, session mode toggled, FW E-stop, homing failure, Cancel) and D-54 c. Ground truth: the wire (``ref_codec``), the gate
results and the status; never the wizard's own state alone.

Verifies: SW-CAL-002, SW-LIM-004, SAF-SW-001, SAF-SW-002, SAF-SW-004, SW-LIM-003, SW-MAN-004, SW-STOP-001,
SW-STOP-003, FW-HOM-002, SW-ACQ-002
"""
from __future__ import annotations

import pytest

import harness as H

MOTION = ("MOVE_ABS", "MOVE_UNTIL_LOAD", "JOG", "HOME")


def _codes(g) -> set[str]:
    return {str(i.code) for i in g.items}


def _ns_on(be) -> bool:
    """Any no-specimen indication the operator sees (global mode flag or the banner indicator)."""
    ind = H.indicator(be, "no_specimen_mode")
    return bool(H.safety(be).no_specimen_mode or getattr(ind, "state", "OFF") == "ON")


def _start(be, confirmed: bool):
    return be.travel_cal.start(confirmed=confirmed)


def _running(be) -> bool:
    return bool(be.travel_cal.active)


def _assert_limits_restored(be) -> None:
    """After any exit: both PC load limits on, no no-specimen state, MANUAL motion refused again (no calibration)."""
    lim = be.limits.get()
    assert lim.pull_enabled and lim.push_enabled, lim
    assert not _ns_on(be)
    assert "LOAD_INPUT_INVALID" in _codes(H.gate(be, "MOVE")), H.gate(be, "MOVE")


@pytest.mark.req("SW-CAL-002", "SAF-SW-004")
def test_d54a_01_start_without_calibration_needs_the_confirmation_and_moves_nothing(lockstep):
    """No calibration, not in the no-specimen mode, axis enabled: the unconfirmed start returns the CONFIRM
    ``NO_SPECIMEN_MOUNTED`` and nothing is written — no motion frame, no SET_PARAM, the wizard is not running."""
    # Verifies: SW-CAL-002, SAF-SW-004
    be = lockstep()
    H.enable(be)
    assert H.run_until(be, lambda: H.motion(be).enabled, 2000)
    assert not H.safety(be).load_input_valid
    m0 = H.wire_mark(be)
    g = _start(be, confirmed=False)
    H.advance(be, 500)
    assert "NO_SPECIMEN_MOUNTED" in _codes(g), g
    sent = {w.name for w in H.tx(be, since=m0)}
    assert not sent & set(MOTION) and "SET_PARAM" not in sent, sent
    assert not _running(be) and not _ns_on(be)


@pytest.mark.req("SW-CAL-002", "SAF-SW-001", "SAF-SW-002", "SW-LIM-004")
def test_d54a_02_confirmed_start_homes_first_and_keeps_the_scope_inside_the_wizard(lockstep):
    """Confirmed start on a non-homed axis: HOME is the first motion frame (before any MOVE_ABS); while the wizard
    runs the banner is on, the FW thresholds are the nominal defaults (DEFAULT_ONLY), a manual move / jog and a
    sequence start are refused (the scope does not leak: OWNER_CONFLICT or LOAD_INPUT_INVALID), and no PC load limit is
    switched off in the stored session."""
    # Verifies: SW-CAL-002, SAF-SW-001, SAF-SW-002, SW-LIM-004
    be = lockstep()
    H.enable(be)
    assert H.run_until(be, lambda: H.motion(be).enabled, 2000)
    assert not H.motion(be).homed
    m0 = H.wire_mark(be)
    g = _start(be, confirmed=True)
    assert g.ok, g
    H.advance(be, 300)
    assert _running(be) and _ns_on(be)
    assert H.thresholds(be).state == "DEFAULT_ONLY", H.thresholds(be)
    move = H.gate(be, "MOVE")
    assert not move.ok and _codes(move) & {"OWNER_CONFLICT", "LOAD_INPUT_INVALID"}, move
    assert not H.gate(be, "JOG").ok
    seq = H.sequence([H.step("travel", 10.0, speed_mm_s=5.0)], travel_ref="machine")
    assert not be.sequencer.check_start(seq).ok
    lim = be.session.get().limits
    assert lim.pull_enabled and lim.push_enabled, lim                 # the wizard scope is never stored
    assert H.run_until(be, lambda: any(w.name in ("HOME", "MOVE_ABS") for w in H.tx(be, since=m0)), 30_000)
    first = next(w.name for w in H.tx(be, since=m0) if w.name in MOTION)
    assert first == "HOME", first


@pytest.mark.req("SW-CAL-002", "SW-LIM-003", "SAF-SW-001")
@pytest.mark.parametrize("exit_by", ["cancel", "stop", "halt", "link_lost"])
def test_d54a_03_every_exit_restores_the_pc_load_limits(lockstep, exit_by):
    """Every exit of a wizard started under the scope — Cancel, STOP, HALT (Pause/Break), link loss — ends the scope:
    both PC load limits on, no banner, the manual motion gate refuses LOAD_INPUT_INVALID again (no calibration)."""
    # Verifies: SW-CAL-002, SW-LIM-003, SAF-SW-001
    be = lockstep()
    H.enable(be)
    assert H.run_until(be, lambda: H.motion(be).enabled, 2000)
    assert _start(be, confirmed=True).ok
    H.advance(be, 1500)                                  # homing under way
    assert _running(be) and _ns_on(be)
    if exit_by == "cancel":
        be.travel_cal.cancel()
    elif exit_by == "stop":
        H.stop(be)
    elif exit_by == "halt":
        H.hotkey_press(be)
    else:
        H.act(be, "inject", fault="hang", duration_ms=3000)
        assert H.run_until(be, lambda: H.link_state(be) == "LOST", 3000)
    H.advance(be, 4000)
    assert not _running(be) or be.travel_cal.state().phase in ("RESTORING", "ABORTED")
    if exit_by == "link_lost":
        assert H.run_until(be, lambda: H.link_state(be) in ("CONNECTED", "DEGRADED"), 10_000)
        H.advance(be, 1000)
    _assert_limits_restored(be)


@pytest.mark.req("SW-CAL-002", "SW-LIM-004")
def test_d54a_04_global_no_specimen_mode_is_not_switched_off_by_the_wizard(lockstep):
    """An operator who entered the global no-specimen mode before the wizard keeps it after the wizard (the wizard scope
    is only used when the mode is off); the wizard's own confirmation is not asked again."""
    # Verifies: SW-CAL-002, SW-LIM-004
    be = lockstep()
    H.no_specimen(be)
    H.enable(be)
    assert H.run_until(be, lambda: H.motion(be).enabled, 2000)
    g = _start(be, confirmed=False)
    assert g.ok and "NO_SPECIMEN_MOUNTED" not in _codes(g), g
    H.advance(be, 500)
    be.travel_cal.cancel()
    H.advance(be, 3000)
    assert H.safety(be).no_specimen_mode


# ============================================================================================ F's additions (v0.5.6)

def _wizard_running(be) -> None:
    H.enable(be)
    assert H.run_until(be, lambda: H.motion(be).enabled, 2000)
    assert _start(be, confirmed=True).ok
    H.advance(be, 300)
    assert _running(be) and _ns_on(be)


def _assert_no_leak(be) -> None:
    """Invariant after any wizard exit: the PC load limits are never off without the session no-specimen mode."""
    lim = be.limits.get()
    if H.safety(be).no_specimen_mode:
        assert H.indicator(be, "no_specimen_mode").state == "ON"
    else:
        assert lim.pull_enabled and lim.push_enabled, lim
        assert "LOAD_INPUT_INVALID" in _codes(H.gate(be, "MOVE")), H.gate(be, "MOVE")
    assert be.status().motion.owner == "MANUAL"


@pytest.mark.req("SW-CAL-002", "SAF-SW-001", "SW-MAN-004")
def test_d54a_05_other_owner_jog_and_move_refused_during_the_wizard(lockstep):
    """While the wizard owns motion under its scope, a MANUAL jog / move / home is refused (OWNER_CONFLICT) and nothing
    of it reaches the wire; the wizard's own homing still completes (its moves run without PC load limits)."""
    # Verifies: SW-CAL-002, SAF-SW-001, SW-MAN-004
    be = lockstep()
    _wizard_running(be)
    m0 = H.wire_mark(be)
    H.jog_start(be, +1, 2.0)
    H.advance(be, 300)
    H.jog_stop(be)
    t = H.move_to(be, 5.0, speed_mm_s=2.0)
    h = H.home(be, load_confirmed=True)
    H.advance(be, 200)
    assert t.done() and t.exception() is not None and h.done() and h.exception() is not None
    assert "OWNER_CONFLICT" in _codes(H.gate(be, "JOG")), H.gate(be, "JOG")
    sent = [w for w in H.tx(be, since=m0) if w.name in ("JOG", "MOVE_ABS")]
    assert not [w for w in sent if w.name == "JOG" and w.fields.get("v_um_s", 0) != 0], [w.fields for w in sent]
    assert H.run_until(be, lambda: be.travel_cal.state().phase not in ("CHECK", "HOME"), 120_000), \
        be.travel_cal.state()
    assert be.travel_cal.state().phase == "BACKLASH", be.travel_cal.state()


@pytest.mark.req("SW-CAL-002", "SW-LIM-004", "SAF-SW-004")
def test_d54a_06_session_no_specimen_mode_toggled_during_the_wizard(lockstep):
    """(a) Entering the session no-specimen mode while the wizard runs is refused (an operation is running; the
    wizard's scope stays separate); (b) a session mode entered before and left during the wizard ends cleanly: after
    the wizard exits the PC load limits are on and MANUAL motion needs a valid load input again — no state with the PC
    load limits off outside a no-specimen state."""
    # Verifies: SW-CAL-002, SW-LIM-004, SAF-SW-004
    be = lockstep()
    _wizard_running(be)
    g = be.limits.set_no_specimen_mode(True, confirmed=True)
    assert not g.ok and not H.safety(be).no_specimen_mode, g
    be.travel_cal.cancel()
    H.advance(be, 3000)
    _assert_no_leak(be)

    be2 = lockstep()
    H.no_specimen(be2)                                   # without a calibration the limits cannot be switched off (B5-01)
    _wizard_running(be2)
    be2.limits.set_no_specimen_mode(False)               # left while the wizard runs (axis idle in the wizard)
    H.advance(be2, 200)
    lim = be2.limits.get()
    assert lim.pull_enabled and lim.push_enabled, lim
    be2.travel_cal.cancel()
    H.advance(be2, 3000)
    _assert_no_leak(be2)


@pytest.mark.req("SW-CAL-002", "SW-STOP-003", "SAF-SW-001")
def test_d54a_07_fw_estop_during_the_wizard_aborts_it_and_ends_the_scope(lockstep):
    """FW-reported E-stop (red button) while the wizard homes: the wizard ends (aborted), the scope ends, both PC load
    limits are on, the motion owner is MANUAL again and no wizard motion frame follows the E-stop."""
    # Verifies: SW-CAL-002, SW-STOP-003, SAF-SW-001
    be = lockstep()
    _wizard_running(be)
    H.advance(be, 1000)
    H.act(be, "estop", open=True)
    assert H.run_until(be, lambda: H.indicator(be, "estop").state == "ON", 2000)
    m0 = H.wire_mark(be)
    H.advance(be, 3000)
    assert not _running(be) or be.travel_cal.state().phase in ("RESTORING", "ABORTED"), be.travel_cal.state()
    assert not [w for w in H.tx(be, since=m0) if w.name in ("MOVE_ABS", "HOME", "JOG", "MOVE_UNTIL_LOAD")]
    assert not _ns_on(be)
    _assert_no_leak(be)


@pytest.mark.req("SW-CAL-002", "FW-HOM-002", "SAF-SW-001")
def test_d54a_08_homing_failure_ends_the_wizard_and_the_scope(lockstep):
    """The START switch never closes (forced inactive): homing fails on the FW side (HOME_FAILED NOT_FOUND); the
    wizard ends with that reason, no calibration move follows, the scope ends, the PC load limits are on."""
    # Verifies: SW-CAL-002, FW-HOM-002, SAF-SW-001
    be = lockstep()
    H.act(be, "limit", name="start", active=False)
    _wizard_running(be)
    assert H.run_until(be, lambda: be.travel_cal.state().phase != "HOME", 300_000, step_ms=5.0),         be.travel_cal.state()
    assert H.until(be, lambda: any(w.fields.get("code") == "HOME_FAILED" for w in H.events(be)), 2000, 500)
    m0 = H.wire_mark(be)
    H.advance(be, 3000)
    st = be.travel_cal.state()
    assert st.phase == "ABORTED" and not _running(be), st
    assert not [w for w in H.tx(be, since=m0) if w.name in ("MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD")]
    assert not _ns_on(be)
    _assert_no_leak(be)


@pytest.mark.req("SW-ACQ-002")
def test_d54c_recordings_folder_picker(lockstep, tmp_path):
    """D-54 c (SW-ACQ-002): ``reports.set_root`` refuses a folder that cannot be created / written (a file in the
    way) and changes nothing; accepts a writable folder (created), stores it in the session, the next recording is
    written there; refused while a recording runs."""
    # Verifies: SW-ACQ-002
    be = lockstep()
    before = be.reports.root()
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    g = be.reports.set_root(str(blocker / "rec"))
    assert not g.ok and be.reports.root() == before, g
    target = tmp_path / "new_root" / "deep"
    g = be.reports.set_root(str(target))
    assert g.ok, g
    assert be.reports.root() == str(target.resolve())
    assert be.session.get().recordings_root == str(target.resolve())
    assert not [p for p in target.iterdir()], list(target.iterdir())          # the write probe is gone
    assert H.record_start(be).ok
    H.advance(be, 300)
    folder = H.recording_folder(be)
    assert folder is not None and str(target.resolve()) in str(folder), folder
    g2 = be.reports.set_root(str(tmp_path / "other"))
    assert not g2.ok and "RECORDING_ACTIVE" in _codes(g2), g2
    H.record_stop(be)


@pytest.mark.req("SW-CAL-002", "SAF-SW-001", "SW-STOP-001")
@pytest.mark.defect("SWR-38")
@pytest.mark.parametrize("state", ["wizard_scope", "session_mode"])
def test_d54a_09_cancel_stops_the_wizard_motion_itself(lockstep, state):
    """Cancel while the wizard's homing runs: the wizard itself stops its motion (controlled STOP) within 200 ms.
    Observed: Cancel ends the scope and hands motion back to MANUAL without a STOP — under the wizard scope the homing is
    then stopped by a SAF-SW-001 LOAD_INPUT_INVALID trip (SW_TRIP event "load limit without valid input"), i.e. by the
    safety net instead of the wizard; with the session no-specimen mode on, the cancelled homing runs on."""
    # Verifies: SW-CAL-002, SAF-SW-001, SW-STOP-001
    be = lockstep()
    if state == "session_mode":
        H.no_specimen(be)
    _wizard_running(be)
    H.advance(be, 1500)
    assert H.motion(be).moving and be.travel_cal.state().phase == "HOME"
    n_trips = len(be.events.history("safety.trip"))
    m0, t0 = H.wire_mark(be), H.now_ns(be)
    be.travel_cal.cancel()
    H.advance(be, 300)
    stops = [w for w in H.tx(be, since=m0) if w.name == "STOP" and w.t_ns - t0 <= 200 * 1_000_000]
    assert stops and len(be.events.history("safety.trip")) == n_trips,         ([w.fields for w in stops], [r.payload for r in be.events.history("safety.trip")][n_trips:])
