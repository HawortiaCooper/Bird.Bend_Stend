"""Level C — M2 motion (SW_test_plan v0.3 §3.14): simulator motion fidelity (runs now, forced path) and the pre-written
MotionController / motion-gate / hotkey / WP-B12 cases (``pending("M2")``, armed with ``--arm M2`` at the M2
verification).

Part A (runs against the current simulator): the simulator is B's test environment (plan §1 rule 3); before the
M2/M3 SW motion cases can rely on it, its motion behaviour is checked against the **SRS §3.2 / ICD** semantics
(expected values from the ICD, ``params.yaml`` defaults and F's oracle ``f_ref`` — never from the simulator code).
Stimuli use the forced wire path (``harness.forced_*``); verdicts and events are read from the wire with
``ref_codec``. The same scenarios run against the FW twin at M2 (X, Integrator's ``test_sim_vs_twin.py``).

Part A also covers the WP-B12 simulator items already delivered (K1_WELDED timing, idle disable, load regrow).
Part B (pending M2): public-API motion (``backend.motion``), motion gates and the hotkey.

Verifies: SYS-008, IF-009, SW-MAN-002, SW-MAN-003, SW-MAN-004, SW-MAN-005, SW-LIM-001, SW-STOP-002, SW-STOP-003,
SW-STOP-004, SAF-SW-004, SAF-SW-005
"""
from __future__ import annotations

import json
import time
from pathlib import Path

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


def _sense_lockstep(lockstep, tmp_path, *, k1: bool = False):
    """Scenario with the optional 48 V presence sense on (ICD v0.7 default off, CR-03 / D-41) and optionally the K1 check
    (D-43 e) — both boot-latched, set through the scenario ``params`` (written into RAM, then re-latched)."""
    sc = {"schema": "bird.bend.simscenario", "version": 1, "world": {"load_offset_counts": 50_000},
          "params": {"motion.steps_per_mm": 800.0, "drv.pwr_sense_enable": True, "drv.k1_check_enable": bool(k1)}}
    path = tmp_path / f"sense_k1_{int(k1)}.simscn.json"
    path.write_text(json.dumps(sc), encoding="utf-8")
    return lockstep(endpoint=f"sim:{path}")


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
def test_tc_sys_008_04_sim_estop_cuts_driver_power(lockstep, tmp_path, pdict):
    """(v0.3.2: optional presence sense ON via scenario, CR-03) E-stop during a move with a power-removal device
    (``drv_power_follows``): immediate stop (STOPPED ESTOP),
    ESTOP_SET, DRIVER_DISABLED cause ESTOP, HOMED cleared; DRIVER_POWER 0 follows ``k1_delay_ms`` + the 20 ms DRV_PWR
    filter later (sense on, SAF-FW-024, SRS A-19);
    ENABLE refused (E_STATE ESTOP) until the input is closed ≥ ``io.estop_release_ms`` and ESTOP_CLEAR is sent."""
    # Verifies: SYS-008, SW-STOP-003, SAF-SW-005
    vbe = _sense_lockstep(lockstep, tmp_path)
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
def test_tc_sys_008_04_sim_driver_power_loss_with_estop_closed(lockstep, tmp_path):
    """(v0.3.2: presence sense ON via scenario, CR-03) FI-15: DRV_PWR lost with the E-stop closed during a move → STOPPED DRV_POWER_LOST, DRIVER_POWER 0,
    DRIVER_DISABLED cause 4, HOMED cleared; motion refused with BLOCK DRV_UNPOWERED (SAF-FW-024)."""
    # Verifies: SYS-008, SW-STOP-003
    vbe = _sense_lockstep(lockstep, tmp_path)
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


