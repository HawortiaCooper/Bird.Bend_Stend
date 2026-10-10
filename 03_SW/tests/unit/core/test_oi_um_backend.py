"""Backend items from the user-manual work (Implementer D, OI-UM-01 / OI-UM-06, GRQ-B-31 a / b): clear-hint wording
with the E-stop release time as a value and the Clear stop window's button names, no negative zero in guard texts,
planned force clamped at 0 beyond the specimen contact, ``record_start`` refused without a link, ``reports.root()``.

Verifies: SAF-SW-005, SW-SEQ-007, SW-SCH-001, SW-ACQ-002
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest
from bbs_support import lockstep_backend

from bend_stand.calc.path import advance, contact_x
from bend_stand.core import gates as G
from bend_stand.core.model import GateId, LinkState
from bend_stand.core.sequencer import executor as X
from bend_stand.core.sequencer.model import Sequence, Step, StepKind
from bend_stand.core.sequencer.plan import PlanContext, expand, planned_path


# ================================================================================================ OI-UM-01
@pytest.mark.req("SAF-SW-005")
def test_clear_hints_use_button_names_and_values() -> None:
    assert G.ESTOP_RELEASE_MS_DEFAULT == 100
    h = G.estop_hint(150)
    assert "≥ 150 ms" in h and "Clear E-STOP" in h and "io." not in h
    assert "≥ 100 ms" in G.CLEAR_HINTS["ESTOP"] and G.clear_procedure("ESTOP", 250).count("≥ 250 ms") == 1
    for code, text in G.CLEAR_HINTS.items():
        assert "Fault clear" not in text and "Clear E-stop" not in text and "estop_release_ms" not in text, code
    assert G.CLEAR_HINTS["FAULT"].startswith("Clear faults") and "Clear faults" in G.CLEAR_HINTS["LOAD_LIMIT"]
    assert G.clear_procedure("HALT") == G.CLEAR_HINTS["HALT"] and G.clear_procedure("nope") is None
    s = G.GateSnapshot(link=LinkState.CONNECTED, estop_release_ms=300, flags=int(G.DF.ESTOP),
                       features=frozenset({"MOTION"}))
    items = [i for i in G.all_gates(s)[GateId.ENABLE].items if i.code == "ESTOP"]
    assert items and "≥ 300 ms" in items[0].clear_hint


@pytest.mark.req("SAF-SW-005")
def test_estop_indicator_and_gate_show_the_board_release_time() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        h.result(be.config.write_and_verify_async({"io.estop_release_ms": 150}))
        be.sim.act("estop", open=True)
        h.advance(200)
        ind = be.status().indicators["estop"]
        assert ind.state == "ON" and "≥ 150 ms" in ind.clear_hint and "Clear E-STOP" in ind.clear_hint
        g = be.status().gates[GateId.ENABLE]
        assert any(i.code == "ESTOP" and "≥ 150 ms" in (i.clear_hint or "") for i in g.items)
    finally:
        be.shutdown()


@pytest.mark.req("SW-SEQ-007")
def test_guard_texts_have_no_negative_zero() -> None:
    assert X._fn(-0.04) == "0.0" and X._fn(-0.0) == "0.0" and X._fn(0.04) == "0.0"  # noqa: SLF001
    assert X._fn(-1.26) == "-1.3" and X._fn(12.34) == "12.3"  # noqa: SLF001
    g = X.HoldBreakGuard(None)
    for i in range(40):
        g.update(i * 12_500, 150.0)
    why = g.update(40 * 12_500, -0.03)
    assert why is not None and "-0.0" not in why and "150.0 N" in why


# ================================================================================================ OI-UM-06
@pytest.mark.req("SW-SCH-001")
def test_advance_clamps_the_force_on_the_unloaded_side_of_the_contact() -> None:
    assert contact_x(4.0, 100.0, 50.0, 1) == pytest.approx(2.0)
    assert contact_x(4.0, 100.0, 50.0, -1) == pytest.approx(6.0)
    assert math.isnan(contact_x(4.0, float("nan"), 50.0, 1)) and math.isnan(contact_x(4.0, 1.0, 0.0, 1))
    assert advance("travel", 4.0, 100.0, 0.0, 50.0, 1, x_contact=2.0, side=1) == (0.0, 0.0, "x")
    assert advance("travel", 4.0, 100.0, 3.0, 50.0, 1, x_contact=2.0, side=1) == (3.0, 50.0, "x")
    x, f, k = advance("travel", 0.0, 0.0, 0.0, 50.0, 1, x_contact=2.0, side=-1)   # compression side
    assert (x, f, k) == (0.0, -100.0, "x")
    assert advance("travel", 0.0, -100.0, 5.0, 50.0, 1, x_contact=2.0, side=-1)[1] == 0.0
    assert advance("load", 0.0, 0.0, 100.0, 50.0, 1, x_contact=2.0, side=1) == (pytest.approx(4.0), 100.0, "F")
    assert advance("travel", 4.0, 100.0, 0.0, 50.0, 1)[1] == pytest.approx(-100.0)   # no contact model: as before
    assert math.isnan(advance("travel", 4.0, float("nan"), 0.0, 50.0, 1, x_contact=float("nan"), side=1)[1])


@pytest.mark.req("SW-SCH-001", "SW-SEQ-002")
def test_plan_does_not_extrapolate_below_contact() -> None:
    """OI-UM-06: travel to 4 mm loads the specimen (contact at the start, 2 mm), the return to 0 plans F = 0, not
    −100 N; a later load step is placed from the contact point."""
    seq = Sequence(steps=[Step("a", StepKind.TRAVEL, 4.0), Step("b", StepKind.TRAVEL, 0.0),
                          Step("l", StepKind.LOAD, 100.0, tol_n=2.0), Step("c", StepKind.TRAVEL, 1.0)],
                   k_est_n_mm=50.0)
    plan = expand(seq, PlanContext(x0_mm=2.0, f0_n=0.0))
    assert [round(p.f_target_n, 6) for p in plan.steps] == [100.0, 0.0, 100.0, 0.0]
    assert plan.steps[2].x_target_mm == pytest.approx(4.0)
    assert all(pt.f_n >= 0.0 for pt in planned_path(plan))
    push = Sequence(steps=[Step("l", StepKind.LOAD, -50.0, tol_n=2.0), Step("t", StepKind.TRAVEL, 5.0)],
                    k_est_n_mm=50.0)
    pp = expand(push, PlanContext(x0_mm=2.0, f0_n=0.0))                          # compression specimen
    assert pp.steps[0].x_target_mm == pytest.approx(1.0) and pp.steps[1].f_target_n == 0.0
    loaded = expand(Sequence(steps=[Step("t", StepKind.TRAVEL, 0.0)], k_est_n_mm=50.0),
                    PlanContext(x0_mm=3.0, f0_n=50.0))                            # start loaded: contact at 2 mm
    assert loaded.steps[0].f_target_n == 0.0
    tare = expand(Sequence(steps=[Step("t", StepKind.TRAVEL, 3.0), Step("z", StepKind.TARE),
                                  Step("u", StepKind.TRAVEL, 4.0)], k_est_n_mm=50.0), PlanContext(x0_mm=2.0))
    assert [p.f_target_n for p in tare.steps] == [50.0, 0.0, 50.0]               # tare restarts the reference


# ================================================================================================ GRQ-B-31
@pytest.mark.req("SW-ACQ-002")
def test_record_start_refused_without_link_and_stop_stays_possible(tmp_path) -> None:
    be = lockstep_backend(recordings_root=str(tmp_path / "rec"))
    try:
        h = be.test_hooks
        assert be.reports.root() == str(tmp_path / "rec")
        assert be.record_start().ok
        folder = be.recorder.folder
        be.sim.act("inject", fault="hang", duration_ms=2500)                     # board silent → LINK LOST
        assert h.run_until(lambda: be.status().link.state.value not in ("CONNECTED", "DEGRADED"), 3000)
        assert be.record_stop().ok                                                # stopping stays possible
        h.advance(100)
        n = len(list(Path(tmp_path / "rec").iterdir()))
        g = be.record_start()
        assert not g.ok and "LINK_DOWN" in g.codes()
        assert len(list(Path(tmp_path / "rec").iterdir())) == n and Path(folder).is_dir()   # no empty folder
        assert not be.status().gates[GateId.RECORD_START].ok
    finally:
        be.shutdown()


@pytest.mark.req("SW-ACQ-002")
def test_reports_root_follows_the_backend_lookup(tmp_path) -> None:
    be = lockstep_backend()
    try:
        assert be.reports.root() == be._recordings_root()  # noqa: SLF001
        s = be.session.get()
        from dataclasses import replace
        assert not be.session.set(replace(s, recordings_root=str(tmp_path / "sess")))
        assert be.reports.root() == str(tmp_path / "sess")
        assert be.reports.list_recordings() == []
    finally:
        be.shutdown()
