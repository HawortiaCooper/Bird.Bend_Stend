"""Test report: steady-state results, ``report.json`` + self-contained ``report.html`` (SW_design §11, §15.5f B6-14;
SRS SW-REP-001…004).

The report is always built **from the recording** (``data.csv`` + ``meta.json``) — at the end of a sequence and
offline (``python -m bend_stand.core.report <rec_dir> …``) with the same code, so a regenerated report reproduces
the live numbers exactly (SW-REP-003): F = K·(raw − tare) per row with K / tare from the run's scale log (or the
recording snapshot), windows from the sidecar ``sequence_runs[].windows``, statistics by ``calc.steady.window_stats``.
Another calibration (file / record) and / or another tare can be applied (``cal=``, ``tare=``).

Contents: marks incl. custom fields, snapshot (calibration K / status / date / LOW_SPAN, tare, limits, FW
thresholds, versions, final k_est), warnings (LOW_SPAN, push forces tension-calibrated, no-specimen mode,
INCOMPLETE windows, NOT_REACHED, guard stops, frame losses, incomplete recording, extrapolated forces, re-applied
calibration / tare), F–x and F–t figures as inline SVG (≤ 5000 points, capture windows shaded), the step result table
per step and loop iteration, the sequence events, optional 3-point-bend outputs (σ, ε per window, E_f from the OLS
slope of the window means; SW-REP-004, off by default).

Report-tab support (GRQ-B-11): ``list_recordings(root)``, ``load_result(folder)``.

Hardening (review SW_code_review v1.1): every sidecar value is type-coerced and HTML-escaped, the page carries a
script-free Content-Security-Policy (SWR-11); a calibration to re-apply is schema / ``validate_load`` / finite-number
checked and a non-PASS status is a report warning (SWR-24).

Implements: SW-REP-001, SW-REP-002 (offline extraction), SW-REP-003, SW-REP-004
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from bend_stand import __version__
from bend_stand.calc.derived import bend3p_modulus, bend3p_strain, bend3p_stress
from bend_stand.calc.loadcal import EXTRAPOLATION_FACTOR
from bend_stand.calc.steady import median_rate_sps, window_stats
from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import Bend3pGeometry, RecordingInfo, ReportPaths, ReportResult, StepResult
from bend_stand.core.schema import atomic_write_json, atomic_write_text

KIND = "report"
VERSION = 1
MAX_POINTS = 5000
NAN = float("nan")
RAW_OK, RAW_SETTLING = 0, 3


# ============================================================================================ result (de)serialisation
def _js(v: Any) -> Any:
    """JSON-safe value: NaN / inf → None, tuples → lists, nested mappings."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (list, tuple)):
        return [_js(x) for x in v]
    if isinstance(v, Mapping):
        return {str(k): _js(x) for k, x in v.items()}
    if isinstance(v, (np.floating, np.integer)):
        return _js(v.item())
    return v


def result_to_dict(r: StepResult) -> dict[str, Any]:
    return {k: _js(getattr(r, k)) for k in StepResult.__dataclass_fields__}


def _f(v: Any) -> float:
    """Float field from a sidecar / report JSON; anything non-numeric → NaN (never raw text, SWR-11)."""
    if v is None or isinstance(v, bool):
        return NAN
    try:
        return float(v)
    except (TypeError, ValueError):
        return NAN


def _opt_f(v: Any) -> float | None:
    return None if v is None else _f(v)


