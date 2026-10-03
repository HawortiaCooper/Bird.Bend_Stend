"""Level C — link layer: retry classes, VERIFY resolution, CONFIRM repeats, priority path, corruption, frame
gaps / duplicates, heartbeat, reconnect (Validator F, M1).

Lock-step backend + simulator for the deterministic cases (wire log = ground truth via ``ref_codec``); F's
``FBoard`` on the real clock where the simulator vocabulary cannot produce the condition (link-attributed DATA
gaps, duplicate DATA frames, over-long / truncated frames).

TC-IF-004-02, TC-IF-005-01, TC-IF-005-02 (M1 part), TC-IF-005-03, TC-IF-007-01, TC-SW-PLT-003-02, TC-IF-011-01,
TC-NFR-002-02 (rt), TC-SAF-SW-003-01 (lock-step part, informative before M3), reconnect rule (§4.6).

Verifies: IF-004, IF-005, IF-007, IF-011, SW-PLT-003, NFR-002, SAF-SW-003
"""
from __future__ import annotations

import time

import pytest

import harness as H
import ref_codec as rc
from oracle.fboard import FBoard

MS = 1_000_000


def _resp_for(be, name, since=0):
    return H.rx(be, name, since=since, kind="response")


# ============================================================================================ RETRY class

@pytest.mark.req("IF-005")
def test_tc_if_005_01_retry_new_seq_then_success(vbe, pdict):
    """One dropped GET_ALL_PARAMS response → the request is re-sent after the 100 ms timeout with a NEW SEQ and
    the read succeeds (ICD §9.3 RETRY)."""
    # Verifies: IF-005
    H.act(vbe, "inject", fault="drop_next", cmd="GET_ALL_PARAMS", what="response", n=1)
    m0 = H.wire_mark(vbe)
    vals = H.result(vbe, H.read_all(vbe))
    assert len(vals) == len(pdict.params)
    p0 = [w for w in H.tx(vbe, "GET_ALL_PARAMS", since=m0) if w.fields["page"] == 0]
    assert len(p0) == 2 and p0[0].seq != p0[1].seq
    assert 100 * MS <= p0[1].t_ns - p0[0].t_ns <= 110 * MS
    assert H.stats(vbe).retries >= 1


@pytest.mark.req("IF-005")
def test_tc_if_005_01_three_drops_timeout_after_two_retries(vbe):
    """Three dropped responses → 1 + 2 attempts (new SEQ each) → CommandTimeout; command_timeouts counted."""
    # Verifies: IF-005
    from bend_stand.core.errors import CommandTimeout

    t0 = H.stats(vbe).command_timeouts
    H.act(vbe, "inject", fault="drop_next", cmd="GET_ALL_PARAMS", what="response", n=3)
    m0 = H.wire_mark(vbe)
    with pytest.raises(CommandTimeout):
        H.result(vbe, H.read_all(vbe))
    p0 = H.tx(vbe, "GET_ALL_PARAMS", since=m0)
    assert len(p0) == 3 and len({w.seq for w in p0}) == 3
    assert H.stats(vbe).command_timeouts == t0 + 1


@pytest.mark.req("IF-005")
def test_tc_if_005_01_late_response_ignored_and_counted(vbe, pdict):
    """A response delayed beyond the timeout belongs to an abandoned SEQ: it is ignored and counted
    (late_responses); the retry resolves the request exactly once."""
    # Verifies: IF-005
    l0 = H.stats(vbe).late_responses
    H.act(vbe, "inject", fault="delay_next", cmd="GET_ALL_PARAMS", what="response", n=1, ms=150)
    m0 = H.wire_mark(vbe)
    vals = H.result(vbe, H.read_all(vbe))
    H.advance(vbe, 200)
    assert len(vals) == len(pdict.params)
    assert H.stats(vbe).late_responses == l0 + 1
    p0 = [w for w in H.tx(vbe, "GET_ALL_PARAMS", since=m0) if w.fields["page"] == 0]
    assert len(p0) == 2


@pytest.mark.req("IF-005", "SAF-SW-003")
def test_tc_if_005_01_ping_retry(vbe):
    """A dropped PING response: the heartbeat PING is retried with a new SEQ ~100 ms later."""
    # Verifies: IF-005, SAF-SW-003
    H.act(vbe, "inject", fault="drop_next", cmd="PING", what="response", n=1)
    m0 = H.wire_mark(vbe)
    H.advance(vbe, 1000)
    pings = H.tx(vbe, "PING", since=m0)
    answered = {w.seq for w in _resp_for(vbe, "PING", m0)}
    lost = [w for w in pings if w.seq not in answered]
    assert len(lost) == 1
    nxt = [w for w in pings if w.t_ns > lost[0].t_ns][0]
    assert nxt.seq != lost[0].seq and 95 * MS <= nxt.t_ns - lost[0].t_ns <= 110 * MS


@pytest.mark.req("IF-005")
def test_tc_if_005_03_duplicated_response_single_resolution(vbe, pdict):
    """FI-03: a duplicated response is ignored; exactly one resolution per SEQ; the following request is not
    confused by the copy."""
    # Verifies: IF-005
    H.act(vbe, "inject", fault="duplicate_next", cmd="GET_ALL_PARAMS", what="response", n=1)
    m0 = H.wire_mark(vbe)
    vals = H.result(vbe, H.read_all(vbe))
    H.advance(vbe, 100)
    assert len(vals) == len(pdict.params)
    r0 = [w for w in _resp_for(vbe, "GET_ALL_PARAMS", m0) if w.fields["page"] == 0]
    assert len(r0) == 2 and r0[0].seq == r0[1].seq                  # the copy really was on the wire
    assert len(H.tx(vbe, "GET_ALL_PARAMS", since=m0)) == 3          # pages 0, 1, 2 — nothing re-sent
    assert H.stats(vbe).command_timeouts == 0


