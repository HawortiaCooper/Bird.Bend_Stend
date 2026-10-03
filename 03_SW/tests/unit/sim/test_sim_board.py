"""SimBoard behaviour (WP-B7): streaming cadence and frame_seq, fallback frames, SET_VALID boundary, NVM rules,
latches / events, simple motion, SimControl vocabulary v2 (names checked against tools/README), scenario,
sent-frame log, wire-timed actions, the out-of-process TCP server.

Verifies: SYS-008, IF-007, IF-010, FW-CMD-001 (sim), FW-CMD-002 (sim), FW-NVM-002 (sim), D-07
"""
from __future__ import annotations

import json
import re
import socket
import struct
from pathlib import Path

import pytest

from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import LockstepClock
from bend_stand.io import protocol as P
from bend_stand.io.framing import FrameDecoder, encode_frame
from bend_stand.io.sim.board import SimBoard
from bend_stand.io.sim.control import VOCABULARY, SimControl, SimScenario, default_scenario
from bend_stand.io.sim.nvm import NvmRecord, NvmStore
from bend_stand.io.sim.server import SimServer
from bend_stand.io.transport import TcpTransport, VirtualTransportPair

Cmd, EV, DS, DF = pg.Cmd, pg.Event, pg.DataStatus, pg.DataFlags
MS = 1_000_000
README = Path(__file__).resolve().parents[4] / "00_System" / "tools" / "README.md"


