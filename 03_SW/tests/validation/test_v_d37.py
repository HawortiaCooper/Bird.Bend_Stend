"""Level C — SRS v0.5 / ICD v0.5 deltas of the M1 gate (CR-01 / D-36, D-37) — Validator F, SW_test_plan v0.3.

* TC-SW-CFG-004-02 (D-37 a): while a SAVE / LOAD / DEFAULTS is outstanding only stop-class frames (STOP, HALT,
  PAUSE) may appear on the wire — no heartbeat PING, no STATUS poll, no queued job; the held traffic follows the
  response. The flash stall is modelled by delaying the board response (``delay_next``, ≈ 0.6 s).
* TC-SAF-SW-005-03 (D-37 b): status bits of a feature whose GET_INFO bit is 0 are shown UNKNOWN (F-board:
  FEAT_DRV_SIGNALS / FEAT_BUTTONS off while the bits read 1).
* TC-SW-STOP-001-04 / TC-SW-STOP-002-04 (D-37 d, OBS-M1-R1): a STOP / HALT counts as confirmed only by its ACK or
  by an indication produced by the FW **after** it received the command — a DATA frame produced before (still in
  flight on an 8 ms link) must not confirm a lost request; the request is repeated.

Verifies: SW-CFG-004, SAF-SW-005, SW-STOP-001, SW-STOP-002, IF-005, IF-011
"""
from __future__ import annotations

import pytest

import harness as H
from oracle.fboard import FBoard

MS = 1_000_000
STOP_CLASS = {"STOP", "HALT", "PAUSE"}
LATENCY_NS = 8 * MS


# ============================================================================================ D-37 (a)

def _nvm_call(be, cmd):
    return {"SAVE_PARAMS": H.save_nvm, "LOAD_PARAMS": H.load_nvm, "DEFAULT_PARAMS": H.defaults}[cmd](be)


@pytest.mark.req("SW-CFG-004", "IF-011")
@pytest.mark.defect("SWD-M2-03")
@pytest.mark.xfail(strict=True, reason="SWD-M2-03 open (B): heartbeat PING / STATUS poll sent while a SAVE / LOAD / "
                                       "DEFAULTS is outstanding (D-37 a quiesce not implemented)")
@pytest.mark.parametrize("cmd", ["SAVE_PARAMS", "LOAD_PARAMS", "DEFAULT_PARAMS"])
def test_tc_sw_cfg_004_02_nvm_quiesce_only_stop_class_frames(vbe, cmd):
    """SRS v0.5 SW-CFG-004 AC: 'between a SAVE and its response only stop-class frames appear' (also LOAD /
    DEFAULTS). STOP, HALT and PAUSE pressed during the flash operation are written at once (priority path)."""
    # Verifies: SW-CFG-004, IF-011
    if cmd == "LOAD_PARAMS":
        H.result(vbe, H.save_nvm(vbe))
        H.advance(vbe, 50)
    H.act(vbe, "inject", fault="delay_next", cmd=cmd, what="response", n=1, ms=600)
    m0 = H.wire_mark(vbe)
    fut = _nvm_call(vbe, cmd)
    H.advance(vbe, 120)
    rd = H.read_all(vbe)                       # a queued job must wait for the response
    H.advance(vbe, 120)
    sent = {n: f(vbe).sent for n, f in (("STOP", H.stop), ("HALT", H.halt), ("PAUSE", H.pause))}
    assert all(sent.values()), sent
    H.advance(vbe, 500)
    H.result(vbe, fut)
    H.result(vbe, rd)
    req = H.tx(vbe, cmd, since=m0)[0]
    resp = [w for w in H.rx(vbe, cmd, since=m0, kind="response") if w.seq == req.seq][0]
    assert (resp.t_ns - req.t_ns) / MS >= 590                       # the stall really lasted ≈ 0.6 s
    between = [w.name for w in H.tx(vbe, since=m0) if req.t_ns < w.t_ns < resp.t_ns]
    assert STOP_CLASS <= set(between)
    assert set(between) <= STOP_CLASS, sorted(set(between) - STOP_CLASS)
    assert any(w.name == "GET_ALL_PARAMS" and w.t_ns > resp.t_ns for w in H.tx(vbe, since=m0))


