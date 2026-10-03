"""Level P — protocol conformance of the production codec against the shared vectors (Validator F, M1).

The vectors (``00_System/tools/vectors/*.json``) and ``ref_codec`` are the oracle; the production modules
``bend_stand.io.framing`` / ``io.protocol`` / ``io.crc`` are the units under test (encode PC→FW, decode FW→PC).

TC-IF-003-01, TC-IF-003-02, TC-IF-004-01, TC-IF-006-01, TC-IF-005-04, TC-IF-012-01, TC-IF-010-01 (vector
version/hash part), TC-SYS-003-01 (units vectors + VV-U).

Verifies: IF-003, IF-004, IF-005, IF-006, IF-009, IF-010, IF-012, SYS-003
"""
from __future__ import annotations

import json
import random
import struct
from pathlib import Path

import pytest

import ref_codec as rc
from oracle import f_ref

VEC = Path(__file__).resolve().parents[3] / "00_System" / "tools" / "vectors"
PV = json.loads((VEC / "protocol_vectors.json").read_text(encoding="utf-8"))
UV = json.loads((VEC / "units_vectors.json").read_text(encoding="utf-8"))
FRAMES = PV["frames"]
REQ = [f for f in FRAMES if f["kind"] == "request"]
FW2PC = [f for f in FRAMES if f["kind"] in ("response", "async")]
INVALID = [f for f in FRAMES if f["kind"] == "invalid"]


def _ids(v):
    return [x["name"] for x in v]


# --------------------------------------------------------------------------------------------- CRC / framing

@pytest.mark.req("IF-004")
@pytest.mark.parametrize("v", PV["crc16"], ids=_ids(PV["crc16"]))
def test_tc_if_004_01_crc_vectors(v):
    """TC-IF-004-01: production CRC-16/CCITT-FALSE equals every vector (check 0x29B1, empty 0xFFFF)."""
    # Verifies: IF-004
    from bend_stand.io.crc import crc16_ccitt_false

    assert crc16_ccitt_false(bytes.fromhex(v["input_hex"])) == int(v["crc"], 16)


@pytest.mark.req("IF-004")
def test_tc_if_004_01_check_value_first_principles():
    # Verifies: IF-004 (SRS AC: "123456789" → 0x29B1)
    from bend_stand.io.crc import crc16_ccitt_false

    assert crc16_ccitt_false(b"123456789") == 0x29B1
    assert crc16_ccitt_false(b"") == 0xFFFF


@pytest.mark.req("IF-003", "IF-004")
@pytest.mark.parametrize("v", PV["streams"], ids=_ids(PV["streams"]))
def test_tc_if_003_01_stream_vectors(v):
    """TC-IF-003-01: FrameDecoder on every `streams` vector: frames AND counters equal the vector."""
    # Verifies: IF-003, IF-004
    from bend_stand.io.framing import FrameDecoder

    dec = FrameDecoder()
    got = []
    for ch in v["chunks_hex"]:
        got += [f.raw.hex().upper() for f in dec.feed(bytes.fromhex(ch))]
    if v.get("expect_frames_before_timeout") is not None:
        assert got == v["expect_frames_before_timeout"]
    if v["idle_timeout_at_end"]:
        got += [f.raw.hex().upper() for f in dec.idle_timeout()]
    assert got == [h.upper() for h in v["expect_frames_hex"]]
    c = dec.counters.as_dict()
    for k, exp in v["expect_counters"].items():
        assert c[k] == exp, (k, c)