@pytest.mark.req("SYS-008", "SAF-SW-005")
def test_tc_sys_008_04_sim_limit_wiring_clear_rule_d40(vbe):
    """D-40 (a): both limit inputs active → FAULT LIMIT_WIRING (immediate stop); FAULT_CLEAR is refused while both
    are active and accepted once they are no longer **both** active; the input still active keeps acting as a normal
    limit latch (motion toward it refused, away accepted)."""
    # Verifies: SYS-008, SAF-SW-005
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 50_000, 10_000))
    assert H.until(vbe, lambda: _x_um(vbe) == 50_000 and "MOVING" not in _flags(H.rx(vbe, "DATA")[-1]), 10_000)
    m0 = H.wire_mark(vbe)
    H.act(vbe, "limit", name="start", active=True)
    H.act(vbe, "limit", name="end", active=True)
    H.advance(vbe, 50)
    assert [w for w in _ev(vbe, m0, "FAULT_SET") if rc.FAULTS[w.fields["arg"]] == "LIMIT_WIRING"]
    status, _ = H.forced_outcome(vbe, "FAULT_CLEAR")
    assert status == "E_CAUSE_ACTIVE"
    H.act(vbe, "limit", name="end", active=False)
    H.advance(vbe, 100)
    status, _ = H.forced_outcome(vbe, "FAULT_CLEAR")
    assert status == "OK"
    status, detail = H.forced_outcome(vbe, "MOVE_ABS", {"target_um": 40_000, "v_um_s": 5_000, "a_um_s2": 0},
                                      motion=True)
    assert status == "E_STATE" and "LIMIT" in _block_names(detail), (status, _block_names(detail))
    status, _ = H.forced_outcome(vbe, "MOVE_ABS", {"target_um": 60_000, "v_um_s": 5_000, "a_um_s2": 0}, motion=True)
    assert status == "OK"
    H.act(vbe, "limit", name="start", active=False)


# ============================================================================================ B — M2 public API (armed at the M2 gate)

# v0.3 / M2 gate: armed (B declared the M2 backend done, commit 099af88) — pending("M2") markers removed.


def _ready(lockstep, **kw):
    be = lockstep(**kw)
    H.m2_ready(be)
    return be


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


@pytest.mark.req("SW-MAN-006", "SAF-SW-005", "SW-STOP-004", "SW-STOP-003")
@pytest.mark.parametrize("cond", ["not_enabled", "not_homed", "halt", "paused", "estop", "drv_unpowered", "alm"])
def test_tc_sw_man_006_02_move_gate_refuses_and_sends_nothing(lockstep, tmp_path, cond):
    """TC-SW-MAN-006-02 — motion gate (SW_design §5.6): one REFUSE item per condition, item code = the generated BLOCK name where the
    FW has one; move_to through the API raises and **no MOVE_ABS** reaches the wire."""
    # Verifies: SW-MAN-006, SAF-SW-005, SW-STOP-004, SW-STOP-003
    be = _sense_lockstep(lockstep, tmp_path) if cond == "drv_unpowered" else lockstep()   # sense: CR-03 option
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
def test_tc_sys_008_05_sim_k1_welded_timing(lockstep, tmp_path, k1_ms):
    """WP-B12 sim: E-stop open with DRV_PWR held on (drv_power_follows false) → no K1_WELDED before k1 − 50 ms, fault
    latched within (k1, k1 + 25 ms] (SAF-FW-025); normal case (power drops 60 ms after) → never. v0.3.2: needs
    ``drv.pwr_sense_enable`` and ``drv.k1_check_enable`` (D-43 e, both boot-latched, scenario params)."""
    # Verifies: SYS-008
    be = _sense_lockstep(lockstep, tmp_path, k1=True)
    assert H.result(be, H.write_verify(be, {"drv.k1_weld_ms": k1_ms})).ok
    m0 = H.wire_mark(be)
    t0 = H.now_ns(be)
    H.act(be, "estop", open=True, drv_power_follows=False)
    H.advance(be, k1_ms + 60)
    f = [w for w in _ev(be, m0, "FAULT_SET") if rc.FAULTS[w.fields["arg"]] == "K1_WELDED"]
    assert f and k1_ms < (f[0].t_ns - t0) / MS <= k1_ms + 25, [(w.t_ns - t0) / MS for w in f]
    H.act(be, "drv_power", on=False)
    H.act(be, "estop", open=False)
    be2 = _sense_lockstep(lockstep, tmp_path, k1=True)
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


# ============================================================================================ C — M2 gate additions

