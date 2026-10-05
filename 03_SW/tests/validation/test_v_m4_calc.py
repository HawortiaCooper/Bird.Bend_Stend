"""Level U — M4 calculation and model vectors (SW_test_plan §3.11, §4.2 VV-SEQ / VV-GEN / VV-C / VV-SS / VV-PATH,
R4 TV-SS / TV-C / TV-D) on the production ``calc`` and ``core.sequencer`` packages. Expected values: R4 §12 (copied),
the plan's VV vectors, F's oracle ``f_ref`` (ICD §7.6 mask, band / raw stop, trapezoid time) — never the code under
test.

Verifies: SW-REP-002, SW-REP-004, SW-SEQ-001, SW-SEQ-002, SW-SEQ-006, SW-SEQ-007, SW-WIZ-001, SW-WIZ-002, SW-SEQF-001,
SW-SCH-001
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

import ref_codec as rc
from oracle import f_ref

K_TV, TARE_TV = 0.000455274890527817, 125_000.0          # R4 TV-SS uses the TV-LC K and the TV-T tare


def _seq(steps, loops=(), **kw):
    from bend_stand.core.api import Loop, Sequence

    s = Sequence(**kw)
    s.steps = list(steps)
    s.loops = [Loop(*lp) for lp in loops]
    return s


def _st(kind, target=None, **kw):
    from bend_stand.core.api import Step, StepKind
    from bend_stand.core.sequencer.model import new_uid

    return Step(new_uid(), StepKind(kind), target, **kw)


# ============================================================================================ steady state

FRAMES = [(0, 2, 4000, 300000), (12500, 2, 4400, 300030), (25000, 2, 4800, 300060),
          (37500, 0, 5000, 300000), (50000, 0, 5000, 300030), (62500, 1, 5000, 300060),
          (75000, 1, 5000, 300000), (87500, 1, 5000, 300030), (100000, 1, 5000, 300060),
          (112500, 1, 5000, 300090), (125000, 1, 5000, 300030), (137500, 1, 5000, 300060)]


@pytest.mark.req("SW-REP-002")
def test_tc_sw_rep_002_01_tv_ss():
    """R4 TV-SS: N = 7, min / max 300 000 / 300 090, mean 300 047.142857…, std 29.277; the same from the production
    ``window_stats`` (column form, VALID ∧ ¬MOVING ∧ same setpoint) and F's oracle."""
    # Verifies: SW-REP-002
    from bend_stand.calc.steady import steady_state, window_stats

    s = steady_state(FRAMES, pos_um=5000, t_from_us=0, t_to_us=10**9)
    o = f_ref.steady_state(FRAMES, 5000, 0, 10**9)
    assert s.n == o.n == 7 and (s.min, s.max) == (300000, 300090)
    assert s.mean == pytest.approx(300047.14285714284, rel=1e-12) and s.std == pytest.approx(29.277002188455995)
    t = [f[0] for f in FRAMES]
    fl = [f[1] for f in FRAMES]
    sp = [f[2] for f in FRAMES]
    raw = [f[3] for f in FRAMES]
    fn = [K_TV * (r - TARE_TV) for r in raw]
    w = window_stats(t, fl, [0] * len(t), sp, raw, fn, t0_us=0, t1_us=137_500, capture_s=0.1375, rate_sps=80.0)
    assert w.n == 7 and w.raw.mean == pytest.approx(300047.14285714284, rel=1e-12)
    assert w.f.mean == pytest.approx(K_TV * (300047.14285714284 - TARE_TV), rel=1e-12)


@pytest.mark.req("SW-REP-002")
@pytest.mark.parametrize("bit, field", [("OVERRUN", "flags"), ("ESTOP", "flags"), ("HALT", "flags"), ("FAULT", "flags"),
                                        ("PAUSED", "status"), ("POS_UNCERTAIN", "status"), ("NO_AFE_DATA", "status"),
                                        ("LINK_WDG", "status"), ("AFE_SATURATED", "status"),
                                        ("AFE_RATE_MISMATCH", "status")])
