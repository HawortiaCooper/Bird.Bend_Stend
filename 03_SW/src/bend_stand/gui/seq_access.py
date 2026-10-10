"""Adapter between the GUI and the backend sequencer / report API (SW_design_GUI §3.6, §3.7, §15.4 WP-D16).

The GUI imports only ``core.api`` (G-01). The sequencer model (``Sequence``, ``Step``, ``Loop``, ``StepKind``),
plan / path records, generator schemas and report results are B's dataclasses (SW_design §10, §11); this module is
the **one place** where their names are resolved, so B's final API (§15.5f) lands here only:

* **types:** ``core.api`` exports (``Step``, ``Loop``, ``StepKind``) when present, else the field annotations of the
  object returned by ``sequencer.new()`` (``typing.get_type_hints`` — no import of the backend module);
* **issues → cells:** ``Issue`` with ``step_uid`` / ``field`` attributes, or a ``key`` of the form
  ``<uid>.<field>``, ``steps[<i>].<field>``, ``step:<uid>:<field>``; everything else is a sequence-level issue;
* **generators:** ``sequencer.generate(name, **params)``, ``sequencer.generators[name](**params)`` or
  ``getattr(sequencer.generators, name)``;
* **plan / path / status / results:** attribute or mapping access with the §10 / §11 field names.

Editing helpers (insert / delete / move / loop wrap) are pure list operations on the step list with the
loop-index bookkeeping (``Loop(first, last, count)`` are inclusive step indices); the GUI never expands, times or
validates a sequence itself — validation, plan, path and every rule stay in the backend (P1).

Implements: SW-SEQ-001 (step fields, editor support), SW-SEQ-002 (loops, one nesting level shown), SW-WIZ-002
(insert append / insert / replace), SW-SCH-001 (planned path access), SW-REP-002 (step result rows)
"""
from __future__ import annotations

import copy
import dataclasses
import math
import typing
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from bend_stand.core import api

STEP_KINDS: tuple[str, ...] = ("travel", "load", "hold", "home", "tare", "mark")

#: editor columns: (field, header). "unit" and "issues" are display-only columns.
FIELDS: tuple[tuple[str, str], ...] = (
    ("kind", "Type"), ("target", "Target"), ("unit", "U"), ("speed_mm_s", "v mm/s"), ("accel_mm_s2", "a mm/s²"),
    ("settle_s", "Settle s"), ("capture_s", "Capture s"), ("step_time_s", "Time s"), ("tol_n", "Tol"),
    ("label", "Label"), ("issues", "!"),
)
ADVANCED: tuple[tuple[str, str], ...] = (("capture_during_move", "Capture in move"), ("wait_operator", "Wait op."))

#: display applicability (kind → editable fields); cross-checked against the backend validation by a test
APPLIES: Mapping[str, frozenset[str]] = {
    "travel": frozenset({"target", "speed_mm_s", "accel_mm_s2", "settle_s", "capture_s", "step_time_s", "label",
                         "capture_during_move"}),
    # LOAD: step time hidden — the timeout is the backend's (1.2 × travel time to the bound + 10 s, B3-07, D-33 d)
    "load": frozenset({"target", "speed_mm_s", "accel_mm_s2", "settle_s", "capture_s", "tol_n", "label"}),
    "hold": frozenset({"settle_s", "capture_s", "step_time_s", "label"}),
    "home": frozenset({"label"}),
    "tare": frozenset({"label"}),
    "mark": frozenset({"label", "wait_operator"}),
}
FLOAT_FIELDS = frozenset({"target", "speed_mm_s", "accel_mm_s2", "settle_s", "capture_s", "step_time_s", "tol_n"})
BOOL_FIELDS = frozenset({"capture_during_move", "wait_operator"})
OPTIONAL_FIELDS = frozenset({"target", "speed_mm_s", "tol_n"})      # empty cell = None (sequence default)

