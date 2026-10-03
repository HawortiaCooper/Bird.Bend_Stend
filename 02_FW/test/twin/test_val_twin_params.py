"""Validator E - twin: parameters, NVM, reboot-required semantics.

Verifies: FW-CFG-002, FW-CFG-003, FW-NVM-001, FW-NVM-002, FW-NVM-003, NFR-008, IF-005
"""
from __future__ import annotations

import json
import random
import struct

import nvm_ref
import pytest
import ref_codec as rc
from vhelp import DICT, PBYID, PBYKEY, V, wire_value

VEC = json.loads((PBYKEY and __import__("pathlib").Path(__file__).resolve().parents[3] /
                  "00_System" / "tools" / "vectors" / "protocol_vectors.json").read_text(encoding="utf-8"))
SESSION = {p.key for p in DICT.params if not p.nvm}
REBOOT_REQ = {p.key for p in DICT.params if p.reboot_required}


def _random_value(p, rnd: random.Random):
    if p.type == "enum":
        return rnd.choice([e.value for e in p.enum])
    if p.type == "bool":
        return rnd.randint(0, 1)
    if p.type == "f32":
        return wire_value(p, rnd.uniform(p.min, p.max))
    return rnd.randint(int(p.min), int(p.max))


def _values(entries) -> dict[int, object]:
    return {e["id"]: e["value"] for e in entries}


def test_get_all_params_pages_equal_vectors_at_defaults(v: V):
    """TC-FW-CFG-002-01 (part 1): page frames byte-identical to the vectors at defaults; page_count -> E_RANGE."""
    # Verifies: FW-CFG-002
    # TC: TC-FW-CFG-002-01
    vec = {f["name"]: f for f in VEC["frames"]}
    for page in range(3):
        seq = v.link.send("GET_ALL_PARAMS", {"page": page})
        v.advance(10)
        fr = [f for f in v.responses() if f.seq == seq][0]
        ref = bytes.fromhex(vec[f"get_all_params_p{page}_resp"]["payload_hex"])
        assert fr.payload == ref, f"page {page}"
    r = v.cmd("GET_ALL_PARAMS", {"page": 3})
    assert (r["status"], r["detail"]) == ("E_RANGE", 0)


def test_all_params_after_random_sets(v: V):
    """TC-FW-CFG-002-01 (part 2) + TC-FW-CFG-003-02 (read-back = response, no flash write)."""
    # Verifies: FW-CFG-002, FW-CFG-003
    # TC: TC-FW-CFG-002-01, TC-FW-CFG-003-02
    rnd = random.Random(20261003)
    model = _values(v.all_params())
    w0 = v.tw.act("query", what="flash")["writes"]
    accepted = 0
    for _ in range(200):
        p = rnd.choice(DICT.params)
        val = _random_value(p, rnd)
        r = v.set(p.key, val)
        if r["status"] == "E_CONFIG":                              # hard rule against current RAM
            continue
        assert r["status"] == "OK", (p.key, val, r)
        model[p.id] = r["entry"]["value"]
        assert r["entry"]["value"] == wire_value(p, val)           # value as stored = value sent
        assert v.ok("GET_PARAM", {"id": p.id})["entry"] == r["entry"]   # response == GET_PARAM
        accepted += 1
    assert accepted >= 20
    ent = v.all_params()
    ids = [e["id"] for e in ent]
    assert ids == sorted(ids) and len(ids) == len(set(ids)) == len(DICT.params)   # each once, ascending
    assert _values(ent) == model
    assert v.tw.act("query", what="flash")["writes"] == w0          # SET never writes flash


