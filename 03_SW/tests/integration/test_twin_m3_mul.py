"""MOVE_UNTIL_LOAD (FW-MOT-006; D-44: Implementer A pulls it forward into M3) in the lock-step twin.

Gated on the INFO feature bit MOVE_UNTIL_LOAD (skipped until A's build reports it). Expected values come from the
ICD (§5.4 MOVE_UNTIL_LOAD, §6.2) and motion_vectors.json (case ``mul_to_bound_5mm``, list ``immediate_stops``),
never from A's code. World: spring specimen, no noise, offset 50 000 counts (raw = 50 000 + 3 285·F).

Verifies: FW-MOT-006, FW-MOT-003, SAF-FW-002, IF-010
"""
from __future__ import annotations

import pytest

import ref_codec as rc
from test_twin_m2_motion import MOTION, TICK_US, enable, events, home, needs, rises, run, status, wait_done
from twin import TwinLink

pytestmark = pytest.mark.twin

CPN = 3285.0
OFFSET = 50_000
CASE = MOTION["mul_to_bound_5mm"]


@pytest.fixture
def ready(twin):
    link = TwinLink(twin)
    twin.advance_ms(5)
    needs(link, "MOTION", "HOMING", "MOVE_UNTIL_LOAD")
    link.cmd("STREAM_STOP")
    enable(twin, link)
    home(twin, link)
    return twin, link


def _x0(tw) -> float:
    w = tw.act("query", what="world")
    return w["x_um_true"] - w["pos_steps"] * 1000.0 / 800.0


def _mul(link, bound, raw_stop, cmp=0, v=2000, a=100_000):  # noqa: ANN001, ANN202
    r = link.cmd("MOVE_UNTIL_LOAD", {"bound_um": bound, "v_um_s": v, "a_um_s2": a, "raw_stop": raw_stop, "cmp": cmp})
    assert r["status"] == "OK", r
    return r


@pytest.mark.req("FW-MOT-006", "FW-MOT-003")
def test_mul_to_bound_periods_and_bound(ready):
    """No load: the segment to a 5 mm bound = motion vector ``mul_to_bound_5mm`` (±1 tick), MOVE_DONE BOUND at the
    bound exactly, VALID untouched."""
    tw, link = ready
    t0 = tw.now_us
    n0 = len(events(link, "MOVE_DONE"))
    _mul(link, 5_000, OFFSET + 1_000_000)
    d = wait_done(tw, link, 10_000, n0)
    assert (d["arg"], d["value"]) == (rc.MOVE_DONE_REASON.index("BOUND"), 5_000)
    t = rises(tw, t0)
    assert len(t) == CASE["n_steps"]
    got = [(b - a) / TICK_US for a, b in zip(t, t[1:])]
    worst = max(abs(g - w) for g, w in zip(got, CASE["periods"][1:]))
    assert worst <= CASE["tolerance"]["period_ticks"] + 0.2, worst


@pytest.mark.req("FW-MOT-006", "SAF-FW-002")
def test_mul_threshold_stop_timing_and_reason(ready):
    """Spring 100 N/mm from machine 2 mm, raw_stop = offset + 3 285·50 N: the first sample at or beyond raw_stop
    stops the axis immediately (no PUL edge > 200 µs after its DRDY, no ramp-down: the pulse train is a prefix
    of ``mul_to_bound_5mm``), MOVE_DONE LOAD_THRESHOLD (no STOPPED, no fault), position ≈ 2.5 mm."""
    tw, link = ready
    tw.act("specimen", kind="spring", k_n_per_mm=100.0, x_contact_um=_x0(tw) + 2_000)
    raw_stop = int(OFFSET + CPN * 50.0)
    t0 = tw.now_us
    n0, s0 = len(events(link, "MOVE_DONE")), len(events(link, "STOPPED"))
    _mul(link, 5_000, raw_stop)
    d = wait_done(tw, link, 10_000, n0)
    assert d["arg"] == rc.MOVE_DONE_REASON.index("LOAD_THRESHOLD"), d
    assert len(events(link, "STOPPED")) == s0 and not status(link)["faults"]
    conv = [c for c in tw.act("query", what="conversions", since_us=t0)["conversions"] if c["raw"] >= raw_stop]
    t_drdy = conv[0]["t_us"]
    after = rises(tw, t_drdy)
    assert not after or after[0] - t_drdy <= 200.0
    t = rises(tw, t0)
    got = [(b - a) / TICK_US for a, b in zip(t, t[1:])]
    assert all(abs(g - w) <= CASE["tolerance"]["period_ticks"] + 0.2 for g, w in zip(got, CASE["periods"][1:]))
    assert abs(status(link)["pos_um"] - 2_500) <= 50                       # 50 N at 100 N/mm + one sample period


