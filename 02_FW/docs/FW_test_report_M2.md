# Bird Bend Stand — Firmware test report, M2 gate (sensor & motion)

| Item | Value |
|---|---|
| Doc | `02_FW/docs/FW_test_report_M2.md` |
| Gate | **M2** (P2 / sensor & motion), FW_test_plan **v0.4** §5.2 |
| Date | 2026-10-04 |
| Author | Validator E — FW (independent of Implementer A) |
| FW under test | commit `099af88` (M2 implementation), `02_FW/src/**` unchanged since (git status clean for `02_FW/src`); FW 0.1.0; images `nucleo_f446re`, `_meas`, `_meas_dwt` |
| Interfaces | SRS v0.5.2, ICD v0.6 (`PROTO_ICD_VERSION` 0.6, PROTO 1.0, PAYLOAD 1), `params.yaml` dict_version 4, `PARAM_DICT_HASH` 0xFCC54C90, 47 parameters; DECISIONS D-01…D-42 |
| Tools | host gcc 13.1 (CLion MinGW), PlatformIO 6.2.0 (arm-none-eabi-gcc 12.3), Python 3.14, pytest 9.1.1 + pytest-randomly; FW host twin `00_System/tools/fw_twin` (A's core, lock-step), built into the validator scratchpad (`VAL_TWIN_BUILD_DIR`) so the shared `fw_twin.exe` of other roles was never rebuilt |
| Constraints | **D-06: no hardware** — target-only parts stay open (+H, HG list of plan §6.5). No commits. |

## 0. Final verdict after the M2 close-out re-test (2026-10-04, commit 4e9989e + ICD v0.7.1, §6)

**GO WITH CONDITIONS (F1–F4).** DEF-M2-01 and DEF-M2-02 are **closed** (code inspection + A's register-level host
suite `test_impl_steptim` + the validator's NOINIT dry run on the v0.7.1 twin model; target confirmation in HG-09 /
HG-14). The former conditions C2, C3, C4, C5 are closed (CR-03 applied: `drv.pwr_sense_enable` and
`drv.k1_check_enable` default false; DWT statistics present, 23 sections). Every host-verifiable M2 TC passes twice;
no skip, no ignore, no xfail.

| # | Condition | Owner | Closure |
|---|---|---|---|
| F1 | +H parts open under D-06 (HG list v0.4, 32 items; HG-22 N/A, HG-21 optional since D-41), incl. the D-42 hardwired ENA cut (HG-10 c/d/e, HG-20, C-25) | Orchestrator / E | PO-approved HW gate |
| F2 | **HG-18 DWT check of A's static NFR-007 upper bounds that exceed the budget**: E-stop handler all-branch 1.15 µs vs ≤ 1 µs; step ISR 2.4 µs vs ≤ 2 µs (FW_design §9.8 / §9.9). Measured maxima above the budget → DEF at the HW gate (fix or SRS decision) | A, E | HW gate |
| F3 | PO approval of the bench safety procedure FW_test_plan §6.8 (E-stop sense bypass for stimulus trials, D-42 path verified first) | Orchestrator → PO | before HG-10 a / HG-04 a |
| F4 | Carried E-C4: SW quiesce during SAVE (D-37 a) verified by Validator F | B / F | M3 |

The first verification round (commit 099af88) follows unchanged in §1–§5 for the record.

## 1. Verdict

**GO WITH CONDITIONS (C1–C7)** — provided the Orchestrator accepts DEF-M2-01 (Medium, target HAL only, not
observable in the twin) as a *fix before the hardware gate* condition (C2); if a fix is wanted before the gate is
closed, the verdict is NO-GO until A's one-line change is re-inspected (it needs no new twin evidence).

All 79 host-verifiable M2 TCs pass **twice** (fixed + random order, different seeds); the 4 H-only M2 TCs (SYS-004-01,
SYS-009-01, SYS-011-01, FW-HOM-003-01) and the +H parts are OPEN-H. The pre-written suites run with no skip, no
ignore and no xfail left. Gate-size runs: **10 000-step command-acceptance differential walk: 9 989 compared, 0
mismatches** (11 unstable steps re-sampled); **2 × 10 000 random stops: count integrity exact** (E-12). The carried M1 condition **E-C2**
(motion/input parts of TC-FW-NVM-003-01, TC-IF-005-01, TC-FW-STR-003-01) is **closed**. The FW follows ICD v0.6 and
D-40 a/b/d; the CR-01 code check (S-14) is clean.

| # | Condition | Owner | Closure |
|---|---|---|---|
| C1 | +H parts open under D-06 (HG list v0.4, 32 items; HG-22 N/A, HG-21 optional since D-41) | Orchestrator / E | PO-approved HW gate |
| C2 | **DEF-M2-01** (Medium): a CLEAN / TRUNCATE halt that lands between a TIM2 update event and its ISR clears UIF and loses the count of a completed pulse (SAF-FW-004) | A (fix), E (inspection + HG-09) | before the HW gate |
| C3 | **DEF-M2-02** (Medium, HW_MEAS only): NOINIT does not preserve the pre-reset last-PUL stamp / heartbeat → HG-14 cannot be evidenced as planned (REQ-A-M2-08) | A | before the HW gate |
| C4 | **CR-03 / D-41**: `drv.pwr_sense_enable` default 0 (and SRS v0.6 / ICD v0.7 / wiring v0.5 with the D-42 hardwired ENA disable). Until then the release image with an unwired PA7 reads "driver unpowered" (ENABLE refused) and an E-stop held > 200 ms with a sense input wired would latch K1_WELDED | Orchestrator, C, A | before any target use (HG-32) |
| C5 | OI-FW-37: `HW_MEAS_DWT` DWT statistics missing (op 9 w0 = 0) — HG-18 / NFR-007 (the static step-ISR upper bound 2.4 µs > 2 µs, FW_design §9.8) cannot be measured without it | A | before the HW gate |
| C6 | §6.8 bench safety procedure (E-stop sense bypass for stimulus trials) — **PO approval** | Orchestrator → PO | before HG-10 a / HG-04 a |
| C7 | OBS-M1-01 / D-37 a SAVE exemption is in SRS / ICD; SW quiesce during SAVE verified by Validator F (carried E-C4) | B / F | M3 |

## 2. Evidence (commands and counts)

From the repo root, Git Bash, `PATH` = CLion MinGW 13.1 first; twin runs with
`VAL_TWIN_BUILD_DIR=<scratchpad>/validator-e-fw/twin_build`.

| # | Command | Result |
|---|---|---|
| E-1 | `pio test -d 02_FW -e native -f "test_impl_*"` (A's suites, run 1) | **151/151 PASS** (25 suites) |
| E-2 | same (run 2) | **151/151 PASS** |
| E-3 | `gen_val_vectors.py` (corpus seed 1) + `pio test -d 02_FW -e native -f "test_val_*" -v` | **9 suites, 34/34 PASS**; anti-skip counts executed = expected: crc 4, frames 184, streams 11, corpus 100 000, check 507, units 660, bits 37, **motion 37** (9 cases + 28 path rows, worst 1 tick), **loadlim 12** (52 steps), **sniff 2 000** (3 386 hits), **rate 500** |
| E-4 | same with `VAL_CORPUS_SEED=2`, `VAL_SEED=777` (shuffled order) | **34/34 PASS** |
| E-5 | negative controls (copies in the scratchpad): one motion period +2, one load-limit trip flag flipped, one sniff case removed | `test_val_ramp`, `test_val_loadlim`, `test_val_m2pure` **FAIL as intended** (value / anti-skip) |
| E-6 | `pytest 02_FW/test/twin -q -p no:randomly` | **190 passed** (M1 27 + oracles 23 + M2 140), 0 skip, 0 xfail |
| E-7 | `VAL_SEED=99 pytest 02_FW/test/twin -q -p randomly --randomly-seed=2026104` | **190 passed** (both E-6 / E-7 repeated after the last helper change: 190 / 190) |
| E-8 | `pytest 02_FW/test/twin/test_val_m2_oracles.py` | **23 passed**: the validator's independent `ramp_ref.py` reproduces all 9 `motion_vectors.json` cases exactly and all 28 path / 3 planner rows; `latch_ref.LoadLimit` reproduces all 12 `loadlim_vectors.json` cases (every trip / regrow-window flag) |
| E-9 | `VAL_WALK=10000 pytest … -k differential_walk -s` | **10 000 steps: 9 989 compared, 11 unstable (re-sampled), 0 mismatches** vs `ref_cmdcheck` |
| E-10 | `pytest 03_SW/tests/integration -q` fixed / `--randomly-seed=1004` | **56 passed, 4 skipped** ×2 (the 4 skips are stale "verified in M2" stop-timing tests, OBS-M2-05) |
| E-11 | `pytest 00_System/tools/tests -q` | **888 passed** |
| E-12 | `VAL_STOPS=10000 pytest … -k random_stops_count_integrity`, seeds 11 and 12 | **pass ×2** (§2.1) |
| E-13 | `pio run -d 02_FW -e nucleo_f446re -e nucleo_f446re_meas -e nucleo_f446re_meas_dwt` | **SUCCESS ×3**, A's check_map M-1…M-4 PASS; release flash 43 580 B / RAM 8 616 B; meas 46 392 B / 41 468 B; meas_dwt 46 388 B / 41 468 B |
| E-14 | `python 02_FW/test/static/check_meas_build.py` (TC-SYS-009-02) | **PASS**: 47/49 objects byte-identical (code, rodata, data, relocations) between release and each measurement image; only `meas_f4.c.o`, `build_id.c.o` differ; `#if HW_MEAS` only in `meas_f4.c`; safety symbols present in both ELFs |
| E-15 | `python 02_FW/test/static/check_static.py` | **15/15 PASS** (new S-14 CR-01 code check, S-15 fixed polarity / load check unconditional / no force scaling; S-09 both generators `--check` exit 0) |

### 2.1 Gate-size random-stop run (E-12)
`VAL_STOPS=10000` with `VAL_SEED=11` (122 s) and `VAL_SEED=12` (78 s): **1 passed each** — 2 × 10 000 random stops (sources PC STOP 0, STOP 1, HALT at random phases 20…400 ms into moves at 1 / 10 / 30 mm/s, both directions): rising PUL edges = Δ`pos_steps` in every trial (± 1 only with POS_UNCERTAIN), no pulse shorter than `pulse_high_ns`. The limit / E-stop / load / step-fault sources are covered by `test_every_stop_source` and the source tests. The helper decodes EVENTs incrementally (the first attempt was O(n²) and was stopped by its task id).

## 3. Coverage (M2 requirements)

Result per TC: **PASS** = host evidence twice; **OPEN-H** = target part open under D-06 (HG item).

| Requirement | TCs (level) | Result |
|---|---|---|
| SYS-001 | TC-SYS-001-01 (S: pinout / wiring v0.4 inspected — CR-01 done; D-41/D-42 drawing pending, C4) | PASS (doc) |
| SYS-002 | TC-SYS-002-01 (T: L1 = all SAF tests; L2 / L3 FW-only reactions with stream off / PC silent), -02 (S-15) | PASS |
| SYS-004 | TC-SYS-004-01 (H), -02 (T) | PASS / OPEN-H (HG-25) |
| SYS-005 | TC-SYS-005-01 (S: no step-unit speed parameter, S-11) | PASS / OPEN-H (HG-19) |
| SYS-006 | TC-SYS-006-01 (T: release → no motion until ESTOP_CLEAR + ENABLE + HOME) | PASS / OPEN-H (HG-10, HG-20; D-41/D-42) |
| SYS-009 | TC-SYS-009-01 (H), -02 (S, E-14) | -02 PASS / OPEN-H |
| SYS-011 | TC-SYS-011-01 (H) | OPEN-H |
| SAF-FW-001 | TC-SAF-FW-001-01 (T: 15 sources) | PASS |
| SAF-FW-002 | -01 (T, PC paths + input paths in the source tests), -02 (U sniffer 2 000 streams), -03 (T MOVE_ABS + HALT burst: 0 pulses after the HALT frame) | PASS / OPEN-H (HG-11/12/13) |
| SAF-FW-003 | -01 (T, 3 period bands, OI-FW-39 reference), -02 (U path rows + T) | PASS |
| SAF-FW-004 | -01 (T, 200 ×2 + E-12), -02 (U every CNT), -03 (T step fault) | PASS twin / **DEF-M2-01** target HAL / OPEN-H (HG-09) |
| SAF-FW-005 | -01 (T) | PASS / OPEN-H (HG-10) |
| SAF-FW-006 | -01 (T) | PASS |
| SAF-FW-007 | -01 (S-15), -02 (T wire break ×4), -03 (T boot ×5) | PASS / OPEN-H (HG-17) |
| SAF-FW-008…011 | -008-01/02/03, -009-01, -010-01, -011-01 (T + U vs loadlim vectors) | PASS |
| SAF-FW-012 | -01, -02 (10 min idle + 60 s jog at 10 SPS, no stale) | PASS |
| SAF-FW-013 / 014 | -013-01 (D-33 h), -014-01 (D-40 a) | PASS |
| SAF-FW-015 / 016 / 017 | link watchdog 200 / 1000 ms, dead-man 50 / 250 / 1000 ms, idle disable 600 s / loaded / stale | PASS |
| SAF-FW-018 | -01 (reset ×4 causes) | PASS / OPEN-H (HG-15) |
| SAF-FW-019 | -01 (main / tick / level-1 storm, LSI 17 / 32 / 47 kHz), -02 | PASS / OPEN-H (HG-14, C3) |
| SAF-FW-020 | -01 (507 check vectors), -02 (10 000-step walk), -03 (S: no relative move in `protocol.yaml`) | PASS |
| SAF-FW-021 | -01 | PASS |
| SAF-FW-023 | -01, -02 (race −1…+5 ms), -03 | PASS |
| SAF-FW-024 / 025 | -024-01/02 (DRV_PWR loss during a SAVE with erase: 19.5 ms after a 503 ms erase ≤ op + 25 ms), -025-01 | PASS (target items optional / N/A since D-41) |
| SAF-FW-026 | -01 | PASS / OPEN-H (HG-16) |
| FW-AFE-001…005 | U (HX711 read model, sign, pulses 25/26/27, settle), T (gain / rate reconfiguration, settle 0/4/20 + re-init, rate mismatch 10 SPS / ±2 %, timestamp + setpoint across the 2³² wrap) | PASS / OPEN-H (HG-05) |
| FW-MOT-001 | -01 (U H3 vectors), -02 (T 50 kHz cap, widths, DIR setup) | PASS / OPEN-H (HG-08) |
| FW-MOT-002…005, 007…009 | U motion vectors + T (TV-M on the wire, busy / zero length, units at spm 100 / 160 / 800 / 100 000, soft limit / bound / un-homed bound / reversal, STOP / HALT, ENABLE settle 0 / 500 / 2000 + power return, spm rules) | PASS |
| FW-HOM-001 / 002 / 004 | T (default world, START active, NOT_FOUND / WIRING, aborted, drift 0.1 / 0.3 mm, load pre-check) | PASS |
| FW-HOM-003 | H | OPEN-H (HG-26) |
| FW-SW-001…005 | U release debounce; T limit bounce (5 / 20 / 200 ms), E-stop release debounce, PAUSE button events + polarity, ALM / PEND / chatter, DRV_PWR filter | PASS / OPEN-H (HG-16/21) |
| FW-CMD-003 | T, 8 faults + all-or-nothing | PASS |
| FW-STR-003 (E-C2), 005, 006 | M2 bits, fallback 1 / 10 / 80 Hz, every EVENT code (NVM_ERROR: M1 flash-cut suite) | PASS |
| FW-TIM-001 | T (wrap, latency = 0 in the twin model) | PASS / OPEN-H (HG-05) |
| FW-NVM-003, IF-005 (E-C2) | SAVE / LOAD / DEFAULT while moving → E_BUSY 1; same-SEQ MOVE_ABS twice → one motion | PASS |
| NFR-006 / 007 | S parts (NFR-007 analysis FW_design §9.8 reviewed: step ISR upper bound 2.4 µs) | S PASS / OPEN-H (HG-18, C5) |

## 4. Findings

### 4.1 Defects

| ID | Sev. | Finding (evidence) | Fix hint | Addressee |
|---|---|---|---|---|
| **DEF-M2-01** | Medium | **Lost step count at a halt racing a TIM2 update** (SAF-FW-004). `02_FW/src/hal/f446/step_tim2.c` `halt_hw()` (called by `hal_step_stop_now()` when `stepgen_halt_complete()` is false, and by `hal_step_abort()`) writes `TIM2->SR = ~TIM_SR_UIF`. If the update event that ends a pulse has occurred but `TIM2_IRQHandler` (level 2) has not run yet — e.g. a level-0/1 ISR (E-stop, START / END limit) entered just after the update preempts the pending level-2 TIM2 IRQ, or the tick / thread halts a few cycles after it — the counter is at ≈ 0, so the decision returns "no pulse in flight", `halt_hw()` clears UIF and the pending TIM2 IRQ returns at `if ((TIM2->SR & TIM_SR_UIF) == 0u)` without `s_count += s_dir`. The completed pulse is never counted; for a CLEAN stop (limit, PC STOP / HALT, load limit) POS_UNCERTAIN is not set and HOMED is kept → silent 1-step position error. Window ≈ ISR entry latency / step period (≈ 1 % per limit stop at 50 kHz). Inspection only — the twin's step model is atomic (plan §1.5 item 2), so no twin test can show it. | Do not clear UIF in `halt_hw()` (it is set only by a real update since no UG is issued there): the pending ISR then takes the `CEN == 0` branch and counts the completed pulse; or count it in `halt_hw()` when UIF is set. Re-verified by inspection + HG-09 (MT-2 vs `pos_steps` over 100 limit / STOP stops). | A |
| **DEF-M2-02** | Medium | **HG-14 evidence chain broken** (HW_MEAS only). `meas_f4.c` `meas_start()` restarts the heartbeat DMA (`DMA2_Stream5` → `s_ni.heartbeat_t_us`) and the PUL stamp ring at boot; NOINIT op 5 reports `heartbeat_t_us` live and the last PUL stamp as `ring[(stamps_total − 1) % RING]` with the *new* boot's write index. After an IWDG reset the pre-reset heartbeat is overwritten within 100 µs and the pre-reset last PUL stamp cannot be addressed, so "last PUL − hang start ≤ 100 ms" and "time to reset" (plan §6.1 MT-4, HG-14) cannot be read back. The twin's DIAG_MEAS model has the same w2 semantics (OBS-M2-04). | In `meas_start()`, if the magic is valid, snapshot {last PUL stamp (DMA NDTR / lap count kept in `.noinit` or the newest stamp before the DMA restart), heartbeat, hang start} into `.noinit` "previous boot" words before restarting the DMAs; NOINIT returns them (ICD Appendix C wording by C). | A, C |

### 4.2 Observations

| ID | Observation | Addressee |
|---|---|---|
| OBS-M2-01 | OI-FW-39 answered in plan v0.4: the controlled-stop distance is counted from the deceleration commit (first lengthened period); the FW's `ceil(v²/2a) + 1 preloaded step` is compliant on that reference. | A, C (closed) |
| OBS-M2-02 | **Un-homed JOG bound is per jog start**: after MOVE_DONE SOFT_LIMIT at `home.max_travel_um`, the next JOG refresh of a held button starts a new jog from there (seen in `test_jog_unhomed_bound_and_reversal`), so the un-homed travel limit does not bound a held jog button. Per spec text ("from the start point"); intent to be confirmed: SW must not re-issue JOG after MOVE_DONE without a new press, or the FW keeps the origin until JOG 0 / a stop. | Orchestrator, B, C |
| OBS-M2-03 | E-stop EXTI handler reads the pin level after clearing PR: an opening shorter than the ISR entry gives no HAL reaction and is not latched if closed again at the next tick. Filtered by the τ ≤ 10 µs input RC; informational. | A (informative) |
| OBS-M2-04 | Twin DIAG_MEAS model: NOINIT magic 0 until the first clear (target: set at boot) and w2 = live time (REQ-C-M2-12). | C |
| OBS-M2-05 | `03_SW/tests/integration/test_twin_m1_fw.py:353` still skips 4 stop-timing tests "verified in M2" — un-skip now that motion exists. | C |
| OBS-M2-06 | ALM chatter exactly at the 1 kHz poll rate aliases to "never active" (twin, phase-locked); the start-block is still fail-safe at the first active sample; the test uses a non-synchronous 0.73 kHz chatter. Informational for HG-16. | A (informative) |
| OBS-M2-07 | Validator suites aligned to ICD v0.6 (OI-FW-36 closed): 27 commands incl. DIAG_MEAS, `icd_version` from `proto_gen.h`, vectors regenerated (0.6, 507 check vectors, 184 frames), D-40 b ceil tolerance, D-40 d load-limit oracle, `test_both_limits_wiring_fault` per D-40 a. | Orchestrator |

### 4.3 Code review summary (SRS v0.5.2 / ICD v0.6)
Reviewed: `core/{safety,motion,cmd,afe,link}.c`, `pure/{latches,homing,drvmon,loadlim,ramp,stepgen.h,stop_sniff,inputs.h,
afe_rate,hx711_*}`, `hal/f446/{exti,step_tim2,meas_f4}.c`. Stop policy, latches, EVENT order, PAUSED / RESUME, the
sniffed-stop hold (DEF-P1-04), limit backup path, homing phase / drift logic, driver monitor, load-limit regrow (D-40
d) and the command checks match the ICD; the safety handlers are RAM-resident and byte-identical in the measurement
images. Findings: DEF-M2-01, DEF-M2-02, OBS-M2-02/03. NFR-007 static analysis (FW_design §9.8) accepted as analysis
only; the target value needs DWT (C5).

## 5. Plan follow-ups done in this round
FW_test_plan v0.4: D-40 a/b/d, D-41 / D-42 in the HG list (HG-04/07/10/17/20/21/22/24 revised, HG-32 new, R-6
superseded, R-9 closed, R-10 new), §6.8 bench safety procedure (PO approval), OI-FW-39 answer, §8.5 request status
(REQ-A-M2-07/08, REQ-C-M2-12 new). New suites: `test_val_loadlim`, `test_val_m2pure`, twin
`test_val_twin_m2_sources.py`, `_cmds.py`, `_meas.py`, `static/check_meas_build.py`, `check_static.py` S-14/S-15.

## 6. Re-test after the M2 close-out (2026-10-04)

FW under test: commit `4e9989e` (DEF-M2-01/02 fixes, OI-FW-37 DWT statistics, D-43 b un-homed window, CR-03 / OI-18
`drv.k1_check_enable`, D-42: the FW never reads ENA back) with the ICD **v0.7.1** generated headers in the working
tree (`PROTO_ICD_VERSION` 0.7.1, dict 5, 48 parameters, hash 0xB7B0263F, state_schema 3). `02_FW/src/**` unchanged by
the validator. All native runs used a private `PLATFORMIO_BUILD_DIR` in the validator scratchpad (standing rule). One
earlier run in the shared `.pio/build/native` collided with another role's run (3 suites ERRORED on file locks, not
FW failures); it was discarded and repeated.

### 6.1 Inspection of the fixes
- **DEF-M2-01 — closed.** `step_tim2.c` `halt_hw()` now stops the counter first, then counts a pending update
  (`UIF` set) once and clears it; the pending ISR then returns at its UIF check. The halt decisions use the period
  that is really running (`ccr_running()`: the preloaded one after an un-served update) and skip the decision when
  the counter was already stopped by OPM; `TIM2_IRQHandler` guards the OPM-stop bookkeeping with `s_running`. Cases
  checked: (a) update served → unchanged behaviour; (b) update pending, no pulse in flight → counted once by the halt;
  (c) update pending, next pulse in flight → OPM, the pending ISR counts the first pulse, the final update the
  second; (d) OPM already stopped the counter, ISR pending → counted by the halt, ISR returns; (e) TRUNCATE with an
  update in the 2-cycle window between force-inactive and CEN clear → counted, `cut` true → POS_UNCERTAIN (± 1 step
  allowed). A's `test_impl_steptim` (register-level TIM2 model, every CNT swept for stop_now / abort; the old source
  fails 6/7) passes. Target confirmation: HG-09.
