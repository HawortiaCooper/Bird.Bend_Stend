"""M4 pure calculations: steady-state extraction, load-step approach / trim arithmetic, timeouts, planned path, 3-point
bend (R4 §12 TV-SS, TV-C, TV-D; SRS SW-SEQ-007 timeout vector) + property tests.

Verifies: SW-REP-002, SW-REP-004, SW-SEQ-006, SW-SEQ-007, SW-SCH-001
"""
from __future__ import annotations

import math
import random
import statistics as st

import numpy as np
import pytest

from bend_stand.calc import trim as T
from bend_stand.calc.derived import bend3p_modulus, bend3p_strain, bend3p_stress, stiffness_ols, work_trapz
from bend_stand.calc.path import advance, planned_path
from bend_stand.calc.steady import (
    STEADY_FLAGS_MASK, STEADY_STATUS_MASK, median_rate_sps, qstats, steady_frame, steady_state, window_stats,
)

K = 0.00045666488086106374
TARE = 125430.0
FRAMES = [(0, 2, 4000, 300000), (12500, 2, 4400, 300030), (25000, 2, 4800, 300060),
          (37500, 0, 5000, 300000), (50000, 0, 5000, 300030), (62500, 1, 5000, 300060),
          (75000, 1, 5000, 300000), (87500, 1, 5000, 300030), (100000, 1, 5000, 300060),
          (112500, 1, 5000, 300090), (125000, 1, 5000, 300030), (137500, 1, 5000, 300060)]


# ---------------------------------------------------------------------------------------------- TV-SS
@pytest.mark.req("SW-REP-002")
def test_tv_ss_steady_state() -> None:
    s = steady_state(FRAMES, pos_um=5000, t_from_us=0, t_to_us=10**9)
    assert s.n == 7 and (s.min, s.max) == (300000, 300090)
    assert s.mean == pytest.approx(300047.14285714284) and s.std == pytest.approx(29.277002188455995)
    F = [K * (r - TARE) for (t, f, p, r) in FRAMES if (f & 1) and not (f & 2)]
    assert sum(F) / len(F) == pytest.approx(79.7415167391565, rel=1e-9)
    assert st.stdev(F) == pytest.approx(0.013369778716359415, rel=1e-6)


@pytest.mark.req("SW-REP-002")
def test_tv_ss_through_window_stats_and_icd_mask() -> None:
    t = [f[0] for f in FRAMES]
    fl = [f[1] for f in FRAMES]
    sp = [f[2] for f in FRAMES]
    raw = [float(f[3]) for f in FRAMES]
    fn = [K * (r - TARE) for r in raw]
    ws = window_stats(t, fl, [0] * len(t), sp, raw, fn, t0_us=0, t1_us=150_000, rate_sps=80.0, capture_s=0.15)
    assert ws.n == 7 and ws.pos_um == 5000
    assert ws.raw.mean == pytest.approx(300047.14285714284) and ws.raw.std == pytest.approx(29.277002188455995)
    assert ws.f.mean == pytest.approx(79.7415167391565, rel=1e-9)
    assert ws.x.mean == pytest.approx(5.0) and ws.x.std == 0.0
    # ICD §7.6: flags bits 4–7 and status bits 0–8, 13, 14 exclude a frame; status bits 9–12, 15 do not
    assert STEADY_FLAGS_MASK == 0xF0 and STEADY_STATUS_MASK == 0x61FF
    assert steady_frame(1) and not steady_frame(3) and not steady_frame(0)
    for b in (4, 5, 6, 7):
        assert not steady_frame(1 | 1 << b)
    for b in list(range(9)) + [13, 14]:
        assert not steady_frame(1, 1 << b)
    for b in (9, 10, 11, 12, 15):
        assert steady_frame(1, 1 << b)
    st_ = [0] * len(t)
    st_[6] = 1 << 13                                                    # POS_UNCERTAIN on one frame
    assert window_stats(t, fl, st_, sp, raw, fn, t0_us=0, t1_us=150_000).n == 6


