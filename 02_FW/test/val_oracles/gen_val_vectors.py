#!/usr/bin/env python3
"""Validator E: shared vectors -> transient text files for the validator Unity suites (test_val_*).

Independent of Implementer A's pre-script (02_FW/tools/gen_test_vectors.py) and A's test helpers:
the JSON files are read IN PLACE (never copied into the repo) and translated here, with the
Integrator's ref_codec tables and gen_params (sources of truth), into simple whitespace-separated
lines under 02_FW/.pio/val_vectors/ (git-ignored build area). The C suites read these files at run
time (path from $VAL_VEC_DIR, default .pio/val_vectors relative to 02_FW) and compare the count of
executed cases with the `N` line (anti-silent-skip, FW_test_plan §1.1 principle 4).

Gate (exit 1 = refuse to run): every vectors/*.json icd_version == PROTO_ICD_VERSION and
param_dict_hash == PARAM_DICT_HASH of the generated FW headers.

Extra validator oracle cases (units): binary64 left-to-right evaluation + round half away from zero
(ICD §0.1) computed here for ties, negatives and large values that the shared vectors do not contain.

    .venv\\Scripts\\python 02_FW\\test\\val_oracles\\gen_val_vectors.py [--out DIR]

Verifies (test data for): IF-003, IF-004, IF-006, IF-010, FW-CMD-001, FW-CFG-003, SYS-003
"""
from __future__ import annotations

import json
import math
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FW = HERE.parents[1]                       # 02_FW
ROOT = FW.parent
TOOLS = ROOT / "00_System" / "tools"
VEC = TOOLS / "vectors"
sys.path.insert(0, str(TOOLS))
import gen_params  # noqa: E402
import ref_codec as rc  # noqa: E402


def die(msg: str) -> None:
    sys.stderr.write(f"gen_val_vectors: {msg}\n")
    sys.exit(1)


def c_define(path: Path, name: str) -> str:
    m = re.search(r"#define\s+" + name + r"\s+(\S+)", path.read_text(encoding="utf-8"))
    if not m:
        die(f"{name} not in {path}")
    return m.group(1)


def hx(b: bytes | str) -> str:
    if isinstance(b, str):
        b = bytes.fromhex(b)
    return b.hex().upper() if b else "-"


def u32(v: int) -> int:
    return int(v) & 0xFFFFFFFF


# ------------------------------------------------------------------------------------------ params
DICT = gen_params.load()
PBYKEY = {p.key: p for p in DICT.params}
PTCODE = {name: code for code, (name, _, _) in rc.PTYPE.items()}


def raw_of(p, value) -> int:
    """FW raw representation (params_gen.h): ints sign-extended to 32 bit, f32 = bits, enum code."""
    if p.type == "f32":
        return struct.unpack("<I", struct.pack("<f", float(value)))[0]
    if p.type == "enum":
        if isinstance(value, str):
            return next(e.value for e in p.enum if e.name == value)
        return int(value)
    if p.type == "bool":
        return 1 if value else 0
    return u32(int(value))


# ------------------------------------------------------------------------------------------ frames
REQ_FIELDS = {   # decoded keys in the order of the C cmd_req_t member
    "REBOOT": ["magic"], "GET_ALL_PARAMS": ["page"], "GET_PARAM": ["id"],
    "SET_VALID": ["valid"], "HOME": ["flags"], "STOP": ["mode"],
    "MOVE_ABS": ["target_um", "v_um_s", "a_um_s2"],
    "JOG": ["v_um_s", "a_um_s2", "bound_um"],
    "MOVE_UNTIL_LOAD": ["bound_um", "v_um_s", "a_um_s2", "raw_stop", "cmp"],
    "DIAG_MEAS": ["op", "sel", "a", "b"],                 # ICD v0.6 Appendix C
}


