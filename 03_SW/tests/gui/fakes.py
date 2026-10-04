"""``FakeBackend``: pure-Python implementation of the ``core.api`` Protocols for GUI tests (SW_design_GUI §10.1).

It records every call with a monotonic timestamp (``calls``), has a scriptable ``status()`` (``set_status``,
``set_indicators``, ``set_gate``), synchronous futures, an EventBus with weak subscriptions (``events.emit``), a
dictionary-backed ``config`` (generated ``params_gen`` metadata, scripted write statuses, H4 rule) and a
``data`` view that synthesises ``PlotSnapshot`` columns. ``test_fake_conformance.py`` checks it against the
Protocols so it cannot drift from B's API.

Origin: pattern of Thrust_Stand_HAW/03_SW/tests/gui/fakes.py @37c87471 (rewritten for the bend-stand API).

D-06: the fake never touches a port; ``endpoints()`` lists only "sim" and the tcp twin.
"""
from __future__ import annotations

import dataclasses
import json
import time
import weakref
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from types import MappingProxyType
from typing import Any, Literal

import numpy as np

from bend_stand.core import params_gen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.api import (
    GATE_OK, BackendStatus, BoardConfigFile, ChannelSpec, ClearResult, Compat, DeviceInfo, EndpointInfo,
    EngineState, EventRecord, FileFormatError, GateId, GateItem, GateResult, HotkeyStatus, Indicator, Issue,
    IssueSeverity, LatestSample, LimitConfig, LinkState, LinkStats, LinkStatus, MotionKind, MotionLimits,
    MoveOutcome, PlotSnapshot, SeqStatus, SeriesMinMax, SessionSettings, Severity, StopResult, StreamStatus,
    TestMarks, ThresholdState, TravelDiffState, VerifyReport, WriteItem, WriteStatus, XYSnapshot,
)

NOT_IMPL = GateResult((GateItem("NOT_IMPLEMENTED", Severity.REFUSE, "not implemented in M1"),))


def tick(win: Any, n: int = 1) -> None:
    """Run ``n`` refresh ticks synchronously (the refresh timer is not started in tests)."""
    for _ in range(n):
        win.refresh.tick()


def done(value: Any = None) -> Future:
    f: Future = Future()
    f.set_result(value)
    return f


def failed(exc: BaseException) -> Future:
    f: Future = Future()
    f.set_exception(exc)
    return f


def refuse(code: str, text: str, hint: str | None = None) -> GateResult:
    return GateResult((GateItem(code, Severity.REFUSE, text, hint),))


def confirm(code: str, text: str) -> GateResult:
    return GateResult((GateItem(code, Severity.CONFIRM, text),))


def warn(code: str, text: str) -> GateResult:
    return GateResult((GateItem(code, Severity.WARN, text),))


def ind(state: str, source: str | None = None, value: float | None = None, hint: str | None = None) -> Indicator:
    return Indicator(state, 1_000_000, source, value, hint)


@dataclasses.dataclass(frozen=True)
class Call:
    name: str
    args: tuple
    kwargs: dict
    t_ns: int


# --------------------------------------------------------------------------------------------- event bus

class FakeEventBus:
    def __init__(self) -> None:
        self._subs: dict[int, tuple[str, Any, bool]] = {}
        self._next = 1
        self._history: list[EventRecord] = []

    def subscribe(self, topic: str, cb: Callable[[EventRecord], None], *, weak: bool = True) -> int:
        tok = self._next
        self._next += 1
        ref: Any = weakref.WeakMethod(cb) if weak and hasattr(cb, "__self__") else cb
        self._subs[tok] = (topic, ref, weak and hasattr(cb, "__self__"))
        return tok

    def unsubscribe(self, token: int) -> None:
        self._subs.pop(token, None)

    def history(self, topic: str | None = None, limit: int | None = None) -> list[EventRecord]:
        h = [r for r in self._history if topic is None or r.topic == topic]
        return h[-limit:] if limit else h

    def topics(self) -> list[str]:
        return sorted({t for t, _r, _w in self._subs.values()})

    def emit(self, topic: str, payload: Any = None) -> EventRecord:
        rec = EventRecord(topic, time.time_ns(), payload)
        self._history.append(rec)
        for t, ref, is_weak in list(self._subs.values()):
            if t != topic:
                continue
            cb = ref() if is_weak else ref
            if cb is not None:
                cb(rec)
        return rec


