"""Backend facade — the only object the GUI constructs (SW_design §15), M1 subset (WP-B11).

Threads on the real clock: Reader, Pipeline, Supervisor (5 ms tick: link, heartbeat, confirmations, polls),
Worker (generator jobs), Recorder, simulator board (``sim`` endpoint). With ``BackendSettings(clock="lockstep",
test_hooks=True)`` **no thread is started**: ``test_hooks.advance(ms, step_ms=1)`` advances the
``LockstepClock`` and runs per step, in this order: simulator → (transport delivery) → Reader → Pipeline →
Supervisor → Worker → Recorder (§12.4 hook a). Fully deterministic.

M1: connect / disconnect (``sim``, ``sim:<file>``, ``COMx`` only on the operator's choice (D-06), ``tcp://``),
stream, configuration (read / check / write+verify / NVM / reboot / board-config file), STOP / HALT / PAUSE
(priority path), RESUME and the clears (VERIFY), recording skeleton, ``status()`` with link / stream / indicators
(UNKNOWN handling) / sys_flags / gates, data view and channel registry. M2–M4 members exist (``core.api``) and
refuse with a ``NOT_IMPLEMENTED`` gate item or raise ``NotImplementedError`` for pure accessors.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/backend.py @37c87471 (facade pattern; rewritten for §15).

Implements: SW-PLT-003, SW-ACQ-001, SW-ACQ-002 (record start/stop), SW-CFG-001…004 (API), SW-STOP-001/002
(backend part), SAF-SW-005 (indicators incl. UNKNOWN), NFR-002 (priority path from the GUI thread)
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
from bend_stand.core.errors import ConfirmationRequired, GateRefused
from bend_stand.core.events import EventBus
from bend_stand.core.gates import CLEAR_HINTS, GateSnapshot, all_gates
from bend_stand.core.jobs import Job, Worker
from bend_stand.core.link import SUPERVISOR_TICK_NS
from bend_stand.core.liveness import LivenessMonitor
from bend_stand.core.model import (
    INDICATOR_UNKNOWN, BackendStatus, BoardConfigFile, CalibrationStatus, ClearResult, DeviceInfo, EndpointInfo,
    EngineState, GateCode, GateId, GateItem, GateResult, HotkeyStatus, Indicator, Indicators, Issue, LimitConfig,
    LinkState, LinkStatus, MotionKind, MotionLimits, MotionStatus, OperationStatus, SafetyStatus,
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
        return check_edits(d.params.values(), edits, moving=bool(d.last_flags & pg.DataFlags.MOVING))

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


class MotionAPI:
    """``backend.motion`` — M2 (WP-B12). M1: every command refuses locally; nothing is sent."""

    def __init__(self, be: Backend) -> None:
        self._be = be

    def _refused(self) -> ReleasingFuture:
        return failed_future(GateRefused(_not_implemented("motion", "M2")))

    def move_to(self, target_mm: float, *, speed_mm_s: float | None = None,
                accel_mm_s2: float | None = None) -> ReleasingFuture:
        return self._refused()

    def move_by(self, delta_mm: float, *, speed_mm_s: float | None = None,
                accel_mm_s2: float | None = None) -> ReleasingFuture:
        return self._refused()

    def jog_start(self, direction: int, speed_mm_s: float) -> None:
        return None

    def jog_update(self, speed_mm_s: float) -> None:
        return None

    def jog_stop(self) -> None:
        return None

    def enable(self) -> GateResult:
        return _not_implemented("enable", "M2")

    def disable(self, *, confirmed: bool = False) -> GateResult:
        return _not_implemented("disable", "M2")

    def home(self, *, load_confirmed: bool = False) -> ReleasingFuture:
        return self._refused()

    def set_test_zero(self) -> float:
        raise NotImplementedError("test zero: M3")

    def reset_test_zero(self) -> None:
        raise NotImplementedError("test zero: M3")

    def set_valid(self, flag: bool) -> GateResult:
        """Manual VALID toggle (SW-MAN-006) — wire part available in M1."""
        g = self._be.status().gates[GateId.VALID_TOGGLE]
        if g.ok:
            self._be._job(self._be.device.set_valid_job, bool(flag))  # noqa: SLF001
        return g

    def limits(self) -> MotionLimits | None:
        return None

    def check(self, kind: MotionKind, *, speed_mm_s: float | None = None, accel_mm_s2: float | None = None,
              target_mm: float | None = None) -> GateResult:
        return _not_implemented(f"{kind.value.lower()} check", "M2")


class LimitsAPI:
    def __init__(self, be: Backend) -> None:
        self._be = be
        self._cfg = LimitConfig()

    def get(self) -> LimitConfig:
        return self._cfg

    def set(self, cfg: LimitConfig) -> list[Issue]:
        return [Issue(None, "ERROR", "NOT_IMPLEMENTED", "SW limits: available from M3")]  # type: ignore[arg-type]

    def thresholds(self) -> ThresholdState:
        return self._be.device.thresholds

    def recheck_async(self) -> Future[ThresholdState]:
        return self._be._job(self._be.device.session_values_job)  # noqa: SLF001

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
        self.pipeline = Pipeline(self.clock, self.events, self.liveness, on_fw_event=self.device.handle_fw_event,
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
        self.motion = MotionAPI(self)
        self.limits = LimitsAPI(self)
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

    # ---- lifecycle -----------------------------------------------------------------------------------
    def start(self) -> None:
        """Supervisor, Worker, Pipeline (hotkey: M3). No port is opened (D-06)."""
        if self._started:
            return
        self._started = True
        self.liveness.install_excepthook()
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
            if self.device.last_flags & pg.DataFlags.MOVING:
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
                self.device.tick(self.clock.monotonic_ns())
            except Exception:  # noqa: BLE001 — the supervisor must survive a bad tick
                log.exception("supervisor tick failed")

    def _step_all(self, now: int) -> None:
        """One lockstep step: simulator → Reader → Pipeline → Supervisor → Worker → Recorder."""
        if self.sim_endpoint is not None:
            self.sim_endpoint.step(now)
        rd = self.device.reader
        if rd is not None:
            rd.step(now)
        self.pipeline.step(now)
        self.device.tick(now)
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

    # ---- global actions (GUI thread, non-blocking, never raise) --------------------------------------
    def stop(self, source: str = "gui") -> StopResult:
        try:
            return self.device.stop(pg.StopMode.IMMEDIATE, source)
        except Exception as exc:  # noqa: BLE001 — never raises (§14)
            return StopResult("STOP", source, False, None, str(exc))

    def halt(self, source: str = "gui") -> StopResult:
        try:
            return self.device.halt(source)
        except Exception as exc:  # noqa: BLE001
            return StopResult("HALT", source, False, None, str(exc))

    def pause(self, source: str = "gui") -> StopResult:
        try:
            return self.device.pause(source)
        except Exception as exc:  # noqa: BLE001
            return StopResult("PAUSE", source, False, None, str(exc))

    def resume(self, source: str = "gui") -> GateResult:
        """M1 manual Resume = RESUME 0x3C only (no motion re-issue; the sequencer's re-issue is M4)."""
        g = self.status().gates[GateId.RESUME]
        if g.ok:
            fut = self._job(self.device.clear_job, pg.Cmd.RESUME)
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
        except (OSError, Exception) as exc:  # noqa: BLE001
            return GateResult((GateItem(GateCode.RECORDING_ACTIVE, Severity.REFUSE, f"cannot start: {exc}"),))
        return g

    def record_stop(self) -> GateResult:
        g = self.status().gates[GateId.RECORD_STOP]
        if g.ok:
            self.recorder.stop({"link_stats": vars(self._link_stats())})
        return g

    def hotkey_test_start(self, timeout_s: float = 10.0) -> GateResult:
        return _not_implemented("Pause/Break key test", "M3")

    # ---- actions that wait for the board ---------------------------------------------------------------
    def clear_stop_async(self, *, confirmed: bool = False) -> Future[ClearResult]:
        g = self.status().gates[GateId.CLEAR_STOP]
        if g.confirm_items and not confirmed:
            return failed_future(ConfirmationRequired(g))
        return self._job(self.device.clear_job, pg.Cmd.HALT_CLEAR)

    def estop_clear_async(self, *, confirmed: bool) -> Future[ClearResult]:
        if not confirmed:
            return failed_future(ConfirmationRequired(self.status().gates[GateId.ESTOP_CLEAR]))
        return self._job(self.device.clear_job, pg.Cmd.ESTOP_CLEAR)

    def fault_clear_async(self) -> Future[ClearResult]:
        return self._job(self.device.clear_job, pg.Cmd.FAULT_CLEAR)

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

    def _indicators(self, now: int) -> Indicators:
        d = self.device
        connected = d.connected
        data_ok = connected and d.stream_on and d.last_data_ns is not None and now - d.last_data_ns <= DATA_STALE_NS
        st = d.board
        status_ok = connected and st is not None and now - d.board_ns <= STATUS_STALE_NS
        flags, status = d.latest()
        live = data_ok or status_ok
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
                if n == "HALT" and st is not None and flags & pg.DataFlags.HALT:
                    src = pg.Source(st.halt_src).name if st.halt_src in (0, 1, 2) else None
                put(n, bool(flags >> i & 1), live, source=src)
        for i, n in enumerate(pg.DATA_STATUS_BITS):
            if n:
                src = None
                if n == "PAUSED" and st is not None and status & pg.DataStatus.PAUSED:
                    src = pg.Source(st.pause_src).name if st.pause_src in (0, 1, 2) else None
                put(n, bool(status >> i & 1), live, source=src)
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
        items["hotkey"] = Indicator("OFF", None, "UNAVAILABLE")
        return Indicators(items)

    def status(self) -> BackendStatus:
        now = self.clock.monotonic_ns()
        d = self.device
        st = d.board
        flags, status_bits = d.latest()
        stats = self._link_stats()
        lt = self.pipeline.latest_copy()
        data_age = None if d.last_data_ns is None else (now - d.last_data_ns) / 1e6
        moving = bool(flags & pg.DataFlags.MOVING)
        snap = GateSnapshot(link=d.state, compat=d.compat, stream_on=d.stream_on,
                            data_fresh=data_age is not None and data_age <= 500, flags=flags, status=status_bits,
                            faults=st.faults if st else 0, io=st.io if st else 0, moving=moving,
                            recording=self.recorder.state != "IDLE", status_known=st is not None)
        fw_rate = (st.afe_rate_dsps / 10.0) if st is not None and st.afe_rate_dsps else None
        self._status_seq += 1
        return BackendStatus(
            link=LinkStatus(d.state, d.why, d.endpoint, d.compat, d.info, stats),
            stream=StreamStatus(d.stream_on, lt.rate_sps, fw_rate,
                                bool(status_bits & pg.DataStatus.AFE_RATE_MISMATCH), data_age),
            indicators=self._indicators(now),
            motion=MotionStatus(moving=moving, homed=bool(flags & pg.DataFlags.HOMED),
                                enabled=bool(flags & pg.DataFlags.ENABLED),
                                paused=bool(status_bits & pg.DataStatus.PAUSED),
                                position_mm=None if st is None and lt.t_host_ns == 0 else
                                (lt.x_mm if lt.t_host_ns else (st.pos_um / 1000.0 if st else None)),
                                motion_state=st.motion if st else None),
            safety=SafetyStatus(thresholds=d.thresholds),
            calibration=CalibrationStatus(board_spm=d.params.get("motion.steps_per_mm")),
            operation=OperationStatus(),
            recording=self.recorder.status(),
            gates=all_gates(snap),
            hotkey=HotkeyStatus("UNAVAILABLE", "global hotkey: M3", False),
            cfg_dirty=None if st is None else bool(st.sys_flags & pg.SysFlags.CFG_DIRTY),
            config_read_only=d.compat.config_read_only,
            reboot_pending=None if st is None else bool(st.sys_flags & pg.SysFlags.REBOOT_PENDING),
            nvm_defaulted=None if st is None else bool(st.sys_flags & pg.SysFlags.NVM_DEFAULTED),
            board=st, seq=self._status_seq)


__all__ = ["Backend", "BackendSettings", "TWIN_ENDPOINT", "SIM_SERVER_ENDPOINT"]
