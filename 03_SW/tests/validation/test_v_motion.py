"""Level C — M2 motion (SW_test_plan v0.3 §3.14): simulator motion fidelity (runs now, forced path) and the pre-written
MotionController / motion-gate / hotkey / WP-B12 cases (``pending("M2")``, armed with ``--arm M2`` at the M2
verification).

Part A (runs against the current simulator): the simulator is B's test environment (plan §1 rule 3); before the
M2/M3 SW motion cases can rely on it, its motion behaviour is checked against the **SRS §3.2 / ICD** semantics
(expected values from the ICD, ``params.yaml`` defaults and F's oracle ``f_ref`` — never from the simulator code).
Stimuli use the forced wire path (``harness.forced_*``); verdicts and events are read from the wire with
``ref_codec``. The same scenarios run against the FW twin at M2 (X, Integrator's ``test_sim_vs_twin.py``).

Part B (pending M2): public-API motion (``backend.motion``), motion gates, hotkey, and the simulator items of
WP-B12 (K1_WELDED timing, idle disable, load regrow, homing back-off).

Verifies: SYS-008, IF-009, SW-MAN-002, SW-MAN-003, SW-MAN-004, SW-MAN-005, SW-LIM-001, SW-STOP-002, SW-STOP-003,
SW-STOP-004, SAF-SW-004, SAF-SW-005
"""
from __future__ import annotations

import pytest

import harness as H
import ref_codec as rc
from oracle import f_ref

MS = 1_000_000


def _ev(be, since, code):
    return H.events(be, since, code)


def _block_names(detail: int) -> set[str]:
    return set(rc.bits_to_names(detail, rc.BLOCK))


def _x_um(be) -> float:
    """Commanded position = setpoint of the newest DATA frame (wire, ref_codec)."""
    return float(H.rx(be, "DATA")[-1].fields["setpoint_um"])


def _flags(w) -> list:
    return w.fields.get("flags", [])


def _move_done(be, since):
    return [w for w in _ev(be, since, "MOVE_DONE")]


# ============================================================================================ A — simulator now

@pytest.mark.req("SYS-008", "IF-009")
def test_tc_sys_008_04_sim_move_abs_reaches_target(vbe, pdict):
    """MOVE_ABS 50 mm at 10 mm/s: MOVING 1 while running, MOVE_DONE reason TARGET with value = 50 000 µm and
    value2 = um_to_steps(50 000, spm) (F's oracle, ICD §0.1); final DATA setpoint 50 000, MOVING 0; travel time
    ≥ the trapezoid time of VV-SEQ-04 (f_ref) and not more than 0.5 s above it."""
    # Verifies: SYS-008, IF-009
    H.forced_enable_home(vbe)
    spm = float(H.param_default(pdict, "motion.steps_per_mm"))
    a = float(H.param_default(pdict, "motion.a_max_um_s2"))
    m0 = H.wire_mark(vbe)
    t0 = H.now_ns(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 50_000, 10_000))
    assert H.until(vbe, lambda: bool(_move_done(vbe, m0)), 20_000)
    done = _move_done(vbe, m0)[0]
    assert rc.MOVE_DONE_REASON[done.fields["arg"]] == "TARGET"
    assert done.fields["value"] == 50_000 and done.fields["value2"] == f_ref.um_to_steps(50_000, spm)
    assert any("MOVING" in _flags(w) for w in H.rx(vbe, "DATA", since=m0))
    H.advance(vbe, 50)
    last = H.rx(vbe, "DATA", since=m0)[-1]
    assert last.fields["setpoint_um"] == 50_000 and "MOVING" not in _flags(last)
    t_plan = f_ref.plan_trapezoid(50_000, 10_000, a, a)["t"]            # µm, µm/s, µm/s² (decel = a_max)
    dt = (done.t_ns - t0) / 1e9
    assert t_plan - 0.02 <= dt <= t_plan + 0.5, (dt, t_plan)


