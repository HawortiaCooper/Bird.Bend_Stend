"""Device time base and loss detection (R4 §9, SW_design §7.3). Pure functions (stateless); the pipeline
keeps the state (previous raw/unwrapped time, previous frame_seq).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/samples.py @37c87471 (TimeUnwrapper / GapCounter logic,
adapted: pure functions, epoch on a backward step, u16 frame_seq rule of SWD-P1-12 b).

Implements: IF-006, IF-007, SW-ACQ-004 (loss counting)
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

WRAP = 1 << 32
HALF = 1 << 31


@dataclass(frozen=True)
class UnwrapStep:
    t_u: int            # unwrapped µs (within the epoch)
    new_epoch: bool     # backward step (board reset without BOOT seen): the caller starts a new epoch


def unwrap_step(prev_raw: int | None, prev_u: int | None, t_raw: int) -> UnwrapStep:
    """One unwrap step: a forward modular difference < 2³¹ continues (incl. a wrap, i.e. ``t < t_prev`` with a
    backward step > 2³¹ → +2³²); a smaller backward step starts a new epoch (``t_u = t_raw``)."""
    t_raw &= 0xFFFFFFFF
    if prev_raw is None or prev_u is None:
        return UnwrapStep(t_raw, False)
    d = (t_raw - prev_raw) & 0xFFFFFFFF
    if d < HALF:
        return UnwrapStep(prev_u + d, False)
    return UnwrapStep(t_raw, True)


def unwrap_us(ts: Iterable[int], prev_raw: int | None = None, prev_u: int | None = None) -> list[int]:
    """Unwrap a sequence of 32-bit µs stamps (R4 TV-L ``unwrap_us``); backward steps restart at the raw value."""
    out: list[int] = []
    for t in ts:
        s = unwrap_step(prev_raw, prev_u, t)
        out.append(s.t_u)
        prev_raw, prev_u = t & 0xFFFFFFFF, s.t_u
    return out


def plausible_wraps(host_elapsed_us: float, d_mod_us: int) -> int:
    """Whole 2³² µs wraps to add after a long host gap (≥ 60 s) so that the device elapsed time
    ``d_mod + n·2³²`` best matches the host receive stamps (TS SWD-M3R1-02)."""
    n = round((float(host_elapsed_us) - float(d_mod_us)) / WRAP)
    return max(0, int(n))


SeqKind = Literal["first", "next", "lost", "dup", "anomaly"]


@dataclass(frozen=True)
class SeqStep:
    kind: SeqKind
    lost: int = 0


def frame_gap(prev_seq: int | None, seq: int) -> SeqStep:
    """u16 ``frame_seq`` rule (SWD-P1-12 b): ``d = (seq − prev) & 0xFFFF``; ``d = 0`` duplicate;
    ``1 ≤ d < 0x8000`` → ``lost = d − 1``; ``d ≥ 0x8000`` → anomaly (re-base, no loss counted)."""
    seq &= 0xFFFF
    if prev_seq is None:
        return SeqStep("first", 0)
    d = (seq - prev_seq) & 0xFFFF
    if d == 0:
        return SeqStep("dup", 0)
    if d < 0x8000:
        return SeqStep("next" if d == 1 else "lost", d - 1)
    return SeqStep("anomaly", 0)


def frame_gaps(seqs: Sequence[int], prev_seq: int | None = None) -> list[SeqStep]:
    out = []
    for s in seqs:
        st = frame_gap(prev_seq, s)
        out.append(st)
        if st.kind != "dup":
            prev_seq = s & 0xFFFF
    return out


def missed_conversions(dt_us: float, median_period_us: float) -> int:
    """Missed HX711 conversions between two frames without a seq gap: Δt > 1.5 × median period (R4 §9)."""
    if median_period_us <= 0 or dt_us <= 1.5 * median_period_us:
        return 0
    return max(1, round(dt_us / median_period_us) - 1)
