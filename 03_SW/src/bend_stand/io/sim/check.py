"""Pure command acceptance of the simulated FW (ICD v0.5 §4.4 check order, §4.3 BLOCK mask, §5, §6.3,
§11.4). **Written from the ICD text**, not copied from the Integrator's oracle ``ref_cmdcheck.py`` (otherwise the
differential check of SW_design §12.5 would prove nothing).

``check(st, ftype, payload) -> (status_name, detail)``: ``("OK", 0)`` when the command is accepted. The
simulator calls it first and executes only on OK, so a NACK has no side effect by construction.
``SimCheckState`` has exactly the fields of ``check_vectors.json`` ``state_schema`` 2 and is built from a vector
with ``from_vector`` (an unknown key or a newer schema fails). Since ICD v0.5 (D-36 / CR-01) the keys
``stop_btn_active`` / ``stop_btn_released_ms`` are kept (keys are never removed, F-B-25) but **ignored** — there is
no physical STOP button any more, so HALT_CLEAR is never refused.

Implements: FW-CMD-001 (check order, sim), FW-CFG-003 (SET_PARAM checks, sim), SAF-FW-020 (motion refusal,
sim), SYS-008
"""
from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any

from bend_stand.calc.motion import rate_cap_um_s
from bend_stand.calc.paramrules import set_violation
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg

STATE_SCHEMA = 4                       # ICD v0.7: + unhomed_origin_um (D-43 b); v0.7.5: + ena_on (D-50 c)
B = pg.Block
MOVING = ("MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD", "HOMING", "STOPPING")
MOTION = (pg.Cmd.MOVE_ABS, pg.Cmd.MOVE_UNTIL_LOAD, pg.Cmd.HOME)


@dataclass
class SimCheckState:
    """Acceptance-relevant FW state (ICD §6; ``check_vectors.json`` state keys, state_schema 4)."""

    params: dict[str, Any] = field(default_factory=dict)     # key → value (complete table in the simulator)
    motion_state: str = "IDLE"
    enabling_left_ms: int = 0
    homed: bool = True
    pos_um: int = 100_000
    estop_latched: bool = False
    estop_input_open: bool = False
    estop_closed_ms: int = 100_000
    halt_latched: bool = False
    stop_btn_active: bool = False              # ignored since ICD v0.5 (D-36: no STOP button)
    stop_btn_released_ms: int = 100_000        # ignored since ICD v0.5
    faults: list[str] = field(default_factory=list)
    fault_causes: list[str] = field(default_factory=list)
    limit_start: bool = False
    limit_end: bool = False
    afe_stale: bool = False
    afe_saturated: bool = False
    raw: int = 0
    drv_power: bool = True
    alm_active: bool = False
    nvm_record_valid: bool = True
    paused: bool = False
    unhomed_origin_um: int = 0                 # D-43 b: position latched when the axis became un-homed (schema 3)
    ena_on: bool = True                        # D-50 c / ICD v0.7.5 (h): ENA output at the enabled level (schema 4)

    @classmethod
    def from_vector(cls, defaults: Mapping[str, Any], state: Mapping[str, Any], schema: int) -> SimCheckState:
        if schema != STATE_SCHEMA:
            raise ValueError(f"unsupported state_schema {schema} (implemented {STATE_SCHEMA})")
        known = {f.name for f in fields(cls)}
        merged = dict(defaults)
        merged.update(state)
        unknown = set(merged) - known
        if unknown:
            raise ValueError(f"unknown state keys {sorted(unknown)}")
        params = {p.key: p.default for p in pgen.PARAMS}
        params.update(defaults.get("params", {}))
        params.update(state.get("params", {}))
        merged["params"] = params
        merged["faults"] = list(merged.get("faults", []))
        merged["fault_causes"] = list(merged.get("fault_causes", []))
        return cls(**merged)

    def p(self, key: str) -> Any:
        v = self.params.get(key)
        return pgen.BY_KEY[key].default if v is None else v