def test_reboot_required_params_old_behaviour_until_save_reboot(v: V):
    """TC-FW-CFG-003-02 (reboot-required part): ena_invert / pul_invert / pwr_sense_enable."""
    # Verifies: FW-CFG-003, FW-PAR-002, FW-PAR-006
    # TC: TC-FW-CFG-003-02
    assert REBOOT_REQ == {"motion.pul_invert", "motion.ena_invert", "drv.pwr_sense_enable"}
    if "DRV_SIGNALS" in v.info()["features"]:                    # M2 build: make driver power absent
        v.tw.act("drv_power", on=False)
        v.advance(40)
    ena0 = v.tw.act("query", what="outputs")["ENA"]
    st0 = v.status()
    assert "DRV_PWR" not in st0["status"]                         # sense enabled: M1 not confirmed / M2 off
    en0 = v.cmd("ENABLE")
    assert (en0["status"], en0["detail"]) == ("E_STATE", 1 << rc.BLOCK.index("DRV_UNPOWERED"))
    for k in ("motion.ena_invert", "motion.pul_invert"):
        assert v.set(k, 1)["status"] == "OK"
    st = v.status()
    assert "REBOOT_PENDING" in st["sys_flags"]
    assert v.tw.act("query", what="outputs")["ENA"] == ena0       # old ENA level kept
    assert v.set("drv.pwr_sense_enable", 0)["status"] == "OK"
    st = v.status()
    assert "REBOOT_PENDING" in st["sys_flags"]
    # old behaviour must be kept until SAVE + REBOOT (FW-CFG-003, params.yaml reboot_required)
    r = v.cmd("ENABLE")
    old_kept = {"status_DRV_PWR_unchanged": "DRV_PWR" not in st["status"],
                "enable_still_refused_DRV_UNPOWERED": (r["status"], r.get("detail")) == ("E_STATE", en0["detail"])}
    print("VALOBS reboot-required drv.pwr_sense_enable before reboot:", old_kept, "ENABLE ->", r)
    v.ok("SAVE_PARAMS")
    v.reboot()
    v.advance(60)
    st = v.status()
    assert "REBOOT_PENDING" not in st["sys_flags"]
    assert "DRV_PWR" in st["status"]                              # new behaviour after SAVE + REBOOT
    assert old_kept == {"status_DRV_PWR_unchanged": True, "enable_still_refused_DRV_UNPOWERED": True}, \
        f"DEF-M1-01: drv.pwr_sense_enable (reboot_required) took effect without reboot: {old_kept}"


def test_nonreboot_params_effective_at_once(v: V):
    """TC-FW-CFG-003-02 (others effective <= 10 ms): RATE pin and HX711 config follow afe.rate_sps."""
    # Verifies: FW-CFG-003, FW-AFE-002 (M1 synthetic part)
    # TC: TC-FW-CFG-003-02
    assert v.tw.act("query", what="outputs")["RATE"] == 1
    r = v.set("afe.rate_sps", "SPS10")
    assert r["status"] == "OK"
    t_resp = v.tw.now_us
    assert v.tw.act("query", what="outputs")["RATE"] == 0
    seam = [s for s in v.tw.act("query", what="seam_log")["seam_log"] if s["call"] == "hal_hx711_config"]
    assert seam and "rate80=0" in seam[-1]["args"] and seam[-1]["t_us"] <= t_resp + 10_000
    v.ok("STREAM_START")
    v.advance(2000)
    d = v.data()
    assert 18 <= len(d) <= 22                                     # 10 SPS
    assert not [e for e in v.events() if e["code"] == "AFE_STALE" and e["arg"] == 1]   # H5 default 250 ms


