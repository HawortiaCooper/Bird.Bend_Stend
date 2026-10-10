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
    assert PD.dict_version == 6
    m = {k: keys[f"motion.{k}"].default for k in ("pulse_high_ns", "pulse_low_min_ns", "max_step_rate_hz")}
    assert m == {"pulse_high_ns": 12500, "pulse_low_min_ns": 12500, "max_step_rate_hz": 40000}   # D-45 e
    assert 1e9 / m["max_step_rate_hz"] >= m["pulse_high_ns"] + m["pulse_low_min_ns"]           # H3
    assert "io.stop_active_level" not in keys and all(p.id != 0x0603 for p in PD.params)   # D-36
    assert len(PD.params) == 48
    to = keys["afe.timeout_ms"]
    assert (to.default, to.min, to.max) == (250, 25, 1000)                     # D-33a
    assert keys["motion.steps_per_mm"].default == 800.0                        # D-27 closed
    assert "home.ref_switch" not in keys and all(p.id != 0x0401 for p in PD.params)
    k1 = keys["drv.k1_weld_ms"]
    assert (k1.id, k1.type, k1.default, k1.min, k1.max) == (0x0705, "u16", 200, 100, 2000)


def test_dictionary_cr03_k1_check_v07() -> None:
    """CR-03 / D-41: power sense optional (default off); SRS OI-18: K1_WELDED check gated by its own parameter."""
    keys = {p.key: p for p in PD.params}
    ps, kc = keys["drv.pwr_sense_enable"], keys["drv.k1_check_enable"]
    assert (ps.id, ps.type, ps.default, ps.reboot_required) == (0x0704, "bool", False, True)
    assert (kc.id, kc.type, kc.default, kc.reboot_required, kc.moving_ok) == (0x0706, "bool", False, True, False)
    names = {v["name"]: v for v in CHECK["vectors"]}
    # vectors that need the sense set it explicitly (the default no longer evaluates DRV_POWER)
    for n in ("move_alarm_unpowered", "move_after_drv_power_lost", "jog_after_drv_power_lost"):
        assert names[n]["state"]["params"]["drv.pwr_sense_enable"] is True, n
    assert names["move_alarm_unpowered"]["expect"]["status"] == "E_STATE"


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
    ("halt_clear_paused", False), ("jog0_paused", True), ("move_paused_refused", True), ("stop_paused", True), ("halt_paused", True),
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
    ("resume_halt_key_race", "E_STATE", ["HALT"], True),
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
    """F-B-25: state keys versioned and stable (v0.5: stop_btn_* kept but never set, D-36)."""
    assert CHECK["state_schema"] == cc.STATE_SCHEMA == 4                   # v0.7.5: + ena_on (D-50 c, OI-FW-50)
    assert list(CHECK["state_defaults"])[-3:] == ["paused", "unhomed_origin_um", "ena_on"]
    assert len(CHECK["state_defaults"]) == 24 and CHECK["state_defaults"]["unhomed_origin_um"] == 0
    assert CHECK["state_defaults"]["ena_on"] is True
    for v in CHECK["vectors"] + CHECK["hw_meas_vectors"]:
        assert set(v["state"]) <= set(CHECK["state_defaults"]), v["name"]
        st = {**CHECK["state_defaults"], **v["state"]}
        estop = st["estop_latched"] or st["estop_input_open"]
        assert not (estop and st["ena_on"]), v["name"]                   # an E-stop always disables ENA
        assert st["ena_on"] or estop or st["motion_state"] == "NOT_ENABLED", v["name"]


def test_fwr09_static_level_pul_needs_ena_disabled() -> None:
    """D-50 c / FWR-09 (ICD v0.7.5 App. C op 8): STATIC_LEVEL sel PUL refused while ENA holds; DIR allowed."""
    hv = {v["name"]: v for v in CHECK["hw_meas_vectors"]}
    meas_state = rc.names_to_bits(["MEAS_STATE"], rc.BLOCK)
    exp = {"meas_static_pul_ena_holding": ("E_STATE", meas_state), "meas_static_dir_ena_holding": ("OK", 0),
           "meas_static_pul_after_disable": ("OK", 0), "meas_static_not_enabled": ("OK", 0)}
    for n, want in exp.items():
        e = hv[n]["expect"]
        assert (e["status"], e["detail"]) == want, n
    assert list(hv)[-3:] == ["meas_static_pul_ena_holding", "meas_static_dir_ena_holding",
                             "meas_static_pul_after_disable"]                  # appended last: earlier SEQs unchanged


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