def entry_ints(e: dict) -> list:
    code = PTCODE[e["type"]]
    if isinstance(e["value"], str):            # marker such as INVALID_PADDING: wire not comparable
        return [e["id"], code, "X"]
    wire = rc.pvalue_pack(code, e["value"])
    return [e["id"], code, struct.unpack("<I", wire.ljust(4, b"\0"))[0]]


def info_ints(d: dict) -> list[int]:
    b = d["build"].encode("ascii").ljust(16, b"\0")
    uid = bytes.fromhex(d["uid"])
    return ([d["proto_major"], d["proto_minor"], d["payload_version"], *d["fw_version"],
             int(d["param_dict_hash"], 16)] + list(uid) + list(b) +
            [d["param_count"], rc.names_to_bits(d["features"], rc.FEATURES)])


BITS = {"flags": rc.DATA_FLAGS, "status": rc.DATA_STATUS, "faults": rc.FAULTS, "io": rc.IO,
        "sys_flags": rc.SYS_FLAGS}
ENUMS = {"motion_state": rc.MOTION_STATE, "home_phase": rc.HOME_PHASE, "halt_src": rc.SOURCE,
         "reset_cause": rc.RESET_CAUSE, "pause_src": rc.SOURCE}


def status_ints(d: dict) -> list[int]:
    out = []
    for k in rc.STATUS_FIELDS:      # same order as the C status_t declaration (verified in C)
        v = d[k]
        if k in BITS:
            v = rc.names_to_bits(v, BITS[k])
        elif k in ENUMS:
            v = ENUMS[k].index(v) if isinstance(v, str) else int(v)
        out.append(u32(int(v)))
    return out


def frames_lines(pv: dict) -> list[str]:
    lines = []
    for f in pv["frames"]:
        name, kind = f["name"], f["kind"]
        fr = f["frame_hex"]
        if kind == "invalid":
            lines.append(f"IV {name} {fr}")
            continue
        t = int(f["type"], 16)
        seq, pl = f["seq"], f.get("payload_hex", "")
        tn = f.get("type_name")
        d = f.get("decoded", {})
        if kind == "request":
            cname = rc.CMD_NAME.get(t)
            decodable = cname is not None and len(bytes.fromhex(pl)) == rc.REQ_LEN[cname]
            if not decodable:
                vals = []
            elif tn == "SET_PARAM":
                vals = entry_ints(d)
            elif tn in REQ_FIELDS:
                vals = [u32(d[k]) for k in REQ_FIELDS[tn]]
            else:
                vals = []
            defined = 1 if decodable else 0
            lines.append(f"RQ {name} {t} {seq} {hx(pl)} {fr} {defined} {len(vals)} " + " ".join(map(str, vals)))
        elif kind == "response":
            st = d.get("status")
            if not d:
                code, vals = 8, []                         # raw frame (max_len_frame)
            elif st != "OK":
                code, vals = 1, [rc.STATUS[st], d["detail"]]
            elif tn == "GET_INFO":
                code, vals = 2, info_ints(d["info"])
            elif tn == "GET_STATUS":
                code, vals = 3, status_ints(d["board_status"])
            elif tn == "GET_ALL_PARAMS":
                vals = [d["page"], d["page_count"], len(d["entries"])]
                for e in d["entries"]:
                    vals += entry_ints(e)
                code = 4
            elif tn in ("GET_PARAM", "SET_PARAM"):
                code, vals = 5, entry_ints(d["entry"])
            elif tn == "SET_VALID":
                code, vals = 6, [d["t_us"]]
            elif tn == "ENABLE":
                code, vals = 7, [d["settle_ms"]]
            elif tn == "FAULT_CLEAR":
                code, vals = 7, [rc.names_to_bits(d["cleared"], rc.FAULTS)]
            elif tn == "DIAG_MEAS":
                code, vals = 9, [u32(w) for w in d["w"]]  # 16 LE u32 words (ICD v0.6 Appendix C)
            else:
                code, vals = 0, []
            canon = f.get("canonical_payload_hex") if f.get("reencode") is False else None
            re_ok = 0 if canon is not None else 1
            lines.append(f"RS {name} {t} {seq} {re_ok} {hx(canon if canon is not None else pl)} {fr} {code} "
                         f"{len(vals)} " + " ".join(map(str, vals)))
        elif kind == "async" and tn == "DATA":
            vals = [d["t_us"], d["payload_version"], rc.names_to_bits(d["flags"], rc.DATA_FLAGS),
                    u32(d["afe_raw"]), u32(d["setpoint_um"]), d["frame_seq"],
                    rc.names_to_bits(d["status"], rc.DATA_STATUS)]
            lines.append(f"AD {name} {t} {seq} {hx(pl)} {fr} " + " ".join(map(str, vals)))
        elif kind == "async" and tn == "EVENT":
            vals = [d["t_us"], rc.EVENT[d["code"]], d["arg"], u32(d["value"]), u32(d["value2"])]
            lines.append(f"AE {name} {t} {seq} {hx(pl)} {fr} " + " ".join(map(str, vals)))
        else:
            die(f"unhandled frame kind {kind}/{tn} ({name})")
    return lines