- **DEF-M2-02 — closed.** `meas_start()` snapshots, when the `.noinit` block is valid, the previous boot's newest PUL
  stamp (`ring_newest()`: first descending step of the time-ordered ring, signed modular compare), last heartbeat and
  hang start into `prev_*`, counts the boot and clears the rings before the DMAs restart; NOINIT w4…w8 report them
  (ICD v0.7.1 Appendix C). Twin dry run `test_hang_noinit_readback`: HANG while moving → IWDG; after the reboot
  w4 = 1, previous last PUL 46.8 ms and reset 46.8 ms after the hang start (≤ 100 ms), w8 = 1; a pin reset keeps the
  record (w8 = 2); sel 1 clears it.
- **OI-FW-37** — DWT section statistics present (`meas_dwt.h`, 23 sections, op 9); the macros are empty unless
  `HW_MEAS_DWT`. TC-SYS-009-02 extended per the plan §6.3 exception: in `_meas_dwt` the HAL objects that include
  `meas_dwt.h` (exti, hx711_f4, step_tim2, sys_f4, time_tim5, uart2_dma, meas_f4) and `main.cpp.o` may differ; core /
  pure / gen objects identical → PASS. `_meas` vs release: 47/49 identical (only meas_f4, build_id) → PASS.
- **D-43 b / OBS-M2-02 — closed.** New `test_unhomed_window_fixed_origin_d43b`: three separate un-homed jogs never
  pass origin + `home.max_travel_um`; JOG toward the reached bound → E_RANGE 0; away accepted.