@pytest.mark.req("FW-MOT-006")
def test_mul_already_beyond_no_pulse(ready):
    """Last sample already beyond raw_stop: MOVE_DONE LOAD_THRESHOLD without a single pulse (immediate_stops
    ``mul_threshold_before_first_pulse``), so a repeated command never moves further."""
    tw, link = ready
    tw.act("specimen", kind="spring", k_n_per_mm=100.0, x_contact_um=_x0(tw) - 1_000)   # 100 N at machine 0
    run(tw, link, 100)
    t0 = tw.now_us
    n0 = len(events(link, "MOVE_DONE"))
    _mul(link, 5_000, int(OFFSET + CPN * 50.0))
    d = wait_done(tw, link, 2_000, n0)
    assert d["arg"] == rc.MOVE_DONE_REASON.index("LOAD_THRESHOLD")
    assert not rises(tw, t0)
    assert status(link)["pos_um"] == 0


@pytest.mark.req("FW-MOT-006")
def test_mul_cmp_le_toward_lower_bound(ready):
    """cmp 1 (raw ≤ raw_stop) moving toward a lower bound with a negative-K-like fall of raw: the specimen is
    released while moving back, the threshold on the falling raw stops the axis (LOAD_THRESHOLD)."""
    tw, link = ready
    run(tw, link, 0)
    assert link.cmd("MOVE_ABS", {"target_um": 6_000, "v_um_s": 5_000, "a_um_s2": 0})["status"] == "OK"
    wait_done(tw, link, 5_000, len(events(link, "MOVE_DONE")))
    tw.act("specimen", kind="spring", k_n_per_mm=100.0, x_contact_um=_x0(tw) + 2_000)   # 400 N at 6 mm
    run(tw, link, 100)
    n0 = len(events(link, "MOVE_DONE"))
    _mul(link, 1_000, int(OFFSET + CPN * 200.0), cmp=1)                  # stop when F ≤ 200 N (≈ 4 mm)
    d = wait_done(tw, link, 10_000, n0)
    assert d["arg"] == rc.MOVE_DONE_REASON.index("LOAD_THRESHOLD"), d
    assert abs(status(link)["pos_um"] - 4_000) <= 50


# ================================================================================ OI-FW-43 execution rules (ICD v0.7.2 §5.4)
@pytest.mark.req("FW-MOT-006", "SAF-FW-008")
def test_mul_load_limit_has_precedence(ready):
    """(a) A sample that trips the FW load limit (40 N) is the load limit's stop although it is also beyond
    raw_stop (30 N would be hit first only by the threshold — here raw_stop 50 N is beyond the limit): FAULT_SET
    LOAD_LIMIT, STOPPED LOAD_LIMIT, MOVE_DONE STOPPED, never LOAD_THRESHOLD."""
    tw, link = ready
    from test_twin_m2_motion import setp

    tw.act("specimen", kind="spring", k_n_per_mm=100.0, x_contact_um=_x0(tw) + 2_000)
    setp(link, "safety.load_raw_max", int(OFFSET + CPN * 40.0))
    n0, s0 = len(events(link, "MOVE_DONE")), len(events(link, "STOPPED"))
    _mul(link, 5_000, int(OFFSET + CPN * 40.0))                       # same sample: limit and threshold
    d = wait_done(tw, link, 10_000, n0)
    assert d["arg"] == rc.MOVE_DONE_REASON.index("STOPPED"), d
    st = events(link, "STOPPED")[s0:]
    assert [e["arg"] for e in st] == [rc.STOP_CAUSE["LOAD_LIMIT"]]
    assert "LOAD_LIMIT" in status(link)["faults"]


