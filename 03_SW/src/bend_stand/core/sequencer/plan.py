"""Expanded plan and planned path (SW_design §10.2, §15.5f B6-03; SRS SW-SEQ-002, SW-SCH-001).

``iter_exec(seq, infinite=True)`` walks the step table with the loops (one nesting level; count 0 = until stopped)
and yields ``(step_idx, loop_iters)`` lazily — the executor's step order. ``expand(seq, ctx)`` builds the finite
``Plan`` (a count-0 loop expanded **once**, ``infinite = True``): per executed step the start / target coordinates
(sequence coordinate; LOAD positions estimated with k_est, TRAVEL forces with k_est — ``calc.path.advance``), the
planned times (trapezoid durations ``calc.trim.move_time_s``; LOAD: approach + trim estimate; HOME / TARE fixed
estimates) and the capture window. One plan is the single source for the editor preview, totals, the chart and the
executor's remaining time.

Implements: SW-SEQ-002 (loop expansion, iteration counters), SW-SCH-001 (planned path), SW-SEQ-003 (plan times)
"""
from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

from bend_stand.calc.path import PathPoint, advance, planned_path as _path
from bend_stand.calc.trim import move_time_s
from bend_stand.core.sequencer.model import MAX_PLAN_STEPS, Loop, Sequence, StepKind

NAN = float("nan")
LOAD_TRIM_EST_S = 1.0                  # plan estimate of the trim phase of a load step
HOME_V_MM_S = 10.0                     # plan estimate of homing (fast seek + approach)
HOME_FIXED_S = 5.0
TARE_EXTRA_S = 1.5                     # tare: ≥ 1 s after the last move + evaluation


class PlanTooLong(ValueError):
    pass


@dataclass(frozen=True)
class PlanContext:
    x0_mm: float = 0.0                 # start position in the sequence coordinate
    f0_n: float = 0.0                  # start force
    a_default_mm_s2: float = 100.0     # FW default acceleration (motion.a_max_um_s2)
    home_x_mm: float = 0.0             # machine 0 in the sequence coordinate (−x_zero for travel_ref "test")
    tare_window_s: float = 10.0


@dataclass(frozen=True)
class PlannedStep:
    exec_idx: int
    step_idx: int
    uid: str
    loop_iters: tuple[int, ...]
    kind: str
    label: str
    x_start_mm: float
    x_target_mm: float
    f_start_n: float
    f_target_n: float
    t_cmd_s: float
    t_reached_s: float
    capture: tuple[float, float] | None
    t_end_s: float
    infinite: bool = False
    known: str = "x"

    @property
    def duration_s(self) -> float:
        return self.t_end_s - self.t_cmd_s


@dataclass(frozen=True)
class Plan:
    steps: tuple[PlannedStep, ...]
    total_s: float | None
    infinite: bool
    windows_total: int | None
    x_range_mm: tuple[float, float] | None
    f_range_n: tuple[float, float] | None

    def __len__(self) -> int:
        return len(self.steps)


def _tree(loops: list[Loop]) -> tuple[list[Loop], dict[int, list[Loop]]]:
    """Outer loops and, per outer loop index, its inner loops (validated input: disjoint or nested one level)."""
    ls = sorted(loops, key=lambda lp: (lp.first, -lp.last))
    outer: list[Loop] = []
    inner: dict[int, list[Loop]] = {}
    for lp in ls:
        parent = next((o for o in outer if o.first <= lp.first and lp.last <= o.last), None)
        if parent is None:
            outer.append(lp)
            inner[id(lp)] = []
        else:
            inner[id(parent)].append(lp)
    return outer, inner


def iter_exec(seq: Sequence, *, infinite: bool = True) -> Iterator[tuple[int, tuple[int, ...]]]:
    """``(step_idx, loop_iters)`` in execution order; ``infinite=False`` runs a count-0 loop once."""
    outer, inner = _tree(seq.loops)

    def level(lo: int, hi: int, loops: list[Loop], iters: tuple[int, ...],
              sub: dict[int, list[Loop]] | None) -> Iterator[tuple[int, tuple[int, ...]]]:
        i = lo
        while i <= hi:
            lp = next((x for x in loops if x.first == i), None)
            if lp is None:
                yield i, iters
                i += 1
                continue
            it = 1
            while lp.count == 0 or it <= lp.count:
                if sub is not None:
                    yield from level(lp.first, lp.last, sub.get(id(lp), []), iters + (it,), None)
                else:
                    yield from level(lp.first, lp.last, [], iters + (it,), None)
                it += 1
                if lp.count == 0 and not infinite:
                    break
            i = lp.last + 1

    yield from level(0, len(seq.steps) - 1, outer, (), inner)


