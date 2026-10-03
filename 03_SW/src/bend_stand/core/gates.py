"""Gates — pure functions of a ``GateSnapshot`` (SW_design §5.6), M1 subset.

Every ``GateId`` gets a ``GateResult`` in ``status().gates`` (GRQ-B-01). Items mirroring a FW BLOCK condition use
the generated ``BLOCK_BITS`` names, DATA status items the ``DATA_STATUS_BITS`` names, SW-only items the
``GateCode`` StrEnum (GF-11). M1 implements the link / stream / config / pause / resume / clear / record gates;
motion, calibration, tare, sequence and hotkey gates REFUSE with ``NOT_IMPLEMENTED`` (plus the link items) until
their milestones (M2–M4) — the FW stays the authority in any case.

Implements: SW-ACQ-001 (stream gates), SW-CFG-003/004 (config gate), SW-STOP-003/004 (clear / resume gates,
M1 part), SAF-SW-004 (confirmation items: E-stop clear)
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from bend_stand.core import protocol_gen as pg
from bend_stand.core.model import Compat, GateCode, GateId, GateItem, GateResult, LinkState, Severity

R, C, W = Severity.REFUSE, Severity.CONFIRM, Severity.WARN

CLEAR_HINTS: Mapping[str, str] = MappingProxyType({
    "ESTOP": "release the E-stop, wait ≥ io.estop_release_ms, Clear E-stop, then Enable and Home",
    "HALT": "Clear stop",                       # HALT comes from GUI STOP / Pause/Break only (D-36, GF-19)
    "PAUSED": "motion blocked — Resume (clears PAUSE) or Clear stop",
    "FAULT": "Fault clear when the cause is gone",
    "LOAD_LIMIT": "Fault clear, then move to reduce the load — re-trips if the load grows",
    "K1_WELDED": "contactor K1 did not drop: switch off the driver supply, have K1 checked; Fault clear when "
                 "the E-stop sense and driver power agree again",
    "DRV_PWR": "restore driver power (E-stop released, RESET on K1), then Enable and Home",
    "HOME_DRIFT": "home switch moved: check the switch, Fault clear",
    "LINK_WDG": "clears with the next command frame",
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
    status: int = 0
    faults: int = 0
    io: int = 0
    moving: bool = False
    recording: bool = False
    sequence_paused: bool = False
    hotkey_available: bool = False
    status_known: bool = False


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
    if s.flags & pg.DataFlags.ESTOP:
        out.append(GateItem("ESTOP", R, "E-stop latched", CLEAR_HINTS["ESTOP"]))
    if s.flags & pg.DataFlags.HALT:
        out.append(GateItem("HALT", R, "HALT latched — Clear stop first", CLEAR_HINTS["HALT"]))
    if s.flags & pg.DataFlags.FAULT:
        out.append(GateItem("FAULT", R, "fault latched — clear the fault first", CLEAR_HINTS["FAULT"]))
    return out


def g_resume(s: GateSnapshot) -> GateResult:
    items = _link_items(s) + _ro(s)
    if items:
        return GateResult(tuple(items))
    if not s.status & pg.DataStatus.PAUSED and not s.sequence_paused:
        items.append(GateItem(GateCode.NOTHING_TO_CLEAR, R, "not paused"))
    items += _latched(s)                               # the FW would refuse RESUME (D-31)
    return GateResult(tuple(items))


def g_clear_stop(s: GateSnapshot) -> GateResult:
    items = _link_items(s) + _ro(s)
    if items:
        return GateResult(tuple(items))
    if not s.flags & pg.DataFlags.HALT and not s.status & pg.DataStatus.PAUSED:
        items.append(GateItem(GateCode.NOTHING_TO_CLEAR, R, "nothing to clear (no HALT, no PAUSED)"))
    if s.io & pg.IoBits.STOP_BTN:               # input retired by D-36 / CR-01 (ICD v0.5); kept while the bit exists
        items.append(GateItem("STOP_BTN", R, "STOP input active — the FW refuses the clear"))
    if s.sequence_paused:
        items.append(GateItem(GateCode.SEQUENCE_PAUSED, C, "ends the paused sequence — use Resume to continue it"))
    return GateResult(tuple(items))


def g_estop_clear(s: GateSnapshot) -> GateResult:
    items = _link_items(s) + _ro(s)
    if items:
        return GateResult(tuple(items))
    if not s.flags & pg.DataFlags.ESTOP:
        items.append(GateItem(GateCode.NOTHING_TO_CLEAR, R, "E-stop not latched"))
    elif s.io & pg.IoBits.ESTOP_OPEN:
        items.append(GateItem("ESTOP", R, "E-stop input still open", CLEAR_HINTS["ESTOP"]))
    else:
        items.append(GateItem(GateCode.CAUSE_ACTIVE, C, "button released; driver stays disabled; Enable and re-home"))
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


def g_not_implemented(s: GateSnapshot, what: str, milestone: str) -> GateResult:
    return GateResult(tuple(_link_items(s) + _ro(s) + [
        GateItem(GateCode.NOT_IMPLEMENTED, R, f"{what}: available from {milestone}")]))


_NOT_YET: Mapping[GateId, tuple[str, str]] = MappingProxyType({
    GateId.MOVE: ("motion", "M2"), GateId.JOG: ("jog", "M2"), GateId.HOME: ("homing", "M2"),
    GateId.ENABLE: ("enable", "M2"), GateId.DISABLE: ("disable", "M2"), GateId.SAMPLE: ("take sample", "M3"),
    GateId.TARE: ("tare", "M3"), GateId.CAL_TRAVEL_START: ("travel calibration", "M3"),
    GateId.CAL_LOAD_START: ("load calibration", "M3"), GateId.SEQUENCE_START: ("sequencer", "M4"),
    GateId.SEQUENCE_EDIT: ("sequencer", "M4"), GateId.TEST_ZERO: ("test zero", "M3"),
    GateId.NO_SPECIMEN: ("no-specimen mode", "M3"), GateId.HOTKEY_TEST: ("Pause/Break key test", "M3"),
})


def all_gates(s: GateSnapshot) -> Mapping[GateId, GateResult]:
    out: dict[GateId, GateResult] = {
        GateId.STREAM_START: g_stream_start(s), GateId.STREAM_STOP: g_stream_stop(s),
        GateId.CONFIG_WRITE: g_config_write(s), GateId.PAUSE: g_pause(s), GateId.RESUME: g_resume(s),
        GateId.CLEAR_STOP: g_clear_stop(s), GateId.ESTOP_CLEAR: g_estop_clear(s),
        GateId.FAULT_CLEAR: g_fault_clear(s), GateId.RECORD_START: g_record_start(s),
        GateId.RECORD_STOP: g_record_stop(s), GateId.VALID_TOGGLE: g_valid_toggle(s),
    }
    for gid, (what, ms) in _NOT_YET.items():
        out[gid] = g_not_implemented(s, what, ms)
    return MappingProxyType(out)