@pytest.mark.req("FW-MOT-006", "SAF-FW-003", "SAF-FW-023")
def test_mul_threshold_during_controlled_stop_keeps_stopped(ready):
    """(b) PAUSE while moving (a_stop 10 mm/s^2: 0.2 mm ramp from 2 mm/s): the threshold sample during the ramp
    cuts it with a CLEAN halt; MOVE_DONE stays STOPPED (one STOPPED, cause PAUSE), PAUSED latched."""
    tw, link = ready
    from test_twin_m2_motion import setp

    setp(link, "motion.a_stop_um_s2", 10_000)
    tw.act("specimen", kind="spring", k_n_per_mm=100.0, x_contact_um=_x0(tw) + 2_000)
    n0, s0 = len(events(link, "MOVE_DONE")), len(events(link, "STOPPED"))
    _mul(link, 10_000, int(OFFSET + CPN * 45.0))                       # threshold at 2.45 mm
    while status(link)["pos_um"] < 2_350:
        tw.advance_ms(5)
    link.send("PAUSE")
    d = wait_done(tw, link, 5_000, n0)
    assert d["arg"] == rc.MOVE_DONE_REASON.index("STOPPED"), d
    assert [e["arg"] for e in events(link, "STOPPED")[s0:]] == [rc.STOP_CAUSE["PC_PAUSE"]]
    st = status(link)
    assert "PAUSED" in st["status"] and "POS_UNCERTAIN" not in st["status"]
    assert 2_440 <= st["pos_um"] <= 2_480, st["pos_um"]               # cut at the threshold, not at ~2.55 mm


@pytest.mark.req("FW-MOT-006")
@pytest.mark.parametrize("beyond", [False, True])
def test_mul_bound_within_one_step(ready, beyond):
    """(c) bound 1 µm from the position at 100 steps/mm (0.1 step: rounds to the current step): completes at once
    without a pulse — BOUND, or LOAD_THRESHOLD when the last sample is already beyond raw_stop. (At 800 steps/mm
    1 µm = 0.8 step rounds to one step: a normal one-step move.)"""
    tw, link = ready
    from test_twin_m2_motion import setp

    setp(link, "motion.steps_per_mm", 100.0)                           # FW-MOT-009: HOMED kept
    assert link.cmd("MOVE_ABS", {"target_um": 5_000, "v_um_s": 5_000, "a_um_s2": 0})["status"] == "OK"
    wait_done(tw, link, 5_000, len(events(link, "MOVE_DONE")))
    assert status(link)["pos_um"] == 5_000
    if beyond:                                                          # 100 N at the current position
        tw.act("specimen", kind="spring", k_n_per_mm=100.0,
               x_contact_um=tw.act("query", what="world")["x_um_true"] - 1_000)
    run(tw, link, 100)
    t0 = tw.now_us
    n0 = len(events(link, "MOVE_DONE"))
    _mul(link, 5_001, int(OFFSET + CPN * 50.0))
    d = wait_done(tw, link, 2_000, n0)
    assert d["arg"] == rc.MOVE_DONE_REASON.index("LOAD_THRESHOLD" if beyond else "BOUND"), d
    assert not rises(tw, t0)


@pytest.mark.req("FW-MOT-006")
def test_mul_threshold_keeps_valid(ready):
    """(d) LOAD_THRESHOLD is not a stop source: VALID stays set, no STOPPED / VALID_CLEARED, HOMED kept."""
    tw, link = ready
    assert link.cmd("SET_VALID", {"valid": 1})["status"] == "OK"
    tw.act("specimen", kind="spring", k_n_per_mm=100.0, x_contact_um=_x0(tw) + 2_000)
    n0, s0 = len(events(link, "MOVE_DONE")), len(events(link, "STOPPED"))
    v0 = len(events(link, "VALID_CLEARED"))
    _mul(link, 5_000, int(OFFSET + CPN * 50.0))
    d = wait_done(tw, link, 10_000, n0)
    assert d["arg"] == rc.MOVE_DONE_REASON.index("LOAD_THRESHOLD")
    assert len(events(link, "STOPPED")) == s0 and len(events(link, "VALID_CLEARED")) == v0
    st = status(link)
    assert "VALID" in st["flags"] and "HOMED" in st["flags"]
