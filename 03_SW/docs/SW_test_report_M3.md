# Bird Bend Stand — PC Software Test Report, milestone M3 (SW_test_report_M3)

| Doc | SW_test_report_M3 |
|---|---|
| Version | **1.1 — SWD-M3-01 re-test** (1.0: M3 gate verification, NOT ACCEPTED formal) |
| Date | 2026-10-05 |
| Author | Validator F — SW |
| Plan | `03_SW/docs/SW_test_plan.md` **v0.4** (M3 corrections §10c M3-C1…C5) |
| Baseline | HEAD **2d36eec** (M3 implementation) + working tree: the Integrator's OI-B-M3-03 fix, D's GUI follow-ups (B5-18 wizard start with C-12, fake `limits.check` / `marks.delete_preset`, dict-6 values, 3-point-bend group, load-cal fit page); SRS v0.6.1; ICD v0.7.3, dict **6** (D-45 e); SW_design **v0.5** (§22b, §15.5e B5-01…24); SW_design_GUI **v0.5**; D-01…D-45 |
| SW tree | `03_SW/src/**` unchanged by F; the source fingerprint (md5 over `03_SW/src/**/*.py`) was identical before and after runs 1–3, the coverage run and the trace run |
| Environment | DEV: Windows 10 Pro 19045, Python 3.14 (`.venv`), PySide6 offscreen, pytest 9.1.1 + qt / cov / randomly; `BEND_STAND_HOTKEY=off`; `BEND_STAND_DATA_DIR` per test (root conftest, verified: TC-SW-PLT-001-02). D-06: no COM port opened. |

---

## 0. Final verdict (after the re-test, §8)

```
Verdict M3: ACCEPTED WITH CONDITIONS
SW tree: 2d36eec + working tree with B's SWD-M3-01 fix (SW_design v0.5.1 B5-25) and MC3-4 diagnostics, D's GUI LINK LOST
         diagnostics and the out-of-process perf smoke — 03_SW/src fingerprint identical before / after all runs
Runs (full 03_SW/tests = 3128 tests):
   #1 fixed order          3127 passed, 1 xfailed (744 s)
   #2 seed 12345           3127 passed, 1 xfailed (753 s)
   #3 seed 3557910523      3127 passed, 1 xfailed (723 s)
   → identical; xfail = F's strict test of SWD-M3-02 (S3, GUI, open)
Validation suite alone (trace run): 560 passed, 1 xfailed → _reports/trace.json
Coverage (unit + validation): calc 99.9 %, core 95.3 % line / 89.8 % branch, io 95.7 / 91.1 %, core.safety 98.3 %
Closed: SWD-M3-01 (re-test + review), MC3-1; MC3-4 implemented (diagnostics in place; OBS-M3-R1 not seen in 3 runs)
Open defects: SWD-M3-02 (S3, D) — misleading 'SW limit trip … – STOP sent' error toast when a latch clears
Conditions: MC3-2 (REF PC), MC3-3 (PO demonstrations), MC3-5 (SWD-M3-02), MC3-6 (OBS-M3-R1 watch on REF PC)
```

## 0a. Verdict of the gate verification (v1.0, superseded by §0)

```
Verdict M3: NOT ACCEPTED (formal) — becomes ACCEPTED WITH CONDITIONS when SWD-M3-01 is fixed and its strict-xfail test
            flips (single re-run)
Scope: SAF-SW-001…006, SW-LIM-001…004, SW-META-001/002, SW-RT-001…006, SW-MAN-001…006, SW-STOP-001…003,
       SW-ACQ-002…004, SW-CAL-001…009, SW-TARE-001…003, NFR-001…004 (DEV parts; acceptance on the REF PC)
Runs (full 03_SW/tests: unit 2192 + gui 258 + integration 101 + validation 560 = 3111):
   #1 fixed order          3109 passed, 1 xfailed, 1 failed (739 s) — gui/test_sim_integration.py::test_perf_smoke_on_simulator
                           (LINK LOST in a real-clock run, OBS-M3-R1; 5 / 5 in isolation)
   #2 seed 12345           3110 passed, 1 xfailed (731 s)
   #3 seed 2941870787      3110 passed, 1 xfailed (730 s)
   xfail = F's strict test of SWD-M3-01 (open defect)
Validation suite alone (trace run): 559 passed, 1 xfailed → _reports/trace.json
Coverage (unit + validation, branch on): calc 99.9 % line, core 95.2 % / 89.7 % branch, core.calibration 85.7 %,
   io 95.6 / 90.8 %, io.sim 95.6 / 90.8 %; core.safety 98.2 % (MC2-3 closed), core.gates 98.7 %, core.link 96.5 %
Open defects: SWD-M3-01 (S2, safety layer, latent)
Conditions after the fix: MC3-1 … MC3-4 (§5)
```

