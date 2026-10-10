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
    GATE_OK, BackendStatus, BoardConfigFile, ChannelSpec, ClearResult, Compat, ConfirmationRequired, DeviceInfo,
    EndpointInfo, EngineState, EventRecord, FileFormatError, GateId, GateItem, GateRefused, GateResult, HotkeyStatus,
    Indicator, Issue, IssueSeverity, LatestSample, LimitConfig, LinkState, LinkStats, LinkStatus, MotionKind,
    MotionLimits, MotionStatus, MoveDone, MoveOutcome, PlotSnapshot, SeqStatus, SeriesMinMax, SessionSettings,
    Severity, StopResult, StreamStatus, TestMarks, ThresholdState, TravelDiffState, VerifyReport, WriteItem,
    WriteStatus, XYSnapshot,
)

from fake_sequencer import FakeReports, FakeSequencer  # noqa: E402  M4 (B §15.5f, B's pure sequencer code)

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
    specs.append(ChannelSpec("F_kgf", "force", "kgf", "Force", False, "needs load calibration + tare",
                             dimension="force"))
    specs.append(ChannelSpec("x_mm", "travel (machine)", "mm", "Travel", dimension="length"))
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


# --------------------------------------------------------------------------------------------- M2–M3 fakes

def default_limits() -> MotionLimits:
    return MotionLimits(30.0, 20.0, 50.0, 2.0, 100.0, False, 30.0, 30.0, 0.5, 290.0)   # dict 6: 40 kHz / 800 st/mm


