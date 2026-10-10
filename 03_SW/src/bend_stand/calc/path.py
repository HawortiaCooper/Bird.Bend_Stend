"""Planned path of a sequence for the chart x = travel, y = load (R4 §8.2, SRS SW-SCH-001) — pure functions.

``advance(kind, x_prev, f_prev, target, k_est, pull_dir)`` gives the next (x, F, known) point: TRAVEL → x known,
F_est = F_prev + pull_dir·k_est·(x − x_prev); LOAD → F known, x_est = x_prev + pull_dir·(F − F_prev)/k_est;
HOLD / MARK / TARE → the same point (TARE: F = 0 afterwards, the force reference restarts); HOME → x = home
position, F unknown → a break in the polyline (0 assumed afterwards: homing is done unloaded, SAF-FW-021).
**No contact (OI-UM-06):** the specimen is a spring that only pushes back on its loaded side of the contact point
``x_contact`` (where the model force is 0). With ``x_contact`` and ``side`` (+1 tension / −1 compression) given, a
TRAVEL step beyond the contact on the unloaded side plans F = 0 (no linear extrapolation to negative forces), and a
LOAD step is placed relative to the contact point. ``contact_x`` gives the contact point of a loaded state.
``planned_path(points)`` turns the planned steps into chart points.

Implements: SW-SCH-001
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

NAN = float("nan")


@dataclass(frozen=True)
class PathPoint:
    x_mm: float
    f_n: float
    exec_idx: int
    label: str
    known: str               # "x" | "F" | "both" | "none"
    capture: bool = False
    brk: bool = False        # polyline break before this point (after HOME)


def contact_x(x_mm: float, f_n: float, k_est: float, pull_dir: int) -> float:
    """Contact point (F = 0 on the specimen line through ``(x, F)``); NaN when unknown."""
    if not (k_est > 0 and math.isfinite(f_n) and math.isfinite(x_mm)):
        return NAN
    return float(x_mm) - (1 if pull_dir >= 0 else -1) * float(f_n) / float(k_est)


def advance(kind: str, x_prev: float, f_prev: float, target: float | None, k_est: float, pull_dir: int,
            home_x: float = 0.0, *, x_contact: float | None = None,
            side: int | None = None) -> tuple[float, float, str]:
    """→ ``(x, F, known)`` after a step of ``kind`` (``target`` mm for travel, N for load). With ``x_contact`` (finite)
    the specimen line goes through the contact point; with ``side`` the force is 0 on the unloaded side (OI-UM-06)."""
    # Implements: SW-SCH-001 (planned path; OI-UM-06 no extrapolation below contact)
    sgn = 1 if pull_dir >= 0 else -1
    xc = x_contact if x_contact is not None and math.isfinite(x_contact) and k_est > 0 else None
    if kind == "travel" and target is not None:
        if xc is not None:
            f = sgn * k_est * (float(target) - xc)
        else:
            f = f_prev + sgn * k_est * (target - x_prev) if math.isfinite(f_prev) else NAN
        if side and math.isfinite(f) and f * side < 0:
            f = 0.0                                     # beyond the contact on the unloaded side: no force
        return float(target), f + 0.0, "x"
    if kind == "load" and target is not None:
        if xc is not None:
            x = xc + sgn * float(target) / k_est
        else:
            x = x_prev + sgn * (target - f_prev) / k_est if k_est > 0 and math.isfinite(f_prev) else x_prev
        return x, float(target), "F"
    if kind == "home":
        return float(home_x), 0.0, "x"
    if kind == "tare":
        return x_prev, 0.0, "x"
    return x_prev, f_prev, "both" if math.isfinite(f_prev) else "x"


def planned_path(steps: Iterable[Any]) -> tuple[PathPoint, ...]:
    """Points from planned steps (attributes ``exec_idx, kind, label, x_start_mm, f_start_n, x_target_mm,
    f_target_n, capture, known``): the start point of the first step, then the end point of every step."""
    out: list[PathPoint] = []
    brk = False
    for i, s in enumerate(steps):
        kind = str(getattr(s, "kind", ""))
        if i == 0:
            out.append(PathPoint(float(s.x_start_mm), float(s.f_start_n), int(s.exec_idx), "start", "both"))
        if kind == "home":
            brk = True
        out.append(PathPoint(float(s.x_target_mm), float(s.f_target_n), int(s.exec_idx), str(s.label or kind),
                             str(getattr(s, "known", "x")), getattr(s, "capture", None) is not None, brk))
        brk = False
    return tuple(out)


__all__ = ["PathPoint", "advance", "contact_x", "planned_path"]
