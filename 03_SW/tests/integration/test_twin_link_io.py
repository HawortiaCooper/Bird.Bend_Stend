"""SW io layer (Implementer B: FrameDecoder, protocol) <-> FW host twin in lock-step virtual time.

OBS-M2-09 (M3): the client waits in the twin's virtual time (``io_client.LockstepIoClient``), so host load can
no longer turn a wall-clock wait into a failure; B's TcpTransport keeps its realtime smoke in
test_backend_twin.py (``test_backend_connect_sequence_tcp_realtime``).

Connect sequence at the wire level (ICD §9.5), GET_INFO version/hash check, GET_ALL_PARAMS paging, SET_PARAM
write-verify, NVM save / reboot / restore, stream start/stop and CRC-error handling — all bytes built and
decoded by B's production codec, answered by A's firmware.

Verifies: IF-001, IF-002 (twin), IF-003, IF-004, IF-006, IF-008, FW-CFG-002, FW-CFG-003, FW-CFG-004,
          FW-NVM-001, FW-STR-001, SW-PLT-003 (wire part), SYS-008
"""
from __future__ import annotations

import time

import pytest

from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.io import protocol as proto

from io_client import LockstepIoClient
from lockstep_boards import TwinBoard

pytestmark = [pytest.mark.twin]

# non-session, non-reboot parameters that are safe to change in M1 (no motion, no AFE rate change)
EDITS = {"stream.fallback_hz": 20, "io.release_ms": 77, "safety.link_timeout_ms": 1500, "afe.rate_tol_pct": 25}


@pytest.fixture
def twin_rt(twin):
    """Name kept from the realtime version: the lock-step twin (same act / logs API)."""
    return twin


@pytest.fixture
def cli(twin):
    twin.advance_ms(5)
    twin.read_client()                              # the power-on BOOT went out before the port was opened (VCP)
    c = LockstepIoClient(TwinBoard(tw=twin)).open()
    c.pump(0.05)                                    # pending bytes at open (BOOT etc.) go to the decoder
    yield c
    c.close()


@pytest.mark.req("SW-PLT-003", "IF-008", "FW-CFG-004")
def test_connect_get_info_version_and_hash(cli, twin_rt):
    info = cli.info()
    assert (info.proto_major, info.payload_version) == (pg.PROTO_MAJOR, pg.PAYLOAD_VERSION)
    assert info.proto_minor >= pg.PROTO_MINOR
    assert info.param_dict_hash == pgen.PARAM_DICT_HASH
    assert info.param_count == pgen.PARAM_COUNT
    assert {"NVM", "TWIN"} <= info.features and ({"AFE", "AFE_SYNTHETIC"} & info.features)   # M1/M2 build + twin
    assert "HOMING" not in info.features or "MOTION" in info.features
    st = cli.status()
    assert st.motion_state == pg.MotionState.NOT_ENABLED
    assert st.reset_cause == pg.ResetCause.POWER_ON
    assert st.sys_flags & pg.SysFlags.NVM_DEFAULTED and st.sys_flags & pg.SysFlags.CFG_DIRTY   # blank flash
    assert not st.sys_flags & pg.SysFlags.STREAM_ON
    # the power-on BOOT EVENT went out before the port was opened (as on the VCP); a reset while connected:
    twin_rt.act("reset", cause="pin")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not any(e.code == pg.Event.BOOT for e in cli.events):
        cli.pump(0.02)
    boot = [e for e in cli.events if e.code == pg.Event.BOOT]
    assert boot and boot[0].arg == pg.ResetCause.PIN
    assert cli.info().param_dict_hash == pgen.PARAM_DICT_HASH          # reconnect not needed (TCP = VCP stays)


@pytest.mark.req("FW-CFG-002", "IF-001")
def test_get_all_params_pages_defaults(cli):
    entries = cli.all_params()
    ids = [e.id for e in entries]
    assert ids == sorted(ids) and ids == [m.id for m in pgen.PARAMS]       # every id once, ascending
    for e in entries:
        assert e.value() == pgen.BY_ID[e.id].default, e.key
    count = (pgen.PARAM_COUNT + pg.PARAMS_PER_PAGE - 1) // pg.PARAMS_PER_PAGE
    r = cli.cmd("GET_ALL_PARAMS", page=count)
    assert (r.status, r.detail) == (pg.Status.E_RANGE, 0)


