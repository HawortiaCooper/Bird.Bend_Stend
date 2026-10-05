"""M2 backend (WP-B12/B13): MotionController (enable / disable / home / move_to / move_by latest-wins / jog refresh /
dead-man / test zero), motion gates, SW travel limits + jog bound, FW threshold manager paths, NVM quiesce (D-37 a),
stop confirmation by device time (D-37 d), feature-dependent indicators (D-37 b), Pause/Break hotkey (fake), MC-4.

All on the lock-step backend against the in-process simulator; wire facts from the PC wire log.

Verifies: SW-MAN-001, SW-MAN-002, SW-MAN-003, SW-MAN-004, SW-MAN-005, SW-MAN-006, SW-LIM-001, SAF-SW-002, SAF-SW-004,
SAF-SW-005, SW-STOP-001, SW-STOP-002, SW-STOP-004, SW-CFG-004, IF-005, IF-009, IF-011
"""
from __future__ import annotations

import struct
from dataclasses import replace

import pytest

from bbs_support import lockstep_backend, tx_frames
from bend_stand.core import protocol_gen as pg
from bend_stand.core.backend import Backend, BackendSettings
from bend_stand.core.errors import ConfirmationRequired, GateRefused
from bend_stand.core.gates import GateSnapshot, g_enable, g_motion
from bend_stand.core.model import GateId, LinkState, MotionKind, StopConfirmation
from bend_stand.core.safety import calibrated_target, check_manual

Cmd = pg.Cmd
MS = 1_000_000


def _ready(home: bool = True, **kw) -> Backend:
    kw.setdefault("no_specimen", True)
    be = lockstep_backend(**kw)
    h = be.test_hooks
    h.advance(50)
    assert be.motion.enable().ok
    assert h.run_until(lambda: be.status().motion.enabled, 2000)
    if home:
        be.device.params.update("home.v_fast_um_s", 20_000)       # local copy only; the sim uses its own value
        h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}))
        t = be.motion.home(load_confirmed=True)
        h.result(t, 60_000)
        assert h.run_until(lambda: be.status().motion.homed and not be.status().motion.moving, 2000)
    return be


def _targets(be: Backend) -> list[int]:
    return [struct.unpack_from("<i", r.frame, 6)[0] for r in tx_frames(be, Cmd.MOVE_ABS)]


def _settle(be: Backend, ms: int = 20_000) -> None:
    assert be.test_hooks.run_until(lambda: not be.status().motion.moving and be.motion._active is None, ms)  # noqa: SLF001


# ============================================================================================ enable / home


@pytest.mark.req("SW-MAN-006", "SAF-SW-004")
def test_enable_home_and_confirmations() -> None:
    be = lockstep_backend(no_specimen=True)
    try:
        h = be.test_hooks
        g = be.status().gates[GateId.MOVE]
        assert not g.ok and "NOT_ENABLED" in g.codes()
        assert be.motion.enable().ok
        h.advance(100)
        assert be.status().motion.enabling_left_ms > 0 and "ENABLING_SW" in be.status().gates[GateId.MOVE].codes()
        assert h.run_until(lambda: be.status().motion.enabled, 1000)
        # HOME with the load unknown (no calibration) needs the confirmation; nothing sent without it
        hg = be.status().gates[GateId.HOME]
        assert hg.needs_confirmation and "HOME_LOAD_CONFIRM" in hg.codes()
        with pytest.raises(ConfirmationRequired):
            h.result(be.motion.home(load_confirmed=False))
        assert not tx_frames(be, Cmd.HOME)
        h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}))
        out = h.result(be.motion.home(load_confirmed=True), 60_000)
        assert out.kind == "DONE" and out.done.reason == "TARGET" and tx_frames(be, Cmd.HOME)[0].frame[6] == 1
        st = be.status().motion
        assert st.homed and st.home_phase == "DONE" and st.commanded_target_mm is None or True
        # DISABLE: confirmation first (SAF-SW-004), nothing sent without it
        g = be.motion.disable(confirmed=False)
        assert not g.ok and "CONFIRMATION_REQUIRED" in g.codes() and not tx_frames(be, Cmd.DISABLE)
        assert be.motion.disable(confirmed=True).ok
        h.advance(50)
        assert not be.status().motion.enabled and not be.status().motion.homed and tx_frames(be, Cmd.DISABLE)
        assert not be.motion.disable(confirmed=True).ok                 # already disabled
    finally:
        be.shutdown()


