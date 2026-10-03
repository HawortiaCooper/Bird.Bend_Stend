"""Proves ref_motion.py and motion_vectors.json against R4 §12 TV-M anchors and ICD §6.5 (M2, OI-FW-20).

Verifies: FW-MOT-003, FW-MOT-005, SAF-FW-003
Run:  .venv\\Scripts\\python -m pytest 00_System/tools/tests -q
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import gen_vectors
import ref_motion as rm

MV = json.loads((Path(gen_vectors.VEC_DIR) / "motion_vectors.json").read_text(encoding="utf-8"))
CASES = {c["name"]: c for c in MV["cases"]}
F, A, V = 90e6, 64000.0, 12800.0


def test_tvm_anchors_from_r4() -> None:
    """Independent anchors copied from R4 §12 test_ramp_periods / test_planner."""
    p = CASES["tvm_trapezoid"]["periods"]
    assert p[:5] == [503115, 208397, 159909, 134809, 118770] and sum(p) == 468_000_000
    assert sum(1 for c in p if c > 7032) == 2559 and (p.count(7031), p.count(7032)) == (46080, 15361)
    assert p[30000:30008] == [7031, 7031, 7031, 7032, 7031, 7031, 7031, 7032]
    t = CASES["tvm_triangle"]["periods"]
    assert sum(t) == 22_500_000 and t[-3:] == [159909, 208397, 503116] and min(t) == 11255
    a = CASES["tvm_triangle_asym"]["periods"]
    assert sum(a) == 19_485_570 and a[-1] == 355756 and min(a) == 9744
    plan = MV["planner"]
    assert (plan[0]["kind"], plan[0]["n_acc"], plan[0]["n_cruise"], plan[0]["n_dec"]) == ("trap", 1280, 61440, 1280)
    assert (plan[1]["kind"], plan[1]["n_acc"], plan[1]["n_dec"]) == ("tri", 667, 333)
    assert plan[1]["v_peak"] == pytest.approx(9237.604307034011) and plan[1]["t"] == pytest.approx(0.21650635094610965)


@pytest.mark.parametrize("name", sorted(CASES))
def test_case_consistent_and_reproducible(name: str) -> None:
    c = CASES[name]
    assert c["n_periods"] == len(c["periods"]) and c["sum_ticks"] == sum(c["periods"])
    assert all(x > 0 for x in c["periods"])
    assert c["tolerance"]["sum_ticks"] == max(1, math.ceil(c["n_periods"] / 1000))


def test_controlled_stop_from_cruise_mirrors_the_ramp() -> None:
    """A stop from cruise at a_stop = a decelerates over v²/2a = 1280 steps, mirroring the acceleration."""
    c = CASES["tvm_stop_in_cruise"]["periods"]
    t = CASES["tvm_trapezoid"]["periods"]
    assert len(c) == 30000 + 1280 and c[-3:] == t[-3:] and c[:30000] == t[:30000]


def test_stop_never_faster_and_ends_slow() -> None:
    for name in ("default_stop_in_accel", "default_stop_near_end", "jog_speed_changes"):
        c = CASES[name]
        k = c["events"][-1]["after_step"]
        tail = c["periods"][k:]
        assert tail and all(b >= a - 1 for a, b in zip(tail, tail[1:])), name   # monotone (±1 tick carry)


def test_jog_changes_are_continuous() -> None:
    p = CASES["jog_speed_changes"]["periods"]
    for k in (2000, 5000):                              # no step change larger than the ramp allows
        assert abs(p[k] - p[k - 1]) / p[k - 1] < 0.05      # one ramp step (here <= 1.6 %), no jump
    assert p[4999] == pytest.approx(90e6 / 4000, abs=1)   # 5 mm/s at 800 steps/mm = 4000 steps/s cruise
    assert p[6999] == pytest.approx(90e6 / 800, abs=1)    # 1 mm/s = 800 steps/s


@pytest.mark.parametrize("p_ticks,spm,a_stop,path", [
    (180_001, 800.0, 1_000_000, "CLEAN"), (180_000, 800.0, 1_000_000, "STRETCH"), (90_000, 800.0, 1_000_000, "ISR"),
    (90_001, 800.0, 1_000_000, "STRETCH"), (225_000, 100.0, 10_000, "STRETCH")])
def test_ctrl_stop_path_rule(p_ticks: int, spm: float, a_stop: int, path: str) -> None:
    """ICD §6.5 (D-30): CLEAN only when P > 2 ms AND d <= 1 step; ISR at P <= 1 ms; else STRETCH."""
    row = next(r for r in MV["ctrl_stop_paths"] if r["p_ticks"] == p_ticks and r["steps_per_mm"] == spm
               and r["a_stop_um_s2"] == a_stop)
    assert row["path"] == path
    assert rm.ctrl_stop_path(p_ticks, F, rm.steps_per_s(a_stop, spm))[0] == path
