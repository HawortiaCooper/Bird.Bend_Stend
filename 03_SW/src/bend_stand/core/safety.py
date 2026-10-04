"""Safety layer of the backend (SW_design §6). M2: the FW load-threshold manager (§6.3, SAF-SW-002); the per-frame
SW limit supervisor (§6.1, SAF-SW-001) and the no-specimen mode (§6.7) are M3.

``ThresholdManager`` owns the three session parameters ``safety.load_raw_min``, ``safety.load_raw_max`` and
``safety.zero_raw`` (never saved, ICD §11.5). Target paths:

* **DEFAULT** — no calibration / tare: the dictionary defaults (±7 022 271 counts around raw 0, ≈ ±109 % FS) →
  state ``DEFAULT_ONLY`` after the read-back.
* **MANUAL** (M2 bring-up) — operator-entered raw thresholds (checked against the dictionary ranges and H2) →
  ``VERIFIED`` with ``cal_id = "manual-raw"``.
* **CALIBRATED** (path for M3) — ``calc.loadcal.fw_raw_limits(±fw_level_n, k, tare_raw)`` rounded toward the tare,
  clamped inward to the dictionary range with a warning (D-29 g) → ``VERIFIED`` (+ ``clamped``) or ``INVALID``.

``apply_job()`` writes only the values that differ, in an order that keeps H2 (``raw_min < raw_max``) true after
every SET (``core.params.write_plan``), then reads all three back with GET_PARAM and compares exactly; any NACK,
timeout or mismatch → ``FAILED`` (motion gates refuse, ``limits.recheck_async()`` retries). Triggers: connect /
EVENT BOOT resync, LOAD / DEFAULT_PARAMS, ``limits.recheck_async()``, a new target.

Implements: SAF-SW-002 (M2 part: default / manual paths, write + read-back verify; calibrated computation path)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from bend_stand.calc.loadcal import clamp_raw_limits, effective_force_n, fw_raw_limits
from bend_stand.calc.rounding import round_half_away
from bend_stand.core import params_gen as pgen
from bend_stand.core.errors import CommandTimeout, LinkError
from bend_stand.core.jobs import Job
from bend_stand.core.model import Issue, IssueSeverity, ThresholdState
from bend_stand.core.params import write_plan

if TYPE_CHECKING:  # pragma: no cover
    from bend_stand.core.device import Device

KEYS = ("safety.load_raw_min", "safety.load_raw_max", "safety.zero_raw")
FS_N = 200.0 * 9.80665                       # SRS §2: 200 kg full scale
FW_LEVEL_MAX_N = 1.10 * FS_N                 # D-12: never above 110 % FS


@dataclass(frozen=True)
class ThresholdTarget:
    mode: Literal["DEFAULT", "MANUAL", "CALIBRATED"] = "DEFAULT"
    raw_min: int = int(pgen.BY_KEY["safety.load_raw_min"].default)
    raw_max: int = int(pgen.BY_KEY["safety.load_raw_max"].default)
    zero_raw: int = int(pgen.BY_KEY["safety.zero_raw"].default)
    cal_id: str | None = None
    tare_id: str | None = None
    fw_level_n: float | None = None
    clamped: bool = False
    eff_pull_n: float | None = None
    eff_push_n: float | None = None
    invalid: str | None = None


def check_manual(raw_min: int, raw_max: int, zero_raw: int) -> list[Issue]:
    """Dictionary range of each value and H2 (``raw_min < raw_max``); the zero must lie between them."""
    out: list[Issue] = []
    for key, v in zip(KEYS, (raw_min, raw_max, zero_raw)):
        meta = pgen.BY_KEY[key]
        if not meta.in_range(int(v)):
            out.append(Issue(key, IssueSeverity.ERROR, "RANGE", f"{key} = {v} outside {meta.min}…{meta.max}"))
    if int(raw_min) >= int(raw_max):
        out.append(Issue("safety.load_raw_min", IssueSeverity.ERROR, "H2", "raw_min must be < raw_max (rule H2)"))
    elif not int(raw_min) < int(zero_raw) < int(raw_max):
        out.append(Issue("safety.zero_raw", IssueSeverity.ERROR, "ZERO_OUTSIDE",
                         "zero_raw must lie between raw_min and raw_max"))
    return out


def calibrated_target(k: float, tare_raw: float, fw_level_n: float, *, cal_id: str | None = None,
                      tare_id: str | None = None) -> ThresholdTarget:
    """SAF-SW-002 computation (R4 §4.4) with the inward clamp of D-29 g."""
    if not 0 < fw_level_n <= FW_LEVEL_MAX_N + 1e-9:
        raise ValueError(f"FW load-limit level must be in (0, {FW_LEVEL_MAX_N:.2f}] N (SW-LIM-002)")
    lo, hi = fw_raw_limits(+fw_level_n, -fw_level_n, k, tare_raw)
    mn, mx = pgen.BY_KEY["safety.load_raw_min"], pgen.BY_KEY["safety.load_raw_max"]
    zero = round_half_away(tare_raw)                     # SYS-003 (not banker's rounding)
    try:
        lo, hi, clamped = clamp_raw_limits(lo, hi, int(mn.min), int(mn.max), int(mx.min), int(mx.max))
    except ValueError as exc:
        return ThresholdTarget("CALIBRATED", cal_id=cal_id, tare_id=tare_id, fw_level_n=fw_level_n, invalid=str(exc))
    if not pgen.BY_KEY["safety.zero_raw"].in_range(zero) or not lo < zero < hi:
        return ThresholdTarget("CALIBRATED", cal_id=cal_id, tare_id=tare_id, fw_level_n=fw_level_n,
                               invalid="tare outside the threshold range")
    f_a, f_b = effective_force_n(hi, k, tare_raw), effective_force_n(lo, k, tare_raw)
    return ThresholdTarget("CALIBRATED", lo, hi, zero, cal_id, tare_id, fw_level_n, clamped, max(f_a, f_b),
                           min(f_a, f_b))


class ThresholdManager:
    def __init__(self, device: Device) -> None:
        self.device = device
        self.target = ThresholdTarget()

    # ---- targets --------------------------------------------------------------------------------------
    def set_default(self) -> ThresholdTarget:
        self.target = ThresholdTarget()
        return self.target

    def set_manual(self, raw_min: int, raw_max: int, zero_raw: int = 0) -> list[Issue]:
        issues = check_manual(raw_min, raw_max, zero_raw)
        if not issues:
            self.target = ThresholdTarget("MANUAL", int(raw_min), int(raw_max), int(zero_raw), cal_id="manual-raw")
        return issues

    def set_calibrated(self, k: float, tare_raw: float, fw_level_n: float, **ids: Any) -> ThresholdTarget:
        self.target = calibrated_target(k, tare_raw, fw_level_n, **ids)
        return self.target

    # ---- write + verify ---------------------------------------------------------------------------------
    def _state(self, t: ThresholdTarget, state: str, text: str = "") -> ThresholdState:
        return ThresholdState(state, t.cal_id, t.tare_id, t.fw_level_n, t.raw_min, t.raw_max, t.zero_raw,  # type: ignore[arg-type]
                              t.clamped, t.eff_pull_n, t.eff_push_n, text)

    def apply_job(self) -> Job:
        d = self.device
        t = self.target
        if t.invalid:
            st = self._state(t, "INVALID", t.invalid)
            d.thresholds = st
            d.events.publish("safety.thresholds", st)
            return st
        want = {"safety.load_raw_min": t.raw_min, "safety.load_raw_max": t.raw_max, "safety.zero_raw": t.zero_raw}
        cur = {k: d.params.get(k) for k in KEYS}
        try:
            for k, v in write_plan(cur, want):
                yield from d.set_param_job(k, v)
            for k in KEYS:
                got = yield from d.get_param_job(k)
                if got != want[k]:
                    raise LinkError(f"{k}: read-back {got} != {want[k]}")
        except (LinkError, CommandTimeout) as exc:
            st = self._state(t, "FAILED", str(exc))
        else:
            if t.mode == "DEFAULT":
                st = self._state(t, "DEFAULT_ONLY", "no calibration: board load limit at its nominal default")
            elif t.mode == "MANUAL":
                st = self._state(t, "VERIFIED", "manual raw thresholds (M2 bring-up)")
            else:
                st = self._state(t, "VERIFIED", "FW load limit clamped to the dictionary range" if t.clamped else "")
        d.thresholds = st
        d.events.publish("safety.thresholds", st)
        return st


__all__ = ["ThresholdManager", "ThresholdTarget", "calibrated_target", "check_manual", "KEYS", "FW_LEVEL_MAX_N"]
