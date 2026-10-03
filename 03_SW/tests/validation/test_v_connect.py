"""Level C — connect sequence, version negotiation, endpoints (Validator F, M1).

Lock-step backend + in-process simulator for the deterministic cases; the out-of-process simulator server and
F's own ``FBoard`` (ref_codec + params.yaml, oracle/fboard.py) on the real clock for the endpoint and IF-008
cases. Wire ground truth = the PC wire log re-parsed with ``ref_codec``.

TC-SW-PLT-003-01, TC-SW-PLT-003-03 (sim + sim server), TC-IF-008-01, TC-SYS-008-01 (M1 subset).

Verifies: SW-PLT-003, IF-008, SYS-008, SAF-SW-002 (M1 session-value subset), IF-005
"""
from __future__ import annotations

import json

import pytest

import harness as H
from oracle.fboard import FBoard

MOTION = {"ENABLE", "DISABLE", "HOME", "MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD", "RESUME", "HALT_CLEAR",
          "ESTOP_CLEAR", "FAULT_CLEAR", "SAVE_PARAMS", "LOAD_PARAMS", "DEFAULT_PARAMS", "REBOOT"}
SESSION = ("safety.load_raw_min", "safety.load_raw_max", "safety.zero_raw")


def _connect_names(ws):
    return [w.name for w in ws if w.dir == "TX"]


def _check_connect_order(tx_names: list[str], pages: int) -> None:
    """ICD §9.5 order: GET_INFO… → GET_STATUS → GET_ALL_PARAMS pages → session SET/GET → STREAM_START."""
    i_info = tx_names.index("GET_INFO")
    i_status = tx_names.index("GET_STATUS")
    i_all = [i for i, n in enumerate(tx_names) if n == "GET_ALL_PARAMS"]
    i_stream = tx_names.index("STREAM_START")
    assert i_info < i_status < i_all[0] and i_all[-1] < i_stream
    assert len(i_all) >= pages
    assert all(n in ("GET_INFO", "PING") for n in tx_names[:i_status]), tx_names[:i_status]
    assert not MOTION & set(tx_names), "connect must never enable / home / move / clear / write NVM (§4.6)"


