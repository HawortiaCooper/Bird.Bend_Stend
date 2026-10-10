"""GENERATED - do not edit.

Source : 00_System/specs/protocol.yaml (ICD_protocol.md v0.7.5, PROTO 1.0, PAYLOAD 1)
Tool   : 00_System/tools/gen_protocol.py (run via gen_params.py)

Names and codes of commands, NACK codes, flag/status/FAULT/IO/BLOCK bits, EVENT codes and
their argument enums, for the codec, the indicator registry and the GUI channel tree
(GF-08). <ID>_BITS lists bit names by bit index ('' = reserved); <ID>_DESC maps a name to
its ICD description. Pure Python, no Qt, no PyYAML.

Implements: IF-010, IF-012, FW-STR-003, FW-STR-006
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum, IntFlag
from types import MappingProxyType

ICD_VERSION = '0.7.5'
PROTO_MAJOR = 1
PROTO_MINOR = 0
PAYLOAD_VERSION = 1

SYNC0 = 0xA5  # first sync byte (D-05 separator)
SYNC1 = 0x5A  # second sync byte
HEADER_LEN = 6  # SYNC0 SYNC1 TYPE SEQ LEN_lo LEN_hi
FRAME_OVERHEAD = 8  # header + CRC-16
MAX_LEN = 160  # maximum payload length (frame <= 168 B)
RX_BUF_MIN = 336  # minimum receiver buffer (2 maximum frames, ICD §2.3)
INTERBYTE_TIMEOUT_MS = 20  # receiver inter-byte timeout
CRC_INIT = 0xFFFF  # CRC-16/CCITT-FALSE init (poly 0x1021)
CRC_POLY = 0x1021  # CRC-16/CCITT-FALSE polynomial
RESP_BIT = 0x80  # response TYPE = command TYPE | RESP_BIT
NACK_LEN = 3  # NACK payload: u8 status, u16 detail
INFO_LEN = 44  # INFO body (GET_INFO OK response after the STATUS byte)
STATUS_LEN = 86  # STATUS body (GET_STATUS OK response after the STATUS byte)
DATA_LEN = 18  # DATA payload, PAYLOAD_VERSION 1
EVENT_LEN = 16  # EVENT payload
PARAM_ENTRY_LEN = 7  # u16 id, u8 type, u8[4] value
PARAMS_PER_PAGE = 20  # PARAM_ENTRYs per GET_ALL_PARAMS page
REBOOT_MAGIC = 0xB007B007  # REBOOT request magic
JOG_NO_BOUND = -2147483648  # JOG bound_um = 0x80000000: no bound
AFE_NO_DATA = -2147483648  # afe_raw / afe_raw_last = 0x80000000: no AFE sample
RAW_MIN = -8388608  # HX711 negative rail (saturated)
RAW_MAX = 8388607  # HX711 positive rail (saturated)
DETAIL_CAUSE_INPUT = 0xFFFF  # E_CAUSE_ACTIVE detail of ESTOP_CLEAR / HALT_CLEAR: input still active
TWIN_TCP_PORT = 5760  # FW host twin serial-over-TCP port (127.0.0.1)
TWIN_CTL_PORT = 5761  # FW host twin world-control port (JSON lines, tools/README)
HOME_RELEASE_MAX_UM = 0x2710  # HOME: START not released within this travel in RELEASE / BACKOFF -> HOME_WIRING (ICD §5.4, OI-FW-23)
MEAS_BODY_LEN = 64  # DIAG_MEAS OK body: u32 w[16] (ICD Appendix C)
MEAS_STAMPS_PER_PAGE = 14  # DIAG_MEAS STAMPS: stamps per page (w2..w15)
MEAS_MAGIC = 0x4D454153  # DIAG_MEAS NOINIT w0 when the .noinit block is valid ('MEAS')
HOME_SLOW_EXTRA_UM = 0x2710  # HOME: no START edge within home.backoff_um + this travel in SLOW_APPROACH -> HOME_NOT_FOUND (ICD §5.4, OI-FW-23)


class AsyncType(IntEnum):
    """Asynchronous frame TYPEs (FW → PC) (ICD §3.1)."""

    DATA = 0xC0
    EVENT = 0xC1


ASYNC_TYPE_NAMES: tuple[str, ...] = ('DATA', 'EVENT')
ASYNC_TYPE_DESC: Mapping[str, str] = MappingProxyType({'DATA': 'DATA frame (§7.3), one per HX711 conversion while the stream is on', 'EVENT': 'EVENT frame (§7.4), sent regardless of the stream state'})
ASYNC_TYPE_RETIRED: frozenset[str] = frozenset()


class Status(IntEnum):
    """Response STATUS codes (ICD §4.2)."""

    OK = 0
    E_UNKNOWN_CMD = 1
    E_LENGTH = 2
    E_PARAM_ID = 3
    E_TYPE = 4
    E_RANGE = 5
    E_CONFIG = 6
    E_BUSY = 7
    E_STATE = 8
    E_CAUSE_ACTIVE = 9
    E_CONFIRM = 10
    E_NVM = 11
    E_INTERNAL = 12


STATUS_CODE_NAMES: tuple[str, ...] = ('OK', 'E_UNKNOWN_CMD', 'E_LENGTH', 'E_PARAM_ID', 'E_TYPE', 'E_RANGE', 'E_CONFIG', 'E_BUSY', 'E_STATE', 'E_CAUSE_ACTIVE', 'E_CONFIRM', 'E_NVM', 'E_INTERNAL')
STATUS_CODE_DESC: Mapping[str, str] = MappingProxyType({'OK': 'accepted / executed', 'E_UNKNOWN_CMD': 'TYPE in 0x01..0x3F not defined', 'E_LENGTH': "LEN ≠ the command's fixed LEN", 'E_PARAM_ID': 'unknown parameter id', 'E_TYPE': "PARAM_ENTRY type byte ≠ the parameter's type", 'E_RANGE': 'argument out of range (never clamped)', 'E_CONFIG': 'SET_PARAM violates a hard rule (§11.4)', 'E_BUSY': 'not possible now, retry later', 'E_STATE': 'motion / enable / RESUME refused in the current state', 'E_CAUSE_ACTIVE': 'clear refused, cause still present', 'E_CONFIRM': 'HOME with load above home.max_load_raw without the confirmed flag', 'E_NVM': 'NVM failure', 'E_INTERNAL': 'implementation error or command not in this build; nothing executed'})
STATUS_CODE_RETIRED: frozenset[str] = frozenset()
STATUS_CODE_DETAIL: Mapping[str, str] = MappingProxyType({'OK': '(none)', 'E_UNKNOWN_CMD': 'the TYPE', 'E_LENGTH': 'the expected LEN', 'E_PARAM_ID': 'the requested id', 'E_TYPE': 'the parameter id', 'E_RANGE': 'SET_PARAM: parameter id; other commands: byte offset of the offending field in the request payload', 'E_CONFIG': 'id of the other parameter of the rule', 'E_BUSY': 'busy_detail (Appendix B): 1 = MOTION (moving, homing or stopping), 2 = ENABLING (ENA settle running)', 'E_STATE': 'BLOCK mask (§4.3): all blocking conditions evaluated for that command', 'E_CAUSE_ACTIVE': 'ESTOP_CLEAR: 0xFFFF = sense input open (latched or not), else ms still missing until io.estop_release_ms; HALT_CLEAR: never refused since v0.5 (D-36); FAULT_CLEAR: FAULT mask (§7.6) of the latched faults whose cause is still present', 'E_CONFIRM': '0', 'E_NVM': 'nvm_detail (Appendix B): 1 = no valid record (LOAD), 2 = erase/program error, 3 = verify error', 'E_INTERNAL': 'internal_detail (Appendix B): 1 = NOT_IN_BUILD, 2 = INVARIANT; other values implementation-defined'})


class BusyDetail(IntEnum):
    """E_BUSY detail (ICD §4.2)."""

    MOTION = 1
    ENABLING = 2


BUSY_DETAIL_NAMES: tuple[str, ...] = ('MOTION', 'ENABLING')
BUSY_DETAIL_DESC: Mapping[str, str] = MappingProxyType({'MOTION': 'a motion is running (motion state MOVE_ABS, JOG, MOVE_UNTIL_LOAD, HOMING or STOPPING)', 'ENABLING': 'ENA settle (motion.ena_settle_ms) running'})
BUSY_DETAIL_RETIRED: frozenset[str] = frozenset()


class InternalDetail(IntEnum):
    """E_INTERNAL detail (ICD §4.2)."""

    NOT_IN_BUILD = 1
    INVARIANT = 2


INTERNAL_DETAIL_NAMES: tuple[str, ...] = ('NOT_IN_BUILD', 'INVARIANT')
INTERNAL_DETAIL_DESC: Mapping[str, str] = MappingProxyType({'NOT_IN_BUILD': 'command defined in the ICD but implemented in a later milestone (its feature bit is 0); nothing executed', 'INVARIANT': 'internal consistency check failed; nothing executed'})
INTERNAL_DETAIL_RETIRED: frozenset[str] = frozenset()


class NvmDetail(IntEnum):
    """E_NVM detail / NVM_ERROR arg (ICD §4.2)."""

    NO_RECORD = 1
    ERASE_PROGRAM = 2
    VERIFY = 3


NVM_DETAIL_NAMES: tuple[str, ...] = ('NO_RECORD', 'ERASE_PROGRAM', 'VERIFY')
NVM_DETAIL_DESC: Mapping[str, str] = MappingProxyType({'NO_RECORD': 'no valid NVM record (LOAD_PARAMS), or a record that violates a hard rule', 'ERASE_PROGRAM': 'flash erase or program error', 'VERIFY': 'read-back verify error'})
NVM_DETAIL_RETIRED: frozenset[str] = frozenset()


class Block(IntFlag):
    """BLOCK mask (detail of E_STATE) (ICD §4.3)."""

    ESTOP = 0x0001
    HALT = 0x0002
    FAULT = 0x0004
    NOT_ENABLED = 0x0008
    NOT_HOMED = 0x0010
    LIMIT = 0x0020
    AFE_STALE = 0x0040
    AFE_SATURATED = 0x0080
    DRV_UNPOWERED = 0x0100
    DRIVER_ALARM = 0x0200
    PAUSED = 0x0400
    MEAS_STATE = 0x0800


BLOCK_BITS: tuple[str, ...] = ('ESTOP', 'HALT', 'FAULT', 'NOT_ENABLED', 'NOT_HOMED', 'LIMIT', 'AFE_STALE', 'AFE_SATURATED', 'DRV_UNPOWERED', 'DRIVER_ALARM', 'PAUSED', 'MEAS_STATE')
BLOCK_DESC: Mapping[str, str] = MappingProxyType({'ESTOP': 'ESTOP latched or E-stop sense input open (also evaluated by RESUME)', 'HALT': 'HALT latched (PC: HALT command / Pause-Break key) (also evaluated by RESUME)', 'FAULT': 'any FAULT latched (§7.6) (also evaluated by RESUME)', 'NOT_ENABLED': 'motion state NOT_ENABLED (ENABLE never done, or DISABLE / E-stop / idle disable / driver power loss since)', 'NOT_HOMED': 'MOVE_ABS, MOVE_UNTIL_LOAD or JOG with a bound while not homed', 'LIMIT': 'motion toward an active or latched limit switch (direction = sign(target − x) or sign(v)); only motion away is accepted while latched (D-33h)', 'AFE_STALE': 'no HX711 sample for afe.timeout_ms', 'AFE_SATURATED': 'last HX711 sample at a rail', 'DRV_UNPOWERED': "only with the optional power sense (drv.pwr_sense_enable, default 0 since CR-03 / D-41): the DRV_POWER input reads 'off' (D-28, D-29c)", 'DRIVER_ALARM': 'ALM start-block (SAF-FW-026, D-28): ALM active and driver power present (sense disabled → assumed present); new motion starts only (MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0 while not jogging)', 'PAUSED': 'PAUSED latched (D-30): MOVE_ABS, MOVE_UNTIL_LOAD, HOME and JOG ≠ 0 (incl. refreshes of a running jog) refused; cleared by RESUME (clears only PAUSED) or HALT_CLEAR (clears HALT and PAUSED) (D-31)', 'MEAS_STATE': 'DIAG_MEAS op not allowed in the current motion state: HANG needs a running motion, STATIC_LEVEL needs NOT_ENABLED and, for sel PUL, the ENA output at the disabled level (Appendix C, D-40c, FWR-09)'})
BLOCK_RETIRED: frozenset[str] = frozenset()


class StopMode(IntEnum):
    """STOP mode (ICD §5.5)."""

    IMMEDIATE = 0
    CONTROLLED = 1


STOP_MODE_NAMES: tuple[str, ...] = ('IMMEDIATE', 'CONTROLLED')
STOP_MODE_DESC: Mapping[str, str] = MappingProxyType({'IMMEDIATE': 'no further PUL edge ≤ 2 ms after the last command byte (SAF-FW-002)', 'CONTROLLED': 'planned deceleration at motion.a_stop_um_s2 (clean halt only at step period > 2 ms and planned stop distance <= 1 step, §6.5)'})
STOP_MODE_RETIRED: frozenset[str] = frozenset()


class MulCmp(IntEnum):
    """MOVE_UNTIL_LOAD cmp (ICD §5.4)."""

    GE = 0
    LE = 1


MUL_CMP_NAMES: tuple[str, ...] = ('GE', 'LE')
MUL_CMP_DESC: Mapping[str, str] = MappingProxyType({'GE': 'stop when raw ≥ raw_stop', 'LE': 'stop when raw ≤ raw_stop'})
MUL_CMP_RETIRED: frozenset[str] = frozenset()


class HomeFlags(IntFlag):
    """HOME flags (ICD §5.4)."""

    LOAD_CONFIRMED = 0x01


HOME_FLAGS_BITS: tuple[str, ...] = ('LOAD_CONFIRMED',)
HOME_FLAGS_DESC: Mapping[str, str] = MappingProxyType({'LOAD_CONFIRMED': 'operator confirmed homing with load above home.max_load_raw (SAF-FW-021)'})
HOME_FLAGS_RETIRED: frozenset[str] = frozenset()


class MotionState(IntEnum):
    """Motion state (STATUS motion_state) (ICD §6.1)."""

    NOT_ENABLED = 0
    ENABLING = 1
    IDLE = 2
    MOVE_ABS = 3
    JOG = 4
    MOVE_UNTIL_LOAD = 5
    HOMING = 6
    STOPPING = 7


MOTION_STATE_NAMES: tuple[str, ...] = ('NOT_ENABLED', 'ENABLING', 'IDLE', 'MOVE_ABS', 'JOG', 'MOVE_UNTIL_LOAD', 'HOMING', 'STOPPING')
MOTION_STATE_DESC: Mapping[str, str] = MappingProxyType({'NOT_ENABLED': 'after boot, DISABLE, E-stop, idle disable or driver power loss; no pulse possible (DATA ENABLED = 0)', 'ENABLING': 'ENA asserted, motion.ena_settle_ms running (ENABLED = 0)', 'IDLE': 'enabled, standing (holding) (ENABLED = 1, MOVING = 0)', 'MOVE_ABS': 'executing MOVE_ABS (MOVING = 1)', 'JOG': 'executing JOG (MOVING = 1)', 'MOVE_UNTIL_LOAD': 'executing MOVE_UNTIL_LOAD (MOVING = 1)', 'HOMING': 'executing HOME, see home_phase (MOVING = 1)', 'STOPPING': 'controlled deceleration in progress (MOVING = 1)'})
MOTION_STATE_RETIRED: frozenset[str] = frozenset()


class HomePhase(IntEnum):
    """Homing phase (STATUS home_phase) (ICD §7.2)."""

    NONE = 0
    PRECHECK = 1
    RELEASE = 2
    FAST_SEEK = 3
    BACKOFF = 4
    SLOW_APPROACH = 5
    MOVE_TO_ZERO = 6
    DONE = 7


HOME_PHASE_NAMES: tuple[str, ...] = ('NONE', 'PRECHECK', 'RELEASE', 'FAST_SEEK', 'BACKOFF', 'SLOW_APPROACH', 'MOVE_TO_ZERO', 'DONE')
HOME_PHASE_DESC: Mapping[str, str] = MappingProxyType({'NONE': 'no homing since boot', 'PRECHECK': 'load pre-check', 'RELEASE': 'moving off an active START switch (+x)', 'FAST_SEEK': 'fast seek toward START (−x) at home.v_fast_um_s', 'BACKOFF': 'back-off home.backoff_um (+x)', 'SLOW_APPROACH': 'slow approach toward START at home.v_slow_um_s, edge capture', 'MOVE_TO_ZERO': 'move to x = 0', 'DONE': 'last homing completed or failed'})
HOME_PHASE_RETIRED: frozenset[str] = frozenset()


class Source(IntEnum):
    """Latch source (STATUS halt_src / pause_src, EVENT HALT_SET / PAUSED arg) (ICD §7.2)."""

    NONE = 0
    PC = 1
    BUTTON = 2


SOURCE_NAMES: tuple[str, ...] = ('NONE', 'PC', 'BUTTON')
SOURCE_DESC: Mapping[str, str] = MappingProxyType({'NONE': 'not latched', 'PC': 'PC command (HALT, PAUSE)', 'BUTTON': 'physical PAUSE button (pause_src / PAUSED only; halt_src is never BUTTON since v0.5, D-36)'})
SOURCE_RETIRED: frozenset[str] = frozenset()


class ResetCause(IntEnum):
    """Reset cause (STATUS reset_cause, EVENT BOOT arg) (ICD §7.2)."""

    UNKNOWN = 0
    POWER_ON = 1
    PIN = 2
    SOFTWARE = 3
    IWDG = 4
    WWDG = 5
    LOW_POWER = 6
    BROWN_OUT = 7


RESET_CAUSE_NAMES: tuple[str, ...] = ('UNKNOWN', 'POWER_ON', 'PIN', 'SOFTWARE', 'IWDG', 'WWDG', 'LOW_POWER', 'BROWN_OUT')
RESET_CAUSE_DESC: Mapping[str, str] = MappingProxyType({'UNKNOWN': 'no flag recognised', 'POWER_ON': 'power-on / POR', 'PIN': 'NRST pin', 'SOFTWARE': 'software reset (REBOOT)', 'IWDG': 'independent watchdog', 'WWDG': 'window watchdog', 'LOW_POWER': 'low-power reset', 'BROWN_OUT': 'brown-out reset'})
RESET_CAUSE_RETIRED: frozenset[str] = frozenset()


class SysFlags(IntFlag):
    """STATUS sys_flags (ICD §7.2)."""

    CLK_FALLBACK = 0x01
    CFG_DIRTY = 0x02
    STREAM_ON = 0x04
    REBOOT_PENDING = 0x08
    NVM_DEFAULTED = 0x10


SYS_FLAGS_BITS: tuple[str, ...] = ('CLK_FALLBACK', 'CFG_DIRTY', 'STREAM_ON', 'REBOOT_PENDING', 'NVM_DEFAULTED')
SYS_FLAGS_DESC: Mapping[str, str] = MappingProxyType({'CLK_FALLBACK': 'running on HSI fallback clock (FW-PLT-002); timing ±1 %', 'CFG_DIRTY': 'RAM parameters differ from the NVM record (§11.2)', 'STREAM_ON': 'DATA stream on', 'REBOOT_PENDING': 'a reboot_required parameter was changed (effective after SAVE_PARAMS + REBOOT)', 'NVM_DEFAULTED': 'defaults after boot/LOAD rules 2/4/5, until the next SAVE or clean LOAD'})
SYS_FLAGS_RETIRED: frozenset[str] = frozenset()


class Features(IntFlag):
    """INFO feature_mask (ICD §7.1)."""

    AFE = 0x00000001
    AFE_SYNTHETIC = 0x00000002
    MOTION = 0x00000004
    HOMING = 0x00000008
    MOVE_UNTIL_LOAD = 0x00000010
    NVM = 0x00000020
    TWIN = 0x00000040
    BUTTONS = 0x00000080
    DRV_SIGNALS = 0x00000100
    HW_MEAS = 0x00000200


FEATURES_BITS: tuple[str, ...] = ('AFE', 'AFE_SYNTHETIC', 'MOTION', 'HOMING', 'MOVE_UNTIL_LOAD', 'NVM', 'TWIN', 'BUTTONS', 'DRV_SIGNALS', 'HW_MEAS')
FEATURES_DESC: Mapping[str, str] = MappingProxyType({'AFE': 'real HX711', 'AFE_SYNTHETIC': 'M1 placeholder samples', 'MOTION': 'step generation', 'HOMING': 'HOME command', 'MOVE_UNTIL_LOAD': 'MOVE_UNTIL_LOAD command', 'NVM': 'SAVE/LOAD_PARAMS', 'TWIN': 'host twin build', 'BUTTONS': 'PAUSE button input (the STOP/BREAK input is retired, D-36)', 'DRV_SIGNALS': 'ALM/PEND/DRV_POWER inputs', 'HW_MEAS': 'measurement build (HW_MEAS, CR-02 / D-40c): DIAG_MEAS 0x3D executes; 0 = release / twin -> E_INTERNAL NOT_IN_BUILD'})
FEATURES_RETIRED: frozenset[str] = frozenset()


class DataFlags(IntFlag):
    """DATA flags (u8; also STATUS flags) (ICD §7.6)."""

    VALID = 0x01
    MOVING = 0x02
    HOMED = 0x04
    ENABLED = 0x08
    ESTOP = 0x10
    HALT = 0x20
    FAULT = 0x40
    OVERRUN = 0x80


DATA_FLAGS_BITS: tuple[str, ...] = ('VALID', 'MOVING', 'HOMED', 'ENABLED', 'ESTOP', 'HALT', 'FAULT', 'OVERRUN')
DATA_FLAGS_DESC: Mapping[str, str] = MappingProxyType({'VALID': 'data validity (D-05): SET_VALID, cleared by every operational stop except the jog dead-man', 'MOVING': 'motion state MOVE_ABS, JOG, MOVE_UNTIL_LOAD, HOMING or STOPPING', 'HOMED': 'machine zero valid', 'ENABLED': 'driver enabled and settled (motion state ≥ IDLE)', 'ESTOP': 'ESTOP latched or E-stop sense input open', 'HALT': 'HALT latched (PC HALT command / Pause-Break key; STATUS halt_src = PC)', 'FAULT': 'any FAULT latched (STATUS faults)', 'OVERRUN': '≥ 1 DATA frame dropped or ≥ 1 conversion missed by the FW since the previous sent frame'})
DATA_FLAGS_RETIRED: frozenset[str] = frozenset()


class DataStatus(IntFlag):
    """DATA status (u16; also STATUS status) (ICD §7.6)."""

    PAUSED = 0x0001
    LIMIT_START = 0x0002
    LIMIT_END = 0x0004
    LOAD_LIMIT = 0x0008
    AFE_STALE = 0x0010
    AFE_SATURATED = 0x0020
    AFE_SETTLING = 0x0040
    AFE_RATE_MISMATCH = 0x0080
    LINK_WDG = 0x0100
    STOP_BTN = 0x0200
    PAUSE_BTN = 0x0400
    ALM = 0x0800
    PEND = 0x1000
    POS_UNCERTAIN = 0x2000
    NO_AFE_DATA = 0x4000
    DRV_PWR = 0x8000


DATA_STATUS_BITS: tuple[str, ...] = ('PAUSED', 'LIMIT_START', 'LIMIT_END', 'LOAD_LIMIT', 'AFE_STALE', 'AFE_SATURATED', 'AFE_SETTLING', 'AFE_RATE_MISMATCH', 'LINK_WDG', '', 'PAUSE_BTN', 'ALM', 'PEND', 'POS_UNCERTAIN', 'NO_AFE_DATA', 'DRV_PWR')
DATA_STATUS_DESC: Mapping[str, str] = MappingProxyType({'PAUSED': 'PAUSED latch (source: STATUS pause_src, EVENT PAUSED arg); blocks new motion (BLOCK PAUSED); cleared by RESUME (clears only PAUSED) or HALT_CLEAR (clears HALT and PAUSED) (§5.5, D-30, D-31)', 'LIMIT_START': 'START limit input active or LIMIT_START latched', 'LIMIT_END': 'END limit input active or LIMIT_END latched', 'LOAD_LIMIT': 'FAULT LOAD_LIMIT latched', 'AFE_STALE': 'no HX711 sample for afe.timeout_ms', 'AFE_SATURATED': 'this sample at a rail', 'AFE_SETTLING': 'sample within afe.settle_discard after a (re)configuration', 'AFE_RATE_MISMATCH': 'measured rate deviates more than afe.rate_tol_pct', 'LINK_WDG': 'link watchdog tripped, until the next valid command frame', 'STOP_BTN': 'was: physical STOP/BREAK button input active. D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (MCU sense, D-41)', 'PAUSE_BTN': 'physical PAUSE button input active', 'ALM': 'driver ALM active', 'PEND': 'driver PEND (in position) active', 'POS_UNCERTAIN': 'an immediate stop may have truncated a pulse (±1 step), cleared by the next HOME', 'NO_AFE_DATA': 'fallback frame (afe_raw = 0x80000000)', 'DRV_PWR': 'driver power present; evaluated only with the optional power sense (drv.pwr_sense_enable, default 0, CR-03 / D-41): reads 1 when it is off and FEAT_DRV_SIGNALS = 1'})
DATA_STATUS_RETIRED: frozenset[str] = frozenset({'STOP_BTN'})  # reserved, never sent, never reused (members kept for compatibility)
DATA_STATUS_FEATURE: Mapping[str, str] = MappingProxyType({'PAUSE_BTN': 'BUTTONS', 'ALM': 'DRV_SIGNALS', 'PEND': 'DRV_SIGNALS', 'DRV_PWR': 'DRV_SIGNALS'})  # bit valid only while this INFO feature bit is 1 (ICD §7.6)


class Faults(IntFlag):
    """FAULT mask (u16) (ICD §7.6)."""

    LOAD_LIMIT = 0x0001
    AFE_FAULT = 0x0002
    STEP_FAULT = 0x0004
    LIMIT_WIRING = 0x0008
    HOME_NOT_FOUND = 0x0010
    HOME_WIRING = 0x0020
    K1_WELDED = 0x0040
    HOME_DRIFT = 0x0080


FAULTS_BITS: tuple[str, ...] = ('LOAD_LIMIT', 'AFE_FAULT', 'STEP_FAULT', 'LIMIT_WIRING', 'HOME_NOT_FOUND', 'HOME_WIRING', 'K1_WELDED', 'HOME_DRIFT')
FAULTS_DESC: Mapping[str, str] = MappingProxyType({'LOAD_LIMIT': 'FW load limit or rail sample; always clearable (re-trip on regrow, SAF-FW-011)', 'AFE_FAULT': 'AFE stale while moving; cause: AFE stale or last sample saturated', 'STEP_FAULT': 'step overrun / count fault; HOMED cleared; no persistent cause', 'LIMIT_WIRING': 'both limit inputs active; cause: both still active', 'HOME_NOT_FOUND': 'no START edge within home.max_travel_um; no persistent cause', 'HOME_WIRING': 'END switch reached during homing; no persistent cause', 'K1_WELDED': 'Power-removal device did not open with the E-stop (only with the optional power sense and the K1 check enabled: drv.pwr_sense_enable and drv.k1_check_enable, both default 0; SRS OI-18): E-stop sense open while driver power stays present > drv.k1_weld_ms (D-29c); cause: E-stop open and power present', 'HOME_DRIFT': 're-homing edge deviates > home.drift_tol_um; no persistent cause'})
FAULTS_RETIRED: frozenset[str] = frozenset()


class IoBits(IntFlag):
    """IO mask (u16, STATUS io; 'active' after polarity) (ICD §7.6)."""

    ESTOP_OPEN = 0x0001
    LIMIT_START = 0x0002
    LIMIT_END = 0x0004
    STOP_BTN = 0x0008
    PAUSE_BTN = 0x0010
    ALM = 0x0020
    PEND = 0x0040
    DRV_PWR = 0x0080
    ENA_DISABLED = 0x0100
    RATE_80 = 0x0200


IO_BITS: tuple[str, ...] = ('ESTOP_OPEN', 'LIMIT_START', 'LIMIT_END', '', 'PAUSE_BTN', 'ALM', 'PEND', 'DRV_PWR', 'ENA_DISABLED', 'RATE_80')
IO_DESC: Mapping[str, str] = MappingProxyType({'ESTOP_OPEN': 'E-stop sense input open', 'LIMIT_START': 'START limit input active', 'LIMIT_END': 'END limit input active', 'STOP_BTN': 'was: STOP/BREAK button input active (PC7 is no longer an input). D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (MCU sense, D-41)', 'PAUSE_BTN': 'PAUSE button input active', 'ALM': 'driver ALM input active', 'PEND': 'driver PEND input active', 'DRV_PWR': "raw driver-power sense input 'powered' (optional 48 V presence sense, CR-03)", 'ENA_DISABLED': 'ENA output at the disabled level', 'RATE_80': 'HX711 RATE output high'})
IO_RETIRED: frozenset[str] = frozenset({'STOP_BTN'})  # reserved, never sent, never reused (members kept for compatibility)
IO_FEATURE: Mapping[str, str] = MappingProxyType({'PAUSE_BTN': 'BUTTONS', 'ALM': 'DRV_SIGNALS', 'PEND': 'DRV_SIGNALS', 'DRV_PWR': 'DRV_SIGNALS'})  # bit valid only while this INFO feature bit is 1 (ICD §7.6)


class Event(IntEnum):
    """EVENT codes (ICD §8.1)."""

    BOOT = 1
    STOPPED = 2
    MOVE_DONE = 3
    ESTOP_SET = 4
    ESTOP_CLEARED = 5
    HALT_SET = 6
    HALT_CLEARED = 7
    PAUSED = 8
    PAUSE_CLEARED = 9
    RESUME_REQUEST = 10
    FAULT_SET = 11
    FAULT_CLEARED = 12
    LIMIT_SET = 13
    LIMIT_CLEARED = 14
    LINK_WDG = 15
    LINK_RESTORED = 16
    VALID_CLEARED = 17
    HOMED = 18
    HOME_FAILED = 19
    DRIVER_ENABLED = 20
    DRIVER_DISABLED = 21
    STOP_BUTTON = 22
    PAUSE_BUTTON = 23
    ALM_CHANGED = 24
    AFE_REINIT = 25
    AFE_RATE_MISMATCH = 26
    AFE_STALE = 27
    PARAMS_SAVED = 28
    PARAMS_LOADED = 29
    PARAMS_DEFAULTED = 30
    NVM_ERROR = 31
    CLK_FALLBACK = 32
    DRIVER_POWER = 33
    NOT_SETTLED = 34


EVENT_NAMES: tuple[str, ...] = ('BOOT', 'STOPPED', 'MOVE_DONE', 'ESTOP_SET', 'ESTOP_CLEARED', 'HALT_SET', 'HALT_CLEARED', 'PAUSED', 'PAUSE_CLEARED', 'RESUME_REQUEST', 'FAULT_SET', 'FAULT_CLEARED', 'LIMIT_SET', 'LIMIT_CLEARED', 'LINK_WDG', 'LINK_RESTORED', 'VALID_CLEARED', 'HOMED', 'HOME_FAILED', 'DRIVER_ENABLED', 'DRIVER_DISABLED', 'PAUSE_BUTTON', 'ALM_CHANGED', 'AFE_REINIT', 'AFE_RATE_MISMATCH', 'AFE_STALE', 'PARAMS_SAVED', 'PARAMS_LOADED', 'PARAMS_DEFAULTED', 'NVM_ERROR', 'CLK_FALLBACK', 'DRIVER_POWER', 'NOT_SETTLED')
EVENT_DESC: Mapping[str, str] = MappingProxyType({'BOOT': 'reset_cause', 'STOPPED': 'stop_cause (§8.2)', 'MOVE_DONE': 'move_done_reason (§8.3)', 'ESTOP_SET': '0', 'ESTOP_CLEARED': '0', 'HALT_SET': 'source: 1 PC (HALT command / Pause-Break key); 2 BUTTON never since v0.5 (D-36)', 'HALT_CLEARED': '0', 'PAUSED': 'source: 1 PC, 2 BUTTON (sent when PAUSED goes 0 → 1 only)', 'PAUSE_CLEARED': 'pause_cleared_reason: 2 HALT_CLEAR, 3 RESUME (code 1 unused since ICD v0.3)', 'RESUME_REQUEST': '0 (PAUSE button pressed while PAUSED)', 'FAULT_SET': 'FAULT bit index (§7.6)', 'FAULT_CLEARED': 'FAULT mask cleared', 'LIMIT_SET': 'limit_id: 0 START, 1 END', 'LIMIT_CLEARED': 'limit_id: 0 START, 1 END', 'LINK_WDG': '0', 'LINK_RESTORED': '0', 'VALID_CLEARED': 'stop_cause (§8.2)', 'HOMED': '0', 'HOME_FAILED': 'home_fail_reason: 1 NOT_FOUND, 2 WIRING, 3 ABORTED', 'DRIVER_ENABLED': '0', 'DRIVER_DISABLED': 'driver_disabled_cause: 1 PC DISABLE, 2 IDLE, 3 ESTOP, 4 DRV_POWER_LOST', 'STOP_BUTTON': '–', 'PAUSE_BUTTON': '1 pressed, 0 released', 'ALM_CHANGED': '1 active, 0 inactive', 'AFE_REINIT': 're-init count (low 16 bit)', 'AFE_RATE_MISMATCH': '1 set, 0 cleared', 'AFE_STALE': '1 stale, 0 fresh again', 'PARAMS_SAVED': '0', 'PARAMS_LOADED': 'values replaced by defaults', 'PARAMS_DEFAULTED': 'params_defaulted_reason', 'NVM_ERROR': 'nvm_detail (E_NVM detail)', 'CLK_FALLBACK': '0', 'DRIVER_POWER': '1 power present, 0 lost (debounced; only with drv.pwr_sense_enable)', 'NOT_SETTLED': '0 (PEND not active within drv.pend_timeout_ms after the last pulse; warning only)'})
EVENT_RETIRED: frozenset[str] = frozenset({'STOP_BUTTON'})  # reserved, never sent, never reused (members kept for compatibility)
EVENT_ARG: Mapping[str, str] = MappingProxyType({'BOOT': 'reset_cause', 'STOPPED': 'stop_cause (§8.2)', 'MOVE_DONE': 'move_done_reason (§8.3)', 'ESTOP_SET': '0', 'ESTOP_CLEARED': '0', 'HALT_SET': 'source: 1 PC (HALT command / Pause-Break key); 2 BUTTON never since v0.5 (D-36)', 'HALT_CLEARED': '0', 'PAUSED': 'source: 1 PC, 2 BUTTON (sent when PAUSED goes 0 → 1 only)', 'PAUSE_CLEARED': 'pause_cleared_reason: 2 HALT_CLEAR, 3 RESUME (code 1 unused since ICD v0.3)', 'RESUME_REQUEST': '0 (PAUSE button pressed while PAUSED)', 'FAULT_SET': 'FAULT bit index (§7.6)', 'FAULT_CLEARED': 'FAULT mask cleared', 'LIMIT_SET': 'limit_id: 0 START, 1 END', 'LIMIT_CLEARED': 'limit_id: 0 START, 1 END', 'LINK_WDG': '0', 'LINK_RESTORED': '0', 'VALID_CLEARED': 'stop_cause (§8.2)', 'HOMED': '0', 'HOME_FAILED': 'home_fail_reason: 1 NOT_FOUND, 2 WIRING, 3 ABORTED', 'DRIVER_ENABLED': '0', 'DRIVER_DISABLED': 'driver_disabled_cause: 1 PC DISABLE, 2 IDLE, 3 ESTOP, 4 DRV_POWER_LOST', 'STOP_BUTTON': '–', 'PAUSE_BUTTON': '1 pressed, 0 released', 'ALM_CHANGED': '1 active, 0 inactive', 'AFE_REINIT': 're-init count (low 16 bit)', 'AFE_RATE_MISMATCH': '1 set, 0 cleared', 'AFE_STALE': '1 stale, 0 fresh again', 'PARAMS_SAVED': '0', 'PARAMS_LOADED': 'values replaced by defaults', 'PARAMS_DEFAULTED': 'params_defaulted_reason', 'NVM_ERROR': 'nvm_detail (E_NVM detail)', 'CLK_FALLBACK': '0', 'DRIVER_POWER': '1 power present, 0 lost (debounced; only with drv.pwr_sense_enable)', 'NOT_SETTLED': '0 (PEND not active within drv.pend_timeout_ms after the last pulse; warning only)'})
EVENT_VALUES: Mapping[str, str] = MappingProxyType({'BOOT': 'HardFault record of the previous run: faulting PC / CFSR (bit patterns as i32); 0 / 0 if none (OI-FW-21)', 'STOPPED': 'pos_um / pos_steps', 'MOVE_DONE': 'final pos_um / pos_steps', 'ESTOP_SET': 'pos_um / pos_steps', 'ESTOP_CLEARED': '0 / 0', 'HALT_SET': '0 / 0', 'HALT_CLEARED': '0 / 0', 'PAUSED': '0 / 0', 'PAUSE_CLEARED': '0 / 0', 'RESUME_REQUEST': '0 / 0', 'FAULT_SET': 'deciding value: raw (LOAD_LIMIT), deviation µm (HOME_DRIFT), ms the driver power stayed present with the E-stop open (K1_WELDED) / 0; else pos_um / pos_steps', 'FAULT_CLEARED': '0 / 0', 'LIMIT_SET': 'pos_um / pos_steps', 'LIMIT_CLEARED': '0 / 0', 'LINK_WDG': '0 / 0', 'LINK_RESTORED': '0 / 0', 'VALID_CLEARED': '0 / 0', 'HOMED': 'drift µm vs. the previous zero (0 if not homed before) / 0', 'HOME_FAILED': 'pos_um / pos_steps', 'DRIVER_ENABLED': '0 / 0', 'DRIVER_DISABLED': '0 / 0', 'STOP_BUTTON': '–', 'PAUSE_BUTTON': '0 / 0', 'ALM_CHANGED': '0 / 0', 'AFE_REINIT': '0 / 0', 'AFE_RATE_MISMATCH': 'measured rate 0.1 SPS / 0', 'AFE_STALE': '0 / 0', 'PARAMS_SAVED': 'record sequence number / 0', 'PARAMS_LOADED': '0 / 0', 'PARAMS_DEFAULTED': '0 / 0', 'NVM_ERROR': '0 / 0', 'CLK_FALLBACK': '0 / 0', 'DRIVER_POWER': '0 / 0', 'NOT_SETTLED': 'elapsed ms / 0'})


class StopCause(IntEnum):
    """Stop causes (STOPPED arg, VALID_CLEARED arg) (ICD §8.2)."""

    NONE = 0
    PC_STOP = 1
    PC_STOP_CONTROLLED = 2
    PC_HALT = 3
    STOP_BUTTON = 4
    PAUSE_BUTTON = 5
    PC_PAUSE = 6
    ESTOP = 7
    LIMIT_START = 8
    LIMIT_END = 9
    LIMIT_WIRING = 10
    LOAD_LIMIT = 11
    AFE_FAULT = 12
    LINK_WDG = 13
    JOG_DEADMAN = 14
    STEP_FAULT = 15
    HOME_FAIL = 16
    DRV_POWER_LOST = 17


STOP_CAUSE_NAMES: tuple[str, ...] = ('NONE', 'PC_STOP', 'PC_STOP_CONTROLLED', 'PC_HALT', 'PAUSE_BUTTON', 'PC_PAUSE', 'ESTOP', 'LIMIT_START', 'LIMIT_END', 'LIMIT_WIRING', 'LOAD_LIMIT', 'AFE_FAULT', 'LINK_WDG', 'JOG_DEADMAN', 'STEP_FAULT', 'HOME_FAIL', 'DRV_POWER_LOST')
STOP_CAUSE_DESC: Mapping[str, str] = MappingProxyType({'NONE': '—', 'PC_STOP': 'STOP mode 0', 'PC_STOP_CONTROLLED': 'STOP mode 1', 'PC_HALT': 'HALT command', 'STOP_BUTTON': 'was: physical STOP/BREAK button. D-36: no physical holding STOP/BREAK button; the single red button is the E-stop (MCU sense, D-41)', 'PAUSE_BUTTON': 'physical PAUSE button', 'PC_PAUSE': 'PAUSE command', 'ESTOP': 'E-stop sense opened', 'LIMIT_START': 'START limit switch', 'LIMIT_END': 'END limit switch', 'LIMIT_WIRING': 'both limit inputs active', 'LOAD_LIMIT': 'FW load limit (incl. rail sample)', 'AFE_FAULT': 'AFE stale while moving', 'LINK_WDG': 'link watchdog', 'JOG_DEADMAN': 'jog dead-man (VALID unchanged)', 'STEP_FAULT': 'step overrun / count fault', 'HOME_FAIL': 'homing failure', 'DRV_POWER_LOST': 'driver power lost while a motion was running (SAF-FW-024, D-29c)'})
STOP_CAUSE_RETIRED: frozenset[str] = frozenset({'STOP_BUTTON'})  # reserved, never sent, never reused (members kept for compatibility)


class MoveDoneReason(IntEnum):
    """MOVE_DONE reasons (ICD §8.3)."""

    TARGET = 0
    LOAD_THRESHOLD = 1
    BOUND = 2
    SOFT_LIMIT = 3
    JOG_ZERO = 4
    STOPPED = 5


MOVE_DONE_REASON_NAMES: tuple[str, ...] = ('TARGET', 'LOAD_THRESHOLD', 'BOUND', 'SOFT_LIMIT', 'JOG_ZERO', 'STOPPED')
MOVE_DONE_REASON_DESC: Mapping[str, str] = MappingProxyType({'TARGET': 'MOVE_ABS / HOME end', 'LOAD_THRESHOLD': 'MOVE_UNTIL_LOAD threshold', 'BOUND': 'MOVE_UNTIL_LOAD or JOG bound', 'SOFT_LIMIT': 'JOG end at a soft limit or un-homed travel bound', 'JOG_ZERO': 'JOG 0', 'STOPPED': 'ended by a stop source; see the preceding STOPPED / HOME_FAILED'})
MOVE_DONE_REASON_RETIRED: frozenset[str] = frozenset()


class HomeFailReason(IntEnum):
    """HOME_FAILED reasons (ICD §8.1)."""

    NOT_FOUND = 1
    WIRING = 2
    ABORTED = 3


HOME_FAIL_REASON_NAMES: tuple[str, ...] = ('NOT_FOUND', 'WIRING', 'ABORTED')
HOME_FAIL_REASON_DESC: Mapping[str, str] = MappingProxyType({'NOT_FOUND': 'no START edge within home.max_travel_um (fault HOME_NOT_FOUND)', 'WIRING': 'END switch reached (fault HOME_WIRING)', 'ABORTED': 'any other stop during homing (no latch of its own)'})
HOME_FAIL_REASON_RETIRED: frozenset[str] = frozenset()


class DriverDisabledCause(IntEnum):
    """DRIVER_DISABLED causes (ICD §8.1)."""

    PC_DISABLE = 1
    IDLE = 2
    ESTOP = 3
    DRV_POWER_LOST = 4


DRIVER_DISABLED_CAUSE_NAMES: tuple[str, ...] = ('PC_DISABLE', 'IDLE', 'ESTOP', 'DRV_POWER_LOST')
DRIVER_DISABLED_CAUSE_DESC: Mapping[str, str] = MappingProxyType({'PC_DISABLE': 'DISABLE command', 'IDLE': 'idle auto-disable (safety.idle_disable_s)', 'ESTOP': 'E-stop sense opened', 'DRV_POWER_LOST': 'driver power lost while enabled / enabling (SAF-FW-024, D-29c)'})
DRIVER_DISABLED_CAUSE_RETIRED: frozenset[str] = frozenset()


class PauseClearedReason(IntEnum):
    """PAUSE_CLEARED reasons (ICD §8.1)."""

    HALT_CLEAR = 2
    RESUME = 3


PAUSE_CLEARED_REASON_NAMES: tuple[str, ...] = ('HALT_CLEAR', 'RESUME')
PAUSE_CLEARED_REASON_DESC: Mapping[str, str] = MappingProxyType({'HALT_CLEAR': 'accepted HALT_CLEAR (clears HALT and PAUSED, D-31)', 'RESUME': 'accepted RESUME (clears only PAUSED, D-31)'})
PAUSE_CLEARED_REASON_RETIRED: frozenset[str] = frozenset()


class ParamsDefaultedReason(IntEnum):
    """PARAMS_DEFAULTED reasons (ICD §8.1)."""

    COMMAND = 0
    NO_RECORD = 1
    CRC_ERROR = 2
    MIGRATION = 3
    HARD_RULE = 4


PARAMS_DEFAULTED_REASON_NAMES: tuple[str, ...] = ('COMMAND', 'NO_RECORD', 'CRC_ERROR', 'MIGRATION', 'HARD_RULE')
PARAMS_DEFAULTED_REASON_DESC: Mapping[str, str] = MappingProxyType({'COMMAND': 'DEFAULT_PARAMS', 'NO_RECORD': 'no NVM record (blank)', 'CRC_ERROR': 'both records CRC-bad', 'MIGRATION': 'record with another PARAM_DICT_HASH (migration by id)', 'HARD_RULE': 'resulting image violates a hard rule'})
PARAMS_DEFAULTED_REASON_RETIRED: frozenset[str] = frozenset()


class LimitId(IntEnum):
    """Limit switch id (LIMIT_SET / LIMIT_CLEARED arg) (ICD §8.1)."""

    START = 0
    END = 1


LIMIT_ID_NAMES: tuple[str, ...] = ('START', 'END')
LIMIT_ID_DESC: Mapping[str, str] = MappingProxyType({'START': 'START switch (−x end, home reference)', 'END': 'END switch (+x end)'})
LIMIT_ID_RETIRED: frozenset[str] = frozenset()


class AfeSampleStatus(IntFlag):
    """afe_sample_t.status (seam hal_hx711, tools/README; not on the wire) (ICD tools/README seam v1.2)."""

    SCK_OVERRUN = 0x01
    MISSED_EDGE = 0x02


AFE_SAMPLE_STATUS_BITS: tuple[str, ...] = ('SCK_OVERRUN', 'MISSED_EDGE')
AFE_SAMPLE_STATUS_DESC: Mapping[str, str] = MappingProxyType({'SCK_OVERRUN': 'the read of this sample overran (SCK high > 60 us or DOUT still low after the last pulse): the HX711 may have entered power-down; the core re-initialises it (afe_reinit_count, EVENT AFE_REINIT) and flags the next afe.settle_discard samples AFE_SETTLING', 'MISSED_EDGE': 'at least one DOUT-ready edge was missed before this sample (recovered by hal_hx711_kick or a late edge): the core sets OVERRUN in the next DATA frame'})
AFE_SAMPLE_STATUS_RETIRED: frozenset[str] = frozenset()


class MeasOp(IntEnum):
    """DIAG_MEAS op (request byte 0) (ICD App. C)."""

    INFO = 0
    PROBE_ARM = 1
    PROBE_READ = 2
    COUNTER = 3
    STAMPS = 4
    NOINIT = 5
    STIM_RUN = 6
    HANG = 7
    STATIC_LEVEL = 8
    DWT = 9


MEAS_OP_NAMES: tuple[str, ...] = ('INFO', 'PROBE_ARM', 'PROBE_READ', 'COUNTER', 'STAMPS', 'NOINIT', 'STIM_RUN', 'HANG', 'STATIC_LEVEL', 'DWT')
MEAS_OP_DESC: Mapping[str, str] = MappingProxyType({'INFO': 'variant, clocks, ring size, stamp overhead', 'PROBE_ARM': 'arm the event-latency probe (MT-3)', 'PROBE_READ': 'read the probe captures', 'COUNTER': 'independent PUL counter (MT-2): read / reset', 'STAMPS': 'device-time stamp ring (MT-4), newest first', 'NOINIT': '.noinit block (last PUL, heartbeat, hang start, previous-boot record, boot counter; survives a reset)', 'STIM_RUN': 'stimulus series on the J-STIM output (MT-7)', 'HANG': 'test-image hang injection while moving (IWDG evidence)', 'STATIC_LEVEL': 'drive PUL or DIR statically for the DMM (only NOT_ENABLED; PUL only with ENA at the disabled level; released before the next command is executed)', 'DWT': 'DWT per-section cycle statistics (HW_MEAS_DWT builds; else w0 = 0)'})
MEAS_OP_RETIRED: frozenset[str] = frozenset()
MEAS_OP_SEL: Mapping[str, str] = MappingProxyType({'INFO': '0', 'PROBE_ARM': 'meas_src', 'PROBE_READ': '0', 'COUNTER': '0 read, 1 reset (returns the value before the reset)', 'STAMPS': 'meas_chan', 'NOINIT': '0 read, 1 clear', 'STIM_RUN': 'bit 0 polarity (0 high pulse, 1 low pulse), bits 1-7 hold time 1…127 ms', 'HANG': 'meas_hang_where', 'STATIC_LEVEL': 'meas_pin', 'DWT': '0 read, 1 reset'})
MEAS_OP_A: Mapping[str, str] = MappingProxyType({'INFO': '0', 'PROBE_ARM': 'bits 0-1 meas_probe_mode, bit 8 event polarity (0 rising, 1 falling), other bits 0', 'PROBE_READ': '0', 'COUNTER': '0', 'STAMPS': 'page 0…1023', 'NOINIT': '0', 'STIM_RUN': 'pulses 1…1000', 'HANG': 'duration 0…10000 ms (0 = until the IWDG resets)', 'STATIC_LEVEL': 'level 0/1', 'DWT': 'section 0…31 (0…23 defined, App. C table C.1)'})
MEAS_OP_B: Mapping[str, str] = MappingProxyType({'INFO': '0', 'PROBE_ARM': 'timer prescaler 0…65535', 'PROBE_READ': '0', 'COUNTER': '0', 'STAMPS': '0', 'NOINIT': '0', 'STIM_RUN': 'seed', 'HANG': '0', 'STATIC_LEVEL': '0', 'DWT': '0'})
MEAS_OP_RETRY: Mapping[str, str] = MappingProxyType({'INFO': 'RETRY', 'PROBE_ARM': 'VERIFY', 'PROBE_READ': 'RETRY', 'COUNTER': 'RETRY (read) / VERIFY (reset)', 'STAMPS': 'RETRY', 'NOINIT': 'RETRY (read) / VERIFY (clear)', 'STIM_RUN': 'VERIFY', 'HANG': 'VERIFY', 'STATIC_LEVEL': 'VERIFY', 'DWT': 'RETRY (read) / VERIFY (reset)'})


class MeasSrc(IntEnum):
    """DIAG_MEAS probe event source (PROBE_ARM sel; J-EVT selector position, FW_test_plan §6.2) (ICD App. C)."""

    ESTOP = 0
    LIMIT_START = 1
    LIMIT_END = 2
    PAUSE = 3
    DOUT = 4
    DRV_PWR = 5
    RX = 6
    DIR = 7
    STIM = 8


MEAS_SRC_NAMES: tuple[str, ...] = ('ESTOP', 'LIMIT_START', 'LIMIT_END', 'PAUSE', 'DOUT', 'DRV_PWR', 'RX', 'DIR', 'STIM')
MEAS_SRC_DESC: Mapping[str, str] = MappingProxyType({'ESTOP': 'E-stop sense PA10', 'LIMIT_START': 'START limit PB0', 'LIMIT_END': 'END limit PC1', 'PAUSE': 'PAUSE button PB6', 'DOUT': 'HX711 DOUT PB4 (data ready)', 'DRV_PWR': 'driver-power sense PA7', 'RX': 'USART2 RX PA3 (start bit of a frame byte)', 'DIR': 'DIR output node', 'STIM': 'stimulus output PB8 (self-test)'})
MEAS_SRC_RETIRED: frozenset[str] = frozenset()


class MeasProbeMode(IntEnum):
    """DIAG_MEAS probe mode (PROBE_ARM a bits 0-1) (ICD App. C)."""

    TRIGGER = 0
    RESET = 1
    PWM_INPUT = 2


MEAS_PROBE_MODE_NAMES: tuple[str, ...] = ('TRIGGER', 'RESET', 'PWM_INPUT')
MEAS_PROBE_MODE_DESC: Mapping[str, str] = MappingProxyType({'TRIGGER': 'the first event edge after arming starts the probe counter (single shot)', 'RESET': 'every event edge restarts the probe counter (last event wins)', 'PWM_INPUT': 'PUL period and high width per pulse (min/max over the pulses since arming; a period that overflowed the 16-bit capture = 0xFFFFFFFF)'})
MEAS_PROBE_MODE_RETIRED: frozenset[str] = frozenset()


class MeasProbeFlags(IntFlag):
    """DIAG_MEAS PROBE_READ w0 flags (ICD App. C)."""

    ARMED = 0x0001
    TRIGGERED = 0x0002
    OVERCAPTURE = 0x0004
    WINDOW_OVERFLOW = 0x0008
    EDGE_BEFORE_EVENT = 0x0010


MEAS_PROBE_FLAGS_BITS: tuple[str, ...] = ('ARMED', 'TRIGGERED', 'OVERCAPTURE', 'WINDOW_OVERFLOW', 'EDGE_BEFORE_EVENT')
MEAS_PROBE_FLAGS_DESC: Mapping[str, str] = MappingProxyType({'ARMED': 'probe armed', 'TRIGGERED': 'the event occurred (w1 valid)', 'OVERCAPTURE': 'a capture was overwritten before it was read (hardware over-capture)', 'WINDOW_OVERFLOW': 'the probe counter overflowed after the event (window 65 536 ticks): later captures invalid', 'EDGE_BEFORE_EVENT': 'a PUL edge was captured before the event (CCR = 0 case)'})
MEAS_PROBE_FLAGS_RETIRED: frozenset[str] = frozenset()


class MeasChan(IntEnum):
    """DIAG_MEAS stamp channel (STAMPS sel) (ICD App. C)."""

    EVT = 0
    PUL = 1
    DIR = 2
    AUX = 3


MEAS_CHAN_NAMES: tuple[str, ...] = ('EVT', 'PUL', 'DIR', 'AUX')
MEAS_CHAN_DESC: Mapping[str, str] = MappingProxyType({'EVT': 'J-EVT edges (TIM8 CH1)', 'PUL': 'PUL rising edges (TIM8 CH2)', 'DIR': 'DIR edges (TIM8 CH4)', 'AUX': 'J-AUX edges (TIM1 CH4)'})
MEAS_CHAN_RETIRED: frozenset[str] = frozenset()


class MeasHangWhere(IntEnum):
    """DIAG_MEAS HANG where (HANG sel) (ICD App. C)."""

    MAIN = 0
    TICK = 1
    ISR1 = 2


MEAS_HANG_WHERE_NAMES: tuple[str, ...] = ('MAIN', 'TICK', 'ISR1')
MEAS_HANG_WHERE_DESC: Mapping[str, str] = MappingProxyType({'MAIN': 'main loop stops kicking the IWDG', 'TICK': 'the 1 kHz tick hangs', 'ISR1': 'level-1 ISR storm (software-triggered unused EXTI line, test image only)'})
MEAS_HANG_WHERE_RETIRED: frozenset[str] = frozenset()


class MeasPin(IntEnum):
    """DIAG_MEAS STATIC_LEVEL pin (sel) (ICD App. C)."""

    PUL = 0
    DIR = 1


MEAS_PIN_NAMES: tuple[str, ...] = ('PUL', 'DIR')
MEAS_PIN_DESC: Mapping[str, str] = MappingProxyType({'PUL': 'PUL output', 'DIR': 'DIR output'})
MEAS_PIN_RETIRED: frozenset[str] = frozenset()


class MeasVariant(IntFlag):
    """DIAG_MEAS INFO w0 variant (ICD App. C)."""

    MEAS = 0x00000001
    DWT = 0x00000002
    TWIN_MODEL = 0x00000004


MEAS_VARIANT_BITS: tuple[str, ...] = ('MEAS', 'DWT', 'TWIN_MODEL')
MEAS_VARIANT_DESC: Mapping[str, str] = MappingProxyType({'MEAS': 'HW_MEAS build (MT-2/3/4/7, HANG, STATIC_LEVEL)', 'DWT': 'HW_MEAS_DWT build (DWT section statistics)', 'TWIN_MODEL': "the FW host twin's model of DIAG_MEAS (REQ-C-M2-08)"})
MEAS_VARIANT_RETIRED: frozenset[str] = frozenset()


class RetryClass(IntEnum):
    """SW retry class (§9.3; not on the wire) (ICD §9.3)."""

    RETRY = 0
    CONFIRM = 1
    ONCE_PRIORITY = 2
    VERIFY = 3


RETRY_CLASS_NAMES: tuple[str, ...] = ('RETRY', 'CONFIRM', 'ONCE_PRIORITY', 'VERIFY')
RETRY_CLASS_DESC: Mapping[str, str] = MappingProxyType({'RETRY': 'timeout 100 ms, ≤ 2 retries with a new SEQ and the newest value', 'CONFIRM': 'priority path, repeated every 50 ms until confirmed (≤ 20 in 1 s)', 'ONCE_PRIORITY': 'unused since ICD v0.4.1 (D-34: the clears are VERIFY); value kept, never reassigned', 'VERIFY': 'never retried; after a timeout GET_STATUS decides (commands with priority = true still use the SW priority lane)'})
RETRY_CLASS_RETIRED: frozenset[str] = frozenset()


class Cmd(IntEnum):
    """Command TYPEs (ICD §3.2); response TYPE = TYPE | RESP_BIT."""

    PING = 0x01
    GET_INFO = 0x02
    GET_STATUS = 0x03
    REBOOT = 0x04
    GET_ALL_PARAMS = 0x10
    GET_PARAM = 0x11
    SET_PARAM = 0x12
    SAVE_PARAMS = 0x13
    LOAD_PARAMS = 0x14
    DEFAULT_PARAMS = 0x15
    STREAM_START = 0x20
    STREAM_STOP = 0x21
    SET_VALID = 0x22
    ENABLE = 0x30
    DISABLE = 0x31
    HOME = 0x32
    MOVE_ABS = 0x33
    JOG = 0x34
    MOVE_UNTIL_LOAD = 0x35
    STOP = 0x36
    HALT = 0x37
    HALT_CLEAR = 0x38
    ESTOP_CLEAR = 0x39
    FAULT_CLEAR = 0x3A
    PAUSE = 0x3B
    RESUME = 0x3C
    DIAG_MEAS = 0x3D


CMD_REQ_LEN: Mapping[Cmd, int] = MappingProxyType({
    Cmd.PING: 0,
    Cmd.GET_INFO: 0,
    Cmd.GET_STATUS: 0,
    Cmd.REBOOT: 4,
    Cmd.GET_ALL_PARAMS: 1,
    Cmd.GET_PARAM: 2,
    Cmd.SET_PARAM: 7,
    Cmd.SAVE_PARAMS: 0,
    Cmd.LOAD_PARAMS: 0,
    Cmd.DEFAULT_PARAMS: 0,
    Cmd.STREAM_START: 0,
    Cmd.STREAM_STOP: 0,
    Cmd.SET_VALID: 1,
    Cmd.ENABLE: 0,
    Cmd.DISABLE: 0,
    Cmd.HOME: 1,
    Cmd.MOVE_ABS: 12,
    Cmd.JOG: 12,
    Cmd.MOVE_UNTIL_LOAD: 17,
    Cmd.STOP: 1,
    Cmd.HALT: 0,
    Cmd.HALT_CLEAR: 0,
    Cmd.ESTOP_CLEAR: 0,
    Cmd.FAULT_CLEAR: 0,
    Cmd.PAUSE: 0,
    Cmd.RESUME: 0,
    Cmd.DIAG_MEAS: 8,
})
CMD_RETRY: Mapping[Cmd, RetryClass] = MappingProxyType({
    Cmd.PING: RetryClass.RETRY,
    Cmd.GET_INFO: RetryClass.RETRY,
    Cmd.GET_STATUS: RetryClass.RETRY,
    Cmd.REBOOT: RetryClass.VERIFY,
    Cmd.GET_ALL_PARAMS: RetryClass.RETRY,
    Cmd.GET_PARAM: RetryClass.RETRY,
    Cmd.SET_PARAM: RetryClass.RETRY,
    Cmd.SAVE_PARAMS: RetryClass.VERIFY,
    Cmd.LOAD_PARAMS: RetryClass.VERIFY,
    Cmd.DEFAULT_PARAMS: RetryClass.VERIFY,
    Cmd.STREAM_START: RetryClass.RETRY,
    Cmd.STREAM_STOP: RetryClass.RETRY,
    Cmd.SET_VALID: RetryClass.RETRY,
    Cmd.ENABLE: RetryClass.VERIFY,
    Cmd.DISABLE: RetryClass.VERIFY,
    Cmd.HOME: RetryClass.VERIFY,
    Cmd.MOVE_ABS: RetryClass.VERIFY,
    Cmd.JOG: RetryClass.VERIFY,
    Cmd.MOVE_UNTIL_LOAD: RetryClass.VERIFY,
    Cmd.STOP: RetryClass.CONFIRM,
    Cmd.HALT: RetryClass.CONFIRM,
    Cmd.HALT_CLEAR: RetryClass.VERIFY,
    Cmd.ESTOP_CLEAR: RetryClass.VERIFY,
    Cmd.FAULT_CLEAR: RetryClass.VERIFY,
    Cmd.PAUSE: RetryClass.CONFIRM,
    Cmd.RESUME: RetryClass.VERIFY,
    Cmd.DIAG_MEAS: RetryClass.VERIFY,
})
# JOG 0 is RETRY class (CMD_RETRY gives the JOG != 0 class)
CMD_PRIORITY: frozenset[Cmd] = frozenset({Cmd.STOP, Cmd.HALT, Cmd.HALT_CLEAR, Cmd.ESTOP_CLEAR, Cmd.FAULT_CLEAR, Cmd.PAUSE})
CMD_SNIFFED: frozenset[Cmd] = frozenset({Cmd.STOP, Cmd.HALT, Cmd.PAUSE})  # FW stop sniffer (ICD §2)

# registry: table id -> enum/flag class (GUI channel tree, indicator registry)
TABLES: Mapping[str, type] = MappingProxyType({
    'async_type': AsyncType,
    'status_code': Status,
    'busy_detail': BusyDetail,
    'internal_detail': InternalDetail,
    'nvm_detail': NvmDetail,
    'block': Block,
    'stop_mode': StopMode,
    'mul_cmp': MulCmp,
    'home_flags': HomeFlags,
    'motion_state': MotionState,
    'home_phase': HomePhase,
    'source': Source,
    'reset_cause': ResetCause,
    'sys_flags': SysFlags,
    'features': Features,
    'data_flags': DataFlags,
    'data_status': DataStatus,
    'faults': Faults,
    'io': IoBits,
    'event': Event,
    'stop_cause': StopCause,
    'move_done_reason': MoveDoneReason,
    'home_fail_reason': HomeFailReason,
    'driver_disabled_cause': DriverDisabledCause,
    'pause_cleared_reason': PauseClearedReason,
    'params_defaulted_reason': ParamsDefaultedReason,
    'limit_id': LimitId,
    'afe_sample_status': AfeSampleStatus,
    'meas_op': MeasOp,
    'meas_src': MeasSrc,
    'meas_probe_mode': MeasProbeMode,
    'meas_probe_flags': MeasProbeFlags,
    'meas_chan': MeasChan,
    'meas_hang_where': MeasHangWhere,
    'meas_pin': MeasPin,
    'meas_variant': MeasVariant,
    'retry_class': RetryClass,
    'command': Cmd,
})