# ============================================================================================ D-37 (b)

@pytest.fixture
def fb_be(pdict, tmp_path):
    made = []

    def make(**kw):
        fb = FBoard(pdict, **kw)
        be = H.realtime_backend(recordings_root=str(tmp_path / f"rec{len(made)}"))
        made.append((fb, be))
        H.connect(be, fb.endpoint).result(10)
        assert H.wait_rt(lambda: H.status(be).board is not None and bool(H.config_values(be)), 5)
        assert H.wait_rt(lambda: H.status(be).stream.on, 5)
        return fb, be

    yield make
    for fb, be in made:
        try:
            be.shutdown()
        finally:
            fb.close()


BASE_FEATURES = ("AFE", "MOTION", "HOMING", "MOVE_UNTIL_LOAD", "NVM")


@pytest.mark.req("SAF-SW-005", "IF-008")
@pytest.mark.defect("SWD-M2-04")
@pytest.mark.xfail(strict=True, reason="SWD-M2-04 open (B): ALM / PEND / DRV_PWR / PAUSE_BTN indicators are shown "
                                       "ON/OFF although their GET_INFO feature bit is 0 (D-37 b)")
def test_tc_saf_sw_005_03_bits_of_a_missing_feature_are_unknown(fb_be):
    """D-37 (b): FEAT_DRV_SIGNALS = 0 and FEAT_BUTTONS = 0 while DATA / STATUS carry DRV_PWR, ALM, PAUSE_BTN = 1 (as
    a non-conforming FW would) → the indicators alm, pend, drv_pwr, pause_btn are UNKNOWN; the motion / enable
    gates are not refused because of DRV_UNPOWERED / DRIVER_ALARM derived from those invalid bits."""
    # Verifies: SAF-SW-005, IF-008
    fb, be = fb_be(features=BASE_FEATURES, status_bits=("DRV_PWR", "ALM", "PAUSE_BTN"), io=("ALM", "PAUSE_BTN"))
    H.wait_rt(lambda: False, 0.3)               # a few DATA frames + one STATUS poll
    bad = {k: H.indicator(be, k).state for k in ("alm", "pend", "drv_pwr", "pause_btn")
           if H.indicator(be, k).state != "UNKNOWN"}
    assert not bad, bad
    enable_codes = {str(i.code) for i in H.gate(be, "ENABLE").items}
    assert not {"DRV_UNPOWERED", "DRIVER_ALARM"} & enable_codes, enable_codes


@pytest.mark.req("SAF-SW-005")
def test_tc_saf_sw_005_03_bits_of_a_present_feature_are_shown(fb_be):
    """Contrast case: with FEAT_DRV_SIGNALS = 1 the same bits are valid — DRV_PWR 1 → ON, ALM 1 → ON, PEND 0 → OFF."""
    # Verifies: SAF-SW-005
    fb, be = fb_be(features=BASE_FEATURES + ("DRV_SIGNALS",), status_bits=("DRV_PWR", "ALM"), io=("ALM",))
    assert H.wait_rt(lambda: H.indicator(be, "drv_pwr").state == "ON", 3)
    assert H.indicator(be, "alm").state == "ON" and H.indicator(be, "pend").state == "OFF"


# ============================================================================================ D-37 (d)

def _moving_frames(be, since):
    return [w for w in H.rx(be, "DATA", since=since) if "MOVING" in w.fields.get("flags", [])]


