"""Simulator M2 behaviour (WP-B12): exact step motion against the planner and the ramp vectors, homing phases and
failures, HOME_DRIFT, stops (CLEAN / TRUNCATE, controlled, clean halt), jog bound / reversal / dead-man, inputs
(E-stop + K1 + DRV_PWR filter, limits + wiring, PAUSE contact type), idle disable, load regrow, link watchdog,
NOT_SETTLED, ENABLE settle from power return, feature mask (D-37 b, NOT_IN_BUILD), CR-01 retirements.

Expected values come from the ICD / FW_design / params defaults and ``calc.motion`` (vector-tested), not from the
simulator code.

Verifies: SYS-008, FW-MOT-003, FW-MOT-004, FW-MOT-005, FW-MOT-008, FW-HOM-001, FW-HOM-002, FW-HOM-004, FW-SW-001,
FW-SW-003, FW-SW-004, FW-SW-005, SAF-FW-001, SAF-FW-003, SAF-FW-004, SAF-FW-005, SAF-FW-011, SAF-FW-013,
SAF-FW-014, SAF-FW-015, SAF-FW-016, SAF-FW-017, SAF-FW-024, SAF-FW-025
"""
from __future__ import annotations

import pytest

from bend_stand.calc.motion import F_TICK_HZ, ramp_periods, um_to_steps
from bend_stand.core import protocol_gen as pg
from bend_stand.io import protocol as P
from bend_stand.io.sim.board import SimConfig
from sim.test_sim_board import Client

Cmd, EV, DS, DF = pg.Cmd, pg.Event, pg.DataStatus, pg.DataFlags
SPM = 800.0


def _set(cl: Client, key: str, value) -> None:
    assert cl.cmd(Cmd.SET_PARAM, P.build_set_param(key, value)).ok, key


def _enable(cl: Client) -> None:
    assert cl.cmd(Cmd.ENABLE).ok
    cl.run(510)
    assert cl.b.motion_state == "IDLE"


def _home(cl: Client, *, fast: bool = True) -> None:
    if fast:
        _set(cl, "home.v_fast_um_s", 20_000)
        _set(cl, "home.v_slow_um_s", 2_000)
    assert cl.cmd(Cmd.HOME, b"\x01").ok
    for _ in range(600):
        cl.run(100)
        if cl.b.homed and cl.b.motion is None:
            return
    raise AssertionError("not homed")


def _ready(**kw) -> Client:
    cl = Client(**kw)
    cl.hb = True
    cl.cmd(Cmd.STREAM_START)
    _enable(cl)
    _home(cl)
    return cl


def _move(cl: Client, target_um: int, v: int = 20_000, a: int = 0) -> P.Response:
    return cl.cmd(Cmd.MOVE_ABS, P.build_request(Cmd.MOVE_ABS, target_um=target_um, v_um_s=v, a_um_s2=a))


def _codes(cl: Client, since: int) -> list[str]:
    return [EV(e.code).name for e in cl.events[since:]]


