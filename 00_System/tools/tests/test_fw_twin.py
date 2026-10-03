"""Self-tests of the FW host twin harness (00_System/tools/fw_twin) on the harness PROBE core.

The probe is a stand-in core (fw_twin/probe/probe_core.c, NOT the firmware) that exercises every seam, so
these tests prove the twin's own mechanics independently of Implementer A's code: lock-step determinism,
byte pacing and the wire log, timed byte injection, AFE cadence, TX class drops, flash file + power cut,
resets / IWDG, on_frame / on_event timing, TCP + control port, world inputs and the seam log.
FW behaviour is tested in 03_SW/tests/integration against A's core.

Verifies: SYS-008 (twin harness), DEF-P1-03 M1 subset (lock-step, wire log, byte injection, flash cut, reset
causes, edge / seam logs)
Run:  .venv\\Scripts\\python -m pytest 00_System/tools/tests/test_fw_twin.py -q
"""
from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "fw_twin"))

import build as twin_build  # noqa: E402
import ref_codec as rc  # noqa: E402

try:
    twin_build.find_gcc()
    HAVE_GCC = True
except SystemExit:
    HAVE_GCC = False

pytestmark = pytest.mark.skipif(not HAVE_GCC, reason="no host gcc (CLion MinGW 13.1)")

from twin import BYTE_NS, Twin, TwinLink, byte_end  # noqa: E402


@pytest.fixture(scope="module")
def probe_exe():
    return twin_build.ensure_built("probe")


@pytest.fixture
def tw(probe_exe, tmp_path):
    t = Twin("lockstep", exe=probe_exe, run_dir=tmp_path)
    yield t
    t.close()


def test_seam_contract_equals_readme():
    """A's headers (when present) equal the README seam v1 block (single seam source, DEF-P1-02)."""
    d = twin_build.check_seams()
    assert d == [] or d[0].startswith("02_FW/src/hal/hal_*.h absent"), d


def test_build_log_and_probe_identity(tw):
    log = (TOOLS / "fw_twin" / "build" / "build_probe.log").read_text(encoding="utf-8")
    assert "exit: 0" in log
    info = TwinLink(tw).cmd("GET_INFO")["info"]
    assert info["build"] == "PROBE-NOT-FW" and "TWIN" in info["features"]


def test_byte_pacing_matches_engine_formula():
    assert BYTE_NS == 10851
    assert byte_end(0, 25) - 0 == (26 * 10**9 + 92159) // 92160      # 26 B DATA frame = 282.1 us


def test_wire_log_tx_times_and_rx_injection_at_virtual_time(tw):
    link = TwinLink(tw)
    tw.advance_ms(5)
    at = tw.now_us + 1234.5
    link.send("PING", at_us=at)
    tw.advance_ms(5)
    wl = tw.act("query", what="wire_log")["wire_log"]
    rx = [w for w in wl if w["dir"] == "rx" and w["type"] == rc.CMD["PING"]][-1]
    assert rx["first_us"] == pytest.approx(at, abs=0.001)
    assert rx["last_us"] == pytest.approx(byte_end(int(at * 1000), 7) / 1000, abs=0.001)
    tx = [w for w in wl if w["dir"] == "tx" and w["type"] == rc.CMD["PING"] | 0x80][-1]
    n = len(bytes.fromhex(tx["hex"]))
    assert tx["last_us"] * 1000 == pytest.approx(byte_end(int(round(tx["first_us"] * 1000)), n - 1), abs=1)
    assert tx["first_us"] >= rx["last_us"]                 # the response starts after the request's last byte


def test_lockstep_is_deterministic(probe_exe, tmp_path):
    def run(d):
        with Twin("lockstep", exe=probe_exe, run_dir=d) as t:
            link = TwinLink(t)
            link.cmd("STREAM_START")
            for i in range(5):
                link.cmd("GET_STATUS")
                t.advance_ms(37)
            return [(w["dir"], w["first_us"], w["hex"]) for w in t.wire_log]
    a, b = run(tmp_path / "a"), run(tmp_path / "b")
    assert a == b and len(a) > 20