# --------------------------------------------------------------------------------------------- config

class FakeConfig:
    LOCKED = frozenset({"safety.load_raw_min", "safety.load_raw_max", "safety.zero_raw"})

    def __init__(self, owner: "FakeBackend") -> None:
        self._owner = owner
        self.board: dict[str, Any] = {p.key: p.default for p in params_gen.PARAMS}
        self.script: dict[str, WriteStatus] = {}          # key -> forced status for the next write
        self.files: dict[str, Any] = {}
        self.load_result: BoardConfigFile | None = None

    def metas(self) -> Sequence[Any]:
        return params_gen.PARAMS

    def groups(self) -> Sequence[tuple[str, str, tuple[str, ...]]]:
        return params_gen.GROUPS

    def values(self) -> Mapping[str, Any]:
        return dict(self.board)

    def locked_keys(self) -> frozenset[str]:
        return self.LOCKED

    def check(self, edits: Mapping[str, Any]) -> list[Issue]:
        self._owner._rec("config.check", dict(edits))
        out: list[Issue] = []
        for k, v in edits.items():
            meta = params_gen.BY_KEY.get(k)
            if meta is None:
                out.append(Issue(k, IssueSeverity.ERROR, "UNKNOWN", f"unknown parameter {k}"))
            elif not meta.in_range(v):
                out.append(Issue(k, IssueSeverity.ERROR, "RANGE", f"{k} out of range"))
        merged = {**self.board, **edits}
        if merged["motion.v_max_load_um_s"] > merged["motion.v_max_travel_um_s"]:
            out.append(Issue("motion.v_max_load_um_s", IssueSeverity.ERROR, "H4",
                             "motion.v_max_load_um_s <= motion.v_max_travel_um_s"))
        return out

    def write_and_verify_async(self, edits: Mapping[str, Any]) -> Future:
        self._owner._rec("config.write_and_verify_async", dict(edits))
        items = []
        reboot = False
        for k, v in edits.items():
            meta = params_gen.BY_KEY[k]
            st = self.script.pop(k, None) or (WriteStatus.REBOOT_REQUIRED if meta.reboot_required else WriteStatus.OK)
            stored = self.board[k]
            if st in (WriteStatus.OK, WriteStatus.REBOOT_REQUIRED):
                self.board[k] = v
                stored = v
                reboot = reboot or st == WriteStatus.REBOOT_REQUIRED
            elif st == WriteStatus.MISMATCH:
                self.board[k] = stored = meta.default
            items.append(WriteItem(k, v, stored, st, text="" if st == WriteStatus.OK else f"{st.value} (scripted)"))
        self._owner.set_status(cfg_dirty=True, reboot_pending=reboot or bool(self._owner.status().reboot_pending))
        return done(VerifyReport(tuple(items), cfg_dirty=True, reboot_pending=reboot))

    def read_all_async(self) -> Future:
        self._owner._rec("config.read_all_async")
        return done(dict(self.board))

    def save_async(self) -> Future:
        self._owner._rec("config.save_async")
        self._owner.set_status(cfg_dirty=False)
        return done(None)

    def load_async(self) -> Future:
        self._owner._rec("config.load_async")
        return done(dict(self.board))

    def defaults_async(self) -> Future:
        self._owner._rec("config.defaults_async")
        self.board = {p.key: p.default for p in params_gen.PARAMS}
        self._owner.set_status(cfg_dirty=True)
        return done(dict(self.board))

    def reboot_async(self) -> Future:
        self._owner._rec("config.reboot_async")
        self._owner.set_status(reboot_pending=False)
        return done(None)

    def save_board_config(self, path: str, values: Mapping[str, Any] | None = None) -> None:
        self._owner._rec("config.save_board_config", path, dict(values or {}))
        vals = dict(values if values is not None else self.board)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"format": "bird.bend.board/1", "param_dict_hash": params_gen.PARAM_DICT_HASH,
                       "values": vals}, fh)

    def load_board_config(self, path: str) -> BoardConfigFile:
        self._owner._rec("config.load_board_config", path)
        if self.load_result is not None:
            return self.load_result
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise FileFormatError(f"not a board configuration file: {exc}") from exc
        vals = dict(data.get("values", {}))
        unknown = tuple(k for k in vals if k not in params_gen.BY_KEY)
        oor = tuple(k for k, v in vals.items() if k in params_gen.BY_KEY and not params_gen.BY_KEY[k].in_range(v))
        missing = tuple(k for k in params_gen.BY_KEY if k not in vals and k not in self.LOCKED)
        good = {k: v for k, v in vals.items() if k not in unknown and k not in oor}
        return BoardConfigFile(path, good, unknown, missing, oor,
                               data.get("param_dict_hash") != params_gen.PARAM_DICT_HASH,
                               data.get("param_dict_hash"))