@pytest.mark.req("SYS-008", "SW-MAN-004")
def test_tc_sys_008_04_sim_jog_bound_and_deadman(vbe, pdict):
    """JOG with a bound stops exactly at the bound (MOVE_DONE BOUND, value = bound); a JOG that is not refreshed is
    stopped by the dead-man after ``motion.jog_timeout_ms`` (STOPPED cause JOG_DEADMAN, VALID untouched)."""
    # Verifies: SYS-008, SW-MAN-004
    H.forced_enable_home(vbe)
    m0 = H.wire_mark(vbe)
    H.result(vbe, H.forced_request(vbe, "JOG", {"v_um_s": 5_000, "a_um_s2": 0, "bound_um": 3_000}, motion=True))
    # refresh every 80 ms (like the backend) until the bound is reached
    for _ in range(40):
        H.advance(vbe, 80)
        if _move_done(vbe, m0):
            break
        H.forced_request(vbe, "JOG", {"v_um_s": 5_000, "a_um_s2": 0, "bound_um": 3_000}, motion=True)
    d = _move_done(vbe, m0)
    assert d and rc.MOVE_DONE_REASON[d[0].fields["arg"]] == "BOUND" and d[0].fields["value"] == 3_000
    jt = int(H.param_default(pdict, "motion.jog_timeout_ms"))
    m1 = H.wire_mark(vbe)
    H.result(vbe, H.forced_request(vbe, "JOG", {"v_um_s": 2_000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}, motion=True))
    t_last = H.tx(vbe, "JOG", since=m1)[-1].t_ns
    assert H.until(vbe, lambda: bool(_ev(vbe, m1, "STOPPED")), jt + 200)
    st = _ev(vbe, m1, "STOPPED")[0]
    assert rc.STOP_CAUSE_NAME[st.fields["arg"]] == "JOG_DEADMAN"
    assert jt <= (st.t_ns - t_last) / MS <= jt + 20, (st.t_ns - t_last) / MS
    assert not _ev(vbe, m1, "VALID_CLEARED")


@pytest.mark.req("SYS-008", "SW-MAN-006")
def test_tc_sys_008_04_sim_home_from_mid_travel(vbe):
    """Re-HOME from x = 40 mm (homing at START only, D-29 b): EVENT HOMED (drift within ``home.drift_tol_um``, no
    HOME_DRIFT fault), MOVE_DONE at 0 µm, HOMED flag, POS_UNCERTAIN not set, MOVING 0 (FW-HOM-001…004)."""
    # Verifies: SYS-008, SW-MAN-006
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 40_000, 20_000))
    assert H.until(vbe, lambda: _x_um(vbe) == 40_000 and "MOVING" not in _flags(H.rx(vbe, "DATA")[-1]), 10_000)
    m0 = H.wire_mark(vbe)
    H.result(vbe, H.forced_request(vbe, "HOME", {"flags": 1}, motion=True))
    assert H.until(vbe, lambda: bool(_ev(vbe, m0, "HOMED")), 120_000)
    H.advance(vbe, 100)
    last = H.rx(vbe, "DATA", since=m0)[-1]
    assert "HOMED" in _flags(last) and "POS_UNCERTAIN" not in last.fields["status"]
    assert last.fields["setpoint_um"] == 0 and "MOVING" not in _flags(last)
    assert not [w for w in _ev(vbe, m0, "FAULT_SET") if rc.FAULTS[w.fields["arg"]] == "HOME_DRIFT"]


@pytest.mark.req("SYS-008", "SW-LIM-001", "SAF-SW-005")
def test_tc_sys_008_04_sim_limit_stops_latches_direction_aware(vbe, pdict):
    """END limit hit during a + move: immediate stop (STOPPED LIMIT_END), LIMIT_SET; while latched a further + move
    is refused (E_STATE with BLOCK LIMIT), a − move is accepted (ICD §5.5, D-33 h); the latch clears after
    ``io.release_ms`` of released input."""
    # Verifies: SYS-008, SW-LIM-001, SAF-SW-005
    H.forced_enable_home(vbe)
    m0 = H.wire_mark(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 100_000, 20_000))
    H.advance(vbe, 300)
    H.act(vbe, "limit", name="end", active=True)
    H.advance(vbe, 20)
    st = _ev(vbe, m0, "STOPPED")
    assert st and rc.STOP_CAUSE_NAME[st[0].fields["arg"]] == "LIMIT_END" and _ev(vbe, m0, "LIMIT_SET")
    x = _x_um(vbe)
    status, detail = H.forced_outcome(vbe, "MOVE_ABS", {"target_um": int(x) + 5_000, "v_um_s": 5_000, "a_um_s2": 0},
                                      motion=True)
    assert status == "E_STATE" and "LIMIT" in _block_names(detail), (status, _block_names(detail))
    H.act(vbe, "limit", name="end", active=False)
    status, _ = H.forced_outcome(vbe, "MOVE_ABS", {"target_um": int(x) - 2_000, "v_um_s": 5_000, "a_um_s2": 0},
                                 motion=True)
    assert status == "OK"
    rel = int(H.param_default(pdict, "io.release_ms"))
    m1 = H.wire_mark(vbe)
    assert H.until(vbe, lambda: bool(_ev(vbe, m1, "LIMIT_CLEARED")), rel + 50)


