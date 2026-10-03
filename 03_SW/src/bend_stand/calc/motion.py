"""Wire µm ↔ steps and the step-rate speed cap, bit-exact with the FW (ICD §0.1, ``units_vectors.json``),
plus the trapezoid planner used for durations and by the simulator (R4 §2, TV-M planner) and the exact
square-root step ramp of R4 §1.5 (``ramp_periods`` for the TV-M vectors, ``Ramp`` with the virtual index for
on-the-fly speed changes and controlled stops — the simulator's step generator, FW-MOT-003).

``spm`` is the binary32 value of ``motion.steps_per_mm`` (``f32()``); arithmetic in binary64 left to right:
``steps = round(um · spm / 1000)``, ``um = round(steps · 1000 / spm)``, ``cap = floor(rate · 1000 / spm)``,
round = half away from zero. Results **saturate** (ICD v0.5 §0.1, OBS-M1-05): µm and steps to the int32 range
[−2³¹, 2³¹−1], the speed cap to [0, 2³²−1].

Implements: SYS-003, FW-MOT-002 (sim), FW-MOT-003 (ramp, sim), FW-MOT-009 (speed cap), SW-CAL-002 (conversion
basis), SAF-FW-003 (controlled-stop distance, sim)
"""
from __future__ import annotations

import math
import struct

from bend_stand.calc.rounding import round_half_away


def f32(x: float) -> float:
    """Round a value to IEEE-754 binary32 (the wire/FW representation of ``motion.steps_per_mm``)."""
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


I32_MIN, I32_MAX, U32_MAX = -(2 ** 31), 2 ** 31 - 1, 2 ** 32 - 1


def _sat_i32(x: int) -> int:
    return I32_MIN if x < I32_MIN else I32_MAX if x > I32_MAX else x


def um_to_steps(um: int, spm: float) -> int:
    return _sat_i32(round_half_away(um * spm / 1000.0))


def steps_to_um(steps: int, spm: float) -> int:
    return _sat_i32(round_half_away(steps * 1000.0 / spm))


def rate_cap_um_s(max_step_rate_hz: int, spm: float) -> int:
    """Speed cap from the step rate, ``floor(max_step_rate_hz · 1000 / spm)`` µm/s (ICD §5.4), saturated to u32."""
    return min(U32_MAX, max(0, math.floor(max_step_rate_hz * 1000.0 / spm)))


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


# ============================================================================================ ramp (R4 §1.5)

F_TICK_HZ = 90_000_000            # TIM2 at 90 MHz, PSC 0 (FW_design §5.6.1)


def ramp_periods(n_steps: int, f_tick: float, v: float, a: float, d: float) -> list[int]:
    """Exact square-root ramp with the max rule and fractional carry (R4 §1.5, TV-M), binary64.

    ``v`` steps/s, ``a``/``d`` steps/s²; returns the ``n_steps`` periods in timer ticks:
    ``c_k = max(Ca/(√k + √(k−1)), f/v, Cd/(√r + √(r−1)))`` with ``r = N − k + 1``; ``acc += c; ci = int(acc)``.
    """
    r = Ramp(f_tick, v, a, d, n_steps)
    out = []
    while True:
        c = r.next()
        if c is None:
            return out
        out.append(c)


ISR_MAX_TICKS = 90_000                # 1 ms at 90 MHz (FW CTRL_STOP_ISR_MAX_TICKS)
HALT_MIN_TICKS = 180_000              # 2 ms (FW CTRL_STOP_HALT_MIN_TICKS, D-29 d)


def ctrl_stop_path(p_ticks: float, f_tick: float, a_stop: float) -> tuple[str, float]:
    """Controlled-stop path (ICD §6.5, FW_design §5.6.4): ``CLEAN`` (clean halt allowed: period > 2 ms **and**
    stop distance ≤ 1 step), ``ISR`` (period ≤ 1 ms) or ``STRETCH``; returns ``(path, d_steps)``."""
    v = f_tick / p_ticks
    d_steps = v * v / (2.0 * a_stop)
    if p_ticks > HALT_MIN_TICKS and d_steps <= 1.0:
        return "CLEAN", d_steps
    if p_ticks <= ISR_MAX_TICKS:
        return "ISR", d_steps
    return "STRETCH", d_steps


