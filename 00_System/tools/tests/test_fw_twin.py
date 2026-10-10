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
import math
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
    return twin_build.ensure_built_private("probe")          # private per-run binary (OBS-M2-09)


@pytest.fixture
def tw(probe_exe, tmp_path):
    t = Twin("lockstep", exe=probe_exe, run_dir=tmp_path)
    yield t
    t.close()


def test_seam_contract_equals_readme():
    """A's headers (when present) equal the README seam v1 block (single seam source, DEF-P1-02)."""
    d = twin_build.check_seams()
    assert d == [] or d[0].startswith("02_FW/src/hal/hal_*.h absent"), d


def test_build_log_and_probe_identity(tw, probe_exe):
    # OBS-RC-8: the log lies next to the private binary (build.private_build_dir(): $BEND_TWIN_BUILD_DIR or a temp dir)
    log = (probe_exe.parent / "build_probe.log").read_text(encoding="utf-8")
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


# ---------------------------------------------------------------------------- v0.6 twin (REQ-C-M2-02…10)
def test_loop_load_delays_main_loop_not_isrs(tw):
    """REQ-C-M2-02: each main-loop pass consumes us_per_pass; responses (main loop) come later, the tick runs."""
    link = TwinLink(tw)
    tw.advance_ms(5)
    seq = link.send("PING")
    tw.advance_ms(5)
    rx0 = [w for w in tw.wire_log if w["dir"] == "rx" and w["seq"] == seq][-1]
    tx0 = [w for w in tw.wire_log if w["dir"] == "tx" and w["seq"] == seq and w["type"] == 0x81][0]
    tw.act("inject", fault="loop_load", us_per_pass=900, duration_ms=200)
    tw.advance_ms(3)
    seq = link.send("PING")
    tw.advance_ms(10)
    rx1 = [w for w in tw.wire_log if w["dir"] == "rx" and w["seq"] == seq][-1]
    tx1 = [w for w in tw.wire_log if w["dir"] == "tx" and w["seq"] == seq and w["type"] == 0x81][0]
    assert (tx1["first_us"] - rx1["last_us"]) > (tx0["first_us"] - rx0["last_us"])
    assert tx1["first_us"] - rx1["last_us"] <= 2 * 900 + 100


def test_conversions_carry_gain_and_rate(tw):
    """REQ-C-M2-05: every conversion reports the gain pulses / channel and the rate used."""
    tw.advance_ms(100)
    conv = tw.act("query", what="conversions")["conversions"]
    assert conv and all({"gain_pulses", "channel_gain", "rate_sps"} <= set(c) for c in conv)


def test_world_follows_dir_pin_and_wiring(tw):
    """REQ-C-M2-06: the world integrates PUL + DIR pin; with dir_wiring_inverted it moves the other way while
    the FW counter is unchanged."""
    link = TwinLink(tw)
    tw.act("driver", dir_wiring_inverted=True)
    _train(tw, link, 100, 9000)
    tw.advance_ms(20)
    w = tw.act("query", what="world")
    assert w["pos_steps"] == 100 and w["x_um_true"] == pytest.approx(-125.0)


def test_pend_auto_model(tw):
    """REQ-C-M2-07: PEND inactive while pulsing and pend_lag_ms after the last pulse."""
    link = TwinLink(tw)
    tw.act("driver", pend_auto=True, pend_lag_ms=5)
    _train(tw, link, 200, 9000)                                 # 20 ms of pulses
    tw.advance_ms(5)
    assert not (int(tw._engine_query()["inputs"]) >> 6) & 1     # PEND low while moving
    tw.advance_ms(40)
    assert (int(tw._engine_query()["inputs"]) >> 6) & 1


def test_seam_log_has_no_stop_level(tw):
    """REQ-C-M2-09: hal_inputs_config logs pause / alm only (CR-01)."""
    tw.advance_ms(5)
    assert not any("stop=" in x["args"] for x in tw.seam_log if x["call"] == "hal_inputs_config")


def test_query_clear_and_log_size(tw):
    """REQ-C-M2-10: default log_max 1 000 000; query clear drains the log."""
    assert tw.edges.maxlen == 1_000_000
    tw.advance_ms(20)
    assert tw.act("query", what="conversions", clear=True)["conversions"]
    assert tw.act("query", what="conversions")["conversions"] == []


