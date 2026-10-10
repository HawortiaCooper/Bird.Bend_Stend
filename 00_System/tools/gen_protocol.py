#!/usr/bin/env python3
"""Protocol name-registry generator for the Bird Bend Stand project (GF-08, OI-FW-11).

Implements: IF-001, IF-010, IF-012, FW-STR-003, FW-STR-006

Reads   00_System/specs/protocol.yaml   (single source of names and codes)
Writes  02_FW/src/gen/proto_gen.h
        03_SW/src/bend_stand/core/protocol_gen.py
        00_System/specs/ICD_protocol.md  (tables between "BEGIN GENERATED protocol:<id>" markers)

Called by gen_params.py (the single generator entry point: `gen_params.py` / `--check`), so the
parameter dictionary and the protocol names regenerate together. Only dependency: PyYAML.
"""
from __future__ import annotations

import keyword
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parents[1]
PROTO_YAML_PATH = ROOT / "00_System" / "specs" / "protocol.yaml"
FW_PROTO_H_PATH = ROOT / "02_FW" / "src" / "gen" / "proto_gen.h"
SW_PROTO_PY_PATH = ROOT / "03_SW" / "src" / "bend_stand" / "core" / "protocol_gen.py"

SCHEMA_VERSION = 1
GENERATED_BANNER = "GENERATED - do not edit"
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MARK_RE = re.compile(r"<!-- BEGIN GENERATED protocol:([a-z0-9_]+) \(gen_protocol\.py\) -->"
                     r".*?<!-- END GENERATED protocol:\1 -->", re.S)
CTYPES = {"u8": ("uint8_t", 0, 0xFF), "u16": ("uint16_t", 0, 0xFFFF),
          "u32": ("uint32_t", 0, 0xFFFFFFFF), "i32": ("int32_t", -0x80000000, 0x7FFFFFFF)}


class ProtoError(Exception):
    """Validation error in protocol.yaml."""


# --------------------------------------------------------------------------------------
# text and identifier safety (FWR-14; shared with gen_params.py)
# --------------------------------------------------------------------------------------
# Every YAML string that reaches generated C or Python passes one of these checks in load(), so a
# dictionary edit can never turn into code: identifiers against strict ASCII patterns (no keyword of
# either language), versions and numbers against numeric patterns, free text (rendered only inside C
# comments, Python string literals via repr(), docstrings, '#' comments and Markdown cells) without
# control / format / line-separator characters (newline, tab, CR, NEL, U+2028/9, bidi controls, BOM),
# C comment delimiters, backslashes, triple quotes and C trigraphs. Non-ASCII letters and symbols
# (µ, §, →, ≥) stay allowed: they are inert in those contexts and the dictionaries use them.
C_KEYWORDS = frozenset("""auto break case char const continue default do double else enum extern float for
goto if inline int long register restrict return short signed sizeof static struct switch typedef union
unsigned void volatile while bool true false _Bool _Complex _Imaginary _Alignas _Alignof _Atomic _Generic
_Noreturn _Static_assert _Thread_local NULL""".split())
C_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PY_CLASS_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
VERSION_RE = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}(\.[0-9]{1,3})?$")
_BAD_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_BAD_SEQUENCES = ("*/", "/*", "\\", '"""')
_TRIGRAPH_RE = re.compile(r"\?\?[=/'()!<>-]")


def safe_text(v: Any, where: str, err: type[Exception] | None = None) -> str:
    """Free text (comments, string literals, docstrings, Markdown): see the rule above."""
    err = err or ProtoError
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise err(f"{where}: text expected, got {type(v).__name__}")
    s = str(v)
    for ch in s:
        if unicodedata.category(ch) in _BAD_CATEGORIES:
            raise err(f"{where}: control / format / line-separator character U+{ord(ch):04X} in {s!r}")
    for seq in _BAD_SEQUENCES:
        if seq in s:
            raise err(f"{where}: {seq!r} not allowed in generated text {s!r}")
    if _TRIGRAPH_RE.search(s):
        raise err(f"{where}: C trigraph sequence in {s!r}")
    return s


