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
test mode, feature-dependent indicators UNKNOWN (D-37 b). M3–M4 members exist (``core.api``) and refuse with a
``NOT_IMPLEMENTED`` gate item or raise ``NotImplementedError`` for pure accessors.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/backend.py @37c87471 (facade pattern; rewritten for §15).

Implements: SW-PLT-003, SW-ACQ-001, SW-ACQ-002 (record start/stop), SW-CFG-001…004 (API), SW-STOP-001/002
(backend part), SAF-SW-005 (indicators incl. UNKNOWN, D-37 b), NFR-002 (priority path from the GUI thread),
SW-MAN-001…006 / SW-LIM-001 (M2 backend part), SW-STOP-002 (hotkey), SAF-SW-002 (M2 part)
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Literal

from bend_stand import __version__
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.channels import ChannelRegistry
from bend_stand.core.clock import MONOTONIC, Clock, LockstepClock, wall_utc_iso
from bend_stand.core.dataview import DataView
from bend_stand.core.device import Device, DeviceSettings
from bend_stand.core.errors import ConfirmationRequired, GateRefused, RecorderError
from bend_stand.core.events import EventBus
from bend_stand.core.gates import CLEAR_HINTS, GateSnapshot, all_gates, g_hotkey_test
from bend_stand.core.jobs import Job, Worker
from bend_stand.core.link import SUPERVISOR_TICK_NS
from bend_stand.core.liveness import LivenessMonitor
from bend_stand.core.motion import MotionController
from bend_stand.core.model import (
    INT_DF, INT_DS, INT_SYS,
    INDICATOR_UNKNOWN, BackendStatus, BoardConfigFile, CalibrationStatus, ClearResult, DeviceInfo, EndpointInfo,
    EngineState, GateCode, GateId, GateItem, GateResult, HotkeyStatus, Indicator, Indicators, Issue, IssueSeverity,
    LimitConfig, LinkState, LinkStatus, MotionStatus, OperationStatus, SafetyStatus,
    SeqStatus, SessionSettings, Severity, StopResult, StreamStatus, TestMarks, ThresholdState, Token,
    TravelDiffState, VerifyReport,
)
from bend_stand.core.observers import ReleasingFuture, done_future, failed_future
from bend_stand.core.params import LOCKED_KEYS, check_edits, load_board_config, save_board_config
from bend_stand.core.pipeline import Pipeline
from bend_stand.core.recorder import Recorder
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
    """``backend.limits``: SW travel limits (SW-LIM-001, M2), FW load thresholds (SAF-SW-002: default / manual raw
    in M2, calibrated in M3); SW load limits and the no-specimen mode are M3."""

    def __init__(self, be: Backend) -> None:
        self._be = be
        self._cfg = LimitConfig()

    def get(self) -> LimitConfig:
        return self._cfg

    def check(self, cfg: LimitConfig) -> list[Issue]:
        """Travel limits inside the FW soft limits, min < max (SW-LIM-001); SW load limits / FW level: M3 (kept)."""
        out: list[Issue] = []
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
        if cfg.fw_level_n > 2157.46 + 1e-6:
            out.append(Issue("fw_level_n", E, "FW_LEVEL", "FW load-limit level above 110 % FS (SW-LIM-002)"))
        return out

    def set(self, cfg: LimitConfig) -> list[Issue]:
        """Apply the limits (refused while moving); returns the ERROR issues (empty = applied)."""
        if self._be.device.last_flags & INT_DF.MOVING:
            return [Issue(None, IssueSeverity.ERROR, "MOVING", "limits cannot be changed while the axis moves")]
        issues = [i for i in self.check(cfg) if i.severity == IssueSeverity.ERROR]
        if not issues:
            self._cfg = cfg
            self._be.events.publish("log", {"text": "SW travel limits set"})
        return issues

    def thresholds(self) -> ThresholdState:
        return self._be.device.thresholds

    def recheck_async(self) -> Future[ThresholdState]:
        return self._be._job(self._be.device.session_values_job)  # noqa: SLF001

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
        return _not_implemented("no-specimen mode", "M3")


class MarksAPI:
    def __init__(self) -> None:
        self._marks = TestMarks()

    def get(self) -> TestMarks:
        return self._marks

    def set(self, marks: TestMarks) -> None:
        self._marks = marks

    def list_presets(self) -> list[str]:
        return []

    def save_preset(self, path: str) -> None:
        raise NotImplementedError("mark presets: M3")

    def load_preset(self, path: str) -> TestMarks:
        raise NotImplementedError("mark presets: M3")


class SessionAPI:
    def __init__(self) -> None:
        self._s = SessionSettings()

    def get(self) -> SessionSettings:
        return self._s

    def set(self, settings: SessionSettings) -> list[Issue]:
        self._s = settings
        return []

    def load(self, path: str) -> SessionSettings:
        raise NotImplementedError("session files: M3")

    def save(self, path: str) -> None:
        raise NotImplementedError("session files: M3")


