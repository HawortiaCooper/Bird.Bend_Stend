"""M3 safety: the per-frame SW-limit supervisor (SAF-SW-001; pure tests with synthetic DATA rows + the lock-step
latency test with 100 injected violations), the ThresholdManager calibrated path and identity check (SAF-SW-002,
MC2-3), the no-specimen mode (SW-LIM-004), the limit rules (SW-LIM-001/002), the speed-vs-margin warning (SAF-SW-006)
and the direction-aware trip latch in the motion gates.

Verifies: SAF-SW-001, SAF-SW-002, SAF-SW-004, SAF-SW-006, SW-LIM-001, SW-LIM-002, SW-LIM-003, SW-LIM-004
"""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

from bbs_support import lockstep_backend
from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import GateId, LimitConfig, MotionKind, ThresholdState
from bend_stand.core.pipeline import DataRow
from bend_stand.core.safety import SafetyInputs, SafetySupervisor, ThresholdManager, ThresholdTarget, calibrated_target
from bend_stand.core.scaling import DERIVED_KEYS

DF, DS = pg.DataFlags, pg.DataStatus
MOVING_HOMED = int(DF.MOVING | DF.HOMED | DF.ENABLED)
K = 1 / 3285.0


def row(t_ms: float, x_mm: float, f: float = float("nan"), *, flags: int = MOVING_HOMED, status: int = 0,
        raw: int = 50_000, raw_state: int = 0, epoch: int = 0) -> DataRow:
    der = (f,) + (float("nan"),) * (len(DERIVED_KEYS) - 1)
    t = int(t_ms * 1000)
    return DataRow(t * 1000, t & 0xFFFFFFFF, t, t / 1e6, 0, 0, flags, status, raw, raw_state, int(round(x_mm * 1000)),
                   0, epoch, der, 0)


class Rig:
    def __init__(self, cfg: LimitConfig | None = None, inp: SafetyInputs | None = None) -> None:
        self.cfg = cfg or LimitConfig()
        self.inp = inp or SafetyInputs(load_valid=True, load_reason=None, k=K)
        self.stops: list[str] = []
        self.terms: list[str] = []
        self.pub: list[tuple[str, object]] = []
        self.rows: list[tuple[str, str]] = []
        self.sup = SafetySupervisor(stop=self.stops.append, terminate=self.terms.append,
                                    publish=lambda t, p: self.pub.append((t, p)),
                                    event_row=lambda n, t: self.rows.append((n, t)), limits=lambda: self.cfg,
                                    inputs=lambda: self.inp)

    def topics(self, name: str) -> list:
        return [p for t, p in self.pub if t == name]


@pytest.mark.req("SAF-SW-001")
def test_load_trip_order_latch_release_and_restop() -> None:
    r = Rig(replace(LimitConfig(), pull_trip_n=100.0, push_trip_n=-100.0, fw_level_n=200.0))
    r.sup.process(row(0, 10.0, 50.0))
    assert not r.stops and r.sup.trip is None
    r.sup.process(row(12.5, 10.01, 101.0))
    assert r.stops == ["SW_LIMIT:PULL"] and r.terms == ["SW_LIMIT:PULL"]
    tr = r.sup.trip
    assert tr.limit == "PULL" and tr.value == 101.0 and tr.threshold == 100.0 and r.rows[0][0] == "SW_TRIP"
    assert r.topics("safety.trip")[0] is tr
    assert r.sup.direction_refused(+1) is tr and r.sup.direction_refused(-1) is None
    assert r.sup.direction_refused(+1, pull_dir=-1) is None and r.sup.direction_refused(0) is None
    r.sup.process(row(25, 10.02, 102.0))                                 # still moving toward: re-stop rate-limited
    assert len(r.stops) == 1
    r.sup.process(row(140, 10.03, 103.0))
    assert len(r.stops) == 2
    r.sup.process(row(150, 10.0, 99.0, flags=int(DF.HOMED)))             # inside, but not by the 2 % band
    assert r.sup.trip is not None
    r.sup.process(row(160, 10.0, 97.0, flags=int(DF.HOMED)))
    assert r.sup.trip is None and r.rows[-1] == ("SW_TRIP_CLEARED", "PULL") and r.topics("safety.trip")[-1] is None
    r.sup.process(row(170, 10.0, -100.5, flags=int(DF.HOMED)))           # push side trips also at standstill
    assert r.sup.trip.limit == "PUSH" and r.sup.direction_refused(-1)
    r.cfg = replace(r.cfg, push_enabled=False)                           # disabling the limit releases the latch
    r.sup.process(row(180, 10.0, -101.0, flags=int(DF.HOMED)))
    assert r.sup.trip is None


