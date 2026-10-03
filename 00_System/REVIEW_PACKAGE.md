# P1 Specification — review package for the product owner

**Date:** 2026-10-03 · **Gate:** P1 → P2 (implementation) · **Validator verdicts:** FW = YES WITH CONDITIONS, SW = YES WITH CONDITIONS
**Baseline:** SRS v0.4.1 (172 requirements) · ICD v0.4.1 (PROTO 1.0, PAYLOAD 1) · params dict v3 (48 params, hash 0xF0376293) ·
FW_design v0.3 · SW_design v0.3.1 · SW_design_GUI v0.3 · pinout/wiring v0.3 · FW test plan v0.1 (129 TCs) · SW test plan v0.1 (167 TCs)
**Evidence (Orchestrator re-run):** generators `--check` exit 0; tools test suite 813/813 (fixed + random order); traceability: 172/172 requirements designed and covered by ≥ 1 test case.

## 1. What to read (in this order)

| # | Document | Lines | Read for |
|---|---|---|---|
| 1 | `00_System/specs/DECISIONS.md` | 40 | **All 34 decisions in one table** — yours (D-03…D-28, D-32) and mine (D-29…D-34, marked "Orchestrator") — confirm or change |
| 2 | `00_System/specs/SRS.md` v0.4.1 | 671 | Your request as 172 numbered, testable requirements. Start with **§3.2 (stop & enable policy table)** and **§7 (your request → requirement IDs)** |
| 3 | `01_HW/wiring.md` v0.3 | 250 | **What has to be built/changed on the bench** (E-stop contactor circuit, buffer board, RATE wire, driver DIP sheet, check list C-01…C-23) |
| 4 | `01_HW/pinout.md` v0.3 | 176 | Pin allocation (deviations from Stefan listed) |
| 5 | `03_SW/docs/SW_design_GUI.md` v0.3 | 1511 | Wireframes of every tab, wizard and dialog (§4–§7) |
| 6 | `00_System/specs/ICD_protocol.md` v0.4.1 | 1490 | Binary protocol; **§7.1 = the data frame you specified** |
| 7 | `03_SW/docs/SW_design.md` v0.3.1 / `02_FW/docs/FW_design.md` v0.3 | 2292 / 1346 | Architecture, budgets, M1 work breakdowns (SW §22, FW §9) |
| — | Test plans, research R1…R5, `params.yaml`, `protocol.yaml`, `TRACEABILITY.md` | — | Reference; no decision needed |

## 2. Where the specification deviates from, or adds to, your request

| Your item | What the spec does | Why |
|---|---|---|
| Stefan pinout | Kept STEP PA0 (TIM2), DIR PA1, ENA PA4, USART2 VCP. **Changed/added:** HX711 DOUT PB4 / SCK PB10 / RATE PB5, END PC1, E-stop sense PA10, STOP PC7, PAUSE PB6, ALM PA8, PEND PA9, driver-power sense PA7 | 5 V-tolerant inputs, free EXTI lines, RATE under FW control (R5 §6, checked against the F446 datasheet) |
| Stefan FW | Used for knowledge only, not code | It never drove ENA, limits floated, telemetry dead, timer clock 16 % off (R1) |
| "Start/stop switches + zeroing" | Two NC end-of-travel switches; **homing only at START**; soft limits after homing; absolute moves refused until homed | D-08, D-18, D-29 b |
| "NC button immediately stops" | **E-stop = power removal** (contactor in the 48 V line, RESET button) + MCU sense; plus physical **STOP** (holding stop, FW-only, works without PC) and **PAUSE** buttons | IEC 60204-1 (your Q1, D-11, D-26, D-28) |
| UART CRC16 | `A5 5A` + type + seq + len + payload + CRC-16/CCITT-FALSE in **both** directions | Resync, lost-frame detection, command/response matching |
| Data chunk | Your fields in your order (u32 µs time, u8 payload version, u8 flags with bit 0 = validity, i32 raw AFE, i32 setpoint µm) **+ u16 frame counter + u16 status** = 26 B frame, one per HX711 sample (80 Hz ≈ 2.3 % of the link) | Lost-frame detection; MOVING/limits/stops/faults visible per sample (your Q17) |
| "Setpoint distance" | Commanded position at the instant of the HX711 sample (µm) | Exact travel/load pairs for the X-Y chart |
| Validity flag | Set/reset by command (reply carries the device time it applies from); FW clears it automatically on every stop/fault | No stale "valid" data after a stop |
| AFE config | Gain/channel and rate (10/80 SPS via the RATE wire) settable; FW measures the real rate and flags a mismatch; stale timeout 250 ms | Rate is a hardware pin on the HX711 (R2) |
| Pause/Break key | Latched HALT (holding) + sequence terminated, explicit "Clear stop"; physical/GUI **Pause** is resumable via a dedicated RESUME command | Your Q4/D-26; two race conditions closed (D-30, D-31, D-34) |
| Manual ±0.1/1/10 mm | Sent as absolute targets (latest click wins); slider sends one move on release | A retried relative move would move twice (R3) |
| Travel calibration | Pre-move 2 mm for backlash; step 2 recomputes over the **total 60 mm** | 6× more accurate than using 50 mm only (R4) |
| Load calibration | Linearity judged by worst residual in % of span (PASS ≤ 0.1 %, WARN ≤ 0.5 %); outlier/drift checks per 10 s point; **LOW_SPAN warning with 1 kg + 10 kg** | R² is too lenient (R4); your weights reach 5 % FS only |
| Tare | 10 s robust average, **session-only**, refused while moving/noisy | You tare before each measurement |
| Load-target steps | Approach until load, then small trim moves, position frozen during capture; **not reached → travel to the soft limit, stop, NOT_REACHED, sequence stops** | Your Q26 (D-32) |
| Safety layers | FW: load limit on **every** sample (≤ 110 % FS, PC may lower), limits, STOP, E-stop sense, link watchdog 1 s, jog dead-man, IWDG; PC: travel/load limits on every frame; "no-specimen mode" for first use | R2: the motor can push ≈ 9 kN vs. 2.35 kN cell overload |
| Driver | **PFDE HBS86H**, 48 V, **DIP change: 4000 p/rev, closed loop (SW7 off / SW8 on)** → 800 steps/mm | Your D-27 approval; open loop hid lost steps |