class Client:
    def __init__(self, nvm: NvmStore | None = None, **board_kw) -> None:
        self.c = LockstepClock(start_ns=0)
        self.pair = VirtualTransportPair(self.c, bytes_per_s=None)
        self.pair.pc.open()
        self.b = SimBoard(self.c, self.pair.board, nvm=nvm, **board_kw)
        self.ctl = SimControl(self.b, advance=self.run)
        self.dec = FrameDecoder()
        self.data: list[P.DataSample] = []
        self.events: list[P.EventPayload] = []
        self.resp: dict[int, P.Response] = {}
        self.seq = 0
        self.hb = False                                   # PC heartbeat (PING every 150 ms)

    def run(self, ms: float) -> None:
        for _ in range(int(ms)):
            self.c.advance(ns=MS)
            if self.hb and (self.c.monotonic_ns() // MS) % 150 == 0:
                self.pair.pc.write(encode_frame(Cmd.PING, 250, b""))
            self.b.step()
            for fr in self.dec.feed(self.pair.pc.read(65536, 0.0)):
                if fr.type == pg.AsyncType.DATA:
                    self.data.append(P.decode_data(fr.payload))
                elif fr.type == pg.AsyncType.EVENT:
                    self.events.append(P.decode_event(fr.payload))
                else:
                    self.resp[fr.seq] = P.split_response(fr.type, fr.seq, fr.payload)

    def cmd(self, cmd: int, payload: bytes = b"", wait: float = 2) -> P.Response:
        self.seq = (self.seq + 1) & 0xFF
        s = self.seq
        self.pair.pc.write(encode_frame(cmd, s, payload))
        self.run(wait)
        return self.resp.pop(s)

    def ev(self, name: str) -> list[P.EventPayload]:
        return [e for e in self.events if e.code == EV[name]]


@pytest.mark.req("SYS-008", "FW-STR-001")
def test_boot_event_and_info() -> None:
    cl = Client()
    cl.run(5)
    assert cl.ev("BOOT")[0].arg == pg.ResetCause.POWER_ON and cl.ev("PARAMS_DEFAULTED")[0].arg == 1
    info = P.decode_info(cl.cmd(Cmd.GET_INFO).body)
    assert info.build == f"SIM-ICD{pg.ICD_VERSION}" and info.param_dict_hash == pgen.PARAM_DICT_HASH
    st = P.decode_status(cl.cmd(Cmd.GET_STATUS).body)
    assert st.motion == "NOT_ENABLED" and st.sys_flags & pg.SysFlags.NVM_DEFAULTED
    assert not cl.data                                   # stream off after boot


@pytest.mark.req("IF-007", "FW-STR-002")
def test_data_cadence_and_frame_seq_continuity() -> None:
    cl = Client()
    cl.cmd(Cmd.STREAM_START)
    cl.run(10_000)
    n1 = len(cl.data)
    assert 798 <= n1 <= 808                              # 80 SPS × (1 + 0.5 %) for 10 s
    dts = [b.t_us - a.t_us for a, b in zip(cl.data, cl.data[1:], strict=False)]
    assert all(12_400 <= d <= 12_500 for d in dts)
    cl.cmd(Cmd.STREAM_STOP)
    cl.run(1000)
    cl.cmd(Cmd.STREAM_START)
    cl.run(500)
    seqs = [d.frame_seq for d in cl.data]
    assert all(b - a == 1 for a, b in zip(seqs, seqs[1:], strict=False))   # not reset, no frames while off
    assert all(d.payload_version == 1 for d in cl.data)
    assert all(d.status & DS.DRV_PWR for d in cl.data)


@pytest.mark.req("IF-007", "FW-STR-005")
def test_stall_gives_fallback_frames_and_overrun_on_congestion() -> None:
    cl = Client()
    cl.cmd(Cmd.STREAM_START)
    cl.run(500)
    cl.ctl.act("afe", stall=True)
    cl.run(1500)
    fb = [d for d in cl.data if d.status & DS.NO_AFE_DATA]
    assert fb and all(d.afe_raw == pg.AFE_NO_DATA and d.status & DS.AFE_STALE for d in fb)
    t = [d.t_us for d in fb]
    assert all(abs((b - a) - 100_000) <= 1000 for a, b in zip(t, t[1:], strict=False))   # stream.fallback_hz 10
    assert cl.ev("AFE_STALE")[0].arg == 1
    cl.ctl.act("afe", stall=False)
    cl.run(300)
    assert cl.ev("AFE_STALE")[-1].arg == 0
    n = len(cl.data)
    cl.ctl.act("inject", fault="tx_congestion", duration_ms=100)
    cl.run(300)
    later = cl.data[n - 1:]
    jumps = [(a, b) for a, b in zip(later, later[1:], strict=False) if b.frame_seq - a.frame_seq > 1]
    assert jumps and jumps[0][1].flags & DF.OVERRUN       # FW loss → OVERRUN in the next sent frame
    sent = cl.ctl.act("query", what="sent")["frames"]
    assert any(f["dropped"] for f in sent) and cl.b.counters["tx_drops"] > 0


@pytest.mark.req("FW-CMD-002", "IF-007")
def test_set_valid_boundary_by_t_us() -> None:
    cl = Client()
    cl.cmd(Cmd.STREAM_START)
    cl.run(100)
    t_resp = P.decode_u32(cl.cmd(Cmd.SET_VALID, b"\x01", wait=0.0).body) if False else None
    cl.seq += 1
    cl.pair.pc.write(encode_frame(Cmd.SET_VALID, cl.seq, b"\x01"))
    cl.run(200)
    t_resp = P.decode_u32(cl.resp[cl.seq].body)
    for d in cl.data:
        is_after = ((d.t_us - t_resp) & 0xFFFFFFFF) < 0x80000000
        assert bool(d.flags & DF.VALID) == is_after
    cl.cmd(Cmd.STOP, b"\x00")                              # STOP while idle clears VALID (+ VALID_CLEARED)
    cl.run(50)
    assert cl.ev("VALID_CLEARED") and not cl.data[-1].flags & DF.VALID


@pytest.mark.req("FW-NVM-002", "SW-CFG-004")
def test_nvm_records_power_cut_migration_and_hard_rule(tmp_path) -> None:
    path = str(tmp_path / "nvm.json")
    cl = Client(nvm=NvmStore(path))
    cl.cmd(Cmd.SET_PARAM, P.build_set_param("afe.rate_tol_pct", 30))
    assert cl.cmd(Cmd.SAVE_PARAMS).ok and cl.ev("PARAMS_SAVED")[0].value == 1
    st = P.decode_status(cl.cmd(Cmd.GET_STATUS).body)
    assert not st.sys_flags & pg.SysFlags.CFG_DIRTY and st.nvm_record_seq == 1
    cl.cmd(Cmd.SET_PARAM, P.build_set_param("afe.rate_tol_pct", 31))
    cl.ctl.act("flash", cut_after_word=3)
    cl.cmd(Cmd.SAVE_PARAMS)                               # power cut during the program → reset
    cl.run(20)
    assert cl.ev("BOOT")[-1].arg == pg.ResetCause.POWER_ON
    entry = P.decode_param_entry(cl.cmd(Cmd.GET_PARAM, struct.pack("<H", 0x0103)).body)
    assert entry.value() == 30                            # previous record still valid
    # persisted across a simulator restart
    cl2 = Client(nvm=NvmStore(path))
    assert cl2.b.params["afe.rate_tol_pct"] == 30
    # migration by id: another dictionary hash, a retired id and an out-of-range value
    store = NvmStore()
    vals = {p.id: p.default for p in pgen.PARAMS if p.nvm}
    vals[0x0103] = 45
    vals[0x0401] = 1
    vals[0x0104] = 99
    store.records = [NvmRecord(5, 0x1234, vals), None]
    cl3 = Client(nvm=store)
    cl3.run(5)
    assert cl3.b.params["afe.rate_tol_pct"] == 45 and cl3.b.params["afe.settle_discard"] == 4
    assert cl3.ev("PARAMS_DEFAULTED")[0].arg == 3 and cl3.b.nvm_defaulted
    # hard-rule image → all defaults (boot) / E_NVM 1 (LOAD)
    vals2 = {p.id: p.default for p in pgen.PARAMS if p.nvm}
    vals2[0x0301] = 300_000
    store2 = NvmStore()
    store2.records = [NvmRecord(1, pgen.PARAM_DICT_HASH, vals2), None]
    cl4 = Client(nvm=store2)
    cl4.run(5)
    assert cl4.ev("PARAMS_DEFAULTED")[0].arg == 4 and cl4.b.params["limits.soft_min_um"] == 500
    r = cl4.cmd(Cmd.LOAD_PARAMS)
    assert (r.status_name, r.detail) == ("E_NVM", 1)
    # clean LOAD and DEFAULT
    cl.cmd(Cmd.SET_PARAM, P.build_set_param("afe.rate_tol_pct", 40))
    assert cl.cmd(Cmd.LOAD_PARAMS).ok and cl.b.params["afe.rate_tol_pct"] == 30 and cl.ev("PARAMS_LOADED")
    assert cl.cmd(Cmd.DEFAULT_PARAMS).ok and cl.b.params["afe.rate_tol_pct"] == 20


@pytest.mark.req("SYS-008", "SAF-FW-023")
def test_latches_and_events() -> None:
    cl = Client()
    cl.cmd(Cmd.STREAM_START)
    cl.ctl.act("estop", open=True)
    cl.run(50)
    assert cl.ev("ESTOP_SET") and cl.ev("DRIVER_POWER")[0].arg == 0
    st = P.decode_status(cl.cmd(Cmd.GET_STATUS).body)
    assert st.flags & DF.ESTOP and st.io & pg.IoBits.ESTOP_OPEN and "K1_WELDED" not in cl.b._fault_causes()  # noqa: SLF001
    assert cl.cmd(Cmd.ESTOP_CLEAR).detail == pg.DETAIL_CAUSE_INPUT
    cl.ctl.act("estop", open=False)
    cl.ctl.act("drv_power", on=True)
    cl.run(150)
    assert cl.cmd(Cmd.ESTOP_CLEAR).ok and cl.ev("ESTOP_CLEARED")
    cl.ctl.act("button", name="pause", pressed=True)
    cl.run(5)
    cl.ctl.act("button", name="pause", pressed=False)
    cl.run(25)                                           # re-armed after a stable release of io.release_ms
    assert cl.ev("PAUSED")[0].arg == pg.Source.BUTTON
    cl.ctl.act("button", name="pause", pressed=True)
    cl.run(5)
    assert cl.ev("RESUME_REQUEST")
    assert not cl.ctl.act("button", name="stop", pressed=True)["ok"]     # retired (ICD v0.5, D-36)
    assert cl.cmd(Cmd.HALT).ok and cl.ev("HALT_SET")[0].arg == pg.Source.PC
    r = cl.cmd(Cmd.RESUME)
    assert (r.status_name, r.detail) == ("E_STATE", int(pg.Block.HALT))
    assert cl.cmd(Cmd.HALT_CLEAR).ok and cl.ev("PAUSE_CLEARED")[0].arg == pg.PauseClearedReason.HALT_CLEAR
    cl.ctl.act("limit", name="end", active=True)
    cl.run(5)
    assert cl.ev("LIMIT_SET")[0].arg == pg.LimitId.END
    cl.ctl.act("limit", name="start", active=True)
    cl.run(5)
    assert cl.b.faults_mask & pg.Faults.LIMIT_WIRING
    cl.ctl.act("limit", name="start", active=False)
    cl.ctl.act("limit", name="end", active=False)
    cl.run(50)
    assert len(cl.ev("LIMIT_CLEARED")) == 2
    assert cl.cmd(Cmd.FAULT_CLEAR).ok
    cl.ctl.act("alm", active=True)
    cl.run(30)
    assert cl.ev("ALM_CHANGED")[0].arg == 1 and cl.data[-1].status & DS.ALM
    cl.ctl.override_status(set_bits=int(DS.POS_UNCERTAIN), duration_ms=50)
    cl.run(30)
    assert cl.data[-1].status & DS.POS_UNCERTAIN
    cl.ctl.emit_event(int(EV.NOT_SETTLED), 0, 200)
    cl.run(2)
    assert cl.ev("NOT_SETTLED")[0].value == 200


def _enable(cl: Client) -> None:
    assert P.decode_u16(cl.cmd(Cmd.ENABLE).body) == 500
    cl.run(510)
    assert cl.ev("DRIVER_ENABLED")


@pytest.mark.req("SYS-008", "FW-MOT-004", "FW-HOM-001")
def test_simple_motion_home_move_jog_stop() -> None:
    cl = Client()
    cl.hb = True
    cl.cmd(Cmd.STREAM_START)
    _enable(cl)
    cl.cmd(Cmd.SET_PARAM, P.build_set_param("home.v_fast_um_s", 20000))
    assert cl.cmd(Cmd.HOME, b"\x00").ok
    for _ in range(40):                                  # fast seek, back-off, slow approach, move to 0
        cl.run(1000)
        if cl.ev("HOMED"):
            break
    assert cl.ev("HOMED") and cl.ev("MOVE_DONE")[-1].arg == pg.MoveDoneReason.TARGET and cl.b.homed
    assert cl.b.pos_um == 0
    r = cl.cmd(Cmd.MOVE_ABS, P.build_request(Cmd.MOVE_ABS, target_um=10_000, v_um_s=20_000, a_um_s2=0))
    assert r.ok
    cl.run(100)
    assert cl.data[-1].flags & DF.MOVING
    busy = cl.cmd(Cmd.MOVE_ABS, P.build_request(Cmd.MOVE_ABS, target_um=5000, v_um_s=1, a_um_s2=0))
    assert busy.status_name == "E_BUSY"
    cl.run(1500)
    md = cl.ev("MOVE_DONE")[-1]
    assert md.arg == pg.MoveDoneReason.TARGET and md.value == 10_000 and md.value2 == 8_000
    # jog with dead-man
    assert cl.cmd(Cmd.JOG, P.build_request(Cmd.JOG, v_um_s=-2000, a_um_s2=0, bound_um=pg.JOG_NO_BOUND)).ok
    cl.run(600)
    assert cl.ev("STOPPED")[-1].arg == pg.StopCause.JOG_DEADMAN and not cl.b.motion
    # immediate STOP while moving → STOPPED + MOVE_DONE STOPPED; CLEAN halt: exact count, no POS_UNCERTAIN
    cl.cmd(Cmd.MOVE_ABS, P.build_request(Cmd.MOVE_ABS, target_um=50_000, v_um_s=20_000, a_um_s2=0))
    cl.run(200)
    cl.cmd(Cmd.STOP, b"\x00")
    cl.run(20)
    assert cl.ev("STOPPED")[-1].arg == pg.StopCause.PC_STOP and cl.ev("MOVE_DONE")[-1].arg == pg.MoveDoneReason.STOPPED
    assert not cl.data[-1].status & DS.POS_UNCERTAIN and cl.ev("MOVE_DONE")[-1].value2 == cl.b.steps
    # PAUSE while moving: controlled stop, latch blocks motion
    cl.cmd(Cmd.MOVE_ABS, P.build_request(Cmd.MOVE_ABS, target_um=60_000, v_um_s=20_000, a_um_s2=0))
    cl.run(200)
    cl.cmd(Cmd.PAUSE)
    cl.run(100)
    assert cl.ev("STOPPED")[-1].arg == pg.StopCause.PC_PAUSE and cl.b.paused
    r = cl.cmd(Cmd.MOVE_ABS, P.build_request(Cmd.MOVE_ABS, target_um=1000, v_um_s=1000, a_um_s2=0))
    assert r.status_name == "E_STATE" and r.detail & pg.Block.PAUSED
    assert cl.cmd(Cmd.RESUME).ok and cl.ev("PAUSE_CLEARED")[-1].arg == pg.PauseClearedReason.RESUME
    # MOVE_UNTIL_LOAD with a spring specimen
    cl.ctl.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=cl.b.pos_um + 1000)
    cl.ctl.act("afe", noise_counts=0)
    raw_stop = 50_000 + int(3285 * 100)                   # 100 N
    cl.cmd(Cmd.MOVE_UNTIL_LOAD, P.build_request(Cmd.MOVE_UNTIL_LOAD, bound_um=cl.b.pos_um + 20_000, v_um_s=2000,
                                                a_um_s2=0, raw_stop=raw_stop, cmp=0))
    cl.run(3000)
    assert cl.ev("MOVE_DONE")[-1].arg == pg.MoveDoneReason.LOAD_THRESHOLD
    # disable
    assert cl.cmd(Cmd.DISABLE).ok and cl.ev("DRIVER_DISABLED")[-1].arg == pg.DriverDisabledCause.PC_DISABLE


