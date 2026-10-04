# Bird Bend Stand — Wiring, supplies, grounding and hardware check list

| Item | Value |
|---|---|
| Doc | `01_HW/wiring.md` |
| Version | **0.5** — M2 close-out, **CR-03 (D-41 / D-42 / D-43)**: **no power-removal contactor** (K1, RESET, KT and the K1 aux sense removed); the red E-stop = NC contact → PA10 (MCU/FW stop) **plus an extra NO contact that forces the driver ENA opto to "disabled" independent of the MCU** (D-42, §2.1 / §7, diode OR without contention with the MCU ENA output); DRV_PWR (PA7) optional 48 V presence sense (`drv.pwr_sense_enable` default 0; K1_WELDED only with `drv.k1_check_enable`, SRS OI-18); J-STIM series resistor 220 Ω (REQ-A-M2-07); multimeter points §11.1; check list C-07/C-10/C-16/C-17/C-20/C-21/C-23 revised, C-25/C-26 new. · 0.4 — M2: CR-01 (D-36: single red button = E-stop, no STOP/BREAK button; operator panel = E-stop + PAUSE), measurement header MH (CR-02 / D-40 c, §6.1), check-list methods without oscilloscope / logic analyser (D-35 G5) with the C ↔ HG map (REQ-A-M2-02). · 0.3 — final P1 draft, aligned to SRS v0.3 / ICD v0.3(+v0.4) / D-29…D-33 and the Validator E review (`02_FW/docs/FW_test_plan.md`) |
| Date | 2026-10-04 |
| Owner | Implementer A (FW) |
| Binding inputs | **v0.5: SRS v0.6, ICD v0.7, DECISIONS D-41 (no contactor, E-stop MCU/FW only, residual risk accepted by the PO), D-42 (hardwired ENA cut), D-43 (d, e), FW_test_plan v0.4 §6.5 HG-10/17/20…24/32, §6.8, REQ-A-M2-07** · SRS v0.3 SYS-001, SYS-005…SYS-009, SYS-011, SAF-FW-005/007, FW-SW-001…004; DECISIONS D-11, D-13, **D-16 (driver = PFDE HBS86H, Leadshine-compatible clone, 24–100 VDC, control inputs 5–24 V)**, D-17, D-18, D-20, D-21, D-22, D-24, D-26, **D-27 (DIP/label)**, **D-28 (R5 defaults accepted)**, **D-29 (b START-only homing, c DRV_PWR / K1_WELDED)**; SRS v0.3 SAF-FW-024/025/026, FW-SW-005, SYS-011; R2 §1, §4, §6; **R5 §1.3 Option A (E-stop power removal), §2.4 (PSU), §3.1 (ALM/PEND), §4 (direct 3.3 V drive), §5.2 (filters, interface board Option A), §5.5 (buttons)** |
| Related | `01_HW/pinout.md` (pins, NVIC), `02_FW/docs/FW_design.md` |
| Status | Engineering proposal for a lab stand, **not a certified safety design**; the risk assessment (EN ISO 12100 / ISO 13849-1) stays with the PO (R5 header). **Since D-41 the red button is not an IEC 60204-1 emergency stop** (no power removal; residual risk stated to and accepted by the PO). The R5 defaults (E-stop Option A — **superseded by D-41 / D-42**, horizontal axis/no brake, SDR-480-48, perfboard buffer → SN74ACT244 shield, PAUSE NO, pin map, TRIP provision) are **accepted by the PO (D-28)**; D-36 replaced the separate STOP/BREAK button by the single red E-stop; items still marked **(PO)** concern the driver DIP setup (D-27). |

Signal pins are given by name; the pin/connector numbers are in `pinout.md` §1.1. All values marked ASSUMED come from R2/R5 and are verified at the hardware gate (§11).

---

## 1. Overview