@pytest.mark.req("FW-CFG-003", "SW-CFG-003")
def test_set_param_write_verify(cli, twin_rt):
    w0 = twin_rt.act("query", what="flash")["writes"]
    for key, v in EDITS.items():
        r = cli.set_param(key, v)
        assert r.ok, (key, r)
        stored = proto.decode_param_entry(r.body)
        assert stored.value() == v
        g = cli.get_param(key)
        assert g.ok and proto.decode_param_entry(g.body).value() == v           # as stored == GET_PARAM
    vals = {e.key: e.value() for e in cli.all_params()}
    assert all(vals[k] == v for k, v in EDITS.items())                          # read-back via pages
    assert cli.status().sys_flags & pg.SysFlags.CFG_DIRTY
    m = pgen.BY_KEY["io.release_ms"]
    r = cli.request(pg.Cmd.SET_PARAM, proto.encode_param_entry_unchecked(m.id, int(m.type), m.max + 1))
    assert (r.status, r.detail) == (pg.Status.E_RANGE, m.id)                    # never clamped
    assert proto.decode_param_entry(cli.get_param(m.key).body).value() == EDITS["io.release_ms"]
    assert twin_rt.act("query", what="flash")["writes"] == w0                   # SET never writes flash


@pytest.mark.req("FW-NVM-001", "SW-CFG-004")
def test_save_reboot_restore(cli, twin_rt):
    for key, v in EDITS.items():
        assert cli.set_param(key, v).ok
    r = cli.cmd("SAVE_PARAMS", timeout_s=3.0)
    assert r.ok, r
    st = cli.status()
    assert not st.sys_flags & pg.SysFlags.CFG_DIRTY and st.nvm_record_seq >= 1
    saved = [e for e in cli.events if e.code == pg.Event.PARAMS_SAVED]
    assert saved and saved[-1].value == st.nvm_record_seq
    assert cli.cmd("REBOOT", magic=pg.REBOOT_MAGIC).ok
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not any(e.code == pg.Event.BOOT and e.arg == pg.ResetCause.SOFTWARE
                                                   for e in cli.events):
        cli.pump(0.02)
    assert any(e.code == pg.Event.BOOT and e.arg == pg.ResetCause.SOFTWARE for e in cli.events)
    assert [r["cause"] for r in twin_rt.resets] == ["software"]
    vals = {e.key: e.value() for e in cli.all_params()}
    assert all(vals[k] == v for k, v in EDITS.items())
    st = cli.status()
    assert st.reset_cause == pg.ResetCause.SOFTWARE
    assert not st.sys_flags & (pg.SysFlags.CFG_DIRTY | pg.SysFlags.NVM_DEFAULTED)


@pytest.mark.req("FW-STR-001", "FW-STR-002", "IF-006", "IF-007")
def test_stream_start_stop_and_frames(cli, twin_rt):
    cli.pump(0.3)
    assert not cli.data                                     # stream off after boot
    t0 = twin_rt.now_us
    assert cli.cmd("STREAM_START").ok and cli.cmd("STREAM_START").ok      # idempotent
    cli.pump(1.0)
    assert cli.cmd("STREAM_STOP").ok
    cli.pump(0.1)
    n = len(cli.data)
    t1 = twin_rt.now_us
    cli.pump(0.3)
    assert len(cli.data) == n                               # nothing after STREAM_STOP
    assert all(d.payload_version == 1 for d in cli.data)
    seqs = [d.frame_seq for d in cli.data]
    assert seqs == list(range(seqs[0], seqs[0] + n))
    conv = [c for c in twin_rt.act("query", what="conversions", since_us=t0)["conversions"]
            if c["delivered"] and c["t_us"] <= t1]
    ts = {c["fw_t_us"] for c in conv}
    assert {d.t_us for d in cli.data} <= ts                 # DATA t_us = the conversion's data-ready time
    assert cli.cmd("STREAM_START").ok
    cli.pump(0.2)
    assert cli.data[n].frame_seq == seqs[-1] + 1 or cli.data[n].frame_seq > seqs[-1]   # not reset by STOP/START


@pytest.mark.req("IF-004", "FW-CMD-004")
def test_corrupted_command_counted_not_answered(cli):
    st0 = cli.status()
    bad = bytearray(proto.build_request(pg.Cmd.PING) or b"")
    from bend_stand.io.framing import encode_frame  # noqa: PLC0415
    fr = bytearray(encode_frame(int(pg.Cmd.PING), 0x33, bytes(bad)))
    fr[-1] ^= 0x5A
    cli.tr.write(bytes(fr))
    cli.pump(0.1)
    assert not [r for r in cli.responses if r.seq == 0x33]
    st1 = cli.status()
    assert st1.rx_crc_errors == st0.rx_crc_errors + 1