def safe_ident(v: Any, where: str, err: type[Exception] | None = None,
               pattern: re.Pattern[str] = C_IDENT_RE) -> str:
    """Identifier used in generated C and / or Python code: ASCII pattern, no keyword of either language."""
    err = err or ProtoError
    if (not isinstance(v, str) or not v.isascii() or not pattern.match(v) or v in C_KEYWORDS
            or keyword.iskeyword(v) or keyword.issoftkeyword(v)):
        raise err(f"{where}: bad identifier {v!r}")
    return v


def safe_version(v: Any, where: str, err: type[Exception] | None = None) -> str:
    """Version text rendered into a C string literal / Python string: digits and dots only."""
    err = err or ProtoError
    if not isinstance(v, str) or not VERSION_RE.match(v):
        raise err(f"{where}: version must match {VERSION_RE.pattern}, got {v!r}")
    return v


def safe_int(v: Any, where: str, lo: int, hi: int, err: type[Exception] | None = None) -> int:
    """Integer rendered as a literal: a YAML int (not bool, not text) in [lo, hi]."""
    err = err or ProtoError
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise err(f"{where}: integer {lo}..{hi} expected, got {v!r}")
    return v


@dataclass(frozen=True)
class Item:
    name: str
    value: int              # code (enum / events) or bit index (bitset)
    desc: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    retired: str = ""       # ICD version that retired the name (reserved; never reused)
    feature: str = ""       # bitset item: INFO feature bit the value depends on (§7.6, D-37 b)


@dataclass(frozen=True)
class Table:
    id: str
    kind: str               # enum | bitset | events
    title: str
    icd: str
    c_prefix: str
    py_name: str
    width: int
    wire: bool
    columns: tuple[str, ...]
    items: tuple[Item, ...]


@dataclass(frozen=True)
class Command:
    name: str
    value: int
    req_len: int
    request: str
    response: str
    retry: str
    retry_text: str
    priority: bool
    sniffed: bool
    srs: str


@dataclass(frozen=True)
class Constant:
    name: str
    value: int
    ctype: str
    desc: str


@dataclass(frozen=True)
class Protocol:
    icd_version: str
    proto_major: int
    proto_minor: int
    payload_version: int
    constants: tuple[Constant, ...]
    cmd_prefix: str
    cmd_py: str
    commands: tuple[Command, ...]
    tables: tuple[Table, ...]

    def table(self, tid: str) -> Table:
        for t in self.tables:
            if t.id == tid:
                return t
        raise KeyError(tid)

    def names(self, tid: str) -> list[str]:
        """Names by bit index ("" = reserved or retired) for a bitset, by code order for an enum
        (retired codes excluded)."""
        t = self.table(tid)
        if t.kind == "bitset":
            out = [""] * t.width
            for it in t.items:
                if not it.retired:
                    out[it.value] = it.name
            while out and not out[-1]:
                out.pop()
            return out
        return [it.name for it in sorted(t.items, key=lambda i: i.value) if not it.retired]

    def codes(self, tid: str) -> dict[str, int]:
        """Active names -> code / bit index (retired excluded)."""
        return {it.name: it.value for it in self.table(tid).items if not it.retired}

    def retired(self, tid: str) -> dict[str, int]:
        return {it.name: it.value for it in self.table(tid).items if it.retired}


# --------------------------------------------------------------------------------------
# loading / validation
# --------------------------------------------------------------------------------------
def _req(d: dict[str, Any], k: str, where: str) -> Any:
    if k not in d:
        raise ProtoError(f"{where}: missing '{k}'")
    return d[k]


def _check_names(items: list[Item], where: str, width: int | None = None) -> None:
    names = [i.name for i in items]
    vals = [i.value for i in items]
    for n in names:
        if not NAME_RE.match(n):
            raise ProtoError(f"{where}: bad name {n!r}")
    if len(set(names)) != len(names):
        raise ProtoError(f"{where}: duplicate names")
    if len(set(vals)) != len(vals):
        raise ProtoError(f"{where}: duplicate values")
    if width is not None and any(not 0 <= v < width for v in vals):
        raise ProtoError(f"{where}: bit outside width {width}")