```
 230 VAC ─[Q0 MCB]─► 48 V PSU (SDR-480-48, PO, mains switch) ─+48V─[F1 T10A]──────────────► HBS86H +Vdc
                                                                0V ─────────────────────────► HBS86H GND
                                                   +48V ─► isolated DC-DC 48→5 V (1 W) ─► +5V_CUT (logic side, §8)
                         red E-stop S0: NC ──────────────────────────────► E-stop sense (PA10, MCU/FW stop, §7)
                                        NO ── +5V_CUT ─[R_E]─►|D2 ──┐
                         PA4 ENA (─ buffer) ───────────────►|D1 ───┴─► HBS86H ENA+ (diode OR, D-42, §2.1)
                         (optional) 48 V presence opto ──────────────────► DRV_PWR sense (PA7, default ignored, §7.2)
                         no contactor, no RESET button (D-41)

 PC (USB) ═══ ST-LINK/V2-1 ═══ NUCLEO-F446RE (USART2 VCP 921600, powered from USB or DC-DC 5 V, §8)
                                 │  PUL/DIR/ENA (3.3 V push-pull, common cathode, D-17) ─► HBS86H opto inputs (§2)
                                 │  ALM/PEND ◄─ HBS86H OC opto outputs (§3)
                                 │  DOUT/SCK/RATE ─ HX711 module @ 3.3 V ─ Keli DEF 200 kg (§4)
                                 │  START/END limit NC (§5) · PAUSE NO on the operator panel (§6)
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

### 2.1 ENA: MCU drive OR hardwired E-stop cut (D-42, REQ-A-M2-07) — no contention

```
 Stage 1 (bring-up): PA4 ───────────────────────────────►|─ D1 ─┐
 Stage 2 (buffer):   PA4 ─► SN74ACT244 (5 V) output ─────►|─ D1 ─┤        HBS86H "Signal"
                                                                ├────────► ENA+ ─[≈270R int.]─[LED]─ ENA− ──► logic GND
 +5V_CUT (§8) ──[S0 NO]──[R_E 68 Ω, 0.5 W]───────────────►|─ D2 ─┘
                                                     10 kΩ pull-down ENA+ → logic GND (node defined while nothing drives)
 D1, D2: Schottky, e.g. BAT54 / BAT43 (≥ 30 V, ≥ 200 mA)          values ASSUMED (internal 270 Ω, LED 1.2 V, Schottky 0.3 V)