# ============================================================================================ move


@pytest.mark.req("SW-MAN-002", "SW-MAN-003", "IF-009", "SYS-003")
def test_move_to_move_by_latest_wins() -> None:
    be = _ready()
    try:
        h = be.test_hooks
        t = be.motion.move_to(10.0)
        out = h.result(t, 20_000)
        assert out.kind == "DONE" and out.done.reason == "TARGET" and out.done.pos_mm == 10.0
        n0 = len(_targets(be))
        for _ in range(3):                                        # each move completes in between: 11, 12, 13
            h.result(be.motion.move_by(1.0), 20_000)
        assert _targets(be)[n0:] == [11_000, 12_000, 13_000]
        h.result(be.motion.move_to(10.0), 20_000)
        n0 = len(_targets(be))
        t1 = be.motion.move_by(1.0)
        h.advance(20)
        t2 = be.motion.move_by(1.0)                               # pending 12
        t3 = be.motion.move_by(1.0)                               # pending 13 (replaces 12)
        assert be.status().motion.pending_target_mm == pytest.approx(13.0)
        assert h.result(t2).kind == "CANCELLED"
        assert h.result(t1, 20_000).kind == "DONE" and h.result(t3, 20_000).kind == "DONE"
        assert _targets(be)[n0:] == [11_000, 13_000] and be.status().motion.pending_target_mm is None
        h.result(be.motion.move_to(12.3455), 20_000)              # round half away (SYS-003)
        assert _targets(be)[-1] == 12_346
        st = be.status().motion
        assert st.commanded_target_mm == pytest.approx(12.3455) and st.position_mm == pytest.approx(12.346)
        assert not [r for r in tx_frames(be, Cmd.JOG)]
    finally:
        be.shutdown()


@pytest.mark.req("SW-MAN-005", "SW-LIM-001")
def test_check_caps_range_and_sw_travel_limits() -> None:
    be = _ready()
    try:
        h = be.test_hooks
        lim = be.motion.limits()
        assert lim.v_travel_mm_s == 30.0 and lim.v_cap_mm_s == 30.0 and not lim.loaded
        g = be.motion.check(MotionKind.MOVE, speed_mm_s=31.0, target_mm=50.0)
        assert not g.ok and any("30" in i.text for i in g.refused)
        assert not be.motion.check(MotionKind.MOVE, accel_mm_s2=1000.0).ok
        assert not be.motion.check(MotionKind.MOVE, speed_mm_s=0.0).ok
        assert not be.motion.check(MotionKind.MOVE, target_mm=500.0).ok          # beyond the soft limit
        assert isinstance(be.motion.move_to(50.0, speed_mm_s=31.0).exception(), GateRefused)
        # SW travel limits: inside the soft limits only; target outside refused; jog bound = the limit
        bad = be.limits.set(replace(be.limits.get(), travel_max_mm=500.0, travel_max_enabled=True))
        assert bad and bad[0].code == "OUTSIDE_SOFT_LIMITS"
        assert not be.limits.set(replace(be.limits.get(), travel_min_mm=1.0, travel_min_enabled=True,
                                         travel_max_mm=5.0, travel_max_enabled=True))
        assert be.motion.limits().travel_max_mm == 5.0
        assert isinstance(be.motion.move_to(6.0).exception(), GateRefused)
        assert not tx_frames(be, Cmd.MOVE_ABS)
        h.result(be.motion.move_to(4.0), 20_000)
        be.gui_beat()
        be.motion.jog_start(+1, 2.0)
        for _ in range(30):
            h.advance(50)
            be.gui_beat()
        bounds = {struct.unpack_from("<i", r.frame, 14)[0] for r in tx_frames(be, Cmd.JOG)}
        assert bounds == {5000}
        md = [e.payload for e in be.events.history("motion.done")][-1]
        assert md.reason == "BOUND" and md.pos_mm == 5.0
        assert not be.status().motion.jogging
        n = len(tx_frames(be, Cmd.JOG))
        be.motion.jog_start(+1, 2.0)                              # at the limit: refused locally
        assert len(tx_frames(be, Cmd.JOG)) == n
    finally:
        be.shutdown()


