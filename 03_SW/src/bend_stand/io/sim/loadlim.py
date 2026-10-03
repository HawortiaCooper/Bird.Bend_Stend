"""FW load limit of the simulator (ICD v0.6 §5.5 "FW load limit", D-12, D-40 d) — pure model, replayed against
``00_System/tools/vectors/loadlim_vectors.json`` (written from the ICD text, not copied from ``ref_loadlim.py``).

* Every HX711 sample is a violation when ``raw > load_raw_max``, ``raw < load_raw_min`` or at a rail;
  ``load_trip_samples`` consecutive violations trip; a sample inside resets the count.
* FAULT_CLEAR: reference = the last sample; if it violates, the **regrow window** opens: a violating sample
  re-trips at once only when it lies more than ``load_regrow_raw`` beyond the reference on its side, every other
  violating sample is ignored (unloading); the window ends with the first sample inside the thresholds or the next
  FAULT_CLEAR; a threshold change does not end it.
* Threshold changes act from the next sample and keep the count.

Implements: SAF-FW-008, SAF-FW-009, SAF-FW-010 (next-sample effect), SAF-FW-011 (sim)
"""
from __future__ import annotations

from dataclasses import dataclass

RAIL_LO, RAIL_HI = -8_388_608, 8_388_607


@dataclass
class LoadLimit:
    lo: int = -7_022_271
    hi: int = 7_022_271
    trip_samples: int = 1
    regrow: int = 128_849
    count: int = 0
    window: bool = False
    ref: int = 0
    last: int | None = None

    def config(self, lo: int, hi: int, trip_samples: int, regrow: int) -> None:
        self.lo, self.hi = int(lo), int(hi)
        self.trip_samples, self.regrow = max(1, int(trip_samples)), max(0, int(regrow))

    def violation(self, raw: int) -> bool:
        return raw in (RAIL_LO, RAIL_HI) or raw > self.hi or raw < self.lo

    def sample(self, raw: int) -> bool:
        """Evaluate one sample; True = trip (immediate stop + LOAD_LIMIT)."""
        self.last = int(raw)
        if not self.violation(raw):
            self.count = 0
            self.window = False
            return False
        if self.window:
            beyond = (raw > self.hi and raw > self.ref + self.regrow) or (raw < self.lo and raw < self.ref - self.regrow)
            if beyond:
                self.window = False
                self.count = self.trip_samples
                return True
            self.count = 0
            return False
        self.count += 1
        return self.count >= self.trip_samples

    def fault_clear(self) -> None:
        self.count = 0
        if self.last is None:
            self.window = False
            return
        self.ref = self.last
        self.window = self.violation(self.last)
