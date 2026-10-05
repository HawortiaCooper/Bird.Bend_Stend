"""Load-calibration engine (SW_design §9.4; R4 §6; SRS SW-CAL-005…009).

PO sequence (PO-SW-8.PC1–PC3): point 0 "remove load" (zero), point 1 "put a known weight" (mass entered), point 2
"put a bigger known weight" (mass entered); more points are allowed (``n_points``). Per point: the stream is on,
``presettle_s`` (2 s) then ``capture_s`` (10 s) with the axis standing still (CaptureHub), then ``EVALUATE`` with
``calc.stats.window_acceptance`` (any saturated sample → invalid; MAD 5σ outliers > 2 % → invalid; N ≥ 95 % of
round(capture × measured rate); |drift| ≤ max(2·std, 20); std ≤ max(3·std_zero, 50)) and the mass rules (increasing,
each ≥ 1.5 × the previous weight; the first weight < 2 % FS is a warning only, D-22). A rejected point is re-taken
(``repeat()`` / ``continue_()``), never silently used. ``FIT``: ``calc.loadcal.load_calibration`` → PASS / WARN
(accept only with ``continue_(confirmed=True)``) / FAIL ("points are not linear", accept refused) /
UNVERIFIED_LINEARITY (2 points, ``finish_early()``); LOW_SPAN when the largest force < 20 % FS. ``retake(i)``
discards point *i* and captures it again (mass prefilled). Accept (idle only) stores ``load_<serial>_<UTC>.json`` +
``active_load.json`` (R4 §6.4 schema, ``push_calibrated = false``, AFE block, board UID / FW) and activates it — the
FW thresholds are rewritten by the backend's threshold recheck. The engine needs no motion; any stop / pause /
latch / link loss aborts it (the point in capture is discarded) and the active calibration stays unchanged.

Implements: SW-CAL-001 (wizard engine), SW-CAL-005, SW-CAL-006, SW-CAL-007, SW-CAL-008, SW-CAL-009
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from bend_stand import __version__
from bend_stand.calc.loadcal import LoadCalResult, load_calibration, mass_rules
from bend_stand.calc.stats import window_acceptance
from bend_stand.calc.units import FS_KG, G0
from bend_stand.core.capture import CaptureResult
from bend_stand.core.clock import wall_utc_iso
from bend_stand.core.engine import EngineBase, refuse
from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import ConfirmRequest, GateId, GateResult, InputSpec

if TYPE_CHECKING:  # pragma: no cover
    from bend_stand.core.backend import Backend

log = logging.getLogger("bend_stand.core.calibration.load")
MASS_SPEC = ("mass_kg", "Mass", "kg", 0.0, 2.0 * FS_KG)


class LoadCalEngine(EngineBase):
    KIND = "load_cal"
    TOPIC = "cal.load.state"
    PHASES = ("CONFIG", "AWAIT_OPERATOR", "PRESETTLE", "CAPTURE", "EVALUATE", "FIT", "ACCEPT", "DONE")
    TERMINAL = ("IDLE", "DONE", "ABORTED")

    def __init__(self, backend: Backend) -> None:
        super().__init__(backend)
        self._points: list[dict[str, Any] | None] = []
        self._masses: list[float | None] = []
        self._idx = 0
        self._presettle = 2.0
        self._capture = 10.0
        self._g = G0
        self._restore_stream = False
        self._fit: LoadCalResult | None = None
        self._warnings: tuple[str, ...] = ()

    # ---- start --------------------------------------------------------------------------------------------
    def start(self, *, n_points: int = 3, presettle_s: float | None = None, capture_s: float | None = None,
              g: float | None = None, confirmed: bool = False, **_kw: Any) -> GateResult:
        be = self._be
        if self.active:
            return self._busy()
        if not 2 <= int(n_points) <= 10:
            return refuse("RANGE", "2…10 points (zero + weights)")
        g_res = be._gates()[GateId.CAL_LOAD_START]  # noqa: SLF001
        if not g_res.ok:
            return g_res
        need = self._needs_confirmation(g_res, confirmed)
        if need is not None:
            return need
        s = be.session.get()
        self._presettle = float(s.cal_presettle_s if presettle_s is None else presettle_s)
        self._capture = float(s.cal_capture_s if capture_s is None else capture_s)
        self._g = float(s.g_local if g is None else g)
        self._points = [None] * int(n_points)
        self._masses = [0.0] + [None] * (int(n_points) - 1)
        self._idx = 0
        self._fit = None
        self._warnings = ()
        self._restore_stream = not be.device.stream_on
        if self._restore_stream:
            be._job(be.device.stream_job, True)  # noqa: SLF001
        self._reset(phase="CONFIG", title="Load calibration", step_count=int(n_points), can_cancel=True)
        self._await(0)
        return g_res

    # ---- point flow ------------------------------------------------------------------------------------------
    def _await(self, i: int, errors: tuple[str, ...] = (), stats: Mapping[str, float] | None = None) -> None:
        self._idx = i
        n = len(self._points)
        done = sum(p is not None for p in self._points)
        if i == 0:
            instr = "Remove all load from the cell (no specimen, no weights, hook empty), then capture the zero point."
            inputs: tuple[InputSpec, ...] = ()
            label = "Capture zero point ▶"
        else:
            which = "a known weight" if i == 1 else "a bigger known weight"
            instr = f"Hang {which} on the cell, let it settle, enter its mass, then capture point {i}."
            inputs = (InputSpec(*MASS_SPEC, default=self._masses[i]),)
            label = f"Capture point {i} ▶"
        self._set(phase="AWAIT_OPERATOR", step_index=i, instruction=instr, inputs=inputs, continue_label=label,
                  continue_moves=False, can_continue=True, can_repeat=bool(errors), can_cancel=True, progress=None,
                  errors=errors, stats=stats, needs_confirmation=None, warnings=self._warnings,
                  result=self._fit, title=f"Load calibration — point {i} of {n - 1}" + (f" ({done} done)" if done
                                                                                          else ""))

    def continue_(self, inputs: Mapping[str, float] | None = None, *, confirmed: bool = False) -> None:
        ph = self._state.phase
        if ph == "AWAIT_OPERATOR":
            self._capture_point(inputs or {})
        elif ph == "FIT":
            self._accept(confirmed)

    def repeat(self) -> None:
        if self._state.phase == "AWAIT_OPERATOR":
            self._capture_point({})

    def _capture_point(self, inputs: Mapping[str, float]) -> None:
        be = self._be
        i = self._idx
        if i > 0:
            m = inputs.get("mass_kg", self._masses[i])
            try:
                m = float(m) if m is not None else None
            except (TypeError, ValueError):
                m = None
            if m is None:
                self._set(errors=("enter the mass of the weight",))
                return
            errs, warns = mass_rules([x for x in self._masses_in_order(i, m) if x is not None])
            if errs:
                self._set(errors=tuple(errs))
                return
            self._warnings = tuple(warns)
            self._masses[i] = m
        if be.motion.fw_moving() or be.motion.busy:
            self._set(errors=("the axis is moving — wait for standstill",))
            return
        try:
            be.capture.start("load_cal", self._capture, presettle_s=self._presettle, require_still=True,
                             on_done=self._point_done, on_progress=self._progress,
                             rate_hint=lambda: be.pipeline.latest_copy().rate_sps)
        except RuntimeError as exc:
            self._set(errors=(str(exc),))
            return
        self._set(phase="PRESETTLE" if self._presettle > 0 else "CAPTURE", progress=0.0, can_continue=False,
                  can_repeat=False, errors=(), inputs=(), warnings=self._warnings)

    def _masses_in_order(self, i: int, m: float) -> list[float | None]:
        out: list[float | None] = []
        for j in range(1, len(self._masses)):
            if j == i:
                out.append(m)
            elif self._points[j] is not None:
                out.append(self._masses[j])
        return out

    def _progress(self, p: float) -> None:
        total = self._presettle + self._capture
        if self._state.phase not in ("PRESETTLE", "CAPTURE"):
            return
        phase = "PRESETTLE" if p * total < self._presettle else "CAPTURE"
        self._set(phase=phase, progress=p)

    def _point_done(self, res: CaptureResult) -> None:
        if self._state.phase not in ("PRESETTLE", "CAPTURE"):
            return
        if not res.ok:
            self._abort(res.aborted or "capture aborted")
            return
        i = self._idx
        self._set(phase="EVALUATE", progress=1.0)
        zero = self._points[0]
        std_ref = None if i == 0 or zero is None else float(zero["raw_std"])
        w = window_acceptance(res.raw, res.t_s, window_s=res.window_s, n_nominal=res.n_nominal,
                              n_saturated=res.n_saturated, n_lost=res.n_lost, std_ref=std_ref, require_count=True)
        texts = list(w.texts)
        if res.n_no_data:
            texts.append(f"{res.n_no_data} frame(s) without AFE data")
        stats: dict[str, float] = {"n": float(res.raw.size), "n_nominal": float(res.n_nominal)}
        if w.stats is not None:
            stats.update(mean=w.stats.mean, std=w.stats.std, drift=w.drift, rejected=float(w.stats.n_rejected))
        if texts or w.stats is None:
            self._be.record_event("CAL_POINT_REJECTED", f"point {i}: " + "; ".join(texts or ["no samples"]))
            self._await(i, tuple(texts) or ("no samples",), stats)
            return
        m = float(self._masses[i] or 0.0)
        self._points[i] = {
            "mass_kg": m, "force_n": m * self._g, "raw_mean": w.stats.mean, "raw_std": w.stats.std, "raw_se": w.se,
            "n_used": w.stats.n_used, "n_rejected": w.stats.n_rejected, "n_nominal": res.n_nominal,
            "n_saturated": res.n_saturated, "n_lost": res.n_lost, "drift_counts": w.drift,
            "t_start_utc": wall_utc_iso(self._be.clock)}
        self._be.record_event("CAL_POINT", f"point {i}: m={m:g} kg raw={w.stats.mean:.2f} std={w.stats.std:.2f} "
                                           f"n={w.stats.n_used}")
        nxt = next((j for j, p in enumerate(self._points) if p is None), None)
        if nxt is None:
            self._do_fit()
        else:
            self._await(nxt, (), stats)

    # ---- fit / accept ----------------------------------------------------------------------------------------
    def finish_early(self) -> None:
        """Zero point + ≥ 1 weight accepted → FIT with the points so far (2 points = UNVERIFIED_LINEARITY)."""
        if self._state.phase != "AWAIT_OPERATOR":
            return
        got = [p for p in self._points if p is not None]
        if self._points and self._points[0] is not None and len(got) >= 2:
            self._points = got
            self._masses = [p["mass_kg"] for p in got]
            self._do_fit()
        else:
            self._set(errors=("finish early needs the zero point and at least one weight",))

    def retake(self, i: int) -> None:
        if self._state.phase not in ("FIT", "AWAIT_OPERATOR") or not 0 <= int(i) < len(self._points):
            return
        i = int(i)
        self._points[i] = None
        self._fit = None
        self._await(i)

    def _do_fit(self) -> None:
        pts = [p for p in self._points if p is not None]
        try:
            fit = load_calibration([p["raw_mean"] for p in pts], [p["mass_kg"] for p in pts], g=self._g,
                                   point_se_counts=[p["raw_se"] for p in pts])
        except ValueError as exc:
            self._fit = None
            self._set(phase="FIT", step_index=len(self._points), errors=(f"points are not linear: {exc}",),
                      can_continue=False, can_repeat=False, inputs=(), progress=None, result=None)
            return
        self._fit = fit
        ok = fit.status != "FAIL"
        conf = None
        if fit.status == "WARN":
            res = ", ".join(f"{r:+.3f} N" for r in fit.residuals)
            conf = ConfirmRequest("FIT_WARN", f"non-linearity {fit.nl_pct_span:.3f} % of span (0.1…0.5 %): residuals "
                                              f"{res}. Accept anyway?")
        errors = () if ok else tuple(fit.texts) or ("points are not linear",)
        self._set(phase="FIT", step_index=len(self._points), result=fit, needs_confirmation=conf, can_continue=ok,
                  continue_label="Accept calibration ▶", continue_moves=False, can_repeat=False, inputs=(),
                  progress=None, errors=errors, warnings=self._warnings + tuple(t for t in fit.texts if ok),
                  instruction="Check the fit; accept to make it the active calibration, or re-take a point.")

    def _accept(self, confirmed: bool) -> None:
        be = self._be
        fit = self._fit
        if fit is None or fit.status == "FAIL":
            self._set(errors=("points are not linear — re-take a point or cancel",))
            return
        if fit.status == "WARN" and not confirmed:
            self._set(errors=("non-linearity in the WARN band: confirm to accept",))
            return
        if be.motion.fw_moving() or be.motion.busy:
            self._set(errors=("accept only while the axis stands still",))
            return
        self._set(phase="ACCEPT", can_continue=False)
        info = be.device.info
        params = be.device.params.values()
        pts = [p for p in self._points if p is not None]
        rec = {"created_utc": wall_utc_iso(be.clock), "operator": be.marks.get().operator, "sw_version": __version__,
               "sensor": {"model": "Keli DEF", "capacity_kg": FS_KG, "serial": ""},
               "afe": be.load_input.board_afe(params),
               "board": {"fw_version": ".".join(map(str, info.fw_version)) if info else "",
                         "uid": info.uid if info else ""},
               "direction": "pull", "push_calibrated": False, "g_used": self._g, "points": pts, "fit": fit.as_fit(),
               "low_span": fit.low_span, "warnings": list(self._warnings), "notes": ""}
        try:
            path = be.calibration_store.save_load(rec)
        except (OSError, FileFormatError) as exc:
            self._set(phase="FIT", can_continue=True, errors=(f"calibration file not written: {exc}",))
            return
        rec["file"] = path.name
        be.activate_load_calibration(rec)
        be.record_event("CAL_LOAD_ACTIVATED", f"{path.name} K={fit.K:.9g} N/count status={fit.status}"
                                              + (" LOW_SPAN" if fit.low_span else ""))
        self._set(phase="DONE", result=fit, can_cancel=False, errors=(), needs_confirmation=None,
                  instruction=f"Calibration active ({path.name}).")
        self._restore()

    # ---- end ------------------------------------------------------------------------------------------------
    def _restore(self) -> None:
        if self._restore_stream:
            self._restore_stream = False
            be = self._be
            if be.device.connected:
                be._job(be.device.stream_job, False)  # noqa: SLF001

    def _abort(self, reason: str) -> None:
        self._set(phase="ABORTED", abort_reason=reason, can_continue=False, can_repeat=False, can_cancel=False,
                  progress=None)
        self._restore()

    def cancel(self) -> None:
        if not self.active:
            return
        self._be.capture.abort("load_cal", "cancelled")
        if self.active:
            self._abort("cancelled")

    def terminate(self, reason: str) -> None:
        if not self.active:
            return
        self._be.capture.abort("load_cal", reason)
        if self.active:
            self._abort(reason)


__all__ = ["LoadCalEngine"]