@pytest.mark.req("SW-REP-002")
def test_window_rules_setpoint_time_incomplete_on_target() -> None:
    n = 80
    t = [i * 12_500 for i in range(n)]
    fl = [1] * n
    sp = [5000] * n
    sp[40] = 5001                                                      # setpoint changed → frame excluded
    raw = [1000.0 + (i % 3) for i in range(n)]
    f = [0.1 * r for r in raw]
    ws = window_stats(t, fl, [0] * n, sp, raw, f, t0_us=0, t1_us=t[-1], rate_sps=80.0, capture_s=1.0,
                      target_n=100.0, tol_n=1.0)
    assert ws.n == n - 1 and "ON_TARGET" in ws.flags and "INCOMPLETE" not in ws.flags
    ws2 = window_stats(t, fl, [0] * n, sp, raw, f, t0_us=0, t1_us=t[-1], rate_sps=80.0, capture_s=1.0,
                       target_n=110.0, tol_n=1.0)
    assert "NOT_ON_TARGET" in ws2.flags
    half = window_stats(t, [1] * 40 + [0] * 40, [0] * n, sp, raw, f, t0_us=0, t1_us=t[-1], rate_sps=80.0,
                        capture_s=1.0)
    assert half.n == 40 and "INCOMPLETE" in half.flags                 # 40 < 0.8 · 1 s · 80 SPS
    late = window_stats(t, fl, [0] * n, sp, raw, f, t0_us=512_500, t1_us=t[-1], rate_sps=80.0)
    assert late.n == 39 and late.pos_um == 5000                       # t window [t0, t1]
    odd = window_stats(t, fl, [0] * n, sp, raw, f, t0_us=500_000, t1_us=t[-1], rate_sps=80.0)
    assert odd.n == 1 and odd.pos_um == 5001                           # setpoint of the window's first frame
    nan_f = window_stats(t, fl, [0] * n, sp, raw, [float("nan")] * n, t0_us=0, t1_us=t[-1], target_n=1.0, tol_n=1.0)
    assert nan_f.f.n == 0 and math.isnan(nan_f.f.mean) and "NOT_ON_TARGET" in nan_f.flags
    empty = window_stats([], [], [], [], [], [], t0_us=0, t1_us=1)
    assert empty.n == 0 and empty.pos_um is None and "INCOMPLETE" in empty.flags


@pytest.mark.req("SW-REP-002")
def test_qstats_drift_se_and_ramp_result() -> None:
    t = np.arange(10) * 0.0125
    x = 1000.0 + 2 * np.arange(10)
    q = qstats(t, x, 10.0)
    assert q.drift == pytest.approx(1600.0) and q.n == 10 and q.se > 0                 # R4 TV-RS drift vector
    xs = [0.0, 0.1, 0.2, 0.3, 0.4]
    fs = [0.0, 5.2, 9.8, 15.1, 20.0]
    ws = window_stats([i * 12500 for i in range(5)], [3] * 5, [0] * 5, [int(v * 1000) for v in xs], [0.0] * 5, fs,
                      t0_us=0, t1_us=50_000, ramp=True)
    assert ws.flags[0] == "RAMP" and ws.ramp["k_n_mm"] == pytest.approx(49.9)
    assert ws.ramp["x_max_mm"] == pytest.approx(0.4) and ws.ramp["f_max_n"] == 20.0
    assert median_rate_sps([0, 12500, 25000, 37500, 50000]) == pytest.approx(80.0)
    assert median_rate_sps([0, 1]) is None


# ---------------------------------------------------------------------------------------------- TV-C, approach
@pytest.mark.req("SW-SEQ-006")
def test_tv_c_trim_controller() -> None:
    x, seq = 3.0, []
    for _ in range(5):
        F = 50.0 * x
        seq.append((x, F))
        x = x + T.trim_step(F, f_target=200.0, k_est=40.0, kp=0.5, max_step=10.0)
    assert seq[1] == (3.625, 181.25) and seq[4] == pytest.approx((3.980224609375, 199.01123046875))
    assert 1 - 0.5 * 50.0 / 40.0 == pytest.approx(0.375)
    assert 50.0 * 2.0 * 0.05 == pytest.approx(5.0)
    assert T.trim_step(0.0, 200.0, 40.0) == 0.2 and T.trim_step(400.0, 200.0, 40.0) == -0.2   # |Δx| ≤ 0.2 mm
    with pytest.raises(ValueError):
        T.trim_step(0.0, 1.0, 0.0)


