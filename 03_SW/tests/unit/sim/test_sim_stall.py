"""Simulator stimulus ``inject step_stall`` (SW_design §12.4): the step-pulse output of a running move is frozen — no
pulse, step counter / world unchanged, MOVING stays, no EVENT and no status bit — until ``duration_ms`` has passed
(0 = until the move ends) or a stop ends the move. It makes the PC step-timeout guard (SW-SEQ-007 TIMEOUT) testable
without any other stop cause.

Verifies: SYS-008, SW-SEQ-007
"""
from __future__ import annotations

import pytest
from .test_sim_board import DF, DS, Client, _enable

from bend_stand.core import protocol_gen as pg
from bend_stand.io import protocol as P

Cmd = pg.Cmd


def _homed() -> Client:
    cl = Client()
    cl.hb = True
    cl.cmd(Cmd.STREAM_START)
    _enable(cl)
    cl.cmd(Cmd.SET_PARAM, P.build_set_param("home.v_fast_um_s", 20000))
    assert cl.cmd(Cmd.HOME, b"\x00").ok
    for _ in range(40):
        cl.run(1000)
        if cl.ev("HOMED"):
            break
    assert cl.b.homed and cl.b.motion is None
    return cl


def _move(cl: Client, target_um: int, v_um_s: int = 5000) -> None:
    assert cl.cmd(Cmd.MOVE_ABS, P.build_request(Cmd.MOVE_ABS, target_um=target_um, v_um_s=v_um_s, a_um_s2=0)).ok


def _world(cl: Client) -> float:
    return cl.ctl.act("query", what="world")["x_um_true"]


@pytest.mark.req("SYS-008", "SW-SEQ-007")
def test_timed_stall_freezes_the_move_then_it_completes() -> None:
    cl = _homed()
    _move(cl, 10_000)
    cl.run(300)
    n_ev = len(cl.events)
    r = cl.ctl.act("inject", fault="step_stall", duration_ms=1000)
    assert r == {"ok": True, "armed": False}
    assert cl.ctl.act("inject", fault="step_stall", duration_ms=-1)["ok"] is False     # negative: refused
    s0, x0, p0 = cl.b.steps, _world(cl), cl.b.pulses
    q = cl.ctl.act("query", what="pulses")
    assert q["step_stalled"] is True and q["step_stall_armed"] is False and q["pul_count"] == p0
    cl.run(900)
    assert (cl.b.steps, cl.b.pulses) == (s0, p0) and _world(cl) == x0                 # frozen: no pulse, no travel
    tail = cl.data[-60:]
    assert all(d.flags & DF.MOVING for d in tail)                                     # still MOVING …
    assert len({d.setpoint_um for d in tail}) == 1                                    # … at a frozen position
    assert not any(d.status & (DS.LIMIT_START | DS.LIMIT_END | DS.POS_UNCERTAIN) for d in tail)
    assert cl.events[n_ev:] == [] and not cl.b.faults_mask                            # no EVENT, no fault
    cl.run(200)
    assert cl.b.steps > s0                                                            # resumed after 1000 ms
    cl.run(2500)
    md = cl.ev("MOVE_DONE")[-1]
    assert md.arg == pg.MoveDoneReason.TARGET and md.value == 10_000 and cl.b.motion is None
    assert not cl.ev("STOPPED")


@pytest.mark.req("SYS-008", "SW-SEQ-007")
def test_open_stall_lasts_until_an_immediate_stop() -> None:
    cl = _homed()
    _move(cl, 20_000)
    cl.run(200)
    assert cl.ctl.act("inject", fault="step_stall")["armed"] is False                 # duration 0: until the end
    s0 = cl.b.steps
    cl.run(5000)
    assert cl.b.steps == s0 and cl.b.motion is not None and not cl.ev("MOVE_DONE")[1:]
    cl.cmd(Cmd.STOP, b"\x00")
    cl.run(20)
    md = cl.ev("MOVE_DONE")[-1]
    assert cl.ev("STOPPED")[-1].arg == pg.StopCause.PC_STOP and md.arg == pg.MoveDoneReason.STOPPED
    assert cl.b.steps == s0 == md.value2 and not cl.data[-1].status & DS.POS_UNCERTAIN   # no pulse in flight
    assert cl.b.step_stall_until_us is None
    _move(cl, 1000)                                                                   # the next move is not stalled
    cl.run(3000)
    assert cl.ev("MOVE_DONE")[-1].arg == pg.MoveDoneReason.TARGET and cl.b.pos_um == 1000


@pytest.mark.req("SYS-008", "SW-SEQ-007")
def test_controlled_stop_lifts_the_stall_and_ramps_down() -> None:
    cl = _homed()
    _move(cl, 30_000, v_um_s=10_000)
    cl.run(400)
    cl.ctl.act("inject", fault="step_stall")
    s0 = cl.b.steps
    cl.run(500)
    assert cl.b.steps == s0
    cl.cmd(Cmd.STOP, b"\x01")                                                         # controlled
    cl.run(2000)
    md = cl.ev("MOVE_DONE")[-1]
    assert md.arg == pg.MoveDoneReason.STOPPED and cl.b.motion is None
    assert cl.b.steps >= s0 and md.value2 == cl.b.steps                               # stop ramp from the frozen point


@pytest.mark.req("SYS-008", "SW-SEQ-007")
def test_stall_armed_without_a_move_applies_to_the_next_move_and_reset_clears_it() -> None:
    cl = _homed()
    assert cl.ctl.act("inject", fault="step_stall", duration_ms=400) == {"ok": True, "armed": True}
    _move(cl, 5000)
    s0 = cl.b.steps
    cl.run(300)
    assert cl.b.steps == s0 and cl.b.motion is not None                               # stalled from the start
    cl.run(3000)
    assert cl.ev("MOVE_DONE")[-1].arg == pg.MoveDoneReason.TARGET and cl.b.pos_um == 5000
    cl.ctl.act("inject", fault="step_stall")
    assert cl.b.step_stall_armed_ms == 0
    cl.ctl.act("reset", cause="pin")
    cl.run(50)
    assert cl.b.step_stall_armed_ms is None and cl.b.step_stall_until_us is None


@pytest.mark.req("SYS-008", "SW-SEQ-007")
def test_second_inject_sets_a_new_end_from_now() -> None:
    cl = _homed()
    _move(cl, 10_000)
    cl.run(300)
    cl.ctl.act("inject", fault="step_stall", duration_ms=300)
    s0 = cl.b.steps
    cl.run(200)
    cl.ctl.act("inject", fault="step_stall", duration_ms=300)                        # 200 ms in: ends 300 ms later
    cl.run(250)
    assert cl.b.steps == s0                                                           # still frozen at 450 ms
    cl.run(100)
    assert cl.b.steps > s0
    assert cl.ctl.act("query", what="pulses")["step_stalled"] is False
    cl.run(3000)
    assert cl.ev("MOVE_DONE")[-1].arg == pg.MoveDoneReason.TARGET and cl.b.pos_um == 10_000
