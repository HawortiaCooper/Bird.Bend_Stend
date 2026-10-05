"""GENERATED - do not edit.

Source : 00_System/specs/params.yaml (dict_version 6, schema 1)
Tool   : 00_System/tools/gen_params.py
Hash   : PARAM_DICT_HASH = 0xF8BCDCB8

Parameter metadata for the GUI (typed config fields) and the protocol codec.
f32 min/max/default are stored already rounded to binary32, so read-back values
compare exactly. Pure Python, no Qt, no PyYAML.

Implements: IF-010, SW-CFG-001
"""
from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType

PARAM_DICT_HASH = 0xF8BCDCB8
PARAM_DICT_VERSION = 6
PARAM_SCHEMA_VERSION = 1
PARAM_COUNT = 48


class ParamType(IntEnum):
    """Wire type codes (ICD §7.5)."""

    U8 = 1
    I8 = 2
    U16 = 3
    I16 = 4
    U32 = 5
    I32 = 6
    F32 = 7
    BOOL = 8
    ENUM = 9


TYPE_SIZE: Mapping[ParamType, int] = MappingProxyType({
    ParamType.U8: 1, ParamType.I8: 1, ParamType.U16: 2, ParamType.I16: 2, ParamType.U32: 4,
    ParamType.I32: 4, ParamType.F32: 4, ParamType.BOOL: 1, ParamType.ENUM: 1})
_STRUCT: Mapping[ParamType, str] = MappingProxyType({
    ParamType.U8: "<B", ParamType.I8: "<b", ParamType.U16: "<H", ParamType.I16: "<h",
    ParamType.U32: "<I", ParamType.I32: "<i", ParamType.F32: "<f", ParamType.BOOL: "<B",
    ParamType.ENUM: "<B"})

FLAG_MOVING_OK = 0x01
FLAG_NVM = 0x02
FLAG_REBOOT = 0x04


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


@dataclass(frozen=True, eq=False)
class ParamMeta:
    """Metadata of one parameter (ICD Appendix A, params.yaml)."""

    id: int
    key: str                                  # dotted key, e.g. "afe.gain_channel"
    type: ParamType
    unit: str                                 # ASCII unit, "" = dimensionless
    min: int | float                          # bool: 0, enum: lowest code
    max: int | float                          # bool: 1, enum: highest code
    default: int | float | bool               # bool -> bool, enum -> code, f32 -> binary32-rounded
    description: str
    enum: Mapping[int, str] | None = None     # code -> NAME, iteration order = GUI display order
    group: str = ""                           # GUI group = key prefix ("afe", "motion", ...)
    group_label: str = ""
    label: str = ""                           # short human label
    name: str = ""                            # last key element
    moving_ok: bool = False
    nvm: bool = True
    reboot_required: bool = False
    decimals: int | None = None               # f32 display precision, None for integer types
    advanced: bool = False
    enum_labels: Mapping[int, str] | None = None
    srs: tuple[str, ...] = ()

    @property
    def flags(self) -> int:
        return ((FLAG_MOVING_OK if self.moving_ok else 0) | (FLAG_NVM if self.nvm else 0)
                | (FLAG_REBOOT if self.reboot_required else 0))

    @property
    def size(self) -> int:
        return TYPE_SIZE[self.type]

    def enum_value(self, name: str) -> int:
        """Enum NAME -> code (KeyError if unknown)."""
        for code, n in (self.enum or {}).items():
            if n == name:
                return code
        raise KeyError(f"{self.key}: no enum item {name!r}")

    def in_range(self, value: int | float | bool) -> bool:
        if self.type == ParamType.F32:
            v = float(value)
            return math.isfinite(v) and self.min <= _f32(v) <= self.max
        if isinstance(value, float) and not value.is_integer():
            return False
        if self.type == ParamType.ENUM:
            return int(value) in (self.enum or {})
        return self.min <= int(value) <= self.max

    def pack(self, value: int | float | bool) -> bytes:
        """Value -> 4-byte wire value (ICD §7.5), range-checked (ValueError)."""
        if not self.in_range(value):
            raise ValueError(f"{self.key}: {value!r} outside [{self.min}, {self.max}]")
        v = float(value) if self.type == ParamType.F32 else int(value)
        return struct.pack(_STRUCT[self.type], v).ljust(4, b"\x00")

    def unpack(self, wire: bytes) -> int | float | bool:
        """4-byte wire value -> Python value (bool for BOOL). ValueError on bad padding."""
        if len(wire) != 4 or any(wire[self.size:]):
            raise ValueError(f"{self.key}: bad wire value {bytes(wire).hex()}")
        (v,) = struct.unpack(_STRUCT[self.type], bytes(wire[: self.size]))
        return bool(v) if self.type == ParamType.BOOL else v


