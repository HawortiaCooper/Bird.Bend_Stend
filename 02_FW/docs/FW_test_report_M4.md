# Bird Bend Stand — Firmware test report, M4 gate (FW side)

| Item | Value |
|---|---|
| Doc | `02_FW/docs/FW_test_report_M4.md` |
| Gate | **M4** (sequencer, load-target steps, report — SW scope; FW side: D-47 a link watchdog + full FW regression), FW_test_plan **v0.4.3** |
| Date | 2026-10-05 |
| Author | Validator E — FW (independent of Implementer A) |
| FW under test | commit `5daf3b8`; FW change since M3: `02_FW/src/core/safety.c` (D-47 a, FW_design §5.7.1 / §9.10 row 12); `02_FW/src/**` unchanged since |
| Interfaces | SRS v0.6.3, ICD **v0.7.4** (`PROTO_ICD_VERSION` 0.7.4, PROTO 1.0, PAYLOAD 1), `params.yaml` dict_version 6, `PARAM_DICT_HASH` 0xF8BCDCB8, 48 parameters; DECISIONS D-01…D-48 |
| Tools | host gcc 13.1 (CLion MinGW), PlatformIO 6.2.0 (arm-none-eabi-gcc 12.3), Python 3.14.7, pytest 9.1.1 + pytest-randomly. Private dirs: `VAL_TWIN_BUILD_DIR` / `BEND_TWIN_BUILD_DIR` / `PLATFORMIO_BUILD_DIR` in the validator scratchpad |
| Constraints | **D-06: no hardware** — target-only parts stay open (HG list, plan §6.5). No commits. |

## 1. Verdict

**GO WITH CONDITIONS (M4-C1…C4: the hardware-gate items carried from M3).**

D-47 a is verified on A's FW in the twin. A PC silence of at least `safety.link_timeout_ms` clears VALID in every
motion state: VALID_CLEARED (arg LINK_WDG) is sent only on a 1 → 0 change, there is no stop when not moving, and the
controlled stop with LINK_WDG / STOPPED / MOVE_DONE happens only while moving. A silence of timeout − 1 ms never trips.
This holds for all 32 cases of `linkwdg_vectors.json`, and each case's expectation is first re-derived by my own oracle.

The full FW regression passes: every host test twice, with no skip, no ignore and no xfail. No new defects.

| # | Condition | Owner | Closure |
|---|---|---|---|
| M4-C1 | Carried M3-C1: +H parts open under D-06 (HG list v0.4.3, 32 items), incl. the D-42 hardwired ENA cut | Orchestrator / E | PO-approved HW gate |
| M4-C2 | Carried M3-C2: HG-18 DWT check (E-stop handler 1.15 µs vs ≤ 1 µs, step ISR 2.4 µs vs ≤ 2 µs) | A, E | HW gate |
| M4-C3 | Carried M3-C3: PO approval of §6.8 (with the D-45 deltas), recorded as a dedicated *approved* `D-06-GATE-…` row | Orchestrator → PO | before HG-10 a |
| M4-C4 | Carried M3-C4: FW-MOT-006 load-path timing (HG-12 MOVE_UNTIL_LOAD trials) and DEF-HG-01 (HG-29 d / HG-02 c) confirmed on the target | E | HW gate |

## 2. Evidence (commands and counts)

| Run | Command | Result |
|---|---|---|
| Validator vectors v0.7.4 | `gen_val_vectors.py` | gate passed (icd 0.7.4, hash 0xF8BCDCB8); check 543, motion 39, loadlim 13, corpus 100 000 |
| Validator Unity suites ×2 | `pio test -e native -f "test_val_*"` | **34/34** ×2 |
| Full native (A + E) | `pio test -e native` | **211/211** |
| Validator twin suites ×2 | `pytest 02_FW/test/twin -p no:randomly` / `-p randomly` (seed 2536468287) | **246 passed** ×2, 0 skip / xfail. New: 33 in `test_val_twin_m4_linkwdg.py` (oracle check + 32 replays) |
| Images | `pio run -e nucleo_f446re -e nucleo_f446re_meas -e nucleo_f446re_meas_dwt` | SUCCESS ×3, check_map PASS ×3. Flash / RAM: release 45 536 / 8 672 B; meas 48 620 / 41 544 B; meas_dwt 49 472 / 43 880 B |
| TC-SYS-009-02 | `check_meas_build.py` | **PASS** (safety handlers / core byte-identical, `HW_MEAS` confined) |
| Static | `check_static.py --build-dir <private>/nucleo_f446re` | **15/15 PASS** |
| HIL unit tests ×2 | `pytest 00_System/tools/hil/tests` | **39/39** ×2 |
| HIL quick twin dry run | `hil_session.py --twin --quick` | 44 steps: **0 FAIL / 0 INCONCLUSIVE / 0 ERROR**. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A. PARTIAL and OPEN mean only manual or target-only parts remain |