@pytest.mark.req("SYS-008", "SW-STOP-003", "SAF-SW-005")
def test_tc_sys_008_04_sim_estop_cuts_driver_power(vbe, pdict):
    """E-stop during a move (D-36: the red button cuts the driver supply through K1): immediate stop (STOPPED ESTOP),
    ESTOP_SET, DRIVER_DISABLED cause ESTOP, HOMED cleared; DRIVER_POWER 0 follows ``k1_delay_ms`` + the 20 ms DRV_PWR
    filter later (sense on, SAF-FW-024, SRS A-19);
    ENABLE refused (E_STATE ESTOP) until the input is closed ≥ ``io.estop_release_ms`` and ESTOP_CLEAR is sent."""
    # Verifies: SYS-008, SW-STOP-003, SAF-SW-005
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 100_000, 20_000))
    H.advance(vbe, 200)
    m0 = H.wire_mark(vbe)
    t0 = H.now_ns(vbe)
    H.act(vbe, "estop", open=True, k1_delay_ms=40)
    H.advance(vbe, 150)
    st = _ev(vbe, m0, "STOPPED")
    assert st and rc.STOP_CAUSE_NAME[st[0].fields["arg"]] == "ESTOP" and (st[0].t_ns - t0) / MS <= 3
    assert _ev(vbe, m0, "ESTOP_SET")
    dd = _ev(vbe, m0, "DRIVER_DISABLED")
    assert dd and dd[0].fields["arg"] == 3                                  # driver_disabled_cause 3 = ESTOP
    pw = [w for w in _ev(vbe, m0, "DRIVER_POWER") if w.fields["arg"] == 0]
    assert pw and 40 <= (pw[0].t_ns - t0) / MS <= 40 + 20 + 5, [(w.t_ns - t0) / MS for w in pw]   # + DRV_PWR filter
    last = H.rx(vbe, "DATA", since=m0)[-1]
    assert "HOMED" not in _flags(last) and "ESTOP" in _flags(last) and "DRV_PWR" not in last.fields["status"]
    status, detail = H.forced_outcome(vbe, "ENABLE")
    assert status == "E_STATE" and "ESTOP" in _block_names(detail)
    H.act(vbe, "estop", open=False)
    H.act(vbe, "drv_power", on=True)
    H.advance(vbe, int(H.param_default(pdict, "io.estop_release_ms")) + 20)
    assert H.result(vbe, H.estop_clear(vbe)).confirmed
    status, _ = H.forced_outcome(vbe, "ENABLE")
    assert status == "OK"


@pytest.mark.req("SYS-008", "SW-STOP-003")
def test_tc_sys_008_04_sim_driver_power_loss_with_estop_closed(vbe):
    """FI-15: DRV_PWR lost with the E-stop closed during a move → STOPPED DRV_POWER_LOST, DRIVER_POWER 0,
    DRIVER_DISABLED cause 4, HOMED cleared; motion refused with BLOCK DRV_UNPOWERED (SAF-FW-024)."""
    # Verifies: SYS-008, SW-STOP-003
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 100_000, 20_000))
    H.advance(vbe, 200)
    m0 = H.wire_mark(vbe)
    H.act(vbe, "drv_power", on=False)
    H.advance(vbe, 100)
    st = _ev(vbe, m0, "STOPPED")
    assert st and rc.STOP_CAUSE_NAME[st[0].fields["arg"]] == "DRV_POWER_LOST"
    assert [w for w in _ev(vbe, m0, "DRIVER_POWER") if w.fields["arg"] == 0]
    assert [w for w in _ev(vbe, m0, "DRIVER_DISABLED") if w.fields["arg"] == 4]
    assert "HOMED" not in _flags(H.rx(vbe, "DATA", since=m0)[-1])
    status, detail = H.forced_outcome(vbe, "ENABLE")
    assert status == "E_STATE" and "DRV_UNPOWERED" in _block_names(detail), _block_names(detail)
    H.act(vbe, "drv_power", on=True)


