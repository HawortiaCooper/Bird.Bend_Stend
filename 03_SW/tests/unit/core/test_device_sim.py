"""Device against the in-process simulator on the lock-step clock (SimRig = Backend + SimBoard, §19):
connect + compat states, configuration write/verify statuses, NVM, reboot, clears / RESUME (VERIFY, D-31/D-34),
STOP/HALT/PAUSE confirmation, heartbeat, link loss, DATA loss while moving.

Verifies: SW-PLT-003, IF-008, SW-CFG-001…004, IF-005, IF-011, SAF-SW-003, SW-STOP-001/002/003, SW-ACQ-001
"""
from __future__ import annotations

import struct
from dataclasses import replace

import pytest

from bbs_support import lockstep_backend, tx_frames
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.errors import CommandNotExecuted, ConfigReadOnly, LinkError
from bend_stand.core.model import Compat, GateId, LinkState, WriteStatus
from bend_stand.io import protocol as P
from bend_stand.io.sim.board import SimBoard

Cmd = pg.Cmd


@pytest.fixture
def be():
    b = lockstep_backend()
    yield b
    b.shutdown()


@pytest.mark.req("SW-PLT-003", "IF-008", "SW-ACQ-001")
def test_connect_sequence(be) -> None:
    st = be.status()
    assert st.link.state == LinkState.CONNECTED and st.link.compat == Compat.OK
    assert st.link.info.build.startswith("SIM-ICD") and "MOTION" in st.link.info.features
    order = [w.frame[2] for w in be.test_hooks.wire_log() if w.direction == "TX"]
    i_info, i_status = order.index(Cmd.GET_INFO), order.index(Cmd.GET_STATUS)
    i_params, i_stream = order.index(Cmd.GET_ALL_PARAMS), order.index(Cmd.STREAM_START)
    assert i_info < i_status < i_params < i_stream          # ICD §9.5 order
    assert order.count(Cmd.GET_ALL_PARAMS) == 3              # 48 parameters → 3 pages
    assert be.device.params.values()["motion.steps_per_mm"] == 800.0
    assert be.device.thresholds.state == "DEFAULT_ONLY"
    be.test_hooks.advance(1000)
    st = be.status()
    assert st.stream.on and 70 < st.stream.rate_sps < 90
    assert st.link.stats.data_frames > 70 and st.link.stats.crc_errors == 0
    # never an automatic enable / home / move after connect
    assert not any(tx_frames(be, c) for c in (Cmd.ENABLE, Cmd.HOME, Cmd.MOVE_ABS, Cmd.JOG))


@pytest.mark.req("IF-008")
@pytest.mark.parametrize("change, flag", [
    ({"proto_major": 2}, Compat.MAJOR_MISMATCH), ({"payload_version": 2}, Compat.PAYLOAD_MISMATCH),
    ({"param_dict_hash": 0x12345678}, Compat.PARAM_HASH_MISMATCH), ({"proto_minor": 0}, Compat.OK),
])
def test_compat_states(monkeypatch: pytest.MonkeyPatch, change: dict, flag: Compat) -> None:
    orig = SimBoard.info
    monkeypatch.setattr(SimBoard, "info", lambda self: replace(orig(self), **change))
    b = lockstep_backend()
    try:
        st = b.status()
        assert st.link.compat == flag
        if flag & (Compat.MAJOR_MISMATCH | Compat.PAYLOAD_MISMATCH | Compat.PARAM_HASH_MISMATCH):
            assert st.config_read_only and not st.gates[GateId.CONFIG_WRITE].ok
            f = b.config.write_and_verify_async({"afe.rate_tol_pct": 30})
            with pytest.raises(ConfigReadOnly):
                b.test_hooks.result(f)
        if flag & Compat.MAJOR_MISMATCH:
            assert not st.gates[GateId.PAUSE].ok
            assert not tx_frames(b, Cmd.SET_PARAM)            # read-only: session values not written
    finally:
        b.shutdown()


@pytest.mark.req("SW-CFG-003")
def test_write_and_verify_ok_rule_order_and_reboot_required(be) -> None:
    h = be.test_hooks
    assert h.result(be.config.write_and_verify_async({"afe.timeout_ms": 100})).ok      # valid at SPS80
    rep = h.result(be.config.write_and_verify_async({
        "afe.rate_sps": "SPS10", "afe.timeout_ms": 300, "motion.pul_invert": True, "afe.rate_tol_pct": 25}))
    by = rep.by_key()
    assert by["afe.rate_sps"].status == WriteStatus.OK and by["afe.timeout_ms"].status == WriteStatus.OK
    assert by["motion.pul_invert"].status == WriteStatus.REBOOT_REQUIRED
    assert rep.ok and rep.cfg_dirty is True and rep.reboot_pending is True
    sets = [P.decode_param_entry(w.frame[6:13]).key for w in tx_frames(be, Cmd.SET_PARAM)]
    assert sets[-4:].index("afe.timeout_ms") < sets[-4:].index("afe.rate_sps")   # H5: timeout up before SPS10
    assert be.config.values()["afe.rate_sps"] == 0


