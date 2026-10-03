"""M1 calculations: units, rounding, µm↔steps (units_vectors.json), time base, frame gaps, hard rules H1–H5.

Verifies: SYS-003, IF-006, IF-007, SW-CFG-003, FW-MOT-009 (cap formula), SW-ACQ-004
"""
from __future__ import annotations

import random
import struct

import pytest

from bend_stand.calc import motion, paramrules, rounding, timebase, units
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bbs_support import vectors

G0 = 9.80665
UNITS = vectors("units_vectors.json")
CHECK = vectors("check_vectors.json")


# ---------------------------------------------------------------------------------------------- R4 TV-U

@pytest.mark.req("SYS-003")
def test_tv_u_units() -> None:
    assert units.kgf_to_n(10.0) == pytest.approx(98.0665, rel=1e-12)
    assert units.n_to_kgf(1.0) == pytest.approx(0.10197162129779283, rel=1e-12)
    assert units.FS_N == pytest.approx(1961.33, rel=1e-12)
    assert units.FW_LIMIT_MAX_N == pytest.approx(2157.463, rel=1e-12)
    assert units.mm_to_um(1.0005) == 1001 and units.mm_to_um(-1.0005) == -1001
    assert units.um_to_mm(1500) == 1.5 and units.mm_s_to_um_s(2.5) == 2500
    assert units.pct_fs_to_n(5.0) == pytest.approx(98.0665)


@pytest.mark.req("SYS-003")
@pytest.mark.parametrize("x, r", [(0.5, 1), (-0.5, -1), (1.5, 2), (2.5, 3), (-2.5, -3), (0.49999999999999994, 0),
                                  (1.4999999999999998, 1), (7, 7), (-0.0, 0), (1e15 + 0.5, 1000000000000001)])
def test_round_half_away(x: float, r: int) -> None:
    assert rounding.round_half_away(x) == r


@pytest.mark.req("SYS-003")
def test_round_rejects_nan() -> None:
    with pytest.raises(ValueError):
        rounding.round_half_away(float("nan"))


# ---------------------------------------------------------------------------------------------- units_vectors

def _spm(v: dict) -> float:
    spm = struct.unpack("<f", int(v["spm_f32_hex"], 16).to_bytes(4, "little"))[0]
    assert spm == motion.f32(v["spm"])
    return spm


@pytest.mark.req("SYS-003")
def test_units_vectors_header() -> None:
    assert UNITS["icd_version"] == pg.ICD_VERSION
    assert int(UNITS["param_dict_hash"], 16) == pgen.PARAM_DICT_HASH


@pytest.mark.req("SYS-003")
@pytest.mark.parametrize("v", UNITS["um_to_steps"], ids=lambda v: f"{v['spm']}-{v['um']}")
def test_um_to_steps_exact(v: dict) -> None:
    assert motion.um_to_steps(v["um"], _spm(v)) == v["steps"]


@pytest.mark.req("SYS-003")
@pytest.mark.parametrize("v", UNITS["steps_to_um"], ids=lambda v: f"{v['spm']}-{v['steps']}")
def test_steps_to_um_exact(v: dict) -> None:
    assert motion.steps_to_um(v["steps"], _spm(v)) == v["um"]


@pytest.mark.req("FW-MOT-009", "SYS-003")
@pytest.mark.parametrize("v", UNITS["rate_cap"], ids=lambda v: f"{v['spm']}-{v['max_step_rate_hz']}")
def test_rate_cap_exact(v: dict) -> None:
    assert motion.rate_cap_um_s(v["max_step_rate_hz"], _spm(v)) == v["rate_cap_um_s"]


@pytest.mark.req("SYS-003")
def test_tv_tc_um_steps() -> None:
    spm = 636.0778443113772
    assert [motion.um_to_steps(u, spm) for u in (12345, -500, 100)] == [7852, -318, 64]
    assert [motion.steps_to_um(s, spm) for s in (7852, -318, 64)] == [12344, -500, 101]