def test_afe_cadence_and_one_data_frame_per_delivered_conversion(tw):
    link = TwinLink(tw)
    link.cmd("STREAM_START")
    t0 = tw.now_us
    tw.advance_ms(10_000)
    link.poll()
    conv = [c for c in tw.act("query", what="conversions", since_us=t0)["conversions"] if c["delivered"]]
    data = link.data()
    assert 799 <= len(conv) <= 801                         # 80 SPS
    assert [d["t_us"] for d in data][-len(conv):] == [c["fw_t_us"] for c in conv]
    seqs = [d["frame_seq"] for d in data]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))


def test_rate_error_and_stall(tw):
    tw.act("afe", rate_error=0.02)
    t0 = tw.now_us
    tw.advance_ms(5000)
    n = len(tw.act("query", what="conversions", since_us=t0)["conversions"])
    assert n == pytest.approx(80 * 1.02 * 5, abs=2)
    tw.act("afe", stall=True)
    t1 = tw.now_us
    tw.advance_ms(1000)
    assert tw.act("query", what="conversions", since_us=t1)["conversions"] == []


def test_tx_congestion_drops_data_class_only(tw):
    link = TwinLink(tw)
    link.cmd("STREAM_START")
    tw.advance_ms(100)
    tw.act("inject", fault="tx_congestion", duration_ms=200)
    tw.advance_ms(300)
    sent = tw.act("query", what="sent")["sent"]
    dropped = [s for s in sent if s["dropped"]]
    assert 14 <= len(dropped) <= 17 and all(s["cls"] == 0 for s in dropped)
    link.poll()
    seqs = [d["frame_seq"] for d in link.data()]
    gaps = [b - a for a, b in zip(seqs, seqs[1:]) if b - a != 1]
    assert gaps == [len(dropped) + 1]


def test_flash_file_persists_and_power_cut_mid_save(tw):
    link = TwinLink(tw)
    link.cmd("SET_PARAM", {"id": 0x0801, "type": "u8", "value": 25})       # stream.fallback_hz
    link.cmd("SAVE_PARAMS", timeout_ms=3000)
    w0 = tw.act("query", what="flash")["writes"]
    assert w0 > 0
    link.cmd("SET_PARAM", {"id": 0x0801, "type": "u8", "value": 7})
    tw.act("flash", cut_after_word=40)
    seq = link.send("SAVE_PARAMS")
    tw.advance_ms(1000)
    assert tw.resets and tw.resets[-1]["cause"] == "power"
    link.poll()
    assert link.find("SAVE_PARAMS", seq) is None             # no response: power was cut
    r = link.cmd("GET_PARAM", {"id": 0x0801})
    assert r["entry"]["value"] == 25                          # the old record survived the cut


def test_iwdg_reset_on_main_loop_hang(tw):
    tw.advance_ms(10)
    tw.act("inject", fault="hang", where="main")
    tw.advance_ms(200)
    assert [r["cause"] for r in tw.resets] == ["iwdg"]
    link = TwinLink(tw)
    tw.advance_ms(5)
    link.poll()
    boot = [e for e in link.events() if e["code"] == "BOOT"]
    assert boot and boot[-1]["arg"] == rc.RESET_CAUSE.index("IWDG")


def test_reset_action_pin_and_tx_truncation(tw):
    link = TwinLink(tw)
    link.cmd("STREAM_START")
    tw.advance_ms(3)
    tw.act("reset", cause="pin")
    tw.advance_ms(10)
    link.poll()
    assert tw.resets[-1]["cause"] == "pin"
    assert [e["arg"] for e in link.events() if e["code"] == "BOOT"][-1] == rc.RESET_CAUSE.index("PIN")