@pytest.mark.req("SAF-SW-001", "SW-CAL-008")
def test_warnings_hysteresis_and_saturated_counts_as_overload() -> None:
    r = Rig(replace(LimitConfig(), pull_trip_n=100.0, push_trip_n=-100.0, fw_level_n=200.0, warn_pct=90.0))
    r.sup.process(row(0, 1.0, 91.0, flags=int(DF.HOMED)))
    assert r.sup.active_warnings() == ("PULL_WARN",) and r.topics("safety.warning")[0].active
    r.sup.process(row(10, 1.0, 88.5, flags=int(DF.HOMED)))
    assert r.sup.active_warnings() == ("PULL_WARN",)
    r.sup.process(row(20, 1.0, 87.0, flags=int(DF.HOMED)))
    assert not r.sup.active_warnings() and not r.topics("safety.warning")[-1].active
    r.sup.process(row(30, 1.0, float("nan"), raw=pg.RAW_MAX, raw_state=1, flags=int(DF.HOMED)))
    assert r.sup.trip.limit == "PULL" and math.isinf(r.sup.trip.value)
    r2 = Rig(replace(LimitConfig(), pull_trip_n=100.0, push_trip_n=-100.0, fw_level_n=200.0),
             SafetyInputs(load_valid=True, load_reason=None, k=-K))
    r2.sup.process(row(0, 1.0, float("nan"), raw=pg.RAW_MAX, raw_state=1, flags=int(DF.HOMED)))
    assert r2.sup.trip.limit == "PUSH"                                   # negative K: the rail is a push overload


@pytest.mark.req("SAF-SW-001", "SW-LIM-004")
def test_invalid_input_stops_motion_once_and_no_specimen_disables_load_trips() -> None:
    r = Rig(inp=SafetyInputs(load_valid=False, load_reason="no load calibration"))
    r.sup.process(row(0, 1.0, flags=int(DF.HOMED)))
    assert not r.stops                                                   # standstill: no stop
    r.sup.process(row(10, 1.0))
    r.sup.process(row(20, 1.01))
    assert r.stops == ["SW_LIMIT:LOAD_INPUT_INVALID"] and r.terms == ["LOAD_INPUT_INVALID"]
    assert r.topics("safety.trip")[0].limit == "LOAD_INPUT_INVALID" and r.sup.trip is None
    r.sup.process(row(30, 1.01, flags=int(DF.HOMED)))
    r.sup.process(row(40, 1.02))
    assert len(r.stops) == 2                                            # a new motion → stopped again
    r3 = Rig(inp=SafetyInputs(load_valid=True, load_reason=None, k=K))
    r3.sup.process(row(0, 1.0, status=int(DS.AFE_STALE)))                # AFE stale while moving → invalid input
    assert r3.stops == ["SW_LIMIT:LOAD_INPUT_INVALID"]
    r4 = Rig(inp=SafetyInputs(load_valid=True, load_reason=None, k=K))
    r4.sup.process(row(0, 1.0, raw_state=2))                             # no AFE data while moving
    assert r4.stops
    r5 = Rig(replace(LimitConfig(), pull_trip_n=100.0, fw_level_n=200.0),
             SafetyInputs(no_specimen=True, load_valid=False, load_reason="x"))
    r5.sup.process(row(0, 1.0, 500.0))
    r5.sup.process(row(10, 1.1, 500.0))
    assert not r5.stops and r5.sup.trip is None and not r5.sup.active_warnings()


