"""Sequence files ``*.bbseq.json`` (``bird.bend.sequence`` v2, SW_design §13.4, §10.7; SRS SW-SEQF-001).

``to_dict`` writes every field in a fixed key order (floats as JSON numbers = Python ``repr``, so a round trip is
identical); ``from_dict`` parses into a **new** ``Sequence`` with strict type checks — any error raises
``FileFormatError`` and the caller keeps its current sequence unchanged. Version 1 files are migrated
(``on_trim_fail`` dropped: SRS v0.6.2 has no continue option; ``travel_bound_mm`` ignored, D-32) with warnings.
Writes are atomic (temp file + ``os.replace``).

Implements: SW-SEQF-001
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from bend_stand.core.errors import FileFormatError
from bend_stand.core.schema import atomic_write_text
from bend_stand.core.sequencer.model import Loop, Sequence, Step, StepDefaults, StepKind

KIND = "sequence"
VERSION = 2
SUFFIX = ".bbseq.json"
STEP_FIELDS = ("uid", "kind", "target", "speed_mm_s", "accel_mm_s2", "settle_s", "capture_s", "step_time_s", "tol_n",
               "capture_during_move", "wait_operator", "label")
_FLOAT_OPT = {"target", "speed_mm_s", "tol_n"}
_FLOAT = {"accel_mm_s2", "settle_s", "capture_s", "step_time_s"}
_BOOL = {"capture_during_move", "wait_operator"}
_DEFAULT_FIELDS = ("speed_mm_s", "accel_mm_s2", "settle_s", "capture_s", "tol_n")
_TOP = ("name", "travel_ref", "k_est_n_mm", "pull_dir", "defaults", "steps", "loops", "notes")


def to_dict(seq: Sequence) -> dict[str, Any]:
    d = seq.defaults
    return {"schema": f"bird.bend.{KIND}", "schema_version": VERSION, "name": seq.name,
            "travel_ref": seq.travel_ref, "k_est_n_mm": float(seq.k_est_n_mm), "pull_dir": int(seq.pull_dir),
            "defaults": {k: float(getattr(d, k)) for k in _DEFAULT_FIELDS},
            "steps": [{k: (StepKind(s.kind).value if k == "kind" else getattr(s, k)) for k in STEP_FIELDS}
                      for s in seq.steps],
            "loops": [{"first": lp.first, "last": lp.last, "count": lp.count} for lp in seq.loops],
            "notes": seq.notes}


def dumps(seq: Sequence) -> str:
    return json.dumps(to_dict(seq), indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _num(v: Any, where: str, *, optional: bool = False) -> float | None:
    if v is None and optional:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise FileFormatError(f"{where}: number expected, got {v!r}")
    return float(v)


def _int(v: Any, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise FileFormatError(f"{where}: integer expected, got {v!r}")
    return v


def _str(v: Any, where: str) -> str:
    if not isinstance(v, str):
        raise FileFormatError(f"{where}: text expected, got {v!r}")
    return v


def from_dict(d: Any) -> tuple[Sequence, list[str]]:
    """Parse (and migrate) a sequence document → ``(Sequence, warnings)``; ``FileFormatError`` on any error."""
    if not isinstance(d, dict) or d.get("schema") != f"bird.bend.{KIND}":
        raise FileFormatError(f"not a bird.bend.{KIND} file")
    ver = d.get("schema_version")
    if not isinstance(ver, int) or isinstance(ver, bool):
        raise FileFormatError("schema_version missing")
    if ver > VERSION:
        raise FileFormatError(f"made by a newer SW version (schema_version {ver} > {VERSION})")
    warns: list[str] = []
    known = set(_TOP) | {"schema", "schema_version"}
    if ver == 1 and "on_trim_fail" in d:
        known.add("on_trim_fail")
        if d.get("on_trim_fail") != "stop":
            warns.append(f"on_trim_fail = {d.get('on_trim_fail')!r} ignored: a trim that does not converge is "
                         "NOT_REACHED and stops the sequence (SRS v0.6.2)")
    warns += [f"unknown key {k!r} ignored" for k in d if k not in known]
    for k in ("steps", "loops"):
        if not isinstance(d.get(k, []), list):
            raise FileFormatError(f"{k} must be a list")
    dd = d.get("defaults", {})
    if not isinstance(dd, dict):
        raise FileFormatError("defaults must be an object")
    defaults = StepDefaults(**{k: _num(dd[k], f"defaults.{k}") for k in _DEFAULT_FIELDS if k in dd})
    steps: list[Step] = []
    for i, sd in enumerate(d.get("steps", [])):
        w = f"steps[{i}]"
        if not isinstance(sd, dict):
            raise FileFormatError(f"{w}: object expected")
        kw: dict[str, Any] = {}
        for k, v in sd.items():
            if k == "travel_bound_mm":
                warns.append(f"{w}.travel_bound_mm ignored (D-32: the load-step bound is the nearer of the soft limit "
                              "and an enabled SW travel limit)")
                continue
            if k not in STEP_FIELDS:
                warns.append(f"{w}: unknown key {k!r} ignored")
                continue
            if k == "uid" or k == "label":
                kw[k] = _str(v, f"{w}.{k}")
            elif k == "kind":
                try:
                    kw[k] = StepKind(_str(v, f"{w}.kind"))
                except ValueError:
                    raise FileFormatError(f"{w}.kind: unknown step type {v!r}") from None
            elif k in _FLOAT_OPT:
                kw[k] = _num(v, f"{w}.{k}", optional=True)
            elif k in _FLOAT:
                kw[k] = _num(v, f"{w}.{k}")
            elif k in _BOOL:
                if not isinstance(v, bool):
                    raise FileFormatError(f"{w}.{k}: true/false expected")
                kw[k] = v
        if "uid" not in kw or "kind" not in kw:
            raise FileFormatError(f"{w}: uid and kind are required")
        steps.append(Step(**kw))
    loops: list[Loop] = []
    for i, ld in enumerate(d.get("loops", [])):
        w = f"loops[{i}]"
        if not isinstance(ld, dict) or not {"first", "last", "count"} <= set(ld):
            raise FileFormatError(f"{w}: first, last and count are required")
        loops.append(Loop(_int(ld["first"], f"{w}.first"), _int(ld["last"], f"{w}.last"),
                          _int(ld["count"], f"{w}.count")))
    tr = _str(d.get("travel_ref", "test"), "travel_ref")
    if tr not in ("test", "machine"):
        raise FileFormatError("travel_ref must be 'test' or 'machine'")
    pdir = _int(d.get("pull_dir", 1), "pull_dir")
    if pdir not in (1, -1):
        raise FileFormatError("pull_dir must be +1 or -1")
    seq = Sequence(name=_str(d.get("name", ""), "name"), steps=steps, loops=loops, travel_ref=tr,  # type: ignore[arg-type]
                   k_est_n_mm=float(_num(d.get("k_est_n_mm", 50.0), "k_est_n_mm")), pull_dir=pdir,
                   defaults=defaults, notes=_str(d.get("notes", ""), "notes"))
    return seq, warns


def loads(text: str) -> tuple[Sequence, list[str]]:
    from bend_stand.core.schema import parse_json_text  # noqa: PLC0415

    d = parse_json_text(text, "sequence file")              # SWR-12: deep nesting → FileFormatError
    try:
        return from_dict(d)
    except FileFormatError:
        raise
    except (RecursionError, TypeError, ValueError, KeyError, AttributeError) as exc:
        raise FileFormatError(f"sequence file: {type(exc).__name__}: {exc}") from exc


def load(path: str | Path) -> tuple[Sequence, list[str]]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise FileFormatError(f"cannot read {path}: {exc}") from exc
    try:
        return loads(text)
    except FileFormatError as exc:
        raise FileFormatError(f"{Path(path).name}: {exc.user_text}") from exc


def save(path: str | Path, seq: Sequence) -> None:
    atomic_write_text(path, dumps(seq))


__all__ = ["to_dict", "from_dict", "dumps", "loads", "load", "save", "VERSION", "SUFFIX"]