# ============================================================================================ VERIFY class

@pytest.mark.req("IF-005", "SW-CFG-004")
def test_tc_if_005_02_save_response_lost_resolved_by_get_status(vbe):
    """SAVE_PARAMS (VERIFY): response dropped → never re-sent; GET_STATUS after the 3 s timeout shows
    nvm_record_seq + 1 and CFG_DIRTY 0 → resolved as executed."""
    # Verifies: IF-005, SW-CFG-004
    H.advance(vbe, 1200)
    seq0 = H.status(vbe).board.nvm_record_seq
    assert H.result(vbe, H.write_verify(vbe, {"io.release_ms": 31})).ok
    H.act(vbe, "inject", fault="drop_next", cmd="SAVE_PARAMS", what="response", n=1)
    m0 = H.wire_mark(vbe)
    H.result(vbe, H.save_nvm(vbe))
    saves = H.tx(vbe, "SAVE_PARAMS", since=m0)
    assert len(saves) == 1
    gs = [w for w in H.tx(vbe, "GET_STATUS", since=m0) if w.t_ns >= saves[0].t_ns + 3000 * MS]
    assert gs, "outcome must be resolved by GET_STATUS"
    st = H.status(vbe)
    assert st.board.nvm_record_seq == seq0 + 1 and st.cfg_dirty is False


@pytest.mark.req("IF-005", "SW-CFG-004")
def test_tc_if_005_02_save_request_lost_not_executed_never_resent(vbe):
    """SAVE_PARAMS request lost → one frame only; GET_STATUS shows no new record → 'not executed' error; no
    automatic re-send."""
    # Verifies: IF-005, SW-CFG-004
    H.advance(vbe, 1200)
    seq0 = H.status(vbe).board.nvm_record_seq
    H.act(vbe, "inject", fault="drop_next", cmd="SAVE_PARAMS", what="request", n=1)
    m0 = H.wire_mark(vbe)
    with pytest.raises(Exception) as ei:
        H.result(vbe, H.save_nvm(vbe))
    assert "not executed" in str(ei.value).lower() or "NotExecuted" in type(ei.value).__name__
    H.advance(vbe, 3000)
    assert len(H.tx(vbe, "SAVE_PARAMS", since=m0)) == 1
    assert H.status(vbe).board.nvm_record_seq == seq0


@pytest.mark.req("IF-005", "SW-CFG-004")
@pytest.mark.parametrize("cmd", ["DEFAULT_PARAMS", "LOAD_PARAMS", "REBOOT"])
def test_tc_if_005_02_nvm_and_reboot_response_lost_single_frame(vbe, cmd):
    """DEFAULT/LOAD_PARAMS / REBOOT (VERIFY): response dropped → exactly one frame; resolved by the EVENT
    (PARAMS_DEFAULTED / PARAMS_LOADED / BOOT)."""
    # Verifies: IF-005, SW-CFG-004
    if cmd == "LOAD_PARAMS":
        H.result(vbe, H.save_nvm(vbe))
    H.act(vbe, "inject", fault="drop_next", cmd=cmd, what="response", n=1)
    m0 = H.wire_mark(vbe)
    fut = {"DEFAULT_PARAMS": H.defaults, "LOAD_PARAMS": H.load_nvm, "REBOOT": H.reboot}[cmd](vbe)
    H.result(vbe, fut, 8000)
    H.advance(vbe, 1500)
    assert len(H.tx(vbe, cmd, since=m0)) == 1
    ev = {"DEFAULT_PARAMS": "PARAMS_DEFAULTED", "LOAD_PARAMS": "PARAMS_LOADED", "REBOOT": "BOOT"}[cmd]
    assert any(w.fields.get("code") == ev for w in H.rx(vbe, "EVENT", since=m0))


def _log_texts(be):
    return [str(getattr(r, "payload", "")) for r in H.history(be, "log")]


@pytest.mark.req("IF-005", "SW-STOP-004")
def test_tc_if_005_02_resume_response_lost_resolved_never_resent(vbe):
    """RESUME (VERIFY, D-31): response dropped → one RESUME frame; GET_STATUS shows PAUSED = 0 → done."""
    # Verifies: IF-005, SW-STOP-004 (M1 manual part)
    assert H.pause(vbe).sent
    assert H.run_until(vbe, lambda: H.status(vbe).motion.paused, 500)
    assert H.gate(vbe, "RESUME").ok
    H.act(vbe, "inject", fault="drop_next", cmd="RESUME", what="response", n=1)
    m0 = H.wire_mark(vbe)
    assert H.resume(vbe).ok
    H.advance(vbe, 500)
    assert len(H.tx(vbe, "RESUME", since=m0)) == 1
    assert not H.status(vbe).motion.paused
    assert H.tx(vbe, "GET_STATUS", since=m0)


