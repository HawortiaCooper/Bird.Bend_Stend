#!/usr/bin/env python3
"""Parameter-dictionary code generator for the Bird Bend Stand project.

Implements: FW-CFG-001, IF-010
Origin: Thrust_Stand_HAW/00_System/tools/gen_params.py @9473c68 (copied, then trimmed:
        no repeated groups, no fw_owned flag, armed_ok -> moving_ok, bend-stand paths; D-02).

Reads   00_System/specs/params.yaml   (single source of truth: parameters)
        00_System/specs/protocol.yaml (single source of truth: protocol names, via gen_protocol.py)
Writes  02_FW/src/gen/params_gen.h
        02_FW/src/gen/params_gen.c
        02_FW/src/gen/proto_gen.h                     (gen_protocol.py)
        03_SW/src/bend_stand/core/params_gen.py
        03_SW/src/bend_stand/core/protocol_gen.py     (gen_protocol.py)
        00_System/specs/ICD_protocol.md  (Appendix A and the "protocol:<id>" tables, between the
                                          GENERATED markers)

Usage:
    python gen_params.py              # (re)generate all outputs
    python gen_params.py --check      # exit 1 if any output is out of date (CI)
    python gen_params.py --canonical  # print the canonical serialization used for the hash
    python gen_params.py --hash       # print PARAM_DICT_HASH

Only dependency: PyYAML.
"""
from __future__ import annotations

import argparse
import math
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("gen_params.py needs PyYAML:  python -m pip install pyyaml")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_protocol  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parents[1]
YAML_PATH = ROOT / "00_System" / "specs" / "params.yaml"
ICD_PATH = ROOT / "00_System" / "specs" / "ICD_protocol.md"
FW_H_PATH = ROOT / "02_FW" / "src" / "gen" / "params_gen.h"
FW_C_PATH = ROOT / "02_FW" / "src" / "gen" / "params_gen.c"
SW_PY_PATH = ROOT / "03_SW" / "src" / "bend_stand" / "core" / "params_gen.py"

ICD_BEGIN = "<!-- BEGIN GENERATED PARAM TABLE (gen_params.py) -->"
ICD_END = "<!-- END GENERATED PARAM TABLE -->"

SCHEMA_VERSION = 1

# type name -> (wire code, size in bytes, C type, int range)
TYPES: dict[str, tuple[int, int, str, tuple[int, int] | None]] = {
    "u8": (1, 1, "uint8_t", (0, 0xFF)),
    "i8": (2, 1, "int8_t", (-0x80, 0x7F)),
    "u16": (3, 2, "uint16_t", (0, 0xFFFF)),
    "i16": (4, 2, "int16_t", (-0x8000, 0x7FFF)),
    "u32": (5, 4, "uint32_t", (0, 0xFFFFFFFF)),
    "i32": (6, 4, "int32_t", (-0x80000000, 0x7FFFFFFF)),
    "f32": (7, 4, "float", None),
    "bool": (8, 1, "bool", (0, 1)),
    "enum": (9, 1, "uint8_t", (0, 0xFF)),
}

FLAG_MOVING_OK = 0x01
FLAG_NVM = 0x02
FLAG_REBOOT = 0x04

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# names become C struct members and Python identifiers: no keywords of either language
RESERVED = frozenset("""auto break case char const continue default do double else enum extern float for
goto if inline int long register restrict return short signed sizeof static struct switch typedef union
unsigned void volatile while bool true false and as assert async await class def del elif except finally
from global import in is lambda none nonlocal not or pass raise try with yield""".split())
ENUM_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
GENERATED_BANNER = "GENERATED - do not edit"


class DictError(Exception):
    """Validation error in params.yaml."""


# --------------------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class EnumItem:
    name: str
    value: int
    label: str


@dataclass
class Param:
    id: int
    key: str
    name: str
    group: str
    group_label: str
    label: str
    type: str
    unit: str
    min: int | float
    max: int | float
    default: int | float
    enum: tuple[EnumItem, ...]
    moving_ok: bool
    nvm: bool
    reboot_required: bool
    advanced: bool
    decimals: int | None
    description: str
    srs: tuple[str, ...]

    @property
    def flags(self) -> int:
        return ((FLAG_MOVING_OK if self.moving_ok else 0) | (FLAG_NVM if self.nvm else 0)
                | (FLAG_REBOOT if self.reboot_required else 0))

    @property
    def code(self) -> int:
        return TYPES[self.type][0]

    @property
    def size(self) -> int:
        return TYPES[self.type][1]


@dataclass
class GroupDef:
    group: str
    label: str
    c_member: str
    params_yaml: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Dictionary:
    schema_version: int
    dict_version: int
    groups: list[GroupDef]
    params: list[Param]  # sorted by id

    @property
    def hash(self) -> int:
        return zlib.crc32(canonical(self).encode("utf-8")) & 0xFFFFFFFF


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def f32_round(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def f32_bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(x)))[0]