def load(path: Path = PROTO_YAML_PATH) -> Protocol:
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if _req(doc, "schema_version", "top") != SCHEMA_VERSION:
        raise ProtoError("unsupported schema_version")
    consts = []
    for c in _req(doc, "constants", "top"):
        name, value, ct = str(c["name"]), c["value"], str(c["ctype"])
        if not NAME_RE.match(name) or ct not in CTYPES or not isinstance(value, int):
            raise ProtoError(f"constant {name}: bad name/ctype/value")
        lo, hi = CTYPES[ct][1:]
        if not lo <= value <= hi:
            raise ProtoError(f"constant {name}: value outside {ct}")
        consts.append(Constant(name, value, ct, safe_text(c.get("desc", ""), f"constant {name}.desc")))
    if len({c.name for c in consts}) != len(consts):
        raise ProtoError("duplicate constant names")

    tables: list[Table] = []
    for td in _req(doc, "tables", "top"):
        tid = str(_req(td, "id", "table"))
        if not ID_RE.match(tid):
            raise ProtoError(f"bad table id {tid!r}")
        kind = str(_req(td, "kind", tid))
        if kind not in ("enum", "bitset", "events"):
            raise ProtoError(f"{tid}: unknown kind {kind}")
        width = int(td.get("width", 0))
        if kind == "bitset" and width not in (8, 16, 32):
            raise ProtoError(f"{tid}: bitset width must be 8, 16 or 32")
        cols = tuple(safe_ident(c, f"{tid}.columns", pattern=ID_RE)
                     for c in ([td["columns"]] if isinstance(td.get("columns"), str) else td.get("columns", [])))
        items = []
        for it in _req(td, "items", tid):
            key = "bit" if kind == "bitset" else "value"
            iname = str(_req(it, "name", tid))
            extra = {k: safe_text(it[k], f"{tid}.{iname}.{k}") for k in ("arg", "values", *cols) if k in it}
            ret = it.get("retired", "")
            items.append(Item(iname, safe_int(_req(it, key, tid), f"{tid}.{iname}.{key}", 0, 0xFFFF),
                              safe_text(" ".join(str(it.get("desc", "")).split()), f"{tid}.{iname}.desc"), extra,
                              safe_version(str(ret), f"{tid}.{iname}.retired") if ret != "" else "",
                              str(it.get("feature", ""))))
            if kind == "events" and not {"arg", "values"} <= extra.keys():
                raise ProtoError(f"{tid}.{it['name']}: events need arg and values")
            if any(c not in extra for c in cols):
                raise ProtoError(f"{tid}.{it['name']}: missing column")
        _check_names(items, tid, width if kind == "bitset" else None)   # retired values count: never reused
        if kind == "enum" and any(not 0 <= i.value <= 0xFF for i in items):
            raise ProtoError(f"{tid}: enum codes must be 0..255")
        if kind == "events" and any(not 1 <= i.value <= 0xFFFF for i in items):
            raise ProtoError(f"{tid}: event codes must be 1..65535")
        prefix = str(_req(td, "c_prefix", tid))
        if not re.match(r"^[A-Z][A-Z0-9_]*_$", prefix):
            raise ProtoError(f"{tid}: bad c_prefix")
        tables.append(Table(tid, kind, safe_text(_req(td, "title", tid), f"{tid}.title"),
                            safe_text(td.get("icd", ""), f"{tid}.icd"), prefix,
                            safe_ident(_req(td, "py_name", tid), f"{tid}.py_name", pattern=PY_CLASS_RE),
                            width, bool(td.get("wire", True)), cols, tuple(items)))
    if len({t.id for t in tables}) != len(tables) or len({t.c_prefix for t in tables}) != len(tables):
        raise ProtoError("duplicate table id or c_prefix")
    feats = {i.name for t in tables if t.id == "features" for i in t.items}
    for t in tables:
        for i in t.items:
            if i.feature and (t.kind != "bitset" or i.feature not in feats):
                raise ProtoError(f"{t.id}.{i.name}: feature {i.feature!r} unknown or not a bitset item")

    cd = _req(doc, "commands", "top")
    retry_names = {i.name for t in tables if t.id == "retry_class" for i in t.items}
    cmds = []
    for c in _req(cd, "items", "commands"):
        cn = str(c["name"])
        cmd = Command(cn, safe_int(c["value"], f"command {cn}.value", 0, 0xFF),
                      safe_int(c["req_len"], f"command {cn}.req_len", 0, 0xFFFF),
                      safe_text(c["request"], f"command {cn}.request"),
                      safe_text(c["response"], f"command {cn}.response"), str(c["retry"]),
                      safe_text(c.get("retry_text", c["retry"]), f"command {cn}.retry_text"),
                      bool(c["priority"]), bool(c.get("sniffed", False)),
                      safe_text(c["srs"], f"command {cn}.srs"))
        if not 0x01 <= cmd.value <= 0x3F:
            raise ProtoError(f"command {cmd.name}: TYPE must be 0x01..0x3F")
        if cmd.retry not in retry_names:
            raise ProtoError(f"command {cmd.name}: unknown retry class {cmd.retry}")
        max_len = next(k.value for k in consts if k.name == "MAX_LEN")
        if not 0 <= cmd.req_len <= max_len:
            raise ProtoError(f"command {cmd.name}: req_len")
        cmds.append(cmd)
    _check_names([Item(c.name, c.value) for c in cmds], "commands")
    cprefix = str(cd["c_prefix"])
    if not re.match(r"^[A-Z][A-Z0-9_]*_$", cprefix):
        raise ProtoError("commands: bad c_prefix")
    return Protocol(safe_version(_req(doc, "icd_version", "top"), "icd_version"),
                    safe_int(doc["proto_major"], "proto_major", 0, 255),
                    safe_int(doc["proto_minor"], "proto_minor", 0, 255),
                    safe_int(doc["payload_version"], "payload_version", 0, 255), tuple(consts),
                    cprefix, safe_ident(cd["py_name"], "commands.py_name", pattern=PY_CLASS_RE),
                    tuple(cmds), tuple(tables))


