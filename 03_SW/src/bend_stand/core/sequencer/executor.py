"""Sequence execution engine (SW_design §10.3–§10.5, §22c; SRS SW-SEQ-003…007, SW-STOP-004, SW-ACQ-004, D-30…D-33).

One run = **one generator job** on the executor's own runner (``Worker("sequencer")`` — a thread on the real clock,
stepped after the general Worker on the lock-step clock). Every wait is a ``Poll`` whose predicate also watches the
run's interrupt counter, so pause / stop / terminate wake the job at once; the job never sends a command after the
run has ended (``terminate_all`` swaps the state synchronously in the caller's thread, §3.5).

Timeline per step (device time, KD-09): command → reached (``t_reached`` = MOVE_DONE ``t_us`` or the last trim
sample) → settle → capture (SET_VALID 1 at the first sample ≥ t_reached + settle, SET_VALID 0 at the first sample ≥
the planned end − one frame period, so every VALID = 1 frame lies inside the planned window) → hold until t_reached +
max(step time, settle + capture). Steps are single absolute FW commands (MOVE_ABS, MOVE_UNTIL_LOAD, HOME); TRAVEL
targets in the test coordinate add ``x_zero`` frozen at the start (D-29 k).

LOAD steps (SW-SEQ-006, D-32, D-33 d): approach with MOVE_UNTIL_LOAD toward the nearer of the FW soft limit and an
enabled SW travel limit in the step direction, raw stop at F_target − sgn·band; LOAD_THRESHOLD → k_est from the
approach (OLS, clamped) → trim (MOVE_DONE + 100 ms, mean of the last 4 samples, Δx = Kp·e/k_est ≤ 0.2 mm at 0.2 mm/s,
≤ 10 moves); BOUND or trim exhausted → **NOT_REACHED**, sequence STOPPED (not a SW-limit trip).

Guards (pipeline sink, per sample while a motion command of the sequence runs, SW-SEQ-007): SLIP (load moving
opposite to the motion by > 5 % of the target / running extreme), BREAK_DETECTED (|F| drops > 20 % below its running
max while loading, also during the approach), TIMEOUT (1.2 × planned travel time + 10 s; waits without motion
3 × planned + 10 s), ALM 0 → 1 (controlled STOP, DRIVER_ALARM, no retry, D-33 c), recording failure (controlled
STOP, SW-ACQ-004). Pause (D-30/D-31): the run keeps step / loop counters; the open window is discarded; Resume =
RESUME 0x3C, wait PAUSED = 0, then the interrupted step is re-run (absolute target / approach + trim / capture anew).

Implements: SW-SEQ-002 (loop counters), SW-SEQ-003, SW-SEQ-004, SW-SEQ-005 (start gate, with ``core.gates``),
SW-SEQ-006, SW-SEQ-007, SW-STOP-004, SW-ACQ-004 (sequence part), SW-SCH-002 (live marker + trace, 20 Hz status),
SW-REP-002 (live step results), FW-MOT-006 (use)
"""
from __future__ import annotations

import collections
import logging
import math
import threading
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import numpy as np

from bend_stand.calc import trim as T
from bend_stand.calc.steady import median_rate_sps, window_stats
from bend_stand.calc.units import FS_N
from bend_stand.core import protocol_gen as pg
from bend_stand.core.errors import GateRefused, LinkError
from bend_stand.core.jobs import Job, Poll, Sleep, Worker
from bend_stand.core.model import INT_DF, INT_DS, GateResult, SeqStatus, SeqWindow, StepResult, StopResult
from bend_stand.core.sequencer.model import Sequence, StepKind, machine_mm
from bend_stand.core.sequencer.plan import Plan, PlanContext, expand, iter_exec

if TYPE_CHECKING:  # pragma: no cover
    from bend_stand.core.backend import Backend

log = logging.getLogger("bend_stand.core.sequencer.executor")
MS = 1_000_000
S = 1_000_000_000
M32 = 0xFFFFFFFF
OWNER = "SEQUENCE"
STATUS_PERIOD_NS = 50 * MS                 # 20 Hz while running (SW-SCH-002: ≥ 10 Hz)
RESUME_CONFIRM_NS = 500 * MS
IDLE_WAIT_NS = 5 * S
TAIL_NS = 1 * S                            # recording tail after the end (§10.3)
TRACE_MAX = 200_000
HOME_TIMEOUT_S = 180.0
SLIP_FRAC = 0.05
BREAK_FRAC = 0.20
GUARD_ARM_N = 0.01 * FS_N                  # guards armed once |F| ≥ max(5 % target, 1 % FS) (§10.5)
SLIP_FLOOR_N = 0.005 * FS_N
ACTIVE = ("PREPARING", "RUNNING", "PAUSED", "WAITING_OPERATOR", "STOPPING")
ENDED = ("FINISHED", "STOPPED", "ABORTED", "ERROR")
DS, DF = INT_DS, INT_DF


class _Ended(Exception):
    pass


class _Paused(Exception):
    pass


@dataclass
class _Guard:
    kind: str                                # "load" | "travel"
    exp: int                                 # expected sign of dF for this motion (pull_dir · direction)
    target: float | None
    f_start: float
    loading: bool
    ext: float | None = None                 # running extreme in the expected direction
    max_abs: float = 0.0


@dataclass
class _Win:
    exec_idx: int
    uid: str
    loop_iters: tuple[int, ...]
    t0: int                                  # planned window (t_us_u)
    t1: int
    ramp: bool = False
    t_reached: int | None = None
    t_on: int | None = None
    t_off: int | None = None
    t: list[int] = field(default_factory=list)
    flags: list[int] = field(default_factory=list)
    status: list[int] = field(default_factory=list)
    sp: list[int] = field(default_factory=list)
    raw: list[float] = field(default_factory=list)
    f: list[float] = field(default_factory=list)


@dataclass
class _Run:
    seq: Sequence
    plan: Plan | None
    x_zero: float
    x_off: float                             # sequence coordinate = machine − x_off
    home_confirmed: bool
    t0_ns: int
    state: str = "PREPARING"
    phase: str = "PREPARE"
    ctl: str = "RUN"                         # RUN | PAUSE
    end: tuple[str, str, str] | None = None  # (state, reason, message)
    int_seq: int = 0
    paused_source: str | None = None
    resume_req: str | None = None
    continue_req: bool = False
    exec_idx: int | None = None
    step_idx: int | None = None
    loop_iters: tuple[int, ...] = ()
    step_t0_ns: int = 0
    k_est: float = 50.0
    trim_iter: int = 0
    message: str = ""
    windows_done: int = 0
    started_recording: bool = False
    rec_folder: str | None = None
    report_folder: str | None = None
    last_t_u: int | None = None
    last_t32: int | None = None
    recent: collections.deque[tuple[int, float, float, int]] = field(
        default_factory=lambda: collections.deque(maxlen=64))
    marker: tuple[float, float, float] = (float("nan"), float("nan"), float("nan"))
    trace_x: list[float] = field(default_factory=list)
    trace_f: list[float] = field(default_factory=list)
    trace_stride: int = 1
    trace_n: int = 0
    window: _Win | None = None
    approach: list[tuple[float, float]] | None = None
    guard: _Guard | None = None
    alm_prev: bool = False
    valid_on: bool = False
    fail_flags: list[str] = field(default_factory=list)
    results: list[StepResult] = field(default_factory=list)
    windows: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    scale_log: list[dict[str, Any]] = field(default_factory=list)
    last_scale: tuple[Any, Any] | None = None
    t_start_utc: str = ""
    collecting: bool = True