@pytest.mark.req("SW-CFG-003")
def test_write_and_verify_local_errors_send_nothing(be) -> None:
    n0 = len(tx_frames(be, Cmd.SET_PARAM))
    rep = be.test_hooks.result(be.config.write_and_verify_async(
        {"afe.rate_tol_pct": 99, "safety.zero_raw": 1, "no.key": 1, "motion.v_max_load_um_s": 40000}))
    assert not rep.ok and all(i.status == WriteStatus.NOT_ATTEMPTED for i in rep.items)
    codes = {i.code for i in rep.issues}
    assert {"RANGE", "LOCKED", "UNKNOWN_KEY"} <= codes
    assert len(tx_frames(be, Cmd.SET_PARAM)) == n0
    issues = be.config.check({"motion.v_max_load_um_s": 40000})
    assert any(i.code == "RULE_H4" for i in issues)


@pytest.mark.req("SW-CFG-003")
def test_write_and_verify_rejected_mismatch_busy_timeout(be) -> None:
    h, sim = be.test_hooks, be.sim
    sim.inject_nack("SET_PARAM", "E_BUSY", 1)
    rep = h.result(be.config.write_and_verify_async({"afe.rate_tol_pct": 30}))
    assert rep.items[0].status == WriteStatus.BUSY
    sim.inject_nack("SET_PARAM", "E_RANGE", pgen.BY_KEY["afe.rate_tol_pct"].id)
    rep = h.result(be.config.write_and_verify_async({"afe.rate_tol_pct": 31}))
    assert rep.items[0].status == WriteStatus.REJECTED and "afe.rate_tol_pct" in rep.items[0].text
    sim.inject_store_mismatch("afe.rate_tol_pct", 33)
    rep = h.result(be.config.write_and_verify_async({"afe.rate_tol_pct": 32}))
    assert rep.items[0].status == WriteStatus.MISMATCH and rep.items[0].stored == 33
    sim.act("inject", fault="drop_next", cmd="SET_PARAM", what="response", n=3)
    rep = h.result(be.config.write_and_verify_async({"afe.rate_tol_pct": 34}))
    assert rep.items[0].status == WriteStatus.TIMEOUT
    assert not rep.ok


@pytest.mark.req("SW-CFG-003")
def test_e_config_item_retried_in_a_further_pass(be) -> None:
    h, sim = be.test_hooks, be.sim
    sim.inject_nack("SET_PARAM", "E_CONFIG", pgen.BY_KEY["afe.rate_sps"].id, count=1)
    rep = h.result(be.config.write_and_verify_async({"afe.rate_tol_pct": 21, "stream.fallback_hz": 5}))
    assert {i.key: i.status for i in rep.items} == {"afe.rate_tol_pct": WriteStatus.OK,
                                                    "stream.fallback_hz": WriteStatus.OK}
    keys = [P.decode_param_entry(w.frame[6:13]).key for w in tx_frames(be, Cmd.SET_PARAM)]
    assert keys.count("afe.rate_tol_pct") == 2 and keys[-1] == "afe.rate_tol_pct"   # retried after the others


@pytest.mark.req("SW-CFG-004")
def test_nvm_save_load_defaults(be) -> None:
    h = be.test_hooks
    h.result(be.config.write_and_verify_async({"afe.rate_tol_pct": 40}))
    assert be.status().cfg_dirty is True
    h.result(be.config.save_async())
    st = be.status()
    assert st.cfg_dirty is False and st.board.nvm_record_seq == 1 and st.nvm_defaulted is False
    h.result(be.config.write_and_verify_async({"afe.rate_tol_pct": 41}))
    vals = h.result(be.config.load_async())
    assert vals["afe.rate_tol_pct"] == 40 and be.status().cfg_dirty is False
    vals = h.result(be.config.defaults_async())
    assert vals["afe.rate_tol_pct"] == 20 and be.status().cfg_dirty is True
    # DEFAULT resets the session values: the backend re-sent them (ICD §11.5)
    assert be.device.thresholds.state == "DEFAULT_ONLY"