@pytest.mark.req("SW-STOP-001", "IF-005")
@pytest.mark.defect("F-MC-4")
def test_tc_sw_stop_001_05_stop_confirmation_is_not_a_str(vbe):
    """F-MC-4 (M1 condition): the ``stop.confirmed`` payload is the ``StopConfirmation`` dataclass only — the v0.3
    str-equality shim is gone (a consumer comparing with "STOP" can no longer pass by accident)."""
    # Verifies: SW-STOP-001, IF-005
    t0 = H.now_ns(vbe)
    assert H.stop(vbe).sent
    H.advance(vbe, 200)
    conf = [r.payload for r in H.history(vbe, "stop.confirmed") if r.t_host_ns >= t0]
    assert conf and conf[-1].cmd == "STOP" and conf[-1].confirmed
    assert conf[-1] != "STOP" and "STOP" != conf[-1] and not isinstance(conf[-1], str)


@pytest.mark.req("SAF-SW-002", "SW-CFG-003")
def test_tc_saf_sw_002_05_manual_thresholds_h2_safe_verified_and_failure_blocks_motion(lockstep, pdict):
    """SAF-SW-002 M2 part (ThresholdManager, B4-04): manual raw thresholds → SET_PARAM of the three session values with
    H2 (load_raw_min < load_raw_max) true after **every** SET, each read back by GET_PARAM, state VERIFIED; an injected
    store mismatch → FAILED and the motion gates REFUSE (THRESHOLDS_UNVERIFIED), nothing moves."""
    # Verifies: SAF-SW-002, SW-CFG-003
    # thresholds chosen BELOW the defaults (−7 022 271 … 7 022 271) so that the H2-safe order matters: max must be
    # lowered after min (else min ≥ max in between); zero_raw must lie between them (B's rule)
    be = lockstep()
    H.m2_ready(be)
    ids = {p.id: p.key for p in pdict.params}
    vals = {p.key: p.default for p in pdict.params}
    m0 = H.wire_mark(be)
    st = H.result(be, be.limits.set_manual_thresholds_async(-6_000_000, -5_000_000, -5_500_000))
    assert st.state == "VERIFIED" and st.raw_min == -6_000_000 and st.raw_max == -5_000_000, st
    st = H.result(be, be.limits.set_manual_thresholds_async(-4_500_000, -4_200_000, -4_300_000))
    assert st.state == "VERIFIED", st                 # new min > old max → max must be raised first (H2)
    sets = H.tx(be, "SET_PARAM", since=m0)
    keys = []
    for w in sets:
        k = ids[w.fields["id"]]
        vals[k] = w.fields["value"]
        keys.append(k)
        assert f_ref.rule_ok("H2", vals), (k, vals["safety.load_raw_min"], vals["safety.load_raw_max"])
    assert {"safety.load_raw_min", "safety.load_raw_max"} <= set(keys)
    reads = {ids[w.fields["id"]] for w in H.tx(be, "GET_PARAM", since=m0)}
    assert set(keys) <= reads
    H.inject_store_mismatch(be, "safety.load_raw_max", -4_000_001)
    st = H.result(be, be.limits.set_manual_thresholds_async(-6_000_000, -4_000_000, -5_500_000))
    assert st.state == "FAILED", st
    H.advance(be, 100)
    g = H.gate(be, "MOVE")
    assert not g.ok and "THRESHOLDS_UNVERIFIED" in {str(i.code) for i in g.items}, g
    m1 = H.wire_mark(be)
    with pytest.raises(Exception):  # noqa: B017
        H.result(be, H.move_to(be, 30.0), 2000)
    assert not H.tx(be, "MOVE_ABS", since=m1)


