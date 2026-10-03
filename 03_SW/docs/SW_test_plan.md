# Bird Bend Stand — PC Software Test Plan (SW_test_plan)

| Doc | SW_test_plan |
|---|---|
| Version | **0.3 — M2 plan: CR-01 / D-36, D-37, SW-RT-006 (D-38), M2 SW early acceptance** (0.2 = M1 execution corrections, 0.1 = P1 gate draft) |
| Date | 2026-10-04 |
| Owner | Validator F — SW (`03_SW/docs/SW_test_plan.md`, `03_SW/docs/SW_test_report*.md`, `03_SW/tests/validation/**`) |
| Verifies | SRS **v0.5.1**: SW-* (61, incl. new SW-RT-006), SAF-SW-* (6), NFR-001…004, the SW side of IF-001…012, SYS-003, SYS-008, SYS-010 (**86 requirements**; v0.2: 85 of SRS v0.3) |
| Binding inputs | `DECISIONS.md` D-01…**D-39** (v0.3: D-36 one red button = E-stop, CR-01; D-37 M1-gate interface decisions a–d; D-38 plot panes; D-39 M2 start). Earlier: D-30: PAUSED blocks motion. **D-31: dedicated RESUME command 0x3C**, clears only PAUSED, refused while HALT/ESTOP/fault latched; HALT_CLEAR clears HALT and PAUSED. **D-32 (PO Q26)**: a load step that does not reach its target travels to the soft limit in the step direction and stops there, the step is NOT_REACHED and the sequence stops; the approach bound is that soft limit; the timeout must not abort earlier; BREAK_DETECTED still aborts. PO accepted the GUI defaults GQ-01…20 and KL-01. |
| M2 baseline (v0.3) | SRS **v0.5.1** (CR-01: SAF-FW-022 withdrawn, KL-07; D-37 in SW-CFG-004, SAF-SW-005, IF-011, SW-STOP-001/002; new SW-RT-006), ICD **v0.5** (PROTO 1.0, PAYLOAD 1; `params.yaml` dict_version **4**, **47** parameters, PARAM_DICT_HASH **0xFCC54C90**; STOP_BTN / STOP_BUTTON retired; `*_FEATURE` validity; units vectors with int32 saturation; new `motion_vectors.json` + `ref_motion.py`), DECISIONS D-36…**D-40** (D-40 a: LIMIT_WIRING FAULT_CLEAR once the inputs are no longer both active; b: ramp sum tolerance ±ceil(N/1000) ticks — FW only, the SW simulator reproduces `motion_vectors.json` exactly; d: regrow reference until back inside the thresholds or the next FAULT_CLEAR), SW_design v0.3.3 + B's M2 work in progress (WP-B12 simulator rewritten: exact ramps, homing back-off, K1 timer, idle disable, regrow), SW_design_GUI **v0.4** §4.7 (plot panes, G-45…G-52) |
| M1 baseline (v0.2) | SRS **v0.4.1**, ICD **v0.4.1** (164 frame vectors, 11 streams, 509 check vectors, `units_vectors.json`), `params.yaml` dict_version 3 (PARAM_DICT_HASH **0xF0376293**), SW_design **v0.3.2** (as-built), SW_design_GUI **v0.3.1** (§15.1 as-built), FW host twin (A's firmware) — the M1 corrections of §10a apply |
| Specs and tools (P1) | `ICD_protocol.md` **v0.3** (RESUME not yet in it, see SWD-P1-02), `protocol.yaml`, `params.yaml` dict_version 2 (PARAM_DICT_HASH 0xB046DD01), `00_System/tools/{ref_codec.py, ref_cmdcheck.py, vectors/}` (check vectors: 486 at ICD 0.3), R4 §12, `SW_design.md` **v0.2** (§15 API, §19 hooks, §22 M1 breakdown), `SW_design_GUI.md` v0.1 (G-01…G-36, P-01…P-05, §10.4 demonstrations, GF-09) |
| Reference | Process pattern: Thrust_Stand_HAW `03_SW/docs/SW_test_plan.md` (read-only, D-02). Only the structure is reused. |

**Where the SRS v0.3 and D-30/D-31/D-32 disagree, this plan follows the decisions.** These are:
- the Pause/Resume semantics in SAF-FW-023, SW-STOP-004, §2 and §3.2 (SWD-P1-01);
- the load-step bound and timeout in SW-SEQ-006/007 (SWD-P1-18).

Each affected test case is marked **[D-31]** or **[D-32]**. GUI tests use the PO-accepted defaults GQ-01…20.

---

## 0. Scope and reading guide

The plan covers how every PC-software requirement is verified:
- the test levels, environments, oracles and independence rules (§1, §2);
- one or more test cases per requirement (§3);
- calculation vectors (§4);
- simulator fault-injection scenarios (§5);
- performance runs and demonstrations (§6);
- milestone entry/exit criteria and the verdict format (§7);
- the P1 design review of SW_design v0.2, SW_design_GUI v0.1 and ICD v0.3 against SRS v0.3 (§8), ending with the **P1 testability verdict** (§9).

Out of scope:
- FW tests (Validator E).
- Implementation of the SW⇄twin integration tests. The Integrator owns `03_SW/tests/integration/**`. This plan defines their SW acceptance list (§2.1, level X) and F reviews them.
- Implementer unit tests (`tests/unit/**`, B) and GUI tests (`tests/gui/**`, D). F re-runs them at gates but never counts them as validation evidence.

---

## 1. Oracles and independence rules

| Oracle | Source | Used for | Status (checked by F, 2026-10-03) |
|---|---|---|---|
| R4 §12 vectors (22 test functions) | Researcher | all `calc` acceptance tests | **Self-consistent.** All 22 functions pass against F's independent implementation written from the R4 text, 3 runs (2 × fixed order + 1 × `-p randomly`): 22/22 each. The implementation is in the scratchpad `validator-f-sw/f_ref.py`; at M1 it moves to `tests/validation/oracle/f_ref.py`. TV-L `test_link_budget` uses a 22-byte frame. That vector is obsolete against the ICD 26-byte DATA frame (SWD-P1-13); IF-011 uses ICD §10 numbers instead. |
| VV-* vectors (§4.2) | F, computed with `f_ref.py` (never with production code) | derived channels, linearity boundaries, thresholds/clamping, trim/approach, plausibility, steady-state, generators, path | computed 2026-10-03 (values in §4.2) |
| `protocol_vectors.json` + `ref_codec.py` | Integrator | codec encode/decode, parser `streams`, CRC, and **decoding of the `wire_log`** in every validation test (independent of production `io.protocol`) | `ref_codec.py verify` exit 0; 4 crc, 158 frames, 11 streams; tools pytest 758/758 pass |
| `check_vectors.json` + `ref_cmdcheck.py` | Integrator | expected FW verdicts. The simulator is B's test environment, not an oracle (rule 3). | 486 vectors (ICD 0.3, D-30 BLOCK PAUSED); `gen_params.py --check` and `gen_vectors.py --check` both exit 0 |
| FW host twin (`tcp://127.0.0.1:5760`) | Implementer A + Integrator | level X: SW against the real FW logic | **not yet built** (condition C6) |
| SRS acceptance criteria | Orchestrator | expected results of every TC | v0.3, with the D-30/D-31 deltas of SWD-P1-01 |

Independence rules (binding for `tests/validation/**`):
1. Expected values come from an oracle above, or from first principles inside the test. They are never computed by calling the production function under test or a helper it uses.
2. Tests use only the public backend API (SW_design §15, through `harness.py`, §2.4), `SimControl`, `Backend.test_hooks` and the `wire_log`. They never use private modules. `ref_codec`/`ref_cmdcheck` are imported only by tests.
3. The SW simulator is a test environment written by the same implementer as the backend. A C-level scenario therefore proves *SW behaviour given correct FW behaviour*. FW behaviour is proven only by the check-vector replay (P), by the twin (X) and on target (H). Every C case that depends on a FW reaction (BLOCK PAUSED, RESUME refusal, latches) is also listed for X.
4. Every test carries `@pytest.mark.req("<ID>", …)` and `# Verifies: <ID>`. A validation test without `req` fails collection (conftest hook).
5. No test opens a COM port (D-06). The validation conftest adds its own guard on top of B's: `serial.Serial.__init__`/`open` raise `HardwareAccessForbidden` unless `--run-hil` and `BEND_HIL_PORT` are given at a PO-approved hardware gate.

---

## 2. Strategy

### 2.1 Test levels

| Lvl | What | Location (F unless stated) | Clock | Runs |
|---|---|---|---|---|
| **U** | Pure functions (`calc.*`, generators, plan expansion, file schemas) checked against the R4 and VV vectors | `validation/vectors/`, `validation/unit/` | none | every gate run |
| **P** | Protocol conformance with the shared vectors: production codec in both directions; parser `streams`; **check-vector replay through the public path** (Backend + simulator: each vector state → request → wire response equals `expect`, decoded with `ref_codec`) | `validation/protocol/` | lockstep | every gate run |
| **C** | Simulator scenarios: `Backend` + in-process `SimBoard`, deterministic `LockstepSimClock` (functional cases); `RealTimeSimClock` for latency cases (marked `rt`); fault injection §5 | `validation/scenarios/` | lockstep / rt | every gate run (rt cases ≤ 5 min total) |
| **X** | SW⇄FW-twin integration: the same scripted scenarios over `tcp://127.0.0.1:5760`; responses, NACK codes, EVENT order, DATA flags, positions compared with the sim run | `tests/integration/**` (**Integrator**), acceptance list in §3 (Lvl column shows "X") | twin lockstep | per milestone, once the twin exists |
| **G** | GUI offscreen (pytest-qt, `QT_QPA_PLATFORM=offscreen` set before the QApplication import) against the real Backend + simulator. F's GUI cases complement D's G-01…G-36 and use only widgets' public object names. | `validation/gui/` | rt | every gate run |
| **F** | Performance / endurance on the **reference PC** (`perf`, `soak` markers; real display); DEV-PC smoke runs (60 s) are informative only | `validation/perf/` | rt | M3 / M4 gates; smoke every milestone |
| **W** | Windows interactive (`winint`): real `RegisterHotKey`/LL hook, `SendInput`, foreground window switching | `validation/winint/` | rt | M3 gate on REF-PC (interactive session) |
| **D** | Demonstration with the PO (procedures §6.2, results signed in the test report) | report | — | M3 / M4 gates, P3 |
| **I** | Inspection / analysis (code, documents, traceability) | report | — | per gate |
| **H** | Target hardware (`hil`), only at a PO-approved hardware gate (D-06, D-07), jointly with Validator E / Integrator | `tests/integration/hil/**` | real | hardware gate |

### 2.2 Environments

| Env | Definition |
|---|---|
| **DEV** | This PC: Windows 10 Pro 19045, Python 3.14, `.venv`. All U/P/C/G cases run here. |
| **PY311** | A Python 3.11 venv for the SW-PLT-001 lower bound (full U+P+C). Not installed today (SWD-P1-16). |
| **REF** | The PO's lab PC (SRS A-10, still undefined; condition C5). Every F/W/D result is quoted for REF. The report records CPU, cores, RAM, disk, Windows build, power plan, display/DPI and monitor count. |
| **SIM** | In-process `"sim"`; out-of-process `python -m bend_stand.io.sim.server --port 5770` (`tcp://127.0.0.1:5770`) for F-level runs, so the simulator does not share the GIL with the GUI. |
| **TWIN** | `fw_twin.exe --port 5760 --ctl 5761 --clock lockstep` (Integrator, `00_System/tools/fw_twin/`). |
| **TGT** | NUCLEO-F446RE + stand, hardware gate only. |

### 2.3 Markers, repeat runs, reporting
- Markers: `req(*ids)`, `validation`, plus the level markers `unit`/`component`/`gui`/`perf`/`soak`/`hil`, `winint` and `rt`. `validation`, `winint` and `rt` are not registered in `pyproject.toml` today (SWD-P1-16; owner B).
- **Gate run protocol** (each TC set, counts recorded per run in the test report):
  1. `.venv\Scripts\python -m pytest 03_SW\tests\validation -m "not perf and not soak and not winint" -p no:randomly`
  2. the same with `-p randomly -p "randomly_seed=12345"`
  3. the same with `-p randomly` (random seed, printed and recorded)
  4. the implementer suites `tests/unit`, `tests/gui` (and `tests/integration` when the twin exists) once, fixed order, for regression

  All runs must show **identical pass counts**. Any order-dependent result is an S2 defect, or S1 if it is in a safety test.
- A conftest plugin writes `03_SW/tests/validation/_reports/trace.json` (requirement → node ids → outcome) for `TRACEABILITY.md` and the SYS-010 check.
- Coverage floor (`pytest-cov`, branch on, validation and unit suites combined):
  - `calc` ≥ 95 % line / 90 % branch;
  - `io` ≥ 90 / 85 %;
  - `core` ≥ 90 / 80 %, with `core.safety`, `core.link`, `core.sequencer.executor` and `core.gates` ≥ 95 % line;
  - `gui` ≥ 60 % (measured by D);
  - generated modules are excluded.

  A safety branch without an asserting test is a defect even when it is covered.

### 2.4 Harness and fixtures (`tests/validation/conftest.py`, `harness.py`)
- `harness.py` is the **only** file that names SW_design §15 members. If B changes the API in v0.3, only the harness follows. The TCs are written against harness verbs: `connect`, `enable`, `home`, `move_to`, `move_by`, `jog`, `stop`, `halt`, `pause`, `resume`, `clear_stop`, `tare`, `cal_travel`, `cal_load`, `seq_start`, `record`, `status`, `wire()`, `world()`.
- Fixtures:
  - `sim_backend(scenario, clock="lockstep"|"rt")`;
  - `wire` — the `wire_log` decoded with `ref_codec`, giving `(t_ns, dir, type, seq, fields)`;
  - `world` — `SimControl.query("world"/"pulses")`, the true position and the PUL count;
  - `rx_stamp(frame)` — the Reader receive stamp;
  - `vectors`, `check_vectors` — loaded in place from `00_System/tools/vectors/`;
  - `f_ref` — the validator oracle;
  - `tmp_appdata` — `%APPDATA%` and the recordings root redirected to `tmp_path`, so no test touches the real profile;
  - `qtbot` and `main_window(sim)`.
- The hooks F needs are listed in SWD-P1-09 (condition C3).

### 2.4a Pre-written tests and arming (v0.3)
- Tests written ahead of the implementation carry `@pytest.mark.pending("<group>", needs="…")`. They are **skipped** (reason "pending <group> …") unless the group is armed: `pytest … --arm M2` or `BEND_VALIDATION_ARM=M2` (`all` arms every group). Implementers may arm them for early feedback; Validator F arms them at the milestone verification and turns every failure into a defect or a harness correction (only `harness.py` names the API, §2.4; the M2 verbs are one block there).
- `_reports/trace.json` records them with outcome `pending:<group>`; every entry carries the plan TC id derived from the test name (`test_tc_sw_cfg_004_02_…` → `TC-SW-CFG-004-02`).
- Tests of a decided-but-not-yet-implemented rule that run against the current code (e.g. D-37) are written armed; while they fail they are open defects as `xfail(strict=True)` + `defect("SWD-Mx-nn")` (M1-C7 rule), so a fix flips them (XPASS = failure → marker removed at the re-test).
- Wire-decoding predicates (`ref_codec` over the whole log) are evaluated with `harness.until()` (lock-step advance in 25 ms chunks), never on every 1 ms tick (O(n²) decode).

### 2.5 Test-case conventions
- ID `TC-<REQ>-nn`. A parametrised case counts once. A TC named after its primary requirement may list secondary ones in brackets.
- Columns: **Lvl** (§2.1) · **Env** (DEV unless stated; **REF** = needs the reference PC, **TGT** = target hardware, **TWIN**) · **MS** (milestone of the first acceptance) · **Pre** (preconditions) · **Stimulus** · **Expected** (traced to the SRS acceptance criterion; "AC" = the SRS text).
- Default preconditions "**std**":
  - simulator scenario `std.simscn.json`: spring specimen k = 50 N/mm at x_c = 120 mm, S = 3285 counts/N, offset 125 000 counts, noise σ = 45 counts, 80 SPS × (1 + 0.5 %);
  - connected, streaming, ENABLED, HOMED;
  - an active load calibration (K = 1/3285 N/count) and a session tare;
  - thresholds VERIFIED;
  - SW limits at their defaults;
  - recording off.
- Timing tolerances:
  - "±1 frame" = ±12.5 ms of device time;
  - latency percentiles over ≥ 100 trials (SRS p95);
  - floats: `rel = 1e-9` for closed formulas and `rel = 1e-6` for fits (R4).

---

## 3. Test case catalogue

### 3.1 System (SW side)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SYS-003-01 | U | DEV | M1 | – | R4 TV-U and TV-TC `um_to_steps`/`steps_to_um`; VV-U rounding (0.0005 mm → 1 µm, −0.0005 → −1, 12.3454 mm → 12 345 µm, ±2.5 → ±3); **v0.3:** `units_vectors.json` of ICD v0.5 incl. the `saturated: true` cases (int32 saturation, OBS-M1-05; oracle `f_ref.sat_i32`) | all equal (AC: TV-U and µm/steps vectors pass); F > 0 for tension with K > 0, sign carried by K; saturated results = 2³¹ − 1 / −2³¹ |
| TC-SYS-003-02 | G | DEV | M3 | std | View ▸ Units N ↔ kgf | axes and readouts relabel; 98.0665 N shown as 10.000 kgf (AC: N/kgf selector) |
| TC-SYS-008-01 | C | DEV | M1→M4 | fresh sim | Headless workflow through the harness: connect → enable → home → no-specimen mode → travel cal (D entries = sim true distance) → load cal (zero, 1 kg, 10 kg via `load_offset`) → leave no-specimen mode → tare → sequence (travel + load + hold steps) → report | every step succeeds; three report files exist (AC: full workflow against the simulator). M1 runs the connect/config/stream subset only. |
| TC-SYS-008-02 | X | TWIN | M1/M2 | twin running | TC-SYS-008-01 subset (M1: connect, config write/verify, stream, STOP/HALT/PAUSE/RESUME confirmation; M2+: enable/home/move/jog) against the twin and against the sim | same responses, NACK codes, EVENT sequences, DATA flags; positions within ±1 step (AC: SW⇄twin integration tests pass) |
| TC-SYS-008-03 | D | REF | M4 | GUI + sim | PO-witnessed full GUI workflow (§6.2 DM-01) | completed without workaround |
| TC-SYS-010-01 | I | DEV | M1 | code | Scan `03_SW/src/**` for origin notes on copied files (path @ commit hash) and `Implements:` tags; generate `trace.json` | 100 % of the 85 requirements in scope have ≥ 1 passing validation TC; every reused file has an origin note with a hash (AC) |

### 3.2 Safety — PC software (SAF-SW)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SAF-SW-001-01 | C rt | DEV + REF | M3 | std, pull/push trips set to ±150 N | 100 injected violations: MOVE_ABS into the spring until F > trip (50 pull, 30 push via `pull_dir` reversal, 20 travel-limit predictive) | for each: STOP mode 0 on the wire; latency from the Reader stamp of the first violating DATA frame to the STOP write: **p95 ≤ 50 ms** (max reported); `SAFETY_TRIP` event with value; running sequence/wizard terminated (AC) |
| TC-SAF-SW-001-02 | C | DEV | M3 | load limits enabled | One case per invalid input: no calibration; no tare this session; AFE-config mismatch (`afe.gain_channel` changed on the board); AFE stale (`afe stall`); NO_AFE_DATA fallback frames; feature AFE_SYNTHETIC | `move`/`jog`/`sequence_start` gates REFUSE with the reason; a forced API call raises `PreconditionError` and **no motion frame** appears on the wire; if the condition appears while moving → STOP (`LOAD_INPUT_INVALID`) (AC: motion disabled) |
| TC-SAF-SW-001-03 | U | DEV | M3 | – | VV-LIM: F = trip (no trip, strict >), trip + 1 mN (trip); rail sample → ±∞ with sign(K)·sign(rail); warning on at 1765.197 N, off below 1725.970 N | as listed |
| TC-SAF-SW-001-04 | C | DEV | M3 | after a pull trip | move that increases tension; move that reduces it; travel: MOVE_UNTIL_LOAD with a bound beyond the SW travel limit | increasing: refused; reducing: allowed; latch clears inside the hysteresis band; predictive travel trip before the limit (≤ v·75 ms overshoot) |
| TC-SAF-SW-002-01 | U | DEV | M3 | – | R4 TV-T `fw_raw_limits` (3 vectors); VV-THR-01…03 (§4.2) incl. clamping | equal; clamped values with `clamped = True` and the effective level (AC: TV-T incl. K below nominal → clamp + warning) |
| TC-SAF-SW-002-02 | C | DEV | M3 | std | Triggers: connect; reconnect; tare; calibration accept; FW-level edit; LOAD_PARAMS; DEFAULT_PARAMS; sim `reset` (EVENT BOOT); no-specimen mode on/off | each trigger → SET_PARAM `safety.load_raw_min/max` (H2-safe order) + `safety.zero_raw`, then GET_PARAM read-back on the wire; state VERIFIED with matching cal/tare ids; motion gate opens only after the read-back (AC) |
| TC-SAF-SW-002-03 | C | DEV | M3 | std | `inject_store_mismatch(safety.load_raw_max)`; `inject_nack(SET_PARAM, E_RANGE)`; dropped response ×3 | FAILED, motion disabled, reason shown; `recheck` after the injection is removed → VERIFIED (AC: injected mismatch → motion disabled) |
| TC-SAF-SW-002-04 | C | DEV | M3 | default scenario (offset 125 000 counts) | connect + tare with the FW level at 110 % FS | raw_max computed 7 212 265 → **clamped to 7 151 121**, warning "effective +2138.85 N"; raw_min −6 962 265; motion allowed (D-29 g). Note: the default sim offset (1.94 % FS) exceeds the 1 % FS allowance, so every default session clamps (SWD-P1-15). |
| TC-SAF-SW-003-01 | C rt | DEV | M3 | std | 10 min: idle, streaming, a jog phase and a busy GENERAL lane (GET_ALL_PARAMS loop) | max gap between consecutive PC→FW frames on the wire **≤ 250 ms**; PING sent only after ≥ 150 ms of TX idle (AC: heartbeat 10 min) |
| TC-SAF-SW-003-02 | C | DEV | M3 | sequence running a travel step | FI-06: FW→PC silence 600 ms | STOP within 500 ms + 1 tick of the last DATA; sequence ABORTED; LINK LOST indicator; after recovery no motion command for 5 s (AC) |
| TC-SAF-SW-003-03 | C | DEV | M3 | jog moving | `stall_thread("pipeline", 1500)`, then `("reader", 1500)` | heartbeat stops (wire gap > 1 s) → simulator LINK_WDG controlled stop; supervisor STOP while moving; liveness event (AC: heartbeat only while the pipeline is alive) |
| TC-SAF-SW-003-04 | C | DEV | M3 | moving / idle | 400 ms DATA gap while moving; 600 ms gap while idle | no STOP in either case; DEGRADED only (negative test) |
| TC-SAF-SW-004-01 | G | DEV | M3 | sim: load 6 % FS; then load unknown | HOME (C-01), DISABLE (C-02), E-stop clear (C-03), enter no-specimen mode (C-10) | each dialog appears under its condition; Return/Enter/Space with focus on every focusable widget never confirm; Esc cancels; a mouse click confirms; the wire shows HOME flags bit0 = 1 only after confirmation; STOP button present and functional in each dialog (AC) |
| TC-SAF-SW-004-02 | C | DEV | M3 | same conditions | call the actions through the API without the confirmation token | `ConfirmationRequired`; nothing on the wire |
| TC-SAF-SW-005-01 | C rt | DEV | M3 | std | Real cause per indicator via `SimControl` (FI table §5): ESTOP; HALT (v0.3, CR-01: source PC only — Pause/Break key or PC HALT; source BUTTON retired); PAUSED with source PC / BUTTON; LIMIT_START/END; LOAD_LIMIT; AFE stale / saturated / rate mismatch; LINK_WDG; link state; HOMED; POS_UNCERTAIN; ENABLED; DRV_PWR; ALM (+ gate text "new motion blocked"); PEND; K1_WELDED; HOME_DRIFT; CLK_FALLBACK (`emit_event` + STATUS); no-specimen banner | `status().indicators.<item>` changes **≤ 200 ms** after the carrier frame (DATA, EVENT or STATUS response, Reader stamp); the source is correct; each latched item has a non-empty `clear_hint` that matches its clear procedure (AC) |
| TC-SAF-SW-005-03 [D-37 b] | C | DEV | **M2 entry** | F-board with a selectable GET_INFO feature mask | FEAT_DRV_SIGNALS = 0 and FEAT_BUTTONS = 0 while DATA / STATUS carry DRV_PWR, ALM, PAUSE_BTN = 1 (non-conforming FW); contrast case FEAT_DRV_SIGNALS = 1 | indicators `alm`, `pend`, `drv_pwr`, `pause_btn` = UNKNOWN; the `enable` / motion gates carry no DRV_UNPOWERED / DRIVER_ALARM item from invalid bits; contrast: DRV_PWR 1 → ON, ALM 1 → ON, PEND 0 → OFF (AC SAF-SW-005 v0.5). Also X: the M1-mask twin (FEAT_DRV_SIGNALS = 0) shows UNKNOWN, not 'driver power lost' (IF-C-M1-02) |
| TC-SAF-SW-005-04 [CR-01] | I | DEV | M2 entry | code | scan of the operator texts (string literals except docstrings) in `core`, `gui` | no text points to a physical STOP/BREAK button or a 'STOP input' (the only physical stop is the red E-stop; KL-07) |
| TC-SAF-SW-005-02 | G | DEV | M3 | as -01 | same injections through the GUI | a chip/banner for **every** item renders within one tick of the status change; grey = UNKNOWN when stale or disconnected; the help dialog shows the clear procedure. Needs GUI v0.2 (SWD-P1-05). |
| TC-SAF-SW-006-01 | U | DEV | M4 | – | VV-M: (k 50 N/mm, v 10 mm/s) and (500, 10) at default levels | no warning / warning; boundary k·v = 3017.43 N/s (AC: pytest of the rule) |
| TC-SAF-SW-006-02 | C+G | DEV | M4 | std, k_est 500 | manual speed field 10 mm/s; a sequence step at 10 mm/s | WARN item in `motion.check`, the editor and the start list; the GUI warning line is shown (AC) |

### 3.3 Interface (SW side)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-IF-001-01 | I | DEV | M1 | code | AST scan of production code: hand-written numeric codes/bit tables (outside `io.protocol` struct layouts); imports of `ref_codec`/`ref_cmdcheck`; commands and fields used vs ICD §3.2/§7 | none / none / all present (AC: every command/field used appears in the ICD); v0.3: names marked `retired` in `protocol.yaml` are judged by TC-IF-001-02 |
| TC-IF-001-02 [CR-01] | I | DEV | M2 entry | code | AST scan of `core`, `calc`, `io` (simulator excluded), `gui` for uses of ICD names marked `retired:` in `protocol.yaml` (oracle read directly) | none — STOP_BTN / STOP_BUTTON never drive a gate, indicator or text (ICD v0.5 §7.6) |
| TC-IF-002-01 | U | DEV | M1 | `serial.Serial` monkeypatched (no port) | `transport_factory("COM7")` then read/write; `endpoints()` | constructor args 921600, 8, N, 1, no xonxoff/rtscts/dsrdtr; timeout configured once (no per-read SetCommState); `endpoints()` opens nothing (AC: SW side; D-06) |
| TC-IF-002-02 | H | TGT | HW gate | PO approval | VCP soak (with NFR-004 H part) | 0 sequence gaps, 0 CRC errors |
| TC-IF-003-01 | P | DEV | M1 | – | production `FrameDecoder` on all 11 `streams` vectors (chunked as given, idle timeout hook); seeded random fuzz (1 MB noise with 1 000 valid frames inserted at random split points) | frames **and** counters equal the vectors; every inserted frame recovered, no exception (AC) |
| TC-IF-003-02 | P | DEV | M1 | – | all 158 `frames` vectors: PC→FW requests encoded from `decoded`; FW→PC frames decoded | requests byte-identical; decoded fields equal (`reencode: false` → `canonical_payload_hex`; `INVALID_PADDING` rejected; longer OK response accepted) |
| TC-IF-004-01 | P | DEV | M1 | – | `crc16` vectors; bad-CRC frame vectors | 0x29B1 for "123456789", 0xFFFF for empty; bad frames dropped and counted |
| TC-IF-004-02 | C | DEV | M1 | std | FI-04: corrupt a response (`corrupt_next`); a corrupted PC→FW frame injected at the FW (`rx_bytes`); DATA/over-long/truncated frames on the F-board (v0.2, §10a) | dropped and counted in the link stats; no state change from the corrupt frame; the lost SET_PARAM is retried per class; the FW `rx_crc_errors` counter increments (AC: corrupted frames → no action + counter) |
| TC-IF-005-01 | C | DEV | M1 | std | FI-02: drop the response to PING, GET_STATUS, SET_PARAM ×1 and ×3 | retry with a **new SEQ** and the newest value, ≤ 2 retries, then TIMEOUT; a late response to an abandoned SEQ is ignored and counted |
| TC-IF-005-02 | C | DEV | M1/M3 | std | FI-02: drop the response to MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0, ENABLE, DISABLE, SAVE_PARAMS, REBOOT, RESUME [D-31]; D-34 races (v0.3, CR-01: the new latch between a clear and its lost response comes from the **Pause/Break key (PC HALT)**, the physical PAUSE or the E-stop — no STOP button) | each command appears **once** on the wire; then GET_STATUS; outcome resolved per SW_design §4.4.1; sim PUL count shows exactly one motion (AC: no duplicated motion, no motion command sent twice); race: the HALT_CLEAR precedes the new HALT on the wire and the HALT stays latched |
| TC-IF-005-03 | C | DEV | M1 | std | FI-03: duplicate a response frame; deliver a response after the next command's | the second copy is ignored; exactly one resolution per SEQ |
| TC-IF-005-04 | P | DEV | M1 | – | retry class used by the channel for each of the commands vs `protocol_gen.CMD_RETRY` | equal for all commands (JOG decided by v) |
| TC-IF-006-01 | P | DEV | M1 | – | every DATA vector (typical, idle, fallback, rails, sync in payload, all bits, wrap, E-stop); numpy batch decode vs per-frame decode | all fields equal (AC) |
| TC-IF-007-01 | C | DEV | M1 | std | FI-01: `afe drop_every 50`; `inject tx_congestion` (FW drops); link loss, duplicates and the seq wrap on the F-board (v0.2, §10a) | FW-attributed losses (OVERRUN) and link losses counted separately; missed conversions (Δt > 1.5 × median) counted; the u16 wrap counts 1 lost; fallback frames excluded from the rate (SW side of IF-007) |
| TC-IF-008-01 | C | DEV | M1 | F-board (`oracle/fboard.py`, ref_codec) with proto_major 2 / payload_version 2 / hash ≠ / proto_minor higher (v0.2) | connect | MAJOR/PAYLOAD mismatch → read-only: motion, clears-and-enable and config writes refused locally (nothing on the wire), DATA of the other payload not decoded; hash mismatch → config read-only + warning (session values still written when their id/type match); minor higher → compatible (AC; 'minor lower' cannot exist against SW PROTO 1.0) |
| TC-IF-008-02 | G | DEV | M1 | as -01 | GUI | RO chip + banner; motion and config controls disabled |
| TC-IF-009-01 | C | DEV | M3 | std | scripted session: `move_to`, `move_by`, ±0.1/1/10, slider release, jog, travel cal, sequence with travel_ref test and machine | every motion frame carries absolute µm (= commanded target rounded half away); no relative command exists in `Cmd` (AC) |
| TC-IF-010-01 | I/T | DEV | M1 | – | `gen_params.py --check`, `gen_vectors.py --check`; `params_gen.PARAM_DICT_HASH` vs `gen_params.py --hash`; vector `icd_version` vs implemented | exit 0; equal; vectors loaded in place (AC) |
| TC-IF-011-01 | C | DEV | M1 | std | (v0.3, D-37 c / SRS v0.5 IF-011: priority path = STOP, HALT, PAUSE + HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR; RESUME on the CONTROL lane = M1-C8) job queue + GENERAL lane busy (40 queued reads + a write) and the token bucket empty (TX held at 100 frames/s); then `stop()`, `halt()`, `pause()`, RESUME [D-31], clears (v0.2: '4 outstanding' is not constructible through the M1 public API — the Worker serialises jobs; B's ScriptedBoard unit test covers it) | each priority frame is the **next frame** written after the frame in progress (`wire_log`); wait ≤ one frame time + 3 ms (AC: STOP sent while the queue is full goes out first) |
| TC-IF-011-02 | A+C | DEV | M1 | std | analysis of ICD §10; 10 min sim at 80 Hz with events | FW→PC ≤ 9 216 B/s (10 %); measured ≈ 2 080 B/s DATA + events (AC) |
| TC-IF-012-01 | I+P | DEV | M1 | – | checklist: every IF-012 command (+ RESUME 0x3C, D-31) has a builder, and a vector in TC-IF-003-02 | complete. RESUME is blocked until the ICD carries it (SWD-P1-02). |

### 3.4 Platform and configuration (SW-PLT, SW-CFG)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SW-PLT-001-01 | I+D | DEV (I) / REF (D) | M1 | clean Windows 10 user | inspection: Python 3.14 64-bit, `requirements*.txt` = the four runtime packages pinned and installed; offscreen `python -m bend_stand --sim` smoke; D: fresh install on REF (DM-02). v0.2: Python 3.11 dropped (D-33 i) | starts; runtime dependencies = PySide6-Essentials, pyqtgraph, numpy, pyserial (AC) |
| TC-SW-PLT-002-01 | U | DEV | M1 | – | subprocess import of `bend_stand.calc/io/core` with PySide6/shiboken6/pyqtgraph blocked; AST scan for threads/clock/I/O in `calc` | imports succeed; no Qt; `calc` pure (AC: import check) |
| TC-SW-PLT-002-02 | U | DEV | M1/M3 | – | all 22 R4 §12 functions against **production** `calc` (F's copy of the expected values) | 22/22 pass. M1: TV-U/TC/L/M; M3: the rest. |
| TC-SW-PLT-003-01 | C | DEV | M1 | fresh sim | connect | wire order GET_INFO → GET_STATUS → GET_ALL_PARAMS (all pages) → session SET_PARAMs + read-back → STREAM_START; link statistics fields present (AC) |
| TC-SW-PLT-003-02 | C | DEV | M1 | std | FI-01/04/05: exact counts of dropped frames, CRC corruptions and LEN errors | link stats change by exactly the injected amounts (lockstep) (AC) |
| TC-SW-PLT-003-03 | C/X | DEV, TWIN | M1 | – | endpoints `sim`, `tcp://127.0.0.1:5770` (sim server), `tcp://127.0.0.1:5760` (twin) | the same connect behaviour for each |
| TC-SW-CFG-001-01 | C | DEV | M1 | std | `read_all_params` | all 48 values = sim board values; metadata (unit, range, enum names, default) = `params_gen` (AC) |
| TC-SW-CFG-001-02 | G | DEV | M1 | std | Config tab | every parameter shown with its typed editor and board value |
| TC-SW-CFG-002-01 | U | DEV | M1 | – | board-config file round trip; crafted files: unknown key, missing key, out-of-range, wrong type, hash mismatch, newer schema, truncated JSON | round trip identical (enums by name, f32 exact); each case reported; recall fills edit fields only — no SET_PARAM on the wire (AC) |
| TC-SW-CFG-003-01 | C | DEV | M1 | std | write_and_verify: valid edit; out of range; H1/H2/H3/H4 edits that need the write order; `inject_nack`; `inject_store_mismatch`; non-`moving_ok` key while moving; dropped response; `motion.pul_invert` (reboot required); `safety.zero_raw` edit | OK; refused before the first write; OK with a rule-safe order; REJECTED (status, detail); MISMATCH; BUSY with nothing sent; TIMEOUT; REBOOT_REQUIRED → REBOOT_PENDING after SAVE + offer REBOOT; session key ERROR "managed by the backend" and absent from the saved file (AC) |
| TC-SW-CFG-003-02 | G | DEV | M1 | as -01 | Config tab | per-row status shown; session rows locked |
| TC-SW-CFG-004-02 [D-37 a] | C | DEV | **M2 entry** | std; the flash stall modelled by delaying the board response 600 ms (`delay_next`) | SAVE_PARAMS / LOAD_PARAMS / DEFAULT_PARAMS outstanding; during it 240 ms idle (heartbeat and STATUS-poll time), a queued `read_all`, then `stop()`, `halt()`, `pause()` | between the request and its response **only STOP / HALT / PAUSE** appear on the TX wire (no PING, no GET_STATUS, no job); the three stop-class frames are written at the call; the held `read_all` follows the response (AC SW-CFG-004 v0.5 'wire-log test') |
| TC-SW-CFG-004-01 | C+D | DEV, REF | M1 | std | SAVE / LOAD / DEFAULTS (+ confirmation); CFG_DIRTY | SAVE → `nvm_record_seq` +1, CFG_DIRTY 0; DEFAULTS → session thresholds re-sent and verified; indicator follows `sys_flags`; demonstrated in the GUI (AC: demonstration) |

### 3.5 Safety limits and report marks (SW-LIM, SW-META)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SW-LIM-001-01 | C | DEV | M3 | std, travel limits 10…200 mm | set a limit outside the soft limits; `move_to(250)`; jog toward the max limit; jog while at the limit; sequence with a target of 250 mm | refused with a message; nothing on the wire; JOG carries `bound_um` = 200 000; local refusal at the limit; sequence validation ERROR, start refused (AC) |
| TC-SW-LIM-002-01 | C | DEV | M3 | std | FW level 2160 N (> 110 % FS); FW level < SW trip; warning level 90 % | both refused at edit, no SET; warning indicator at 90 % (AC) |
| TC-SW-LIM-003-01 | C | DEV | M3 | std | save/recall session; record | limits identical after recall; applied at start; present in `meta.json` and `report.json` (AC) |
| TC-SW-LIM-004-01 | C | DEV | M3 | no calibration, no tare | motion attempt; enter no-specimen mode without/with confirmation; jog; travel and load wizards start; read-back thresholds; recording; disconnect/reconnect; new Backend instance | refused; CONFIRM required; banner state; jog allowed; both wizards allowed; thresholds = ±7 022 271 / zero 0; event row `NO_SPECIMEN_ON`; mode off after reconnect and restart; not in the session file; LOAD steps refused (AC) |
| TC-SW-LIM-004-02 | C | DEV | M3 | valid calibration + tare | enter no-specimen mode | read-back thresholds = **calibrated** values (SAF-SW-002), not the defaults (AC, Orchestrator review) |
| TC-SW-LIM-004-03 | G | DEV | M3 | – | C-10 dialog; tab switching | keyboard rules as TC-SAF-SW-004-01; banner on every tab |
| TC-SW-META-001-01 | C+G | DEV | M3 | std | marks incl. a custom field (add, rename, remove) → record → report | the custom field is in `meta.json` and `report.html` (AC) |
| TC-SW-META-002-01 | C | DEV | M3 | std | preset save/recall; record; mark edit during the recording | preset identical; `meta.json` contains marks + snapshot (board config, calibration, tare, limits, SW/FW versions, dictionary hash); `MARK_EDIT` row and `marks_final` (AC) |

### 3.6 Realtime display (SW-RT)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SW-RT-001-01 | G | DEV | M3 | std | open 2 plot windows; float / re-attach / close / reopen; STOP in each floating window; save the layout; new MainWindow | ≥ 2 windows; each STOP calls `stop()` synchronously; layout restored (AC: GUI test) |
| TC-SW-RT-001-02 | D | REF | M3 | – | DM-03: second monitor, restart | layout restored on the same monitor |
| TC-SW-RT-002-01 | G | DEV | M3 | std, then without calibration | channel tree: toggle every channel (each DATA field, each `DATA_FLAGS_BITS`/`DATA_STATUS_BITS` bit, sample rate, seq gaps, each derived channel) | every channel draws/hides; derived channels greyed with a reason when prerequisites are missing and re-enabled after calibration (AC) |
| TC-SW-RT-003-01 | G+D | DEV, REF | M3 | std | time window 5 s / 600 s; freeze; auto/manual Y; X-Y view (x mm, y N/kgf) | as specified (AC: demonstration) |
| TC-SW-RT-004-01 | U | DEV | M3 | – | R4 TV-D + VV-D-01…09 (§4.2) on production `calc.derived` | equal (AC) |
| TC-SW-RT-004-02 | C | DEV | M3 | std, 60 s of motion | live derived columns vs F's offline recomputation from the recorded raw | equal within the documented causal lag |
| TC-SW-RT-005-01 | G+D | DEV, REF | M3 | inject saturation, stale, no calibration, extrapolation | readouts | texts n/a / STALE / SATURATED / INVALID / EXTRAPOLATED (AC; Should) |

**SW-RT-006 plot panes (v0.3, D-38; Thrust_Stand SW-RT-004 / D-62 / D-63; GUI design v0.4 §4.7, G-45…G-52).** Level G, offscreen, B's real Backend on the lock-step simulator; expected results from the SRS text and the D-63 rules — never from D's placement code. MS = M1 add-on (SRS MS column M3; first acceptance at the M2 gate, re-run at M3). The SRS acceptance names D-63 rules (1)–(4); rule (5) (group tick) is verified as well (D-63 (5) is part of the adopted Thrust_Stand decision, finding SWD-M2-R6).

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SW-RT-006-01 | G | DEV | M2 | Plot 1 with 5 panes | columns 2 → 3 → 4 → 1 → 3; then 5 | pane order unchanged; `grid.position_of` = row-major `divmod(i, n)` for every n; 5 refused (`ValueError`) (AC: column switch keeps order) |
| TC-SW-RT-006-02 | G | DEV | M2 | one empty pane | "+ Pane"; "Plot in pane 2" (raw); "Move to pane 1"; "Plot in new pane" (x_mm) | empty pane appended; raw ticked straight into pane 2; moved curve leaves pane 2 empty; new pane created with x_mm; a channel appears in exactly one pane per window |
| TC-SW-RT-006-03 | G | DEV | M2 | 4 panes, 2 columns | drop pane 3 on cell 0, then on cell 3 (grid drop handler = the drag & drop target; pane MIME round trip) | order [3, 1, 2, 4] then [1, 2, 4, 3]; columns unchanged (AC: drag-reorder) |
| TC-SW-RT-006-04 | G | DEV | M2 | Plot 1 with raw + x_mm; Plot 2 created (View ▸ New plot window) | "Move to window ▸ Plot 2" of the raw pane | the pane object with the same curve keys is in Plot 2; source window unticks them, target ticks them; Plot 1 keeps ≥ 1 pane (AC: move pane to another window keeps curves) |
| TC-SW-RT-006-05 | G | DEV | M2 | raw in pane 1 | rename "Load cell"; untick / re-tick raw; "Automatic title"; close pane 2 (x_mm); close the last pane | user title kept across curve changes; automatic title = quantity ("Raw counts") again; closing unticks its channels; the last pane cannot be closed |
| TC-SW-RT-006-06 | G | DEV | M2 | empty window | tick bit.valid, raw, x_mm, then bit.moving / homed / paused | all status bits in the first pane that shows status bits; raw, x_mm and bits in three different panes (D-63 (1)) |
| TC-SW-RT-006-07 | G | DEV | M2 | raw in pane 1 + two empty panes | tick x_mm, bit.valid, rate_sps | x_mm → first empty pane, bit → second empty pane (no new pane); rate_sps → a new pane only when no empty pane is left (D-63 (2)) |
| TC-SW-RT-006-08 | G | DEV | M2 | raw, x_mm, two bits | untick one bit; untick x_mm; tick rate_sps | the bit pane keeps the other bit; the emptied x_mm pane is kept and reused by rate_sps; pane count unchanged (D-63 (3)) |
| TC-SW-RT-006-09 | G | DEV | M2 | bit.valid moved by the user to a new pane | tick bit.moving; select the user pane; tick bit.paused | the moved channel stays; later bits join the first pane (grid order) showing status bits; the selected pane does not redirect ticks (D-63 (4)) |
| TC-SW-RT-006-10 | G | DEV | M2 | no calibration (F_N, F_kgf unavailable) | tick the tree group of raw (load) | only the available children ticked; greyed ones stay unticked; the group row is partially checked (D-63 (5)) |
| TC-SW-RT-006-11 | G | DEV | M2 | – | "+ X-Y pane" (x = x_mm, y = raw) | the X-Y pane is a pane type (not a time pane); x choices = travel channels, y = force then raw counts; it pulls `data.xy(x, y, window)` (not the time snapshot); saved as `{"type": "xy", …}` (AC: X-Y view as a pane) |
| TC-SW-RT-006-12 | G | DEV | M2 | 3 time panes in Plot 1, Plot 2 open | zoom the time axis of one pane | the same X range in every time pane of Plot 1, Plot 2 unchanged (AC: time axis linked within a window) |
| TC-SW-RT-006-13 | G | DEV | M2 | Plot 1: 3 columns, 5 panes incl. an empty pane, an X-Y pane, a renamed pane, moved order; Plot 2: 2 columns | save, close, new MainWindow on the same settings file | for both windows: title, columns, pane order, curves per pane (incl. empty panes), user titles, window length identical (AC: restart restores layout) |
| TC-SW-RT-006-14 | G | DEV | M2 | Plot 1 with 4 filled time panes, Plot 2 floating (both shown) | one refresh tick with both windows at 30 s; Plot 2 → 60 s, one tick | exactly **one** `data.snapshot` call for the union of keys; then exactly two (30 s, 60 s) — never one per pane (AC: one snapshot per time window with 4 panes) |
| TC-NFR-001-04 | F (rt) | DEV (informative) / REF | M2 smoke, M3 REF | real-clock simulator 80 Hz; Plot 1 with 4 time panes (raw, travel, 8 status bits, rate) + X-Y pane, 30 s | 4 s refresh (DEV), 10 min on REF in PR-1 | paint-to-paint p95 ≤ 50 ms, no refresh-stage error (AC: NFR-001 frame interval holds with 4 panes) |

### 3.7 Manual control (SW-MAN)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SW-MAN-001-01 | G+C | DEV | M3 | std; then not homed | drag the slider handle; release at 42.5 mm; groove click / wheel / keys | nothing on the wire while dragging; exactly one MOVE_ABS 42 500 µm on release; others send nothing; slider disabled when not homed (AC) |
| TC-SW-MAN-002-01 | C | DEV | M3 | commanded 10 mm; then a move running with pending 12 mm | `move_to(20)`; `move_by(+2.5)`; `move_by(−1)` while pending | MOVE_ABS 20 000; 22 500 (base = commanded target); base = the pending target; only absolute targets on the wire (AC) |
| TC-SW-MAN-003-01 | C | DEV | M3 | commanded 10.000 mm | (a) three +1 mm clicks, each move completing in between; (b) three +1 mm clicks during a running move | (a) wire 11 000, 12 000, 13 000; (b) no MOVE_ABS before MOVE_DONE, then exactly one MOVE_ABS 13 000 (wire 11, 13); pending target in `status()` (AC) |
| TC-SW-MAN-003-02 | C | DEV | M3 | pending target set | STOP; PAUSE; MOVE_DONE with a reason other than TARGET | pending target dropped; no MOVE_ABS follows (D-30 rule "drop pending on PAUSED/STOPPED") |
| TC-SW-MAN-004-01 | C rt | DEV | M3 | std; un-homed variant | `jog_start` for 60 s; `jog_stop`; GUI beat withheld for 400 ms | JOG ≠ 0, then refreshes with a **max interval ≤ 100 ms** (SWD-P1-11); JOG 0 on stop; refresh stops without the GUI beat → sim dead-man controlled stop; un-homed: v ≤ v_unhomed, `JOG_NO_BOUND` (AC) |
| TC-SW-MAN-004-02 | G | DEV | M3 | – | release; focus loss; app deactivate; tab change; gate closes while held | `jog_stop` exactly once each (AC: focus loss → JOG 0) |
| TC-SW-MAN-005-01 | C+G | DEV | M3 | std; loaded (F > band) | speed 31 mm/s unloaded, 21 mm/s loaded, above the step-rate cap; accel > a_max; STOP on the tab | refused with the allowed maximum; STOP mode 0 sent (AC) |
| TC-SW-MAN-006-01 | G+D | DEV, REF | M3 | std | enable/disable (bound to the FW state, C-02); HOME; set/reset test zero; VALID toggle | the checkbox follows DATA ENABLED (no optimistic state); wire commands correct; demonstrated (AC: demonstration) |

### 3.8 Stop, Pause/Break, Pause/Resume (SW-STOP) — incl. D-30/D-31

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SW-STOP-001-01 | G | DEV | M3 | std | for all tabs, docked/floating docks, every dialog class incl. file open/save, every wizard phase, the tare popup | exactly one STOP, `NoFocus`, not default/escape, fires on press; file dialogs are Qt non-native (AC) |
| TC-SW-STOP-001-02 | C | DEV | M3 | sequence, travel wizard, jog, load approach | `stop()` | STOP mode 0 on the priority path; `terminate_all`; no motion frame of the old epoch after the STOP (AC) |
| TC-SW-STOP-001-03 | I | DEV | M3 | – | inspect the application start | `AA_DontUseNativeDialogs` set before any dialog (AC: inspection) |
| TC-SW-STOP-001-04 [D-37 d] | C | DEV | **M2 entry** | lock-step, link latency 8 ms each way, enabled + homed (forced path) | MOVE_ABS 200 mm at 20 mm/s sent; `stop()` 2 ms later with the STOP **request lost** | precondition shown on the wire: a DATA frame produced before the FW executed the MOVE (MOVING = 0) arrives after the STOP write; expected: it does **not** confirm the STOP — the STOP is repeated (≥ 2 frames), `stop.confirmed` has attempts ≥ 2, the axis stops long before 200 mm (OBS-M1-R1; AC SW-STOP-001 v0.5 'confirmed only by ACK or an indication with t_us ≥ the FW receive time') |
| TC-SW-STOP-002-01 | W | DEV interactive, REF | M3 | std, sequence running, Notepad focused | `SendInput` Pause; then Ctrl+Break; stream off variant; first 3 HALT responses dropped | HALT on the wire, sequence terminated; repeated every 50 ms until ACK (4 frames), ≤ 20 in 1 s; with the stream off, confirmed via GET_STATUS `flags.HALT`; hotkey indicator active (AC) |
| TC-SW-STOP-002-02 | C | DEV | M2 (pre-written) / M3 | fake hotkey backend (hook `test_hooks.hotkey_press()`, GRQ-F-M2-01), axis moving through the M2 API | callback; first 3 HALT requests lost; variant all lost for 1.2 s | HALT written ≤ 1 ms after the callback, repeated every 50 ms, confirmed with attempts = 4; all lost: ≤ 20 in 1 s, `stop.unconfirmed` + banner "use the red E-stop" (v0.3, CR-01: no physical STOP) |
| TC-SW-STOP-002-04 [D-37 d] | C | DEV | **M2 entry** | lock-step, 8 ms latency; HALT latched | Clear stop (HALT_CLEAR in flight), Pause/Break 1 ms later with the HALT request lost | frames produced before the clear executed (HALT = 1) arrive after the HALT write and do **not** confirm it: HALT repeated (≥ 2), attempts ≥ 2, HALT latched at the end |
| TC-SW-STOP-002-03 | D | REF | M3 | – | DM-06: key with an elevated window focused; the application itself elevated | not delivered (KL-01) and documented; the GUI shows the hotkey state and the elevation warning |
| TC-SW-STOP-003-01 | C | DEV | M3 | (a) sequence (b) travel wizard (c) load-cal capture (d) tare | FI-18 PC HALT (Pause/Break key), FI-19 E-stop (power cut via K1), FI-15 DRV_PWR loss (v0.3, CR-01: the physical STOP-button case is removed) | each operation ABORTED in the pipeline batch of the carrier frame; no motion frame from it afterwards (AC: within one received frame) |
| TC-SW-STOP-003-02 | C | DEV | M3 | HALT latched (Pause/Break key) | reconnect; RESUME; then Clear stop; then watch 5 s | HALT survives reconnect and RESUME (E_STATE HALT); HALT_CLEAR clears HALT (never refused with E_CAUSE_ACTIVE since ICD v0.5, D-36); **no motion command** for 5 s (AC). v0.3: 'Clear stop while the STOP button is pressed' removed (CR-01) |
| TC-SW-STOP-004-01 [D-31] | C | DEV | M4 | sequence, TRAVEL step moving | GUI Pause | wire **PAUSE** (not STOP); sim controlled stop; sequence PAUSED with step/loop kept; no VALID = 1 frame until resumed; Resume → wire **RESUME (0x3C)** → after OK / PAUSED = 0 → MOVE_ABS with the step's absolute target; the step completes with a new settle + capture window; the report uses the resumed window only (AC) |
| TC-SW-STOP-004-02 [D-31] | C | DEV | M4 | LOAD step in (a) approach (b) trim (c) capture | GUI Pause, then Resume | RESUME, then approach + trim re-run from the current force; within tolerance; capture window restarted (AC: pause in a load step) |
| TC-SW-STOP-004-03 [D-31] | C | DEV | M4 | sequence in settle / capture / HOLD | physical PAUSE press; second press (RESUME_REQUEST) | PAUSED (source BUTTON), window discarded; the second press → RESUME + re-issue (resume gate open) or "resume request ignored: reason" (gate closed) |
| TC-SW-STOP-004-04 [D-31] | C | DEV | M3 | manual `move_to` running | GUI Pause; `move_to` / jog / HOME while PAUSED; forced `Device.move_abs`; Resume; new `move_to` | controlled stop, pending dropped; gates REFUSE "PAUSED — Resume"; forced → NACK E_STATE BLOCK PAUSED, no motion (sim PUL count unchanged); manual Resume = **RESUME only**, no motion frame; a new `move_to` works afterwards |
| TC-SW-STOP-004-05 [D-30] | C | DEV | M3 | jog running | PAUSE (button), with a JOG refresh in flight | jog session ended on EVENT PAUSED before the next refresh; an in-flight refresh gets NACK E_STATE PAUSED, treated as expected (no DEGRADED, no retry); the axis does not restart |
| TC-SW-STOP-004-06 [D-30 race] | C | DEV | M3/M4 | MOVE_ABS (manual, and as a sequence step) | `SimControl` schedules a PAUSE-button press at −20…+20 ms (1 ms steps) around the arrival of the MOVE_ABS frame; link latency 1–16 ms; 41 × 2 runs | in every run: no pulse after the PAUSED-set instant other than the controlled deceleration; no motion restart; the sequence ends PAUSED (never ERROR); manual: no auto re-send. Also X. |
| TC-SW-STOP-004-07 [D-31] | C+G | DEV | M3 | HALT latched (Pause/Break) while PAUSED (manual) | GUI Resume; API `resume()` | `resume` gate REFUSE "**Clear stop first**", toolbar Resume disabled with that tooltip; **no RESUME frame** sent; HALT and PAUSED unchanged. A forced RESUME frame (`Device`) → NACK E_STATE (BLOCK HALT), nothing cleared. |
| TC-SW-STOP-004-08 [D-31 race] | C | DEV | M4 | sequence PAUSED | (v0.3, CR-01) GUI Resume, then the Pause/Break key 0–30 ms later (1 ms steps; HALT on the priority path can overtake the RESUME on the CONTROL lane) — and the variant E-stop opened 0–30 ms before the RESUME frame arrives | whichever arrives first: HALT (resp. ESTOP) latched at the end, PAUSED latched or cleared consistently with the FW verdict; **no re-issue** MOVE_ABS is sent, or a sent one is refused; zero pulses after the HALT / E-stop; the sequence is terminated (SW-STOP-003); clearing needs Clear stop. Also X. |
| TC-SW-STOP-004-09 [D-31] | C | DEV | M3 | PAUSED; ESTOP / FAULT latched variants | Resume | gate REFUSE with the latch name; no RESUME frame; forced → NACK E_STATE with that BLOCK bit |
| TC-SW-STOP-004-10 [D-31] | C | DEV | M3/M4 | PAUSED | PAUSE pressed again between the RESUME OK and the re-issue | re-issue NACK E_STATE PAUSED; the sequence stays PAUSED with a reason (not ERROR); PAUSE while PAUSED → no new EVENT PAUSED |
| TC-SW-STOP-004-11 [D-30] | C | DEV | M3 | travel wizard MOVE1; load-cal capture; tare capture | PAUSE | wizard / tare ABORTED (not resumable); travel spm0 restored (SET_PARAM is allowed while PAUSED); `cal_travel_start` / motion gates REFUSE "PAUSED — Resume" until RESUME |
| TC-SW-STOP-004-12 [D-31] | C | DEV | M4 | sequence PAUSED, HALT not latched | Clear stop | per SW_design v0.2 refused locally ("use Resume or Stop"). D-31 makes HALT_CLEAR also clear PAUSED; expected behaviour to be confirmed by B v0.3 (SWD-P1-02) |
| TC-SW-STOP-004-13 | G | DEV | M3 | – | toolbar Pause/Resume with the PAUSED source; RESUME_REQUEST toast | follows `indicators.paused` and its source |

### 3.9 Acquisition (SW-ACQ)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SW-ACQ-001-01 | G+C | DEV | M1 | std | stream toggle on every tab; `stream_stop` while moving / while an operation runs | available on every tab with its state; stop refused while moving or during an operation (AC) |
| TC-SW-ACQ-002-01 | C | DEV | M3 | lockstep, t_us starting 60 s before the 2³² wrap | record 1 h of device time (288 000 frames + rate ε) with a sequence and events | `data.csv` D rows = the simulator's sent-frame list **exactly once each** (frame_seq + t_us); t_us unwrapped and monotonic across the wrap; event rows (TARE, VALID_ON/OFF, STOP, step boundaries) time-ordered; `meta.json` valid; the recording covers the whole sequence (AC: 1 h every frame exactly once) |
| TC-SW-ACQ-002-02 | F | REF | M3 | rt | as -01 inside the NFR-004 soak | same result in real time |
| TC-SW-ACQ-003-01 | U | DEV | M3 | – | sample statistics on a synthetic window (F's oracle); window 0.09 s / 0.1 s / 10 s / 10.01 s | mean, std (ddof 1), N equal; out-of-range windows refused (AC: pytest) |
| TC-SW-ACQ-003-02 | C+G | DEV | M3 | recording / not recording | Take sample | one row in `samples.csv` (or the daily file) with timestamp and marks (AC: one row) |
| TC-SW-ACQ-004-01 | C | DEV | M3 | std, recording | FI-01/04/09 counts | gaps, CRC errors and overruns in `meta.json` and per-row `seq_lost` = injected (AC) |
| TC-SW-ACQ-004-02 | C | DEV | M3 | sequence + recording | FI-25 `fail_recorder(OSError(ENOSPC))`; `fail_recorder(PermissionError)` | `REC_FAILURE` ≤ 1 s; indicator; sequence ends via **STOP mode 1** (controlled); `rows_written + rows_lost` = frames received; REC_GAP row; `complete: false` (AC: no silent drop) |
| TC-SW-ACQ-004-03 | C | DEV | M3 | recording | `stall_thread("recorder", 70 000)` (queue 60 s) | overflow reported and counted, never silent |
| TC-SW-ACQ-004-04 | C | DEV | M3 | free-space probe stubbed below the limit | `record_start`; sequence start | refused with the reason |

### 3.10 Calibration and tare (SW-CAL, SW-TARE)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SW-CAL-001-01 | G | DEV | M3 | std | walk both wizards through all pages | description, input fields, Continue / Repeat / Cancel, progress bar during capture, STOP on every page (AC) |
| TC-SW-CAL-001-02 | C | DEV | M3 | std, spm0 = 160 | Cancel in each travel phase (BACKLASH … RESULT, incl. after spm1 written) | board `motion.steps_per_mm` read back = spm0; **no SAVE_PARAMS** on the wire; `nvm_record_seq` and `active_travel.json` unchanged (AC) |
| TC-SW-CAL-001-03 | C | DEV | M3 | travel wizard | E-stop during MOVE2; link loss after spm1 (reconnect); sim reset (NVM value); third-party value written in between | spm0 restored when idle; restore-pending record survives; spm0 restored at reconnect; reset → record deleted without a write; foreign value → no write + `travel_cal_differs` indicator; indicator and metadata until verified (AC) |
| TC-SW-CAL-001-04 | C | DEV | M3 | load wizard | STOP / HALT / PAUSE / link loss at each point | active load calibration unchanged (file + `status()`) |
| TC-SW-CAL-002-01 | U | DEV | M3 | – | R4 TV-TC; VV-TC-01/02 | N1 = 6400, spm1 = 636.8159…, N2 = 31 841, spm2 = 636.0778… (AC) |
| TC-SW-CAL-002-02 | C | DEV | M3 | sim true steps/mm = 160·(1 + 0.5 %); no-specimen mode variant | run the wizard; D inputs = sim true distances | +2 mm backlash move first; all moves absolute and in one direction; N1 from MOVE_DONE `value2`; result = true value (rel 1e-6); allowed in no-specimen mode (AC: simulator run) |
| TC-SW-CAL-003-01 | U | DEV | M3 | – | VV-TC-03 plausibility set (§4.2) | reject D ≤ 0 and results outside 100…10 000; 160 → 800 = **confirmation** (not rejection) showing **160 / 800** (SWD-P1-07); 5 % and ±20 % rules; "repeat" warning at > 0.5 % (AC) |
| TC-SW-CAL-004-01 | C | DEV | M3 | – | Accept | SET + read-back, SAVE_PARAMS, CFG_DIRTY 0, `nvm_record_seq` +1; `travel_<UTC>.json` + `active_travel.json` agree with the board; 3 decimals shown (AC) |
| TC-SW-CAL-005-01 | C | DEV | M3 | stream off; no-specimen mode variant | load wizard: zero, 1 kg, 10 kg | the stream starts and is restored after; 2 s pre-settle excluded; each capture N = round(10 s × measured rate) ± 1 (= 804 at +0.5 %) (AC "800 ± 1 nominal", see SWD-P1-10) |
| TC-SW-CAL-006-01 | U | DEV | M3 | – | R4 TV-RS; VV-RS boundaries | 760 kept → valid / 759 → invalid; 16 outliers of 800 → valid / 17 → invalid; drift and std rules; m2 = 1.49·m1 → reject; m1 < 4 kg → warning only (AC) |
| TC-SW-CAL-006-02 | C | DEV | M3 | load wizard | FI-09 saturation; FI-12 spikes p = 3 %; FI-13 drift; noise × 4; missed conversions 6 %; decreasing masses | REJECTED with the specific reason; only Repeat offered (AC: one scenario per rejection) |
| TC-SW-CAL-007-01 | U | DEV | M3 | – | R4 TV-LC (PASS/WARN/FAIL/2-point/degenerate) + VV-LC-01…06 | statuses and K exactly as listed (AC) |
| TC-SW-CAL-007-02 | C+G | DEV | M3 | WARN / FAIL fits | Accept | WARN needs the confirmation (C-06); FAIL: Accept disabled, "points are not linear" |
| TC-SW-CAL-008-01 | U+C | DEV | M3 | 1 kg + 10 kg calibration | display / record F = 294.0 N and 294.4 N | LOW_SPAN set (98.07 N < 392.27 N); 294.4 N marked extrapolated (> 294.1995 N), 294.0 N not (AC; Should) |
| TC-SW-CAL-009-01 | C | DEV | M3 | – | accept → new Backend instance | JSON per the R4 §6.4 schema (afe, points, fit, status, `push_calibrated: false`); the active copy loads with the same K/B; previous files kept (AC) |
| TC-SW-CAL-009-02 | C | DEV | M3 | std | change `afe.gain_channel` (then `afe.rate_sps`) on the board | "calibration invalid"; motion disabled with load limits enabled; forces flagged invalid; allowed in no-specimen mode (AC) |
| TC-SW-TARE-001-01 | G | DEV | M3 | std | TARE from every tab | non-modal progress; refusal reasons verbatim (AC) |
| TC-SW-TARE-002-01 | U | DEV | M3 | – | R4 TV-T `force_n` | equal (AC) |
| TC-SW-TARE-002-02 | C | DEV | M3 | stream off | tare; new Backend instance; different board UID | tare_raw within 3σ/√N of the model offset; stream restored; TARE event row; thresholds + zero_raw re-sent and verified; after restart no tare and load-limited motion refused; no tare file written anywhere under `tmp_appdata` (AC) |
| TC-SW-TARE-003-01 | C | DEV | M3 | std | one case each: moving; 0.9 s / 1.1 s after a move (device time); HALT / FAULT latched; std high; drift; 17/800 outliers; one saturated sample; 9 vs 8 of 800 lost; during a sequence capture window; offset 429 491 counts (> 10 % FS at K_TV) | refused / accepted at the boundaries as stated; the offset case is accepted with a warning (AC: one scenario per refusal/warning) |

### 3.11 Sequencer, generators, files, chart (SW-SEQ, SW-WIZ, SW-SEQF, SW-SCH)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SW-SEQ-001-01 | U | DEV | M4 | – | model validation per field (types, ranges, `capture_during_move` needs capture > 0, LOAD needs tol and k_est); `travel_ref` test vs machine; x_zero changed during a run | issue per field; machine target = target + x_zero frozen at start; a later x_zero change has no effect (AC: both references) |
| TC-SW-SEQ-001-02 | G | DEV | M4 | – | editor cells | invalid entries flagged |
| TC-SW-SEQ-002-01 | U | DEV | M4 | – | VV-SEQ-01…04 expansions | lists equal; count 0 → infinite flag; counts 0* / 10 001 rejected, 1 / 10 000 accepted; overlap / 2-level nesting → ERROR (AC: expanded-plan tests) |
| TC-SW-SEQ-002-02 | C | DEV | M4 | – | run a nested-loop sequence | `loop_iter` in rows, sidecar and report |
| TC-SW-SEQ-003-01 | C | DEV | M4 | lockstep | 10-step sequence (TRAVEL, LOAD, HOLD, HOME, TARE, MARK) | settle / capture start / capture end / step end each within **±1 frame** of t_reached + planned offset; TRAVEL/HOLD dwell = max(step time, settle + capture), LOAD dwell = settle + capture; recording from before SEQ_START to after the end (AC) |
| TC-SW-SEQ-004-01 | C | DEV | M4 | lockstep | 50-step sequence incl. one pause/resume and one aborted run | every VALID = 1 frame lies inside a logged planned window; windows contiguous, starting at `t_on`; VALID = 0 after window end, pause, stop, abort; never re-asserted after a FW auto-clear (AC) |
| TC-SW-SEQ-005-01 | C | DEV | M4 | std | one refusal per reason: not homed; not enabled; DRV_PWR absent; ALM active (powered); ESTOP / HALT / FAULT / LIMIT latched; **PAUSED** [D-30]; target outside SW limits; LOAD step without calibration / tare; thresholds not verified; recording cannot start; (POS_UNCERTAIN, SWD-P1-04) | start refused with that reason; nothing on the wire (AC: one test per reason) |
| TC-SW-SEQ-006-01 | U | DEV | M4 | – | R4 TV-C; VV-C-01…03 (band, raw_stop rounding, cmp selection) | equal (AC) |
| TC-SW-SEQ-006-02 [D-32] | C | DEV | M4 | spring k = 50, k_est = 40, target 200 N, tol 2 N | LOAD step | MOVE_UNTIL_LOAD with `bound_um` = **soft limit in the step direction** (D-32; SWD-P1-18 for enabled SW travel limits), cmp / raw_stop per VV-C; trims ≤ 0.2 mm at 0.2 mm/s; within tol in ≤ 10 iterations; no motion frame during settle / capture / hold (AC) |
| TC-SW-SEQ-006-04 [D-32] | C | DEV | M4 | spring too soft to reach the target before the soft limit (k = 2 N/mm, target 1000 N from x = 130 mm, soft max 290 mm); variant pull_dir −1 toward soft min | LOAD step at 2 mm/s | approach continues past the planned travel and past 3× planned + 10 s **without** a timeout abort; FW MOVE_DONE BOUND exactly at the soft limit (no limit-switch trip); step marked **NOT_REACHED**; sequence STOPPED; no trim attempted; report row NOT_REACHED with the final x and F |
| TC-SW-SEQ-006-03 | C | DEV | M4 | tol 0.01 N with σ 45 counts | LOAD step; `on_trim_fail` = stop / continue | stop: step aborted after 10 iterations, sequence STOPPED; continue: capture with NOT_ON_TARGET |
| TC-SW-SEQ-007-01 | C | DEV | M4 | std | FI-28 slip (> 5 %), break (drop > 20 %), TRAVEL-step timeout (no MOVE_DONE within 3× planned + 10 s: driver lag model stalled) | priority STOP, step failed, sequence STOPPED, event with values (AC: per guard) |
| TC-SW-SEQ-007-04 [D-32] | C | DEV | M4 | LOAD step, approach toward the soft limit | (a) `step_time` 0: compute the timeout; let the sim stall the move (DRV lag) so the bound is never reached; (b) specimen break (`f_break`) at 60 % of the target during a long approach | (a) timeout = travel time from the start to the bound at the step speed (trapezoid, VV-SEQ-05) + margin, never shorter; STOP only after it; (b) BREAK_DETECTED aborts at once (STOP, sequence STOPPED) even though the bound is far away |
| TC-SW-SEQ-007-02 | C | DEV | M4 | std | controls start / pause / resume [D-31] / stop / abort | stop → STOP mode 1, STOPPED; abort → HALT, ABORTED, HALT latched; pause/resume per TC-SW-STOP-004-01 |
| TC-SW-SEQ-007-03 | C | DEV | M4 | sequence running | FI-16 ALM becomes active during a step | the running move completes; the next step's motion is refused (DRIVER_ALARM); the sequence ends STOPPED with reason "driver alarm" without retrying (SWD-P1-14) |
| TC-SW-WIZ-001-01 | U | DEV | M4 | – | VV-GEN-01…07 | step lists equal; invalid arguments → ValueError (AC) |
| TC-SW-WIZ-002-01 | G | DEV | M4 | – | insert append / replace / after; edit the inserted steps | inserted and editable; preview updates (AC; Should) |
| TC-SW-SEQF-001-01 | U | DEV | M4 | – | round trip of a sequence with every field and loop; corrupt files (truncated, wrong schema, newer version, invalid step, overlapping loops) | round trip byte-identical; `FileFormatError`; the current sequence is unchanged (identity + deep equality) (AC) |
| TC-SW-SCH-001-01 | U | DEV | M4 | – | VV-PATH | points equal; a HOME step breaks the line (AC: path computation) |
| TC-SW-SCH-001-02 | D | REF | M4 | – | DM-08 chart with labels | as specified |
| TC-SW-SCH-002-01 | C+G | DEV | M4 | sequence running | 5 s of marker updates | backend `SeqStatus` publishes ≥ 10 Hz; GUI marker repaints ≥ 10 Hz (paint counter); highlighted step = `exec_idx`; measured trace overlaid (AC: timing test) |
| TC-SW-SCH-002-02 | D | REF | M4 | – | DM-08 smoothness | accepted by the PO |

### 3.12 Report (SW-REP)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-SW-REP-001-01 | C | DEV | M4 | sequence with LOW_SPAN calibration, push forces, extrapolation, one INCOMPLETE window | run → report | `data.csv`, `meta.json`, `report.json`, `report.html` exist; the HTML parses (`html.parser`) and contains marks incl. custom fields, F–x and F–t SVG figures, the step table and warnings (LOW_SPAN, tension-calibrated push, extrapolated, INCOMPLETE); the JSON has calibration, tare, limits, sequence, board config, versions, k_est, events (AC: checklist) |
| TC-SW-REP-001-02 | D | REF | M4 | – | open the HTML in a browser | renders (AC) |
| TC-SW-REP-002-01 | U | DEV | M4 | – | R4 TV-SS; VV-SS-01…05 | N = 7, mean 300 047.142857…; each mask bit excludes; setpoint change excludes; INCOMPLETE at N = 63 of 64 needed, not at 64; ON_TARGET inclusive at \|mean − target\| = tol (AC) |
| TC-SW-REP-002-02 | C | DEV | M4 | – | sequence → report | per-window statistics equal F's recomputation from `data.csv` |
| TC-SW-REP-003-01 | C | DEV | M4 | recording | offline CLI with no options; with `--cal` (K' = 1.01·K); with `--tare-raw` + 1000 | identical numbers; F scaled by 1.01; F shifted by −K·1000 (AC; Should) |
| TC-SW-REP-004-01 | U+C | DEV | M4 | – | TV-D 3-point bend; report without geometry | 60 MPa, 0.006, 5000 MPa; no σ/ε output when off (AC; Should) |

### 3.13 Non-functional (NFR-001…004)

| TC | Lvl | Env | MS | Pre | Stimulus | Expected |
|---|---|---|---|---|---|---|
| TC-NFR-001-01 | F | **REF** | M3 | out-of-process sim at 80 Hz; recording on; Plot 1 = time panes with **all registry channels** (v0.3: placed by the D-63 rules, ≥ 4 panes, 2 columns) + an X-Y pane, 30 s window; Plot 2 floating | 10 min | paint-to-paint interval **p95 ≤ 50 ms** (≥ 20 fps); event-loop p99 ≤ 100 ms reported (AC; GUI P-01) |
| TC-NFR-001-02 | F | DEV | M1… | as -01 | 60 s smoke at every milestone | trend recorded (informative) |
| TC-NFR-001-03 | F | REF | M3 | 600 s window, 4 plot windows | 10 min | ≥ 20 fps (informative, P-04) |
| TC-NFR-002-01 | F | **REF** | M3 | NFR-001 load | 100 STOP clicks (posted mouse press on the toolbar and dock STOP, 0.5 s apart) | click timestamp → STOP frame in the sim-server `wire_log` **p95 ≤ 50 ms** (AC; P-02) |
| TC-NFR-002-02 | C rt | DEV | M1 | headless | 100 × `Backend.stop()` | ≤ 50 ms p95 (backend part, WP-B11) |
| TC-NFR-003-01 | F+W | **REF** | M3 | NFR-001 load, another application focused | 100 `SendInput` Pause presses | key → HALT frame **p95 ≤ 50 ms** (AC; P-03) |
| TC-NFR-003-02 | H | TGT | HW gate | PO approval, with Validator E | 100 presses, scope on PUL | key → last PUL edge ≤ 100 ms p95 |
| TC-NFR-004-01 | F | **REF** | M3 | NFR-001 load, recording | 1 h at 80 Hz (≈ 289 440 frames at +0.5 %) | `async_overflow` 0, `rows_lost` 0, link loss 0 (LinkModel loss 0) → 0 SW-attributable losses; rows = frames sent; RSS growth from minute 15 (ring buffer full) to the end **≤ 50 MB** (AC; P-05) |
| TC-NFR-004-02 | C | DEV | M1/M3 | lockstep accelerated | 1 h of device time, backend only | 0 SW losses; memory bounded (regression at every milestone) |
| TC-NFR-004-03 | H | TGT | HW gate | VCP | 1 h | 0 seq gaps, 0 CRC errors |

### 3.14 M2 SW early acceptance (v0.3; backend B builds in M2, D-39)

The SRS assigns no SW requirement to M2, but B builds the motion backend (MotionController, motion gates, simulator WP-B12) and D-39 folds the M1 close-out items into M2. These TCs are accepted at the **M2 gate** (early acceptance; they are re-run at M3 where their requirement's MS lies). **Run** = column "now": ✓ = runs against the current simulator / F-board (forced wire path where the M2 API is not there yet); P = pre-written, `pending("M2")`, armed at the M2 verification (§2.4a). Simulator-fidelity cases check B's test environment against the ICD / SRS §3.2 / `params.yaml` / `ref_motion` (rule 3); each is also an X case against the twin once A's M2 motion is in it.

| TC | Run | Lvl | Pre | Stimulus | Expected |
|---|---|---|---|---|---|
| TC-SYS-008-04 (9 scenarios) | ✓ | C | lock-step sim, forced enable + home | (a) MOVE_ABS 50 mm @ 10 mm/s; (b) JOG with bound 3 mm (80 ms refresh), JOG without refresh; (c) re-HOME from 40 mm; (d) END limit during a + move, + / − moves while latched, release; (e) E-stop during a move (K1 delay 40 ms), clear; (f) DRV_PWR loss with E-stop closed; (g) PAUSE during a move, MOVE_ABS / JOG while PAUSED, RESUME; (h) MOVE_ABS response lost; (i) [D-40 a] both limits active, release END, FAULT_CLEAR, moves toward / away from START | (a) MOVE_DONE TARGET value 50 000 µm, value2 = `um_to_steps` (f_ref), MOVING 1 → 0, duration within [t_plan − 20 ms, t_plan + 0.5 s] of `plan_trapezoid`; (b) MOVE_DONE BOUND at 3 000 µm; STOPPED JOG_DEADMAN `jog_timeout_ms` … + 20 ms after the last JOG, no VALID_CLEARED; (c) HOMED, setpoint 0, no POS_UNCERTAIN, no HOME_DRIFT; (d) STOPPED LIMIT_END + LIMIT_SET; + move E_STATE BLOCK LIMIT, − move OK; LIMIT_CLEARED ≤ `io.release_ms` + 50 ms after release; (e) STOPPED ESTOP ≤ 3 ms, ESTOP_SET, DRIVER_DISABLED 3, DRIVER_POWER 0 at k1 + 0…25 ms (20 ms DRV_PWR filter), HOMED 0; ENABLE E_STATE ESTOP until ESTOP_CLEAR after `io.estop_release_ms`; (f) STOPPED DRV_POWER_LOST, DRIVER_POWER 0, DRIVER_DISABLED 4, HOMED 0, ENABLE E_STATE DRV_UNPOWERED; (g) STOPPED PC_PAUSE, PAUSED arg PC, no POS_UNCERTAIN; E_STATE BLOCK PAUSED for both; PAUSE_CLEARED arg 3 without motion; MOVE_ABS OK afterwards; (h) one MOVE_ABS, one MOVE_DONE (IF-005); (i) FAULT_SET LIMIT_WIRING; FAULT_CLEAR E_CAUSE_ACTIVE while both active, OK after END released; − move toward START E_STATE BLOCK LIMIT, + move OK |
| TC-SYS-008-05 (WP-B12) | ✓ | C | lock-step sim | K1: E-stop open with DRV_PWR held, `drv.k1_weld_ms` ∈ {100, 200, 2000}; normal drop 60 ms; idle disable (unloaded / loaded ≥ release band); LOAD_LIMIT regrow (200 N/mm spring past the default FW threshold, FAULT_CLEAR, reduce, increase) | K1_WELDED within (k1, k1 + 25 ms], never in the normal case (SAF-FW-025); DRIVER_DISABLED cause IDLE at `idle_disable_s` ± 1 s, none when loaded (2 × idle time) (SAF-FW-017); clear accepted beyond the threshold, unloading move runs, loading again re-trips (SAF-FW-011) |
| TC-SYS-008-06 | ✓ | P | – | `motion_vectors.json` (ICD v0.5, `ref_motion.py`): event-free ramp cases, planner, `ctrl_stop_paths` | production `calc.motion` (the simulator's ramp) reproduces every period **exactly**, the planner fields and the CLEAN / ISR / STRETCH selection (D-30) |
| TC-SW-MAN-002-02 | P | C | M2 API: enable + home | `move_to(20)`, `move_by(+2.5)`, `move_to(12.3455)` | one MOVE_ABS each: 20 000, 22 500, 12 346 µm (round half away); no relative command (IF-009, SYS-003) |
| TC-SW-MAN-003-01 / -02 | P | C | as above | (see §3.7) | wire 11/12/13 and 11/13; pending target dropped on STOP / PAUSE |
| TC-SW-MAN-004-01 (3 cases) | P | C | as above; un-homed variant | jog 5 s with GUI beat; jog without beat; un-homed jog 50 mm/s | refresh max interval ≤ 100 ms, JOG 0 on stop; FW dead-man STOPPED JOG_DEADMAN ≤ 300 ms + `jog_timeout_ms` + 200 ms; un-homed v ≤ `v_unhomed_um_s`, bound = JOG_NO_BOUND |
| TC-SW-MAN-005-01 | P | C | as above | 31 mm/s; accel > a_max | `check()` refuses naming the maximum; nothing on the wire |
| TC-SW-LIM-001-01 | P | C | SW limits 10…200 mm | `move_to(250)`; jog + from 190 mm | refused locally, nothing on the wire; JOG `bound_um` = 200 000, MOVE_DONE BOUND at 200 000 |
| TC-SW-MAN-006-02 (7 cases) | P | C | not enabled / not homed / HALT / PAUSED / ESTOP / DRV_PWR off / ALM | `move` gate; `move_to` through the API | REFUSE item with the generated BLOCK name (NOT_ENABLED, NOT_HOMED, HALT, PAUSED, ESTOP, DRV_UNPOWERED, DRIVER_ALARM); no MOVE_ABS on the wire |
| TC-SAF-SW-004-02 | P | C | enabled, not homed, load unknown | `disable()` / `home()` without confirmation; `home(load_confirmed=True)` | refused, nothing on the wire; HOME flags bit0 = 1 |
| TC-SW-STOP-002-02 | P | C | fake hotkey, moving | hotkey press, 3 HALT requests lost | as §3.8 |
| TC-SW-STOP-001-04, TC-SW-STOP-002-04, TC-SW-CFG-004-02, TC-SAF-SW-005-03/-04, TC-IF-001-02 | ✓ (strict xfail while open) | C / I | – | §3.2–§3.8 | M2 **entry** items (D-37, CR-01; STATUS E-C3, E-C4, F-MC-4) |
| TC-SYS-008-02 (X motion subset) | at M2 | X | twin with A's M2 motion | TC-SYS-008-04 (a)–(h) + TC-SYS-008-05 on sim and twin | identical EVENT sequences / verdicts; positions ± 1 step (Integrator's `test_sim_vs_twin.py` + F's `test_v_twin.py`) |

---

## 4. Calculation vectors

### 4.1 R4 §12 (verbatim, all 22 functions)
TV-U, TV-LC (5), TV-RS (3), TV-T (2), TV-TC (2), TV-M (5), TV-L (2), TV-SS, TV-C, TV-D are copied into `validation/vectors/test_r4_vectors.py` unchanged, bound to the production functions through `harness.calc`.

TV-M (ramp/planner) has no SW requirement other than simulator kinematics. It is kept for SYS-008 and the FW cross-check.

TV-L `test_link_budget` is kept as a constant check, but the IF-011 oracle is ICD §10 (26-byte frame, 2 080 B/s, 2.26 %).

### 4.2 Validator vectors (VV, computed by F with `f_ref.py`, never with production code)

| ID | Function | Input | Expected |
|---|---|---|---|
| VV-U | rounding | 0.0005 mm, −0.0005 mm, 12.3454 mm, ±2.5 | 1, −1, 12 345 µm, ±3 |
| VV-D-01 | speed (central ±2) | x = 0, 125, 250, 375, 500 µm at t = 0…50 000 µs (12.5 ms) | 10.0 mm/s |
| VV-D-02 | speed with jitter | same x, t = 0, 12 400, 25 100, 37 450, 50 050 µs | 9.99000999… mm/s |
| VV-D-03 | force rate (SG 9, order 2, uniform h = 12.5 ms) | F = 3 + 160 t + 50 t² | 165.0 N/s at the centre (exact for quadratics). Gap in the window: behaviour to be defined (SWD-P1-12). |
| VV-D-04 | tangent stiffness | standstill window (Δx = 0) | NaN |
| VV-D-05 | secant stiffness | F = 10 N at x_test = 0.2 mm; x_test = 0.04 mm | 50 N/mm; NaN |
| VV-D-06 | peak / break | F = 0, 50, 100, 150, 200, 170, 155 | peak 200; BREAK at the 155 sample (< 160); not at 170 |
| VV-D-07 | rolling std (1 s) | 80 samples alternating ±45 | 45.28391448839648 counts |
| VV-D-08 | kgf | 98.0665 N | 10.000000000 kgf |
| VV-D-09 | work | TV-D | 4.01 N·mm |
| VV-LC-01 | linearity just PASS | raw 125 000 / 339 760 / 555 800, masses 0 / 10 / 20 kg | NL 0.09904 %, PASS, K 4.55274890527817e-4 |
| VV-LC-02 | just WARN | raw2 = 555 825 | NL 0.10097 %, WARN |
| VV-LC-03 | upper WARN | raw2 = 561 050 | NL 0.49914 %, WARN |
| VV-LC-04 | just FAIL | raw2 = 561 075 | NL 0.50102 %, FAIL |
| VV-LC-05 | non-monotonic | 125 000 / 339 760 / 300 000 | FAIL |
| VV-LC-06 | negative K, 1 kg + 10 kg | raw 125 000 / 92 788 / −197 120, masses 0 / 1 / 10 | K −3.0444089159e-4, PASS, LOW_SPAN (98.07 N < 392.266 N), m1 < 2 % FS warning |
| VV-RS | point acceptance | N 760/759 of 800; outliers 16/17; lost 8/9 | valid/invalid at each boundary (strict ">") |
| VV-T | tare offset warning | K_TV, offset 429 490 / 429 491 counts | no warning / warning (boundary 429 490.0007) |
| VV-THR-01 | thresholds + clamp | K = 1/3285, tare 125 000, level 2157.463 N | (−6 962 265, 7 212 265) → clamped max 7 151 121, F_eff_hi 2138.8496 N, warning |
| VV-THR-02 | negative K | K = −1/3285, same | sides swap, same clamp |
| VV-THR-03 | zero tare | K = 1/3285, tare 0, ±1000 N | (−3 284 999, 3 284 999) |
| VV-LIM | SW load limit | trip 1961.33 N, warning 90 %, hysteresis 2 % | on ≥ 1765.197; off < 1725.9704; trip strict > |
| VV-M | SAF-SW-006 | levels 110 % / 100 % FS → margin 196.133 N | k·v·0.065 > margin ⇔ k·v > 3017.4308 N/s |
| VV-C-01 | approach | tol 2 N, k_est 50, v 1 mm/s, target 200 N, K = 1/3285, tare 125 000 | band 3.25 N, F_stop 196.75 N, raw exact 771 323.75 → raw_stop 771 323 (floor), cmp GE |
| VV-C-02 | approach, K < 0 | same with K = −1/3285 | raw exact −521 323.75 → −521 323 (ceil), cmp LE |
| VV-C-03 | band at trim speed | v 0.2 mm/s | band = tol = 2.0 N |
| VV-TC-01 | 160 → 800 | spm0 160, 10 mm commanded, D1 = 2.000 mm | N1 1600, spm1 800.0 → confirmation, not rejection |
| VV-TC-02 | near nominal | spm0 160, D1 9.98, D_tot 59.88 | N1 1600, spm1 160.3206…, N2 8016, spm2 160.5878…, consistency 0.2 % (no repeat warning) |
| VV-TC-03 | plausibility | D = 0 / −1; result 99.99 / 10 000.01; change 4.9 % / 5.1 % / 20.1 %; consistency 0.49 / 0.51 % | reject / reject; no confirm / confirm / confirm; no warn / warn |
| VV-SS-01…05 | steady state (ICD §7.6 mask) | TV-SS frames + one frame each with OVERRUN, PAUSED, POS_UNCERTAIN, NO_AFE_DATA, LINK_WDG; a setpoint change; 1 s window at 80 SPS with 63 / 64 usable frames; \|mean − target\| = tol | each flagged frame excluded; INCOMPLETE / complete; ON_TARGET |
| VV-SEQ-01 | nested loops | steps A B C D, loop B..C × 2 inside A..D × 2 | A B C B C D A B C B C D (12) with loop_iters |
| VV-SEQ-02 | until stopped | loop count 0 | infinite flag, one expansion |
| VV-SEQ-03 | invalid loops | overlap; 2 nesting levels; count 10 001 | ERROR |
| VV-SEQ-04 | durations | 10 mm at 2 mm/s, a 100 mm/s² (160 steps/mm) | 5.02 s; 50 mm at 10 mm/s: 5.10 s |
| VV-SEQ-05 [D-32] | load-step timeout | from x = 130 mm to soft max 290 mm at 2 mm/s, a 100 mm/s² | travel time 80.02 s (+ margin, value pending SWD-P1-18); the timeout is never shorter than this |
| VV-GEN-01 | staircase travel up | 0 → 10 mm, increment 2.5 | 0, 2.5, 5, 7.5, 10 |
| VV-GEN-02 | up-down | same | 0, 2.5, 5, 7.5, 10, 7.5, 5, 2.5, 0 (peak not repeated) |
| VV-GEN-03 | non-dividing increment | 0 → 10, increment 3 | 0, 3, 6, 9 |
| VV-GEN-04 | count variant | 0 → 10, count 5 | 0, 2.5, 5, 7.5, 10 (semantics to confirm, SWD-P1-12) |
| VV-GEN-05 | return to zero | 0 → 10 inc 5, return_to_zero | 0, 5, 0, 10 (order to confirm, SWD-P1-12) |
| VV-GEN-06 | cyclic | lo 1, hi 5, 3 cycles | steps [1, 5] + loop × 3 → 1 5 1 5 1 5 |
| VV-GEN-07 | invalid | increment 0, −1, > span | ValueError |
| VV-PATH | planned path | x0 = 0, F0 = 0, k_est 50, pull_dir +1: TRAVEL 2; LOAD 200 N; HOLD; TRAVEL 0; HOME | (2, 100), (4, 200), (4, 200), (0, 0), break |

---

## 5. Fault-injection scenarios (simulator; the same vocabulary for the twin where it exists)

Means:
- `SimControl.act(...)`, using the twin vocabulary of `00_System/tools/README.md`;
- `LinkModel` (loss, duplicate, corrupt, latency, unplug);
- `inject_nack` / `inject_store_mismatch`;
- `override_status` / `emit_event`, only for causes with no physical model;
- `Backend.test_hooks` (`fail_recorder`, `stall_thread`).

Every scenario asserts the wire (`wire_log`), the world (`query`), `status()` and the recording.

| FI | Injection | Expected SW reaction | TCs |
|---|---|---|---|
| FI-01 | Dropped DATA frames (`afe drop_every n`, LinkModel loss, `tx_congestion`) | gaps counted (FW vs link); no STOP below a 500 ms gap; recording marks `seq_lost` | IF-007-01, PLT-003-02, ACQ-004-01, SAF-SW-003-04 |
| FI-02 | Dropped response for a chosen command (deterministic hook, SWD-P1-09) | RETRY / CONFIRM / ONCE / VERIFY rules; motion never re-sent | IF-005-01/02, STOP-002-01 |
| FI-03 | Duplicated / re-ordered frames (response; DATA with the same `frame_seq`) | duplicate response ignored; a duplicate DATA is not recorded twice and not counted as 65 535 losses (SWD-P1-12) | IF-005-03, ACQ-002-01 |
| FI-04 | Corrupted bytes / CRC flips, both directions | dropped + counted; a lost PC→FW command follows its class | IF-004-02, PLT-003-02 |
| FI-05 | Noise bursts, sync pattern in a payload, truncated frame + idle | parser resync per the `streams` vectors | IF-003-01 |
| FI-06 | Link loss during motion (unplug / `link_silence` 600 ms and 1.5 s) | SW STOP after 500 ms; sim LINK_WDG at 1 s; reconnect without restart; travel-cal restore at reconnect | SAF-SW-003-02, CAL-001-03 |
| FI-07 | PC-side hang (`stall_thread` pipeline / reader / runner) | heartbeat stops → FW watchdog; liveness STOP while moving | SAF-SW-003-03 |
| FI-08 | Stream gap from AFE stall (fallback frames 10 Hz, NO_AFE_DATA) | AFE stale indicator; load input invalid → STOP while moving; captures aborted; fallback rows excluded | SAF-SW-001-02, SAF-SW-005-01 |
| FI-09 | ADC saturation (`afe saturate pos/neg`) | FW LOAD_LIMIT; SW ±∞; calibration point invalid; tare refused | SAF-SW-001-03, CAL-006-02, TARE-003-01 |
| FI-10 | AFE stale while idle | motion refused; FAULT_CLEAR effective only after fresh samples | SAF-SW-005-01 |
| FI-11 | AFE rate mismatch (`rate_error 0.1`) | indicator; frames excluded from windows; sequence-start WARN | SAF-SW-005-01, REP-002-01 |
| FI-12 | Spikes (p = 1e-3 / 3 %) | MAD rejection; > 2 % → point invalid | CAL-006-02, TARE-003-01 |
| FI-13 | Drift (+200 counts/min) | drift rule rejects the tare / point | CAL-006-02, TARE-003-01 |
| FI-14 | **K1 weld**: `estop open` with `drv_power` kept on > 200 ms | ESTOP handling + K1_WELDED indicator with clear hint; FAULT_CLEAR refused while the cause persists | SAF-SW-005-01, STOP-003-01 |
| FI-15 | **DRV_PWR loss** with the E-stop closed, during a jog and a sequence | DRIVER_POWER 0 → operation terminated, HOMED lost, gates REFUSE DRV_UNPOWERED; after return: hint "Enable, then Home", no automatic enable | STOP-003-01, SEQ-005-01 |
| FI-16 | **ALM** active (powered), idle and during a move | indicator "new motion blocked"; running move completes; new motion / sequence start refused | SAF-SW-005-01, SEQ-005-01, SEQ-007-03 |
| FI-17 | **PAUSE** (button / PC) during a manual move, jog, travel/load step (approach, trim, settle, capture, hold, home), travel wizard, load-cal capture, tare; plus the in-flight races | D-30/D-31 behaviour of §3.8 | STOP-004-01…13 |
| FI-18 | **HALT** (Pause/Break key / PC; v0.3: no STOP button, CR-01) in the same states; HALT just after a Resume | operation terminated; HALT never cleared by RESUME / reconnect | STOP-002-01/02/04, STOP-003-01/02, STOP-004-07/08 |
| FI-19 | **E-stop** in the same states, then the clear procedure | terminated; C-03; ENABLE + HOME needed; no restart | STOP-003-01, SAF-SW-004-01 |
| FI-20 | Limit switch during a move; both limits (wiring) | stop handled; direction-aware gate; LIMIT_WIRING indicator; clear once not both active, the remaining input acts as a limit latch (D-40 a) | SAF-SW-005-01, LIM-001-01 |
| FI-21 | FW load-limit trip (specimen overload past the FW threshold with the SW limits off) | FAULT_SET LOAD_LIMIT → terminated; clear + unload allowed | SAF-SW-005-01 |
| FI-22 | Step fault (`inject step_fault`) | HOMED lost, operation terminated | SAF-SW-005-01 |
| FI-23 | Board reset (`reset pin/iwdg`) mid-sequence | EVENT BOOT → new time epoch, sequence terminated, no re-enable, threshold rewrite, restore-pending check | SAF-SW-002-02, CAL-001-03 |
| FI-24 | NACK / BUSY / store mismatch on SET_PARAM | per-row statuses; threshold FAILED | CFG-003-01, SAF-SW-002-03 |
| FI-25 | **Disk full / write error** during recording; recorder stall | REC_FAILURE, controlled stop of the sequence, counted losses | ACQ-004-02/03/04 |
| FI-26 | Version / hash mismatch | read-only states | IF-008-01/02 |
| FI-27 | EVENT loss (event queue overflow → EVENT SEQ gap) | GET_STATUS + resync; no wrong state | IF-007-01 (extension) |
| FI-28 | Specimen break / slip (`specimen f_break`, bilinear yield); unreachable load (too-soft spring, D-32) | guards BREAK_DETECTED / SLIP; unreachable → travel to the soft limit, NOT_REACHED, sequence stops, no early timeout | SEQ-007-01, SEQ-007-04, SEQ-006-04 |
| FI-29 | GUI stall (no `gui_beat` > 2 s) while jogging | jog refresh stops; FW dead-man stops | MAN-004-01 |
| FI-30 | NVM flash stall (response of SAVE / LOAD / DEFAULTS delayed 0.6 s, D-37 a) | only stop-class frames while outstanding | CFG-004-02 |
| FI-31 | Link latency 8 ms + lost STOP / HALT request while a MOVE_ABS / HALT_CLEAR is in flight (OBS-M1-R1, D-37 d) | confirmation only from frames produced after the FW received the command | STOP-001-04, STOP-002-04 |
| FI-32 | Feature bit 0 with its status bits set (F-board, D-37 b) | bits shown UNKNOWN, no gate items from them | SAF-SW-005-03 |

---

## 6. Performance runs and demonstrations

### 6.1 Reference-PC runs (acceptance only on REF; DEV results are informative)

| Run | TCs | Duration | Needs |
|---|---|---|---|
| PR-1 plot refresh | TC-NFR-001-01 (P-01), -03 (P-04) | 10 min each | real display, out-of-process sim, recording |
| PR-2 STOP latency | TC-NFR-002-01 (P-02) | 100 clicks | sim-server `wire_log` (TCP) |
| PR-3 Pause/Break latency | TC-NFR-003-01 (P-03), TC-SW-STOP-002-01 | 100 presses | interactive desktop, `SendInput`, another application focused |
| PR-4 soak | TC-NFR-004-01 (P-05), TC-SW-ACQ-002-02 | 1 h | recording on, memory sampler (ctypes `GetProcessMemoryInfo`) |
| PR-5 SW-limit latency on REF | TC-SAF-SW-001-01 (rt) | 100 trips | — |

### 6.2 Demonstrations with the PO (results signed in the test report)

| DM | Item | Req |
|---|---|---|
| DM-01 | Full workflow against the simulator: connect → enable → home → travel cal → load cal → tare → sequence → report (later on hardware) | SYS-008 |
| DM-02 | Fresh install on Windows 10 from the requirements file | SW-PLT-001 |
| DM-03 | Floating plot on a second monitor, restart → layout restored; squeezed toolbar keeps STOP; 125 % / 150 % DPI | SW-RT-001, SW-STOP-001 |
| DM-04 | Time view / X-Y view during a sequence; readout states | SW-RT-003, SW-RT-005 |
| DM-05 | NVM buttons (Save / Reload / Defaults with confirmation, CFG chip) | SW-CFG-004 |
| DM-06 | Physical Pause/Break key with another application focused; elevated window (KL-01) | SW-STOP-002 |
| DM-07 | Manual tab: enable/disable, HOME, test zero, VALID | SW-MAN-006 |
| DM-08 | Sequence chart: planned path, labels, live marker smoothness | SW-SCH-001/002 |
| DM-09 | Pause / Resume, and the Resume refusal with HALT latched ("Clear stop first") [D-31] | SW-STOP-004 |
| DM-10 | HTML report opened in a browser | SW-REP-001 |

---

## 7. Entry / exit criteria and verdict

### 7.1 Milestones

| MS | SW scope (SRS MS column) | Entry | Exit (all required) |
|---|---|---|---|
| **M1** | SYS-003, SYS-008 (sim + twin subset), SYS-010, IF-001…012, SW-PLT-001…003, SW-CFG-001…004, SW-ACQ-001; NFR-002/004 backend parts; perf smoke | P1 gate passed; ICD version frozen for M1 (with RESUME, D-31) and vectors regenerated (`--check` exit 0); B's WP-B0…B11 merged with `Implements:` tags; B's unit suite green incl. the check-vector replay; conditions C2, C3 met | All M1 TCs pass in the 3 gate runs (§2.3) with identical counts; no open S1/S2; coverage floor met for M1 modules; X subset (TC-SYS-008-02) passes, or is carried as a dated condition if the twin is late (C6); TC-SW-PLT-001-01 on PY311 or C5/SWD-P1-16 resolved |
| **M2** | (no SW requirement in the SRS MS column) — v0.3: early acceptance of §3.14, SW-RT-006 (M1 add-on, §3.6), the D-37 / CR-01 items | ICD v0.5 landed (`--check` exit 0); M2 entry items closed: D-37 a/b/d (TC-SW-CFG-004-02, TC-SAF-SW-005-03, TC-SW-STOP-001-04 / 002-04), CR-01 remnants (TC-IF-001-02, TC-SAF-SW-005-04), StopConfirmation shim removed (F-MC-4) | M1 suite re-run green ×3; §3.14 armed (`--arm M2`) and passing; SW-RT-006 TCs pass; no open S1/S2; X motion subset passes (regression for SYS-008) |
| **M3** | SAF-SW-001…005, SW-LIM, SW-META, SW-RT, SW-MAN, SW-STOP-001…003 (+ STOP-004 manual cases), SW-ACQ-002…004, SW-CAL, SW-TARE, NFR-001…004 | REF-PC named and specced (C5); GUI design v0.2 (C4); SRS v0.4 with the D-30/D-31 deltas (C1) | All M3 TCs pass ×3; PR-1…PR-5 met on REF; DM-02…07 and DM-09 witnessed; no open S1/S2 |
| **M4** | SAF-SW-006, SW-STOP-004, SW-SEQ, SW-WIZ, SW-SEQF, SW-SCH, SW-REP, SYS-008 full | the POS_UNCERTAIN decision (SWD-P1-04) made | All M4 TCs pass ×3; DM-01, DM-08, DM-10 witnessed; full traceability (TC-SYS-010-01) |
| **HW gate** | IF-002 (target), NFR-003 (PUL), NFR-004 (VCP) | PO approval (D-06/D-07) | H cases signed jointly with Validator E |

Defect severity:
- **S1** safety: motion when it must not happen; a stop, latch or watchdog ineffective; HALT/PAUSED cleared without an operator action; wrong data marked VALID.
- **S2** wrong result or data loss: calculation, calibration, recording, report.
- **S3** function unavailable, with a workaround.
- **S4** cosmetic.

Defect IDs are `SWD-Mx-nn` (severity, file:line, evidence, fix hint, addressee). Every fixed defect gets a regression test tagged `req` and `defect("SWD-…")`. Safety tests are never quarantined.

### 7.2 Verdict format (test report, per milestone)

```
Verdict M<n>: ACCEPTED | ACCEPTED WITH CONDITIONS | NOT ACCEPTED
Scope: <req list>  · SW commit/tree: <hash>  · ICD <v>, dict hash <0x…>, vectors <n>
Runs: #1 fixed: P/F/S/X counts · #2 seed 12345 · #3 seed <s> · implementer suites: counts
Coverage: calc/io/core/gui line/branch  · REF-PC results (PR-1…5) · DMs witnessed
Open defects: SWD-Mn-nn (Sx, addressee, due) …
Conditions (only for ACCEPTED WITH CONDITIONS): Cn — text, owner, due milestone
```

- **ACCEPTED**: every in-scope TC passes in all runs and there is no open defect above S4.
- **ACCEPTED WITH CONDITIONS**: no open S1/S2; the open S3/S4 items or deferred environment items (twin, REF-PC) are listed as dated conditions; no safety TC is failing or deferred.
- **NOT ACCEPTED**: any S1/S2 open, any safety TC failing or not run, or run-to-run differences.

---

## 8. P1 design review findings (SW_design v0.2, SW_design_GUI v0.1, ICD v0.3 vs SRS v0.3 + D-30/D-31)

Severity: **High** = a requirement is not testable or not verifiable as specified, or a safety semantic is contradictory · **Medium** = an ambiguous acceptance criterion, or a hook needed before the milestone · **Low** = consistency or editorial.

| ID | Sev | To | Finding | Proposed resolution |
|---|---|---|---|---|
| SWD-P1-01 | High | Orchestrator | **SRS v0.3 contradicts D-30/D-31.** These texts still say "PAUSED clears on the next accepted motion command or HALT_CLEAR": §2 "PAUSE", §3.2 PAUSE row, SAF-FW-023, FW-MOT-007, SW-STOP-004. SAF-FW-020 has no PAUSED refusal. IF-012 lacks RESUME. The SW-STOP-004 acceptance criterion ("the accepted motion command clears PAUSED") cannot pass under D-30/D-31. | SRS v0.4 (ICD SD-13 + D-31). New SW-STOP-004 AC: "GUI Pause sends PAUSE; while PAUSED no motion command is accepted; Resume sends RESUME, then the step's absolute target; Resume is refused while HALT/ESTOP/fault is latched; a STOP-button HALT latched before the RESUME frame is never cleared by it". This plan already tests that (§3.8). |
| SWD-P1-02 | High | Integrator, B | **D-31 is not yet in the ICD or the design.** ICD v0.3 / `protocol.yaml` have no RESUME (0x3C), no RESUME check vectors and no retry class for it. SW_design v0.2 still implements Resume as HALT_CLEAR (§4.7, §5.1, §5.5, §5.5.1, §10.5, the `resume`/`clear_stop` gates; F-B-30 mitigation). Undefined so far: (a) the retry class of RESUME (proposal: ONCE_PRIORITY, priority path); (b) the NACK E_STATE (HALT/ESTOP/FAULT) on RESUME as an expected outcome (sequence stays PAUSED or is terminated by the HALT, no retry, no re-issue); (c) Clear stop while a sequence is PAUSED (v0.2 refuses it locally; under D-31 HALT_CLEAR also clears PAUSED — keep the refusal or end the sequence). TC-SW-STOP-004-01/07/08/09/12 and TC-IF-005-02 depend on these. | ICD v0.4 + vectors (`resume_*` check vectors incl. HALT/ESTOP/FAULT latched and `paused_after`); SW_design v0.3 aligned; the simulator check implements RESUME before the M1 check-vector replay. |
| SWD-P1-03 | Medium | B | **Race handling must be specified as an SW outcome, not only a FW refusal.** A motion command or JOG refresh in flight when PAUSE is pressed gets NACK E_STATE (BLOCK PAUSED). §4.4/§10.5 must state: an expected outcome, no retry, no DEGRADED counting (3 consecutive refresh "failures"), the sequence stays PAUSED. | Add to §4.4.1 / §10.5; verified by TC-SW-STOP-004-05/06/10. |
| SWD-P1-04 | Medium | Orchestrator, B | **The steady-state mask can silently empty every window.** The SW_design §11 mask (ICD §7.6) excludes POS_UNCERTAIN (set by any immediate stop, kept until the next HOME, HOMED stays 1) and AFE_RATE_MISMATCH. Neither blocks sequence start (§10.3, SRS SW-SEQ-005). Consequence: after one manual STOP before a sequence, every window is INCOMPLETE with no data. SRS SW-REP-002 "no stop/fault flag" does not say which bits. | Either the `sequence_start` gate REFUSEs on POS_UNCERTAIN ("re-home") and WARNs on a rate mismatch, or bit 13 is removed from the mask (R4 §8.3 does not list it). The SRS references the ICD §7.6 mask. Decide before M4 (C7). |
| SWD-P1-05 | Medium | D | **SW_design_GUI v0.1 is still on SRS v0.2.** (a) The indicator bar lacks DRV_PWR, K1_WELDED, HOME_DRIFT, CLK_FALLBACK, NO_AFE_DATA and the no-specimen banner (SAF-SW-005 v0.3, SW-LIM-004). (b) The ALM chip says "no FW reaction" instead of "new motion blocked" (D-28). (c) G-04 covers C-01…C-09 but not C-10. (d) The PAUSED tooltip "Resume / next motion command" and Pause/Resume text are D-29a (D-30/D-31: RESUME, "Clear stop first"). (e) C-05 / G-26 use "160 / 1280" (SRS: 160 / 800). (f) The traceability counts 59 SW requirements (v0.3: 60, SW-LIM-004 missing). (g) The API contract is B v0.1 (B v0.2 §15.5 A-01…A-25). | GUI design v0.2 before M3 (C4); TC-SAF-SW-005-02 and TC-SW-LIM-004-03 depend on it. |
| SWD-P1-06 | Medium | Orchestrator | **Ambiguous acceptance criteria** (this plan's interpretation in brackets; please confirm in SRS v0.4). SAF-SW-005 "≤ 200 ms after the frame that carries it" [the carrier = DATA, EVENT or GET_STATUS response; for STATUS-only items the poll period adds up to 1 s while streaming]. NFR-001 "all channels enabled" [all `ChannelRegistry` channels in one time view]. NFR-004 "lost frames attributable to the SW" [async overflow + recorder rows_lost; FW = OVERRUN; link = gap without OVERRUN]. SW-CAL-005 "800 ± 1 nominal samples" [N = round(capture × measured rate) ± 1]. SW-SCH-002 "updated ≥ 10 Hz" [backend publish rate and GUI repaint]. SW-SEQ-003 "±1 frame of plan" [relative to the observed t_reached]. SW-ACQ-002 "every frame exactly once" [ground truth = simulator sent-frame log]. | Confirm or reword. |
| SWD-P1-07 | Low | B | SW_design v0.2 §9.3 shows "D-27 candidates 160 / 1280 steps/mm"; SRS SW-CAL-003 requires 160 / 800 (D-27). | Use 160 / 800. |
| SWD-P1-08 | Low | B | §10.3 start gate lists "homed and enabled, no latch" but not DRV_PWR, ALM (powered) and PAUSED explicitly, which SRS SW-SEQ-005 enumerates. They are partly implied: power loss → NOT_ENABLED; ALM is not a latch. | List them as gate items with texts (TC-SW-SEQ-005-01 expects one reason each). |
| SWD-P1-09 | Medium | B | **Validation hooks needed through the public API (condition C3, M1).** (a) A full `Backend` + simulator on a `LockstepSimClock` (§12.6 covers the sim and engines only). (b) `wire_log` also on `TcpTransport` / the sim server for REF runs. (c) The Reader receive stamp per DATA/EVENT frame (SAF-SW-001, SAF-SW-005 latency). (d) Deterministic per-command response drop / duplicate / delay (`LinkModel.drop_next(cmd, n)`), beyond random loss. (e) The simulator sent-frame log (frame_seq, t_us) as ground truth. (f) Scheduling a world action relative to a wire event ("press PAUSE/STOP at arrival of the next MOVE_ABS/RESUME frame + Δ") for the D-30/D-31 races. (g) A `fail_recorder` that accepts any `OSError` (ENOSPC). (h) A free-space probe override. | Extend `SimControl` / `test_hooks` (SW_design §12.4, §19). |
| SWD-P1-10 | Medium | Integrator | **FW twin not yet available**, and the sim/twin world vocabulary is not frozen (F-B-06). The SYS-008 AC needs "SW⇄twin integration tests pass". Every C case that relies on FW behaviour (D-30/D-31 refusals, latches) is only as good as B's simulator until X runs. | Twin + `test_sim_vs_twin.py` with the X subset of §3 (TC-SYS-008-02, STOP-004-06/08, IF-005-02, IF-011-01) by M1 exit, or a dated condition (C6). |
| SWD-P1-11 | Low | B | Jog refresh "every 100 ms" on a 5 ms Supervisor tick can exceed the SRS SW-MAN-004 "refresh ≤ 100 ms" through jitter. | Schedule at 80–90 ms; TC-SW-MAN-004-01 checks max ≤ 100 ms. |
| SWD-P1-12 | Low | B | Unspecified behaviour needed for vectors: (a) SG force-rate across a frame gap / missed conversion (NaN or a t_us-based OLS?); (b) a duplicated or non-advancing `frame_seq` (must not count 65 535 losses or record twice); (c) staircase `count` semantics (levels incl. start?) and the `return_to_zero` step order. | Define in §7.4, §7.3, §10.6; VV-D-03, VV-GEN-04/05 are pending this. |
| SWD-P1-13 | Low | Researcher / Orchestrator | R4 §12 TV-L asserts a 22-byte DATA frame (1.9 %); ICD v0.3 has 26 bytes (2.26 %). R4 §11 HX711 S ≈ 2190 counts/N (2 mV/V) vs SRS A-02 3.0 mV/V (3285 counts/N). | R4 errata note; validation uses the ICD/SRS values. |
| SWD-P1-14 | Low | Orchestrator, B | ALM becoming active **during** a sequence: the running move completes (SAF-FW-026), but the next step's command is refused. The SRS does not say how the sequence ends. | Proposal: the sequence ends STOPPED, reason "driver alarm", no retry (TC-SW-SEQ-007-03). |
| SWD-P1-15 | Low | B, Integrator | The default simulator scenario offset is 125 000 counts (1.94 % FS), above the 1 % FS zero-balance allowance behind the FW range (A-02). Every default session therefore clamps the 110 % FS threshold (VV-THR-01). This is correct behaviour, but it masks the unclamped path in casual runs. | Keep a clamping scenario explicitly; default to an offset ≤ 64 425 counts. |
| SWD-P1-16 | Low | B, Orchestrator | (a) `pyproject.toml` does not register the markers `winint` (used by the GUI design), `validation` and `rt`; with `--strict-markers` collection fails. (b) SW-PLT-001 "Python ≥ 3.11" is not testable on this PC (only 3.14 is installed). (c) `03_SW/requirements*.txt` is still to be created (F-B-31, D-31). | Register the markers; provide a 3.11 interpreter or amend the AC to "3.14 verified, 3.11 by inspection"; create the requirements files in WP-B0. |
| SWD-P1-17 | Medium | Orchestrator / PO | **Reference PC undefined** (SRS A-10). NFR-001…004 acceptance is "on the reference PC" and needs an interactive Windows session with a real display and keyboard (PR-1…PR-4, W cases). | Name and spec the REF-PC and grant an operator session before the M3 gate (C5). |
| SWD-P1-18 | Medium | Orchestrator, B | **D-32 (load step not reaching its target) is not yet in the SRS or SW_design v0.2.** SRS SW-SEQ-006/007 still say "timeout 0 = 3× planned + 10 s" and do not define NOT_REACHED. SW_design §10.4 still uses "bound = SW travel limit or the step's `travel_bound_mm`, whichever is nearer", MOVE_DONE BOUND → step error `LOAD_NOT_REACHED`, and §10.5 `TIMEOUT` = 3× planned + 10 s. Open points: (a) the timeout margin value; (b) whether an **enabled SW travel limit** (SW-LIM-001, SAF-SW-001 predictive travel trip) or the step's `travel_bound_mm` narrows the D-32 bound — otherwise the approach trips the SW limit (STOP + SW_LIMIT) before reaching the soft limit, and the step ends as a trip instead of NOT_REACHED; (c) the report status name (NOT_REACHED vs LOAD_NOT_REACHED). | SRS v0.4: bound = soft limit in the step direction, narrowed by an enabled SW travel limit (proposal); timeout = travel time to the bound + margin (e.g. max(10 s, 20 %)); status NOT_REACHED; the sequence stops. SW_design v0.3 §10.4/§10.5 aligned. Verified by TC-SW-SEQ-006-02/-04 and TC-SW-SEQ-007-04. |

Closed during this review (no action): no-specimen thresholds keep the calibrated values (SW_design v0.2 §6.3 now matches SW-LIM-004); STATUS 86 B and `home.ref_switch` removal are reflected in SW_design v0.2; the ICD v0.3 BLOCK PAUSED (D-30) and the 486 check vectors are consistent (`--check` exit 0, tools tests 758/758).

---

## 8a. M2-entry review (v0.3, 2026-10-04)

Validation-suite baseline at the start of this revision (ICD v0.5 regenerated, B's M2 work in progress): 8 of 379
tests failed — 4 were F's own hard-coded ICD v0.4.1 facts (48 parameters, hash 0xF0376293; fixed: values now taken
from `params.yaml`), 2 the missing int32 saturation of `calc.motion` (ICD v0.5 §0.1), 2 the retired name
`IoBits.STOP_BTN` in `core/gates.py`. The new D-37 / CR-01 tests (§3.2–§3.8) failed at first against the then-current
backend (PING + GET_STATUS sent during an outstanding SAVE / LOAD / DEFAULTS; ALM / PEND / DRV_PWR / PAUSE_BTN shown
ON/OFF with the feature bit 0; a lost STOP / HALT confirmed by a frame produced before the FW executed the preceding
command — OBS-M1-R1; operator text "use the physical STOP / E-stop"). B's M2 work landed during the revision and F
re-ran them: **all pass now**; they stay as regression tests (no SWD number was raised because the items were within
B's announced M2-entry work, D-39).

| ID | Sev | To | Finding | Proposed resolution |
|---|---|---|---|---|
| OI-F-M2-01 | Low | Orchestrator | SRS v0.5.1 SW-RT-006 acceptance cites "D-63 rules (1)–(4)"; D-63 has five rules (5: a group tick ticks only the available children). D's design G-46 and this plan verify (1)–(5). | SRS v0.5.2: "(1)–(5)". |
| OI-F-M2-02 | Low | Orchestrator | CR-01 remnant: SRS SYS-002 still lists "physical STOP/PAUSE" among the FW-alone functions. | "physical PAUSE" (the E-stop is listed separately). |
| OI-F-M2-03 | Low | Integrator | CR-01 remnant: ICD §9.3 rationale of the VERIFY clears still says "HALT_CLEAR a new STOP-button HALT (button pressed and released ≥ io.release_ms …)". | Reword to a Pause/Break-key HALT (TC-IF-005-02 race now uses it). |
| OI-F-M2-04 | Low | B | The simulator answers SAVE_PARAMS at once (no flash stall); D-37 a is therefore exercised only with an injected response delay (FI-30), and sim and twin differ in timing. | Model the SAVE stall (response and buffered commands after ≈ `nvm_save_ms`), as the twin does (OBS-M1-02 fix). |
| OI-F-M2-05 | Info | B / F | The M2 motion API landed while this plan was written: an armed run (`--arm M2`) of the 18 pre-written §3.14 public-API cases already passes at this snapshot. They stay `pending("M2")` until B declares WP-B12 / M2 done; F arms them at the M2 verification. | — |
| OI-F-M2-06 | Info | Orchestrator / PO | TC-NFR-001-04 (4 panes) is informative on DEV; acceptance on the REF PC (MC-2 / G6 still open). | REF PC before M3 entry. |

## 9. P1 verdict on SW testability

**YES WITH CONDITIONS.** Every SW-*, SAF-SW-*, NFR-001…004 requirement and the SW side of IF-*, SYS-003, SYS-008 and SYS-010 has at least one test case with a measurable expected result. The oracles are available and verified:
- R4 §12: 22/22 against F's independent implementation, 3 runs;
- shared vectors: `--check` exit 0;
- tools tests: 758/758 pass.

The design provides the essential hooks (`wire_log`, `SimControl`, check-vector replay, `test_hooks`, out-of-process simulator).

Conditions:

| C | Condition | Owner | Due |
|---|---|---|---|
| C1 | SRS v0.4 carries the D-30/D-31 deltas and restates the SW-STOP-004 / SAF-FW-023 / IF-012 acceptance criteria (SWD-P1-01) and the D-32 load-step bound / timeout / NOT_REACHED rules of SW-SEQ-006/007 (SWD-P1-18); the ambiguous criteria of SWD-P1-06 are confirmed | Orchestrator | before M1 TCs are frozen (M3 for SW-STOP-004) |
| C2 | ICD v0.4 with RESUME 0x3C + vectors; SW_design v0.3 aligned to D-31 incl. the RESUME retry class, refusal handling and race outcomes (SWD-P1-02, -03) | Integrator, B | M1 entry |
| C3 | Validation hooks of SWD-P1-09 available through the public API | B | M1 entry |
| C4 | SW_design_GUI v0.2 aligned to SRS v0.3/v0.4 and B v0.2 (SWD-P1-05) | D | M3 entry |
| C5 | Reference PC named and specced with an interactive session; Python 3.11 lower bound resolved (SWD-P1-16, -17) | Orchestrator / PO | M3 entry (3.11: M1 exit) |
| C6 | FW host twin and the sim-vs-twin X subset available (SWD-P1-10) | Integrator, A | M1 exit, else a dated condition |
| C7 | Steady-state mask vs POS_UNCERTAIN / rate mismatch decided (SWD-P1-04) | Orchestrator, B | M4 entry |

---

## 10. Coverage summary

| Group | Requirements | TCs | Of which REF / TGT / D |
|---|---|---|---|
| SYS (SW side: 003, 008, 010) | 3 | 9 | 1 D |
| SAF-SW-001…006 | 6 | 20 | SAF-SW-001-01 also on REF |
| IF-001…012 (SW side) | 12 | 21 | IF-002-02 TGT |
| SW-PLT / SW-CFG | 7 | 13 | PLT-001 REF, CFG-004 D |
| SW-LIM / SW-META | 6 | 8 | — |
| SW-RT | 6 | 21 | RT-001-02, RT-003, RT-005 D |
| SW-MAN | 6 | 10 | MAN-006 D |
| SW-STOP | 4 | 23 | STOP-002-01 W (REF), STOP-002-03 D |
| SW-ACQ | 4 | 9 | ACQ-002-02 REF |
| SW-CAL / SW-TARE | 12 | 20 | — |
| SW-SEQ / SW-WIZ / SW-SEQF / SW-SCH | 12 | 22 | SCH-001-02, SCH-002-02 D |
| SW-REP | 4 | 6 | REP-001-02 D |
| NFR-001…004 | 4 | 11 | 7 REF, 2 TGT |
| **Total** | **86** | **193** (v0.2: 167) | REF-dependent 11, TGT 3, D 10 |

Every requirement in scope is covered (counts = distinct `TC-<REQ>-nn` ids of this plan, computed 2026-10-04).

## 10a. M1 execution corrections (v0.2)

| # | TC | Correction | Reason |
|---|---|---|---|
| M1-C1 | TC-IF-008-01/-02 | Version / hash mismatches are presented by F's own **F-board** (`tests/validation/oracle/fboard.py`: ref_codec + `params.yaml`, TCP, real clock) instead of the simulator; "minor lower" replaced by "minor higher → compatible" | the simulator cannot present another INFO (hook gap, cf. SW-C-M1-01); PROTO_MINOR of the SW is 0 |
| M1-C2 | TC-IF-007-01, TC-SW-PLT-003-02, FI-03 | Link-attributed DATA gaps, duplicate DATA frames, the u16 wrap, over-long and truncated frames come from the F-board (`skip_every`, `dup_every`, `seq_start`, `inject_raw`) | the vocabulary has per-command response faults only, no per-DATA-frame link loss / duplicate |
| M1-C3 | TC-IF-011-01 | "4 outstanding commands" replaced by "job queue + lane busy + empty token bucket"; RESUME and the clears are checked separately against SRS IF-011 (found delayed: SWD-M1-04) | the Worker runs board-waiting jobs one at a time, so ≤ 3 requests are outstanding through the public API |
| M1-C4 | TC-SW-PLT-001-01 | Python 3.11 lower bound dropped; inspection + offscreen smoke on DEV, fresh install demonstrated on REF (DM-02, PO) | D-33 i (runtime 3.14 only), SRS v0.4.1 SW-PLT-001 |
| M1-C5 | TC-SW-CFG-003-01 (BUSY), TC-SW-ACQ-001-01 (stream stop while moving), SWD-M1-02 | Motion is produced on the **forced path** (`Device` ENABLE / HOME / MOVE_ABS, harness `forced_*`) — never used for an expected value | the M1 API cannot move (motion is M2); same pattern as TC-SW-STOP-004-04 |
| M1-C6 | TC-SAF-SW-003-01 | M1 runs a 120 s lock-step part (gap ≤ 250 ms, PING after ≥ 150 ms idle) + the pipeline-stall check; the 10 min rt run stays M3 | WP-B6 heartbeat is M1 code |
| M1-C7 | all | Open defects are kept as `xfail(strict=True)` tests marked `defect("SWD-M1-nn")`; a fix turns them into XPASS = failure, then the marker is removed (regression test) | §7.1 rule "every fixed defect gets a regression test" |
| M1-C8 | TC-IF-011-01 (RESUME) | RESUME stays on the CONTROL lane (Orchestrator decision; SRS IF-011 wording to be corrected in v0.5): expected = submitted at once, never behind Worker jobs, out within one token interval (≤ 12 ms) with busy queues; the clears are checked as priority frames (written at the call instant) | Orchestrator message at the M1 re-test |

Validation suite layout (M1): `tests/validation/{conftest.py, harness.py, oracle/{f_ref.py, fboard.py}, test_v_protocol.py (P), test_v_connect.py, test_v_config.py, test_v_link.py, test_v_stream.py (C), test_v_gui.py (G), test_v_twin.py (X), test_v_static.py (U/I)}`; `_reports/trace.json` (req → node → outcome), `_reports/processes.log` (every process F started / stopped, by PID).

## 10b. v0.3 corrections and suite changes

| # | TC | Change | Reason |
|---|---|---|---|
| M2-C1 | TC-SAF-SW-005-01, TC-SW-STOP-002-02, -003-01, -003-02, -004-08, FI-18, TC-IF-005-02 (race) | physical STOP-button stimuli removed or replaced (Pause/Break-key HALT, E-stop); HALT source PC only; HALT_CLEAR never refused with E_CAUSE_ACTIVE; banner text "use the red E-stop" | CR-01 / D-36, ICD v0.5 |
| M2-C2 | TC-SW-CFG-004-02, TC-SAF-SW-005-03/-04, TC-SW-STOP-001-04, TC-SW-STOP-002-04, TC-IF-001-02 | new | D-37 a, b, d; CR-01 remnants; OBS-M1-R1 |
| M2-C3 | TC-IF-011-01 | priority list = STOP / HALT / PAUSE + clears; RESUME on the CONTROL lane | D-37 c, SRS v0.5 IF-011 |
| M2-C4 | TC-SYS-003-01 | + int32 saturation vectors; F's oracle `f_ref.sat_i32`, `rate_cap_um_s` | ICD v0.5 §0.1, OBS-M1-05 |
| M2-C5 | TC-IF-010-01, TC-IF-005-01/03, TC-IF-004-02 | parameter count and dictionary hash taken from `params.yaml` (oracle), no literals | dict_version 4 (47 params, 0xFCC54C90) |
| M2-C6 | TC-IF-005-02 (M1 part "no motion from the M1 API") | retired | motion exists in M2; superseded by TC-SW-MAN-006-02 and TC-SYS-008-04 (h) |
| M2-C7 | TC-SW-RT-006-01…14, TC-NFR-001-04 | new | SW-RT-006 (D-38), GUI design v0.4 §4.7 |
| M2-C8 | §3.14 | M2 SW early-acceptance set; simulator-fidelity TC-SYS-008-04/05/06; `pending("M2")` mechanism (§2.4a) | D-39, WP-B12 |

Validation suite (v0.3): `test_v_d37.py` (C: D-37 a/b/d), `test_v_motion.py` (C: simulator motion + WP-B12 items
now; MotionController / gates / hotkey pending M2), `test_v_plots.py` (G: SW-RT-006, NFR-001 4-pane smoke),
additions to `test_v_protocol.py` (units saturation, `motion_vectors.json`), `test_v_static.py` (retired names,
CR-01 texts), `test_v_link.py` (D-34 race with the Pause/Break key). `oracle/f_ref.py`: int32 saturation, rate cap;
`oracle/fboard.py`: selectable feature mask and STATUS `io`; `harness.py`: forced request / outcome from the wire,
M2 verbs, `until()`; `conftest.py`: `--arm`, `pending` marker, TC id in `trace.json`.

## 11. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-03 | Validator F | First plan for the P1 gate: strategy and levels, 167 TCs for 85 requirements, R4 + VV vectors (R4 §12 independently verified 22/22 × 3 runs), 29 fault-injection scenarios, REF runs PR-1…5, demonstrations DM-01…10, milestone criteria, findings SWD-P1-01…18, P1 verdict YES WITH CONDITIONS (C1…C7). Includes D-30, D-31 (RESUME 0x3C) and D-32 (load step travels to the soft limit, NOT_REACHED) per Orchestrator messages. |
| 0.3 | 2026-10-04 | Validator F | M2 plan: baseline SRS v0.5.1 / ICD v0.5 (dict 4, 47 params, 0xFCC54C90) / D-36…D-40 / GUI design v0.4; CR-01 (physical STOP-button cases removed, HALT source PC only), D-37 a–d TCs incl. OBS-M1-R1, SW-RT-006 TCs (14 + NFR-001 4-pane smoke), §3.14 M2 SW early acceptance (simulator fidelity incl. WP-B12, MotionController, gates, hotkey), pre-written tests with `pending` / `--arm` (§2.4a), FI-30…32, D-40 (LIMIT_WIRING clear rule, TC-SYS-008-04 (i)), §8a M2-entry review (OI-F-M2-01…06), §10b corrections M2-C1…C8; 86 requirements → 193 TCs. |
| 0.2 | 2026-10-03 | Validator F | M1 execution: baseline SRS/ICD v0.4.1, dict 3, SW_design v0.3.2, GUI v0.3.1, twin; TC corrections M1-C1…C7 (§10a: F-board for IF-008 / link-attributed gaps / duplicates / frame errors, IF-011 queue condition, Python 3.14 only, forced motion path, heartbeat lock-step part, strict-xfail open defects, M1-C8 RESUME on the CONTROL lane); validation suite layout. Results: `SW_test_report_M1.md` (incl. re-test). |
