"""Backend facade — the only object the GUI constructs (SW_design §15), M1 subset (WP-B11).

Threads on the real clock: Reader, Pipeline, Supervisor (5 ms tick: link, heartbeat, confirmations, polls),
Worker (generator jobs), Recorder, simulator board (``sim`` endpoint). With ``BackendSettings(clock="lockstep",
test_hooks=True)`` **no thread is started**: ``test_hooks.advance(ms, step_ms=1)`` advances the
``LockstepClock`` and runs per step, in this order: simulator → (transport delivery) → Reader → Pipeline →
Supervisor → Worker → Recorder (§12.4 hook a). Fully deterministic.

M1: connect / disconnect (``sim``, ``sim:<file>``, ``COMx`` only on the operator's choice (D-06), ``tcp://``),
stream, configuration (read / check / write+verify / NVM / reboot / board-config file), STOP / HALT / PAUSE
(priority path), RESUME and the clears (VERIFY), recording skeleton, ``status()`` with link / stream / indicators
(UNKNOWN handling) / sys_flags / gates, data view and channel registry.
M2 (WP-B12/B13): ``backend.motion`` = ``core.motion.MotionController`` (enable / disable / home / move_to /
move_by / jog / test zero, motion gates), SW travel limits (``limits.set``), the FW load-threshold manager with the
default and manual-raw paths (``core.safety``), the system-wide Pause/Break → HALT key (``io.win_hotkey``) with its
test mode, feature-dependent indicators UNKNOWN (D-37 b).
M3 (§22b): calibration store + session + marks (files under ``<data>``), ``LoadInput`` (active load calibration,
session tare, no-specimen mode), pipeline scaling + derived channels, ``SafetySupervisor`` (SAF-SW-001 on every
frame, before the sinks), the automatic FW-threshold rewrite whenever the SAF-SW-002 target changes, ``CaptureHub``,
tare / load-calibration / travel-calibration engines (``terminate_all`` on stops, pauses, latches, link loss),
take-sample, the complete recorder (derived columns, SW event rows, snapshot sidecar, failure handling).
M4 (§22c): ``backend.sequencer`` (model / validation / plan / path / generators / files, ``check_start`` / ``start``
/ controls) over ``core.sequencer.executor.SequenceExecutor`` (own runner: thread on the real clock, stepped after the
Worker on the lock-step clock; owner ``SEQUENCE``; pause / resume / terminate routing, RESUME_REQUEST, ALM, recording
failure), ``backend.reports`` (``core.report``), ``safety.trip_cleared`` (MC3-5).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/backend.py @37c87471 (facade pattern; rewritten for §15).

Implements: SW-PLT-003, SW-ACQ-001, SW-ACQ-002 (record start/stop), SW-CFG-001…004 (API), SW-STOP-001/002
(backend part), SAF-SW-005 (indicators incl. UNKNOWN, D-37 b), NFR-002 (priority path from the GUI thread),
SW-MAN-001…006 / SW-LIM-001 (M2 backend part), SW-STOP-002 (hotkey), SAF-SW-002, SAF-SW-001, SAF-SW-004
(no-specimen confirmation), SAF-SW-005 (M3 indicators), SAF-SW-006, SW-LIM-001…004, SW-META-001/002, SW-ACQ-002…004,
SW-CAL-001…009 (API), SW-TARE-001…003 (API), SW-RT-002/004 (channel availability), SW-SEQ-001…007 (API),
SW-STOP-004 (sequence pause / resume), SW-WIZ-001/002, SW-SEQF-001, SW-SCH-001/002, SW-REP-001…004 (API)
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np

from bend_stand import __version__
from bend_stand.core import metadata as marks_mod
from bend_stand.core import params_gen as pgen
from bend_stand.core import paths
from bend_stand.core import protocol_gen as pg
from bend_stand.core import session as session_mod
from bend_stand.core.calibration.load import LoadCalEngine
from bend_stand.core.calibration.store import CalibrationStore
from bend_stand.core.calibration.travel import TravelCalEngine
from bend_stand.core.capture import CaptureHub, CaptureResult
from bend_stand.core.channels import ChannelRegistry
from bend_stand.core.clock import MONOTONIC, Clock, LockstepClock, wall_utc_iso
from bend_stand.core.dataview import DataView
from bend_stand.core.device import Device, DeviceSettings
from bend_stand.core.errors import ConfirmationRequired, FileFormatError, GateRefused, RecorderError
from bend_stand.core.events import EventBus
from bend_stand.core.gates import CLEAR_HINTS, GateSnapshot, all_gates
from bend_stand.core.jobs import Job, Worker
from bend_stand.core.link import SUPERVISOR_TICK_NS
from bend_stand.core.liveness import LivenessMonitor
from bend_stand.core.loadinput import LoadInput
from bend_stand.core.motion import MotionController
from bend_stand.core.model import (
    INT_DF, INT_DS, INT_SYS,
    INDICATOR_UNKNOWN, BackendStatus, BoardConfigFile, CalibrationStatus, ClearResult, DeviceInfo, EndpointInfo,
    GateCode, GateId, GateItem, GateResult, HotkeyStatus, Indicator, Indicators, Issue, IssueSeverity,
    LimitConfig, LinkState, LinkStatus, MotionStatus, OperationStatus, ResumeIgnored, SafetyStatus, SampleRow,
    SeqStatus, SessionSettings, Severity, StopResult, StreamStatus, TareStatus, TestMarks, ThresholdState,
    TravelDiffState, VerifyReport,
)
from bend_stand.core.observers import ReleasingFuture, failed_future
from bend_stand.core.params import LOCKED_KEYS, check_edits, load_board_config, save_board_config
from bend_stand.core.pipeline import DataRow, Pipeline
from bend_stand.core.recorder import Recorder, append_sample
from bend_stand.core.safety import SafetyInputs, SafetySupervisor
from bend_stand.core.tare import TareEngine
from bend_stand.calc.path import PathPoint
from bend_stand.core import report as report_mod
from bend_stand.core.model import RecordingInfo, ReportPaths, ReportResult, StepResult
from bend_stand.core.sequencer import seqfile
from bend_stand.core.sequencer.executor import SequenceExecutor
from bend_stand.core.sequencer.generators import GeneratorSchema, SchemaContext, generator_schemas
from bend_stand.core.sequencer.generators import generate as seq_generate
from bend_stand.core.sequencer.model import Block, Sequence, SeqIssue, StepKind, ValidationContext
from bend_stand.core.sequencer.model import validate as seq_validate
from bend_stand.core.sequencer.plan import Plan, PlanContext
from bend_stand.core.sequencer.plan import expand as seq_expand
from bend_stand.core.sequencer.plan import planned_path as seq_planned_path
from bend_stand.io import ports
from bend_stand.io.transport import LINK_BYTES_PER_S, Transport, parse_endpoint, transport_factory

log = logging.getLogger("bend_stand.core.backend")
MS = 1_000_000
DATA_STALE_NS = 500 * MS
STATUS_STALE_NS = 1500 * MS
TWIN_ENDPOINT = f"tcp://127.0.0.1:{pg.TWIN_TCP_PORT}"
SIM_SERVER_ENDPOINT = "tcp://127.0.0.1:5770"


@dataclass(frozen=True)
class BackendSettings:
    clock: Literal["real", "lockstep"] = "real"
    test_hooks: bool = False
    wire_log: bool = False
    session_path: str | None = None
    recordings_root: str | None = None
    stream_on_connect: bool = True
    auto_reconnect: bool = True
    heartbeat: bool = True
    seq_seed: int | None = None
    sim_bytes_per_s: float | None = LINK_BYTES_PER_S
    sim_latency_ns: int = 0
    sim_nvm_path: str | None = None
    hotkey: Literal["auto", "win32", "fake", "off"] = "auto"    # auto: env BEND_STAND_HOTKEY, else win32
    data_dir: str | None = None              # <data> root (calibration, sessions, presets); env BEND_STAND_DATA_DIR


def _not_implemented(what: str, milestone: str) -> GateResult:
    return GateResult((GateItem(GateCode.NOT_IMPLEMENTED, Severity.REFUSE, f"{what}: available from {milestone}"),))


# ============================================================================== sub-APIs

class ConfigAPI:
    """``backend.config`` (SW-CFG-001…004)."""

    def __init__(self, be: Backend) -> None:
        self._be = be

    def metas(self) -> tuple[pgen.ParamMeta, ...]:
        return pgen.PARAMS

    def groups(self) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        return pgen.GROUPS

    def values(self) -> Mapping[str, Any]:
        return self._be.device.params.values()

    def locked_keys(self) -> frozenset[str]:
        return LOCKED_KEYS

    def check(self, edits: Mapping[str, Any]) -> list[Issue]:
        d = self._be.device
        return check_edits(d.params.values(), edits, moving=bool(d.last_flags & INT_DF.MOVING))

    def write_and_verify_async(self, edits: Mapping[str, Any]) -> Future[VerifyReport]:
        return self._be._job(self._be.device.write_and_verify_job, dict(edits))  # noqa: SLF001

    def read_all_async(self) -> Future[Mapping[str, Any]]:
        return self._be._job(self._be.device.read_all_params_job)  # noqa: SLF001

    def save_async(self) -> Future[None]:
        return self._be._job(self._be.device.save_job)  # noqa: SLF001

    def load_async(self) -> Future[Mapping[str, Any]]:
        return self._be._job(self._be.device.load_job)  # noqa: SLF001

    def defaults_async(self) -> Future[Mapping[str, Any]]:
        return self._be._job(self._be.device.default_job)  # noqa: SLF001

    def reboot_async(self) -> Future[None]:
        return self._be._job(self._be.device.reboot_job)  # noqa: SLF001

    def save_board_config(self, path: str, values: Mapping[str, Any] | None = None) -> None:
        d = self._be.device
        info = d.info
        save_board_config(path, d.params.values() if values is None else values, sw_version=__version__,
                          fw_version=".".join(map(str, info.fw_version)) if info else "",
                          board_uid=info.uid if info else "", saved_utc=wall_utc_iso(self._be.clock))

    def load_board_config(self, path: str) -> BoardConfigFile:
        return load_board_config(path)


class LimitsAPI:
    """``backend.limits``: SW travel + load limits and the FW load-limit level (SW-LIM-001…003; stored in the session),
    FW load thresholds (SAF-SW-002: default / manual raw / calibrated), no-specimen mode (SW-LIM-004)."""

    def __init__(self, be: Backend) -> None:
        self._be = be

    def get(self) -> LimitConfig:
        return self._be.session.get().limits

    def check(self, cfg: LimitConfig) -> list[Issue]:
        """SW-LIM-001 (travel inside the FW soft limits, min < max) + SW-LIM-002 (load trips, warning level, FW level
        ≤ 110 % FS and ≥ the enabled SW trips) + the SAF-SW-001 rule that a load limit may be switched off only while
        the load input is valid."""
        out: list[Issue] = list(session_mod.validate_limits(cfg))
        be = self._be
        st = be.load_input.evaluate(be.device.params.values(), be.device.info)
        if (not cfg.pull_enabled or not cfg.push_enabled) and not st.valid:
            old = self.get()
            if (old.pull_enabled and not cfg.pull_enabled) or (old.push_enabled and not cfg.push_enabled):
                out.append(Issue("pull_enabled" if not cfg.pull_enabled else "push_enabled", IssueSeverity.ERROR,
                                 "LOAD_INPUT_INVALID", f"a load limit can be switched off only with a valid load "
                                                       f"input ({st.reason}); use the no-specimen mode"))
        vals = self._be.device.params.values()
        lo = vals.get("limits.soft_min_um")
        hi = vals.get("limits.soft_max_um")
        E = IssueSeverity.ERROR
        for name, en, v in (("travel_min_mm", cfg.travel_min_enabled, cfg.travel_min_mm),
                            ("travel_max_mm", cfg.travel_max_enabled, cfg.travel_max_mm)):
            if not en:
                continue
            if v is None:
                out.append(Issue(name, E, "MISSING", f"{name}: enabled without a value"))
            elif (lo is not None and v * 1000.0 < lo) or (hi is not None and v * 1000.0 > hi):
                out.append(Issue(name, E, "OUTSIDE_SOFT_LIMITS", f"{name} = {v:g} mm outside the FW soft limits "
                                 f"{(lo or 0) / 1000:g}…{(hi or 0) / 1000:g} mm"))
        if cfg.travel_min_enabled and cfg.travel_max_enabled and cfg.travel_min_mm is not None and \
                cfg.travel_max_mm is not None and cfg.travel_min_mm >= cfg.travel_max_mm:
            out.append(Issue("travel_min_mm", E, "ORDER", "travel min must be < travel max"))
        return out

    def set(self, cfg: LimitConfig) -> list[Issue]:
        """Apply the limits (refused while moving); returns the ERROR issues (empty = applied). Stored in the session
        (SW-LIM-003); a changed FW level rewrites the FW thresholds (automatic recheck, SAF-SW-002)."""
        if self._be.device.last_flags & INT_DF.MOVING:
            return [Issue(None, IssueSeverity.ERROR, "MOVING", "limits cannot be changed while the axis moves")]
        issues = [i for i in self.check(cfg) if i.severity == IssueSeverity.ERROR]
        if not issues:
            old = self.get()
            self._be.session.apply_limits(cfg)
            if old != cfg:
                self._be.record_event("LIMITS", _limits_text(cfg))
                self._be.events.publish("log", {"text": "SW limits set: " + _limits_text(cfg)})
        return issues

    def thresholds(self) -> ThresholdState:
        return self._be.device.thresholds

    def recheck_async(self) -> Future[ThresholdState]:
        return self._be.thresholds_job()

    def set_manual_thresholds_async(self, raw_min: int, raw_max: int, zero_raw: int = 0) -> Future[ThresholdState]:
        """M2 bring-up path: operator-entered raw thresholds, written and verified (state VERIFIED, cal_id
        ``manual-raw``). Refused while moving."""
        if self._be.device.last_flags & INT_DF.MOVING:
            return failed_future(GateRefused(GateResult((GateItem(GateCode.MOTION_ACTIVE, Severity.REFUSE,
                                                                  "axis moving"),))))
        issues = self._be.device.threshold_mgr.set_manual(raw_min, raw_max, zero_raw)
        if issues:
            return failed_future(GateRefused(GateResult(tuple(GateItem(i.code, Severity.REFUSE, i.text)
                                                              for i in issues))))
        return self.recheck_async()

    def set_default_thresholds_async(self) -> Future[ThresholdState]:
        self._be.device.threshold_mgr.set_default()
        return self.recheck_async()

    def set_no_specimen_mode(self, on: bool, *, confirmed: bool = False) -> GateResult:
        """SW-LIM-004: enter (CONFIRM C-10, SAF-SW-004) / leave the no-specimen mode (session only)."""
        be = self._be
        li = be.load_input
        if bool(on) == li.no_specimen:
            return GATE_OK_
        if not on:
            if be.device.last_flags & INT_DF.MOVING or be.motion.busy:
                return GateResult((GateItem(GateCode.MOTION_ACTIVE, Severity.REFUSE, "axis moving"),))
            be.set_no_specimen(False)
            return GATE_OK_
        g = be._gates()[GateId.NO_SPECIMEN]  # noqa: SLF001
        if not g.ok:
            return g
        if g.confirm_items and not confirmed:
            return GateResult(g.items + (GateItem(GateCode.CONFIRMATION_REQUIRED, Severity.REFUSE,
                                                  "confirm: " + g.confirm_items[0].text),))
        be.set_no_specimen(True)
        return g


GATE_OK_ = GateResult()


def _limits_text(c: LimitConfig) -> str:
    def tr(v: float | None, en: bool) -> str:
        return f"{v:g} mm" if en and v is not None else "off"
    return (f"travel {tr(c.travel_min_mm, c.travel_min_enabled)}…{tr(c.travel_max_mm, c.travel_max_enabled)}; "
            f"pull {c.pull_trip_n:g} N {'on' if c.pull_enabled else 'off'}; push {c.push_trip_n:g} N "
            f"{'on' if c.push_enabled else 'off'}; warn {c.warn_pct:g} %; FW level {c.fw_level_n:g} N")


class MarksAPI:
    """``backend.marks`` (SW-META-001/002): marks with custom fields, presets, edits while recording."""

    def __init__(self, be: Backend) -> None:
        self._be = be
        self._marks = TestMarks()

    def get(self) -> TestMarks:
        return self._marks

    def set(self, marks: TestMarks) -> None:
        errs = marks_mod.check_marks(marks)
        if errs:
            raise ValueError("; ".join(errs))
        old, self._marks = self._marks, marks
        be = self._be
        if be.recorder.state == "RECORDING" and old != marks:
            for key, a, b in _mark_diff(old, marks):
                be.recorder.mark_edits.append({"key": key, "old": a, "new": b, "utc": wall_utc_iso(be.clock)})
                be.record_event("MARK_EDIT", f"{key}: {a!r} -> {b!r}")
                be.events.publish("marks.edited", {"key": key, "old": a, "new": b})

    def presets_dir(self) -> Path:
        return paths.presets_dir(self._be.data_dir)

    def list_presets(self) -> list[str]:
        return marks_mod.list_presets(self.presets_dir())

    def save_preset(self, path: str) -> None:
        p = Path(path)
        if not p.is_absolute() and p.parent == Path("."):
            p = self.presets_dir() / (p.name if p.name.endswith(marks_mod.SUFFIX) else p.name + marks_mod.SUFFIX)
        marks_mod.save_preset(p, self._marks)

    def load_preset(self, path: str) -> TestMarks:
        m = marks_mod.load_preset(path)
        self.set(m)
        return m

    def delete_preset(self, path: str) -> None:
        """GRQ-B-27: delete a preset file (only ``*.bbmarks.json``)."""
        p = Path(path)
        if not p.name.endswith(marks_mod.SUFFIX):
            raise ValueError("not a marks preset file")
        p.unlink()


def _mark_diff(a: TestMarks, b: TestMarks) -> list[tuple[str, str, str]]:
    out = [(k, getattr(a, k), getattr(b, k)) for k in ("specimen", "number", "operator", "notes")
           if getattr(a, k) != getattr(b, k)]
    ca, cb = dict(a.custom), dict(b.custom)
    for k in sorted(set(ca) | set(cb)):
        if ca.get(k) != cb.get(k):
            out.append((f"custom.{k}", ca.get(k, ""), cb.get(k, "")))
    return out


class SessionAPI:
    """``backend.session`` (§13.5, SRS §5.2): validated settings, applied at start, auto-saved on every change."""

    def __init__(self, be: Backend) -> None:
        self._be = be
        self._s = SessionSettings()
        self.path: Path | None = None
        self.load_issues: list[Issue] = []

    def get(self) -> SessionSettings:
        return self._s

    def check(self, settings: SessionSettings) -> list[Issue]:
        return [i for i in session_mod.validate(settings) if i.severity == IssueSeverity.ERROR] + \
            [i for i in self._be.limits.check(settings.limits) if i.severity == IssueSeverity.ERROR]

    def set(self, settings: SessionSettings) -> list[Issue]:
        issues = self.check(settings)
        if issues:
            return issues
        if settings.limits != self._s.limits and self._be.device.last_flags & INT_DF.MOVING:
            return [Issue("limits", IssueSeverity.ERROR, "MOVING", "limits cannot be changed while the axis moves")]
        self._s = settings
        self._after_change()
        return []

    def apply_limits(self, cfg: LimitConfig) -> None:
        self._s = replace(self._s, limits=cfg)
        self._after_change()

    def _after_change(self) -> None:
        self._autosave()
        self._be.on_scale_changed()

    def _autosave(self) -> None:
        if self.path is None:
            return
        try:
            session_mod.save(self.path, self._s)
        except OSError as exc:  # never fatal: the settings stay active for this run
            log.warning("session auto-save failed: %s", exc)

    def load(self, path: str) -> SessionSettings:
        s, self.load_issues = session_mod.load(path)
        errs = [i for i in self._be.limits.check(s.limits) if i.severity == IssueSeverity.ERROR
                and i.code != "LOAD_INPUT_INVALID"]
        if errs:
            raise FileFormatError(f"{path}: " + "; ".join(i.text for i in errs))
        self._s = s
        self._after_change()
        return s

    def save(self, path: str) -> None:
        session_mod.save(path, self._s)

    def load_default(self, path: Path) -> None:
        """At start: the default / configured session file (missing → defaults; broken → defaults + log)."""
        self.path = path
        if not path.exists():
            return
        try:
            s, self.load_issues = session_mod.load(path)
            self._s = s
        except FileFormatError as exc:
            log.warning("session file ignored: %s", exc)
            self.load_issues = [Issue(None, IssueSeverity.ERROR, "FILE", str(exc))]


class CalibrationStoreAPI:
    """``backend.calibrations`` (§9.6, §9.3.1): active records, history, travel restore / operator decisions."""

    def __init__(self, be: Backend) -> None:
        self._be = be

    def active_load(self) -> Mapping[str, Any] | None:
        cal = self._be.load_input.cal
        return None if cal is None else dict(cal.record)

    def active_travel(self) -> Mapping[str, Any] | None:
        try:
            return self._be.calibration_store.active_travel()
        except FileFormatError:
            return None

    def history(self, kind: str) -> list[str]:
        return self._be.calibration_store.history(kind)

    def restore_travel_async(self) -> Future[TravelDiffState]:
        return self.resolve_travel_difference_async("restore")

    def resolve_travel_difference_async(self, action: str) -> Future[TravelDiffState]:
        if action not in ("restore", "keep_board", "ignore_session"):
            return failed_future(ValueError(f"unknown action {action!r}"))
        be = self._be
        if not be.device.connected:
            return failed_future(GateRefused(GateResult((GateItem(GateCode.LINK_DOWN, Severity.REFUSE,
                                                                  "not connected"),))))
        return be._job(be.travel_cal.resolve_job, action)  # noqa: SLF001


class SequencerAPI:
    """``backend.sequencer`` (§10, §15.5f B6-01…B6-13): model helpers, validation, plan, path, generators, files and
    the execution controls of ``SequenceExecutor``."""

    def __init__(self, be: Backend) -> None:
        self._be = be
        self.last_load_warnings: list[str] = []

    @property
    def executor(self) -> SequenceExecutor:
        return self._be.seq

    # ---- model / files -------------------------------------------------------------------------------
    def new(self) -> Sequence:
        s = self._be.session.get()
        return Sequence(travel_ref=s.seq_travel_ref, k_est_n_mm=float(s.k_est_n_mm or 50.0), pull_dir=s.pull_dir)

    def load(self, path: str) -> Sequence:
        """SW-SEQF-001: a new object (``FileFormatError`` on any error — the caller's sequence is untouched)."""
        seq, self.last_load_warnings = seqfile.load(path)
        return seq

    def save(self, seq: Sequence, path: str) -> None:
        seqfile.save(path, seq)

    # ---- validation / plan ---------------------------------------------------------------------------
    def validation_context(self) -> ValidationContext:
        be = self._be
        m = be.motion
        lim = m.limits() if be.device.connected else None
        lo, hi = m.travel_range_mm() if be.device.params.values() else (None, None)
        cfg = be.limits.get()
        li = be.load_input
        return ValidationContext(
            v_travel_cap_mm_s=None if lim is None else min(lim.v_travel_mm_s, lim.v_step_rate_mm_s),
            v_load_cap_mm_s=None if lim is None else min(lim.v_load_mm_s, lim.v_step_rate_mm_s),
            a_max_mm_s2=None if lim is None else lim.a_max_mm_s2, travel_lo_mm=lo, travel_hi_mm=hi,
            x_zero_mm=m.x_zero_mm, pull_trip_n=cfg.pull_trip_n if cfg.pull_enabled else None,
            push_trip_n=cfg.push_trip_n if cfg.push_enabled else None, fw_level_n=cfg.fw_level_n,
            f_cal_max_n=None if li.cal is None else li.cal.f_cal_max,
            margin_check=(cfg.pull_enabled or cfg.push_enabled) and not li.no_specimen)

    def validate(self, seq: Sequence) -> list[SeqIssue]:
        return seq_validate(seq, self.validation_context())

    def plan_context(self, seq: Sequence) -> PlanContext:
        be = self._be
        off = be.motion.x_zero_mm if seq.travel_ref == "test" else 0.0
        x = be.motion.position_mm() if be.device.connected else None
        f = be.data.latest("F_N").value if be.device.connected else float("nan")
        lim = be.motion.limits() if be.device.connected else None
        return PlanContext(0.0 if x is None else x - off, f if f == f else 0.0,
                           lim.a_max_mm_s2 if lim is not None else 100.0, -off, be.session.get().tare_window_s)

    def expand(self, seq: Sequence) -> Plan:
        return seq_expand(seq, self.plan_context(seq))

    def planned_path(self, seq_or_plan: Sequence | Plan) -> tuple[PathPoint, ...]:
        plan = seq_or_plan if isinstance(seq_or_plan, Plan) else self.expand(seq_or_plan)
        return seq_planned_path(plan)

    # ---- generators ----------------------------------------------------------------------------------
    def schema_context(self) -> SchemaContext:
        be = self._be
        lim = be.motion.limits() if be.device.connected else None
        lo, hi = be.motion.travel_range_mm() if be.device.params.values() else (None, None)
        x_off = be.motion.x_zero_mm
        s = be.session.get()
        kw: dict[str, Any] = {}
        if lim is not None:
            kw.update(v_travel_max_mm_s=min(lim.v_travel_mm_s, lim.v_step_rate_mm_s),
                      v_load_max_mm_s=min(lim.v_load_mm_s, lim.v_step_rate_mm_s), a_max_mm_s2=lim.a_max_mm_s2)
        if lo is not None:
            kw["travel_lo_mm"] = lo - x_off
        if hi is not None:
            kw["travel_hi_mm"] = hi - x_off
        kw["speed_default_mm_s"] = min(s.manual_speed_mm_s, kw.get("v_travel_max_mm_s", 30.0))
        return SchemaContext(**kw)

    def generator_schemas(self) -> Mapping[str, GeneratorSchema]:
        return generator_schemas(self.schema_context())

    def generate(self, name: str, params: Mapping[str, Any] | None = None, **kw: Any) -> Block:
        return seq_generate(name, params, ctx=self.schema_context(), **kw)

    # ---- execution -----------------------------------------------------------------------------------
    def check_start(self, seq: Sequence) -> GateResult:
        """SW-SEQ-005 (§15.5f B6-06): the static ``sequence_start`` gate + the items of this sequence."""
        be = self._be
        snap = be._gate_snapshot()  # noqa: SLF001
        items = list(all_gates(snap)[GateId.SEQUENCE_START].items)
        issues = self.validate(seq)
        errs = [i for i in issues if i.severity == IssueSeverity.ERROR]
        rng = [i for i in errs if i.code == "TARGET_OUT_OF_RANGE"]
        other = [i for i in errs if i.code != "TARGET_OUT_OF_RANGE"]
        if other:
            items.append(GateItem(GateCode.SEQ_INVALID, Severity.REFUSE, f"{len(other)} validation error(s): "
                                  f"{other[0].text}" + (f" (+{len(other) - 1} more)" if len(other) > 1 else "")))
        if rng:
            items.append(GateItem(GateCode.TARGET_OUT_OF_RANGE, Severity.REFUSE, rng[0].text
                                  + (f" (+{len(rng) - 1} more)" if len(rng) > 1 else "")))
        kinds = set()
        for s in seq.steps:
            try:
                kinds.add(StepKind(s.kind))
            except ValueError:
                pass
        li = be.load_input
        lst = li.evaluate(be.device.params.values(), be.device.info)
        if StepKind.LOAD in kinds:
            if li.no_specimen:
                items.append(GateItem(GateCode.NO_SPECIMEN_MODE, Severity.REFUSE, "load steps need the PC load limits:"
                                                                                  " leave the no-specimen mode"))
            elif not lst.valid:
                items.append(GateItem(GateCode.LOAD_INPUT_INVALID, Severity.REFUSE, "load steps need a valid "
                                      f"calibration + tare: {lst.reason}", "Calibrate + Tare"))
            if "MOVE_UNTIL_LOAD" not in snap.features:
                items.append(GateItem(GateCode.FEATURE_MISSING, Severity.REFUSE, "not supported by this FW build "
                                                                                 "(MOVE_UNTIL_LOAD)"))
            if li.cal is not None and li.cal.low_span:
                items.append(GateItem(GateCode.LOW_SPAN, Severity.WARN, "calibration LOW_SPAN (< 20 % FS): load "
                                                                        "targets may be extrapolated"))
        elif li.no_specimen and not any(i.severity == Severity.REFUSE for i in items):
            items.append(GateItem(GateCode.NO_SPECIMEN_TRAVEL_ONLY, Severity.CONFIRM, "travel-only sequence in the "
                                  "no-specimen mode (PC load limits off): confirm that no specimen is mounted"))
        if StepKind.HOME in kinds and not any(i.severity == Severity.REFUSE for i in items):
            over = snap.raw is not None and abs(snap.raw - snap.zero_raw) > snap.home_max_load_raw
            if over or not snap.load_known:
                items.append(GateItem(GateCode.HOME_LOAD_CONFIRM, Severity.CONFIRM, "HOME steps: load unknown or "
                                      "above 5 % FS — confirm homing (SAF-SW-004)"))
        for code in ("SAF_SW_006_MARGIN", "EXTRAPOLATED"):
            w = [i for i in issues if i.code == code and i.severity == IssueSeverity.WARN]
            if w:
                items.append(GateItem(code, Severity.WARN, w[0].text + (f" (+{len(w) - 1} more steps)"
                                                                         if len(w) > 1 else "")))
        if be.recorder.state != "RECORDING":
            from bend_stand.core.recorder import FREE_MIN_BYTES  # noqa: PLC0415

            free = be._free_space(Path(be._recordings_root()))  # noqa: SLF001
            if free is not None and free < FREE_MIN_BYTES:
                items.append(GateItem(GateCode.DISK_SPACE, Severity.REFUSE, f"not enough free disk space for the "
                                      f"recording ({free // (1024 * 1024)} MB)"))
        return GateResult(tuple(items))

    def start(self, seq: Sequence, *, confirmed: bool = False) -> GateResult:
        """B3-20: nothing starts without ``confirmed=True`` when CONFIRM items exist (the gate itself is returned)."""
        ex = self._be.seq
        if ex.active:
            return GateResult((GateItem(GateCode.SEQUENCE_RUNNING, Severity.REFUSE, "a sequence is running"),))
        g = self.check_start(seq)
        if not g.ok or (g.confirm_items and not confirmed):
            return g
        home_ok = any(i.code == GateCode.HOME_LOAD_CONFIRM for i in g.confirm_items)
        ex.start(seq.copy(), plan_ctx=self.plan_context(seq), home_confirmed=home_ok)
        return g

    def pause(self) -> StopResult:
        return self._be.pause("sequence")

    def resume(self) -> GateResult:
        return self._be.resume("sequence")

    def stop(self) -> StopResult:
        return self._be.seq.stop("sequence")

    def abort(self, reason: str = "operator") -> StopResult:
        return self._be.seq.abort(reason)

    def continue_(self) -> None:
        self._be.seq.continue_()

    def status(self) -> SeqStatus:
        return self._be.seq.status()

    def results(self) -> tuple[StepResult, ...]:
        return self._be.seq.results()


class ReportAPI:
    """``backend.reports`` (§11, §15.5f B6-14)."""

    def __init__(self, be: Backend) -> None:
        self._be = be

    def build_async(self, rec_dir: str, cal: Any = None, tare: float | None = None,
                    bend3p: Any = None) -> Future[ReportPaths]:
        be = self._be
        s = be.session.get()
        geom = s.bend3p if bend3p is None else (None if bend3p is False else bend3p)

        def job() -> Job:
            paths = report_mod.build_report(rec_dir, cal=cal, tare=tare, bend3p=geom if geom is not None else False,
                                            bend3p_compliance=s.compliance_mm_per_n)
            be.events.publish("report.ready", paths)
            return paths
            yield  # pragma: no cover - makes this a generator job

        return be._job(job)  # noqa: SLF001

    def list_recordings(self, root: str | None = None) -> list[RecordingInfo]:
        return report_mod.list_recordings(root or self._be._recordings_root())  # noqa: SLF001

    def load_result(self, rec_dir: str) -> ReportResult:
        return report_mod.load_result(rec_dir)


# ============================================================================== backend

class Backend:
    def __init__(self, settings: BackendSettings | None = None) -> None:
        self.settings = s = settings or BackendSettings()
        self.clock: Clock = LockstepClock() if s.clock == "lockstep" else MONOTONIC
        self.lockstep = s.clock == "lockstep"
        self.events = EventBus(self.clock)
        self.liveness = LivenessMonitor(self.clock)
        self.worker = Worker(self.clock, "worker")
        self.sim_endpoint: Any = None
        self.sim: Any = None
        self._sim_name: str | None = None
        self.device = Device(self.clock, self.events, self.liveness, self._transport_for,
                             DeviceSettings(wire_log=s.wire_log, stream_on_connect=s.stream_on_connect,
                                            auto_reconnect=s.auto_reconnect, heartbeat=s.heartbeat,
                                            seq_seed=s.seq_seed))
        self.device.threaded_reader = not self.lockstep
        self.device.submit_job = lambda fn: self.worker.submit(fn)
        self.pipeline = Pipeline(self.clock, self.events, self.liveness, on_fw_event=self._on_fw_event,
                                 on_events_lost=self.device._poll_now)  # noqa: SLF001
        self.device.on_async = self.pipeline.put
        self.device.on_link_change = self._on_link_change
        self.recorder = Recorder(self.clock, on_state=lambda st: self.events.publish("rec.state", st),
                                 on_failure=self._on_rec_failure,
                                 on_warning=lambda text: self.events.log(text, logging.WARNING))
        self.recorder.free_space_probe = self._free_space
        self.recorder.op_name = lambda: "" if self.owner == "MANUAL" else self.owner
        self.data_dir = paths.app_data_dir(s.data_dir)
        self.calibration_store = CalibrationStore(paths.calibration_dir(self.data_dir))
        self.load_input = LoadInput()
        self.capture = CaptureHub()
        self.owner = "MANUAL"                              # motion owner: MANUAL | TRAVEL_CAL (§3.5)
        self._last_moving_t: int | None = None
        self._latest_t: int | None = None
        self._latest_epoch = 0
        self._th_job: Any = None
        self._gc_watch: Any = None
        self.safety = SafetySupervisor(stop=self._safety_stop, terminate=self.terminate_all,
                                       publish=self.events.publish, event_row=self.record_event,
                                       limits=lambda: self.session.get().limits, inputs=self._safety_inputs)
        self.pipeline.safety = self.safety.process
        self.pipeline.sinks.append(self._track_row)
        self.pipeline.sinks.append(self.capture.on_row)
        self.pipeline.sinks.append(self.recorder.on_row)
        self.pipeline.event_sinks.append(self.recorder.on_event)
        self.channels = ChannelRegistry()
        self.channels.on_change = lambda: self.events.publish("channels.changed", None)
        self.data = DataView(self.pipeline, self.clock.monotonic_ns)
        self.config = ConfigAPI(self)
        self.session = SessionAPI(self)
        self.limits = LimitsAPI(self)
        self.motion = MotionController(self)
        self.marks = MarksAPI(self)
        self.tare_engine = TareEngine(self)
        self.travel_cal = TravelCalEngine(self)
        self.load_cal = LoadCalEngine(self)
        self.calibrations = CalibrationStoreAPI(self)
        self.device.threshold_mgr.provider = self._threshold_target
        self.device.on_synced = self._on_synced
        self.events.subscribe("device.params", self._on_params_event, weak=False)
        self.session.load_default(Path(s.session_path) if s.session_path
                                  else paths.sessions_dir(self.data_dir) / "default.bbsession.json")
        self._load_active_calibration()
        self.on_scale_changed()
        self.seq = SequenceExecutor(self)                 # M4 sequencer (own runner, §22c)
        self.pipeline.sinks.append(self.seq.on_row)
        self.data.trace_provider = self.seq.trace
        self.sequencer = SequencerAPI(self)
        self.reports = ReportAPI(self)
        self.test_hooks: Any = None
        if s.test_hooks:
            from bend_stand.core.testing import TestHooks  # noqa: PLC0415

            self.test_hooks = TestHooks(self)
        self._sup_thread: threading.Thread | None = None
        self._sup_stop = threading.Event()
        self._started = False
        self._status_seq = 0
        self.hotkey: Any = None
        self._hotkey_mode = "UNAVAILABLE"
        self._hotkey_reason = "not started"
        self._next_hotkey_ping_ns = 0

    # ---- lifecycle -----------------------------------------------------------------------------------
    def start(self) -> None:
        """Supervisor, Worker, Pipeline (hotkey: M3). No port is opened (D-06)."""
        if self._started:
            return
        self._started = True
        self.liveness.install_excepthook()
        self._start_hotkey()
        if self.lockstep:
            return
        from bend_stand.core import timing  # noqa: PLC0415

        timing.init()
        self._gc_watch = timing.GcWatch()
        self._gc_watch.install()
        self.worker.start()
        self.seq.runner.start()
        self.pipeline.start()
        self._sup_stop.clear()
        self._sup_thread = threading.Thread(target=self._supervisor_loop, name="bend-supervisor", daemon=True)
        self._sup_thread.start()

    def shutdown(self) -> None:
        """STOP if moving, stop recording, disconnect, join threads."""
        try:
            if self.device.last_flags & INT_DF.MOVING:
                self.device.stop(pg.StopMode.IMMEDIATE, "shutdown")
            if self.recorder.state != "IDLE":
                self.recorder.stop()
            if self.device.state != LinkState.DISCONNECTED:
                if self.lockstep:
                    f = self.worker.submit(self.device.disconnect_job)
                    for _ in range(200):
                        if f.done():
                            break
                        self.test_hooks.advance(1) if self.test_hooks else self._step_all(self.clock.monotonic_ns())
                else:
                    try:
                        self.worker.submit(self.device.disconnect_job).result(timeout=2.0)
                    except Exception:  # noqa: BLE001
                        self.device._close_link()  # noqa: SLF001
        finally:
            self._sup_stop.set()
            t, self._sup_thread = self._sup_thread, None
            if t is not None:
                t.join(1.0)
            self.seq.shutdown()
            self.worker.stop()
            self.pipeline.stop()
            self._stop_sim()
            hk, self.hotkey = self.hotkey, None
            if hk is not None:
                hk.stop()
            self.liveness.uninstall_excepthook()
            gw, self._gc_watch = self._gc_watch, None
            if gw is not None:
                gw.uninstall()
            self._started = False

    def gui_beat(self) -> None:
        self.liveness.beat("gui")

    def _supervisor_loop(self) -> None:
        from bend_stand.core.timing import Ticker, raise_thread_priority  # noqa: PLC0415

        raise_thread_priority(2)
        ticker = Ticker(SUPERVISOR_TICK_NS, self.clock)
        while ticker.wait(self._sup_stop):
            ticker.due()
            try:
                self._tick(self.clock.monotonic_ns())
            except Exception:  # noqa: BLE001 — the supervisor must survive a bad tick
                log.exception("supervisor tick failed")

    def _tick(self, now: int) -> None:
        """Supervisor body: link / heartbeat / confirmations (Device), jog refresh (motion), hotkey ping."""
        self.liveness.beat("supervisor", now)
        self.device.tick(now)
        self.motion.tick(now)
        self.seq.tick(now)
        self._auto_thresholds()
        hk = self.hotkey
        if hk is not None and now >= self._next_hotkey_ping_ns:
            self._next_hotkey_ping_ns = now + 250 * MS
            hk.ping()

    def _step_all(self, now: int) -> None:
        """One lockstep step: simulator → Reader → Pipeline → Supervisor → Worker → Recorder."""
        if self.sim_endpoint is not None:
            self.sim_endpoint.step(now)
        rd = self.device.reader
        if rd is not None:
            rd.step(now)
        self.pipeline.step(now)
        self._tick(now)
        self.worker.step(now)
        self.seq.runner.step(now)
        self.recorder.step(now)

    # ---- connection ----------------------------------------------------------------------------------
    def endpoints(self) -> list[EndpointInfo]:
        out = [EndpointInfo(p.device, f"{p.device} {p.description}".strip(), "serial", p.description, p.is_stlink)
               for p in ports.list_ports()]
        out.append(EndpointInfo("sim", "Simulator (in-process)", "sim", "FW simulator, default scenario"))
        out.append(EndpointInfo(TWIN_ENDPOINT, "FW host twin", "tcp", "Integrator's FW twin over TCP"))
        out.append(EndpointInfo(SIM_SERVER_ENDPOINT, "Simulator server", "tcp", "out-of-process simulator"))
        return out

    def _transport_for(self, endpoint: str) -> Transport:
        ep = parse_endpoint(endpoint)
        if ep.kind == "sim":
            from bend_stand.io.sim.endpoint import SimEndpoint  # noqa: PLC0415

            if self.sim_endpoint is not None and self._sim_name == endpoint:
                return self.sim_endpoint.transport     # reconnect: the same simulated board (USB re-plug)
            self._stop_sim()
            adv = self.test_hooks.advance if self.test_hooks is not None and self.lockstep else None
            se = SimEndpoint(self.clock, ep.target or None, bytes_per_s=self.settings.sim_bytes_per_s,
                             latency_ns=self.settings.sim_latency_ns, advance=adv,
                             nvm_path=self.settings.sim_nvm_path)
            self.sim_endpoint, self.sim, self._sim_name = se, se.control, endpoint
            if not self.lockstep:
                se.start()
            return se.transport
        self._stop_sim()
        return transport_factory(endpoint, self.clock)

    def _stop_sim(self) -> None:
        se, self.sim_endpoint = self.sim_endpoint, None
        self.sim = None
        if se is not None:
            se.stop()

    def _job(self, fn: Callable[..., Job], *args: Any) -> ReleasingFuture:
        return self.worker.submit(fn, *args)

    def connect_async(self, endpoint: str) -> Future[DeviceInfo]:
        self.pipeline.reset_link()
        return self._job(self.device.connect_job, endpoint)

    def disconnect_async(self) -> Future[None]:
        return self._job(self._disconnect_job)

    def _disconnect_job(self) -> Job:
        yield from self.device.disconnect_job()
        self._stop_sim()
        return None

    def link_diagnostics(self, window_ms: int = 10_000) -> dict[str, Any]:
        """MC3-4 (OBS-M3-R1): longest tick gap of every backend thread and the GC pauses over the last window, the
        receive age and the frame counters — logged with every LINK LOST."""
        now = self.clock.monotonic_ns()
        d = self.device
        rd = d.reader
        out: dict[str, Any] = {"window_ms": window_ms,
                               "tick_gap_ms": self.liveness.longest_gaps(window_ms * MS, now),
                               "rx_age_ms": None if rd is None or rd.last_rx_ns is None
                               else round((now - rd.last_rx_ns) / 1e6, 1),
                               "pipeline_queue": self.pipeline.queued(),
                               "frames_lost_link": self.pipeline.counters.frames_lost_link}
        if self._gc_watch is not None:
            out.update(self._gc_watch.summary(window_ms * MS))
        return out

    def _on_link_change(self, state: LinkState) -> None:
        if state == LinkState.LOST:
            try:
                diag = self.link_diagnostics()
            except Exception as exc:  # noqa: BLE001 — diagnostics must never break the reaction
                diag = {"error": str(exc)}
            self.events.log("LINK LOST", logging.ERROR, diagnostics=diag)
            log.error("LINK LOST diagnostics: %s", diag)
            self.record_event("LINK_LOST", " ".join(f"{k}={v}" for k, v in diag.items()))
        if state in (LinkState.LOST, LinkState.DISCONNECTED):
            self.motion.on_link_down()
            self.terminate_all("LINK_LOST" if state == LinkState.LOST else "DISCONNECTED")
            self.capture.abort(None, "link down")
            if self.load_input.no_specimen:          # the mode ends at disconnect / link loss (SW-LIM-004)
                self.set_no_specimen(False, why="link down")
            self.safety.reset()

    _TERMINATING = frozenset({int(pg.Event.STOPPED), int(pg.Event.ESTOP_SET), int(pg.Event.HALT_SET),
                              int(pg.Event.PAUSED), int(pg.Event.FAULT_SET), int(pg.Event.LINK_WDG)})

    def _on_fw_event(self, ev: Any) -> None:
        """Pipeline thread: link-level reactions first (epoch, BOOT resync), then the motion controller, then the
        operations (§5.5.1: stops / latches / pause / driver power loss terminate wizards and captures)."""
        self.device.handle_fw_event(ev)
        self.motion.on_fw_event(ev)
        code = int(ev.code)
        if code in self._TERMINATING or (code == int(pg.Event.DRIVER_POWER) and ev.arg == 0):
            name = pg.Event(code).name
            if code == int(pg.Event.STOPPED):
                try:
                    name = f"STOPPED ({pg.StopCause(ev.arg).name})"
                except ValueError:
                    pass
            self.terminate_all(name)
        elif code == int(pg.Event.BOOT):
            self.terminate_all("board reset")
        elif code == int(pg.Event.RESUME_REQUEST):          # PAUSE button pressed while PAUSED (D-26 (1), D-31)
            self.resume("button")
        elif code == int(pg.Event.PAUSE_CLEARED) and ev.arg != int(pg.PauseClearedReason.RESUME):
            self.seq.on_pause_cleared()
        elif code == int(pg.Event.ALM_CHANGED) and ev.arg == 1 and \
                self.device.valid_status_mask() & int(pg.DataStatus.ALM):
            self.seq.on_alm()                                 # D-33 c (also from the DATA status edge)

    _PAUSE_REASONS = frozenset({"PAUSE", "PAUSED", "STOPPED (PC_PAUSE)", "STOPPED (PAUSE_BUTTON)"})

    def terminate_all(self, reason: str) -> None:
        """Swap every running operation to ABORTED (no waiting, §3.5); take-sample captures are not affected. A
        running sequence is **paused** for the pause causes (``pause_all``, §3.5) and ended ABORTED otherwise."""
        try:
            if reason in self._PAUSE_REASONS:
                src = {1: "PC", 2: "BUTTON"}.get(self.device.pause_src_ev or 0)
                if reason == "STOPPED (PAUSE_BUTTON)":
                    src = "BUTTON"
                self.seq.on_pause(src or "PC")
            else:
                self.seq.terminate(reason)
        except Exception:  # noqa: BLE001 — never raises
            log.exception("sequence terminate failed")
        for eng in (self.travel_cal, self.load_cal, self.tare_engine):
            try:
                eng.terminate(reason)
            except Exception:  # noqa: BLE001 — never raises
                log.exception("terminate %s failed", eng.KIND)

    def _safety_stop(self, reason: str) -> None:
        try:
            self.device.stop(pg.StopMode.IMMEDIATE, reason)
        finally:
            self.motion.on_stop_issued("STOP")

    # ---- Pause/Break hotkey (SW-STOP-002) -------------------------------------------------------------
    def _hotkey_choice(self) -> str:
        h = self.settings.hotkey
        if h == "auto":
            h = os.environ.get("BEND_STAND_HOTKEY", "win32").strip().lower() or "win32"
        return h

    def _start_hotkey(self) -> None:
        from bend_stand.io import win_hotkey as wh  # noqa: PLC0415

        choice = self._hotkey_choice()
        if choice == "off":
            self._hotkey_mode, self._hotkey_reason = "UNAVAILABLE", "disabled (BEND_STAND_HOTKEY=off)"
            return
        backend = wh.FakeHotkeyBackend() if choice == "fake" or self.lockstep else None

        def on_status(mode: str, detail: str) -> None:
            self._hotkey_mode, self._hotkey_reason = mode, detail
            self.events.publish("hotkey.state", HotkeyStatus(mode, detail, False))  # type: ignore[arg-type]

        def on_test(delay_ms: float | None) -> None:
            self.events.publish("hotkey.test", delay_ms)

        from bend_stand.core.timing import raise_thread_priority  # noqa: PLC0415

        self.hotkey = wh.GlobalHaltHotkey(lambda src: self.halt(src), backend=backend, on_status=on_status,
                                          on_test=on_test, clock_ns=self.clock.monotonic_ns,
                                          thread_init=lambda: raise_thread_priority(2))
        self.hotkey.can_test = lambda: not (self.device.last_flags & INT_DF.MOVING)
        if self.lockstep:                         # no threads on the lockstep clock: test_hooks.hotkey_press()
            self._hotkey_mode, self._hotkey_reason = "UNAVAILABLE", "lockstep clock (no hotkey thread)"
            return
        self.hotkey.start()

    def hotkey_status(self) -> HotkeyStatus:
        hk = self.hotkey
        test = bool(hk is not None and hk.test_mode_active)
        if hk is None:
            return HotkeyStatus(self._hotkey_mode, self._hotkey_reason, False)  # type: ignore[arg-type]
        mode = hk.mode if hk.alive else self._hotkey_mode
        return HotkeyStatus(mode, hk.status_detail or self._hotkey_reason, test)  # type: ignore[arg-type]

    # ---- global actions (GUI thread, non-blocking, never raise) --------------------------------------
    def stop(self, source: str = "gui") -> StopResult:
        try:
            res = self.device.stop(pg.StopMode.IMMEDIATE, source)       # act first …
        except Exception as exc:  # noqa: BLE001 — never raises (§14)
            res = StopResult("STOP", source, False, None, str(exc))
        self._after_stop("STOP", source)
        return res

    def halt(self, source: str = "gui") -> StopResult:
        try:
            res = self.device.halt(source)
        except Exception as exc:  # noqa: BLE001
            res = StopResult("HALT", source, False, None, str(exc))
        self._after_stop("HALT", source)
        return res

    def pause(self, source: str = "gui") -> StopResult:
        try:
            res = self.device.pause(source)
        except Exception as exc:  # noqa: BLE001
            res = StopResult("PAUSE", source, False, None, str(exc))
        self._after_stop("PAUSE", source)
        return res

    def _after_stop(self, cmd: str, source: str = "") -> None:
        """… then drop the jog session and the pending target (SW_design §4.5 (2)), terminate the operations (wizards
        are not resumable, GRQ-B-10 d) and write the event row."""
        try:
            self.motion.on_stop_issued(cmd)
            self.terminate_all(cmd)
            self.record_event(cmd, source)
        except Exception:  # noqa: BLE001 pragma: no cover - never raises
            log.exception("after-stop handling failed")

    def resume(self, source: str = "gui") -> GateResult:
        """RESUME 0x3C (D-31). With a PAUSED sequence the executor sends RESUME and, after its ACK, re-issues the
        interrupted step (SW-STOP-004, B6-13); manual mode: RESUME only."""
        g = self._gates()[GateId.RESUME]
        if g.ok and self.seq.paused:
            if not self.seq.request_resume(source):
                g = GateResult((GateItem(GateCode.OPERATION_RUNNING, Severity.REFUSE, "resume already in progress"),))
                self.events.publish("resume.ignored", ResumeIgnored("gui" if source != "button" else "button", g))
            return g
        if g.ok:
            fut = self.device.clear_async(pg.Cmd.RESUME)       # submitted at once (CONTROL lane), no Worker job
            fut.add_done_callback(self._publish_resume)
        else:
            self.events.publish("resume.ignored", ResumeIgnored("gui" if source != "button" else "button", g))
        return g

    def _publish_resume(self, f: Future[ClearResult]) -> None:
        if f.exception() is None:
            self.events.publish("log", {"text": f"RESUME: {f.result().outcome} {f.result().text}"})
            self.record_event("RESUME", f.result().outcome)

    def tare(self, window_s: float | None = None) -> GateResult:
        """SW-TARE-001: from every tab; progress on topic ``tare.state`` (§9.5)."""
        try:
            return self.tare_engine.start_tare(window_s)
        except Exception as exc:  # noqa: BLE001 — never raises
            log.exception("tare start failed")
            return GateResult((GateItem(GateCode.OPERATION_RUNNING, Severity.REFUSE, str(exc)),))

    def take_sample(self, window_s: float = 1.0) -> GateResult:
        """SW-ACQ-003: mean / std / N of F, x and raw over ``window_s`` (0.1–10 s) → ``samples.csv``."""
        if not 0.1 <= float(window_s) <= 10.0:
            return GateResult((GateItem("RANGE", Severity.REFUSE, "sample window must be 0.1…10 s"),))
        g = self._gates()[GateId.SAMPLE]
        if not g.ok:
            return g
        try:
            self.capture.start("sample", float(window_s), presettle_s=0.0, require_still=False,
                               on_done=self._sample_done, rate_hint=lambda: self.pipeline.latest_copy().rate_sps)
        except RuntimeError as exc:
            return GateResult((GateItem(GateCode.OPERATION_RUNNING, Severity.REFUSE, str(exc)),))
        return g

    def _sample_done(self, res: CaptureResult) -> None:
        if not res.ok:
            self.events.log(f"take sample aborted: {res.aborted}", logging.WARNING)
            return

        def ms(a: np.ndarray) -> tuple[float, float, int]:
            v = a[np.isfinite(a)]
            if v.size == 0:
                return float("nan"), float("nan"), 0
            return float(v.mean()), float(v.std(ddof=1)) if v.size > 1 else float("nan"), int(v.size)
        fm, fs, fn = ms(res.f_n)
        xm, xs, _xn = ms(res.x_mm)
        rm, rs, rn = ms(res.raw)
        row = SampleRow(wall_utc_iso(self.clock), self.pipeline.latest_copy().t_dev_s, res.window_s,
                        res.n_frames, fm, fs, fn, xm, xs, rm, rs, rn, self.marks.get())
        path = self.recorder.samples_path(self._recordings_root())
        try:
            append_sample(path, row)
            row = replace(row, file=str(path))
        except OSError as exc:
            self.events.log(f"samples file not written: {exc}", logging.ERROR)
        self.record_event("SAMPLE", f"F={fm:.4f}±{fs:.4f} N n={fn} x={xm:.4f} mm raw={rm:.1f}")
        self.events.publish("sample.taken", row)

    def _recordings_root(self) -> str:
        return (self.settings.recordings_root or self.session.get().recordings_root
                or str(paths.default_recordings_root()))

    def _free_space(self, path: Path) -> int | None:
        th = self.test_hooks
        if th is not None and th.free_space is not None:
            return int(th.free_space)
        from bend_stand.core.recorder import free_space  # noqa: PLC0415

        return free_space(path)

    def _on_rec_failure(self, st: Any) -> None:
        self.seq.on_recording_failed()                      # SW-ACQ-004: controlled stop of a running sequence (act
        self.events.publish("rec.failure", st)              # first, publish after)
        self.events.log(f"RECORDING FAILED: {st.failure}", logging.ERROR)

    def record_event(self, name: str, text: str = "") -> None:
        """SW event row in a running recording (TARE, SW_TRIP, NO_SPECIMEN_ON, MARK_EDIT, X_ZERO, CAL_*, …)."""
        try:
            self.recorder.event_row(name, text)
        except Exception:  # noqa: BLE001 — recording problems are reported by the recorder itself
            log.exception("event row failed")

    def _snapshot(self) -> dict[str, Any]:
        """Automatic snapshot for the recording sidecar (SW-META-002, SW-LIM-003)."""
        li = self.load_input
        info = self.device.info
        tare = li.tare_for(info.uid if info else None)
        st = li.evaluate(self.device.params.values(), info)
        return {"calibration": None if li.cal is None else dict(li.cal.record),
                "calibration_valid_for_limits": st.cal_valid, "load_input_reason": st.reason,
                "tare": None if tare is None else asdict(tare), "limits": asdict(self.limits.get()),
                "session": session_mod.to_dict(self.session.get()), "x_zero_mm": self.motion.x_zero_mm,
                "travel_calibration": self.calibrations.active_travel(),
                "travel_cal_differs": asdict(self.travel_cal.diff)}

    def record_start(self) -> GateResult:
        return self.record_start_with(None)

    def record_start_with(self, extra_meta: Mapping[str, Any] | None) -> GateResult:
        """``record_start`` with extra sidecar entries (the sequencer's run header, §13.7)."""
        g = self._gates()[GateId.RECORD_START]
        if not g.ok:
            return g
        root = self._recordings_root()
        info = self.device.info
        meta = {"sw_version": __version__, "marks_at_start": marks_mod.marks_to_dict(self.marks.get()),
                "board_params": self.device.params.values(),
                "thresholds": vars(self.device.thresholds), "no_specimen_mode": self.load_input.no_specimen,
                "snapshot": self._snapshot()}
        if info is not None:
            meta.update(fw_version=".".join(map(str, info.fw_version)), board_uid=info.uid, build=info.build)
        if extra_meta:
            meta.update(dict(extra_meta))
        cal = self.load_input.cal
        tare = self.load_input.tare_for(info.uid if info else None)
        header = {"calibration": "none" if cal is None else f"K={cal.k:.12g} N/count status={cal.status} "
                                                            f"file={cal.file}",
                  "tare": "none" if tare is None else f"tare_raw={tare.tare_raw:.3f} id={tare.tare_id}",
                  "limits": _limits_text(self.limits.get()), "thresholds": self.device.thresholds.state,
                  "x_zero_mm": f"{self.motion.x_zero_mm:.4f}",
                  "no_specimen_mode": str(self.load_input.no_specimen).lower()}
        try:
            m = self.marks.get()
            self.recorder.start(root, meta, specimen=m.specimen, number=m.number, header=header)
        except RecorderError as exc:
            return GateResult((GateItem(GateCode.RECORDING_ACTIVE, Severity.REFUSE, exc.user_text),))
        except OSError as exc:                     # never surface the raw (localised) OS text (SWD-M1-09)
            return GateResult((GateItem(GateCode.RECORDING_ACTIVE, Severity.REFUSE,
                                        f"cannot start the recording (file error {exc.errno})"),))
        return g

    def record_stop(self) -> GateResult:
        g = self._gates()[GateId.RECORD_STOP]
        if g.ok:
            self.recorder.stop({"link_stats": vars(self._link_stats()),
                                "marks_final": marks_mod.marks_to_dict(self.marks.get()),
                                "no_specimen_mode_at_stop": self.load_input.no_specimen})
            if self.seq.report_pending is not None:          # a sequence ran inside the operator's recording
                self._job(self._pending_report_job)
        return g

    def _pending_report_job(self) -> Job:
        self.seq.build_pending_report()
        return None
        yield  # pragma: no cover - generator job

    # ---- M3 state: calibration / tare / scale / thresholds / no-specimen -----------------------------------
    def _load_active_calibration(self) -> None:
        try:
            rec = self.calibration_store.active_load()
            self.load_input.set_calibration(rec)
        except FileFormatError as exc:
            self.load_input.set_calibration(None, str(exc))
            log.warning("active load calibration ignored: %s", exc)

    def activate_load_calibration(self, record: Mapping[str, Any]) -> None:
        self.load_input.set_calibration(record)
        self.on_scale_changed()

    def _on_params_event(self, _rec: Any) -> None:
        self.on_scale_changed()

    def on_test_zero(self, x_zero_mm: float, text: str) -> None:
        self.load_input.x_zero_mm = x_zero_mm
        self.load_input.reset_epoch += 1
        self.on_scale_changed()
        self.record_event("X_ZERO", text)

    def on_scale_changed(self) -> None:
        """Calibration, tare, AFE params, x_zero or geometry changed: new ``ScaleConfig`` for the pipeline (atomic
        swap) and channel availability (SW-RT-002); the threshold target is re-evaluated by the tick."""
        li = self.load_input
        s = self.session.get()
        li.bend3p, li.compliance = s.bend3p, s.compliance_mm_per_n
        params, info = self.device.params.values(), self.device.info
        sc = li.scale_config(params, info)
        self.pipeline.scale = sc
        st = li.evaluate(params, info)
        self.channels.apply_scale(sc.force_available, None if sc.force_available else st.reason,
                                  li.cal is not None, s.bend3p is not None)

    def _threshold_target(self) -> Any:
        return self.load_input.threshold_target(self.device.params.values(), self.device.info,
                                                self.session.get().limits.fw_level_n)

    def _auto_thresholds(self) -> None:
        """SAF-SW-002: rewrite + verify the FW thresholds whenever their target changed (calibration activated, tare /
        undo, FW level edit, AFE change, …); idle only, one job at a time; a FAILED target is retried only by
        ``limits.recheck_async()`` or a new target."""
        d = self.device
        if not d.connected or d.compat.read_only or not d.params.values() or d._sync_active:  # noqa: SLF001
            return
        if self._th_job is not None and not self._th_job.done():
            return
        if d.last_flags & INT_DF.MOVING or self.motion.busy:
            return
        if d.threshold_mgr.needs_apply():
            self.thresholds_job()

    def thresholds_job(self) -> ReleasingFuture:
        """Write + verify the FW thresholds of the current target (one job registered at a time, so the automatic
        rewrite never runs a second, competing write)."""
        fut = self._job(self.device.session_values_job)
        self._th_job = fut
        return fut

    def set_no_specimen(self, on: bool, why: str = "operator") -> None:
        li = self.load_input
        if li.no_specimen == on:
            return
        li.no_specimen = on
        self.record_event("NO_SPECIMEN_ON" if on else "NO_SPECIMEN_OFF", why)
        self.events.log(("no-specimen mode ON — PC load limits off" if on else "no-specimen mode OFF") + f" ({why})",
                        logging.WARNING if on else logging.INFO)
        self.events.publish("safety.no_specimen", on)

    def _on_synced(self) -> None:
        """Worker thread, end of the connect / BOOT resync: scale + post-sync checks (travel restore rule); a VALID
        left at 1 by a link loss during a capture window is cleared (SWC-M4-03; D-47: the FW also clears it on its
        link watchdog)."""
        self.on_scale_changed()
        self._job(self.travel_cal.post_sync_job)
        if self.device.last_flags & INT_DF.VALID and not self.seq.capture_open and not self.device.compat.read_only:
            self.record_event("VALID_OFF", "left over after a reconnect")
            self._job(self.device.set_valid_job, False)

    def _safety_inputs(self) -> SafetyInputs:
        li = self.load_input
        d = self.device
        st = li.evaluate(d.params.values(), d.info)
        cal = li.cal
        spm = d.params.get("motion.steps_per_mm")
        return SafetyInputs(li.no_specimen, st.valid, st.reason, cal.k if cal is not None else None,
                            float(spm) if spm else 800.0, self.session.get().pull_dir, self.motion.current_end_um())

    def _track_row(self, row: DataRow) -> None:
        """Pipeline sink: device time of the last MOVING = 1 (tare refusal < 1 s after a move, SW-TARE-003)."""
        if row.epoch != self._latest_epoch:
            self._latest_epoch, self._last_moving_t = row.epoch, None
        self._latest_t = row.t_us_u
        if row.flags & INT_DF.MOVING:
            self._last_moving_t = row.t_us_u

    def hotkey_test_start(self, timeout_s: float = 10.0) -> GateResult:
        """GRQ-B-15: arm the Pause/Break key test window (≤ 10 s); motion is refused while it runs; result on the
        topic ``hotkey.test`` (delay ms or None at the timeout)."""
        g = self._gates()[GateId.HOTKEY_TEST]
        hk = self.hotkey
        if not g.ok or hk is None:
            return g
        hk.test_timeout_s = max(0.1, min(10.0, float(timeout_s)))
        try:
            hk.start_test_mode()
        except Exception as exc:  # noqa: BLE001
            return GateResult((GateItem(GateCode.HOTKEY_UNAVAILABLE, Severity.REFUSE, str(exc)),))
        return g

    # ---- actions that wait for the board ---------------------------------------------------------------
    def _clear_refusal(self, gid: GateId) -> GateResult | None:
        """Local refusal of a clear (SWD-M1-03): link down or the IF-008 read-only state ("no clears-and-enable",
        ICD §9.5). Other gate items (nothing to clear, cause still active) are left to the FW, whose NACK is
        decoded and shown verbatim (a clear never starts motion)."""
        g = self._gates()[gid]
        hard = tuple(i for i in g.refused if i.code in (GateCode.LINK_DOWN, GateCode.COMPAT_READ_ONLY))
        return GateResult(hard) if hard else None

    def _clear(self, gid: GateId, cmd: pg.Cmd, *, confirmed: bool | None) -> Future[ClearResult]:
        refused = self._clear_refusal(gid)
        if refused is not None:
            return failed_future(GateRefused(refused))
        g = self._gates()[gid]
        if confirmed is not None and g.confirm_items and not confirmed:
            return failed_future(ConfirmationRequired(g))
        return self.device.clear_async(cmd)         # priority path, written now (D-34, SWD-M1-04)

    def clear_stop_async(self, *, confirmed: bool = False) -> Future[ClearResult]:
        if confirmed and self.seq.paused and self._clear_refusal(GateId.CLEAR_STOP) is None:
            self.seq.end_cleared()                           # the paused sequence ends STOPPED (CLEARED) first
        return self._clear(GateId.CLEAR_STOP, pg.Cmd.HALT_CLEAR, confirmed=confirmed)

    def estop_clear_async(self, *, confirmed: bool) -> Future[ClearResult]:
        if not confirmed:
            refused = self._clear_refusal(GateId.ESTOP_CLEAR)
            return failed_future(GateRefused(refused) if refused is not None
                                 else ConfirmationRequired(self._gates()[GateId.ESTOP_CLEAR]))
        return self._clear(GateId.ESTOP_CLEAR, pg.Cmd.ESTOP_CLEAR, confirmed=None)

    def fault_clear_async(self) -> Future[ClearResult]:
        return self._clear(GateId.FAULT_CLEAR, pg.Cmd.FAULT_CLEAR, confirmed=None)

    def stream_start_async(self) -> Future[None]:
        g = self.status().gates[GateId.STREAM_START]
        if not g.ok and not g.codes() == (GateCode.STREAM_ALREADY,):
            return failed_future(GateRefused(g))
        return self._job(self.device.stream_job, True)

    def stream_stop_async(self) -> Future[None]:
        g = self.status().gates[GateId.STREAM_STOP]
        if not g.ok and not g.codes() == (GateCode.STREAM_ALREADY,):
            return failed_future(GateRefused(g))
        return self._job(self.device.stream_job, False)

    # ---- status -------------------------------------------------------------------------------------
    def _link_stats(self) -> Any:
        return self.device.link_stats(self.pipeline.counters.as_linkstats())

    @staticmethod
    def _latch_source(on: bool, from_event: int | None, from_status: int | None) -> str | None:
        """Source of a HALT / PAUSED latch (SWD-M1-05): the EVENT arg at once, the STATUS field as fallback;
        ``None`` (not ``"NONE"``) when not latched or unknown (GF-18)."""
        if not on:
            return None
        for v in (from_event, from_status):
            if v in (int(pg.Source.PC), int(pg.Source.BUTTON)):
                return pg.Source(v).name
        return None

    def _indicators(self, now: int) -> Indicators:
        d = self.device
        connected = d.connected
        data_ok = connected and d.stream_on and d.last_data_ns is not None and now - d.last_data_ns <= DATA_STALE_NS
        st = d.board
        status_ok = connected and st is not None and now - d.board_ns <= STATUS_STALE_NS
        flags, status = d.latest()
        live = data_ok or status_ok
        vmask = d.valid_status_mask()                   # D-37 b: bits of an absent feature are invalid → UNKNOWN
        items: dict[str, Indicator] = {}

        def put(name: str, on: bool, known: bool, **kw: Any) -> None:
            key = name.lower()
            if not known:
                items[key] = INDICATOR_UNKNOWN
                return
            hint = CLEAR_HINTS.get(name) if on else None
            items[key] = Indicator("ON" if on else "OFF", None, kw.get("source"), kw.get("value"), hint)

        for i, n in enumerate(pg.DATA_FLAGS_BITS):
            if n:
                src = None
                if n == "HALT":
                    src = self._latch_source(bool(flags & INT_DF.HALT), d.halt_src_ev,
                                             st.halt_src if st is not None else None)
                put(n, bool(flags >> i & 1), live, source=src)
        for i, n in enumerate(pg.DATA_STATUS_BITS):
            if n:
                src = None
                if n == "PAUSED":
                    src = self._latch_source(bool(status & INT_DS.PAUSED), d.pause_src_ev,
                                             st.pause_src if st is not None else None)
                put(n, bool(status >> i & 1), live and bool(vmask >> i & 1), source=src)
        for i, n in enumerate(pg.FAULTS_BITS):
            if n:
                put(n, bool(st is not None and st.faults >> i & 1), status_ok)
        for i, n in enumerate(pg.SYS_FLAGS_BITS):
            if n:
                put(n, bool(st is not None and st.sys_flags >> i & 1), status_ok)
        items["link_state"] = Indicator("ON" if connected else "OFF", None, d.state.value)
        trip = self.safety.trip
        items["sw_trip"] = (Indicator("ON", trip.t_us, trip.limit, trip.value, CLEAR_HINTS["SW_TRIP"])
                            if trip is not None else Indicator("OFF"))
        th = d.thresholds
        ok = th.state in ("VERIFIED", "DEFAULT_ONLY") and d.threshold_mgr.matches(th)
        items["thresholds_state"] = Indicator("ON" if ok else "OFF", None, th.state, 1.0 if th.clamped else 0.0,
                                              None if ok else "Recheck thresholds") if connected else INDICATOR_UNKNOWN
        items["no_specimen_mode"] = Indicator("ON" if self.load_input.no_specimen else "OFF")
        diff = self.travel_cal.diff
        items["travel_cal_differs"] = (Indicator("ON", None, diff.source, diff.board_spm,
                                                 "Restore / keep board value / ignore for this session")
                                       if diff.differs and not diff.ignored else Indicator("OFF"))
        synth = d.info is not None and bool(d.info.feature_mask & pg.Features.AFE_SYNTHETIC)
        items["afe_synthetic"] = Indicator("ON" if synth else "OFF") if d.info else INDICATOR_UNKNOWN
        items["recording_failed"] = Indicator("ON" if self.recorder.state == "FAILED" else "OFF", None,
                                              None, None, self.recorder.failure)
        hs = self.hotkey_status()
        items["hotkey"] = Indicator("ON" if hs.mode in ("REGISTERED", "LL_HOOK") else "OFF", None, hs.mode, None,
                                    None if hs.mode != "UNAVAILABLE" else "Pause/Break key unavailable: " + hs.reason)
        return Indicators(items)

    def _gate_snapshot(self, now: int | None = None) -> GateSnapshot:
        d = self.device
        now = self.clock.monotonic_ns() if now is None else now
        st = d.board
        flags, status_bits = d.latest()
        vmask = d.valid_status_mask()
        fresh = d.last_data_ns is not None and now - d.last_data_ns <= 500 * MS
        lt = self.pipeline.latest_copy()
        raw = None if not lt.t_host_ns or lt.raw != lt.raw else float(lt.raw)
        hk = self.hotkey
        params = d.params.values()
        li = self.load_input
        lst = li.evaluate(params, d.info)
        cfg = self.session.get().limits
        x = self.motion.position_mm() if d.connected else None
        _lo, hi = self.motion.travel_range_mm()
        room = None if x is None or hi is None else hi - x
        latest_t = self._latest_t
        recent = (latest_t is not None and self._last_moving_t is not None
                  and latest_t - self._last_moving_t < 1_000_000)
        op = next((e.KIND for e in (self.load_cal, self.tare_engine) if e.active), None)
        return GateSnapshot(
            link=d.state, compat=d.compat, stream_on=d.stream_on, data_fresh=fresh, flags=flags,
            status=status_bits & vmask, faults=st.faults if st else 0, io=(st.io & d.valid_io_mask()) if st else 0,
            moving=self.motion.fw_moving() or self.motion.busy, recording=self.recorder.state != "IDLE",
            status_known=st is not None, hotkey_available=bool(hk is not None and hk.available),
            features=d.info.features if d.info is not None else frozenset(), valid_status=vmask,
            motion_state=("ENABLING" if not flags & INT_DF.ENABLED and self.motion.enabling_left_ms(now) > 0
                          else st.motion if st is not None else None), thresholds_state=d.thresholds.state,
            hotkey_test=bool(hk is not None and hk.test_mode_active), jogging=self.motion.jogging, raw=raw,
            zero_raw=int(params.get("safety.zero_raw", 0) or 0),
            home_max_load_raw=int(params.get("home.max_load_raw", 322_123) or 322_123), load_known=lst.valid,
            load_limits_on=(cfg.pull_enabled or cfg.push_enabled) and not li.no_specimen,
            load_input_valid=lst.valid, load_input_reason=lst.reason, no_specimen=li.no_specimen,
            thresholds_match=d.threshold_mgr.matches(d.thresholds),
            sw_trip=", ".join(t.limit for t in self.safety.active_trips()) or None, owner=self.owner, operation=op,
            capture_kinds=self.capture.kinds(), moved_recently=recent,
            afe_synthetic=bool(d.info is not None and d.info.feature_mask & pg.Features.AFE_SYNTHETIC),
            travel_cal_differs=self.travel_cal.diff.differs and not self.travel_cal.diff.ignored,
            travel_room_mm=room, sequence_paused=self.seq.paused,
            sequence_state=self.seq.run.state if self.seq.run is not None else "IDLE",
            seq_capture=self.seq.capture_open, recording_failed=self.recorder.state == "FAILED")

    def _gates(self, now: int | None = None) -> Mapping[GateId, GateResult]:
        return all_gates(self._gate_snapshot(now))

    def _motion_status(self, now: int, flags: int, status_bits: int, st: Any) -> MotionStatus:
        m = self.motion
        pos = m.position_mm() if self.device.connected else None
        hp = None
        if st is not None:
            try:
                hp = pg.HomePhase(st.home_phase).name
            except ValueError:
                hp = str(st.home_phase)
        return MotionStatus(
            moving=bool(flags & INT_DF.MOVING), homed=bool(flags & INT_DF.HOMED),
            enabled=bool(flags & INT_DF.ENABLED), enabling_left_ms=m.enabling_left_ms(now),
            paused=bool(status_bits & INT_DS.PAUSED), position_mm=pos,
            test_position_mm=None if pos is None else pos - m.x_zero_mm, commanded_target_mm=m.commanded_target_mm,
            pending_target_mm=m.pending_target_mm, x_zero_mm=m.x_zero_mm, owner=self.owner,
            motion_state=st.motion if st is not None else None, limits=m.limits() if self.device.connected else None,
            jogging=m.jogging, home_phase=hp, pos_uncertain=bool(status_bits & INT_DS.POS_UNCERTAIN))

    def status(self) -> BackendStatus:
        now = self.clock.monotonic_ns()
        d = self.device
        st = d.board
        flags, status_bits = d.latest()
        stats = self._link_stats()
        lt = self.pipeline.latest_copy()
        data_age = None if d.last_data_ns is None else (now - d.last_data_ns) / 1e6
        moving = bool(flags & INT_DF.MOVING)
        fw_rate = (st.afe_rate_dsps / 10.0) if st is not None and st.afe_rate_dsps else None
        self._status_seq += 1
        return BackendStatus(
            link=LinkStatus(d.state, d.why, d.endpoint, d.compat, d.info, stats),
            stream=StreamStatus(d.stream_on, lt.rate_sps, fw_rate,
                                bool(status_bits & INT_DS.AFE_RATE_MISMATCH), data_age),
            indicators=self._indicators(now),
            motion=self._motion_status(now, flags, status_bits, st),
            safety=self._safety_status(),
            calibration=self._calibration_status(),
            tare=self._tare_status(now),
            operation=self._operation_status(),
            recording=self.recorder.status(),
            gates=self._gates(now),
            hotkey=self.hotkey_status(),
            cfg_dirty=None if st is None else bool(st.sys_flags & INT_SYS.CFG_DIRTY),
            config_read_only=d.compat.config_read_only,
            reboot_pending=None if st is None else bool(st.sys_flags & INT_SYS.REBOOT_PENDING),
            nvm_defaulted=None if st is None else bool(st.sys_flags & INT_SYS.NVM_DEFAULTED),
            board=st, seq=self._status_seq)


    def _safety_status(self) -> SafetyStatus:
        d = self.device
        st = self.load_input.evaluate(d.params.values(), d.info)
        trip = self.safety.trip
        warns = self.safety.active_warnings() + (("FW_CLAMPED",) if d.thresholds.clamped else ())
        return SafetyStatus(None if trip is None else trip.limit, warns, d.thresholds, st.valid, st.reason,
                            self.load_input.no_specimen, trip, self.safety.active_trips())

    def _calibration_status(self) -> CalibrationStatus:
        d = self.device
        li = self.load_input
        cal = li.cal
        st = li.evaluate(d.params.values(), d.info)
        try:
            act = self.calibration_store.active_travel()
        except FileFormatError:
            act = None
        diff = self.travel_cal.diff
        invalid = None
        if cal is None:
            invalid = li.cal_error or "no load calibration"
        elif st.afe_mismatch:
            invalid = "calibration invalid: AFE configuration differs (gain / channel / rate)"
        return CalibrationStatus(
            load_k=None if cal is None else cal.k, load_status=None if cal is None else cal.status,
            low_span=bool(cal is not None and cal.low_span), load_date=None if cal is None else cal.created_utc,
            load_valid_for_limits=st.cal_valid, travel_spm=None if act is None else float(act["spm2"]),
            travel_date=None if act is None else act.get("created_utc"), board_spm=d.params.get("motion.steps_per_mm"),
            travel_cal_differs=diff.differs and not diff.ignored, restore_pending=diff.restore_pending,
            travel_diff=diff, load_invalid_reason=invalid, f_cal_max_n=None if cal is None else cal.f_cal_max,
            load_file=None if cal is None else cal.file)

    def _tare_status(self, now: int) -> TareStatus:
        info = self.device.info
        t = self.load_input.tare_for(info.uid if info is not None else None)
        eng = self.tare_engine
        state = "CAPTURING" if eng.active else ("ACTIVE" if t is not None else "NONE")
        age = None if t is None or eng.tare_host_ns is None else (now - eng.tare_host_ns) / 1e9
        return TareStatus(state, None if t is None else t.tare_raw, age, None if t is None else t.tare_id,
                          self.load_input.can_undo())

    def _operation_status(self) -> OperationStatus:
        seq = self.seq.status()
        if self.seq.active:
            return OperationStatus(self.owner, f"sequence:{seq.phase}", seq)
        for e in (self.travel_cal, self.load_cal, self.tare_engine):
            if e.active:
                return OperationStatus(self.owner, f"{e.KIND}:{e.state().phase}", seq)
        return OperationStatus(self.owner, None, seq)


__all__ = ["Backend", "BackendSettings", "TWIN_ENDPOINT", "SIM_SERVER_ENDPOINT"]
