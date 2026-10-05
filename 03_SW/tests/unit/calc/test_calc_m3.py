"""M3 calculations (WP-B15): R4 §12 vectors TV-RS, TV-LC, TV-T, TV-TC, TV-D and the SRS rule tests for load
calibration (SW-CAL-006/007/008), travel calibration (SW-CAL-002/003), tare (SW-TARE-002/003), SW limits
(SAF-SW-001/006, SW-LIM-002) and the derived channels (SW-RT-004, SW-REP-004).

Verifies: SW-PLT-002, SW-CAL-002, SW-CAL-003, SW-CAL-006, SW-CAL-007, SW-CAL-008, SW-CAL-009, SW-TARE-002,
SW-TARE-003, SAF-SW-001, SAF-SW-006, SW-LIM-002, SW-RT-004, SW-REP-004
"""
from __future__ import annotations

import math
import random

import numpy as np
import pytest

from bend_stand.calc import derived as D
from bend_stand.calc import limits as L
from bend_stand.calc.loadcal import (
    afe_block, afe_matches, extrapolated, fw_raw_limits, load_calibration, low_span, mass_rules,
)
from bend_stand.calc.motion import f32, um_to_steps
from bend_stand.calc.stats import drift_slope, robust_window_stats, se_ar1, window_acceptance
from bend_stand.calc.tare import force_n, tare_acceptance, tare_offset_warning
from bend_stand.calc.travelcal import (
    consistency, spm_from_steps, target_for_steps, travel_cal_step1, travel_cal_step2, travel_plausibility,
)
from bend_stand.calc.units import FS_N, G0

RAW = [125000.0, 339760.0, 554490.0]
M = [0.0, 10.0, 20.0]
K = 0.00045666488086106374
TARE = 125430.0


# ------------------------------------------------------------------------------------------------ TV-LC
@pytest.mark.req("SW-CAL-007")
def test_tv_lc_pass() -> None:
    c = load_calibration(RAW, M)
    assert c.K == pytest.approx(0.00045666488086106374, rel=1e-9)
    assert c.B == pytest.approx(-57.085393272546426, rel=1e-9)
    assert list(c.residuals) == pytest.approx([0.0022831649134573695, -0.004566648808591367,
                                               0.002283483895155314], abs=1e-9)
    assert c.r2 == pytest.approx(0.9999999983736457, abs=1e-12)
    assert c.nl_pct_span == pytest.approx(0.0023283429145484784, rel=1e-6)
    assert c.nl_pct_fs == pytest.approx(0.00023283429145484785, rel=1e-6)
    assert c.status == "PASS"
    assert c.K / G0 == pytest.approx(4.656685829116607e-05, rel=1e-9)
    assert 1 / c.K == pytest.approx(2189.7895851208254, rel=1e-9)
    assert c.k == c.K and c.b == c.B and c.as_fit()["status"] == "PASS"


@pytest.mark.req("SW-CAL-007")
def test_tv_lc_warn_fail_2pt_degenerate() -> None:
    w = load_calibration([125000.0, 339760.0, 560000.0], M)
    assert w.K == pytest.approx(0.00045085660914376133, rel=1e-9)
    assert w.nl_pct_span == pytest.approx(0.41990115858589555, rel=1e-6)
    assert w.r2 == pytest.approx(0.9999471021069184, abs=1e-12) and w.status == "WARN"
    f = load_calibration([125000.0, 339760.0, 566000.0], M)
    assert f.K == pytest.approx(0.00044464559345022724, rel=1e-9)
    assert f.nl_pct_span == pytest.approx(0.8675289068826835, rel=1e-6)
    assert f.r2 == pytest.approx(0.9997741670782083, abs=1e-12) and f.status == "FAIL"
    t = load_calibration([125000.0, 554490.0], [0.0, 20.0])
    assert t.K == pytest.approx(0.00045666488160376256, rel=1e-9)
    assert t.B == pytest.approx(-57.083110200470315, rel=1e-9)
    assert t.status == "UNVERIFIED_LINEARITY"
    with pytest.raises(ValueError):
        load_calibration([5.0, 5.0, 5.0], M)


