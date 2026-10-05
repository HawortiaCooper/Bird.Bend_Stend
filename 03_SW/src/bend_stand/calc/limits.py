"""SW limit rules — pure functions (SW_design §6.1, §6.6; R4 §4.6; SRS SAF-SW-001, SAF-SW-006, SW-LIM-001/002).

* ``evaluate_force_limit`` — trip side ``"PULL"`` (F > pull trip) / ``"PUSH"`` (F < push trip) of the enabled sides.
* ``saturated_force`` — a saturated sample counts as ±∞ with sign ``sign(K)·sign(rail)`` (overload, never "no data").
* ``force_warning`` — warning edge with hysteresis (on at ``warn_pct``·|trip|, off 2 % of the trip level below).
* ``evaluate_travel_limit`` — position beyond an enabled travel limit by more than ``tol_mm``.
* ``predicted_crossing`` — unbounded motion: ``x + v·t_lead`` crosses a limit in the direction of motion.
* ``limit_margin_warning`` — SAF-SW-006: ``k_est·v·t_react > F_fw − F_pc``.
* ``check_limit_config`` — SW-LIM-001/002 edit rules (pure part; the FW soft limits are passed in).

Implements: SAF-SW-001 (rules), SAF-SW-006, SW-LIM-001, SW-LIM-002
"""
from __future__ import annotations

import math

from bend_stand.calc.units import FS_N, FW_LIMIT_MAX_N

T_REACT_S = 0.065
T_LEAD_S = 0.075
WARN_HYST_FRAC = 0.02


def evaluate_force_limit(f_n: float, pull_trip_n: float, pull_enabled: bool, push_trip_n: float,
                         push_enabled: bool) -> str | None:
    """``"PULL"`` / ``"PUSH"`` when the force is beyond an enabled trip level, else ``None`` (NaN → None)."""
    if math.isnan(f_n):
        return None
    if pull_enabled and f_n > pull_trip_n:
        return "PULL"
    if push_enabled and f_n < push_trip_n:
        return "PUSH"
    return None


def saturated_force(k: float, rail_sign: int) -> float:
    """±∞ for a saturated sample: overload in the direction ``sign(k)·sign(rail)``."""
    return math.copysign(math.inf, (1.0 if k >= 0 else -1.0) * (1.0 if rail_sign >= 0 else -1.0))


def force_warning(f_n: float, trip_n: float, warn_pct: float, active: bool) -> bool:
    """Warning state with hysteresis: on at ``|F| ≥ warn_pct·|trip|`` on the trip's side, off below that minus 2 %
    of the trip level."""
    if math.isnan(f_n) or trip_n == 0:
        return False
    on_level = warn_pct / 100.0 * abs(trip_n)
    v = f_n if trip_n > 0 else -f_n
    if active:
        return v >= on_level - WARN_HYST_FRAC * abs(trip_n)
    return v >= on_level


def back_inside(f_n: float, trip_n: float) -> bool:
    """Trip latch release: the force is back inside the trip level by the hysteresis band (2 % of the trip)."""
    if math.isnan(f_n):
        return False
    v = f_n if trip_n > 0 else -f_n
    return v <= abs(trip_n) * (1.0 - WARN_HYST_FRAC)


def evaluate_travel_limit(x_mm: float, lo_mm: float | None, hi_mm: float | None, tol_mm: float = 0.0) -> str | None:
    """``"TRAVEL_MIN"`` / ``"TRAVEL_MAX"`` when ``x`` is beyond an enabled limit by more than ``tol_mm``."""
    if math.isnan(x_mm):
        return None
    if hi_mm is not None and x_mm > hi_mm + tol_mm:
        return "TRAVEL_MAX"
    if lo_mm is not None and x_mm < lo_mm - tol_mm:
        return "TRAVEL_MIN"
    return None


def predicted_crossing(x_mm: float, v_mm_s: float, lo_mm: float | None, hi_mm: float | None,
                       t_lead_s: float = T_LEAD_S) -> str | None:
    """Unbounded motion (no end point inside the limits): the predicted position ``x + v·t_lead`` crosses a limit in
    the direction of motion."""
    if math.isnan(x_mm) or math.isnan(v_mm_s) or v_mm_s == 0:
        return None
    xp = x_mm + v_mm_s * t_lead_s
    if v_mm_s > 0 and hi_mm is not None and xp > hi_mm:
        return "TRAVEL_MAX"
    if v_mm_s < 0 and lo_mm is not None and xp < lo_mm:
        return "TRAVEL_MIN"
    return None


def limit_margin_warning(k_est_n_mm: float | None, v_mm_s: float, f_fw_n: float, f_pc_n: float,
                         t_react_s: float = T_REACT_S) -> bool:
    """SAF-SW-006: the force overshoot during the reaction time exceeds the margin between FW and PC levels."""
    if k_est_n_mm is None or not k_est_n_mm > 0 or not v_mm_s > 0:
        return False
    return k_est_n_mm * v_mm_s * t_react_s > f_fw_n - abs(f_pc_n)


def check_limit_config(*, pull_trip_n: float, pull_enabled: bool, push_trip_n: float, push_enabled: bool,
                       warn_pct: float, fw_level_n: float) -> list[tuple[str, str, str]]:
    """SW-LIM-002 rules → list of ``(field, code, text)`` errors."""
    out: list[tuple[str, str, str]] = []
    if not 0 < pull_trip_n <= FW_LIMIT_MAX_N + 1e-9:
        out.append(("pull_trip_n", "RANGE", f"pull trip must be in (0, {FW_LIMIT_MAX_N:.2f}] N"))
    if not -FW_LIMIT_MAX_N - 1e-9 <= push_trip_n < 0:
        out.append(("push_trip_n", "RANGE", f"push trip must be in [−{FW_LIMIT_MAX_N:.2f}, 0) N"))
    if not 50.0 <= warn_pct <= 100.0:
        out.append(("warn_pct", "RANGE", "warning level must be 50…100 % of the trip level"))
    if not 0 < fw_level_n <= FW_LIMIT_MAX_N + 1e-6:
        out.append(("fw_level_n", "FW_LEVEL", f"FW load-limit level must be in (0, {FW_LIMIT_MAX_N:.2f}] N "
                                              "(never above 110 % FS, D-12)"))
    trips = [abs(t) for t, en in ((pull_trip_n, pull_enabled), (push_trip_n, push_enabled)) if en]
    if trips and fw_level_n + 1e-9 < max(trips):
        out.append(("fw_level_n", "FW_BELOW_SW", f"FW level {fw_level_n:.1f} N below the SW trip level "
                                                 f"{max(trips):.1f} N (SW-LIM-002)"))
    return out


__all__ = ["evaluate_force_limit", "saturated_force", "force_warning", "back_inside", "evaluate_travel_limit",
           "predicted_crossing", "limit_margin_warning", "check_limit_config", "T_REACT_S", "T_LEAD_S", "FS_N"]
