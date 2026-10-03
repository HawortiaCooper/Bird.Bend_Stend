"""Display formatting only (SW_design_GUI §8): decimals per unit, thousands separators, "n/a".

Implements: SW-RT-005 (readout texts)
"""
from __future__ import annotations

import math
from typing import Any

NA = "n/a"

DECIMALS = {"N": 2, "kgf": 3, "mm": 3, "um": 0, "µm": 0, "counts": 0, "cnt": 0, "SPS": 1, "Hz": 1, "s": 3,
            "mm/s": 3, "N/s": 1}


def is_missing(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def fmt_value(v: Any, unit: str = "", decimals: int | None = None) -> str:
    """Number with the unit's decimals and thin-space thousands separators; "n/a" for None / NaN."""
    if is_missing(v):
        return NA
    if isinstance(v, bool):
        return "1" if v else "0"
    d = DECIMALS.get(unit, 3) if decimals is None else decimals
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if d == 0:
        s = f"{int(round(x)):,}"
    else:
        s = f"{x:,.{d}f}"
    return s.replace(",", " ")


def fmt_hex(v: int | None, width: int = 8) -> str:
    return NA if v is None else f"0x{int(v):0{width}X}"


def fmt_version(v: Any) -> str:
    if v is None:
        return NA
    if isinstance(v, (tuple, list)):
        return ".".join(str(int(x)) for x in v)
    return str(v)


def fmt_age_s(age_s: float | None) -> str:
    if is_missing(age_s):
        return NA
    a = float(age_s)  # type: ignore[arg-type]
    if a < 60:
        return f"{a:.0f} s"
    if a < 3600:
        return f"{a / 60:.0f} min"
    return f"{a / 3600:.1f} h"