@pytest.mark.req("IF-003", "IF-004")
@pytest.mark.parametrize("seed", [12345, 4711])
def test_tc_if_003_01_fuzz_noise_with_inserted_frames(seed):
    """TC-IF-003-01 fuzz: 1 MB of seeded noise with 1 000 valid frames at random positions and random chunking;
    production decoder == ref_codec.FrameParser (frames and counters); every inserted frame recovered."""
    # Verifies: IF-003, IF-004
    from bend_stand.io.framing import FrameDecoder

    rnd = random.Random(seed)
    pool = [bytes.fromhex(f["frame_hex"]) for f in FW2PC]
    noise = bytearray(rnd.getrandbits(8) for _ in range(1_000_000))
    inserted = []
    for pos in sorted(rnd.randrange(len(noise)) for _ in range(1000)):
        inserted.append((pos, rnd.choice(pool)))
    stream = bytearray()
    last = 0
    for pos, fr in inserted:
        stream += noise[last:pos] + fr
        last = pos
    stream += noise[last:]
    dec, ref = FrameDecoder(), rc.FrameParser()
    got, exp = [], []
    i = 0
    while i < len(stream):
        n = rnd.randint(1, 600)
        chunk = bytes(stream[i:i + n])
        got += [f.raw for f in dec.feed(chunk)]
        exp += [f.raw for f in ref.feed(chunk)]
        i += n
    got += [f.raw for f in dec.idle_timeout()]
    exp += [f.raw for f in ref.idle_timeout()]
    assert got == exp
    rc_c = ref.counters()
    pc = dec.counters.as_dict()
    for k in ("frames_ok", "crc_errors", "len_errors", "timeout_drops"):
        assert pc[k] == rc_c[k], k
    # every inserted frame was recovered (noise may add a few random-but-valid frames on top, never fewer)
    from collections import Counter

    need, have = Counter(fr for _p, fr in inserted), Counter(got)
    assert all(have[f] >= n for f, n in need.items())


@pytest.mark.req("IF-004")
@pytest.mark.parametrize("v", INVALID, ids=_ids(INVALID))
def test_tc_if_004_01_bad_crc_frames_dropped_and_counted(v):
    # Verifies: IF-004
    from bend_stand.io.framing import FrameDecoder

    dec = FrameDecoder()
    out = dec.feed(bytes.fromhex(v["frame_hex"])) + dec.idle_timeout()
    assert [f.raw.hex().upper() for f in out] == v["expect"]["frames"]
    assert dec.counters.as_dict()["crc_errors"] == v["expect"]["crc_errors"]


# --------------------------------------------------------------------------------------------- codec

def _encode_request_production(name: str, decoded: dict) -> bytes:
    from bend_stand.core import protocol_gen as pg
    from bend_stand.io import protocol as P

    cmd = pg.Cmd[name]
    if name == "SET_PARAM":
        return P.encode_param_entry_unchecked(decoded["id"], rc.PTYPE_CODE[decoded["type"]], decoded["value"])
    return P.build_request(cmd, **decoded)


@pytest.mark.req("IF-003", "IF-009", "IF-012")
@pytest.mark.parametrize("v", REQ, ids=_ids(REQ))
def test_tc_if_003_02_requests_byte_identical(v):
    """TC-IF-003-02: every PC→FW request encoded by the production builders is byte-identical to the vector
    (payload and whole frame incl. CRC)."""
    # Verifies: IF-003, IF-009, IF-012
    from bend_stand.io.framing import encode_frame

    if v["type_name"] == "SET_PARAM" and v["decoded"].get("value") == "INVALID_PADDING":
        from bend_stand.io import protocol as P                     # malformed on purpose: decoders reject it
        with pytest.raises(ValueError):
            P.decode_param_entry(bytes.fromhex(v["payload_hex"])).value()
        return
    if v.get("framing_only") or v["type_name"] not in rc.CMD:
        from bend_stand.io.framing import FrameDecoder               # frame layer only (e.g. wrong LEN, max LEN)
        fr = FrameDecoder().feed(bytes.fromhex(v["frame_hex"]))
        assert len(fr) == 1 and fr[0].payload.hex().upper() == v["payload_hex"].upper()
        return
    payload = _encode_request_production(v["type_name"], v["decoded"])
    assert payload.hex().upper() == v["payload_hex"].upper()
    assert encode_frame(int(v["type"], 16), v["seq"], payload).hex().upper() == v["frame_hex"].upper()


def _norm(x):
    """Production decode → comparable plain values (names for flags handled by the caller)."""
    if isinstance(x, (list, tuple)):
        return [_norm(i) for i in x]
    return x


