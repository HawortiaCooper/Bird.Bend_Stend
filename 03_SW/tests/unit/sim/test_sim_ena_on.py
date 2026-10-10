"""Check-vector state_schema 4 (ICD v0.7.5 (h), D-50 c): the simulator's check-state view exports ``ena_on`` from
its driver-enable model (true at boot / after ENABLE, false after DISABLE and E-stop) and a vector state applies it.

Verifies: SYS-008, IF-010
"""
from __future__ import annotations

import pytest

from bend_stand.core import protocol_gen as pg
from bend_stand.io.sim.check import STATE_SCHEMA, SimCheckState

from .test_sim_board import Client, _enable

Cmd = pg.Cmd


@pytest.mark.req("SYS-008", "IF-010")
def test_check_state_exports_and_applies_ena_on() -> None:
    assert STATE_SCHEMA == 4 and SimCheckState().ena_on is True
    cl = Client()
    assert cl.b.check_state().ena_on is True                                     # boot: ENA holding (D-13)
    _enable(cl)
    assert cl.b.check_state().ena_on is True
    assert cl.cmd(Cmd.DISABLE).ok
    cl.run(10)
    assert cl.b.check_state().ena_on is False
    _enable(cl)
    cl.ctl.act("estop", open=True)
    cl.run(50)
    assert cl.b.check_state().ena_on is False
    st = SimCheckState.from_vector({}, {"ena_on": True}, STATE_SCHEMA)
    cl.b.load_check_state(st)
    assert cl.b.check_state().ena_on is True and not cl.b.ena_disabled
    cl.b.load_check_state(SimCheckState.from_vector({}, {"ena_on": False}, STATE_SCHEMA))
    assert cl.b.ena_disabled