```

| Condition | ENA LED current (ASSUMED values) | Notes |
|---|---|---|
| MCU "enabled" (PA4 / buffer low), E-stop released | 0 mA → driver enabled | D2 open (S0 NO open), D1 not conducting |
| MCU "disabled", E-stop released | stage 1: (3.3 − 0.3 − 1.2) / 270 ≈ 6.7 mA (bring-up margin as §2, C-06); stage 2: (5.0 − 0.3 − 1.2) / 270 ≈ 13 mA | D1 conducts; D2 has no source |
| E-stop pressed, MCU in reset / unpowered / "enabled" | (5.0 − 0.3 − 1.2) / (68 + 270) ≈ **10.4 mA → driver disabled** | **independent of the MCU, the FW and the Nucleo supply** (+5V_CUT comes from the 48 V bus); D1 is reverse-biased (ENA+ ≈ 4.0…4.5 V, PA4 / buffer output low): only Schottky leakage (µA) |
| E-stop pressed, MCU "disabled" | ≈ 10…13 mA (the higher source wins) | no contention: each source sees the other through a reverse-biased diode; **PA4 (TC, not 5 V tolerant) never sees the 5 V** |
| ENA+ shorted to GND (fault) | +5V_CUT: 4.7 V / 68 Ω ≈ 69 mA | within a 1 W DC-DC (200 mA); R_E 0.5 W |

Rules: the D-42 path contains **no MCU-controlled element** (HG-20); the FW never reads ENA back, so the externally
forced "disabled" state never causes a fault, a latch or a start-block of its own (D-42; FW_design §9.10). Without the
buffer (stage 1) D1 costs ≈ 1 mA of the already marginal direct drive — the SN74ACT244 stage stays mandatory before
calibration (D-28, SYS-011). The 10 kΩ pull-down keeps ENA+ at 0 V (no LED current = enabled/holding, D-13) while the MCU
is in reset and the E-stop is released.

**Stage 2 interface shield (R5 §5.2 Option A, required before calibration per D-28; ASSUMED part values):** SN74ACT244 at 5 V (Nucleo 5V pin), inputs from PA0/PA1/PA4 with 10 kΩ pull-downs (outputs low during MCU reset → same safe state as today), outputs to PUL+/DIR+ and through D1 to ENA+ (§2.1), PUL−/DIR−/ENA− still to GND → ≈ 11–13 mA per LED. Unused buffer inputs to GND, 100 nF + 10 µF at U1. The shield also carries the input cells of §3, §5, §6, §7, ESD arrays (TPD4E05U06 class) and pluggable terminals J1 driver (10-pole), J2 limits (4-pole), J3 panel (8-pole), J4 HX711 (6-pin). Every signal is on the Arduino headers (pin map R5 §6.1).

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

## 6. Operator panel: red E-stop and PAUSE (D-14, D-26, D-36, R5 §5.5)

CR-01 / D-36: there is **no STOP/BREAK button and no STOP input** (PC7 is free and used by the measurement header). The operator panel carries the red mushroom E-stop (§7: NC → PA10 sense, NO → hardwired ENA cut, D-41 / D-42) and the PAUSE button; holding stops come from the PC (GUI STOP, Pause/Break key = HALT).

| Button | Contact | Wiring (input cell) | FW |
|---|---|---|---|
| **E-stop** — red mushroom, latching, turn-to-release | **NC** (direct-opening) + **NO** | NC: 1 kΩ pull-up, 1 kΩ + 4.7 nF (τ ≤ 10 µs) → PA10; NO: +5V_CUT → R_E → D2 → ENA+ (§2.1, §7, D-42) | NC open = E-stop: TRUNCATE, ENA disabled, ESTOP latched (SAF-FW-005); the NO cut is invisible to the FW (no read-back, D-42) |
| **PAUSE** — yellow or black momentary button | **NO to GND** (A-08) | 4.7 kΩ pull-up, 1 kΩ + 220 nF (≈ 1.3 ms) → PB6 | press = controlled stop (holding), PAUSED; press while paused = RESUME_REQUEST event only (SAF-FW-023) |
| ~~RESET (blue)~~ | – | **removed with the contactor (D-41)**; a driver alarm is reset by switching the 48 V PSU off / on (HG-24) | – |

---

### 6.1 Measurement header MH (CR-02, D-40 c; FW_test_plan v0.3 §6.2) — bench only

A 2 × 8 pin header on the interface board / shield; GND next to every signal; wires ≤ 10 cm; **1 kΩ series resistor at
the MCU end of every jumper** (a misconfigured pin can never drive a safety node). Taps are taken at the driver-side
nodes: the MCU pins during the direct 3.3 V bring-up, the SN74ACT244 outputs once the buffer is fitted (SYS-011) — hence
5 V-tolerant MCU pins only (pinout §1.5). The MH pins are digital inputs without pull in every FW build (never analog),
so the jumpers may stay fitted; they are used only by the measurement images (`nucleo_f446re_meas` / `_meas_dwt`).

| Jumper | From (tap) | To MCU pin | Purpose |
|---|---|---|---|
| J-PUL-A | PUL node | PC7 | probe: last PUL edge after an event, PWM-input period / width, PUL time stamps |
| J-PUL-B | PUL node | PB7 | independent pulse counter (MT-2) |
| J-DIR | DIR node | PC9 | DIR edges, DIR setup, per-direction counts |
| J-ENA | ENA node | PC8 | ENA edge after E-stop / DRV_PWR / ENABLE |
| J-EVT (one source at a time) | PA10, PB0, PC1, PB6, PB4, PA7, PA3, DIR or PB8 | PC6 | event start of the latency probe (MT-3) and event stamps |
| J-AUX (selector) | PB4, PB10, PA2, PA7, PA8, PA10, PB8 | PA11 | time stamps of a second signal |
| J-STIM | PB8 — **series resistor 220 Ω, not 1 kΩ** (REQ-A-M2-07): the input cells have 1 kΩ pull-ups, so the stimulus must reach a valid low: 3.3 V · 220 / 1220 ≈ 0.60 V ≤ 0.99 V (0.3 VDD); with 1 kΩ the node would sit at 1.65 V (undefined). PB8 current ≤ 2.7 mA (≤ 15 mA into a node still grounded) | input connector of E-stop sense / START / END / PAUSE **with the switch unplugged**; for the E-stop sense only per the bench procedure FW_test_plan §6.8 (the button's **NO / D-42 channel stays wired and active**, PSU mains switch in reach) | stimulus series with random phase (MT-7) |


## 7. E-stop: MCU/FW stop + hardwired ENA cut (D-41, D-42; supersedes R5 §1.3 Option A / D-28)

```
 Operator panel, red mushroom S0 (turn-to-release), two contact blocks:

   S0.NC (direct-opening) ──► input cell 1k PU / 1k + 4.7 nF (τ ≤ 10 µs) ──► PA10  E-stop sense (EXTI level 0)
   S0.NO ─────────────────── +5V_CUT ─[R_E 68 Ω]─►|D2 ──► ENA+ (diode OR with the MCU drive, §2.1)

 Logic side only (logic GND); the 48 V circuit has no E-stop element: +48V ─[F1]─► HBS86H +Vdc (no contactor)