# --------------------------------------------------------------------------------------------- data

def default_channels() -> list[ChannelSpec]:
    """Registry like B's ``core.channels`` (B4-08: ``dimension`` filled; status bits ``bit.<name>``)."""
    specs = [ChannelSpec("raw", "raw", "counts", "DATA", dimension="counts"),
             ChannelSpec("setpoint_um", "setpoint", "µm", "DATA", dimension="length"),
             ChannelSpec("rate_sps", "sample rate", "SPS", "DATA", dimension="rate"),
             ChannelSpec("lost_frames", "seq gaps", "", "DATA", dimension="count")]
    for n in (*pg.DATA_FLAGS_BITS, *pg.DATA_STATUS_BITS):
        if n:
            specs.append(ChannelSpec(f"bit.{n.lower()}", n, "", "Status bits", dimension="bits"))
    specs.append(ChannelSpec("F_N", "force", "N", "Force", False, "needs load calibration + tare", dimension="force"))
    return specs


class FakeData:
    def __init__(self, owner: "FakeBackend") -> None:
        self._owner = owner
        self.vstate: dict[str, np.ndarray] = {}
        self.latest_values: dict[str, tuple[float, str]] = {"raw": (123456.0, "OK"), "rate_sps": (80.0, "OK")}
        self.snapshot_calls = 0
        self.snapshot_args: list[tuple[tuple[str, ...], float, int]] = []
        self.xy_calls: list[tuple[str, str, float | None]] = []
        self.fail: BaseException | None = None

    def snapshot(self, keys: Sequence[str], window_s: float, px_width: int) -> PlotSnapshot:
        self.snapshot_calls += 1
        self.snapshot_args.append((tuple(keys), float(window_s), int(px_width)))
        if self.fail is not None:
            raise self.fail
        t = np.linspace(-window_s, 0.0, int(px_width))
        series = {}
        for k in keys:
            if k.startswith("bit."):
                v = (np.sin(t) > 0).astype(np.float32)
                lo, hi = v, v
            else:
                base = (np.sin(t / 3.0) * 100.0).astype(np.float32)
                lo, hi = base - 1.0, base + 1.0
            vs = self.vstate.get(k, np.zeros(int(px_width), dtype=np.uint8))
            series[k] = SeriesMinMax(lo.astype(np.float32), hi.astype(np.float32), vs)
        return PlotSnapshot(12.5, t, series, 0)

    def xy(self, x_key: str, y_key: str, window_s: float | None = None, max_points: int = 4000, *,
           since: Literal["window", "record", "sequence"] = "window") -> XYSnapshot:
        self.xy_calls.append((x_key, y_key, window_s))
        x = np.linspace(0.0, 10.0, 200)
        return XYSnapshot(x, 50.0 * x + 3.0, np.zeros(200, dtype=np.uint8), 12.5)

    def latest(self, key: str) -> LatestSample:
        if key not in self.latest_values:
            raise KeyError(key)
        v, st = self.latest_values[key]
        return LatestSample(key, v, st, 12.5)

    def sequence_trace(self, max_points: int = 20000) -> XYSnapshot:
        e = np.zeros(0)
        return XYSnapshot(e, e, np.zeros(0, dtype=np.uint8), 0.0)


class FakeChannels:
    def __init__(self) -> None:
        self.specs = default_channels()

    def channels(self) -> Sequence[ChannelSpec]:
        return list(self.specs)

    def get(self, key: str) -> ChannelSpec:
        return next(s for s in self.specs if s.key == key)


# --------------------------------------------------------------------------------------------- M2–M4 stubs

