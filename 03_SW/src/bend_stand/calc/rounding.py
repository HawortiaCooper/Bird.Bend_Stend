"""Rounding rule of the project: half away from zero, exactly as C99 ``round()`` on binary64 (SYS-003, ICD §0.1).

Implements: SYS-003
"""
from __future__ import annotations

import math


def round_half_away(x: float) -> int:
    """Round a binary64 value half away from zero (C99 ``round``), exact also near .5 (no ``x + 0.5`` error)."""
    if isinstance(x, int):
        return x
    if not math.isfinite(x):
        raise ValueError(f"cannot round {x!r}")
    ax = abs(x)
    f = math.floor(ax)
    r = f + 1 if ax - f >= 0.5 else f       # ax - f is exact in binary64
    return int(-r if x < 0 else r)