## 3. Decisions you need to take

| # | Question | Default if you say nothing |
|---|---|---|
| G1 | **Confirm DECISIONS.md**, in particular my D-29…D-34 (homing at START only; welded-contactor fault; speed limits 30 mm/s travel / 20 mm/s under load; no-specimen mode; latest-wins jog buttons; PAUSED latch + RESUME command; clears never auto-retried; sequence-start refusals; ALM during a sequence stops it) | as written |
| G2 | **Calibration span (SRS OI-01):** 1 kg + 10 kg reach 5 % FS, so forces up to 200 kg are extrapolated 20×. Can you get a heavier reference (≥ 50 kg, or a reference force gauge)? | accept with LOW_SPAN warning |
| G3 | **SW items 9–11 (OI-08):** your SW list jumps from 8 to 12 — anything missing? | nothing missing |
| G4 | **Hardware build for the HW gate** (`wiring.md`): E-stop Option A (2-NC mushroom, DILM7-class contactor with 48 V coil, RESET button), physical STOP (NC) and PAUSE (NO) buttons, SN74ACT244 buffer board (perfboard first), HX711 RATE wire to PB5, DRV_PWR sense from the contactor aux contact, Mean Well SDR-480-48. Who builds it, and by when? | you build it before the HW gate; agents provide schematics |
| G5 | **Test equipment at the HW gate:** oscilloscope or logic analyzer (pulse timing, E-stop reaction), caliper, 1 kg + 10 kg weights | scope or logic analyzer available |
| G6 | **Lab PC spec (OI-17)** for the performance tests (CPU, RAM, screen, Windows version) | needed before M3 |
| G7 | **Approve the milestone plan** (§4) and the start of P2 | approve |

## 4. Validator conditions carried into P2

| Condition | Owner | Due |
|---|---|---|
| Twin seam contract single-sourced (done in tools/README) and twin control vocabulary implemented — M1 subset | Integrator | M1 exit (rest before M2) |
| Test hooks in the backend (lockstep clock, wire log, fault injection, ENOSPC) | B | M1 entry |
| GUI alignment to SW_design v0.3.1 delta B31-01…04 (clear results NOT_CONFIRMED, RESUME not confirmed text) | D | M1 (WP-D) |
| FW twin + SIM-vs-twin differential subset | Integrator + A | M1 exit |
| Motion vectors (`motion_vectors.json`) | Integrator | M2 entry |
| Reference PC named | PO / Orchestrator | M3 entry |
| **D-06 stays in force:** no COM port / flashing until you approve the HW gate; no motion under load until HG-01…24 + HG-28 pass | all | HW gate |

## 5. What happens after approval (P2)

1. **M1 — Link & skeleton** (no hardware needed, D-07): FW board init, clock, USART2 DMA link, framing + CRC, parameters + NVM, streaming with a synthetic 80 Hz source, stop sniffer; FW host twin; PC backend (transport, codec, device API, simulator, recorder skeleton); GUI connect / config tab (read, write, verify, save/load) / minimal plot / toolbar with STOP. Validators: host unit tests, shared-vector conformance, twin integration. Work breakdowns: FW_design §9 (WP0–WP8), SW_design §22 (WP-B0…B12), SW_design_GUI §15 (WP-D0…D8).
2. **M2 — Sensor & motion:** HX711 driver, step generation with ramps, limits, homing, E-stop/STOP/PAUSE, load limit, DRV_PWR/K1, validity flag — on the twin.
3. **HW gate (your approval):** bench check list HG-01…28 / C-01…C-23, first motion = short slow jog checking direction and scale.
4. **M3 — SW application:** manual control, detachable plots, recording, limits, tare, calibration wizards, report marks.
5. **M4 — Sequencer:** step/load editor, generators, looping, save/recall, travel-load chart with live marker, steady-state report.

Each milestone ends with validator verdicts and a short report to you; commits at each gate (D-25).