@pytest.mark.req("FW-MOT-009")
def test_v_limit_and_planner() -> None:
    assert motion.v_limit_um_s(30000, 50000, 800.0) == 30000
    assert motion.v_limit_um_s(30000, 10000, 800.0) == 12500
    assert motion.v_limit_um_s(30000, 50000, 800.0, v_unhomed_um_s=2000) == 2000
    t = motion.plan_trapezoid(64000, 12800.0, 64000.0, 64000.0)
    assert (t["kind"], t["n_acc"], t["n_cruise"], t["n_dec"]) == ("trap", 1280, 61440, 1280)
    assert t["t"] == pytest.approx(5.2)
    t = motion.plan_trapezoid(1000, 12800.0, 64000.0, 128000.0)
    assert (t["kind"], t["n_acc"], t["n_dec"]) == ("tri", 667, 333)
    assert t["v_peak"] == pytest.approx(9237.604307034011) and t["t"] == pytest.approx(0.21650635094610965)
    assert motion.plan_trapezoid(0, 1.0, 1.0, 1.0)["t"] == 0.0
    with pytest.raises(ValueError):
        motion.plan_trapezoid(5, 0.0, 1.0, 1.0)
    assert motion.move_duration_s(64000 * 1000 / 640, 20000, 100000) == pytest.approx(5.2)
    assert motion.move_duration_s(0, 1, 1) == 0.0
    assert motion.move_duration_s(1000, 20000, 100000) == pytest.approx(2 * (1000 / 100000) ** 0.5)
    assert motion.stop_distance_um(20000, 100000) == pytest.approx(2000.0)


# ---------------------------------------------------------------------------------------------- time base

@pytest.mark.req("IF-006", "IF-007")
def test_tv_l_unwrap() -> None:
    assert timebase.unwrap_us([4294967000, 4294967290, 200, 12700]) == \
        [4294967000, 4294967290, 4294967496, 4294979996]
    assert 2**32 / 1e6 / 60 == pytest.approx(71.58278826666667)


@pytest.mark.req("IF-006")
def test_backward_step_is_new_epoch() -> None:
    s = timebase.unwrap_step(5_000_000, 5_000_000, 1_000)
    assert s.new_epoch and s.t_u == 1_000
    assert timebase.unwrap_step(None, None, 77) == timebase.UnwrapStep(77, False)
    assert timebase.unwrap_us([10, 20, 5, 15]) == [10, 20, 5, 15]


@pytest.mark.req("IF-006")
def test_plausible_wraps() -> None:
    assert timebase.plausible_wraps(2 * 2**32 + 1000, 1000) == 2
    assert timebase.plausible_wraps(500, 1000) == 0


@pytest.mark.req("IF-007", "SW-ACQ-004")
def test_frame_gaps() -> None:
    steps = timebase.frame_gaps([10, 11, 14, 14, 15, 3, 4, 0xFFFE, 0, 1], prev_seq=None)
    assert [s.kind for s in steps] == ["first", "next", "lost", "dup", "next", "anomaly", "next", "anomaly",
                                       "lost", "next"]
    assert steps[2].lost == 2 and steps[8].lost == 1          # 0xFFFE -> 0 across the u16 wrap: 1 lost
    assert sum(s.lost for s in steps) == 3


@pytest.mark.req("IF-007")
def test_missed_conversions() -> None:
    assert timebase.missed_conversions(12500, 12500) == 0
    assert timebase.missed_conversions(25000, 12500) == 1
    assert timebase.missed_conversions(50000, 12500) == 3
    assert timebase.missed_conversions(100, 0) == 0


# ---------------------------------------------------------------------------------------------- hard rules

def _state_params(v: dict) -> dict:
    vals = {p.key: p.default for p in pgen.PARAMS}
    vals.update(CHECK["state_defaults"]["params"])
    vals.update(v["state"].get("params", {}))
    return vals


def _decode_entry(payload_hex: str) -> tuple[str, object]:
    b = bytes.fromhex(payload_hex)
    pid, _typ = struct.unpack_from("<HB", b)
    meta = pgen.BY_ID[pid]
    return meta.key, meta.unpack(b[3:7])