**Why NOT ACCEPTED (formal).** Plan §7.2 says any open S1/S2 means NOT ACCEPTED, and SWD-M3-01 is open. While a travel-limit latch is set, the PC force limits (SAF-SW-001) are not evaluated. The case is reproducible: a pull trip of 150 N was not acted on and the force reached 975 N.
- The FW load limit (110 % FS) still protects in this case.
- The precondition is unusual: the axis must already be beyond an enabled travel limit after an outward move, which the own GUI refuses. A second client or a forced stimulus can produce it.
- The fix is small (§3).

Every other in-scope TC passed. The one run-to-run difference (run 1) is OBS-M3-R1, which is the same signature as OBS-M2-R1 (§4).

---

## 1. What was verified

| Area | TCs | Result |
|---|---|---|
| Suite adaptation | OI-B-M3-01: every M1 / M2 motion stimulus enters the no-specimen mode when the load input is invalid (`harness.no_specimen`, `ensure_motion_allowed`). OI-B-M3-02: H3 write-order cases rebased on the dict 6 defaults (40 kHz, 12.5 / 12.5 µs = the H3 boundary) | done; the whole earlier validation suite passed (496 / 496) before the M3 additions |
| Calculations (U) | TC-SW-CAL-007-01 VV-LC-01…06 + 2-point + degenerate (production = F's OLS, statuses at 0.1 / 0.5 % span); TC-SW-CAL-008-01 LOW_SPAN, extrapolation 3 × F_max, mass rules; TC-SW-CAL-002-01 VV-TC-01/02; TC-SW-CAL-003-01 plausibility (reject / SPM_CHANGE_20 with 160 / 800 / SPM_CHANGE_5 / REPEAT 0.5 %); TC-SAF-SW-001-03 VV-LIM; TC-SAF-SW-006-01 VV-M boundary; TC-SW-RT-004-01 VV-D; TC-SW-TARE-002-01 force / kgf; TC-SAF-SW-002-01 VV-THR | **P** (16 + 3) |
| Tare | TC-SW-TARE-002-02: stream started and restored; tare_raw within 3σ/√N of the cell offset; N = round(10 s × rate) ± 1; session-only, no tare file, a new Backend has no tare. TC-SW-TARE-003-01: moving, < 1 s after a move (device time), HALT, saturated sample, drift → refused; large offset → warning. TC-SW-TARE-001-01 (G): TARE on every tab → non-modal popup with STOP; refused with HALT latched | **P** |
| Load calibration | TC-SW-CAL-005-01: zero + 1 kg + 10 kg (`weight`). K = F's OLS on the point means, ≈ 1/3285 N/count; PASS; LOW_SPAN; `active_load.json` + history. FW thresholds after the tare = `f_ref.fw_raw_limits(±fw_level, K, tare)` with zero = rha(tare), written and read back, VERIFIED with this cal + tare id. TC-SW-CAL-005-02: N = 804 ± 1. TC-SW-CAL-006-02: outliers 2.5 % → point rejected with Repeat; saturation → aborted by the FW load-limit fault, active calibration unchanged; decreasing mass refused. TC-SW-CAL-008-02: 290 N OK / 300 N EXTRAPOLATED, F within 0.2 N. TC-SW-CAL-009-02: `afe.gain_channel` changed → calibration invalid for limits, motion refused LOAD_INPUT_INVALID | **P** |
| Travel calibration | TC-SW-CAL-002-02 / -004-01: board 796 on an 800 steps/mm mechanism; D1 / D_tot entered from the true world distance. Backlash first, all moves absolute and +; N1 / N2 / spm2 = oracle; result 800 (rel 1e-6); accept → SET + read-back + SAVE, `travel_spm`. TC-SW-CAL-001-02: Cancel in REFERENCE / ENTER_D1 / MOVE2 / RESULT → spm0 read back, no SAVE_PARAMS, no `active_travel.json` | **P** |
| PC limits (SAF-SW-001) | TC-SAF-SW-001-02: no calibration → REFUSE LOAD_INPUT_INVALID with the reason, nothing on the wire; AFE stall while moving → STOP. TC-SAF-SW-001-01 (lock-step part): pull trip 150 N into a 50 N/mm spring → STOP written ≤ 50 ms after the first violating DATA frame (F from F's oracle on the wire raw), `safety.trip` PULL with value, indicator + hint, a tension-increasing move refused (SW_TRIP), a reducing one allowed. TC-SAF-SW-001-04 (1): travel minimum supervised while a PULL latch is set | **P** |
| | TC-SAF-SW-001-04 (2): force limits while a TRAVEL latch is set | **F** → SWD-M3-01 |
| Thresholds (SAF-SW-002) | -03 store mismatch → FAILED → motion refused; recheck → VERIFIED. -04 clamp scenario (offset 125 000): raw_max clamped to 7 151 121, FW_CLAMPED warning, VERIFIED. **MC2-3: `core.safety` 98.2 %** | **P** |
| Limits / no-specimen / session | TC-SW-LIM-002-01 ×6: FW level > 110 % FS / below an enabled trip, pull > 110 % FS, warn % 40, travel min ≥ max, travel beyond the soft limit → ERROR, nothing changed, no SET. TC-SW-LIM-004-01: CONFIRMATION_REQUIRED without the confirmation; confirmed → indicator + WARN item; FW thresholds = nominal defaults (DEFAULT_ONLY, read back); ends at disconnect; not in the session file; a new Backend starts off. -004-02: keeps the calibrated thresholds. TC-SW-LIM-003-01: session save / load identical, limits in `meta.json` | **P** |
| Marks / recording / sample | TC-SW-META-001/002: custom fields, duplicate / empty keys refused, preset round trip, MARK_EDIT row, `marks_at_start` / `marks_final`. TC-SW-ACQ-002 (C): every D row F_N = K·(raw − tare) (rel 1e-6), F_kgf = F_N / g, TARE event row, sidecar snapshot. TC-SW-ACQ-003: 5 kg sample f_mean 49.03 N within 3σ/√N, N ± 1, 0.09 s / 10.01 s refused, daily file. TC-SW-ACQ-004-04: free space below the limit → refused | **P** |
| Margin / test zero / channels | TC-SAF-SW-006-02: k_est 500 N/mm at 10 mm/s → WARN SAF_SW_006_MARGIN, 50 N/mm → none. SW-MAN-006: test zero at 12 mm → `x_test_mm` 0, X_ZERO row, reset. TC-SW-RT-002-02: force channels unavailable with a reason / 'n/a' → available after calibration + tare (channels.changed) | **P** |
| Manual GUI | TC-SW-MAN-001-01: slider drag → nothing on the wire, release → exactly one MOVE_ABS = rha(value·1000), keys / page do nothing. TC-SW-MAN-004-02: hold-to-jog refreshes; release and window deactivation → exactly one JOG 0 | **P** |
| M1 / M2 regression | complete earlier validation suite incl. D-37, OBS-M1-R1, SW-RT-006, M2 motion / gates / hotkey (fake), sim fidelity, X subset | **P** |
| Not verifiable on DEV | NFR-001…004 acceptance (PR-1…PR-5 on the REF PC), TC-SAF-SW-001-01 100-trial rt statistic (PR-5), Win32 hotkey (W), DM-02…DM-11 | **pending — PO / REF PC** |

### 1.1 Code review (M3 backend)

| Module | Checked | Result |
|---|---|---|
| `core/safety.py` SafetySupervisor | order STOP → terminate → latch → row + publish; strict `>`; saturated → ±∞; load input invalid while moving → STOP once per motion; warning hysteresis; travel trips only for outward motion; planned bounds inside the limits do not trip; restop while increasing (100 ms) | **SWD-M3-01**: a single latch slot (`self.trip`) gates both evaluations (`if … and self.trip is None` at l. 301 and l. 315) — while any latch is set, the other limit class is not evaluated. Release of a TRAVEL latch needs standstill inside, so a whole inward move runs without force supervision |
| `core/safety.py` ThresholdManager / `loadinput.py` | calibrated target only with a valid cal + tare of this board (also in the no-specimen mode); identity check (`matches`) feeds the gates; FAILED retried only on a new target or recheck | conforms (VV-THR, clamp, mismatch tests) |
| `core/tare.py`, `core/capture.py` | gate items, stream start / restore, std reference max(3·std_zero, 50) (135 counts without a calibration), refusal reasons verbatim, offset warning, threshold rewrite, one-level undo | conforms |
| `core/calibration/{load,travel,store}.py` | point flow, mass rules, WARN confirmation, accept only when idle; travel: owner token, trial SET + read-back, restore on every exit, restore-pending record, NVM only at accept | conforms (wizard and cancel tests) |
| `core/recorder.py`, `core/metadata.py`, `core/session.py` | derived columns, event rows, sidecar snapshot, failure path (REC_FAILURE, rows_lost), unique folders; custom-field rules; session validation + autosave without tare / mode | conforms |
| `tests/conftest.py` | OBS-D-M3-01: `BEND_STAND_DATA_DIR` per test (autouse) | verified (TC-SW-PLT-001-02) |

## 2. Evidence

```
QT_QPA_PLATFORM=offscreen  (BEND_STAND_HOTKEY=off, BEND_STAND_DATA_DIR per test from 03_SW/tests/conftest.py)
.venv\Scripts\python -m pytest 03_SW\tests -p no:randomly -rfEsxX                       (run 1)
.venv\Scripts\python -m pytest 03_SW\tests -p randomly --randomly-seed=12345 -rfEsxX     (run 2)
.venv\Scripts\python -m pytest 03_SW\tests -p randomly -rfEsxX                           (run 3, seed 2941870787)
.venv\Scripts\python -m pytest 03_SW\tests\unit 03_SW\tests\validation -p no:randomly --cov=bend_stand --cov-branch
                                --cov-config=03_SW\pyproject.toml --cov-report=term-missing   (2751 passed, 1 xfailed)
.venv\Scripts\python -m pytest 03_SW\tests\validation -p no:randomly                      (trace run: 559 passed, 1 xfailed)
gui\test_sim_integration.py::test_perf_smoke_on_simulator alone: 5 / 5 passed
```

Logs: scratchpad `validator-f-sw/m3/run1…3.txt`, `cov.txt`, `val_trace.txt`, `fp_start/fp_end.txt`.

**Requirement coverage** of the validation suite (`_reports/trace.json`, passed / total entries):
- SAF-SW: 001 6 / 7 (1 = SWD-M3-01), 002 19, 004 5, 005 24, 006 2
- SW-LIM: 001 9, 002 6, 003 1, 004 3
- SW-META: 001 1, 002 2
- SW-ACQ: 002 7, 003 1, 004 5
- SW-CAL: 001 5, 002 3, 003 1, 004 3, 005 2, 006 5, 007 9, 008 3, 009 2
- SW-TARE: 001 3, 002 2, 003 6
- SW-RT: 001 2, 002 2, 003 2, 004 4, 005 2, 006 15
- SW-MAN: 001 1, 002 4, 003 3, 004 7, 005 1, 006 9
- SW-STOP: 001 18, 002 12, 003 13
- NFR: 001 3, 002 5, 003 2, 004 1 (DEV parts)

**Processes:** `_reports/processes.log` shows 330 started and 330 stopped / exited, each through its own handle and recorded PID. During this gate F stopped one of its own background pytest chains, by task handle, to add a test; it was restarted from scratch.

## 3. Defects

| ID | Sev | To | Finding (file:line) | Evidence | Fix hint |
|---|---|---|---|---|---|
| **SWD-M3-01** | **S2** (safety layer, latent; FW load limit still active) | B | `core/safety.py:301` evaluates a new force trip only `if … self.trip is None`; `:315` evaluates the travel limits only `if self.trip is None`. With a TRAVEL latch set (released only at standstill inside the limit, l. 377-382), a move back inside the travel range into a specimen is **not supervised against the PC force limits** (SAF-SW-001 "on every received frame"). | `test_v_m3.py::test_tc_saf_sw_001_04_pull_limit_supervised_while_travel_latch_is_set` (strict xfail): pull trip 150 N, travel min 15 mm, axis at 10 mm after an outward (other-client) move → TRAVEL_MIN latch; `move_to(40)` into a 50 N/mm spring → force reached **975 N**, no PULL trip until standstill | Keep one latch per limit class (force, travel) or evaluate force and travel every frame regardless of the other latch; the restop and direction rules per latch |

## 4. Observations

* **OBS-M3-R1 (recurrence of OBS-M2-R1).** Run 1 failed D's real-clock `test_perf_smoke_on_simulator` with LINK LOST: the `stream_stop` gate gave LINK_DOWN, but no frames were lost and the refresh p95 was 33.8 ms. It passed 5 / 5 in isolation and in runs 2 and 3. This is the second occurrence, again in a full-suite run on a busy host. Hypothesis: a ≥ 1 s stall of the whole process (GIL), for example a full GC of the large pytest heap under the GUI GC policy, starves the in-process simulator thread. An operator session has a small heap, so it is less exposed, but the behaviour is the same.
  * Action (B / D, before M4): log the longest Supervisor tick gap and the GC durations together with every LINK LOST.
  * PR-4 (1 h soak on the REF PC) records LINK LOST. A recurrence without a stall becomes a defect.
* `core.calibration` line coverage is 85.7 % and `core.tare` 82 %. The core floor (≥ 90 % line overall) is met; the misses are error / abort branches covered by B's unit tests.
* GUI-only items without a validation test of their own, covered by D's GUI suite (258 / 258 in each run): the wizard pages, the Safety-limits / Marks tabs and the hotkey test dialog.

## 5. Conditions (after SWD-M3-01 is fixed)

| ID | Condition | Owner | Due |
|---|---|---|---|
| MC3-1 | SWD-M3-01 fix and F's re-run (strict xfail flips) | B, F | M3 close-out |
| MC3-2 | REF PC: NFR-001…004 (PR-1…PR-5 incl. the 100-trial SW-limit latency and the 4-pane DM-11), Win32 hotkey / NFR-003 (PR-3, DM-06); REF PC still to be specified (MC-2 / G6) | PO / Orchestrator, F | before the M4 gate |
| MC3-3 | PO demonstrations: DM-02 fresh install, DM-03 second monitor, DM-05 NVM buttons, DM-07 manual tab, DM-09 Pause/Resume, DM-11 plot panes; the calibration / tare wizards walk-through with real weights at the HW gate | PO, F | M4 / HW gate |
| MC3-4 | OBS-M3-R1 instrumentation (LINK LOST with tick-gap and GC timing) | B, D | M4 entry |

## 6. Items for other roles

* **B:** SWD-M3-01 (blocking), MC3-4.
* **D:** MC3-4 (GC policy timing in the LINK LOST log); no GUI defect found (258 / 258 in every run).
* **Orchestrator / PO:** REF PC (MC3-2), demonstrations (MC3-3).

## 8. Re-test (v1.1)

B reported SWD-M3-01 fixed: `core/safety.py` keeps one latch per class (PULL, PUSH, TRAVEL_MIN, TRAVEL_MAX), all
evaluated on every frame, each with its own direction refusal, re-stop and clear rule; `SafetyStatus.trips`,
`active_trips()`; 12 ordered-pair unit tests (SW_design v0.5.1 B5-25). B also added the MC3-4 diagnostics
(`Backend.link_diagnostics()`, GcWatch, a LINK_LOST recording row). D added GUI-side LINK LOST diagnostics and moved
`test_perf_smoke_on_simulator` to the out-of-process simulator.

### 8.1 Review of the `core/safety.py` diff
| Point | Finding |
|---|---|
| Thread safety | The latch map is replaced, not mutated (`self.latches = {**…}` / dict comprehension). Readers on other threads (gates, `status()`, `direction_refused`) see either the old or the new map, never a half-updated one. `_last_restop_us` is mutated in place, but only the Pipeline thread uses it. `trip` is a property over one snapshot of the map. **OK.** |
| Every class every frame | Force: PULL and PUSH are each evaluated unless that class is latched or disabled. Travel: evaluated while moving and homed regardless of force latches; a class already latched is skipped (`side in self.latches`). A new trip of any class sends STOP first (order STOP → terminate → latch → row). **OK** (SAF-SW-001 "every received frame"). |
| Per-class clear rules | PULL / PUSH: back inside by 2 % of the trip level, or the limit disabled. TRAVEL_*: at standstill inside, or disabled. Same rules as before, now per class. **OK.** |
| Direction-aware refusal / re-stop | `direction_refused` returns the first latched class whose violation the direction increases. The re-stop runs per class with its own 100 ms timer. The tension-increasing move is still refused with a PULL latch set (extended reproducer). **No regression.** |
| Publishing | On a clear, `safety.trip` publishes the remaining latest latch, or `None`. Consumer impact: SWD-M3-02. |

### 8.2 Results
* **SWD-M3-01 — closed.** Strict xfail removed. `test_tc_saf_sw_001_04_pull_limit_supervised_while_travel_latch_is_set` now passes. With a TRAVEL_MIN latch set, the move into the spring trips PULL; F_max stays below 150 N + v·k·75 ms + 5 N, against 975 N before the fix. PULL appears in `safety.trips`, and the tension-increasing move is refused (SW_TRIP). TC-SAF-SW-001-04 (1) (travel min supervised while PULL is latched) and TC-SAF-SW-001-01 also pass.
* **New SWD-M3-02 (S3, D).** `gui/main_window.py:710-712` shows every `safety.trip` publish as an error toast "SW limit trip: <text> – STOP sent", including the clear (payload `None`, or since B5-25 the remaining latch). After unloading, the operator reads "SW limit trip: None – STOP sent" although no STOP was sent. `event_log` also files the clear as an error. Reproduced by `test_v_gui_m3.py::test_tc_saf_sw_005_06_trip_clear_does_not_announce_a_new_stop` (strict xfail). No hazard: no motion, no lost indication. Fix hint: toast only for a new trip (payload not `None` and its `t_us` newer than the last toast); show a clear as info "SW limit cleared: <limit>". Alternatively, B publishes the clear on its own topic or payload (`SwTripCleared`).
* **MC3-4 — implemented.** `Backend.link_diagnostics()` is called on every LINK LOST (longest tick gap per thread, GC pauses, rx age, queue, link losses), logged, and written as a LINK_LOST recording row. D's GUI writes LINK_LOST_DIAG to the event log. OBS-M3-R1 did not occur in the three re-test runs. D's perf smoke now uses the out-of-process simulator, so it no longer covers the in-process case.
* **Processes:** `_reports/processes.log` shows 372 started and 372 stopped / exited, each by its own handle and recorded PID.

### 8.3 Conditions after the re-test
| ID | Condition | Owner | Due | State |
|---|---|---|---|---|
| MC3-1 | SWD-M3-01 fix + re-run | B, F | M3 close-out | **closed** |
| MC3-2 | REF PC: NFR-001…004 (PR-1…PR-5 incl. the 100-trial SW-limit latency, DM-11), Win32 hotkey / NFR-003 (PR-3, DM-06); REF PC still unspecified (MC-2 / G6) | PO / Orchestrator, F | before the M4 gate | open |
| MC3-3 | PO demonstrations DM-02 / 03 / 05 / 07 / 09 / 11; the wizard walk-through with real weights at the HW gate | PO, F | M4 / HW gate | open |
| MC3-4 | LINK LOST diagnostics (tick gap, GC) | B, D | M4 entry | **implemented** (verified by review; the trigger did not recur) |
| MC3-5 | SWD-M3-02 (S3): no "trip – STOP sent" toast / error entry at a latch clear | D (+ B for the topic semantics) | M4 entry | open |
| MC3-6 | OBS-M3-R1: if a LINK LOST occurs in PR-4 on the REF PC, the diagnostics decide (stall vs defect) | F | REF-PC run | open |

## 9. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-10-05 | Validator F | M3 gate verification on 2d36eec + follow-ups: suite adapted (OI-B-M3-01/02), M3 validation modules (U / C / G, 64 new tests), 3 full runs (3111 tests), coverage (MC2-3 closed: core.safety 98.2 %), code review; SWD-M3-01 (S2, latent); verdict NOT ACCEPTED (formal) → ACCEPTED WITH CONDITIONS after the SWD-M3-01 fix. |
| 1.1 | 2026-10-05 | Validator F | Re-test: SWD-M3-01 closed (review of the per-class latch map, extended reproducer), new SWD-M3-02 (S3, GUI toast at a latch clear, strict xfail), MC3-4 implemented; 3 runs identical (3127 passed, 1 xfailed); final verdict ACCEPTED WITH CONDITIONS (MC3-2, -3, -5, -6). |
