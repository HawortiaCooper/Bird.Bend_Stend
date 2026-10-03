"""Payload codec for both directions (ICD §3–§8): PC requests (built by ``core.device``, decoded by the
simulator), FW responses and asynchronous DATA/EVENT frames (encoded by the simulator, decoded by the backend),
INFO (44 B), STATUS (86 B), PARAM_ENTRY (7 B), GET_ALL_PARAMS pages, ``DATA_DTYPE`` and NACK decoding.

Hand-written from the ICD struct layouts; every name/code comes from the generated ``core.protocol_gen``
(no hand-listed codes, ICD §0.3) and every byte value is proven against ``protocol_vectors.json`` (the oracle,
ICD §0.1). Tolerance (ICD §0.2, IF-008): OK responses longer than known are accepted (trailing bytes ignored).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/protocol.py @37c87471 (structure adapted: struct tables per
command, NACK split, PARAM_ENTRY via params_gen; new bend-stand command set and layouts).

Implements: IF-001, IF-005, IF-006, IF-008, IF-009, IF-012, FW-CMD-003 (NACK text)
"""
from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import BoardStatus, DeviceInfo, bit_names

Cmd = pg.Cmd
Status = pg.Status

# ============================================================================== requests (PC → FW)

#: request layout per command: (struct, field names). Empty payload commands map to (None, ()).
_REQ: dict[Cmd, tuple[struct.Struct | None, tuple[str, ...]]] = {
    Cmd.REBOOT: (struct.Struct("<I"), ("magic",)),
    Cmd.GET_ALL_PARAMS: (struct.Struct("<B"), ("page",)),
    Cmd.GET_PARAM: (struct.Struct("<H"), ("id",)),
    Cmd.SET_PARAM: (struct.Struct("<HB4s"), ("id", "type", "value")),
    Cmd.SET_VALID: (struct.Struct("<B"), ("valid",)),
    Cmd.HOME: (struct.Struct("<B"), ("flags",)),
    Cmd.MOVE_ABS: (struct.Struct("<iII"), ("target_um", "v_um_s", "a_um_s2")),
    Cmd.JOG: (struct.Struct("<iIi"), ("v_um_s", "a_um_s2", "bound_um")),
    Cmd.MOVE_UNTIL_LOAD: (struct.Struct("<iIIiB"), ("bound_um", "v_um_s", "a_um_s2", "raw_stop", "cmp")),
    Cmd.STOP: (struct.Struct("<B"), ("mode",)),
}

#: byte offset → field name of every request (E_RANGE detail decoding, §4.7)
REQ_FIELD_OFFSETS: Mapping[Cmd, Mapping[int, str]] = {}


def _offsets() -> None:
    for cmd, (st, names) in _REQ.items():
        if st is None:
            continue
        offs: dict[int, str] = {}
        pos = 0
        for name, code in zip(names, _codes(st.format), strict=True):
            offs[pos] = name
            pos += struct.calcsize("<" + code)
        REQ_FIELD_OFFSETS[cmd] = offs  # type: ignore[index]


def _codes(fmt: str) -> list[str]:
    out, num = [], ""
    for ch in fmt.lstrip("<"):
        if ch.isdigit():
            num += ch
        else:
            out.append(num + ch)
            num = ""
    return out


_offsets()


def build_request(cmd: Cmd | int, **fields: Any) -> bytes:
    """Request payload of ``cmd`` (LEN fixed per command, ICD §3.2). SET_PARAM: use ``build_set_param``."""
    cmd = Cmd(cmd)
    st, names = _REQ.get(cmd, (None, ()))
    if st is None:
        if fields:
            raise ValueError(f"{cmd.name} takes no arguments")
        return b""
    if cmd == Cmd.SET_PARAM:
        return build_set_param(fields["id"], fields["value"])
    missing = [n for n in names if n not in fields]
    if missing:
        raise ValueError(f"{cmd.name}: missing {missing}")
    payload = st.pack(*(int(fields[n]) for n in names))
    assert len(payload) == pg.CMD_REQ_LEN[cmd]
    return payload