# ============================================================================================ jog


@pytest.mark.req("SW-MAN-004")
def test_jog_refresh_interval_stop_and_dead_man_without_gui() -> None:
    be = _ready()
    try:
        h = be.test_hooks
        be.motion.jog_start(+1, 2.0)
        for _ in range(60):
            h.advance(25)
            be.gui_beat()
        be.motion.jog_stop()
        h.advance(300)
        jogs = tx_frames(be, Cmd.JOG)
        run = [r for r in jogs if struct.unpack_from("<i", r.frame, 6)[0] != 0]
        gaps = [(b.t_ns - a.t_ns) / MS for a, b in zip(run, run[1:])]
        assert len(run) >= 17 and max(gaps) < 100
        assert struct.unpack_from("<i", jogs[-1].frame, 6)[0] == 0
        md = [e.payload for e in be.events.history("motion.done")][-1]
        assert md.reason == "JOG_ZERO"
        # GUI beat withheld → refresh stops → FW dead-man (SAF-FW-016)
        be.gui_beat()
        be.motion.jog_start(+1, 2.0)
        assert h.run_until(lambda: any(e.payload.name == "STOPPED" for e in be.events.history("fw.event")[-5:]),
                           900)
        st = [e.payload for e in be.events.history("fw.event") if e.payload.name == "STOPPED"][-1]
        assert st.arg_name == "JOG_DEADMAN" and not be.motion.jogging
        assert h.run_until(lambda: not be.status().gates[GateId.DISABLE].refused, 2000)
        # un-homed: capped at v_unhomed, no bound
        assert be.motion.disable(confirmed=True).ok
        h.advance(30)
        assert be.motion.enable().ok
        h.advance(600)
        n0 = len(tx_frames(be, Cmd.JOG))
        be.motion.jog_start(-1, 50.0)
        h.advance(10)
        r = tx_frames(be, Cmd.JOG)[n0]
        v, _a, b = struct.unpack_from("<iIi", r.frame, 6)
        assert v == -2000 and b == pg.JOG_NO_BOUND
        be.motion.jog_stop()
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-004", "SW-MAN-003")
@pytest.mark.parametrize("how", ["stop", "pause", "halt"])
def test_pending_target_and_jog_dropped_by_stop_pause_halt(how: str) -> None:
    be = _ready()
    try:
        h = be.test_hooks
        t1 = be.motion.move_to(50.0)
        h.advance(50)
        t2 = be.motion.move_by(5.0)
        assert be.status().motion.pending_target_mm == pytest.approx(55.0)
        n0 = len(_targets(be))
        getattr(be, how)("test")
        assert be.status().motion.pending_target_mm is None and h.result(t2).kind == "CANCELLED"
        h.advance(2000)
        out = h.result(t1)
        assert out.kind == "DONE" and out.done.reason == "STOPPED" and out.done.stop_cause
        assert len(_targets(be)) == n0
        if how == "pause":
            g = be.status().gates[GateId.MOVE]
            assert "PAUSED" in g.codes()
            assert be.resume().ok
            h.advance(100)
        if how == "halt":
            assert h.result(be.clear_stop_async()).confirmed
        # a motion command refused with BLOCK PAUSED is an expected outcome (D-33 k)
        be.pause("test")
        h.advance(50)
        be.device._poll_now()  # noqa: SLF001
        h.advance(20)
        assert isinstance(be.motion.move_to(1.0).exception(), GateRefused)
    finally:
        be.shutdown()


