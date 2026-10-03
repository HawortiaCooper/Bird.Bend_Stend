# Bird Bend Stand — System Requirements Specification (SRS)

| Doc | SRS |
|---|---|
| Version | **0.2 — DRAFT for product-owner review** (P1 gate; Orchestrator-reviewed) |
| Date | 2026-10-03 |
| Owner | Orchestrator (`00_System/specs/SRS.md`); draft prepared on the Orchestrator's behalf |
| Binding inputs | `Initial_specs.txt` (PO request), `DECISIONS.md` D-01…D-27 (D-10 amended by D-11; D-16 amended: driver at 48 V DC; D-26 operator controls; D-27 DIP setting still open), `P1_QUESTIONS.md`, research R1–R4 |
| Downstream | `ICD_protocol.md` and `params.yaml` (Integrator), `01_HW/` pinout & wiring, FW/SW design, test plans, `TRACEABILITY.md` |

**Notation**
- "shall" is normative. Each requirement row: **ID · Requirement · Source · Acceptance criterion · V · MS · Pri**.
- **Source**: `PO-FW-n` / `PO-SW-n` = numbered items of `Initial_specs.txt` (sub-items of SW item 8 are `PO-SW-8.x`, see §7); `D-xx` = decision; `Rn §x` = research section; `CONV` = conventions in `CLAUDE.md`/`PROCESS.md`.
- **V** (verification): **T** test (host unit test, FW host twin or PC simulator), **A** analysis, **I** inspection, **D** demonstration. Suffix **+H** = needs target hardware, executed only at a PO-approved hardware gate (D-06, D-07).
- **MS** = milestone (PROCESS.md §2): M1 link & skeleton · M2 sensor & motion · M3 SW application · M4 sequencer.
- **Pri**: Must / Should / Could.
- Command names (STOP, HALT, MOVE_ABS, …) and parameter names (`motion.*`, `safety.*`, …) are **provisional**: the Integrator fixes names, codes and byte layouts in `ICD_protocol.md` / `params.yaml` without changing the semantics stated here.
- Numbers marked ASSUMED are collected in §10.

---

## 1. Scope

This SRS specifies the Bird Bend Stand: a pull/push (tension/compression) bend test stand made of a
NUCLEO-F446RE firmware (FW) that drives a closed-loop stepper axis and reads a load cell, and a Windows
PC application (SW, Python/PySide6) for configuration, manual control, calibration, tare, realtime display,
test sequences and reports. It covers system, safety, FW, interface, SW and non-functional requirements.
The byte-level protocol (`ICD_protocol.md`) and the parameter dictionary (`params.yaml`) are owned by the
Integrator and must implement the requirements here. The hardware power-removal circuit of the E-stop and
the optional buffer board are proposed by R5 (pending) and only constrained here.

## 2. Definitions

| Term | Definition |
|---|---|
| FS | Load-cell full scale: 200 kg = **1961.33 N** (G0 = 9.80665 m/s²). Nominal HX711 counts (3.0 mV/V, gain 128, ratiometric): **32 212 counts/kg**, 6 442 451 counts at FS; 1 % FS = 64 425 counts (R2 §4.4, ASSUMED nominal). |
| Raw counts | HX711 24-bit two's-complement result, sign-extended to int32, unscaled. Rail values 0x7FFFFF / 0x800000 (−8 388 608) = **saturated** (≈ 130 % FS). |
| Operational stop | IEC 60204-1 **stop category 2**: pulse generation ends, the driver stays **enabled and holding** (D-10 as amended by D-11). *Immediate* = no further step pulse after the stated reaction time; *controlled* = deceleration with `motion.a_stop_um_s2`, then stop. An operational stop never resumes the stopped move automatically. |
| E-stop | Emergency stop, **stop category 0**: NC mushroom button whose hardwired circuit removes the driver's 48 V supply independently of MCU/FW/SW (D-11); a separate NC contact informs the MCU ("E-stop sense"). Driver power loss ⇒ position lost ⇒ not homed. |
| STOP (GUI) | GUI STOP button: immediate operational stop, sequence terminated, not latched in FW (D-26 (2)). |
| HALT | Latched immediate operational stop raised by the **Pause/Break key** (PC command) or the **physical STOP/BREAK button** (FW input, acts without the PC); sequence terminated; cleared only by an explicit PC command (D-14, D-26 (2)). Not an E-stop. |
| PAUSE | Physical PAUSE button (MCU input) or GUI Pause: controlled operational stop, sequence **paused** and resumable by GUI Resume or the button (D-14, D-26 (1)). |
| Homed | FW state: machine coordinate referenced by a completed HOME sequence and not invalidated since (E-stop, disable, idle disable, step fault, failed/aborted homing, reset). |
| Machine coordinate | Travel x [µm on the wire, mm in SW], 0 = home reference (`home.offset_um` from the home switch), increasing away from the home (START) switch. |
| Test travel zero | PC-side offset `x_zero` (e.g. at specimen contact); test travel = x − x_zero. Never sent to the FW. |
| Setpoint distance | D-05 field: commanded profile position = steps issued so far × 1000 / `steps_per_mm` [µm], latched at HX711 data-ready (R4 §2.3; interpretation ASSUMED, §10). |
| Validity flag (VALID) | DATA flag bit 0, set/reset by PC command (PO-FW-7); marks frames usable for analysis; FW clears it automatically on every stop/fault. |
| Moving / idle | Moving = FW is generating pulses or executing a ramp (MOVING flag). Idle = not moving. |
| Steady-state window | Report samples of a step: VALID = 1, MOVING = 0, setpoint distance constant, no stop/fault flag, t ∈ [t_reached + settle, t_reached + settle + capture] (R4 §8.3). |
| Tare | `tare_raw` = robust mean raw over the tare window; force F = K·(raw − tare_raw) (R4 §7). |
| Load calibration | K [N/count], B [N] from an OLS fit over zero + known weights (R4 §6). |
| Soft limits | FW travel limits [`limits.soft_min_um`, `limits.soft_max_um`] (machine coordinate), active when homed. **SW travel limits**: per-test limits inside them. |
| FW load limit | Raw thresholds `safety.load_raw_min/max` compared by the FW on every sample (D-12). **SW load limits**: force limits in N evaluated on the PC. |
| Latched | The condition persists until its stated clear condition is met. |
| Link capacity | 92 160 B/s (921 600 Bd, 8N1). |
| p95 | 95th percentile over ≥ 100 trials. |
| Reference PC | The PO's Windows 10 lab PC used with the stand (ASSUMED, §10); its spec is recorded in each performance test report. |
| Dead-man | Jog motion continues only while refreshed by the PC. |

## 3. System context

### 3.1 Context

```
 PC — Windows 10, Python >= 3.11, PySide6 GUI + backend (core/io/calc), simulator
   │  USB ── ST-LINK/V2-1 VCP ── USART2: 921600 Bd 8N1, no flow control, binary frames, separator, CRC-16 both directions
 NUCLEO-F446RE — FW (PlatformIO + stm32duino, LL timers/EXTI/DMA, 180 MHz from HSE bypass)
   ├─ PUL / DIR / ENA (3.3 V push-pull, common cathode, D-17) ──► HBS86H closed-loop driver (48 V DC, DIP 800 pulses/rev per D-27, unconfirmed)
   │       └─► 86HS2140 motor (1000-line encoder → driver) ──(direct coupling, ASSUMED)──► SFU1605 ball screw, 5 mm lead, 300 mm stroke
   ├─ ALM / PEND ◄── HBS86H opto outputs (read & report only, D-16)
   ├─ HX711 (3.3 V, DOUT / PD_SCK, RATE on a GPIO; 80 SPS, A/128) ◄── Keli DEF 200 kg S-cell (3.0 mV/V, tension + compression)
   ├─ START limit switch (NC, home end) · END limit switch (NC)                      (D-08, D-18)
   ├─ E-stop sense contact (NC) ◄── E-stop button ──► hardwired removal of the driver's 48 V supply (cat. 0, D-11, R5)
   ├─ physical STOP/BREAK button (D-26 (2))  · physical PAUSE button (D-14, D-26 (1))
   └─ (status LED)
```

### 3.2 Stop and enable policy (normative summary; details in §4.2)

| Trigger | Category | Pulses | ENA | Latch / clear | HOMED | VALID | Source |
|---|---|---|---|---|---|---|---|
| E-stop button (HW power removal + MCU sense) | 0 | stop ≤ 100 µs | driven to *disabled* | ESTOP; clear = input closed ≥ 100 ms **and** PC ESTOP_CLEAR, then ENABLE + HOME | cleared | cleared | D-11 |
| GUI STOP, SW limit trip, wizard/sequence abort (PC STOP) | 2 immediate | stop | kept | none (sequence terminated on the PC) | kept | cleared | D-10, D-26 |
| Pause/Break key (PC HALT) | 2 immediate | stop | kept | HALT; clear = PC HALT_CLEAR | kept | cleared | D-14, D-26 |
| Physical STOP/BREAK button | 2 immediate | stop | kept | HALT (src = button); clear = button released **and** PC HALT_CLEAR | kept | cleared | D-26 |
| Physical PAUSE button / GUI Pause | 2 controlled | decelerate | kept | PAUSED until the next accepted motion command or PC clear | kept | cleared | D-14, D-26 |
| Limit switch while moving | 2 immediate | stop | kept | LIMIT_x; auto-clear after release ≥ 20 ms and moved away | kept | cleared | D-08, D-10 |
| Both limit switches active | 2 immediate | stop | kept | LIMIT_WIRING; FAULT_CLEAR when both released | kept | cleared | R4 §3.3 |
| FW load limit / ADC saturation | 2 immediate | stop | kept | LOAD_LIMIT; FAULT_CLEAR (unload allowed, re-trip on growth) | kept | cleared | D-12 |
| AFE stale while moving | 2 immediate | stop | kept | AFE_FAULT; FAULT_CLEAR after fresh samples | kept | cleared | D-10 |
| Link watchdog (1 s, while moving) | 2 controlled | decelerate | kept | LINK_WDG; clears at next valid frame | kept | cleared | D-15 |
| Jog dead-man expired | 2 controlled | decelerate | kept | none | kept | unchanged | R1 §10.3 |
| Step overrun / count fault | 2 immediate | stop | kept | STEP_FAULT; FAULT_CLEAR | cleared | cleared | R4 §1.6 |
| Homing failure | 2 immediate | stop | kept | HOME_NOT_FOUND / HOME_WIRING; FAULT_CLEAR | cleared | cleared | R4 §3.1 |
| Idle ≥ 600 s **and** load < 2 % FS | — | — | *disabled* | event; re-ENABLE by PC | cleared | — | D-15 |
| PC DISABLE (operator confirmed) | — | refused while moving | *disabled* | — | cleared | — | R4 §4.2 |
| MCU reset / IWDG / power-up | — | none | left at "no current" = driver enabled, holding (disabled if E-stop active) | boot: not enabled, not homed | cleared | stream off | D-13 |
| Driver ALM | — | **no reaction in this release** (report only) | — | — | — | — | D-16 (§8) |

---

## 4. Requirements

### 4.1 System (SYS-)

| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SYS-001 | The system shall consist of the PC application, the NUCLEO-F446RE FW, the HBS86H driver with 86HS2140 closed-loop motor and SFU1605 ball-screw table, the HX711 AFE with Keli DEF 200 kg cell, two NC limit switches, the NC E-stop with hardwired power removal and MCU sense, and the physical STOP/BREAK and PAUSE buttons, connected as in §3.1. | PO intro, D-08, D-11, D-16, D-19, D-20, D-26 | `01_HW` wiring documentation shows every connection of §3.1; inspection checklist complete. | I | M2 | Must |
| SYS-002 | The FW shall stream raw counts and the commanded position and execute single motion commands; the FW alone shall implement the E-stop sense reaction, physical STOP/PAUSE, limit switches, FW load limit, link watchdog, jog dead-man and idle disable, independently of PC presence, stream state and SW. Scaling, tare, derived quantities, SW limits, calibration, sequencing and reports shall run on the PC. | CONV, D-12, D-26, R4 §4 | Every SAF-FW test also passes with the PC disconnected and the stream off; code inspection finds no force scaling in FW. | T, I | M2 | Must |
| SYS-003 | Conventions: force > 0 = tension (pull), < 0 = compression (push), sign carried by K; travel in the machine coordinate (§2); SW units mm, N (display N or kgf selectable), s; wire units µm, µm/s, µm/s², µs, raw counts; all float→int conversions round half away from zero. | D-20, R4 §0 | R4 §12 vectors TV-U and `um_to_steps`/`steps_to_um` pass in FW host tests and pytest; GUI offers the N/kgf selector. | T | M1 | Must |
| SYS-004 | Mechanical envelope: stroke 300 mm; commanded speed 0.001…10 mm/s required (higher speeds up to the pulse-rate cap allowed); force range ±FS. | D-19 | On the stand: moves at 0.01 mm/s and 10 mm/s (after travel calibration) with measured mean speed error ≤ 1 %; usable travel between soft limits ≥ 280 mm measured. | D+H | M2 | Must |
| SYS-005 | Drive train configuration: HBS86H supplied from **48 V DC**; DIP pulses/rev as set and documented (currently 800 pulses/rev per the Leadshine table → nominal **160 steps/mm**, ASSUMED, D-27), SW6 motor-type switch matching the 8.2 N·m 86HS2140-class motor with 1000-line encoder closed inside the driver; SFU1605 lead 5 mm, direct coupling ASSUMED; driver command-smoothing filter off. FW and SW shall not depend on a particular pulses/rev value: steps/mm is a calibrated parameter, and speed/acceleration limits are expressed in length units (mm/s, mm/s² in SW; µm/s, µm/s² on the wire) so they remain valid after recalibration. | D-16 (amended), D-19, D-23, D-27, R2 §1.6, §1.9 | `01_HW` setup sheet lists supply, DIP positions (incl. SW5/SW6) and tuning settings; first travel calibration result recorded and compared with the candidate readings 160 / 1280 steps/mm; inspection: no speed/accel parameter in step units. | I, I+H | M2 | Must |
| SYS-006 | The E-stop shall remove the driver's power through a hardwired circuit that does not depend on MCU, FW or SW (stop category 0), and shall provide a separate NC contact to the MCU E-stop sense input. Releasing the button shall not cause motion (driver re-enable only by PC command, SAF-FW-006). Circuit proposed by R5, documented in `01_HW`. | D-11, PO-FW-3, PO-SW-6 | Circuit review; with the MCU held in reset, pressing the E-stop de-energises the driver (supply measured < 5 V within 100 ms); after release no axis motion until ENABLE + HOME. | I, T+H | M2 | Must |
| SYS-007 | `01_HW` shall document the pinout (origin Stefan, deviations with rationale), PUL/DIR/ENA common-cathode wiring with MCU GND only on the driver's opto side, HX711 at 3.3 V with RATE on a GPIO, NC limit switches to GND with RC filters, E-stop circuit and sense contact, STOP/BREAK and PAUSE buttons, ALM/PEND inputs, supplies and grounding. | PO intro, D-17, D-21, D-22, D-26, R1 §9, R2 §6 | Every FW pin appears in `pinout.md` with direction, level and origin; no 5 V signal on a TTa pin (PA4/PA5). | I | M1 | Must |
| SYS-008 | All FW logic (motion, homing, safety, AFE, protocol) shall be executable in a FW host twin, and the SW shall run against a PC simulator with the models of R4 §11 (driver, switches, buttons, HX711, specimen, link); target tests run only at a PO-approved hardware gate. | D-06, D-07, R4 §11 | Full GUI workflow (connect, home, calibrate, tare, sequence, report) demonstrated against the simulator; SW⇄twin integration tests pass. | T, D | M1 | Must |
| SYS-009 | Before any motion under load on target, a hardware check list shall be passed: 921600 Bd VCP streaming soak, flash erase vs IWDG, HX711 80 SPS on silicon, E-stop/STOP-button/limit/load-limit reaction times, ENA enable/disable with 3.3 V common-cathode drive, PUL/DIR timing at the driver terminals, ALM/PEND levels. | R3 §7.2, D-17, D-24 | Check-list report by Validator E with measured values; every budget of §6 met. | T+H | M2 | Must |
| SYS-010 | Code reused from Thrust_Stand_HAW or Stefan shall be copied into this repository with an origin note (path @ commit); every requirement shall be traced to design, code and tests through ID tags (`Implements:` / `Verifies:`) and `TRACEABILITY.md`. | D-02, PROCESS §3, PO-FW-1 | Traceability report: 100 % of Must requirements have ≥ 1 verifying test; every reused file carries an origin note. | I | M1 | Must |

### 4.2 Safety — firmware (SAF-FW-)

| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SAF-FW-001 | Every operational stop shall end pulse generation, keep the ENA level unchanged (driver holding), discard the active move target and ramp state, clear VALID and report its cause (status flag + EVENT); a stopped move is never resumed by the FW. | D-10, D-11, R3 §1.7 P11, R4 §8.5 | Twin test per stop source of Table 3.2: ENA unchanged, MOVING = 0 and VALID = 0 in the next DATA frame, EVENT cause correct; a later clear/ENABLE does not restart the old move. | T | M2 | Must |
| SAF-FW-002 | Immediate stops (PC STOP, PC HALT, physical STOP/BREAK button, limit switch, FW load limit, AFE fault, step fault, homing/wiring fault) shall emit no further PUL edge later than: 200 µs after a limit-switch or STOP-button input edge at the MCU pin; 200 µs after the HX711 data-ready of the deciding sample; 2 ms after the last byte of a STOP/HALT command frame. | D-10, D-12, D-26, R4 §4.6 | Twin timing test (virtual time); on target scope/timer-capture, 100 trials per path, max ≤ budget. | T, T+H | M2 | Must |
| SAF-FW-003 | Controlled stops (link watchdog, PAUSE button, jog dead-man) shall decelerate with `motion.a_stop_um_s2` to standstill, then behave as SAF-FW-001. | D-14, D-15, D-26, R4 §4.5 | Twin: deceleration starts ≤ 2 ms after the trigger; stop distance = v²/(2·a_stop) ± 1 step. | T | M2 | Must |
| SAF-FW-004 | No partial (runt) or uncounted step pulse shall occur at any stop; the step counter shall equal the number of completed pulses; an immediate stop that may truncate a pulse in flight sets `pos_uncertain` (±1 step) and keeps HOMED; a missed timer update raises STEP_FAULT (immediate stop, HOMED cleared). | R1 B-07, R4 §1.6 | Twin: 10 000 random stops, simulated PUL edges = step counter (or ±1 with `pos_uncertain`); target: 100 moves cross-checked by an external counter, difference 0. | T, T+H | M2 | Must |
| SAF-FW-005 | When the E-stop sense input opens, the FW shall (a) emit no further PUL edge later than **100 µs** after the input edge at the MCU pin, (b) drive ENA to the disabled level within 1 ms, (c) latch ESTOP, clear VALID and HOMED and discard the active move — in every link, stream and motion state. | D-11, R4 §4.6, PO-FW-3 | Twin and target (scope), 100 trials: max (a) ≤ 100 µs, (b) ≤ 1 ms; flags set in the next DATA frame. | T, T+H | M2 | Must |
| SAF-FW-006 | ESTOP_CLEAR shall be accepted only after the sense input has been closed continuously for ≥ `input.estop_release_ms` (default 100 ms), otherwise NACK with the input state; after clear the driver stays disabled and the axis not homed: motion requires ENABLE, MOVE_ABS additionally HOME. | D-11, R4 §4.2 | Twin: clear refused at 50 ms, accepted at 100 ms; afterwards JOG → NACK (not enabled), after ENABLE MOVE_ABS → NACK (not homed). | T | M2 | Must |
| SAF-FW-007 | E-stop sense and limit inputs shall be NC contacts to GND with pull-up, "open/high" = active, with the polarity fixed in FW (no parameter), so a broken wire or unplugged connector reads as active; an input active at boot is latched/reported from the first status and motion is refused accordingly. | D-18, R2 §6.3–6.4, R4 §3.5 | Inspection: no polarity parameter for these inputs; twin: wire break on each input → stop + flag; boot with an input open → reported in the first GET_STATUS. | I, T | M2 | Must |
| SAF-FW-008 | The FW shall compare **every** HX711 sample with `safety.load_raw_min/max`; `safety.load_trip_samples` (default 1) consecutive violations shall cause an immediate stop, latch LOAD_LIMIT and clear VALID — in every state (incl. homing, jog, un-homed, stream off). The check cannot be disabled. | D-12, R4 §4.4 | Twin: injected raw_max + 1 → stop on that sample, same with the stream off; inspection: no parameter disables the check. | T, I | M2 | Must |
| SAF-FW-009 | A sample at either rail (0x7FFFFF or −8 388 608) shall count as a load-limit violation regardless of the thresholds. | D-12, R2 §4.1 | Twin: rail sample with thresholds at the range cap → trip. | T | M2 | Must |
| SAF-FW-010 | Load thresholds shall default to ±7 022 271 counts (110 % FS minus the 1 % FS zero-balance allowance) and be settable only within ±7 151 121 counts (110 % FS plus 1 % FS); outside → E_RANGE (never clamped); `raw_min < raw_max` hard rule; settable while moving, effective from the next sample. | D-12, R2 §3, §4.4 | Parameter vectors: SET 7 151 122 → E_RANGE; SET 7 151 121 accepted and read back; defaults after DEFAULTS equal the listed values. | T | M2 | Must |
| SAF-FW-011 | FAULT_CLEAR shall clear LOAD_LIMIT even if the load is still beyond the threshold, so the operator can unload; the FW shall re-trip immediately if the violation grows by more than `safety.load_regrow_raw` beyond the value at clear. | R4 §4.4 | Twin: after clear, a move reducing the load runs; a move increasing it beyond regrow trips. | T | M2 | Must |
| SAF-FW-012 | No HX711 sample for `afe.timeout_ms` (default 100 ms) shall set AFE_STALE; while moving this causes an immediate stop and latches AFE_FAULT. Motion commands shall be refused while the AFE is stale or the last sample was saturated. | D-10, R4 §4.2 | Twin: DOUT stops during a jog → stop ≤ 100 ms + 1 ms; motion NACK while stale; FAULT_CLEAR succeeds only after fresh samples. | T | M2 | Must |
| SAF-FW-013 | A limit input becoming active while moving shall cause an immediate stop, latch LIMIT_START/LIMIT_END, clear VALID and send an EVENT. While a limit is active or latched, motion toward that switch is refused and motion away from it is allowed; the latch clears automatically when the input has been released ≥ `input.release_ms` (20 ms) and the axis has moved away. (Expected switch edges during HOME are handled by FW-HOM.) | PO-FW-2, D-08, D-18, R4 §3.3 | Twin: hit during MOVE_ABS → stop; JOG toward → NACK; JOG away → executes; latch cleared 20 ms after release. | T | M2 | Must |
| SAF-FW-014 | Both limit inputs active simultaneously shall raise LIMIT_WIRING: immediate stop, all motion refused until both inputs are released and FAULT_CLEAR is received. | R4 §3.3, §3.5 | Twin: both open → fault + motion NACK; FAULT_CLEAR refused while active. | T | M2 | Must |
| SAF-FW-015 | Link watchdog: while moving (any mode), no CRC-valid frame from the PC for `safety.link_timeout_ms` (default 1000 ms) shall cause a controlled stop with the driver kept enabled, set LINK_WDG, clear VALID and send an EVENT; LINK_WDG clears at the next valid frame; no motion restarts. | D-15, R4 §4.5 | Twin: silence 999 ms → no stop; 1000 ms + 2 ms → deceleration started. | T | M2 | Must |
| SAF-FW-016 | Jog dead-man: JOG motion shall continue only while refreshed by a JOG command at least every `motion.jog_timeout_ms` (default 250 ms); otherwise controlled stop. | R1 §10.3, R4 §2.1 | Twin: refresh every 200 ms keeps moving; deceleration starts ≤ timeout + 2 ms after the last refresh. | T | M2 | Must |
| SAF-FW-017 | Idle disable: after `safety.idle_disable_s` (default 600 s, 0 = never) without motion **and** with abs(raw − `safety.zero_raw`) < `safety.release_band_raw` (default 2 % FS), the FW shall set ENA to disabled, clear HOMED and send an EVENT; with the load at or above the band the driver stays enabled (holding) indefinitely. | D-15 | Twin (virtual time): unloaded → disabled at 600 s ± 1 s; loaded → still enabled after 1 h. | T | M2 | Must |
| SAF-FW-018 | After reset, power-up or IWDG reset the FW shall keep PUL idle from reset release, leave ENA at the "no current" level (driver enabled, holding, D-13) unless the E-stop input is active (then disabled level), report HOMED = 0, motion state "not enabled" (no pulse until ENABLE), stream off and the reset cause. | D-13, R2 §1.5, CONV | Twin: after boot STATUS shows not homed / not enabled / reset cause; JOG before ENABLE → NACK. Target: reset with a 10 kg hanging load → axis motion ≤ 0.01 mm. | T, T+H | M2 | Must |
| SAF-FW-019 | An independent watchdog (IWDG) shall be active from boot; a FW hang while moving shall stop the PUL output ≤ 100 ms after the hang (reset → SAF-FW-018). The timeout may be extended only while idle for flash operations. | R2 §5.5, R3 §3 | Injected infinite loop while moving: PUL stops ≤ 100 ms (twin; scope on target); reset cause = IWDG. | T, T+H | M2 | Must |
| SAF-FW-020 | Motion gating: the command set shall contain **absolute moves only** (no relative move on the wire); targets outside the soft limits → E_RANGE; speed/accel above `motion.v_max_um_s` / `motion.a_max_um_s2` → E_RANGE; MOVE_ABS and MOVE_UNTIL_LOAD refused when not homed; un-homed only HOME and JOG at ≤ `motion.v_unhomed_um_s`; every motion command refused while ESTOP/HALT/fault is latched, the driver is not enabled or the AFE is stale. | D-23, R3 §1.6 (b), R4 §2.1, §3.2 | Command-check vectors, one per refusal reason, give the expected NACK code; inspection: the ICD defines no relative-move command. | T, I | M2 | Must |
| SAF-FW-021 | HOME shall be refused when abs(raw − `safety.zero_raw`) > `home.max_load_raw` (default 5 % FS) unless the command carries the operator-confirmed flag; the FW load limit stays active during homing. | D-15 | Twin: 6 % FS load → NACK; same with flag → homing runs; trip during homing → stop. | T | M2 | Must |
| SAF-FW-022 | Physical STOP/BREAK button: an active edge (first edge, no software delay) shall cause an immediate stop and latch HALT (source = button) without any PC involvement; HALT_CLEAR is accepted only when the button input has been inactive for ≥ `input.release_ms`; status flag and EVENT are reported. | D-26 (2) | Twin: press during move → stop ≤ 200 µs, HALT latched, EVENT; clear refused while pressed, accepted after release; works with PC disconnected. | T, T+H | M2 | Must |
| SAF-FW-023 | Physical PAUSE button: an active edge while moving shall cause a controlled stop, set PAUSED and send EVENT PAUSE; a press while PAUSED shall only send EVENT RESUME_REQUEST; the FW never resumes motion by itself; PAUSED clears on the next accepted motion command or a PC clear. | D-14, D-26 (1) | Twin: press during a move → deceleration + EVENT; second press → EVENT only, no pulses. | T | M2 | Must |

