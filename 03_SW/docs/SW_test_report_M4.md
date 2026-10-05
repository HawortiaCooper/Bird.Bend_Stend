# Bird Bend Stand — PC Software Test Report, milestone M4 (SW_test_report_M4)

| Doc | SW_test_report_M4 |
|---|---|
| Version | **1.0 — M4 gate verification** |
| Date | 2026-10-05 |
| Author | Validator F — SW |
| Plan | `03_SW/docs/SW_test_plan.md` **v0.5** (M4 corrections §10d M4-C1…C7) |
| Baseline | HEAD **5daf3b8** (M4 implementation); SRS v0.6.3; ICD v0.7.4 (D-47 a); SW_design **v0.6** (§22c, §15.5f B6-01…21); SW_design_GUI **v0.6**; D-01…D-48 |
| SW tree | `03_SW/src/**` unchanged by F; the source fingerprint (md5 over `03_SW/src/**/*.py`) was identical before and after runs 1–3, the coverage run and the trace run |
| Environment | DEV: Windows 10 Pro 19045, Python 3.14 (`.venv`), PySide6 offscreen, pytest 9.1.1 + qt / cov / randomly; `BEND_STAND_HOTKEY=off`, `BEND_STAND_DATA_DIR` per test. D-06: no COM port opened. |

---

## 0. Verdict

```
Verdict M4: ACCEPTED WITH CONDITIONS
Scope: SW-SEQ-001…007, SW-WIZ-001/002, SW-SEQF-001, SW-SCH-001/002, SW-REP-001…004, SW-STOP-004 (sequence),
       SW-ACQ-004 (sequence part), SAF-SW-006 (per step); regression of M1–M3
Runs (full 03_SW/tests: unit 2263 + gui 297 + integration 168 + validation 612 = 3340):
   #1 fixed order          3339 passed, 1 xfailed (1042 s)
   #2 seed 12345           3339 passed, 1 xfailed (1070 s)
   #3 seed 2876304719      3339 passed, 1 xfailed (1044 s)
   → identical; xfail = F's strict test of SWD-M4-01 (S3, open)
Validation suite alone (trace run): 611 passed, 1 xfailed → _reports/trace.json
Coverage (unit + validation): calc 99.5 % line, core 95.3 % / 89.2 % branch, core.sequencer 94.0 % / 88.0 %
   (executor 87 %, model 98 %, seqfile 97 %), core.report 89 %, calc.steady 96 %, io 95.6 %; core.safety 98.3 %
Open defects: SWD-M4-01 (S3) · observations OBS-M4-01…03 (S4 / PO)
Conditions: MC4-1 (SWD-M4-01), MC4-2 (= MC3-2 REF PC), MC4-3 (= MC3-3 + DM-01 / DM-08 / DM-10 demonstrations),
   MC4-4 (D-48 guard limitation to the PO)
```

**Why ACCEPTED WITH CONDITIONS.**
- Every M4 TC passes in all three runs, with identical counts.
- The live step results equal the report results and F's independent recomputation from `data.csv`, field by field.
- VALID = 1 frames lie only inside the planned windows over a 50-step sequence.
- The single open defect is an S3 validation gap without a hazard.
- The reference-PC and demonstration items stay with the PO.

---

## 1. Housekeeping

