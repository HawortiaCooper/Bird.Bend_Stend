"""Sequence generators and their parameter schemas (SW_design §10.6, §15.5f B6-04; SRS SW-WIZ-001/002, R4 §8.2).

Pure functions returning a ``Block(steps, loops)`` (indices relative to the block; inserted with
``Sequence.insert_block``, the steps stay editable):

* ``staircase`` (travel or load): levels in integer units (µm / mN) — *increment* mode ``L_i = start + i·inc`` while not
  past ``end`` (the end level only if the increment divides the span); *count* mode = number of levels incl. start
  and end (≥ 2), the last level exactly ``end``; ``up_down`` appends the down levels without repeating the peak;
  ``return_to_zero`` inserts a zero step (same speed, no capture) after every non-zero level incl. the last;
  a step equal to the previous step's target is never generated. Example start 0, end 30, count 4, up_down,
  return_to_zero → 0, 10, 0, 20, 0, 30, 0, 20, 0, 10, 0.
* ``linear_ramp``: TRAVEL x0 (no capture) + TRAVEL x1 with ``capture_during_move`` (VALID = 1 while moving).
* ``cyclic``: two steps lo / hi (dwell = minimum step time, optional capture) inside ``Loop(count = cycles)``.
* ``hold``: one step at the target with ``capture_s = duration`` (creep / relaxation captured throughout).
* ``return_``: TRAVEL 0 (sequence coordinate) or HOME.

``generator_schemas(ctx)`` describes every parameter (kind, unit, min / max / default resolved from the motion caps,
SW limits and session defaults at call time, ``depends_on`` for conditional fields); ``generate(name, params)``
checks the parameters against the same schema and calls the generator (``ValueError`` names the field).

Implements: SW-WIZ-001, SW-WIZ-002 (blocks for insert), GRQ-B-18 (schemas)
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from bend_stand.calc.rounding import round_half_away
from bend_stand.core.sequencer.model import Block, Loop, Step, StepKind, new_uid

UNIT_SCALE = {"travel": 1000.0, "load": 1000.0}       # µm per mm, mN per N (integer level arithmetic)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    unit: str
    kind: str                                   # "float" | "int" | "enum" | "bool"
    min: float | None = None
    max: float | None = None
    default: Any = None
    choices: tuple[str, ...] = ()
    depends_on: tuple[str, Any] | None = None   # shown / used only when params[name] == value
    srs: str = "SW-WIZ-001"


@dataclass(frozen=True)
class GeneratorSchema:
    name: str
    label: str
    description: str
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class SchemaContext:
    v_travel_max_mm_s: float = 30.0
    v_load_max_mm_s: float = 20.0
    a_max_mm_s2: float = 1000.0
    travel_lo_mm: float = -300.0
    travel_hi_mm: float = 300.0
    load_max_n: float = 2157.46
    speed_default_mm_s: float = 1.0
    settle_default_s: float = 1.0
    capture_default_s: float = 2.0
    tol_default_n: float = 2.0


def _label(kind: str, v: float) -> str:
    return f"{v:g} mm" if kind == "travel" else f"{v:g} N"


def _step(kind: str, target: float | None, *, speed: float | None, accel: float = 0.0, settle: float = 0.0,
          capture: float = 0.0, step_time: float = 0.0, tol: float | None = None, label: str = "",
          capture_during_move: bool = False) -> Step:
    k = StepKind(kind)
    return Step(new_uid(), k, None if target is None else float(target), None if speed is None else float(speed),
                float(accel), float(settle), float(capture), 0.0 if k == StepKind.LOAD else float(step_time),
                (None if tol is None else float(tol)) if k == StepKind.LOAD else None, capture_during_move, False,
                label)


def _kind(kind: str) -> str:
    if kind not in ("travel", "load"):
        raise ValueError(f"kind: 'travel' or 'load' expected, got {kind!r}")
    return kind


def staircase_levels(start: float, end: float, *, increment: float | None = None, count: int | None = None,
                     up_down: bool = False, unit: float = 1000.0) -> list[float]:
    """Level values (SWD-P1-12 c), computed in integer units (``unit`` per mm / N)."""
    a, b = round_half_away(start * unit), round_half_away(end * unit)
    span = b - a
    if span == 0:
        raise ValueError("end: must differ from start")
    sgn = 1 if span > 0 else -1
    if (increment is None) == (count is None):
        raise ValueError("increment / count: give exactly one")
    if increment is not None:
        inc = round_half_away(abs(float(increment)) * unit)
        if inc <= 0 or inc > abs(span):
            raise ValueError("increment: must be > 0 and ≤ |end − start|")
        lv = []
        i = 0
        while True:
            v = a + sgn * i * inc
            if (v - b) * sgn > 0:
                break
            lv.append(v)
            i += 1
    else:
        if not isinstance(count, int) or isinstance(count, bool) or count < 2:
            raise ValueError("count: number of levels incl. start and end, ≥ 2")
        lv = [a + round_half_away(span * i / (count - 1)) for i in range(count - 1)] + [b]
    if up_down:
        lv = lv + lv[-2::-1]
    return [v / unit for v in lv]


def staircase(*, kind: str = "travel", start: float = 0.0, end: float = 1.0, increment: float | None = None,
              count: int | None = None, up_down: bool = False, return_to_zero: bool = False, speed_mm_s: float = 1.0,
              accel_mm_s2: float = 0.0, settle_s: float = 1.0, capture_s: float = 2.0, step_time_s: float = 0.0,
              tol_n: float | None = None, **_: Any) -> Block:
    kind = _kind(kind)
    levels = staircase_levels(start, end, increment=increment, count=count, up_down=up_down,
                              unit=UNIT_SCALE[kind])
    targets: list[tuple[float, bool]] = []          # (target, is_zero_return)
    for v in levels:
        targets.append((v, False))
        if return_to_zero and v != 0:
            targets.append((0.0, True))
    steps: list[Step] = []
    prev: float | None = None
    for v, is_zero in targets:
        if prev is not None and v == prev:
            continue                                # no consecutive duplicate (e.g. two zeros)
        if is_zero:
            steps.append(_step(kind, 0.0, speed=speed_mm_s, accel=accel_mm_s2, tol=tol_n, label="zero"))
        else:
            steps.append(_step(kind, v, speed=speed_mm_s, accel=accel_mm_s2, settle=settle_s, capture=capture_s,
                               step_time=step_time_s, tol=tol_n, label=_label(kind, v)))
        prev = v
    return Block(steps, [])


def linear_ramp(*, x0_mm: float = 0.0, x1_mm: float = 1.0, speed_mm_s: float = 0.5, accel_mm_s2: float = 0.0,
                approach_speed_mm_s: float | None = None, **_: Any) -> Block:
    if x0_mm == x1_mm:
        raise ValueError("x1_mm: must differ from x0_mm")
    s0 = _step("travel", x0_mm, speed=approach_speed_mm_s or speed_mm_s, accel=accel_mm_s2, label="ramp start")
    s1 = _step("travel", x1_mm, speed=speed_mm_s, accel=accel_mm_s2, label=f"ramp → {x1_mm:g} mm",
               capture_during_move=True)
    return Block([s0, s1], [])


def cyclic(*, kind: str = "travel", lo: float = 0.0, hi: float = 1.0, cycles: int = 3, speed_mm_s: float = 1.0,
           accel_mm_s2: float = 0.0, dwell_s: float = 0.0, settle_s: float = 0.0, capture_s: float = 0.0,
           tol_n: float | None = None, **_: Any) -> Block:
    kind = _kind(kind)
    if not lo < hi:
        raise ValueError("hi: must be greater than lo")
    if not isinstance(cycles, int) or isinstance(cycles, bool) or not 0 <= cycles <= 10_000:
        raise ValueError("cycles: 1…10 000 (0 = until stopped)")
    st = [_step(kind, v, speed=speed_mm_s, accel=accel_mm_s2, settle=settle_s, capture=capture_s,
                step_time=dwell_s, tol=tol_n, label=_label(kind, v)) for v in (lo, hi)]
    if kind == "load" and dwell_s > 0:      # LOAD has no step time: the dwell becomes the settle time
        for s in st:
            s.settle_s = max(s.settle_s, float(dwell_s))
    return Block(st, [Loop(0, 1, int(cycles))])


def hold(*, kind: str = "travel", target: float = 0.0, duration_s: float = 60.0, speed_mm_s: float = 1.0,
         accel_mm_s2: float = 0.0, tol_n: float | None = None, **_: Any) -> Block:
    kind = _kind(kind)
    if not duration_s > 0:
        raise ValueError("duration_s: must be > 0")
    return Block([_step(kind, target, speed=speed_mm_s, accel=accel_mm_s2, capture=duration_s, tol=tol_n,
                        label=f"hold {_label(kind, target)}")], [])


def return_(*, to: str = "zero", speed_mm_s: float = 2.0, accel_mm_s2: float = 0.0, **_: Any) -> Block:
    if to == "home":
        return Block([Step(new_uid(), StepKind.HOME, label="home")], [])
    if to != "zero":
        raise ValueError("to: 'zero' or 'home'")
    return Block([_step("travel", 0.0, speed=speed_mm_s, accel=accel_mm_s2, label="return")], [])


GENERATORS: Mapping[str, Callable[..., Block]] = {
    "staircase": staircase, "linear_ramp": linear_ramp, "cyclic": cyclic, "hold": hold, "return_": return_,
}


def generator_schemas(ctx: SchemaContext | None = None) -> dict[str, GeneratorSchema]:
    c = ctx or SchemaContext()
    vt = c.v_travel_max_mm_s
    kind = FieldSpec("kind", "Kind", "", "enum", default="travel", choices=("travel", "load"))
    lvl = (c.travel_lo_mm, c.travel_hi_mm)

    def common(load_speed: bool = True) -> tuple[FieldSpec, ...]:
        return (FieldSpec("speed_mm_s", "Speed", "mm/s", "float", 1e-3, vt, min(c.speed_default_mm_s, vt)),
                FieldSpec("accel_mm_s2", "Ramp (0 = FW default)", "mm/s²", "float", 0.0, c.a_max_mm_s2, 0.0))

    tgt_max = max(abs(c.travel_lo_mm), abs(c.travel_hi_mm), c.load_max_n)
    tol = FieldSpec("tol_n", "Load tolerance", "N", "float", 1e-3, c.load_max_n, c.tol_default_n,
                    depends_on=("kind", "load"), srs="SW-SEQ-006")
    return {
        "staircase": GeneratorSchema("staircase", "Staircase", "steps up (and down) in travel or load", (
            kind,
            FieldSpec("start", "Start", "mm | N", "float", -tgt_max, tgt_max, 0.0),
            FieldSpec("end", "End", "mm | N", "float", -tgt_max, tgt_max, 5.0),
            FieldSpec("by", "Levels by", "", "enum", default="increment", choices=("increment", "count")),
            FieldSpec("increment", "Increment", "mm | N", "float", 1e-3, tgt_max, 1.0, depends_on=("by", "increment")),
            FieldSpec("count", "Levels (incl. start and end)", "", "int", 2, 1000, 6, depends_on=("by", "count")),
            FieldSpec("up_down", "Up and down", "", "bool", default=False),
            FieldSpec("return_to_zero", "Return to zero between levels", "", "bool", default=False),
            *common(),
            FieldSpec("settle_s", "Settle", "s", "float", 0.0, 3600.0, c.settle_default_s),
            FieldSpec("capture_s", "Capture", "s", "float", 0.0, 3600.0, c.capture_default_s),
            FieldSpec("step_time_s", "Step time (travel)", "s", "float", 0.0, 86400.0, 0.0,
                      depends_on=("kind", "travel")),
            tol)),
        "linear_ramp": GeneratorSchema("linear_ramp", "Linear ramp", "x0 → x1 at constant speed, captured while "
                                                                         "moving", (
            FieldSpec("x0_mm", "Start x0", "mm", "float", *lvl, 0.0),
            FieldSpec("x1_mm", "End x1", "mm", "float", *lvl, 5.0),
            FieldSpec("speed_mm_s", "Ramp speed", "mm/s", "float", 1e-3, vt, min(0.5, vt)),
            FieldSpec("approach_speed_mm_s", "Speed to x0", "mm/s", "float", 1e-3, vt, min(c.speed_default_mm_s, vt)),
            FieldSpec("accel_mm_s2", "Ramp (0 = FW default)", "mm/s²", "float", 0.0, c.a_max_mm_s2, 0.0))),
        "cyclic": GeneratorSchema("cyclic", "Cyclic / triangle", "between lo and hi, n cycles", (
            kind,
            FieldSpec("lo", "Lower", "mm | N", "float", -tgt_max, tgt_max, 0.0),
            FieldSpec("hi", "Upper", "mm | N", "float", -tgt_max, tgt_max, 2.0),
            FieldSpec("cycles", "Cycles (0 = until stopped)", "", "int", 0, 10_000, 3),
            *common(),
            FieldSpec("dwell_s", "Dwell", "s", "float", 0.0, 3600.0, 0.0),
            FieldSpec("settle_s", "Settle", "s", "float", 0.0, 3600.0, 0.0),
            FieldSpec("capture_s", "Capture", "s", "float", 0.0, 3600.0, 0.0),
            tol)),
        "hold": GeneratorSchema("hold", "Hold / creep–relaxation", "go to the target, hold and capture throughout", (
            kind,
            FieldSpec("target", "Target", "mm | N", "float", -tgt_max, tgt_max, 1.0),
            FieldSpec("duration_s", "Hold duration", "s", "float", 0.1, 86400.0, 60.0),
            *common(),
            tol)),
        "return_": GeneratorSchema("return_", "Return", "travel 0 (test zero) or home", (
            FieldSpec("to", "Return to", "", "enum", default="zero", choices=("zero", "home")),
            FieldSpec("speed_mm_s", "Speed", "mm/s", "float", 1e-3, vt, min(2.0, vt), depends_on=("to", "zero")),
            FieldSpec("accel_mm_s2", "Ramp (0 = FW default)", "mm/s²", "float", 0.0, c.a_max_mm_s2, 0.0,
                      depends_on=("to", "zero")))),
    }


def generate(name: str, params: Mapping[str, Any] | None = None, *, ctx: SchemaContext | None = None,
             **kw: Any) -> Block:
    """Check ``params`` (+ keyword arguments) against the schema, then call the generator."""
    schemas = generator_schemas(ctx)
    if name not in schemas:
        raise ValueError(f"unknown generator {name!r}")
    p = dict(params or {})
    p.update(kw)
    sc = schemas[name]
    args: dict[str, Any] = {}
    names = {f.name for f in sc.fields}
    unknown = [k for k in p if k not in names]
    if unknown:
        raise ValueError(f"{unknown[0]}: unknown parameter of {name}")
    for f in sc.fields:
        if f.depends_on is not None:
            dep, val = f.depends_on
            if p.get(dep, next((g.default for g in sc.fields if g.name == dep), None)) != val:
                continue
        v = p.get(f.name, f.default)
        if v is None:
            args[f.name] = None
            continue
        if f.kind == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"{f.name}: true / false expected")
        elif f.kind == "enum":
            if v not in f.choices:
                raise ValueError(f"{f.name}: one of {', '.join(f.choices)}")
        elif f.kind == "int":
            if isinstance(v, bool) or not isinstance(v, int):
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                else:
                    raise ValueError(f"{f.name}: integer expected")
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise ValueError(f"{f.name}: number expected")
            v = float(v)
        if f.kind in ("int", "float") and ((f.min is not None and v < f.min) or (f.max is not None and v > f.max)):
            raise ValueError(f"{f.name}: {v:g} outside {f.min:g}…{f.max:g} {f.unit}".rstrip())
        args[f.name] = v
    if name == "staircase":
        by = args.pop("by", "increment")
        if by == "increment":
            args.pop("count", None)
        else:
            args.pop("increment", None)
    return GENERATORS[name](**args)


__all__ = ["FieldSpec", "GeneratorSchema", "SchemaContext", "staircase", "staircase_levels", "linear_ramp", "cyclic",
           "hold", "return_", "GENERATORS", "generator_schemas", "generate"]
