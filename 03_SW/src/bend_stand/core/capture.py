"""CaptureHub — device-time capture windows over pipeline samples (SW_design §9.2).

``start(kind, window_s, presettle_s, require_still, on_done, on_progress)`` opens a window
``[t_first + presettle, + window)`` in **device time** (unwrapped ``t_us``, KD-09) over the DATA rows of the pipeline
(a pipeline sink, Pipeline thread). Collected per sample: raw (OK / settling samples), state counts (saturated,
no-data), frame losses (``seq_lost``), the commanded position and the force. A capture ends with a
``CaptureResult`` when the window is complete, or is aborted (``CaptureResult.aborted`` = reason) on: MOVING = 1 when
``require_still`` (tare, calibration points), a new time epoch (board reset), ``abort(kind | None, reason)`` from the
backend (stop / latch / link loss). Progress callbacks at ≤ 5 Hz of device time. One capture per kind at a time.

``n_nominal`` = round(window × measured conversion rate) (SW-CAL-005); the rate is the median sample period of the
window (fallback: the pipeline's measured rate).

Implements: SW-CAL-005 (capture window), SW-TARE-002 (tare window), SW-ACQ-003 (take-sample window)
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from bend_stand.core.pipeline import RAW_STATE_NO_DATA, RAW_STATE_OK, RAW_STATE_SATURATED, RAW_STATE_SETTLING, DataRow

log = logging.getLogger("bend_stand.core.capture")
MOVING = 0x02
PROGRESS_US = 200_000


@dataclass(frozen=True)
class CaptureResult:
    kind: str
    raw: np.ndarray                 # float64, OK + settling samples (saturated / no-data excluded)
    t_s: np.ndarray                 # device time of ``raw`` (s, unwrapped)
    x_mm: np.ndarray                # commanded position of every frame of the window
    f_n: np.ndarray                 # force of every frame of the window (NaN without calibration + tare)
    n_frames: int
    n_saturated: int
    n_no_data: int
    n_settling: int
    n_lost: int
    window_s: float
    rate_sps: float | None
    n_nominal: int
    t_start_us: int
    t_end_us: int
    moving_frames: int = 0
    aborted: str | None = None

    @property
    def ok(self) -> bool:
        return self.aborted is None


@dataclass
class _Capture:
    kind: str
    window_us: int
    presettle_us: int
    require_still: bool
    on_done: Callable[[CaptureResult], None]
    on_progress: Callable[[float], None] | None
    rate_hint: Callable[[], float | None] | None
    t0: int | None = None
    epoch: int | None = None
    raw: list[float] = field(default_factory=list)
    t: list[float] = field(default_factory=list)
    x: list[float] = field(default_factory=list)
    f: list[float] = field(default_factory=list)
    n_frames: int = 0
    n_sat: int = 0
    n_nodata: int = 0
    n_settle: int = 0
    n_lost: int = 0
    n_moving: int = 0
    next_progress: int = 0
    done: bool = False

    def result(self, aborted: str | None = None) -> CaptureResult:
        t = np.asarray(self.t, dtype=np.float64)
        rate = None
        if t.size >= 3:
            d = np.diff(t)
            d = d[d > 0]
            if d.size:
                rate = 1.0 / float(np.median(d))
        if rate is None and self.rate_hint is not None:
            rate = self.rate_hint()
        win_s = self.window_us / 1e6
        n_nom = int(round(win_s * rate)) if rate else 0
        start = (self.t0 or 0) + self.presettle_us
        return CaptureResult(self.kind, np.asarray(self.raw, dtype=np.float64), t, np.asarray(self.x),
                             np.asarray(self.f), self.n_frames, self.n_sat, self.n_nodata, self.n_settle,
                             self.n_lost, win_s, rate, n_nom, start, start + self.window_us, self.n_moving, aborted)


class CaptureHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._caps: dict[str, _Capture] = {}

    def running(self, kind: str | None = None) -> bool:
        with self._lock:
            return bool(self._caps) if kind is None else kind in self._caps

    def kinds(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._caps)

    def start(self, kind: str, window_s: float, *, presettle_s: float = 0.0, require_still: bool = True,
              on_done: Callable[[CaptureResult], None], on_progress: Callable[[float], None] | None = None,
              rate_hint: Callable[[], float | None] | None = None) -> None:
        if not window_s > 0:
            raise ValueError("capture window must be > 0 s")
        with self._lock:
            if kind in self._caps:
                raise RuntimeError(f"a {kind} capture is already running")
            self._caps[kind] = _Capture(kind, int(round(window_s * 1e6)), int(round(max(0.0, presettle_s) * 1e6)),
                                        bool(require_still), on_done, on_progress, rate_hint)

    def abort(self, kind: str | None = None, reason: str = "aborted") -> None:
        with self._lock:
            caps = [self._caps.pop(k) for k in list(self._caps) if kind is None or k == kind]
        for c in caps:
            self._finish(c, reason)

    @staticmethod
    def _finish(c: _Capture, aborted: str | None) -> None:
        if c.done:
            return
        c.done = True
        try:
            c.on_done(c.result(aborted))
        except Exception:  # noqa: BLE001 — a failing consumer must not break the pipeline
            log.exception("capture consumer failed")

    def on_row(self, row: DataRow) -> None:
        """Pipeline sink."""
        with self._lock:
            caps = list(self._caps.values())
        for c in caps:
            done, why = self._feed(c, row)
            if done:
                with self._lock:
                    if self._caps.get(c.kind) is c:
                        del self._caps[c.kind]
                self._finish(c, why)

    def _feed(self, c: _Capture, row: DataRow) -> tuple[bool, str | None]:
        t = row.t_us_u
        if c.t0 is None:
            c.t0, c.epoch, c.next_progress = t, row.epoch, t
        if row.epoch != c.epoch:
            return True, "board reset during the capture"
        start = c.t0 + c.presettle_us
        if c.require_still and row.flags & MOVING:
            c.n_moving += 1
            return True, "the axis moved during the capture"
        if t < start:
            self._progress(c, t)
            return False, None
        if t >= start + c.window_us:
            return True, None
        c.n_frames += 1
        c.n_lost += row.seq_lost if c.n_frames > 1 else 0
        c.x.append(row.setpoint_um / 1000.0)
        c.f.append(row.f_n)
        if row.raw_state == RAW_STATE_SATURATED:
            c.n_sat += 1
        elif row.raw_state == RAW_STATE_NO_DATA:
            c.n_nodata += 1
        else:
            if row.raw_state == RAW_STATE_SETTLING:
                c.n_settle += 1
            elif row.raw_state != RAW_STATE_OK:  # pragma: no cover - defensive
                return False, None
            c.raw.append(float(row.raw))
            c.t.append(t / 1e6)
        self._progress(c, t)
        return False, None

    @staticmethod
    def _progress(c: _Capture, t: int) -> None:
        if c.on_progress is None or t < c.next_progress:
            return
        c.next_progress = t + PROGRESS_US
        total = c.presettle_us + c.window_us
        frac = min(1.0, max(0.0, (t - (c.t0 or t)) / total)) if total else 1.0
        try:
            c.on_progress(frac)
        except Exception:  # noqa: BLE001
            log.exception("capture progress consumer failed")


__all__ = ["CaptureHub", "CaptureResult"]
