# Bird Bend Stand — Firmware test report, M3 gate (FW side)

| Item | Value |
|---|---|
| Doc | `02_FW/docs/FW_test_report_M3.md` |
| Gate | **M3** (SW application; FW side: FW-MOT-006 pulled forward by D-44, M3 fixes, HW-gate preparation), FW_test_plan **v0.4.2** |
| Date | 2026-10-05 |
| Author | Validator E — FW (independent of Implementer A) |
| FW under test | commit `2d36eec` (M3 implementation, FW_design v0.7); `02_FW/src/**` unchanged since; images `nucleo_f446re`, `_meas`, `_meas_dwt` built from it |
| Interfaces | SRS v0.6.2, ICD **v0.7.3** (`PROTO_ICD_VERSION` 0.7.3, PROTO 1.0, PAYLOAD 1), `params.yaml` dict_version **6**, `PARAM_DICT_HASH` **0xF8BCDCB8**, 48 parameters; DECISIONS D-01…D-45 |
| Tools | host gcc 13.1 (CLion MinGW), PlatformIO 6.2.0 (arm-none-eabi-gcc 12.3), Python 3.14.7, pytest 9.1.1 + pytest-randomly; FW twin built privately (`VAL_TWIN_BUILD_DIR` / `BEND_TWIN_BUILD_DIR` in the validator scratchpad), private `PLATFORMIO_BUILD_DIR` for every pio run |
| Constraints | **D-06: no hardware** — target-only parts stay open (HG list, plan §6.5). No commits. |

## 1. Verdict

**GO WITH CONDITIONS (M3-C1…C4; M3-C5 closed).** FW-MOT-006 MOVE_UNTIL_LOAD passes TC-FW-MOT-006-01 including the ICD v0.7.3
§5.4 execution rules (a)–(e) and the shared vectors (`mul_*`, `immediate_stops`, `threshold_in_controlled_stop`).
DEF-M3-01 is **closed**: the reproducer passes 3/3, and the HIL dry run passes with the stop-guard workaround
removed. DEF-HG-01 is **closed by inspection**; it is confirmed on the target at HG-29 d. My suites are updated
for dict 6 / D-45 e. The Integrator's review findings R-HIL-01…03 are fixed. Every host-verifiable test passes
twice (fixed + random order), with no skip, no ignore and no xfail left.

| # | Condition | Owner | Closure |
|---|---|---|---|
| M3-C1 | Carried M2 F1: +H parts open under D-06 (HG list v0.4.2, 32 items), incl. the D-42 hardwired ENA cut (HG-10 c/d/e, HG-20) | Orchestrator / E | PO-approved HW gate |
| M3-C2 | Carried M2 F2: HG-18 DWT check of A's static NFR-007 bounds (E-stop handler 1.15 µs vs ≤ 1 µs, step ISR 2.4 µs vs ≤ 2 µs) | A, E | HW gate |
| M3-C3 | Carried M2 F3, now with the D-45 deltas: PO approval of the bench safety procedure §6.8 (window 5…55 mm next to START). The approval is then recorded as a dedicated *approved* `D-06-GATE-…` row (runbook §1) | Orchestrator → PO | before HG-10 a |
| M3-C4 | Target confirmation of FW-MOT-006 on silicon. A threshold stop uses the load path of SAF-FW-002, so HG-12 runs 10 extra MOVE_UNTIL_LOAD trials on the spring (≤ 200 µs from the deciding DOUT edge to the last PUL); these are implemented in `hil_procs.hg12_mul` and pass 10/10 in the twin dry run. DEF-HG-01 is confirmed with HG-29 d / HG-02 c min ≈ max. | E | HW gate |
| M3-C5 | Integrator review items R-HIL-04…06 — **closed** (§6). | E | closed 2026-10-05 |

## 2. Evidence (commands and counts)