def raw_u32(p: Param, v: int | float) -> int:
    """Value as uint32 bit pattern (signed types sign-extended, f32 = binary32 bits)."""
    if p.type == "f32":
        return f32_bits(v)
    return int(v) & 0xFFFFFFFF


def fmt_value(p: Param, v: int | float) -> str:
    """Canonical (hash) text of a value."""
    if p.type == "f32":
        return "f%08X" % f32_bits(v)
    return str(int(v))


def human_value(p: Param, v: int | float) -> str:
    if p.type == "f32":
        return format(v, ".7g")
    if p.type == "bool":
        return "true" if v else "false"
    if p.type == "enum":
        for e in p.enum:
            if e.value == v:
                return e.name
    return str(int(v))


def c_ident(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", s).upper()


# --------------------------------------------------------------------------------------
# loading / validation
# --------------------------------------------------------------------------------------
def _req(d: dict[str, Any], k: str, where: str) -> Any:
    if k not in d:
        raise DictError(f"{where}: missing '{k}'")
    return d[k]


def _parse_enum(raw: Any, where: str) -> tuple[EnumItem, ...]:
    if not isinstance(raw, list) or not raw:
        raise DictError(f"{where}: enum must be a non-empty list")
    items = []
    for e in raw:
        name = str(_req(e, "name", where))
        value = _req(e, "value", where)
        if not ENUM_NAME_RE.match(name):
            raise DictError(f"{where}: bad enum name {name!r}")
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 255:
            raise DictError(f"{where}: enum value of {name} must be int 0..255")
        items.append(EnumItem(name, value, str(e.get("label", name))))
    names = [i.name for i in items]
    values = sorted(i.value for i in items)
    if len(set(names)) != len(names):
        raise DictError(f"{where}: duplicate enum names")
    if len(set(values)) != len(values):
        raise DictError(f"{where}: duplicate enum values")
    if values != list(range(values[0], values[0] + len(values))):
        raise DictError(f"{where}: enum values must be contiguous, got {values}")
    return tuple(items)


def _check_number(t: str, v: Any, where: str, what: str) -> int | float:
    if isinstance(v, bool):
        raise DictError(f"{where}: {what} must be a number")
    if t == "f32":
        if not isinstance(v, (int, float)):
            raise DictError(f"{where}: {what} must be a number")
        fv = f32_round(float(v))
        if not math.isfinite(fv):
            raise DictError(f"{where}: {what} not finite as f32")
        return fv
    if not isinstance(v, int):
        raise DictError(f"{where}: {what} must be an integer")
    lo, hi = TYPES[t][3]  # type: ignore[misc]
    if not lo <= v <= hi:
        raise DictError(f"{where}: {what} {v} outside {t} range")
    return v


def _build_param(pd: dict[str, Any], g: GroupDef, id_base: int) -> Param:
    name = str(_req(pd, "name", f"group {g.group}"))
    where = f"{g.group}.{name}"
    if not NAME_RE.match(name) or name in RESERVED:
        raise DictError(f"{where}: bad or reserved name")
    off = _req(pd, "id_off", where)
    if not isinstance(off, int) or not 1 <= off <= 0xFF:
        raise DictError(f"{where}: id_off must be 0x01..0xFF")
    pid = id_base + off
    if not 0x0001 <= pid <= 0xFFFE:
        raise DictError(f"{where}: id 0x{pid:04X} invalid")
    t = str(_req(pd, "type", where))
    if t not in TYPES:
        raise DictError(f"{where}: unknown type {t!r}")
    enum: tuple[EnumItem, ...] = ()
    if t == "enum":
        enum = _parse_enum(_req(pd, "enum", where), where)
        if "min" in pd or "max" in pd:
            raise DictError(f"{where}: enum must not have min/max")
        vmin = min(e.value for e in enum)
        vmax = max(e.value for e in enum)
        dname = _req(pd, "default", where)
        match = [e for e in enum if e.name == str(dname)]
        if not match:
            raise DictError(f"{where}: default {dname!r} is not an enum name")
        vdef: int | float = match[0].value
    elif t == "bool":
        if "min" in pd or "max" in pd:
            raise DictError(f"{where}: bool must not have min/max")
        d = _req(pd, "default", where)
        if not isinstance(d, bool):
            raise DictError(f"{where}: bool default must be true/false")
        vmin, vmax, vdef = 0, 1, int(d)
    else:
        if "enum" in pd:
            raise DictError(f"{where}: enum list on non-enum type")
        vmin = _check_number(t, _req(pd, "min", where), where, "min")
        vmax = _check_number(t, _req(pd, "max", where), where, "max")
        vdef = _check_number(t, _req(pd, "default", where), where, "default")
        if not vmin <= vmax:
            raise DictError(f"{where}: min > max")
        if not vmin <= vdef <= vmax:
            raise DictError(f"{where}: default outside [min, max]")
    unit = str(pd.get("unit", ""))
    if not unit.isascii():
        raise DictError(f"{where}: unit must be ASCII")
    if not isinstance(_req(pd, "moving_ok", where), bool):
        raise DictError(f"{where}: moving_ok must be bool")
    for flag in ("nvm", "reboot_required", "advanced"):
        if flag in pd and not isinstance(pd[flag], bool):
            raise DictError(f"{where}: {flag} must be bool")
    srs = pd.get("srs", [])
    if not isinstance(srs, list) or not srs:
        raise DictError(f"{where}: srs must be a non-empty list")
    decimals = pd.get("decimals")
    if decimals is not None and (t != "f32" or not isinstance(decimals, int) or not 0 <= decimals <= 9):
        raise DictError(f"{where}: decimals only for f32, 0..9")
    if t == "f32" and decimals is None:
        decimals = 3
    return Param(
        id=pid, key=f"{g.group}.{name}", name=name, group=g.group, group_label=g.label,
        label=str(pd.get("label", name)), type=t, unit=unit, min=vmin, max=vmax, default=vdef,
        enum=enum, moving_ok=bool(pd["moving_ok"]), nvm=bool(pd.get("nvm", True)),
        reboot_required=bool(pd.get("reboot_required", False)),
        advanced=bool(pd.get("advanced", False)), decimals=decimals,
        description=" ".join(str(pd.get("description", "")).split()),
        srs=tuple(str(s) for s in srs),
    )


def load(path: Path = YAML_PATH) -> Dictionary:
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    sv = _req(doc, "schema_version", "top")
    if sv != SCHEMA_VERSION:
        raise DictError(f"unsupported schema_version {sv}")
    dv = _req(doc, "dict_version", "top")
    groups: list[GroupDef] = []
    params: list[Param] = []
    seen_members: set[str] = set()
    seen_bases: set[int] = set()
    for gd in _req(doc, "groups", "top"):
        gname = str(_req(gd, "group", "group"))
        if not NAME_RE.match(gname) or gname in RESERVED:
            raise DictError(f"bad group name {gname!r}")
        member = str(_req(gd, "c_member", gname))
        if member in seen_members:
            raise DictError(f"duplicate c_member {member}")
        seen_members.add(member)
        base = _req(gd, "id_base", gname)
        if not isinstance(base, int) or base & 0xFF or base in seen_bases:
            raise DictError(f"group {gname}: id_base must be a unique multiple of 0x100")
        seen_bases.add(base)
        pl = _req(gd, "params", gname)
        names = [p.get("name") for p in pl]
        offs = [p.get("id_off") for p in pl]
        if len(set(names)) != len(names) or len(set(offs)) != len(offs):
            raise DictError(f"group {gname}: duplicate name or id_off")
        g = GroupDef(gname, str(gd.get("label", gname)), member, pl)
        groups.append(g)
        for pd in pl:
            params.append(_build_param(pd, g, base))
    ids = [p.id for p in params]
    retired = doc.get("retired_ids", []) or []
    reused = sorted(set(ids) & set(retired))
    if reused:
        raise DictError("retired ids reused: " + ", ".join(f"0x{i:04X}" for i in reused))
    if len(set(ids)) != len(ids):
        raise DictError("duplicate ids")
    params.sort(key=lambda p: p.id)
    return Dictionary(sv, dv, groups, params)


# --------------------------------------------------------------------------------------
# canonical serialization / hash
# --------------------------------------------------------------------------------------
def canonical(d: Dictionary) -> str:
    lines = [f"PARAMDICT/{d.schema_version}\n"]
    for p in d.params:
        enum = ",".join(f"{e.value}={e.name}" for e in sorted(p.enum, key=lambda e: e.value))
        lines.append("|".join([
            f"0x{p.id:04X}", p.key, p.type, p.unit, fmt_value(p, p.min), fmt_value(p, p.max),
            fmt_value(p, p.default), str(p.flags), enum]) + "\n")
    return "".join(lines)


# --------------------------------------------------------------------------------------
# C output
# --------------------------------------------------------------------------------------
def _c_header_comment(d: Dictionary) -> str:
    return (f"/* {GENERATED_BANNER}.\n"
            f" * Source : 00_System/specs/params.yaml (dict_version {d.dict_version}, "
            f"schema {d.schema_version})\n"
            f" * Tool   : 00_System/tools/gen_params.py\n"
            f" * Hash   : PARAM_DICT_HASH = 0x{d.hash:08X}\n"
            f" * Implements: FW-CFG-001, IF-010\n"
            f" */\n")


def gen_c_header(d: Dictionary) -> str:
    o: list[str] = [_c_header_comment(d)]
    o.append("#ifndef PARAMS_GEN_H\n#define PARAMS_GEN_H\n\n"
             "#include <stdbool.h>\n#include <stddef.h>\n#include <stdint.h>\n\n"
             "#ifdef __cplusplus\nextern \"C\" {\n#endif\n\n"
             "#ifndef PARAMS_GEN_WITH_KEYS\n#define PARAMS_GEN_WITH_KEYS 1\n#endif\n\n")
    o.append(f"#define PARAM_DICT_HASH       0x{d.hash:08X}UL\n")
    o.append(f"#define PARAM_DICT_VERSION    {d.dict_version}u\n")
    o.append(f"#define PARAM_SCHEMA_VERSION  {d.schema_version}u\n")
    o.append(f"#define PARAM_COUNT           {len(d.params)}u\n\n")
    o.append("/* Wire type codes (ICD §7.5). */\ntypedef enum {\n")
    for t, (code, *_rest) in TYPES.items():
        o.append(f"    PARAM_T_{t.upper():<5} = {code},\n")
    o.append("} param_type_t;\n\n")
    o.append("/* param_meta_t.flags */\n"
             f"#define PARAM_F_MOVING_OK 0x{FLAG_MOVING_OK:02X}u /* SET_PARAM allowed while moving */\n"
             f"#define PARAM_F_NVM       0x{FLAG_NVM:02X}u /* persisted by SAVE_PARAMS */\n"
             f"#define PARAM_F_REBOOT    0x{FLAG_REBOOT:02X}u /* effective after SAVE + REBOOT */\n\n")
    o.append("/* ICD status codes returned by param_validate_set(). */\n"
             "#define PARAM_ST_OK        0u\n#define PARAM_ST_E_TYPE    4u\n"
             "#define PARAM_ST_E_RANGE   5u\n\n")

    o.append("/* Parameter IDs (stable, ICD Appendix A). */\ntypedef enum {\n")
    for p in d.params:
        o.append(f"    PID_{c_ident(p.key):<30} = 0x{p.id:04X}, /* {p.type} */\n")
    o.append("} param_id_t;\n\n")

    o.append("/* Enumerations. */\n")
    for p in d.params:
        if p.type != "enum":
            continue
        tname = f"{p.group}_{p.name}"
        o.append("typedef enum {\n")
        for e in sorted(p.enum, key=lambda e: e.value):
            o.append(f"    {c_ident(tname)}_{e.name} = {e.value}, /* {e.label} */\n")
        o.append(f"}} {tname}_t;\n\n")

    o.append("/* Parameter storage (RAM image). Member order = params.yaml order. */\n")
    for g in d.groups:
        o.append("typedef struct {\n")
        for pd in g.params_yaml:
            ctype = TYPES[pd["type"]][2]
            comment = pd.get("unit", "")
            o.append(f"    {ctype:<9} {pd['name']};" + (f" /* {comment} */" if comment else "") + "\n")
        o.append(f"}} params_{g.group}_t;\n\n")
    o.append("typedef struct {\n")
    for g in d.groups:
        o.append(f"    params_{g.group}_t {g.c_member};\n")
    o.append("} params_t;\n\n")

    o.append(
        "/* Metadata table entry. Raw values: uint32 bit pattern; signed types are sign-\n"
        " * extended, f32 = IEEE-754 binary32 bits, bool/enum = 0..255. */\n"
        "typedef struct {\n"
        "    uint16_t id;\n"
        "    uint8_t  type;    /* param_type_t */\n"
        "    uint8_t  flags;   /* PARAM_F_* */\n"
        "    uint16_t offset;  /* byte offset in params_t */\n"
        "    uint8_t  size;    /* bytes in params_t and on the wire (1, 2, 4) */\n"
        "    uint32_t min_raw;\n"
        "    uint32_t max_raw;\n"
        "    uint32_t def_raw;\n"
        "#if PARAMS_GEN_WITH_KEYS\n"
        "    const char *key;  /* dotted key, e.g. \"afe.gain_channel\" */\n"
        "#endif\n"
        "} param_meta_t;\n\n")
    o.append("/** Metadata of all parameters, sorted by ascending id. */\n"
             "extern const param_meta_t PARAM_TABLE[PARAM_COUNT];\n\n"
             "/** Binary search by id; NULL if unknown. */\n"
             "const param_meta_t *param_find(uint16_t id);\n\n"
             "/** Index of `m` in PARAM_TABLE (for paging GET_ALL_PARAMS). */\n"
             "static inline uint16_t param_index(const param_meta_t *m) { return (uint16_t)(m - PARAM_TABLE); }\n\n"
             "/** Write every default into `p` (whole struct is zeroed first). */\n"
             "void params_set_defaults(params_t *p);\n\n"
             "/** Current value as raw uint32 (see param_meta_t). */\n"
             "uint32_t param_get_raw(const params_t *p, const param_meta_t *m);\n\n"
             "/** Store a raw value without any check (use after param_validate_set). */\n"
             "void param_set_raw(params_t *p, const param_meta_t *m, uint32_t raw);\n\n"
             "/** Raw value -> 4-byte little-endian wire value (low `size` bytes, rest 0). */\n"
             "void param_raw_to_wire(const param_meta_t *m, uint32_t raw, uint8_t wire[4]);\n\n"
             "/** Check a SET_PARAM request (type byte + 4 value bytes) against `m`.\n"
             " *  Returns PARAM_ST_OK (raw value in *raw_out), PARAM_ST_E_TYPE or PARAM_ST_E_RANGE\n"
             " *  (padding bytes != 0, bool > 1, enum/int out of range, f32 NaN/Inf/out of range).\n"
             " *  Does NOT check the moving state (PARAM_F_MOVING_OK) nor the hard rules. */\n"
             "uint8_t param_validate_set(const param_meta_t *m, uint8_t wire_type,\n"
             "                           const uint8_t wire[4], uint32_t *raw_out);\n\n"
             "/** True if the raw value lies in [min, max] of `m` (used after NVM load). */\n"
             "bool param_raw_in_range(const param_meta_t *m, uint32_t raw);\n\n"
             "/** Replace every out-of-range value in `p` by its default; returns count fixed. */\n"
             "uint16_t params_sanitize(params_t *p);\n\n")
    o.append("#ifdef __cplusplus\n}\n#endif\n\n#endif /* PARAMS_GEN_H */\n")
    return "".join(o)


def gen_c_source(d: Dictionary) -> str:
    o: list[str] = [_c_header_comment(d)]
    o.append('#include "params_gen.h"\n\n#include <string.h>\n\n'
             "#if !PARAMS_GEN_WITH_KEYS\n#define PKEY(k)\n#else\n#define PKEY(k) , k\n#endif\n\n")
    o.append("#define PM(id, type, flags, member, size, mn, mx, df, key) \\\n"
             "    { (uint16_t)(id), (uint8_t)(type), (uint8_t)(flags), \\\n"
             "      (uint16_t)offsetof(params_t, member), (uint8_t)(size), \\\n"
             "      (uint32_t)(mn), (uint32_t)(mx), (uint32_t)(df) PKEY(key) }\n\n")
    o.append("const param_meta_t PARAM_TABLE[PARAM_COUNT] = {\n")
    for p in d.params:
        fl = []
        if p.moving_ok:
            fl.append("PARAM_F_MOVING_OK")
        if p.nvm:
            fl.append("PARAM_F_NVM")
        if p.reboot_required:
            fl.append("PARAM_F_REBOOT")
        flags = "|".join(fl) or "0"
        o.append(f"    /* {p.key}: min {human_value(p, p.min)}, max {human_value(p, p.max)}, "
                 f"default {human_value(p, p.default)} {p.unit} */\n")
        o.append(f"    PM(PID_{c_ident(p.key)}, PARAM_T_{p.type.upper()}, {flags}, "
                 f"{p.group}.{p.name}, {p.size}, 0x{raw_u32(p, p.min):08X}u, "
                 f"0x{raw_u32(p, p.max):08X}u, 0x{raw_u32(p, p.default):08X}u, \"{p.key}\"),\n")
    o.append("};\n\n")
    o.append(r'''const param_meta_t *param_find(uint16_t id)
{
    uint16_t lo = 0u, hi = (uint16_t)PARAM_COUNT;
    while (lo < hi) {
        uint16_t mid = (uint16_t)((lo + hi) / 2u);
        uint16_t mid_id = PARAM_TABLE[mid].id;
        if (mid_id == id) {
            return &PARAM_TABLE[mid];
        }
        if (mid_id < id) {
            lo = (uint16_t)(mid + 1u);
        } else {
            hi = mid;
        }
    }
    return NULL;
}

uint32_t param_get_raw(const params_t *p, const param_meta_t *m)
{
    const uint8_t *src = (const uint8_t *)p + m->offset;
    switch (m->type) {
    case PARAM_T_U8:
    case PARAM_T_ENUM: { uint8_t v; memcpy(&v, src, 1); return v; }
    case PARAM_T_BOOL: { bool v; memcpy(&v, src, sizeof v); return v ? 1u : 0u; }
    case PARAM_T_I8:   { int8_t v; memcpy(&v, src, 1); return (uint32_t)(int32_t)v; }
    case PARAM_T_U16:  { uint16_t v; memcpy(&v, src, 2); return v; }
    case PARAM_T_I16:  { int16_t v; memcpy(&v, src, 2); return (uint32_t)(int32_t)v; }
    case PARAM_T_U32:
    case PARAM_T_I32:
    case PARAM_T_F32:  { uint32_t v; memcpy(&v, src, 4); return v; }
    default: return 0u;
    }
}

void param_set_raw(params_t *p, const param_meta_t *m, uint32_t raw)
{
    uint8_t *dst = (uint8_t *)p + m->offset;
    switch (m->type) {
    case PARAM_T_U8:
    case PARAM_T_ENUM:
    case PARAM_T_I8:   { uint8_t v = (uint8_t)raw; memcpy(dst, &v, 1); break; }
    case PARAM_T_BOOL: { bool v = (raw != 0u); memcpy(dst, &v, sizeof v); break; }
    case PARAM_T_U16:
    case PARAM_T_I16:  { uint16_t v = (uint16_t)raw; memcpy(dst, &v, 2); break; }
    case PARAM_T_U32:
    case PARAM_T_I32:
    case PARAM_T_F32:  { memcpy(dst, &raw, 4); break; }
    default: break;
    }
}

void param_raw_to_wire(const param_meta_t *m, uint32_t raw, uint8_t wire[4])
{
    uint8_t i;
    for (i = 0u; i < 4u; i++) {
        wire[i] = (i < m->size) ? (uint8_t)(raw >> (8u * i)) : 0u;
    }
}

static float raw_to_f32(uint32_t raw)
{
    float f;
    memcpy(&f, &raw, 4);
    return f;
}

bool param_raw_in_range(const param_meta_t *m, uint32_t raw)
{
    switch (m->type) {
    case PARAM_T_F32: {
        float v = raw_to_f32(raw);
        if (v != v) {
            return false; /* NaN */
        }
        return (v >= raw_to_f32(m->min_raw)) && (v <= raw_to_f32(m->max_raw));
    }
    case PARAM_T_I8:
    case PARAM_T_I16:
    case PARAM_T_I32:
        return ((int32_t)raw >= (int32_t)m->min_raw) && ((int32_t)raw <= (int32_t)m->max_raw);
    default:
        return (raw >= m->min_raw) && (raw <= m->max_raw);
    }
}

uint8_t param_validate_set(const param_meta_t *m, uint8_t wire_type,
                           const uint8_t wire[4], uint32_t *raw_out)
{
    uint32_t raw = 0u;
    uint8_t i;
    if (wire_type != m->type) {
        return PARAM_ST_E_TYPE;
    }
    for (i = 0u; i < 4u; i++) {
        if (i < m->size) {
            raw |= (uint32_t)wire[i] << (8u * i);
        } else if (wire[i] != 0u) {
            return PARAM_ST_E_RANGE; /* padding must be zero */
        }
    }
    if (m->type == PARAM_T_I8) {
        raw = (uint32_t)(int32_t)(int8_t)(uint8_t)raw;
    } else if (m->type == PARAM_T_I16) {
        raw = (uint32_t)(int32_t)(int16_t)(uint16_t)raw;
    }
    if (m->type == PARAM_T_F32 && ((raw & 0x7F800000u) == 0x7F800000u)) {
        return PARAM_ST_E_RANGE; /* NaN or Inf */
    }
    if (!param_raw_in_range(m, raw)) {
        return PARAM_ST_E_RANGE;
    }
    *raw_out = raw;
    return PARAM_ST_OK;
}

void params_set_defaults(params_t *p)
{
    uint16_t i;
    memset(p, 0, sizeof *p);
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {
        param_set_raw(p, &PARAM_TABLE[i], PARAM_TABLE[i].def_raw);
    }
}

uint16_t params_sanitize(params_t *p)
{
    uint16_t i, fixed = 0u;
    for (i = 0u; i < (uint16_t)PARAM_COUNT; i++) {
        const param_meta_t *m = &PARAM_TABLE[i];
        if (!param_raw_in_range(m, param_get_raw(p, m))) {
            param_set_raw(p, m, m->def_raw);
            fixed++;
        }
    }
    return fixed;
}
''')
    return "".join(o)


# --------------------------------------------------------------------------------------
# Python output
# --------------------------------------------------------------------------------------
def _py_num(p: Param, v: int | float) -> str:
    if p.type == "f32":
        return repr(float(v))
    return str(int(v))


def _py_default(p: Param) -> str:
    if p.type == "bool":
        return "True" if p.default else "False"
    return _py_num(p, p.default)


PY_PREAMBLE = r'''from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType

'''

PY_CLASSES = r'''

class ParamType(IntEnum):
    """Wire type codes (ICD §7.5)."""

    U8 = 1
    I8 = 2
    U16 = 3
    I16 = 4
    U32 = 5
    I32 = 6
    F32 = 7
    BOOL = 8
    ENUM = 9


TYPE_SIZE: Mapping[ParamType, int] = MappingProxyType({
    ParamType.U8: 1, ParamType.I8: 1, ParamType.U16: 2, ParamType.I16: 2, ParamType.U32: 4,
    ParamType.I32: 4, ParamType.F32: 4, ParamType.BOOL: 1, ParamType.ENUM: 1})
_STRUCT: Mapping[ParamType, str] = MappingProxyType({
    ParamType.U8: "<B", ParamType.I8: "<b", ParamType.U16: "<H", ParamType.I16: "<h",
    ParamType.U32: "<I", ParamType.I32: "<i", ParamType.F32: "<f", ParamType.BOOL: "<B",
    ParamType.ENUM: "<B"})

FLAG_MOVING_OK = 0x01
FLAG_NVM = 0x02
FLAG_REBOOT = 0x04


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


@dataclass(frozen=True, eq=False)
class ParamMeta:
    """Metadata of one parameter (ICD Appendix A, params.yaml)."""

    id: int
    key: str                                  # dotted key, e.g. "afe.gain_channel"
    type: ParamType
    unit: str                                 # ASCII unit, "" = dimensionless
    min: int | float                          # bool: 0, enum: lowest code
    max: int | float                          # bool: 1, enum: highest code
    default: int | float | bool               # bool -> bool, enum -> code, f32 -> binary32-rounded
    description: str
    enum: Mapping[int, str] | None = None     # code -> NAME, iteration order = GUI display order
    group: str = ""                           # GUI group = key prefix ("afe", "motion", ...)
    group_label: str = ""
    label: str = ""                           # short human label
    name: str = ""                            # last key element
    moving_ok: bool = False
    nvm: bool = True
    reboot_required: bool = False
    decimals: int | None = None               # f32 display precision, None for integer types
    advanced: bool = False
    enum_labels: Mapping[int, str] | None = None
    srs: tuple[str, ...] = ()

    @property
    def flags(self) -> int:
        return ((FLAG_MOVING_OK if self.moving_ok else 0) | (FLAG_NVM if self.nvm else 0)
                | (FLAG_REBOOT if self.reboot_required else 0))

    @property
    def size(self) -> int:
        return TYPE_SIZE[self.type]

    def enum_value(self, name: str) -> int:
        """Enum NAME -> code (KeyError if unknown)."""
        for code, n in (self.enum or {}).items():
            if n == name:
                return code
        raise KeyError(f"{self.key}: no enum item {name!r}")

    def in_range(self, value: int | float | bool) -> bool:
        if self.type == ParamType.F32:
            v = float(value)
            return math.isfinite(v) and self.min <= _f32(v) <= self.max
        if isinstance(value, float) and not value.is_integer():
            return False
        if self.type == ParamType.ENUM:
            return int(value) in (self.enum or {})
        return self.min <= int(value) <= self.max

    def pack(self, value: int | float | bool) -> bytes:
        """Value -> 4-byte wire value (ICD §7.5), range-checked (ValueError)."""
        if not self.in_range(value):
            raise ValueError(f"{self.key}: {value!r} outside [{self.min}, {self.max}]")
        v = float(value) if self.type == ParamType.F32 else int(value)
        return struct.pack(_STRUCT[self.type], v).ljust(4, b"\x00")

    def unpack(self, wire: bytes) -> int | float | bool:
        """4-byte wire value -> Python value (bool for BOOL). ValueError on bad padding."""
        if len(wire) != 4 or any(wire[self.size:]):
            raise ValueError(f"{self.key}: bad wire value {bytes(wire).hex()}")
        (v,) = struct.unpack(_STRUCT[self.type], bytes(wire[: self.size]))
        return bool(v) if self.type == ParamType.BOOL else v


def _M(d: dict[int, str]) -> Mapping[int, str]:
    return MappingProxyType(d)


PARAMS: tuple[ParamMeta, ...] = (
'''


def gen_python(d: Dictionary) -> str:
    o: list[str] = []
    o.append(f'"""{GENERATED_BANNER}.\n\n'
             f"Source : 00_System/specs/params.yaml (dict_version {d.dict_version}, "
             f"schema {d.schema_version})\n"
             f"Tool   : 00_System/tools/gen_params.py\n"
             f"Hash   : PARAM_DICT_HASH = 0x{d.hash:08X}\n\n"
             "Parameter metadata for the GUI (typed config fields) and the protocol codec.\n"
             "f32 min/max/default are stored already rounded to binary32, so read-back values\n"
             "compare exactly. Pure Python, no Qt, no PyYAML.\n\n"
             "Implements: IF-010, SW-CFG-001\n"
             '"""\n')
    o.append(PY_PREAMBLE)
    o.append(f"PARAM_DICT_HASH = 0x{d.hash:08X}\n")
    o.append(f"PARAM_DICT_VERSION = {d.dict_version}\n")
    o.append(f"PARAM_SCHEMA_VERSION = {d.schema_version}\n")
    o.append(f"PARAM_COUNT = {len(d.params)}\n")
    o.append(PY_CLASSES)
    for p in d.params:
        if p.enum:
            enum = "_M({" + ", ".join(f"{e.value}: {e.name!r}" for e in p.enum) + "})"
            elabels = "_M({" + ", ".join(f"{e.value}: {e.label!r}" for e in p.enum) + "})"
        else:
            enum = elabels = "None"
        o.append(
            f"    ParamMeta(\n"
            f"        id=0x{p.id:04X}, key={p.key!r}, type=ParamType.{p.type.upper()}, unit={p.unit!r},\n"
            f"        min={_py_num(p, p.min)}, max={_py_num(p, p.max)}, default={_py_default(p)},\n"
            f"        description={p.description!r},\n"
            f"        enum={enum},\n"
            f"        group={p.group!r}, group_label={p.group_label!r}, label={p.label!r}, "
            f"name={p.name!r},\n"
            f"        moving_ok={p.moving_ok}, nvm={p.nvm}, reboot_required={p.reboot_required}, "
            f"decimals={p.decimals!r}, advanced={p.advanced},\n"
            f"        enum_labels={elabels},\n"
            f"        srs={p.srs!r}),\n")
    o.append(")\n\n")
    o.append("BY_ID: Mapping[int, ParamMeta] = MappingProxyType({p.id: p for p in PARAMS})\n")
    o.append("BY_KEY: Mapping[str, ParamMeta] = MappingProxyType({p.key: p for p in PARAMS})\n\n")
    o.append("# GUI grouping in display order: (group key, group label, param keys in yaml order)\n")
    o.append("GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (\n")
    for g in d.groups:
        keys = tuple(f"{g.group}.{pd['name']}" for pd in g.params_yaml)
        o.append(f"    ({g.group!r}, {g.label!r},\n     {keys!r}),\n")
    o.append(")\n\n")
    o.append("assert len(PARAMS) == PARAM_COUNT == len(BY_ID) == len(BY_KEY)\n")
    return "".join(o)


# --------------------------------------------------------------------------------------
# ICD appendix (markdown)
# --------------------------------------------------------------------------------------
def gen_markdown(d: Dictionary) -> str:
    o = [ICD_BEGIN, "",
         f"Generated from `params.yaml` dict_version {d.dict_version} — "
         f"**PARAM_DICT_HASH = 0x{d.hash:08X}**, {len(d.params)} parameters. "
         "Flags: **M** = moving_ok (settable while moving), **N** = nvm (persisted), "
         "**R** = reboot_required. Normative descriptions: `params.yaml`.", "",
         "| ID | Key | Type | Unit | Min | Max | Default | Flags | Values / notes |",
         "|---|---|---|---|---|---|---|---|---|"]
    for p in d.params:
        flags = (("M" if p.moving_ok else "") + ("N" if p.nvm else "")
                 + ("R" if p.reboot_required else ""))
        if p.type == "enum":
            notes = ", ".join(f"{e.value}={e.name}" for e in sorted(p.enum, key=lambda e: e.value))
            mn = mx = ""
        elif p.type == "bool":
            notes, mn, mx = "", "", ""
        else:
            notes, mn, mx = "", human_value(p, p.min), human_value(p, p.max)
        if not p.nvm:
            notes = (notes + "; " if notes else "") + "session value (not persisted)"
        o.append(f"| 0x{p.id:04X} | `{p.key}` | {p.type} | {p.unit} | {mn} | {mx} | "
                 f"{human_value(p, p.default)} | {flags or '-'} | {notes} |")
    o += ["", ICD_END]
    return "\n".join(o)


def update_icd(text: str, d: Dictionary) -> str:
    if ICD_BEGIN not in text or ICD_END not in text:
        return text
    a = text.index(ICD_BEGIN)
    b = text.index(ICD_END) + len(ICD_END)
    return text[:a] + gen_markdown(d) + text[b:]


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------
def outputs(d: Dictionary, proto: "gen_protocol.Protocol | None" = None) -> dict[Path, str]:
    proto = gen_protocol.load() if proto is None else proto
    res = {
        FW_H_PATH: gen_c_header(d),
        FW_C_PATH: gen_c_source(d),
        SW_PY_PATH: gen_python(d),
        **gen_protocol.outputs(proto),
    }
    if ICD_PATH.exists():
        text = update_icd(ICD_PATH.read_text(encoding="utf-8"), d)
        res[ICD_PATH] = gen_protocol.update_icd(text, proto)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify outputs are up to date")
    ap.add_argument("--canonical", action="store_true", help="print canonical serialization")
    ap.add_argument("--hash", action="store_true", help="print PARAM_DICT_HASH")
    args = ap.parse_args(argv)
    try:
        d = load()
    except DictError as e:
        print(f"params.yaml: {e}", file=sys.stderr)
        return 2
    try:
        proto = gen_protocol.load()
    except gen_protocol.ProtoError as e:
        print(f"protocol.yaml: {e}", file=sys.stderr)
        return 2
    if args.canonical:
        sys.stdout.write(canonical(d))
        return 0
    if args.hash:
        print(f"0x{d.hash:08X}")
        return 0
    stale = []
    for path, content in outputs(d, proto).items():
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old == content:
            continue
        if args.check:
            stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        print(f"wrote {path.relative_to(ROOT)}")
    if args.check and stale:
        for p in stale:
            print(f"out of date: {p.relative_to(ROOT)}", file=sys.stderr)
        return 1
    print(f"{len(d.params)} params, PARAM_DICT_HASH = 0x{d.hash:08X}; protocol.yaml ICD "
          f"v{proto.icd_version}: {len(proto.commands)} commands, {len(proto.tables)} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
