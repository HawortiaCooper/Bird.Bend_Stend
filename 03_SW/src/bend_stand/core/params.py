"""Parameter store, local checks, write plan and the board-config file (SW_design §5.3, SW-CFG-001…004).

* ``ParamStore`` holds the board values as last read, the dictionary metadata (``params_gen``) and the keys
  managed by the backend (``safety.load_raw_min/max``, ``safety.zero_raw`` — ThresholdManager, F-B-21).
* ``check_edits(values, edits, *, moving)`` — pure, < 1 ms, GUI thread (GRQ-B-03): unknown / type / range / enum /
  f32 finite, hard rules H1–H5 on board ⊕ edits, locked keys, ``reboot_required`` INFO, not-``moving_ok`` keys
  while moving. ``write_and_verify`` step (1) uses exactly this function.
* ``write_plan`` = ``calc.paramrules.write_order`` of the changed keys.
* ``*.bbboard.json`` (``bird.bend.board`` v1): ``save_board_config`` / ``load_board_config`` — the loaded file
  fills edit fields only (unknown / missing / out-of-range keys and a hash mismatch are reported).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/params.py @37c87471 (pattern: store + issues + file;
rewritten: bend dictionary, H1–H5 from calc.paramrules, no jsonschema).

Implements: SW-CFG-001, SW-CFG-002, SW-CFG-003
"""
from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from typing import Any

from bend_stand.calc.paramrules import check_hard_rules, write_order
from bend_stand.core import params_gen as pgen
from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import BoardConfigFile, Issue, IssueSeverity
from bend_stand.core.schema import atomic_write_json, read_json, unknown_keys

LOCKED_KEYS: frozenset[str] = frozenset({"safety.load_raw_min", "safety.load_raw_max", "safety.zero_raw"})
BOARD_SCHEMA = "board"
BOARD_VERSION = 1
E, W, I = IssueSeverity.ERROR, IssueSeverity.WARN, IssueSeverity.INFO


def normalise(meta: pgen.ParamMeta, value: Any) -> Any:
    """Typed Python value of ``value`` for ``meta`` (enum NAME → code, bool, f32 binary32); ``ValueError``."""
    t = meta.type
    if t == pgen.ParamType.ENUM:
        if isinstance(value, str):
            return meta.enum_value(value)
        if isinstance(value, bool) or not isinstance(value, int) and not (
                isinstance(value, float) and value.is_integer()):
            raise ValueError(f"{meta.key}: enum code expected")
        return int(value)
    if t == pgen.ParamType.BOOL:
        if isinstance(value, bool):
            return value
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"{meta.key}: bool expected")
    if t == pgen.ParamType.F32:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{meta.key}: number expected")
        v = float(value)
        if not math.isfinite(v):
            raise ValueError(f"{meta.key}: not finite")
        from bend_stand.calc.motion import f32  # noqa: PLC0415

        return f32(v)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{meta.key}: integer expected")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{meta.key}: integer expected")
    return int(value)


def check_edits(values: Mapping[str, Any], edits: Mapping[str, Any], *, moving: bool = False,
                locked: frozenset[str] = LOCKED_KEYS) -> list[Issue]:
    """Local checks of a set of edits against the board values (GRQ-B-03; = write_and_verify step 1)."""
    issues: list[Issue] = []
    merged = dict(values)
    for key, raw in edits.items():
        meta = pgen.BY_KEY.get(key)
        if meta is None:
            issues.append(Issue(key, E, "UNKNOWN_KEY", f"{key}: not in the parameter dictionary"))
            continue
        if key in locked:
            issues.append(Issue(key, E, "LOCKED", f"{key}: managed by the backend (load thresholds)"))
            continue
        try:
            v = normalise(meta, raw)
        except (ValueError, KeyError) as exc:
            issues.append(Issue(key, E, "TYPE", str(exc)))
            continue
        if not meta.in_range(v):
            issues.append(Issue(key, E, "RANGE", f"{key}: {raw!r} outside [{meta.min}, {meta.max}]"))
            continue
        merged[key] = v
        if moving and not meta.moving_ok and values.get(key) != v:
            issues.append(Issue(key, E, "MOVING", f"{key}: cannot be changed while the axis moves"))
        if meta.reboot_required and values.get(key) != v:
            issues.append(Issue(key, I, "REBOOT_REQUIRED", f"{key}: effective after Save + Reboot"))
    if not any(i.severity == E and i.code in ("TYPE", "RANGE", "UNKNOWN_KEY") for i in issues):
        for viol in check_hard_rules(merged):
            keys = [k for k in edits if k in (viol.key, viol.other_key)] or [viol.key]
            for k in keys:
                issues.append(Issue(k, E, f"RULE_{viol.rule}", f"{k}: {viol.text}"))
    lt = merged.get("safety.link_timeout_ms")
    if "safety.link_timeout_ms" in edits and isinstance(lt, int) and lt < 750:
        issues.append(Issue("safety.link_timeout_ms", W, "LINK_TIMEOUT",
                            "link timeout < 750 ms is not robust against lost heartbeats (ICD §9.2)"))
    return issues


