#!/usr/bin/env python3
"""Reference FW load limit (test oracle) for loadlim_vectors.json (ICD §5.5 / §6.2, D-12, D-40 d).

Implements: SAF-FW-008, SAF-FW-009, SAF-FW-010 (next-sample effect), SAF-FW-011 (regrow window)

Normative behaviour (ICD v0.6 §5.5 "FW load limit"):
- Every HX711 sample is a *violation* when raw > safety.load_raw_max, raw < safety.load_raw_min, or the sample
  is at a rail (0x7FFFFF / −8 388 608; SAF-FW-009). safety.load_trip_samples consecutive violations trip
  (immediate stop, LOAD_LIMIT latched); a sample inside the thresholds resets the count.
- FAULT_CLEAR of LOAD_LIMIT is always accepted. The *reference* is the last sample before the clear (its raw
  value). If that sample is a violation, the **regrow window** opens:
  - a violating sample re-trips **immediately** (no trip_samples count) only if it lies more than
    safety.load_regrow_raw beyond the reference on its side (raw > max and raw > ref + regrow, or raw < min and
    raw < ref − regrow); any other violating sample is ignored (unloading allowed) and does not count;
  - the window **ends** with the first sample inside the thresholds (normal rule from the next sample) or with
    the next FAULT_CLEAR (new reference); a threshold change (SET_PARAM) does not end it.
- Threshold changes act from the next sample and keep the consecutive count.
"""
from __future__ import annotations

from dataclasses import dataclass

RAW_MIN, RAW_MAX = -8_388_608, 8_388_607


@dataclass
class LoadLim:
    lo: int = -7_022_271
    hi: int = 7_022_271
    trip_samples: int = 1
    regrow: int = 128_849
    count: int = 0
    regrow_on: bool = False
    ref: int = 0
    last: int = 0
    last_sat: bool = False

    def config(self, lo: int, hi: int, trip_samples: int, regrow: int) -> None:
        self.lo, self.hi, self.trip_samples, self.regrow = lo, hi, max(1, trip_samples), max(0, regrow)

    def violates(self, raw: int, sat: bool) -> bool:
        return sat or raw > self.hi or raw < self.lo

    def sample(self, raw: int) -> bool:
        """One sample; True = trip."""
        sat = raw in (RAW_MIN, RAW_MAX)
        self.last, self.last_sat = raw, sat
        if not self.violates(raw, sat):
            self.count = 0
            self.regrow_on = False
            return False
        if self.regrow_on:
            grow = (raw > self.hi and raw > self.ref + self.regrow) or (raw < self.lo and raw < self.ref - self.regrow)
            if not grow:
                self.count = 0
                return False
            self.regrow_on = False
            self.count = self.trip_samples
            return True
        self.count += 1
        return self.count >= self.trip_samples

    def fault_clear(self) -> None:
        self.count = 0
        self.ref = self.last
        self.regrow_on = self.violates(self.last, self.last_sat)