| Item | Result |
|---|---|
| SWD-M3-02 strict xfail (now XPASS after D's MC3-5 fix, B6-15 `safety.trip_cleared`) | marker removed; `test_tc_saf_sw_005_06_trip_clear_does_not_announce_a_new_stop` passes as a regression test → **SWD-M3-02 / MC3-5 closed** |
| `test_v_static` after B's `Block` → `StepBlock` rename (alias kept) | 34 / 34 static + GUI-M3 tests pass |
| TC-SW-SEQ-007-03 text | aligned to SRS SW-SEQ-007 / D-33 c / D-47 c: ALM → controlled STOP at once + terminate, reason DRIVER_ALARM, no retry (plan M4-C1) |

## 2. What was verified

| Area | TCs | Result |
|---|---|---|
| Steady state (U) | TC-SW-REP-002-01: R4 TV-SS (N 7, mean 300 047.142857…, std 29.277) on `steady_state` and on the column form `window_stats`. VV-SS: each mask bit of ICD §7.6 (OVERRUN, ESTOP, HALT, FAULT, PAUSED, POS_UNCERTAIN, NO_AFE_DATA, LINK_WDG, AFE_SATURATED, AFE_RATE_MISMATCH) excludes, non-mask bits (PEND, ALM) do not; oracle `f_ref.steady_ok` written from the ICD sentence. INCOMPLETE at N = 63 / not 64 (1 s, 80 SPS); ON_TARGET inclusive; setpoint change excluded | **P** |
| Load-step math (U) | TV-C trim sequence (3.625 mm / 181.25 N … 3.98022 / 199.0112), Δx clamp 0.2 mm; VV-C-01/02/03 (band 3.25 N, raw_stop 771 323 GE / −521 323 LE, band = tol at 0.2 mm/s); SW-SEQ-007 timeout vector: travel time 80.02 s → 1.2·T + 10 = 106.02 s; hold 3 × planned + 10 s | **P** |
| Model / plan / generators / files (U) | VV-SEQ-01 nested loops (12 steps, loop counters outer first); VV-SEQ-02 count 0 → infinite; VV-SEQ-03 overlap / two levels / 10 001 → ERROR; VV-SEQ-04 plan durations = trapezoid; field validation (LOAD tol, LOAD capture_during_move, speed, missing target); VV-GEN-01…07 (§10.6 semantics, M4-C2); `insert_block` re-indexes loops, steps stay editable, replace mode; seqfile round trip byte-identical, corrupt / wrong schema / newer / unknown step type → FileFormatError, current sequence unchanged (M4-C5); VV-PATH; TV-D 3-point bend (60 MPa, 0.006, 5000 MPa) | **P**, except SWD-M4-01 |
| Timeline + VALID (C) | TC-SW-SEQ-003-01 / -004-01: 50-step travel sequence. Every VALID = 1 frame of `data.csv` lies inside a logged planned window. Each window opens within one frame of t_reached + settle. Each window has N ≥ 0.8·capture·rate (F's ICD §7.6 selection). Dwell ≥ max(step time, settle + capture) from t_reached. VALID = 0 at the end | **P** |
| Start refusals (C) | TC-SW-SEQ-005-01 ×10: not homed, PAUSED, HALT, ALM, AFE_RATE_MISMATCH, target outside the travel range, LOAD step without calibration, thresholds unverified (store mismatch), free space below the limit, invalid loops → refused, nothing on the wire (no MOVE_ABS / MOVE_UNTIL_LOAD / HOME / SET_VALID / JOG) | **P** |
| Load steps (C) | TC-SW-SEQ-006-02: k = 50 N/mm, k_est 40, 200 N ± 2 → one MOVE_UNTIL_LOAD, bound = soft limit 290 000 µm, raw_stop / cmp = F's oracle (±1 count), trims ≤ 10 at 0.2 mm/s each ≤ 0.2 mm, ON_TARGET, no MOVING frame during settle + capture. TC-SW-SEQ-006-04: soft spring + enabled SW travel max 60 mm → bound 60 000 µm, NOT_REACHED at x = 60 mm, sequence stopped NOT_REACHED, **no SW-limit trip** (D-33 d). TC-SW-SEQ-006-03: unreachable target between two step positions → NOT_REACHED after ≤ 10 trims, the sequence stops (M4-C3) | **P** |
| Guards / controls (C) | BREAK_DETECTED (specimen breaks at 120 N on the way to 200 N) → STOP, flagged; SLIP (grip slip at 120 N) → STOP, SLIP / BREAK (M4-C7); ALM during a travel step → **STOP mode 1**, DRIVER_ALARM, no further motion command (M4-C1); operator stop → STOP mode 1, STOPPED; abort → HALT, ABORTED, HALT latched; no SET_VALID 1 afterwards | **P** |
| Pause / resume (C) | TC-SW-STOP-004-01: Pause in the capture → wire PAUSE (not STOP), sequence PAUSED, window discarded, no VALID = 1 frame while PAUSED; Resume → RESUME (0x3C) before the re-issued MOVE_ABS 30 000 µm; FINISHED; the result uses the resumed window only. TC-SW-STOP-004-08: PAUSED + Pause/Break HALT → Resume refused locally, no RESUME / MOVE_ABS frame, the sequence terminated | **P** |
| Recording failure (C) | TC-SW-ACQ-004-02: ENOSPC during the sequence → REC_FAILURE, STOP mode 1, end reason RECORDING_FAILED; sidecar integrity complete = false with rows_lost > 0 and the failure text | **P** |
| Chart (C) | TC-SW-SCH-002-01 backend part: `seq.status` ≥ 10 Hz (≥ 50 publishes in 5 s device time), marker = DATA setpoint (± 0.2 mm), measured trace not empty. TC-SW-SCH-001-01 VV-PATH (U) | **P** (GUI repaint rate + DM-08: D's GUI suite / PO) |
| Report (C) | TC-SW-REP-001-01 / -002-02: data.csv, meta.json, report.json, report.html; the HTML parses with ≥ 2 SVG figures, marks incl. a custom field, the LOW_SPAN warning, one row per window; checklist over meta.json + report.json (M4-C6). **Live StepResult = rebuilt report = F's recomputation** from data.csv (N, raw / F / x means, rel 1e-9) for every window incl. loop iterations; ON_TARGET = \|F̄ − target\| ≤ tol. TC-SW-REP-003-01 (offline CLI, subprocess): no options → identical; `--tare-raw tare + 1000` → F − K·1000; `--cal` with K × 1.01 → F × 1.01; TC-SW-REP-004-01 `--bend3p 100 20 5` → σ = 3·F·L/(2bh²) present | **P** |
| Travel reference (C) | SW-SEQ-001: test zero at 10 mm, test targets 5 / 7 mm → MOVE_ABS 15 000 / 17 000 µm; machine reference → 5 000 µm | **P** |
| M1–M3 regression | complete earlier validation suite | **P** |

### 2.1 Code review (M4 backend)

| Module | Checked | Result |
|---|---|---|
| `core/sequencer/executor.py` | one generator job on its own runner; interrupt-aware polls; termination routing (pause vs abort); VALID via SET_VALID with device time, closed one frame before the planned end; FW clears VALID on stops, never re-asserted; guards per sample (BREAK before SLIP, arming at max(5 % target, 1 % FS), SLIP floor 0.5 % FS, BREAK only while loading); ALM edge from valid status bits → controlled stop; link failure during a capture → ABORTED / LINK_LOST, VALID cleared at the next answer / reconnect | conforms to SW-SEQ-003…007 / D-47 / D-48. OBS-M4-02 |
| `core/sequencer/model.py`, `plan.py`, `generators.py`, `seqfile.py` | ranges / applicability / loops / plan length; expansion order; §10.6 generator semantics; file v2 atomic, fixed key order | conforms; **SWD-M4-01**; OBS-M4-01 |
| `calc/steady.py`, `calc/trim.py`, `calc/path.py` | ICD §7.6 mask constants (flags 0xF0, status 0x1FF + bits 13, 14), INCOMPLETE fraction 0.8, ON_TARGET inclusive, raw-stop rounding toward the earlier stop for both K signs, step / hold timeouts | conforms (U vectors) |
| `core/report.py` | rebuild only from data.csv + sidecar (same `window_stats` on the same columns), overrides for K / tare / geometry, self-contained HTML with inline SVG | conforms (live = report = F's recomputation; CLI overrides) |
| `core/motion.py` `move_until_load` | bound reported to the safety supervisor as the planned end point → an approach ending at an enabled SW travel limit is not a trip (D-33 d) | conforms (TC-SW-SEQ-006-04) |

## 3. Evidence

```
QT_QPA_PLATFORM=offscreen  (BEND_STAND_HOTKEY=off, BEND_STAND_DATA_DIR per test)
.venv\Scripts\python -m pytest 03_SW\tests -p no:randomly -rfEsxX                       (run 1)
.venv\Scripts\python -m pytest 03_SW\tests -p randomly --randomly-seed=12345 -rfEsxX     (run 2)
.venv\Scripts\python -m pytest 03_SW\tests -p randomly -rfEsxX                           (run 3, seed 2876304719)
.venv\Scripts\python -m pytest 03_SW\tests\unit 03_SW\tests\validation -p no:randomly --cov=bend_stand --cov-branch
                                --cov-config=03_SW\pyproject.toml --cov-report=term-missing   (2874 passed, 1 xfailed)
.venv\Scripts\python -m pytest 03_SW\tests\validation -p no:randomly                      (trace run: 611 passed, 1 xfailed)
```

Logs: scratchpad `validator-f-sw/m4/run1…3.txt`, `cov.txt`, `val_trace.txt`, `fp_start/fp_end.txt`.

**Processes:** `_reports/processes.log` shows 414 started and 414 stopped / exited, each by its own handle and recorded PID. There are 24 additional "report cli" exit entries: the offline-report CLI runs as a waited `subprocess.run` (no PID kept), and every one exited with rc 0.

## 4. Defects and observations

| ID | Sev | To | Finding (file:line) | Evidence | Fix hint |
|---|---|---|---|---|---|
| **SWD-M4-01** | S3 | B | `core/sequencer/model.py` `_validate_step` (l. ~330–375) has no rule "`capture_during_move` needs `capture_s` > 0" (stated in §15.5f B6-02, plan TC-SW-SEQ-001-01). A TRAVEL ramp step with capture 0 validates without an issue and runs without a window (no data, no hazard) | `test_v_m4_calc.py::test_tc_sw_seq_001_01_ramp_step_needs_a_capture` (strict xfail) | ERROR `capture_s` RANGE when `capture_during_move` and not capture_s > 0 |
| OBS-M4-01 | S4 | B | `generate("staircase", count=5)` without `by="count"` silently ignores `count` (default `by` = increment) and returns the increment-1 staircase; the GUI always sets `by`, so only API users are affected | probe | raise `ValueError("count: needs by='count'")` when a field is given whose `depends_on` is not met |
| OBS-M4-02 | PO | Orchestrator / PO | D-48: BREAK_DETECTED is evaluated only while a command increases \|F\|, so a specimen that fails during a HOLD / capture at constant travel (creep rupture) is not detected by the sequencer. SRS SW-SEQ-007 states the guard without this restriction. The FW load limit and the operator remain the protection | review | PO to confirm the D-48 limitation, or extend BREAK to standstill steps (MC4-4) |
| OBS-M4-03 | S4 | B | `meta.json` → `sequence_runs[].events`: SEQ_START / SEQ_STEP entries carry `t_us_u: null`, whereas the data.csv event rows have device time | inspection of a recorded sidecar | fill `t_us_u` from the last sample time |

The TIMEOUT guard (step timeout 1.2·T + 10 s) is verified at the vector level and in B's unit tests. No simulator stimulus in the frozen vocabulary stalls a running move without another stop cause, so it has no C-level validation test of its own.

## 5. Conditions

| ID | Condition | Owner | Due |
|---|---|---|---|
| MC4-1 | SWD-M4-01 fix (strict xfail flips → regression test) | B, F | before P3 / release |
| MC4-2 | REF PC: NFR-001…004 (PR-1…PR-5 incl. the 100-trial SW-limit latency, DM-11), Win32 hotkey / NFR-003 (PR-3, DM-06); the REF PC is still unspecified (= MC3-2) | PO / Orchestrator, F | HW gate / P3 |
| MC4-3 | PO demonstrations DM-01 (full workflow), DM-08 (sequence chart + marker smoothness), DM-10 (HTML report in a browser) plus the open M3 ones DM-02 / 03 / 05 / 07 / 09 / 11 (= MC3-3) | PO, F | P3 |
| MC4-4 | OBS-M4-02: PO decision on BREAK detection during standstill steps (D-48) | Orchestrator / PO | P3 |
| — | OBS-M4-01, OBS-M4-03 (S4) | B | next SW revision |

Closed at this gate: MC3-5 / SWD-M3-02 (trip-clear message), MC3-4 (implemented at the M3 re-test).

## 6. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-10-05 | Validator F | M4 gate verification on 5daf3b8: housekeeping (SWD-M3-02 closed, TC-SW-SEQ-007-03 aligned), M4 validation modules (U 14 functions / 25 cases, C 16 functions / 26 cases), 3 full runs (3340 tests) identical, coverage, code review; SWD-M4-01 (S3), OBS-M4-01…03; verdict ACCEPTED WITH CONDITIONS (MC4-1…4). |