@pytest.mark.req("SW-CAL-007")
def test_load_cal_negative_k_nonmonotonic_and_k_zero() -> None:
    neg = load_calibration([125000.0, -89760.0, -304490.0], M)          # cell wired inverted: K < 0 accepted
    assert neg.K < 0 and neg.status == "PASS"
    nm = load_calibration([125000.0, 339760.0, 300000.0], M)
    assert nm.status == "FAIL" and any("monotonic" in s for s in nm.texts)
    kz = load_calibration([125000.0, 900000.0, 125100.0], [0.0, 10.0, 10.0])
    assert kz.status == "FAIL"
    zero_force = load_calibration([1.0, 2.0, 3.0], [0.0, 0.0, 0.0])
    assert zero_force.status == "FAIL" and "K ≈ 0" in zero_force.texts[0]
    with pytest.raises(ValueError):
        load_calibration([1.0], [0.0])
    with pytest.raises(ValueError):
        load_calibration([1.0, 2.0], [0.0])
    with pytest.raises(ValueError):
        load_calibration([1.0, float("nan")], [0.0, 1.0])


@pytest.mark.req("SW-CAL-007")
def test_load_cal_linear_within_noise_note() -> None:
    c = load_calibration([125000.0, 339760.0, 554500.0], M, point_se_counts=[30.0, 30.0, 30.0])
    assert c.linear_within_noise is True and "linear within noise" in c.texts
    c2 = load_calibration(RAW, M, point_se_counts=[float("nan")] * 3)
    assert c2.linear_within_noise is None


@pytest.mark.req("SW-CAL-008")
def test_low_span_and_extrapolation_1kg_10kg() -> None:
    raw = [50_000.0, 50_000.0 + 3285 * G0, 50_000.0 + 3285 * 10 * G0]
    c = load_calibration(raw, [0.0, 1.0, 10.0])
    assert c.low_span and c.status == "PASS" and c.f_span_n == pytest.approx(98.0665)
    assert any("LOW_SPAN" in s for s in c.texts)
    assert extrapolated(295.0, c.f_span_n) and not extrapolated(294.0, c.f_span_n)
    assert extrapolated(-295.0, c.f_span_n) and not extrapolated(float("nan"), c.f_span_n)
    assert not extrapolated(1e9, None)
    assert not low_span(0.2 * FS_N) and low_span(0.2 * FS_N - 0.01)


@pytest.mark.req("SW-CAL-006")
@pytest.mark.parametrize("masses, n_err, n_warn", [
    ([1.0, 10.0], 0, 1),          # D-22: 1 kg < 2 % FS → warning only
    ([10.0, 20.0], 0, 0),
    ([10.0, 14.0], 1, 0),         # < 1.5 × m1
    ([10.0, 10.0], 1, 0),         # not increasing
    ([20.0, 10.0], 1, 0),
    ([0.0, 10.0], 1, 0),          # weight mass must be > 0
    ([-1.0], 1, 0),
])
def test_mass_rules(masses, n_err, n_warn) -> None:
    errors, warnings = mass_rules(masses)
    assert (len(errors), len(warnings)) == (n_err, n_warn), (errors, warnings)


@pytest.mark.req("SW-CAL-009")
def test_afe_block_and_match() -> None:
    a = afe_block(0, 1)
    assert a == {"type": "HX711", "channel": "A", "gain": 128, "rate_sps": 80}
    assert afe_block(2, 0)["gain"] == 64 and afe_block(1, 0)["channel"] == "B"
    assert afe_block(None, None)["gain"] == 0
    assert afe_matches(a, afe_block(0, 1)) and not afe_matches(a, afe_block(2, 1))
    assert not afe_matches(a, afe_block(0, 0)) and not afe_matches(None, a)


