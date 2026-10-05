"""Engine phase → wizard view kind (SW_design_GUI §6.1): a pure display table, completeness-tested against the
engines' ``PHASES`` (G-26 / G-27). The engine owns the state machine; the view kind only selects which parts of
the page are shown (live motion line, capture progress + stats, result table, fit plot).

Implements: SW-CAL-001 (every engine phase has a wizard view)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

#: phases shown as page states, not in the step strip
TERMINAL_PHASES: frozenset[str] = frozenset({"ABORTED", "RESTORING", "DONE", "REFUSED", "CANCELLED"})
#: phases before ``start()`` (the engine is idle)
IDLE_PHASES: frozenset[str] = frozenset({"IDLE", "NONE", ""})

VIEWS: Mapping[str, Mapping[str, str]] = {
    "travel_cal": {"CHECK": "checklist", "BACKLASH": "move", "REFERENCE": "instruction", "MOVE1": "move",
                   "ENTER_D1": "input", "MOVE2": "move", "ENTER_DTOT": "input", "RESULT": "result",
                   "ACCEPT": "progress", "DONE": "done", "ABORTED": "aborted", "RESTORING": "restoring",
                   "CANCELLED": "aborted"},
    "load_cal": {"CONFIG": "checklist", "AWAIT_OPERATOR": "input", "PRESETTLE": "capture", "CAPTURE": "capture",
                 "EVALUATE": "evaluate", "FIT": "fit", "ACCEPT": "progress", "DONE": "done", "ABORTED": "aborted",
                 "CANCELLED": "aborted"},
    "tare": {"CHECK": "checklist", "CAPTURE": "capture", "EVALUATE": "evaluate", "DONE": "done",
             "REFUSED": "refused", "ABORTED": "aborted", "CANCELLED": "aborted"},
}

#: parts of the page per view kind
VIEW_PARTS: Mapping[str, frozenset[str]] = {
    "checklist": frozenset(), "move": frozenset({"live", "progress"}), "instruction": frozenset(),
    "input": frozenset({"inputs"}), "capture": frozenset({"progress", "stats"}),
    "evaluate": frozenset({"stats", "result"}), "fit": frozenset({"result", "plot"}),
    "result": frozenset({"result"}), "progress": frozenset({"progress"}), "done": frozenset({"result"}),
    "aborted": frozenset(), "restoring": frozenset({"progress"}), "refused": frozenset(),
    "generic": frozenset({"inputs", "progress", "stats", "result"}),
}


def view_of(kind: str, phase: str) -> str:
    return VIEWS.get(kind, {}).get(phase, "generic")


def missing_views(kind: str, phases: tuple[str, ...]) -> list[str]:
    """Names of ``phases`` without a view (G-26 / G-27 completeness)."""
    table = VIEWS.get(kind, {})
    return [p for p in phases if p not in table]


def strip_phases(phases: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(p for p in phases if p not in TERMINAL_PHASES)


# --------------------------------------------------------------------------------------------- result display

def _plain(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if hasattr(obj, "_asdict"):
        return obj._asdict()
    return obj


def result_parts(result: Any) -> tuple[list[tuple[str, str]], list[str], list[list[str]]]:
    """Generic display of an engine ``result``: (summary key/value pairs, table headers, table rows).

    A mapping (or dataclass) gives its scalar entries as the summary; its first list of mappings (e.g. the load
    points) becomes the table. A list of mappings is the table itself. Values are shown verbatim (formatted)."""
    r = _plain(result)
    summary: list[tuple[str, str]] = []
    rows: list[Mapping[str, Any]] = []
    if r is None:
        return [], [], []
    if isinstance(r, Mapping):
        for k, v in r.items():
            v = _plain(v)
            if isinstance(v, (list, tuple)) and v and all(isinstance(_plain(x), Mapping) for x in v):
                if not rows:
                    rows = [_plain(x) for x in v]
                continue
            summary.append((str(k), fmt_any(v)))
    elif isinstance(r, (list, tuple)) and all(isinstance(_plain(x), Mapping) for x in r):
        rows = [_plain(x) for x in r]
    else:
        summary.append(("result", fmt_any(r)))
    headers: list[str] = []
    for row in rows:
        for k in row:
            if k not in headers:
                headers.append(str(k))
    table = [[fmt_any(row.get(h)) for h in headers] for row in rows]
    return summary, headers, table


def fmt_any(v: Any) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if v != v:
            return "n/a"
        a = abs(v)
        if a != 0 and (a < 1e-3 or a >= 1e7):
            return f"{v:.6g}"
        return f"{v:.4f}".rstrip("0").rstrip(".") if a < 1000 else f"{v:,.1f}".replace(",", " ")
    if isinstance(v, (list, tuple)):
        return ", ".join(fmt_any(x) for x in v)
    return str(v)


FIT_X_KEYS = ("raw_mean", "raw", "mean_raw", "raw_counts")
FIT_Y_KEYS = ("f_ref_n", "F_ref", "force_n", "f_n", "F_N", "force")
FIT_K_KEYS = ("K", "k", "k_n_per_count")
FIT_B_KEYS = ("B", "b", "b_n")


def fit_points(result: Any) -> tuple[list[float], list[float], float | None, float | None]:
    """(raw, force, K, B) for the fit mini plot when the result carries them (display only)."""
    r = _plain(result)
    if not isinstance(r, Mapping):
        return [], [], None, None
    _s, headers, _t = result_parts(r)
    xs: list[float] = []
    ys: list[float] = []
    xk = next((k for k in FIT_X_KEYS if k in headers), None)
    yk = next((k for k in FIT_Y_KEYS if k in headers), None)
    if xk and yk:
        for v in r.values():
            v = _plain(v)
            if isinstance(v, (list, tuple)) and v and all(isinstance(_plain(x), Mapping) for x in v):
                for p in v:
                    p = _plain(p)
                    try:
                        xs.append(float(p[xk]))
                        ys.append(float(p[yk]))
                    except (KeyError, TypeError, ValueError):
                        pass
                break
    k = next((float(r[n]) for n in FIT_K_KEYS if isinstance(r.get(n), (int, float))), None)
    b = next((float(r[n]) for n in FIT_B_KEYS if isinstance(r.get(n), (int, float))), None)
    return xs, ys, k, b


def residual_points(result: Any) -> tuple[list[float], list[float]]:
    """(point index, residual N) when the result carries ``residuals`` (``LoadCalResult``, B5-08) — display only."""
    r = _plain(result)
    res = r.get("residuals") if isinstance(r, Mapping) else None
    if not isinstance(res, (list, tuple)):
        return [], []
    ys = []
    for v in res:
        try:
            ys.append(float(v))
        except (TypeError, ValueError):
            return [], []
    return [float(i) for i in range(len(ys))], ys