| Run | Command (private build / twin dirs) | Result |
|---|---|---|
| Validator vectors v0.7.3 | `.venv\Scripts\python 02_FW\test\val_oracles\gen_val_vectors.py` | gate passed (icd 0.7.3, hash 0xF8BCDCB8); check 543, motion 39 cases (incl. `mul_*`, oracle re-derived), loadlim 13, corpus 100 000 |
| Validator Unity suites ×2 | `pio test -e native -f "test_val_*"` | **34/34** ×2 (check, codec, flags, link, loadlim, m2pure, nvm, ramp, units) |
| Full native (A + E) | `pio test -e native` | **210/210** |
| Validator twin suites | `pytest 02_FW/test/twin -p no:randomly` / `-p randomly` | **213 passed** ×2, 0 skip / xfail (new: `test_val_twin_m3_mul.py` 16, `test_val_twin_m3_sniffhold.py` 3) |
| Differential command walk | `VAL_WALK=10000 pytest …::test_command_check_differential_walk` | PASS (10 000 steps, `ref_cmdcheck` vs FW) |
| Random stops | `VAL_STOPS=2000 pytest …::test_random_stops_count_integrity` | PASS (count integrity exact, ±1 only with POS_UNCERTAIN) |
| Images | `pio run -e nucleo_f446re -e nucleo_f446re_meas -e nucleo_f446re_meas_dwt` | SUCCESS ×3, check_map PASS ×3; release flash 45 464 B / RAM 8 672 B; meas 48 548 / 41 544; meas_dwt 49 400 / 43 880 |
| TC-SYS-009-02 | `check_meas_build.py` (now honours `PLATFORMIO_BUILD_DIR`) | **PASS**: meas 49/51 objects identical (only build_id, meas_f4 differ); meas_dwt only the allowed HAL objects + main; `HW_MEAS` confined to meas_f4.c |
| Static | `check_static.py --build-dir <private>/nucleo_f446re` | **15/15 PASS** |
| HIL unit tests ×2 | `pytest 00_System/tools/hil/tests` | **39/39** ×2 |
| HIL twin dry run | `hil_session.py --twin --out <dir>` (full counts) | 44 steps: **0 FAIL, 0 INCONCLUSIVE, 0 ERROR**. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A. PARTIAL and OPEN mean only manual or target-only parts remain. Report: `00_System/tools/hil/dryrun/HG_dryrun_twin_20261005.md` |

## 3. Coverage

### 3.1 FW-MOT-006 — TC-FW-MOT-006-01 (T + U vectors), `02_FW/test/twin/test_val_twin_m3_mul.py`
Samples are scripted with `afe raw_script`, so each threshold crossing lands on a known sample. Step periods are
reconstructed from the PUL edge log and compared with the shared vectors through the validator's oracle (`ramp_ref`).

