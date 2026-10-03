# Bird Bend Stand — PC Software Design (SW_design)

| Item | Value |
|---|---|
| Version | **0.1 — DRAFT for the P1 gate** |
| Date | 2026-10-03 |
| Owner | Implementer B (SW backend): overall architecture + backend (`core`, `io`, `calc`). GUI design: `03_SW/docs/SW_design_GUI.md` (Implementer D), which consumes §15 of this document. |
| Binding inputs | `00_System/specs/SRS.md` v0.2 (SAF-SW-, SW-, IF-, NFR-001..004, §3.2, §5.2), `DECISIONS.md` D-01…D-27, R3 (§1.6–1.7, §5, §6, §7), R4 (§2, §4–§12), R5 (§0, §5.5, §8) |
| Pending inputs | `ICD_protocol.md`, `params.yaml`, `core/params_gen.py`, `00_System/tools/ref_codec.py` (Integrator, in progress). This document fixes **semantics only**; command codes, byte layouts, status/NACK codes, timeouts and retry counts are referenced as "ICD §x (pending)". Where a design choice needs an ICD feature it is listed as a finding (§21). |
| Reference | `E:\Bavovna\Drone\Thrust_Stand_HAW` @ **9473c68** (read-only, D-02): `03_SW/src/thrust_stand/{core,io}`, `03_SW/docs/SW_design.md` (cited "TS-SWD §x") |
| Status of code | **No application code yet** (P1). `03_SW/pyproject.toml` is a design artefact (§1). |

Conventions: requirement IDs in **bold** where a design element implements them; code will carry
`# Implements: <ID>` tags. "B" = backend (this document), "D" = GUI (`SW_design_GUI.md`). Units in the
backend API are SW units (mm, mm/s, mm/s², N, s); wire units (µm, µm/s, µm/s², µs, raw counts) exist only
inside `io.protocol` and `core.device` (SYS-003).

---

## 0. Key design decisions (summary)

| # | Decision | Why / requirement |
|---|---|---|
| KD-01 | Strict layering `calc` ← `io` ← `core` ← `gui`; `core`, `io`, `calc` never import PySide6/shiboken6/pyqtgraph (AST + subprocess import test). | SW-PLT-002, CONV |
| KD-02 | Seven backend threads with single responsibilities: Reader, Pipeline, Supervisor, Worker, Operation runner, Recorder, Hotkey (§3). Only the Pipeline writes data structures; only the writer lock serialises the port. | NFR-001/002/004, R3 §5.4 |
| KD-03 | **Priority TX path**: STOP, HALT, HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR are written directly under the frame-writer lock, bypassing lanes, queues, token bucket and outstanding-command limits; the lock is held for one frame at a time (≤ 3 ms). | IF-011, NFR-002/003, SW-STOP-001/002 |
| KD-04 | **Motion epoch**: every STOP/HALT/stop-event from the FW increments an epoch; queued, pending or retried motion commands of an older epoch are dropped. Motion commands are **never auto-retried**; their outcome after a timeout is resolved by GET_STATUS. Only idempotent non-motion commands are retried (new SEQ, newest payload). | IF-005, R3 §1.7 P4/P11 |
| KD-05 | SW limits are evaluated **in the Pipeline thread on every DATA frame**; a trip writes STOP before anything is published ("act first, publish after"). | SAF-SW-001, R3 §5.4 SWD-M1-04 |
| KD-06 | Motion commands are allowed only while the stream runs and is fresh, the FW load thresholds are verified for the active calibration + tare, and every enabled SW limit has a valid input (pure gate functions, §5.6). | SAF-SW-001/002, SW-SEQ-005 |
| KD-07 | **Absolute targets only** on the wire; the `MotionController` keeps the *commanded target* accumulator (±0.1/1/10 mm add to it) and a latest-wins pending target while the FW is busy. | SW-MAN-001..003, IF-009, D-23 |
| KD-08 | Calibration, tare and sequencer are **Qt-free state machines** with an immutable state snapshot + events; the GUI wizards only render the snapshot and call engine methods. | SW-CAL-001, SW-TARE-001, PO-SW-8.WIZ |
| KD-09 | **Device time** (unwrapped `t_us`) is the only time base for settle/capture windows, VALID boundaries, steady-state extraction and derived rates; host time is used only for timeouts, UI and link supervision. | R4 §8.5, §9, SW-SEQ-003/004 |
| KD-10 | Recordings keep raw counts, K, tare_raw and every event, so forces and the report can be recomputed offline with another calibration/tare. | SW-REP-003, R4 §7 |
| KD-11 | Minimal runtime dependencies: PySide6-Essentials, pyqtgraph, numpy, pyserial (+ stdlib). No jsonschema/Jinja2/matplotlib/platformdirs: hand-written schema validators, HTML report with inline SVG figures, `%APPDATA%` paths via `os.environ`. | SW-PLT-001 |
| KD-12 | In-process simulator (`io.sim`) uses the **same** codec and the generated parameter dictionary; behavioural equality with the FW is checked by differential tests against the Integrator's FW host twin over `tcp://`. | SYS-008, D-07 |
| KD-13 | Tare is **session-only** (valid for the connected board UID while the application runs, never loaded from a file); calibrations are persisted with an active copy loaded at start. | SW-TARE-002, SW-CAL-009, safety (offsets drift) |
| KD-14 | Heartbeat PING after **150 ms** of TX idle (SRS bound: gap ≤ 250 ms), gated by Reader/Pipeline liveness, so a hung backend lets the FW link watchdog trip. | SAF-SW-003, R3 §5.4 SWD-M1-03 |

---

## 1. Technology stack and packaging

- Python ≥ 3.11 (3.14 on the development PC, project `.venv`), type hints everywhere, `from __future__ import annotations`.
- Runtime: `PySide6-Essentials` (GUI only), `pyqtgraph` (GUI only), `numpy` (pipeline, ring buffer, calc), `pyserial` (transport).
- Test: `pytest`, `pytest-qt` (GUI, offscreen), `pytest-cov`, `pytest-randomly` (tests must be order-independent; seed printed and reproducible with `-p randomly -p "randomly_seed=…"`).
- `03_SW/pyproject.toml` (written with this document): package `bend_stand` (src layout), extras `test`, pytest markers `req(*ids)`, `unit`, `component`, `integration`, `gui`, `perf`, `soak`, `hil`, `slow`; coverage omits the generated `core/params_gen.py` and `gui/*` (D measures GUI coverage separately).
- Entry points: `python -m bend_stand [--sim[=scenario.json]] [--port COM7 | --port tcp://127.0.0.1:5760] [--session file]` and the script `bend-stand`. `--sim` is the default when no port is given and no COM port exists (D-06: the application never opens a COM port that the operator did not select).
- Windows timing: `core.timing.init()` calls `winmm.timeBeginPeriod(1)` (paired `timeEndPeriod`) and `sys.setswitchinterval(0.001)` at start-up (TS-SWD §3.1 "Timing primitives").

## 2. Package layout (`03_SW/src/bend_stand/`)

```
bend_stand/
  __init__.py            version
  __main__.py            argument parsing → gui.app.main(...) or headless (owner: see finding F-B-12)
  calc/                  (B) pure functions, numpy/stdlib only, no I/O, no threads, no clock — §18
    units.py             G0, n_to_kgf, kgf_to_n, mm↔µm, FS constants (SRS §2)
    rounding.py          round_half_away(x) (SYS-003)
    motion.py            um_to_steps, steps_to_um, plan_trapezoid, move_duration_s, stop_distance_mm,
                         ramp_periods (reference for simulator / FW vector cross-check)
    stats.py             robust_window_stats (MAD k=5), se_ar1, drift_slope, window_acceptance
    loadcal.py           load_calibration (OLS, NL_span, status), point_acceptance, low_span, fw_raw_limits
    travelcal.py         travel_cal_step1, travel_cal_step2, travel_plausibility
    tare.py              force_n, tare_acceptance, tare_offset_warning
    limits.py            evaluate_force_limit, evaluate_travel_limit, limit_margin_warning (SAF-SW-006)
    timebase.py          unwrap_us, plausible_wraps, frame_gaps (seq u16 + Δt rule)
    derived.py           speed, force_rate, stiffness_ols, stiffness_tangent, stiffness_secant, work_trapz,
                         running_peak, break_detect, rolling_std, sample_rate, bend3p_* (R4 §10)
    steady.py            steady_state (R4 §8.3, SW-REP-002)
    trim.py              approach_band, force_to_raw_stop, k_est_from_approach, trim_step (R4 §8.4)
    path.py              planned_path (SW-SCH-001)
  io/                    (B) bytes, ports, protocol, simulator — no application state
    crc.py               crc16_ccitt_false (variant per ICD, pending)
    framing.py           FrameEncoder, FrameDecoder (hunt / LEN / CRC drop-one / inter-byte timeout)
    protocol.py          command/response/async type constants, payload builders, INFO/STATUS/PARAM_ENTRY/
                         EVENT codecs, DATA numpy dtype — all from the ICD (hand-written, vector-tested)
    transport.py         Transport ABC, SerialTransport, VirtualTransportPair, TcpTransport, transport_factory
    reader.py            ReaderThread (bytes → frames → dispatch)
    ports.py             list ST-LINK VCPs (VID 0x0483) first; never opens a port
    win_hotkey.py        GlobalHaltHotkey (Pause, Ctrl+Break; RegisterHotKey / WH_KEYBOARD_LL fallback)
    sim/                 in-process FW simulator (§12)
      board.py           SimBoard: protocol endpoint + command dispatch (ICD check order) + NVM store
      fw_logic.py        FW state machine: enable, homing, motion, jog dead-man, stops/latches, watchdogs
      models.py          DriverModel, SwitchModel, ButtonModel, Hx711Model, SpecimenModel, LinkModel
      scenario.py        SimScenario (JSON), world-control API, fault injector
      clock.py           RealTimeSimClock, LockstepSimClock
  core/                  (B) application logic, no Qt
    params_gen.py        GENERATED from params.yaml (Integrator) — never edited
    params.py            ParamStore, typed values, local range + hard-rule checks, write plan, board-config file
    model.py             frozen dataclasses/enums: DeviceInfo, BoardStatus, StatusFlags, Latches, LinkState,
                         Compat, MoveDone, VerifyItem/VerifyReport, Indicators
    errors.py            exception hierarchy (§14)
    clock.py             Clock protocol (monotonic_ns, wall), FakeClock
    timing.py            timeBeginPeriod, Ticker (absolute deadlines)
    observers.py         ObserverList (weak bound methods), ReleasingFuture, AsyncCall
    events.py            EventBus (topics, bounded history 10 000), EventRecord, SW event codes
    liveness.py          LivenessMonitor (per-thread beats, excepthooks)
    link.py              FrameWriter, CommandChannel, PrioritySender, StopConfirmer, LinkSupervisor
    linkstats.py         PC + FW counters, deltas mod 2³²
    device.py            Device (wire-level API, §5.1)
    motion.py            MotionController (absolute targets, pending target, jog session, x_zero) §5.4
    gates.py             pure gate functions (motion, home, enable/disable, clear, tare, sequence start) §5.6
    operations.py        OperationRegistry: who owns motion; terminate_all(reason) §3.5
    samples.py           SampleBatch (decoded DATA + derived columns), channel keys
    channels.py          ChannelSpec registry, availability + reason (SW-RT-002)
    pipeline.py          SamplePipeline + stages (§7)
    ringbuffer.py        ColumnRingBuffer + min/max pyramid (§7.5)
    dataview.py          DataView: thread-safe snapshot API for plots/readouts (§15)
    safety.py            SafetySupervisor, LimitConfig, ThresholdManager (§6)
    capture.py           CaptureHub (device-time windows over pipeline samples) §9.2
    tare.py              TareEngine §9.5
    calibration/         travel.py (TravelCalEngine), load.py (LoadCalEngine), store.py (CalibrationStore)
    sequencer/           model.py, plan.py, generators.py, executor.py, loadstep.py, seqfile.py (§10)
    recorder.py          Recorder (CSV + JSON sidecar + optional raw frame dump), SampleTaker (§8)
    report.py            WindowAccumulator, ReportBuilder (JSON + HTML/SVG), offline CLI (§11)
    metadata.py          TestMarks, presets I/O (SW-META)
    session.py           SessionSettings (limits, FW level, k_est, pull_dir, trim, windows, units, geometry)
    paths.py             app data dirs (%APPDATA%\BirdBendStand), recordings root
    schema.py            hand-written JSON field specs/validators + atomic write helper (§13)
    api.py               typing.Protocol surface of the Backend for GUI fakes (§15)
    backend.py           Backend facade (§15) — the only object the GUI constructs
  gui/                   (D) PySide6 — SW_design_GUI.md
```

Generated module (`core/params_gen.py`, Integrator): the backend relies on `PARAMS`, `BY_ID`, `BY_KEY`,
`GROUPS`, `PARAM_DICT_HASH`, `PARAM_DICT_VERSION` and `ParamMeta` with `id, key, type, unit, min, max,
default, enum (code→NAME), group, label, decimals, nvm, moving_ok, advanced, srs, pack(), unpack(),
in_range()` (pattern of TS-SWD §2). `moving_ok` (= "settable while moving", FW-CFG-001) is required by the
write plan (finding F-B-19). Cross-parameter hard rules (SRS §5.1: `soft_min < soft_max`,
`load_raw_min < load_raw_max`, pulse timing vs step rate) are implemented in `core.params.check_hard_rules`
and tested against the ICD/vectors.

---

## 3. Threading model and data flow

### 3.1 Threads

| Thread | Module | Work | Trigger / period | Liveness beat |
|---|---|---|---|---|
| **GUI (main)** | D | Qt event loop; polls snapshots (§15) every 33–50 ms; calls `Backend.stop()`/`halt()` **directly** (non-blocking) and everything else through `*_async` | event-driven | GUI timer tick (`backend.liveness.beat("gui")`) |
| **Reader** | `io.reader` | `transport.read()` (≤ 4096 B, 2 ms timeout, configured once), `FrameDecoder.feed()`; responses → `CommandChannel.on_response()` (resolves futures); DATA/EVENT/LOG → bounded queue (4096) to the Pipeline **in arrival order**. Never writes to the port. | continuous | every loop |
| **Pipeline** | `core.pipeline` | drains the queue (batch closes when the queue is empty or after 8 frames), decodes, unwraps time, detects gaps, scales, derives, annotates, **evaluates safety per sample**, feeds sinks; dispatches FW EVENTs (in order with DATA) | queue-driven, 10 ms idle wake-up | every batch / wake-up |
| **Supervisor** | `core.link.LinkSupervisor` | 5 ms `Ticker`: heartbeat PING, link state, DATA-loss-while-moving reaction, liveness evaluation, `StopConfirmer` repeats (STOP/HALT), jog refresh, reconnect trigger | 5 ms | it is the monitor (its death stops the heartbeat → FW watchdog) |
| **Worker** | `core.observers.AsyncCall` executor (1 thread) | executes `*_async` calls (connect, read/write params, SAVE/LOAD/DEFAULTS, threshold write+verify, file I/O for reports) | queue | per job |
| **Operation runner** | `core.operations` (1 thread, started per operation) | runs the active long operation: sequence executor, travel-calibration moves, load-step approach + trim, homing wait; 20 ms `Ticker` + event waits | while an operation runs | every tick (stall > 300 ms while moving → STOP) |
| **Recorder** | `core.recorder` | formats and writes CSV rows / raw dump from its bounded queue, flush 1 s | queue | every write / 250 ms |
| **Hotkey** | `io.win_hotkey` | Win32 message loop; Pause / Ctrl+Break → `Backend.halt("hotkey")` directly | message-driven | answers a 250 ms supervisor ping |

Capture (tare, calibration points, take-sample), the safety supervisor, the window accumulator and the
sequencer's per-sample guards are **pipeline sinks** (they run in the Pipeline thread and never block).

### 3.2 Data flow