## 3. Coverage

### 3.1 SAF-FW-015 / D-47 a — TC-SAF-FW-015-01 extension (`02_FW/test/twin/test_val_twin_m4_linkwdg.py`)

**Oracle check.** `val_oracles/latch_ref.link_watchdog` is written from the SRS v0.6.3 SAF-FW-015 text, not from the
Integrator's `ref_linkwdg.py`. It reproduces the `expect` block of all 32 vector cases (trip, stop, valid_after,
LINK_WDG status, EVENT multiset, after-frame EVENTs). Result: PASS.

**Replay setup.** Each case runs in its motion state:
- NOT_ENABLED at boot;
- IDLE after ENABLE;
- MOVE_ABS and MOVE_UNTIL_LOAD at 1 mm/s.

The run uses `safety.link_timeout_ms` 200 / 1000 ms and VALID before = 0 / 1. SET_VALID is the last command frame.
The silence is measured on the wire from the end of that frame:
- 999 / 199 ms: never trips;
- 1002 / 202 ms: trips. These cases add +60 ms in the same silence period so the controlled stop can end.

**What is checked.**
- The EVENT multiset whose FW t_us falls inside the silence equals the vector exactly. In particular there is
  exactly one VALID_CLEARED, only when VALID was 1, and no stop EVENTs when not moving.
- From the last DATA frame of the silence:
  - its VALID flag;
  - its LINK_WDG status bit;
  - moving cases that tripped: stopped with the driver still enabled;
  - moving cases below the timeout: still moving.
- After the next valid command frame: LINK_RESTORED appears exactly when the vector expects it, and LINK_WDG is
  cleared in every later DATA frame and status.

Result: **32/32 PASS**.

`02_FW/src/core/safety.c` was also inspected. Step 5 keeps the moving-only stop, LINK_WDG and EVENTs. A separate
check, `silence ≥ timeout → vclear(SC_LINK_WDG)`, runs in every state and is a no-op while VALID is already 0, so
the EVENT is sent only on a 1 → 0 change.

### 3.2 Regression adjustments (D-47 a side effects)
- `test_val_twin_link.py::test_set_valid_boundary_across_wrap` (TC-FW-CMD-002-01) used to leave the link silent for
  1.3 s with VALID = 1. Since D-47 a the watchdog then correctly clears VALID. The test now sends a PING heartbeat
  every 200 ms (SAF-SW-003 behaviour) and passes. Plan v0.4.3 records the rule: any twin TC with more than 1 s of PC
  silence keeps a heartbeat.
- `test_val_twin_m3_mul.py` docstring now cites ICD v0.7.4 (the rules are unchanged since v0.7.3).

## 4. Findings

| ID | Severity | Item | Addressee |
|---|---|---|---|
| OBS-M4-01 | Info | The response to the frame that restores the link (e.g. GET_STATUS) still shows status bit LINK_WDG. The bit and EVENT LINK_RESTORED are cleared / sent in the next 1 kHz tick (`safety.c` step 5 compares `cmd_rx_count`). ICD §9.1 says only "at the next valid command frame". A PC that shows LINK_WDG from that one response sees it for ≤ 1 ms longer. Suggest an ICD note ("cleared within 1 ms after the next valid frame; the response to that frame may still carry it") | C (ICD wording), B/D (informative) |

No DEF.

## 5. Changes in this round (Validator E files only)
- `02_FW/test/twin/test_val_twin_m4_linkwdg.py` (new: oracle check + 32-case replay).
- `02_FW/test/val_oracles/latch_ref.py`: `link_watchdog()` oracle (SRS v0.6.3 SAF-FW-015).
- `02_FW/test/twin/test_val_twin_link.py`: heartbeat in TC-FW-CMD-002-01.
- `02_FW/test/twin/test_val_twin_m3_mul.py`: docstring ICD v0.7.4.
- `02_FW/docs/FW_test_plan.md` **v0.4.3**: TC-SAF-FW-015-01 extended (D-47 a, vectors replay), heartbeat rule, binding inputs.
