"""Safety layer of the backend (SW_design §6): the per-frame SW-limit supervisor (§6.1, SAF-SW-001), the FW
load-threshold manager (§6.3, SAF-SW-002) and the inputs of the no-specimen mode (§6.7, SW-LIM-004).

**SafetySupervisor** (Pipeline thread, every DATA sample, before the ring buffer and the sinks — KD-05):

1. *Load* (a SW load limit enabled, no-specimen mode off): with a valid input (active calibration matching the AFE
   configuration, a tare of this board, raw state OK / settling, AFE not stale) ``F = K·(raw − tare)``; a saturated
   sample counts as ±∞ (``sign(K)·sign(rail)``); ``F`` beyond an enabled trip level → **trip**. Warning edges at
   ``warn_pct`` with 2 % hysteresis (topic ``safety.warning``).
2. *Load input invalid* while a load limit is enabled and the axis moves → STOP (reason ``LOAD_INPUT_INVALID``),
   operations terminated (the motion gates refuse new motion, SAF-SW-001 last sentence).
3. *Travel* (enabled limits, homed, moving): ``x`` = commanded position. While the running command's end point
   (MOVE_ABS target / JOG bound) lies inside the enabled limits, the trip fires only when ``x`` passes a limit by more
   than one step (a planned deceleration onto a bound equal to the limit is not a trip, D-33 d); for motion without
   such an end point (unbounded JOG, homing, commands of other clients) the predicted position ``x + v·75 ms``
   crossing a limit in the direction of motion trips.
4. *Direction-aware latch* (``SwTrip``): after a trip, motion that increases the violation is refused by the gates and
   stopped again if it happens anyway (≤ every 100 ms); the latch clears when the value is back inside the trip
   level by the hysteresis band (load) / inside the limit (travel), or when the limit is disabled.

Trip order (§6.2): (1) priority STOP (immediate), (2) terminate every running operation, (3) latch + indicator,
(4) event ``safety.trip`` + recording event row + log.

**ThresholdManager** owns ``safety.load_raw_min/max`` and ``safety.zero_raw`` (session parameters, never saved,
ICD §11.5). The target comes from a provider (backend: ``LoadInput.threshold_target``) unless a manual target
(M2 bring-up) is set:

* **DEFAULT** — no valid calibration / tare: dictionary defaults (±7 022 271 counts around raw 0) → ``DEFAULT_ONLY``;
* **MANUAL** — operator raw thresholds (dictionary ranges, H2) → ``VERIFIED`` with ``cal_id = "manual-raw"``;
* **CALIBRATED** — ``fw_raw_limits(±fw_level_n, k, tare_raw)`` rounded toward the tare, zero = round half away from
  zero (SYS-003), clamped inward to the dictionary range with a warning (D-29 g) → ``VERIFIED`` (+ ``clamped``) or
  ``INVALID``.

``apply_job()`` writes only the values that differ, in an order that keeps H2 true after every SET, reads all three
back with GET_PARAM and compares exactly; NACK / timeout / mismatch → ``FAILED`` (motion refused until a new target
or ``limits.recheck_async()``). ``matches(state)`` tells the gates whether the verified board values belong to the
current target (calibration + tare + FW level); ``needs_apply()`` lets the backend rewrite them automatically
whenever the target changes (calibration activated, tare / undo, FW level edit, AFE change, connect, BOOT).

Implements: SAF-SW-001, SAF-SW-002, SW-LIM-004 (FW thresholds in the no-specimen mode), SW-CAL-008 (warnings)
"""
from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from bend_stand.calc import limits as L
from bend_stand.calc.loadcal import clamp_raw_limits, effective_force_n, fw_raw_limits
from bend_stand.calc.rounding import round_half_away
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.errors import CommandTimeout, LinkError
from bend_stand.core.jobs import Job
from bend_stand.core.model import Issue, IssueSeverity, LimitConfig, SafetyWarning, SwTrip, ThresholdState
from bend_stand.core.params import write_plan

if TYPE_CHECKING:  # pragma: no cover
    from bend_stand.core.device import Device
    from bend_stand.core.pipeline import DataRow