_CFG = [v for v in CHECK["vectors"] if v["expect"]["status"] == "E_CONFIG"]
_SET_OK = [v for v in CHECK["vectors"] if v["request"]["type_name"] == "SET_PARAM"
           and v["expect"]["status"] == "OK"]


@pytest.mark.req("SW-CFG-003", "IF-010")
@pytest.mark.parametrize("v", _CFG, ids=lambda v: v["name"])
def test_every_e_config_vector_reproduced(v: dict) -> None:
    key, value = _decode_entry(v["request"]["payload_hex"])
    viol = paramrules.set_violation(_state_params(v), key, value)
    assert viol is not None and viol.other_id == v["expect"]["detail"]


@pytest.mark.req("SW-CFG-003")
@pytest.mark.parametrize("v", _SET_OK, ids=lambda v: v["name"])
def test_accepted_set_param_vectors_violate_no_rule(v: dict) -> None:
    key, value = _decode_entry(v["request"]["payload_hex"])
    assert paramrules.set_violation(_state_params(v), key, value) is None


@pytest.mark.req("SW-CFG-003")
def test_check_hard_rules_and_defaults() -> None:
    assert paramrules.check_hard_rules({}) == []
    bad = paramrules.check_hard_rules({"afe.rate_sps": 0, "afe.timeout_ms": 100, "motion.v_max_load_um_s": 40000})
    assert [v.rule for v in bad] == ["H4", "H5"]
    assert "rule H4" in bad[0].text
    with pytest.raises(KeyError):
        paramrules.rule_holds("H9", {})


def _random_valid(rng: random.Random) -> dict:
    while True:
        v = {
            "limits.soft_min_um": rng.randint(-50_000, 399_999), "limits.soft_max_um": rng.randint(-49_999, 400_000),
            "safety.load_raw_min": rng.randint(-7_151_121, 7_151_120),
            "safety.load_raw_max": rng.randint(-7_151_120, 7_151_121),
            "motion.max_step_rate_hz": rng.randint(100, 100_000),
            "motion.pulse_high_ns": rng.randint(2500, 100_000), "motion.pulse_low_min_ns": rng.randint(2500, 100_000),
            "motion.v_max_load_um_s": rng.randint(1, 250_000), "motion.v_max_travel_um_s": rng.randint(1, 250_000),
            "afe.timeout_ms": rng.randint(25, 1000), "afe.rate_sps": rng.randint(0, 1),
        }
        if not paramrules.check_hard_rules(v):
            return v


@pytest.mark.req("SW-CFG-003")
def test_write_order_keeps_rules_after_each_set() -> None:
    rng = random.Random(12345)
    for _ in range(2000):
        cur, tgt = _random_valid(rng), _random_valid(rng)
        state = dict(cur)
        order = paramrules.write_order(cur, tgt)
        assert {k for k, _ in order} == {k for k in tgt if tgt[k] != cur[k]}
        for k, val in order:
            assert paramrules.set_violation(state, k, val) is None, (cur, tgt, order)
            state[k] = val
        assert state == tgt


@pytest.mark.req("SW-CFG-003")
def test_write_order_h5_and_unplaceable() -> None:
    cur = {"afe.rate_sps": 1, "afe.timeout_ms": 50}
    assert paramrules.write_order(cur, {"afe.rate_sps": 0, "afe.timeout_ms": 300}) == \
        [("afe.timeout_ms", 300), ("afe.rate_sps", 0)]
    assert paramrules.write_order({"afe.rate_sps": 0, "afe.timeout_ms": 300},
                                  {"afe.rate_sps": 1, "afe.timeout_ms": 50}) == \
        [("afe.rate_sps", 1), ("afe.timeout_ms", 50)]
    # target itself invalid → unplaceable key comes last (writer gets E_CONFIG and reports REJECTED)
    order = paramrules.write_order({}, {"motion.v_max_load_um_s": 40000, "afe.rate_tol_pct": 30})
    assert order == [("afe.rate_tol_pct", 30), ("motion.v_max_load_um_s", 40000)]
