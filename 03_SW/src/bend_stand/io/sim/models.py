"""Simulator world models (SW_design §12.3, R4 §11): HX711 load-cell AFE, specimen, switches/buttons/driver
signals as plain world state. Deterministic (seeded RNG); time in µs of the board clock.

HX711 rate × (1 + ε), noise (R2 §4.4: 45 counts rms at 80 SPS, ≈ 25 counts at 10 SPS), offset (default 50 000
counts, SWD-P1-15) with an optional drift (counts/min, R4 §11), gain scaling by ``afe.gain_channel`` (A128 / A64 /
B32), rails / saturate, stall, dropped / missed conversions, verbatim raw script; linear-spring / bilinear specimen
with break and relaxation (M2); **M3 calibration load model**: weights hanging on the cell (``World.cell_load_n``,
+ = tension) with a decaying swing after they are put on (test-only ``SimControl.set_cell_load``);
world inputs: E-stop sense (NC), limit switches by position (world coordinates) or forced, PAUSE button with
its contact type (NO default), ALM / PEND (logical driver outputs), driver supply (optional 48 V presence sense, D-41: no contactor), the hardwired ENA cut of the
E-stop (D-42), broken wires; scheduled
contact bounce is applied by ``SimControl`` (M2, WP-B12). D-36 / CR-01 (ICD v0.5): there is no STOP/BREAK
button input any more.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/sim_sensors.py @37c87471 (idea of the ``_Afe`` rate /
saturation model; rewritten for the HX711 and the bend specimen).

Implements: SYS-008 (simulator models), FW-AFE-004 (rate error model), SW-CAL-005 (calibration load model)
"""
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field

from bend_stand.calc.rounding import round_half_away
from bend_stand.core import protocol_gen as pg

COUNTS_PER_N = 3285.0          # 3.0 mV/V, gain 128, 200 kg FS (SRS A-02)
DEFAULT_OFFSET = 50_000        # ≤ 1 % FS, the default session does not clamp (SWD-P1-15)
NOISE_10SPS_FACTOR = 25.0 / 45.0   # R2 §4.4: 0.78 g rms at 10 SPS vs 1.40 g rms at 80 SPS
#: ``afe.gain_channel`` code → sensitivity relative to channel A gain 128 (params dict 5: A128 0, B32 1, A64 2)
GAIN_SCALE = {0: 1.0, 2: 0.5, 1: 0.25}


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
    _relax_x: float | None = None          # position at which the current relaxation started
    _relax_t_us: int = 0

    def force_n(self, x_um: float, t_us: int | None = None) -> float:
        """Force at the world position ``x_um`` (µm); with ``t_us`` the relaxation (``relax_pct`` with time
        constant ``relax_tau_s`` at constant position) is applied."""
        if self.kind == "none" or self.broken:
            return 0.0
        dx_mm = (x_um - self.x_contact_um) / 1000.0
        if dx_mm <= 0:
            return 0.0
        f = self.k_n_per_mm * dx_mm
        if t_us is not None and self.relax_pct > 0:
            if self._relax_x is None or abs(x_um - self._relax_x) > 0.5:
                self._relax_x, self._relax_t_us = x_um, t_us
            dt = max(0, t_us - self._relax_t_us) / 1e6
            f *= 1.0 - self.relax_pct / 100.0 * (1.0 - math.exp(-dt / max(1e-3, self.relax_tau_s)))
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
    drift_counts_per_min: float = 0.0  # offset drift (temperature / creep, R4 §11)
    drift_t0_us: int | None = None
    creep_pct: float = 0.0             # v0.7.2 cell model (tools/README): creep toward creep_pct·(cpn·F), tau
    creep_tau_s: float = 1800.0
    nonlin_pct_fs: float = 0.0         # odd non-linearity nonlin_pct_fs·FS·4u(1−u), u = |F|/fs_n
    fs_n: float = 1961.33
    creep_counts: float = 0.0
    _creep_t_us: int | None = None
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

    def set_drift(self, counts_per_min: float, t_us: int) -> None:
        """Start an offset drift of ``counts_per_min`` from ``t_us`` (the offset reached so far is kept)."""
        self.offset_counts = int(round(self.offset_at(t_us)))
        self.drift_counts_per_min, self.drift_t0_us = float(counts_per_min), int(t_us)

    def offset_at(self, t_us: int) -> float:
        if not self.drift_counts_per_min or self.drift_t0_us is None:
            return float(self.offset_counts)
        return self.offset_counts + self.drift_counts_per_min * (t_us - self.drift_t0_us) / 60e6

    def convert(self, force_n: float, gain_scale: float = 1.0) -> int | None:
        """Produce the conversion at ``next_drdy_us`` (advances the schedule); ``None`` = missed edge.
        ``raw = gain·(offset(t) + S·F) + noise`` (noise per R2 for the configured rate), clipped to the rails."""
        t_conv = self.next_drdy_us
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
        noise = self.noise_counts * (NOISE_10SPS_FACTOR if self.rate_sps <= 10.0 else 1.0)
        ideal = self.counts_per_n * force_n
        if self.nonlin_pct_fs and self.fs_n > 0 and math.isfinite(force_n):
            u = min(1.0, abs(force_n) / self.fs_n)
            ideal += math.copysign(self.nonlin_pct_fs / 100.0 * self.fs_n * 4.0 * u * (1.0 - u), force_n) *                 self.counts_per_n
        if self.creep_pct:
            target = self.creep_pct / 100.0 * self.counts_per_n * force_n
            if self._creep_t_us is not None and math.isfinite(target):
                dt = max(0, t_conv - self._creep_t_us) / 1e6
                self.creep_counts += (target - self.creep_counts) * (1.0 - math.exp(-dt / max(1e-3, self.creep_tau_s)))
            self._creep_t_us = t_conv
            ideal += self.creep_counts
        raw = gain_scale * (self.offset_at(t_conv) + ideal) + self._rng.gauss(0.0, noise)
        if not math.isfinite(raw):
            return pg.RAW_MAX if raw > 0 else pg.RAW_MIN
        return max(pg.RAW_MIN, min(pg.RAW_MAX, round_half_away(raw)))