### 4.3 Safety — PC software (SAF-SW-)

| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SAF-SW-001 | The backend shall evaluate the enabled SW limits (load in N with active calibration + tare; travel in mm) on every received DATA frame; a violation shall send STOP, terminate any sequence/wizard motion and log an event with the value; STOP frame on the wire ≤ 50 ms after reception of the violating frame. While a load limit is enabled but has no valid input (no calibration/tare, AFE fault), motion commands are disabled. | PO-SW-2, D-12, R4 §4.6 | Simulator: 100 injected violations, STOP on the wire p95 ≤ 50 ms; motion controls disabled with load limit enabled and no calibration. | T | M3 | Must |
| SAF-SW-002 | After every change of calibration, tare or FW load-limit level, and at connect, the SW shall compute `safety.load_raw_min/max` per R4 §4.4 (rounding toward the tare), refuse values corresponding to > 110 % FS or outside the FW range, write them together with `safety.zero_raw`, and verify by read-back; SW motion commands stay disabled until the verified thresholds match the active calibration + tare. | D-12, R4 §4.4, §7 | pytest R4 TV-T `fw_raw_limits` vectors; simulator: tare → SET_PARAM + read-back observed; injected mismatch → motion disabled. | T | M3 | Must |
| SAF-SW-003 | While connected, the SW shall send a heartbeat (PING) whenever nothing else was sent for 200 ms (gap ≤ 250 ms), only while its receive/processing pipeline is alive; during motion, DATA loss > 500 ms shall send STOP, terminate the sequence and show LINK LOST. | D-15, R3 §1.5 | Simulator: 600 ms stream gap during a sequence → STOP sent, sequence terminated; 10 min run: max heartbeat gap ≤ 250 ms. | T | M3 | Must |
| SAF-SW-004 | Operator confirmations shall be required for: HOME with abs(F) ≥ 5 % FS (sends the confirmed flag), DISABLE ("specimen unloaded?"), E-stop clear (button released, re-home notice); Enter/Space never confirm, and STOP is reachable inside every such dialog. | D-11, D-15, R4 §2.1 | GUI tests: each dialog appears under its condition; keyboard default does not confirm; STOP button present. | T | M3 | Must |
| SAF-SW-005 | The GUI shall show persistent indicators for ESTOP, HALT (with source), PAUSED, LIMIT_START/END, LOAD_LIMIT, AFE stale/saturated/rate mismatch, LINK_WDG and link state, HOMED, driver enabled, ALM, PEND, and for each latched condition its clear procedure. | D-11, D-16, D-26 | Simulator fault injection: each indicator updates ≤ 200 ms after the frame that carries it. | T | M3 | Must |
| SAF-SW-006 | Before a move or sequence with enabled load limits, the SW shall warn when k_est·v·0.065 s > F_fw − F_pc (speed too high for the configured limit margin). | R4 §4.6 | pytest of the rule; GUI warning shown in the simulator. | T | M4 | Should |

### 4.4 Firmware (FW-)

#### FW-PLT — platform
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| FW-PLT-001 | The FW shall run on NUCLEO-F446RE, built with PlatformIO + stm32duino, with LL/register access and own IRQ handlers for step timer, EXTI and UART DMA (build flags per R1 §6.2); pinout and basic board init taken from Stefan where no conflict exists. | PO intro, D-09, R1 §6, §9 | Clean-checkout build succeeds; inspection of `platformio.ini` flags and pin init vs `01_HW/pinout.md`. | I | M1 | Must |
| FW-PLT-002 | SYSCLK shall be 180 MHz from HSE bypass (8 MHz ST-LINK MCO) with bounded PLL start and an HSI fallback reported as CLK_FALLBACK; all timer, pulse and baud constants shall be derived from the actual bus clocks (no hard-coded Stefan values). | R2 §5.1, R3 P7, R4 §1.9 | Inspection; twin with simulated HSE failure → flag; target: measured PUL frequency error ≤ 0.1 % on HSE. | I, T, T+H | M1 | Must |

#### FW-AFE — HX711
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| FW-AFE-001 | The FW shall read one HX711 on the DOUT-ready falling edge (EXTI), bit-banged in the ISR with interrupts masked only during each SCK-high (≤ 1 µs, never masking the E-stop/STOP/limit IRQs), SCK-high ≤ 50 µs always; result as int32 sign-extended 24-bit; driver logic reused from Thrust_Stand_HAW `hx711_seq` with origin note. | PO-FW-1, D-04, R2 §4.1, R3 §3.3 | Host vectors of the reused pure module pass; target logic-analyser capture of 10 000 reads: max SCK-high ≤ 50 µs, read ≤ 60 µs. | T, T+H | M2 | Must |
| FW-AFE-002 | Gain/channel (A/128, A/64, B/32 = 25/27/26 pulses) and rate (10/80 SPS through the RATE GPIO) shall be configurable without reboot; defaults A/128 and 80 SPS. | PO-FW-1, PO-FW-4, D-04, D-21 | Twin: SET gain → pulse count changes on the next read; RATE pin level follows the parameter; defaults after DEFAULTS. | T | M2 | Must |
| FW-AFE-003 | After power-up, gain/rate change or a detected HX711 power-down/reset, the first `afe.settle_discard` (default 4) samples shall be flagged AFE_SETTLING in DATA (still subject to the load limit); a detected power-down shall trigger re-initialisation and increment a counter in GET_STATUS. | R2 §4.1, §4.3 | Twin: 4 flagged frames after a gain change; injected SCK-high overrun → re-init counter +1. | T | M2 | Must |
| FW-AFE-004 | The FW shall measure the real DOUT-ready period continuously (median of the last 16 periods), report the measured rate (0.1 SPS resolution) in GET_STATUS and set AFE_RATE_MISMATCH (flag + EVENT) when it deviates from the configured rate by more than `afe.rate_tol_pct` (default 20 %). | D-21, R2 §4.2 | Twin: HX711 model at 10 SPS with 80 configured → flag ≤ 1 s; 80 SPS ± 2 % → no flag; target: reported rate within ±1 % of the logic-analyser value. | T, T+H | M2 | Must |
| FW-AFE-005 | Each conversion shall be time-stamped with the 32-bit µs timer at DOUT-ready EXTI entry, and the commanded position (setpoint distance) shall be latched in the same ISR. | D-05, D-23, R4 §2.3, §9 | Twin: timestamp = virtual data-ready time ± 2 µs; latched position = step count at that instant. | T | M2 | Must |

#### FW-MOT — step/dir/enable motion
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| FW-MOT-001 | PUL shall be generated by a hardware timer in PWM mode with the pulse at the end of each period (R4 §1.3–1.4); pulse high width (default 10 µs, min 2.5 µs), minimum low width (default 10 µs), maximum step rate (default 50 kHz, cap 200 kHz) and DIR setup before the first active edge (default 20 µs) are parameters; DIR changes only while the timer is stopped; PUL/DIR/ENA polarities are parameters. | PO intro, D-16, D-24, R2 §1.4, R4 §1.4 | Twin vectors; target logic analyser at 50 kHz: high ≥ 10 µs, low ≥ 10 µs, DIR setup ≥ 20 µs on every reversal. | T, T+H | M2 | Must |
| FW-MOT-002 | Position shall be an int32 step counter changed only per completed pulse and reported in µm via `steps_per_mm` with round half away from zero. | R4 §1.6, §2.2 | R4 TV-TC `um_to_steps`/`steps_to_um` vectors pass on host. | T | M2 | Must |
| FW-MOT-003 | Moves shall follow trapezoid/triangle profiles from the exact square-root ramp with fractional carry and separate accel/decel; period sequences match R4 TV-M within ±1 timer tick per period and ±N/1000 ticks in total; speed changes on the fly (JOG, controlled stop) use the virtual-index method. | R4 §1.5, §12 | Host vectors TV-M (5.2 s move, triangle, asymmetric triangle) pass. | T | M2 | Must |
| FW-MOT-004 | MOVE_ABS(target µm, speed µm/s, accel µm/s², 0 = default) shall move to the absolute target and send MOVE_DONE with the final position; a new MOVE_ABS/MOVE_UNTIL_LOAD while moving is refused (busy). | D-23, R4 §2.1 | Twin: 10 mm at 5 mm/s ends at the exact step count, duration = planner ± 1 ms; second move while moving → NACK busy. | T | M2 | Must |
| FW-MOT-005 | JOG(signed speed) shall move toward the soft limit in that direction (un-homed: bounded by `home.max_travel_um` from the start point and ≤ `motion.v_unhomed_um_s`); repeated JOG changes the speed on the fly and refreshes the dead-man; JOG(0) = controlled stop. | PO-SW-5, D-23, R4 §2.1 | Twin: jog never passes the soft limit; speed change ramps without step loss. | T | M2 | Must |
| FW-MOT-006 | MOVE_UNTIL_LOAD(direction, speed, accel, raw stop threshold, absolute travel bound) shall move until the first sample beyond the raw threshold (immediate stop with the SAF-FW-002 load-path timing) or until the bound, and report MOVE_DONE with the reason. | D-23, R4 §2.1, §8.4 | Twin with specimen model: stop on the first sample past the threshold; bound respected; reason codes correct. | T | M4 | Must |
| FW-MOT-007 | STOP(mode immediate / controlled) shall stop without latching; HALT shall stop immediately and latch HALT (source = PC) until HALT_CLEAR; both accepted in every state and idempotent. | PO-SW-5, PO-SW-6, D-14, D-26 | Twin: HALT during move → stop + latch; motion NACK until HALT_CLEAR; repeated HALT → one latch, OK responses. | T | M2 | Must |
| FW-MOT-008 | ENABLE shall assert the ENA enabled level, wait `motion.ena_settle_ms` (default 200 ms) before any pulse and set "enabled"; refused while ESTOP is latched or its input open. DISABLE (refused while moving) shall assert the disabled level and clear HOMED. | D-13, D-16, R2 §1.4–1.5 | Twin: first pulse ≥ 200 ms after ENABLE; DISABLE while moving → NACK; after DISABLE HOMED = 0. | T | M2 | Must |
| FW-MOT-009 | `motion.steps_per_mm` (float32; range at least 100…10 000, proposed 1…100 000; default **160**, ASSUMED per D-27) shall be changeable only while idle; HOMED is kept and µm positions rescale (machine zero is a step count). All speed/acceleration parameters and command arguments are in µm/s and µm/s²; the effective speed limit is min(`motion.v_max_um_s`, `motion.max_step_rate_hz`·1000/steps_per_mm), so a steps/mm change never invalidates a speed parameter. | PO-FW-4, D-19, D-27, R4 §2.2 | SET while moving → NACK busy; SET 100 and 10 000 accepted; after SET while idle, reported position = steps·1000/spm; at spm = 10 000 a 10 mm/s move is refused (E_RANGE, step-rate cap) while v_max is unchanged. | T | M2 | Must |

#### FW-HOM — homing / zeroing
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| FW-HOM-001 | HOME shall run: pre-check (enabled, no latch, SAF-FW-021) → release the home switch if active → fast seek toward `home.switch` (default START) at `home.v_fast_um_s` → immediate stop on the edge → back-off `home.backoff_um` → slow approach at `home.v_slow_um_s` capturing the position at the first switch edge in the EXTI ISR → set zero so that the edge lies at −`home.offset_um` → move to 0 → HOMED + EVENT. | PO-FW-2, D-08, D-18, R4 §3.1 | Twin with switch model incl. bounce: HOMED set, final position 0 = edge + offset ± 1 step. | T | M2 | Must |
| FW-HOM-002 | Homing failures shall stop immediately, leave the axis not homed and report: no switch within `home.max_travel_um` → HOME_NOT_FOUND; opposite switch reached → HOME_WIRING; any other stop/fault during HOME → HOME_ABORTED. | R4 §3.1 | Twin: the three scenarios give the expected fault and EVENT. | T | M2 | Must |
| FW-HOM-003 | The home position shall be repeatable within 0.02 mm (2σ) over 10 homing cycles. | R4 §3.1 | Target: dial indicator at the zero position, 10 cycles. | T+H | M2 | Should |