class FakeMotion:
    """Records every call; ``check`` mimics the caps / range of ``status().motion.limits`` (B §5.4) so the GUI
    field colouring can be tested; ``results`` scripts GateResults, ``tickets`` scripts MoveTicket futures."""

    def __init__(self, owner: "FakeBackend") -> None:
        self._o = owner
        self.results: dict[str, Any] = {}           # name -> GateResult (enable / disable / set_valid)
        self.tickets: dict[str, Future] = {}         # name -> Future to return once (move_to / move_by / home)
        self.check_calls: list[tuple] = []
        self.x_zero = 0.0

    def _ticket(self, name: str, target: float | None) -> Future:
        f = self.tickets.pop(name, None)
        if f is not None:
            return f
        if name == "home":
            gate = self._o.status().gates.get(GateId.HOME)
            if gate is not None and gate.refused:
                return failed(GateRefused(gate))
        else:
            gate = self._o.status().gates.get(GateId.MOVE)
            if gate is not None and gate.refused:
                return failed(GateRefused(gate))
        return done(MoveOutcome("DONE", MoveDone("TARGET", target or 0.0, 0, 1_000_000)))

    def move_to(self, target_mm: float, *, speed_mm_s: float | None = None,
                accel_mm_s2: float | None = None) -> Future:
        self._o._rec("motion.move_to", target_mm, speed_mm_s=speed_mm_s, accel_mm_s2=accel_mm_s2)
        return self._ticket("move_to", target_mm)

    def move_by(self, delta_mm: float, *, speed_mm_s: float | None = None,
                accel_mm_s2: float | None = None) -> Future:
        self._o._rec("motion.move_by", delta_mm, speed_mm_s=speed_mm_s, accel_mm_s2=accel_mm_s2)
        return self._ticket("move_by", delta_mm)

    def jog_start(self, direction: int, speed_mm_s: float) -> None:
        self._o._rec("motion.jog_start", direction, speed_mm_s)

    def jog_update(self, speed_mm_s: float) -> None:
        self._o._rec("motion.jog_update", speed_mm_s)

    def jog_stop(self) -> None:
        self._o._rec("motion.jog_stop")

    def enable(self) -> GateResult:
        self._o._rec("motion.enable")
        return self.results.get("enable", GATE_OK)

    def disable(self, *, confirmed: bool = False) -> GateResult:
        self._o._rec("motion.disable", confirmed=confirmed)
        if "disable" in self.results:
            return self.results["disable"]
        gate = self._o.status().gates.get(GateId.DISABLE, GATE_OK)
        if gate.confirm_items and not confirmed:
            return GateResult(gate.items + (GateItem("CONFIRMATION_REQUIRED", Severity.REFUSE,
                                                     "confirm: " + gate.confirm_items[0].text),))
        return gate

    def home(self, *, load_confirmed: bool = False) -> Future:
        self._o._rec("motion.home", load_confirmed=load_confirmed)
        gate = self._o.status().gates.get(GateId.HOME, GATE_OK)
        if gate.ok and gate.confirm_items and not load_confirmed and "home" not in self.tickets:
            return failed(ConfirmationRequired(gate))
        return self._ticket("home", 0.0)

    def set_test_zero(self) -> float:
        self._o._rec("motion.set_test_zero")
        gate = self._o.status().gates.get(GateId.TEST_ZERO, GATE_OK)
        if gate.refused:
            raise GateRefused(gate)
        self.x_zero = self._o.status().motion.position_mm or 0.0
        return self.x_zero

    def reset_test_zero(self) -> None:
        self._o._rec("motion.reset_test_zero")
        self.x_zero = 0.0

    def set_valid(self, flag: bool) -> GateResult:
        self._o._rec("motion.set_valid", flag)
        return self.results.get("set_valid", GATE_OK)

    def limits(self) -> MotionLimits | None:
        return self._o.status().motion.limits

    def check(self, kind: MotionKind, *, speed_mm_s: float | None = None, accel_mm_s2: float | None = None,
              target_mm: float | None = None) -> GateResult:
        self.check_calls.append((kind, speed_mm_s, accel_mm_s2, target_mm))
        gate = self._o.status().gates.get(GateId.JOG if kind == MotionKind.JOG else GateId.MOVE, GATE_OK)
        items = list(gate.items)
        lim = self.limits()
        if lim is not None:
            cap = min(lim.v_cap_mm_s, lim.v_unhomed_mm_s) if kind == MotionKind.JOG and \
                self._o.status().indicators.homed.state != "ON" else lim.v_cap_mm_s
            if speed_mm_s is not None and speed_mm_s > cap + 1e-9:
                items.append(GateItem("SPEED_CAP", Severity.REFUSE, f"speed {speed_mm_s:g} mm/s above the cap "
                                      f"{cap:g} mm/s"))
            if accel_mm_s2 is not None and accel_mm_s2 > lim.a_max_mm_s2 + 1e-9:
                items.append(GateItem("ACCEL_CAP", Severity.REFUSE, f"acceleration {accel_mm_s2:g} mm/s² above "
                                      f"{lim.a_max_mm_s2:g}"))
            if target_mm is not None and lim.travel_min_mm is not None and lim.travel_max_mm is not None and \
                    not lim.travel_min_mm <= target_mm <= lim.travel_max_mm:
                items.append(GateItem("TARGET_OUT_OF_RANGE", Severity.REFUSE, f"target {target_mm:g} mm outside "
                                      f"the travel range"))
        for extra in self.results.get("check_extra", ()):
            items.append(extra)
        return GateResult(tuple(items))


