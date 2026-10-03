#!/usr/bin/env python3
"""Single generator of the shared protocol vectors (ICD_protocol.md v0.4.1 §12).

Implements: IF-003, IF-004, IF-006, IF-010, FW-CMD-001, FW-CFG-003, SAF-FW-020
Writes  00_System/tools/vectors/protocol_vectors.json   (CRC, frames, parser streams)
        00_System/tools/vectors/check_vectors.json      (state + command -> verdict)
        00_System/tools/vectors/units_vectors.json      (um <-> steps, step-rate speed cap; ICD §0.1)

Usage:
    python gen_vectors.py            # (re)generate both files
    python gen_vectors.py --check    # exit 1 if a file is out of date (CI)

Needs PyYAML (via gen_params.load()). Vectors are data: never hand-edit them; add scenarios
here. Validators add scenarios, not generators (R3 §3.4, pitfall P12).
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import gen_params  # noqa: E402
import ref_cmdcheck as cc  # noqa: E402
import ref_codec as rc  # noqa: E402

VEC_DIR = TOOLS_DIR / "vectors"
PROTO_PATH = VEC_DIR / "protocol_vectors.json"
CHECK_PATH = VEC_DIR / "check_vectors.json"
UNITS_PATH = VEC_DIR / "units_vectors.json"
R = rc.RESP_BIT


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


# ======================================================================================
# protocol vectors
# ======================================================================================
def make_protocol(pd: gen_params.Dictionary) -> dict[str, Any]:
    by_key = {p.key: p for p in pd.params}
    dict_hash = f"0x{pd.hash:08X}"
    frames: list[dict[str, Any]] = []

    def add(name: str, ftype: int, seq: int, decoded: dict[str, Any], desc: str,
            payload: bytes | None = None, **extra: Any) -> bytes:
        pl = rc.encode_decoded(ftype, decoded) if payload is None else payload
        raw = rc.encode_frame(ftype, seq, pl)
        if payload is None:      # canonical form: bit-name lists in bit order
            decoded = rc.decode_frame(rc.Frame(ftype, seq, pl))
        kind, tname = rc.classify(ftype)
        frames.append({"name": name, "description": desc,
                       "dir": "pc->fw" if kind == "request" else "fw->pc",
                       "kind": kind, "type": f"0x{ftype:02X}", "type_name": tname, "seq": seq,
                       "len": len(pl), "payload_hex": pl.hex().upper(),
                       "crc": f"0x{rc.crc16_ccitt(raw[2:-2]):04X}", "frame_hex": raw.hex().upper(),
                       "decoded": decoded, **extra})
        return raw

    def entry(key: str, value: Any | None = None) -> dict[str, Any]:
        p = by_key[key]
        v = p.default if value is None else value
        if p.type == "f32":
            v = _f32(v)
        return {"id": p.id, "type": p.type, "value": v}

    ok = {"status": "OK"}
    C = rc.CMD

    def req_resp(name: str, seq: int, req: dict[str, Any], resp: dict[str, Any], dreq: str,
                 dresp: str, tag: str = "") -> None:
        n = name.lower() + (f"_{tag}" if tag else "")
        add(f"{n}_req", C[name], seq, req, dreq)
        add(f"{n}_resp", C[name] | R, seq, resp, dresp)

    # --- system ------------------------------------------------------------------------
    raw_ping = rc.encode_frame(C["PING"], 0x07)
    req_resp("PING", 0x07, {}, dict(ok), "PING, empty payload (heartbeat)", "PING response: STATUS OK only")
    info = {"proto_major": rc.PROTO_MAJOR, "proto_minor": rc.PROTO_MINOR,
            "payload_version": rc.PAYLOAD_VERSION, "fw_version": [0, 1, 0],
            "param_dict_hash": dict_hash, "uid": "0123456789ABCDEF00112233",
            "build": "20261003-1200", "param_count": len(pd.params),
            "features": ["AFE", "MOTION", "HOMING", "MOVE_UNTIL_LOAD", "NVM", "BUTTONS",
                         "DRV_SIGNALS"]}
    req_resp("GET_INFO", 0x01, {}, {**ok, "info": info}, "GET_INFO request",
             "GET_INFO response (44-byte INFO), hash = current params.yaml")
    info_pl = b"\x00" + rc.encode_info(info)
    add("get_info_resp_longer", C["GET_INFO"] | R, 0x02, {**ok, "info": info},
        "INFO with 4 trailing bytes (future minor version): receivers ignore them",
        payload=info_pl + bytes.fromhex("DEADBEEF"), reencode=False,
        canonical_payload_hex=info_pl.hex().upper())
    board = {"uptime_ms": 123456, "t_us": 123456789, "flags": ["HOMED", "ENABLED"],
             "motion_state": "IDLE", "status": ["PEND", "DRV_PWR"], "faults": [],
             "io": ["PEND", "DRV_PWR", "RATE_80"], "home_phase": "DONE", "halt_src": "NONE",
             "reset_cause": "POWER_ON", "sys_flags": ["CFG_DIRTY", "STREAM_ON"],
             "pos_um": 100000, "target_um": 100000, "pos_steps": 80000, "afe_raw_last": -1234,
             "afe_rate_dsps": 801, "afe_reinit_count": 0, "rx_frames_ok": 1500,
             "rx_crc_errors": 2, "rx_frame_errors": 1, "rx_overruns": 0, "tx_drops": 0,
             "event_overflows": 0, "loop_max_us": 412, "link_age_ms": 35,
             "stack_free_min": 1840, "nvm_save_ms": 0, "idle_disable_left_s": 512,
             "nvm_record_seq": 37, "nvm_save_uptime_ms": 0, "v_limit_um_s": 30000,
             "pause_src": "NONE"}
    req_resp("GET_STATUS", 0x03, {}, {**ok, "board_status": board}, "GET_STATUS request",
             "GET_STATUS response (86-byte STATUS)")
    board2 = dict(board, flags=["ESTOP", "HALT", "FAULT"], motion_state="NOT_ENABLED",
                  status=["PAUSED", "LIMIT_START", "LOAD_LIMIT", "STOP_BTN"], pause_src="BUTTON",
                  faults=["LOAD_LIMIT", "K1_WELDED"], io=["ESTOP_OPEN", "STOP_BTN", "ENA_DISABLED",
                                                          "ALM", "DRV_PWR"],
                  home_phase="NONE", halt_src="BUTTON", reset_cause="IWDG",
                  sys_flags=["CLK_FALLBACK", "NVM_DEFAULTED"], afe_raw_last=7151200,
                  idle_disable_left_s=0xFFFF, pos_um=-1500, target_um=-1500, pos_steps=-1200)
    add("get_status_resp_latched", C["GET_STATUS"] | R, 0x04, {**ok, "board_status": board2},
        "STATUS with E-stop, HALT (button), PAUSED (button), faults LOAD_LIMIT + K1_WELDED, IWDG reset")
    board3 = dict(board, flags=["ENABLED"], status=["PAUSED", "PEND", "DRV_PWR"], pause_src="PC")
    add("get_status_resp_paused_pc", C["GET_STATUS"] | R, 0x06, {**ok, "board_status": board3},
        "STATUS PAUSED by the PC PAUSE command (pause_src PC, D-29a / GF-01), not homed")
    board4 = dict(board, flags=[], motion_state="NOT_ENABLED", status=["PEND"], io=["PEND", "RATE_80"],
                  idle_disable_left_s=0xFFFF)
    add("get_status_resp_drv_power_lost", C["GET_STATUS"] | R, 0x07, {**ok, "board_status": board4},
        "STATUS after driver power was lost with the E-stop closed (D-29c): NOT_ENABLED, HOMED "
        "cleared, DRV_PWR 0, no latch")
    req_resp("REBOOT", 0x05, {"magic": rc.REBOOT_MAGIC}, dict(ok), "REBOOT with magic 0xB007B007",
             "OK; FW resets <= 50 ms after the response")

    # --- parameters ----------------------------------------------------------------------
    pages = math.ceil(len(pd.params) / rc.PARAMS_PER_PAGE)
    for pg in range(pages):
        chunk = pd.params[pg * rc.PARAMS_PER_PAGE:(pg + 1) * rc.PARAMS_PER_PAGE]
        req_resp("GET_ALL_PARAMS", 0x10 + pg, {"page": pg},
                 {**ok, "page": pg, "page_count": pages, "entries": [entry(p.key) for p in chunk]},
                 f"GET_ALL_PARAMS page {pg}",
                 f"page {pg}/{pages} with all DEFAULT values, ascending id (conformance vector "
                 f"for the FW default table)", tag=f"p{pg}")
    add("get_all_params_page_range_nack", C["GET_ALL_PARAMS"] | R, 0x12,
        {"status": "E_RANGE", "detail": 0}, f"page >= page_count ({pages}) -> E_RANGE detail 0")
    req_resp("GET_PARAM", 0x13, {"id": by_key["motion.steps_per_mm"].id},
             {**ok, "entry": entry("motion.steps_per_mm")}, "GET_PARAM motion.steps_per_mm",
             "f32 800.0 = 0x44480000 (default, D-27)")
    set_cases = [("u8", "afe.settle_discard", 8), ("u16", "safety.link_timeout_ms", 1500),
                 ("u32", "motion.v_max_travel_um_s", 25000), ("i32", "safety.load_raw_min", -3000000),
                 ("f32", "motion.steps_per_mm", 636.0778), ("bool", "motion.dir_invert", 1),
                 ("enum", "afe.gain_channel", 2)]
    for i, (t, key, val) in enumerate(set_cases):
        req_resp("SET_PARAM", 0x20 + i, entry(key, val), {**ok, "entry": entry(key, val)},
                 f"SET_PARAM {key} = {val} ({t})", "value as stored echoed", tag=t)
    pid = by_key["safety.load_raw_max"].id
    add("set_param_range_req", C["SET_PARAM"], 0x28, entry("safety.load_raw_max", 7151122),
        "SET_PARAM safety.load_raw_max = 7 151 122 (> cap 7 151 121, SAF-FW-010)")
    add("set_param_range_nack", C["SET_PARAM"] | R, 0x28, {"status": "E_RANGE", "detail": pid},
        "E_RANGE, detail = parameter id; never clamped")
    pid = by_key["safety.link_timeout_ms"].id
    add("set_param_type_req", C["SET_PARAM"], 0x29, {"id": pid, "type": "u32", "value": 1000},
        "SET_PARAM safety.link_timeout_ms with type byte u32 (parameter is u16)")
    add("set_param_type_nack", C["SET_PARAM"] | R, 0x29, {"status": "E_TYPE", "detail": pid},
        "E_TYPE, detail = parameter id")
    add("set_param_padding_req", C["SET_PARAM"], 0x2A,
        {"id": pid, "type": "u16", "value": "INVALID_PADDING"},
        "u16 value 1000 with non-zero padding byte -> E_RANGE (decoders must reject it)",
        payload=struct.pack("<HB4s", pid, 3, b"\xE8\x03\x00\x01"))
    add("set_param_padding_nack", C["SET_PARAM"] | R, 0x2A, {"status": "E_RANGE", "detail": pid},
        "E_RANGE, detail = parameter id")
    add("set_param_id_req", C["SET_PARAM"], 0x2B, {"id": 0x0999, "type": "u8", "value": 1},
        "SET_PARAM unknown id 0x0999")
    add("set_param_id_nack", C["SET_PARAM"] | R, 0x2B, {"status": "E_PARAM_ID", "detail": 0x0999},
        "E_PARAM_ID, detail = id")
    add("set_param_busy_nack", C["SET_PARAM"] | R, 0x2C, {"status": "E_BUSY", "detail": 1},
        "SET_PARAM of a parameter without moving_ok while moving -> E_BUSY detail 1 (MOTION)")
    add("set_param_config_nack", C["SET_PARAM"] | R, 0x2D,
        {"status": "E_CONFIG", "detail": by_key["limits.soft_max_um"].id},
        "soft_min_um >= soft_max_um (hard rule H1) -> E_CONFIG, detail = id of the other parameter")
    for i, n in enumerate(("SAVE_PARAMS", "LOAD_PARAMS", "DEFAULT_PARAMS")):
        req_resp(n, 0x30 + i, {}, dict(ok), n, "OK (<= 2.5 s)")
    add("load_params_nvm_nack", C["LOAD_PARAMS"] | R, 0x33, {"status": "E_NVM", "detail": 1},
        "LOAD_PARAMS without a valid NVM record -> E_NVM 1, RAM unchanged")

    # --- streaming ---------------------------------------------------------------------
    req_resp("STREAM_START", 0x40, {}, dict(ok), "STREAM_START (one DATA frame per conversion)", "OK")
    req_resp("STREAM_STOP", 0x41, {}, dict(ok), "STREAM_STOP", "OK")
    req_resp("SET_VALID", 0x42, {"valid": 1}, {**ok, "t_us": 7_000_123}, "SET_VALID 1",
             "t_us from which DATA frames (by their t_us) carry VALID = 1")
    req_resp("SET_VALID", 0x43, {"valid": 0}, {**ok, "t_us": 4_294_967_000}, "SET_VALID 0",
             "t_us close to the 32-bit wrap", tag="clear")

    # --- motion / stop -------------------------------------------------------------------
    req_resp("ENABLE", 0x50, {}, {**ok, "settle_ms": 500}, "ENABLE",
             "OK, ENABLED after 500 ms (motion.ena_settle_ms)")
    req_resp("DISABLE", 0x51, {}, dict(ok), "DISABLE (operator confirmed unloaded)", "OK")
    req_resp("HOME", 0x52, {"flags": 0}, dict(ok), "HOME, no load confirmation", "OK, homing started")
    req_resp("HOME", 0x53, {"flags": 1}, dict(ok), "HOME with operator-confirmed-load flag",
             "OK", tag="confirmed")
    req_resp("MOVE_ABS", 0x54, {"target_um": 110000, "v_um_s": 5000, "a_um_s2": 0}, dict(ok),
             "MOVE_ABS to 110.000 mm at 5 mm/s, accel 0 = motion.a_max_um_s2", "OK, move started")
    req_resp("MOVE_ABS", 0x55, {"target_um": -2500, "v_um_s": 1, "a_um_s2": 10_000_000}, dict(ok),
             "MOVE_ABS negative target, minimum speed, large accel (field encoding)", "OK", tag="neg")
    req_resp("JOG", 0x56, {"v_um_s": 2000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}, dict(ok),
             "JOG +2 mm/s, no bound (runs to the soft limit)", "OK", tag="pos")
    req_resp("JOG", 0x57, {"v_um_s": -10000, "a_um_s2": 50000, "bound_um": 20000}, dict(ok),
             "JOG -10 mm/s with bound 20.000 mm (SW travel limit, FW stops exactly there)",
             "OK", tag="neg")
    req_resp("JOG", 0x58, {"v_um_s": 0, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}, dict(ok), "JOG 0 = controlled stop of a jog",
             "OK", tag="zero")
    req_resp("MOVE_UNTIL_LOAD", 0x59,
             {"bound_um": 150000, "v_um_s": 500, "a_um_s2": 0, "raw_stop": 1288490, "cmp": 0},
             dict(ok), "MOVE_UNTIL_LOAD toward 150 mm until raw >= 1 288 490 (cmp 0)", "OK")
    req_resp("MOVE_UNTIL_LOAD", 0x5A,
             {"bound_um": 50000, "v_um_s": 200, "a_um_s2": 20000, "raw_stop": -644245, "cmp": 1},
             dict(ok), "MOVE_UNTIL_LOAD toward 50 mm until raw <= -644 245 (cmp 1)", "OK", tag="le")
    req_resp("STOP", 0x5B, {"mode": 0}, dict(ok), "STOP immediate (GUI STOP)", "OK", tag="immediate")
    req_resp("STOP", 0x5C, {"mode": 1}, dict(ok), "STOP controlled", "OK", tag="controlled")
    req_resp("HALT", 0x5D, {}, dict(ok), "HALT (Pause/Break key)", "OK, HALT latched (src PC)")
    req_resp("HALT_CLEAR", 0x5E, {}, dict(ok), "HALT_CLEAR", "OK")
    req_resp("ESTOP_CLEAR", 0x5F, {}, dict(ok), "ESTOP_CLEAR", "OK (driver stays disabled, not homed)")
    req_resp("FAULT_CLEAR", 0x60, {}, {**ok, "cleared": ["LOAD_LIMIT", "STEP_FAULT"]},
             "FAULT_CLEAR", "OK, body = mask of cleared faults")
    req_resp("PAUSE", 0x61, {}, dict(ok), "PAUSE (GUI Pause): controlled stop + PAUSED latch",
             "OK; PAUSED blocks motion until RESUME or HALT_CLEAR (D-30, D-31)")
    req_resp("RESUME", 0x62, {}, dict(ok), "RESUME (GUI Resume, D-31): clears only PAUSED",
             "OK; PAUSED cleared, no motion starts (the SW re-issues the absolute target)")
    add("resume_state_nack", C["RESUME"] | R, 0x63,
        {"status": "E_STATE", "detail": rc.names_to_bits(["HALT"], rc.BLOCK)},
        "RESUME refused: HALT latched (e.g. STOP button pressed just before) -> E_STATE HALT, PAUSED "
        "and HALT both stay (D-31)", detail_names=["HALT"])

    # --- NACK forms ----------------------------------------------------------------------
    add("unknown_cmd_req", 0x3F, 0x70, {"raw_hex": ""}, "undefined command TYPE 0x3F")
    add("unknown_cmd_nack", 0xBF, 0x70, {"status": "E_UNKNOWN_CMD", "detail": 0x3F},
        "E_UNKNOWN_CMD, detail = TYPE")
    add("move_abs_length_req", C["MOVE_ABS"], 0x71, {}, "MOVE_ABS with 8 instead of 12 bytes",
        payload=bytes(8), framing_only=True)
    add("move_abs_length_nack", C["MOVE_ABS"] | R, 0x71, {"status": "E_LENGTH", "detail": 12},
        "E_LENGTH, detail = expected LEN")
    add("move_abs_range_nack", C["MOVE_ABS"] | R, 0x72, {"status": "E_RANGE", "detail": 4},
        "E_RANGE, detail = byte offset of the offending field (4 = v_um_s)")
    mask = rc.names_to_bits(["NOT_ENABLED", "NOT_HOMED"], rc.BLOCK)
    add("move_abs_state_nack", C["MOVE_ABS"] | R, 0x73, {"status": "E_STATE", "detail": mask},
        "E_STATE, detail = BLOCK mask NOT_ENABLED | NOT_HOMED", detail_names=["NOT_ENABLED", "NOT_HOMED"])
    pmask = rc.names_to_bits(["PAUSED"], rc.BLOCK)
    add("move_abs_paused_nack", C["MOVE_ABS"] | R, 0x79, {"status": "E_STATE", "detail": pmask},
        "E_STATE, detail = BLOCK PAUSED: motion refused while PAUSED (D-30)", detail_names=["PAUSED"])
    add("move_abs_busy_nack", C["MOVE_ABS"] | R, 0x74, {"status": "E_BUSY", "detail": 1},
        "E_BUSY detail 1: another move is executing")
    add("estop_clear_cause_nack", C["ESTOP_CLEAR"] | R, 0x75,
        {"status": "E_CAUSE_ACTIVE", "detail": 0xFFFF}, "E_CAUSE_ACTIVE: E-stop sense input still open")
    add("fault_clear_cause_nack", C["FAULT_CLEAR"] | R, 0x76,
        {"status": "E_CAUSE_ACTIVE", "detail": rc.names_to_bits(["LIMIT_WIRING"], rc.FAULTS)},
        "E_CAUSE_ACTIVE: LIMIT_WIRING cause (both switches) still present", detail_names=["LIMIT_WIRING"])
    add("home_confirm_nack", C["HOME"] | R, 0x77, {"status": "E_CONFIRM", "detail": 0},
        "E_CONFIRM: load above home.max_load_raw and no confirmed flag")
    add("ping_internal_nack", C["PING"] | R, 0x78, {"status": "E_INTERNAL", "detail": 0x1234},
        "E_INTERNAL with implementation-defined detail (NACK form test)")
    add("move_until_load_not_in_build_nack", C["MOVE_UNTIL_LOAD"] | R, 0x7A,
        {"status": "E_INTERNAL", "detail": 1},
        "E_INTERNAL detail 1 NOT_IN_BUILD: command of a later milestone, feature bit 0 (OI-FW-21)")

    # --- DATA ----------------------------------------------------------------------------
    def data(**kw: Any) -> dict[str, Any]:
        d = {"t_us": 12_500_000, "payload_version": 1, "flags": [], "afe_raw": 0,
             "setpoint_um": 0, "frame_seq": 0, "status": []}
        d.update(kw)
        return d
    d_typ = data(t_us=12_512_500, flags=["VALID", "MOVING", "HOMED", "ENABLED"], afe_raw=322123,
                 setpoint_um=104_375, frame_seq=1001, status=["DRV_PWR"])
    data_raw = add("data_typical", rc.ASYNC["DATA"], 1001 & 0xFF, d_typ,
                   "moving, VALID, homed; header SEQ = frame_seq & 0xFF")
    add("data_idle", rc.ASYNC["DATA"], 0x00, data(flags=["HOMED", "ENABLED"], afe_raw=-1234,
        setpoint_um=100000, frame_seq=0x0100, status=["PEND", "DRV_PWR"]),
        "idle, in position (PEND), unloaded")
    add("data_fallback", rc.ASYNC["DATA"], 0x05, data(t_us=20_000_000, flags=["HOMED", "ENABLED"],
        afe_raw=rc.AFE_NO_DATA, setpoint_um=100000, frame_seq=5,
        status=["AFE_STALE", "NO_AFE_DATA", "DRV_PWR"]),
        "fallback frame while the AFE is stale: afe_raw = 0x80000000, NO_AFE_DATA (FW-STR-005)")
    add("data_saturated_pos", rc.ASYNC["DATA"], 0x06, data(flags=["HOMED", "ENABLED", "FAULT"],
        afe_raw=rc.RAW_MAX, setpoint_um=200000, frame_seq=6,
        status=["LOAD_LIMIT", "AFE_SATURATED", "DRV_PWR"]),
        "positive rail 0x7FFFFF -> saturated, load limit tripped")
    add("data_saturated_neg", rc.ASYNC["DATA"], 0x07, data(flags=["HOMED", "ENABLED", "FAULT"],
        afe_raw=rc.RAW_MIN, setpoint_um=200000, frame_seq=7,
        status=["LOAD_LIMIT", "AFE_SATURATED", "DRV_PWR"]), "negative rail -8 388 608")
    d_sync = data(t_us=0x00A55A00, flags=["VALID", "HOMED", "ENABLED"], afe_raw=0x005AA510,
                  setpoint_um=0x5AA5, frame_seq=0x5AA5, status=["DRV_PWR"])
    data_sync_raw = add("data_sync_in_payload", rc.ASYNC["DATA"], 0xA5, d_sync,
                        "payload and SEQ contain A5 5A patterns (parser must not resync inside)")
    add("data_all_bits", rc.ASYNC["DATA"], 0xFF,
        data(t_us=0xFFFFFFFF, flags=list(rc.DATA_FLAGS), afe_raw=-1, setpoint_um=-0x80000000,
             frame_seq=0xFFFF, status=[n for n in rc.DATA_STATUS if n]),
        "every flag and status bit set, t_us and frame_seq at their maximum")
    add("data_wrap", rc.ASYNC["DATA"], 0x00, data(t_us=200, flags=["HOMED", "ENABLED", "OVERRUN"],
        afe_raw=5, setpoint_um=0, frame_seq=0, status=["DRV_PWR"]),
        "after t_us and frame_seq wrapped; OVERRUN = a frame was dropped in the FW before this one")
    add("data_estop", rc.ASYNC["DATA"], 0x09, data(flags=["ESTOP"], afe_raw=1000, setpoint_um=123456,
        frame_seq=9, status=["POS_UNCERTAIN", "ALM"]),
        "E-stop: not enabled, not homed, VALID cleared, driver unpowered (DRV_PWR 0, ALM)")

    # --- EVENT ---------------------------------------------------------------------------
    ev_args = {
        "BOOT": (rc.RESET_CAUSE.index("POWER_ON"), 0), "STOPPED": (rc.STOP_CAUSE["LIMIT_END"], 289_950, 231_960),
        "MOVE_DONE": (rc.MOVE_DONE_REASON.index("TARGET"), 110_000, 88_000),
        "ESTOP_SET": (0, 104_375, 83_500), "ESTOP_CLEARED": (0, 0), "HALT_SET": (rc.HALT_SRC.index("BUTTON"), 0),
        "HALT_CLEARED": (0, 0), "PAUSED": (rc.HALT_SRC.index("PC"), 0), "PAUSE_CLEARED": (2, 0),
        "RESUME_REQUEST": (0, 0), "FAULT_SET": (rc.FAULTS.index("LOAD_LIMIT"), 7_022_272, 80_000),
        "FAULT_CLEARED": (rc.names_to_bits(["LOAD_LIMIT"], rc.FAULTS), 0), "LIMIT_SET": (1, 290_400, 232_320),
        "LIMIT_CLEARED": (1, 0), "LINK_WDG": (0, 0), "LINK_RESTORED": (0, 0),
        "VALID_CLEARED": (rc.STOP_CAUSE["PC_STOP"], 0), "HOMED": (0, 12),
        "HOME_FAILED": (1, -360_000, -288_000), "DRIVER_ENABLED": (0, 0), "DRIVER_DISABLED": (2, 0),
        "STOP_BUTTON": (1, 0), "PAUSE_BUTTON": (1, 0), "ALM_CHANGED": (1, 0), "AFE_REINIT": (3, 0),
        "AFE_RATE_MISMATCH": (1, 100), "AFE_STALE": (1, 0), "PARAMS_SAVED": (0, 38),
        "PARAMS_LOADED": (0, 0), "PARAMS_DEFAULTED": (1, 0), "NVM_ERROR": (2, 0),
        "CLK_FALLBACK": (0, 0), "DRIVER_POWER": (0, 0), "NOT_SETTLED": (0, 200),
    }
    assert set(ev_args) == set(rc.EVENT)
    for i, (code, a) in enumerate(sorted(ev_args.items(), key=lambda kv: rc.EVENT[kv[0]])):
        arg, val, val2 = (a + (0,))[:3]
        add(f"event_{code.lower()}", rc.ASYNC["EVENT"], i, {"t_us": 1_000_000 + 1000 * i,
            "code": code, "arg": arg, "value": val, "value2": val2}, f"EVENT {code} (ICD §8.1)")

    extra_events = [
        ("event_paused_button", "PAUSED", rc.SOURCE.index("BUTTON"), 0, 0,
         "EVENT PAUSED by the physical button (arg 2 = BUTTON; GF-01)"),
        ("event_fault_set_k1_welded", "FAULT_SET", rc.FAULTS.index("K1_WELDED"), 200, 0,
         "EVENT FAULT_SET K1_WELDED: E-stop open while driver power stayed present for 200 ms "
         "(drv.k1_weld_ms; value = ms, D-29c)"),
        ("event_stopped_drv_power_lost", "STOPPED", rc.STOP_CAUSE["DRV_POWER_LOST"], 150_000, 120_000,
         "EVENT STOPPED cause DRV_POWER_LOST: driver power lost with the E-stop closed (D-29c)"),
        ("event_driver_disabled_drv_power_lost", "DRIVER_DISABLED", 4, 0, 0,
         "EVENT DRIVER_DISABLED cause 4 DRV_POWER_LOST (HOMED cleared, D-29c)"),
        ("event_driver_power_lost", "DRIVER_POWER", 0, 0, 0, "EVENT DRIVER_POWER 0 = lost"),
        ("event_pause_cleared_resume", "PAUSE_CLEARED", 3, 0, 0, "EVENT PAUSE_CLEARED by RESUME (arg 3, D-31)"),
        ("event_boot_hardfault", "BOOT", rc.RESET_CAUSE.index("SOFTWARE"), 0x0800_1A2C, 0x0000_8200,
         "EVENT BOOT after a HardFault reset: value = faulting PC 0x08001A2C, value2 = CFSR 0x00008200 "
         "(OI-FW-21)"),
    ]
    for j, (name, code, arg, val, val2, desc) in enumerate(extra_events):
        add(name, rc.ASYNC["EVENT"], 0x40 + j, {"t_us": 2_000_000 + 1000 * j, "code": code, "arg": arg,
            "value": val, "value2": val2}, desc)

    # --- framing-only / invalid ----------------------------------------------------------
    add("max_len_frame", C["GET_ALL_PARAMS"] | R, 0x80, {}, f"LEN = {rc.MAX_LEN} (maximum) frame-layer test",
        payload=bytes(range(rc.MAX_LEN)), framing_only=True)
    bad = bytearray(data_raw)
    bad[-1] ^= 0xFF
    frames.append({"name": "data_bad_crc", "description":
                   "data_typical with the CRC high byte inverted -> dropped, crc_errors += 1",
                   "dir": "fw->pc", "kind": "invalid", "frame_hex": bytes(bad).hex().upper(),
                   "expect": {"frames": [], "crc_errors": 1}})

    # --- parser streams ----------------------------------------------------------------
    ping_resp = rc.encode_frame(C["PING"] | R, 0x07, b"\x00")
    bad_ping = bytearray(ping_resp)
    bad_ping[-2] ^= 0x01
    corrupted_sync = bytearray(data_sync_raw)
    corrupted_sync[-4] ^= 0x10          # payload corruption behind the embedded syncs
    get_param_resp = bytes.fromhex(next(f for f in frames if f["name"] == "get_param_resp")["frame_hex"])
    streams_in: list[tuple[str, str, list[bytes], bool]] = [
        ("bad_crc_then_good", "bad-CRC frame immediately followed by a good one",
         [bytes(bad_ping) + ping_resp], False),
        ("garbage_prefix", "noise incl. lone A5 and A5 A5 before a frame",
         [bytes.fromhex("00FFA513A5A57E") + ping_resp], False),
        ("split_chunks", "one DATA frame delivered in 3 chunks (1 / 5 / rest)",
         [data_raw[:1], data_raw[1:6], data_raw[6:]], False),
        ("len_too_large", "header with LEN = 0xFFFF then a good frame: len_errors = 1",
         [bytes.fromhex("A55AC000FFFF") + ping_resp], False),
        ("len_161_header", "header with LEN = 161 (one above the maximum) then a good frame",
         [bytes.fromhex("A55AC000A100") + ping_resp], False),
        ("sync_in_payload_good", "good DATA frame containing A5 5A, then PING response: both "
         "decoded, no errors", [data_sync_raw + ping_resp], False),
        ("sync_in_payload_corrupted", "corrupted DATA frame containing A5 5A, then PING response: "
         "the bad frame fails CRC, hunting resumes after its SYNC0, false syncs inside are "
         "rejected, the PING response is found", [bytes(corrupted_sync) + ping_resp], False),
        ("truncated_then_stream", "first 15 bytes of a DATA frame, then two complete DATA frames: "
         "the truncated candidate fails CRC, hunting resumes after its SYNC0, both following "
         "frames are recovered", [data_raw[:15] + data_raw + data_sync_raw], False),
        ("two_frames_then_split", "two complete frames in one chunk, then a third split across "
         "two chunks", [raw_ping + get_param_resp + data_raw[:10], data_raw[10:]], False),
        ("truncated_then_idle", "first 15 bytes of a DATA frame, then a PING response, then line "
         "idle >= 20 ms: nothing is emitted until the inter-byte timeout, then the candidate is "
         "dropped and the buffered PING response recovered", [data_raw[:15] + ping_resp], True),
        ("lone_sync0_at_end", "a frame followed by a lone A5 then idle: the A5 is kept until the "
         "timeout, then discarded without counting", [ping_resp + b"\xA5"], True),
    ]
    streams = []
    for name, desc, chunks, idle in streams_in:
        p = rc.FrameParser()
        got: list[rc.Frame] = []
        for c in chunks:
            got += p.feed(c)
        before = [g.raw.hex().upper() for g in got]
        if idle:
            got += p.idle_timeout()
        streams.append({"name": name, "description": desc,
                        "chunks_hex": [c.hex().upper() for c in chunks], "idle_timeout_at_end": idle,
                        "expect_frames_before_timeout": before if idle else None,
                        "expect_frames_hex": [g.raw.hex().upper() for g in got],
                        "expect_counters": p.counters()})

    crc = [
        {"name": "check_value", "input_ascii": "123456789", "input_hex": b"123456789".hex().upper(),
         "crc": f"0x{rc.crc16_ccitt(b'123456789'):04X}"},
        {"name": "empty", "input_hex": "", "crc": f"0x{rc.crc16_ccitt(b''):04X}"},
        {"name": "single_zero", "input_hex": "00", "crc": f"0x{rc.crc16_ccitt(bytes(1)):04X}"},
        {"name": "ping_req_body", "input_hex": "01070000",
         "crc": f"0x{rc.crc16_ccitt(bytes.fromhex('01070000')):04X}",
         "note": "TYPE SEQ LEN_lo LEN_hi of ping_req"},
    ]
    return {
        "_comment": ["GENERATED by 00_System/tools/gen_vectors.py - do not edit",
                     f"ICD_protocol.md v{rc.ICD_VERSION}; hex strings upper-case, no separators",
                     "bit fields (flags, status, faults, io, sys_flags, features, cleared): lists "
                     "of set bit names (ICD tables); enums (motion_state, ...) by name",
                     "afe_raw = -2147483648 (0x80000000) = no AFE data (fallback frame)",
                     "streams: feed chunks in order to a fresh parser (ICD §2.3); if "
                     "idle_timeout_at_end, then signal a >= 20 ms inter-byte timeout",
                     "reencode = false: decoding gives 'decoded'; encoding 'decoded' gives "
                     "canonical_payload_hex",
                     "framing_only = true: frame-layer test only, payload has no command semantics",
                     "decoded.value = 'INVALID_PADDING': decoders must reject the payload"],
        "icd_version": rc.ICD_VERSION, "proto_version": f"{rc.PROTO_MAJOR}.{rc.PROTO_MINOR}",
        "payload_version": rc.PAYLOAD_VERSION, "param_dict_hash": dict_hash,
        "param_count": len(pd.params), "params_per_page": rc.PARAMS_PER_PAGE,
        "max_len": rc.MAX_LEN, "crc16": crc, "frames": frames, "streams": streams,
    }


# ======================================================================================
# check vectors (state + command -> verdict)
# ======================================================================================
def make_check(pd: gen_params.Dictionary) -> dict[str, Any]:
    model = cc.Model(pd.params)
    vecs: list[dict[str, Any]] = []
    seq = [0]

    def add(name: str, desc: str, state: dict[str, Any], cmd: str, fields: dict[str, Any] | None = None,
            payload: bytes | None = None, ftype: int | None = None, srs: tuple[str, ...] = ()) -> None:
        t = rc.CMD[cmd] if ftype is None else ftype
        pl = payload if payload is not None else rc.encode_request(cmd, fields or {})
        st = cc.FwState.from_dict(state)
        status, detail = model.check(st, t, pl)
        s = seq[0] & 0xFF
        seq[0] += 1
        exp: dict[str, Any] = {"status": status, "detail": detail}
        if status == "E_STATE":
            exp["detail_names"] = rc.bits_to_names(detail, rc.BLOCK)
        if status == "E_CAUSE_ACTIVE" and cmd == "FAULT_CLEAR":
            exp["detail_names"] = rc.bits_to_names(detail, rc.FAULTS)
        if status != "OK":
            exp["response_frame_hex"] = rc.encode_frame(t | R, s, rc.encode_response(
                cmd, {"status": status, "detail": detail})).hex().upper()
        if st.paused or cmd in ("PAUSE", "RESUME"):
            exp["paused_after"] = model.paused_after(st, t, pl, status)
        vecs.append({"name": name, "description": desc, "srs": list(srs), "state": state,
                     "request": {"type": f"0x{t:02X}", "type_name": cmd, "seq": s,
                                 "payload_hex": pl.hex().upper(),
                                 "frame_hex": rc.encode_frame(t, s, pl).hex().upper()},
                     "expect": exp})

    mv = {"target_um": 110000, "v_um_s": 5000, "a_um_s2": 0}
    jog_nb = {"v_um_s": 2000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}
    moving = {"motion_state": "MOVE_ABS"}
    boot = {"motion_state": "NOT_ENABLED", "homed": False}

    # --- check order / framing-level ----------------------------------------------------
    add("unknown_type", "undefined TYPE 0x3E -> E_UNKNOWN_CMD", {}, "PING", payload=b"", ftype=0x3E,
        srs=("FW-CMD-001",))
    add("length_ping", "PING with 1 byte -> E_LENGTH 0", {}, "PING", payload=b"\x00", srs=("FW-CMD-001",))
    add("length_before_args", "MOVE_ABS with 11 bytes, also out of range -> E_LENGTH wins", boot,
        "MOVE_ABS", payload=bytes(11), srs=("FW-CMD-001",))
    add("args_before_state", "MOVE_ABS target above soft max while not enabled -> E_RANGE wins",
        boot, "MOVE_ABS", dict(mv, target_um=290001), srs=("FW-CMD-001", "SAF-FW-020"))
    for c in ("PING", "GET_INFO", "GET_STATUS", "STREAM_START", "STREAM_STOP", "HALT"):
        add(f"{c.lower()}_estop", f"{c} accepted while E-stop latched and not enabled",
            dict(boot, estop_latched=True, estop_input_open=True), c, srs=("FW-MOT-007",))
    add("stop_while_moving", "STOP immediate accepted while moving", moving, "STOP", {"mode": 0},
        srs=("FW-MOT-007",))
    add("stop_bad_mode", "STOP mode 2 -> E_RANGE offset 0", {}, "STOP", {"mode": 2})
    add("halt_while_halted", "HALT is idempotent", {"halt_latched": True}, "HALT", srs=("FW-MOT-007",))

    # --- motion gating: one per refusal reason (SAF-FW-020) ------------------------------
    S = ("SAF-FW-020",)
    add("move_ok", "MOVE_ABS in the ready state", {}, "MOVE_ABS", mv, srs=("FW-MOT-004",))
    add("move_target_below", "target below soft_min_um -> E_RANGE 0", {}, "MOVE_ABS",
        dict(mv, target_um=499), srs=S)
    add("move_target_above", "target above soft_max_um -> E_RANGE 0", {}, "MOVE_ABS",
        dict(mv, target_um=290001), srs=S)
    add("move_target_at_limits", "target = soft_max_um accepted", {}, "MOVE_ABS",
        dict(mv, target_um=290000), srs=S)
    add("move_speed_zero", "speed 0 -> E_RANGE 4", {}, "MOVE_ABS", dict(mv, v_um_s=0), srs=S)
    add("move_speed_travel_cap", "speed 30 001 um/s unloaded > v_max_travel -> E_RANGE 4", {},
        "MOVE_ABS", dict(mv, v_um_s=30001), srs=S)
    add("move_speed_travel_ok", "speed 30 000 um/s unloaded accepted", {}, "MOVE_ABS",
        dict(mv, v_um_s=30000), srs=S)
    add("move_speed_load_cap", "speed 20 001 um/s while loaded (5 % FS) > v_max_load -> E_RANGE 4",
        {"raw": 322123}, "MOVE_ABS", dict(mv, v_um_s=20001), srs=S)
    add("move_speed_steprate_cap", "steps_per_mm 10 000: 10 mm/s exceeds 50 kHz cap (5 mm/s) -> E_RANGE 4",
        {"params": {"motion.steps_per_mm": 10000.0}}, "MOVE_ABS", dict(mv, v_um_s=10000),
        srs=("FW-MOT-009",))
    add("move_accel_cap", "accel above a_max_um_s2 -> E_RANGE 8", {}, "MOVE_ABS",
        dict(mv, a_um_s2=100001), srs=S)
    add("move_not_homed", "MOVE_ABS not homed -> E_STATE NOT_HOMED", {"homed": False}, "MOVE_ABS", mv,
        srs=S + ("SAF-FW-006",))
    add("move_not_enabled", "MOVE_ABS after boot -> E_STATE NOT_ENABLED | NOT_HOMED", boot,
        "MOVE_ABS", mv, srs=S + ("SAF-FW-018",))
    add("move_estop", "E-stop latched", dict(boot, estop_latched=True), "MOVE_ABS", mv, srs=S)
    add("move_halt", "HALT latched", {"halt_latched": True}, "MOVE_ABS", mv, srs=S + ("FW-MOT-007",))
    add("move_fault", "fault latched (STEP_FAULT)", {"faults": ["STEP_FAULT"], "homed": False},
        "MOVE_ABS", mv, srs=S)
    add("move_toward_limit_end", "END limit latched, move toward it -> E_STATE LIMIT",
        {"limit_end": True}, "MOVE_ABS", mv, srs=("SAF-FW-013",))
    add("move_away_limit_end", "END limit latched, move away accepted", {"limit_end": True},
        "MOVE_ABS", dict(mv, target_um=90000), srs=("SAF-FW-013",))
    add("move_afe_stale", "AFE stale -> E_STATE AFE_STALE", {"afe_stale": True}, "MOVE_ABS",
        dict(mv, v_um_s=1000), srs=("SAF-FW-012",))
    add("move_afe_saturated", "last sample saturated -> E_STATE AFE_SATURATED",
        {"afe_saturated": True, "raw": 8388607}, "MOVE_ABS", dict(mv, v_um_s=1000), srs=("SAF-FW-012",))
    add("move_drv_unpowered", "driver power sense enabled and power off -> E_STATE DRV_UNPOWERED",
        {"params": {"drv.pwr_sense_enable": True}, "drv_power": False}, "MOVE_ABS", mv, srs=("R5 §1.5",))
    add("move_drv_sense_off", "power input off but sense disabled (bring-up) -> accepted",
        {"drv_power": False, "params": {"drv.pwr_sense_enable": False}}, "MOVE_ABS", mv,
        srs=("R5 §1.5",))
    add("move_alarm_powered", "ALM active with driver power present -> E_STATE DRIVER_ALARM (D-28)",
        {"alm_active": True}, "MOVE_ABS", mv, srs=("FW-SW-004", "D-28"))
    add("jog_alarm_powered", "JOG with ALM active and power present -> E_STATE DRIVER_ALARM (D-28)",
        {"alm_active": True}, "JOG", jog_nb, srs=("FW-SW-004", "D-28"))
    add("jog_refresh_alarm", "JOG speed refresh of a RUNNING jog with ALM active and power present -> "
        "OK (not a new motion start; SAF-FW-026)", {"motion_state": "JOG", "alm_active": True}, "JOG",
        dict(jog_nb, v_um_s=1500), srs=("SAF-FW-026", "D-28"))
    add("home_alarm_powered", "HOME with ALM active and power present -> E_STATE DRIVER_ALARM",
        {"homed": False, "alm_active": True}, "HOME", {"flags": 0}, srs=("SAF-FW-026", "D-28"))
    add("enable_alarm_powered", "ENABLE with ALM active and power present -> OK (ENABLE not blocked)",
        {"motion_state": "NOT_ENABLED", "homed": False, "alm_active": True}, "ENABLE",
        srs=("SAF-FW-026",))
    for c in ("STOP", "HALT", "PAUSE"):
        add(f"{c.lower()}_alarm_moving", f"{c} during a move with ALM active -> OK (never blocked)",
            dict(moving, alm_active=True), c, {"mode": 0} if c == "STOP" else None, srs=("SAF-FW-026",))
    add("move_alarm_unpowered", "ALM active because the driver is unpowered -> DRV_UNPOWERED only",
        {"alm_active": True, "drv_power": False}, "MOVE_ABS", mv, srs=("D-28",))
    add("move_alarm_sense_off", "ALM active, sense disabled (power assumed) -> DRIVER_ALARM",
        {"alm_active": True, "drv_power": False, "params": {"drv.pwr_sense_enable": False}},
        "MOVE_ABS", mv, srs=("D-28",))
    add("move_after_drv_power_lost", "driver power lost with the E-stop closed: NOT_ENABLED, "
        "HOMED cleared -> E_STATE NOT_ENABLED | NOT_HOMED | DRV_UNPOWERED (D-29c)",
        {"motion_state": "NOT_ENABLED", "homed": False, "drv_power": False}, "MOVE_ABS", mv,
        srs=("D-29c", "SAF-FW-005"))
    add("jog_after_drv_power_lost", "JOG after driver power loss -> E_STATE NOT_ENABLED | "
        "DRV_UNPOWERED (D-29c)", {"motion_state": "NOT_ENABLED", "homed": False, "drv_power": False},
        "JOG", jog_nb, srs=("D-29c",))
    add("enable_after_drv_power_return", "power back, axis not homed: ENABLE accepted (settle "
        "follows), motion still needs HOME (D-29c)", {"motion_state": "NOT_ENABLED", "homed": False},
        "ENABLE", srs=("D-29c", "FW-MOT-008"))
    add("move_after_drv_power_return", "power back and ENABLE done, not homed -> E_STATE NOT_HOMED",
        {"homed": False}, "MOVE_ABS", mv, srs=("D-29c",))
    add("move_k1_welded", "K1_WELDED latched (E-stop open, power present) -> E_STATE ESTOP | FAULT",
        {"motion_state": "NOT_ENABLED", "homed": False, "estop_latched": True, "estop_input_open": True,
         "faults": ["K1_WELDED"], "fault_causes": ["K1_WELDED"]}, "MOVE_ABS", mv, srs=("D-29c",))
    add("alarm_running_move", "JOG 0 during a running move with ALM active -> OK, no effect "
        "(running moves are unaffected by ALM; only new motion is refused)",
        dict(moving, alm_active=True), "JOG", {"v_um_s": 0, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}, srs=("D-28", "D-16"))
    add("move_enabling", "during ENA settle -> E_BUSY 2", {"motion_state": "ENABLING", "enabling_left_ms": 300},
        "MOVE_ABS", mv, srs=("FW-MOT-008",))
    add("move_while_moving", "different MOVE_ABS while moving -> E_BUSY 1", moving, "MOVE_ABS",
        dict(mv, target_um=120000), srs=("FW-MOT-004",))
    add("move_identical_while_moving", "identical MOVE_ABS while executing it -> E_BUSY 1 (motion "
        "commands are never retried, ICD §9.3)", moving, "MOVE_ABS", mv, srs=("IF-005",))
    add("move_while_stopping", "MOVE_ABS during a controlled stop -> E_BUSY 1",
        {"motion_state": "STOPPING"}, "MOVE_ABS", mv)
    # --- PAUSED (D-30, amends D-29a): blocks new motion incl. jog refreshes; only HALT_CLEAR clears it
    P = ("SAF-FW-023", "D-30")
    pz = {"paused": True}
    jog0 = {"v_um_s": 0, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}
    add("move_paused", "MOVE_ABS while PAUSED -> E_STATE PAUSED, PAUSED kept", pz, "MOVE_ABS", mv, srs=P)
    add("move_paused_refused", "MOVE_ABS while PAUSED and HALT latched -> E_STATE HALT | PAUSED",
        dict(pz, halt_latched=True), "MOVE_ABS", mv, srs=P)
    add("jog_paused", "JOG != 0 while PAUSED -> E_STATE PAUSED", pz, "JOG",
        {"v_um_s": 2000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}, srs=P)
    add("jog_refresh_paused", "JOG refresh in flight arriving while STOPPING after a PAUSE -> E_STATE "
        "PAUSED (E_STATE is checked before E_BUSY; closes OI-ICD-04)", dict(pz, motion_state="STOPPING"),
        "JOG", {"v_um_s": 2000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}, srs=P)
    add("jog_refresh_paused_jogging", "JOG refresh while still in state JOG with PAUSED set (clean-halt "
        "edge) -> E_STATE PAUSED", dict(pz, motion_state="JOG"), "JOG",
        {"v_um_s": 2000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}, srs=P)
    add("jog0_paused", "JOG 0 while PAUSED -> OK, PAUSED kept", pz, "JOG", jog0, srs=P)
    add("mul_paused", "MOVE_UNTIL_LOAD while PAUSED -> E_STATE PAUSED", pz, "MOVE_UNTIL_LOAD",
        {"bound_um": 150000, "v_um_s": 500, "a_um_s2": 0, "raw_stop": 1288490, "cmp": 0}, srs=P)
    add("home_paused", "HOME while PAUSED -> E_STATE PAUSED", dict(pz, homed=False), "HOME",
        {"flags": 0}, srs=P)
    add("halt_clear_paused", "HALT_CLEAR with nothing else latched clears PAUSED (Resume step 1)", pz,
        "HALT_CLEAR", srs=P)
    add("move_after_resume", "MOVE_ABS after HALT_CLEAR (PAUSED cleared) -> OK (Resume step 2)", {},
        "MOVE_ABS", mv, srs=P)
    add("halt_clear_paused_refused", "HALT_CLEAR refused (STOP button held) -> PAUSED kept",
        dict(pz, halt_latched=True, stop_btn_active=True), "HALT_CLEAR", srs=P)
    for c in ("STOP", "HALT", "ESTOP_CLEAR", "FAULT_CLEAR", "ENABLE", "DISABLE", "PING", "SAVE_PARAMS"):
        add(f"{c.lower()}_paused", f"{c} while PAUSED -> PAUSED kept", pz, c,
            {"mode": 1} if c == "STOP" else None, srs=P)
    add("pause_paused", "PAUSE while PAUSED -> OK, no event, source unchanged", pz, "PAUSE", srs=P)

    # --- RESUME (D-31; OBS-P1-15, SWD-P1-02): clears only PAUSED; refused while ESTOP/HALT/FAULT
    RS = ("SAF-FW-023", "D-31")
    add("resume_paused", "RESUME while PAUSED -> OK, PAUSED cleared (PAUSE_CLEARED arg 3)", pz, "RESUME", srs=RS)
    add("resume_not_paused", "RESUME with nothing paused -> OK, no-op, no event", {}, "RESUME", srs=RS)
    add("resume_halt", "RESUME while PAUSED and HALT latched (PC) -> E_STATE HALT, both stay", dict(pz,
        halt_latched=True), "RESUME", srs=RS)
    add("resume_halt_button_race", "STOP button latched HALT ms before the RESUME frame, button already "
        "released >= io.release_ms -> E_STATE HALT, HALT and PAUSED stay (F-B-30 closed)",
        dict(pz, halt_latched=True, stop_btn_released_ms=500), "RESUME", srs=RS)
    add("resume_estop_latched", "RESUME while PAUSED and ESTOP latched (input closed) -> E_STATE ESTOP",
        dict(pz, motion_state="NOT_ENABLED", homed=False, estop_latched=True), "RESUME", srs=RS)
    add("resume_estop_input_open", "RESUME while the E-stop sense input is open -> E_STATE ESTOP",
        dict(pz, motion_state="NOT_ENABLED", homed=False, estop_latched=True, estop_input_open=True),
        "RESUME", srs=RS)
    add("resume_fault", "RESUME while PAUSED and a FAULT latched (cause gone) -> E_STATE FAULT",
        dict(pz, faults=["LOAD_LIMIT"]), "RESUME", srs=RS)
    add("resume_all_latched", "RESUME with ESTOP, HALT and FAULT latched -> E_STATE ESTOP | HALT | FAULT",
        dict(pz, motion_state="NOT_ENABLED", homed=False, estop_latched=True, halt_latched=True,
             faults=["STEP_FAULT"]), "RESUME", srs=RS)
    add("resume_unpowered", "RESUME while PAUSED and driver power off -> OK (DRV_UNPOWERED not evaluated; "
        "the following motion command is refused)", dict(pz, motion_state="NOT_ENABLED", homed=False,
        drv_power=False), "RESUME", srs=RS)
    add("resume_alarm", "RESUME while PAUSED and ALM active -> OK (ALM start-block applies to motion only)",
        dict(pz, alm_active=True), "RESUME", srs=RS)
    add("resume_limit_afe", "RESUME while PAUSED, END limit latched and AFE stale -> OK (not evaluated)",
        dict(pz, limit_end=True, afe_stale=True), "RESUME", srs=RS)
    add("resume_stopping", "RESUME while PAUSED and still STOPPING after the PAUSE -> OK (never E_BUSY; "
        "the deceleration continues)", dict(pz, motion_state="STOPPING"), "RESUME", srs=RS)
    add("resume_length", "RESUME with 1 payload byte -> E_LENGTH 0", pz, "RESUME", payload=b"\x00", srs=RS)
    add("halt_clear_halt_and_paused", "HALT_CLEAR with HALT and PAUSED latched -> OK, both cleared (D-31)",
        dict(pz, halt_latched=True), "HALT_CLEAR", srs=RS)
    add("move_after_resume_halt_refused", "MOVE_ABS after a refused RESUME (HALT + PAUSED) -> E_STATE "
        "HALT | PAUSED", dict(pz, halt_latched=True), "MOVE_ABS", mv, srs=RS)

    jog = {"v_um_s": 2000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND}
    add("jog_unhomed_ok", "JOG un-homed at v_unhomed accepted", {"homed": False}, "JOG", jog,
        srs=("FW-MOT-005",))
    add("jog_unhomed_fast", "JOG un-homed above v_unhomed -> E_RANGE 0", {"homed": False}, "JOG",
        dict(jog, v_um_s=-2001), srs=S)
    add("jog_before_enable", "JOG after boot -> E_STATE NOT_ENABLED", boot, "JOG", jog,
        srs=("SAF-FW-018", "SAF-FW-006"))
    add("jog_toward_start", "START limit active, JOG negative -> E_STATE LIMIT", {"limit_start": True},
        "JOG", dict(jog, v_um_s=-2000), srs=("SAF-FW-013",))
    add("jog_away_start", "START limit active, JOG positive accepted", {"limit_start": True}, "JOG", jog,
        srs=("SAF-FW-013",))
    add("jog_speed_change", "JOG while jogging = speed change", {"motion_state": "JOG"},
        "JOG", dict(jog, v_um_s=5000), srs=("FW-MOT-005",))
    add("jog_zero_idle", "JOG 0 while idle -> OK no-op", {}, "JOG", {"v_um_s": 0, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
    add("jog_zero_estop", "JOG 0 never refused (even E-stop)", dict(boot, estop_latched=True), "JOG",
        {"v_um_s": 0, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
    add("jog_during_move", "JOG during MOVE_ABS -> E_BUSY 1", moving, "JOG", jog)
    add("jog_both_limits", "LIMIT_WIRING fault -> E_STATE FAULT",
        {"faults": ["LIMIT_WIRING"], "fault_causes": ["LIMIT_WIRING"], "limit_start": True,
         "limit_end": True}, "JOG", jog, srs=("SAF-FW-014",))
    add("jog_bound_ok", "JOG + with bound 150 mm ahead of 100 mm -> OK", {}, "JOG",
        dict(jog, bound_um=150000), srs=("FW-MOT-005",))
    add("jog_bound_behind", "JOG + with bound behind the axis -> E_RANGE 8", {}, "JOG",
        dict(jog, bound_um=90000), srs=("FW-MOT-005",))
    add("jog_bound_outside", "JOG - with bound below soft_min_um -> E_RANGE 8", {}, "JOG",
        dict(jog, v_um_s=-2000, bound_um=0), srs=("FW-MOT-005",))
    add("jog_bound_unhomed", "JOG with a bound while not homed -> E_STATE NOT_HOMED", {"homed": False},
        "JOG", dict(jog, bound_um=150000), srs=("FW-MOT-005",))

    mul = {"bound_um": 150000, "v_um_s": 500, "a_um_s2": 0, "raw_stop": 1288490, "cmp": 0}
    add("mul_ok", "MOVE_UNTIL_LOAD accepted", {}, "MOVE_UNTIL_LOAD", mul, srs=("FW-MOT-006",))
    add("mul_load_cap", "MOVE_UNTIL_LOAD above v_max_load even unloaded -> E_RANGE 4", {},
        "MOVE_UNTIL_LOAD", dict(mul, v_um_s=20001), srs=S)
    add("mul_raw_range", "raw_stop outside 24-bit -> E_RANGE 12", {}, "MOVE_UNTIL_LOAD",
        dict(mul, raw_stop=8388608), srs=S)
    add("mul_cmp", "cmp 2 -> E_RANGE 16", {}, "MOVE_UNTIL_LOAD", dict(mul, cmp=2), srs=S)
    add("mul_bound_at_position", "bound_um = current position (no direction) -> E_RANGE 0 (F-B-28)", {},
        "MOVE_UNTIL_LOAD", dict(mul, bound_um=100000), srs=("FW-MOT-006",))
    add("mul_bound_at_position_bad_cmp", "bound_um = position and cmp 2 -> E_RANGE 0 (payload order)", {},
        "MOVE_UNTIL_LOAD", dict(mul, bound_um=100000, cmp=2), srs=("FW-MOT-006",))
    add("mul_not_homed", "MOVE_UNTIL_LOAD not homed -> E_STATE NOT_HOMED", {"homed": False},
        "MOVE_UNTIL_LOAD", mul, srs=S)

    add("home_ok", "HOME unloaded", {"homed": False}, "HOME", {"flags": 0}, srs=("FW-HOM-001",))
    add("home_load_refused", "HOME at 6 % FS without confirmation -> E_CONFIRM",
        {"homed": False, "raw": 386547}, "HOME", {"flags": 0}, srs=("SAF-FW-021",))
    add("home_load_confirmed", "HOME at 6 % FS with confirmation -> OK", {"homed": False, "raw": 386547},
        "HOME", {"flags": 1}, srs=("SAF-FW-021",))
    add("home_load_at_limit", "HOME at exactly home.max_load_raw from zero_raw -> OK",
        {"homed": False, "raw": 422123, "params": {"safety.zero_raw": 100000}}, "HOME", {"flags": 0},
        srs=("SAF-FW-021",))
    add("home_bad_flags", "HOME flags bit 1 set -> E_RANGE 0", {}, "HOME", {"flags": 2})
    add("home_not_enabled", "HOME after ESTOP_CLEAR without ENABLE -> E_STATE NOT_ENABLED", boot,
        "HOME", {"flags": 0}, srs=("SAF-FW-006",))
    add("home_while_homing", "HOME while homing -> E_BUSY 1", {"motion_state": "HOMING", "homed": False},
        "HOME", {"flags": 0}, srs=("IF-005",))

    # --- enable / disable ------------------------------------------------------------------
    add("enable_boot", "ENABLE after boot", boot, "ENABLE", srs=("FW-MOT-008",))
    add("enable_halt", "ENABLE allowed while HALT latched (no motion)", dict(boot, halt_latched=True), "ENABLE")
    add("enable_estop_latched", "ENABLE while ESTOP latched -> E_STATE ESTOP", dict(boot, estop_latched=True),
        "ENABLE", srs=("FW-MOT-008",))
    add("enable_estop_input", "ENABLE with sense input open -> E_STATE ESTOP", dict(boot, estop_input_open=True,
        estop_latched=True), "ENABLE", srs=("FW-MOT-008",))
    add("enable_unpowered", "ENABLE with driver power off (sense enabled) -> E_STATE DRV_UNPOWERED",
        dict(boot, drv_power=False, params={"drv.pwr_sense_enable": True}), "ENABLE")
    add("disable_moving", "DISABLE while moving -> E_BUSY 1", moving, "DISABLE", srs=("FW-MOT-008",))
    add("disable_idle", "DISABLE while idle", {}, "DISABLE", srs=("FW-MOT-008",))

    # --- clears ----------------------------------------------------------------------------
    es = dict(boot, estop_latched=True)
    add("estop_clear_open", "ESTOP_CLEAR with sense input open -> E_CAUSE_ACTIVE 0xFFFF",
        dict(es, estop_input_open=True), "ESTOP_CLEAR", srs=("SAF-FW-006",))
    add("estop_clear_50ms", "ESTOP_CLEAR 50 ms after release -> E_CAUSE_ACTIVE 50 (ms remaining)",
        dict(es, estop_closed_ms=50), "ESTOP_CLEAR", srs=("SAF-FW-006",))
    add("estop_clear_100ms", "ESTOP_CLEAR 100 ms after release -> OK", dict(es, estop_closed_ms=100),
        "ESTOP_CLEAR", srs=("SAF-FW-006",))
    add("estop_clear_none", "ESTOP_CLEAR while nothing latched -> OK (idempotent)", {}, "ESTOP_CLEAR")
    hb = {"halt_latched": True}
    add("halt_clear_pressed", "HALT_CLEAR while STOP button pressed -> E_CAUSE_ACTIVE 0xFFFF",
        dict(hb, stop_btn_active=True), "HALT_CLEAR", srs=("SAF-FW-022",))
    add("halt_clear_10ms", "HALT_CLEAR 10 ms after button release -> E_CAUSE_ACTIVE 10",
        dict(hb, stop_btn_released_ms=10), "HALT_CLEAR", srs=("SAF-FW-022",))
    add("halt_clear_ok", "HALT_CLEAR after release >= io.release_ms", hb, "HALT_CLEAR", srs=("SAF-FW-022",))
    add("fault_clear_load", "FAULT_CLEAR of LOAD_LIMIT with load still beyond -> OK (unload allowed)",
        {"faults": ["LOAD_LIMIT"], "fault_causes": ["LOAD_LIMIT"], "raw": 7100000}, "FAULT_CLEAR",
        srs=("SAF-FW-011",))
    add("fault_clear_wiring", "FAULT_CLEAR with both limits still active -> E_CAUSE_ACTIVE LIMIT_WIRING",
        {"faults": ["LIMIT_WIRING"], "fault_causes": ["LIMIT_WIRING"]}, "FAULT_CLEAR", srs=("SAF-FW-014",))
    add("fault_clear_afe", "FAULT_CLEAR of AFE_FAULT while still stale -> E_CAUSE_ACTIVE AFE_FAULT",
        {"faults": ["AFE_FAULT", "STEP_FAULT"], "fault_causes": ["AFE_FAULT"], "afe_stale": True},
        "FAULT_CLEAR", srs=("SAF-FW-012", "FW-CMD-003"))
    add("fault_clear_k1", "FAULT_CLEAR of K1_WELDED while power still on with E-stop open",
        {"faults": ["K1_WELDED"], "fault_causes": ["K1_WELDED"]}, "FAULT_CLEAR", srs=("R5 §1.5",))
    add("fault_clear_gone", "FAULT_CLEAR when all causes gone -> OK",
        {"faults": ["STEP_FAULT", "HOME_NOT_FOUND", "HOME_DRIFT"]}, "FAULT_CLEAR", srs=("FW-CMD-003",))

    # --- NVM / system --------------------------------------------------------------------
    for c in ("SAVE_PARAMS", "LOAD_PARAMS", "DEFAULT_PARAMS"):
        add(f"{c.lower()}_moving", f"{c} while moving -> E_BUSY 1", moving, c, srs=("FW-NVM-003",))
        add(f"{c.lower()}_idle", f"{c} while idle", {}, c, srs=("FW-NVM-001",))
    add("load_no_record", "LOAD_PARAMS without valid record -> E_NVM 1", {"nvm_record_valid": False},
        "LOAD_PARAMS", srs=("FW-NVM-002",))
    add("reboot_bad_magic", "REBOOT wrong magic -> E_RANGE 0", {}, "REBOOT", {"magic": 0x12345678})
    add("reboot_moving", "REBOOT while moving -> E_BUSY 1", moving, "REBOOT", {"magic": rc.REBOOT_MAGIC})
    add("set_valid_2", "SET_VALID 2 -> E_RANGE 0", {}, "SET_VALID", {"valid": 2}, srs=("FW-CMD-002",))
    add("pause_moving", "PAUSE while moving -> OK (controlled stop + PAUSED)", moving, "PAUSE",
        srs=("SAF-FW-023",))
    add("pause_estop", "PAUSE accepted in every state", dict(boot, estop_latched=True), "PAUSE")
    add("pause_length", "PAUSE with 1 byte -> E_LENGTH 0", {}, "PAUSE", payload=b"")
    pages = math.ceil(len(pd.params) / rc.PARAMS_PER_PAGE)
    add("get_all_params_last", f"GET_ALL_PARAMS page {pages - 1} (last)", {}, "GET_ALL_PARAMS",
        {"page": pages - 1}, srs=("FW-CFG-002",))
    add("get_all_params_beyond", f"GET_ALL_PARAMS page {pages} -> E_RANGE 0", {}, "GET_ALL_PARAMS",
        {"page": pages}, srs=("FW-CFG-002",))
    add("get_param_unknown", "GET_PARAM 0x0999 -> E_PARAM_ID", {}, "GET_PARAM", {"id": 0x0999})

    # --- SET_PARAM per parameter (FW-CFG-003) -----------------------------------------
    # min/max vectors of hard-rule-coupled parameters run in a state where the partner
    # parameters permit the bound, so they test the range check, not the rule. Bounds that no
    # state can permit (strict "<" rules with equal ranges) stay E_CONFIG by design.
    relax: dict[str, dict[str, Any]] = {
        "motion.pulse_high_ns": {"motion.max_step_rate_hz": 100, "motion.pulse_low_min_ns": 2500},
        "motion.pulse_low_min_ns": {"motion.max_step_rate_hz": 100, "motion.pulse_high_ns": 2500},
        "motion.max_step_rate_hz": {"motion.pulse_high_ns": 2500, "motion.pulse_low_min_ns": 2500},
        "motion.v_max_travel_um_s": {"motion.v_max_load_um_s": 1},
        "motion.v_max_load_um_s": {"motion.v_max_travel_um_s": 250000},
        "limits.soft_min_um": {"limits.soft_max_um": 400000},
        "limits.soft_max_um": {"limits.soft_min_um": -10000},
        "safety.load_raw_max": {"safety.load_raw_min": -7151121},
        "safety.load_raw_min": {"safety.load_raw_max": 7151121},
    }
    for p in pd.params:
        k = p.key
        F = ("FW-CFG-003",)

        def sp(value: Any, tcode: int | None = None, wire: bytes | None = None) -> bytes:
            tc = p.code if tcode is None else tcode
            w = wire if wire is not None else rc.pvalue_pack(tc, value)
            return struct.pack(rc.PARAM_ENTRY_FMT, p.id, tc, w)

        add(f"set_{k}_default", f"{k} = default", {}, "SET_PARAM", payload=sp(p.default), srs=F)
        rs = {"params": relax[k]} if k in relax else {}
        note = " (partner parameters relaxed)" if rs else ""
        add(f"set_{k}_min", f"{k} = min{note}", rs, "SET_PARAM", payload=sp(p.min), srs=F)
        add(f"set_{k}_max", f"{k} = max{note}", rs, "SET_PARAM", payload=sp(p.max), srs=F)
        if p.type == "f32":
            below, above = p.min - (abs(p.min) * 0.01 or 1.0), p.max * 1.01
            add(f"set_{k}_below", f"{k} = {below} < min -> E_RANGE", {}, "SET_PARAM", payload=sp(below), srs=F)
            add(f"set_{k}_above", f"{k} = {above} > max -> E_RANGE", {}, "SET_PARAM", payload=sp(above), srs=F)
            add(f"set_{k}_nan", f"{k} = NaN -> E_RANGE", {}, "SET_PARAM",
                payload=sp(0, wire=struct.pack("<I", 0x7FC00000)), srs=F)
        else:
            lo, hi = gen_params.TYPES[p.type][3]  # type: ignore[misc]
            if p.min - 1 >= lo:
                add(f"set_{k}_below", f"{k} = min - 1 -> E_RANGE", {}, "SET_PARAM", payload=sp(p.min - 1), srs=F)
            if p.max + 1 <= hi:
                add(f"set_{k}_above", f"{k} = max + 1 -> E_RANGE", {}, "SET_PARAM", payload=sp(p.max + 1), srs=F)
        wrong = 5 if p.code != 5 else 6
        add(f"set_{k}_type", f"{k} with wrong type code {wrong} -> E_TYPE", {}, "SET_PARAM",
            payload=sp(0, tcode=wrong, wire=bytes(4)), srs=F)
        if p.size < 4:
            add(f"set_{k}_padding", f"{k} non-zero padding -> E_RANGE", {}, "SET_PARAM",
                payload=sp(0, wire=rc.pvalue_pack(p.code, p.default)[:3] + b"\x01"), srs=F)
        add(f"set_{k}_moving", f"{k} = default while moving -> {'OK' if p.moving_ok else 'E_BUSY 1'}",
            moving, "SET_PARAM", payload=sp(p.default), srs=F + ("FW-MOT-009",))

    # --- hard rules ----------------------------------------------------------------------
    H = ("FW-CFG-003", "FW-PAR-004")
    by_key = {p.key: p for p in pd.params}

    def spk(key: str, v: Any) -> bytes:
        q = by_key[key]
        return struct.pack(rc.PARAM_ENTRY_FMT, q.id, q.code, rc.pvalue_pack(q.code, v))
    add("rule_h1_min_eq_max", "soft_min_um = soft_max_um -> E_CONFIG (H1)", {}, "SET_PARAM",
        payload=spk("limits.soft_min_um", 290000), srs=H)
    add("rule_h1_max_below_min", "soft_max_um below soft_min_um -> E_CONFIG (H1)", {}, "SET_PARAM",
        payload=spk("limits.soft_max_um", 400), srs=H)
    add("rule_h2_min_above_max", "load_raw_min >= load_raw_max -> E_CONFIG (H2)",
        {"params": {"safety.load_raw_max": 1000}}, "SET_PARAM", payload=spk("safety.load_raw_min", 1000),
        srs=("SAF-FW-010",))
    add("rule_h3_rate", "max_step_rate 50 001 Hz with 10 + 10 us pulses -> E_CONFIG (H3)", {},
        "SET_PARAM", payload=spk("motion.max_step_rate_hz", 50001), srs=("FW-MOT-001",))
    add("rule_h3_width", "pulse_high 15 us at 50 kHz -> E_CONFIG (H3)", {}, "SET_PARAM",
        payload=spk("motion.pulse_high_ns", 15000), srs=("FW-MOT-001",))
    add("rule_h3_ok", "pulse_high 5 us at 50 kHz -> OK", {}, "SET_PARAM",
        payload=spk("motion.pulse_high_ns", 5000), srs=("FW-MOT-001",))
    add("rule_h4_load_above_travel", "v_max_load 30 001 > v_max_travel -> E_CONFIG (H4)", {},
        "SET_PARAM", payload=spk("motion.v_max_load_um_s", 30001), srs=("R5 §2.3",))
    add("rule_h4_travel_below_load", "v_max_travel 19 999 < v_max_load -> E_CONFIG (H4)", {},
        "SET_PARAM", payload=spk("motion.v_max_travel_um_s", 19999), srs=("R5 §2.3",))
    H5 = ("SAF-FW-012", "D-33a")
    sps10 = {"params": {"afe.rate_sps": 0}}
    add("rule_h5_timeout_199_sps10", "afe.timeout_ms 199 at SPS10 (period 100 ms) -> E_CONFIG (H5)", sps10,
        "SET_PARAM", payload=spk("afe.timeout_ms", 199), srs=H5)
    add("rule_h5_timeout_200_sps10", "afe.timeout_ms 200 at SPS10 = 2 periods -> OK (H5)", sps10,
        "SET_PARAM", payload=spk("afe.timeout_ms", 200), srs=H5)
    add("rule_h5_timeout_25_sps80", "afe.timeout_ms 25 at SPS80 = 2 periods -> OK (H5)", {},
        "SET_PARAM", payload=spk("afe.timeout_ms", 25), srs=H5)
    add("rule_h5_rate_sps10_default_timeout", "afe.rate_sps SPS10 with the default timeout 250 ms -> OK",
        {}, "SET_PARAM", payload=spk("afe.rate_sps", 0), srs=H5)
    add("rule_h5_rate_sps10_short_timeout", "afe.rate_sps SPS10 with afe.timeout_ms 100 -> E_CONFIG (H5)",
        {"params": {"afe.timeout_ms": 100}}, "SET_PARAM", payload=spk("afe.rate_sps", 0), srs=H5)
    add("rule_h5_rate_sps80_from_sps10", "afe.rate_sps SPS10 -> SPS80 with afe.timeout_ms 200 -> OK",
        {"params": {"afe.timeout_ms": 200, "afe.rate_sps": 0}}, "SET_PARAM", payload=spk("afe.rate_sps", 1),
        srs=H5)
    add("load_raw_max_cap", "load_raw_max = 7 151 121 accepted (SAF-FW-010)", {}, "SET_PARAM",
        payload=spk("safety.load_raw_max", 7151121), srs=("SAF-FW-010",))
    add("spm_change_moving", "steps_per_mm while moving -> E_BUSY 1", moving, "SET_PARAM",
        payload=spk("motion.steps_per_mm", 636.0), srs=("FW-MOT-009",))
    add("load_limit_moving", "load_raw_max while moving -> OK (effective next sample)", moving,
        "SET_PARAM", payload=spk("safety.load_raw_max", 3000000), srs=("SAF-FW-010",))

    names = [v["name"] for v in vecs]
    assert len(names) == len(set(names)), "duplicate check-vector names"
    # every vector state is a valid configuration (all hard rules hold for its parameter overrides)
    for v in vecs:
        st0 = cc.FwState.from_dict(v["state"])
        for k, val in st0.params.items():
            assert model.hard_rule(st0, k, val) is None, f"{v['name']}: state violates a hard rule ({k})"
    # every range end is reachable (ICD §11.4, v0.2): min/max vectors are accepted
    unreachable = [v["name"] for v in vecs if v["name"].startswith("set_")
                   and v["name"].endswith(("_min", "_max")) and v["expect"]["status"] != "OK"]
    assert not unreachable, f"unreachable range ends: {unreachable}"
    return {
        "_comment": ["GENERATED by 00_System/tools/gen_vectors.py from ref_cmdcheck.py - do not edit",
                     f"ICD_protocol.md v{rc.ICD_VERSION} §4-§6: command acceptance (check order "
                     "steps 1-5), not execution",
                     "state: overrides of state_defaults; params: overrides of the params.yaml "
                     "defaults (enum/bool as numeric code)",
                     "expect.status/detail: verdict; response_frame_hex for NACKs (same SEQ)",
                     "expect.paused_after (present when state.paused or for PAUSE): PAUSED latch "
                     "after the command (execution effect, ICD §5.5; twin / simulator / FW latch "
                     "tests)",
                     "state_schema: version of the state keys (state_defaults); keys are only added "
                     "(never renamed or removed) and every addition bumps state_schema (F-B-25)",
                     "an implementation passes when, for each vector, the FW in the given state "
                     "answers the request with the expected STATUS and detail and, for a NACK, "
                     "exactly response_frame_hex, without side effects"],
        "icd_version": rc.ICD_VERSION, "param_dict_hash": f"0x{pd.hash:08X}",
        "state_schema": cc.STATE_SCHEMA,
        "state_defaults": {k: v for k, v in cc.FwState().__dict__.items()},
        "vectors": vecs,
    }


# ======================================================================================
# units vectors (ICD §0.1: um <-> steps, step-rate speed cap; OI-FW-20, SYS-003)
# ======================================================================================
def round_half_away(x: float) -> int:
    """Exact round-half-away-from-zero of a binary64 value (C99 round())."""
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def um_to_steps(um: int, spm32: float) -> int:
    return round_half_away(float(um) * spm32 / 1000.0)


def steps_to_um(steps: int, spm32: float) -> int:
    return round_half_away(float(steps) * 1000.0 / spm32)


def rate_cap_um_s(rate_hz: int, spm32: float) -> int:
    return math.floor(float(rate_hz) * 1000.0 / spm32)


def make_units(pd: gen_params.Dictionary) -> dict[str, Any]:
    p = {q.key: q for q in pd.params}["motion.steps_per_mm"]
    spms = [100.0, 160.0, 636.0778443113772, 640.0, 800.0, 1280.0, 4000.0, 100000.0]
    ums = [0, 1, -1, 3, 5, -5, 15, 100, -500, 12345, 25000, 104375, 290000, 399999, 400000, -10000]
    steps = [0, 1, -1, 2, -2, 3, 64, -318, 7852, 16000, 46400, 160000, 4000000, -1600000]
    rates = [100, 1000, 50000, 99999, 100000]
    cases_u2s, cases_s2u, cases_cap = [], [], []
    for spm in spms:
        spm32 = _f32(spm)
        assert p.min <= spm32 <= p.max
        bits = f"0x{struct.unpack('<I', struct.pack('<f', spm32))[0]:08X}"
        for um in ums:
            cases_u2s.append({"spm_f32_hex": bits, "spm": spm32, "um": um, "steps": um_to_steps(um, spm32)})
        for st in steps:
            if abs(st * 1000.0 / spm32) < 2**31:
                cases_s2u.append({"spm_f32_hex": bits, "spm": spm32, "steps": st, "um": steps_to_um(st, spm32)})
        for r in rates:
            cases_cap.append({"spm_f32_hex": bits, "spm": spm32, "max_step_rate_hz": r,
                              "rate_cap_um_s": rate_cap_um_s(r, spm32)})
    ties = [c for c in cases_u2s if (abs(c["um"] * c["spm"] / 1000.0) % 1) == 0.5]
    ties += [c for c in cases_s2u if (abs(c["steps"] * 1000.0 / c["spm"]) % 1) == 0.5]
    assert len(ties) >= 4, "tie cases must be present"
    return {
        "_comment": ["GENERATED by 00_System/tools/gen_vectors.py - do not edit",
                     f"ICD_protocol.md v{rc.ICD_VERSION} §0.1: steps_per_mm is the binary32 parameter value "
                     "(spm_f32_hex); every expression is evaluated in IEEE-754 binary64 left to right; "
                     "rounding = round half away from zero (C99 round/lround) of the binary64 result",
                     "um_to_steps(um) = round(um * spm / 1000.0); steps_to_um(steps) = "
                     "round(steps * 1000.0 / spm); rate_cap_um_s = floor(max_step_rate_hz * 1000.0 / spm) "
                     "(v_limit = min(v_max_*, rate_cap), ICD §5.4)",
                     "an implementation in other arithmetic (float, fixed point) MUST reproduce every "
                     "value exactly; tie cases (x.5) are included"],
        "icd_version": rc.ICD_VERSION, "param_dict_hash": f"0x{pd.hash:08X}",
        "um_to_steps": cases_u2s, "steps_to_um": cases_s2u, "rate_cap": cases_cap,
        "motion_vectors": "planned for M2 as vectors/motion_vectors.json (R4 §12 TV-M: ramp periods, "
                          "planner; FW float32 tolerance ±1 tick per period, sum ±N/1000 ticks)",
    }


def render(obj: dict[str, Any]) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify the vector files are up to date")
    args = ap.parse_args(argv)
    pd = gen_params.load()
    outputs = {PROTO_PATH: render(make_protocol(pd)), CHECK_PATH: render(make_check(pd)),
               UNITS_PATH: render(make_units(pd))}
    stale = []
    for path, content in outputs.items():
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old == content:
            continue
        if args.check:
            stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        print(f"wrote {path.relative_to(gen_params.ROOT)}")
    if stale:
        for p in stale:
            print(f"out of date: {p.relative_to(gen_params.ROOT)}", file=sys.stderr)
        return 1
    pv = json.loads(outputs[PROTO_PATH])
    cv = json.loads(outputs[CHECK_PATH])
    uv = json.loads(outputs[UNITS_PATH])
    print(f"protocol: {len(pv['crc16'])} crc, {len(pv['frames'])} frames, {len(pv['streams'])} streams; "
          f"check: {len(cv['vectors'])} vectors; units: {len(uv['um_to_steps'])} + "
          f"{len(uv['steps_to_um'])} + {len(uv['rate_cap'])}; PARAM_DICT_HASH = 0x{pd.hash:08X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