ACTIVE_STATES = frozenset({"PREPARING", "RUNNING", "PAUSED", "WAITING_OPERATOR", "STOPPING"})
END_STATES = frozenset({"FINISHED", "STOPPED", "ABORTED", "ERROR", "ENDED"})


# ============================================================================================ generic access
def get(obj: Any, *names: str, default: Any = None) -> Any:
    """First present attribute / mapping key of ``names`` (``None`` values are skipped)."""
    for n in names:
        if obj is None:
            return default
        v = obj.get(n) if isinstance(obj, Mapping) else getattr(obj, n, None)
        if v is not None:
            return v
    return default


def text_of(v: Any) -> str:
    """Enum / str / None → display text (enum value, upper-case states stay as given)."""
    if v is None:
        return ""
    return str(getattr(v, "value", v))


def kind_of(step: Any) -> str:
    return text_of(get(step, "kind")).lower()


def finite(v: Any) -> bool:
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


# ============================================================================================ types
class SeqTypes:
    """Resolved model classes (``Step``, ``Loop``, ``StepKind``) of the backend's sequence object."""

    def __init__(self, sample: Any) -> None:
        self.step_cls = getattr(api, "Step", None)
        self.loop_cls = getattr(api, "Loop", None)
        self.kind_enum = getattr(api, "StepKind", None)
        if sample is not None and (self.step_cls is None or self.loop_cls is None):
            hints = _hints(type(sample))
            self.step_cls = self.step_cls or _list_arg(hints.get("steps"))
            self.loop_cls = self.loop_cls or _list_arg(hints.get("loops"))
        if self.kind_enum is None and self.step_cls is not None:
            k = _hints(self.step_cls).get("kind")
            self.kind_enum = k if isinstance(k, type) else None

    def kind(self, name: str) -> Any:
        if self.kind_enum is None:
            return name
        try:
            return self.kind_enum(name)
        except ValueError:
            return self.kind_enum[name.upper()]

    def make_step(self, kind: str, **fields: Any) -> Any:
        if self.step_cls is None:
            raise TypeError("the backend sequence model is not available")
        names = {f.name: f for f in dataclasses.fields(self.step_cls)} if dataclasses.is_dataclass(self.step_cls) \
            else {}
        kw = {k: v for k, v in fields.items() if not names or k in names}
        if "uid" in names and "uid" not in kw:
            kw["uid"] = new_uid()
        return self.step_cls(kind=self.kind(kind), **kw)

    def make_loop(self, first: int, last: int, count: int) -> Any:
        if self.loop_cls is None:
            raise TypeError("the backend loop model is not available")
        return self.loop_cls(first=first, last=last, count=count)


def _hints(cls: type) -> dict[str, Any]:
    try:
        return typing.get_type_hints(cls)
    except Exception:  # noqa: BLE001 - unresolvable forward references: no types
        return {}


def _list_arg(tp: Any) -> Any:
    args = typing.get_args(tp)
    return args[0] if args else None


def new_uid() -> str:
    return uuid.uuid4().hex[:8]


# ============================================================================================ editing (pure)
def steps_of(seq: Any) -> list[Any]:
    return list(get(seq, "steps", default=()) or ())


def loops_of(seq: Any) -> list[Any]:
    return list(get(seq, "loops", default=()) or ())


def loop_span(loop: Any) -> tuple[int, int, int]:
    return int(get(loop, "first", default=0)), int(get(loop, "last", default=0)), int(get(loop, "count", default=1))


def set_lists(seq: Any, steps: list[Any], loops: list[Any]) -> None:
    seq.steps = list(steps)
    seq.loops = list(loops)


def _relabel_loop(loop: Any, first: int, last: int) -> Any:
    if dataclasses.is_dataclass(loop):
        return dataclasses.replace(loop, first=first, last=last)
    loop.first, loop.last = first, last
    return loop