def test_tc_sw_rep_002_01_vv_ss_mask_bits(bit, field):
    """VV-SS-01…05: one frame with each excluded bit of the ICD §7.6 mask is dropped; a not-masked bit (e.g. PEND,
    ALM) is not. Oracle = ``f_ref.steady_ok`` written from the ICD sentence."""
    # Verifies: SW-REP-002
    from bend_stand.calc.steady import window_stats

    names = rc.DATA_FLAGS if field == "flags" else rc.DATA_STATUS
    b = 1 << names.index(bit)
    n = 20
    t = [i * 12_500 for i in range(n)]
    fl = [1] * n
    st = [0] * n
    if field == "flags":
        fl[5] |= b
    else:
        st[5] |= b
    rows = [(t[i], fl[i], st[i], 5000, 100 + i, float(i)) for i in range(n)]
    exp = f_ref.window_select(rows, 0, t[-1])
    w = window_stats(t, fl, st, [5000] * n, [r[4] for r in rows], [r[5] for r in rows], t0_us=0, t1_us=t[-1])
    assert w.n == len(exp) == n - 1
    assert w.raw.mean == pytest.approx(sum(r[4] for r in exp) / len(exp))
    st2 = [0] * n
    st2[5] = (1 << rc.DATA_STATUS.index("PEND")) | (1 << rc.DATA_STATUS.index("ALM"))
    assert window_stats(t, [1] * n, st2, [5000] * n, [0] * n, [0.0] * n, t0_us=0, t1_us=t[-1]).n == n


@pytest.mark.req("SW-REP-002")
def test_tc_sw_rep_002_01_incomplete_and_on_target_boundaries():
    """INCOMPLETE if N < 0.8·capture·rate (1 s at 80 SPS: 63 → INCOMPLETE, 64 → not); ON_TARGET inclusive at
    |mean − target| = tol; setpoint change inside the window excluded."""
    # Verifies: SW-REP-002
    from bend_stand.calc.steady import window_stats

    for n, inc in ((63, True), (64, False)):
        t = [i * 12_500 for i in range(n)]
        w = window_stats(t, [1] * n, [0] * n, [0] * n, [0] * n, [10.0] * n, t0_us=0, t1_us=1_000_000, capture_s=1.0,
                         rate_sps=80.0, target_n=12.0, tol_n=2.0)
        assert ("INCOMPLETE" in w.flags) == inc and "ON_TARGET" in w.flags
    t = [i * 12_500 for i in range(10)]
    sp = [5000] * 5 + [5001] * 5
    w = window_stats(t, [1] * 10, [0] * 10, sp, list(range(10)), [0.0] * 10, t0_us=0, t1_us=t[-1])
    assert w.n == 5


# ============================================================================================ load step (TV-C / VV-C)