class Ramp:
    """Step-period generator of one motion (R4 §1.5; FW ``ramp`` / ``stepgen_core``; oracle
    ``00_System/tools/ref_motion.py``, vectors ``motion_vectors.json`` — reproduced exactly in binary64).

    ``A(k) = C_a/(√k + √(k−1))``, ``D(r) = C_d/(√r + √(r−1))``, ``c_min = f/v``.

    * Position move of ``n`` steps: ``c_i = max(A(i), c_min, D(r))``, ``r`` = steps left incl. this one.
    * Speed up on the fly to ``v₂`` (> current ``f/c_last``): ``k ← v_cur²/(2α)``, then ``max(A(k+j), f/v₂)``.
    * Slow down to ``v₂``: virtual ``r = v_cur²/(2α_d)``; ``c = D(r)`` while ``r ≥ 1`` and ``D(r) < f/v₂``, then
      cruise at ``f/v₂``.
    * Controlled stop with ``α_stop``: ``r₀ = ⌈v_cur²/(2α_stop)⌉`` (≤ the steps left of a position move); the
      motion ends after ``r₀`` more steps, ``c = max(c_last, D_stop(r))`` (never faster than now).
    * One fractional-carry accumulator over the whole motion: ``acc += c; ticks = ⌊acc⌋; acc −= ticks``.

    ``next()`` returns the period in ticks of the next step, or ``None`` when the motion has ended. The end term
    ``D(r)`` of the move's end point (JOG bound / soft limit) is applied in every mode except the stop.
    """

    __slots__ = ("f", "alpha", "alpha_d", "ca", "cd", "c_min", "v_target", "k", "r", "rv", "stop_floor", "cs",
                 "acc", "c_last", "steps")

    def __init__(self, f_tick: float, v: float, a: float, d: float, n: int) -> None:
        if v <= 0 or a <= 0 or d <= 0:
            raise ValueError("v, a, d must be > 0")
        self.f = float(f_tick)
        self.alpha = float(a)
        self.alpha_d = float(d)
        self.ca = self.f * math.sqrt(2.0 / self.alpha)
        self.cd = self.f * math.sqrt(2.0 / self.alpha_d)
        self.v_target = float(v)
        self.c_min = self.f / self.v_target
        self.k = 0.0                     # accel index (virtual after speed changes)
        self.r = int(n)                  # steps left until the end point, not yet emitted
        self.rv: float | None = None     # virtual decel index while slowing to a lower speed
        self.stop_floor: float | None = None
        self.cs = 0.0                    # C_d of the stop deceleration
        self.acc = 0.0
        self.c_last: float | None = None
        self.steps = 0

    # ---- state -----------------------------------------------------------------------------------
    @property
    def v_now(self) -> float:
        """Current speed in steps/s (0 before the first step)."""
        return 0.0 if self.c_last is None else self.f / self.c_last

    @property
    def done(self) -> bool:
        return self.r <= 0

    @property
    def stopping(self) -> bool:
        return self.stop_floor is not None

    def stop_steps(self, d: float | None = None) -> int:
        """Steps a controlled stop needs from now: ``⌈v²/(2α_d)⌉``."""
        ad = self.alpha_d if d is None else float(d)
        v = self.v_now
        return math.ceil(v * v / (2.0 * ad)) if v > 0 else 0

    # ---- changes on the fly --------------------------------------------------------------------------
    def set_end(self, n_remaining: int) -> None:
        """New end point (JOG bound / soft limit change): ``n_remaining`` steps after the ones emitted."""
        if self.stop_floor is None:
            self.r = int(n_remaining)

    def set_speed(self, v: float) -> None:
        v = float(v)
        if v <= 0:
            raise ValueError("speed must be > 0 (use stop())")
        if self.stop_floor is not None or (v == self.v_target and self.rv is None):
            return                       # unchanged speed (jog refresh) / stop in progress: nothing changes
        cur = self.v_now
        self.v_target = v
        self.c_min = self.f / v
        if cur <= 0 or self.c_last is None or self.c_last > self.c_min:
            self.rv = None               # accelerate (from rest: k = 0)
            self.k = 0.0 if cur <= 0 else cur * cur / (2.0 * self.alpha)
            return
        self.rv = cur * cur / (2.0 * self.alpha_d)       # slow down along the decel curve

    def stop(self, d: float | None = None) -> None:
        """Controlled stop with deceleration ``d`` (steps/s²; default = the move's)."""
        if self.stop_floor is not None:
            return
        ad = self.alpha_d if d is None else float(d)
        self.cs = self.f * math.sqrt(2.0 / ad)
        self.rv = None
        if self.c_last is None:
            self.r = 0
            return
        self.r = min(self.r, self.stop_steps(ad))
        self.stop_floor = self.c_last

    # ---- generation ------------------------------------------------------------------------------------
    def next(self) -> int | None:
        if self.r <= 0:
            return None
        r = self.r
        if self.stop_floor is not None:
            c = max(self.stop_floor, self.cs / (math.sqrt(r) + math.sqrt(r - 1.0)))
        else:
            if self.rv is not None:
                rv = self.rv
                d_rv = self.cd / (math.sqrt(rv) + math.sqrt(rv - 1.0)) if rv >= 1.0 else math.inf
                if d_rv < self.c_min:
                    c = d_rv
                    self.rv = rv - 1.0
                else:
                    self.rv = None
                    c = self.c_min
            else:
                self.k += 1.0
                k = self.k
                c = max(self.ca / (math.sqrt(k) + math.sqrt(k - 1.0)), self.c_min)
            c = max(c, self.cd / (math.sqrt(r) + math.sqrt(r - 1.0)))
        self.r = r - 1
        self.c_last = c
        self.steps += 1
        self.acc += c
        ci = int(self.acc)
        self.acc -= ci
        return ci
