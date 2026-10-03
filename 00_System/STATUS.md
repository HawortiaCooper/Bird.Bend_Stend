# Project status

**Phase:** P0 Research — **closed** (Orchestrator review 2026-10-03) · P1 Specification — **in progress** (PO answered all 24 questions → D-10..D-25) · **Date:** 2026-10-03

## P0 gate (Orchestrator, 2026-10-03) — ACCEPTED
R1–R4 written; conflicts resolved: (a) Stefan step-timer error: checked `Stefan/FW/stanok/Core/Src/main.c:136-158` + `tim.c:45` → TIM2 84 MHz / (9+1) = 8.4 MHz vs assumed 10 MHz → 16 % slow (R1/R4 correct, R2's 6.7 % ignores the prescaler); irrelevant for us (D-09). (b) Step timing: SRS takes R2's conservative defaults (≥ 10 µs high/low, ≤ 50 kHz, DIR setup 20 µs) as parameters; R4's TIM2 PWM-mode-2 scheme stays (CCR scaled). Key safety finding R2 §8: motor force ≈ 9 kN ≫ cell safe overload 2.35 kN → FW per-sample load limit is safety-critical.

| Artifact | Owner | State |
|---|---|---|
| PROCESS.md, CLAUDE.md, role agents | Orchestrator | v1 (2026-10-03) |
| specs/DECISIONS.md | Orchestrator | D-01…D-25 (PO answers 2026-10-03; D-10 amended by D-11) |
| research/R1 Stefan reference (pinout, init, stepper GUI) | Researcher | done 2026-10-03 (Stefan FW never ran on HW: ENA/limits unconfigured, telemetry dead, baud mismatch → knowledge only) |
| research/R2 Hardware components | Researcher | done 2026-10-03 (F446 DS/RM/UM PDFs timed out → those values ASSUMED, A to verify) |
| P1_QUESTIONS.md | Orchestrator | 21 consolidated PO questions with defaults (supersedes the per-R lists below) |
| research/R3 Thrust_Stand_HAW reuse (FW drivers, protocol, tools, GUI) | Researcher | done 2026-10-03 (pinned to TS HEAD 9473c68; TS never ran on HW either; calib/tare/limits/recorder/sequencer were design-only there) |
| research/R4 Methods (motion, calibration, tare, steady state, safety) | Researcher | done 2026-10-03 (22 reference test functions pass vs pure-Python ref, run with shim — project .venv not yet created) |
| specs/SRS.md, ICD_protocol.md, params.yaml | Orchestrator / Integrator | P1 — not started |

## Open questions for the product owner
(collected during P0, presented at the P1 review; consolidated/de-duplicated before asking)

From R1: Q-R1-01 HBS86H input wiring (CA/CC/buffer) + ENA pull for reset-safe disable · Q-R1-02 ALM/PEND wired? · Q-R1-03 limit switch type, START = home? · Q-R1-04 E-stop NC into MCU only or also HW path? · Q-R1-05 mechanics (pitch, microstep, travel, max speed/accel) · Q-R1-06 GUI slider = jog speed or target position? · Q-R1-07 same Nucleo as Stefan, solder bridges changed? · Q-R1-08 HX711 module, RATE reachable, 3.3 V logic? · Q-R1-09 did Stefan's stand ever run on HW?

From R3: Q-R3-01 DATA may carry actual position + u16 status beyond D-05? · Q-R3-02 one DATA frame per HX711 sample vs fixed rate · Q-R3-03 setpoint in µm or steps (Integrator) · Q-R3-04 NVM sectors 1+2 vs last 128 KB (A/R2) · Q-R3-05 Pause/Break = full E-STOP or pause script + stop motor? · Q-R3-06 load-target sequence steps needed in M4? · Q-R3-07 = Q-R1-08 (RATE strap) · Q-R3-08 921600 over ST-LINK VCP unproven (Stefan never ran on HW → first-board check) · Q-R3-09 clock: stm32duino variant uses HSI 180 MHz (R1) → closed unless board has HSE fitted.

From R4 (recommended defaults in R4 §14): Q-R4-01 **safety: holding E-stop (D-10) ≈ stop cat. 2, IEC 60204-1 E-stop must be cat. 0/1 → NC button = machine stop + hardwired power-cut E-stop if crosshead is reachable** · Q-R4-02 behaviour on MCU reset/power loss under load · Q-R4-03 = Q-R3-05 Pause/Break · Q-R4-04 max load during homing (5 % FS) · Q-R4-05 travel-cal instrument (caliper, total 60 mm) · Q-R4-06 separate push calibration (no) · Q-R4-07 available weights · Q-R4-08 = Q-R1-05 mechanics (placeholder 640 steps/mm, 400 mm, 20 mm/s, 100 mm/s²) · Q-R4-09 linearity limits 0.1/0.5 % span · Q-R4-10 load targets = approach + trim · Q-R4-11 link loss → controlled stop, hold · Q-R4-12 idle auto-disable 600 s if unloaded · Q-R4-13 32-bit µs timestamp · Q-R4-14 3-point-bend stress/strain optional

## Notes for P1 (Orchestrator)
- From R4: TIM2 PWM mode 2 + preload, one update IRQ/step, exact sqrt ramp; setpoint = commanded position latched at HX711 DRDY; frame seq counter + MOVING flag; D-10 stop/disable policy table (R4 §4); travel cal uses total 60 mm; linearity by max residual % span (not R²); tare on PC (recordings stay raw); FW load-limit thresholds in raw counts pushed by PC after tare/cal. Project `.venv` must be created before M1 (Orchestrator/B).
- From R3: no relative moves on the wire (retry would double-move) → PC converts jog-by-distance to absolute targets; plot performance was an open High issue in TS (SWD-PM3-05) → lean plot dock + X-Y pane, perf acceptance criterion in SRS; HW-check list from day 1 (R3 §7.2); avoid ICD churn, single vector generator, no hand-numbered TC explosion.
- SRS candidates from R1: NC interrupt-driven E-stop keeping ENA (D-10); no runt step pulse on stop; PC stop path bypasses the command queue; FW jog watchdog; echo seq numbers in replies.
- stm32duino build flags needed: `-DHAL_TIM_MODULE_ONLY -DHAL_UART_MODULE_ONLY -DHAL_EXTI_MODULE_DISABLED -DTICK_INT_PRIORITY=5` (R1 §6.2).