@pytest.mark.req("SW-SEQ-006")
def test_tc_sw_seq_006_01_tv_c_and_vv_c():
    """R4 TV-C (k_true 50, k_est 40, Kp 0.5, target 200 N, from 3 mm): 3.625 mm / 181.25 N after one step, (3.98022…,
    199.0112…) after four; VV-C-01/02/03: band 3.25 N (v 1 mm/s), raw_stop 771 323 GE (K > 0), −521 323 LE (K < 0),
    band = tol at 0.2 mm/s."""
    # Verifies: SW-SEQ-006
    from bend_stand.calc.trim import approach_band, force_to_raw_stop, trim_step

    x, seq = 3.0, []
    for _ in range(5):
        f = 50.0 * x
        seq.append((x, f))
        x = x + trim_step(f, 200.0, 40.0, kp=0.5, max_step=10.0)
    assert seq[1] == (3.625, 181.25) and seq[4] == pytest.approx((3.980224609375, 199.01123046875))
    assert trim_step(0.0, 200.0, 40.0) == pytest.approx(0.2)              # clamped to 0.2 mm (SW-SEQ-006)
    assert approach_band(2.0, 50.0, 1.0) == pytest.approx(3.25) == f_ref.band(2.0, 50.0, 1.0)
    assert approach_band(2.0, 50.0, 0.2) == pytest.approx(2.0)
    k = 1 / 3285
    f_stop = 200.0 - approach_band(2.0, 50.0, 1.0)
    for kk, exp in ((k, (771_323, "GE")), (-k, (-521_323, "LE"))):
        raw, cmp_ = force_to_raw_stop(f_stop, kk, 125_000.0, +1)
        o = f_ref.raw_stop(200.0, +1, 2.0, 50.0, 1.0, kk, 125_000.0)
        assert (raw, {0: "GE", 1: "LE"}[cmp_]) == exp == o, (raw, cmp_, o)    # ICD mul_cmp: 0 GE, 1 LE


@pytest.mark.req("SW-SEQ-007")
def test_tc_sw_seq_007_05_timeout_vector():
    """SRS SW-SEQ-007 vector: 130 → 290 mm at 2 mm/s, 100 mm/s² → travel time 80.02 s → timeout 1.2·T + 10 s =
    106.02 s (never shorter); hold: 3 × planned + 10 s."""
    # Verifies: SW-SEQ-007
    from bend_stand.calc.trim import hold_timeout_s, move_time_s, step_timeout_s

    assert move_time_s(160.0, 2.0, 100.0) == pytest.approx(f_ref.move_time_s(160.0, 2.0, 100.0)) == \
        pytest.approx(80.02)
    assert step_timeout_s(160.0, 2.0, 100.0) == pytest.approx(1.2 * 80.02 + 10.0)
    assert hold_timeout_s(20.0) == pytest.approx(70.0)


# ============================================================================================ plan / loops

@pytest.mark.req("SW-SEQ-002")
def test_tc_sw_seq_002_01_vv_seq_expansion():
    """VV-SEQ-01: A B C D, loop B..C × 2 inside A..D × 2 → A B C B C D A B C B C D (12) with loop iteration counters
    (outer first); VV-SEQ-02: count 0 → one expansion + infinite flag; VV-SEQ-04: plan durations = trapezoid time."""
    # Verifies: SW-SEQ-002
    from bend_stand.core.sequencer.plan import expand

    a, b, c, d = (_st("travel", v, speed_mm_s=2.0, accel_mm_s2=100.0, label=n) for n, v in
                  (("A", 10.0), ("B", 20.0), ("C", 30.0), ("D", 40.0)))
    p = expand(_seq([a, b, c, d], [(0, 3, 2), (1, 2, 2)], travel_ref="machine"))
    assert [s.label for s in p.steps] == list("ABCBCDABCBCD")
    assert [s.loop_iters for s in p.steps][:6] == [(1,), (1, 1), (1, 1), (1, 2), (1, 2), (1,)]
    assert p.steps[6].loop_iters == (2,) and not p.infinite
    p0 = expand(_seq([a, b], [(0, 1, 0)], travel_ref="machine"))
    assert p0.infinite and len(p0.steps) == 2
    s1 = _st("travel", 10.0, speed_mm_s=2.0, accel_mm_s2=100.0)
    p1 = expand(_seq([s1], travel_ref="machine"))
    st = p1.steps[0]
    assert st.t_reached_s - st.t_cmd_s == pytest.approx(f_ref.move_time_s(10.0 - st.x_start_mm, 2.0, 100.0), rel=1e-6)


@pytest.mark.req("SW-SEQ-002", "SW-SEQ-001")
@pytest.mark.parametrize("loops, code", [([(0, 2, 2), (1, 3, 2)], "overlap"), ([(0, 3, 2), (1, 2, 2), (1, 1, 2)], "nest"),
                                         ([(0, 1, 10_001)], "count")], ids=["overlap", "two_levels", "count_10001"])
