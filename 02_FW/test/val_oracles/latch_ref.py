#!/usr/bin/env python3
"""Validator E - independent latch / filter / load-limit oracles for the M2 safety suites.

Sources: SRS v0.5.1 SAF-FW-006/008/009/011/012/013/014/024/025, FW-SW-001/005, §6; ICD v0.5 §5.5, §6.2;
D-33 (f)(h). Pure functions on millisecond / sample sequences - no FW code, no twin.

Assumptions marked ASSUMED are where SRS/ICD leave a detail open; each is listed in FW_test_plan v0.3
§8.3 (OI-E-M2-xx) for Implementer A / the Integrator to confirm before the M2 run.

Verifies (oracle for): SAF-FW-006, SAF-FW-008, SAF-FW-009, SAF-FW-011, SAF-FW-013, SAF-FW-024,
SAF-FW-025, FW-SW-001, FW-SW-005
"""
from __future__ import annotations

from dataclasses import dataclass, field

RAIL_HI, RAIL_LO = 0x7FFFFF, -0x800000
DRV_PWR_FILTER_MS = 20          # FW-SW-005 (stability filter, both directions)
TICK_MS = 1                     # 1 kHz sampling of polled inputs
DRV_PWR_REACTION_MS = 25        # SAF-FW-024 budget (input change -> stop/ENA/NOT_ENABLED)
K1_EXTRA_MS = 25                # SAF-FW-025 upper bound k1_weld_ms + 25 ms (D-33 f)


# ------------------------------------------------------------------ E-stop clear (SAF-FW-006)
def estop_clear(open_now: bool, closed_for_ms: float, estop_release_ms: int) -> tuple[str, int | None]:
    """ICD §5.5: input open -> E_CAUSE_ACTIVE 0xFFFF; closed < release -> E_CAUSE_ACTIVE (ms missing);
    else OK. The missing-ms value may differ by the 1 ms tick (tests accept ±1)."""
    if open_now:
        return "E_CAUSE_ACTIVE", 0xFFFF
    if closed_for_ms < estop_release_ms:
        return "E_CAUSE_ACTIVE", int(estop_release_ms - closed_for_ms)
    return "OK", None


# ------------------------------------------------------- limit latch auto-clear (SAF-FW-013, D-33 h)
def limit_latch_cleared(released_stable_ms: float, release_ms: int) -> bool | None:
    """Released continuously >= io.release_ms -> cleared (no position condition since D-33 h).
    Returns None inside the 1 ms sampling uncertainty band."""
    if released_stable_ms >= release_ms + TICK_MS:
        return True
    if released_stable_ms < release_ms:
        return False
    return None


def limit_motion_allowed(latched: str | None, active: set[str], direction: int) -> bool:
    """While a limit is active or latched only motion away from it is accepted (SAF-FW-013).
    START is at -x, END at +x; direction = sign of the requested motion."""
    blocked = set(active) | ({latched} if latched else set())
    if direction < 0 and "START" in blocked:
        return False
    if direction > 0 and "END" in blocked:
        return False
    return True


# --------------------------------------------------------- DRV_PWR filter (FW-SW-005, SAF-FW-024)
def drv_pwr_filtered(levels_ms: list[int], initial: int) -> list[int]:
    """1 kHz samples of the raw 'powered' level (1 = powered) -> filtered level per ms.
    A change is accepted after it has been stable for DRV_PWR_FILTER_MS samples."""
    out, cur, cand, run = [], initial, initial, 0
    for lv in levels_ms:
        if lv == cur:
            cand, run = cur, 0
        elif lv == cand:
            run += 1
        else:
            cand, run = lv, 1
        if cand != cur and run >= DRV_PWR_FILTER_MS:
            cur, run = cand, 0
        out.append(cur)
    return out


def toggle_ignored(duration_ms: float) -> bool | None:
    """A DRV_PWR toggle shorter than the filter is ignored; within ±1 tick: either."""
    if duration_ms <= DRV_PWR_FILTER_MS - TICK_MS:
        return True
    if duration_ms >= DRV_PWR_FILTER_MS + TICK_MS:
        return False
    return None


# ------------------------------------------------------------------- K1 weld (SAF-FW-025)
def k1_weld_window(k1_weld_ms: int) -> tuple[float, float]:
    """Fault latched in (k1, k1 + 25] ms after the E-stop sense edge while power stays present."""
    return float(k1_weld_ms), float(k1_weld_ms + K1_EXTRA_MS)


def k1_cause_present(estop_open: bool, drv_pwr_present: bool, sense_enabled: bool) -> bool:
    return sense_enabled and estop_open and drv_pwr_present


