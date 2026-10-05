"""Validator E - MOVE_UNTIL_LOAD (FW-MOT-006, pulled forward to M3 by D-44) on A's FW in the twin.

TC-FW-MOT-006-01 (plan §3.3) and the execution rules (a)-(e) of ICD v0.7.3 §5.4 / §6.2, independent of A's and the
Integrator's tests. Sample values are scripted (`afe raw_script`: the next HX711 samples verbatim, one per 12.5 ms
at 80 SPS), so every threshold crossing is placed on a known sample; step periods are reconstructed from the PUL
edge log and compared with the shared motion vectors (`mul_to_bound_5mm`, `mul_stop_in_cruise`, and the prefix
lists `immediate_stops` / `threshold_in_controlled_stop`) through the validator's own oracle (ramp_ref).

Verifies: FW-MOT-006, SAF-FW-002 (load-path stop of the threshold), SAF-FW-003 (rule b), SAF-FW-008 (rule a),
          SAF-FW-012 / FW-AFE stale (fallback frames are not samples), IF-012 (MOVE_UNTIL_LOAD refusals)
TC: TC-FW-MOT-006-01
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import ramp_ref as rr
import ref_codec as rc
import vhelp_m2 as m
from vhelp import V

VEC = Path(__file__).resolve().parents[3] / "00_System" / "tools" / "vectors"
MD = {n: i for i, n in enumerate(rc.MOVE_DONE_REASON)}
SC = rc.STOP_CAUSE
LOW = 50_000                          # unloaded model raw (load offset)
SAMPLE_US = 12_500


def _mv() -> dict:
    return json.loads((VEC / "motion_vectors.json").read_text(encoding="utf-8"))


def _case(name: str) -> dict:
    return next(c for c in _mv()["cases"] if c["name"] == name)


def mul(v: V, bound_um: int, raw_stop: int, cmp: int = 0, v_um_s: int = 2_000, a_um_s2: int = 100_000) -> dict:
    return v.cmd("MOVE_UNTIL_LOAD", {"bound_um": bound_um, "v_um_s": v_um_s, "a_um_s2": a_um_s2,
                                     "raw_stop": raw_stop, "cmp": cmp})


def script(v: V, raws: list[int]) -> None:
    v.tw.act("afe", raw_script=raws)


def pul_periods(v: V, t0: float) -> list[int]:
    start = m.seam(v, "hal_step_start", t0)
    assert start, "step output never started"
    return m.periods_from_edges(m.edges(v, "PUL", t0), start[0]["t_us"])


def conv_times(v: V, t0: float) -> list[dict]:
    return [c for c in v.tw.act("query", what="conversions", since_us=t0)["conversions"] if c["delivered"]]


@pytest.fixture
def ready_mul(v):
    m.need(v, "MOTION", "HOMING", "AFE", "MOVE_UNTIL_LOAD")
    m.ready(v, x_um=20_000)
    v.ok("STREAM_START")
    v.ok("SET_VALID", {"valid": 1})
    return v


# ----------------------------------------------------------------------------- bound / threshold / periods
def test_bound_reached_exactly_with_vector_periods(ready_mul):
    """Threshold never reached -> planned stop exactly at the bound, MOVE_DONE BOUND; periods = mul_to_bound_5mm."""
    v = ready_mul
    t0, n0 = v.tw.now_us, m.n_events(v)
    v.ok("MOVE_UNTIL_LOAD", {"bound_um": 25_000, "v_um_s": 2_000, "a_um_s2": 100_000, "raw_stop": 8_000_000, "cmp": 0})
    md = m.wait_event(v, "MOVE_DONE", n0, 10_000)
    assert md["arg"] == MD["BOUND"] and md["value"] == 25_000 and md["value2"] == 20_000, md
    ok, msg = rr.check_periods(pul_periods(v, t0), _case("mul_to_bound_5mm")["periods"])
    assert ok, msg
    assert rr.case_periods(_case("mul_to_bound_5mm")) == _case("mul_to_bound_5mm")["periods"]   # oracle = vector


@pytest.mark.parametrize("k", [6, 15, 40])
def test_threshold_cmp0_immediate_clean_stop(ready_mul, k):
    """cmp 0: the first sample with raw ≥ raw_stop stops at once (load path), MOVE_DONE LOAD_THRESHOLD; the
    emitted periods are a prefix of the base case (= `immediate_stops` rule); rule (d): no STOPPED, VALID and HOMED
    kept, no POS_UNCERTAIN, no VALID_CLEARED; equality counts as beyond."""
    v = ready_mul
    R = 400_000
    t0, n0 = v.tw.now_us, m.n_events(v)
    script(v, [LOW] * k + [R] * 4)                      # sample k (0-based from now) = raw_stop exactly
    v.ok("MOVE_UNTIL_LOAD", {"bound_um": 25_000, "v_um_s": 2_000, "a_um_s2": 100_000, "raw_stop": R, "cmp": 0})
    md = m.wait_event(v, "MOVE_DONE", n0, 10_000)
    assert md["arg"] == MD["LOAD_THRESHOLD"], md
    ev = m.events_since(v, n0)
    assert not [e for e in ev if e["code"] in ("STOPPED", "VALID_CLEARED", "FAULT_SET")], ev
    st = v.status()
    assert {"HOMED", "VALID"} <= set(st["flags"]) and "POS_UNCERTAIN" not in st["status"], st
    p = pul_periods(v, t0)
    base = _case("mul_to_bound_5mm")["periods"]
    assert 0 < len(p) < len(base)
    ok, msg = rr.check_periods(p, base[:len(p)])
    assert ok, msg
    assert md["value2"] - 16_000 == m.rising_count(v, t0)            # every pulse counted (CLEAN halt)
    dec = [c for c in conv_times(v, t0) if c["raw"] == R][0]["t_us"]
    late = [e for e in m.edges(v, "PUL", t0) if e["level"] == 1 and e["t_us"] > dec + 200]
    assert not late, f"PUL edge {late[0]['t_us'] - dec:.1f} µs after the deciding sample (load path ≤ 200 µs)"


def test_immediate_stops_vector_rows_are_prefixes():
    """Vector sanity (validator oracle): every `immediate_stops` / `threshold_in_controlled_stop` row names an
    existing base case and a step inside it; the expected periods are the base case's first `after_step` periods."""
    mv = _mv()
    names = {c["name"]: c for c in mv["cases"]}
    for row in mv["immediate_stops"] + mv["threshold_in_controlled_stop"]:
        base = names[row["base_case"]]
        assert 0 <= row["after_step"] < base["n_periods"], row
        assert rr.case_periods(base) == base["periods"]
        assert row["reason"] in ("LOAD_THRESHOLD", "STOPPED")
    assert all(r["reason"] == "STOPPED" for r in mv["threshold_in_controlled_stop"])        # rule (b)


