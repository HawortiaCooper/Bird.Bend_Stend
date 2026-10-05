"""Sequence model and validation (SW_design §10.1, §15.5f B6-01/B6-02; SRS SW-SEQ-001/002, SAF-SW-006).

Plain (mutable) dataclasses the GUI edits field by field: ``Step``, ``Loop``, ``StepDefaults``, ``Sequence``,
``Block`` (generator output). TRAVEL targets are in the sequence's travel reference — relative to the test travel zero
``x_zero`` frozen at the sequence start (``travel_ref = "test"``, default, D-29 k) or machine millimetres. LOAD targets
are newtons (+ = tension when ``pull_dir`` moves toward tension).

``validate(seq, ctx)`` is pure and returns ``SeqIssue(step_uid, field, severity, code, text)`` items (ERROR / WARN);
``ctx`` (``ValidationContext``) carries the board / session values the rules need (caps, travel range, SW load trips,
FW level, largest calibration force); without a context only the context-free rules run.

Implements: SW-SEQ-001 (step table + field validation), SW-SEQ-002 (loops: count 1…10 000 or until stopped, one
nesting level), SW-WIZ-002 (``insert_block``), SAF-SW-006 (margin WARN per moving step)
"""
from __future__ import annotations

import copy
import math
import uuid
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Literal

from bend_stand.calc.loadcal import EXTRAPOLATION_FACTOR
from bend_stand.calc.limits import limit_margin_warning
from bend_stand.calc.trim import approach_band
from bend_stand.core.model import IssueSeverity

E, W = IssueSeverity.ERROR, IssueSeverity.WARN
LOOP_COUNT_MAX = 10_000
MAX_TIME_S = 1_000_000.0
LABEL_MAX = 80
MAX_PLAN_STEPS = 200_000


class StepKind(StrEnum):
    TRAVEL = "travel"
    LOAD = "load"
    HOLD = "hold"
    HOME = "home"
    TARE = "tare"
    MARK = "mark"


MOVING_KINDS = frozenset({StepKind.TRAVEL, StepKind.LOAD})


def new_uid() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Step:
    uid: str
    kind: StepKind
    target: float | None = None            # mm (TRAVEL, sequence travel reference) or N (LOAD)
    speed_mm_s: float | None = None        # None = defaults.speed_mm_s; LOAD: approach speed
    accel_mm_s2: float = 0.0               # 0 = FW default (motion.a_max_um_s2)
    settle_s: float = 0.0
    capture_s: float = 0.0                 # VALID window length; 0 = no report window
    step_time_s: float = 0.0               # TRAVEL / HOLD: minimum dwell from t_reached; unused for LOAD
    tol_n: float | None = None             # LOAD tolerance; None = defaults.tol_n
    capture_during_move: bool = False      # TRAVEL ramp step: VALID = 1 during the move (no steady state)
    wait_operator: bool = False            # MARK: wait for continue_()
    label: str = ""


@dataclass
class Loop:
    first: int                             # inclusive step indices
    last: int
    count: int = 1                         # 1…10 000; 0 = until stopped


@dataclass
class StepDefaults:
    speed_mm_s: float = 1.0
    accel_mm_s2: float = 0.0
    settle_s: float = 1.0
    capture_s: float = 2.0
    tol_n: float = 2.0


@dataclass
class StepBlock:
    """Generator output (SW-WIZ-001): steps + loops with indices relative to the block. Exported as ``Block`` (the
    class itself is not named ``Block``, which is reserved for the generated BLOCK-mask enum, TC-IF-001-01)."""

    steps: list[Step] = field(default_factory=list)
    loops: list[Loop] = field(default_factory=list)


Block = StepBlock