#### FW-SW — switch and button inputs
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| FW-SW-001 | START/END limit inputs shall use own EXTI lines, react on the first edge (no software delay) and debounce only the release (stable ≥ `input.release_ms`, default 20 ms, 1 kHz sampling); hardware RC τ ≈ 100 µs. | PO-FW-2, D-18, R4 §3.4 | Twin with bounce model: one stop per hit; release reported after 20 ms stable. | T | M2 | Must |
| FW-SW-002 | The E-stop sense input shall use its own EXTI at the highest interrupt priority, never masked by AFE critical sections (BASEPRI scheme), with hardware RC τ ≤ 10 µs; release debounced ≥ `input.estop_release_ms` (default 100 ms). | D-11, R4 §1.8, §3.4 | Inspection of NVIC priorities/BASEPRI; twin release test. | I, T | M2 | Must |
| FW-SW-003 | The physical STOP/BREAK and PAUSE button inputs shall each have an EXTI with priority just below the E-stop, react on the first active edge, debounce release/re-arm with `input.release_ms`, have a polarity parameter (`io.stop_active_level`, `io.pause_active_level`; contact type per R5, NC recommended for STOP), and report their state in DATA status and GET_STATUS plus an EVENT on every press. | D-14, D-26 | Twin with bounce model: one EVENT per press; polarity parameter inverts detection; state bits in DATA. | T | M2 | Must |
| FW-SW-004 | ALM and PEND inputs shall be read with polarity parameters `drv.alm_active_level` / `drv.pend_active_level`, reported in every DATA frame status and GET_STATUS, with an EVENT on every ALM change; **no automatic reaction** in this release. | D-16 | Twin: ALM toggle → flag + EVENT, motion continues; inspection: no stop path from ALM. | T, I | M2 | Must |

#### FW-CFG / FW-NVM — configuration
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| FW-CFG-001 | All configuration parameters shall be defined only in `params.yaml` (id, type, unit, min, max, default, NVM flag, settable-while-moving flag); FW tables are generated and never hand-edited. | PO-FW-4, CONV | Generator `--check` passes in CI; no hand-written parameter table in `02_FW`. | I | M1 | Must |
| FW-CFG-002 | A command sequence shall read **all** parameters (ids + values, paged if needed). | PO-FW-5 | Twin: every dictionary entry returned exactly once with its current value. | T | M1 | Must |
| FW-CFG-003 | A command shall set any single parameter: type and range checked (out of range → E_RANGE, never clamped), hard rules checked, parameters not settable while moving → busy; the response returns the value as stored; no implicit flash write. | PO-FW-6 | Vectors per parameter type: min/max accepted, min−1/max+1 refused; flash untouched after SET. | T | M1 | Must |
| FW-CFG-004 | GET_INFO shall return protocol version (major.minor), payload version, FW version + build id, parameter-dictionary hash, MCU UID and feature mask. | IF-008, R3 §1.4 | Twin: fields match build constants and generated hash. | T | M1 | Must |
| FW-NVM-001 | SAVE, LOAD and DEFAULTS commands shall exist; CFG_DIRTY (RAM ≠ NVM record) shall be reported with one normative definition (ICD). | PO-FW-6, R3 §1.4, §1.7 P2 | Twin: SET → dirty; SAVE → clean; reboot → saved values restored; DEFAULTS → defaults, dirty. | T | M1 | Must |
| FW-NVM-002 | NVM shall use two alternating flash records with sequence number, CRC-32 and dictionary hash: a power loss during SAVE keeps the previous record; corrupt/absent → defaults + flag; changed hash → migration by id (same id, type and in range kept); ≥ 10 000 saves. | R2 §5.5, R3 §1.4 | Twin flash model: power cut at every program step → a valid record at boot; endurance analysis. | T, A | M1 | Must |
| FW-NVM-003 | Flash erase/program shall run only while idle (otherwise busy), after queued TX frames are drained; HX711 samples lost during the stall are flagged. | R2 §5.5, R3 §1.7 P5 | Twin: SAVE while moving → NACK; target: no split frame on the wire around SAVE. | T, T+H | M1 | Must |

#### FW-CMD — command handling
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| FW-CMD-001 | Every CRC-valid command shall get exactly one response echoing its link sequence number; NACK carries status + detail and has no side effect; check order type → length → arguments → state → execution; unknown type → NACK. | IF-005, R3 §1.3 | Shared protocol vectors pass (incl. unknown type, wrong length). | T | M1 | Must |
| FW-CMD-002 | SET_VALID(0/1) shall return the device time t_us from which the value applies; every DATA frame with t_us ≥ that value carries it. | PO-FW-7, D-05, R4 §8.5 | Twin: frames before/after the boundary carry the old/new value. | T | M1 | Must |
| FW-CMD-003 | FAULT_CLEAR shall clear latched faults (LOAD_LIMIT per SAF-FW-011, AFE_FAULT, STEP_FAULT, LIMIT_WIRING, homing faults) only when their cause is gone; otherwise NACK naming the remaining cause. ESTOP and HALT have their own clear commands. | R4 §4.2 | Twin: one test per fault. | T | M2 | Must |
| FW-CMD-004 | GET_STATUS shall report: rx frames OK / CRC errors / framing errors, RX overruns, TX drops, loop_max_us, reset cause, link age, measured AFE rate, AFE re-init count, all latches, all input states, CLK_FALLBACK, last NVM save time. | R3 §1.4, D-21 | Twin: each counter increments under its injected condition. | T | M1 | Must |

#### FW-STR / FW-TIM — streaming and time base
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| FW-STR-001 | STREAM_START / STREAM_STOP commands shall start/stop DATA frames; the stream is off after boot; the stream state does not affect any safety function. | PO-FW-8 | Twin: start/stop; SAF tests pass with stream off. | T | M1 | Must |
| FW-STR-002 | One DATA frame per HX711 conversion shall be sent, transmission starting ≤ 2 ms after data-ready (M1: synthetic samples at 80 Hz). | D-23, PROCESS M1 | Twin: frames = conversions over 10 min; latency max ≤ 2 ms. | T | M1 | Must |
| FW-STR-003 | DATA shall carry the fields of IF-006, with status bits at least: VALID, MOVING, HOMED, ENABLED, ESTOP, HALT, PAUSED, LIMIT_START, LIMIT_END, LOAD_LIMIT, AFE stale/saturated/settling, AFE_RATE_MISMATCH, LINK_WDG, STOP button, PAUSE button, ALM, PEND, OVERRUN, `pos_uncertain`. | PO-FW-11, D-05, D-23 | Shared codec vectors; twin: each bit set under its condition. | T | M1 | Must |
| FW-STR-004 | The u16 frame sequence counter shall increment by 1 per DATA frame, also for frames dropped in the FW; the next sent frame carries OVERRUN. | D-23, R4 §9 | Twin with forced TX congestion: counter gap = dropped frames, OVERRUN set. | T | M1 | Must |
| FW-STR-005 | While the AFE is stale, the FW shall send DATA frames at `stream.fallback_hz` (default 10 Hz) marked "no AFE data", so that position and status stay visible. | R3 §1.6 (a) | Twin: DOUT silent → 10 Hz frames with the marker. | T | M2 | Should |
| FW-STR-006 | EVENT frames shall report: every stop with cause, fault set/clear, VALID auto-cleared (with cause), MOVE_DONE, HOMED / homing failure, driver enable/disable (incl. idle), STOP/PAUSE button presses, RESUME_REQUEST, ALM changes, AFE re-init and rate mismatch; queue ≥ 8 deep, overflow counted. | R3 §1.4, R4 §4, D-26 | Twin: each event observed with correct code and t_us. | T | M2 | Must |
| FW-TIM-001 | A free-running 32-bit 1 MHz timer (wrap 71.6 min) shall be the device time base for DATA t_us, SET_VALID and EVENT timestamps; data-ready timestamp latency ≤ 5 µs. | PO-FW-11, D-23, R4 §9 | Twin; target: timestamp vs logic-analyser DOUT edge, max difference ≤ 5 µs. | T, T+H | M2 | Must |

#### FW-PAR — required configuration parameters (existence and range; dictionary = Integrator, §5)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| FW-PAR-001 | The dictionary shall contain the AFE parameters of Table 5.1 (gain/channel, rate, rate tolerance, settle discard, stale timeout) with at least the listed ranges and the listed defaults. | PO-FW-1, PO-FW-4, D-04, D-21 | `params.yaml` review against Table 5.1; generated C/Python contain them; SW-CFG displays them. | I | M1 | Must |
| FW-PAR-002 | The dictionary shall contain the mechanics and pulse-timing parameters of Table 5.1 (steps/mm, PUL/DIR/ENA polarity, pulse high/low width, max step rate, DIR setup, ENA settle). | PO-FW-4, D-16, D-19 | as FW-PAR-001. | I | M1 | Must |
| FW-PAR-003 | The dictionary shall contain the speed/acceleration parameters of Table 5.1 (v_max, a_max, a_stop, v_unhomed, jog timeout), all in length units (µm/s, µm/s²), never in steps. | D-19, D-27, R4 §2.1, §3.2 | as FW-PAR-001; inspection: no step-unit speed parameter. | I | M1 | Must |
| FW-PAR-004 | The dictionary shall contain the travel parameters of Table 5.1 (soft min/max with rule min < max; home switch, fast/slow speed, accel, back-off, offset, max travel). | PO-FW-2, D-18, D-19, R4 §3 | as FW-PAR-001. | I, T | M1 | Must |
| FW-PAR-005 | The dictionary shall contain the safety parameters of Table 5.1 (load raw min/max, trip samples, regrow, zero_raw, release band, homing max load, idle disable time, link timeout). | D-12, D-15, R4 §4 | as FW-PAR-001. | I | M1 | Must |
| FW-PAR-006 | The dictionary shall contain the I/O parameters of Table 5.1 (input release debounce, E-stop release time, STOP/PAUSE/ALM/PEND polarity, stream fallback rate) and shall **not** contain polarity parameters for the E-stop sense and limit inputs. | D-16, D-26, SAF-FW-007 | as FW-PAR-001. | I | M1 | Must |

### 4.5 Interface (IF-)

| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| IF-001 | The PC⇄FW protocol shall be fully specified in `ICD_protocol.md`, owned by the Integrator, versioned with a change history; FW and SW implement only from it. | CONV, PROCESS §1 | ICD exists with version + history; review: every command/field used in code appears in the ICD. | I | M1 | Must |
| IF-002 | Physical link: USART2 via the ST-LINK/V2-1 VCP, **921 600 Bd, 8N1, no flow control**, binary (not human-readable), multi-byte values little-endian. | PO-FW-9, PO-FW-10, D-03 | Twin/codec vectors little-endian; target soak (SYS-009) at 921 600 Bd. | T, T+H | M1 | Must |
| IF-003 | Every frame shall start with a fixed **separator** (sync bytes), followed by type, link sequence number, length, payload, CRC; the ICD fixes the maximum payload and the receiver resync rules (drop one byte on CRC/length error, inter-byte timeout). | PO-FW-10, D-05, R3 §1.1–1.2 | Both parsers reproduce the shared stream vectors (bad CRC, noise, sync in payload, truncated frames). | T | M1 | Must |
| IF-004 | Every frame in **both directions** shall carry a CRC-16 (variant fixed in the ICD; proposed CRC-16/CCITT-FALSE, check "123456789" → 0x29B1); frames with a bad CRC are dropped, counted and never acted upon. | PO-FW-9, PO-FW-10, D-03 | CRC check vector; corrupted-frame vectors produce no action and increment the counter. | T | M1 | Must |
| IF-005 | Command/response: one response per CRC-valid command echoing its sequence number, ACK/NACK with status + detail; the PC retries only idempotent commands, with a new sequence number and the newest value; response timeouts and retry counts are defined in the ICD. | R3 §1.3, §1.5, §1.7 P4 | Protocol vectors; simulator with dropped responses: no duplicated motion. | T | M1 | Must |
| IF-006 | The DATA frame shall contain: system time t_us (u32 µs), payload version (1 B), flag byte with bit 0 = data validity, AFE raw (i32), setpoint distance (i32 µm), frame sequence counter (u16), status flags (FW-STR-003), CRC-16; byte layout defined in the ICD. | PO-FW-11, D-05, D-23 | Codec vectors decode every field; ICD layout review. | T, I | M1 | Must |
| IF-007 | Exactly one DATA frame shall be sent per HX711 conversion (fallback frames per FW-STR-005 marked as such). | D-23 | Twin: frame count = conversion count over 10 min. | T | M1 | Must |
| IF-008 | Versioning: PROTO_VERSION major.minor (minor = backward-compatible additions), PAYLOAD_VERSION in every DATA frame, parameter-dictionary hash in GET_INFO; at connect the SW refuses motion and shows read-only state on a major or payload-version mismatch, and makes the configuration read-only with a warning on a hash mismatch. | R3 §1.3, §2.1 | Simulator with mismatching versions/hash → expected SW behaviour. | T | M1 | Must |
| IF-009 | Wire units: positions µm (i32), speeds µm/s, accelerations µm/s², times µs, load raw counts; motion commands carry absolute targets only (no relative moves). | D-23, R4 §0.1, R3 §1.6 (b) | ICD review; codec vectors. | I | M1 | Must |
| IF-010 | `params.yaml` shall generate the FW C header and the SW Python module (never hand-edited, `--check` in CI); shared protocol vectors and a reference codec shall be used by FW host tests and SW pytest alike. | CONV, R3 §2, §7.1 | CI job runs `--check` and both test suites on the same vector file. | I, T | M1 | Must |
| IF-011 | FW→PC traffic shall stay ≤ 10 % of the link capacity at 80 Hz incl. events; STOP, HALT and ESTOP_CLEAR frames from the PC are never delayed by SW queues or TX throttling. | R3 §1.5, R4 §9 | Analysis of frame sizes; SW test: STOP sent while the queue is full goes out first. | A, T | M1 | Must |
| IF-012 | The ICD shall define at least: PING, GET_INFO, GET_STATUS, GET_ALL_PARAMS, GET_PARAM, SET_PARAM, SAVE/LOAD/DEFAULTS, STREAM_START/STOP, SET_VALID, ENABLE/DISABLE, HOME, MOVE_ABS, JOG, MOVE_UNTIL_LOAD, STOP, HALT/HALT_CLEAR, ESTOP_CLEAR, FAULT_CLEAR, REBOOT; async DATA and EVENT. | PO-FW-5…8, D-23, D-26 | ICD review checklist. | I | M1 | Must |

