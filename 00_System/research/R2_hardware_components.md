# R2 - Hardware components (HBS86H, 86HS2140, Keli DEF 200 kg, HX711, NUCLEO-F446RE)

Project: Bird Bend Stand. Author role: Researcher (R2). Date: 2026-10-03. Phase: P0.
Scope: electrical/timing facts the FW, SW and HW-pinout work needs, with numbers, sources and status
marks. Pinout/board init of the Stefan reference is R1; Thrust_Stand_HAW reuse is R3; motion/calibration
methods are R4. This document only cross-references them.

Status marks used on every fact:
- **VERIFIED** - read in the cited primary/secondary source during this research (source + section given).
- **ASSUMED** - engineering estimate, family-typical value or inference; must be checked on the real hardware.
- **UNKNOWN** - not found; listed as an open question (`Q-R2-nn`).

Text was extracted from the vendor PDFs with a stdlib Python dumper (scratchpad, not part of the repo); where
the extraction lost table alignment it is said so.

---

## 0. Key findings (read this first)

1. **"HBS86H" is two different products in the field.** The genuine Leadshine HBS86H (datasheet 2012,
   [S1]) and the JMC 2HSS86H-KH whose manual is widely re-published as "HBS86H Manual" ([S2], [S3]) differ
   in DIP-switch functions, minimum pulse width (10 µs vs 2.5 µs), and ALM/PEND default polarity. The FW
   must make all of these parameters (pulse width, DIR setup, ENA/ALM/PEND polarity), and the PO must tell
   us which unit is installed (Q-R2-01).
2. **Driver inputs are opto LEDs with an internal ~270 Ω series resistor, 7-16 mA (typ 10 mA)** [S1].
   A 3.3 V GPIO gives only ~6-7 mA -> marginal. **A 3.3 V push-pull pin in "common-anode at 5 V" wiring never
   switches the opto fully off** (~2.6 mA leak when the pin is "high"). Use a 5 V buffer (74ACT244 /
   74AHCT125 class) or differential driver; see §6.1.
3. **Safe step timing for every known variant:** PUL high >= 10 µs, PUL low >= 10 µs (=> f_max <= 50 kHz),
   DIR stable >= 20 µs before the active PUL edge, ENA asserted >= 5 µs before DIR and in practice >= 200 ms
   before the first pulse (ASSUMED). Driver absolute max input 200 kHz [S1].
4. **ENA default meaning: opto current OFF = drive ENABLED** ("usually left unconnected (enabled)") [S1].
   A broken ENA wire or an MCU in reset leaves the drive enabled (holding, no motion because no pulses).
   "Motor disabled by default" needs either an actively driven ENA line or a re-configured ENA polarity
   (tuning software). When disabled, the motor is unpowered and the load can back-drive it (ASSUMED,
   generic stepper-drive behaviour).
5. **ALM / PEND are isolated open-collector phototransistor outputs, 20 mA @ 24 V max** [S1]. Leadshine
   default ALM: conducting in normal operation, open on fault (fail-safe with a pull-up); JMC default is the
   opposite [S2]. PEND = "actual position equals command" - usable for settle detection.
6. **Following-error alarm** trips when position error > limit (JMC default 1000 x 10 counts [S2]); red LED
   blinks 7 times (Leadshine [S1]) / 5 times (JMC [S2]). After any ALM the FW must stop pulses, latch a fault,
   and treat the axis position as unknown (needs re-zero). Reset method: UNKNOWN per variant (power cycle is
   the documented one; ENA toggle ASSUMED).
7. **Motor "86HS2140" = 86HS2140-06 class NEMA34 closed-loop motor: 8.2 N·m, 6.0 A, 140 mm, ~3200 g·cm²,
   4.2 kg, 1000-line encoder (4000 counts/rev)** (VERIFIED on a vendor page [S6], encoder line count ASSUMED).
   Closest Leadshine twin: 86HS80-EC-1000 (8.0 N·m, 6.0 A, 0.44 Ω, 3.73 mH, 2580 g·cm²) [S1].
8. **The motor + any screw can exceed the load cell's safe overload by 2-5x** (8.2 N·m on a 5 mm ball screw
   ≈ 9.3 kN vs 200 kg x 120 % = 2.35 kN). The FW load limit (checked on every HX711 sample) is the only thing
   protecting the load cell; HX711 saturation codes must count as overload.
9. **Keli DEF is 3.0 mV/V (DEE is 2.0 mV/V)** [S8][S9][S10]; 400 Ω in / 352 Ω out, excitation 10-12 V
   recommended (HX711 boards excite at AVDD ≈ 4.3 V - fine, ratiometric), safe overload 120 % (conservative of
   two sources), IP65 for <= 1000 kg, wires Red EXC+, Black EXC-, Green SIG+, White SIG-, (Yellow shield).
10. **HX711 numbers for this system (3.0 mV/V, gain 128, 80 SPS):** 32 212 counts/kg (ratiometric, AVDD-
    independent), full scale 200 kg = 6 442 451 counts, saturation at ≈ 260 kg (130 % FS); noise from the
    datasheet 90 nV rms -> ≈ 45 counts ≈ **1.4 g rms (≈ 9 g p-p) at AVDD 4.3 V**; 10 s average (800 samples)
    -> ≈ 0.05 g white-noise limit, in practice dominated by creep (0.02 % FS / 30 min = 40 g).
11. **HX711 RATE (10/80 SPS) is a pin, not a register.** Software can only change it if RATE is wired to an
    MCU GPIO (board modification). Many boards ship strapped to 10 SPS. FW must *measure* the actual DOUT
    period and report a mismatch against the configured rate. Gain/channel (A128/A64/B32) is fully software
    controlled (25/27/26 pulses). After any rate/gain change or power-down: discard 4 samples (50 ms at 80 SPS).
    SCK high must stay < 50 µs (> 60 µs = power-down) -> SCK-high must be IRQ-protected.
12. **USART baud at 921600 on F446 at 180 MHz: -0.35 % for USART2 (APB1 45 MHz) and identical for USART1/6
    (APB2 90 MHz)** - APB2 gives no accuracy advantage here. At Stefan's clock (HSI 84 MHz, APB1 42 MHz) the
    error is **-0.93 % plus HSI tolerance (±1 % at 25 °C)** -> use HSE bypass (8 MHz ST-LINK MCO). 1 Mbaud would
    be exact (0 %) on both APBs. The data rate needed (~2 kB/s at 80 SPS) is <3 % of the link capacity.