class StubEngine:
    """Calibration / tare engines (M3): ``state()`` reports NOT_IMPLEMENTED, ``start`` refuses."""

    def __init__(self, kind: str, phases: tuple[str, ...], milestone: str = "M3") -> None:
        self.kind = kind
        self.PHASES = phases
        self.milestone = milestone

    def state(self) -> EngineState:
        return EngineState(self.kind, "IDLE", title=f"{self.kind}: available from {self.milestone}")

    def subscribe(self, cb: Callable[[EngineState], None]) -> Token:
        return 0

    def start(self, **config: Any) -> GateResult:
        return _not_implemented(self.kind, self.milestone)

    def continue_(self, inputs: Mapping[str, float] | None = None, *, confirmed: bool = False) -> None:
        return None

    def repeat(self) -> None:
        return None

    def cancel(self) -> None:
        return None

    def undo(self) -> GateResult:
        return _not_implemented(self.kind, self.milestone)

    def finish_early(self) -> None:
        return None

    def retake(self, i: int) -> None:
        return None


class CalibrationStoreAPI:
    def active_load(self) -> Mapping[str, Any] | None:
        return None

    def active_travel(self) -> Mapping[str, Any] | None:
        return None

    def history(self, kind: str) -> list[str]:
        return []

    def restore_travel_async(self) -> Future[TravelDiffState]:
        return done_future(TravelDiffState())

    def resolve_travel_difference_async(self, action: str) -> Future[TravelDiffState]:
        return done_future(TravelDiffState())


class SequencerAPI:
    def new(self) -> Any:
        raise NotImplementedError("sequencer: M4")

    def load(self, path: str) -> Any:
        raise NotImplementedError("sequencer: M4")

    def save(self, seq: Any, path: str) -> None:
        raise NotImplementedError("sequencer: M4")

    def validate(self, seq: Any) -> list[Issue]:
        raise NotImplementedError("sequencer: M4")

    def expand(self, seq: Any) -> Any:
        raise NotImplementedError("sequencer: M4")

    def planned_path(self, seq: Any) -> Any:
        raise NotImplementedError("sequencer: M4")

    def generator_schemas(self) -> Mapping[str, Any]:
        return {}

    def start(self, seq: Any, *, confirmed: bool = False) -> GateResult:
        return _not_implemented("sequencer", "M4")

    def pause(self) -> StopResult:
        return StopResult("PAUSE", "sequencer", False, None, "sequencer: M4")

    def resume(self) -> GateResult:
        return _not_implemented("sequencer", "M4")

    def stop(self) -> StopResult:
        return StopResult("STOP", "sequencer", False, None, "sequencer: M4")

    def abort(self) -> StopResult:
        return StopResult("HALT", "sequencer", False, None, "sequencer: M4")

    def status(self) -> SeqStatus:
        return SeqStatus()