- **CR-03 / OI-18 — verified.** New `test_cr03_defaults_sense_and_k1_off`: defaults false; DRV_PWR input ignored
  (reads 1, ENABLE accepted); E-stop held 1 s with power present → no K1_WELDED, no DRIVER_POWER; with only the sense
  enabled still no K1_WELDED. The SAF-FW-024/025 and FW-SW-005 TCs now enable the optional sense (and the K1 check)
  by SET + SAVE + REBOOT, as an installation with a presence sense would; the 18 v0.6-assumption failures were all
  resolved this way, none was a FW defect.
- **D-42** — no ENA read-back in `src/`; wiring v0.5 §2.1 diode OR, J-STIM 220 Ω, multimeter points M-1…M-9 and
  C-25 / C-26 match plan §6.5 / §6.8.

### 6.2 Evidence (re-test)
| # | Command | Result |
|---|---|---|
| R-1 | `pio test -e native -f "test_impl_*"` (private build dir) ×2 | **160/160 ×2** (incl. `test_impl_steptim`) |
| R-2 | `gen_val_vectors.py` (state_schema 3; loadlim 13 cases incl. `regrow_clear_without_latch_keeps_reference`) + `pio test -e native -f "test_val_*" -v` | **9 suites, 34/34**; anti-skip: check 520, frames 184, streams 11, corpus 100 000, units 660, bits 37, loadlim 13 (58 steps), sniff 2 000, rate 500, motion 37 |
| R-3 | same with corpus seed 2, `VAL_SEED=4242` | **34/34** |
| R-4 | `pytest 02_FW/test/twin` fixed / random (`--randomly-seed=2026105`, `VAL_SEED=73`) | **193 passed ×2** (v0.6 round: 190; + D-43 b, CR-03, DWT-op tests) |
| R-5 | `VAL_WALK=10000` differential walk | **9 987 compared, 13 unstable (re-sampled), 0 mismatches** |
| R-6 | `VAL_STOPS=10000 VAL_SEED=21` random stops | **pass** (count integrity exact) |
| R-7 | `pytest 03_SW/tests/integration` fixed / random | run 1: 61 passed + 2 setup errors (the shared `fw_twin.exe` was rebuilt by another role at that moment); repeated: **63 passed**; random seed 1005: **63 passed** — the 4 former "verified in M2" skips now run (OBS-M2-05 closed) |
| R-8 | `pytest 00_System/tools/tests` | **904 passed** |
| R-9 | `pio run -e nucleo_f446re -e nucleo_f446re_meas -e nucleo_f446re_meas_dwt` | **SUCCESS ×3**, check_map PASS; release flash 44 112 B / RAM 8 672 B |
| R-10 | `check_meas_build.py` (TC-SYS-009-02) | **PASS** (§6.1) |
| R-11 | `check_static.py` | **15/15 PASS** (S-09 generators `--check` exit 0) |

### 6.3 Validator-side updates in this round
`gen_val_vectors.py` (state_schema 3 / `unhomed_origin_um`), `test_val_check` (22 state words), `test_val_nvm` and
`test_get_info_fields` (48 parameters), `latch_ref.LoadLimit` + `test_val_loadlim` (ICD v0.7: a FAULT_CLEAR takes a
new reference only when it clears a latched LOAD_LIMIT — modelled as the core gate), `vhelp_m2.enable_power_sense()`,
twin tests updated for the CR-03 defaults, `test_val_twin_m2_meas.py` on the v0.7.1 NOINIT record + the DWT op,
`check_meas_build.py` §6.3 exception. FW_test_plan v0.4.1 (§9).

### 6.4 Remaining observations
| ID | Observation | Addressee |
|---|---|---|
| OBS-M2-08 | `ring_newest()` uses a signed modular compare: valid while the stamps of one boot kept in the ring span < 2³¹ µs (35.8 min) — always the case for a running axis; informative | A |
| OBS-M2-09 | Shared build / twin directories collide between roles (native ERRORED, integration setup errors). The standing rule (private `PLATFORMIO_BUILD_DIR`) is adopted; the integration suite still uses the shared `fw_twin.exe` | C |