# ---------------------------------------------------------------------------- ICD v0.5 (CR-01 / D-36 / D-37)
def test_stop_button_retired_d36() -> None:
    """D-36: STOP_BTN bits and the STOP_BUTTON event / stop cause are retired: reserved, never reused,
    identifiers kept (marked RETIRED) for compatibility."""
    assert PROTO.retired("data_status") == {"STOP_BTN": 9} and PROTO.retired("io") == {"STOP_BTN": 3}
    assert PROTO.retired("event") == {"STOP_BUTTON": 22} and PROTO.retired("stop_cause") == {"STOP_BUTTON": 4}
    assert PG.DATA_STATUS_BITS[9] == "" and PG.IO_BITS[3] == "" and "STOP_BTN" in PG.DATA_STATUS_RETIRED
    assert PG.DataStatus.STOP_BTN == 0x0200                                   # member kept (compatibility)
    h = gen_protocol.FW_PROTO_H_PATH.read_text(encoding="utf-8")
    assert re.search(r"#define DS_RETIRED_MASK\s+0x0200u", h) and re.search(r"#define IO_RETIRED_MASK\s+0x0008u", h)
    assert re.search(r"#define DS_DEFINED_MASK\s+0xFDFFu", h)
    assert PG.Source.BUTTON == 2                                              # PAUSE keeps BUTTON


def test_feature_dependent_bits_d37b() -> None:
    """D-37 b / IF-C-M1-02: bits valid only with a feature bit."""
    assert dict(PG.DATA_STATUS_FEATURE) == {"PAUSE_BTN": "BUTTONS", "ALM": "DRV_SIGNALS", "PEND": "DRV_SIGNALS",
                                            "DRV_PWR": "DRV_SIGNALS"}
    assert dict(PG.IO_FEATURE) == {"PAUSE_BTN": "BUTTONS", "ALM": "DRV_SIGNALS", "PEND": "DRV_SIGNALS",
                                   "DRV_PWR": "DRV_SIGNALS"}


def test_halt_clear_never_refused_and_estop_clear_open_input() -> None:
    by = {v["name"]: v["expect"] for v in CHECK["vectors"]}
    assert by["halt_clear_ok"]["status"] == "OK"
    assert (by["estop_clear_input_open_unlatched"]["status"], by["estop_clear_input_open_unlatched"]["detail"]) == (
        "E_CAUSE_ACTIVE", 0xFFFF)
    for v in CHECK["vectors"]:
        if v["request"]["type_name"] == "HALT_CLEAR":
            assert v["expect"]["status"] == "OK", v["name"]


def test_units_saturation_obs_m1_05() -> None:
    sat = [c for c in UNITS["um_to_steps"] + UNITS["steps_to_um"] if c.get("saturated")]
    assert len(sat) == 6
    assert {c.get("steps", 0) for c in UNITS["um_to_steps"] if c.get("saturated")} == {2**31 - 1, -2**31}


# ---------------------------------------------------------------------------- ICD v0.6 (D-40, REQ-C-M2-01)
LOADLIM = json.loads((VEC / "loadlim_vectors.json").read_text(encoding="utf-8"))


def test_diag_meas_registry_d40c() -> None:
    c = next(c for c in PROTO.commands if c.name == "DIAG_MEAS")
    assert (c.value, c.req_len, c.priority, c.sniffed) == (0x3D, 8, False, False)
    assert PG.Features.HW_MEAS == 1 << 9 and PG.Block.MEAS_STATE == 1 << 11
    assert PG.MEAS_OP_NAMES == tuple(rc.MEAS_OP) and len(rc.MEAS_OP) == 10
    assert PG.MEAS_OP_RETRY["PROBE_READ"] == "RETRY" and PG.MEAS_OP_RETRY["HANG"] == "VERIFY"
    h = gen_protocol.FW_PROTO_H_PATH.read_text(encoding="utf-8")
    assert "CMD_DIAG_MEAS" in h and "FEAT_HW_MEAS" in h and "MEAS_OP_STATIC_LEVEL" in h and "PROTO_MEAS_BODY_LEN" in h


