"""Level X — SW ⇄ FW host twin, M1 subset of the integration acceptance (Validator F, condition C6).

The twin runs Implementer A's unmodified firmware (``00_System/tools/fw_twin``, real-time clock, TCP endpoint);
B's Backend connects over ``tcp://``. Ground truth: the PC wire log (re-parsed by ``ref_codec``) and the twin's
own logs (``sent``, ``conversions``). The twin engine processes are started and stopped only through the
``Twin`` object's own handles (role rule: never by name).

TC-SYS-008-02 (M1: connect, config write/verify, NVM, stream, STOP/HALT/PAUSE/RESUME/clear confirmation),
TC-SW-PLT-003-03 (twin endpoint), IF-007 (frame count = conversion count), sim-vs-twin outcome equality.

Verifies: SYS-008, SW-PLT-003, SW-CFG-003, SW-CFG-004, SW-ACQ-001, IF-005, IF-007, IF-011, SW-STOP-003,
SW-STOP-004
"""
from __future__ import annotations

import time

import pytest

import harness as H

pytestmark = [pytest.mark.twin, pytest.mark.rt]



def _twin_exe():
    import build as twin_build

    try:
        twin_build.find_gcc()
    except SystemExit as e:  # pragma: no cover - environment
        pytest.skip(f"no host gcc for the twin: {e}")
    if not twin_build.fw_core_present():  # pragma: no cover
        pytest.skip("A's FW core absent")
    return twin_build.ensure_built("fw")


@pytest.fixture
def twin_be(tmp_path):
    from twin import Twin

    tw = Twin("realtime", exe=_twin_exe(), run_dir=tmp_path / "twin", speed=1.0)
    proc = tw.eng.p
    H.log_process("fw_twin engine", proc.pid, "started")
    port, _ctl = tw.serve(0, 0)
    be = H.realtime_backend(recordings_root=str(tmp_path / "rec"))
    try:
        H.connect(be, f"tcp://127.0.0.1:{port}").result(15)
        assert H.wait_rt(lambda: H.status(be).stream.on and H.status(be).board is not None, 5)
        yield tw, be
    finally:
        be.shutdown()
        tw.close()
        assert tw.eng is None and proc.poll() is not None
        H.log_process("fw_twin engine", proc.pid, f"stopped rc={proc.returncode}")


def _events(be, since=0):
    return [w.fields.get("code") for w in H.rx(be, "EVENT", since=since)]


@pytest.mark.req("SYS-008", "SW-PLT-003", "IF-008")
def test_twin_connect_sequence_and_compat(twin_be, pdict):
    """Connect to A's firmware: ICD §9.5 order, compat OK (hash = params.yaml), all 48 values = defaults (blank
    flash → NVM_DEFAULTED, CFG_DIRTY), session values verified, never any motion / clear / NVM command."""
    # Verifies: SYS-008, SW-PLT-003, IF-008
    tw, be = twin_be
    names = [w.name for w in H.tx(be)]
    assert names.index("GET_INFO") < names.index("GET_STATUS") < names.index("GET_ALL_PARAMS") < \
        names.index("STREAM_START")
    assert not {"ENABLE", "HOME", "MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD", "RESUME", "HALT_CLEAR", "ESTOP_CLEAR",
                "FAULT_CLEAR", "SAVE_PARAMS", "LOAD_PARAMS", "DEFAULT_PARAMS", "REBOOT"} & set(names)
    st = H.status(be)
    assert not H.compat(be) and st.link.info.param_dict_hash == pdict.hash and "TWIN" in rc_features(st)
    vals = H.config_values(be)
    for p in pdict.params:
        assert vals[p.key] == pytest.approx(p.default), p.key
    assert st.nvm_defaulted is True and st.cfg_dirty is True
    assert H.thresholds(be).state in ("DEFAULT_ONLY", "VERIFIED")


def rc_features(st) -> set[str]:
    import ref_codec as rc

    return set(rc.bits_to_names(st.link.info.feature_mask, rc.FEATURES))


