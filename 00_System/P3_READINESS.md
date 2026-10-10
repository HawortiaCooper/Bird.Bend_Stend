# P3 readiness — what is done, what the product owner needs to do

**Date:** 2026-10-10 (first issue 2026-10-05) · **State:** P2 complete — M1…M4 ACCEPTED WITH CONDITIONS; pre-P3 tasks 1–7 done (independent FW + SW code reviews closed, packaging, manual, dev-PC perf); every remaining condition needs the PO, the bench or the reference PC.
**Baseline:** SRS v0.6.6 (172 active requirements) · ICD v0.7.5 (dict 6, hash 0xF8BCDCB8, no wire change since 0.7.4) · FW v0.8.5 (FW_design v0.8.5) · SW_design v0.6.8 · SW_design_GUI v0.6.1 · FW_test_plan v0.4.11 · SW_test_plan v0.5.3.

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

## 1a. Pre-P3 tasks 1–7 (2026-10-08…10)
| Area | Result |
|---|---|
| Independent FW + HIL + generator review (Validator E) | 21 findings (1 High) fixed by A/C/E and re-verified; **GO WITH CONDITIONS** (`02_FW/docs/FW_code_review.md` v1.2); FW host suites 243/243, validator 58/58 |
| Independent SW review (Validator F) | 37 findings (6 S2) — 36 closed and re-verified, 1 S4 backlog; **ACCEPTED WITH CONDITIONS** (`03_SW/docs/SW_code_review.md` v1.4); full suite 3630/3630 ×2 (fixed + random order) |
| ISR timing | path-based static bounds with bus wait states (`02_FW/tools/isr_wcet.py`); E-stop → PUL inactive ≤ 2.3 µs worst case (budget 100 µs); NFR-007 decided by DWT at HG-18 (D-51/D-52) |
| Windows distribution | `03_SW/packaging/build_dist.ps1`: one-folder build, no Python needed, manual + quick card included, hash-pinned dependencies, smoke 9/9; installer needs Inno Setup 6 |
| Operator documentation | `03_SW/docs/USER_MANUAL.md` + `QUICK_REFERENCE.md`, 40 screenshots, docs-drift test |
| Dev-PC performance (informative) | at low host load all budgets met (paint p95 38 ms, STOP click 19 ms, Pause 5 ms, limit→STOP 4 ms, 1 h soak 0 losses); latency grows above ~60–80 % host CPU |
| Traceability | 173 requirements: 0 without design / test case / code tag / test citation (HW/inspection-only listed) |

## 2. What the product owner needs to do

### A. Hardware gate (FW_test_plan §6, runbook `00_System/tools/hil/HW_GATE_RUNBOOK.md`)
Bench procedure §6.8 is **approved** (D-49 b). To run the gate:
1. **Prepare the bench:** SN74ACT244 buffer board (perfboard is fine), D-42 E-stop NO contact → ENA (wiring.md §2.1), HX711 RATE wire to PB5, measurement-header jumpers with 1 kΩ / 220 Ω resistors, multimeter, caliper, 1 kg + 10 kg weights, spring for HG-25.
2. **Set the driver DIP switches** (D-27): SW1 on, SW2 off, SW3 on, SW4 off (4000 p/rev), SW5 off, SW6 on, SW7 off, SW8 on (closed loop) — with the driver powered off.
3. **Give a date** → the Orchestrator records the dedicated `D-06-GATE-YYYYMMDD` approval row; only then do the HIL scripts open a COM port.
4. **During the session** you flash the three images (release, HW_MEAS, HW_MEAS_DWT) and perform the manual steps; Validator E drives the scripts.

Open items to be decided at the gate: HG-18 measures the E-stop reaction (≤ 1 µs), the step ISR (≤ 2 µs), the level-1 handlers (≤ 2.5 µs each, step ISR + largest ≤ 5 µs, D-52) and the IRQ-masked windows (≤ 1 µs; conservative static bounds 1.14–1.79 µs, OI-FW-51); an overrun becomes a defect (fixed-point ramp prepared as remedy for the step ISR, D-51). Bring the PC incident log `%APPDATA%\BirdBendStand\logs\bend_stand.log` from every gate run. First motion = a short slow jog checking direction and scale.

### B. Reference (lab) PC
Send the spec (CPU, RAM, screen, Windows version; a CPU below ~75 % of an i7-10700's single-thread speed will likely miss NFR-001/002 with all channels) and run `powershell -ExecutionPolicy Bypass -File 03_SW\tests\perf\run_ref_pc.ps1 -Out D:\bbs_perf_ref` (≈ 2 h 10 min, idle PC, interactive session; send the folder back) for: plot refresh ≥ 20 fps (NFR-001), STOP click and Pause/Break → frame ≤ 50 ms (NFR-002/003), 1 h soak (NFR-004, also decides MC3-6: the occasional false LINK LOST seen only on the loaded dev PC), system-wide Pause/Break hotkey.

### C. Demonstrations (with Validator F)
Fresh install (DM-02) · full workflow on the simulator (DM-01) · two-monitor layout restore and 4-pane smoothness (DM-03/DM-11) · NVM buttons (DM-05) · Manual tab (DM-07) · sequence chart with live marker (DM-08) · Pause/Resume (DM-09) · HTML report (DM-10) · calibration wizards with the real weights (at the HW gate).

## 3. Known limitations to accept at P3 (SRS §8.1)
- **No hardwired power removal** (D-41): the E-stop is MCU/FW + the D-42 hardwired ENA cut; not an IEC 60204-1 emergency stop.
- The D-42 NO contact is not monitored by the FW (periodic functional check C-25).
- Pause/Break hotkey does not reach elevated (Administrator) windows.
- Calibration with 1 kg + 10 kg covers 5 % FS → LOW_SPAN warning; forces beyond 3× the largest weight are marked extrapolated.
- BREAK at standstill (D-49 a) is evaluated from t_reached + 100 ms; a break in the first 100 ms after a move is caught only by the FW load limit / NOT_ON_TARGET.
- ALM / PEND are reported only (plus the ALM start-block, D-28); automatic ALM reaction is a later release.
- PC on-screen STOP latency depends on the GUI thread under heavy host load; the Pause/Break key (own thread) is the robust stop path (OBS-P3-02).

## 4. PO answers 2026-10-10 (D-54)
- Travel calibration must be available independently of the load calibration → fixing (SW-CAL-002, B + D, F verifies).
- 600 s × 4 windows at ≥ 20 fps desired → new NFR-009 (Should); GPU (OpenGL) rendering being tried (D).
- Driver-alarm reset procedure confirmed: E-stop → 48 V off/on → release → Clear E-STOP → Enable → HOME.
- D-42 ENA-cut check at the start of every test campaign confirmed.
- No code signing.
- Recordings-folder picker in the GUI → building (B + D).
- Fixed-point step ramp → prepared as a second, selectable FW image (A, E verifies); the default image is unchanged.
- HW gate date and reference-PC runs: waiting for the PO's go.