def loops_after_delete(loops: Iterable[Any], rows: Iterable[int]) -> list[Any]:
    """Loops after deleting step rows: a loop shrinks by the deleted rows inside it, shifts by those before it, and
    disappears when all its steps are deleted."""
    gone = sorted(set(rows))
    out = []
    for lp in loops:
        first, last, _c = loop_span(lp)
        inside = [r for r in gone if first <= r <= last]
        before = [r for r in gone if r < first]
        if len(inside) == last - first + 1:
            continue
        out.append(_relabel_loop(lp, first - len(before), last - len(before) - len(inside)))
    return out


def loops_after_insert(loops: Iterable[Any], at: int, n: int) -> list[Any]:
    """Loops after inserting ``n`` steps before row ``at``: rows inside a loop grow it, rows before it shift it."""
    out = []
    for lp in loops:
        first, last, _c = loop_span(lp)
        if at <= first:
            out.append(_relabel_loop(lp, first + n, last + n))
        elif at <= last:
            out.append(_relabel_loop(lp, first, last + n))
        else:
            out.append(lp)
    return out


def insert_steps(seq: Any, at: int, new_steps: list[Any]) -> None:
    steps = steps_of(seq)
    at = max(0, min(at, len(steps)))
    set_lists(seq, steps[:at] + list(new_steps) + steps[at:], loops_after_insert(loops_of(seq), at, len(new_steps)))


def delete_rows(seq: Any, rows: Iterable[int]) -> None:
    rows = sorted(set(rows))
    steps = [s for i, s in enumerate(steps_of(seq)) if i not in rows]
    set_lists(seq, steps, loops_after_delete(loops_of(seq), rows))


def move_row(seq: Any, row: int, delta: int) -> int:
    """Swap a step with its neighbour (loops keep their index ranges: a step can move into / out of a loop)."""
    steps = steps_of(seq)
    to = row + delta
    if not (0 <= row < len(steps) and 0 <= to < len(steps)):
        return row
    steps[row], steps[to] = steps[to], steps[row]
    set_lists(seq, steps, loops_of(seq))
    return to


def duplicate_rows(seq: Any, rows: Iterable[int]) -> list[int]:
    """Copies of the selected steps (new uids) inserted after the last selected row; returns the new rows."""
    rows = sorted(set(rows))
    if not rows:
        return []
    steps = steps_of(seq)
    copies = []
    for r in rows:
        c = copy.deepcopy(steps[r])
        if hasattr(c, "uid"):
            c = dataclasses.replace(c, uid=new_uid()) if dataclasses.is_dataclass(c) else c
        copies.append(c)
    at = rows[-1] + 1
    insert_steps(seq, at, copies)
    return list(range(at, at + len(copies)))


def loop_depth(loops: Iterable[Any], row: int) -> int:
    return sum(1 for lp in loops if loop_span(lp)[0] <= row <= loop_span(lp)[1])


def wrap_loop(seq: Any, types: SeqTypes, first: int, last: int, count: int) -> None:
    """Add ``Loop(first, last, count)``; nesting / overlap rules are the backend's validation (one level)."""
    loops = loops_of(seq) + [types.make_loop(first, last, count)]
    loops.sort(key=lambda lp: (loop_span(lp)[0], -loop_span(lp)[1]))
    set_lists(seq, steps_of(seq), loops)


def unwrap_loop(seq: Any, row: int) -> bool:
    """Remove the innermost loop containing ``row``."""
    loops = loops_of(seq)
    hits = [lp for lp in loops if loop_span(lp)[0] <= row <= loop_span(lp)[1]]
    if not hits:
        return False
    inner = min(hits, key=lambda lp: loop_span(lp)[1] - loop_span(lp)[0])
    loops.remove(inner)
    set_lists(seq, steps_of(seq), loops)
    return True


def gutter_text(loops: Iterable[Any], row: int) -> str:
    """Loop bracket in the row header: ``┌×3`` at the first row, ``│`` inside, ``└`` at the last, ``∞`` for 0."""
    parts = []
    for lp in sorted(loops, key=lambda lp: (loop_span(lp)[0], -loop_span(lp)[1])):
        first, last, count = loop_span(lp)
        if not first <= row <= last:
            continue
        c = "∞" if count == 0 else f"×{count}"
        if first == last:
            parts.append(f"[{c}")
        elif row == first:
            parts.append(f"┌{c}")
        elif row == last:
            parts.append("└")
        else:
            parts.append("│")
    return " ".join(parts)