@pytest.mark.req("SYS-008", "SW-CFG-003", "SW-CFG-004", "IF-005")
def test_twin_config_write_verify_nvm_reboot(twin_be, pdict):
    """Write & verify incl. an H1 / H4 ordered set (no E_CONFIG from the FW), SAVE (record seq + 1, CFG_DIRTY 0),
    REBOOT (one frame) → EVENT BOOT → automatic resync; the values come back from NVM, NVM_DEFAULTED clear."""
    # Verifies: SYS-008, SW-CFG-003, SW-CFG-004, IF-005
    tw, be = twin_be
    edits = {"limits.soft_min_um": 300000, "limits.soft_max_um": 399999, "motion.v_max_load_um_s": 40000,
             "motion.v_max_travel_um_s": 50000, "io.release_ms": 77}
    m0 = H.wire_mark(be)
    rep = H.write_verify(be, edits).result(10)
    assert rep.ok and all(str(i.status) == "OK" for i in rep.items), rep
    assert not [w for w in H.rx(be, "SET_PARAM", since=m0) if w.fields["status"] != "OK"]
    seq0 = H.status(be).board.nvm_record_seq
    H.save_nvm(be).result(10)
    assert H.wait_rt(lambda: H.status(be).board.nvm_record_seq == seq0 + 1 and H.status(be).cfg_dirty is False, 3)
    m1 = H.wire_mark(be)
    H.reboot(be).result(10)
    assert H.wait_rt(lambda: H.status(be).stream.on and "BOOT" in _events(be, m1), 10)
    assert H.wait_rt(lambda: all(H.config_values(be).get(k) == v for k, v in edits.items()), 5)
    assert len(H.tx(be, "REBOOT", since=m1)) == 1
    assert H.wait_rt(lambda: H.status(be).nvm_defaulted is False, 3)


@pytest.mark.req("IF-007", "SW-ACQ-001", "SYS-008")
def test_twin_one_frame_per_conversion_and_fw_drop_attribution(twin_be):
    """IF-007 on the twin: DATA frames received == HX711 conversions delivered while streaming (no loss); under
    TX congestion the SW counts exactly the frames the FW dropped as FW losses."""
    # Verifies: IF-007, SW-ACQ-001, SYS-008
    tw, be = twin_be
    d0 = H.stats(be).data_frames
    c0 = sum(1 for c in tw.act("query", what="conversions")["conversions"] if c["delivered"])
    time.sleep(5.0)
    d1 = H.stats(be).data_frames
    c1 = sum(1 for c in tw.act("query", what="conversions")["conversions"] if c["delivered"])
    assert abs((d1 - d0) - (c1 - c0)) <= 2 and d1 - d0 >= 390               # in-flight frames at both ends
    s = H.stats(be)
    assert s.frames_lost_fw == s.frames_lost_link == s.crc_errors == 0
    sent0 = len(tw.act("query", what="sent")["sent"])
    tw.act("inject", fault="tx_congestion", duration_ms=300)
    time.sleep(1.0)
    dropped = sum(1 for f in tw.act("query", what="sent")["sent"][sent0:] if f["type"] == 0xC0 and f["dropped"])
    assert H.wait_rt(lambda: H.stats(be).frames_lost_fw == dropped, 2), (H.stats(be).frames_lost_fw, dropped)
    assert dropped >= 1 and H.stats(be).frames_lost_link == 0