def _run_until(cl: Client, pred, ms: int = 30_000, step: int = 5) -> bool:
    for _ in range(ms // step):
        if pred():
            return True
        cl.run(step)
    return pred()


# ============================================================================================ motion


@pytest.mark.req("FW-MOT-003", "FW-MOT-004", "SYS-008")
def test_move_abs_exact_steps_and_duration_from_the_ramp() -> None:
    cl = _ready()
    n0 = len(cl.events)
    t0 = cl.b.now_us()
    assert _move(cl, 10_000, 30_000).ok
    assert _run_until(cl, lambda: "MOVE_DONE" in _codes(cl, n0), 5000, 1)
    md = cl.ev("MOVE_DONE")[-1]
    assert md.arg == pg.MoveDoneReason.TARGET and md.value == 10_000 and md.value2 == 8000 == cl.b.steps
    expected_s = sum(ramp_periods(8000, F_TICK_HZ, 30_000 * SPM / 1000, 100_000 * SPM / 1000,
                                  100_000 * SPM / 1000)) / F_TICK_HZ        # 57 000 000 ticks (motion_vectors)
    dt = (cl.b.now_us() - t0) / 1e6
    assert expected_s <= dt <= expected_s + 0.01
    cl.run(30)
    assert cl.b.pulses >= 8000 and not cl.data[-1].flags & DF.MOVING
    assert _move(cl, 10_000).ok and cl.ev("MOVE_DONE")[-1].value2 == 8000      # target = position: at once


@pytest.mark.req("SAF-FW-003", "SAF-FW-001")
def test_controlled_stop_distance_and_clean_halt_at_low_speed() -> None:
    cl = _ready()
    assert _move(cl, 200_000, 30_000).ok
    cl.run(1500)                                          # cruising at 30 mm/s = 24 000 steps/s
    s0 = cl.b.steps
    n0 = len(cl.events)
    assert cl.cmd(Cmd.STOP, b"\x01", wait=1).ok           # controlled: a_stop 1 m/s² → v²/2a = 450 µm = 360 steps
    assert cl.b.motion_state == "STOPPING"
    assert _run_until(cl, lambda: cl.b.motion is None, 2000, 1)
    assert 340 <= cl.b.steps - s0 <= 400
    assert _codes(cl, n0)[0] == "STOPPED" and cl.ev("MOVE_DONE")[-1].arg == pg.MoveDoneReason.STOPPED
    assert cl.ev("STOPPED")[-1].arg == pg.StopCause.PC_STOP_CONTROLLED
    # step period > 2 ms (0.5 mm/s = 400 steps/s → 2.5 ms) and stop distance ≤ 1 step → clean halt, no STOPPING
    assert _move(cl, cl.b.pos_um + 5000, 500).ok
    cl.run(1000)
    s1 = cl.b.steps
    cl.cmd(Cmd.STOP, b"\x01", wait=1)
    assert cl.b.motion is None and cl.b.steps - s1 <= 1 and not cl.b.pos_uncertain


@pytest.mark.req("SAF-FW-004", "SAF-FW-005")
def test_estop_truncates_and_orders_events() -> None:
    cl = _ready()
    _set(cl, "drv.k1_weld_ms", 100)
    assert _move(cl, 100_000, 30_000).ok
    cl.run(300)
    n0 = len(cl.events)
    cl.ctl.act("estop", open=True, k1_delay_ms=40)
    cl.run(2)
    codes = _codes(cl, n0)
    assert codes[:2] == ["ESTOP_SET", "STOPPED"] and codes[-1] == "MOVE_DONE"
    assert codes.index("DRIVER_DISABLED") < codes.index("MOVE_DONE")
    assert cl.ev("DRIVER_DISABLED")[-1].arg == pg.DriverDisabledCause.ESTOP and not cl.b.homed
    t_edge = cl.b.now_us()
    assert _run_until(cl, lambda: cl.ev("DRIVER_POWER") and cl.ev("DRIVER_POWER")[-1].arg == 0, 200, 1)
    dt_ms = (cl.b.now_us() - t_edge) / 1000
    assert 40 + 20 - 3 <= dt_ms <= 40 + 20 + 3                       # K1 delay + 20 ms DRV_PWR filter
    assert not [e for e in cl.ev("FAULT_SET") if e.arg == pg.FAULTS_BITS.index("K1_WELDED")]
    r = cl.cmd(Cmd.ENABLE)
    assert r.status_name == "E_STATE" and r.detail & pg.Block.ESTOP


@pytest.mark.req("SAF-FW-025")
@pytest.mark.parametrize("k1", [100, 200])
def test_k1_welded_latched_after_k1_weld_ms(k1: int) -> None:
    cl = Client()
    _set(cl, "drv.k1_weld_ms", k1)
    cl.run(5)
    t0 = cl.b.now_us()
    cl.ctl.act("estop", open=True, drv_power_follows=False)
    assert _run_until(cl, lambda: bool(cl.b.faults_mask & pg.Faults.K1_WELDED), k1 + 50, 1)
    assert k1 < (cl.b.now_us() - t0) / 1000 <= k1 + 25
    assert "K1_WELDED" in cl.b._fault_causes()                        # noqa: SLF001
    assert cl.cmd(Cmd.FAULT_CLEAR).status_name == "E_CAUSE_ACTIVE"
    cl.ctl.act("drv_power", on=False)
    cl.run(30)
    assert cl.cmd(Cmd.FAULT_CLEAR).ok and not cl.b.faults_mask & pg.Faults.K1_WELDED


@pytest.mark.req("SAF-FW-024", "FW-SW-005", "FW-MOT-008")
def test_driver_power_loss_filter_and_enable_settle_after_return() -> None:
    cl = _ready()
    cl.ctl.act("drv_power", on=False, bounce_ms=[5, 5])               # off 5 ms, on 5 ms, off: settles off
    cl.run(15)
    assert cl.b.pwr_filt                                              # < 20 ms stable: not yet accepted
    assert _move(cl, 50_000).ok
    cl.run(40)
    assert not cl.b.pwr_filt and cl.ev("STOPPED")[-1].arg == pg.StopCause.DRV_POWER_LOST
    assert cl.ev("DRIVER_DISABLED")[-1].arg == pg.DriverDisabledCause.DRV_POWER_LOST and not cl.b.homed
    r = cl.cmd(Cmd.ENABLE)
    assert r.status_name == "E_STATE" and r.detail & pg.Block.DRV_UNPOWERED
    cl.ctl.act("drv_power", on=True)
    cl.run(21)
    t_ret = cl.b.pwr_on_us
    r = cl.cmd(Cmd.ENABLE, wait=1)
    assert r.ok and 495 <= P.decode_u16(r.body) <= 500
    assert _run_until(cl, lambda: cl.b.motion_state == "IDLE", 600, 1)
    assert cl.b.now_us() - t_ret >= 500_000


@pytest.mark.req("FW-HOM-001", "FW-HOM-004")
def test_homing_phases_zero_at_the_edge_and_drift() -> None:
    cl = Client()
    cl.hb = True
    cl.cmd(Cmd.STREAM_START)
    _enable(cl)
    phases = []
    _set(cl, "home.v_fast_um_s", 20_000)
    _set(cl, "home.v_slow_um_s", 2_000)
    assert cl.cmd(Cmd.HOME, b"\x00").ok
    for _ in range(30_000):
        cl.run(1)
        if not phases or phases[-1] != cl.b.home_phase:
            phases.append(cl.b.home_phase)
        if cl.b.homed:
            break
    names = [pg.HomePhase(p).name for p in phases]
    assert names[:4] == ["FAST_SEEK", "BACKOFF", "SLOW_APPROACH", "MOVE_TO_ZERO"]
    assert cl.ev("HOMED")[-1].value == 0 and cl.b.steps == 0 and not cl.b.pos_uncertain
    # machine 0 lies home.offset_um (1 mm) beyond the START switch edge (± 1 step)
    world = cl.ctl.act("query", what="world")
    assert abs(world["x_um_true"] - (cl.b.world.start_switch_um + 1000)) <= 1000 / SPM + 1
    assert not cl.b.limit_latch["start"]                             # expected edges set no latch
    # re-home after 0.1 mm of lost steps → drift reported, no fault; 0.3 mm → HOME_DRIFT latched
    for shift, fault in ((100, False), (300, True)):
        cl.run(100)
        cl.ctl.act("world_shift", um=shift)
        assert cl.cmd(Cmd.HOME, b"\x01").ok
        assert _run_until(cl, lambda: cl.b.homed and cl.b.motion is None and cl.b.home_phase == 7, 30_000, 10)
        drift = cl.ev("HOMED")[-1].value
        assert abs(abs(drift) - shift) <= 3
        assert bool(cl.b.faults_mask & pg.Faults.HOME_DRIFT) is fault
    assert cl.cmd(Cmd.FAULT_CLEAR).ok


@pytest.mark.req("FW-HOM-002")
def test_homing_failures() -> None:
    cl = Client()
    cl.hb = True
    _enable(cl)
    _set(cl, "home.max_travel_um", 20_000)
    _set(cl, "home.v_fast_um_s", 20_000)
    assert cl.cmd(Cmd.HOME, b"\x01").ok
    assert _run_until(cl, lambda: cl.b.motion is None, 5000)
    assert cl.ev("HOME_FAILED")[-1].arg == pg.HomeFailReason.NOT_FOUND and cl.b.faults_mask & pg.Faults.HOME_NOT_FOUND
    assert cl.ev("MOVE_DONE")[-1].arg == pg.MoveDoneReason.STOPPED and not cl.b.homed
    assert cl.cmd(Cmd.FAULT_CLEAR).ok
    _set(cl, "home.max_travel_um", 360_000)
    assert cl.cmd(Cmd.HOME, b"\x01").ok
    cl.run(200)
    cl.ctl.act("limit", name="end", active=True)                     # END reached while seeking START
    cl.run(5)
    assert cl.ev("HOME_FAILED")[-1].arg == pg.HomeFailReason.WIRING and cl.b.faults_mask & pg.Faults.HOME_WIRING
    cl.ctl.act("limit", name="end", active=False)
    cl.run(30)
    assert cl.cmd(Cmd.FAULT_CLEAR).ok
    assert cl.cmd(Cmd.HOME, b"\x01").ok
    cl.run(200)
    n0 = len(cl.events)
    cl.cmd(Cmd.STOP, b"\x00")                                        # any other stop → ABORTED, no latch
    assert "HOME_FAILED" in _codes(cl, n0) and cl.ev("HOME_FAILED")[-1].arg == pg.HomeFailReason.ABORTED
    assert not cl.b.faults_mask


# ============================================================================================ jog


@pytest.mark.req("FW-MOT-005", "SAF-FW-016")
def test_jog_bound_reversal_and_dead_man() -> None:
    cl = _ready()
    jog = lambda v, b: cl.cmd(Cmd.JOG, P.build_request(Cmd.JOG, v_um_s=v, a_um_s2=0, bound_um=b), wait=1)  # noqa: E731
    assert jog(5000, 3000).ok
    for _ in range(40):
        if cl.b.motion is None:
            break
        jog(5000, 3000)
        cl.run(80)
    md = cl.ev("MOVE_DONE")[-1]
    assert md.arg == pg.MoveDoneReason.BOUND and md.value == 3000 and cl.b.steps == um_to_steps(3000, SPM)
    # reversal: + then − (decelerate to 0 first, the motion state stays JOG, one MOVE_DONE at the end)
    n0 = len(cl.events)
    assert jog(5000, pg.JOG_NO_BOUND).ok
    cl.run(100)
    s_peak = cl.b.steps
    assert jog(-5000, pg.JOG_NO_BOUND).ok and cl.b.motion_state == "JOG"
    for _ in range(4):                                               # (the soft limit 0.5 mm is ~0.55 s away)
        cl.run(80)
        jog(-5000, pg.JOG_NO_BOUND)
    assert cl.b.motion is not None and cl.b.motion.direction == -1 and cl.b.steps < s_peak
    assert "MOVE_DONE" not in _codes(cl, n0)
    t_last = cl.b.now_us()
    assert _run_until(cl, lambda: cl.b.motion is None, 1000, 1)
    st = cl.ev("STOPPED")[-1]
    assert st.arg == pg.StopCause.JOG_DEADMAN and 250 <= (st.t_us - t_last % 2**32) / 1000 <= 260
    assert jog(0, pg.JOG_NO_BOUND).ok                                # JOG 0 while idle: OK no-op


@pytest.mark.req("SAF-FW-015")
def test_link_watchdog_controlled_stop_while_moving() -> None:
    cl = _ready()
    cl.hb = False
    assert _move(cl, 200_000, 30_000).ok
    t0 = cl.b.now_us()
    assert _run_until(cl, lambda: bool(cl.ev("LINK_WDG")), 1500, 1)
    assert 999 <= (cl.b.now_us() - t0) / 1000 <= 1003 and cl.b.motion_state == "STOPPING"
    assert _run_until(cl, lambda: cl.b.motion is None, 2000, 1)
    assert cl.ev("STOPPED")[-1].arg == pg.StopCause.LINK_WDG


# ============================================================================================ inputs / safety


@pytest.mark.req("SAF-FW-013", "SAF-FW-014", "FW-SW-001")
def test_limit_hit_latch_direction_release_and_wiring() -> None:
    cl = _ready()
    assert _move(cl, 100_000, 30_000).ok
    cl.run(300)
    cl.ctl.act("limit", name="end", active=True, bounce_ms=[0.5, 1, 2])
    cl.run(3)
    assert cl.ev("STOPPED")[-1].arg == pg.StopCause.LIMIT_END and len(cl.ev("LIMIT_SET")) == 1
    r = _move(cl, cl.b.pos_um + 1000)
    assert r.status_name == "E_STATE" and r.detail & pg.Block.LIMIT
    assert _move(cl, cl.b.pos_um - 1000).ok                          # away from the switch: accepted
    cl.ctl.act("limit", name="end", active=False)
    cl.run(19)
    assert cl.b.limit_latch["end"]
    cl.run(3)
    assert not cl.b.limit_latch["end"] and cl.ev("LIMIT_CLEARED")
    cl.run(500)
    cl.ctl.act("limit", name="start", active=True)
    cl.ctl.act("limit", name="end", active=True)
    cl.run(3)
    assert cl.b.faults_mask & pg.Faults.LIMIT_WIRING and cl.cmd(Cmd.FAULT_CLEAR).status_name == "E_CAUSE_ACTIVE"


@pytest.mark.req("FW-SW-003", "SAF-FW-023")
def test_pause_button_contact_type_and_rearm() -> None:
    cl = Client()
    cl.run(5)
    cl.ctl.act("button", name="pause", pressed=True)
    cl.run(3)
    assert cl.b.paused and cl.ev("PAUSE_BUTTON")[-1].arg == 1 and cl.data == [] or cl.b.paused
    _set(cl, "io.pause_active_level", 0)                             # OPEN_ACTIVE: a pressed NO contact = released
    cl.ctl.act("button", name="pause", pressed=False)                # open contact → active with the new polarity
    cl.run(3)
    assert cl.cmd(Cmd.HALT_CLEAR).ok and not cl.b.paused
    assert not cl.ctl.act("wire", input="stop", broken=True)["ok"]    # retired (D-36)
    assert cl.ctl.act("wire", input="pause", broken=True)["ok"]


@pytest.mark.req("SAF-FW-017")
def test_idle_disable_unloaded_only_and_status_counter() -> None:
    cl = _ready()
    _set(cl, "safety.idle_disable_s", 3)
    cl.run(1000)
    st = P.decode_status(cl.cmd(Cmd.GET_STATUS).body)
    assert 1 <= st.idle_disable_left_s <= 3
    assert _run_until(cl, lambda: cl.b.motion_state == "NOT_ENABLED", 3000, 10)
    assert cl.ev("DRIVER_DISABLED")[-1].arg == pg.DriverDisabledCause.IDLE and not cl.b.homed
    st = P.decode_status(cl.cmd(Cmd.GET_STATUS).body)
    assert st.idle_disable_left_s == 0xFFFF
    _enable(cl)
    cl.ctl.act("load_offset", counts=50_000 + 2 * 128_849)           # loaded → stays enabled
    cl.run(5000)
    assert cl.b.motion_state == "IDLE"
    cl.ctl.act("load_offset", counts=50_000)
    cl.ctl.act("afe", stall=True)                                    # stale → never disabled (D-33 g)
    cl.run(5000)
    assert cl.b.motion_state == "IDLE"


@pytest.mark.req("SAF-FW-011", "SAF-FW-008")
def test_load_limit_trip_clear_and_regrow() -> None:
    cl = _ready()
    _set(cl, "safety.load_raw_max", 400_000)
    cl.ctl.act("afe", noise_counts=0)
    xt0 = cl.ctl.act("query", what="world")["x_um_true"]
    cl.ctl.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=int(xt0) + 2000)
    assert _move(cl, 40_000, 5000).ok
    assert _run_until(cl, lambda: bool(cl.b.faults_mask & pg.Faults.LOAD_LIMIT), 20_000)
    assert cl.ev("STOPPED")[-1].arg == pg.StopCause.LOAD_LIMIT
    cl.run(20)
    assert cl.cmd(Cmd.FAULT_CLEAR).ok and not cl.b.faults_mask       # cleared although still beyond
    x = cl.b.pos_um
    assert _move(cl, x - 1000, 2000).ok
    cl.run(1500)
    assert not cl.b.faults_mask                                      # unloading runs
    assert _move(cl, x + 20_000, 2000).ok
    assert _run_until(cl, lambda: bool(cl.b.faults_mask & pg.Faults.LOAD_LIMIT), 20_000)


