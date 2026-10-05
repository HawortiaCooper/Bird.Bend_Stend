"""M4 sequence model, validation, loop expansion / plan, sequence files and generators (pure, no backend).

Verifies: SW-SEQ-001, SW-SEQ-002, SW-SEQF-001, SW-WIZ-001, SW-WIZ-002, SW-SCH-001, SAF-SW-006
"""
from __future__ import annotations

import itertools
import json
import random

import pytest

from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import IssueSeverity
from bend_stand.core.sequencer import generators as G
from bend_stand.core.sequencer import seqfile
from bend_stand.core.sequencer.model import (
    Block, Loop, Sequence, Step, StepDefaults, StepKind, ValidationContext, loop_depths, machine_mm, validate,
)
from bend_stand.core.sequencer.plan import PlanContext, count_exec, expand, iter_exec, planned_path

E, W = IssueSeverity.ERROR, IssueSeverity.WARN


def _seq(n: int = 4, loops: list[Loop] | None = None, **kw) -> Sequence:
    steps = [Step(f"u{i}", StepKind.TRAVEL, float(i), speed_mm_s=2.0, settle_s=0.5, capture_s=1.0) for i in range(n)]
    return Sequence(name="t", steps=steps, loops=loops or [], **kw)


def _codes(issues) -> set[str]:
    return {i.code for i in issues}


# ------------------------------------------------------------------------------------------------ validation
@pytest.mark.req("SW-SEQ-001")
def test_step_fields_validation_flags_invalid_entries() -> None:
    seq = Sequence(steps=[
        Step("a", StepKind.TRAVEL, None),                                              # missing target
        Step("b", StepKind.TRAVEL, 1.0, speed_mm_s=-1.0),                              # speed ≤ 0
        Step("c", StepKind.LOAD, 100.0, tol_n=0.0),                                    # tol ≤ 0
        Step("d", StepKind.LOAD, 100.0, capture_during_move=True, tol_n=1.0),          # not for load
        Step("e", StepKind.HOLD, settle_s=-1.0),                                       # time < 0
        Step("e", StepKind.MARK, wait_operator=True),                                  # duplicate uid
        Step("g", "bogus"),                                                            # type: ignore[arg-type]
        Step("h", StepKind.HOME, settle_s=1.0, label="x" * 81),
        Step("i", StepKind.TRAVEL, 1.0, wait_operator=True),
    ], k_est_n_mm=0.0, pull_dir=2, travel_ref="x")                                     # type: ignore[arg-type]
    iss = validate(seq)
    by = {(i.step_uid, i.field, i.code) for i in iss}
    assert ("a", "target", "MISSING") in by and ("b", "speed_mm_s", "SPEED_CAP") in by
    assert ("c", "tol_n", "RANGE") in by and ("d", "capture_during_move", "NOT_APPLICABLE") in by
    assert ("e", "settle_s", "RANGE") in by and ("e", "uid", "DUPLICATE_UID") in by
    assert ("g", "kind", "ENUM") in by and ("h", "label", "RANGE") in by
    assert (None, "k_est_n_mm", "RANGE") in by and (None, "pull_dir", "ENUM") in by and (None, "travel_ref", "ENUM") in by
    assert any(i.code == "NOT_APPLICABLE" and i.severity == W for i in iss if i.step_uid in ("h", "i"))
    assert validate(Sequence()) and "EMPTY" in _codes(validate(Sequence()))
    assert next(i for i in iss if i.step_uid == "a").key == "a.target"
    assert not [i for i in validate(_seq()) if i.severity == E]