@pytest.mark.req("SW-PLT-003", "IF-008", "SAF-SW-002")
def test_tc_sw_plt_003_01_connect_order_fresh_sim(vbe, pdict):
    """TC-SW-PLT-003-01: GET_INFO → GET_STATUS → GET_ALL_PARAMS (all pages, each page once at least) → session
    values verified by GET_PARAM read-back → STREAM_START; link statistics present."""
    # Verifies: SW-PLT-003, IF-008, SAF-SW-002 (M1: defaults written/verified)
    ws = H.wire(vbe)
    names = _connect_names(ws)
    pages = -(-len(pdict.params) // 20)
    _check_connect_order(names, pages)
    got_pages = sorted({w.fields["page"] for w in ws if w.dir == "TX" and w.name == "GET_ALL_PARAMS"})
    assert got_pages == list(range(pages))
    ids = {p.key: p.id for p in pdict.params}
    readback = {w.fields["id"] for w in ws if w.dir == "TX" and w.name == "GET_PARAM"}
    assert {ids[k] for k in SESSION} <= readback
    i_last_rb = max(i for i, w in enumerate(ws) if w.dir == "TX" and w.name == "GET_PARAM")
    i_stream = next(i for i, w in enumerate(ws) if w.dir == "TX" and w.name == "STREAM_START")
    assert i_last_rb < i_stream
    st = H.status(vbe)
    assert H.link_state(vbe) == "CONNECTED" and st.stream.on
    s = st.link.stats
    for f in ("frames_ok", "frames_lost_fw", "frames_lost_link", "crc_errors", "len_errors", "timeout_drops"):
        assert isinstance(getattr(s, f), int), f
    assert st.link.info.param_dict_hash == pdict.hash
    assert H.thresholds(vbe).state in ("DEFAULT_ONLY", "VERIFIED")


@pytest.mark.req("SW-PLT-003", "SAF-SW-002", "SW-CFG-003")
def test_tc_sw_plt_003_01_session_values_written_in_h2_safe_order(lockstep, tmp_path, pdict):
    """The board starts with non-default session values whose order matters for H2 (both thresholds below the
    default min): the SW must SET load_raw_max first, then load_raw_min, then zero_raw, never provoke E_CONFIG,
    and verify all three by GET_PARAM (ICD §11.4, §11.5)."""
    # Verifies: SW-PLT-003, SAF-SW-002, SW-CFG-003
    ids = {p.key: p.id for p in pdict.params}
    dflt = {p.key: p.default for p in pdict.params}
    sc = {"schema": "bird.bend.simscenario", "version": 1, "world": {"load_offset_counts": 50000},
          "params": {"safety.load_raw_min": -7_100_000, "safety.load_raw_max": -7_050_000,
                     "safety.zero_raw": 1234}}
    f = tmp_path / "s.simscn.json"
    f.write_text(json.dumps(sc), encoding="utf-8")
    be = lockstep(endpoint=f"sim:{f}")
    ws = H.wire(be)
    sets = [w for w in ws if w.dir == "TX" and w.name == "SET_PARAM"]
    order = [w.fields["id"] for w in sets]
    assert order.index(ids["safety.load_raw_max"]) < order.index(ids["safety.load_raw_min"])
    assert {w.fields["id"]: w.fields["value"] for w in sets} == {ids[k]: dflt[k] for k in SESSION}
    assert not [w for w in ws if w.dir == "RX" and w.name == "SET_PARAM" and w.fields.get("status") != "OK"]
    vals = H.config_values(be)
    assert all(vals[k] == dflt[k] for k in SESSION)
    assert H.thresholds(be).state in ("DEFAULT_ONLY", "VERIFIED")


@pytest.mark.req("SW-PLT-003", "SYS-008")
def test_tc_sw_plt_003_03_sim_server_endpoint_same_connect_behaviour(tmp_path, pdict):
    """TC-SW-PLT-003-03: the out-of-process simulator (``python -m bend_stand.io.sim.server``) over
    ``tcp://`` gives the same connect sequence as ``sim``; the stream runs at 80 SPS (+0.5 %)."""
    # Verifies: SW-PLT-003, SYS-008
    srv = H.SimServerProcess()
    be = None
    try:
        be = H.realtime_backend(recordings_root=str(tmp_path))
        H.connect(be, srv.endpoint).result(10)
        assert H.wait_rt(lambda: H.status(be).stream.rate_sps is not None, 5)
        _check_connect_order(_connect_names(H.wire_rt(be)), -(-len(pdict.params) // 20))
        assert 78.0 < H.status(be).stream.rate_sps < 82.0
        q = srv.act("query", what="world")
        assert q.get("ok") is True
        H.disconnect(be).result(5)
    finally:
        if be is not None:
            be.shutdown()
        srv.close()
    assert srv.proc.poll() is not None


@pytest.mark.req("SYS-008", "SW-PLT-003", "SW-CFG-003", "SW-CFG-004", "SW-ACQ-001")
def test_tc_sys_008_01_m1_headless_workflow(vbe, pdict):
    """TC-SYS-008-01 (M1 subset): connect → read all → write+verify → save to NVM → stream 10 s at ~80.4 SPS
    with 0 losses → stream off/on → disconnect, all through the public API."""
    # Verifies: SYS-008, SW-PLT-003, SW-CFG-003, SW-CFG-004, SW-ACQ-001
    vals = H.result(vbe, H.read_all(vbe))
    assert set(vals) == {p.key for p in pdict.params}
    rep = H.result(vbe, H.write_verify(vbe, {"stream.fallback_hz": 20, "io.release_ms": 77}))
    assert rep.ok and {i.key: str(i.status) for i in rep.items} == {"stream.fallback_hz": "OK", "io.release_ms": "OK"}
    assert rep.cfg_dirty is True
    H.result(vbe, H.save_nvm(vbe))
    H.advance(vbe, 1500)
    assert H.status(vbe).cfg_dirty is False
    n0 = H.stats(vbe).data_frames
    H.advance(vbe, 10_000, 2)
    s = H.stats(vbe)
    assert 795 <= s.data_frames - n0 <= 815                       # 10 s × 80 × (1 + 0.5 %) ± 1 frame
    assert s.frames_lost_fw == s.frames_lost_link == s.crc_errors == 0
    assert H.status(vbe).stream.rate_sps == pytest.approx(80.4, abs=0.3)
    H.result(vbe, H.stream(vbe, False))
    assert not H.status(vbe).stream.on
    H.result(vbe, H.stream(vbe, True))
    assert H.status(vbe).stream.on
    H.result(vbe, H.disconnect(vbe))
    assert H.link_state(vbe) == "DISCONNECTED"


# ============================================================================================ IF-008 (F-board)

@pytest.fixture
def fboard_be(pdict, tmp_path):
    made = []

    def make(**kw):
        fb = FBoard(pdict, **kw)
        be = H.realtime_backend(recordings_root=str(tmp_path / "rec"))
        made.append((fb, be))
        H.connect(be, fb.endpoint).result(10)
        assert H.wait_rt(lambda: H.status(be).board is not None and bool(H.config_values(be)), 5)
        return fb, be

    yield make
    for fb, be in made:
        try:
            be.shutdown()
        finally:
            fb.close()


def _no_config_or_motion_on_wire(fb, n0):
    sent = fb.names()[n0:]
    assert not {"SET_PARAM", "SAVE_PARAMS", "LOAD_PARAMS", "DEFAULT_PARAMS", "ENABLE", "HOME", "MOVE_ABS", "JOG",
                "MOVE_UNTIL_LOAD", "REBOOT"} & set(sent), sent


@pytest.mark.req("IF-008", "SW-PLT-003")
@pytest.mark.parametrize("variant", ["major", "payload"])
def test_tc_if_008_01_major_or_payload_mismatch_read_only(fboard_be, variant):
    """TC-IF-008-01: PROTO major 2 / PAYLOAD_VERSION 2 → read-only: config writes and motion refused locally
    (nothing on the wire); monitoring (GET_STATUS, parameters) allowed; DATA of another payload version is not
    decoded."""
    # Verifies: IF-008, SW-PLT-003
    kw = {"proto_major": 2} if variant == "major" else {"payload_version": 2, "data_payload_version": 2}
    fb, be = fboard_be(**kw)
    st = H.status(be)
    assert st.config_read_only is True
    flag = "MAJOR_MISMATCH" if variant == "major" else "PAYLOAD_MISMATCH"
    assert flag in H.compat(be)
    assert not H.gate(be, "CONFIG_WRITE").ok
    for g in ("MOVE", "JOG", "HOME", "ENABLE"):
        assert not H.gate(be, g).ok
    assert "SET_PARAM" not in fb.names()                           # session values not written either
    n0 = len(fb.names())
    with pytest.raises(Exception):
        H.write_verify(be, {"io.release_ms": 77}).result(5)
    for fn in (H.save_nvm, H.load_nvm, H.defaults):
        with pytest.raises(Exception):
            fn(be).result(5)
    _no_config_or_motion_on_wire(fb, n0)
    vals = H.config_values(be)
    assert vals["io.release_ms"] == next(p.default for p in fb.pdict.params if p.key == "io.release_ms")
    if variant == "payload":
        assert H.wait_rt(lambda: H.stats(be).bad_payload > 10, 3)
        assert H.stats(be).data_frames == 0


@pytest.mark.req("IF-008")
@pytest.mark.defect("SWD-M1-03")
@pytest.mark.parametrize("which", ["clear_stop", "estop_clear", "fault_clear"])
def test_tc_if_008_01_read_only_refuses_clears_locally(fboard_be, which):
    """ICD §9.5 step 3 / SW_design §5.2: in the read-only state 'no clears-and-enable' — a clear must be refused
    locally with nothing on the wire."""
    # Verifies: IF-008
    fb, be = fboard_be(proto_major=2, flags=("HALT", "ESTOP", "FAULT"))
    assert H.wait_rt(lambda: H.status(be).board is not None, 3)
    n0 = len(fb.names())
    fut = {"clear_stop": lambda: H.clear_stop(be, True), "estop_clear": lambda: H.estop_clear(be, True),
           "fault_clear": lambda: H.fault_clear(be)}[which]()
    try:
        fut.result(3)
    except Exception:  # noqa: BLE001 - a local refusal may raise
        pass
    sent = set(fb.names()[n0:])
    assert not {"HALT_CLEAR", "ESTOP_CLEAR", "FAULT_CLEAR"} & sent, sent


@pytest.mark.req("IF-008", "SAF-SW-002")
def test_tc_if_008_01_hash_mismatch_config_read_only_session_still_written(fboard_be, pdict):
    """Dictionary hash ≠ → configuration read-only + warning (user writes refused locally); the session values
    are still written (id/type checked individually) and verified; monitoring continues."""
    # Verifies: IF-008, SAF-SW-002
    fb, be = fboard_be(dict_hash=pdict.hash ^ 0x5A5A5A5A)
    st = H.status(be)
    assert H.compat(be) == {"PARAM_HASH_MISMATCH"} and st.config_read_only is True
    assert not H.gate(be, "CONFIG_WRITE").ok
    n0 = len(fb.names())
    with pytest.raises(Exception):
        H.write_verify(be, {"io.release_ms": 77}).result(5)
    _no_config_or_motion_on_wire(fb, n0)
    assert H.wait_rt(lambda: H.stats(be).data_frames > 10, 3)       # DATA decoded (payload version OK)
    names = fb.names()
    assert names.count("GET_PARAM") >= 3                             # session values verified by read-back


@pytest.mark.req("IF-008", "SAF-SW-002")
def test_tc_if_008_01_hash_mismatch_rewrites_session_values(pdict, tmp_path):
    """Hash mismatch with non-default session values on the board: the SW still writes them (SET_PARAM of the
    three safety.* ids only) and verifies them."""
    # Verifies: IF-008, SAF-SW-002
    fb = FBoard(pdict, dict_hash=pdict.hash ^ 1)
    fb.params["safety.zero_raw"] = 4321
    be = H.realtime_backend(recordings_root=str(tmp_path))
    try:
        H.connect(be, fb.endpoint).result(10)
        assert H.wait_rt(lambda: "STREAM_START" in fb.names(), 5)
        ids = {p.key: p.id for p in pdict.params}
        sets = [r for n, r in fb.received if n == "SET_PARAM"]
        assert [r["id"] for r in sets] == [ids["safety.zero_raw"]] and sets[0]["value"] == 0
        assert fb.params["safety.zero_raw"] == 0
    finally:
        be.shutdown()
        fb.close()


@pytest.mark.req("IF-008")
def test_tc_if_008_01_minor_higher_is_compatible(fboard_be):
    """PROTO 1.1 (a newer minor = backward-compatible additions): no read-only state, config writable.
    (Plan correction v0.2: 'minor lower' cannot be built against SW PROTO 1.0 — minor 0 is the floor.)"""
    # Verifies: IF-008
    fb, be = fboard_be(proto_minor=1)
    st = H.status(be)
    assert not st.config_read_only and not H.compat(be)
    rep = H.write_verify(be, {"io.release_ms": 77}).result(5)
    assert rep.ok and "SET_PARAM" in fb.names()