class ReportAPI:
    def build_async(self, rec_dir: str, cal: Any = None, tare: Any = None, bend3p: Any = None) -> Future[Any]:
        return failed_future(NotImplementedError("reports: M4"))

    def list_recordings(self, root: str | None = None) -> list[str]:
        return []

    def load_result(self, rec_dir: str) -> Any:
        raise NotImplementedError("reports: M4")


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
        self.recorder = Recorder(self.clock, on_state=lambda st: self.events.publish("rec.state", st))
        self.pipeline.sinks.append(self.recorder.on_row)
        self.pipeline.event_sinks.append(self.recorder.on_event)
        self.channels = ChannelRegistry()
        self.channels.on_change = lambda: self.events.publish("channels.changed", None)
        self.data = DataView(self.pipeline, self.clock.monotonic_ns)
        self.config = ConfigAPI(self)
        self.limits = LimitsAPI(self)
        self.motion = MotionController(self)
        self.marks = MarksAPI()
        self.session = SessionAPI()
        self.tare_engine = StubEngine("tare", ("CHECK", "CAPTURE", "EVALUATE", "DONE"))
        self.travel_cal = StubEngine("travel_cal", ("CHECK", "BACKLASH", "REFERENCE", "MOVE1", "ENTER_D1", "MOVE2",
                                                    "ENTER_DTOT", "RESULT", "ACCEPT", "DONE"))
        self.load_cal = StubEngine("load_cal", ("CONFIG", "AWAIT_OPERATOR", "PRESETTLE", "CAPTURE", "EVALUATE",
                                                "FIT", "ACCEPT", "DONE"))
        self.calibrations = CalibrationStoreAPI()
        self.sequencer = SequencerAPI()
        self.reports = ReportAPI()
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
        self.worker.start()
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
            self.worker.stop()
            self.pipeline.stop()
            self._stop_sim()
            hk, self.hotkey = self.hotkey, None
            if hk is not None:
                hk.stop()
            self.liveness.uninstall_excepthook()
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
        self.device.tick(now)
        self.motion.tick(now)
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

    def _on_link_change(self, state: LinkState) -> None:
        if state == LinkState.LOST:
            self.events.log("LINK LOST", logging.ERROR)
        if state in (LinkState.LOST, LinkState.DISCONNECTED):
            self.motion.on_link_down()

    def _on_fw_event(self, ev: Any) -> None:
        """Pipeline thread: link-level reactions first (epoch, BOOT resync), then the motion controller."""
        self.device.handle_fw_event(ev)
        self.motion.on_fw_event(ev)

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
        self._after_stop("STOP")
        return res

    def halt(self, source: str = "gui") -> StopResult:
        try:
            res = self.device.halt(source)
        except Exception as exc:  # noqa: BLE001
            res = StopResult("HALT", source, False, None, str(exc))
        self._after_stop("HALT")
        return res

    def pause(self, source: str = "gui") -> StopResult:
        try:
            res = self.device.pause(source)
        except Exception as exc:  # noqa: BLE001
            res = StopResult("PAUSE", source, False, None, str(exc))
        self._after_stop("PAUSE")
        return res

    def _after_stop(self, cmd: str) -> None:
        """… then drop the jog session and the pending target (SW_design §4.5 (2))."""
        try:
            self.motion.on_stop_issued(cmd)
        except Exception:  # noqa: BLE001 pragma: no cover - never raises
            log.exception("motion.on_stop_issued failed")

    def resume(self, source: str = "gui") -> GateResult:
        """M1 manual Resume = RESUME 0x3C only (no motion re-issue; the sequencer's re-issue is M4)."""
        g = self.status().gates[GateId.RESUME]
        if g.ok:
            fut = self.device.clear_async(pg.Cmd.RESUME)       # submitted at once (CONTROL lane), no Worker job
            fut.add_done_callback(self._publish_resume)
        else:
            from bend_stand.core.model import ResumeIgnored  # noqa: PLC0415

            self.events.publish("resume.ignored", ResumeIgnored("gui" if source != "button" else "button", g))
        return g

    def _publish_resume(self, f: Future[ClearResult]) -> None:
        if f.exception() is None:
            self.events.publish("log", {"text": f"RESUME: {f.result().outcome} {f.result().text}"})

    def tare(self, window_s: float | None = None) -> GateResult:
        return _not_implemented("tare", "M3")

    def take_sample(self, window_s: float = 1.0) -> GateResult:
        return _not_implemented("take sample", "M3")

    def record_start(self) -> GateResult:
        g = self.status().gates[GateId.RECORD_START]
        if not g.ok:
            return g
        root = self.settings.recordings_root or os.path.join(os.path.expanduser("~"), "Documents", "BirdBendStand",
                                                             "recordings")
        info = self.device.info
        meta = {"sw_version": __version__, "marks_at_start": vars(self.marks.get()),
                "board_params": self.device.params.values(),
                "thresholds": vars(self.device.thresholds), "no_specimen_mode": False}
        if info is not None:
            meta.update(fw_version=".".join(map(str, info.fw_version)), board_uid=info.uid, build=info.build)
        try:
            m = self.marks.get()
            self.recorder.start(root, meta, specimen=m.specimen, number=m.number)
        except RecorderError as exc:
            return GateResult((GateItem(GateCode.RECORDING_ACTIVE, Severity.REFUSE, exc.user_text),))
        except OSError as exc:                     # never surface the raw (localised) OS text (SWD-M1-09)
            return GateResult((GateItem(GateCode.RECORDING_ACTIVE, Severity.REFUSE,
                                        f"cannot start the recording (file error {exc.errno})"),))
        return g

    def record_stop(self) -> GateResult:
        g = self.status().gates[GateId.RECORD_STOP]
        if g.ok:
            self.recorder.stop({"link_stats": vars(self._link_stats())})
        return g

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
        items["sw_trip"] = Indicator("OFF")
        th = d.thresholds
        items["thresholds_state"] = Indicator("ON" if th.state in ("VERIFIED", "DEFAULT_ONLY") else "OFF", None,
                                              th.state) if connected else INDICATOR_UNKNOWN
        items["no_specimen_mode"] = Indicator("OFF")
        items["travel_cal_differs"] = Indicator("OFF")
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
            home_max_load_raw=int(params.get("home.max_load_raw", 322_123) or 322_123), load_known=False)

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
            pending_target_mm=m.pending_target_mm, x_zero_mm=m.x_zero_mm, owner="MANUAL",
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
            safety=SafetyStatus(thresholds=d.thresholds),
            calibration=CalibrationStatus(board_spm=d.params.get("motion.steps_per_mm")),
            operation=OperationStatus(),
            recording=self.recorder.status(),
            gates=self._gates(now),
            hotkey=self.hotkey_status(),
            cfg_dirty=None if st is None else bool(st.sys_flags & INT_SYS.CFG_DIRTY),
            config_read_only=d.compat.config_read_only,
            reboot_pending=None if st is None else bool(st.sys_flags & INT_SYS.REBOOT_PENDING),
            nvm_defaulted=None if st is None else bool(st.sys_flags & INT_SYS.NVM_DEFAULTED),
            board=st, seq=self._status_seq)


__all__ = ["Backend", "BackendSettings", "TWIN_ENDPOINT", "SIM_SERVER_ENDPOINT"]