@pytest.mark.rt
@pytest.mark.req("SW-STOP-002", "NFR-003")
def test_tc_sw_stop_002_05_hotkey_fake_backend_halts_and_test_mode_does_not(tmp_path):
    """SW-STOP-002 (C, real clock, fake hotkey backend — the Win32 part is W on the REF PC): status().hotkey active;
    a key press writes HALT within 50 ms; in test mode (``hotkey_test_start``) the press is only measured (topic
    ``hotkey.test`` with a delay) and **no** HALT is sent."""
    # Verifies: SW-STOP-002, NFR-003
    be = H.realtime_backend(recordings_root=str(tmp_path / "rec"), hotkey="fake")
    try:
        H.connect(be, "sim").result(10)
        assert H.wait_rt(lambda: H.status(be).stream.on, 5)
        hk = H.status(be).hotkey
        assert hk.mode in ("REGISTERED", "LL_HOOK"), hk
        got = []
        H.subscribe(be, "hotkey.test", lambda rec: got.append(rec))
        g = be.hotkey_test_start(2.0)
        assert g.ok, g
        m0 = H.wire_mark(be)
        H.hotkey_press(be)
        assert H.wait_rt(lambda: bool(got), 3)
        time.sleep(0.2)
        assert not H.tx(be, "HALT", since=m0)
        assert H.wait_rt(lambda: not H.status(be).hotkey.test_running, 3)
        m1 = H.wire_mark(be)
        t0 = time.monotonic_ns()
        H.hotkey_press(be)
        assert H.wait_rt(lambda: bool(H.tx(be, "HALT", since=m1)), 2)
        assert (H.tx(be, "HALT", since=m1)[0].t_ns - t0) / MS <= 50
    finally:
        be.shutdown()


@pytest.mark.req("SYS-008", "SAF-SW-005", "SW-STOP-003")
def test_tc_sys_008_07_sim_release1_no_contactor_d41(vbe, pdict):
    """MC2-1 / D-41 / CR-03 (ICD v0.7 defaults ``drv.pwr_sense_enable`` = ``drv.k1_check_enable`` = false): E-stop during
    a move with the driver supply staying on (no power-removal device, ``drv_power_follows`` false) → STOPPED ESTOP
    ≤ 3 ms, ESTOP_SET, DRIVER_DISABLED cause ESTOP, HOMED cleared; **no** DRIVER_POWER event, **no** K1_WELDED; DRV_PWR
    reads 1; ENABLE refused until released ≥ ``io.estop_release_ms`` + ESTOP_CLEAR; a DRV_POWER input change is
    ignored (no stop)."""
    # Verifies: SYS-008, SAF-SW-005, SW-STOP-003
    assert H.param_default(pdict, "drv.pwr_sense_enable") in (0, False)
    assert H.param_default(pdict, "drv.k1_check_enable") in (0, False)
    assert H.config_values(vbe)["drv.pwr_sense_enable"] in (0, False)
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 100_000, 20_000))
    H.advance(vbe, 200)
    m0 = H.wire_mark(vbe)
    t0 = H.now_ns(vbe)
    H.act(vbe, "estop", open=True, drv_power_follows=False)
    H.advance(vbe, 600)
    st = _ev(vbe, m0, "STOPPED")
    assert st and rc.STOP_CAUSE_NAME[st[0].fields["arg"]] == "ESTOP" and (st[0].t_ns - t0) / MS <= 3
    assert _ev(vbe, m0, "ESTOP_SET")
    assert [w for w in _ev(vbe, m0, "DRIVER_DISABLED") if w.fields["arg"] == 3]
    assert not [w for w in _ev(vbe, m0, "FAULT_SET") if rc.FAULTS[w.fields["arg"]] == "K1_WELDED"]
    assert not _ev(vbe, m0, "DRIVER_POWER")
    last = H.rx(vbe, "DATA", since=m0)[-1]
    assert "DRV_PWR" in last.fields["status"] and "HOMED" not in _flags(last)
    status, detail = H.forced_outcome(vbe, "ENABLE")
    assert status == "E_STATE" and "ESTOP" in _block_names(detail)
    H.act(vbe, "estop", open=False)
    H.advance(vbe, int(H.param_default(pdict, "io.estop_release_ms")) + 20)
    assert H.result(vbe, H.estop_clear(vbe)).confirmed
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 30_000, 10_000))
    H.advance(vbe, 200)
    m1 = H.wire_mark(vbe)
    H.act(vbe, "drv_power", on=False)                                    # input ignored with the sense off
    H.advance(vbe, 300)
    assert not _ev(vbe, m1, "STOPPED") and not _ev(vbe, m1, "DRIVER_POWER")
    assert H.until(vbe, lambda: any(rc.MOVE_DONE_REASON[w.fields["arg"]] == "TARGET" for w in _move_done(vbe, m1)),
                   10_000)
    H.act(vbe, "drv_power", on=True)