@pytest.mark.req("SYS-008", "SW-STOP-004")
def test_tc_sys_008_04_sim_pause_controlled_stop_blocks_until_resume(vbe):
    """PAUSE during a move: controlled stop (STOPPED PC_PAUSE, no POS_UNCERTAIN), EVENT PAUSED arg PC; a new
    MOVE_ABS and a JOG are refused (E_STATE BLOCK PAUSED); RESUME clears (PAUSE_CLEARED arg 3) without motion; a
    MOVE_ABS is then accepted (D-30, D-31)."""
    # Verifies: SYS-008, SW-STOP-004
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 100_000, 20_000))
    H.advance(vbe, 300)
    m0 = H.wire_mark(vbe)
    assert H.pause(vbe).sent
    H.advance(vbe, 300)
    st = _ev(vbe, m0, "STOPPED")
    assert st and rc.STOP_CAUSE_NAME[st[0].fields["arg"]] == "PC_PAUSE"
    p = _ev(vbe, m0, "PAUSED")
    assert p and rc.SOURCE[p[0].fields["arg"]] == "PC"
    assert "POS_UNCERTAIN" not in H.rx(vbe, "DATA", since=m0)[-1].fields["status"]
    x = _x_um(vbe)
    for name, f in (("MOVE_ABS", {"target_um": 10_000, "v_um_s": 5_000, "a_um_s2": 0}),
                    ("JOG", {"v_um_s": 1_000, "a_um_s2": 0, "bound_um": 10_000})):
        status, detail = H.forced_outcome(vbe, name, f, motion=True)
        assert status == "E_STATE" and "PAUSED" in _block_names(detail), (name, status, _block_names(detail))
    m1 = H.wire_mark(vbe)
    assert H.resume(vbe).ok
    H.advance(vbe, 200)
    pc = _ev(vbe, m1, "PAUSE_CLEARED")
    assert pc and pc[0].fields["arg"] == 3 and abs(_x_um(vbe) - x) < 1
    status, _ = H.forced_outcome(vbe, "MOVE_ABS", {"target_um": 10_000, "v_um_s": 5_000, "a_um_s2": 0}, motion=True)
    assert status == "OK"


@pytest.mark.req("SYS-008", "IF-005")
def test_tc_sys_008_04_sim_motion_never_duplicated_by_the_link(vbe):
    """IF-005 VERIFY with real motion: the MOVE_ABS response is lost → exactly one MOVE_ABS on the wire, the axis
    makes exactly one move to the target (MOVE_DONE TARGET once)."""
    # Verifies: SYS-008, IF-005
    H.forced_enable_home(vbe)
    H.act(vbe, "inject", fault="drop_next", cmd="MOVE_ABS", what="response", n=1)
    m0 = H.wire_mark(vbe)
    H.forced_move_abs(vbe, 30_000, 20_000)
    H.advance(vbe, 4000)
    assert len(H.tx(vbe, "MOVE_ABS", since=m0)) == 1
    d = _move_done(vbe, m0)
    assert len(d) == 1 and d[0].fields["value"] == 30_000


# ============================================================================================ B — pending M2

M2 = pytest.mark.pending("M2", needs="backend.motion (WP-B12), MotionController, motion gates")


def _ready(lockstep, **kw):
    be = lockstep(**kw)
    H.m2_ready(be)
    return be