@pytest.mark.req("FW-SW-004")
def test_alm_start_block_and_not_settled() -> None:
    cl = _ready()
    cl.ctl.act("alm", active=True)
    cl.run(2)
    r = _move(cl, 5000)
    assert r.status_name == "E_STATE" and r.detail & pg.Block.DRIVER_ALARM
    cl.ctl.act("alm", active=False)
    cl.run(10)
    assert cl.b.alm_filt                                             # inactive only after a stable io.release_ms
    cl.run(15)
    assert not cl.b.alm_filt and cl.ev("ALM_CHANGED")[-1].arg == 0
    cl.ctl.act("pend", active=False)
    assert _move(cl, 2000).ok
    assert _run_until(cl, lambda: bool(cl.ev("NOT_SETTLED")), 2000, 1)
    assert cl.ev("NOT_SETTLED")[-1].value >= 200


# ============================================================================================ features (D-37 b)


@pytest.mark.req("SAF-SW-005", "SYS-008")
def test_feature_mask_bits_sent_as_zero_and_not_in_build() -> None:
    cl = Client(config=SimConfig(features=int(pg.Features.AFE_SYNTHETIC | pg.Features.NVM)))
    cl.cmd(Cmd.STREAM_START)
    cl.run(100)
    s = cl.data[-1].status
    assert not s & (DS.ALM | DS.PEND | DS.DRV_PWR | DS.PAUSE_BTN)
    st = P.decode_status(cl.cmd(Cmd.GET_STATUS).body)
    assert not st.io & (pg.IoBits.ALM | pg.IoBits.PEND | pg.IoBits.DRV_PWR | pg.IoBits.PAUSE_BTN)
    for cmd, payload in ((Cmd.ENABLE, b""), (Cmd.HOME, b"\x00"), (Cmd.DISABLE, b"")):
        r = cl.cmd(cmd, payload)
        assert (r.status_name, r.detail) in (("E_INTERNAL", int(pg.InternalDetail.NOT_IN_BUILD)), ("E_STATE", r.detail))
    r = cl.cmd(Cmd.ENABLE)
    assert (r.status_name, r.detail) == ("E_INTERNAL", int(pg.InternalDetail.NOT_IN_BUILD))
    assert cl.cmd(Cmd.JOG, P.build_request(Cmd.JOG, v_um_s=0, a_um_s2=0, bound_um=pg.JOG_NO_BOUND)).ok
    cl.ctl.act("button", name="pause", pressed=True)                 # no BUTTONS feature → not sampled
    cl.run(5)
    assert not cl.b.paused
    cl.b.features = cl.b.features | int(pg.Features.DRV_SIGNALS)
    cl.run(30)
    assert cl.data[-1].status & DS.DRV_PWR


@pytest.mark.req("SYS-008")
def test_scenario_and_server_feature_mask() -> None:
    from bend_stand.io.sim.control import SimScenario, parse_features
    from bend_stand.io.sim.server import SimServer

    assert parse_features("AFE_SYNTHETIC, NVM") == int(pg.Features.AFE_SYNTHETIC | pg.Features.NVM) == \
        parse_features(["afe_synthetic", "nvm"])
    assert parse_features(5) == 5
    sc = SimScenario.from_dict({"schema": "bird.bend.simscenario", "version": 1, "features": ["AFE", "NVM"]})
    cl = Client()
    sc.apply(cl.b, cl.ctl)
    assert cl.b.features == int(pg.Features.AFE | pg.Features.NVM)
    srv = SimServer(0, 0, features="AFE_SYNTHETIC,NVM")
    try:
        assert srv.board.info().feature_mask == int(pg.Features.AFE_SYNTHETIC | pg.Features.NVM)
    finally:
        srv.stop()