@pytest.mark.req("SW-STOP-001", "IF-005")
@pytest.mark.defect("SWD-M2-02")
@pytest.mark.xfail(strict=True, reason="SWD-M2-02 open (B): OBS-M1-R1 — confirmation by PC receive order; a DATA "
                                       "frame produced before the MOVE_ABS executed confirms a lost STOP (D-37 d)")
def test_tc_sw_stop_001_04_pre_execution_data_never_confirms_a_lost_stop(lockstep):
    """OBS-M1-R1 / D-37 (d): 8 ms link latency each way; MOVE_ABS 200 mm in flight, STOP pressed 2 ms later and its
    request lost. DATA frames produced by the FW before it executed the MOVE (MOVING = 0) arrive after the STOP
    write — they must not confirm the STOP: the STOP is repeated (≥ 2 frames), the confirmation needs attempts ≥ 2,
    and the axis stops long before the target."""
    # Verifies: SW-STOP-001, IF-005
    be = lockstep(sim_latency_ns=LATENCY_NS)
    H.forced_enable_home(be)
    H.advance(be, 100)
    H.act(be, "inject", fault="drop_next", cmd="STOP", what="request", n=1)
    m0 = H.wire_mark(be)
    H.forced_move_abs(be, 200_000, 20_000)
    H.advance(be, 2)
    t_stop = H.now_ns(be)
    assert H.stop(be).sent
    H.advance(be, 1500)
    first_moving = min((w.t_ns for w in _moving_frames(be, m0)), default=None)
    stale = [w for w in H.rx(be, "DATA", since=m0) if w.t_ns > t_stop and "MOVING" not in w.fields.get("flags", [])
             and (first_moving is None or w.t_ns < first_moving)]
    assert stale, "precondition: a pre-execution MOVING = 0 frame arrived after the STOP write"
    stops = H.tx(be, "STOP", since=m0)
    conf = [r.payload for r in H.history(be, "stop.confirmed") if r.payload.cmd == "STOP" and r.t_host_ns >= t_stop]
    assert len(stops) >= 2, len(stops)
    assert conf and conf[0].attempts >= 2, conf[:1]
    assert not H.status(be).motion.moving
    assert H.rx(be, "DATA")[-1].fields["setpoint_um"] < 150_000


@pytest.mark.req("SW-STOP-002", "IF-005")
@pytest.mark.defect("SWD-M2-02")
@pytest.mark.xfail(strict=True, reason="SWD-M2-02 open (B): HALT confirmed by a frame produced before the FW executed "
                                       "the preceding HALT_CLEAR (D-37 d)")
def test_tc_sw_stop_002_04_halt_confirmed_only_by_device_time_after_receipt(lockstep):
    """D-37 (d) for HALT: HALT latched → Clear stop (HALT_CLEAR in flight, 8 ms latency) → Pause/Break 1 ms later
    with the HALT request lost. Frames produced before the clear executed still show HALT = 1 and arrive after the
    HALT write: they must not confirm it — HALT is repeated and is latched at the end."""
    # Verifies: SW-STOP-002, IF-005
    be = lockstep(sim_latency_ns=LATENCY_NS)
    assert H.halt(be).sent
    assert H.run_until(be, lambda: H.indicator(be, "halt").state == "ON", 1000)
    H.advance(be, 50)
    H.act(be, "inject", fault="drop_next", cmd="HALT", what="request", n=1)
    m0 = H.wire_mark(be)
    fut = H.clear_stop(be)
    H.advance(be, 1)
    t_h = H.now_ns(be)
    assert H.halt(be).sent
    H.advance(be, 1500)
    H.result(be, fut)
    halts = H.tx(be, "HALT", since=m0)
    conf = [r.payload for r in H.history(be, "stop.confirmed") if r.payload.cmd == "HALT" and r.t_host_ns >= t_h]
    assert len(halts) >= 2, len(halts)
    assert conf and conf[0].attempts >= 2, conf[:1]
    assert H.indicator(be, "halt").state == "ON"