def test_threshold_cmp1_unloading(ready_mul):
    """cmp 1 (raw ≤ raw_stop) moving −x while the raw falls."""
    v = ready_mul
    script(v, [300_000] * 3)
    v.advance(40)                                      # last sample 300 000 (not beyond 200 000)
    t0, n0 = v.tw.now_us, m.n_events(v)
    script(v, [300_000] * 8 + [190_000] * 4)
    v.ok("MOVE_UNTIL_LOAD", {"bound_um": 15_000, "v_um_s": 2_000, "a_um_s2": 100_000, "raw_stop": 200_000, "cmp": 1})
    md = m.wait_event(v, "MOVE_DONE", n0, 10_000)
    assert md["arg"] == MD["LOAD_THRESHOLD"] and md["value"] < 20_000, md
    assert m.rising_count(v, t0) > 0


@pytest.mark.parametrize("cmp, last", [(0, 400_000), (0, 400_001), (1, 400_000), (1, 399_999)])
def test_already_beyond_no_pulse(ready_mul, cmp, last):
    """Already beyond (equality included) before the first pulse -> MOVE_DONE LOAD_THRESHOLD, no pulse."""
    v = ready_mul
    script(v, [last] * 3)
    v.advance(40)
    t0, n0 = v.tw.now_us, m.n_events(v)
    v.ok("MOVE_UNTIL_LOAD", {"bound_um": 25_000 if cmp == 0 else 15_000, "v_um_s": 2_000, "a_um_s2": 0,
                             "raw_stop": 400_000, "cmp": cmp})
    md = m.wait_event(v, "MOVE_DONE", n0, 50)
    assert md["arg"] == MD["LOAD_THRESHOLD"] and md["value"] == 20_000, md
    v.advance(20)
    assert m.rising_count(v, t0) == 0