# --------------------------------------------------------------------------------------
# C header
# --------------------------------------------------------------------------------------
def _c_comment(s: str) -> str:
    return s.replace("*/", "* /")


def _hexish(c: Constant) -> bool:
    return c.ctype == "u32" or c.name.startswith(("SYNC", "RESP", "CRC", "DETAIL"))


def _c_const(c: Constant) -> str:
    if c.ctype == "i32":
        v = "(-2147483647 - 1)" if c.value == -0x80000000 else str(c.value)
        return f"((int32_t){v})"
    if c.ctype == "u32":
        return f"0x{c.value:08X}UL"
    return f"0x{c.value:02X}u" if _hexish(c) else f"{c.value}u"


def gen_c(p: Protocol) -> str:
    o = [f"/* {GENERATED_BANNER}.\n"
         f" * Source : 00_System/specs/protocol.yaml (ICD_protocol.md v{p.icd_version}, "
         f"PROTO {p.proto_major}.{p.proto_minor}, PAYLOAD {p.payload_version})\n"
         " * Tool   : 00_System/tools/gen_protocol.py (run via gen_params.py)\n"
         " * Names and codes of commands, NACK codes, flag/status/FAULT/IO/BLOCK bits, EVENT codes\n"
         " * and their argument enums. FW code uses these identifiers only (no hand-listed codes).\n"
         " * Implements: IF-010, IF-012, FW-STR-003, FW-STR-006\n */\n"
         "#ifndef PROTO_GEN_H\n#define PROTO_GEN_H\n\n#include <stdint.h>\n\n"
         "#ifdef __cplusplus\nextern \"C\" {\n#endif\n\n"
         f'#define PROTO_ICD_VERSION      "{p.icd_version}"\n'
         f"#define PROTO_MAJOR            {p.proto_major}u\n"
         f"#define PROTO_MINOR            {p.proto_minor}u\n"
         f"#define PROTO_PAYLOAD_VERSION  {p.payload_version}u\n\n"
         "/* ---- constants (ICD §2, §3, §7) ---- */\n"]
    for c in p.constants:
        o.append(f"#define PROTO_{c.name:<22} {_c_const(c):<22} /* {_c_comment(c.desc)} */\n")
    o.append("\n/* ---- command TYPEs (ICD §3.2); response TYPE = TYPE | PROTO_RESP_BIT ---- */\n"
             "typedef enum {\n")
    for c in p.commands:
        o.append(f"    {p.cmd_prefix}{c.name:<20} = 0x{c.value:02X},\n")
    o.append("} proto_cmd_t;\n\n/* fixed request LEN per command (E_LENGTH otherwise) */\n")
    for c in p.commands:
        o.append(f"#define {p.cmd_prefix}REQ_LEN_{c.name:<20} {c.req_len}u\n")
    prio = " || \\\n     ".join(f"(t) == {p.cmd_prefix}{c.name}" for c in p.commands if c.priority)
    o.append("\n/* commands the SW sends through its priority path (ICD §2.4) */\n"
             f"#define {p.cmd_prefix}IS_PRIORITY(t) \\\n    ({prio})\n\n")
    snif = " || \\\n     ".join(f"(t) == {p.cmd_prefix}{c.name}" for c in p.commands if c.sniffed)
    o.append("/* commands the FW stop sniffer acts on ahead of normal processing (ICD §2) */\n"
             f"#define {p.cmd_prefix}IS_SNIFFED(t) \\\n    ({snif})\n\n")
    for t in p.tables:
        if not t.wire:
            continue
        o.append(f"/* ---- {_c_comment(t.title)} (ICD {_c_comment(t.icd)}) ---- */\n")
        if t.kind in ("enum", "events"):
            o.append("typedef enum {\n")
            for it in sorted(t.items, key=lambda i: i.value):
                v = f"0x{it.value:02X}" if it.value >= 0x80 else str(it.value)
                d = f"RETIRED in ICD v{it.retired}: never sent, code never reused. {it.desc}" if it.retired else it.desc
                o.append(f"    {t.c_prefix}{it.name:<24} = {v},"
                         + (f" /* {_c_comment(d)} */" if d else "") + "\n")
            o.append(f"}} proto_{t.id}_t;\n\n")
        else:
            digits = t.width // 4
            suffix = "UL" if t.width == 32 else "u"
            mask = rmask = 0
            for it in sorted(t.items, key=lambda i: i.value):
                if it.retired:
                    rmask |= 1 << it.value
                else:
                    mask |= 1 << it.value
                d = (f"RETIRED in ICD v{it.retired}: reserved, sent as 0, never reused. {it.desc}"
                     if it.retired else it.desc + (f" [valid only with FEAT_{it.feature}]" if it.feature else ""))
                o.append(f"#define {t.c_prefix}{it.name + '_BIT':<26} {it.value}u\n")
                o.append(f"#define {t.c_prefix}{it.name:<26} 0x{1 << it.value:0{digits}X}{suffix}"
                         f" /* {_c_comment(d)} */\n")
            o.append(f"#define {t.c_prefix}{'DEFINED_MASK':<26} 0x{mask:0{digits}X}{suffix}\n")
            if rmask:
                o.append(f"#define {t.c_prefix}{'RETIRED_MASK':<26} 0x{rmask:0{digits}X}{suffix}\n")
            o.append("\n")
    o.append("#ifdef __cplusplus\n}\n#endif\n\n#endif /* PROTO_GEN_H */\n")
    return "".join(o)