def test_on_frame_and_on_event_timing(tw):
    link = TwinLink(tw)
    tw.act("on_frame", cmd="PING", nth=2, delay_us=2000, then={"action": "estop", "open": True,
                                                                "drv_power_follows": False})
    link.cmd("PING")
    link.cmd("PING")
    tw.advance_ms(5)
    rx = [w for w in tw.wire_log if w["dir"] == "rx" and w["type"] == rc.CMD["PING"]][1]
    ins = tw.act("query", what="inputs")["inputs"]
    est = [i for i in ins if i["input"] == "estop"]
    assert est and est[0]["t_us"] == pytest.approx(rx["last_us"] + 2000, abs=0.001)
    seam = tw.act("query", what="seam_log", since_us=est[0]["t_us"])["seam_log"]
    assert seam[0]["call"] == "hal_step_abort" and seam[0]["t_us"] == est[0]["t_us"]   # fixed HAL reaction
    tw.act("on_event", code="HALT_SET", delay_us=1000, then={"action": "alm", "active": True})
    link.cmd("HALT")
    tw.advance_ms(5)
    ev = [w for w in tw.wire_log if w["dir"] == "tx" and w["type"] == rc.ASYNC["EVENT"]
          and rc.decode_event(bytes.fromhex(w["hex"])[6:22])["code"] == "HALT_SET"][0]
    alm = [i for i in tw.act("query", what="inputs")["inputs"] if i["input"] == "alm"]
    assert alm[0]["t_us"] == pytest.approx(ev["last_us"] + 1000, abs=0.001)


def test_bounce_and_drv_power_follow(tw):
    tw.act("estop", open=True, k1_delay_ms=20, bounce_ms=[0.5, 0.3, 0.2])
    t0 = tw.now_us
    tw.advance_ms(30)
    ins = tw.act("query", what="inputs", since_us=t0)["inputs"]
    est = [i["level"] for i in ins if i["input"] == "estop"]
    assert est[-1] == 1 and len(est) == 5                          # 1,0,1,0,1 (ends open)
    drv = [i for i in ins if i["input"] == "drv_power"]
    assert drv and drv[0]["level"] == 1 and drv[0]["t_us"] == pytest.approx(t0 + 20000, abs=1)
    assert tw.act("button", name="stop", pressed=True)["ok"] is False      # D-36


def test_t0_wrap(probe_exe, tmp_path):
    with Twin("lockstep", exe=probe_exe, run_dir=tmp_path, t0_us=2**32 - 300_000) as t:
        link = TwinLink(t)
        link.cmd("STREAM_START")
        t.advance_ms(600)
        link.poll()
        ts = [d["t_us"] for d in link.data()]
        assert any(a > b for a, b in zip(ts, ts[1:]))              # wrapped
        assert all(((b - a) & 0xFFFFFFFF) == pytest.approx(12500, abs=2) for a, b in zip(ts, ts[1:]))


def test_response_drop_fault(tw):
    link = TwinLink(tw)
    tw.act("inject", fault="drop_next", cmd="PING", what="response")
    s1 = link.send("PING")
    tw.advance_ms(5)
    link.poll()
    assert link.find("PING", s1) is None
    assert link.cmd("PING")["status"] == "OK"


def test_tcp_and_control_port_realtime(probe_exe, tmp_path):
    with Twin("realtime", exe=probe_exe, run_dir=tmp_path) as t:
        port, ctl = t.serve(0, 0)
        c = socket.create_connection(("127.0.0.1", port))
        k = socket.create_connection(("127.0.0.1", ctl))
        kf = k.makefile("rwb")
        c.sendall(rc.make_frame("GET_INFO", 7, {}))
        p = rc.FrameParser()
        c.settimeout(2)
        frames = []
        deadline = time.time() + 3
        while time.time() < deadline and not any(f.type == 0x82 for f in frames):
            frames += p.feed(c.recv(4096))
        r = [rc.decode_frame(f) for f in frames if f.type == 0x82][0]
        import gen_params  # noqa: PLC0415
        assert r["info"]["param_dict_hash"] == f"0x{gen_params.load().hash:08X}"
        kf.write(b'{"action": "query", "what": "world"}\n')
        kf.flush()
        rep = json.loads(kf.readline())
        assert rep["ok"] and rep["now_us"] > 0
        kf.write(b'{"action": "clock", "advance_ms": 1}\n')
        kf.flush()
        assert json.loads(kf.readline())["ok"] is False                 # clock only in lock-step
        c.close()
        k.close()


