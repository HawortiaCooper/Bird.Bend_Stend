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

import re
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
        consts.append(Constant(name, value, ct, str(c.get("desc", ""))))
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
        cols = tuple(str(c) for c in ([td["columns"]] if isinstance(td.get("columns"), str)
                                      else td.get("columns", [])))
        items = []
        for it in _req(td, "items", tid):
            key = "bit" if kind == "bitset" else "value"
            extra = {k: str(it[k]) for k in ("arg", "values", *cols) if k in it}
            items.append(Item(str(_req(it, "name", tid)), int(_req(it, key, tid)),
                              " ".join(str(it.get("desc", "")).split()), extra,
                              str(it.get("retired", "")), str(it.get("feature", ""))))
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
        if not re.match(r"^[A-Z][A-Z0-9]*_$", prefix):
            raise ProtoError(f"{tid}: bad c_prefix")
        tables.append(Table(tid, kind, str(_req(td, "title", tid)), str(td.get("icd", "")), prefix,
                            str(_req(td, "py_name", tid)), width, bool(td.get("wire", True)), cols,
                            tuple(items)))
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
        cmd = Command(str(c["name"]), int(c["value"]), int(c["req_len"]), str(c["request"]),
                      str(c["response"]), str(c["retry"]), str(c.get("retry_text", c["retry"])),
                      bool(c["priority"]), bool(c.get("sniffed", False)), str(c["srs"]))
        if not 0x01 <= cmd.value <= 0x3F:
            raise ProtoError(f"command {cmd.name}: TYPE must be 0x01..0x3F")
        if cmd.retry not in retry_names:
            raise ProtoError(f"command {cmd.name}: unknown retry class {cmd.retry}")
        max_len = next(k.value for k in consts if k.name == "MAX_LEN")
        if not 0 <= cmd.req_len <= max_len:
            raise ProtoError(f"command {cmd.name}: req_len")
        cmds.append(cmd)
    _check_names([Item(c.name, c.value) for c in cmds], "commands")
    return Protocol(str(_req(doc, "icd_version", "top")), int(doc["proto_major"]),
                    int(doc["proto_minor"]), int(doc["payload_version"]), tuple(consts),
                    str(cd["c_prefix"]), str(cd["py_name"]), tuple(cmds), tuple(tables))


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
        o.append(f"/* ---- {_c_comment(t.title)} (ICD {t.icd}) ---- */\n")
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