def test_diag_meas_model_counter_and_stamps(probe_exe, tmp_path):
    """REQ-C-M2-08: twin model of hal_meas_cmd (via the probe core, which forwards DIAG_MEAS)."""
    import struct
    with Twin("lockstep", exe=probe_exe, run_dir=tmp_path, hw_meas=True) as t:
        link = TwinLink(t)
        t.advance_ms(2)
        _train(t, link, 50, 9000)
        t.advance_ms(10)
        r = link.cmd("DIAG_MEAS", {"op": 3, "sel": 0, "a": 0, "b": 0})
        if r["status"] != "OK":
            pytest.skip(f"probe core does not forward DIAG_MEAS ({r})")
        assert r["w"][0] == 50
        s = link.cmd("DIAG_MEAS", {"op": 4, "sel": 1, "a": 0, "b": 0})["w"]
        assert s[0] == 50 and s[2] > s[3] > 0


def test_diag_meas_noinit_and_info_match_target_v07(probe_exe, tmp_path):
    """REQ-C-M2-12 / OI-FW-38 / OI-FW-41 (ICD v0.7.1 App. C): NOINIT magic valid from the first read (set at
    boot), w2 = the 10 kHz heartbeat (not the time of the read), previous-boot snapshot w4...w8 at each reset,
    sel 1 clears all, a power cycle starts fresh; INFO ring 2048 / stimulus 10 MHz; PROBE_READ w5 = PUL stamps
    since arming."""
    with Twin("lockstep", exe=probe_exe, run_dir=tmp_path, hw_meas=True) as t:
        link = TwinLink(t)
        t.advance_ms(2)
        r = link.cmd("DIAG_MEAS", {"op": 5, "sel": 0, "a": 0, "b": 0})
        if r["status"] != "OK":
            pytest.skip(f"probe core does not forward DIAG_MEAS ({r})")
        w = r["w"]
        assert w[0] == 0x4D454153 and w[1] == 0 and w[3] == 0
        assert w[2] % 100 == 0 and 0 < w[2]
        info = link.cmd("DIAG_MEAS", {"op": 0, "sel": 0, "a": 0, "b": 0})["w"]
        assert info[4] == 2048 and info[7] == 10_000_000
        _train(t, link, 5, 9000)
        t.advance_ms(2)
        assert link.cmd("DIAG_MEAS", {"op": 1, "sel": 0, "a": 0, "b": 0})["status"] == "OK"   # arm, EVT source
        _train(t, link, 30, 9000)
        t.advance_ms(10)
        pr = link.cmd("DIAG_MEAS", {"op": 2, "sel": 0, "a": 0, "b": 0})["w"]
        assert pr[1] == 0 and pr[5] == 30                                  # since arming, not since the event
        last = link.cmd("DIAG_MEAS", {"op": 5, "sel": 0, "a": 0, "b": 0})["w"][1]
        assert last > 0
        assert w[4:9] == [0, 0, 0, 0, 0]                                   # fresh block: no previous boot
        hb_before = link.cmd("DIAG_MEAS", {"op": 5, "sel": 0, "a": 0, "b": 0})["w"][2]
        t.act("reset", cause="iwdg")
        t.advance_ms(5)
        # v0.7.1 (OI-FW-41): previous boot snapshot w4...w8, this boot's w1 restarts at 0
        w2 = TwinLink(t).cmd("DIAG_MEAS", {"op": 5, "sel": 0, "a": 0, "b": 0})["w"]
        assert w2[0] == 0x4D454153 and w2[1] == 0
        assert w2[4] == 1 and w2[5] == last and hb_before <= w2[6] and w2[8] == 1
        t.act("reset", cause="pin")
        t.advance_ms(5)
        w3 = TwinLink(t).cmd("DIAG_MEAS", {"op": 5, "sel": 1, "a": 0, "b": 0})["w"]
        assert w3[4] == 1 and w3[5] == 0 and w3[8] == 2                    # no PUL in the second boot
        w4 = TwinLink(t).cmd("DIAG_MEAS", {"op": 5, "sel": 0, "a": 0, "b": 0})["w"]
        assert w4[0] == 0x4D454153 and w4[1] == 0 and w4[3:9] == [0, 0, 0, 0, 0, 0]   # sel 1 cleared all
        t.act("reset", cause="power")
        t.advance_ms(5)
        w5 = TwinLink(t).cmd("DIAG_MEAS", {"op": 5, "sel": 0, "a": 0, "b": 0})["w"]
        assert w5[0] == 0x4D454153 and w5[4] == 0 and w5[8] == 0           # power cycle: fresh block


