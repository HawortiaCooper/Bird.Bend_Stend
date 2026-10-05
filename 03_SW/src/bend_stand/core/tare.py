"""Tare engine (SW_design §9.5; R4 §7; SRS SW-TARE-001…003).

``start(window_s=None)`` (= ``Backend.tare``): gate ``tare`` (moving or < 1 s after a move, a stop / fault latched,
another capture / wizard running, AFE synthetic, stream stale …) → phase ``CHECK``; the stream is started if it is
off (and restored afterwards) → ``CAPTURE`` over ``window_s`` (session default 10 s, 2–60 s) with the axis required
to stand still → ``EVALUATE`` with the R4 §7 rules (any saturated sample, outliers > 2 %, std > max(3·std_zero_cal,
50), |drift| > max(2·std, 20), lost frames > 1 %) → ``DONE`` (``TareResult``: robust mean, statistics, board UID) or
``REFUSED`` (verbatim reasons in ``errors``). Warning (accepted): ``|K·(tare_raw − raw_zero_cal)| > 10 % FS``.
Success writes a ``TARE`` event row; the FW thresholds and ``safety.zero_raw`` are rewritten + verified by the
backend's automatic threshold recheck (SAF-SW-002). ``undo()`` restores the state before the last tare (one level,
``TARE_UNDO`` row). The tare is **session-only** (D-29 j): never saved, valid only for the board UID it was taken on.

Implements: SW-TARE-001, SW-TARE-002, SW-TARE-003
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from bend_stand.calc.tare import tare_acceptance, tare_offset_warning
from bend_stand.core.capture import CaptureResult
from bend_stand.core.clock import wall_utc_iso
from bend_stand.core.engine import EngineBase, refuse
from bend_stand.core.model import GATE_OK, GateCode, GateId, GateResult, TareResult

if TYPE_CHECKING:  # pragma: no cover
    from bend_stand.core.backend import Backend

log = logging.getLogger("bend_stand.core.tare")
WINDOW_MIN_S, WINDOW_MAX_S = 2.0, 60.0
NOMINAL_NOISE_COUNTS = 45.0


class TareEngine(EngineBase):
    KIND = "tare"
    TOPIC = "tare.state"
    PHASES = ("CHECK", "CAPTURE", "EVALUATE", "DONE")
    TERMINAL = ("IDLE", "DONE", "ABORTED", "REFUSED")

    def __init__(self, backend: Backend) -> None:
        super().__init__(backend)
        self._restore_stream = False
        self._window_s = 10.0
        self.tare_host_ns: int | None = None

    # ---- start ------------------------------------------------------------------------------------------
    def start(self, **config: Any) -> GateResult:
        return self.start_tare(config.get("window_s"))

    def start_tare(self, window_s: float | None = None) -> GateResult:
        be = self._be
        if self.active:
            return self._busy()
        w = float(window_s) if window_s is not None else float(be.session.get().tare_window_s)
        if not WINDOW_MIN_S <= w <= WINDOW_MAX_S:
            g = refuse("RANGE", f"tare window {w:g} s outside {WINDOW_MIN_S:g}…{WINDOW_MAX_S:g} s")
            self._reset(phase="REFUSED", errors=(g.text(),))
            return g
        g = be._gates()[GateId.TARE]  # noqa: SLF001
        if not g.ok:
            self._reset(phase="REFUSED", title="Tare", errors=tuple(i.text for i in g.refused))
            return g
        self._window_s = w
        self._restore_stream = not be.device.stream_on
        self._reset(phase="CHECK", title="Tare", instruction="keep the stand still — capturing the zero load",
                    step_count=1, can_cancel=True, progress=0.0)
        if self._restore_stream:
            fut = be._job(be.device.stream_job, True)  # noqa: SLF001
            fut.add_done_callback(self._stream_started)
        else:
            self._begin()
        return g

    def _stream_started(self, f: Any) -> None:
        if f.exception() is not None:
            self._set(phase="ABORTED", abort_reason=f"stream could not be started: {f.exception()}", can_cancel=False)
            return
        if self._state.phase == "CHECK":
            self._begin()

    def _begin(self) -> None:
        be = self._be
        try:
            be.capture.start("tare", self._window_s, presettle_s=0.0, require_still=True, on_done=self._done,
                             on_progress=lambda p: self._set(progress=p),
                             rate_hint=lambda: be.pipeline.latest_copy().rate_sps)
        except RuntimeError as exc:
            self._set(phase="ABORTED", abort_reason=str(exc), can_cancel=False)
            self._restore()
            return
        self._set(phase="CAPTURE", progress=0.0)

    # ---- evaluation (Pipeline thread) ----------------------------------------------------------------
    def _done(self, res: CaptureResult) -> None:
        be = self._be
        if not res.ok:
            self._set(phase="ABORTED", abort_reason=res.aborted, can_cancel=False, progress=None)
            self._restore()
            return
        self._set(phase="EVALUATE", progress=1.0)
        li = be.load_input
        cal = li.cal
        # std limit max(3·std_zero_cal, 50): without a calibration the nominal HX711 noise (R2 §4.4) is the reference
        std0 = cal.std_zero if cal is not None and cal.std_zero is not None else NOMINAL_NOISE_COUNTS
        w = tare_acceptance(res.raw, res.t_s, window_s=res.window_s, n_nominal=res.n_nominal,
                            n_saturated=res.n_saturated, n_lost=res.n_lost, std_zero_cal=std0)
        stats: Mapping[str, float] = {}
        if w.stats is not None:
            stats = {"n": float(w.stats.n_used), "mean": w.stats.mean, "std": w.stats.std, "drift": w.drift,
                     "rejected": float(w.stats.n_rejected), "lost": float(res.n_lost)}
        if res.n_no_data:
            w_texts = (*w.texts, f"{res.n_no_data} frame(s) without AFE data")
            reasons = (*w.reasons, "NO_DATA")
        else:
            w_texts, reasons = w.texts, w.reasons
        if reasons or w.stats is None:
            self._set(phase="REFUSED", errors=tuple(w_texts) or ("no samples",), stats=stats, can_cancel=False)
            be.record_event("TARE_REFUSED", "; ".join(w_texts))
            self._restore()
            return
        info = be.device.info
        uid = info.uid if info is not None else None
        warnings: list[str] = []
        if cal is not None and tare_offset_warning(cal.k, w.stats.mean, cal.raw_zero):
            warnings.append("large offset — specimen loaded? (|K·(tare − zero of the calibration)| > 10 % FS)")
        tr = TareResult(li.next_tare_id(uid), w.stats.mean, w.stats.std, w.se, w.drift, w.stats.n_used,
                        w.stats.n_rejected, res.n_lost, res.window_s, res.t_end_us, wall_utc_iso(be.clock), uid,
                        tuple(warnings))
        li.set_tare(tr)
        self.tare_host_ns = be.clock.monotonic_ns()
        be.on_scale_changed()
        be.record_event("TARE", f"tare_raw={tr.tare_raw:.3f} std={tr.std:.2f} n={tr.n_used} id={tr.tare_id}")
        self._set(phase="DONE", result=tr, warnings=tuple(warnings), stats=stats, can_cancel=False)
        self._restore()

    def _restore(self) -> None:
        if self._restore_stream:
            self._restore_stream = False
            be = self._be
            if be.device.connected:
                be._job(be.device.stream_job, False)  # noqa: SLF001

    # ---- control -------------------------------------------------------------------------------------------
    def cancel(self) -> None:
        if self._state.phase == "CHECK":
            self._set(phase="ABORTED", abort_reason="cancelled", can_cancel=False)
            self._restore()
        elif self._state.phase == "CAPTURE":
            self._be.capture.abort("tare", "cancelled")

    def terminate(self, reason: str) -> None:
        """STOP / HALT / E-stop / PAUSE / link loss (§3.5): a running tare is aborted."""
        if self._state.phase == "CHECK":
            self._set(phase="ABORTED", abort_reason=reason, can_cancel=False)
            self._restore()
        elif self._state.phase == "CAPTURE":
            self._be.capture.abort("tare", reason)

    def continue_(self, inputs: Mapping[str, float] | None = None, *, confirmed: bool = False) -> None:
        return None

    def repeat(self) -> None:
        if not self.active:
            self.start_tare(self._window_s)

    def undo(self) -> GateResult:
        be = self._be
        if self.active:
            return refuse(GateCode.OPERATION_RUNNING, "tare running")
        if be.motion.fw_moving() or be.motion.busy:
            return refuse(GateCode.MOTION_ACTIVE, "axis moving")
        if not be.load_input.can_undo():
            return refuse(GateCode.NOTHING_TO_CLEAR, "nothing to undo")
        be.load_input.undo_tare()
        self.tare_host_ns = be.clock.monotonic_ns() if be.load_input.tare is not None else None
        be.on_scale_changed()
        t = be.load_input.tare
        be.record_event("TARE_UNDO", "no tare" if t is None else f"tare_raw={t.tare_raw:.3f} id={t.tare_id}")
        self._set(phase="IDLE", result=t, warnings=(), errors=())
        return GATE_OK


__all__ = ["TareEngine"]