def test_cfg_dirty_save_reboot_defaults_load(v: V):
    """TC-FW-NVM-001-01."""
    # Verifies: FW-NVM-001, FW-NVM-002 (session values), SAF-FW-010 (DEFAULT -> session defaults)
    # TC: TC-FW-NVM-001-01
    st = v.status()
    assert {"CFG_DIRTY", "NVM_DEFAULTED"} <= set(st["sys_flags"])          # blank flash
    ev = v.events()
    assert ("PARAMS_DEFAULTED", 1) in [(e["code"], e["arg"]) for e in ev]
    assert v.set("stream.fallback_hz", 25)["status"] == "OK"
    assert v.set("limits.soft_max_um", 250000)["status"] == "OK"
    r = v.ok("SAVE_PARAMS")
    st = v.status()
    assert "CFG_DIRTY" not in st["sys_flags"] and "NVM_DEFAULTED" not in st["sys_flags"]
    seq1 = st["nvm_record_seq"]
    assert seq1 >= 1 and ("PARAMS_SAVED", seq1) in [(e["code"], e["value"]) for e in v.events()]
    # SET of an nvm parameter -> dirty; back to the record value -> clean (ICD §11.2 definition)
    assert v.set("stream.fallback_hz", 30)["status"] == "OK"
    assert "CFG_DIRTY" in v.status()["sys_flags"]
    assert v.set("stream.fallback_hz", 25)["status"] == "OK"
    assert "CFG_DIRTY" not in v.status()["sys_flags"]
    # session SET never dirties
    assert v.set("safety.zero_raw", 1234)["status"] == "OK"
    assert v.set("safety.load_raw_max", 5000000)["status"] == "OK"
    assert "CFG_DIRTY" not in v.status()["sys_flags"]
    # reboot -> saved values restored, session values at defaults
    n_ev = len(v.events())
    v.reboot()
    v.advance(60)
    assert v.get("stream.fallback_hz") == 25 and v.get("limits.soft_max_um") == 250000
    assert v.get("safety.zero_raw") == 0 and v.get("safety.load_raw_max") == PBYKEY["safety.load_raw_max"].default
    ev = v.events()[n_ev:]
    assert ("PARAMS_LOADED", 0) in [(e["code"], e["arg"]) for e in ev]
    assert "CFG_DIRTY" not in v.status()["sys_flags"]
    # DEFAULT_PARAMS -> all defaults incl. session values, dirty, PARAMS_DEFAULTED(0)
    assert v.set("safety.zero_raw", 777)["status"] == "OK"
    v.ok("DEFAULT_PARAMS")
    assert v.get("stream.fallback_hz") == 10 and v.get("safety.zero_raw") == 0
    assert "CFG_DIRTY" in v.status()["sys_flags"]
    assert ("PARAMS_DEFAULTED", 0) in [(e["code"], e["arg"]) for e in v.events()]
    # LOAD -> record values, session values kept
    assert v.set("safety.zero_raw", -4321)["status"] == "OK"
    v.ok("LOAD_PARAMS")
    assert v.get("stream.fallback_hz") == 25 and v.get("safety.zero_raw") == -4321
    assert "CFG_DIRTY" not in v.status()["sys_flags"]
    assert [e for e in v.events() if e["code"] == "PARAMS_LOADED"]
    # session parameters are never in a record (decoded flash)
    _, newest = nvm_ref.scan(nvm_ref.read(v.tw.run_dir / "flash.bin"))
    assert newest is not None
    assert not {PBYKEY[k].id for k in SESSION} & set(newest.entries)
    assert newest.entries[PBYKEY["stream.fallback_hz"].id][1] == 25


def _record_values(flash: bytes) -> dict[int, int] | None:
    _, newest = nvm_ref.scan(flash)
    return None if newest is None else {i: raw for i, (_t, raw) in newest.entries.items()}