# ------------------------------------------------------------ load limit (SAF-FW-008/009/011)
@dataclass
class LoadLimit:
    """SAF-FW-008/009/011 per ICD v0.6 §5.5 "FW load limit" (normative since D-40 d), written from the text:
    violation = raw > max, raw < min or a rail sample; trip after trip_samples consecutive violations; FAULT_CLEAR
    takes the LAST sample as reference and opens the regrow window if that sample violates; inside the window a
    violating sample re-trips at once only if it is more than regrow beyond the reference on its side, other
    violations are ignored (unloading); the window ends with the first in-range sample or the next FAULT_CLEAR;
    a threshold change keeps the count and the window. ICD v0.7 (OI-B-M2-03): only a FAULT_CLEAR that actually clears
    a latched LOAD_LIMIT takes a new reference; with LOAD_LIMIT not latched the load-limit state is unchanged."""
    raw_min: int = -7022271
    raw_max: int = 7022271
    trip_samples: int = 1
    regrow_raw: int = 128849
    _run: int = 0
    _window: bool = False
    _ref: int = 0
    _last: int = 0
    latched: bool = False
    tripped: bool = False
    trips: list[int] = field(default_factory=list)

    def violates(self, raw: int) -> bool:
        return raw >= RAIL_HI or raw <= RAIL_LO or raw > self.raw_max or raw < self.raw_min

    @property
    def window(self) -> bool:
        return self._window

    def config(self, raw_min: int, raw_max: int, trip_samples: int, regrow_raw: int) -> None:
        self.raw_min, self.raw_max, self.trip_samples, self.regrow_raw = raw_min, raw_max, trip_samples, regrow_raw

    def sample(self, raw: int) -> bool:
        """One sample; True = this sample trips (immediate stop + LOAD_LIMIT)."""
        self._last = raw
        if not self.violates(raw):
            self._run, self._window = 0, False
            return False
        if self._window:
            grew = (raw > self.raw_max and raw > self._ref + self.regrow_raw) or                    (raw < self.raw_min and raw < self._ref - self.regrow_raw)
            if not grew:
                self._run = 0
                return False
            self._window = False
            self._run = self.trip_samples
            self.latched = True
            return True
        self._run += 1
        if self._run >= self.trip_samples:
            self.latched = True
            return True
        return False

    def feed(self, i: int, raw: int) -> bool:
        """Like sample(), but reports only the first trip of a latched episode (records its index)."""
        t = self.sample(raw)
        if t and not self.tripped:
            self.tripped = True
            self.trips.append(i)
            return True
        return False

    def fault_clear(self, raw_at_clear: int | None = None) -> None:
        """Reference = the last sample (or raw_at_clear when given); window opens if it violates."""
        if raw_at_clear is not None:
            self._last = raw_at_clear
        if not self.latched:                     # ICD v0.7: nothing latched -> state unchanged
            return
        self.latched = False
        self.tripped, self._run = False, 0
        self._ref = self._last
        self._window = self.violates(self._last)


def first_trip(samples: list[int], **kw) -> int | None:
    ll = LoadLimit(**kw)
    for i, r in enumerate(samples):
        if ll.feed(i, r):
            return i
    return None


def run_loadlim_case(case: dict) -> list[tuple[bool | None, bool]]:
    """Replay one loadlim_vectors.json case -> [(trip, window_after)] per step (trip None for non-samples)."""
    ini = case["init"]
    ll = LoadLimit(ini["load_raw_min"], ini["load_raw_max"], ini["trip_samples"], ini["regrow"])
    out = []
    for st in case["steps"]:
        if st["op"] == "sample":
            out.append((ll.sample(st["raw"]), ll.window))
        elif st["op"] == "fault_clear":
            ll.fault_clear()
            out.append((None, ll.window))
        elif st["op"] == "config":
            ll.config(st["load_raw_min"], st["load_raw_max"], st["trip_samples"], st["regrow"])
            out.append((None, ll.window))
        else:
            raise ValueError(st["op"])
    return out


# -------------------------------------------------------------- stale AFE (SAF-FW-012)
def stale_stop_window(last_sample_ms: float, timeout_ms: int) -> tuple[float, float]:
    """Moving: immediate stop + AFE_FAULT at last sample + timeout, at most +1 ms (tick)."""
    return last_sample_ms + timeout_ms, last_sample_ms + timeout_ms + TICK_MS


def timeout_rule_ok(timeout_ms: int, rate_sps: int) -> bool:
    """Hard rule H5 (SRS SAF-FW-012): afe.timeout_ms >= 2 x conversion period."""
    return timeout_ms >= 2 * (1000.0 / rate_sps)