class FakeMotion:
    def __init__(self, owner: "FakeBackend") -> None:
        self._o = owner

    def move_to(self, target_mm: float, *, speed_mm_s: float | None = None,
                accel_mm_s2: float | None = None) -> Future:
        self._o._rec("motion.move_to", target_mm)
        return done(MoveOutcome("REFUSED", None, "not implemented"))

    def move_by(self, delta_mm: float, *, speed_mm_s: float | None = None,
                accel_mm_s2: float | None = None) -> Future:
        self._o._rec("motion.move_by", delta_mm)
        return done(MoveOutcome("REFUSED", None, "not implemented"))

    def jog_start(self, direction: int, speed_mm_s: float) -> None:
        self._o._rec("motion.jog_start", direction, speed_mm_s)

    def jog_update(self, speed_mm_s: float) -> None:
        self._o._rec("motion.jog_update", speed_mm_s)

    def jog_stop(self) -> None:
        self._o._rec("motion.jog_stop")

    def enable(self) -> GateResult:
        self._o._rec("motion.enable")
        return NOT_IMPL

    def disable(self, *, confirmed: bool = False) -> GateResult:
        self._o._rec("motion.disable", confirmed=confirmed)
        return NOT_IMPL

    def home(self, *, load_confirmed: bool = False) -> Future:
        self._o._rec("motion.home", load_confirmed=load_confirmed)
        return done(MoveOutcome("REFUSED", None, "not implemented"))

    def set_test_zero(self) -> float:
        self._o._rec("motion.set_test_zero")
        return 0.0

    def reset_test_zero(self) -> None:
        self._o._rec("motion.reset_test_zero")

    def set_valid(self, flag: bool) -> GateResult:
        self._o._rec("motion.set_valid", flag)
        return NOT_IMPL

    def limits(self) -> MotionLimits | None:
        return None

    def check(self, kind: MotionKind, *, speed_mm_s: float | None = None, accel_mm_s2: float | None = None,
              target_mm: float | None = None) -> GateResult:
        return NOT_IMPL


class FakeLimits:
    def __init__(self, owner: "FakeBackend") -> None:
        self._o = owner

    def get(self) -> LimitConfig:
        return LimitConfig()

    def set(self, cfg: LimitConfig) -> list[Issue]:
        self._o._rec("limits.set", cfg)
        return []

    def thresholds(self) -> ThresholdState:
        return ThresholdState()

    def recheck_async(self) -> Future:
        return done(ThresholdState())

    def set_no_specimen_mode(self, on: bool, *, confirmed: bool = False) -> GateResult:
        self._o._rec("limits.set_no_specimen_mode", on, confirmed=confirmed)
        st = self._o.status()
        self._o.set_status(safety=dataclasses.replace(st.safety, no_specimen_mode=bool(on)))
        return GATE_OK


class FakeMarks:
    def __init__(self) -> None:
        self._m = TestMarks()

    def get(self) -> TestMarks:
        return self._m

    def set(self, marks: TestMarks) -> None:
        self._m = marks

    def list_presets(self) -> list[str]:
        return []

    def save_preset(self, path: str) -> None:
        pass

    def load_preset(self, path: str) -> TestMarks:
        return self._m


class FakeSession:
    def __init__(self) -> None:
        self._s = SessionSettings()

    def get(self) -> SessionSettings:
        return self._s

    def set(self, settings: SessionSettings) -> list[Issue]:
        self._s = settings
        return []

    def load(self, path: str) -> SessionSettings:
        return self._s

    def save(self, path: str) -> None:
        pass


class FakeEngine:
    PHASES: tuple[str, ...] = ("IDLE",)

    def __init__(self, kind: str) -> None:
        self._kind = kind

    def state(self) -> EngineState:
        return EngineState(self._kind, "IDLE")

    def subscribe(self, cb: Callable[[EngineState], None]) -> int:
        return 0

    def start(self, **config: Any) -> GateResult:
        return NOT_IMPL

    def continue_(self, inputs: Mapping[str, float] | None = None, *, confirmed: bool = False) -> None:
        pass

    def repeat(self) -> None:
        pass

    def cancel(self) -> None:
        pass


class FakeTareEngine(FakeEngine):
    def undo(self) -> GateResult:
        return NOT_IMPL


class FakeLoadCal(FakeEngine):
    def finish_early(self) -> None:
        pass

    def retake(self, i: int) -> None:
        pass