| Case | Result |
|---|---|
| Bound reached: MOVE_DONE BOUND exactly at the bound (25 000 µm / 20 000 steps), periods = `mul_to_bound_5mm` (±1 tick, total ±ceil(N/1000)) | PASS |
| `cmp` 0 at samples 6 / 15 / 40 (equality = beyond): LOAD_THRESHOLD, periods = prefix of the base case (`immediate_stops` rule), no PUL edge > 200 µs after the deciding sample, every pulse counted | PASS ×3 |
| `cmp` 1 while unloading (−x) | PASS |
| Already beyond (cmp 0/1, equal and beyond): LOAD_THRESHOLD at once, no pulse | PASS ×4 |
| Refusals: NOT_HOMED; v = 0 / > `v_max_load_um_s` → E_RANGE 4; bound = x / outside the soft limits → E_RANGE 0; `raw_stop` → 12; `cmp` → 16; PAUSED → E_STATE PAUSED; second command → E_BUSY 1 | PASS |
| AFE stall while moving: STOPPED(AFE_FAULT), never LOAD_THRESHOLD (fallback frames are not samples) | PASS |
| (a) load limit first: FAULT_SET(LOAD_LIMIT, value = raw), one STOPPED(LOAD_LIMIT), MOVE_DONE STOPPED | PASS |
| (b) threshold during a PAUSE deceleration (a_stop 10 mm/s²): one STOPPED(PC_PAUSE), MOVE_DONE STOPPED, deceleration cut short (100 of 160 steps), periods = oracle `mul_stop_in_cruise` with the actual stop step (one-period preload latency, ICD §6.5); no POS_UNCERTAIN | PASS |
| (c) bound within one step (100 steps/mm, +4 µm): BOUND / LOAD_THRESHOLD at once, no pulse | PASS |
| (d) LOAD_THRESHOLD is not a stop source: no STOPPED / VALID_CLEARED, VALID + HOMED kept, no POS_UNCERTAIN | PASS (in the cmp 0 cases) |
| (e) MOVE_UNTIL_LOAD + HALT in one burst: MOVE_DONE STOPPED, STOPPED(PC_HALT), no pulse after the HALT frame | PASS |
| Vector sanity: every `immediate_stops` / `threshold_in_controlled_stop` row names an existing base case, `after_step` lies inside it, and the base periods are reproduced by the oracle | PASS |
| U: `test_val_ramp` replays the `mul_*` planner cases (motion.txt, refused unless the oracle reproduces them) and `test_val_check` replays the 21 MOVE_UNTIL_LOAD check vectors | PASS |

Target part (load-path timing on silicon) → M3-C4. The HG-12 procedure now includes it; the twin dry run gives 10/10 LOAD_THRESHOLD, with the deciding DOUT edge to the last PUL ≤ 200 µs.

### 3.2 M3 fixes
| Item | Evidence | Status |
|---|---|---|
| DEF-M3-01 stale sniffed-stop hold (Medium) | `test_val_twin_m3_sniffhold.py` (STOP 0 / HALT / PAUSE, motion start < 20 ms after the stop) 3/3 PASS, strict xfail removed. Inspection of `stop_sniff.c` / `link.c`: frames dispatched before the sniffer saw them are queued (dq, 4 entries, aged by `SNIFF_HOLD_MAX_MS`) and their later sniff hit is ignored. The HIL dry run passes with the stop guard off | **closed** |
| DEF-HG-01 first PWM-input capture (Medium, measurement image) | Inspection of `meas_f4.c`: `s_pwm_first` is set in `probe_arm`. The first CC2 capture after arming only starts the measurement, so the 2nd rising edge gives the period and the high width of the first full pulse. The twin model mirrors it (OBS-E-HG-05 fixed): dry-run PWM min = max at 10 / 40 / 50 kHz | **closed (host)**; target confirmation M3-C4 |
| D-45 e defaults (12.5 + 12.5 µs, 40 kHz) | `test_pulse_timing_at_cap_and_dir_setup` parametrised: dict defaults (cap 40 kHz → v_limit 20 mm/s at 2000 steps/mm) and explicit 10 + 10 µs / 50 kHz (H3-legal, `rule_h3_old_rate_ok`); widths / periods / DIR setup per the oracle | PASS |

### 3.3 Regression
The twin suites (M1/M2, 194 tests) and the Unity suites pass unchanged except for the dict-6 updates listed in §5.
The boot feature set accepts FEAT_MOVE_UNTIL_LOAD (OI-FW-44).

## 4. Findings

| ID | Severity | Item | Addressee |
|---|---|---|---|
| OBS-M3-01 | Low | `meas_f4.c` PWM_INPUT has no overflow indication: a period > 65 535 probe ticks (e.g. a ramp period at PSC 0) wraps silently into min / max. The HIL scripts arm only during cruise at a fitting PSC. A flag (UIF while `s_pwm`) would make the mode self-checking | A (optional) |
| OBS-M3-02 | Info | In a controlled stop on the ISR path the first planned deceleration period is not emitted: period k was already preloaded at the cruise value, so the output equals the oracle with period k = cruise. This matches ICD §6.5 ("takes effect at the latest with the next step-timer update"). The shared vector `mul_stop_in_cruise` is the planner sequence; a twin replay must apply this one-period substitution | C (note in `motion_vectors.json` `_comment`, optional) |
| OBS-M3-03 | Info | R-HIL-04…06 were referenced in STATUS without text — wording received from the Integrator and applied (§6) | closed |