13. **USART2 DMA: RX = DMA1 Stream5 Ch4, TX = DMA1 Stream6 Ch4** (VERIFIED in Stefan's CubeMX output).
14. **Flash NVM:** F446RE sectors 0-3 = 16 KB, 4 = 64 KB, 5-7 = 128 KB. Recommend two-sector ping-pong in
    sectors 6+7 (0x08040000, 0x08060000) with append-only CRC records, writes only while the motor is stopped
    (128 KB erase typ 1 s, max 2 s, CPU stalls on flash fetch - ASSUMED F4-family values).
15. **Reference-code finding (for R1 / Implementer A):** Stefan runs SYSCLK 84 MHz (HSI/16 x 336 / 4, APB1 /2,
    `Stefan/FW/stanok/Core/Src/main.c:136-158`) but `hw_stm32.hpp:31-33` assumes TIM2 = 90 MHz -> with PSC 9-1
    the step timer runs at 9.33 MHz, not 10 MHz: **all Stefan speeds/pulse widths are 6.7 % off** if reused.
    Stefan also drives ENA from PA4, which is a TTa (not 5 V tolerant) pin.

---

## 1. HBS86H hybrid servo (closed-loop stepper) driver

### 1.1 Variants and sources

| Variant | Source | Notes |
|---|---|---|
| Leadshine HBS86H (genuine), datasheet "Hybrid Servo Drive HBS86 Datasheet", 7 pages, PDF dated 2012-01 | [S1] (mirror of leadshineusa.com/UploadFile/Down/HBS86Hd.pdf, whose TLS cert has expired) | Primary source for this section. "20-70VAC or 30-90VDC, 8.2A Peak". |
| Leadshine HBS86H newer text (Mecheltron datasheet) | [S4] - only seen as WebSearch snippets of cnc.info.pl / Scribd (HTTP 403 / no body) | States "3.3-5V when PUL-HIGH", "pulse width ... longer than 1.2 µs", "DIR ... ahead of PUL ... 5 µs". Not read directly -> treat as ASSUMED. |
| JMC 2HSS86H-KH (Shenzhen Just Motion Control) | [S2] official JMC manual; [S3] the same text re-labelled "HBS86H Hybrid Stepper Servo Drive Manual" (hardware-cnc.nl), also on manualslib as "Leadshine HBS86H" | Many units sold as "HBS86H" are this design (HISU tuner, P1-P20 parameters). |
| Other clone with brake output | linuxcnc forum attachment (WPS-generated, CID fonts - not decodable here) | Only the outline was readable: adds brake output, SW1 single/double pulse, SW7 command smoothing. |

**Status:** which one is installed is **UNKNOWN** -> Q-R2-01 (photo of label and DIP block).

### 1.2 Electrical specification

| Parameter | Leadshine [S1] | JMC 2HSS86H-KH [S2] | Status |
|---|---|---|---|
| Supply | 30 / 60 (typ) / 100 VDC, or 20-70 VAC; "20-63VAC or 30-90VDC recommended, leaving rooms for voltage fluctuation and back-EMF" | 24-70 VAC or 30-100 VDC | VERIFIED |
| Output current | 0 - 8.2 A peak | 6 A, 20 kHz PWM | VERIFIED |
| Pulse input frequency | 0 - 200 kHz | 200 k max | VERIFIED |
| Logic signal current | 7 / 10 / 16 mA (min/typ/max) | - | VERIFIED |
| Isolation resistance | >= 500 MΩ | - | VERIFIED |
| Protections | over-current, over-voltage, position following error | over-current 12 A ±10 %, over-voltage 130 V, position error (settable) | VERIFIED |
| Encoder supply (to motor encoder) | +5 V @ 100 mA max | +5 V, 80 mA max | VERIFIED |
| RS232 tuning port | 57.6 kbps (JMC); Leadshine: "configure close-loop current, open-loop current, position following error limit" | same | VERIFIED |
| Operating temperature (heat sink) | 70 °C max; ambient 0-50 °C | 70 °C max | VERIFIED |
| Weight | 580 g | ~580 g, 150 x 97.5 x 53 mm | VERIFIED |

### 1.3 PUL / DIR / ENA inputs - electrical

Facts:
- Inputs are opto-isolated, each with a **270 Ω series resistor drawn inside the drive** in the typical-connection
  diagrams of both [S1] ("Typical Connections") and [S2] §5.1-5.3. VERIFIED.
- Leadshine levels: "4-5V when PUL-HIGH, 0-0.5V when PUL-LOW"; external resistor "R=0 if VCC=5V; R=1K
  (Power>0.125W) if VCC=12V; R=2K (Power>0.125W) if VCC=24V" [S1 Control Signal Connector + Typical
  Connections]. VERIFIED. (Newer Leadshine text: 3.3-5 V high [S4] - ASSUMED.)
- JMC: "Compatible with 5V or 24V" on each input [S2 §3.2] (no external resistor needed for 24 V on that
  variant); sequence chart labels "High level > 3.5V". VERIFIED.
- Wiring modes supported: common anode (controller sinks PUL-/DIR-/ENA-), common cathode (controller sources
  PUL+/DIR+/ENA+), differential [S1][S2 §5.1-5.3]. VERIFIED.

Derived opto current through the 270 Ω (LED Vf ≈ 1.1-1.2 V, ASSUMED):

| Drive method | I_LED | Verdict |
|---|---|---|
| 5 V supply, sink via open-drain/OC (VOL 0.4 V) | (5 - 0.4 - 1.2)/270 ≈ **12.6 mA** | in the 7-16 mA window |
| 3.3 V push-pull, common cathode (VOH ≈ 3.1-3.3 V) | (3.3 - 0.2..0.4 - 1.1..1.2)/270 ≈ **6.3-7.4 mA** | at/below 7 mA min - marginal, not recommended |
| 5 V anode + 3.3 V push-pull pin "high" (off state) | (5 - 3.3 - ~1.0)/270 ≈ **2.6 mA still flowing** | opto may stay partially on -> **forbidden** |
| 5 V anode + MCU FT pin in open-drain | on: ≈ 12 mA (above the 8 mA at which F446 guarantees VOL 0.4 V, below 25 mA abs max); off: pin pulled to ≈ 4 V | works electrically, only on FT pins (not PA4/PA5); no protection from the noisy cable -> fallback only |
| 5 V buffer (74ACT244 / 74AHCT125 / 74HCT244, VIH 2.0 V accepts 3.3 V), common cathode | ≈ 12-13 mA | **recommended** |
| AM26LS31-class differential driver (5 V) into PUL+/PUL- | ≈ 7-10 mA | best for cables > 2 m |

**Conclusion:** 3.3 V MCU drive is not sufficient per the 2012 Leadshine spec; use a 5 V buffer. Add a
pull-down (10 kΩ) at each buffer input so the lines are defined while the MCU is in reset/boot (PUL idle,
DIR fixed, ENA in the chosen safe state). ASSUMED design, standard practice.

### 1.4 Timing

| Symbol | Requirement | Leadshine [S1] | JMC [S2 §5.5] | Newer Leadshine [S4] | **FW value to use** |
|---|---|---|---|---|---|
| t_PUL_H | pulse width | "longer than 10 µs" | t3 >= 2.5 µs | > 1.2 µs | **10 µs** (param, min 2.5) |
| t_PUL_L | low level width | (implied 10 µs) | t4 >= 2.5 µs | - | **>= 10 µs** |
| f_max | input frequency | 200 kHz | 200 k | 200 kHz | **50 kHz cap** (param; 200 kHz only at exactly 2.5/2.5 µs on JMC) |
| t_DIR_SU | DIR before active PUL edge | >= 5 µs | t2 >= 6 µs | >= 5 µs | **20 µs** |
| t_DIR_H | DIR hold after edge | not specified | not specified | - | **>= 10 µs** after the last pulse before reversing (ASSUMED) |
| t_ENA_SU | ENA before DIR | not specified | t1 >= 5 µs | - | **>= 5 µs electrical; >= 200 ms before first pulse** (ASSUMED: drive energises / aligns; verify with PEND/ALM) |

All "Leadshine/JMC" cells VERIFIED from the cited sections; FW column = recommendation.
Active edge: Leadshine "each rising or falling edge active (software configurable)"; JMC SW1 selects the
active edge [S2 §6.1]. Because the polarity of the edge is configurable, the FW should only change DIR while
PUL is idle and keep PUL idle level a parameter (default: idle = no LED current). VERIFIED (configurability) /
ASSUMED (FW rule).

Reference values (Stefan, R1): `pulse_us = 3` default (`Stefan/FW/stanok/App/Inc/axis.hpp:57`) - OK for JMC
(>= 2.5 µs) but **violates the 2012 Leadshine 10 µs** requirement.

### 1.5 ENA polarity and behaviour

- Leadshine: "In default, high level (NPN control signal) for enabling the driver and low level for disabling
  the driver. Usually left UNCONNECTED (ENABLED). ... PNP and Differential control signals are on the
  contrary ... The active level of ENA signal is software configurable." [S1 Control Signal Connector].
  Interpretation: **no LED current = enabled, LED current = disabled** in all three wiring modes. VERIFIED
  (text) / interpretation ASSUMED (consistent across the three modes).
- JMC: parameter P13 "Enable signal level" (0 = low, 1 = high), default 0; "Usually, ENA+ and ENA- are NC"
  [S2 §5.5, §10]. VERIFIED.
- Disabled drive: motor phases unpowered, shaft free, a back-drivable screw (ball screw) under specimen
  load will move. ASSUMED (generic; not stated in [S1]/[S2]).
