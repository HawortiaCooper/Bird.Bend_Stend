"""SimBoard — the in-process FW simulator (SW_design §12): protocol endpoint, command dispatch, FW state machine,
NVM, DATA/EVENT streaming, world-control hooks. Uses the **same** codec (``io.framing``/``io.protocol``, board
direction) and the generated dictionary; command acceptance is the pure ``sim.check.check`` (a NACK has no side
effect by construction — the state is only changed after an OK verdict).

Time: virtual board time from the ``Clock`` (``t_us`` = µs since boot + ``t0_us``, 32-bit on the wire).
``step(now_ns)`` runs, in time order, the 1 ms FW tick and the HX711 conversions (the axis is advanced step by step
to each of them), then processes RX and flushes TX in the FW wire order DATA > responses > EVENT (ICD §2.4).
On the real clock ``start()`` runs it in a 1 ms thread; on the lockstep clock the backend calls ``step``.

Behaviour = ICD v0.5 §4–§9 + FW_design §5 (M2, WP-B12):
- **Motion** with exact step counting: the step counter is the position (``steps``), every period comes from the
  exact square-root ramp of R4 §1.5 (``calc.motion.Ramp``, 90 MHz timer ticks, fractional carry, virtual index for
  on-the-fly jog changes, controlled stops with ``motion.a_stop_um_s2``, clean-halt substitution of §6.5);
  immediate stops are CLEAN (a pulse in flight completes and is counted) except the E-stop (TRUNCATE → a pulse in
  flight is cut, ``POS_UNCERTAIN``). EVENT order STOPPED → VALID_CLEARED → … → MOVE_DONE (FW_design §5.3).
- **Homing** at START only: RELEASE → FAST_SEEK → BACKOFF → SLOW_APPROACH (edge capture per step) → zero set →
  MOVE_TO_ZERO; HOME_NOT_FOUND / HOME_WIRING / ABORTED; HOME_DRIFT on re-homing (signed drift in HOMED).
- **Inputs**: E-stop (act on the edge, closed ≥ ``io.estop_release_ms`` for the clear), limit switches by world
  position or forced (first edge acts, release debounced ``io.release_ms``, latch auto-clear, LIMIT_WIRING),
  PAUSE button (contact type ``io.pause_active_level``), ALM (active on the first sample, inactive after a stable
  ``io.release_ms``), PEND / NOT_SETTLED, DRV_PWR with the 20 ms filter (SAF-FW-024), K1_WELDED timer
  (SAF-FW-025), boot ENA rule (§6.4).
- **Safety**: FW load limit on every sample incl. rails and the regrow rule after FAULT_CLEAR (SAF-FW-011), AFE
  stale → AFE_FAULT while moving, link watchdog (controlled stop), jog dead-man, idle disable (unloaded, AFE
  fresh, ``idle_disable_left_s`` in STATUS), ALM start-block (in ``check``).
- **Feature mask** (D-37 b): bits of absent features are sent as 0 (DRV_SIGNALS: ALM / PEND / DRV_PWR; BUTTONS:
  PAUSE_BTN) and those inputs are not sampled; commands of absent features answer ``E_INTERNAL`` NOT_IN_BUILD
  after a passed check, like A's M1 FW.
- D-36 / CR-01: no STOP/BREAK button, HALT source PC only.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/simulator.py @37c87471 (architecture: loopback endpoint,
per-command dispatch with NACK first, fault injector, NVM as JSON; behaviour rewritten for the bend stand).

Implements: SYS-008, IF-007, IF-010, FW-CMD-001 (sim), FW-CMD-002 (sim), FW-STR-001…006 (sim), FW-MOT-001…009
(sim), FW-HOM-001/002/004 (sim), FW-SW-001/003/004/005 (sim), SAF-FW-001…026 (sim, without SAF-FW-022), D-07
"""
from __future__ import annotations

import collections
import logging
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bend_stand.calc.motion import F_TICK_HZ, Ramp, ctrl_stop_path, steps_to_um, um_to_steps, v_limit_um_s
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import Clock
from bend_stand.core.model import INT_DF, INT_DS, INT_FA, INT_FE, INT_IO, BoardStatus, DeviceInfo
from bend_stand.io import protocol as P
from bend_stand.io.framing import Frame, FrameDecoder, encode_frame
from bend_stand.io.sim.check import MOVING, SimCheckState, check
from bend_stand.io.sim.loadlim import LoadLimit
from bend_stand.io.sim.models import Hx711Model, World
from bend_stand.io.sim.nvm import NvmStore
from bend_stand.io.transport import Transport

log = logging.getLogger("bend_stand.sim")
Cmd = pg.Cmd
DF, DS, FA = INT_DF, INT_DS, INT_FA        # plain ints (fast bit tests)
SC = pg.StopCause
EV = pg.Event
FE = INT_FE
DEFAULT_FEATURES = int(FE.AFE | FE.MOTION | FE.HOMING | FE.MOVE_UNTIL_LOAD | FE.NVM | FE.BUTTONS | FE.DRV_SIGNALS)
SIM_UID = "53494D0000000000B1BDB0A0"
EVENT_QUEUE = 32
DRV_PWR_FILTER_MS = 20                         # FW-SW-005 stability filter, both directions
TICKS_PER_US = F_TICK_HZ / 1e6
#: commands that answer E_INTERNAL NOT_IN_BUILD after a passed check while their feature bit is 0 (A's M1 FW)
FEATURE_OF_CMD: dict[int, int] = {
    int(Cmd.ENABLE): int(FE.MOTION), int(Cmd.DISABLE): int(FE.MOTION), int(Cmd.MOVE_ABS): int(FE.MOTION),
    int(Cmd.JOG): int(FE.MOTION), int(Cmd.HOME): int(FE.HOMING), int(Cmd.MOVE_UNTIL_LOAD): int(FE.MOVE_UNTIL_LOAD),
}


@dataclass
class Move:
    """One running motion leg; ``ramp`` gives the step periods, ``t_next_us`` is the completion time of the
    pending step (already timed)."""

    kind: str                      # MOVE_ABS | JOG | MOVE_UNTIL_LOAD | HOMING
    direction: int
    end_steps: int
    end_reason: str
    v_um_s: float
    a_um_s2: float
    ramp: Ramp
    t_next_us: float
    raw_stop: int = 0
    cmp: int = 0
    phase: str = ""                # homing phase name (HomePhase)
    released: bool = False         # RELEASE / BACKOFF: START released (stable) and the back-off retargeted
    last_refresh_us: int = 0
    stopping: bool = False
    stop_cause: int | None = None
    stop_reason: str = "STOPPED"
    reverse: tuple[int, int, float, float, str] | None = None   # jog reversal after the stop
    start_steps: int = 0


@dataclass
class _Delayed:
    t_us: int
    fn: Callable[[], None]


@dataclass
class _Fault:
    kind: str                      # drop_next | duplicate_next | delay_next | corrupt_next | nack
    cmd: int | None
    what: str = "response"
    n: int = 1
    ms: int = 0
    status: int = 0
    detail: int = 0


@dataclass
class _Debounce:
    """First edge acts; the release counts after a stable inactive level of ``release_ms`` (FW-SW-001)."""

    level: bool = False
    armed: bool = True
    rel_since: int | None = None


@dataclass
class SimConfig:
    features: int = DEFAULT_FEATURES
    fw_version: tuple[int, int, int] = (0, 2, 0)
    t0_us: int = 1_000_000
    reset_cause: int = int(pg.ResetCause.POWER_ON)
    true_offset_um: int = 100_000          # world position of the carriage at power-up (machine 0, not homed)
    seed: int = 1
    nvm_path: str | None = None