@dataclass
class Sequence:
    name: str = ""
    steps: list[Step] = field(default_factory=list)
    loops: list[Loop] = field(default_factory=list)
    travel_ref: Literal["test", "machine"] = "test"
    k_est_n_mm: float = 50.0
    pull_dir: int = 1
    defaults: StepDefaults = field(default_factory=StepDefaults)
    notes: str = ""

    def copy(self) -> Sequence:
        return copy.deepcopy(self)

    def speed(self, s: Step) -> float:
        return float(self.defaults.speed_mm_s if s.speed_mm_s is None else s.speed_mm_s)

    def tol(self, s: Step) -> float:
        return float(self.defaults.tol_n if s.tol_n is None else s.tol_n)

    def insert_block(self, block: Block, mode: Literal["append", "insert", "replace"] = "append",
                     index: int | None = None) -> None:
        """SW-WIZ-002: insert generated steps (editable) — append, insert before ``index`` or replace all."""
        new = [replace(s) for s in block.steps]
        uids = {s.uid for s in self.steps} if mode != "replace" else set()
        for s in new:
            if not s.uid or s.uid in uids:
                s.uid = new_uid()
            uids.add(s.uid)
        if mode == "replace":
            self.steps = new
            self.loops = [replace(lp) for lp in block.loops]
            return
        at = len(self.steps) if mode == "append" or index is None else max(0, min(int(index), len(self.steps)))
        n = len(new)
        loops = []
        for lp in self.loops:
            if at <= lp.first:
                loops.append(replace(lp, first=lp.first + n, last=lp.last + n))
            elif at <= lp.last:
                loops.append(replace(lp, last=lp.last + n))
            else:
                loops.append(replace(lp))
        loops += [replace(lp, first=lp.first + at, last=lp.last + at) for lp in block.loops]
        self.steps = self.steps[:at] + new + self.steps[at:]
        self.loops = sorted(loops, key=lambda lp: (lp.first, -lp.last))


    def delete_steps(self, rows: list[int] | tuple[int, ...]) -> None:
        """GRQ-B-28: delete step rows; a loop shrinks by the deleted rows inside it, shifts by those before it and
        disappears when all its steps are deleted."""
        gone = sorted({int(r) for r in rows if 0 <= int(r) < len(self.steps)})
        loops = []
        for lp in self.loops:
            inside = sum(1 for r in gone if lp.first <= r <= lp.last)
            before = sum(1 for r in gone if r < lp.first)
            if inside == lp.last - lp.first + 1:
                continue
            loops.append(replace(lp, first=lp.first - before, last=lp.last - before - inside))
        self.steps = [s for i, s in enumerate(self.steps) if i not in gone]
        self.loops = loops

    def move_step(self, row: int, delta: int) -> int:
        """GRQ-B-28: swap a step with its neighbour (``delta`` ±1); loops keep their index ranges (a step can move
        into / out of a loop). Returns the new row."""
        to = row + delta
        if not (0 <= row < len(self.steps) and 0 <= to < len(self.steps)) or abs(delta) != 1:
            return row
        self.steps[row], self.steps[to] = self.steps[to], self.steps[row]
        return to

    def duplicate_steps(self, rows: list[int] | tuple[int, ...]) -> list[int]:
        """GRQ-B-28: copies (new uids) of the selected steps inserted after the last selected row (inside its loop if
        that row is in one); returns the new rows."""
        sel = sorted({int(r) for r in rows if 0 <= int(r) < len(self.steps)})
        if not sel:
            return []
        at = sel[-1] + 1
        copies = [replace(self.steps[r], uid=new_uid()) for r in sel]
        loops = []
        for lp in self.loops:
            if at <= lp.first:
                loops.append(replace(lp, first=lp.first + len(copies), last=lp.last + len(copies)))
            elif at <= lp.last + 1 and lp.first <= sel[-1] <= lp.last:
                loops.append(replace(lp, last=lp.last + len(copies)))
            else:
                loops.append(replace(lp))
        self.steps = self.steps[:at] + copies + self.steps[at:]
        self.loops = loops
        return list(range(at, at + len(copies)))


@dataclass(frozen=True)
class SeqIssue:
    """Validation finding (B6-02); ``step_uid`` None = sequence level (loops: ``field`` = ``loops[i]``)."""

    step_uid: str | None
    field: str | None
    severity: IssueSeverity
    code: str
    text: str

    @property
    def key(self) -> str:
        return ".".join(p for p in (self.step_uid, self.field) if p)