@pytest.mark.req("SAF-FW-015", "SYS-008")
def test_link_watchdog_and_step_fault() -> None:
    cl = Client()
    _enable(cl)
    cl.cmd(Cmd.SET_PARAM, P.build_set_param("motion.jog_timeout_ms", 1000))
    cl.cmd(Cmd.JOG, P.build_request(Cmd.JOG, v_um_s=1000, a_um_s2=0, bound_um=pg.JOG_NO_BOUND))
    cl.ctl.act("inject", fault="link_silence", duration_ms=1500)
    cl.run(1300)
    assert cl.ev("LINK_WDG")
    cl.run(300)
    cl.cmd(Cmd.PING)
    assert cl.ev("LINK_RESTORED")
    cl.ctl.act("inject", fault="step_fault")
    cl.run(2)
    assert cl.b.faults_mask & pg.Faults.STEP_FAULT
    cl.ctl.act("inject", fault="hang", duration_ms=50)
    cl.seq += 1
    cl.pair.pc.write(encode_frame(Cmd.PING, cl.seq, b""))
    cl.run(60)
    assert cl.seq not in cl.resp                           # dropped while the board hung


@pytest.mark.req("SYS-008")
def test_per_command_faults_and_timed_actions() -> None:
    cl = Client()
    cl.ctl.act("inject", fault="duplicate_next", cmd="PING", what="response")
    cl.cmd(Cmd.PING)
    cl.ctl.act("inject", fault="delay_next", cmd="PING", what="response", ms=30)
    cl.seq += 1
    cl.pair.pc.write(encode_frame(Cmd.PING, cl.seq, b""))
    cl.run(10)
    assert cl.seq not in cl.resp
    cl.run(30)
    assert cl.seq in cl.resp
    cl.ctl.act("inject", fault="corrupt_next", cmd="PING")
    cl.seq += 1
    cl.pair.pc.write(encode_frame(Cmd.PING, cl.seq, b""))
    cl.run(5)
    assert cl.seq not in cl.resp and cl.dec.counters.crc_errors == 1
    cl.ctl.act("inject", fault="delay_next", cmd="PING", what="request", ms=20)
    cl.seq += 1
    cl.pair.pc.write(encode_frame(Cmd.PING, cl.seq, b""))
    cl.run(15)
    assert cl.seq not in cl.resp
    cl.run(10)
    assert cl.seq in cl.resp                             # processed 20 ms late
    cl.ctl.act("inject", fault="duplicate_next", cmd="GET_STATUS", what="request")
    cl.cmd(Cmd.GET_STATUS)
    cl.ctl.act("on_frame", cmd="RESUME", nth=1, delay_us=2000, then={"action": "button", "name": "pause",
                                                                       "pressed": True})
    cl.cmd(Cmd.RESUME, wait=1)
    assert not cl.b.world.pause_btn
    cl.run(3)
    assert cl.b.world.pause_btn                           # ran 2 ms (virtual) after the RESUME was received
    cl.ctl.act("on_event", code="ALM_CHANGED", delay_us=1000, then={"action": "pend", "active": False})
    cl.ctl.act("alm", active=True)
    cl.run(5)
    assert not cl.b.world.pend
    assert cl.b.world.alm
    cl.ctl.act("rx_bytes", hex=encode_frame(Cmd.PING, 200, b"").hex())
    cl.run(2)
    assert 200 in cl.resp
    cl.ctl.act("rx_bytes", hex=encode_frame(Cmd.PING, 201, b"").hex(), at_us=cl.b.now_us() + 5000)
    cl.run(10)
    assert 201 in cl.resp
    cl.ctl.inject_nack("PING", "E_INTERNAL", 7)
    assert cl.cmd(Cmd.PING).status_name == "E_INTERNAL"
    wl = cl.ctl.act("query", what="wire_log")["frames"]
    assert wl and {"dir", "type", "seq", "first_us", "last_us", "hex"} <= set(wl[0])