def streams_lines(pv: dict) -> list[str]:
    out = []
    for s in pv["streams"]:
        c = s["expect_counters"]
        before = s.get("expect_frames_before_timeout")
        bf = "-1" if before is None else " ".join([str(len(before))] + [hx(x) for x in before])
        out.append(f"ST {s['name']} {len(s['chunks_hex'])} " + " ".join(hx(x) for x in s["chunks_hex"]) +
                   f" {1 if s['idle_timeout_at_end'] else 0} {bf} "
                   f"{len(s['expect_frames_hex'])} " + " ".join(hx(x) for x in s["expect_frames_hex"]) +
                   f" {c['frames_ok']} {c['crc_errors']} {c['len_errors']} {c['timeout_drops']}")
    return out


def corpus_lines(n: int, seed: int) -> list[str]:
    """Differential parser corpus (TC-IF-003-01): random streams, expectation = ref_codec.FrameParser."""
    import random
    rnd = random.Random(seed)
    good = [bytes.fromhex(f["frame_hex"]) for f in json.loads((VEC / "protocol_vectors.json").read_text())["frames"]
            if f["kind"] != "invalid"]
    out = []
    for _ in range(n):
        parts = []
        for _k in range(rnd.randint(1, 4)):
            r = rnd.random()
            if r < 0.35:
                parts.append(rnd.choice(good))
            elif r < 0.5:                                  # random valid frame
                ln = rnd.choice([0, 1, 3, 7, 12, 26, 160])
                parts.append(rc.encode_frame(rnd.randrange(256), rnd.randrange(256), rnd.randbytes(ln)))
            elif r < 0.65:                                 # corrupted copy (one bit)
                b = bytearray(rnd.choice(good))
                i = rnd.randrange(len(b))
                b[i] ^= 1 << rnd.randrange(8)
                parts.append(bytes(b))
            elif r < 0.75:                                 # truncated
                b = rnd.choice(good)
                parts.append(b[:rnd.randrange(1, len(b))])
            elif r < 0.85:                                 # noise incl. sync bytes
                parts.append(bytes(rnd.choice([0xA5, 0x5A, 0x00, 0xFF, rnd.randrange(256)])
                                   for _ in range(rnd.randint(1, 12))))
            elif r < 0.92:                                 # bad length header
                parts.append(bytes([0xA5, 0x5A, rnd.randrange(256), 0]) +
                             int(rnd.choice([161, 200, 0xFFFF])).to_bytes(2, "little"))
            else:
                parts.append(bytes([0xA5]))
        data = b"".join(parts)
        # split into chunks and timeout events
        ev = []
        i = 0
        while i < len(data):
            k = rnd.randint(1, max(1, len(data) - i))
            ev.append(data[i:i + k])
            i += k
            if rnd.random() < 0.15:
                ev.append(None)                            # idle timeout
        if rnd.random() < 0.5:
            ev.append(None)
        ps = rc.FrameParser()
        frames = []
        for e in ev:
            frames += ps.idle_timeout() if e is None else ps.feed(e)
        c = ps.counters()
        out.append("PC " + str(len(ev)) + " " + " ".join("T" if e is None else e.hex().upper() for e in ev) +
                   f" {len(frames)} " + " ".join(f.raw.hex().upper() for f in frames) +
                   f" {c['frames_ok']} {c['crc_errors']} {c['len_errors']} {c['timeout_drops']}")
    return out