def decode_request(cmd: Cmd | int, payload: bytes) -> dict[str, Any]:
    """Fields of a request payload (simulator side). ``ValueError`` if LEN differs from the ICD."""
    cmd = Cmd(cmd)
    if len(payload) != pg.CMD_REQ_LEN[cmd]:
        raise ValueError(f"{cmd.name}: LEN {len(payload)} != {pg.CMD_REQ_LEN[cmd]}")
    st, names = _REQ.get(cmd, (None, ()))
    if st is None:
        return {}
    if cmd == Cmd.SET_PARAM:
        e = decode_param_entry(payload)
        return {"id": e.id, "type": e.type, "value": e.raw}
    return dict(zip(names, st.unpack(payload), strict=True))


# ============================================================================== PARAM_ENTRY (ICD §7.5)

_ENTRY = struct.Struct("<HB4s")


@dataclass(frozen=True)
class ParamEntry:
    id: int
    type: int
    raw: bytes                       # the 4 wire value bytes as received

    @property
    def meta(self) -> pgen.ParamMeta | None:
        return pgen.BY_ID.get(self.id)

    @property
    def key(self) -> str | None:
        m = self.meta
        return m.key if m else None

    def value(self) -> int | float | bool:
        """Typed value via the dictionary (``ValueError`` on unknown id, wrong type byte or non-zero padding)."""
        m = self.meta
        if m is None:
            raise ValueError(f"unknown parameter id 0x{self.id:04X}")
        if self.type != int(m.type):
            raise ValueError(f"{m.key}: type byte {self.type} != {int(m.type)}")
        return m.unpack(self.raw)

    def encode(self) -> bytes:
        return _ENTRY.pack(self.id, self.type, self.raw)


def encode_param_entry(pid: int, value: Any, ptype: int | None = None) -> bytes:
    """PARAM_ENTRY of a dictionary parameter (range-checked by ``ParamMeta.pack``)."""
    m = pgen.BY_ID[pid]
    return _ENTRY.pack(pid, int(m.type) if ptype is None else ptype, m.pack(value))


_TYPE_FMT: Mapping[int, str] = {1: "<B", 2: "<b", 3: "<H", 4: "<h", 5: "<I", 6: "<i", 7: "<f", 8: "<B", 9: "<B"}


def encode_param_entry_unchecked(pid: int, ptype: int, value: int | float) -> bytes:
    """PARAM_ENTRY packed by the type byte **without** range checks (simulator / test frames only; the SW
    never sends an out-of-range value, ``build_set_param`` refuses it)."""
    fmt = _TYPE_FMT[ptype]
    v = float(value) if ptype == 7 else int(value)
    return _ENTRY.pack(pid, ptype, struct.pack(fmt, v).ljust(4, b"\x00"))


def build_set_param(pid_or_key: int | str, value: Any) -> bytes:
    m = pgen.BY_KEY[pid_or_key] if isinstance(pid_or_key, str) else pgen.BY_ID[pid_or_key]
    if m.type == pgen.ParamType.ENUM and isinstance(value, str):
        value = m.enum_value(value)
    return encode_param_entry(m.id, value)


def decode_param_entry(b: bytes) -> ParamEntry:
    if len(b) != pg.PARAM_ENTRY_LEN:
        raise ValueError(f"PARAM_ENTRY length {len(b)}")
    pid, typ, raw = _ENTRY.unpack(b)
    return ParamEntry(pid, typ, bytes(raw))


# ============================================================================== responses (FW → PC)

@dataclass(frozen=True)
class Response:
    cmd: int
    seq: int
    status: int
    detail: int = 0
    body: bytes = b""

    @property
    def ok(self) -> bool:
        return self.status == Status.OK

    @property
    def status_name(self) -> str:
        return status_name(self.status)


def status_name(status: int) -> str:
    try:
        return Status(status).name
    except ValueError:
        return f"STATUS_{status}"


_NACK = struct.Struct("<BH")


def encode_ok(body: bytes = b"") -> bytes:
    return b"\x00" + bytes(body)


def encode_nack(status: int, detail: int) -> bytes:
    if status == 0:
        raise ValueError("NACK status must be ≠ OK")
    return _NACK.pack(status, detail & 0xFFFF)


def split_response(ftype: int, seq: int, payload: bytes) -> Response:
    """STATUS byte + body (OK) or ``u8 status, u16 detail`` (NACK, exactly 3 bytes; ICD §4.1)."""
    cmd = ftype & 0x7F
    if not payload:
        raise ValueError("empty response payload")
    status = payload[0]
    if status == Status.OK:
        return Response(cmd, seq, 0, 0, bytes(payload[1:]))
    if len(payload) < 3:
        raise ValueError("NACK shorter than 3 bytes")
    st, detail = _NACK.unpack_from(payload)
    return Response(cmd, seq, st, detail, b"")


