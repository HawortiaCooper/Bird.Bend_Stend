# Bird Bend Stand — Wiring, supplies, grounding and hardware check list

| Item | Value |
|---|---|
| Doc | `01_HW/wiring.md` |
| Version | **0.3** — final P1 draft, aligned to SRS v0.3 / ICD v0.3(+v0.4) / D-29…D-33 and the Validator E review (`02_FW/docs/FW_test_plan.md`) |
| Date | 2026-10-03 |
| Owner | Implementer A (FW) |
| Binding inputs | SRS v0.3 SYS-001, SYS-005…SYS-009, SYS-011, SAF-FW-005/007, FW-SW-001…004; DECISIONS D-11, D-13, **D-16 (driver = PFDE HBS86H, Leadshine-compatible clone, 24–100 VDC, control inputs 5–24 V)**, D-17, D-18, D-20, D-21, D-22, D-24, D-26, **D-27 (DIP/label)**, **D-28 (R5 defaults accepted)**, **D-29 (b START-only homing, c DRV_PWR / K1_WELDED)**; SRS v0.3 SAF-FW-024/025/026, FW-SW-005, SYS-011; R2 §1, §4, §6; **R5 §1.3 Option A (E-stop power removal), §2.4 (PSU), §3.1 (ALM/PEND), §4 (direct 3.3 V drive), §5.2 (filters, interface board Option A), §5.5 (buttons)** |
| Related | `01_HW/pinout.md` (pins, NVIC), `02_FW/docs/FW_design.md` |
| Status | Engineering proposal for a lab stand, **not a certified safety design**; the risk assessment (EN ISO 12100 / ISO 13849-1) stays with the PO (R5 header). The R5 defaults (E-stop Option A, horizontal axis/no brake, SDR-480-48, perfboard buffer → SN74ACT244 shield, STOP NC / PAUSE NO, pin map, TRIP provision) are **accepted by the PO (D-28)**; items still marked **(PO)** concern the driver DIP setup (D-27). |

Signal pins are given by name; the pin/connector numbers are in `pinout.md` §1.1. All values marked ASSUMED come from R2/R5 and are verified at the hardware gate (§11).

---

## 1. Overview

```
 230 VAC ─[Q0 MCB]─► 48 V PSU (SDR-480-48, PO) ─+48V─[F1 T10A]─[K1 contactor, 2 poles in series]─► HBS86H +Vdc
                                                 0V ──────────────────────────────────────────────► HBS86H GND
                         E-stop S0 (2×NC) ─ NC1 in the K1 coil/self-hold path (hardware, cat. 0, §7)
                                          └ NC2 ───────────────────────────► E-stop sense (PA10)
                         K1 aux NO ───────────────────────────────────────► DRV_PWR sense (PA7, required)

 PC (USB) ═══ ST-LINK/V2-1 ═══ NUCLEO-F446RE (USART2 VCP 921600, powered from USB or DC-DC 5 V, §8)
                                 │  PUL/DIR/ENA (3.3 V push-pull, common cathode, D-17) ─► HBS86H opto inputs (§2)
                                 │  ALM/PEND ◄─ HBS86H OC opto outputs (§3)
                                 │  DOUT/SCK/RATE ─ HX711 module @ 3.3 V ─ Keli DEF 200 kg (§4)
                                 │  START/END limit NC (§5) · STOP/BREAK NC · PAUSE NO (§6)
                                 └─ GND = single logic ground (opto return side only, never the 48 V GND / PE) (§9)
 HBS86H ── motor phases A+/A-/B+/B- (shielded) ── 86HS2140 ── encoder cable (separate) ── HBS86H
```

Wiring stages:
- **Stage 1 — bring-up only (D-17, D-24, D-28):** direct 3.3 V MCU drive of the driver inputs, input conditioning on a perfboard (RC + pull-ups). **The PFDE HBS86H label rates PUL/DIR/ENA for 5–24 V (D-16/D-27); 3.3 V is below that rating** — it worked on Stefan's stand (D-24) but has no guaranteed margin.
- **Stage 2 — required before calibration and test campaigns (D-28):** interface shield with the **SN74ACT244 5 V buffer** (R5 §5.2 Option A) carrying all input cells. Same pins, same common-cathode driver wiring; FW unchanged. (Interim: the same buffer on the bring-up perfboard.)

---

