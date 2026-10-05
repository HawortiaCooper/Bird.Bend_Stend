"""Simulator M3 alignment (WP-B23, ICD v0.7.2): MOVE_UNTIL_LOAD execution rules (a)–(d) (OI-FW-43), POS_UNCERTAIN
at the E-stop edge (SWC-M3-01), world mechanics independent of the board steps/mm (SWC-M3-03), the calibration
load model (weights ``weight`` / ``set_cell_load``, gain scaling, drift, creep, non-linearity, R2 noise).

Expected values come from the ICD §5.4 / §6.2 text and tools/README, not from the simulator code.

Verifies: SYS-008, FW-MOT-006, SAF-FW-004, SAF-FW-008, FW-MOT-009, SW-CAL-005
"""
from __future__ import annotations

import statistics

import pytest

from bend_stand.core import protocol_gen as pg
from bend_stand.io import protocol as P
from sim.test_sim_board import Client
from sim.test_sim_motion import _codes, _move, _ready, _run_until, _set

Cmd, EV, DS, DF = pg.Cmd, pg.Event, pg.DataStatus, pg.DataFlags
GE, LE = int(pg.MulCmp.GE), int(pg.MulCmp.LE)


def _mul(cl: Client, bound_um: int, raw_stop: int, cmp_: int = GE, v: int = 2000) -> P.Response:
    return cl.cmd(Cmd.MOVE_UNTIL_LOAD, P.build_request(Cmd.MOVE_UNTIL_LOAD, bound_um=bound_um, v_um_s=v, a_um_s2=0,
                                                       raw_stop=raw_stop, cmp=cmp_))


def _done(cl: Client, since: int) -> list[P.EventPayload]:
    return [e for e in cl.events[since:] if e.code == EV.MOVE_DONE]


def _reason(e: P.EventPayload) -> str:
    return pg.MoveDoneReason(e.arg).name


@pytest.mark.req("FW-MOT-006", "SYS-008")
def test_mul_threshold_stop_keeps_valid_no_stopped() -> None:
    """(d) a threshold sample: CLEAN halt, MOVE_DONE LOAD_THRESHOLD, no STOPPED, VALID kept, no POS_UNCERTAIN."""
    cl = _ready()
    cl.ctl.act("specimen", kind="spring", k_n_per_mm=200.0, x_contact_um=int(cl.b._x_true()) + 1_000)  # noqa: SLF001
    assert cl.cmd(Cmd.SET_VALID, b"\x01").ok
    n0 = len(cl.events)
    raw0 = cl.b.last_raw
    assert _mul(cl, cl.b.pos_um + 20_000, raw0 + 3285 * 100).ok          # stop at +100 N
    assert _run_until(cl, lambda: _done(cl, n0), 30_000)
    d = _done(cl, n0)[0]
    assert _reason(d) == "LOAD_THRESHOLD" and "STOPPED" not in _codes(cl, n0)
    assert cl.b.valid == 1 and not cl.b.pos_uncertain and cl.b.homed
    assert cl.b.last_raw >= raw0 + 3285 * 100 - 3 * 45


@pytest.mark.req("FW-MOT-006", "SAF-FW-008")
def test_mul_load_limit_sample_is_a_load_limit_stop_never_threshold() -> None:
    """(a) a sample that trips the FW load limit and is also beyond raw_stop → FAULT_SET + STOPPED (LOAD_LIMIT) +
    MOVE_DONE STOPPED."""
    cl = _ready()
    _set(cl, "safety.load_raw_max", cl.b.last_raw + 3285 * 50)
    cl.ctl.act("specimen", kind="spring", k_n_per_mm=5000.0, x_contact_um=int(cl.b._x_true()) + 500)  # noqa: SLF001
    n0 = len(cl.events)
    assert _mul(cl, cl.b.pos_um + 20_000, cl.b.last_raw + 3285 * 40).ok     # threshold below the limit …
    cl.ctl.act("afe", raw_script=[pg.RAW_MAX - 1] * 3)                       # … but one sample jumps over both
    assert _run_until(cl, lambda: _done(cl, n0), 30_000)
    codes = _codes(cl, n0)
    assert "FAULT_SET" in codes and "STOPPED" in codes
    st = [e for e in cl.events[n0:] if e.code == EV.STOPPED][0]
    assert pg.StopCause(st.arg).name == "LOAD_LIMIT" and _reason(_done(cl, n0)[0]) == "STOPPED"


