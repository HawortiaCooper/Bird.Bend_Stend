"""Wire µm ↔ steps and the step-rate speed cap, bit-exact with the FW (ICD §0.1, ``units_vectors.json``),
plus the trapezoid planner used for durations and by the simulator (R4 §2, TV-M planner).

``spm`` is the binary32 value of ``motion.steps_per_mm`` (``f32()``); arithmetic in binary64 left to right:
``steps = round(um · spm / 1000)``, ``um = round(steps · 1000 / spm)``, ``cap = floor(rate · 1000 / spm)``,
round = half away from zero.

Implements: SYS-003, FW-MOT-002 (sim), FW-MOT-009 (speed cap), SW-CAL-002 (conversion basis)
"""
from __future__ import annotations

import math
import struct

from bend_stand.calc.rounding import round_half_away


def f32(x: float) -> float:
    """Round a value to IEEE-754 binary32 (the wire/FW representation of ``motion.steps_per_mm``)."""
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def um_to_steps(um: int, spm: float) -> int:
    return round_half_away(um * spm / 1000.0)


def steps_to_um(steps: int, spm: float) -> int:
    return round_half_away(steps * 1000.0 / spm)


def rate_cap_um_s(max_step_rate_hz: int, spm: float) -> int:
    """Speed cap from the step rate, ``floor(max_step_rate_hz · 1000 / spm)`` µm/s (ICD §5.4)."""
    return math.floor(max_step_rate_hz * 1000.0 / spm)


def v_limit_um_s(v_max_um_s: int, max_step_rate_hz: int, spm: float, v_unhomed_um_s: int | None = None) -> int:
    """``min(v_max, step-rate cap[, v_unhomed])`` (ICD §5.4)."""
    v = min(int(v_max_um_s), rate_cap_um_s(max_step_rate_hz, spm))
    if v_unhomed_um_s is not None:
        v = min(v, int(v_unhomed_um_s))
    return v


def plan_trapezoid(n_steps: int, v: float, a: float, d: float) -> dict[str, float | int | str]:
    """Trapezoid / triangle plan in step units (R4 §2 TV-M planner).

    ``v`` steps/s, ``a``/``d`` steps/s². Returns kind ``trap``/``tri``, ``n_acc``, ``n_cruise``, ``n_dec``,
    ``v_peak`` and the duration ``t`` in s.
    """
    n = abs(int(n_steps))
    if n == 0:
        return {"kind": "none", "n_acc": 0, "n_cruise": 0, "n_dec": 0, "v_peak": 0.0, "t": 0.0}
    if v <= 0 or a <= 0 or d <= 0:
        raise ValueError("v, a, d must be > 0")
    n_acc_full = v * v / (2.0 * a)
    n_dec_full = v * v / (2.0 * d)
    if n_acc_full + n_dec_full <= n:
        n_acc = round_half_away(n_acc_full)
        n_dec = round_half_away(n_dec_full)
        n_cruise = n - n_acc - n_dec
        t = v / a + v / d + (n - n_acc_full - n_dec_full) / v
        return {"kind": "trap", "n_acc": n_acc, "n_cruise": n_cruise, "n_dec": n_dec, "v_peak": float(v), "t": t}
    v_peak = math.sqrt(2.0 * n * a * d / (a + d))
    n_acc = round_half_away(n * d / (a + d))
    return {"kind": "tri", "n_acc": n_acc, "n_cruise": 0, "n_dec": n - n_acc, "v_peak": v_peak,
            "t": v_peak / a + v_peak / d}


def move_duration_s(distance_um: float, v_um_s: float, a_um_s2: float, d_um_s2: float | None = None) -> float:
    """Duration of a trapezoid move in s (wire units; used for timeouts and the planned path)."""
    dist = abs(float(distance_um))
    if dist == 0:
        return 0.0
    d = a_um_s2 if d_um_s2 is None else d_um_s2
    s_acc = v_um_s * v_um_s / (2 * a_um_s2)
    s_dec = v_um_s * v_um_s / (2 * d)
    if s_acc + s_dec <= dist:
        return v_um_s / a_um_s2 + v_um_s / d + (dist - s_acc - s_dec) / v_um_s
    vp = math.sqrt(2 * dist * a_um_s2 * d / (a_um_s2 + d))
    return vp / a_um_s2 + vp / d


def stop_distance_um(v_um_s: float, a_stop_um_s2: float) -> float:
    """Planned controlled-stop distance ``v² / (2 a_stop)`` in µm."""
    return v_um_s * v_um_s / (2.0 * a_stop_um_s2)