log = logging.getLogger("bend_stand.core.safety")
KEYS = ("safety.load_raw_min", "safety.load_raw_max", "safety.zero_raw")
FS_N = 200.0 * 9.80665                       # SRS §2: 200 kg full scale
FW_LEVEL_MAX_N = 1.10 * FS_N                 # D-12: never above 110 % FS
MOVING, HOMED = int(pg.DataFlags.MOVING), int(pg.DataFlags.HOMED)
AFE_STALE_BIT = int(pg.DataStatus.AFE_STALE)
RAW_OK, RAW_SATURATED, RAW_NO_DATA, RAW_SETTLING = 0, 1, 2, 3
RESTOP_US = 100_000


# ============================================================================================ thresholds

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

    def key(self) -> tuple[Any, ...]:
        return (self.mode, self.raw_min, self.raw_max, self.zero_raw, self.cal_id, self.tare_id, self.fw_level_n,
                self.invalid)


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
        self.manual: ThresholdTarget | None = None
        self.provider: Callable[[], ThresholdTarget] | None = None
        self.applied_key: tuple[Any, ...] | None = None

    # ---- targets --------------------------------------------------------------------------------------
    def current_target(self) -> ThresholdTarget:
        if self.manual is not None:
            return self.manual
        if self.provider is not None:
            try:
                return self.provider()
            except Exception:  # noqa: BLE001 — never let a provider error widen anything: defaults
                log.exception("threshold provider failed")
                return ThresholdTarget()
        return self.target

    def set_default(self) -> ThresholdTarget:
        """Leave the manual path: the provider's target (calibrated or default) applies again."""
        self.manual = None
        self.target = ThresholdTarget()
        return self.current_target()

    def set_manual(self, raw_min: int, raw_max: int, zero_raw: int = 0) -> list[Issue]:
        issues = check_manual(raw_min, raw_max, zero_raw)
        if not issues:
            self.manual = ThresholdTarget("MANUAL", int(raw_min), int(raw_max), int(zero_raw), cal_id="manual-raw")
        return issues

    def set_calibrated(self, k: float, tare_raw: float, fw_level_n: float, **ids: Any) -> ThresholdTarget:
        self.target = calibrated_target(k, tare_raw, fw_level_n, **ids)
        return self.target

    def needs_apply(self) -> bool:
        return self.current_target().key() != self.applied_key

    def matches(self, st: ThresholdState) -> bool:
        """The verified board values belong to the current target (SAF-SW-002: motion only then)."""
        t = self.current_target()
        if t.invalid:
            return False
        want = "DEFAULT_ONLY" if t.mode == "DEFAULT" else "VERIFIED"
        return (st.state == want and st.cal_id == t.cal_id and st.tare_id == t.tare_id
                and st.fw_level_n == t.fw_level_n and (st.raw_min, st.raw_max, st.zero_raw)
                == (t.raw_min, t.raw_max, t.zero_raw))

    # ---- write + verify ---------------------------------------------------------------------------------
    def _state(self, t: ThresholdTarget, state: str, text: str = "") -> ThresholdState:
        return ThresholdState(state, t.cal_id, t.tare_id, t.fw_level_n, t.raw_min, t.raw_max, t.zero_raw,  # type: ignore[arg-type]
                              t.clamped, t.eff_pull_n, t.eff_push_n, text)

    def apply_job(self) -> Job:
        d = self.device
        t = self.current_target()
        self.target = t
        self.applied_key = t.key()
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
                st = self._state(t, "VERIFIED", f"FW load limit clamped to the dictionary range: effective "
                                                f"+{t.eff_pull_n:.1f} N / {t.eff_push_n:.1f} N" if t.clamped else "")
        d.thresholds = st
        d.events.publish("safety.thresholds", st)
        if t.clamped and st.state == "VERIFIED":
            d.events.publish("safety.warning", SafetyWarning("FW_CLAMPED", True, t.eff_pull_n, t.fw_level_n, st.text))
        return st


# ============================================================================================ supervisor

@dataclass(frozen=True)
class SafetyInputs:
    """What the supervisor needs besides the DATA row (built by the backend per frame)."""

    no_specimen: bool = False
    load_valid: bool = False                 # static load input (calibration + tare + AFE match, not synthetic)
    load_reason: str | None = "no calibration"
    k: float | None = None
    spm: float = 800.0
    pull_dir: int = 1
    end_um: int | None = None                # end point of the running command of this backend (None = unknown)


