"""MotionController — manual motion of the PC application (SW_design §5.4), M2 (WP-B12).

All values in SW units (mm, mm/s, mm/s²); wire units only at the ``Device`` boundary (mm → µm with round half away
from zero, SYS-003). Absolute targets only on the wire (IF-009).

* **move_to / move_by** — gate + speed / accel caps + travel range (FW soft limits ∩ enabled SW travel limits,
  SW-LIM-001) + direction-aware LIMIT, refused locally with ``GateRefused`` (nothing sent). ``move_by`` adds to the
  **last commanded target incl. a pending one** (SW-MAN-002/003). **Latest wins** (D-29 i): while a manual move of
  this backend runs, a new target becomes ``pending_target_mm`` (a newer one replaces it; the replaced ticket
  resolves ``CANCELLED``) and is sent on MOVE_DONE reason TARGET; any other MOVE_DONE reason, a stop, PAUSE or a
  latch drops it.
* **MoveTicket** = ``ReleasingFuture[MoveOutcome]``: ``DONE`` (EVENT MOVE_DONE, with the preceding STOPPED cause),
  ``REFUSED_PAUSED`` (NACK with BLOCK PAUSED — an expected outcome, D-33 k), ``REFUSED`` (other NACK),
  ``NOT_EXECUTED`` (VERIFY timeout resolved by GET_STATUS, §4.4.1 — never re-sent), ``CANCELLED`` (epoch drop,
  superseded, link loss, board reset).
* **Jog** (SW-MAN-004): ``jog_start`` sends JOG at once; the Supervisor tick refreshes it every **80 ms** (absolute
  schedule, < 100 ms incl. tick and jitter, SWD-P1-11) with the newest speed and bound while the session is active,
  the motion epoch is unchanged and the GUI beat is younger than 300 ms; otherwise the refresh stops and the FW
  dead-man ends the jog (SAF-FW-016). 3 consecutive refresh timeouts → ``jog_stop``; a refresh NACK ends the
  session. ``jog_stop`` sends JOG 0 (controlled stop). Bound: homed and an SW travel limit enabled in the jog
  direction → ``bound_um`` = that limit, else ``JOG_NO_BOUND``. The jog speed is clamped to the cap that applies
  (un-homed: ``motion.v_unhomed_um_s``).
* **enable / disable(confirmed) / home(load_confirmed)** with the SAF-SW-004 confirmations (DISABLE "specimen
  unloaded?", HOME under unknown or ≥ 5 % FS load → HOME flags bit0).
* **Stops**: ``on_stop_issued`` (STOP / HALT / PAUSE written by the backend) and the FW EVENTs STOPPED / PAUSED /
  ESTOP_SET / DRIVER_POWER 0 / BOOT end the jog session and drop the pending target **first**; the running ticket
  resolves with the MOVE_DONE that follows (or ``CANCELLED`` at BOOT / link loss).

* **M3**: motion owner (``owner="TRAVEL_CAL"`` for the travel wizard; manual calls are refused with
  ``OWNER_CONFLICT`` while a wizard owns motion), SW trip latch direction (REFUSE ``SW_TRIP`` for a target / jog that
  would increase a latched SW-limit violation, SAF-SW-001 §6.1 rule 4), SAF-SW-006 speed-vs-margin WARN,
  ``current_end_um()`` (end point of the running command for the safety supervisor), test-zero rows.

Thread safety: decisions are taken under ``_lock``; no channel call is made while holding it (the channel resolves
futures under its own lock).

Implements: SW-MAN-001…006 (backend part), SW-LIM-001 (travel range, jog bound), SAF-SW-004 (HOME / DISABLE
confirmations), SW-STOP-004 (jog / pending dropped on PAUSE), IF-005 (motion never auto-retried), IF-009,
SAF-SW-001 (direction-aware trip latch), SAF-SW-006 (margin warning)
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bend_stand.calc.limits import limit_margin_warning
from bend_stand.calc.motion import rate_cap_um_s
from bend_stand.calc.rounding import round_half_away
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.errors import CommandTimeout, ConfirmationRequired, GateRefused, NackError
from bend_stand.core.gates import CLEAR_HINTS, g_disable, g_enable, g_motion, g_test_zero
from bend_stand.core.link import CommandDropped, Lane
from bend_stand.core.model import (
    INT_DF, INT_DS, GateCode, GateItem, GateResult, MotionKind, MotionLimits, MoveDone, MoveOutcome, Severity,
)
from bend_stand.core.observers import ReleasingFuture, failed_future
from bend_stand.io import protocol as P

if TYPE_CHECKING:  # pragma: no cover
    from bend_stand.core.backend import Backend

log = logging.getLogger("bend_stand.core.motion")
MS = 1_000_000
Cmd = pg.Cmd
DF, DS = INT_DF, INT_DS
JOG_REFRESH_NS = 80 * MS
GUI_BEAT_MAX_MS = 300.0
JOG_TIMEOUTS_MAX = 3
LOADED_MARGIN = 0.8                       # SW "loaded" predicate at 0.8 × release band (safe side, §5.4)
R, W = Severity.REFUSE, Severity.WARN


@dataclass(eq=False)
class _Active:
    kind: str                             # MOVE | HOME
    ticket: ReleasingFuture
    target_um: int | None
    epoch: int
    sent_ns: int = 0


@dataclass(eq=False)
class _Pending:
    target_mm: float
    v_um_s: int
    a_um_s2: int
    ticket: ReleasingFuture


@dataclass(eq=False)
class _Jog:
    direction: int
    speed_mm_s: float
    bound_um: int
    epoch: int
    next_due_ns: int
    timeouts: int = 0


def _resolve(t: ReleasingFuture | None, outcome: MoveOutcome) -> None:
    if t is not None and not t.done():
        t.set_result(outcome)


def _um(mm: float) -> int:
    return round_half_away(float(mm) * 1000.0)


class MotionController:
    def __init__(self, backend: Backend) -> None:
        self._be = backend
        self._lock = threading.RLock()
        self.commanded_target_mm: float | None = None
        self.x_zero_mm = 0.0
        self._active: _Active | None = None
        self._pending: _Pending | None = None
        self._jog: _Jog | None = None
        self._stop_cause: str | None = None
        self._enabling_until_ns = 0
        self._last_paused = False
        self._done_ns = 0
        self._done_pos_mm: float | None = None

    # ================================================================================ helpers
    @property
    def _dev(self) -> Any:
        return self._be.device

    @property
    def pending_target_mm(self) -> float | None:
        p = self._pending
        return None if p is None else p.target_mm

    @property
    def jogging(self) -> bool:
        return self._jog is not None

    def fw_moving(self) -> bool:
        """MOVING from the newest DATA / STATUS, unless that indication is older than the last MOVE_DONE (the frame
        after the MOVE_DONE may not have arrived yet)."""
        d = self._dev
        return bool(d.last_flags & DF.MOVING) and d.last_flags_ns > self._done_ns

    @property
    def busy(self) -> bool:
        """A motion command of this backend is running (manual move or homing)."""
        return self._active is not None

    def position_mm(self) -> float | None:
        """Commanded position: setpoint of the newest DATA frame, else STATUS ``pos_um``."""
        lt = self._be.pipeline.latest_copy()
        if self._done_pos_mm is not None and lt.t_host_ns <= self._done_ns:
            return self._done_pos_mm                  # MOVE_DONE newer than the newest DATA frame: final position
        if lt.t_host_ns and not math.isnan(lt.x_mm):
            return lt.x_mm
        st = self._dev.board
        return None if st is None else st.pos_um / 1000.0

    def enabling_left_ms(self, now: int | None = None) -> int:
        now = self._be.clock.monotonic_ns() if now is None else now
        if not self._dev.connected or self._dev.last_flags & DF.ENABLED:
            return 0
        return max(0, (self._enabling_until_ns - now) // MS)

    def _param(self, key: str, default: Any = None) -> Any:
        """Board value as last read; before the first read the **dictionary default** (``params_gen``), never a
        hand-written number (ICD v0.7.3 / dict 6: e.g. ``motion.max_step_rate_hz`` 40 000)."""
        v = self._dev.params.get(key)
        if v is None:
            meta = pgen.BY_KEY.get(key)
            return default if meta is None else meta.default
        return v

    def _loaded(self) -> bool:
        """SW mirror of the FW 'loaded' predicate (abs(raw − zero_raw) ≥ release band, or AFE stale / unknown),
        evaluated on the safe side at 0.8 × the band (SW_design §5.4)."""
        if self._dev.last_status & DS.AFE_STALE:
            return True
        lt = self._be.pipeline.latest_copy()
        if not lt.t_host_ns or math.isnan(lt.raw):
            return True
        band = float(self._param("safety.release_band_raw"))
        return abs(lt.raw - float(self._param("safety.zero_raw"))) >= LOADED_MARGIN * band

    # ================================================================================ limits / check
    def travel_range_mm(self) -> tuple[float | None, float | None]:
        """FW soft limits ∩ enabled SW travel limits (machine mm)."""
        lo = self._param("limits.soft_min_um")
        hi = self._param("limits.soft_max_um")
        lo = None if lo is None else lo / 1000.0
        hi = None if hi is None else hi / 1000.0
        cfg = self._be.limits.get()
        if cfg.travel_min_enabled and cfg.travel_min_mm is not None:
            lo = cfg.travel_min_mm if lo is None else max(lo, cfg.travel_min_mm)
        if cfg.travel_max_enabled and cfg.travel_max_mm is not None:
            hi = cfg.travel_max_mm if hi is None else min(hi, cfg.travel_max_mm)
        return lo, hi

    def limits(self) -> MotionLimits | None:
        d = self._dev
        if not d.params.values():
            return None
        spm = float(self._param("motion.steps_per_mm"))
        v_travel = self._param("motion.v_max_travel_um_s") / 1000.0
        v_load = self._param("motion.v_max_load_um_s") / 1000.0
        v_step = rate_cap_um_s(int(self._param("motion.max_step_rate_hz")), spm) / 1000.0
        loaded = self._loaded()
        st = d.board
        fresh = st is not None and self._be.clock.monotonic_ns() - d.board_ns <= 1500 * MS
        lo, hi = self.travel_range_mm()
        return MotionLimits(v_travel, v_load, v_step, self._param("motion.v_unhomed_um_s") / 1000.0,
                            self._param("motion.a_max_um_s2") / 1000.0, loaded,
                            min(v_load if loaded else v_travel, v_step),
                            st.v_limit_um_s / 1000.0 if fresh and st is not None else None, lo, hi)

    def _cap(self, kind: MotionKind, lim: MotionLimits) -> float:
        cap = lim.v_cap_mm_s
        if kind == MotionKind.LOAD_APPROACH:
            cap = min(lim.v_load_mm_s, lim.v_step_rate_mm_s)
        if kind in (MotionKind.JOG, MotionKind.HOME) and not self._dev.last_flags & DF.HOMED:
            cap = min(cap, lim.v_unhomed_mm_s)
        return cap

    def _static_gate(self, kind: MotionKind, owner: str = "MANUAL") -> GateResult:
        snap = self._be._gate_snapshot()  # noqa: SLF001
        return g_motion(snap, kind, owner)

    def _trip_items(self, direction: int) -> list[GateItem]:
        """REFUSE ``SW_TRIP`` when motion in ``direction`` would increase a latched SW-limit violation (§6.1 rule 4)."""
        sup = getattr(self._be, "safety", None)
        if sup is None or direction == 0:
            return []
        t = sup.direction_refused(direction, self._be.session.get().pull_dir)
        if t is None:
            return []
        return [GateItem(GateCode.SW_TRIP, R, f"SW limit {t.limit} tripped: this direction increases the violation",
                         CLEAR_HINTS["SW_TRIP"])]

    def _margin_items(self, speed_mm_s: float | None) -> list[GateItem]:
        """SAF-SW-006: WARN when k_est·v·0.065 s > F_fw − F_pc (enabled load limits, outside the no-specimen mode)."""
        be = self._be
        s = be.session.get()
        cfg = be.limits.get()
        if s.k_est_n_mm is None or be.load_input.no_specimen:
            return []
        trips = [abs(t) for t, en in ((cfg.pull_trip_n, cfg.pull_enabled), (cfg.push_trip_n, cfg.push_enabled)) if en]
        v = s.manual_speed_mm_s if speed_mm_s is None else speed_mm_s
        if trips and v and limit_margin_warning(s.k_est_n_mm, v, cfg.fw_level_n, max(trips)):
            return [GateItem(GateCode.SAF_SW_006_MARGIN, W, f"speed {v:g} mm/s too high for the limit margin "
                             f"{cfg.fw_level_n - max(trips):.0f} N with k_est {s.k_est_n_mm:g} N/mm (overshoot "
                             f"≈ {s.k_est_n_mm * v * 0.065:.0f} N)")]
        return []

    def check(self, kind: MotionKind, *, speed_mm_s: float | None = None, accel_mm_s2: float | None = None,
              target_mm: float | None = None, owner: str = "MANUAL") -> GateResult:
        """Pure check for the GUI fields (GRQ-B-05, < 1 ms): the motion gate plus caps, range and direction."""
        items = list(self._static_gate(kind, owner).items)
        items += self._margin_items(speed_mm_s)
        lim = self.limits()
        if lim is not None:
            cap = self._cap(kind, lim)
            if speed_mm_s is not None:
                if not speed_mm_s > 0:
                    items.append(GateItem(GateCode.SPEED_CAP, R, "speed must be > 0"))
                elif speed_mm_s > cap + 1e-9:
                    items.append(GateItem(GateCode.SPEED_CAP, R, f"speed {speed_mm_s:g} mm/s above the cap "
                                          f"{cap:g} mm/s" + (" (loaded)" if lim.loaded else "")))
            if accel_mm_s2 is not None and (accel_mm_s2 <= 0 or accel_mm_s2 > lim.a_max_mm_s2 + 1e-9):
                items.append(GateItem(GateCode.ACCEL_CAP, R, f"acceleration {accel_mm_s2:g} mm/s² outside "
                                      f"(0, {lim.a_max_mm_s2:g}] mm/s²"))
        if target_mm is not None and kind == MotionKind.MOVE:
            lo, hi = self.travel_range_mm()
            if (lo is not None and target_mm < lo - 1e-9) or (hi is not None and target_mm > hi + 1e-9):
                los = "−∞" if lo is None else f"{lo:g}"
                his = "∞" if hi is None else f"{hi:g}"
                items.append(GateItem(GateCode.TARGET_OUT_OF_RANGE, R, f"target {target_mm:g} mm outside the travel "
                                      f"range {los}…{his} mm"))
            x = self.position_mm()
            if x is not None:
                items += self._limit_toward(target_mm - x)
                d = target_mm - x
                items += self._trip_items(0 if abs(d) < 1e-9 else (1 if d > 0 else -1))
        return GateResult(tuple(items))

    def _limit_toward(self, delta: float) -> list[GateItem]:
        st = self._dev.last_status
        if (delta > 0 and st & DS.LIMIT_END) or (delta < 0 and st & DS.LIMIT_START):
            return [GateItem("LIMIT", R, "limit switch active in this direction — only motion away from it",
                             CLEAR_HINTS["LIMIT"])]
        return []

    # ================================================================================ move_to / move_by
    def _speed_um(self, speed_mm_s: float | None, kind: MotionKind) -> int:
        lim = self.limits()
        if speed_mm_s is None:
            s = self._be.session.get().manual_speed_mm_s
            if lim is not None:
                s = min(s, self._cap(kind, lim))
        else:
            s = speed_mm_s
        return max(1, _um(s))

    def _accel_um(self, accel_mm_s2: float | None) -> int:
        if accel_mm_s2 is None:
            accel_mm_s2 = self._be.session.get().manual_accel_mm_s2
        return 0 if accel_mm_s2 is None else max(1, _um(accel_mm_s2))

    def move_to(self, target_mm: float, *, speed_mm_s: float | None = None,
                accel_mm_s2: float | None = None, owner: str = "MANUAL",
                accel_fw_default: bool = False) -> ReleasingFuture:
        """``accel_fw_default=True`` (sequencer): accel 0 on the wire = ``motion.a_max_um_s2`` (ICD §5.4)."""
        target_mm = float(target_mm)
        g = self.check(MotionKind.MOVE, speed_mm_s=speed_mm_s, accel_mm_s2=accel_mm_s2, target_mm=target_mm,
                       owner=owner)
        if not g.ok:
            return failed_future(GateRefused(g))
        v = self._speed_um(speed_mm_s, MotionKind.MOVE)
        a = 0 if accel_fw_default and accel_mm_s2 is None else self._accel_um(accel_mm_s2)
        ticket = ReleasingFuture()
        superseded: _Pending | None = None
        with self._lock:
            act = self._active
            if act is not None and act.kind == "MOVE":
                superseded, self._pending = self._pending, _Pending(target_mm, v, a, ticket)   # latest wins
                send = False
            elif act is not None or self._jog is not None or self.fw_moving():
                return failed_future(GateRefused(GateResult((GateItem(GateCode.MOTION_ACTIVE, R, "axis moving"),))))
            else:
                send = True
        if superseded is not None:
            _resolve(superseded.ticket, MoveOutcome("CANCELLED", text="superseded by a newer target"))
        if send:
            self._send_move(target_mm, v, a, ticket)
        else:
            self._be.events.publish("motion.target", {"pending_target_mm": target_mm})
        return ticket

    def move_by(self, delta_mm: float, **kw: Any) -> ReleasingFuture:
        with self._lock:
            base = self.pending_target_mm
            if base is None:
                base = self.commanded_target_mm
        if base is None:
            base = self.position_mm()
        if base is None:
            return failed_future(GateRefused(GateResult((GateItem(GateCode.LINK_DOWN, R, "position unknown"),))))
        return self.move_to(base + float(delta_mm), **kw)

    def _send_move(self, target_mm: float, v_um_s: int, a_um_s2: int, ticket: ReleasingFuture) -> None:
        d = self._dev
        ch = d.channel
        if ch is None:
            _resolve(ticket, MoveOutcome("CANCELLED", text="not connected"))
            return
        target_um = _um(target_mm)
        act = _Active("MOVE", ticket, target_um, ch.motion_epoch, self._be.clock.monotonic_ns())
        with self._lock:
            self._active = act
            self.commanded_target_mm = target_mm
        fut = d.request(Cmd.MOVE_ABS, P.build_request(Cmd.MOVE_ABS, target_um=target_um, v_um_s=v_um_s,
                                                      a_um_s2=a_um_s2), epoch=act.epoch)
        self._be.events.publish("motion.target", {"commanded_target_mm": target_mm})
        fut.add_done_callback(lambda f: self._on_motion_response(act, f))

    # ================================================================================ responses
    def _finish_active(self, act: _Active, outcome: MoveOutcome, *, drop_pending: bool = True) -> None:
        with self._lock:
            if self._active is act:
                self._active = None
            p = self._pending if drop_pending else None
            if drop_pending:
                self._pending = None
        _resolve(act.ticket, outcome)
        if p is not None:
            _resolve(p.ticket, MoveOutcome("CANCELLED", text=f"previous move ended: {outcome.kind}"))
        if outcome.kind != "DONE":
            self.resync()

    def _on_motion_response(self, act: _Active, f: Any) -> None:
        exc = f.exception()
        if exc is None:
            return                                         # accepted: the ticket resolves with MOVE_DONE
        if isinstance(exc, NackError):
            paused = exc.name == "E_STATE" and bool(exc.detail & pg.Block.PAUSED)
            self._finish_active(act, MoveOutcome("REFUSED_PAUSED" if paused else "REFUSED", text=exc.detail_text))
            if paused:
                self._be.events.log(f"{exc.cmd} refused: paused — press Resume (expected, D-33 k)", logging.INFO)
            return
        if isinstance(exc, CommandDropped):
            self._be.events.publish("motion.dropped", {"cmd": {"MOVE": "MOVE_ABS", "MUL": "MOVE_UNTIL_LOAD"}.get(
                act.kind, "HOME")})
            self._finish_active(act, MoveOutcome("CANCELLED", text=str(exc)))
            return
        if isinstance(exc, CommandTimeout) and self._dev.channel is not None:
            # VERIFY: never re-sent; GET_STATUS decides (§4.4.1)
            self._dev.channel.submit(Cmd.GET_STATUS).add_done_callback(lambda g: self._on_verify_status(act, g))
            return
        self._finish_active(act, MoveOutcome("CANCELLED", text=str(exc)))

    def _on_verify_status(self, act: _Active, g: Any) -> None:
        with self._lock:
            if self._active is not act:
                return                                     # MOVE_DONE arrived meanwhile
        if g.exception() is not None or g.result() is None:
            self._finish_active(act, MoveOutcome("NOT_EXECUTED", text="outcome unknown (GET_STATUS failed)"))
            return
        st = P.decode_status(g.result().body)
        self._dev._apply_status(st, g.result().t_host_ns, g.result().t_sent_ns)  # noqa: SLF001
        ms = st.motion
        if act.kind == "MOVE":
            executed = ms in ("MOVE_ABS", "STOPPING") and st.target_um == act.target_um
            if not executed and ms == "IDLE" and act.target_um is not None and st.pos_um == act.target_um:
                # a short move that already ended while its response and its MOVE_DONE were lost (SWD-M2-03)
                md = MoveDone("TARGET", st.pos_um / 1000.0, st.pos_steps, st.t_us, None)
                self._done_ns, self._done_pos_mm = self._be.clock.monotonic_ns(), md.pos_mm
                self._be.events.publish("motion.done", md)
                self._finish_active(act, MoveOutcome("DONE", md, text="MOVE_DONE not received; position = target "
                                                                       "by GET_STATUS"), drop_pending=False)
                self._send_pending_after(act)
                return
        elif act.kind == "MUL":
            executed = ms in ("MOVE_UNTIL_LOAD", "STOPPING") and st.target_um == act.target_um
        else:
            executed = ms in ("HOMING", "STOPPING") or st.home_phase not in (int(pg.HomePhase.NONE),
                                                                              int(pg.HomePhase.DONE))
        if not executed:
            self._finish_active(act, MoveOutcome("NOT_EXECUTED", text="not executed (no automatic re-send)"))

    def move_until_load(self, bound_mm: float, *, speed_mm_s: float, raw_stop: int, cmp: int,
                        accel_mm_s2: float | None = None, owner: str = "SEQUENCE") -> ReleasingFuture:
        """FW-MOT-006 approach of a load-target step (SW-SEQ-006): MOVE_UNTIL_LOAD toward the absolute ``bound_mm``
        (direction = sign(bound − x)); refused locally (``GateRefused``) by the LOAD_APPROACH gate, the cap
        ``v_max_load`` and ``BOUND_NOT_AHEAD`` (a bound not strictly ahead of the axis, F-B-28). The ticket resolves
        with MOVE_DONE (LOAD_THRESHOLD / BOUND / STOPPED); never auto-retried."""
        bound_mm = float(bound_mm)
        g = self.check(MotionKind.LOAD_APPROACH, speed_mm_s=speed_mm_s, accel_mm_s2=accel_mm_s2, owner=owner)
        items = list(g.items)
        x = self.position_mm()
        bound_um = _um(bound_mm)
        if x is None or bound_um == _um(x):
            items.append(GateItem(GateCode.BOUND_NOT_AHEAD, R, "approach bound not ahead of the axis"))
        else:
            d = 1 if bound_um > _um(x) else -1
            items += self._limit_toward(d) + self._trip_items(d)
        g = GateResult(tuple(items))
        if not g.ok:
            return failed_future(GateRefused(g))
        ch = self._dev.channel
        if ch is None:
            return failed_future(GateRefused(GateResult((GateItem(GateCode.LINK_DOWN, R, "not connected"),))))
        ticket = ReleasingFuture()
        with self._lock:
            if self._active is not None or self._jog is not None or self.fw_moving():
                return failed_future(GateRefused(GateResult((GateItem(GateCode.MOTION_ACTIVE, R, "axis moving"),))))
            act = _Active("MUL", ticket, bound_um, ch.motion_epoch, self._be.clock.monotonic_ns())
            self._active = act
        v = max(1, _um(speed_mm_s))
        a = 0 if accel_mm_s2 is None else max(1, _um(accel_mm_s2))
        fut = self._dev.request(Cmd.MOVE_UNTIL_LOAD, P.build_request(
            Cmd.MOVE_UNTIL_LOAD, bound_um=bound_um, v_um_s=v, a_um_s2=a, raw_stop=int(raw_stop), cmp=int(cmp)),
            epoch=act.epoch)
        self._be.events.publish("motion.target", {"bound_mm": bound_mm})
        fut.add_done_callback(lambda f: self._on_motion_response(act, f))
        return ticket

    def _send_pending_after(self, act: _Active) -> None:
        with self._lock:
            pend, self._pending = self._pending, None
        if pend is not None:
            self._send_move(pend.target_mm, pend.v_um_s, pend.a_um_s2, pend.ticket)

    # ================================================================================ FW events
    def on_fw_event(self, ev: P.EventPayload) -> None:
        """Pipeline thread, in arrival order with DATA (after ``Device.handle_fw_event``)."""
        E = pg.Event
        code = ev.code
        if code == E.MOVE_DONE:
            self._on_move_done(ev)
        elif code == E.STOPPED:
            try:
                self._stop_cause = pg.StopCause(ev.arg).name
            except ValueError:
                self._stop_cause = f"CAUSE_{ev.arg}"
            self._drop("FW stop")
        elif code in (E.PAUSED, E.ESTOP_SET, E.HALT_SET, E.LINK_WDG, E.FAULT_SET, E.LIMIT_SET) or \
                (code == E.DRIVER_POWER and ev.arg == 0):
            self._drop(E(code).name)
        elif code == E.BOOT:
            self._done_pos_mm = None
            with self._lock:
                act, self._active = self._active, None
            self._drop("board reset")
            if act is not None:
                _resolve(act.ticket, MoveOutcome("CANCELLED", text="board reset"))
            self.resync()

    def _on_move_done(self, ev: P.EventPayload) -> None:
        try:
            reason = pg.MoveDoneReason(ev.arg).name
        except ValueError:
            reason = f"REASON_{ev.arg}"
        self._done_ns = self._be.clock.monotonic_ns()
        self._done_pos_mm = ev.value / 1000.0
        md = MoveDone(reason, ev.value / 1000.0, ev.value2, ev.t_us,
                      self._stop_cause if reason == "STOPPED" else None)
        self._stop_cause = None
        with self._lock:
            act, self._active = self._active, None
            pend = self._pending if (act is not None and act.kind == "MOVE" and reason == "TARGET") else None
            dropped = None
            if pend is None:
                dropped, self._pending = self._pending, None
            else:
                self._pending = None
            jog = self._jog
            if jog is not None:
                self._jog = None                           # a jog ends with its MOVE_DONE
        self._be.events.publish("motion.done", md)
        if act is not None:
            _resolve(act.ticket, MoveOutcome("DONE", md))
        if dropped is not None:
            _resolve(dropped.ticket, MoveOutcome("CANCELLED", text=f"move ended: {reason}"))
        if pend is not None:
            self._send_move(pend.target_mm, pend.v_um_s, pend.a_um_s2, pend.ticket)
        elif reason != "TARGET":
            with self._lock:
                self.commanded_target_mm = md.pos_mm          # resync at standstill (§5.4)

    def _drop(self, why: str) -> None:
        """End the jog session and drop the pending target (stop / pause / latch, D-30)."""
        with self._lock:
            p, self._pending = self._pending, None
            self._jog = None
        if p is not None:
            _resolve(p.ticket, MoveOutcome("CANCELLED", text=why))

    def on_stop_issued(self, cmd: str) -> None:
        """STOP / HALT / PAUSE written by this backend (priority path): drop jog + pending at once (§4.5 (2))."""
        self._drop(cmd)

    def on_link_down(self) -> None:
        with self._lock:
            act, self._active = self._active, None
        self._drop("link lost")
        if act is not None:
            _resolve(act.ticket, MoveOutcome("CANCELLED", text="link lost"))

    def resync(self) -> None:
        """commanded_target := commanded position at standstill (after stops, faults, clears, reconnect)."""
        x = self.position_mm()
        with self._lock:
            if self._active is None:
                self.commanded_target_mm = x

    # ================================================================================ enable / disable / home
    def enable(self) -> GateResult:
        g = g_enable(self._be._gate_snapshot())  # noqa: SLF001
        if not g.ok:
            return g
        t0 = self._be.clock.monotonic_ns()
        fut = self._dev.request(Cmd.ENABLE)

        def done(f: Any) -> None:
            if f.exception() is None and f.result() is not None:
                self._enabling_until_ns = t0 + P.decode_u16(f.result().body) * MS
            elif isinstance(f.exception(), NackError):
                self._be.events.log(f"ENABLE refused: {f.exception().detail_text}", logging.WARNING)
        fut.add_done_callback(done)
        return g

    def disable(self, *, confirmed: bool = False) -> GateResult:
        g = g_disable(self._be._gate_snapshot())  # noqa: SLF001
        if not g.ok:
            return g
        if g.confirm_items and not confirmed:
            return GateResult(g.items + (GateItem(GateCode.CONFIRMATION_REQUIRED, R,
                                                  "confirm: " + g.confirm_items[0].text),))
        fut = self._dev.request(Cmd.DISABLE)
        fut.add_done_callback(lambda f: self.resync())
        return g

    def home(self, *, load_confirmed: bool = False, owner: str = "MANUAL") -> ReleasingFuture:
        g = self._static_gate(MotionKind.HOME, owner)
        if not g.ok:
            return failed_future(GateRefused(g))
        if g.confirm_items and not load_confirmed:
            return failed_future(ConfirmationRequired(g))
        ch = self._dev.channel
        if ch is None:
            return failed_future(GateRefused(g))
        ticket = ReleasingFuture()
        with self._lock:
            if self._active is not None or self._jog is not None:
                return failed_future(GateRefused(GateResult((GateItem(GateCode.MOTION_ACTIVE, R, "axis moving"),))))
            act = _Active("HOME", ticket, None, ch.motion_epoch, self._be.clock.monotonic_ns())
            self._active = act
        flags = int(pg.HomeFlags.LOAD_CONFIRMED) if load_confirmed else 0
        fut = self._dev.request(Cmd.HOME, P.build_request(Cmd.HOME, flags=flags), epoch=act.epoch)
        fut.add_done_callback(lambda f: self._on_motion_response(act, f))
        return ticket

    def set_valid(self, flag: bool) -> GateResult:
        """Manual VALID toggle (SW-MAN-006)."""
        from bend_stand.core.model import GateId  # noqa: PLC0415

        g = self._be._gates()[GateId.VALID_TOGGLE]  # noqa: SLF001
        if g.ok:
            self._be._job(self._dev.set_valid_job, bool(flag))  # noqa: SLF001
            rec = getattr(self._be, "record_event", None)
            if rec is not None:
                rec("VALID_ON" if flag else "VALID_OFF", "manual")
        return g

    # ================================================================================ test zero
    def set_test_zero(self) -> float:
        g = g_test_zero(self._be._gate_snapshot())  # noqa: SLF001
        if not g.ok:
            raise GateRefused(g)
        x = self.position_mm()
        self.x_zero_mm = 0.0 if x is None else x
        self._be.events.publish("log", {"text": f"X_ZERO {self.x_zero_mm:.4f} mm"})
        self._zero_changed(f"{self.x_zero_mm:.4f} mm")
        return self.x_zero_mm

    def reset_test_zero(self) -> None:
        self.x_zero_mm = 0.0
        self._be.events.publish("log", {"text": "X_ZERO reset (machine coordinate)"})
        self._zero_changed("0 (machine coordinate)")

    def _zero_changed(self, text: str) -> None:
        """Test travel zero changed: scale + derived accumulators (work, peak) restart, X_ZERO event row (B5-16)."""
        cb = getattr(self._be, "on_test_zero", None)
        if cb is not None:
            cb(self.x_zero_mm, text)

    def current_end_um(self) -> int | None:
        """End point of the running motion command of this backend (MOVE_ABS target, JOG bound) for the safety
        supervisor's travel rule (§6.1 rule 3); None when unknown or unbounded."""
        act, jog = self._active, self._jog
        if act is not None and act.kind in ("MOVE", "MUL"):
            return act.target_um
        if jog is not None and jog.bound_um != int(pg.JOG_NO_BOUND):
            return jog.bound_um
        return None

    # ================================================================================ jog
    def _jog_bound_um(self, direction: int) -> int:
        if not self._dev.last_flags & DF.HOMED:
            return int(pg.JOG_NO_BOUND)
        cfg = self._be.limits.get()
        if direction > 0 and cfg.travel_max_enabled and cfg.travel_max_mm is not None:
            return _um(cfg.travel_max_mm)
        if direction < 0 and cfg.travel_min_enabled and cfg.travel_min_mm is not None:
            return _um(cfg.travel_min_mm)
        return int(pg.JOG_NO_BOUND)

    def _jog_payload(self, jog: _Jog) -> bytes:
        lim = self.limits()
        speed = jog.speed_mm_s if lim is None else min(jog.speed_mm_s, self._cap(MotionKind.JOG, lim))
        v = max(1, _um(speed)) * jog.direction
        return P.build_request(Cmd.JOG, v_um_s=v, a_um_s2=self._accel_um(None), bound_um=jog.bound_um)

    def jog_start(self, direction: int, speed_mm_s: float) -> None:
        """Hold-to-jog press (SW-MAN-004); refused silently (log + event) when the jog gate refuses."""
        d = 1 if direction > 0 else -1
        self._be.gui_beat()                                # the press is a GUI action
        with self._lock:
            jog = self._jog
        if jog is not None and jog.direction == d:
            self.jog_update(speed_mm_s)
            return
        g = self._static_gate(MotionKind.JOG)
        items = list(g.items) + self._limit_toward(d) + self._trip_items(d)
        bound = self._jog_bound_um(d)
        x = self.position_mm()
        if bound != int(pg.JOG_NO_BOUND) and x is not None and (bound - _um(x)) * d <= 0:
            items.append(GateItem(GateCode.BOUND_NOT_AHEAD, R, "at or beyond the SW travel limit in this direction"))
        if not speed_mm_s > 0:
            items.append(GateItem(GateCode.SPEED_CAP, R, "jog speed must be > 0"))
        res = GateResult(tuple(items))
        ch = self._dev.channel
        if not res.ok or ch is None:
            self._be.events.log(f"jog refused: {res.text()}", logging.INFO)
            return
        now = self._be.clock.monotonic_ns()
        jog = _Jog(d, float(speed_mm_s), bound, ch.motion_epoch, now + JOG_REFRESH_NS)
        with self._lock:
            self._jog = jog
        self._send_jog(jog)

    def jog_update(self, speed_mm_s: float) -> None:
        with self._lock:
            if self._jog is not None and speed_mm_s > 0:
                self._jog.speed_mm_s = float(speed_mm_s)

    def jog_stop(self) -> None:
        """Release / focus loss: JOG 0 (controlled stop, RETRY class, SAFETY lane)."""
        with self._lock:
            had, self._jog = self._jog is not None, None
        board_jog = self._dev.board is not None and self._dev.board.motion == "JOG"
        if had or board_jog:
            self._dev.request(Cmd.JOG, P.build_request(Cmd.JOG, v_um_s=0, a_um_s2=0, bound_um=int(pg.JOG_NO_BOUND)),
                              lane=Lane.SAFETY, key="jog0")

    def _send_jog(self, jog: _Jog) -> None:
        fut = self._dev.request(Cmd.JOG, self._jog_payload(jog), epoch=jog.epoch)
        fut.add_done_callback(lambda f: self._on_jog_response(jog, f))

    def _on_jog_response(self, jog: _Jog, f: Any) -> None:
        exc = f.exception()
        if exc is None:
            jog.timeouts = 0
            return
        if isinstance(exc, CommandTimeout):
            jog.timeouts += 1
            if jog.timeouts >= JOG_TIMEOUTS_MAX:
                with self._lock:
                    current = self._jog is jog
                if current:
                    self._be.events.log("jog: 3 refresh timeouts — JOG 0 sent", logging.WARNING)
                    self.jog_stop()
            return
        with self._lock:                                   # NACK / dropped: the session ends at once
            if self._jog is jog:
                self._jog = None
        if isinstance(exc, NackError):
            if exc.name == "E_RANGE" and exc.detail == 0 and not self._dev.last_flags & DF.HOMED:
                # D-43 b: un-homed travel window (origin ± home.max_travel_um) reached in this direction
                self._be.events.log("jog ended: un-homed travel window reached — home the axis (only motion back "
                                    "into the window is accepted)", logging.INFO)
            else:
                self._be.events.log(f"jog ended: {exc.detail_text}", logging.INFO)

    def tick(self, now: int) -> None:
        """Supervisor tick (5 ms; lockstep: every step): jog refresh, PAUSED edge from DATA / STATUS."""
        d = self._dev
        paused = bool(d.last_status & DS.PAUSED)
        if paused and not self._last_paused:
            self._drop("PAUSED")
        self._last_paused = paused
        with self._lock:
            jog = self._jog
        if jog is None:
            return
        ch = d.channel
        if ch is None or not d.connected or ch.motion_epoch != jog.epoch:
            with self._lock:
                if self._jog is jog:
                    self._jog = None
            return
        if now < jog.next_due_ns:
            return
        age = self._be.liveness.age_ms("gui", now)
        if age is None or age > GUI_BEAT_MAX_MS:
            with self._lock:                               # GUI not alive: let the FW dead-man stop the jog
                if self._jog is jog:
                    self._jog = None
            self._be.events.log("jog refresh stopped: GUI not responding (FW dead-man stops the jog)",
                                logging.WARNING)
            return
        jog.next_due_ns += JOG_REFRESH_NS
        if jog.next_due_ns <= now:
            jog.next_due_ns = now + JOG_REFRESH_NS
        self._send_jog(jog)


__all__ = ["MotionController", "JOG_REFRESH_NS"]