### 4.6 PC software (SW-)

#### SW-PLT — platform
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-PLT-001 | The SW shall be a Windows 10 (64-bit) desktop application on Python ≥ 3.11 with PySide6, pyqtgraph, numpy, pyserial, installable from a documented requirements file. | PROCESS §5 | Fresh install on Windows 10 starts; dependency list inspected. | I, D | M1 | Must |
| SW-PLT-002 | Backend packages (`core`, `io`, `calc`) shall not import Qt widgets; calculations shall be pure functions covered by pytest vectors (all R4 §12 vectors). | CONV | Import-check test; pytest of R4 §12 passes. | T, I | M1 | Must |
| SW-PLT-003 | The SW shall connect to a COM port or to the simulator/twin endpoint, run the connect sequence (GET_INFO → version/hash check → GET_STATUS → GET_ALL_PARAMS) and show link statistics (frames, lost frames, CRC errors). | R3 §1.5, IF-008 | Simulator connect test; statistics change under injected loss. | T | M1 | Must |

#### SW-CFG — board configuration (PO-SW-1)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-CFG-001 | The SW shall read all settings from the board and display them in dedicated typed fields generated from the dictionary (unit, range, enum names, board value, default, edited value). | PO-SW-1 | GUI test vs simulator: every parameter shown, values equal the board. | T | M1 | Must |
| SW-CFG-002 | The configuration shall be saved to a JSON file (enums by name, dictionary hash) and recalled; recall fills edit fields only and reports unknown, missing and out-of-range entries and hash mismatch. | PO-SW-1 | Round trip identical; crafted file → each report case shown. | T | M1 | Must |
| SW-CFG-003 | Edited values shall be checked against hard rules, written to the board and validated by read-back, with per-parameter status OK / REJECTED / MISMATCH / BUSY / TIMEOUT. | PO-SW-1 | Simulator with injected rejection/mismatch → status shown. | T | M1 | Must |
| SW-CFG-004 | Buttons shall save to NVM, reload from NVM and restore defaults (with confirmation), and a CFG_DIRTY indicator shall be shown. | PO-SW-1, FW-NVM-001 | Demonstration against the simulator. | D | M1 | Must |

#### SW-LIM — safety limits (PO-SW-2)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-LIM-001 | Travel limits (min/max, machine mm, each with enable) shall be configurable within the FW soft limits; manual control and the sequencer refuse targets outside them; violations are enforced by SAF-SW-001. | PO-SW-2 | Test: target outside → refused with message; limit outside soft limits → refused. | T | M3 | Must |
| SW-LIM-002 | Load limits shall be configurable: pull max (N > 0) and push max (N < 0), each with enable and warning level (defaults ±100 % FS trip, 90 % warning), plus the FW load-limit level (default 110 % FS; may be set lower but not below the SW trip level; never above 110 % FS) used by SAF-SW-002. | PO-SW-2, D-12 | Test: FW level > 110 % FS or < SW trip → refused; warning indicator at 90 %. | T | M3 | Must |
| SW-LIM-003 | Limits shall be saved/recalled with the session file, applied at start, and recorded in the recording metadata and the report. | PO-SW-2, PO-SW-3 | Round trip; JSON metadata contains the limits. | T | M3 | Must |

#### SW-META — report marks (PO-SW-3)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-META-001 | Report marks shall include measured element (specimen), specimen number, operator and notes, plus user-defined custom key/value fields (add, rename, remove). | PO-SW-3 | GUI test: custom field added appears in the JSON metadata and HTML report. | T | M3 | Must |
| SW-META-002 | Mark presets shall be saved/recalled; marks plus an automatic snapshot (board configuration, calibration, tare, limits, SW/FW versions) shall be included in every recording and report. | PO-SW-3, D-23 | Test: recording JSON contains marks + snapshot. | T | M3 | Must |

#### SW-RT — realtime display (PO-SW-4)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-RT-001 | Realtime plot windows shall be detachable (dock, float, re-attach), at least two at a time, each with its own STOP button; the layout is restored at the next start. | PO-SW-4, R3 §6.2 | GUI test float/re-attach; restart restores layout. | T, D | M3 | Must |
| SW-RT-002 | Plots shall be switchable on/off per channel for every DATA field (raw, setpoint distance, sample rate, frame-sequence gaps, each status bit) and for every derived channel; derived channels whose prerequisites (calibration, tare, geometry) are missing are shown greyed. | PO-SW-4 | GUI test toggles every channel; greyed state without calibration. | T | M3 | Must |
| SW-RT-003 | A time view (adjustable window 5–600 s, freeze, auto/manual Y) and an **X-Y view** (x = travel mm, y = load N or kgf) of the live data shall be available. | PO-SW-4, PO-SW-15, R3 §6.2 | Demonstration with the simulator. | D | M3 | Must |
| SW-RT-004 | Derived channels per R4 §10 shall be provided: force N and kgf, travel mm (machine and test), speed, force rate, tangent and secant stiffness, work, peak force, noise (rolling std), sample rate / lost frames, and optional 3-point-bend stress/strain when geometry is entered; each a pure function with vectors. | PO-SW-4, R4 §10 | pytest R4 TV-D and derived-channel vectors. | T | M3 | Must |
| SW-RT-005 | Numeric readouts (value and state n/a / STALE / SATURATED / INVALID) shall be shown for force, travel, raw and sample rate. | R3 §6.11 | Demonstration with injected states. | D | M3 | Should |

#### SW-MAN — manual control (PO-SW-5)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-MAN-001 | A slider shall set the target position over the SW travel range; one MOVE_ABS is sent on release (nothing while dragging); disabled when not homed. | PO-SW-5, D-23 | GUI test: drag sends nothing; release sends one MOVE_ABS with the slider value. | T | M3 | Must |
| SW-MAN-002 | A numeric entry shall accept an absolute target or a travel distance (±mm); a distance is converted by the SW to an absolute target = last commanded target + distance. | PO-SW-5, D-23 | Test: the wire carries only absolute targets. | T | M3 | Must |
| SW-MAN-003 | Buttons −10, −1, −0.1, +0.1, +1, +10 mm shall send absolute targets = last commanded target ± step; repeated clicks accumulate on the commanded target, not on the live position. | PO-SW-5, D-23 | Test: three +1 mm clicks from 10.000 mm → targets 11, 12, 13 mm. | T | M3 | Must |
| SW-MAN-004 | Hold-to-jog forward/rear buttons shall send JOG while pressed (refresh ≤ 100 ms) and JOG 0 on release or focus loss; jog works un-homed at ≤ v_unhomed. | D-23, SAF-FW-016 | GUI test incl. focus loss → JOG 0 sent. | T | M3 | Must |
| SW-MAN-005 | Manual speed and acceleration fields (limited to the FW maxima) and a STOP button for immediate stop shall be on the manual tab. | PO-SW-5 | Test: values above max refused; STOP sends STOP immediate. | T | M3 | Must |
| SW-MAN-006 | The manual tab shall offer driver enable/disable (bound to FW state), HOME, set test travel zero (PC offset) and the VALID flag toggle. | PO-FW-2, PO-FW-7, D-23 | Demonstration with the simulator. | D | M3 | Must |

#### SW-STOP — stop, Pause/Break, pause/resume (PO-SW-5, PO-SW-6)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-STOP-001 | A GUI STOP button (immediate operational stop, D-26 (2)) shall be present as the first toolbar item, in every floating window and in every dialog; it never takes keyboard focus, bypasses command queues and terminates any running sequence or wizard motion. | PO-SW-5, PO-SW-6, D-26 | GUI test "STOP reachable on every tab/window/dialog"; latency per NFR-002. | T | M3 | Must |
| SW-STOP-002 | A system-wide Pause/Break hotkey (Pause, Ctrl+Break; also when the application is not focused) shall send HALT, terminate the running sequence and any calibration/tare step, and repeat HALT until it is acknowledged (ACK, or HALT shown in DATA/GET_STATUS) — ≤ 20 tries in 1 s, also with the stream off. | PO-SW-6, D-14, D-26, R3 §6.3 | Test with another application focused; latency per NFR-003. | T | M3 | Must |
| SW-STOP-003 | A HALT or ESTOP reported by the FW from any source (key, physical STOP/BREAK button, E-stop) shall terminate any running sequence/wizard/tare within one received frame; a latched HALT shall be cleared only by an explicit GUI "Clear stop" action; no motion restarts automatically after the clear. | D-11, D-14, D-26 | Simulator: physical-button HALT and E-stop EVENT during a sequence → sequence terminated; clear sends HALT_CLEAR; no motion command follows automatically. | T | M3 | Must |
| SW-STOP-004 | Sequence Pause (GUI Pause button or physical PAUSE EVENT) shall cause a controlled stop and keep the sequence state; Resume (GUI or RESUME_REQUEST from the button) shall restart the interrupted step's motion (re-issue the absolute target or re-run approach + trim) and its capture window. In manual mode PAUSE is a controlled stop only. | D-14, D-26 (1), PO-SW-12 | Simulator: pause during a travel step and a load step, resume → step completes; VALID never 1 during the pause. | T | M4 | Must |

#### SW-ACQ — stream, recording, momentary sample (PO-SW-7)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-ACQ-001 | Stream start/stop buttons shall be available on the toolbar from every tab, with the stream state shown. | PO-SW-7, PO-FW-8 | GUI test on every tab. | T | M1 | Must |
| SW-ACQ-002 | Record start/stop shall write a new test folder with a CSV of all DATA fields (unwrapped t_us, frame sequence, flags, raw, setpoint distance), derived columns and event rows (tare, VALID changes, stops, steps), plus a JSON metadata sidecar; recording runs during the whole sequence. | PO-SW-7, PO-SW-12, D-23 | 1 h simulator recording contains every frame exactly once. | T | M3 | Must |
| SW-ACQ-003 | "Take sample" shall append mean, std and N of force, travel and raw over a window (default 1 s, 0.1–10 s), with timestamp and marks, to `samples.csv`. | PO-SW-7 | pytest on the statistics; GUI test appends one row. | T | M3 | Must |
| SW-ACQ-004 | Frame-sequence gaps, CRC errors and overruns shall be counted and written into the recording; a recording failure (disk full, write error) shall be reported immediately, stop a running sequence (controlled stop) and never drop rows silently. | R3 §6.7 | Fault-injection tests. | T | M3 | Must |