@pytest.mark.req("SW-SEQ-001", "SAF-SW-006")
def test_context_rules_caps_range_load_limit_margin_extrapolation() -> None:
    ctx = ValidationContext(v_travel_cap_mm_s=30.0, v_load_cap_mm_s=20.0, a_max_mm_s2=100.0, travel_lo_mm=0.5,
                            travel_hi_mm=290.0, x_zero_mm=10.0, pull_trip_n=300.0, push_trip_n=-300.0,
                            fw_level_n=320.0, f_cal_max_n=30.0, margin_check=True)
    seq = Sequence(steps=[
        Step("t1", StepKind.TRAVEL, -9.4),                       # machine 0.6 → inside
        Step("t2", StepKind.TRAVEL, -9.6),                       # machine 0.4 → outside
        Step("t3", StepKind.TRAVEL, 1.0, speed_mm_s=25.0, accel_mm_s2=200.0),
        Step("l1", StepKind.LOAD, 100.0, speed_mm_s=25.0, tol_n=2.0),
        Step("l2", StepKind.LOAD, 299.0, speed_mm_s=1.0, tol_n=2.0),
        Step("l3", StepKind.LOAD, -200.0, speed_mm_s=1.0, tol_n=2.0)], k_est_n_mm=50.0)
    iss = validate(seq, ctx)
    by = {(i.step_uid, i.code, i.severity) for i in iss}
    assert ("t2", "TARGET_OUT_OF_RANGE", E) in by and not any(u == "t1" for u, _c, _s in by)
    assert ("t3", "ACCEL_CAP", E) in by and ("t3", "SAF_SW_006_MARGIN", W) in by
    assert ("t3", "SPEED_CAP", E) not in by and ("l1", "SPEED_CAP", E) in by          # LOAD cap = v_load (D-29 e)
    assert ("l2", "LOAD_LIMIT", E) in by and ("l1", "EXTRAPOLATED", W) in by
    assert ("l3", "EXTRAPOLATED", W) in by and ("l3", "LOAD_LIMIT", E) not in by
    assert machine_mm(seq, 1.0, 10.0) == 11.0
    seq.travel_ref = "machine"
    assert machine_mm(seq, 1.0, 10.0) == 1.0
    assert ("t1", "TARGET_OUT_OF_RANGE", E) in {(i.step_uid, i.code, i.severity) for i in validate(seq, ctx)}


@pytest.mark.req("SW-SEQ-002")
def test_loop_rules_one_nesting_level() -> None:
    ok = validate(_seq(6, [Loop(0, 5, 2), Loop(1, 2, 3), Loop(4, 4, 10_000)]))
    assert not [i for i in ok if i.severity == E]
    assert loop_depths([Loop(0, 5, 2), Loop(1, 2, 3)]) == [1, 2]
    assert "LOOP_DEPTH" in _codes(validate(_seq(6, [Loop(0, 5, 2), Loop(1, 4, 3), Loop(2, 3, 2)])))
    assert "LOOP_OVERLAP" in _codes(validate(_seq(6, [Loop(0, 3, 2), Loop(2, 5, 2)])))
    assert "LOOP_DUPLICATE" in _codes(validate(_seq(6, [Loop(1, 3, 2), Loop(1, 3, 4)])))
    assert "LOOP_RANGE" in _codes(validate(_seq(3, [Loop(1, 3, 2)])))
    assert "LOOP_COUNT" in _codes(validate(_seq(3, [Loop(0, 1, 10_001)])))
    assert "LOOP_COUNT" in _codes(validate(_seq(3, [Loop(0, 1, -1)])))
    assert "LOOP_INFINITE_INNER" in _codes(validate(_seq(4, [Loop(0, 3, 2), Loop(1, 2, 0)])))
    w = validate(_seq(4, [Loop(0, 1, 0)]))
    assert "UNREACHABLE" in _codes(w) and not [i for i in w if i.severity == E]
    assert "PLAN_TOO_LONG" in _codes(validate(_seq(30, [Loop(0, 29, 10_000)])))


# ------------------------------------------------------------------------------------------------ expansion
@pytest.mark.req("SW-SEQ-002")
def test_nested_loop_expansion_order_and_counters() -> None:
    seq = _seq(5, [Loop(1, 3, 2), Loop(2, 2, 3)])
    got = list(iter_exec(seq))
    assert got == [(0, ()), (1, (1,)), (2, (1, 1)), (2, (1, 2)), (2, (1, 3)), (3, (1,)),
                   (1, (2,)), (2, (2, 1)), (2, (2, 2)), (2, (2, 3)), (3, (2,)), (4, ())]
    assert count_exec(seq) == 12
    inf = _seq(3, [Loop(1, 2, 0)])
    first = list(itertools.islice(iter_exec(inf), 7))
    assert first == [(0, ()), (1, (1,)), (2, (1,)), (1, (2,)), (2, (2,)), (1, (3,)), (2, (3,))]
    assert list(iter_exec(inf, infinite=False)) == [(0, ()), (1, (1,)), (2, (1,))]
    disjoint = _seq(4, [Loop(0, 0, 2), Loop(2, 3, 2)])
    assert [i for i, _ in iter_exec(disjoint)] == [0, 0, 1, 2, 3, 2, 3]


