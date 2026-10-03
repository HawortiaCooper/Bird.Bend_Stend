"""Validator F independent reference implementations (oracle; never production code, never imported by it).

Written from the R4 text (sections 1.5, 5, 6, 7, 8.3, 8.4, 9, 10, 12 implementation notes) and from ICD v0.4.1
§0.1 / §9.3 / §11.4 — not from any implementer code. P1: proved the R4 §12 vectors self-consistent (22/22 x 3);
M1: oracle of the validation suite (moved from the P1 scratchpad, SW_test_plan §1).

Verifies: (oracle) SYS-003, IF-005, SW-CFG-003
"""
from __future__ import annotations
import math
from dataclasses import dataclass

G0 = 9.80665
FS_N = 200 * G0


def rha(x: float) -> int:  # round half away from zero (SYS-003)
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


def _ols(x, y):
    n = len(x)
    mx = sum(x) / n; my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    if sxx == 0:
        raise ValueError("degenerate")
    k = sxy / sxx
    return k, my - k * mx


@dataclass
class Cal:
    K: float; B: float; residuals: list; r2: float; nl_pct_span: float; nl_pct_fs: float; status: str


def load_calibration(raw, masses):
    f = [m * G0 for m in masses]
    k, b = _ols(raw, f)
    if k == 0:
        raise ValueError("K == 0")
    res = [fi - (k * r + b) for r, fi in zip(raw, f)]
    my = sum(f) / len(f)
    sst = sum((fi - my) ** 2 for fi in f)
    r2 = 1 - sum(e * e for e in res) / sst
    span = max(abs(v) for v in f)
    mr = max(abs(e) for e in res)
    nl_span = 100 * mr / span; nl_fs = 100 * mr / FS_N
    mono = all((raw[i + 1] - raw[i]) * (f[i + 1] - f[i]) * (1 if k > 0 else -1) > 0 for i in range(len(raw) - 1))
    if len(raw) == 2:
        st = "UNVERIFIED_LINEARITY"
    elif not mono or nl_span > 0.5:
        st = "FAIL"
    elif nl_span > 0.1:
        st = "WARN"
    else:
        st = "PASS"
    return Cal(k, b, res, r2, nl_span, nl_fs, st)


@dataclass
class RS:
    median: float; mad: float; sigma_mad: float; mean: float; std: float; n_used: int; n_rejected: int


def _median(v):
    s = sorted(v); n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def robust_window_stats(x, k=5.0):
    med = _median(x)
    mad = _median([abs(a - med) for a in x])
    sm = 1.4826 * mad
    kept = [a for a in x if abs(a - med) <= k * sm] if sm > 0 else list(x)
    n = len(kept); mean = sum(kept) / n
    std = math.sqrt(sum((a - mean) ** 2 for a in kept) / (n - 1)) if n > 1 else 0.0
    return RS(med, mad, sm, mean, std, n, len(x) - n)


def se_ar1(y):
    n = len(y); m = sum(y) / n
    d = [a - m for a in y]
    s2 = sum(a * a for a in d)
    rho = sum(d[i] * d[i + 1] for i in range(n - 1)) / s2 if s2 else 0.0
    rho = min(max(rho, 0.0), 0.99)
    neff = n * (1 - rho) / (1 + rho)
    std = math.sqrt(s2 / (n - 1))
    return rho, neff, std / math.sqrt(neff)


def drift_slope(t, x):
    return _ols(t, x)[0]


def force_n(raw, k, tare):
    return k * (raw - tare)


def fw_raw_limits(f_hi, f_lo, k, tare_raw):
    a = tare_raw + f_hi / k; b = tare_raw + f_lo / k
    return math.ceil(min(a, b)), math.floor(max(a, b))


def travel_cal_step1(spm_old, cmd_mm, meas_mm):
    n1 = rha(cmd_mm * spm_old)
    return n1, n1 / meas_mm


def travel_cal_step2(spm1, n1, cmd_mm, meas_total_mm, meas1_mm):
    n2 = rha(cmd_mm * spm1)
    spm2 = (n1 + n2) / meas_total_mm
    inc = n2 / (meas_total_mm - meas1_mm)
    return n2, spm2, inc, inc / spm1 - 1


def um_to_steps(u, spm):
    return rha(u * spm / 1000)


def steps_to_um(s, spm):
    return rha(s * 1000 / spm)


def ramp_periods(n_steps, f_tick, v, a, d):
    ca = f_tick * math.sqrt(2 / a); cd = f_tick * math.sqrt(2 / d); cmin = f_tick / v
    out = []; acc = 0.0
    for k in range(1, n_steps + 1):
        r = n_steps - k + 1
        c = max(ca / (math.sqrt(k) + math.sqrt(k - 1)), cmin, cd / (math.sqrt(r) + math.sqrt(r - 1)))
        acc += c; ci = int(acc); acc -= ci; out.append(ci)
    return out


def avr446_periods(f_tick, a, count):
    c = int(0.676 * f_tick * math.sqrt(2 / a)); out = [c]; n = 0; rest = 0
    while len(out) < count:
        n += 1; num = 2 * c + rest; c -= num // (4 * n + 1); rest = num % (4 * n + 1); out.append(c)
    return out