#### SW-CAL — calibration (PO-SW-8)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-CAL-001 | A calibration tab shall start a travel wizard and a load wizard; each step shows its description, input fields, Continue / Repeat / Cancel and a progress bar during capture; STOP stays reachable; Cancel or any stop leaves the active calibration unchanged. | PO-SW-8.WIZ | GUI test through all pages; cancel at each step keeps the old calibration. | T | M3 | Must |
| SW-CAL-002 | Travel wizard (homed, no specimen): take up backlash with +2 mm; step 1 moves 10 mm (N1 = round(10·spm0)), the operator enters the measured distance D1, spm1 = N1/D1 is written to the board and verified; step 2 moves 50 mm more (N2 = round(50·spm1), target = re-read commanded position + 50 mm), the operator enters the **total** D_tot, spm2 = (N1 + N2)/D_tot is written; all moves are absolute targets in one direction. | PO-SW-8.TC1, PO-SW-8.TC2, D-23, R4 §5.1 | pytest R4 TV-TC (N1 = 6400, spm1 = 636.8159…, N2 = 31841, spm2 = 636.0778…); simulator run of the wizard. | T | M3 | Must |
| SW-CAL-003 | Travel plausibility: reject D ≤ 0 or a result outside 100…10 000 steps/mm; a change > 20 % is not rejected but requires explicit confirmation that shows the candidate DIP readings (160 / 1280 steps/mm, D-27) — needed for the first calibration; a change > 5 % or a value outside ±20 % of the configured expected steps/mm asks for confirmation; warn "repeat" when the incremental value N2/(D_tot − D1) differs from spm1 by > 0.5 %. | R4 §5.1, §5.3, D-27 | pytest for each rule incl. 160 → 1280 (confirmation, not rejection). | T | M3 | Must |
| SW-CAL-004 | The accepted steps/mm shall be written to the board, verified by read-back, saved to NVM and stored in a travel calibration file (date, D1, D_tot, N1, N2, results; displayed with 3 decimals). | PO-SW-8.TC2, R4 §5.1 | Simulator: board value, NVM and file agree. | T | M3 | Must |
| SW-CAL-005 | Load wizard per the PO sequence: step 1 "remove load" → reference (zero) point; step 2 "put known weight", mass entered → point; step 3 "put bigger known weight", mass entered → point; for each point the stream is on, 2 s pre-settle, then 10 s capture (configurable). | PO-SW-8.PC1–PC3, D-22 | Simulator: three points, each with 800 ± 1 nominal samples. | T | M3 | Must |
| SW-CAL-006 | Point acceptance: any saturated sample → invalid; MAD outlier rejection (5·σ_MAD) with > 2 % rejected → invalid; ≥ 95 % of nominal samples; drift ≤ max(2·std, 20 counts); std ≤ max(3·std_zero, 50 counts); masses increasing with m2 ≥ 1.5·m1 (m1 < 2 % FS → warning only, D-22); an invalid point is re-taken. | R4 §6.1–6.2, D-22 | pytest R4 TV-RS; scenario tests for each rejection. | T | M3 | Must |
| SW-CAL-007 | The SW shall fit F = K·raw + B by OLS, show residuals, R² (informative) and NL_span, and classify PASS (≤ 0.1 % span), WARN (≤ 0.5 %, operator may accept) or FAIL (> 0.5 %, K ≈ 0 or non-monotonic → "points are not linear", refused); 2 points → UNVERIFIED_LINEARITY; negative K accepted. | PO-SW-8.FIT, D-23, R4 §6.2 | pytest R4 TV-LC (PASS / WARN / FAIL / 2-point / degenerate). | T | M3 | Must |
| SW-CAL-008 | When the largest reference force is < 20 % FS, the calibration shall carry a LOW_SPAN warning, and displayed forces beyond 3× the largest calibration force shall be marked "extrapolated". | D-22, P1 follow-up, R4 Q-R4-07 | Test with 1 kg + 10 kg → LOW_SPAN; force > 294 N marked. | T | M3 | Should |
| SW-CAL-009 | The calibration result shall be saved as JSON (R4 §6.4 schema incl. AFE config, points, fit, status, `push_calibrated = false`) and an active copy loaded by default at start; an AFE-configuration mismatch with the board shall warn "calibration invalid"; previous files are kept. | PO-SW-8.FILE, D-20 | Restart → same K/B active; changed gain on the board → warning. | T | M3 | Must |

#### SW-TARE — tare (PO-SW-8)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-TARE-001 | A TARE button shall be on the global toolbar, usable from every tab, with non-modal progress and verbatim refusal reasons. | PO-SW-8.TARE-ANY | GUI test on every tab. | T | M3 | Must |
| SW-TARE-002 | Tare shall capture `tare.window_s` (default 10 s, 2–60 s) with the robust statistics of R4 §6.1, set tare_raw = robust mean and apply F = K·(raw − tare_raw); it starts the stream if off and restores its state; a tare event row is recorded; on success the FW thresholds are re-sent (SAF-SW-002). | PO-SW-8.TARE, R4 §7 | pytest R4 TV-T `force_n` vectors; simulator: thresholds re-sent after tare. | T | M3 | Must |
| SW-TARE-003 | Tare shall be refused while moving or < 1 s after a move, with a stop/fault latched, with std/drift beyond the R4 §7 limits, > 2 % outliers or any saturated sample, > 1 % lost frames, or during a sequence capture window; it shall warn when abs(K·(tare_raw − raw_zero_cal)) > 10 % FS. | R4 §7 | One scenario test per refusal/warning. | T | M3 | Must |

#### SW-SEQ — test sequences (PO-SW-12)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-SEQ-001 | A sequence tab shall edit a step table with: step type (travel target, load target, hold, home, tare, mark), target (mm or N), speed (mm/s), ramp (accel mm/s², 0 = default), settling time (s), capture duration (s), step time (s, minimum duration; timeout for load steps), load tolerance (N), label; invalid entries are flagged in the editor. | PO-SW-12, R4 §8.1 | Model tests; GUI test of field validation. | T | M4 | Must |
| SW-SEQ-002 | Loops over a step range with a count (1–10 000) or "until stopped", nestable one level, shall be supported; loop iteration counters are recorded and reported. | PO-SW-12 | Expanded-plan tests for nested loops. | T | M4 | Must |
| SW-SEQ-003 | The backend engine shall execute steps as single absolute FW commands with the timeline command → reached → settle → capture → hold until max(step time, settle + capture); recording runs during the whole sequence. | PO-SW-12, R4 §8.1 | Simulator: each phase boundary within ±1 frame of plan. | T | M4 | Must |
| SW-SEQ-004 | The SW shall set VALID = 1 exactly over the capture windows (via SET_VALID device time) and 0 at window end and on pause/stop/abort; every frame with VALID = 1 lies inside a planned capture window. | PO-SW-12, PO-FW-7, R4 §8.5 | Simulator recording check over a 50-step sequence. | T | M4 | Must |
| SW-SEQ-005 | Sequence start shall be refused unless the axis is homed and enabled, no latch is active, all targets are within the SW limits, calibration + tare are valid for load steps, FW thresholds are verified and recording can start. | R4 §8 | One test per refusal reason. | T | M4 | Must |
| SW-SEQ-006 | Load-target steps (mandatory) shall use approach + trim: approach with MOVE_UNTIL_LOAD (direction from `pull_dir` and the error sign, speed = step speed, raw stop at F_target − sgn·band, band = max(tol, k_est·v·0.065 s)); trim after MOVE_DONE + 100 ms using the mean of the last 4 samples, Δx = 0.5·(F_target − F̄)/k_est, abs(Δx) ≤ 0.2 mm at 0.2 mm/s, ≤ 10 iterations until abs(error) ≤ tol; position frozen during capture; k_est from the approach OLS slope (clamped), fallback user value. | D-23, R4 §8.4 | pytest R4 TV-C; simulator specimen k = 50 N/mm, k_est = 40 → within tol in ≤ 10 iterations. | T | M4 | Must |
| SW-SEQ-007 | Guards shall abort the step and stop the sequence on: load moving opposite to the motion by > 5 % of target, load drop > 20 % from its running maximum (BREAK_DETECTED), step timeout (0 = 3× planned + 10 s), travel bound; controls shall be start, pause, resume (SW-STOP-004), stop (controlled stop, sequence ends) and abort (HALT). | PO-SW-12, R4 §8.4 | Scenario tests per guard and control. | T | M4 | Must |

#### SW-WIZ / SW-SEQF / SW-SCH — generators, files, chart (PO-SW-13…15)
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-WIZ-001 | Generator wizards shall create: staircase (travel or load; start, end, increment or count; up / up-down; optional return to zero between steps), linear ramp (x0 → x1 at speed, capture during the ramp), cyclic/triangle (min/max travel or load, cycles, speed, dwell), hold/creep-relaxation, and return (travel 0 / home), each with speed, ramp, settle and capture parameters. | PO-SW-13, R4 §8.2 | pytest: generated step lists equal expected lists for each generator. | T | M4 | Must |
| SW-WIZ-002 | Generated steps shall be inserted into the editor (append or replace) and remain editable; the preview updates. | PO-SW-13 | GUI test. | T | M4 | Should |
| SW-SEQF-001 | Sequences shall be saved and recalled as JSON with a schema version (steps, loops, k_est, pull_dir); an invalid file gives an error and leaves the current sequence unchanged. | PO-SW-14 | Round trip identical; corrupt file → current sequence unchanged. | T | M4 | Must |
| SW-SCH-001 | A sequence chart (x = travel mm, y = load N/kgf) shall show the planned path of the defined sequence (travel targets known; load targets placed via k_est), with step labels. | PO-SW-15, R4 §8.2 | Test of the path computation; demonstration. | T, D | M4 | Must |
| SW-SCH-002 | During execution the chart shall show a moving line marker of the actual stage (vertical line at the current travel + point at (x, F)), highlight the active step and overlay the measured trace, updated ≥ 10 Hz. | PO-SW-15 | Demonstration; timing test of marker updates. | D, T | M4 | Must |

#### SW-REP — report
| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| SW-REP-001 | Each test shall produce raw data CSV + JSON metadata (marks, calibration, tare, limits, sequence, board configuration, SW/FW versions, k_est, events) + an HTML summary (marks, F–x and F–t figures, step result table, warnings incl. LOW_SPAN / tension-calibrated push). | D-23, PO-SW-3 | Simulator run generates the three files; HTML opens in a browser; contents checklist. | T | M4 | Must |
| SW-REP-002 | Steady-state extraction per step and loop iteration shall use only samples with VALID = 1, MOVING = 0, setpoint distance equal to its value at window start, no stop/fault flag and t within [t_reached + settle, + capture]; statistics N, mean, std, min, max, SE(N_eff), drift for raw, F and x; INCOMPLETE if N < 0.8·capture·rate; ON_TARGET for load steps (abs(mean − target) ≤ tol). | PO-SW-12, R4 §8.3 | pytest R4 TV-SS (N = 7, mean = 300 047.142857…). | T | M4 | Must |
| SW-REP-003 | The report shall be reproducible offline from the recording, also with another calibration/tare applied. | R4 §7 | Regenerated report equals the original numbers; re-applied tare changes F as expected. | T | M4 | Should |
| SW-REP-004 | Optional 3-point-bend outputs (off by default): with span L, width b, thickness h → σ = 3FL/(2bh²), ε = 6δh/L², E_f = L³·m/(4bh³). | D-23, R4 §10 | pytest R4 TV-D (60 MPa, 0.006, 5000 MPa). | T | M4 | Should |

### 4.7 Non-functional (NFR-)

| ID | Requirement | Source | Acceptance criterion | V | MS | Pri |
|---|---|---|---|---|---|---|
| NFR-001 | Plot refresh ≥ 20 fps (p95 frame interval ≤ 50 ms) with all channels enabled at 80 Hz, recording on, one time view + one X-Y view, 30 s window, on the reference PC. | R3 §0 (8), §6.2 | Perf test over 10 min on the reference PC (simulator source). | T | M3 | Must |
| NFR-002 | GUI STOP click → STOP frame on the wire ≤ 50 ms (p95, 100 clicks) under the NFR-001 load. | R3 §6.12 | Perf test with wire timestamps (virtual serial / sniffer). | T | M3 | Must |
| NFR-003 | Pause/Break key → HALT frame on the wire ≤ 50 ms (p95); key → last PUL edge ≤ 100 ms (p95) on target. | PO-SW-6, D-14, R3 §6.3 | Perf test (simulator); target scope measurement 100 presses. | T, T+H | M3 | Must |
| NFR-004 | Endurance: 1 h at 80 Hz (288 000 frames) with recording and plotting: 0 lost frames attributable to the SW, memory growth ≤ 50 MB after buffers are full; on target over the VCP: 0 sequence gaps and 0 CRC errors. | R3 §6.12 | Soak test with simulator; target soak at the HW gate. | T, T+H | M3 | Must |
| NFR-005 | FW: no dynamic memory allocation after init (no malloc/new in the build, heap unused); stack high-water mark reported in GET_STATUS. | CONV | Map-file inspection (no allocator symbols); twin/target status check. | I, T | M1 | Must |
| NFR-006 | FW main loop: each pass ≤ 1 ms, no blocking > 1 ms; exception: NVM erase/program while idle (≤ 2.5 s); `loop_max_us` reported. | CONV | Target: loop_max_us ≤ 1000 under max load (50 kHz stepping, 80 Hz stream, 20 commands/s) outside NVM operations. | T+H | M2 | Must |
| NFR-007 | FW ISR budgets: step ISR ≤ 2 µs worst case, step-generation CPU load ≤ 15 % at 50 kHz, IRQ-masked windows ≤ 1 µs, E-stop/STOP ISR ≤ 1 µs. | R4 §1.8 | DWT cycle-counter measurement on target. | T+H | M2 | Must |
| NFR-008 | FW command response time ≤ 10 ms after the last command byte (SAVE/LOAD/DEFAULTS ≤ 2.5 s). | R3 §1.5 | Twin timing; target sample of 1000 commands. | T, T+H | M1 | Must |

