#!/usr/bin/env python3
"""Reference step-period generator (test oracle) for motion_vectors.json.

Implements: FW-MOT-003 (ramp), SAF-FW-003 (controlled stop, ICD §6.5), R4 §1.5 / §12 TV-M; OI-FW-20 (M2).

The FW (`02_FW/src/pure/ramp.c`, float32) must reproduce every period within ±1 tick and every sum within
±N/1000 ticks (N = steps); the SW simulator (Python, binary64) reproduces them exactly. Definitions
(all in binary64; f = timer clock [ticks/s], α / α_d = accel / decel [steps/s²], v = speed [steps/s]):

  C_a = f·√(2/α), C_d = f·√(2/α_d), c_min = f / v
  A(k) = C_a / (√k + √(k−1))      period of step k when accelerating from rest (k ≥ 1; real k allowed)
  D(r) = C_d / (√r + √(r−1))      period of a step with r steps (including it) left until rest (r ≥ 1)

  Position move of N steps from rest to rest (R4 §1.5, "max rule"): step i = 1…N, r = N − i + 1:
      c_i = max(A(i), c_min, D(r))
  Fractional carry over the WHOLE motion (one accumulator, also across mode changes):
      acc += c; ticks = floor(acc); acc −= ticks           (acc starts at 0)

  Velocity mode (JOG) and on-the-fly changes (R4 §1.5; virtual indices):
  - v_cur = f / c_last (c_last = the last emitted period before rounding, i.e. the float period).
  - Accelerate to v_new > v_cur: k0 = v_cur² / (2α); step j = 1, 2, …: c = max(A(k0 + j), f / v_new).
    (From rest k0 = 0, which is the position-move rule without a D term.)
  - Decelerate to 0 < v_new < v_cur: R = v_cur² / (2α_d); step j = 0, 1, …: r = R − j,
    c = D(r) while r ≥ 1 and D(r) < f / v_new, after that c = f / v_new (cruise). If R < 1, cruise at once.
  - Stop (JOG 0, controlled STOP, PAUSE, link watchdog, dead-man): r0 = ceil(v_cur² / (2α_d)); the motion
    ends after r0 more steps; step j = 0…r0−1: r = r0 − j, c = max(c_floor, D(r)) with c_floor = c_last
    (never faster than the current period). In a position move: r_stop = min(r_move, r0), same formula
    (the move's A / c_min terms are below c_last there, so c = max(c_last, D(r)) is equivalent).

  Controlled-stop path (ICD §6.5, D-30; FW_design §5.6.4): P = current step period [ticks],
  d = v² / (2·α_stop) [steps] with v = f / P:
      P > 2 ms and d ≤ 1 step  -> "CLEAN"   (clean immediate halt allowed)
      P ≤ 1 ms                 -> "ISR"     (applied by the next update)
      otherwise                -> "STRETCH" (running period reprogrammed, then planned deceleration)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

F_TICK = 90_000_000.0                       # TIM2 at 90 MHz, PSC 0 (R4 §1.4)
ISR_MAX_TICKS = 90_000                      # 1 ms at 90 MHz (FW CTRL_STOP_ISR_MAX_TICKS)
HALT_MIN_TICKS = 180_000                    # 2 ms (FW CTRL_STOP_HALT_MIN_TICKS, D-29 d)


def a_term(c_a: float, k: float) -> float:
    return c_a / (math.sqrt(k) + math.sqrt(k - 1.0))


def d_term(c_d: float, r: float) -> float:
    return c_d / (math.sqrt(r) + math.sqrt(r - 1.0))


@dataclass
class Gen:
    """Period generator with the fractional carry (one accumulator per motion)."""

    f: float = F_TICK
    acc: float = 0.0
    periods: list[int] = field(default_factory=list)
    c_last: float = 0.0

    def emit(self, c: float) -> None:
        self.c_last = c
        self.acc += c
        ci = int(self.acc)                  # floor (acc >= 0)
        self.acc -= ci
        self.periods.append(ci)


def ramp_move(n_steps: int, f: float, v: float, a: float, d: float) -> list[int]:
    """R4 TV-M ramp_periods: position move of n steps from rest to rest."""
    g = Gen(f)
    c_a, c_d, c_min = f * math.sqrt(2.0 / a), f * math.sqrt(2.0 / d), f / v
    for i in range(1, n_steps + 1):
        g.emit(max(a_term(c_a, i), c_min, d_term(c_d, n_steps - i + 1)))
    return g.periods


def move_with_stop(n_steps: int, f: float, v: float, a: float, d: float, stop_after: int,
                   a_stop: float) -> list[int]:
    """Position move with a controlled stop requested after `stop_after` emitted steps."""
    g = Gen(f)
    c_a, c_d, c_min = f * math.sqrt(2.0 / a), f * math.sqrt(2.0 / d), f / v
    for i in range(1, stop_after + 1):
        g.emit(max(a_term(c_a, i), c_min, d_term(c_d, n_steps - i + 1)))
    r_move = n_steps - stop_after
    _stop(g, a_stop, r_move)
    return g.periods


def _stop(g: Gen, a_stop: float, r_limit: int | None = None) -> None:
    v_cur = g.f / g.c_last
    r0 = math.ceil(v_cur * v_cur / (2.0 * a_stop))
    if r_limit is not None:
        r0 = min(r0, r_limit)
    c_s = g.f * math.sqrt(2.0 / a_stop)
    floor = g.c_last
    for j in range(r0):
        g.emit(max(floor, d_term(c_s, r0 - j)))


def jog(f: float, a: float, d: float, a_stop: float, segments: list[tuple[float, int]]) -> list[int]:
    """Velocity mode. segments = [(v_target, steps), ...]: run `steps` steps toward v_target (accelerating or
    decelerating per the virtual-index rules, then cruising); v_target = 0 = stop (steps ignored)."""
    g = Gen(f)
    c_a, c_d = f * math.sqrt(2.0 / a), f * math.sqrt(2.0 / d)
    for v_t, n in segments:
        if v_t == 0:
            _stop(g, a_stop)
            break
        c_tgt = f / v_t
        if not g.periods or g.c_last > c_tgt:            # accelerate (from rest: k0 = 0)
            k0 = 0.0 if not g.periods else (f / g.c_last) ** 2 / (2.0 * a)
            for j in range(1, n + 1):
                g.emit(max(a_term(c_a, k0 + j), c_tgt))
        else:                                             # decelerate to v_t, then cruise
            r = (f / g.c_last) ** 2 / (2.0 * d)
            slowing = True
            for _ in range(n):
                if slowing and r >= 1.0 and d_term(c_d, r) < c_tgt:
                    g.emit(d_term(c_d, r))
                    r -= 1.0
                else:
                    slowing = False
                    g.emit(c_tgt)
    return g.periods


def plan_trapezoid(n_steps: int, v: float, a: float, d: float) -> dict:
    """R4 §1.5 planner (SW previews / timeouts)."""
    n_acc, n_dec = v * v / (2 * a), v * v / (2 * d)
    if n_acc + n_dec <= n_steps:
        return {"kind": "trap", "n_acc": round(n_acc), "n_cruise": n_steps - round(n_acc) - round(n_dec),
                "n_dec": round(n_dec), "v_peak": v, "t": v / a + v / d + (n_steps - n_acc - n_dec) / v}
    na = n_steps * d / (a + d)
    vp = math.sqrt(2 * a * na)
    return {"kind": "tri", "n_acc": round(na), "n_cruise": 0, "n_dec": n_steps - round(na), "v_peak": vp,
            "t": vp / a + vp / d}


def ctrl_stop_path(p_ticks: int, f: float, a_stop: float) -> tuple[str, float]:
    """ICD §6.5 / FW_design §5.6.4 path selection; returns (path, d_steps)."""
    v = f / p_ticks
    d_steps = v * v / (2.0 * a_stop)
    if p_ticks > HALT_MIN_TICKS and d_steps <= 1.0:
        return "CLEAN", d_steps
    if p_ticks <= ISR_MAX_TICKS:
        return "ISR", d_steps
    return "STRETCH", d_steps


def steps_per_s(um_per_s: float, spm: float) -> float:
    """Wire speed / acceleration (µm/s, µm/s²) -> steps/s, steps/s² (binary64, spm = binary32 value)."""
    return um_per_s * spm / 1000.0
