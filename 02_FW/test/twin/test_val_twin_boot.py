"""Validator E - twin: boot state, GET_INFO, CLK_FALLBACK, stream on/off, twin build integrity.

Verifies: FW-CFG-004, IF-008, FW-PLT-002, FW-STR-001, SYS-008, SAF-FW-018 (M1 boot part), NFR-005
"""
from __future__ import annotations

import re
import subprocess
import sys

import ref_codec as rc
from vhelp import FW, V, gen_define, idle_status_bits


def _fw_version_from_ini() -> list[int]:
    t = (FW / "platformio.ini").read_text(encoding="utf-8")
    return [int(re.search(rf"-DFW_VERSION_{k}=(\d+)", t).group(1)) for k in ("MAJOR", "MINOR", "PATCH")]


def test_get_info_fields(v: V, tw):
    """TC-FW-CFG-004-01, TC-IF-008-01 (GET_INFO part)."""
    # Verifies: FW-CFG-004, IF-008
    # TC: TC-FW-CFG-004-01, TC-IF-008-01
    i = v.info()
    assert (i["proto_major"], i["proto_minor"]) == (int(gen_define("PROTO_MAJOR").rstrip("u")),
                                                    int(gen_define("PROTO_MINOR").rstrip("u"))) == (1, 0)
    assert i["payload_version"] == rc.PAYLOAD_VERSION == 1
    assert i["fw_version"] == _fw_version_from_ini()
    assert int(i["param_dict_hash"], 16) == int(gen_define("PARAM_DICT_HASH").rstrip("uUlL"), 16)
    # dict_version 4 (ICD v0.5, CR-01: io.stop_active_level retired) -> 47 parameters (v0.2 plan: 48)
    assert i["param_count"] == int(gen_define("PARAM_COUNT").rstrip("u")) == 47
    assert i["uid"].upper() == tw.uid.upper()                      # hal_uid seam
    if "MOTION" in i["features"]:                                  # M2 build (plan v0.3 §5.2): MOVE_UNTIL_LOAD is M4
        assert set(i["features"]) == {"AFE", "MOTION", "HOMING", "NVM", "TWIN", "BUTTONS", "DRV_SIGNALS"}
    else:
        assert set(i["features"]) == {"AFE_SYNTHETIC", "NVM", "TWIN"}  # M1 build + FEAT_TWIN
    assert i["build"]                                             # build id string present


def test_boot_state_and_stream_off(v: V):
    """TC-FW-STR-001-01 (boot part); SAF-FW-018 M1 part: NOT_ENABLED, not homed, VALID 0, stream off."""
    # Verifies: FW-STR-001, SAF-FW-018
    # TC: TC-FW-STR-001-01
    v.advance(1000)
    assert v.data() == []                                         # no DATA after boot
    st = v.status()
    assert st["motion_state"] == "NOT_ENABLED"
    assert "HOMED" not in st["flags"] and "VALID" not in st["flags"]
    assert "STREAM_ON" not in st["sys_flags"]
    assert "PAUSED" not in st["status"] and st["pause_src"] == "NONE" and st["halt_src"] == "NONE"
    assert st["reset_cause"] == "POWER_ON"
    ev = v.events()
    assert ev[0]["code"] == "BOOT" and ev[0]["arg"] == rc.RESET_CAUSE.index("POWER_ON")
    assert "CLK_FALLBACK" not in [e["code"] for e in ev]
    assert st["stack_free_min"] > 0                               # NFR-005: stack high-water reported


def test_stream_start_stop_idempotent_frame_seq_continues(v: V):
    """TC-FW-STR-001-01."""
    # Verifies: FW-STR-001
    # TC: TC-FW-STR-001-01
    v.ok("STREAM_START")
    v.ok("STREAM_START")
    v.advance(500)
    d1 = v.data()
    assert 38 <= len(d1) <= 42                                    # 80 Hz, one stream (not doubled)
    assert [d["frame_seq"] for d in d1] == list(range(d1[0]["frame_seq"], d1[0]["frame_seq"] + len(d1)))
    assert d1[0]["frame_seq"] == 0                                # starts at 0 at boot
    v.ok("STREAM_STOP")
    v.ok("STREAM_STOP")
    n = len(v.data())
    v.advance(500)
    assert len(v.data()) == n                                     # stopped
    v.ok("STREAM_START")
    v.advance(100)
    d2 = v.data()[n:]
    assert d2 and d2[0]["frame_seq"] == d1[-1]["frame_seq"] + 1   # not reset by STOP/START
    assert all(d["payload_version"] == 1 for d in v.data())       # IF-008 every DATA frame


def test_clk_fallback_status_event_not_data(tw, v: V):
    """TC-FW-PLT-002-02."""
    # Verifies: FW-PLT-002
    # TC: TC-FW-PLT-002-02
    assert tw.act("clk", hse_fail=True)["ok"]
    tw.act("reset", cause="pin")
    v.advance(5)
    v.link.frames.clear()
    st = v.status()
    assert "CLK_FALLBACK" in st["sys_flags"]
    v.ok("STREAM_START")
    v.advance(200)
    ev = [e["code"] for e in v.events()]
    # EVENT order at boot: BOOT, CLK_FALLBACK, NVM result (FW_design §3.2) - events sent before the
    # link frames were cleared are re-read from the wire log instead
    wl = [rc.decode_event(bytes.fromhex(w["hex"])[6:-2]) for w in v.wire("tx") if w["type"] == rc.ASYNC["EVENT"]]
    codes = [e["code"] for e in wl]
    i = max(k for k, c in enumerate(codes) if c == "BOOT")
    assert codes[i:i + 2] == ["BOOT", "CLK_FALLBACK"]
    assert ev.count("CLK_FALLBACK") == 0                          # once, at boot only
    d = v.data()
    assert d and all(set(x["flags"]) <= set(rc.DATA_FLAGS) for x in d)
    # no DATA bit for the clock (FW-PLT-002): flags/status of DATA identical to a normal boot
    base = {"AFE_SETTLING"} | idle_status_bits(v)                 # v0.3: driver bits valid with DRV_SIGNALS
    assert all(x["flags"] == [] and set(x["status"]) <= base for x in d),         {(tuple(x["flags"]), tuple(x["status"])) for x in d}


def test_twin_builds_from_unchanged_sources():
    """TC-SYS-008-01 (T part): seam headers identical to the README seam block, A's core linked."""
    # Verifies: SYS-008
    # TC: TC-SYS-008-01
    tools = FW.parent / "00_System" / "tools" / "fw_twin"
    r = subprocess.run([sys.executable, str(tools / "build.py"), "--check-seams"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    log = (tools / "build" / "build_fw.log").read_text(encoding="utf-8", errors="replace")
    for d in ("src/pure", "src/core", "src/gen"):
        assert d.replace("/", "\\") in log or d in log
    for f in list((FW / "src" / "core").glob("*.c")) + list((FW / "src" / "pure").glob("*.[ch]")):
        t = f.read_text(encoding="utf-8")
        assert not re.search(r"#\s*if(def|ndef)?\s+.*\b(FW_TWIN|TWIN|HOST_TEST)\b", t), f