@M2
@pytest.mark.req("SW-MAN-002", "IF-009", "SYS-003")
def test_tc_sw_man_002_02_move_to_and_move_by_absolute_um(lockstep):
    """move_to(20) → one MOVE_ABS 20 000 µm; move_by(+2.5) → 22 500 (base = commanded target); move_to(12.3455) →
    12 346 µm (round half away, SYS-003); no relative command exists on the wire."""
    # Verifies: SW-MAN-002, IF-009, SYS-003
    be = _ready(lockstep)
    for call, want in ((lambda: H.move_to(be, 20.0), 20_000), (lambda: H.move_by(be, 2.5), 22_500),
                       (lambda: H.move_to(be, 12.3455), 12_346)):
        m0 = H.wire_mark(be)
        H.result(be, call(), 30_000)
        mv = H.tx(be, "MOVE_ABS", since=m0)
        assert len(mv) == 1 and mv[0].fields["target_um"] == want, [w.fields for w in mv]
        assert H.run_until(be, lambda: not H.motion(be).moving, 30_000)
    assert not {"JOG"} & {w.name for w in H.tx(be)}


@M2
@pytest.mark.req("SW-MAN-003")
def test_tc_sw_man_003_01_latest_wins_pending_target(lockstep):
    """(a) three +1 mm clicks, each move completed in between → 11, 12, 13; (b) three +1 mm clicks during a running
    move → exactly 11 and 13 on the wire, the pending target 13 is shown in status()."""
    # Verifies: SW-MAN-003
    be = _ready(lockstep)
    H.result(be, H.move_to(be, 10.0), 30_000)
    assert H.run_until(be, lambda: not H.motion(be).moving, 30_000)
    m0 = H.wire_mark(be)
    for _ in range(3):
        H.result(be, H.move_by(be, 1.0), 30_000)
        assert H.run_until(be, lambda: not H.motion(be).moving, 30_000)
    assert [w.fields["target_um"] for w in H.tx(be, "MOVE_ABS", since=m0)] == [11_000, 12_000, 13_000]
    H.result(be, H.move_to(be, 10.0), 30_000)
    assert H.run_until(be, lambda: not H.motion(be).moving, 30_000)
    m1 = H.wire_mark(be)
    H.move_by(be, 1.0)
    H.advance(be, 20)
    H.move_by(be, 1.0)
    H.move_by(be, 1.0)
    assert H.motion(be).pending_target_mm == pytest.approx(13.0)
    assert H.run_until(be, lambda: not H.motion(be).moving and H.motion(be).pending_target_mm is None, 30_000)
    assert [w.fields["target_um"] for w in H.tx(be, "MOVE_ABS", since=m1)] == [11_000, 13_000]


@M2
@pytest.mark.req("SW-MAN-003", "SW-STOP-004")
@pytest.mark.parametrize("how", ["stop", "pause"])
def test_tc_sw_man_003_02_pending_target_dropped_on_stop_or_pause(lockstep, how):
    """A pending latest-wins target is dropped by STOP / PAUSE; no MOVE_ABS follows (D-30)."""
    # Verifies: SW-MAN-003, SW-STOP-004
    be = _ready(lockstep)
    H.move_to(be, 50.0)
    H.advance(be, 50)
    H.move_by(be, 5.0)
    assert H.motion(be).pending_target_mm is not None
    m0 = H.wire_mark(be)
    (H.stop if how == "stop" else H.pause)(be)
    H.advance(be, 3000)
    assert H.motion(be).pending_target_mm is None and not H.tx(be, "MOVE_ABS", since=m0)


@M2
@pytest.mark.req("SW-MAN-004")
def test_tc_sw_man_004_01_jog_refresh_and_stop(lockstep):
    """jog_start → JOG ≠ 0 at once, refreshed with max interval ≤ 100 ms for 5 s; jog_stop → JOG 0 (v = 0)."""
    # Verifies: SW-MAN-004
    be = _ready(lockstep)
    m0 = H.wire_mark(be)
    H.jog_start(be, +1, 2.0)
    for _ in range(100):
        H.advance(be, 50)
        be.gui_beat()
    H.jog_stop(be)
    H.advance(be, 300)
    jogs = H.tx(be, "JOG", since=m0)
    run = [w for w in jogs if w.fields["v_um_s"] != 0]
    gaps = [(b.t_ns - a.t_ns) / MS for a, b in zip(run, run[1:])]
    assert len(run) >= 45 and max(gaps) <= 100, max(gaps)
    assert jogs[-1].fields["v_um_s"] == 0


