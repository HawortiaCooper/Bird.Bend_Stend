"""Level U — M3 calculation vectors on the production ``calc`` package (SW_test_plan §4.2 VV-*, values computed by F with
``oracle/f_ref.py`` at P1; the oracle is re-evaluated here where it implements the rule).

TC-SW-CAL-002-01 (VV-TC), TC-SW-CAL-003-01 (plausibility), TC-SW-CAL-007-01 (VV-LC-01…06), TC-SW-CAL-008-01
(LOW_SPAN / extrapolation), TC-SAF-SW-001-03 (VV-LIM), TC-SAF-SW-006-01 (VV-M), TC-SW-RT-004-01 (VV-D), TC-SW-TARE-002-01
(TV-T force_n).

Verifies: SW-CAL-002, SW-CAL-003, SW-CAL-007, SW-CAL-008, SAF-SW-001, SAF-SW-006, SW-RT-004, SW-TARE-002
"""
from __future__ import annotations

import math

import pytest

from oracle import f_ref

G0 = 9.80665


# ============================================================================================ load calibration

@pytest.mark.req("SW-CAL-007")
@pytest.mark.parametrize("raw, masses, status, k", [
    ((125_000, 339_760, 555_800), (0, 10, 20), "PASS", 4.55274890527817e-4),        # VV-LC-01 (NL 0.09904 %)
    ((125_000, 339_760, 555_825), (0, 10, 20), "WARN", None),                       # VV-LC-02 (0.10097 %)
    ((125_000, 339_760, 561_050), (0, 10, 20), "WARN", None),                       # VV-LC-03 (0.49914 %)
    ((125_000, 339_760, 561_075), (0, 10, 20), "FAIL", None),                       # VV-LC-04 (0.50102 %)
    ((125_000, 339_760, 300_000), (0, 10, 20), "FAIL", None),                       # VV-LC-05 non-monotonic
    ((125_000, 92_788, -197_120), (0, 1, 10), "PASS", -3.0444089159e-4),            # VV-LC-06 negative K
    ((125_000, 450_000), (0, 10), "UNVERIFIED_LINEARITY", None),                    # 2-point (TV-LC)
], ids=["VV-LC-01", "VV-LC-02", "VV-LC-03", "VV-LC-04", "VV-LC-05", "VV-LC-06", "2-point"])
def test_tc_sw_cal_007_01_linearity_vectors(raw, masses, status, k):
    """Production ``calc.loadcal.load_calibration`` = the VV-LC statuses at the 0.1 / 0.5 % span boundaries and K
    (rel 1e-9), and equals F's oracle fit (K, B, NL % span) for every vector."""
    # Verifies: SW-CAL-007
    from bend_stand.calc.loadcal import load_calibration

    r = load_calibration(list(raw), list(masses))
    o = f_ref.load_calibration(list(raw), list(masses))
    assert r.status == status == o.status
    assert r.K == pytest.approx(o.K, rel=1e-12) and r.B == pytest.approx(o.B, rel=1e-9, abs=1e-9)
    assert r.nl_pct_span == pytest.approx(o.nl_pct_span, rel=1e-9, abs=1e-12)
    if k is not None:
        assert r.K == pytest.approx(k, rel=1e-9)


@pytest.mark.req("SW-CAL-007")
def test_tc_sw_cal_007_01_degenerate_refused():
    """All raw means equal (the cell does not respond) → refused (ValueError), never K = ∞ / 0."""
    # Verifies: SW-CAL-007
    from bend_stand.calc.loadcal import load_calibration

    with pytest.raises(ValueError):
        load_calibration([125_000, 125_000, 125_000], [0, 10, 20])


