"""SIM-vs-twin differential of the **move stall** stimulus (ICD v0.7.5 vocabulary ``inject fault="step_stall"``,
tools/README "Move stall"; the stimulus behind the SW step-timeout guard SW-SEQ-007 TIMEOUT).

Same harness and comparison rule as ``test_sim_vs_twin_motion.py`` (lock-step, one PC-side ``Link``, the same
script at the same virtual instants). Sections:

- ``stall``: a 500 ms stall that ends by itself during a MOVE_ABS: MOVING kept, position frozen during the stall
  (exact on each board), the move then completes at its target (exact) about 500 ms later than the same move
  without a stall (measured on each board; compared as a boolean within ±(one 10 ms poll + one step period));
- ``stall2``: a stall until the move ends (``duration_ms`` absent) + STOP mode 0 inside it: the stop position equals
  the frozen position on each board (exact boolean), STOPPED (PC_STOP) + MOVE_DONE STOPPED (codes exact,
  positions within the time-triggered-stop tolerance: the stall starts at a time);
- ``stall3``: injected while idle = armed for the next move: no progress during the first 300 ms of the next move
  (exact), then the move completes at its target (exact).

Controlled stops inside a stall are model-dependent (tools/README) and not compared.

Verifies: SYS-008, IF-010 (behavioural equality of the shared vocabulary), SW-SEQ-007 (stimulus)
"""
from __future__ import annotations

import pytest

from test_sim_vs_twin_motion import Script, enable_home, pos_tol, world_setup

pytestmark = [pytest.mark.twin, pytest.mark.needs_b("bend_stand.io.sim.board", "SimBoard")]

V = 10_000                                   # µm/s
MOVE_A = {"target_um": 20_000, "v_um_s": V, "a_um_s2": 0}
HOME_BACK = {"target_um": 2_000, "v_um_s": 20_000, "a_um_s2": 0}


class Unsupported(Exception):
    pass


def _stall(s: Script, **kw) -> None:  # noqa: ANN003
    r = s.b.act("inject", fault="step_stall", **kw)
    if not r.get("ok"):
        raise Unsupported(f"{s.b.kind}: inject step_stall refused: {r}")


def _duration_ms(s: Script, fields: dict, stall_ms: float | None = None, at_ms: float = 300.0) -> float:
    """MOVE_ABS → IDLE time (10 ms status polls); optionally a stall of ``stall_ms`` ``at_ms`` into the move."""
    t0 = s.b.now_ms
    s.L.cmd("MOVE_ABS", fields)
    if stall_ms is not None:
        s.run(at_ms)
        s.at_ms_boundary()
        _stall(s, duration_ms=stall_ms)
    while s.L.status()["motion_state"] not in ("IDLE", "NOT_ENABLED"):
        s.run(10)
        assert s.b.now_ms - t0 < 60_000, f"{s.b.kind}: move did not end"
    return s.b.now_ms - t0


def sc_stall(s: Script) -> None:
    tol = pos_tol(V)
    s.L.cmd("MOVE_ABS", HOME_BACK)
    s.wait_state()
    ref = _duration_ms(s, MOVE_A)                                  # reference without a stall
    s.L.cmd("MOVE_ABS", HOME_BACK)
    s.wait_state()
    s.new_events()
    s.resp("stall.move", "MOVE_ABS", MOVE_A)
    s.run(300)
    s.at_ms_boundary()
    _stall(s, duration_ms=500)
    s.run(100)
    st1 = s.snap("stall.frozen", tol=tol, events=False)
    s.run(300)
    st2 = s.L.status()
    s.rec("stall.frozen_same", (st2["motion_state"], st2["pos_steps"] == st1["pos_steps"]))
    s.wait_state()
    s.snap("stall.done")                                           # target, MOVE_DONE TARGET only (exact)
    s.L.cmd("MOVE_ABS", HOME_BACK)
    s.wait_state()
    s.new_events()
    with_stall = _duration_ms(s, MOVE_A, stall_ms=500)
    s.rec("stall.delay_500ms", abs((with_stall - ref) - 500.0) <= 10.0 + 1.0)
    s.L.cmd("MOVE_ABS", HOME_BACK)
    s.wait_state()
    s.new_events()