- After re-enable the closed-loop drive takes the current encoder position as the new command (no jump).
  ASSUMED. The FW step counter is then no longer trustworthy if the load moved the axis -> clear "zeroed".

### 1.6 Pulses-per-revolution DIP settings

Leadshine HBS86H [S1 "DIP Switch Settings"] - SW1-SW4 microstep, SW5 direction, SW6 motor type:

| Pulses/rev | SW1 | SW2 | SW3 | SW4 |
|---|---|---|---|---|
| Software configured ("Default 200" as printed) | on | on | on | on |
| 800 | off | on | on | on |
| 1600 | on | off | on | on |
| 3200 | off | off | on | on |
| 6400 | on | on | off | on |
| 12800 | off | on | off | on |
| 25600 | on | off | off | on |
| 51200 | off | off | off | on |
| 1000 | on | on | on | off |
| 2000 | off | on | on | off |
| 4000 | on | off | on | off |
| 5000 | off | off | on | off |
| 8000 | on | on | off | off |
| 10000 | off | on | off | off |
| 20000 | on | off | off | off |
| 40000 | off | off | off | off |

- SW5: ON = positive direction, OFF = negative. SW6: ON = 86HS40-EC-1000 (4 N·m), OFF = 86HS80-EC-1000 (8 N·m).
  VERIFIED (text extraction; row/column order reconstructed - the 16 values and 16 switch patterns match one
  to one).
- JMC 2HSS86H-KH [S2 §6]: SW1 = active edge, SW2 = direction (off CCW / on CW), **SW3-SW6 = microstep with the
  same 16 values in the same order**; all ON = internal value = P20 x 50, P20 default 8 -> **400 pulses/rev**.
  VERIFIED.
- **Shipping default of the installed unit: UNKNOWN** (Q-R2-02). Recommendation: never use the all-ON
  "software" position; set an explicit value <= 4000 (the encoder has 4000 counts/rev with a 1000-line encoder,
  so finer settings do not add real position resolution), e.g. **1600 or 4000**, and calibrate steps/mm.

### 1.7 ALM and PEND outputs

| Output | Leadshine [S1 "Stator Signal Connector"] | JMC [S2 §3.1, §10] |
|---|---|---|
| Type | OC (optocoupler) output, "can sink or source 20mA current at 24V" | optocoupler output transistor |
| ALM default | "resistance between ALM+ and ALM- is low impedance in normal operation and become high when HBS86 goes into error"; software configurable | P10 "Alarm level" default 0: "transistor is cut off when the system is in normal working ... when it comes to fault ... becomes conductive" (**opposite**) |
| ALM sources | over-voltage, over-current, position following error | over-current, voltage reference, parameter upload, over-voltage, position error |
| PEND | "active when the difference between the actual position and the command position is zero"; "active at high impedance" | P14 "Arrival level" default 1 (conductive when arrived, ASSUMED from "1 means opposite to 0") |

All VERIFIED (text). MCU wiring: ALM+ (collector) -> MCU input with 4.7 kΩ pull-up to 3.3 V, ALM- -> MCU GND
(the output is isolated from the drive). Low level = phototransistor Vce(sat) (~0.2-0.4 V, ASSUMED) < F446 VIL.
Recommended configuration: ALM conducting = OK (Leadshine default) so a broken wire reads as "alarm"
(fail-safe). FW parameters: `drv.alm_active_level`, `drv.pend_active_level`.

### 1.8 Protections, alarm behaviour, reset

| Indication | Leadshine [S1 "Protection Indications"] | JMC [S2 §7] (red LED, 0.8 s flash, 2 s interval) |
|---|---|---|
| Over-current | 1 blink / 5 s | 1 flash |
| Voltage reference error | - | 2 |
| Parameter upload error | - | 3 |
| Over-voltage | 2 blinks | 4 |
| Position following error | 7 blinks | 5 |

- Following-error limit: JMC P16 range 0-3000, unit x10, default 1000 -> 10 000 (units: encoder counts,
  ASSUMED -> 2.5 rev at 4000 counts/rev). Leadshine: configurable via tuning software, default UNKNOWN.
- Behaviour in alarm: drive goes into error mode, ALM output switches [S1][S2]; motor current off (ASSUMED).
- Reset: not documented in [S1]/[S2] beyond troubleshooting. Power cycle = certain; ENA toggle = ASSUMED/
  UNKNOWN per variant (Q-R2-02, test on the bench).
- JMC troubleshooting [S2 §11.3]: red LED after a small rotation = wrong phase wiring or wrong motor/encoder
  parameters; following-error alarm when "the frequency of the pulse signal is too fast". VERIFIED.
- Bend-stand meaning: pushing a stiff specimen beyond the motor torque -> following-error alarm -> motor
  goes free -> the specimen springs the axis back. The FW load limit must trip long before (see §7.3).

FW rules (recommendation): on ALM active -> stop PUL generation in hardware (timer off), set ENA per safe-state
param, latch `FAULT_DRIVER_ALARM`, clear the "zeroed" flag, report to PC; "clear fault" command only succeeds
when ALM is inactive. Do not send pulses after power-up until ALM is inactive.

### 1.9 Tuning notes

- "No Tuning", "No adjustment in general applications" [S1][S2 §2]. Tuning via RS232 (RJ-type connector,
  57.6 kbps) with Leadshine "HBS tuning software"/STU or JMC "HISU" hand-held unit. VERIFIED.
- JMC parameters [S2 §10] (defaults): P1 current-loop Kp 1000, P2 Ki 100, P3 damping 100, P4 position Kp 1300,
  P5 position Ki 250, P6 speed Kp 50, P7 speed Ki 10, P8 open-loop current 4.5 A, P9 closed-loop current 2.0 A
  ("actual current = open loop + close loop"), P10 alarm level 0, P12 stop-lock 0, P13 enable level 0,
  P14 arrival level 1, P15 encoder 0 = 1000 lines / 1 = 2500, P16 position-error limit 1000 (x10), P18 motor
  type 4, P19 speed smoothness 0 (0-10, "the larger the value, the smoother"), P20 user p/r 8 (x50). VERIFIED.
- Keep speed smoothing (P19 / "command smoothing" SW7 on other clones) at 0: it adds command lag, i.e. the
  motor keeps moving briefly after the FW stops pulses (E-stop latency). ASSUMED.
- Encoder line setting (1000/2500) and motor type must match the motor, otherwise "red alarm light on after
  running a small angle" [S2 §11.3].

---

## 2. Motor "86HS2140"

| Property | 86HS2140-06 [S6] | Leadshine 86HS80-EC-1000 [S1] | Status |
|---|---|---|---|
| Frame | NEMA34 (86 mm), 2-phase, 1.8° | NEMA34, 1.8° | VERIFIED |
| Holding torque | 8.2 N·m | 8.0 N·m | VERIFIED |
| Rated current | 6.0 A | 6.0 A | VERIFIED |
| Phase resistance | 0.5 Ω | 0.44 Ω | VERIFIED |
| Phase inductance | 0.7 mH as published (**implausible**, typical for this size 3-4 mH) | 3.73 mH | [S6] value doubtful -> ASSUMED ≈ 3.7 mH |
| Rotor inertia | 3200 g·cm² | 2580 g·cm² | VERIFIED |
| Length / weight | 140 mm (another listing: 143 mm) / 4.2 kg | - / 3.8 kg | VERIFIED |
| Encoder | 6-wire incremental A/B (+5V, GND) [S6]; vendor series pages say 1000 lines | 1000 lines | 1000 lines ASSUMED -> 4000 counts/rev quadrature (drive counts x4 [S2 §9]) |

Naming: 86 = frame, HS = hybrid servo, 2 = 2-phase, 140 = body length, -06 = 6 A (ASSUMED from the series
86HS2100-05 / 86HS2140-06 / 86HS2180-06 [S6]). Exact label: Q-R2-04.

