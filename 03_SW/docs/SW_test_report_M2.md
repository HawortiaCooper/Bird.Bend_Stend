# Bird Bend Stand — PC Software Test Report, milestone M2 (SW_test_report_M2)

| Doc | SW_test_report_M2 |
|---|---|
| Version | **1.0 — M2 gate verification** |
| Date | 2026-10-04 |
| Author | Validator F — SW |
| Plan | `03_SW/docs/SW_test_plan.md` **v0.3.1** (§3.14 M2 SW early acceptance armed, §2.4a, §10b M2-C1…C9) |
| Baseline | HEAD **099af88** (M2 implementation, ICD v0.6) + working tree with D's GUI alignment B4-01…11 / D-41 (SW_design_GUI v0.4.1); SRS v0.5.2; ICD **v0.6** (PROTO 1.0, PAYLOAD 1, dict v4, 47 params, PARAM_DICT_HASH 0xFCC54C90; vectors: protocol, check, units incl. saturation, `motion_vectors.json`, `loadlim_vectors.json`); SW_design **v0.4** (§22a, §15.5d B4-01…11); D-01…**D-42** |
| SW tree | `03_SW/src/**` unchanged by F. Source fingerprint (md5 over all `03_SW/src/**/*.py`) identical at the start and the end of runs 1–3 + coverage + trace run. |
| Environment | DEV: Windows 10 Pro 19045, Python 3.14 (`.venv`), PySide6 offscreen, pytest 9.1.1 + qt / cov / randomly; `BEND_STAND_HOTKEY=off` (root conftest; no global hotkey registered by tests, one F test uses the fake backend). D-06 respected: no COM port opened. |

---

## 0. Verdict

```
Verdict M2: ACCEPTED WITH CONDITIONS
Scope: M2 SW early acceptance (plan §3.14): simulator motion fidelity incl. WP-B12 (TC-SYS-008-04…07),
       MotionController / motion gates / confirmations / hotkey (SW-MAN-002…006, SW-LIM-001, SAF-SW-004, SW-STOP-002
       C part), ThresholdManager M2 part (SAF-SW-002), M2-entry items D-37 a/b/d (incl. OBS-M1-R1), CR-01 remnants,
       F-MC-4; SW-RT-006 (M1 add-on, D-38); regression of the whole M1 scope
SW tree: 099af88 + working tree (GUI B4 alignment) · ICD 0.6 · dict 0xFCC54C90
Runs (full 03_SW/tests: unit 2030 + gui 188 + integration 60 + validation 482 = 2760):
   #1 fixed order        2756 passed, 4 skipped, 0 failed (454 s)
   #2 seed 12345         2756 passed, 4 skipped, 0 failed (467 s)
   #3 random seed        2755 passed, 4 skipped, 1 failed (548 s) — test_gui_sim_smoke_offscreen, see §4 OBS-M2-R1
   #4 random seed        2756 passed, 4 skipped, 0 failed (649 s) — added after #3 to separate a load effect
                         from an order effect; smoke test 16 / 16 in isolation
   skips = Integrator's M1 stop-timing cases (motion timing verified by its M2 twin cases)
Validation suite alone (trace run): 482 passed; + 4 tests added during the review (486), passed.
Coverage (unit + validation, branch on): calc 99.7 % line, core 94.8 % / 87.6 % branch, io 95.6 % / 90.8 %,
   io.sim 95.6 % / 90.6 %; core.link 96.5 %, core.gates 96.8 %, core.motion 89.1 %, core.safety 88.4 % (< 95 % floor
   of plan §2.3 — the uncovered lines are the M3 calibrated-threshold path; condition MC2-3)
Open defects: none above S4 (SWD-M2-01…03 are S4, §3)
Conditions: MC2-1 … MC2-6 (§5)
```