---

## 5. Configuration parameters

### 5.1 FW parameters required in `params.yaml`

Names are proposals; the Integrator fixes names/ids/types. "Range" is the minimum range the dictionary shall
allow; "Moving" = settable while moving. Counts assume the nominal cell sensitivity (§10 A-02).

| Group | Parameter | Type | Unit | Range (at least) | Default | Moving | Source | Req |
|---|---|---|---|---|---|---|---|---|
| afe | `afe.gain_channel` | enum | — | A128, A64, B32 | A128 | no | D-04 | FW-PAR-001 |
| afe | `afe.rate_sps` | enum | SPS | 10, 80 | 80 | no | D-04, D-21 | FW-PAR-001 |
| afe | `afe.rate_tol_pct` | u8 | % | 5…50 | 20 | yes | R2 §4.2 | FW-PAR-001 |
| afe | `afe.settle_discard` | u8 | samples | 0…20 | 4 | yes | R2 §4.1 | FW-PAR-001 |
| afe | `afe.timeout_ms` | u16 | ms | 20…1000 | 100 | yes | R4 §4.2 | FW-PAR-001 |
| motion | `motion.steps_per_mm` | f32 | steps/mm | ≥ 100…10 000 (proposed 1…100 000) | 160 (ASSUMED, D-27) | no | PO-FW-4, D-19, D-27 | FW-PAR-002 |
| motion | `motion.pul_invert`, `motion.dir_invert` | bool | — | 0/1 | 0 (DIR polarity set at the HW gate; DIP SW5 = negative direction, D-27) | no | D-17, D-27, R2 §1.4 | FW-PAR-002 |
| motion | `motion.ena_invert` | bool | — | 0/1 | 0 (LED current = disabled) | no | D-13, R2 §1.5 | FW-PAR-002 |
| motion | `motion.pulse_high_ns` | u32 | ns | 2 500…100 000 | 10 000 | no | D-16 | FW-PAR-002 |
| motion | `motion.pulse_low_min_ns` | u32 | ns | 2 500…100 000 | 10 000 | no | D-16 | FW-PAR-002 |
| motion | `motion.max_step_rate_hz` | u32 | Hz | 100…200 000 | 50 000 | no | D-16 | FW-PAR-002 |
| motion | `motion.dir_setup_us` | u16 | µs | 5…1000 | 20 | no | D-16 | FW-PAR-002 |
| motion | `motion.ena_settle_ms` | u16 | ms | 0…2000 | 200 | no | R2 §1.4 | FW-PAR-002 |
| motion | `motion.v_max_um_s` | u32 | µm/s | 1…250 000 | 10 000 | no | D-19 | FW-PAR-003 |
| motion | `motion.a_max_um_s2` | u32 | µm/s² | 1 000…10 000 000 | 100 000 | no | R4 §2.1 | FW-PAR-003 |
| motion | `motion.a_stop_um_s2` | u32 | µm/s² | 10 000…10 000 000 | 1 000 000 | no | R4 §4.2 | FW-PAR-003 |
| motion | `motion.v_unhomed_um_s` | u32 | µm/s | 1…20 000 | 2 000 | no | R4 §3.2 | FW-PAR-003 |
| motion | `motion.jog_timeout_ms` | u16 | ms | 50…1000 | 250 | yes | R1 §10.3 (ASSUMED value) | FW-PAR-003 |
| limits | `limits.soft_min_um` | i32 | µm | −10 000…400 000 | 500 | no | R4 §3.2 | FW-PAR-004 |
| limits | `limits.soft_max_um` | i32 | µm | 0…400 000 | 290 000 | no | D-19 (ASSUMED) | FW-PAR-004 |
| home | `home.switch` | enum | — | START, END | START | no | D-18 | FW-PAR-004 |
| home | `home.v_fast_um_s` / `home.v_slow_um_s` | u32 | µm/s | 10…20 000 | 5 000 / 500 | no | R4 §3.1 | FW-PAR-004 |
| home | `home.a_um_s2` | u32 | µm/s² | 1 000…1 000 000 | 50 000 | no | R4 §3.1 | FW-PAR-004 |
| home | `home.backoff_um` / `home.offset_um` | u32 | µm | 0…20 000 | 2 000 / 1 000 | no | R4 §3.1 | FW-PAR-004 |
| home | `home.max_travel_um` | u32 | µm | 1 000…500 000 | 360 000 | no | R4 §3.1, D-19 | FW-PAR-004 |
| home | `home.max_load_raw` | i32 | counts | 0…7 151 121 | 322 123 (5 % FS) | no | D-15 | FW-PAR-005 |
| safety | `safety.load_raw_max` / `safety.load_raw_min` | i32 | counts | −7 151 121…+7 151 121 | +7 022 271 / −7 022 271 | yes | D-12 | FW-PAR-005 |
| safety | `safety.load_trip_samples` | u8 | samples | 1…4 | 1 | yes | R4 §4.4 | FW-PAR-005 |
| safety | `safety.load_regrow_raw` | i32 | counts | 0…1 288 490 | 128 849 (2 % FS) | yes | R4 §4.4 | FW-PAR-005 |
| safety | `safety.zero_raw` | i32 | counts | −8 388 608…8 388 607 | 0 (PC writes tare_raw) | yes | D-15, R4 §7 | FW-PAR-005 |
| safety | `safety.release_band_raw` | i32 | counts | 0…644 245 | 128 849 (2 % FS) | yes | D-15 | FW-PAR-005 |
| safety | `safety.idle_disable_s` | u16 | s | 0…65 535 (0 = never) | 600 | yes | D-15 | FW-PAR-005 |
| safety | `safety.link_timeout_ms` | u16 | ms | 200…5 000 | 1 000 | yes | D-15 | FW-PAR-005 |
| io | `input.release_ms` | u8 | ms | 5…200 | 20 | no | R4 §3.4 | FW-PAR-006 |
| io | `input.estop_release_ms` | u16 | ms | 50…2 000 | 100 | no | R4 §4.2 | FW-PAR-006 |
| io | `io.stop_active_level` / `io.pause_active_level` | enum | — | open-active, closed-active | open-active (NC) / closed-active (NO) (ASSUMED, R5) | no | D-26 | FW-PAR-006 |
| io | `drv.alm_active_level` / `drv.pend_active_level` | enum | — | high, low | high (ALM open = alarm) / high (PEND high-impedance = active) | no | D-16, R2 §1.7 | FW-PAR-006 |
| stream | `stream.fallback_hz` | u8 | Hz | 1…80 | 10 | yes | R3 §1.6 | FW-PAR-006 |

Hard rules (FW-checked): `soft_min_um < soft_max_um`; `load_raw_min < load_raw_max`;
`1e9 / max_step_rate_hz ≥ pulse_high_ns + pulse_low_min_ns`. Speeds are not rule-coupled to steps/mm: the FW caps the effective speed at `max_step_rate_hz`·1000/steps_per_mm (FW-MOT-009).
**No** polarity parameter exists for the E-stop sense and limit inputs (SAF-FW-007).

### 5.2 SW settings (session/config files, not in `params.yaml`)

SW travel/load limits and warning levels (SW-LIM), FW load-limit level, tare window (10 s), calibration
pre-settle/capture (2 s / 10 s), momentary-sample window (1 s), `pull_dir` (+1/−1), k_est default, trim
parameters (Kp 0.5, 0.2 mm, 0.2 mm/s, 10 iterations), display unit (N/kgf), optional 3-point-bend geometry.

## 6. Timing budget summary

| Path | Budget | Req |
|---|---|---|
| E-stop sense edge at MCU pin → last PUL edge | ≤ 100 µs (normative); ENA disabled ≤ 1 ms | SAF-FW-005 |
| E-stop button → driver de-energised (hardware) | ≤ 100 ms, independent of MCU | SYS-006 |
| Limit switch / physical STOP button edge → last PUL edge | ≤ 200 µs | SAF-FW-002, SAF-FW-022 |
| HX711 data-ready of deciding sample → last PUL edge (FW load limit) | ≤ 200 µs (= 1 sample + processing; from the physical force crossing ≈ 12.5 ms × trip samples + HX711 filter delay ≈ 65 ms, R4 §4.6) | SAF-FW-002, SAF-FW-008 |
| STOP/HALT command last byte → last PUL edge | ≤ 2 ms | SAF-FW-002 |
| Trigger → start of controlled deceleration (link wdg, PAUSE, dead-man) | ≤ 2 ms | SAF-FW-003 |
| FW hang while moving → PUL stops | ≤ 100 ms | SAF-FW-019 |
| GUI STOP click → STOP frame on the wire | ≤ 50 ms p95 | NFR-002 |
| Pause/Break key → HALT frame / → last PUL edge | ≤ 50 ms / ≤ 100 ms p95 | NFR-003 |
| Violating DATA frame received → SW STOP frame on the wire | ≤ 50 ms p95 | SAF-SW-001 |
| HX711 data-ready → DATA frame transmission start | ≤ 2 ms | FW-STR-002 |
| Link silence while moving → controlled stop | 1000 ms (+ ≤ 2 ms) | SAF-FW-015 |
| Jog refresh missing → controlled stop | 250 ms (+ ≤ 2 ms) | SAF-FW-016 |

---

## 7. PO request coverage (`Initial_specs.txt`)

The SW list in `Initial_specs.txt` is numbered 1–8, then 12–15: **items SW-9…SW-11 do not exist** (see OI-08).
The unnumbered paragraphs under SW item 8 are referenced as PO-SW-8.x below.

| PO item | Content (short) | Requirement IDs |
|---|---|---|
| Intro | Stefan as reference for pinout/board init; STEP/DIR/EN to HBS86H; 86HS2140; Keli DEF 200 kg; HX711 | SYS-001, SYS-005, SYS-007, FW-PLT-001, FW-MOT-001, FW-MOT-008 |
| PO-FW-1 | One HX711 AFE, 80 Hz, gain 128; drivers from Thrust_Stand_HAW (read-only) | FW-AFE-001…005, FW-PAR-001, SYS-010 |
| PO-FW-2 | Start/stop (limit) switches and zeroing mechanism | FW-SW-001, SAF-FW-007, SAF-FW-013, SAF-FW-014, FW-HOM-001…003, FW-PAR-004, SW-MAN-006 |
| PO-FW-3 | NC button immediately stops the motor | SYS-006, SAF-FW-005, SAF-FW-006, SAF-FW-007, FW-SW-002 (+ physical STOP/BREAK: SAF-FW-022, FW-SW-003) |
| PO-FW-4 | AFE configuration settings; steps-per-mm calibration parameter | FW-AFE-002, FW-MOT-009, FW-PAR-001, FW-PAR-002, FW-CFG-001 |
| PO-FW-5 | Command reading all configurations | FW-CFG-002, FW-CFG-004, IF-012 |
| PO-FW-6 | Command setting each parameter | FW-CFG-003, FW-NVM-001…003, IF-012 |
| PO-FW-7 | Command set/reset data validity flag | FW-CMD-002, FW-STR-003, SAF-FW-001, SW-MAN-006, SW-SEQ-004 |
| PO-FW-8 | Commands start/stop data streaming | FW-STR-001, SW-ACQ-001, IF-012 |
| PO-FW-9 | UART 921600, no flow control, CRC16 on all communication | IF-002, IF-004 |
| PO-FW-10 | Data block starts with separator, binary, contains CRC-16 | IF-003, IF-004, IF-006 |
| PO-FW-11 | Fields: time mark, payload version, flag byte (validity), AFE 32 bit, setpoint distance, CRC-16 | IF-006, FW-STR-003, FW-TIM-001, FW-AFE-005 |
| PO-SW-1 | Board config: read, display, save/recall file, change, write, validate | SW-CFG-001…004, SW-PLT-003 |
| PO-SW-2 | Safety limits for travel and load | SW-LIM-001…003, SAF-SW-001, SAF-SW-002, SAF-FW-008…011 |
| PO-SW-3 | Report marks (element, number) + custom | SW-META-001, SW-META-002, SW-REP-001 |
| PO-SW-4 | Realtime display, per-value on/off incl. calculated, detachable | SW-RT-001…005, NFR-001 |
| PO-SW-5 | Manual tab: slider, travel entry, ±0.1/1/10 mm, immediate stop | SW-MAN-001…006, SW-STOP-001, FW-MOT-004, FW-MOT-005, FW-MOT-007 |
| PO-SW-6 | NC safety knob stops motor; Pause/Break key stops motor/script from anywhere | SYS-006, SAF-FW-005, SW-STOP-002, SW-STOP-003, FW-MOT-007, NFR-003 (+ D-26: SAF-FW-022/023, SW-STOP-004) |
| PO-SW-7 | Buttons: stream start/stop, record to file, momentary sample | SW-ACQ-001…004 |
| PO-SW-8 / 8.WIZ | Calibration & tare tab; wizard with step description + Continue | SW-CAL-001 |
| PO-SW-8.TC1 | Travel cal step 1: 10 mm, operator enters measured distance, recompute steps/mm, send to board | SW-CAL-002, SW-CAL-003, SW-CAL-004 |
| PO-SW-8.TC2 | Travel cal step 2: 50 mm more, entry, fine-tune steps/mm | SW-CAL-002, SW-CAL-003, SW-CAL-004 |
| PO-SW-8.PC1 | Pull cal step 1: remove load, reference level | SW-CAL-005, SW-CAL-006 |
| PO-SW-8.PC2 | Pull cal step 2: known weight, entry, 10 s average | SW-CAL-005, SW-CAL-006 |
| PO-SW-8.PC3 | Pull cal step 3: bigger known weight, entry, 10 s average | SW-CAL-005, SW-CAL-006, SW-CAL-008 |
| PO-SW-8.FIT | Linearity check, K and B, used for scaling charts from raw | SW-CAL-007, SW-RT-004 |
| PO-SW-8.FILE | Calibration saved to file, loaded by default | SW-CAL-009 |
| PO-SW-8.TARE | Tare: capture ~10 s, average, offset compensation (pull) | SW-TARE-002, SW-TARE-003, SAF-SW-002 |
| PO-SW-8.TARE-ANY | Tare button available from any tab | SW-TARE-001 |
| PO-SW-9…11 | (not present in `Initial_specs.txt`) | — (OI-08) |
| PO-SW-12 | Steps: time, target travel or load, speed, ramping, settling, capture; whole-time capture, report only steady data; looping; controls | SW-SEQ-001…007, SW-STOP-004, SW-ACQ-002, SW-REP-002, FW-MOT-006 |
| PO-SW-13 | Wizards generating steps, ramps, other sequences | SW-WIZ-001, SW-WIZ-002 |
| PO-SW-14 | Sequences saved and recalled | SW-SEQF-001 |
| PO-SW-15 | Chart x = travel, y = load; moving line marker of actual stage | SW-SCH-001, SW-SCH-002, SW-RT-003 |
| Process paragraphs | Spec-driven development, roles, folders | `PROCESS.md` (D-01), SYS-007, SYS-010 |