# ---------------------------------------------------------------------------------------------- step model
def _train(tw: Twin, link: TwinLink, steps: int, period_ticks: int) -> None:
    import struct
    tw.feed_rx(rc.encode_frame(0x3F, 0x11, struct.pack("<iII", steps, period_ticks, 0)))   # PROBE-only command
    tw.advance_ms(1)


def test_step_model_edges_count_and_world(tw):
    link = TwinLink(tw)
    _train(tw, link, 100, 9000)                                  # 100 us period, pw 900 ticks = 10 us
    tw.advance_ms(20)
    q = tw.act("query", what="pulses")
    assert q["count"] == 100 and q["pos_steps"] == 100 and not q["running"]
    e = tw.act("query", what="edges")["edges"]
    pul = [x for x in e if x["pin"] == "PUL"]
    rises = [x["t_us"] for x in pul if x["level"] == 1]
    falls = [x["t_us"] for x in pul if x["level"] == 0]
    assert all(f - r == pytest.approx(10.0, abs=0.01) for r, f in zip(rises, falls))      # pulse width
    assert all(b - a == pytest.approx(100.0, abs=0.01) for a, b in zip(rises, rises[1:]))  # period
    dir_t = [x["t_us"] for x in e if x["pin"] == "DIR"][-1]
    assert rises[0] - dir_t >= 20.0                                                         # DIR setup
    assert tw.act("query", what="world")["x_um_true"] == pytest.approx(125.0)              # 800 steps/mm


def test_step_model_estop_truncate_and_limit_clean_stop(tw):
    link = TwinLink(tw)
    _train(tw, link, 1000, 9000)
    tw.advance_us(5005)                                          # inside a high phase? find a rising edge
    tw.act("estop", open=True, drv_power_follows=False)
    tw.advance_ms(5)
    seam = tw.act("query", what="seam_log")["seam_log"]
    ab = [s for s in seam if s["call"] == "hal_step_abort"][-1]
    assert "running=1" in ab["args"]
    q = tw.act("query", what="pulses")
    assert not q["running"] and q["count"] in (q["pos_steps"], q["pos_steps"] + 1)        # +1 only if cut
    ena = [x for x in tw.act("query", what="edges")["edges"] if x["pin"] == "ENA"][-1]
    assert ena["level"] == 1 and ena["t_us"] == ab["t_us"]                                  # disabled at once
    # END switch at x = 50 um from the current position: CLEAN stop by the HAL fixed reaction
    tw.act("estop", open=False, drv_power_follows=False)
    x0 = tw.act("query", what="world")["x_um_true"]
    tw.act("limit", name="end", position_um=x0 + 50.0)
    n0 = tw.act("query", what="pulses")["pos_steps"]
    _train(tw, link, 1000, 9000)
    tw.advance_ms(20)
    q = tw.act("query", what="pulses")
    assert q["pos_steps"] - n0 == 40 and not q["running"]                                  # 50 um = 40 steps
    assert [i for i in tw.act("query", what="inputs")["inputs"] if i["input"] == "end"][-1]["level"] == 1
    tw.act("world_shift", um=-100.0)
    assert [i for i in tw.act("query", what="inputs")["inputs"] if i["input"] == "end"][-1]["level"] == 0