# ------------------------------------------------------------------------------------------ check
STATE_ORDER = ["motion_state", "enabling_left_ms", "homed", "pos_um", "estop_latched",
               "estop_input_open", "estop_closed_ms", "halt_latched", "stop_btn_active",
               "stop_btn_released_ms", "faults", "fault_causes", "limit_start", "limit_end",
               "afe_stale", "afe_saturated", "raw", "drv_power", "alm_active", "nvm_record_valid",
               "paused"]


def check_lines(cv: dict) -> list[str]:
    if cv.get("state_schema") != 2:
        die(f"check_vectors state_schema {cv.get('state_schema')} != 2 (validator mapping)")
    base = cv["state_defaults"]
    if sorted(base) != sorted(["params"] + STATE_ORDER):
        die(f"check_vectors state keys changed: {sorted(base)}")
    out = []
    for v in cv["vectors"]:
        st = dict(base)
        st.update(v["state"])
        unknown = set(v["state"]) - set(base)
        if unknown:
            die(f"{v['name']}: unknown state keys {unknown}")
        ints = []
        for k in STATE_ORDER:
            x = st[k]
            if k == "motion_state":
                x = rc.MOTION_STATE.index(x)
            elif k in ("faults", "fault_causes"):
                x = rc.names_to_bits(x, rc.FAULTS)
            ints.append(u32(int(x)))
        prm = []
        for key, val in st["params"].items():
            p = PBYKEY[key]
            prm += [p.id, raw_of(p, val)]
        rq, ex = v["request"], v["expect"]
        pa = ex.get("paused_after")
        out.append(f"CV {v['name']} {int(rq['type'], 16)} {rq['seq']} {hx(rq['payload_hex'])} "
                   f"{rc.STATUS[ex['status']]} {ex['detail']} {ex.get('response_frame_hex') or '-'} "
                   f"{-1 if pa is None else int(bool(pa))} " + " ".join(map(str, ints)) +
                   f" {len(prm) // 2} " + " ".join(map(str, prm)))
    return out


# ------------------------------------------------------------------------------------------ units
def round_half_away(x: float) -> int:
    """C99 round() on a binary64 value (exact: no x + 0.5 double rounding)."""
    a = abs(x)
    r = math.floor(a)
    if a - r >= 0.5:
        r += 1
    return int(-r if x < 0 else r)


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def um_to_steps(um: int, spm: float) -> int:
    return round_half_away(float(um) * spm / 1000.0)


def steps_to_um(steps: int, spm: float) -> int:
    return round_half_away(float(steps) * 1000.0 / spm)


def rate_cap(rate: int, spm: float) -> int:
    return int(math.floor(float(rate) * 1000.0 / spm))