@pytest.mark.req("SW-CFG-004")
def test_save_response_lost_resolved_by_status(be) -> None:
    h = be.test_hooks
    be.sim.act("inject", fault="drop_next", cmd="SAVE_PARAMS", what="response")
    h.result(be.config.save_async(), 8000)                    # executed: proven by GET_STATUS
    assert len(tx_frames(be, Cmd.SAVE_PARAMS)) == 1            # never re-sent
    be.sim.act("inject", fault="drop_next", cmd="SAVE_PARAMS", what="request")
    with pytest.raises(CommandNotExecuted):
        h.result(be.config.save_async(), 8000)
    assert len(tx_frames(be, Cmd.SAVE_PARAMS)) == 2


@pytest.mark.req("SW-CFG-004", "SW-PLT-003")
def test_load_without_record_and_reboot_resync(be) -> None:
    h = be.test_hooks
    with pytest.raises(LinkError):
        h.result(be.config.load_async())                       # E_NVM 1 (no record)
    h.result(be.config.reboot_async(), 5000)
    h.advance(300)
    st = be.status()
    assert st.link.state == LinkState.CONNECTED and st.stream.on
    assert st.board.reset_cause == pg.ResetCause.SOFTWARE
    assert not tx_frames(be, Cmd.ENABLE) and not tx_frames(be, Cmd.HOME)


@pytest.mark.req("SW-STOP-001", "IF-011")
def test_stop_halt_pause_priority_and_confirmed(be) -> None:
    h = be.test_hooks
    for fn, cmd in ((be.stop, Cmd.STOP), (be.halt, Cmd.HALT), (be.pause, Cmd.PAUSE)):
        r = fn("test")
        assert r.sent and r.cmd == cmd.name and r.t_write_ns is not None
        h.advance(20)
    topics = [e.payload for e in be.events.history("stop.confirmed")]
    assert topics == ["STOP", "HALT", "PAUSE"]
    h.advance(1100)
    st = be.status()
    assert st.indicators.halt.state == "ON" and st.indicators.halt.source == "PC"
    assert st.indicators.paused.state == "ON" and st.indicators.paused.source == "PC"
    assert not st.gates[GateId.RESUME].ok                       # HALT latched → Clear stop first (D-31)


@pytest.mark.req("SW-STOP-002")
def test_halt_unconfirmed_with_stream_off_polls_status(be) -> None:
    h = be.test_hooks
    h.result(be.stream_stop_async())
    be.sim.act("inject", fault="link_silence", duration_ms=2000)
    r = be.halt("hotkey")
    assert r.sent
    h.advance(1200)
    assert [e.payload for e in be.events.history("stop.unconfirmed")] == ["HALT"]
    assert 15 <= len(tx_frames(be, Cmd.HALT)) <= 20
    assert tx_frames(be, Cmd.GET_STATUS)


@pytest.mark.req("SW-STOP-003", "IF-005")
def test_halt_clear_dropped_response_one_frame_and_resolution(be) -> None:
    h = be.test_hooks
    be.halt("test")
    h.advance(50)
    be.sim.act("inject", fault="drop_next", cmd="HALT_CLEAR", what="response")
    res = h.result(be.clear_stop_async())
    assert res.confirmed and res.outcome == "OK"                # executed, proven by GET_STATUS
    assert len(tx_frames(be, Cmd.HALT_CLEAR)) == 1              # never re-sent (D-34)
    # latch still set (request lost) → NOT_CONFIRMED, still exactly one frame per click
    be.halt("test")
    h.advance(50)
    be.sim.act("inject", fault="drop_next", cmd="HALT_CLEAR", what="request")
    res = h.result(be.clear_stop_async())
    assert not res.confirmed and res.outcome == "NOT_CONFIRMED" and "click again" in res.text
    assert len(tx_frames(be, Cmd.HALT_CLEAR)) == 2


@pytest.mark.req("SW-STOP-003")
def test_clears_refused_and_fault_clear(be) -> None:
    h, sim = be.test_hooks, be.sim
    sim.act("button", name="stop", pressed=True)
    h.advance(50)
    res = h.result(be.clear_stop_async())
    assert res.outcome == "REFUSED" and "STOP button" in res.text
    sim.act("button", name="stop", pressed=False)
    h.advance(50)
    assert h.result(be.clear_stop_async()).confirmed
    sim.act("afe", saturate="pos")
    h.advance(100)
    sim.act("afe", saturate=None)
    h.advance(100)
    assert be.status().board.faults & pg.Faults.LOAD_LIMIT
    res = h.result(be.fault_clear_async())
    assert res.confirmed and res.cleared == ("LOAD_LIMIT",)
    with pytest.raises(Exception):
        h.result(be.estop_clear_async(confirmed=False))
    sim.act("estop", open=True)
    h.advance(100)
    sim.act("estop", open=False)
    h.advance(50)
    assert h.result(be.estop_clear_async(confirmed=True)).outcome == "REFUSED"   # closed < estop_release_ms
    h.advance(200)
    assert h.result(be.estop_clear_async(confirmed=True)).confirmed


