"""M1 link behaviour of A's firmware in the lock-step twin (virtual time, deterministic).

PC side = the twin's TwinLink (ref_codec oracle, tests only) for exact byte/time control; loss detection uses
B's production ``calc.timebase.frame_gaps``. Evidence for FW_test_plan T-level M1 cases (shared with
Validator E) and SW_test_plan level X.

Verifies: IF-005, IF-007, IF-011, NFR-008, FW-STR-002, FW-STR-004, FW-CMD-001, FW-CMD-002, FW-CMD-003,
          FW-NVM-001, FW-NVM-002, FW-NVM-003, SAF-FW-002 (sniffer path, M1 part), SAF-FW-023 (no-motion part; SAF-FW-022 withdrawn by D-36),
          D-30, D-31, D-34
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import ref_cmdcheck as cc
import ref_codec as rc
import gen_params
from twin import Twin, TwinLink

from bend_stand.calc.timebase import frame_gaps

pytestmark = pytest.mark.twin

VEC = Path(gen_params.__file__).resolve().parent / "vectors"
CHECK = json.loads((VEC / "check_vectors.json").read_text(encoding="utf-8"))
MODEL = cc.Model(gen_params.load().params)
STOP_FAMILY = ("STOP", "HALT", "PAUSE", "RESUME", "HALT_CLEAR", "ESTOP_CLEAR", "FAULT_CLEAR")
PID = {p.key: p.id for p in gen_params.load().params}
PTYPE = {p.key: p.type for p in gen_params.load().params}


def setp(link: TwinLink, key: str, value) -> dict:
    return link.cmd("SET_PARAM", {"id": PID[key], "type": PTYPE[key], "value": value})


def wire_pair(tw: Twin, cmd: str, seq: int) -> tuple[dict, dict]:
    """(request rx entry, response tx entry) of the wire log."""
    rx = [w for w in tw.wire_log if w["dir"] == "rx" and w["type"] == rc.CMD[cmd] and w["seq"] == seq][-1]
    tx = [w for w in tw.wire_log if w["dir"] == "tx" and w["type"] == rc.CMD[cmd] | 0x80 and w["seq"] == seq
          and w["first_us"] >= rx["last_us"]][0]
    return rx, tx


def status(link: TwinLink) -> dict:
    r = link.cmd("GET_STATUS")
    assert r["status"] == "OK", r
    return r["board_status"]


# ============================================================================================ connect / timing
@pytest.mark.req("IF-008", "NFR-008", "SW-PLT-003")
def test_connect_sequence_order_and_response_time(twin):
    link = TwinLink(twin)
    seqs = []
    for name, f in (("GET_INFO", {}), ("GET_STATUS", {}), ("GET_ALL_PARAMS", {"page": 0}),
                    ("GET_ALL_PARAMS", {"page": 1}), ("GET_ALL_PARAMS", {"page": 2})):
        r = link.cmd(name, f)
        assert r["status"] == "OK", (name, r)
        seqs.append((name, (link.seq - 1) & 0xFF))
    for key, v in (("safety.zero_raw", 50000), ("safety.load_raw_min", -6000000), ("safety.load_raw_max", 6000000)):
        r = setp(link, key, v)
        assert r["status"] == "OK" and r["entry"]["value"] == v, (key, r)
        seqs.append(("SET_PARAM", (link.seq - 1) & 0xFF))
    assert link.cmd("STREAM_START")["status"] == "OK"
    seqs.append(("STREAM_START", (link.seq - 1) & 0xFF))
    worst = 0.0
    for name, s in seqs:
        rx, tx = wire_pair(twin, name, s)
        worst = max(worst, tx["first_us"] - rx["last_us"])
    assert worst <= 10_000, f"last request byte -> first response byte {worst} us > 10 ms (NFR-008)"
    st = status(link)
    assert "CFG_DIRTY" in st["sys_flags"]               # session values never dirty, but blank flash is
    assert st["motion_state"] == "NOT_ENABLED" and st["reset_cause"] == "POWER_ON"


# ============================================================================================ stream
@pytest.mark.req("FW-STR-002", "IF-007", "IF-011")
def test_long_run_one_data_frame_per_sample_zero_loss(twin):
    """10 min virtual at 80 SPS with GET_ALL_PARAMS polling (152 B frames) at 5 Hz: DATA = conversions, no gap."""
    link = TwinLink(twin)
    assert link.cmd("STREAM_START")["status"] == "OK"
    t_start = twin.now_us
    minutes = 10
    for i in range(minutes * 60 * 5):
        link.send("GET_ALL_PARAMS", {"page": i % 3})
        twin.advance_ms(200)
        link.poll()
    t_end = twin.now_us
    data = link.data()
    conv = [c for c in twin.act("query", what="conversions", since_us=t_start)["conversions"]
            if c["delivered"] and c["t_us"] <= t_end - 2_000]
    steps = frame_gaps([d["frame_seq"] for d in data])
    lost = sum(s.lost for s in steps)
    assert lost == 0 and all(s.kind in ("first", "next") for s in steps), "frame_seq gaps in a clean run"
    by_t = {d["t_us"]: d for d in data}
    missing = [c for c in conv if c["fw_t_us"] not in by_t]
    assert not missing, f"{len(missing)} conversions without a DATA frame"
    assert len(conv) == pytest.approx(minutes * 60 * 80, abs=2)
    assert not any("OVERRUN" in d["flags"] for d in data)
    # DRDY -> first DATA byte (FW-STR-002): idle line <= 2 ms, otherwise + one frame in transmission
    tx = [w for w in twin.wire_log if w["dir"] == "tx" and w["type"] == rc.ASYNC["DATA"]]
    conv_t = {c["fw_t_us"]: c["t_us"] for c in conv}
    lat = [w["first_us"] - conv_t[int.from_bytes(bytes.fromhex(w["hex"])[6:10], "little")]
           for w in tx if int.from_bytes(bytes.fromhex(w["hex"])[6:10], "little") in conv_t]
    assert lat and max(lat) <= 2_000 + 168 * 1e6 / 92160, f"max DRDY->DATA {max(lat):.1f} us"
    tx_bytes = sum(len(w["hex"]) // 2 for w in twin.wire_log if w["dir"] == "tx" and w["first_us"] >= t_start)
    assert tx_bytes / ((t_end - t_start) / 1e6) <= 0.10 * 92160      # IF-011 FW->PC <= 10 % of the link


@pytest.mark.req("FW-STR-004", "IF-007")
@pytest.mark.parametrize("k", [1, 5, 50])
def test_frame_gap_detection_under_tx_congestion(twin, k):
    link = TwinLink(twin)
    assert link.cmd("STREAM_START")["status"] == "OK"
    twin.advance_ms(100)
    st0 = status(link)
    t0 = twin.now_us
    period_ms = 1000 / 80
    twin.act("inject", fault="tx_congestion", duration_ms=k * period_ms - 1)
    twin.advance_ms(k * period_ms + 100)
    link.poll()
    dropped = [s for s in twin.act("query", what="sent", since_us=t0)["sent"] if s["dropped"]]
    assert len(dropped) == k and all(s["type"] == rc.ASYNC["DATA"] for s in dropped)
    data = link.data()
    steps = frame_gaps([d["frame_seq"] for d in data])
    gaps = [(i, s) for i, s in enumerate(steps) if s.kind == "lost"]
    assert [s.lost for _, s in gaps] == [k], gaps                      # B's detector sees exactly k lost
    i = gaps[0][0]
    assert "OVERRUN" in data[i]["flags"]                               # FW attribution on the next sent frame
    assert not any("OVERRUN" in d["flags"] for d in data[i + 1:])
    st1 = status(link)
    assert st1["tx_drops"] - st0["tx_drops"] == k


@pytest.mark.req("FW-CMD-002")
def test_set_valid_boundary_across_wrap(twin_exe, tmp_path):
    with Twin("lockstep", exe=twin_exe, run_dir=tmp_path, t0_us=2**32 - 400_000) as tw:
        link = TwinLink(tw)
        assert link.cmd("STREAM_START")["status"] == "OK"
        tw.advance_ms(380)
        r = link.cmd("SET_VALID", {"valid": 1})
        t_apply = r["t_us"]
        tw.advance_ms(100)
        link.poll()
        data = link.data()
        assert any(d["t_us"] < data[0]["t_us"] for d in data)          # the run crosses the 2^32 wrap
        for d in data:
            newer = ((d["t_us"] - t_apply) & 0xFFFFFFFF) < 2**31
            assert ("VALID" in d["flags"]) == newer, d


# ============================================================================================ NVM
def _all_params(link: TwinLink) -> dict[int, object]:
    out = {}
    for p in range(3):
        r = link.cmd("GET_ALL_PARAMS", {"page": p})
        out.update({e["id"]: e["value"] for e in r["entries"]})
    return out


def _set_pair(link: TwinLink, v: int) -> None:
    assert setp(link, "stream.fallback_hz", v)["status"] == "OK"
    assert setp(link, "io.release_ms", 100 + v)["status"] == "OK"


def _pair(link: TwinLink) -> tuple[int, int]:
    p = _all_params(link)
    return p[PID["stream.fallback_hz"]], p[PID["io.release_ms"]]


@pytest.mark.req("FW-NVM-001", "FW-NVM-002", "FW-NVM-003")
def test_nvm_save_reboot_restore_and_power_cut_during_save(twin):
    link = TwinLink(twin)
    _set_pair(link, 11)
    w0 = twin.act("query", what="flash")["writes"]
    assert link.cmd("SAVE_PARAMS", timeout_ms=3000)["status"] == "OK"
    words = twin.act("query", what="flash")["writes"] - w0
    assert words > 0
    st = status(link)
    assert "CFG_DIRTY" not in st["sys_flags"]
    assert link.cmd("REBOOT", {"magic": rc.REBOOT_MAGIC})["status"] == "OK"
    twin.advance_ms(100)
    assert [r["cause"] for r in twin.resets] == ["software"]
    link.poll()
    assert _pair(link) == (11, 111)
    committed = 11
    cut_points = sorted({0, 1, 2, words // 2, words - 2, words - 1})
    for n, v in zip(cut_points, range(20, 20 + len(cut_points))):
        _set_pair(link, v)
        twin.act("flash", cut_after_word=n)
        link.send("SAVE_PARAMS")
        twin.advance_ms(1500)
        assert twin.resets[-1]["cause"] == "power", f"cut after word {n}: no power cut happened"
        link.poll()
        got = _pair(link)
        assert got in ((committed, 100 + committed), (v, 100 + v)), f"cut at word {n}: mixed record {got}"
        committed = got[0]
        boot = [e for e in link.events() if e["code"] == "BOOT"]
        assert boot[-1]["arg"] == rc.RESET_CAUSE.index("POWER_ON")
    # erase cut: keep saving until a save needs an erase (sector switch); that one is cut mid-erase
    twin.act("flash", cut_in_erase=1)
    n_resets = len(twin.resets)
    for i in range(100):
        v = 1 + (40 + i) % 80                                         # stream.fallback_hz range 1..80
        _set_pair(link, v)
        link.send("SAVE_PARAMS")
        twin.advance_ms(1500)
        link.poll()
        if len(twin.resets) > n_resets:
            break
        committed = v
    else:
        pytest.fail("no erase within 100 saves (record log never switched sectors)")
    assert _pair(link) == (committed, 100 + committed), "erase cut lost the last committed record"


# ============================================================================================ stops / latches
def _twin_state(halt: bool, paused: bool) -> cc.FwState:
    """The state the M1 twin is in after boot (+ HALT / PAUSE): NOT_ENABLED, not homed, x = 0."""
    return cc.FwState(motion_state="NOT_ENABLED", homed=False, pos_um=0, halt_latched=halt, paused=paused,
                      nvm_record_valid=False)


_REACHABLE_KEYS = {"halt_latched", "paused", "motion_state", "homed", "pos_um", "enabling_left_ms"}


def _stop_family_vectors() -> list[dict]:
    out = []
    for v in CHECK["vectors"]:
        name = rc.CMD_NAME.get(int(v["request"]["type"], 16))
        if name not in STOP_FAMILY or v["state"].get("params") or set(v["state"]) - _REACHABLE_KEYS:
            continue
        st = _twin_state(bool(v["state"].get("halt_latched")), bool(v["state"].get("paused")))
        payload = bytes.fromhex(v["request"]["payload_hex"])
        verdict = MODEL.check(st, int(v["request"]["type"], 16), payload)
        if verdict != (v["expect"]["status"], v["expect"]["detail"]):
            continue                                    # depends on a state the M1 twin cannot reach
        out.append(v)
    return out


VECS = _stop_family_vectors()


def _enter(link: TwinLink, halt: bool, paused: bool) -> None:
    if paused:
        assert link.cmd("PAUSE")["status"] == "OK"
    if halt:
        assert link.cmd("HALT")["status"] == "OK"


@pytest.mark.req("FW-CMD-003", "SAF-FW-023", "FW-CMD-001")
@pytest.mark.parametrize("vec", VECS, ids=[v["name"] for v in VECS])
def test_stop_family_vectors_in_twin(twin, vec):
    link = TwinLink(twin)
    halt, paused = bool(vec["state"].get("halt_latched")), bool(vec["state"].get("paused"))
    _enter(link, halt, paused)
    link.poll()
    frame = bytes.fromhex(vec["request"]["frame_hex"])
    twin.feed_rx(frame)
    twin.advance_ms(10)
    link.poll()
    seq = vec["request"]["seq"]
    resp = [f for f in link.frames if f.type == frame[2] | 0x80 and f.seq == seq]
    assert resp, "no response"
    if vec["expect"]["status"] == "OK":
        assert resp[-1].payload[0] == 0
    else:
        assert resp[-1].raw.hex().upper() == vec["expect"]["response_frame_hex"]
    st = status(link)
    if "paused_after" in vec["expect"]:
        assert ("PAUSED" in st["status"]) == vec["expect"]["paused_after"]


@pytest.mark.req("SAF-FW-023", "FW-CMD-003", "D-30", "D-31", "D-36")
@pytest.mark.parametrize("halt,paused", [(False, False), (True, False), (False, True), (True, True)])
def test_stop_family_oracle_and_events(twin, halt, paused):
    """Every stop/clear command in the 4 reachable latch states: verdict = ref_cmdcheck, latch effects + EVENTs."""
    for name, fields in (("STOP", {"mode": 0}), ("STOP", {"mode": 1}), ("STOP", {"mode": 2}), ("HALT", {}),
                         ("PAUSE", {}), ("RESUME", {}), ("HALT_CLEAR", {}), ("ESTOP_CLEAR", {}), ("FAULT_CLEAR", {})):
        twin.act("reset", cause="pin")
        twin.advance_ms(5)
        link = TwinLink(twin)
        _enter(link, halt, paused)
        link.poll()
        n_ev = len(link.events())
        exp = MODEL.check(_twin_state(halt, paused), rc.CMD[name], rc.encode_request(name, fields))
        r = link.cmd(name, fields)
        got = (r["status"], r.get("detail", 0))
        assert got == exp, (name, fields, halt, paused, got, exp)
        twin.advance_ms(5)
        link.poll()
        ev = [(e["code"], e["arg"]) for e in link.events()[n_ev:] if e["code"] != "VALID_CLEARED"]
        st = status(link)
        ok = exp[0] == "OK"
        want_paused = (paused or (ok and name == "PAUSE")) and not (ok and name in ("RESUME", "HALT_CLEAR"))
        want_halt = (halt or (ok and name == "HALT")) and not (ok and name == "HALT_CLEAR")
        assert ("PAUSED" in st["status"]) == want_paused, (name, st["status"])
        assert ("HALT" in st["flags"]) == want_halt, (name, st["flags"])
        want_ev = []
        if ok and name == "HALT" and not halt:
            want_ev.append(("HALT_SET", rc.SOURCE.index("PC")))
        if ok and name == "PAUSE" and not paused:
            want_ev.append(("PAUSED", rc.SOURCE.index("PC")))
        if ok and name == "HALT_CLEAR" and halt:
            want_ev.append(("HALT_CLEARED", 0))
        if ok and name == "HALT_CLEAR" and paused:
            want_ev.append(("PAUSE_CLEARED", 2))
        if ok and name == "RESUME" and paused:
            want_ev.append(("PAUSE_CLEARED", 3))
        assert sorted(ev) == sorted(want_ev), (name, halt, paused, ev)


@pytest.mark.req("FW-CMD-003", "D-36")
def test_confirm_repeats_are_idempotent(twin):
    """20 CONFIRM repeats of HALT / PAUSE (SW retry class CONFIRM): one EVENT, every repeat answered OK."""
    link = TwinLink(twin)
    for name in ("HALT", "PAUSE"):
        for _ in range(20):
            assert link.cmd(name)["status"] == "OK"
    twin.advance_ms(5)
    link.poll()
    codes = [e["code"] for e in link.events()]
    assert codes.count("HALT_SET") == 1 and codes.count("PAUSED") == 1


@pytest.mark.req("SAF-FW-002", "SAF-FW-003", "IF-011")
@pytest.mark.parametrize("name,fields", [("HALT", {}), ("STOP", {"mode": 0}), ("STOP", {"mode": 1}), ("PAUSE", {})])
def test_stop_sniffer_path_in_virtual_time(twin, name, fields):
    """Last byte of a STOP/HALT/PAUSE frame at 5 phases vs the 1 kHz tick, each with a running un-homed JOG
    (v_unhomed): the motion part runs <= 2 ms after the last byte — HALT / STOP 0: the stop primitive is called
    before the response is on the wire and no PUL edge follows later than 2 ms; STOP 1 / PAUSE: 2 ms after the
    last byte the axis is halted (clean-halt path, ICD §6.5) or already decelerating. Un-skipped in v0.7
    (REQ-C-M2-12): M2 FW has motion, so the stop path is exercised with a running motion."""
    link = TwinLink(twin)
    twin.advance_ms(5)
    if "MOTION" not in link.cmd("GET_INFO")["info"]["features"]:
        pytest.skip("A's FW build reports FEAT_MOTION = 0")
    link.cmd("STREAM_STOP")
    r = link.cmd("ENABLE")
    assert r["status"] == "OK", r
    _run(twin, link, r.get("settle_ms", 500) + 20)
    v = int(MODEL.value(cc.FwState(), "motion.v_unhomed_um_s"))
    tick_us = 1e6 / 90e6
    for phase in (0.05, 0.3, 0.55, 0.8, 0.99):
        if name in ("HALT", "PAUSE"):
            assert link.cmd("HALT_CLEAR")["status"] == "OK"      # clears the previous phase's latch
        r = link.cmd("JOG", {"v_um_s": v, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
        assert r["status"] == "OK", (phase, r)
        _run(twin, link, 100)                                    # cruise reached, inside the jog dead-man time
        assert status(link)["motion_state"] == "JOG", phase
        base = (int(twin.now_us // 1000) + 3) * 1000
        seq = link.send(name, fields, at_us=base + phase * 1000)
        twin.advance_ms(15)
        link.poll()
        rx, tx = wire_pair(twin, name, seq)
        assert tx["first_us"] - rx["last_us"] <= 10_000
        t = [e["t_us"] for e in twin.act("query", what="edges", since_us=rx["last_us"] - 10_000)["edges"]
             if e["pin"] == "PUL" and e["level"] == 1]
        before = [x for x in t if x <= rx["last_us"]]
        assert len(before) >= 2, (phase, "jog not running")
        cruise = before[-1] - before[-2]
        late = [x for x in t if x > rx["last_us"] + 2_000]
        if name == "HALT" or fields.get("mode") == 0:
            calls = [c for c in twin.act("query", what="seam_log", since_us=rx["last_us"])["seam_log"]
                     if c["call"] in ("hal_step_stop_now", "hal_step_abort")]
            assert calls and calls[0]["t_us"] - rx["last_us"] <= 2_000, (phase, calls[:1])
            assert calls[0]["t_us"] <= tx["first_us"], (phase, "stop not executed before the response")
            assert not late, (phase, late[:3])
        else:
            k = next((i for i, x in enumerate(t) if x > rx["last_us"] + 2_000), None)
            assert k is None or t[k] - t[k - 1] > cruise + tick_us, (phase, cruise, t[k] - t[k - 1])
        _run(twin, link, 200)
        assert status(link)["motion_state"] == "IDLE", phase


def _run(tw: Twin, link: TwinLink, ms: float) -> None:
    """Advance virtual time with a PING heartbeat every 150 ms (link watchdog, ICD §9.2)."""
    end = tw.now_us + ms * 1000
    while tw.now_us < end:
        link.send("PING")
        tw.advance_us(min(150_000.0, end - tw.now_us))
    link.poll()