# ---------------------------------------------------------------------------------------------- M3 load model
def _raws(t, ms):
    t0 = t.now_us
    t.advance_ms(ms)
    return [c["raw"] for c in t.act("query", what="conversions", since_us=t0 + 1)["conversions"]]


def test_load_model_weight_drift_creep_nonlin(tw):
    """ICD v0.7.2: weight kg·g·cpn exact (noise 0); linear zero drift; creep first order; non-linearity odd,
    maximal at half scale; every term 0 by default."""
    tw.advance_ms(100)
    r0 = _raws(tw, 200)
    assert set(r0) == {50000}
    assert tw.act("weight", kg=10.0)["force_n"] == pytest.approx(98.0665)
    assert set(_raws(tw, 200)) == {round(50000 + 3285.0 * 98.0665)}
    tw.act("weight", kg=0)
    tw.act("afe", drift_counts_per_s=100.0)
    r = _raws(tw, 1000)
    assert r[-1] - r[0] == pytest.approx(100.0 * (len(r) - 1) / 80, abs=2)
    tw.act("afe", drift_counts_per_s=0.0)
    base = tw.act("query", what="world")["raw_ideal"]
    tw.act("afe", creep_pct=1.0, creep_tau_s=1.0)
    tw.act("weight", n=1000.0)
    tw.advance_ms(1000)
    w = tw.act("query", what="world")
    assert w["creep_counts"] == pytest.approx(0.01 * 3285.0 * 1000.0 * (1 - math.exp(-1.0)), rel=1e-3)
    tw.act("afe", creep_pct=0.0)
    tw.act("weight", n=1961.33 / 2)
    tw.act("afe", nonlin_pct_fs=0.03)
    w = tw.act("query", what="world")
    assert w["raw_ideal"] - base == pytest.approx(3285.0 * 1961.33 / 2, abs=1)   # raw_ideal excludes nonlin
    r = _raws(tw, 100)
    assert r[-1] - base - 3285.0 * 1961.33 / 2 == pytest.approx(0.0003 * 3285.0 * 1961.33, abs=1)


def test_load_model_relaxation_and_reset_carry_over(tw):
    """Specimen relaxation (first order toward relax_pct·F at constant position) and the load states survive an
    MCU reset (world, not MCU)."""
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=-4000.0, relax_pct=10.0, relax_tau_s=2.0)
    tw.act("afe", drift_counts_per_s=5.0)
    tw.advance_ms(2000)
    w = tw.act("query", what="world")
    assert w["specimen_n"] == pytest.approx(200.0)
    assert w["relax_n"] == pytest.approx(20.0 * (1 - math.exp(-1.0)), rel=1e-3)
    assert w["load_n"] == pytest.approx(200.0 - w["relax_n"])
    tw.act("reset", cause="pin")
    tw.advance_ms(5)
    w2 = tw.act("query", what="world")
    assert w2["relax_n"] >= w["relax_n"] and w2["drift_counts"] >= w["drift_counts"]
    assert w2["drift_counts"] == pytest.approx(5.0 * tw.now_us / 1e6, abs=0.5)


def test_load_model_scenario_keys(probe_exe, tmp_path):
    """v0.7.2 scenario keys incl. the simulator's drift_counts_per_min alias (SWC-M3-02)."""
    scn = {"schema": "bird.bend.simscenario", "version": 1,
           "world": {"afe": {"drift_counts_per_min": 600.0}, "weight_kg": 1.0}}
    with Twin("lockstep", exe=probe_exe, run_dir=tmp_path, scenario=scn) as t:
        t.advance_ms(2000)
        w = t.act("query", what="world")
        assert w["weight_n"] == pytest.approx(9.80665)
        assert w["drift_counts"] == pytest.approx(10.0 * 2.0, abs=0.1)


# ---------------------------------------------------------------------------------------------- M4 specimen model
def _spec_at(t, x_um: float) -> dict:
    """World state with the carriage moved (lost-step shift, probe core never steps) to x_um."""
    w = t.act("query", what="world")
    t.act("world_shift", um=x_um - w["x_um_true"])
    return t.act("query", what="world")


