# P3 readiness — what is done, what the product owner needs to do

**Date:** 2026-10-05 · **State:** P2 complete — M1…M4 ACCEPTED WITH CONDITIONS; every remaining condition needs the PO, the bench or the reference PC.
**Baseline:** SRS v0.6.4 (172 active requirements) · ICD v0.7.4 (dict 6, hash 0xF8BCDCB8) · FW_design v0.7 · SW_design v0.6 · SW_design_GUI v0.6 · FW_test_plan v0.4.3 · SW_test_plan v0.5.

## 1. Evidence on the simulator / FW twin (no hardware, D-06)
| Area | Result |
|---|---|
| FW host tests (implementer + validator) | 211 pass; release image 45.5 KB flash / 8.7 KB RAM; map check, HW_MEAS byte-identity PASS |
| FW twin validation suite | 246 pass ×2 (incl. 32/32 link-watchdog cases vs an independent oracle) |
| PC software, full suite | 3352 tests ×3 identical runs, 0 failed / skipped / xfail |
| Integration PC ⇄ real FW in the twin | 171 pass (lock-step, deterministic) |
| HW-gate rehearsal (HIL scripts vs twin) | 44 steps: 0 FAIL / 0 INCONCLUSIVE; only manual / target-only parts remain |
| Traceability | every requirement has a design section and ≥ 1 test case |

Reports: `02_FW/docs/FW_test_report_M1…M4.md`, `03_SW/docs/SW_test_report_M1…M4.md`.

## 2. What the product owner needs to do

### A. Hardware gate (FW_test_plan §6, runbook `00_System/tools/hil/HW_GATE_RUNBOOK.md`)
Bench procedure §6.8 is **approved** (D-49 b). To run the gate:
1. **Prepare the bench:** SN74ACT244 buffer board (perfboard is fine), D-42 E-stop NO contact → ENA (wiring.md §2.1), HX711 RATE wire to PB5, measurement-header jumpers with 1 kΩ / 220 Ω resistors, multimeter, caliper, 1 kg + 10 kg weights, spring for HG-25.
2. **Set the driver DIP switches** (D-27): SW1 on, SW2 off, SW3 on, SW4 off (4000 p/rev), SW5 off, SW6 on, SW7 off, SW8 on (closed loop) — with the driver powered off.
3. **Give a date** → the Orchestrator records the dedicated `D-06-GATE-YYYYMMDD` approval row; only then do the HIL scripts open a COM port.
4. **During the session** you flash the three images (release, HW_MEAS, HW_MEAS_DWT) and perform the manual steps; Validator E drives the scripts.

Open items to be decided at the gate: HG-18 measures the E-stop handler (static bound 1.15 µs vs 1 µs budget) and the step ISR (2.4 µs vs 2 µs); an overrun becomes a defect. First motion = a short slow jog checking direction and scale.

### B. Reference (lab) PC
Send the spec (CPU, RAM, screen, Windows version) and give access for: plot refresh ≥ 20 fps (NFR-001), STOP click and Pause/Break → frame ≤ 50 ms (NFR-002/003), 1 h soak (NFR-004, also decides MC3-6: the occasional false LINK LOST seen only on the loaded dev PC), system-wide Pause/Break hotkey.

### C. Demonstrations (with Validator F)
Fresh install (DM-02) · full workflow on the simulator (DM-01) · two-monitor layout restore and 4-pane smoothness (DM-03/DM-11) · NVM buttons (DM-05) · Manual tab (DM-07) · sequence chart with live marker (DM-08) · Pause/Resume (DM-09) · HTML report (DM-10) · calibration wizards with the real weights (at the HW gate).

## 3. Known limitations to accept at P3 (SRS §8.1)
- **No hardwired power removal** (D-41): the E-stop is MCU/FW + the D-42 hardwired ENA cut; not an IEC 60204-1 emergency stop.
- The D-42 NO contact is not monitored by the FW (periodic functional check C-25).
- Pause/Break hotkey does not reach elevated (Administrator) windows.
- Calibration with 1 kg + 10 kg covers 5 % FS → LOW_SPAN warning; forces beyond 3× the largest weight are marked extrapolated.
- BREAK at standstill (D-49 a) is evaluated from t_reached + 100 ms; a break in the first 100 ms after a move is caught only by the FW load limit / NOT_ON_TARGET.
- ALM / PEND are reported only (plus the ALM start-block, D-28); automatic ALM reaction is a later release.
