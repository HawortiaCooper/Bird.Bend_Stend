"""Proves the protocol name registry (protocol.yaml) against the reference codec, the generated
outputs, the ICD and the vectors (GF-08, OI-FW-11, ICD v0.2 §0.3).

Verifies: IF-001, IF-010, IF-012, FW-STR-003, FW-STR-006, SAF-FW-023 (PAUSED, D-29a),
          SAF-FW-005 (driver power, D-29c), FW-HOM-001 (START-only homing, D-29b)
Run:  .venv\\Scripts\\python -m pytest 00_System/tools/tests -q
"""
from __future__ import annotations

import importlib.util
import json
import re
import struct
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import gen_params
import gen_protocol
import gen_vectors
import ref_cmdcheck as cc
import ref_codec as rc

PROTO = gen_protocol.load()
PD = gen_params.load()
VEC = Path(gen_vectors.VEC_DIR)
CHECK = json.loads((VEC / "check_vectors.json").read_text(encoding="utf-8"))
FRAMES = json.loads((VEC / "protocol_vectors.json").read_text(encoding="utf-8"))["frames"]
ICD_TEXT = gen_params.ICD_PATH.read_text(encoding="utf-8")


def _load_generated_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("protocol_gen", gen_protocol.SW_PROTO_PY_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["protocol_gen"] = mod
    spec.loader.exec_module(mod)
    return mod


PG = _load_generated_module()

# ref_codec hand-written table -> protocol.yaml table id (independent oracle == registry)
BIT_TABLES = {"data_flags": rc.DATA_FLAGS, "data_status": rc.DATA_STATUS, "faults": rc.FAULTS,
              "block": rc.BLOCK, "io": rc.IO, "sys_flags": rc.SYS_FLAGS, "features": rc.FEATURES}
ENUM_TABLES = {"motion_state": rc.MOTION_STATE, "home_phase": rc.HOME_PHASE, "source": rc.SOURCE,
               "reset_cause": rc.RESET_CAUSE, "move_done_reason": rc.MOVE_DONE_REASON}
CODE_TABLES = {"status_code": rc.STATUS, "event": rc.EVENT, "stop_cause": rc.STOP_CAUSE,
               "async_type": rc.ASYNC}


# ---------------------------------------------------------------------------- registry == oracle
@pytest.mark.parametrize("tid", sorted(BIT_TABLES))
def test_bit_tables_match_ref_codec(tid: str) -> None:
    assert PROTO.names(tid) == BIT_TABLES[tid]


@pytest.mark.parametrize("tid", sorted(ENUM_TABLES))
def test_enum_tables_match_ref_codec(tid: str) -> None:
    codes = PROTO.codes(tid)
    assert sorted(codes.values()) == list(range(len(codes)))          # contiguous from 0
    assert PROTO.names(tid) == ENUM_TABLES[tid]


@pytest.mark.parametrize("tid", sorted(CODE_TABLES))
def test_code_tables_match_ref_codec(tid: str) -> None:
    assert PROTO.codes(tid) == CODE_TABLES[tid]


def test_small_enums_match_ref_codec() -> None:
    assert PROTO.codes("busy_detail") == {v: k for k, v in rc.BUSY_DETAIL.items()}
    assert PROTO.codes("home_fail_reason") == {v: k for k, v in rc.HOME_FAIL_REASON.items()}


def test_commands_match_ref_codec() -> None:
    assert {c.name: c.value for c in PROTO.commands} == rc.CMD
    assert {c.name: c.req_len for c in PROTO.commands} == rc.REQ_LEN


def test_constants_match_ref_codec() -> None:
    k = {c.name: c.value for c in PROTO.constants}
    assert (k["SYNC0"], k["SYNC1"], k["MAX_LEN"], k["HEADER_LEN"], k["FRAME_OVERHEAD"]) == (
        rc.SYNC0, rc.SYNC1, rc.MAX_LEN, rc.HEADER_LEN, rc.OVERHEAD)
    assert (k["INFO_LEN"], k["STATUS_LEN"], k["DATA_LEN"], k["EVENT_LEN"]) == (
        rc.INFO_LEN, rc.STATUS_LEN, rc.DATA_LEN, rc.EVENT_LEN)
    assert (k["REBOOT_MAGIC"], k["JOG_NO_BOUND"], k["AFE_NO_DATA"], k["RAW_MIN"], k["RAW_MAX"]) == (
        rc.REBOOT_MAGIC, rc.JOG_NO_BOUND, rc.AFE_NO_DATA, rc.RAW_MIN, rc.RAW_MAX)
    assert (k["PARAMS_PER_PAGE"], k["RESP_BIT"], k["INTERBYTE_TIMEOUT_MS"]) == (
        rc.PARAMS_PER_PAGE, rc.RESP_BIT, rc.INTERBYTE_TIMEOUT_MS)
    assert k["RX_BUF_MIN"] == 2 * (rc.MAX_LEN + rc.OVERHEAD)


def test_versions_consistent() -> None:
    assert PROTO.icd_version == rc.ICD_VERSION == PG.ICD_VERSION
    assert (PROTO.proto_major, PROTO.proto_minor, PROTO.payload_version) == (
        rc.PROTO_MAJOR, rc.PROTO_MINOR, rc.PAYLOAD_VERSION)
    assert f"| Version | **{PROTO.icd_version} " in ICD_TEXT
    assert re.search(rf"^\| {re.escape(PROTO.icd_version)} \| ", ICD_TEXT, re.M), "change history row"


# ---------------------------------------------------------------------------- generated Python
def test_generated_python_module_matches_registry() -> None:
    """GF-08: the SW/GUI module exports the same names, bit order and codes."""
    for t in PROTO.tables:
        cls = PG.TABLES[t.id]
        if t.kind == "bitset":
            assert getattr(PG, f"{t.id.upper()}_BITS") == tuple(PROTO.names(t.id))
            assert {m.name: m.value for m in cls} == {i.name: 1 << i.value for i in t.items}
        else:
            assert {m.name: m.value for m in cls} == {i.name: i.value for i in t.items}
        assert set(getattr(PG, f"{t.id.upper()}_DESC")) == {i.name for i in t.items}
    assert {c.name: c.value for c in PG.Cmd} == rc.CMD
    assert {c.name: n for c, n in PG.CMD_REQ_LEN.items()} == rc.REQ_LEN
    assert PG.CMD_RETRY[PG.Cmd.MOVE_ABS] == PG.RetryClass.VERIFY
    assert PG.CMD_PRIORITY == {PG.Cmd.STOP, PG.Cmd.HALT, PG.Cmd.HALT_CLEAR, PG.Cmd.ESTOP_CLEAR,
                               PG.Cmd.FAULT_CLEAR, PG.Cmd.PAUSE}
    assert PG.DataStatus.PAUSED == 1 and PG.DATA_STATUS_BITS[0] == "PAUSED"
    assert PG.STATUS_LEN == rc.STATUS_LEN == 86


def test_generated_c_header_lists_every_name() -> None:
    h = gen_protocol.FW_PROTO_H_PATH.read_text(encoding="utf-8")
    for t in PROTO.tables:
        if not t.wire:                                   # SW-only table: not in the FW header
            assert not any(f"{t.c_prefix}{i.name}" in h for i in t.items)
            continue
        for i in t.items:
            assert re.search(rf"\b{t.c_prefix}{i.name}\b", h), f"{t.c_prefix}{i.name}"
    for c in PROTO.commands:
        assert f"CMD_{c.name} " in h and f"CMD_REQ_LEN_{c.name} " in h


def test_icd_generated_tables_present() -> None:
    for tid in ("commands", "status_code", "block", "motion_state", "data_flags", "data_status",
                "faults", "io", "event", "stop_cause", "move_done_reason", "registry"):
        assert f"<!-- BEGIN GENERATED protocol:{tid} (gen_protocol.py) -->" in ICD_TEXT
    assert "home.ref_switch` START" not in ICD_TEXT                   # D-29b: no END homing


# ---------------------------------------------------------------------------- dictionary (D-29)
def test_dictionary_d29_d33_d27() -> None:
    keys = {p.key: p for p in PD.params}
    assert PD.dict_version == 3
    to = keys["afe.timeout_ms"]
    assert (to.default, to.min, to.max) == (250, 25, 1000)                     # D-33a
    assert keys["motion.steps_per_mm"].default == 800.0                        # D-27 closed
    assert "home.ref_switch" not in keys and all(p.id != 0x0401 for p in PD.params)
    k1 = keys["drv.k1_weld_ms"]
    assert (k1.id, k1.type, k1.default, k1.min, k1.max) == (0x0705, "u16", 200, 100, 2000)


def test_every_range_end_reachable() -> None:
    """ICD §11.4 (v0.2): the min/max vector of every parameter is accepted."""
    bad = [v["name"] for v in CHECK["vectors"] if v["name"].startswith("set_")
           and v["name"].endswith(("_min", "_max")) and v["expect"]["status"] != "OK"]
    assert bad == []
    assert sum(1 for v in CHECK["vectors"] if v["name"].startswith("set_")
               and v["name"].endswith(("_min", "_max"))) == 2 * len(PD.params)


# ---------------------------------------------------------------------------- PAUSED (D-29a)
MODEL = cc.Model(PD.params)


def test_paused_after_vectors() -> None:
    vs = [v for v in CHECK["vectors"] if "paused_after" in v["expect"]]
    assert len(vs) >= 15
    for v in vs:
        st = cc.FwState.from_dict(v["state"])
        pl = bytes.fromhex(v["request"]["payload_hex"])
        t = int(v["request"]["type"], 16)
        assert MODEL.paused_after(st, t, pl, v["expect"]["status"]) == v["expect"]["paused_after"], v["name"]


@pytest.mark.parametrize("name,expected", [
    ("move_paused", True), ("jog_paused", True), ("mul_paused", True), ("home_paused", True),
    ("jog_refresh_paused", True), ("jog_refresh_paused_jogging", True),
    ("halt_clear_paused", False), ("jog0_paused", True), ("move_paused_refused", True),
    ("halt_clear_paused_refused", True), ("stop_paused", True), ("halt_paused", True),
    ("estop_clear_paused", True), ("fault_clear_paused", True), ("enable_paused", True),
    ("pause_paused", True), ("pause_moving", True)])
def test_paused_clear_rule_anchor(name: str, expected: bool) -> None:
    """Independent anchors from D-30 / ICD §5.5 (not derived from the model): only HALT_CLEAR
    clears PAUSED."""
    v = next(x for x in CHECK["vectors"] if x["name"] == name)
    assert v["expect"]["paused_after"] is expected


@pytest.mark.parametrize("name", ["move_paused", "jog_paused", "mul_paused", "home_paused",
                                  "jog_refresh_paused", "jog_refresh_paused_jogging"])
def test_paused_blocks_motion(name: str) -> None:
    """D-30: every new motion start incl. jog refreshes is refused with BLOCK PAUSED (bit 10)."""
    v = next(x for x in CHECK["vectors"] if x["name"] == name)
    assert v["expect"]["status"] == "E_STATE" and "PAUSED" in v["expect"]["detail_names"]
    assert v["expect"]["detail"] & 0x0400


@pytest.mark.parametrize("name", ["jog0_paused", "stop_paused", "halt_paused", "pause_paused",
                                  "halt_clear_paused", "estop_clear_paused", "fault_clear_paused",
                                  "enable_paused", "move_after_resume"])
def test_paused_does_not_block_stops_clears(name: str) -> None:
    v = next(x for x in CHECK["vectors"] if x["name"] == name)
    assert v["expect"]["status"] == "OK", name


# ---------------------------------------------------------------------------- driver power (D-29c)
def test_drv_power_loss_vectors() -> None:
    by = {v["name"]: v["expect"] for v in CHECK["vectors"]}
    assert by["move_after_drv_power_lost"]["detail_names"] == ["NOT_ENABLED", "NOT_HOMED", "DRV_UNPOWERED"]
    assert by["jog_after_drv_power_lost"]["detail_names"] == ["NOT_ENABLED", "DRV_UNPOWERED"]
    assert by["enable_after_drv_power_return"]["status"] == "OK"
    assert by["move_after_drv_power_return"]["detail_names"] == ["NOT_HOMED"]
    assert by["move_k1_welded"]["detail_names"] == ["ESTOP", "FAULT", "NOT_ENABLED", "NOT_HOMED"]


def test_status_and_event_vectors_v02() -> None:
    by = {f["name"]: f for f in FRAMES}
    assert by["get_status_resp_paused_pc"]["decoded"]["board_status"]["pause_src"] == "PC"
    assert by["get_status_resp_latched"]["decoded"]["board_status"]["pause_src"] == "BUTTON"
    assert by["get_status_resp"]["len"] == 1 + 86
    st_pl = bytes.fromhex(by["get_status_resp_paused_pc"]["payload_hex"])
    assert st_pl[1 + 84] == PROTO.codes("source")["PC"] and st_pl[1 + 85] == 0
    k1 = by["event_fault_set_k1_welded"]["decoded"]
    assert (k1["code"], k1["arg"], k1["value"]) == ("FAULT_SET", 6, 200)
    assert by["event_pause_cleared"]["decoded"]["arg"] == 2          # only HALT_CLEAR (D-30)
    assert "move_abs_paused_nack" in by
    ev = by["event_paused_button"]["decoded"]
    assert (ev["code"], ev["arg"]) == ("PAUSED", 2)
    assert by["event_stopped_drv_power_lost"]["decoded"]["arg"] == 17


def test_status_reserved_byte_zero_on_encode() -> None:
    d: dict[str, Any] = next(f for f in FRAMES if f["name"] == "get_status_resp")["decoded"]["board_status"]
    pl = rc.encode_status(d)
    assert len(pl) == 86 and pl[85] == 0 and struct.unpack_from("<I", pl, 80)[0] == d["v_limit_um_s"]


# ---------------------------------------------------------------------------- ICD v0.4
UNITS = json.loads((VEC / "units_vectors.json").read_text(encoding="utf-8"))


def test_clears_are_verify_d34() -> None:
    """D-34: the clears are never auto-retried (VERIFY) but still use the SW priority lane."""
    for name in ("HALT_CLEAR", "ESTOP_CLEAR", "FAULT_CLEAR", "RESUME"):
        assert PG.CMD_RETRY[PG.Cmd[name]] == PG.RetryClass.VERIFY, name
    assert {PG.Cmd.HALT_CLEAR, PG.Cmd.ESTOP_CLEAR, PG.Cmd.FAULT_CLEAR} <= PG.CMD_PRIORITY
    assert all(c.retry != "ONCE_PRIORITY" for c in PROTO.commands)
    assert PG.RetryClass.ONCE_PRIORITY == 2                                  # code kept, unused


def test_resume_registry() -> None:
    """D-31: RESUME 0x3C, LEN 0, VERIFY (never auto-retried), not sniffed; PCLR_RESUME = 3."""
    c = next(c for c in PROTO.commands if c.name == "RESUME")
    assert (c.value, c.req_len, c.retry, c.priority, c.sniffed) == (0x3C, 0, "VERIFY", False, False)
    assert PG.Cmd.RESUME == 0x3C and PG.CMD_RETRY[PG.Cmd.RESUME] == PG.RetryClass.VERIFY
    assert PG.CMD_SNIFFED == {PG.Cmd.STOP, PG.Cmd.HALT, PG.Cmd.PAUSE}         # OI-FW-19
    assert PG.PauseClearedReason.RESUME == 3 and PG.PauseClearedReason.HALT_CLEAR == 2
    assert PG.InternalDetail.NOT_IN_BUILD == 1 and PG.InternalDetail.INVARIANT == 2   # OI-FW-21
    h = gen_protocol.FW_PROTO_H_PATH.read_text(encoding="utf-8")
    assert "INTERNAL_NOT_IN_BUILD" in h and "CMD_IS_SNIFFED" in h and "PROTO_HOME_RELEASE_MAX_UM" in h


@pytest.mark.parametrize("name,status,names,paused_after", [
    ("resume_paused", "OK", None, False),
    ("resume_not_paused", "OK", None, False),
    ("resume_halt", "E_STATE", ["HALT"], True),
    ("resume_halt_button_race", "E_STATE", ["HALT"], True),
    ("resume_estop_latched", "E_STATE", ["ESTOP"], True),
    ("resume_estop_input_open", "E_STATE", ["ESTOP"], True),
    ("resume_fault", "E_STATE", ["FAULT"], True),
    ("resume_all_latched", "E_STATE", ["ESTOP", "HALT", "FAULT"], True),
    ("resume_unpowered", "OK", None, False),
    ("resume_alarm", "OK", None, False),
    ("resume_limit_afe", "OK", None, False),
    ("resume_stopping", "OK", None, False),
    ("halt_clear_halt_and_paused", "OK", None, False),
    ("move_after_resume_halt_refused", "E_STATE", ["HALT", "PAUSED"], True)])
def test_resume_anchor(name: str, status: str, names: list[str] | None, paused_after: bool) -> None:
    """Independent anchors from D-31 / OBS-P1-15 (not derived from the model)."""
    e = next(x for x in CHECK["vectors"] if x["name"] == name)["expect"]
    assert e["status"] == status and e["paused_after"] is paused_after
    if names is not None:
        assert e["detail_names"] == names


@pytest.mark.parametrize("name,status,detail_key", [
    ("rule_h5_timeout_199_sps10", "E_CONFIG", "afe.rate_sps"),
    ("rule_h5_timeout_200_sps10", "OK", None),
    ("rule_h5_timeout_25_sps80", "OK", None),
    ("rule_h5_rate_sps10_default_timeout", "OK", None),
    ("rule_h5_rate_sps10_short_timeout", "E_CONFIG", "afe.timeout_ms"),
    ("rule_h5_rate_sps80_from_sps10", "OK", None)])
def test_h5_anchor(name: str, status: str, detail_key: str | None) -> None:
    """D-33a / DEF-P1-01: afe.timeout_ms >= 2 x conversion period."""
    e = next(x for x in CHECK["vectors"] if x["name"] == name)["expect"]
    keys = {p.key: p.id for p in PD.params}
    assert e["status"] == status
    if detail_key:
        assert e["detail"] == keys[detail_key]


def test_mul_bound_at_position() -> None:
    """F-B-28: MOVE_UNTIL_LOAD with bound = current position -> E_RANGE offset 0."""
    for n in ("mul_bound_at_position", "mul_bound_at_position_bad_cmp"):
        e = next(x for x in CHECK["vectors"] if x["name"] == n)["expect"]
        assert (e["status"], e["detail"]) == ("E_RANGE", 0)


def test_state_schema() -> None:
    """F-B-25: state keys versioned; v2 keys stable."""
    assert CHECK["state_schema"] == cc.STATE_SCHEMA == 2
    assert list(CHECK["state_defaults"])[-1] == "paused" and len(CHECK["state_defaults"]) == 22
    for v in CHECK["vectors"]:
        assert set(v["state"]) <= set(CHECK["state_defaults"]), v["name"]


def test_units_vectors_r4_anchor() -> None:
    """OI-FW-20 / SYS-003: R4 TV-TC um_to_steps anchors hold with the binary32 spm; ties present."""
    spm = struct.unpack("<f", struct.pack("<f", 636.0778443113772))[0]
    u2s = {(c["spm"], c["um"]): c["steps"] for c in UNITS["um_to_steps"]}
    s2u = {(c["spm"], c["steps"]): c["um"] for c in UNITS["steps_to_um"]}
    assert [u2s[(spm, u)] for u in (12345, -500, 100)] == [7852, -318, 64]
    assert [s2u[(spm, k)] for k in (7852, -318, 64)] == [12344, -500, 101]
    assert (u2s[(100.0, 5)], u2s[(100.0, -5)], u2s[(100.0, 15)]) == (1, -1, 2)         # half away
    assert (s2u[(160.0, 2)], s2u[(160.0, -2)], s2u[(160.0, 1)]) == (13, -13, 6)
    cap = {(c["spm"], c["max_step_rate_hz"]): c["rate_cap_um_s"] for c in UNITS["rate_cap"]}
    assert cap[(800.0, 50000)] == 62500 and cap[(160.0, 50000)] == 312500
    for c in UNITS["um_to_steps"]:
        assert f"0x{struct.unpack('<I', struct.pack('<f', c['spm']))[0]:08X}" == c["spm_f32_hex"]


def test_new_frames_v04() -> None:
    by = {f["name"]: f for f in FRAMES}
    assert by["resume_req"]["frame_hex"].startswith("A55A3C")
    assert by["resume_state_nack"]["decoded"] == {"status": "E_STATE", "detail": 2}
    assert by["event_pause_cleared_resume"]["decoded"]["arg"] == 3
    b = by["event_boot_hardfault"]["decoded"]
    assert (b["code"], b["value"], b["value2"]) == ("BOOT", 0x08001A2C, 0x8200)
    assert by["move_until_load_not_in_build_nack"]["decoded"] == {"status": "E_INTERNAL", "detail": 1}