**Why ACCEPTED WITH CONDITIONS.** All in-scope test cases pass. That includes the 18 pre-written public-API M2 cases, now armed, and the D-37 / OBS-M1-R1 / CR-01 regression tests. No S1 or S2 is open. The single run-3 failure is a real-clock GUI smoke test. It did not reproduce in isolation (6 of 6 passed, plus the repeats in §2.1), and the frame counters of the failing run show 0 frames lost (§4). The open items are S4 texts and D-41 / CR-03 alignment that has not landed yet, the coverage floor of `core.safety` (M3 path), plus REF-PC and PO items.

---

## 1. What was verified

| Area | TCs (plan) | Result |
|---|---|---|
| Simulator motion vs ICD / SRS §3.2 (forced path) | TC-SYS-008-04 (a)–(i): MOVE_ABS + duration vs `plan_trapezoid`, JOG bound / dead-man, re-HOME, END limit + direction-aware latch, E-stop (stop ≤ 3 ms, DRIVER_POWER after k1 + 20 ms filter), DRV_PWR loss, PAUSE / RESUME, VERIFY with real motion, LIMIT_WIRING clear rule (D-40 a) | P (9/9) |
| WP-B12 simulator | TC-SYS-008-05: K1_WELDED in (k1, k1 + 25 ms] for k1 ∈ {100, 200, 2000} and never in the normal case; idle disable at 600 s ± 1 s, none when loaded; load regrow after FAULT_CLEAR | P (5/5) |
| Vector conformance | TC-SYS-008-06: `motion_vectors.json` ramp periods **exact**, planner, CLEAN / ISR / STRETCH; `loadlim_vectors.json` 12/12 through the simulator model; units incl. int32 saturation | P |
| D-41 preview | TC-SYS-008-07: `drv.pwr_sense_enable` = 0 (write + SAVE + REBOOT) → E-stop with the supply held: ESTOP_SET, no K1_WELDED, no DRIVER_POWER, DRV_PWR = 1 | P |
| MotionController (public API) | TC-SW-MAN-002-02 (absolute µm, round half away), -003-01 (11/12/13 and latest-wins 11/13 + pending shown), -003-02 (pending dropped on STOP / PAUSE), -004-01 ×3 (refresh ≤ 100 ms, JOG 0; dead-man without GUI beat; un-homed cap + JOG_NO_BOUND), -005-01 (caps refused, nothing sent), TC-SW-LIM-001-01 (target outside SW limits refused; jog bound = SW limit, MOVE_DONE BOUND) | P |
| VERIFY via the API | TC-IF-005-02 M2 part: MOVE_ABS response lost → one frame, DONE; request lost → one frame, NOT_EXECUTED; board reset during a move → CANCELLED, no automatic ENABLE / HOME / MOVE / JOG within 5 s | P (added in this review) |
| Motion gates | TC-SW-MAN-006-02 ×7 (NOT_ENABLED, NOT_HOMED, HALT, PAUSED, ESTOP, DRV_UNPOWERED, DRIVER_ALARM = generated BLOCK names; no MOVE_ABS on the wire); TC-SAF-SW-004-02 (DISABLE / HOME confirmations, HOME flags bit0) | P |
| Thresholds (SAF-SW-002 M2 part) | TC-SAF-SW-002-01 early (VV-THR-01…03 on `calibrated_target`), -05 (manual raw thresholds: H2 kept after every SET even when the new pair lies above the old one, GET_PARAM read-back, VERIFIED; store mismatch → FAILED → `move` gate REFUSE THRESHOLDS_UNVERIFIED, nothing sent), -06 (input rules) | P |
| Hotkey | TC-SW-STOP-002-02 (lock-step hook: HALT ≤ 1 ms, 3 lost → 4 frames 50 ms apart, attempts 4, HALT latched); TC-SW-STOP-002-05 (real clock, fake backend: status active, test-mode press measured and **no HALT**, real press → HALT ≤ 50 ms). Win32 part (W) and NFR-003 on the REF PC stay M3 | P (C part) |
| M2-entry items | D-37 a TC-SW-CFG-004-02 ×3 (only STOP / HALT / PAUSE during SAVE / LOAD / DEFAULTS); D-37 b TC-SAF-SW-005-03 (UNKNOWN on a missing feature, no gate item); D-37 d TC-SW-STOP-001-04 / -002-04 (**OBS-M1-R1**: pre-execution frames never confirm a lost STOP / HALT); CR-01 TC-IF-001-02, TC-SAF-SW-005-04; **F-MC-4** TC-SW-STOP-001-05 | P — OBS-M1-R1 and F-MC-4 **closed** |
| SW-RT-006 | TC-SW-RT-006-01…14 + TC-NFR-001-04 (DEV smoke, 4 panes + X-Y: p95 ≤ 50 ms) on D's GUI v0.4.1 state | P (15/15) |
| M1 regression | whole validation suite incl. the X subset against A's firmware in the twin | P |