No new DEF.

## 5. Changes in this round (Validator E files only)
- `02_FW/test/twin/test_val_twin_m3_mul.py` (new, 16 tests: TC-FW-MOT-006-01, rules a–e, vector sanity).
- `02_FW/test/twin/test_val_twin_m3_sniffhold.py`: strict xfail removed (DEF-M3-01 fixed).
- `02_FW/test/twin/test_val_twin_m2_cmds.py::test_steps_per_mm_rules`: the v_limit expectation now comes from `motion.max_step_rate_hz` (dict 6: 40 kHz).
- `02_FW/test/twin/test_val_twin_m2_motion.py::test_pulse_timing_at_cap_and_dir_setup`: parametrised for the dict-6 defaults and for an explicit 10 + 10 µs / 50 kHz setting.
- `02_FW/test/twin/test_val_twin_boot.py`: feature set with MOVE_UNTIL_LOAD (OI-FW-44, earlier this round).
- `02_FW/test/static/check_meas_build.py`: honours `PLATFORMIO_BUILD_DIR` (private builds).
- `00_System/tools/hil/`:
  - R-HIL-01: the dict hash now comes from `gen_params`.
  - R-HIL-02: the approval must be a dedicated *approved* table row with a whole-token match (`approval_row`).
  - R-HIL-03: the PSU-off block takes an explicit `timing`; the 50 kHz trials set 10 + 10 µs / 50 kHz, and HG-08 a runs a defaults series (a1) and an explicit series (a2).
  - HG-12 adds 10 MOVE_UNTIL_LOAD threshold trials (`hg12_mul`, M3-C4).
  - The OBS-E-HG-01/02 twin special cases are removed (model fixed in v0.7.3).
  - The stop guard is off.
  - Unit tests: 39.
  - Runbook v0.2.
  - Dry-run report replaced.
- `02_FW/docs/FW_test_plan.md` **v0.4.2**: D-45 (a)–(g) in §6 (HG-02/04/08/10/29, §6.8 P-1), TC-FW-MOT-006-01 moved to M3 with rules (a)–(e), new §6.9 on the HIL realisation, binding inputs updated.

## 6. Close-out of M3-C5 (Integrator review R-HIL-04…06, 2026-10-05)
| Item | Change in `00_System/tools/hil/` | Status |
|---|---|---|
| R-HIL-04 (Low) — twin compensations made obsolete by the v0.7.3 model fixes | stimulus spacing: interval = hold + delay for twin and board (`k = 2` removed); HG-13: target reference point (start bit of byte 0, frame time at the line rate) for the twin too; CCR3 = 0 is "no capture" everywhere — the zero-latency fallback in the E-stop trials (HG-10) and in HG-04 is removed (twin captures now report ≥ 1 tick: ENA 0.1 µs in the dry run) | closed |
| R-HIL-05 (Low) — `Link.stop_guard_ms` | default 0 since DEF-M3-01 was verified fixed (the option stays, 25 ms re-enables it) | closed |
| R-HIL-06 (Info) — ICD version in docstrings | `hil_link.py` cites ICD v0.7.3 (module docstring, DIAG_MEAS tables); no v0.7.1 citation left in the package | closed |

Re-run after the change: HIL unit tests 39/39 ×2 (fixed + random order); quick twin dry run (`--quick`, 44 steps):
0 FAIL / 0 INCONCLUSIVE / 0 ERROR — 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A (same picture as the full run of §2).
