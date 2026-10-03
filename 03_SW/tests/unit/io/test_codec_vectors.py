"""CRC, framing and payload codec against ``protocol_vectors.json`` in both directions (ICD §12, tools/README).

Every frame vector: PC→FW requests encoded byte-identical from ``decoded`` and decoded back (simulator side);
FW→PC frames decoded to ``decoded`` and re-encoded byte-identical (``reencode: false`` → canonical payload);
``INVALID_PADDING`` rejected; ``streams`` frames **and** counters; ``invalid`` dropped with ``crc_errors``.

Verifies: IF-003, IF-004, IF-006, IF-008, IF-012, IF-001
"""
from __future__ import annotations

import random
from typing import Any

import pytest

from bbs_support import vectors
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import BoardStatus, DeviceInfo, bit_names
from bend_stand.io import crc, framing
from bend_stand.io import protocol as P

PV = vectors("protocol_vectors.json")
FRAMES = PV["frames"]
TYPE_NAMES = {t.name.lower(): int(t) for t in pgen.ParamType}
TYPE_OF_CODE = {v: k for k, v in TYPE_NAMES.items()}


def _names_to_bits(names: list[str], table: tuple[str, ...]) -> int:
    return sum(1 << table.index(n) for n in names)


def _enum(table: type, v: Any) -> int:
    return int(table[v]) if isinstance(v, str) else int(v)


# ------------------------------------------------------------------------------------------------ header

@pytest.mark.req("IF-001", "IF-010")
def test_vector_file_matches_generated_versions() -> None:
    assert PV["icd_version"] == pg.ICD_VERSION
    assert PV["proto_version"] == f"{pg.PROTO_MAJOR}.{pg.PROTO_MINOR}"
    assert PV["payload_version"] == pg.PAYLOAD_VERSION
    assert int(PV["param_dict_hash"], 16) == pgen.PARAM_DICT_HASH
    assert PV["param_count"] == pgen.PARAM_COUNT and PV["params_per_page"] == pg.PARAMS_PER_PAGE
    assert PV["max_len"] == pg.MAX_LEN == framing.MAX_LEN


# ------------------------------------------------------------------------------------------------ CRC

@pytest.mark.req("IF-004")
@pytest.mark.parametrize("v", PV["crc16"], ids=lambda v: v["name"])
def test_crc16_vectors(v: dict) -> None:
    data = bytes.fromhex(v["input_hex"])
    assert crc.crc16_ccitt_false(data) == int(v["crc"], 16)
    assert crc.crc16_ccitt_false_py(data) == int(v["crc"], 16)
    # chunked use continues the CRC
    assert crc.crc16_ccitt_false(data[3:], crc.crc16_ccitt_false(data[:3])) == int(v["crc"], 16)


# ------------------------------------------------------------------------------------------------ streams

@pytest.mark.req("IF-003")
@pytest.mark.parametrize("s", PV["streams"], ids=lambda s: s["name"])
def test_streams(s: dict) -> None:
    dec = framing.FrameDecoder()
    got = []
    for ch in s["chunks_hex"]:
        got += [f.raw.hex().upper() for f in dec.feed(bytes.fromhex(ch))]
    if s.get("expect_frames_before_timeout") is not None:
        assert got == s["expect_frames_before_timeout"]
    if s["idle_timeout_at_end"]:
        got += [f.raw.hex().upper() for f in dec.idle_timeout()]
    assert got == s["expect_frames_hex"]
    assert dec.counters.as_dict() == s["expect_counters"]


@pytest.mark.req("IF-003")
@pytest.mark.parametrize("s", PV["streams"], ids=lambda s: s["name"])
def test_streams_byte_by_byte_with_time(s: dict) -> None:
    """Same result when bytes arrive one at a time with timestamps < 20 ms apart, timeout via poll()."""
    dec = framing.FrameDecoder()
    got, t = [], 0
    for ch in s["chunks_hex"]:
        for b in bytes.fromhex(ch):
            t += 100_000
            got += [f.raw.hex().upper() for f in dec.feed(bytes([b]), t, check_timeout=False)]
    if s["idle_timeout_at_end"]:
        got += [f.raw.hex().upper() for f in dec.poll(t + 19_000_000)]
        got += [f.raw.hex().upper() for f in dec.poll(t + 20_000_000)]
    assert got == s["expect_frames_hex"]
    assert dec.counters.as_dict() == s["expect_counters"]