# ---------------------------------------------------------------------------------------------- helpers

def loaded(st: SimCheckState) -> bool:
    """FW "loaded" predicate (ICD §5.4): ``abs(raw − zero_raw) ≥ release_band_raw`` or AFE stale."""
    return st.afe_stale or abs(int(st.raw) - int(st.p("safety.zero_raw"))) >= int(st.p("safety.release_band_raw"))


def v_limit(st: SimCheckState, *, load_cmd: bool = False, unhomed_jog: bool = False) -> int:
    v_max = int(st.p("motion.v_max_load_um_s") if (load_cmd or loaded(st)) else st.p("motion.v_max_travel_um_s"))
    v = min(v_max, rate_cap_um_s(int(st.p("motion.max_step_rate_hz")), float(st.p("motion.steps_per_mm"))))
    if unhomed_jog:
        v = min(v, int(st.p("motion.v_unhomed_um_s")))
    return v


def _soft(st: SimCheckState) -> tuple[int, int]:
    return int(st.p("limits.soft_min_um")), int(st.p("limits.soft_max_um"))


def _power_present(st: SimCheckState) -> bool:
    return st.drv_power or not bool(st.p("drv.pwr_sense_enable"))


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


def block_mask(st: SimCheckState, cmd: pg.Cmd, *, direction: int = 0, needs_home: bool = False,
               new_start: bool = True) -> int:
    """BLOCK mask of a motion command (ICD §4.3); ``direction`` = sign of the motion (LIMIT)."""
    m = 0
    if st.estop_latched or st.estop_input_open:
        m |= B.ESTOP
    if st.halt_latched:
        m |= B.HALT
    if st.faults:
        m |= B.FAULT
    if st.motion_state == "NOT_ENABLED":
        m |= B.NOT_ENABLED
    if needs_home and not st.homed:
        m |= B.NOT_HOMED
    if cmd != pg.Cmd.HOME and ((direction < 0 and st.limit_start) or (direction > 0 and st.limit_end)):
        m |= B.LIMIT
    if st.afe_stale:
        m |= B.AFE_STALE
    if st.afe_saturated:
        m |= B.AFE_SATURATED
    if not _power_present(st):
        m |= B.DRV_UNPOWERED
    elif st.alm_active and new_start:
        m |= B.DRIVER_ALARM
    if st.paused:
        m |= B.PAUSED
    return int(m)


def _range_param(meta: pgen.ParamMeta, ptype: int, raw: bytes) -> bool:
    """True when the 4 wire bytes are a valid value of ``meta`` (padding, bool, enum, NaN, range)."""
    size = pgen.TYPE_SIZE[meta.type]
    if any(raw[size:]):
        return False
    try:
        v = meta.unpack(raw)
    except ValueError:
        return False
    if meta.type == pgen.ParamType.BOOL:
        return raw[0] in (0, 1)
    if meta.type == pgen.ParamType.F32 and not math.isfinite(float(v)):
        return False
    return meta.in_range(v)


# ---------------------------------------------------------------------------------------------- check