@pytest.mark.req("SYS-008")
def test_tc_sys_008_05_sim_sense_on_k1_check_off_never_k1(lockstep, tmp_path):
    """D-43 (e): presence sense on but ``drv.k1_check_enable`` false → an E-stop with the supply held is reported and
    never latches K1_WELDED."""
    # Verifies: SYS-008
    be = _sense_lockstep(lockstep, tmp_path, k1=False)
    m0 = H.wire_mark(be)
    H.act(be, "estop", open=True, drv_power_follows=False)
    H.advance(be, 2500)
    assert _ev(be, m0, "ESTOP_SET")
    assert not [w for w in _ev(be, m0, "FAULT_SET") if rc.FAULTS[w.fields["arg"]] == "K1_WELDED"]


@pytest.mark.req("SYS-008", "SW-MAN-004")
def test_tc_sys_008_04_sim_unhomed_window_d43(vbe):
    """D-43 (b): un-homed jog travel is bounded by the un-homed origin ± ``home.max_travel_um`` latched when the axis
    became un-homed — not from each jog start: a held + jog stops at origin + max (MOVE_DONE SOFT_LIMIT); a new + jog
    from there is refused (E_RANGE); a − jog is accepted."""
    # Verifies: SYS-008, SW-MAN-004
    assert H.result(vbe, H.write_verify(vbe, {"home.max_travel_um": 3_000})).ok
    status, _ = H.forced_outcome(vbe, "ENABLE")
    assert status == "OK"
    H.advance(vbe, 700)
    x0 = _x_um(vbe)
    m0 = H.wire_mark(vbe)
    jog = {"v_um_s": 2_000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}
    H.forced_request(vbe, "JOG", jog, motion=True)
    for _ in range(60):
        H.advance(vbe, 80)
        if _move_done(vbe, m0):
            break
        H.forced_request(vbe, "JOG", jog, motion=True)
    d = _move_done(vbe, m0)
    assert d and rc.MOVE_DONE_REASON[d[0].fields["arg"]] == "SOFT_LIMIT", [w.fields for w in d]
    assert abs(d[0].fields["value"] - (x0 + 3_000)) <= 1, (d[0].fields["value"], x0)
    H.advance(vbe, 300)
    status, _ = H.forced_outcome(vbe, "JOG", jog, motion=True)
    assert status == "E_RANGE", status
    status, _ = H.forced_outcome(vbe, "JOG", {"v_um_s": -1_000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND},
                                 motion=True)
    assert status == "OK"


@pytest.mark.req("SAF-SW-002")
@pytest.mark.parametrize("k, tare, level, exp_min, exp_max, clamped", [
    (1 / 3285, 125_000, 2157.463, -6_962_265, 7_151_121, True),      # VV-THR-01 (raw max 7 212 265 → clamp)
    (-1 / 3285, 125_000, 2157.463, -6_962_265, 7_151_121, True),     # VV-THR-02 (negative K: sides swap)
    (1 / 3285, 0, 1000.0, -3_284_999, 3_284_999, False),             # VV-THR-03
], ids=["VV-THR-01", "VV-THR-02", "VV-THR-03"])
def test_tc_saf_sw_002_01_threshold_vectors(k, tare, level, exp_min, exp_max, clamped, pdict):
    """TC-SAF-SW-002-01 (U, early at M2): production ``core.safety.calibrated_target`` / ``calc.loadcal`` against
    F's oracle ``f_ref.fw_raw_limits`` + the dictionary clamp (VV-THR-01…03, plan §4.2); effective pull level of
    VV-THR-01 = 2138.8496 N."""
    # Verifies: SAF-SW-002
    from bend_stand.core.safety import calibrated_target

    lo, hi = f_ref.fw_raw_limits(level, -level, k, tare)
    rng = {p.key: (p.min, p.max) for p in pdict.params}
    lo_c = min(max(lo, rng["safety.load_raw_min"][0]), rng["safety.load_raw_min"][1])
    hi_c = min(max(hi, rng["safety.load_raw_max"][0]), rng["safety.load_raw_max"][1])
    assert (lo_c, hi_c) == (exp_min, exp_max)
    t = calibrated_target(k, tare, level)
    assert (t.raw_min, t.raw_max, t.clamped, t.invalid) == (exp_min, exp_max, clamped, None)
    if clamped and k > 0:
        assert t.eff_pull_n == pytest.approx(2138.8496, abs=1e-4)