# ------------------------------------------------------------------------------------------------ TV-RS
@pytest.mark.req("SW-CAL-006", "SW-TARE-002")
def test_tv_rs_robust_stats_se_drift() -> None:
    X = [100.0, 102.0, 98.0, 101.0, 99.0, 100.0, 5000.0, 100.0, 101.0, 99.0]
    r = robust_window_stats(X, k=5.0)
    assert (r.median, r.mad) == (100.0, 1.0)
    assert r.sigma_mad == pytest.approx(1.4826)
    assert (r.n_used, r.n_rejected) == (9, 1) and r.n == 10
    assert r.mean == pytest.approx(100.0) and r.std == pytest.approx(1.224744871391589)
    rho1, n_eff, se = se_ar1([10.0, 12.0, 11.0, 13.0, 12.0, 14.0, 13.0, 15.0])
    assert rho1 == pytest.approx(0.125) and n_eff == pytest.approx(6.222222222222222)
    assert se == pytest.approx(0.6428571428571429)
    t = [k * 0.0125 for k in range(10)]
    x = [1000.0 + 2 * k for k in range(10)]
    assert drift_slope(t, x) == pytest.approx(160.0) and drift_slope(t, x) * 10.0 == pytest.approx(1600.0)
    assert math.sqrt(12 / 800) == pytest.approx(0.1224744871391589)
    assert math.isnan(drift_slope([1.0], [2.0])) and math.isnan(drift_slope([1.0, 1.0], [2.0, 3.0]))
    with pytest.raises(ValueError):
        robust_window_stats([])
    one = robust_window_stats([5.0])
    assert one.n_used == 1 and math.isnan(one.std)
    assert math.isnan(se_ar1([1.0])[2])