All numbered PO items are covered.

## 8. Deferred / out of scope (this release)

| Item | Reason / where it goes |
|---|---|
| Automatic reaction to driver ALM (stop, latch DRIVER_FAULT, ENA drop, HOMED cleared) | D-16: read & report only now; planned for a later release (see OI-06). |
| ALM reset procedure (ENA toggle vs power cycle) | Unknown per R2 §1.8; R5 / bench test. |
| 5 V buffer interface board (74AHCT125 class) | Optional per D-17; R5 proposal pending. |
| Separate push (compression) calibration | D-20: one (tension) calibration; push forces reported as "tension-calibrated". |
| Continuous force control / force hold during capture (`trim_during_hold`), FW-side force loop | R4 §8.4, Q-R4-10: approach + trim only. |
| Machine-compliance correction channel (C_m) and data-ready skew correction | R4 §9–10: optional later. |
| Resistor-coded switch loops (short-circuit detection) | R4 §3.5: optional HW upgrade. |
| Independent hardware pulse counter cross-check (TIM3) | R4 §1.4 (d): optional diagnostics. |
| PEND-based settle detection | PEND reported only (D-16). |
| HX711 external clock, more AFEs, other link baud rates, Nucleo B1 button | Not requested. |
| PC↔device clock synchronisation beyond event timestamp pairs | Not needed (SET_VALID uses device time, R4 §9). |

## 9. Open items

| ID | Item | Proposed resolution |
|---|---|---|
| OI-01 | **Calibration span:** weights 1 kg + 10 kg reach only 5 % FS → forces up to 200 kg are extrapolated 20×; 1 kg is below R4's rule m1 ≥ 2 % FS; NL thresholds in % of a 98 N span (0.1 % ≈ 10 g) are comparable to the cell's hysteresis (60 g FS), so WARN results are likely. | Recommend a heavier reference (≥ 50 kg = 25 % FS, ideally ≥ 100 kg) or a reference force gauge. Until then SRS accepts the weights with warnings (SW-CAL-006 m1 rule as warning, SW-CAL-008 LOW_SPAN + "extrapolated" marking). PO to decide. |
| OI-02 | **Direct-drive assumption** (D-19): the nominal steps/mm assumes the motor coupled 1:1 to the 5 mm screw (no belt/gear). | PO to confirm the coupling; the travel calibration measures the real value; the expected-value check (SW-CAL-003) flags a ratio error. |
| OI-03 | **Link loss: D-10 vs D-15.** D-10 (amended) lists link loss among stops with "stop pulses immediately"; D-15 says link watchdog → **controlled** stop. | **Closed (Orchestrator 2026-10-03):** D-10 reworded — link loss and PAUSE use a controlled stop (D-15, D-26), still holding. |
| OI-04 | **"Motor disabled by default" (CLAUDE.md) vs D-13** (driver keeps holding through MCU reset). | **Closed (Orchestrator 2026-10-03):** CLAUDE.md now says "motion disabled by default; ENA follows D-13". |
| OI-05 | **3.3 V direct drive, common cathode (D-17):** 6.3–7.4 mA opto current vs 7 mA minimum (R2 §1.3). Pulses are proven (D-24), but ENA was never driven: the *disabled* state (LED current) used by E-stop, idle disable and DISABLE may be unreliable. | Verify ENA disable in the SYS-009 check list; if marginal, adopt the R5 buffer board. Personnel safety does not depend on ENA (E-stop removes driver power, SYS-006). |
| OI-06 | **ALM without reaction (D-16):** a following-error alarm leaves the motor unpowered (specimen can spring back) while the FW keeps counting/pulsing. | SW shows an ALM warning (SAF-SW-005); recommend activating the deferred ALM reaction right after the first hardware gate. |
| OI-07 | **FW load-threshold cap (SAF-FW-010)** relies on the nominal 3.0 mV/V sensitivity and ±1 % FS zero balance (A-02); HX711 gain tolerance is not specified. | After the first calibration compare measured counts/kg with 32 212; if the deviation is > 2 %, re-derive the dictionary range/defaults (dictionary version bump). |
| OI-08 | **PO numbering:** `Initial_specs.txt` SW list jumps from item 8 to 12 (no items 9–11). | PO to confirm nothing is missing; SRS maps the paragraphs under item 8 as PO-SW-8.x. |
| OI-09 | **E-stop power-removal circuit** (SYS-006) and **contact types of the STOP/BREAK and PAUSE buttons** (D-26: NC recommended for STOP) are not designed yet. With driver power removed the ball screw may be back-driven by the specimen load. | R5 to propose circuit and contact types; defaults in Table 5.1 (STOP NC, PAUSE NO) are ASSUMED until then. |
| OI-10 | **Setpoint distance semantics** (A-03): commanded position at sample time vs final target of the move. | SRS uses the commanded position (exact x for the travel–load chart); the target is known to the PC. If the PO wants the target too, the Integrator adds a field (payload minor version). |
| OI-11 | **921 600 Bd over the ST-LINK/V2-1 VCP** is not officially specified (R2 RK-5). | SYS-009 soak test; fallback: external USB-UART on USART1/6 at the same baud. |
| OI-12 | **D-22 still states "driver from a separate 24 V supply"**, contradicting amended D-16 (48 V DC). | **Closed (Orchestrator 2026-10-03):** D-22 corrected to 48 V (D-16). |
| OI-13 | **Driver DIP setting (D-27, open):** the PO photo decodes to 800 pulses/rev (Leadshine table → 160 steps/mm) or 6400 pulses/rev (JMC-clone table → 1280 steps/mm); switch ON-direction and brand label unconfirmed; **SW6 = ON selects the 4 N·m motor type while the motor is 8.2 N·m class** (wrong current/tuning, risk of following-error alarms). 800 pulses/rev gives 6.25 µm per step. | PO to confirm label, switch ON-direction and SW6, and decide whether to change to 4000 pulses/rev (800 steps/mm, R2 §1.6 recommendation). SRS defaults to 160 steps/mm — the lowest candidate, so the first calibration moves never exceed the commanded distance — and the travel wizard measures the real value (SW-CAL-003 allows the large first correction with confirmation). |

## 10. Assumptions

| ID | Assumption (ASSUMED) | Source | Used in |
|---|---|---|---|
| A-01 | Motor directly coupled to the 5 mm lead screw (1:1); DIP = 800 pulses/rev (Leadshine reading of the PO photo) → nominal 160 steps/mm (the JMC-clone reading would be 6400 pulses/rev → 1280 steps/mm). | D-19, D-27 | SYS-005, FW-MOT-009, Table 5.1 |
| A-02 | Load cell 3.0 mV/V nominal → 32 212 counts/kg, FS = 6 442 451 counts, zero balance ±1 % FS (±64 425 counts), saturation ≈ 130 % FS; HX711 gain error negligible. | R2 §3, §4.4 | SAF-FW-010, Table 5.1 |
| A-03 | "Setpoint distance" = commanded profile position latched at HX711 data-ready. | R4 §2.3 | IF-006, FW-AFE-005 |
| A-04 | HBS86H ENA: no LED current = enabled, LED current = disabled; after re-enable the driver takes the current encoder position (no jump). | R2 §1.5 | SAF-FW-018, FW-MOT-008 |
| A-05 | Driver needs ≈ 200 ms after enable before the first pulse. | R2 §1.4 | FW-MOT-008 |
| A-06 | Jog dead-man refresh timeout 250 ms (PC refresh ≤ 100 ms). | R1 §10.3, R4 §2.1 | SAF-FW-016, SW-MAN-004 |
| A-07 | Soft limits 0.5 / 290 mm, home offset 1 mm, back-off 2 mm, max homing travel 360 mm (1.2 × 300 mm stroke); actual end-stop positions unknown. | R4 §3, D-19 | Table 5.1, SYS-004 |
| A-08 | Physical STOP/BREAK button NC (open = active), PAUSE button NO (closed = active), momentary; the physical STOP button latches HALT (clear = released + PC HALT_CLEAR). | D-26 (NC recommended for STOP), R5 pending | FW-SW-003, SAF-FW-022, SAF-FW-023 |
| A-09 | ALM output configured "conducting = OK" (open/high = alarm, fail-safe); PEND active at high impedance. | R2 §1.7 (Leadshine default) | Table 5.1 |
| A-10 | Reference PC = the PO's Windows 10 lab PC. | — | NFR-001…004 |
| A-11 | HSE bypass 8 MHz from the ST-LINK MCO available (factory solder bridges). | R2 §5.1, R1 §9.2 | FW-PLT-002 |
| A-12 | Limit micro-switch repeatability ±5…20 µm. | R4 §3.1 | FW-HOM-003 |
| A-13 | HX711 filter/settling delay ≈ 25–50 ms at 80 SPS → force-crossing-to-stop ≈ 65 ms. | R4 §4.6, R2 §4.1 | §6, SAF-SW-006 |
| A-14 | IWDG can be shortened to ≤ 100 ms during motion and extended only for idle flash operations. | R2 §5.5 | SAF-FW-019 |
| A-15 | SW load-limit defaults ±100 % FS trip / 90 % warning, below the FW 110 % FS level. | R4 §4.4 (layering) | SW-LIM-002 |
| A-16 | Fallback frame rate 10 Hz while the AFE is stale. | R3 §1.6 (a) | FW-STR-005 |
| A-17 | The installed HX711 board runs at 3.3 V with RATE wired to a GPIO (board modified per D-21); noise ≈ +40 % vs 5 V supply accepted. | D-21, R2 §4.3 | FW-AFE-002 |
| A-18 | 921 600 Bd works over the ST-LINK/V2-1 VCP. | R2 §5.2 | IF-002 |

## 11. Requirement count

| Group | Count |
|---|---|
| SYS | 10 |
| SAF-FW | 23 |
| SAF-SW | 6 |
| FW (PLT 2, AFE 5, MOT 9, HOM 3, SW 4, CFG 4, NVM 3, CMD 4, STR 6, TIM 1, PAR 6) | 47 |
| IF | 12 |
| SW (PLT 3, CFG 4, LIM 3, META 2, RT 5, MAN 6, STOP 4, ACQ 4, CAL 9, TARE 3, SEQ 7, WIZ 2, SEQF 1, SCH 2, REP 4) | 59 |
| NFR | 8 |
| **Total** | **165** (Must 157, Should 8, Could 0) |

## 12. Change history

| Version | Date | Change |
|---|---|---|
| 0.2 | 2026-10-03 | Orchestrator review: SW-STOP-002 HALT retry ends on ACK (works with stream off); SW-STOP-003 FW-reported HALT/ESTOP from any source terminates the sequence; OI-03/04/12 closed. |
| 0.1 | 2026-10-03 | Initial draft for PO review: requirements from Initial_specs, D-01…D-27 (D-16 amended to 48 V DC; D-17 common-cathode; D-19 5 mm lead confirmed; D-26 physical STOP/BREAK + PAUSE buttons; D-27 DIP 800 pulses/rev → default 160 steps/mm, open), R1–R4. |