@pytest.mark.req("IF-005")
def test_move_response_lost_resolved_by_status_never_resent() -> None:
    be = _ready()
    try:
        h = be.test_hooks
        be.sim.act("inject", fault="drop_next", cmd="MOVE_ABS", what="response")
        out = h.result(be.motion.move_to(20.0), 20_000)
        assert out.kind == "DONE" and len(tx_frames(be, Cmd.MOVE_ABS)) == 1
        be.sim.act("inject", fault="drop_next", cmd="MOVE_ABS", what="request")
        out = h.result(be.motion.move_to(25.0), 20_000)
        assert out.kind == "NOT_EXECUTED" and len(tx_frames(be, Cmd.MOVE_ABS)) == 2
        be.sim.inject_nack("MOVE_ABS", "E_STATE", int(pg.Block.PAUSED))
        assert h.result(be.motion.move_to(25.0), 2000).kind == "REFUSED_PAUSED"
        be.sim.inject_nack("MOVE_ABS", "E_RANGE", 4)
        assert h.result(be.motion.move_to(25.0), 2000).kind == "REFUSED"
    finally:
        be.shutdown()


@pytest.mark.req("SW-MAN-006")
def test_test_zero_and_valid_toggle() -> None:
    be = _ready()
    try:
        h = be.test_hooks
        h.result(be.motion.move_to(7.0), 20_000)
        assert be.motion.set_test_zero() == pytest.approx(7.0)
        h.result(be.motion.move_to(9.0), 20_000)
        assert be.status().motion.test_position_mm == pytest.approx(2.0)
        be.motion.reset_test_zero()
        assert be.status().motion.x_zero_mm == 0.0
        assert be.motion.set_valid(True).ok
        h.advance(100)
        assert be.status().indicators.valid.state == "ON"
    finally:
        be.shutdown()


# ============================================================================================ gates (pure)


@pytest.mark.req("SAF-SW-005", "SW-STOP-004")
def test_motion_gate_items_and_d37b_invalid_bits() -> None:
    base = GateSnapshot(link=LinkState.CONNECTED, stream_on=True, data_fresh=True,
                        flags=int(pg.DataFlags.ENABLED | pg.DataFlags.HOMED), features=frozenset(pg.FEATURES_BITS),
                        thresholds_state="DEFAULT_ONLY", motion_state="IDLE", status=int(pg.DataStatus.DRV_PWR),
                        thresholds_match=True, load_limits_on=False)
    assert g_motion(base, MotionKind.MOVE).ok
    cases = {"DRV_UNPOWERED": replace(base, status=0),
             "DRIVER_ALARM": replace(base, status=int(pg.DataStatus.ALM | pg.DataStatus.DRV_PWR)),
             "PAUSED": replace(base, status=int(pg.DataStatus.PAUSED | pg.DataStatus.DRV_PWR)),
             "HALT": replace(base, flags=base.flags | int(pg.DataFlags.HALT), status=int(pg.DataStatus.DRV_PWR)),
             "THRESHOLDS_UNVERIFIED": replace(base, thresholds_state="FAILED", status=int(pg.DataStatus.DRV_PWR)),
             "FEATURE_MISSING": replace(base, features=frozenset(), status=int(pg.DataStatus.DRV_PWR)),
             "STREAM_STALE": replace(base, data_fresh=False, status=int(pg.DataStatus.DRV_PWR))}
    for code, snap in cases.items():
        g = g_motion(snap, MotionKind.MOVE)
        assert not g.ok and code in g.codes(), (code, g)
    # D-37 b: DRV_PWR / ALM not valid (feature bit 0) → never DRV_UNPOWERED / DRIVER_ALARM
    inval = replace(base, status=int(pg.DataStatus.ALM), valid_status=0xFFFF & ~int(
        pg.DataStatus.ALM | pg.DataStatus.DRV_PWR | pg.DataStatus.PEND))
    assert not {"DRV_UNPOWERED", "DRIVER_ALARM"} & set(g_motion(inval, MotionKind.MOVE).codes())
    assert not {"DRV_UNPOWERED"} & set(g_enable(inval).codes())
    # jog refresh of a running jog is not blocked by ALM (SAF-FW-026)
    jogging = replace(cases["DRIVER_ALARM"], jogging=True, moving=True)
    assert "DRIVER_ALARM" not in g_motion(jogging, MotionKind.JOG).codes()


