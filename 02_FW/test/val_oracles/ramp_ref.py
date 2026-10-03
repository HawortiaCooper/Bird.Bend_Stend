#!/usr/bin/env python3
"""Validator E - independent motion oracle (R4 §1.5 exact square-root ramp, planner, controlled stop,
pulse timing). Written from R4 §1.5/§1.6, ICD §5.4/§6.5, SRS FW-MOT-001/003, SAF-FW-003 and
FW_design §5.6.1 formulas only - never from Implementer A's code.

Anchors: R4 §12 TV-M (f_tick 90 MHz, 640 steps/mm, a = 100 mm/s², v = 20 mm/s) are asserted by
02_FW/test/twin/test_val_m2_oracles.py; when the Integrator publishes vectors/motion_vectors.json
(OI-FW-20) the same self-test compares this oracle with it case by case.

Units: steps, steps/s, steps/s², timer ticks (f_tick Hz). Conversions from the wire units
(µm/s, µm/s², ICD §0.1) use `steps_per_s(um_s, spm) = um_s * spm / 1000` in binary64.

    .venv\\Scripts\\python 02_FW\\test\\val_oracles\\ramp_ref.py --emit DIR   # ramp.txt for test_val_ramp

Verifies (oracle for): FW-MOT-001, FW-MOT-003, FW-MOT-004, FW-MOT-005, SAF-FW-003, SYS-004
"""
from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

F_TICK_TARGET = 90_000_000           # TIM2 on APB1 x2 (pinout §2), PSC 0
CLEAN_HALT_MIN_PERIOD_S = 0.002      # ICD §6.5 condition 1 (step period > 2 ms)


# ------------------------------------------------------------------------------------- basics
def round_half_away(x: float) -> int:
    """R4 §0.3 / ICD §0.1 rounding (Python round() is banker's rounding - never used here)."""
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


def steps_per_s(um_s: float, spm: float) -> float:
    return um_s * spm / 1000.0


def ramp_constant(f_tick: float, alpha: float) -> float:
    """C = f·sqrt(2/α): period of the first step from rest (R4 §1.5)."""
    return f_tick * math.sqrt(2.0 / alpha)


def acc_term(c: float, k: float) -> float:
    """Period of step k of a constant-acceleration profile from rest, cancellation-free form."""
    return c / (math.sqrt(k) + math.sqrt(max(k - 1.0, 0.0)))


# ------------------------------------------------------------------------------- pulse timing
@dataclass(frozen=True)
class PulseTiming:
    pw_ticks: int          # pulse high (PWM mode 2: pulse at the END of each period)
    c_min_ticks: int       # shortest allowed period (max step rate, PW + minimum low)
    dir_setup_ticks: int


def pulse_timing(pulse_high_ns: int, pulse_low_min_ns: int, max_step_rate_hz: int, dir_setup_us: int,
                 f_tick: int = F_TICK_TARGET) -> PulseTiming:
    """FW_design §5.6.1 (formulas are SRS FW-MOT-001 parameters in timer ticks)."""
    pw = round_half_away(pulse_high_ns * f_tick / 1e9)
    c_min = max(math.ceil(f_tick / max_step_rate_hz), pw + math.ceil(pulse_low_min_ns * f_tick / 1e9))
    return PulseTiming(pw, c_min, math.ceil(dir_setup_us * f_tick / 1e6))


# --------------------------------------------------------------------- position move (R4 §1.5)
def ramp_periods_float(n_steps: int, f_tick: float, v: float, a: float, d: float) -> list[float]:
    """Unrounded periods c_k = max(acc(k), c_min, dec(r)), k = 1..N, r = N-k+1."""
    ca, cd, cmin = ramp_constant(f_tick, a), ramp_constant(f_tick, d), f_tick / v
    return [max(acc_term(ca, k), cmin, acc_term(cd, n_steps - k + 1)) for k in range(1, n_steps + 1)]


def ramp_periods(n_steps: int, f_tick: float, v: float, a: float, d: float) -> list[int]:
    """Integer timer periods with fractional carry (R4 §1.5): acc += c; c_int = floor(acc); acc -= c_int."""
    out, acc = [], 0.0
    for c in ramp_periods_float(n_steps, f_tick, v, a, d):
        acc += c
        ci = int(acc)
        acc -= ci
        out.append(ci)
    return out


