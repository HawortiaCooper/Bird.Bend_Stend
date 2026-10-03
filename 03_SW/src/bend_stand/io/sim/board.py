"""SimBoard — the in-process FW simulator (SW_design §12): protocol endpoint, command dispatch, FW state machine,
NVM, DATA/EVENT streaming, world-control hooks. Uses the **same** codec (``io.framing``/``io.protocol``, board
direction) and the generated dictionary; command acceptance is the pure ``sim.check.check`` (a NACK has no side
effect by construction — the state is only changed after an OK verdict).

Time: virtual board time from the ``Clock`` (``t_us`` = µs since boot + ``t0_us``, 32-bit on the wire).
``step(now_ns)`` processes RX, runs the 1 ms FW tick, produces HX711 conversions and flushes TX in the FW wire
order DATA > responses > EVENT (ICD §2.4). On the real clock ``start()`` runs it in a 1 ms thread; on the
lockstep clock the backend calls ``step`` from ``test_hooks.advance()``.

M1 scope (WP-B7): everything of the M1 command set incl. NVM rules, stream / fallback frames / OVERRUN,
SET_VALID boundary + auto-clear, latches (ESTOP, HALT, PAUSED, LINK_WDG, LIMIT, FAULT LOAD_LIMIT / AFE_FAULT /
STEP_FAULT), ENABLE settle, RESUME / clears (D-31, D-34), events with the EVENT SEQ counter, GET_STATUS 86 B;
simple trapezoid motion for MOVE_ABS / JOG (dead-man) / MOVE_UNTIL_LOAD / HOME (fast seek + edge, no back-off
yet) and stops. Exact step-period ramps, homing back-off / slow approach, switch bounce, K1_WELDED timing,
idle disable and load regrow are WP-B12 (M2). D-36: the physical STOP button keeps only the M1 minimum
(HALT source BUTTON) until CR-01 retires it.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/simulator.py @37c87471 (architecture: loopback endpoint,
per-command dispatch with NACK first, fault injector, NVM as JSON; behaviour rewritten for the bend stand).

Implements: SYS-008, IF-007, IF-010, FW-CMD-001 (sim), FW-CMD-002 (sim), FW-STR-001…006 (sim), D-07
"""
from __future__ import annotations

import collections
import logging
import math
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bend_stand.calc.motion import steps_to_um, um_to_steps
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import Clock
from bend_stand.core.model import BoardStatus, DeviceInfo
from bend_stand.io import protocol as P
from bend_stand.io.framing import Frame, FrameDecoder, encode_frame
from bend_stand.io.sim.check import MOVING, SimCheckState, check
from bend_stand.io.sim.models import Hx711Model, World
from bend_stand.io.sim.nvm import NvmStore
from bend_stand.io.transport import Transport

log = logging.getLogger("bend_stand.sim")
Cmd = pg.Cmd
DF, DS, FA = pg.DataFlags, pg.DataStatus, pg.Faults
SC = pg.StopCause
EV = pg.Event
DEFAULT_FEATURES = int(pg.Features.AFE | pg.Features.MOTION | pg.Features.HOMING | pg.Features.MOVE_UNTIL_LOAD
                       | pg.Features.NVM | pg.Features.BUTTONS | pg.Features.DRV_SIGNALS)
SIM_UID = "53494D0000000000B1BDB0A0"
EVENT_QUEUE = 32
POSITION_FUDGE_UM = 0.5