@pytest.mark.req("IF-003")
def test_feed_timeout_on_late_bytes() -> None:
    dec = framing.FrameDecoder()
    good = bytes.fromhex("A55A810701000090FF")
    assert dec.feed(good[:5], 0) == []
    out = dec.feed(good, 25_000_000)           # ≥ 20 ms of silence before these bytes
    assert [f.raw for f in out] == [good] and dec.counters.timeout_drops == 1
    dec.reset()
    assert dec.buffered == 0


@pytest.mark.req("IF-003", "IF-004")
def test_random_noise_fuzz_resyncs() -> None:
    rng = random.Random(4711)
    good = framing.encode_frame(0x81, 7, b"\x00")
    dec = framing.FrameDecoder()
    n_ok = 0
    for i in range(1000):
        noise = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 1200)))
        n_ok += len(dec.feed(noise))
        dec.idle_timeout()
        out = dec.feed(good)
        assert out and out[-1].raw == good, i
        n_ok += len(out)
        assert dec.buffered <= framing.MIN_BUFFER + 1200
    assert n_ok >= 1000


@pytest.mark.req("IF-003")
def test_encode_frame_limits_and_type_classes() -> None:
    with pytest.raises(ValueError):
        framing.encode_frame(0x100, 0)
    with pytest.raises(ValueError):
        framing.encode_frame(0x01, 0, bytes(161))
    assert framing.FrameEncoder.encode(1, 7) == bytes.fromhex("A55A01070000E477")
    assert [framing.fw_to_pc_type_class(t) for t in (0x81, 0xBF, 0xC0, 0xC1, 0xC5, 0x00, 0x40, 0xD0)] == \
        ["response", "response", "async", "async", "reserved", "invalid", "invalid", "invalid"]
    assert framing.is_command_type(0x3C) and not framing.is_command_type(0x81)


# ------------------------------------------------------------------------------------------------ frames

def _cmd_of(v: dict) -> pg.Cmd | None:
    try:
        return pg.Cmd[v["type_name"]]
    except KeyError:
        return None


def _entry_decoded(e: P.ParamEntry) -> dict:
    meta = pgen.BY_ID[e.id]
    val = meta.unpack(e.raw)
    return {"id": e.id, "type": TYPE_OF_CODE[e.type], "value": int(val) if isinstance(val, bool) else val}


def _info_decoded(i: DeviceInfo) -> dict:
    return {"proto_major": i.proto_major, "proto_minor": i.proto_minor, "payload_version": i.payload_version,
            "fw_version": list(i.fw_version), "param_dict_hash": f"0x{i.param_dict_hash:08X}", "uid": i.uid,
            "build": i.build, "param_count": i.param_count,
            "features": list(bit_names(pg.FEATURES_BITS, i.feature_mask))}


def _status_decoded(s: BoardStatus) -> dict:
    d: dict[str, Any] = {f: getattr(s, f) for f in P.STATUS_FIELDS if f != "reserved"}
    d["flags"] = list(bit_names(pg.DATA_FLAGS_BITS, s.flags))
    d["status"] = list(bit_names(pg.DATA_STATUS_BITS, s.status))
    d["faults"] = list(bit_names(pg.FAULTS_BITS, s.faults))
    d["io"] = list(bit_names(pg.IO_BITS, s.io))
    d["sys_flags"] = list(bit_names(pg.SYS_FLAGS_BITS, s.sys_flags))
    d["motion_state"] = pg.MotionState(s.motion_state).name
    d["home_phase"] = pg.HomePhase(s.home_phase).name
    d["halt_src"] = pg.Source(s.halt_src).name
    d["pause_src"] = pg.Source(s.pause_src).name
    d["reset_cause"] = pg.ResetCause(s.reset_cause).name
    return d


