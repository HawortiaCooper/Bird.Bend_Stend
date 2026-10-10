"""Hand-written JSON helpers (SW_design §13, KD-11: no jsonschema): atomic write, schema header checks.

Every file carries ``"schema": "bird.bend.<kind>"`` and an integer ``"schema_version"``; readers accept their
own version, refuse newer ones (``FileFormatError``) and warn about unknown keys.

Implements: SW-CFG-002 (board-config file), SW-ACQ-002 (sidecar)
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from bend_stand.core.errors import FileFormatError


def atomic_write_text(path: str | os.PathLike[str], text: str) -> None:
    """Write via a temp file in the same directory + ``os.replace`` (never a half-written file)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str | os.PathLike[str], obj: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


MAX_JSON_BYTES = 20 * 1024 * 1024       #: SWR-12: settings / calibration / sequence files above this are refused


def parse_json_text(text: str, where: str) -> Any:
    """``json.loads`` with the ``FileFormatError`` contract (SWR-12): invalid JSON, deep nesting (``RecursionError``)
    or an oversized text never escape as another exception type."""
    if len(text) > MAX_JSON_BYTES:
        raise FileFormatError(f"{where}: file too large ({len(text)} characters > {MAX_JSON_BYTES})")
    try:
        return json.loads(text)
    except RecursionError as exc:
        raise FileFormatError(f"{where}: nested too deeply") from exc
    except (ValueError, MemoryError) as exc:
        raise FileFormatError(f"{where}: not valid JSON: {exc}") from exc


def read_json(path: str | os.PathLike[str], kind: str, version: int) -> dict[str, Any]:
    """Load a ``bird.bend.<kind>`` file; refuse other kinds and newer versions (``FileFormatError`` on any error)."""
    # Implements: SW-SEQF-001, SW-CAL-009, SW-LIM-003 (loader contract, SWR-12)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise FileFormatError(f"cannot read {path}: {exc}") from exc
    d = parse_json_text(text, str(path))
    if not isinstance(d, dict) or d.get("schema") != f"bird.bend.{kind}":
        raise FileFormatError(f"{path}: not a bird.bend.{kind} file")
    v = d.get("schema_version")
    if not isinstance(v, int):
        raise FileFormatError(f"{path}: schema_version missing")
    if v > version:
        raise FileFormatError(f"{path}: made by a newer SW version (schema_version {v} > {version})")
    return d


def unknown_keys(d: Mapping[str, Any], known: Iterable[str]) -> list[str]:
    k = set(known) | {"schema", "schema_version"}
    return sorted(x for x in d if x not in k)