@pytest.mark.req("SW-CAL-008", "SW-CAL-006")
def test_tc_sw_cal_008_01_low_span_extrapolation_and_mass_rules():
    """VV-LC-06 / TC-SW-CAL-008-01: span 98.07 N < 20 % FS (392.27 N) → LOW_SPAN; 294.4 N extrapolated (> 3 × 98.0665
    = 294.1995 N), 294.0 N not. TC-SW-CAL-006-01 mass rules: decreasing masses / m2 = 1.49·m1 refused, m1 < 2 % FS
    only a warning."""
    # Verifies: SW-CAL-008, SW-CAL-006
    from bend_stand.calc.loadcal import extrapolated, low_span, mass_rules

    assert low_span(10 * G0) and not low_span(0.2 * f_ref.FS_N + 0.01)
    assert extrapolated(294.4, 10 * G0) and not extrapolated(294.0, 10 * G0)
    assert mass_rules([10.0, 1.0])[0]                       # decreasing (weights after the zero point)
    assert mass_rules([10.0, 14.9])[0]                      # m2 = 1.49 · m1
    assert not mass_rules([10.0, 15.0])[0]
    errs, warns = mass_rules([1.0, 10.0])
    assert not errs and warns                               # m1 < 2 % FS (4 kg): warning only (D-22)


# ============================================================================================ travel calibration

@pytest.mark.req("SW-CAL-002")
@pytest.mark.parametrize("spm0, d1, dtot, n1, spm1, n2, spm2", [
    (160.0, 2.000, None, 1600, 800.0, None, None),                                    # VV-TC-01
    (160.0, 9.98, 59.88, 1600, 160.3206412825651, 8016, 160.58784235136),            # VV-TC-02
], ids=["VV-TC-01", "VV-TC-02"])
def test_tc_sw_cal_002_01_travel_vectors(spm0, d1, dtot, n1, spm1, n2, spm2):
    """R4 §5 two-step procedure (10 mm, then 50 mm at spm1): production ``calc.travelcal`` = VV-TC-01/02 and F's
    oracle (N1 = round(10·spm0), spm1 = N1 / D1, N2 = round(50·spm1), spm2 = (N1 + N2) / D_tot)."""
    # Verifies: SW-CAL-002
    from bend_stand.calc.travelcal import travel_cal_step1, travel_cal_step2

    a = travel_cal_step1(spm0, 10.0, d1)
    assert a[0] == n1 == f_ref.travel_cal_step1(spm0, 10.0, d1)[0]
    assert a[1] == pytest.approx(spm1, rel=1e-9)
    if dtot is not None:
        b = travel_cal_step2(a[1], a[0], 50.0, dtot, d1)
        o = f_ref.travel_cal_step2(a[1], a[0], 50.0, dtot, d1)
        assert b[0] == n2 == o[0]
        assert b[1] == pytest.approx(spm2, rel=1e-6) and b[1] == pytest.approx(o[1], rel=1e-12)


@pytest.mark.req("SW-CAL-003")
def test_tc_sw_cal_003_01_plausibility():
    """VV-TC-03: result outside 100…10 000 rejected; a change > 20 % or the 160 ↔ 800 DIP case needs a confirmation
    (never a rejection); 5 % rule; a consistency > 0.5 % warns 'repeat'."""
    # Verifies: SW-CAL-003
    from bend_stand.calc.travelcal import consistency, travel_plausibility

    assert travel_plausibility(99.99, 160.0).reject and travel_plausibility(10_000.01, 800.0).reject
    assert travel_plausibility(800.0, 800.0, d_mm=0.0).reject and travel_plausibility(800.0, 800.0, d_mm=-1).reject
    big = travel_plausibility(800.0, 160.0)
    assert big.ok and [c for c, _ in big.confirm] == ["SPM_CHANGE_20"], big
    assert "160" in big.confirm[0][1] and "800" in big.confirm[0][1]
    assert not travel_plausibility(800.0 * 1.049, 800.0).confirm
    assert [c for c, _ in travel_plausibility(800.0 * 1.051, 800.0).confirm] == ["SPM_CHANGE_5"]
    assert not travel_plausibility(800.0, 800.0, consistency_rel=0.0049).warn
    assert [c for c, _ in travel_plausibility(800.0, 800.0, consistency_rel=0.0051).warn] == ["REPEAT"]
    inc, c = consistency(40_000, 59.95, 9.95, 800.0)
    assert inc == pytest.approx(800.0) and abs(c) < 1e-12


# ============================================================================================ limits / margin