def test_power_cut_at_every_program_word_and_erase(v: V, tw):
    """TC-FW-NVM-002-02: power cut after every program word (and mid-erase) over consecutive SAVEs incl.
    a sector switch -> after every cut a complete valid record (old or new, never mixed)."""
    # Verifies: FW-NVM-002
    # TC: TC-FW-NVM-002-02
    key = "stream.fallback_hz"
    pid = PBYKEY[key].id
    path = tw.run_dir / "flash.bin"
    v.set(key, 11)
    v.ok("SAVE_PARAMS")
    cur = 11
    words = None
    cuts = 0
    for n in range(0, 200):
        new = 12 + (n % 60)
        if new == cur:
            new += 1
        assert v.set(key, new)["status"] == "OK"
        before = _record_values(path.read_bytes())
        resets0 = len(tw.resets)
        tw.act("flash", cut_after_word=n)
        seq = v.link.send("SAVE_PARAMS")
        v.advance(1200)
        cut = len(tw.resets) > resets0
        after = _record_values(path.read_bytes())
        assert after is not None, f"no valid record after cut at word {n}"
        if cut:
            cuts += 1
            assert after in (before, {**before, pid: new}), f"mixed record after cut at word {n}"
            v.link = type(v.link)(tw)                             # new PC session after the power cut
            v.advance(5)
            got = v.get(key)
            assert got == after[pid]                               # boot loaded exactly the record
            cur = got
        else:
            words = n if words is None else words                 # first n without a cut = words per SAVE
            assert after[pid] == new
            r = v.link.find("SAVE_PARAMS", seq)
            assert r is not None and r["status"] == "OK"
            cur = new
            tw.act("flash", cut_after_word=10**9)                  # disarm (counter persists otherwise)
            break
    assert words is not None and cuts == words and 90 <= words <= 120, (words, cuts)
    # fill the active sector so that the next SAVE switches sectors with an erase; cut mid-erase
    slots, newest = nvm_ref.scan(path.read_bytes())
    used = sum(1 for s in slots if s[0] == newest.sector and s[2] != "blank")
    for k in range(32 - used):
        assert v.set(key, 20 + (k % 50))["status"] == "OK"
        v.ok("SAVE_PARAMS")
    before = _record_values(path.read_bytes())
    assert v.set(key, 79)["status"] == "OK"
    resets0 = len(tw.resets)
    tw.act("flash", cut_in_erase=1)
    v.link.send("SAVE_PARAMS")
    v.advance(1500)
    assert len(tw.resets) == resets0 + 1                           # cut during the erase
    assert _record_values(path.read_bytes()) == before             # previous record intact
    v.link = type(v.link)(tw)
    v.advance(5)
    assert v.get(key) == before[pid]
    # the next SAVE completes the switch: record in the other sector
    sec0 = nvm_ref.scan(path.read_bytes())[1].sector
    assert v.set(key, 79)["status"] == "OK"
    v.ok("SAVE_PARAMS")
    nw = nvm_ref.scan(path.read_bytes())[1]
    assert nw.sector != sec0 and nw.slot == 0 and nw.entries[pid][1] == 79
    # cut after every word of the first SAVE in the new sector as well (slot 1)
    for n in (0, 1, 45, 89, 90, 96, 97):
        before = _record_values(path.read_bytes())
        assert v.set(key, 5 + n % 7)["status"] == "OK"
        resets0 = len(tw.resets)
        tw.act("flash", cut_after_word=n)
        v.link.send("SAVE_PARAMS")
        v.advance(1200)
        after = _record_values(path.read_bytes())
        if len(tw.resets) > resets0:
            assert after in (before, {**before, pid: 5 + n % 7})
            v.link = type(v.link)(tw)
            v.advance(5)
        tw.act("flash", cut_after_word=10**9)


def test_blank_flash_and_hash_change_migration(v: V, tw):
    """TC-FW-NVM-002-02 (boot rules): blank -> PARAMS_DEFAULTED(1); other hash -> migration (3)."""
    # Verifies: FW-NVM-002
    # TC: TC-FW-NVM-002-02
    ev = v.events()
    assert [(e["code"], e["arg"]) for e in ev if e["code"].startswith("PARAMS_")] == [("PARAMS_DEFAULTED", 1)]
    assert "NVM_DEFAULTED" in v.status()["sys_flags"]
    assert v.set("limits.soft_max_um", 222000)["status"] == "OK"
    v.ok("SAVE_PARAMS")
    path = tw.run_dir / "flash.bin"
    fl = path.read_bytes()
    _, newest = nvm_ref.scan(fl)
    exe, run_dir = tw.exe, tw.run_dir
    tw.close()                                                     # power off: engine flushes flash.bin
    fl = path.read_bytes()
    _, newest = nvm_ref.scan(fl)
    path.write_bytes(nvm_ref.patch_hash(fl, newest, 0x13961802))   # record of dictionary v1
    from twin import Twin
    tw2 = Twin("lockstep", exe=exe, run_dir=run_dir, fresh_flash=False)
    v = V(tw2)
    v.advance(5)
    st = v.status()
    assert {"CFG_DIRTY", "NVM_DEFAULTED"} <= set(st["sys_flags"])
    ev = [(e["code"], e["arg"]) for e in v.events() if e["code"].startswith("PARAMS_")]
    assert ev == [("PARAMS_DEFAULTED", 3)]
    assert v.get("limits.soft_max_um") == 222000                   # kept by id (same type, in range)
    tw2.close()