LLV = json.loads((Path(__file__).resolve().parents[3] / "00_System" / "tools" / "vectors" /
                  "loadlim_vectors.json").read_text(encoding="utf-8"))


@pytest.mark.req("SYS-008", "IF-010")
@pytest.mark.parametrize("case", LLV["cases"], ids=lambda c: c["name"])
def test_tc_sys_008_06_sim_load_limit_equals_loadlim_vectors(case):
    """Simulator fidelity (rule 3): the simulator's FW load-limit model replays every Integrator ``loadlim_vectors``
    case (ICD v0.6 §5.5, D-12, SAF-FW-008…011, D-40 d regrow reference): trip per sample, regrow window state,
    reference at FAULT_CLEAR."""
    # Verifies: SYS-008, IF-010
    from bend_stand.io.sim.loadlim import LoadLimit

    i = case["init"]
    ll = LoadLimit()
    ll.config(i["load_raw_min"], i["load_raw_max"], i["trip_samples"], i["regrow"])
    for n, s in enumerate(case["steps"]):
        if s["op"] == "sample":
            assert ll.sample(s["raw"]) == s["trip"], (n, s)
        elif s["op"] == "fault_clear":
            ll.fault_clear()
            assert ll.ref == s["ref"], (n, s)
        else:
            ll.config(s["load_raw_min"], s["load_raw_max"], s["trip_samples"], s["regrow"])
        assert ll.window == s["regrow_window"], (n, s)


@pytest.mark.req("IF-005", "SW-MAN-002")
@pytest.mark.parametrize("what", ["response", "request"])
def test_tc_if_005_02_m2_move_to_verify_never_resent(lockstep, what):
    """TC-IF-005-02 M2 part through the public API: MOVE_ABS of ``move_to`` with its response lost → the move runs
    once and the ticket resolves DONE (GET_STATUS shows it executing); with its **request** lost → GET_STATUS shows
    no move → ticket NOT_EXECUTED; in both cases exactly **one** MOVE_ABS on the wire (VERIFY, never re-sent)."""
    # Verifies: IF-005, SW-MAN-002
    be = _ready(lockstep)
    H.act(be, "inject", fault="drop_next", cmd="MOVE_ABS", what=what, n=1)
    m0 = H.wire_mark(be)
    out = H.result(be, H.move_to(be, 20.0), 30_000)
    H.advance(be, 500)
    assert len(H.tx(be, "MOVE_ABS", since=m0)) == 1
    assert H.tx(be, "GET_STATUS", since=m0)
    if what == "response":
        assert out.kind == "DONE" and out.done.reason == "TARGET", out
        assert len(_move_done(be, m0)) == 1
    else:
        assert out.kind == "NOT_EXECUTED", out
        assert not _move_done(be, m0) and not H.motion(be).moving


@pytest.mark.req("SW-MAN-002", "SAF-SW-003")
def test_tc_sw_man_002_03_board_reset_cancels_ticket_no_automatic_motion(lockstep):
    """FI-23 (M2 part): board reset during a ``move_to`` → ticket CANCELLED; after the reconnect no MOVE_ABS / HOME /
    ENABLE / JOG is sent automatically (no re-enable, no re-issue)."""
    # Verifies: SW-MAN-002, SAF-SW-003
    be = _ready(lockstep)
    t = H.move_to(be, 150.0)
    H.advance(be, 300)
    H.act(be, "reset", cause="pin")
    out = H.result(be, t, 5000)
    assert out.kind == "CANCELLED", out
    m0 = H.wire_mark(be)
    H.advance(be, 5000)
    assert not {"MOVE_ABS", "HOME", "ENABLE", "JOG", "MOVE_UNTIL_LOAD"} & {w.name for w in H.tx(be, since=m0)}