@pytest.mark.req("SAF-SW-001", "SW-LIM-001")
def test_travel_rules_planned_bound_one_step_and_predictive() -> None:
    cfg = replace(LimitConfig(), travel_min_mm=10.0, travel_min_enabled=True, travel_max_mm=50.0,
                  travel_max_enabled=True, pull_enabled=False, push_enabled=False)
    planned = SafetyInputs(load_valid=True, load_reason=None, k=K, spm=800.0, end_um=50_000)
    r = Rig(cfg, planned)
    for i, x in enumerate((49.9, 49.95, 50.0, 50.0)):                   # decelerating onto the bound = the limit
        r.sup.process(row(i * 12.5, x))
    assert not r.stops
    r.sup.process(row(60, 50.002))                                       # beyond by more than one step (1.25 µm)
    assert r.stops == ["SW_LIMIT:TRAVEL_MAX"] and r.sup.trip.unit == "mm"
    free = Rig(cfg, replace(planned, end_um=None))                       # unbounded (JOG without bound, homing)
    free.sup.process(row(0, 49.80))
    free.sup.process(row(12.5, 49.825))                                  # 2 mm/s: 49.825 + 0.15 = 49.975 < 50
    assert not free.stops
    free2 = Rig(cfg, replace(planned, end_um=None))
    free2.sup.process(row(0, 49.80))
    free2.sup.process(row(12.5, 49.90))                                  # 8 mm/s: + 0.6 → crosses → predicted trip
    assert free2.stops == ["SW_LIMIT:TRAVEL_MAX"] and "predicted" in free2.sup.trip.text
    back = Rig(cfg, replace(planned, end_um=20_000))                    # outside the min (limit edited), moving in
    back.sup.process(row(0, 5.0))
    back.sup.process(row(12.5, 5.05))
    assert not back.stops
    out = Rig(cfg, replace(planned, end_um=None))
    out.sup.process(row(0, 10.05))
    out.sup.process(row(12.5, 9.9))
    assert out.stops == ["SW_LIMIT:TRAVEL_MIN"] and out.sup.direction_refused(-1)
    out.sup.process(row(25, 10.5, flags=int(DF.HOMED)))                  # back inside → released
    assert out.sup.trip is None
    nohome = Rig(cfg, planned)
    nohome.sup.process(row(0, 60.0, flags=int(DF.MOVING)))              # not homed: no travel rule
    nohome.sup.process(row(10, 61.0, flags=int(DF.MOVING)))
    assert not nohome.stops
    nolim = Rig(replace(LimitConfig(), pull_enabled=False, push_enabled=False), planned)
    nolim.sup.process(row(0, 60.0))
    nolim.sup.process(row(10, 61.0))
    assert not nolim.stops
    nolim.sup.reset()
    assert nolim.sup.trip is None and nolim.sup.trips == 0


@pytest.mark.req("SAF-SW-002")
def test_threshold_manager_targets_identity_and_provider_failure() -> None:
    class D:
        thresholds = ThresholdState()
        params: dict = {}
    tm = ThresholdManager(D())                                          # type: ignore[arg-type]
    assert tm.current_target().mode == "DEFAULT" and tm.needs_apply()
    t = calibrated_target(K, 50_000, 2157.46, cal_id="c", tare_id="t")
    tm.provider = lambda: t
    assert tm.current_target() is t
    good = ThresholdState("VERIFIED", "c", "t", 2157.46, t.raw_min, t.raw_max, t.zero_raw)
    assert tm.matches(good) and not tm.matches(replace(good, tare_id="t2"))
    assert not tm.matches(replace(good, state="DEFAULT_ONLY"))
    assert not tm.set_manual(-10, 10, 0) and tm.current_target().cal_id == "manual-raw"
    assert tm.set_default().mode == "CALIBRATED" and tm.manual is None
    tm.provider = lambda: 1 / 0                                         # never widen on an error: defaults
    assert tm.current_target() == ThresholdTarget()
    tm.provider = lambda: ThresholdTarget("CALIBRATED", invalid="bad")
    assert not tm.matches(good)
    assert tm.set_calibrated(K, 0.0, 100.0).mode == "CALIBRATED"
    with pytest.raises(ValueError):
        calibrated_target(K, 0.0, 3000.0)