@pytest.mark.req("SAF-SW-001")
def test_tc_saf_sw_001_03_force_limit_vectors():
    """VV-LIM: trip 1961.33 N strict > (F = trip → none, trip + 1 mN → PULL); warning on at 90 % = 1765.197 N, off
    below 1725.9704 N (hysteresis 2 % of the trip); saturated sample → ±∞ with sign(K)·sign(rail)."""
    # Verifies: SAF-SW-001
    from bend_stand.calc.limits import evaluate_force_limit, force_warning, saturated_force

    t = 1961.33
    assert evaluate_force_limit(t, t, True, -t, True) is None
    assert evaluate_force_limit(t + 1e-3, t, True, -t, True) == "PULL"
    assert evaluate_force_limit(-t - 1e-3, t, True, -t, True) == "PUSH"
    assert evaluate_force_limit(t + 1, t, False, -t, True) is None
    assert force_warning(1765.197, t, 90.0, False) and not force_warning(1765.19, t, 90.0, False)
    assert force_warning(1725.971, t, 90.0, True) and not force_warning(1725.969, t, 90.0, True)
    assert saturated_force(1 / 3285, +1) == math.inf and saturated_force(-1 / 3285, +1) == -math.inf


@pytest.mark.req("SAF-SW-006")
def test_tc_saf_sw_006_01_margin_rule_boundary():
    """VV-M: FW level 110 % FS, PC trip 100 % FS → margin 196.133 N; warning ⇔ k·v·0.065 s > margin ⇔ k·v > 3017.43 N/s
    (k 50 N/mm at 10 mm/s: none; 500 N/mm: warning)."""
    # Verifies: SAF-SW-006
    from bend_stand.calc.limits import limit_margin_warning

    f_fw, f_pc = 1.10 * f_ref.FS_N, 1.00 * f_ref.FS_N
    assert not limit_margin_warning(50.0, 10.0, f_fw, f_pc)
    assert limit_margin_warning(500.0, 10.0, f_fw, f_pc)
    assert not limit_margin_warning(301.7, 10.0, f_fw, f_pc) and limit_margin_warning(301.8, 10.0, f_fw, f_pc)


# ============================================================================================ derived (VV-D)

@pytest.mark.req("SW-RT-004")
def test_tc_sw_rt_004_01_derived_vectors():
    """VV-D-01/02 speed 10.0 / 9.99000999 mm/s; VV-D-04 tangent stiffness NaN at standstill; VV-D-05 secant 50 N/mm and
    NaN below the minimum travel; VV-D-06 peak 200 and break at the 155 N sample (< 80 % of the peak); VV-D-07 rolling
    std 45.28391448839648; VV-D-09 work (TV-D) 4.01 N·mm."""
    # Verifies: SW-RT-004
    from bend_stand.calc import derived as D

    x = [0.0, 0.125, 0.25, 0.375, 0.5]
    v = D.speed([0, 0.0125, 0.025, 0.0375, 0.05], x)
    assert v[2] == pytest.approx(10.0, rel=1e-9)
    v = D.speed([0, 0.0124, 0.0251, 0.03745, 0.05005], x)
    assert v[2] == pytest.approx(9.99000999000999, rel=1e-9)
    assert math.isnan(D.stiffness_tangent([1.0] * 9, [5.0 + i for i in range(9)])[-1])
    assert D.stiffness_secant(10.0, 0.2) == pytest.approx(50.0) and math.isnan(D.stiffness_secant(10.0, 0.04))
    f = [0, 50, 100, 150, 200, 170, 155]
    assert list(D.running_peak(f))[-1] == 200
    assert D.break_index(f) == 6 and D.break_index(f[:6]) is None
    sd = D.rolling_std([45.0 if i % 2 == 0 else -45.0 for i in range(80)], 80)
    assert sd[-1] == pytest.approx(45.28391448839648, rel=1e-12)


@pytest.mark.req("SW-TARE-002", "SW-RT-004")
def test_tc_sw_tare_002_01_force_and_kgf():
    """TV-T ``force_n`` = K·(raw − tare) equals the oracle; 98.0665 N = 10.000 kgf (VV-D-08)."""
    # Verifies: SW-TARE-002, SW-RT-004
    from bend_stand.calc.tare import force_n

    for raw in (125_000, 450_000, -200_000):
        assert force_n(raw, 1 / 3285, 125_000) == pytest.approx(f_ref.force_n(raw, 1 / 3285, 125_000), rel=1e-12)
    assert 98.0665 / G0 == pytest.approx(10.0, rel=1e-12)