def _status_from(d: dict) -> BoardStatus:
    kw = {f: d[f] for f in P.STATUS_FIELDS if f in d}
    kw["flags"] = _names_to_bits(d["flags"], pg.DATA_FLAGS_BITS)
    kw["status"] = _names_to_bits(d["status"], pg.DATA_STATUS_BITS)
    kw["faults"] = _names_to_bits(d["faults"], pg.FAULTS_BITS)
    kw["io"] = _names_to_bits(d["io"], pg.IO_BITS)
    kw["sys_flags"] = _names_to_bits(d["sys_flags"], pg.SYS_FLAGS_BITS)
    kw["motion_state"] = _enum(pg.MotionState, d["motion_state"])
    kw["home_phase"] = _enum(pg.HomePhase, d["home_phase"])
    kw["halt_src"] = _enum(pg.Source, d["halt_src"])
    kw["pause_src"] = _enum(pg.Source, d["pause_src"])
    kw["reset_cause"] = _enum(pg.ResetCause, d["reset_cause"])
    return BoardStatus(**kw)


def _info_from(d: dict) -> DeviceInfo:
    return DeviceInfo(d["proto_major"], d["proto_minor"], d["payload_version"], tuple(d["fw_version"]),
                      int(d["param_dict_hash"], 16), d["uid"], d["build"], d["param_count"],
                      _names_to_bits(d["features"], pg.FEATURES_BITS))


def _decode_response(cmd: pg.Cmd, r: P.Response) -> dict:
    if not r.ok:
        return {"status": r.status_name, "detail": r.detail}
    d: dict[str, Any] = {"status": "OK"}
    if cmd == pg.Cmd.GET_INFO:
        d["info"] = _info_decoded(P.decode_info(r.body))
    elif cmd == pg.Cmd.GET_STATUS:
        d["board_status"] = _status_decoded(P.decode_status(r.body))
    elif cmd == pg.Cmd.GET_ALL_PARAMS:
        pg_ = P.decode_param_page(r.body)
        d.update(page=pg_.page, page_count=pg_.page_count, entries=[_entry_decoded(e) for e in pg_.entries])
    elif cmd in (pg.Cmd.GET_PARAM, pg.Cmd.SET_PARAM):
        d["entry"] = _entry_decoded(P.decode_param_entry(r.body[:7]))
    elif cmd == pg.Cmd.SET_VALID:
        d["t_us"] = P.decode_u32(r.body)
    elif cmd == pg.Cmd.ENABLE:
        d["settle_ms"] = P.decode_u16(r.body)
    elif cmd == pg.Cmd.FAULT_CLEAR:
        d["cleared"] = list(bit_names(pg.FAULTS_BITS, P.decode_u16(r.body)))
    return d


def _encode_response(cmd: pg.Cmd, d: dict) -> bytes:
    if d["status"] != "OK":
        return P.encode_nack(int(pg.Status[d["status"]]), d["detail"])
    if cmd == pg.Cmd.GET_INFO:
        return P.encode_ok(P.encode_info(_info_from(d["info"])))
    if cmd == pg.Cmd.GET_STATUS:
        return P.encode_ok(P.encode_status(_status_from(d["board_status"])))
    if cmd == pg.Cmd.GET_ALL_PARAMS:
        ents = [P.encode_param_entry(e["id"], e["value"], TYPE_NAMES[e["type"]]) for e in d["entries"]]
        return P.encode_ok(P.encode_param_page(d["page"], d["page_count"], ents))
    if cmd in (pg.Cmd.GET_PARAM, pg.Cmd.SET_PARAM):
        e = d["entry"]
        return P.encode_ok(P.encode_param_entry(e["id"], e["value"], TYPE_NAMES[e["type"]]))
    if cmd == pg.Cmd.SET_VALID:
        return P.encode_ok(P.encode_u32(d["t_us"]))
    if cmd == pg.Cmd.ENABLE:
        return P.encode_ok(P.encode_u16(d["settle_ms"]))
    if cmd == pg.Cmd.FAULT_CLEAR:
        return P.encode_ok(P.encode_u16(_names_to_bits(d["cleared"], pg.FAULTS_BITS)))
    return P.encode_ok()


