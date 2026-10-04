#!/usr/bin/env python3
"""Reference codec (test oracle) for the Bird Bend Stand PC <-> FW protocol.

Implements: ICD_protocol.md v0.6 (PROTO_VERSION 1.0, PAYLOAD_VERSION 1);
            IF-002, IF-003, IF-004, IF-006, IF-009, FW-STR-003
Origin: framing, CRC and parser follow Thrust_Stand_HAW/00_System/tools/ref_codec.py @9473c68
        (copied and trimmed, D-02); message set and payloads are bend-stand specific.

Pure Python >= 3.11, standard library only. It is deliberately minimal and literal: FW host
tests and SW pytest consume the vectors produced by gen_vectors.py and may import this module
as an oracle. It is NOT the production codec of either side.

The name tables below are hand-written on purpose (independent oracle); the normative names and
codes live in 00_System/specs/protocol.yaml and tests/test_ref_codec.py proves both agree.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

ICD_VERSION = "0.7"

# ======================================================================================
# constants (ICD §2, §3)
# ======================================================================================
SYNC0, SYNC1 = 0xA5, 0x5A
SYNC = bytes((SYNC0, SYNC1))
HEADER_LEN = 6          # SYNC0 SYNC1 TYPE SEQ LEN_lo LEN_hi
MAX_LEN = 160           # maximum payload length (frame <= 168 B = 1.82 ms on the wire)
OVERHEAD = 8            # header + CRC
INTERBYTE_TIMEOUT_MS = 20
PROTO_MAJOR, PROTO_MINOR = 1, 0
PAYLOAD_VERSION = 1
AFE_NO_DATA = -0x80000000          # afe_raw of a fallback (no AFE data) DATA frame
RAW_MIN, RAW_MAX = -0x800000, 0x7FFFFF   # HX711 24-bit range (rails = saturated)
REBOOT_MAGIC = 0xB007B007
JOG_NO_BOUND = -0x80000000         # JOG bound_um: run to the soft limit (ICD §5.4)
PARAMS_PER_PAGE = 20
RESP_BIT = 0x80

CMD: dict[str, int] = {
    "PING": 0x01, "GET_INFO": 0x02, "GET_STATUS": 0x03, "REBOOT": 0x04,
    "GET_ALL_PARAMS": 0x10, "GET_PARAM": 0x11, "SET_PARAM": 0x12,
    "SAVE_PARAMS": 0x13, "LOAD_PARAMS": 0x14, "DEFAULT_PARAMS": 0x15,
    "STREAM_START": 0x20, "STREAM_STOP": 0x21, "SET_VALID": 0x22,
    "ENABLE": 0x30, "DISABLE": 0x31, "HOME": 0x32, "MOVE_ABS": 0x33, "JOG": 0x34,
    "MOVE_UNTIL_LOAD": 0x35, "STOP": 0x36, "HALT": 0x37, "HALT_CLEAR": 0x38,
    "ESTOP_CLEAR": 0x39, "FAULT_CLEAR": 0x3A, "PAUSE": 0x3B, "RESUME": 0x3C, "DIAG_MEAS": 0x3D,
}
CMD_NAME = {v: k for k, v in CMD.items()}
ASYNC = {"DATA": 0xC0, "EVENT": 0xC1}
ASYNC_NAME = {v: k for k, v in ASYNC.items()}

# request payload struct formats (ICD §4); LEN is fixed per command
REQ_FMT: dict[str, str] = {
    "PING": "<", "GET_INFO": "<", "GET_STATUS": "<", "REBOOT": "<I",
    "GET_ALL_PARAMS": "<B", "GET_PARAM": "<H", "SET_PARAM": "<HB4s",
    "SAVE_PARAMS": "<", "LOAD_PARAMS": "<", "DEFAULT_PARAMS": "<",
    "STREAM_START": "<", "STREAM_STOP": "<", "SET_VALID": "<B",
    "ENABLE": "<", "DISABLE": "<", "HOME": "<B", "MOVE_ABS": "<iII", "JOG": "<iIi",
    "MOVE_UNTIL_LOAD": "<iIIiB", "STOP": "<B", "HALT": "<", "HALT_CLEAR": "<",
    "ESTOP_CLEAR": "<", "FAULT_CLEAR": "<", "PAUSE": "<", "RESUME": "<", "DIAG_MEAS": "<BBHI",
}
REQ_FIELDS: dict[str, tuple[str, ...]] = {
    "REBOOT": ("magic",), "GET_ALL_PARAMS": ("page",), "GET_PARAM": ("id",),
    "SET_VALID": ("valid",), "HOME": ("flags",),
    "MOVE_ABS": ("target_um", "v_um_s", "a_um_s2"), "JOG": ("v_um_s", "a_um_s2", "bound_um"),
    "MOVE_UNTIL_LOAD": ("bound_um", "v_um_s", "a_um_s2", "raw_stop", "cmp"),
    "STOP": ("mode",), "DIAG_MEAS": ("op", "sel", "a", "b"),
}
REQ_LEN = {k: struct.calcsize(v) for k, v in REQ_FMT.items()}

STATUS: dict[str, int] = {
    "OK": 0, "E_UNKNOWN_CMD": 1, "E_LENGTH": 2, "E_PARAM_ID": 3, "E_TYPE": 4, "E_RANGE": 5,
    "E_CONFIG": 6, "E_BUSY": 7, "E_STATE": 8, "E_CAUSE_ACTIVE": 9, "E_CONFIRM": 10,
    "E_NVM": 11, "E_INTERNAL": 12,
}
STATUS_NAME = {v: k for k, v in STATUS.items()}
BUSY_DETAIL = {1: "MOTION", 2: "ENABLING"}

# bit tables (index = bit number); "" = reserved
DATA_FLAGS = ["VALID", "MOVING", "HOMED", "ENABLED", "ESTOP", "HALT", "FAULT", "OVERRUN"]
DATA_STATUS = ["PAUSED", "LIMIT_START", "LIMIT_END", "LOAD_LIMIT", "AFE_STALE", "AFE_SATURATED",
               "AFE_SETTLING", "AFE_RATE_MISMATCH", "LINK_WDG", "", "PAUSE_BTN", "ALM",   # 9 retired (D-36)
               "PEND", "POS_UNCERTAIN", "NO_AFE_DATA", "DRV_PWR"]
FAULTS = ["LOAD_LIMIT", "AFE_FAULT", "STEP_FAULT", "LIMIT_WIRING", "HOME_NOT_FOUND",
          "HOME_WIRING", "K1_WELDED", "HOME_DRIFT"]
BLOCK = ["ESTOP", "HALT", "FAULT", "NOT_ENABLED", "NOT_HOMED", "LIMIT", "AFE_STALE",
         "AFE_SATURATED", "DRV_UNPOWERED", "DRIVER_ALARM", "PAUSED", "MEAS_STATE"]
IO = ["ESTOP_OPEN", "LIMIT_START", "LIMIT_END", "", "PAUSE_BTN", "ALM", "PEND",   # 3 retired (D-36)
      "DRV_PWR", "ENA_DISABLED", "RATE_80"]
SYS_FLAGS = ["CLK_FALLBACK", "CFG_DIRTY", "STREAM_ON", "REBOOT_PENDING", "NVM_DEFAULTED"]
FEATURES = ["AFE", "AFE_SYNTHETIC", "MOTION", "HOMING", "MOVE_UNTIL_LOAD", "NVM", "TWIN",
            "BUTTONS", "DRV_SIGNALS", "HW_MEAS"]

MOTION_STATE = ["NOT_ENABLED", "ENABLING", "IDLE", "MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD",
                "HOMING", "STOPPING"]
HOME_PHASE = ["NONE", "PRECHECK", "RELEASE", "FAST_SEEK", "BACKOFF", "SLOW_APPROACH",
              "MOVE_TO_ZERO", "DONE"]
SOURCE = ["NONE", "PC", "BUTTON"]          # halt_src, pause_src, HALT_SET / PAUSED arg
HALT_SRC = SOURCE
RESET_CAUSE = ["UNKNOWN", "POWER_ON", "PIN", "SOFTWARE", "IWDG", "WWDG", "LOW_POWER", "BROWN_OUT"]

EVENT: dict[str, int] = {
    "BOOT": 1, "STOPPED": 2, "MOVE_DONE": 3, "ESTOP_SET": 4, "ESTOP_CLEARED": 5,
    "HALT_SET": 6, "HALT_CLEARED": 7, "PAUSED": 8, "PAUSE_CLEARED": 9, "RESUME_REQUEST": 10,
    "FAULT_SET": 11, "FAULT_CLEARED": 12, "LIMIT_SET": 13, "LIMIT_CLEARED": 14,
    "LINK_WDG": 15, "LINK_RESTORED": 16, "VALID_CLEARED": 17, "HOMED": 18, "HOME_FAILED": 19,
    "DRIVER_ENABLED": 20, "DRIVER_DISABLED": 21, "PAUSE_BUTTON": 23,   # 22 retired (D-36)
    "ALM_CHANGED": 24, "AFE_REINIT": 25, "AFE_RATE_MISMATCH": 26, "AFE_STALE": 27,
    "PARAMS_SAVED": 28, "PARAMS_LOADED": 29, "PARAMS_DEFAULTED": 30, "NVM_ERROR": 31,
    "CLK_FALLBACK": 32, "DRIVER_POWER": 33, "NOT_SETTLED": 34,
}
EVENT_NAME = {v: k for k, v in EVENT.items()}

STOP_CAUSE: dict[str, int] = {
    "NONE": 0, "PC_STOP": 1, "PC_STOP_CONTROLLED": 2, "PC_HALT": 3,   # 4 retired (D-36)
    "PAUSE_BUTTON": 5, "PC_PAUSE": 6, "ESTOP": 7, "LIMIT_START": 8, "LIMIT_END": 9,
    "LIMIT_WIRING": 10, "LOAD_LIMIT": 11, "AFE_FAULT": 12, "LINK_WDG": 13, "JOG_DEADMAN": 14,
    "STEP_FAULT": 15, "HOME_FAIL": 16, "DRV_POWER_LOST": 17,
}
STOP_CAUSE_NAME = {v: k for k, v in STOP_CAUSE.items()}
MOVE_DONE_REASON = ["TARGET", "LOAD_THRESHOLD", "BOUND", "SOFT_LIMIT", "JOG_ZERO", "STOPPED"]
HOME_FAIL_REASON = {1: "NOT_FOUND", 2: "WIRING", 3: "ABORTED"}
MEAS_OP = ["INFO", "PROBE_ARM", "PROBE_READ", "COUNTER", "STAMPS", "NOINIT", "STIM_RUN", "HANG",
           "STATIC_LEVEL", "DWT"]
MEAS_BODY_WORDS = 16                    # DIAG_MEAS OK body: u32 w[16] (ICD Appendix C)

# param wire types (ICD §7.5): code -> (name, struct fmt, size)
PTYPE = {1: ("u8", "<B", 1), 2: ("i8", "<b", 1), 3: ("u16", "<H", 2), 4: ("i16", "<h", 2),
         5: ("u32", "<I", 4), 6: ("i32", "<i", 4), 7: ("f32", "<f", 4), 8: ("bool", "<B", 1),
         9: ("enum", "<B", 1)}
PTYPE_CODE = {v[0]: k for k, v in PTYPE.items()}


# ======================================================================================
# CRC (ICD §2.1)
# ======================================================================================
def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, xorout 0 (IF-004)."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


# ======================================================================================
# framing (ICD §2)
# ======================================================================================
def encode_frame(ftype: int, seq: int, payload: bytes = b"") -> bytes:
    if not 0 <= ftype <= 0xFF or not 0 <= seq <= 0xFF:
        raise ValueError("type/seq out of range")
    if len(payload) > MAX_LEN:
        raise ValueError("payload too long")
    body = bytes((ftype, seq)) + struct.pack("<H", len(payload)) + payload
    return SYNC + body + struct.pack("<H", crc16_ccitt(body))


@dataclass(frozen=True)
class Frame:
    type: int
    seq: int
    payload: bytes

    @property
    def raw(self) -> bytes:
        return encode_frame(self.type, self.seq, self.payload)


@dataclass
class FrameParser:
    """Receiver state machine of ICD §2.3 (both directions).

    feed(): bytes in, complete CRC-valid frames out.
    idle_timeout(): call when no byte arrived for >= 20 ms (inter-byte timeout).
    """

    buf: bytearray = field(default_factory=bytearray)
    crc_errors: int = 0
    len_errors: int = 0
    timeout_drops: int = 0
    frames_ok: int = 0

    def feed(self, data: bytes) -> list[Frame]:
        self.buf += data
        return self._scan()

    def idle_timeout(self) -> list[Frame]:
        out: list[Frame] = []
        while True:
            out += self._scan()
            i = self.buf.find(SYNC)
            if i < 0:
                self.buf.clear()          # a trailing lone SYNC0 cannot become a frame any more
                return out
            self.timeout_drops += 1       # incomplete candidate at the head: drop its SYNC0
            del self.buf[: i + 1]

    def _scan(self) -> list[Frame]:
        out: list[Frame] = []
        while True:
            i = self.buf.find(SYNC)
            if i < 0:
                keep = 1 if self.buf[-1:] == bytes((SYNC0,)) else 0
                del self.buf[: len(self.buf) - keep]
                return out
            del self.buf[:i]
            if len(self.buf) < HEADER_LEN:
                return out
            length = self.buf[4] | (self.buf[5] << 8)
            if length > MAX_LEN:
                self.len_errors += 1
                del self.buf[:1]
                continue
            if len(self.buf) < OVERHEAD + length:
                return out
            body = bytes(self.buf[2: HEADER_LEN + length])
            rx_crc = self.buf[HEADER_LEN + length] | (self.buf[HEADER_LEN + length + 1] << 8)
            if crc16_ccitt(body) != rx_crc:
                self.crc_errors += 1
                del self.buf[:1]
                continue
            out.append(Frame(body[0], body[1], body[4:]))
            self.frames_ok += 1
            del self.buf[: OVERHEAD + length]

    def counters(self) -> dict[str, int]:
        return {"frames_ok": self.frames_ok, "crc_errors": self.crc_errors,
                "len_errors": self.len_errors, "timeout_drops": self.timeout_drops}


# ======================================================================================
# value helpers
# ======================================================================================
def bits_to_names(value: int, names: list[str]) -> list[str]:
    return [n for i, n in enumerate(names) if value >> i & 1 and n]


def names_to_bits(names: list[str], table: list[str]) -> int:
    return sum(1 << table.index(n) for n in names)


def pvalue_pack(tcode: int, value: int | float | bool) -> bytes:
    _, fmt, _size = PTYPE[tcode]
    v = float(value) if tcode == 7 else int(value)
    return struct.pack(fmt, v).ljust(4, b"\x00")


def pvalue_unpack(tcode: int, wire: bytes) -> int | float:
    _, fmt, size = PTYPE[tcode]
    if any(wire[size:4]):
        raise ValueError("non-zero padding")
    (v,) = struct.unpack(fmt, wire[:size])
    return v


def _enum_name(table: list[str], v: int) -> str | int:
    return table[v] if 0 <= v < len(table) else v


def _enum_code(table: list[str], v: str | int) -> int:
    return v if isinstance(v, int) else table.index(v)


# ======================================================================================
# structures (ICD §7)
# ======================================================================================
INFO_FMT = "<3B3BI12s16sHI"            # 44 B
STATUS_FMT = "<IIBBHHHBBBBiiiiHH5I6H3IBx"  # 86 B (v0.2: + u8 pause_src, u8 reserved = 0)
DATA_FMT = "<IBBiiHH"                  # 18 B
EVENT_FMT = "<IHHii"                   # 16 B
PARAM_ENTRY_FMT = "<HB4s"              # 7 B
INFO_LEN = struct.calcsize(INFO_FMT)
STATUS_LEN = struct.calcsize(STATUS_FMT)
DATA_LEN = struct.calcsize(DATA_FMT)
EVENT_LEN = struct.calcsize(EVENT_FMT)
assert (INFO_LEN, STATUS_LEN, DATA_LEN, EVENT_LEN) == (44, 86, 18, 16)

STATUS_FIELDS = ["uptime_ms", "t_us", "flags", "motion_state", "status", "faults", "io",
                 "home_phase", "halt_src", "reset_cause", "sys_flags", "pos_um", "target_um",
                 "pos_steps", "afe_raw_last", "afe_rate_dsps", "afe_reinit_count",
                 "rx_frames_ok", "rx_crc_errors", "rx_frame_errors", "rx_overruns", "tx_drops",
                 "event_overflows", "loop_max_us", "link_age_ms", "stack_free_min",
                 "nvm_save_ms", "idle_disable_left_s", "nvm_record_seq", "nvm_save_uptime_ms",
                 "v_limit_um_s", "pause_src"]
_STATUS_BITS = {"flags": DATA_FLAGS, "status": DATA_STATUS, "faults": FAULTS, "io": IO,
                "sys_flags": SYS_FLAGS}
_STATUS_ENUMS = {"motion_state": MOTION_STATE, "home_phase": HOME_PHASE, "halt_src": SOURCE,
                 "reset_cause": RESET_CAUSE, "pause_src": SOURCE}


def encode_info(d: dict[str, Any]) -> bytes:
    build = d["build"].encode("ascii")
    if len(build) > 16:
        raise ValueError("build string > 16 chars")
    return struct.pack(INFO_FMT, d["proto_major"], d["proto_minor"], d["payload_version"],
                       *d["fw_version"], int(d["param_dict_hash"], 16), bytes.fromhex(d["uid"]),
                       build.ljust(16, b"\x00"), d["param_count"],
                       names_to_bits(d["features"], FEATURES))


def decode_info(b: bytes) -> dict[str, Any]:
    v = struct.unpack(INFO_FMT, b[:INFO_LEN])     # trailing bytes ignored (ICD §0.2)
    return {"proto_major": v[0], "proto_minor": v[1], "payload_version": v[2],
            "fw_version": [v[3], v[4], v[5]], "param_dict_hash": f"0x{v[6]:08X}",
            "uid": v[7].hex().upper(), "build": v[8].rstrip(b"\x00").decode("ascii"),
            "param_count": v[9], "features": bits_to_names(v[10], FEATURES)}


def encode_status(d: dict[str, Any]) -> bytes:
    vals = []
    for k in STATUS_FIELDS:
        x = d[k]
        if k in _STATUS_BITS:
            x = names_to_bits(x, _STATUS_BITS[k])
        elif k in _STATUS_ENUMS:
            x = _enum_code(_STATUS_ENUMS[k], x)
        vals.append(x)
    return struct.pack(STATUS_FMT, *vals)


def decode_status(b: bytes) -> dict[str, Any]:
    d = dict(zip(STATUS_FIELDS, struct.unpack(STATUS_FMT, b[:STATUS_LEN])))
    for k, t in _STATUS_BITS.items():
        d[k] = bits_to_names(d[k], t)
    for k, t in _STATUS_ENUMS.items():
        d[k] = _enum_name(t, d[k])
    return d


def encode_data(d: dict[str, Any]) -> bytes:
    return struct.pack(DATA_FMT, d["t_us"], d["payload_version"],
                       names_to_bits(d["flags"], DATA_FLAGS), d["afe_raw"], d["setpoint_um"],
                       d["frame_seq"], names_to_bits(d["status"], DATA_STATUS))


def decode_data(b: bytes) -> dict[str, Any]:
    if len(b) < DATA_LEN:
        raise ValueError(f"DATA payload {len(b)} < {DATA_LEN} bytes")
    t, ver, fl, raw, sp, seq, st = struct.unpack(DATA_FMT, b[:DATA_LEN])
    if ver != PAYLOAD_VERSION:
        raise ValueError(f"unsupported PAYLOAD_VERSION {ver}")
    return {"t_us": t, "payload_version": ver, "flags": bits_to_names(fl, DATA_FLAGS),
            "afe_raw": raw, "setpoint_um": sp, "frame_seq": seq,
            "status": bits_to_names(st, DATA_STATUS)}


def encode_event(d: dict[str, Any]) -> bytes:
    return struct.pack(EVENT_FMT, d["t_us"], EVENT[d["code"]], d["arg"], d["value"], d["value2"])


def decode_event(b: bytes) -> dict[str, Any]:
    t, c, a, v, v2 = struct.unpack(EVENT_FMT, b[:EVENT_LEN])
    return {"t_us": t, "code": EVENT_NAME.get(c, c), "arg": a, "value": v, "value2": v2}


def encode_param_entry(e: dict[str, Any]) -> bytes:
    tcode = PTYPE_CODE[e["type"]]
    return struct.pack(PARAM_ENTRY_FMT, e["id"], tcode, pvalue_pack(tcode, e["value"]))


def decode_param_entry(b: bytes) -> dict[str, Any]:
    pid, tcode, wire = struct.unpack(PARAM_ENTRY_FMT, b[:7])
    return {"id": pid, "type": PTYPE[tcode][0], "value": pvalue_unpack(tcode, wire)}


# ---- request payloads (PC -> FW) ------------------------------------------------------
def encode_request(name: str, d: dict[str, Any]) -> bytes:
    if name == "SET_PARAM":
        return encode_param_entry(d)
    fields = REQ_FIELDS.get(name, ())
    return struct.pack(REQ_FMT[name], *(d[f] for f in fields))


def decode_request(name: str, b: bytes) -> dict[str, Any]:
    if len(b) != REQ_LEN[name]:
        raise ValueError(f"{name}: LEN {len(b)} != {REQ_LEN[name]}")
    if name == "SET_PARAM":
        return decode_param_entry(b)
    return dict(zip(REQ_FIELDS.get(name, ()), struct.unpack(REQ_FMT[name], b)))


# ---- response payloads (FW -> PC): STATUS byte + body / NACK -------------------------
def encode_response(name: str, d: dict[str, Any]) -> bytes:
    st = STATUS[d["status"]]
    if st != 0:
        return struct.pack("<BH", st, d["detail"])
    if name == "GET_INFO":
        body = encode_info(d["info"])
    elif name == "GET_STATUS":
        body = encode_status(d["board_status"])
    elif name == "GET_ALL_PARAMS":
        body = struct.pack("<BBB", d["page"], d["page_count"], len(d["entries"]))
        body += b"".join(encode_param_entry(e) for e in d["entries"])
    elif name in ("GET_PARAM", "SET_PARAM"):
        body = encode_param_entry(d["entry"])
    elif name == "SET_VALID":
        body = struct.pack("<I", d["t_us"])
    elif name == "ENABLE":
        body = struct.pack("<H", d["settle_ms"])
    elif name == "FAULT_CLEAR":
        body = struct.pack("<H", names_to_bits(d["cleared"], FAULTS))
    elif name == "DIAG_MEAS":
        body = struct.pack(f"<{MEAS_BODY_WORDS}I", *d["w"])
    else:
        body = b""
    return b"\x00" + body


def decode_response(name: str, b: bytes) -> dict[str, Any]:
    st = b[0]
    if st != 0:
        if len(b) != 3:
            raise ValueError("NACK payload must be 3 bytes")
        return {"status": STATUS_NAME.get(st, st), "detail": struct.unpack_from("<H", b, 1)[0]}
    body = b[1:]
    d: dict[str, Any] = {"status": "OK"}
    if name == "GET_INFO":
        d["info"] = decode_info(body)
    elif name == "GET_STATUS":
        d["board_status"] = decode_status(body)
    elif name == "GET_ALL_PARAMS":
        page, count, n = body[0], body[1], body[2]
        if len(body) != 3 + 7 * n:
            raise ValueError("GET_ALL_PARAMS: length does not match n")
        d.update(page=page, page_count=count,
                 entries=[decode_param_entry(body[3 + 7 * i: 10 + 7 * i]) for i in range(n)])
    elif name in ("GET_PARAM", "SET_PARAM"):
        d["entry"] = decode_param_entry(body)
    elif name == "SET_VALID":
        d["t_us"] = struct.unpack_from("<I", body)[0]
    elif name == "ENABLE":
        d["settle_ms"] = struct.unpack_from("<H", body)[0]
    elif name == "FAULT_CLEAR":
        d["cleared"] = bits_to_names(struct.unpack_from("<H", body)[0], FAULTS)
    elif name == "DIAG_MEAS":
        if len(body) != 4 * MEAS_BODY_WORDS:
            raise ValueError("DIAG_MEAS body must be 64 bytes")
        d["w"] = list(struct.unpack(f"<{MEAS_BODY_WORDS}I", body))
    return d


def encode_async(name: str, d: dict[str, Any]) -> bytes:
    if name == "DATA":
        return encode_data(d)
    if name == "EVENT":
        return encode_event(d)
    raise KeyError(name)


def decode_async(name: str, b: bytes) -> dict[str, Any]:
    if name == "DATA":
        return decode_data(b)
    if name == "EVENT":
        return decode_event(b)
    raise KeyError(name)


def classify(ftype: int) -> tuple[str, str]:
    """TYPE -> (kind, name) with kind in request / response / async / invalid (ICD §3)."""
    if ftype in ASYNC_NAME:
        return "async", ASYNC_NAME[ftype]
    if 0x01 <= ftype <= 0x3F:
        return "request", CMD_NAME.get(ftype, f"0x{ftype:02X}")
    if 0x81 <= ftype <= 0xBF:
        return "response", CMD_NAME.get(ftype & 0x7F, f"0x{ftype & 0x7F:02X}")
    return "invalid", f"0x{ftype:02X}"


def decode_frame(fr: Frame) -> dict[str, Any]:
    kind, name = classify(fr.type)
    if kind == "async":
        return decode_async(name, fr.payload)
    if kind == "response":
        if name.startswith("0x"):        # response to an unknown command: NACK only
            return decode_response("PING", fr.payload)
        return decode_response(name, fr.payload)
    if kind == "request" and not name.startswith("0x"):
        return decode_request(name, fr.payload)
    return {"raw_hex": fr.payload.hex().upper()}


def encode_decoded(ftype: int, d: dict[str, Any]) -> bytes:
    kind, name = classify(ftype)
    if kind == "async":
        return encode_async(name, d)
    if kind == "response":
        return encode_response("PING" if name.startswith("0x") else name, d)
    if kind == "request" and not name.startswith("0x"):
        return encode_request(name, d)
    return bytes.fromhex(d.get("raw_hex", ""))


def make_frame(name: str, seq: int, d: dict[str, Any], response: bool = False) -> bytes:
    """Convenience: encode a command request / response or an async frame by name."""
    if name in ASYNC:
        return encode_frame(ASYNC[name], seq, encode_async(name, d))
    t = CMD[name] | (RESP_BIT if response else 0)
    return encode_frame(t, seq, encode_decoded(t, d))