Torque-speed: no curve found (UNKNOWN). Estimate of the corner speed where torque starts to fall (I·ωL ≈ V,
50 pole pairs, L 3.7-4.0 mH, 6 A): ≈ 380-410 rpm at 48 V, **≈ 560-600 rpm at 70 V**, ≈ 720-770 rpm at 90 V
(ASSUMED). Practical max with HBS86H ≈ 1000-1200 rpm at roughly half torque (ASSUMED). A bend stand at
<= 10 mm/s on a 4-10 mm pitch screw runs at 60-150 rpm -> full torque available.

---

## 3. Keli DEF 200 kg S-type load cell

| Parameter | Value | Source / status |
|---|---|---|
| Type | S-beam, tension & compression, alloy steel (nickel plated) or stainless | VERIFIED [S8][S9] |
| Capacities in series | 50 ... 750 kg, 1 ... 5 t (200 kg included) | VERIFIED [S8][S10] |
| Rated output | **DEF: 3.0 ± 0.003 mV/V** (DEE: 2.0 ± 0.003 mV/V) | VERIFIED [S9][S10]; installed cell's certificate: Q-R2-06 |
| Accuracy class | C3 (OIML R60) [S10]; "0.03 / 0.05" [S8] | VERIFIED |
| Non-linearity | ±0.03 % FS (0.05 % FS lower class) -> 60 g @ 200 kg | VERIFIED [S8] |
| Hysteresis | ±0.03 % FS -> 60 g | VERIFIED [S8] |
| Creep (30 min) | ±0.02 % FS -> 40 g | VERIFIED [S8] |
| TC zero / TC span | ±0.02 % FS / 10 °C each -> 40 g / 10 °C | VERIFIED [S8][S10] |
| Zero balance | ±1 % FS (-> up to ±2 kg-equivalent offset, ≈ ±64 k counts) | VERIFIED [S10] |
| Input / output impedance | 400 ± 20 Ω / 352 ± 3 Ω | VERIFIED [S8][S9][S10] |
| Insulation | > 5000 MΩ | VERIFIED |
| Excitation | 10-12 V recommended, 15 V max | VERIFIED [S8][S10] |
| Temperature | compensated -10...+40 °C, service -30...+70 °C [S8]; [S10] says -40...+50 °C | VERIFIED (sources differ) |
| Safe overload | **120 % FS** [S10] / 150 % FS [S8][S9] -> design with **120 % = 240 kg = 2354 N** | VERIFIED, conservative choice |
| Ultimate (breaking) overload | 150 % FS [S10] / 200 % FS [S8] -> design with **150 % = 300 kg** | VERIFIED |
| Protection | IP65 for 100-1000 kg (IP68 for larger) [S10]; "IP65/IP68" [S8][S9] | VERIFIED |
| Cable | 4-core, Ø5 mm, 5 m | VERIFIED [S9][S10] |
| Wire colours (Keli) | **Red = EXC+, Black = EXC-, Green = SIG+, White = SIG-, Yellow = shield** | VERIFIED [S9] |

Notes:
- 4-wire cell, no sense leads: the span is calibrated with the 5 m cable - do not shorten it (span/TC shift).
  ASSUMED (standard 4-wire practice).
- HX711 mapping: Red -> E+, Black -> E-, Green -> A+, White -> A-, shield -> GND at the HX711 end only.
  Sign: S-cells are usually positive in tension; if inverted, swap Green/White or let calibration carry a
  negative K (SW must accept K < 0). ASSUMED.
- Excitation from an HX711 board is AVDD ≈ 4.3 V (green board) -> 12.9 mV at 200 kg. Lower than 10 V is fine
  (ratiometric) but halves signal vs noise. Bridge common mode = AVDD/2 = 2.15 V, inside the HX711 input
  common-mode range AGND+1.2 ... AVDD-1.3 V = 1.2 ... 3.0 V. At AVDD = 3.0 V the window is 1.2 ... 1.7 V for
  a 1.5 V bridge mid-point - still inside but tight. VERIFIED (datasheet range [S11]) / calculation.

---

## 4. HX711

Primary source [S11] (Avia HX711 datasheet, Table 1-3, Fig. 2-3). Most facts were already verified in
Thrust_Stand_HAW R3 §1 ([S12], read-only) and re-checked here against [S11].

### 4.1 Facts

| Item | Value | Status |
|---|---|---|
| Supply | AVDD and DVDD 2.6-5.5 V, VSUP (regulator) 2.7-5.5 V; "DVDD should be the same power supply as the MCU" | VERIFIED [S11] |
| Full-scale input | ±0.5·AVDD/GAIN -> gain 128: ±19.5 mV @ 5 V, ±16.8 mV @ 4.3 V | VERIFIED [S11 Table 2] |
| Output | 24-bit two's complement, saturates at 0x800000 / 0x7FFFFF "until the input signal comes back" | VERIFIED |
| Rate | RATE pin: 0 = 10 SPS, 1 (DVDD) = 80 SPS (internal osc, "typical"); external clock: fclk/1 105 920 or fclk/138 240 | VERIFIED |
| Settling | 400 ms (RATE 0) / 50 ms (RATE 1) "from power up, reset, input channel change and gain change" = **4 conversion periods** | VERIFIED; after a RATE change: not specified, assume 4 periods of the new rate (ASSUMED) |
| Noise (gain 128) | 50 nV rms @ 10 SPS, 90 nV rms @ 80 SPS | VERIFIED |
| Drift (gain 128) | offset ±6 nV/°C, gain ±5 ppm/°C | VERIFIED |
| DOUT | high = not ready; low = data ready; 25th SCK pulse pulls DOUT high again | VERIFIED |
| Gain/channel | 25 pulses = A/128, 26 = B/32, 27 = A/64 - applies to the **next** conversion; "not less than 25 or more than 27 within one conversion period" (Fig. 2 mislabels the 27-pulse row "CH.B" - Table 3/text say A/64) | VERIFIED |
| T1 | DOUT falling -> SCK rising >= 0.1 µs | VERIFIED |
| T2 | SCK rising -> DOUT valid <= 0.1 µs (sample after the falling edge) | VERIFIED |
| T3 | SCK high 0.2 µs min, 1 µs typ, **50 µs max** | VERIFIED |
| T4 | SCK low 0.2 µs min, 1 µs typ, no max | VERIFIED |
| Power-down | SCK low->high and high **> 60 µs** -> power-down; SCK low -> reset, input/gain back to **A/128** | VERIFIED |
| Supply current | analog 1.4 mA normal, digital 0.1 mA | VERIFIED |
| Digital VIH/VIL | **not specified** in the datasheet | UNKNOWN |

### 4.2 What "AFE sample-rate config" can mean (PO requirement FW-1 / FW-4, decision D-04)

The rate is a **hardware strap**; there is no register. Options:

| Option | What the parameter does | HW change | Recommendation |
|---|---|---|---|
| A. Declared rate | `afe.rate_sps` (10/80) is stored and reported; FW measures the DOUT-ready period and raises `AFE_RATE_MISMATCH` if it differs by > 20 % | none | **minimum, always implement** (cheap self-check; catches a board strapped to 10 SPS) |
| B. GPIO-driven RATE | FW drives the RATE pin from a GPIO, discards 4 samples after a change | lift pin 15 / move the 0 Ω selector, wire to an MCU pin, 10 kΩ pull-down | **recommended if the PO allows a board modification** (Q-R2-07) |
| C. External clock on XI | arbitrary rates (fclk/138 240), exact 80 SPS with 11.0592 MHz | XI is grounded on boards | not recommended (experimental) |
| D. Output decimation | FW reports every N-th / averaged sample (output data rate <= 80 SPS) | none | optional, independent of A/B |

Gain/channel is a real software parameter: `afe.gain` ∈ {A128 (default, D-04), A64, B32}.

### 4.3 Board variants