# ---- INFO (ICD §7.1, 44 B) -----------------------------------------------------------------------------
_INFO = struct.Struct("<BBB3BI12s16sHI")
assert _INFO.size == pg.INFO_LEN


def decode_info(body: bytes) -> DeviceInfo:
    if len(body) < pg.INFO_LEN:
        raise ValueError(f"INFO body {len(body)} < {pg.INFO_LEN}")
    (pmaj, pmin, pv, fa, fb, fc, h, uid, build, count, feats) = _INFO.unpack_from(body)
    return DeviceInfo(pmaj, pmin, pv, (fa, fb, fc), h, uid.hex().upper(),
                      build.split(b"\x00", 1)[0].decode("ascii", "replace"), count, feats)


def encode_info(info: DeviceInfo) -> bytes:
    b = info.build.encode("ascii")[:16]
    return _INFO.pack(info.proto_major, info.proto_minor, info.payload_version, *info.fw_version,
                      info.param_dict_hash, bytes.fromhex(info.uid), b, info.param_count, info.feature_mask)


# ---- STATUS (ICD §7.2, 86 B) ---------------------------------------------------------------------------
_STATUS = struct.Struct("<IIBBHHHBBBBiiiiHHIIIIIHHHHHHIIIBB")
assert _STATUS.size == pg.STATUS_LEN
STATUS_FIELDS: tuple[str, ...] = (
    "uptime_ms", "t_us", "flags", "motion_state", "status", "faults", "io", "home_phase", "halt_src",
    "reset_cause", "sys_flags", "pos_um", "target_um", "pos_steps", "afe_raw_last", "afe_rate_dsps",
    "afe_reinit_count", "rx_frames_ok", "rx_crc_errors", "rx_frame_errors", "rx_overruns", "tx_drops",
    "event_overflows", "loop_max_us", "link_age_ms", "stack_free_min", "nvm_save_ms", "idle_disable_left_s",
    "nvm_record_seq", "nvm_save_uptime_ms", "v_limit_um_s", "pause_src", "reserved",
)


def decode_status(body: bytes) -> BoardStatus:
    if len(body) < pg.STATUS_LEN:
        raise ValueError(f"STATUS body {len(body)} < {pg.STATUS_LEN}")
    return BoardStatus(**dict(zip(STATUS_FIELDS, _STATUS.unpack_from(body), strict=True)))


def encode_status(st: BoardStatus) -> bytes:
    return _STATUS.pack(*(getattr(st, f) for f in STATUS_FIELDS))


# ---- other OK bodies ------------------------------------------------------------------------------------
_PAGE_HDR = struct.Struct("<BBB")


@dataclass(frozen=True)
class ParamPage:
    page: int
    page_count: int
    entries: tuple[ParamEntry, ...]


def decode_param_page(body: bytes) -> ParamPage:
    page, count, n = _PAGE_HDR.unpack_from(body)
    need = _PAGE_HDR.size + n * pg.PARAM_ENTRY_LEN
    if len(body) < need:
        raise ValueError(f"GET_ALL_PARAMS page truncated: {len(body)} < {need}")
    entries = tuple(decode_param_entry(body[3 + i * 7: 10 + i * 7]) for i in range(n))
    return ParamPage(page, count, entries)


def encode_param_page(page: int, page_count: int, entries: Sequence[bytes]) -> bytes:
    return _PAGE_HDR.pack(page, page_count, len(entries)) + b"".join(entries)


def decode_u16(body: bytes) -> int:
    return struct.unpack_from("<H", body)[0]


def decode_u32(body: bytes) -> int:
    return struct.unpack_from("<I", body)[0]


def encode_u16(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)


def encode_u32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


# ============================================================================== DATA (ICD §7.3) / EVENT (§7.4)

DATA_DTYPE = np.dtype([("t_us", "<u4"), ("payload_version", "u1"), ("flags", "u1"), ("afe_raw", "<i4"),
                       ("setpoint_um", "<i4"), ("frame_seq", "<u2"), ("status", "<u2")])
assert DATA_DTYPE.itemsize == pg.DATA_LEN
_DATA = struct.Struct("<IBBiiHH")


@dataclass(frozen=True)
class DataSample:
    t_us: int
    payload_version: int
    flags: int
    afe_raw: int
    setpoint_um: int
    frame_seq: int
    status: int