def insert_block(seq: Any, types: SeqTypes, block: Any, mode: str, index: int | None = None) -> None:
    """Insert a generator ``Block(steps, loops)`` (SW-WIZ-002): ``Sequence.insert_block`` when the backend has
    it, else the same list operation here (block loops re-indexed to the insertion point)."""
    fn = getattr(seq, "insert_block", None)
    if callable(fn):
        try:
            fn(block, mode=mode, index=index)
            return
        except TypeError:
            fn(block, mode, index)
            return
    b_steps = [copy.deepcopy(s) for s in steps_of(block)]
    b_loops = loops_of(block)
    if mode == "replace":
        set_lists(seq, [], [])
        at = 0
    elif mode == "insert" and index is not None:
        at = index
    else:
        at = len(steps_of(seq))
    insert_steps(seq, at, b_steps)
    loops = loops_of(seq)
    for lp in b_loops:
        first, last, count = loop_span(lp)
        loops.append(types.make_loop(first + at, last + at, count))
    loops.sort(key=lambda lp: (loop_span(lp)[0], -loop_span(lp)[1]))
    set_lists(seq, steps_of(seq), loops)


def replace_step(seq: Any, row: int, **fields: Any) -> None:
    steps = steps_of(seq)
    s = steps[row]
    if dataclasses.is_dataclass(s):
        steps[row] = dataclasses.replace(s, **fields)
    else:
        for k, v in fields.items():
            setattr(s, k, v)
    set_lists(seq, steps, loops_of(seq))


def snapshot(seq: Any) -> Any:
    return copy.deepcopy(seq)


# ============================================================================================ issues
@dataclasses.dataclass(frozen=True)
class CellIssue:
    uid: str | None
    field: str | None
    severity: str            # "ERROR" | "WARN"
    text: str


def severity_text(v: Any) -> str:
    s = text_of(v).upper()
    return "ERROR" if s in ("ERROR", "REFUSE") else ("WARN" if s.startswith("WARN") else s or "ERROR")


def cell_issues(issues: Iterable[Any], steps: Sequence[Any]) -> list[CellIssue]:
    """Map backend issues to (step uid, field); sequence-level issues keep ``uid = None``."""
    uids = [get(s, "uid") for s in steps]
    out = []
    for i in issues or ():
        uid = get(i, "step_uid", "uid")
        fld = get(i, "field")
        key = get(i, "key")
        if uid is None and isinstance(key, str):
            uid, fld2 = _parse_key(key, uids)
            fld = fld or fld2
        out.append(CellIssue(None if uid is None else str(uid), fld, severity_text(get(i, "severity")),
                             str(get(i, "text", default="") or get(i, "code", default=""))))
    return out


def _parse_key(key: str, uids: Sequence[Any]) -> tuple[str | None, str | None]:
    k = key.strip()
    if k.startswith("steps[") and "]" in k:
        try:
            idx = int(k[6:k.index("]")])
        except ValueError:
            return None, None
        rest = k[k.index("]") + 1:].lstrip(".") or None
        return (str(uids[idx]) if 0 <= idx < len(uids) else None), rest
    for sep in (":", "."):
        if k.startswith("step" + sep):
            k = k[5:]
        parts = k.split(sep)
        if parts[0] in {str(u) for u in uids}:
            return parts[0], (parts[1] if len(parts) > 1 and parts[1] else None)
    return None, None


# ============================================================================================ plan / path
@dataclasses.dataclass(frozen=True)
class PlanSummary:
    n_exec: int
    total_s: float | None          # None = infinite (count 0 loop)
    x_range: tuple[float, float] | None
    f_range: tuple[float, float] | None
    windows: int