@pytest.mark.req("SW-STOP-004")
def test_resume_and_resume_timeout_resolution(be) -> None:
    h = be.test_hooks
    be.pause("test")
    h.advance(60)
    g = be.resume("gui")
    assert g.ok
    h.advance(60)
    assert be.status().indicators.paused.state == "OFF"
    be.pause("test")
    h.advance(60)
    be.sim.act("inject", fault="drop_next", cmd="RESUME", what="response")
    be.resume("gui")
    h.advance(300)
    assert len(tx_frames(be, Cmd.RESUME)) == 2 and be.status().indicators.paused.state == "OFF"
    g = be.resume("gui")                                         # not paused → refused locally
    assert not g.ok and be.events.history("resume.ignored")


@pytest.mark.req("SAF-SW-003")
def test_heartbeat_gap_bounded_with_busy_general_lane(be) -> None:
    h = be.test_hooks
    h.result(be.stream_stop_async())
    for _ in range(40):
        be.config.read_all_async()
    h.advance(10_000)
    t = [w.t_ns for w in be.test_hooks.wire_log() if w.direction == "TX"]
    gaps = [b - a for a, b in zip(t, t[1:], strict=False)]
    assert max(gaps) <= 250_000_000
    assert tx_frames(be, Cmd.PING)


@pytest.mark.req("SAF-SW-003")
def test_heartbeat_stops_when_pipeline_is_stalled(be) -> None:
    h = be.test_hooks
    h.advance(500)
    n0 = len(tx_frames(be, Cmd.PING))
    t0 = be.clock.monotonic_ns()
    h.stall_thread("pipeline", 2000)
    h.advance(2000)
    pings = [w.t_ns for w in tx_frames(be, Cmd.PING) if t0 + 400_000_000 < w.t_ns < t0 + 1_990_000_000]
    assert pings == [] and len(tx_frames(be, Cmd.PING)) - n0 <= 3
    h.advance(500)
    assert len(tx_frames(be, Cmd.PING)) > n0


@pytest.mark.req("SAF-SW-003")
def test_data_loss_while_moving_sends_stop(be) -> None:
    h, sim = be.test_hooks, be.sim
    h.result(be.config.write_and_verify_async({"motion.jog_timeout_ms": 1000}))
    ch = be.device.channel
    h.result(ch.submit(Cmd.ENABLE))
    h.advance(600)
    h.result(ch.submit(Cmd.JOG, P.build_request(Cmd.JOG, v_um_s=1000, a_um_s2=0, bound_um=pg.JOG_NO_BOUND)))
    h.advance(100)
    assert be.status().motion.moving
    sim.act("inject", fault="tx_congestion", duration_ms=900)
    h.advance(700)
    stops = tx_frames(be, Cmd.STOP)
    assert stops and any("LINK LOST" in str(e.payload) for e in be.events.history("log"))


@pytest.mark.req("SAF-SW-003", "SW-PLT-003")
def test_link_lost_and_reconnect(be) -> None:
    h = be.test_hooks
    be.sim_endpoint.pair.unplug()
    h.advance(50)
    assert be.status().link.state == LinkState.LOST
    be.sim_endpoint.pair.replug()
    h.advance(3000)
    st = be.status()
    assert st.link.state == LinkState.CONNECTED and st.stream.on
    assert not tx_frames(be, Cmd.ENABLE)


@pytest.mark.req("SW-PLT-003")
def test_no_board_answer() -> None:
    b = lockstep_backend(connect=False)
    try:
        f = b.connect_async("sim")
        b.test_hooks.advance(1)
        b.sim.act("inject", fault="hang", duration_ms=10_000)
        with pytest.raises(LinkError):
            b.test_hooks.result(f, 5000)
        assert b.status().link.state == LinkState.DISCONNECTED
    finally:
        b.shutdown()


@pytest.mark.req("SW-PLT-003")
def test_disconnect_and_set_valid(be) -> None:
    h = be.test_hooks
    assert be.motion.set_valid(True).ok
    h.advance(50)
    assert be.status().indicators.valid.state == "ON"
    be.stop("test")                                    # every operational stop clears VALID (FW)
    h.advance(50)
    assert be.status().indicators.valid.state == "OFF"
    h.result(be.disconnect_async())
    st = be.status()
    assert st.link.state == LinkState.DISCONNECTED and st.indicators.valid.state == "UNKNOWN"
    assert be.stop("gui").sent is False
    assert struct.calcsize("<I") == 4
