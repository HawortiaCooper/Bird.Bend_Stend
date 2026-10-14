"""Minimal JSON-Schema validator (subset of draft 2020-12) for the files the application ships and writes
(bring-up step definitions, bring-up record, D-56 a). No third-party dependency (SW_design KD-11).

Supported keywords: ``type`` (str or list; "integer" excludes bool), ``enum``, ``const``, ``required``,
``properties``, ``additionalProperties`` (bool or schema), ``items`` (schema), ``minItems``, ``maxItems``,
``minimum``, ``maximum``, ``minLength``, ``maxLength``, ``pattern`` (``re.search``), ``anyOf``, ``oneOf``, ``allOf``,
``$ref`` to ``#/$defs/<name>``. Unknown keywords are ignored (``title``, ``description``, ``$schema``, ``$id``).

Implements: SW-BRU-003 (the bring-up JSON validates against its schema), SW-BRU-001 (step-definition file check)
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

_TYPES: dict[str, Any] = {
    "object": lambda v: isinstance(v, Mapping),
    "array": lambda v: isinstance(v, (list, tuple)),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    and (not isinstance(v, float) or math.isfinite(v)),
    "null": lambda v: v is None,
}
MAX_ERRORS = 50


def validate(instance: Any, schema: Mapping[str, Any]) -> list[str]:
    """Return the list of violations (``"<json path>: <text>"``); empty = valid."""
    errors: list[str] = []
    _check(instance, schema, schema, "$", errors)
    return errors[:MAX_ERRORS]


def _resolve(ref: str, root: Mapping[str, Any]) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(v: Any, s: Mapping[str, Any], root: Mapping[str, Any], path: str, errors: list[str]) -> None:
    if len(errors) >= MAX_ERRORS:
        return
    if "$ref" in s:
        _check(v, _resolve(str(s["$ref"]), root), root, path, errors)
    t = s.get("type")
    if t is not None:
        names = [t] if isinstance(t, str) else list(t)
        if not any(_TYPES[n](v) for n in names):
            errors.append(f"{path}: expected {'/'.join(names)}, got {type(v).__name__}")
            return
    if "const" in s and v != s["const"]:
        errors.append(f"{path}: must be {s['const']!r}")
    if "enum" in s and v not in s["enum"]:
        errors.append(f"{path}: {v!r} not one of {list(s['enum'])}")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errors.append(f"{path}: {v} < minimum {s['minimum']}")
        if "maximum" in s and v > s["maximum"]:
            errors.append(f"{path}: {v} > maximum {s['maximum']}")
    if isinstance(v, str):
        if "minLength" in s and len(v) < s["minLength"]:
            errors.append(f"{path}: shorter than {s['minLength']}")
        if "maxLength" in s and len(v) > s["maxLength"]:
            errors.append(f"{path}: longer than {s['maxLength']}")
        if "pattern" in s and re.search(str(s["pattern"]), v) is None:
            errors.append(f"{path}: {v!r} does not match {s['pattern']!r}")
    if isinstance(v, Mapping):
        for k in s.get("required", ()):
            if k not in v:
                errors.append(f"{path}: missing {k!r}")
        props = s.get("properties", {})
        extra = s.get("additionalProperties", True)
        for k, item in v.items():
            if k in props:
                _check(item, props[k], root, f"{path}.{k}", errors)
            elif extra is False:
                errors.append(f"{path}: unknown key {k!r}")
            elif isinstance(extra, Mapping):
                _check(item, extra, root, f"{path}.{k}", errors)
    if isinstance(v, (list, tuple)):
        if "minItems" in s and len(v) < s["minItems"]:
            errors.append(f"{path}: fewer than {s['minItems']} items")
        if "maxItems" in s and len(v) > s["maxItems"]:
            errors.append(f"{path}: more than {s['maxItems']} items")
        if "items" in s:
            for i, item in enumerate(v):
                _check(item, s["items"], root, f"{path}[{i}]", errors)
    for sub in s.get("allOf", ()):
        _check(v, sub, root, path, errors)
    for key in ("anyOf", "oneOf"):
        if key in s:
            hits = 0
            for sub in s[key]:
                e: list[str] = []
                _check(v, sub, root, path, e)
                hits += not e
            if hits == 0 or (key == "oneOf" and hits > 1):
                errors.append(f"{path}: matches {hits} of the {key} alternatives")


__all__ = ["validate", "MAX_ERRORS"]