@pytest.mark.req("SW-SEQ-002", "SW-SCH-001", "SW-SEQ-003")
def test_plan_times_windows_path_and_both_references() -> None:
    seq = Sequence(steps=[
        Step("t", StepKind.TRAVEL, 10.0, speed_mm_s=2.0, settle_s=1.0, capture_s=2.0, step_time_s=5.0),
        Step("l", StepKind.LOAD, 200.0, speed_mm_s=1.0, settle_s=0.5, capture_s=1.0, step_time_s=99.0),
        Step("h", StepKind.HOLD, settle_s=0.0, capture_s=3.0),
        Step("r", StepKind.TRAVEL, 0.0, speed_mm_s=2.0, capture_during_move=True),
        Step("H", StepKind.HOME), Step("T", StepKind.TARE), Step("m", StepKind.MARK, label="end")],
        loops=[Loop(1, 2, 2)], k_est_n_mm=50.0)
    plan = expand(seq, PlanContext(x0_mm=0.0, f0_n=0.0, a_default_mm_s2=100.0, home_x_mm=-10.0, tare_window_s=4.0))
    s = plan.steps
    assert [p.kind for p in s] == ["travel", "load", "hold", "load", "hold", "travel", "home", "tare", "mark"]
    t_move = 10.0 / 2.0 + 2.0 / 100.0
    assert s[0].t_reached_s == pytest.approx(t_move) and s[0].capture == pytest.approx((t_move + 1.0, t_move + 3.0))
    assert s[0].t_end_s == pytest.approx(t_move + 5.0)                       # max(step time, settle + capture)
    assert s[1].f_target_n == 200.0 and s[1].x_target_mm == pytest.approx(4.0)    # 10 + (200 − 500)/50 (k_est)
    assert s[1].t_end_s - s[1].t_reached_s == pytest.approx(1.5)              # LOAD dwell ignores step_time_s
    assert s[0].f_target_n == pytest.approx(500.0) and s[0].known == "x" and s[1].known == "F"
    assert s[5].capture == (s[5].t_cmd_s, s[5].t_reached_s)                  # capture during the move
    assert s[6].x_target_mm == -10.0 and s[7].f_target_n == 0.0
    assert s[3].loop_iters == (2,) and s[3].exec_idx == 3
    assert plan.windows_total == 6 and not plan.infinite and plan.total_s == pytest.approx(s[-1].t_end_s)
    pts = planned_path(plan)
    assert len(pts) == len(s) + 1 and pts[0].label == "start" and pts[7].brk and pts[1].capture
    inf = expand(_seq(3, [Loop(1, 2, 0)]))
    assert inf.infinite and inf.total_s is None and inf.windows_total is None and inf.steps[1].infinite


@pytest.mark.req("SW-WIZ-002")
def test_insert_block_modes_reindex_loops() -> None:
    seq = _seq(4, [Loop(1, 2, 3)])
    blk = Block([Step("u0", StepKind.TRAVEL, 9.0), Step("n2", StepKind.TRAVEL, 8.0)], [Loop(0, 1, 2)])
    s1 = seq.copy()
    s1.insert_block(blk, "append")
    assert len(s1.steps) == 6 and s1.steps[4].uid != "u0" and [(lp.first, lp.last) for lp in s1.loops] == [(1, 2), (4, 5)]
    s2 = seq.copy()
    s2.insert_block(blk, "insert", 2)
    assert [(lp.first, lp.last, lp.count) for lp in s2.loops] == [(1, 4, 3), (2, 3, 2)]
    s3 = seq.copy()
    s3.insert_block(blk, "insert", 0)
    assert [(lp.first, lp.last) for lp in s3.loops] == [(0, 1), (3, 4)]
    s4 = seq.copy()
    s4.insert_block(blk, "replace")
    assert [s.uid for s in s4.steps] == ["u0", "n2"] and s4.loops == [Loop(0, 1, 2)]
    assert blk.steps[0].uid == "u0" and len(seq.steps) == 4                  # the block / original stay unchanged