@pytest.mark.req("SYS-008")
def test_vocabulary_matches_tools_readme() -> None:
    text = README.read_text(encoding="utf-8")
    sect = text[text.index("vocabulary v2"):]
    rows = re.findall(r"^\| `([a-z_]+)`(?: / `([a-z_]+)`)? \|.*\| (both|T|S)(?: \([^)]*\))? \|$", sect, re.M)
    names = {}
    for a, b, side in rows:
        names[a] = side
        if b:
            names[b] = side
    assert names and names == dict(VOCABULARY)
    cl = Client()
    for action, side in names.items():
        if side == "T" and action != "flash":
            assert cl.ctl.act(action) == {"ok": False, "error": "twin only"}
    assert cl.ctl.act("nope")["ok"] is False
    assert cl.ctl.act("inject", fault="isr_storm")["error"] == "inject: twin only"
    assert cl.ctl.act("afe", sck_overrun=True)["ok"] is False
    assert cl.ctl.act("query", what="seam_log")["ok"] is False
    assert cl.ctl.act("button", name="x", pressed=True)["ok"] is False
    for what in ("world", "pulses", "outputs", "sent", "flash", "edges"):
        assert cl.ctl.act("query", what=what)["ok"] is True
    assert cl.ctl.act("clock", advance_ms=10)["ok"] is True
    assert cl.ctl.act("reset", cause="iwdg")["ok"] and cl.b.reset_cause == pg.ResetCause.IWDG
    for a in (dict(action="wire", input="start", broken=True), dict(action="pend", active=False),
              dict(action="load_offset", counts=1000), dict(action="world_shift", um=5),
              dict(action="limit", name="start", position_um=-2000), dict(action="limit", name="end", position_um=5),
              dict(action="afe", rate_error=0.01, drop_every=3, miss_next=1, raw_script=[1, 2], saturate="neg")):
        name = a.pop("action")
        assert cl.ctl.act(name, **a)["ok"], name
    cl.ctl.set_estop(True)
    cl.ctl.press("pause")
    cl.ctl.release("pause")
    cl.ctl.set_specimen("none")
    with pytest.raises(KeyError):
        cl.ctl.inject_store_mismatch("no.key", 1)
    assert SimControl(cl.b).act("clock", advance_ms=1)["ok"] is False   # lock-step only