def decode_data(payload: bytes) -> DataSample:
    if len(payload) < pg.DATA_LEN:
        raise ValueError(f"DATA payload {len(payload)} < {pg.DATA_LEN}")
    return DataSample(*_DATA.unpack_from(payload))


def encode_data(s: DataSample) -> bytes:
    return _DATA.pack(s.t_us & 0xFFFFFFFF, s.payload_version, s.flags & 0xFF, s.afe_raw, s.setpoint_um,
                      s.frame_seq & 0xFFFF, s.status & 0xFFFF)


def decode_data_batch(payloads: Sequence[bytes]) -> np.ndarray:
    """Batch decode with one ``np.frombuffer`` (IF-006); payloads must be PAYLOAD_VERSION-1 sized."""
    return np.frombuffer(b"".join(payloads), dtype=DATA_DTYPE)


_EVENT = struct.Struct("<IHHii")
assert _EVENT.size == pg.EVENT_LEN


@dataclass(frozen=True)
class EventPayload:
    t_us: int
    code: int
    arg: int
    value: int
    value2: int

    @property
    def name(self) -> str:
        return event_name(self.code)


def event_name(code: int) -> str:
    try:
        return pg.Event(code).name
    except ValueError:
        return f"EVENT_{code}"


#: EVENT name → argument enum class (ICD §8.1, Appendix B); bitmask args are handled separately
EVENT_ARG_ENUM: Mapping[str, type] = {
    "BOOT": pg.ResetCause, "STOPPED": pg.StopCause, "MOVE_DONE": pg.MoveDoneReason, "HALT_SET": pg.Source,
    "PAUSED": pg.Source, "PAUSE_CLEARED": pg.PauseClearedReason, "VALID_CLEARED": pg.StopCause,
    "HOME_FAILED": pg.HomeFailReason, "DRIVER_DISABLED": pg.DriverDisabledCause,
    "PARAMS_DEFAULTED": pg.ParamsDefaultedReason, "NVM_ERROR": pg.NvmDetail, "LIMIT_SET": pg.LimitId,
    "LIMIT_CLEARED": pg.LimitId,
}


def event_arg_name(code: int, arg: int) -> str | None:
    name = event_name(code)
    if name == "FAULT_SET":
        return pg.FAULTS_BITS[arg] if 0 <= arg < len(pg.FAULTS_BITS) else f"BIT{arg}"
    if name == "FAULT_CLEARED":
        return ",".join(bit_names(pg.FAULTS_BITS, arg))
    enum = EVENT_ARG_ENUM.get(name)
    if enum is None:
        return None
    try:
        return enum(arg).name
    except ValueError:
        return f"{arg}"


def decode_event(payload: bytes) -> EventPayload:
    if len(payload) < pg.EVENT_LEN:
        raise ValueError(f"EVENT payload {len(payload)} < {pg.EVENT_LEN}")
    return EventPayload(*_EVENT.unpack_from(payload))


def encode_event(e: EventPayload) -> bytes:
    return _EVENT.pack(e.t_us & 0xFFFFFFFF, e.code, e.arg & 0xFFFF, e.value, e.value2)


# ============================================================================== NACK decoding (§4.7)

_RANGE_TEXT: Mapping[tuple[Cmd, str], str] = {
    (Cmd.MOVE_ABS, "target_um"): "target outside the soft limits",
    (Cmd.MOVE_ABS, "v_um_s"): "speed 0 or above the current speed cap v_limit",
    (Cmd.MOVE_ABS, "a_um_s2"): "acceleration above motion.a_max_um_s2",
    (Cmd.JOG, "v_um_s"): "speed above the current speed cap",
    (Cmd.JOG, "a_um_s2"): "acceleration above motion.a_max_um_s2",
    (Cmd.JOG, "bound_um"): "bound not ahead of the axis / outside the soft limits",
    (Cmd.MOVE_UNTIL_LOAD, "bound_um"): "bound outside the soft limits or equal to the position",
    (Cmd.MOVE_UNTIL_LOAD, "v_um_s"): "speed 0 or above the loaded speed cap",
    (Cmd.MOVE_UNTIL_LOAD, "raw_stop"): "raw_stop outside the HX711 range",
    (Cmd.MOVE_UNTIL_LOAD, "cmp"): "cmp not 0 (GE) or 1 (LE)",
}

