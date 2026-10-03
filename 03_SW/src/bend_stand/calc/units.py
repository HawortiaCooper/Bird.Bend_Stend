"""Units and full-scale constants (SRS §2, SYS-003). Pure functions, no I/O.

GUI display units (N / kgf) use these helpers (SW_design §15.4 rule 8).

Implements: SYS-003, SW-RT-004 (kgf channel)
"""
from __future__ import annotations

from bend_stand.calc.rounding import round_half_away

G0 = 9.80665                       #: standard gravity, m/s² (N per kgf)
FS_KG = 200.0                      #: load cell full scale (D-19, Keli DEF 200 kg)
FS_N = FS_KG * G0                  #: 1961.33 N
FW_LIMIT_MAX_N = 1.10 * FS_N       #: 110 % FS = 2157.463 N (D-12, SW-LIM-002)
STROKE_MM = 300.0                  #: table stroke (D-19)


def n_to_kgf(f_n: float) -> float:
    return f_n / G0


def kgf_to_n(f_kgf: float) -> float:
    return f_kgf * G0


def mm_to_um(mm: float) -> int:
    """mm (SW unit) → µm (wire unit), round half away from zero (SYS-003)."""
    return round_half_away(mm * 1000.0)


def um_to_mm(um: int | float) -> float:
    return float(um) / 1000.0


def mm_s_to_um_s(v_mm_s: float) -> int:
    return round_half_away(v_mm_s * 1000.0)


def pct_fs_to_n(pct: float) -> float:
    return pct / 100.0 * FS_N