# --------------------------------------------------------------------------------------
# Python module
# --------------------------------------------------------------------------------------
def gen_py(p: Protocol) -> str:
    o = [f'"""{GENERATED_BANNER}.\n\n'
         f"Source : 00_System/specs/protocol.yaml (ICD_protocol.md v{p.icd_version}, "
         f"PROTO {p.proto_major}.{p.proto_minor}, PAYLOAD {p.payload_version})\n"
         "Tool   : 00_System/tools/gen_protocol.py (run via gen_params.py)\n\n"
         "Names and codes of commands, NACK codes, flag/status/FAULT/IO/BLOCK bits, EVENT codes and\n"
         "their argument enums, for the codec, the indicator registry and the GUI channel tree\n"
         "(GF-08). <ID>_BITS lists bit names by bit index ('' = reserved); <ID>_DESC maps a name to\n"
         "its ICD description. Pure Python, no Qt, no PyYAML.\n\n"
         "Implements: IF-010, IF-012, FW-STR-003, FW-STR-006\n"
         '"""\n'
         "from __future__ import annotations\n\n"
         "from collections.abc import Mapping\n"
         "from enum import IntEnum, IntFlag\n"
         "from types import MappingProxyType\n\n"
         f"ICD_VERSION = {p.icd_version!r}\n"
         f"PROTO_MAJOR = {p.proto_major}\n"
         f"PROTO_MINOR = {p.proto_minor}\n"
         f"PAYLOAD_VERSION = {p.payload_version}\n\n"]
    for c in p.constants:
        v = f"0x{c.value:02X}" if _hexish(c) else str(c.value)
        o.append(f"{c.name} = {v}  # {c.desc}\n")
    for t in p.tables:
        base = "IntFlag" if t.kind == "bitset" else "IntEnum"
        o.append(f"\n\nclass {t.py_name}({base}):\n    \"\"\"{t.title} (ICD {t.icd}).\"\"\"\n\n")
        for it in sorted(t.items, key=lambda i: i.value):
            if t.kind == "bitset":
                v = f"0x{1 << it.value:0{t.width // 4}X}"
            else:
                v = f"0x{it.value:02X}" if it.value >= 0x80 else str(it.value)
            o.append(f"    {it.name} = {v}\n")
        uid = t.id.upper()
        if t.kind == "bitset":
            o.append(f"\n\n{uid}_BITS: tuple[str, ...] = {tuple(p.names(t.id))!r}\n")
        else:
            o.append(f"\n\n{uid}_NAMES: tuple[str, ...] = {tuple(p.names(t.id))!r}\n")
        desc = {it.name: (it.desc if t.kind != "events" else it.extra["arg"]) for it in t.items}
        o.append(f"{uid}_DESC: Mapping[str, str] = MappingProxyType({desc!r})\n")
        ret = sorted(it.name for it in t.items if it.retired)
        o.append(f"{uid}_RETIRED: frozenset[str] = frozenset({set(ret)!r})"
                 "  # reserved, never sent, never reused (members kept for compatibility)\n"
                 if ret else f"{uid}_RETIRED: frozenset[str] = frozenset()\n")
        if t.kind == "bitset" and any(it.feature for it in t.items):
            o.append(f"{uid}_FEATURE: Mapping[str, str] = MappingProxyType("
                     f"{ {it.name: it.feature for it in t.items if it.feature}!r})"
                     "  # bit valid only while this INFO feature bit is 1 (ICD §7.6)\n")
        if t.kind == "events":
            o.append(f"{uid}_ARG: Mapping[str, str] = MappingProxyType("
                     f"{ {it.name: it.extra['arg'] for it in t.items}!r})\n")
            o.append(f"{uid}_VALUES: Mapping[str, str] = MappingProxyType("
                     f"{ {it.name: it.extra['values'] for it in t.items}!r})\n")
        for col in t.columns:
            o.append(f"{uid}_{col.upper()}: Mapping[str, str] = MappingProxyType("
                     f"{ {it.name: it.extra[col] for it in t.items}!r})\n")
    o.append(f"\n\nclass {p.cmd_py}(IntEnum):\n    \"\"\"Command TYPEs (ICD §3.2); response TYPE = "
             "TYPE | RESP_BIT.\"\"\"\n\n")
    for c in p.commands:
        o.append(f"    {c.name} = 0x{c.value:02X}\n")
    rc = p.table("retry_class").py_name
    o.append(f"\n\nCMD_REQ_LEN: Mapping[{p.cmd_py}, int] = MappingProxyType({{\n")
    o += [f"    {p.cmd_py}.{c.name}: {c.req_len},\n" for c in p.commands]
    o.append("})\n")
    o.append(f"CMD_RETRY: Mapping[{p.cmd_py}, {rc}] = MappingProxyType({{\n")
    o += [f"    {p.cmd_py}.{c.name}: {rc}.{c.retry},\n" for c in p.commands]
    o.append("})\n# JOG 0 is RETRY class (CMD_RETRY gives the JOG != 0 class)\n")
    prio = ", ".join(f"{p.cmd_py}.{c.name}" for c in p.commands if c.priority)
    o.append(f"CMD_PRIORITY: frozenset[{p.cmd_py}] = frozenset({{{prio}}})\n")
    snif = ", ".join(f"{p.cmd_py}.{c.name}" for c in p.commands if c.sniffed)
    o.append(f"CMD_SNIFFED: frozenset[{p.cmd_py}] = frozenset({{{snif}}})  # FW stop sniffer (ICD §2)\n")
    o.append("\n# registry: table id -> enum/flag class (GUI channel tree, indicator registry)\n"
             "TABLES: Mapping[str, type] = MappingProxyType({\n")
    o += [f"    {t.id!r}: {t.py_name},\n" for t in p.tables]
    o.append(f"    'command': {p.cmd_py},\n}})\n")
    return "".join(o)