def _int(v: Any) -> int | None:
    """Integer field: int, integral float or digit string; anything else → None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("+-").isdigit():
        return int(v.strip())
    return None


#: StepResult fields by JSON type (everything not listed is float / NaN)
_ID_INT = ("exec_idx", "step_idx")                      # identity: invalid → FileFormatError
_COUNT_INT = ("n", "trim_iterations")                   # counters: invalid → 0
_TEXT = ("uid", "label", "kind", "unit", "text")
_OPT_FLOAT = ("target", "tol_n", "t_reached_s", "k_est_n_mm")


def result_from_dict(d: Mapping[str, Any]) -> StepResult:
    """``StepResult`` from a sidecar run log / ``report.json`` entry with every field coerced to its declared type
    (review finding SWR-11: a recording may come from another PC; no raw JSON value reaches the HTML). Identity
    fields that are not integers raise ``FileFormatError``; other malformed values become NaN / 0 / "" / ()."""
    if not isinstance(d, Mapping):
        raise FileFormatError(f"step result is not an object: {type(d).__name__}")
    kw: dict[str, Any] = {}
    for name, fld in StepResult.__dataclass_fields__.items():
        if name not in d:
            continue
        v = d[name]
        if name in _ID_INT:
            iv = _int(v)
            if iv is None:
                raise FileFormatError(f"step result {name} = {v!r} is not an integer")
            v = iv
        elif name in _COUNT_INT:
            v = _int(v) or 0
        elif name in _TEXT:
            v = "" if v is None else str(v)
        elif name == "loop_iters":
            v = tuple(i for i in (_int(x) for x in (v if isinstance(v, (list, tuple)) else ())) if i is not None)
        elif name == "flags":
            v = tuple(str(x) for x in (v if isinstance(v, (list, tuple)) else ()))
        elif name == "window_s":
            v = (_f(v[0]), _f(v[1])) if isinstance(v, (list, tuple)) and len(v) == 2 else None
        elif name in _OPT_FLOAT:
            v = _opt_f(v)
        elif name in ("stats", "ramp"):
            v = v if isinstance(v, Mapping) else ({} if name == "stats" else None)
        elif isinstance(fld.default, float):
            v = _f(v)
        kw[name] = v
    for name in _ID_INT + ("uid", "label", "kind"):       # required by the dataclass
        kw.setdefault(name, 0 if name in _ID_INT else "")
    return StepResult(**kw)


# ============================================================================================ recording input
class Recording:
    """``data.csv`` rows as columns + event rows + ``meta.json``."""

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)
        meta_p = self.folder / "meta.json"
        data_p = self.folder / "data.csv"
        try:
            self.meta: dict[str, Any] = json.loads(meta_p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FileFormatError(f"{meta_p}: {exc}") from exc
        if self.meta.get("schema") != "bird.bend.recording":
            raise FileFormatError(f"{meta_p}: not a bird.bend.recording sidecar")
        try:
            lines = data_p.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise FileFormatError(f"{data_p}: {exc}") from exc
        hdr = next((ln for ln in lines if ln and not ln.startswith("#")), None)
        if hdr is None:
            raise FileFormatError(f"{data_p}: no column header")
        cols = hdr.split(",")
        ix = {c: i for i, c in enumerate(cols)}
        need = ("row_type", "t_us_u", "t_dev_s", "flags", "status", "raw", "raw_state", "setpoint_um", "seq_lost",
                "event")
        missing = [c for c in need if c not in ix]
        if missing:
            raise FileFormatError(f"{data_p}: columns missing: {missing}")
        t, tdev, fl, st, raw, rs, sp, lost = [], [], [], [], [], [], [], []
        self.events: list[tuple[int | None, float | None, str]] = []
        start = lines.index(hdr) + 1
        for ln in lines[start:]:
            p = ln.split(",")
            if not p or len(p) < len(cols):
                continue
            if p[ix["row_type"]] == "D":
                t.append(int(p[ix["t_us_u"]]))
                tdev.append(float(p[ix["t_dev_s"]]))
                fl.append(int(p[ix["flags"]]))
                st.append(int(p[ix["status"]]))
                raw.append(float(p[ix["raw"]]))
                rs.append(int(p[ix["raw_state"]]))
                sp.append(int(p[ix["setpoint_um"]]))
                lost.append(int(p[ix["seq_lost"]] or 0))
            elif p[ix["row_type"]] == "E":
                tu = p[ix["t_us_u"]]
                td = p[ix["t_dev_s"]]
                self.events.append((int(tu) if tu else None, float(td) if td else None, p[ix["event"]]))
        self.t = np.asarray(t, np.int64)
        self.t_dev = np.asarray(tdev, np.float64)
        self.flags = np.asarray(fl, np.int64)
        self.status = np.asarray(st, np.int64)
        self.raw = np.asarray(raw, np.float64)
        self.raw_state = np.asarray(rs, np.int64)
        self.sp = np.asarray(sp, np.int64)
        self.lost = int(np.sum(lost)) if lost else 0

    @property
    def runs(self) -> list[dict[str, Any]]:
        return list(self.meta.get("sequence_runs") or [])

    def snapshot_scale(self) -> tuple[float | None, float | None]:
        snap = self.meta.get("snapshot") or {}
        cal = snap.get("calibration")
        tare = snap.get("tare")
        k = None if not cal else _cal_k(cal)
        tr = None if not tare else tare.get("tare_raw")
        return k, None if tr is None else float(tr)


def _cal_k(cal: Mapping[str, Any]) -> float:
    try:
        return float(cal["fit"]["k_n_per_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FileFormatError(f"calibration record without fit.k_n_per_count ({exc})") from exc


def _finite(v: Any, what: str) -> float:
    if isinstance(v, bool) or v is None:
        raise ValueError(f"{what} is not a number")
    x = float(v)
    if not math.isfinite(x):
        raise ValueError(f"{what} is not finite")
    return x


def _check_cal_numbers(d: Mapping[str, Any], *, complete: bool = True) -> None:
    """Types and finite numbers of a load-calibration record (beyond ``validate_load``). ``complete=False`` (an
    in-process record): ``points`` / ``afe`` are checked only when present."""
    fit = d.get("fit")
    if not isinstance(fit, Mapping):
        raise ValueError("fit is not an object")
    if _finite(fit.get("k_n_per_count"), "fit.k_n_per_count") == 0:
        raise ValueError("fit.k_n_per_count must be non-zero")
    for key in ("offset_raw", "linearity_pct_span", "max_residual_n"):
        if fit.get(key) is not None:
            _finite(fit[key], f"fit.{key}")
    pts = d.get("points")
    if pts is None and not complete:
        pts = []
    if not isinstance(pts, list):
        raise ValueError("points is not a list")
    for i, p in enumerate(pts):
        if not isinstance(p, Mapping):
            raise ValueError(f"points[{i}] is not an object")
        _finite(p.get("raw_mean"), f"points[{i}].raw_mean")
        _finite(p.get("force_n"), f"points[{i}].force_n")
    afe = d.get("afe")
    if afe is not None or complete:
        if not isinstance(afe, Mapping):
            raise ValueError("afe is not an object")
        if _finite(afe.get("rate_sps"), "afe.rate_sps") <= 0:
            raise ValueError("afe.rate_sps must be > 0")
    for key in ("created_utc", "file"):
        if d.get(key) is not None and not isinstance(d[key], str):
            raise ValueError(f"{key} is not a string")


def _load_cal(cal: Any) -> Mapping[str, Any]:
    """Calibration to re-apply (SW-REP-003). A **file** (offline ``--cal``, Report tab) — a ``bird.bend.cal.load``
    store file, or a bare calibration record as in a recording's ``meta.json`` snapshot — is validated like an active
    calibration: schema kind / version when the file has a ``schema`` header, ``validate_load`` (K finite and
    non-zero, status PASS / WARN / UNVERIFIED_LINEARITY, >= 2 points, AFE fields) and finite numbers / types (review
    finding SWR-24).
    An in-process **record** (mapping) gets the number / type checks of the fields it has (K finite, non-zero).
    ``FileFormatError`` otherwise. A status other than PASS is reported as a warning in the report."""
    from bend_stand.core.calibration.store import LOAD_KIND, validate_load  # noqa: PLC0415
    from bend_stand.core.calibration.store import VERSION as CAL_VERSION  # noqa: PLC0415
    from bend_stand.core.schema import read_json  # noqa: PLC0415

    complete = not isinstance(cal, Mapping)
    if isinstance(cal, Mapping):
        d: Mapping[str, Any] = cal
        src = "calibration record"
    else:
        try:
            raw = json.loads(Path(cal).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise FileFormatError("not a JSON object")
            d = read_json(cal, LOAD_KIND, CAL_VERSION) if "schema" in raw else raw
        except (OSError, ValueError, RecursionError, FileFormatError) as exc:
            raise FileFormatError(f"calibration file {cal}: {exc}") from exc
        src = f"calibration file {cal}"
    _cal_k(d)                                             # "without fit.k_n_per_count" message first
    try:
        if complete:
            validate_load(d)
        _check_cal_numbers(d, complete=complete)
    except FileFormatError as exc:
        raise FileFormatError(f"{src}: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise FileFormatError(f"{src}: load calibration record invalid: {exc}") from exc
    return d


def force_column(rec: Recording, *, k_override: float | None = None, tare_override: float | None = None) -> np.ndarray:
    """F per row: K·(raw − tare) with the scale in effect at that row (run scale logs, else the recording snapshot),
    or the overrides; NaN for saturated / no-data rows or without K / tare."""
    n = rec.t.size
    k_arr = np.full(n, np.nan)
    t_arr = np.full(n, np.nan)
    k0, t0 = rec.snapshot_scale()
    k_arr[:] = np.nan if k0 is None else k0
    t_arr[:] = np.nan if t0 is None else t0
    for run in rec.runs:
        for e in run.get("scale_log") or []:
            m = rec.t >= int(e["t_us_u"])
            ok = e.get("k") is not None and e.get("tare_raw") is not None and e.get("ok", True)
            k_arr[m] = float(e["k"]) if ok else np.nan
            t_arr[m] = float(e["tare_raw"]) if ok else np.nan
    if k_override is not None:
        k_arr[:] = k_override
    if tare_override is not None:
        t_arr[:] = tare_override
    good = (rec.raw_state == RAW_OK) | (rec.raw_state == RAW_SETTLING)
    f = k_arr * (rec.raw - t_arr)
    f[~good] = np.nan
    return f


# ============================================================================================ build
def recompute_results(rec: Recording, f: np.ndarray, *, override: bool) -> list[tuple[dict[str, Any], StepResult]]:
    """Per run: every non-discarded window recomputed from the CSV; results without a window (NOT_REACHED, guards,
    BOUND_NOT_AHEAD) taken from the run log. → ``[(run, result), …]`` in execution order."""
    from bend_stand.core.sequencer.executor import result_from_window  # noqa: PLC0415

    out: list[tuple[dict[str, Any], StepResult]] = []
    for run in rec.runs:
        live = [result_from_dict(d) for d in run.get("results") or []]
        by_key = {(r.exec_idx, r.uid): r for r in live}
        used: set[tuple[int, str]] = set()
        items: list[StepResult] = []
        for w in run.get("windows") or []:
            if w.get("discarded"):
                continue
            t0, t1 = int(w["t0_us_u"]), int(w["t1_us_u"])
            sel = (rec.t >= t0) & (rec.t <= t1)
            ramp = bool(w.get("ramp"))
            tgt = w.get("target") if w.get("kind") == "load" else None
            tol = w.get("tol_n") if w.get("kind") == "load" else None
            ws = window_stats(rec.t[sel], rec.flags[sel], rec.status[sel], rec.sp[sel], rec.raw[sel], f[sel],
                              t0_us=t0, t1_us=t1, rate_sps=median_rate_sps(rec.t[sel]), target_n=tgt, tol_n=tol,
                              ramp=ramp)
            key = (int(w["exec_idx"]), str(w["uid"]))
            lv = by_key.get(key)
            used.add(key)
            r = result_from_window(ws, exec_idx=key[0], step_idx=int(w.get("step_idx") or 0), uid=key[1],
                                   label=str(w.get("label") or ""), kind=str(w.get("kind") or ""),
                                   loop_iters=tuple(w.get("loop_iters") or ()), target=w.get("target"), tol=tol,
                                   t0=t0, t1=t1, k_est=w.get("k_est_n_mm"),
                                   trim_iter=lv.trim_iterations if lv is not None else 0,
                                   f_end=lv.f_end_n if lv is not None and not override else NAN,
                                   x_end=lv.x_end_mm if lv is not None else NAN,
                                   t_reached=None if w.get("t_reached_us_u") is None else int(w["t_reached_us_u"]))
            items.append(r)
        items += [r for r in live if (r.exec_idx, r.uid) not in used]
        items.sort(key=lambda r: (r.exec_idx, 0 if r.n else 1))
        out += [(run, r) for r in items]
    return out


def _bend3p_rows(results: Sequence[tuple[dict[str, Any], StepResult]], g: Bend3pGeometry,
                 compliance: float) -> tuple[list[dict[str, Any]], float | None]:
    rows: list[dict[str, Any]] = []
    pts: list[tuple[float, float]] = []
    for run, r in results:
        if not math.isfinite(r.f_mean_n) or not math.isfinite(r.x_mean_mm):
            continue
        x_off = float(run.get("x_off_mm") or 0.0)
        delta = r.x_mean_mm - x_off - compliance * r.f_mean_n
        rows.append({"exec_idx": r.exec_idx, "label": r.label, "f_n": r.f_mean_n, "deflection_mm": delta,
                     "sigma_mpa": bend3p_stress(r.f_mean_n, g.span_mm, g.width_mm, g.thickness_mm),
                     "eps": bend3p_strain(delta, g.span_mm, g.thickness_mm)})
        pts.append((delta, r.f_mean_n))
    e_f = None
    if len(pts) >= 2 and float(np.ptp([p[0] for p in pts])) > 0:
        x = np.array([p[0] for p in pts])
        y = np.array([p[1] for p in pts])
        dx = x - x.mean()
        m = float(np.dot(dx, y - y.mean()) / np.dot(dx, dx))
        e_f = bend3p_modulus(g.span_mm, g.width_mm, g.thickness_mm, m)
    return rows, e_f


def build_report(folder: str | Path, *, cal: Any = None, tare: float | None = None,
                 bend3p: Bend3pGeometry | None | bool = None, bend3p_compliance: float = 0.0,
                 out_dir: str | Path | None = None, now_utc: str | None = None) -> ReportPaths:
    """Build ``report.json`` + ``report.html`` for a recording folder (SW-REP-001…004)."""
    rec = Recording(folder)
    cal_rec = None if cal is None else _load_cal(cal)
    k_ov = None if cal_rec is None else _cal_k(cal_rec)
    tare_ov = None if tare is None else float(tare)
    f = force_column(rec, k_override=k_ov, tare_override=tare_ov)
    results = recompute_results(rec, f, override=cal is not None or tare is not None)
    meta = rec.meta
    snap = meta.get("snapshot") or {}
    cal_used = cal_rec if cal_rec is not None else snap.get("calibration")
    k_used, tare_used = rec.snapshot_scale()
    if k_ov is not None:
        k_used = k_ov
    if tare_ov is not None:
        tare_used = tare_ov
    geom = None
    if isinstance(bend3p, Bend3pGeometry):
        geom = bend3p
    elif bend3p is None and (snap.get("session") or {}).get("bend3p"):
        b = snap["session"]["bend3p"]
        geom = Bend3pGeometry(float(b["span_mm"]), float(b["width_mm"]), float(b["thickness_mm"]))
    warnings = _warnings(rec, f, results, cal_used, cal is not None, tare is not None)
    b3_rows, e_f = _bend3p_rows(results, geom, bend3p_compliance) if geom is not None else ([], None)
    created = now_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = [{"name": (r.get("sequence") or {}).get("name", ""), "state": r.get("state"),
             "end_reason": r.get("end_reason"), "message": r.get("message"), "start_utc": r.get("start_utc"),
             "k_est_final_n_mm": r.get("k_est_final_n_mm"), "x_zero_mm": r.get("x_zero_mm"),
             "travel_ref": r.get("travel_ref"), "windows": len(r.get("windows") or [])} for r in rec.runs]
    fit = (cal_used or {}).get("fit") or {}
    summary = {"rows": int(rec.t.size), "frames_lost": rec.lost, "results": len(results),
               "incomplete": sum(1 for _r, x in results if "INCOMPLETE" in x.flags),
               "not_reached": sum(1 for _r, x in results if "NOT_REACHED" in x.flags),
               "f_max_n": _js(float(np.nanmax(f))) if np.isfinite(f).any() else None,
               "f_min_n": _js(float(np.nanmin(f))) if np.isfinite(f).any() else None}
    doc = {"schema": f"bird.bend.{KIND}", "schema_version": VERSION, "created_utc": created,
           "sw_version": __version__, "recording": str(rec.folder), "start_utc": meta.get("start_utc"),
           "stop_utc": meta.get("stop_utc"), "marks": meta.get("marks_final") or meta.get("marks_at_start") or {},
           "versions": {"sw": meta.get("sw_version"), "fw": meta.get("fw_version"), "board_uid": meta.get("board_uid"),
                        "icd": meta.get("icd_version"), "param_dict_hash": meta.get("param_dict_hash")},
           "calibration": None if cal_used is None else {
               "k_n_per_count": _js(k_used), "status": fit.get("status"), "low_span": bool(
                   fit.get("low_span") or cal_used.get("low_span")), "created_utc": cal_used.get("created_utc"),
               "file": cal_used.get("file"), "push_calibrated": bool(cal_used.get("push_calibrated", False)),
               "override": cal is not None},
           "tare_raw": _js(tare_used), "tare_override": tare is not None, "limits": snap.get("limits"),
           "thresholds": meta.get("thresholds"), "no_specimen_mode": bool(meta.get("no_specimen_mode")),
           "integrity": meta.get("integrity"), "runs": runs, "warnings": warnings,
           "results": [dict(result_to_dict(r), run=i) for i, (_run, r) in
                       enumerate(results)],
           "bend3p": None if geom is None else {"span_mm": geom.span_mm, "width_mm": geom.width_mm,
                                                "thickness_mm": geom.thickness_mm, "rows": _js(b3_rows),
                                                "e_f_mpa": _js(e_f), "compliance_mm_per_n": bend3p_compliance},
           "summary": summary}
    out = Path(out_dir) if out_dir is not None else rec.folder
    out.mkdir(parents=True, exist_ok=True)
    jp, hp = out / "report.json", out / "report.html"
    atomic_write_json(jp, _js(doc))
    atomic_write_text(hp, render_html(doc, rec, f, results))
    return ReportPaths(str(out), str(jp), str(hp), str(rec.folder / "data.csv"))


def _warnings(rec: Recording, f: np.ndarray, results: Sequence[tuple[dict[str, Any], StepResult]],
              cal: Mapping[str, Any] | None, cal_ov: bool, tare_ov: bool) -> list[str]:
    w: list[str] = []
    fit = (cal or {}).get("fit") or {}
    if cal is None:
        w.append("no load calibration: forces not available")
    else:
        if fit.get("low_span") or cal.get("low_span"):
            w.append("LOW_SPAN: calibrated with < 20 % FS — forces outside the calibrated range are extrapolated")
        if not cal.get("push_calibrated", False) and np.isfinite(f).any() and float(np.nanmin(f)) < -1.0:
            w.append("push forces tension-calibrated (the calibration covers pull only)")
        pts = cal.get("points") or []
        fmax = max((abs(float(p.get("force_n", 0.0))) for p in pts), default=0.0)
        if fmax > 0 and np.isfinite(f).any() and float(np.nanmax(np.abs(f))) > EXTRAPOLATION_FACTOR * fmax:
            w.append(f"forces beyond 3 × the largest calibration force ({fmax:g} N): extrapolated")
    if cal_ov:
        w.append("re-calculated with another calibration")
        if fit.get("status") != "PASS":                   # SWR-24: an applied calibration that is not PASS
            w.append(f"applied calibration status {fit.get('status')!s} (not PASS)")
    if tare_ov:
        w.append("re-calculated with another tare")
    if rec.meta.get("no_specimen_mode"):
        w.append("no-specimen mode was on (PC load limits off)")
    n_inc = sum(1 for _r, x in results if "INCOMPLETE" in x.flags)
    if n_inc:
        w.append(f"{n_inc} INCOMPLETE window(s) (N < 80 % of capture · rate)")
    n_nr = sum(1 for _r, x in results if "NOT_REACHED" in x.flags)
    if n_nr:
        w.append(f"{n_nr} step(s) NOT_REACHED")
    for flag in ("BREAK_DETECTED", "SLIP", "TIMEOUT", "DRIVER_ALARM", "BOUND_NOT_AHEAD"):
        if any(flag in x.flags for _r, x in results):
            w.append(f"sequence stopped by {flag}")
    if rec.lost:
        w.append(f"{rec.lost} frame(s) lost")
    integ = rec.meta.get("integrity") or {}
    if integ and not integ.get("complete", False):
        w.append("recording incomplete (rows lost or write failure)")
    for r in rec.runs:
        if r.get("state") != "FINISHED":
            w.append(f"sequence {((r.get('sequence') or {}).get('name') or '')!s} ended {r.get('state')} "
                     f"({r.get('end_reason')}): partial run")
        tr = r.get("truncated")
        if isinstance(tr, Mapping) and any(isinstance(v, int) and v > 0 for v in tr.values()):
            # Implements: SW-REP-001, SW-SEQ-002 (SWR-30): a bounded run log (endless loop, SWR-28) is named
            parts = ", ".join(f"{int(v)} {k}" for k, v in sorted(tr.items()) if isinstance(v, int) and v > 0)
            w.append(f"run log truncated: the oldest {parts} were dropped from the sidecar (endless loop); the "
                     "table covers the newest steps only — data.csv keeps every row")
    return w


# ============================================================================================ HTML + SVG
def _nice_ticks(lo: float, hi: float, n: int = 6) -> list[float]:
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo] if math.isfinite(lo) else []
    raw = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.ceil(lo / step) * step
    out = []
    v = start
    while v <= hi + 1e-9 * step:
        out.append(round(v, 12))
        v += step
    return out


def _decimate(x: np.ndarray, y: np.ndarray, n: int = MAX_POINTS) -> tuple[np.ndarray, np.ndarray]:
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if x.size > n:
        stride = -(-x.size // n)
        x, y = x[::stride], y[::stride]
    return x, y


def svg_chart(x: np.ndarray, y: np.ndarray, *, title: str, x_label: str, y_label: str,
              shade: Sequence[tuple[float, float]] = (), points: Sequence[tuple[float, float, str]] = (),
              width: int = 760, height: int = 360) -> str:
    """Inline SVG line chart (no external dependencies, KD-11)."""
    x, y = _decimate(np.asarray(x, np.float64), np.asarray(y, np.float64))
    ml, mr, mt, mb = 64, 16, 28, 44
    pw, ph = width - ml - mr, height - mt - mb
    if x.size == 0:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img">'
                f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle">{html.escape(title)}: no data</text></svg>')
    x0, x1 = float(x.min()), float(x.max())
    y0, y1 = float(y.min()), float(y.max())
    if x1 == x0:
        x0, x1 = x0 - 1, x1 + 1
    if y1 == y0:
        y0, y1 = y0 - 1, y1 + 1
    pad = 0.05 * (y1 - y0)
    y0, y1 = y0 - pad, y1 + pad

    def sx(v: float) -> float:
        return ml + (v - x0) / (x1 - x0) * pw

    def sy(v: float) -> float:
        return mt + (1 - (v - y0) / (y1 - y0)) * ph

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img" '
             f'viewBox="0 0 {width} {height}">',
             f'<text x="{ml}" y="18" class="t">{html.escape(title)}</text>']
    for a, b in shade:
        a, b = max(a, x0), min(b, x1)
        if b > a:
            parts.append(f'<rect x="{sx(a):.1f}" y="{mt}" width="{sx(b) - sx(a):.1f}" height="{ph}" class="w"/>')
    for v in _nice_ticks(x0, x1):
        parts.append(f'<line x1="{sx(v):.1f}" y1="{mt}" x2="{sx(v):.1f}" y2="{mt + ph}" class="g"/>'
                     f'<text x="{sx(v):.1f}" y="{mt + ph + 16}" text-anchor="middle">{v:g}</text>')
    for v in _nice_ticks(y0, y1):
        parts.append(f'<line x1="{ml}" y1="{sy(v):.1f}" x2="{ml + pw}" y2="{sy(v):.1f}" class="g"/>'
                     f'<text x="{ml - 6}" y="{sy(v) + 4:.1f}" text-anchor="end">{v:g}</text>')
    parts.append(f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" class="f"/>')
    pts = " ".join(f"{sx(a):.1f},{sy(b):.1f}" for a, b in zip(x.tolist(), y.tolist(), strict=True))
    parts.append(f'<polyline points="{pts}" class="l"/>')
    for a, b, lab in points:
        if math.isfinite(a) and math.isfinite(b):
            parts.append(f'<circle cx="{sx(a):.1f}" cy="{sy(b):.1f}" r="3" class="p"><title>{html.escape(lab)}'
                         f'</title></circle>')
    parts.append(f'<text x="{ml + pw / 2}" y="{height - 6}" text-anchor="middle">{html.escape(x_label)}</text>')
    parts.append(f'<text x="14" y="{mt + ph / 2}" transform="rotate(-90 14 {mt + ph / 2})" text-anchor="middle">'
                 f'{html.escape(y_label)}</text></svg>')
    return "".join(parts)


#: the report needs no script and no external resource: everything but inline style is refused (SWR-11)
_CSP = ("<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">")


def _idx1(v: Any) -> str:
    """1-based index cell: the integer + 1, anything else escaped as text (SWR-11)."""
    iv = _int(v)
    return str(iv + 1) if iv is not None else html.escape(str(v))


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return "–" if not math.isfinite(v) else f"{v:.{nd}g}" if abs(v) >= 1e5 else f"{v:.{nd}f}"
    return html.escape(str(v))


_CSS = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1b1f24;background:#fff}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:22px 0 8px;border-bottom:1px solid #ccd}
table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #ccd;padding:3px 7px;text-align:right}
th{background:#eef1f6}td.l,th.l{text-align:left}.warn{color:#9a4a00}.bad{color:#b00020;font-weight:600}
svg text{font-size:11px;fill:#333}svg .t{font-size:13px;font-weight:600}svg .g{stroke:#e3e6ea}
svg .f{fill:none;stroke:#99a}svg .l{fill:none;stroke:#1f5fbf;stroke-width:1.2}svg .w{fill:#cfeccf}
svg .p{fill:#d0602a}.kv td{text-align:left}"""