| Board | RATE as shipped | AVDD / excitation | Logic supply | Status |
|---|---|---|---|---|
| Generic green "HX711 module" (S8550 + divider) | pin 15 tied to GND by PCB trace = **10 SPS**; lift pin 15 and tie to DVDD (or 10 kΩ to VCC) for 80 SPS | ≈ 4.3 V with VCC = 5 V (divider ≈ 8.2 k / 20 k: 1.25 V x 28.2/8.2 = 4.30 V; changing the 20 k to 11.5 k gives 3.0 V) | single VCC = DVDD | rate strap VERIFIED in forum reports [S13]; divider values ASSUMED (back-calculated) |
| Boards with a "10 Hz / 80 Hz" 0 Ω selector | one position populated (often 10 Hz) - move the 0 Ω resistor | board-specific | single VCC | VERIFIED [S14] |
| SparkFun SEN-13879 | solder jumper closed = RATE to GND = 10 SPS; cut for 80 SPS | VCC (analog) separate | **VDD separate (3.3 V logic)** | VERIFIED via [S12 §1.4] |

Consequences:
- Green board at VCC = 5 V: DOUT swings to 5 V -> MCU pin must be FT; SCK from a 3.3 V pin into DVDD = 5 V
  has no guaranteed VIH (UNKNOWN; CMOS 0.7·DVDD = 3.5 V would fail) -> either open-drain FT pin + 4.7-10 kΩ
  pull-up to 5 V (rise time ≈ 0.2 µs, fine with T3 = 1-2 µs) or a level shifter. Green board at VCC = 3.3 V:
  AVDD drops to ≈ 3.0-3.2 V (regulator dropout, PSRR lost) -> noise in grams +40 %. Best: board with separate
  VDD = 3.3 V and VCC = 5 V. Q-R2-07.
- After power-down/reset by an over-long SCK-high, gain returns to A/128 and 50 ms of data are invalid ->
  FW must detect it (re-send gain pulses, discard 4 samples, raise a counter).

### 4.4 Numbers for this system (gain 128)

counts/kg = S · 128 · 2^24 / capacity (ratiometric: E+ = AVDD, so independent of AVDD):

| Cell output S | counts/kg | counts @ 200 kg | saturation | noise @ 80 SPS, AVDD 5.0 / 4.3 / 3.0 V | noise @ 10 SPS, AVDD 4.3 V |
|---|---|---|---|---|---|
| **3.0 mV/V (DEF)** | **32 212** | 6 442 451 | ≈ 260 kg (130 % FS) | 1.20 / **1.40** / 2.00 g rms (≈ 8 / 9 / 13 g p-p) | 0.78 g rms |
| 2.0 mV/V (if DEE) | 21 475 | 4 294 967 | ≈ 391 kg | 1.80 / 2.09 / 3.00 g rms | 1.16 g rms |

- LSB = AVDD/(128·2^24) = 2.33 nV @ 5 V, 2.00 nV @ 4.3 V; 90 nV rms ≈ 39 / 45 counts rms. One count
  ≈ 0.031 g (meaningless vs noise). Noise-free resolution ≈ 200 kg / 9 g ≈ 22 000 divisions (≈ 14.4 bit).
- 10 s tare/calibration average at 80 SPS (800 samples): 1.4 g/√800 ≈ 0.05 g (white-noise bound). Real
  limits: creep 40 g / 30 min, TC zero 40 g / 10 °C, non-linearity 60 g, hysteresis 60 g (load-cell, §3).
- Values from the datasheet noise figure (VERIFIED input) and calculation; real green-board noise is often
  higher (ASSUMED) -> measure in M2.
- Read timing at 80 SPS: 25 pulses x (1 µs high + 1 µs low) ≈ 50 µs per read, must finish within the 12.5 ms
  conversion period after DOUT falls (ASSUMED safe window). Detect ready by EXTI falling on DOUT (disable the
  EXTI while clocking). Wrap each SCK-high in a short critical section (BASEPRI so that the E-stop IRQ stays
  above it, ~1 µs masked per bit) - an ISR during SCK-high must not stretch it toward 50/60 µs. Alternative:
  SPI mode 1 (CPOL 0, CPHA 1) <= 1 MHz for the 24 data bits + 1-3 bit-banged pulses. Details/driver reuse: R3.

---

## 5. NUCLEO-F446RE (STM32F446RET6, MB1136)

### 5.1 Clocks and limits

| Item | Value | Status |
|---|---|---|
| SYSCLK max | 180 MHz (needs over-drive mode above 168 MHz, voltage scale 1) | ASSUMED (F446 datasheet DS10693 headline value; not fetched - PDF timed out) |
| APB1 / APB2 max | 45 MHz / 90 MHz | ASSUMED (same) |
| Timer clocks | 2 x PCLK when the APB prescaler ≠ 1: APB1 timers 90 MHz, APB2 timers 180 MHz | ASSUMED (RM0390 clock-tree rule) |
| HSE on Nucleo-64 | 8 MHz MCO from the ST-LINK MCU into PH0 (HSE **bypass**) by default solder-bridge config | VERIFIED for MB1136 in Thrust R3 §6.7 [S12] / UM1724 |
| HSI accuracy | ±1 % at 25 °C factory-trimmed; several % over temperature | ASSUMED (F4-family datasheet value) |
| Stefan clock | HSI 16 MHz /16 x 336 /4 = **84 MHz**, APB1 42 MHz, APB2 84 MHz, scale 3 | VERIFIED `Stefan/FW/stanok/Core/Src/main.c:131-158` |

Recommendation: HSE bypass 8 MHz -> PLL M=4 (2 MHz), N=180, P=2 -> 180 MHz, APB1 /4 = 45 MHz, APB2 /2 = 90 MHz,
5 wait states, over-drive on (CubeMX does this). ASSUMED (standard F446 configuration).

### 5.2 PC link: USART2 via ST-LINK/V2-1 VCP at 921600

- USART2 PA2 (TX) / PA3 (RX) is wired to the ST-LINK MCU by SB13/SB14 (UM1724 §6.8). VERIFIED [S12 §6.7].
- No official ST maximum for the V2-1 VCP was found (UNKNOWN). Community: "921600 works", up to ~2 Mbaud
  reported; no flow control, USB FS bulk transfers every 1 ms, sustained overrun of the ST-LINK UART possible
  [S15]. The ST-LINK F103 side presumably runs its USART at 36 MHz -> 921600 actual 923 077 (+0.16 %) (ASSUMED).
- Bandwidth need is tiny: e.g. a 24-byte data frame x 80 SPS ≈ 1.9 kB/s vs 92 kB/s line capacity (2 %).
  The risk is baud mismatch / VCP firmware, not throughput.

Baud error (BRR: USARTDIV = fPCLK/(8·(2-OVER8)·baud), 4-bit fraction for OVER16, 3-bit for OVER8 - calculation):

| PCLK | Port | 921 600 actual / error | 1 000 000 | 460 800 |
|---|---|---|---|---|
| 45 MHz (APB1 @ 180 MHz) | USART2/3, UART4/5 | 918 367 / **-0.35 %** (OVER16 and OVER8) | exact | -0.35 % |
| 90 MHz (APB2 @ 180 MHz) | USART1/6 | 918 367 / **-0.35 %** | exact | +0.16 % |
| 42 MHz (Stefan APB1) | USART2 | 913 043 / **-0.93 %** (+ HSI ±1 %) | exact | +0.16 % |
| 84 MHz (APB2 @ 168 MHz) | USART1/6 | 923 077 / +0.16 % | exact | +0.16 % |

=> USART1/6 on APB2 gives **no** accuracy advantage at 180 MHz; -0.35 % vs the ST-LINK's +0.16 % is a 0.5 %
mismatch, comfortably inside the ~±2 % 8N1 budget. Max baud = fPCLK/8: 5.6 Mbaud (USART2), 11.25 Mbaud
(USART1/6). Fallback if the VCP misbehaves: USART1 (e.g. PA9/PA10) or USART6 (PC6/PC7) to an external USB-UART
(FT232R / CP2102N), same -0.35 %. D-03 fixes 921600; 1 Mbaud would be exact on our side (only if the PO ever
wants to change it).

DMA (F4 DMA streams/channels):