@dataclass
class World:
    """Physical world around the board: inputs (logical driver outputs, contacts), driver supply, the carriage.

    Positions are **world** coordinates (µm): ``x_true = machine position + offset + x_um_true_offset`` is
    computed by the board; the switches and the specimen live in world coordinates."""

    stroke_um: int = 300_000
    steps_per_mm: float = 800.0        # mechanics: world travel per pulse = 1000 / this (SWC-M3-03)
    start_switch_um: int = -1_500
    end_switch_um: int = 301_000
    estop_open: bool = False
    drv_power: bool = True
    limit_start_forced: bool | None = None
    limit_end_forced: bool | None = None
    pause_btn: bool = False            # pressed
    pause_contact: str = "NO"          # wiring of the PAUSE button: "NO" (default, R5 §5.5) | "NC"
    alm: bool = False
    pend: bool = True
    broken: set[str] = field(default_factory=set)
    x_um_true_offset: int = 0          # world_shift (lost steps)
    ena_hardwired_cut: bool = True     # D-42: the E-stop's NO contact forces the driver ENA to disabled
    specimen: Specimen = field(default_factory=Specimen)
    # M3 calibration load model: weights hanging on the cell (+ = tension), with a decaying swing after placing
    cell_load_n: float = 0.0
    swing_n: float = 0.0
    swing_tau_s: float = 1.0
    swing_hz: float = 1.5
    swing_t0_us: int = 0

    def cell_force_n(self, t_us: int | None) -> float:
        """Force of the hanging weights incl. the decaying swing (R4 §6.1: 'swinging weight')."""
        f = self.cell_load_n
        if self.swing_n and t_us is not None and t_us >= self.swing_t0_us:
            dt = (t_us - self.swing_t0_us) / 1e6
            f += self.swing_n * math.exp(-dt / max(1e-3, self.swing_tau_s)) * math.cos(2 * math.pi * self.swing_hz * dt)
        return f

    def limit_start(self, x_true_um: float) -> bool:
        if "start" in self.broken:
            return True
        if self.limit_start_forced is not None:
            return self.limit_start_forced
        return x_true_um <= self.start_switch_um

    def limit_end(self, x_true_um: float) -> bool:
        if "end" in self.broken:
            return True
        if self.limit_end_forced is not None:
            return self.limit_end_forced
        return x_true_um >= self.end_switch_um

    def estop_input_open(self) -> bool:
        return self.estop_open or "estop" in self.broken

    def power_present(self) -> bool:
        return self.drv_power and "drv_power" not in self.broken

    def pause_closed(self) -> bool:
        """Electrical state of the PAUSE contact (a broken wire reads open)."""
        if "pause" in self.broken:
            return False
        return self.pause_btn if self.pause_contact == "NO" else not self.pause_btn

    def alm_active(self) -> bool:
        return self.alm or "alm" in self.broken          # broken ALM wire reads "alarm" (fail-safe, A-09)

    def pend_active(self) -> bool:
        return self.pend and "pend" not in self.broken