def render_html(doc: Mapping[str, Any], rec: Recording, f: np.ndarray,
                results: Sequence[tuple[dict[str, Any], StepResult]]) -> str:
    e = html.escape
    marks = doc.get("marks") or {}
    title = " ".join(str(marks.get(k) or "") for k in ("specimen", "number")).strip() or rec.folder.name
    x_off = 0.0
    runs = rec.runs
    if runs:
        x_off = float(runs[0].get("x_off_mm") or 0.0)
    else:
        x_off = float((rec.meta.get("snapshot") or {}).get("x_zero_mm") or 0.0)
    x = rec.sp / 1000.0 - x_off
    t = rec.t_dev - (rec.t_dev[0] if rec.t_dev.size else 0.0)
    t_of = (lambda tu: float(np.interp(tu, rec.t, t)) if rec.t.size else NAN)
    shade_t = [(t_of(r.window_s[0] * 1e6), t_of(r.window_s[1] * 1e6)) for _run, r in results if r.window_s]
    pts_fx = [(r.x_mean_mm - float(run.get("x_off_mm") or 0.0), r.f_mean_n, r.label) for run, r in results]
    out = [f"<!doctype html><html><head><meta charset='utf-8'>{_CSP}<title>Bend test report – {e(title)}</title>"
           f"<style>{_CSS}</style></head><body>", f"<h1>Bend test report – {e(title)}</h1>",
           f"<div>Recording {e(str(rec.folder.name))} · start {e(str(doc.get('start_utc') or ''))} · report "
           f"{e(str(doc.get('created_utc')))} · SW {e(str(doc.get('sw_version')))}</div>"]
    out.append("<h2>Marks</h2><table class='kv'>")
    for k in ("specimen", "number", "operator", "notes"):
        out.append(f"<tr><th class='l'>{e(k)}</th><td>{e(str(marks.get(k) or ''))}</td></tr>")
    for kv in marks.get("custom") or []:
        if isinstance(kv, Mapping):
            kv = (kv.get("key", ""), kv.get("value", ""))
        if isinstance(kv, (list, tuple)) and len(kv) == 2:
            out.append(f"<tr><th class='l'>{e(str(kv[0]))}</th><td>{e(str(kv[1]))}</td></tr>")
    out.append("</table>")
    cal = doc.get("calibration") or {}
    v = doc.get("versions") or {}
    out.append("<h2>Snapshot</h2><table class='kv'>")
    rows = [("calibration K [N/count]", cal.get("k_n_per_count")), ("calibration status", cal.get("status")),
            ("calibration date", cal.get("created_utc")), ("LOW_SPAN", cal.get("low_span")),
            ("calibration file", cal.get("file")), ("tare raw", doc.get("tare_raw")),
            ("no-specimen mode", doc.get("no_specimen_mode")), ("SW / FW", f"{v.get('sw')} / {v.get('fw')}"),
            ("board UID", v.get("board_uid")), ("ICD / dictionary", f"{v.get('icd')} / {v.get('param_dict_hash')}")]
    lim = doc.get("limits") or {}
    if lim:
        rows.append(("SW limits", ", ".join(f"{k}={lim[k]}" for k in sorted(lim))))
    for rr in doc.get("runs") or []:
        rows.append(("sequence", f"{rr.get('name')} — {rr.get('state')} ({rr.get('end_reason')}), final k_est "
                                 f"{_fmt(rr.get('k_est_final_n_mm'), 3)} N/mm, x_zero {_fmt(rr.get('x_zero_mm'), 4)} mm"))
    for k, val in rows:
        out.append(f"<tr><th class='l'>{e(k)}</th><td>{_fmt(val, 6) if isinstance(val, float) else e(str(val))}</td>"
                   "</tr>")
    out.append("</table>")
    out.append("<h2>Warnings</h2>")
    ws = doc.get("warnings") or []
    out.append("<ul>" + "".join(f"<li class='warn'>{e(w)}</li>" for w in ws) + "</ul>" if ws else "<p>none</p>")
    out.append("<h2>Figures</h2>")
    out.append(svg_chart(x, f, title="F–x", x_label="travel [mm] (test)", y_label="F [N]", points=pts_fx))
    out.append("<br>")
    out.append(svg_chart(t, f, title="F–t (capture windows shaded)", x_label="t [s]", y_label="F [N]",
                         shade=shade_t))
    out.append("<h2>Step results</h2><table><tr><th>exec</th><th>step</th><th>loop</th><th class='l'>label</th>"
               "<th>target</th><th>N</th><th>F mean [N]</th><th>F std</th><th>F SE</th><th>F drift</th>"
               "<th>x mean [mm]</th><th>x std</th><th>raw mean</th><th>k_est</th><th class='l'>flags</th></tr>")
    for _run, r in results:
        bad = any(fl in r.flags for fl in ("NOT_REACHED", "BREAK_DETECTED", "SLIP", "TIMEOUT", "NOT_ON_TARGET",
                                            "INCOMPLETE", "DRIVER_ALARM", "BOUND_NOT_AHEAD"))
        out.append(f"<tr><td>{_idx1(r.exec_idx)}</td><td>{_idx1(r.step_idx)}</td>"
                   f"<td>{e('.'.join(map(str, r.loop_iters)))}</td><td class='l'>{e(str(r.label))}</td>"
                   f"<td>{_fmt(r.target, 3)} {e(str(r.unit))}</td><td>{e(str(r.n))}</td>"
                   f"<td>{_fmt(r.f_mean_n)}</td><td>{_fmt(r.f_std_n)}</td><td>{_fmt(r.f_se_n)}</td>"
                   f"<td>{_fmt(r.f_drift_n)}</td><td>{_fmt(r.x_mean_mm)}</td><td>{_fmt(r.x_std_mm)}</td>"
                   f"<td>{_fmt(r.raw_mean, 1)}</td><td>{_fmt(r.k_est_n_mm, 3)}</td>"
                   f"<td class='l {'bad' if bad else ''}'>{e(', '.join(map(str, r.flags)) or '–')}"
                   f"{(' — ' + e(str(r.text))) if r.text else ''}</td></tr>")
    out.append("</table>")
    b3 = doc.get("bend3p")
    if b3:
        out.append(f"<h2>3-point bend</h2><p>L = {_fmt(b3.get('span_mm'))} mm, b = {_fmt(b3.get('width_mm'))} mm, "
                   f"h = {_fmt(b3.get('thickness_mm'))} mm; E<sub>f</sub> = {_fmt(b3.get('e_f_mpa'), 1)} MPa</p>"
                   "<table><tr>"
                   "<th>exec</th><th class='l'>label</th><th>F [N]</th><th>δ [mm]</th><th>σ [MPa]</th><th>ε</th></tr>")
        for row in b3.get("rows") or []:
            out.append(f"<tr><td>{_idx1(row.get('exec_idx'))}</td><td class='l'>{e(str(row.get('label')))}</td>"
                       f"<td>{_fmt(row['f_n'])}</td><td>{_fmt(row['deflection_mm'])}</td>"
                       f"<td>{_fmt(row['sigma_mpa'])}</td><td>{_fmt(row['eps'], 6)}</td></tr>")
        out.append("</table>")
    evs = [ev for ev in rec.events if ev[2].split(":", 1)[0] in (
        "SEQ_START", "SEQ_END", "SEQ_PAUSE", "SEQ_RESUME", "SEQ_GUARD", "SEQ_MARK", "SEQ_REACHED", "TARE", "STOP",
        "HALT", "PAUSE", "SW_TRIP", "STOPPED", "LIMIT_SET", "FAULT_SET", "ESTOP_SET", "SEQ_RESUME_REFUSED")]
    if evs:
        out.append("<h2>Events</h2><table><tr><th>t_dev [s]</th><th class='l'>event</th></tr>")
        for _tu, td, text in evs[:500]:
            out.append(f"<tr><td>{_fmt(td, 3)}</td><td class='l'>{e(text)}</td></tr>")
        out.append("</table>")
    out.append("</body></html>\n")
    return "\n".join(out)