class SequenceExecutor:
    """Runs one sequence at a time (owner ``SEQUENCE``); thread-safe control methods (GUI / pipeline / supervisor)."""

    def __init__(self, backend: Backend) -> None:
        self._be = backend
        self._lock = threading.RLock()
        self.runner = Worker(backend.clock, "sequencer")
        self._run: _Run | None = None
        self._last: _Run | None = None
        self._next_pub_ns = 0
        self._last_pub: SeqStatus | None = None
        self._job: Any = None
        self.report_pending: _Run | None = None   # report to build when the operator's recording stops

    # ============================================================================== properties
    @property
    def run(self) -> _Run | None:
        return self._run

    @property
    def active(self) -> bool:
        r = self._run
        return r is not None and r.state in ACTIVE

    @property
    def paused(self) -> bool:
        r = self._run
        return r is not None and r.state == "PAUSED"

    @property
    def capture_open(self) -> bool:
        r = self._run
        return r is not None and r.window is not None and r.window.t_on is not None and r.state in ACTIVE

    # ============================================================================== control (any thread)
    def start(self, seq: Sequence, *, plan_ctx: PlanContext, home_confirmed: bool) -> None:
        be = self._be
        x_zero = float(be.motion.x_zero_mm)
        try:
            plan = expand(seq, plan_ctx)
        except ValueError:
            plan = None
        run = _Run(seq, plan, x_zero, x_zero if seq.travel_ref == "test" else 0.0, home_confirmed,
                   be.clock.monotonic_ns(), k_est=float(seq.k_est_n_mm))
        with self._lock:
            self._run = run
        be.owner = OWNER
        self._publish(force=True)
        self._job = self.runner.submit(self._main, run)

    def on_pause(self, source: str | None = None) -> None:
        """``pause_all`` (§3.5): GUI Pause (before / after the frame) and EVENT PAUSED / STOPPED (pause cause)."""
        run = self._run
        if run is None or run.end is not None or run.state not in ("PREPARING", "RUNNING", "WAITING_OPERATOR",
                                                                     "PAUSED"):
            return
        with self._lock:
            run.paused_source = run.paused_source or source
            if run.state == "PAUSED":
                return
            run.ctl = "PAUSE"
            run.state = "PAUSED"
            run.guard = None
            run.int_seq += 1
            run.message = "paused — Resume re-issues the interrupted step"
        self._event(run, "SEQ_PAUSE", f"source={source or '?'} exec={run.exec_idx}")
        self._publish(force=True)

    def request_resume(self, source: str) -> bool:
        run = self._run
        if run is None or run.state != "PAUSED" or run.end is not None:
            return False
        with self._lock:
            run.resume_req = source
            run.int_seq += 1
        return True

    def continue_(self) -> None:
        run = self._run
        if run is not None and run.state == "WAITING_OPERATOR":
            with self._lock:
                run.continue_req = True
                run.int_seq += 1

    def _set_end(self, run: _Run, state: str, reason: str, message: str = "") -> bool:
        with self._lock:
            if run.end is not None or run.state in ENDED:
                return False
            run.end = (state, reason, message or reason)
            if state == "ABORTED":
                run.state = "ABORTED"
            elif state != "FINISHED":
                run.state = "STOPPING"
            run.guard = None
            run.int_seq += 1
        self._publish(force=True)
        return True

    def stop(self, source: str = "sequence") -> StopResult:
        """Controlled stop (STOP mode 1), the sequence ends STOPPED (SW-SEQ-007)."""
        run = self._run
        be = self._be
        if run is None or run.state not in ACTIVE:
            return StopResult("STOP", source, False, None, "no sequence running")
        self._set_end(run, "STOPPED", "OPERATOR_STOP", "stopped by the operator")
        try:
            res = be.device.stop(pg.StopMode.CONTROLLED, source)
        except Exception as exc:  # noqa: BLE001 — never raises
            res = StopResult("STOP", source, False, None, str(exc))
        be.motion.on_stop_issued("STOP")
        be.record_event("STOP", f"{source} (controlled)")
        return res

    def abort(self, reason: str = "operator") -> StopResult:
        """HALT (latched; Clear stop needed), the sequence ends ABORTED (SW-SEQ-007)."""
        run = self._run
        if run is not None and run.state in ACTIVE:
            self._set_end(run, "ABORTED", "OPERATOR_ABORT", f"aborted ({reason})")
        return self._be.halt("sequence")

    def terminate(self, reason: str) -> None:
        """FW-reported HALT / ESTOP / stop, SW trip, link loss, STOP button … → ABORTED at once (no further command)."""
        run = self._run
        if run is not None and run.state in ACTIVE:
            self._set_end(run, "ABORTED", reason, f"terminated: {reason}")

    def end_cleared(self) -> None:
        """Clear stop confirmed while PAUSED (SWD-P1-02 c): the sequence ends STOPPED (CLEARED) before HALT_CLEAR."""
        run = self._run
        if run is not None and run.state in ACTIVE:
            self._set_end(run, "STOPPED", "CLEARED", "paused sequence ended by Clear stop")

    def on_pause_cleared(self) -> None:
        """PAUSE_CLEARED not caused by this run's RESUME (another client / HALT_CLEAR) → STOPPED."""
        run = self._run
        if run is not None and run.state == "PAUSED" and run.resume_req is None and run.phase != "RESUMING":
            self._set_end(run, "STOPPED", "PAUSE_CLEARED", "PAUSE cleared outside the sequence")

    def on_recording_failed(self) -> None:
        """SW-ACQ-004: a recording failure stops a running sequence (controlled stop)."""
        run = self._run
        if run is not None and run.state in ACTIVE and self._set_end(run, "STOPPED", "RECORDING_FAILED",
                                                                      "recording failed — controlled stop"):
            self._controlled_stop("recording failed")

    def on_alm(self) -> None:
        """D-33 c: ALM became active during the sequence → controlled STOP, ended DRIVER_ALARM (no retry)."""
        run = self._run
        if run is not None and run.state in ACTIVE and self._set_end(run, "STOPPED", "DRIVER_ALARM",
                                                                      "driver alarm (ALM) — controlled stop"):
            run.fail_flags.append("DRIVER_ALARM")
            self._controlled_stop("driver alarm")

    def _controlled_stop(self, why: str) -> None:
        be = self._be
        try:
            be.device.stop(pg.StopMode.CONTROLLED, f"sequence: {why}")
        finally:
            be.motion.on_stop_issued("STOP")
        be.record_event("STOP", f"sequence: {why} (controlled)")

    def _guard_trip(self, run: _Run, code: str, text: str) -> None:
        """SLIP / BREAK_DETECTED / TIMEOUT: priority STOP, step failed, sequence STOPPED (§10.5)."""
        if not self._set_end(run, "STOPPED", code, text):
            return
        run.fail_flags.append(code)
        be = self._be
        try:
            be.device.stop(pg.StopMode.IMMEDIATE, f"sequence {code}")
        finally:
            be.motion.on_stop_issued("STOP")
        be.record_event("SEQ_GUARD", f"{code} {text}")
        be.events.log(f"sequence {code}: {text}", logging.WARNING)

    def shutdown(self) -> None:
        run = self._run
        if run is not None and run.state in ACTIVE:
            self.terminate("shutdown")
        self.runner.stop()

    # ============================================================================== pipeline sink
    def on_row(self, row: Any) -> None:
        """Pipeline thread, every DATA sample: device time, marker / trace, windows, approach samples, guards, ALM."""
        run = self._run
        if run is None or not run.collecting:
            return
        run.last_t_u, run.last_t32 = row.t_us_u, row.t_us
        x = row.setpoint_um / 1000.0
        f = row.f_n
        run.recent.append((row.t_us_u, x, f, row.raw_state))
        xs = x - run.x_off
        run.marker = (xs, f, row.t_us_u / 1e6)
        run.trace_n += 1
        if run.trace_n % run.trace_stride == 0:
            run.trace_x.append(xs)
            run.trace_f.append(f)
            if len(run.trace_x) >= TRACE_MAX:
                run.trace_x, run.trace_f = run.trace_x[::2], run.trace_f[::2]
                run.trace_stride *= 2
        sc = self._be.pipeline.scale
        key = (sc.k, sc.tare_raw, sc.force_available)
        if key != run.last_scale:
            run.last_scale = key
            run.scale_log.append({"t_us_u": int(row.t_us_u), "k": sc.k, "tare_raw": sc.tare_raw,
                                  "ok": bool(sc.force_available)})
        w = run.window
        if w is not None and w.t0 <= row.t_us_u <= w.t1:
            w.t.append(int(row.t_us_u))
            w.flags.append(int(row.flags))
            w.status.append(int(row.status))
            w.sp.append(int(row.setpoint_um))
            w.raw.append(float(row.raw))
            w.f.append(float(f))
        ap = run.approach
        if ap is not None and math.isfinite(f):
            ap.append((x, f))
        g = run.guard
        if g is not None and math.isfinite(f):
            self._check_guard(run, g, f)
        vmask = self._be.device.valid_status_mask()
        alm = bool(row.status & vmask & DS.ALM)
        if alm and not run.alm_prev and run.state in ACTIVE:
            self.on_alm()
        run.alm_prev = alm

    def _check_guard(self, run: _Run, g: _Guard, f: float) -> None:
        """BREAK_DETECTED first (the more specific cause of a drop), then SLIP (§10.5)."""
        g.ext = f if g.ext is None else (max(g.ext, f) if g.exp > 0 else min(g.ext, f))
        if g.loading:
            g.max_abs = max(g.max_abs, abs(f))
            barm = max(0.05 * abs(g.target), GUARD_ARM_N) if g.target is not None else GUARD_ARM_N
            if g.max_abs >= barm and abs(f) < (1.0 - BREAK_FRAC) * g.max_abs:
                self._guard_trip(run, "BREAK_DETECTED", f"load dropped to {f:.1f} N from its running maximum "
                                                        f"{g.max_abs:.1f} N (> 20 %)")
                return
        ref = abs(g.target) if g.kind == "load" and g.target is not None else abs(g.ext)
        arm = max(SLIP_FRAC * ref, GUARD_ARM_N)
        if max(abs(g.f_start), abs(g.ext)) >= arm:
            thr = max(SLIP_FRAC * ref, SLIP_FLOOR_N)
            if g.exp * (g.ext - f) > thr:
                self._guard_trip(run, "SLIP", f"load moved opposite to the motion by {abs(g.ext - f):.1f} N "
                                              f"(> {thr:.1f} N) from {g.ext:.1f} N")

    # ============================================================================== status
    def tick(self, now: int) -> None:
        """Supervisor tick: ``seq.status`` at 20 Hz while running (SW-SCH-002) + on change."""
        run = self._run
        if run is not None and run.state in ACTIVE and now >= self._next_pub_ns:
            self._publish(force=True)

    def _publish(self, force: bool = False) -> None:
        st = self.status()
        self._next_pub_ns = self._be.clock.monotonic_ns() + STATUS_PERIOD_NS
        if force or st != self._last_pub:
            self._last_pub = st
            self._be.events.publish("seq.status", st)

    def status(self) -> SeqStatus:
        run = self._run
        if run is None:
            return SeqStatus()
        now = self._be.clock.monotonic_ns()
        plan = run.plan
        seq = run.seq
        elapsed = (now - run.t0_ns) / 1e9
        plan_t = remaining = behind = None
        total = plan.total_s if plan is not None else None
        ps = None
        if plan is not None and run.exec_idx is not None and run.exec_idx < len(plan.steps):
            ps = plan.steps[run.exec_idx]
            in_step = (now - run.step_t0_ns) / 1e9 if run.state in ACTIVE else ps.duration_s
            plan_t = ps.t_cmd_s + min(max(0.0, in_step), ps.duration_s)
            if total is not None:
                remaining = max(0.0, total - plan_t) if run.state in ACTIVE else 0.0
            behind = elapsed - plan_t
        step = seq.steps[run.step_idx] if run.step_idx is not None and run.step_idx < len(seq.steps) else None
        kind = None if step is None else StepKind(step.kind).value
        last = run.results[-1] if run.results else None
        end = run.end
        return SeqStatus(
            state=run.state, step_idx=run.step_idx, loop_iter=run.loop_iters[-1] if run.loop_iters else None,
            phase=run.phase, plan_total_s=total, remaining_s=remaining, paused_source=run.paused_source,
            end_reason=end[1] if end is not None else None, exec_idx=run.exec_idx,
            step_uid=None if step is None else step.uid, step_kind=kind, label="" if step is None else step.label,
            loop_iters=run.loop_iters, plan_len=None if plan is None or plan.infinite else len(plan.steps),
            target=None if step is None else step.target, unit={"travel": "mm", "load": "N"}.get(kind or "", ""),
            trim_iter=run.trim_iter, plan_t_s=plan_t, behind_s=behind, elapsed_s=elapsed,
            windows_done=run.windows_done, windows_total=plan.windows_total if plan is not None else None,
            k_est_n_mm=run.k_est, message=end[2] if end is not None else run.message, sequence_name=seq.name,
            travel_ref=seq.travel_ref, x_zero_mm=run.x_zero, marker_x_mm=run.marker[0], marker_f_n=run.marker[1],
            marker_t_s=run.marker[2], recording_folder=run.rec_folder, report_folder=run.report_folder,
            last_result=last)

    def results(self) -> tuple[StepResult, ...]:
        run = self._run
        return () if run is None else tuple(run.results)

    def trace(self, max_points: int = 20000) -> tuple[np.ndarray, np.ndarray]:
        run = self._run
        if run is None:
            z = np.zeros(0, np.float32)
            return z, z.copy()
        x = np.asarray(run.trace_x[:], np.float32)
        f = np.asarray(run.trace_f[:len(x)], np.float32)
        x = x[:len(f)]
        if max_points > 0 and len(x) > max_points:
            stride = -(-len(x) // max_points)
            x, f = x[::stride], f[::stride]
        return x, f

    # ============================================================================== job helpers
    def _check(self, run: _Run) -> None:
        if run.end is not None:
            raise _Ended()
        if run.ctl == "PAUSE":
            raise _Paused()

    def _poll(self, run: _Run, pred: Any, timeout_ns: int) -> Job:
        self._check(run)
        seen = run.int_seq
        yield Poll(lambda: run.int_seq != seen or bool(pred()), max(0, int(timeout_ns)))
        self._check(run)
        return bool(pred())

    def _now(self) -> int:
        return self._be.clock.monotonic_ns()

    def _wait_dev(self, run: _Run, t_u: int, deadline_ns: int, what: str) -> Job:
        """Wait until the newest sample's device time reaches ``t_u`` (TIMEOUT guard at the host deadline)."""
        ok = yield from self._poll(run, lambda: run.last_t_u is not None and run.last_t_u >= t_u,
                                   deadline_ns - self._now())
        if not ok:
            self._guard_trip(run, "TIMEOUT", f"no data while waiting for {what}")
            raise _Ended()

    def _set_valid(self, run: _Run, flag: bool) -> Job:
        """SET_VALID (device time of application); a link failure ends the run ABORTED / LINK_LOST (SWC-M4-03) —
        the FW clears VALID on its link watchdog (D-47) and the backend clears a left-over VALID at reconnect."""
        if flag:
            run.valid_on = True                  # also when the response is lost: the FW may have applied it
        try:
            t = yield from self._be.device.set_valid_job(flag)
        except LinkError as exc:
            if run.end is None:
                self._set_end(run, "ABORTED", "LINK_LOST", f"link lost: SET_VALID {int(flag)} not answered ({exc})")
            raise _Ended() from None
        if not flag:
            run.valid_on = False
        return t

    def _clear_valid_after_link_loss(self, run: _Run) -> Job:
        """SWC-M4-03: a run that ended on a link failure while VALID may be 1 clears it as soon as the board answers
        again (SET_VALID 0 is the only frame sent after the end; ≤ 60 s, then the reconnect hook takes over)."""
        be = self._be
        deadline = self._now() + 60 * S
        while run.valid_on and self._now() < deadline:
            if be.device.connected:
                try:
                    yield from be.device.set_valid_job(False)
                    run.valid_on = False
                    self._event(run, "VALID_OFF", "sequence: cleared after the link returned")
                    return
                except LinkError:
                    pass
            yield Sleep(250 * MS)

    def _unwrap(self, run: _Run, t32: int) -> int:
        if run.last_t_u is None or run.last_t32 is None:
            return int(t32)
        d = ((int(t32) - run.last_t32 + 0x80000000) & M32) - 0x80000000
        return run.last_t_u + d

    def _event(self, run: _Run, name: str, text: str) -> None:
        run.events.append({"t_us_u": run.last_t_u, "name": name, "text": text})
        self._be.record_event(name, text)

    def _f_now(self, run: _Run, n: int = T.MEAN_SAMPLES) -> tuple[float, int | None]:
        """Mean of the last ``n`` AFE-valid force samples and the device time of the last one."""
        vals = [(t, f) for (t, _x, f, rs) in run.recent if rs == 0 and math.isfinite(f)][-n:]
        if not vals:
            return float("nan"), None
        return float(np.mean([f for _t, f in vals])), vals[-1][0]

    def _x_now(self) -> float | None:
        return self._be.motion.position_mm()

    # ============================================================================== the run
    def _main(self, run: _Run) -> Job:
        be = self._be
        try:
            yield from self._prepare(run)
            for exec_idx, (i, iters) in enumerate(iter_exec(run.seq)):
                self._check(run)
                with self._lock:
                    run.exec_idx, run.step_idx, run.loop_iters, run.trim_iter = exec_idx, i, iters, 0
                    run.step_t0_ns = self._now()
                step = run.seq.steps[i]
                self._event(run, "SEQ_STEP", f"exec={exec_idx} step={i + 1} uid={step.uid} kind="
                                             f"{StepKind(step.kind).value} target={step.target} "
                                             f"iters={'.'.join(map(str, iters)) or '-'}")
                self._publish(force=True)
                while True:
                    try:
                        yield from self._run_step(run, exec_idx, i, iters)
                        break
                    except _Paused:
                        self._discard_window(run, "PAUSE")
                        yield from self._wait_resume(run)
                        with self._lock:
                            run.step_t0_ns = self._now()
            self._set_end(run, "FINISHED", "COMPLETED", "sequence completed")
        except _Ended:
            pass
        except _Paused:                                    # pragma: no cover - defensive (pause outside a step)
            self._set_end(run, "STOPPED", "PAUSED", "paused outside a step")
        except LinkError as exc:                       # link lost (§10.3): ABORTED, nothing more is sent
            self._set_end(run, "ABORTED", "LINK_LOST", f"link lost: {exc}")
        except Exception as exc:  # noqa: BLE001 — a defect must end the run safely
            log.exception("sequence run failed")
            if self._set_end(run, "ERROR", "ERROR", f"internal error: {exc}"):
                self._controlled_stop("internal error")
        yield from self._finish(run)
        return run.end

    def _prepare(self, run: _Run) -> Job:
        be = self._be
        from bend_stand.core.clock import wall_utc_iso  # noqa: PLC0415

        run.t_start_utc = wall_utc_iso(be.clock)
        if be.recorder.state != "RECORDING":
            g = be.record_start_with({"sequence_start": self._run_header(run)})
            if not g.ok:
                self._set_end(run, "ERROR", "RECORDING", "recording cannot start: " + g.text())
                raise _Ended()
            run.started_recording = True
        run.rec_folder = None if be.recorder.folder is None else str(be.recorder.folder)
        with self._lock:
            if run.state == "PREPARING":
                run.state, run.phase = "RUNNING", "COMMAND"
        self._event(run, "SEQ_START", f"name={run.seq.name or '-'} steps={len(run.seq.steps)} "
                                      f"x_zero={run.x_zero:.4f} ref={run.seq.travel_ref}")
        self._publish(force=True)
        self._check(run)
        yield None

    def _run_header(self, run: _Run) -> dict[str, Any]:
        from bend_stand.core.sequencer import seqfile  # noqa: PLC0415

        p = run.plan
        return {"sequence": seqfile.to_dict(run.seq), "x_zero_mm": run.x_zero, "travel_ref": run.seq.travel_ref,
                "plan_total_s": None if p is None else p.total_s, "plan_len": None if p is None else len(p.steps),
                "windows_total": None if p is None else p.windows_total}

    def _wait_resume(self, run: _Run) -> Job:
        """PAUSED (SW-STOP-004): wait for Resume; RESUME 0x3C (VERIFY, never re-sent), wait PAUSED = 0."""
        be = self._be
        while True:
            with self._lock:
                run.phase = "PAUSED"
            self._publish(force=True)
            seen = run.int_seq
            yield Poll(lambda: run.end is not None or run.resume_req is not None or run.int_seq != seen, 10**18)
            if run.end is not None:
                raise _Ended()
            src = run.resume_req
            if src is None:
                continue
            with self._lock:
                run.phase = "RESUMING"
            fut = be.device.clear_async(pg.Cmd.RESUME)
            res = yield fut
            with self._lock:
                run.resume_req = None
            if run.end is not None:
                raise _Ended()
            if res.outcome == "REFUSED":
                self._event(run, "SEQ_RESUME_REFUSED", res.text)
                # an expected outcome (D-31): the latch's own EVENT terminates the run; if not yet, end it here
                self._set_end(run, "ABORTED", "RESUME_REFUSED", f"Resume refused: {res.text}")
                raise _Ended()
            if res.outcome != "OK":
                with self._lock:
                    run.message = res.text or "Resume not confirmed — press Resume again"
                continue
            ok = yield Poll(lambda: not be.device.last_status & DS.PAUSED or run.end is not None, RESUME_CONFIRM_NS)
            if run.end is not None:
                raise _Ended()
            if not ok:
                with self._lock:
                    run.message = "Resume not confirmed — press Resume again"
                continue
            yield Poll(lambda: (not be.motion.busy and not be.motion.fw_moving()) or run.end is not None,
                       IDLE_WAIT_NS)
            with self._lock:
                if run.end is not None:
                    raise _Ended()
                run.ctl, run.state, run.paused_source, run.message = "RUN", "RUNNING", None, "resumed"
            self._event(run, "SEQ_RESUME", f"source={src} exec={run.exec_idx}")
            self._publish(force=True)
            return

    # ---- step dispatch -------------------------------------------------------------------------------
    def _run_step(self, run: _Run, exec_idx: int, i: int, iters: tuple[int, ...]) -> Job:
        step = run.seq.steps[i]
        kind = StepKind(step.kind)
        if kind == StepKind.TRAVEL:
            yield from self._travel(run, exec_idx, i, iters)
        elif kind == StepKind.LOAD:
            yield from self._load(run, exec_idx, i, iters)
        elif kind == StepKind.HOLD:
            t_r = run.last_t_u
            if t_r is None:
                yield from self._wait_dev(run, 0, self._now() + 2 * S, "data")
                t_r = run.last_t_u or 0
            yield from self._dwell(run, exec_idx, i, iters, t_r, hold=True)
        elif kind == StepKind.HOME:
            yield from self._home(run)
        elif kind == StepKind.TARE:
            yield from self._tare(run)
        else:
            yield from self._mark(run, step)

    def _cap_planned_s(self, step: Any) -> float:
        return float(step.settle_s) + float(step.capture_s)

    # ---- motion ----------------------------------------------------------------------------------------
    def _motion(self, run: _Run, ticket: Any, timeout_s: float, guard: _Guard | None, phase: str) -> Job:
        """Wait for a MoveTicket of the sequence (TIMEOUT guard); returns the ``MoveDone``."""
        if ticket.done() and ticket.exception() is not None:
            exc = ticket.exception()
            self._check(run)
            gate = getattr(exc, "gate", None)
            if isinstance(exc, GateRefused) and gate is not None and "PAUSED" in gate.codes():
                # refused locally because PAUSED is (still) indicated: an expected outcome (D-33 k)
                yield from self._poll(run, lambda: run.ctl == "PAUSE", 2 * S)
                self._check(run)
            text = getattr(exc, "user_text", None) or str(exc)
            self._set_end(run, "STOPPED", "STEP_REFUSED", f"step refused: {text}")
            raise _Ended()
        with self._lock:
            run.phase, run.guard = phase, guard
        self._publish(force=True)
        ok = yield from self._poll(run, ticket.done, int(timeout_s * S))
        run.guard = None
        if not ok:
            self._guard_trip(run, "TIMEOUT", f"no MOVE_DONE within {timeout_s:.1f} s")
            raise _Ended()
        exc = ticket.exception()
        if exc is not None:
            self._check(run)
            self._set_end(run, "STOPPED", "STEP_REFUSED", f"step refused: {getattr(exc, 'user_text', exc)}")
            raise _Ended()
        out = ticket.result()
        if out.kind == "REFUSED_PAUSED":
            yield from self._poll(run, lambda: run.ctl == "PAUSE", 2 * S)   # the PAUSED indication follows
            self._check(run)
            raise _Paused()
        if out.kind != "DONE" or out.done is None:
            self._check(run)
            self._set_end(run, "STOPPED", "STEP_REFUSED", f"move not executed: {out.kind} {out.text}".strip())
            raise _Ended()
        if out.done.reason == "STOPPED":
            yield from self._poll(run, lambda: False, 50 * MS)       # the terminating / pause event is dispatched
            self._check(run)
            self._set_end(run, "STOPPED", "STEP_REFUSED", f"move stopped ({out.done.stop_cause})")
            raise _Ended()
        return out.done

    def _guard_for(self, run: _Run, kind: str, direction: int, target: float | None) -> _Guard:
        f0, _t = self._f_now(run, 1)
        exp = run.seq.pull_dir * direction
        if math.isfinite(f0) and abs(f0) >= GUARD_ARM_N:
            ref = f0                                 # loaded: moving away from zero load = loading
        else:
            ref = target if target is not None and target != 0 else exp
        loading = exp * (1 if ref > 0 else -1) > 0
        return _Guard(kind, exp, target, f0 if math.isfinite(f0) else 0.0, loading)

    def _accel(self, step: Any) -> float | None:
        return float(step.accel_mm_s2) if step.accel_mm_s2 and step.accel_mm_s2 > 0 else None

    def _a_eff(self, step: Any) -> float:
        a = self._accel(step)
        if a is not None:
            return a
        lim = self._be.motion.limits()
        return lim.a_max_mm_s2 if lim is not None else 100.0

    def _travel(self, run: _Run, exec_idx: int, i: int, iters: tuple[int, ...]) -> Job:
        be = self._be
        step = run.seq.steps[i]
        x_m = machine_mm(run.seq, float(step.target), run.x_zero)
        v = run.seq.speed(step)
        x0 = self._x_now()
        dist = abs(x_m - (x0 if x0 is not None else x_m))
        timeout = T.step_timeout_s(dist, v, self._a_eff(step))
        direction = 0 if x0 is None or abs(x_m - x0) < 1e-9 else (1 if x_m > x0 else -1)
        win = None
        if step.capture_during_move:
            t_on32 = yield from self._set_valid(run, True)
            self._check(run)
            t_on = self._unwrap(run, t_on32)
            t_end_plan = t_on + int((T.move_time_s(dist, v, self._a_eff(step)) + 60.0) * 1e6)
            win = _Win(exec_idx, step.uid, iters, t_on, t_end_plan, ramp=True, t_on=t_on)
            run.window = win
            self._event(run, "VALID_ON", f"sequence ramp exec={exec_idx}")
        ticket = be.motion.move_to(x_m, speed_mm_s=v, accel_mm_s2=self._accel(step), owner=OWNER,
                                   accel_fw_default=True)
        guard = self._guard_for(run, "travel", direction, None) if direction else None
        done = yield from self._motion(run, ticket, timeout, guard, "MOVING")
        if done.reason != "TARGET":
            self._set_end(run, "STOPPED", "STEP_REFUSED", f"travel step ended {done.reason}")
            raise _Ended()
        t_r = self._unwrap(run, done.t_us)
        if win is not None:
            win.t_reached = t_r
            t_off32 = yield from self._set_valid(run, False)
            win.t_off = self._unwrap(run, t_off32)
            win.t1 = win.t_off
            yield from self._wait_dev(run, win.t1 + 1, self._now() + 2 * S, "the end of the ramp window")
            self._event(run, "VALID_OFF", f"sequence ramp exec={exec_idx}")
            self._close_window(run, win, step, ramp=True)
        yield from self._dwell(run, exec_idx, i, iters, t_r, settle_only=win is not None)

    def _dwell(self, run: _Run, exec_idx: int, i: int, iters: tuple[int, ...], t_r: int, *, hold: bool = False,
               settle_only: bool = False, load: bool = False) -> Job:
        """settle → capture (VALID window) → hold, all measured from ``t_r`` (device time)."""
        be = self._be
        step = run.seq.steps[i]
        settle = int(round(float(step.settle_s) * 1e6))
        cap = 0 if settle_only else int(round(float(step.capture_s) * 1e6))
        dwell_s = float(step.settle_s) + (0.0 if settle_only else float(step.capture_s))
        if not load:
            dwell_s = max(float(step.step_time_s), dwell_s)
        deadline = self._now() + int(T.hold_timeout_s(dwell_s) * S)
        if cap > 0:
            t0, t1 = t_r + settle, t_r + settle + cap
            win = _Win(exec_idx, step.uid, iters, t0, t1, t_reached=t_r)   # every sample of [t0, t1] (report = live)
            with self._lock:
                run.phase = "SETTLE"
                run.window = win
            self._publish(force=True)
            yield from self._wait_dev(run, t0, deadline, "the capture start")
            rate = be.pipeline.latest_copy().rate_sps or 80.0
            period = int(1e6 / rate)
            with self._lock:
                run.phase = "CAPTURE"
            self._publish(force=True)
            t_on32 = yield from self._set_valid(run, True)
            win.t_on = self._unwrap(run, t_on32)
            self._event(run, "VALID_ON", f"sequence window exec={exec_idx} t0={t0} t1={t1}")
            self._check(run)
            yield from self._wait_dev(run, t1 - period, deadline, "the capture end")
            t_off32 = yield from self._set_valid(run, False)
            win.t_off = self._unwrap(run, t_off32)
            self._event(run, "VALID_OFF", f"sequence window exec={exec_idx}")
            yield from self._wait_dev(run, t1 + 1, deadline, "the capture end")
            self._close_window(run, win, step)
        t_end = t_r + int(round(dwell_s * 1e6))
        with self._lock:
            run.phase = "HOLD" if cap > 0 or hold else "SETTLE"
        self._publish(force=True)
        if run.last_t_u is None or run.last_t_u < t_end:
            yield from self._wait_dev(run, t_end, deadline, "the end of the step time")

    def _discard_window(self, run: _Run, reason: str) -> None:
        w = run.window
        if w is None:
            return
        run.window = None
        if w.t_on is None:                       # still in the settle phase: nothing was captured
            return
        sw = SeqWindow(w.exec_idx, w.uid, w.loop_iters, (w.t0 / 1e6, w.t1 / 1e6),
                       None if w.t_on is None else w.t_on / 1e6, None if w.t_off is None else w.t_off / 1e6, True,
                       reason)
        run.windows.append(self._win_dict(w, run, discarded=True, reason=reason))
        self._be.events.publish("seq.window", sw)
        self._event(run, "SEQ_WINDOW_DISCARDED", f"exec={w.exec_idx} reason={reason}")

    def _win_dict(self, w: _Win, run: _Run, *, discarded: bool = False, reason: str = "") -> dict[str, Any]:
        step = run.seq.steps[run.step_idx] if run.step_idx is not None else None
        sc = self._be.pipeline.scale
        return {"exec_idx": w.exec_idx, "step_idx": run.step_idx, "uid": w.uid, "loop_iters": list(w.loop_iters),
                "kind": None if step is None else StepKind(step.kind).value,
                "label": "" if step is None else step.label, "target": None if step is None else step.target,
                "tol_n": None if step is None or StepKind(step.kind) != StepKind.LOAD else run.seq.tol(step),
                "t0_us_u": w.t0, "t1_us_u": w.t1, "t_reached_us_u": w.t_reached, "t_on_us_u": w.t_on, "t_off_us_u": w.t_off, "ramp": w.ramp,
                "capture_s": (w.t1 - w.t0) / 1e6, "k": sc.k, "tare_raw": sc.tare_raw, "k_est_n_mm": run.k_est,
                "discarded": discarded, "reason": reason}

    def _close_window(self, run: _Run, w: _Win, step: Any, *, ramp: bool = False) -> None:
        run.window = None
        kind = StepKind(step.kind)
        tgt = step.target if kind == StepKind.LOAD else None
        tol = run.seq.tol(step) if kind == StepKind.LOAD else None
        in_t = [t for t in w.t if w.t0 <= t <= w.t1]
        ws = window_stats(w.t, w.flags, w.status, w.sp, w.raw, w.f, t0_us=w.t0, t1_us=w.t1,
                          rate_sps=median_rate_sps(in_t), target_n=tgt, tol_n=tol, ramp=ramp)
        run.windows_done += 1
        d = self._win_dict(w, run)
        run.windows.append(d)
        self._be.events.publish("seq.window", SeqWindow(w.exec_idx, w.uid, w.loop_iters, (w.t0 / 1e6, w.t1 / 1e6),
                                                        None if w.t_on is None else w.t_on / 1e6,
                                                        None if w.t_off is None else w.t_off / 1e6))
        f_end, _t = self._f_now(run)
        xe = self._x_now()
        res = result_from_window(ws, exec_idx=w.exec_idx, step_idx=run.step_idx or 0, uid=w.uid,
                                 label=step.label, kind=kind.value, loop_iters=w.loop_iters, target=step.target,
                                 tol=tol, t0=w.t0, t1=w.t1, k_est=run.k_est, trim_iter=run.trim_iter,
                                 t_reached=w.t_reached,
                                 f_end=f_end, x_end=float("nan") if xe is None else xe - run.x_off)
        self._add_result(run, res)

    def _add_result(self, run: _Run, res: StepResult) -> None:
        run.results.append(res)
        self._be.events.publish("seq.step_result", res)
        self._event(run, "SEQ_RESULT", f"exec={res.exec_idx} uid={res.uid} n={res.n} F={res.f_mean_n:.4f} "
                                       f"x={res.x_mean_mm:.4f} flags={'|'.join(res.flags) or '-'}")

    def _fail_result(self, run: _Run, flags: tuple[str, ...], text: str) -> None:
        if run.step_idx is None or run.exec_idx is None:
            return
        step = run.seq.steps[run.step_idx]
        kind = StepKind(step.kind)
        f_end, _t = self._f_now(run)
        xe = self._x_now()
        self._add_result(run, StepResult(
            run.exec_idx, run.step_idx, step.uid, step.label, kind.value, run.loop_iters, step.target,
            {"travel": "mm", "load": "N"}.get(kind.value, ""), run.seq.tol(step) if kind == StepKind.LOAD else None,
            flags, k_est_n_mm=run.k_est, f_end_n=f_end, x_end_mm=float("nan") if xe is None else xe - run.x_off,
            trim_iterations=run.trim_iter, text=text))

    # ---- load step (SW-SEQ-006) ------------------------------------------------------------------------
    def _bound_mm(self, direction: int) -> float | None:
        lo, hi = self._be.motion.travel_range_mm()
        return hi if direction > 0 else lo

    def _load(self, run: _Run, exec_idx: int, i: int, iters: tuple[int, ...]) -> Job:
        be = self._be
        step = run.seq.steps[i]
        f_t = float(step.target)
        tol = run.seq.tol(step)
        v = run.seq.speed(step)
        s = be.session.get()
        sc = be.pipeline.scale
        if sc.k is None or sc.tare_raw is None or not sc.force_available:
            self._set_end(run, "STOPPED", "STEP_REFUSED", "load step without a valid force (calibration + tare)")
            raise _Ended()
        yield from self._wait_dev(run, (run.last_t_u or 0) + 1, self._now() + 2 * S, "data")
        f_now, t_last = self._f_now(run)
        if not math.isfinite(f_now):
            self._set_end(run, "STOPPED", "STEP_REFUSED", "load step: no valid force samples")
            raise _Ended()
        e = f_t - f_now
        pull = run.seq.pull_dir
        t_r: int | None = t_last if abs(e) <= tol else None
        x_bound: float | None = None
        if t_r is None:
            sgn = 1 if e > 0 else -1
            d = sgn * pull                                       # motion direction (pull_dir · error sign)
            band = T.approach_band(tol, run.k_est, v)
            f_stop = f_t - sgn * band
            x_bound = self._bound_mm(d)
            if (f_stop - f_now) * sgn > 0:                       # outside the band: FW-timed approach
                x0 = self._x_now()
                if x_bound is None or x0 is None or (x_bound - x0) * d * 1000.0 < 0.5:
                    run.fail_flags.append("BOUND_NOT_AHEAD")
                    self._set_end(run, "STOPPED", "BOUND_NOT_AHEAD", "load step: the approach bound is not ahead of "
                                                                      "the axis (axis at the travel limit)")
                    raise _Ended()
                raw_stop, cmp_ = T.force_to_raw_stop(f_stop, float(sc.k), float(sc.tare_raw), sgn)
                timeout = T.step_timeout_s(abs(x_bound - x0), v, self._a_eff(step))
                run.approach = []
                ticket = be.motion.move_until_load(x_bound, speed_mm_s=v, raw_stop=raw_stop, cmp=cmp_,
                                                   accel_mm_s2=self._accel(step), owner=OWNER)
                self._event(run, "SEQ_APPROACH", f"exec={exec_idx} F_t={f_t:g} F_stop={f_stop:.3f} raw_stop={raw_stop}"
                                                 f" cmp={cmp_} bound={x_bound:.4f} timeout={timeout:.1f}")
                guard = self._guard_for(run, "load", d, f_t)
                try:
                    done = yield from self._motion(run, ticket, timeout, guard, "APPROACH")
                finally:
                    ap, run.approach = run.approach, None
                if done.reason == "BOUND":
                    yield from self._wait_dev(run, self._unwrap(run, done.t_us) + 50_000, self._now() + 2 * S, "data")
                    f_b, _ = self._f_now(run)
                    run.fail_flags.append("NOT_REACHED")
                    self._fail_result(run, ("NOT_REACHED",), f"load {f_t:g} N not reached at the approach bound "
                                                             f"{x_bound:.3f} mm (F = {f_b:.1f} N)")
                    self._set_end(run, "STOPPED", "NOT_REACHED", f"step {i + 1}: load {f_t:g} N not reached at the "
                                                                 f"approach bound (F = {f_b:.1f} N)")
                    raise _Ended()
                if done.reason != "LOAD_THRESHOLD":
                    self._set_end(run, "STOPPED", "STEP_REFUSED", f"approach ended {done.reason}")
                    raise _Ended()
                xs = [a for a, _b in (ap or [])]
                fs = [b for _a, b in (ap or [])]
                k, measured = T.k_est_from_approach(xs, fs, pull_dir=pull, k_min=s.k_min_n_mm, k_max=s.k_max_n_mm,
                                                    fallback=run.k_est)
                if measured:
                    run.k_est = k
                t_done = self._unwrap(run, done.t_us)
            else:
                t_done = run.last_t_u or 0
            # ---- trim (≤ max_iter moves)
            base_x = self._x_now()
            for it in range(int(s.trim_max_iter) + 1):
                with self._lock:
                    run.phase, run.trim_iter = "TRIM", it
                self._publish(force=True)
                yield from self._wait_dev(run, t_done + int(T.SETTLE_AFTER_DONE_S * 1e6), self._now() + 5 * S,
                                          "the trim settle time")
                f_m, t_m = self._f_now(run)
                if math.isfinite(f_m) and abs(f_t - f_m) <= tol:
                    t_r = t_m
                    break
                if it >= int(s.trim_max_iter) or not math.isfinite(f_m):
                    run.fail_flags.append("NOT_REACHED")
                    self._fail_result(run, ("NOT_REACHED", f"TRIM_ITER_{it}"),
                                      f"trim did not converge in {it} moves (F = {f_m:.2f} N, target {f_t:g} N)")
                    self._set_end(run, "STOPPED", "NOT_REACHED", f"step {i + 1}: trim did not reach {f_t:g} ± "
                                                                 f"{tol:g} N in {it} moves")
                    raise _Ended()
                dx = pull * T.trim_step(f_m, f_t, run.k_est, s.trim_kp, s.trim_max_step_mm)
                base_x = self._x_now() if base_x is None else base_x
                tgt = (base_x or 0.0) + dx
                lo, hi = be.motion.travel_range_mm()
                at_bound = (lo is not None and tgt < lo) or (hi is not None and tgt > hi)
                if at_bound:
                    run.fail_flags.append("NOT_REACHED")
                    self._fail_result(run, ("NOT_REACHED",), "trim reached the travel bound")
                    self._set_end(run, "STOPPED", "NOT_REACHED", f"step {i + 1}: trim reached the travel bound")
                    raise _Ended()
                tv = min(float(s.trim_v_mm_s), v)
                timeout = T.step_timeout_s(abs(dx), tv, self._a_eff(step))
                ticket = be.motion.move_to(tgt, speed_mm_s=tv, owner=OWNER, accel_fw_default=True)
                guard = self._guard_for(run, "load", (1 if dx > 0 else -1), f_t)
                done = yield from self._motion(run, ticket, timeout, guard, "TRIM")
                base_x = done.pos_mm
                t_done = self._unwrap(run, done.t_us)
                run.trim_iter = it + 1
        if t_r is None:                                         # pragma: no cover - loop always sets or raises
            t_r = run.last_t_u or 0
        self._event(run, "SEQ_REACHED", f"exec={exec_idx} t_reached={t_r} trim={run.trim_iter} "
                                        f"k_est={run.k_est:.3f}")
        yield from self._dwell(run, exec_idx, i, iters, t_r, load=True)

    # ---- home / tare / mark ------------------------------------------------------------------------------
    def _home(self, run: _Run) -> Job:
        be = self._be
        x0 = self._x_now() or 0.0
        ticket = be.motion.home(load_confirmed=run.home_confirmed, owner=OWNER)
        timeout = HOME_TIMEOUT_S + abs(x0) / 5.0
        with self._lock:
            run.phase = "HOME"
        done = yield from self._motion(run, ticket, timeout, None, "HOME")
        if done.reason != "TARGET":
            self._set_end(run, "STOPPED", "HOME_FAILED", f"homing ended {done.reason}")
            raise _Ended()
        yield from self._poll(run, lambda: bool(be.device.last_flags & DF.HOMED), 2 * S)

    def _tare(self, run: _Run) -> Job:
        be = self._be
        with self._lock:
            run.phase = "TARE"
        self._publish(force=True)
        last_move = None
        for (t, _x, _f, _r) in run.recent:
            last_move = t
        # SW-TARE-003: ≥ 1 s after the last move (device time)
        yield from self._poll(run, lambda: not be._gate_snapshot().moved_recently, 3 * S)  # noqa: SLF001
        win = be.session.get().tare_window_s
        g = be.tare_engine.start_tare(win)
        if not g.ok:
            self._set_end(run, "STOPPED", "TARE_REFUSED", "tare refused: " + g.text())
            raise _Ended()
        ok = yield from self._poll(run, lambda: be.tare_engine.state().phase in ("DONE", "REFUSED", "ABORTED"),
                                   int((win + 30.0) * S))
        st = be.tare_engine.state()
        if not ok or st.phase != "DONE":
            self._set_end(run, "STOPPED", "TARE_REFUSED", "tare failed: " + ("; ".join(st.errors) or st.phase))
            raise _Ended()
        dev = be.device
        ok = yield from self._poll(run, lambda: dev.thresholds.state in ("VERIFIED", "DEFAULT_ONLY")
                                   and dev.threshold_mgr.matches(dev.thresholds), 5 * S)
        if not ok:
            self._set_end(run, "STOPPED", "TARE_REFUSED", "FW thresholds not verified after the tare")
            raise _Ended()
        del last_move

    def _mark(self, run: _Run, step: Any) -> Job:
        self._event(run, "SEQ_MARK", step.label or "mark")
        if step.wait_operator:
            with self._lock:
                run.state, run.phase, run.continue_req = "WAITING_OPERATOR", "WAIT_OPERATOR", False
                run.message = f"waiting for the operator: {step.label or 'Continue'}"
            self._publish(force=True)
            yield from self._poll(run, lambda: run.continue_req, 10**18)
            with self._lock:
                run.state, run.continue_req, run.message = "RUNNING", False, ""
        yield None

    # ---- end ---------------------------------------------------------------------------------------------
    def _finish(self, run: _Run) -> Job:
        be = self._be
        end = run.end or ("STOPPED", "UNKNOWN", "")
        if run.window is not None:
            self._discard_window(run, end[1])
        fails = [f for f in run.fail_flags if f in ("SLIP", "BREAK_DETECTED", "TIMEOUT", "DRIVER_ALARM",
                                                     "BOUND_NOT_AHEAD")]
        if fails:
            self._fail_result(run, tuple(dict.fromkeys(fails)), end[2])
        with self._lock:
            run.state = end[0]
            run.phase = "END"
            run.guard = None
        be.owner = "MANUAL"
        be.motion.resync()
        self._event(run, "SEQ_END", f"state={end[0]} reason={end[1]} {end[2]}")
        be.events.log(f"sequence {end[0]}: {end[2]}", logging.INFO if end[0] == "FINISHED" else logging.WARNING)
        self._publish(force=True)
        if run.valid_on and end[1] in ("LINK_LOST", "ERROR"):
            yield from self._clear_valid_after_link_loss(run)
        log_entry = self.run_log(run)
        if run.started_recording and be.recorder.state in ("RECORDING", "FAILED"):
            yield Sleep(TAIL_NS)
            run.collecting = False
            be.recorder.meta.setdefault("sequence_runs", []).append(log_entry)
            be.record_stop()
            self._build_report(run)
        else:
            run.collecting = False
            if be.recorder.state in ("RECORDING", "FAILED"):
                be.recorder.meta.setdefault("sequence_runs", []).append(log_entry)
                self.report_pending = run
        self._publish(force=True)
        return None

    def _build_report(self, run: _Run) -> None:
        be = self._be
        folder = be.recorder.folder
        if folder is None:
            return
        try:
            from bend_stand.core.report import build_report  # noqa: PLC0415

            s = be.session.get()
            paths = build_report(str(folder), bend3p=s.bend3p, bend3p_compliance=s.compliance_mm_per_n)
            run.report_folder = paths.folder
            be.events.publish("report.ready", paths)
        except Exception as exc:  # noqa: BLE001 — the run result stays; the report can be rebuilt offline
            log.exception("report failed")
            be.events.log(f"report not built: {exc}", logging.ERROR)

    def build_pending_report(self) -> None:
        """The operator's recording (started before the sequence) stopped: build its report now."""
        run, self.report_pending = self.report_pending, None
        if run is not None:
            self._build_report(run)
            self._publish(force=True)

    def run_log(self, run: _Run) -> dict[str, Any]:
        """Sidecar entry ``meta.json`` → ``sequence_runs[]``: everything the offline report needs (SW-REP-003)."""
        from bend_stand.core.report import result_to_dict  # noqa: PLC0415

        end = run.end or ("", "", "")
        return {**self._run_header(run), "start_utc": run.t_start_utc, "state": end[0], "end_reason": end[1],
                "message": end[2], "k_est_final_n_mm": run.k_est, "pull_dir": run.seq.pull_dir,
                "windows": list(run.windows), "results": [result_to_dict(r) for r in run.results],
                "events": list(run.events), "scale_log": list(run.scale_log),
                "x_off_mm": run.x_off}


def result_from_window(ws: Any, *, exec_idx: int, step_idx: int, uid: str, label: str, kind: str,
                       loop_iters: tuple[int, ...], target: float | None, tol: float | None, t0: int, t1: int,
                       k_est: float | None, trim_iter: int = 0, f_end: float = float("nan"),
                       x_end: float = float("nan"), t_reached: int | None = None) -> StepResult:
    """``calc.steady.WindowStats`` → ``StepResult`` (shared by the live executor and the offline report)."""
    flags = tuple(ws.flags)
    if trim_iter:
        flags += (f"TRIM_ITER_{trim_iter}",)
    r, f, x = ws.raw, ws.f, ws.x
    return StepResult(exec_idx, step_idx, uid, label, kind, tuple(loop_iters), target,
                      {"travel": "mm", "load": "N"}.get(kind, ""), tol, flags, ws.n, ws.n_expected, f.mean, f.std,
                      f.min, f.max, f.se, f.drift, x.mean, x.std, x.drift, r.mean, r.std, r.min, r.max, r.se, r.drift,
                      dict(ws.stats), None if t_reached is None else t_reached / 1e6, (t0 / 1e6, t1 / 1e6), k_est,
                      f_end, x_end, ws.ramp, trim_iter, "")


def replace_result(res: StepResult, **kw: Any) -> StepResult:
    return replace(res, **kw)


__all__ = ["SequenceExecutor", "OWNER", "result_from_window", "STATUS_PERIOD_NS"]
