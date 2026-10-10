#!/usr/bin/env python3
"""Reference command-acceptance model (test oracle) for ICD_protocol.md v0.7 §4-§6.

Implements: FW-CMD-001 (check order), FW-CFG-003 (SET_PARAM checks), SAF-FW-020 (motion
gating), SAF-FW-006/-021/-022, FW-CMD-003, FW-MOT-004/-005/-008/-009 (acceptance only).

Given a snapshot of the FW state (FwState) and one CRC-valid command frame, `check()` returns
the response verdict of check-order steps 1..5: ("OK", 0) when the command is accepted, else
(STATUS name, detail). It models ACCEPTANCE only, never execution (no motion, no timing).
gen_vectors.py turns scenarios of this model into vectors/check_vectors.json; FW host tests and
the SW simulator replay those vectors. Pure Python; the parameter table comes from
gen_params.load() (PyYAML) or any object list with the same attributes.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any

import ref_codec as rc

MOVING_STATES = ("MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD", "HOMING", "STOPPING")
MOTION_CMDS = ("MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD", "HOME")
STATE_SCHEMA = 4        # check_vectors.json state_schema (F-B-25); 3 (v0.7): + unhomed_origin_um (D-43 b);
                        # 4 (v0.7.5, D-50 c / OI-FW-50): + ena_on (FWR-09)
SPS = {0: 10, 1: 80}    # afe.rate_sps enum code -> conversions per second (H5)


@dataclass
class FwState:
    """FW state snapshot relevant for command acceptance (ICD §6). Defaults = 'ready':
    enabled, homed, idle at 100 mm, unloaded, nothing latched, NVM record valid."""

    params: dict[str, Any] = field(default_factory=dict)   # key -> value overrides of defaults
    motion_state: str = "IDLE"
    enabling_left_ms: int = 0
    homed: bool = True
    pos_um: int = 100_000
    estop_latched: bool = False
    estop_input_open: bool = False
    estop_closed_ms: int = 100_000  # time the sense input has been closed continuously
    halt_latched: bool = False
    stop_btn_active: bool = False             # IGNORED since ICD v0.5 (D-36), never set by a vector (key kept)
    stop_btn_released_ms: int = 100_000       # IGNORED since ICD v0.5 (D-36), never set by a vector (key kept)
    faults: list[str] = field(default_factory=list)        # latched FAULT bits
    fault_causes: list[str] = field(default_factory=list)  # faults whose cause is still present
    limit_start: bool = False       # START limit input active or LIMIT_START latched
    limit_end: bool = False
    afe_stale: bool = False
    afe_saturated: bool = False     # last sample at a rail
    raw: int = 0                    # last HX711 sample
    drv_power: bool = True          # DRV_PWR sense input (evaluated if drv.pwr_sense_enable)
    alm_active: bool = False
    nvm_record_valid: bool = True
    paused: bool = False            # PAUSED latch: blocks new motion (BLOCK PAUSED, D-30)
    unhomed_origin_um: int = 0      # D-43 b: position latched when the axis became un-homed (schema 3)
    ena_on: bool = True             # schema 4 (D-50 c, FWR-09): ENA output at the enabled level (STATUS io
                                    # ENA_DISABLED = not ena_on); always true outside NOT_ENABLED; after a boot
                                    # NOT_ENABLED keeps it true (holding, D-13); false after DISABLE / E-stop

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FwState":
        return cls(**d)


class Model:
    def __init__(self, params: list[Any]) -> None:
        self.params = params                       # gen_params.Param objects (sorted by id)
        self.by_id = {p.id: p for p in params}
        self.by_key = {p.key: p for p in params}

    # ---- helpers ---------------------------------------------------------------------
    def value(self, st: FwState, key: str) -> Any:
        if key in st.params:
            return st.params[key]
        return self.by_key[key].default

    def v_limit(self, st: FwState, loaded: bool, homed_for_jog: bool | None = None) -> int:
        """Effective speed cap in um/s (ICD §5.4): min(v_max_*, step-rate cap[, v_unhomed])."""
        cap = self.value(st, "motion.v_max_load_um_s" if loaded else "motion.v_max_travel_um_s")
        spm = float(self.value(st, "motion.steps_per_mm"))
        rate_cap = math.floor(self.value(st, "motion.max_step_rate_hz") * 1000.0 / spm)
        v = min(cap, rate_cap)
        if homed_for_jog is False:
            v = min(v, self.value(st, "motion.v_unhomed_um_s"))
        return int(v)

    def loaded(self, st: FwState) -> bool:
        if st.afe_stale:
            return True                            # unknown load -> conservative
        return abs(st.raw - self.value(st, "safety.zero_raw")) >= self.value(st, "safety.release_band_raw")

    def block_mask(self, st: FwState, cmd: str, direction: int, jog_bound: bool = False) -> int:
        """ICD §4.3 BLOCK bits that refuse `cmd` (direction -1 / 0 / +1 in x)."""
        b: list[str] = []
        if st.estop_latched or st.estop_input_open:
            b.append("ESTOP")
        sense = bool(self.value(st, "drv.pwr_sense_enable"))
        unpowered = sense and not st.drv_power
        if cmd == "ENABLE":
            if unpowered:
                b.append("DRV_UNPOWERED")
            return rc.names_to_bits(b, rc.BLOCK)
        if st.halt_latched:
            b.append("HALT")
        if st.faults:
            b.append("FAULT")
        if st.motion_state == "NOT_ENABLED":
            b.append("NOT_ENABLED")
        if not st.homed and (cmd in ("MOVE_ABS", "MOVE_UNTIL_LOAD") or (cmd == "JOG" and jog_bound)):
            b.append("NOT_HOMED")
        if cmd != "HOME" and ((direction < 0 and st.limit_start) or (direction > 0 and st.limit_end)):
            b.append("LIMIT")
        if st.afe_stale:
            b.append("AFE_STALE")
        if st.afe_saturated:
            b.append("AFE_SATURATED")
        if unpowered:
            b.append("DRV_UNPOWERED")
        # D-28 / SAF-FW-026 ALM start-block: NEW motion refused while ALM is active and driver
        # power is present (sense disabled -> power assumed present); a speed refresh of a
        # running jog is not a new motion start and is not blocked by ALM
        jog_refresh = cmd == "JOG" and st.motion_state == "JOG"
        if st.alm_active and not unpowered and not jog_refresh:
            b.append("DRIVER_ALARM")
        # D-30: PAUSED refuses MOVE_ABS, MOVE_UNTIL_LOAD, HOME and JOG != 0 incl. jog refreshes
        # (JOG 0 never reaches this point)
        if st.paused:
            b.append("PAUSED")
        return rc.names_to_bits(b, rc.BLOCK)

    def hard_rule(self, st: FwState, key: str, value: Any) -> str | None:
        """ICD §11.4 hard rules H1..H5 with `key` := value; returns the OTHER key or None."""
        def v(k: str) -> Any:
            return value if k == key else self.value(st, k)
        if key in ("limits.soft_min_um", "limits.soft_max_um"):
            if not v("limits.soft_min_um") < v("limits.soft_max_um"):
                return "limits.soft_max_um" if key == "limits.soft_min_um" else "limits.soft_min_um"
        if key in ("safety.load_raw_min", "safety.load_raw_max"):
            if not v("safety.load_raw_min") < v("safety.load_raw_max"):
                return "safety.load_raw_max" if key == "safety.load_raw_min" else "safety.load_raw_min"
        h3 = ("motion.max_step_rate_hz", "motion.pulse_high_ns", "motion.pulse_low_min_ns")
        if key in h3:
            # integer form of 1e9 / rate >= high + low (exact, no float rounding)
            if 1_000_000_000 < v(h3[0]) * (v(h3[1]) + v(h3[2])):
                return h3[1] if key == h3[0] else h3[0]
        if key in ("motion.v_max_load_um_s", "motion.v_max_travel_um_s"):
            if not v("motion.v_max_load_um_s") <= v("motion.v_max_travel_um_s"):
                return ("motion.v_max_travel_um_s" if key == "motion.v_max_load_um_s"
                        else "motion.v_max_load_um_s")
        if key in ("afe.timeout_ms", "afe.rate_sps"):
            # H5 (D-33a): timeout >= 2 x conversion period, integer form timeout_ms * sps >= 2000
            if v("afe.timeout_ms") * SPS[int(v("afe.rate_sps"))] < 2000:
                return "afe.rate_sps" if key == "afe.timeout_ms" else "afe.timeout_ms"
        return None

    def validate_set(self, p: Any, tcode: int, wire: bytes) -> tuple[str, Any]:
        """Mirror of generated param_validate_set(): OK / E_TYPE / E_RANGE."""
        if tcode != p.code:
            return "E_TYPE", None
        size = p.size
        if any(wire[size:4]):
            return "E_RANGE", None
        fmt = rc.PTYPE[tcode][1]
        (val,) = struct.unpack(fmt, wire[:size])
        if p.type == "f32":
            if not math.isfinite(val) or not p.min <= val <= p.max:
                return "E_RANGE", None
            return "OK", val
        if p.type == "enum":
            return ("OK", val) if val in {e.value for e in p.enum} else ("E_RANGE", None)
        return ("OK", val) if p.min <= val <= p.max else ("E_RANGE", None)

    @staticmethod
    def paused_after(st: FwState, ftype: int, payload: bytes, verdict: str) -> bool:
        """PAUSED after the command (ICD §5.5, D-30, D-31): set by an accepted PAUSE; cleared ONLY
        by an accepted RESUME or HALT_CLEAR; a NACK or any other command leaves it unchanged (motion
        commands are refused while PAUSED). Execution effect; PAUSE while moving also stops."""
        del payload
        if verdict != "OK" or ftype not in rc.CMD_NAME:
            return st.paused
        name = rc.CMD_NAME[ftype]
        if name == "PAUSE":
            return True
        if name in ("HALT_CLEAR", "RESUME"):
            return False
        return st.paused

    # ---- main -------------------------------------------------------------------------
    def check(self, st: FwState, ftype: int, payload: bytes, hw_meas: bool = False) -> tuple[str, int]:
        """hw_meas: the build has FEAT_HW_MEAS (measurement build, D-40c); release / twin = False."""
        # (1) TYPE
        if ftype not in rc.CMD_NAME:
            return "E_UNKNOWN_CMD", ftype
        name = rc.CMD_NAME[ftype]
        # (2) LEN
        if len(payload) != rc.REQ_LEN[name]:
            return "E_LENGTH", rc.REQ_LEN[name]
        req = rc.decode_request(name, payload) if name != "SET_PARAM" else {}
        moving = st.motion_state in MOVING_STATES
        # (3) arguments, (4) state, (5) execution — per command
        if name == "REBOOT":
            if req["magic"] != rc.REBOOT_MAGIC:
                return "E_RANGE", 0
            return ("E_BUSY", 1) if moving else ("OK", 0)
        if name == "GET_ALL_PARAMS":
            pages = math.ceil(len(self.params) / rc.PARAMS_PER_PAGE)
            return ("E_RANGE", 0) if req["page"] >= pages else ("OK", 0)
        if name == "GET_PARAM":
            return ("OK", 0) if req["id"] in self.by_id else ("E_PARAM_ID", req["id"])
        if name == "SET_PARAM":
            pid, tcode, wire = struct.unpack(rc.PARAM_ENTRY_FMT, payload)
            p = self.by_id.get(pid)
            if p is None:
                return "E_PARAM_ID", pid
            verdict, val = self.validate_set(p, tcode, wire)
            if verdict != "OK":
                return verdict, pid
            if moving and not p.moving_ok:
                return "E_BUSY", 1
            other = self.hard_rule(st, p.key, val)
            if other is not None:
                return "E_CONFIG", self.by_key[other].id
            return "OK", 0
        if name in ("SAVE_PARAMS", "LOAD_PARAMS", "DEFAULT_PARAMS"):
            if moving:
                return "E_BUSY", 1
            if name == "LOAD_PARAMS" and not st.nvm_record_valid:
                return "E_NVM", 1
            return "OK", 0
        if name in ("SET_VALID", "STOP"):
            key = {"SET_VALID": "valid", "STOP": "mode"}[name]
            return ("E_RANGE", 0) if req[key] > 1 else ("OK", 0)
        if name == "ENABLE":
            mask = self.block_mask(st, "ENABLE", 0)
            return ("E_STATE", mask) if mask else ("OK", 0)
        if name == "DISABLE":
            return ("E_BUSY", 1) if moving else ("OK", 0)
        if name == "ESTOP_CLEAR":
            # an open sense input always means ESTOP (§4.3): E_CAUSE_ACTIVE 0xFFFF whether or not
            # the latch is (already) set; the unlatched + open state is not reachable in the FW
            if st.estop_latched or st.estop_input_open:
                if st.estop_input_open:
                    return "E_CAUSE_ACTIVE", 0xFFFF
                need = self.value(st, "io.estop_release_ms")
                if st.estop_closed_ms < need:
                    return "E_CAUSE_ACTIVE", need - st.estop_closed_ms
            return "OK", 0
        if name == "HALT_CLEAR":
            return "OK", 0      # D-36 (v0.5): no STOP button -> HALT_CLEAR is never refused
        if name == "DIAG_MEAS":
            return self._check_meas(st, req, moving, hw_meas)
        if name == "RESUME":
            # D-31: clears only PAUSED; refused while ESTOP (latched or input open), HALT or any
            # FAULT is latched; no other BLOCK bit is evaluated; never E_BUSY (no motion start)
            b = []
            if st.estop_latched or st.estop_input_open:
                b.append("ESTOP")
            if st.halt_latched:
                b.append("HALT")
            if st.faults:
                b.append("FAULT")
            mask = rc.names_to_bits(b, rc.BLOCK)
            return ("E_STATE", mask) if mask else ("OK", 0)
        if name == "FAULT_CLEAR":
            remaining = [f for f in st.faults if f in st.fault_causes and f != "LOAD_LIMIT"]
            if remaining:
                return "E_CAUSE_ACTIVE", rc.names_to_bits(remaining, rc.FAULTS)
            return "OK", 0
        if name in MOTION_CMDS:
            return self._check_motion(st, name, req, payload, moving)
        return "OK", 0   # PING, GET_INFO, GET_STATUS, STREAM_*, HALT

    @staticmethod
    def _check_meas(st: FwState, r: dict[str, Any], moving: bool, hw_meas: bool) -> tuple[str, int]:
        """DIAG_MEAS (ICD Appendix C, D-40c): not in build -> E_INTERNAL NOT_IN_BUILD (after LEN); then op /
        sel / a / b in payload order (offsets 0 / 1 / 2 / 4); then MEAS_STATE for HANG / STATIC_LEVEL."""
        if not hw_meas:
            return "E_INTERNAL", 1
        op, sel, a, b = r["op"], r["sel"], r["a"], r["b"]
        if op > 9:
            return "E_RANGE", 0
        name = rc.MEAS_OP[op]
        # (sel ok, a ok, b ok) per op
        rules = {
            "INFO": (sel == 0, a == 0, b == 0),
            "PROBE_ARM": (sel <= 8, (a & 0x3) <= 2 and (a & ~0x0103) == 0, b <= 0xFFFF),
            "PROBE_READ": (sel == 0, a == 0, b == 0),
            "COUNTER": (sel <= 1, a == 0, b == 0),
            "STAMPS": (sel <= 3, a <= 1023, b == 0),
            "NOINIT": (sel <= 1, a == 0, b == 0),
            "STIM_RUN": ((sel >> 1) >= 1, 1 <= a <= 1000, True),
            "HANG": (sel <= 2, a <= 10000, b == 0),
            "STATIC_LEVEL": (sel <= 1, a <= 1, b == 0),
            "DWT": (sel <= 1, a <= 31, b == 0),
        }[name]
        for ok, off in zip(rules, (1, 2, 4)):
            if not ok:
                return "E_RANGE", off
        meas_state = rc.names_to_bits(["MEAS_STATE"], rc.BLOCK)
        if name == "HANG" and not moving:
            return "E_STATE", meas_state
        if name == "STATIC_LEVEL" and st.motion_state != "NOT_ENABLED":
            return "E_STATE", meas_state
        if name == "STATIC_LEVEL" and sel == 0 and st.ena_on:     # FWR-09 (ICD v0.7.5 App. C op 8): PUL only with
            return "E_STATE", meas_state                          # ENA at the disabled level
        return "OK", 0

    def _check_motion(self, st: FwState, name: str, req: dict[str, Any], payload: bytes,
                      moving: bool) -> tuple[str, int]:
        lo, hi = self.value(st, "limits.soft_min_um"), self.value(st, "limits.soft_max_um")
        a_max = self.value(st, "motion.a_max_um_s2")
        direction = 0
        # (3) arguments
        if name == "MOVE_ABS":
            if not lo <= req["target_um"] <= hi:
                return "E_RANGE", 0
            if not 1 <= req["v_um_s"] <= self.v_limit(st, self.loaded(st)):
                return "E_RANGE", 4
            if req["a_um_s2"] > a_max:
                return "E_RANGE", 8
            direction = (req["target_um"] > st.pos_um) - (req["target_um"] < st.pos_um)
        elif name == "MOVE_UNTIL_LOAD":
            if not lo <= req["bound_um"] <= hi or req["bound_um"] == st.pos_um:
                return "E_RANGE", 0                 # outside soft limits, or = current position (F-B-28)
            if not 1 <= req["v_um_s"] <= self.v_limit(st, True):
                return "E_RANGE", 4
            if req["a_um_s2"] > a_max:
                return "E_RANGE", 8
            if not rc.RAW_MIN <= req["raw_stop"] <= rc.RAW_MAX:
                return "E_RANGE", 12
            if req["cmp"] > 1:
                return "E_RANGE", 16
            direction = (req["bound_um"] > st.pos_um) - (req["bound_um"] < st.pos_um)
        elif name == "JOG":
            v = req["v_um_s"]
            if abs(v) > self.v_limit(st, self.loaded(st), homed_for_jog=st.homed):
                return "E_RANGE", 0
            if v != 0 and not st.homed:
                # D-43 b: un-homed travel window origin ± home.max_travel_um; a JOG toward a reached bound is
                # refused until homed (offset 0 = the v field gives the direction)
                w = self.value(st, "home.max_travel_um")
                if (v > 0 and st.pos_um >= st.unhomed_origin_um + w) or (v < 0 and st.pos_um <= st.unhomed_origin_um - w):
                    return "E_RANGE", 0
            if req["a_um_s2"] > a_max:
                return "E_RANGE", 4
            if v == 0:
                return "OK", 0                      # JOG 0: stop a jog / no-op, never refused
            direction = 1 if v > 0 else -1
            bound = req["bound_um"]
            if bound != rc.JOG_NO_BOUND:
                if not lo <= bound <= hi or (bound - st.pos_um) * direction <= 0:
                    return "E_RANGE", 8             # outside soft limits or not ahead of the axis
        elif name == "HOME":
            if req["flags"] & 0xFE:
                return "E_RANGE", 0
        # (4) state: E_STATE -> E_BUSY -> E_CONFIRM
        mask = self.block_mask(st, name, direction,
                               jog_bound=name == "JOG" and req["bound_um"] != rc.JOG_NO_BOUND)
        if mask:
            return "E_STATE", mask
        if st.motion_state == "ENABLING":
            return "E_BUSY", 2
        if moving:
            if name == "JOG" and st.motion_state == "JOG":
                return "OK", 0                      # speed change on the fly + dead-man refresh
            return "E_BUSY", 1
        if name == "HOME" and not req["flags"] & 1:
            if abs(st.raw - self.value(st, "safety.zero_raw")) > self.value(st, "home.max_load_raw"):
                return "E_CONFIRM", 0
        return "OK", 0