@pytest.mark.req("SAF-SW-002", "SAF-SW-001")
def test_calibrated_thresholds_clamped_warning_on_the_wire() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        rec = {"created_utc": "2026-10-05T00:00:00Z", "afe": {"type": "HX711", "channel": "A", "gain": 128,
                                                              "rate_sps": 80},
               "points": [{"mass_kg": 0.0, "force_n": 0.0, "raw_mean": 50_000.0, "raw_std": 45.0},
                          {"mass_kg": 20.0, "force_n": 196.133, "raw_mean": 50_000.0 + 1.1 * 3285 * 196.133,
                           "raw_std": 45.0}],
               "fit": {"k_n_per_count": 1 / (1.1 * 3285), "status": "UNVERIFIED_LINEARITY"}}
        be.activate_load_calibration(rec)          # K below nominal → the 110 % FS level clamps (D-29 g)
        be.tare()
        assert h.run_until(lambda: be.tare_engine.state().phase == "DONE", 15_000)
        assert h.run_until(lambda: be.device.thresholds.state == "VERIFIED", 3000)
        th = be.device.thresholds
        assert th.clamped and th.raw_max == 7_151_121 and th.eff_pull_n < be.limits.get().fw_level_n
        assert "FW_CLAMPED" in be.status().safety.warnings and be.status().indicators.thresholds_state.value == 1.0
        assert any(e.payload.code == "FW_CLAMPED" for e in be.events.history("safety.warning"))
    finally:
        be.shutdown()


@pytest.mark.req("SAF-SW-001")
def test_stop_on_the_wire_within_50_ms_for_100_injected_violations() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        rec = {"created_utc": "2026-10-05T00:00:00Z", "afe": {"type": "HX711", "channel": "A", "gain": 128,
                                                              "rate_sps": 80},
               "points": [{"mass_kg": 0.0, "force_n": 0.0, "raw_mean": 50_000.0, "raw_std": 45.0},
                          {"mass_kg": 10.0, "force_n": 98.0665, "raw_mean": 50_000.0 + 3285 * 98.0665,
                           "raw_std": 45.0}],
               "fit": {"k_n_per_count": 1 / 3285, "status": "UNVERIFIED_LINEARITY"}}
        be.activate_load_calibration(rec)
        assert be.tare(window_s=2.0).ok
        assert h.run_until(lambda: be.tare_engine.state().phase == "DONE", 15_000)
        assert not be.limits.set(replace(be.limits.get(), pull_trip_n=50.0, push_trip_n=-50.0))
        h.advance(300)
        lat = []
        for _ in range(100):
            n_stop = len([w for w in h.wire_log() if w.direction == "TX" and w.frame[2] == int(pg.Cmd.STOP)])
            be.sim.act("afe", raw_script=[50_000 + 3285 * 80])               # one violating sample (80 N > 50 N)
            h.advance(60)
            stops = [w for w in h.wire_log() if w.direction == "TX" and w.frame[2] == int(pg.Cmd.STOP)]
            assert len(stops) == n_stop + 1
            rx = [r for r in h.rx_log() if r.type == pg.AsyncType.DATA and r.t_host_ns <= stops[-1].t_ns]
            lat.append((stops[-1].t_ns - rx[-1].t_host_ns) / 1e6)
            h.advance(40)                                                    # back inside → latch released
            assert be.safety.trip is None
        lat.sort()
        assert lat[94] <= 50.0 and lat[-1] <= 50.0
        assert len(be.events.history("safety.trip")) >= 100
    finally:
        be.shutdown()