def units_lines(uv: dict) -> list[str]:
    out = []
    for e in uv["um_to_steps"]:
        out.append(f"UM {int(e['spm_f32_hex'], 16)} {e['um']} {e['steps']} S")
    for e in uv["steps_to_um"]:
        out.append(f"SU {int(e['spm_f32_hex'], 16)} {e['steps']} {e['um']} S")
    for e in uv["rate_cap"]:
        out.append(f"RC {int(e['spm_f32_hex'], 16)} {e['max_step_rate_hz']} {e['rate_cap_um_s']} S")
    # validator oracle cases (V): ties, negatives, extremes inside the int32 result range
    spms = [100.0, 160.0, 333.3333, 800.0, 1234.5678, 4000.0, 6400.0, 100000.0]
    ums = [0, 1, -1, 2, -2, 3, 5, -5, 625, -625, 1875, -1875, 999, 1001, 123456, -123456, 400000,
           -10000, 2147483, -2147483, 21474836, -21474836]
    steps = [0, 1, -1, 2, 3, -3, 7, 8, -8, 1234567, -1234567, 320000000, -320000000, 2147483647,
             -2147483648]
    for s in spms:
        sp = f32(s)
        bits = struct.unpack("<I", struct.pack("<f", sp))[0]
        for um in ums:
            st = um_to_steps(um, sp)
            if -2**31 <= st < 2**31:
                out.append(f"UM {bits} {um} {st} V")
        for k in steps:
            um = steps_to_um(k, sp)
            if -2**31 <= um < 2**31:
                out.append(f"SU {bits} {k} {um} V")
        # exact ties: steps = n + 0.5 for um = (2n+1) * 500 / spm when representable
        for n in range(-3, 4):
            um = (2 * n + 1) * 500
            st = um_to_steps(um, sp)
            out.append(f"UM {bits} {um} {st} V")
        for rate in (100, 999, 50000, 100000):
            out.append(f"RC {bits} {rate} {rate_cap(rate, sp)} V")
    return out


def motion_lines(mv: dict) -> list[str]:
    """motion_vectors.json (M2, OI-FW-20) -> test_val_ramp lines (one logical case per line):
    C name f spm_hex v_um_s a_um_s2 d_um_s2 a_stop(0 = none) n_steps n_ev [kind after v]* n_periods sum tol_p tol_s p...
    S p_ticks spm_hex a_stop_um_s2 path(ISR/HALT/STRETCH -> 0/1/2)          (ctrl_stop_paths rows)
    Every case is first re-derived by the validator's independent oracle (ramp_ref.case_periods); a
    disagreement refuses to write the file (never a silent pass on a wrong vector)."""
    import ramp_ref as rr
    out = []
    for c in mv["cases"]:
        mine = rr.case_periods(c)
        if mine != c["periods"]:
            die(f"motion_vectors case {c['name']}: validator oracle disagrees with the shared vector")
        spm_hex = f"{struct.unpack('<I', struct.pack('<f', c['steps_per_mm']))[0]:08X}"
        ev = []
        for e in c["events"]:
            kind = {"controlled_stop": "S", "jog": "J"}[e["event"]]
            ev += [kind, str(e["after_step"]), str(e.get("v_um_s", 0))]
        sum_tol = max(1, math.ceil(c["n_periods"] / 1000))  # D-40 b: ±ceil(N/1000) (= tolerance.sum_ticks)
        if sum_tol != c["tolerance"]["sum_ticks"]:
            die(f"motion_vectors {c['name']}: sum tolerance {c['tolerance']['sum_ticks']} != ceil(N/1000)")
        out.append(" ".join(["C", c["name"], str(c["f_tick"]), spm_hex, str(c["v_um_s"]), str(c["a_um_s2"]),
                             str(c["d_um_s2"]), str(c["a_stop_um_s2"] or 0), str(c["n_steps"]),
                             str(len(c["events"]))] + ev +
                            [str(c["n_periods"]), str(c["sum_ticks"]), str(c["tolerance"]["period_ticks"]),
                             str(sum_tol)] + [str(p) for p in c["periods"]]))
    code = {"ISR": 0, "CLEAN": 1, "STRETCH": 2}
    for r in mv["ctrl_stop_paths"]:
        spm_hex = f"{struct.unpack('<I', struct.pack('<f', r['steps_per_mm']))[0]:08X}"
        out.append(f"S {r['p_ticks']} {spm_hex} {r['a_stop_um_s2']} {code[r['path']]}")
    return out