# ------------------------------------------------------------------------------------------------ files
@pytest.mark.req("SW-SEQF-001")
def test_sequence_file_round_trip_identical(tmp_path) -> None:
    seq = Sequence(name="staircase 0-200 N", travel_ref="machine", k_est_n_mm=41.25, pull_dir=-1,
                   defaults=StepDefaults(1.5, 50.0, 2.0, 5.0, 2.5), notes="n",
                   steps=[Step("s1", StepKind.TRAVEL, 0.1, 2.0, label="start"),
                          Step("s2", StepKind.LOAD, 100.0, None, 0.0, 2.0, 5.0, 0.0, 2.0, label="100 N"),
                          Step("s3", StepKind.MARK, wait_operator=True), Step("s4", StepKind.TRAVEL, 1 / 3,
                                                                              capture_during_move=True)],
                   loops=[Loop(1, 2, 3)])
    p = tmp_path / "a.bbseq.json"
    seqfile.save(p, seq)
    back, warns = seqfile.load(p)
    assert back == seq and not warns
    p2 = tmp_path / "b.bbseq.json"
    seqfile.save(p2, back)
    assert p.read_bytes() == p2.read_bytes()
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["schema"] == "bird.bend.sequence" and d["schema_version"] == 2 and "on_trim_fail" not in d


