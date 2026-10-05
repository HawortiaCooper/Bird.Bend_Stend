"""Travel-calibration engine and the active-travel-calibration restore rule (SW_design §9.3, §9.3.1; R4 §5; SRS
SW-CAL-001…004).

Phases: ``CHECK`` (gate, spm0 = board steps/mm, restore-pending record written) → ``BACKLASH`` (Continue: +2 mm in the
measuring direction) → ``REFERENCE`` (operator zeroes the caliper; ``s_ref`` = FW step count from MOVE_DONE; Continue:
``N1 = round(10·spm0)`` steps as an absolute target computed with the FW rounding) → ``MOVE1`` → ``ENTER_D1``
(``spm1 = N1_actual / D1``, SW-CAL-003 plausibility with confirmations; trial value written to the board **RAM** + read
back, the record notes it) → ``MOVE2`` (Continue: ``N2 = round(50·spm1)`` steps further, same direction) →
``ENTER_DTOT`` (``spm2 = (N1 + N2)/D_tot``, consistency warning "repeat" > 0.5 %) → ``RESULT`` → accept: SET spm2 +
read-back, SAVE_PARAMS, ``travel_<UTC>.json`` + ``active_travel.json``, record deleted → ``DONE``. While the wizard
runs it owns motion (``Backend.owner = "TRAVEL_CAL"``; the manual motion gates refuse ``OWNER_CONFLICT``).

**Restore rule** (§9.3.1): every exit other than accept (Cancel, STOP, HALT, PAUSE, E-stop, faults, link loss …)
restores spm0: board reachable → ``RESTORING`` (wait ≤ 2 s for standstill, SET spm0 + read-back, record deleted) →
``ABORTED``; link down / app exit → the record stays and ``post_sync_job`` (every connect / BOOT resync) resolves it:
board = spm0 → record deleted; board = trial value → spm0 written automatically (configuration restore, never
motion); anything else → indicator ``travel_cal_differs`` with the operator decisions
``resolve_job("restore" | "keep_board" | "ignore_session")``. Independently, a board value differing from
``active_travel.json`` raises the indicator (nothing written automatically).

Implements: SW-CAL-001, SW-CAL-002, SW-CAL-003, SW-CAL-004
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from bend_stand import __version__
from bend_stand.calc.motion import f32
from bend_stand.calc.rounding import round_half_away
from bend_stand.calc.travelcal import consistency, spm_from_steps, target_for_steps, travel_plausibility
from bend_stand.core.clock import wall_utc_iso
from bend_stand.core.engine import EngineBase, refuse
from bend_stand.core.errors import BendStandError, CommandTimeout, FileFormatError, LinkError
from bend_stand.core.jobs import Job, Poll
from bend_stand.core.model import (
    INT_DF, INT_SYS, ConfirmRequest, GateId, GateResult, InputSpec, TravelCalResult, TravelDiffState,
)

if TYPE_CHECKING:  # pragma: no cover
    from bend_stand.core.backend import Backend

log = logging.getLogger("bend_stand.core.calibration.travel")
MS = 1_000_000
KEY = "motion.steps_per_mm"
OWNER = "TRAVEL_CAL"
BACKLASH_MM, L1_MM, L2_MM = 2.0, 10.0, 50.0
ROOM_MM = BACKLASH_MM + L1_MM + L2_MM


class TravelCalEngine(EngineBase):
    KIND = "travel_cal"
    TOPIC = "cal.travel.state"
    PHASES = ("CHECK", "BACKLASH", "REFERENCE", "MOVE1", "ENTER_D1", "MOVE2", "ENTER_DTOT", "RESULT", "ACCEPT",
              "DONE")
    TERMINAL = ("IDLE", "DONE", "ABORTED")

    def __init__(self, backend: Backend) -> None:
        super().__init__(backend)
        self._r = TravelCalResult(0.0)
        self._v = 2.0
        self._s_ref = 0
        self._s1 = 0
        self._moving = False
        self._spm1_board: float | None = None
        self._cfg_dirty0 = False
        self._trial_written = False
        self.diff = TravelDiffState()
        self._ignored_uid: str | None = None
        self._pending_confirm: list[tuple[str, str]] = []

    # ================================================================================ start
    def start(self, *, v_mm_s: float | None = None, confirmed: bool = False, **_kw: Any) -> GateResult:
        be = self._be
        if self.active:
            return self._busy()
        g = be._gates()[GateId.CAL_TRAVEL_START]  # noqa: SLF001
        if not g.ok:
            return g
        need = self._needs_confirmation(g, confirmed)
        if need is not None:
            return need
        spm0 = be.device.params.get(KEY)
        if spm0 is None:
            return refuse("NO_PARAMS", "board parameters not read")
        self._v = float(be.session.get().cal_v_mm_s if v_mm_s is None else v_mm_s)
        self._r = TravelCalResult(float(spm0))
        self._spm1_board = None
        self._trial_written = False
        st = be.device.board
        self._cfg_dirty0 = bool(st is not None and st.sys_flags & INT_SYS.CFG_DIRTY)
        self._pending_confirm = []
        info = be.device.info
        try:
            be.calibration_store.write_restore(info.uid if info else None, float(spm0), None, wall_utc_iso(be.clock))
        except OSError as exc:
            return refuse("FILE", f"restore record not written: {exc}")
        be.owner = OWNER
        self._reset(phase="CHECK", title="Travel calibration", step_count=len(self.PHASES), can_cancel=True,
                    result=self._r)
        self._set(phase="BACKLASH", step_index=1, can_continue=True, continue_moves=True,
                  continue_label="Move +2 mm ▶",
                  instruction=f"No specimen mounted. Board steps/mm = {spm0:.3f}. The axis first moves +2 mm (backlash "
                              "take-up); all moves go in the + direction.")
        return g

    # ================================================================================ continue
    def continue_(self, inputs: Mapping[str, float] | None = None, *, confirmed: bool = False) -> None:
        ph = self._state.phase
        if self._moving or not self.active:
            return
        if ph == "BACKLASH":
            x = self._be.motion.position_mm()
            if x is None:
                self._abort("position unknown")
                return
            self._move(x + BACKLASH_MM, "BACKLASH", self._backlash_done)
        elif ph == "REFERENCE":
            n1 = round_half_away(L1_MM * self._r.spm0)
            tgt, _n = target_for_steps(self._s_ref, n1, self._r.spm0)
            self._move(tgt / 1000.0, "MOVE1", self._move1_done)
        elif ph == "ENTER_D1":
            self._enter_d1(inputs or {}, confirmed)
        elif ph == "MOVE2":
            spm1 = self._spm1_board or self._r.spm1 or self._r.spm0
            n2 = round_half_away(L2_MM * spm1)
            tgt, _n = target_for_steps(self._s1, n2, spm1)
            self._move(tgt / 1000.0, "MOVE2", self._move2_done)
        elif ph == "ENTER_DTOT":
            self._enter_dtot(inputs or {}, confirmed)
        elif ph == "RESULT":
            self._accept(confirmed)

    def repeat(self) -> None:
        return None

    def _move(self, target_mm: float, phase: str, cb: Any) -> None:
        be = self._be
        self._moving = True
        self._set(phase=phase, can_continue=False, errors=(), needs_confirmation=None, inputs=())
        ticket = be.motion.move_to(target_mm, speed_mm_s=self._v, owner=OWNER)
        ticket.add_done_callback(lambda f: self._on_ticket(f, phase, cb))

    def _on_ticket(self, f: Any, phase: str, cb: Any) -> None:
        self._moving = False
        if self._state.phase != phase:
            return                                     # aborted meanwhile
        exc = f.exception()
        if exc is not None:
            self._abort(getattr(exc, "user_text", None) or str(exc))
            return
        out = f.result()
        if out.kind != "DONE" or out.done is None or out.done.reason != "TARGET":
            why = out.text or (out.done.reason if out.done is not None else out.kind)
            if out.done is not None and out.done.stop_cause:
                why = f"{out.done.reason} ({out.done.stop_cause})"
            self._abort(f"move ended: {why}")
            return
        cb(out.done)

    def _backlash_done(self, done: Any) -> None:
        self._s_ref = int(done.pos_steps)
        self._set(phase="REFERENCE", step_index=2, can_continue=True, continue_moves=True,
                  continue_label="Move 10 mm ▶",
                  instruction="Zero the caliper / dial indicator at the current position, then Continue: the axis "
                              f"moves {L1_MM:g} mm (N1 = {round_half_away(L1_MM * self._r.spm0)} steps).")

    def _move1_done(self, done: Any) -> None:
        self._s1 = int(done.pos_steps)
        n1 = self._s1 - self._s_ref
        self._r = TravelCalResult(self._r.spm0, n1)
        self._set(phase="ENTER_D1", step_index=4, result=self._r, can_continue=True, continue_moves=False,
                  continue_label="Set trial steps/mm ▶",
                  inputs=(InputSpec("d1_mm", "Measured distance D1", "mm", 0.001, 100.0, L1_MM),),
                  instruction=f"Enter the measured distance D1 (nominal {L1_MM:g} mm, {n1} steps issued).")

    def _check(self, spm: float, d: float, cons: float | None, confirmed: bool) -> bool:
        exp = self._be.session.get().expected_spm
        chk = travel_plausibility(spm, self._r.spm0, exp, d_mm=d, consistency_rel=cons)
        if not chk.ok:
            self._set(errors=chk.reject)
            return False
        warns = tuple(t for _c, t in chk.warn)
        if chk.confirm and not confirmed:
            self._set(errors=(), warnings=warns, needs_confirmation=ConfirmRequest(
                chk.confirm[0][0], " — ".join(t for _c, t in chk.confirm)))
            return False
        self._set(errors=(), warnings=warns, needs_confirmation=None)
        return True

    def _enter_d1(self, inputs: Mapping[str, float], confirmed: bool) -> None:
        try:
            d1 = float(inputs["d1_mm"])
        except (KeyError, TypeError, ValueError):
            self._set(errors=("enter D1 in mm",))
            return
        if not d1 > 0:
            self._set(errors=("measured distance must be > 0 mm",))
            return
        n1 = int(self._r.n1 or 0)
        spm1 = spm_from_steps(n1, d1)
        if not self._check(spm1, d1, None, confirmed):
            return
        self._r = TravelCalResult(self._r.spm0, n1, d1, spm1)
        be = self._be
        info = be.device.info
        try:
            be.calibration_store.write_restore(info.uid if info else None, self._r.spm0, spm1, wall_utc_iso(be.clock))
        except OSError as exc:
            self._abort(f"restore record not written: {exc}")
            return
        self._trial_written = True
        self._set(phase="ENTER_D1", can_continue=False, result=self._r, instruction="writing the trial steps/mm …")
        be._job(self._set_spm_job, spm1).add_done_callback(self._trial_set)  # noqa: SLF001

    def _set_spm_job(self, spm: float) -> Job:
        """SET_PARAM (RAM only — SET never writes flash) + GET_PARAM read-back, compared as binary32."""
        d = self._be.device
        yield from d.set_param_job(KEY, spm)
        got = yield from d.get_param_job(KEY)
        if f32(got) != f32(spm):
            raise LinkError(f"steps/mm read-back {got} != {f32(spm)}")
        return got

    def _trial_set(self, f: Any) -> None:
        if self._state.phase != "ENTER_D1":
            return
        if f.exception() is not None:
            self._abort(f"trial steps/mm not set: {f.exception()}")
            return
        self._spm1_board = float(f.result())
        self._be.motion.resync()
        self._set(phase="MOVE2", step_index=5, can_continue=True, continue_moves=True,
                  continue_label="Move 50 mm ▶",
                  instruction=f"Trial steps/mm {self._spm1_board:.3f} set (board RAM). Do not touch the caliper; "
                              f"Continue: the axis moves {L2_MM:g} mm more in the same direction.")

    def _move2_done(self, done: Any) -> None:
        n2 = int(done.pos_steps) - self._s1
        r = self._r
        self._r = TravelCalResult(r.spm0, r.n1, r.d1_mm, r.spm1, n2)
        self._set(phase="ENTER_DTOT", step_index=6, result=self._r, can_continue=True, continue_moves=False,
                  continue_label="Compute ▶",
                  inputs=(InputSpec("dtot_mm", "Measured TOTAL distance D_tot", "mm", 0.001, 200.0,
                                    L1_MM + L2_MM),),
                  instruction=f"Enter the TOTAL distance from the reference (nominal {L1_MM + L2_MM:g} mm).")

    def _enter_dtot(self, inputs: Mapping[str, float], confirmed: bool) -> None:
        r = self._r
        try:
            dtot = float(inputs["dtot_mm"])
        except (KeyError, TypeError, ValueError):
            self._set(errors=("enter D_tot in mm",))
            return
        if r.d1_mm is None or not dtot > r.d1_mm:
            self._set(errors=("the total distance must exceed D1",))
            return
        n1, n2 = int(r.n1 or 0), int(r.n2 or 0)
        spm2 = spm_from_steps(n1 + n2, dtot)
        inc, cons = consistency(n2, dtot, r.d1_mm, float(r.spm1 or r.spm0))
        chk = travel_plausibility(spm2, r.spm0, self._be.session.get().expected_spm, d_mm=dtot, consistency_rel=cons)
        if not chk.ok:
            self._set(errors=chk.reject)
            return
        self._r = TravelCalResult(r.spm0, n1, r.d1_mm, r.spm1, n2, dtot, spm2, inc, cons)
        conf = list(chk.confirm)
        if self._cfg_dirty0:
            conf.append(("CFG_DIRTY", "the board has other unsaved parameter changes: accepting saves them too"))
        self._pending_confirm = conf
        self._set(phase="RESULT", step_index=7, result=self._r, can_continue=True, continue_moves=False,
                  continue_label="Accept ▶", inputs=(), errors=(), warnings=tuple(t for _c, t in chk.warn),
                  needs_confirmation=ConfirmRequest(conf[0][0], " — ".join(t for _c, t in conf)) if conf else None,
                  instruction=f"spm0 {r.spm0:.3f} → spm1 {float(r.spm1 or 0):.3f} → spm2 {spm2:.3f} steps/mm "
                              f"(step 2 alone {inc:.3f}, {100 * cons:+.3f} %). Accept saves it to the board NVM.")

    # ================================================================================ accept
    def _accept(self, confirmed: bool) -> None:
        if self._pending_confirm and not confirmed:
            self._set(errors=("confirm the result first",))
            return
        be = self._be
        if be.motion.fw_moving() or be.motion.busy:
            self._set(errors=("accept only while the axis stands still",))
            return
        self._set(phase="ACCEPT", step_index=8, can_continue=False, errors=())
        be._job(self._accept_job).add_done_callback(self._accepted)  # noqa: SLF001

    def _accept_job(self) -> Job:
        be = self._be
        r = self._r
        assert r.spm2 is not None
        got = yield from self._set_spm_job(r.spm2)
        yield from be.device.save_job()
        info = be.device.info
        rec = {"created_utc": wall_utc_iso(be.clock), "operator": be.marks.get().operator, "sw_version": __version__,
               "board": {"uid": info.uid if info else "", "fw_version": ".".join(map(str, info.fw_version))
                         if info else ""},
               "spm0": r.spm0, "n1": r.n1, "d1_mm": r.d1_mm, "spm1": r.spm1, "n2": r.n2, "d_tot_mm": r.d_tot_mm,
               "spm2": float(got), "spm2_computed": r.spm2, "spm2_inc": r.spm2_inc, "consistency": r.consistency,
               "expected_spm": be.session.get().expected_spm,
               "confirmations": [c for c, _t in self._pending_confirm], "notes": ""}
        path = be.calibration_store.save_travel(rec)
        be.calibration_store.delete_restore()
        return path, float(got)

    def _accepted(self, f: Any) -> None:
        be = self._be
        if f.exception() is not None:
            exc = f.exception()
            self._abort(f"accept failed: {getattr(exc, 'user_text', None) or exc}")
            return
        path, spm = f.result()
        self._trial_written = False
        be.owner = "MANUAL"
        be.motion.resync()
        self.diff = TravelDiffState()
        be.record_event("CAL_TRAVEL_ACTIVATED", f"{path.name} steps/mm={spm:.3f}")
        self._set(phase="DONE", step_index=9, can_cancel=False, needs_confirmation=None,
                  instruction=f"Travel calibration active: {spm:.3f} steps/mm ({path.name}).")

    # ================================================================================ abort / restore
    def cancel(self) -> None:
        if self.active and self._state.phase not in ("ACCEPT", "RESTORING"):
            self._abort("cancelled")

    def terminate(self, reason: str) -> None:
        if self.active and self._state.phase not in ("RESTORING",):
            self._abort(reason)

    def _abort(self, reason: str) -> None:
        be = self._be
        self._moving = False
        be.owner = "MANUAL"
        if not self._trial_written:
            try:
                be.calibration_store.delete_restore()
            except OSError:  # pragma: no cover
                pass
            self._set(phase="ABORTED", abort_reason=reason, can_continue=False, can_cancel=False,
                      needs_confirmation=None, inputs=())
            return
        self._set(phase="RESTORING", abort_reason=reason, can_continue=False, can_cancel=False,
                  needs_confirmation=None, inputs=(), instruction="restoring the previous steps/mm …")
        if not be.device.connected:
            self._restore_failed("link down — steps/mm restored at the next connect")
            return
        be._job(self._restore_job).add_done_callback(self._restored)  # noqa: SLF001

    def _restore_job(self) -> Job:
        d = self._be.device
        yield Poll(lambda: not d.last_flags & INT_DF.MOVING, 2000 * MS)          # MOVING = 0 (≤ 2 s)
        got = yield from self._set_spm_job(self._r.spm0)
        self._be.calibration_store.delete_restore()
        return got

    def _restored(self, f: Any) -> None:
        if f.exception() is not None:
            self._restore_failed(f"restore failed: {f.exception()}")
            return
        self._trial_written = False
        self._be.motion.resync()
        self.diff = TravelDiffState()
        self._be.record_event("CAL_TRAVEL_RESTORED", f"steps/mm {self._r.spm0:.3f}")
        self._set(phase="ABORTED", instruction=f"steps/mm restored to {self._r.spm0:.3f}")

    def _restore_failed(self, text: str) -> None:
        d = self._be.device
        self.diff = TravelDiffState(True, self._r.spm0, d.params.get(KEY), True, ("restore", "keep_board",
                                                                                  "ignore_session"),
                                    "restore_pending", False)
        self._be.events.publish("cal.travel.restore", self.diff)
        self._set(phase="ABORTED", instruction=text)

    # ================================================================================ connect / decisions
    def post_sync_job(self) -> Job:
        """After every (re)connect / BOOT resync (§9.3.1): resolve a restore-pending record, compare with
        ``active_travel.json``; returns the new ``TravelDiffState``."""
        be = self._be
        d = be.device
        if self.active and self._state.phase != "RESTORING":
            return self.diff
        store = be.calibration_store
        uid = d.info.uid if d.info else None
        board = d.params.get(KEY)
        rec = store.restore_pending()
        diff = TravelDiffState()
        if rec is not None and board is not None and rec.get("board_uid") in (None, uid):
            spm0, trial = float(rec["spm0"]), rec.get("spm_trial")
            if f32(board) == f32(spm0):
                store.delete_restore()
            elif trial is not None and f32(board) == f32(float(trial)) and not d.last_flags & INT_DF.MOVING:
                try:
                    yield from self._set_spm_job(spm0)
                    store.delete_restore()
                    be.record_event("CAL_TRAVEL_RESTORED", f"steps/mm {spm0:.3f} restored after reconnect")
                    be.motion.resync()
                except (LinkError, CommandTimeout, BendStandError) as exc:
                    diff = TravelDiffState(True, spm0, board, True, ("restore", "keep_board", "ignore_session"),
                                           "restore_pending")
                    log.warning("travel restore after reconnect failed: %s", exc)
            else:
                diff = TravelDiffState(True, spm0, board, True, ("restore", "keep_board", "ignore_session"),
                                       "restore_pending")
        if not diff.differs:
            try:
                act = store.active_travel()
            except FileFormatError:
                act = None
            board = d.params.get(KEY)
            if act is not None and board is not None and f32(float(act["spm2"])) != f32(board):
                diff = TravelDiffState(True, float(act["spm2"]), board, False, ("restore", "ignore_session"),
                                       "active_file")
        if diff.differs and uid is not None and uid == self._ignored_uid:
            diff = TravelDiffState(True, diff.session_spm, diff.board_spm, diff.restore_pending, diff.actions,
                                   diff.source, True)
        if self.active and self._state.phase == "RESTORING" and not diff.restore_pending:
            self._trial_written = False
            self._set(phase="ABORTED", instruction=f"steps/mm restored to {self._r.spm0:.3f}")
        self.diff = diff
        be.events.publish("cal.travel.restore", diff)
        return diff

    def resolve_job(self, action: str) -> Job:
        be = self._be
        d = be.device
        diff = self.diff
        store = be.calibration_store
        if action == "restore":
            ref = diff.session_spm
            if ref is None:
                rec = store.restore_pending()
                act = store.active_travel()
                ref = float(rec["spm0"]) if rec else (float(act["spm2"]) if act else None)
            if ref is None:
                raise BendStandError("nothing to restore")
            if d.last_flags & INT_DF.MOVING:
                raise BendStandError("the axis is moving — restore when it stands still")
            yield from self._set_spm_job(ref)
            store.delete_restore()
            be.motion.resync()
            be.record_event("TRAVEL_DIFF_RESTORE", f"steps/mm {ref:.3f}")
        elif action == "keep_board":
            store.delete_restore()
            be.record_event("TRAVEL_DIFF_KEEP_BOARD", f"board steps/mm {d.params.get(KEY)}")
        elif action == "ignore_session":
            self._ignored_uid = d.info.uid if d.info else None
            be.record_event("TRAVEL_DIFF_IGNORE_SESSION", "")
        else:
            raise ValueError(f"unknown action {action!r}")
        return (yield from self.post_sync_job())


__all__ = ["TravelCalEngine", "OWNER", "ROOM_MM"]