# --------------------------------------------------------------------------------------
# ICD markdown
# --------------------------------------------------------------------------------------
def _cell(s: str) -> str:
    return s.replace("|", "\\|")


def _begin(tid: str) -> str:
    return f"<!-- BEGIN GENERATED protocol:{tid} (gen_protocol.py) -->"


def _end(tid: str) -> str:
    return f"<!-- END GENERATED protocol:{tid} -->"


def md_commands(p: Protocol) -> list[str]:
    o = [f"Generated from `protocol.yaml` `commands` (C `{p.cmd_prefix}*`, `{p.cmd_prefix}REQ_LEN_*`; "
         f"Python `{p.cmd_py}`, `CMD_REQ_LEN`, `CMD_RETRY`).", "",
         "| TYPE | Name | Request payload (LEN) | Response body on OK | Retry | SW priority path / FW sniffer | SRS |",
         "|---|---|---|---|---|---|---|"]
    for c in p.commands:
        flags = " / ".join(x for x in ("priority" if c.priority else "", "sniffed" if c.sniffed else "") if x)
        o.append(f"| `0x{c.value:02X}` | {c.name} | {_cell(c.request)} ({c.req_len}) | "
                 f"{_cell(c.response)} | {_cell(c.retry_text)} | {flags or '–'} | {_cell(c.srs)} |")
    return o