def plan_steps(plan: Any) -> list[Any]:
    if plan is None:
        return []
    inner = get(plan, "steps", "items")
    return list(inner if inner is not None else plan) if not isinstance(plan, (str, bytes)) else []


def plan_summary(plan: Any) -> PlanSummary:
    ps = plan_steps(plan)
    infinite = any(bool(get(p, "infinite", default=False)) for p in ps) or bool(get(plan, "infinite", default=False))
    total = get(plan, "total_s", "plan_total_s")
    if total is None and ps:
        total = max((float(get(p, "t_end_s", default=0.0)) for p in ps), default=0.0)
    xs = [float(v) for p in ps for v in (get(p, "x_target_mm"),) if finite(v)]
    fs = [float(v) for p in ps for v in (get(p, "f_target_n"),) if finite(v)]
    win = sum(1 for p in ps if get(p, "capture") is not None)
    return PlanSummary(len(ps), None if infinite else (float(total) if total is not None else None),
                       (min(xs), max(xs)) if xs else None, (min(fs), max(fs)) if fs else None, win)


@dataclasses.dataclass(frozen=True)
class PathPt:
    x: float
    f: float
    exec_idx: int | None
    label: str
    known: str               # "x" | "F" | "both" | "" (break)
    capture: bool = False


def path_points(path: Any, plan: Any = None) -> list[PathPt]:
    """``PathPoint(x_mm, f_n, exec_idx, label, known)`` list → display points; a point with non-finite x or F is
    a break of the polyline (HOME). ``capture`` from the plan (``PlannedStep.capture``)."""
    caps = {int(get(p, "exec_idx", default=-1)) for p in plan_steps(plan) if get(p, "capture") is not None}
    pts = []
    raw = path if isinstance(path, (list, tuple)) or path is None else (get(path, "points") or path)
    for p in raw or ():
        x, f = get(p, "x_mm", "x"), get(p, "f_n", "f")
        ei = get(p, "exec_idx")
        ei = None if ei is None else int(ei)
        label = str(get(p, "label", default=""))
        if bool(get(p, "brk", default=False)) and pts:          # B6-03: break before this point (after HOME)
            pts.append(PathPt(math.nan, math.nan, None, "", ""))
        known = text_of(get(p, "known", default="both"))
        if not (finite(x) and finite(f)):
            pts.append(PathPt(math.nan, math.nan, ei, label, ""))
            continue
        cap = get(p, "capture")
        cap = bool(cap) if isinstance(cap, bool) else (ei is not None and ei in caps)
        pts.append(PathPt(float(x), float(f), ei, label, known, cap))
    return pts


# ============================================================================================ status
@dataclasses.dataclass(frozen=True)
class RunView:
    state: str
    active: bool
    exec_idx: int | None
    n_exec: int | None
    step_uid: str | None
    loop_iters: tuple[int, ...]
    phase: str
    paused_source: str | None
    windows_done: int | None
    windows_total: int | None
    plan_t_s: float | None
    plan_total_s: float | None
    behind_s: float | None
    remaining_s: float | None
    k_est: float | None
    message: str
    end_reason: str
    marker_x: float | None = None          # B6-07 live marker (sequence coordinate), None when unknown
    marker_f: float | None = None
    label: str = ""


def run_view(st: Any, n_exec: int | None = None) -> RunView:
    state = text_of(get(st, "state", default="IDLE")).upper() or "IDLE"
    li = get(st, "loop_iters")
    if li is None:
        one = get(st, "loop_iter")
        li = () if one is None else (int(one),)
    ei = get(st, "exec_idx", "step_idx")
    mx, mf = get(st, "marker_x_mm"), get(st, "marker_f_n")
    plan_len = get(st, "plan_len")
    return RunView(state, state in ACTIVE_STATES, None if ei is None else int(ei),
                   int(plan_len) if plan_len is not None else n_exec,
                   get(st, "step_uid"), tuple(int(v) for v in li), text_of(get(st, "phase")),
                   get(st, "paused_source"), get(st, "windows_done"), get(st, "windows_total"),
                   get(st, "plan_t_s"), get(st, "plan_total_s"), get(st, "behind_s"), get(st, "remaining_s"),
                   get(st, "k_est_n_mm"), str(get(st, "message", default="") or ""),
                   text_of(get(st, "end_reason", "reason")), float(mx) if finite(mx) else None,
                   float(mf) if finite(mf) else None, str(get(st, "label", default="") or ""))