class FakeLimits:
    def __init__(self, owner: "FakeBackend") -> None:
        self._o = owner
        self.cfg = LimitConfig()
        self.next_issues: list[Issue] | None = None
        self.no_specimen_result: GateResult = GATE_OK

    def get(self) -> LimitConfig:
        return self.cfg

    def set(self, cfg: LimitConfig) -> list[Issue]:
        self._o._rec("limits.set", cfg)
        issues, self.next_issues = (self.next_issues or []), None
        if not [i for i in issues if i.severity == IssueSeverity.ERROR]:
            self.cfg = cfg
        return issues

    def check(self, cfg: LimitConfig) -> list[Issue]:
        """B5-21: the same issues as ``set`` without applying (scripted via ``next_issues``, not consumed)."""
        self._o._rec("limits.check", cfg)
        return list(self.next_issues or [])

    def thresholds(self) -> ThresholdState:
        return self._o.status().safety.thresholds

    def recheck_async(self) -> Future:
        self._o._rec("limits.recheck_async")
        return done(self.thresholds())

    # B4-04 extras (not in the Protocol; the GUI uses them when present)
    def set_manual_thresholds_async(self, raw_min: int, raw_max: int, zero_raw: int = 0) -> Future:
        self._o._rec("limits.set_manual_thresholds_async", raw_min, raw_max, zero_raw)
        st = ThresholdState("VERIFIED", "manual-raw", None, None, raw_min, raw_max, zero_raw)
        self._o.set_status(safety=dataclasses.replace(self._o.status().safety, thresholds=st))
        return done(st)

    def set_default_thresholds_async(self) -> Future:
        self._o._rec("limits.set_default_thresholds_async")
        st = ThresholdState("DEFAULT_ONLY", None, None, None, -7_022_271, 7_022_271, 0)
        self._o.set_status(safety=dataclasses.replace(self._o.status().safety, thresholds=st))
        return done(st)

    def set_no_specimen_mode(self, on: bool, *, confirmed: bool = False) -> GateResult:
        self._o._rec("limits.set_no_specimen_mode", on, confirmed=confirmed)
        if not self.no_specimen_result.ok:
            return self.no_specimen_result
        st = self._o.status()
        self._o.set_status(safety=dataclasses.replace(st.safety, no_specimen_mode=bool(on)))
        return GATE_OK


class FakeMarks:
    def __init__(self, owner: "FakeBackend | None" = None) -> None:
        self._o = owner
        self._m = TestMarks()
        self.presets: dict[str, TestMarks] = {}

    def get(self) -> TestMarks:
        return self._m

    def set(self, marks: TestMarks) -> None:
        if self._o is not None:
            self._o._rec("marks.set", marks)
        self._m = marks

    def list_presets(self) -> list[str]:
        return sorted(self.presets)

    def save_preset(self, path: str) -> None:
        if self._o is not None:
            self._o._rec("marks.save_preset", path)
        self.presets[path] = self._m

    def delete_preset(self, path: str) -> None:
        """B5-22 (GRQ-B-27): only ``*.bbmarks.json``; unknown path → FileFormatError."""
        if self._o is not None:
            self._o._rec("marks.delete_preset", path)
        if not path.endswith(".bbmarks.json") or path not in self.presets:
            raise FileFormatError(f"not a mark preset: {path}")
        del self.presets[path]

    def load_preset(self, path: str) -> TestMarks:
        if self._o is not None:
            self._o._rec("marks.load_preset", path)
        if path not in self.presets:
            raise FileFormatError(f"no preset {path}")
        return self.presets[path]


class FakeSession:
    def __init__(self) -> None:
        self._s = SessionSettings()
        self.load_issues: list[Issue] = []         # B6-33 (2): issues of the last load (LOAD_LIMITS_RESTORED, FILE)

    def get(self) -> SessionSettings:
        return self._s

    def set(self, settings: SessionSettings) -> list[Issue]:
        self._s = settings
        return []

    def load(self, path: str) -> SessionSettings:
        return self._s

    def save(self, path: str) -> None:
        pass


ENGINE_PHASES = {
    "travel_cal": ("CHECK", "HOME", "BACKLASH", "REFERENCE", "MOVE1", "ENTER_D1", "MOVE2", "ENTER_DTOT", "RESULT", "ACCEPT",
                   "DONE"),
    "load_cal": ("CONFIG", "AWAIT_OPERATOR", "PRESETTLE", "CAPTURE", "EVALUATE", "FIT", "ACCEPT", "DONE"),
    "tare": ("CHECK", "CAPTURE", "EVALUATE", "DONE"),
}
ENGINE_TOPIC = {"travel_cal": "cal.travel.state", "load_cal": "cal.load.state", "tare": "tare.state"}