@pytest.mark.req("IF-003", "IF-005", "IF-006", "IF-008")
@pytest.mark.parametrize("v", FW2PC, ids=_ids(FW2PC))
def test_tc_if_003_02_fw_to_pc_decoded(v):
    """TC-IF-003-02: every FW→PC frame vector is decoded by the production decoders to the vector's fields
    (ref_codec re-encode check for `reencode: false` items; longer OK responses accepted)."""
    # Verifies: IF-003, IF-005, IF-006, IF-008
    from bend_stand.core import protocol_gen as pg
    from bend_stand.io import protocol as P
    from bend_stand.io.framing import FrameDecoder

    frames = FrameDecoder().feed(bytes.fromhex(v["frame_hex"]))
    assert len(frames) == 1
    fr = frames[0]
    d = v["decoded"]
    if v.get("framing_only"):                                       # frame layer only (LEN = 160)
        assert fr.payload.hex().upper() == v["payload_hex"].upper() and len(fr.payload) == v["len"]
        return
    if v["kind"] == "async" and v["type_name"] == "DATA":
        s = P.decode_data(fr.payload)
        assert (s.t_us, s.payload_version, s.afe_raw, s.setpoint_um, s.frame_seq) == (
            d["t_us"], d["payload_version"], d["afe_raw"], d["setpoint_um"], d["frame_seq"])
        assert s.flags == rc.names_to_bits(d["flags"], rc.DATA_FLAGS)
        assert s.status == rc.names_to_bits(d["status"], rc.DATA_STATUS)
        assert fr.seq == d["frame_seq"] & 0xFF                      # ICD §2.2: header SEQ = low byte
        return
    if v["kind"] == "async":                                        # EVENT
        e = P.decode_event(fr.payload)
        assert (e.t_us, e.arg, e.value, e.value2) == (d["t_us"], d["arg"], d["value"], d["value2"])
        exp_code = rc.EVENT[d["code"]] if isinstance(d["code"], str) else d["code"]
        assert e.code == exp_code
        return
    r = P.split_response(fr.type, fr.seq, fr.payload)
    assert r.status == rc.STATUS[d["status"]] if isinstance(d["status"], str) else d["status"]
    if d["status"] != "OK":
        assert r.detail == d["detail"]
        return
    name = v["type_name"]
    body = r.body
    if name == "GET_INFO":
        info = P.decode_info(body)
        di = d["info"]
        assert (info.proto_major, info.proto_minor, info.payload_version, list(info.fw_version),
                info.param_dict_hash, info.uid.upper(), info.build, info.param_count) == (
            di["proto_major"], di["proto_minor"], di["payload_version"], di["fw_version"],
            int(di["param_dict_hash"], 16), di["uid"].upper(), di["build"], di["param_count"])
        assert info.feature_mask == rc.names_to_bits(di["features"], rc.FEATURES)
    elif name == "GET_STATUS":
        st = P.decode_status(body)
        ds = d["board_status"]
        enc = rc.encode_status(ds)                                   # oracle bytes of the decoded dict
        assert enc == body[:rc.STATUS_LEN]
        assert st.flags == rc.names_to_bits(ds["flags"], rc.DATA_FLAGS)
        assert st.faults == rc.names_to_bits(ds["faults"], rc.FAULTS)
        assert st.sys_flags == rc.names_to_bits(ds["sys_flags"], rc.SYS_FLAGS)
        assert st.pos_um == ds["pos_um"] and st.nvm_record_seq == ds["nvm_record_seq"]
        assert st.pause_src == rc.SOURCE.index(ds["pause_src"])
    elif name == "GET_ALL_PARAMS":
        pp = P.decode_param_page(body)
        assert (pp.page, pp.page_count) == (d["page"], d["page_count"])
        assert [(e.id, rc.PTYPE[e.type][0]) for e in pp.entries] == [(e["id"], e["type"]) for e in d["entries"]]
        for e, de in zip(pp.entries, d["entries"]):
            assert rc.pvalue_unpack(e.type, e.raw) == pytest.approx(de["value"])
            assert e.value() == pytest.approx(de["value"])
    elif name in ("GET_PARAM", "SET_PARAM"):
        e = P.decode_param_entry(body)
        assert e.value() == pytest.approx(d["entry"]["value"])
        assert (e.id, rc.PTYPE[e.type][0]) == (d["entry"]["id"], d["entry"]["type"])
    elif name == "SET_VALID":
        assert P.decode_u32(body) == d["t_us"]
    elif name == "ENABLE":
        assert P.decode_u16(body) == d["settle_ms"]
    elif name == "FAULT_CLEAR":
        assert P.decode_u16(body) == rc.names_to_bits(d["cleared"], rc.FAULTS)
    if v.get("reencode") is False:
        assert rc.encode_decoded(int(v["type"], 16), d).hex().upper() == v["canonical_payload_hex"].upper()
    _ = pg  # names checked through rc tables