@pytest.mark.req("FW-MOT-006", "SAF-FW-003")
def test_mul_threshold_cuts_a_controlled_stop_reason_stays_stopped() -> None:
    """(b) controlled stop in progress: the compare stays armed; a threshold sample cuts the ramp CLEAN, MOVE_DONE
    reason stays STOPPED, exactly one STOPPED."""
    cl = _ready()
    n0 = len(cl.events)
    raw_stop = cl.b.last_raw + 3285 * 100
    assert _mul(cl, cl.b.pos_um + 50_000, raw_stop, v=20_000).ok
    cl.run(800)
    assert cl.cmd(Cmd.STOP, b"\x01").ok                                      # controlled
    cl.run(3)
    assert cl.b.motion is not None and cl.b.motion.stopping
    cl.ctl.act("afe", raw_script=[raw_stop + 10])
    assert _run_until(cl, lambda: _done(cl, n0), 5_000, 1)
    assert _reason(_done(cl, n0)[0]) == "STOPPED"
    assert [c for c in _codes(cl, n0)].count("STOPPED") == 1 and not cl.b.pos_uncertain


@pytest.mark.req("FW-MOT-006")
@pytest.mark.parametrize("beyond", [False, True])
def test_mul_bound_within_one_step_completes_at_once(beyond: bool) -> None:
    """(c) a bound ≠ the position that rounds to the current step: no pulse, LOAD_THRESHOLD if the last sample is
    beyond raw_stop, else BOUND."""
    cl = _ready()
    assert _move(cl, 10_000).ok
    assert _run_until(cl, lambda: cl.b.motion is None and cl.b.pos_um == 10_000, 30_000)
    cl.run(50)
    _set(cl, "motion.steps_per_mm", 100.0)                                   # 1 µm = 0.1 step (check vector setup)
    p0 = cl.b.pulses
    n0 = len(cl.events)
    raw = cl.b.last_raw
    cmp_, stop = (LE, raw + 10_000) if beyond else (GE, raw + 10_000)
    assert _mul(cl, cl.b.pos_um + 1, stop, cmp_).ok                           # rounds to the current step
    cl.run(20)
    assert cl.b.pulses == p0 and _reason(_done(cl, n0)[0]) == ("LOAD_THRESHOLD" if beyond else "BOUND")


@pytest.mark.req("FW-MOT-006")
def test_mul_equality_counts_as_beyond_both_senses() -> None:
    cl = _ready()
    assert cl.b._beyond(100, 100, GE) and cl.b._beyond(100, 100, LE)  # noqa: SLF001
    assert not cl.b._beyond(99, 100, GE) and not cl.b._beyond(101, 100, LE)  # noqa: SLF001
    assert not cl.b._beyond(pg.AFE_NO_DATA, 0, GE)  # noqa: SLF001


@pytest.mark.req("SAF-FW-004", "SYS-008")
def test_estop_while_running_always_sets_pos_uncertain() -> None:
    """SWC-M3-01 (ICD v0.7.2 §6.2): the E-stop TRUNCATE sets POS_UNCERTAIN whenever the step output was running."""
    cl = _ready()
    assert _move(cl, cl.b.pos_um + 50_000).ok
    cl.run(300)
    cl.b.motion.t_next_us = cl.b.now_us() + 5_000                            # between two pulses (low phase)
    cl.ctl.act("estop", open=True)
    cl.run(5)
    assert cl.b.motion is None and cl.b.pos_uncertain
    cl2 = _ready()
    cl2.ctl.act("estop", open=True)                                          # idle: nothing running → not set
    cl2.run(5)
    assert not cl2.b.pos_uncertain