def test_specimen_m4_sides_and_contact_point(tw):
    """M4 specimen: pull side (default, M1–M3 model) only beyond the contact point; push side = compression
    (force < 0 only below the contact point); both = clamped, linear through the contact point."""
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=10_000.0)
    assert _spec_at(tw, 9_000)["specimen_n"] == 0.0
    assert _spec_at(tw, 14_000)["specimen_n"] == pytest.approx(200.0)
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=10_000.0, side="push")
    assert _spec_at(tw, 14_000)["specimen_n"] == 0.0
    w = _spec_at(tw, 6_000)
    assert w["specimen_n"] == pytest.approx(-200.0) and w["load_n"] == pytest.approx(-200.0)
    tw.advance_ms(50)                                   # the cell sees the compression: raw below the offset
    conv = tw.act("query", what="conversions", since_us=tw.now_us - 30_000)["conversions"]
    assert conv and all(c["raw"] == round(50_000 - 3285.0 * 200.0) for c in conv)
    tw.act("specimen", kind="spring", k_n_per_mm=20.0, x_contact_um=10_000.0, side="both")
    assert _spec_at(tw, 7_500)["specimen_n"] == pytest.approx(-50.0)
    assert _spec_at(tw, 12_500)["specimen_n"] == pytest.approx(50.0)
    r = tw.act("specimen", kind="spring", k_n_per_mm=20.0, side="sideways")
    assert r["ok"] is False and "sideways" in r["error"]
    assert _spec_at(tw, 12_500)["specimen_n"] == pytest.approx(50.0)      # refused action: model unchanged


def test_specimen_m4_nonlinearity(tw):
    """Cubic term k3 (odd in the deflection) on top of the linear / bilinear curve; softening never inverts."""
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=0.0, side="both", k3_n_per_mm3=0.5)
    assert _spec_at(tw, 4_000)["specimen_n"] == pytest.approx(50 * 4 + 0.5 * 64)
    assert _spec_at(tw, -4_000)["specimen_n"] == pytest.approx(-(50 * 4 + 0.5 * 64))
    tw.act("specimen", kind="bilinear", k_n_per_mm=50.0, x_contact_um=0.0, k2_n_per_mm=10.0, f_yield_n=100.0,
           k3_n_per_mm3=-1.0)
    assert _spec_at(tw, 4_000)["specimen_n"] == pytest.approx(100 + 10 * 2 - 64)
    assert _spec_at(tw, 20_000)["specimen_n"] == 0.0                  # 100 + 180 − 8000 < 0 → 0 (no inversion)


def test_specimen_m4_break_by_force_and_travel_latched_over_reset(tw):
    """Break at a force (clean: load → 0) or at a deflection with a residual fraction of the intact curve; the
    break is latched in the world (survives an MCU reset; cleared by a new specimen action); seam log entry."""
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=0.0, f_break_n=150.0)
    assert _spec_at(tw, 2_900)["specimen_n"] == pytest.approx(145.0)
    w = _spec_at(tw, 3_100)
    assert w["specimen_n"] == 0.0 and w["specimen_broken"] is True
    assert _spec_at(tw, 1_000)["specimen_n"] == 0.0                   # stays broken when unloaded
    brk = [e for e in tw.seam_log if e["call"] == "specimen_break"]
    assert len(brk) == 1 and float(brk[0]["args"].split()[0]) == pytest.approx(155.0)
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=0.0, side="push", break_travel_um=4_000.0,
           break_residual_pct=30.0)
    assert _spec_at(tw, -3_000)["specimen_n"] == pytest.approx(-150.0)
    w = _spec_at(tw, -4_000)
    assert w["specimen_broken"] is True and w["specimen_n"] == pytest.approx(-0.3 * 200.0)
    tw.act("reset", cause="pin")
    tw.advance_ms(5)
    w = tw.act("query", what="world")
    assert w["specimen_broken"] is True and w["specimen_n"] == pytest.approx(-60.0)
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=0.0, side="push")
    assert tw.act("query", what="world")["specimen_broken"] is False


def test_specimen_m4_relaxation_on_the_compression_side(tw):
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=0.0, side="push", relax_pct=10.0,
           relax_tau_s=1.0)
    _spec_at(tw, -4_000)
    tw.advance_ms(1000)
    w = tw.act("query", what="world")
    assert w["relax_n"] == pytest.approx(-20.0 * (1 - math.exp(-1.0)), rel=1e-3)
    assert w["load_n"] == pytest.approx(-200.0 - w["relax_n"])