| Request | Stream / channel | Status |
|---|---|---|
| USART2_RX | **DMA1 Stream5, Channel 4** (circular) | VERIFIED `Stefan/FW/stanok/Core/Src/usart.c:87-88` (CubeMX output) |
| USART2_TX | **DMA1 Stream6, Channel 4** | VERIFIED `usart.c:105-106` |
| USART1_RX / TX | DMA2 Stream2 or 5 Ch4 / DMA2 Stream7 Ch4 | ASSUMED (RM0390 Table "DMA2 request mapping", not fetched) |
| USART6_RX / TX | DMA2 Stream1 or 2 Ch5 / DMA2 Stream6 or 7 Ch5 | ASSUMED |

### 5.3 5 V tolerance

- Most F446 GPIOs are FT (5 V tolerant when used as digital I/O, not in analog mode). **PA4 and PA5 are TTa
  (not 5 V tolerant)** [S16]. Check DS10693 "pin and ball definitions" for every pin chosen in the pinout
  (R1 / Implementer A). ASSUMED except PA4/PA5.
- Stefan wiring (`hw_stm32.hpp:11-17`): PUL = PA0 (TIM2_CH1), DIR = PA1, **ENA = PA4 (TTa)**, limits PB0/PB1,
  E-stop PC13 (B1). With a 5 V buffer between MCU and drive (recommended) the TTa pin is harmless; it must not
  be used in "5 V anode + open-drain" wiring.
- PA5 = LD2 user LED (Nucleo), do not use it for HX711 DOUT (see Thrust R3 §1.7 [S12]).

### 5.4 Timers for step generation

| Timer | Width | Clock (180 MHz setup) | Features | Use |
|---|---|---|---|---|
| TIM1, TIM8 | 16-bit advanced | 180 MHz | repetition counter (8-bit), one-pulse mode, complementary outputs, break input | PUL generator with hardware **break input** wired to the E-stop -> outputs forced idle in hardware |
| TIM2, TIM5 | **32-bit** | 90 MHz | ext. clock / slave modes | step counter (counts emitted pulses via ITR) or Stefan-style 32-bit output compare |
| TIM3, TIM4 | 16-bit | 90 MHz | | PUL alternative, scheduler tick |
| TIM6/7, TIM9-14 | 16-bit | | | tick / timeouts |

ASSUMED (RM0390 general knowledge, not fetched). At 50 kHz and 90 MHz timer clock the period is 1800 ticks
(0.06 % frequency quantisation); 10 µs high = 900 ticks. Recommended architecture (detail in R4): PWM on a
TIM1/TIM8 channel with frequency updated by a 1 kHz ramp tick, pulses counted by a 32-bit TIM2/TIM5 slave
(internal trigger; exact ITR mapping to be checked in RM0390 "TIMx internal trigger connection") with a
compare interrupt to stop on target; E-stop on the TIM1/TIM8 BKIN pin. Avoid one ISR per step if the HX711
critical sections must coexist (an ISR per step at 50 kHz = 50 000 IRQ/s).

### 5.5 Flash sectors for NVM

STM32F446xE, 512 KB, single bank (RM0390 §3 - ASSUMED, standard F446 map):

| Sector | Address | Size |
|---|---|---|
| 0 | 0x0800 0000 | 16 KB (vector table) |
| 1 | 0x0800 4000 | 16 KB |
| 2 | 0x0800 8000 | 16 KB |
| 3 | 0x0800 C000 | 16 KB |
| 4 | 0x0801 0000 | 64 KB |
| 5 | 0x0802 0000 | 128 KB |
| 6 | 0x0804 0000 | 128 KB |
| 7 | 0x0806 0000 | 128 KB |

- Erase time (x32 parallelism, F4 family, ASSUMED same for F446): 16 KB typ 250 / max 500 ms, 64 KB 550 /
  1100 ms, 128 KB 1 / 2 s [S17]. Code fetch from flash stalls during erase/program (single bank) -> step timer
  ISRs and HX711 bit-bang stall -> **erase/program only while the motor is stopped and HX711 reading is paused
  or tolerant of a missed sample.**
- Recommendation: **sectors 6 + 7 as ping-pong** (linker FLASH length 256 KB, code < 256 KB), append-only
  records {magic, seq, len, payload, CRC}, erase the other sector only when the current one is full ->
  erases are rare; power loss during erase keeps the other copy. Alternative if flash space matters: sectors 2+3
  (16 KB each, faster erase) with a linker script that places the vector table in sector 0 and code from
  sector 4 - more complex.
- Add IWDG (independent watchdog) so a hung MCU stops issuing pulses (the drive stays enabled but idle).

---

## 6. Interfacing summary (wiring recommendations)

### 6.1 Step / dir / enable

```
 F446 (3.3 V)          5 V buffer (74ACT244 or 74AHCT125, VCC = 5 V from Nucleo 5V)     HBS86H
 PUL (TIM PWM) ---+--> A1 ------ Y1 ------------------------------------------------> PUL+   (270 Ω inside)
                  10k                                                    GND(MCU) --> PUL-
 DIR (GPIO)    ---+--> A2 ------ Y2 ------------------------------------------------> DIR+
                  10k                                                    GND(MCU) --> DIR-
 ENA (GPIO)    ---+--> A3 ------ Y3 ------------------------------------------------> ENA+
                  10k (pull-down = no LED current = drive ENABLED with default polarity!)    ENA-
```
- Common-cathode at 5 V gives ≈ 12 mA per input (§1.3). Pull-downs define the reset state.
- **MCU GND connects only to the opto side (PUL-/DIR-/ENA-, ALM-/PEND-); never to the drive's power GND** -
  keep the opto isolation (no ground loop through the 60-90 V PWM stage). Motor PSU earth/PE separate.
- Cable > 2 m or noisy: differential (AM26LS31) to PUL+/PUL-, DIR+/DIR-.
- ENA safe state: decide with the PO (Q-R2-09): (a) default polarity - pull-down = enabled, FW disables by
  driving current; (b) re-configure ENA polarity in the drive so that "no current = disabled" -> MCU reset
  or wire break = disabled (true "disabled by default", but the load can back-drive the axis).

### 6.2 ALM / PEND inputs
ALM+ / PEND+ -> MCU input (FT pin) with 4.7 kΩ pull-up to 3.3 V and an optional small filter capacitor
(<= 1 nF, keeps the edge fast); ALM- / PEND- -> MCU GND. ALM configured "conducting = OK" so open wire = alarm. EXTI on ALM (high priority).

### 6.3 Limit switches - use NC
- **NC contact to GND, input pull-up (external 1-4.7 kΩ to 3.3 V): closed = 0 = OK, open = 1 = stop.** A
  broken wire, unplugged connector or failed contact reads as "limit reached" -> fail-safe; with NO a broken
  wire silently disables the protection. ASSUMED (standard practice, cf. ISO 13849 "positive mode").
- Wetting current: 3.3 V / 1 kΩ = 3.3 mA - OK for gold contacts; silver microswitches prefer > 10 mA or a
  12/24 V loop through an optocoupler. Inductive sensors (NPN/PNP, 10-30 V) need an opto / level shift (FT pins
  tolerate 5 V only).
- Filtering: RC 1 kΩ / 10-100 nF (τ 10-100 µs) at the pin + Schmitt input + EXTI. **Act on the first edge
  (stop immediately)**; use software debounce (e.g. 3 consecutive 1 ms samples) only for *release* / re-arm.
  Stefan uses PB0/PB1 with pull-up, "NC to ground" (`hw_stm32.hpp:15-16`) - same scheme. VERIFIED.

### 6.4 E-stop (NC chain)
- Mushroom NC contacts in series; channel 1 -> MCU input (pull-up, open = STOP, EXTI at the highest NVIC
  priority, also wired to TIM1/TIM8 BKIN so PUL is forced idle in hardware within ns). Channel 2 (hardware,
  independent of FW): either an NC contact in the 5 V supply of the PUL/DIR buffer (pulses physically blocked,
  drive keeps holding) or a contactor on the drive's motor supply. Choice depends on Q-R2-09.
