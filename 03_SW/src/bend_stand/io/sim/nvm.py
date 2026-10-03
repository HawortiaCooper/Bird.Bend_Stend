"""Simulated NVM: two alternating parameter records with sequence number, CRC, dict hash (ICD §11.2/§11.3).

Records live in memory and optionally in a JSON file (``bird.bend.simnvm`` v1) so the parameter set survives a
simulator restart. A record-level power cut (vocabulary ``flash``, S variant) leaves the record being written
CRC-bad, the previous one stays valid.

Implements: FW-NVM-001/002 (sim), SW-CFG-004 (NVM buttons against the simulator)
"""
from __future__ import annotations

import json
import os
import zlib
from dataclasses import dataclass, field
from typing import Any

from bend_stand.calc.paramrules import check_hard_rules
from bend_stand.core import params_gen as pgen


@dataclass
class NvmRecord:
    seq: int
    dict_hash: int
    values: dict[int, Any]            # id → value (wire-typed Python values)
    crc_ok: bool = True

    def to_json(self) -> dict[str, Any]:
        return {"seq": self.seq, "dict_hash": self.dict_hash, "crc_ok": self.crc_ok,
                "values": {str(k): v for k, v in self.values.items()}}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> NvmRecord:
        return cls(int(d["seq"]), int(d["dict_hash"]), {int(k): v for k, v in d["values"].items()},
                   bool(d.get("crc_ok", True)))


@dataclass
class BootResult:
    values: dict[str, Any]
    event: str | None                 # PARAMS_LOADED | PARAMS_DEFAULTED | None
    arg: int = 0
    defaulted: bool = False           # NVM_DEFAULTED
    record_seq: int = 0
    ok: bool = True                   # LOAD: False → E_NVM 1, RAM unchanged


@dataclass
class NvmStore:
    path: str | None = None
    records: list[NvmRecord | None] = field(default_factory=lambda: [None, None])
    writes: int = 0
    cut_next_save: bool = False

    def __post_init__(self) -> None:
        if self.path and os.path.exists(self.path):
            try:
                d = json.loads(open(self.path, encoding="utf-8").read())
                self.records = [NvmRecord.from_json(r) if r else None for r in d.get("records", [None, None])]
            except (OSError, ValueError, KeyError):
                self.records = [None, None]

    def _persist(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"schema": "bird.bend.simnvm", "schema_version": 1,
                       "records": [r.to_json() if r else None for r in self.records]}, f)
        os.replace(tmp, self.path)

    def newest_valid(self) -> NvmRecord | None:
        good = [r for r in self.records if r is not None and r.crc_ok]
        return max(good, key=lambda r: r.seq) if good else None

    def save(self, ram: dict[str, Any]) -> NvmRecord:
        """Write the RAM image of all ``nvm`` parameters to the alternate record. A pending cut leaves it bad."""
        newest = self.newest_valid()
        seq = (newest.seq if newest else 0) + 1
        slot = 0 if newest is None else 1 - self.records.index(newest)     # the alternate record
        values = {p.id: ram[p.key] for p in pgen.PARAMS if p.nvm}
        rec = NvmRecord(seq, pgen.PARAM_DICT_HASH, values, crc_ok=not self.cut_next_save)
        self.cut_next_save = False
        self.records[slot] = rec
        self.writes += 1
        self._persist()
        return rec

    def boot_image(self, ram_session: dict[str, Any] | None = None, *, for_load: bool = False) -> BootResult:
        """ICD §11.3 rules 2–5. ``for_load``: LOAD_PARAMS (no record / hard-rule image → ok=False)."""
        defaults = {p.key: p.default for p in pgen.PARAMS}
        session = {p.key: (ram_session or defaults)[p.key] for p in pgen.PARAMS if not p.nvm}
        rec = self.newest_valid()
        if rec is None:
            if for_load:
                return BootResult({}, None, ok=False)
            return BootResult(defaults, "PARAMS_DEFAULTED", 1 if all(r is None for r in self.records) else 2,
                              defaulted=True)
        values = dict(defaults)
        values.update(session)
        replaced = 0
        migration = rec.dict_hash != pgen.PARAM_DICT_HASH
        for p in pgen.PARAMS:
            if not p.nvm:
                continue
            if p.id in rec.values and p.in_range(rec.values[p.id]):
                v = rec.values[p.id]
                values[p.key] = bool(v) if p.type == pgen.ParamType.BOOL else v
            else:
                replaced += 1
        if check_hard_rules(values):
            if for_load:
                return BootResult({}, "PARAMS_DEFAULTED", 4, ok=False)
            return BootResult(defaults, "PARAMS_DEFAULTED", 4, defaulted=True, record_seq=rec.seq)
        if migration:
            return BootResult(values, "PARAMS_DEFAULTED", 3, defaulted=True, record_seq=rec.seq)
        return BootResult(values, "PARAMS_LOADED", replaced, defaulted=False, record_seq=rec.seq)

    def differs(self, ram: dict[str, Any]) -> bool:
        """CFG_DIRTY rule (ICD §11.2)."""
        rec = self.newest_valid()
        if rec is None or rec.dict_hash != pgen.PARAM_DICT_HASH:
            return True
        for p in pgen.PARAMS:
            if p.nvm and rec.values.get(p.id) != ram[p.key]:
                return True
        return False

    def summary(self) -> dict[str, Any]:
        return {"writes": self.writes, "records": [r.to_json() if r else None for r in self.records]}


def crc32_of(values: dict[int, Any]) -> int:  # pragma: no cover - informative only
    return zlib.crc32(json.dumps(values, sort_keys=True).encode())
