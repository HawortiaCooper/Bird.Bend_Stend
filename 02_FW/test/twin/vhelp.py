"""Validator E - PC-side helper for the twin suites (ref_codec based; validator-owned).

Verifies: (harness for) FW-CMD-001, FW-CFG-002/003, FW-NVM-001/002, FW-STR-001…004, IF-004/005/007/008
"""
from __future__ import annotations

import struct
from pathlib import Path

import gen_params
import ref_codec as rc
from twin import Twin, TwinLink

DICT = gen_params.load()
PBYKEY = {p.key: p for p in DICT.params}
PBYID = {p.id: p for p in DICT.params}
FW = Path(__file__).resolve().parents[2]


def gen_define(name: str) -> str:
    import re
    for h in ("proto_gen.h", "params_gen.h"):
        t = (FW / "src" / "gen" / h).read_text(encoding="utf-8")
        m = re.search(r"#define\s+" + name + r"\s+(\S+)", t)
        if m:
            return m.group(1)
    raise KeyError(name)


def wire_value(p, value):
    """Value as decoded by ref_codec (f32 rounded to binary32, enum code, bool int)."""
    if p.type == "f32":
        return struct.unpack("<f", struct.pack("<f", float(value)))[0]
    if p.type == "enum" and isinstance(value, str):
        return next(e.value for e in p.enum if e.name == value)
    if p.type == "bool":
        return 1 if value else 0
    return value


class V:
    """Validator view of one twin: commands, status, parameters, frames received."""

    def __init__(self, tw: Twin):
        self.tw = tw
        self.link = TwinLink(tw)

    # ------------------------------------------------------------ commands
    def cmd(self, name: str, fields: dict | None = None, timeout_ms: float = 3000, step_ms: float = 0.25) -> dict:
        return self.link.cmd(name, fields or {}, timeout_ms=timeout_ms, step_ms=step_ms)

    def ok(self, name: str, fields: dict | None = None, **kw) -> dict:
        r = self.cmd(name, fields, **kw)
        assert r["status"] == "OK", (name, fields, r)
        return r

    def status(self) -> dict:
        return self.ok("GET_STATUS")["board_status"]

    def info(self) -> dict:
        return self.ok("GET_INFO")["info"]

    def get(self, key: str):
        p = PBYKEY[key]
        return self.ok("GET_PARAM", {"id": p.id})["entry"]["value"]

    def set(self, key: str, value) -> dict:
        p = PBYKEY[key]
        return self.cmd("SET_PARAM", {"id": p.id, "type": p.type, "value": wire_value(p, value)})

    def reboot(self) -> dict:
        return self.ok("REBOOT", {"magic": rc.REBOOT_MAGIC})

    def all_params(self) -> list[dict]:
        out, page, count = [], 0, None
        while count is None or page < count:
            r = self.ok("GET_ALL_PARAMS", {"page": page})
            count = r["page_count"]
            out += r["entries"]
            page += 1
        return out

    def advance(self, ms: float) -> None:
        self.tw.advance_ms(ms)
        self.link.poll()

    # ------------------------------------------------------------ frames
    def events(self) -> list[dict]:
        self.advance(3)                       # let queued EVENT frames (lowest wire priority) drain
        return self.link.events()

    def event_frames(self) -> list[tuple[int, dict]]:
        """(header SEQ, decoded EVENT) of every EVENT frame received."""
        self.link.poll()
        return [(f.seq, rc.decode_event(f.payload)) for f in self.link.frames if f.type == rc.ASYNC["EVENT"]]

    def data(self) -> list[dict]:
        self.link.poll()
        return self.link.data()

    def data_frames(self):
        self.link.poll()
        return [f for f in self.link.frames if f.type == rc.ASYNC["DATA"]]

    def responses(self):
        self.link.poll()
        return [f for f in self.link.frames if f.type & rc.RESP_BIT and f.type < 0xC0]

    def wire(self, d: str | None = None) -> list[dict]:
        wl = self.tw.act("query", what="wire_log")["wire_log"]
        return [w for w in wl if d is None or w["dir"] == d]

    def reboot_power(self) -> None:
        self.tw.act("reset", cause="power")
        self.advance(5)


def flag(st: dict, name: str) -> bool:
    for k in ("flags", "status", "sys_flags", "io"):
        if name in st.get(k, []):
            return True
    return False
