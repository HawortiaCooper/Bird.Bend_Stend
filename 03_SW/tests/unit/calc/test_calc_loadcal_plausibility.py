"""D-50 a (SRS v0.6.5 SW-CAL-007): load-calibration plausibility — weight detection (≥ max(10 × std_zero, 50
counts) from the zero point and from the previous point) and the |K| range 0.5…2 × the nominal |K| of the configured
cell / AFE. Expected values come from the SRS / A-02 numbers (3.0 mV/V, 200 kg, gain 128 → 32 212 counts/kg), not
from the code.

Verifies: SW-CAL-006, SW-CAL-007
"""
from __future__ import annotations

import math

import pytest

from bend_stand.calc import loadcal as L

G0 = 9.80665
K_NOM_128 = 1 / (32_212.25472 / G0)             # A-02: 32 212 counts/kg at gain 128 → N/count


@pytest.mark.req("SW-CAL-006", "SW-CAL-007")
@pytest.mark.parametrize("std, thr", [(45.0, 450.0), (2.0, 50.0), (5.0, 50.0), (5.1, 51.0), (None, 50.0),
                                      (float("nan"), 50.0), (-45.0, 450.0)])
def test_weight_threshold_vectors(std, thr) -> None:
    assert L.weight_threshold_counts(std) == pytest.approx(thr)


@pytest.mark.req("SW-CAL-006", "SW-CAL-007")
@pytest.mark.parametrize("raw, zero, prev, std, ok, where", [
    (50_004.66, 50_000.0, None, 45.0, False, "zero point"),         # F-B-COV-01: 5 kg entered, no weight hung
    (49_994.74, 50_004.66, None, 45.0, False, "zero point"),
    (82_850.0, 50_000.0, None, 45.0, True, ""),                     # 1 kg ≈ 32 212 counts
    (50_450.0, 50_000.0, None, 45.0, True, ""),                     # exactly 10 × std: accepted (not <)
    (50_449.0, 50_000.0, None, 45.0, False, "zero point"),
    (50_060.0, 50_000.0, None, 2.0, True, ""),                      # min 50 counts
    (50_049.0, 50_000.0, None, 2.0, False, "zero point"),
    (82_900.0, 50_000.0, 82_850.0, 45.0, False, "previous point"),  # second weight not hung
    (-272_122.0, 50_000.0, None, 45.0, True, ""),                   # negative K (wiring): legal
    (372_122.0, 50_000.0, 82_850.0, 45.0, True, ""),
])
def test_weight_detected_vectors(raw, zero, prev, std, ok, where) -> None:
    got, text = L.weight_detected(raw, zero, prev, std)
    assert got is ok
    if ok:
        assert text == ""
    else:
        assert text.startswith("weight not detected") and where in text and "re-take" in text


@pytest.mark.req("SW-CAL-007")
def test_nominal_k_from_the_configured_cell_and_afe() -> None:
    assert L.nominal_k(128) == pytest.approx(K_NOM_128, rel=1e-9)
    assert 1 / L.nominal_k(128) == pytest.approx(3284.7358, rel=1e-6)          # counts/N (simulator uses 3285)
    assert L.nominal_k(64) == pytest.approx(2 * K_NOM_128, rel=1e-12)
    assert L.nominal_k(32) == pytest.approx(4 * K_NOM_128, rel=1e-12)
    assert L.nominal_k(128, sens_mv_v=2.0) == pytest.approx(1.5 * K_NOM_128, rel=1e-12)
    for bad in (None, 0, -128, "x", float("nan")):
        assert L.nominal_k(bad) is None
    assert L.nominal_k(128, sens_mv_v=0.0) is None and L.nominal_k(128, fs_n=float("inf")) is None


@pytest.mark.req("SW-CAL-007")
@pytest.mark.parametrize("ratio, status", [(1.0, "OK"), (-1.0, "OK"), (0.5, "OK"), (2.0, "OK"), (-2.0, "OK"),
                                           (0.3, "IMPLAUSIBLE"), (3.0, "IMPLAUSIBLE"), (0.49, "IMPLAUSIBLE"),
                                           (2.01, "IMPLAUSIBLE"), (-16_000.0, "IMPLAUSIBLE")])
def test_k_plausibility_vectors(ratio, status) -> None:
    p = L.k_plausibility(ratio * K_NOM_128, K_NOM_128)
    assert p.status == status and p.ratio == pytest.approx(abs(ratio)) and p.k_nominal == pytest.approx(K_NOM_128)
    assert ("K implausible" in p.text) is (status == "IMPLAUSIBLE")
    d = p.as_dict(confirmed=status == "IMPLAUSIBLE")
    assert d["k_status"] == status and d["confirmed"] is (status == "IMPLAUSIBLE") and d["k_range"] == [0.5, 2.0]
    assert d["nominal_formula"] == "FS_N / (S_mV_V/1000 * gain * 2^24)"


@pytest.mark.req("SW-CAL-007")
def test_k_plausibility_without_nominal_and_with_degenerate_k() -> None:
    p = L.k_plausibility(K_NOM_128, None)
    assert p.status == "NOMINAL_UNKNOWN" and "nominal unknown" in p.text and p.ratio is None
    assert p.as_dict()["k_status"] == "NOMINAL_UNKNOWN" and p.as_dict()["confirmed"] is False
    assert L.k_plausibility(K_NOM_128, float("nan")).status == "NOMINAL_UNKNOWN"
    assert L.k_plausibility(float("inf"), K_NOM_128).status == "IMPLAUSIBLE"
    assert L.k_plausibility(0.0, K_NOM_128).status == "IMPLAUSIBLE"
    assert not math.isnan(L.k_plausibility(0.0, K_NOM_128).ratio)