def loadlim_lines(lv: dict) -> list[str]:
    """loadlim_vectors.json (ICD v0.6, D-40 d) -> test_val_loadlim lines; refused unless the validator's
    independent LoadLimit (latch_ref) reproduces every step.
    L name min max trip_samples regrow n_steps then per step: S raw trip win | C win | G min max ts regrow win"""
    import latch_ref as lr
    out = []
    for c in lv["cases"]:
        mine = lr.run_loadlim_case(c)
        toks = []
        for st, (trip, win) in zip(c["steps"], mine):
            if win != st["regrow_window"] or (st["op"] == "sample" and trip != st["trip"]):
                die(f"loadlim_vectors {c['name']}: validator oracle disagrees")
            if st["op"] == "sample":
                toks += ["S", str(st["raw"]), str(int(st["trip"])), str(int(st["regrow_window"]))]
            elif st["op"] == "fault_clear":
                toks += ["C", str(int(st["regrow_window"]))]
            else:
                toks += ["G", str(st["load_raw_min"]), str(st["load_raw_max"]), str(st["trip_samples"]),
                         str(st["regrow"]), str(int(st["regrow_window"]))]
        i = c["init"]
        out.append(" ".join(["L", c["name"], str(i["load_raw_min"]), str(i["load_raw_max"]), str(i["trip_samples"]),
                             str(i["regrow"]), str(len(c["steps"]))] + toks))
    return out


SNIFFED = ("STOP", "HALT", "PAUSE")


def sniff_oracle(stream: bytes) -> list[tuple[int, int, int, int]]:
    """Validator oracle of the stop sniffer (ICD §2 / FW_design §5.9.3, written from the spec): every position
    where a complete CRC-valid STOP (mode <= 1) / HALT / PAUSE frame with its fixed LEN starts -> (pos, type,
    seq, mode). Embedded frames count too (a false stop is safe-side)."""
    out = []
    for i in range(len(stream) - 7):
        if stream[i] != rc.SYNC0 or stream[i + 1] != rc.SYNC1:
            continue
        t = stream[i + 2]
        name = rc.CMD_NAME.get(t)
        if name not in SNIFFED:
            continue
        ln = stream[i + 4] | (stream[i + 5] << 8)
        if ln != rc.REQ_LEN[name] or i + 8 + ln > len(stream):
            continue
        body = stream[i + 2: i + 6 + ln]
        crc = stream[i + 6 + ln] | (stream[i + 7 + ln] << 8)
        if rc.crc16_ccitt(body) != crc:
            continue
        mode = stream[i + 6] if name == "STOP" else 0
        if name == "STOP" and mode > 1:
            continue
        out.append((i, t, stream[i + 3], mode))
    return out


