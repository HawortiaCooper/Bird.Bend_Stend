"""Gates — pure functions of a ``GateSnapshot`` (SW_design §5.6), M2 subset.

Every ``GateId`` gets a ``GateResult`` in ``status().gates`` (GRQ-B-01). Items mirroring a FW BLOCK condition use
the generated ``BLOCK_BITS`` names, DATA status items the ``DATA_STATUS_BITS`` names, SW-only items the
``GateCode`` StrEnum (GF-11). The REFUSE items mirror the FW BLOCK mask so the GUI greys a control before the FW
would NACK it; the FW stays the authority.

M2: link / stream / config / pause / resume / clear / record gates (M1) plus the **motion gates** ``move``,
``jog``, ``home``, ``enable``, ``disable`` and ``test_zero``. Status bits of a feature whose GET_INFO bit is 0 are
invalid (D-37 b): the snapshot carries them already masked (``core.device.valid_status_mask``), so DRV_UNPOWERED /
DRIVER_ALARM are never derived from them. Calibration, tare, sequence and SW load-limit gates are M3/M4
(``NOT_IMPLEMENTED``); until M3 every motion gate carries a WARN that the PC load limits are not active (the FW load
limit is). Direction-dependent items (LIMIT toward a switch, SW travel limits, speed caps) are added by
``MotionController.check()``.

Implements: SW-ACQ-001 (stream gates), SW-CFG-003/004 (config gate), SW-STOP-003/004 (clear / resume gates),
SAF-SW-004 (confirmation items: HOME under unknown load, DISABLE, E-stop clear), SAF-SW-005 (motion refusals
mirror the FW state), SW-MAN-001…006 (motion gates)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import (
    INT_DF, INT_DS, INT_IO, Compat, GateCode, GateId, GateItem, GateResult, LinkState, MotionKind, Severity,
)

R, C, W = Severity.REFUSE, Severity.CONFIRM, Severity.WARN
DF, DS, IO = INT_DF, INT_DS, INT_IO       # plain ints (fast)

CLEAR_HINTS: Mapping[str, str] = MappingProxyType({
    "ESTOP": "release the red E-stop button, press RESET (K1), wait ≥ io.estop_release_ms, Clear E-stop, then "
             "Enable and Home",
    "HALT": "Clear stop",                       # HALT comes from GUI STOP / Pause/Break only (D-36, GF-19)
    "PAUSED": "motion blocked — Resume (clears PAUSE) or Clear stop",
    "FAULT": "Fault clear when the cause is gone",
    "LOAD_LIMIT": "Fault clear, then move to reduce the load — re-trips if the load grows",
    "K1_WELDED": "contactor K1 did not drop: switch off the driver supply, have K1 checked; Fault clear when "
                 "the E-stop sense and driver power agree again",
    "DRV_PWR": "restore driver power (E-stop released, RESET on K1), then Enable and Home",
    "DRV_UNPOWERED": "restore driver power (E-stop released, RESET on K1), then Enable and Home",
    "DRIVER_ALARM": "driver alarm: new motion blocked — power-cycle the driver (E-stop + RESET), then Enable and "
                    "Home",
    "HOME_DRIFT": "home switch moved: check the switch, Fault clear",
    "LINK_WDG": "clears with the next command frame",
    "NOT_ENABLED": "Enable the driver",
    "NOT_HOMED": "Home the axis",
    "LIMIT": "only motion away from the active limit switch is accepted",
    "AFE_STALE": "no HX711 samples — check the AFE",
    "AFE_SATURATED": "HX711 at a rail — unload / check the cell",
})


def clear_procedure(code: str) -> str | None:
    return CLEAR_HINTS.get(code)


@dataclass(frozen=True)
class GateSnapshot:
    link: LinkState = LinkState.DISCONNECTED
    compat: Compat = Compat.OK
    stream_on: bool = False
    data_fresh: bool = False
    flags: int = 0
    status: int = 0                        # DATA / STATUS status bits, already masked to the valid ones (D-37 b)
    faults: int = 0
    io: int = 0                            # STATUS io bits, masked likewise
    moving: bool = False
    recording: bool = False
    sequence_paused: bool = False
    hotkey_available: bool = False
    status_known: bool = False
    # M2
    features: frozenset[str] = field(default_factory=frozenset)
    valid_status: int = 0xFFFF             # mask of the valid status bits (D-37 b)
    motion_state: str | None = None
    thresholds_state: str = "UNVERIFIED"
    hotkey_test: bool = False
    jogging: bool = False                  # a jog session of this backend is running
    raw: float | None = None               # newest raw counts (None = no sample)
    zero_raw: int = 0
    home_max_load_raw: int = 322_123
    load_known: bool = False               # a valid load calibration + tare exist (M3)


def _link_items(s: GateSnapshot) -> list[GateItem]:
    if s.link not in (LinkState.CONNECTED, LinkState.DEGRADED):
        return [GateItem(GateCode.LINK_DOWN, R, f"not connected ({s.link.value.lower()})")]
    return []


def _ro(s: GateSnapshot) -> list[GateItem]:
    if s.compat.read_only:
        return [GateItem(GateCode.COMPAT_READ_ONLY, R, "incompatible FW version: read-only")]
    return []


def g_stream_start(s: GateSnapshot) -> GateResult:
    items = _link_items(s)
    if not items and s.stream_on:
        items.append(GateItem(GateCode.STREAM_ALREADY, R, "stream already on"))
    return GateResult(tuple(items))


def g_stream_stop(s: GateSnapshot) -> GateResult:
    items = _link_items(s)
    if not items and not s.stream_on:
        items.append(GateItem(GateCode.STREAM_ALREADY, R, "stream already off"))
    if s.moving:
        items.append(GateItem(GateCode.MOTION_ACTIVE, R, "moving: the PC limits need the data stream"))
    return GateResult(tuple(items))


def g_config_write(s: GateSnapshot) -> GateResult:
    items = _link_items(s)
    if not items and s.compat.config_read_only:
        items.append(GateItem(GateCode.CONFIG_READ_ONLY, R, "configuration read-only (dictionary / version "
                              "mismatch)"))
    if s.moving:
        items.append(GateItem(GateCode.MOTION_ACTIVE, W, "moving: only parameters marked M can be written"))
    return GateResult(tuple(items))


def g_pause(s: GateSnapshot) -> GateResult:
    items = _link_items(s) + _ro(s)
    if not items and not s.moving and not s.sequence_paused:
        items.append(GateItem(GateCode.IDLE_PAUSE, W, "no motion running — sets PAUSED only"))
    return GateResult(tuple(items))


def _latched(s: GateSnapshot) -> list[GateItem]:
    out = []
    if s.flags & DF.ESTOP:
        out.append(GateItem("ESTOP", R, "E-stop latched", CLEAR_HINTS["ESTOP"]))
    if s.flags & DF.HALT:
        out.append(GateItem("HALT", R, "HALT latched — Clear stop first", CLEAR_HINTS["HALT"]))
    if s.flags & DF.FAULT:
        out.append(GateItem("FAULT", R, "fault latched — clear the fault first", CLEAR_HINTS["FAULT"]))
    return out


def g_resume(s: GateSnapshot) -> GateResult:
    items = _link_items(s) + _ro(s)
    if items:
        return GateResult(tuple(items))
    if not s.status & DS.PAUSED and not s.sequence_paused:
        items.append(GateItem(GateCode.NOTHING_TO_CLEAR, R, "not paused"))
    items += _latched(s)                               # the FW would refuse RESUME (D-31)
    return GateResult(tuple(items))


def g_clear_stop(s: GateSnapshot) -> GateResult:
    items = _link_items(s) + _ro(s)
    if items:
        return GateResult(tuple(items))
    if not s.flags & DF.HALT and not s.status & DS.PAUSED:
        items.append(GateItem(GateCode.NOTHING_TO_CLEAR, R, "nothing to clear (no HALT, no PAUSED)"))
    if s.sequence_paused:
        items.append(GateItem(GateCode.SEQUENCE_PAUSED, C, "ends the paused sequence — use Resume to continue it"))
    return GateResult(tuple(items))


def g_estop_clear(s: GateSnapshot) -> GateResult:
    items = _link_items(s) + _ro(s)
    if items:
        return GateResult(tuple(items))
    if not s.flags & DF.ESTOP:
        items.append(GateItem(GateCode.NOTHING_TO_CLEAR, R, "E-stop not latched"))
    elif s.io & IO.ESTOP_OPEN:
        items.append(GateItem("ESTOP", R, "E-stop input still open", CLEAR_HINTS["ESTOP"]))
    else:
        items.append(GateItem(GateCode.CAUSE_ACTIVE, C, "button released and K1 reset; the driver stays disabled: "
                                                        "Enable and re-home"))
    return GateResult(tuple(items))


def g_fault_clear(s: GateSnapshot) -> GateResult:
    items = _link_items(s) + _ro(s)
    if items:
        return GateResult(tuple(items))
    if not s.faults:
        items.append(GateItem(GateCode.NOTHING_TO_CLEAR, R, "no fault latched"))
    for i, n in enumerate(pg.FAULTS_BITS):
        if s.faults >> i & 1:
            items.append(GateItem(n, W, f"{n} latched", CLEAR_HINTS.get(n, CLEAR_HINTS["FAULT"])))
    return GateResult(tuple(items))


def g_record_start(s: GateSnapshot) -> GateResult:
    items = []
    if s.recording:
        items.append(GateItem(GateCode.RECORDING_ACTIVE, R, "already recording"))
    if not s.stream_on:
        items.append(GateItem(GateCode.STREAM_OFF, W, "stream off: the recording stays empty until it starts"))
    return GateResult(tuple(items))


def g_record_stop(s: GateSnapshot) -> GateResult:
    return GateResult(() if s.recording else (GateItem(GateCode.NOT_RECORDING, R, "not recording"),))


def g_valid_toggle(s: GateSnapshot) -> GateResult:
    return GateResult(tuple(_link_items(s) + _ro(s)))


# ============================================================================================ motion (M2)

def _drv_power_off(s: GateSnapshot) -> bool:
    """DRV_UNPOWERED only from a **valid** DRV_PWR bit (D-37 b)."""
    return bool(s.valid_status & DS.DRV_PWR) and not s.status & DS.DRV_PWR


def _feature_items(s: GateSnapshot, *names: str) -> list[GateItem]:
    return [GateItem(GateCode.FEATURE_MISSING, R, f"not supported by this FW build ({n})")
            for n in names if n not in s.features]


def motion_items(s: GateSnapshot, kind: MotionKind) -> list[GateItem]:
    """REFUSE / WARN items common to MOVE, JOG, HOME (SW_design §5.6); direction-free."""
    items = _link_items(s) + _ro(s)
    if items:
        return items
    items += _feature_items(s, "MOTION", *(("HOMING",) if kind == MotionKind.HOME else ()),
                            *(("MOVE_UNTIL_LOAD",) if kind == MotionKind.LOAD_APPROACH else ()))
    if not s.stream_on or not s.data_fresh:
        items.append(GateItem(GateCode.STREAM_STALE, R, "data stream off or no DATA for 500 ms (the PC limits need "
                                                        "it)"))
    items += _latched(s)
    if s.status & DS.PAUSED:
        items.append(GateItem("PAUSED", R, "paused — press Resume (clears PAUSE)", CLEAR_HINTS["PAUSED"]))
    if s.flags & DF.ENABLED:
        pass                                           # DATA is authoritative; STATUS motion_state may be older
    elif s.motion_state == "ENABLING":
        items.append(GateItem(GateCode.ENABLING_SW, R, "driver settling after ENABLE"))
    else:
        items.append(GateItem("NOT_ENABLED", R, "driver not enabled", CLEAR_HINTS["NOT_ENABLED"]))
    if kind in (MotionKind.MOVE, MotionKind.LOAD_APPROACH) and not s.flags & DF.HOMED:
        items.append(GateItem("NOT_HOMED", R, "axis not homed", CLEAR_HINTS["NOT_HOMED"]))
    if s.status & DS.AFE_STALE:
        items.append(GateItem("AFE_STALE", R, "HX711 stale", CLEAR_HINTS["AFE_STALE"]))
    if s.status & DS.AFE_SATURATED:
        items.append(GateItem("AFE_SATURATED", R, "HX711 saturated", CLEAR_HINTS["AFE_SATURATED"]))
    if _drv_power_off(s):
        items.append(GateItem("DRV_UNPOWERED", R, "driver unpowered — position lost", CLEAR_HINTS["DRV_UNPOWERED"]))
    elif s.valid_status & DS.ALM and s.status & DS.ALM and not (kind == MotionKind.JOG and s.jogging):
        items.append(GateItem("DRIVER_ALARM", R, "driver alarm (ALM) — new motion blocked",
                              CLEAR_HINTS["DRIVER_ALARM"]))
    if s.thresholds_state not in ("VERIFIED", "DEFAULT_ONLY"):
        items.append(GateItem(GateCode.THRESHOLDS_UNVERIFIED, R, f"FW load thresholds not verified "
                              f"({s.thresholds_state}) — Recheck thresholds"))
    if s.hotkey_test:
        items.append(GateItem(GateCode.HOTKEY_TEST, R, "Pause/Break key test running"))
    if s.moving and (kind == MotionKind.HOME or (kind == MotionKind.JOG and not s.jogging)):
        items.append(GateItem(GateCode.MOTION_ACTIVE, R, "axis moving"))
    if s.status & (DS.LIMIT_START | DS.LIMIT_END):
        items.append(GateItem("LIMIT", W, "limit switch active — only motion away from it", CLEAR_HINTS["LIMIT"]))
    items.append(GateItem(GateCode.PC_LOAD_LIMITS_OFF, W, "PC load limits (SAF-SW-001) active from M3 — the FW load "
                                                          "limit is active"))
    return items


def g_motion(s: GateSnapshot, kind: MotionKind) -> GateResult:
    items = motion_items(s, kind)
    if kind == MotionKind.HOME and not any(i.severity == R for i in items):
        over = s.raw is not None and abs(s.raw - s.zero_raw) > s.home_max_load_raw
        if over:
            items.append(GateItem(GateCode.HOME_LOAD_CONFIRM, C, "load above the homing limit (5 % FS) — confirm "
                                                                 "homing under load (SAF-SW-004)"))
        elif not s.load_known:
            items.append(GateItem(GateCode.HOME_LOAD_CONFIRM, C, "load unknown (no load calibration / tare) — "
                                                                 "confirm that no specimen is loaded"))
    return GateResult(tuple(items))


def g_enable(s: GateSnapshot) -> GateResult:
    items = _link_items(s) + _ro(s)
    if items:
        return GateResult(tuple(items))
    items += _feature_items(s, "MOTION")
    if s.flags & DF.ESTOP or s.io & IO.ESTOP_OPEN:
        items.append(GateItem("ESTOP", R, "E-stop latched or pressed", CLEAR_HINTS["ESTOP"]))
    if _drv_power_off(s):
        items.append(GateItem("DRV_UNPOWERED", R, "driver unpowered", CLEAR_HINTS["DRV_UNPOWERED"]))
    return GateResult(tuple(items))


def g_disable(s: GateSnapshot) -> GateResult:
    items = _link_items(s) + _ro(s)
    if items:
        return GateResult(tuple(items))
    items += _feature_items(s, "MOTION")
    if s.moving:
        items.append(GateItem(GateCode.MOTION_ACTIVE, R, "axis moving — stop first"))
    elif s.motion_state == "NOT_ENABLED":
        items.append(GateItem(GateCode.NOTHING_TO_CLEAR, R, "driver already disabled"))
    if not any(i.severity == R for i in items):
        items.append(GateItem(GateCode.DISABLE_CONFIRM, C, "specimen unloaded? The driver stops holding and the "
                                                           "axis loses its home (SAF-SW-004)"))
    return GateResult(tuple(items))


def g_test_zero(s: GateSnapshot) -> GateResult:
    items = _link_items(s)
    if not items:
        if not s.flags & DF.HOMED:
            items.append(GateItem("NOT_HOMED", R, "axis not homed", CLEAR_HINTS["NOT_HOMED"]))
        if s.moving:
            items.append(GateItem(GateCode.MOTION_ACTIVE, R, "axis moving"))
    return GateResult(tuple(items))


def g_hotkey_test(s: GateSnapshot) -> GateResult:
    items: list[GateItem] = []
    if s.moving:
        items.append(GateItem(GateCode.MOTION_ACTIVE, R, "axis moving"))
    if not s.hotkey_available:
        items.append(GateItem(GateCode.HOTKEY_UNAVAILABLE, R, "Pause/Break key unavailable"))
    if s.hotkey_test:
        items.append(GateItem(GateCode.HOTKEY_TEST, R, "test already running"))
    return GateResult(tuple(items))


def g_not_implemented(s: GateSnapshot, what: str, milestone: str) -> GateResult:
    return GateResult(tuple(_link_items(s) + _ro(s) + [
        GateItem(GateCode.NOT_IMPLEMENTED, R, f"{what}: available from {milestone}")]))


_NOT_YET: Mapping[GateId, tuple[str, str]] = MappingProxyType({
    GateId.SAMPLE: ("take sample", "M3"), GateId.TARE: ("tare", "M3"),
    GateId.CAL_TRAVEL_START: ("travel calibration", "M3"), GateId.CAL_LOAD_START: ("load calibration", "M3"),
    GateId.SEQUENCE_START: ("sequencer", "M4"), GateId.SEQUENCE_EDIT: ("sequencer", "M4"),
    GateId.NO_SPECIMEN: ("no-specimen mode", "M3"),
})


def all_gates(s: GateSnapshot) -> Mapping[GateId, GateResult]:
    out: dict[GateId, GateResult] = {
        GateId.STREAM_START: g_stream_start(s), GateId.STREAM_STOP: g_stream_stop(s),
        GateId.CONFIG_WRITE: g_config_write(s), GateId.PAUSE: g_pause(s), GateId.RESUME: g_resume(s),
        GateId.CLEAR_STOP: g_clear_stop(s), GateId.ESTOP_CLEAR: g_estop_clear(s),
        GateId.FAULT_CLEAR: g_fault_clear(s), GateId.RECORD_START: g_record_start(s),
        GateId.RECORD_STOP: g_record_stop(s), GateId.VALID_TOGGLE: g_valid_toggle(s),
        GateId.MOVE: g_motion(s, MotionKind.MOVE), GateId.JOG: g_motion(s, MotionKind.JOG),
        GateId.HOME: g_motion(s, MotionKind.HOME), GateId.ENABLE: g_enable(s), GateId.DISABLE: g_disable(s),
        GateId.TEST_ZERO: g_test_zero(s), GateId.HOTKEY_TEST: g_hotkey_test(s),
    }
    for gid, (what, ms) in _NOT_YET.items():
        out[gid] = g_not_implemented(s, what, ms)
    return MappingProxyType(out)
