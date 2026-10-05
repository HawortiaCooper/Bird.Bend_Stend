# HG1 hardware gate — TWIN DRY RUN report

> **Dry run against the FW host twin (`Twin(hw_meas=True)`, Integrator's DIAG_MEAS model).** It exercises the HIL procedures, the DIAG_MEAS evidence chain and the budget evaluation. **It is not hardware evidence** — D-06 stays in force; every HG item is decided on the board at the PO-approved gate. Operator answers marked *dry-run default* are scripted placeholders; *twin-observed* values come from the twin's world model.

| Field | Value |
|---|---|
| mode | twin |
| date | 2026-10-05 02:06 |
| approval | — (twin, D-06) |
| port | — (twin) |
| board_uid | – |
| fw_build | host |
| icd | 0.7.2 |
| python | 3.14.7 |
| host | EXmachina007 |
| seed | 20261005 |
| quick | False |
| twin_exe | private per-run copy of A's FW twin (BEND_TWIN_BUILD_DIR in the validator scratchpad) |

## Summary

| Item | Title | Verdict | PASS | FAIL | INCONCL. | open | Requirements |
|---|---|---|---|---|---|---|---|
| S-00 | Session start: persons, image commit, board identity, DIP applied | **PARTIAL (MANUAL open)** | 2 | 0 | 0 | 1 | SYS-009, D-06 |
| IMG-MEAS | PO flashes HW_MEAS; image verified | **PASS** | 3 | 0 | 0 | 0 | TC-SYS-009-02 |
| HG-01 | Board identity, solder bridges, MH header continuity (C-01) | **OPEN (MANUAL)** | 0 | 0 | 0 | 8 | SYS-007, FW-PLT-002 |
| HG-19 | Driver DIP sheet (C-19) | **OPEN (MANUAL)** | 0 | 0 | 0 | 2 | SYS-005 |
| HG-20 | E-stop circuit D-41 / D-42 (C-20) | **OPEN (MANUAL)** | 0 | 0 | 0 | 8 | SYS-006, SYS-001 |
| HG-31 | Loopback hygiene (C-24) | **OPEN (MANUAL)** | 0 | 0 | 0 | 2 | SYS-009 |
| HG-29 | Measurement-chain self-test (C-24) | **INCONCLUSIVE** | 15 | 0 | 1 | 3 | SYS-009 |
| HG-03 | VCP 921 600 Bd soak (C-03) | **PASS** | 6 | 0 | 0 | 0 | IF-002, SYS-009 |
| HG-02 | Clock source and PUL frequency (C-02) | **INCONCLUSIVE** | 4 | 0 | 1 | 0 | FW-PLT-002 |
| P2-ENTRY | Phase 2 entry: driver PSU on | **N/A** | 0 | 0 | 0 | 0 |  |
| HG-32 | D-41 / CR-03 configuration (C-26) | **PASS** | 5 | 0 | 0 | 0 | SYS-009, D-41 |
| HG-06 | Opto drive margin (C-06) | **PARTIAL (MANUAL open)** | 4 | 0 | 0 | 6 | SYS-009, SYS-011 |
| HG-10cd | D-42 hardwired ENA cut: MCU in reset, DMM M-1…M-4 (C-25) — before any bypass | **PARTIAL (MANUAL open)** | 3 | 0 | 0 | 8 | SAF-FW-005, SYS-006, D-42 |
| HG-28 | First motion, direction, homing smoke (SYS-009) | **PASS** | 7 | 0 | 0 | 0 | SYS-009, FW-HOM-001/002 |
| HG-07 | ENA enable / disable and settle (C-07) | **PASS** | 4 | 0 | 0 | 0 | FW-MOT-008 |
| HG-08 | PUL / DIR timing (C-08) | **INCONCLUSIVE** | 6 | 0 | 4 | 0 | FW-MOT-001 |
| HG-09 | Step count integrity (C-09) | **PASS** | 9 | 0 | 0 | 0 | SAF-FW-004 |
| BENCH-ENTRY | §6.8 P-1…P-6: E-stop sense bypass (J-STIM), HG-29 e E-stop input | **PARTIAL (MANUAL, TARGET-ONLY open)** | 1 | 0 | 0 | 3 | SYS-009 |
| HG-10a | E-stop reaction, 100 STIM trials (C-10 a) | **PASS** | 6 | 0 | 0 | 0 | SAF-FW-005, SYS-006 |
| BENCH-EXIT | §6.8 P-8: sense restored — before any further motion | **PASS** | 2 | 0 | 0 | 0 | SYS-009 |
| HG-10b | E-stop real presses + slow press, FW with forced ENA (C-10 b/e) | **PASS** | 12 | 0 | 0 | 0 | SAF-FW-005, SYS-006, D-42 |
| HG-11 | Limit reaction (C-11) | **PASS** | 12 | 0 | 0 | 0 | SAF-FW-002 |
| HG-13 | PC STOP / HALT (C-13) | **PASS** | 3 | 0 | 0 | 0 | SAF-FW-002, NFR-003 |
| HG-17 | Input wire break (C-17) | **PARTIAL (MANUAL open)** | 3 | 0 | 0 | 1 | SAF-FW-007 |
| HG-04 | Flash erase vs IWDG, E-stop during SAVE, TX integrity (C-04) | **PARTIAL (TARGET-ONLY open)** | 7 | 0 | 0 | 1 | FW-NVM-002/003, SAF-FW-005/019 |
| HG-14 | Hang -> IWDG (C-14) | **PASS** | 4 | 0 | 0 | 0 | SAF-FW-019 |
| HG-16 | ALM / PEND levels and start-block (C-16) | **PARTIAL (MANUAL open)** | 5 | 0 | 0 | 5 | FW-SW-004, SAF-FW-026 |
| HG-24 | ALM reset (C-16, D-41) | **OPEN (MANUAL)** | 0 | 0 | 0 | 3 | SYS-009, D-28 |
| IMG-DWT | PO flashes HW_MEAS_DWT; image verified | **PARTIAL (NOT MEASURED open)** | 3 | 0 | 0 | 1 | NFR-007 |
| HG-05 | HX711 on silicon (C-05) | **PARTIAL (NOT MEASURED, TARGET-ONLY open)** | 4 | 0 | 0 | 2 | FW-AFE-001/004, FW-TIM-001 |
| HG-18 | Main-loop and ISR budgets — F2 (C-18) | **PARTIAL (NOT MEASURED open)** | 3 | 0 | 0 | 12 | NFR-005/006/007, FW-SW-002 |
| HG-27 | Command response sample (NFR-008) | **PASS** | 4 | 0 | 0 | 0 | NFR-008 |
| IMG-MEAS2 | PO flashes HW_MEAS again; image verified | **PASS** | 3 | 0 | 0 | 0 |  |
| HG-21 | DRV_PWR sense (optional, D-41) | **N/A** | 0 | 0 | 0 | 0 | (SAF-FW-024, FW-SW-005) |
| HG-22 | K1_WELDED (N/A, D-41) | **N/A** | 0 | 0 | 0 | 0 | (SAF-FW-025) |
| HG-23 | Buffer board SN74ACT244 (C-22) | **INCONCLUSIVE** | 14 | 0 | 4 | 4 | SYS-011, SYS-009 |
| GATE-LOAD | SYS-009 gate: prerequisites before the first load | **N/A** | 0 | 0 | 0 | 0 | SYS-009 |
| HG-12 | FW load-limit reaction (C-12) | **PASS** | 3 | 0 | 0 | 0 | SAF-FW-002/008 |
| HG-15 | Reset under load (C-15, D-33 e) | **PASS** | 13 | 0 | 0 | 0 | SAF-FW-018 |
| HG-25 | Speed envelope (SYS-004) | **PASS** | 7 | 0 | 0 | 0 | SYS-004 |
| HG-26 | Home repeatability (FW-HOM-003) | **PASS** | 1 | 0 | 0 | 0 | FW-HOM-003 |
| IMG-REL | PO flashes the release image | **PASS** | 3 | 0 | 0 | 0 | SYS-009 |
| HG-30 | Release-image confirmation (+ HG-32 repeat, HG-31) | **PARTIAL (MANUAL open)** | 19 | 0 | 0 | 1 | SYS-009 (R-4) |

Verdict counts: INCONCLUSIVE 4, N/A 4, OPEN 5, PARTIAL 11, PASS 19

## What stays open after this run

| Item | Check | Kind | Why |
|---|---|---|---|
| S-00 | DIP applied before the first motion | MANUAL | operator input: dry-run default (not evidence) |
| HG-01 | C-01 solder bridges | MANUAL | operator input: dry-run default (not evidence) |
| HG-01 | J-PUL-A series R | MANUAL | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| HG-01 | J-PUL-B series R | MANUAL | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| HG-01 | J-DIR series R | MANUAL | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| HG-01 | J-ENA series R | MANUAL | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| HG-01 | J-EVT series R | MANUAL | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| HG-01 | J-AUX series R | MANUAL | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| HG-01 | J-STIM series R | MANUAL | 220 Ω ± 5 %; operator input: dry-run default (not evidence) |
| HG-19 | DIP setting | MANUAL | D-27 target: 4000 p/rev, CCW, assist on, 86-80/86-118 closed loop; operator input: dry-run default (not evidence) |
| HG-19 | travel calibration vs 800 steps/mm | MANUAL | first travel calibration result is recorded at HG-25 |
| HG-20 | circuit NC | MANUAL | operator input: dry-run default (not evidence) |
| HG-20 | circuit NO | MANUAL | operator input: dry-run default (not evidence) |
| HG-20 | circuit D1 | MANUAL | operator input: dry-run default (not evidence) |
| HG-20 | circuit NOK1 | MANUAL | operator input: dry-run default (not evidence) |
| HG-20 | circuit NOMCU | MANUAL | operator input: dry-run default (not evidence) |
| HG-20 | circuit LABEL | MANUAL | operator input: dry-run default (not evidence) |
| HG-20 | M-5 +5V_CUT | MANUAL | operator input: dry-run default (not evidence) |
| HG-20 | isolation | MANUAL | operator input: dry-run default (not evidence) |
| HG-31 | MH pins not analog | MANUAL | operator input: dry-run default (not evidence) |
| HG-31 | release image with jumpers fitted | MANUAL | confirmed when the release image runs |
| HG-29 | a continuity (HG-01) | MANUAL | HG-01 verdict OPEN (MANUAL) |
| HG-29 | c J-AUX stamps (pulse width = hold) | TARGET-ONLY | J-AUX not modelled in the twin |
| HG-29 | e RC + threshold delay per input | TARGET-ONLY | needs J-AUX on the pin node; not modelled in the twin (E-stop part in the bench block) |
| HG-06 | PUL VOH | MANUAL | operator input: dry-run default (not evidence) |
| HG-06 | PUL I_LED | MANUAL | bring-up ≥ 6 mA; operator input: dry-run default (not evidence) |
| HG-06 | DIR VOH | MANUAL | operator input: dry-run default (not evidence) |
| HG-06 | DIR I_LED | MANUAL | bring-up ≥ 6 mA; operator input: dry-run default (not evidence) |
| HG-06 | ENA VOH | MANUAL | operator input: dry-run default (not evidence) |
| HG-06 | ENA I_LED | MANUAL | bring-up ≥ 6 mA; operator input: dry-run default (not evidence) |
| HG-10cd | c1 MCU in reset + E-stop -> motor free | MANUAL | operator input: dry-run default (not evidence) |
| HG-10cd | d M-1 ENA cut current | MANUAL | ≥ the driver's ENA threshold (ASSUMED ≥ 7 mA, wiring §11.1); operator input: dry-run default (not evidence) |
| HG-10cd | d M-3 PA4 (reset, pressed) | MANUAL | operator input: dry-run default (not evidence) |
| HG-10cd | d M-4 ENA+ (pressed) | MANUAL | operator input: dry-run default (not evidence) |
| HG-10cd | c2 reset + released -> holding (D-13) | MANUAL | operator input: dry-run default (not evidence) |
| HG-10cd | d M-2a enabled + released | MANUAL | operator input: dry-run default (not evidence) |
| HG-10cd | d M-2c no over-current | MANUAL | operator input: dry-run default (not evidence) |
| HG-10cd | d M-3 PA4 never 5 V | MANUAL | operator input: dry-run default (not evidence) |
| BENCH-ENTRY | P-4 J-STIM resistor | MANUAL | operator input: dry-run default (not evidence) |
| BENCH-ENTRY | P-4 PA10 idle (valid low) | MANUAL | operator input: dry-run default (not evidence) |
| BENCH-ENTRY | HG-29 e E-stop input RC delay | TARGET-ONLY | J-AUX not modelled |
| HG-17 | D-42 NO wire break recorded as undetectable | MANUAL | operator input: dry-run default (not evidence) |
| HG-04 | c no gap > 22 µs inside a TX frame (J-AUX = TX) | TARGET-ONLY | J-AUX not modelled in the twin |
| HG-16 | ALM-V-off | MANUAL | dry-run default (not evidence) |
| HG-16 | PEND-V-off | MANUAL | dry-run default (not evidence) |
| HG-16 | ALM-V-on | MANUAL | dry-run default (not evidence) |
| HG-16 | PEND-V-on | MANUAL | dry-run default (not evidence) |
| HG-16 | drv.*_active_level set | MANUAL | dry-run default (not evidence) |
| HG-24 | ALM provoked | MANUAL | dry-run default (not evidence) |
| HG-24 | power-cycle reset works | MANUAL | operator input: dry-run default (not evidence) |
| HG-24 | ENA-toggle reset | MANUAL | dry-run default (not evidence) |
| IMG-DWT | DWT variant | NOT MEASURED | the twin model has no DWT image (DWT not modelled) |
| HG-05 | SCK-high / read time (MT-1 DWT) | NOT MEASURED | needs the HW_MEAS_DWT image (DWT not modelled in the twin) |
| HG-05 | SCK edges per read 25 / 27 / 26 (J-AUX = PD_SCK) | TARGET-ONLY | J-AUX not modelled in the twin |
| HG-18 | main-loop pass (section 0) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | TIM2 step ISR (F2) (section 1) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | E-stop handler EXTI15_10 (F2) (section 2) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | START limit EXTI0 (section 3) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | END limit EXTI1 (section 4) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | CRIT_HALT (PRIMASK) (section 11) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | CRIT_AFE (BASEPRI 0x20) (section 12) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | CRIT_MOTION (BASEPRI 0x20) (section 13) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | hal_step_stop_now (PRIMASK) (section 16) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | hal_step_abort (PRIMASK) (section 17) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | hal_step_set_period_now (PRIMASK) (section 18) | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-18 | step CPU at 50 kHz | NOT MEASURED | DWT not modelled in the twin (w0 = 0) |
| HG-23 | buffer board installed | MANUAL | operator input: dry-run default (not evidence) |
| HG-23 | PUL I_LED (buffer) | MANUAL | operator input: dry-run default (not evidence) |
| HG-23 | DIR I_LED (buffer) | MANUAL | operator input: dry-run default (not evidence) |
| HG-23 | ENA I_LED (buffer) | MANUAL | operator input: dry-run default (not evidence) |
| HG-30 | HG-31 release image with jumpers fitted | MANUAL | operator input: dry-run default (not evidence) |

## Items

### S-00 — Session start: persons, image commit, board identity, DIP applied

Verdict: **PARTIAL (MANUAL open)** · requirements: SYS-009, D-06 · 2026-10-05 02:06:08 → 2026-10-05 02:06:08

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| param dict hash | == 0xB7B0263F | 0xB7B0263F | 0 | – | **PASS** |  |
| protocol 1.0 / payload 1 | == (1, 0, 1) | [1, 0, 1] | 0 | – | **PASS** |  |
| DIP applied before the first motion | D-27 | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| TC-SYS-009-02 / check_meas_build | recorded | see HG-29 f | – | – | **INFO** |  |

<details><summary>Operator steps / answers (3)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| PERSONS | Operator and observer names | (dry run) | dry-run default (not evidence) |
| COMMIT | Git commit of the three images (release / HW_MEAS / HW_MEAS_DWT, one commit) | (dry run) | dry-run default (not evidence) |
| DIP | Driver DIP target setting applied with the driver unpowered (D-27, wiring §10)? | True | dry-run default (not evidence) |

</details>

### IMG-MEAS — PO flashes HW_MEAS; image verified

Verdict: **PASS** · requirements: TC-SYS-009-02 · 2026-10-05 02:06:08 → 2026-10-05 02:06:08

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| build | recorded | host | – | – | **INFO** |  |
| param dict hash | == 0xB7B0263F | 0xB7B0263F | 0 | – | **PASS** | ICD v0.7.1 dict 5 |
| FEAT_HW_MEAS = 1 | measurement image | ['AFE', 'MOTION', 'HOMING', 'MOVE_UNTIL_LOAD', 'NVM', 'TWIN', 'BUTTONS', 'DRV_SIGNALS', 'HW_MEAS'] | – | – | **PASS** |  |
| variant | twin: MEAS \| TWIN_MODEL | ['MEAS', 'TWIN_MODEL'] | – | – | **PASS** |  |

Notes:

- twin: engine restarted with hw_meas=True (stand-in for flashing nucleo_f446re_meas (HW_MEAS))

### HG-01 — Board identity, solder bridges, MH header continuity (C-01)

Verdict: **OPEN (MANUAL)** · requirements: SYS-007, FW-PLT-002 · 2026-10-05 02:06:08 → 2026-10-05 02:06:08

Method: visual + photo; DMM continuity incl. the 1 kΩ / 220 Ω

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| C-01 solder bridges | as pinout.md §5 | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| J-PUL-A series R | 950 <= value ± u <= 1100 ohm | 1000 … 1000 | 5 | 1 | **MANUAL** | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| J-PUL-B series R | 950 <= value ± u <= 1100 ohm | 1000 … 1000 | 5 | 1 | **MANUAL** | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| J-DIR series R | 950 <= value ± u <= 1100 ohm | 1000 … 1000 | 5 | 1 | **MANUAL** | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| J-ENA series R | 950 <= value ± u <= 1100 ohm | 1000 … 1000 | 5 | 1 | **MANUAL** | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| J-EVT series R | 950 <= value ± u <= 1100 ohm | 1000 … 1000 | 5 | 1 | **MANUAL** | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| J-AUX series R | 950 <= value ± u <= 1100 ohm | 1000 … 1000 | 5 | 1 | **MANUAL** | 1 kΩ ± 5 % + wire; operator input: dry-run default (not evidence) |
| J-STIM series R | 209 <= value ± u <= 240 ohm | 220 … 220 | 2 | 1 | **MANUAL** | 220 Ω ± 5 %; operator input: dry-run default (not evidence) |

<details><summary>Operator steps / answers (9)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| C-01 | Board = Stefan's NUCLEO-F446RE (D-22); solder bridges SB13/14 ON, SB62/63 OFF, SB16/50 ON, SB54/55 OFF, SB46/52 OFF, … | True | dry-run default (not evidence) |
| PHOTO | Photo file name(s) of the board and the MH header | (dry run) | dry-run default (not evidence) |
| J-PUL-A | DMM resistance from the J-PUL-A tap to MCU pin PC7 (MCU unpowered) [ohm] | 1000 | dry-run default (not evidence) |
| J-PUL-B | DMM resistance from the J-PUL-B tap to MCU pin PB7 (MCU unpowered) [ohm] | 1000 | dry-run default (not evidence) |
| J-DIR | DMM resistance from the J-DIR tap to MCU pin PC9 (MCU unpowered) [ohm] | 1000 | dry-run default (not evidence) |
| J-ENA | DMM resistance from the J-ENA tap to MCU pin PC8 (MCU unpowered) [ohm] | 1000 | dry-run default (not evidence) |
| J-EVT | DMM resistance from the J-EVT tap to MCU pin PC6 (MCU unpowered) [ohm] | 1000 | dry-run default (not evidence) |
| J-AUX | DMM resistance from the J-AUX tap to MCU pin PA11 (MCU unpowered) [ohm] | 1000 | dry-run default (not evidence) |
| J-STIM | DMM resistance PB8 -> J-STIM plug (REQ-A-M2-07: 220 Ω) [ohm] | 220 | dry-run default (not evidence) |

</details>

### HG-19 — Driver DIP sheet (C-19)

Verdict: **OPEN (MANUAL)** · requirements: SYS-005 · 2026-10-05 02:06:08 → 2026-10-05 02:06:08

Method: inspection of SW1…SW8

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| DIP setting | == on off on off off on off on | on off on off off on off on | 0 | – | **MANUAL** | D-27 target: 4000 p/rev, CCW, assist on, 86-80/86-118 closed loop; operator input: dry-run default (not evidence) |
| travel calibration vs 800 steps/mm | recorded (SW-CAL wizard, M3) | – | – | – | **MANUAL** | first travel calibration result is recorded at HG-25 |

<details><summary>Operator steps / answers (1)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| DIP | Driver DIP SW1..SW8 as seen (8 × on/off, e.g. 'on off on off off on off on') | on off on off off on off on | dry-run default (not evidence) |

</details>

### HG-20 — E-stop circuit D-41 / D-42 (C-20)

Verdict: **OPEN (MANUAL)** · requirements: SYS-006, SYS-001 · 2026-10-05 02:06:08 → 2026-10-05 02:06:08

Method: inspection + DMM M-5

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| circuit NC | E-stop NC contact -> input cell -> PA10 (wiring §7) | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| circuit NO | E-stop NO contact -> +5V_CUT -> R_E 68 Ω -> D2 -> ENA+ (§2.1) | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| circuit D1 | D1 Schottky in the MCU ENA path, 10 kΩ pull-down ENA+ -> logic GND | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| circuit NOK1 | no contactor in the 48 V path (D-41); 48 V PSU mains switch reachable | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| circuit NOMCU | the D-42 path contains no MCU-controlled element | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| circuit LABEL | panel label / red mushroom on yellow, PAUSE separate | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| M-5 +5V_CUT | 4.75 <= value ± u <= 5.25 V | 5 … 5 | 0.02 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| isolation | min - u >= 10 MΩ | 50 MΩ | 0 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |

<details><summary>Operator steps / answers (8)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| NC | Inspection: E-stop NC contact -> input cell -> PA10 (wiring §7)? | True | dry-run default (not evidence) |
| NO | Inspection: E-stop NO contact -> +5V_CUT -> R_E 68 Ω -> D2 -> ENA+ (§2.1)? | True | dry-run default (not evidence) |
| D1 | Inspection: D1 Schottky in the MCU ENA path, 10 kΩ pull-down ENA+ -> logic GND? | True | dry-run default (not evidence) |
| NOK1 | Inspection: no contactor in the 48 V path (D-41); 48 V PSU mains switch reachable? | True | dry-run default (not evidence) |
| NOMCU | Inspection: the D-42 path contains no MCU-controlled element? | True | dry-run default (not evidence) |
| LABEL | Inspection: panel label / red mushroom on yellow, PAUSE separate? | True | dry-run default (not evidence) |
| M-5 | DMM M-5: +5V_CUT at the DC-DC output, 48 V bus ON [V] | 5 | dry-run default (not evidence) |
| M-5iso | DMM: resistance 48 V GND <-> logic GND, 48 V bus OFF [Mohm] | 50 | dry-run default (not evidence) |

</details>

### HG-31 — Loopback hygiene (C-24)

Verdict: **OPEN (MANUAL)** · requirements: SYS-009 · 2026-10-05 02:06:08 → 2026-10-05 02:06:08

Method: init-code statement; release image at HG-30

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| MH pins not analog | no loopback pin in analog mode while a 5 V tap is fitted | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| release image with jumpers fitted | repeated at HG-30 | – | – | – | **MANUAL** | confirmed when the release image runs |

<details><summary>Operator steps / answers (1)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| INIT | A's statement / init-code inspection: in EVERY build the MH pins (PC6…PC9, PB7, PA11, PB8) are digital inputs without… | True | dry-run default (not evidence) |

</details>

### HG-29 — Measurement-chain self-test (C-24)

Verdict: **INCONCLUSIVE** · requirements: SYS-009 · 2026-10-05 02:06:08 → 2026-10-05 02:06:09

Method: INFO; STIM -> MT-3 / MT-4; 10 kHz pulse train (PSU off): MT-2 = Δpos = stamps, PWM period; RC delays

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| a continuity (HG-01) | HG-01 PASS today | – | – | – | **MANUAL** | HG-01 verdict OPEN (MANUAL) |
| b probe clock | == 180000000 | 180000000 | 0 | – | **PASS** |  |
| b counter bits | == 32 | 32 | 0 | – | **PASS** |  |
| b stamp clock | == 1000000 | 1000000 | 0 | – | **PASS** |  |
| b ring size | == 2048 | 2048 | 0 | – | **PASS** |  |
| b DMA stamp latency | max + u <= 1000 ns | 0 ns | 0 | 1 | **PASS** |  |
| b stimulus clock | == 10000000 | 10000000 | 0 | – | **PASS** |  |
| c STIM_RUN accepted | == OK | OK | 0 | – | **PASS** |  |
| c probe TRIGGERED by STIM | MT-3 trigger on J-EVT | ['ARMED', 'TRIGGERED'] | – | – | **PASS** |  |
| c EVT interval = 2·hold + random delay | 20000 <= value ± u <= 21000 µs | 2.03e+04 … 2.095e+04 | 1 | 4 | **PASS** | delay 0…1 ms (step timer stopped) — twin model spacing |
| c MT-3 CNT vs MT-4 stamps | probe CNT·tick within [t_read1, t_read2] − first stamp | cnt 136920.0 µs in [135923, 137923] µs | – | – | **PASS** |  |
| c J-AUX stamps (pulse width = hold) | hold ± 1 µs | – | – | – | **TARGET-ONLY** | J-AUX not modelled in the twin |
| d MT-2 = \|Δpos_steps\| | == 9916 | 9916 | 0 | – | **PASS** |  |
| d MT-4 stamps = MT-2 | == 9916 | 9916 | 0 | – | **PASS** |  |
| d pulses in the move | recorded | 9916 | – | – | **INFO** | ≈ 10 000 (2 mm/s at 5000 steps/mm for ~1 s + ramps) |
| d PWM statistics free of the first-capture artifact | min ≈ max during cruise | {'pwm_min_period': 0, 'pwm_max_period': 18000, 'pwm_min_high': 1800, 'pwm_max_high': 1800} | – | – | **INCONCLUSIVE** | min values corrupted by the first capture after arming (DEF-HG-01 board HAL / OBS-E-HG-05 twin model); periods evalua… |
| d PWM-input period at 10 kHz | 17999 <= value ± u <= 18001 ticks | 1.8e+04 … 1.8e+04 | 0 | 1 | **PASS** | PSC 0, 6010 periods during cruise |
| PSU-off block: parameters restored by REBOOT | GET_PARAM == originals | {'motion.steps_per_mm': [800.0, 800.0], 'motion.v_unhomed_um_s': [2000, 2000], 'drv.alm_active_level': [0, 0]} | – | – | **PASS** |  |
| e RC + threshold delay per input | recorded (basis of u_th, R-3) | – | – | – | **TARGET-ONLY** | needs J-AUX on the pin node; not modelled in the twin (E-stop part in the bench block) |
| f TC-SYS-009-02 report | FW_test_report_M2 lists TC-SYS-009-02 PASS | 02_FW\docs\FW_test_report_M2.md | – | – | **PASS** |  |

<details><summary>Operator steps / answers (2)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| JUMPERS | Measurement header MH: J-EVT <- PB8 (STIM), J-AUX <- PB8 (STIM); J-STIM not plugged into any input | MH (twin: selector = probe source) | twin-emulated |
| PSU-OFF | Switch the 48 V driver PSU OFF (mains switch). Driver signal cable stays connected. Confirm the motor shaft turns fre… | done | twin-emulated |

</details>

Notes:

- driver unpowered reads ALM active -> drv.alm_active_level flipped in RAM for the PSU-off block (D-28 start-block would refuse the pulse trains); restored by the REBOOT

### HG-03 — VCP 921 600 Bd soak (C-03)

Verdict: **PASS** · requirements: IF-002, SYS-009 · 2026-10-05 02:06:09 → 2026-10-05 02:06:12

Method: 80 Hz stream + 20 cmd/s, counters

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| duration | min - u >= 600 s | 600.2 s | 0 | 1 | **PASS** |  |
| DATA frame_seq gaps | == 0 | 0 | 0 | – | **PASS** | 48016 DATA frames |
| PC CRC / length errors | == 0 | 0 | 0 | – | **PASS** |  |
| FW rx_crc/frame errors, overruns, tx_drops | == 0 | 0 | 0 | – | **PASS** | {'rx_crc_errors': 0, 'rx_frame_errors': 0, 'rx_overruns': 0, 'tx_drops': 0, 'event_overflows': 0} |
| all commands answered | == 12585 | 12585 | 0 | – | **PASS** | 0 NACK |
| DATA rate | min - u >= 79 Hz | 80 Hz | 0.5 | 1 | **PASS** | 80 SPS stream (D-04) |

### HG-02 — Clock source and PUL frequency (C-02)

Verdict: **INCONCLUSIVE** · requirements: FW-PLT-002 · 2026-10-05 02:06:12 → 2026-10-05 02:06:13

Method: CLK_FALLBACK; MT-5 regression; MT-3 PWM input at 50 kHz (PSU off)

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| a no CLK_FALLBACK | sys_flags + EVENTs | ['CFG_DIRTY', 'STREAM_ON', 'NVM_DEFAULTED'] | – | – | **PASS** |  |
| b device clock error vs PC (MT-5) | max \|x\| + u <= 1000 ppm | 0 ppm | 100 | 1 | **PASS** | 600 samples over 599 s (twin: virtual clock = PC clock) |
| c PWM statistics free of the first-capture artifact | min ≈ max during cruise | {'pwm_min_period': 0, 'pwm_max_period': 3600, 'pwm_min_high': 1800, 'pwm_max_high': 1800} | – | – | **INCONCLUSIVE** | min values corrupted by the first capture after arming (DEF-HG-01 board HAL / OBS-E-HG-05 twin model); periods evalua… |
| c PUL period at 50 kHz (PSC 0) | 3599 <= value ± u <= 3601 ticks | 3600 … 3600 | 0 | 1 | **PASS** | 25049 periods during cruise; 1800 TIM2 ticks exact |
| PSU-off block: parameters restored by REBOOT | GET_PARAM == originals | {'motion.steps_per_mm': [800.0, 800.0], 'motion.v_unhomed_um_s': [2000, 2000], 'drv.alm_active_level': [0, 0]} | – | – | **PASS** |  |

<details><summary>Operator steps / answers (1)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| PSU-OFF | Switch the 48 V driver PSU OFF (mains switch). Driver signal cable stays connected. Confirm the motor shaft turns fre… | done | twin-emulated |

</details>

Notes:

- MT-5 span 599 s < 600 s (quick mode)
- driver unpowered reads ALM active -> drv.alm_active_level flipped in RAM for the PSU-off block (D-28 start-block would refuse the pulse trains); restored by the REBOOT

### P2-ENTRY — Phase 2 entry: driver PSU on

Verdict: **N/A** · requirements: – · 2026-10-05 02:06:13 → 2026-10-05 02:06:13

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| phase 2 entry | recorded | driver powered | – | – | **INFO** |  |

<details><summary>Operator steps / answers (1)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| P2 | Phase 2: driver PSU ON (48 V), direct 3.3 V drive, no specimen, nothing in the travel; observer at the red button | done | twin-emulated |

</details>

### HG-32 — D-41 / CR-03 configuration (C-26)

Verdict: **PASS** · requirements: SYS-009, D-41 · 2026-10-05 02:06:13 → 2026-10-05 02:06:13

Method: GET_PARAM defaults; E-stop held > 1 s

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| drv.pwr_sense_enable default | == 0 | 0 | 0 | – | **PASS** | CR-03 |
| drv.k1_check_enable default | == 0 | 0 | 0 | – | **PASS** | D-43 e |
| E-stop held > 1 s: only ESTOP | ESTOP latched, no K1_WELDED / fault | {'flags': ['ESTOP'], 'faults': []} | – | – | **PASS** |  |
| ENABLE refused by ESTOP only | E_STATE BLOCK = ESTOP (no DRV_UNPOWERED) | {'status': 'E_STATE', 'detail': 1, '_rtt_ms': 0.25, '_pc_ns': 608332357642} | – | – | **PASS** |  |
| ESTOP_CLEAR after release | == OK | OK | 0 | – | **PASS** |  |

<details><summary>Operator steps / answers (2)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| PRESS-HOLD | Press and HOLD the red E-stop (≥ 2 s) | done | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |

</details>

### HG-06 — Opto drive margin (C-06)

Verdict: **PARTIAL (MANUAL open)** · requirements: SYS-009, SYS-011 · 2026-10-05 02:06:13 → 2026-10-05 02:06:13

Method: STATIC_LEVEL + DMM (VOH, 100 Ω shunt)

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| PUL STATIC_LEVEL accepted | == OK | OK | 0 | – | **PASS** |  |
| PUL driven high (twin edge log) | pin level 1 while STATIC_LEVEL | {'t_us': 608485781.254, 'pin': 'PUL', 'level': 1} | – | – | **PASS** |  |
| PUL VOH | min - u >= 3 V | 3.1 V | 0.02 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| PUL I_LED | min - u >= 6 mA | 6.8 mA | 0.1 | 1 | **MANUAL** | bring-up ≥ 6 mA; operator input: dry-run default (not evidence) |
| DIR STATIC_LEVEL accepted | == OK | OK | 0 | – | **PASS** |  |
| DIR driven high (twin edge log) | pin level 1 while STATIC_LEVEL | {'t_us': 608488031.254, 'pin': 'DIR', 'level': 1} | – | – | **PASS** |  |
| DIR VOH | min - u >= 3 V | 3.1 V | 0.02 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| DIR I_LED | min - u >= 6 mA | 6.8 mA | 0.1 | 1 | **MANUAL** | bring-up ≥ 6 mA; operator input: dry-run default (not evidence) |
| ENA VOH | min - u >= 3 V | 3.1 V | 0.02 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| ENA I_LED | min - u >= 6 mA | 6.8 mA | 0.1 | 1 | **MANUAL** | bring-up ≥ 6 mA; operator input: dry-run default (not evidence) |

<details><summary>Operator steps / answers (6)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| PUL-VOH | PUL held high by DIAG_MEAS STATIC_LEVEL: DMM VOH at the driver terminal PUL+ vs logic GND [V] | 3.1 | dry-run default (not evidence) |
| PUL-ILED | PUL held high by DIAG_MEAS STATIC_LEVEL: DMM voltage over the 100 Ω shunt in series with PUL− [mV] | 680 | dry-run default (not evidence) |
| DIR-VOH | DIR held high by DIAG_MEAS STATIC_LEVEL: DMM VOH at the driver terminal DIR+ vs logic GND [V] | 3.1 | dry-run default (not evidence) |
| DIR-ILED | DIR held high by DIAG_MEAS STATIC_LEVEL: DMM voltage over the 100 Ω shunt in series with DIR− [mV] | 680 | dry-run default (not evidence) |
| ENA-VOH | ENA at the disabled level (NOT_ENABLED = LED current): DMM VOH at the driver terminal ENA+ vs logic GND [V] | 3.1 | dry-run default (not evidence) |
| ENA-ILED | ENA at the disabled level (NOT_ENABLED = LED current): DMM voltage over the 100 Ω shunt in series with ENA− [mV] | 680 | dry-run default (not evidence) |

</details>

### HG-10cd — D-42 hardwired ENA cut: MCU in reset, DMM M-1…M-4 (C-25) — before any bypass

Verdict: **PARTIAL (MANUAL open)** · requirements: SAF-FW-005, SYS-006, D-42 · 2026-10-05 02:06:13 → 2026-10-05 02:06:13

Method: functional by hand + DMM; FW restart sequence

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| c1 MCU in reset + E-stop -> motor free | D-42 NO contact disables the driver | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| d M-1 ENA cut current | min - u >= 7 mA | 10.29 mA | 0.1 | 1 | **MANUAL** | ≥ the driver's ENA threshold (ASSUMED ≥ 7 mA, wiring §11.1); operator input: dry-run default (not evidence) |
| d M-3 PA4 (reset, pressed) | max + u <= 3.4 V | 0.2 V | 0.02 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| d M-4 ENA+ (pressed) | 4 <= value ± u <= 4.6 V | 4.3 … 4.3 | 0.02 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| c2 reset + released -> holding (D-13) | hardware only, no MCU involved | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| d M-2a enabled + released | max + u <= 0.5 mA | 0 mA | 0.1 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| c3 FW latched ESTOP, ENA disabled | ESTOP + io ENA_DISABLED | {'flags': ['ESTOP'], 'io': ['ESTOP_OPEN', 'PEND', 'DRV_PWR', 'ENA_DISABLED', 'RATE_80']} | – | – | **PASS** |  |
| d M-2c no over-current | max + u <= 13.5 mA | 12 mA | 0.1 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| d M-3 PA4 never 5 V | max + u <= 3.4 V | 3.3 V | 0.02 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| c3 release never re-energises | ESTOP latched, driver disabled after release | ['ESTOP'] | – | – | **PASS** | twin-observed (world model, not HW evidence) |
| c3 holding only after ESTOP_CLEAR + ENABLE | D-41 restart sequence | True | – | – | **PASS** | twin-observed (world model, not HW evidence) |

<details><summary>Operator steps / answers (13)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| c1 | Hold the MCU in reset (Nucleo B2 RESET held / NRST jumper to GND). Press the red E-stop. Turn the motor shaft by hand… | True | dry-run default (not evidence) |
| M-1 | Still in reset + pressed: DMM M-1 voltage across R_E (68 Ω) [V] | 0.7 | dry-run default (not evidence) |
| M-3b | Still in reset + pressed: DMM M-3 at PA4 (CN8-3) [V] | 0.2 | dry-run default (not evidence) |
| M-4p | Still in reset + pressed: DMM M-4 at ENA+ [V] | 4.3 | dry-run default (not evidence) |
| c2 | Still in reset: RELEASE the E-stop. Shaft HOLDING again (ENA pull-down = enabled, D-13)? | True | dry-run default (not evidence) |
| c3 | Release the MCU reset; wait 2 s (the session reconnects) | done | twin-emulated |
| M-2a | MCU running, ENABLED, E-stop released: DMM M-2 ENA loop current (100 Ω shunt in ENA−) [mA] | 0 | dry-run default (not evidence) |
| c3-press | MCU running: press the red E-stop | done | twin-emulated |
| M-2c | Pressed, MCU driving disabled: DMM M-2 ENA loop current [mA] | 12 | dry-run default (not evidence) |
| M-3c | Pressed, MCU driving disabled: DMM M-3 at PA4 [V] | 3.3 | dry-run default (not evidence) |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| c3-free | E-stop released, MCU running: shaft still FREE (FW keeps ENA disabled until ESTOP_CLEAR + ENABLE)? | True | twin-observed |
| c3-hold | After ESTOP_CLEAR + ENABLE: shaft HOLDING? | True | twin-observed |

</details>

### HG-28 — First motion, direction, homing smoke (SYS-009)

Verdict: **PASS** · requirements: SYS-009, FW-HOM-001/002 · 2026-10-05 02:06:13 → 2026-10-05 02:06:18

Method: un-homed 1 mm/s jog, caliper; HOME; HOME with inverted DIR

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| MT-2 = \|Δpos_steps\| (first jog) | == 2397 | 2397 | 0 | – | **PASS** |  |
| direction +x away from START | SYS-009 first motion | True | – | – | **PASS** | twin-observed (world model, not HW evidence) |
| scale (caliper vs commanded) | max + u <= 10 % | 0 % | 0.6675 | 1 | **PASS** | commanded 2.996 mm at 800 steps/mm; twin-observed (world model, not HW evidence) |
| HOME at START | HOMED, MOVE_DONE TARGET | {'t_us': 29219000, 'code': 'MOVE_DONE', 'arg': 0, 'value': 0, 'value2': 0, '_pc_ns': 638252107642} | – | – | **PASS** |  |
| inverted DIR -> HOME_WIRING / HOME_NOT_FOUND | fault latched, HOME_FAILED | {'faults': ['HOME_WIRING'], 'home_failed': [{'t_us': 89574000, 'code': 'HOME_FAILED', 'arg': 2, 'value': -301500, 'va… | – | – | **PASS** |  |
| inverted DIR travel | max + u <= 360 mm | 301.5 mm | 0.1 | 1 | **PASS** | twin: world travel |
| re-HOME after the inverted test | HOMED | {'t_us': 160654000, 'code': 'MOVE_DONE', 'arg': 0, 'value': 0, 'value2': 0, '_pc_ns': 769686357642} | – | – | **PASS** |  |

<details><summary>Operator steps / answers (4)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| PREP | No specimen, nothing in the travel; caliper / dial on the table; hand at the E-stop | skipped | dry-run: manual step not emulated |
| DIR | Did the table move AWAY from the START switch (+x)? | True | twin-observed |
| CAL | Caliper: travel of this jog [mm] | 2.996 | twin-observed |
| INV-OK | Next: HOME with motion.dir_invert deliberately WRONG — the table runs AWAY from START toward the END switch (no load,… | True | dry-run default (not evidence) |

</details>

### HG-07 — ENA enable / disable and settle (C-07)

Verdict: **PASS** · requirements: FW-MOT-008 · 2026-10-05 02:06:18 → 2026-10-05 02:06:18

Method: MT-3 trigger on the ENABLE frame (RX), CCR3 = ENA edge, MT-4 first PUL

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| disabled = shaft free | ENA LED current = driver disabled | True | – | – | **PASS** | twin-observed (world model, not HW evidence) |
| JOG during ENA settle refused | E_BUSY ENABLING | {'status': 'E_BUSY', 'detail': 2, '_rtt_ms': 0.5, '_pc_ns': 769698857642} | – | – | **PASS** |  |
| enabled = holding | no ENA current = enabled | True | – | – | **PASS** | twin-observed (world model, not HW evidence) |
| first PUL/DIR edge − ENA enable edge | min - u >= 500000 µs | 5.569e+05 µs | 11 | 1 | **PASS** | motion.ena_settle_ms = 500; ENA edge 70 µs after the ENABLE frame |

<details><summary>Operator steps / answers (3)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| JUMPERS | Measurement header MH: J-EVT <- PA3 (USART2 RX), J-ENA, J-PUL-A, J-DIR fitted | MH (twin: selector = probe source) | twin-emulated |
| FREE | NOT_ENABLED (DISABLE sent): motor shaft FREE by hand (no load!)? | True | twin-observed |
| HOLD | After ENABLE + settle: shaft HOLDING? | True | twin-observed |

</details>

### HG-08 — PUL / DIR timing (C-08)

Verdict: **INCONCLUSIVE** · requirements: FW-MOT-001 · 2026-10-05 02:06:18 → 2026-10-05 02:06:20

Method: PWM input ≥ 1e5 pulses at 50 kHz (PSU off); 1-step reversals: MT-3 on DIR + MT-4

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| PSU-off block: parameters restored by REBOOT | GET_PARAM == originals | {'motion.steps_per_mm': [800.0, 800.0], 'motion.v_unhomed_um_s': [2000, 2000], 'drv.alm_active_level': [0, 0]} | – | – | **PASS** |  |
| a PWM statistics free of the first-capture artifact | min ≈ max during cruise | {'pwm_min_period': 0, 'pwm_max_period': 1800, 'pwm_min_high': 900, 'pwm_max_high': 900} | – | – | **INCONCLUSIVE** | min values corrupted by the first capture after arming (DEF-HG-01 board HAL / OBS-E-HG-05 twin model); periods evalua… |
| a pulses measured | min - u >= 100000 pulses | 1.05e+05 pulses | 0 | 1 | **PASS** |  |
| a PUL high | min - u >= 10 µs | 10 µs | 0.01111 | 1 | **INCONCLUSIVE** |  |
| a PUL low (period_min − high_max) | min - u >= 10 µs | 10 µs | 0.02222 | 1 | **INCONCLUSIVE** |  |
| a step rate ≤ 50 kHz (period) | min - u >= 20 µs | 20 µs | 0.01111 | 1 | **INCONCLUSIVE** |  |
| b DIR setup (MT-3, 1 PUL per reversal) | min - u >= 20 µs | 4990 µs | 0.1 | 100 | **PASS** | 100 reversals, 0 invalid |
| b DIR setup (MT-4 stamps, cross-check) | min - u >= 20 µs | 4990 µs | 2 | 100 | **PASS** |  |
| b MT-3 vs MT-4 agree | max + u <= 2.1 µs | 0 µs | 0 | 100 | **PASS** |  |
| b reversals | min - u >= 100 trials | 100 trials | 0 | 1 | **PASS** |  |

<details><summary>Operator steps / answers (3)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| JUMPERS | Measurement header MH: J-PUL-A, J-DIR fitted; J-EVT <- DIR node | MH (twin: selector = probe source) | twin-emulated |
| PSU-OFF | Switch the 48 V driver PSU OFF (mains switch). Driver signal cable stays connected. Confirm the motor shaft turns fre… | done | twin-emulated |
| PSU-ON | Switch the 48 V driver PSU back ON; wait 2 s | done | twin-emulated |

</details>

Notes:

- driver unpowered reads ALM active -> drv.alm_active_level flipped in RAM for the PSU-off block (D-28 start-block would refuse the pulse trains); restored by the REBOOT

### HG-09 — Step count integrity (C-09)

Verdict: **PASS** · requirements: SAF-FW-004 · 2026-10-05 02:06:20 → 2026-10-05 02:06:47

Method: MT-2 vs Δpos_steps: random moves, jogs with reversals (stamp segments), random STOP / HALT; caliper

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| moves: MT-2 = \|Δpos_steps\| | all equal | 100/100 equal | – | 100 | **PASS** | 100 random MOVE_ABS |
| caliper move 0 | max \|x\| + u <= 0.02125 mm | 0 mm | 0 | 1 | **PASS** | \|caliper − commanded\| ≤ caliper u + 1 step; twin-observed (world model, not HW evidence) |
| caliper move 1 | max \|x\| + u <= 0.02125 mm | 0 mm | 0 | 1 | **PASS** | \|caliper − commanded\| ≤ caliper u + 1 step; twin-observed (world model, not HW evidence) |
| caliper move 2 | max \|x\| + u <= 0.02125 mm | 0 mm | 0 | 1 | **PASS** | \|caliper − commanded\| ≤ caliper u + 1 step; twin-observed (world model, not HW evidence) |
| caliper move 3 | max \|x\| + u <= 0.02125 mm | 0 mm | 0 | 1 | **PASS** | \|caliper − commanded\| ≤ caliper u + 1 step; twin-observed (world model, not HW evidence) |
| caliper move 4 | max \|x\| + u <= 0.02125 mm | 0 mm | 0 | 1 | **PASS** | \|caliper − commanded\| ≤ caliper u + 1 step; twin-observed (world model, not HW evidence) |
| jogs: MT-2 = Σ segments (stamps) | all equal | 10/10 equal | – | 10 | **PASS** | 10 jogs with 2 reversals |
| jogs: \|Σ ± segments\| = \|Δpos_steps\| | per direction segment | [] | – | – | **PASS** |  |
| random STOP / HALT: MT-2 = \|Δpos_steps\| | all equal | 100/100 equal | – | 100 | **PASS** | ± 1 accepted only with POS_UNCERTAIN |
| E-stop stops | recorded | see HG-10 a/b | – | – | **INFO** | counter vs Δpos is checked in every E-stop trial |

<details><summary>Operator steps / answers (5)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| CAL0 | Caliper: travel of the last move [mm] | 37.68 | twin-observed |
| CAL1 | Caliper: travel of the last move [mm] | 37.18 | twin-observed |
| CAL2 | Caliper: travel of the last move [mm] | 96.31 | twin-observed |
| CAL3 | Caliper: travel of the last move [mm] | 112.8 | twin-observed |
| CAL4 | Caliper: travel of the last move [mm] | 51.3 | twin-observed |

</details>

### BENCH-ENTRY — §6.8 P-1…P-6: E-stop sense bypass (J-STIM), HG-29 e E-stop input

Verdict: **PARTIAL (MANUAL, TARGET-ONLY open)** · requirements: SYS-009 · 2026-10-05 02:06:47 → 2026-10-05 02:06:47

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| P-1 soft-limit window | recorded | 5…55 mm | – | – | **INFO** | 50 mm window next to START |
| P-4 J-STIM resistor | 200 <= value ± u <= 240 ohm | 220 … 220 | 2 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| P-4 PA10 idle (valid low) | max + u <= 0.99 V | 0.6 V | 0.02 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| P-5 ESTOP_OPEN follows STIM | 0 idle, 1 during a STIM hold (driver unpowered) | {'idle': ['ALM', 'PEND', 'RATE_80'], 'stim': ['ESTOP_OPEN', 'ALM', 'PEND', 'ENA_DISABLED', 'RATE_80']} | – | – | **PASS** |  |
| HG-29 e E-stop input RC delay | recorded (u_th E-stop) | – | – | – | **TARGET-ONLY** | J-AUX not modelled |
| P-7 abort criteria | recorded | unexpected motion / noise / ALM / outside the window -> red button + PSU off, operator types 'abort' (the session sen… | – | – | **INFO** |  |

<details><summary>Operator steps / answers (5)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| P-1 | P-1: no specimen, no load, no tool in the travel; operator at the PC and observer at the operator panel (red button +… | (dry run) | dry-run default (not evidence) |
| P-3 | P-3: driver PSU OFF; unplug the NC sense connector at the interface board; fit J-STIM (PB8, 220 Ω) to the PA10 input … | done | twin-emulated |
| P-4R | P-4: J-STIM series resistor value [ohm] | 220 | dry-run default (not evidence) |
| P-4V | P-4: DMM PA10 pin (CN10-33) with STIM idle [V] | 0.6 | dry-run default (not evidence) |
| P-6 | P-6: driver PSU ON; observer keeps a hand at the red button (D-42 still active) and the PSU switch | done | twin-emulated |

</details>

Notes:

- P-2 gate (HG-10 c/d PASS) is enforced on the board; in the twin dry run HG-10 c/d is MANUAL

### HG-10a — E-stop reaction, 100 STIM trials (C-10 a)

Verdict: **PASS** · requirements: SAF-FW-005, SYS-006 · 2026-10-05 02:06:47 → 2026-10-05 02:07:24

Method: MT-3 trigger PSC 17 on the E-stop node, CCR2 last PUL, CCR3 ENA; 30 mm/s jog; re-home per trial

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| a STIM trials with E-stop event + probe trigger | == 100 | 100 | 0 | – | **PASS** |  |
| a STIM last PUL after the E-stop edge | max + u <= 100 µs | 0 µs | 1.1 | 100 | **PASS** | MT-3 PSC 17 (0.1 µs) + u_th 1.0 µs (R-3) |
| a STIM ENA disabled after the E-stop edge | max + u <= 1000 µs | 0 µs | 1.1 | 100 | **PASS** | twin: zero-latency reaction model, CCR3 = 0 taken as 0 µs (OBS-E-HG-03) |
| a STIM CCR2 consistent with PUL stamps | CCR2 = 0 <=> no PUL stamp after the event | True | – | – | **PASS** |  |
| a STIM MT-2 = \|Δpos_steps\| (HG-09 E-stop part) | all equal | 100/100 equal | – | 100 | **PASS** | ± 1 accepted only with POS_UNCERTAIN (truncated pulse, ICD §6.2 v0.7.2); 100 trials with POS_UNCERTAIN |
| a STIM latched, ENA disabled, HOMED cleared | SAF-FW-005 state after every trial | True | – | – | **PASS** |  |
| a STIM event phase vs step period (10 bins) | recorded | [16, 8, 7, 10, 9, 14, 7, 9, 7, 13] | – | – | **INFO** | §6.1 rule 3 phase coverage |

### BENCH-EXIT — §6.8 P-8: sense restored — before any further motion

Verdict: **PASS** · requirements: SYS-009 · 2026-10-05 02:07:24 → 2026-10-05 02:07:25

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| P-8 sense restored: press | EVENT ESTOP_SET, ENA disabled, ESTOP latched, ESTOP_OPEN = 1 | {'events': ['ESTOP_SET', 'DRIVER_DISABLED'], 'io': ['ESTOP_OPEN', 'PEND', 'DRV_PWR', 'ENA_DISABLED', 'RATE_80']} | – | – | **PASS** |  |
| P-8 sense restored: release + ESTOP_CLEAR | ESTOP_OPEN follows the button | ['PEND', 'DRV_PWR', 'ENA_DISABLED', 'RATE_80'] | – | – | **PASS** |  |
| soft limits restored | recorded | {'limits.soft_min_um': 500, 'limits.soft_max_um': 290000} | – | – | **INFO** |  |

<details><summary>Operator steps / answers (4)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| P-8a | P-8: driver PSU OFF; remove J-STIM; reconnect the NC sense connector; remove the tag; photo | done | twin-emulated |
| P-8b | P-8: driver PSU ON | done | twin-emulated |
| P-8c | P-8: press the red E-stop (MCU running) | done | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |

</details>

### HG-10b — E-stop real presses + slow press, FW with forced ENA (C-10 b/e)

Verdict: **PASS** · requirements: SAF-FW-005, SYS-006, D-42 · 2026-10-05 02:07:25 → 2026-10-05 02:07:29

Method: 10 real presses during a jog; 2 slow presses

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| b real press trials with E-stop event + probe trigger | == 10 | 10 | 0 | – | **PASS** |  |
| b real press last PUL after the E-stop edge | max + u <= 100 µs | 0 µs | 1.1 | 10 | **PASS** | MT-3 PSC 17 (0.1 µs) + u_th 1.0 µs (R-3) |
| b real press ENA disabled after the E-stop edge | max + u <= 1000 µs | 0 µs | 1.1 | 10 | **PASS** | twin: zero-latency reaction model, CCR3 = 0 taken as 0 µs (OBS-E-HG-03) |
| b real press CCR2 consistent with PUL stamps | CCR2 = 0 <=> no PUL stamp after the event | True | – | – | **PASS** |  |
| b real press MT-2 = \|Δpos_steps\| (HG-09 E-stop part) | all equal | 10/10 equal | – | 10 | **PASS** | ± 1 accepted only with POS_UNCERTAIN (truncated pulse, ICD §6.2 v0.7.2); 10 trials with POS_UNCERTAIN |
| b real press latched, ENA disabled, HOMED cleared | SAF-FW-005 state after every trial | True | – | – | **PASS** |  |
| b real press event phase vs step period (10 bins) | recorded | [2, 0, 0, 5, 1, 1, 1, 0, 0, 0] | – | – | **INFO** | §6.1 rule 3 phase coverage |
| e0 no unexpected FW state | only ESTOP latched, no fault / extra latch | ['ESTOP_SET', 'DRIVER_DISABLED'] | – | – | **PASS** |  |
| e0 release never causes motion | counter 0, ESTOP latched, NOT_ENABLED | {'counter': 0, 'state': 'NOT_ENABLED'} | – | – | **PASS** |  |
| e0 HOME required | MOVE_ABS refused NOT_HOMED | {'status': 'E_STATE', 'detail': 16, '_rtt_ms': 0.5, '_pc_ns': 2974274219952} | – | – | **PASS** |  |
| e1 no unexpected FW state | only ESTOP latched, no fault / extra latch | ['ESTOP_SET', 'DRIVER_DISABLED'] | – | – | **PASS** |  |
| e1 release never causes motion | counter 0, ESTOP latched, NOT_ENABLED | {'counter': 0, 'state': 'NOT_ENABLED'} | – | – | **PASS** |  |
| e1 HOME required | MOVE_ABS refused NOT_HOMED | {'status': 'E_STATE', 'detail': 16, '_rtt_ms': 0.5, '_pc_ns': 2975748969952} | – | – | **PASS** |  |

<details><summary>Operator steps / answers (25)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| JUMPERS | Measurement header MH: J-EVT <- PA10 node (real button contact), J-ENA, J-PUL-A | MH (twin: selector = probe source) | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +69.8 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +73.3 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +47.8 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +119.6 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +23.9 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +40.1 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +71.7 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +71.2 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +179.0 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +135.3 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| e0 | Press the red E-stop SLOWLY (≈ 1 s half-way, then fully) so that the NO contact (D-42) acts BEFORE the NC sense opens… | done | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| e1 | Press the red E-stop SLOWLY (≈ 1 s half-way, then fully) so that the NO contact (D-42) acts AFTER (press quickly past… | done | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |

</details>

Notes:

- e: the twin has no model of the D-42 NO contact forcing ENA — the FW-state part ran with a normal press

### HG-11 — Limit reaction (C-11)

Verdict: **PASS** · requirements: SAF-FW-002 · 2026-10-05 02:07:29 → 2026-10-05 02:07:58

Method: 100 STIM trials START / END (switch unplugged) MT-3 PSC 1; 10 real actuations at 1 mm/s

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| START STIM trials with LIMIT_SET + trigger | == 100 | 100 | 0 | – | **PASS** |  |
| START STIM: last PUL after the limit edge | max + u <= 200 µs | 0 µs | 15.01 | 100 | **PASS** | MT-3 PSC 1 + u_th 15.0 µs (R-3) |
| START STIM: MT-2 = \|Δpos_steps\| | all equal | 100/100 equal | – | 100 | **PASS** |  |
| START latch refuses motion toward the switch | E_STATE LIMIT | {'status': 'E_STATE', 'detail': 32, '_rtt_ms': 0.5, '_pc_ns': 3123100060059} | – | – | **PASS** |  |
| START real actuations detected | == 10 | 10 | 0 | – | **PASS** |  |
| START real: last PUL after the switch edge | max + u <= 200 µs | 0 µs | 15.1 | 10 | **PASS** | 1 mm/s, real contact incl. bounce |
| END STIM trials with LIMIT_SET + trigger | == 100 | 100 | 0 | – | **PASS** |  |
| END STIM: last PUL after the limit edge | max + u <= 200 µs | 0 µs | 15.01 | 100 | **PASS** | MT-3 PSC 1 + u_th 15.0 µs (R-3) |
| END STIM: MT-2 = \|Δpos_steps\| | all equal | 100/100 equal | – | 100 | **PASS** |  |
| END latch refuses motion toward the switch | E_STATE LIMIT | {'status': 'E_STATE', 'detail': 32, '_rtt_ms': 0.5, '_pc_ns': 3328173328969} | – | – | **PASS** |  |
| END real actuations detected | == 10 | 10 | 0 | – | **PASS** |  |
| END real: last PUL after the switch edge | max + u <= 200 µs | 0 µs | 15.1 | 10 | **PASS** | 1 mm/s, real contact incl. bounce |

<details><summary>Operator steps / answers (4)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| START-STIM | Unplug the START switch; J-STIM (220 Ω) -> its input connector; J-EVT <- START pin node; J-PUL-A fitted (§6.8 steps w… | skipped | dry-run: manual step not emulated |
| START-REAL | Remove J-STIM; reconnect the START switch (J-EVT stays on its pin node) | skipped | dry-run: manual step not emulated |
| END-STIM | Unplug the END switch; J-STIM (220 Ω) -> its input connector; J-EVT <- END pin node; J-PUL-A fitted (§6.8 steps witho… | skipped | dry-run: manual step not emulated |
| END-REAL | Remove J-STIM; reconnect the END switch (J-EVT stays on its pin node) | skipped | dry-run: manual step not emulated |

</details>

### HG-13 — PC STOP / HALT (C-13)

Verdict: **PASS** · requirements: SAF-FW-002, NFR-003 · 2026-10-05 02:07:58 → 2026-10-05 02:08:16

Method: 100 STOP 0 + 100 HALT after ≥ 5 ms silence; MT-3 trigger on RX, last byte end -> last PUL

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| STOP: last byte end -> last PUL | max + u <= 2000 µs | 0 µs | 1.2 | 100 | **PASS** | MT-3 PSC 17 on RX, frame end computed at 921600 Bd |
| HALT: last byte end -> last PUL | max + u <= 2000 µs | 0 µs | 1.2 | 100 | **PASS** | MT-3 PSC 17 on RX, frame end computed at 921600 Bd |
| trials with a valid RX trigger | == 200 | 200 | 0 | – | **PASS** | [] |
| Pause/Break key -> last edge (NFR-003) | ≤ 100 ms p95 | – | – | – | **N/A** | M3 (SW hotkey on the reference PC) |

<details><summary>Operator steps / answers (1)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| JUMPERS | Measurement header MH: J-EVT <- PA3 (USART2 RX), J-PUL-A fitted | MH (twin: selector = probe source) | twin-emulated |

</details>

Notes:

- twin DIAG_MEAS model triggers the RX probe at the end of each received byte (start bit on the board); the evaluation uses the matching reference point (OBS-E-HG-01)

### HG-17 — Input wire break (C-17)

Verdict: **PARTIAL (MANUAL open)** · requirements: SAF-FW-007 · 2026-10-05 02:08:16 → 2026-10-05 02:08:19

Method: unplug E-stop / START / END while jogging; D-42 NO

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| E-stop NC sense wire break -> stop + flag | STOPPED(ESTOP) and ESTOP reported | {'stopped': {'t_us': 2958936000, 'code': 'STOPPED', 'arg': 7, 'value': 50514, 'value2': 40411, '_pc_ns': 373203594543… | – | – | **PASS** |  |
| START switch wire break -> stop + flag | STOPPED(LIMIT_START) and LIMIT_START reported | {'stopped': {'t_us': 2981630000, 'code': 'STOPPED', 'arg': 8, 'value': 49443, 'value2': 39554, '_pc_ns': 375472994543… | – | – | **PASS** |  |
| END switch wire break -> stop + flag | STOPPED(LIMIT_END) and LIMIT_END reported | {'stopped': {'t_us': 2991921000, 'code': 'STOPPED', 'arg': 9, 'value': 240456, 'value2': 192365, '_pc_ns': 3765021445… | – | – | **PASS** |  |
| D-42 NO wire break recorded as undetectable | R-10, mitigated by HG-10 c periodic | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |

<details><summary>Operator steps / answers (8)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| BREAK-estop | Unplug the E-stop NC sense connector NOW (table jogging slowly) | scheduled +66.9 ms | twin-emulated |
| FIX-estop | Reconnect the E-stop NC sense connector | done | twin-emulated |
| BREAK-start | Unplug the START switch connector NOW (table jogging slowly) | scheduled +88.5 ms | twin-emulated |
| FIX-start | Reconnect the START switch connector | done | twin-emulated |
| BREAK-end | Unplug the END switch connector NOW (table jogging slowly) | scheduled +38.2 ms | twin-emulated |
| FIX-end | Reconnect the END switch connector | done | twin-emulated |
| NO-BREAK | D-42 NO wire disconnected, MCU held in reset, E-stop pressed: is the shaft still HOLDING (= the break is NOT detected… | True | dry-run default (not evidence) |
| NO-FIX | Reconnect the D-42 NO wire; release the MCU reset and the E-stop; repeat HG-10 c1 once | skipped | dry-run: manual step not emulated |

</details>

### HG-04 — Flash erase vs IWDG, E-stop during SAVE, TX integrity (C-04)

Verdict: **PARTIAL (TARGET-ONLY open)** · requirements: FW-NVM-002/003, SAF-FW-005/019 · 2026-10-05 02:08:19 → 2026-10-05 02:08:24

Method: 20 SAVEs, E-stop pressed inside the erase window (OI-E-HG-04)

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| no IWDG reset during the SAVEs | no BOOT event, uptime continuous | 0 | – | – | **PASS** |  |
| SAVE duration (nvm_save_ms) | max + u <= 2500 ms | 502 ms | 1 | 161 | **PASS** | 162 SAVEs |
| SAVE response (PC, upper bound) | max + u <= 2550 ms | 503.6 ms | 0 | 161 | **PASS** |  |
| SAVEs that included a sector erase | min - u >= 1 SAVEs | 4 SAVEs | 0 | 1 | **PASS** | twin flash log |
| a ENA disabled ≤ 1 ms with the E-stop during a SAVE (program) | max + u <= 1000 µs | 0 µs | 1.1 | 2 | **PASS** |  |
| a ENA disabled ≤ 1 ms with the E-stop inside an erase | max + u <= 1000 µs | 0 µs | 1.1 | 3 | **PASS** | 3 presses inside an erase window |
| b DRV_PWR change during the erase | erase time + 25 ms | – | – | – | **N/A** | optional: no presence sense (D-41) |
| c no gap > 22 µs inside a TX frame (J-AUX = TX) | MT-4 stamps of every TX edge | – | – | – | **TARGET-ONLY** | J-AUX not modelled in the twin |
| valid record after reboot | stream.fallback_hz == last saved, no NVM_DEFAULTED | {'read': 10, 'sys': []} | – | – | **PASS** |  |

<details><summary>Operator steps / answers (11)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| JUMPERS | Measurement header MH: J-EVT <- PA10 node (sense connected, real button), J-ENA | MH (twin: selector = probe source) | twin-emulated |
| PRESS | GO — press the red E-stop NOW (SAVE running) | scheduled +1.6 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | GO — press the red E-stop NOW (SAVE running) | scheduled +3.1 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | GO — press the red E-stop NOW (SAVE running) | scheduled +5.8 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | GO — press the red E-stop NOW (SAVE running) | scheduled +4.6 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | GO — press the red E-stop NOW (SAVE running) | scheduled +4.4 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |

</details>

### HG-14 — Hang -> IWDG (C-14)

Verdict: **PASS** · requirements: SAF-FW-019 · 2026-10-05 02:08:24 → 2026-10-05 02:08:39

Method: HANG main / tick / ISR1 while jogging; .noinit record

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| reset cause IWDG | every trial | [['MAIN', 'IWDG'], ['MAIN', 'IWDG'], ['MAIN', 'IWDG'], ['MAIN', 'IWDG'], ['MAIN', 'IWDG'], ['MAIN', 'IWDG']] | – | – | **PASS** |  |
| .noinit record valid after the reset | w4 prev_valid = 1 | True | – | – | **PASS** |  |
| last PUL − hang start | max + u <= 100 ms | 47.53 ms | 0.1 | 30 | **PASS** | SAF-FW-019 |
| heartbeat end − hang start (IWDG time) | max + u <= 90 ms | 47.53 ms | 0.1 | 30 | **PASS** | plan: ≤ 90 ms |

### HG-16 — ALM / PEND levels and start-block (C-16)

Verdict: **PARTIAL (MANUAL open)** · requirements: FW-SW-004, SAF-FW-026 · 2026-10-05 02:08:39 → 2026-10-05 02:08:41

Method: PSU off -> ALM, start refused; levels by DMM

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| ALM active with the driver unpowered | fail-safe reading (wiring §3) | {'on': ['PEND', 'DRV_PWR', 'RATE_80'], 'off': ['ALM', 'PEND', 'RATE_80']} | – | – | **PASS** |  |
| new motion refused while ALM active (DRV_PWR assumed present) | E_STATE BLOCK DRIVER_ALARM (SAF-FW-026) | {'status': 'E_STATE', 'detail': 512, '_rtt_ms': 0.5, '_pc_ns': 4295262221374} | – | – | **PASS** |  |
| ALM-V-off | recorded | 3.3 | – | – | **MANUAL** | dry-run default (not evidence) |
| PEND-V-off | recorded | 3.3 | – | – | **MANUAL** | dry-run default (not evidence) |
| ALM inactive when powered | ALM low impedance = OK | ['PEND', 'DRV_PWR', 'RATE_80'] | – | – | **PASS** |  |
| one ALM_CHANGED per change | == [1, 0] | 1 … 0 | 0 | – | **PASS** |  |
| ALM-V-on | recorded | 0.1 | – | – | **MANUAL** | dry-run default (not evidence) |
| PEND-V-on | recorded | 3.3 | – | – | **MANUAL** | dry-run default (not evidence) |
| PEND moving / in position | recorded | False / True | – | – | **INFO** | levels recorded; drv.pend_active_level set accordingly (closed loop) |
| running move unaffected by ALM | MOVE_DONE TARGET | {'t_us': 28727000, 'code': 'MOVE_DONE', 'arg': 0, 'value': 50000, 'value2': 40000, '_pc_ns': 4322695971374} | – | – | **PASS** |  |
| drv.*_active_level set | recorded | keep | – | – | **MANUAL** | dry-run default (not evidence) |

<details><summary>Operator steps / answers (7)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| PSU-OFF | Switch the 48 V driver PSU OFF (driver unpowered) | done | twin-emulated |
| ALM-V-off | DMM at the ALM input pin PA8, driver unpowered [V] | 3.3 | dry-run default (not evidence) |
| PEND-V-off | DMM at the PEND input pin PA9, driver unpowered [V] | 3.3 | dry-run default (not evidence) |
| PSU-ON | Switch the 48 V driver PSU ON; wait 2 s | done | twin-emulated |
| ALM-V-on | DMM at PA8, driver powered, idle [V] | 0.1 | dry-run default (not evidence) |
| PEND-V-on | DMM at PA9, driver powered, in position [V] | 3.3 | dry-run default (not evidence) |
| LEVELS | Resulting drv.alm_active_level / drv.pend_active_level (keep / change) | keep | dry-run default (not evidence) |

</details>

### HG-24 — ALM reset (C-16, D-41)

Verdict: **OPEN (MANUAL)** · requirements: SYS-009, D-28 · 2026-10-05 02:08:41 → 2026-10-05 02:08:41

Method: PSU power cycle; ENA toggle

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| ALM provoked | ALM active in GET_STATUS io | {'method': '(dry run)', 'io': ['PEND', 'DRV_PWR', 'RATE_80']} | – | – | **MANUAL** | dry-run default (not evidence) |
| power-cycle reset works | ALM inactive | ['PEND', 'DRV_PWR', 'RATE_80'] | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| ENA-toggle reset | recorded | (dry run) | – | – | **MANUAL** | dry-run default (not evidence) |

<details><summary>Operator steps / answers (4)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| PROVOKE | Provoke a driver ALM on the bench (method used, e.g. motor phase connector unplugged with PSU off before, or under-vo… | (dry run) | dry-run default (not evidence) |
| PWRCYCLE | Reset the alarm: 48 V PSU OFF, wait 5 s, ON (no RESET button since D-41) | skipped | dry-run: manual step not emulated |
| PWRRES | Driver alarm LED off after the power cycle? | True | dry-run default (not evidence) |
| ENATOG | Provoke the ALM again; DISABLE / ENABLE (ENA toggle): result (cleared / not cleared) | (dry run) | dry-run default (not evidence) |

</details>

### IMG-DWT — PO flashes HW_MEAS_DWT; image verified

Verdict: **PARTIAL (NOT MEASURED open)** · requirements: NFR-007 · 2026-10-05 02:08:41 → 2026-10-05 02:08:41

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| build | recorded | host | – | – | **INFO** |  |
| param dict hash | == 0xB7B0263F | 0xB7B0263F | 0 | – | **PASS** | ICD v0.7.1 dict 5 |
| FEAT_HW_MEAS = 1 | measurement image | ['AFE', 'MOTION', 'HOMING', 'MOVE_UNTIL_LOAD', 'NVM', 'TWIN', 'BUTTONS', 'DRV_SIGNALS', 'HW_MEAS'] | – | – | **PASS** |  |
| variant | twin: MEAS \| TWIN_MODEL | ['MEAS', 'TWIN_MODEL'] | – | – | **PASS** |  |
| DWT variant | INFO w0 has DWT | – | – | – | **NOT MEASURED** | the twin model has no DWT image (DWT not modelled) |

Notes:

- twin: engine restarted with hw_meas=True (stand-in for flashing nucleo_f446re_meas_dwt (HW_MEAS_DWT))

### HG-05 — HX711 on silicon (C-05)

Verdict: **PARTIAL (NOT MEASURED, TARGET-ONLY open)** · requirements: FW-AFE-001/004, FW-TIM-001 · 2026-10-05 02:08:41 → 2026-10-05 02:08:46

Method: DOUT stamps vs DATA t_us, rate, reinit; DWT 19/20; SCK edges (J-AUX)

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| samples | min - u >= 9800 reads | 1e+04 reads | 0 | 1 | **PASS** | 10000 requested |
| DATA t_us − DOUT edge (timestamp latency) | max + u <= 5 µs | 0 µs | 1 | 10000 | **PASS** | max ≤ 4 µs PASS; 4…6 µs -> HW_MEAS_DWT fine method |
| reported rate vs MT-4 median | max \|x\| + u <= 1 % | 0 % | 0 | 1 | **PASS** |  |
| afe_reinit_count unchanged | == 0 | 0 | 0 | – | **PASS** |  |
| SCK-high / read time (MT-1 DWT) | ≤ 50 µs / ≤ 60 µs | – | – | – | **NOT MEASURED** | needs the HW_MEAS_DWT image (DWT not modelled in the twin) |
| SCK edges per read 25 / 27 / 26 (J-AUX = PD_SCK) | per gain, 100 reads | – | – | – | **TARGET-ONLY** | J-AUX not modelled in the twin |

<details><summary>Operator steps / answers (1)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| JUMPERS | Measurement header MH: J-EVT <- PB4 (HX711 DOUT) | MH (twin: selector = probe source) | twin-emulated |

</details>

### HG-18 — Main-loop and ISR budgets — F2 (C-18)

Verdict: **PARTIAL (NOT MEASURED open)** · requirements: NFR-005/006/007, FW-SW-002 · 2026-10-05 02:08:46 → 2026-10-05 02:08:55

Method: DWT sections 0…22 under 50 kHz + 80 Hz + 20 cmd/s and real E-stop / limit / PAUSE events

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| PSU-off block: parameters restored by REBOOT | GET_PARAM == originals | {'motion.steps_per_mm': [800.0, 800.0], 'motion.v_unhomed_um_s': [2000, 2000], 'drv.alm_active_level': [0, 0]} | – | – | **PASS** |  |
| main-loop pass (section 0) | ≤ 1000.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| TIM2 step ISR (F2) (section 1) | ≤ 2.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| E-stop handler EXTI15_10 (F2) (section 2) | ≤ 1.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| START limit EXTI0 (section 3) | ≤ 1.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| END limit EXTI1 (section 4) | ≤ 1.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| CRIT_HALT (PRIMASK) (section 11) | ≤ 1.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| CRIT_AFE (BASEPRI 0x20) (section 12) | ≤ 1.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| CRIT_MOTION (BASEPRI 0x20) (section 13) | ≤ 1.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| hal_step_stop_now (PRIMASK) (section 16) | ≤ 1.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| hal_step_abort (PRIMASK) (section 17) | ≤ 1.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| hal_step_set_period_now (PRIMASK) (section 18) | ≤ 1.0 µs | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| step CPU at 50 kHz | ≤ 15 % | – | – | – | **NOT MEASURED** | DWT not modelled in the twin (w0 = 0) |
| loop_max_us (FW self-report, NFR-006) | max + u <= 1000 µs | 0 µs | 1 | 1 | **PASS** | twin: loop time not modelled (reads 0) |
| stack_free_min | min - u >= 1 bytes | 3072 bytes | 0 | 1 | **PASS** |  |

<details><summary>Operator steps / answers (24)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| PSU-OFF | Switch the 48 V driver PSU OFF (mains switch). Driver signal cable stays connected. Confirm the motor shaft turns fre… | done | twin-emulated |
| PSU-ON | Switch the 48 V driver PSU back ON; wait 2 s | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +47.1 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +158.7 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +192.7 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +48.2 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +67.0 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS-IDLE | PRESS the red E-stop NOW (idle, enabled) | scheduled +148.8 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS-IDLE | PRESS the red E-stop NOW (idle, enabled) | scheduled +121.4 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS-IDLE | PRESS the red E-stop NOW (idle, enabled) | scheduled +102.2 ms | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PAUSE | Press the PAUSE button NOW (table moving) | scheduled +66.5 ms | twin-emulated |
| PAUSE-REL | Release the PAUSE button | done | twin-emulated |
| PAUSE | Press the PAUSE button NOW (table moving) | scheduled +33.9 ms | twin-emulated |
| PAUSE-REL | Release the PAUSE button | done | twin-emulated |
| PAUSE | Press the PAUSE button NOW (table moving) | scheduled +65.3 ms | twin-emulated |
| PAUSE-REL | Release the PAUSE button | done | twin-emulated |

</details>

Notes:

- driver unpowered reads ALM active -> drv.alm_active_level flipped in RAM for the PSU-off block (D-28 start-block would refuse the pulse trains); restored by the REBOOT

### HG-27 — Command response sample (NFR-008)

Verdict: **PASS** · requirements: NFR-008 · 2026-10-05 02:08:55 → 2026-10-05 02:08:56

Method: 1000 commands under streaming; NVM ops

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| command response (PC round trip, upper bound) | max + u <= 10 ms | 2 ms | 0 | 1000 | **PASS** | 1000 commands of 19 types under streaming; twin: virtual time |
| unexpected NACKs | == 0 | 0 | 0 | – | **PASS** | [] |
| SAVE / LOAD / DEFAULT response | max + u <= 2500 ms | 2.318 ms | 0 | 4 | **PASS** | DEFAULT followed by LOAD restores the stored values |
| NVM commands OK | status OK | {'SAVE_PARAMS': [['OK', 2.318]], 'LOAD_PARAMS': [['OK', 0.5], ['OK', 0.5]], 'DEFAULT_PARAMS': [['OK', 0.5]]} | – | – | **PASS** |  |

### IMG-MEAS2 — PO flashes HW_MEAS again; image verified

Verdict: **PASS** · requirements: – · 2026-10-05 02:08:56 → 2026-10-05 02:08:56

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| build | recorded | host | – | – | **INFO** |  |
| param dict hash | == 0xB7B0263F | 0xB7B0263F | 0 | – | **PASS** | ICD v0.7.1 dict 5 |
| FEAT_HW_MEAS = 1 | measurement image | ['AFE', 'MOTION', 'HOMING', 'MOVE_UNTIL_LOAD', 'NVM', 'TWIN', 'BUTTONS', 'DRV_SIGNALS', 'HW_MEAS'] | – | – | **PASS** |  |
| variant | twin: MEAS \| TWIN_MODEL | ['MEAS', 'TWIN_MODEL'] | – | – | **PASS** |  |

Notes:

- twin: engine restarted with hw_meas=True (stand-in for flashing nucleo_f446re_meas (HW_MEAS))

### HG-21 — DRV_PWR sense (optional, D-41)

Verdict: **N/A** · requirements: (SAF-FW-024, FW-SW-005) · 2026-10-05 02:08:56 → 2026-10-05 02:08:56

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| DRV_PWR sense reaction | ≤ 25 ms | – | – | – | **N/A** | no presence sense fitted (D-41, CR-03 default) |

<details><summary>Operator steps / answers (1)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| FITTED | Is the optional 48 V presence sense on PA7 fitted (wiring §7.2)? | False | dry-run default (not evidence) |

</details>

### HG-22 — K1_WELDED (N/A, D-41)

Verdict: **N/A** · requirements: (SAF-FW-025) · 2026-10-05 02:08:56 → 2026-10-05 02:08:56

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| K1_WELDED | N/A | – | – | – | **N/A** | no contactor since D-41; feature verified in the twin (TC-SAF-FW-025-01) |

### HG-23 — Buffer board SN74ACT244 (C-22)

Verdict: **INCONCLUSIVE** · requirements: SYS-011, SYS-009 · 2026-10-05 02:08:56 → 2026-10-05 02:08:58

Method: inspection, I_LED, HG-07 / HG-08 repeat

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| buffer board installed | before the first calibration (D-28) | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |
| PUL STATIC_LEVEL accepted | == OK | OK | 0 | – | **PASS** |  |
| PUL driven high (twin edge log) | pin level 1 while STATIC_LEVEL | {'t_us': 404673.612, 'pin': 'PUL', 'level': 1} | – | – | **PASS** |  |
| PUL I_LED (buffer) | 10 <= value ± u <= 13 mA | 11.5 … 11.5 | 0.1 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| DIR STATIC_LEVEL accepted | == OK | OK | 0 | – | **PASS** |  |
| DIR driven high (twin edge log) | pin level 1 while STATIC_LEVEL | {'t_us': 406923.612, 'pin': 'DIR', 'level': 1} | – | – | **PASS** |  |
| DIR I_LED (buffer) | 10 <= value ± u <= 13 mA | 11.5 … 11.5 | 0.1 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| ENA I_LED (buffer) | 10 <= value ± u <= 13 mA | 11.5 … 11.5 | 0.1 | 1 | **MANUAL** | operator input: dry-run default (not evidence) |
| buffer disabled = shaft free | ENA LED current = driver disabled | True | – | – | **PASS** | twin-observed (world model, not HW evidence) |
| buffer JOG during ENA settle refused | E_BUSY ENABLING | {'status': 'E_BUSY', 'detail': 2, '_rtt_ms': 25.5, '_pc_ns': 447500000} | – | – | **PASS** |  |
| buffer enabled = holding | no ENA current = enabled | True | – | – | **PASS** | twin-observed (world model, not HW evidence) |
| buffer first PUL/DIR edge − ENA enable edge | min - u >= 500000 µs | 6.019e+05 µs | 11 | 2 | **PASS** | motion.ena_settle_ms = 500; ENA edge 70 µs after the ENABLE frame |
| PSU-off block: parameters restored by REBOOT | GET_PARAM == originals | {'motion.steps_per_mm': [800.0, 800.0], 'motion.v_unhomed_um_s': [2000, 2000], 'drv.alm_active_level': [0, 0]} | – | – | **PASS** |  |
| buffer a PWM statistics free of the first-capture artifact | min ≈ max during cruise | {'pwm_min_period': 0, 'pwm_max_period': 1800, 'pwm_min_high': 900, 'pwm_max_high': 900} | – | – | **INCONCLUSIVE** | min values corrupted by the first capture after arming (DEF-HG-01 board HAL / OBS-E-HG-05 twin model); periods evalua… |
| buffer a pulses measured | min - u >= 100000 pulses | 1.05e+05 pulses | 0 | 1 | **PASS** |  |
| buffer a PUL high | min - u >= 10 µs | 10 µs | 0.01111 | 1 | **INCONCLUSIVE** |  |
| buffer a PUL low (period_min − high_max) | min - u >= 10 µs | 10 µs | 0.02222 | 1 | **INCONCLUSIVE** |  |
| buffer a step rate ≤ 50 kHz (period) | min - u >= 20 µs | 20 µs | 0.01111 | 1 | **INCONCLUSIVE** |  |
| buffer b DIR setup (MT-3, 1 PUL per reversal) | min - u >= 20 µs | 4990 µs | 0.1 | 100 | **PASS** | 100 reversals, 0 invalid |
| buffer b DIR setup (MT-4 stamps, cross-check) | min - u >= 20 µs | 4990 µs | 2 | 100 | **PASS** |  |
| buffer b MT-3 vs MT-4 agree | max + u <= 2.1 µs | 0 µs | 0 | 100 | **PASS** |  |
| buffer b reversals | min - u >= 100 trials | 100 trials | 0 | 1 | **PASS** |  |

<details><summary>Operator steps / answers (14)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| INSP | SN74ACT244 buffer board installed (SYS-011): 5 V supply, pull-downs, outputs to PUL+/DIR+ and via D1 to ENA+ (wiring … | True | dry-run default (not evidence) |
| TAPS | Move the MH taps J-PUL-A / J-PUL-B / J-DIR / J-ENA to the buffer outputs | skipped | dry-run: manual step not emulated |
| PUL-VOH | PUL held high by DIAG_MEAS STATIC_LEVEL: DMM VOH at the driver terminal PUL+ vs logic GND [V] | 4.9 | dry-run default (not evidence) |
| PUL-ILED | PUL held high by DIAG_MEAS STATIC_LEVEL: DMM voltage over the 100 Ω shunt in series with PUL− [mV] | 1150 | dry-run default (not evidence) |
| DIR-VOH | DIR held high by DIAG_MEAS STATIC_LEVEL: DMM VOH at the driver terminal DIR+ vs logic GND [V] | 4.9 | dry-run default (not evidence) |
| DIR-ILED | DIR held high by DIAG_MEAS STATIC_LEVEL: DMM voltage over the 100 Ω shunt in series with DIR− [mV] | 1150 | dry-run default (not evidence) |
| ENA-VOH | ENA at the disabled level (NOT_ENABLED = LED current): DMM VOH at the driver terminal ENA+ vs logic GND [V] | 4.9 | dry-run default (not evidence) |
| ENA-ILED | ENA at the disabled level (NOT_ENABLED = LED current): DMM voltage over the 100 Ω shunt in series with ENA− [mV] | 1150 | dry-run default (not evidence) |
| JUMPERS | Measurement header MH: J-EVT <- PA3 (USART2 RX), J-ENA, J-PUL-A, J-DIR fitted | MH (twin: selector = probe source) | twin-emulated |
| FREE | NOT_ENABLED (DISABLE sent): motor shaft FREE by hand (no load!)? | True | twin-observed |
| HOLD | After ENABLE + settle: shaft HOLDING? | True | twin-observed |
| JUMPERS | Measurement header MH: J-PUL-A, J-DIR fitted; J-EVT <- DIR node | MH (twin: selector = probe source) | twin-emulated |
| PSU-OFF | Switch the 48 V driver PSU OFF (mains switch). Driver signal cable stays connected. Confirm the motor shaft turns fre… | done | twin-emulated |
| PSU-ON | Switch the 48 V driver PSU back ON; wait 2 s | done | twin-emulated |

</details>

Notes:

- driver unpowered reads ALM active -> drv.alm_active_level flipped in RAM for the PSU-off block (D-28 start-block would refuse the pulse trains); restored by the REBOOT

### GATE-LOAD — SYS-009 gate: prerequisites before the first load

Verdict: **N/A** · requirements: SYS-009 · 2026-10-05 02:08:58 → 2026-10-05 02:08:59

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| SYS-009 prerequisites for load (twin: reported, not enforced) | recorded | 15 open | – | – | **INFO** | HG-01: OPEN (MANUAL); HG-02: INCONCLUSIVE; HG-04: PARTIAL (TARGET-ONLY open); HG-05: PARTIAL (NOT MEASURED, TARGET-ON… |

Notes:

- twin dry run: the gate is reported but not enforced (MANUAL items cannot pass in the twin)

### HG-12 — FW load-limit reaction (C-12)

Verdict: **PASS** · requirements: SAF-FW-002/008 · 2026-10-05 02:08:59 → 2026-10-05 02:09:07

Method: spring; threshold above the present raw; MT-4 deciding DOUT -> last PUL; MT-3 RESET mode

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| trip on the first violating sample | FAULT_SET(LOAD_LIMIT).value == first DATA raw > threshold | 100/100 | – | – | **PASS** |  |
| deciding DOUT edge -> last PUL (MT-4) | max + u <= 200 µs | 0 µs | 2 | 100 | **PASS** |  |
| MT-3 (RESET mode) vs MT-4 agree | max + u <= 2 µs | 0 µs | 0 | 89 | **PASS** | 89 trials read before the next DOUT edge |

<details><summary>Operator steps / answers (2)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| SPRING | Fit the spring specimen (contact ahead of the table in +x); hand at the E-stop | done | twin-emulated |
| JUMPERS | Measurement header MH: J-EVT <- PB4 (DOUT), J-PUL-A fitted | MH (twin: selector = probe source) | twin-emulated |

</details>

### HG-15 — Reset under load (C-15, D-33 e)

Verdict: **PASS** · requirements: SAF-FW-018 · 2026-10-05 02:09:07 → 2026-10-05 02:09:09

Method: ≥ 98 N preload; NRST / Nucleo power / IWDG; dial; MT-2 = 0

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| preload ≥ 98 N (raw) | min - u >= 4.048e+05 counts | 4.114e+05 counts | 0 | 1 | **PASS** | 3285.0 counts/N (nominal cell; override with --counts-per-n) |
| PIN: reset cause | == PIN | PIN | 0 | – | **PASS** |  |
| PIN: MT-2 = 0 from boot until ENABLE | == 0 | 0 | 0 | – | **PASS** |  |
| PIN: raw change (minus commanded motion × slope) | max \|x\| + u <= 214.2 counts | 0 counts | 0 | 1 | **PASS** | noise σ 0 counts, slope 65700 counts/mm |
| PIN: axis motion (dial − commanded) | max \|x\| + u <= 0.01 mm | 0 mm | 0.001 | 1 | **PASS** | D-13: the driver keeps holding; twin-observed (world model, not HW evidence) |
| POWER: reset cause | == POWER_ON | POWER_ON | 0 | – | **PASS** |  |
| POWER: MT-2 = 0 from boot until ENABLE | == 0 | 0 | 0 | – | **PASS** |  |
| POWER: raw change (minus commanded motion × slope) | max \|x\| + u <= 214.2 counts | 0 counts | 0 | 1 | **PASS** | noise σ 0 counts, slope 65700 counts/mm |
| POWER: axis motion (dial − commanded) | max \|x\| + u <= 0.01 mm | 0 mm | 0.001 | 1 | **PASS** | D-13: the driver keeps holding; twin-observed (world model, not HW evidence) |
| IWDG: reset cause | == IWDG | IWDG | 0 | – | **PASS** |  |
| IWDG: MT-2 = 0 from boot until ENABLE | == 0 | 0 | 0 | – | **PASS** |  |
| IWDG: raw change (minus commanded motion × slope) | max \|x\| + u <= 214.2 counts | 0.125 counts | 0 | 1 | **PASS** | noise σ 0 counts, slope 65700 counts/mm |
| IWDG: axis motion (dial − commanded) | max \|x\| + u <= 0.01 mm | 0 mm | 0.0035 | 1 | **PASS** | D-13: the driver keeps holding; twin-observed (world model, not HW evidence) |

<details><summary>Operator steps / answers (7)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| SPRING | Fit the spring specimen (contact ahead of the table in +x); hand at the E-stop | done | twin-emulated |
| DIAL | Set the dial indicator on the table and zero it | skipped | dry-run: manual step not emulated |
| NRST | Press and release the Nucleo RESET button (B2) | done | twin-emulated |
| DIAL-PIN | Dial indicator reading now [mm] | 0 | twin-observed |
| PWR | Power-cycle the Nucleo only (USB / 5 V off 3 s, on); the 48 V PSU stays ON | done | twin-emulated |
| DIAL-POWER | Dial indicator reading now [mm] | 0 | twin-observed |
| DIAL-IWDG | Dial indicator reading now [mm] | -0.00125 | twin-observed |

</details>

Notes:

- twin: the axis model is not back-driven by the spring when ENA is released, so the dial check cannot fail in the twin; the FW part (counter 0, ENA left enabled at boot) is exercised

### HG-25 — Speed envelope (SYS-004)

Verdict: **PASS** · requirements: SYS-004 · 2026-10-05 02:09:09 → 2026-10-05 02:09:17

Method: 0.01 mm/s dial, 10 mm/s caliper, 30 mm/s, re-home, travel

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| 0.01 mm/s over 2 mm: speed error | max \|x\| + u <= 1 % | 0.06254 % | 0.5 | 1 | **PASS** | dial u 0.5 %; duration from MT-4 first / last PUL; twin-observed (world model, not HW evidence) |
| 10 mm/s over 50 mm: cruise speed error | max \|x\| + u <= 1 % | 0.1021 % | 0.04 | 1 | **PASS** | T 5.0950 s, ramps removed with a = 100000 µm/s²; twin-observed (world model, not HW evidence) |
| 30 mm/s unloaded: no ALM | no ALM_CHANGED | [] | – | – | **PASS** |  |
| re-home drift | max + u <= 200 µm | 0 µm | 1.25 | 1 | **PASS** | HOMED value |
| no HOME_DRIFT fault |  | [] | – | – | **PASS** |  |
| usable travel (FW) | min - u >= 280 mm | 289.5 mm | 0 | 1 | **PASS** |  |
| usable travel (measured) | min - u >= 280 mm | 289.5 mm | 0.05 | 1 | **PASS** | twin-observed (world model, not HW evidence) |

<details><summary>Operator steps / answers (7)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| CAL | Travel calibration done (motion.steps_per_mm calibrated with the SW wizard, M3)? | True | dry-run default (not evidence) |
| NOSPEC | Remove the spring specimen (unloaded speed envelope) | done | twin-emulated |
| DIAL0 | Dial indicator on the table at 10 mm, zeroed | skipped | dry-run: manual step not emulated |
| DIAL1 | Dial indicator travel after the 0.01 mm/s move [mm] | 2 | twin-observed |
| CAL0 | Caliper reference on the table at 20 mm | skipped | dry-run: manual step not emulated |
| CAL50 | Caliper: travel of the 10 mm/s move (20 -> 70 mm) [mm] | 50 | twin-observed |
| TRAVEL | Caliper / scale: travel soft_min -> soft_max [mm] | 289.5 | twin-observed |

</details>

### HG-26 — Home repeatability (FW-HOM-003)

Verdict: **PASS** · requirements: FW-HOM-003 · 2026-10-05 02:09:17 → 2026-10-05 02:09:22

Method: 10 homing cycles, dial

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| home repeatability (2σ) | max + u <= 0.02 mm | 0 mm | 0.001 | 1 | **PASS** | 10 cycles; twin-observed (world model, not HW evidence) |
| FW HOMED drift values (µm) | recorded | [0, 0, 0, 0, 0, 0, 0, 0, 0, 0] | – | – | **INFO** |  |

<details><summary>Operator steps / answers (11)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| DIAL | Dial indicator touching the table at x = 0 (after HOME), zeroed | skipped | dry-run: manual step not emulated |
| DIAL0 | Dial reading at x = 0 after HOME [mm] | -10 | twin-observed |
| DIAL1 | Dial reading at x = 0 after HOME [mm] | -10 | twin-observed |
| DIAL2 | Dial reading at x = 0 after HOME [mm] | -10 | twin-observed |
| DIAL3 | Dial reading at x = 0 after HOME [mm] | -10 | twin-observed |
| DIAL4 | Dial reading at x = 0 after HOME [mm] | -10 | twin-observed |
| DIAL5 | Dial reading at x = 0 after HOME [mm] | -10 | twin-observed |
| DIAL6 | Dial reading at x = 0 after HOME [mm] | -10 | twin-observed |
| DIAL7 | Dial reading at x = 0 after HOME [mm] | -10 | twin-observed |
| DIAL8 | Dial reading at x = 0 after HOME [mm] | -10 | twin-observed |
| DIAL9 | Dial reading at x = 0 after HOME [mm] | -10 | twin-observed |

</details>

### IMG-REL — PO flashes the release image

Verdict: **PASS** · requirements: SYS-009 · 2026-10-05 02:09:22 → 2026-10-05 02:09:22

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| build | recorded | host | – | – | **INFO** |  |
| param dict hash | == 0xB7B0263F | 0xB7B0263F | 0 | – | **PASS** | ICD v0.7.1 dict 5 |
| FEAT_HW_MEAS = 0 | release: no HW_MEAS | ['AFE', 'MOTION', 'HOMING', 'MOVE_UNTIL_LOAD', 'NVM', 'TWIN', 'BUTTONS', 'DRV_SIGNALS'] | – | – | **PASS** |  |
| DIAG_MEAS -> NOT_IN_BUILD | E_INTERNAL detail 1, nothing executed | {'status': 'E_INTERNAL', 'detail': 1, '_rtt_ms': 0.5, '_pc_ns': 401500000} | – | – | **PASS** |  |

Notes:

- twin: engine restarted with hw_meas=False (stand-in for flashing nucleo_f446re (release))

### HG-30 — Release-image confirmation (+ HG-32 repeat, HG-31)

Verdict: **PARTIAL (MANUAL open)** · requirements: SYS-009 (R-4) · 2026-10-05 02:09:22 → 2026-10-05 02:09:26

Method: GET_INFO / NOT_IN_BUILD; E-stop, limit, load limit, STOP / HALT / PAUSE, HOME, soak, loop_max_us

| Check | Criterion | Value | u | n | Decision | Note |
|---|---|---|---|---|---|---|
| build | recorded | host | – | – | **INFO** |  |
| param dict hash | == 0xB7B0263F | 0xB7B0263F | 0 | – | **PASS** | ICD v0.7.1 dict 5 |
| FEAT_HW_MEAS = 0 | release: no HW_MEAS | ['AFE', 'MOTION', 'HOMING', 'MOVE_UNTIL_LOAD', 'NVM', 'TWIN', 'BUTTONS', 'DRV_SIGNALS'] | – | – | **PASS** |  |
| DIAG_MEAS -> NOT_IN_BUILD | E_INTERNAL detail 1, nothing executed | {'status': 'E_INTERNAL', 'detail': 1, '_rtt_ms': 0.5, '_pc_ns': 402750000} | – | – | **PASS** |  |
| drv.pwr_sense_enable default | == 0 | 0 | 0 | – | **PASS** | CR-03 |
| drv.k1_check_enable default | == 0 | 0 | 0 | – | **PASS** | D-43 e |
| E-stop held > 1 s: only ESTOP | ESTOP latched, no K1_WELDED / fault | {'flags': ['ESTOP'], 'faults': []} | – | – | **PASS** |  |
| ENABLE refused by ESTOP only | E_STATE BLOCK = ESTOP (no DRV_UNPOWERED) | {'status': 'E_STATE', 'detail': 1, '_rtt_ms': 0.25, '_pc_ns': 2443000000} | – | – | **PASS** |  |
| ESTOP_CLEAR after release | == OK | OK | 0 | – | **PASS** |  |
| E-stop: latch + ENA disabled + EVENTs | ESTOP_SET, STOPPED(ESTOP), MOVE_DONE | ['ESTOP_SET', 'STOPPED', 'DRIVER_DISABLED', 'MOVE_DONE'] | – | – | **PASS** |  |
| E-stop: shaft free | driver disabled | True | – | – | **PASS** | twin-observed (world model, not HW evidence) |
| limit stop + latch | LIMIT_SET, STOPPED(LIMIT_START), JOG toward refused | {'stopped': [{'t_us': 31861000, 'code': 'STOPPED', 'arg': 8, 'value': -1000, 'value2': -800, '_pc_ns': 31861750000}],… | – | – | **PASS** |  |
| load-limit trip | FAULT_SET(LOAD_LIMIT) | {'t_us': 44338000, 'code': 'FAULT_SET', 'arg': 0, 'value': 168671, 'value2': 97845, '_pc_ns': 44347500000} | – | – | **PASS** |  |
| STOP during a jog | STOPPED(PC_STOP) | [{'t_us': 49111097, 'code': 'STOPPED', 'arg': 1, 'value': 52504, 'value2': 42003, '_pc_ns': 49113250000}] | – | – | **PASS** |  |
| HALT during a jog | STOPPED(PC_HALT) | [{'t_us': 49756336, 'code': 'STOPPED', 'arg': 3, 'value': 52503, 'value2': 42002, '_pc_ns': 49758500000}] | – | – | **PASS** |  |
| PAUSE during a jog | STOPPED(PC_PAUSE) | [{'t_us': 50401586, 'code': 'STOPPED', 'arg': 6, 'value': 52503, 'value2': 42002, '_pc_ns': 50403750000}] | – | – | **PASS** |  |
| RESUME clears PAUSED | == OK | OK | 0 | – | **PASS** |  |
| HOME | MOVE_DONE TARGET, HOMED | {'t_us': 69549000, 'code': 'MOVE_DONE', 'arg': 0, 'value': 0, 'value2': 0, '_pc_ns': 69550750000} | – | – | **PASS** |  |
| 2-min soak: seq gaps / CRC / FW errors | == 0 | 0 | 0 | – | **PASS** | 9616 DATA frames in 120 s |
| loop_max_us (release image) | max + u <= 1000 µs | 0 µs | 1 | 1 | **PASS** | twin: not modelled |
| HG-31 release image with jumpers fitted | no loopback pin analog | True | – | – | **MANUAL** | operator input: dry-run default (not evidence) |

<details><summary>Operator steps / answers (8)</summary>

| Key | Prompt | Answer | Source |
|---|---|---|---|
| PRESS-HOLD | Press and HOLD the red E-stop (≥ 2 s) | done | twin-emulated |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| PRESS | PRESS the red E-stop NOW (table moving) | scheduled +33.8 ms | twin-emulated |
| FREE | Shaft free by hand? | True | twin-observed |
| ESTOP-RELEASE | Release the red E-stop (turn to release) if it is pressed | done | twin-emulated |
| SPRING | Fit the spring specimen (contact ahead of the table in +x); hand at the E-stop | done | twin-emulated |
| SPRING | Is the spring specimen fitted (load-limit repeat)? | True | twin-observed |
| HG31 | HG-31: the release image runs with the MH jumpers fitted (no pin in analog mode — A's statement) — confirmed? | True | dry-run default (not evidence) |

</details>