- The Nucleo B1 button (PC13) is a pushbutton, not an NC safety contact - OK as a "stop" input for development
  only.
- E-stop latency: EXTI -> timer disable < 5 µs (ASSUMED) + drive smoothing (keep P19/SW7 = 0).

### 6.5 Grounding / noise near the driver
- Drive and motor PSU in a separate area; motor cable shielded, shield to drive PE/GND at the drive end;
  encoder cable separate from the motor phases. ASSUMED good practice.
- Route the load-cell cable ≥ 10-20 cm away from motor/PSU cables, cross at 90°; shield (yellow) to HX711 GND
  only. HX711 board close to the Nucleo in the same (preferably metal) enclosure; SCK/DOUT wires < 20 cm.
- Single-point ground: Nucleo GND = HX711 GND = opto-side returns; laptop USB ground enters through the
  ST-LINK - if the motor PSU is earthed and the laptop is on mains, avoid a second ground path through the
  drive (opto isolation keeps it open).
- Decouple the HX711 board (it has its own regulator); power it from Nucleo 5 V/3.3 V, not from the drive PSU.

---

## 7. Mechanics (drive train UNKNOWN - Q-R2-05)

### 7.1 Formula
```
steps_per_mm = pulses_per_rev x gear_ratio / lead_mm         (gear_ratio = motor turns per screw turn)
resolution   = 1 / steps_per_mm [mm]
v_max        = min( f_max_pulse / steps_per_mm ,  rpm_max x lead_mm / (60 x gear_ratio) )   [mm/s]
f_pulse      = v [mm/s] x steps_per_mm
F_axial      = 2π x T_motor x gear_ratio x η / lead_m      (η ≈ 0.9 ball screw, 0.3-0.4 trapezoidal)
```
Stefan default `steps_per_mm = 640` (`Stefan/FW/stanok/App/Inc/axis.hpp:52`) = e.g. 3200 ppr / 5 mm or
6400 ppr / 10 mm (inference, R1).

### 7.2 Table (gear 1:1)

v columns: pulse-limited at the recommended 50 kHz cap and at the driver's 200 kHz; motor-limited at
600 rpm (≈ corner speed at 70 V) and 1000 rpm (ASSUMED practical max).

| lead | ppr | steps/mm | resolution µm | v @50 kHz mm/s | v @200 kHz | v @600 rpm | v @1000 rpm | f for 1 mm/s | f for 10 mm/s |
|---|---|---|---|---|---|---|---|---|---|
| 4 | 400 | 100 | 10.0 | 500 | 2000 | 40 | 66.7 | 100 Hz | 1.0 kHz |
| 4 | 800 | 200 | 5.0 | 250 | 1000 | 40 | 66.7 | 200 Hz | 2.0 kHz |
| 4 | 1600 | 400 | 2.5 | 125 | 500 | 40 | 66.7 | 400 Hz | 4.0 kHz |
| 4 | 3200 | 800 | 1.25 | 62.5 | 250 | 40 | 66.7 | 800 Hz | 8.0 kHz |
| 4 | 4000 | 1000 | 1.0 | 50 | 200 | 40 | 66.7 | 1.0 kHz | 10 kHz |
| 4 | 6400 | 1600 | 0.625 | 31.2 | 125 | 40 | 66.7 | 1.6 kHz | 16 kHz |
| 4 | 10000 | 2500 | 0.4 | 20 | 80 | 40 | 66.7 | 2.5 kHz | 25 kHz |
| 5 | 400 | 80 | 12.5 | 625 | 2500 | 50 | 83.3 | 80 Hz | 0.8 kHz |
| 5 | 800 | 160 | 6.25 | 312 | 1250 | 50 | 83.3 | 160 Hz | 1.6 kHz |
| 5 | 1600 | 320 | 3.125 | 156 | 625 | 50 | 83.3 | 320 Hz | 3.2 kHz |
| 5 | 3200 | 640 | 1.56 | 78.1 | 312 | 50 | 83.3 | 640 Hz | 6.4 kHz |
| 5 | 4000 | 800 | 1.25 | 62.5 | 250 | 50 | 83.3 | 800 Hz | 8.0 kHz |
| 5 | 6400 | 1280 | 0.78 | 39.1 | 156 | 50 | 83.3 | 1.28 kHz | 12.8 kHz |
| 5 | 10000 | 2000 | 0.5 | 25 | 100 | 50 | 83.3 | 2.0 kHz | 20 kHz |
| 10 | 400 | 40 | 25.0 | 1250 | 5000 | 100 | 167 | 40 Hz | 0.4 kHz |
| 10 | 800 | 80 | 12.5 | 625 | 2500 | 100 | 167 | 80 Hz | 0.8 kHz |
| 10 | 1600 | 160 | 6.25 | 312 | 1250 | 100 | 167 | 160 Hz | 1.6 kHz |
| 10 | 3200 | 320 | 3.125 | 156 | 625 | 100 | 167 | 320 Hz | 3.2 kHz |
| 10 | 4000 | 400 | 2.5 | 125 | 500 | 100 | 167 | 400 Hz | 4.0 kHz |
| 10 | 6400 | 640 | 1.56 | 78.1 | 312 | 100 | 167 | 640 Hz | 6.4 kHz |
| 10 | 10000 | 1000 | 1.0 | 50 | 200 | 100 | 167 | 1.0 kHz | 10 kHz |

Effective v_max = smaller of the pulse- and motor-limited columns. With ppr <= 4000 the pulse rate never
limits a bend stand (motor speed does). The real positioning accuracy is set by the encoder (4000 counts/rev
-> 1.0-2.5 µm at 4-10 mm lead) and by screw lead error/backlash, not by the microstep setting.

### 7.3 Force capability vs load cell

| lead | η 0.9 (ball screw) | η 0.35 (trapezoidal) | vs load-cell safe overload 2354 N |
|---|---|---|---|
| 4 mm | 11.6 kN (1182 kgf) | 4.5 kN (460 kgf) | 2-5x over |
| 5 mm | 9.3 kN (946 kgf) | 3.6 kN (368 kgf) | 1.5-4x over |
| 10 mm | 4.6 kN (473 kgf) | 1.8 kN (184 kgf) | up to 2x over |

(8.2 N·m holding torque, gear 1:1; calculation.) Overshoot after a load-limit trip ≈ k_specimen x v x t_latency,
t_latency ≈ 1-2 HX711 periods (12.5-25 ms at 80 SPS) + stop time; e.g. 5 mm/s x 30 ms = 0.15 mm -> 150 N on a
1000 N/mm specimen. At 10 SPS the latency is 8x longer -> another reason for 80 SPS. FW hard load limit
(proposal for the SRS): trip at <= 110 % FS (220 kg) or the user limit, whichever is lower, plus HX711
saturation = trip.

---

## 8. Risks and open questions

### 8.1 Risks

| # | Risk | Mitigation |
|---|---|---|
| RK-1 | 3.3 V direct opto drive marginal; 5 V-anode + 3.3 V push-pull never turns off | 5 V buffer (§6.1) |
| RK-2 | Motor can crush the load cell (2-5x safe overload) | FW load limit on every sample, saturation = trip, SW limits, mechanical stop/shear pin (PO) |
| RK-3 | HX711 board strapped to 10 SPS while config says 80 | measure DOUT period, `AFE_RATE_MISMATCH`; RATE to GPIO if allowed |
| RK-4 | ISR stretches HX711 SCK high > 60 µs -> power-down, gain reset, 50 ms invalid | IRQ-protected SCK-high, detect & recover |
| RK-5 | ST-LINK V2-1 VCP at 921600 not officially specified | early link soak test (M1, when HW access allowed); CRC + sequence counter; fallback external USB-UART |
| RK-6 | Flash erase stalls CPU up to 2 s | NVM writes only when idle, ping-pong sectors |
| RK-7 | Following-error alarm -> motor free -> specimen spring-back, position lost | load limit well below stall; ALM handling; re-zero |
| RK-8 | ENA default (no current = enabled) conflicts with "disabled by default" | decide polarity (Q-R2-09) |
| RK-9 | Clone variance in timing/polarity (10 µs vs 2.5 µs, ALM inverted) | conservative timing defaults + polarity params |
| RK-10 | Stefan clock mismatch (84 vs 90 MHz) copied into new FW | derive timer constants from `HAL_RCC_GetPCLKxFreq()` |
| RK-11 | Load-cell creep/hysteresis (40-60 g) larger than ADC noise | report accuracy budget in SRS; settling-time rules (R4) |
| RK-12 | PWM noise from the 60-90 V drive into the bridge signal | routing/shielding §6.5, opto isolation kept |