def md_table(t: Table) -> list[str]:
    ids = (f"C `{t.c_prefix}*`, Python `{t.py_name}`" if t.wire
           else f"Python `{t.py_name}` (not on the wire)")
    o = [f"Generated from `protocol.yaml` table `{t.id}` ({ids}).", ""]
    if t.kind == "bitset":
        feat = any(i.feature for i in t.items)
        o += ["| Bit | Name | Meaning |" + (" Valid only with |" if feat else ""),
              "|---|---|---|" + ("---|" if feat else "")]
        used = {i.value: i for i in t.items}
        b = 0
        while b < t.width:
            if b in used:
                u = used[b]
                if u.retired:
                    o.append(f"| {b} | ~~`{u.name}`~~ | **retired in v{u.retired}**: reserved, sent as 0, never "
                             f"reused — {_cell(u.desc)} |" + (" – |" if feat else ""))
                else:
                    o.append(f"| {b} | `{u.name}` | {_cell(u.desc)} |"
                             + (f" `FEAT_{u.feature}` |" if u.feature else (" – |" if feat else "")))
                b += 1
                continue
            e = b
            while e + 1 < t.width and e + 1 not in used:
                e += 1
            o.append(f"| {b}{'–' + str(e) if e > b else ''} | — | reserved (0) |" + (" – |" if feat else ""))
            b = e + 1
        return o
    if t.kind == "events":
        o += ["| Code | Name | `arg` | `value` / `value2` |", "|---|---|---|---|"]
        for it in sorted(t.items, key=lambda i: i.value):
            if it.retired:
                o.append(f"| {it.value} | ~~`{it.name}`~~ | **retired in v{it.retired}**: never sent, code never "
                         f"reused — {_cell(it.desc)} | – |")
                continue
            o.append(f"| {it.value} | `{it.name}` | {_cell(it.extra['arg'])} | "
                     f"{_cell(it.extra['values'])} |")
        return o
    head = "| Code | Name | Meaning |" + "".join(f" `{c}` |" for c in t.columns)
    o += [head, "|---|---|---|" + "---|" * len(t.columns)]
    for it in sorted(t.items, key=lambda i: i.value):
        v = f"`0x{it.value:02X}`" if it.value >= 0x80 else str(it.value)
        if it.retired:
            o.append(f"| {v} | ~~`{it.name}`~~ | **retired in v{it.retired}**: never sent, code never reused — "
                     f"{_cell(it.desc)} |" + "".join(" – |" for _ in t.columns))
            continue
        o.append(f"| {v} | `{it.name}` | {_cell(it.desc)} |"
                 + "".join(f" {_cell(it.extra[c])} |" for c in t.columns))
    return o