### 1.1 Code review (B's M2 code vs SRS v0.5.2 / ICD v0.6)

| Module | Checked | Result |
|---|---|---|
| `core/device.py` (confirmation) | D-37 d: indication confirms only when `last_flags_ns > t_write` **and** its device time ≥ the FW-receive bound (lower envelope of host − device time + largest recent RTT) or ≥ a STATUS anchor that answers a request written after the command; serial-number compare (32-bit wrap safe) | conforms; OBS-M1-R1 closed (TC-SW-STOP-001-04 / -002-04 pass with 8 ms latency). Residual: the bound is conservative (RTT max over a window), so confirmation may come one frame later — acceptable, the repeat still works |
| `core/model.py` | `StopConfirmation` frozen dataclass, no str equality; no consumer compares with a str (grep) | **F-MC-4 closed** |
| `core/motion.py` | absolute targets only; latest-wins and pending drop (STOPPED / PAUSED / latches / BOOT / own STOP-HALT-PAUSE); VERIFY: never re-sent, GET_STATUS decides; jog refresh 80 ms gated by GUI beat ≤ 300 ms and epoch; un-homed cap; SW travel bound; caps / range refused locally | conforms. SWD-M2-03 (S4) below |
| `core/gates.py` | BLOCK-name items; DRV_UNPOWERED / DRIVER_ALARM only from **valid** bits (D-37 b); THRESHOLDS_UNVERIFIED; HOME CONFIRM when load unknown / > `home.max_load_raw`; DISABLE CONFIRM; PC_LOAD_LIMITS_OFF WARN until M3 | conforms. SWD-M2-01 (S4, D-41 texts) |
| `core/safety.py`, `calc/loadcal.py` | write plan keeps H2; read-back exact; any NACK / timeout / mismatch → FAILED; calibrated path rounds toward the tare and clamps inward (VV-THR-01…03 equal) | conforms. SWD-M2-02 (S4) below; calibrated path not yet used (M3) |
| `io/sim/board.py`, `io/sim/loadlim.py` | ramps = `ref_motion` (exact), EVENT order, homing phases, inputs + debounce, K1 timer, 20 ms DRV_PWR filter, idle disable, regrow window per ICD v0.6 §5.5 / D-40 d, CR-01 (STOP button retired, `button stop` refused), feature mask mirror | conforms. SAVE still answered at once (OI-F-M2-04 → MC2-4) |
| `io/win_hotkey.py` | copy of TS @37c87471: RegisterHotKey (MOD_NOREPEAT) + LL-hook fallback posting to its own thread, HALT on the hotkey thread, auto-repeat suppression, test mode fail-safe (a press after the window or with motion = real HALT), 250 ms liveness ping | conforms (Win32 path not executable offscreen; W on REF PC = M3) |

## 2. Evidence

### 2.1 Commands

```
QT_QPA_PLATFORM=offscreen  (BEND_STAND_HOTKEY=off from 03_SW/tests/conftest.py)
.venv\Scripts\python -m pytest 03_SW\tests -p no:randomly -q -rfEsxX                    (run 1)
.venv\Scripts\python -m pytest 03_SW\tests -p randomly --randomly-seed=12345 -q -rfEsxX   (run 2)
.venv\Scripts\python -m pytest 03_SW\tests -p randomly -q -rfEsxX                        (run 3, run 4 — seed in the log header)
.venv\Scripts\python -m pytest 03_SW\tests\unit 03_SW\tests\validation -p no:randomly --cov=bend_stand --cov-branch
                                --cov-config=03_SW\pyproject.toml                      (coverage: 2512 passed)
.venv\Scripts\python -m pytest 03_SW\tests\validation -p no:randomly                   (trace run: 482 passed → _reports/trace.json)
test_gui_sim_smoke_offscreen alone: 6 × pass; then 10 × in a loop (smoke10)
```