def count_exec(seq: Sequence, limit: int = MAX_PLAN_STEPS + 1) -> int:
    """Number of executed steps of the finite plan (count-0 loops once), stopping at ``limit``."""
    n = 0
    for _ in iter_exec(seq, infinite=False):
        n += 1
        if n >= limit:
            break
    return n


def is_infinite(seq: Sequence) -> bool:
    return any(lp.count == 0 for lp in seq.loops)


def expand(seq: Sequence, ctx: PlanContext | None = None) -> Plan:
    """The finite plan (``PlanTooLong`` beyond ``MAX_PLAN_STEPS`` executed steps)."""
    c = ctx or PlanContext()
    inf = is_infinite(seq)
    k_est = float(seq.k_est_n_mm) if seq.k_est_n_mm and seq.k_est_n_mm > 0 else 50.0
    x, f, t = float(c.x0_mm), float(c.f0_n), 0.0
    out: list[PlannedStep] = []
    windows = 0
    infinite_loops = [lp for lp in seq.loops if lp.count == 0]
    for n, (i, iters) in enumerate(iter_exec(seq, infinite=False)):
        if n >= MAX_PLAN_STEPS:
            raise PlanTooLong(f"more than {MAX_PLAN_STEPS} executed steps")
        s = seq.steps[i]
        kind = StepKind(s.kind)
        x0, f0 = x, f
        target = s.target if s.target is not None and math.isfinite(float(s.target)) else None
        nx, nf, known = advance(kind.value, x, f, target, k_est, seq.pull_dir, c.home_x_mm)
        a = float(s.accel_mm_s2) if s.accel_mm_s2 and s.accel_mm_s2 > 0 else float(c.a_default_mm_s2)
        v = seq.speed(s)
        t_move = 0.0
        if kind in (StepKind.TRAVEL, StepKind.LOAD) and target is not None and v > 0 and a > 0:
            t_move = move_time_s(nx - x0, v, a)
            if kind == StepKind.LOAD:
                t_move += LOAD_TRIM_EST_S
        elif kind == StepKind.HOME:
            t_move = abs(x0 - c.home_x_mm) / HOME_V_MM_S + HOME_FIXED_S
        elif kind == StepKind.TARE:
            t_move = c.tare_window_s + TARE_EXTRA_S
        t_cmd = t
        t_reached = t + t_move
        cap: tuple[float, float] | None = None
        dwell = 0.0
        if kind == StepKind.TRAVEL and s.capture_during_move:
            cap = (t_cmd, t_reached)
            dwell = max(float(s.step_time_s), float(s.settle_s))
        elif kind in (StepKind.TRAVEL, StepKind.LOAD, StepKind.HOLD):
            if s.capture_s > 0:
                cap = (t_reached + float(s.settle_s), t_reached + float(s.settle_s) + float(s.capture_s))
            dwell = float(s.settle_s) + float(s.capture_s)
            if kind != StepKind.LOAD:
                dwell = max(float(s.step_time_s), dwell)
        if cap is not None:
            windows += 1
        t_end = t_reached + dwell
        in_inf = any(lp.first <= i <= lp.last for lp in infinite_loops)
        out.append(PlannedStep(n, i, s.uid, iters, kind.value, s.label or kind.value, x0, nx, f0, nf, t_cmd,
                               t_reached, cap, t_end, in_inf, known))
        x, f, t = nx, nf, t_end
    xs = [p.x_target_mm for p in out if math.isfinite(p.x_target_mm)] + ([c.x0_mm] if out else [])
    fs = [p.f_target_n for p in out if math.isfinite(p.f_target_n)] + ([c.f0_n] if out else [])
    return Plan(tuple(out), None if inf else t, inf, None if inf else windows,
                (min(xs), max(xs)) if xs else None, (min(fs), max(fs)) if fs else None)


def planned_path(plan: Plan) -> tuple[PathPoint, ...]:
    return _path(plan.steps)


__all__ = ["PlanContext", "PlannedStep", "Plan", "PlanTooLong", "iter_exec", "count_exec", "expand", "planned_path",
           "is_infinite"]