def md_registry(p: Protocol, placed: set[str]) -> list[str]:
    o = ["Generated from `protocol.yaml` — the single source of protocol names and codes (GF-08). "
         f"C: `02_FW/src/gen/proto_gen.h`; Python: `03_SW/src/bend_stand/core/protocol_gen.py`. "
         "Bit identifiers: `<prefix><NAME>` = mask, `<prefix><NAME>_BIT` = index; Python "
         "`<ID>_BITS` = names by bit index, `<ID>_DESC` = descriptions.", "",
         "| Table | Kind | ICD | C | Python |", "|---|---|---|---|---|",
         f"| `commands` | enum | §3.2 | `{p.cmd_prefix}*`, `{p.cmd_prefix}REQ_LEN_*`, "
         f"`{p.cmd_prefix}IS_PRIORITY()`, `{p.cmd_prefix}IS_SNIFFED()` | `{p.cmd_py}`, `CMD_REQ_LEN`, `CMD_RETRY`, "
         "`CMD_PRIORITY`, `CMD_SNIFFED` |",
         "| `constants` | — | B.1 | `PROTO_*` | module constants |"]
    for t in p.tables:
        where = t.icd if t.id in placed else "App. B"
        c = f"`{t.c_prefix}*`, `proto_{t.id}_t`" if t.wire and t.kind != "bitset" else (
            f"`{t.c_prefix}*`" if t.wire else "—")
        o.append(f"| `{t.id}` | {t.kind} | {where} | {c} | `{t.py_name}` |")
    o += ["", "### B.1 Constants", "", "| Name | Value | Type | Meaning |", "|---|---|---|---|"]
    for k in p.constants:
        if k.ctype == "i32" and k.value == -0x80000000:
            v = "0x80000000 (−2 147 483 648)"
        else:
            v = f"0x{k.value:02X}" if _hexish(k) else str(k.value)
        o.append(f"| `{k.name}` | {v} | {k.ctype} | {_cell(k.desc)} |")
    n = 2
    for t in p.tables:
        if t.id in placed:
            continue
        o += ["", f"### B.{n} {t.title}", ""] + md_table(t)
        n += 1
    return o


def update_icd(text: str, p: Protocol) -> str:
    placed = {m.group(1) for m in MARK_RE.finditer(text)} - {"registry", "commands"}
    known = {t.id for t in p.tables} | {"registry", "commands"}
    unknown = placed - known
    if unknown:
        raise ProtoError("ICD markers without a table: " + ", ".join(sorted(unknown)))

    def repl(m: re.Match[str]) -> str:
        tid = m.group(1)
        if tid == "commands":
            body = md_commands(p)
        elif tid == "registry":
            body = md_registry(p, placed)
        else:
            body = md_table(p.table(tid))
        return "\n".join([_begin(tid), "", *body, "", _end(tid)])
    return MARK_RE.sub(repl, text)


def outputs(p: Protocol) -> dict[Path, str]:
    return {FW_PROTO_H_PATH: gen_c(p), SW_PROTO_PY_PATH: gen_py(p)}