@pytest.mark.req("IF-005", "SW-STOP-004")
def test_tc_if_005_02_resume_race_new_pause_not_cleared(vbe):
    """D-31 race: the RESUME response is lost and the physical PAUSE is pressed right after the RESUME executed:
    one RESUME frame only, PAUSED stays set, the outcome is 'not confirmed' (never re-sent)."""
    # Verifies: IF-005, SW-STOP-004
    assert H.pause(vbe).sent
    assert H.run_until(vbe, lambda: H.status(vbe).motion.paused, 500)
    H.act(vbe, "inject", fault="drop_next", cmd="RESUME", what="response", n=1)
    H.act(vbe, "on_frame", cmd="RESUME", nth=1, delay_us=2000,
          then={"action": "button", "name": "pause", "pressed": True})
    m0 = H.wire_mark(vbe)
    assert H.resume(vbe).ok
    H.advance(vbe, 60)
    H.act(vbe, "button", name="pause", pressed=False)
    H.advance(vbe, 600)
    assert len(H.tx(vbe, "RESUME", since=m0)) == 1
    assert H.status(vbe).motion.paused
    assert any("NOT_CONFIRMED" in t for t in _log_texts(vbe))


@pytest.mark.req("IF-005", "IF-011", "SW-STOP-003", "SW-STOP-002")
def test_tc_if_005_02_halt_clear_lost_new_halt_not_wiped(vbe):
    """D-34 (v0.3, CR-01: no STOP button — the new HALT now comes from the Pause/Break key = ``halt()``): the
    HALT_CLEAR response is lost and a new HALT is pressed right after the clear executed → exactly one HALT_CLEAR
    frame (priority path, never re-sent), the new HALT follows it on the wire, the HALT stays latched."""
    # Verifies: IF-005, IF-011, SW-STOP-003, SW-STOP-002
    assert H.halt(vbe).sent
    assert H.run_until(vbe, lambda: H.indicator(vbe, "halt").state == "ON", 500)
    H.act(vbe, "inject", fault="drop_next", cmd="HALT_CLEAR", what="response", n=1)
    m0 = H.wire_mark(vbe)
    fut = H.clear_stop(vbe)
    H.advance(vbe, 3)                          # the clear has executed at the board; its response is lost
    assert H.halt(vbe).sent                    # new HALT (Pause/Break key) before the VERIFY resolution
    res = H.result(vbe, fut)
    H.advance(vbe, 300)
    clears, halts = H.tx(vbe, "HALT_CLEAR", since=m0), H.tx(vbe, "HALT", since=m0)
    assert len(clears) == 1 and halts and clears[0].t_ns < halts[0].t_ns
    assert not (res.confirmed and str(res.outcome) == "OK" and H.indicator(vbe, "halt").state != "ON")
    assert H.indicator(vbe, "halt").state == "ON"


@pytest.mark.req("IF-005", "IF-011")
def test_tc_if_005_02_clears_response_lost_resolved_by_status(vbe):
    """HALT_CLEAR / FAULT_CLEAR responses lost without a new latch → one frame each, resolved OK by GET_STATUS
    (FAULT_CLEAR reports the cleared fault names)."""
    # Verifies: IF-005, IF-011
    assert H.halt(vbe).sent
    assert H.run_until(vbe, lambda: H.indicator(vbe, "halt").state == "ON", 500)
    H.act(vbe, "inject", fault="drop_next", cmd="HALT_CLEAR", what="response", n=1)
    m0 = H.wire_mark(vbe)
    res = H.result(vbe, H.clear_stop(vbe))
    assert res.confirmed and str(res.outcome) == "OK" and len(H.tx(vbe, "HALT_CLEAR", since=m0)) == 1
    H.act(vbe, "inject", fault="step_fault")
    assert H.run_until(vbe, lambda: H.status(vbe).board is not None and H.status(vbe).board.faults != 0, 2000)
    H.act(vbe, "inject", fault="drop_next", cmd="FAULT_CLEAR", what="response", n=1)
    m1 = H.wire_mark(vbe)
    res = H.result(vbe, H.fault_clear(vbe))
    assert res.confirmed and "STEP_FAULT" in res.cleared and len(H.tx(vbe, "FAULT_CLEAR", since=m1)) == 1


@pytest.mark.req("IF-005", "IF-011")
def test_tc_if_005_02_estop_clear_lost_new_estop_not_wiped(vbe):
    """ESTOP_CLEAR response lost and the E-stop opened again right after → one frame, NOT_CONFIRMED."""
    # Verifies: IF-005, IF-011
    H.act(vbe, "estop", open=True)
    H.advance(vbe, 100)
    H.act(vbe, "estop", open=False)
    H.advance(vbe, 400)
    assert H.indicator(vbe, "estop").state == "ON"
    H.act(vbe, "inject", fault="drop_next", cmd="ESTOP_CLEAR", what="response", n=1)
    H.act(vbe, "on_frame", cmd="ESTOP_CLEAR", nth=1, delay_us=2000, then={"action": "estop", "open": True})
    m0 = H.wire_mark(vbe)
    res = H.result(vbe, H.estop_clear(vbe, True))
    assert not res.confirmed and len(H.tx(vbe, "ESTOP_CLEAR", since=m0)) == 1
    H.act(vbe, "estop", open=False)


@pytest.mark.req("IF-005", "IF-009")
def test_tc_if_005_02_no_motion_command_from_the_m1_api(vbe):
    """M1: every motion entry point refuses locally (gate NOT_IMPLEMENTED) — no motion frame ever reaches the wire
    (the M2 part of TC-IF-005-02 runs when motion exists)."""
    # Verifies: IF-005, IF-009
    m0 = H.wire_mark(vbe)
    for fn in (lambda: vbe.motion.move_to(10.0), lambda: vbe.motion.move_by(1.0),
               lambda: vbe.motion.home(load_confirmed=True)):
        f = fn()
        with pytest.raises(Exception):
            f.result(0)
    vbe.motion.jog_start(1, 1.0)
    vbe.motion.jog_stop()
    assert not vbe.motion.enable().ok
    H.advance(vbe, 500)
    names = {w.name for w in H.tx(vbe, since=m0)}
    assert not names & {"MOVE_ABS", "MOVE_UNTIL_LOAD", "JOG", "HOME", "ENABLE", "DISABLE"}