@M2
@pytest.mark.req("SW-MAN-004", "SAF-SW-003")
def test_tc_sw_man_004_01_jog_dead_man_without_gui_beat(lockstep, pdict):
    """GUI beat withheld while jogging → the backend stops refreshing (beat older than 300 ms) → FW dead-man stop
    (STOPPED JOG_DEADMAN) within jog_timeout_ms + 300 ms + 1 tick."""
    # Verifies: SW-MAN-004, SAF-SW-003
    be = _ready(lockstep)
    m0 = H.wire_mark(be)
    be.gui_beat()
    H.jog_start(be, +1, 2.0)
    jt = int(H.param_default(pdict, "motion.jog_timeout_ms"))
    assert H.until(be, lambda: bool(_ev(be, m0, "STOPPED")), 300 + jt + 200)
    assert rc.STOP_CAUSE_NAME[_ev(be, m0, "STOPPED")[0].fields["arg"]] == "JOG_DEADMAN"


@M2
@pytest.mark.req("SW-MAN-004", "SW-LIM-001")
def test_tc_sw_man_004_01_unhomed_jog_capped_and_unbounded(lockstep, pdict):
    """Un-homed jog: v ≤ motion.v_unhomed_um_s and bound = JOG_NO_BOUND (0x80000000, ICD §5.4)."""
    # Verifies: SW-MAN-004, SW-LIM-001
    be = lockstep()
    H.m2_ready(be, home_first=False)
    m0 = H.wire_mark(be)
    H.jog_start(be, +1, 50.0)
    H.advance(be, 200)
    H.jog_stop(be)
    H.advance(be, 300)
    run = [w for w in H.tx(be, "JOG", since=m0) if w.fields["v_um_s"] != 0]
    vmax = int(H.param_default(pdict, "motion.v_unhomed_um_s"))
    assert run and all(abs(w.fields["v_um_s"]) <= vmax and w.fields["bound_um"] == rc.JOG_NO_BOUND for w in run)


@M2
@pytest.mark.req("SW-MAN-005")
def test_tc_sw_man_005_01_speed_and_accel_caps_refused_with_maximum(lockstep, pdict):
    """Speed 31 mm/s (> v_max_travel 30) and accel > a_max → check() REFUSE / ERROR naming the allowed maximum,
    move_to fails, nothing on the wire."""
    # Verifies: SW-MAN-005
    be = _ready(lockstep)
    m0 = H.wire_mark(be)
    g = H.motion_check(be, "MOVE", speed_mm_s=31.0, target_mm=50.0)
    assert not g.ok and any("30" in (i.text or "") for i in g.items), g
    g = H.motion_check(be, "MOVE", accel_mm_s2=1000.0, target_mm=50.0)
    assert not g.ok
    with pytest.raises(Exception):  # noqa: B017 - GateRefused / ValueError per SW_design §5.4
        H.result(be, H.move_to(be, 50.0, speed_mm_s=31.0), 2000)
    assert not H.tx(be, "MOVE_ABS", since=m0)


@M2
@pytest.mark.req("SW-LIM-001", "IF-009")
def test_tc_sw_lim_001_01_targets_outside_sw_limits_refused_jog_bound(lockstep):
    """SW travel limits 10…200 mm: move_to(250) refused locally (nothing on the wire); a + jog carries bound_um =
    200 000 (the FW stops exactly there, MOVE_DONE BOUND)."""
    # Verifies: SW-LIM-001, IF-009
    be = _ready(lockstep)
    assert not H.set_travel_limits(be, 10.0, 200.0)
    m0 = H.wire_mark(be)
    with pytest.raises(Exception):  # noqa: B017
        H.result(be, H.move_to(be, 250.0), 2000)
    assert not H.tx(be, "MOVE_ABS", since=m0)
    H.result(be, H.move_to(be, 190.0), 60_000)
    assert H.run_until(be, lambda: not H.motion(be).moving, 60_000)
    m1 = H.wire_mark(be)
    H.jog_start(be, +1, 2.0)
    for _ in range(150):
        H.advance(be, 50)
        be.gui_beat()
        if _move_done(be, m1):
            break
    run = [w for w in H.tx(be, "JOG", since=m1) if w.fields["v_um_s"] != 0]
    assert run and all(w.fields["bound_um"] == 200_000 for w in run)
    d = _move_done(be, m1)
    assert d and rc.MOVE_DONE_REASON[d[0].fields["arg"]] == "BOUND" and d[0].fields["value"] == 200_000


