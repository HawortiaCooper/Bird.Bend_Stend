"""Report marks and mark presets (SW_design §13.6; SRS SW-META-001/002).

``TestMarks``: specimen, number, operator, notes + user-defined custom ``(key, value)`` pairs (add / rename /
remove = a new tuple). ``check_marks`` refuses empty or duplicate custom keys. Presets are ``*.bbmarks.json``
(``bird.bend.marks`` v1) files in ``<data>/presets``.

Implements: SW-META-001, SW-META-002 (presets, marks in recordings)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import TestMarks
from bend_stand.core.schema import atomic_write_json, read_json

KIND = "marks"
VERSION = 1
SUFFIX = ".bbmarks.json"


def check_marks(m: TestMarks) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for i, kv in enumerate(m.custom):
        if not isinstance(kv, tuple) or len(kv) != 2:
            errors.append(f"custom field {i + 1}: (key, value) pair expected")
            continue
        k = str(kv[0]).strip()
        if not k:
            errors.append(f"custom field {i + 1}: empty key")
        elif k.lower() in seen:
            errors.append(f"custom field {k!r}: duplicate key")
        seen.add(k.lower())
    return errors


def marks_to_dict(m: TestMarks) -> dict[str, Any]:
    return {"specimen": m.specimen, "number": m.number, "operator": m.operator, "notes": m.notes,
            "custom": [{"key": k, "value": v} for k, v in m.custom]}


def marks_from_dict(d: dict[str, Any]) -> TestMarks:
    try:
        custom = tuple((str(c["key"]), str(c.get("value", ""))) for c in d.get("custom", []))
        m = TestMarks(str(d.get("specimen", "")), str(d.get("number", "")), str(d.get("operator", "")),
                      str(d.get("notes", "")), custom)
    except (KeyError, TypeError, AttributeError) as exc:
        raise FileFormatError(f"marks: {exc}") from exc
    errs = check_marks(m)
    if errs:
        raise FileFormatError("; ".join(errs))
    return m


def save_preset(path: str | Path, m: TestMarks) -> None:
    atomic_write_json(path, {"schema": f"bird.bend.{KIND}", "schema_version": VERSION, **marks_to_dict(m)})


def load_preset(path: str | Path) -> TestMarks:
    return marks_from_dict(read_json(path, KIND, VERSION))


def list_presets(folder: Path) -> list[str]:
    if not folder.exists():
        return []
    return sorted(str(p) for p in folder.glob(f"*{SUFFIX}"))


__all__ = ["check_marks", "marks_to_dict", "marks_from_dict", "save_preset", "load_preset", "list_presets", "SUFFIX"]