Logs: scratchpad `validator-f-sw/m2gate/run1…4.txt`, `cov.txt`, `val_trace.txt`, `smoke10.txt`, `fp_start/fp_end.txt`.


### 2.2 Run 4 / smoke repeats

* **Run 4** (random order, full `03_SW/tests`, collected before the last 4 additions): **2756 passed, 4 skipped, 0 failed**
  (649 s). Runs 1, 2 and 4 are identical; run 3 differs only by OBS-M2-R1.
* **`test_gui_sim_smoke_offscreen` repeated 10 × after run 4:** 10 / 10 passed (plus 6 / 6 earlier).
* The 4 tests added during the review (TC-IF-005-02 M2 part ×2, TC-SW-MAN-002-03, TC-SAF-SW-002-06) pass on their own
  and in the `test_v_motion.py` module run.
* Processes: `_reports/processes.log` — 197 started, 197 stopped / exited, each through its own handle and the PID
  recorded at start (simulator servers, GUI smokes, fw_twin engines). The two background pytest chains F stopped
  (a superseded run and a stuck shell aggregation) were F's own tasks, stopped through their task handles.

### 2.3 Validation suite changes for the gate

* Armed: the 11 `pending("M2")` functions (18 instances) of `test_v_motion.py` (markers removed, plan M2-C9).
* Added: TC-SYS-008-06 load-limit vectors (12), TC-SYS-008-07 (D-41), TC-SAF-SW-002-01 early (3), -05, -06,
  TC-SW-STOP-001-05 (F-MC-4), TC-SW-STOP-002-05 (fake hotkey, rt), TC-IF-005-02 M2 part (2), TC-SW-MAN-002-03.
* `_reports/trace.json` (trace run, before the last 4 additions): every entry has its plan TC id; no `pending` left.

## 3. Defects (code review)

| ID | Sev | To | Finding (file:line) | Fix hint |
|---|---|---|---|---|
| SWD-M2-01 | S4 | B (+ D for chips) | D-41 / D-42 (no contactor) not yet in the backend texts: `core/gates.py:35` ESTOP clear hint "press RESET (K1)", `:43-44` DRV_PWR / DRV_UNPOWERED "RESET on K1", `:41` K1_WELDED hint, `:175` estop-clear CONFIRM "button released and K1 reset". The GUI wording was aligned by D (v0.4.1). | Reword with CR-03 (ICD v0.7 default `drv.pwr_sense_enable` = 0): "release the red E-stop button, wait ≥ io.estop_release_ms, Clear E-stop, Enable, Home"; K1_WELDED / DRV_PWR texts only for the optional 48 V presence sense |
| SWD-M2-02 | S4 | B | `core/safety.py:78` `zero = round(tare_raw)` uses Python banker's rounding; the project rule is round half away from zero (SYS-003, `calc.rounding.round_half_away`). Only the M3 calibrated path is affected (x.5 tare means). | `round_half_away(tare_raw)` |
| SWD-M2-03 | S4 | B | `core/motion.py:367-373` VERIFY resolution after a lost MOVE_ABS response: "executed" only if STATUS shows MOVE_ABS / STOPPING with the same target. A short move that already finished **and** whose EVENT MOVE_DONE was lost (EVENT overflow) is reported NOT_EXECUTED although it ran (the axis is at the target). No hazard (nothing is re-sent; resync follows). | Also treat `motion IDLE and pos_um == target_um` (or `target_um == act.target_um`) as executed |

## 4. Observations

