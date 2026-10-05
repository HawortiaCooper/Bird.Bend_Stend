"""M3 component tests (lock-step backend + simulator): load-calibration wizard (weights on the simulated cell), tare,
calibrated FW thresholds (SAF-SW-002) incl. AFE mismatch and the no-specimen mode, calibration files.

Verifies: SW-CAL-005, SW-CAL-006, SW-CAL-007, SW-CAL-008, SW-CAL-009, SW-TARE-001, SW-TARE-002, SW-TARE-003,
SAF-SW-001, SAF-SW-002, SW-LIM-004, SW-RT-002, SW-RT-004
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from bbs_support import lockstep_backend, tx_frames
from bend_stand.calc.loadcal import fw_raw_limits
from bend_stand.calc.units import G0
from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import GateId
from bend_stand.core.safety import calibrated_target

Cmd = pg.Cmd


def _fast(be, capture: float = 3.0, presettle: float = 0.5, tare: float = 2.0) -> None:
    s = be.session.get()
    assert not be.session.set(replace(s, cal_capture_s=capture, cal_presettle_s=presettle, tare_window_s=tare))


def _wait_phase(be, eng, phase: str, ms: float = 20_000, index: int | None = None) -> None:
    ok = be.test_hooks.run_until(lambda: eng.state().phase == phase and (index is None or
                                                                       eng.state().step_index == index), ms, 5)
    assert ok, eng.state()


def calibrate(be, masses=(1.0, 10.0)) -> object:
    """Run the load wizard against the simulator with weights hung on the cell; returns the FIT state."""
    lc = be.load_cal
    assert lc.start(n_points=len(masses) + 1).ok
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=0)
    be.sim.act("weight", n=0.0)
    lc.continue_()
    for i, m in enumerate(masses, start=1):
        _wait_phase(be, lc, "AWAIT_OPERATOR", index=i)
        be.sim.act("weight", kg=m)
        be.test_hooks.advance(100)
        lc.continue_({"mass_kg": m})
    _wait_phase(be, lc, "FIT")
    return lc.state()


def tare(be) -> object:
    be.sim.act("weight", n=0.0)
    be.test_hooks.advance(1200)
    g = be.tare()
    assert g.ok, g
    _wait_phase(be, be.tare_engine, "DONE", 10_000)
    return be.tare_engine.state().result


@pytest.fixture
def be():
    b = lockstep_backend()
    _fast(b)
    yield b
    b.shutdown()


@pytest.mark.req("SW-CAL-005", "SW-CAL-007", "SW-CAL-008", "SW-CAL-009")
def test_load_wizard_1kg_10kg_low_span_accept_and_file(be) -> None:
    st = calibrate(be)
    fit = st.result
    assert fit.status == "PASS" and fit.low_span and fit.K == pytest.approx(1 / 3285, rel=2e-3)
    tab = fit.point_table()                                              # GRQ-B-25
    assert [r["mass_kg"] for r in tab] == [0.0, 1.0, 10.0] and tab[2]["force_n"] == pytest.approx(10 * G0)
    assert abs(tab[1]["residual_n"]) < 0.5
    assert any("LOW_SPAN" in w for w in st.warnings) and any("2 % FS" in w for w in st.warnings)
    be.load_cal.continue_()
    _wait_phase(be, be.load_cal, "DONE", 2000)
    rec = be.calibrations.active_load()
    assert rec["push_calibrated"] is False and rec["afe"] == {"type": "HX711", "channel": "A", "gain": 128,
                                                             "rate_sps": 80}
    assert rec["fit"]["status"] == "PASS" and rec["low_span"] and len(rec["points"]) == 3
    for p in rec["points"]:                         # N = round(capture × measured rate) ± 1 (SW-CAL-005)
        assert abs(p["n_used"] + p["n_rejected"] - p["n_nominal"]) <= 1
    files = be.calibrations.history("load")
    assert len(files) == 1 and json.loads(Path(files[0]).read_text(encoding="utf-8"))["fit"] == rec["fit"]
    cs = be.status().calibration
    assert cs.load_k == pytest.approx(fit.K) and cs.low_span and cs.f_cal_max_n == pytest.approx(10 * G0)
    assert cs.load_valid_for_limits
    # restart: the active copy is loaded by default (same data folder)
    from bend_stand.core.backend import Backend, BackendSettings  # noqa: PLC0415

    b2 = Backend(BackendSettings(clock="lockstep", test_hooks=True))
    try:
        assert b2.calibrations.active_load()["fit"]["k_n_per_count"] == pytest.approx(fit.K)
        assert b2.status().tare.state == "NONE"                         # tare is session-only (D-29 j)
    finally:
        b2.shutdown()


@pytest.mark.req("SW-CAL-006", "SW-CAL-007")
def test_load_wizard_rejections_retake_finish_early_and_cancel(be) -> None:
    lc = be.load_cal
    assert lc.start().ok
    assert not lc.start().ok                                            # already running
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=0)
    be.sim.set_cell_load(0.0)
    lc.continue_()
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=1)
    lc.continue_({})                                                    # mass missing
    assert lc.state().errors == ("enter the mass of the weight",)
    be.sim.set_cell_load(mass_kg=10.0, swing_n=150.0, swing_tau_s=20.0)  # swinging weight → noisy point
    lc.continue_({"mass_kg": 10.0})
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=1)
    st = lc.state()
    assert st.errors and st.can_repeat and any("std" in e or "drift" in e or "outliers" in e for e in st.errors)
    be.sim.set_cell_load(mass_kg=10.0)
    be.test_hooks.advance(500)
    lc.repeat()
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=2)
    lc.continue_({"mass_kg": 12.0})                                     # < 1.5 × 10 kg
    assert any("1.5" in e for e in lc.state().errors)
    be.sim.act("weight", kg=20.0)
    lc.continue_({"mass_kg": 20.0})
    be.test_hooks.advance(1000)                     # a few samples flagged saturated (a real rail would trip the FW
    be.sim.override_status(int(pg.DataStatus.AFE_SATURATED), 0, 40)     # load limit and abort the wizard)
    _wait_phase(be, lc, "AWAIT_OPERATOR", index=2)
    assert any("saturated" in e for e in lc.state().errors)
    lc.finish_early()                                                   # zero + 1 weight → 2-point fit
    _wait_phase(be, lc, "FIT")
    assert lc.state().result.status == "UNVERIFIED_LINEARITY" and lc.state().can_continue
    lc.retake(1)
    assert lc.state().phase == "AWAIT_OPERATOR" and lc.state().inputs[0].default == 10.0
    be.halt("test")                                                     # any stop aborts the wizard
    assert lc.state().phase == "ABORTED" and be.calibrations.active_load() is None


@pytest.mark.req("SW-CAL-007")
@pytest.mark.parametrize("nonlin, status", [(1.0, "WARN"), (5.0, "FAIL")])
def test_load_wizard_warn_needs_confirmation_fail_refused(be, nonlin, status) -> None:
    be.sim.act("afe", nonlin_pct_fs=nonlin, fs_n=200.0)                  # strong curvature over a 200 N "cell"
    st = calibrate(be, masses=(4.0, 10.0, 20.0)) if status == "FAIL" else calibrate(be, masses=(4.0, 10.0))
    assert st.result.status == status, st.result
    if status == "WARN":
        assert st.needs_confirmation.code == "FIT_WARN"
        be.load_cal.continue_()
        assert be.load_cal.state().phase == "FIT" and be.calibrations.active_load() is None
        be.load_cal.continue_(confirmed=True)
        _wait_phase(be, be.load_cal, "DONE", 2000)
        assert be.calibrations.active_load()["fit"]["status"] == "WARN"
    else:
        assert not st.can_continue and "not linear" in " ".join(st.errors)
        be.load_cal.continue_(confirmed=True)
        assert be.calibrations.active_load() is None
        be.load_cal.cancel()
        assert be.load_cal.state().phase == "ABORTED"


@pytest.mark.req("SAF-SW-001", "SAF-SW-002", "SW-TARE-002", "SW-RT-002")
def test_tare_rewrites_calibrated_thresholds_and_enables_motion(be) -> None:
    h = be.test_hooks
    assert "LOAD_INPUT_INVALID" in be.status().gates[GateId.MOVE].codes()
    assert not be.channels.get("F_N").available
    calibrate(be)
    be.load_cal.continue_()
    _wait_phase(be, be.load_cal, "DONE", 2000)
    assert "no tare" in be.status().safety.load_input_reason
    n_set = len(tx_frames(be, Cmd.SET_PARAM))
    tr = tare(be)
    assert tr.tare_raw == pytest.approx(50_000, abs=30) and be.status().tare.state == "ACTIVE"
    assert h.run_until(lambda: be.device.thresholds.state == "VERIFIED" and
                       be.device.thresholds.tare_id == tr.tare_id, 3000)
    k = be.load_input.cal.k
    t = calibrated_target(k, tr.tare_raw, be.limits.get().fw_level_n)
    p = be.sim.board.params
    assert (p["safety.load_raw_min"], p["safety.load_raw_max"], p["safety.zero_raw"]) == (t.raw_min, t.raw_max,
                                                                                          t.zero_raw)
    assert len(tx_frames(be, Cmd.SET_PARAM)) > n_set and tx_frames(be, Cmd.GET_PARAM)
    g = be.status().gates[GateId.MOVE]
    assert "LOAD_INPUT_INVALID" not in g.codes() and "THRESHOLDS_UNVERIFIED" not in g.codes()
    assert be.channels.get("F_N").available and be.status().safety.load_input_valid
    be.sim.act("weight", kg=10.0)
    h.advance(500)
    assert be.data.latest("F_N").value == pytest.approx(98.0665, abs=1.0)
    assert be.data.latest("F_kgf").value == pytest.approx(10.0, abs=0.1)
    # undo → no tare → defaults again, motion refused
    assert be.tare_engine.undo().ok and be.status().tare.state == "NONE"
    assert h.run_until(lambda: be.device.thresholds.state == "DEFAULT_ONLY", 3000)
    assert "LOAD_INPUT_INVALID" in be.status().gates[GateId.MOVE].codes()
    assert not be.tare_engine.undo().ok                                 # one level only


@pytest.mark.req("SW-CAL-009", "SAF-SW-001", "SAF-SW-002", "SW-LIM-004")
def test_afe_mismatch_invalidates_and_no_specimen_keeps_calibrated_thresholds(be) -> None:
    h = be.test_hooks
    calibrate(be)
    be.load_cal.continue_()
    _wait_phase(be, be.load_cal, "DONE", 2000)
    tr = tare(be)
    assert h.run_until(lambda: be.device.thresholds.tare_id == tr.tare_id, 3000)
    # no-specimen mode with a valid calibration + tare: calibrated thresholds stay (SW-LIM-004)
    g = be.limits.set_no_specimen_mode(True)
    assert not g.ok and "NO_SPECIMEN_CONFIRM" in g.codes() and not be.status().safety.no_specimen_mode
    assert be.limits.set_no_specimen_mode(True, confirmed=True).ok
    h.advance(300)
    assert be.device.thresholds.state == "VERIFIED" and be.device.thresholds.tare_id == tr.tare_id
    assert be.status().indicators.no_specimen_mode.state == "ON"
    assert "NO_SPECIMEN_MODE" in be.status().gates[GateId.MOVE].codes()
    assert be.limits.set_no_specimen_mode(False).ok
    # gain changed on the board → calibration invalid for the limits, defaults written, motion refused
    assert h.result(be.config.write_and_verify_async({"afe.gain_channel": "A64"})).ok
    h.advance(300)
    cs = be.status().calibration
    assert not cs.load_valid_for_limits and "AFE" in cs.load_invalid_reason
    assert h.run_until(lambda: be.device.thresholds.state == "DEFAULT_ONLY", 3000)
    g = be.status().gates[GateId.MOVE]
    assert "LOAD_INPUT_INVALID" in g.codes() and "AFE" in g.text()
    lt = be.data.latest("F_N")
    assert lt.state == "INVALID"                                         # displayed, marked invalid
    h.result(be.config.write_and_verify_async({"afe.gain_channel": "A128"}))
    assert h.run_until(lambda: be.device.thresholds.state == "VERIFIED", 3000)


@pytest.mark.req("SW-TARE-003")
def test_tare_refusals() -> None:
    be = lockstep_backend(no_specimen=True)
    try:
        h = be.test_hooks
        _fast(be)
        assert not be.tare(window_s=1.0).ok                              # 2…60 s
        assert be.tare().ok
        h.advance(500)
        be.sim.override_status(int(pg.DataStatus.AFE_SATURATED), 0, 40)  # flagged samples (a rail would trip the FW)
        _wait_phase(be, be.tare_engine, "REFUSED", 5000)
        assert any("saturated" in e for e in be.tare_engine.state().errors)
        be.sim.set_cell_load(0.0, swing_n=80.0, swing_tau_s=30.0)
        h.advance(200)
        be.tare()
        _wait_phase(be, be.tare_engine, "REFUSED", 5000)
        be.sim.set_cell_load(0.0)
        be.tare()
        h.advance(500)
        be.sim.act("inject", fault="tx_congestion", duration_ms=300)      # frames lost inside the window
        _wait_phase(be, be.tare_engine, "REFUSED", 5000)
        assert any("lost frames" in e for e in be.tare_engine.state().errors)
        be.halt("test")
        h.advance(100)
        g = be.tare()
        assert not g.ok and "HALT" in g.codes()
        h.result(be.clear_stop_async())
        h.advance(200)
        assert be.motion.enable().ok
        h.advance(700)
        be.motion.jog_start(1, 2.0)
        h.advance(100)
        g = be.tare()
        assert not g.ok and "MOTION_ACTIVE" in g.codes()
        be.motion.jog_stop()
        assert h.run_until(lambda: not be.status().motion.moving, 2000)
        g = be.tare()
        assert not g.ok and "MOVED_RECENTLY" in g.codes()
        h.advance(1200)
        assert be.tare().ok
        be.tare_engine.cancel()
        assert be.tare_engine.state().phase == "ABORTED"
    finally:
        be.shutdown()


@pytest.mark.req("SW-TARE-002")
def test_tare_dropped_for_another_board_uid(be) -> None:
    tr = tare(be)
    assert be.load_input.tare_for(be.device.info.uid) is tr
    assert be.load_input.tare_for("OTHER") is None


@pytest.mark.req("SW-RT-004")
def test_derived_channels_in_the_pipeline(be) -> None:
    h = be.test_hooks
    calibrate(be)
    be.load_cal.continue_()
    _wait_phase(be, be.load_cal, "DONE", 2000)
    tare(be)
    assert not be.session.set(replace(be.session.get(), bend3p=__import__(
        "bend_stand.core.model", fromlist=["Bend3pGeometry"]).Bend3pGeometry(100.0, 20.0, 5.0)))
    assert be.channels.get("sigma_mpa").available
    be.sim.act("weight", n=200.0)
    h.advance(1500)
    assert be.data.latest("sigma_mpa").value == pytest.approx(60.0, rel=0.02)          # 3FL/(2bh²)
    assert be.data.latest("noise_counts").value == pytest.approx(45.0, rel=0.4)
    assert be.data.latest("peak_n").value >= 199.0
    be.sim.act("weight", n=400.0)                                                     # > 3 × 98 N → extrapolated
    h.advance(300)
    assert be.data.latest("F_N").state == "EXTRAPOLATED"
    snap = be.data.snapshot(["F_N", "speed_mm_s"], 5.0, 100)
    assert 1 in set(snap.series["F_N"].vstate.tolist())


@pytest.mark.req("SW-TARE-002", "SW-CAL-005")
def test_tare_and_load_wizard_start_the_stream_and_restore_it(be) -> None:
    h = be.test_hooks
    h.result(be.stream_stop_async())
    h.advance(200)
    assert not be.device.stream_on
    assert be.tare(window_s=2.0).ok
    _wait_phase(be, be.tare_engine, "DONE", 10_000)
    h.advance(300)
    assert not be.device.stream_on                                       # restored
    assert be.load_cal.start(n_points=2).ok
    h.advance(300)
    assert be.device.stream_on
    be.load_cal.cancel()
    h.advance(300)
    assert be.load_cal.state().phase == "ABORTED" and not be.device.stream_on
    assert be.status().tare.state == "ACTIVE" and be.status().tare.can_undo