def sniff_lines(n: int, seed: int) -> list[str]:
    """SN <stream hex> <n_chunks> <chunk sizes...> <n_hits> (pos type seq mode)*"""
    import random
    rnd = random.Random(seed)
    out = []
    for k in range(n):
        parts = []
        for _ in range(rnd.randint(1, 8)):
            c = rnd.random()
            seq = rnd.randrange(256)
            if c < 0.15:
                parts.append(rc.make_frame("STOP", seq, {"mode": rnd.choice([0, 1, 2])}))
            elif c < 0.25:
                parts.append(rc.make_frame("HALT", seq, {}))
            elif c < 0.35:
                parts.append(rc.make_frame("PAUSE", seq, {}))
            elif c < 0.42:
                parts.append(rc.make_frame("RESUME", seq, {}))
            elif c < 0.50:
                parts.append(rc.make_frame("PING", seq, {}))
            elif c < 0.58:                            # bad CRC STOP / HALT
                b = bytearray(rc.make_frame("STOP", seq, {"mode": 0}) if rnd.random() < 0.5
                              else rc.make_frame("HALT", seq, {}))
                b[-1] ^= 0x5A
                parts.append(bytes(b))
            elif c < 0.64:                            # wrong LEN: STOP header with LEN 2
                body = bytes([rc.CMD["STOP"], seq, 2, 0, 0, 0])
                crc = rc.crc16_ccitt(body)
                parts.append(bytes([rc.SYNC0, rc.SYNC1]) + body + bytes([crc & 0xFF, crc >> 8]))
            elif c < 0.72:                            # a HALT frame embedded in a MOVE_ABS payload (12 B)
                h = rc.make_frame("HALT", seq, {})
                pl = h + bytes(rnd.randrange(256) for _ in range(12 - len(h)))
                body = bytes([rc.CMD["MOVE_ABS"], rnd.randrange(256), 12, 0]) + pl
                crc = rc.crc16_ccitt(body)
                parts.append(bytes([rc.SYNC0, rc.SYNC1]) + body + bytes([crc & 0xFF, crc >> 8]))
            else:
                parts.append(bytes(rnd.choice([rc.SYNC0, rc.SYNC1, rnd.randrange(256)])
                                   for _ in range(rnd.randint(1, 12))))
        s = b"".join(parts)
        if k < 40:                                    # exhaustive split points for short streams: 1-byte chunks
            sizes = [1] * len(s) if k % 2 == 0 else [len(s)]
        else:
            sizes, left = [], len(s)
            while left:
                c = min(left, rnd.randint(1, 20))
                sizes.append(c)
                left -= c
        hits = sniff_oracle(s)
        out.append(f"SN {s.hex().upper() or '-'} {len(sizes)} " + " ".join(map(str, sizes)) + f" {len(hits)} "
                   + " ".join(f"{p} {t} {q} {m}" for p, t, q, m in hits))
    return out


def rate_lines(n: int, seed: int) -> list[str]:
    """AR <nominal_sps> <tol_pct> <count> <t_us...> <expect_dsps> <expect_mismatch>   (FW-AFE-004 oracle:
    median of the last 16 periods, 1e7/median rounded, mismatch when |dsps - 10*nominal| > tol% of it;
    timestamps wrap modulo 2^32)."""
    import random
    import statistics
    rnd = random.Random(seed)
    out = []
    for k in range(n):
        nom = rnd.choice([10, 80])
        true_sps = nom * rnd.choice([1.0, 1.02, 0.98, 0.75, 1.25, 0.81, 1.19, 0.125, 8.0])
        per = 1e6 / true_sps
        t = rnd.choice([0, 2**32 - 5_000_000, rnd.randrange(2**32)])
        ts = []
        for i in range(rnd.randint(17, 40)):
            ts.append(t % 2**32)
            d = per * (1 + rnd.gauss(0, 0.002))
            if rnd.random() < 0.08:
                d *= rnd.choice([0.3, 2.0, 3.0])     # outliers (missed edge, double edge)
            t += int(round(d))
        p = [(ts[i + 1] - ts[i]) % 2**32 for i in range(len(ts) - 1)][-16:]
        med = statistics.median(p)
        dsps = int(1e7 / med + 0.5)
        tol = 20
        mm = int(abs(dsps - 10 * nom) * 100 > tol * 10 * nom)
        out.append(f"AR {nom} {tol} {len(ts)} " + " ".join(map(str, ts)) + f" {dsps} {mm}")
    return out


def bits_lines() -> list[str]:
    """Bit positions (ICD §7.6 via ref_codec tables) for the flags/io/sys composition suite."""
    out = []
    for tab, names in (("DF", rc.DATA_FLAGS), ("DS", rc.DATA_STATUS), ("IO", rc.IO), ("SY", rc.SYS_FLAGS)):
        for i, n in enumerate(names):
            if n:                                   # "" = reserved / retired (v0.5: STOP_BTN, D-36)
                out.append(f"B {tab} {n} {i}")
    return out


