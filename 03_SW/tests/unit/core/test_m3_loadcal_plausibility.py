"""D-50 a (SRS v0.6.5 SW-CAL-007) in the load-calibration wizard on the simulator (cell ≈ 3285 counts/N, nominal
3284.7): the F-B-COV-01 reproducer (5 kg entered, no weight hung) is now refused "weight not detected"; a fitted |K|
at 0.3 × / 3 × nominal (wrong mass entered) needs the K_IMPLAUSIBLE confirmation, which is recorded in the file;
without a derivable nominal the file records "nominal unknown"; a re-taken zero point is re-checked before the fit.

Verifies: SW-CAL-006, SW-CAL-007, SW-CAL-009
"""
from __future__ import annotations

import pytest
from bbs_support import lockstep_backend

from .test_m3_calibration import _fast, _wait_phase


@pytest.fixture
def be():
    b = lockstep_backend()
    _fast(b)
    yield b
    b.shutdown()


def _zero(be) -> None:
    lc = be.load_cal
    assert lc.start(n_points=2).ok
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=0)
    be.sim.act("weight", n=0.0)
    be.test_hooks.advance(200)
    lc.continue_()
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=1)


def _point(be, hung_kg: float, entered_kg: float, phase: str = "FIT", index: int | None = None) -> None:
    be.sim.act("weight", kg=hung_kg)
    be.test_hooks.advance(300)
    be.load_cal.continue_({"mass_kg": entered_kg})
    _wait_phase(be, be.load_cal, phase, index=index)


@pytest.mark.req("SW-CAL-006", "SW-CAL-007")
def test_two_point_calibration_without_weight_is_refused(be) -> None:
    """F-B-COV-01 reproducer: before D-50 a this gave K = −4.9 N/count (UNVERIFIED_LINEARITY, acceptable)."""
    lc = be.load_cal
    _zero(be)
    _point(be, 0.0, 5.0, "AWAIT_OPERATOR", index=1)                              # 5 kg entered, nothing hung
    st = lc.state()
    assert st.phase == "AWAIT_OPERATOR" and st.step_index == 1 and st.can_repeat
    assert st.errors and st.errors[0].startswith("weight not detected"), st.errors
    assert lc.state().result is None and be.calibrations.active_load() is None
    lc.finish_early()                                                            # only the zero point exists
    assert "finish early needs" in lc.state().errors[0]
    _point(be, 5.0, 5.0)                                                         # re-take with the weight hung
    st = lc.state()
    assert st.result.status == "UNVERIFIED_LINEARITY" and st.needs_confirmation is None and st.can_continue
    lc.continue_()
    _wait_phase(be, lc, "DONE", 2000)
    rec = be.calibrations.active_load()
    assert rec["plausibility"]["k_status"] == "OK" and rec["plausibility"]["confirmed"] is False
    assert rec["plausibility"]["k_ratio"] == pytest.approx(1.0, abs=0.01) and rec["confirmations"] == []


@pytest.mark.req("SW-CAL-007", "SW-CAL-009")
@pytest.mark.parametrize("hung, entered, ratio", [(10.0, 3.0, 0.3), (3.0, 9.0, 3.0)])
def test_k_outside_half_to_double_nominal_needs_a_recorded_confirmation(be, hung, entered, ratio) -> None:
    lc = be.load_cal
    _zero(be)
    _point(be, hung, entered)
    st = lc.state()
    assert st.needs_confirmation.code == "K_IMPLAUSIBLE" and "K implausible" in st.needs_confirmation.text
    assert any("K implausible" in w for w in st.warnings) and st.can_continue
    lc.continue_()                                                               # not confirmed: refused
    assert lc.state().phase == "FIT" and lc.state().errors == ("K implausible: confirm to accept",)
    assert be.calibrations.active_load() is None
    lc.continue_(confirmed=True)
    _wait_phase(be, lc, "DONE", 2000)
    rec = be.calibrations.active_load()
    p = rec["plausibility"]
    assert p["k_status"] == "IMPLAUSIBLE" and p["confirmed"] is True and rec["confirmations"] == ["K_IMPLAUSIBLE"]
    assert p["k_ratio"] == pytest.approx(ratio, rel=0.02) and p["k_nominal_n_per_count"] == pytest.approx(
        9.80665 / 32_212.25472, rel=1e-6)


@pytest.mark.req("SW-CAL-007")
def test_nominal_unknown_is_recorded_and_not_checked(be, monkeypatch) -> None:
    import bend_stand.core.calibration.load as LM

    monkeypatch.setattr(LM, "nominal_k", lambda _gain: None)
    lc = be.load_cal
    _zero(be)
    _point(be, 3.0, 9.0)                                                         # 3 × nominal, but no nominal
    st = lc.state()
    assert st.needs_confirmation is None and any("nominal unknown" in w for w in st.warnings)
    lc.continue_()
    _wait_phase(be, lc, "DONE", 2000)
    p = be.calibrations.active_load()["plausibility"]
    assert p["k_status"] == "NOMINAL_UNKNOWN" and p["k_nominal_n_per_count"] is None and "nominal unknown" in p["text"]


@pytest.mark.req("SW-CAL-006", "SW-CAL-007")
def test_retaken_zero_point_rechecks_the_weights_before_the_fit(be) -> None:
    lc = be.load_cal
    _zero(be)
    _point(be, 5.0, 5.0)
    lc.retake(0)                                                                 # weight still hung: "zero" = weight
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=0)
    lc.continue_()
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=1)
    st = lc.state()
    assert st.errors[0].startswith("weight not detected") and st.result is None
    assert "zero point" in st.errors[0]
    lc.cancel()
    assert lc.state().phase == "ABORTED" and be.calibrations.active_load() is None