def fmt_hms(s: float | None) -> str:
    if s is None or not finite(s):
        return "–"
    s = max(0.0, float(s))
    h, rem = divmod(int(round(s)), 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def run_line(v: RunView) -> str:
    """One status line (§3.6 run status): state, step i/N, step label (while active), loop, phase, windows,
    plan / total, remaining."""
    st = v.state + (f" ({v.paused_source})" if v.state == "PAUSED" and v.paused_source else "")
    parts = [st]
    if v.exec_idx is not None:
        parts.append(f"step {v.exec_idx + 1}/{v.n_exec}" if v.n_exec else f"step {v.exec_idx + 1}")
    if v.label and v.active:      # SW-SEQ-003: label of the step being run, from the same status snapshot as the
        parts.append(f'"{v.label}"')   # step / phase (B publishes them together); not shown once the run has ended
    if v.loop_iters:
        parts.append("loop " + ".".join(str(i) for i in v.loop_iters))
    if v.phase:
        parts.append(f"phase {v.phase}")
    if v.windows_done is not None:
        parts.append(f"windows {v.windows_done}/{v.windows_total if v.windows_total is not None else '∞'}")
    if v.plan_t_s is not None or v.plan_total_s is not None:
        tot = fmt_hms(v.plan_total_s) if v.plan_total_s is not None else "∞"
        beh = f" (behind {v.behind_s:.1f} s)" if v.behind_s else ""
        parts.append(f"plan {fmt_hms(v.plan_t_s)} / {tot}{beh}")
    if v.active or v.remaining_s is not None:
        parts.append("remaining ~" + fmt_hms(v.remaining_s) if v.remaining_s is not None
                     else "remaining ∞ – loop until stopped")
    if v.k_est is not None:
        parts.append(f"k_est {float(v.k_est):.1f} N/mm")
    if v.end_reason and not v.active:
        parts.append(f"end: {v.end_reason}")
    return "  ·  ".join(parts)


# ============================================================================================ step results
def flags_of(r: Any) -> tuple[str, ...]:
    fl = get(r, "flags", default=())
    if isinstance(fl, str):
        fl = [f.strip() for f in fl.replace(";", ",").split(",") if f.strip()]
    out = [text_of(f) for f in fl]
    st = text_of(get(r, "status"))
    if st and st not in out and st not in ("OK", "DONE"):
        out.insert(0, st)
    return tuple(out)


def _stat(r: Any, q: str, s: str) -> Any:
    """Statistic ``s`` of quantity ``q`` (F, x, raw): nested ``r.F.mean`` / ``r["f"]["mean"]`` or flat
    ``f_mean`` / ``F_mean`` / ``mean_f``."""
    for qk in (q, q.lower(), q.upper(), {"F": "force", "x": "travel", "raw": "raw"}[q]):
        sub = get(r, qk, f"{qk}_stats")
        if sub is not None and not isinstance(sub, (int, float)):
            v = get(sub, s)
            if v is not None:
                return v
    return get(r, f"{q.lower()}_{s}_n", f"{q.lower()}_{s}_mm", f"{q.lower()}_{s}", f"{q}_{s}", f"{s}_{q.lower()}")


RESULT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("step", "Step"), ("loop", "Loop"), ("label", "Label"), ("n", "N"), ("F.mean", "F mean"), ("F.std", "F std"),
    ("F.min", "F min"), ("F.max", "F max"), ("F.se", "F SE"), ("F.drift", "F drift"), ("x.mean", "x mean [mm]"),
    ("target", "Target"), ("flags", "Flags"),
)


