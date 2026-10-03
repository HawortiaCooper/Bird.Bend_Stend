# Bird Bend Stand — PC Software Design (SW_design)

| Item | Value |
|---|---|
| Version | **0.3.1 — DRAFT for the P1 gate** (aligned to **ICD v0.4 / v0.4.1** (D-34), SRS v0.3, D-29…D-34, SW_test_plan SWD-P1-01…18 / C1–C7; v0.2 answered GRQ-B-01…18) |
| Date | 2026-10-03 |
| Owner | Implementer B (SW backend): overall architecture + backend (`core`, `io`, `calc`), package root (`bend_stand/__init__.py`, `__main__.py`), launch scripts `03_SW/run*.bat`, `03_SW/tests/conftest.py` (D-29 n). GUI design: `03_SW/docs/SW_design_GUI.md` (Implementer D), which consumes §15 of this document. |
| Binding inputs | `00_System/specs/SRS.md` **v0.3** (SAF-SW-, SW-, IF-, NFR-001..004, §3.2, §5.2; new SW-LIM-004; the v0.4 draft was cross-checked — same 82 SW-side IDs, traceability §20 unchanged), `DECISIONS.md` D-01…D-33, **`ICD_protocol.md` v0.4** (final P1 draft; PROTO 1.0, PAYLOAD 1; STATUS 86 B with `pause_src`; PAUSED latch with BLOCK bit PAUSED (D-30); RESUME 0x3C VERIFY class (D-31); hard rule H5 (D-33 a); homing at START only; `state_schema` 2; frozen sim/twin vocabulary v2 in tools/README; `units_vectors.json`) and its **v0.4.1** delta (D-34: HALT_CLEAR / ESTOP_CLEAR / FAULT_CLEAR become VERIFY class on the priority path), `03_SW/docs/SW_test_plan.md` (Validator F, verdict YES WITH CONDITIONS; SWD-P1-01…18), `protocol.yaml` + generated `core/protocol_gen.py` (names/codes), `params.yaml` **dict_version 3** (PARAM_DICT_HASH **0xF0376293**; since dict 2: `home.ref_switch` 0x0401 retired, `drv.k1_weld_ms` 0x0705 added; dict 3: `motion.steps_per_mm` default 800 (D-27 closed), `afe.timeout_ms` default 250 + H5 (D-33 a)) + generated `core/params_gen.py`, `00_System/tools/{ref_codec.py, ref_cmdcheck.py, README.md, vectors/}` (check vectors ICD v0.4: 509, `state_schema` 2; `units_vectors.json`), R3 (§1.6–1.7, §5, §6, §7), R4 (§2, §4–§12), R5 (§0, §5.5, §8), `SW_design_GUI.md` v0.1 §11.2 (GRQ-B-01…18), §14 (GF-04/05/07/10) |
| Orchestrator / PO decisions after D-29 | **D-30**: PAUSED is a motion-blocking FW latch (BLOCK PAUSED); controlled-stop clean halt only when step period > 2 ms **and** stop distance ≤ 1 step. **D-31**: dedicated **RESUME 0x3C** clears only PAUSED and is refused while HALT/ESTOP/fault is latched; HALT_CLEAR clears HALT and PAUSED; Resume = RESUME, then re-issue. **D-32** (PO Q26): a load step that does not reach its load travels to the end of travel, ends NOT_REACHED and stops the sequence. **D-33**: (b) sequence start refused on POS_UNCERTAIN, AFE_RATE_MISMATCH, PAUSED, ALM, DRV_PWR off; (c) ALM during a sequence → controlled STOP + sequence ends; (d) load-step bound = nearer of soft limit and enabled SW travel limit, timeout 1.2 × travel time + 10 s; (i) runtime Python 3.14; (k) BLOCK PAUSED refusal is an expected outcome. **D-27 closed** (PO): 4000 p/rev closed loop → nominal 800 steps/mm (params default 800 from ICD v0.4). No-specimen mode keeps the calibrated SAF-SW-002 thresholds when a valid calibration + tare exist; only without them the FW thresholds stay at the nominal defaults (SW-LIM-004, §6.7). |
| Reference | `E:\Bavovna\Drone\Thrust_Stand_HAW` (read-only, D-02): copies are taken from its **current HEAD** with the commit hash recorded in every origin note (D-29 m; design citations "TS-SWD §x" were checked at 9473c68) |
| Status of code | **No application code yet** (P1). `03_SW/pyproject.toml` is a design artefact (§1). The M1 work breakdown is §22. |

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
| KD-03 | **Priority TX path**: STOP, HALT, **PAUSE**, HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR are written directly under the frame-writer lock, bypassing lanes, queues, token bucket and outstanding-command limits; the lock is held for one frame at a time (largest frame 168 B ≈ 1.8 ms, ICD §2). | IF-011, NFR-002/003, SW-STOP-001/002, ICD §2.4 |
| KD-04 | **Motion epoch**: every STOP/HALT/PAUSE sent and every stop indication from the FW increments an epoch; queued or pending motion commands of an older epoch are dropped. Motion commands are **never auto-retried** (ICD §9.3 VERIFY class, SD-08); their outcome after a timeout is resolved by GET_STATUS (`motion_state`, `target_um`, latches). Only idempotent commands are retried (new SEQ, newest payload). | IF-005, ICD §9.3, R3 §1.7 P4/P11 |
| KD-05 | SW limits are evaluated **in the Pipeline thread on every DATA frame**; a trip writes STOP before anything is published ("act first, publish after"). | SAF-SW-001, R3 §5.4 SWD-M1-04 |
| KD-06 | Motion commands are allowed only while the stream runs and is fresh, the FW load thresholds are verified for the active calibration + tare, and every enabled SW limit has a valid input (pure gate functions, §5.6). **No-specimen mode** (D-29 h) is the only way to move with SW load limits off: explicit confirmation, session-only, banner, FW load limit at its nominal default (§6.7). | SAF-SW-001/002, SW-SEQ-005, D-29 h |
| KD-07 | **Absolute targets only** on the wire; the `MotionController` keeps the *commanded target* accumulator (±0.1/1/10 mm add to it) and a **latest-wins** pending target while the FW is busy (D-29 i). | SW-MAN-001..003, IF-009, D-23, D-29 i |
| KD-08 | Calibration, tare and sequencer are **Qt-free state machines** with an immutable state snapshot + events; the GUI wizards only render the snapshot and call engine methods. | SW-CAL-001, SW-TARE-001, PO-SW-8.WIZ |
| KD-09 | **Device time** (unwrapped `t_us`) is the only time base for settle/capture windows, VALID boundaries, steady-state extraction and derived rates; host time is used only for timeouts, UI and link supervision. | R4 §8.5, §9, SW-SEQ-003/004 |
| KD-10 | Recordings keep raw counts, K, tare_raw and every event, so forces and the report can be recomputed offline with another calibration/tare. | SW-REP-003, R4 §7 |
| KD-11 | Minimal runtime dependencies: PySide6-Essentials, pyqtgraph, numpy, pyserial (+ stdlib). No jsonschema/Jinja2/matplotlib/platformdirs: hand-written schema validators, HTML report with inline SVG figures, `%APPDATA%` paths via `os.environ`. | SW-PLT-001 |
| KD-12 | In-process simulator (`io.sim`) uses the **same** codec and the generated parameter dictionary; its command acceptance replays **every** `check_vectors.json` vector (differential check against the Integrator's oracle `ref_cmdcheck`, §12.5) and its behaviour is compared with the FW host twin over `tcp://`. | SYS-008, D-07, ICD §12 |
| KD-13 | Tare is **session-only** (valid for the connected board UID while the application runs, never loaded from a file, D-29 j); calibrations are persisted with an active copy loaded at start. | SW-TARE-002, SW-CAL-009, D-29 j |
| KD-14 | Heartbeat PING after **150 ms** of TX idle (ICD §9.2: 150–200 ms, gap ≤ 250 ms), gated by Reader/Pipeline liveness, so a hung backend lets the FW link watchdog trip. | SAF-SW-003, ICD §9.2, R3 §5.4 SWD-M1-03 |
| KD-15 | **Pause = FW PAUSE command** (0x3B, D-29 a): GUI Pause and the physical PAUSE button produce the same FW state (PAUSED latch, controlled stop, VALID cleared); the sequencer reacts to the FW indication, not to the button that caused it. **PAUSED blocks every new motion start in the FW (D-30)**; it is cleared by **RESUME 0x3C** (only PAUSED; refused while HALT/ESTOP/fault is latched, D-31) or by HALT_CLEAR (clears HALT and PAUSED). Resume = RESUME, then re-issue the interrupted step's motion — always a PC action. | SW-STOP-004, D-14, D-26, D-29 a, D-30, D-31, ICD §5.5 |
| KD-16 | **Active travel calibration** = `active_travel.json` steps/mm = board NVM value. A wizard writes trial values to board RAM only; every exit other than ACCEPT restores the previous value as soon as the board is reachable and idle, and a persisted *restore-pending* record survives link loss and application crashes (§9.3.1, GF-05). | SW-CAL-001/004, GRQ-B-17 |

---

## 1. Technology stack and packaging

- **Supported runtime: Python 3.14** (D-33 i; the application ships with its own venv, no separate 3.11 test). `requires-python = ">=3.11"` stays as the declared floor of SW-PLT-001, but only 3.14 is verified. Type hints everywhere, `from __future__ import annotations`.
- **Requirements files** (owner B, D-31 / F-B-31): `03_SW/requirements.txt` (runtime, pinned to the verified versions: PySide6-Essentials 6.11.2, pyqtgraph 0.14.0, numpy 2.5.3, pyserial 3.5) and `03_SW/requirements-dev.txt` (`-r requirements.txt` + pytest 9.1.1, pytest-qt 4.5.0, pytest-cov 7.1.0, pytest-randomly 5.0.0, PyYAML 6.0.3 for the Integrator's generators). Install: `.venv\Scripts\python -m pip install -r 03_SW\requirements-dev.txt`. `pyproject.toml` keeps the same set as ranges; a unit test checks that both files agree.
- Runtime: `PySide6-Essentials` (GUI only), `pyqtgraph` (GUI only), `numpy` (pipeline, ring buffer, calc), `pyserial` (transport).
- Test: `pytest`, `pytest-qt` (GUI, offscreen), `pytest-cov`, `pytest-randomly` (tests must be order-independent; seed printed and reproducible with `-p randomly -p "randomly_seed=…"`).
- `03_SW/pyproject.toml` (written with this document): package `bend_stand` (src layout), extras `test`, pytest markers `req(*ids)`, `unit`, `component`, `integration`, `validation`, `gui`, `winint` (interactive Windows session: real display/keyboard, hotkey), `rt` (real-time clock, not lockstep), `perf`, `soak`, `hil`, `slow` — `--strict-markers` is on (SWD-P1-16); coverage omits the generated `core/params_gen.py`, `core/protocol_gen.py` and `gui/*` (D measures GUI coverage separately).
- Entry points (`bend_stand/__main__.py`, owner B, D-29 n): `python -m bend_stand [--sim[=scenario.json]] [--port COM7 | --port tcp://127.0.0.1:5760] [--session file] [--headless [--duration s]]` and the script `bend-stand` (`bend_stand.__main__:main`). Without `--headless`, `main()` parses the arguments (the selected endpoint as **`args.endpoint: str | None`** — `"sim"`, `"sim:<scenario.json>"`, `"COM7"` or `"tcp://host:port"`; `None` = start disconnected), builds the `Backend` **without starting it** and returns `bend_stand.gui.app.run(backend, args)` (owner D), which calls `backend.start()` before showing the window, connects to `args.endpoint` if given, and calls `backend.shutdown()` on exit (GF-14); `--headless` connects, streams and prints link statistics (M1 smoke test, no Qt import). **No COM port is ever opened unless the operator selected it** (`--port COMx` or the GUI connect action, D-06); without `--port` the GUI starts disconnected and `--headless` requires `--sim` or `--port`. `python -m bend_stand.io.sim.server --port 5770 [--scenario f]` serves the simulator out of process over TCP (GUI perf runs, GRQ-B-16). Launch scripts: `03_SW/run.bat` (GUI, disconnected), `03_SW/run_sim.bat` (GUI + `--sim`), both via `.venv\Scripts\python`.
- Windows timing: `core.timing.init()` calls `winmm.timeBeginPeriod(1)` (paired `timeEndPeriod`) and `sys.setswitchinterval(0.001)` at start-up (TS-SWD §3.1 "Timing primitives").

## 2. Package layout (`03_SW/src/bend_stand/`)

```
bend_stand/
  __init__.py            __version__, PROTO/PAYLOAD versions implemented (owner B, D-29 n)
  __main__.py            argument parsing → gui.app.run(backend, args) or headless (owner B, D-29 n)
  calc/                  (B) pure functions, numpy/stdlib only, no I/O, no threads, no clock — §18
    units.py             G0, n_to_kgf, kgf_to_n, mm↔µm, FS constants (SRS §2)
    paramrules.py        check_hard_rules H1–H4 (ICD §11.4), write_order (pure; used by core.params and io.sim)
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
    crc.py               crc16_ccitt_false (ICD §2.1, check 0x29B1)
    framing.py           FrameEncoder, FrameDecoder (ICD §2.3: hunt / LEN > 160 / CRC drop-one / 20 ms timeout)
    protocol.py          payload builders and decoders for both directions (PC requests + FW responses/async,
                         the latter also used by the simulator), INFO/STATUS/PARAM_ENTRY/EVENT codecs, DATA
                         numpy dtype, NACK detail decoding — hand-written from the ICD, vector-tested
    transport.py         Transport ABC, SerialTransport, VirtualTransportPair, TcpTransport, transport_factory
    reader.py            ReaderThread (bytes → frames → dispatch)
    ports.py             list ST-LINK VCPs (VID 0x0483) first; never opens a port
    win_hotkey.py        GlobalHaltHotkey (Pause, Ctrl+Break; RegisterHotKey / WH_KEYBOARD_LL fallback)
    sim/                 in-process FW simulator (§12)
      board.py           SimBoard: protocol endpoint + command dispatch + NVM store
      check.py           pure command acceptance (ICD §4–§6): check(state, cmd, payload) -> (status, detail)
      fw_logic.py        FW state machine: enable, homing, motion, jog dead-man, stops/latches, watchdogs
      server.py          out-of-process simulator over TCP (GRQ-B-16 perf runs)
      models.py          DriverModel, SwitchModel, ButtonModel, Hx711Model, SpecimenModel, LinkModel
      scenario.py        SimScenario (JSON), world-control API, fault injector
      clock.py           RealTimeSimClock, LockstepSimClock
  core/                  (B) application logic, no Qt
    params_gen.py        GENERATED from params.yaml (Integrator) — never edited
    protocol_gen.py      GENERATED from protocol.yaml (Integrator, ICD v0.2 §0.3) — never edited
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
    testing.py           test-only fault hooks (recorder failure, thread stall), enabled by BackendSettings
  (03_SW root, owner B)  run.bat, run_sim.bat; tests/conftest.py (fixtures, D-06 port guard, vector loaders)
  gui/                   (D) PySide6 — SW_design_GUI.md
```

Generated modules (Integrator, never edited; both are **leaf modules** importing only the stdlib, so
`io` — codec and simulator — may import them without breaking the layering `calc ← io ← core`; the layering
test whitelists exactly these two `core` modules for `io`):
- `core/params_gen.py` (dict_version **3**, hash **0xF0376293** — ICD v0.4: `motion.steps_per_mm` default 800, `afe.timeout_ms` default 250, H5): `PARAMS`, `BY_ID`, `BY_KEY`,
  `GROUPS`, `PARAM_DICT_HASH`, `PARAM_DICT_VERSION`, `PARAM_COUNT`, `ParamType`, `TYPE_SIZE` and `ParamMeta`
  (`id, key, type, unit, min, max, default, description, enum (code→NAME), group, group_label, label, name,
  moving_ok, nvm, reboot_required, decimals, advanced, enum_labels, srs, flags, size, enum_value(),
  in_range(), pack(), unpack()`) — F-B-19 is satisfied.
- `core/protocol_gen.py` (ICD v0.2 §0.3, from `protocol.yaml`): constants (`MAX_LEN`, `RX_BUF_MIN`,
  `INTERBYTE_TIMEOUT_MS`, `STATUS_LEN = 86`, `JOG_NO_BOUND`, `AFE_NO_DATA`, `RAW_MIN/MAX`, `REBOOT_MAGIC`, …),
  `Cmd`, `CMD_REQ_LEN`, `CMD_RETRY`, `CMD_PRIORITY`, `RetryClass`, `Status`, `BusyDetail`, `NvmDetail`,
  `Block`, `StopMode`, `MulCmp`, `HomeFlags`, `MotionState`, `HomePhase`, `Source`, `ResetCause`,
  `SysFlags`, `Features`, `DataFlags`, `DataStatus`, `Faults`, `IoBits`, `Event` (+ `EVENT_ARG`,
  `EVENT_VALUES`), `StopCause`, `MoveDoneReason`, `HomeFailReason`, `DriverDisabledCause`,
  `PauseClearedReason`, `ParamsDefaultedReason`, `LimitId`, `<ID>_BITS` / `<ID>_NAMES` / `<ID>_DESC`, `TABLES`.
  **No backend module hand-lists a code, bit or name** (ICD §0.3); `Indicators` and the channel registry
  iterate `DATA_FLAGS_BITS`, `DATA_STATUS_BITS`, `FAULTS_BITS`, `SYS_FLAGS_BITS`.
Cross-parameter hard rules **H1–H5** (ICD v0.4 §11.4: H1 `soft_min_um < soft_max_um`, H2 `load_raw_min <
load_raw_max`, H3 `max_step_rate_hz·(pulse_high_ns + pulse_low_min_ns) ≤ 1e9`, H4 `v_max_load_um_s ≤
v_max_travel_um_s`, **H5 `afe.timeout_ms · sps ≥ 2000`** with sps = 10 / 80 from `afe.rate_sps` — SPS10 →
≥ 200 ms, SPS80 → ≥ 25 ms, D-33 a; `E_CONFIG` detail = id of the partner parameter) are implemented once in
the pure `calc.paramrules.check_hard_rules(values) -> list[RuleViolation(rule, key, other_key)]` (used by
`core.params` and the simulator) and tested against every `E_CONFIG` vector of `check_vectors.json`.
`calc.paramrules.write_order(current, target)` follows ICD §11.4: min/max pairs move the outward bound
first; H3 lowers the rate before widening pulses and widens the rate after narrowing pulses; **H5 raises
`afe.timeout_ms` before switching to SPS10 and switches to SPS80 before lowering it**; an `E_CONFIG` item is
retried in a further pass until a pass makes no progress. The SW's AFE stale handling reads
`afe.timeout_ms` (default 250 ms), it never assumes a fixed value.

Parameter names used by the backend (ICD Appendix A, D-29 f / SD-03…SD-06): `motion.steps_per_mm`,
`motion.v_max_travel_um_s` (30 mm/s), `motion.v_max_load_um_s` (20 mm/s), `motion.a_max_um_s2`,
`motion.a_stop_um_s2`, `motion.v_unhomed_um_s`, `motion.max_step_rate_hz` (≤ 100 kHz), `motion.jog_timeout_ms`,
`motion.ena_settle_ms`, `limits.soft_min_um` (max 399 999) / `soft_max_um`, `home.max_load_raw`,
`home.drift_tol_um`, `safety.load_raw_min/max` (±7 151 120 / ±7 151 121 range ends, SD-12),
`safety.zero_raw` (session values, `nvm: false`), `safety.release_band_raw`, `safety.load_regrow_raw`,
`safety.link_timeout_ms`, `io.release_ms`, `io.estop_release_ms`, `drv.pwr_sense_enable`, `drv.k1_weld_ms`
(dict 2, D-29 c), `afe.gain_channel`, `afe.rate_sps`, `stream.fallback_hz`. `home.ref_switch` no longer
exists (dict 2, homing only at START, D-29 b). No backend module uses a parameter key that is not in
`params_gen` (test: string literals of the form `"<group>.<name>"` checked against `BY_KEY`).

---

## 3. Threading model and data flow

### 3.1 Threads

| Thread | Module | Work | Trigger / period | Liveness beat |
|---|---|---|---|---|
| **GUI (main)** | D | Qt event loop; polls snapshots (§15) every 33–50 ms; calls `Backend.stop()`/`halt()` **directly** (non-blocking) and everything else through `*_async` | event-driven | GUI timer tick (`backend.liveness.beat("gui")`) |
| **Reader** | `io.reader` | `transport.read()` (≤ 4096 B, 2 ms timeout, configured once), `FrameDecoder.feed()`; responses → `CommandChannel.on_response()` (resolves futures); DATA/EVENT/LOG → bounded queue (4096) to the Pipeline **in arrival order**. Never writes to the port. | continuous | every loop |
| **Pipeline** | `core.pipeline` | drains the queue (batch closes when the queue is empty or after 8 frames), decodes, unwraps time, detects gaps, scales, derives, annotates, **evaluates safety per sample**, feeds sinks; dispatches FW EVENTs (in order with DATA) | queue-driven, 10 ms idle wake-up | every batch / wake-up |
| **Supervisor** | `core.link.LinkSupervisor` | 5 ms `Ticker`: heartbeat PING, link state, DATA-loss-while-moving reaction, liveness evaluation, `StopConfirmer` repeats (STOP/HALT/PAUSE), jog refresh, GET_STATUS poll (1 Hz while streaming: counters, `sys_flags`, `v_limit_um_s`, `idle_disable_left_s`; 4 Hz while the stream is off: indicator freshness, GRQ-B-02), travel-restore retry (§9.3.1), reconnect trigger | 5 ms | it is the monitor (its death stops the heartbeat → FW watchdog) |
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

`pause_all(source)` is called on `Backend.pause()` (before the PAUSE frame is written) and on FW EVENT
PAUSED (either source): a running `SEQUENCE` swaps to `PAUSED` (state kept, §10.5); every other
motion-owning operation (`TRAVEL_CAL`, `HOMING`) and the `TARE` / `LOAD_CAL_POINT` captures are terminated
exactly as by STOP (reason `PAUSE`, GRQ-B-10 d) — wizards are not resumable. `SAMPLE` (take-sample capture)
is never affected by stops or pauses; its result carries the state flags of its window.

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
- `VirtualTransportPair`: in-memory byte pipes with optional baud pacing (92 160 B/s), latency, unplug/replug.
- **`wire_log` on every transport** (base class, SWD-P1-09 b): a bounded ring (default 200 000 entries) of
  `WireRecord(t_ns, direction TX | RX, frame bytes)` recorded at the write call (TX) and at the read that
  completed the frame (RX); enabled by `BackendSettings(wire_log=True)` for Serial, Virtual and TCP
  transports, so REF runs against the out-of-process simulator (`tcp://127.0.0.1:5770`) have the same
  timestamps as in-process runs; `test_hooks.wire_log()` returns a copy. The simulator server keeps its own
  board-side log (§12.4).
- D-06: no code path opens a COM port by itself; port lists come from `io.ports` (enumeration only).

### 4.2 Framing and codec (`io.framing`, `io.protocol`)

- Frame (ICD §2, IF-003/004): `A5 5A | TYPE u8 | SEQ u8 | LEN u16 (0…160) | PAYLOAD | CRC16`,
  CRC-16/CCITT-FALSE over TYPE..PAYLOAD, low byte first (check `"123456789"` → 0x29B1). `FrameDecoder` = TS
  `io/framing.py` state machine adapted to ICD §2.3: buffer ≥ 336 B; hunt for `A5 5A` (a trailing lone `A5`
  kept); `LEN > 160` → `len_errors`, drop one byte; CRC mismatch → `crc_errors`, drop one byte (a truncated
  frame never swallows the next one); **inter-byte timeout 20 ms** evaluated only after an empty read (TS
  SWD-M2-01) → `timeout_drops`, drop one byte, re-scan; a lone trailing `A5` discarded without counting;
  TYPE invalid for the PC side (§3.1: anything but `0x81..0xBF`, `0xC0..0xCF`) → `unknown_type`, ignored;
  `0xC2..0xCF` reserved asynchronous frames are ignored without counting an error. Output (frames +
  counters) equals `ref_codec.FrameParser` on every `streams` vector.
- **Name tables** (command TYPEs and LENs, retry classes, STATUS codes and detail enums, BLOCK/FAULT/IO bit
  names, DATA `flags`/`status` bits, `sys_flags`, feature bits, motion states, home phases, halt/pause
  sources, reset causes, EVENT codes and their `arg` enums, stop causes, MOVE_DONE reasons, constants) come
  **only** from the generated `core/protocol_gen.py` (ICD v0.2 §0.3, GF-08). `io.protocol` adds the struct
  layouts; a test proves that every name/code decoded from `protocol_vectors.json` equals the generated
  tables. `ref_codec` is imported in tests only, never in production code (tools/README).
- `io.protocol` (hand-written, vector-tested; the vectors are the oracle, ICD §0.1 / R3 P14) provides for
  **both directions**: request builders (PC→FW, used by `core.device`; decoders used by the simulator),
  response encoders/decoders (FW→PC; encoders used by the simulator), `INFO` (44 B), `STATUS` (86 B),
  `PARAM_ENTRY` (7 B, typed via `params_gen.ParamMeta.pack/unpack`, non-zero padding → `INVALID_PADDING`
  error), GET_ALL_PARAMS pages (`page, page_count, n, n × entry`), `EVENT` (16 B: `t_us u32, code u16,
  arg u16, value i32, value2 i32`), `DATA_DTYPE` (numpy structured, little-endian, packed, 18 B: `t_us u32,
  payload_version u8, flags u8, afe_raw i32, setpoint_um i32, frame_seq u16, status u16`) for
  `np.frombuffer` batch decoding (IF-006), and `Nack(status, detail)` with `nack_text(cmd, status, detail)`
  (§4.7).
- Tolerance (ICD §0.2, IF-008): OK responses longer than known are accepted (trailing bytes ignored, vector
  `get_info_resp_longer`); unknown EVENT codes, STATUS codes, reasons and set reserved bits are logged and
  never fatal; a DATA payload whose `payload_version` ≠ 1 is not decoded (`bad_payload`, compat state
  `PAYLOAD_MISMATCH`).

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
- **Lanes** (≤ 4 outstanding in total, ICD §9.3; the priority path is exempt):

  | Lane | Commands | Rule |
  |---|---|---|
  | priority (exempt, §4.5) | STOP, HALT, PAUSE, HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR | never waits, outside the outstanding limit and the token bucket |
  | SAFETY | PING, SET_VALID(0), JOG 0 | 1 reserved slot |
  | CONTROL | RESUME, MOVE_ABS, MOVE_UNTIL_LOAD, JOG ≠ 0, HOME, ENABLE, DISABLE, SET_VALID(1), SET_PARAM of `safety.*` session values | 1 reserved slot |
  | GENERAL | GET_INFO, GET_STATUS, GET_PARAM, GET_ALL_PARAMS, other SET_PARAM, SAVE/LOAD/DEFAULT_PARAMS, STREAM_START/STOP, REBOOT | ≤ 2 |

- **Retry classes** = ICD §9.3 (F-B-01 closed); the class is a property of the command in `io.protocol`
  (`RETRY_CLASS[cmd]`, JOG decided by `v == 0`), never chosen by the caller:

  | Class | Commands | Behaviour |
  |---|---|---|
  | `RETRY` | PING, GET_INFO, GET_STATUS, GET_PARAM, GET_ALL_PARAMS, SET_PARAM, STREAM_START, STREAM_STOP, SET_VALID, **JOG 0** | timeout 100 ms (GET_INFO at connect 200 ms); ≤ 2 retries, each with a **new SEQ** and the **newest** value (latest-wins slots for SET_VALID, threshold SET_PARAMs and JOG 0); a retry superseded by a newer command is dropped; late responses of abandoned SEQs are counted and ignored |
  | `CONFIRM` | STOP, HALT, **PAUSE** | priority path; repeated every 50 ms until confirmed — STOP: ACK or `MOVING = 0` (DATA or GET_STATUS); HALT: ACK or `flags.HALT`; PAUSE: ACK or `status.PAUSED` — ≤ 20 attempts in 1 s; with the stream off the `StopConfirmer` polls GET_STATUS (ICD §9.4); unconfirmed after 1 s → event `stop.unconfirmed` + banner "use the physical STOP / E-stop" |
  | `VERIFY` on the **priority path** | **HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR** (D-34, ICD v0.4.1; the class `ONCE_PRIORITY` no longer exists) | written through the priority path (bypassing lanes, queues, token bucket), timeout 100 ms, **never auto-retried** — a retried clear could wipe a new STOP-button HALT, E-stop or load-limit trip raised in between; a timeout is resolved by GET_STATUS (§4.4.1); NACK detail decoded (§4.7) and shown verbatim |
  | `VERIFY` | **RESUME** (ICD v0.4: VERIFY, CONTROL lane, not sniffed), MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0, ENABLE, DISABLE, SAVE_PARAMS, LOAD_PARAMS, DEFAULT_PARAMS, REBOOT | **never retried** (SD-08). After the timeout (100 ms; SAVE/LOAD/DEFAULT 3000 ms + wire time of the TX backlog; REBOOT: EVENT BOOT or 3 s) the channel sends GET_STATUS and resolves the outcome (§4.4.1); `CommandOutcomeUnknown` only if GET_STATUS also fails. |

  JOG ≠ 0 refreshes (§5.4) are a stream of new VERIFY commands scheduled every **80 ms** on the 5 ms
  Supervisor tick (absolute deadlines; worst case 80 + 5 + jitter < 100 ms, SW-MAN-004, SWD-P1-11) with the
  newest speed and bound; a timed-out refresh is **not** resolved by GET_STATUS (the next refresh supersedes
  it) and is covered by the FW dead-man (`motion.jog_timeout_ms`, SAF-FW-016); 3 consecutive refresh
  **timeouts** → `jog_stop()` + link DEGRADED. A refresh **NACK** ends the jog session at once (no further
  refresh) and is not a timeout.

- **Expected refusals (D-33 k, SWD-P1-03).** A NACK is an answer, not a link failure: only response
  *timeouts* count toward DEGRADED. A motion command (MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0 incl.
  refreshes) refused with `E_STATE` whose BLOCK mask contains **PAUSED** is an **expected outcome** of the
  PAUSE race (a command in flight when PAUSE was pressed): no retry, no DEGRADED count, no error dialog; the
  `MoveTicket` resolves `REFUSED_PAUSED`, the jog session and the pending target are dropped, the manual
  commanded target is resynced, and a running sequence stays (or becomes) PAUSED with its step and phase
  unchanged — the step's motion is re-issued only by Resume. The same holds for a motion command refused
  while the SW already knows a stop/latch is in progress (epoch changed): it is logged, not shown.

#### 4.4.1 VERIFY outcome resolution (GET_STATUS, ICD §7.2)

| Command | "executed" when GET_STATUS shows … | else |
|---|---|---|
| MOVE_ABS | `motion_state` = MOVE_ABS / STOPPING with `target_um` = sent target, **or** a MOVE_DONE received after the request was written (every accepted motion ends with exactly one MOVE_DONE, ICD §5.4; EVENTs are not lost silently — an EVENT SEQ gap makes the outcome `unknown`) | not executed → `CommandNotExecuted` (the caller may re-issue explicitly; no automatic re-send) |
| MOVE_UNTIL_LOAD | `motion_state` = MOVE_UNTIL_LOAD / STOPPING with `target_um` = bound, or a MOVE_DONE for this epoch | not executed |
| HOME | `motion_state` = HOMING or `home_phase` ≠ NONE, or EVENT HOMED / HOME_FAILED received | not executed |
| JOG ≠ 0 | `motion_state` = JOG | not executed |
| ENABLE / DISABLE | `motion_state` ∈ {ENABLING, IDLE, moving} / = NOT_ENABLED and `io.ENA_DISABLED` | not executed |
| SAVE_PARAMS | `nvm_record_seq` increased and `CFG_DIRTY` = 0 | not executed |
| LOAD / DEFAULT_PARAMS | EVENT PARAMS_LOADED / PARAMS_DEFAULTED received; otherwise unknown → full parameter re-read | — |
| REBOOT | EVENT BOOT, `uptime_ms` smaller than before | — |
| RESUME | `status.PAUSED` = 0 **and no EVENT PAUSED received since the RESUME was written** (a new pause in between means the RESUME did not take effect for the current pause), or EVENT PAUSE_CLEARED arg 3 RESUME received | not executed → the sequence stays PAUSED, message "Resume not confirmed — press Resume again" (never re-sent automatically) |
| HALT_CLEAR | `flags.HALT` = 0 and `status.PAUSED` = 0 (or EVENT HALT_CLEARED / PAUSE_CLEARED arg 2 received) | not executed → `ClearResult(confirmed=False)`, message "Clear not confirmed — click again"; nothing is re-sent automatically (D-34) |
| ESTOP_CLEAR | `flags.ESTOP` = 0 (or EVENT ESTOP_CLEARED received) | as above |
| FAULT_CLEAR | the previously latched faults are no longer in STATUS `faults` (or EVENT FAULT_CLEARED received; its arg = cleared mask) | as above; if only some faults cleared, `ClearResult.cleared` lists those |

A clear whose latch is still set after the GET_STATUS check may also have been executed and re-tripped (e.g.
LOAD_LIMIT re-trip on regrow); the result text then names the new latch event if one arrived.

- **Motion epoch** (KD-04): `CommandChannel.motion_epoch` increments on every STOP/HALT/PAUSE sent and on
  every FW stop indication (EVENT STOPPED, ESTOP_SET, HALT_SET, PAUSED, FAULT_SET, LIMIT_SET, LINK_WDG,
  HOME_FAILED, DRIVER_DISABLED, DRIVER_POWER(0); MOVE_DONE reason STOPPED; `MOVING` 1→0 without MOVE_DONE).
  A motion request carries the epoch at creation; the writer drops it if the epoch changed before it was
  written (event `motion.dropped`). JOG sessions and pending targets are bound to the epoch.
- **Token bucket** ≤ 100 command frames/s (ICD §9.3) for GENERAL/CONTROL; SAFETY and priority frames are
  exempt (they are ≤ 7 Hz + bursts of ≤ 20 in 1 s).

### 4.5 Priority TX path (`core.link.PrioritySender`) — IF-011, NFR-002, NFR-003

- `send_now(cmd, payload)` builds the frame and acquires `FrameWriter._lock` with priority: the writer lock is
  a two-level lock (a "priority waiting" flag makes normal writers yield before taking the lock), so the
  longest wait is **one frame already being written** (PC→FW frames ≤ 25 B, i.e. MOVE_UNTIL_LOAD; the
  largest frame of either direction is 168 B ≈ 1.8 ms; typ. < 0.5 ms because the OS buffers the write).
  No queue, lane, token bucket or outstanding limit applies. Frames: STOP 9 B, HALT 8 B, PAUSE 8 B (fixed
  TYPEs for the FW stop sniffer, ICD §2).
- The call never raises to the caller: transport errors are recorded in the returned `StopResult(sent,
  t_write_ns, error)` and published; the GUI banner shows the real send result (TS SWD-M1-05).
- STOP/HALT/PAUSE: (1) increment the motion epoch (a plain integer store, so a motion frame waiting for the
  lock is dropped) and write the frame under the priority lock, (2) cancel the jog session and the pending
  target, (3) call `OperationRegistry.terminate_all(reason)` (PAUSE: `pause_all(source)`, §3.5), (4) arm the
  `StopConfirmer` (CONFIRM class), (5) publish `stop.issued` — in this order, all within the caller's
  thread, no waiting (act first, publish after).
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

### 4.7 Command mapping (ICD v0.2 §3.2) and NACK decoding (ICD §4)

| TYPE | Command | Request (wire) | OK body | Lane / class | Backend caller |
|---|---|---|---|---|---|
| 0x01 | PING | – | – | SAFETY / RETRY | `LinkSupervisor` heartbeat, `Device.ping` |
| 0x02 | GET_INFO | – | INFO 44 B | GENERAL / RETRY | connect, reconnect |
| 0x03 | GET_STATUS | – | STATUS 86 B | GENERAL / RETRY | connect, poll, VERIFY resolution, stop confirmation |
| 0x04 | REBOOT | `u32 0xB007B007` | – | GENERAL / VERIFY | `config.reboot_async` (REBOOT_PENDING) |
| 0x10 | GET_ALL_PARAMS | `u8 page` | page, page_count, n, entries | GENERAL / RETRY | `read_all_params` (3 pages for dict 1) |
| 0x11 | GET_PARAM | `u16 id` | PARAM_ENTRY | GENERAL / RETRY | read-back |
| 0x12 | SET_PARAM | PARAM_ENTRY | PARAM_ENTRY as stored | GENERAL or CONTROL (`safety.*`) / RETRY | write plan, ThresholdManager, travel cal |
| 0x13–0x15 | SAVE / LOAD / DEFAULT_PARAMS | – | – | GENERAL / VERIFY | config API, travel cal ACCEPT |
| 0x20 / 0x21 | STREAM_START / STOP | – | – | GENERAL / RETRY | connect, toolbar, engines |
| 0x22 | SET_VALID | `u8 0/1` | `u32 t_us` | SAFETY (0) / CONTROL (1), RETRY newest | sequencer windows, manual toggle |
| 0x30 | ENABLE | – | `u16 settle_ms` | CONTROL / VERIFY | `motion.enable` (state ENABLING shown for `settle_ms`) |
| 0x31 | DISABLE | – | – | CONTROL / VERIFY | `motion.disable(confirmed)` |
| 0x32 | HOME | `u8 flags` (bit0 = load confirmed) | – | CONTROL / VERIFY | `motion.home`; `E_CONFIRM` → `ConfirmationRequired` |
| 0x33 | MOVE_ABS | `i32 target_um, u32 v_um_s, u32 a_um_s2` | – | CONTROL / VERIFY | `motion.move_to/move_by`, engines |
| 0x34 | JOG | `i32 v_um_s, u32 a_um_s2, i32 bound_um` (`JOG_NO_BOUND` = 0x80000000) | – | CONTROL / VERIFY (v ≠ 0), SAFETY / RETRY (v = 0) | `motion.jog_*` (§5.4) |
| 0x35 | MOVE_UNTIL_LOAD | `i32 bound_um, u32 v_um_s, u32 a_um_s2, i32 raw_stop, u8 cmp` | – | CONTROL / VERIFY | load-step approach (§10.4) |
| 0x36 | STOP | `u8 mode` 0 immediate / 1 controlled | – | priority / CONFIRM | `Backend.stop`, safety, supervisor, sequencer stop (mode 1) |
| 0x37 | HALT | – | – | priority / CONFIRM | `Backend.halt`, hotkey |
| 0x38 | HALT_CLEAR | – | – | priority / VERIFY (D-34) | `clear_stop_async` (clears HALT **and** PAUSED, D-31) |
| 0x39 | ESTOP_CLEAR | – | – | priority / VERIFY (D-34) | `estop_clear_async(confirmed)` |
| 0x3A | FAULT_CLEAR | – | `u16 cleared` FAULT mask | priority / VERIFY (D-34) | `fault_clear_async` |
| 0x3B | PAUSE | – | – | priority / CONFIRM | `Backend.pause` (D-29 a) |
| 0x3C | **RESUME** (ICD v0.4, D-31) | – (0) | – | CONTROL / VERIFY (ICD v0.4; refused only by ESTOP latched or input open, HALT and latched faults — never by NOT_ENABLED, DRV_UNPOWERED, DRIVER_ALARM, LIMIT, AFE or motion state) | `Backend.resume()`: clears **only** PAUSED; `E_STATE` (BLOCK mask) while HALT, ESTOP or a fault is latched |

All request LENs are fixed (`E_LENGTH` otherwise); the builders are tested byte-identical against the
request frames of `protocol_vectors.json`. Speeds/accelerations: accel `0` = `motion.a_max_um_s2`.

**NACK** = exactly 3 bytes `u8 status, u16 detail`; a NACKed command had **no effect** (ICD §4.1).
`NackError(cmd, status, detail)` carries `nack_text()` (shown verbatim, FW-CMD-003):

| Status | Decoding of `detail` → text |
|---|---|
| `E_UNKNOWN_CMD` 1 / `E_LENGTH` 2 | TYPE / expected LEN → "internal protocol error" (logged as a defect; never expected) |
| `E_PARAM_ID` 3 / `E_TYPE` 4 | parameter id → key from `params_gen.BY_ID` |
| `E_RANGE` 5 | SET_PARAM: parameter id → key + allowed range; other commands: **byte offset** → field name from the request layout (e.g. MOVE_ABS 0 = target, 4 = speed "above the current speed cap `v_limit`", 8 = accel; JOG 8 = bound "not ahead of the axis / outside the soft limits"; MOVE_UNTIL_LOAD 12 = raw_stop, 16 = cmp) |
| `E_CONFIG` 6 | id of the **other** parameter of the violated hard rule → "conflicts with `<key>` (rule Hn)" |
| `E_BUSY` 7 | 1 MOTION ("axis moving / homing / stopping"), 2 ENABLING ("driver settling") |
| `E_STATE` 8 | BLOCK mask → list of names + clear hints (§6.5): ESTOP, HALT, FAULT, NOT_ENABLED, NOT_HOMED, LIMIT, AFE_STALE, AFE_SATURATED, DRV_UNPOWERED, DRIVER_ALARM, **PAUSED** (D-30: an expected outcome for motion commands, §4.4 — shown as "paused — press Resume", never as an error); on RESUME the mask names the latch that refused it (HALT / ESTOP / FAULT, D-31) |
| `E_CAUSE_ACTIVE` 9 | ESTOP_CLEAR: `0xFFFF` "E-stop input still open", else "wait n ms"; HALT_CLEAR: `0xFFFF` "STOP button still pressed", else "wait n ms"; FAULT_CLEAR: FAULT mask of the faults whose cause is present (names) |
| `E_CONFIRM` 10 | HOME under load → `ConfirmationRequired` (the gate shows C-02 and the call is repeated with the flag) |
| `E_NVM` 11 | 1 "no valid NVM record / record violates a hard rule", 2 "flash erase/program error", 3 "flash verify error" |
| `E_INTERNAL` 12 | implementation-defined → logged with the raw value |

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
    def enable(self) -> int                       # returns settle_ms (0 = already enabled)
    def disable(self) -> None
    def home(self, *, load_confirmed: bool) -> None                 # HOME flags bit0
    def move_abs_um(self, target_um: int, v_um_s: int, a_um_s2: int, *, epoch: int) -> None
    def move_until_load(self, bound_um: int, v_um_s: int, a_um_s2: int, raw_stop: int,
                        cmp: MulCmp, *, epoch: int) -> None         # direction = sign(bound − x)
    def jog_um_s(self, v_um_s: int, a_um_s2: int = 0, bound_um: int = JOG_NO_BOUND, *,
                 epoch: int) -> None                                # v = 0 → JOG 0 (RETRY class)
    # stops, pause and clears (priority path, never raise)
    def stop(self, mode: StopMode = StopMode.IMMEDIATE, reason: str = "user") -> StopResult
    def halt(self, source: str) -> StopResult
    def pause(self, source: str) -> StopResult                     # PAUSE 0x3B (D-29 a)
    def resume_cmd(self) -> ClearResult                             # RESUME 0x3C: clears only PAUSED (D-31, ICD v0.4)
    def halt_clear(self) -> ClearResult; def estop_clear(self) -> ClearResult
    def fault_clear(self) -> ClearResult                            # .cleared = FAULT mask names
    # ClearResult(sent, confirmed, outcome OK | REFUSED(nack) | NOT_CONFIRMED, cleared, text): clears and
    # RESUME are VERIFY class (D-31, D-34) — one frame, never re-sent; a timeout is resolved by GET_STATUS
    # every blocking method has `<name>_async(...) -> ReleasingFuture` (Worker thread)
```

`ParamValue`, `DeviceInfo` (INFO §7.1: proto/payload versions, fw_version, `param_dict_hash`, `uid` hex,
`build`, `param_count`, `features: frozenset[str]`), `BoardStatus` (STATUS §7.2, all 86-byte fields decoded:
`motion_state`, `home_phase`, `halt_src`, `reset_cause`, `sys_flags` (CLK_FALLBACK, CFG_DIRTY, STREAM_ON,
REBOOT_PENDING, NVM_DEFAULTED), `pos_um`, `target_um`, `pos_steps`, `afe_raw_last`, `afe_rate_sps`,
counters, `link_age_ms`, `idle_disable_left_s`, `nvm_record_seq`, `v_limit_um_s`, `pause_src` (offset 84,
`Source` NONE/PC/BUTTON, ICD v0.2)), `StatusFlags` (DATA `flags` u8 + `status` u16, ICD §7.6), `Faults`,
`IoLevels`, `BlockMask` are frozen dataclasses wrapping the generated `protocol_gen` `IntFlag`s/`IntEnum`s
(`DataFlags`, `DataStatus`, `Faults`, `IoBits`, `Block`, `SysFlags`, `MotionState`, …). A STATUS body
longer than 86 B is accepted (trailing bytes ignored, ICD §0.2). Compat flags: `OK | MINOR_DIFF |
MAJOR_MISMATCH | PAYLOAD_MISMATCH | PARAM_HASH_MISMATCH` (IF-008). `MulCmp` (`protocol_gen`)
(`GE = 0`: stop when raw ≥ raw_stop, `LE = 1`, ICD §5.4).

**Feature mask** (INFO `feature_mask`): a function whose bit is 0 is refused by its gate with the reason
"not supported by this FW build" — `MOTION` (all motion), `HOMING` (HOME), `MOVE_UNTIL_LOAD` (LOAD steps,
sequence start with LOAD steps), `NVM` (SAVE/LOAD), `BUTTONS` / `DRV_SIGNALS` (indicators shown as
"not wired"). `AFE_SYNTHETIC` (M1 FW placeholder samples): banner "synthetic load data", load calibration
and tare refused (`cal_load_start`, `tare` gates), SW load limits treated as without valid input.

### 5.2 Connect sequence (SW-PLT-003, IF-008)

Order = ICD §9.5.
1. Open the transport; pending bytes are fed to a fresh `FrameDecoder` (stale responses have no pending SEQ
   and are ignored, R3 P13); start Reader/Pipeline/Supervisor; randomise the first SEQ.
2. GET_INFO (200 ms timeout) repeated for ≤ 3 s; heartbeat starts with the first answer. No answer → "no board".
3. Version check: `proto_major` ≠ 1 or `payload_version` ≠ 1 → `MAJOR_MISMATCH`/`PAYLOAD_MISMATCH`:
   **read-only state** (monitoring and config read allowed; every motion, clear-and-enable and config write
   refused locally; DATA of another payload version is not decoded). `proto_minor` lower than the SW's →
   features beyond it unused (`MINOR_DIFF`). `param_dict_hash` ≠ `PARAM_DICT_HASH` → `PARAM_HASH_MISMATCH`:
   configuration read-only + warning (`set_param`/`write_and_verify` raise `ConfigReadOnly`; the
   ThresholdManager may still write the three `safety.*` session values because their ids/types are checked
   individually against the received PARAM_ENTRY type — if any differs, motion stays disabled).
4. GET_STATUS → latches, inputs, reset cause, motion state/HOMED/ENABLED, `sys_flags` (CFG_DIRTY →
   indicator "unsaved changes in RAM"; NVM_DEFAULTED → banner "board runs on defaults"; REBOOT_PENDING;
   CLK_FALLBACK → warning "MCU on HSI fallback clock, ±1 % timing", SD-10); nothing is saved automatically.
5. GET_ALL_PARAMS (pages 0…page_count−1) → `ParamStore`. Checks: AFE configuration vs the active
   calibration's `afe` block → calibration invalid for load limits (SW-CAL-009, D-29 l); `motion.steps_per_mm`
   vs the active travel calibration and a restore-pending record (§9.3.1). (`home.ref_switch` no longer exists in dict 2; a
   board with dict 1 is reported as `PARAM_HASH_MISMATCH`, configuration read-only.)
6. `ThresholdManager.on_connect()` writes and verifies `safety.load_raw_min/max` + `safety.zero_raw`
   (SAF-SW-002, §6.3) — motion stays disabled until verified (or the defaults in no-specimen mode, §6.7).
7. STREAM_START (default on; SW-ACQ-001 toggle later). Time unwrap starts a new epoch (§7.3).
8. `MotionController.resync()` from the first DATA frame (commanded target = reported `setpoint_um`).
A board UID different from the previous connection in this run invalidates the session tare (KD-13) and the
restore-pending record is kept for its own UID only.
Link statistics (frames OK, lost frames FW/link, CRC/len/timeout errors, late responses, command timeouts,
FW counters from GET_STATUS) are available from step 2 (`Backend.status().link`).

### 5.3 Configuration read / write / verify (SW-CFG-001..004)

- `ParamStore` holds board values, defaults and metadata from `params_gen` (typed fields, units, ranges, enum
  names for D's generated form, SW-CFG-001).
- `check(edits) -> list[Issue]` (GRQ-B-03): the same code as step (1) below, callable from the GUI thread
  (pure, < 1 ms): per key unknown / type / range (`ParamMeta.in_range`, enum code, f32 finite) and hard
  rules H1–H4 on board values ⊕ edits; locked keys (`safety.load_raw_min/max`, `safety.zero_raw` owned by the
  ThresholdManager, F-B-21) → ERROR "managed by the backend";
  `reboot_required` keys → INFO "effective after Save + Reboot" (SD-06).
- `write_and_verify(edits)` (TS-SWD §4.1 procedure, simplified, no `fw_owned`): (0) refuse under
  `PARAM_HASH_MISMATCH`; (1) `check(edits)` — any ERROR aborts before the first write (SW-CFG-003);
  (2) write changed parameters in an order that keeps H1–H4 true after each single SET (ICD §11.4: min/max
  pairs move the outward bound first; H3 lower the rate before widening pulses, widen the rate after
  narrowing pulses; H4 raise travel before load, lower load before travel); an `E_CONFIG` item is retried
  after the others in a further pass until a pass makes no progress (then REJECTED); parameters without
  `moving_ok` while moving → `BUSY` without sending; (3) `GET_ALL_PARAMS` read-back; per item `OK |
  REJECTED(status, detail) | MISMATCH | BUSY | TIMEOUT | NOT_ATTEMPTED | REBOOT_REQUIRED`; (4) GET_STATUS →
  CFG_DIRTY, REBOOT_PENDING. Never saves to NVM implicitly. After `motion.steps_per_mm`, `afe.*` or
  `safety.*`-related edits the ThresholdManager rechecks (§6.3).
- Board-config file (`*.bbboard.json`, §13.1): `save_board_config(path, values)`; `load_board_config(path)
  -> BoardConfigFile(values, unknown_keys, missing_keys, out_of_range, hash_mismatch)` — fills edit fields
  only (SW-CFG-002).
- NVM buttons: `save_params` (then GET_STATUS: CFG_DIRTY must be 0, `nvm_record_seq` increased),
  `load_params` / `default_params` (+ re-read; DEFAULT_PARAMS resets the session values to ±7 022 271 / 0,
  so the `ThresholdManager` **must** re-send them, ICD §5.2/§11.5) (SW-CFG-004). `reboot_async()`
  (REBOOT_PENDING after an `R` parameter + SAVE) refused while moving; reconnect after EVENT BOOT.
- `safety.load_raw_*` and `safety.zero_raw` are owned by the `ThresholdManager`; the config form shows them
  read-only (F-B-21, done in D's design).

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
    def reset_test_zero(self) -> None     # x_zero := 0 (machine coordinate), event row X_ZERO (GRQ-B-06)
    def set_valid(self, flag: bool) -> int    # manual VALID toggle (SW-MAN-006); gate valid_toggle
    def limits(self) -> MotionLimits      # caps + SW travel range (below)
    def check(self, kind: MotionKind, *, speed_mm_s: float | None = None, accel_mm_s2: float | None = None,
              target_mm: float | None = None) -> GateResult   # GRQ-B-05: pure, GUI thread, < 1 ms
    def resync(self) -> None              # commanded_target := reported position (after stops/faults)
```

`MotionKind` = `MOVE | JOG | HOME | LOAD_APPROACH`. `MotionLimits` (frozen): `v_travel_mm_s`
(`motion.v_max_travel_um_s`), `v_load_mm_s` (`motion.v_max_load_um_s`), `v_step_rate_mm_s`
(`floor(max_step_rate_hz·1000/steps_per_mm)` µm/s), `v_unhomed_mm_s`, `a_max_mm_s2`, `loaded: bool`,
`v_cap_mm_s` (= the cap that applies now), `v_cap_fw_mm_s` (STATUS `v_limit_um_s`, ≤ 1 s old), SW travel
range (enabled limits ∩ soft limits, machine and test mm).

- **Conversion**: mm → µm with `round_half_away` (SYS-003) at the `Device` boundary only.
- **Speed caps (D-29 e, ICD §5.4)**: `v_cap = min(v_max, v_step_rate)` with `v_max = v_load` for
  MOVE_UNTIL_LOAD and whenever the axis is **loaded**, else `v_travel`; un-homed JOG additionally ≤
  `v_unhomed`. *Loaded* mirrors the FW predicate `abs(raw − safety.zero_raw) ≥ safety.release_band_raw` or
  AFE stale, evaluated by the SW with a safe-side margin (`≥ 0.8 · release_band_raw` counts as loaded), so a
  command the SW accepts is never refused by the FW for its speed except in a race at the band edge (then
  `E_RANGE` offset 4 → "speed above the loaded cap, retry slower"). Above the cap → `check()` ERROR /
  `ValueError` with the allowed maximum (SW-MAN-005); `None` = session default (≤ cap). Accel 0 = FW
  `a_max`; above `a_max` → ERROR.
- **Targets** must lie inside the enabled SW travel limits (SW-LIM-001) and the FW soft limits; refused
  locally with a message, nothing sent.
- **Commanded-target accumulator, latest wins (D-29 i, F-B-09/GF-04 closed)**: `move_by` adds to
  the **last commanded target including a pending one** (`pending_target_mm` if set, else
  `commanded_target_mm`), never to the live position (SW-MAN-002/003 v0.3). If the FW is idle the MOVE_ABS
  is sent at once; if a manual move of the same owner is still running, the new target becomes `pending_target_mm`
  (latest wins) and is sent on MOVE_DONE reason TARGET; any other MOVE_DONE reason or a stop discards it.
  Rapid clicks 11/12/13 send 11 and 13 (final target 13); with each move completed between clicks the wire
  sees 11, 12, 13. `status().motion.pending_target_mm` lets the GUI show the final target.
- **Resync**: after any stop, fault, latch clear, reconnect or MOVE_DONE other than TARGET,
  `commanded_target_mm` := reported position at standstill (`setpoint_um` of the first DATA frame with
  `MOVING = 0`, or MOVE_DONE `value`), so the next ±1 mm starts where the axis stopped.
- **Slider** (SW-MAN-001): GUI sends nothing while dragging; on release it calls `move_to(value)` once.
- **Jog** (SW-MAN-004): `jog_start` sends `JOG(v, a, bound)` immediately; the Supervisor refreshes it every
  80 ms on its 5 ms tick (max interval < 100 ms incl. tick and jitter, SWD-P1-11; newest speed and bound) while the session is active, the motion epoch is unchanged, the GUI beat is
  younger than 300 ms and the gate stays open; `jog_stop` (button release, focus loss — D calls it) sends
  JOG 0 (controlled stop, RETRY class). **Bound (F-B-15 closed, ICD §5.4)**: homed and an SW travel limit
  enabled in the jog direction → `bound_um` = that limit (the FW stops exactly there with a planned
  deceleration, MOVE_DONE BOUND); otherwise `JOG_NO_BOUND` (FW: soft limit when homed, `home.max_travel_um`
  from the start point when un-homed). A bound must lie strictly ahead of the axis: if the axis is at or
  beyond the SW limit in that direction the jog is refused locally (direction-aware, §6.1). The predictive
  PC check (§6.1 rule 3) stays as a second layer. Un-homed jog ≤ `v_unhomed`.
- **ENABLE**: the response's `settle_ms` starts an `ENABLING` display state; motion gates refuse with
  "driver settling (n ms)" until DATA shows `ENABLED = 1` (FW `E_BUSY` 2 otherwise).
- **PAUSED (D-30)**: a PAUSE (GUI or button) is a controlled stop and a **motion-blocking latch** in the FW
  (BLOCK bit PAUSED, ICD v0.3): every new motion start incl. JOG refreshes is refused while PAUSED; STOP,
  HALT, PAUSE, JOG 0 and the clears stay accepted. The manual gates therefore REFUSE while PAUSED with the
  hint "Resume (clears PAUSE)"; manual `Backend.resume()` = RESUME only (no motion, D-31). A manual command
  already in flight and refused with BLOCK PAUSED is an expected outcome (§4.4, D-33 k). On EVENT PAUSED or
  STOPPED (and on DATA `status.PAUSED` 0→1) the Pipeline **first** ends any jog session and drops the
  pending latest-wins target (motion epoch++), before any other processing.
- **MoveTicket**: a future resolved by EVENT MOVE_DONE (`MoveDone(reason: TARGET | LOAD_THRESHOLD | BOUND |
  SOFT_LIMIT | JOG_ZERO | STOPPED, pos_mm, pos_steps, t_us, stop_cause | None)` — `stop_cause` from the
  preceding STOPPED / HOME_FAILED), or resolved without motion as `REFUSED_PAUSED` (NACK with BLOCK PAUSED,
  expected, D-33 k) / `REFUSED(nack)` (any other NACK), or cancelled with the stop/latch/link reason; engines
  wait on it, the GUI may attach a done-callback via the bridge.

### 5.5 Stop, halt, pause and clear (SW-STOP-001..004, SAF-SW-004)

| Backend call | Wire (ICD v0.2) | Side effects |
|---|---|---|
| `Backend.stop(source)` | STOP mode 0 (immediate), CONFIRM | epoch++, jog/pending cancelled, `terminate_all` (sequence/wizard/tare terminated), event; not latched (D-26 (2)); FW clears VALID |
| `SequenceExecutor.stop()` | STOP mode 1 (controlled), CONFIRM | sequence ends STOPPED (SW-SEQ-007) |
| `Backend.halt(source)` (Pause/Break key, abort) | HALT, CONFIRM (≤ 20 tries / 1 s until ACK or `flags.HALT`) | as STOP; HALT latched in FW (`halt_src` PC) (SW-STOP-002) |
| `Backend.pause(source)` (GUI Pause) | **PAUSE 0x3B**, CONFIRM (ACK or `status.PAUSED`) (D-29 a) | epoch++, jog session and pending target dropped, `pause_all` (§3.5): sequence → PAUSED (state kept); wizards/tare terminated (GRQ-B-10 d); manual: FW controlled stop; FW sets the motion-blocking PAUSED latch (EVENT PAUSED arg PC, STATUS `pause_src`), clears VALID (D-30) |
| `Backend.resume()` | **RESUME 0x3C** (clears only PAUSED, D-31), then — sequence only — re-issue the interrupted step's motion (absolute target / approach + trim) | gate `resume` re-checked; capture window restarted; manual mode: RESUME only (no motion); a RESUME refused with `E_STATE` HALT/ESTOP/FAULT is an expected outcome (§10.5) |
| `Backend.clear_stop_async()` | HALT_CLEAR | only by explicit GUI action; NACK `E_CAUSE_ACTIVE` (STOP button pressed / released < `io.release_ms`) decoded; clears HALT and PAUSED (D-31). **While a sequence is PAUSED** (no HALT — a HALT has already terminated it): CONFIRM "ends the paused sequence" → the sequence is ended STOPPED (reason `CLEARED`) first, then HALT_CLEAR is sent; **no motion afterwards** (SW-STOP-003) |
| `Backend.estop_clear_async(confirmed)` | ESTOP_CLEAR | requires the confirmation token "button released, re-home needed" (SAF-SW-004); afterwards driver disabled + not homed: ENABLE then HOME (SAF-FW-006) |
| `Backend.fault_clear_async()` | FAULT_CLEAR | OK body = cleared FAULT mask; `E_CAUSE_ACTIVE` detail = faults whose cause is present, nothing cleared (FW-CMD-003); LOAD_LIMIT always clearable (unload; re-trip on regrow) |

#### 5.5.1 FW EVENT dispatch (ICD §8; Pipeline thread, in arrival order with DATA)

EVENTs are notifications; DATA flags / GET_STATUS stay authoritative (ICD §7.4). An EVENT SEQ gap (lost
events, `event_overflows`) triggers an immediate GET_STATUS and a `resync()`.

| EVENT (code) | Backend action |
|---|---|
| BOOT (1) | new time epoch (§7.3), link stats reset, re-run connect steps 4–8 (no motion, no re-enable), tare kept (same UID), restore-pending check (§9.3.1), indicator "board reset (cause)" |
| STOPPED (2) | epoch++, jog session ended and pending target dropped first (D-30), `stop_cause` stored for the next MOVE_DONE; cause ∈ {PC_HALT, STOP_BUTTON, ESTOP, LIMIT_*, LIMIT_WIRING, LOAD_LIMIT, AFE_FAULT, LINK_WDG, STEP_FAULT, HOME_FAIL, DRV_POWER_LOST} → `terminate_all(cause)`; PC_PAUSE / PAUSE_BUTTON → `pause_all`; PC_STOP / PC_STOP_CONTROLLED → already handled by the sender (or `terminate_all` if another PC instance sent it); JOG_DEADMAN → jog session ended, event "jog stopped: no refresh" |
| MOVE_DONE (3) | resolve the MoveTicket; manual pending target sent on TARGET; `resync()` on any other reason |
| ESTOP_SET (4) / ESTOP_CLEARED (5) | `terminate_all("ESTOP")`, travel-cal restore-pending stays armed; indicators |
| HALT_SET (6, arg source) / HALT_CLEARED (7) | `terminate_all("HALT <src>")`; `halt_source` |
| PAUSED (8, arg source 1 PC / 2 BUTTON) / PAUSE_CLEARED (9) | **first** epoch++, jog session ended, pending target dropped (D-30, OI-ICD-04); then `pause_all(source)`; `paused_source` (STATUS `pause_src`, GRQ-B-02). PAUSE_CLEARED (arg RESUME, ICD v0.4) → the resume in progress continues with the re-issue; PAUSE_CLEARED arg HALT_CLEAR → the sequence was already ended by `clear_stop`; an unexpected PAUSE_CLEARED while a sequence is PAUSED (another client) → sequence STOPPED |
| RESUME_REQUEST (10) | `Backend.resume(source="button")` if the `resume` gate is open: sequence PAUSED → RESUME + re-issue; manual → RESUME only (no motion, D-14/D-31); otherwise topic **`resume.ignored`** (`ResumeIgnored(source "button" | "gui", reason GateResult, t_us)`, GF-11) + event row (SW-STOP-004) |
| FAULT_SET (11, arg bit) / FAULT_CLEARED (12) | `terminate_all(fault)` if an operation runs; indicators per fault (§6.5); LOAD_LIMIT value = deciding raw → event row with F |
| LIMIT_SET (13) / LIMIT_CLEARED (14) | indicators; `resync()` |
| LINK_WDG (15) / LINK_RESTORED (16) | `terminate_all("LINK_WDG")`; indicator; the SW never restarts motion |
| VALID_CLEARED (17, arg cause) | sequencer closes the open window as discarded (`WINDOW_DISCARDED`), recorder event row; VALID never re-asserted |
| HOMED (18, value drift µm) / HOME_FAILED (19, arg reason) | resolve the homing ticket; drift shown; `resync()`; HOMING operation ends |
| DRIVER_ENABLED (20) / DRIVER_DISABLED (21, arg 1 PC / 2 IDLE / 3 ESTOP / 4 DRV_POWER_LOST) | indicators; IDLE: info "driver disabled after idle time"; 3/4: HOMED lost → `resync()` |
| STOP_BUTTON (22) / PAUSE_BUTTON (23) | input indicators only (the stop itself comes as STOPPED / HALT_SET / PAUSED) |
| ALM_CHANGED (24) | indicator `alm`; new motion refused by FW while powered (D-28) → gate REFUSE "driver alarm". **ALM 0→1 while a sequence runs (D-33 c)**: priority STOP mode 1 (controlled) + the sequence ends STOPPED, reason `DRIVER_ALARM` (no retry; the FW itself only reports, D-16). Same rule for travel calibration and homing operations (terminated). The ALM state is also taken from DATA `status.ALM` (0→1 edge) so a lost EVENT cannot hide it |
| AFE_REINIT (25), AFE_RATE_MISMATCH (26), AFE_STALE (27) | indicators; captures abort on stale (§9.2) |
| PARAMS_SAVED / LOADED / DEFAULTED (28–30), NVM_ERROR (31) | parameter re-read where values may have changed; LOADED/DEFAULTED → `ThresholdManager.recheck()` (session values reset by DEFAULT); NVM error text |
| CLK_FALLBACK (32) | warning indicator (SD-10) |
| DRIVER_POWER (33, arg 1/0) | indicator `drv_pwr`; 0 → motion refused (DRV_UNPOWERED), HOMED cleared by FW (D-29 c) → `terminate_all("DRV_POWER_LOST")`, `resync()`; 1 → hint "Enable, then Home" |
| NOT_SETTLED (34) | warning event "PEND not active n ms after the last pulse" (no reaction, D-16) |
| unknown code | logged, ignored (ICD §0.2) |

### 5.6 Gates (`core.gates`, pure functions of a `GateSnapshot`)

`GateSnapshot` = link state, compat, latest DATA flags (age), GET_STATUS latches/inputs, ParamStore,
threshold state, SW-limit input validity, operation owner, calibration/tare state, recording state.
Each gate returns `GateResult(items: list[GateItem(code, severity REFUSE|CONFIRM|WARN, text, clear_hint)])`.

`GateId` (StrEnum) lists every precomputed gate; `status().gates: Mapping[GateId, GateResult]` is rebuilt
with every status snapshot (≥ 10 Hz). The REFUSE items mirror the FW BLOCK mask (ICD §4.3) so the GUI greys
a control before the FW would NACK it; the FW stays the authority. GRQ-B-01: all gates requested by D are
provided.

| Gate | REFUSE items (any) | CONFIRM / WARN |
|---|---|---|
| `move`, `jog` (`motion_gate(kind, owner)`) | not connected / DEGRADED / LOST; compat major/payload mismatch; feature `MOTION` missing; stream off or DATA > 500 ms old; NOT_ENABLED / ENABLING; not homed (`move`; `jog` with bound); ESTOP (latched or input open), HALT, any FAULT latched, **PAUSED** (D-30; hint "Resume clears PAUSE"); LIMIT toward an active/latched switch (direction-aware); AFE stale or saturated; DRV_UNPOWERED; DRIVER_ALARM (ALM + power, D-28); thresholds not verified (SAF-SW-002); an enabled SW load limit without valid input (SAF-SW-001; not in no-specimen mode); owner conflict; SW-limit trip active in the commanded direction (§6.1); hotkey test running | WARN SAF-SW-006 margin; WARN no-specimen mode; WARN travel calibration differs (§9.3.1) |
| `home` | as `move` without NOT_HOMED/LIMIT (the FW homing handles the switches; homing only at START, D-29 b); feature `HOMING` | CONFIRM if abs(F) ≥ 5 % FS **or** load unknown (no calibration/tare, AFE synthetic) → HOME carries the confirmed flag (SAF-SW-004, SAF-FW-021) |
| `enable` | ESTOP latched / input open; DRV_UNPOWERED; read-only compat | – |
| `disable` | moving / stopping | CONFIRM "specimen unloaded?" (SAF-SW-004) |
| `pause` | not connected; read-only compat | WARN "no motion running — sets PAUSED only" when idle and no sequence |
| `resume` | FW PAUSED = 0 and no sequence PAUSED; HALT, ESTOP or a fault latched (the FW would refuse RESUME, D-31); feature/compat as `move`; sequence: the `move` gate for the step's motion kind refuses (the PAUSED item ignored) | – |
| `clear_stop` | nothing to clear (no HALT, no PAUSED); STOP button active (STATUS io) | CONFIRM "ends the paused sequence — use Resume to continue it" while a sequence is PAUSED (D-31, SWD-P1-02 c) |
| `estop_clear` | E-stop input still open (STATUS io) / closed < `io.estop_release_ms` | CONFIRM "button released; driver stays disabled; Enable and re-home" |
| `fault_clear` | no fault latched | WARN per fault whose cause is still present (FW will refuse) |
| `stream_start` / `stream_stop` | not connected / already in that state; `stream_stop`: moving or an operation running (PC limits need data) | – |
| `record_start` / `record_stop` | `record_start`: already recording, folder not writable, free space < limit; `record_stop`: not recording or the recording belongs to a running sequence | WARN marks incomplete (specimen/number empty, Q27 default: warning only) |
| `sample` | stream off; another SAMPLE capture running | WARN not recording (goes to the daily samples file) |
| `tare` | SW-TARE-003 list (§9.5); AFE synthetic | WARN large offset (after the capture) |
| `config_write` | not connected; `PARAM_HASH_MISMATCH` / read-only compat; any operation running; (per key) moving and key without `moving_ok` | WARN REBOOT_PENDING / reboot-required keys |
| `cal_travel_start` | `move` gate; another operation; SW-limit room for 2 + 10 + 50 mm in + direction | CONFIRM "no specimen mounted" if load unknown (D-29 h: allowed in no-specimen mode) |
| `cal_load_start` | not connected; stream cannot start; another operation; AFE synthetic; AFE stale | WARN existing active calibration will be replaced on accept |
| `sequence_start` | SW-SEQ-005 list (§10.3), each with its own item and text: not homed / not enabled; any latch (ESTOP, HALT, FAULT, LIMIT); **PAUSED** ("Resume or Clear stop first"); **POS_UNCERTAIN** ("re-home: the position may be off by one step"); **AFE_RATE_MISMATCH** ("HX711 rate differs from the configuration"); **ALM active** ("driver alarm"); **DRV_PWR off** ("driver unpowered") (D-33 b, SWD-P1-04/08); LOAD steps in no-specimen mode; feature `MOVE_UNTIL_LOAD` missing with LOAD steps | CONFIRM HOME steps under load; CONFIRM **"Pause/Break key unavailable"** when the hotkey is not active (GQ-09/Q27 default); CONFIRM travel-only sequence in no-specimen mode; WARN margin, LOW_SPAN, extrapolation, travel calibration differs |
| `sequence_edit` | a sequence is running or PAUSED | – |
| `valid_toggle` | not connected; a sequence runs (SW-SEQ-004) | – |
| `test_zero` | not homed (`set_test_zero`); moving; a sequence runs (x_zero frozen) | – |
| `no_specimen` (enter) | moving; an operation runs | CONFIRM C-10 text (§6.7) |
| `hotkey_test` | moving; an operation runs; hotkey unavailable | – |

Gate item codes (GF-11): an item that mirrors a FW BLOCK condition uses **exactly the generated
`protocol_gen.BLOCK_BITS` name** (`ESTOP`, `HALT`, `FAULT`, `NOT_ENABLED`, `NOT_HOMED`, `LIMIT`,
`AFE_STALE`, `AFE_SATURATED`, `DRV_UNPOWERED`, `DRIVER_ALARM`, `PAUSED`); an item mirroring a DATA status
bit without a BLOCK counterpart uses the generated `DATA_STATUS_BITS` name (`POS_UNCERTAIN`,
`AFE_RATE_MISMATCH`, …); SW-only items use names that never collide with generated ones (`LINK_DOWN`,
`COMPAT_READ_ONLY`, `FEATURE_MISSING`, `STREAM_STALE`, `THRESHOLDS_UNVERIFIED`, `LOAD_INPUT_INVALID`,
`OWNER_CONFLICT`, `SW_TRIP`, `HOTKEY_TEST`, `NO_SPECIMEN_MODE`, `TRAVEL_CAL_DIFFERS`, `SAF_SW_006_MARGIN`, …;
the full list is the `GateCode` StrEnum in `core.gates`, which a unit test checks for collisions with
`protocol_gen`).

Confirmation tokens: the GUI shows the CONFIRM text and calls the action with `confirmed=True` (also
`sequencer.start(seq, confirmed=True)`, GF-14); `confirmed=True` confirms the CONFIRM items of the gate
result **as evaluated at the call** — if a new CONFIRM item appeared since the dialog, the call raises
`ConfirmationRequired` with the new items; the backend refuses a CONFIRM-gated action without it (Enter/Space
handling and STOP-in-dialog are D's, SAF-SW-004).

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
| No-specimen mode | `no_specimen_mode` (session-only, never in the session file; ends at disconnect / exit) | off (§6.7, SW-LIM-004) |

Disabling the pull or push limit individually is allowed only while the load input is valid (calibration +
tare); without a valid input the only way to move with the SW load limits off is no-specimen mode (D-29 h).

Rules per sample (pure functions `calc.limits.*`; the supervisor adds state and actions):

1. **Load**: with a valid input (active calibration matching the AFE config, a tare, raw state OK or
   SETTLING) `F = K·(raw − tare_raw)`; trip if `F > pull_trip_n` or `F < push_trip_n` (enabled sides). A
   **saturated** sample counts as `±∞` with sign `sign(K)·sign(rail)` (overload, never "no data").
   Warning edge when `|F| ≥ warn_pct·|trip|` (hysteresis 2 % of the trip level) → `safety.warning` event and
   indicator.
2. **Load input invalid** while a load limit is enabled (no calibration/tare, AFE-config mismatch — D-29 l,
   NO_AFE_DATA fallback frame, AFE stale/fault flag, AFE synthetic): if moving → STOP (reason
   `LOAD_INPUT_INVALID`); the motion gate refuses new motion (SAF-SW-001 last sentence). In no-specimen mode
   (§6.7) the SW load limits are off, so this rule does not apply.
3. **Travel** (enabled, axis homed): `x = setpoint_um/1000` (exact commanded position). JOG and MOVE_ABS
   cannot pass an SW limit because targets are checked and JOG carries the limit as `bound_um` (§5.4; the FW
   stops exactly there); MOVE_UNTIL_LOAD bounds are never beyond an enabled SW limit (§10.4, D-33 d). As a
   second layer against a FW defect: (i) while the running command's end point (`target_um` of MOVE_ABS, JOG
   `bound_um`, MOVE_UNTIL_LOAD `bound_um`, as sent) lies inside the enabled limits, the trip fires only when
   the commanded position itself passes the limit by more than one step (`x` beyond limit + 1/spm) — a
   planned deceleration onto a bound that equals the limit is **not** a trip (D-33 d: reaching the bound is
   NOT_REACHED, not a limit trip); (ii) for motion without such an end point (un-bounded JOG, homing) the
   predicted position `x_pred = x + v_cmd·t_lead` (`v_cmd` from consecutive setpoints and `t_us`,
   `t_lead = 75 ms`) crossing a limit in the direction of motion trips. A position outside a limit at
   standstill (limit edited) is no trip, but the gate refuses motion further outside.
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
- **Check**: `fw_level_n ≤ 110 % FS` (2157.46 N) and ≥ the enabled SW trip magnitudes (SW-LIM-002) —
  violations are refused at edit time (`limits.set` issue), never written. **Clamp inward (D-29 g, SAF-SW-002 v0.3,
  F-B-07 closed)**: a computed raw value outside the dictionary range ±7 151 121 counts (SAF-FW-010; happens when
  `|K|` is below nominal) is clamped to the range end on its side, which can only make the FW trip *earlier*
  in force; state `VERIFIED` with `clamped = True` and a warning event/indicator text "FW load limit clamped:
  effective +a N / −b N" (`F_eff = K·(raw_clamped − tare_raw)`). Only if clamping leaves
  `raw_min ≥ raw_max` or `zero_raw` outside its range (tare beyond the cap — not physical) → `INVALID`,
  motion disabled.
- **Write** (CONTROL lane, idle only): order keeps H2 `raw_min < raw_max` after each SET (new min < current
  max → min first, else max first), then `zero_raw`; **read-back** (GET_PARAM ×3) compared exactly →
  `VERIFIED(cal_id, tare_id, fw_level_n, raw_min, raw_max, zero_raw, clamped)`. NACK/timeout/mismatch →
  `FAILED`, motion disabled, event; `recheck()` retries (also public: `limits.recheck_async()`, GRQ-B-04).
- **Triggers**: connect/reconnect, EVENT BOOT, calibration activated, tare done/undone, FW level edited,
  no-specimen mode entered/left, PARAMS_LOADED/PARAMS_DEFAULTED (DEFAULT resets the session values, ICD
  §11.5), `write_and_verify` touching `motion.steps_per_mm`/`afe.*`, every `read_all_params` (consistency
  check). Calibration accept, tare and limit edits are refused while moving, so thresholds never lag a
  running move.
- **No calibration or no tare** (outside no-specimen mode): state `DEFAULT_ONLY` (dictionary defaults
  ±7 022 271 and `zero_raw` 0 written and verified); with any SW load limit enabled the motion gate refuses
  ("calibrate + tare, or enter no-specimen mode", §6.7).
- **No-specimen mode** (§6.7, SW-LIM-004, Orchestrator decision): the ThresholdManager behaves exactly as
  outside the mode — with a valid calibration + tare it keeps writing the **calibrated** thresholds
  (`VERIFIED`); only without them the FW thresholds stay at the nominal defaults (`DEFAULT_ONLY`, never
  widened). The mode switches off only the PC's own load trips.
- Motion gate item: `thresholds.state == VERIFIED` and its ids equal the active calibration + tare, **or**
  `DEFAULT_ONLY` verified while in no-specimen mode without a valid calibration + tare.

### 6.4 Link loss and liveness (SAF-SW-003) — see §4.6

DATA gap > 500 ms while moving → priority STOP + `terminate_all("LINK_LOST")` + indicator LINK LOST;
heartbeat gated by liveness; FW watchdog (`safety.link_timeout_ms`, default 1 s, controlled stop) is the
backstop when the PC side is dead. A configured `safety.link_timeout_ms` < 750 ms raises a config WARN
("not robust against lost heartbeats", ICD §9.2).

### 6.5 Confirmations and indicators (SAF-SW-004/005, backend part)

- Confirmation-gated actions (§5.6): HOME with abs(F) ≥ 5 % FS or unknown load, DISABLE, ESTOP_CLEAR,
  entering no-specimen mode, sequence start CONFIRM items.
- `Indicators` (frozen dataclass, rebuilt in the Pipeline on every DATA/EVENT and on each GET_STATUS).
  **Every item is an `Indicator(state: ON | OFF | UNKNOWN, since_t_us, source: str | None, value: float |
  None, clear_hint: str | None)`** (GRQ-B-02). `UNKNOWN` before the first DATA/STATUS after connect, while
  disconnected, and when the item's source is stale (DATA items: stream on and newest DATA > 500 ms old;
  STATUS-only items: newest GET_STATUS > 1.5 s old — the Supervisor polls STATUS at 4 Hz while the stream is
  off). Items and their source (ICD §7.6); **item keys are the generated bit names lower-cased**
  (`DATA_FLAGS_BITS`, `DATA_STATUS_BITS`, `FAULTS_BITS`, `SYS_FLAGS_BITS` → e.g. `stop_btn`, `pause_btn`,
  `afe_rate_mismatch`, `k1_welded`, `clk_fallback`), SW-only items have SW names (GF-12):
  - DATA `flags`: `valid`, `moving`, `homed`, `enabled`, `estop`, `halt` (+ `source` PC / KEY / BUTTON:
    STATUS `halt_src` + the SW's own record of who sent HALT), `fault`, `overrun`;
  - DATA `status`: `paused` (+ `source` PC / BUTTON from STATUS `pause_src` (ICD v0.2) and EVENT PAUSED
    arg), `limit_start`, `limit_end`, `load_limit`, `afe_stale`, `afe_saturated`, `afe_settling`,
    `afe_rate_mismatch` (+ measured rate), `link_wdg`, `stop_btn`, `pause_btn`, `alm`, `pend`,
    `pos_uncertain`, `no_afe_data`, `drv_pwr` (D-29 c: OFF → "driver unpowered — position lost, re-home");
  - STATUS `faults` (one item each): `afe_fault`, `step_fault`, `limit_wiring`, `home_not_found`,
    `home_wiring`, **`k1_welded`** (D-29 c), **`home_drift`** (value = deviation µm);
  - STATUS `sys_flags`: `cfg_dirty`, `reboot_pending`, `nvm_defaulted`, `clk_fallback`;
  - SW: `link_state`, `sw_trip`, `thresholds_state` (+ `clamped`), `no_specimen_mode`, `travel_cal_differs`
    (§9.3.1), `afe_synthetic`, `recording_failed`, `hotkey`.
  Each latched item carries `clear_hint` from `gates.clear_procedure(code)`, e.g. ESTOP: "release the E-stop,
  wait ≥ `io.estop_release_ms`, Clear E-stop, then Enable and Home"; LOAD_LIMIT: "Fault clear, then move to
  reduce the load — re-trips if the load grows"; HALT(button): "release the STOP button, then Clear stop";
  K1_WELDED: "contactor K1 did not drop: switch off the driver supply, have K1 checked; Fault clear when the
  E-stop sense and driver power agree again"; DRV_PWR off: "restore driver power (E-stop released, RESET on
  K1), then Enable and Home"; HOME_DRIFT: "home switch moved by n µm: check the switch, Fault clear"; PAUSED:
  "motion blocked — Resume (clears PAUSE) or Stop the sequence". Edge changes are published (`indicators.changed`). The GUI polls
  `Backend.status()` at ≥ 10 Hz → indicator latency ≤ 200 ms (SAF-SW-005).

### 6.6 Speed-vs-margin warning (SAF-SW-006)

`calc.limits.limit_margin_warning(k_est_n_mm, v_mm_s, f_fw_n, f_pc_n, t_react_s=0.065) -> bool`
(`k_est·v·t_react > F_fw − F_pc`). Evaluated by `motion_gate` (WARN) for manual moves/jogs, by
`motion.check()` for the values in the GUI fields (GRQ-B-05) and by sequence validation per step (WARN in
the editor and at start).

### 6.7 No-specimen mode (SW-LIM-004, D-29 h, F-B-08 / GF-07 closed)

First use (no load calibration, no tare) and travel calibration need motion while the SW load limits have
no valid input. The operator switches the SW load limits off **for the session** instead:

- `limits.set_no_specimen_mode(on: bool, *, confirmed: bool = False) -> GateResult`; entering needs the
  CONFIRM item **C-10** "No specimen is mounted. The PC load limits are switched OFF for this session. The
  board load limit stays active (calibrated thresholds if a calibration and a tare exist, otherwise its
  nominal default ±7 022 271 counts around raw 0 ≈ ±109 % FS, ICD §11.5, D-12). Mount no specimen until a
  load calibration and a tare exist." Refused while moving or while an operation runs. Leaving needs no
  confirmation (it only adds protection) and is refused while moving.
- Effects: SW pull/push trips and the "load input invalid" rule (§6.1 rules 1–2) are off; load warnings are
  still shown when F is computable; travel limits stay active; the FW load limit stays active — calibrated
  thresholds when a valid calibration + tare exist, else the nominal defaults (§6.3, SW-LIM-004);
  `status().safety.no_specimen_mode` = True → GUI banner on every tab + indicator; every motion gate carries
  WARN "no-specimen mode"; entering/leaving writes an event row `NO_SPECIMEN_ON/OFF` into a running
  recording; recordings and reports carry `no_specimen_mode` and a report warning.
- Allowed: manual motion, jog, homing (HOME gate CONFIRM "load unknown" stays), travel calibration, the load
  calibration wizard, tare, take sample, recording. Sequence start: REFUSE with LOAD steps; travel-only
  sequences need the CONFIRM item.
- Never persisted: not in the session file; the mode **ends at disconnect** (also link LOST → reconnect
  starts with the mode off) and at application exit (SW-LIM-004). Leaving the mode re-enables the configured SW limits; without calibration + tare the motion
  gate then refuses again.
- Safety reasoning: the FW per-sample load limit (D-12, independent of the PC) remains active at all times;
  only the PC's tighter limits are suspended, as a recorded operator decision.

---

## 7. Data pipeline (`core.pipeline`)

### 7.1 Sample representation

`io.protocol.DATA_DTYPE` (ICD §7.3, PAYLOAD_VERSION 1, 18 B payload / 26 B frame, IF-006): `t_us u32,
payload_version u8, flags u8 (bit0 VALID … bit7 OVERRUN), afe_raw i32 (0x80000000 = no AFE data),
setpoint_um i32, frame_seq u16, status u16 (ICD §7.6)`. A batch is decoded with one `np.frombuffer`.
`SampleBatch` columns (numpy, per batch):

| Column | Type | Content |
|---|---|---|
| `t_us_u` | int64 | unwrapped device time (§7.3) |
| `t_dev_s` | float64 | `(t_us_u − t0_us)/1e6` (display/CSV) |
| `t_host_ns` | int64 | Reader receive stamp |
| `frame_seq`, `seq_lost` | uint16, int32 | sequence number, frames lost before this one |
| `flags`, `status` | uint8, uint16 | raw bit fields (lossless) + decoded bool views (`valid`, `moving`, …) |
| `raw`, `raw_state` | int32, uint8 | counts; `OK / SATURATED (status AFE_SATURATED or rail value) / NO_DATA (status NO_AFE_DATA, raw = 0x80000000, stored as 0 + state) / SETTLING (AFE_SETTLING)` |
| `setpoint_um`, `x_mm`, `x_test_mm` | int32, float64 | commanded position, machine mm, test mm (`− x_zero`) |
| `F_N`, `F_kgf`, `calc_reason` | float64, uint16 | NaN + reason bits (NO_CAL, NO_TARE, SATURATED, NO_DATA, AFE_MISMATCH, SYNTHETIC); `EXTRAPOLATED` bit set (value kept) when `abs(F) > 3·F_cal_max` (SW-CAL-008, GRQ-B-09) |
| `vstate` | uint8 | per-sample display/validity state for plots: 0 OK, 1 EXTRAPOLATED, 2 INVALID (saturated, settling, not computable), 3 NO_DATA (GRQ-B-08) |
| derived (§7.4) | float64 | NaN + reason where not applicable |
| `op`, `step_idx`, `loop_iter`, `phase` | int16/uint8 | annotation from the operation state (atomic tuple) |

### 7.2 Stages (per batch; one frame per batch in normal 80 Hz operation)

1. **Decode**: payload version / LEN check (`bad_payload` counted, frame dropped and logged).
2. **Time unwrap** (§7.3).
3. **Gaps / link stats** (§7.3).
4. **Classify** raw: `AFE_SATURATED` or a rail value (−8 388 608 / 8 388 607) → SATURATED; `NO_AFE_DATA`
   (fallback frame, `afe_raw` = 0x80000000, sent at `stream.fallback_hz` while the AFE is stale, FW-STR-005)
   → NO_DATA (excluded from rate, captures and steady-state windows); `AFE_SETTLING` → SETTLING.
5. **Scale**: `F = calc.tare.force_n(raw, K, tare_raw)`, `x_mm`, `x_test_mm`; extrapolation flag when
   `abs(F) > 3·F_cal_max` (SW-CAL-008).
6. **Derived** channels (§7.4), streaming state carried between batches.
7. **EVENT dispatch** (in arrival order with DATA): table §5.5.1.
8. **Annotation** from the operation state.
9. **Safety** (§6.1) — may write STOP immediately.
10. **Sinks**: ring buffer, recorder queue, CaptureHub, WindowAccumulator, operation guards (§10.5), weak
    observers (GUI `latest` only; plots poll the ring buffer); then `liveness.beat("pipeline")`.

### 7.3 Time base and loss detection (R4 §9, IF-006/007)

- **Unwrap** (`calc.timebase.unwrap_us` + TS `TimeUnwrapper` logic): `t < t_prev` with a backward step
  > 2³¹ → `+2³²`; a smaller backward step or EVENT BOOT → new **epoch** (event `FW_RESET`); long host gaps
  (≥ 60 s, e.g. stream paused) resolve the wrap count from host receive stamps (`plausible_wraps`, TS
  SWD-M3R1-02). All windows use integer `t_us_u`.
- **Frame loss** (`calc.timebase.frame_gaps`, SWD-P1-12 b): `d = (seq − prev) & 0xFFFF`. `1 ≤ d < 0x8000` →
  `lost = d − 1`; **`d = 0`** → duplicated frame: dropped before the pipeline (not recorded twice, not
  evaluated twice), counter `dup_frames`; **`d ≥ 0x8000`** (non-advancing / backward without EVENT BOOT) →
  `seq_anomalies += 1`, event `FRAME_SEQ_ANOMALY`, the reference is re-based to this frame and **no loss is
  counted** (never 65 535 losses); the frame itself is processed normally. First frame after a gap with OVERRUN → attributed to
  the FW (`frames_lost_fw`), else to the link (`frames_lost_link`). **Missed HX711 conversion**: Δt > 1.5 ×
  median period without a seq gap (`afe_missed`). `frame_seq` counts only frames due while the stream is
  on and is not reset by STREAM_STOP/START (ICD §2.2), so a stream pause is an event row, not a loss; the
  reference resets only on EVENT BOOT. **Lost EVENTs**: the EVENT header SEQ is an event counter → a gap =
  lost events (`events_lost`) → GET_STATUS + `resync()` (§5.5.1). Counters go to `Backend.status().link`,
  per-row `seq_lost`, and the recording (SW-ACQ-004).
- **Measured sample rate**: median of the last 16 AFE periods (fallback frames excluded); shown next to the FW
  value (FW-AFE-004).

### 7.4 Derived channels (SW-RT-004, R4 §10) — pure `calc.derived` functions

| Key | Formula / method (R4 §10) | Prerequisite | Live lag |
|---|---|---|---|
| `F_N`, `F_kgf` | `K·(raw − tare_raw)`, `/G0` | calibration + tare | 0 |
| `x_mm`, `x_test_mm` | `setpoint_um/1000`, `− x_zero` | homed for absolute meaning | 0 |
| `speed_mm_s` | central difference over ±2 samples using `t_us` | — | 2 samples (live value = centred at i−2) |
| `force_rate_n_s` | Savitzky–Golay derivative, 9 samples, order 2, on **contiguous** samples only: a frame gap (`seq_lost > 0`), a missed conversion (Δt > 1.5 × median period), a NO_DATA or saturated sample breaks the window → NaN (reason `GAP`) until 9 contiguous valid samples exist again; the offline report uses the same rule (SWD-P1-12 a) | F | 4 samples |
| `k_tan_n_mm` | OLS dF/dx over a sliding window with `|Δx| ≥ 0.05 mm`; NaN at standstill | F | window/2 |
| `k_sec_n_mm` | `F / x_test`, NaN for `abs(x_test) < 0.05 mm` | F, x_zero | 0 |
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
- `DataView` (GUI API, §15): see §15.6 for the exact result types (GRQ-B-08/09).
  `snapshot(keys, window_s, px_width) -> PlotSnapshot` → a **common** column time axis and, per key, exactly
  `px_width` min/max pairs plus a worst-of `vstate` per column, from the coarsest adequate pyramid level
  (render cost bounded by screen width, 5–600 s windows, SW-RT-003); `t_end_dev_s` = device time of the
  newest sample (the GUI's relative time axis). `xy(x_key, y_key, window_s=None, max_points=4000, *, since:
  Literal["window", "record", "sequence"] = "window") -> XYSnapshot` → stride-decimated pairs with per-stride
  extrema kept + `vstate` (X-Y view, SW-RT-003). `latest() -> LatestSample` (value + state n/a / STALE /
  SATURATED / INVALID / EXTRAPOLATED / OK, SW-RT-005). `sequence_trace(max_points=20 000)` → (x, F) of the
  running sequence, decimated on insertion (Δx ≥ 0.01 mm or ΔF ≥ 0.1 % FS) for the chart overlay
  (SW-SCH-002). The pyramid keeps, per level, the min/max of every float column and the max of `vstate`.

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
  hash, session settings, sequence JSON, k_est, pull_dir, `no_specimen_mode` — SW-META-002, SW-LIM-003),
  rewritten at stop (link statistics, CRC errors, overruns, frame gaps, events, integrity `{complete,
  rows_written, rows_lost, failures}`).
- **Mark edits during a recording** (GRQ-B-07, accepted as proposed): `marks_at_start` is frozen in
  `meta.json` at start; each edit while recording writes a `MARK_EDIT` event row (`key`, old, new) and is
  collected; at stop the sidecar gets `marks_final` + `mark_edits[]`. The report shows `marks_final` and
  lists the edits with their times.
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
    needs_confirmation: ConfirmRequest | None       # e.g. > 20 % steps/mm change (SW-CAL-003), WARN fit
    can_continue: bool; can_repeat: bool; can_cancel: bool
    continue_label: str                             # e.g. "Move 10 mm ▶", "Capture point 2 ▶" (GRQ-B-10)
    continue_moves: bool                            # True: continue_() starts motion → mouse-only button
    abort_reason: str | None                        # STOP / HALT <src> / ESTOP / PAUSE / LINK_LOST / ...
class Engine(Protocol):
    PHASES: ClassVar[tuple[str, ...]]               # documented phase order (GUI step strip, GRQ-B-10)
    def state(self) -> EngineState
    def subscribe(self, cb: Callable[[EngineState], None]) -> Token     # weak, backend thread
    def start(self, **config) -> GateResult
    def continue_(self, inputs: Mapping[str, float] | None = None, *, confirmed: bool = False) -> None
    def repeat(self) -> None; def cancel(self) -> None
```

Methods never block (work goes to the Worker / Operation runner / CaptureHub); state changes are published
after they happen. Any STOP/HALT/ESTOP/PAUSE/latch/link loss → `terminate` → phase `ABORTED` with
`abort_reason` (PAUSE terminates like STOP, GRQ-B-10 d — wizards are not resumable); **the active
calibration is never changed by a cancelled or aborted wizard** (SW-CAL-001; travel: §9.3.1). STOP stays
reachable in the wizard (D).

Phase lists (`PHASES`): travel `CHECK, BACKLASH, REFERENCE, MOVE1, ENTER_D1, MOVE2, ENTER_DTOT, RESULT,
ACCEPT, DONE` (+ `ABORTED`, `RESTORING`); load `CONFIG, AWAIT_OPERATOR, PRESETTLE, CAPTURE, EVALUATE, FIT,
ACCEPT, DONE` (+ `ABORTED`; AWAIT…EVALUATE repeat per point, `step_index` = point); tare `CHECK, CAPTURE,
EVALUATE, DONE` (+ `REFUSED`, `ABORTED`).

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
| `CHECK` | gate `cal_travel_start`: connected, enabled, homed, no latch, no specimen (abs(F) < 2 % FS if calibrated; else CONFIRM or no-specimen mode, D-29 h), travel room for 2 + 10 + 50 mm in + direction within SW/soft limits; remembers `spm0` and writes the **restore-pending record** (§9.3.1) | gate |
| `BACKLASH` | `move_to(x0 + 2 mm)` at `cal.v_mm_s` (default 2 mm/s) | one direction only |
| `REFERENCE` | operator zeroes the caliper → Continue; `s_ref` = STATUS `pos_steps` | — |
| `MOVE1` | target for exactly `N1 = round_half_away(10·spm0)` steps from the reference: `calc.travelcal.target_for_steps(x_ref_um, N1, spm0) → (target_um, n_actual)`; after MOVE_DONE the step count is **verified** from MOVE_DONE `value2` (`pos_steps`): `N1_actual = value2 − s_ref` (F-B-02 closed; ±1 step at spm > 1000 is measured, not assumed) | wire is µm, counts from the FW |
| `ENTER_D1` | `spm1 = travel_cal_step1(N1_actual, D1)`; plausibility (below) → `SET_PARAM motion.steps_per_mm = spm1` (idle, RAM only — SET_PARAM never writes flash) + read-back | TV-TC |
| `MOVE2` | `N2 = round_half_away(50·spm1)`; target from STATUS `pos_um` (now µm under spm1) and `target_for_steps(x1_um, N2, spm1)`; `N2_actual` from MOVE_DONE `value2` | SW-CAL-002 |
| `ENTER_DTOT` | operator enters the **total** `D_tot`; `spm2 = (N1_actual + N2_actual)/D_tot`; `inc = N2_actual/(D_tot − D1)`, consistency `inc/spm1 − 1` | TV-TC |
| `RESULT` | show spm0/spm1/spm2 (3 decimals), warnings | — |
| `ACCEPT` | `SET_PARAM spm2` + read-back, `SAVE_PARAMS` (+ CFG_DIRTY = 0, `nvm_record_seq` increased), write `travel_<UTC>.json` + `active_travel.json`, delete the restore-pending record | SW-CAL-004 |
| `CANCEL` / abort | restore rule §9.3.1 | SW-CAL-001 |

Plausibility (`calc.travelcal.travel_plausibility`, SW-CAL-003): **reject** `D ≤ 0` or a result outside
100…10 000 steps/mm; **confirm** a change > 20 % (showing the D-27 candidates **800 / 160** steps/mm — 800
= 4000 p/rev closed loop, the approved DIP setting (D-27 closed); 160 = 800 p/rev, "DIP not changed" —
SRS SW-CAL-003, SWD-P1-07; needed for the first calibration if the board still runs on another value), a
change > 5 % or a value outside ±20 % of the expected steps/mm (session `expected_spm`, **default 800**, the
FW default from ICD v0.4 / params); **warn "repeat"** when `|inc/spm1 − 1| > 0.5 %`. HOMED stays valid across
steps/mm changes (FW-MOT-009); the engine resyncs the commanded target after each SET.

#### 9.3.1 Active travel calibration and the restore rule (GF-05, GRQ-B-17, KD-16)

**Definition.** The *active travel calibration* is the steps/mm the board boots with, i.e. the value in its
newest valid NVM record; after an ACCEPT it equals `active_travel.json`. The wizard's trial value `spm1`
exists **only in board RAM** (SET_PARAM never writes flash, ICD §5.2), so the active calibration changes only
at ACCEPT (SET + SAVE_PARAMS + file). SW-CAL-001 "cancel or any stop leaves the active calibration unchanged"
is therefore always true for NVM and the file; the rule below makes the board **RAM** return to it as well.
(SRS v0.3 SW-CAL-001 adopts this definition and rule; F-B-23 closed.)

**Restore-pending record.** At `CHECK` the engine writes `%APPDATA%\BirdBendStand\calibration\
travel_restore_pending.json` `{board_uid, spm0, spm_trial: null, created_utc}` (atomic write) and updates
`spm_trial` before each trial SET. The record exists exactly while the board RAM may hold a trial value. If
CFG_DIRTY = 1 at `CHECK`, a CONFIRM item warns that ACCEPT's SAVE_PARAMS would also persist the other unsaved
RAM changes.

**Restore rule** — every wizard exit other than ACCEPT (Cancel, STOP, HALT, PAUSE, E-stop, any fault, link
loss, liveness fault, application exit, application crash):

| Situation at the exit | Action |
|---|---|
| link up, board idle (any motion state except moving/stopping; NOT_ENABLED after an E-stop counts as idle) | phase `RESTORING`: `SET_PARAM motion.steps_per_mm = spm0` + read-back at once; success → delete the record, `ABORTED`/`CANCELLED` with "steps/mm restored to spm0" |
| link up, board still stopping | wait for `MOVING = 0` (≤ 2 s), then as above |
| link lost (or app exits/crashes) | the record stays; the Supervisor retries at the next successful (re)connect |
| (re)connect / EVENT BOOT with a record for this board UID | after GET_ALL_PARAMS (§5.2 step 5): board value = `spm0` (e.g. the board rebooted and loaded NVM, or the earlier restore succeeded) → delete the record; board value = `spm_trial` and idle → SET `spm0` + read-back automatically (a configuration restore, never motion), then delete; board value is neither `spm0` nor `spm_trial` → keep the record, **do not write**, indicator `travel_cal_differs` with both values and the actions [Restore spm0] / [Keep board value] / [Ignore for this session] (someone else changed it; API below) |
| restore fails (NACK, timeout, mismatch) | indicator `travel_cal_differs` "board steps/mm (x) differs from the active travel calibration (spm0)"; Supervisor retries every 5 s while connected and idle, max 3 times, then waits for the operator's [Restore spm0] (`calibrations.restore_travel_async()`) |

Operator decisions (GRQ-B-19 / GF-13, M3): `calibrations.resolve_travel_difference_async(action:
Literal["restore", "keep_board", "ignore_session"]) -> Future[TravelDiffState]` — `restore`: SET the reference
value (`spm0`, or the `active_travel.json` value) + read-back, record deleted; `keep_board`: no write, the
restore-pending record is deleted (the board value stays; the indicator stays as a WARN "board steps/mm
differs from the active calibration — recalibrate" until a calibration is accepted); `ignore_session`: no
write, the indicator and the gate items are suppressed for this board UID until the application exits (the
record, if any, is kept for the next run). Every decision is an event row (`TRAVEL_DIFF_<ACTION>`) and is
written into the recording metadata. `status().calibration.travel_diff: TravelDiffState(board_spm,
reference_spm, source "restore_pending" | "active_file", ignored, actions)` drives the GUI buttons.

Independently of the record, at every connect the board value is compared with `active_travel.json` (if it
exists): a difference → indicator `travel_cal_differs` (WARN in the `move` gate, CONFIRM in
`sequence_start`) with [Restore active calibration] / [Ignore for this session]; nothing is written
automatically without a restore-pending record. Changing steps/mm while HOMED is allowed (FW-MOT-009:
machine zero = step count; µm positions rescale), so a restore never invalidates homing.

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
  "points are not linear", accept disabled; WARN → `needs_confirmation` (C-06: NL_span + residuals) and
  ACCEPT only with `continue_(confirmed=True)` (GRQ-B-10 c); negative K accepted; LOW_SPAN when the largest
  reference force < 20 % FS (SW-CAL-008); "linear within noise" note when `max|r| ≤ 3·u_r`.
- GRQ-B-10 (a) `load_cal.finish_early()`: allowed in `AWAIT_OPERATOR` once the zero point and ≥ 1 weight
  are accepted → `FIT` with 2 points = UNVERIFIED_LINEARITY (accept allowed, report warning). (b)
  `load_cal.retake(point_index)`: from `FIT` (or any `AWAIT_OPERATOR`), discards that point and returns to
  its `AWAIT_OPERATOR` with the stored mass prefilled; the other points are kept; masses must stay increasing.
- The engine needs no motion; it is refused while a motion-owning operation runs, and terminated by
  STOP/HALT/ESTOP/PAUSE/link loss like every wizard (the point in capture is discarded).
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
- Validity (KD-13, D-29 j): current application run and board UID only; never loaded from a file;
  recordings carry it. Refused with AFE synthetic (M1 FW placeholder samples).
- **Undo** (GRQ-B-13; PO default Q27: keep with [Undo tare]): `tare_engine.undo() -> GateResult` restores
  the state before the last tare of this run and board — the previous `TareState` or "no tare" (one level;
  event row `TARE_UNDO`), then `ThresholdManager.recheck()` (with "no tare" the motion gate refuses again
  unless no-specimen mode is on). Refused while moving, during a sequence, or when nothing can be undone.

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
    step_time_s: float = 0.0         # TRAVEL/HOLD: minimum dwell at target; LOAD: not used (timeout fixed by D-33 d, §10.5)
    tol_n: float | None = None       # LOAD completion tolerance
    capture_during_move: bool = False  # linear-ramp steps: VALID = 1 while moving (no steady state)
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
    plan_total_s: float | None       # None for an infinite loop (count = 0)       (GRQ-B-12)
    remaining_s: float | None        # plan_total_s − plan_t_s + behind_s; None if infinite
    paused_source: str | None        # "PC" / "BUTTON" while PAUSED
    k_est_n_mm: float; message: str
class SequenceExecutor:
    def start(self, seq: Sequence, *, confirmed: bool = False) -> GateResult  # gate SW-SEQ-005 (v0.3: was confirmations=)
    def pause(self, source: str = "gui") -> None; def resume(self) -> None
    def stop(self) -> None            # controlled stop, sequence ends (SW-SEQ-007)
    def abort(self, reason: str) -> None    # HALT (SW-SEQ-007)
    def continue_(self) -> None       # MARK wait_operator
    def status(self) -> SeqStatus; def subscribe(self, cb) -> Token   # status published at 10 Hz + on change
```

- **Start gate** (`gates.sequence_start_gate`, SW-SEQ-005, D-33 b; one REFUSE item with its own text per
  condition, SWD-P1-08): homed and enabled, no latch (ESTOP, HALT, FAULT, LIMIT), **not PAUSED**, **no
  POS_UNCERTAIN** (re-home first — otherwise every steady-state window would be empty, SWD-P1-04), **no
  AFE_RATE_MISMATCH**, **ALM not active**, **DRV_PWR present**, no other operation, validation without ERROR, all TRAVEL targets inside the SW limits (after freezing `x_zero`), calibration +
  tare valid if any LOAD step (and whenever a load limit is enabled), thresholds VERIFIED, stream on and
  fresh, recording can start (folder writable, free space). CONFIRM items: HOME steps with load ≥ 5 % FS or
  unknown. Then: start the recording if off, freeze `x_zero`, event `SEQ_START` (plan summary into the
  sidecar), owner = SEQUENCE.
- Runs in the **Operation runner** thread: 20 ms `Ticker` plus event waits (MoveTicket, pipeline sample
  condition). **Phase boundaries use device time**: the executor waits until the newest sample's `t_us_u`
  reaches the boundary, so every boundary lies within ±1 frame of plan (SW-SEQ-003).
- **Step timeline** (SW-SEQ-003; D-29 k, F-B-20 closed): command → reached (`t_reached` = `t_us` of
  MOVE_DONE, or of the last trim sample) → settle until `t_reached + settle_s` → capture → hold until
  `t_reached + max(step_time_s, settle_s + capture_s)` (TRAVEL/HOLD; dwell and settle are **measured from
  target reached**; for LOAD steps `step_time_s` is the reach timeout and the dwell is `settle_s +
  capture_s`) → next step. TRAVEL targets are relative to the **test travel zero** by default (`travel_ref =
  "test"`: machine target = target + `x_zero` frozen at start); `"machine"` is optional. HOME waits for
  EVENT HOMED (MOVE_DONE TARGET at 0) then resyncs; HOME_FAILED → step error → sequence STOPPED; TARE runs
  the TareEngine inline (refusal → step error → sequence STOPPED); MARK writes an event row and optionally
  waits for `continue_()`.
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
   motion direction `d = sgn·pull_dir`; **`bound_um` = the nearer of the FW soft limit and an enabled SW
   travel limit in direction `d`** (D-32, D-33 d: the axis may travel to the end of travel to find the load;
   the NC end switch stays the hardware backstop) — the direction is implicit in the absolute bound (ICD §5.4:
   direction = sign(bound − x)); `cmp = GE (0)` when the raw value must rise, i.e. `sgn·sign(K) > 0`, else
   `LE (1)` (F-B-04 closed) → `MOVE_UNTIL_LOAD(bound_um, v, a, raw_stop, cmp)` at `v ≤ v_load` (D-29 e). A
   bound not strictly ahead of the axis is refused locally (step error `BOUND_NOT_AHEAD`, no frame; F-B-28) — a different case from NOT_REACHED. If the
   last sample is already beyond `raw_stop` the FW completes at once (MOVE_DONE LOAD_THRESHOLD, no motion).
   MOVE_DONE reason LOAD_THRESHOLD → trim; **BOUND → step status `NOT_REACHED`** (bound reached without the
   target load: not a limit trip, no SW-limit latch), the axis holds at the bound, the sequence ends
   **STOPPED** with reason `NOT_REACHED` (D-32), the report lists the step as NOT_REACHED with the force at
   the bound; STOPPED → per the stop cause (§5.5.1). Old sequence files with a `travel_bound_mm` field load
   with a warning "ignored (D-32)".
3. **k_est**: `k_est_from_approach(x, F)` = OLS slope over the last ≥ 0.1 mm of the approach (sign-normalised by
   `pull_dir`), clamped to [`k_min`, `k_max`] (session), fallback = the sequence value; carried to later steps
   and recorded (SW-REP-001).
4. **Trim**: wait until `t_done + 100 ms` (device time); `F̄` = mean of the last 4 samples; `|F_target − F̄| ≤
   tol` → reached; else `Δx = trim_step(F̄, F_target, k_est, kp=0.5, max_step=0.2 mm)` (sign by `pull_dir`),
   `MOVE_ABS(commanded + Δx)` at 0.2 mm/s (clipped to the same bound as the approach; at the bound →
   NOT_REACHED), repeat ≤ 10 iterations (session trim parameters, SRS §5.2). Exhausted → `TRIM_FAILED`:
   `on_trim_fail = "stop"` (default, SW-SEQ-006 v0.3) → step NOT_ON_TARGET, sequence STOPPED; `"continue"` →
   capture anyway with the flag (pending PO, SRS OI-14 / F-B-14).
5. **Capture**: position frozen (no trim during settle/capture/hold).
TV-C (R4 §12) is the unit vector for `trim_step`; the simulator scenario `k = 50 N/mm, k_est = 40` must reach
tolerance within ≤ 10 iterations (SW-SEQ-006 acceptance).

### 10.5 Guards, controls, pause/resume — SW-SEQ-007, SW-STOP-004

- **Guards** (pipeline sink, per sample, while a TRAVEL/LOAD step moves): `SLIP` — load moves opposite to the
  expected direction by > 5 % of `|F_target|` (or of the running extreme for travel steps) from its running
  extreme; `BREAK_DETECTED` — `|F|` drops > 20 % below its running max once the running max ≥ max(5 % of
  target, 1 % FS) — **still aborts a load step that is travelling toward its bound** (D-32); `TIMEOUT` —
  LOAD approach: `t − t_cmd > 1.2 · T_bound + 10 s` with `T_bound = calc.motion.move_duration_s(|bound −
  x_cmd|, v, a)` (trapezoid travel time from the command position to the bound at the step speed, D-33 d;
  computed once per approach command, so the timeout can never end the step before the bound is reached);
  each trim MOVE_ABS: `1.2 · T + 10 s` likewise; TRAVEL: no MOVE_DONE within `1.2 · T_planned + 10 s`
  (same rule, one formula for all motion steps); `BOUND` → NOT_REACHED (§10.4, not a failure of the
  guard). Action for SLIP / BREAK_DETECTED / TIMEOUT: priority STOP, step failed, sequence STOPPED, event
  with values. **ALM 0→1** during the sequence → controlled STOP, sequence STOPPED `DRIVER_ALARM` (D-33 c,
  §5.5.1).
- **Controls**: start, pause, resume, stop (STOP mode 1 = controlled, sequence ends), abort (HALT).
- **Pause** (KD-15, D-29 a, D-30): GUI Pause sends **PAUSE 0x3B** (§5.5); the physical PAUSE button acts in
  the FW directly. Either way the FW performs a controlled stop if moving, sets the **motion-blocking**
  PAUSED latch (every new motion start incl. JOG refreshes refused, BLOCK PAUSED), clears VALID (EVENT
  VALID_CLEARED if it was 1) and reports EVENT PAUSED (source; STATUS `pause_src`). The executor reacts in
  `pause_all` — at the GUI call already (act first) and again, idempotently, on EVENT PAUSED: epoch++ (a
  motion command of the step still queued is dropped; one already in flight is refused by the FW), the open
  VALID window is closed and discarded (`WINDOW_DISCARDED`); step, phase and loop counters are kept; state
  PAUSED with `paused_source`. The executor never sends SET_VALID(0) for a pause (the FW already cleared it)
  and never re-asserts VALID.
- **Resume** (GUI `Backend.resume()` or EVENT RESUME_REQUEST = PAUSE button pressed while PAUSED; gate
  `resume` re-checked) — D-31 sequence:
  1. **RESUME 0x3C** (CONTROL lane, VERIFY class per ICD v0.4 — never auto-retried) — clears **only**
     PAUSED. The resume waits for the OK and for `status.PAUSED = 0` (EVENT PAUSE_CLEARED arg RESUME, or
     DATA), ≤ 500 ms.
     - **Refused** with `E_STATE` (BLOCK HALT / ESTOP / FAULT): an expected outcome (D-31), no retry. A HALT,
       E-stop or fault latched in the meantime has its own EVENT, which terminates the sequence
       (`terminate_all`, SW-STOP-003); the RESUME refusal only adds the reason to the log. If the refusal
       arrives before that EVENT, the sequence is ended ABORTED with the latch named in the BLOCK mask
       (never left PAUSED with a latch the PAUSE cannot explain). A STOP-button HALT latched just before the
       RESUME frame is therefore never cleared by Resume (closes F-B-30).
     - **Timeout**: VERIFY resolution by GET_STATUS (§4.4.1); PAUSED = 0 → continue; PAUSED = 1 → the
       sequence stays PAUSED, message "Resume not confirmed — press Resume again".
  2. Re-issue the interrupted step (new epoch): TRAVEL → re-issue the absolute target, then settle + capture
     anew; LOAD → re-run approach + trim from the current force (new bound and timeout per §10.4/§10.5);
     HOLD → restart settle/capture; HOME/TARE/MARK → re-run. A re-issue refused with BLOCK **PAUSED** (the
     operator paused again in between) is an expected outcome: the sequence stays PAUSED, step and phase
     unchanged (D-33 k). Any other refusal (gate or NACK) → the step fails and the sequence ends STOPPED with
     the reason.
- **Clear stop while a sequence is PAUSED** (SWD-P1-02 c): allowed after the CONFIRM "ends the paused
  sequence"; the backend ends the sequence STOPPED (reason `CLEARED`) first and then sends HALT_CLEAR, which
  clears PAUSED (D-31). While HALT is latched no sequence can be PAUSED (HALT terminates it). An unexpected
  PAUSE_CLEARED from another client → sequence STOPPED.
- VALID is never 1 during a pause (FW clears it; SW-STOP-004 acceptance). In manual mode `Backend.pause()`
  is the FW controlled stop + PAUSED; while PAUSED all manual motion is refused (gate + FW); manual
  `Backend.resume()` (or the PAUSE button → RESUME_REQUEST) sends RESUME only — no motion is re-issued.

### 10.6 Generators (`sequencer.generators`) — SW-WIZ-001/002

Pure functions returning `Block(steps, loops)`:

| Generator | Parameters | Output |
|---|---|---|
| `staircase` | kind TRAVEL/LOAD, start, end, increment **or** count, `up` / `up_down`, `return_to_zero`, speed, accel, settle, capture, step_time, tol | one step per level; up_down mirrors without repeating the peak; optional zero step between levels |
| `linear_ramp` | x0, x1, speed, accel | TRAVEL x0 (no capture) + TRAVEL x1 with `capture_during_move` |
| `cyclic` | kind, lo, hi, cycles, speed, accel, dwell_s, capture | two steps lo/hi + `Loop(count = cycles)` |
| `hold` | kind, target, duration_s | one step, `capture_s = duration_s` (creep/relaxation, captured throughout) |
| `return_` | `"zero"` / `"home"` | TRAVEL 0 (test) or HOME |

**Staircase semantics (SWD-P1-12 c).** Levels are computed in integer units (µm, mN) to avoid float drift.
*Increment mode*: `L_i = start + i·inc`, `i = 0, 1, …` while `L_i` does not pass `end`; the start level
`L_0` is a step; the end level is included only if the increment divides the span; increment ≤ 0 or > span
→ ValueError. *Count mode*: `count` = number of levels **including** start and end (`count ≥ 2`),
`inc = (end − start)/(count − 1)` (rounded to µm / mN, the last level is exactly `end`). `up_down` appends
the down levels `L_{n−1} … L_0` (the peak is not repeated). `return_to_zero`: a zero step (TRAVEL test 0 mm
/ LOAD 0 N, same speed, no capture) is inserted **after every level whose target is not zero**, including
after the last level, so the block always ends unloaded; finally a step whose target equals the previous
step's target is not generated (no duplicate zero). Example `start 0, end 30, count 4, up_down,
return_to_zero` → 0, 10, 0, 20, 0, 30, 0, 20, 0, 10, 0 (11 steps). Vectors VV-GEN-04/05 use this rule. `Sequence.insert_block(block, mode =
append | replace | insert, index)` re-indexes loops; inserted steps stay editable (SW-WIZ-002, editor is D's).

Parameter schemas (GRQ-B-18, M4): `generator_schemas() -> Mapping[str, GeneratorSchema(name, label,
fields: tuple[FieldSpec(name, label, unit, kind: "float" | "int" | "enum" | "bool", min, max, default,
choices, depends_on, srs), ...])]`; `min/max/default` are resolved against the current `MotionLimits`,
SW limits and session defaults at call time, so the GUI builds its forms without duplicating ranges. Each
generator validates its arguments with the same schema (one source).

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
  none of DATA `flags` bits 4–7 (ESTOP, HALT, FAULT, OVERRUN) and `status` bits 0–8, 13, 14 (PAUSED, LIMIT_*,
  LOAD_LIMIT, AFE_STALE/SATURATED/SETTLING/RATE_MISMATCH, LINK_WDG, POS_UNCERTAIN, NO_AFE_DATA) set — the
  ICD §7.6 window rule, one mask constant shared with the validator —, `setpoint_um` equal to its value at
  window start, `t ∈ [t_reached + settle, + capture]`
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
- **Report tab support** (GRQ-B-11, M4): `reports.list_recordings(root=None) -> list[RecordingInfo(folder,
  started_utc, marks summary, sequence_name, status COMPLETE | PARTIAL | FAILED, duration_s, has_report)]`
  (reads `meta.json` only, newest first); `reports.load_result(dir) -> ReportResult` (parsed `report.json`:
  marks, warnings, per-step/iteration results); `reports.build_async(dir, cal=None, tare=None, bend3p:
  Bend3pGeometry | None = None) -> Future[ReportPaths]` (`bend3p` default from the session geometry when
  enabled there).

---

## 12. Simulator (`io.sim`) — SYS-008, D-07, R4 §11

### 12.1 Structure

`SimBoard` sits on the board end of a `VirtualTransportPair` (or of a TCP socket in `io.sim.server`); it uses
the **same** `io.framing`/`io.protocol` codec (board direction: request decoders, response/DATA/EVENT
encoders) and the generated `params_gen` dictionary (ranges, defaults, `moving_ok`, `nvm`,
`reboot_required`) plus `core.params.check_hard_rules` (common-mode risk accepted, mitigated by the
check-vector replay and the twin differential tests, §12.5). NVM is a JSON file (or memory) with two
alternating records and the ICD §11.2/§11.3 rules (CFG_DIRTY, boot rules 2–5, migration by id, NVM_DEFAULTED);
REBOOT restarts the logic and sends EVENT BOOT. GET_INFO returns PROTO 1.0, PAYLOAD 1, the generated
dictionary hash, `feature_mask` per scenario (default: AFE, MOTION, HOMING, MOVE_UNTIL_LOAD, NVM, BUTTONS,
DRV_SIGNALS; `AFE_SYNTHETIC` selectable to mimic the M1 FW).

### 12.2 FW behaviour (`sim.check` + `sim.fw_logic`) — behavioural copy of ICD v0.2 §4–§9 (+ D-30) + SRS §3.2

- **Acceptance** is a separate pure function `sim.check.check(st: SimCheckState, cmd: str, payload: bytes)
  -> tuple[str, int]` implementing ICD §4.4 check order (TYPE → LEN → arguments `E_PARAM_ID`/`E_TYPE`/
  `E_RANGE` in payload order (incl. MOVE_UNTIL_LOAD `bound_um` equal to the position → `E_RANGE` 0, F-B-28)
  → state `E_STATE` (BLOCK mask §4.3 incl. PAUSED; ENABLE only bits 0 and 8; HOME without bits 4/5; JOG 0
  never; **RESUME only ESTOP (latched or input open), HALT and latched faults**) → `E_BUSY` →
  `E_CAUSE_ACTIVE` → `E_CONFIRM` → `E_CONFIG` (H1–H5 via `calc.paramrules`) → execution `E_NVM`). It is
  **written from the ICD text, not copied from `ref_cmdcheck.py`** (otherwise the differential check of
  §12.5 would prove nothing); `fw_logic` calls `check()` first and executes only on OK, so a NACK has no side
  effect by construction. `SimCheckState` is a view of the live simulator state with exactly the fields of
  `ref_cmdcheck.FwState` (`params`, `motion_state`, `enabling_left_ms`, `homed`, `pos_um`, `estop_latched`,
  `estop_input_open`, `estop_closed_ms`, `halt_latched`, `stop_btn_active`, `stop_btn_released_ms`,
  `faults`, `fault_causes`, `limit_start`, `limit_end`, `afe_stale`, `afe_saturated`, `raw`, `drv_power`,
  `alm_active`, `nvm_record_valid`, `paused`) for **`state_schema` 2** and can be **constructed from a
  vector's `state`** (`from_vector(state_defaults ⊕ state)`; an unknown `state_schema` fails the test).
  Execution of RESUME: clears only PAUSED, `pause_src` → NONE, EVENT PAUSE_CLEARED arg **3** (RESUME); an
  accepted HALT_CLEAR clears HALT and PAUSED with PAUSE_CLEARED arg 2 (D-31).
- **Execution** (`fw_logic`): the whole stop/enable policy table ICD §6.2 (E-stop, PC STOP modes 0/1, HALT
  from PC/button, **PAUSE from PC (0x3B) and button** with the motion-blocking PAUSED latch (`pause_src`,
  BLOCK bit PAUSED, D-30; cleared by an accepted RESUME 0x3C — refused while HALT/ESTOP/fault is latched — or
  HALT_CLEAR, D-31 / ICD v0.4) and RESUME_REQUEST on a
  button press while PAUSED, limits incl. LIMIT_WIRING,
  FW load limit incl. rails and regrow, AFE stale with fallback frames at `stream.fallback_hz`, link watchdog
  `safety.link_timeout_ms`, jog dead-man `motion.jog_timeout_ms` (VALID unchanged), step fault injection,
  homing failures HOME_NOT_FOUND / HOME_WIRING / ABORTED, HOME_DRIFT on re-homing, **K1_WELDED** (E-stop open
  while driver power stays on > the ICD delay), **driver power lost** (immediate stop, NOT_ENABLED, HOMED
  cleared, DRIVER_POWER 0, DRIVER_DISABLED 4), ALM refusal while powered, idle disable with
  `safety.release_band_raw`, DISABLE, reset); motion with analytic trapezoid profiles and exact step counts
  (`calc.motion`), soft limits, speed caps `min(v_max_travel | v_max_load, step-rate cap)` with the "loaded"
  predicate, un-homed jog cap, busy refusal, JOG with `bound_um` / `JOG_NO_BOUND` and on-the-fly changes,
  MOVE_UNTIL_LOAD with `cmp` evaluated per HX711 sample (pre-check before the first pulse), HOME at the START
  switch only (D-29 b) with the switch model, immediate stops freeze the position at the stop instant
  (completed steps; POS_UNCERTAIN), controlled stops with `a_stop` (clean halt only when the step period > 2 ms
  and the planned stop distance ≤ 1 step, D-29 d / D-30); ENABLE settle `motion.ena_settle_ms` (`E_BUSY` 2); steps/mm change rescales µm (machine zero =
  step count); SET_VALID boundary semantics and VALID auto-clear with VALID_CLEARED (cause); exactly one
  MOVE_DONE per motion (`value` µm, `value2` steps) preceded by STOPPED for stop sources; EVENT queue ≥ 8
  with overflow counter and the EVENT SEQ counter; GET_STATUS with all 86-byte fields; one DATA frame per
  HX711 conversion with the setpoint latched at data-ready, `frame_seq` advancing also for dropped frames,
  OVERRUN under TX congestion.

### 12.3 Models (`sim.models`, R4 §11)

| Model | Content (defaults) |
|---|---|
| `DriverModel` | steps/mm from the board parameter (default **800**, D-27 closed / ICD v0.4; scenarios may set 160 to rehearse the "DIP not changed" calibration); ideal step follower + optional first-order lag τ = 5 ms; ALM/PEND injection (report only, D-16); power removal (E-stop) → position lost, optional back-drive release of the specimen down to a holding force ≈ 300 N (R5 §1.2, configurable) |
| `SwitchModel` | START at x = −`home.offset` − 0.5 mm, END at stroke 300 mm + margin; bounce 3 × 0.2–2 ms; stuck-open (broken wire), swapped switches |
| `ButtonModel` | E-stop sense (NC), STOP/BREAK (NC), PAUSE (NO), polarity per parameters, bounce |
| `Hx711Model` | data-ready at 80 SPS × (1 + ε), ε = +0.5 % (±2 % configurable); 4-sample moving average of `offset + S·F(t)`, S = 3285 counts/N (SRS A-02, 3.0 mV/V; R4's 2190 is the 2 mV/V value), offset **50 000 counts** by default (≤ 64 425 = 1 % FS, so the default session does not clamp the 110 % FS threshold — SWD-P1-15; the scenario `clamp.simscn.json` keeps 125 000 counts and K 10 % below nominal to exercise the clamp path, VV-THR-01); Gaussian noise σ = 45 counts; spikes p = 1e-4; rail saturation; drift +20 counts/min; missed conversions; RATE pin 10/80 SPS; gain/channel scaling; power-down/re-init |
| `SpecimenModel` | none / linear spring `F = k·(x − x_c)` beyond contact (sign by `pull_dir`), bilinear yield k1 → k2, relaxation 2 % with τ = 30 s at constant x, break at `F_break` (F → 0), hysteresis for cycles, machine compliance C_m |
| `LinkModel` | latency 1–16 ms, frame loss rate, CRC corruption, PC→FW silence windows (watchdog tests), unplug/replug |

### 12.4 Scenario and control

`SimScenario` (`bird.bend.simscenario` v1, JSON — no YAML dependency; format exactly as in
`00_System/tools/README.md` "vocabulary v2": `world`, `params`, `schedule` with `t_ms`; units µm, counts, N,
ms, µs where named `_us`; default `load_offset_counts` 50 000, `motion.steps_per_mm` 800) holds every model
parameter, the initial world and an optional fault schedule. `SimControl` (thread-safe) implements the
**frozen vocabulary v2** (ICD v0.4, F-B-06 closed) as `act(action: str, **args) -> dict` with the README's
names, arguments and reply shape (`{"ok": true, ...}` / `{"ok": false, "error": ...}`): every action whose
Side is "both" — `clock`, `reset`, `query` (`world`, `pulses`, `outputs`, `wire_log`, `sent`, `flash`;
`edges`/`seam_log` return the model equivalent where defined), `estop`, `drv_power`, `button`, `limit`,
`wire`, `alm`, `pend`, `specimen`, `load_offset`, `afe`, `world_shift`, `inject` (incl. `drop_next`,
`duplicate_next`, `delay_next`, `corrupt_next` with `cmd`/`what`/`n`/`ms`), `rx_bytes`, `on_frame`,
`on_event` — plus `flash` at record level (S variant). Twin-only actions (`chatter`, `iwdg`, `clk`,
`isr_storm`, `where`, `sck_overrun`) answer `{"ok": false, "error": "twin only"}`. Typed wrappers
(`set_estop(open)`, `press/release(button)`, `set_specimen(...)`, …) call `act()`; a unit test runs the
README's action table against `act()` so the names cannot drift. The out-of-process server exposes the same
JSON-lines control port (default 5771) as the twin's 5761.

**GUI test coverage (GRQ-B-16, accepted):**

| Need (GUI tests G-06…G-36) | Means |
|---|---|
| every status flag / indicator | real causes via world actions (E-stop, buttons, limits, ALM/PEND, DRV_POWER, AFE stall/saturate/rate error, link silence, step fault, specimen load → LOAD_LIMIT, K1 = `estop` with `drv_power_follows: false`); for states without a cheap physical cause a **test-only** `SimControl.override_status(set_bits, clear_bits, duration_ms)` that marks the DATA/STATUS bits (flagged `sim_override` in the recording; never used by behavioural-equality tests) |
| FW EVENTs incl. RESUME_REQUEST | `button pause` press while PAUSED; `SimControl.emit_event(code, arg, value, value2)` test-only for rare codes (NVM_ERROR, CLK_FALLBACK, NOT_SETTLED) |
| link drop / stream gap | `inject link_silence`, `LinkModel` unplug/replug, `inject tx_congestion` |
| NACK / BUSY / MISMATCH on SET_PARAM | `SimControl.inject_nack(cmd, status, detail, count=1)`; `SimControl.inject_store_mismatch(key, stored_value)` (response "as stored" differs) |
| recorder write failure, thread stall | backend-side test hooks `Backend.test_hooks` (`core.testing`, only with `BackendSettings(test_hooks=True)`): `fail_recorder(exc: OSError | Exception, after_rows=0)` — any `OSError` incl. `OSError(errno.ENOSPC)` raised from the next write/flush; `set_free_space(bytes | None)` overrides the free-space probe; `stall_thread(name, ms)` |
| perf runs without GIL contention | out-of-process simulator `python -m bend_stand.io.sim.server` + endpoint `tcp://127.0.0.1:5770`; in-process: `SimScenario.realtime.max_cpu_pct` throttle |

**Validation hooks (SWD-P1-09, condition C3; all reachable through `Backend.sim` / `Backend.test_hooks`; the
action names are those of the frozen tools/README vocabulary v2):**

| Hook | Design |
|---|---|
| (a) whole backend on the lockstep clock | `BackendSettings(clock="lockstep", test_hooks=True)`: no backend thread is started; every thread loop body is a `step(now_ns)` method (Reader, Pipeline, Supervisor, Operation runner, Recorder, Worker queue). `test_hooks.advance(ms, step_ms=1)` advances the `LockstepSimClock` and runs, per step and in this fixed order: simulator → transport delivery → Reader → Pipeline → Supervisor → Operation runner → Worker → Recorder. GUI-thread calls are made from the test thread between advances; futures resolve during `advance`. Fully deterministic (no sleeps, no wall clock) |
| (b) wire log on TCP / sim server | `wire_log` on every transport (§4.1); the server keeps the board-side log, `act("query", what="wire_log")` |
| (c) Reader receive stamps | `test_hooks.rx_log()` → `RxRecord(t_host_ns, type, seq, frame_seq | None, event_code | None)` for every DATA/EVENT/response frame (bounded ring); the same stamp is the `t_host_ns` column of `SampleBatch` |
| (d) deterministic per-command faults | `LinkModel.drop_next(cmd, n=1, what="request" | "response")`, `duplicate_next(cmd, n=1, what=…)`, `delay_next(cmd, ms, what=…)`, `corrupt_next(cmd, n=1)`; also `act("inject", fault="drop_next", cmd="MOVE_ABS", what="response")` |
| (e) simulator sent-frame log (ground truth) | `act("query", what="sent")` → `[{type, seq, frame_seq, t_us, dropped}]` for every frame the board produced, incl. DATA frames dropped by TX congestion (`dropped: true`) |
| (f) world actions timed to wire events | `act("on_frame", cmd="RESUME", nth=1, delay_us=2000, then={"action": "button", "name": "stop", "pressed": true})` — the action runs `delay_us` of simulated time after the board received the nth matching request (also `on_event` for FW events it sends); in lockstep exact to 1 µs, in real time best effort (logged actual delay) |
| (g) ENOSPC | `fail_recorder(OSError(errno.ENOSPC, …))` (row above) |
| (h) free-space probe | `set_free_space(bytes)` (row above) |

### 12.5 Behavioural equality and the check-vector replay

The ICD and the shared vectors are normative.
1. **Codec**: `io.framing`/`io.protocol` pass every `protocol_vectors.json` item in **both** directions
   (PC→FW requests encoded byte-identical and decoded by the simulator; FW→PC frames encoded byte-identical
   by the simulator from `decoded`, decoded by the backend; `reencode: false` → `canonical_payload_hex`;
   `INVALID_PADDING` rejected; `streams` frames + counters).
2. **Check-vector replay (differential check, `tests/unit/sim/test_check_vectors.py`)**: for each vector of
   `check_vectors.json` (ICD v0.4: 509 vectors incl. RESUME and H5, **`state_schema` 2** — the replay refuses
   an unknown `state_schema`; the test never hard-codes the count), build the simulator state from
   `state_defaults ⊕ state`
   (parameter overrides applied directly to the sim's RAM table), feed `request.frame_hex` through the
   SimBoard's frame path and capture the response frame. Pass = STATUS equals `expect.status`; detail equals
   `expect.detail`; for NACKs the response bytes equal `response_frame_hex`; where present the PAUSED latch
   after the command equals `expect.paused_after`; and a NACK left the complete
   simulator state unchanged (state snapshot before/after, incl. parameters, latches, VALID, PAUSED, NVM).
   The test also asserts `icd_version` and `param_dict_hash` of the file equal the implemented ones, so a
   regenerated vector set cannot be silently skipped. Vector names are the pytest ids.
3. **Randomised differential check** (`test_check_random.py`, seeded by pytest-randomly): ≥ 2 000 random
   states × requests compare `sim.check.check()` with the oracle `ref_cmdcheck.Model.check()` (imported in
   tests only); a divergence is a simulator defect unless the ICD is ambiguous (then a finding to the
   Integrator, who adds the case to `gen_vectors.py`; B never writes vectors).
4. **Twin differential tests** (Integrator, `03_SW/tests/integration/test_sim_vs_twin.py`) run identical
   scripted scenarios on the simulator and on the FW host twin (`tcp://`) and compare responses, NACK codes,
   event sequences, DATA flags and positions (timing within tolerances).
The simulator reports the ICD version it implements in its `build` string (`SIM-ICD<x.y>`, from
`protocol_gen.ICD_VERSION`).

### 12.6 Clocks

`RealTimeSimClock` (1 ms tick thread; GUI demo, `rt`-marked tests and the out-of-process server) and
`LockstepSimClock` (the test advances virtual time; frames are produced into the virtual pipe as time
advances). Since v0.3 the **whole backend** can run on the lockstep clock (hook (a) of §12.4), not only the
simulator and the engines: all backend time comes from the `Clock` protocol (`monotonic_ns`), never from
`time.*` directly (checked by a grep test over `core`/`io`).

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
| 13.10 | `*.simscn.json` | `bird.bend.simscenario` v1 | §12.4 (shared with the FW twin, tools/README vocabulary v2) | SYS-008 |
| 13.11 | `travel_restore_pending.json` | `bird.bend.cal.travel_restore` v1 | board_uid, spm0, spm_trial, created_utc (exists only while a trial steps/mm may be in board RAM) | SW-CAL-001, §9.3.1 |

`*.bbsession.json` never contains the no-specimen mode or a tare (session-only, D-29 h/j). `meta.json` (13.7)
additionally holds `marks_at_start`, `marks_final`, `mark_edits[]` (GRQ-B-07), `no_specimen_mode`,
`thresholds.clamped`, `travel_cal_differs`.

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
  (`NotConnected`, `TransportError`, `CommandTimeout`, `CommandOutcomeUnknown`, `CommandNotExecuted`,
  `NackError(cmd, status, name, detail, detail_text)`, `IncompatibleFirmware`, `ConfigReadOnly`),
  `PreconditionError(GateResult)` (`GateRefused`, `ConfirmationRequired`), `CalibrationError`,
  `SequenceError`, `FileFormatError`, `RecorderError`. NACK details are decoded per ICD §4.2/§4.3 into text
  (§4.7, e.g. "HALT_CLEAR refused: STOP button still pressed").
- `stop()`, `halt()`, `pause()` never raise; failures are in the returned `StopResult` and in events.
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
    def stop(self, source: str = "gui") -> StopResult         # SW-STOP-001 (STOP mode 0)
    def halt(self, source: str = "gui") -> StopResult         # Pause/Break semantics (also used by "Abort")
    def pause(self, source: str = "gui") -> StopResult        # v0.2: FW PAUSE 0x3B (D-29 a); was -> None
    def resume(self, source: str = "gui") -> GateResult      # v0.3: RESUME 0x3C (D-31) + re-issue (§10.5)
    def tare(self, window_s: float | None = None) -> GateResult        # SW-TARE-001; progress → events "tare.state"
    def take_sample(self, window_s: float = 1.0) -> GateResult         # SW-ACQ-003
    def record_start(self) -> GateResult; def record_stop(self) -> GateResult   # v0.2: record_stop returns GateResult
    def hotkey_test_start(self, timeout_s: float = 10.0) -> GateResult # v0.2 GRQ-B-15 (result: event "hotkey.test")
    # global actions that wait for the board: futures
    def clear_stop_async(self, *, confirmed: bool = False) -> Future[ClearResult]  # HALT_CLEAR (SW-STOP-003); v0.3: while a
                                 # sequence is PAUSED needs confirmed=True and ends it STOPPED first (D-31)
    def estop_clear_async(self, *, confirmed: bool) -> Future[ClearResult]
    def fault_clear_async(self) -> Future[ClearResult]                 # ClearResult.cleared: FAULT names
    # v0.3.1 (D-34): every clear future resolves after one frame + (on timeout) a GET_STATUS check;
    # ClearResult.outcome NOT_CONFIRMED = "clear not confirmed — click again" (the operator may repeat)
    def stream_start_async(self) -> Future[None]; def stream_stop_async(self) -> Future[None]

    # immutable snapshots (thread-safe, cheap)
    def status(self) -> BackendStatus

    # sub-APIs
    config: ConfigAPI            # metas() / groups() (params_gen), values(), check(edits) -> list[Issue] (v0.2, GRQ-B-03),
                                 # write_and_verify_async(edits), save/load/defaults_async(), reboot_async() (v0.2),
                                 # save_board_config(path) / load_board_config(path) -> BoardConfigFile (SW-CFG)
    motion: MotionController     # §5.4: move_to, move_by, jog_start/update/stop, enable, disable(confirmed=), home(load_confirmed=),
                                 # set_test_zero, reset_test_zero (v0.2), set_valid, limits() -> MotionLimits (v0.2 fields),
                                 # check(kind, speed_mm_s=, accel_mm_s2=, target_mm=) -> GateResult (v0.2, GRQ-B-05)
    limits: LimitsAPI            # get() -> LimitConfig; set(cfg) -> list[Issue]; thresholds() -> ThresholdState;
                                 # recheck_async() -> Future[ThresholdState] (v0.2, GRQ-B-04);
                                 # set_no_specimen_mode(on, *, confirmed=False) -> GateResult (v0.2, D-29 h)
    data: DataView               # snapshot -> PlotSnapshot / xy(..., since=) -> XYSnapshot / latest / sequence_trace (§15.6)
    channels: ChannelRegistry    # keys, labels, units, groups, available + reason (SW-RT-002); topic channels.changed (v0.2)
    marks: MarksAPI              # get/set TestMarks (incl. custom fields), list/save/load presets (SW-META); edits while
                                 # recording → MARK_EDIT rows (GRQ-B-07)
    session: SessionAPI          # get/set SessionSettings, load/save session file
    tare_engine: TareEngine      # state()/subscribe()/cancel(); undo() -> GateResult (v0.2, GRQ-B-13)
    travel_cal: TravelCalEngine; load_cal: LoadCalEngine     # Engine protocol §9.1 (v0.2: PHASES, continue_label,
                                 # continue_moves, abort_reason); load_cal.finish_early(), load_cal.retake(i) (v0.2)
    calibrations: CalibrationStore   # active load/travel records, history list; restore_travel_async() (v0.2, §9.3.1)
    sequencer: SequencerAPI      # new/load/save, validate, expand (Plan), planned_path, generators, generator_schemas()
                                 # (v0.2, GRQ-B-18), insert_block, start/pause/resume/stop/abort/continue_, status()
    reports: ReportAPI           # build_async(dir, cal=None, tare=None, bend3p=None) (v0.2 bend3p), list_recordings(root=None),
                                 # load_result(dir) (v0.2, GRQ-B-11)
    events: EventBus             # subscribe(topic, cb, weak=True) -> Token; unsubscribe(token)
    sim: SimControl | None       # present when connected to "sim" (§12.4)
    test_hooks: TestHooks | None # only with BackendSettings(test_hooks=True) (v0.2, GRQ-B-16)
```

### 15.2 `BackendStatus` (polled; rebuilt on change, returned by reference)

| Field | Content |
|---|---|
| `link` | state, why, endpoint, compat, `DeviceInfo` summary (incl. `features`), `LinkStats` (frames OK, lost FW/link, events lost, CRC/len/timeout errors, late responses, command timeouts, FW counters from STATUS) |
| `stream` | on/off, measured rate, rate mismatch |
| `indicators` | `Indicators` (§6.5): every item `Indicator(state ON/OFF/UNKNOWN, since_t_us, source, value, clear_hint)` (v0.2, GRQ-B-02) |
| `motion` | moving, homed, enabled, enabling_left_ms (v0.2), paused (v0.2), position_mm, test_position_mm, commanded_target_mm, pending_target_mm, x_zero_mm, owner, `MotionLimits` (v0.2: v_travel/v_load/v_step_rate/v_unhomed/a_max, loaded, v_cap, v_cap_fw), SW travel range |
| `safety` | SW trip latch, active warnings, `ThresholdState` (v0.2: + `clamped`, effective levels), load-limit input valid + reason, `no_specimen_mode` (v0.2) |
| `calibration` | active load (K, status, LOW_SPAN, date, AFE mismatch → invalid for limits), active travel (spm, date), board spm, `travel_cal_differs` + `restore_pending` (v0.2) |
| `tare` | state, tare_raw, age, id, `can_undo` (v0.2) |
| `operation` | owner kind + engine phase; `sequence: SeqStatus` (v0.2: `plan_total_s`, `remaining_s`, `paused_source`) |
| `recording` | state, folder, rows, rows_lost, queue fill %, failure text |
| `gates` | `Mapping[GateId, GateResult]` for **all** ids of §5.6 (v0.2 adds `pause`, `resume`, `stream_start`, `stream_stop`, `record_start`, `record_stop`, `sample`, `config_write`, `cal_travel_start`, `cal_load_start`, `sequence_edit`, `valid_toggle`, `test_zero`, `no_specimen`, `hotkey_test`; GRQ-B-01) |
| `hotkey` | `HotkeyStatus(mode: REGISTERED / LL_HOOK / UNAVAILABLE, reason, test_running)` (v0.2, GRQ-B-15) |
| `cfg_dirty`, `config_read_only`, `reboot_pending`, `nvm_defaulted` | STATUS `sys_flags`, hash mismatch (v0.2 adds the last two) |

### 15.3 Event topics (`EventBus`, payloads are frozen dataclasses)

`link.state`, `link.compat`, `device.info`, `device.params`, `device.status`, `fw.event` (decoded FW EVENT),
`stop.issued` (StopResult), `stop.confirmed` / `stop.unconfirmed` (v0.2: for STOP, HALT and PAUSE; replaces
`halt.confirmed` / `halt.unconfirmed`), `indicators.changed`, `safety.trip`, `safety.warning`,
`safety.thresholds`, `safety.no_specimen` (v0.2), `motion.done` (MoveDone), `motion.target`,
`motion.dropped`, `tare.state`, `cal.travel.state`, `cal.load.state` (EngineState), `cal.travel.restore`
(v0.2), `channels.changed` (v0.2, GRQ-B-14), `seq.status`, `seq.step_result`, `seq.window`, `rec.state`,
`rec.failure`, `sample.taken`, `marks.edited` (v0.2), `resume.ignored` (v0.3, GF-11), `hotkey.state`, `hotkey.test` (v0.2: measured delay
ms or timeout), `report.ready`, `log`.

### 15.4 Rules for the GUI

1. Create `Backend`, call `start()` before showing the main window (hotkey and liveness active before Connect).
2. Only `stop`, `halt`, `pause`, `resume`, `tare`, `take_sample`, `record_*`, `hotkey_test_start`,
   `status`, `data.*`, `config.check`, `motion.check`, `limits.get/set/set_no_specimen_mode`,
   `tare_engine.undo`, engine `state()`/methods and `motion` commands may be called in the GUI thread (all
   non-blocking; motion commands return a `MoveTicket` immediately and refuse with `PreconditionError`
   before sending); everything that waits for the board is `*_async`. No `.result()` in the GUI thread.
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
10. An `Indicator` in state `UNKNOWN` is drawn as "unknown" (grey), never as OK (GRQ-B-02).
11. A Continue button whose `EngineState.continue_moves` is True is mouse-only and labelled with
    `continue_label` (GRQ-B-10).
12. No-specimen mode: the GUI shows C-10 via the `no_specimen` gate CONFIRM item and a permanent banner while
    `status().safety.no_specimen_mode` is True.

### 15.5 API delta v0.1 → v0.2 (for Implementer D)

| # | Change | Kind | Reason |
|---|---|---|---|
| A-01 | `Backend.pause(source) -> StopResult` (was `-> None`); sends FW PAUSE 0x3B; confirmation via `stop.confirmed/unconfirmed`; PAUSED blocks all motion in the FW (D-30) — the `move`/`jog`/`home` gates REFUSE while PAUSED | changed | D-29 a, D-30, GF-01 |
| A-02 | `Backend.resume(source="gui") -> GateResult`, then re-issue of the interrupted step (sequence) / nothing (manual); EVENT PAUSED/STOPPED end the jog session and drop the pending target — **superseded in v0.3 by B3-01/B3-02 (RESUME 0x3C)** | changed semantics | D-30, §10.5 |
| A-03 | `Backend.record_stop() -> GateResult` (was `-> None`) | changed | gate `record_stop` |
| A-04 | `Backend.hotkey_test_start(timeout_s=10.0) -> GateResult`; `status().hotkey: HotkeyStatus`; topic `hotkey.test` | new | GRQ-B-15 |
| A-05 | `status().gates` covers all `GateId`s of §5.6 (15 new ids) | extended | GRQ-B-01 |
| A-06 | `Indicators` items are `Indicator(state, since_t_us, source, value, clear_hint)` with `UNKNOWN`; new items `k1_welded`, `home_drift`, `drv_pwr`, `no_afe_data`, `reboot_pending`, `nvm_defaulted`, `clk_fallback`, `no_specimen_mode`, `travel_cal_differs`, `afe_synthetic`, `recording_failed`; `paused.source` | changed | GRQ-B-02, D-29 c, ICD §7.6 |
| A-07 | `config.check(edits) -> list[Issue]`; `config.reboot_async()`; `WriteItem` status `REBOOT_REQUIRED` | new | GRQ-B-03, SD-06 |
| A-08 | `limits.recheck_async() -> Future[ThresholdState]`; `ThresholdState.clamped` + effective levels | new | GRQ-B-04, D-29 g |
| A-09 | `limits.set_no_specimen_mode(on, *, confirmed=False) -> GateResult`; `status().safety.no_specimen_mode`; topic `safety.no_specimen` | new | D-29 h, GF-07 |
| A-10 | `motion.check(kind, *, speed_mm_s, accel_mm_s2, target_mm) -> GateResult`; `MotionKind` | new | GRQ-B-05 |
| A-11 | `motion.reset_test_zero()` | new | GRQ-B-06 |
| A-12 | `MotionLimits` fields: `v_travel_mm_s`, `v_load_mm_s`, `v_step_rate_mm_s`, `v_unhomed_mm_s`, `a_max_mm_s2`, `loaded`, `v_cap_mm_s`, `v_cap_fw_mm_s` (was one `v_max`) | changed | D-29 e, SD-03 |
| A-13 | `MoveDone(reason TARGET / LOAD_THRESHOLD / BOUND / SOFT_LIMIT / JOG_ZERO / STOPPED, pos_mm, pos_steps, t_us, stop_cause)` | changed | ICD §8.3 |
| A-14 | `data.snapshot() -> PlotSnapshot` (common column time, `t_end_dev_s`, per-key min/max + `vstate`); `data.xy(..., since=)` → `XYSnapshot` with `vstate`; `LatestSample.state` adds `EXTRAPOLATED` | changed | GRQ-B-08/09 |
| A-15 | Engine: `PHASES`, `EngineState.continue_label`, `continue_moves`, `abort_reason`; `start()` returns `GateResult`; `load_cal.finish_early()`, `load_cal.retake(i)`; WARN fit via `needs_confirmation`; PAUSE aborts wizards | changed/new | GRQ-B-10 |
| A-16 | `tare_engine.undo() -> GateResult`; `status().tare.can_undo` | new | GRQ-B-13 |
| A-17 | topic `channels.changed` | new | GRQ-B-14 |
| A-18 | `SeqStatus.plan_total_s`, `remaining_s`, `paused_source` | new | GRQ-B-12 |
| A-19 | `reports.list_recordings(root=None)`, `reports.load_result(dir)`, `build_async(..., bend3p=None)` | new | GRQ-B-11 |
| A-20 | `sequencer.generator_schemas()` | new | GRQ-B-18 |
| A-21 | `calibrations.restore_travel_async()`; `status().calibration.travel_cal_differs`, `restore_pending`; topic `cal.travel.restore` | new | GF-05, GRQ-B-17 |
| A-22 | `SimControl.act(action, **args)` (twin vocabulary), `override_status`, `emit_event`, `inject_nack`, `inject_store_mismatch`; `Backend.test_hooks` (`fail_recorder`, `stall_thread`); endpoint `tcp://127.0.0.1:5770` for the out-of-process simulator | new | GRQ-B-16 |
| A-23 | topics `stop.confirmed` / `stop.unconfirmed` replace `halt.confirmed` / `halt.unconfirmed`; new `safety.no_specimen`, `cal.travel.restore`, `marks.edited`, `hotkey.test` | changed | §15.3 |
| A-24 | `status().reboot_pending`, `nvm_defaulted`; `status().motion.enabling_left_ms`, `paused` | new | ICD §7.2 |
| A-25 | Entry point: `bend-stand = bend_stand.__main__:main`; D provides `bend_stand.gui.app.run(backend, args) -> int` | changed | D-29 n |

Nothing was removed except the two renamed topics (A-23).

### 15.5a API delta v0.2 → v0.3 (for Implementer D)

| # | Change | Kind | Reason |
|---|---|---|---|
| B3-01 | `Backend.resume(source)` sends **RESUME 0x3C** (clears only PAUSED), then re-issues the interrupted sequence step; manual mode: RESUME only. A RESUME refused because HALT/ESTOP/fault is latched returns/publishes a `GateResult`/event with that latch (expected outcome, the latch itself terminates the sequence) | changed semantics | D-31, SWD-P1-02 |
| B3-02 | `clear_stop_async(*, confirmed=False)`: while a sequence is PAUSED the `clear_stop` gate carries CONFIRM "ends the paused sequence"; with `confirmed=True` the sequence ends STOPPED (`CLEARED`), then HALT_CLEAR (clears HALT and PAUSED). v0.2 refused it | changed | D-31, SWD-P1-02 c |
| B3-03 | `resume` gate REFUSEs while HALT, ESTOP or a fault is latched (FW would refuse RESUME); hint "Clear stop / clear the fault first" | changed | D-31 |
| B3-04 | Motion gates (`move`, `jog`, `home`) REFUSE while PAUSED with hint "Resume clears PAUSE"; `MoveTicket` outcomes add `REFUSED_PAUSED` (expected; show as info, not as error) and `REFUSED(nack)` | changed | D-30, D-33 k, SWD-P1-03 |
| B3-05 | `sequence_start` gate: new REFUSE items `PAUSED`, `POS_UNCERTAIN` ("re-home"), `AFE_RATE_MISMATCH`, `ALM`, `DRV_PWR_OFF`, each with its own text | changed | D-33 b, SWD-P1-04/08 |
| B3-06 | `SeqStatus` / `seq.step_result`: step status `NOT_REACHED` (load step reached its bound without the target load; sequence ends STOPPED, reason `NOT_REACHED`); new end reasons `DRIVER_ALARM` (ALM during the sequence, controlled STOP) and `CLEARED` (B3-02) | new values | D-32, D-33 c/d |
| B3-07 | `Step.travel_bound_mm` removed (the load-step bound is fixed: nearer of soft limit and enabled SW travel limit); `Step.step_time_s` is no longer a LOAD timeout (timeout = 1.2 × travel time to the bound + 10 s); the editor hides both for LOAD steps | removed / changed | D-32, D-33 d |
| B3-08 | Travel-calibration plausibility candidates are **800 / 160** steps/mm in the `ConfirmRequest` text (was 160 / 1280); `SessionSettings.expected_spm` default **800** (D-27 closed: 4000 p/rev closed loop) | changed text / default | SRS SW-CAL-003, SWD-P1-07, D-27 |
| B3-09 | `BackendSettings(clock="lockstep", test_hooks=True, wire_log=True)`; `test_hooks.advance(ms, step_ms=1)`, `rx_log()`, `wire_log()`, `fail_recorder(exc, after_rows=0)` (any `OSError`, e.g. ENOSPC), `set_free_space(bytes)`; `SimControl` / `act()`: `drop_next`, `duplicate_next`, `delay_next`, `corrupt_next`, `query(what="sent" / "wire_log")`, `on_frame(cmd, nth, delay_us, then)`, `on_event(...)` | new (test only) | SWD-P1-09, C3 |
| B3-10 | `LinkStats` adds `dup_frames`, `seq_anomalies`; `status().link` counts DEGRADED from timeouts only (NACKs never) | new | SWD-P1-12 b, D-33 k |
| B3-11 | Derived channel `force_rate_n_s` is NaN (reason `GAP`) for 9 samples after a gap / missed conversion / invalid sample | changed semantics | SWD-P1-12 a |
| B3-12 | Staircase generator: `count` includes start and end; `return_to_zero` inserts a zero step after every non-zero level incl. the last; consecutive duplicate targets are not generated (§10.6) | defined | SWD-P1-12 c |
| B3-13 | Jog refresh every 80 ms (backend; the GUI still only calls `jog_start/jog_stop`) | internal | SWD-P1-11 |
| B3-14 | Default simulator scenario: cell offset 50 000 counts (no threshold clamp); `clamp.simscn.json` for the clamp path | changed default | SWD-P1-15 |
| B3-15 | Supported runtime Python 3.14; `03_SW/requirements.txt` + `requirements-dev.txt`; pytest markers `validation`, `winint`, `rt` registered, `--strict-markers` | packaging | D-33 i, SWD-P1-16, D-31 |
| B3-16 | Gate item codes: FW-mirroring items use the generated `BLOCK_BITS` names (then `DATA_STATUS_BITS`), SW-only items use the `GateCode` StrEnum (no collisions, unit-tested) | defined | GF-11 |
| B3-17 | New topic `resume.ignored` (`ResumeIgnored(source, reason: GateResult, t_us)`) when a RESUME_REQUEST or a GUI resume is not executed | new | GF-11 |
| B3-18 | Indicator keys = generated bit names lower-cased (`stop_btn`, `pause_btn`, …; were `stop_button` / `pause_button`) | renamed | GF-12 |
| B3-19 | `calibrations.resolve_travel_difference_async(action: "restore" \| "keep_board" \| "ignore_session") -> Future[TravelDiffState]`; `status().calibration.travel_diff: TravelDiffState` (M3) | new | GRQ-B-19 / GF-13 |
| B3-20 | Entry contract: `main()` builds the Backend **unstarted**, `args.endpoint: str \| None`; `gui.app.run(backend, args)` starts it, connects if `args.endpoint`, and shuts it down; `sequencer.start(seq, *, confirmed=False) -> GateResult` (was `confirmations=`); `confirmed=True` confirms the CONFIRM items as evaluated at the call | changed | GF-14 |
| B3-21 | One status name `NOT_REACHED` for a load step that reaches its bound (no `LOAD_NOT_REACHED`); `Step.travel_bound_mm` is **removed** (the bound is fixed by D-33 d); the local refusal of a bound not ahead of the axis is the different step error `BOUND_NOT_AHEAD` | confirmed | GF-17, D-32, D-33 d |

No other member changed; the v0.2 surface (A-01…A-25) is otherwise unchanged. B3-16…B3-21 answer D's GF-11…GF-17 (SW_design_GUI v0.2).

### 15.5b API delta v0.3 → v0.3.1 (D-34, ICD v0.4 / v0.4.1)

| # | Change | Kind | Reason |
|---|---|---|---|
| B31-01 | `clear_stop_async`, `estop_clear_async`, `fault_clear_async` send **one** frame on the priority path and are **never re-sent**; on a timeout the future resolves after a GET_STATUS check. `ClearResult` gains `confirmed: bool` and `outcome: OK \| REFUSED \| NOT_CONFIRMED`; NOT_CONFIRMED → show "Clear not confirmed — click again" (the button stays enabled from the gate) | changed semantics | D-34 |
| B31-02 | `resume()`: a RESUME timeout is resolved by GET_STATUS (`PAUSED` = 0 and no new EVENT PAUSED since); otherwise the sequence stays PAUSED with "Resume not confirmed — press Resume again" | clarified | ICD v0.4 |
| B31-03 | `SimControl.act()` uses the frozen tools/README vocabulary v2 (names, arguments, reply shape); twin-only actions reply `{"ok": false, "error": "twin only"}`; scenario defaults: offset 50 000 counts, steps/mm 800 | aligned | ICD v0.4, F-B-06 |
| B31-04 | `config.check()` reports H5 (`afe.timeout_ms` vs `afe.rate_sps`) like H1–H4; the write plan orders H5 per ICD §11.4 | new rule | D-33 a |

Nothing else changed for the GUI.

### 15.6 Result types added in v0.2

```python
@dataclass(frozen=True)
class PlotSnapshot:                           # data.snapshot(keys, window_s, px_width)
    t_end_dev_s: float                        # device time of the newest sample (relative axis origin)
    t_col_s: np.ndarray                       # float64[px_width], column centres relative to t_end (≤ 0)
    series: Mapping[str, SeriesMinMax]        # per requested key
    level: int                                # pyramid level used (0 = raw)
@dataclass(frozen=True)
class SeriesMinMax:
    lo: np.ndarray; hi: np.ndarray            # float32[px_width], NaN = no sample in that column
    vstate: np.ndarray                        # uint8[px_width]: 0 OK, 1 EXTRAPOLATED, 2 INVALID, 3 NO_DATA (worst of column)
@dataclass(frozen=True)
class XYSnapshot: x: np.ndarray; y: np.ndarray; vstate: np.ndarray; t_end_dev_s: float
@dataclass(frozen=True)
class LatestSample: key: str; value: float; state: Literal["n/a", "STALE", "SATURATED", "INVALID",
                                                          "EXTRAPOLATED", "OK"]; t_dev_s: float
@dataclass(frozen=True)
class Indicator: state: Literal["ON", "OFF", "UNKNOWN"]; since_t_us: int | None; source: str | None
                 value: float | None; clear_hint: str | None
@dataclass(frozen=True)
class HotkeyStatus: mode: Literal["REGISTERED", "LL_HOOK", "UNAVAILABLE"]; reason: str; test_running: bool
```

**Hotkey test mode** (GRQ-B-15, TS D-58 pattern): `hotkey_test_start(timeout_s)` (gate `hotkey_test`:
refused while moving, while an operation runs or with the hotkey unavailable) arms a test window ≤ 10 s.
During the window every motion gate refuses ("Pause/Break key test running"), so a key press in the window
is measured (hook stamp → callback, ms) and **does not send HALT**; event `hotkey.test(delay_ms | None)`
ends the window (press or timeout). Outside the window the key always sends HALT.

### 15.7 Answers to the GUI requests (SW_design_GUI §11.2)

| Request | Answer | Where |
|---|---|---|
| GRQ-B-01 gates | **accepted** — all 13 ids + `no_specimen`, `hotkey_test`; `sequence_start` has a **CONFIRM** (not only WARN) item "Pause/Break key unavailable" per the Q27 default | §5.6, A-05 |
| GRQ-B-02 freshness + PAUSED source | **accepted** — `Indicator.state UNKNOWN`; `paused.source` from STATUS `pause_src` (ICD v0.2, 86-byte STATUS) and EVENT PAUSED arg | §6.5, A-06 |
| GRQ-B-03 `config.check` | **accepted** | §5.3, A-07 |
| GRQ-B-04 `limits.recheck_async` | **accepted** | §6.3, A-08 |
| GRQ-B-05 `motion.check` | **accepted** (also returns cap errors for the loaded/travel cap that applies now) | §5.4, A-10 |
| GRQ-B-06 `reset_test_zero` | **accepted** | §5.4, A-11 |
| GRQ-B-07 mark edits while recording | **accepted as proposed** (frozen at start, MARK_EDIT rows, `marks_final` at stop) | §8, §13 |
| GRQ-B-08 snapshot format | **accepted** — common column time, `t_end_dev_s`, per-key `vstate`; `xy(since=)` | §7.5, §15.6, A-14 |
| GRQ-B-09 EXTRAPOLATED | **accepted** | §7.1, A-14 |
| GRQ-B-10 engine additions | **accepted** (a) `finish_early`, (b) `retake(i)`, (c) WARN → `needs_confirmation`, (d) PAUSE terminates wizards like STOP; `continue_label`, `continue_moves`, `PHASES` | §9.1, §9.4, A-15 |
| GRQ-B-11 report tab | **accepted** (M4) | §11, A-19 |
| GRQ-B-12 remaining time | **accepted** (`plan_total_s`, `remaining_s`; None for infinite loops) | §10.3, A-18 |
| GRQ-B-13 tare undo | **accepted** (one level, PO default Q27) | §9.5, A-16 |
| GRQ-B-14 `channels.changed` | **accepted** | A-17 |
| GRQ-B-15 hotkey status + test mode | **accepted**; test-mode key press never sends HALT because motion is refused during the window | §15.6, A-04 |
| GRQ-B-16 SimControl coverage + perf mode | **accepted**; flag overrides are explicit test-only APIs (never used by equality tests); perf mode = out-of-process simulator over TCP | §12.4, A-22 |
| GRQ-B-17 travel cal after link loss / E-stop | **accepted** — restore rule with a persisted restore-pending record | §9.3.1, A-21 |
| GRQ-B-18 generator schemas | **accepted** (M4) | §10.6, A-20 |
| GF-04 latest wins | closed by D-29 i; `pending_target_mm` in status | §5.4 |
| GF-05 active travel calibration | definition + restore rule (= SRS v0.3 SW-CAL-001) | §9.3.1 |
| GF-07 first-use flow | closed by D-29 h (no-specimen mode) | §6.7 |
| GF-10 | noted: jog refresh stays in the backend (gated by `gui_beat`); D's `ConfirmDialog` for all safety confirmations matches §5.6 | §5.4 |

No request is rejected.

---

## 16. Performance design (NFR-001..004)

| Item | Design value | Basis |
|---|---|---|
| DATA rate / size | 80 Hz × 26 B = 2.08 kB/s (2.26 % of the link); all FW→PC traffic ≤ 3.6 % | ICD §10, IF-011 |
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

## 17. Reuse from Thrust_Stand_HAW (current HEAD, read-only, D-02, D-29 m)

Copied files carry `# Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/<path> @<hash> (<as-is | adapted:
what>)` (SYS-010). Per D-29 m copies are taken from the reference's **current HEAD** at copy time; the hash
is read from the files `.git/HEAD` and `.git/refs/heads/<branch>` (or `.git/packed-refs`) — plain file reads,
no git command or any other tool is run inside the reference (D-02). The hash is written into every origin note and
into this table (column "Notes") when the copy is made in M1 (F-B-11 closed).
The verdicts below were made at 9473c68; modules that exist only in later TS commits (`calibration.py`,
`tare.py`, `capture.py`, `calc_stage.py`) may now be copied too, after a diff review against this design.

| Thrust_Stand source | Bend stand target | Verdict | Notes |
|---|---|---|---|
| `io/crc.py` | `io/crc.py` | as-is | variant confirmed by the ICD |
| `io/framing.py` | `io/framing.py` | adapt | MAX_LEN 160, buffer ≥ 336 B, 20 ms timeout, constants from `protocol_gen` (ICD §2.3) |
| `io/transport.py` | `io/transport.py` | as-is + | add `wire_log`, `sim` endpoint in the factory |
| `io/reader.py` | `io/reader.py` | as-is | async queue → Pipeline (DATA + EVENT in order) |
| `io/ports.py` | `io/ports.py` | as-is | enumeration only (D-06) |
| `io/win_hotkey.py` | `io/win_hotkey.py` | as-is (rename) | callback → `Backend.halt("hotkey")` |
| `io/protocol.py` | `io/protocol.py` | adapt (tables from `protocol_gen`) | new command set, DATA/STATUS/EVENT codecs from the ICD |
| `io/simulator.py` | `io/sim/board.py`, `fw_logic.py` | adapt architecture | keep loopback, `_cmd_<name>` dispatch with NACK order, fault injector, NVM-as-JSON; replace thrust physics by stepper/homing/specimen |
| `io/sim_sensors.py` | `io/sim/models.py` (`Hx711Model`) | adapt | `_Afe` rate/gain/settle/saturation reused; add filter, spikes, drift |
| `io/elevation.py` | `io/elevation.py` | as-is (optional) | warns that the hotkey is not delivered to elevated windows |
| `core/link.py` | `core/link.py` | adapt | FrameWriter + CommandChannel kept; new lanes/retry classes, motion epoch, `PrioritySender`, `StopConfirmer` (from `EstopConfirmer`), heartbeat 150 ms |
| `core/device.py` | `core/device.py` | adapt by splitting | keep ≈ 900 generic lines (connect, params, write_and_verify, stream, VALID, supervisor/reconnect); drop arm/throttle/DShot/temp/self-test; new motion + stop/clear section |
| `core/model.py` | `core/model.py` | adapt | LinkState, DeviceInfo, BoardStatus, flags, VerifyItem |
| `core/events.py`, `observers.py`, `errors.py` | same | as-is (rename) | error classes extended (§14) |
| `core/liveness.py`, `clock.py`, `timing.py` | same | as-is | thresholds per §4.6 |
| `core/linkstats.py` | same | adapt | counter names |
| `core/params.py` | `core/params.py`, `calc/paramrules.py` | adapt | bend hard rules H1–H4 + write order (pure, in `calc`), `*.bbboard.json` (no jsonschema) |
| `core/params_gen.py` | `core/params_gen.py`, `core/protocol_gen.py` | generated | Integrator's generators |
| `core/samples.py`, `pipeline.py`, `sensors.py`, `channels.py` | same | adapt | TimeUnwrapper, GapCounter as-is; new dtype, classifier, scaling, derived stages |
| `core/ringbuffer.py` | same | as-is | — |
| `core/motor_gate.py` | `core/gates.py` | adapt pattern | GateItem/Severity/GateResult; new gate set |
| `core/backend.py` | `core/backend.py` | adapt | facade per §15 |
| `core/selftest.py`, `core/esc_telemetry.py` | — | not reused | thrust-specific |
| TS-SWD §7.2/7.3 (calibration, tare), §8 (limits), §9 (sequencer), §10 (recording/report); TS HEAD `calibration.py`, `tare.py`, `capture.py` | `core/calibration`, `tare.py`, `capture.py`, `safety.py`, `sequencer/`, `recorder.py`, `report.py` | adapt where a HEAD module exists, else new code from this design | D-29 m allows copying from HEAD with the hash recorded |
| `tests/unit/test_layering_packaging.py` | `03_SW/tests/unit/test_layering.py` | as-is (rename) | SW-PLT-002 |
| `tests/unit/core/scripted_board.py`, `sim_rig.py`, `tests/unit/io/test_vectors.py` pattern | `03_SW/tests/unit/...` | adapt | command-channel edge cases, Device + SimBoard rig, vector tests |
| `__main__.py`, `run_sim.bat`, `pyproject.toml`, `tests/conftest.py` | `bend_stand/__main__.py`, `03_SW/run.bat`, `03_SW/run_sim.bat`, `03_SW/pyproject.toml`, `03_SW/tests/conftest.py` (owner B, D-29 n) | adapt | fewer dependencies (KD-11) |

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
| `paramrules.check_hard_rules`, `write_order` | H1–H5, SET order keeping rules true (ICD §11.4) | `check_vectors.json` `E_CONFIG` + property test | SW-CFG-003, IF-010 |
| `rounding.round_half_away` | float → int | TV-TC (`um_to_steps`) | SYS-003 |
| `motion.um_to_steps`, `steps_to_um`, `rate_cap_um_s` | wire µm ↔ steps (sim, travel cal), speed cap from the step rate | TV-TC + **`00_System/tools/vectors/units_vectors.json`** (ICD v0.4 §0.1: `spm` is the binary32 value from `spm_f32_hex`, computed exactly as the FW does — every value must match exactly, no tolerance; the test loads the file in place and asserts `icd_version`/`param_dict_hash`) | FW-MOT-002 (sim), SW-CAL-002, FW-MOT-009 |
| `motion.plan_trapezoid`, `move_duration_s`, `stop_distance_mm` | plan durations, sim kinematics | TV-M planner | SW-SEQ-002, SYS-008 |
| `motion.ramp_periods`, `avr446_periods` | reference ramp (FW vector cross-check, sim) | TV-M | SYS-008 |
| `stats.robust_window_stats`, `se_ar1`, `drift_slope`, `window_acceptance` | capture statistics | TV-RS | SW-CAL-006, SW-TARE-002/003 |
| `loadcal.load_calibration`, `point_acceptance`, `low_span` | fit + status | TV-LC | SW-CAL-006/007/008 |
| `loadcal.fw_raw_limits`, `clamp_inward` | FW thresholds, clamp to the FW range with effective levels | TV-T + clamp vectors (K below nominal) | SAF-SW-002 |
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
  sample batches; simulator logic with `LockstepSimClock`; the check-vector replay and the randomised
  `sim.check` vs `ref_cmdcheck` differential test (§12.5).
- Markers (registered in `pyproject.toml`, `--strict-markers`): `req`, `unit`, `component`, `integration`,
  `validation`, `gui`, `winint`, `rt`, `perf`, `soak`, `hil`, `slow`; `winint`/`perf`/`soak` skip unless
  requested (`--run-winint`, `--run-perf`, `--run-soak`), `rt` runs by default.
- `03_SW/tests/conftest.py` (owner B, D-29 n): fixtures `repo_root`, `vectors_dir`, `protocol_vectors`,
  `check_vectors` (loaded in place from `00_System/tools/vectors/`, never copied), `ref_oracle` (adds
  `00_System/tools` to `sys.path` for tests only), `fake_clock`, `lockstep_sim`, `sim_rig`; options
  `--run-perf`, `--run-soak` (those markers skip by default; `hil` always skips unless `--run-hil` **and** the
  environment variable `BEND_HIL_PORT` is set — D-06); an **autouse guard** that replaces
  `serial.Serial.open` with a function raising `HardwareAccessForbidden` in every test without the `hil`
  marker, so no test can open a COM port (D-06).
- Component rig `SimRig` = Backend + SimBoard + fake hotkey backend (no Qt), used for SAF-SW timing tests
  (`wire_log`), recorder integrity (1 h at 80 Hz in accelerated lockstep), sequence scenarios.
- Validator F (`tests/validation/**`) uses only the public API (§15) and the ICD/R4 vectors; Integrator
  (`tests/integration/**`) uses `tcp://` against the FW twin and the sim-vs-twin differential tests.

---

## 20. Requirement → design traceability

"B" = designed here; "D" = GUI part in `SW_design_GUI.md` (backend support named). IDs and milestones per
**SRS v0.3** (86 SW-side IDs: SAF-SW 6, IF 12, SW 60, NFR 8; SW-LIM-004 new; SW-MAN-002/003, SW-CAL-001,
SW-SEQ-006, SAF-SW-001/002/004/005, SW-STOP-004 changed — rows below updated).

| ID | Design (this doc) | Modules | Share |
|---|---|---|---|
| SYS-002 (PC part) | §6, §7, §9, §10, §11 | `calc`, `core` | B |
| SYS-003 | §0, §5.4, §18 | `calc.units`, `calc.rounding`, `core.device` | B (+ D N/kgf selector) |
| SYS-008 | §12 | `io.sim` | B |
| SYS-010 | §17 | origin notes | B |
| SAF-SW-001 | §6.1, §6.2, §5.6, §6.7 | `core.safety`, `core.gates`, `calc.limits` (invalid input incl. no tare / AFE mismatch → motion refused; no-specimen mode the only exception) | B |
| SAF-SW-002 | §6.3 | `core.safety.ThresholdManager` (clamp inward + warning, re-send after LOAD/DEFAULT), `calc.loadcal.fw_raw_limits/clamp_inward` | B |
| SAF-SW-003 | §4.6, §6.4 | `core.link.LinkSupervisor` (PING after 150 ms idle — within the SRS 200 ms / ICD 150–200 ms window, gap ≤ 250 ms) | B (+ D "LINK LOST" display) |
| SAF-SW-004 | §5.5, §5.6, §6.7 | `core.gates` (CONFIRM items incl. C-10 no-specimen mode, tokens) | B + D (dialogs, keyboard) |
| SAF-SW-005 | §6.5, §15.2 | `core.model.Indicators` (HALT/PAUSED source, DRV_PWR, K1_WELDED, HOME_DRIFT, CLK_FALLBACK, no-specimen banner, UNKNOWN state), `gates.clear_procedure` | B + D (display) |
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
| IF-010 | §2, §4.2, §12.5 | `core.params_gen`, `core.protocol_gen` (generated), vector tests, check-vector replay | B (consumer) |
| IF-011 | §4.5 | `core.link.PrioritySender`, `FrameWriter` | B |
| IF-012 | §4.7, §5.1 | `core.device`, `io.protocol` (25 commands incl. PAUSE, + RESUME 0x3C from ICD v0.4) | B (codes from `protocol_gen`) |
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
| SW-LIM-004 | §6.7, §6.3, §5.6 | `LimitsAPI.set_no_specimen_mode`, `ThresholdManager` (calibrated thresholds if calibration + tare, else defaults), gate `no_specimen`, recorder event rows | B + D (C-10 dialog, banner) |
| SW-META-001 | §11, §13.6 | `core.metadata` | B + D (form) |
| SW-META-002 | §8, §13.6, §13.7 | `core.metadata`, recorder sidecar | B + D |
| SW-RT-001 | — (GUI) | backend: `stop()` API, `DataView` | **D** (`SW_design_GUI.md`, docks/layout) |
| SW-RT-002 | §7.4 | `core.channels.ChannelRegistry` | B + D (toggles, greyed) |
| SW-RT-003 | §7.5 | `DataView.snapshot/xy` | B + D (views) |
| SW-RT-004 | §7.4, §18 | `calc.derived`, pipeline | B |
| SW-RT-005 | §7.5 | `DataView.latest()` states | B + D (readouts) |
| SW-MAN-001 | §5.4 | `MotionController.move_to` | B + D (slider sends on release) |
| SW-MAN-002 | §5.4 | `move_by` on the last commanded target incl. a pending one | B (+ D entry) |
| SW-MAN-003 | §5.4 | commanded-target accumulator, latest-wins pending target, `status().motion.pending_target_mm` | B (+ D buttons, pending display) |
| SW-MAN-004 | §5.4 | jog session + refresh | B + D (press/release/focus) |
| SW-MAN-005 | §5.4 | caps, STOP | B + D |
| SW-MAN-006 | §5.4 | enable/disable/home/x_zero/VALID | B + D |
| SW-STOP-001 | §4.5, §5.5 | `PrioritySender`, `terminate_all` | B + D (button everywhere, no focus) |
| SW-STOP-002 | §4.4, §5.5 | `io.win_hotkey`, `StopConfirmer` | B |
| SW-STOP-003 | §5.5, §10.3 | pipeline event dispatch, `terminate_all`, `clear_stop` | B + D ("Clear stop") |
| SW-STOP-004 | §5.5, §5.5.1, §10.5 | `Backend.pause` (PAUSE 0x3B), `Backend.resume` (RESUME 0x3C + re-issue, D-31), refusal handling (D-33 k), executor pause/resume | B + D |
| SW-ACQ-001 | §8 | `stream_start/stop` | B + D (toolbar) |
| SW-ACQ-002 | §8 | `core.recorder` | B (+ D buttons) |
| SW-ACQ-003 | §8 | `SampleTaker`, `CaptureHub` | B (+ D button) |
| SW-ACQ-004 | §7.3, §8 | gap counters, recorder failure path | B (+ D alarm) |
| SW-CAL-001 | §9.1, §9.3.1 | Engine protocol; travel restore rule + restore-pending record, `travel_cal_differs` indicator (also in recording metadata) | B + D (wizard pages, STOP) |
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
| SW-SEQ-005 | §10.3, §5.6 | `gates.sequence_start_gate` (incl. PAUSED, POS_UNCERTAIN, AFE_RATE_MISMATCH, ALM, DRV_PWR, D-33 b) | B |
| SW-SEQ-006 | §10.4 | `sequencer.loadstep` (MOVE_UNTIL_LOAD with `cmp`), `calc.trim`; trim not converged → sequence STOPPED (option pending PO, OI-14) | B |
| SW-SEQ-007 | §10.5 | guards (TIMEOUT 1.2 × T + 10 s, BREAK_DETECTED, SLIP), NOT_REACHED end (D-32), ALM → controlled STOP (D-33 c), controls | B + D (buttons) |
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

Coverage against SRS v0.3: **SW 60/60, SAF-SW 6/6, IF 12/12, NFR-001…004 4/4** have a design element;
SW-RT-001 is GUI-only (backend support listed). Checked by script: every SW-side ID of SRS v0.3 appears in
this table.

---

## 21. Open items and findings

Status after v0.2. "Closed" names the decision or document that closed the item.

| ID | To | Finding / question | Status / resolution |
|---|---|---|---|
| F-B-01 | Integrator | Per-command retry policy; motion never auto-retried. | **Closed**: ICD §9.3, `protocol_gen.CMD_RETRY` (JOG 0 = RETRY), §4.4. |
| F-B-02 | Integrator | Step count and active target needed (travel cal, VERIFY resolution). | **Closed**: STATUS `pos_steps`/`target_um`, MOVE_DONE `value2`; §4.4.1, §9.3. |
| F-B-03 | Integrator, Orchestrator | GUI Pause on the wire and the PAUSED clear command. | **Closed**: D-29 a + D-30 + D-31 (PAUSE 0x3B, motion-blocking latch; RESUME 0x3C clears only PAUSED, HALT_CLEAR clears HALT and PAUSED); §5.5, §10.5. |
| F-B-04 | Integrator | MOVE_DONE reasons; MOVE_UNTIL_LOAD comparison sense. | **Closed**: ICD §8.3, `cmp` field; §10.4. |
| F-B-05 | Integrator | Fallback marker, status bits, HALT source, EVENT codes, SET_VALID `t_us`, stop confirmation with the stream off. | **Closed**: ICD §5.3, §7.6, §8, §9.4; §5.5.1, §6.5. |
| F-B-06 | Integrator + B | One world-control vocabulary and scenario format for simulator and twin. | **Closed**: vocabulary v2 frozen in ICD v0.4 tools/README; §12.4 implements it. |
| F-B-07 | Orchestrator | Thresholds outside the FW range: refuse vs clamp inward. | **Closed**: D-29 g, SAF-SW-002 v0.3; §6.3. |
| F-B-08 | Orchestrator | First use without calibration/tare. | **Closed**: D-29 h, SW-LIM-004 + Orchestrator decision (calibrated thresholds kept when available); §6.7. |
| F-B-09 | Orchestrator, Validator F | SW-MAN-003 acceptance vs latest-wins. | **Closed**: D-29 i, SW-MAN-002/003 v0.3; §5.4. Validator F: acceptance = "wire 11, 13; final target 13". |
| F-B-10 | Orchestrator | Count-band parameters (`home.max_load_raw`, `release_band_raw`, `load_regrow_raw`) could be rewritten from K. | **Open, deferred** (release 2 option; not designed). |
| F-B-11 | Orchestrator | Thrust_Stand pin vs HEAD. | **Closed**: D-29 m; §17 (hash read from `.git` files, no tool run in the reference). |
| F-B-12 | Orchestrator | Ownership of package root, launch scripts, `tests/conftest.py`. | **Closed**: D-29 n. |
| F-B-13 | Orchestrator | Tare session-only. | **Closed**: D-29 j; KD-13. |
| F-B-14 | PO | Load-step trim not converged: stop vs continue. | **Open (PO Q26 / SRS OI-14)**: default "stop" implemented (SW-SEQ-006 v0.3); `on_trim_fail = "continue"` kept as an option until the PO decides. |
| F-B-15 | Integrator | JOG bound for exact SW travel limits. | **Closed**: ICD §5.4 `bound_um`, `JOG_NO_BOUND`; §5.4. |
| F-B-16 | Implementer D | GUI consumes §15. | **Closed**: §15.5 delta + §15.7 answers. |
| F-B-17 | Validator F | Verification hooks. | **Closed by design (C3)**: all SWD-P1-09 hooks (a)–(h) are specified in §12.4 / §15.5a B3-09 and scheduled in WP-B4/B6/B7/B11 (M1 entry for the API, implementation within M1). |
| F-B-18 | Orchestrator | Minimal dependency set (KD-11). | **Closed**: SRS v0.3 SW-PLT-001 lists exactly PySide6, pyqtgraph, numpy, pyserial. |
| F-B-19 | Integrator | `params_gen` metadata. | **Closed**: the generated module provides all fields (§2). |
| F-B-20 | Orchestrator | Dwell from target reached; targets relative to test zero. | **Closed**: D-29 k; §10.3. |
| F-B-21 | Implementer D | `safety.*` session values read-only in the config form. | **Closed**: done in SW_design_GUI. |
| F-B-22 | Orchestrator | AFE mismatch invalidates the calibration for load limits. | **Closed**: D-29 l, SAF-SW-001 v0.3. |
| F-B-23 | Orchestrator | SRS wording for "active travel calibration" and the restore rule (GF-05). | **Closed**: SRS v0.3 SW-CAL-001; §9.3.1. |
| F-B-24 | Integrator | K1_WELDED delay 100 ms (ICD v0.1) vs 200 ms (D-29 c). | **Closed**: ICD v0.2 `drv.k1_weld_ms` (default 200). |
| F-B-25 | Integrator | D-30 in the vectors; stable `state_defaults` keys + a `state_schema` version field. | **Closed**: ICD v0.4 `state_schema` 2, stable keys, RESUME vectors (509 vectors). |
| F-B-26 | Integrator | Generated protocol name tables. | **Closed**: `core/protocol_gen.py` (ICD v0.2 §0.3). |
| F-B-27 | Integrator | PAUSED clearing by motion commands vs steady-state windows / button behaviour. | **Closed**: superseded by D-30. |
| F-B-28 | Integrator | MOVE_UNTIL_LOAD with `bound_um` equal to the current position. | **Closed**: ICD v0.4 → `E_RANGE` 0; the simulator implements it; the SW still refuses it locally (`BOUND_NOT_AHEAD`). |
| F-B-29 | Orchestrator | OI-ICD-04 jog refresh after PAUSE. | **Closed**: D-30 (FW refuses while PAUSED); the SW also ends the jog session and drops the pending target first on EVENT PAUSED / STOPPED (§5.4, §5.5.1). |
| F-B-30 | Integrator, Orchestrator | Resume via HALT_CLEAR could clear a just-latched STOP-button HALT. | **Closed**: D-31 RESUME 0x3C (refused while HALT/ESTOP/fault latched); §10.5. |
| F-B-31 | Orchestrator | Requirements file for SW-PLT-001. | **Closed**: D-31 ownership; `03_SW/requirements.txt` + `requirements-dev.txt` created (§1). |
| F-B-32 | Integrator | RESUME details (D-31, SWD-P1-02 a/b). | **Closed** (ICD v0.4): VERIFY class, CONTROL lane, not sniffed; PAUSE_CLEARED arg 3; refused only by ESTOP / HALT / latched faults; vectors incl. `paused_after`. |
| F-B-33 | Integrator | D-33 a hard rule for `afe.timeout_ms`. | **Closed** (ICD v0.4): H5 with partner-id detail, vectors and write order; implemented in `calc.paramrules` (§2). |
| F-B-35 | — | D-34: clears never auto-retried. | **Closed** in v0.3.1: clears are VERIFY on the priority path, GET_STATUS resolution (§4.4, §4.4.1, §15.5b). |
| F-B-34 | Orchestrator | SRS v0.4 wording: SW-SEQ-007 TIMEOUT for **all** motion steps uses `1.2 × T + 10 s` (B applies the D-33 d formula also to TRAVEL steps and trim moves instead of v0.3's "3 × planned + 10 s" — one rule, never earlier than D-32 allows); load-step status name `NOT_REACHED`; SW-SEQ-005 list incl. the D-33 b items; SW-STOP-004 AC per SWD-P1-01. | **Open → Orchestrator (SRS v0.4, condition C1)** |

### 21.1 Answers to the SW test plan findings (SW_test_plan §8, conditions §9)

| Finding | Answer in v0.3 | Where |
|---|---|---|
| SWD-P1-01 (SRS vs D-30/D-31) | Orchestrator item; the design already follows D-30/D-31 (proposed AC wording supported) | F-B-34 |
| SWD-P1-02 (D-31 not in design) | **Done**: RESUME 0x3C everywhere (command table, Device, facade, gates, EVENT dispatch, executor); (a) retry class VERIFY on the CONTROL lane (ICD v0.4 draft); (b) refused RESUME = expected outcome, no retry, sequence terminated by its latch / ABORTED; (c) Clear stop while paused = CONFIRM, sequence ends STOPPED first | §4.4, §4.7, §5.5, §5.6, §10.5, F-B-32 |
| SWD-P1-03 (race as SW outcome) | **Done**: BLOCK PAUSED refusal is expected (D-33 k): no retry, no DEGRADED count, `REFUSED_PAUSED`, sequence stays PAUSED | §4.4, §5.4, §10.5 |
| SWD-P1-04 (mask empties windows) | **Done** via D-33 b: `sequence_start` refuses on POS_UNCERTAIN and AFE_RATE_MISMATCH; mask unchanged | §5.6, §10.3 |
| SWD-P1-05 (GUI design on SRS v0.2) | Implementer D; §15.5a lists the v0.3 delta | §15.5a |
| SWD-P1-06 (ambiguous ACs) | Orchestrator; B's design matches F's interpretations (carrier frame incl. GET_STATUS; sent-frame log as ground truth §12.4 e) | — |
| SWD-P1-07 (160 / 1280) | **Done**: candidates 800 / 160; expected value 800 (D-27 closed); simulator default 800 | §9.3, §12.3 |
| SWD-P1-08 (start gate items) | **Done**: one REFUSE item each for DRV_PWR, ALM, PAUSED (+ POS_UNCERTAIN, AFE_RATE_MISMATCH) | §5.6, §10.3 |
| SWD-P1-09 / C3 (hooks) | **Done (design)**: (a) lockstep backend, (b) wire log on all transports + server, (c) `rx_log`, (d) `drop_next`/`duplicate_next`/`delay_next`, (e) sent-frame log, (f) `on_frame`/`on_event`, (g) any `OSError`, (h) `set_free_space` | §4.1, §12.4, §12.6, §15.5a |
| SWD-P1-10 (twin) | Integrator / A (C6); the simulator vocabulary follows the ICD v0.4 README | F-B-06 |
| SWD-P1-11 (jog refresh jitter) | **Done**: 80 ms schedule on the 5 ms tick, max < 100 ms | §4.4, §5.4 |
| SWD-P1-12 (undefined behaviours) | **Done**: (a) force rate NaN across gaps; (b) duplicate / non-advancing `frame_seq`; (c) staircase count and return order | §7.3, §7.4, §10.6 |
| SWD-P1-13 (R4 errata) | Researcher / Orchestrator; B uses ICD/SRS values (26 B, 3285 counts/N) | §12.3, §16 |
| SWD-P1-14 (ALM during a sequence) | **Done** via D-33 c: controlled STOP, sequence STOPPED `DRIVER_ALARM` | §5.5.1, §10.5 |
| SWD-P1-15 (sim offset vs clamp) | **Done**: default offset 50 000 counts; `clamp.simscn.json` for the clamp path | §12.3 |
| SWD-P1-16 (markers, 3.11, requirements) | **Done**: markers `validation`, `winint`, `rt` registered with `--strict-markers`; runtime 3.14 (D-33 i); requirements files created | §1, `pyproject.toml` |
| SWD-P1-17 (reference PC) | Orchestrator / PO (D-33 j) | — |
| SWD-P1-18 (D-32 load step) | **Done** via D-33 d: bound = nearer of soft limit and enabled SW travel limit (no SW-limit trip on reaching it), timeout 1.2 × T + 10 s, status NOT_REACHED, BREAK_DETECTED still aborts | §6.1, §10.4, §10.5 |

---

## 22. M1 work breakdown — SW backend (P2 start after the PO gate)

**M1 scope (SRS v0.3, milestone M1, SW side):** SYS-003, SYS-008 (simulator), SYS-010, IF-001…IF-012,
SW-PLT-001…003, SW-CFG-001…004, SW-ACQ-001 (stream toggle); plus the backend support D needs for the M1 GUI
(connect / config / plot) and the priority STOP/HALT/PAUSE path (IF-011). Motion engines, safety supervisor,
calibration and sequencer are M2–M4 (they build on the M1 modules; only their §15 interfaces are stubbed).

**Entry conditions:** P1 gate passed; ICD v0.4.1 (RESUME, H5, D-34 clear classes, `state_schema` 2, vocabulary v2, `units_vectors.json`)
and `params.yaml` frozen for M1; `gen_params.py --check` / `gen_vectors.py --check` green; `.venv` as listed
in CLAUDE.md.

| WP | Content (modules) | Depends on | Tests (all `@pytest.mark.req`, `# Verifies:`) | Req |
|---|---|---|---|---|
| **WP-B0** scaffolding | `bend_stand/__init__.py` (`__version__`, implemented PROTO/PAYLOAD/ICD versions from `protocol_gen`), `__main__.py` (argparse, `--headless` smoke mode, GUI hand-off `gui.app.run`), `run.bat`, `run_sim.bat`, `tests/conftest.py` (fixtures + **D-06 port guard**), `pyproject.toml` entry point, `requirements*.txt` (F-B-31), `core.api` Protocol stubs so D can start with fakes; **v0.3: `requirements*.txt` already created in P1, markers registered** | — | `test_layering.py` (AST + subprocess: `calc`/`io`/`core` import no PySide6/shiboken6/pyqtgraph; `io` imports from `core` only `params_gen`/`protocol_gen`); `test_packaging.py` (import, version, entry point, `--help`); `test_port_guard.py` (opening a COM port in a test raises) | SW-PLT-001/002, D-06 |
| **WP-B1** calc foundation | `calc.units`, `calc.rounding`, `calc.timebase` (unwrap, plausible_wraps, frame_gaps), `calc.motion.um_to_steps/steps_to_um`, `calc.paramrules` (H1–H4, write order), H5 + ICD §11.4 write order in `calc.paramrules`, `calc.motion.rate_cap_um_s` | WP-B0 | R4 TV-U, TV-TC (`um_to_steps`), TV-L; every `E_CONFIG` check vector reproduced by `check_hard_rules`; property test: `write_order` keeps H1–H4 true after each SET for random valid start/target pairs; **`units_vectors.json`**: every `um_to_steps` / `steps_to_um` / `rate_cap` value exact with binary32 `spm`; H5 `E_CONFIG` vectors | SYS-003, IF-006/007, SW-CFG-003 |
| **WP-B2** CRC + framing | `io.crc`, `io.framing` (copied from TS HEAD + adapted, origin note with hash) | WP-B0 | `protocol_vectors.json`: all `crc16`, all `streams` (frames **and** counters), `framing_only`, `invalid`; 1 MB random-noise fuzz (no exception, resync) | IF-003/004 |
| **WP-B3** codec | `io.protocol`: request builders/decoders, response/DATA/EVENT encoders/decoders, INFO / STATUS (86 B) / PARAM_ENTRY / pages, `DATA_DTYPE`, NACK split + `nack_text` | WP-B2 | every `frames` vector both directions byte-identical (`reencode: false` → canonical; `INVALID_PADDING` rejected); names/codes of decoded vectors = `protocol_gen`; longer OK responses accepted; unknown EVENT tolerated | IF-001/005/006/008/012 |
| **WP-B4** transports + reader | `io.transport` (Serial — never opened in tests, VirtualTransportPair with pacing + `wire_log`, Tcp), `io.ports` (enumeration), `io.reader`; `wire_log` in the Transport base class (all transports), `rx_log` ring (hooks b, c) | WP-B2 | virtual pair pacing 92 160 B/s; reader: arrival order DATA/EVENT, responses routed, inter-byte timeout only after an empty read, queue full = liveness fault; Serial settings applied once (mocked `serial.Serial`) | IF-002, IF-011 |
| **WP-B5** core infrastructure | `core.errors`, `clock` (+ FakeClock), `timing` (Ticker, timeBeginPeriod), `observers` (weak, ReleasingFuture, AsyncCall), `events` (EventBus), `liveness`, `model` (DeviceInfo, BoardStatus, flag wrappers over `protocol_gen`), `linkstats` | WP-B3 | weak-observer lifetime (no leak, TS rules 1/2), Ticker absolute deadlines with FakeClock, EventBus history bound, excepthook → liveness fault | NFR-004 (basis) |
| **WP-B6** link layer | `core.link`: FrameWriter (two-level lock), CommandChannel (SEQ, lanes, `CMD_RETRY` classes, epoch, token bucket), PrioritySender + StopConfirmer (STOP/HALT/PAUSE), LinkSupervisor (heartbeat, link states, STATUS poll); RESUME on the CONTROL lane (VERIFY, ICD v0.4); NACK ≠ timeout for DEGRADED; BLOCK PAUSED refusal = expected outcome (D-33 k); clears (HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR) VERIFY on the priority path, never re-sent (D-34) | WP-B4, WP-B5 | `ScriptedBoard`: RETRY with new SEQ + newest value; VERIFY never retried → GET_STATUS resolution (§4.4.1); late response dropped; epoch drops a queued MOVE after STOP; STOP written first with 4 outstanding + empty token bucket (IF-011, `wire_log` ≤ 3 ms); CONFIRM repeats until ACK / flag, alarm after 1 s with the stream off; max heartbeat gap ≤ 250 ms in a 10 min accelerated run with a busy GENERAL lane; heartbeat stops when the Pipeline beat stops; NACK E_STATE PAUSED on MOVE_ABS / JOG refresh → no retry, no DEGRADED, ticket `REFUSED_PAUSED`; RESUME timeout → GET_STATUS resolution (§4.4.1); a dropped clear response (`drop_next`) → exactly one clear frame on the wire, GET_STATUS resolution, `NOT_CONFIRMED` when the latch is still set | IF-005, IF-011, SAF-SW-003 (link part) |
| **WP-B7** simulator (M1 part) | `io.sim.check` (pure acceptance written from the ICD text), `board` (endpoint, dispatch, NVM JSON with two records + boot rules + CFG_DIRTY), `fw_logic` (states, latches incl. PAUSED, SET_VALID semantics, EVENT queue + SEQ counter, BOOT/REBOOT, GET_STATUS counters, streaming one DATA per conversion + fallback frames + OVERRUN), `models.Hx711Model` (80 SPS × (1+ε), noise, rails, stall), `SimControl.act` (vocabulary frozen with the Integrator, F-B-06), `clock` (Lockstep/RealTime), `server` (TCP); RESUME 0x3C acceptance/execution (ICD v0.4); hooks d (`drop_next` …), e (sent-frame log), f (`on_frame` / `on_event`); default scenario offset 50 000 + `clamp.simscn.json`; `state_schema` 2, RESUME with PAUSE_CLEARED arg 3, H5, MOVE_UNTIL_LOAD bound = position → `E_RANGE` 0 (F-B-28), `act()` = vocabulary v2 (unit test against the README table) | WP-B3, WP-B1 | **check-vector replay** (all vectors: verdict, detail, NACK bytes, `paused_after`, no side effect on NACK; version/hash match); randomised `check` vs `ref_cmdcheck` (≥ 2 000 cases); protocol vectors through the SimBoard encoders; NVM: power cut between records, migration by id, hard-rule image → defaults; DATA cadence 80 SPS ± ε, `frame_seq` continuity across STREAM_STOP/START, fallback at `stream.fallback_hz`; SET_VALID boundary by `t_us` | SYS-008, IF-007, IF-010, FW-CMD-001 (sim) |
| **WP-B8** parameters | `core.params` (ParamStore, `check()`, write plan with multi-pass `E_CONFIG`, read-back statuses, `*.bbboard.json`), `core.schema` (field specs, atomic write) | WP-B1, WP-B5 | board-config round trip (enums by name, f32 exact); unknown / missing / out-of-range keys and hash mismatch reported, edit fields only; `check()` = `write_and_verify` step 1 | SW-CFG-001/002/003 |
| **WP-B9** device | `core.device`: connect sequence §5.2 (compat states, feature mask), `read_all_params`, `get/set_param`, `write_and_verify`, SAVE/LOAD/DEFAULTS (+ CFG_DIRTY / `nvm_record_seq`), `reboot`, stream start/stop, `set_valid`, `get_status`, stop/halt/pause/clears, reconnect | WP-B6, WP-B7, WP-B8 | `SimRig` (Device + SimBoard, lockstep): connect OK / proto major / payload / dict-hash mismatch → read-only states; write_and_verify statuses OK / REJECTED / MISMATCH (`inject_store_mismatch`) / BUSY / TIMEOUT / REBOOT_REQUIRED; SAVE → CFG_DIRTY 0; DEFAULTS → session values flagged for re-send; reconnect after BOOT without any automatic enable/home/move | SW-PLT-003, SW-CFG-003/004, IF-008 |
| **WP-B10** pipeline (M1 subset) | `core.samples`, `core.pipeline` (decode, unwrap, gaps, classify, EVENT dispatch skeleton, sinks), `core.ringbuffer` (+ pyramid, `vstate`), `core.dataview` (`snapshot` → `PlotSnapshot`, `xy`, `latest`), `core.channels` (raw, setpoint, rate, status bits from `protocol_gen`) | WP-B5, WP-B3 | wrap / BOOT epochs, `seq_lost` FW vs link attribution, missed conversions, fallback rows NO_DATA; snapshot returns exactly `px_width` columns for 5–600 s windows; pipeline ≤ 1 ms/frame benchmark (`perf` marker); 1 h accelerated run without queue growth | IF-006/007, NFR-001/004 (backend part), SW-ACQ-001 |
| **WP-B11** backend facade (M1 subset) | `core.backend` (start/shutdown/gui_beat, endpoints, connect/disconnect, stop/halt/pause, `status()` with link/stream/indicators (`UNKNOWN` handling)/cfg flags/gates `stream_*`, `config_write`), full `core.api` Protocols (M2–M4 members raise `NotImplementedError` in M1), `core.testing`; `BackendSettings(clock="lockstep")` with `test_hooks.advance()` (hook a), `fail_recorder` / `set_free_space` stubs for M3 | WP-B9, WP-B10 | headless component tests: connect to `sim`, stream 10 s, status at 10 Hz, STOP via the facade on the wire ≤ 50 ms p95 (100 runs); `core.api` conformance (Backend satisfies every Protocol); `python -m bend_stand --headless --sim --duration 5` exits 0 with link stats; the same component tests run deterministically under the lockstep backend (two runs → identical wire logs) | SW-PLT-003, SW-ACQ-001, NFR-002 (backend part) |
| **WP-B12** (M1 end / M2 start) | simulator motion execution (MOVE_ABS/JOG/HOME/stops/PAUSE latch, switches, driver power, K1), `core.motion` basic (enable/disable/home/move_to/jog with bound), `core.gates` motion subset | WP-B7, WP-B11 | sim motion vs the `calc.motion` planner; jog dead-man; PAUSE blocks motion (D-30, once the vectors carry it) | prepares M2 / SYS-002 |

**Order and parallelism:** WP-B0 → (WP-B1 ∥ WP-B2) → WP-B3 → (WP-B4 ∥ WP-B5) → WP-B6 → (WP-B7 ∥ WP-B8) →
WP-B9 → WP-B10 → WP-B11 → WP-B12. D can start the GUI against `core.api` fakes after WP-B0 and against the
real backend after WP-B11.

**M1 exit criteria (B part):** all tests above green in fixed and random order (`-p randomly`, two seeds);
every M1 SRS ID has a test with a `req` marker (`pytest --collect-only` report); branch coverage ≥ 85 % for
`io` and the M1 `core`/`calc` modules (generated modules excluded); layering test green; no COM port opened by
any test (guard); `gen_params.py --check` / `gen_vectors.py --check` green against the vectors used; headless
smoke run against `--sim` and, when the Integrator's twin exists, against `tcp://127.0.0.1:5760`.

---

## 23. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-03 | Implementer B | Initial P1 design: architecture, threading, link client, Device/Motion API, safety supervisor, pipeline, recorder, calibration/tare engines, sequencer, report, simulator, file formats, GUI API contract, reuse table, traceability (SW 59/59, SAF-SW 6/6, IF 12/12, NFR-001…004), findings F-B-01…22. |
| 0.2 | 2026-10-03 | Implementer B | Aligned to ICD v0.2 (frame/CRC/parser constants, 25 commands incl. PAUSE 0x3B, retry classes from `protocol_gen.CMD_RETRY`, VERIFY resolution table, NACK decoding, DATA 18 B, EVENT 16 B, STATUS 86 B with `pause_src`, JOG `bound_um` / `JOG_NO_BOUND`, MOVE_UNTIL_LOAD `cmp`, MOVE_DONE reasons with step counts, FW EVENT dispatch table, feature mask, dict_version 2 names), SRS v0.3 (SW-LIM-004, SW-MAN-002/003, SW-CAL-001, SW-SEQ-006, SAF-SW-002) and D-29 a/c/e/g/h/i/j/k/l/m/n + D-30 (PAUSED blocks motion, Resume = HALT_CLEAR + re-issue). New: no-specimen mode §6.7, clamp-inward thresholds, active-travel-calibration restore rule §9.3.1, load-cal finish/retake, tare undo, all GUI gates, indicator freshness, check-vector replay and randomised differential check §12.5, SimControl test coverage, out-of-process simulator, `calc.paramrules`, conftest D-06 guard. §15.5 API delta (A-01…A-25) and §15.7 answers to GRQ-B-01…18 (all accepted). Findings closed: F-B-01…05, 07…09, 11…13, 15, 16, 18…24, 26, 27, 29; open: F-B-06, 10, 14, 17, 25, 28, 30, 31. M1 work breakdown §22. Traceability against SRS v0.3: SW 60/60, SAF-SW 6/6, IF 12/12, NFR-001…004. |
| 0.3 | 2026-10-03 | Implementer B | Final P1 round: D-31 RESUME 0x3C (Resume = RESUME + re-issue; refused RESUME as an expected outcome; Clear stop while a sequence is paused = CONFIRM, sequence STOPPED first), D-33 k BLOCK PAUSED refusal as an expected outcome (no retry, no DEGRADED), D-33 b sequence-start items (PAUSED, POS_UNCERTAIN, AFE_RATE_MISMATCH, ALM, DRV_PWR), D-33 c ALM during a sequence → controlled STOP, D-32/D-33 d load-step bound / NOT_REACHED / timeout 1.2 × T + 10 s (and no SW-limit trip on a planned bound), steps/mm candidates 160/800, validation hooks (lockstep backend, wire log on all transports, rx log, per-command drop/duplicate/delay, sent-frame log, wire-event-timed world actions, ENOSPC, free-space override), jog refresh 80 ms, undefined behaviours defined (force rate across gaps, duplicate/backward frame_seq, staircase semantics), simulator default offset 50 000 counts, runtime Python 3.14, requirements files, markers `validation`/`winint`/`rt`; D-27 closed → expected steps/mm and simulator default 800 (160 kept as the "DIP not changed" candidate). §15.5a API delta B3-01…B3-21 (incl. GF-11/12/13/14/17 from SW_design_GUI v0.2); §21.1 answers to SWD-P1-01…18. Findings closed: F-B-17, F-B-30, F-B-31; new F-B-32…34. |
| 0.3.1 | 2026-10-03 | Implementer B | D-34 / ICD v0.4.1: HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR are VERIFY class on the priority path (never re-sent, GET_STATUS resolution, `ClearResult.outcome NOT_CONFIRMED`); ICD v0.4 alignment: RESUME resolution (PAUSED = 0 and no new EVENT PAUSED since), H1–H5 with the §11.4 write order in `calc.paramrules`, `calc.motion` checked against `units_vectors.json`, simulator RESUME / H5 / F-B-28 / `state_schema` 2 / PAUSE_CLEARED reason 3, `SimControl.act()` = frozen vocabulary v2, dict_version 3 (hash 0xF0376293). §15.5b delta B31-01…04. Findings closed: F-B-06, 25, 28, 32, 33, 35. |