@pytest.mark.req("SAF-SW-002")
def test_tc_saf_sw_002_06_threshold_rules_refused_locally():
    """ThresholdManager input rules (pure, M2): out-of-range raw value → RANGE; raw_min ≥ raw_max → H2; zero outside
    → refused; a calibration whose rounded thresholds cross (tiny level) → INVALID target, never written."""
    # Verifies: SAF-SW-002
    from bend_stand.core.safety import calibrated_target, check_manual

    assert [i.code for i in check_manual(-9_000_000, 1000, 0)] == ["RANGE"]
    assert [i.code for i in check_manual(1000, 1000, 1000)] == ["H2"]
    assert [i.code for i in check_manual(-1000, 1000, 5000)] == ["ZERO_OUTSIDE"]
    assert not check_manual(-1000, 1000, 0)
    assert calibrated_target(1.0, 0.5, 1e-9).invalid
    assert calibrated_target(1 / 3285, 8_000_000, 1000.0).invalid       # tare outside the clamped range


# ============================================================================================ D — M2 close-out re-test

@pytest.mark.req("SW-CFG-004", "IF-011")
@pytest.mark.defect("MC2-4")
def test_tc_sw_cfg_004_02_save_quiesce_with_simulated_flash_stall(vbe):
    """MC2-4 (OI-F-M2-04): the simulator now stalls SAVE_PARAMS like the FW (default 500 ms, no injection). The SW sends
    only STOP / HALT / PAUSE while the SAVE is outstanding; the HALT written during the stall is executed by the board
    right after the flash operation (HALT latched at the end, D-37 a)."""
    # Verifies: SW-CFG-004, IF-011
    m0 = H.wire_mark(vbe)
    fut = H.save_nvm(vbe)
    H.advance(vbe, 200)
    rd = H.read_all(vbe)
    H.advance(vbe, 100)
    assert H.halt(vbe).sent
    H.advance(vbe, 700)
    H.result(vbe, fut)
    H.result(vbe, rd)
    req = H.tx(vbe, "SAVE_PARAMS", since=m0)[0]
    resp = [w for w in H.rx(vbe, "SAVE_PARAMS", since=m0, kind="response") if w.seq == req.seq][0]
    assert (resp.t_ns - req.t_ns) / MS >= 450, (resp.t_ns - req.t_ns) / MS
    between = {w.name for w in H.tx(vbe, since=m0) if req.t_ns < w.t_ns < resp.t_ns}
    assert between and between <= {"STOP", "HALT", "PAUSE"}, between
    assert any(w.name == "GET_ALL_PARAMS" and w.t_ns > resp.t_ns for w in H.tx(vbe, since=m0))
    H.advance(vbe, 200)
    assert H.indicator(vbe, "halt").state == "ON"


@pytest.mark.req("SAF-SW-002", "SYS-003")
@pytest.mark.defect("SWD-M2-02")
@pytest.mark.parametrize("tare, zero", [(2.5, 3), (-2.5, -3), (3.5, 4), (2.4999, 2)])
def test_tc_saf_sw_002_07_zero_raw_rounds_half_away(tare, zero):
    """SWD-M2-02 regression: the session zero of the calibrated threshold path rounds half away from zero (SYS-003,
    oracle ``f_ref.rha``), not to even."""
    # Verifies: SAF-SW-002, SYS-003
    from bend_stand.core.safety import calibrated_target

    assert f_ref.rha(tare) == zero
    assert calibrated_target(1 / 3285, tare, 1000.0).zero_raw == zero


@pytest.mark.req("SAF-SW-005", "SAF-SW-004")
@pytest.mark.defect("SWD-M2-01")
def test_tc_saf_sw_005_05_no_contactor_texts_d41():
    """SWD-M2-01 regression (D-41 / D-42, CR-03): no clear hint or E-stop gate text asks for a contactor RESET / K1
    action; K1_WELDED / DRV_PWR hints refer to the optional presence sense."""
    # Verifies: SAF-SW-005, SAF-SW-004
    from bend_stand.core.gates import CLEAR_HINTS

    for k, t in CLEAR_HINTS.items():
        assert "RESET" not in t and "K1" not in t, (k, t)
    for k in ("K1_WELDED", "DRV_PWR", "DRV_UNPOWERED"):
        assert "optional" in CLEAR_HINTS[k], (k, CLEAR_HINTS[k])
    import inspect

    from bend_stand.core import gates

    src = inspect.getsource(gates.g_estop_clear)
    assert "K1" not in src and "RESET" not in src