_CAUSE_HINT: Mapping[str, str] = {
    "ESTOP": "release the E-stop, then Clear E-stop, Enable and Home",
    "HALT": "Clear stop first",
    "FAULT": "clear the fault first",
    "NOT_ENABLED": "enable the driver",
    "NOT_HOMED": "home the axis",
    "LIMIT": "move away from the limit switch",
    "AFE_STALE": "no load-cell data",
    "AFE_SATURATED": "load cell saturated",
    "DRV_UNPOWERED": "driver unpowered — restore power, Enable and Home",
    "DRIVER_ALARM": "driver alarm active",
    "PAUSED": "paused — press Resume",
}


def nack_text(cmd: Cmd | int, status: int, detail: int) -> str:
    """Human text of a NACK (shown verbatim, FW-CMD-003)."""
    try:
        cmd = Cmd(cmd)
        cname = cmd.name
    except ValueError:
        cname = f"0x{int(cmd):02X}"
    name = status_name(status)
    if status in (Status.E_UNKNOWN_CMD, Status.E_LENGTH):
        return f"{name}: internal protocol error ({cname}, detail {detail})"
    if status in (Status.E_PARAM_ID, Status.E_TYPE):
        m = pgen.BY_ID.get(detail)
        return f"{name}: {'parameter ' + m.key if m else f'unknown parameter id 0x{detail:04X}'}"
    if status == Status.E_RANGE:
        if cname == "SET_PARAM" or cname == "GET_PARAM":
            m = pgen.BY_ID.get(detail)
            if m is not None:
                return f"{m.key}: value outside [{m.min}, {m.max}]"
            return f"value out of range (id 0x{detail:04X})"
        offs = REQ_FIELD_OFFSETS.get(cmd) if isinstance(cmd, Cmd) else None
        field = offs.get(detail) if offs else None
        if field is None:
            return f"{cname}: argument out of range (offset {detail})"
        extra = _RANGE_TEXT.get((cmd, field))  # type: ignore[arg-type]
        return f"{cname}: {field} out of range" + (f" ({extra})" if extra else "")
    if status == Status.E_CONFIG:
        m = pgen.BY_ID.get(detail)
        return f"conflicts with {m.key if m else f'0x{detail:04X}'} (hard rule)"
    if status == Status.E_BUSY:
        try:
            b = pg.BusyDetail(detail)
        except ValueError:
            return f"busy ({detail})"
        return "axis moving / homing / stopping" if b == pg.BusyDetail.MOTION else "driver settling"
    if status == Status.E_STATE:
        names = bit_names(pg.BLOCK_BITS, detail)
        return "blocked: " + ", ".join(f"{n} ({_CAUSE_HINT.get(n, '')})" if n in _CAUSE_HINT else n for n in names)
    if status == Status.E_CAUSE_ACTIVE:
        if cname == "ESTOP_CLEAR":
            return "E-stop input still open" if detail == pg.DETAIL_CAUSE_INPUT else f"wait {detail} ms"
        if cname == "HALT_CLEAR":
            return "STOP input still active" if detail == pg.DETAIL_CAUSE_INPUT else f"wait {detail} ms"
        return "cause still present: " + ", ".join(bit_names(pg.FAULTS_BITS, detail))
    if status == Status.E_CONFIRM:
        return "load above home.max_load_raw: confirm homing under load"
    if status == Status.E_NVM:
        return {1: "no valid NVM record / record violates a hard rule", 2: "flash erase/program error",
                3: "flash verify error"}.get(detail, f"NVM error {detail}")
    if status == Status.E_INTERNAL:
        if detail == pg.InternalDetail.NOT_IN_BUILD:
            return "not supported by this FW build"
        return f"internal FW error (detail {detail})"
    return f"{name} (detail {detail})"


def is_block_paused_refusal(status: int, detail: int) -> bool:
    """E_STATE whose BLOCK mask contains PAUSED (expected outcome of the PAUSE race, D-33 k)."""
    return status == Status.E_STATE and bool(detail & pg.Block.PAUSED)


def retry_class(cmd: Cmd | int, payload: bytes = b"") -> pg.RetryClass:
    """SW retry class of a request (ICD §9.3): ``CMD_RETRY``, except JOG with v = 0 → RETRY."""
    cmd = Cmd(cmd)
    if cmd == Cmd.JOG and len(payload) >= 4 and struct.unpack_from("<i", payload)[0] == 0:
        return pg.RetryClass.RETRY
    return pg.CMD_RETRY[cmd]