def test_tc_sw_seq_002_01_vv_seq_invalid_loops(loops, code):
    """VV-SEQ-03: overlapping loops, two nesting levels, count 10 001 → ERROR issues (start refused)."""
    # Verifies: SW-SEQ-002, SW-SEQ-001
    from bend_stand.core.sequencer.model import validate

    steps = [_st("travel", float(10 * i + 10), speed_mm_s=2.0) for i in range(4)]
    issues = validate(_seq(steps, loops, travel_ref="machine"))
    assert any(str(i.severity) == "ERROR" for i in issues), issues


@pytest.mark.req("SW-SEQ-001")
def test_tc_sw_seq_001_01_field_validation():
    """TC-SW-SEQ-001-01: one ERROR per invalid field — LOAD without tolerance > 0, capture_during_move with capture 0,
    negative speed, TRAVEL without a target; a valid step has none."""
    # Verifies: SW-SEQ-001
    from bend_stand.core.sequencer.model import validate

    bad = [_st("load", 100.0, speed_mm_s=1.0, tol_n=0.0), _st("load", 100.0, speed_mm_s=1.0, capture_during_move=True),
           _st("travel", 10.0, speed_mm_s=-1.0), _st("travel", None)]
    for s in bad:
        assert any(str(i.severity) == "ERROR" and i.step_uid == s.uid for i in validate(_seq([s],
                   travel_ref="machine"))), s
    assert not [i for i in validate(_seq([_st("travel", 10.0, speed_mm_s=2.0)], travel_ref="machine"))
                if str(i.severity) == "ERROR"]


# ============================================================================================ generators

def _targets(block):
    return [s.target for s in block.steps]


@pytest.mark.req("SW-WIZ-001")
def test_tc_sw_wiz_001_01_vv_gen():
    """VV-GEN-01…07 (§10.6 semantics, SWD-P1-12 c): staircase 0→10 inc 2.5 = 0, 2.5, 5, 7.5, 10; up-down mirrors without
    repeating the peak; inc 3 → 0, 3, 6, 9; count 5 → 0, 2.5, 5, 7.5, 10; inc 5 + return_to_zero → 0, 5, 0, 10, 0; cyclic
    lo 1 hi 5 × 3 → steps [1, 5] + loop × 3 (= 1 5 1 5 1 5); invalid increments → ValueError naming the field."""
    # Verifies: SW-WIZ-001
    from bend_stand.core.sequencer.generators import generate
    from bend_stand.core.sequencer.plan import expand

    assert _targets(generate("staircase", start=0, end=10, increment=2.5)) == [0, 2.5, 5, 7.5, 10]
    assert _targets(generate("staircase", start=0, end=10, increment=2.5, up_down=True)) == \
        [0, 2.5, 5, 7.5, 10, 7.5, 5, 2.5, 0]
    assert _targets(generate("staircase", start=0, end=10, increment=3)) == [0, 3, 6, 9]
    assert _targets(generate("staircase", start=0, end=10, by="count", count=5)) == [0, 2.5, 5, 7.5, 10]
    assert _targets(generate("staircase", start=0, end=10, increment=5, return_to_zero=True)) == [0, 5, 0, 10, 0]
    b = generate("cyclic", lo=1, hi=5, cycles=3)
    assert _targets(b) == [1, 5] and [(lp.first, lp.last, lp.count) for lp in b.loops] == [(0, 1, 3)]
    assert [s.x_target_mm for s in expand(_seq(b.steps, [(0, 1, 3)], travel_ref="machine")).steps] == [1, 5] * 3
    for bad in (0, -1, 11):
        with pytest.raises(ValueError, match="increment"):
            generate("staircase", start=0, end=10, increment=bad)
    r = generate("linear_ramp", x0_mm=0, x1_mm=20, speed_mm_s=0.5)
    assert _targets(r) == [0, 20] and r.steps[1].capture_during_move and not r.steps[0].capture_during_move