@pytest.mark.req("SW-SEQ-006")
def test_trim_converges_within_ten_iterations_k50_kest40() -> None:
    """SRS acceptance in arithmetic form: specimen k = 50 N/mm, k_est = 40, ±0.2 mm steps, tol 2 N."""
    k = 50.0
    x = 190.0 / k                                  # where a FW-timed approach to F_stop = 200 − band ends
    for it in range(11):
        f = k * x
        if abs(200.0 - f) <= 2.0:
            break
        x += T.trim_step(f, 200.0, 40.0)
    else:  # pragma: no cover
        pytest.fail("trim did not converge")
    assert it <= 10 or abs(200.0 - k * x) <= 2.0


@pytest.mark.req("SW-SEQ-006")
def test_approach_band_and_raw_stop_rounding_both_signs() -> None:
    assert T.approach_band(2.0, 50.0, 1.0) == pytest.approx(3.25)               # k·v·0.065 s > tol
    assert T.approach_band(5.0, 50.0, 1.0) == 5.0
    # K > 0, F rising: GE, floor → the FW stops at or before F_stop
    r, c = T.force_to_raw_stop(100.0, 0.001, 1000.0, +1)
    assert (r, c) == (101000, T.CMP_GE)
    r, c = T.force_to_raw_stop(100.3, 0.001, 1000.0, +1)
    assert (r, c) == (101300, T.CMP_GE) and r <= 1000.0 + 100.3 / 0.001
    r, c = T.force_to_raw_stop(100.0004, 0.001, 1000.0, +1)
    assert c == T.CMP_GE and r == 101000                                       # floor
    # K > 0, F falling: LE, ceil
    r, c = T.force_to_raw_stop(50.0004, 0.001, 1000.0, -1)
    assert c == T.CMP_LE and r == 51001
    # K < 0: the raw value falls while F rises → LE
    r, c = T.force_to_raw_stop(100.0, -0.001, 1000.0, +1)
    assert c == T.CMP_LE and r == -99000
    r, c = T.force_to_raw_stop(-100.0, -0.001, 1000.0, -1)
    assert c == T.CMP_GE and r == 101000
    assert T.force_to_raw_stop(1e9, 0.001, 0.0, +1)[0] == T.RAW_STOP_MAX
    with pytest.raises(ValueError):
        T.force_to_raw_stop(1.0, 0.0, 0.0, 1)


@pytest.mark.req("SW-SEQ-006")
def test_k_est_from_approach_ols_clamp_fallback() -> None:
    xs = [i * 0.01 for i in range(100)]
    fs = [0.0 if x < 0.3 else 50.0 * (x - 0.3) for x in xs]                    # contact after 0.3 mm
    k, ok = T.k_est_from_approach(xs, fs, pull_dir=1, k_min=0.5, k_max=1e5, fallback=40.0)
    assert ok and k == pytest.approx(50.0, rel=1e-9)
    k, ok = T.k_est_from_approach([-v for v in xs], fs, pull_dir=-1, k_min=0.5, k_max=1e5, fallback=40.0)
    assert ok and k == pytest.approx(50.0, rel=1e-9)
    assert T.k_est_from_approach(xs, fs, pull_dir=1, k_min=0.5, k_max=30.0, fallback=40.0) == (30.0, True)
    assert T.k_est_from_approach(xs[:2], fs[:2], pull_dir=1, k_min=0.5, k_max=1e5, fallback=40.0) == (40.0, False)
    assert T.k_est_from_approach([0.0, 0.01, 0.02, 0.03], [0, 1, 2, 3], pull_dir=1, k_min=0.5, k_max=1e5,
                                 fallback=40.0) == (40.0, False)                # span < 0.1 mm
    assert T.k_est_from_approach(xs, [-v for v in fs], pull_dir=1, k_min=0.5, k_max=1e5,
                                 fallback=40.0) == (40.0, False)                # wrong sign → fallback


@pytest.mark.req("SW-SEQ-007")
def test_step_timeout_vector_and_hold_rule() -> None:
    """SRS SW-SEQ-007: 130 → 290 mm at 2 mm/s, 100 mm/s² → travel time 80.02 s → timeout 106.0 s."""
    assert T.move_time_s(290 - 130, 2.0, 100.0) == pytest.approx(80.02)
    assert T.step_timeout_s(160.0, 2.0, 100.0) == pytest.approx(106.024)
    assert round(T.step_timeout_s(160.0, 2.0, 100.0), 1) == 106.0
    assert T.hold_timeout_s(5.0) == 25.0
    assert T.step_timeout_s(0.0, 1.0, 1.0) == 10.0
    with pytest.raises(ValueError):
        T.move_time_s(1.0, 0.0, 1.0)