def check(st: SimCheckState, ftype: int, payload: bytes) -> tuple[str, int]:
    """Verdict of ICD §4.4 steps 1–5 for one CRC-valid command frame."""
    # 1. TYPE
    try:
        cmd = pg.Cmd(ftype)
    except ValueError:
        return "E_UNKNOWN_CMD", ftype
    # 2. LEN
    if len(payload) != pg.CMD_REQ_LEN[cmd]:
        return "E_LENGTH", pg.CMD_REQ_LEN[cmd]
    moving = st.motion_state in MOVING
    C = pg.Cmd

    # ---- parameters / NVM / system --------------------------------------------------------------------
    if cmd == C.GET_PARAM:
        (pid,) = struct.unpack("<H", payload)
        return ("OK", 0) if pid in pgen.BY_ID else ("E_PARAM_ID", pid)
    if cmd == C.GET_ALL_PARAMS:
        pages = -(-pgen.PARAM_COUNT // pg.PARAMS_PER_PAGE)
        return ("OK", 0) if payload[0] < pages else ("E_RANGE", 0)
    if cmd == C.SET_PARAM:
        pid, ptype = struct.unpack_from("<HB", payload)
        raw = payload[3:7]
        meta = pgen.BY_ID.get(pid)
        if meta is None:
            return "E_PARAM_ID", pid
        if ptype != int(meta.type):
            return "E_TYPE", pid
        if not _range_param(meta, ptype, raw):
            return "E_RANGE", pid
        if moving and not meta.moving_ok:
            return "E_BUSY", int(pg.BusyDetail.MOTION)
        viol = set_violation(st.params, meta.key, meta.unpack(raw))
        if viol is not None:
            return "E_CONFIG", viol.other_id
        return "OK", 0
    if cmd == C.REBOOT:
        if struct.unpack("<I", payload)[0] != pg.REBOOT_MAGIC:
            return "E_RANGE", 0
        return ("E_BUSY", int(pg.BusyDetail.MOTION)) if moving else ("OK", 0)
    if cmd in (C.SAVE_PARAMS, C.DEFAULT_PARAMS):
        return ("E_BUSY", int(pg.BusyDetail.MOTION)) if moving else ("OK", 0)
    if cmd == C.LOAD_PARAMS:
        if moving:
            return "E_BUSY", int(pg.BusyDetail.MOTION)
        return ("OK", 0) if st.nvm_record_valid else ("E_NVM", int(pg.NvmDetail.NO_RECORD))
    if cmd in (C.PING, C.GET_INFO, C.GET_STATUS, C.STREAM_START, C.STREAM_STOP):
        return "OK", 0
    if cmd == C.SET_VALID:
        return ("OK", 0) if payload[0] <= 1 else ("E_RANGE", 0)
    if cmd == C.DIAG_MEAS:          # ICD v0.6 App. C: only HW_MEAS builds execute it (the simulator is no such build)
        return "E_INTERNAL", int(pg.InternalDetail.NOT_IN_BUILD)

    # ---- stops, pause, clears ---------------------------------------------------------------------------
    if cmd == C.STOP:
        return ("OK", 0) if payload[0] <= 1 else ("E_RANGE", 0)
    if cmd in (C.HALT, C.PAUSE):
        return "OK", 0
    if cmd == C.HALT_CLEAR:
        return "OK", 0                                       # never refused since ICD v0.5 (D-36, no cause input)
    if cmd == C.ESTOP_CLEAR:
        if st.estop_latched or st.estop_input_open:          # an open input always means ESTOP (ICD v0.5 §4.3)
            if st.estop_input_open:
                return "E_CAUSE_ACTIVE", pg.DETAIL_CAUSE_INPUT
            need = int(st.p("io.estop_release_ms"))
            if st.estop_closed_ms < need:
                return "E_CAUSE_ACTIVE", need - int(st.estop_closed_ms)
        return "OK", 0
    if cmd == C.FAULT_CLEAR:
        active = [f for f in st.faults if f in st.fault_causes and f != "LOAD_LIMIT"]
        if active:
            return "E_CAUSE_ACTIVE", sum(int(pg.Faults[f]) for f in active)
        return "OK", 0
    if cmd == C.RESUME:
        m = 0
        if st.estop_latched or st.estop_input_open:
            m |= B.ESTOP
        if st.halt_latched:
            m |= B.HALT
        if st.faults:
            m |= B.FAULT
        return ("E_STATE", int(m)) if m else ("OK", 0)

    # ---- enable / disable -------------------------------------------------------------------------------
    if cmd == C.ENABLE:
        m = 0
        if st.estop_latched or st.estop_input_open:
            m |= B.ESTOP
        if not _power_present(st):
            m |= B.DRV_UNPOWERED
        return ("E_STATE", int(m)) if m else ("OK", 0)
    if cmd == C.DISABLE:
        return ("E_BUSY", int(pg.BusyDetail.MOTION)) if moving else ("OK", 0)

    # ---- motion -----------------------------------------------------------------------------------------
    smin, smax = _soft(st)
    a_max = int(st.p("motion.a_max_um_s2"))
    if cmd == C.MOVE_ABS:
        target, v, a = struct.unpack("<iII", payload)
        if not smin <= target <= smax:
            return "E_RANGE", 0
        if v == 0 or v > v_limit(st):
            return "E_RANGE", 4
        if a > a_max:
            return "E_RANGE", 8
        mask = block_mask(st, cmd, direction=_sign(target - st.pos_um), needs_home=True)
        return _state_then_busy(st, mask, jog_refresh=False)
    if cmd == C.MOVE_UNTIL_LOAD:
        bound, v, a, raw_stop, cmp_ = struct.unpack("<iIIiB", payload)
        if not smin <= bound <= smax or bound == st.pos_um:
            return "E_RANGE", 0
        if v == 0 or v > v_limit(st, load_cmd=True):
            return "E_RANGE", 4
        if a > a_max:
            return "E_RANGE", 8
        if not pg.RAW_MIN <= raw_stop <= pg.RAW_MAX:
            return "E_RANGE", 12
        if cmp_ > 1:
            return "E_RANGE", 16
        mask = block_mask(st, cmd, direction=_sign(bound - st.pos_um), needs_home=True)
        return _state_then_busy(st, mask, jog_refresh=False)
    if cmd == C.HOME:
        flags = payload[0]
        if flags & 0xFE:
            return "E_RANGE", 0
        mask = block_mask(st, cmd)
        verdict = _state_then_busy(st, mask, jog_refresh=False)
        if verdict != ("OK", 0):
            return verdict
        over = abs(int(st.raw) - int(st.p("safety.zero_raw"))) > int(st.p("home.max_load_raw"))
        if over and not flags & pg.HomeFlags.LOAD_CONFIRMED:
            return "E_CONFIRM", 0
        return "OK", 0
    if cmd == C.JOG:
        v, a, bound = struct.unpack("<iIi", payload)
        if abs(v) > v_limit(st, unhomed_jog=not st.homed):
            return "E_RANGE", 0
        if v != 0 and not st.homed:              # D-43 b: un-homed window origin ± home.max_travel_um reached
            w = int(st.p("home.max_travel_um"))
            if (v > 0 and st.pos_um >= st.unhomed_origin_um + w) or (v < 0 and st.pos_um <= st.unhomed_origin_um - w):
                return "E_RANGE", 0
        if a > a_max:
            return "E_RANGE", 4
        if v == 0:
            return "OK", 0                                  # JOG 0: never refused by state (ICD §4.3)
        has_bound = bound != pg.JOG_NO_BOUND
        if has_bound:
            ahead = (bound - st.pos_um) * _sign(v) > 0
            if not (smin <= bound <= smax and ahead):
                return "E_RANGE", 8
        jogging = st.motion_state == "JOG"
        mask = block_mask(st, cmd, direction=_sign(v), needs_home=has_bound, new_start=not jogging)
        return _state_then_busy(st, mask, jog_refresh=jogging)
    return "E_INTERNAL", int(pg.InternalDetail.INVARIANT)  # pragma: no cover - every Cmd handled above


def _state_then_busy(st: SimCheckState, mask: int, *, jog_refresh: bool) -> tuple[str, int]:
    if mask:
        return "E_STATE", mask
    if st.motion_state == "ENABLING":
        return "E_BUSY", int(pg.BusyDetail.ENABLING)
    if st.motion_state in MOVING and not jog_refresh:
        return "E_BUSY", int(pg.BusyDetail.MOTION)
    return "OK", 0