def _data_decoded(s: P.DataSample) -> dict:
    return {"t_us": s.t_us, "payload_version": s.payload_version,
            "flags": list(bit_names(pg.DATA_FLAGS_BITS, s.flags)), "afe_raw": s.afe_raw,
            "setpoint_um": s.setpoint_um, "frame_seq": s.frame_seq,
            "status": list(bit_names(pg.DATA_STATUS_BITS, s.status))}


def _single_frame(v: dict) -> framing.Frame:
    out = framing.FrameDecoder().feed(bytes.fromhex(v["frame_hex"]))
    assert len(out) == 1
    return out[0]


_REQ = [v for v in FRAMES if v.get("kind") == "request"]
_RESP = [v for v in FRAMES if v.get("kind") == "response"]
_ASYNC = [v for v in FRAMES if v.get("kind") == "async"]
_INVALID = [v for v in FRAMES if v.get("kind") == "invalid"]


@pytest.mark.req("IF-005", "IF-012", "IF-009")
@pytest.mark.parametrize("v", _REQ, ids=lambda v: v["name"])
def test_request_vectors(v: dict) -> None:
    fr = _single_frame(v)
    assert (fr.type, fr.seq, fr.payload.hex().upper()) == (int(v["type"], 16), v["seq"], v["payload_hex"])
    cmd = _cmd_of(v)
    if v.get("framing_only") or cmd is None:
        if cmd is not None:     # wrong LEN: the simulator's decoder refuses it
            with pytest.raises(ValueError):
                P.decode_request(cmd, fr.payload)
        return
    d = v["decoded"]
    if cmd == pg.Cmd.SET_PARAM and d["value"] == "INVALID_PADDING":
        e = P.decode_param_entry(fr.payload)
        with pytest.raises(ValueError):
            e.value()
        return
    if cmd == pg.Cmd.SET_PARAM:
        meta = pgen.BY_ID.get(d["id"])
        if meta is not None and TYPE_NAMES[d["type"]] == int(meta.type) and meta.in_range(d["value"]):
            assert P.build_set_param(d["id"], d["value"]) == fr.payload
        elif meta is not None:                 # the SW never sends it: the builder refuses
            with pytest.raises((ValueError, KeyError)):
                if TYPE_NAMES[d["type"]] != int(meta.type):
                    raise ValueError("type")
                P.build_set_param(d["id"], d["value"])
        payload = P.encode_param_entry_unchecked(d["id"], TYPE_NAMES[d["type"]], d["value"])
        e = P.decode_param_entry(fr.payload)
        assert (e.id, TYPE_OF_CODE[e.type]) == (d["id"], d["type"])
    else:
        payload = P.build_request(cmd, **d)
        assert P.decode_request(cmd, fr.payload) == d
    assert framing.encode_frame(int(cmd), v["seq"], payload).hex().upper() == v["frame_hex"]


@pytest.mark.req("IF-005", "IF-008", "IF-012")
@pytest.mark.parametrize("v", _RESP, ids=lambda v: v["name"])
def test_response_vectors(v: dict) -> None:
    fr = _single_frame(v)
    assert framing.fw_to_pc_type_class(fr.type) == "response"
    if v.get("framing_only"):
        assert fr.payload.hex().upper() == v["payload_hex"]
        return
    cmd = _cmd_of(v) or (fr.type & 0x7F)          # unknown TYPE stays an int
    r = P.split_response(fr.type, fr.seq, fr.payload)
    assert _decode_response(cmd, r) == v["decoded"]
    if "detail_names" in v:
        assert list(bit_names(pg.BLOCK_BITS if r.status == pg.Status.E_STATE else pg.FAULTS_BITS, r.detail)) == \
            v["detail_names"]
    payload = _encode_response(cmd, v["decoded"])
    if v.get("reencode") is False:
        assert payload.hex().upper() == v["canonical_payload_hex"]
    else:
        assert framing.encode_frame(fr.type, fr.seq, payload).hex().upper() == v["frame_hex"]
    if not r.ok:
        assert P.nack_text(cmd, r.status, r.detail)