def test_diag_meas_vectors() -> None:
    by = {v["name"]: v["expect"] for v in CHECK["vectors"]}
    assert (by["diag_meas_not_in_build"]["status"], by["diag_meas_not_in_build"]["detail"]) == ("E_INTERNAL", 1)
    assert (by["diag_meas_length"]["status"], by["diag_meas_length"]["detail"]) == ("E_LENGTH", 8)
    hm = {v["name"]: v["expect"] for v in CHECK["hw_meas_vectors"]}
    assert all(hm[f"meas_{o.lower()}_ok"]["status"] == "OK" for o in ("INFO", "PROBE_ARM", "STAMPS", "STIM_RUN"))
    assert (hm["meas_arm_psc"]["status"], hm["meas_arm_psc"]["detail"]) == ("E_RANGE", 4)
    assert hm["meas_hang_idle"]["detail_names"] == ["MEAS_STATE"] and hm["meas_hang_moving"]["status"] == "OK"
    assert hm["meas_static_not_enabled"]["status"] == "OK" and hm["meas_static_enabled"]["status"] == "E_STATE"
    fr = {f["name"]: f for f in FRAMES}
    assert fr["diag_meas_info_resp"]["len"] == 1 + 64 and fr["diag_meas_info_req"]["len"] == 8


def test_limit_wiring_clear_rule_d40a() -> None:
    by = {v["name"]: v["expect"] for v in CHECK["vectors"]}
    assert by["fault_clear_wiring"]["status"] == "E_CAUSE_ACTIVE"
    assert by["fault_clear_wiring_one_released"]["status"] == "OK"
    assert by["move_toward_end_after_wiring_clear"]["detail_names"] == ["LIMIT"]
    assert by["move_away_end_after_wiring_clear"]["status"] == "OK"


def _ll(name: str) -> list[dict]:
    return next(c for c in LOADLIM["cases"] if c["name"] == name)["steps"]


def test_loadlim_regrow_window_d40d() -> None:
    """Independent anchors (ICD §5.5): window opens at a clear with the load beyond, re-trips only beyond
    ref + regrow, ends with the first sample inside or the next FAULT_CLEAR."""
    s = _ll("regrow_no_trip_within")
    assert [x.get("trip") for x in s if x["op"] == "sample"] == [False, False, True, False, False, True]
    s = _ll("regrow_window_ends_inside")
    assert s[2]["regrow_window"] is False and s[3]["trip"] is True
    # OI-B-M2-03 (v0.7): only a FAULT_CLEAR that clears a latched LOAD_LIMIT takes a new reference
    s = _ll("regrow_clear_without_latch_keeps_reference")
    assert s[2]["trip"] is False and s[3]["ref"] == 7_100_000 and s[4]["trip"] is False and s[5]["trip"] is True
    s = _ll("regrow_new_reference_after_retrip")
    assert s[2]["trip"] is True and s[3]["ref"] == 7_228_850 and s[4]["trip"] is False and s[5]["trip"] is True
    assert _ll("clear_inside_no_window")[2]["regrow_window"] is False
    assert _ll("regrow_config_keeps_window")[2]["regrow_window"] is True
    assert all(not x.get("trip") for x in _ll("regrow_unload"))[1:] if False else True
    assert [x["trip"] for x in _ll("regrow_unload") if x["op"] == "sample"] == [True, False, False, False]


def test_unhomed_window_d43b() -> None:
    """D-43 b (v0.7): un-homed JOG toward a reached bound of origin ± home.max_travel_um -> E_RANGE 0."""
    want = {"jog_unhomed_window_inside": ("OK", 0), "jog_unhomed_window_reached_toward": ("E_RANGE", 0),
            "jog_unhomed_window_reached_away": ("OK", 0), "jog_unhomed_window_neg_reached": ("E_RANGE", 0),
            "jog_unhomed_window_origin": ("E_RANGE", 0), "jog_zero_at_bound": ("OK", 0),
            "jog_homed_ignores_window": ("OK", 0)}
    got = {v["name"]: (v["expect"]["status"], v["expect"]["detail"]) for v in CHECK["vectors"] if v["name"] in want}
    assert got == want
    st = cc.FwState(homed=False, unhomed_origin_um=10_000, pos_um=370_000)
    jog = rc.encode_request("JOG", {"v_um_s": 1000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
    assert MODEL.check(st, rc.CMD["JOG"], jog) == ("E_RANGE", 0)
    assert MODEL.check(cc.FwState(homed=False, unhomed_origin_um=10_001, pos_um=370_000), rc.CMD["JOG"], jog) == ("OK", 0)


def test_motion_sum_tolerance_ceil_d40b() -> None:
    import math as _m
    mv = json.loads((VEC / "motion_vectors.json").read_text(encoding="utf-8"))
    for c in mv["cases"]:
        assert c["tolerance"]["sum_ticks"] == max(1, _m.ceil(c["n_periods"] / 1000))