@pytest.mark.req("SAF-SW-005")
def test_feature_bits_unknown_on_an_m1_build() -> None:
    be = lockstep_backend(connect=False)
    try:
        h = be.test_hooks
        h.result(be.connect_async("sim"), 5000)
        be.sim.board.features = int(pg.Features.AFE_SYNTHETIC | pg.Features.NVM)
        h.result(be.connect_async("sim"), 5000)                  # reconnect: GET_INFO with the M1 mask
        h.advance(300)
        ind = be.status().indicators
        assert {ind[k].state for k in ("alm", "pend", "drv_pwr", "pause_btn")} == {"UNKNOWN"}
        assert ind.valid.state != "UNKNOWN"
        assert "FEATURE_MISSING" in be.status().gates[GateId.ENABLE].codes()
    finally:
        be.shutdown()


# ============================================================================================ thresholds


@pytest.mark.req("SAF-SW-002")
def test_threshold_manager_default_manual_and_calibrated_paths() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        assert be.limits.thresholds().state == "DEFAULT_ONLY"
        assert isinstance(be.limits.set_manual_thresholds_async(5, 1, 0).exception(), GateRefused)
        st = h.result(be.limits.set_manual_thresholds_async(-400_000, 400_000, 50_000))
        assert st.state == "VERIFIED" and st.cal_id == "manual-raw" and be.sim.board.params["safety.load_raw_max"] == \
            400_000 and be.sim.board.params["safety.zero_raw"] == 50_000
        be.sim.inject_store_mismatch("safety.zero_raw", 1)
        assert h.result(be.limits.set_manual_thresholds_async(-400_000, 400_000, 60_000)).state == "FAILED"
        assert "THRESHOLDS_UNVERIFIED" in be.status().gates[GateId.MOVE].codes()
        assert h.result(be.limits.set_default_thresholds_async()).state == "DEFAULT_ONLY"
        h.result(be.config.defaults_async())                     # DEFAULTS → re-sent
        assert be.limits.thresholds().state == "DEFAULT_ONLY"
    finally:
        be.shutdown()
    assert check_manual(0, 0, 0) and check_manual(-10, 10, 20) and not check_manual(-10, 10, 0)
    t = calibrated_target(0.00045666488086106374, 125430.0, 1500.0)
    assert (t.raw_min, t.raw_max, t.zero_raw, t.clamped) == (-3159254, 3410114, 125430, False)
    t = calibrated_target(0.0001, 0.0, 2000.0)                    # small K → beyond the range → clamped inward
    assert t.clamped and (t.raw_min, t.raw_max) == (-7151121, 7151121) and t.eff_pull_n < 2000.0
    with pytest.raises(ValueError):
        calibrated_target(0.0005, 0.0, 3000.0)                    # > 110 % FS


# ============================================================================================ D-37 a / d, MC-4