@pytest.mark.req("SW-WIZ-002", "SW-SEQ-002")
def test_tc_sw_wiz_002_01_insert_block_keeps_loops_and_editability():
    """SW-WIZ-002 (model part): a generated block (cyclic with its loop) inserted after step 1 keeps its loop
    re-indexed to the new rows; the steps stay editable (changing a target changes the plan); replace mode replaces."""
    # Verifies: SW-WIZ-002, SW-SEQ-002
    from bend_stand.core.sequencer.generators import generate
    from bend_stand.core.sequencer.plan import expand

    s = _seq([_st("travel", 1.0, speed_mm_s=2.0), _st("travel", 2.0, speed_mm_s=2.0), _st("travel", 3.0,
              speed_mm_s=2.0)], travel_ref="machine")
    s.insert_block(generate("cyclic", lo=10, hi=20, cycles=2), mode="insert", index=1)
    assert [st.target for st in s.steps] == [1.0, 10, 20, 2.0, 3.0]
    assert [(lp.first, lp.last, lp.count) for lp in s.loops] == [(1, 2, 2)]
    s.steps[1].target = 11.0
    assert [p.x_target_mm for p in expand(s).steps][:3] == [1.0, 11.0, 20.0]
    s.insert_block(generate("hold", target=5.0, duration_s=3.0), mode="replace")
    assert [st.target for st in s.steps] == [5.0] and not s.loops


# ============================================================================================ files

@pytest.mark.req("SW-SEQF-001")
def test_tc_sw_seqf_001_01_round_trip_and_corrupt_files(tmp_path):
    """Round trip of a sequence with every field and a nested loop is identical (file bytes and model); truncated /
    wrong schema / newer version / overlapping loops → FileFormatError and the caller's sequence is untouched."""
    # Verifies: SW-SEQF-001
    from bend_stand.core.errors import FileFormatError
    from bend_stand.core.sequencer import seqfile

    s = _seq([_st("travel", 10.0, speed_mm_s=2.0, accel_mm_s2=50.0, settle_s=1.0, capture_s=2.0, step_time_s=5.0,
                  label="t"),
              _st("load", 100.0, speed_mm_s=1.0, tol_n=1.5, settle_s=0.5, capture_s=1.0),
              _st("hold", None, capture_s=3.0, step_time_s=4.0), _st("mark", None, wait_operator=True, label="m"),
              _st("tare", None), _st("home", None)], [(0, 3, 2), (1, 2, 3)], name="rt", travel_ref="test",
             k_est_n_mm=42.0, pull_dir=-1)
    p1, p2 = tmp_path / "a.bbseq.json", tmp_path / "b.bbseq.json"
    seqfile.save(str(p1), s)
    s2, _warn = seqfile.load(str(p1))
    seqfile.save(str(p2), s2)
    assert p1.read_bytes() == p2.read_bytes()
    assert [(x.uid, x.kind, x.target, x.tol_n) for x in s2.steps] == [(x.uid, x.kind, x.target, x.tol_n) for x in s.steps]
    assert (s2.k_est_n_mm, s2.pull_dir, s2.travel_ref) == (42.0, -1, "test")
    doc = json.loads(p1.read_text(encoding="utf-8"))
    bad = {"truncated": p1.read_text(encoding="utf-8")[:-40],
           "schema": json.dumps({**doc, "schema": "other"}),
           "newer": json.dumps({**doc, "schema_version": 99}),
           "bad_step": json.dumps({**doc, "steps": [{**doc["steps"][0], "kind": "teleport"}]})}
    current = s.copy()
    for name, text in bad.items():
        f = tmp_path / f"{name}.bbseq.json"
        f.write_text(text, encoding="utf-8")
        with pytest.raises(FileFormatError):
            seqfile.load(str(f))
    assert [x.uid for x in current.steps] == [x.uid for x in s.steps]
    # a structurally valid file whose loops overlap loads, but the sequence is invalid (ERROR, start refused)
    from bend_stand.core.sequencer.model import validate

    f = tmp_path / "overlap.bbseq.json"
    f.write_text(json.dumps({**doc, "loops": [{"first": 0, "last": 2, "count": 2}, {"first": 1, "last": 3,
                                                                                    "count": 2}]}), encoding="utf-8")
    try:
        ov, _ = seqfile.load(str(f))
    except FileFormatError:
        return
    assert any(str(i.severity) == "ERROR" for i in validate(ov))


