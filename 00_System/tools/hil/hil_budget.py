"""HIL budget evaluation with measurement uncertainty (FW_test_plan v0.4.1 §6.1 rule 4) — pure functions.

Owner: Validator E (00_System/tools/hil, Integrator reviews). No I/O, no hardware access.

Decision rule (§6.1 rule 4) for an upper budget B and uncertainty u:
    PASS          max(measured) + u <= B
    FAIL          max(measured) - u >  B
    INCONCLUSIVE  otherwise  -> repeat in a finer mode or decide under §6.6
and mirrored for a lower limit L (min(measured) - u >= L PASS, min + u < L FAIL).

Other outcomes an HG check can have (they never count as PASS):
    NOT MEASURED  the step ran but the quantity is not available (e.g. DWT in the twin)
    MANUAL        needs an operator / instrument reading (in a twin dry run: scripted default, not evidence)
    TARGET-ONLY   the measurement chain exists only on the board (e.g. J-AUX stamps in the twin)
    N/A           not applicable in this configuration (e.g. HG-21 without a power sense, D-41)

Verifies: (harness for) SYS-009, NFR-005/006/007, SAF-FW-002/004/005/018/019, FW-MOT-001/008, FW-AFE-001/004
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

PASS, FAIL, INCONCL = "PASS", "FAIL", "INCONCLUSIVE"
NOT_MEASURED, MANUAL, TARGET_ONLY, NA, INFO = "NOT MEASURED", "MANUAL", "TARGET-ONLY", "N/A", "INFO"
OPEN_KINDS = (NOT_MEASURED, MANUAL, TARGET_ONLY)

F_CPU = 180_000_000          # core / DWT clock (FW-PLT-002)
F_PROBE = 180_000_000        # TIM8 input clock (ICD App. C INFO w1)


@dataclass
class Check:
    name: str                  # short id, e.g. "a last PUL after E-stop edge"
    criterion: str             # human-readable criterion incl. budget
    decision: str
    value: Any = None          # deciding value (max / min / count / bool / text)
    unit: str = ""
    u: float | None = None     # uncertainty added per §6.1 rule 4 (same unit)
    budget: Any = None
    n: int | None = None       # number of trials / samples behind the value
    note: str = ""
    stats: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------------------------------ statistics
def stats(values: Iterable[float]) -> dict:
    v = [float(x) for x in values]
    if not v:
        return {"n": 0}
    s = sorted(v)
    p95 = s[min(len(s) - 1, max(0, math.ceil(0.95 * len(s)) - 1))]
    return {"n": len(v), "min": s[0], "max": s[-1], "mean": statistics.fmean(v), "median": statistics.median(s),
            "p95": p95, "std": statistics.pstdev(v) if len(v) > 1 else 0.0}


def _fmt(x: Any) -> str:
    if isinstance(x, float):
        return f"{x:.4g}"
    return str(x)


# ------------------------------------------------------------------------------------------ decisions
def decide_le(value: float, budget: float, u: float) -> str:
    """Upper budget: §6.1 rule 4."""
    if value + u <= budget:
        return PASS
    if value - u > budget:
        return FAIL
    return INCONCL


def decide_ge(value: float, limit: float, u: float) -> str:
    """Lower limit: mirror of §6.1 rule 4."""
    if value - u >= limit:
        return PASS
    if value + u < limit:
        return FAIL
    return INCONCL


def check_le(name: str, values: Iterable[float], budget: float, u: float, unit: str, what: str = "max",
             note: str = "") -> Check:
    v = [float(x) for x in values]
    if not v:
        return Check(name, f"{what} <= {_fmt(budget)} {unit} (u {_fmt(u)})", NOT_MEASURED, None, unit, u, budget, 0,
                     note or "no samples")
    m = max(v)
    return Check(name, f"{what} + u <= {_fmt(budget)} {unit}", decide_le(m, budget, u), m, unit, u, budget, len(v),
                 note, stats(v))


def check_ge(name: str, values: Iterable[float], limit: float, u: float, unit: str, what: str = "min",
             note: str = "") -> Check:
    v = [float(x) for x in values]
    if not v:
        return Check(name, f"{what} >= {_fmt(limit)} {unit} (u {_fmt(u)})", NOT_MEASURED, None, unit, u, limit, 0,
                     note or "no samples")
    m = min(v)
    return Check(name, f"{what} - u >= {_fmt(limit)} {unit}", decide_ge(m, limit, u), m, unit, u, limit, len(v),
                 note, stats(v))


def check_range(name: str, values: Iterable[float], lo: float, hi: float, u: float, unit: str,
                note: str = "") -> Check:
    v = [float(x) for x in values]
    if not v:
        return Check(name, f"{_fmt(lo)}…{_fmt(hi)} {unit}", NOT_MEASURED, None, unit, u, (lo, hi), 0, note or "no samples")
    d_lo, d_hi = decide_ge(min(v), lo, u), decide_le(max(v), hi, u)
    dec = FAIL if FAIL in (d_lo, d_hi) else INCONCL if INCONCL in (d_lo, d_hi) else PASS
    return Check(name, f"{_fmt(lo)} <= value ± u <= {_fmt(hi)} {unit}", dec, (min(v), max(v)), unit, u, (lo, hi),
                 len(v), note, stats(v))


def check_abs_le(name: str, values: Iterable[float], budget: float, u: float, unit: str, note: str = "") -> Check:
    return check_le(name, [abs(float(x)) for x in values], budget, u, unit, "max |x|", note)


def check_eq(name: str, got: Any, want: Any, note: str = "", tol: float = 0) -> Check:
    if isinstance(got, (int, float)) and isinstance(want, (int, float)):
        ok = abs(got - want) <= tol
    else:
        ok = got == want
    crit = f"== {_fmt(want)}" + (f" ± {_fmt(tol)}" if tol else "")
    return Check(name, crit, PASS if ok else FAIL, got, "", 0 if tol == 0 else tol, want, None, note)


def check_all_eq(name: str, pairs: list[tuple[Any, Any]], note: str = "", tol: float = 0) -> Check:
    """Every (got, want) pair equal (± tol): e.g. MT-2 counter vs Δpos_steps per trial."""
    bad = [(i, g, w) for i, (g, w) in enumerate(pairs) if (abs(g - w) > tol if tol else g != w)]
    if not pairs:
        return Check(name, "all equal", NOT_MEASURED, None, n=0, note=note or "no trials")
    return Check(name, "all equal" + (f" ± {_fmt(tol)}" if tol else ""), FAIL if bad else PASS,
                 f"{len(pairs) - len(bad)}/{len(pairs)} equal", n=len(pairs),
                 note=(note + ("; first mismatches: " + str(bad[:5]) if bad else "")).strip("; "))


def check_true(name: str, ok: bool, criterion: str, value: Any = None, note: str = "") -> Check:
    return Check(name, criterion, PASS if ok else FAIL, value if value is not None else ok, note=note)


def info(name: str, value: Any, unit: str = "", note: str = "") -> Check:
    return Check(name, "recorded", INFO, value, unit, note=note)


def not_measured(name: str, criterion: str, why: str) -> Check:
    return Check(name, criterion, NOT_MEASURED, note=why)


def manual(name: str, criterion: str, value: Any = None, note: str = "") -> Check:
    return Check(name, criterion, MANUAL, value, note=note)


def target_only(name: str, criterion: str, why: str) -> Check:
    return Check(name, criterion, TARGET_ONLY, note=why)


def na(name: str, criterion: str, why: str) -> Check:
    return Check(name, criterion, NA, note=why)


# ------------------------------------------------------------------------------------------ item verdict
def verdict(checks: list[Check]) -> str:
    """Item verdict: FAIL > INCONCLUSIVE > open (MANUAL / TARGET-ONLY / NOT MEASURED) > PASS; N/A and INFO ignored."""
    ds = [c.decision for c in checks if c.decision not in (NA, INFO)]
    if not ds:
        return NA
    if FAIL in ds:
        return FAIL
    if INCONCL in ds:
        return INCONCL
    open_ = sorted({d for d in ds if d in OPEN_KINDS})
    if open_ and PASS in ds:
        return "PARTIAL (" + ", ".join(open_) + " open)"
    if open_:
        return "OPEN (" + ", ".join(open_) + ")"
    return PASS


# ------------------------------------------------------------------------------------------ conversions
def probe_tick_us(psc: int, f_probe: float = F_PROBE) -> float:
    """One probe tick in µs: TIM8 at f_probe / (PSC + 1) (ICD App. C)."""
    return (psc + 1) * 1e6 / f_probe


def probe_us(ticks: int, psc: int, f_probe: float = F_PROBE) -> float:
    return ticks * probe_tick_us(psc, f_probe)


def cycles_us(cycles: float, f_cpu: float = F_CPU) -> float:
    return cycles * 1e6 / f_cpu


def sdiff32(a: int, b: int) -> int:
    """Signed a - b of two 32-bit device times (t_us wraps after 71.6 min)."""
    return ((a - b + 0x80000000) & 0xFFFFFFFF) - 0x80000000


def u_stamp_pair_us(dma_latency_ns: float = 1000.0) -> float:
    """Uncertainty of a difference of two MT-4 stamps: 1 µs quantisation + DMA service latency (INFO w5)."""
    return 1.0 + dma_latency_ns / 1000.0


def uart_frame_us(n_bytes: int, baud: float) -> float:
    """Time from the first start bit to the end of the last stop bit (8N1 = 10 bit times per byte)."""
    return n_bytes * 10 * 1e6 / baud


# ------------------------------------------------------------------------------------------ DWT (HG-18, F2)
@dataclass
class DwtSection:
    section: int
    valid: bool
    count: int
    min_cycles: int
    max_cycles: int
    sum_cycles: int
    hist: list[int] = field(default_factory=list)

    @classmethod
    def from_words(cls, section: int, w: list[int]) -> "DwtSection":
        return cls(section, bool(w[0]), w[1], w[2], w[3], w[4] | (w[5] << 32), list(w[6:16]))

    @property
    def mean_cycles(self) -> float:
        return self.sum_cycles / self.count if self.count else 0.0


def dwt_overhead(cal: DwtSection) -> tuple[float, float]:
    """Stamp-pair overhead (section 21 / INFO w6): (mean cycles, uncertainty cycles = half spread + 1)."""
    if not cal.valid or not cal.count:
        return 0.0, 0.0
    return cal.mean_cycles, (cal.max_cycles - cal.min_cycles) / 2 + 1


def check_dwt(name: str, sec: DwtSection, budget_us: float, ovh: tuple[float, float], note: str = "",
              exc_cycles: float = 0.0) -> Check:
    """Section maximum minus the calibrated stamp overhead against a µs budget (§6.3 HW_MEAS_DWT rule). For an
    interrupt handler `exc_cycles` adds the exception entry + exit that the DWT section (first to last
    instruction of the handler body) does not see (D-51 / FW_design §9.8 v0.8: NFR-007 is judged on the total,
    entry 12 + vector 5 + exit 10 = 27 cycles; a lazy FP context is stacked inside the body and is measured)."""
    crit = (f"max(section {sec.section}) − stamp overhead" + (f" + {exc_cycles:g} cyc entry/exit" if exc_cycles else "")
            + f" + u <= {budget_us} µs")
    if not sec.valid:
        return Check(name, crit, NOT_MEASURED, None, "µs", None, budget_us, 0,
                     (note + "; " if note else "") + "DWT not valid in this image (w0 = 0)")
    if sec.count == 0:
        return Check(name, crit, NOT_MEASURED, None, "µs", None, budget_us, 0,
                     (note + "; " if note else "") + "section never executed during the workload (count 0)")
    net = cycles_us(max(sec.max_cycles - ovh[0], 0.0) + exc_cycles)
    u = cycles_us(ovh[1])
    raw = cycles_us(sec.max_cycles + exc_cycles)
    dec = PASS if raw <= budget_us else decide_le(net, budget_us, u)   # raw max ≤ budget is conservative
    return Check(name, crit, dec, net, "µs", u, budget_us, sec.count,
                 (f"raw max {raw:.3f} µs ({sec.max_cycles} cyc), min {cycles_us(sec.min_cycles):.3f} µs, "
                  f"mean {cycles_us(sec.mean_cycles):.3f} µs, hist {sec.hist}" + (f"; {note}" if note else "")))