@pytest.mark.req("FW-MOT-009", "SYS-008")
def test_world_travel_per_pulse_is_the_mechanics_not_the_board_parameter() -> None:
    """SWC-M3-03: SET motion.steps_per_mm changes the µm per commanded step, never the carriage travel per pulse —
    so a travel calibration measures the real mechanics."""
    cl = _ready()
    x0, p0 = cl.b._x_true(), cl.b.pulses  # noqa: SLF001
    _set(cl, "motion.steps_per_mm", 640.0)
    assert cl.b._x_true() == pytest.approx(x0)  # noqa: SLF001
    assert _move(cl, cl.b.pos_um + 10_000).ok                                 # 10 mm at 640 steps/mm = 6400 steps
    assert _run_until(cl, lambda: cl.b.motion is None and cl.b.pulses > p0, 30_000)
    assert cl.b.pulses - p0 == 6400
    assert cl.b._x_true() - x0 == pytest.approx(6400 * 1000.0 / 800.0)     # = 8 mm of real travel  # noqa: SLF001


def _raws(cl: Client, n: int) -> list[int]:
    out = []
    for _ in range(n):
        cl.run(13)
        out.append(cl.b.last_raw)
    return out


@pytest.mark.req("SW-CAL-005", "SYS-008")
def test_load_model_weight_gain_drift_noise() -> None:
    cl = Client()
    cl.cmd(Cmd.STREAM_START)
    cl.run(500)
    base = statistics.mean(_raws(cl, 200))
    assert abs(base - 50_000) < 20 and 30 < statistics.stdev(_raws(cl, 200)) < 60     # R2: 45 counts rms
    assert cl.ctl.act("weight", kg=10.0) == {"ok": True, "weight_n": pytest.approx(98.0665)}
    m10 = statistics.mean(_raws(cl, 200))
    assert m10 - base == pytest.approx(3285 * 98.0665, rel=2e-4)
    assert cl.ctl.act("query", what="world")["weight_n"] == pytest.approx(98.0665)
    assert cl.cmd(Cmd.SET_PARAM, P.build_set_param("afe.gain_channel", "A64")).ok
    cl.run(200)
    m64 = statistics.mean(_raws(cl, 200))
    assert m64 == pytest.approx(0.5 * m10, rel=5e-4)                           # A64 halves the cell output
    assert cl.cmd(Cmd.SET_PARAM, P.build_set_param("afe.gain_channel", "A128")).ok
    cl.ctl.act("weight", n=0.0)
    cl.ctl.set_afe_drift(600.0)                                                 # 10 counts/s
    cl.run(200)
    a = statistics.mean(_raws(cl, 80))
    cl.run(5000)
    b = statistics.mean(_raws(cl, 80))
    assert b - a == pytest.approx(10 * (5.0 + 80 * 0.013), abs=15)
    assert cl.ctl.act("query", what="world")["drift_counts"] > 50
    cl.ctl.act("afe", drift_counts_per_s=0.0)                                   # vocabulary form (v0.7.2)


@pytest.mark.req("SW-CAL-005", "SYS-008")
def test_load_model_swing_creep_nonlinearity_and_rails() -> None:
    cl = Client()
    cl.cmd(Cmd.STREAM_START)
    cl.run(300)
    cl.ctl.set_cell_load(mass_kg=10.0, swing_n=20.0, swing_tau_s=0.5)
    early = _raws(cl, 40)                                                        # swinging
    cl.run(4000)
    late = _raws(cl, 80)                                                         # settled
    assert statistics.stdev(early) > 3 * statistics.stdev(late)
    cl.ctl.set_cell_load(0.0)
    cl.ctl.act("afe", nonlin_pct_fs=0.1, fs_n=1961.33)
    cl.ctl.act("weight", n=980.665)                                              # u = 0.5 → +0.1 % FS
    lin = 50_000 + 3285 * 980.665
    assert statistics.mean(_raws(cl, 100)) - lin == pytest.approx(3285 * 1.96133, rel=0.05)
    cl.ctl.act("afe", nonlin_pct_fs=0.0, creep_pct=1.0, creep_tau_s=1.0)
    cl.run(6000)
    assert cl.ctl.act("query", what="world")["creep_counts"] == pytest.approx(0.01 * 3285 * 980.665, rel=0.02)
    cl.ctl.act("afe", creep_pct=0.0)
    cl.ctl.set_cell_load(5000.0)                                                 # beyond the rail (≈ 2550 N)
    cl.run(100)
    assert cl.b.last_raw == pg.RAW_MAX