# ============================================================================================ CONFIRM class

@pytest.mark.req("IF-005", "SW-STOP-002")
@pytest.mark.parametrize("cmd", ["HALT", "PAUSE"])
def test_confirm_class_repeats_every_50ms_until_confirmed(vbe, cmd):
    """CONFIRM (ICD §9.3/§9.4): request lost 3× → repeated every 50 ms with new SEQs until the ACK (4 frames)."""
    # Verifies: IF-005, SW-STOP-002 (repeat part)
    H.act(vbe, "inject", fault="drop_next", cmd=cmd, what="request", n=3)
    m0 = H.wire_mark(vbe)
    r = H.halt(vbe) if cmd == "HALT" else H.pause(vbe)
    assert r.sent
    H.advance(vbe, 600)
    fr = H.tx(vbe, cmd, since=m0)
    assert len(fr) == 4 and len({w.seq for w in fr}) == 4
    gaps = [(b.t_ns - a.t_ns) / MS for a, b in zip(fr, fr[1:])]
    assert all(49 <= g <= 56 for g in gaps), gaps
    assert any(rec.payload.cmd == cmd for rec in H.history(vbe, "stop.confirmed"))


@pytest.mark.req("IF-005", "SW-STOP-002")
def test_confirm_class_alarm_after_1s_at_most_20_tries(vbe):
    """All HALT requests lost: ≤ 20 attempts in 1 s, then stop.unconfirmed + alarm text (SW-STOP-002, §9.4)."""
    # Verifies: IF-005, SW-STOP-002
    H.act(vbe, "inject", fault="drop_next", cmd="HALT", what="request", n=100)
    m0 = H.wire_mark(vbe)
    t0 = H.now_ns(vbe)
    H.halt(vbe)
    H.advance(vbe, 1500)
    fr = H.tx(vbe, "HALT", since=m0)
    assert 2 <= len([w for w in fr if w.t_ns - t0 <= 1000 * MS]) <= 20
    assert len(fr) <= 20
    assert any(rec.payload.cmd == "HALT" and rec.payload.confirmed is False for rec in H.history(vbe, "stop.unconfirmed"))


@pytest.mark.req("IF-005", "SW-STOP-002")
def test_confirm_class_stream_off_polls_get_status(vbe):
    """Stream off + HALT request lost twice: repeats continue and GET_STATUS is polled between them (§9.4);
    confirmed by ACK / flags.HALT."""
    # Verifies: IF-005, SW-STOP-002
    H.result(vbe, H.stream(vbe, False))
    H.advance(vbe, 300)
    H.act(vbe, "inject", fault="drop_next", cmd="HALT", what="request", n=2)
    m0 = H.wire_mark(vbe)
    H.halt(vbe)
    H.advance(vbe, 400)
    ws = H.tx(vbe, since=m0)
    halts = [w for w in ws if w.name == "HALT"]
    assert len(halts) == 3
    between = [w for w in ws if w.name == "GET_STATUS" and halts[0].t_ns < w.t_ns < halts[-1].t_ns]
    assert between
    assert any(rec.payload.cmd == "HALT" and rec.payload.confirmed for rec in H.history(vbe, "stop.confirmed"))


# ============================================================================================ IF-011 priority

def _fill_queues(be):
    """Busy link: 40 parameter reads queued (Worker job queue non-empty, GENERAL lane busy) at a request rate
    above the 100 frames/s token bucket, so the bucket is empty when the priority call is made."""
    futs = [H.read_all(be) for _ in range(40)]
    futs.append(H.write_verify(be, {"io.release_ms": 41, "io.estop_release_ms": 141, "stream.fallback_hz": 11}))
    return futs


def _bucket_limited(be, window_ms=200) -> bool:
    t_end = H.now_ns(be)
    tx = [w for w in H.tx(be) if w.t_ns > t_end - window_ms * MS]
    return len(tx) <= 100 * window_ms / 1000 + 3                    # TX rate held at the 100 /s bucket


@pytest.mark.req("IF-011", "NFR-002", "SW-STOP-001")
@pytest.mark.parametrize("prio", ["STOP", "HALT", "PAUSE"])
def test_tc_if_011_01_priority_frame_is_next_with_full_queues(vbe, prio):
    """TC-IF-011-01: with the job queue and the GENERAL lane busy and the token bucket empty, STOP / HALT /
    PAUSE is written at once (same instant as the call) and is the next frame on the wire."""
    # Verifies: IF-011, NFR-002, SW-STOP-001
    futs = _fill_queues(vbe)
    H.advance(vbe, 400)
    assert _bucket_limited(vbe) and not all(f.done() for f in futs)
    m0 = H.wire_mark(vbe)
    t_call = H.now_ns(vbe)
    {"STOP": H.stop, "HALT": H.halt, "PAUSE": H.pause}[prio](vbe)
    nxt = H.tx(vbe, since=m0)
    assert nxt and nxt[0].name == prio and nxt[0].t_ns == t_call, [w.name for w in nxt]
    H.advance(vbe, 3000)