@dataclass
class Motion:
    kind: str                      # MOVE_ABS | JOG | MOVE_UNTIL_LOAD | HOMING
    end_um: float                  # end point (target / bound / soft limit)
    v_um_s: float
    a_um_s2: float
    direction: int
    end_reason: str = "TARGET"
    raw_stop: int = 0
    cmp: int = 0
    home_phase: str = "FAST_SEEK"
    last_refresh_us: int = 0
    stopping: bool = False
    stop_cause: int | None = None
    stop_reason: str = "STOPPED"
    v_cur: float = 0.0
    start_um: float = 0.0


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
class SimConfig:
    features: int = DEFAULT_FEATURES
    fw_version: tuple[int, int, int] = (0, 1, 0)
    t0_us: int = 1_000_000
    reset_cause: int = int(pg.ResetCause.POWER_ON)
    true_offset_um: int = 100_000          # axis position at boot relative to the machine zero (not homed)
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
        self._pos_override: int | None = None
        self._hung = False
        self.boot(cause=self.cfg.reset_cause, first=True)

    # ============================================================================== boot / time
    def boot(self, cause: int = int(pg.ResetCause.POWER_ON), first: bool = False) -> None:
        with self._lock:
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
            self.x_um = 0.0
            self.true_offset_um = self.cfg.true_offset_um if first else getattr(self, "true_offset_um",
                                                                                   self.cfg.true_offset_um)
            self.motion: Motion | None = None
            self.target_um = 0
            self.pos_uncertain = False
            self.estop_latched = self.world.estop_input_open()
            self.estop_closed_since_us: int | None = None if self.world.estop_input_open() else 0
            self.halt_latched = False
            self.halt_src = int(pg.Source.NONE)
            self.stop_released_since_us: int | None = 0
            self.paused = False
            self.pause_src = int(pg.Source.NONE)
            self.faults_mask = 0
            self.limit_latch = {"start": False, "end": False}
            self.limit_release_since: dict[str, int | None] = {"start": None, "end": None}
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
            self.load_trip_count = 0
            self.prev_inputs = self._inputs()
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
        return um_to_steps(int(round(self.x_um)), self.spm)

    @property
    def pos_um(self) -> int:
        return steps_to_um(self.pos_steps, self.spm)

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
            self._drain_rx(discard=now < self.link_silence_until_us)
            while self.last_tick_us + 1000 <= now:
                self.last_tick_us += 1000
                self._tick_1ms(self.last_tick_us)
            while self.afe.due(now):
                t = self.afe.next_drdy_us
                raw = self.afe.convert(self._force_n())
                if raw is not None:
                    self._on_sample(t, raw)
                else:
                    self.overrun_pending = True
            self._flush()

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
            halt_latched=self.halt_latched, stop_btn_active=self.world.stop_btn,
            stop_btn_released_ms=0 if self.stop_released_since_us is None
            else (now - self.stop_released_since_us) // 1000,
            faults=[n for i, n in enumerate(pg.FAULTS_BITS) if self.faults_mask >> i & 1],
            fault_causes=self._fault_causes(),
            limit_start=self._limit_active("start"), limit_end=self._limit_active("end"),
            afe_stale=self.afe_stale, afe_saturated=self.afe_saturated,
            raw=0 if self.last_raw == pg.AFE_NO_DATA else self.last_raw,
            drv_power=self.world.power_present(), alm_active=self.world.alm,
            nvm_record_valid=self.nvm.newest_valid() is not None, paused=self.paused)

    def load_check_state(self, st: SimCheckState) -> None:
        """Set the live state from a check-vector state (differential replay, §12.5)."""
        now = self.now_us()
        self.params = {k: v for k, v in st.params.items()}
        self.motion_state = st.motion_state
        self.enabling_until_us = now + st.enabling_left_ms * 1000
        self.homed = st.homed
        self.x_um = float(st.pos_um)
        self._pos_override = st.pos_um            # vector positions need not be step-representable
        if st.motion_state in MOVING:
            self.motion = Motion("JOG" if st.motion_state == "JOG" else
                                 ("HOMING" if st.motion_state == "HOMING" else st.motion_state
                                  if st.motion_state != "STOPPING" else "MOVE_ABS"),
                                 float(st.pos_um) + 1000.0, 1000.0, float(self.p("motion.a_max_um_s2")), 1,
                                 last_refresh_us=now, stopping=st.motion_state == "STOPPING")
        else:
            self.motion = None
        self.estop_latched = st.estop_latched
        self.world.estop_open = st.estop_input_open
        self.estop_closed_since_us = None if st.estop_input_open else now - st.estop_closed_ms * 1000
        self.halt_latched = st.halt_latched
        self.world.stop_btn = st.stop_btn_active
        self.stop_released_since_us = None if st.stop_btn_active else now - st.stop_btn_released_ms * 1000
        self.faults_mask = sum(int(FA[n]) for n in st.faults)
        self.forced_causes = set(st.fault_causes)
        self.limit_latch = {"start": st.limit_start, "end": st.limit_end}
        self.world.limit_start_forced = st.limit_start
        self.world.limit_end_forced = st.limit_end
        self.afe_stale = st.afe_stale
        self.afe_saturated = st.afe_saturated
        self.last_raw = st.raw
        self.world.drv_power = st.drv_power
        self.world.alm = st.alm_active
        if not st.nvm_record_valid:
            self.nvm.records = [None, None]
        elif self.nvm.newest_valid() is None:
            self.nvm.save(self.params)
        self.paused = st.paused
        self.pause_src = int(pg.Source.PC) if st.paused else int(pg.Source.NONE)

    def snapshot(self) -> dict[str, Any]:
        """Complete mutable FW state (no-side-effect check of NACKed vectors)."""
        return {
            "params": dict(self.params), "motion_state": self.motion_state, "homed": self.homed, "x": self.x_um,
            "motion": None if self.motion is None else dict(vars(self.motion)), "estop": self.estop_latched,
            "halt": (self.halt_latched, self.halt_src), "paused": (self.paused, self.pause_src),
            "faults": self.faults_mask, "valid": list(self.valid_changes), "stream": self.stream_on,
            "nvm": self.nvm.summary(), "reboot_pending": self.reboot_pending, "limit": dict(self.limit_latch),
            "events": len(self.events), "enabling": self.enabling_until_us,
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
        cmd = Cmd(fr.type)
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
        self.params[meta.key] = value
        if meta.reboot_required:
            self.reboot_pending = True
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
        if not img.ok:
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
            self.enabling_until_us = now + settle * 1000
            if settle == 0:
                self._enabled()
            return P.encode_u16(settle)
        if self.motion_state == "ENABLING":
            return P.encode_u16(max(0, (self.enabling_until_us - now) // 1000))
        return P.encode_u16(0)

    def _enabled(self) -> None:
        self.motion_state = "IDLE"
        self.emit(EV.DRIVER_ENABLED)

    def _cmd_disable(self, _p: bytes) -> bytes:
        if self.motion_state != "NOT_ENABLED":
            self.motion_state = "NOT_ENABLED"
            self.homed = False
            self.emit(EV.DRIVER_DISABLED, int(pg.DriverDisabledCause.PC_DISABLE))
        return b""

    def _a(self, a: int) -> float:
        return float(a or self.p("motion.a_max_um_s2"))

    def _start(self, m: Motion) -> None:
        m.start_um = self.x_um
        m.last_refresh_us = self.now_us()
        self.motion = m
        self.motion_state = m.kind
        self.target_um = int(round(m.end_um))

    def _cmd_move_abs(self, p: bytes) -> bytes:
        target, v, a = struct.unpack("<iII", p)
        if target == self.pos_um:
            self.emit(EV.MOVE_DONE, int(pg.MoveDoneReason.TARGET), self.pos_um, self.pos_steps)
            return b""
        self._start(Motion("MOVE_ABS", float(target), float(v), self._a(a), 1 if target > self.x_um else -1))
        return b""

    def _cmd_move_until_load(self, p: bytes) -> bytes:
        bound, v, a, raw_stop, cmp_ = struct.unpack("<iIIiB", p)
        if self._beyond(self.last_raw, raw_stop, cmp_):
            self.emit(EV.MOVE_DONE, int(pg.MoveDoneReason.LOAD_THRESHOLD), self.pos_um, self.pos_steps)
            return b""
        self._start(Motion("MOVE_UNTIL_LOAD", float(bound), float(v), self._a(a),
                           1 if bound > self.x_um else -1, end_reason="BOUND", raw_stop=raw_stop, cmp=cmp_))
        return b""

    @staticmethod
    def _beyond(raw: int, raw_stop: int, cmp_: int) -> bool:
        if raw == pg.AFE_NO_DATA:
            return False
        return raw >= raw_stop if cmp_ == pg.MulCmp.GE else raw <= raw_stop

    def _cmd_jog(self, p: bytes) -> bytes:
        v, a, bound = struct.unpack("<iIi", p)
        m = self.motion
        if v == 0:
            if m is not None and m.kind == "JOG" and not m.stopping:
                self._controlled_stop(None, "JOG_ZERO")
            return b""
        d = 1 if v > 0 else -1
        if bound != pg.JOG_NO_BOUND:
            end, reason = float(bound), "BOUND"
        elif self.homed:
            end = float(self.p("limits.soft_max_um") if d > 0 else self.p("limits.soft_min_um"))
            reason = "SOFT_LIMIT"
        else:
            end, reason = self.x_um + d * float(self.p("home.max_travel_um")), "SOFT_LIMIT"
        if m is not None and m.kind == "JOG" and not m.stopping:
            if d != m.direction:                       # reversal: decelerate to zero first (simplified)
                m.v_cur = 0.0
            m.direction, m.end_um, m.v_um_s, m.a_um_s2, m.end_reason = d, end, float(abs(v)), self._a(a), reason
            m.last_refresh_us = self.now_us()
            self.target_um = int(round(end))
            return b""
        self._start(Motion("JOG", end, float(abs(v)), self._a(a), d, end_reason=reason))
        return b""

    def _cmd_home(self, _p: bytes) -> bytes:
        v = float(self.p("home.v_fast_um_s"))
        self.home_phase = int(pg.HomePhase.FAST_SEEK)
        if self.world.limit_start(self._x_true()):
            self.home_phase = int(pg.HomePhase.RELEASE)
            self._start(Motion("HOMING", self.x_um + float(pg.HOME_RELEASE_MAX_UM), v,
                               float(self.p("home.a_um_s2")), 1, home_phase="RELEASE"))
        else:
            self._start(Motion("HOMING", self.x_um - float(self.p("home.max_travel_um")), v,
                               float(self.p("home.a_um_s2")), -1, home_phase="FAST_SEEK"))
        self.motion_state = "HOMING"
        return b""

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
        self._halt(int(pg.Source.PC))
        return b""

    def _halt(self, src: int) -> None:
        cause = SC.PC_HALT if src == pg.Source.PC else SC.STOP_BUTTON
        if self.motion is not None:
            self._immediate_stop(int(cause))
        if not self.halt_latched:
            self.halt_latched = True
            self.halt_src = src
            self.emit(EV.HALT_SET, src)
        self._clear_valid(int(cause))

    def _cmd_pause(self, _p: bytes) -> bytes:
        self._pause(int(pg.Source.PC))
        return b""

    def _pause(self, src: int) -> None:
        cause = SC.PC_PAUSE if src == pg.Source.PC else SC.PAUSE_BUTTON
        if self.paused:
            if src == pg.Source.BUTTON:
                self.emit(EV.RESUME_REQUEST)
            return
        if self.motion is not None and not self.motion.stopping:
            self._controlled_stop(int(cause), "STOPPED")
        self.paused = True
        self.pause_src = src
        self.emit(EV.PAUSED, src)
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
        self.faults_mask &= ~cleared
        if cleared:
            self.emit(EV.FAULT_CLEARED, cleared)
        return P.encode_u16(cleared)

    def _fault_causes(self) -> list[str]:
        forced = getattr(self, "forced_causes", None)
        if forced is not None:
            return sorted(forced)
        out = []
        if self.afe_stale or self.afe_saturated:
            out.append("AFE_FAULT")
        if self.world.limit_start(self._x_true()) and self.world.limit_end(self._x_true()):
            out.append("LIMIT_WIRING")
        if bool(self.p("drv.pwr_sense_enable")) and self.world.estop_input_open() and self.world.power_present():
            out.append("K1_WELDED")
        return out

    # ============================================================================== motion execution
    def _x_true(self) -> float:
        return self.x_um + self.true_offset_um

    def _finish(self, reason: str, cause: int | None = None) -> None:
        if cause is not None:
            self.emit(EV.STOPPED, cause, self.pos_um, self.pos_steps)
        self.motion = None
        if self.motion_state in MOVING:
            self.motion_state = "IDLE" if self.motion_state != "NOT_ENABLED" else "NOT_ENABLED"
        self.target_um = self.pos_um
        self.emit(EV.MOVE_DONE, int(pg.MoveDoneReason[reason]), self.pos_um, self.pos_steps)

    def _immediate_stop(self, cause: int, *, keep_state: str | None = None) -> None:
        m = self.motion
        if m is None:
            return
        if m.kind == "HOMING":
            self.homed = False
            self.home_phase = int(pg.HomePhase.DONE)
            self.emit(EV.HOME_FAILED, int(pg.HomeFailReason.ABORTED), self.pos_um, self.pos_steps)
        self.pos_uncertain = True
        self.x_um = float(self.pos_um)
        self._finish("STOPPED", cause)
        if keep_state:
            self.motion_state = keep_state

    def _controlled_stop(self, cause: int | None, reason: str) -> None:
        m = self.motion
        if m is None or m.stopping:
            return
        m.stopping = True
        m.stop_cause = cause
        m.stop_reason = reason
        self.motion_state = "STOPPING"
        if m.v_cur <= 0:
            self._end_controlled()

    def _end_controlled(self) -> None:
        m = self.motion
        assert m is not None
        if m.kind == "HOMING":
            self.homed = False
            self.home_phase = int(pg.HomePhase.DONE)
            self.emit(EV.HOME_FAILED, int(pg.HomeFailReason.ABORTED), self.pos_um, self.pos_steps)
        self.x_um = float(self.pos_um)
        self._finish(m.stop_reason, m.stop_cause)

    def _motion_tick(self, dt_s: float) -> None:
        m = self.motion
        if m is None:
            return
        if m.stopping:
            a_stop = float(self.p("motion.a_stop_um_s2"))
            m.v_cur = max(0.0, m.v_cur - a_stop * dt_s)
            self.x_um += m.direction * m.v_cur * dt_s
            self.pulses += 1
            if m.v_cur <= 0.0:
                self._end_controlled()
            return
        dist = (m.end_um - self.x_um) * m.direction
        v_brake = math.sqrt(max(0.0, 2.0 * m.a_um_s2 * dist))
        v_target = min(m.v_um_s, v_brake)
        if m.v_cur < v_target:
            m.v_cur = min(v_target, m.v_cur + m.a_um_s2 * dt_s)
        else:
            m.v_cur = max(v_target, m.v_cur - m.a_um_s2 * dt_s)
        m.v_cur = max(m.v_cur, min(m.v_um_s, 200.0))     # creep so a planned stop always completes
        step = m.v_cur * dt_s
        self.pulses += 1
        if step >= dist - POSITION_FUDGE_UM:
            self.x_um = m.end_um
            if m.kind == "HOMING":
                self._homing_end_of_leg(m)
                return
            self._finish(m.end_reason)
            return
        self.x_um += m.direction * step
        if m.kind == "HOMING":
            self._homing_check(m)

    def _homing_check(self, m: Motion) -> None:
        xt = self._x_true()
        if m.home_phase == "RELEASE" and not self.world.limit_start(xt):
            m.home_phase = "FAST_SEEK"
            self.home_phase = int(pg.HomePhase.FAST_SEEK)
            m.direction, m.end_um, m.v_cur = -1, self.x_um - float(self.p("home.max_travel_um")), 0.0
        elif m.home_phase == "FAST_SEEK" and self.world.limit_start(xt):
            # edge captured: set the zero so that this edge lies at −home.offset_um (simplified, no back-off)
            drift = 0
            new_x = -float(self.p("home.offset_um"))
            if self.homed:
                drift = int(round(self.x_um - new_x))
            self.true_offset_um = int(round(xt - new_x))
            self.x_um = new_x
            m.home_phase = "MOVE_TO_ZERO"
            self.home_phase = int(pg.HomePhase.MOVE_TO_ZERO)
            m.direction, m.end_um, m.v_cur = 1, 0.0, 0.0
            self._home_drift = drift
        elif self.world.limit_end(xt):
            self._home_fail(int(pg.HomeFailReason.WIRING), "HOME_WIRING")

    def _homing_end_of_leg(self, m: Motion) -> None:
        if m.home_phase == "MOVE_TO_ZERO":
            self.homed = True
            self.pos_uncertain = False
            self.home_phase = int(pg.HomePhase.DONE)
            drift = getattr(self, "_home_drift", 0)
            if abs(drift) > int(self.p("home.drift_tol_um")):
                self._fault("HOME_DRIFT", abs(drift))
            self.emit(EV.HOMED, 0, abs(drift))
            self._finish("TARGET")
        elif m.home_phase == "RELEASE":
            self._home_fail(int(pg.HomeFailReason.WIRING), "HOME_WIRING")
        else:
            self._home_fail(int(pg.HomeFailReason.NOT_FOUND), "HOME_NOT_FOUND")

    def _home_fail(self, reason: int, fault: str) -> None:
        self.homed = False
        self.home_phase = int(pg.HomePhase.DONE)
        self._fault(fault, self.pos_um, self.pos_steps)
        self.emit(EV.HOME_FAILED, reason, self.pos_um, self.pos_steps)
        self.pos_uncertain = True
        self._finish("STOPPED", int(SC.HOME_FAIL))
        self._clear_valid(int(SC.HOME_FAIL))

    def _fault(self, name: str, value: int = 0, value2: int = 0) -> None:
        bit = int(FA[name])
        if not self.faults_mask & bit:
            self.faults_mask |= bit
            self.emit(EV.FAULT_SET, pg.FAULTS_BITS.index(name), value, value2)

    # ============================================================================== 1 ms FW tick
    def _inputs(self) -> dict[str, bool]:
        xt = self._x_true() if hasattr(self, "true_offset_um") else self.x_um
        return {"estop": self.world.estop_input_open(), "start": self.world.limit_start(xt),
                "end": self.world.limit_end(xt), "stop": self.world.stop_btn, "pause": self.world.pause_btn,
                "alm": self.world.alm, "power": self.world.power_present()}

    def _limit_active(self, name: str) -> bool:
        return self.limit_latch[name] or self.prev_inputs.get(name, False)

    def _tick_1ms(self, t_us: int) -> None:
        inp = self._inputs()
        prev = self.prev_inputs
        self.prev_inputs = inp
        sense = bool(self.p("drv.pwr_sense_enable"))
        # E-stop sense
        if inp["estop"] and not prev["estop"]:
            if self.motion is not None:
                self._immediate_stop(int(SC.ESTOP))
            self.estop_closed_since_us = None
            if not self.estop_latched:
                self.estop_latched = True
                self.emit(EV.ESTOP_SET, 0, self.pos_um, self.pos_steps)
            if self.motion_state != "NOT_ENABLED":
                self.motion_state = "NOT_ENABLED"
                self.emit(EV.DRIVER_DISABLED, int(pg.DriverDisabledCause.ESTOP))
            self.homed = False
            self._clear_valid(int(SC.ESTOP))
        elif not inp["estop"] and prev["estop"]:
            self.estop_closed_since_us = t_us
        # driver power (sense enabled)
        if sense and inp["power"] != prev["power"]:
            self.emit(EV.DRIVER_POWER, 1 if inp["power"] else 0)
            if not inp["power"]:
                if self.motion is not None:
                    self._immediate_stop(int(SC.DRV_POWER_LOST))
                if self.motion_state != "NOT_ENABLED":
                    self.motion_state = "NOT_ENABLED"
                    self.emit(EV.DRIVER_DISABLED, int(pg.DriverDisabledCause.DRV_POWER_LOST))
                self.homed = False
                self._clear_valid(int(SC.DRV_POWER_LOST))
        # STOP button (D-36: M1 minimum — HALT source BUTTON)
        if inp["stop"] != prev["stop"]:
            self.emit(EV.STOP_BUTTON, 1 if inp["stop"] else 0)
            if inp["stop"]:
                self.stop_released_since_us = None
                self._halt(int(pg.Source.BUTTON))
            else:
                self.stop_released_since_us = t_us
        # PAUSE button
        if inp["pause"] != prev["pause"]:
            self.emit(EV.PAUSE_BUTTON, 1 if inp["pause"] else 0)
            if inp["pause"]:
                self._pause(int(pg.Source.BUTTON))
        if inp["alm"] != prev["alm"]:
            self.emit(EV.ALM_CHANGED, 1 if inp["alm"] else 0)
        # limits
        for name, lid, cause in (("start", pg.LimitId.START, SC.LIMIT_START), ("end", pg.LimitId.END, SC.LIMIT_END)):
            if inp[name] and not prev[name]:
                homing = self.motion is not None and self.motion.kind == "HOMING" and name == "start"
                if not homing:
                    m = self.motion
                    if m is not None and (m.direction < 0) == (name == "start"):
                        self._immediate_stop(int(cause))
                        self._clear_valid(int(cause))
                    if not self.limit_latch[name]:
                        self.limit_latch[name] = True
                        self.emit(EV.LIMIT_SET, int(lid), self.pos_um, self.pos_steps)
                self.limit_release_since[name] = None
            elif not inp[name] and self.limit_latch[name]:
                since = self.limit_release_since[name]
                if since is None:
                    self.limit_release_since[name] = t_us
                elif t_us - since >= int(self.p("io.release_ms")) * 1000:
                    self.limit_latch[name] = False
                    self.limit_release_since[name] = None
                    self.emit(EV.LIMIT_CLEARED, int(lid))
        if inp["start"] and inp["end"] and not (self.faults_mask & FA.LIMIT_WIRING):
            if self.motion is not None:
                self._immediate_stop(int(SC.LIMIT_WIRING))
            self._fault("LIMIT_WIRING", self.pos_um, self.pos_steps)
            self._clear_valid(int(SC.LIMIT_WIRING))
        # enable settle
        if self.motion_state == "ENABLING" and t_us >= self.enabling_until_us:
            self._enabled()
        # AFE stale
        if not self.afe_stale and t_us - self.last_sample_us > int(self.p("afe.timeout_ms")) * 1000:
            self.afe_stale = True
            self.emit(EV.AFE_STALE, 1)
            if self.motion is not None:
                self._immediate_stop(int(SC.AFE_FAULT))
                self._fault("AFE_FAULT", self.pos_um, self.pos_steps)
                self._clear_valid(int(SC.AFE_FAULT))
            self.next_fallback_us = t_us
        if self.afe_stale and self.stream_on and t_us >= self.next_fallback_us:
            self.next_fallback_us = t_us + 1_000_000 // max(1, int(self.p("stream.fallback_hz")))
            self._data_frame(t_us, pg.AFE_NO_DATA, fallback=True)
        # link watchdog (moving only)
        if self.motion is not None and not self.link_wdg and \
                t_us - self.last_cmd_us > int(self.p("safety.link_timeout_ms")) * 1000:
            self.link_wdg = True
            self.emit(EV.LINK_WDG)
            self._controlled_stop(int(SC.LINK_WDG), "STOPPED")
            self._clear_valid(int(SC.LINK_WDG))
        # jog dead-man
        m = self.motion
        if m is not None and m.kind == "JOG" and not m.stopping and \
                t_us - m.last_refresh_us > int(self.p("motion.jog_timeout_ms")) * 1000:
            self._controlled_stop(int(SC.JOG_DEADMAN), "STOPPED")   # VALID unchanged
        self._motion_tick(0.001)

    # ============================================================================== samples / DATA
    def _force_n(self) -> float:
        return self.world.specimen.force_n(self.x_um)

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
        # FW load limit on every sample (D-12); rails always trip
        lo, hi = int(self.p("safety.load_raw_min")), int(self.p("safety.load_raw_max"))
        if raw > hi or raw < lo or self.afe_saturated:
            self.load_trip_count += 1
            if (self.load_trip_count >= int(self.p("safety.load_trip_samples")) or self.afe_saturated) and \
                    not self.faults_mask & FA.LOAD_LIMIT:
                if self.motion is not None:
                    self._immediate_stop(int(SC.LOAD_LIMIT))
                self._fault("LOAD_LIMIT", raw, self.pos_steps)
                self._clear_valid(int(SC.LOAD_LIMIT))
        else:
            self.load_trip_count = 0
        m = self.motion
        if m is not None and m.kind == "MOVE_UNTIL_LOAD" and not m.stopping and self._beyond(raw, m.raw_stop, m.cmp):
            self.x_um = float(self.pos_um)
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
        inp = self.prev_inputs
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
        if inp.get("stop"):
            s |= DS.STOP_BTN
        if inp.get("pause"):
            s |= DS.PAUSE_BTN
        if self.world.alm:
            s |= DS.ALM
        if self.world.pend and self.motion is None:
            s |= DS.PEND
        if self.pos_uncertain:
            s |= DS.POS_UNCERTAIN
        if fallback:
            s |= DS.NO_AFE_DATA
        if self.world.power_present() or not bool(self.p("drv.pwr_sense_enable")):
            s |= DS.DRV_PWR
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
        inp = self.prev_inputs
        io = 0
        for name, bit in (("estop", pg.IoBits.ESTOP_OPEN), ("start", pg.IoBits.LIMIT_START),
                          ("end", pg.IoBits.LIMIT_END), ("stop", pg.IoBits.STOP_BTN), ("pause", pg.IoBits.PAUSE_BTN),
                          ("alm", pg.IoBits.ALM), ("power", pg.IoBits.DRV_PWR)):
            if inp.get(name):
                io |= bit
        if self.world.pend:
            io |= pg.IoBits.PEND
        if self.motion_state == "NOT_ENABLED":
            io |= pg.IoBits.ENA_DISABLED
        if int(self.p("afe.rate_sps")) == 1:
            io |= pg.IoBits.RATE_80
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
        from bend_stand.calc.motion import v_limit_um_s  # noqa: PLC0415
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
            nvm_save_ms=self.nvm_save_ms, idle_disable_left_s=0xFFFF, nvm_record_seq=self.record_seq,
            nvm_save_uptime_ms=self.nvm_save_uptime_ms, v_limit_um_s=vlim, pause_src=self.pause_src)

    # ============================================================================== test hooks
    def add_fault(self, kind: str, cmd: int | None, *, what: str = "response", n: int = 1, ms: int = 0,
                  status: int = 0, detail: int = 0) -> None:
        with self._lock:
            self.faults.append(_Fault(kind, cmd, what, n, ms, status, detail))