# ============================================================================================ chart / bend

@pytest.mark.req("SW-SCH-001")
def test_tc_sw_sch_001_01_vv_path():
    """VV-PATH: x0 = 0, F0 = 0, k_est 50, pull_dir +1: TRAVEL 2; LOAD 200 N; HOLD; TRAVEL 0; HOME → (2, 100), (4, 200),
    (4, 200), (0, 0), break after HOME."""
    # Verifies: SW-SCH-001
    from bend_stand.core.sequencer.plan import expand, planned_path

    s = _seq([_st("travel", 2.0, speed_mm_s=2.0), _st("load", 200.0, speed_mm_s=1.0, tol_n=2.0), _st("hold", None),
              _st("travel", 0.0, speed_mm_s=2.0), _st("home", None)], travel_ref="machine", k_est_n_mm=50.0)
    s.steps[0].label = "T2"
    pts = planned_path(expand(s))
    xy = [(round(p.x_mm, 6), round(p.f_n, 6)) for p in pts if not p.brk]
    want = [(2.0, 100.0), (4.0, 200.0), (4.0, 200.0), (0.0, 0.0)]
    pos = 0
    for w in want:                                      # in order (the path may carry the start point first)
        while pos < len(xy) and xy[pos] != w:
            pos += 1
        assert pos < len(xy), (w, xy)
    assert any(p.brk for p in pts) or pts[-1].known == "none"
    assert any(p.label == "T2" for p in pts)


@pytest.mark.req("SW-REP-004")
def test_tc_sw_rep_004_01_tv_d_bend():
    """R4 TV-D 3-point bend: F 200 N, L 100, b 20, h 5 → σ 60 MPa; δ 2 mm → ε 0.006; slope 50 N/mm → E_f 5000 MPa."""
    # Verifies: SW-REP-004
    from bend_stand.calc.derived import bend3p_modulus, bend3p_strain, bend3p_stress

    assert bend3p_stress(200.0, 100.0, 20.0, 5.0) == pytest.approx(60.0)
    assert bend3p_strain(2.0, 100.0, 5.0) == pytest.approx(0.006)
    assert bend3p_modulus(100.0, 20.0, 5.0, 50.0) == pytest.approx(5000.0)


@pytest.mark.req("SW-SEQ-001")
@pytest.mark.defect("SWD-M4-01")
@pytest.mark.xfail(strict=True, reason="SWD-M4-01 open (B): core/sequencer/model.py _validate_step has no rule "
                                       "'capture_during_move needs capture_s > 0' (§15.5f B6-02, SW_test_plan VV)")
def test_tc_sw_seq_001_01_ramp_step_needs_a_capture():
    """B6-02 / TC-SW-SEQ-001-01: a TRAVEL step with ``capture_during_move`` and ``capture_s`` 0 is an ERROR (a ramp
    step must define its capture)."""
    # Verifies: SW-SEQ-001
    from bend_stand.core.sequencer.model import validate

    s = _st("travel", 10.0, speed_mm_s=2.0, capture_during_move=True, capture_s=0.0)
    assert any(str(i.severity) == "ERROR" and i.step_uid == s.uid for i in validate(_seq([s], travel_ref="machine")))