def plan_trapezoid(n, v, a, d):
    na = v * v / (2 * a); nd = v * v / (2 * d)
    if na + nd <= n:
        na_i = rha(na); nd_i = rha(nd)
        return {"kind": "trap", "n_acc": na_i, "n_cruise": n - na_i - nd_i, "n_dec": nd_i, "v_peak": v,
                "t": v / a + v / d + (n - na - nd) / v}
    na_x = n * d / (a + d); vp = math.sqrt(2 * a * na_x); na_i = rha(na_x)
    return {"kind": "tri", "n_acc": na_i, "n_cruise": 0, "n_dec": n - na_i, "v_peak": vp, "t": vp / a + vp / d}


def unwrap_us(ts):
    out = []; off = 0; prev = None
    for t in ts:
        if prev is not None and t < prev:
            off += 2 ** 32
        out.append(t + off); prev = t
    return out


@dataclass
class SS:
    n: int; min: int; max: int; mean: float; std: float


def steady_state(frames, pos_um, t_from_us, t_to_us):
    v = [r for (t, f, p, r) in frames if (f & 1) and not (f & 2) and p == pos_um and t_from_us <= t <= t_to_us]
    n = len(v); m = sum(v) / n
    s = math.sqrt(sum((a - m) ** 2 for a in v) / (n - 1))
    return SS(n, min(v), max(v), m, s)


def trim_step(f, f_target, k_est, kp, max_step):
    dx = kp * (f_target - f) / k_est
    return max(-max_step, min(max_step, dx))


def stiffness_ols(x, f):
    return _ols(x, f)


def work_trapz(x, f):
    return sum((x[i + 1] - x[i]) * (f[i] + f[i + 1]) / 2 for i in range(len(x) - 1))


# ======================================================================================================
# ICD v0.4.1 tables written from the ICD text (M1 oracle)
# ======================================================================================================
#: ICD §9.3 retry classes (JOG decided by v: JOG 0 = RETRY, JOG != 0 = VERIFY)
RETRY_CLASS = {
    "PING": "RETRY", "GET_INFO": "RETRY", "GET_STATUS": "RETRY", "GET_PARAM": "RETRY", "GET_ALL_PARAMS": "RETRY",
    "SET_PARAM": "RETRY", "STREAM_START": "RETRY", "STREAM_STOP": "RETRY", "SET_VALID": "RETRY",
    "STOP": "CONFIRM", "HALT": "CONFIRM", "PAUSE": "CONFIRM",
    "MOVE_ABS": "VERIFY", "MOVE_UNTIL_LOAD": "VERIFY", "HOME": "VERIFY", "RESUME": "VERIFY", "ENABLE": "VERIFY",
    "DISABLE": "VERIFY", "SAVE_PARAMS": "VERIFY", "LOAD_PARAMS": "VERIFY", "DEFAULT_PARAMS": "VERIFY",
    "REBOOT": "VERIFY", "HALT_CLEAR": "VERIFY", "ESTOP_CLEAR": "VERIFY", "FAULT_CLEAR": "VERIFY",
}
#: ICD §2.4 / §9.3 / IF-011: commands written on the priority path (bypass every SW queue)
PRIORITY = {"STOP", "HALT", "PAUSE", "HALT_CLEAR", "ESTOP_CLEAR", "FAULT_CLEAR"}
#: SRS IF-012 minimum command set (+ RESUME D-31)
IF012 = ["PING", "GET_INFO", "GET_STATUS", "GET_ALL_PARAMS", "GET_PARAM", "SET_PARAM", "SAVE_PARAMS",
         "LOAD_PARAMS", "DEFAULT_PARAMS", "STREAM_START", "STREAM_STOP", "SET_VALID", "ENABLE", "DISABLE", "HOME",
         "MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD", "STOP", "HALT", "HALT_CLEAR", "PAUSE", "RESUME", "ESTOP_CLEAR",
         "FAULT_CLEAR", "REBOOT"]


def rule_ok(rule: str, v: dict) -> bool:
    """ICD §11.4 hard rules H1..H5 on a complete value set (enum afe.rate_sps: 0 = SPS10, 1 = SPS80)."""
    if rule == "H1":
        return v["limits.soft_min_um"] < v["limits.soft_max_um"]
    if rule == "H2":
        return v["safety.load_raw_min"] < v["safety.load_raw_max"]
    if rule == "H3":
        return v["motion.max_step_rate_hz"] * (v["motion.pulse_high_ns"] + v["motion.pulse_low_min_ns"]) <= 10**9
    if rule == "H4":
        return v["motion.v_max_load_um_s"] <= v["motion.v_max_travel_um_s"]
    if rule == "H5":
        sps = {0: 10, 1: 80}[int(v["afe.rate_sps"])]
        return v["afe.timeout_ms"] * sps >= 2000
    raise KeyError(rule)


def all_rules_ok(v: dict) -> bool:
    return all(rule_ok(r, v) for r in ("H1", "H2", "H3", "H4", "H5"))


def f32(x: float) -> float:
    import struct as _s
    return _s.unpack("<f", _s.pack("<f", float(x)))[0]