@pytest.mark.req("SW-LIM-001", "SW-LIM-002", "SW-LIM-003", "SAF-SW-006")
def test_limits_rules_session_storage_and_margin_warning() -> None:
    be = lockstep_backend(no_specimen=True)
    try:
        lim = be.limits
        codes = {i.code for i in lim.set(replace(lim.get(), fw_level_n=2200.0))}
        assert "FW_LEVEL" in codes
        assert "FW_BELOW_SW" in {i.code for i in lim.set(replace(lim.get(), fw_level_n=1000.0))}
        assert {i.code for i in lim.set(replace(lim.get(), pull_enabled=False))} == {"LOAD_INPUT_INVALID"}
        assert not lim.set(replace(lim.get(), pull_trip_n=500.0, push_trip_n=-500.0, fw_level_n=600.0))
        assert be.session.get().limits.fw_level_n == 600.0
        saved = be.session.path.read_text(encoding="utf-8")
        assert '"fw_level_n": 600.0' in saved                         # auto-saved session (SW-LIM-003)
        assert not be.session.set(replace(be.session.get(), k_est_n_mm=2000.0))
        g = be.motion.check(MotionKind.MOVE, speed_mm_s=5.0)
        assert "SAF_SW_006_MARGIN" not in g.codes()                     # no-specimen: no PC load limits
        assert be.limits.set_no_specimen_mode(False).ok
        g = be.motion.check(MotionKind.MOVE, speed_mm_s=5.0)              # 2000·5·0.065 = 650 N > 100 N margin
        assert "SAF_SW_006_MARGIN" in g.codes()
        assert be.session.set(replace(be.session.get(), tare_window_s=100.0))
    finally:
        be.shutdown()


@pytest.mark.req("SW-LIM-004", "SAF-SW-004")
def test_no_specimen_mode_api_ends_at_disconnect() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        g = be.status().gates[GateId.NO_SPECIMEN]
        assert g.needs_confirmation and "No specimen is mounted" in g.confirm_items[0].text
        r = be.limits.set_no_specimen_mode(True)
        assert "CONFIRMATION_REQUIRED" in r.codes() and not be.status().safety.no_specimen_mode
        assert be.limits.set_no_specimen_mode(True, confirmed=True).ok
        assert be.limits.set_no_specimen_mode(True).ok                  # already on
        assert be.events.history("safety.no_specimen")[-1].payload is True
        g = be.status().gates[GateId.MOVE]
        assert "LOAD_INPUT_INVALID" not in g.codes() and "NO_SPECIMEN_MODE" in g.codes()
        assert be.device.thresholds.state == "DEFAULT_ONLY"             # no calibration: nominal defaults
        h.result(be.disconnect_async())
        assert not be.status().safety.no_specimen_mode
        assert '"no_specimen' not in be.session.path.read_text(encoding="utf-8") if be.session.path.exists() \
            else True
    finally:
        be.shutdown()


@pytest.mark.req("SAF-SW-001")
def test_trip_latch_refuses_motion_that_increases_the_violation() -> None:
    from core.test_motion_m2 import _ready                              # noqa: PLC0415

    be = _ready()
    try:
        h = be.test_hooks
        assert not be.limits.set(replace(be.limits.get(), travel_min_mm=1.0, travel_min_enabled=True,
                                         travel_max_mm=30.0, travel_max_enabled=True))
        h.result(be.motion.move_to(20.0), 20_000)
        h.advance(100)
        assert not be.limits.set(replace(be.limits.get(), travel_max_mm=25.0))
        # forced move (end point unknown to this backend) toward 60 mm: predictive trip before 25 mm
        from bend_stand.io import protocol as P  # noqa: PLC0415

        be.device.request(pg.Cmd.MOVE_ABS, P.build_request(pg.Cmd.MOVE_ABS, target_um=60_000, v_um_s=10_000,
                                                           a_um_s2=0), epoch=be.device.channel.motion_epoch)
        assert h.run_until(lambda: be.safety.trip is not None, 5000)
        assert be.safety.trip.limit == "TRAVEL_MAX" and "predicted" in be.safety.trip.text
        st = be.status()
        assert st.safety.sw_trip == "TRAVEL_MAX" and st.indicators.sw_trip.state == "ON"
        assert "SW_TRIP" in st.gates[GateId.MOVE].codes()
        refused = be.motion.check(MotionKind.MOVE, target_mm=24.9).refused
        assert "SW_TRIP" in {i.code for i in refused}                      # toward the limit: refused
        assert "SW_TRIP" not in {i.code for i in be.motion.check(MotionKind.MOVE, target_mm=10.0).refused}
        assert h.run_until(lambda: be.safety.trip is None, 2000)           # stopped inside → released
        assert be.status().motion.position_mm <= 25.0
    finally:
        be.shutdown()
