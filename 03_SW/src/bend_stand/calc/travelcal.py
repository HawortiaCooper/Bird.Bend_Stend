"""Travel (steps/mm) calibration — pure functions (R4 §5, TV-TC; SRS SW-CAL-002/003).

* Step 1: ``N1 = round(L1·spm0)`` steps (round half away from zero, SYS-003), measured ``D1`` → ``spm1 = N1/D1``.
* Step 2: ``N2 = round(L2·spm1)``, measured **total** ``D_tot`` → ``spm2 = (N1 + N2)/D_tot``; consistency
  ``N2/(D_tot − D1) / spm1 − 1`` (warn "repeat" above 0.5 %).
* ``spm_from_steps`` uses the FW-reported step counts (MOVE_DONE ``value2``), not the nominal ones.
* ``travel_plausibility`` — SW-CAL-003: **reject** D ≤ 0 or a result outside 100…10 000 steps/mm; **confirm** a
  change > 20 % (with the D-27 candidates 800 / 160 steps/mm), a change > 5 % or a value outside ±20 % of the
  expected steps/mm; **warn** "repeat" when the consistency exceeds 0.5 %.
* ``target_for_steps`` — the absolute µm target that makes the FW issue exactly ``n`` steps from ``s_ref`` with its
  own conversion (``calc.motion.um_to_steps`` = FW rounding, binary32 steps/mm).

Implements: SW-CAL-002, SW-CAL-003, SW-CAL-004 (record values)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from bend_stand.calc.motion import f32, steps_to_um, um_to_steps
from bend_stand.calc.rounding import round_half_away

SPM_MIN = 100.0
SPM_MAX = 10_000.0
CHANGE_CONFIRM_BIG = 0.20
CHANGE_CONFIRM = 0.05
EXPECTED_BAND = 0.20
CONSISTENCY_WARN = 0.005
DIP_CANDIDATES = (800.0, 160.0)      # 4000 p/rev closed loop (D-27 target) / 800 p/rev (DIP not changed)
DIP_TEXT = ("expected values from the driver DIP setting: 800 steps/mm = 4000 pulses/rev (approved setting, D-27); "
            "160 steps/mm = 800 pulses/rev (DIP change not applied)")


def travel_cal_step1(spm_old: float, cmd_mm: float, meas_mm: float) -> tuple[int, float]:
    """``(N1, spm1)`` (TV-TC): ``N1 = round(cmd·spm_old)``, ``spm1 = N1/meas``."""
    if not meas_mm > 0:
        raise ValueError("measured distance must be > 0")
    n1 = round_half_away(cmd_mm * spm_old)
    return n1, n1 / meas_mm


def travel_cal_step2(spm1: float, n1: int, cmd_mm: float, meas_total_mm: float,
                     meas1_mm: float) -> tuple[int, float, float, float]:
    """``(N2, spm2, spm2_inc, consistency)`` (TV-TC)."""
    if not meas_total_mm > meas1_mm > 0:
        raise ValueError("total distance must exceed the step-1 distance (both > 0)")
    n2 = round_half_away(cmd_mm * spm1)
    spm2 = (n1 + n2) / meas_total_mm
    inc = n2 / (meas_total_mm - meas1_mm)
    return n2, spm2, inc, inc / spm1 - 1.0


def spm_from_steps(n_steps: int, meas_mm: float) -> float:
    if not meas_mm > 0:
        raise ValueError("measured distance must be > 0")
    return n_steps / meas_mm


def consistency(n2: int, d_tot_mm: float, d1_mm: float, spm1: float) -> tuple[float, float]:
    """``(spm_inc, consistency)`` of step 2 with the actual step count."""
    if not d_tot_mm > d1_mm:
        raise ValueError("total distance must exceed the step-1 distance")
    inc = n2 / (d_tot_mm - d1_mm)
    return inc, inc / spm1 - 1.0


@dataclass(frozen=True)
class TravelCheck:
    reject: tuple[str, ...] = ()
    confirm: tuple[tuple[str, str], ...] = ()      # (code, text)
    warn: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.reject


def travel_plausibility(spm_new: float, spm_old: float, expected_spm: float | None = None, *,
                        d_mm: float | None = None, consistency_rel: float | None = None) -> TravelCheck:
    """SW-CAL-003 rules (pure)."""
    reject: list[str] = []
    confirm: list[tuple[str, str]] = []
    warn: list[tuple[str, str]] = []
    if d_mm is not None and not d_mm > 0:
        reject.append("measured distance must be > 0 mm")
    if not math.isfinite(spm_new) or not SPM_MIN <= spm_new <= SPM_MAX:
        reject.append(f"result {spm_new:.3f} steps/mm outside {SPM_MIN:g}…{SPM_MAX:g}")
    if reject:
        return TravelCheck(tuple(reject))
    change = abs(spm_new / spm_old - 1.0) if spm_old > 0 else math.inf
    if change > CHANGE_CONFIRM_BIG:
        confirm.append(("SPM_CHANGE_20", f"steps/mm changes by {100 * change:.1f} % ({spm_old:.3f} → {spm_new:.3f}); "
                                         + DIP_TEXT + " — confirm that the measurement is right"))
    elif change > CHANGE_CONFIRM:
        confirm.append(("SPM_CHANGE_5", f"steps/mm changes by {100 * change:.1f} % ({spm_old:.3f} → "
                                        f"{spm_new:.3f}) — confirm"))
    if expected_spm and abs(spm_new / expected_spm - 1.0) > EXPECTED_BAND:
        confirm.append(("SPM_EXPECTED", f"{spm_new:.3f} steps/mm is outside ±20 % of the expected "
                                        f"{expected_spm:g} steps/mm — confirm"))
    if consistency_rel is not None and abs(consistency_rel) > CONSISTENCY_WARN:
        warn.append(("REPEAT", f"step 2 alone gives {100 * consistency_rel:+.2f} % vs step 1 (> 0.5 %): "
                               "measurement inconsistent — repeat"))
    return TravelCheck((), tuple(confirm), tuple(warn))


def target_for_steps(s_ref: int, n: int, spm: float) -> tuple[int, int]:
    """Absolute target (µm) so that the FW moves ``n`` steps from step position ``s_ref``; returns
    ``(target_um, n_actual)`` (``n_actual`` = steps the FW will issue with its own rounding, binary32 spm)."""
    s = f32(spm)
    want = int(s_ref) + int(n)
    target_um = steps_to_um(want, s)
    for cand in (target_um, target_um + 1, target_um - 1):      # FW rounding of µm → steps may differ by one
        if um_to_steps(cand, s) == want:
            return cand, n
    return target_um, um_to_steps(target_um, s) - int(s_ref)


__all__ = ["travel_cal_step1", "travel_cal_step2", "spm_from_steps", "consistency", "TravelCheck",
           "travel_plausibility", "target_for_steps", "DIP_CANDIDATES", "DIP_TEXT", "SPM_MIN", "SPM_MAX"]