```
 port ─► Reader ─frames─┬─► responses ──► CommandChannel (future per (type|0x80, SEQ))
                        └─► DATA / EVENT / LOG (arrival order) ─► queue ─► Pipeline
 Pipeline per batch: decode ─► time unwrap ─► gaps ─► classify ─► scale (K, tare, x_zero) ─► derived
     ─► annotate (operation/step/phase) ─► SAFETY (per sample; may STOP) ─► EVENT dispatch
     ─► sinks: RingBuffer │ Recorder queue │ CaptureHub │ WindowAccumulator │ operation guards │ observers
 GUI timer ─► DataView.snapshot(...) / Backend.status() ─► pyqtgraph / readouts / indicators
 GUI / engines ─► MotionController / Device ─► CommandChannel (lanes) ─► FrameWriter ─► port
 GUI STOP / hotkey / safety / supervisor ─► PrioritySender ─► FrameWriter (lock, next frame slot) ─► port
```

### 3.3 Thread-safety and object-lifetime rules (carried over from TS-SWD §3.1, SWD-PM3-07)

1. The Reader never blocks on anything except the transport and never writes (TS SWD-M1-06).
2. Only the Pipeline writes the ring buffer, the latest-sample record and derived state; readers copy under a short lock.
3. `Device` methods are callable from any thread; frames are serialised by `FrameWriter._lock`.
4. Observers (`EventBus.subscribe`, `SamplePipeline.add_sink`, engine observers) run on backend threads, must return in < 1 ms, must not block or call blocking `Device` methods. Registrations hold bound methods **weakly**; the backend never owns a GUI object (TS rule 1).
5. Futures release callbacks once run (`ReleasingFuture`), `AsyncCall` drops `fn/args` before resolving (TS rule 2).
6. No reference cycles on backend paths; the backend never calls `gc.*` (GUI GC policy is D's, TS-SWD §13.10).
7. Events are published **after** locks are released and **after** the safety action they report (act first, publish after).
8. Integer time (ns host, µs device) for all deadlines, debounce and windows; floats only for display/CSV.

### 3.4 Timing primitives

`core.timing.Ticker(period_ns)` uses absolute deadlines on `time.monotonic_ns()`; overruns skip and are
counted. Start-up measures `sleep(0.001)` granularity (p95 > 3 ms → warning event, value in the recording
sidecar).

### 3.5 Operations and motion ownership

`OperationRegistry` holds at most one **motion-owning operation**: `MANUAL` (default, no runner),
`SEQUENCE`, `TRAVEL_CAL`, `HOMING`; plus non-motion captures (`TARE`, `LOAD_CAL_POINT`, `SAMPLE`) that may run
concurrently only where the SRS allows (tare refused during a sequence capture window, SW-TARE-003).
Motion commands carry an owner token; the gate refuses a command from a non-owner (manual buttons are
disabled while a sequence or wizard owns motion). `terminate_all(reason)` is called by STOP, HALT, safety
trips, FW-reported HALT/ESTOP/stop events, link loss and liveness faults: it swaps every running operation to
`ABORTED`/`TERMINATED` synchronously (atomic state swap, no waiting), so no further command is issued by it;
the runner thread then cleans up (SW-STOP-001/003).

---

## 4. Transport and protocol client

### 4.1 Transport (`io.transport`)

```python
class Transport(ABC):
    name: str
    def open(self) -> None; def close(self) -> None; def is_open(self) -> bool
    def read(self, max_bytes: int) -> bytes        # returns after ≤ read timeout (2 ms), b"" on timeout
    def write(self, data: bytes) -> None           # whole frame; raises TransportError
    last_write_ns: int                              # for perf tests (NFR-002/003)
def transport_factory(endpoint: str) -> Transport
    # "COM7" → SerialTransport(921600, 8N1, no flow control, timeout set once)       (IF-002)
    # "tcp://127.0.0.1:5760" → TcpTransport (FW host twin, Integrator)
    # "sim" | "sim:<scenario.json>" → VirtualTransportPair host end + SimBoard (§12)
```

- `SerialTransport`: port settings and the read timeout are configured **once** (TS SWD-M1-01: changing the
  timeout per read hammered the ST-LINK VCP with SetCommState). Pending bytes at open are fed to the parser,
  not flushed blindly (R3 §1.7 P13).
- `VirtualTransportPair`: in-memory byte pipes with optional baud pacing (92 160 B/s), latency, unplug/replug
  and a write-timestamp log (`wire_log`) used by NFR-002/003 perf tests and IF-011 tests.
- D-06: no code path opens a COM port by itself; port lists come from `io.ports` (enumeration only).

### 4.2 Framing and codec (`io.framing`, `io.protocol`)

- Frame structure, CRC variant, max payload and receiver resync rules come from the ICD (IF-003, IF-004;
  proposal R3 §1.6: `A5 5A | TYPE | SEQ | LEN u16 | PAYLOAD | CRC16`). `FrameDecoder` = TS `io/framing.py`
  state machine: hunt for sync, LEN > max → drop one byte, CRC error → drop one byte, inter-byte timeout
  (measured only after an empty read, TS SWD-M2-01) → drop one byte; counters `crc_errors`, `len_errors`,
  `timeout_drops`, `unknown_type`, `bad_payload`.
- `io.protocol` is hand-written from the ICD and verified against the shared vector file
  (`00_System/tools/vectors/*.json`, produced by `ref_codec.py`; the vectors are the oracle, R3 §1.7 P14):
  request builders per command, response decoders (INFO, STATUS, PARAM_ENTRY pages, SET_VALID `t_us`,
  MOVE_DONE), EVENT decoder (unknown codes tolerated and logged), NACK split (status + detail), and
  `DATA_DTYPE` (numpy structured, little-endian, packed) for `np.frombuffer` batch decoding (IF-006).
- OK responses longer than known are accepted (trailing bytes ignored); unknown status codes are logged
  (minor-version tolerance, IF-008).

### 4.3 Reader (`io.reader`)

TS `io/reader.py` as-is: ABOVE_NORMAL priority thread, reads ≤ 4096 B per call, stamps the host time
**before** the read, applies the inter-byte timeout only after an empty read, dispatches responses to the
`CommandChannel` and async frames to the Pipeline queue, then publishes `line_ns` (the instant up to which
the line was observed; TS SWD-M2-07). A full Pipeline queue is a liveness fault (never a silent drop):
counter `async_overflow` + event + the motion reaction of §6.4.

### 4.4 Command channel (`core.link.CommandChannel`)

```python
def request(cmd: Cmd, payload: bytes, *, lane: Lane, timeout_s: float | None = None,
            retry: RetryClass | None = None, epoch: int | None = None) -> ReleasingFuture[Response]
```

- **SEQ**: incrementing u8, skipping values still pending; the response echoes it (FW-CMD-001); late
  responses of abandoned SEQs are counted and dropped; stale bytes at connect cannot match because the first
  SEQ is randomised (TS SWD-M2-06).
- **Lanes** (≤ 4 outstanding in total; values pending ICD):

  | Lane | Commands | Rule |
  |---|---|---|
  | priority (exempt, §4.5) | STOP, HALT, HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR | never waits, outside the outstanding limit and the token bucket |
  | SAFETY | PING, SET_VALID(0) | 1 reserved slot |
  | CONTROL | MOVE_ABS, MOVE_UNTIL_LOAD, JOG, HOME, ENABLE, DISABLE, SET_VALID(1), SET_PARAM of `safety.*` thresholds | 1 reserved slot |
  | GENERAL | GET_*, SET_PARAM, SAVE/LOAD/DEFAULTS, STREAM_*, REBOOT | ≤ 2 |

- **Retry classes** (IF-005; counts/timeouts from the ICD, defaults below until then):

  | Class | Commands | Behaviour |
  |---|---|---|
  | `RETRY` | PING, GET_INFO, GET_STATUS, GET_PARAM, GET_ALL_PARAMS page, SET_PARAM, STREAM_START/STOP, SET_VALID | ≤ 2 retries after a 100 ms timeout, each with a **new SEQ** and the **newest** value (latest-wins slots for SET_VALID and threshold SET_PARAMs) |
  | `CONFIRM` | STOP, HALT | priority path; repeated every 50 ms until confirmed (STOP: ACK or DATA/STATUS `MOVING = 0`; HALT: ACK or HALT flag in DATA/GET_STATUS), ≤ 20 tries in 1 s, also with the stream off (SW-STOP-002) |
  | `ONCE_PRIORITY` | HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR | priority path, ≤ 2 retries (idempotent clears), NACK detail shown verbatim |
  | `VERIFY` | MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0, ENABLE, DISABLE, SAVE, LOAD, DEFAULTS, REBOOT | **never retried**. On timeout: GET_STATUS (motion state, target, latches, CFG_DIRTY) decides "accepted / not accepted"; the caller gets `CommandOutcomeUnknown` only if GET_STATUS also fails. Rationale: a retried MOVE after a PAUSE-button stop would un-pause the axis (SAF-FW-023 "PAUSED until the next accepted motion command"); a relative semantics never exists on the wire anyway. |

  JOG refreshes (§5.4) are a stream of new commands (every 100 ms, newest speed); a lost one is covered by
  the next refresh and by the FW dead-man (SAF-FW-016).
- **Motion epoch** (KD-04): `CommandChannel.motion_epoch` increments on every STOP/HALT sent and on every
  FW stop indication (EVENT stop/fault/latch, `MOVING` 1→0 without MOVE_DONE, PAUSED set). A motion request
  carries the epoch at creation; the writer drops it if the epoch changed before it was written (event
  `MOTION_CMD_DROPPED`). JOG sessions and pending targets are bound to the epoch.
- **Timeouts** (ICD pending): default 100 ms; GET_INFO during connect 200 ms; SAVE/LOAD/DEFAULTS 3 s
  (NFR-008: FW ≤ 2.5 s) plus the wire time of the TX backlog (TS M3-12 lesson).
- **Token bucket** ≤ 100 frames/s for GENERAL/CONTROL (protects the FW command path); SAFETY and priority
  frames are exempt.

### 4.5 Priority TX path (`core.link.PrioritySender`) — IF-011, NFR-002, NFR-003

- `send_now(cmd, payload)` builds the frame and acquires `FrameWriter._lock` with priority: the writer lock is
  a two-level lock (a "priority waiting" flag makes normal writers yield before taking the lock), so the
  longest wait is **one frame already being written** (≤ 264 B ≈ 2.9 ms at 921 600 Bd; typ. < 0.5 ms because
  the OS buffers the write). No queue, lane, token bucket or outstanding limit applies.
- The call never raises to the caller: transport errors are recorded in the returned `StopResult(sent,
  t_write_ns, error)` and published; the GUI banner shows the real send result (TS SWD-M1-05).
- STOP/HALT also: (1) increment the motion epoch, (2) cancel the jog session and the pending target,
  (3) call `OperationRegistry.terminate_all(reason)`, (4) arm the `StopConfirmer` (CONFIRM class), (5)
  publish `stop.issued` — in this order, all within the caller's thread, no waiting.
- Callers: GUI STOP (GUI thread, SW-STOP-001), hotkey thread (SW-STOP-002), Pipeline (SAF-SW-001 trips,
  sequence guards), Supervisor (SAF-SW-003 link loss, liveness), Operation runner (abort).

### 4.6 Heartbeat, link states and liveness (`core.link.LinkSupervisor`, `core.liveness`)

- **Heartbeat** (SAF-SW-003): every 5 ms tick, if `now − last_tx ≥ 150 ms` and the liveness gate is open,
  send PING (SAFETY lane). Worst-case gap 150 + 5 + jitter ≪ 250 ms (95 ms margin for GIL/GC pauses). Any
  sent frame counts. Liveness gate: Reader beat < 200 ms, Pipeline beat < 300 ms, and while streaming the last
  DATA < 500 ms. A dead or hung backend therefore stops the heartbeat and the FW link watchdog
  (SAF-FW-015, 1 s) performs a controlled stop.
- **DATA loss while moving** (SAF-SW-003): moving (latest `MOVING` flag, or a motion command in flight, or a
  jog session) and no DATA for > 500 ms → priority STOP, `terminate_all("LINK_LOST")`, link state `LOST`
  indicator "LINK LOST". STREAM_STOP is refused by the backend while moving or while an operation runs
  (PC limits need data).
- **Link states** (`LinkState`): `DISCONNECTED → CONNECTING → CONNECTED`; `DEGRADED` after 3 consecutive
  command timeouts or 300 ms without any received frame while streaming (recovery probe GET_STATUS every 1 s,
  TS SWD-M3R2-02); `LOST` after 1 s without frames or on a serial exception; reconnect attempts every 1 s
  (same endpoint) run the connect sequence (§5.2) and compare the board UID. **The SW never re-enables,
  re-homes, re-moves or restarts a sequence automatically after a reconnect.**
- **Liveness faults** (thread beat missing: Reader > 100 ms, Pipeline > 200 ms or DATA received but not
  evaluated > 200 ms, Operation runner > 300 ms while moving, GUI > 2 s; uncaught exception in any thread via
  `threading.excepthook`/`sys.excepthook`): while moving → priority STOP + `terminate_all` + event; idle →
  warning event only. The hotkey thread missing its 250 ms ping → warning "Pause/Break key unavailable" (the
  physical STOP button and E-stop remain the path of record).

---

## 5. Device API

### 5.1 `core.device.Device` (wire-level, thread-safe)

```python
class Device:                                   # Implements: SW-PLT-003, SW-CFG-*, IF-005, IF-008
    def __init__(self, transport_factory: Callable[[str], Transport], events: EventBus,
                 clock: Clock = MONOTONIC) -> None
    # connection
    def connect(self, endpoint: str, *, stream: bool = True) -> DeviceInfo      # §5.2
    def disconnect(self) -> None                 # STOP if moving, STREAM_STOP, close (no DISABLE)
    state: LinkState; compat: Compat; info: DeviceInfo | None
    def get_info(self) -> DeviceInfo; def get_status(self) -> BoardStatus; def ping(self) -> float
    def reboot(self) -> None                     # VERIFY class; reconnect after EVENT BOOT or 2 s
    # parameters (SW-CFG)
    def read_all_params(self) -> dict[str, ParamValue]              # paged GET_ALL_PARAMS (FW-CFG-002)
    def get_param(self, key: str) -> ParamValue
    def set_param(self, key: str, value: int | float | bool | str) -> ParamValue   # value as stored
    def write_and_verify(self, edits: Mapping[str, Any], *, progress=None) -> VerifyReport   # §5.3
    def save_params(self) -> None; def load_params(self) -> dict; def default_params(self) -> dict
    # streaming / validity
    def stream_start(self) -> None; def stream_stop(self) -> None   # stop refused while moving / op running
    def set_valid(self, flag: bool) -> int       # returns device t_us from which it applies (FW-CMD-002)
    # motion, wire units, used only by MotionController / engines (gates applied there)
    def enable(self) -> None; def disable(self) -> None
    def home(self, *, load_confirmed: bool) -> None
    def move_abs_um(self, target_um: int, v_um_s: int, a_um_s2: int, *, epoch: int) -> None
    def move_until_load(self, direction: int, v_um_s: int, a_um_s2: int, raw_stop: int,
                        bound_um: int, *, epoch: int) -> None
    def jog_um_s(self, v_um_s: int, *, epoch: int) -> None
    # stops and clears (priority path, never raise)
    def stop(self, mode: StopMode = StopMode.IMMEDIATE, reason: str = "user") -> StopResult
    def halt(self, source: str) -> StopResult
    def halt_clear(self) -> ClearResult; def estop_clear(self) -> ClearResult; def fault_clear(self) -> ClearResult
    # every blocking method has `<name>_async(...) -> ReleasingFuture` (Worker thread)
```

`ParamValue`, `DeviceInfo`, `BoardStatus` (FW-CMD-004 counters, latches, inputs, measured AFE rate, reset
cause …), `StatusFlags` (decoded DATA status bits, FW-STR-003) are frozen dataclasses in `core.model`;
their fields follow the ICD (pending). Compat flags: `OK | MINOR_DIFF | MAJOR_MISMATCH | PAYLOAD_MISMATCH |
PARAM_HASH_MISMATCH` (IF-008).

### 5.2 Connect sequence (SW-PLT-003, IF-008)

1. Open the transport, start Reader/Pipeline/Supervisor, randomise the first SEQ.
2. GET_INFO (200 ms timeout) repeated for ≤ 3 s; heartbeat starts with the first answer. No answer → "no board".
3. Version check: `proto_major` ≠ SW major or `payload_version` ≠ expected → `MAJOR_MISMATCH`/`PAYLOAD_MISMATCH`:
   **read-only state** (monitoring, streaming, config read allowed; every motion, clear-and-enable and config
   write refused locally). `param_dict_hash` ≠ `PARAM_DICT_HASH` → `PARAM_HASH_MISMATCH`: configuration
   read-only + warning (`set_param`/`write_and_verify` raise `ConfigReadOnly`).
4. GET_STATUS → latches, inputs, reset cause, homed/enabled, CFG_DIRTY (banner "unsaved changes in RAM" or
   "board runs on defaults" per the ICD boot events; nothing saved automatically).
5. GET_ALL_PARAMS (all pages) → `ParamStore`; AFE configuration compared with the active calibration's `afe`
   block → "calibration invalid" warning on mismatch (SW-CAL-009).
6. STREAM_START (default on; SW-ACQ-001 toggle later). Time unwrap starts a new epoch (§7.3).
7. `ThresholdManager.on_connect()` (SAF-SW-002, §6.3) — motion stays disabled until verified.
8. `MotionController.resync()` from the first DATA frame (commanded target = reported position).
Link statistics (frames OK, lost frames FW/link, CRC/len/timeout errors, late responses, command timeouts,
FW counters from GET_STATUS) are available from step 2 (`Backend.status().link`).

### 5.3 Configuration read / write / verify (SW-CFG-001..004)

- `ParamStore` holds board values, defaults and metadata from `params_gen` (typed fields, units, ranges, enum
  names for D's generated form, SW-CFG-001).
- `write_and_verify(edits)` (TS-SWD §4.1 procedure, simplified, no `fw_owned`): (0) refuse under
  `PARAM_HASH_MISMATCH`; (1) build the target (board values ⊕ edits), check `in_range` and hard rules
  locally — any violation aborts before the first write (SW-CFG-003); (2) write changed parameters in an
  order that keeps hard rules true after each single SET (pairs `soft_min/max`, `load_raw_min/max`: write
  the bound that moves *outward* first); parameters with `moving_ok = false` while moving → `BUSY` without
  sending; (3) `GET_ALL_PARAMS` read-back; per item `OK | REJECTED(status, detail) | MISMATCH | BUSY |
  TIMEOUT | NOT_ATTEMPTED`; (4) GET_STATUS → CFG_DIRTY. Never saves to NVM implicitly.
- Board-config file (`*.bbboard.json`, §13.1): `save_board_config(path, values)`; `load_board_config(path)
  -> BoardConfigFile(values, unknown_keys, missing_keys, out_of_range, hash_mismatch)` — fills edit fields
  only (SW-CFG-002).
- NVM buttons: `save_params` (then GET_STATUS: CFG_DIRTY must be 0), `load_params` / `default_params`
  (+ re-read, then `ThresholdManager.recheck()` because thresholds may have changed) (SW-CFG-004).
- `safety.load_raw_*` and `safety.zero_raw` are owned by the `ThresholdManager`; the config form shows them
  read-only (a manual edit would desynchronise SAF-SW-002) — finding F-B-21 for D.

### 5.4 Motion API (`core.motion.MotionController`) — SW-MAN-001..006, SW-LIM-001

```python
class MotionController:                    # all values in SW units; owner = OperationRegistry token
    commanded_target_mm: float | None      # last accepted absolute target (SW-MAN-002/003)
    pending_target_mm: float | None        # latest-wins target while the FW is busy
    x_zero_mm: float                       # test travel zero (PC offset, never sent)
    def move_to(self, target_mm: float, *, speed_mm_s: float | None = None,
                accel_mm_s2: float | None = None, owner: OwnerToken = MANUAL) -> MoveTicket
    def move_by(self, delta_mm: float, **kw) -> MoveTicket   # target = commanded_target + delta
    def jog_start(self, direction: int, speed_mm_s: float) -> None   # refresh every 100 ms (Supervisor)
    def jog_update(self, speed_mm_s: float) -> None; def jog_stop(self) -> None   # JOG 0 = controlled stop
    def move_until_load(self, ...) -> MoveTicket            # used by the load-step engine (§10.4)
    def enable(self) -> None; def disable(self, *, confirmed: bool) -> None
    def home(self, *, load_confirmed: bool = False) -> MoveTicket
    def set_test_zero(self) -> float      # x_zero = current commanded position (SW-MAN-006), event row
    def set_valid(self, flag: bool) -> int    # manual VALID toggle (SW-MAN-006)
    def limits(self) -> MotionLimits      # effective v/a caps (FW maxima, step-rate cap), SW travel range
    def resync(self) -> None              # commanded_target := reported position (after stops/faults)
```

- **Conversion**: mm → µm with `round_half_away` (SYS-003) at the `Device` boundary only; speeds/accels
  are checked against `motion.v_max_um_s`, `motion.a_max_um_s2` and the step-rate cap
  `max_step_rate_hz·1000/steps_per_mm` (FW-MOT-009) → `ValueError` with the allowed maximum (SW-MAN-005);
  `None` = session default (≤ caps).
- **Targets** must lie inside the enabled SW travel limits (SW-LIM-001) and the FW soft limits; refused
  locally with a message, nothing sent.
- **Commanded-target accumulator**: `move_by` adds to `commanded_target_mm`, never to the live position
  (SW-MAN-003). If the FW is idle the MOVE_ABS is sent at once; if a manual move of the same owner is still
  running, the new target becomes `pending_target_mm` (latest wins) and is sent on MOVE_DONE (reason
  "reached"); any other MOVE_DONE reason or a stop discards it. (Rapid clicks 11/12/13 therefore send 11 and
  13; with each move completed between clicks the wire sees 11, 12, 13 — see F-B-09.)
- **Resync**: after any stop, fault, latch clear, reconnect or MOVE_DONE other than "reached",
  `commanded_target_mm` := reported position at standstill (setpoint distance from the first DATA frame with
  `MOVING = 0`), so the next ±1 mm starts where the axis stopped.
- **Slider** (SW-MAN-001): GUI sends nothing while dragging; on release it calls `move_to(value)` once.
- **Jog** (SW-MAN-004): `jog_start` sends JOG(v) immediately (CONTROL lane); the Supervisor refreshes it every
  100 ms (newest speed) while the session is active, the motion epoch is unchanged, the GUI beat is younger
  than 300 ms and the gate stays open; `jog_stop` (button release, focus loss — D calls it) sends JOG(0).
  Un-homed jog is limited to `motion.v_unhomed_um_s` (FW-MOT-005). Travel SW limits during jog: §6.1.
- **MoveTicket**: a future resolved by EVENT MOVE_DONE (reason, final position µm → mm, `t_us`) or cancelled
  with the stop/latch/link reason; engines wait on it, the GUI may attach a done-callback via the bridge.

### 5.5 Stop, halt, pause and clear (SW-STOP-001..004, SAF-SW-004)

| Backend call | Wire (ICD pending) | Side effects |
|---|---|---|
| `Backend.stop(source)` | STOP(immediate), CONFIRM class | epoch++, jog/pending cancelled, `terminate_all` (sequence/wizard/tare terminated), event; not latched (D-26 (2)) |
| `Backend.halt(source)` (Pause/Break key, abort) | HALT, CONFIRM class (≤ 20 tries / 1 s until ACK or HALT flag) | as STOP; HALT latched in FW (SW-STOP-002) |
| `Backend.pause(source)` (GUI Pause) | STOP(controlled) — or a PAUSE command if the ICD defines one (F-B-03) | sequence → PAUSED (state kept); manual mode: controlled stop only (SW-STOP-004) |
| `Backend.resume()` | per step: re-issue the absolute target or re-run approach + trim | gate re-checked; capture window restarted |
| `Backend.clear_stop()` | HALT_CLEAR | only by explicit GUI action; refused locally while the physical STOP button is still active (NACK detail shown); **no motion afterwards** (SW-STOP-003) |
| `Backend.estop_clear(confirmed)` | ESTOP_CLEAR | requires the confirmation token "button released, re-home needed" (SAF-SW-004); afterwards driver disabled + not homed (SAF-FW-006) |
| `Backend.fault_clear()` | FAULT_CLEAR | NACK detail names the remaining cause (FW-CMD-003) |

FW-originated stops (EVENT/flags from the physical STOP/BREAK button, E-stop, limit, load limit, AFE fault,
link watchdog, PAUSE button) are handled in the Pipeline: HALT/ESTOP → `terminate_all` within the frame that
carries it (SW-STOP-003); PAUSE EVENT → `sequencer.pause(source="button")`; RESUME_REQUEST EVENT →
`sequencer.resume()` if PAUSED and the gate is open (SW-STOP-004); every stop → epoch++, `resync()`.

### 5.6 Gates (`core.gates`, pure functions of a `GateSnapshot`)

`GateSnapshot` = link state, compat, latest DATA flags (age), GET_STATUS latches/inputs, ParamStore,
threshold state, SW-limit input validity, operation owner, calibration/tare state, recording state.
Each gate returns `GateResult(items: list[GateItem(code, severity REFUSE|CONFIRM|WARN, text, clear_hint)])`.

| Gate | REFUSE items (any) | CONFIRM / WARN |
|---|---|---|
| `motion_gate(kind, owner)` | not connected / DEGRADED / LOST; compat major/payload mismatch; stream off or DATA > 500 ms old; driver not enabled; not homed (MOVE_ABS, MOVE_UNTIL_LOAD); ESTOP/HALT/fault latched; AFE stale or saturated; thresholds not verified (SAF-SW-002); an enabled SW load limit without valid input (SAF-SW-001); owner conflict; SW-limit trip active in the commanded direction (§6.2) | WARN SAF-SW-006 margin; WARN ALM active (D-16 report only) |
| `home_gate` | as motion (homed not required, jog-level) | CONFIRM if abs(F) ≥ 5 % FS **or** load unknown (no calibration/tare) → HOME carries the confirmed flag (SAF-SW-004, SAF-FW-021) |
| `enable_gate` / `disable_gate` | ESTOP latched / input open; moving (DISABLE) | DISABLE: CONFIRM "specimen unloaded?" (SAF-SW-004) |
| `estop_clear_gate` | E-stop input still open (from STATUS) | CONFIRM "button released; driver stays disabled; re-home" |
| `tare_gate` | SW-TARE-003 list (§9.5) | WARN large offset |
| `sequence_start_gate` | SW-SEQ-005 list (§10.3) | WARN margin, LOW_SPAN, extrapolation |

Confirmation tokens: the GUI shows the CONFIRM text and calls the action with `confirmed=True`; the backend
refuses a CONFIRM-gated action without it (Enter/Space handling and STOP-in-dialog are D's, SAF-SW-004).

---
## 6. Safety supervisor (`core.safety`) — SAF-SW-001..006

### 6.1 Per-frame evaluation (SAF-SW-001)

`SafetySupervisor.process(batch)` runs in the Pipeline thread after scaling, **for every sample in time
order**. Configuration (`LimitConfig`, session file, SW-LIM-001..003; edits refused while moving):

| Limit | Fields | Default |
|---|---|---|
| Travel min / max | `travel_min_mm`, `travel_max_mm` (machine mm), each `enabled`; must lie inside the FW soft limits | disabled until set (FW soft limits always active) |
| Pull max | `pull_trip_n` (> 0), `enabled`, `warn_pct` | +100 % FS (1961.33 N), enabled, warn 90 % (A-15) |
| Push max | `push_trip_n` (< 0), `enabled`, `warn_pct` | −100 % FS, enabled, warn 90 % |
| FW load-limit level | `fw_level_n` (≤ 110 % FS = 2157.46 N, ≥ the largest enabled SW trip magnitude) | 110 % FS (SW-LIM-002) |

Rules per sample (pure functions `calc.limits.*`; the supervisor adds state and actions):

1. **Load**: with a valid input (active calibration matching the AFE config, a tare, raw state OK or
   SETTLING) `F = K·(raw − tare_raw)`; trip if `F > pull_trip_n` or `F < push_trip_n` (enabled sides). A
   **saturated** sample counts as `±∞` with sign `sign(K)·sign(rail)` (overload, never "no data").
   Warning edge when `|F| ≥ warn_pct·|trip|` (hysteresis 2 % of the trip level) → `safety.warning` event and
   indicator.
2. **Load input invalid** while a load limit is enabled (no calibration/tare, AFE-config mismatch, NO_DATA
   fallback frame, AFE stale/fault flag): if moving → STOP (reason `LOAD_INPUT_INVALID`); the motion gate
   refuses new motion (SAF-SW-001 last sentence).
3. **Travel** (enabled, axis homed): `x = setpoint_um/1000` (exact commanded position). While moving, the
   predicted position `x_pred = x + v_cmd·t_lead` (`v_cmd` from consecutive setpoints and `t_us`, `t_lead =
   75 ms` = frame period + 50 ms reaction budget + margin) crossing a limit in the direction of motion trips.
   This bounds jog overshoot (the FW jogs toward its soft limit, FW-MOT-005); see F-B-15 for an exact FW-side
   bound. A position outside a limit at standstill (limit edited) is no trip, but the gate refuses motion
   further outside.
4. **Direction-aware latch** `SwTrip(limit_id, side)`: after a trip, motion that **increases** the violation is
   refused, motion that reduces it is allowed (unloading, moving back), mirroring SAF-FW-011. A load-side
   direction uses `pull_dir` (session, +1/−1, SRS §5.2): `Δx·pull_dir > 0` increases tension. The latch clears
   automatically when the value is back inside the trip level by the hysteresis band.

### 6.2 Trip action and latency

Order (KD-05): (1) `PrioritySender.stop(IMMEDIATE, "SW_LIMIT:<id>")`, (2) `OperationRegistry.terminate_all`
(sequence/wizard motion terminated), (3) latch + indicator, (4) event `SAFETY_TRIP {limit, value, threshold,
t_us}` → recorder event row and log. Budget from reception of the violating frame (Reader stamp) to the STOP
write (`wire_log`): Reader dispatch ≤ 1 ms + Pipeline wake-up ≤ 1 ms + per-frame processing ≤ 1 ms + writer
wait ≤ 3 ms ≈ **≤ 6 ms typical**; requirement **≤ 50 ms p95** (SAF-SW-001), margin covers GIL/GC pauses
(GUI GC policy, D). Verified by the simulator perf test (100 injected violations).

### 6.3 FW load-threshold manager (`ThresholdManager`) — SAF-SW-002, D-12

- **Compute** (`calc.loadcal.fw_raw_limits`, R4 §4.4, rounding toward the tare): `(raw_min, raw_max) =
  fw_raw_limits(f_hi=+fw_level_n, f_lo=−fw_level_n, k=K, tare_raw)`; `zero_raw = round_half_away(tare_raw)`.
- **Check**: `fw_level_n ≤ 110 % FS` and ≥ the enabled SW trip magnitudes (SW-LIM-002); both raw values
  within the dictionary range (±7 151 121 counts, SAF-FW-010) — otherwise **refused**: state `INVALID`, motion
  disabled, event with the computed values (F-B-07 proposes tightening to the range cap instead).
- **Write** (CONTROL lane, idle only): order keeps `raw_min < raw_max` after each SET (new min < current max →
  min first, else max first), then `zero_raw`; **read-back** (GET_PARAM ×3) compared exactly →
  `VERIFIED(cal_id, tare_id, fw_level_n, raw_min, raw_max, zero_raw)`. NACK/timeout/mismatch → `FAILED`,
  motion disabled, event; `recheck()` retries.
- **Triggers**: connect/reconnect, FW reset detected, calibration activated, tare done, FW level edited,
  LOAD/DEFAULTS/`write_and_verify` touching `safety.*`, every `read_all_params` (consistency check).
  Calibration accept, tare and limit edits are refused while moving, so thresholds never lag a running move.
- **No calibration or no tare**: expected thresholds = dictionary defaults (±7 022 271), `zero_raw` untouched,
  state `DEFAULT_ONLY`; motion allowed only while every SW load limit is disabled (operator decision, warning
  banner); the FW 110 % FS default stays active (F-B-08).
- Motion gate item: `thresholds.state == VERIFIED` and its ids equal the active calibration + tare (or
  `DEFAULT_ONLY` with load limits disabled).

### 6.4 Link loss and liveness (SAF-SW-003) — see §4.6

DATA gap > 500 ms while moving → priority STOP + `terminate_all("LINK_LOST")` + indicator LINK LOST;
heartbeat gated by liveness; FW watchdog (1 s) is the backstop when the PC side is dead.

### 6.5 Confirmations and indicators (SAF-SW-004/005, backend part)

- Confirmation-gated actions (§5.6): HOME with abs(F) ≥ 5 % FS or unknown load, DISABLE, ESTOP_CLEAR.
- `Indicators` (frozen dataclass, rebuilt in the Pipeline on every DATA/EVENT and on each GET_STATUS):
  `estop`, `halt` + `halt_source` (PC/key/button), `paused`, `limit_start`, `limit_end`, `limit_wiring`,
  `load_limit`, `afe_stale`, `afe_saturated`, `afe_settling`, `afe_rate_mismatch` (+ measured rate),
  `link_wdg`, `link_state`, `homed`, `enabled`, `alm`, `pend`, `stop_button`, `pause_button`, `pos_uncertain`,
  `overrun`, `drv_pwr` (if the ICD adds it, R5 §8), `sw_trip`, `thresholds_state`, `cfg_dirty`. Each latched
  item carries `clear_hint` from `gates.clear_procedure(code)` (e.g. ESTOP: "release the E-stop, wait
  ≥ 100 ms, Clear E-stop, then Enable and Home"; LOAD_LIMIT: "Fault clear, then move to reduce the load —
  re-trips if the load grows"; HALT(button): "release the STOP button, then Clear stop"). Edge changes are
  also published (`indicators.changed`). The GUI polls `Backend.status()` at ≥ 10 Hz → indicator latency
  ≤ 200 ms (SAF-SW-005).

### 6.6 Speed-vs-margin warning (SAF-SW-006)

`calc.limits.limit_margin_warning(k_est_n_mm, v_mm_s, f_fw_n, f_pc_n, t_react_s=0.065) -> bool`
(`k_est·v·t_react > F_fw − F_pc`). Evaluated by `motion_gate` (WARN) for manual moves/jogs and by sequence
validation per step (WARN in the editor and at start).

---

## 7. Data pipeline (`core.pipeline`)

### 7.1 Sample representation

`io.protocol.DATA_DTYPE` (ICD pending; IF-006 fields): `t_us u32, payload_version u8, flags u8 (bit0
VALID), raw i32, setpoint_um i32, frame_seq u16, status (u16/u32 bit field per FW-STR-003)`. A batch is
decoded with one `np.frombuffer`. `SampleBatch` columns (numpy, per batch):

| Column | Type | Content |
|---|---|---|
| `t_us_u` | int64 | unwrapped device time (§7.3) |
| `t_dev_s` | float64 | `(t_us_u − t0_us)/1e6` (display/CSV) |
| `t_host_ns` | int64 | Reader receive stamp |
| `frame_seq`, `seq_lost` | uint16, int32 | sequence number, frames lost before this one |
| `flags`, `status` | uint8, uint32 | raw bit fields (lossless) + decoded bool views (`valid`, `moving`, …) |
| `raw`, `raw_state` | int32, uint8 | counts; `OK / SATURATED / NO_DATA (fallback) / SETTLING` |
| `setpoint_um`, `x_mm`, `x_test_mm` | int32, float64 | commanded position, machine mm, test mm (`− x_zero`) |
| `F_N`, `F_kgf`, `calc_reason` | float64, uint16 | NaN + reason bits (NO_CAL, NO_TARE, SATURATED, NO_DATA, AFE_MISMATCH) |
| derived (§7.4) | float64 | NaN + reason where not applicable |
| `op`, `step_idx`, `loop_iter`, `phase` | int16/uint8 | annotation from the operation state (atomic tuple) |

### 7.2 Stages (per batch; one frame per batch in normal 80 Hz operation)

1. **Decode**: payload version / LEN check (`bad_payload` counted, frame dropped and logged).
2. **Time unwrap** (§7.3).
3. **Gaps / link stats** (§7.3).
4. **Classify** raw: rails → SATURATED; fallback marker (FW-STR-005) → NO_DATA; `AFE_SETTLING` → SETTLING.
5. **Scale**: `F = calc.tare.force_n(raw, K, tare_raw)`, `x_mm`, `x_test_mm`; extrapolation flag when
   `|F| > 3·F_cal_max` (SW-CAL-008).
6. **Derived** channels (§7.4), streaming state carried between batches.
7. **EVENT dispatch** (in arrival order with DATA): MOVE_DONE → MoveTicket; stop/latch events → epoch++,
   `resync`, `terminate_all` where §5.5 says so; VALID_CLEARED; RESUME_REQUEST; BOOT → new time epoch,
   threshold recheck.
8. **Annotation** from the operation state.
9. **Safety** (§6.1) — may write STOP immediately.
10. **Sinks**: ring buffer, recorder queue, CaptureHub, WindowAccumulator, operation guards (§10.5), weak
    observers (GUI `latest` only; plots poll the ring buffer); then `liveness.beat("pipeline")`.

### 7.3 Time base and loss detection (R4 §9, IF-006/007)

- **Unwrap** (`calc.timebase.unwrap_us` + TS `TimeUnwrapper` logic): `t < t_prev` with a backward step
  > 2³¹ → `+2³²`; a smaller backward step or EVENT BOOT → new **epoch** (event `FW_RESET`); long host gaps
  (≥ 60 s, e.g. stream paused) resolve the wrap count from host receive stamps (`plausible_wraps`, TS
  SWD-M3R1-02). All windows use integer `t_us_u`.
- **Frame loss**: `lost = (seq − prev − 1) & 0xFFFF`; first frame after a gap with OVERRUN → attributed to
  the FW (`frames_lost_fw`), else to the link (`frames_lost_link`). **Missed HX711 conversion**: Δt > 1.5 ×
  median period without a seq gap (`afe_missed`). The reference resets on STREAM_STOP/START (a pause is an
  event, not a loss). Counters go to `Backend.status().link`, per-row `seq_lost`, and the recording
  (SW-ACQ-004).
- **Measured sample rate**: median of the last 16 AFE periods (fallback frames excluded); shown next to the FW
  value (FW-AFE-004).

### 7.4 Derived channels (SW-RT-004, R4 §10) — pure `calc.derived` functions

| Key | Formula / method (R4 §10) | Prerequisite | Live lag |
|---|---|---|---|
| `F_N`, `F_kgf` | `K·(raw − tare_raw)`, `/G0` | calibration + tare | 0 |
| `x_mm`, `x_test_mm` | `setpoint_um/1000`, `− x_zero` | homed for absolute meaning | 0 |
| `speed_mm_s` | central difference over ±2 samples using `t_us` | — | 2 samples (live value = centred at i−2) |
| `force_rate_n_s` | Savitzky–Golay derivative, 9 samples, order 2 | F | 4 samples |
| `k_tan_n_mm` | OLS dF/dx over a sliding window with `|Δx| ≥ 0.05 mm`; NaN at standstill | F | window/2 |
| `k_sec_n_mm` | `F / x_test`, NaN for `|x_test| < 0.05 mm` | F, x_zero | 0 |
| `work_nmm` | cumulative trapezoid ∫F dx since reset (record start / x_zero set) | F | 0 |
| `peak_n` | running max of `|F|` since reset; `break` marker on a drop > 20 % from the running max | F | 0 |
| `noise_counts`, `noise_n` | rolling std of raw over 1 s | — / K | 0 |
| `rate_sps`, `lost_frames` | `1/Δt_us`, cumulative losses | — | 0 |
| `sigma_mpa`, `eps` | `3FL/(2bh²)`, `6δh/L²` (3-point bend, optional) | geometry entered, F | 0 |
| status bits | one 0/1 channel per DATA status bit | — | 0 |

Live channels are causal (newest sample stamped with the value of the window ending at it; lag documented
above); the report recomputes non-causally with the same functions (`centered=True`). Display filters (EMA)
are D's and display-only. `core.channels.ChannelRegistry` lists every channel with key, label, unit,
quantity group, prerequisite set, `available` + `reason` (greyed state, SW-RT-002).

### 7.5 Ring buffer and data view (NFR-001, NFR-004)

- `ColumnRingBuffer` (TS `core/ringbuffer.py` as-is): preallocated; capacity = 900 s × 96 SPS (80 SPS + 20 %
  headroom) = **86 400 rows**; `t_dev_s` float64, all data columns float32 (raw ±2²³ and µm positions are
  exact in float32); ≈ 30 columns ≈ 10 MB + min/max pyramid (×4/16/64/256) ≈ 7 MB. Single writer (Pipeline),
  readers copy under a lock.
- `DataView` (GUI API, §15): `snapshot(keys, window_s, px_width)` → per key exactly `px_width` min/max pairs
  from the coarsest adequate pyramid level (render cost bounded by screen width, 5–600 s windows,
  SW-RT-003); `xy(x_key, y_key, window_s, max_points=4000)` → stride-decimated pairs with per-stride extrema
  kept (X-Y view, SW-RT-003); `latest()` → `LatestSample` (value + state n/a / STALE / SATURATED / INVALID,
  SW-RT-005); `sequence_trace(max_points=20 000)` → (x, F) of the running sequence, decimated on insertion
  (Δx ≥ 0.01 mm or ΔF ≥ 0.1 % FS) for the chart overlay (SW-SCH-002).

---

## 8. Recorder and momentary sample (`core.recorder`) — SW-ACQ-001..004

- **Stream control** (SW-ACQ-001): `Backend.stream_start()/stream_stop()`; state in `status().stream`;
  STREAM_STOP refused while moving or while an operation runs.
- **Folder** (SW-ACQ-002): `<recordings_root>/<YYYYMMDD_HHMMSS>_<specimen>_<number>/` (sanitised; default
  root `%USERPROFILE%\Documents\BirdBendStand\recordings`, session setting). Files: `data.csv`, `meta.json`
  (sidecar), `raw.bin` (lossless frame dump `u64 t_host_ns, u16 len, frame`, default on, ≈ 9 MB/h),
  `samples.csv` (take-sample rows while recording), `report.json` + `report.html` (§11).
- **`data.csv`**: UTF-8, `,` delimiter, `.` decimal, `nan`; header block of `# ` lines (format id
  `bird.bend.data/1`, SW/FW versions, board UID, start time, marks, K/B/status/file, tare_raw/time, limits,
  thresholds, x_zero, pointer to the sidecar), then the column header. One row per **DATA frame exactly once**
  (`row_type = D`) and interleaved **event rows** (`row_type = E`, time-ordered by `t_us_u`) for TARE,
  VALID_ON/OFF (SET_VALID response `t_us`) and FW VALID_CLEARED, STOP/HALT/SW trips, FW events, step
  boundaries (STEP_START/REACHED/SETTLE/CAPTURE_START/CAPTURE_END/STEP_END), PAUSE/RESUME, MARK, X_ZERO,
  FRAME_GAP, REC_GAP. Columns: `row_type, t_dev_s, t_us_u, t_us, t_host, frame_seq, seq_lost, flags, status,
  valid, moving, raw, raw_state, setpoint_um, x_mm, x_test_mm, F_N, F_kgf,` derived (§7.4 order)`, op,
  step_idx, loop_iter, phase, event`. Formats: integers `%d`, times `%.6f`, derived `%.9g`.
- **Sidecar `meta.json`** (`bird.bend.recording` v1, §13.6): written at start (marks + automatic snapshot:
  board configuration, calibration object, tare, limits, verified thresholds, SW/FW/proto versions, dictionary
  hash, session settings, sequence JSON, k_est, pull_dir — SW-META-002, SW-LIM-003), rewritten at stop (link
  statistics, CRC errors, overruns, frame gaps, events, integrity `{complete, rows_written, rows_lost,
  failures}`).
- **Back-pressure / failure** (SW-ACQ-004, TS-SWD §10.5 adapted): bounded queue = 60 s of rows; ≥ 50 % →
  warning; write/flush exception, disk full, overflow or recorder beat missing > 2 s → event `REC_FAILURE`,
  indicator "RECORDING FAILED", **a running sequence is stopped with a controlled stop** (recording is not a
  motion-safety condition); rows are never dropped silently (`rows_lost` + first/last `t_us` + `REC_GAP` row
  when writing resumes; sidecar `complete: false`). Free space checked at start (≥ max(500 MB, 1.5 × estimate))
  and every 10 s. Flush every 1 s, `os.fsync` at stop.
- **Sequence coupling**: sequence start starts a recording if none runs (SW-SEQ-003/005) and stops it 1 s
  after the sequence ends.
- **Take sample** (SW-ACQ-003): `Backend.take_sample(window_s=1.0)` (0.1–10 s) uses the CaptureHub → mean,
  std, N of F, x and raw (+ state counts) with UTC time, `t_dev_s`, marks → appended to `samples.csv` of the
  running recording, else to `<root>/samples/samples_<YYYYMMDD>.csv`; event `sample.taken` with the row.

---

## 9. Calibration and tare engines

### 9.1 Engine pattern (Qt-free state machines, KD-08)

```python
@dataclass(frozen=True)
class EngineState:
    kind: str; phase: str; step_index: int; step_count: int
    title: str; instruction: str                    # text for the wizard page (SW-CAL-001)
    inputs: tuple[InputSpec, ...]                   # fields the operator must fill (e.g. "D1 [mm]", "mass [kg]")
    progress: float | None                          # 0..1 during settle/capture (progress bar)
    stats: Mapping[str, float] | None               # live capture stats (N, mean, std, drift)
    result: Any | None; warnings: tuple[str, ...]; errors: tuple[str, ...]
    needs_confirmation: ConfirmRequest | None       # e.g. > 20 % steps/mm change (SW-CAL-003)
    can_continue: bool; can_repeat: bool; can_cancel: bool
class Engine(Protocol):
    def state(self) -> EngineState
    def subscribe(self, cb: Callable[[EngineState], None]) -> Token     # weak, backend thread
    def start(self, **config) -> None
    def continue_(self, inputs: Mapping[str, float] | None = None, *, confirmed: bool = False) -> None
    def repeat(self) -> None; def cancel(self) -> None
```

Methods never block (work goes to the Worker / Operation runner / CaptureHub); state changes are published
after they happen. Any STOP/HALT/ESTOP/latch → `terminate` → phase `ABORTED` with the reason; **the active
calibration is never changed by a cancelled or aborted wizard** (SW-CAL-001). STOP stays reachable in the
wizard (D).

### 9.2 CaptureHub (`core.capture`)

`start(kind, window_s, presettle_s, require_still: bool, on_done)` — device-time window
`[t_first + presettle, + window]` over pipeline samples (AFE frames only). Aborts on: saturated sample
(calibration point: invalid), stop/latch event, `MOVING = 1` when `require_still`, frames lost > 1 % (tare) /
N < 95 % nominal (calibration, evaluated at the end). Progress published at 5 Hz. Result: raw array, `t_us`
array, lost-frame count, nominal N (= window × measured rate). One capture at a time per kind; a tare is
refused while a sequence capture window is active.

### 9.3 Travel calibration (`core.calibration.travel.TravelCalEngine`) — SW-CAL-002..004, R4 §5

| Phase | Action | Formula / check |
|---|---|---|
| `CHECK` | connected, enabled, homed, no latch, no specimen (abs(F) < 2 % FS if calibrated, else operator confirms), travel room for 2 + 10 + 50 mm in + direction within SW/soft limits; remembers `spm0` | gate |
| `BACKLASH` | `move_to(x0 + 2 mm)` at `cal.v_mm_s` (default 2 mm/s) | one direction only |
| `REFERENCE` | operator zeroes the caliper → Continue | — |
| `MOVE1` | target for exactly `N1 = round_half_away(10·spm0)` steps from the reference: `calc.travelcal.target_for_steps(x_ref_um, N1, spm0) → (target_um, n_actual)` | wire is µm; exact for spm ≤ 1000 µm-resolution, ±1 step otherwise (F-B-02) |
| `ENTER_D1` | `spm1 = travel_cal_step1(...)` = `N1/D1`; plausibility (below) → `SET_PARAM motion.steps_per_mm = spm1` (idle) + read-back | TV-TC |
| `MOVE2` | `x1` = re-read position (now µm under spm1); `N2 = round_half_away(50·spm1)`; `target_for_steps(x1_um, N2, spm1)` | SW-CAL-002 |
| `ENTER_DTOT` | operator enters the **total** `D_tot`; `spm2 = (N1+N2)/D_tot`; `inc = N2/(D_tot − D1)`, consistency `inc/spm1 − 1` | TV-TC |
| `RESULT` | show spm0/spm1/spm2 (3 decimals), warnings | — |
| `ACCEPT` | `SET_PARAM spm2` + read-back, `SAVE` (+ CFG_DIRTY = 0), write `travel_<UTC>.json` + `active_travel.json` | SW-CAL-004 |
| `CANCEL` / abort | if the board value ≠ `spm0`: `SET_PARAM spm0` + read-back (idle after the stop); failure → warning + indicator "board steps/mm differs from the saved calibration" | SW-CAL-001 |

Plausibility (`calc.travelcal.travel_plausibility`, SW-CAL-003): **reject** `D ≤ 0` or a result outside
100…10 000 steps/mm; **confirm** a change > 20 % (showing the D-27 candidates 160 / 1280 steps/mm — needed
for the first calibration), a change > 5 % or a value outside ±20 % of the expected steps/mm (session
`expected_spm`, default 160); **warn "repeat"** when `|inc/spm1 − 1| > 0.5 %`. HOMED stays valid across
steps/mm changes (FW-MOT-009); the engine resyncs the commanded target after each SET.

### 9.4 Load calibration (`core.calibration.load.LoadCalEngine`) — SW-CAL-005..009, R4 §6

- Config: masses (default flow PO-SW-8.PC1–PC3: zero, m1, m2; more points allowed), `g_used` (session,
  default G0), presettle 2 s, capture 10 s (configurable, session §5.2). The engine starts the stream if off
  and restores it afterwards.
- Per point: `AWAIT_OPERATOR` (instruction "remove load" / "hang known weight, enter mass") → `PRESETTLE` →
  `CAPTURE` (progress, live std/drift) → `EVALUATE` with `calc.loadcal.point_acceptance` (any saturated
  sample → invalid; MAD 5σ outliers > 2 % → invalid; N ≥ 95 % nominal; `|drift| ≤ max(2·std, 20)`;
  `std ≤ max(3·std_zero, 50)`; masses increasing with `m2 ≥ 1.5·m1`; `m1 < 2 % FS` → warning only, D-22)
  → `ACCEPTED` or `REJECTED(reasons)` (Repeat).
- `FIT`: `calc.loadcal.load_calibration(raw_means, forces)` → K, B, residuals, R² (informative), NL_span,
  NL_FS, status PASS / WARN / FAIL / UNVERIFIED_LINEARITY (2 points); FAIL (also K ≈ 0, non-monotonic) →
  "points are not linear", accept disabled; WARN → operator may accept; negative K accepted; LOW_SPAN when
  the largest reference force < 20 % FS (SW-CAL-008); "linear within noise" note when `max|r| ≤ 3·u_r`.
- `ACCEPT` (idle only): `CalibrationStore.save_load(record)` (R4 §6.4 schema, `push_calibrated = false`, AFE
  snapshot, board UID/FW) + activate → `ThresholdManager.recheck()`. The session tare (raw) stays valid; F is
  recomputed with the new K.

### 9.5 Tare (`core.tare.TareEngine`) — SW-TARE-001..003, R4 §7

- `Backend.tare(window_s=None)` (session default 10 s, 2–60 s) is callable from every tab (toolbar, D);
  non-modal progress via engine events; refusal reasons are returned verbatim (code + text).
- **Refused** (`gates.tare_gate`) when: moving or < 1 s (device time) after the last `MOVING = 1`; any
  stop/fault latched; a sequence capture window active; not connected. Started stream is restored afterwards.
- Capture (`require_still=True`); **refused at the end** when: any saturated sample; outliers > 2 %;
  `std > max(3·std_zero_cal, 50)` counts; `|drift| > max(2·std, 20)`; lost frames > 1 %. Warning (accepted)
  when `|K·(tare_raw − raw_zero_cal)| > 10 % FS` ("large offset — specimen loaded?").
- Success: `tare_raw` = robust mean → session `TareState(id, tare_raw, t_us_u, utc, stats, board_uid)`;
  event row `TARE`; `ThresholdManager.recheck()` (motion gate closed until verified, SAF-SW-002).
- Validity (KD-13): current application run and board UID only; never loaded from a file; recordings carry it.

### 9.6 Calibration store (`core.calibration.store`) — SW-CAL-004, SW-CAL-009

`%APPDATA%\BirdBendStand\calibration\`: `load_<serial>_<UTC>.json` + `active_load.json`,
`travel_<UTC>.json` + `active_travel.json`; previous files are kept; the active copies load at start. At
connect the `afe` block is compared with `afe.gain_channel` / `afe.rate_sps`: mismatch → warning "calibration
invalid" and the load-limit input is treated as invalid until the AFE configuration matches again or a new
calibration is accepted (safe side for SAF-SW-001).

---
## 10. Sequencer (`core.sequencer`) — SW-SEQ-001..007, SW-STOP-003/004, SW-WIZ, SW-SEQF, SW-SCH-001

### 10.1 Model (`sequencer.model`, SW-SEQ-001/002)

```python
class StepKind(StrEnum): TRAVEL = "travel"; LOAD = "load"; HOLD = "hold"; HOME = "home"; TARE = "tare"; MARK = "mark"
@dataclass
class Step:
    uid: str                         # stable id (loops, report, chart labels survive reordering)
    kind: StepKind
    target: float | None = None      # mm (TRAVEL, in the sequence's travel_ref) or N (LOAD)
    speed_mm_s: float | None = None  # None = sequence default; LOAD: approach speed
    accel_mm_s2: float = 0.0         # 0 = FW default ("ramp")
    settle_s: float = 0.0
    capture_s: float = 0.0           # VALID window length; 0 = no report window
    step_time_s: float = 0.0         # TRAVEL/HOLD: minimum dwell at target; LOAD: reach timeout (0 = 3×planned + 10 s)
    tol_n: float | None = None       # LOAD completion tolerance
    capture_during_move: bool = False  # linear-ramp steps: VALID = 1 while moving (no steady state)
    travel_bound_mm: float | None = None  # LOAD: max excursion of the approach (default: SW travel limit)
    wait_operator: bool = False      # MARK: pause until the operator presses Continue
    label: str = ""
@dataclass
class Loop:  first: int; last: int; count: int      # inclusive step indices; count 1..10 000, 0 = until stopped
@dataclass
class Sequence:
    name: str; steps: list[Step]; loops: list[Loop]
    travel_ref: Literal["test", "machine"] = "test"  # targets relative to x_zero (frozen at start) or machine mm
    k_est_n_mm: float = 50.0; pull_dir: int = +1     # R4 §8.4, SRS §5.2
    defaults: StepDefaults                            # speed, accel, settle, capture, tol
    on_trim_fail: Literal["stop", "continue"] = "stop"
    notes: str = ""
    def validate(self, ctx: ValidationContext) -> list[Issue]   # editor flags (SW-SEQ-001)
```

Validation (`Issue(step_uid, field, severity ERROR|WARN, text)`): field types/ranges (speed ≤ effective cap,
accel ≤ `a_max`, times ≥ 0, `capture_s > 0` when `capture_during_move`, LOAD needs `tol_n > 0` and
`k_est > 0`), targets inside the SW travel limits (TRAVEL after `travel_ref` conversion) and the load trip
levels (LOAD), loops disjoint or properly nested **one level** (overlap / deeper nesting → ERROR), count
range, SAF-SW-006 margin per moving step (WARN).

### 10.2 Plan and planned path (`sequencer.plan`, `calc.path`) — SW-SEQ-002, SW-SCH-001

- `expand(seq, ctx) -> Plan`: list of `PlannedStep(exec_idx, step_idx, uid, loop_iters: tuple[int, ...],
  kind, x_start_mm, x_target_mm, f_target_n, t_cmd_s, t_reached_s, capture=(t0, t1) | None, t_end_s, label,
  infinite: bool)` in plan time, durations from `calc.motion.move_duration_s` (trapezoid, R4 §1.5) and, for
  LOAD steps, the travel estimate `Δx = pull_dir·(F_target − F_prev)/k_est`. Loops expand inner first; a
  `count = 0` loop expands once with `infinite = True`. **One plan is the single source** for the editor
  preview, total duration, the chart, the executor's step order/loop labels and the expanded-plan tests (TS-SWD
  §9.2 principle).
- `calc.path.planned_path(plan, x0_mm, f0_n, k_est, pull_dir) -> list[PathPoint(x_mm, f_n, exec_idx, label,
  known: "x" | "F" | "both")]` (pure): TRAVEL → x known, `F_est = F_prev + pull_dir·k_est·(x − x_prev)`; LOAD →
  F known, `x_est = x_prev + pull_dir·(F − F_prev)/k_est`; HOLD/MARK/TARE → same point; HOME → break in the
  polyline (unknown F). The chart (D) draws the polyline with step labels; during execution it overlays
  `DataView.sequence_trace()`, the live marker (latest x, F) and the active step from `SeqStatus`
  (SW-SCH-002, ≥ 10 Hz polling).

### 10.3 Executor (`sequencer.executor`) — SW-SEQ-003..005

```python
class SeqState(Enum): IDLE; PREPARING; RUNNING; PAUSED; WAITING_OPERATOR; STOPPING; FINISHED; STOPPED; ABORTED; ERROR
@dataclass(frozen=True)
class SeqStatus:
    state: SeqState; exec_idx: int; step_idx: int; step_uid: str; loop_iters: tuple[int, ...]
    phase: str                       # COMMAND, MOVING, APPROACH, TRIM, SETTLE, CAPTURE, HOLD, WAIT_OPERATOR
    t_dev_s: float; plan_t_s: float; behind_s: float; windows_done: int; windows_total: int | None
    k_est_n_mm: float; message: str
class SequenceExecutor:
    def start(self, seq: Sequence, *, confirmations: Confirmations) -> None   # gate SW-SEQ-005
    def pause(self, source: str = "gui") -> None; def resume(self) -> None
    def stop(self) -> None            # controlled stop, sequence ends (SW-SEQ-007)
    def abort(self, reason: str) -> None    # HALT (SW-SEQ-007)
    def continue_(self) -> None       # MARK wait_operator
    def status(self) -> SeqStatus; def subscribe(self, cb) -> Token   # status published at 10 Hz + on change
```

- **Start gate** (`gates.sequence_start_gate`, SW-SEQ-005): homed and enabled, no latch, no other operation,
  validation without ERROR, all TRAVEL targets inside the SW limits (after freezing `x_zero`), calibration +
  tare valid if any LOAD step (and whenever a load limit is enabled), thresholds VERIFIED, stream on and
  fresh, recording can start (folder writable, free space). CONFIRM items: HOME steps with load ≥ 5 % FS or
  unknown. Then: start the recording if off, freeze `x_zero`, event `SEQ_START` (plan summary into the
  sidecar), owner = SEQUENCE.
- Runs in the **Operation runner** thread: 20 ms `Ticker` plus event waits (MoveTicket, pipeline sample
  condition). **Phase boundaries use device time**: the executor waits until the newest sample's `t_us_u`
  reaches the boundary, so every boundary lies within ±1 frame of plan (SW-SEQ-003).
- **Step timeline** (SW-SEQ-003): command → reached (`t_reached` = `t_us` of MOVE_DONE, or of the last trim
  sample) → settle until `t_reached + settle_s` → capture → hold until `t_reached + max(step_time_s,
  settle_s + capture_s)` (TRAVEL/HOLD; for LOAD steps `step_time_s` is the reach timeout and the dwell is
  `settle_s + capture_s`) → next step. TRAVEL target (machine) = target + frozen `x_zero` when
  `travel_ref = "test"`. HOME waits for HOMED (EVENT) then resyncs; TARE runs the TareEngine inline (refusal →
  step error → sequence STOPPED); MARK writes an event row and optionally waits for `continue_()`.
- **VALID windows** (SW-SEQ-004): capture start → `set_valid(1)` → returned `t_on` is the window start; end
  when the newest `t_us_u ≥ t_on + capture` → `set_valid(0)` → `t_off`. `capture_during_move` steps: VALID 1
  before MOVE_ABS, 0 after MOVE_DONE. VALID is also reset (and the reset awaited, ≤ 500 ms) on pause, stop
  and error; on abort, HALT/ESTOP and link loss the FW clears it itself (SAF-FW-001) and the executor never
  re-asserts it. The manual VALID toggle is refused while a sequence runs. Every window `{exec_idx,
  loop_iters, t_on, t_off, planned}` is logged to the sidecar so the validator can prove that every VALID = 1
  frame lies inside a planned window.
- **Termination** (SW-STOP-003): FW-reported HALT/ESTOP (any source), SW trip, link loss, liveness fault →
  `terminate_all` swaps the state to ABORTED in the Pipeline/Supervisor thread within the frame that carries
  it; the runner only cleans up (no command is sent by the sequence afterwards). Recorder failure →
  STOPPING (controlled) → STOPPED with error (SW-ACQ-004).
- **End**: FINISHED / STOPPED / ABORTED → owner released, recording stopped after a 1 s tail if the
  sequence started it, report built (§11, partial runs flagged), axis holds position (no automatic return —
  the operator adds a return step).

### 10.4 Load-target steps — approach + trim (`sequencer.loadstep`, `calc.trim`) — SW-SEQ-006, R4 §8.4

1. `F_now` = mean of the last 4 AFE-valid samples; `e = F_target − F_now`; `|e| ≤ tol` → reached.
2. **Approach**: `sgn = sign(e)`; `band = approach_band(tol, k_est, v) = max(tol, k_est·v·0.065 s)`;
   `F_stop = F_target − sgn·band` (skip the approach when `F_now` is already inside the band);
   `raw_stop = force_to_raw_stop(F_stop, K, tare_raw, sgn)` rounded so the FW stops no later than `F_stop`;
   motion direction `= sgn·pull_dir`; `bound_um` = the SW travel limit (or `travel_bound_mm`) in that
   direction → `MOVE_UNTIL_LOAD(direction, v, a, raw_stop, bound_um)` (the comparison sense follows
   `sign(K)·sgn`; ICD pending, F-B-04). MOVE_DONE reason THRESHOLD → trim; BOUND → step error
   `LOAD_NOT_REACHED`.
3. **k_est**: `k_est_from_approach(x, F)` = OLS slope over the last ≥ 0.1 mm of the approach (sign-normalised by
   `pull_dir`), clamped to [`k_min`, `k_max`] (session), fallback = the sequence value; carried to later steps
   and recorded (SW-REP-001).
4. **Trim**: wait until `t_done + 100 ms` (device time); `F̄` = mean of the last 4 samples; `|F_target − F̄| ≤
   tol` → reached; else `Δx = trim_step(F̄, F_target, k_est, kp=0.5, max_step=0.2 mm)` (sign by `pull_dir`),
   `MOVE_ABS(commanded + Δx)` at 0.2 mm/s (SW-limit checked), repeat ≤ 10 iterations (session trim
   parameters, SRS §5.2). Exhausted → `TRIM_FAILED`: `on_trim_fail = "stop"` (default) → step
   NOT_ON_TARGET, sequence STOPPED; `"continue"` → capture anyway with the flag (F-B-14).
5. **Capture**: position frozen (no trim during settle/capture/hold).
TV-C (R4 §12) is the unit vector for `trim_step`; the simulator scenario `k = 50 N/mm, k_est = 40` must reach
tolerance within ≤ 10 iterations (SW-SEQ-006 acceptance).

### 10.5 Guards, controls, pause/resume — SW-SEQ-007, SW-STOP-004

- **Guards** (pipeline sink, per sample, while a TRAVEL/LOAD step moves): `SLIP` — load moves opposite to the
  expected direction by > 5 % of `|F_target|` (or of the running extreme for travel steps) from its running
  extreme; `BREAK_DETECTED` — `|F|` drops > 20 % below its running max once the running max ≥ max(5 % of
  target, 1 % FS); `TIMEOUT` — LOAD: `t − t_cmd > step_time_s` (0 → 3×planned + 10 s), TRAVEL: no MOVE_DONE
  within 3×planned + 10 s; `BOUND` — MOVE_DONE reason. Action: priority STOP, step failed, sequence STOPPED,
  event with values.
- **Controls**: start, pause, resume, stop (controlled stop, sequence ends), abort (HALT).
- **Pause** (GUI Pause or physical PAUSE EVENT): STOP(controlled) unless the FW already stopped; an open VALID
  window is closed and discarded (`WINDOW_DISCARDED`); step, phase and loop counters are kept; state PAUSED.
  **Resume** (GUI or RESUME_REQUEST EVENT; motion gate re-checked): TRAVEL → re-issue the absolute target, then
  settle + capture anew; LOAD → re-run approach + trim from the current force; HOLD → restart settle/capture;
  HOME/TARE/MARK → re-run. VALID is never 1 during a pause. In manual mode `Backend.pause()` is a controlled
  stop only.

### 10.6 Generators (`sequencer.generators`) — SW-WIZ-001/002

Pure functions returning `Block(steps, loops)`:

| Generator | Parameters | Output |
|---|---|---|
| `staircase` | kind TRAVEL/LOAD, start, end, increment **or** count, `up` / `up_down`, `return_to_zero`, speed, accel, settle, capture, step_time, tol | one step per level; up_down mirrors without repeating the peak; optional zero step between levels |
| `linear_ramp` | x0, x1, speed, accel | TRAVEL x0 (no capture) + TRAVEL x1 with `capture_during_move` |
| `cyclic` | kind, lo, hi, cycles, speed, accel, dwell_s, capture | two steps lo/hi + `Loop(count = cycles)` |
| `hold` | kind, target, duration_s | one step, `capture_s = duration_s` (creep/relaxation, captured throughout) |
| `return_` | `"zero"` / `"home"` | TRAVEL 0 (test) or HOME |

Levels are computed in integer units (µm, mN) to avoid float drift; the end level is included only if the
increment divides the span; increment ≤ 0 or > span → ValueError. `Sequence.insert_block(block, mode =
append | replace | insert, index)` re-indexes loops; inserted steps stay editable (SW-WIZ-002, editor is D's).

### 10.7 Sequence files (`sequencer.seqfile`) — SW-SEQF-001

`save(seq, path)` / `load(path) -> Sequence` (§13.4). `load` parses into a **new** object and validates the
schema; any error raises `FileFormatError` and the caller keeps the current sequence unchanged. Writes are
atomic (temp file + `os.replace`); round trip is identical (fixed key order, `repr` floats).

---

## 11. Steady-state extraction and report (`core.report`, `calc.steady`) — SW-REP-001..004

- **WindowAccumulator** (pipeline sink): groups samples by execution step + loop iteration from the
  annotation (or `manual #n` for manual VALID toggles) together with the executor's planned window
  `[t_reached + settle, + capture]`.
- **Steady state** (`calc.steady.steady_state`, SW-REP-002, TV-SS): samples with VALID = 1, MOVING = 0,
  `setpoint_um` equal to its value at window start, no stop/fault flag, `t ∈ [t_reached + settle, + capture]`
  → per quantity (raw, F, x): N, mean, std (ddof 1), min, max, SE(N_eff) (`se_ar1`), drift (slope·T);
  `INCOMPLETE` if `N < 0.8·capture·rate` (measured rate); load steps `ON_TARGET` if `|mean − target| ≤ tol`.
  `capture_during_move` windows produce a ramp result instead (OLS stiffness, F and x ranges).
- **Outputs** (SW-REP-001): `report.json` (`bird.bend.report` v1: marks, snapshot references, per-step/iteration
  results, warnings) and `report.html`: self-contained (inline CSS, inline **SVG** figures built by
  `core.report.svg_chart` — no matplotlib): marks incl. custom fields (SW-META-001), snapshot (calibration
  K/B/status/date/LOW_SPAN, tare, limits, thresholds, board configuration summary, SW/FW versions, final
  k_est), warnings (LOW_SPAN, "push forces tension-calibrated", extrapolated forces, INCOMPLETE windows,
  trim failures, frame losses, recording incomplete), F–x and F–t figures (≤ 5000 points, capture windows
  shaded), step result table, event list. Generated at sequence end (also for stopped/aborted runs, flagged
  partial) and on demand for a manual recording.
- **3-point bend** (SW-REP-004, off by default): with L, b, h → σ = 3FL/(2bh²), ε = 6δh/L², E_f = L³·m/(4bh³)
  (m = OLS slope N/mm over a selected range) per window (TV-D).
- **Offline** (SW-REP-003): `python -m bend_stand.core.report <recording_dir> [--cal file.json] [--tare-raw
  value | --tare-window t0 t1] [--out dir]` recomputes F from raw and rebuilds windows from VALID + annotations;
  unchanged inputs reproduce the original numbers exactly (test).

---

## 12. Simulator (`io.sim`) — SYS-008, D-07, R4 §11

### 12.1 Structure

`SimBoard` sits on the board end of a `VirtualTransportPair`; it uses the **same** `io.framing`/`io.protocol`
codec (board direction) and the generated `params_gen` dictionary (ranges, defaults, `moving_ok`) plus
`core.params.check_hard_rules` (common-mode risk accepted, mitigated by the twin differential tests, §12.5).
NVM is a JSON file (or memory) with the CFG_DIRTY definition of the ICD; REBOOT restarts the logic and sends
EVENT BOOT. GET_INFO returns the ICD's proto/payload versions and the generated dictionary hash.

### 12.2 FW behaviour (`sim.fw_logic`) — behavioural copy of the SRS FW requirements + ICD

Command check order type → length → arguments → state → execution, one response per command echoing SEQ,
NACK codes and details per ICD (FW-CMD-001); the **whole stop/enable policy table SRS §3.2** (E-stop, STOP,
HALT from PC/button, PAUSE button, limits incl. wiring fault, FW load limit incl. rails and regrow, AFE stale
and fallback frames, link watchdog 1 s, jog dead-man 250 ms, step fault injection, homing failures, idle
disable 600 s with the 2 % FS band, DISABLE, reset); motion with analytic trapezoid profiles and exact step
counts (`calc.motion`), soft limits, speed caps incl. the step-rate cap, busy refusal, JOG toward the soft
limit, MOVE_UNTIL_LOAD evaluated per HX711 sample, HOME sequence with the switch model (FW-HOM-001/002
outcomes), immediate stops freeze the position at the stop instant (completed steps; `pos_uncertain`),
controlled stops with `a_stop`; ENABLE settle; steps/mm change rescales µm (machine zero = step count);
SET_VALID boundary semantics and VALID auto-clear with VALID_CLEARED; EVENT queue ≥ 8 with overflow counter;
GET_STATUS counters; one DATA frame per HX711 conversion with the setpoint latched at data-ready, u16 frame
sequence and OVERRUN under TX congestion.

### 12.3 Models (`sim.models`, R4 §11)

| Model | Content (defaults) |
|---|---|
| `DriverModel` | ideal step follower + optional first-order lag τ = 5 ms; ALM/PEND injection (report only, D-16); power removal (E-stop) → position lost, optional back-drive release of the specimen down to a holding force ≈ 300 N (R5 §1.2, configurable) |
| `SwitchModel` | START at x = −`home.offset` − 0.5 mm, END at stroke 300 mm + margin; bounce 3 × 0.2–2 ms; stuck-open (broken wire), swapped switches |
| `ButtonModel` | E-stop sense (NC), STOP/BREAK (NC), PAUSE (NO), polarity per parameters, bounce |
| `Hx711Model` | data-ready at 80 SPS × (1 + ε), ε = +0.5 % (±2 % configurable); 4-sample moving average of `offset + S·F(t)`, S = 3285 counts/N (SRS A-02, 3.0 mV/V; R4's 2190 is the 2 mV/V value), offset 125 000 counts; Gaussian noise σ = 45 counts; spikes p = 1e-4; rail saturation; drift +20 counts/min; missed conversions; RATE pin 10/80 SPS; gain/channel scaling; power-down/re-init |
| `SpecimenModel` | none / linear spring `F = k·(x − x_c)` beyond contact (sign by `pull_dir`), bilinear yield k1 → k2, relaxation 2 % with τ = 30 s at constant x, break at `F_break` (F → 0), hysteresis for cycles, machine compliance C_m |
| `LinkModel` | latency 1–16 ms, frame loss rate, CRC corruption, PC→FW silence windows (watchdog tests), unplug/replug |

### 12.4 Scenario and control

`SimScenario` (`bird.bend.simscenario` v1, JSON — no YAML dependency) holds every model parameter, the initial
world (specimen, switch positions, cell offset) and an optional fault schedule (`t_s`, action). `SimControl`
(thread-safe) exposes the world actions used by tests and an optional simulator panel: `set_specimen(...)`,
`press(button)/release(button)`, `set_estop(open)`, `set_load_offset(counts)`, `set_rate_error(eps)`,
`inject(fault, **args)` (AFE stall, saturation, step fault, ALM, link loss/corruption). The action vocabulary
is to be aligned with the FW twin control port (F-B-06).

### 12.5 Behavioural equality

The ICD and the shared vectors are normative. (1) The codec passes the `ref_codec` vectors (both directions).
(2) Differential tests (Integrator, `03_SW/tests/integration/test_sim_vs_twin.py`) run identical scripted
scenarios on the simulator and on the FW host twin (`tcp://`) and compare responses, NACK codes, event
sequences, DATA flags and positions (timing within tolerances). A divergence is a simulator defect unless the
ICD is ambiguous (then an ICD finding to the Integrator). The simulator reports the ICD version it implements.

### 12.6 Clocks

`RealTimeSimClock` (1 ms tick thread; GUI demo and backend component tests) and `LockstepSimClock` (the test
advances virtual time; frames are produced into the virtual pipe as time advances) for deterministic tests of
the simulator logic and of engines driven with a `FakeClock`.

---

## 13. File formats

All JSON files carry `"schema": "bird.bend.<kind>"` and an integer `"schema_version"`; readers accept their
own version, migrate older versions with explicit functions and refuse newer ones (`FileFormatError "made by
a newer SW version"`); unknown keys → warning. Validation is hand-written (`core.schema` field specs, KD-11).
Writes are atomic (temp file + `os.replace`). Paths: `%APPDATA%\BirdBendStand\{calibration,sessions,presets,
sequences,logs}`; recordings under the session's `recordings_root`.

| § | File | Schema | Content | Req |
|---|---|---|---|---|
| 13.1 | `*.bbboard.json` | `bird.bend.board` v1 | saved_utc, sw/fw version, board UID, `param_dict_hash`, `param_dict_version`, `params {key: value}` (enums by NAME, bool, f32 as float) | SW-CFG-002 |
| 13.2 | `load_<serial>_<UTC>.json`, `active_load.json` | `bird.bend.cal.load` v1 | R4 §6.4 verbatim (sensor, afe, board, direction, `push_calibrated = false`, g_used, points with stats, fit with status, LOW_SPAN flag, notes) | SW-CAL-009 |
| 13.3 | `travel_<UTC>.json`, `active_travel.json` | `bird.bend.cal.travel` v1 | created_utc, operator, board UID/FW, spm0, N1, D1, spm1, N2, D_tot, spm2, spm2_inc, consistency, expected_spm, confirmations, notes | SW-CAL-004 |
| 13.4 | `*.bbseq.json` | `bird.bend.sequence` v1 | name, travel_ref, k_est_n_mm, pull_dir, defaults, on_trim_fail, steps[] (all §10.1 fields), loops[], notes | SW-SEQF-001 |
| 13.5 | `*.bbsession.json` | `bird.bend.session` v1 | limits (travel/pull/push with enable + warn), fw_level_n, pull_dir, k_est default + k_min/k_max, trim (kp, max_step_mm, v_mm_s, max_iter), tare window, cal presettle/capture, sample window, display unit, g_local, expected_spm, 3-point-bend geometry, recordings_root, raw dump on/off, last files | SW-LIM-003, SRS §5.2 |
| 13.6 | `*.bbmarks.json` (presets) | `bird.bend.marks` v1 | specimen, number, operator, notes, custom `[{key, value}]` | SW-META-001/002 |
| 13.7 | `<rec>/meta.json` | `bird.bend.recording` v1 | marks, snapshot (board config, calibration object, tare, limits, thresholds, versions, dictionary hash, session), sequence JSON + plan summary, VALID window log, events, link stats, integrity | SW-ACQ-002/004, SW-META-002 |
| 13.8 | `<rec>/data.csv`, `samples.csv` | `bird.bend.data` 1 (header line) | §8 | SW-ACQ-002/003 |
| 13.9 | `<rec>/report.json`, `report.html` | `bird.bend.report` v1 | §11 | SW-REP-001 |
| 13.10 | `*.simscn.json` | `bird.bend.simscenario` v1 | §12.4 | SYS-008 |

Example (sequence):

```json
{"schema": "bird.bend.sequence", "schema_version": 1, "name": "staircase 0-200 N",
 "travel_ref": "test", "k_est_n_mm": 50.0, "pull_dir": 1, "on_trim_fail": "stop",
 "defaults": {"speed_mm_s": 1.0, "accel_mm_s2": 0.0, "settle_s": 2.0, "capture_s": 5.0, "tol_n": 2.0},
 "steps": [
   {"uid": "s1", "kind": "travel", "target": 0.0, "speed_mm_s": 2.0, "label": "start"},
   {"uid": "s2", "kind": "load", "target": 100.0, "tol_n": 2.0, "settle_s": 2.0, "capture_s": 5.0, "label": "100 N"},
   {"uid": "s3", "kind": "load", "target": 200.0, "tol_n": 2.0, "settle_s": 2.0, "capture_s": 5.0, "label": "200 N"},
   {"uid": "s4", "kind": "travel", "target": 0.0, "speed_mm_s": 2.0, "label": "return"}],
 "loops": [{"first": 1, "last": 2, "count": 3}], "notes": ""}
```

---
## 14. Error handling and logging

- Exception hierarchy (`core.errors`, each with `user_text`): `BendStandError` → `LinkError`
  (`NotConnected`, `TransportError`, `CommandTimeout`, `CommandOutcomeUnknown`, `NackError(status, name,
  detail, detail_text)`, `IncompatibleFirmware`, `ConfigReadOnly`), `PreconditionError(GateResult)`
  (`GateRefused`, `ConfirmationRequired`), `CalibrationError`, `SequenceError`, `FileFormatError`,
  `RecorderError`. NACK details are decoded per the ICD into text (e.g. "HALT latched by STOP button; button
  still pressed").
- `stop()`, `halt()`, `pause()` never raise; failures are in the returned result and in events.
- Engines report errors through their state (`errors`, phase `ABORTED`/`ERROR`), never by raising into
  backend threads; futures carry exceptions to the GUI bridge.
- Uncaught exceptions in any thread → liveness fault (§4.6): STOP if moving, `terminate_all`, event, log.
- Logging: stdlib `logging`, rotating file `%APPDATA%\BirdBendStand\logs\bend_stand.log` (10 × 5 MB);
  `EventBus` keeps the last 10 000 events in RAM; every event of a recording is also written to it.
- Defensive rules: the SW never re-asserts VALID after a FW auto-clear; never restarts motion after a clear,
  reconnect or resume without an explicit operator action (resume is one).

---

## 15. Backend API for the GUI (contract for Implementer D)

The GUI constructs exactly one `Backend` (`core.backend`) and talks to it only through the members below.
`core.api` publishes the same surface as `typing.Protocol` classes so D can build fakes for GUI tests. Nothing
in this API is a Qt type; D's `gui.bridge.QtBridge` adapts events and futures to queued Qt signals.

### 15.1 Facade

```python
class Backend:
    def __init__(self, settings: BackendSettings | None = None) -> None  # loads session + active calibrations; no port opened
    def start(self) -> None          # Supervisor, Worker, hotkey (Pause/Break works before Connect)
    def shutdown(self) -> None       # STOP if moving, stop recording, disconnect, join threads
    def gui_beat(self) -> None       # call on every GUI timer tick (liveness)

    # connection
    def endpoints(self) -> list[EndpointInfo]                 # ST-LINK COM ports first, "sim", configured tcp twin
    def connect_async(self, endpoint: str) -> Future[DeviceInfo]
    def disconnect_async(self) -> Future[None]

    # global actions: callable from the GUI thread, non-blocking (≤ 5 ms), never raise
    def stop(self, source: str = "gui") -> StopResult         # SW-STOP-001
    def halt(self, source: str = "gui") -> StopResult         # Pause/Break semantics (also used by "Abort")
    def pause(self, source: str = "gui") -> None              # SW-STOP-004
    def resume(self) -> GateResult
    def tare(self, window_s: float | None = None) -> GateResult        # SW-TARE-001; progress → events "tare.state"
    def take_sample(self, window_s: float = 1.0) -> GateResult         # SW-ACQ-003
    def record_start(self) -> GateResult; def record_stop(self) -> None   # SW-ACQ-002
    # global actions that wait for the board: futures
    def clear_stop_async(self) -> Future[ClearResult]                  # HALT_CLEAR (SW-STOP-003)
    def estop_clear_async(self, *, confirmed: bool) -> Future[ClearResult]
    def fault_clear_async(self) -> Future[ClearResult]
    def stream_start_async(self) -> Future[None]; def stream_stop_async(self) -> Future[None]

    # immutable snapshots (thread-safe, cheap)
    def status(self) -> BackendStatus

    # sub-APIs
    config: ConfigAPI            # ParamMeta list/groups, board values, write_and_verify_async, save/load/defaults_async,
                                 # save_board_config(path) / load_board_config(path) -> BoardConfigFile (SW-CFG)
    motion: MotionController     # §5.4 (move_to, move_by, jog_*, enable, disable, home, set_test_zero, set_valid, limits)
    limits: LimitsAPI            # get() -> LimitConfig; set(cfg) -> list[Issue]; thresholds() -> ThresholdState
    data: DataView               # snapshot / xy / latest / sequence_trace (§7.5)
    channels: ChannelRegistry    # keys, labels, units, groups, available + reason (SW-RT-002)
    marks: MarksAPI              # get/set TestMarks (incl. custom fields), list/save/load presets (SW-META)
    session: SessionAPI          # get/set SessionSettings, load/save session file
    tare_engine: TareEngine      # state()/subscribe()/cancel()
    travel_cal: TravelCalEngine; load_cal: LoadCalEngine     # Engine protocol §9.1
    calibrations: CalibrationStore   # active load/travel records, history list
    sequencer: SequencerAPI      # new/load/save, validate, expand (Plan), planned_path, generators,
                                 # insert_block, start/pause/resume/stop/abort/continue_, status()
    reports: ReportAPI           # build_async(recording_dir, cal=None, tare=None) -> Future[ReportPaths]
    events: EventBus             # subscribe(topic, cb, weak=True) -> Token; unsubscribe(token)
    sim: SimControl | None       # present when connected to "sim"
```

### 15.2 `BackendStatus` (polled; rebuilt on change, returned by reference)

| Field | Content |
|---|---|
| `link` | state, why, endpoint, compat, `DeviceInfo` summary, `LinkStats` (frames OK, lost FW/link, CRC/len/timeout errors, late responses, command timeouts, FW counters) |
| `stream` | on/off, measured rate, rate mismatch |
| `indicators` | `Indicators` (§6.5) incl. `clear_hint` per latched item |
| `motion` | moving, homed, enabled, position_mm, test_position_mm, commanded_target_mm, pending_target_mm, x_zero_mm, owner, effective caps (v, a, step-rate), SW travel range |
| `safety` | SW trip latch, active warnings, `ThresholdState`, load-limit input valid + reason |
| `calibration` | active load (K, status, LOW_SPAN, date, AFE mismatch), active travel (spm, date), board spm |
| `tare` | state, tare_raw, age, id |
| `operation` | owner kind + engine phase; `sequence: SeqStatus` |
| `recording` | state, folder, rows, rows_lost, queue fill %, failure text |
| `gates` | precomputed `GateResult` for `move`, `jog`, `home`, `enable`, `disable`, `tare`, `sequence_start`, `clear_stop`, `estop_clear`, `fault_clear` (enable/disable controls; texts for tooltips) |
| `hotkey` | active / unavailable (reason) |
| `cfg_dirty`, `config_read_only` | CFG_DIRTY, hash mismatch |

### 15.3 Event topics (`EventBus`, payloads are frozen dataclasses)

`link.state`, `link.compat`, `device.info`, `device.params`, `device.status`, `fw.event` (decoded FW EVENT),
`stop.issued` (StopResult), `halt.confirmed` / `halt.unconfirmed`, `indicators.changed`, `safety.trip`,
`safety.warning`, `safety.thresholds`, `motion.done` (MoveDone), `motion.target`, `motion.dropped`,
`tare.state`, `cal.travel.state`, `cal.load.state` (EngineState), `seq.status`, `seq.step_result`,
`seq.window`, `rec.state`, `rec.failure`, `sample.taken`, `report.ready`, `log`.

### 15.4 Rules for the GUI

1. Create `Backend`, call `start()` before showing the main window (hotkey and liveness active before Connect).
2. Only `stop`, `halt`, `pause`, `resume`, `tare`, `take_sample`, `record_*`, `status`, `data.*`, engine
   `state()`/methods and `motion` commands may be called in the GUI thread (all non-blocking; motion
   commands return a `MoveTicket` immediately and refuse with `PreconditionError` before sending);
   everything that waits for the board is `*_async`. No `.result()` in the GUI thread.
3. Subscribe with bound methods of a long-lived bridge object (held weakly by the backend); callbacks run on
   backend threads, must return in < 1 ms and must not touch widgets — re-emit as queued signals.
4. One GUI timer (`Qt.PreciseTimer`, 33–50 ms) polls `status()` (≥ 10 Hz for indicators, SAF-SW-005) and
   `data.snapshot()`; call `gui_beat()` on each tick.
5. Jog: `motion.jog_start` on press, `motion.jog_stop` on release, focus loss and window deactivation.
6. Controls are enabled from `status().gates`; REFUSE texts become tooltips; CONFIRM items open a dialog
   (Enter/Space never confirm, STOP inside) and the action is repeated with `confirmed=True`.
7. STOP buttons call `backend.stop("<window>")` directly and show the returned `StopResult`.
8. Units: the API uses N, mm, s; the N/kgf display choice is a GUI setting (`calc.units` helpers).
9. Exceptions from futures are `BendStandError` subclasses; show `user_text`.

---

## 16. Performance design (NFR-001..004)

| Item | Design value | Basis |
|---|---|---|
| DATA rate / size | 80 Hz × ≈ 28 B ≈ 2.2 kB/s (≈ 2.4 % of the link) | R3 §1.6, IF-011 |
| Reader → Pipeline | ≤ 2 ms read timeout, queue 4096 frames (51 s), overflow = SW loss (must stay 0, NFR-004) | §4.3 |
| Pipeline per frame | ≤ 1 ms (numpy on 1–8 rows, ~30 vector ops, safety loop) | benchmark test |
| Violating frame → STOP on the wire | ≤ 6 ms typ., requirement ≤ 50 ms p95 | §6.2, SAF-SW-001 |
| GUI STOP click → STOP frame | GUI thread → priority write ≤ 3 ms + GIL ≤ 1 ms (switch interval) ≪ 50 ms p95 | NFR-002 |
| Pause/Break → HALT frame | hotkey thread, independent of the GUI thread; ≤ 5 ms typ. | NFR-003 (TS measured p95 3.6 ms) |
| Plot data | ring buffer 86 400 rows, pyramid ×4/16/64/256; `snapshot()` returns ≤ 2 points per pixel column → render cost bounded by width; 30 s × 80 Hz = 2400 rows (raw path) | NFR-001 |
| X-Y view | ≤ 4000 points per refresh (stride + extrema) | NFR-001 |
| Recorder | 80 rows/s × ≈ 300 B ≈ 86 MB/h CSV + 9 MB/h raw dump; batch formatting every 250 ms in its own thread | NFR-004 |
| Memory | preallocated ring (≈ 17 MB incl. pyramid), bounded queues (Reader 4096, recorder 60 s), event history 10 000, capture ≤ 60 s × 96 SPS, sequence trace ≤ 20 000 points; no per-sample Python objects kept after a batch → growth after fill ≤ 50 MB measured with `GetProcessMemoryInfo` via ctypes | NFR-004 |
| Timer resolution | `timeBeginPeriod(1)`, `setswitchinterval(0.001)`; heavy jobs (report, file I/O, write_and_verify) on the Worker | TS-SWD §3.1 |
| Perf hooks | `VirtualTransportPair.wire_log` (write timestamps), Reader receive stamps, `Backend.status().link`, ticker overrun counters | NFR-001..004 tests (Validator F) |

---

## 17. Reuse from Thrust_Stand_HAW (@9473c68, read-only, D-02)

Copied files carry `# Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/<path> @9473c68 (<as-is | adapted:
what>)` (SYS-010). Copies are taken from a pinned snapshot, never by running tools in the reference (F-B-11).

| Thrust_Stand source | Bend stand target | Verdict | Notes |
|---|---|---|---|
| `io/crc.py` | `io/crc.py` | as-is | variant confirmed by the ICD |
| `io/framing.py` | `io/framing.py` | as-is | LEN max per ICD (proposal 256) |
| `io/transport.py` | `io/transport.py` | as-is + | add `wire_log`, `sim` endpoint in the factory |
| `io/reader.py` | `io/reader.py` | as-is | async queue → Pipeline (DATA + EVENT in order) |
| `io/ports.py` | `io/ports.py` | as-is | enumeration only (D-06) |
| `io/win_hotkey.py` | `io/win_hotkey.py` | as-is (rename) | callback → `Backend.halt("hotkey")` |
| `io/protocol.py` | `io/protocol.py` | adapt (rewrite tables) | new command set, DATA/STATUS/EVENT codecs from the ICD |
| `io/simulator.py` | `io/sim/board.py`, `fw_logic.py` | adapt architecture | keep loopback, `_cmd_<name>` dispatch with NACK order, fault injector, NVM-as-JSON; replace thrust physics by stepper/homing/specimen |
| `io/sim_sensors.py` | `io/sim/models.py` (`Hx711Model`) | adapt | `_Afe` rate/gain/settle/saturation reused; add filter, spikes, drift |
| `io/elevation.py` | `io/elevation.py` | as-is (optional) | warns that the hotkey is not delivered to elevated windows |
| `core/link.py` | `core/link.py` | adapt | FrameWriter + CommandChannel kept; new lanes/retry classes, motion epoch, `PrioritySender`, `StopConfirmer` (from `EstopConfirmer`), heartbeat 150 ms |
| `core/device.py` | `core/device.py` | adapt by splitting | keep ≈ 900 generic lines (connect, params, write_and_verify, stream, VALID, supervisor/reconnect); drop arm/throttle/DShot/temp/self-test; new motion + stop/clear section |
| `core/model.py` | `core/model.py` | adapt | LinkState, DeviceInfo, BoardStatus, flags, VerifyItem |
| `core/events.py`, `observers.py`, `errors.py` | same | as-is (rename) | error classes extended (§14) |
| `core/liveness.py`, `clock.py`, `timing.py` | same | as-is | thresholds per §4.6 |
| `core/linkstats.py` | same | adapt | counter names |
| `core/params.py` | `core/params.py` | adapt | bend hard rules, write order, `*.bbboard.json` (no jsonschema) |
| `core/params_gen.py` | generated | regenerate | Integrator's generator |
| `core/samples.py`, `pipeline.py`, `sensors.py`, `channels.py` | same | adapt | TimeUnwrapper, GapCounter as-is; new dtype, classifier, scaling, derived stages |
| `core/ringbuffer.py` | same | as-is | — |
| `core/motor_gate.py` | `core/gates.py` | adapt pattern | GateItem/Severity/GateResult; new gate set |
| `core/backend.py` | `core/backend.py` | adapt | facade per §15 |
| `core/selftest.py`, `core/esc_telemetry.py` | — | not reused | thrust-specific |
| TS-SWD §7.2/7.3 (calibration, tare), §8 (limits), §9 (sequencer), §10 (recording/report) | `core/calibration`, `tare.py`, `safety.py`, `sequencer/`, `recorder.py`, `report.py` | new code from adapted design | at 9473c68 these modules did not exist (R3 §5.2); later TS commits contain `calibration.py`, `tare.py`, `capture.py` — design reference only unless re-pinned (F-B-11) |
| `tests/unit/test_layering_packaging.py` | `03_SW/tests/unit/test_layering.py` | as-is (rename) | SW-PLT-002 |
| `tests/unit/core/scripted_board.py`, `sim_rig.py`, `tests/unit/io/test_vectors.py` pattern | `03_SW/tests/unit/...` | adapt | command-channel edge cases, Device + SimBoard rig, vector tests |
| `__main__.py`, `run_sim.bat`, `pyproject.toml` | `bend_stand/__main__.py`, `03_SW/run_sim.bat`, `03_SW/pyproject.toml` | adapt | fewer dependencies (KD-11) |

Lessons designed in from the start (R3 §5.4, §1.7): port configured once (SWD-M1-01), heartbeat margin
(SWD-M1-03), act-before-publish (SWD-M1-04/-11), only the writer writes (SWD-M1-06), timeout only after an
empty read (SWD-M2-01), no flash while moving/streaming handled by FW + SW gating of SAVE (SWD-M2-02),
host-stamp wrap counting (SWD-M3R1-02), gates include link/liveness (SWD-M3R1-03), weak observers / GC policy
boundary (SWD-PM3-07), SAVE timeout scaled by backlog (M3-12), no relative moves (P4/P11), plot performance
designed with a perf smoke test from M3 (SWD-PM3-05).

---

## 18. Calculation catalogue (`calc`, pure functions, pytest vectors) — SW-PLT-002

| Function | Purpose | Vector (R4 §12) | Req |
|---|---|---|---|
| `units.n_to_kgf`, `G0` | N ↔ kgf | TV-U | SYS-003, SW-RT-004 |
| `rounding.round_half_away` | float → int | TV-TC (`um_to_steps`) | SYS-003 |
| `motion.um_to_steps`, `steps_to_um` | wire µm ↔ steps (sim, travel cal) | TV-TC | FW-MOT-002 (sim), SW-CAL-002 |
| `motion.plan_trapezoid`, `move_duration_s`, `stop_distance_mm` | plan durations, sim kinematics | TV-M planner | SW-SEQ-002, SYS-008 |
| `motion.ramp_periods`, `avr446_periods` | reference ramp (FW vector cross-check, sim) | TV-M | SYS-008 |
| `stats.robust_window_stats`, `se_ar1`, `drift_slope`, `window_acceptance` | capture statistics | TV-RS | SW-CAL-006, SW-TARE-002/003 |
| `loadcal.load_calibration`, `point_acceptance`, `low_span` | fit + status | TV-LC | SW-CAL-006/007/008 |
| `loadcal.fw_raw_limits` | FW thresholds | TV-T | SAF-SW-002 |
| `travelcal.travel_cal_step1/2`, `travel_plausibility`, `target_for_steps` | travel calibration | TV-TC | SW-CAL-002/003 |
| `tare.force_n`, `tare_acceptance`, `tare_offset_warning` | runtime force, tare checks | TV-T | SW-TARE-002/003 |
| `limits.evaluate_force_limit`, `evaluate_travel_limit`, `limit_margin_warning` | SW limits | new vectors (B) | SAF-SW-001/006 |
| `timebase.unwrap_us`, `plausible_wraps`, `frame_gaps` | time base, losses | TV-L | IF-006/007, SW-ACQ-004 |
| `derived.*` (speed, force_rate, stiffness_ols/tangent/secant, work_trapz, running_peak, break_detect, rolling_std, sample_rate, bend3p_stress/strain/modulus) | derived channels | TV-D + new | SW-RT-004, SW-REP-004 |
| `steady.steady_state` | report windows | TV-SS | SW-REP-002 |
| `trim.approach_band`, `force_to_raw_stop`, `k_est_from_approach`, `trim_step` | load steps | TV-C | SW-SEQ-006 |
| `path.planned_path` | sequence chart path | new vectors (B) | SW-SCH-001 |

Generators (`core.sequencer.generators`) and the plan expansion are pure as well and tested with expected step
lists / expanded plans (SW-WIZ-001, SW-SEQ-002).

---

## 19. Test hooks (Implementer B unit tests; Validator F and Integrator use the same hooks)

- `03_SW/tests/unit/**` (B): every test tagged `@pytest.mark.req("SW-…")` + `# Verifies:`; all R4 §12 vectors;
  codec vs shared vectors; layering test (SW-PLT-002); CommandChannel with `ScriptedBoard` (lost responses →
  no duplicated motion, IF-005; STOP first with a full queue, IF-011); engines with `FakeClock` + synthetic
  sample batches; simulator logic with `LockstepSimClock`.
- Component rig `SimRig` = Backend + SimBoard + fake hotkey backend (no Qt), used for SAF-SW timing tests
  (`wire_log`), recorder integrity (1 h at 80 Hz in accelerated lockstep), sequence scenarios.
- Validator F (`tests/validation/**`) uses only the public API (§15) and the ICD/R4 vectors; Integrator
  (`tests/integration/**`) uses `tcp://` against the FW twin and the sim-vs-twin differential tests.

---

## 20. Requirement → design traceability

"B" = designed here; "D" = GUI part in `SW_design_GUI.md` (backend support named). Milestones per SRS.

| ID | Design (this doc) | Modules | Share |
|---|---|---|---|
| SYS-002 (PC part) | §6, §7, §9, §10, §11 | `calc`, `core` | B |
| SYS-003 | §0, §5.4, §18 | `calc.units`, `calc.rounding`, `core.device` | B (+ D N/kgf selector) |
| SYS-008 | §12 | `io.sim` | B |
| SYS-010 | §17 | origin notes | B |
| SAF-SW-001 | §6.1, §6.2, §5.6 | `core.safety`, `core.gates`, `calc.limits` | B |
| SAF-SW-002 | §6.3 | `core.safety.ThresholdManager`, `calc.loadcal` | B |
| SAF-SW-003 | §4.6, §6.4 | `core.link.LinkSupervisor` | B (+ D "LINK LOST" display) |
| SAF-SW-004 | §5.5, §5.6 | `core.gates` (CONFIRM items, tokens) | B + D (dialogs, keyboard) |
| SAF-SW-005 | §6.5, §15.2 | `core.model.Indicators`, `gates.clear_procedure` | B + D (display) |
| SAF-SW-006 | §6.6, §10.1 | `calc.limits.limit_margin_warning` | B (+ D warning) |
| IF-001 | §4.2 | `io.protocol` (from ICD only) | B |
| IF-002 | §4.1 | `io.transport.SerialTransport` | B |
| IF-003 | §4.2 | `io.framing` | B |
| IF-004 | §4.2 | `io.crc`, `io.framing` | B |
| IF-005 | §4.4 | `core.link.CommandChannel` | B |
| IF-006 | §7.1 | `io.protocol.DATA_DTYPE`, `core.pipeline` | B |
| IF-007 | §7.3 | `calc.timebase.frame_gaps`, `core.linkstats` | B |
| IF-008 | §5.2 | `core.device.connect`, `Compat` | B (+ D read-only state) |
| IF-009 | §5.4 | `core.motion`, `core.device` | B |
| IF-010 | §2, §4.2 | `core.params_gen` (generated), vector tests | B (consumer) |
| IF-011 | §4.5 | `core.link.PrioritySender`, `FrameWriter` | B |
| IF-012 | §5.1 | `core.device`, `io.protocol` | B (codes per ICD) |
| SW-PLT-001 | §1 | `pyproject.toml` | B |
| SW-PLT-002 | §0 KD-01, §18 | layering test, `calc` | B |
| SW-PLT-003 | §5.2, §15.2 | `core.device`, `core.linkstats` | B + D (statistics view) |
| SW-CFG-001 | §5.3 | `core.params`, `params_gen` metadata | B + D (typed form) |
| SW-CFG-002 | §5.3, §13.1 | `core.params` board-config file | B + D |
| SW-CFG-003 | §5.3 | `Device.write_and_verify` | B + D (status column) |
| SW-CFG-004 | §5.3 | `save/load/default_params`, CFG_DIRTY | B + D (buttons, confirmation) |
| SW-LIM-001 | §5.4, §6.1 | `core.motion`, `core.safety` | B + D (editor) |
| SW-LIM-002 | §6.1, §6.3 | `LimitConfig`, `ThresholdManager` | B + D |
| SW-LIM-003 | §8, §13.5 | `core.session`, recorder snapshot, report | B |
| SW-META-001 | §11, §13.6 | `core.metadata` | B + D (form) |
| SW-META-002 | §8, §13.6, §13.7 | `core.metadata`, recorder sidecar | B + D |
| SW-RT-001 | — (GUI) | backend: `stop()` API, `DataView` | **D** (`SW_design_GUI.md`, docks/layout) |
| SW-RT-002 | §7.4 | `core.channels.ChannelRegistry` | B + D (toggles, greyed) |
| SW-RT-003 | §7.5 | `DataView.snapshot/xy` | B + D (views) |
| SW-RT-004 | §7.4, §18 | `calc.derived`, pipeline | B |
| SW-RT-005 | §7.5 | `DataView.latest()` states | B + D (readouts) |
| SW-MAN-001 | §5.4 | `MotionController.move_to` | B + D (slider sends on release) |
| SW-MAN-002 | §5.4 | `move_by`, commanded target | B (+ D entry) |
| SW-MAN-003 | §5.4 | commanded-target accumulator | B (+ D buttons); see F-B-09 |
| SW-MAN-004 | §5.4 | jog session + refresh | B + D (press/release/focus) |
| SW-MAN-005 | §5.4 | caps, STOP | B + D |
| SW-MAN-006 | §5.4 | enable/disable/home/x_zero/VALID | B + D |
| SW-STOP-001 | §4.5, §5.5 | `PrioritySender`, `terminate_all` | B + D (button everywhere, no focus) |
| SW-STOP-002 | §4.4, §5.5 | `io.win_hotkey`, `StopConfirmer` | B |
| SW-STOP-003 | §5.5, §10.3 | pipeline event dispatch, `terminate_all`, `clear_stop` | B + D ("Clear stop") |
| SW-STOP-004 | §10.5 | executor pause/resume | B + D |
| SW-ACQ-001 | §8 | `stream_start/stop` | B + D (toolbar) |
| SW-ACQ-002 | §8 | `core.recorder` | B (+ D buttons) |
| SW-ACQ-003 | §8 | `SampleTaker`, `CaptureHub` | B (+ D button) |
| SW-ACQ-004 | §7.3, §8 | gap counters, recorder failure path | B (+ D alarm) |
| SW-CAL-001 | §9.1 | Engine protocol | B + D (wizard pages, STOP) |
| SW-CAL-002 | §9.3 | `TravelCalEngine`, `calc.travelcal` | B |
| SW-CAL-003 | §9.3 | `travel_plausibility`, ConfirmRequest | B (+ D dialog) |
| SW-CAL-004 | §9.3, §9.6 | write/verify/SAVE, store | B |
| SW-CAL-005 | §9.4 | `LoadCalEngine`, `CaptureHub` | B |
| SW-CAL-006 | §9.4 | `calc.loadcal.point_acceptance`, `calc.stats` | B |
| SW-CAL-007 | §9.4 | `calc.loadcal.load_calibration` | B (+ D residual plot) |
| SW-CAL-008 | §9.4, §7.2 | LOW_SPAN, extrapolation flag | B (+ D marking) |
| SW-CAL-009 | §9.6, §13.2 | `CalibrationStore`, AFE compare | B |
| SW-TARE-001 | §9.5, §15.1 | `Backend.tare`, events | B + D (toolbar, progress) |
| SW-TARE-002 | §9.5 | `TareEngine`, `calc.tare` | B |
| SW-TARE-003 | §9.5 | `gates.tare_gate`, acceptance | B |
| SW-SEQ-001 | §10.1 | `sequencer.model`, `validate` | B + D (editor) |
| SW-SEQ-002 | §10.1, §10.2 | loops, `expand` | B |
| SW-SEQ-003 | §10.3 | executor timeline (device time) | B |
| SW-SEQ-004 | §10.3 | VALID windows + window log | B |
| SW-SEQ-005 | §10.3 | `gates.sequence_start_gate` | B |
| SW-SEQ-006 | §10.4 | `sequencer.loadstep`, `calc.trim` | B |
| SW-SEQ-007 | §10.5 | guards, controls | B + D (buttons) |
| SW-WIZ-001 | §10.6 | `sequencer.generators` | B + D (wizard UI) |
| SW-WIZ-002 | §10.6 | `insert_block` | B + D |
| SW-SEQF-001 | §10.7, §13.4 | `sequencer.seqfile` | B |
| SW-SCH-001 | §10.2 | `calc.path.planned_path` | B + D (chart) |
| SW-SCH-002 | §7.5, §10.3 | `sequence_trace`, `SeqStatus` | B + D (marker ≥ 10 Hz) |
| SW-REP-001 | §11 | `core.report` | B |
| SW-REP-002 | §11 | `calc.steady` | B |
| SW-REP-003 | §11 | offline report CLI | B |
| SW-REP-004 | §11 | `calc.derived.bend3p_*` | B |
| NFR-001 | §7.5, §16 | ring buffer, `DataView` | B + D (rendering) |
| NFR-002 | §4.5, §16 | priority path | B + D (button wiring) |
| NFR-003 | §4.5, §16 | hotkey thread, priority path | B |
| NFR-004 | §7.5, §8, §16 | bounded storage, recorder | B |
| NFR-005…008 | — | FW requirements; the simulator mirrors NFR-008 response timing only | n/a |
| SAF-FW-*, FW-MOT/HOM/SW/CMD/STR/CFG/NVM-* | §12.2 | **simulated** in `io.sim.fw_logic` (test infrastructure; verification is on FW/twin) | B (sim) |

Coverage: **SW 59/59, SAF-SW 6/6, IF 12/12, NFR-001…004 4/4** have a design element; SW-RT-001 is GUI-only
(backend support listed).

---

## 21. Open items and findings

| ID | To | Finding / question | Proposed resolution |
|---|---|---|---|
| F-B-01 | Integrator | Retry policy must be per command in the ICD. Motion commands must not be auto-retried (a retried MOVE after a PAUSE-button stop would clear PAUSED and move). | ICD retry table = §4.4 classes (RETRY / CONFIRM / ONCE_PRIORITY / VERIFY). |
| F-B-02 | Integrator | Exact travel calibration and VERIFY-outcome resolution need the step count and the active target: µm on the wire is ambiguous by ±1 step when steps/mm > 1000 (D-27 candidate 1280). | Add `pos_steps` (i32) and active move target / motion state to GET_STATUS and `pos_steps` to MOVE_DONE. |
| F-B-03 | Integrator, Orchestrator | GUI Pause on the wire: SRS §3.2 lists "GUI Pause" with the FW PAUSED latch, but IF-012 has no PAUSE command; which PC command clears PAUSED ("PC clear")? | Either a PAUSE command (controlled stop + PAUSED) or STOP(controlled) with PAUSED kept on the PC (current design, §5.5); name the clearing command. |
| F-B-04 | Integrator | MOVE_DONE needs: reason (reached / load threshold / bound / stopped + cause), final position, `t_us`; MOVE_UNTIL_LOAD needs the comparison sense (≥ / ≤ raw_stop, sign(K)·direction). | Define in the ICD. |
| F-B-05 | Integrator | DATA/EVENT details the backend relies on: fallback-frame "no AFE data" marker (FW-STR-005), full status bit list (incl. `DRV_PWR`, R5 §8), HALT source, EVENT code list (stop causes, VALID_CLEARED cause, RESUME_REQUEST, BOOT), SET_VALID response `t_us`, STOP/HALT confirmation criteria with the stream off (GET_STATUS). | Define in ICD 1.0. |
| F-B-06 | Integrator | Simulator and FW twin should share one world-control vocabulary (specimen, buttons, E-stop, faults) and a JSON scenario format so differential tests drive both identically. | Agree on `bird.bend.simscenario` v1 (§12.4); B provides the sim side. |
| F-B-07 | Orchestrator | SAF-SW-002 says "refuse" thresholds outside the FW range. With K below nominal the 110 % FS level can map beyond ±7 151 121 counts and block all motion; clamping **inward** to the range cap would be safe-side (earlier trip). | Allow clamp-inward with a warning, or keep refusal (current design). |
| F-B-08 | Orchestrator | Without calibration + tare, motion is disabled while SW load limits are enabled (SAF-SW-001); first use needs load calibration → tare → motion, or the operator disables SW load limits (FW 110 % default stays). Also `safety.zero_raw` stays 0 without a tare, so the FW HOME 5 % FS check and the idle-disable band are referenced to 0 instead of the cell's real zero (offset up to ≈ ±1 % FS + HX711 offset). | Confirm the first-use flow; consider requiring a tare (or a "zero reference" capture) before HOME. |
| F-B-09 | Orchestrator, Validator F | SW-MAN-003 acceptance ("three clicks → targets 11, 12, 13") vs FW-MOT-004 (MOVE refused while moving): the design sends the latest pending target at MOVE_DONE, so rapid clicks produce 11, 13 on the wire (final target 13). | Accept latest-wins (proposed) or require FIFO targets. |
| F-B-10 | Orchestrator | `home.max_load_raw`, `safety.release_band_raw`, `safety.load_regrow_raw` are nominal counts; with a calibrated K the SW could rewrite them with the thresholds. | Optional SAF-SW-002 extension; not designed in v0.1. |
| F-B-11 | Orchestrator, Researcher | `Thrust_Stand_HAW` HEAD is now **0f4e55e** (moved since the R3 pin 9473c68). Modules `calibration.py`, `tare.py`, `capture.py`, `calc_stage.py`, `stand.py` exist in the working tree but not at the pin. Role rules forbid running git there. | Provide a `git archive` snapshot of 9473c68 (or re-pin to 0f4e55e) under `00_System/research/ref_snapshots/thrust_stand/` before P2 copies. |
| F-B-12 | Orchestrator | Ownership not assigned: `03_SW/src/bend_stand/__init__.py`, `__main__.py`, `03_SW/run*.bat`, `03_SW/tests/conftest.py`. | Proposal: B owns package root files and `tests/conftest.py`; D owns `gui/app.py`. |
| F-B-13 | Orchestrator | Tare is session-only (KD-13): after an application restart motion with load limits needs a new tare. | Confirm (safe side; R4 §7 tare per test). |
| F-B-14 | Orchestrator / PO | Load-step trim not converged after 10 iterations: default "stop the sequence" vs "capture anyway, flag NOT_ON_TARGET". | Default `stop`, per-sequence option `continue`. |
| F-B-15 | Integrator | SW travel limits during JOG are enforced by the PC with a predictive margin (≤ ≈ 0.6 mm overshoot at 10 mm/s). An optional JOG argument `bound_um` (like MOVE_UNTIL_LOAD) would let the FW stop exactly at the SW limit. | Add `bound_um` to JOG (0 = soft limit). |
| F-B-16 | Implementer D | The GUI consumes §15 (facade, snapshots, events, rules 1–9); `core.api` Protocols are provided for fakes. | D references §15 in `SW_design_GUI.md`; API changes go through B. |
| F-B-17 | Validator F | Verification hooks: `VirtualTransportPair.wire_log`, Reader receive stamps, VALID window log in `meta.json`, `SimControl` fault injection, `LockstepSimClock`. | Use in SW_test plan (SAF-SW-001, NFR-002/003, SW-SEQ-004, SW-ACQ-002). |
| F-B-18 | Orchestrator | Dependency set kept minimal (KD-11): no jsonschema, Jinja2, matplotlib, platformdirs, psutil, pyyaml (TS used them). HTML report figures are inline SVG; memory measured via ctypes. | Confirm for SW-PLT-001. |
| F-B-19 | Integrator | `params_gen.ParamMeta` must expose `moving_ok` (settable while moving), enum code→NAME, f32 `decimals`, NVM flag, group labels and the dictionary hash/version. | Generator output per §2. |
| F-B-20 | Orchestrator | Interpretations to confirm: (a) SW-SEQ-003 dwell "max(step time, settle + capture)" is measured from `t_reached`; (b) sequence travel targets are in test travel (`x − x_zero`, frozen at start) by default (`travel_ref`), machine mm optional. | Confirm or amend SRS. |
| F-B-21 | Implementer D | `safety.load_raw_min/max` and `safety.zero_raw` are owned by the ThresholdManager; the config form must show them read-only (a manual write desynchronises SAF-SW-002). | Read-only rows with a note. |
| F-B-22 | Orchestrator | SW-CAL-009 requires only a warning on an AFE-configuration mismatch; the design additionally treats the load-limit input as invalid (motion disabled with load limits) because K is wrong for another gain. | Confirm the stricter behaviour. |

---

## 22. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-03 | Implementer B | Initial P1 design: architecture, threading, link client, Device/Motion API, safety supervisor, pipeline, recorder, calibration/tare engines, sequencer, report, simulator, file formats, GUI API contract, reuse table, traceability (SW 59/59, SAF-SW 6/6, IF 12/12, NFR-001…004), findings F-B-01…22. |
