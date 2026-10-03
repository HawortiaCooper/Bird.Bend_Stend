"""Frozen dataclasses and enums of the backend data model and of the GUI-facing API (SW_design §5.1, §5.6,
§6.5, §15.2, §15.6). Pure Python + numpy, no Qt. ``core.api`` re-exports everything the GUI may use.

Names of FW bits/codes come only from the generated ``core.protocol_gen`` (ICD §0.3, GF-11/12): indicator
keys are the generated bit names lower-cased; gate items mirroring a FW BLOCK condition use the
``BLOCK_BITS`` names; SW-only gate codes are the ``GateCode`` StrEnum (checked for collisions by a test).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/model.py @37c87471 (adapted: LinkState, DeviceInfo,
BoardStatus, flag wrappers; new bend-stand status types).

Implements: SW-PLT-003, IF-008, SAF-SW-005 (Indicator states incl. UNKNOWN), SW-RT-005
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import IntFlag, StrEnum
from types import MappingProxyType
from typing import Any, Literal

import numpy as np

from bend_stand.core import protocol_gen as pg

# --------------------------------------------------------------------------------------------- fast flags


class IntBits:
    """Plain-``int`` view of a generated ``IntFlag`` (``IntBits(pg.DataFlags).MOVING`` → ``2``): bit tests with
    ``int & int`` instead of the much slower ``IntFlag`` operators on the hot paths (status rebuild, simulator)."""

    def __init__(self, flag_enum: Any) -> None:
        for m in flag_enum.__members__.values():
            object.__setattr__(self, m.name, int(m))


INT_DF = IntBits(pg.DataFlags)
INT_DS = IntBits(pg.DataStatus)
INT_FA = IntBits(pg.Faults)
INT_IO = IntBits(pg.IoBits)
INT_SYS = IntBits(pg.SysFlags)
INT_FE = IntBits(pg.Features)
INT_BLOCK = IntBits(pg.Block)


# --------------------------------------------------------------------------------------------- link


class LinkState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    LOST = "LOST"


class Compat(IntFlag):
    """Compatibility of the connected FW (IF-008, ICD §9.5). ``OK`` = 0; flags combine."""

    OK = 0
    MINOR_DIFF = 0x01          # FW proto_minor lower than the SW's: features beyond it unused
    MAJOR_MISMATCH = 0x02      # proto_major differs: read-only state
    PAYLOAD_MISMATCH = 0x04    # payload_version differs: read-only, DATA not decoded
    PARAM_HASH_MISMATCH = 0x08  # dictionary hash differs: configuration read-only

    @property
    def read_only(self) -> bool:
        return bool(self & (Compat.MAJOR_MISMATCH | Compat.PAYLOAD_MISMATCH))

    @property
    def config_read_only(self) -> bool:
        return bool(self & (Compat.MAJOR_MISMATCH | Compat.PAYLOAD_MISMATCH | Compat.PARAM_HASH_MISMATCH))


@dataclass(frozen=True)
class DeviceInfo:
    """INFO body, ICD §7.1 (44 B)."""

    proto_major: int
    proto_minor: int
    payload_version: int
    fw_version: tuple[int, int, int]
    param_dict_hash: int
    uid: str                      # 12 bytes as hex, as read
    build: str
    param_count: int
    feature_mask: int

    @property
    def features(self) -> frozenset[str]:
        return frozenset(n for i, n in enumerate(pg.FEATURES_BITS) if n and self.feature_mask >> i & 1)


@dataclass(frozen=True)
class BoardStatus:
    """STATUS body, ICD §7.2 (86 B), all fields decoded (FW-CMD-004)."""

    uptime_ms: int
    t_us: int
    flags: int
    motion_state: int
    status: int
    faults: int
    io: int
    home_phase: int
    halt_src: int
    reset_cause: int
    sys_flags: int
    pos_um: int
    target_um: int
    pos_steps: int
    afe_raw_last: int
    afe_rate_dsps: int
    afe_reinit_count: int
    rx_frames_ok: int
    rx_crc_errors: int
    rx_frame_errors: int
    rx_overruns: int
    tx_drops: int
    event_overflows: int
    loop_max_us: int
    link_age_ms: int
    stack_free_min: int
    nvm_save_ms: int
    idle_disable_left_s: int
    nvm_record_seq: int
    nvm_save_uptime_ms: int
    v_limit_um_s: int
    pause_src: int
    reserved: int = 0

    # typed views over the generated tables
    @property
    def data_flags(self) -> pg.DataFlags:
        return pg.DataFlags(self.flags)

    @property
    def data_status(self) -> pg.DataStatus:
        return pg.DataStatus(self.status)

    @property
    def fault_mask(self) -> pg.Faults:
        return pg.Faults(self.faults)

    @property
    def io_bits(self) -> pg.IoBits:
        return pg.IoBits(self.io)

    @property
    def sys(self) -> pg.SysFlags:
        return pg.SysFlags(self.sys_flags)

    @property
    def motion(self) -> str:
        try:
            return pg.MotionState(self.motion_state).name
        except ValueError:
            return f"UNKNOWN({self.motion_state})"

    @property
    def moving(self) -> bool:
        return bool(self.flags & pg.DataFlags.MOVING)

    @property
    def paused(self) -> bool:
        return bool(self.status & pg.DataStatus.PAUSED)


def bit_names(names: tuple[str, ...], mask: int) -> tuple[str, ...]:
    """Names of the set bits of ``mask`` from a generated ``<ID>_BITS`` tuple (unknown bits → ``BIT<n>``)."""
    out = []
    for i in range(max(len(names), int(mask).bit_length())):
        if int(mask) >> i & 1:
            out.append(names[i] if i < len(names) and names[i] else f"BIT{i}")
    return tuple(out)


@dataclass(frozen=True)
class LinkStats:
    """PC + FW link counters (SW_design §5.2, B3-10). PC counters are cumulative since connect."""

    frames_ok: int = 0
    data_frames: int = 0
    event_frames: int = 0
    response_frames: int = 0
    frames_lost_fw: int = 0
    frames_lost_link: int = 0
    dup_frames: int = 0
    seq_anomalies: int = 0
    events_lost: int = 0
    afe_missed: int = 0
    crc_errors: int = 0
    len_errors: int = 0
    timeout_drops: int = 0
    unknown_type: int = 0
    bad_payload: int = 0
    late_responses: int = 0
    command_timeouts: int = 0
    nacks: int = 0
    retries: int = 0
    async_overflow: int = 0
    tx_frames: int = 0
    # FW counters (newest GET_STATUS), None before the first STATUS
    fw_rx_frames_ok: int | None = None
    fw_rx_crc_errors: int | None = None
    fw_rx_frame_errors: int | None = None
    fw_rx_overruns: int | None = None
    fw_tx_drops: int | None = None
    fw_event_overflows: int | None = None


@dataclass(frozen=True)
class EndpointInfo:
    endpoint: str                          # "COM7" | "sim" | "sim:<file>" | "tcp://127.0.0.1:5760"
    label: str
    kind: Literal["serial", "sim", "tcp"]
    description: str = ""
    is_stlink: bool = False


# --------------------------------------------------------------------------------------------- stop / clear


@dataclass(frozen=True)
class StopResult:
    """Result of the priority path (STOP/HALT/PAUSE). Never raised; ``sent=False`` = not on the wire."""

    cmd: str
    source: str
    sent: bool
    t_write_ns: int | None = None
    error: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class StopConfirmation:
    """Payload of the topics ``stop.confirmed`` / ``stop.unconfirmed`` (SWD-M1-06): which priority command
    (``STOP`` / ``HALT`` / ``PAUSE``) was (not) confirmed, its source, the frames written, the decision time.
    (The v0.3 str-equality shim was removed in M2, MC-4.)"""

    cmd: str
    source: str
    attempts: int
    t_ns: int
    confirmed: bool


ClearOutcome = Literal["OK", "REFUSED", "NOT_CONFIRMED"]


@dataclass(frozen=True)
class ClearResult:
    """HALT_CLEAR / ESTOP_CLEAR / FAULT_CLEAR / RESUME result (D-31, D-34: one frame, never re-sent)."""

    cmd: str
    sent: bool
    confirmed: bool
    outcome: ClearOutcome
    cleared: tuple[str, ...] = ()
    text: str = ""
    nack_status: int | None = None
    nack_detail: int | None = None


# --------------------------------------------------------------------------------------------- gates


class Severity(StrEnum):
    REFUSE = "REFUSE"
    CONFIRM = "CONFIRM"
    WARN = "WARN"


class GateId(StrEnum):
    """Every precomputed gate of SW_design §5.6 (GRQ-B-01)."""

    MOVE = "move"
    JOG = "jog"
    HOME = "home"
    ENABLE = "enable"
    DISABLE = "disable"
    PAUSE = "pause"
    RESUME = "resume"
    CLEAR_STOP = "clear_stop"
    ESTOP_CLEAR = "estop_clear"
    FAULT_CLEAR = "fault_clear"
    STREAM_START = "stream_start"
    STREAM_STOP = "stream_stop"
    RECORD_START = "record_start"
    RECORD_STOP = "record_stop"
    SAMPLE = "sample"
    TARE = "tare"
    CONFIG_WRITE = "config_write"
    CAL_TRAVEL_START = "cal_travel_start"
    CAL_LOAD_START = "cal_load_start"
    SEQUENCE_START = "sequence_start"
    SEQUENCE_EDIT = "sequence_edit"
    VALID_TOGGLE = "valid_toggle"
    TEST_ZERO = "test_zero"
    NO_SPECIMEN = "no_specimen"
    HOTKEY_TEST = "hotkey_test"


class GateCode(StrEnum):
    """SW-only gate item codes (never collide with generated names, GF-11)."""

    LINK_DOWN = "LINK_DOWN"
    COMPAT_READ_ONLY = "COMPAT_READ_ONLY"
    CONFIG_READ_ONLY = "CONFIG_READ_ONLY"
    FEATURE_MISSING = "FEATURE_MISSING"
    STREAM_STALE = "STREAM_STALE"
    STREAM_OFF = "STREAM_OFF"
    STREAM_ALREADY = "STREAM_ALREADY"
    THRESHOLDS_UNVERIFIED = "THRESHOLDS_UNVERIFIED"
    LOAD_INPUT_INVALID = "LOAD_INPUT_INVALID"
    OWNER_CONFLICT = "OWNER_CONFLICT"
    OPERATION_RUNNING = "OPERATION_RUNNING"
    SW_TRIP = "SW_TRIP"
    HOTKEY_TEST = "HOTKEY_TEST"
    HOTKEY_UNAVAILABLE = "HOTKEY_UNAVAILABLE"
    NO_SPECIMEN_MODE = "NO_SPECIMEN_MODE"
    TRAVEL_CAL_DIFFERS = "TRAVEL_CAL_DIFFERS"
    SAF_SW_006_MARGIN = "SAF_SW_006_MARGIN"
    MOTION_ACTIVE = "MOTION_ACTIVE"
    ENABLING_SW = "ENABLING_SW"
    NOTHING_TO_CLEAR = "NOTHING_TO_CLEAR"
    CAUSE_ACTIVE = "CAUSE_ACTIVE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    RECORDING_ACTIVE = "RECORDING_ACTIVE"
    NOT_RECORDING = "NOT_RECORDING"
    REBOOT_PENDING_WARN = "REBOOT_PENDING_WARN"
    IDLE_PAUSE = "IDLE_PAUSE"
    SEQUENCE_RUNNING = "SEQUENCE_RUNNING"
    SEQUENCE_PAUSED = "SEQUENCE_PAUSED"
    # M2 motion
    PC_LOAD_LIMITS_OFF = "PC_LOAD_LIMITS_OFF"
    HOME_LOAD_CONFIRM = "HOME_LOAD_CONFIRM"
    DISABLE_CONFIRM = "DISABLE_CONFIRM"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    TARGET_OUT_OF_RANGE = "TARGET_OUT_OF_RANGE"
    SPEED_CAP = "SPEED_CAP"
    ACCEL_CAP = "ACCEL_CAP"
    BOUND_NOT_AHEAD = "BOUND_NOT_AHEAD"
    LIMIT_TOWARD = "LIMIT_TOWARD"


@dataclass(frozen=True)
class GateItem:
    code: str
    severity: Severity
    text: str
    clear_hint: str | None = None


@dataclass(frozen=True)
class GateResult:
    items: tuple[GateItem, ...] = ()

    @property
    def ok(self) -> bool:
        """True when nothing REFUSEs (CONFIRM items may still need ``confirmed=True``)."""
        return not any(i.severity == Severity.REFUSE for i in self.items)

    @property
    def refused(self) -> tuple[GateItem, ...]:
        return tuple(i for i in self.items if i.severity == Severity.REFUSE)

    @property
    def confirm_items(self) -> tuple[GateItem, ...]:
        return tuple(i for i in self.items if i.severity == Severity.CONFIRM)

    @property
    def warnings(self) -> tuple[GateItem, ...]:
        return tuple(i for i in self.items if i.severity == Severity.WARN)

    @property
    def needs_confirmation(self) -> bool:
        return self.ok and bool(self.confirm_items)

    def codes(self) -> tuple[str, ...]:
        return tuple(i.code for i in self.items)

    def text(self) -> str:
        return "; ".join(i.text for i in self.items)


GATE_OK = GateResult()


# --------------------------------------------------------------------------------------------- indicators


IndicatorState = Literal["ON", "OFF", "UNKNOWN"]


@dataclass(frozen=True)
class Indicator:
    state: IndicatorState
    since_t_us: int | None = None
    source: str | None = None
    value: float | None = None
    clear_hint: str | None = None


INDICATOR_UNKNOWN = Indicator("UNKNOWN")

#: SW-only indicator keys (GF-12); FW keys = generated bit names lower-cased.
SW_INDICATOR_KEYS: tuple[str, ...] = (
    "link_state", "sw_trip", "thresholds_state", "no_specimen_mode", "travel_cal_differs", "afe_synthetic",
    "recording_failed", "hotkey",
)


def fw_indicator_keys() -> tuple[str, ...]:
    """Generated names lower-cased, in table order (DATA flags, DATA status, FAULTS, sys_flags), unique."""
    seen: dict[str, None] = {}
    for table in (pg.DATA_FLAGS_BITS, pg.DATA_STATUS_BITS, pg.FAULTS_BITS, pg.SYS_FLAGS_BITS):
        for n in table:
            if n:
                seen.setdefault(n.lower(), None)
    return tuple(seen)


INDICATOR_KEYS: tuple[str, ...] = fw_indicator_keys() + SW_INDICATOR_KEYS


class Indicators(Mapping[str, Indicator]):
    """Immutable indicator set; items readable as ``ind["paused"]`` or ``ind.paused`` (B3-18)."""

    __slots__ = ("_items",)

    def __init__(self, items: Mapping[str, Indicator] | None = None) -> None:
        base = {k: INDICATOR_UNKNOWN for k in INDICATOR_KEYS}
        if items:
            base.update(items)
        object.__setattr__(self, "_items", MappingProxyType(base))

    def __getitem__(self, key: str) -> Indicator:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getattr__(self, name: str) -> Indicator:
        try:
            return self._items[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value: Any) -> None:  # pragma: no cover - immutability
        raise AttributeError("Indicators is immutable")

    def replace(self, **items: Indicator) -> Indicators:
        d = dict(self._items)
        d.update(items)
        return Indicators(d)

    def __repr__(self) -> str:
        on = [k for k, v in self._items.items() if v.state == "ON"]
        return f"Indicators(on={on})"


# --------------------------------------------------------------------------------------------- status


@dataclass(frozen=True)
class LinkStatus:
    state: LinkState = LinkState.DISCONNECTED
    why: str = ""
    endpoint: str | None = None
    compat: Compat = Compat.OK
    info: DeviceInfo | None = None
    stats: LinkStats = field(default_factory=LinkStats)

    @property
    def read_only(self) -> bool:
        return self.compat.read_only

    @property
    def config_read_only(self) -> bool:
        return self.compat.config_read_only


@dataclass(frozen=True)
class StreamStatus:
    on: bool = False
    rate_sps: float | None = None            # measured (median of 16 periods)
    fw_rate_sps: float | None = None         # STATUS afe_rate_dsps / 10
    rate_mismatch: bool = False
    last_data_age_ms: float | None = None


@dataclass(frozen=True)
class MotionLimits:
    v_travel_mm_s: float
    v_load_mm_s: float
    v_step_rate_mm_s: float
    v_unhomed_mm_s: float
    a_max_mm_s2: float
    loaded: bool
    v_cap_mm_s: float
    v_cap_fw_mm_s: float | None = None
    travel_min_mm: float | None = None       # enabled SW limits ∩ soft limits, machine mm
    travel_max_mm: float | None = None


@dataclass(frozen=True)
class MotionStatus:
    moving: bool = False
    homed: bool = False
    enabled: bool = False
    enabling_left_ms: int = 0
    paused: bool = False
    position_mm: float | None = None
    test_position_mm: float | None = None
    commanded_target_mm: float | None = None
    pending_target_mm: float | None = None
    x_zero_mm: float = 0.0
    owner: str = "MANUAL"
    motion_state: str | None = None
    limits: MotionLimits | None = None
    jogging: bool = False                    # a jog session of this backend is running (M2)
    home_phase: str | None = None            # STATUS home_phase name (M2)
    pos_uncertain: bool = False              # DATA status POS_UNCERTAIN (M2)


@dataclass(frozen=True)
class ThresholdState:
    """FW load-threshold manager state (SAF-SW-002, §6.3)."""

    state: Literal["UNVERIFIED", "VERIFIED", "DEFAULT_ONLY", "FAILED", "INVALID"] = "UNVERIFIED"
    cal_id: str | None = None
    tare_id: str | None = None
    fw_level_n: float | None = None
    raw_min: int | None = None
    raw_max: int | None = None
    zero_raw: int | None = None
    clamped: bool = False
    eff_pull_n: float | None = None
    eff_push_n: float | None = None
    text: str = ""


@dataclass(frozen=True)
class SafetyStatus:
    sw_trip: str | None = None
    warnings: tuple[str, ...] = ()
    thresholds: ThresholdState = field(default_factory=ThresholdState)
    load_input_valid: bool = False
    load_input_reason: str | None = "no calibration"
    no_specimen_mode: bool = False


@dataclass(frozen=True)
class TravelDiffState:
    """Active travel calibration vs board steps/mm (§9.3.1, B3-19; M3)."""

    differs: bool = False
    session_spm: float | None = None
    board_spm: float | None = None
    restore_pending: bool = False
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalibrationStatus:
    load_k: float | None = None
    load_status: str | None = None
    low_span: bool = False
    load_date: str | None = None
    load_valid_for_limits: bool = False
    travel_spm: float | None = None
    travel_date: str | None = None
    board_spm: float | None = None
    travel_cal_differs: bool = False
    restore_pending: bool = False
    travel_diff: TravelDiffState = field(default_factory=TravelDiffState)


@dataclass(frozen=True)
class TareStatus:
    state: str = "NONE"
    tare_raw: float | None = None
    age_s: float | None = None
    tare_id: str | None = None
    can_undo: bool = False


@dataclass(frozen=True)
class SeqStatus:
    state: str = "IDLE"                      # IDLE | RUNNING | PAUSED | ENDED
    step_idx: int | None = None
    loop_iter: int | None = None
    phase: str | None = None
    plan_total_s: float | None = None
    remaining_s: float | None = None
    paused_source: str | None = None
    end_reason: str | None = None


@dataclass(frozen=True)
class OperationStatus:
    owner: str = "MANUAL"                    # MANUAL | SEQUENCE | TRAVEL_CAL | HOMING
    phase: str | None = None
    sequence: SeqStatus = field(default_factory=SeqStatus)


@dataclass(frozen=True)
class RecordingStatus:
    state: Literal["IDLE", "RECORDING", "FAILED"] = "IDLE"
    folder: str | None = None
    rows: int = 0
    rows_lost: int = 0
    queue_fill_pct: float = 0.0
    failure: str | None = None


@dataclass(frozen=True)
class HotkeyStatus:
    mode: Literal["REGISTERED", "LL_HOOK", "UNAVAILABLE"] = "UNAVAILABLE"
    reason: str = "not started"
    test_running: bool = False


@dataclass(frozen=True)
class BackendStatus:
    """Polled snapshot (§15.2); rebuilt on change and returned by reference (immutable)."""

    link: LinkStatus = field(default_factory=LinkStatus)
    stream: StreamStatus = field(default_factory=StreamStatus)
    indicators: Indicators = field(default_factory=Indicators)
    motion: MotionStatus = field(default_factory=MotionStatus)
    safety: SafetyStatus = field(default_factory=SafetyStatus)
    calibration: CalibrationStatus = field(default_factory=CalibrationStatus)
    tare: TareStatus = field(default_factory=TareStatus)
    operation: OperationStatus = field(default_factory=OperationStatus)
    recording: RecordingStatus = field(default_factory=RecordingStatus)
    gates: Mapping[GateId, GateResult] = field(
        default_factory=lambda: MappingProxyType({g: GATE_OK for g in GateId}))
    hotkey: HotkeyStatus = field(default_factory=HotkeyStatus)
    cfg_dirty: bool | None = None
    config_read_only: bool = False
    reboot_pending: bool | None = None
    nvm_defaulted: bool | None = None
    board: BoardStatus | None = None         # newest GET_STATUS (None before the first)
    seq: int = 0                             # snapshot counter (changes on every rebuild)


# --------------------------------------------------------------------------------------------- config


class IssueSeverity(StrEnum):
    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"


@dataclass(frozen=True)
class Issue:
    key: str | None
    severity: IssueSeverity
    code: str
    text: str


class WriteStatus(StrEnum):
    OK = "OK"
    REJECTED = "REJECTED"
    MISMATCH = "MISMATCH"
    BUSY = "BUSY"
    TIMEOUT = "TIMEOUT"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    REBOOT_REQUIRED = "REBOOT_REQUIRED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class WriteItem:
    key: str
    requested: Any
    stored: Any = None
    status: WriteStatus = WriteStatus.NOT_ATTEMPTED
    nack_status: int | None = None
    nack_detail: int | None = None
    text: str = ""


@dataclass(frozen=True)
class VerifyReport:
    items: tuple[WriteItem, ...]
    cfg_dirty: bool | None = None
    reboot_pending: bool | None = None
    issues: tuple[Issue, ...] = ()

    @property
    def ok(self) -> bool:
        good = (WriteStatus.OK, WriteStatus.UNCHANGED, WriteStatus.REBOOT_REQUIRED)
        return not self.issues_errors and all(i.status in good for i in self.items)

    @property
    def issues_errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == IssueSeverity.ERROR)

    def by_key(self) -> Mapping[str, WriteItem]:
        return MappingProxyType({i.key: i for i in self.items})


@dataclass(frozen=True)
class BoardConfigFile:
    """Result of ``load_board_config`` (SW-CFG-002): fills edit fields only."""

    path: str
    values: Mapping[str, Any]
    unknown_keys: tuple[str, ...] = ()
    missing_keys: tuple[str, ...] = ()
    out_of_range: tuple[str, ...] = ()
    hash_mismatch: bool = False
    file_hash: int | None = None
    issues: tuple[Issue, ...] = ()


# --------------------------------------------------------------------------------------------- motion


class MotionKind(StrEnum):
    MOVE = "MOVE"
    JOG = "JOG"
    HOME = "HOME"
    LOAD_APPROACH = "LOAD_APPROACH"


@dataclass(frozen=True)
class MoveDone:
    reason: str                   # MoveDoneReason name
    pos_mm: float
    pos_steps: int
    t_us: int
    stop_cause: str | None = None


@dataclass(frozen=True)
class MoveOutcome:
    """Resolution of a ``MoveTicket``: DONE (``done`` set), REFUSED_PAUSED, REFUSED, CANCELLED, NOT_EXECUTED."""

    kind: Literal["DONE", "REFUSED_PAUSED", "REFUSED", "CANCELLED", "NOT_EXECUTED"]
    done: MoveDone | None = None
    text: str = ""


# --------------------------------------------------------------------------------------------- limits / engines


@dataclass(frozen=True)
class LimitConfig:
    """SW limits (§6.1, SW-LIM-001…003)."""

    travel_min_mm: float | None = None
    travel_min_enabled: bool = False
    travel_max_mm: float | None = None
    travel_max_enabled: bool = False
    pull_trip_n: float = 1961.33
    pull_enabled: bool = True
    push_trip_n: float = -1961.33
    push_enabled: bool = True
    warn_pct: float = 90.0
    fw_level_n: float = 2157.46


@dataclass(frozen=True)
class InputSpec:
    key: str
    label: str
    unit: str = ""
    min: float | None = None
    max: float | None = None
    default: float | None = None


@dataclass(frozen=True)
class ConfirmRequest:
    code: str
    text: str


@dataclass(frozen=True)
class EngineState:
    """Wizard/engine snapshot (§9.1, KD-08)."""

    kind: str
    phase: str
    step_index: int = 0
    step_count: int = 0
    title: str = ""
    instruction: str = ""
    inputs: tuple[InputSpec, ...] = ()
    progress: float | None = None
    stats: Mapping[str, float] | None = None
    result: Any | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    needs_confirmation: ConfirmRequest | None = None
    can_continue: bool = False
    can_repeat: bool = False
    can_cancel: bool = False
    continue_label: str = ""
    continue_moves: bool = False
    abort_reason: str | None = None


@dataclass(frozen=True)
class TestMarks:
    __test__ = False  # not a pytest class
    specimen: str = ""
    number: str = ""
    operator: str = ""
    notes: str = ""
    custom: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SessionSettings:
    """Session settings (§13.5; M3 fills the full set). Never contains tare / no-specimen mode."""

    display_unit: Literal["N", "kgf"] = "N"
    pull_dir: int = 1
    expected_spm: float = 800.0
    recordings_root: str | None = None
    raw_dump: bool = True
    sample_window_s: float = 1.0
    tare_window_s: float = 2.0
    k_est_n_mm: float | None = None
    g_local: float = 9.80665
    limits: LimitConfig = field(default_factory=LimitConfig)
    manual_speed_mm_s: float = 5.0           # default speed of move_to / move_by (≤ the cap that applies, M2)
    manual_accel_mm_s2: float | None = None  # None = the FW default motion.a_max_um_s2 (accel 0 on the wire)


# --------------------------------------------------------------------------------------------- data view


@dataclass(frozen=True)
class SeriesMinMax:
    lo: np.ndarray          # float32[px_width], NaN = no sample in that column
    hi: np.ndarray
    vstate: np.ndarray      # uint8[px_width]: 0 OK, 1 EXTRAPOLATED, 2 INVALID, 3 NO_DATA


@dataclass(frozen=True)
class PlotSnapshot:
    t_end_dev_s: float
    t_col_s: np.ndarray     # float64[px_width], column centres relative to t_end (<= 0)
    series: Mapping[str, SeriesMinMax]
    level: int


@dataclass(frozen=True)
class XYSnapshot:
    x: np.ndarray
    y: np.ndarray
    vstate: np.ndarray
    t_end_dev_s: float


LatestState = Literal["n/a", "STALE", "SATURATED", "INVALID", "EXTRAPOLATED", "OK"]


@dataclass(frozen=True)
class LatestSample:
    key: str
    value: float
    state: LatestState
    t_dev_s: float


@dataclass(frozen=True)
class ChannelSpec:
    """A plottable channel (SW-RT-002). ``dimension`` = physical quantity for the default pane placement of the
    plot panes (GRQ-B-21, SW-RT-006): ``force``, ``length``, ``speed``, ``force_rate``, ``counts``, ``rate``,
    ``count``, ``state``, ``bits``."""

    key: str
    label: str
    unit: str
    group: str
    available: bool = True
    reason: str | None = None
    dimension: str = ""


# --------------------------------------------------------------------------------------------- events


@dataclass(frozen=True)
class EventRecord:
    topic: str
    t_host_ns: int
    payload: Any = None


@dataclass(frozen=True)
class FwEvent:
    """Decoded FW EVENT (topic ``fw.event``)."""

    seq: int
    t_us: int
    code: int
    name: str
    arg: int
    arg_name: str | None
    value: int
    value2: int
    t_host_ns: int = 0


@dataclass(frozen=True)
class ResumeIgnored:
    source: Literal["button", "gui"]
    reason: GateResult
    t_us: int | None = None


@dataclass(frozen=True)
class LinkStateChange:
    state: LinkState
    why: str
    endpoint: str | None = None


Token = int

__all__ = [n for n in dir() if not n.startswith("_") and n not in {
    "annotations", "Iterator", "Mapping", "dataclass", "field", "IntFlag", "StrEnum", "MappingProxyType",
    "Any", "Literal", "np", "pg"}]