class FakeEngine:
    """Scriptable engine (B §9.1): ``set_state(**fields)`` replaces the ``EngineState`` and publishes the engine
    topic; every method call is recorded as ``<kind>.<method>``; ``start_result`` scripts the start gate."""

    PHASES: tuple[str, ...] = ("IDLE",)

    def __init__(self, kind: str, owner: "FakeBackend | None" = None) -> None:
        self._kind = kind
        self._o = owner
        self.PHASES = ENGINE_PHASES.get(kind, ("IDLE",))
        self._state = EngineState(kind, "IDLE")
        self.start_result: GateResult | None = None
        self._subs: list[Callable[[EngineState], None]] = []

    def _rec(self, name: str, *args: Any, **kwargs: Any) -> None:
        if self._o is not None:
            self._o._rec(f"{self._kind}.{name}", *args, **kwargs)

    def set_state(self, **fields: Any) -> EngineState:
        self._state = dataclasses.replace(self._state, **fields)
        for cb in list(self._subs):
            cb(self._state)
        if self._o is not None:
            self._o.events.emit(ENGINE_TOPIC.get(self._kind, f"{self._kind}.state"), self._state)
        return self._state

    def state(self) -> EngineState:
        return self._state

    def subscribe(self, cb: Callable[[EngineState], None]) -> int:
        self._subs.append(cb)
        return len(self._subs)

    def start(self, **config: Any) -> GateResult:
        self._rec("start", **config)
        if self.start_result is not None:
            g = self.start_result
            if g.ok and (not g.confirm_items or config.get("confirmed")):
                self.set_state(phase=self.PHASES[0], abort_reason=None)
            return g
        return NOT_IMPL

    def continue_(self, inputs: Mapping[str, float] | None = None, *, confirmed: bool = False) -> None:
        self._rec("continue_", dict(inputs) if inputs else None, confirmed=confirmed)

    def repeat(self) -> None:
        self._rec("repeat")

    def cancel(self) -> None:
        self._rec("cancel")


class FakeTareEngine(FakeEngine):
    def undo(self) -> GateResult:
        self._rec("undo")
        return self._o.results.get("tare_undo", GATE_OK) if self._o is not None else GATE_OK


class FakeLoadCal(FakeEngine):
    def finish_early(self) -> None:
        self._rec("finish_early")

    def retake(self, i: int) -> None:
        self._rec("retake", i)


class FakeCalStore:
    def __init__(self, owner: "FakeBackend") -> None:
        self._o = owner
        self.load_record: Mapping[str, Any] | None = None
        self.files: dict[str, list[str]] = {"load": [], "travel": []}

    def active_load(self) -> Mapping[str, Any] | None:
        return self.load_record

    def active_travel(self) -> Mapping[str, Any] | None:
        return None

    def history(self, kind: Literal["load", "travel"]) -> list[str]:
        return list(self.files.get(kind, []))

    def restore_travel_async(self) -> Future:
        return done(TravelDiffState())

    def resolve_travel_difference_async(
            self, action: Literal["restore", "keep_board", "ignore_session"]) -> Future:
        self._o._rec("calibrations.resolve_travel_difference_async", action)
        return done(TravelDiffState())


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
        self.marks = FakeMarks(self)
        self.session = FakeSession()
        self.tare_engine = FakeTareEngine("tare", self)
        self.travel_cal = FakeEngine("travel_cal", self)
        self.load_cal = FakeLoadCal("load_cal", self)
        self.calibrations = FakeCalStore(self)
        self.sequencer = FakeSequencer(self)
        self.reports = FakeReports(self)
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

    def set_motion(self, **fields: Any) -> None:
        self.set_status(motion=dataclasses.replace(self._status.motion, **fields))

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
                        config_read_only=self.compat.config_read_only,
                        motion=MotionStatus(position_mm=100.0, test_position_mm=100.0, commanded_target_mm=100.0,
                                            limits=default_limits()))
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
        return self.results.get("hotkey_test_start", GATE_OK)

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