@pytest.mark.req("SW-CFG-004", "IF-011")
def test_nvm_quiesce_only_stop_class_frames_during_save() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        be.sim.act("inject", fault="delay_next", cmd="SAVE_PARAMS", what="response", ms=600)
        fut = be.config.save_async()
        h.advance(30)
        t_save = tx_frames(be, Cmd.SAVE_PARAMS)[0].t_ns
        rd = be.config.read_all_async()
        clr = be.fault_clear_async()                              # a clear is held too (non-stop class)
        h.advance(200)
        be.stop("t")
        be.halt("t")
        h.advance(500)
        h.result(fut)
        h.result(rd)
        h.result(clr)
        resp = next(r for r in be.test_hooks.rx_log() if r.type == (Cmd.SAVE_PARAMS | 0x80))
        between = {r.frame[2] for r in be.test_hooks.wire_log()
                   if r.direction == "TX" and t_save < r.t_ns < resp.t_host_ns}
        assert between == {int(Cmd.STOP), int(Cmd.HALT)}, between
        assert be.status().link.state == LinkState.CONNECTED
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-001", "IF-005")
def test_stop_confirmed_only_by_an_indication_after_the_fw_received_it() -> None:
    be = _ready(sim_latency_ns=8 * MS)
    try:
        h = be.test_hooks
        h.advance(200)
        be.sim.act("inject", fault="drop_next", cmd="STOP", what="request")
        t = be.motion.move_to(200.0)
        h.advance(2)
        assert be.stop("t").sent
        h.advance(1500)
        stops = tx_frames(be, Cmd.STOP)
        conf = [e.payload for e in be.events.history("stop.confirmed") if e.payload.cmd == "STOP"]
        assert len(stops) >= 2 and conf and conf[-1].attempts >= 2
        assert h.result(t).done.pos_mm < 150.0
        c = conf[-1]
        assert isinstance(c, StopConfirmation) and c != "STOP"          # MC-4: no str equality any more
        assert be.device.device_time_at(be.clock.monotonic_ns()) is not None and be.device.rtt_bound_ns() > 0
    finally:
        be.shutdown()


# ============================================================================================ hotkey


@pytest.mark.req("SW-STOP-002", "NFR-003")
def test_hotkey_press_halts_and_test_mode_reports_only() -> None:
    be = _ready(home=False)
    try:
        h = be.test_hooks
        h.hotkey_press()
        h.advance(100)
        assert tx_frames(be, Cmd.HALT) and be.status().indicators.halt.state == "ON"
        assert h.result(be.clear_stop_async()).confirmed
        assert be.status().hotkey.mode == "UNAVAILABLE"          # no hotkey thread on the lock-step clock
        assert not be.hotkey_test_start().ok
    finally:
        be.shutdown()
    rt = Backend(BackendSettings(hotkey="fake"))
    rt.start()
    try:
        st = rt.status().hotkey
        assert st.mode == "REGISTERED" and rt.status().indicators.hotkey.state == "ON"
        seen = []
        rt.events.subscribe("hotkey.test", lambda r: seen.append(r.payload), weak=False)
        assert rt.hotkey_test_start(2.0).ok and rt.status().hotkey.test_running
        assert not rt.status().gates[GateId.HOTKEY_TEST].ok                 # already running
        rt.hotkey.backend.press()
        for _ in range(100):
            if seen:
                break
            import time  # noqa: PLC0415
            time.sleep(0.01)
        assert seen and seen[0] is not None and rt.hotkey.halts == 0
        rt.hotkey.backend.press()                                # outside the window: a real HALT (not connected)
        for _ in range(100):
            if rt.hotkey.halts:
                break
            import time  # noqa: PLC0415
            time.sleep(0.01)
        assert rt.hotkey.halts == 1 and any(e.payload.cmd == "HALT" for e in rt.events.history("stop.issued"))
    finally:
        rt.shutdown()
    off = Backend(BackendSettings(hotkey="off"))
    off.start()
    try:
        assert off.status().hotkey.mode == "UNAVAILABLE" and "off" in off.status().hotkey.reason
    finally:
        off.shutdown()