* **OBS-M2-R1 (run 3, `test_gui_sim_smoke_offscreen`).** `python -m bend_stand --sim` (real clock, in-process simulator) logged "LINK LOST" once and exited with the link DEGRADED. The frame counters show nothing missing: 682 frames OK, 617 DATA, lost FW 0, link 0, CRC 0. The interval p95 was 33.8 ms. Run 3 took 548 s against 454–467 s for runs 1–2, so the host was more loaded. Other roles were running suites in parallel. The behaviour fits a ≥ 1 s stall of the subprocess: no FW→PC frame was processed, LOST was set, and the recovery then passed through DEGRADED. The test passed 6/6 in isolation (see also §2.2). This is not an order effect: the test starts its own process and shares nothing with other tests. Action: the M3 REF-PC runs (PR-1, PR-4) record any LINK LOST event. If it appears without a measurable stall, it becomes a defect.
* The jog refresh-timeout path (`motion.py:596-609`) and the link-down cancel are covered only by B's unit tests (coverage gap in the validation run, not a defect).
* `core.safety` 88.4 % line coverage: the misses are the calibrated / INVALID paths (M3). TC-SAF-SW-002-06 was added for the input rules.

## 5. Conditions

| ID | Condition | Owner | Due |
|---|---|---|---|
| MC2-1 | CR-03 (D-41 / D-42): SRS v0.6 incl. my OI-F-M2-01 (SW-RT-006 "D-63 rules (1)–(5)") and OI-F-M2-02 (SYS-002 "physical PAUSE"); ICD v0.7 `drv.pwr_sense_enable` default 0; backend texts SWD-M2-01; F re-runs TC-SYS-008-07 with the new default and the C-03 text check | Orchestrator, C, B, F | M3 entry |
| MC2-2 | OI-F-M2-03: ICD §9.3 rationale of the VERIFY clears still describes a "STOP-button HALT" | C (Integrator) | ICD v0.7 |
| MC2-3 | `core.safety` ≥ 95 % line (plan §2.3 floor) once the calibrated threshold path is used (load calibration / tare, M3); SWD-M2-02 fixed | B, F | M3 gate |
| MC2-4 | OI-F-M2-04: the simulator answers SAVE / LOAD / DEFAULTS without the flash stall. D-37 a is exercised only with an injected delay (FI-30), so sim and twin timing differ. Model the stall (≈ `nvm_save_ms`, buffered commands answered after it) | B | M3 entry |
| MC2-5 | Hotkey Win32 path + NFR-003 on the REF PC (PR-3, DM-06) and SW-RT-006 demonstration **DM-11** (Plot 2 on the second monitor, layout restored after restart, 4-pane smoothness with the TC-NFR-001-04 numbers; D's request); REF PC still to be specified (MC-2 / G6) | Orchestrator / PO, F | M3 |
| MC2-6 | Carried from M1: F-MC-1 PO demonstrations DM-02 (fresh install), DM-05 (NVM buttons), SYS-008 GUI walk-through; X motion subset sim vs twin (TC-SYS-008-02) incl. the WP-B12 scenarios once A's M2 motion is fully in the twin (the Integrator's `test_twin_m2_motion.py` 9 cases pass in these runs) | PO, Integrator, F | M2 exit / M3 entry |

## 6. Items for other roles

* **B:** SWD-M2-01…03; MC2-3, MC2-4.
* **D:** none open. GUI 188/188 in every run; the K1 chip (`indicator_map`) applies only with the optional presence sense after CR-03.
* **Integrator:** OI-F-M2-03 (ICD §9.3 text).
* **Orchestrator / PO:** CR-03 SRS items (OI-F-M2-01/02), REF PC, DM-02 / 05 / 11.

## 7. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-10-04 | Validator F | M2 gate verification on 099af88 + GUI B4 alignment: §3.14 armed, 3 (+1) full runs, coverage, code review of the M2 backend; OBS-M1-R1 and F-MC-4 closed; SWD-M2-01…03 (S4); verdict ACCEPTED WITH CONDITIONS (MC2-1…6). |