def result_row(r: Any) -> dict[str, Any]:
    """One step / iteration result (SW-REP-002) → display dict (values in N / mm; flags verbatim)."""
    idx = get(r, "step_idx", "exec_idx")                    # 0-based model index → 1-based display
    step = int(idx) + 1 if idx is not None else get(r, "step", default="")
    li = get(r, "loop_iters", "loop_iter", "loop")
    if isinstance(li, (tuple, list)):
        li = ".".join(str(v) for v in li) if li else ""
    row = {"step": step,
           "loop": "" if li is None else li, "label": str(get(r, "label", default="") or ""),
           "n": get(r, "n", "N", default=_stat(r, "F", "n")), "target": get(r, "target", "f_target_n", "target_n"),
           "flags": flags_of(r)}
    for s in ("mean", "std", "min", "max", "se", "drift"):
        row[f"F.{s}"] = _stat(r, "F", s)
    row["x.mean"] = _stat(r, "x", "mean")
    return row


def result_rows(result: Any) -> list[dict[str, Any]]:
    rows = get(result, "steps", "results", "step_results", "windows", default=()) or ()
    return [result_row(r) for r in rows]


def result_warnings(result: Any) -> list[str]:
    w = get(result, "warnings", default=()) or ()
    return [str(get(x, "text", default=x)) if not isinstance(x, str) else x for x in w]


def recording_rows(items: Iterable[Any]) -> list[dict[str, Any]]:
    """``reports.list_recordings()`` → rows (``RecordingInfo`` or a plain folder path)."""
    out = []
    for it in items or ():
        if isinstance(it, str):
            out.append({"folder": it, "started": "", "specimen": "", "sequence": "", "status": "", "duration": None,
                        "has_report": None})
            continue
        marks = get(it, "marks", "marks_summary", default="")
        if not isinstance(marks, str):
            marks = ", ".join(f"{v}" for v in (marks.values() if isinstance(marks, Mapping) else
                                              [get(marks, "element"), get(marks, "number")]) if v)
        out.append({"folder": str(get(it, "folder", "path", "dir", default="")),
                    "started": text_of(get(it, "started_utc", "started", default="")),
                    "specimen": marks, "sequence": str(get(it, "sequence_name", "sequence", default="") or ""),
                    "status": text_of(get(it, "status", default="")),
                    "duration": get(it, "duration_s"), "has_report": get(it, "has_report")})
    return out


def report_paths(paths: Any) -> dict[str, str]:
    """``ReportPaths`` → {"html", "json", "csv", "folder"} (missing → absent)."""
    out = {}
    for k, names in (("html", ("html", "html_path", "report_html")), ("json", ("json", "json_path", "report_json")),
                     ("csv", ("csv", "csv_path", "data_csv")), ("folder", ("folder", "dir", "out_dir"))):
        v = get(paths, *names)
        if v:
            out[k] = str(v)
    if isinstance(paths, str):
        out["html"] = paths
    return out


# ============================================================================================ generators
def schemas(sequencer: Any) -> list[Any]:
    try:
        sch = sequencer.generator_schemas()
    except Exception:  # noqa: BLE001 - not implemented yet: no generators
        return []
    if isinstance(sch, Mapping):
        return list(sch.values())
    return list(sch or ())


def generate(sequencer: Any, name: str, params: Mapping[str, Any]) -> Any:
    """Call the backend generator ``name`` (the generator validates its arguments: ``ValueError`` verbatim)."""
    fn = getattr(sequencer, "generate", None)
    if callable(fn):
        return fn(name, dict(params))                          # B6-04: generate(name, params)
    gens = getattr(sequencer, "generators", None)
    if isinstance(gens, Mapping):
        g = gens.get(name)
    else:
        g = getattr(gens, name, None) or getattr(gens, name.rstrip("_") + "_", None)
    if g is None:
        raise NotImplementedError(f"generator {name!r} is not available")
    return g(**dict(params))