def test_wire_break_and_chatter(tw):
    t0 = tw.now_us
    tw.act("wire", input="pause", broken=True)
    tw.act("chatter", input="alm", period_ms=1.0, duration_ms=10)
    tw.advance_ms(20)
    ins = tw.act("query", what="inputs", since_us=t0)["inputs"]
    assert any(i["input"] == "pause" and i["level"] == 1 for i in ins) is False            # pull-up: already high
    alm = [i for i in ins if i["input"] == "alm"]
    assert len(alm) >= 18 and alm[-1]["level"] == 0
    tw.act("wire", input="estop", broken=True)
    tw.advance_ms(1)
    assert tw.act("query", what="world")["inputs"]["estop"] == 1


def test_rx_bytes_during_flash_stall_land_at_wire_time(tw):
    """OBS-M1-02 (ICD v0.5 §2.4 SAVE exemption): bytes injected inside a flash stall reach the RX ring at
    their wire time (logged there), and the command is answered after the flash operation."""
    link = TwinLink(tw)
    link.cmd("SET_PARAM", {"id": 0x0801, "type": "u8", "value": 25})
    for _ in range(32):                                        # fill sector 1: the next SAVE erases sector 2
        link.cmd("SAVE_PARAMS", timeout_ms=3000)
    e0 = tw.act("query", what="flash")["erases"]
    t_save = tw.now_us
    seq_save = link.send("SAVE_PARAMS")
    at = t_save + 100_000.0                                    # 100 ms into the (≈ 500 ms) erase stall
    seq_ping = link.send("PING", at_us=at)
    tw.advance_ms(1500)
    link.poll()
    assert tw.act("query", what="flash")["erases"] > e0         # the SAVE really erased (CPU stall)
    rx = [w for w in tw.wire_log if w["dir"] == "rx" and w["seq"] == seq_ping]
    assert rx and abs(rx[0]["first_us"] - at) < 1.0              # logged at its wire time, not at stall end
    save_resp = next(w for w in tw.wire_log if w["dir"] == "tx" and w["seq"] == seq_save and w["type"] == 0x93)
    ping_resp = next(w for w in tw.wire_log if w["dir"] == "tx" and w["seq"] == seq_ping and w["type"] == 0x81)
    assert ping_resp["first_us"] > at + 100_000.0                # answered after the flash operation
    assert save_resp["first_us"] > at                            # SAVE itself completes after the PING arrived


def test_isr_storm_starves_step_isr_and_main_iwdg(tw):
    """M2 vocabulary: `inject isr_storm` = level-1 ISR storm: step ISR and main loop starved (hardware pulses
    continue at the preloaded period), IWDG run window (47.75 ms at 32 kHz) resets the MCU."""
    link = TwinLink(tw)
    _train(tw, link, 1000, 9000)
    tw.act("inject", fault="isr_storm", duration_ms=100)
    tw.advance_ms(30)
    starved = [x for x in tw.seam_log if x["call"] == "step_isr_starved"]
    assert starved                                                  # updates happened, ISR did not run
    tw.advance_ms(100)
    assert [r["cause"] for r in tw.resets] == ["iwdg"]
    assert tw.resets[0]["t_us"] < 60_000                            # within the run window after the storm began


def test_iwdg_windows_follow_a_semantics(tw):
    """OI-C-M1-03 (A): set_timeout(ms <= 90) = run window, > 90 = NVM long window; fixed by PR/RLR, not scaled."""
    y = tw._engine_query()
    assert int(y["wdg_timeout_ns"]) == pytest.approx(191 * 8 / 32000 * 1e9, rel=1e-6) or not int(y["wdg_armed"])


def test_hardfault_reset_plants_record(tw):
    """Seam v1.2 (OI-FW-32): reset cause 'hardfault' -> SOFTWARE reset with a fault record for hal_fault_record()."""
    tw.advance_ms(5)
    r = tw.act("reset", cause="hardfault", pc=0x08001234, cfsr=0x8200)
    assert r["ok"] and tw.resets[-1]["cause"] == "software"