@pytest.mark.req("SYS-008", "IF-011", "SW-STOP-003", "SW-STOP-004", "IF-005")
def test_twin_stop_halt_pause_resume_clear(twin_be):
    """Priority path and confirmations against the FW: HALT → HALT (source PC) confirmed; RESUME refused locally
    while HALT is latched (nothing on the wire); Clear stop clears HALT; PAUSE → PAUSED (source PC); RESUME
    clears PAUSED (EVENT PAUSE_CLEARED); STOP while idle confirmed; no motion command anywhere."""
    # Verifies: SYS-008, IF-011, SW-STOP-003, SW-STOP-004, IF-005
    tw, be = twin_be
    m0 = H.wire_mark(be)
    assert H.halt(be).sent
    assert H.wait_rt(lambda: H.indicator(be, "halt").state == "ON", 2)
    assert H.wait_rt(lambda: H.indicator(be, "halt").source == "PC", 1.5)      # (≤ 1 s STATUS poll, SWD-M1-05)
    assert H.pause(be).sent
    assert H.wait_rt(lambda: H.status(be).motion.paused, 2)
    g = H.resume(be)
    assert not g.ok and "HALT" in [i.code for i in g.items]
    time.sleep(0.2)
    assert not H.tx(be, "RESUME", since=m0)
    res = H.clear_stop(be).result(5)
    assert res.confirmed and str(res.outcome) == "OK"
    assert H.wait_rt(lambda: H.indicator(be, "halt").state == "OFF" and not H.status(be).motion.paused, 2)
    assert H.pause(be).sent
    assert H.wait_rt(lambda: H.status(be).motion.paused and H.indicator(be, "paused").source == "PC", 2)
    assert H.resume(be).ok
    assert H.wait_rt(lambda: not H.status(be).motion.paused, 2)
    assert "PAUSE_CLEARED" in _events(be, m0) and "HALT_SET" in _events(be, m0) and "PAUSED" in _events(be, m0)
    assert H.stop(be).sent
    time.sleep(0.3)
    assert any(r.payload.cmd == "STOP" for r in H.history(be, "stop.confirmed"))
    names = [w.name for w in H.tx(be, since=m0)]
    assert names.count("RESUME") == 1 and names.count("HALT_CLEAR") == 1
    assert not {"MOVE_ABS", "JOG", "HOME", "ENABLE", "MOVE_UNTIL_LOAD"} & set(names)


def _outcome_script(be, wait):
    """The same scripted M1 sequence; returns comparable outcomes (statuses / event codes / flags)."""
    out = []
    m0 = H.wire_mark(be)
    rep = wait(H.write_verify(be, {"io.release_ms": 61, "afe.timeout_ms": 300, "afe.rate_sps": 0}))
    out.append(("write", tuple(sorted((i.key, str(i.status)) for i in rep.items))))
    out.append(("halt", H.halt(be).sent))
    wait(None, 0.4)
    out.append(("halt_flag", H.indicator(be, "halt").state))
    out.append(("clear", str(wait(H.clear_stop(be)).outcome)))
    out.append(("pause", H.pause(be).sent))
    wait(None, 0.4)
    out.append(("paused", H.status(be).motion.paused))
    out.append(("resume", H.resume(be).ok))
    wait(None, 0.4)
    out.append(("paused_after", H.status(be).motion.paused))
    wait(None, 0.3)
    evs = [e for e in _events(be, m0) if e in ("HALT_SET", "HALT_CLEARED", "PAUSED", "PAUSE_CLEARED", "VALID_CLEARED")]
    out.append(("events", tuple(evs)))
    resp = tuple((w.name, w.fields.get("status")) for w in H.rx(be, since=m0) if w.kind == "response"
                 and w.name not in ("PING", "GET_STATUS", "GET_ALL_PARAMS", "GET_PARAM"))
    out.append(("responses", resp))
    return out


@pytest.mark.req("SYS-008", "IF-005")
def test_sim_vs_twin_same_outcomes(twin_be, lockstep):
    """TC-SYS-008-02: the same scripted M1 sequence on the simulator (lock-step) and on A's firmware (twin)
    gives the same write statuses, latch states, EVENT sequence and response statuses."""
    # Verifies: SYS-008, IF-005
    tw, be_t = twin_be

    def wait_rt(fut, s=0.0):
        if fut is None:
            time.sleep(s)
            return None
        return fut.result(10)

    be_s = lockstep()

    def wait_ls(fut, s=0.0):
        if fut is None:
            H.advance(be_s, s * 1000)
            return None
        return H.result(be_s, fut)

    a = _outcome_script(be_s, wait_ls)
    b = _outcome_script(be_t, wait_rt)
    assert a == b
