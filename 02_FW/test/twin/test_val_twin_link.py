"""Validator E - twin: link, dispatcher, counters, stream timing and bandwidth.

Verifies: FW-CMD-001, FW-CMD-002, FW-CMD-004, FW-STR-002, FW-STR-003, FW-STR-004, IF-004, IF-007,
          IF-011, IF-012, NFR-008
"""
from __future__ import annotations

import random

import pytest
import ref_codec as rc
from twin import Twin
from vhelp import PBYKEY, V, idle_status_bits

SAFE_CMDS = ["PING", "GET_INFO", "GET_STATUS", "GET_PARAM", "SET_PARAM", "STREAM_START", "STREAM_STOP",
             "SET_VALID", "HALT", "HALT_CLEAR", "PAUSE", "RESUME", "STOP", "ESTOP_CLEAR", "FAULT_CLEAR",
             "ENABLE", "DISABLE", "HOME", "MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD", "GET_ALL_PARAMS",
             "DEFAULT_PARAMS", "LOAD_PARAMS"]


def _fields(name: str, rnd: random.Random) -> dict:
    p = PBYKEY["stream.fallback_hz"]
    return {
        "GET_PARAM": {"id": rnd.choice([p.id, 0x0999])},
        "SET_PARAM": {"id": p.id, "type": "u8", "value": rnd.choice([1, 10, 80, 81, 0])},
        "SET_VALID": {"valid": rnd.choice([0, 1, 2])},
        "STOP": {"mode": rnd.choice([0, 1, 2])},
        "HOME": {"flags": rnd.choice([0, 1, 4])},
        "MOVE_ABS": {"target_um": 1000, "v_um_s": 1000, "a_um_s2": 0},
        "JOG": {"v_um_s": rnd.choice([0, 500]), "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND},
        "MOVE_UNTIL_LOAD": {"bound_um": 1000, "v_um_s": 100, "a_um_s2": 0, "raw_stop": 10, "cmp": 0},
        "GET_ALL_PARAMS": {"page": rnd.choice([0, 1, 2, 3])},
    }.get(name, {})


def _counters(st: dict) -> tuple[int, int, int, int]:
    return st["rx_frames_ok"], st["rx_crc_errors"], st["rx_frame_errors"], st["rx_overruns"]


def test_fuzz_one_response_per_valid_command(v: V, tw):
    """TC-FW-CMD-001-02: 10 000 frames (valid commands, unknown/invalid TYPE, wrong LEN, bad CRC, noise)."""
    # Verifies: FW-CMD-001, IF-003, IF-004, FW-CMD-004
    # TC: TC-FW-CMD-001-02, TC-IF-004-01 (T part)
    rnd = random.Random(4242)
    st0 = v.status()
    c0 = _counters(st0)
    ref = rc.FrameParser()
    expect: list[tuple[int, int]] = []        # (response TYPE, SEQ) in order
    invalid_type = 0
    sent = 0
    seq = 0
    buf = bytearray()
    while sent < 10_000:
        r = rnd.random()
        seq = (seq + 1) & 0xFF
        if r < 0.55:
            name = rnd.choice(SAFE_CMDS)
            fr = rc.make_frame(name, seq, _fields(name, rnd))
        elif r < 0.62:                                            # undefined TYPE in the command range
            fr = rc.encode_frame(rnd.choice([0x0A, 0x0F, 0x1F, 0x2A, 0x3D, 0x3E, 0x3F]), seq, b"")
        elif r < 0.70:                                            # TYPE invalid for the FW side
            fr = rc.encode_frame(rnd.choice([0x00, 0x40, 0x81, 0xC0, 0xC1, 0xFF]), seq, rnd.randbytes(rnd.randint(0, 6)))
        elif r < 0.78:                                            # wrong LEN for a defined command
            fr = rc.encode_frame(rc.CMD[rnd.choice(["PING", "STOP", "MOVE_ABS", "SET_PARAM"])], seq,
                                 rnd.randbytes(rnd.choice([1, 2, 5, 13])))
        elif r < 0.90:                                            # bad CRC
            b = bytearray(rc.make_frame("HALT" if rnd.random() < 0.5 else "PING", seq, {}))
            b[-1] ^= 0x5A
            fr = bytes(b)
        else:                                                     # noise (no sync)
            fr = bytes(rnd.choice([0x00, 0x11, 0x5A, 0xFF]) for _ in range(rnd.randint(1, 5)))
        sent += 1
        for f in ref.feed(fr):
            if 0x01 <= f.type <= 0x3F:
                expect.append((f.type | 0x80, f.seq))
            else:
                invalid_type += 1
        buf += fr
        if len(buf) > 600:                                        # pace: the FW keeps up, no RX overrun
            tw.feed_rx(bytes(buf))
            buf.clear()
            v.advance(80)
    tw.feed_rx(bytes(buf))
    v.advance(300)
    ref.idle_timeout()                                            # the FW sees >= 20 ms idle at the end
    # fuzz may have left HALT/PAUSE/stream etc. latched: read counters
    got = [(f.type, f.seq) for f in v.responses() if f.type != rc.CMD["GET_STATUS"] | 0x80 or True]
    got = [g for g in got[1:]]                                    # drop the initial GET_STATUS response
    assert got == expect, f"responses {len(got)} vs CRC-valid command frames {len(expect)}"
    st1 = v.status()
    c1 = _counters(st1)
    assert c1[0] - c0[0] == ref.frames_ok + 1                     # +1: the GET_STATUS just sent
    assert c1[1] - c0[1] == ref.crc_errors
    assert c1[2] - c0[2] == ref.len_errors + ref.timeout_drops + invalid_type
    assert c1[3] == c0[3] == 0
    assert v.tw.act("query", what="pulses")["count"] == 0


def test_corrupted_frames_no_action(v: V):
    """TC-IF-004-01 (T): corrupted HALT / SET_PARAM / PAUSE frames act on nothing and are counted."""
    # Verifies: IF-004
    # TC: TC-IF-004-01
    c0 = v.status()["rx_crc_errors"]
    p = PBYKEY["stream.fallback_hz"]
    for name, f in (("HALT", {}), ("PAUSE", {}), ("SET_PARAM", {"id": p.id, "type": "u8", "value": 33}),
                    ("STREAM_START", {})):
        b = bytearray(rc.make_frame(name, 5, f))
        b[3] ^= 0x10                                               # SEQ bit: inside the CRC coverage
        v.tw.feed_rx(bytes(b))
        v.advance(30)
    st = v.status()
    assert st["rx_crc_errors"] - c0 == 4
    assert "HALT" not in st["flags"] and "PAUSED" not in st["status"] and "STREAM_ON" not in st["sys_flags"]
    assert v.get("stream.fallback_hz") == 10
    assert not [e for e in v.events() if e["code"] in ("HALT_SET", "PAUSED")]


def test_every_defined_command_known(v: V):
    """TC-IF-012-01: each of the 27 commands (ICD v0.6: + DIAG_MEAS 0x3D) -> no E_UNKNOWN_CMD; motion -> E_STATE /
    E_INTERNAL 1; DIAG_MEAS in a build without FEAT_HW_MEAS -> E_INTERNAL NOT_IN_BUILD (OI-FW-36)."""
    # Verifies: IF-012, FW-CMD-001
    # TC: TC-IF-012-01
    rnd = random.Random(1)
    assert len(rc.CMD) == 27 and rc.CMD.get("DIAG_MEAS") == 0x3D
    for name in rc.CMD:
        if name == "REBOOT":
            continue
        f = _fields(name, rnd)
        if name == "GET_ALL_PARAMS":
            f = {"page": 0}
        if name == "SET_VALID":
            f = {"valid": 1}
        if name == "STOP":
            f = {"mode": 0}
        if name == "GET_PARAM":
            f = {"id": PBYKEY["stream.fallback_hz"].id}
        if name == "DIAG_MEAS":
            f = {"op": 0, "sel": 0, "a": 0, "b": 0}
        r = v.cmd(name, f)
        assert r["status"] != "E_UNKNOWN_CMD", name
        if name == "DIAG_MEAS":                                 # twin without --hw-meas = no FEAT_HW_MEAS
            assert "HW_MEAS" not in v.info()["features"]
            assert (r["status"], r["detail"]) == ("E_INTERNAL", 1), r
        if name in ("ENABLE", "MOVE_ABS", "JOG", "HOME", "MOVE_UNTIL_LOAD", "DISABLE") and r["status"] != "OK":
            assert r["status"] in ("E_STATE", "E_INTERNAL", "E_RANGE"), (name, r)
    assert v.reboot()["status"] == "OK"
    v.advance(60)
    assert v.tw.resets and v.tw.resets[-1]["cause"] == "software"
    assert v.status()["reset_cause"] == "SOFTWARE"


def test_status_counters_each_condition(v: V, tw):
    """TC-FW-CMD-004-01: every counter increments under its injected condition."""
    # Verifies: FW-CMD-004
    # TC: TC-FW-CMD-004-01
    s0 = v.status()
    # CRC error
    b = bytearray(rc.make_frame("PING", 1, {}))
    b[-2] ^= 1
    tw.feed_rx(bytes(b))
    v.advance(25)
    # LEN > 160
    tw.feed_rx(bytes([0xA5, 0x5A, 0x01, 0x02, 0xA1, 0x00]))
    v.advance(25)
    # inter-byte timeout (truncated frame, then >= 20 ms silence)
    tw.feed_rx(rc.make_frame("GET_STATUS", 3, {})[:5])
    v.advance(30)
    # invalid TYPE (FW->PC type sent to the FW)
    tw.feed_rx(rc.encode_frame(0xC0, 4, b""))
    v.advance(25)
    s1 = v.status()
    assert s1["rx_crc_errors"] - s0["rx_crc_errors"] == 1
    # LEN error + timeout drop + invalid TYPE; the truncated header also leaves bytes the parser drops
    assert s1["rx_frame_errors"] - s0["rx_frame_errors"] == 3
    assert s1["rx_frames_ok"] - s0["rx_frames_ok"] == 2           # invalid TYPE frame + this GET_STATUS
    assert s1["link_age_ms"] <= 1
    # RX ring overflow: main loop held 40 ms while 4 KB arrive
    tw.act("inject", fault="hang", where="main", duration_ms=40)
    tw.feed_rx(b"".join(rc.make_frame("PING", k & 0xFF, {}) for k in range(500)))
    v.advance(200)
    s2 = v.status()
    assert s2["rx_overruns"] > s1["rx_overruns"]
    # TX congestion -> tx_drops
    v.ok("STREAM_START")
    v.advance(50)
    tw.act("inject", fault="tx_congestion", duration_ms=100)
    v.advance(200)
    s3 = v.status()
    assert s3["tx_drops"] >= 7                                    # 100 ms at 80 Hz
    # event overflow: responses (152 B pages) hog the line, EVENT class + queue fill up
    v.ok("STREAM_STOP")
    burst = b""
    for k in range(80):
        burst += b"".join(rc.make_frame("GET_ALL_PARAMS", k & 0xFF, {"page": 0}) for _ in range(4))
        burst += rc.make_frame("HALT", 1, {}) + rc.make_frame("HALT_CLEAR", 2, {})
    tw.feed_rx(burst)
    v.advance(3000)
    s4 = v.status()
    assert s4["event_overflows"] > 0
    assert s4["rx_overruns"] > s2["rx_overruns"]                  # back-pressure into the RX ring (DEF-P1-07)
    v.ok("PAUSE")                                                  # one more event after the drops
    v.advance(50)
    seqs = [s for s, _ in v.event_frames()]
    gaps = [(a, b) for a, b in zip(seqs, seqs[1:]) if (b - a) & 0xFF != 1]
    assert gaps, "EVENT SEQ gap expected after event drops"
    # latches and sources
    v.ok("HALT")
    v.ok("PAUSE")
    s5 = v.status()
    assert "HALT" in s5["flags"] and s5["halt_src"] == "PC" and s5["pause_src"] == "PC" and "PAUSED" in s5["status"]
    assert s5["v_limit_um_s"] > 0 and s5["afe_rate_dsps"] in range(790, 811)
    # reset causes
    for cause, name in (("pin", "PIN"), ("iwdg", "IWDG"), ("power", "POWER_ON"), ("software", "SOFTWARE")):
        tw.act("reset", cause=cause)
        v.advance(5)
        assert v.status()["reset_cause"] == name


def test_set_valid_boundary_across_wrap(twin_exe, tmp_path):
    """TC-FW-CMD-002-01: frames with t_us >= response t carry the new VALID (modular across 2^32)."""
    # Verifies: FW-CMD-002, SAF-FW-001 (boot VALID 0)
    # TC: TC-FW-CMD-002-01
    with Twin("lockstep", exe=twin_exe, run_dir=tmp_path / "r", t0_us=0xFFFFFFFF - 1_500_000) as tw:
        v = V(tw)
        r = v.ok("SET_VALID", {"valid": 1})                       # stream off: stored
        t1 = r["t_us"]
        v.ok("STREAM_START")
        v.advance(1000)
        assert all("VALID" in d["flags"] for d in v.data())
        v.advance(300)
        r0 = v.ok("SET_VALID", {"valid": 0})
        t0 = r0["t_us"]
        v.advance(400)
        r1 = v.ok("SET_VALID", {"valid": 1})
        t2 = r1["t_us"]
        v.advance(700)
        data = v.data()
        wrapped = any(a["t_us"] > b["t_us"] for a, b in zip(data, data[1:]))
        assert wrapped, "test must cross the 2^32 wrap"

        def after(t, ref):
            return ((t - ref) & 0xFFFFFFFF) < 0x80000000

        for d in data:
            if after(d["t_us"], t2):
                want = True
            elif after(d["t_us"], t0):
                want = False
            else:
                want = True
            assert ("VALID" in d["flags"]) == want, (d, t0, t2)
        assert t1 is not None
    # boot: VALID 0 in the first frames
    with Twin("lockstep", exe=twin_exe, run_dir=tmp_path / "r2") as tw:
        v = V(tw)
        v.ok("STREAM_START")
        v.advance(200)
        assert all("VALID" not in d["flags"] for d in v.data())


def test_stream_10min_latency_counts_bandwidth(v: V, tw):
    """TC-FW-STR-002-01, TC-IF-007-01 (M1 part), TC-IF-011-01, TC-NFR-008-01: 10 min virtual at 80 Hz with
    GET_ALL_PARAMS polling and 20 commands/s; two DOUT stalls."""
    # Verifies: FW-STR-002, IF-007, IF-011, NFR-008, FW-STR-005 (fallback during stall, M2 Should)
    # TC: TC-FW-STR-002-01, TC-IF-007-01, TC-IF-011-01, TC-NFR-008-01
    rnd = random.Random(7)
    v.ok("STREAM_START")
    t_start = tw.now_us
    names = ["PING", "GET_STATUS", "GET_INFO", "GET_PARAM", "SET_VALID", "SET_PARAM", "GET_ALL_PARAMS",
             "STOP", "HALT", "HALT_CLEAR", "PAUSE", "RESUME", "FAULT_CLEAR", "ESTOP_CLEAR", "JOG"]
    stalls = [(120_000, 2_000), (400_000, 5_000)]                  # (start ms, duration ms)
    for ms in range(0, 600_000, 50):                               # 20 commands/s
        for st_ms, dur in stalls:
            if ms == st_ms:
                tw.act("afe", stall=True)
            if ms == st_ms + dur:
                tw.act("afe", stall=False)
        name = rnd.choice(names)
        f = _fields(name, rnd)
        if name == "SET_PARAM":
            f["value"] = 10
        if name == "SET_VALID":
            f = {"valid": rnd.randint(0, 1)}
        if name == "STOP":
            f = {"mode": rnd.randint(0, 1)}
        if name == "JOG":
            f["v_um_s"] = 0
        ph = rnd.uniform(0.0, 12.5)                                # random phase vs. the 80 Hz data-ready
        v.advance(ph)
        v.link.send(name, f)
        v.advance(50 - ph)
        if len(v.link.frames) > 5000:
            v.link.frames = v.link.frames[-200:]
    v.advance(100)
    dur_s = (tw.now_us - t_start) / 1e6
    # IF-007: DATA frames (with AFE data) = delivered conversions
    conv = [c for c in tw.act("query", what="conversions")["conversions"] if c["t_us"] >= t_start - 1]
    sent = [s for s in tw.act("query", what="sent")["sent"] if s["type"] == rc.ASYNC["DATA"]]
    real = [s for s in sent if int.from_bytes(bytes.fromhex(s["hex"])[12:16], "little") != 0x80000000]
    fb = [s for s in sent if int.from_bytes(bytes.fromhex(s["hex"])[12:16], "little") == 0x80000000]
    delivered = [c for c in conv if c["delivered"]]
    assert len(real) == len(delivered), (len(real), len(delivered))
    assert not [s for s in sent if s["dropped"]]
    # fallback frames only inside the stalls (+ afe.timeout_ms), each marked NO_AFE_DATA + AFE_STALE
    for s in fb:
        d = rc.decode_data(bytes.fromhex(s["hex"])[6:-2])
        assert {"NO_AFE_DATA", "AFE_STALE"} <= set(d["status"])
        t_ms = (s["t_us"] - t_start) / 1000
        assert any(st <= t_ms <= st + dur + 50 for st, dur in stalls), t_ms
    assert 10 <= len(fb) <= 80                                     # ~10 Hz during 7 s minus 2 x 250 ms
    # FW-STR-002 latency: data-ready -> first byte of its DATA frame on the wire
    wl = v.wire("tx")
    dtx = {}
    for w in wl:
        if w["type"] == rc.ASYNC["DATA"]:
            fr = bytes.fromhex(w["hex"])
            dtx[int.from_bytes(fr[6:10], "little")] = w
    lat_idle, lat_busy = [], []
    other = sorted((w["first_us"], w["last_us"]) for w in wl if w["type"] != rc.ASYNC["DATA"])
    import bisect
    starts = [o[0] for o in other]
    for c in delivered:
        w = dtx.get(c["fw_t_us"])
        assert w is not None, c
        lat = w["first_us"] - c["t_us"]
        k = bisect.bisect_right(starts, c["t_us"]) - 1
        busy_rem = max(0.0, other[k][1] - c["t_us"]) if k >= 0 else 0.0
        if busy_rem > 0:
            lat_busy.append(lat - busy_rem)
        else:
            lat_idle.append(lat)
    print(f"VALSTR002 conversions={len(delivered)} max_idle_us={max(lat_idle):.1f} "
          f"max_busy_excess_us={max(lat_busy) if lat_busy else 0:.1f} n_busy={len(lat_busy)}")
    assert max(lat_idle) <= 2000
    assert not lat_busy or max(lat_busy) <= 2000
    # IF-011 bandwidth: FW->PC bytes <= 10 % of 92 160 B/s
    tx_bytes = sum(len(w["hex"]) // 2 for w in wl if w["first_us"] >= t_start)
    rate = tx_bytes / dur_s
    print(f"VALIF011 fw_to_pc={rate:.0f} B/s ({100 * rate / 92160:.2f} %) incl. 20 cmd/s responses")
    data_rate = sum(len(w["hex"]) // 2 for w in wl if w["first_us"] >= t_start and w["type"] in (0xC0, 0xC1)) / dur_s
    assert data_rate <= 0.10 * 92160                               # stream + events at 80 Hz
    # NFR-008: last request byte -> first response byte <= 10 ms
    rx = [w for w in v.wire("rx") if w["first_us"] >= t_start]
    resp = {}
    for w in wl:
        if 0x81 <= w["type"] <= 0xBF:
            resp.setdefault((w["type"] & 0x7F, w["seq"]), []).append(w)
    worst = 0.0
    for r in rx:
        cand = [w for w in resp.get((r["type"], r["seq"]), []) if w["first_us"] >= r["last_us"]]
        assert cand, r
        worst = max(worst, cand[0]["first_us"] - r["last_us"])
    print(f"VALNFR008 commands={len(rx)} worst_response_us={worst:.1f}")
    assert worst <= 10_000


@pytest.mark.parametrize("k", [1, 5, 50])
def test_tx_congestion_frame_seq_gap_and_overrun(v: V, tw, k):
    """TC-FW-STR-004-01: k dropped frames -> frame_seq gap k, OVERRUN only on the next sent frame, tx_drops + k."""
    # Verifies: FW-STR-004
    # TC: TC-FW-STR-004-01
    v.ok("STREAM_START")
    v.advance(100)
    d0 = v.status()["tx_drops"]
    data = v.data()
    t_next = data[-1]["t_us"] + 12_500
    # congestion window covering exactly k due frames (80 Hz model, frames due at t_next + i*12.5 ms)
    tw.advance_to(int((t_next - 1_000) * 1000))
    tw.act("inject", fault="tx_congestion", duration_ms=12.5 * (k - 1) + 2)
    sseq = v.link.send("STOP", {"mode": 0})                        # IF-011: stop response never dropped
    v.advance(12.5 * k + 200)
    assert v.link.find("STOP", sseq) is not None and v.link.find("STOP", sseq)["status"] == "OK"
    data = v.data()
    gaps = [(a, b) for a, b in zip(data, data[1:]) if (b["frame_seq"] - a["frame_seq"]) & 0xFFFF != 1]
    assert len(gaps) == 1
    a, b = gaps[0]
    assert (b["frame_seq"] - a["frame_seq"] - 1) & 0xFFFF == k
    assert "OVERRUN" in b["flags"]
    assert [d for d in data if "OVERRUN" in d["flags"]] == [b]
    assert v.status()["tx_drops"] - d0 == k


def test_data_bits_provoked_in_m1(v: V, tw):
    """TC-FW-STR-003-01 (T, M1-reachable bits): VALID, HALT, PAUSED, AFE_STALE + NO_AFE_DATA, AFE_SATURATED,
    AFE_SETTLING, AFE_RATE_MISMATCH, OVERRUN, DRV_PWR (sense disabled + reboot). Motion/input bits -> M2."""
    # Verifies: FW-STR-003
    # TC: TC-FW-STR-003-01
    v.ok("STREAM_START")
    v.advance(200)

    def last():
        v.advance(30)
        return v.data()[-1]

    base = idle_status_bits(v)                                    # v0.3: {PEND, DRV_PWR} with DRV_SIGNALS
    d0 = last()
    assert d0["flags"] == [] and set(d0["status"]) == base, d0
    v.ok("SET_VALID", {"valid": 1})
    assert "VALID" in last()["flags"]
    v.ok("HALT")
    d = last()
    assert "HALT" in d["flags"] and "VALID" not in d["flags"]     # HALT clears VALID (SAF-FW-001)
    v.ok("HALT_CLEAR")
    assert "HALT" not in last()["flags"]
    v.ok("PAUSE")
    assert "PAUSED" in last()["status"]
    v.ok("RESUME")
    assert "PAUSED" not in last()["status"]
    tw.act("afe", saturate="pos")
    d = last()
    assert "AFE_SATURATED" in d["status"] and d["afe_raw"] == 0x7FFFFF
    tw.act("afe", saturate=None)
    assert "AFE_SATURATED" not in last()["status"]
    assert v.set("afe.gain_channel", "A64")["status"] == "OK"       # re-config -> settle discard (4)
    v.advance(13)
    n = len(v.data())
    v.advance(100)
    st = [("AFE_SETTLING" in x["status"]) for x in v.data()[n - 1:]]
    assert st[:4].count(True) >= 3 and not any(st[6:])
    tw.act("afe", stall=True)
    v.advance(400)
    d = v.data()[-1]
    assert {"AFE_STALE", "NO_AFE_DATA"} <= set(d["status"])
    tw.act("afe", stall=False)
    v.advance(100)
    assert "AFE_STALE" not in v.data()[-1]["status"]
    tw.act("afe", rate_error=0.30)                                   # 80 SPS -> 61.5 SPS: mismatch > 20 %
    v.advance(1500)
    assert "AFE_RATE_MISMATCH" in v.data()[-1]["status"]
    assert [e for e in v.events() if e["code"] == "AFE_RATE_MISMATCH" and e["arg"] == 1]
    tw.act("afe", rate_error=0.0)
    v.advance(1500)
    assert "AFE_RATE_MISMATCH" not in v.data()[-1]["status"]
    if "DRV_SIGNALS" in v.info()["features"]:                      # M2 build: valid input, make it "off"
        tw.act("drv_power", on=False)
        v.advance(40)
        assert "DRV_PWR" in last()["status"]                       # ICD v0.7 default: sense off -> reads 1
        assert v.set("drv.pwr_sense_enable", 1)["status"] == "OK"
        v.ok("SAVE_PARAMS")
        v.reboot()
        v.advance(60)
        v.ok("STREAM_START")
        v.advance(100)
        assert "DRV_PWR" not in v.data()[-1]["status"]             # sense on, power off -> 0


def test_overrun_only_for_fw_losses(v: V, tw):
    """TC-FW-STR-003-01 / TC-FW-STR-004-01 (OVERRUN semantics, ICD §7.6 bit 7, §2.2; SRS NFR-004 attribution)."""
    # Verifies: FW-STR-003, FW-STR-004
    # TC: TC-FW-STR-003-01, TC-FW-STR-004-01
    v.ok("STREAM_START")
    v.advance(200)
    tw.act("afe", stall=True)                                      # sensor stops converting (no FW loss)
    v.advance(500)
    tw.act("afe", stall=False)
    v.advance(200)
    real = [d for d in v.data() if d["afe_raw"] != rc.AFE_NO_DATA]
    stall_flag = [d for d in real if "OVERRUN" in d["flags"]]
    v.ok("STREAM_STOP")
    tw.act("afe", stall=True)
    v.advance(50)
    tw.act("afe", stall=False)
    v.advance(100)
    n = len(v.data())
    v.ok("STREAM_START")
    v.advance(50)
    off_flag = [d for d in v.data()[n:] if "OVERRUN" in d["flags"]]
    assert not stall_flag and not off_flag, (len(stall_flag), len(off_flag))


def test_overrun_after_class_d_drop_survives_stream_stop_start(v: V, tw):
    """Re-test of the DEF-M1-02 fix (regression): a class-D drop of the last due frame before STREAM_STOP
    must still be flagged on the next SENT frame (ICD §2.2 / §2.4: "the next sent frame has OVERRUN")."""
    # Verifies: FW-STR-004
    # TC: TC-FW-STR-004-01
    v.ok("STREAM_START")
    v.advance(100)
    t_next = v.data()[-1]["t_us"] + 12_500
    tw.advance_to(int((t_next - 1_000) * 1000))
    tw.act("inject", fault="tx_congestion", duration_ms=2)          # drops exactly the next due frame
    v.advance(3)
    v.ok("STREAM_STOP")
    v.advance(100)
    n = len(v.data())
    v.ok("STREAM_START")
    v.advance(30)
    first = v.data()[n]
    gap = (first["frame_seq"] - v.data()[n - 1]["frame_seq"] - 1) & 0xFFFF
    assert gap == 1
    assert "OVERRUN" in first["flags"], "frame_seq gap without OVERRUN would be attributed to the link"


def test_no_overrun_when_stream_started_right_after_save(v: V, tw):
    """Re-test of the DEF-M1-02 fix (regression): stream OFF during a SAVE with a sector erase, STREAM_START
    right after the SAVE response -> no frame was due during the hold, so no OVERRUN."""
    # Verifies: FW-STR-003, FW-NVM-003
    # TC: TC-FW-STR-003-01, TC-FW-NVM-003-01
    for k in range(64):
        assert v.set("stream.fallback_hz", 2 + k % 60)["status"] == "OK"
        v.ok("SAVE_PARAMS")
    v.ok("SAVE_PARAMS")                                             # this one erases (500 ms AFE hold)
    v.ok("STREAM_START")
    v.advance(200)
    d = v.data()
    assert d and not [x for x in d if "OVERRUN" in x["flags"]]