@pytest.mark.req("IF-011", "IF-005")
@pytest.mark.defect("SWD-M1-04")
@pytest.mark.parametrize("cmd", ["HALT_CLEAR", "FAULT_CLEAR", "ESTOP_CLEAR"])
def test_tc_if_011_01_clears_not_delayed_by_queues(vbe, cmd):
    """Regression SWD-M1-04 (ICD §2.4, D-34): the clear frames are written at the call instant on the priority
    path — never behind Worker jobs, lanes or the token bucket — with queued parameter jobs and an empty bucket."""
    # Verifies: IF-011, IF-005
    H.halt(vbe)
    H.advance(vbe, 200)
    _fill_queues(vbe)
    H.advance(vbe, 400)
    assert _bucket_limited(vbe)
    m0 = H.wire_mark(vbe)
    t_call = H.now_ns(vbe)
    {"HALT_CLEAR": lambda: H.clear_stop(vbe), "FAULT_CLEAR": lambda: H.fault_clear(vbe),
     "ESTOP_CLEAR": lambda: H.estop_clear(vbe, True)}[cmd]()
    nxt = H.tx(vbe, since=m0)
    assert nxt and nxt[0].name == cmd and nxt[0].t_ns == t_call, [w.name for w in nxt][:5]
    H.advance(vbe, 3000)


@pytest.mark.req("IF-011", "SW-STOP-004")
@pytest.mark.defect("SWD-M1-04")
def test_tc_if_011_01_resume_on_control_lane_not_behind_jobs(vbe):
    """RESUME stays on the CONTROL lane (Orchestrator decision, ICD §2.4 / §9.3; SRS IF-011 wording to follow in
    v0.5): it is submitted at once, never behind Worker jobs — with 40 queued reads it goes out within one token
    interval of the 100 frames/s bucket (≤ 10 ms + one frame), ahead of the queued GENERAL requests."""
    # Verifies: IF-011, SW-STOP-004
    H.pause(vbe)
    H.advance(vbe, 200)
    futs = _fill_queues(vbe)
    H.advance(vbe, 400)
    assert _bucket_limited(vbe) and not all(f.done() for f in futs)
    m0 = H.wire_mark(vbe)
    t_call = H.now_ns(vbe)
    assert H.resume(vbe).ok
    H.advance(vbe, 30)
    res = H.tx(vbe, "RESUME", since=m0)
    assert len(res) == 1 and res[0].t_ns - t_call <= 12 * MS
    before = [w for w in H.tx(vbe, since=m0) if w.t_ns < res[0].t_ns and w.name == "GET_ALL_PARAMS"]
    assert len(before) <= 1
    H.advance(vbe, 3000)


@pytest.mark.req("IF-011")
def test_tc_if_011_02_fw_to_pc_bandwidth_below_10_percent(vbe):
    """TC-IF-011-02: 60 s streaming + status polls + heartbeat: FW→PC bytes ≤ 10 % of 92 160 B/s; DATA ≈ 26 B ×
    80.4 Hz (ICD §10)."""
    # Verifies: IF-011
    m0 = H.wire_mark(vbe)
    t0 = H.now_ns(vbe)
    H.advance(vbe, 60_000, 2)
    dt = (H.now_ns(vbe) - t0) / 1e9
    rx_b = sum(len(w.raw) for w in H.rx(vbe, since=m0))
    data_b = sum(len(w.raw) for w in H.rx(vbe, "DATA", since=m0))
    assert rx_b / dt <= 0.10 * 92_160
    assert all(len(w.raw) == 26 for w in H.rx(vbe, "DATA", since=m0))
    assert data_b / dt == pytest.approx(26 * 80.4, rel=0.01)


# ============================================================================================ corruption / counters

@pytest.mark.req("IF-004", "SW-PLT-003", "IF-005")
def test_tc_if_004_02_corrupt_response_dropped_counted_and_retried(vbe, pdict):
    """FI-04: a corrupted GET_ALL_PARAMS response is dropped and counted (crc_errors + 1, exact); no state change;
    the request is retried (RETRY) and succeeds."""
    # Verifies: IF-004, SW-PLT-003, IF-005
    c0 = H.stats(vbe).crc_errors
    H.act(vbe, "inject", fault="corrupt_next", cmd="GET_ALL_PARAMS", what="response", n=1)
    m0 = H.wire_mark(vbe)
    vals = H.result(vbe, H.read_all(vbe))
    assert len(vals) == len(pdict.params) and H.stats(vbe).crc_errors == c0 + 1
    p0 = [w for w in H.tx(vbe, "GET_ALL_PARAMS", since=m0) if w.fields["page"] == 0]
    assert len(p0) == 2 and p0[0].seq != p0[1].seq


@pytest.mark.req("IF-004")
def test_tc_if_004_02_corrupt_pc_frame_counted_by_fw_no_action(vbe):
    """A corrupted PC→FW HALT frame (bad CRC) reaching the FW: no action (no HALT latched, no response), the FW
    rx_crc_errors counter in GET_STATUS increments."""
    # Verifies: IF-004
    H.advance(vbe, 1100)
    e0 = H.stats(vbe).fw_rx_crc_errors
    bad = bytearray(rc.make_frame("HALT", 0x55, {}))
    bad[-1] ^= 0xFF
    m0 = H.wire_mark(vbe)
    H.act(vbe, "rx_bytes", hex=bytes(bad).hex())
    H.advance(vbe, 1500)
    assert not [w for w in H.rx(vbe, since=m0) if w.kind == "response" and w.seq == 0x55]
    assert H.indicator(vbe, "halt").state == "OFF"
    assert H.stats(vbe).fw_rx_crc_errors == e0 + 1


@pytest.fixture
def fb_link(pdict, tmp_path):
    made = []

    def make(**kw):
        fb = FBoard(pdict, **kw)
        be = H.realtime_backend(recordings_root=str(tmp_path / "rec"))
        made.append((fb, be))
        H.connect(be, fb.endpoint).result(10)
        assert H.wait_rt(lambda: H.status(be).stream.on, 5)
        return fb, be

    yield make
    for fb, be in made:
        try:
            be.shutdown()
        finally:
            fb.close()