def _increasing(trip: SwTrip, v: float, pull_dir: int) -> bool:
    """Motion with velocity ``v`` (mm/s) increases the violation of ``trip``."""
    if not v or math.isnan(v):
        return False
    if trip.limit in ("TRAVEL_MAX", "TRAVEL_MIN"):
        return v * trip.side > 0
    return v * pull_dir * trip.side > 0


class SafetySupervisor:
    def __init__(self, *, stop: Callable[[str], Any], terminate: Callable[[str], Any],
                 publish: Callable[[str, Any], Any], event_row: Callable[[str, str], Any],
                 limits: Callable[[], LimitConfig], inputs: Callable[[], SafetyInputs]) -> None:
        self._stop, self._terminate, self._publish, self._row = stop, terminate, publish, event_row
        self._limits, self._inputs = limits, inputs
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Disconnect / new link: no latch, no warnings, no motion history."""
        self.trip: SwTrip | None = None
        self.warnings: set[str] = set()
        self._prev: tuple[int, float, int] | None = None
        self._invalid_stop_sent = False
        self._last_restop_us: int | None = None
        self.trips = 0
        self.last_f: float = float("nan")

    # ---- queries (any thread) -------------------------------------------------------------------------
    def direction_refused(self, direction: int, pull_dir: int = 1) -> SwTrip | None:
        """The latched trip if motion in ``direction`` (+1 / −1, travel sign) would increase its violation."""
        t = self.trip
        if t is None or direction == 0:
            return None
        return t if _increasing(t, float(direction), pull_dir) else None

    def active_warnings(self) -> tuple[str, ...]:
        return tuple(sorted(self.warnings))

    # ---- per sample (Pipeline thread) ----------------------------------------------------------------
    def process(self, row: DataRow) -> None:
        inp = self._inputs()
        cfg = self._limits()
        t, x = row.t_us_u, row.setpoint_um / 1000.0
        v = float("nan")
        prev = self._prev
        if prev is not None and prev[2] == row.epoch and t > prev[0]:
            v = (x - prev[1]) / ((t - prev[0]) / 1e6)
        self._prev = (t, x, row.epoch)
        moving = bool(row.flags & MOVING)
        if not moving:
            self._invalid_stop_sent = False
        f = self._force(row, inp)
        self.last_f = f
        self._release(cfg, f, x, moving)
        if self.trip is not None and moving and _increasing(self.trip, v, inp.pull_dir):
            last = self._last_restop_us
            if last is None or t - last >= RESTOP_US:
                self._last_restop_us = t
                self._stop(f"SW_LIMIT:{self.trip.limit}")
                self._terminate(f"SW_LIMIT:{self.trip.limit}")
        load_on = (cfg.pull_enabled or cfg.push_enabled) and not inp.no_specimen
        if load_on:
            if not math.isnan(f):
                side = L.evaluate_force_limit(f, cfg.pull_trip_n, cfg.pull_enabled, cfg.push_trip_n, cfg.push_enabled)
                if side is not None and self.trip is None:
                    thr = cfg.pull_trip_n if side == "PULL" else cfg.push_trip_n
                    self._trip(SwTrip(side, 1 if side == "PULL" else -1, f, thr, "N", t,
                                      f"{side.lower()} limit {thr:.1f} N exceeded: F = {f:.1f} N"))
            elif moving and not self._invalid_stop_sent:
                self._invalid_stop_sent = True
                reason = self._invalid_reason(row, inp)
                self._stop("SW_LIMIT:LOAD_INPUT_INVALID")
                self._terminate("LOAD_INPUT_INVALID")
                self._row("SW_TRIP", f"LOAD_INPUT_INVALID: {reason}")
                self._publish("safety.trip", SwTrip("LOAD_INPUT_INVALID", 0, float("nan"), float("nan"), "", t,
                                                    f"load limit without valid input ({reason}) — STOP"))
                log.warning("SAF-SW-001: moving with an enabled load limit and no valid input (%s) — STOP", reason)
        self._warn(cfg, f if load_on else float("nan"))
        if moving and row.flags & HOMED and self.trip is None:
            self._travel(cfg, inp, x, v, t)

    def _force(self, row: DataRow, inp: SafetyInputs) -> float:
        if not inp.load_valid or inp.k is None or row.status & AFE_STALE_BIT:
            return float("nan")
        if row.raw_state == RAW_SATURATED:
            return L.saturated_force(inp.k, 1 if row.raw > 0 else -1)
        if row.raw_state not in (RAW_OK, RAW_SETTLING):
            return float("nan")
        return row.f_n

    @staticmethod
    def _invalid_reason(row: DataRow, inp: SafetyInputs) -> str:
        if not inp.load_valid:
            return inp.load_reason or "no valid calibration / tare"
        if row.status & AFE_STALE_BIT:
            return "AFE stale"
        return "no AFE data"

    def _travel(self, cfg: LimitConfig, inp: SafetyInputs, x: float, v: float, t: int) -> None:
        lo = cfg.travel_min_mm if cfg.travel_min_enabled else None
        hi = cfg.travel_max_mm if cfg.travel_max_enabled else None
        if lo is None and hi is None:
            return
        tol = 1.0 / inp.spm if inp.spm > 0 else 0.001
        end = None if inp.end_um is None else inp.end_um / 1000.0
        planned = end is not None and (lo is None or end >= lo - 1e-9) and (hi is None or end <= hi + 1e-9)
        side = L.evaluate_travel_limit(x, lo, hi, tol)
        predicted = False
        if side is None and not planned:
            side = L.predicted_crossing(x, v, lo, hi)
            predicted = side is not None
        if side is None:
            return
        sgn = 1 if side == "TRAVEL_MAX" else -1
        if not (v * sgn > 0):            # only motion outward trips; moving back inside (limit edited) never does
            return
        thr = hi if side == "TRAVEL_MAX" else lo
        self._trip(SwTrip(side, 1 if side == "TRAVEL_MAX" else -1, x, float(thr), "mm", t,
                          f"travel {'max' if side == 'TRAVEL_MAX' else 'min'} {thr:.3f} mm: x = {x:.3f} mm"
                          + (" (predicted)" if predicted else "")))

    def _trip(self, trip: SwTrip) -> None:
        reason = f"SW_LIMIT:{trip.limit}"
        self._stop(reason)                     # (1) STOP first
        self._terminate(reason)                # (2) operations
        self.trip = trip                       # (3) latch
        self.trips += 1
        self._last_restop_us = trip.t_us
        self._row("SW_TRIP", trip.text)        # (4) record + publish
        self._publish("safety.trip", trip)
        log.warning("SAF-SW-001 trip: %s", trip.text)

    def _release(self, cfg: LimitConfig, f: float, x: float, moving: bool = False) -> None:
        t = self.trip
        if t is None:
            return
        if t.limit == "PULL":
            done = not cfg.pull_enabled or L.back_inside(f, cfg.pull_trip_n)
        elif t.limit == "PUSH":
            done = not cfg.push_enabled or L.back_inside(f, cfg.push_trip_n)
        elif t.limit == "TRAVEL_MAX":            # a travel latch (also a predicted one) holds until standstill
            done = not cfg.travel_max_enabled or cfg.travel_max_mm is None or (
                not moving and x <= cfg.travel_max_mm + 1e-9)
        else:
            done = not cfg.travel_min_enabled or cfg.travel_min_mm is None or (
                not moving and x >= cfg.travel_min_mm - 1e-9)
        if done:
            self.trip = None
            self._row("SW_TRIP_CLEARED", t.limit)
            self._publish("safety.trip", None)

    def _warn(self, cfg: LimitConfig, f: float) -> None:
        for code, trip, en in (("PULL_WARN", cfg.pull_trip_n, cfg.pull_enabled),
                               ("PUSH_WARN", cfg.push_trip_n, cfg.push_enabled)):
            was = code in self.warnings
            now = bool(en) and L.force_warning(f, trip, cfg.warn_pct, was)
            if now != was:
                (self.warnings.add if now else self.warnings.discard)(code)
                level = cfg.warn_pct / 100.0 * trip
                self._publish("safety.warning", SafetyWarning(code, now, None if math.isnan(f) else f, level,
                                                              f"{code}: F = {f:.1f} N (warning at {level:.1f} N)"
                                                              if now else f"{code} cleared"))


__all__ = ["ThresholdManager", "ThresholdTarget", "calibrated_target", "check_manual", "KEYS", "FW_LEVEL_MAX_N",
           "SafetySupervisor", "SafetyInputs"]
