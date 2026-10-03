"""Proves the reference codec and command model against the shared vectors.

Verifies: IF-003, IF-004, IF-006, IF-008, IF-010, IF-011, FW-CMD-001, FW-CFG-003, SAF-FW-020
Run:  .venv\\Scripts\\python -m pytest 00_System/tools/tests -q
"""
from __future__ import annotations

import importlib.util
import json
import math
import random
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

import gen_params
import gen_vectors
import ref_cmdcheck as cc
import ref_codec as rc

VEC = Path(gen_vectors.VEC_DIR)
PROTO = json.loads((VEC / "protocol_vectors.json").read_text(encoding="utf-8"))
CHECK = json.loads((VEC / "check_vectors.json").read_text(encoding="utf-8"))
PD = gen_params.load()
MODEL = cc.Model(PD.params)


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return struct.pack("<f", a) == struct.pack("<f", b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    return a == b


# ---------------------------------------------------------------------------- generators
def test_generated_outputs_up_to_date() -> None:
    """IF-010: params_gen.{h,c,py} and ICD Appendix A match params.yaml (--check)."""
    assert gen_params.main(["--check"]) == 0


def test_vectors_up_to_date() -> None:
    """Vector files equal a fresh generation (one generator, R3 P12)."""
    assert gen_vectors.main(["--check"]) == 0


def test_vector_metadata_matches_codec_and_dictionary() -> None:
    assert PROTO["icd_version"] == CHECK["icd_version"] == rc.ICD_VERSION
    assert PROTO["proto_version"] == f"{rc.PROTO_MAJOR}.{rc.PROTO_MINOR}"
    assert PROTO["payload_version"] == rc.PAYLOAD_VERSION
    assert PROTO["param_dict_hash"] == CHECK["param_dict_hash"] == f"0x{PD.hash:08X}"
    assert PROTO["param_count"] == len(PD.params)
    assert PROTO["max_len"] == rc.MAX_LEN and PROTO["params_per_page"] == rc.PARAMS_PER_PAGE


def test_generated_python_module_consistent() -> None:
    spec = importlib.util.spec_from_file_location("params_gen", gen_params.SW_PY_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["params_gen"] = mod          # dataclasses resolve the module by name
    spec.loader.exec_module(mod)
    assert mod.PARAM_DICT_HASH == PD.hash and mod.PARAM_COUNT == len(PD.params)
    spm = mod.BY_KEY["motion.steps_per_mm"]
    assert spm.default == 160.0 and spm.pack(160.0) == bytes.fromhex("00002043")
    assert not mod.BY_KEY["safety.zero_raw"].nvm


# ---------------------------------------------------------------------------- CRC
def test_crc_check_value() -> None:
    """IF-004: CRC-16/CCITT-FALSE check '123456789' -> 0x29B1."""
    assert rc.crc16_ccitt(b"123456789") == 0x29B1


@pytest.mark.parametrize("c", PROTO["crc16"], ids=lambda c: c["name"])
def test_crc_vectors(c: dict[str, Any]) -> None:
    assert f"0x{rc.crc16_ccitt(bytes.fromhex(c['input_hex'])):04X}" == c["crc"]


# ---------------------------------------------------------------------------- frames
@pytest.mark.parametrize("f", PROTO["frames"], ids=lambda f: f["name"])
def test_frame_vector(f: dict[str, Any]) -> None:
    raw = bytes.fromhex(f["frame_hex"])
    p = rc.FrameParser()
    got = p.feed(raw)
    if f["kind"] == "invalid":
        assert got == [] and p.crc_errors == f["expect"]["crc_errors"]
        return
    assert len(got) == 1 and not p.buf
    fr = got[0]
    assert (f"0x{fr.type:02X}", fr.seq, fr.payload.hex().upper(), len(fr.payload)) == (
        f["type"], f["seq"], f["payload_hex"], f["len"])
    assert f"0x{rc.crc16_ccitt(raw[2:-2]):04X}" == f["crc"]
    assert rc.encode_frame(fr.type, fr.seq, fr.payload) == raw
    assert raw[:2] == rc.SYNC and len(raw) == rc.OVERHEAD + len(fr.payload)
    if f.get("framing_only"):
        return
    if f["decoded"].get("value") == "INVALID_PADDING":
        with pytest.raises(ValueError):
            rc.decode_frame(fr)
        return
    assert _eq(rc.decode_frame(fr), f["decoded"])
    expect = (bytes.fromhex(f["canonical_payload_hex"]) if f.get("reencode") is False else fr.payload)
    assert rc.encode_decoded(fr.type, f["decoded"]) == expect


def test_every_command_and_async_type_has_vectors() -> None:
    """IF-012: every command has a request and an OK response vector; DATA and EVENT present."""
    have = {(f["kind"], f["type_name"],
             f["decoded"].get("status") if f["kind"] == "response" else None)
            for f in PROTO["frames"] if "decoded" in f}
    for name in rc.CMD:
        assert ("request", name, None) in have, name
        assert ("response", name, "OK") in have, name
    names = {f["name"] for f in PROTO["frames"]}
    for code in rc.EVENT:
        assert f"event_{code.lower()}" in names
    statuses = {f["decoded"].get("status") for f in PROTO["frames"] if f["kind"] == "response"}
    assert statuses >= set(rc.STATUS)


def test_ping_example_bytes() -> None:
    assert rc.encode_frame(0x01, 0x07) == bytes.fromhex("A55A01070000") + struct.pack(
        "<H", rc.crc16_ccitt(bytes.fromhex("01070000")))


# ---------------------------------------------------------------------------- DATA layout
def test_data_layout_offsets() -> None:
    """IF-006 / FW-STR-003: DATA v1 = 18 B payload, D-05 field order, frame 26 B."""
    d = {"t_us": 0x04030201, "payload_version": 1, "flags": ["VALID"], "afe_raw": -2,
         "setpoint_um": 0x0D0C0B0A, "frame_seq": 0x0F0E, "status": ["DRV_PWR"]}
    pl = rc.encode_data(d)
    assert len(pl) == 18
    assert pl[0:4] == bytes((1, 2, 3, 4)) and pl[4] == 1 and pl[5] == 0x01
    assert pl[6:10] == struct.pack("<i", -2) and pl[10:14] == bytes((0x0A, 0x0B, 0x0C, 0x0D))
    assert pl[14:16] == bytes((0x0E, 0x0F)) and pl[16:18] == struct.pack("<H", 0x8000)
    assert len(rc.encode_frame(rc.ASYNC["DATA"], 0, pl)) == 26


def test_bandwidth_budget() -> None:
    """IF-011: FW->PC traffic at 80 Hz incl. events and responses <= 10 % of 92 160 B/s."""
    cap = 921600 / 10
    data = (rc.OVERHEAD + rc.DATA_LEN) * 80
    events = (rc.OVERHEAD + rc.EVENT_LEN) * 20                   # 20 events/s (generous)
    status = (rc.OVERHEAD + 1 + rc.STATUS_LEN) * 5               # GET_STATUS polled at 5 Hz
    small = (rc.OVERHEAD + 1) * 20                               # 20 other responses/s
    assert data == 2080 and data / cap == pytest.approx(0.02257, abs=1e-4)
    assert (data + events + status + small) / cap < 0.10


def test_decode_data_rejects_unknown_payload_version() -> None:
    pl = bytearray(rc.encode_data({"t_us": 0, "payload_version": 1, "flags": [], "afe_raw": 0,
                                   "setpoint_um": 0, "frame_seq": 0, "status": []}))
    pl[4] = 2
    with pytest.raises(ValueError):
        rc.decode_data(bytes(pl))


# ---------------------------------------------------------------------------- streams
@pytest.mark.parametrize("s", PROTO["streams"], ids=lambda s: s["name"])
def test_stream_vector(s: dict[str, Any]) -> None:
    """IF-003: resync rules reproduce the shared streams exactly."""
    p = rc.FrameParser()
    got: list[rc.Frame] = []
    for c in s["chunks_hex"]:
        got += p.feed(bytes.fromhex(c))
    if s["idle_timeout_at_end"]:
        assert [g.raw.hex().upper() for g in got] == s["expect_frames_before_timeout"]
        got += p.idle_timeout()
    assert [g.raw.hex().upper() for g in got] == s["expect_frames_hex"]
    assert p.counters() == s["expect_counters"]


def test_parser_recovers_all_frames_from_noise() -> None:
    """Randomised: frames separated by noise without 0xA5 are all recovered, in order."""
    rnd = random.Random(20261003)
    frames = [f for f in PROTO["frames"] if f["kind"] not in ("invalid",)]
    for _ in range(50):
        pick = [bytes.fromhex(rnd.choice(frames)["frame_hex"]) for _ in range(8)]
        stream = b"".join(bytes(rnd.choice([b for b in range(256) if b != 0xA5])
                                for _ in range(rnd.randrange(0, 6))) + fr for fr in pick)
        p = rc.FrameParser()
        got: list[rc.Frame] = []
        i = 0
        while i < len(stream):
            n = rnd.randrange(1, 40)
            got += p.feed(stream[i:i + n])
            i += n
        assert [g.raw for g in got] == pick


# ---------------------------------------------------------------------------- check vectors
@pytest.mark.parametrize("v", CHECK["vectors"], ids=lambda v: v["name"])
def test_check_vector(v: dict[str, Any]) -> None:
    """FW-CMD-001 / FW-CFG-003 / SAF-FW-020: model verdict and NACK bytes match the vector."""
    req = v["request"]
    p = rc.FrameParser()
    (fr,) = p.feed(bytes.fromhex(req["frame_hex"]))
    assert fr.payload.hex().upper() == req["payload_hex"] and f"0x{fr.type:02X}" == req["type"]
    st = cc.FwState.from_dict(v["state"])
    status, detail = MODEL.check(st, fr.type, fr.payload)
    assert (status, detail) == (v["expect"]["status"], v["expect"]["detail"])
    if status != "OK":
        (resp,) = rc.FrameParser().feed(bytes.fromhex(v["expect"]["response_frame_hex"]))
        assert resp.type == fr.type | rc.RESP_BIT and resp.seq == fr.seq
        assert rc.decode_response("PING", resp.payload) == {"status": status, "detail": detail}


def test_state_defaults_documented() -> None:
    assert CHECK["state_defaults"] == cc.FwState().__dict__


# Independent anchors taken from SRS acceptance criteria (not derived from the model).
SRS_ANCHORS = {
    "set_safety.load_raw_max_above": ("E_RANGE", None),      # SAF-FW-010: 7 151 122
    "load_raw_max_cap": ("OK", 0),                           # SAF-FW-010: 7 151 121
    "estop_clear_50ms": ("E_CAUSE_ACTIVE", None),            # SAF-FW-006
    "estop_clear_100ms": ("OK", 0),                          # SAF-FW-006
    "jog_before_enable": ("E_STATE", 8),                     # SAF-FW-006 / -018
    "move_not_homed": ("E_STATE", 16),                       # SAF-FW-006
    "home_load_refused": ("E_CONFIRM", 0),                   # SAF-FW-021
    "home_load_confirmed": ("OK", 0),                        # SAF-FW-021
    "jog_toward_start": ("E_STATE", 32),                     # SAF-FW-013
    "jog_away_start": ("OK", 0),                             # SAF-FW-013
    "fault_clear_load": ("OK", 0),                           # SAF-FW-011
    "fault_clear_wiring": ("E_CAUSE_ACTIVE", 8),             # SAF-FW-014
    "move_while_moving": ("E_BUSY", 1),                      # FW-MOT-004
    "spm_change_moving": ("E_BUSY", 1),                      # FW-MOT-009
    "move_speed_steprate_cap": ("E_RANGE", 4),               # FW-MOT-009 (spm 10 000)
    "disable_moving": ("E_BUSY", 1),                         # FW-MOT-008
    "halt_clear_pressed": ("E_CAUSE_ACTIVE", 0xFFFF),        # SAF-FW-022
    "move_alarm_powered": ("E_STATE", 512),                  # D-28
    "move_identical_while_moving": ("E_BUSY", 1),            # IF-005: no retry, no dup-ack
    "jog_bound_behind": ("E_RANGE", 8),                     # F-B-15 bound ahead of the axis
}


@pytest.mark.parametrize("name", sorted(SRS_ANCHORS))
def test_srs_anchor(name: str) -> None:
    v = next(x for x in CHECK["vectors"] if x["name"] == name)
    status, detail = SRS_ANCHORS[name]
    assert v["expect"]["status"] == status
    if detail is not None:
        assert v["expect"]["detail"] == detail


def test_no_relative_move_command() -> None:
    """SAF-FW-020 / IF-009: the command set has no relative move."""
    assert not any("REL" in n for n in rc.CMD)


def test_param_pages() -> None:
    """FW-CFG-002: all parameters returned exactly once over the pages, ascending id."""
    pages = [f for f in PROTO["frames"] if f["name"].startswith("get_all_params_p")
             and f["name"].endswith("_resp")]
    ids = [e["id"] for f in pages for e in f["decoded"]["entries"]]
    assert ids == sorted(p.id for p in PD.params)
    assert len(pages) == math.ceil(len(PD.params) / rc.PARAMS_PER_PAGE)
    assert max(f["len"] for f in pages) <= rc.MAX_LEN