```

| Element | Function | Parts (examples, ASSUMED) |
|---|---|---|
| S0 | Ø40 mm red mushroom, yellow background, turn-to-release; **1 × NC (direct-opening, gold-flashed for 3 mA / 3.3 V) → PA10**, **1 × NO → D-42 ENA cut** | Schneider ZB5AS844 + ZBE102 (NC) + ZBE101 (NO), or Eaton M22-PVT + M22-K01 + M22-K10 |
| +5V_CUT | isolated DC-DC 36…75 V → 5 V, 1 W, ≥ 1.5 kV isolation, input from the 48 V bus after F1, output 0 V = logic GND | TRACO TMR 1-4811 class (§8) |
| R_E, D2 (D1) | 68 Ω 0.5 W; Schottky BAT54 / BAT43 | §2.1 |

Behaviour (D-41 / D-42, SAF-FW-005/006):
- **Press:** the NO contact drives ENA+ (≈ 10 mA) → the driver de-energises the motor **independent of the MCU** (HG-10 c);
  the NC contact opens → the FW stops the pulses ≤ 100 µs (TRUNCATE), drives ENA to *disabled* ≤ 1 ms, latches ESTOP,
  clears HOMED and VALID. With a slow press either contact may act first; the FW never reads ENA back, so the forced
  state causes no fault (HG-10 e). The load may back-drive the screw (horizontal axis, no brake, accepted — R5 §1.2).
- **Release:** the NO contact opens; the MCU keeps ENA *disabled* (NOT_ENABLED) → no motion, nothing re-energises
  by itself; restart = PC ESTOP_CLEAR (sense closed ≥ 100 ms) + ENABLE (≥ 500 ms settle) + HOME (SAF-FW-006).
- **MCU fault:** the D-42 cut is unaffected; the MCU channel is covered by the IWDG (≤ 100 ms → reset → boot with the
  E-stop open drives ENA disabled, SAF-FW-018).
- **Driver alarm reset:** 48 V PSU off / on (no RESET button any more, HG-24).

Residual risks (D-41, stated to the PO): not an IEC 60204-1 stop category 0 (no power removal; the driver keeps its supply);
the cut relies on the driver's ENA input; a broken NO wire / failed NO contact is **not detected** (normally-open, not
monitored — R-10; mitigation: the periodic functional test HG-10 c); the MCU channel depends on the FW (IWDG-covered).
The 48 V PSU mains switch is the only power removal.

### 7.1 FW behaviour with the release-1 defaults (CR-03)

`drv.pwr_sense_enable` = **0** (default since ICD v0.7, reboot-required) → PA7 is ignored: STATUS DRV_PWR reads 1, no
DRV_UNPOWERED refusal, no DRIVER_POWER events. `drv.k1_check_enable` = **0** → K1_WELDED is never latched (it needs both
parameters, SRS OI-18). The ALM start-block uses DRV_PWR = 1 ("power assumed present"). HG-32 checks the defaults.

### 7.2 Optional 48 V presence sense on PA7 (not fitted in release 1)

```
 +48V ─[22 kΩ 0.5 W]─┬─[LTV-817 LED]─ 0V(48)          creepage ≥ 3 mm, separate connector (R5 §5.2)
                     └ (reverse diode 1N4148 across the LED)
 3V3 ─[10 kΩ PU — not 1 kΩ: CTR margin]─┬─[1k]─┬─► PA7         opto C ─ node, E ─ logic GND
                                        │      [1 µF]
                         opto collector ┘      GND