# ----------------------------------------------------------------------------- refusals
def test_refusals(v):
    m.need(v, "MOTION", "HOMING", "AFE", "MOVE_UNTIL_LOAD")
    m.enable(v)
    r = mul(v, 10_000, 400_000)
    assert r["status"] == "E_STATE" and "NOT_HOMED" in rc.bits_to_names(r["detail"], rc.BLOCK), r
    m.ready(v, x_um=20_000)
    vmax = int(v.get("motion.v_max_load_um_s"))
    assert (r := mul(v, 25_000, 400_000, v_um_s=vmax + 1))["status"] == "E_RANGE" and r["detail"] == 4, r
    assert (r := mul(v, 25_000, 400_000, v_um_s=0))["status"] == "E_RANGE" and r["detail"] == 4, r
    assert (r := mul(v, 20_000, 400_000))["status"] == "E_RANGE" and r["detail"] == 0, r        # bound == x
    assert (r := mul(v, 400_000, 400_000))["status"] == "E_RANGE" and r["detail"] == 0, r       # outside soft
    assert (r := mul(v, 25_000, 8_388_608))["status"] == "E_RANGE" and r["detail"] == 12, r
    assert (r := mul(v, 25_000, 400_000, cmp=2))["status"] == "E_RANGE" and r["detail"] == 16, r
    v.ok("PAUSE")
    r = mul(v, 25_000, 400_000)
    assert r["status"] == "E_STATE" and "PAUSED" in rc.bits_to_names(r["detail"], rc.BLOCK), r
    v.ok("HALT_CLEAR")
    n0 = m.n_events(v)
    v.ok("MOVE_UNTIL_LOAD", {"bound_um": 40_000, "v_um_s": 2_000, "a_um_s2": 0, "raw_stop": 8_000_000, "cmp": 0})
    v.advance(50)
    r = mul(v, 30_000, 400_000)
    assert (r["status"], r["detail"]) == ("E_BUSY", 1), r
    v.ok("STOP", {"mode": 0})
    m.wait_event(v, "MOVE_DONE", n0, 1000)


# ----------------------------------------------------------------------------- AFE stale
def test_fallback_frames_are_not_samples(ready_mul):
    """AFE stall while moving -> STOPPED(AFE_FAULT), never LOAD_THRESHOLD although a fallback raw
    (AFE_NO_DATA = −2^31) would satisfy cmp 1 against any raw_stop."""
    v = ready_mul
    n0 = m.n_events(v)
    v.ok("MOVE_UNTIL_LOAD", {"bound_um": 15_000, "v_um_s": 1_000, "a_um_s2": 0, "raw_stop": -8_000_000, "cmp": 1})
    v.advance(100)
    v.tw.act("afe", stall=True)
    md = m.wait_event(v, "MOVE_DONE", n0, 2_000)
    v.tw.act("afe", stall=False)
    stp = m.events_since(v, n0, "STOPPED")
    assert md["arg"] == MD["STOPPED"] and stp and stp[0]["arg"] == SC["AFE_FAULT"], (md, stp)


# ----------------------------------------------------------------------------- rule (a)
def test_rule_a_load_limit_wins(ready_mul):
    """(a) a sample that trips the FW load limit and is also beyond raw_stop is the load limit's stop."""
    v = ready_mul
    lim = 600_000
    m.set_ok(v, "safety.load_raw_max", lim)
    n0 = m.n_events(v)
    script(v, [LOW] * 10 + [lim + 1] * 4)
    v.ok("MOVE_UNTIL_LOAD", {"bound_um": 25_000, "v_um_s": 2_000, "a_um_s2": 0, "raw_stop": lim - 1000, "cmp": 0})
    md = m.wait_event(v, "MOVE_DONE", n0, 10_000)
    fs = m.events_since(v, n0, "FAULT_SET")
    stp = m.events_since(v, n0, "STOPPED")
    assert md["arg"] == MD["STOPPED"], md
    assert fs and fs[0]["arg"] == rc.FAULTS.index("LOAD_LIMIT") and fs[0]["value"] == lim + 1, fs
    assert len(stp) == 1 and stp[0]["arg"] == SC["LOAD_LIMIT"], stp