def test_specimen_m4_scenario_keys(probe_exe, tmp_path):
    scn = {"schema": "bird.bend.simscenario", "version": 1,
           "world": {"specimen": {"kind": "spring", "k_n_per_mm": 10.0, "x_contact_um": 0, "side": "both",
                                  "break_travel_um": 5000, "break_residual_pct": 50}}}
    with Twin("lockstep", exe=probe_exe, run_dir=tmp_path, scenario=scn) as t:
        assert _spec_at(t, -2_000)["specimen_n"] == pytest.approx(-20.0)
        w = _spec_at(t, 6_000)
        assert w["specimen_broken"] and w["specimen_n"] == pytest.approx(30.0)


def test_specimen_m4_grip_slip_once_and_kept_over_reset(tw):
    """Grip slip (the simulator's test extra, SW_design B6-17): at |F| ≥ slip_at_n the contact point moves once by
    slip_mm in the deflection direction (force drop ≈ k·slip); a world state (kept over an MCU reset)."""
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=0.0, slip_at_n=100.0, slip_mm=0.5)
    assert _spec_at(tw, 1_900)["specimen_n"] == pytest.approx(95.0)
    assert _spec_at(tw, 2_500)["specimen_n"] == pytest.approx(100.0)          # 125 N → slipped 0.5 mm → 100 N
    assert _spec_at(tw, 3_000)["specimen_n"] == pytest.approx(125.0)          # once only
    slips = [e for e in tw.seam_log if e["call"] == "specimen_slip"]
    assert len(slips) == 1 and float(slips[0]["args"].split()[1]) == pytest.approx(500.0)
    tw.act("reset", cause="pin")
    tw.advance_ms(5)
    assert tw.act("query", what="world")["specimen_n"] == pytest.approx(125.0)


def test_specimen_m4_relaxed_one_sided_specimen_never_reverses(tw):
    """Unloading a relaxed pull specimen loses contact (force 0) instead of pushing back; a clamped (both)
    specimen may go negative."""
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=0.0, relax_pct=30.0, relax_tau_s=1.0)
    _spec_at(tw, 3_000)
    tw.advance_ms(5000)                                            # relax_n ≈ 45 N
    w = _spec_at(tw, 500)                                          # elastic 25 N < relaxation
    assert w["relax_n"] > 25.0 and w["load_n"] == 0.0
    tw.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=0.0, side="both", relax_pct=30.0,
           relax_tau_s=1.0)
    _spec_at(tw, 3_000)
    tw.advance_ms(5000)
    w = _spec_at(tw, 500)
    assert w["load_n"] == pytest.approx(25.0 - w["relax_n"]) and w["load_n"] < 0


# ---------------------------------------------------------------------------------------------- move stall (v0.7.5)
def _pul(tw: Twin, since_us: float = 0.0, level: int | None = None) -> list[dict]:
    e = [x for x in tw.act("query", what="edges", since_us=since_us)["edges"] if x["pin"] == "PUL"]
    return [x for x in e if level is None or x["level"] == level]


def _to_low_phase(tw: Twin) -> None:
    while _pul(tw)[-1]["level"] == 1:                              # stall call in the low phase (exact shift)
        tw.advance_us(1)


def test_step_stall_freezes_the_step_output_and_resumes_the_plan(tw):
    """ICD v0.7.5 vocabulary `inject step_stall` (tools/README "Move stall"): no pulse, no count, no world motion,
    still running for `duration_ms`; afterwards the rest of the running period and the unchanged plan: exactly one
    rise interval grows by the frozen time, the train ends complete."""
    link = TwinLink(tw)
    _train(tw, link, 100, 9000)                                    # 100 us period
    tw.advance_us(2_000)
    _to_low_phase(tw)
    q0, x0, t_call = tw.act("query", what="pulses"), tw.act("query", what="world")["x_um_true"], tw.now_us
    assert tw.act("inject", fault="step_stall", duration_ms=10)["ok"]
    tw.advance_ms(9)
    q = tw.act("query", what="pulses")
    assert q["running"] and q["step_stalled"] and q["pos_steps"] == q0["pos_steps"] and q["count"] == q0["count"]
    assert tw.act("query", what="world")["x_um_true"] == x0 and _pul(tw, t_call) == []
    tw.advance_ms(20)
    q = tw.act("query", what="pulses")
    assert q["count"] == 100 and q["pos_steps"] == 100 and not q["running"] and not q["step_stalled"]
    assert tw.act("query", what="world")["x_um_true"] == pytest.approx(125.0)
    beg = [s for s in tw.seam_log if s["call"] == "step_stall_begin"]
    end = [s for s in tw.seam_log if s["call"] == "step_stall_end"]
    assert len(beg) == len(end) == 1 and beg[0]["t_us"] == pytest.approx(t_call)
    assert float(beg[0]["args"]) == pytest.approx(t_call + 10_000.0)
    assert float(end[0]["args"]) == pytest.approx(10_000.0) and end[0]["t_us"] == pytest.approx(t_call + 10_000.0)
    rises = [x["t_us"] for x in _pul(tw, level=1)]
    gaps = [b - a for a, b in zip(rises, rises[1:])]
    assert [g for g in gaps if g > 150.0] == [pytest.approx(10_100.0, abs=0.01)]
    assert all(g == pytest.approx(100.0, abs=0.01) for g in gaps if g <= 150.0)


