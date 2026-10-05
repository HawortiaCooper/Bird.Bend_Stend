"""SIM-vs-twin **motion** differential subset (MC2-6, M2 gate condition; SW_test_plan TC-SYS-008-02 M2 part).

The same scripted scenario runs against Implementer B's simulator (``SimBoard`` on a lock-step clock) and the FW
host twin (A's unmodified firmware, lock-step), both driven by the same PC-side ``Link`` at the same virtual
instants (``lockstep_boards.py``: no wall clock, deterministic). Scenarios: homing, MOVE_ABS, JOG with a bound,
stops (STOP 0 / STOP 1 / HALT + HALT_CLEAR), limit switches, E-stop, FW load limit, PAUSE / RESUME and
MOVE_UNTIL_LOAD (when both report the feature).

Comparison rule (tools/README "SIM-vs-twin comparison rule", with the OI-B-M2-04 coordinate convention):
- responses (status, detail), EVENT sequences (code, arg; ``value`` / ``value2`` where they are positions or
  reasons), STATUS latches (flags, status, faults, motion state, home phase) and ``pos_um`` / ``pos_steps`` are
  compared **exactly** for world- or plan-determined end points (move target, JOG bound, homing at the START
  edge, limit-switch stop at the first active step, MOVE_UNTIL_LOAD bound);
- for stops triggered at a time (a command or an input edge while moving) the end position may differ by the
  simulator's 1 ms step grid, which delays both the start of the move and the reaction by up to 1 ms each:
  ``|Δ| ≤ ceil(v · 2 ms) + 1`` steps (``pos_tol``), all the rest (events, latches, reasons) exactly; a FW load-limit trip depends on the HX711 conversion phase
  (12.5 ms at 80 SPS): ``|Δ| ≤ ceil(v · 12.5 ms) + 1`` steps and the trip raw is not compared;
- POS_UNCERTAIN after an E-stop edge while moving is compared in its own section ``estop_uncertain``
  (ICD v0.7.2 §6.2: set whenever the step output was running at the edge — finding SWC-M3-01, aligned by B);
- boot-time EVENT order (BOOT / PARAMS_DEFAULTED, unspecified in ICD §11.3) and all times are not compared;
- world: carriage at x = 0 at power-up, switches START −1.5 mm / END 301 mm, offset 50 000 counts, no noise,
  automatic PEND (``driver pend_auto``), feature mask of A's build mirrored by the simulator (D-37 b).

Verifies: SYS-008, IF-010 (behavioural equality), FW-HOM-001, FW-MOT-003/004/005/006, SAF-FW-002, SAF-FW-003,
          SAF-FW-005, SAF-FW-008, SAF-FW-013, SAF-FW-023, D-30, D-31
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

import gen_params
import ref_codec as rc

pytestmark = [pytest.mark.twin, pytest.mark.needs_b("bend_stand.io.sim.board", "SimBoard")]

_PD = gen_params.load()
PID = {p.key: p.id for p in _PD.params}
PTYPE = {p.key: p.type for p in _PD.params}
SPM = 800.0
BACK = {"target_um": 5_000, "v_um_s": 20_000, "a_um_s2": 0}      # inside the soft limits
NB = rc.JOG_NO_BOUND
STATUS_KEYS = ("motion_state", "pos_um", "pos_steps", "flags", "status", "faults", "home_phase")


def pos_tol(v_um_s: float, window_ms: float = 2.0) -> int:
    return math.ceil(abs(v_um_s) * SPM / 1000 * window_ms / 1000) + 1


def _ev(e: dict) -> tuple:
    return (e["code"], e["arg"], e["value"], e["value2"])


class Script:
    """Runs one scenario on one board; records a transcript of exact items and of toleranced positions."""

    def __init__(self, board) -> None:  # noqa: ANN001
        from lockstep_boards import Link

        self.b = board
        self.L = Link(board)
        self.exact: list[tuple] = []
        self.approx: dict[str, tuple[float, int]] = {}
        self._ev0 = 0

    # ------------------------------------------------------------------ recording
    def rec(self, label: str, value) -> None:  # noqa: ANN001
        self.exact.append((label, value))

    def new_events(self, positional: bool = True) -> list[tuple]:
        evs = [e for e in self.L.events() if e["code"] not in ("BOOT", "PARAMS_DEFAULTED")]
        out, self._ev0 = evs[self._ev0:], len(evs)
        return [_ev(e) if positional else (e["code"], e["arg"]) for e in out]

    def snap(self, label: str, *, tol: int | None = None, events: bool = True, phase_bits: bool = False) -> dict:
        st = self.L.status()
        keys = STATUS_KEYS if tol is None else tuple(k for k in STATUS_KEYS if k not in ("pos_um", "pos_steps"))
        rec = {k: st[k] for k in keys}
        if phase_bits:      # POS_UNCERTAIN after an E-stop edge: compared in its own section (finding SWC-M3-01)
            rec["status"] = [b for b in rec["status"] if b != "POS_UNCERTAIN"]
            self.rec("estop_uncertain." + label, "POS_UNCERTAIN" in st["status"])
        self.rec(label + ".status", rec)
        if events:
            self.rec(label + ".events", self.new_events(positional=tol is None))
        if tol is not None:
            self.approx[label + ".pos_steps"] = (st["pos_steps"], tol)
        return st

    def resp(self, label: str, name: str, fields: dict | None = None) -> dict:
        r = self.L.cmd(name, fields)
        self.rec(label, (r["status"], r.get("detail", 0)))
        return r

    # ------------------------------------------------------------------ helpers
    def run(self, ms: float) -> None:
        self.L.run(ms)

    def wait_state(self, states: tuple[str, ...] = ("IDLE", "NOT_ENABLED"), timeout_ms: float = 60_000) -> None:
        t_end = self.b.now_ms + timeout_ms
        while self.b.now_ms < t_end:
            self.run(100)
            if self.L.status()["motion_state"] in states:
                return
        raise AssertionError(f"{self.b.kind}: still moving after {timeout_ms} ms")

    def at_ms_boundary(self) -> None:
        """Align the next action to a whole virtual millisecond (the simulator's step grid)."""
        frac = self.b.now_ms - math.floor(self.b.now_ms)
        if frac:
            self.b.advance_ms(1.0 - frac)


# ==================================================================================== scenarios
def world_setup(s: Script) -> None:
    for a, kw in (("load_offset", {"counts": 50000}), ("afe", {"noise_counts": 0, "rate_error": 0.0}),
                  ("specimen", {"kind": "none"}), ("limit", {"name": "start", "position_um": -1500}),
                  ("limit", {"name": "end", "position_um": 301000}), ("driver", {"pend_auto": True})):
        r = s.b.act(a, **kw)
        assert r.get("ok"), (s.b.kind, a, r)


def enable_home(s: Script) -> None:
    s.L.cmd("STREAM_STOP")
    s.resp("enable", "ENABLE")
    s.run(600)
    s.snap("enabled")
    s.resp("home", "HOME", {"flags": 0})
    s.wait_state()
    s.snap("homed")
    s.rec("homed.world_x_um", round(s.b.world_x_um(), 3))


def sc_moves(s: Script) -> None:
    """MOVE_ABS 10 mm at the defaults, back to 2.5 mm at 5 mm/s, JOG + 5 mm/s with bound 20 mm (dead-man refresh)."""
    p0 = s.b.pulses()
    s.resp("move10", "MOVE_ABS", {"target_um": 10_000, "v_um_s": 30_000, "a_um_s2": 100_000})
    s.wait_state()
    s.snap("move10.done")
    s.resp("move2_5", "MOVE_ABS", {"target_um": 2_500, "v_um_s": 5_000, "a_um_s2": 50_000})
    s.wait_state()
    s.snap("move2_5.done")
    s.rec("pulses", s.b.pulses() - p0)
    s.rec("world_x_um", round(s.b.world_x_um(), 3))
    s.resp("jog", "JOG", {"v_um_s": 5_000, "a_um_s2": 0, "bound_um": 20_000})
    for _ in range(60):                                   # held jog: refresh every 100 ms until the bound
        s.b.advance_ms(100)
        if s.L.status()["motion_state"] != "JOG":
            break
        s.L.cmd("JOG", {"v_um_s": 5_000, "a_um_s2": 0, "bound_um": 20_000})
    s.wait_state()
    s.snap("jog.bound")
    s.resp("jog_behind", "JOG", {"v_um_s": 5_000, "a_um_s2": 0, "bound_um": 20_000})    # bound not ahead


def _long_move(s: Script, v: int = 10_000) -> None:
    s.L.cmd("MOVE_ABS", {"target_um": 200_000, "v_um_s": v, "a_um_s2": 0})
    s.run(400)
    s.at_ms_boundary()
    s.new_events()


def sc_stops(s: Script) -> None:
    """STOP 1, STOP 0, HALT (+ refused move, HALT_CLEAR) while cruising at 10 mm/s."""
    tol = pos_tol(10_000)
    s.L.cmd("MOVE_ABS", BACK)
    s.wait_state()
    for label, name, fields in (("stop1", "STOP", {"mode": 1}), ("stop0", "STOP", {"mode": 0}), ("halt", "HALT", {})):
        _long_move(s)
        s.resp(label, name, fields)
        s.wait_state()
        s.snap(label + ".end", tol=tol)
        if name == "HALT":
            s.resp("halt.move_refused", "MOVE_ABS", BACK)
            s.resp("halt.clear", "HALT_CLEAR")
            s.snap("halt.cleared", tol=tol)
        s.L.cmd("MOVE_ABS", BACK)
        s.wait_state()
        s.snap(label + ".back")


def sc_pause_resume(s: Script) -> None:
    tol = pos_tol(10_000)
    _long_move(s)
    s.resp("pause", "PAUSE")
    s.wait_state()
    s.snap("pause.end", tol=tol)
    s.resp("pause.move_refused", "MOVE_ABS", {"target_um": 30_000, "v_um_s": 10_000, "a_um_s2": 0})
    s.resp("resume", "RESUME")
    s.resp("pause.move_ok", "MOVE_ABS", {"target_um": 30_000, "v_um_s": 10_000, "a_um_s2": 0})
    s.wait_state()
    s.snap("pause.reissued")


def sc_limit(s: Script) -> None:
    """END switch at 50 mm: the move stops at the first active step (OI-B-M2-04, exact); toward refused, away OK."""
    s.b.act("limit", name="end", position_um=50_000)
    s.resp("limit.move", "MOVE_ABS", {"target_um": 100_000, "v_um_s": 5_000, "a_um_s2": 0})
    s.wait_state()
    s.snap("limit.stopped")
    s.rec("limit.world_x_um", round(s.b.world_x_um(), 3))
    s.resp("limit.toward", "MOVE_ABS", {"target_um": 100_000, "v_um_s": 5_000, "a_um_s2": 0})
    s.resp("limit.away", "MOVE_ABS", {"target_um": 40_000, "v_um_s": 5_000, "a_um_s2": 0})
    s.wait_state()
    s.run(300)                                          # release debounce, latch auto-clear
    s.snap("limit.away_done")
    s.b.act("limit", name="end", position_um=301_000)


def sc_load_limit(s: Script) -> None:
    """Spring specimen 200 N/mm from 50 mm: the FW load limit (raw max 400 000) stops a 2 mm/s move."""
    s.L.cmd("MOVE_ABS", {"target_um": 45_000, "v_um_s": 20_000, "a_um_s2": 0})
    s.wait_state()
    s.new_events()
    s.b.act("specimen", kind="spring", k_n_per_mm=200.0, x_contact_um=50_000)
    s.resp("ll.set", "SET_PARAM", {"id": PID["safety.load_raw_max"], "type": PTYPE["safety.load_raw_max"],
                                   "value": 400_000})
    s.resp("ll.move", "MOVE_ABS", {"target_um": 100_000, "v_um_s": 2_000, "a_um_s2": 0})
    s.wait_state()
    st = s.snap("ll.tripped", tol=pos_tol(2_000, 12.5), events=False)
    s.rec("ll.events", [(c, a) for c, a, _v, _v2 in s.new_events()])
    s.rec("ll.faults", st["faults"])
    s.b.act("specimen", kind="none")
    s.run(100)
    s.resp("ll.clear", "FAULT_CLEAR")
    s.snap("ll.cleared", tol=pos_tol(2_000, 12.5))


def sc_mul(s: Script) -> None:
    """MOVE_UNTIL_LOAD (FW-MOT-006, D-44): to a bound (exact) and stopped by the load threshold (sample phase)."""
    s.L.cmd("MOVE_ABS", {"target_um": 45_000, "v_um_s": 20_000, "a_um_s2": 0})
    s.wait_state()
    s.new_events()
    s.b.act("specimen", kind="spring", k_n_per_mm=100.0, x_contact_um=50_000)
    s.resp("mul.bound", "MOVE_UNTIL_LOAD", {"bound_um": 48_000, "v_um_s": 2_000, "a_um_s2": 0, "raw_stop": 300_000,
                                           "cmp": 0})
    s.wait_state()
    s.snap("mul.bound.done")
    s.resp("mul.thr", "MOVE_UNTIL_LOAD", {"bound_um": 60_000, "v_um_s": 2_000, "a_um_s2": 0, "raw_stop": 300_000,
                                         "cmp": 0})
    s.wait_state()
    s.snap("mul.thr.done", tol=pos_tol(2_000, 12.5), events=False)
    s.rec("mul.thr.events", [(c, a) for c, a, _v, _v2 in s.new_events()])
    s.resp("mul.again", "MOVE_UNTIL_LOAD", {"bound_um": 60_000, "v_um_s": 2_000, "a_um_s2": 0, "raw_stop": 300_000,
                                           "cmp": 0})                       # already beyond: no motion
    s.wait_state()
    s.snap("mul.again.done", tol=pos_tol(2_000, 12.5))
    s.b.act("specimen", kind="none")


def sc_mul43(s: Script) -> None:
    """ICD v0.7.2 §5.4 OI-FW-43: (d) LOAD_THRESHOLD keeps VALID / no STOPPED; (a) a load-limit trip sample wins over
    the threshold; (c) a bound within one step (100 steps/mm) completes at once without a pulse."""
    tol = pos_tol(2_000, 12.5)
    s.L.cmd("MOVE_ABS", {"target_um": 45_000, "v_um_s": 20_000, "a_um_s2": 0})
    s.wait_state()
    s.new_events()
    s.resp("mul43.valid", "SET_VALID", {"valid": 1})
    s.b.act("specimen", kind="spring", k_n_per_mm=100.0, x_contact_um=s.b.world_x_um() + 2_000)
    s.resp("mul43.d", "MOVE_UNTIL_LOAD", {"bound_um": 50_000, "v_um_s": 2_000, "a_um_s2": 0, "raw_stop": 50_000 + 3285 * 50,
                                         "cmp": 0})
    s.wait_state()
    s.snap("mul43.d.done", tol=tol, events=False)
    s.rec("mul43.d.events", [(c, a) for c, a, _v, _v2 in s.new_events()])
    s.b.act("specimen", kind="none")
    s.L.cmd("MOVE_ABS", {"target_um": 45_000, "v_um_s": 20_000, "a_um_s2": 0})
    s.wait_state()
    s.new_events()
    s.b.act("specimen", kind="spring", k_n_per_mm=100.0, x_contact_um=s.b.world_x_um() + 2_000)
    s.resp("mul43.a.set", "SET_PARAM", {"id": PID["safety.load_raw_max"], "type": PTYPE["safety.load_raw_max"],
                                        "value": 50_000 + 3285 * 40})
    s.resp("mul43.a", "MOVE_UNTIL_LOAD", {"bound_um": 50_000, "v_um_s": 2_000, "a_um_s2": 0,
                                         "raw_stop": 50_000 + 3285 * 40, "cmp": 0})
    s.wait_state()
    s.snap("mul43.a.done", tol=tol, events=False)
    s.rec("mul43.a.events", [(c, a) for c, a, _v, _v2 in s.new_events()])
    s.b.act("specimen", kind="none")
    s.run(100)
    s.resp("mul43.a.clear", "FAULT_CLEAR")
    s.resp("mul43.a.reset", "SET_PARAM", {"id": PID["safety.load_raw_max"], "type": PTYPE["safety.load_raw_max"],
                                          "value": 7_022_271})
    s.L.cmd("MOVE_ABS", {"target_um": 5_000, "v_um_s": 20_000, "a_um_s2": 0})
    s.wait_state()
    s.resp("mul43.c.spm", "SET_PARAM", {"id": PID["motion.steps_per_mm"], "type": PTYPE["motion.steps_per_mm"],
                                        "value": 100.0})          # 4 000 steps = 40 mm now (FW-MOT-009)
    s.new_events()
    p0 = s.b.pulses()
    s.resp("mul43.c", "MOVE_UNTIL_LOAD", {"bound_um": 40_001, "v_um_s": 2_000, "a_um_s2": 0, "raw_stop": 3_000_000,
                                         "cmp": 0})
    s.wait_state()
    s.snap("mul43.c.done")
    s.rec("mul43.c.pulses", s.b.pulses() - p0)
    s.resp("mul43.c.spm_back", "SET_PARAM", {"id": PID["motion.steps_per_mm"], "type": PTYPE["motion.steps_per_mm"],
                                             "value": 800.0})


def sc_world_spm(s: Script) -> None:
    """The world is mechanics: changing ``motion.steps_per_mm`` on the board changes the µm per commanded step, not
    the carriage travel per pulse (tools/README OI-B-M2-04: one step = 1000 / world steps_per_mm µm). Board at 100
    steps/mm, MOVE +1 mm = 100 pulses = 0.125 mm of world travel at the world's 800 steps/mm (travel calibration,
    SW-CAL-002, relies on this)."""
    s.resp("wspm.set", "SET_PARAM", {"id": PID["motion.steps_per_mm"], "type": PTYPE["motion.steps_per_mm"], "value": 100.0})
    x0, p0 = s.b.world_x_um(), s.b.pulses()
    pos = s.L.status()["pos_um"]
    s.resp("wspm.move", "MOVE_ABS", {"target_um": pos + 1_000, "v_um_s": 2_000, "a_um_s2": 0})
    s.wait_state()
    s.rec("world_spm.pulses", s.b.pulses() - p0)
    s.rec("world_spm.world_dx_um", round(s.b.world_x_um() - x0, 1))
    s.resp("wspm.back", "SET_PARAM", {"id": PID["motion.steps_per_mm"], "type": PTYPE["motion.steps_per_mm"],
                                      "value": 800.0})
    s.new_events()


def sc_estop(s: Script) -> None:
    """E-stop sense opens while cruising (release-1 wiring, power stays): ESTOP latched, HOMED / VALID cleared."""
    _long_move(s)
    r = s.b.act("estop", open=True, drv_power_follows=False)
    assert r.get("ok"), r
    s.run(200)
    s.snap("estop.open", tol=pos_tol(10_000), phase_bits=True)
    s.resp("estop.move_refused", "MOVE_ABS", BACK)
    s.b.act("estop", open=False, drv_power_follows=False)
    s.run(600)
    s.resp("estop.clear", "ESTOP_CLEAR")
    s.snap("estop.cleared", tol=pos_tol(10_000), phase_bits=True)


SCENARIOS = {"moves": sc_moves, "stops": sc_stops, "pause_resume": sc_pause_resume, "limit": sc_limit,
             "load_limit": sc_load_limit, "mul": sc_mul, "mul43": sc_mul43, "world_spm": sc_world_spm,
             "estop": sc_estop}


def run_all(board, features: list[str]) -> Script:  # noqa: ANN001
    s = Script(board)
    board.advance_ms(5)
    world_setup(s)
    enable_home(s)
    for name, fn in SCENARIOS.items():
        if name in ("mul", "mul43") and "MOVE_UNTIL_LOAD" not in features:
            s.rec("mul.skipped", True)
            continue
        fn(s)
    return s


# ==================================================================================== fixtures
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
        tw = run_all(tb, feats)
    finally:
        tb.close()
    sb = SimBoardSide(mask, tmp_path_factory.mktemp("sim"))
    try:
        sim = run_all(sb, feats)
    finally:
        sb.close()
    out = os.environ.get("BEND_DIFF_OUT")
    if out:
        Path(out).write_text(json.dumps({"twin": tw.exact, "sim": sim.exact, "twin_approx": tw.approx,
                                         "sim_approx": sim.approx}, indent=1, default=str), encoding="utf-8")
    return tw, sim


def _section(s: Script, prefix: str) -> list[tuple]:
    return [x for x in s.exact if x[0].split(".")[0] == prefix or x[0].startswith(prefix + ".")]


SWC_M3_01 = ("SWC-M3-01 (to B): the simulator sets POS_UNCERTAIN at an E-stop edge only when a pulse is high at "
             "that instant; ICD v0.7.2 §6.2 / A's FW: whenever the step output was running (io/sim/board.py "
             "_clean_halt_position truncate branch)")
SWC_M3_03 = ("SWC-M3-03 (to B): the simulator integrates the world position with the board's motion.steps_per_mm "
             "instead of the world's mechanics (tools/README OI-B-M2-04: 1000 / world steps_per_mm µm per pulse); a "
             "steps/mm change on the board must not change the carriage travel per pulse (travel calibration)")
SECTIONS = ("enable", "home", "homed", "move10", "move2_5", "pulses", "world_x_um", "jog", "jog_behind", "stop1",
            "stop0", "halt", "pause", "resume", "limit", "ll", "mul", "mul43", "estop",
            "world_spm",                       # SWC-M3-03 closed: B aligned the simulator (strict xfail flipped)
            "estop_uncertain")                 # SWC-M3-01 closed: B aligned the simulator (strict xfail flipped)


@pytest.mark.req("SYS-008", "IF-010", "FW-HOM-001", "FW-MOT-003", "FW-MOT-004", "FW-MOT-005", "FW-MOT-006",
                 "SAF-FW-002", "SAF-FW-003", "SAF-FW-005", "SAF-FW-008", "SAF-FW-013", "SAF-FW-023")
@pytest.mark.parametrize("section", SECTIONS)
def test_sim_vs_twin_motion(runs, section):
    tw, sim = runs
    a, b = _section(tw, section), _section(sim, section)
    diffs = [(x, y) for x, y in zip(a, b) if x != y]
    if len(a) != len(b):
        diffs.append((f"twin {len(a)} items", f"sim {len(b)} items"))
    pos = []
    for k, (v, tol) in tw.approx.items():
        if k.split(".")[0] == section and k in sim.approx and abs(v - sim.approx[k][0]) > tol:
            pos.append((k, v, sim.approx[k][0], tol))
    lines = [f"  twin {x}\n  sim  {y}" for x, y in diffs] + [f"  {k}: twin {v} sim {w} (tol ±{t})" for k, v, w, t in pos]
    assert not diffs and not pos, f"SIM vs twin [{section}]:\n" + "\n".join(lines)
