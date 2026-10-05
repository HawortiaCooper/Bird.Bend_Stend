"""Load input of the PC limits: active load calibration, session tare, no-specimen mode (SW_design §6.1 rule 2,
§6.3, §6.7, §9.4–§9.6; SRS SAF-SW-001/002, SW-CAL-009, SW-TARE-002, SW-LIM-004).

``LoadInput`` is the single owner of

* the **active load calibration** (``ActiveLoadCal`` from ``active_load.json``; K, the largest calibration force,
  the AFE block, the zero point's raw mean / std),
* the **session tare** (``TareResult``; valid only for the board UID it was taken on, never loaded from a file —
  D-29 j) with one undo level,
* the **no-specimen mode** flag (session-only; ends at disconnect / exit — SW-LIM-004).

``evaluate(params, info)`` → ``LoadInputState``: whether the static load input is valid for the limits and why not
(no calibration, no tare, AFE-configuration mismatch → calibration invalid for the limits (D-29 l), AFE synthetic).
Dynamic per-sample conditions (AFE stale / saturated / no data) are added by the safety supervisor and the gates.
``scale_config`` → the ``ScaleConfig`` of the pipeline; ``threshold_target`` → the SAF-SW-002 target (calibrated
when calibration + tare are valid — also in the no-specimen mode — else the dictionary defaults, never widened).

Implements: SAF-SW-001 (load input validity), SAF-SW-002 (target selection), SW-CAL-009 (AFE mismatch rule),
SW-TARE-002 (session-only tare), SW-LIM-004 (no-specimen flag)
"""
from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bend_stand.calc.loadcal import afe_block, afe_matches
from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import Bend3pGeometry, DeviceInfo, TareResult
from bend_stand.core.scaling import AFE_MISMATCH, NO_CAL, NO_TARE, SYNTHETIC, ScaleConfig, reason_text
from bend_stand.core.safety import ThresholdTarget, calibrated_target


@dataclass(frozen=True)
class ActiveLoadCal:
    record: Mapping[str, Any]
    k: float
    f_cal_max: float
    afe: Mapping[str, Any]
    raw_zero: float | None
    std_zero: float | None
    cal_id: str
    status: str
    low_span: bool
    created_utc: str | None
    file: str | None

    @classmethod
    def from_record(cls, rec: Mapping[str, Any]) -> ActiveLoadCal:
        fit = rec["fit"]
        pts = rec["points"]
        zero = next((p for p in pts if float(p.get("mass_kg", p.get("force_n", 1.0))) == 0.0), None)
        f_max = max(abs(float(p["force_n"])) for p in pts)
        std0 = None if zero is None else zero.get("raw_std")
        return cls(dict(rec), float(fit["k_n_per_count"]), f_max, dict(rec["afe"]),
                   None if zero is None else float(zero["raw_mean"]),
                   None if std0 is None or not math.isfinite(float(std0)) else float(std0),
                   str(rec.get("file") or rec.get("created_utc") or "load-cal"), str(fit.get("status", "")),
                   bool(fit.get("low_span", False)), rec.get("created_utc"), rec.get("file"))


@dataclass(frozen=True)
class LoadInputState:
    valid: bool                   # static input valid for the PC load limits and the calibrated FW thresholds
    reason_bits: int
    reason: str | None
    cal_valid: bool               # a calibration that matches the board AFE configuration
    afe_mismatch: bool
    tare_valid: bool


class LoadInput:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.cal: ActiveLoadCal | None = None
        self.cal_error: str | None = None
        self.tare: TareResult | None = None
        self._undo: list[TareResult | None] = []
        self.no_specimen = False
        self.x_zero_mm = 0.0
        self.bend3p: Bend3pGeometry | None = None
        self.compliance = 0.0
        self.reset_epoch = 0
        self._tare_seq = 0

    # ---- calibration ----------------------------------------------------------------------------------------
    def set_calibration(self, record: Mapping[str, Any] | None, error: str | None = None) -> None:
        with self._lock:
            self.cal = None if record is None else ActiveLoadCal.from_record(record)
            self.cal_error = error

    # ---- tare -------------------------------------------------------------------------------------------------
    def next_tare_id(self, uid: str | None) -> str:
        with self._lock:
            self._tare_seq += 1
            return f"T{self._tare_seq}-{uid or 'board'}"

    def set_tare(self, tare: TareResult) -> None:
        with self._lock:
            self._undo = [self.tare]
            self.tare = tare

    def can_undo(self) -> bool:
        with self._lock:
            return bool(self._undo)

    def undo_tare(self) -> bool:
        with self._lock:
            if not self._undo:
                return False
            self.tare = self._undo.pop()
            return True

    def clear_tare(self) -> None:
        with self._lock:
            self.tare = None
            self._undo = []

    def tare_for(self, uid: str | None) -> TareResult | None:
        t = self.tare
        return t if t is not None and (uid is None or t.board_uid == uid) else None

    # ---- evaluation -------------------------------------------------------------------------------------------
    @staticmethod
    def board_afe(params: Mapping[str, Any]) -> dict[str, Any]:
        return afe_block(params.get("afe.gain_channel"), params.get("afe.rate_sps"))

    def evaluate(self, params: Mapping[str, Any], info: DeviceInfo | None) -> LoadInputState:
        cal, uid = self.cal, (info.uid if info is not None else None)
        bits = 0
        mismatch = False
        if cal is None:
            bits |= NO_CAL
        elif params and not afe_matches(cal.afe, self.board_afe(params)):
            bits |= AFE_MISMATCH
            mismatch = True
        tare = self.tare_for(uid)
        if tare is None:
            bits |= NO_TARE
        if info is not None and info.feature_mask & pg.Features.AFE_SYNTHETIC:
            bits |= SYNTHETIC
        cal_ok = cal is not None and not mismatch
        return LoadInputState(bits == 0, bits, None if bits == 0 else reason_text(bits), cal_ok, mismatch,
                              tare is not None)

    def scale_config(self, params: Mapping[str, Any], info: DeviceInfo | None) -> ScaleConfig:
        st = self.evaluate(params, info)
        cal = self.cal
        tare = self.tare_for(info.uid if info is not None else None)
        return ScaleConfig(cal.k if cal is not None else None, tare.tare_raw if tare is not None else None,
                           st.reason_bits, cal.f_cal_max if cal is not None else None, self.x_zero_mm, self.bend3p,
                           self.compliance, self.reset_epoch)

    def threshold_target(self, params: Mapping[str, Any], info: DeviceInfo | None,
                         fw_level_n: float) -> ThresholdTarget:
        """SAF-SW-002: calibrated thresholds when a valid calibration (matching AFE) and a tare of this board exist
        (also in the no-specimen mode, SW-LIM-004), else the nominal defaults (never widened)."""
        st = self.evaluate(params, info)
        cal = self.cal
        tare = self.tare_for(info.uid if info is not None else None)
        if st.cal_valid and tare is not None and cal is not None and not st.reason_bits & SYNTHETIC:
            return calibrated_target(cal.k, tare.tare_raw, fw_level_n, cal_id=cal.cal_id, tare_id=tare.tare_id)
        return ThresholdTarget()


__all__ = ["LoadInput", "LoadInputState", "ActiveLoadCal"]