@pytest.mark.req("SW-SEQF-001")
def test_invalid_files_raise_and_v1_migration(tmp_path) -> None:
    base = seqfile.to_dict(_seq(2))
    bad = [None, {"schema": "bird.bend.other", "schema_version": 1}, {**base, "schema_version": 3},
           {**base, "schema_version": "2"}, {**base, "steps": "x"}, {**base, "steps": [{"uid": "a"}]},
           {**base, "steps": [{"uid": "a", "kind": "fly"}]}, {**base, "steps": [{"uid": "a", "kind": "travel",
                                                                                 "target": "1"}]},
           {**base, "steps": [{"uid": "a", "kind": "travel", "wait_operator": 1}]},
           {**base, "loops": [{"first": 0, "last": 1}]}, {**base, "loops": [{"first": 0.5, "last": 1, "count": 1}]},
           {**base, "travel_ref": "abs"}, {**base, "pull_dir": 0}, {**base, "defaults": []},
           {**base, "k_est_n_mm": float("nan")}]
    for i, d in enumerate(bad):
        with pytest.raises(FileFormatError):
            seqfile.from_dict(d)
        p = tmp_path / f"bad{i}.bbseq.json"
        p.write_text(json.dumps(d, allow_nan=True), encoding="utf-8")
        with pytest.raises(FileFormatError):
            seqfile.load(p)
    (tmp_path / "x.bbseq.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(FileFormatError):
        seqfile.load(tmp_path / "x.bbseq.json")
    with pytest.raises(FileFormatError):
        seqfile.load(tmp_path / "missing.bbseq.json")
    v1 = {"schema": "bird.bend.sequence", "schema_version": 1, "name": "old", "travel_ref": "test",
          "k_est_n_mm": 50.0, "pull_dir": 1, "on_trim_fail": "continue",
          "defaults": {"speed_mm_s": 1.0, "accel_mm_s2": 0.0, "settle_s": 2.0, "capture_s": 5.0, "tol_n": 2.0},
          "steps": [{"uid": "s2", "kind": "load", "target": 100.0, "tol_n": 2.0, "travel_bound_mm": 50.0,
                     "extra": 1}], "loops": [], "notes": "", "zzz": 0}
    seq, warns = seqfile.from_dict(v1)
    assert seq.steps[0].target == 100.0 and len(warns) == 4
    assert any("on_trim_fail" in w for w in warns) and any("travel_bound_mm" in w for w in warns)


# ------------------------------------------------------------------------------------------------ generators
@pytest.mark.req("SW-WIZ-001")
def test_staircase_vectors() -> None:
    t = lambda b: [s.target for s in b.steps]  # noqa: E731
    assert t(G.staircase(start=0, end=30, count=4, up_down=True, return_to_zero=True)) == \
        [0, 10, 0, 20, 0, 30, 0, 20, 0, 10, 0]                                 # §10.6 example (11 steps)
    assert t(G.staircase(start=0, end=10, increment=3)) == [0, 3, 6, 9]       # end only if the increment divides
    assert t(G.staircase(start=0, end=9, increment=3)) == [0, 3, 6, 9]
    assert t(G.staircase(start=10, end=0, increment=2.5)) == [10, 7.5, 5, 2.5, 0]
    assert t(G.staircase(start=0, end=1, count=4)) == pytest.approx([0, 0.333, 0.667, 1])   # µm rounding
    assert t(G.staircase(kind="load", start=50, end=150, increment=50, up_down=True)) == [50, 100, 150, 100, 50]
    b = G.staircase(kind="load", start=50, end=100, increment=50, return_to_zero=True, tol_n=1.5, capture_s=3.0)
    assert t(b) == [50, 0, 100, 0] and b.steps[1].label == "zero" and b.steps[1].capture_s == 0.0
    assert all(s.kind == StepKind.LOAD and s.tol_n == 1.5 for s in b.steps)
    for kw in ({"start": 0, "end": 0, "count": 2}, {"start": 0, "end": 5, "increment": 0},
               {"start": 0, "end": 5, "increment": 6}, {"start": 0, "end": 5, "count": 1},
               {"start": 0, "end": 5}, {"start": 0, "end": 5, "count": 2, "increment": 1}, {"kind": "x"}):
        with pytest.raises(ValueError):
            G.staircase(**kw)


@pytest.mark.req("SW-WIZ-001")
def test_other_generators() -> None:
    r = G.linear_ramp(x0_mm=1.0, x1_mm=4.0, speed_mm_s=0.5, approach_speed_mm_s=3.0)
    assert [(s.target, s.capture_during_move, s.speed_mm_s) for s in r.steps] == [(1.0, False, 3.0), (4.0, True, 0.5)]
    c = G.cyclic(kind="load", lo=10.0, hi=100.0, cycles=5, dwell_s=2.0, tol_n=1.0)
    assert [s.target for s in c.steps] == [10.0, 100.0] and c.loops == [Loop(0, 1, 5)]
    assert c.steps[0].settle_s == 2.0 and c.steps[0].step_time_s == 0.0
    ct = G.cyclic(lo=0.0, hi=2.0, cycles=0, dwell_s=1.0)
    assert ct.loops[0].count == 0 and ct.steps[0].step_time_s == 1.0
    hd = G.hold(kind="load", target=150.0, duration_s=600.0)
    assert hd.steps[0].capture_s == 600.0 and hd.steps[0].kind == StepKind.LOAD
    assert G.return_(to="home").steps[0].kind == StepKind.HOME and G.return_().steps[0].target == 0.0
    for fn, kw in ((G.linear_ramp, {"x0_mm": 1, "x1_mm": 1}), (G.cyclic, {"lo": 2, "hi": 1}),
                   (G.cyclic, {"cycles": 10_001}), (G.hold, {"duration_s": 0}), (G.return_, {"to": "x"})):
        with pytest.raises(ValueError):
            fn(**kw)


@pytest.mark.req("SW-WIZ-001", "SW-WIZ-002")
def test_schemas_and_generate_checks_parameters() -> None:
    ctx = G.SchemaContext(v_travel_max_mm_s=25.0, travel_lo_mm=-9.5, travel_hi_mm=280.0)
    sc = G.generator_schemas(ctx)
    assert set(sc) == {"staircase", "linear_ramp", "cyclic", "hold", "return_"}
    f = {x.name: x for x in sc["staircase"].fields}
    assert f["speed_mm_s"].max == 25.0 and f["count"].depends_on == ("by", "count") and f["kind"].kind == "enum"
    assert {x.name for x in sc["linear_ramp"].fields} >= {"x0_mm", "x1_mm"}
    assert sc["linear_ramp"].fields[0].min == -9.5
    b = G.generate("staircase", {"start": 0.0, "end": 3.0, "by": "count", "count": 4}, ctx=ctx)
    assert [s.target for s in b.steps] == [0, 1, 2, 3]
    b = G.generate("staircase", start=0.0, end=3.0, increment=1)               # int accepted for a float field
    assert len(b.steps) == 4
    assert G.generate("cyclic", {"cycles": 2.0}).loops[0].count == 2
    for name, p in (("staircase", {"speed_mm_s": 99.0}), ("staircase", {"up_down": 1}), ("hold", {"kind": "z"}),
                    ("staircase", {"bogus": 1}), ("nope", {}), ("cyclic", {"cycles": 2.5}),
                    ("hold", {"target": "x"})):
        with pytest.raises(ValueError):
            G.generate(name, p, ctx=ctx)
    rnd = random.Random()
    for _ in range(50):                                                         # property: count mode ends at end
        a, z = rnd.uniform(-50, 50), rnd.uniform(-50, 50)
        if abs(a - z) < 0.01:
            continue
        n = rnd.randint(2, 40)
        lv = G.staircase_levels(a, z, count=n)
        assert len(lv) == n and lv[-1] == round(z * 1000) / 1000 and lv[0] == round(a * 1000) / 1000


@pytest.mark.req("SW-SEQ-001", "SW-SEQ-002")
def test_edit_helpers_keep_loop_indices() -> None:
    """GRQ-B-28: delete / move / duplicate with the loop bookkeeping next to insert_block."""
    s = _seq(6, [Loop(1, 3, 2), Loop(5, 5, 4)])
    s.delete_steps([0, 2])
    assert [x.uid for x in s.steps] == ["u1", "u3", "u4", "u5"] and s.loops == [Loop(0, 1, 2), Loop(3, 3, 4)]
    s.delete_steps([3])
    assert s.loops == [Loop(0, 1, 2)]
    assert s.move_step(0, 1) == 1 and [x.uid for x in s.steps] == ["u3", "u1", "u4"] and s.loops == [Loop(0, 1, 2)]
    assert s.move_step(0, -1) == 0 and s.move_step(2, 2) == 2
    new = s.duplicate_steps([0, 1])
    assert new == [2, 3] and len({x.uid for x in s.steps}) == 5 and s.loops == [Loop(0, 3, 2)]
    t = _seq(4, [Loop(2, 3, 2)])
    assert t.duplicate_steps([0]) == [1] and t.loops == [Loop(3, 4, 2)] and t.duplicate_steps([]) == []
    assert not [i for i in validate(s) if i.severity == E]


@pytest.mark.req("SW-SEQ-001", "SW-WIZ-001")
def test_gate_fixes_swd_m4_01_obs_m4_01() -> None:
    """SWD-M4-01: a ramp step (capture during move) needs capture_s > 0; OBS-M4-01: staircase ``count`` alone
    infers by = "count", conflicting / inapplicable conditional fields raise."""
    bad = Sequence(steps=[Step("r", StepKind.TRAVEL, 1.0, capture_during_move=True, capture_s=0.0)])
    assert ("r", "capture_s", "RANGE", E) in {(i.step_uid, i.field, i.code, i.severity) for i in validate(bad)}
    ramp = G.linear_ramp(x0_mm=1.0, x1_mm=4.0, speed_mm_s=0.5)
    assert ramp.steps[1].capture_s == pytest.approx(6.0)
    assert not [i for i in validate(Sequence(steps=ramp.steps)) if i.severity == E]
    assert [s.target for s in G.generate("staircase", start=0.0, end=4.0, count=5).steps] == [0, 1, 2, 3, 4]
    with pytest.raises(ValueError, match="count / increment"):
        G.generate("staircase", start=0.0, end=4.0, count=5, increment=1.0)
    with pytest.raises(ValueError, match="tol_n"):
        G.generate("staircase", start=0.0, end=4.0, increment=1.0, tol_n=1.0)     # kind defaults to travel
    assert len(G.generate("staircase", kind="travel", start=0.0, end=4.0, increment=1.0, tol_n=1.0).steps) == 5
    assert len(G.generate("staircase", by="increment", start=0.0, end=4.0, increment=1.0, count=3).steps) == 5