@pytest.mark.req("SW-PLT-003", "IF-003", "IF-004")
def test_tc_sw_plt_003_02_exact_counts_crc_len_timeout(fb_link):
    """TC-SW-PLT-003-02: link statistics change by exactly the injected amounts — 2 CRC-corrupted frames,
    1 over-long LEN header, 1 truncated frame followed by silence (inter-byte timeout)."""
    # Verifies: SW-PLT-003, IF-003, IF-004
    fb, be = fb_link()
    H.stream(be, False).result(5)
    time.sleep(0.2)
    s0 = H.stats(be)
    good = rc.make_frame("EVENT", 3, {"t_us": 1, "code": "CLK_FALLBACK", "arg": 0, "value": 0, "value2": 0})
    bad = bytearray(good)
    bad[-2] ^= 0x01
    fb.inject_raw(bytes(bad) + bytes(bad))
    time.sleep(0.1)
    fb.inject_raw(bytes([0xA5, 0x5A, 0xC1, 0x07, 0xA1, 0x00]))      # LEN 161 > 160
    time.sleep(0.1)
    fb.inject_raw(good[:10], hold_s=0.06)                             # truncated, then silence ≥ 20 ms
    time.sleep(0.3)
    s1 = H.stats(be)
    assert s1.crc_errors - s0.crc_errors == 2
    assert s1.len_errors - s0.len_errors == 1
    assert s1.timeout_drops - s0.timeout_drops == 1


@pytest.mark.req("IF-007", "SW-PLT-003", "SW-ACQ-004")
def test_tc_if_007_01_link_gaps_duplicates_and_u16_wrap(fb_link):
    """TC-IF-007-01 / FI-03 (F-board): frame_seq gaps without OVERRUN are counted as link losses (exactly the
    skipped count), duplicates are dropped and counted, the u16 wrap is not a loss and not an anomaly."""
    # Verifies: IF-007, SW-PLT-003, SW-ACQ-004
    fb, be = fb_link(seq_start=65_400, skip_every=50, dup_every=37, max_frames=400)
    assert H.wait_rt(lambda: fb.unique_sent >= 400, 15)
    time.sleep(0.3)
    s = H.stats(be)
    assert s.frames_lost_link == fb.skipped and fb.skipped == 8
    assert s.frames_lost_fw == 0
    assert s.dup_frames == fb.dups
    assert s.seq_anomalies == 0
    assert s.data_frames == fb.unique_sent
    assert any(q < 100 for q in fb.sent_seqs) and any(q > 65_000 for q in fb.sent_seqs)   # wrapped


def _sent_data(be):
    return [f for f in H.sim_sent(be) if f["type"] == 0xC0]


@pytest.mark.req("IF-007", "SW-ACQ-004")
def test_tc_if_007_01_fw_overrun_losses_attributed_to_fw(vbe):
    """FI-01: TX congestion in the FW drops DATA frames (frame_seq advances, OVERRUN in the next frame): the SW
    counts exactly the frames the simulator dropped as FW losses, none as link losses."""
    # Verifies: IF-007, SW-ACQ-004
    n0 = len(_sent_data(vbe))
    s0 = H.stats(vbe)
    H.act(vbe, "inject", fault="tx_congestion", duration_ms=200)
    H.advance(vbe, 1000)
    dropped = sum(1 for f in _sent_data(vbe)[n0:] if f["dropped"])
    s1 = H.stats(vbe)
    assert dropped >= 10
    assert s1.frames_lost_fw - s0.frames_lost_fw == dropped
    assert s1.frames_lost_link == s0.frames_lost_link