### 8.2 Open questions for the product owner

| ID | Question | Why |
|---|---|---|
| Q-R2-01 | Exact driver: brand/label/revision of the "HBS86H" (photo of label and DIP block). Genuine Leadshine or JMC 2HSS86H-KH / other clone? | timing, DIP meaning, ALM polarity differ |
| Q-R2-02 | Current DIP settings (pulses/rev, direction, motor select). Is the RS232 tuning cable/software (Leadshine) or HISU (JMC) available to set ENA/ALM/PEND polarity, error limit, smoothing? How is an alarm reset on this unit (ENA toggle or power cycle)? | steps/mm default, safe states |
| Q-R2-03 | Drive supply voltage / PSU model | torque-speed, corner speed |
| Q-R2-04 | Motor label (86HS2140-06?), encoder lines (1000 or 2500) | drive settings, resolution |
| Q-R2-05 | Drive train: ball or trapezoidal screw, lead (mm), belt/gear ratio, travel length, is the axis back-drivable? | steps/mm, v_max, force, behaviour when disabled |
| Q-R2-06 | Load cell label: DEF (3.0 mV/V) or DEE (2.0 mV/V)? Calibration certificate value? Tension only or tension and compression? | counts/kg, sign, limits |
| Q-R2-07 | HX711 board type (green single-VCC / SparkFun / other), supply 3.3 or 5 V, current RATE strap; may we modify it (RATE to GPIO)? | 80 SPS requirement, logic levels |
| Q-R2-08 | Maximum test force and speed actually needed | limits, ppr choice, ramp parameters |
| Q-R2-09 | E-stop / limit semantics: "stop pulses and hold" (drive enabled) or "disable drive (motor free)"? Should a hardware E-stop channel exist independent of the MCU? | SAF requirements, ENA polarity |
| Q-R2-10 | Limit switches: type (mechanical NC microswitch / inductive NPN/PNP), voltage, how many (min, max, home)? | input circuit |
| Q-R2-11 | Is a small interface board (5 V buffer, RC filters, connectors) acceptable? | RK-1 |
| Q-R2-12 | Nucleo powered from USB only? Is the motor PSU earthed and the PC on mains (ground loop check)? | noise/grounding |

---

## 9. Sources

- [S1] Leadshine "Hybrid Servo Drive HBS86 Datasheet" (HBS86H, 7 pp., 2012): https://kitaez-cnc.com/f/hbs86h.pdf
  (original: http://leadshineusa.com/UploadFile/Down/HBS86Hd.pdf - expired TLS certificate, also https://www.scribd.com/document/105954844/HBS86Hd).
  Sections used: Electrical Specifications, Protection Indications, Control Signal Connector, Stator Signal
  Connector, Encoder Feedback Connector, RS232 port, DIP Switch Settings, Matching Motor Specification,
  Typical Connections.
- [S2] JMC "2HSS86H-KH Hybrid Stepper Servo Drive Manual" V1.1: https://www.forum.linuxcnc.org/media/kunena/attachments/21571/2HSS86H-KHEnglish.pdf
  (§3 ports, §4 technological index, §5.1-5.5 wiring & sequence chart, §6 DIP, §7 LED, §9-10 parameters, §11 troubleshooting).
- [S3] "HBS86H Hybrid Stepper Servo Drive Manual" (same text as [S2]): https://hardware-cnc.nl/images/PDF/ACT/HBS86H_English_Manuel.pdf ;
  manualslib copy: https://www.manualslib.com/manual/1576073/Leadshine-Hbs86h.html
- [S4] Newer HBS86H datasheet text (3.3-5 V, 1.2 µs) - search snippets only: https://www.cnc.info.pl/download/file.php?id=44527 ,
  https://www.scribd.com/document/748938425/HBS86H-Datasheet (not readable, HTTP 403 / no body).
- [S5] LinuxCNC forum, HBS86H alarm wiring (ALM normally-open behaviour on a user's unit, polarity changed via RS232):
  https://forum.linuxcnc.org/27-driver-boards/53695-help-to-connect-alarm-signal-driver-hbs86h
- [S6] 86HS2100/2140/2180 closed-loop motor table: https://peacosupport.com/nema-34-closed-loop-stepper-motor ;
  86HS2140 listing (8.2 N·m, 6 A, 4.2 kg, 143 mm) via search: https://sihengmotor.en.made-in-china.com/product/LyZxEWRKkwcd/China-2-Phase-12nm-6A-NEMA-34-Closed-Loop-Stepper-Motor-and-Driver-for-CNC-Machine.html
- [S7] Leadshine HBS86H + 86HBM80-01-1000 kit listing: https://www.ebay.de/itm/322767295144
- [S8] Keli DEF spec page (keli.com.ua): https://keli.com.ua/en/product/load-cell-def/
- [S9] DEE/DEF spec page (kalascale): https://en.kalascale.com/product/load-cell-dee-def
- [S10] Keli DEF spec page (Constanta-Ves): https://constanta-ves.com/en/product/load-cell-keli-def/
- [S11] Avia HX711 datasheet: https://cdn.sparkfun.com/datasheets/Sensors/ForceFlex/hx711_english.pdf (Tables 1-3, Fig. 2-3)
- [S12] Thrust_Stand_HAW `00_System/research/R3_hardware_components.md` §1 (HX711), §6.6-6.7 (USART/VCP, UM1724 SB13/14, MCO) - read-only reference.
- [S13] RATE pin modification on generic boards: https://community.robotshop.com/forum/t/how-do-you-change-the-sps-from-10hz-to-80hz-on-hx711/75867 ;
  https://forum.mysensors.org/post/65933 (via search summary: lift pin 15, 10 kΩ to VCC; R 20 k -> 11.5 k for 3.0 V AVDD)
- [S14] Arduino forum, 0 Ω 10/80 Hz selector: https://forum.arduino.cc/t/question-about-hx711-rate/586432
- [S15] ST community on ST-LINK VCP baud rates: https://community.st.com/t5/boards-and-hardware-tools-mcus/how-can-i-set-baud-rate-greater-than-115200-for-virtual-com-port/td-p/65825 ;
  https://community.st.com/stm32-mcus-products-25/virtual-serial-port-for-stlink-v2-72553
- [S16] PA4/PA5 TTa (not FT): https://stcommunity.st.com/t5/stm32-mcus-products/stm32f20xxx-datasheet-pa4-pa5-tta-or-ft/td-p/451135 ;
  https://autoconfig.stm32world.com/wiki/STM32_Five-volt_Tolerant_I/O_Pins_(FT)
- [S17] F4-family flash erase times (STM32F412 datasheet, same flash IP): https://www.alldatasheet.com/html-pdf/2178840/STMICROELECTRONICS/STM32F412RGH6TR/266987/117/STM32F412RGH6TR.html
- Not fetched (timeouts), to be checked by Implementer A: STM32F446 datasheet DS10693 https://st.com/resource/en/datasheet/stm32f446re.pdf ,
  RM0390 reference manual, UM1724 Nucleo-64 user manual, ST TN1235 (ST-LINK derivatives).
- Reference code (read-only): `E:\Bavovna\Drone\Stefan\FW\stanok\Core\Src\main.c:131-158`, `...\Core\Src\usart.c:44-50,87-106`,
  `...\App\Inc\hw_stm32.hpp:11-33`, `...\App\Inc\axis.hpp:52-65`.