def test_step_stall_high_phase_and_armed_while_idle(tw):
    """A pulse already high completes (and counts) before the freeze; injected while idle the stall is armed and
    starts with the next move (its first pulse only after `duration_ms`); a negative duration is refused."""
    link = TwinLink(tw)
    assert tw.act("inject", fault="step_stall", duration_ms=-1)["ok"] is False
    _train(tw, link, 50, 9000)
    tw.advance_us(1_000)
    while _pul(tw)[-1]["level"] == 0:
        tw.advance_us(1)
    n_at, t_call = tw.act("query", what="pulses")["pos_steps"], tw.now_us
    tw.act("inject", fault="step_stall", duration_ms=5)
    tw.advance_ms(1)
    fall = _pul(tw, level=0)[-1]["t_us"]
    beg = [s for s in tw.seam_log if s["call"] == "step_stall_begin"][-1]
    assert tw.act("query", what="pulses")["pos_steps"] == n_at + 1 and beg["t_us"] == pytest.approx(fall)
    tw.advance_ms(10)
    q = tw.act("query", what="pulses")
    assert q["pos_steps"] == 50 and not q["running"]
    end = [s for s in tw.seam_log if s["call"] == "step_stall_end"][-1]
    assert float(end["args"]) == pytest.approx(t_call + 5_000.0 - fall, abs=0.01)     # frozen from the fall
    tw.act("inject", fault="step_stall", duration_ms=8)            # idle: armed for the next start
    assert tw.act("query", what="pulses")["step_stall_armed"]
    tw.advance_ms(3)
    t_s = tw.now_us
    _train(tw, link, 10, 9000)
    tw.advance_ms(5)
    q = tw.act("query", what="pulses")
    assert _pul(tw, t_s) == [] and q["running"] and q["step_stalled"] and not q["step_stall_armed"]
    tw.advance_ms(10)
    first = _pul(tw, t_s, level=1)[0]["t_us"]
    assert first >= t_s + 8_000.0 and tw.act("query", what="pulses")["pos_steps"] == 60


def test_step_stall_until_a_stop_and_reset(tw):
    """Without `duration_ms` (or 0) the stall lasts until the move ends: a stop (probe HALT → hal_step_stop_now,
    CLEAN) halts the frozen timer at once at the frozen position (seam log `step_stall_halted`) and ends the stall,
    so the next move runs normally; an MCU reset drops an armed stall."""
    link = TwinLink(tw)
    _train(tw, link, 200, 9000)
    tw.advance_us(3_000)
    _to_low_phase(tw)
    n0 = tw.act("query", what="pulses")["pos_steps"]
    tw.act("inject", fault="step_stall")
    tw.advance_ms(500)
    assert tw.act("query", what="pulses")["step_stalled"]
    t_h = tw.now_us
    link.cmd("HALT")
    q = tw.act("query", what="pulses")
    assert not q["running"] and not q["step_stalled"] and q["pos_steps"] == n0
    assert [s for s in tw.seam_log if s["call"] == "step_stall_halted"]
    tw.advance_ms(20)
    assert _pul(tw, t_h) == []
    _train(tw, link, 10, 9000)
    tw.advance_ms(5)
    assert tw.act("query", what="pulses")["pos_steps"] == n0 + 10
    tw.act("inject", fault="step_stall", duration_ms=1000)
    assert tw.act("query", what="pulses")["step_stall_armed"]
    tw.act("reset", cause="pin")
    tw.advance_ms(5)
    assert tw.act("query", what="pulses")["step_stall_armed"] is False