@pytest.mark.req("IF-007")
def test_tc_if_007_01_missed_conversions_counted(vbe):
    """FI-01: every 50th HX711 conversion missing (no frame due, no seq gap): counted as missed conversions with
    the R4 §9 rule Δt > 1.5 × median period (F's oracle on the simulator's sent-frame times)."""
    # Verifies: IF-007
    n0 = len(_sent_data(vbe))
    a0 = H.stats(vbe).afe_missed
    H.act(vbe, "afe", drop_every=50)
    H.advance(vbe, 10_000, 2)
    H.act(vbe, "afe", drop_every=0)
    H.advance(vbe, 100)
    ts = [f["t_us"] for f in _sent_data(vbe)[n0 - 1:] if not f["dropped"]]
    dts = sorted(b - a for a, b in zip(ts, ts[1:]))
    med = dts[len(dts) // 2]
    exp = sum(max(1, round((b - a) / med) - 1) for a, b in zip(ts, ts[1:]) if b - a > 1.5 * med)
    assert exp >= 15
    assert H.stats(vbe).afe_missed - a0 == exp


# ============================================================================================ heartbeat / reconnect

@pytest.mark.req("SAF-SW-003", "IF-005")
@pytest.mark.parametrize("stream_on", [True, False])
def test_heartbeat_gap_and_idle_rule(vbe, stream_on):
    """ICD §9.2 / SAF-SW-003: over 120 s of virtual time the gap between consecutive PC→FW frames never
    exceeds 250 ms; every PING follows ≥ 150 ms of TX idle."""
    # Verifies: SAF-SW-003, IF-005
    if not stream_on:
        H.result(vbe, H.stream(vbe, False))
    m0 = H.wire_mark(vbe)
    H.advance(vbe, 120_000, 2)
    tx = H.tx(vbe, since=m0)
    gaps = [(b.t_ns - a.t_ns) / MS for a, b in zip(tx, tx[1:])]
    assert max(gaps) <= 250, max(gaps)
    for a, b in zip(tx, tx[1:]):
        if b.name == "PING":
            assert (b.t_ns - a.t_ns) / MS >= 150 or a.name == "PING"   # (retry of a lost PING is allowed)


@pytest.mark.req("SAF-SW-003")
def test_heartbeat_stops_when_pipeline_is_dead(vbe):
    """SAF-SW-003: heartbeat only while the receive/processing pipeline is alive — a stalled pipeline (1.5 s)
    produces a PC→FW silence > 1 s (the FW link watchdog can trip)."""
    # Verifies: SAF-SW-003
    m0 = H.wire_mark(vbe)
    H.stall(vbe, "pipeline", 1500)
    H.advance(vbe, 2500)
    tx = H.tx(vbe, since=m0)
    gaps = [(b.t_ns - a.t_ns) / MS for a, b in zip(tx, tx[1:])]
    assert max(gaps) >= 1000


@pytest.mark.req("SW-PLT-003", "SAF-SW-003")
def test_reconnect_after_link_loss_never_enables_or_moves(vbe):
    """§4.6 / ICD §9.3: board silent 1.5 s → LOST → automatic reconnect (connect sequence again); no ENABLE,
    HOME, motion, RESUME or clear is ever sent automatically."""
    # Verifies: SW-PLT-003, SAF-SW-003
    m0 = H.wire_mark(vbe)
    H.act(vbe, "inject", fault="hang", duration_ms=1500)
    assert H.run_until(vbe, lambda: H.link_state(vbe) == "LOST", 2000)
    assert H.run_until(vbe, lambda: H.link_state(vbe) == "CONNECTED" and H.status(vbe).stream.on, 8000)
    names = [w.name for w in H.tx(vbe, since=m0)]
    assert "GET_INFO" in names and "GET_ALL_PARAMS" in names
    assert not {"ENABLE", "HOME", "MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD", "RESUME", "HALT_CLEAR", "ESTOP_CLEAR",
                "FAULT_CLEAR"} & set(names)


# ============================================================================================ NFR-002 (rt)

@pytest.mark.rt
@pytest.mark.req("NFR-002", "IF-011")
def test_tc_nfr_002_02_backend_stop_latency_rt(tmp_path):
    """TC-NFR-002-02: 100 × Backend.stop() on the real clock (sim endpoint, streaming): call → STOP frame
    written ≤ 50 ms p95 (backend part; the GUI part is REF-PC PR-2)."""
    # Verifies: NFR-002, IF-011
    be = H.realtime_backend(recordings_root=str(tmp_path))
    try:
        H.connect(be, "sim").result(10)
        assert H.wait_rt(lambda: H.status(be).stream.on, 5)
        lat = []
        for _ in range(100):
            time.sleep(0.02)
            m0 = H.wire_mark(be)
            t0 = time.monotonic_ns()
            r = H.stop(be)
            assert r.sent
            st = [w for w in H.tx(be, "STOP", since=m0)]
            lat.append((st[0].t_ns - t0) / MS)
        lat.sort()
        assert lat[94] <= 50.0, lat[-5:]
    finally:
        be.shutdown()


# ============================================================================================ open defects

@pytest.mark.req("IF-011", "SW-STOP-001")
@pytest.mark.defect("SWD-M1-01")
def test_stop_attempted_while_link_lost(vbe):
    """The transport is open but no frame arrived for 1 s (LOST): an operator STOP must still be written
    (priority path never waits for the link state; the FW→PC direction may be the broken one)."""
    # Verifies: IF-011, SW-STOP-001
    H.act(vbe, "inject", fault="hang", duration_ms=3000)
    assert H.run_until(vbe, lambda: H.link_state(vbe) == "LOST", 2000)
    m0 = H.wire_mark(vbe)
    r = H.stop(vbe)
    assert r.sent and H.tx(vbe, "STOP", since=m0)


@pytest.mark.req("IF-005", "SW-STOP-001")
@pytest.mark.defect("SWD-M1-02")
def test_stop_lost_request_repeated_until_moving_clears(vbe):
    """CONFIRM class (ICD §9.3): a STOP whose request is lost must be repeated until ACK or MOVING = 0 *observed
    after the STOP*. Stimulus (forced path, motion is M2): STOP pressed 4 ms after a MOVE_ABS was accepted, before
    any DATA frame showed MOVING; the first STOP request is dropped by the board."""
    # Verifies: IF-005, SW-STOP-001
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 200_000))
    H.act(vbe, "inject", fault="drop_next", cmd="STOP", what="request", n=1)
    m0 = H.wire_mark(vbe)
    assert H.stop(vbe).sent
    H.advance(vbe, 1500)
    assert len(H.tx(vbe, "STOP", since=m0)) >= 2
    assert not H.status(vbe).motion.moving


@pytest.mark.req("SAF-SW-005")
@pytest.mark.defect("SWD-M1-05")
@pytest.mark.parametrize("latch", ["halt", "paused"])
def test_latch_source_within_200ms_of_its_event(vbe, latch):
    """SAF-SW-005: an indicator (HALT / PAUSED *with source*) updates ≤ 200 ms after its carrier — here the EVENT
    HALT_SET / PAUSED whose arg is the source (ICD §8.1)."""
    # Verifies: SAF-SW-005
    mp = H.wire_mark(vbe)                      # press just after a 1 Hz STATUS poll: the next one is ~1 s away
    assert H.run_until(vbe, lambda: bool(H.rx(vbe, "GET_STATUS", since=mp)), 1500)
    H.advance(vbe, 10)
    m0 = H.wire_mark(vbe)
    (H.halt if latch == "halt" else H.pause)(vbe)
    ev = "HALT_SET" if latch == "halt" else "PAUSED"
    assert H.run_until(vbe, lambda: any(w.fields.get("code") == ev for w in H.rx(vbe, "EVENT", since=m0)), 200)
    t_ev = [w for w in H.rx(vbe, "EVENT", since=m0) if w.fields.get("code") == ev][0].t_ns
    H.advance(vbe, 200 - (H.now_ns(vbe) - t_ev) / MS)
    ind = H.indicator(vbe, latch)
    assert ind.state == "ON" and ind.source == "PC", ind