class FakeCalStore:
    def __init__(self, owner: "FakeBackend") -> None:
        self._o = owner

    def active_load(self) -> Mapping[str, Any] | None:
        return None

    def active_travel(self) -> Mapping[str, Any] | None:
        return None

    def history(self, kind: Literal["load", "travel"]) -> list[str]:
        return []

    def restore_travel_async(self) -> Future:
        return done(TravelDiffState())

    def resolve_travel_difference_async(
            self, action: Literal["restore", "keep_board", "ignore_session"]) -> Future:
        self._o._rec("calibrations.resolve_travel_difference_async", action)
        return done(TravelDiffState())


class FakeSequencer:
    def new(self) -> Any:
        return None

    def load(self, path: str) -> Any:
        return None

    def save(self, seq: Any, path: str) -> None:
        pass

    def validate(self, seq: Any) -> list[Issue]:
        return []

    def expand(self, seq: Any) -> Any:
        return None

    def planned_path(self, seq: Any) -> Any:
        return None

    def generator_schemas(self) -> Mapping[str, Any]:
        return {}

    def start(self, seq: Any, *, confirmed: bool = False) -> GateResult:
        return NOT_IMPL

    def pause(self) -> StopResult:
        return StopResult("PAUSE", "sequence", False, reason="not implemented")

    def resume(self) -> GateResult:
        return NOT_IMPL

    def stop(self) -> StopResult:
        return StopResult("STOP", "sequence", False, reason="not implemented")

    def abort(self) -> StopResult:
        return StopResult("HALT", "sequence", False, reason="not implemented")

    def status(self) -> SeqStatus:
        return SeqStatus()


class FakeReports:
    def build_async(self, rec_dir: str, cal: Any = None, tare: Any = None, bend3p: Any = None) -> Future:
        return failed(NotImplementedError("M4"))

    def list_recordings(self, root: str | None = None) -> list[str]:
        return []

    def load_result(self, rec_dir: str) -> Any:
        return None


# --------------------------------------------------------------------------------------------- backend