@pytest.mark.req("SYS-008")
def test_scenario_file(tmp_path) -> None:
    p = tmp_path / "s.simscn.json"
    p.write_text(json.dumps({
        "schema": "bird.bend.simscenario", "version": 1,
        "world": {"stroke_um": 300000, "start_switch_um": -1500, "end_switch_um": 301000, "cell_counts_per_n": 3285.0,
                  "load_offset_counts": 125000, "specimen": {"kind": "spring", "k_n_per_mm": 50.0,
                                                             "x_contact_um": 120000},
                  "afe": {"rate_sps": 80, "rate_error": 0.005, "noise_counts": 45},
                  "driver": {"lag_tau_ms": 5, "drv_power": True}, "estop_open": False},
        "params": {"motion.steps_per_mm": 160.0, "afe.rate_sps": "SPS80"},
        "schedule": [{"t_ms": 50, "action": "button", "name": "pause", "pressed": True}]}), encoding="utf-8")
    sc = SimScenario.load(p)
    cl = Client()
    sc.apply(cl.b, cl.ctl)
    assert cl.b.afe.offset_counts == 125000 and cl.b.params["motion.steps_per_mm"] == 160.0
    assert cl.b.world.specimen.kind == "spring"
    cl.run(60)
    assert cl.b.paused
    with pytest.raises(ValueError):
        SimScenario.from_dict({"schema": "x"})
    with pytest.raises(ValueError):
        SimScenario.from_dict({"schema": "bird.bend.simscenario", "version": 2})
    assert default_scenario().world["load_offset_counts"] == 50_000