# ============================================================================================ re-test (fix round 1)

@pytest.mark.req("IF-011", "SW-STOP-001")
@pytest.mark.defect("SWD-M1-01")
@pytest.mark.parametrize("state", ["DEGRADED", "LOST", "CONNECTING"])
def test_stop_halt_pause_written_in_every_link_state_with_open_transport(vbe, state):
    """Regression SWD-M1-01: STOP, HALT and PAUSE are written at the call instant in DEGRADED (PC→FW silence),
    LOST (board silent) and CONNECTING (automatic reconnect running)."""
    # Verifies: IF-011, SW-STOP-001
    if state == "DEGRADED":
        H.act(vbe, "inject", fault="link_silence", duration_ms=3000)
    else:
        H.act(vbe, "inject", fault="hang", duration_ms=1300)
    assert H.run_until(vbe, lambda: H.link_state(vbe) == state, 4000), H.link_state(vbe)
    for name, fn in (("STOP", H.stop), ("HALT", H.halt), ("PAUSE", H.pause)):
        m0 = H.wire_mark(vbe)
        t = H.now_ns(vbe)
        r = fn(vbe)
        fr = H.tx(vbe, name, since=m0)
        assert r.sent and fr and fr[0].t_ns == t, (name, state, r)


@pytest.mark.req("IF-005", "SW-STOP-001")
@pytest.mark.defect("SWD-M1-02")
def test_stop_lost_while_moving_repeated_every_50ms_until_ack(vbe):
    """Regression SWD-M1-02 (forced motion): the first three STOP requests are lost while the axis moves →
    STOP repeated every 50 ms with new SEQs; the 4th is acknowledged → confirmed, axis stopped."""
    # Verifies: IF-005, SW-STOP-001
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 200_000))
    H.act(vbe, "inject", fault="drop_next", cmd="STOP", what="request", n=3)
    m0 = H.wire_mark(vbe)
    assert H.stop(vbe).sent
    H.advance(vbe, 600)
    fr = H.tx(vbe, "STOP", since=m0)
    assert len(fr) == 4 and len({w.seq for w in fr}) == 4, len(fr)
    gaps = [(b.t_ns - a.t_ns) / MS for a, b in zip(fr, fr[1:])]
    assert all(49 <= g <= 56 for g in gaps), gaps
    conf = [r.payload for r in H.history(vbe, "stop.confirmed") if r.payload.cmd == "STOP"]
    assert conf and conf[-1].confirmed and conf[-1].attempts == 4
    assert not H.status(vbe).motion.moving


@pytest.mark.req("IF-005", "SW-STOP-001", "SW-STOP-002")
@pytest.mark.defect("SWD-M1-02")
def test_stop_all_lost_while_moving_at_most_20_then_unconfirmed(vbe):
    """Every STOP request lost while moving: ≤ 20 frames in 1 s, then stop.unconfirmed (cmd STOP) — the moving
    flag in frames received before / after the write never counts as a confirmation while MOVING = 1."""
    # Verifies: IF-005, SW-STOP-001, SW-STOP-002
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 250_000, 5_000))
    H.act(vbe, "inject", fault="drop_next", cmd="STOP", what="request", n=1000)
    m0 = H.wire_mark(vbe)
    t0 = H.now_ns(vbe)
    H.stop(vbe)
    H.advance(vbe, 1500)
    fr = H.tx(vbe, "STOP", since=m0)
    assert 15 <= len(fr) <= 20 and all(w.t_ns - t0 <= 1000 * MS for w in fr)
    un = [r.payload for r in H.history(vbe, "stop.unconfirmed")]
    assert un and un[-1].cmd == "STOP" and un[-1].confirmed is False
    assert not [r for r in H.history(vbe, "stop.confirmed") if r.payload.cmd == "STOP" and r.t_host_ns >= t0]
    H.act(vbe, "inject", fault="drop_next", cmd="STOP", what="request", n=0)


@pytest.mark.req("IF-005", "SW-STOP-001")
@pytest.mark.defect("SWD-M1-02")
def test_idle_stop_lost_confirmed_only_by_a_frame_after_the_write(vbe):
    """Idle axis, STOP request lost: confirmation comes from a DATA frame received *after* the write (MOVING = 0),
    not from the stale state at the call — the stop.confirmed time is ≥ the first DATA after the write."""
    # Verifies: IF-005, SW-STOP-001
    H.act(vbe, "inject", fault="drop_next", cmd="STOP", what="request", n=1)
    m0 = H.wire_mark(vbe)
    t0 = H.now_ns(vbe)
    H.stop(vbe)
    H.advance(vbe, 200)
    first_data = min(w.t_ns for w in H.rx(vbe, "DATA", since=m0))
    conf = [r for r in H.history(vbe, "stop.confirmed") if r.payload.cmd == "STOP" and r.t_host_ns >= t0]
    assert conf and conf[0].t_host_ns >= first_data > t0
