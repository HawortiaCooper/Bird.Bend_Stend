"""Calibration store (SW_design §9.6, §13.2, §13.3, §13.11; R4 §6.4).

``<data>/calibration/``:

* ``load_<serial>_<UTC>.json`` + ``active_load.json`` (``bird.bend.cal.load`` v1, R4 §6.4 schema: sensor, afe, board,
  direction, ``push_calibrated = false``, g_used, points with statistics, fit with status, LOW_SPAN, notes);
* ``travel_<UTC>.json`` + ``active_travel.json`` (``bird.bend.cal.travel`` v1);
* ``travel_restore_pending.json`` (``bird.bend.cal.travel_restore`` v1) — exists only while a trial steps/mm may be in
  the board RAM (§9.3.1).

Previous files are kept; the active copies are loaded at start. Every write is atomic. A file that cannot be read or
fails the field checks is reported (``FileFormatError``) and never becomes active.

Implements: SW-CAL-004 (travel file), SW-CAL-009 (load file, active copy, history), SW-CAL-001 (restore record)
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bend_stand.core.errors import FileFormatError
from bend_stand.core.schema import atomic_write_json, read_json

LOAD_KIND, TRAVEL_KIND, RESTORE_KIND = "cal.load", "cal.travel", "cal.travel_restore"
VERSION = 1
ACTIVE_LOAD, ACTIVE_TRAVEL, RESTORE_FILE = "active_load.json", "active_travel.json", "travel_restore_pending.json"


def _stamp(utc_iso: str) -> str:
    """``2026-10-05T12:00:00.123456Z`` → ``20261005T120000Z``."""
    return utc_iso[:19].replace("-", "").replace(":", "") + "Z"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s.strip()).strip("-")[:32] or "cell"


def validate_load(d: Mapping[str, Any]) -> None:
    """Field checks of a load-calibration record (raises ``FileFormatError``)."""
    try:
        fit = d["fit"]
        k = float(fit["k_n_per_count"])
        if not math.isfinite(k) or k == 0:
            raise ValueError("k_n_per_count must be finite and non-zero")
        if fit.get("status") not in ("PASS", "WARN", "UNVERIFIED_LINEARITY"):
            raise ValueError(f"status {fit.get('status')!r} cannot be active")
        pts = d["points"]
        if not isinstance(pts, list) or len(pts) < 2:
            raise ValueError("at least two points")
        for p in pts:
            float(p["raw_mean"]), float(p["force_n"])
        afe = d["afe"]
        for key in ("channel", "gain", "rate_sps"):
            afe[key]
    except (KeyError, TypeError, ValueError) as exc:
        raise FileFormatError(f"load calibration record invalid: {exc}") from exc


def validate_travel(d: Mapping[str, Any]) -> None:
    try:
        spm = float(d["spm2"])
        if not math.isfinite(spm) or spm <= 0:
            raise ValueError("spm2 must be > 0")
    except (KeyError, TypeError, ValueError) as exc:
        raise FileFormatError(f"travel calibration record invalid: {exc}") from exc


class CalibrationStore:
    """Files of the calibration folder (no board access, no threads)."""

    def __init__(self, base: Path) -> None:
        self.dir = Path(base)

    # ---- generic --------------------------------------------------------------------------------------------
    def _unique(self, name: str) -> Path:
        p = self.dir / name
        stem, suf = p.stem, p.suffix
        i = 2
        while p.exists():
            p = self.dir / f"{stem}_{i}{suf}"
            i += 1
        return p

    def _read(self, name: str, kind: str) -> dict[str, Any] | None:
        p = self.dir / name
        if not p.exists():
            return None
        return read_json(p, kind, VERSION)

    # ---- load -------------------------------------------------------------------------------------------------
    def active_load(self) -> dict[str, Any] | None:
        """The active load calibration (``FileFormatError`` if the file exists but is invalid)."""
        d = self._read(ACTIVE_LOAD, LOAD_KIND)
        if d is not None:
            validate_load(d)
        return d

    def save_load(self, record: Mapping[str, Any]) -> Path:
        """Write ``load_<serial>_<UTC>.json`` and make it active (``active_load.json``)."""
        rec = {"schema": f"bird.bend.{LOAD_KIND}", "schema_version": VERSION, **dict(record)}
        validate_load(rec)
        serial = _safe(str(rec.get("sensor", {}).get("serial") or "cell"))
        path = self._unique(f"load_{serial}_{_stamp(str(rec.get('created_utc', '')))}.json")
        rec["file"] = path.name
        atomic_write_json(path, rec)
        atomic_write_json(self.dir / ACTIVE_LOAD, rec)
        return path

    # ---- travel -----------------------------------------------------------------------------------------------
    def active_travel(self) -> dict[str, Any] | None:
        d = self._read(ACTIVE_TRAVEL, TRAVEL_KIND)
        if d is not None:
            validate_travel(d)
        return d

    def save_travel(self, record: Mapping[str, Any]) -> Path:
        rec = {"schema": f"bird.bend.{TRAVEL_KIND}", "schema_version": VERSION, **dict(record)}
        validate_travel(rec)
        path = self._unique(f"travel_{_stamp(str(rec.get('created_utc', '')))}.json")
        rec["file"] = path.name
        atomic_write_json(path, rec)
        atomic_write_json(self.dir / ACTIVE_TRAVEL, rec)
        return path

    # ---- history ----------------------------------------------------------------------------------------------
    def history(self, kind: str) -> list[str]:
        """Calibration files of ``kind`` ("load" / "travel"), newest first (the active copies excluded)."""
        if kind not in ("load", "travel") or not self.dir.exists():
            return []
        files = [p for p in self.dir.glob(f"{kind}_*.json")]
        files.sort(key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
        return [str(p) for p in files]

    # ---- travel restore-pending record (§9.3.1) ---------------------------------------------------------------
    def restore_pending(self) -> dict[str, Any] | None:
        try:
            return self._read(RESTORE_FILE, RESTORE_KIND)
        except FileFormatError:
            return None

    def write_restore(self, board_uid: str | None, spm0: float, spm_trial: float | None, created_utc: str) -> None:
        atomic_write_json(self.dir / RESTORE_FILE, {
            "schema": f"bird.bend.{RESTORE_KIND}", "schema_version": VERSION, "board_uid": board_uid,
            "spm0": float(spm0), "spm_trial": None if spm_trial is None else float(spm_trial),
            "created_utc": created_utc})

    def delete_restore(self) -> None:
        try:
            (self.dir / RESTORE_FILE).unlink()
        except FileNotFoundError:
            pass


__all__ = ["CalibrationStore", "validate_load", "validate_travel", "ACTIVE_LOAD", "ACTIVE_TRAVEL", "RESTORE_FILE"]
