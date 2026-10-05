"""Simulator specimen extras for the M4 guards (B6-17): grip slip, residual force after a break, through the world
control vocabulary.

Verifies: SYS-008
"""
from __future__ import annotations

import pytest

from bend_stand.io.sim.models import Specimen


@pytest.mark.req("SYS-008")
def test_slip_and_break_residual_model() -> None:
    s = Specimen("spring", 50.0, 1000, slip_at_n=100.0, slip_mm=0.4)
    assert s.force_n(2000) == pytest.approx(50.0) and not s.slipped
    assert s.force_n(3000) == pytest.approx(80.0) and s.slipped and s.x_contact_um == 1400   # slips once at 100 N
    assert s.force_n(4000) == pytest.approx(130.0)
    b = Specimen("spring", 50.0, 0, f_break_n=150.0, break_residual_pct=10.0)
    assert b.force_n(2000) == pytest.approx(100.0)
    assert b.force_n(3100) == pytest.approx(15.5) and b.broken                                 # 155 N → 10 %
    assert b.force_n(2000) == pytest.approx(10.0)
    z = Specimen("spring", 50.0, 0, f_break_n=150.0)
    assert z.force_n(3100) == 0.0 and z.force_n(1000) == 0.0
    assert Specimen().force_n(5000) == 0.0
    assert Specimen("spring", 50.0, 1000).force_n(500) == 0.0


@pytest.mark.req("SYS-008")
def test_world_action_accepts_the_extras() -> None:
    from bbs_support import lockstep_backend

    be = lockstep_backend()
    try:
        r = be.sim.act("specimen", kind="spring", k_n_per_mm=10.0, x_contact_um=0, slip_at_n=5.0, slip_mm=0.1,
                       break_residual_pct=5.0, f_break_n=50.0)
        assert r["ok"]
        sp = be.sim_endpoint.board.world.specimen if hasattr(be.sim_endpoint, "board") else None
        if sp is not None:
            assert sp.slip_at_n == 5.0 and sp.break_residual_pct == 5.0
    finally:
        be.shutdown()


@pytest.mark.req("SYS-008")
def test_twin_m4_specimen_vocabulary_side_k3_break_travel() -> None:
    """SWC-M4-01 (ICD v0.7.4 vocabulary): side pull / push / both, cubic term, break by travel with residual."""
    push = Specimen("spring", 10.0, 0, side="push")
    assert push.force_n(1000) == 0.0 and push.force_n(-2000) == pytest.approx(-20.0)
    both = Specimen("spring", 10.0, 0, side="both")
    assert both.force_n(1000) == pytest.approx(10.0) and both.force_n(-1000) == pytest.approx(-10.0)
    k3 = Specimen("spring", 10.0, 0, k3_n_per_mm3=1.0)
    assert k3.force_n(2000) == pytest.approx(28.0)
    soft = Specimen("spring", 10.0, 0, k3_n_per_mm3=-10.0)
    assert soft.force_n(3000) == 0.0                                              # never below 0
    bt = Specimen("spring", 10.0, 0, break_travel_um=5000, break_residual_pct=20.0)
    assert bt.force_n(4000) == pytest.approx(40.0) and not bt.broken
    assert bt.force_n(5000) == pytest.approx(10.0) and bt.broken
    sl = Specimen("spring", 10.0, 0, side="push", slip_at_n=20.0, slip_mm=0.5)
    assert sl.force_n(-2000) == pytest.approx(-15.0) and sl.x_contact_um == -500   # slips in the deflection direction


@pytest.mark.req("SYS-008", "SW-SEQ-004")
def test_d47a_idle_link_silence_clears_valid_once_without_stop() -> None:
    """D-47 a (FW parity): link silence ≥ safety.link_timeout_ms while idle clears VALID with VALID_CLEARED
    (LINK_WDG) once (1 → 0 only); no STOPPED, no LINK_WDG event / status bit while idle."""
    from bbs_support import lockstep_backend

    from bend_stand.core import protocol_gen as pg

    be = lockstep_backend()
    try:
        h = be.test_hooks
        assert be.motion.set_valid(True).ok
        assert h.run_until(lambda: be.device.last_flags & 1, 1000)
        n0 = len(be.events.history("fw.event"))
        be.sim.act("inject", fault="link_silence", duration_ms=1500)
        h.advance(2500)
        evs = [e.payload for e in be.events.history("fw.event")[n0:]]
        vc = [e for e in evs if e.code == pg.Event.VALID_CLEARED]
        assert len(vc) == 1 and vc[0].arg == int(pg.StopCause.LINK_WDG)
        assert not [e for e in evs if e.code in (pg.Event.LINK_WDG, pg.Event.STOPPED)]
        assert not be.device.last_flags & 1 and not be.device.last_status & int(pg.DataStatus.LINK_WDG)
    finally:
        be.shutdown()