@pytest.mark.req("SYS-008", "D-07")
def test_out_of_process_server_over_tcp() -> None:
    srv = SimServer(port=0, ctl_port=0)
    srv.start()
    try:
        tr = TcpTransport("127.0.0.1", srv.port)
        tr.open()
        tr.write(encode_frame(Cmd.GET_INFO, 9, b""))
        dec = FrameDecoder()
        got = []
        for _ in range(100):
            got += dec.feed(tr.read(4096, 0.05))
            if any(f.type == (Cmd.GET_INFO | 0x80) for f in got):
                break
        resp = next(f for f in got if f.type == (Cmd.GET_INFO | 0x80))
        assert P.decode_info(P.split_response(resp.type, resp.seq, resp.payload).body).build.startswith("SIM")
        with socket.create_connection(("127.0.0.1", srv.ctl_port), timeout=2) as s:
            f = s.makefile("rw", encoding="utf-8", newline="\n")
            f.write(json.dumps({"action": "query", "what": "outputs"}) + "\n")
            f.flush()
            assert json.loads(f.readline())["ok"] is True
            f.write("not json\n")
            f.flush()
            assert json.loads(f.readline())["ok"] is False
        tr.close()
    finally:
        srv.stop()


@pytest.mark.req("SYS-008")
def test_no_backward_time_after_hang() -> None:
    """SWD-M1-11: after an injected hang the HX711 model resumes at 'now' (no overdue stamps)."""
    cl = Client()
    cl.cmd(Cmd.STREAM_START)
    cl.run(300)
    cl.ctl.act("inject", fault="hang", duration_ms=3000)
    cl.run(3500)
    t = [d.t_us for d in cl.data]
    assert all(b > a for a, b in zip(t, t[1:], strict=False))
    assert any(b - a > 2_000_000 for a, b in zip(t, t[1:], strict=False))