@pytest.mark.req("IF-006")
def test_tc_if_006_01_numpy_batch_equals_per_frame_decode():
    """TC-IF-006-01: every DATA vector — numpy batch decode == per-frame decode == vector fields."""
    # Verifies: IF-006
    import numpy as np

    from bend_stand.io import protocol as P

    data = [v for v in FW2PC if v["type_name"] == "DATA" and v["kind"] == "async"]
    assert len(data) >= 8
    payloads = [bytes.fromhex(v["payload_hex"]) for v in data]
    arr = P.decode_data_batch(payloads)
    assert P.DATA_DTYPE.itemsize == 18
    for i, v in enumerate(data):
        d = v["decoded"]
        assert int(arr["t_us"][i]) == d["t_us"] and int(arr["afe_raw"][i]) == d["afe_raw"]
        assert int(arr["setpoint_um"][i]) == d["setpoint_um"] and int(arr["frame_seq"][i]) == d["frame_seq"]
        assert int(arr["flags"][i]) == rc.names_to_bits(d["flags"], rc.DATA_FLAGS)
        assert int(arr["status"][i]) == rc.names_to_bits(d["status"], rc.DATA_STATUS)
    assert isinstance(arr, np.ndarray)


# --------------------------------------------------------------------------------------------- tables

@pytest.mark.req("IF-005", "IF-011")
@pytest.mark.parametrize("name", sorted(f_ref.RETRY_CLASS))
def test_tc_if_005_04_retry_class_per_icd(name):
    """TC-IF-005-04: retry class the channel uses for each command == ICD §9.3 (F's table from the ICD text);
    JOG decided by v."""
    # Verifies: IF-005, IF-011
    from bend_stand.core import protocol_gen as pg
    from bend_stand.io import protocol as P

    cmd = pg.Cmd[name]
    assert P.retry_class(cmd).name == f_ref.RETRY_CLASS[name]
    assert (cmd in pg.CMD_PRIORITY) == (name in f_ref.PRIORITY)