@dataclass(frozen=True)
class ValidationContext:
    """Board / session values for the context rules (all optional)."""

    v_travel_cap_mm_s: float | None = None     # cap that applies to TRAVEL steps now (loaded → v_load)
    v_load_cap_mm_s: float | None = None       # MOVE_UNTIL_LOAD / trim cap (D-29 e)
    a_max_mm_s2: float | None = None
    travel_lo_mm: float | None = None          # machine mm: FW soft limits ∩ enabled SW travel limits
    travel_hi_mm: float | None = None
    x_zero_mm: float = 0.0
    pull_trip_n: float | None = None           # enabled SW load trips (None = off)
    push_trip_n: float | None = None
    fw_level_n: float | None = None
    f_cal_max_n: float | None = None
    margin_check: bool = False                 # SAF-SW-006 applies (load limits active, not no-specimen mode)


def _finite(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def machine_mm(seq: Sequence, target_mm: float, x_zero_mm: float) -> float:
    """Sequence travel coordinate → machine mm."""
    return float(target_mm) + (float(x_zero_mm) if seq.travel_ref == "test" else 0.0)


def loop_depths(loops: list[Loop]) -> list[int]:
    """Nesting depth of every loop (1 = outermost)."""
    out = []
    for i, a in enumerate(loops):
        d = 1 + sum(1 for j, b in enumerate(loops) if j != i and b.first <= a.first and a.last <= b.last
                    and (b.first, b.last) != (a.first, a.last))
        out.append(d)
    return out


def validate_loops(seq: Sequence) -> list[SeqIssue]:
    out: list[SeqIssue] = []
    n = len(seq.steps)
    ok: list[tuple[int, Loop]] = []
    for i, lp in enumerate(seq.loops):
        f = f"loops[{i}]"
        if not isinstance(lp.count, int) or isinstance(lp.count, bool) or not 0 <= lp.count <= LOOP_COUNT_MAX:
            out.append(SeqIssue(None, f, E, "LOOP_COUNT", f"loop {i + 1}: count must be 1…{LOOP_COUNT_MAX} or 0 "
                                                           f"(until stopped)"))
        if not (isinstance(lp.first, int) and isinstance(lp.last, int) and 0 <= lp.first <= lp.last < n):
            out.append(SeqIssue(None, f, E, "LOOP_RANGE", f"loop {i + 1}: steps {lp.first + 1}…{lp.last + 1} outside "
                                                           f"the step table (1…{n})"))
            continue
        ok.append((i, lp))
    for a_i, (i, a) in enumerate(ok):
        for j, b in ok[a_i + 1:]:
            if (a.first, a.last) == (b.first, b.last):
                out.append(SeqIssue(None, f"loops[{j}]", E, "LOOP_DUPLICATE", f"loops {i + 1} and {j + 1} cover the "
                                                                               "same steps"))
            elif not (a.last < b.first or b.last < a.first or (a.first <= b.first and b.last <= a.last)
                      or (b.first <= a.first and a.last <= b.last)):
                out.append(SeqIssue(None, f"loops[{j}]", E, "LOOP_OVERLAP", f"loops {i + 1} and {j + 1} overlap "
                                                                             "(must be disjoint or nested)"))
    lps = [lp for _i, lp in ok]
    for (i, lp), d in zip(ok, loop_depths(lps), strict=True):
        if d > 2:
            out.append(SeqIssue(None, f"loops[{i}]", E, "LOOP_DEPTH", f"loop {i + 1}: nesting deeper than one level"))
        if lp.count == 0 and d > 1:
            out.append(SeqIssue(None, f"loops[{i}]", E, "LOOP_INFINITE_INNER", f"loop {i + 1}: an until-stopped loop "
                                                                                "must not be nested in another loop"))
        if lp.count == 0 and d == 1 and lp.last < n - 1:
            out.append(SeqIssue(None, f"loops[{i}]", W, "UNREACHABLE", f"loop {i + 1} runs until stopped: steps "
                                                                         f"{lp.last + 2}…{n} are never reached"))
    return out


def validate(seq: Sequence, ctx: ValidationContext | None = None) -> list[SeqIssue]:
    """Editor validation (SW-SEQ-001/002); ``ctx`` adds the caps / travel range / load-limit / margin rules."""
    out: list[SeqIssue] = []
    c = ctx or ValidationContext()
    if seq.travel_ref not in ("test", "machine"):
        out.append(SeqIssue(None, "travel_ref", E, "ENUM", "travel reference must be test or machine"))
    if seq.pull_dir not in (1, -1):
        out.append(SeqIssue(None, "pull_dir", E, "ENUM", "pull direction must be +1 or −1"))
    if not _finite(seq.k_est_n_mm) or not seq.k_est_n_mm > 0:
        out.append(SeqIssue(None, "k_est_n_mm", E, "RANGE", "k_est must be > 0 N/mm"))
    d = seq.defaults
    for name, lo in (("speed_mm_s", 1e-6), ("accel_mm_s2", 0.0), ("settle_s", 0.0), ("capture_s", 0.0),
                     ("tol_n", 1e-6)):
        v = getattr(d, name)
        if not _finite(v) or v < lo or v > MAX_TIME_S:
            out.append(SeqIssue(None, f"defaults.{name}", E, "RANGE", f"default {name} = {v!r} invalid"))
    if not seq.steps:
        out.append(SeqIssue(None, "steps", E, "EMPTY", "the sequence has no steps"))
    seen: set[str] = set()
    k_est = float(seq.k_est_n_mm) if _finite(seq.k_est_n_mm) and seq.k_est_n_mm > 0 else None
    for s in seq.steps:
        out += _validate_step(seq, s, c, k_est, seen)
    out += validate_loops(seq)
    if not any(i.code.startswith("LOOP") and i.severity == E for i in out) and seq.steps:
        from bend_stand.core.sequencer.plan import count_exec  # noqa: PLC0415  (plan imports this module)

        n = count_exec(seq)
        if n > MAX_PLAN_STEPS:
            out.append(SeqIssue(None, "loops", E, "PLAN_TOO_LONG", f"{n} executed steps > {MAX_PLAN_STEPS} "
                                                                    "(reduce the loop counts)"))
    return out


def _validate_step(seq: Sequence, s: Step, c: ValidationContext, k_est: float | None, seen: set[str]) -> list[SeqIssue]:
    out: list[SeqIssue] = []
    u = s.uid

    def err(fld: str | None, code: str, text: str, sev: IssueSeverity = E) -> None:
        out.append(SeqIssue(u, fld, sev, code, text))

    if not u:
        err("uid", "MISSING", "step without uid")
    elif u in seen:
        err("uid", "DUPLICATE_UID", f"duplicate step uid {u!r}")
    seen.add(u)
    try:
        kind = StepKind(s.kind)
    except ValueError:
        err("kind", "ENUM", f"unknown step type {s.kind!r}")
        return out
    for name in ("settle_s", "capture_s", "step_time_s", "accel_mm_s2"):
        v = getattr(s, name)
        if not _finite(v) or v < 0 or v > MAX_TIME_S:
            err(name, "RANGE", f"{name} = {v!r} must be ≥ 0")
    if len(s.label or "") > LABEL_MAX:
        err("label", "RANGE", f"label longer than {LABEL_MAX} characters")
    if kind in MOVING_KINDS:
        if s.target is None or not _finite(s.target):
            err("target", "MISSING", "target required" + (" (mm)" if kind == StepKind.TRAVEL else " (N)"))
        v = seq.speed(s)
        cap = c.v_load_cap_mm_s if kind == StepKind.LOAD else c.v_travel_cap_mm_s
        if not _finite(v) or v <= 0:
            err("speed_mm_s", "SPEED_CAP", "speed must be > 0")
        elif cap is not None and v > cap + 1e-9:
            err("speed_mm_s", "SPEED_CAP", f"speed {v:g} mm/s above the cap {cap:g} mm/s"
                + (" (load steps: v_max_load, D-29 e)" if kind == StepKind.LOAD else ""))
        if c.a_max_mm_s2 is not None and _finite(s.accel_mm_s2) and s.accel_mm_s2 > c.a_max_mm_s2 + 1e-9:
            err("accel_mm_s2", "ACCEL_CAP", f"acceleration {s.accel_mm_s2:g} mm/s² above {c.a_max_mm_s2:g} mm/s²")
        if c.margin_check and k_est is not None and _finite(v) and v > 0 and c.fw_level_n is not None:
            trips = [abs(t) for t in (c.pull_trip_n, c.push_trip_n) if t is not None]
            if trips and limit_margin_warning(k_est, v, c.fw_level_n, max(trips)):
                err("speed_mm_s", "SAF_SW_006_MARGIN", f"speed {v:g} mm/s too high for the limit margin "
                    f"{c.fw_level_n - max(trips):.0f} N (overshoot ≈ {k_est * v * 0.065:.0f} N)", W)
    if kind == StepKind.TRAVEL and s.target is not None and _finite(s.target):
        xm = machine_mm(seq, s.target, c.x_zero_mm)
        if (c.travel_lo_mm is not None and xm < c.travel_lo_mm - 1e-9) or \
                (c.travel_hi_mm is not None and xm > c.travel_hi_mm + 1e-9):
            lo = "−∞" if c.travel_lo_mm is None else f"{c.travel_lo_mm:g}"
            hi = "∞" if c.travel_hi_mm is None else f"{c.travel_hi_mm:g}"
            err("target", "TARGET_OUT_OF_RANGE", f"target {s.target:g} mm → machine {xm:g} mm outside the travel "
                f"range {lo}…{hi} mm")
    if kind == StepKind.LOAD:
        tol = seq.tol(s)
        if not _finite(tol) or tol <= 0:
            err("tol_n", "RANGE", "load tolerance must be > 0 N")
        if s.capture_during_move:
            err("capture_during_move", "NOT_APPLICABLE", "capture during move is for travel steps only")
        if s.target is not None and _finite(s.target) and k_est is not None and _finite(tol) and tol > 0:
            band = approach_band(tol, k_est, seq.speed(s)) if _finite(seq.speed(s)) else tol
            trip = c.pull_trip_n if s.target > 0 else c.push_trip_n
            if trip is not None and abs(s.target) + band >= abs(trip):
                err("target", "LOAD_LIMIT", f"target {s.target:g} N + approach band {band:.1f} N reaches the SW load "
                    f"limit {trip:g} N")
            if c.f_cal_max_n is not None and abs(s.target) > EXTRAPOLATION_FACTOR * c.f_cal_max_n:
                err("target", "EXTRAPOLATED", f"target {s.target:g} N beyond 3 × the largest calibration force "
                    f"({c.f_cal_max_n:g} N)", W)
    if kind not in MOVING_KINDS and kind != StepKind.HOLD and (s.capture_s or s.settle_s):
        err("capture_s", "NOT_APPLICABLE", f"{kind.value} steps have no capture window", W)
    if kind == StepKind.HOLD and not (s.capture_s or s.settle_s or s.step_time_s):
        err("step_time_s", "RANGE", "hold step without settle / capture / step time", W)
    if s.wait_operator and kind != StepKind.MARK:
        err("wait_operator", "NOT_APPLICABLE", "wait for operator applies to mark steps", W)
    return out


__all__ = ["StepKind", "Step", "Loop", "StepDefaults", "Block", "StepBlock", "Sequence", "SeqIssue", "ValidationContext",
           "validate", "validate_loops", "loop_depths", "machine_mm", "new_uid", "MOVING_KINDS", "LOOP_COUNT_MAX",
           "MAX_PLAN_STEPS"]