## 2. Driver control inputs — PUL / DIR / ENA (current: direct 3.3 V, common cathode, D-17)

```
 NUCLEO                                     PFDE HBS86H "Signal" terminals, label order PUL+ PUL− DIR+ DIR− ENA+ ENA−
                                            (opto LEDs, ≈ 270 Ω inside per the Leadshine drawing — ASSUMED for the clone)
 PA0 PUL (TIM2_CH1, push-pull 3.3 V) ──────────────────────────► PUL+ ─[270R]─[LED]─ PUL- ─┐
 PA1 DIR (GPIO push-pull)            ──────────────────────────► DIR+ ─[270R]─[LED]─ DIR- ─┤
 PA4 ENA (GPIO push-pull, TC pin!)   ──────────────────────────► ENA+ ─[270R]─[LED]─ ENA- ─┤
 GND (Nucleo) ◄────────────────────────────────────────────────────────────────────────────┘
 optional: 10 kΩ pull-down at the driver end of PUL+ and DIR+ to GND (defines the node during MCU reset, R5 §4.2)
 cable: twisted pairs PUL+/PUL−, DIR+/DIR−, ENA+/ENA−, ≤ 1–2 m, ≥ 10 cm from motor / 48 V wires, cross at 90°
```

| Point | Rule | Source |
|---|---|---|
| Polarity | MCU pin **high = LED current**. PUL: pulse = LED current (`motion.pul_invert` = 0). ENA: **LED current = driver disabled**, no current = enabled (Leadshine behaviour, ASSUMED for the PFDE clone → check-list C-07; `motion.ena_invert` = 0). DIR: level set at the HW gate (`motion.dir_invert`; DIP SW5 = motor direction CCW/CW, D-27). | R2 §1.5, A-04, D-27 |
| Reset / boot | MCU pins are Hi-Z from reset until the FW drives them (≤ 10 ms) → **no LED current** → PUL idle, ENA = **enabled (holding)** — this is D-13 (driver holds through MCU reset). | D-13, R5 §4.2 |
| Input rating | **Label: 5–24 V** (D-16/D-27). Direct 3.3 V gives ≈ 6–7 mA typical, **4.3 mA worst case** (R5 §4.1) → **bring-up only**; a dropped pulse is not detected (and in the current open-loop setting not even corrected by the driver), so the FW position drifts silently. Check-list C-06 at bring-up; **Stage 2 buffer mandatory before calibration (D-28, SYS-011)**. | R5 §4.1–4.2, OI-05, D-28, SYS-011 |
| PA4 | **TC pin (not 5 V tolerant)**: push-pull only; never open-drain to a 5 V anode. | R5 §6 finding 2 |
| Ground | PUL−/DIR−/ENA− go to the **Nucleo GND**; the opto barrier keeps the 48 V side isolated (≥ 500 MΩ). **Never connect Nucleo GND to the driver's 48 V GND or PE.** | R2 §6.1, R5 §4.2 |
| GPIO speed | "low" (slew-limited) on PUL/DIR/ENA — 10 µs pulses need no fast edges; less EMI. | R5 §8 |
| Timing defaults | PUL high ≥ 10 µs, low ≥ 10 µs, ≤ 50 kHz, DIR setup ≥ 20 µs, **ENA / driver power → first pulse ≥ 500 ms** (D-28; R5 corrects R2's 200 ms). | D-16, R5 §1.1 |

**Stage 2 interface shield (R5 §5.2 Option A, required before calibration per D-28; ASSUMED part values):** SN74ACT244 at 5 V (Nucleo 5V pin), inputs from PA0/PA1/PA4 with 10 kΩ pull-downs (outputs low during MCU reset → same safe state as today), outputs to PUL+/DIR+/ENA+, PUL−/DIR−/ENA− still to GND → ≈ 11–13 mA per LED. Unused buffer inputs to GND, 100 nF + 10 µF at U1. The shield also carries the input cells of §3, §5, §6, §7, ESD arrays (TPD4E05U06 class) and pluggable terminals J1 driver (10-pole), J2 limits (4-pole), J3 panel (8-pole), J4 HX711 (6-pin). Every signal is on the Arduino headers (pin map R5 §6.1).

---

## 3. ALM / PEND inputs (read and report only, D-16)

```
 3V3 ─[4.7k]─┬─[1k]─┬─► PA8 (ALM)   / PA9 (PEND)        τ ≈ 5.7 µs
             │      [1nF]
 ALM+ ───────┘       │                                  ALM− / PEND− ──► Nucleo GND (opto return, isolated from 48 V)
                    GND
```
- Label terminal order "OUT": PEND+, PEND−, ALM+, ALM−.
- Leadshine default (**keep it, do not invert**): ALM low impedance = OK **and powered**; high impedance = fault, driver unpowered, or wire broken → fail-safe reading. PEND high impedance = in position (also high when unpowered → only meaningful with ALM = OK). The PFDE clone's default levels are **not documented → measure at the HW gate (C-16)**; `drv.alm_active_level` / `drv.pend_active_level` stay parameters. (R5 §3.1)
- **ALM semantics depend on the motor mode (D-27):** in closed-loop mode ALM includes the position-following-error alarm; in the **current open-loop setting (SW7/SW8 = off/off)** the encoder is unused, so ALM can only signal over-current / over-voltage / unpowered, and missed steps are silent.
- D-28 / SAF-FW-026: in release 1 the FW refuses **new** motion starts while ALM is active **and** DRV_PWR reports driver power. Running moves, including speed refreshes of a running jog, are not stopped or affected by ALM.
- D-27: the FW **never** uses ALM (or PEND) as a step-loss indicator. In the open-loop setting no such signal exists; the only plausibility check is HOME_DRIFT at re-homing (FW-HOM-004).
- External 4.7 kΩ pull-ups are mandatory (internal 30–50 kΩ is too weak for a cable). ≈ 0.7 mA per output, far below the 20 mA rating.

---

## 4. HX711 module and load cell (D-04, D-20, D-21, A-17)

```
 Keli DEF 200 kg (4-wire, 5 m cable, do not shorten)        HX711 module (logic + analog at 3.3 V, A-17)
   Red    EXC+ ─────────────────────────────────────────────► E+
   Black  EXC− ─────────────────────────────────────────────► E−
   Green  SIG+ ─────────────────────────────────────────────► A+
   White  SIG− ─────────────────────────────────────────────► A−
   Yellow shield ───────────────────────────────────────────► GND (HX711 end only)
                                                              VCC / DVDD ◄── Nucleo 3V3   GND ◄── Nucleo GND
 PB4 (DOUT, input) ◄──────────────────────────────────────── DOUT
 PB10 (PD_SCK, output, init low) ──[33 Ω]─────────────────► PD_SCK
 PB5 (RATE, output) ──────────────────────────────────────── RATE (pin 15 lifted from GND / 0 Ω selector moved, D-21)
                                         10 kΩ pull-up RATE → DVDD (80 SPS while the MCU is in reset, R5 §5.2)
```
- **3.3 V supply** for the whole module (A-17): DOUT then swings 0–3.3 V and SCK/RATE from the MCU meet the HX711 input levels; AVDD ≈ 3.0 V costs ≈ +40 % noise in grams (accepted, A-17). A module with a separate analog VCC (5 V) and digital VDD (3.3 V) is better if available (R2 §4.3). **Never run DVDD at 5 V** with the direct MCU connection (SCK VIH not met).
- Sign: S-cells are normally positive in tension; if inverted, swap Green/White **or** let the calibration carry K < 0 (SRS SW-CAL-007 accepts negative K).
- Keep SCK/DOUT/RATE wires ≤ 20 cm, the module close to the Nucleo (same enclosure, preferably metal), the cell cable ≥ 10–20 cm from motor/48 V cables, crossings at 90° (R2 §6.5).
- RATE modification (D-21): on generic green boards lift pin 15 (RATE) from the GND trace or move the 0 Ω selector, then wire it to PB5. The FW always measures the real rate and flags a mismatch (FW-AFE-004), so a missing modification is detected.

---

## 5. Limit switches START / END (mechanical NC, D-18)

```
 3V3 ─[1k]─┬─[1k]─┬─► PB0 (START) / PC1 (END; fallback PB1)        τ_rise = (1k+1k)·47n ≈ 94 µs, τ_fall ≈ 47 µs
           │      [47n]
 switch NC ┘       │       switch common ──► GND (Nucleo)
                  GND
```
- **NC to GND, pull-up:** closed = low = OK; open **or wire broken / connector unplugged** = high = limit active (fail-safe, SAF-FW-007). No polarity parameter exists for these inputs.
- 3.3 mA wetting current (3.3 V / 1 kΩ) — OK for gold contacts; if silver micro-switches show wetting problems lower the pull-up to 680 Ω (R5 §5.2).
- START is the home end and the **only** homing reference (D-29 b; `home.ref_switch` is retired). The END switch is never a home reference: reaching it during HOME means fault HOME_WIRING. Mount so that the actuated position lies **beyond** `limits.soft_min_um` / `soft_max_um` (A-07: soft limits 0.5 / 290 mm, home offset 1 mm).
- The FW stops on the **first** edge (no software delay) and debounces only the release (FW-SW-001).

---

## 6. Operator buttons STOP/BREAK and PAUSE (D-14, D-26, R5 §5.5)

| Button | Contact | Wiring (input cell) | FW |
|---|---|---|---|
| **STOP/BREAK** — red flush pushbutton (**not** a mushroom; must not look like the E-stop, EN ISO 13850), direct-opening contact block | **NC to GND** (confirmed default, A-08, Q-R5-10) | 1 kΩ pull-up, 1 kΩ + 47 nF (τ ≈ 94 µs) → PC7 | open edge = immediate operational stop (holding) + HALT latch (src = button); clear = released + PC HALT_CLEAR (SAF-FW-022) |
| **PAUSE** — yellow or black momentary button | **NO to GND** (A-08) | 4.7 kΩ pull-up, 1 kΩ + 220 nF (≈ 1.3 ms) → PB6 | press = controlled stop (holding), PAUSED; press while paused = RESUME_REQUEST event only (SAF-FW-023) |
| **RESET** (blue) | NO | **not an MCU input** — part of the K1 self-hold circuit (§7) | – |

---

## 7. E-stop with power removal (D-11, SYS-006) — R5 §1.3 **Option A** (accepted, D-28)

```
 +48V ─[F1 T10A]─┬─[K1 1-2]─[K1 3-4]──────────────────────────────► HBS86H +Vdc     (two DC-1 poles in series)
                 │
                 └─[S0.NC1]─┬─[S1 RESET NO, blue]─┬─[KT NC (opt.)]─[K1 coil A1-A2]─┐   coil 48 V DC ≈ 3 W
                            │                     │                ║ varistor/TVS   │
                            └─[K1 13-14 aux NO]───┘ (self-hold)                     │
 0V (48 V return) ──────────────────────────────────────────────────────────────────┴─► HBS86H GND

 Logic side (Nucleo GND, never connected to the 48 V circuit):
   S0.NC2 (2nd NC block of the same E-stop) ──► input cell 1k PU / 1k + 4.7 nF (τ ≤ 10 µs) ──► PA10  E-stop sense
   K1 aux NO (front block, e.g. 43-44)       ──► input cell 1k PU / 1k + 1 µF (≈ 2 ms)       ──► PA7   DRV_PWR (required)
   PB9 ─► NPN driver (base pull-down) ─► relay KT coil; KT NC in the K1 hold path (provisioned, unused in release 1 — D-28)
```

| Element | Function | Parts (examples, ASSUMED, R5 §1.3) |
|---|---|---|
| S0 | Ø40 mm red mushroom, yellow background, turn-to-release, direct-opening, **2 × NC** (NC1 in the 48 V coil path, NC2 to the MCU — gold-flashed block preferred for 3 mA / 3.3 V) | Schneider ZB5AS844 + 2 × ZBE102, or Eaton M22-PVT + M22-K02 |
| K1 | DC-rated contactor, **DC-1 20 A @ 60 V**, 48 V DC coil, front aux 1NO+1NC, coil suppressor (keeps drop-out fast); two poles in series switch +48 V | Eaton DILM7-10 (48 V DC coil) + DILA-XHI11 |
| S1 | RESET, blue, 1 NO, ≥ 48 V DC 0.1 A | – |
| KT (opt.) | MCU trip relay on PB9, NC contact in the hold path — can only **open** the power path, never close it | provisioned (D-28), not used in release 1 |

Behaviour (R5 §1.3): pressing S0 drops K1 → driver unpowered within ≈ 20–60 ms (cat. 0, independent of the MCU) and NC2 opens → FW stops pulses ≤ 100 µs, drives ENA to disabled, latches ESTOP, clears HOMED (SAF-FW-005). Releasing S0 starts nothing: K1 stays open until RESET; the driver then powers up **disabled** (ENA held disabled by the FW), the FW requires PC ESTOP_CLEAR + ENABLE (≥ 500 ms settle) + HOME (SAF-FW-006). A PSU or AC loss also drops K1 until RESET. E-stop + RESET is also the HBS86H alarm reset (power cycle, R5 §3.3).

Back-drive note (R5 §1.2): with > 30–50 kg on the specimen, the SFU1605 back-drives when power is cut — the table springs back by the elastic deflection (safe direction on a **horizontal** stand). The axis is **horizontal** (D-28): no brake; the spring-back on power cut is accepted (it releases the specimen).

FW use of DRV_PWR (D-28, D-29 c; SRS v0.3 FW-SW-005, SAF-FW-024/025/026). The input is sampled at 1 kHz with a 20 ms stability filter; status DRV_PWR and EVENT DRIVER_POWER are sent on every change. The contact is **required** in release 1: `drv.pwr_sense_enable` = false (reboot-required) is for bring-up without the contactor wiring only.
- **Power lost, any cause** (E-stop, PSU loss, K1 not reset): within 25 ms the FW stops pulses, drives ENA to disabled, enters NOT_ENABLED and clears HOMED and VALID. ENABLE and all motion are refused (DRV_UNPOWERED) until power returns. ENABLE then waits `motion.ena_settle_ms` (500 ms) counted from the later of ENABLE and the power return.
- **K1 welded**: the E-stop sense is open while DRV_PWR still reports power continuously for longer than `drv.k1_weld_ms` (default 200 ms > K1 drop-out 20–60 ms + aux delay). This latches fault K1_WELDED: welded contactor, or the aux contact miswired or bridged. FAULT_CLEAR is accepted only after the cause is gone.
- The ALM start-block uses DRV_PWR as "driver power present".

---

## 8. Supplies

| Supply | Source | Load | Notes |
|---|---|---|---|
| **48 V DC driver supply** (D-16) | **Mean Well SDR-480-48** (D-28; 10 A, 15 A peak 3 s, auto-recovery, DC-OK relay); **not LRS-350-48** (48 V model latches off on overload/OVP/OTP) | ≈ 40–70 W continuous, 100–150 W peaks | Set-point 48 V (not 55 V: regen margin to OVP, R5 §2.4). F1 T10A at the PSU + output. PSU → K1 → driver: 1.5 mm² twisted pair ≤ 1–2 m. No extra bulk capacitor by default (inrush on RESET, slower de-energisation). |
| **Nucleo** (D-22) | (a) PC USB via the ST-LINK (JP5 = U5V), or (b) a separate DC-DC 5 V into **E5V (CN7-6), JP5 = E5V** (ASSUMED UM1724) | < 150 mA incl. HX711 and input cells (3.3 V: 4 × 3.3 mA wetting + HX711 ≈ 1.5 mA + driver LEDs 3 × 7 mA) | With (b) the USB still carries the VCP; ST-LINK and target ground are common. |
| 3.3 V (logic, HX711, pull-ups) | Nucleo on-board regulator (3V3 pin) | ≈ 50 mA | – |
| 5 V (Stage 2 buffer) | Nucleo 5V pin | < 60 mA | R5 §5.2 |
| K1 coil | the 48 V bus (48 V coil, D-28) | ≈ 63 mA | PSU loss → power stays off until RESET (wanted) |

---

## 9. Grounding and shielding (R2 §6.5, R5 §5.2)

- **Single logic ground**: Nucleo GND = HX711 GND = input-cell GND = opto return side (PUL−, DIR−, ENA−, ALM−, PEND−). The PC ground enters through the USB/ST-LINK.
- **Isolation barrier**: the driver's 48 V GND, motor PSU 0 V and PE are **never** connected to the Nucleo GND; the HBS86H optos (≥ 500 MΩ) and the dry K1 aux contact keep the barrier. The optional DRV_PWR opto variant (48 V sense with LTV-817) needs ≥ 3 mm creepage on a separate connector (R5 §5.2).
- Motor cable shielded, shield to driver PE at the driver end; encoder cable separate from the motor phases.
- Driver control cable: shielded twisted pairs (e.g. LiYCY 6×2×0.25 mm²), shield to the logic GND at the board end only.
- Load-cell shield (yellow) to the HX711 GND only. Keep the HX711 and its wires away from the PSU, contactor and motor cables.
- Earth the 48 V PSU per its manual; if the PC is also mains-earthed, the opto barrier prevents a loop through the driver.

---

## 10. Driver setup sheet (SYS-005, D-16, D-27 — **PO to confirm the target setting**)

Driver: **PFDE (普菲德) HBS86H**, Leadshine-compatible clone (D-16), 24–100 VDC / 18–70 VAC, run at 48 V DC; control inputs rated 5–24 V; RS232 tuning port (GND/TX/RX). Terminal order (label): Signal PUL+, PUL−, DIR+, DIR−, ENA+, ENA−; OUT PEND+, PEND−, ALM+, ALM−; Encoder EB+, EB−, EA+, EA− (+ encoder supply per the manual).

| Switch | Function (label, D-27) | Current state (1 = ON, the only plausible reading) | Recommended (D-27, PO to decide) | FW / doc consequence |
|---|---|---|---|---|
| SW1–SW4 | pulses/rev, Leadshine table (R2 §1.6) | 0 1 1 1 → **800 p/rev → 160 steps/mm** (5 mm lead, direct) | on/off/on/off → **4000 p/rev → 800 steps/mm** | `motion.steps_per_mm` default 160 until changed; the travel-calibration wizard measures the real value (SW-CAL-002/003) |
| SW5 | motor direction (off = CCW, on = CW) | 0 → CCW | as needed | `motion.dir_invert` fixed at the HW gate so that +x = away from START |
| SW6 | off = standard, on = start-up acceleration assist | 1 → assist on | PO decision (off = standard behaviour) | none in FW; record the state |
| SW7/SW8 | motor select: on/on = 60 mm motor; off/on = 86-80 / 86-118 (closed loop); on/off = 86-151 (closed loop); **off/off = 86 open loop, 6.0 A** | 0 0 → **86 open loop 6.0 A — encoder unused** | closed loop matching the motor body length (86-80/86-118 → off/on; 86-151 → on/off) | **ALM semantics depend on this:** closed loop → following-error ALM + PEND meaningful; open loop → missed steps silent, ALM only over-current/over-voltage/unpowered. Record it in the setup sheet and in every test report |
| Supply | 48 V DC | 48 V | – | – |
| Tuning (RS232) | following-error limit, current, ENA/ALM/PEND levels, smoothing | factory | factory in release 1 (D-28); keep command smoothing off (R2 §1.9) | changes evaluated at the HW gate |

Timing defaults stay conservative regardless of the clone's real limits: PUL high/low ≥ 10 µs, ≤ 50 kHz, DIR setup ≥ 20 µs, ENA/driver-power settle ≥ 500 ms (D-16, D-28).

---

## 11. Hardware check list (SYS-009) — run at the first PO-approved hardware gate, before any motion under load

Executed by Validator E with Implementer A; results go into the check-list report. "Budget" = SRS §6 / requirement value.

| # | Item | Method | Pass criterion / budget | Req. |
|---|---|---|---|---|
| C-01 | Board identity and solder bridges SB13/14 ON, SB62/63 OFF, SB16/50 ON, SB54/55 OFF, SB46/52 OFF, SB51/56 ON (A4/A5 → PC1/PC0) | visual inspection, photo | as `pinout.md` §5 | SYS-007, FW-PLT-002 |
| C-02 | Clock source | GET_STATUS `CLK_FALLBACK`; PUL frequency at 50 kHz with a counter | no fallback; frequency error ≤ 0.1 % | FW-PLT-002 |
| C-03 | VCP link at 921 600 Bd | 10 min streaming soak at 80 Hz + 20 commands/s | 0 sequence gaps, 0 CRC errors (FW and PC counters) | IF-002, NFR-004 |
| C-04 | Flash erase vs IWDG and E-stop during SAVE | SAVE that forces a sector erase; press the E-stop during the erase | no IWDG reset, SAVE ≤ 2.5 s, NVM valid after reboot; ENA disabled ≤ 1 ms also during the erase (RAM-resident ISR, FW_design §5.11) | FW-NVM-002/003, SAF-FW-019 |
| C-05 | HX711 at 80 SPS on silicon | logic analyser on DOUT/SCK, 10 000 reads; GET_STATUS measured rate | SCK-high ≤ 50 µs, read ≤ 60 µs, rate within ±1 % of the LA value, timestamp vs DOUT edge ≤ 5 µs | FW-AFE-001/004, FW-TIM-001 |
| C-06 | Opto drive margin (3.3 V direct bring-up; inputs rated 5–24 V) and later the 5 V buffer | hold PUL and ENA high from a debug build; VOH under load; 100 Ω series shunt → I_LED | bring-up: I_LED ≥ 6 mA and VOH ≥ 3.0 V, else fit the buffer first; **before calibration: buffer fitted (D-28), I_LED ≈ 10–13 mA** | SYS-009, SYS-011, OI-05, D-28 |
| C-07 | ENA enable/disable and settle | ENABLE/DISABLE commands; shaft holding torque by hand; ALM/PEND levels; LA on ENA vs the first PUL/DIR edge after ENABLE **and after a DRV_PWR return** (K1 RESET) | disabled = shaft free (no load!), enabled = holding; first edge ≥ `motion.ena_settle_ms` (500 ms) after the later of ENABLE and power return | FW-MOT-008, SAF-FW-024, A-04 |
| C-08 | PUL/DIR timing at the driver terminals | LA at PUL+/DIR+ at 50 kHz, reversals | high ≥ 10 µs, low ≥ 10 µs, DIR setup ≥ 20 µs on every reversal | FW-MOT-001 |
| C-09 | Step count integrity | 100 moves cross-checked with an external counter / TIM3 counter test build | difference 0 (± 1 only with `pos_uncertain`) | SAF-FW-004 |
| C-10 | E-stop reaction | scope: NC2 edge at PA10 vs last PUL edge and ENA level, 100 trials; supply at the driver vs time with the MCU held in reset | last PUL edge ≤ 100 µs, ENA disabled ≤ 1 ms; driver supply < 5 V within 100 ms independent of the MCU | SAF-FW-005, SYS-006 |
| C-11 | Limit / STOP-button reaction | scope: input edge at the MCU pin vs last PUL edge, 100 trials each | ≤ 200 µs | SAF-FW-002, SAF-FW-022 |
| C-12 | FW load-limit reaction | threshold set just above a known load; DOUT ready of the deciding sample vs last PUL edge | ≤ 200 µs | SAF-FW-002/008 |
| C-13 | PC STOP/HALT command and Pause/Break key | last byte on the RX line vs last PUL edge; key → last edge | ≤ 2 ms; key → last edge ≤ 100 ms p95 | SAF-FW-002, NFR-003 |
| C-14 | Hang → IWDG | injected infinite loop (test image) while moving | PUL stops ≤ 100 ms, reset cause IWDG | SAF-FW-019 |
| C-15 | Reset under load (**D-33 e**, DEF-P1-05) | horizontal axis: spring specimen preloaded in **tension ≥ 98 N (10 kgf)**; MCU reset (NRST, power cycle of the Nucleo only, IWDG test image); dial indicator on the table | axis motion ≤ 0.01 mm (driver keeps holding, D-13); raw change within noise; no PUL edge from reset (LA) | SAF-FW-018 |
| C-16 | ALM / PEND levels of the PFDE clone, ALM reset | driver powered/unpowered, in position / moving, open- vs closed-loop setting; provoke an ALM (bench, e.g. supply under-voltage); reset by E-stop + RESET (power cycle) and, separately, try the ENA-toggle reset; ALM line chatter (wiggle the connector) | levels recorded, `drv.*_active_level` set; one ALM_CHANGED per change (chatter → at most one pair per `io.release_ms`, ALM is polled); new motion starts refused while ALM active and DRV_PWR on; running moves unaffected; power-cycle reset works, ENA-toggle result recorded | FW-SW-004, SAF-FW-026, D-28, SYS-009 |
| C-17 | Input wire-break | unplug each NC input (E-stop sense, START, END, STOP) | reads active, stop + flag | SAF-FW-007 |
| C-18 | Main-loop and ISR budgets | GET_STATUS `loop_max_us`; DWT measurement build at 50 kHz stepping + 80 Hz stream + 20 cmd/s | loop ≤ 1000 µs, step ISR ≤ 2 µs, CPU ≤ 15 %, masked windows ≤ 1 µs, E-stop/STOP ISR ≤ 1 µs | NFR-006, NFR-007 |
| C-19 | Driver DIP sheet | inspection of §10, all 8 switches incl. SW7/SW8 open/closed loop; first travel calibration result vs the expected steps/mm (160 at 800 p/rev, 800 at 4000 p/rev) | recorded | SYS-005, D-27 |
| C-20 | E-stop circuit and K1 plausibility | wiring inspection of §7 against R5 Option A; press S0 normally; then simulate a welded K1 by bridging the K1 aux contact (driver supply off!) and press S0 | as drawn; normal press → DRIVER_POWER (0) ≤ 25 ms after the drop-out, no fault; bridged aux → FAULT K1_WELDED after `drv.k1_weld_ms` (200 ms), not before; FAULT_CLEAR refused until the bridge is removed | SYS-006, SAF-FW-025 |
| C-22 | Driver drive through the buffer board (SYS-011) | before the first calibration: inspect the SN74ACT244 board; measure I_LED of PUL/DIR/ENA through it; repeat C-07/C-08 through the board | board installed; I_LED within the driver rating (≈ 10–13 mA); ENA/PUL/DIR timing as C-07/C-08 | SYS-011, SYS-009 |
| C-23 | DRV_PWR during SAVE (D-33 f) | SAVE forcing a sector erase; open K1 (E-stop) during the erase | after the erase: DRV_PWR reaction ≤ erase time + 25 ms; NVM record valid; no IWDG reset | SAF-FW-024, FW-NVM-003 |
| C-21 | Driver-power loss without E-stop | during a slow jog, switch off the 48 V PSU (or open K1 via the hold path) with S0 released | stop and ENA disabled ≤ 25 ms after the PA7 change, NOT_ENABLED, HOMED = 0, EVENTs DRIVER_POWER (0) + STOPPED (DRV_POWER_LOST); ENABLE refused until power returns, first pulse ≥ 500 ms after the later of ENABLE and power return; PA7 toggles < 20 ms (bounce) ignored | SAF-FW-024, FW-SW-005, FW-MOT-008 |

---

## 12. Connection inspection list (SYS-001 / SYS-007)

Every connection of SRS §3.1 must appear here with its terminal: PUL/DIR/ENA (§2), ALM/PEND (§3), HX711 + cell (§4), START/END (§5), STOP/PAUSE (§6), E-stop NC1/NC2, K1, RESET, DRV_PWR, KT (§7), 48 V supply, Nucleo supply (§8), grounds/shields (§9), motor + encoder (driver manual), USB to the PC. Terminal numbers are filled in when the panel/shield is built (Stage 1: Nucleo header pins from `pinout.md`).

---

## 13. Change history

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-10-03 | First draft: direct 3.3 V common-cathode drive (D-17; bring-up only — PFDE HBS86H inputs rated 5–24 V, SN74ACT244 buffer required before calibration, D-28), ALM/PEND cells (semantics vs open/closed loop, D-27), HX711 at 3.3 V with RATE on PB5, NC limits with RC, STOP NC / PAUSE NO, E-stop R5 Option A with sense contact and DRV_PWR (accepted D-28), supplies (SDR-480-48), grounding, 8-switch driver setup sheet, SYS-009 check list. |
| 0.2 | 2026-10-03 | Aligned to SRS v0.3 / ICD v0.3 / D-29 / D-30:<br>• DRV_PWR (PA7) is **required** (20 ms filter, any-cause power loss → stop / ENA disabled / NOT_ENABLED / not homed ≤ 25 ms; K1_WELDED after `drv.k1_weld_ms` 200 ms);<br>• stale `home.switch` reference removed (START-only homing, D-29 b; END during HOME = HOME_WIRING);<br>• ALM start-block scope (SAF-FW-026) and "ALM is never a step-loss indicator" (D-27);<br>• C-20 now tests K1_WELDED; new C-21 driver-power loss without E-stop. |
| 0.3 | 2026-10-03 | Final P1 round:<br>• C-15 reworded per D-33 e / DEF-P1-05 (horizontal axis, spring specimen ≥ 98 N in tension);<br>• SYS-009 items added per OBS-P1-12: C-07 settle after DRV_PWR return, C-16 ALM reset by power cycle / ENA toggle + chatter, C-22 buffer board, C-23 DRV_PWR during SAVE (C-20/C-21 already cover K1_WELDED and the E-stop/PSU paths);<br>• ALM is polled (OBS-P1-10). Mapping to FW_test_plan HG items: C-15 = HG-15, C-16 = HG-16/24, C-20 = HG-20/22, C-21 = HG-21, C-22 = HG-23, C-23 = HG-04. |