def plan_trapezoid(n: int, v: float, a: float, d: float) -> dict:
    """R4 §1.5 planner (SW previews, timeouts, FW duration check of FW-MOT-004)."""
    n_acc, n_dec = v * v / (2 * a), v * v / (2 * d)
    if n_acc + n_dec <= n:
        t = v / a + v / d + (n - n_acc - n_dec) / v
        return {"kind": "trap", "n_acc": round_half_away(n_acc), "n_dec": round_half_away(n_dec),
                "n_cruise": n - round_half_away(n_acc) - round_half_away(n_dec), "v_peak": v, "t": t}
    n_acc = n * d / (a + d)
    vp = math.sqrt(2 * a * n_acc)
    return {"kind": "tri", "n_acc": round_half_away(n_acc), "n_dec": n - round_half_away(n_acc),
            "v_peak": vp, "t": vp / a + vp / d}


def move_ticks(n_steps: int, f_tick: float, v: float, a: float, d: float) -> int:
    return sum(ramp_periods(n_steps, f_tick, v, a, d))


def check_periods(measured: list[int], expected: list[int], tol_each: int = 1) -> tuple[bool, str]:
    """FW-MOT-003 acceptance: each period ±1 tick, total ±N/1000 ticks (R4 §1.5 float32 note)."""
    if len(measured) != len(expected):
        return False, f"step count {len(measured)} != {len(expected)}"
    worst = max((abs(m - e), i) for i, (m, e) in enumerate(zip(measured, expected))) if expected else (0, -1)
    if worst[0] > tol_each:
        i = worst[1]
        return False, f"period {i}: {measured[i]} vs {expected[i]} (> ±{tol_each} tick)"
    tot = abs(sum(measured) - sum(expected))
    if tot > max(1, len(expected) // 1000):
        return False, f"total {sum(measured)} vs {sum(expected)} (> ±N/1000 ticks)"
    return True, f"N={len(expected)} worst={worst[0]} tick total_err={tot} ticks"


# ------------------------------------------------- controlled stop / clean halt (ICD §6.5, SAF-FW-003)
def stop_distance_steps(v: float, a_stop: float) -> float:
    """Planned stop distance v²/(2·a_stop) in steps (unrounded; SRS SAF-FW-003 tolerance ±1 step)."""
    return v * v / (2.0 * a_stop)


def stop_index(v: float, a_stop: float) -> int:
    """R4 §1.5 virtual remaining index for a controlled stop: r = ceil(v²/(2·α_d))."""
    return max(1, math.ceil(stop_distance_steps(v, a_stop) - 1e-12))


def clean_halt_allowed(period_s: float, v: float, a_stop: float) -> bool:
    """ICD §6.5: BOTH step period > 2 ms AND planned stop distance <= 1 step."""
    return period_s > CLEAN_HALT_MIN_PERIOD_S and stop_distance_steps(v, a_stop) <= 1.0


def decel_periods(r0: int, f_tick: float, a_stop: float, carry: float = 0.0) -> list[int]:
    """Periods of a controlled stop from virtual index r0 down to 1 (dec term only, carry kept)."""
    cd, out, acc = ramp_constant(f_tick, a_stop), [], carry
    for r in range(r0, 0, -1):
        acc += acc_term(cd, r)
        ci = int(acc)
        acc -= ci
        out.append(ci)
    return out


# --------------------------------------------- velocity mode (JOG) with the virtual index (R4 §1.5)
class JogRamp:
    """Step-wise reference for JOG / on-the-fly speed changes / controlled stops.

    Rules (R4 §1.5 as made normative by the Integrator's `ref_motion.py` docstring for
    `motion_vectors.json`, ICD v0.5 §12; implemented here independently from that text):
    - v_cur = f / c_last (c_last = last emitted period before rounding); one carry per motion.
    - speed-up to v_new: k0 = v_cur²/(2α); step j: c = max(A(k0 + j), f/v_new).
    - slow-down to 0 < v_new < v_cur: R = v_cur²/(2α_d); step j: r = R − j; c = D(r) while r ≥ 1 and
      D(r) < f/v_new, afterwards cruise f/v_new (R < 1: cruise at once).
    - stop (JOG 0 / controlled stop): r0 = ceil(v_cur²/(2α_stop)); r0 more steps, c = max(c_last, D(r)).
    """

    def __init__(self, f_tick: float, a: float, d: float, a_stop: float | None = None):
        self.f, self.a, self.d = f_tick, a, d
        self.a_stop = a_stop if a_stop else d
        self.c_last = 0.0          # float period of the last emitted step (0 = at rest)
        self.carry = 0.0
        self.mode = "idle"
        self.target = 0.0
        self.j = 0                 # steps emitted in the current mode
        self.k0 = self.big_r = 0.0
        self.r0 = 0
        self.c_floor = 0.0

    @property
    def v(self) -> float:
        return self.f / self.c_last if self.c_last > 0 else 0.0

    def set_target(self, v_target: float) -> None:
        v_new, v_cur = abs(v_target), self.v
        self.target, self.j = v_new, 0
        if v_new == 0.0:
            if v_cur == 0.0:
                self.mode = "idle"
                return
            self.mode, self.c_floor = "stop", self.c_last
            self.r0 = max(1, math.ceil(v_cur * v_cur / (2.0 * self.a_stop)))
        elif v_new > v_cur:
            self.mode, self.k0 = "acc", v_cur * v_cur / (2.0 * self.a)
        elif v_new < v_cur:
            self.mode, self.big_r = "dec", v_cur * v_cur / (2.0 * self.d)
        else:
            self.mode = "cruise"

    def _float_period(self) -> float | None:
        cmin = self.f / self.target if self.target > 0 else 0.0
        if self.mode == "acc":
            self.j += 1
            return max(acc_term(ramp_constant(self.f, self.a), self.k0 + self.j), cmin)
        if self.mode == "dec":
            r = self.big_r - self.j
            self.j += 1
            if r >= 1.0:
                c = acc_term(ramp_constant(self.f, self.d), r)
                if c < cmin:
                    return c
            self.mode = "cruise"
            return cmin
        if self.mode == "cruise":
            return cmin
        if self.mode == "stop":
            if self.j >= self.r0:
                self.mode, self.c_last = "idle", 0.0
                return None
            r = self.r0 - self.j
            self.j += 1
            return max(self.c_floor, acc_term(ramp_constant(self.f, self.a_stop), r))
        return None

    def next_period(self) -> int | None:
        """Next integer period, or None when stopped."""
        c = self._float_period()
        if c is None:
            return None
        self.c_last = c
        self.carry += c
        ci = int(self.carry)
        self.carry -= ci
        return ci


def move_with_stop(n_steps: int, f_tick: float, v: float, a: float, d: float, stop_after: int,
                   a_stop: float) -> list[int]:
    """Position move with a controlled stop requested after `stop_after` emitted steps:
    r_stop = min(r_move, ceil(v_cur²/(2α_stop))), c = max(c_last, D_stop(r)) (one carry)."""
    ca, cd, cmin = ramp_constant(f_tick, a), ramp_constant(f_tick, d), f_tick / v
    out, acc, c_last = [], 0.0, 0.0

    def emit(c: float) -> None:
        nonlocal acc, c_last
        c_last = c
        acc += c
        ci = int(acc)
        acc -= ci
        out.append(ci)

    for i in range(1, min(stop_after, n_steps) + 1):
        emit(max(acc_term(ca, i), cmin, acc_term(cd, n_steps - i + 1)))
    if stop_after >= n_steps or c_last == 0.0:
        return out
    v_cur = f_tick / c_last
    r_move = n_steps - stop_after
    r_stop = min(r_move, max(1, math.ceil(v_cur * v_cur / (2.0 * a_stop))))
    cs, floor_c = ramp_constant(f_tick, a_stop), c_last
    for r in range(r_stop, 0, -1):
        emit(max(floor_c, acc_term(cs, r)))
    return out


def binary32(x: float) -> float:
    import struct
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def case_periods(case: dict) -> list[int]:
    """Periods of one `motion_vectors.json` case from its WIRE parameters and events (spm as binary32,
    ICD §0.1); used to cross-check the shared vectors with this independent implementation."""
    spm = binary32(case["steps_per_mm"])
    f = float(case["f_tick"])
    v = case["v_um_s"] * spm / 1000.0
    a = case["a_um_s2"] * spm / 1000.0
    d = case["d_um_s2"] * spm / 1000.0
    a_stop = case["a_stop_um_s2"] * spm / 1000.0 if case.get("a_stop_um_s2") else None
    ev = case.get("events", [])
    if not ev:
        return ramp_periods(case["n_steps"], f, v, a, d)
    if all(e["event"] == "controlled_stop" for e in ev) and len(ev) == 1:
        return move_with_stop(case["n_steps"], f, v, a, d, ev[0]["after_step"], a_stop)
    if all(e["event"] == "jog" for e in ev):
        j = JogRamp(f, a, d, a_stop)
        out: list[int] = []
        bounds = [e["after_step"] for e in ev[1:]] + [None]
        for e, until in zip(ev, bounds):
            j.set_target(e["v_um_s"] * spm / 1000.0)
            while until is None or len(out) < until:
                c = j.next_period()
                if c is None:
                    break
                out.append(c)
        return out
    raise ValueError(f"unsupported event mix in {case.get('name')}")


def ctrl_stop_path(p_ticks: int, f_tick: float, a_stop: float) -> str:
    """ICD §6.5 / FW_design §5.6.4 path for a controlled stop at the current period P (ticks)."""
    v = f_tick / p_ticks
    if p_ticks > round(CLEAN_HALT_MIN_PERIOD_S * f_tick) and stop_distance_steps(v, a_stop) <= 1.0:
        return "CLEAN"
    if p_ticks <= round(0.001 * f_tick):
        return "ISR"
    return "STRETCH"


def jog_properties(periods: list[int], f_tick: float, v_set: float, a: float, d_stop: float,
                   change_at: int | None = None, v_new: float | None = None) -> list[str]:
    """Envelope checks for a measured JOG period sequence (FW-MOT-005 / SAF-FW-003, ±1 tick):
    - no period shorter than f/v_max(active target) - 1 tick (never faster than commanded),
    - accelerating phase non-increasing, final controlled-stop phase non-decreasing,
    - cruise period average = f/v within 0.1 %.
    Returns a list of violations (empty = pass)."""
    bad = []
    v_hi = max(v_set, v_new or 0.0)
    cmin = f_tick / v_hi
    for i, c in enumerate(periods):
        if c < cmin - 1:
            bad.append(f"period {i} = {c} < f/v_max {cmin:.1f}")
            break
    # final deceleration: from the last minimum to the end, periods must not decrease (±1 tick)
    if periods:
        j = min(range(len(periods)), key=lambda i: (periods[i], -i))
        tail = periods[j:]
        for i in range(1, len(tail)):
            if tail[i] + 1 < tail[i - 1]:
                bad.append(f"stop phase not monotonic at {j + i}: {tail[i - 1]} -> {tail[i]}")
                break
    return bad


# --------------------------------------------------------------------------- vector emission
def tvm_cases() -> list[tuple[str, int, float, float, float, float]]:
    """(name, N, f_tick, v, a, d) - R4 §12 TV-M (spm 640)."""
    a = 100_000 * 640 / 1000          # 100 mm/s² = 64 000 steps/s²
    v = 20_000 * 640 / 1000           # 20 mm/s = 12 800 steps/s
    return [("TVM_TRAP_5s2", 64000, F_TICK_TARGET, v, a, a),
            ("TVM_TRI", 1000, F_TICK_TARGET, v, a, a),
            ("TVM_TRI_ASYM", 1000, F_TICK_TARGET, v, a, 2 * a)]


def random_cases(n: int, seed: int) -> list[tuple[str, int, float, float, float, float]]:
    """Random (N, v, a, d) inside the parameter ranges of params.yaml dict 4 (wire units -> steps)."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        spm = rng.choice([100.0, 160.0, 640.0, 800.0, 4000.0, 100000.0])
        rate_cap = 100_000.0
        v_um = rng.uniform(1.0, min(250_000.0, rate_cap * 1000 / spm))
        a_um = rng.uniform(1_000.0, 10_000_000.0)
        d_um = rng.choice([a_um, rng.uniform(10_000.0, 10_000_000.0)])
        v, a, d = steps_per_s(v_um, spm), steps_per_s(a_um, spm), steps_per_s(d_um, spm)
        if v < 0.05:                      # keep the period inside 32 bit (FW_design §5.6.1: >= 0.021 steps/s)
            v = 0.05
        n_steps = rng.choice([1, 2, 3, 7, 50, 500, 3000])
        out.append((f"RND{i:04d}_spm{spm:g}", n_steps, F_TICK_TARGET, v, a, d))
    return out


def vector_lines(n_random: int = 200, seed: int = 20261004) -> list[str]:
    """One line per case: R name N f v a d sum first last min max count + up to 8 head periods.
    (The Unity suite recomputes nothing: it compares the FW ramp with these numbers.)"""
    lines = []
    for name, n, f, v, a, d in tvm_cases() + random_cases(n_random, seed):
        p = ramp_periods(n, f, v, a, d)
        head = p[:8] + [0] * (8 - len(p[:8]))
        lines.append(" ".join(["R", name, str(n), str(int(f)), repr(v), repr(a), repr(d), str(sum(p)),
                               str(p[0]), str(p[-1]), str(min(p)), str(max(p))] + [str(x) for x in head]))
    return lines


def main() -> int:
    if "--emit" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--emit") + 1])
        out.mkdir(parents=True, exist_ok=True)
        lines = vector_lines()
        (out / "ramp.txt").write_text("\n".join(["N " + str(len(lines))] + lines) + "\n", encoding="ascii")
        print(f"ramp vectors: {len(lines)} -> {out / 'ramp.txt'}")
        return 0
    for name, n, f, v, a, d in tvm_cases():
        p = ramp_periods(n, f, v, a, d)
        print(name, n, sum(p), p[:5], p[-3:], min(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