# ---------------------------------------------------------------------------------------------- path, TV-D
@pytest.mark.req("SW-SCH-001")
def test_planned_path_advance_rules() -> None:
    assert advance("travel", 0.0, 0.0, 2.0, 50.0, 1) == (2.0, 100.0, "x")
    assert advance("travel", 2.0, 100.0, 0.0, 50.0, -1) == (0.0, 200.0, "x")
    assert advance("load", 0.0, 0.0, 200.0, 50.0, 1) == (4.0, 200.0, "F")
    assert advance("load", 0.0, 0.0, 200.0, 50.0, -1) == (-4.0, 200.0, "F")
    assert advance("home", 3.0, 10.0, None, 50.0, 1, home_x=-10.0) == (-10.0, 0.0, "x")
    assert advance("tare", 3.0, 10.0, None, 50.0, 1) == (3.0, 0.0, "x")
    assert advance("hold", 3.0, 10.0, None, 50.0, 1) == (3.0, 10.0, "both")

    class P:
        def __init__(self, i: int, kind: str, xs: float, fs: float, xt: float, ft: float, cap: object) -> None:
            self.exec_idx, self.kind, self.label, self.known, self.capture = i, kind, kind, "x", cap
            self.x_start_mm, self.f_start_n, self.x_target_mm, self.f_target_n = xs, fs, xt, ft

    pts = planned_path([P(0, "travel", 0, 0, 1, 50, None), P(1, "home", 1, 50, -10, 0, None),
                        P(2, "travel", -10, 0, 0, 500, (1, 2))])
    assert [p.brk for p in pts] == [False, False, True, False] and pts[0].label == "start" and pts[-1].capture


@pytest.mark.req("SW-REP-004")
def test_tv_d_bend3p_and_derived() -> None:
    xs = [0.0, 0.1, 0.2, 0.3, 0.4]
    Fs = [0.0, 5.2, 9.8, 15.1, 20.0]
    k, b = stiffness_ols(xs, Fs)
    assert k == pytest.approx(49.9) and b == pytest.approx(0.04, abs=1e-12)
    assert work_trapz(xs, Fs) == pytest.approx(4.01)
    assert bend3p_stress(200.0, 100.0, 20.0, 5.0) == pytest.approx(60.0)
    assert bend3p_strain(2.0, 100.0, 5.0) == pytest.approx(0.006)
    assert bend3p_modulus(100.0, 20.0, 5.0, 50.0) == pytest.approx(5000.0)


# ---------------------------------------------------------------------------------------------- properties
@pytest.mark.req("SW-SEQ-006", "SW-REP-002")
def test_random_properties() -> None:
    rng = random.Random()                                              # seeded by pytest-randomly
    for _ in range(300):
        k = rng.choice([1, -1]) * rng.uniform(1e-5, 1e-2)
        tare = rng.uniform(-1e5, 1e5)
        f_stop = rng.uniform(-1500, 1500)
        sgn = rng.choice([1, -1])
        r, c = T.force_to_raw_stop(f_stop, k, tare, sgn)
        f_at = k * (r - tare)
        if not T.RAW_STOP_MIN < r < T.RAW_STOP_MAX:
            continue
        assert (f_at - f_stop) * sgn <= 1e-9 * max(1.0, abs(f_stop))   # the threshold never lies beyond F_stop
        assert c == (T.CMP_GE if sgn * k > 0 else T.CMP_LE)
        kk = rng.uniform(1.0, 500.0)
        x0 = rng.uniform(0, 5)
        xs = [x0 + i * 0.01 for i in range(60)]
        fs = [kk * (x - x0) + 3.0 for x in xs]
        assert T.k_est_from_approach(xs, fs, pull_dir=1, k_min=0.5, k_max=1e5, fallback=1.0)[0] == pytest.approx(kk)
        n = rng.randint(2, 200)
        v = [rng.gauss(100.0, 5.0) for _ in range(n)]
        ws = window_stats(list(range(0, n * 12500, 12500)), [1] * n, [0] * n, [7] * n, v, v, t0_us=0,
                          t1_us=n * 12500)
        assert ws.n == n and ws.raw.mean == pytest.approx(float(np.mean(v)))
        assert ws.raw.std == pytest.approx(float(np.std(v, ddof=1)))