def sc_stall_stop(s: Script) -> None:
    tol = pos_tol(V)
    s.resp("stall2.move", "MOVE_ABS", {"target_um": 50_000, "v_um_s": V, "a_um_s2": 0})
    s.run(300)
    s.at_ms_boundary()
    _stall(s)                                                      # until the move ends
    s.run(300)
    st1 = s.snap("stall2.frozen", tol=tol, events=False)
    s.run(300)
    s.rec("stall2.frozen_same", s.L.status()["pos_steps"] == st1["pos_steps"])
    s.resp("stall2.stop0", "STOP", {"mode": 0})
    s.wait_state()
    st = s.snap("stall2.stopped", tol=tol, events=False)
    s.rec("stall2.events", [(c, a) for c, a, _v, _v2 in s.new_events()])
    s.rec("stall2.stop_at_frozen", st["pos_steps"] == st1["pos_steps"])
    s.rec("stall2.uncertain", "POS_UNCERTAIN" in st["status"])
    s.L.cmd("MOVE_ABS", HOME_BACK)                                 # the stop ended the stall: moves normally
    s.wait_state()
    s.snap("stall2.back")


def sc_stall_armed(s: Script) -> None:
    _stall(s, duration_ms=300)                                     # idle: armed for the next move
    p0 = s.L.status()["pos_steps"]
    s.resp("stall3.move", "MOVE_ABS", {"target_um": 5_000, "v_um_s": V, "a_um_s2": 0})
    s.run(250)
    st = s.L.status()
    s.rec("stall3.frozen", (st["motion_state"], st["pos_steps"] == p0))
    s.wait_state()
    s.snap("stall3.done")


def run_all(board) -> Script:  # noqa: ANN001
    s = Script(board)
    board.advance_ms(5)
    world_setup(s)
    enable_home(s)
    for fn in (sc_stall, sc_stall_stop, sc_stall_armed):
        fn(s)
    return s


@pytest.fixture(scope="module")
def runs(twin_exe, tmp_path_factory):
    from bend_stand.core import protocol_gen as pg

    from lockstep_boards import Link, SimBoardSide, TwinBoard

    tb = TwinBoard(twin_exe, tmp_path_factory.mktemp("twin"))
    try:
        tb.advance_ms(5)
        feats = Link(tb).cmd("GET_INFO")["info"]["features"]
        mask = sum(int(pg.Features[n]) for n in feats if n != "TWIN")
        if not {"MOTION", "HOMING"} <= set(feats):
            pytest.skip("A's FW build reports no MOTION/HOMING")
        tw = run_all(tb)
    finally:
        tb.close()
    sb = SimBoardSide(mask, tmp_path_factory.mktemp("sim"))
    try:
        sim: Script | str = run_all(sb)
    except Unsupported as e:
        sim = str(e)
    finally:
        sb.close()
    return tw, sim


SECTIONS = ("stall", "stall2", "stall3")


@pytest.mark.req("SYS-008", "IF-010", "SW-SEQ-007")
@pytest.mark.parametrize("section", SECTIONS)
def test_sim_vs_twin_stall(runs, section):
    tw, sim = runs
    if isinstance(sim, str):
        pytest.xfail(f"Implementer B's simulator lacks the v0.7.5 vocabulary action (flips by itself): {sim}")

    def sec(s: Script) -> list[tuple]:
        return [x for x in s.exact if x[0].split(".")[0] == section]
    a, b = sec(tw), sec(sim)
    diffs = [(x, y) for x, y in zip(a, b) if x != y]
    if len(a) != len(b):
        diffs.append((f"twin {len(a)} items", f"sim {len(b)} items"))
    pos = [(k, v, sim.approx[k][0], t) for k, (v, t) in tw.approx.items()
           if k.split(".")[0] == section and k in sim.approx and abs(v - sim.approx[k][0]) > t]
    lines = [f"  twin {x}\n  sim  {y}" for x, y in diffs] + [f"  {k}: twin {v} sim {w} (tol ±{t})" for k, v, w, t in pos]
    assert not diffs and not pos, f"SIM vs twin [{section}]:\n" + "\n".join(lines)


def test_twin_stall_transcript_facts(runs):
    """The twin's own transcript (independent of the simulator): frozen while stalled, delayed by 500 ms, the stop
    lands on the frozen position without POS_UNCERTAIN, the armed stall holds the first 300 ms of the next move."""
    tw, _sim = runs
    ex = dict(tw.exact)
    assert ex["stall.frozen_same"] == ("MOVE_ABS", True)
    assert ex["stall.delay_500ms"] is True
    assert ex["stall2.frozen_same"] is True and ex["stall2.stop_at_frozen"] is True
    assert ex["stall2.uncertain"] is False
    assert ex["stall2.events"] == [("STOPPED", 1), ("MOVE_DONE", 5)]
    assert ex["stall3.frozen"] == ("MOVE_ABS", True)