@M2
@pytest.mark.req("SAF-SW-005", "SW-STOP-004", "SW-STOP-003")
@pytest.mark.parametrize("cond", ["not_enabled", "not_homed", "halt", "paused", "estop", "drv_unpowered", "alm"])
def test_tc_m2_gate_01_move_gate_refuses_and_sends_nothing(lockstep, cond):
    """Motion gate (SW_design §5.6): one REFUSE item per condition, item code = the generated BLOCK name where the
    FW has one; move_to through the API raises and **no MOVE_ABS** reaches the wire."""
    # Verifies: SAF-SW-005, SW-STOP-004, SW-STOP-003
    be = lockstep()
    code = {"not_enabled": "NOT_ENABLED", "not_homed": "NOT_HOMED", "halt": "HALT", "paused": "PAUSED",
            "estop": "ESTOP", "drv_unpowered": "DRV_UNPOWERED", "alm": "DRIVER_ALARM"}[cond]
    if cond != "not_enabled":
        H.m2_ready(be, home_first=cond != "not_homed")
    if cond == "halt":
        H.halt(be)
    elif cond == "paused":
        H.pause(be)
    elif cond == "estop":
        H.act(be, "estop", open=True)
    elif cond == "drv_unpowered":
        H.act(be, "drv_power", on=False)
    elif cond == "alm":
        H.act(be, "alm", active=True)
    H.advance(be, 300)
    g = H.gate(be, "MOVE")
    assert not g.ok and code in {str(i.code) for i in g.items}, g
    m0 = H.wire_mark(be)
    with pytest.raises(Exception):  # noqa: B017
        H.result(be, H.move_to(be, 20.0), 2000)
    assert not H.tx(be, "MOVE_ABS", since=m0)


@M2
@pytest.mark.req("SAF-SW-004")
def test_tc_saf_sw_004_02_disable_and_home_need_confirmation(lockstep):
    """disable() without the confirmation → refused, nothing on the wire; home() with the load unknown (no
    calibration) without load_confirmed → refused; with it → HOME flags bit0 = 1 (SAF-FW-021)."""
    # Verifies: SAF-SW-004
    be = lockstep()
    H.m2_ready(be, home_first=False)
    m0 = H.wire_mark(be)
    g = H.disable(be, confirmed=False)
    assert not g.ok and not H.tx(be, "DISABLE", since=m0)
    with pytest.raises(Exception):  # noqa: B017
        H.result(be, H.home(be, load_confirmed=False), 2000)
    assert not H.tx(be, "HOME", since=m0)
    H.home(be, load_confirmed=True)
    H.advance(be, 50)
    hm = H.tx(be, "HOME", since=m0)
    assert len(hm) == 1 and hm[0].fields["flags"] & 1


@M2
@pytest.mark.req("SW-STOP-002", "NFR-003")
def test_tc_sw_stop_002_02_hotkey_halts_and_repeats_until_confirmed(lockstep):
    """Fake hotkey press (GRQ-F-M2-01) while moving: HALT written at once (priority path), first 3 HALT requests
    lost → repeated every 50 ms, confirmed with attempts = 4; the move is ended (HALT latched, no restart)."""
    # Verifies: SW-STOP-002, NFR-003
    be = _ready(lockstep)
    H.move_to(be, 150.0)
    H.advance(be, 300)
    H.act(be, "inject", fault="drop_next", cmd="HALT", what="request", n=3)
    m0 = H.wire_mark(be)
    t0 = H.now_ns(be)
    H.hotkey_press(be)
    H.advance(be, 600)
    fr = H.tx(be, "HALT", since=m0)
    assert fr and fr[0].t_ns - t0 <= 1 * MS and len(fr) == 4
    conf = [r.payload for r in H.history(be, "stop.confirmed") if r.payload.cmd == "HALT"]
    assert conf and conf[-1].attempts == 4
    assert not H.motion(be).moving and H.indicator(be, "halt").state == "ON"