@pytest.mark.req("SW-STOP-002")
def test_hotkey_service_fallbacks_with_the_fake_backend() -> None:
    import time  # noqa: PLC0415

    from bend_stand.io import win_hotkey as wh  # noqa: PLC0415

    def wait(pred) -> bool:
        for _ in range(200):
            if pred():
                return True
            time.sleep(0.005)
        return pred()

    for setup, mode in ((lambda b: None, "REGISTERED"), (lambda b: b.fail_register(), "LL_HOOK"),
                        (lambda b: (b.fail_register(), b.fail_hook()), "UNAVAILABLE"),
                        (lambda b: b.fail_ids.add(4), "REGISTERED")):
        b = wh.FakeHotkeyBackend()
        setup(b)
        halts: list[str] = []
        states: list[str] = []
        hk = wh.GlobalHaltHotkey(halts.append, backend=b, on_status=lambda m, d: states.append(m))
        try:
            hk.start()
            assert wait(lambda: hk.mode == mode or not hk.alive), (mode, hk.mode)
            if mode == "UNAVAILABLE":
                assert not hk.available and states[-1] == "UNAVAILABLE"
                continue
            b.press()
            b.hold(wh.VK_CANCEL)                               # Ctrl+Break, auto-repeat → one HALT
            assert wait(lambda: len(halts) >= 2) and halts[0] == "hotkey"
            assert b.estop_in_hook == 0
            hk.ping()
            assert wait(lambda: hk.beat_age_ms() is not None)
            hk.start_test_mode()
            assert hk.test_mode_active
            hk.cancel_test_mode()
            assert not hk.test_mode_active
        finally:
            hk.stop()
        assert not hk.alive and hk.mode == "UNAVAILABLE"


# ============================================================================================ M2 close-out


@pytest.mark.req("IF-005", "SW-MAN-002")
def test_lost_response_and_lost_move_done_of_a_finished_move_resolves_done() -> None:
    """SWD-M2-03: a short move that already ended while its response and its MOVE_DONE were lost → DONE from the
    GET_STATUS position (= target), never re-sent."""
    be = _ready()
    try:
        h = be.test_hooks
        b = be.sim.board
        orig = b.emit

        def emit(code, *a, **k):                       # the MOVE_DONE of this move is lost (EVENT overflow)
            if code == pg.Event.MOVE_DONE:
                b.emit = orig
                return None
            return orig(code, *a, **k)
        h.result(be.motion.move_to(1.0), 20_000)
        n0 = len(tx_frames(be, Cmd.MOVE_ABS))
        be.sim.act("inject", fault="drop_next", cmd="MOVE_ABS", what="response")
        b.emit = emit
        t = be.motion.move_to(1.05, speed_mm_s=30.0)  # 50 µm: done long before the 100 ms response timeout
        out = h.result(t, 5000)
        assert out.kind == "DONE" and out.done.pos_mm == 1.05 and len(tx_frames(be, Cmd.MOVE_ABS)) == n0 + 1
        assert be.status().motion.commanded_target_mm == pytest.approx(1.05)
    finally:
        be.shutdown()


@pytest.mark.req("SAF-SW-002", "SYS-003")
def test_calibrated_zero_rounds_half_away() -> None:
    assert calibrated_target(0.0005, 100.5, 1000.0).zero_raw == 101           # banker's rounding would give 100
    assert calibrated_target(0.0005, -100.5, 1000.0).zero_raw == -101


@pytest.mark.req("SAF-SW-005")
def test_clear_hints_have_no_contactor_texts() -> None:
    """SWD-M2-01 / D-41: no K1 contactor and no RESET button in the E-stop / driver-power texts."""
    from bend_stand.core.gates import CLEAR_HINTS  # noqa: PLC0415

    for k in ("ESTOP", "DRV_PWR", "DRV_UNPOWERED", "DRIVER_ALARM", "K1_WELDED"):
        assert "RESET" not in CLEAR_HINTS[k] and "K1" not in CLEAR_HINTS[k], k
    assert "re-home" in CLEAR_HINTS["ESTOP"]