class SimBoard:
    """Board end of a transport; see module doc."""

    def __init__(self, clock: Clock, transport: Transport, *, config: SimConfig | None = None,
                 world: World | None = None, afe: Hx711Model | None = None, nvm: NvmStore | None = None) -> None:
        self.clock = clock
        self.tx = transport
        self.cfg = config or SimConfig()
        self.world = world or World()
        self.afe = afe or Hx711Model(seed=self.cfg.seed)
        self.nvm = nvm or NvmStore(self.cfg.nvm_path)
        self.decoder = FrameDecoder()
        self._lock = threading.RLock()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self.sent_log: collections.deque[dict[str, Any]] = collections.deque(maxlen=200_000)
        self.wire_log: collections.deque[dict[str, Any]] = collections.deque(maxlen=200_000)
        self.faults: list[_Fault] = []
        self.store_mismatch: dict[str, Any] = {}
        self.on_frame_actions: list[dict[str, Any]] = []
        self.on_event_actions: list[dict[str, Any]] = []
        self.delayed: list[_Delayed] = []
        self.status_override: tuple[int, int, int] | None = None   # (set, clear, until_us)
        self.link_silence_until_us = 0
        self.tx_congestion_until_us = 0
        self.hang_until_us = 0
        self.act_hook: Callable[[dict[str, Any]], Any] | None = None   # SimControl dispatches scheduled actions
        self.pulses = 0
        self.true_offset_um = float(self.cfg.true_offset_um)
        self._pos_override: int | None = None
        self._hung = False
        self.steps = 0
        self.dir_sign = 1
        self.boot(cause=self.cfg.reset_cause, first=True)

    # ============================================================================== features
    def has(self, feature: int) -> bool:
        return bool(self.cfg.features & feature)

    @property
    def features(self) -> int:
        return self.cfg.features

    @features.setter
    def features(self, mask: int) -> None:
        self.cfg.features = int(mask)

    # ============================================================================== boot / time
    def boot(self, cause: int = int(pg.ResetCause.POWER_ON), first: bool = False) -> None:
        with self._lock:
            if not first:                                 # the carriage does not move on a reset (D-13)
                self.true_offset_um = self._x_true() - self.world.x_um_true_offset
            self.boot_ns = self.clock.monotonic_ns()
            self.t0_us = self.cfg.t0_us
            self.reset_cause = int(cause)
            self.decoder = FrameDecoder()
            img = self.nvm.boot_image()
            self.params: dict[str, Any] = dict(img.values)
            self.nvm_defaulted = img.defaulted
            self.reboot_pending = False
            self.stream_on = False
            self.valid_changes: list[tuple[int, int]] = [(0, 0)]   # (t_us32, value)
            self.valid = 0
            self.motion_state = "NOT_ENABLED"
            self.enabling_until_us = 0
            self.homed = False
            self.home_phase = int(pg.HomePhase.NONE)
            self.steps = 0
            self.motion: Move | None = None
            self.target_um = 0
            self.pos_uncertain = False
            self._done_pending: list[tuple[int, int, int]] = []
            self.estop_latched = self.world.estop_input_open()
            self.estop_closed_since_us: int | None = None if self.world.estop_input_open() else 0
            self.halt_latched = False
            self.halt_src = int(pg.Source.NONE)
            self.paused = False
            self.pause_src = int(pg.Source.NONE)
            self.faults_mask = 0
            self.forced_causes: set[str] | None = None
            self.limit_latch = {"start": False, "end": False}
            self.link_wdg = False
            self.last_cmd_us = self.now_us()
            self.afe_stale = False
            self.afe_saturated = False
            self.last_raw = pg.AFE_NO_DATA
            self.last_sample_us = self.now_us()
            self.settle_left = int(self.params["afe.settle_discard"])
            self.afe_reinit = 0
            self.afe.schedule_from(self.now_us())
            self.periods: collections.deque[int] = collections.deque(maxlen=16)
            self.rate_mismatch = False
            self.next_fallback_us = 0
            self.frame_seq = 0
            self.overrun_pending = False
            self.event_seq = 0
            self.events: collections.deque[bytes] = collections.deque()
            self.out_data: list[tuple[int, int, bytes, dict[str, Any]]] = []
            self.out_resp: list[tuple[int, int, bytes]] = []
            self.counters = collections.Counter()
            self.nvm_save_ms = 0
            self.nvm_save_uptime_ms = 0
            self.last_tick_us = self.now_us()
            self.loadlim = LoadLimit()
            self.idle_since_us: int | None = None
            self.pend_wait_since_us: int | None = None
            self.k1_since_us: int | None = None
            self._home_drift = 0
            # inputs (debounced / filtered FW view)
            xt = self._x_true()
            self.db = {"start": _Debounce(self.world.limit_start(xt)), "end": _Debounce(self.world.limit_end(xt)),
                       "pause": _Debounce(self._pause_level())}
            for d in self.db.values():
                d.armed = not d.level
            self.alm_filt = self._alm_level()
            self.alm_rel_since: int | None = None
            sense = bool(self.params["drv.pwr_sense_enable"])
            self.pwr_filt = self.world.power_present() if (sense and self.has(FE.DRV_SIGNALS)) else True
            self.pwr_change_since: int | None = None
            self.pwr_on_us = 0
            self.estop_level = self.world.estop_input_open()
            for name in ("start", "end"):
                if self.db[name].level:                   # inputs active at boot are latched (§6.4)
                    self.limit_latch[name] = True
            self.ena_disabled = self.estop_level or (sense and not self.pwr_filt)
            if img.event is not None:
                self.emit(EV.PARAMS_DEFAULTED if img.event == "PARAMS_DEFAULTED" else EV.PARAMS_LOADED, img.arg)
            self.record_seq = img.record_seq
            self.emit(EV.BOOT, self.reset_cause)

    def now_us(self) -> int:
        return (self.clock.monotonic_ns() - self.boot_ns) // 1000 + self.t0_us

    @staticmethod
    def u32(t_us: int) -> int:
        return t_us & 0xFFFFFFFF

    def uptime_ms(self) -> int:
        return (self.clock.monotonic_ns() - self.boot_ns) // 1_000_000

    def p(self, key: str) -> Any:
        return self.params[key]

    @property
    def spm(self) -> float:
        return float(self.params["motion.steps_per_mm"])

    @property
    def pos_steps(self) -> int:
        return self.steps

    @property
    def pos_um(self) -> int:
        return steps_to_um(self.steps, self.spm)

    @property
    def x_um(self) -> float:
        """Machine position in µm (float)."""
        return self.steps * 1000.0 / self.spm

    def _x_true(self) -> float:
        """World position of the carriage (µm): machine position (sign of the DIR wiring) + offset + lost steps
        (``world_shift``)."""
        return self.dir_sign * self.steps * 1000.0 / self.spm + self.true_offset_um + self.world.x_um_true_offset

    def set_dir_inverted(self, inverted: bool) -> None:
        """World DIR wiring inversion (vocabulary ``driver dir_wiring_inverted``, S variant); x stays continuous."""
        xt = self._x_true()
        self.dir_sign = -1 if inverted else 1
        self.true_offset_um += xt - self._x_true()

    # ============================================================================== threads
    def start(self) -> None:
        if self._thread is not None or self.clock.is_lockstep:
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="bend-sim", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(1.0)

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.step()
            except Exception:  # noqa: BLE001 pragma: no cover - keep the simulator alive
                log.exception("simulator step failed")
            self._stop_evt.wait(0.001)

    # ============================================================================== step
    def step(self, now_ns: int | None = None) -> None:
        with self._lock:
            now = self.now_us()
            for d in [d for d in self.delayed if d.t_us <= now]:
                self.delayed.remove(d)
                d.fn()
            if now < self.hang_until_us:
                self._drain_rx(discard=True)
                self._hung = True
                return
            if self._hung:                          # end of a hang: no overdue conversions with old stamps
                self._hung = False                  # (SWD-M1-11); the main loop resumes at "now"
                self.afe.schedule_from(now)
                self.last_tick_us = now
                m = self.motion
                if m is not None:
                    m.t_next_us = max(m.t_next_us, float(now))
            self._run_until(now)
            self._drain_rx(discard=now < self.link_silence_until_us)
            self._emit_done()
            self._flush()

    def _run_until(self, now: int) -> None:
        """1 ms ticks and HX711 conversions in time order; the axis is advanced to each of them."""
        while True:
            t_tick = self.last_tick_us + 1000
            t_afe = self.afe.next_drdy_us if self.afe.due(now) else None
            if t_tick > now and t_afe is None:
                break
            if t_afe is not None and (t_afe < t_tick or t_tick > now):
                self._advance_axis(t_afe)
                raw = self.afe.convert(self._force_n(t_afe))
                if raw is not None:
                    self._on_sample(t_afe, raw)
                else:
                    self.overrun_pending = True
            else:
                self.last_tick_us = t_tick
                self._advance_axis(t_tick)
                self._tick_1ms(t_tick)
            self._emit_done()

    def _drain_rx(self, discard: bool) -> None:
        try:
            data = self.tx.read(4096, 0.0)
        except Exception:  # noqa: BLE001 pragma: no cover - transport closed
            return
        if not data:
            return
        if discard:
            return
        for fr in self.decoder.feed(data, self.clock.monotonic_ns()):
            self._log_wire("RX", fr.raw)
            self._on_frame(fr)
            self._emit_done()

    # ============================================================================== output
    def _log_wire(self, direction: str, frame: bytes) -> None:
        t = self.now_us()
        self.wire_log.append({"dir": direction, "type": frame[2], "seq": frame[3], "first_us": t,
                              "last_us": t + len(frame) * 10_000_000 // 921_600, "hex": frame.hex().upper()})

    def _flush(self) -> None:
        """FW wire order at every frame boundary: DATA > responses > EVENT (ICD §2.4)."""
        now = self.now_us()
        congested = now < self.tx_congestion_until_us
        for ftype, seq, payload, meta in self.out_data:
            dropped = congested
            self.sent_log.append({"type": ftype, "seq": seq, "frame_seq": meta.get("frame_seq"),
                                  "t_us": meta.get("t_us"), "dropped": dropped})
            if dropped:
                self.counters["tx_drops"] += 1
                self.overrun_pending = True
                continue
            self._write(encode_frame(ftype, seq, payload))
        for ftype, seq, payload in self.out_resp:
            self.sent_log.append({"type": ftype, "seq": seq, "frame_seq": None, "t_us": self.u32(now),
                                  "dropped": False})
            self._write(encode_frame(ftype, seq, payload))
        self.out_data, self.out_resp = [], []
        while self.events:
            ev = self.events.popleft()
            self.sent_log.append({"type": int(pg.AsyncType.EVENT), "seq": ev[0], "frame_seq": None,
                                  "t_us": self.u32(now), "dropped": False})
            self._write(encode_frame(int(pg.AsyncType.EVENT), ev[0], ev[1:]))

    def _write(self, frame: bytes) -> None:
        self._log_wire("TX", frame)
        try:
            self.tx.write(frame)
        except Exception:  # noqa: BLE001 — PC end closed: the FW keeps running
            self.counters["tx_closed"] += 1

    def emit(self, code: int, arg: int = 0, value: int = 0, value2: int = 0) -> None:
        """Queue an EVENT (EVENT SEQ counts every event incl. those lost by overflow, ICD §2.2)."""
        seq = self.event_seq & 0xFF
        self.event_seq += 1
        payload = P.encode_event(P.EventPayload(self.u32(self.now_us()), int(code), int(arg) & 0xFFFF,
                                                int(value), int(value2)))
        if len(self.events) >= EVENT_QUEUE:
            self.counters["event_overflows"] += 1
            return
        self.events.append(bytes([seq]) + payload)
        for a in list(self.on_event_actions):
            if a["code"] == int(code):
                a["count"] += 1
                if a["count"] == a.get("nth", 1):
                    self.on_event_actions.remove(a)
                    self._schedule(a.get("delay_us", 0), a["then"])

    def _schedule(self, delay_us: int, action: dict[str, Any]) -> None:
        hook = self.act_hook
        if hook is None:
            return
        self.delayed.append(_Delayed(self.now_us() + int(delay_us), lambda: hook(dict(action))))

    # ============================================================================== command handling
    def _on_frame(self, fr: Frame) -> None:
        if not 0x01 <= fr.type <= 0x3F:
            self.counters["rx_frame_errors"] += 1
            return
        for f in list(self.faults):
            if f.what == "request" and (f.cmd is None or f.cmd == fr.type):
                if f.kind == "drop_next":
                    self._consume(f)
                    return
                if f.kind == "delay_next":
                    self._consume(f)
                    self.delayed.append(_Delayed(self.now_us() + f.ms * 1000, lambda fr=fr: self._handle(fr)))
                    return
                if f.kind == "duplicate_next":
                    self._consume(f)
                    self._handle(fr)
        self._handle(fr)

    def _consume(self, f: _Fault) -> None:
        f.n -= 1
        if f.n <= 0 and f in self.faults:
            self.faults.remove(f)

    def check_state(self) -> SimCheckState:
        """View of the live state with exactly the fields of the check vectors (state_schema 2)."""
        now = self.now_us()
        return SimCheckState(
            params=self.params, motion_state=self.motion_state,
            enabling_left_ms=max(0, (self.enabling_until_us - now) // 1000) if self.motion_state == "ENABLING" else 0,
            homed=self.homed, pos_um=self.pos_um if self._pos_override is None else self._pos_override,
            estop_latched=self.estop_latched,
            estop_input_open=self.world.estop_input_open(),
            estop_closed_ms=0 if self.estop_closed_since_us is None else (now - self.estop_closed_since_us) // 1000,
            halt_latched=self.halt_latched,
            faults=[n for i, n in enumerate(pg.FAULTS_BITS) if self.faults_mask >> i & 1],
            fault_causes=self._fault_causes(),
            limit_start=self._limit_active("start"), limit_end=self._limit_active("end"),
            afe_stale=self.afe_stale, afe_saturated=self.afe_saturated,
            raw=0 if self.last_raw == pg.AFE_NO_DATA else self.last_raw,
            drv_power=self.pwr_filt, alm_active=self.alm_filt,
            nvm_record_valid=self.nvm.newest_valid() is not None, paused=self.paused)

    def load_check_state(self, st: SimCheckState) -> None:
        """Set the live state from a check-vector state (differential replay, §12.5)."""
        now = self.now_us()
        self.params = {k: v for k, v in st.params.items()}
        self.motion_state = st.motion_state
        self.enabling_until_us = now + st.enabling_left_ms * 1000
        self.homed = st.homed
        self.steps = um_to_steps(int(st.pos_um), self.spm)
        self._pos_override = st.pos_um            # vector positions need not be step-representable
        if st.motion_state in MOVING:
            kind = {"STOPPING": "MOVE_ABS"}.get(st.motion_state, st.motion_state)
            n = 1000
            ramp = Ramp(F_TICK_HZ, 1000.0, float(self.p("motion.a_max_um_s2")), float(self.p("motion.a_max_um_s2")),
                        n)
            ramp.next()
            self.motion = Move(kind, 1, self.steps + n, "TARGET", 1000.0, float(self.p("motion.a_max_um_s2")), ramp,
                               float(now) + 1e9, last_refresh_us=now, stopping=st.motion_state == "STOPPING",
                               phase="FAST_SEEK" if kind == "HOMING" else "")
        else:
            self.motion = None
        self.estop_latched = st.estop_latched
        self.world.estop_open = st.estop_input_open
        self.estop_level = st.estop_input_open
        self.estop_closed_since_us = None if st.estop_input_open else now - st.estop_closed_ms * 1000
        self.halt_latched = st.halt_latched
        self.faults_mask = sum(int(pg.Faults[n]) for n in st.faults)
        self.forced_causes = set(st.fault_causes)
        self.limit_latch = {"start": st.limit_start, "end": st.limit_end}
        self.world.limit_start_forced = st.limit_start
        self.world.limit_end_forced = st.limit_end
        self.db["start"].level, self.db["end"].level = st.limit_start, st.limit_end
        self.afe_stale = st.afe_stale
        self.afe_saturated = st.afe_saturated
        self.last_raw = st.raw
        self.world.drv_power = st.drv_power
        self.pwr_filt = st.drv_power
        self.world.alm = st.alm_active
        self.alm_filt = st.alm_active
        if not st.nvm_record_valid:
            self.nvm.records = [None, None]
        elif self.nvm.newest_valid() is None:
            self.nvm.save(self.params)
        self.paused = st.paused
        self.pause_src = int(pg.Source.PC) if st.paused else int(pg.Source.NONE)

    def snapshot(self) -> dict[str, Any]:
        """Complete mutable FW state (no-side-effect check of NACKed vectors)."""
        m = self.motion
        return {
            "params": dict(self.params), "motion_state": self.motion_state, "homed": self.homed, "steps": self.steps,
            "motion": None if m is None else (m.kind, m.direction, m.end_steps, m.stopping, m.ramp.r, m.v_um_s),
            "estop": self.estop_latched, "halt": (self.halt_latched, self.halt_src),
            "paused": (self.paused, self.pause_src), "faults": self.faults_mask, "valid": list(self.valid_changes),
            "stream": self.stream_on, "nvm": self.nvm.summary(), "reboot_pending": self.reboot_pending,
            "limit": dict(self.limit_latch), "events": len(self.events), "enabling": self.enabling_until_us,
        }

    def _handle(self, fr: Frame) -> None:
        now = self.now_us()
        self.counters["rx_frames_ok"] += 1
        self.last_cmd_us = now                       # every valid command frame refreshes the watchdog
        if self.link_wdg:
            self.link_wdg = False
            self.emit(EV.LINK_RESTORED)
        for a in list(self.on_frame_actions):
            if a["cmd"] == fr.type:
                a["count"] += 1
                if a["count"] == a.get("nth", 1):
                    self.on_frame_actions.remove(a)
                    self._schedule(a.get("delay_us", 0), a["then"])
        nack = next((f for f in self.faults if f.kind == "nack" and (f.cmd is None or f.cmd == fr.type)), None)
        if nack is not None:
            self._consume(nack)
            self._respond(fr, P.encode_nack(nack.status, nack.detail))
            return
        status, detail = check(self.check_state(), fr.type, fr.payload)
        if status != "OK":
            self._respond(fr, P.encode_nack(int(pg.Status[status]), detail))
            return
        feat = FEATURE_OF_CMD.get(fr.type)
        if feat is not None and not self.has(feat) and not (fr.type == Cmd.JOG and fr.payload[:4] == b"\0\0\0\0"):
            self._respond(fr, P.encode_nack(int(pg.Status.E_INTERNAL), int(pg.InternalDetail.NOT_IN_BUILD)))
            return
        cmd = Cmd(fr.type)
        self._pos_override = None
        body = getattr(self, f"_cmd_{cmd.name.lower()}")(fr.payload)
        if isinstance(body, tuple):                  # execution error (E_NVM …)
            self._respond(fr, P.encode_nack(*body))
            return
        self._respond(fr, P.encode_ok(body or b""))

    def _respond(self, fr: Frame, payload: bytes) -> None:
        ftype = fr.type | pg.RESP_BIT
        for f in list(self.faults):
            if f.what == "response" and (f.cmd is None or f.cmd == fr.type) and f.kind != "nack":
                self._consume(f)
                if f.kind == "drop_next":
                    return
                if f.kind == "duplicate_next":
                    self.out_resp.append((ftype, fr.seq, payload))
                elif f.kind == "delay_next":
                    self.delayed.append(_Delayed(self.now_us() + f.ms * 1000,
                                                 lambda: self.out_resp.append((ftype, fr.seq, payload))))
                    return
                elif f.kind == "corrupt_next":
                    raw = bytearray(encode_frame(ftype, fr.seq, payload))
                    raw[-1] ^= 0xFF
                    self._log_wire("TX", bytes(raw))
                    try:
                        self.tx.write(bytes(raw))
                    except Exception:  # noqa: BLE001 pragma: no cover
                        pass
                    return
        self.out_resp.append((ftype, fr.seq, payload))

    # ---- system --------------------------------------------------------------------------------------
    def info(self) -> DeviceInfo:
        return DeviceInfo(pg.PROTO_MAJOR, pg.PROTO_MINOR, pg.PAYLOAD_VERSION, self.cfg.fw_version,
                          pgen.PARAM_DICT_HASH, SIM_UID, f"SIM-ICD{pg.ICD_VERSION}"[:16], pgen.PARAM_COUNT,
                          self.cfg.features)

    def _cmd_ping(self, _p: bytes) -> bytes:
        return b""

    def _cmd_get_info(self, _p: bytes) -> bytes:
        return P.encode_info(self.info())

    def _cmd_get_status(self, _p: bytes) -> bytes:
        return P.encode_status(self.board_status())

    def _cmd_reboot(self, _p: bytes) -> bytes:
        self.delayed.append(_Delayed(self.now_us() + 20_000, lambda: self.boot(int(pg.ResetCause.SOFTWARE))))
        return b""

    # ---- parameters / NVM ----------------------------------------------------------------------------
    def _entry(self, meta: pgen.ParamMeta) -> bytes:
        return P.encode_param_entry(meta.id, self.params[meta.key])

    def _cmd_get_all_params(self, p: bytes) -> bytes:
        page = p[0]
        n = pg.PARAMS_PER_PAGE
        count = -(-pgen.PARAM_COUNT // n)
        metas = sorted(pgen.PARAMS, key=lambda m: m.id)[page * n:(page + 1) * n]
        return P.encode_param_page(page, count, [self._entry(m) for m in metas])

    def _cmd_get_param(self, p: bytes) -> bytes:
        return self._entry(pgen.BY_ID[struct.unpack("<H", p)[0]])

    def _cmd_set_param(self, p: bytes) -> bytes:
        e = P.decode_param_entry(p)
        meta = pgen.BY_ID[e.id]
        value = e.value()
        if meta.key in self.store_mismatch:          # test hook: "as stored" differs
            value = self.store_mismatch.pop(meta.key)
        old_spm = self.spm
        self.params[meta.key] = value
        if meta.key == "motion.steps_per_mm":
            # HOMED kept: machine zero is a step count, µm positions rescale (FW-MOT-009); the carriage stays
            self.true_offset_um += self.dir_sign * (self.steps * 1000.0 / old_spm - self.steps * 1000.0 / self.spm)
        if meta.reboot_required:
            self.reboot_pending = True
        if meta.key == "io.pause_active_level":       # re-arm without generating a press
            d = self.db["pause"]
            d.level = self._pause_level()
            d.armed, d.rel_since = not d.level, None
        if meta.key in ("afe.gain_channel", "afe.rate_sps"):
            self.settle_left = int(self.params["afe.settle_discard"])
            self.afe.rate_sps = 80.0 if int(self.params["afe.rate_sps"]) == 1 else 10.0
        return self._entry(meta)

    def _cmd_save_params(self, _p: bytes) -> bytes:
        cut = self.nvm.cut_next_save
        rec = self.nvm.save(self.params)
        if cut:                                      # power cut during the program → reset
            self.delayed.append(_Delayed(self.now_us() + 1000, lambda: self.boot(int(pg.ResetCause.POWER_ON))))
            return b""
        self.record_seq = rec.seq
        self.nvm_defaulted = False
        self.nvm_save_ms = 120
        self.nvm_save_uptime_ms = self.uptime_ms()
        self.emit(EV.PARAMS_SAVED, 0, rec.seq)
        return b""

    def _cmd_load_params(self, _p: bytes) -> bytes | tuple[int, int]:
        img = self.nvm.boot_image(self.params, for_load=True)
        if not img.ok:                               # no EVENT on failure (ICD v0.5, OBS-M1-03)
            return int(pg.Status.E_NVM), int(pg.NvmDetail.NO_RECORD)
        self.params = dict(img.values)
        self.nvm_defaulted = img.defaulted
        self.record_seq = img.record_seq
        self.emit(EV.PARAMS_DEFAULTED if img.event == "PARAMS_DEFAULTED" else EV.PARAMS_LOADED, img.arg)
        return b""

    def _cmd_default_params(self, _p: bytes) -> bytes:
        self.params = {m.key: m.default for m in pgen.PARAMS}
        self.emit(EV.PARAMS_DEFAULTED, int(pg.ParamsDefaultedReason.COMMAND))
        return b""

    # ---- stream / valid ------------------------------------------------------------------------------
    def _cmd_stream_start(self, _p: bytes) -> bytes:
        self.stream_on = True
        return b""

    def _cmd_stream_stop(self, _p: bytes) -> bytes:
        self.stream_on = False
        return b""

    def _cmd_set_valid(self, p: bytes) -> bytes:
        t = self.u32(self.now_us())
        self._set_valid(p[0], t)
        return P.encode_u32(t)

    def _set_valid(self, v: int, t_u32: int) -> None:
        self.valid = int(v)
        self.valid_changes.append((t_u32, int(v)))
        del self.valid_changes[:-4]

    def valid_at(self, t_u32: int) -> int:
        val = self.valid_changes[0][1]
        for t, v in self.valid_changes:
            if ((t_u32 - t) & 0xFFFFFFFF) < 0x80000000:
                val = v
        return val

    def _clear_valid(self, cause: int) -> None:
        if self.valid:
            self._set_valid(0, self.u32(self.now_us()))
            self.emit(EV.VALID_CLEARED, cause)

    # ---- enable / motion -----------------------------------------------------------------------------
    def _cmd_enable(self, _p: bytes) -> bytes:
        now = self.now_us()
        if self.motion_state == "NOT_ENABLED":
            settle = int(self.p("motion.ena_settle_ms"))
            self.motion_state = "ENABLING"
            self.ena_disabled = False
            # settle counted from the later of ENABLE and the driver power return (FW-MOT-008)
            self.enabling_until_us = max(now, self.pwr_on_us) + settle * 1000
            if self.enabling_until_us <= now:
                self._enabled()
                return P.encode_u16(0)
            return P.encode_u16((self.enabling_until_us - now) // 1000)
        if self.motion_state == "ENABLING":
            return P.encode_u16(max(0, (self.enabling_until_us - now) // 1000))
        return P.encode_u16(0)

    def _enabled(self) -> None:
        self.motion_state = "IDLE"
        self.idle_since_us = self.now_us()
        self.emit(EV.DRIVER_ENABLED)

    def _disable(self, cause: int) -> None:
        """ENA to the disabled level, NOT_ENABLED, HOMED cleared, DRIVER_DISABLED (if not already)."""
        self.ena_disabled = True
        self.homed = False
        if self.motion_state != "NOT_ENABLED":
            self.motion_state = "NOT_ENABLED"
            self.emit(EV.DRIVER_DISABLED, cause)

    def _cmd_disable(self, _p: bytes) -> bytes:
        self._disable(int(pg.DriverDisabledCause.PC_DISABLE))
        return b""

    def _a(self, a: int) -> float:
        return float(a or self.p("motion.a_max_um_s2"))

    def _pw_us(self) -> float:
        return int(self.p("motion.pulse_high_ns")) / 1000.0

    def _begin(self, kind: str, end_steps: int, v_um_s: float, a_um_s2: float, end_reason: str, *,
               d_um_s2: float | None = None, t_us: float | None = None, **kw: Any) -> Move | None:
        """Start a motion leg from the current step count; ``None`` when there is nothing to move."""
        n = abs(int(end_steps) - self.steps)
        if n == 0:
            return None
        spm = self.spm
        v = max(1e-3, v_um_s * spm / 1000.0)
        a = max(1e-3, a_um_s2 * spm / 1000.0)
        d = max(1e-3, (d_um_s2 if d_um_s2 is not None else a_um_s2) * spm / 1000.0)
        ramp = Ramp(F_TICK_HZ, v, a, d, n)
        c = ramp.next() or 0
        t0 = float(self.now_us() if t_us is None else t_us)
        setup_ticks = int(self.p("motion.dir_setup_us")) * TICKS_PER_US + self._pw_us() * TICKS_PER_US
        first = max(float(c), setup_ticks)                   # first edge ≥ dir_setup after the DIR write
        m = Move(kind, 1 if end_steps > self.steps else -1, int(end_steps), end_reason, float(v_um_s),
                 float(a_um_s2), ramp, t0 + first / TICKS_PER_US, start_steps=self.steps,
                 last_refresh_us=int(t0), **kw)
        self.motion = m
        self.motion_state = kind
        self.idle_since_us = None
        self.pend_wait_since_us = None
        self.target_um = steps_to_um(int(end_steps), spm)
        return m

    def _cmd_move_abs(self, p: bytes) -> bytes:
        target, v, a = struct.unpack("<iII", p)
        end = um_to_steps(target, self.spm)
        if self._begin("MOVE_ABS", end, float(v), self._a(a), "TARGET") is None:
            self.emit(EV.MOVE_DONE, int(pg.MoveDoneReason.TARGET), self.pos_um, self.pos_steps)
        return b""

    def _cmd_move_until_load(self, p: bytes) -> bytes:
        bound, v, a, raw_stop, cmp_ = struct.unpack("<iIIiB", p)
        if self._beyond(self.last_raw, raw_stop, cmp_):      # pre-check before the first pulse
            self.emit(EV.MOVE_DONE, int(pg.MoveDoneReason.LOAD_THRESHOLD), self.pos_um, self.pos_steps)
            return b""
        if self._begin("MOVE_UNTIL_LOAD", um_to_steps(bound, self.spm), float(v), self._a(a), "BOUND",
                       raw_stop=raw_stop, cmp=cmp_) is None:
            self.emit(EV.MOVE_DONE, int(pg.MoveDoneReason.BOUND), self.pos_um, self.pos_steps)
        return b""

    @staticmethod
    def _beyond(raw: int, raw_stop: int, cmp_: int) -> bool:
        if raw == pg.AFE_NO_DATA:
            return False
        return raw >= raw_stop if cmp_ == pg.MulCmp.GE else raw <= raw_stop

    def _jog_end(self, d: int, bound: int, start_steps: int) -> tuple[int, str]:
        if bound != pg.JOG_NO_BOUND:
            return um_to_steps(bound, self.spm), "BOUND"
        if self.homed:
            lim = int(self.p("limits.soft_max_um") if d > 0 else self.p("limits.soft_min_um"))
            return um_to_steps(lim, self.spm), "SOFT_LIMIT"
        return start_steps + d * um_to_steps(int(self.p("home.max_travel_um")), self.spm), "SOFT_LIMIT"

    def _cmd_jog(self, p: bytes) -> bytes:
        v, a, bound = struct.unpack("<iIi", p)
        m = self.motion
        now = self.now_us()
        if v == 0:
            if m is not None and m.kind == "JOG" and not m.stopping:
                self._controlled_stop(None, "JOG_ZERO")
            return b""
        d = 1 if v > 0 else -1
        if m is not None and m.kind == "JOG":
            m.last_refresh_us = now                      # dead-man refresh (also during a reversal)
            if m.stopping and m.reverse is None:
                return b""                               # a stop in progress is never undone
            start = m.start_steps if not self.homed else self.steps
            end, reason = self._jog_end(d, bound, start)
            if d != m.direction or m.reverse is not None:  # reversal: decelerate to zero first, then restart
                m.reverse = (d, end, float(abs(v)), self._a(a), reason)   # (motion state stays JOG)
                if not m.stopping:
                    m.stopping = True
                    m.ramp.stop(self._a(a) * self.spm / 1000.0)
                return b""
            m.end_steps, m.end_reason, m.v_um_s, m.a_um_s2 = end, reason, float(abs(v)), self._a(a)
            m.ramp.set_end(max(0, abs(end - self.steps) - 1))
            m.ramp.set_speed(abs(v) * self.spm / 1000.0)
            self.target_um = steps_to_um(end, self.spm)
            return b""
        end, reason = self._jog_end(d, bound, self.steps)
        if self._begin("JOG", end, float(abs(v)), self._a(a), reason) is None:
            self.emit(EV.MOVE_DONE, int(pg.MoveDoneReason[reason]), self.pos_um, self.pos_steps)
        return b""

    def _cmd_home(self, _p: bytes) -> bytes:
        self._home_drift = 0
        if self._input_level("start"):
            self._home_leg("RELEASE")
        else:
            self._home_leg("FAST_SEEK")
        self.motion_state = "HOMING"
        return b""

    # ---- homing (FW_design §5.5) --------------------------------------------------------------------
    def _home_leg(self, phase: str, t_us: float | None = None) -> None:
        spm = self.spm
        v_cap = v_limit_um_s(250_000, int(self.p("motion.max_step_rate_hz")), spm)   # homing: step-rate cap only
        v_fast = min(int(self.p("home.v_fast_um_s")), v_cap)
        v_slow = min(int(self.p("home.v_slow_um_s")), v_cap)
        a = float(self.p("home.a_um_s2"))
        if phase in ("RELEASE", "BACKOFF"):
            end = self.steps + um_to_steps(int(pg.HOME_RELEASE_MAX_UM), spm)
            v = v_slow
        elif phase == "FAST_SEEK":
            end = self.steps - um_to_steps(int(self.p("home.max_travel_um")), spm)
            v = v_fast
        elif phase == "SLOW_APPROACH":
            end = self.steps - um_to_steps(int(self.p("home.backoff_um")) + int(pg.HOME_SLOW_EXTRA_UM), spm)
            v = v_slow
        else:                                                   # MOVE_TO_ZERO
            end, v = 0, v_fast
        self.home_phase = int(pg.HomePhase[phase])
        m = self._begin("HOMING", end, float(v), a, "TARGET", t_us=t_us, phase=phase)
        if m is None:                                           # already at 0 (MOVE_TO_ZERO)
            self._home_done()
        else:
            self.motion_state = "HOMING"

    def _home_edge(self, m: Move) -> None:
        """START edge in FAST_SEEK (→ BACKOFF) or SLOW_APPROACH (capture, zero, drift → MOVE_TO_ZERO)."""
        t = m.t_next_us
        self.motion = None
        if m.phase == "FAST_SEEK":
            self._home_leg("BACKOFF", t_us=t)
            return
        edge = self.steps                                       # captured step count at the edge
        offset = um_to_steps(int(self.p("home.offset_um")), self.spm)
        if self.homed:                                          # FW-HOM-004: drift vs the expected edge position
            self._home_drift = steps_to_um(edge, self.spm) + int(self.p("home.offset_um"))
        else:
            self._home_drift = 0
        new = self.steps - edge - offset                        # the edge lies at x = −home.offset_um
        self.true_offset_um += self.dir_sign * (self.steps - new) * 1000.0 / self.spm
        self.steps = new
        self._home_leg("MOVE_TO_ZERO", t_us=t)

    def _home_done(self) -> None:
        self.homed = True
        self.pos_uncertain = False
        self.home_phase = int(pg.HomePhase.DONE)
        drift = self._home_drift
        if abs(drift) > int(self.p("home.drift_tol_um")):
            self._fault("HOME_DRIFT", drift)
        self.emit(EV.HOMED, 0, drift)
        self._finish("TARGET")

    def _home_fail(self, reason: int, fault: str | None) -> None:
        """Homing failure: CLEAN halt, HOMED cleared, FAULT (not for ABORTED), HOME_FAILED, STOPPED, MOVE_DONE."""
        self._clean_halt_position()
        self.homed = False
        self.home_phase = int(pg.HomePhase.DONE)
        if fault is not None:
            self._fault(fault, self.pos_um, self.pos_steps)
        self.emit(EV.HOME_FAILED, reason, self.pos_um, self.pos_steps)
        self.emit(EV.STOPPED, int(SC.HOME_FAIL), self.pos_um, self.pos_steps)
        self._clear_valid(int(SC.HOME_FAIL))
        self._finish("STOPPED")

    # ---- stops / clears ------------------------------------------------------------------------------
    def _cmd_stop(self, p: bytes) -> bytes:
        cause = SC.PC_STOP if p[0] == 0 else SC.PC_STOP_CONTROLLED
        if self.motion is not None:
            if p[0] == 0:
                self._immediate_stop(int(cause))
            else:
                self._controlled_stop(int(cause), "STOPPED")
        self._clear_valid(int(cause))
        return b""

    def _cmd_halt(self, _p: bytes) -> bytes:
        cause = int(SC.PC_HALT)
        if not self.halt_latched:
            self.halt_latched = True
            self.halt_src = int(pg.Source.PC)           # PC only since ICD v0.5 (D-36)
            self.emit(EV.HALT_SET, int(pg.Source.PC))
        if self.motion is not None:
            self._immediate_stop(cause)
        self._clear_valid(cause)
        return b""

    def _cmd_pause(self, _p: bytes) -> bytes:
        self._pause(int(pg.Source.PC))
        return b""

    def _pause(self, src: int) -> None:
        cause = SC.PC_PAUSE if src == pg.Source.PC else SC.PAUSE_BUTTON
        if self.paused:
            if src == pg.Source.BUTTON:
                self.emit(EV.RESUME_REQUEST)
            return
        self.paused = True
        self.pause_src = src
        self.emit(EV.PAUSED, src)
        if self.motion is not None and not self.motion.stopping:
            self._controlled_stop(int(cause), "STOPPED")
        self._clear_valid(int(cause))

    def _cmd_resume(self, _p: bytes) -> bytes:
        if self.paused:
            self.paused = False
            self.pause_src = int(pg.Source.NONE)
            self.emit(EV.PAUSE_CLEARED, int(pg.PauseClearedReason.RESUME))
        return b""

    def _cmd_halt_clear(self, _p: bytes) -> bytes:
        if self.halt_latched:
            self.halt_latched = False
            self.halt_src = int(pg.Source.NONE)
            self.emit(EV.HALT_CLEARED)
        if self.paused:
            self.paused = False
            self.pause_src = int(pg.Source.NONE)
            self.emit(EV.PAUSE_CLEARED, int(pg.PauseClearedReason.HALT_CLEAR))
        return b""

    def _cmd_estop_clear(self, _p: bytes) -> bytes:
        if self.estop_latched:
            self.estop_latched = False
            self.emit(EV.ESTOP_CLEARED)
        return b""

    def _cmd_fault_clear(self, _p: bytes) -> bytes:
        causes = set(self._fault_causes())
        cleared = 0
        for i, name in enumerate(pg.FAULTS_BITS):
            if self.faults_mask >> i & 1 and (name not in causes or name == "LOAD_LIMIT"):
                cleared |= 1 << i
        self.loadlim.fault_clear()                     # SAF-FW-011 / D-40 d: every clear takes a new reference
        self.faults_mask &= ~cleared
        self.forced_causes = None
        if cleared:
            self.emit(EV.FAULT_CLEARED, cleared)
        return P.encode_u16(cleared)

    def _fault_causes(self) -> list[str]:
        if self.forced_causes is not None:
            return sorted(self.forced_causes)
        out = []
        if self.afe_stale or self.afe_saturated:
            out.append("AFE_FAULT")
        if self._input_level("start") and self._input_level("end"):
            out.append("LIMIT_WIRING")
        if bool(self.p("drv.pwr_sense_enable")) and self.has(FE.DRV_SIGNALS) and self.estop_level and self.pwr_filt:
            out.append("K1_WELDED")
        return out

    # ============================================================================== motion execution
    def _finish(self, reason: str) -> None:
        """End of a motion (position final): IDLE, queue MOVE_DONE (emitted after the events of this handler)."""
        self.motion = None
        if self.motion_state in MOVING:
            self.motion_state = "IDLE"
            self.idle_since_us = self.now_us()
            self.pend_wait_since_us = self.now_us()
        self.target_um = self.pos_um
        self._done_pending.append((int(pg.MoveDoneReason[reason]), self.pos_um, self.pos_steps))

    def _emit_done(self) -> None:
        while self._done_pending:
            self.emit(EV.MOVE_DONE, *self._done_pending.pop(0))

    def _clean_halt_position(self, t_us: float | None = None, *, truncate: bool = False) -> None:
        """CLEAN halt: a pulse in flight (its high phase started) completes and is counted. TRUNCATE (E-stop): it is
        cut and not counted → POS_UNCERTAIN (SAF-FW-004)."""
        m = self.motion
        if m is None:
            return
        t = float(self.now_us() if t_us is None else t_us)
        if 0 < m.t_next_us - t < self._pw_us():
            if truncate:
                self.pos_uncertain = True
            else:
                self.steps += m.direction
                self.pulses += 1

    def _immediate_stop(self, cause: int, *, truncate: bool = False) -> None:
        """Immediate stop of the running motion: HOME_FAILED(ABORTED) for homing, STOPPED(cause), MOVE_DONE STOPPED
        (queued). VALID is cleared by the caller (after STOPPED)."""
        m = self.motion
        if m is None:
            return
        self._clean_halt_position(truncate=truncate)
        if m.kind == "HOMING":
            self.homed = False
            self.home_phase = int(pg.HomePhase.DONE)
            self.emit(EV.HOME_FAILED, int(pg.HomeFailReason.ABORTED), self.pos_um, self.pos_steps)
        self.emit(EV.STOPPED, cause, self.pos_um, self.pos_steps)
        self._finish("STOPPED")

    def _controlled_stop(self, cause: int | None, reason: str) -> None:
        """Controlled stop with ``motion.a_stop_um_s2``; clean halt only when the step period > 2 ms **and** the
        planned stop distance ≤ 1 step (§6.5, D-30). STOPPED (if a cause) at initiation, MOVE_DONE at standstill.
        Homing ends immediately (every homing stop is a failure, FW-HOM-002)."""
        m = self.motion
        if m is None or (m.stopping and m.reverse is None):
            return
        if m.kind == "HOMING":
            self._immediate_stop(int(cause) if cause is not None else int(SC.PC_STOP_CONTROLLED))
            return
        m.reverse = None
        if cause is not None:
            self.emit(EV.STOPPED, cause, self.pos_um, self.pos_steps)
        m.stopping, m.stop_cause, m.stop_reason = True, cause, reason
        a_stop = float(self.p("motion.a_stop_um_s2")) * self.spm / 1000.0
        c_last = m.ramp.c_last
        if c_last is None or ctrl_stop_path(c_last, F_TICK_HZ, a_stop)[0] == "CLEAN":
            self.motion = m                                    # clean halt: no further pulse is started
            self._clean_halt_position()
            self._finish(reason)
            return
        m.ramp.stop(a_stop)
        self.motion_state = "STOPPING"

    def _advance_axis(self, t_us: float) -> None:
        """Execute every step completed up to ``t_us`` (per-step switch checks, leg ends)."""
        m = self.motion
        while m is not None and m.t_next_us <= t_us:
            self.steps += m.direction
            self.pulses += 1
            if self._step_switches(m):
                m = self.motion
                continue
            c = m.ramp.next()
            if c is None:
                self._leg_end(m)
                m = self.motion
                continue
            m.t_next_us += c / TICKS_PER_US

    def _step_switches(self, m: Move) -> bool:
        """Limit / homing edges at the position of this step; True when the motion changed."""
        t = int(m.t_next_us)
        xt = self._x_true()
        for name, level in (("start", self.world.limit_start(xt)), ("end", self.world.limit_end(xt))):
            if self._edge(name, level, t):
                if self._limit_edge(name, t):
                    return True
        return self.motion is not m

    def _leg_end(self, m: Move) -> None:
        t = m.t_next_us
        if m.kind == "HOMING":
            self.motion = None
            if m.phase in ("RELEASE", "BACKOFF"):
                if not m.released:
                    self.motion = m
                    self._home_fail(int(pg.HomeFailReason.WIRING), "HOME_WIRING")
                else:
                    self._home_leg("FAST_SEEK" if m.phase == "RELEASE" else "SLOW_APPROACH", t_us=t)
            elif m.phase == "MOVE_TO_ZERO":
                self._home_done()
            else:                                              # FAST_SEEK / SLOW_APPROACH bound reached
                self.motion = m
                self._home_fail(int(pg.HomeFailReason.NOT_FOUND), "HOME_NOT_FOUND")
            return
        if m.kind == "JOG" and m.reverse is not None:
            d, end, v, a, reason = m.reverse
            self.motion = None
            if self._begin("JOG", end, v, a, reason, t_us=t) is None:
                self._finish(reason)
            elif self.motion is not None:
                self.motion.last_refresh_us = m.last_refresh_us
            return
        self._finish(m.stop_reason if m.stopping else m.end_reason)

    def _fault(self, name: str, value: int = 0, value2: int = 0) -> None:
        bit = int(pg.Faults[name])
        if not self.faults_mask & bit:
            self.faults_mask |= bit
            self.emit(EV.FAULT_SET, pg.FAULTS_BITS.index(name), value, value2)

    # ============================================================================== inputs
    def _pause_level(self) -> bool:
        if not self.has(FE.BUTTONS):
            return False
        closed = self.world.pause_closed()
        return closed if int(self.params.get("io.pause_active_level", 1)) == 1 else not closed

    def _alm_level(self) -> bool:
        return self.has(FE.DRV_SIGNALS) and self.world.alm_active()

    def _input_level(self, name: str) -> bool:
        xt = self._x_true()
        if name == "start":
            return self.world.limit_start(xt)
        if name == "end":
            return self.world.limit_end(xt)
        return self._pause_level()

    def _edge(self, name: str, level: bool, t_us: int) -> bool:
        """Debounce: True on the first active edge of an armed input (FW-SW-001)."""
        d = self.db[name]
        d.level = level
        if level:
            d.rel_since = None
            if d.armed:
                d.armed = False
                return True
            return False
        if d.rel_since is None:
            d.rel_since = t_us
        if not d.armed and t_us - d.rel_since >= int(self.p("io.release_ms")) * 1000:
            d.armed = True
            self._released(name)
        return False

    def _released(self, name: str) -> None:
        if name in ("start", "end") and self.limit_latch[name]:
            self.limit_latch[name] = False
            self.emit(EV.LIMIT_CLEARED, int(pg.LimitId.START if name == "start" else pg.LimitId.END))
        elif name == "pause":
            self.emit(EV.PAUSE_BUTTON, 0)
        m = self.motion
        if name == "start" and m is not None and m.kind == "HOMING" and m.phase in ("RELEASE", "BACKOFF") \
                and not m.released:
            m.released = True                         # released: back-off further, then the next phase
            m.ramp.set_end(max(0, um_to_steps(int(self.p("home.backoff_um")), self.spm) - 1))

    def _limit_edge(self, name: str, t_us: int) -> bool:
        """First active edge of a limit input; True when the running motion was stopped/changed."""
        m = self.motion
        if m is not None and m.kind == "HOMING":
            if name == "start" and m.phase in ("FAST_SEEK", "SLOW_APPROACH"):
                self._home_edge(m)                       # expected edge: no LIMIT_START latch
                return True
            if name == "end":                            # END keeps its limit function; homing -> HOME_WIRING
                if not self.limit_latch["end"]:
                    self.limit_latch["end"] = True
                    self.emit(EV.LIMIT_SET, int(pg.LimitId.END), self.pos_um, self.pos_steps)
                self._home_fail(int(pg.HomeFailReason.WIRING), "HOME_WIRING")
                return True
            return False
        lid = pg.LimitId.START if name == "start" else pg.LimitId.END
        if not self.limit_latch[name]:
            self.limit_latch[name] = True
            self.emit(EV.LIMIT_SET, int(lid), self.pos_um, self.pos_steps)
        cause = int(SC.LIMIT_START if name == "start" else SC.LIMIT_END)
        stopped = False
        if m is not None:
            self._immediate_stop(cause)
            self._clear_valid(cause)
            stopped = True
        if self.db["start"].level and self.db["end"].level:
            self._limit_wiring()
        return stopped

    def _limit_wiring(self) -> None:
        if self.faults_mask & FA.LIMIT_WIRING:
            return
        self._fault("LIMIT_WIRING", self.pos_um, self.pos_steps)
        if self.motion is not None:
            self._immediate_stop(int(SC.LIMIT_WIRING))
        self._clear_valid(int(SC.LIMIT_WIRING))

    def _limit_active(self, name: str) -> bool:
        return self.limit_latch[name] or self.db[name].level

    # ============================================================================== 1 ms FW tick
    def _tick_1ms(self, t_us: int) -> None:
        sense = bool(self.p("drv.pwr_sense_enable")) and self.has(FE.DRV_SIGNALS)
        # E-stop sense (act on the edge; HAL TRUNCATE + ENA disabled)
        lvl = self.world.estop_input_open()
        if lvl and not self.estop_level:
            self.estop_level = True
            self.estop_closed_since_us = None
            self.k1_since_us = t_us
            if not self.estop_latched:
                self.estop_latched = True
                self.emit(EV.ESTOP_SET, 0, self.pos_um, self.pos_steps)
            if self.motion is not None:
                self._immediate_stop(int(SC.ESTOP), truncate=True)
            self._clear_valid(int(SC.ESTOP))
            self._disable(int(pg.DriverDisabledCause.ESTOP))
        elif not lvl and self.estop_level:
            self.estop_level = False
            self.estop_closed_since_us = t_us
        # driver power (20 ms filter, SAF-FW-024)
        if sense:
            raw = self.world.power_present()
            if raw != self.pwr_filt:
                if self.pwr_change_since is None:
                    self.pwr_change_since = t_us
                elif t_us - self.pwr_change_since >= DRV_PWR_FILTER_MS * 1000:
                    self.pwr_change_since = None
                    self._power_change(raw, t_us)
            else:
                self.pwr_change_since = None
        else:
            self.pwr_filt = True
        # K1 weld (SAF-FW-025)
        if sense and self.estop_level and self.pwr_filt:
            if self.k1_since_us is None:
                self.k1_since_us = t_us
            elif t_us - self.k1_since_us >= int(self.p("drv.k1_weld_ms")) * 1000 and \
                    not self.faults_mask & FA.K1_WELDED:
                self._fault("K1_WELDED", (t_us - self.k1_since_us) // 1000)
        else:
            self.k1_since_us = None
        # limits (forced levels; position edges are checked per step) + wiring
        for name in ("start", "end"):
            if self._edge(name, self._input_level(name), t_us):
                self._limit_edge(name, t_us)
        if self.db["start"].level and self.db["end"].level:
            self._limit_wiring()
        # PAUSE button
        if self._edge("pause", self._pause_level(), t_us):
            self.emit(EV.PAUSE_BUTTON, 1)
            self._pause(int(pg.Source.BUTTON))
        # ALM (active on the first sample, inactive after a stable io.release_ms)
        alm = self._alm_level()
        if alm and not self.alm_filt:
            self.alm_filt = True
            self.alm_rel_since = None
            self.emit(EV.ALM_CHANGED, 1)
        elif not alm and self.alm_filt:
            if self.alm_rel_since is None:
                self.alm_rel_since = t_us
            elif t_us - self.alm_rel_since >= int(self.p("io.release_ms")) * 1000:
                self.alm_filt = False
                self.alm_rel_since = None
                self.emit(EV.ALM_CHANGED, 0)
        elif alm:
            self.alm_rel_since = None
        # enable settle
        if self.motion_state == "ENABLING" and t_us >= self.enabling_until_us:
            self._enabled()
        # AFE stale
        if not self.afe_stale and t_us - self.last_sample_us > int(self.p("afe.timeout_ms")) * 1000:
            self.afe_stale = True
            self.emit(EV.AFE_STALE, 1)
            if self.motion is not None:
                self._fault("AFE_FAULT", self.pos_um, self.pos_steps)
                self._immediate_stop(int(SC.AFE_FAULT))
                self._clear_valid(int(SC.AFE_FAULT))
            self.next_fallback_us = t_us
        if self.afe_stale and self.stream_on and t_us >= self.next_fallback_us:
            self.next_fallback_us = t_us + 1_000_000 // max(1, int(self.p("stream.fallback_hz")))
            self._data_frame(t_us, pg.AFE_NO_DATA, fallback=True)
        # link watchdog (moving only, controlled stop)
        if self.motion is not None and not self.link_wdg and \
                t_us - self.last_cmd_us >= int(self.p("safety.link_timeout_ms")) * 1000:
            self.link_wdg = True
            self.emit(EV.LINK_WDG)
            self._controlled_stop(int(SC.LINK_WDG), "STOPPED")
            self._clear_valid(int(SC.LINK_WDG))
        # jog dead-man (VALID unchanged)
        m = self.motion
        if m is not None and m.kind == "JOG" and (not m.stopping or m.reverse is not None) and \
                t_us - m.last_refresh_us > int(self.p("motion.jog_timeout_ms")) * 1000:
            self._controlled_stop(int(SC.JOG_DEADMAN), "STOPPED")
        # idle disable (SAF-FW-017: unloaded, AFE fresh, continuously idle)
        self._idle_disable(t_us)
        # PEND / NOT_SETTLED (warning only)
        to = int(self.p("drv.pend_timeout_ms"))
        if self.pend_wait_since_us is not None:
            if not self.has(FE.DRV_SIGNALS) or to == 0 or self.alm_filt or not self.pwr_filt or self._pend_level():
                self.pend_wait_since_us = None
            elif t_us - self.pend_wait_since_us >= to * 1000:
                self.emit(EV.NOT_SETTLED, 0, (t_us - self.pend_wait_since_us) // 1000)
                self.pend_wait_since_us = None

    def _pend_level(self) -> bool:
        return self.has(FE.DRV_SIGNALS) and self.world.pend_active() and self.motion is None

    def _power_change(self, on: bool, t_us: int) -> None:
        self.pwr_filt = on
        self.emit(EV.DRIVER_POWER, 1 if on else 0)
        if on:
            self.pwr_on_us = t_us
            return
        if self.motion is not None:
            self._immediate_stop(int(SC.DRV_POWER_LOST))
        self._clear_valid(int(SC.DRV_POWER_LOST))
        self._disable(int(pg.DriverDisabledCause.DRV_POWER_LOST))

    def _unloaded(self) -> bool:
        return self.last_raw != pg.AFE_NO_DATA and abs(self.last_raw - int(self.p("safety.zero_raw"))) < \
            int(self.p("safety.release_band_raw"))

    def _idle_disable(self, t_us: int) -> None:
        lim = int(self.p("safety.idle_disable_s"))
        if lim == 0 or self.motion_state != "IDLE" or self.afe_stale or not self._unloaded():
            self.idle_since_us = None             # the counter restarts when moving, loaded or stale (D-33 g)
            return
        if self.idle_since_us is None:
            self.idle_since_us = t_us
        elif t_us - self.idle_since_us >= lim * 1_000_000:
            self.idle_since_us = None
            self._disable(int(pg.DriverDisabledCause.IDLE))

    def idle_left_s(self) -> int:
        lim = int(self.p("safety.idle_disable_s"))
        if lim == 0 or self.motion_state != "IDLE" or self.idle_since_us is None:
            return 0xFFFF
        return max(0, lim - (self.now_us() - self.idle_since_us) // 1_000_000)

    # ============================================================================== samples / DATA
    def _force_n(self, t_us: int | None = None) -> float:
        return self.world.specimen.force_n(self._x_true(), t_us)

    def _on_sample(self, t_us: int, raw: int) -> None:
        if self.afe_stale:
            self.afe_stale = False
            self.emit(EV.AFE_STALE, 0)
        if self.last_sample_us and t_us > self.last_sample_us:
            self.periods.append(t_us - self.last_sample_us)
        self.last_sample_us = t_us
        self.last_raw = raw
        self.afe_saturated = raw in (pg.RAW_MIN, pg.RAW_MAX)
        self._rate_check()
        # FW load limit on every sample (D-12): ICD v0.6 §5.5 model (rails, trip count, regrow window D-40 d)
        ll = self.loadlim
        ll.config(int(self.p("safety.load_raw_min")), int(self.p("safety.load_raw_max")),
                  int(self.p("safety.load_trip_samples")), int(self.p("safety.load_regrow_raw")))
        if ll.sample(raw) and not self.faults_mask & FA.LOAD_LIMIT:
            self._fault("LOAD_LIMIT", raw, self.pos_steps)
            if self.motion is not None:
                self._immediate_stop(int(SC.LOAD_LIMIT))
            self._clear_valid(int(SC.LOAD_LIMIT))
        m = self.motion
        if m is not None and m.kind == "MOVE_UNTIL_LOAD" and not m.stopping and self._beyond(raw, m.raw_stop, m.cmp):
            self._clean_halt_position(t_us)
            self._finish("LOAD_THRESHOLD")
        settling = self.settle_left > 0
        if settling:
            self.settle_left -= 1
        if self.stream_on:
            self._data_frame(t_us, raw, settling=settling)

    def _rate_check(self) -> None:
        if len(self.periods) < 16:
            return
        med = sorted(self.periods)[8]
        cfg = 80.0 if int(self.p("afe.rate_sps")) == 1 else 10.0
        meas = 1e6 / med
        mismatch = abs(meas - cfg) / cfg * 100.0 > int(self.p("afe.rate_tol_pct"))
        if mismatch != self.rate_mismatch:
            self.rate_mismatch = mismatch
            self.emit(EV.AFE_RATE_MISMATCH, 1 if mismatch else 0, int(round(meas * 10)))

    def flags_now(self, t_u32: int | None = None) -> int:
        f = 0
        if (self.valid_at(t_u32) if t_u32 is not None else self.valid):
            f |= DF.VALID
        if self.motion is not None:
            f |= DF.MOVING
        if self.homed:
            f |= DF.HOMED
        if self.motion_state not in ("NOT_ENABLED", "ENABLING"):
            f |= DF.ENABLED
        if self.estop_latched or self.world.estop_input_open():
            f |= DF.ESTOP
        if self.halt_latched:
            f |= DF.HALT
        if self.faults_mask:
            f |= DF.FAULT
        return int(f)

    def status_now(self, *, saturated: bool = False, settling: bool = False, fallback: bool = False) -> int:
        s = 0
        if self.paused:
            s |= DS.PAUSED
        if self._limit_active("start"):
            s |= DS.LIMIT_START
        if self._limit_active("end"):
            s |= DS.LIMIT_END
        if self.faults_mask & FA.LOAD_LIMIT:
            s |= DS.LOAD_LIMIT
        if self.afe_stale:
            s |= DS.AFE_STALE
        if saturated:
            s |= DS.AFE_SATURATED
        if settling:
            s |= DS.AFE_SETTLING
        if self.rate_mismatch:
            s |= DS.AFE_RATE_MISMATCH
        if self.link_wdg:
            s |= DS.LINK_WDG
        if self.db["pause"].level:
            s |= DS.PAUSE_BTN
        if self.has(FE.DRV_SIGNALS):             # feature-dependent bits are sent as 0 otherwise (D-37 b)
            if self.alm_filt:
                s |= DS.ALM
            if self._pend_level():
                s |= DS.PEND
            if self.pwr_filt:
                s |= DS.DRV_PWR
        if self.pos_uncertain:
            s |= DS.POS_UNCERTAIN
        if fallback:
            s |= DS.NO_AFE_DATA
        ov = self.status_override
        if ov is not None:
            if self.now_us() < ov[2]:
                s = (s | ov[0]) & ~ov[1]
            else:
                self.status_override = None
        return int(s) & 0xFFFF

    def _data_frame(self, t_us: int, raw: int, *, settling: bool = False, fallback: bool = False) -> None:
        t32 = self.u32(t_us)
        flags = self.flags_now(t32)
        if self.overrun_pending:
            flags |= DF.OVERRUN
            self.overrun_pending = False
        seq = self.frame_seq & 0xFFFF
        self.frame_seq += 1
        sat = raw in (pg.RAW_MIN, pg.RAW_MAX)
        s = P.DataSample(t32, pg.PAYLOAD_VERSION, flags, raw, self.pos_um, seq,
                         self.status_now(saturated=sat, settling=settling, fallback=fallback))
        self.out_data.append((int(pg.AsyncType.DATA), seq & 0xFF, P.encode_data(s), {"frame_seq": seq, "t_us": t32}))

    # ============================================================================== STATUS
    def board_status(self) -> BoardStatus:
        now = self.now_us()
        io = 0
        if self.world.estop_input_open():
            io |= INT_IO.ESTOP_OPEN
        if self.db["start"].level:
            io |= INT_IO.LIMIT_START
        if self.db["end"].level:
            io |= INT_IO.LIMIT_END
        if self.db["pause"].level:
            io |= INT_IO.PAUSE_BTN
        if self.has(FE.DRV_SIGNALS):                 # D-37 b: invalid → sent as 0
            if self.world.alm_active():
                io |= INT_IO.ALM
            if self.world.pend_active():
                io |= INT_IO.PEND
            if self.world.power_present():
                io |= INT_IO.DRV_PWR
        if self.motion_state == "NOT_ENABLED" and self.ena_disabled:
            io |= INT_IO.ENA_DISABLED
        if int(self.p("afe.rate_sps")) == 1:
            io |= INT_IO.RATE_80
        sysf = 0
        if self.nvm.differs(self.params):
            sysf |= pg.SysFlags.CFG_DIRTY
        if self.stream_on:
            sysf |= pg.SysFlags.STREAM_ON
        if self.reboot_pending:
            sysf |= pg.SysFlags.REBOOT_PENDING
        if self.nvm_defaulted:
            sysf |= pg.SysFlags.NVM_DEFAULTED
        med = sorted(self.periods)[len(self.periods) // 2] if self.periods else 0
        loaded = self.afe_stale or (self.last_raw != pg.AFE_NO_DATA and abs(
            self.last_raw - int(self.p("safety.zero_raw"))) >= int(self.p("safety.release_band_raw")))
        vlim = v_limit_um_s(int(self.p("motion.v_max_load_um_s" if loaded else "motion.v_max_travel_um_s")),
                            int(self.p("motion.max_step_rate_hz")), self.spm)
        return BoardStatus(
            uptime_ms=self.uptime_ms() & 0xFFFFFFFF, t_us=self.u32(now), flags=self.flags_now(),
            motion_state=int(pg.MotionState[self.motion_state]), status=self.status_now(), faults=self.faults_mask,
            io=int(io), home_phase=self.home_phase, halt_src=self.halt_src, reset_cause=self.reset_cause,
            sys_flags=int(sysf), pos_um=self.pos_um, target_um=self.target_um if self.motion else self.pos_um,
            pos_steps=self.pos_steps, afe_raw_last=self.last_raw,
            afe_rate_dsps=int(round(1e7 / med)) if med else 0, afe_reinit_count=self.afe_reinit,
            rx_frames_ok=self.counters["rx_frames_ok"], rx_crc_errors=self.decoder.counters.crc_errors,
            rx_frame_errors=self.decoder.counters.len_errors + self.decoder.counters.timeout_drops
            + self.counters["rx_frame_errors"],
            rx_overruns=0, tx_drops=self.counters["tx_drops"],
            event_overflows=self.counters["event_overflows"] & 0xFFFF, loop_max_us=150,
            link_age_ms=min(0xFFFF, (now - self.last_cmd_us) // 1000), stack_free_min=2048,
            nvm_save_ms=self.nvm_save_ms, idle_disable_left_s=self.idle_left_s(), nvm_record_seq=self.record_seq,
            nvm_save_uptime_ms=self.nvm_save_uptime_ms, v_limit_um_s=vlim, pause_src=self.pause_src)

    # ============================================================================== test hooks
    def add_fault(self, kind: str, cmd: int | None, *, what: str = "response", n: int = 1, ms: int = 0,
                  status: int = 0, detail: int = 0) -> None:
        with self._lock:
            self.faults.append(_Fault(kind, cmd, what, n, ms, status, detail))

    def inject_step_fault(self) -> None:
        """Step overrun / count fault (vocabulary ``inject step_fault``): CLEAN halt, STEP_FAULT, HOMED cleared,
        POS_UNCERTAIN, VALID cleared."""
        with self._lock:
            self._fault("STEP_FAULT", self.pos_um, self.pos_steps)
            if self.motion is not None:
                self._immediate_stop(int(SC.STEP_FAULT))
            self.homed = False
            self.pos_uncertain = True
            self._clear_valid(int(SC.STEP_FAULT))
            self._emit_done()


__all__ = ["SimBoard", "SimConfig", "Move", "DEFAULT_FEATURES", "FEATURE_OF_CMD"]