@pytest.mark.req("IF-006", "IF-012")
@pytest.mark.parametrize("v", _ASYNC, ids=lambda v: v["name"])
def test_async_vectors(v: dict) -> None:
    fr = _single_frame(v)
    assert framing.fw_to_pc_type_class(fr.type) == "async"
    d = v["decoded"]
    if fr.type == pg.AsyncType.DATA:
        s = P.decode_data(fr.payload)
        assert _data_decoded(s) == d
        assert fr.seq == s.frame_seq & 0xFF
        arr = P.decode_data_batch([fr.payload, fr.payload])
        assert arr.shape == (2,) and int(arr["afe_raw"][1]) == s.afe_raw and int(arr["t_us"][0]) == s.t_us
        enc = P.encode_data(P.DataSample(d["t_us"], d["payload_version"],
                                         _names_to_bits(d["flags"], pg.DATA_FLAGS_BITS), d["afe_raw"],
                                         d["setpoint_um"], d["frame_seq"],
                                         _names_to_bits(d["status"], pg.DATA_STATUS_BITS)))
    else:
        e = P.decode_event(fr.payload)
        assert {"t_us": e.t_us, "code": e.name, "arg": e.arg, "value": e.value, "value2": e.value2} == d
        enc = P.encode_event(P.EventPayload(d["t_us"], int(pg.Event[d["code"]]), d["arg"], d["value"],
                                            d["value2"]))
        assert P.event_arg_name(e.code, e.arg) is None or isinstance(P.event_arg_name(e.code, e.arg), str)
    assert framing.encode_frame(fr.type, fr.seq, enc).hex().upper() == v["frame_hex"]


@pytest.mark.req("IF-004")
@pytest.mark.parametrize("v", _INVALID, ids=lambda v: v["name"])
def test_invalid_vectors(v: dict) -> None:
    dec = framing.FrameDecoder()
    assert dec.feed(bytes.fromhex(v["frame_hex"])) + dec.idle_timeout() == v["expect"]["frames"]
    assert dec.counters.crc_errors == v["expect"]["crc_errors"]


@pytest.mark.req("IF-012")
def test_every_command_has_a_request_vector_and_builder() -> None:
    names = {v["type_name"] for v in _REQ}
    for cmd in pg.Cmd:
        assert cmd.name in names, cmd
        assert len(P.build_request(cmd, **({} if cmd not in P._REQ else  # noqa: SLF001
                                          {n: 0 for n in P._REQ[cmd][1]}))) == pg.CMD_REQ_LEN[cmd] \
            if cmd != pg.Cmd.SET_PARAM else True


# ------------------------------------------------------------------------------------------------ misc codec

@pytest.mark.req("IF-008")
def test_tolerance_and_errors() -> None:
    with pytest.raises(ValueError):
        P.split_response(0x81, 0, b"")
    with pytest.raises(ValueError):
        P.split_response(0x81, 0, b"\x05\x00")
    with pytest.raises(ValueError):
        P.decode_info(bytes(10))
    with pytest.raises(ValueError):
        P.decode_status(bytes(85))
    with pytest.raises(ValueError):
        P.decode_data(bytes(17))
    with pytest.raises(ValueError):
        P.decode_event(bytes(15))
    with pytest.raises(ValueError):
        P.decode_param_entry(bytes(6))
    with pytest.raises(ValueError):
        P.decode_param_page(bytes([0, 3, 2]) + bytes(7))
    with pytest.raises(ValueError):
        P.encode_nack(0, 0)
    with pytest.raises(ValueError):
        P.build_request(pg.Cmd.PING, x=1)
    with pytest.raises(ValueError):
        P.build_request(pg.Cmd.MOVE_ABS, target_um=1)
    with pytest.raises(ValueError):
        P.ParamEntry(0x9999, 5, bytes(4)).value()
    with pytest.raises(ValueError):
        P.ParamEntry(0x0104, 5, bytes(4)).value()          # type byte mismatch
    assert P.status_name(99) == "STATUS_99" and P.event_name(99) == "EVENT_99"
    assert P.event_arg_name(int(pg.Event.FAULT_SET), 99) == "BIT99"
    assert P.event_arg_name(int(pg.Event.STOPPED), 999) == "999"
    assert P.event_arg_name(int(pg.Event.FAULT_CLEARED), 5) == "LOAD_LIMIT,STEP_FAULT"
    assert P.ParamEntry(0x0104, 1, bytes(4)).key == "afe.settle_discard"
    assert P.build_set_param("afe.rate_sps", "SPS10") == P.encode_param_entry(0x0102, 0)


