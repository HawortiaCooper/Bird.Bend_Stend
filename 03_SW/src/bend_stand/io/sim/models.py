"""Simulator world models (SW_design §12.3, R4 §11): HX711 load-cell AFE, specimen, switches/buttons/driver
signals as plain world state. Deterministic (seeded RNG); time in µs of the board clock.

M1 subset: HX711 rate × (1 + ε), noise, offset (default 50 000 counts, SWD-P1-15), rails / saturate,
stall, dropped / missed conversions, verbatim raw script; linear-spring / bilinear specimen with break.
Switch bounce, broken wires, driver lag and relaxation are M2 (WP-B12).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/sim_sensors.py @37c87471 (idea of the ``_Afe`` rate /
saturation model; rewritten for the HX711 and the bend specimen).

Implements: SYS-008 (simulator models), FW-AFE-004 (rate error model)
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

from bend_stand.core import protocol_gen as pg

COUNTS_PER_N = 3285.0          # 3.0 mV/V, gain 128, 200 kg FS (SRS A-02)
DEFAULT_OFFSET = 50_000        # ≤ 1 % FS, the default session does not clamp (SWD-P1-15)


@dataclass
class Specimen:
    kind: str = "none"                 # none | spring | bilinear
    k_n_per_mm: float = 50.0
    x_contact_um: int = 120_000
    k2_n_per_mm: float | None = None
    f_yield_n: float | None = None
    f_break_n: float | None = None
    relax_pct: float = 0.0
    relax_tau_s: float = 30.0
    broken: bool = False

    def force_n(self, x_um: float) -> float:
        if self.kind == "none" or self.broken:
            return 0.0
        dx_mm = (x_um - self.x_contact_um) / 1000.0
        if dx_mm <= 0:
            return 0.0
        f = self.k_n_per_mm * dx_mm
        if self.kind == "bilinear" and self.f_yield_n is not None and self.k2_n_per_mm is not None \
                and f > self.f_yield_n:
            x_y = self.f_yield_n / self.k_n_per_mm
            f = self.f_yield_n + self.k2_n_per_mm * (dx_mm - x_y)
        if self.f_break_n is not None and f >= self.f_break_n:
            self.broken = True
            return 0.0
        return f


@dataclass
class Hx711Model:
    """Data-ready at ``rate_sps · (1 + rate_error)``; ``raw = offset + S·F + noise`` (rails clip)."""

    rate_sps: float = 80.0
    rate_error: float = 0.005
    noise_counts: float = 45.0
    offset_counts: int = DEFAULT_OFFSET
    counts_per_n: float = COUNTS_PER_N
    stall: bool = False
    saturate: str | None = None        # "pos" | "neg" | None
    drop_every: int = 0                # every n-th conversion missing (0 = off)
    miss_next: int = 0                 # single missed DOUT edges
    seed: int = 1
    raw_script: deque[int] = field(default_factory=deque)
    next_drdy_us: int = 0
    conversions: int = 0
    _rng: random.Random = field(default_factory=random.Random, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    @property
    def period_us(self) -> float:
        return 1e6 / (self.rate_sps * (1.0 + self.rate_error))

    def schedule_from(self, t_us: int) -> None:
        self.next_drdy_us = t_us + int(round(self.period_us))

    def due(self, t_us: int) -> bool:
        return not self.stall and t_us >= self.next_drdy_us

    def convert(self, force_n: float) -> int | None:
        """Produce the conversion at ``next_drdy_us`` (advances the schedule); ``None`` = missed edge."""
        self.next_drdy_us += int(round(self.period_us))
        self.conversions += 1
        if self.miss_next > 0:
            self.miss_next -= 1
            return None
        if self.drop_every and self.conversions % self.drop_every == 0:
            return None
        if self.raw_script:
            return max(pg.RAW_MIN, min(pg.RAW_MAX, int(self.raw_script.popleft())))
        if self.saturate == "pos":
            return pg.RAW_MAX
        if self.saturate == "neg":
            return pg.RAW_MIN
        raw = self.offset_counts + self.counts_per_n * force_n + self._rng.gauss(0.0, self.noise_counts)
        return max(pg.RAW_MIN, min(pg.RAW_MAX, int(round(raw))))


@dataclass
class World:
    """Physical world around the board: inputs (active = after polarity), driver supply, true position."""

    stroke_um: int = 300_000
    start_switch_um: int = -1_500
    end_switch_um: int = 301_000
    estop_open: bool = False
    drv_power: bool = True
    limit_start_forced: bool | None = None
    limit_end_forced: bool | None = None
    stop_btn: bool = False
    pause_btn: bool = False
    alm: bool = False
    pend: bool = True
    broken: set[str] = field(default_factory=set)
    x_um_true_offset: int = 0          # world_shift (lost steps)
    specimen: Specimen = field(default_factory=Specimen)

    def limit_start(self, x_um: float) -> bool:
        if "start" in self.broken:
            return True
        if self.limit_start_forced is not None:
            return self.limit_start_forced
        return x_um + self.x_um_true_offset <= self.start_switch_um

    def limit_end(self, x_um: float) -> bool:
        if "end" in self.broken:
            return True
        if self.limit_end_forced is not None:
            return self.limit_end_forced
        return x_um + self.x_um_true_offset >= self.end_switch_um

    def estop_input_open(self) -> bool:
        return self.estop_open or "estop" in self.broken

    def power_present(self) -> bool:
        return self.drv_power and "drv_power" not in self.broken