@pytest.mark.req("SYS-008")
@pytest.mark.parametrize("k1_ms", [100, 200, 2000])
def test_tc_sys_008_05_sim_k1_welded_timing(lockstep, k1_ms):
    """WP-B12 sim: E-stop open with DRV_PWR held on (drv_power_follows false) → no K1_WELDED before k1 − 50 ms, fault
    latched within (k1, k1 + 25 ms] (SAF-FW-025); normal case (power drops 60 ms after) → never."""
    # Verifies: SYS-008
    be = lockstep()
    assert H.result(be, H.write_verify(be, {"drv.k1_weld_ms": k1_ms})).ok
    m0 = H.wire_mark(be)
    t0 = H.now_ns(be)
    H.act(be, "estop", open=True, drv_power_follows=False)
    H.advance(be, k1_ms + 60)
    f = [w for w in _ev(be, m0, "FAULT_SET") if rc.FAULTS[w.fields["arg"]] == "K1_WELDED"]
    assert f and k1_ms < (f[0].t_ns - t0) / MS <= k1_ms + 25, [(w.t_ns - t0) / MS for w in f]
    H.act(be, "drv_power", on=False)
    H.act(be, "estop", open=False)
    be2 = lockstep()
    m1 = H.wire_mark(be2)
    H.act(be2, "estop", open=True, k1_delay_ms=60)
    H.advance(be2, 2500)
    assert not [w for w in _ev(be2, m1, "FAULT_SET") if rc.FAULTS[w.fields["arg"]] == "K1_WELDED"]


@pytest.mark.req("SYS-008")
def test_tc_sys_008_05_sim_idle_disable(lockstep, pdict):
    """WP-B12 sim (SAF-FW-017): unloaded and idle → ENA disabled, HOMED cleared, DRIVER_DISABLED cause IDLE at
    idle_disable_s ± 1 s; loaded (≥ release band) → still enabled after 2 × idle_disable_s; AFE stalled → none."""
    # Verifies: SYS-008
    idle = int(H.param_default(pdict, "safety.idle_disable_s"))
    be = lockstep()
    H.forced_enable_home(be)
    m0 = H.wire_mark(be)
    t0 = H.now_ns(be)
    H.advance(be, (idle + 2) * 1000, 5.0)
    dd = [w for w in _ev(be, m0, "DRIVER_DISABLED") if w.fields["arg"] == 2]
    assert dd and abs((dd[0].t_ns - t0) / 1e9 - idle) <= 1.0
    be2 = lockstep()
    H.forced_enable_home(be2)
    H.act(be2, "load_offset", counts=50_000 + 2 * int(H.param_default(pdict, "safety.release_band_raw")))
    m1 = H.wire_mark(be2)
    H.advance(be2, 2 * idle * 1000, 5.0)
    assert not [w for w in _ev(be2, m1, "DRIVER_DISABLED") if w.fields["arg"] == 2]


@M2
@pytest.mark.req("SYS-008")
def test_tc_sys_008_05_sim_load_limit_regrow(lockstep, pdict):
    """WP-B12 sim (SAF-FW-011): LOAD_LIMIT tripped by a stiff spring at the default FW threshold, FAULT_CLEAR accepted while still beyond the threshold;
    a move reducing the load runs; a move increasing it by > load_regrow_raw re-trips."""
    # Verifies: SYS-008
    be = lockstep()
    H.forced_enable_home(be)
    # default FW threshold (safety.load_raw_max 7 022 271 counts ≈ 2122 N at 3285 counts/N) reached ≈ 10.6 mm past
    # the contact of a 200 N/mm spring
    H.act(be, "specimen", kind="spring", k_n_per_mm=200.0, x_contact_um=10_000)
    m0 = H.wire_mark(be)
    H.result(be, H.forced_move_abs(be, 40_000, 5_000))
    assert H.until(be, lambda: any(rc.FAULTS[w.fields["arg"]] == "LOAD_LIMIT"
                                       for w in _ev(be, m0, "FAULT_SET")), 20_000)
    assert H.result(be, H.fault_clear(be)).confirmed
    x = _x_um(be)
    status, _ = H.forced_outcome(be, "MOVE_ABS", {"target_um": int(x) - 1_000, "v_um_s": 2_000, "a_um_s2": 0},
                                 motion=True)
    assert status == "OK"
    H.advance(be, 1500)
    m1 = H.wire_mark(be)
    H.result(be, H.forced_move_abs(be, int(x) + 20_000, 2_000))
    assert H.until(be, lambda: any(rc.FAULTS[w.fields["arg"]] == "LOAD_LIMIT"
                                       for w in _ev(be, m1, "FAULT_SET")), 20_000)