# ============================================================================================ report tab support
def list_recordings(root: str | Path) -> list[RecordingInfo]:
    """Recordings under ``root`` (folders with a ``meta.json``), newest first (reads ``meta.json`` only)."""
    out: list[tuple[str, RecordingInfo]] = []
    rp = Path(root)
    if not rp.is_dir():
        return []
    for d in rp.iterdir():
        mp = d / "meta.json"
        if not mp.is_file():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict) or m.get("schema") != "bird.bend.recording":
            continue
        integ = m.get("integrity") or {}
        runs = m.get("sequence_runs") or []
        if integ.get("failures"):
            status = "FAILED"
        elif integ.get("complete") and all(r.get("state") == "FINISHED" for r in runs):
            status = "COMPLETE"
        else:
            status = "PARTIAL"
        marks = m.get("marks_final") or m.get("marks_at_start") or {}
        ms = ", ".join(str(marks.get(k)) for k in ("specimen", "number", "operator") if marks.get(k))
        seq_name = None
        if runs:
            seq_name = (runs[0].get("sequence") or {}).get("name") or "(unnamed)"
        else:
            st = m.get("sequence_start")
            if st:
                seq_name = (st.get("sequence") or {}).get("name") or "(unnamed)"
        dur = None
        try:
            if m.get("start_utc") and m.get("stop_utc"):
                a = datetime.fromisoformat(str(m["start_utc"]).replace("Z", "+00:00"))
                b = datetime.fromisoformat(str(m["stop_utc"]).replace("Z", "+00:00"))
                dur = (b - a).total_seconds()
        except ValueError:
            dur = None
        out.append((str(m.get("start_utc") or ""), RecordingInfo(str(d), m.get("start_utc"), ms, seq_name, status,
                                                                  dur, (d / "report.json").is_file())))
    out.sort(key=lambda p: (p[0], p[1].folder), reverse=True)
    return [i for _k, i in out]