def _window(n=800, mean=50_000.0, std=45.0, drift_total=0.0, seed=1, spikes=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / 80.0
    x = mean + rng.normal(0.0, std, n) + drift_total * t / (n / 80.0)
    for i in range(spikes):
        x[(i * 37) % n] += 1e5
    return x, t


@pytest.mark.req("SW-CAL-006")
def test_window_acceptance_rules() -> None:
    x, t = _window()
    ok = window_acceptance(x, t, window_s=10.0, n_nominal=800, std_ref=45.0)
    assert ok.ok and ok.stats.n_used >= 795 and abs(ok.drift) < 20
    assert window_acceptance(x, t, window_s=10.0, n_nominal=800, n_saturated=1).reasons == ("SATURATED",)
    sp, _ = _window(spikes=20)
    assert "OUTLIERS" in window_acceptance(sp, t, window_s=10.0, n_nominal=800).reasons
    assert "TOO_FEW" in window_acceptance(x[:700], t[:700], window_s=10.0, n_nominal=800).reasons
    dr, _ = _window(drift_total=300.0)
    assert "DRIFT" in window_acceptance(dr, t, window_s=10.0, n_nominal=800).reasons
    nz, _ = _window(std=200.0)
    assert "NOISY" in window_acceptance(nz, t, window_s=10.0, n_nominal=800, std_ref=45.0).reasons
    assert "NOISY" not in window_acceptance(nz, t, window_s=10.0, n_nominal=800, std_ref=None).reasons
    em = window_acceptance([], [], window_s=10.0, n_nominal=800)
    assert not em.ok and "NO_SAMPLES" in em.reasons


# ------------------------------------------------------------------------------------------------ TV-T
@pytest.mark.req("SW-TARE-002", "SW-RT-004")
def test_tv_t_force_n() -> None:
    assert force_n(339760.0, K, TARE) == pytest.approx(97.8769839149518, rel=1e-9)
    assert force_n(339760.0, K, TARE) / G0 == pytest.approx(9.980674737545625, rel=1e-9)
    assert force_n(100000.0, K, TARE) == pytest.approx(-11.61298792029685, rel=1e-9)
    assert force_n(TARE, K, TARE) == 0.0
    arr = force_n(np.array([TARE, 339760.0]), K, TARE)
    assert arr[0] == 0.0 and arr[1] == pytest.approx(97.8769839149518)


@pytest.mark.req("SAF-SW-002")
def test_tv_t_fw_raw_limits() -> None:
    assert fw_raw_limits(1500.0, -1500.0, K, TARE) == (-3159254, 3410114)
    assert fw_raw_limits(1500.0, -500.0, K, TARE) == (-969464, 3410114)
    assert fw_raw_limits(1500.0, -500.0, -K, TARE) == (-3159254, 1220324)


@pytest.mark.req("SW-TARE-003")
def test_tare_acceptance_and_offset_warning() -> None:
    x, t = _window(mean=TARE)
    ok = tare_acceptance(x, t, window_s=10.0, n_nominal=800, std_zero_cal=45.0)
    assert ok.ok
    lost = tare_acceptance(x, t, window_s=10.0, n_nominal=800, n_lost=9)
    assert lost.reasons == ("LOST_FRAMES",)
    assert tare_acceptance(x[:400], t[:400], window_s=5.0, n_nominal=800).ok        # no count rule for tare
    assert tare_offset_warning(K, 125000.0 + 0.11 * FS_N / K, 125000.0)
    assert not tare_offset_warning(K, 125000.0 + 0.09 * FS_N / K, 125000.0)
    assert not tare_offset_warning(None, 1.0, 2.0) and not tare_offset_warning(K, 1.0, None)


# ------------------------------------------------------------------------------------------------ TV-TC
@pytest.mark.req("SW-CAL-002")
def test_tv_tc_travel_cal() -> None:
    n1, spm1 = travel_cal_step1(spm_old=640.0, cmd_mm=10.0, meas_mm=10.05)
    assert n1 == 6400 and spm1 == pytest.approx(636.8159203980099, rel=1e-12)
    n2, spm2, inc, cons = travel_cal_step2(spm1=spm1, n1=n1, cmd_mm=50.0, meas_total_mm=60.12, meas1_mm=10.05)
    assert n2 == 31841
    assert spm2 == pytest.approx(636.0778443113772, rel=1e-12)
    assert inc == pytest.approx(635.929698422209, rel=1e-12)
    assert cons == pytest.approx(-0.001391645446374934, rel=1e-9)
    assert spm_from_steps(6400, 10.05) == pytest.approx(spm1)
    assert consistency(31841, 60.12, 10.05, spm1) == pytest.approx((inc, cons))
    with pytest.raises(ValueError):
        travel_cal_step1(640.0, 10.0, 0.0)
    with pytest.raises(ValueError):
        travel_cal_step2(spm1, n1, 50.0, 10.0, 10.05)
    with pytest.raises(ValueError):
        spm_from_steps(1, -1.0)
    with pytest.raises(ValueError):
        consistency(1, 1.0, 2.0, 640.0)


@pytest.mark.req("SW-CAL-003")
def test_travel_plausibility_rules() -> None:
    assert not travel_plausibility(800.0, 800.0, 800.0, d_mm=0.0).ok
    assert not travel_plausibility(99.0, 100.0, None).ok and not travel_plausibility(10_001.0, 9000.0).ok
    assert not travel_plausibility(float("nan"), 800.0).ok
    c = travel_plausibility(160.0, 800.0, 800.0)              # 800 → 160: confirmation, not rejection
    assert c.ok and "SPM_CHANGE_20" in [x[0] for x in c.confirm] and "800 steps/mm" in c.confirm[0][1]
    assert "SPM_EXPECTED" in [x[0] for x in c.confirm]
    c2 = travel_plausibility(800.0, 160.0, 800.0)             # 160 → 800 (DIP change applied)
    assert c2.ok and [x[0] for x in c2.confirm] == ["SPM_CHANGE_20"]
    c3 = travel_plausibility(805.0, 800.0, 800.0)             # no confirmation
    assert c3.ok and not c3.confirm and not c3.warn
    c4 = travel_plausibility(850.0, 800.0, 800.0)             # > 5 %
    assert [x[0] for x in c4.confirm] == ["SPM_CHANGE_5"]
    c5 = travel_plausibility(800.0, 800.0, 800.0, consistency_rel=0.006)
    assert [x[0] for x in c5.warn] == ["REPEAT"]
    assert travel_plausibility(900.0, 0.0).confirm[0][0] == "SPM_CHANGE_20"


@pytest.mark.req("SW-CAL-002")
@pytest.mark.parametrize("spm", [800.0, 636.0778443113772, 160.0, 1234.5678, 4000.0])
def test_target_for_steps_matches_fw_rounding(spm: float) -> None:
    rng = random.Random(int(spm))
    for _ in range(50):
        s_ref = rng.randint(-200_000, 200_000)
        n = rng.choice([round(10 * spm), round(50 * spm), 1600, 7])
        target, n_act = target_for_steps(s_ref, n, spm)
        assert um_to_steps(target, f32(spm)) - s_ref == n_act
        if spm <= 1000:
            assert n_act == n


# ------------------------------------------------------------------------------------------------ limits
@pytest.mark.req("SAF-SW-001")
def test_force_and_travel_limit_rules() -> None:
    assert L.evaluate_force_limit(101.0, 100.0, True, -100.0, True) == "PULL"
    assert L.evaluate_force_limit(101.0, 100.0, False, -100.0, True) is None
    assert L.evaluate_force_limit(-101.0, 100.0, True, -100.0, True) == "PUSH"
    assert L.evaluate_force_limit(100.0, 100.0, True, -100.0, True) is None         # at, not beyond
    assert L.evaluate_force_limit(float("nan"), 1.0, True, -1.0, True) is None
    assert L.saturated_force(K, +1) == math.inf and L.saturated_force(-K, +1) == -math.inf
    assert L.saturated_force(K, -1) == -math.inf
    assert L.force_warning(90.0, 100.0, 90.0, False) and not L.force_warning(89.0, 100.0, 90.0, False)
    assert L.force_warning(88.5, 100.0, 90.0, True) and not L.force_warning(87.9, 100.0, 90.0, True)
    assert L.force_warning(-95.0, -100.0, 90.0, False) and not L.force_warning(95.0, -100.0, 90.0, False)
    assert not L.force_warning(float("nan"), 100.0, 90.0, True) and not L.force_warning(5.0, 0.0, 90.0, False)
    assert L.back_inside(97.9, 100.0) and not L.back_inside(98.5, 100.0) and L.back_inside(-97.0, -100.0)
    assert not L.back_inside(float("nan"), 100.0)
    assert L.evaluate_travel_limit(50.0, 10.0, 50.0) is None
    assert L.evaluate_travel_limit(50.002, 10.0, 50.0, tol_mm=0.00125) == "TRAVEL_MAX"
    assert L.evaluate_travel_limit(50.001, 10.0, 50.0, tol_mm=0.00125) is None
    assert L.evaluate_travel_limit(9.0, 10.0, None) == "TRAVEL_MIN"
    assert L.evaluate_travel_limit(float("nan"), 10.0, 50.0) is None
    assert L.predicted_crossing(49.9, 2.0, None, 50.0) == "TRAVEL_MAX"
    assert L.predicted_crossing(49.8, 2.0, None, 50.0) is None
    assert L.predicted_crossing(10.1, -2.0, 10.0, None) == "TRAVEL_MIN"
    assert L.predicted_crossing(10.1, 0.0, 10.0, None) is None
    assert L.predicted_crossing(10.1, 2.0, 10.0, None) is None


@pytest.mark.req("SAF-SW-006")
def test_limit_margin_warning() -> None:
    # k 500 N/mm, 2 mm/s, 65 ms → 65 N overshoot vs margin
    assert L.limit_margin_warning(500.0, 2.0, 1050.0, 1000.0)                 # 65 N > 50 N margin
    assert not L.limit_margin_warning(500.0, 2.0, 1100.0, 1000.0)             # 65 N < 100 N margin
    assert L.limit_margin_warning(500.0, 2.0, 1050.0, -1000.0)                # push side uses |F_pc|
    assert not L.limit_margin_warning(None, 2.0, 1.0, 1.0) and not L.limit_margin_warning(50.0, 0.0, 1.0, 1.0)


@pytest.mark.req("SW-LIM-002")
def test_check_limit_config() -> None:
    good = dict(pull_trip_n=1961.33, pull_enabled=True, push_trip_n=-1961.33, push_enabled=True, warn_pct=90.0,
                fw_level_n=2157.46)
    assert L.check_limit_config(**good) == []
    assert [c for _f, c, _t in L.check_limit_config(**{**good, "fw_level_n": 2200.0})] == ["FW_LEVEL"]
    assert [c for _f, c, _t in L.check_limit_config(**{**good, "fw_level_n": 1000.0})] == ["FW_BELOW_SW"]
    assert L.check_limit_config(**{**good, "fw_level_n": 1000.0, "pull_enabled": False, "push_enabled": False}) == []
    bad = L.check_limit_config(**{**good, "pull_trip_n": -1.0, "push_trip_n": 1.0, "warn_pct": 10.0})
    assert {f for f, _c, _t in bad} == {"pull_trip_n", "push_trip_n", "warn_pct"}


# ------------------------------------------------------------------------------------------------ TV-D
@pytest.mark.req("SW-RT-004", "SW-REP-004")
def test_tv_d_derived() -> None:
    xs = [0.0, 0.1, 0.2, 0.3, 0.4]
    fs = [0.0, 5.2, 9.8, 15.1, 20.0]
    k, b = D.stiffness_ols(xs, fs)
    assert k == pytest.approx(49.9) and b == pytest.approx(0.04, abs=1e-12)
    assert D.work_trapz(xs, fs) == pytest.approx(4.01)
    assert D.work_cumulative(xs, fs)[-1] == pytest.approx(4.01)
    assert D.bend3p_stress(200.0, 100.0, 20.0, 5.0) == pytest.approx(60.0)
    assert D.bend3p_strain(2.0, 100.0, 5.0) == pytest.approx(0.006)
    assert D.bend3p_modulus(100.0, 20.0, 5.0, 50.0) == pytest.approx(5000.0)
    assert D.work_trapz([1.0], [1.0]) == 0.0
    assert math.isnan(D.stiffness_ols([1.0, 1.0], [1.0, 2.0])[0])


@pytest.mark.req("SW-RT-004")
def test_derived_series() -> None:
    t = np.arange(20) * 0.0125
    x = 2.0 * t                                                   # 2 mm/s
    v = D.speed(t, x)
    assert np.isnan(v[:2]).all() and np.isnan(v[-2:]).all() and v[2:-2] == pytest.approx(2.0)
    f = 50.0 * x                                                  # 100 N/s
    fr = D.force_rate(t, f)
    assert np.isnan(fr[:4]).all() and fr[4:-4] == pytest.approx(100.0)
    kt = D.stiffness_tangent(x, f)
    assert np.isnan(kt[0]) and kt[-1] == pytest.approx(50.0)
    assert np.isnan(D.stiffness_tangent(np.zeros(5), np.ones(5))).all()          # standstill: NaN
    assert np.isnan(D.stiffness_secant(1.0, 0.01))
    assert D.stiffness_secant(np.array([10.0, 1.0]), np.array([0.2, 0.0]))[0] == pytest.approx(50.0)
    assert D.running_peak([1.0, -3.0, 2.0, float("nan")]).tolist() == [1.0, 3.0, 3.0, 3.0]
    assert D.break_index([10.0, 50.0, 100.0, 90.0, 79.0, 0.0]) == 4
    assert D.break_index([10.0, 20.0, float("nan"), 30.0]) is None
    rs = D.rolling_std([1.0, 2.0, 3.0, 4.0], 3)
    assert np.isnan(rs[:2]).all() and rs[2] == pytest.approx(1.0)
    sr = D.sample_rate([0.0, 0.0125, 0.025])
    assert np.isnan(sr[0]) and sr[1] == pytest.approx(80.0)
    assert math.isnan(D.ols_slope([1.0], [1.0])) and math.isnan(D.ols_slope([1.0, float("nan")], [1.0, 2.0]))


@pytest.mark.req("SW-CAL-007")
def test_property_exact_lines_fit_exactly() -> None:
    rng = random.Random(1234)
    for _ in range(200):
        k = rng.choice([-1, 1]) * rng.uniform(1e-5, 1e-3)
        b = rng.uniform(-100, 100)
        masses = sorted({round(rng.uniform(1, 150), 3) for _ in range(rng.randint(2, 5))})
        masses = [0.0] + masses
        raw = [(m * G0 - b) / k for m in masses]
        c = load_calibration(raw, masses)
        assert c.K == pytest.approx(k, rel=1e-9) and c.B == pytest.approx(b, abs=1e-6)
        assert c.status in ("PASS", "UNVERIFIED_LINEARITY") and c.nl_pct_span < 1e-6