def write_plan(values: Mapping[str, Any], edits: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Changed keys in a SET order that keeps H1–H5 true after each SET (ICD §11.4)."""
    target = {k: normalise(pgen.BY_KEY[k], v) for k, v in edits.items()}
    return write_order(dict(values), target)


class ParamStore:
    """Board values as last read (thread-safe snapshot dict) + dictionary metadata."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, Any] = {}
        self.read_ok = False

    def metas(self) -> tuple[pgen.ParamMeta, ...]:
        return pgen.PARAMS

    def groups(self) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        return pgen.GROUPS

    def defaults(self) -> dict[str, Any]:
        return {p.key: p.default for p in pgen.PARAMS}

    def values(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._values.get(key, default)

    def set_all(self, values: Mapping[str, Any]) -> None:
        with self._lock:
            self._values = dict(values)
            self.read_ok = True

    def update(self, key: str, value: Any) -> None:
        with self._lock:
            self._values[key] = value

    def clear(self) -> None:
        with self._lock:
            self._values = {}
            self.read_ok = False


# ============================================================================== board-config file (§13.1)

def save_board_config(path: str, values: Mapping[str, Any], *, sw_version: str = "", fw_version: str = "",
                      board_uid: str = "", saved_utc: str = "") -> None:
    """Write ``*.bbboard.json``: all dictionary parameters except the session values (enums by NAME)."""
    params: dict[str, Any] = {}
    for meta in pgen.PARAMS:
        if not meta.nvm or meta.key not in values:
            continue
        v = values[meta.key]
        if meta.type == pgen.ParamType.ENUM and meta.enum:
            v = meta.enum.get(int(v), int(v))
        elif meta.type == pgen.ParamType.BOOL:
            v = bool(v)
        elif meta.type == pgen.ParamType.F32:
            v = float(v)
        params[meta.key] = v
    atomic_write_json(path, {
        "schema": f"bird.bend.{BOARD_SCHEMA}", "schema_version": BOARD_VERSION, "saved_utc": saved_utc,
        "sw_version": sw_version, "fw_version": fw_version, "board_uid": board_uid,
        "param_dict_hash": f"0x{pgen.PARAM_DICT_HASH:08X}", "param_dict_version": pgen.PARAM_DICT_VERSION,
        "params": params})


def load_board_config(path: str) -> BoardConfigFile:
    """Read ``*.bbboard.json`` → values for the edit fields (never written by itself, SW-CFG-002)."""
    d = read_json(path, BOARD_SCHEMA, BOARD_VERSION)
    raw = d.get("params")
    if not isinstance(raw, dict):
        raise FileFormatError(f"{path}: 'params' missing")
    issues: list[Issue] = []
    for k in unknown_keys(d, ("saved_utc", "sw_version", "fw_version", "board_uid", "param_dict_hash",
                              "param_dict_version", "params")):
        issues.append(Issue(None, W, "UNKNOWN_FIELD", f"unknown field {k!r}"))
    try:
        file_hash = int(str(d.get("param_dict_hash", "0")), 16)
    except ValueError:
        file_hash = None
    values: dict[str, Any] = {}
    unknown, out_of_range = [], []
    for key, v in raw.items():
        meta = pgen.BY_KEY.get(key)
        if meta is None or not meta.nvm:
            unknown.append(key)
            continue
        try:
            nv = normalise(meta, v)
        except (ValueError, KeyError):
            out_of_range.append(key)
            continue
        if not meta.in_range(nv):
            out_of_range.append(key)
            continue
        values[key] = nv
    missing = [p.key for p in pgen.PARAMS if p.nvm and p.key not in raw]
    for k in unknown:
        issues.append(Issue(k, W, "UNKNOWN_KEY", f"{k}: not in this dictionary (ignored)"))
    for k in out_of_range:
        issues.append(Issue(k, E, "RANGE", f"{k}: value invalid or out of range (ignored)"))
    for k in missing:
        issues.append(Issue(k, I, "MISSING", f"{k}: not in the file (board value kept)"))
    mismatch = file_hash != pgen.PARAM_DICT_HASH
    if mismatch:
        issues.append(Issue(None, W, "HASH_MISMATCH",
                            "file made for another parameter dictionary — values matched by key"))
    return BoardConfigFile(str(path), values, tuple(unknown), tuple(missing), tuple(out_of_range), mismatch,
                           file_hash, tuple(issues))