def load_result(folder: str | Path) -> ReportResult:
    p = Path(folder) / "report.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FileFormatError(f"{p}: {exc}") from exc
    if not isinstance(d, dict) or d.get("schema") != f"bird.bend.{KIND}":
        raise FileFormatError(f"{p}: not a bird.bend.{KIND} file")
    if int(d.get("schema_version", 0)) > VERSION:
        raise FileFormatError(f"{p}: made by a newer SW version")
    res = tuple(result_from_dict(r) for r in d.get("results") or [])
    return ReportResult(str(folder), str(d.get("created_utc") or ""), d.get("marks") or {},
                        tuple(d.get("warnings") or ()), res, tuple(d.get("runs") or ()), d.get("calibration"),
                        d.get("tare_raw"), d.get("bend3p"), d.get("summary") or {})


# ============================================================================================ CLI (SW-REP-003)
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bend_stand.core.report",
                                 description="Rebuild a bend-stand test report from a recording (SW-REP-003).")
    ap.add_argument("recording", help="recording folder (data.csv + meta.json)")
    ap.add_argument("--cal", help="load calibration file (bird.bend.cal.load) to apply instead of the recorded one")
    ap.add_argument("--tare-raw", type=float, help="tare raw counts to apply instead of the recorded tare")
    ap.add_argument("--bend3p", nargs=3, type=float, metavar=("L", "b", "h"),
                    help="3-point bend geometry span, width, thickness [mm]")
    ap.add_argument("--out", help="output folder (default: the recording folder)")
    a = ap.parse_args(argv)
    try:
        p = build_report(a.recording, cal=a.cal, tare=a.tare_raw,
                         bend3p=Bend3pGeometry(*a.bend3p) if a.bend3p else None, out_dir=a.out)
    except FileFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(p.json)
    print(p.html)
    return 0


__all__ = ["build_report", "list_recordings", "load_result", "result_to_dict", "result_from_dict", "Recording",
           "force_column", "svg_chart", "main"]

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