@pytest.mark.req("IF-005")
def test_retry_class_and_block_paused() -> None:
    jog0 = P.build_request(pg.Cmd.JOG, v_um_s=0, a_um_s2=0, bound_um=pg.JOG_NO_BOUND)
    jog1 = P.build_request(pg.Cmd.JOG, v_um_s=5, a_um_s2=0, bound_um=pg.JOG_NO_BOUND)
    assert P.retry_class(pg.Cmd.JOG, jog0) == pg.RetryClass.RETRY
    assert P.retry_class(pg.Cmd.JOG, jog1) == pg.RetryClass.VERIFY
    assert P.retry_class(pg.Cmd.HALT_CLEAR) == pg.RetryClass.VERIFY      # D-34
    assert P.is_block_paused_refusal(pg.Status.E_STATE, int(pg.Block.PAUSED | pg.Block.HALT))
    assert not P.is_block_paused_refusal(pg.Status.E_STATE, int(pg.Block.HALT))


@pytest.mark.req("FW-CMD-003")
@pytest.mark.parametrize("cmd, st, detail, frag", [
    ("MOVE_ABS", "E_RANGE", 4, "v_um_s"), ("JOG", "E_RANGE", 8, "bound_um"), ("MOVE_ABS", "E_RANGE", 99, "offset"),
    ("SET_PARAM", "E_RANGE", 0x0104, "afe.settle_discard"), ("SET_PARAM", "E_RANGE", 0x9999, "id"),
    ("SET_PARAM", "E_CONFIG", 0x0302, "limits.soft_max_um"), ("SET_PARAM", "E_PARAM_ID", 0x9999, "unknown"),
    ("SET_PARAM", "E_TYPE", 0x0104, "afe.settle_discard"), ("MOVE_ABS", "E_BUSY", 1, "moving"),
    ("ENABLE", "E_BUSY", 2, "settling"), ("MOVE_ABS", "E_BUSY", 9, "busy"),
    ("MOVE_ABS", "E_STATE", 0x0402, "press Resume"), ("ESTOP_CLEAR", "E_CAUSE_ACTIVE", 0xFFFF, "still open"),
    ("HALT_CLEAR", "E_CAUSE_ACTIVE", 0xFFFF, "STOP button"), ("HALT_CLEAR", "E_CAUSE_ACTIVE", 30, "wait 30"),
    ("FAULT_CLEAR", "E_CAUSE_ACTIVE", 8, "LIMIT_WIRING"), ("HOME", "E_CONFIRM", 0, "confirm"),
    ("SAVE_PARAMS", "E_NVM", 2, "erase"), ("SAVE_PARAMS", "E_NVM", 9, "NVM error"),
    ("MOVE_UNTIL_LOAD", "E_INTERNAL", 1, "not supported"), ("PING", "E_INTERNAL", 7, "internal"),
    ("PING", "E_UNKNOWN_CMD", 1, "protocol"),
])
def test_nack_text(cmd: str, st: str, detail: int, frag: str) -> None:
    assert frag in P.nack_text(pg.Cmd[cmd], int(pg.Status[st]), detail)