```
LED ≈ (48 − 1.2) / 22 k ≈ 2.1 mA; CTR ≥ 50 % → ≥ 1 mA ≫ 0.33 mA pull-up current → low = powered (fixed polarity). Only
with this sense fitted: set `drv.pwr_sense_enable` = 1 (SAVE + reboot) → power lost (PSU off) → stop, ENA disabled,
NOT_ENABLED, HOMED cleared ≤ 25 ms, ENABLE refused (DRV_UNPOWERED) until power returns + 500 ms settle (SAF-FW-024,
FW-SW-005; HG-21 / HG-04 b / HG-07). Keep `drv.k1_check_enable` = 0 (there is no contactor to watch; HG-22 N/A).

---

## 8. Supplies

| Supply | Source | Load | Notes |
|---|---|---|---|
| **48 V DC driver supply** (D-16) | **Mean Well SDR-480-48** (D-28; 10 A, 15 A peak 3 s, auto-recovery, DC-OK relay); **not LRS-350-48** (48 V model latches off on overload/OVP/OTP) | ≈ 40–70 W continuous, 100–150 W peaks | Set-point 48 V (not 55 V: regen margin to OVP, R5 §2.4). F1 T10A at the PSU + output. PSU → driver: 1.5 mm² twisted pair ≤ 1–2 m (no contactor, D-41). The PSU mains switch is the only power removal (§7). No extra bulk capacitor by default. |
| **Nucleo** (D-22) | (a) PC USB via the ST-LINK (JP5 = U5V), or (b) a separate DC-DC 5 V into **E5V (CN7-6), JP5 = E5V** (ASSUMED UM1724) | < 150 mA incl. HX711 and input cells (3.3 V: 4 × 3.3 mA wetting + HX711 ≈ 1.5 mA + driver LEDs 3 × 7 mA) | With (b) the USB still carries the VCP; ST-LINK and target ground are common. |
| 3.3 V (logic, HX711, pull-ups) | Nucleo on-board regulator (3V3 pin) | ≈ 50 mA | – |
| 5 V (Stage 2 buffer) | Nucleo 5V pin | < 60 mA | R5 §5.2 |
| **+5V_CUT** (D-42 ENA cut) | isolated DC-DC 36…75 V → 5 V, 1 W, ≥ 1.5 kV (e.g. TRACO TMR 1-4811 class, ASSUMED) on the 48 V bus after F1 | ≤ 15 mA while the E-stop is pressed (69 mA on an ENA+ short) | present whenever the driver can energise the motor; independent of the Nucleo / USB. Bring-up alternative: the Nucleo 5V pin (works with the MCU held in reset, HG-10 c, but not with the Nucleo unpowered — then the driver holds, D-13, and no pulses exist) |

---

## 9. Grounding and shielding (R2 §6.5, R5 §5.2)

- **Single logic ground**: Nucleo GND = HX711 GND = input-cell GND = opto return side (PUL−, DIR−, ENA−, ALM−, PEND−). The PC ground enters through the USB/ST-LINK.
- **Isolation barrier**: the driver's 48 V GND, motor PSU 0 V and PE are **never** connected to the Nucleo GND; the HBS86H optos (≥ 500 MΩ) and the isolated +5V_CUT DC-DC (≥ 1.5 kV, D-42) keep the barrier. The optional DRV_PWR opto (48 V presence sense with LTV-817, §7.2) needs ≥ 3 mm creepage on a separate connector (R5 §5.2).
- Motor cable shielded, shield to driver PE at the driver end; encoder cable separate from the motor phases.
- Driver control cable: shielded twisted pairs (e.g. LiYCY 6×2×0.25 mm²), shield to the logic GND at the board end only.
- Load-cell shield (yellow) to the HX711 GND only. Keep the HX711 and its wires away from the PSU, the DC-DC and motor cables.
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

Executed by Validator E with Implementer A; results go into the check-list report. "Budget" = SRS §6 / requirement value. **v0.4 (CR-02, D-35 G5): no oscilloscope / logic analyser** — timing comes from the measurement image `HW_MEAS` through the measurement header (§6.1: OC = on-chip timer capture / DMA stamps / independent counter), from the PC (PC) or from a multimeter / caliper / dial indicator; the detailed procedures are the HG items of `FW_test_plan.md` §6 (map in the last column).

| # | Item | Method | Pass criterion / budget | Req. / HG |
|---|---|---|---|---|
| C-01 | Board identity and solder bridges SB13/14 ON, SB62/63 OFF, SB16/50 ON, SB54/55 OFF, SB46/52 OFF, SB51/56 ON (A4/A5 → PC1/PC0) | visual inspection, photo | as `pinout.md` §5 | SYS-007, FW-PLT-002 |
| C-02 | Clock source | GET_STATUS `CLK_FALLBACK`; PUL frequency at 50 kHz from the HW_MEAS probe (PWM-input period on J-PUL-A) and device clock vs PC over ≥ 600 s | no fallback; frequency error ≤ 0.1 % | FW-PLT-002 / HG-02 |
| C-03 | VCP link at 921 600 Bd | 10 min streaming soak at 80 Hz + 20 commands/s | 0 sequence gaps, 0 CRC errors (FW and PC counters) | IF-002, NFR-004 |
| C-04 | Flash erase vs IWDG and E-stop during SAVE | SAVE that forces a sector erase; press the E-stop during the erase | no IWDG reset, SAVE ≤ 2.5 s, NVM valid after reboot; ENA disabled ≤ 1 ms also during the erase (RAM-resident ISR, FW_design §5.11) | FW-NVM-002/003, SAF-FW-019 |
| C-05 | HX711 at 80 SPS on silicon | HW_MEAS: J-EVT = DOUT, J-AUX = PD_SCK; DOUT stamps vs DATA `t_us`, SCK-high via DWT (`_meas_dwt`), 10 000 reads; GET_STATUS measured rate | SCK-high ≤ 50 µs, read ≤ 60 µs, rate within ±1 % of the stamp median, timestamp vs DOUT edge ≤ 5 µs | FW-AFE-001/004, FW-TIM-001 / HG-05 |
| C-06 | Opto drive margin (3.3 V direct bring-up; inputs rated 5–24 V) and later the 5 V buffer | hold PUL (DIAG_MEAS STATIC_LEVEL, HW_MEAS image) and ENA (DISABLE) at the active level; VOH under load; 100 Ω series shunt → I_LED | bring-up: I_LED ≥ 6 mA and VOH ≥ 3.0 V, else fit the buffer first; **before calibration: buffer fitted (D-28), I_LED ≈ 10–13 mA** | SYS-009, SYS-011, OI-05, D-28 |
| C-07 | ENA enable/disable and settle | ENABLE/DISABLE commands; shaft holding torque by hand; ALM/PEND levels; J-ENA / J-PUL-A / J-DIR stamps: ENA edge vs the first PUL/DIR edge after ENABLE (and after a DRV_PWR return only if the optional presence sense is fitted, §7.2) | disabled = shaft free (no load!), enabled = holding; first edge ≥ `motion.ena_settle_ms` (500 ms) after the later of ENABLE and power return | FW-MOT-008, SAF-FW-024, A-04 / HG-07 |
| C-08 | PUL/DIR timing at the driver terminals | HW_MEAS probe in PWM-input mode on J-PUL-A at 50 kHz (min high / low), J-DIR edge → next PUL edge on reversals | high ≥ 10 µs, low ≥ 10 µs, DIR setup ≥ 20 µs on every reversal | FW-MOT-001 / HG-08 |
| C-09 | Step count integrity | 100 moves cross-checked with the independent counter (J-PUL-B, MT-2) | difference 0 (± 1 only with `pos_uncertain`) | SAF-FW-004 / HG-09 |
| C-10 | E-stop reaction (MCU channel + D-42 cut) | HW_MEAS probe: J-EVT = PA10 node, last PUL (J-PUL-A) and ENA (J-ENA) edge after the event, 100 trials with the J-STIM stimulus (220 Ω, bench procedure FW_test_plan §6.8) + 10 real presses; **MCU held in reset → press → shaft free by hand** (D-42); DMM points M-1…M-4 (§11.1); slow press (NO before / after NC) | last PUL edge ≤ 100 µs, ENA disabled ≤ 1 ms; motor de-energised by the NO contact with the MCU in reset; no FW fault from the forced ENA, release never moves | SAF-FW-005, SYS-006, D-42 / HG-10 a–e |
| C-11 | Limit reaction | HW_MEAS probe: J-EVT = PB0 / PC1 node vs last PUL edge, 100 stimulus trials + 10 real actuations each | ≤ 200 µs | SAF-FW-002 / HG-11 |
| C-12 | FW load-limit reaction | threshold set just above a known load; J-EVT = DOUT of the deciding sample vs last PUL edge | ≤ 200 µs | SAF-FW-002/008 / HG-12 |
| C-13 | PC STOP/HALT command and Pause/Break key | J-EVT = PA3 (RX) start bit of the frame's last byte vs last PUL edge; key → last edge from the PC timestamp and the device-time map | ≤ 2 ms; key → last edge ≤ 100 ms p95 | SAF-FW-002, NFR-003 / HG-13 |
| C-14 | Hang → IWDG | DIAG_MEAS HANG (main / tick / ISR1) while moving; `.noinit` heartbeat and last-PUL stamps read back after the reset | PUL stops ≤ 100 ms after the hang start, reset cause IWDG | SAF-FW-019 / HG-14 |
| C-15 | Reset under load (**D-33 e**, DEF-P1-05) | horizontal axis: spring specimen preloaded in **tension ≥ 98 N (10 kgf)**; MCU reset (NRST, power cycle of the Nucleo only, IWDG test image); dial indicator on the table; independent counter (J-PUL-B) | axis motion ≤ 0.01 mm (driver keeps holding, D-13); raw change within noise; no PUL edge from reset (counter 0) | SAF-FW-018 / HG-15 |
| C-16 | ALM / PEND levels of the PFDE clone, ALM reset | driver powered/unpowered, in position / moving, open- vs closed-loop setting; provoke an ALM (bench, e.g. supply under-voltage); reset by switching the 48 V PSU off / on (no RESET button, D-41) and, separately, try the ENA-toggle reset; ALM line chatter (wiggle the connector) | levels recorded, `drv.*_active_level` set; one ALM_CHANGED per change (chatter → at most one pair per `io.release_ms`, ALM is polled); new motion starts refused while ALM active and DRV_PWR on; running moves unaffected; power-cycle reset works, ENA-toggle result recorded | FW-SW-004, SAF-FW-026, D-28, SYS-009 |
| C-17 | Input wire-break | unplug each NC input (E-stop sense, START, END; the PA7 sense only if fitted); break the D-42 NO wire | NC inputs read active → stop + flag; the D-42 wire break is **not detectable** — recorded (R-10, mitigated by the periodic C-25 test) | SAF-FW-007 / HG-17 |
| C-18 | Main-loop and ISR budgets | GET_STATUS `loop_max_us`; `_meas_dwt` image (DWT statistics) at 50 kHz stepping + 80 Hz stream + 20 cmd/s; idle-loop counter CPU load | loop ≤ 1000 µs, step ISR ≤ 2 µs, CPU ≤ 15 %, windows masking levels 0–2 ≤ 1 µs, E-stop / limit ISR ≤ 1 µs | NFR-006, NFR-007 / HG-18 |
| C-19 | Driver DIP sheet | inspection of §10, all 8 switches incl. SW7/SW8 open/closed loop; first travel calibration result vs the expected steps/mm (160 at 800 p/rev, 800 at 4000 p/rev) | recorded | SYS-005, D-27 |
| C-20 | E-stop circuit (D-41 / D-42) | wiring inspection of §2.1 / §7: NC → PA10, NO → +5V_CUT → R_E → D2 → ENA+, D1 in the MCU ENA path, **no contactor**, panel label; the D-42 path contains no MCU-controlled element. K1_WELDED: **N/A** (no contactor; feature verified in the twin only) | as drawn | SYS-006, SYS-001 / HG-20 (HG-22 N/A) |
| C-22 | Driver drive through the buffer board (SYS-011) | before the first calibration: inspect the SN74ACT244 board; measure I_LED of PUL/DIR/ENA through it; repeat C-07/C-08 through the board | board installed; I_LED within the driver rating (≈ 10–13 mA); ENA/PUL/DIR timing as C-07/C-08 | SYS-011, SYS-009 |
| C-24 | Measurement chain self-test and loopback hygiene (CR-02) | J-STIM → J-EVT self-test of the probe and stamps; release image with the jumpers fitted: MH pins digital input without pull | probe / stamps consistent with the stimulus; no MH pin analog | SYS-009 / HG-29, HG-31 |
| C-23 | DRV_PWR during SAVE (D-33 f) — **optional** (only with the presence sense fitted and `drv.pwr_sense_enable` = 1) | SAVE forcing a sector erase; switch the 48 V PSU off during the erase | after the erase: DRV_PWR reaction ≤ erase time + 25 ms; NVM record valid; no IWDG reset; else N/A | (SAF-FW-024), FW-NVM-003 / HG-04 b |
| C-21 | Driver-power loss without E-stop — **optional** (presence sense fitted, `drv.pwr_sense_enable` = 1; else N/A) | during a slow jog, switch off the 48 V PSU with S0 released | stop and ENA disabled ≤ 25 ms after the PA7 change, NOT_ENABLED, HOMED = 0, EVENTs DRIVER_POWER (0) + STOPPED (DRV_POWER_LOST); ENABLE refused until power returns, first pulse ≥ 500 ms after the later of ENABLE and power return; PA7 toggles < 20 ms (bounce) ignored | (SAF-FW-024, FW-SW-005), FW-MOT-008 / HG-21 |
| C-25 | D-42 hardwired ENA cut, periodic | (i) MCU held in reset (NRST), press S0 → shaft free by hand, release → holding again only after the MCU runs and ENABLE is sent; (ii) DMM points M-1…M-4 (§11.1) | as stated; ENA loop currents within §2.1; PA4 never above 3.4 V | D-42 / HG-10 c, d |
| C-26 | CR-03 configuration (release image) | GET_ALL_PARAMS: `drv.pwr_sense_enable` = 0, `drv.k1_check_enable` = 0; E-stop held > 1 s | defaults as stated; only ESTOP latched (no K1_WELDED, no DRV_UNPOWERED) | D-41 / HG-32 |

### 11.1 Multimeter measurement points (D-35 G5 / D-41: DMM only)

| # | Point (vs logic GND unless stated) | Condition | Expected (ASSUMED values, §2.1) | HG |
|---|---|---|---|---|
| M-1 | voltage across R_E (68 Ω) → I = U / 68 Ω | E-stop pressed, MCU in reset | ≈ 0.7 V (≈ 10 mA; driver threshold per the clone, ≥ 7 mA) | HG-10 d |
| M-2 | ENA loop current: 100 Ω shunt temporarily in series with ENA− (or the R_E reading) | (a) MCU *enabled*, E-stop released; (b) MCU in reset, pressed; (c) MCU *disabled*, pressed | (a) ≈ 0 mA; (b) ≈ 8…10 mA (shunt lowers it); (c) ≤ 13 mA, no over-current at PA4 / buffer output | HG-10 d |
| M-3 | PA4 pin (CN8-3) and, stage 2, the buffer ENA output | every combination of M-2 | ≤ 3.4 V at PA4 (never the 5 V: D1 blocks); buffer output ≤ 5.1 V | HG-10 d |
| M-4 | ENA+ terminal | pressed / released + enabled / released + disabled | ≈ 4.0…4.6 V / ≈ 0 V / stage 1 ≈ 3.0 V, stage 2 ≈ 4.7 V | HG-10 d |
| M-5 | +5V_CUT at the DC-DC output; resistance 48 V GND ↔ logic GND (48 V bus off) | bus on / off | 4.75…5.25 V; ≥ 10 MΩ (isolation) | HG-20 |
| M-6 | PA10 pin (CN10-33) | E-stop released / pressed | ≤ 0.4 V / ≥ 2.3 V | HG-10, HG-17 |
| M-7 | PA10 node with J-STIM (220 Ω) fitted, switch unplugged | STIM idle (closed) / active (open) | ≈ 0.6 V (≤ 0.99 V) / ≈ 3.3 V | §6.8 P-4 |
| M-8 | I_LED of PUL / DIR (`STATIC_LEVEL`) and ENA (DISABLE) — C-06 | active level held | bring-up ≥ 6 mA; buffer 10…13 mA | HG-23 |
| M-9 | PA7 (only with the optional sense, §7.2) | 48 V on / off | ≤ 0.4 V / ≈ 3.3 V | HG-21 |

---

## 12. Connection inspection list (SYS-001 / SYS-007)

Every connection of SRS §3.1 must appear here with its terminal: PUL/DIR/ENA (§2), ALM/PEND (§3), HX711 + cell (§4), START/END (§5), PAUSE and the red E-stop on the operator panel (§6), measurement header MH (§6.1, bench only; J-STIM 220 Ω), E-stop NC → PA10 and NO → ENA cut with +5V_CUT, R_E, D1, D2 (§2.1, §7), optional DRV_PWR presence sense (§7.2), 48 V supply, Nucleo supply (§8), grounds/shields (§9), motor + encoder (driver manual), USB to the PC. Terminal numbers are filled in when the panel/shield is built (Stage 1: Nucleo header pins from `pinout.md`).

---

## 13. Change history

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-10-03 | First draft: direct 3.3 V common-cathode drive (D-17; bring-up only — PFDE HBS86H inputs rated 5–24 V, SN74ACT244 buffer required before calibration, D-28), ALM/PEND cells (semantics vs open/closed loop, D-27), HX711 at 3.3 V with RATE on PB5, NC limits with RC, STOP NC / PAUSE NO, E-stop R5 Option A with sense contact and DRV_PWR (accepted D-28), supplies (SDR-480-48), grounding, 8-switch driver setup sheet, SYS-009 check list. |
| 0.2 | 2026-10-03 | Aligned to SRS v0.3 / ICD v0.3 / D-29 / D-30:<br>• DRV_PWR (PA7) is **required** (20 ms filter, any-cause power loss → stop / ENA disabled / NOT_ENABLED / not homed ≤ 25 ms; K1_WELDED after `drv.k1_weld_ms` 200 ms);<br>• stale `home.switch` reference removed (START-only homing, D-29 b; END during HOME = HOME_WIRING);<br>• ALM start-block scope (SAF-FW-026) and "ALM is never a step-loss indicator" (D-27);<br>• C-20 now tests K1_WELDED; new C-21 driver-power loss without E-stop. |
| 0.5 | 2026-10-04 | M2 close-out, CR-03 (D-41 / D-42 / D-43 d, e; REQ-A-M2-07): contactor K1, RESET button, KT trip relay and the K1-aux DRV_PWR sense removed; §2.1 new — ENA diode OR of the MCU drive and the **hardwired E-stop NO cut** from an isolated 48→5 V supply (no contention, PA4 never sees 5 V, numbers per condition); §7 rewritten (MCU/FW stop + D-42 cut, behaviour, residual risks, CR-03 defaults `drv.pwr_sense_enable` = 0 / `drv.k1_check_enable` = 0, optional 48 V presence sense §7.2); §6 / §6.1 (J-STIM 220 Ω instead of 1 kΩ, §6.8 bench procedure); §8 / §9 (+5V_CUT, isolation); check list C-07, C-10, C-16, C-17, C-20, C-21, C-23 revised, C-25 (D-42 periodic test) and C-26 (CR-03 defaults) new; §11.1 multimeter points M-1…M-9. |
| 0.4 | 2026-10-04 | M2: CR-01 — §6 operator panel = red E-stop + PAUSE, STOP/BREAK button and its PC7 input removed (C-11, C-17 reworded); §6.1 measurement header MH (CR-02, D-40 c); check list without scope / logic analyser (C-02, C-05, C-07…C-15, C-18 re-worded to the HW_MEAS / PC methods), new C-24, C ↔ HG map in the last column (REQ-A-M2-02). |
| 0.3 | 2026-10-03 | Final P1 round:<br>• C-15 reworded per D-33 e / DEF-P1-05 (horizontal axis, spring specimen ≥ 98 N in tension);<br>• SYS-009 items added per OBS-P1-12: C-07 settle after DRV_PWR return, C-16 ALM reset by power cycle / ENA toggle + chatter, C-22 buffer board, C-23 DRV_PWR during SAVE (C-20/C-21 already cover K1_WELDED and the E-stop/PSU paths);<br>• ALM is polled (OBS-P1-10). Mapping to FW_test_plan HG items: C-15 = HG-15, C-16 = HG-16/24, C-20 = HG-20/22, C-21 = HG-21, C-22 = HG-23, C-23 = HG-04. |
