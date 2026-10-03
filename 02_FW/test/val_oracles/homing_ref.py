#!/usr/bin/env python3
"""Validator E - independent homing oracle (FW-HOM-001/002/004, SAF-FW-021) for the twin world model.

Source: ICD v0.5 §5.4 HOME + §6.2, SRS FW-HOM-001…004, R4 §3.1, protocol.yaml constants
HOME_RELEASE_MAX_UM / HOME_SLOW_EXTRA_UM (read from ref_codec-independent YAML here). Not derived from
Implementer A's code.

World model (tools/README twin): x_true = count·1000/spm_world + shift; START active  <=>  x_true <= S
(`start_switch_um`), END active <=> x_true >= E. The FW zero is set so that the captured START edge lies
at FW x = -home.offset_um; afterwards the axis moves to FW x = 0.

Verifies (oracle for): FW-HOM-001, FW-HOM-002, FW-HOM-004, SAF-FW-021
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
_PY = yaml.safe_load((ROOT / "00_System" / "specs" / "protocol.yaml").read_text(encoding="utf-8"))
CONST = {c["name"]: c["value"] for c in _PY["constants"]}
HOME_RELEASE_MAX_UM = int(CONST["HOME_RELEASE_MAX_UM"])
HOME_SLOW_EXTRA_UM = int(CONST["HOME_SLOW_EXTRA_UM"])


def round_half_away(x: float) -> int:
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


@dataclass
class HomeParams:
    v_fast_um_s: int = 5000
    v_slow_um_s: int = 500
    a_um_s2: int = 50000
    backoff_um: int = 2000
    offset_um: int = 1000
    max_travel_um: int = 360000
    drift_tol_um: int = 200
    max_load_raw: int = 322123


@dataclass
class HomeExpect:
    result: str                          # "HOMED" | "HOME_NOT_FOUND" | "HOME_WIRING" | "E_CONFIRM"
    phases: list[str] = field(default_factory=list)
    x_final_um: tuple[float, float] | None = None      # closed interval for x_true at the end (HOMED)
    deviation_um: tuple[float, float] | None = None    # EVENT HOMED value interval
    drift_fault: bool | None = None                    # HOME_DRIFT latched (None = either, boundary case)
    fault_set: str | None = None


def step_um(spm: float) -> float:
    return 1000.0 / spm


def load_precheck(raw: int, zero_raw: int, max_load_raw: int, confirmed: bool) -> str:
    """SAF-FW-021 / ICD §5.4: abs(raw − zero_raw) > max_load_raw and flag bit0 = 0 → E_CONFIRM."""
    return "E_CONFIRM" if abs(raw - zero_raw) > max_load_raw and not confirmed else "OK"


def expect_home(x0_true_um: float, start_switch_um: float, spm: float, p: HomeParams, *,
                homed_before: bool = False, shift_since_last_home_um: float = 0.0,
                end_forced_in_seek: bool = False, start_stuck: bool = False) -> HomeExpect:
    """Expected outcome of HOME in the twin world (no load, no other stop source)."""
    s = start_switch_um
    st = step_um(spm)
    phases = ["PRECHECK"]
    if x0_true_um <= s:
        phases.append("RELEASE")
        if start_stuck:
            return HomeExpect("HOME_WIRING", phases, fault_set="HOME_WIRING")
    elif start_stuck:                     # forced active later -> same as "active at start"
        return HomeExpect("HOME_WIRING", phases + ["RELEASE"], fault_set="HOME_WIRING")
    phases.append("FAST_SEEK")
    if end_forced_in_seek:
        return HomeExpect("HOME_WIRING", phases, fault_set="HOME_WIRING")
    seek_start = max(x0_true_um, s + st)          # after RELEASE the axis is just above the switch (+backoff)
    if x0_true_um <= s:
        seek_start = s + st + p.backoff_um
    if seek_start - s > p.max_travel_um:
        return HomeExpect("HOME_NOT_FOUND", phases, fault_set="HOME_NOT_FOUND")
    phases += ["BACKOFF", "SLOW_APPROACH", "MOVE_TO_ZERO", "DONE"]
    n_off = round_half_away(p.offset_um * spm / 1000.0)
    x_mid = s + n_off * st
    # first step with x <= S is the edge: edge x in (S - st, S]; final x = edge + n_off·st
    x_final = (x_mid - st - 1e-6, x_mid + 1e-6)
    if homed_before:
        dev = -shift_since_last_home_um
        dev_iv = (dev - st - 1.0, dev + st + 1.0)          # ±1 step + µm rounding
        if abs(dev) > p.drift_tol_um + st + 1.0:
            drift = True
        elif abs(dev) < p.drift_tol_um - st - 1.0:
            drift = False
        else:
            drift = None
    else:
        dev_iv, drift = (0.0, 0.0), False
    return HomeExpect("HOMED", phases, x_final, dev_iv, drift, "HOME_DRIFT" if drift else None)


def slow_approach_bound_um(p: HomeParams) -> int:
    """SLOW_APPROACH gives up after backoff + HOME_SLOW_EXTRA_UM (ICD §5.4)."""
    return p.backoff_um + HOME_SLOW_EXTRA_UM


def phase_order_ok(seen: list[str], expected: list[str]) -> bool:
    """`seen` = de-duplicated home_phase sequence sampled from STATUS; must be a subsequence of expected
    (sampling may skip short phases) and end with the expected final phase."""
    it = iter(expected)
    return all(any(x == y for y in it) for x in seen) and (not seen or seen[-1] == expected[-1])