# ----------------------------------------------------------------------------- rule (b)
def test_rule_b_threshold_cuts_controlled_stop(ready_mul):
    """(b) PAUSE at a_stop 10 mm/s² during the cruise, then a sample beyond raw_stop during the deceleration:
    CLEAN halt, MOVE_DONE STOPPED, exactly one STOPPED (PC_PAUSE); periods = mul_stop_in_cruise oracle with the
    actual stop step, cut short (vector `threshold_in_controlled_stop`)."""
    v = ready_mul
    m.set_ok(v, "motion.a_stop_um_s2", 10_000)
    R = 400_000
    t0, n0 = v.tw.now_us, m.n_events(v)
    script(v, [LOW] * 120)
    v.ok("MOVE_UNTIL_LOAD", {"bound_um": 25_000, "v_um_s": 2_000, "a_um_s2": 100_000, "raw_stop": R, "cmp": 0})
    m.run(v, 1_300, step_ms=0.5)                       # ≈ step 2 000 (cruise 1 600 steps/s)
    v.ok("PAUSE")
    v.advance(20)
    script(v, [LOW] * 4 + [R] * 4)                     # beyond ≈ 50…62 ms into the 200 ms deceleration
    md = m.wait_event(v, "MOVE_DONE", n0, 5_000)
    stp = m.events_since(v, n0, "STOPPED")
    assert md["arg"] == MD["STOPPED"] and len(stp) == 1 and stp[0]["arg"] == SC["PC_PAUSE"], (md, stp)
    assert "POS_UNCERTAIN" not in v.status()["status"]
    p = pul_periods(v, t0)
    base = _case("mul_stop_in_cruise")
    fits = []
    for k in range(1_900, 2_200):                      # the step at which the controlled stop took effect
        ref = rr.case_periods(dict(base, events=[{"after_step": k, "event": "controlled_stop"}]))
        if len(p) >= len(ref) or k >= len(p):
            continue
        q = list(ref[:len(p)])
        q[k] = ref[k - 1]          # ISR path: period k was already preloaded at the cruise value (ICD §6.5 / OI-ICD-07)
        if rr.check_periods(p, q)[0]:
            fits.append((k, len(ref)))
    assert fits, "periods are no prefix of a controlled stop from any step 1 900…2 200"
    k, full = fits[0]
    assert len(p) < full - 20, f"deceleration not cut short: {len(p)} of {full} steps"


# ----------------------------------------------------------------------------- rule (c)
def test_rule_c_bound_within_one_step(v):
    """(c) bound ≠ x but rounding to the current step (10 µm steps at 100 steps/mm) completes at once without a
    pulse: BOUND if the last sample is not beyond, LOAD_THRESHOLD if it is."""
    m.need(v, "MOTION", "HOMING", "AFE", "MOVE_UNTIL_LOAD")
    m.set_ok(v, "motion.steps_per_mm", 100.0)
    m.ready(v, x_um=20_000)
    v.ok("STREAM_START")
    for last, want in ((LOW, "BOUND"), (400_000, "LOAD_THRESHOLD")):
        script(v, [last] * 3)
        v.advance(40)
        t0, n0 = v.tw.now_us, m.n_events(v)
        r = mul(v, 20_004, 400_000)
        assert r["status"] == "OK", r
        md = m.wait_event(v, "MOVE_DONE", n0, 50)
        assert md["arg"] == MD[want], (want, md)
        v.advance(20)
        assert m.rising_count(v, t0) == 0


# ----------------------------------------------------------------------------- rule (e)
def test_rule_e_parked_by_sniffed_halt(ready_mul):
    """(e) MOVE_UNTIL_LOAD and HALT in one burst: the start is discarded like a MOVE_ABS — MOVE_DONE STOPPED,
    STOPPED(PC_HALT), no pulse after the HALT frame."""
    v = ready_mul
    t0, n0 = v.tw.now_us, m.n_events(v)
    v.link.send("MOVE_UNTIL_LOAD", {"bound_um": 25_000, "v_um_s": 2_000, "a_um_s2": 0, "raw_stop": 8_000_000, "cmp": 0})
    v.link.send("HALT")
    md = m.wait_event(v, "MOVE_DONE", n0, 1_000)
    stp = m.events_since(v, n0, "STOPPED")
    assert md["arg"] == MD["STOPPED"] and stp and stp[0]["arg"] == SC["PC_HALT"], (md, stp)
    halt_end = [w for w in v.wire("rx") if w["type"] == rc.CMD["HALT"] and w["first_us"] >= t0][0]["last_us"]
    assert not [e for e in m.edges(v, "PUL", t0) if e["level"] == 1 and e["t_us"] > halt_end]
    v.ok("HALT_CLEAR")