def test_save_no_split_frame_and_overrun_after_stall(v: V, tw):
    """TC-FW-NVM-003-01 (idle part): TX drained before the flash op, no frame on the wire during it,
    missed conversions flagged OVERRUN; LOAD/DEFAULT/SAVE response times (NFR-008)."""
    # Verifies: FW-NVM-003, NFR-008
    # TC: TC-FW-NVM-003-01, TC-NFR-008-01
    # fill both sectors (2 x 32 slots) so that the SAVE under test needs a sector erase (500 ms stall)
    for k in range(64):
        assert v.set("stream.fallback_hz", 2 + k % 60)["status"] == "OK"
        v.ok("SAVE_PARAMS")
    v.ok("STREAM_START")
    v.advance(100)
    # backlog: three 152 B pages + events, then SAVE in the same burst
    for p in range(3):
        v.link.send("GET_ALL_PARAMS", {"page": p})
    v.link.send("HALT")
    v.link.send("HALT_CLEAR")
    seq = v.link.send("SAVE_PARAMS")
    t_save = v.tw.now_us
    hseq = 0xE1                                                    # OBS: stop command during the erase
    t_halt = t_save + 50_000
    assert v.tw.act("rx_bytes", hex=rc.make_frame("HALT", hseq, {}).hex(), at_us=t_halt)["ok"]
    v.advance(1500)
    r = v.link.find("SAVE_PARAMS", seq)
    assert r is not None and r["status"] == "OK"
    hr = [w for w in v.wire("tx") if w["type"] == rc.CMD["HALT"] | 0x80 and w["seq"] == hseq]
    t_halt = [w for w in v.wire("rx") if w["type"] == rc.CMD["HALT"] and w["seq"] == hseq][0]["last_us"]
    assert hr
    print(f"VALOBS HALT response during SAVE erase: {(hr[0]['first_us'] - t_halt) / 1000:.1f} ms")
    seam = v.tw.act("query", what="seam_log")["seam_log"]
    seam = [x for x in seam if x["t_us"] > v.data()[0]["t_us"]]
    fl = [s for s in seam if s["call"] in ("hal_flash_program", "hal_flash_erase")]
    assert fl
    assert fl[0]["call"] == "hal_flash_erase"
    t0 = fl[0]["t_us"]
    print(f"VALOBS SAVE timeline: request {t_save:.0f} us, erase start {t0:.0f} us, HALT request {t_halt:.0f} us, "
          f"HALT response {hr[0]['first_us']:.0f} us")
    last = fl[-1]
    t1 = last["t_us"] + (16 * int(last["args"].split()[1]) / 4 if last["call"] == "hal_flash_program" else 500_000)
    for w in v.wire("tx"):
        assert not (w["first_us"] < t1 and w["last_us"] > t0), f"frame on the wire during the flash op {w}"
    data = v.data()
    gaps = [(a, b) for a, b in zip(data, data[1:]) if b["t_us"] - a["t_us"] > 20_000]
    assert gaps, "a conversion gap around SAVE is expected (AFE held)"
    for a, b in gaps:
        assert "OVERRUN" in b["flags"], "first frame after the stall must carry OVERRUN"
    later = [d for d in data if d["t_us"] > gaps[-1][1]["t_us"]]
    assert all("OVERRUN" not in d["flags"] for d in later)
    # response time: SAVE <= 2.5 s, LOAD / DEFAULT <= 2.5 s (twin erase model 500 ms)
    rx = [w for w in v.wire("rx") if w["type"] == rc.CMD["SAVE_PARAMS"]][-1]
    tx = [w for w in v.wire("tx") if w["type"] == rc.CMD["SAVE_PARAMS"] | 0x80 and w["seq"] == rx["seq"]][0]
    assert tx["first_us"] - rx["last_us"] <= 2_500_000


def test_same_frame_twice_answered_twice_no_dedup(v: V):
    """TC-IF-005-01 (FW part): identical frames (same SEQ) are each answered; no double motion."""
    # Verifies: IF-005, FW-CMD-001
    # TC: TC-IF-005-01
    p = PBYKEY["stream.fallback_hz"]
    for name, fields in (("PING", {}), ("SET_PARAM", {"id": p.id, "type": "u8", "value": 15}),
                         ("MOVE_ABS", {"target_um": 1000, "v_um_s": 1000, "a_um_s2": 0}), ("HALT", {})):
        fr = rc.make_frame(name, 0x77, fields)
        n0 = len(v.responses())
        v.tw.feed_rx(fr + fr)
        v.advance(10)
        rs = [f for f in v.responses()[n0:]]
        assert len(rs) == 2 and all(f.seq == 0x77 and f.type == rc.CMD[name] | 0x80 for f in rs), name
    assert [e["code"] for e in v.events()].count("HALT_SET") == 1   # HALT idempotent
    assert v.tw.act("query", what="pulses")["count"] == 0          # no motion at all in M1
