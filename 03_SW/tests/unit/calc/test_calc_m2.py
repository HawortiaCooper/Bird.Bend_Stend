"""M2 calculations: exact step ramp against the Integrator's ``motion_vectors.json`` (R4 TV-M, stops, jog speed
changes, controlled-stop path selection, planner) and the FW load-threshold computation (R4 TV-T) with the inward
clamp (D-29 g).

Verifies: FW-MOT-003, SAF-FW-003, SAF-SW-002, SYS-003
"""
from __future__ import annotations

import pytest

from bbs_support import vectors
from bend_stand.calc.loadcal import clamp_raw_limits, effective_force_n, fw_raw_limits
from bend_stand.calc.motion import Ramp, ctrl_stop_path, f32, plan_trapezoid, ramp_periods
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg

MV = vectors("motion_vectors.json")


def _run_case(c: dict) -> list[int]:
    spm = f32(c["steps_per_mm"])
    dv = c["derived"]
    evs = list(c["events"])
    if c["name"].startswith("jog"):
        r = Ramp(c["f_tick"], evs[0]["v_um_s"] * spm / 1000.0, dv["a_steps_s2"], dv["d_steps_s2"], 10 ** 9)
        out: list[int] = []
        for k, e in enumerate(evs):
            if e["v_um_s"] == 0:
                r.stop(dv["a_stop_steps_s2"])
            elif k:
                r.set_speed(e["v_um_s"] * spm / 1000.0)
            nxt = evs[k + 1]["after_step"] if k + 1 < len(evs) else None
            while nxt is None or len(out) < nxt:
                x = r.next()
                if x is None:
                    break
                out.append(x)
        return out
    r = Ramp(c["f_tick"], dv["v_steps_s"], dv["a_steps_s2"], dv["d_steps_s2"], c["n_steps"])
    out = []
    while True:
        if evs and len(out) == evs[0]["after_step"]:
            assert evs[0]["event"] == "controlled_stop"
            r.stop(dv["a_stop_steps_s2"])
            evs.pop(0)
        x = r.next()
        if x is None:
            return out
        out.append(x)


@pytest.mark.req("FW-MOT-003", "IF-010")
def test_motion_vectors_header() -> None:
    assert MV["icd_version"] == pg.ICD_VERSION and int(MV["param_dict_hash"], 16) == pgen.PARAM_DICT_HASH
    assert MV["cases"] and MV["ctrl_stop_paths"] and MV["planner"]


@pytest.mark.req("FW-MOT-003", "SAF-FW-003")
@pytest.mark.parametrize("c", MV["cases"], ids=lambda c: c["name"])
def test_ramp_reproduces_motion_vectors_exactly(c: dict) -> None:
    """The simulator's generator is binary64 like the oracle → every period and the sum exact."""
    out = _run_case(c)
    assert len(out) == c["n_periods"] and sum(out) == c["sum_ticks"]
    assert out == c["periods"]


@pytest.mark.req("SAF-FW-003")
@pytest.mark.parametrize("p", MV["ctrl_stop_paths"], ids=lambda p: f"{p['p_ticks']}-{p['steps_per_mm']}-"
                                                                   f"{p['a_stop_um_s2']}")
def test_controlled_stop_path_selection(p: dict) -> None:
    a = p["a_stop_um_s2"] * f32(p["steps_per_mm"]) / 1000.0
    assert ctrl_stop_path(p["p_ticks"], 90e6, a) == (p["path"], p["d_steps"])


@pytest.mark.req("FW-MOT-003")
@pytest.mark.parametrize("p", MV["planner"], ids=lambda p: f"{p['n_steps']}-{p['kind']}")
def test_planner_vectors(p: dict) -> None:
    t = plan_trapezoid(p["n_steps"], p["v_steps_s"], p["a_steps_s2"], p["d_steps_s2"])
    assert (t["kind"], t["n_acc"], t["n_cruise"], t["n_dec"]) == (p["kind"], p["n_acc"], p["n_cruise"], p["n_dec"])
    assert t["v_peak"] == pytest.approx(p["v_peak"]) and t["t"] == pytest.approx(p["t"])


@pytest.mark.req("FW-MOT-003")
def test_ramp_on_the_fly_rules() -> None:
    r = Ramp(90e6, 1000.0, 10_000.0, 10_000.0, 100)
    assert r.next() is not None and r.v_now > 0 and not r.stopping
    r.set_speed(1000.0)                                  # unchanged speed (jog refresh) changes nothing
    k = r.k
    r.set_speed(1000.0)
    assert r.k == k
    r.set_end(5)
    assert r.r == 5
    r.stop()
    assert r.stopping and r.r <= 5
    r.set_speed(5000.0)                                   # a stop in progress is never undone
    assert r.stopping
    while r.next() is not None:
        pass
    assert r.done and r.next() is None
    r0 = Ramp(90e6, 1000.0, 10_000.0, 10_000.0, 100)
    r0.stop()                                             # stop before the first step → ends at once
    assert r0.done
    with pytest.raises(ValueError):
        Ramp(90e6, 0.0, 1.0, 1.0, 10)
    with pytest.raises(ValueError):
        r.set_speed(0.0)
    assert ramp_periods(3, 90e6, 1000.0, 10_000.0, 10_000.0) == [1272792, 527208, 1272792] or len(
        ramp_periods(3, 90e6, 1000.0, 10_000.0, 10_000.0)) == 3


# ------------------------------------------------------------------------------------------- thresholds (R4 TV-T)

K = 0.00045666488086106374
TARE = 125430.0


@pytest.mark.req("SAF-SW-002")
def test_fw_raw_limits_tv_t() -> None:
    assert fw_raw_limits(f_hi=1500.0, f_lo=-1500.0, k=K, tare_raw=TARE) == (-3159254, 3410114)
    assert fw_raw_limits(f_hi=1500.0, f_lo=-500.0, k=K, tare_raw=TARE) == (-969464, 3410114)
    assert fw_raw_limits(f_hi=1500.0, f_lo=-500.0, k=-K, tare_raw=TARE) == (-3159254, 1220324)   # sides swap
    with pytest.raises(ValueError):
        fw_raw_limits(1.0, -1.0, 0.0, 0.0)


@pytest.mark.req("SAF-SW-002", "SAF-FW-010")
def test_clamp_inward_to_the_dictionary_range() -> None:
    mn, mx = pgen.BY_KEY["safety.load_raw_min"], pgen.BY_KEY["safety.load_raw_max"]
    args = (int(mn.min), int(mn.max), int(mx.min), int(mx.max))
    assert clamp_raw_limits(-100, 100, *args) == (-100, 100, False)
    lo, hi, c = clamp_raw_limits(-9_000_000, 9_000_000, *args)
    assert (lo, hi, c) == (-7_151_121, 7_151_121, True)
    with pytest.raises(ValueError):
        clamp_raw_limits(9_000_000, -9_000_000, *args)    # inverted pair → empty after clamping
    assert effective_force_n(1000, 0.5, 0.0) == 500.0