class FakeBackend:
    """See module docstring. ``connected`` decides whether STOP/HALT/PAUSE results are ``sent``."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.beats = 0
        self.started = False
        self.shutdowns = 0
        self.events = FakeEventBus()
        self._status = BackendStatus()
        self.config = FakeConfig(self)
        self.motion = FakeMotion(self)
        self.limits = FakeLimits(self)
        self.data = FakeData(self)
        self.channels = FakeChannels()
        self.marks = FakeMarks()
        self.session = FakeSession()
        self.tare_engine = FakeTareEngine("tare")
        self.travel_cal = FakeEngine("travel_cal")
        self.load_cal = FakeLoadCal("load_cal")
        self.calibrations = FakeCalStore(self)
        self.sequencer = FakeSequencer()
        self.reports = FakeReports()
        self.sim = None
        self.test_hooks = None
        self.compat = Compat.OK
        self.connect_error: BaseException | None = None
        self.connect_pending = False
        self.results: dict[str, Any] = {}           # name -> GateResult / ClearResult to return
        self.info = DeviceInfo(1, 0, 1, (0, 1, 0), params_gen.PARAM_DICT_HASH, "0039" + "00" * 10, "3f2a1c",
                               params_gen.PARAM_COUNT, int(pg.Features.AFE_SYNTHETIC | pg.Features.NVM
                                                           | pg.Features.TWIN))

    # ---------------------------------------------------------------- recording / scripting
    def _rec(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(Call(name, args, kwargs, time.monotonic_ns()))

    def call_names(self) -> list[str]:
        return [c.name for c in self.calls]

    def calls_of(self, name: str) -> list[Call]:
        return [c for c in self.calls if c.name == name]

    def set_status(self, **changes: Any) -> BackendStatus:
        self._status = dataclasses.replace(self._status, seq=self._status.seq + 1, **changes)
        return self._status

    def set_indicators(self, **items: Indicator) -> None:
        self.set_status(indicators=self._status.indicators.replace(**items))

    def set_all_indicators(self, state: str) -> None:
        from bend_stand.core.api import INDICATOR_KEYS  # noqa: PLC0415
        self.set_status(indicators=self._status.indicators.replace(**{k: Indicator(state) for k in INDICATOR_KEYS}))

    def set_gate(self, gid: GateId, gate: GateResult | None) -> None:
        gates = dict(self._status.gates)
        if gate is None:
            gates.pop(gid, None)
        else:
            gates[gid] = gate
        self.set_status(gates=MappingProxyType(gates))

    @property
    def connected(self) -> bool:
        return self._status.link.state in (LinkState.CONNECTED, LinkState.DEGRADED)

    # ---------------------------------------------------------------- facade
    def start(self) -> None:
        self._rec("start")
        self.started = True
        self.set_status(hotkey=HotkeyStatus("REGISTERED", "RegisterHotKey ok"))

    def shutdown(self) -> None:
        self._rec("shutdown")
        self.shutdowns += 1

    def gui_beat(self) -> None:
        self.beats += 1

    def endpoints(self) -> list[EndpointInfo]:
        return [EndpointInfo("sim", "Simulator (in-process)", "sim"),
                EndpointInfo("tcp://127.0.0.1:5760", "FW host twin", "tcp")]

    def connect_async(self, endpoint: str) -> Future:
        self._rec("connect_async", endpoint)
        if self.connect_error is not None:
            return failed(self.connect_error)
        if self.connect_pending:
            self.set_status(link=LinkStatus(LinkState.CONNECTING, "GET_INFO", endpoint))
            return Future()
        self.set_status(link=LinkStatus(LinkState.CONNECTED, "", endpoint, self.compat, self.info,
                                        LinkStats(frames_ok=10, data_frames=8)),
                        cfg_dirty=False, reboot_pending=False, nvm_defaulted=False,
                        config_read_only=self.compat.config_read_only)
        self.events.emit("link.state", None)
        return done(self.info)

    def disconnect_async(self) -> Future:
        self._rec("disconnect_async")
        self.set_status(link=LinkStatus(), stream=StreamStatus())
        return done(None)

    def _priority(self, cmd: str, source: str) -> StopResult:
        self._rec(cmd.lower(), source)
        r = StopResult(cmd, source, self.connected, time.monotonic_ns() if self.connected else None,
                       reason="" if self.connected else "not connected")
        self.events.emit("stop.issued", r)
        return r

    def stop(self, source: str = "gui") -> StopResult:
        return self._priority("STOP", source)

    def halt(self, source: str = "gui") -> StopResult:
        return self._priority("HALT", source)

    def pause(self, source: str = "gui") -> StopResult:
        return self._priority("PAUSE", source)

    def resume(self, source: str = "gui") -> GateResult:
        self._rec("resume", source)
        return self.results.get("resume", GATE_OK)

    def tare(self, window_s: float | None = None) -> GateResult:
        self._rec("tare", window_s)
        return self.results.get("tare", NOT_IMPL)

    def take_sample(self, window_s: float = 1.0) -> GateResult:
        self._rec("take_sample", window_s)
        return self.results.get("take_sample", NOT_IMPL)

    def record_start(self) -> GateResult:
        self._rec("record_start")
        return self.results.get("record_start", NOT_IMPL)

    def record_stop(self) -> GateResult:
        self._rec("record_stop")
        return self.results.get("record_stop", NOT_IMPL)

    def hotkey_test_start(self, timeout_s: float = 10.0) -> GateResult:
        self._rec("hotkey_test_start", timeout_s)
        return NOT_IMPL

    def clear_stop_async(self, *, confirmed: bool = False) -> Future:
        self._rec("clear_stop_async", confirmed=confirmed)
        return done(self.results.get("clear_stop", ClearResult("HALT_CLEAR", True, True, "OK", ("HALT",))))

    def estop_clear_async(self, *, confirmed: bool) -> Future:
        self._rec("estop_clear_async", confirmed=confirmed)
        return done(self.results.get("estop_clear", ClearResult("ESTOP_CLEAR", True, True, "OK", ("ESTOP",))))

    def fault_clear_async(self) -> Future:
        self._rec("fault_clear_async")
        return done(self.results.get("fault_clear", ClearResult("FAULT_CLEAR", True, True, "OK", ())))

    def stream_start_async(self) -> Future:
        self._rec("stream_start_async")
        self.set_status(stream=StreamStatus(True, 80.0, 80.0))
        return done(None)

    def stream_stop_async(self) -> Future:
        self._rec("stream_stop_async")
        self.set_status(stream=StreamStatus(False))
        return done(None)

    def status(self) -> BackendStatus:
        return self._status