def _M(d: dict[int, str]) -> Mapping[int, str]:
    return MappingProxyType(d)


PARAMS: tuple[ParamMeta, ...] = (
    ParamMeta(
        id=0x0101, key='afe.gain_channel', type=ParamType.ENUM, unit='',
        min=0, max=2, default=0,
        description='HX711 input channel and PGA gain. Code = number of SCK pulses per read minus 25 (A128: 25, B32: 26, A64: 27). Applied to the next conversion without reboot; the following afe.settle_discard samples are flagged AFE_SETTLING.',
        enum=_M({0: 'A128', 2: 'A64', 1: 'B32'}),
        group='afe', group_label='HX711 load-cell AFE', label='Channel / PGA gain', name='gain_channel',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=_M({0: 'Channel A, gain 128 (25 pulses)', 2: 'Channel A, gain 64 (27 pulses)', 1: 'Channel B, gain 32 (26 pulses)'}),
        srs=('FW-AFE-002', 'FW-AFE-003', 'FW-PAR-001', 'D-04')),
    ParamMeta(
        id=0x0102, key='afe.rate_sps', type=ParamType.ENUM, unit='',
        min=0, max=1, default=1,
        description='HX711 RATE pin level driven by the MCU GPIO (D-21). Applied without reboot; the following afe.settle_discard samples are flagged AFE_SETTLING. The measured rate is compared with this setting (afe.rate_tol_pct). Hard rule H5 with afe.timeout_ms (SPS10 needs afe.timeout_ms >= 200 ms).',
        enum=_M({0: 'SPS10', 1: 'SPS80'}),
        group='afe', group_label='HX711 load-cell AFE', label='Output data rate', name='rate_sps',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=_M({0: '10 SPS (RATE pin low)', 1: '80 SPS (RATE pin high)'}),
        srs=('FW-AFE-002', 'FW-AFE-004', 'FW-PAR-001', 'D-04', 'D-21', 'D-33')),
    ParamMeta(
        id=0x0103, key='afe.rate_tol_pct', type=ParamType.U8, unit='%',
        min=5, max=50, default=20,
        description='AFE_RATE_MISMATCH is set (status bit + EVENT) when the median of the last 16 DOUT-ready periods deviates from the configured rate by more than this percentage.',
        enum=None,
        group='afe', group_label='HX711 load-cell AFE', label='Rate mismatch tolerance', name='rate_tol_pct',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-AFE-004', 'FW-PAR-001')),
    ParamMeta(
        id=0x0104, key='afe.settle_discard', type=ParamType.U8, unit='samples',
        min=0, max=20, default=4,
        description='After power-up, gain/rate change or HX711 re-initialisation, this many samples are sent with status AFE_SETTLING (they are still checked by the FW load limit).',
        enum=None,
        group='afe', group_label='HX711 load-cell AFE', label='Settle samples flagged', name='settle_discard',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-AFE-003', 'FW-PAR-001')),
    ParamMeta(
        id=0x0105, key='afe.timeout_ms', type=ParamType.U16, unit='ms',
        min=25, max=1000, default=250,
        description='No HX711 sample for this time sets AFE_STALE; while moving this is an immediate stop and latches AFE_FAULT. Motion commands are refused while stale; idle disable is not applied while stale (D-33g). Hard rule H5: >= 2 x the conversion period of afe.rate_sps (SPS10: >= 200 ms, SPS80: >= 25 ms; D-33a, DEF-P1-01). Default 250 ms is valid for both rates.',
        enum=None,
        group='afe', group_label='HX711 load-cell AFE', label='Stale timeout', name='timeout_ms',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-012', 'FW-STR-005', 'FW-PAR-001', 'D-33')),
    ParamMeta(
        id=0x0201, key='motion.steps_per_mm', type=ParamType.F32, unit='steps/mm',
        min=100.0, max=100000.0, default=800.0,
        description='Calibrated drive-train ratio (PO-FW-4). Default 800 = DIP 4000 pulses/rev, closed loop, on the 5 mm lead (D-27 closed: PO approved the DIP change; direct coupling ASSUMED until the first travel calibration). Changeable only while idle; HOMED is kept (machine zero is a step count) and every um position rescales: um = round_half_away(steps * 1000 / steps_per_mm). Minimum 100 (FW timer/ramp resolution, FW_design OI-FW-05; SRS proposed 1).',
        enum=None,
        group='motion', group_label='Motion / step generation', label='Steps per mm', name='steps_per_mm',
        moving_ok=False, nvm=True, reboot_required=False, decimals=4, advanced=False,
        enum_labels=None,
        srs=('FW-MOT-002', 'FW-MOT-009', 'FW-PAR-002', 'PO-FW-4', 'D-27')),
    ParamMeta(
        id=0x0202, key='motion.pul_invert', type=ParamType.BOOL, unit='',
        min=0, max=1, default=False,
        description='false = PUL idle low, step pulse = high level (opto LED current). Reboot required: a live change would create a PUL edge that the driver could count as a step.',
        enum=None,
        group='motion', group_label='Motion / step generation', label='PUL polarity inverted', name='pul_invert',
        moving_ok=False, nvm=True, reboot_required=True, decimals=None, advanced=True,
        enum_labels=None,
        srs=('FW-MOT-001', 'FW-PAR-002', 'D-17')),
    ParamMeta(
        id=0x0203, key='motion.dir_invert', type=ParamType.BOOL, unit='',
        min=0, max=1, default=False,
        description='false = DIR pin high while moving in +x (away from the START/home switch). Set at the hardware gate so that +x moves away from START (DIP SW5 = negative direction, D-27). Applied while idle (DIR changes only with the step timer stopped).',
        enum=None,
        group='motion', group_label='Motion / step generation', label='DIR polarity inverted', name='dir_invert',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-MOT-001', 'FW-PAR-002', 'D-27')),
    ParamMeta(
        id=0x0204, key='motion.ena_invert', type=ParamType.BOOL, unit='',
        min=0, max=1, default=False,
        description='false = ENA pin high = opto LED current = driver DISABLED; pin low / no current = enabled (HBS86H, common cathode, D-17, A-04). Reboot required: a live change would release or energise a holding driver.',
        enum=None,
        group='motion', group_label='Motion / step generation', label='ENA polarity inverted', name='ena_invert',
        moving_ok=False, nvm=True, reboot_required=True, decimals=None, advanced=True,
        enum_labels=None,
        srs=('FW-MOT-001', 'FW-MOT-008', 'FW-PAR-002', 'D-13')),
    ParamMeta(
        id=0x0205, key='motion.pulse_high_ns', type=ParamType.U32, unit='ns',
        min=2500, max=100000, default=12500,
        description='Step pulse active width. Hard rule H3 with max_step_rate_hz and pulse_low_min_ns.',
        enum=None,
        group='motion', group_label='Motion / step generation', label='PUL high width', name='pulse_high_ns',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-MOT-001', 'FW-PAR-002', 'D-16')),
    ParamMeta(
        id=0x0206, key='motion.pulse_low_min_ns', type=ParamType.U32, unit='ns',
        min=2500, max=100000, default=12500,
        description='Minimum inactive time between two step pulses. Hard rule H3.',
        enum=None,
        group='motion', group_label='Motion / step generation', label='PUL minimum low width', name='pulse_low_min_ns',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-MOT-001', 'FW-PAR-002', 'D-16')),
    ParamMeta(
        id=0x0207, key='motion.max_step_rate_hz', type=ParamType.U32, unit='Hz',
        min=100, max=100000, default=40000,
        description='Pulse-rate cap. The effective speed limit of every motion command is min(v_max_*, max_step_rate_hz * 1000 / steps_per_mm) um/s (FW-MOT-009); reported as STATUS v_limit_um_s. Hard rule H3. Maximum 100 kHz (FW_design OI-FW-04; SRS cap 200 kHz).',
        enum=None,
        group='motion', group_label='Motion / step generation', label='Maximum step rate', name='max_step_rate_hz',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-MOT-001', 'FW-MOT-009', 'FW-PAR-002', 'D-16')),
    ParamMeta(
        id=0x0208, key='motion.dir_setup_us', type=ParamType.U16, unit='us',
        min=5, max=1000, default=20,
        description='Minimum time between a DIR change and the first active PUL edge.',
        enum=None,
        group='motion', group_label='Motion / step generation', label='DIR setup time', name='dir_setup_us',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-MOT-001', 'FW-PAR-002', 'D-16')),
    ParamMeta(
        id=0x0209, key='motion.ena_settle_ms', type=ParamType.U16, unit='ms',
        min=0, max=2000, default=500,
        description='After ENABLE (ENA asserted) and after driver power returns, no PUL/DIR edge for this time; ENABLED is set when it has elapsed. Default 500 ms per R5 (Leadshine sequence chart, D-28; SRS Table 5.1 said 200 ms).',
        enum=None,
        group='motion', group_label='Motion / step generation', label='ENA settle time', name='ena_settle_ms',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-MOT-008', 'FW-PAR-002', 'D-28')),
    ParamMeta(
        id=0x020A, key='motion.v_max_travel_um_s', type=ParamType.U32, unit='um/s',
        min=1, max=250000, default=30000,
        description='Speed cap of every motion command when the axis is unloaded (abs(raw - safety.zero_raw) < safety.release_band_raw at command time). Replaces SRS motion.v_max_um_s together with v_max_load_um_s (R5 §2.3). Hard rule H4.',
        enum=None,
        group='motion', group_label='Motion / step generation', label='Max speed (travel)', name='v_max_travel_um_s',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-020', 'FW-PAR-003', 'D-19', 'R5 §2.3')),
    ParamMeta(
        id=0x020B, key='motion.v_max_load_um_s', type=ParamType.U32, unit='um/s',
        min=1, max=250000, default=20000,
        description='Speed cap of MOVE_UNTIL_LOAD and of every motion command accepted while abs(raw - safety.zero_raw) >= safety.release_band_raw. Hard rule H4 (<= v_max_travel).',
        enum=None,
        group='motion', group_label='Motion / step generation', label='Max speed (under load)', name='v_max_load_um_s',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-020', 'FW-PAR-003', 'D-19', 'R5 §2.3')),
    ParamMeta(
        id=0x020C, key='motion.a_max_um_s2', type=ParamType.U32, unit='um/s2',
        min=1000, max=10000000, default=100000,
        description='Acceleration cap of motion commands and the value used when a command carries accel = 0.',
        enum=None,
        group='motion', group_label='Motion / step generation', label='Max acceleration', name='a_max_um_s2',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-020', 'FW-PAR-003', 'R4 §2.1')),
    ParamMeta(
        id=0x020D, key='motion.a_stop_um_s2', type=ParamType.U32, unit='um/s2',
        min=10000, max=10000000, default=1000000,
        description='Deceleration of controlled stops (link watchdog, PAUSE, jog dead-man, STOP mode 1, JOG 0).',
        enum=None,
        group='motion', group_label='Motion / step generation', label='Controlled-stop deceleration', name='a_stop_um_s2',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-003', 'FW-PAR-003', 'R4 §4.2')),
    ParamMeta(
        id=0x020E, key='motion.v_unhomed_um_s', type=ParamType.U32, unit='um/s',
        min=1, max=20000, default=2000,
        description='Speed cap of JOG while the axis is not homed.',
        enum=None,
        group='motion', group_label='Motion / step generation', label='Max speed un-homed', name='v_unhomed_um_s',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-020', 'FW-MOT-005', 'FW-PAR-003', 'R4 §3.2')),
    ParamMeta(
        id=0x020F, key='motion.jog_timeout_ms', type=ParamType.U16, unit='ms',
        min=50, max=1000, default=250,
        description='JOG motion continues only while a JOG command arrives at least this often; otherwise controlled stop (STOP cause JOG_DEADMAN). The SW refreshes every <= 100 ms.',
        enum=None,
        group='motion', group_label='Motion / step generation', label='Jog dead-man timeout', name='jog_timeout_ms',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-016', 'FW-PAR-003', 'R1 §10.3')),
    ParamMeta(
        id=0x0301, key='limits.soft_min_um', type=ParamType.I32, unit='um',
        min=-10000, max=399999, default=500,
        description='Lower FW travel limit (machine coordinate), active when homed. MOVE_ABS / MOVE_UNTIL_LOAD targets below -> E_RANGE; JOG stops here. Hard rule H1 (max is one below the soft_max_um max so that every range end is reachable).',
        enum=None,
        group='limits', group_label='Soft travel limits', label='Soft limit min', name='soft_min_um',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-020', 'FW-PAR-004', 'R4 §3.2')),
    ParamMeta(
        id=0x0302, key='limits.soft_max_um', type=ParamType.I32, unit='um',
        min=0, max=400000, default=290000,
        description='Upper FW travel limit (machine coordinate), active when homed. Hard rule H1.',
        enum=None,
        group='limits', group_label='Soft travel limits', label='Soft limit max', name='soft_max_um',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-020', 'FW-PAR-004', 'D-19')),
    ParamMeta(
        id=0x0402, key='home.v_fast_um_s', type=ParamType.U32, unit='um/s',
        min=10, max=20000, default=5000,
        description='Fast seek speed toward the START (home) switch (-x).',
        enum=None,
        group='home', group_label='Homing / zeroing', label='Homing fast speed', name='v_fast_um_s',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-HOM-001', 'FW-PAR-004')),
    ParamMeta(
        id=0x0403, key='home.v_slow_um_s', type=ParamType.U32, unit='um/s',
        min=10, max=20000, default=500,
        description='Release and slow-approach speed (edge capture).',
        enum=None,
        group='home', group_label='Homing / zeroing', label='Homing slow speed', name='v_slow_um_s',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-HOM-001', 'FW-PAR-004')),
    ParamMeta(
        id=0x0404, key='home.a_um_s2', type=ParamType.U32, unit='um/s2',
        min=1000, max=1000000, default=50000,
        description='Acceleration of all homing moves.',
        enum=None,
        group='home', group_label='Homing / zeroing', label='Homing acceleration', name='a_um_s2',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-HOM-001', 'FW-PAR-004')),
    ParamMeta(
        id=0x0405, key='home.backoff_um', type=ParamType.U32, unit='um',
        min=0, max=20000, default=2000,
        description='Travel away from the switch after release, before the slow approach.',
        enum=None,
        group='home', group_label='Homing / zeroing', label='Homing back-off', name='backoff_um',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-HOM-001', 'FW-PAR-004')),
    ParamMeta(
        id=0x0406, key='home.offset_um', type=ParamType.U32, unit='um',
        min=0, max=20000, default=1000,
        description='Machine zero lies this far from the captured START switch edge on the travel side (START edge at x = -offset_um).',
        enum=None,
        group='home', group_label='Homing / zeroing', label='Home offset', name='offset_um',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-HOM-001', 'FW-PAR-004')),
    ParamMeta(
        id=0x0407, key='home.max_travel_um', type=ParamType.U32, unit='um',
        min=1000, max=500000, default=360000,
        description='No switch within this travel -> HOME_NOT_FOUND. Also bounds un-homed JOG / HOME travel to the un-homed origin ± this value (D-43 b: origin latched when the axis became un-homed; a JOG toward a reached bound -> E_RANGE until homed).',
        enum=None,
        group='home', group_label='Homing / zeroing', label='Homing max travel', name='max_travel_um',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-HOM-002', 'FW-MOT-005', 'FW-PAR-004')),
    ParamMeta(
        id=0x0408, key='home.max_load_raw', type=ParamType.I32, unit='counts',
        min=0, max=7151121, default=322123,
        description='HOME is refused (E_CONFIRM) when abs(raw - safety.zero_raw) exceeds this value, unless the command carries the operator-confirmed flag. Default 5 % FS.',
        enum=None,
        group='home', group_label='Homing / zeroing', label='Homing max load', name='max_load_raw',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-021', 'FW-PAR-005', 'D-15')),
    ParamMeta(
        id=0x0409, key='home.drift_tol_um', type=ParamType.U32, unit='um',
        min=10, max=10000, default=200,
        description='HOME of an already homed axis compares the captured switch edge with its expected position (-offset_um); a deviation above this value latches fault HOME_DRIFT (lost steps since the previous homing; data since then suspect). Homing still completes. R5 §8 (SRS delta).',
        enum=None,
        group='home', group_label='Homing / zeroing', label='Home drift tolerance', name='drift_tol_um',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-HOM-001', 'R5 §8')),
    ParamMeta(
        id=0x0501, key='safety.load_raw_max', type=ParamType.I32, unit='counts',
        min=-7151120, max=7151121, default=7022271,
        description='Every HX711 sample above this value counts as a load-limit violation (D-12). Session value written by the PC after calibration/tare (SAF-SW-002); default +110 % FS minus the 1 % FS zero-balance allowance. Range cap +-(110 % FS + 1 % FS); outside -> E_RANGE, never clamped. Effective from the next sample. Hard rule H2 (min is one above the load_raw_min min so that every range end is reachable).',
        enum=None,
        group='safety', group_label='FW safety', label='FW load limit max (raw)', name='load_raw_max',
        moving_ok=True, nvm=False, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-008', 'SAF-FW-010', 'FW-PAR-005', 'D-12')),
    ParamMeta(
        id=0x0502, key='safety.load_raw_min', type=ParamType.I32, unit='counts',
        min=-7151121, max=7151120, default=-7022271,
        description='Every HX711 sample below this value counts as a load-limit violation. Session value (see load_raw_max). Hard rule H2 (max is one below the load_raw_max max).',
        enum=None,
        group='safety', group_label='FW safety', label='FW load limit min (raw)', name='load_raw_min',
        moving_ok=True, nvm=False, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-008', 'SAF-FW-010', 'FW-PAR-005', 'D-12')),
    ParamMeta(
        id=0x0503, key='safety.load_trip_samples', type=ParamType.U8, unit='samples',
        min=1, max=4, default=1,
        description='Consecutive violating samples that trip the FW load limit (immediate stop, latch LOAD_LIMIT). Rail samples (saturation) always count as violations.',
        enum=None,
        group='safety', group_label='FW safety', label='Load trip samples', name='load_trip_samples',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-008', 'SAF-FW-009', 'FW-PAR-005')),
    ParamMeta(
        id=0x0504, key='safety.load_regrow_raw', type=ParamType.I32, unit='counts',
        min=0, max=1288490, default=128849,
        description='After FAULT_CLEAR of LOAD_LIMIT with the load still beyond the threshold, the FW re-trips when the violation grows by more than this value beyond the value at clear. Default 2 % FS.',
        enum=None,
        group='safety', group_label='FW safety', label='Load re-trip growth', name='load_regrow_raw',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-011', 'FW-PAR-005')),
    ParamMeta(
        id=0x0505, key='safety.zero_raw', type=ParamType.I32, unit='counts',
        min=-8388608, max=8388607, default=0,
        description='Raw value of zero load (the PC writes tare_raw, session value). Used by the homing load check, the idle-disable release band and the v_max_load selection.',
        enum=None,
        group='safety', group_label='FW safety', label='Zero (tare) raw', name='zero_raw',
        moving_ok=True, nvm=False, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-017', 'SAF-FW-021', 'FW-PAR-005', 'D-15')),
    ParamMeta(
        id=0x0506, key='safety.release_band_raw', type=ParamType.I32, unit='counts',
        min=0, max=644245, default=128849,
        description='abs(raw - zero_raw) below this value = unloaded (idle disable allowed, v_max_travel applies). Default 2 % FS.',
        enum=None,
        group='safety', group_label='FW safety', label='Unloaded band', name='release_band_raw',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-017', 'FW-PAR-005', 'D-15')),
    ParamMeta(
        id=0x0507, key='safety.idle_disable_s', type=ParamType.U16, unit='s',
        min=0, max=65535, default=600,
        description='After this time without motion and with the axis unloaded, the FW disables the driver (ENA), clears HOMED and sends EVENT DRIVER_DISABLED (cause IDLE). 0 = never.',
        enum=None,
        group='safety', group_label='FW safety', label='Idle disable time', name='idle_disable_s',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-017', 'FW-PAR-005', 'D-15')),
    ParamMeta(
        id=0x0508, key='safety.link_timeout_ms', type=ParamType.U16, unit='ms',
        min=200, max=5000, default=1000,
        description='While moving, no CRC-valid command frame for this time -> controlled stop, LINK_WDG, VALID cleared (ICD §9.1). Values below 3 x the 250 ms heartbeat gap are not robust.',
        enum=None,
        group='safety', group_label='FW safety', label='Link watchdog timeout', name='link_timeout_ms',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-015', 'FW-PAR-005', 'D-15')),
    ParamMeta(
        id=0x0601, key='io.release_ms', type=ParamType.U8, unit='ms',
        min=5, max=200, default=20,
        description='A limit switch or the PAUSE button counts as released after a stable inactive level for this time (1 kHz sampling). Activation acts on the first edge (no delay). (SRS name input.release_ms.)',
        enum=None,
        group='io', group_label='Inputs (switches / buttons)', label='Release debounce', name='release_ms',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-SW-001', 'FW-SW-003', 'SAF-FW-013', 'FW-PAR-006', 'D-36')),
    ParamMeta(
        id=0x0602, key='io.estop_release_ms', type=ParamType.U16, unit='ms',
        min=50, max=2000, default=100,
        description='ESTOP_CLEAR is accepted only after the E-stop sense input has been closed continuously for this time. (SRS name input.estop_release_ms.)',
        enum=None,
        group='io', group_label='Inputs (switches / buttons)', label='E-stop release time', name='estop_release_ms',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-006', 'FW-SW-002', 'FW-PAR-006')),
    ParamMeta(
        id=0x0604, key='io.pause_active_level', type=ParamType.ENUM, unit='',
        min=0, max=1, default=1,
        description='Physical PAUSE button contact type (NO momentary default, R5 §5.5).',
        enum=_M({0: 'OPEN_ACTIVE', 1: 'CLOSED_ACTIVE'}),
        group='io', group_label='Inputs (switches / buttons)', label='PAUSE button contact', name='pause_active_level',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=_M({0: 'NC contact: open / high = pressed', 1: 'NO contact: closed / low = pressed'}),
        srs=('FW-SW-003', 'SAF-FW-023', 'FW-PAR-006', 'D-26', 'D-28')),
    ParamMeta(
        id=0x0701, key='drv.alm_active_level', type=ParamType.ENUM, unit='',
        min=0, max=1, default=0,
        description='ALM input polarity. Default: output conducting = OK, open / high = alarm, unpowered or wire broken (A-09, R5 §3.1). Reported in DATA/STATUS; no automatic stop of a running move (D-16); new motion is refused while ALM is active and driver power is present (E_STATE DRIVER_ALARM, D-28).',
        enum=_M({0: 'HIGH_ACTIVE', 1: 'LOW_ACTIVE'}),
        group='drv', group_label='Driver signals (ALM / PEND / power)', label='ALM active level', name='alm_active_level',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=_M({0: 'pin high = alarm (Leadshine default, fail-safe)', 1: 'pin low = alarm'}),
        srs=('FW-SW-004', 'FW-PAR-006', 'D-16', 'D-28')),
    ParamMeta(
        id=0x0702, key='drv.pend_active_level', type=ParamType.ENUM, unit='',
        min=0, max=1, default=0,
        description='PEND input polarity (high impedance = in position, A-09). PEND is meaningful only while ALM is inactive and the driver is powered.',
        enum=_M({0: 'HIGH_ACTIVE', 1: 'LOW_ACTIVE'}),
        group='drv', group_label='Driver signals (ALM / PEND / power)', label='PEND active level', name='pend_active_level',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=_M({0: 'pin high = in position (Leadshine default)', 1: 'pin low = in position'}),
        srs=('FW-SW-004', 'FW-PAR-006', 'D-16')),
    ParamMeta(
        id=0x0703, key='drv.pend_timeout_ms', type=ParamType.U16, unit='ms',
        min=0, max=5000, default=200,
        description='After the last pulse of a move, PEND is expected within this time; otherwise EVENT NOT_SETTLED (warning only, no reaction, D-16). 0 = check off. R5 §3.4 (SRS delta).',
        enum=None,
        group='drv', group_label='Driver signals (ALM / PEND / power)', label='PEND timeout', name='pend_timeout_ms',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-SW-004', 'R5 §8')),
    ParamMeta(
        id=0x0704, key='drv.pwr_sense_enable', type=ParamType.BOOL, unit='',
        min=0, max=1, default=False,
        description='OPTIONAL since CR-03 / D-41 (no power-removal device in release 1; default false): true = the DRV_POWER input (PA7, optional 48 V presence sense, closed = powered, fixed fail-safe polarity, D-28) is evaluated: power off -> immediate stop, not enabled, not homed, motion refused (E_STATE DRV_UNPOWERED); after power returns ENABLE waits motion.ena_settle_ms; the K1_WELDED check additionally needs drv.k1_check_enable (SRS OI-18). false (default): the input is ignored, status DRV_PWR reads 1 (power assumed present) and K1_WELDED is never detected.',
        enum=None,
        group='drv', group_label='Driver signals (ALM / PEND / power)', label='Driver power sense', name='pwr_sense_enable',
        moving_ok=False, nvm=True, reboot_required=True, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-005', 'D-28', 'D-29', 'R5 §1.5')),
    ParamMeta(
        id=0x0705, key='drv.k1_weld_ms', type=ParamType.U16, unit='ms',
        min=100, max=2000, default=200,
        description="With drv.pwr_sense_enable and drv.k1_check_enable, the E-stop sense input open while the DRV_POWER input still reports power continuously for longer than this time latches fault K1_WELDED (power-removal device stuck closed or its feedback miswired; D-29c, R5 §1.5). Must exceed the device's opening time incl. the feedback delay. Minimum 100 ms (DILM7-class drop-out plus margin).",
        enum=None,
        group='drv', group_label='Driver signals (ALM / PEND / power)', label='K1 weld detection time', name='k1_weld_ms',
        moving_ok=False, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-005', 'D-29', 'R5 §1.5')),
    ParamMeta(
        id=0x0706, key='drv.k1_check_enable', type=ParamType.BOOL, unit='',
        min=0, max=1, default=False,
        description='SRS OI-18 (CR-03 / D-41): true = the K1_WELDED check runs (E-stop sense open while DRV_POWER stays present > drv.k1_weld_ms -> fault K1_WELDED); only effective together with drv.pwr_sense_enable, and only meaningful with a power-removal device whose feedback contact feeds DRV_POWER. false (default, release 1 has no such device): DRV_POWER present during an E-stop is only reported (status DRV_PWR, EVENT DRIVER_POWER), never a fault — otherwise a plain 48 V presence sense would latch K1_WELDED on every E-stop.',
        enum=None,
        group='drv', group_label='Driver signals (ALM / PEND / power)', label='K1 weld check', name='k1_check_enable',
        moving_ok=False, nvm=True, reboot_required=True, decimals=None, advanced=False,
        enum_labels=None,
        srs=('SAF-FW-005', 'D-29', 'D-41', 'CR-03')),
    ParamMeta(
        id=0x0801, key='stream.fallback_hz', type=ParamType.U8, unit='Hz',
        min=1, max=80, default=10,
        description='While the AFE is stale and the stream is on, DATA frames marked NO_AFE_DATA (afe_raw = 0x80000000) are sent at this rate so position and status stay visible.',
        enum=None,
        group='stream', group_label='Data stream', label='Fallback frame rate', name='fallback_hz',
        moving_ok=True, nvm=True, reboot_required=False, decimals=None, advanced=False,
        enum_labels=None,
        srs=('FW-STR-005', 'IF-007', 'FW-PAR-006')),
)

BY_ID: Mapping[int, ParamMeta] = MappingProxyType({p.id: p for p in PARAMS})
BY_KEY: Mapping[str, ParamMeta] = MappingProxyType({p.key: p for p in PARAMS})

# GUI grouping in display order: (group key, group label, param keys in yaml order)
GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ('afe', 'HX711 load-cell AFE',
     ('afe.gain_channel', 'afe.rate_sps', 'afe.rate_tol_pct', 'afe.settle_discard', 'afe.timeout_ms')),
    ('motion', 'Motion / step generation',
     ('motion.steps_per_mm', 'motion.pul_invert', 'motion.dir_invert', 'motion.ena_invert', 'motion.pulse_high_ns', 'motion.pulse_low_min_ns', 'motion.max_step_rate_hz', 'motion.dir_setup_us', 'motion.ena_settle_ms', 'motion.v_max_travel_um_s', 'motion.v_max_load_um_s', 'motion.a_max_um_s2', 'motion.a_stop_um_s2', 'motion.v_unhomed_um_s', 'motion.jog_timeout_ms')),
    ('limits', 'Soft travel limits',
     ('limits.soft_min_um', 'limits.soft_max_um')),
    ('home', 'Homing / zeroing',
     ('home.v_fast_um_s', 'home.v_slow_um_s', 'home.a_um_s2', 'home.backoff_um', 'home.offset_um', 'home.max_travel_um', 'home.max_load_raw', 'home.drift_tol_um')),
    ('safety', 'FW safety',
     ('safety.load_raw_max', 'safety.load_raw_min', 'safety.load_trip_samples', 'safety.load_regrow_raw', 'safety.zero_raw', 'safety.release_band_raw', 'safety.idle_disable_s', 'safety.link_timeout_ms')),
    ('io', 'Inputs (switches / buttons)',
     ('io.release_ms', 'io.estop_release_ms', 'io.pause_active_level')),
    ('drv', 'Driver signals (ALM / PEND / power)',
     ('drv.alm_active_level', 'drv.pend_active_level', 'drv.pend_timeout_ms', 'drv.pwr_sense_enable', 'drv.k1_weld_ms', 'drv.k1_check_enable')),
    ('stream', 'Data stream',
     ('stream.fallback_hz',)),
)

assert len(PARAMS) == PARAM_COUNT == len(BY_ID) == len(BY_KEY)