@pytest.mark.req("IF-005")
def test_tc_if_005_04_jog_class_by_speed():
    # Verifies: IF-005 (ICD §9.3: JOG 0 = RETRY, JOG ≠ 0 = VERIFY)
    from bend_stand.core import protocol_gen as pg
    from bend_stand.io import protocol as P

    jog0 = rc.encode_request("JOG", {"v_um_s": 0, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
    jog1 = rc.encode_request("JOG", {"v_um_s": -500, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
    assert P.retry_class(pg.Cmd.JOG, jog0).name == "RETRY"
    assert P.retry_class(pg.Cmd.JOG, jog1).name == "VERIFY"


@pytest.mark.req("IF-012", "IF-001")
@pytest.mark.parametrize("name", f_ref.IF012)
def test_tc_if_012_01_every_if012_command_has_a_builder_and_a_vector(name):
    """TC-IF-012-01: every IF-012 command (+ RESUME) exists with the ICD code, has a production builder whose
    LEN equals the ICD fixed LEN, and appears in a request vector."""
    # Verifies: IF-012, IF-001
    from bend_stand.core import protocol_gen as pg

    assert int(pg.Cmd[name]) == rc.CMD[name]
    assert pg.CMD_REQ_LEN[pg.Cmd[name]] == rc.REQ_LEN[name]
    assert any(v["type_name"] == name for v in REQ), f"no request vector for {name}"


@pytest.mark.req("IF-010")
def test_tc_if_010_01_vectors_match_implemented_versions():
    """TC-IF-010-01 (vector part): the vector files carry the implemented ICD version and dictionary hash."""
    # Verifies: IF-010
    from bend_stand.core import params_gen as pgen
    from bend_stand.core import protocol_gen as pg

    assert PV["icd_version"] == pg.ICD_VERSION == rc.ICD_VERSION
    assert int(PV["param_dict_hash"], 16) == pgen.PARAM_DICT_HASH == int(UV["param_dict_hash"], 16)
    assert PV["proto_version"] == f"{pg.PROTO_MAJOR}.{pg.PROTO_MINOR}" and PV["payload_version"] == pg.PAYLOAD_VERSION


# --------------------------------------------------------------------------------------------- units (SYS-003)

def _units(kind, saturated):
    return [v for v in UV[kind] if bool(v.get("saturated")) == saturated]


def _check_units(kind, vectors):
    from bend_stand.calc import motion

    for v in vectors:
        spm = struct.unpack(">f", bytes.fromhex(v["spm_f32_hex"][2:]))[0]
        if kind == "um_to_steps":
            assert motion.um_to_steps(v["um"], spm) == v["steps"] == f_ref.um_to_steps(v["um"], spm), v
        elif kind == "steps_to_um":
            assert motion.steps_to_um(v["steps"], spm) == v["um"] == f_ref.steps_to_um(v["steps"], spm), v
        else:
            assert motion.rate_cap_um_s(v["max_step_rate_hz"], spm) == v["rate_cap_um_s"] ==                 f_ref.rate_cap_um_s(v["max_step_rate_hz"], spm), v                       # ICD §0.1 floor


@pytest.mark.req("SYS-003")
@pytest.mark.parametrize("kind", ["um_to_steps", "steps_to_um", "rate_cap"])
def test_tc_sys_003_01_units_vectors(kind):
    """TC-SYS-003-01: production µm↔steps and rate cap reproduce every (non-saturated) units vector exactly (ties
    incl.); the same vectors are also reproduced by F's oracle (first-principles check of the vector file)."""
    # Verifies: SYS-003
    assert _units(kind, False)
    _check_units(kind, _units(kind, False))


@pytest.mark.req("SYS-003", "IF-009")
@pytest.mark.parametrize("kind", ["um_to_steps", "steps_to_um"])
def test_tc_sys_003_01_units_vectors_saturation(kind):
    """ICD v0.5 §0.1 (OBS-M1-05): results saturate to the int32 range — every `saturated: true` vector (F's oracle
    reproduces them first)."""
    # Verifies: SYS-003, IF-009
    vs = _units(kind, True)
    assert vs
    _check_units(kind, vs)


@pytest.mark.req("SYS-003")
@pytest.mark.parametrize("mm, um", [(0.0005, 1), (-0.0005, -1), (12.3454, 12345), (0.0025, 3), (-0.0025, -3),
                                    (0.0015, 2), (2.5e-3, 3)])
def test_tc_sys_003_01_vv_u_rounding(mm, um):
    """VV-U: mm → µm rounds half away from zero (SYS-003)."""
    # Verifies: SYS-003
    from bend_stand.calc import rounding, units

    assert units.mm_to_um(mm) == um
    assert rounding.round_half_away(2.5) == 3 and rounding.round_half_away(-2.5) == -3
    assert rounding.round_half_away(0.49999999999999994) == 0


@pytest.mark.req("SYS-003")
def test_tc_sys_003_01_tv_u_units():
    """R4 TV-U: 10 kg × g0 = 98.0665 N; kgf per N; counts per kg at 2 mV/V (constant check)."""
    # Verifies: SYS-003
    from bend_stand.calc import units

    assert units.kgf_to_n(10.0) == pytest.approx(98.0665, rel=1e-12)
    assert units.n_to_kgf(1.0) == pytest.approx(0.10197162129779283, rel=1e-12)
    assert units.FS_N == pytest.approx(200 * 9.80665, rel=1e-12)
    assert 2e-3 / 200 * 2**23 * 256 == pytest.approx(21474.83648, rel=1e-12)
