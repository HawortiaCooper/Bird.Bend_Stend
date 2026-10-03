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
| specs/SRS.md | Orchestrator | **v0.2** DRAFT (165 req; Orchestrator-reviewed 2026-10-03) — for PO review |
| research/R5 interface & safety HW (E-stop circuit, ALM/PEND, buffer board, pin check) | Researcher | done 2026-10-03 — pin changes (DOUT PB4, RATE PB5, END PC1, STOP PC7, PAUSE PB6, DRV_POWER PA7) forwarded to A and C; SRS v0.3 deltas: ENA settle 500 ms, DRV_POWER sense, FAULT_K1, HOME_DRIFT; 12 PO questions Q-R5-01..12 |
| specs/ICD_protocol.md, params.yaml, tools (generator, ref codec, vectors) | Integrator (C) | ICD **v0.1** (PROTO 1.0, PAYLOAD 1; DATA 26 B frame, 34 EVENT codes, PAUSE cmd 0x3B) · params dict v1, 48 params, hash **0x13961802** · Orchestrator re-ran 2026-10-03: gen_params/gen_vectors `--check` exit 0, tools pytest 654/654 ×2 (fixed + random order) |
| 01_HW/pinout.md, wiring.md, 02_FW/docs/FW_design.md | Implementer A | v0.1 done 2026-10-03 (74/74 FW IDs traced; NVM sectors 1+2; NVIC E-stop 0 / limits+buttons 1 / step 2 / HX711 3; stop sniffer in 1 kHz tick; ICD needs forwarded to C) |
| 03_SW/docs/SW_design.md (backend + API) | Implementer B | v0.1 done 2026-10-03 (81/81 IDs traced; findings F-B-01..22, ICD ones forwarded to C) |
| 03_SW/docs/SW_design_GUI.md | Implementer D | in progress |
| FW/SW test plans | Validators E, F | P1 wave 3 (after designs) |
| `.venv` | Orchestrator | created 2026-10-03: PySide6-Essentials 6.11.2, pyqtgraph 0.14.0, numpy 2.5.3, pyserial 3.5, pytest 9.1.1 (+qt, cov, randomly), PyYAML 6.0.3, platformio 6.2.0 |

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

## P1 reconciliation queue (Orchestrator, to resolve after all designs are in → SRS v0.3)
- R5/D-28: ENA settle 500 ms; DRV_POWER sense; FAULT_K1; HOME_DRIFT; ALM blocks new motion when powered; PAUSE command on the wire (F-B-03, decided: FW PAUSED latch, PAUSE cmd).
- D-27: driver = PFDE HBS86H, currently open-loop 86 @ 6 A, 800 p/rev; OI-13 text (SW6 is acceleration assist, not motor type).
- From A: OI-FW-01 ENA settle 500 in SRS · OI-FW-02 FW-STR-002 2 ms vs long frames (cap frame size, sent to C) · OI-FW-06 SRS text for DRV_PWR/ALM start-block, decide FAULT_K1 and "DRV_PWR off → refuse motion + clear HOMED" · OI-FW-07 controlled stop at step period > 1 ms → CLEAN halt (accept in SRS wording) · OI-FW-12 default speeds vs R5 20/30 mm/s · OI-FW-15 host gcc = CLion MinGW 13.1 (closed, CLAUDE.md) · R-01 USART6 fallback clashes with STOP on PC7.
- From C (ICD §14): SD-01 PAUSE cmd in IF-012 · SD-02 R5 items (DRV_PWR, K1_WELDED, HOME_DRIFT, DRV_UNPOWERED/DRIVER_ALARM block bits, events, params) · SD-03 v_max split travel 30 / load 20 mm/s · SD-04 renames io.*, home.ref_switch · SD-05 zero_raw/load_raw session-only (not NVM) · SD-06 reboot-required params · SD-07 JOG no-bound = 0x80000000, MOVE_UNTIL_LOAD cmp field · SD-08 IF-005 wording (motion never auto-retried) · SD-09 max step rate 100 kHz, spm min 100 · SD-10 CLK_FALLBACK only in GET_STATUS/EVENT · OI-ICD-01 homing at END switch semantics (or restrict to START) · OI-ICD-03 aborted homing = event not latch · unreachable range ends under strict hard rules.
- Alignment pass needed: FW_design and SW_design were written before ICD v0.1 → A and B align to ICD v0.1 (EVENT 16 B, JOG bound, cmp field, PAUSE, vectors header pre-script).
- F-B-07 threshold out of FW range: refuse vs clamp inward · F-B-08 first-use flow without calibration (load limits enabled → motion disabled; zero_raw = 0 without tare) · F-B-09 SW-MAN-003 acceptance vs latest-wins while moving · F-B-10 count bands from K · F-B-11 Thrust_Stand HEAD moved to 0f4e55e (pin 9473c68; calib/tare modules only after pin → snapshot/re-pin needs PO ok) · F-B-12 ownership of package __init__/__main__/run*.bat/tests/conftest.py · F-B-13 tare session-only · F-B-14 load-step trim failure → stop sequence (PO) · F-B-18 dependency set · F-B-20 dwell from target reached; sequence targets relative to test zero · F-B-22 AFE mismatch invalidates calibration for load limits.