import os  # noqa: E402
CORPUS_N = int(os.environ.get("VAL_CORPUS_N", "100000"))
CORPUS_SEED = int(os.environ.get("VAL_CORPUS_SEED", "1"))


# ------------------------------------------------------------------------------------------ main
def main() -> int:
    out_dir = FW / ".pio" / "val_vectors"
    if "--out" in sys.argv:
        out_dir = Path(sys.argv[sys.argv.index("--out") + 1])
    gen = FW / "src" / "gen"
    icd = c_define(gen / "proto_gen.h", "PROTO_ICD_VERSION").strip('"')
    h = int(c_define(gen / "params_gen.h", "PARAM_DICT_HASH").rstrip("uUlL"), 16)
    jsons = {n: json.loads((VEC / f"{n}.json").read_text(encoding="utf-8"))
             for n in ("protocol_vectors", "check_vectors", "units_vectors")}
    for n, d in jsons.items():
        if d["icd_version"] != icd or int(d["param_dict_hash"], 16) != h:
            die(f"{n}.json ({d['icd_version']}, {d['param_dict_hash']}) != FW headers ({icd}, 0x{h:08X})")
    pv, cv, uv = jsons["protocol_vectors"], jsons["check_vectors"], jsons["units_vectors"]
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "crc.txt": [f"CR {c['name']} {hx(c['input_hex'])} {int(str(c['crc']), 0)}" for c in pv["crc16"]],
        "frames.txt": frames_lines(pv),
        "streams.txt": streams_lines(pv),
        "check.txt": check_lines(cv),
        "units.txt": units_lines(uv),
        "corpus.txt": corpus_lines(CORPUS_N, CORPUS_SEED),
        "bits.txt": bits_lines(),
        "sniff.txt": sniff_lines(2000, 4242),
        "rate.txt": rate_lines(500, 4343),
    }
    mvf = VEC / "motion_vectors.json"                  # M2 (OI-FW-20); absent before M2 -> no motion.txt
    if mvf.exists():
        mv = json.loads(mvf.read_text(encoding="utf-8"))
        if mv["icd_version"] != icd or int(mv["param_dict_hash"], 16) != h:
            die(f"motion_vectors.json ({mv['icd_version']}, {mv['param_dict_hash']}) != FW headers")
        sys.path.insert(0, str(HERE))
        files["motion.txt"] = motion_lines(mv)
        jsons["motion_vectors"] = mv
    llf = VEC / "loadlim_vectors.json"                 # ICD v0.6 (D-40 d)
    if llf.exists():
        lv = json.loads(llf.read_text(encoding="utf-8"))
        if lv["icd_version"] != icd or int(lv["param_dict_hash"], 16) != h:
            die(f"loadlim_vectors.json ({lv['icd_version']}, {lv['param_dict_hash']}) != FW headers")
        sys.path.insert(0, str(HERE))
        files["loadlim.txt"] = loadlim_lines(lv)
    counts = {}
    for name, lines in files.items():
        hdr = [f"H {icd} {h}", f"N {len(lines)}"]
        (out_dir / name).write_text("\n".join(hdr + lines) + "\n", encoding="ascii", newline="\n")
        counts[name] = len(lines)
    # JSON counts for the report (anti-skip cross-check)
    json_counts = {"crc16": len(pv["crc16"]), "frames": len(pv["frames"]), "streams": len(pv["streams"]),
                   "check": len(cv["vectors"]), "units_shared": uv["um_to_steps"] and
                   len(uv["um_to_steps"]) + len(uv["steps_to_um"]) + len(uv["rate_cap"])}
    (out_dir / "counts.json").write_text(json.dumps({"files": counts, "json": json_counts}, indent=1))
    print(f"val vectors -> {out_dir}: {counts}; json {json_counts}; corpus seed {CORPUS_SEED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
