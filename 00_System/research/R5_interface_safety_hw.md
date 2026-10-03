# R5 - Interface & safety hardware (E-stop power removal, HBS86H at 48 V, ALM/PEND/reset, 3.3 V drive, interface board, pin map)

| | |
|---|---|
| **Author / role** | Researcher (R5) |
| **Date** | 2026-10-03 |
| **Phase** | P1 (specification) - proposals for the product owner (PO) |
| **Builds on** | R2 (`R2_hardware_components.md`, HBS86H/HX711/F446 facts - not repeated here), R1 §9 (pin proposal) |
| **Binding decisions used** | D-11 (E-stop = power removal, cat. 0), D-13 (driver holds through MCU reset), D-14/D-26 (PAUSE + STOP/BREAK physical buttons), **D-16 (genuine Leadshine HBS86H at 48 V DC; ALM/PEND wired, read-and-report only in release 1)**, **D-17 (direct 3.3 V drive, common-cathode: PUL-/DIR-/ENA- to GND)**, D-18 (NC limit switches), D-19 (SFU1605, 5 mm lead, 300 mm), D-21 (HX711 RATE on a GPIO), D-22 (Nucleo USB-powered, separate driver PSU), D-24 (Stefan's wiring ran) |

Status marks: **VERIFIED** = read in the cited source during this research; **ASSUMED** = engineering estimate, family-typical value or inference (check on the bench); **UNKNOWN** = not found, turned into a question (`Q-R5-nn`).
Calculations: `scratchpad/researcher-R5/torque.py` (session scratchpad, not in the repo); every number derived from it is reproduced in the text.
This is an engineering proposal for a lab stand, **not a certified safety design**; the risk assessment (EN ISO 12100 / 13849-1) remains the PO's responsibility.

---

## 0. Summary for the PO (read this first)

| # | Topic | Finding | Recommendation |
|---|---|---|---|
| 1 | E-stop power removal (D-11) | Switching **48 V DC** needs DC-rated contacts (most relays are rated 30 V DC only). The driver input is capacitive: **breaking** is easy, **making** (capacitor inrush) is the hard duty. A 200 kg load **back-drives the SFU1605** once power is gone (1.25-1.40 N·m on the motor shaft vs ≈ 0.1-0.3 N·m detent + friction). In a horizontal stand that only releases the specimen; a vertical axis with a hanging mass would fall. | **Option A**: 2-NC mushroom E-stop. NC1 drops a **DC-rated contactor (Eaton DILM7 class, DC-1 20 A @ 60 V)**, which holds itself in and needs a blue **RESET** button to close again; two poles in series switch +48 V to the HBS86H. NC2 goes to an MCU input. FW also reads a contactor aux contact (driver power) and ALM. Cost **≈ €110**, about half a day of wiring. Upgrade path: safety relay (**Option B**, ≈ €320) if the risk assessment asks for PL d/e. |
| 2 | HBS86H at 48 V (D-16) | 48 V is inside the genuine range (30-100 VDC, typ. 60 V). Load torque at 200 kg is **1.73 N·m** (≈ 1.9 N·m with friction). Conservative model at 48 V: full 8.2 N·m up to ≈ 225 rpm (19 mm/s); ≥ 1.9 N·m up to ≈ 415 rpm (≈ 35 mm/s). **10 mm/s (120 rpm) has ≈ 4× torque margin.** The SFU1605 itself allows far more (critical speed ≈ 7700 rpm, DN limit ≈ 3000 rpm). | Default limits: **v ≤ 20 mm/s under load, ≤ 30 mm/s for positioning** until a bench test raises them. PSU: **Mean Well SDR-480-48** (10 A, 15 A peak for 3 s, auto-recovery, DC-OK relay) or HRP-300-48. **Avoid LRS-350-48**: its 48 V model **latches off** on overload, OVP or OTP and only recovers after an AC power cycle. No regen clamp is needed (≤ 0.7 J at 600 rpm → +5 V on the bus). |
| 3 | ALM / PEND / reset | ALM is closed (low impedance) when the drive is OK and powered, and open on a fault, when unpowered, or if the wire breaks, so it is fail-safe. PEND is high impedance when the drive is in position. **HBS86H has no reset input.** For Leadshine closed-loop drives the documented reset is **re-powering**. Some Leadshine families have an optional "ENA resets the drive" setting, but whether the HBS86H has it is UNKNOWN. | Release 1: read and report only (D-16). Later: ALM → stop pulses, latch, "not homed", operator acknowledges, **power cycle via E-stop/RESET** (the contactor doubles as the alarm reset). PEND → settle detection before data capture, plus an "in position" timeout after each move. |
| 4 | Current 3.3 V wiring (D-17, D-24) | Common-cathode, 3.3 V push-pull into the internal 270 Ω gives **≈ 6-7 mA typical, 4.3 mA worst case**. Leadshine specifies 7-16 mA. It works at Stefan's speeds because the opto threshold is lower (≈ 2-5 mA). The margin shrinks with cold, opto ageing and noise. **A dropped pulse is not detected** by the closed loop: the drive never saw it, so the FW position drifts silently. | OK for bring-up. Before long test campaigns, fit the **interface board (Option A: SN74ACT244 5 V buffer, ≈ 12 mA per input, same common-cathode wiring)**. |
| 5 | Interface board | Three options were compared: (A) 5 V CMOS buffer, (B) differential AM26LS31 (only ≈ 6-8 mA, so little gain), (C) off-the-shelf / perfboard. | **Option A as an Arduino-shield PCB** (≈ €50, 1-2 days design). It carries the buffer, conditioning for all switch and button inputs (RC + ESD), ALM/PEND pull-ups and pluggable terminals. Every signal sits on the Arduino headers. |
| 6 | Pin map | Checked against the F446 datasheet (DocID027107 Rev 6, Table 10). All proposed inputs are on **FT** pins. **PA4/PA5 are "TC" (3.3 V only)**; R1/R2 said "TTa", but the conclusion is the same. EXTI conflicts resolved. | Changes vs R1: **HX711 DOUT → PB4 (EXTI4, own vector)**, RATE → PB5, END → PC1 (A4), **STOP → PC7 (D9, NC)**, **PAUSE → PB6 (D10, NO)**, DRV_PWR → PA7 (D11), optional TRIP → PB9 (D14). See §6. |

---

## 1. E-stop with power removal (D-11, IEC 60204-1 stop category 0)

### 1.1 Facts that shape the circuit

| Fact | Value | Status / source |
|---|---|---|
| Driver supply | 48 V DC (D-16); HBS86H range 30 / 60 (typ) / 100 VDC | VERIFIED [S1 Electrical Specifications] |
| Driver input current (estimate) | holding ≈ 0.3-0.5 A; 200 kg @ 10 mm/s ≈ 1 A (19.6 W mechanical + ≈ 20-30 W losses); acceleration peaks ≈ 3-4 A | ASSUMED (calculation §2.3) |
| DC arc | above ≈ 30-40 V DC a contact gap can sustain an arc at a few amps. General-purpose relays are rated "30 VDC". Use contacts with a **DC-1 rating at ≥ 48 V**, or contacts in series. | ASSUMED (general practice). DC-1 ratings below are VERIFIED. |
| Break duty | the driver input is a capacitor bank. When the contact opens, the voltage across it rises only as fast as the driver caps discharge, so arc energy is low. | ASSUMED (circuit reasoning) |
| Make duty | on RESET the discharged driver bus caps charge from the PSU output caps. That is a short high-current pulse (tens of A, < 1 ms) and the main contact-welding risk. | ASSUMED; internal HBS86H capacitance UNKNOWN |
| Driver de-energisation after the DC contact opens | contactor opening ≈ 12 ms + bus decay from 48 V to the logic dropout (assumed ≈ 20-25 V): with 470-1000 µF internal and 0.5-3 A draw, ≈ 5-50 ms. **Torque is gone within ≈ 20-60 ms.** | ASSUMED (C·ΔV/I; Eaton opening delay [S11], table alignment uncertain) |
| Behaviour after power is restored | Leadshine closed-loop drives: "ENA must be ahead of DIR by at least 500 ms" [S20 §8]. The protections clear on re-power [S20 §9]. **Position is lost** (D-11: axis "not homed", re-home). | VERIFIED for the CS-D808 sibling [S20]; ASSUMED for HBS86H. **R2 used 200 ms; use 500 ms.** |
| Interaction with D-13 | the contactor holds itself in, independent of the MCU. An MCU reset or Nucleo USB loss does **not** drop driver power, so the driver keeps holding (D-13 preserved). A PSU loss drops the contactor, and power stays off until RESET. | design property |

### 1.2 Back-drive when power is cut under load (SFU1605, 5 mm lead)

Back-drive torque at the motor shaft: `T_b = F · L · η2 / 2π`, where η2 is the reverse efficiency of a ball screw, 0.8-0.9 [S15].

| Load | η2 = 0.80 | η2 = 0.85 | η2 = 0.90 |
|---|---|---|---|
| 200 kg (1961 N) | 1.25 N·m | **1.33 N·m** | 1.40 N·m |

Resisting torque when unpowered = detent torque + nut/bearing drag. For an 86 mm 8 N·m motor plus an unpreloaded rolled SFU1605 this is about **0.1-0.3 N·m** (ASSUMED, typical values; the motor detent torque is UNKNOWN, Q-R2-04). That holds only **≈ 15-45 kg** (0.1 → 148 N, 0.2 → 296 N, 0.3 → 444 N; η2 = 0.85).

**Conclusion (ASSUMED, verify on the bench):** if driver power is cut with more than ≈ 30-50 kg on the specimen, the table **will move back**. It travels until the load falls below that threshold, which means it releases the elastic deflection of specimen + frame (typically ≤ a few mm; stored energy ½·F·x ≈ 2 J at 2 kN / 2 mm). The motor spinning unpowered pumps a little energy into the driver caps through the bridge body diodes (negligible, ASSUMED).

- **Horizontal stand, no gravity load:** this unloading is the *safe* direction, so stop category 0 is acceptable.
- **Vertical axis or any hanging mass:** the table would drop. That needs a holding brake (a motor with brake, controlled from the HBS brake output on newer Leadshine closed-loop drives [S20 §3.1.1], ASSUMED for HBS86H) or a counterweight. **Q-R5-03.**
- Stop category 1 (controlled stop, then power off) does not avoid this: the load is released as soon as power is removed. Cat. 1 is therefore not worth a timed relay here (see §1.4).

### 1.3 Circuit options

#### Option A - Dual-channel NC E-stop + DC-rated contactor with self-hold and manual RESET (recommended)

```
 230 VAC --[Q0 MCB 6 A]--> PSU 48 V (SDR-480-48)          0 V (48 V return) ------------------------+
                            +48V --[F1 T10A]--+                                                      |
                                              |   power path (two poles in series = 2 breaks)        |
                                              +--[K1 1-2]--[K1 3-4]--------------------> HBS86H +Vdc |
                                              |                                          HBS86H GND -+
                                              |   control path (48 V DC, coil ~3 W)                  |
                                              +--[S0.NC1]--+--[S1 RESET NO, blue]--+--[K1 coil A1-A2]-+
                                                           |                       |   || varistor/TVS suppressor
                                                           +--[K1 13-14 aux NO]----+   (self-hold)
                                                           (optional: [KT NC] MCU trip relay in series, can only OPEN)

 Logic side (Nucleo GND, never connected to the 48 V circuit):
   S0.NC2 (2nd NC block of the same E-stop) ------> interface J3: ESTOP_S  -> PA10 (pull-up, open = E-stop)
   K1 aux NO (front block, e.g. 43-44) -----------> interface J3: DRV_PWR  -> PA7  (closed = driver powered)
   HBS86H ALM+/ALM- -------------------------------> interface J1: ALM      -> PA8  (low = OK and powered)
```

Behaviour:
1. Pressing E-stop opens NC1. The K1 coil drops and the driver loses power (cat. 0) independently of the MCU. At the same moment NC2 opens and the MCU sees ESTOP within ≈ 10 µs plus the filter (§5). It stops the step timer, sets ENA to "disabled" and latches ESTOP (D-11). The MCU has therefore already stopped commanding motion while the driver bus is still decaying.
2. Releasing E-stop (turn to release) does **not** restart anything: K1 stays open until RESET is pressed. This meets IEC 60204-1: resetting the E-stop must not start the machine.
3. RESET closes K1. The driver powers up **disabled** because the MCU still drives ENA = disabled. The FW waits ≥ 500 ms and for ALM = normal, then reports "driver ready, not homed". Clearing needs a PC command plus re-homing (D-11).
4. A PSU failure or AC loss drops K1, and power stays off until RESET. This is consistent with "driver power lost → not homed".

Parts (examples - ASSUMED part numbers unless marked; check datasheets before ordering):

| Ref | Part | Key rating | ≈ € |
|---|---|---|---|
| S0 | Ø40 mm red mushroom, turn-to-release, yellow background, IEC 60947-5-5 direct-opening action, **2 × NC blocks** (e.g. Schneider ZB5AS844 + 2× ZBE102, or Eaton M22-PVT + M22-K02) in a yellow enclosure | NC1 carries the 48 V coil current (≈ 63 mA DC-13); NC2 carries 3 mA at 3.3 V (gold-flashed block preferred) | 30-40 |
| S1 | Blue flush pushbutton 1 NO (RESET) | ≥ 48 V DC, 0.1 A | 8-12 |
| K1 | **Eaton DILM7-10** with a **48 V DC coil** (or the 24 V DC coil 276565 [S11] plus a small 24 V supply) + front aux block 1NO+1NC (e.g. DILA-XHI11) + coil suppressor (varistor type, keeps drop-out fast) | **DC-1 20 A @ 60 V** (VERIFIED for DILM7-10(24VDC) [S11]); coil 3 W [S11]; two poles in series for the 48 V line | 35-50 |
| F1 | Fuse holder + T10A at the PSU + output | | 8 |
| - | DIN rail, wiring 1.5 mm² (48 V), 0.25-0.5 mm² (control) | | 15 |
| **Σ** | | | **≈ 100-125** |

- Pros: simple, cheap, no MCU dependency, deterministic de-energisation in ≈ 20-60 ms, no 230 V in the operator station, no automatic restart, and it **doubles as the HBS86H alarm reset** (E-stop + RESET = driver power cycle, §3.3).
- Cons: single power channel, so a welded K1 is not detected automatically (rough guide: ISO 13849 category B/1, roughly PL b-c, ASSUMED). The FW can detect it partly: ESTOP open while DRV_PWR still reads "on" after 100 ms gives `FAULT_K1_WELDED`. Adding the K1 NC mirror contact makes this a direct check.

#### Option B - Safety relay module with feedback loop and monitored manual reset

```
 +48V --[F1]--+--[K1 1-2]--[K1 3-4]-----------------------------> HBS86H +Vdc
              |
              +--> PSR-SCP-42-48UC/ESAM4/3X1/1X2/B (A1/A2 on 48 V)
                    S11-S12 <- S0.NC1  (channel 1)  \  2-channel E-stop, cross-fault
                    S21-S22 <- S0.NC2  (channel 2)  /  detection
                    S33-S34 <- RESET (monitored, acts on release) in series with K1 NC mirror (feedback)
                    13-14 / 23-24 -> K1 coil (two enabling paths in series)
                    33-34  -> MCU "SAFETY_OK" (dry)  ;  41-42 (NC signalling) -> spare
 MCU E-stop sense: 3rd NC block on S0 -> PA10 (or the 33-34 contact)
```

- Phoenix Contact **PSR-SCP-42-48UC/ESAM4/3X1/1X2/B, 2901416** can be supplied straight from the 48 V bus. VERIFIED data [S9]:
  - up to Cat. 4 / PL e / SIL 3; stop category 0
  - 3 enabling NO paths + 1 NC signalling path; AgSnO2 + 0.2 µm Au contacts
  - 6 A continuous per NO; interrupting ohmic load **230 W at 48 V DC** (≈ 4.8 A), 40 W at 48 V DC with τ = 40 ms
  - typical release time 20 ms via S11/S12
  - manual-monitored or automatic start
  - Its **maximum inrush current is listed as 6 A** (table alignment in the extracted PDF is uncertain, so ASSUMED). The driver bus-cap inrush can exceed that, so let the PSR switch K1 rather than the 48 V line directly.
- Pilz PNOZ s3 (PNOZsigma): DC1 rating only **24 V / 6 A**, about £163 [S10]. It cannot switch 48 V on its own contacts, so it would also need K1.
- Cost ≈ €200 (PSR) + K1 €45 + S0 with 3 blocks €45 + S1 €10 → **≈ €300-330**, about one day of wiring.
- Pros: detects contact welding (K1 mirror in the reset loop) and channel cross-faults, monitored reset, certifiable PL d/e architecture.
- Cons: cost and complexity. The response is no faster in practice (20 ms + K1 12 ms).

#### Option C - Switch the AC mains side of the PSU

- Pros: AC contacts have no DC-arc issue and AC contactors are cheap. No DC make-inrush through the contacts, because the PSU's own inrush limiting acts on AC. An AC cycle also clears latch-off protections (relevant for LRS-350-48, §2.4).
- Cons:
  - **De-energisation is slow and load-dependent.** PSU hold-up is 14-16 ms at **full** load [S12][S13][S14]; at the stand's 5-15 % load it is ASSUMED to be 0.1-1 s, plus the output and driver caps. So the cat. 0 time is indeterminate.
  - The PSU needs 1.5 s to start up again (LRS-350 setup time, VERIFIED [S12]).
  - Puts 230 V wiring into the operator station (qualified electrician needed).
  - The coil supply must come from mains or a separate 24 V supply, because the 48 V bus disappears.
- Verdict: acceptable only if a mains contactor already exists in the PSU cabinet. Otherwise use the DC side (Option A/B).

#### Optional stop category 1

An off-delay (PSR with delayed contacts, or a timer relay in the K1 coil path, ≈ 0.2-0.5 s) would let the MCU decelerate first. At ≤ 10-30 mm/s the HBS stops within one servo cycle once pulses stop, and the MCU already stops pulses ≈ 12 ms before K1 opens (NC2 vs K1 opening delay). **Not recommended:** it adds a part and still releases the load (§1.2).

### 1.4 Recommendation

**Option A** for the lab stand (≈ €110), with the K1 aux contact read by the FW and the welded-contactor plausibility check in FW. Design the panel so that Option B can be added later: leave space for a 22.5 mm DIN module and use an E-stop operator with a free slot for a 3rd NC block. Add the optional MCU **trip relay KT** (NC contact in series with the K1 hold path, driven from PB9 through a transistor). It lets the FW remove driver power on a severe fault (for example ALM, or the load limit exceeded with no reaction), but it can **never** re-close power: only a human with RESET can. Provision only; it is not used in release 1 (Q-R5-04).

### 1.5 What the FW must sense (SRS candidates)

| Input | Meaning | FW reaction (release 1) |
|---|---|---|
| ESTOP_S (PA10) open | E-stop pressed or wire broken | stop pulses immediately (EXTI, priority 0), ENA = disabled, latch `ESTOP`, axis "not homed", flag in DATA |
| DRV_PWR (PA7) off | K1 open: driver unpowered (E-stop, PSU loss, not yet reset) | block motion, "not homed", report `DRIVER_UNPOWERED` |
| DRV_PWR on after off | power restored | wait ≥ 500 ms **and** ALM = normal, then "driver ready, not homed" |
| ALM (PA8) high | drive fault **or** unpowered **or** wire broken (default Leadshine polarity) | release 1: report only (D-16), but motion stays blocked while DRV_PWR = on and ALM = high (Q-R5-11) |
| ESTOP_S open and DRV_PWR still on after 100 ms | K1 welded or wiring fault | `FAULT_K1` (latched), report |
| ESTOP_S closed, DRV_PWR on, but ENA commanded "disabled" | normal "powered but disabled" | - |

Without the optional DRV_PWR input the FW can use ALM. With the default polarity ALM reads "not OK" whenever the drive is unpowered, because the output phototransistor's LED is fed by the drive. In that case "fault" and "unpowered" cannot be told apart (ASSUMED, circuit reasoning).

---

## 2. HBS86H at 48 V DC (D-16, replaces the 24 V risk analysis)

### 2.1 Voltage range
- Genuine Leadshine HBS86H: **30 min / 60 typ / 100 max VDC**, "20-63 VAC or 30-90 VDC recommended, leaving rooms for voltage fluctuation and back-EMF" [S1]. **48 V is inside the recommended band.** VERIFIED.
- The 2012 datasheet lists no undervoltage protection (protections: over-current, over-voltage, following error [S1]). Some newer resellers quote "24-100 VDC" [S4 snippets]. The driver's own over-voltage trip level is UNKNOWN (JMC clone: 130 V [R2 S2]).
- **Reconciling D-24 (Stefan ran, possibly at 24 V):** the same model (§2.2) gives ≈ 6.5 N·m at 120 rpm at 24 V. Low-speed operation at 24 V is therefore plausible if the unit starts at that voltage (undervoltage lockout UNKNOWN). Torque collapses above ≈ 190 rpm (16 mm/s). Now moot (48 V).

### 2.2 Torque vs speed estimate (86HS2140-class motor, 8.2 N·m, 6 A, 0.5 Ω, ≈ 3.7 mH)

Model (ASSUMED, conservative):
- per-phase voltage limit `V_bus,eff² ≥ (R·I + Ke·ω)² + (p·ω·L·I)²`
- `Ke = T_hold/(√2·I_rated) = 0.97 V·s/rad`, p = 50 pole pairs, current capped at the 8.2 N·m level
- V_bus,eff = bus minus ≈ 2 V of bridge drops
- No published torque curve was found for this motor/driver pair (UNKNOWN), so verify on the bench (§2.5). Real closed-loop drives with phase advance usually do better at speed than this model.

| rpm | mm/s (5 mm lead) | pulse rate @ 800 steps/mm | T_avail 24 V (22 eff) | **T_avail 48 V (46 eff)** | T_avail 68 V (66 eff) |
|---|---|---|---|---|---|
| 120 | 10 | 8 kHz | 6.5 N·m | **8.2** | 8.2 |
| 180 | 15 | 12 kHz | 2.8 | **8.2** | 8.2 |
| 240 | 20 | 16 kHz | 0 | **7.6** | 8.2 |
| 300 | 25 | 20 kHz | 0 | **5.3** | 8.2 |
| 360 | 30 | 24 kHz | 0 | **3.5** | 7.3 |
| 420 | 35 | 28 kHz | 0 | **1.8** | ≈ 6 |
| 480 | 40 | 32 kHz | 0 | **0** (model) | 4.4 |
| 600 | 50 | 40 kHz | 0 | 0 (model) | 1.9 |

Required torque (VERIFIED formula, ASSUMED efficiencies):
- load: `T = F·L/(2π·η)`, with η = 0.9 → **1.73 N·m at 200 kg**; plus nut/bearing drag ≈ 0.1-0.2 → **≈ 1.9 N·m**
- acceleration: J_total ≈ 3.43·10⁻⁴ kg·m² (motor 3.2·10⁻⁴ [R2], screw 0.40 m ≈ 2.0·10⁻⁵, 5 kg table ≈ 3·10⁻⁶) → 0.04 N·m at 100 mm/s², 0.43 N·m at 1000 mm/s²

Result at 48 V:

| Operating point | Torque margin |
|---|---|
| 10 mm/s with 200 kg | **≈ 4.3×** |
| 20 mm/s with 200 kg | ≈ 3.9× |
| 30 mm/s with 200 kg | ≈ 1.8× (marginal) |
| No-load positioning to ≈ 35 mm/s (model) | OK |

Expect ≈ 50 mm/s if the real curve beats the model (ASSUMED).

### 2.3 Speed limits of the mechanics and pulse chain
- SFU1605 critical speed (fixed-supported, f = 15.1, root Ø ≈ 12.9 mm ASSUMED, unsupported length 0.40-0.45 m): 9 600-12 000 rpm, 80 % → **≥ 7 700 rpm**. DN limit for rolled screws ≈ 50 000 [S16, search summary] → ≈ 3 000 rpm (≈ 250 mm/s). **The screw does not limit**; the motor does.
- Pulse cap 50 kHz (D-16) at 800 steps/mm → 62.5 mm/s. **Not limiting** below the motor limit.
- **Recommended defaults (params):** `motion.v_max_load = 20 mm/s`, `motion.v_max_travel = 30 mm/s`, `motion.a_max = 200 mm/s²` (Stefan used 200). Raise them after the bench test.

### 2.4 PSU sizing

| Item | Value | Status |
|---|---|---|
| Continuous power | mechanical ≤ 20 W (200 kg, 10 mm/s) / ≤ 40 W (20 mm/s); copper ≤ 18 W (6 A amplitude, 2 phases, 0.5 Ω); drive losses ≈ 10-15 W → **≈ 40-70 W continuous, ≈ 100-150 W peaks** | ASSUMED (calculation) |
| Leadshine rule | regulated SMPS: "OVERSIZE" (e.g. a 4 A PSU for a 3 A motor) to avoid current clamp; unregulated: 50-70 % of motor current is enough [S20 §5.1] | VERIFIED (CS-D808 sibling) |
| **Recommended** | **Mean Well SDR-480-48** (DIN rail): 48 V 10 A, adj 48-55 V, **15 A peak for 3 s**, overload auto-recovery, OVP 56-65 V, **DC-OK relay contact (60 V / 0.3 A)** usable as PSU-OK input, hold-up 14 ms @ full load [S13] | VERIFIED (datasheet) |
| Alternative | Mean Well HRP-300-48: 7 A, 150 % peak capable, constant-current overload with auto-recovery, OVP shuts down (re-power), DC-OK signal 3.3-5.6 V [S14] | VERIFIED |
| **Avoid** | Mean Well **LRS-350-48**: 7.3 A, but the **48 V model shuts down and latches off on overload, over-voltage and over-temperature ("re-power on to recover")**; 150 % peak only ≤ 1 s [S12] | VERIFIED |
| Unregulated linear PSU (toroid + bridge + 10-20 mF) | preferred by Leadshine for surge capability; no-load voltage ≈ 52-55 V at high mains is still far below 90-100 V | ASSUMED (generic) |
| Regen / back-EMF | rotational energy ½·J·ω²: 0.03 J @ 120 rpm, **0.68 J @ 600 rpm**, 1.9 J @ 1000 rpm → with ≈ 2.5 mF on the bus (PSU + driver, ASSUMED) the bus rises 48 → 48.2 / **53.4** / 61.7 V. Keep PSU set-point at 48 V (not 55 V), keep v ≤ 600 rpm, so **no regen clamp is needed** (SDR OVP 56-65 V). | calculation, ASSUMED capacitance |
| Extra bulk capacitor | **not recommended by default**: it increases K1 make-inrush and slows E-stop de-energisation. If the supply leads exceed 1 m: ≤ 1000 µF / 100 V low-ESR at the driver plus a 4.7 kΩ / 1 W bleeder | ASSUMED (practice) |
| Wiring | PSU → K1 → driver 1.5 mm² twisted pair, ≤ 1-2 m, T10A fuse at the PSU | ASSUMED |

### 2.5 Bench verification (when HW access is approved, D-06/D-07)
1. No load: jog speed ramp 10 → 60 mm/s in 5 mm/s steps until ALM (following error, 7 blinks [S1]). The highest clean speed ÷ 1.5 gives `v_max_travel`.
2. With a known load (calibration weights / spring specimen ≈ 100-200 kg): repeat up to 30 mm/s. That gives `v_max_load`.
3. Power-cut test at 50 / 100 / 200 kg (horizontal only): measure the table spring-back with a caliper. This verifies §1.2.

**Voltage does not change the maximum force**: stall force is still ≈ 9 kN (R2 §7.3, current-limited). Only a driver **current limit** reduces it (§3.5, Q-R5-08).

---

## 3. ALM, PEND and alarm reset - how they are used (recommendations for a later release, D-16)

### 3.1 Electrical (genuine HBS86H)

| Output | Spec | Default level | Status |
|---|---|---|---|
| ALM+/ALM- | OC opto output, sink/source 20 mA at 24 V; asserted on over-voltage, over-current or position following error | "low impedance in normal operation and become high when HBS86 goes into error"; **software configurable** | VERIFIED [S1]; same text in CS-D808 [S20] |
| PEND+/PEND- | OC opto output, 20 mA at 24 V; "active when the difference between the actual position and the command position is zero"; "active at high impedance" | high impedance = in position | VERIFIED [S1] |
| Configuration | Leadshine ProTuner-type software over the RS232 port: fault-output active impedance, in-position active impedance, ENA active level, pulse active edge, command filter (50-25 600 µs). Changes must be **saved to NVM** ("Save Drive Parameters") or they are lost at power-off. **Do not plug or unplug the RS232 cable while the drive is powered.** | | VERIFIED for ES-D ProTuner [S21]; HBS86H tuning software ASSUMED equivalent [S1: "configure ... via RS232"] |
| Field practice | an HBS86H user found ALM "normally open" on his unit and inverted it via RS232 to normally closed; LinuxCNC experts connect ALM to a breakout input → `joint.N.amp-fault-in`, or put it into `estop-ext` | | VERIFIED (forum) [S5] |

Wiring to the 3.3 V MCU (direct or via the interface board):

```
  3V3 --[4k7]--+--[1k]--+--> PA8 (ALM) / PA9 (PEND)      FT pins, Schmitt input
               |        |
   ALM+ -------+      [1nF]                              tau ~ 5 us
   ALM- ---------------+--- Nucleo GND (= opto return side, isolated from 48 V)
```

- Current ≈ 0.7 mA (far below 20 mA). VOL of the phototransistor is ≈ 0.2-0.4 V (ASSUMED), below F446 VIL max = 0.35·VDD - 0.04 = **1.12 V** (VERIFIED [S7 Table 56]).
- With default polarity: **pin low = OK and powered; pin high = fault / unpowered / wire broken** (fail-safe). **Keep the Leadshine default; do not invert.** A JMC clone would be the opposite, so keep `drv.alm_active_level` as a parameter (R2).
- PEND reads high in position, but also high when the drive is unpowered. **PEND is only meaningful when ALM = OK and DRV_PWR = on.**
- Do not use the MCU's internal pull-ups (30-50 kΩ; **PA10 has only 7-14 kΩ** because it is OTG_FS_ID [S7 Table 56]). They are too weak for cables.

### 3.2 How CNC controllers typically use them

| Signal | Typical use | Source |
|---|---|---|
| ALM / fault | drive-fault input to the controller → machine-off / feed-hold + operator message; LinuxCNC `joint.N.amp-fault-in` → machine off; MASSO: drive fault → feed-hold + message; often also chained into the E-stop loop | VERIFIED [S5][S22][S23] |
| ENA | controller `amp-enable-out`; MASSO suggests routing ENA through the E-stop so E-stop disables the drive | VERIFIED [S22][S23] |
| PEND | in-position handshake: the next move or measurement starts only after PEND; with a PLC, "move done" | VERIFIED as a function [S1][S20 "IN POSITION ... to motion controllers, PLCs"] |
| Following error | on step/dir systems the controller has no encoder, so the **drive's** following-error limit (ProTuner) is the only stall detector, and ALM is how the controller learns about it | VERIFIED [S21 Motor Settings], [S5] |

### 3.3 Alarm reset - what "reset will be connected" can mean on this driver

| Method | HBS86H status | Notes |
|---|---|---|
| Dedicated reset input | **none** (control connector = PUL, DIR, ENA only [S1]) | VERIFIED |
| Power cycle | Leadshine closed-loop CS-D808/1008 (same blink codes 1/2/7): "When above protections are active, the motor shaft will be free ... Reset the drive by repowering it" [S20 §9] | VERIFIED for the sibling; **ASSUMED for HBS86H**. Over-voltage/over-current: power off and investigate first [S20]. |
| ENA toggle | Leadshine EM-series software has an option "ENA to Reset the Drive" ("The drive will restart and all the error will be clear") [S24]; ES-D/HBS manuals do not mention it | **UNKNOWN for HBS86H**; bench test (Q-R5-09) |
| Reading "reset will be connected" | most likely (a) the **ENA line used as a reset/clear** (toggle disabled → enabled), or (b) a **relay that power-cycles the driver**. With Option A, (b) already exists: **E-stop + RESET = power cycle = alarm clear**. The optional trip relay KT (§1.4) gives the FW a way to *remove* power, but restoring it stays manual. | interpretation, ASSUMED |

### 3.4 Recommended FW reactions (later release; release 1 = read and report only)

1. **ALM asserted** (EXTI on PA8 plus 2 consecutive 1 ms samples to reject glitches):
   - stop the step timer immediately
   - latch `FAULT_DRIVER_ALARM`, set the axis "not homed" (the motor was free, so position is lost)
   - set ENA = disabled
   - report the blink-code hint (1 = over-current, 2 = over-voltage, 7 = following error [S1])
   - operator acknowledgement: fix the cause → power-cycle the driver (E-stop + RESET, or ENA toggle if verified) → PC "clear" (accepted only when ALM = OK) → re-home.
2. **PEND**:
   - (a) settle gate: a "hold/capture" step starts its window only after PEND has been active ≥ 20 ms (param);
   - (b) after the last pulse of any move, expect PEND within `drv.pend_timeout = 200 ms` (param), otherwise warn `NOT_SETTLED`. This is an early stall/overload indicator below the drive's own following-error limit;
   - (c) log the PEND state in the DATA status flags (cheap; useful for analysis).
3. **Following-error limit** (ProTuner): leave the factory value in release 1. Later consider tightening it (e.g. 1000-4000 counts = 1.25-5 mm at 5 mm lead) so that a jammed axis trips earlier (Q-R5-12). The JMC default is 10 000 counts, i.e. 12.5 mm [R2 §1.8].

### 3.5 Optional hardware force cap via driver current (load-cell protection)

The torque for 300 kg (150 % FS, the cell's ultimate overload [R2 §3]) is ≈ 2.6 N·m ≈ 32 % of 8.2 N·m. Limiting the driver's closed-loop/peak current with ProTuner to about 40 % would cap the screw force near the cell's ultimate load **independently of the FW**. When exceeded, the drive raises a following-error ALM → the motor goes free → the load is released, which is the safe direction.
- Trade-off: lower stiffness and acceleration. Our needs (1.9 N·m) leave room.
- Requires the tuning cable/software (Q-R2-02 / Q-R5-08). ASSUMED (calculation + generic behaviour).

---

## 4. Current direct 3.3 V wiring (D-17 common-cathode, D-24)

### 4.1 How it works

```
 F446 PA0 (push-pull, VDD 3.3 V) ---------------> PUL+ --[270R]--[opto LED]--> PUL- --> Nucleo GND
 F446 PA1 ------------------------------------> DIR+ --[270R]--[LED]------> DIR- --> GND
 F446 PA4 (TC pin) ---------------------------> ENA+ --[270R]--[LED]------> ENA- --> GND
```

`I_LED = (VOH - Vf) / 270 Ω`.
- VOH: STM32F446 guarantees **VOH ≥ VDD - 0.4 V at 8 mA** (CMOS port, VERIFIED [S7 Table 57/58 VOH]) → ≥ 2.9 V worst case; ≈ 3.1-3.25 V typical at 6-7 mA (ASSUMED).
- Vf: the opto type inside the HBS86H is UNKNOWN. A 200 kHz input implies a fast opto: 6N137 class Vf ≈ 1.4-1.5 V typ / 1.75 V max, or a fast transistor opto ≈ 1.2-1.3 V (ASSUMED).

| VOH \ Vf | 1.20 V | 1.45 V | 1.60 V | 1.75 V |
|---|---|---|---|---|
| 2.90 V (worst) | 6.3 mA | 5.4 | 4.8 | **4.3** |
| 3.10 V | 7.0 | 6.1 | 5.6 | 5.0 |
| 3.25 V (typ) | 7.6 | **6.7** | 6.1 | 5.6 |

- **Typical ≈ 6-7 mA; worst case ≈ 4.3 mA.** Leadshine specifies **7 / 10 / 16 mA** (min/typ/max) [S1], so this is **out of spec by 0-40 %**.
- Why it runs anyway (D-24): the spec minimum guarantees full 200 kHz speed and ageing margin. A 6N137-class opto switches at IF ≈ 1.5-3 mA typ (5 mA max over temperature, ASSUMED). With 10 µs pulses at ≤ 8-16 kHz, 6 mA is 1.2-4× the threshold.
- MCU load: 3 × 7 mA = 21 mA total, fine (≤ 8 mA per pin at the guaranteed VOH).

### 4.2 Failure modes and margins

| Failure mode | Mechanism | Severity | Mitigation without a board |
|---|---|---|---|
| Dropped / short pulses | margin erodes: cold (Vf rises ≈ +2 mV/K → −0.2 mA at 0 °C), opto LED ageing (−10…−30 % light output over years, ASSUMED), threshold max at high temperature | **High**: the closed-loop drive never saw the pulse, so **the FW step count and the real position drift silently** (no ALM) | homing check: re-home periodically and compare the step count at the START switch (`HOME_DRIFT` warning > 0.05 mm); keep pulse width ≥ 10 µs (more time above threshold) |
| Noise into PUL | inputs have no hysteresis on our side. While the MCU drives low (push-pull ≈ 25-40 Ω) the loop is stiff; long untwisted wires next to the 48 V motor cable can couple PWM edges (10 pF × 48 V/50 ns ≈ 10 mA spikes, ASSUMED) | Medium: phantom steps → drift | twisted pairs (PUL+/PUL−, …), ≤ 1-2 m, ≥ 10 cm from motor/PSU wires, cross at 90°; GPIO speed = low (slew-limited) |
| ENA during MCU reset / flashing | pins are floating inputs during and after reset [S7 Table 10 note] → no LED current → **drive enabled, holding** (D-13 consistent) | Low (by design) | FW must initialise ENA low (enabled) or high (disabled) as its first action, per D-13 policy |
| Floating PUL during reset | hi-Z node loaded only by the LED: more susceptible to coupled spikes than when driven | Low-Medium: a phantom step during reset is harmless because the axis is "not homed" after boot anyway | 10 kΩ pull-down at the driver end of PUL/DIR (cheap, keeps the node defined; draws 0.33 mA when high) |
| PA4 is **TC (not 5 V tolerant)** | no issue in common-cathode 3.3 V push-pull drive | none | never re-wire PA4 as open-drain to a 5 V anode |
| Ground | PUL−/DIR−/ENA−/ALM−/PEND− on Nucleo GND; the opto keeps the 48 V side isolated, so no ground loop (VERIFIED: isolation ≥ 500 MΩ [S1]) | - | never connect Nucleo GND to the 48 V GND / PE |

**Quick bench check (when allowed):** hold PUL/ENA high from a debug build. Measure the MCU pin voltage (= VOH under load). Then temporarily insert a 100 Ω resistor in series and measure the voltage across it: I_LED = V_100Ω / 100 Ω. If I_LED < 6 mA, or VOH under load < 3.0 V, the margin is poor and the buffer board should come first.

**Verdict:** acceptable for bring-up and short tests (D-24). **Fit the 5 V buffer board before calibration and long test campaigns.** Silent position drift corrupts travel calibration and the setpoint-distance field (D-05).

---

## 5. Interface (buffer) board proposal

### 5.1 Requirements collected
- Outputs: PUL, DIR, ENA at ≈ 10-13 mA into the HBS86H opto LEDs, common-cathode (no re-wiring of the driver side), defined (inactive) state during MCU reset.
- Inputs (3.3 V dry contacts / open collector, all to FT pins):

| Input | Contact type | Filter | Notes |
|---|---|---|---|
| START, END limits | NC | ≈ 100 µs | D-18 |
| ESTOP_S | NC | ≤ 10 µs | §1 |
| **STOP/BREAK** | **NC** | ≈ 100 µs | D-26 |
| **PAUSE** | **NO** | ≈ 1 ms + SW debounce | D-26, §5.5 |
| ALM, PEND | open collector | ≈ 5 µs | |
| DRV_PWR | K1 aux contact | ≈ 1 ms | |

- HX711 connector (DOUT, SCK, RATE, power), 5 V / 3.3 V from the Nucleo, pluggable terminals, ESD protection, noise-aware layout.

### 5.2 Option A - 5 V CMOS buffer, common-cathode (recommended)

```
                 Nucleo 5V (CN6-5)                                     HBS86H control connector
                    | 100n + 10u                                       (common-cathode, unchanged)
              +-----+--------------+
  PA0 (A0) ---+-> 1A1  U1      1Y1 +-------------------------------> J1-1 PUL+   J1-2 PUL- <-- GND
          10k v                     |
  PA1 (A1) ---+-> 1A2 SN74ACT244 1Y2+-------------------------------> J1-3 DIR+   J1-4 DIR- <-- GND
          10k v   (VCC 5 V,         |
  PA4 (A2) ---+-> 1A3  TTL inputs  1Y3+-----------------------------> J1-5 ENA+   J1-6 ENA- <-- GND
          10k v   VIH 2.0 V)        |
                 1OE,2OE -> GND      |   unused A inputs -> GND
              +--------------------+
  10k pull-downs: during reset/boot the outputs are LOW -> no LED current -> PUL idle, ENA "enabled" (D-13), DIR fixed.
  I_LED = (VOH_5V ~4.6-4.9 V - Vf 1.2-1.75 V)/270 R ~ 11-13 mA  (in the 7-16 mA window)

  Input cell (one per contact input):

      3V3 --[R_pu]--+--------[R_s 1k]--+--> MCU pin (FT, Schmitt, EXTI)
                    |                  |
   terminal J -----+  ESD (TVS)       C_f
   (switch to GND)    to GND           |
                                      GND
   ALM/PEND: R_pu = 4.7k, C_f = 1 nF (tau ~5 us)

  DRV_PWR (default): K1 aux NO (dry) -> input cell (R_pu 1k, C_f 1 uF => ~1 ms).
  DRV_PWR (alternative, isolated 48 V sense, separate connector with >= 3 mm creepage):
      +48V_drv --[10k 0.5W]--[ZD 24V]--[LED LTV-817]-- 0V_48 ;  transistor: C -> node (10k to 3V3), E -> GND
      (threshold ~25 V: decaying bus reads "off"; LED ~2.3 mA)
```

Filter values (rising edge = NC contact opening, τ_rise = (R_pu + R_s)·C_f; act on the first edge in FW, debounce only the release):

| Input | R_pu | C_f | τ_rise / τ_fall | Wetting current |
|---|---|---|---|---|
| START, END (NC) | 1 k | 47 nF | 94 / 47 µs | 3.3 mA |
| ESTOP_S (NC) | 1 k | 4.7 nF | 9.4 / 4.7 µs (≤ 10 µs) | 3.3 mA |
| STOP (NC) | 1 k | 47 nF | 94 / 47 µs | 3.3 mA |
| PAUSE (NO) | 4.7 k | 220 nF | ≈ 1.3 ms / 0.2 ms | 0.7 mA (only while pressed) |
| ALM, PEND (OC) | 4.7 k | 1 nF | ≈ 5.7 µs | 0.7 mA |
| DRV_PWR (aux contact) | 1 k | 1 µF | ≈ 2 ms | 3.3 mA |

The F446 input switching thresholds are VIH ≥ 0.45·VDD + 0.3 = 1.79 V and VIL ≤ 1.12 V, with ≈ 0.33 V hysteresis (VERIFIED [S7 Table 56]). If silver-contact microswitches show wetting problems, lower R_pu to 680 Ω (4.9 mA). The constant current from the Nucleo 3V3 with all NC contacts closed is ≈ 4 × 3.3 mA = 13 mA (OK).

**BOM (Option A, examples; prices ASSUMED):**

| Qty | Part | Example P/N | ≈ € |
|---|---|---|---|
| 1 | Octal buffer, 5 V, TTL inputs, ±24 mA | TI SN74ACT244N (DIP) / SN74ACT244DWR (SOIC) | 1 |
| 3 | 10 kΩ pull-down (buffer inputs) | 0805 | 0.1 |
| 7 | input cells: 1 k / 4.7 k R, 1 k series R, C (47 n / 4.7 n / 1 n / 220 n / 1 µ) | 0805, X7R | 1 |
| 2 | 4-ch ESD TVS array | TI TPD4E05U06 (or Nexperia PESD5V0S1BA ×8) | 2 |
| 1 | 100 nF + 10 µF decoupling at U1 | | 0.3 |
| 1 | J1 driver: 10-pole pluggable 3.81 mm (PUL±, DIR±, ENA±, ALM±, PEND±) | Phoenix MC 1,5/10-ST-3,81 + header | 5 |
| 1 | J2 limits: 4-pole (START, GND, END, GND) | MC 1,5/4 | 3 |
| 1 | J3 panel: 8-pole (ESTOP_S, GND, STOP, GND, PAUSE, GND, DRV_PWR, GND) | MC 1,5/8 | 4 |
| 1 | J4 HX711: 6-pin JST-XH or screw (VCC 5 V, VDD 3.3 V, GND, DOUT, SCK, RATE); 10 kΩ RATE pull-up to HX711 DVDD (80 SPS during reset), 33 Ω series on SCK | | 2 |
| 1 | Arduino-Uno stacking headers (fits Nucleo CN5/6/8/9) | | 3 |
| 1 | 2-layer PCB (5 pcs, JLC/Aisler) | | 10-25 |
| | **Total per board** | | **≈ €35-50** (+ 1-2 days design/layout) |

Power: 5 V from the Nucleo 5 V pin (from USB via the ST-LINK when USB-powered; ASSUMED UM1724), load < 60 mA. If the Nucleo is powered by the DC-DC (D-22), the same pin works.

Grounding and shielding:
- Board GND = Nucleo GND = opto return side only.
- Driver control cable: shielded twisted-pair (e.g. LiYCY 6×2×0.25 mm²), **shield to board GND at the board end only**.
- Motor cable shield to driver PE.
- Keep the board ≥ 20 cm from the driver and PSU, and the HX711 cable away from motor/48 V wires (R2 §6.5).
- No 48 V copper on this board in the default variant (DRV_PWR via the dry K1 contact).

### 5.3 Option B - Differential line driver (AM26LS31 class)

```
 PA0 -> AM26LS31 1A -> 1Y -> PUL+ ; 1Z -> PUL-     (driver PUL- no longer at GND!)
 PA1 -> 2A -> DIR+/DIR- ; PA4 -> 3A -> ENA+/ENA- ; enable G=VCC ; VCC 5 V
```
- Leadshine supports differential inputs [S1][S20]. The differential output swing is only ≈ 3.0-3.6 V at these currents (VOH ≈ 3.4 V, VOL ≈ 0.2-0.3 V, ASSUMED from the AM26LS31 class) → **≈ 5.7-8 mA**: barely better than direct 3.3 V.
- Benefit: noise immunity on long cables (> 3 m) and no ground reference shared through PUL−.
- Cost ≈ €2 IC + re-wiring of the driver side. **Not recommended** for this stand (cable < 2 m).
- If differential is ever needed, a better solution is complementary drive from SN74ACT244 + 74ACT04: ±4.8 V → ≈ 12 mA balanced.

### 5.4 Option C - Off-the-shelf / perfboard

| Candidate | Verdict |
|---|---|
| 74AHCT125 DIP (e.g. Adafruit #1787) on an Arduino proto-shield, hand-wired with screw terminals | **OK** - Option A in perfboard form, ≈ €20, half a day. AHCT ±8 mA spec still gives ≈ 11 mA at slightly lower VOH (ASSUMED). Good as an interim solution. |
| BSS138 "bidirectional level shifter" boards (SparkFun-type) | **Not suitable**: 10 kΩ pull-ups cannot source mA into an LED |
| TXS0108E / TXB0108 auto-direction shifters | **Not suitable**: weak outputs, not designed for DC loads |
| PC817 opto-isolator modules (4-8 ch) | **Not suitable for PUL**: PC817 rise/fall times of several µs to ≈ 18 µs (ASSUMED) distort 10 µs pulses; inverting; extra supply needed |
| CNC breakout boards (parallel-port style) | not applicable (no MCU interface) |

### 5.5 STOP / PAUSE buttons (D-26) - contact type and handling

| Button | Contact | Why | FW handling |
|---|---|---|---|
| **STOP/BREAK** | **NC to GND (confirmed)**, direct-opening contact block; **red flush pushbutton**, *not* a red/yellow mushroom (must not look like the E-stop, EN ISO 13850) | wire break or unplugged connector → STOP (fail-safe, positive-opening principle). Costs nothing extra. | EXTI on the opening edge (rising), priority 0 → operational stop (holding, D-10), sequence terminated (D-26), latch until PC clear. Release debounce 20 ms in the 1 kHz tick. Disable the EXTI after the first edge, re-arm after a debounced release. |
| **PAUSE** | **NO to GND** (momentary), yellow or black button; polarity is a param so NC is possible | toggle-type function: a broken wire should not keep generating pause/resume events. Non-safety function (STOP and E-stop cover safety). | edge-triggered: press = pause (controlled stop, holding); if D-14 "resume by button" is confirmed, press again = resume, with a minimum of 300 ms between toggles + 20-30 ms debounce. Polling at 1 kHz is sufficient (EXTI optional). |
| RESET (power, §1) | NO, blue | IEC 60204-1 colour for reset | not an MCU input (hardware K1 self-hold) |

---

## 6. Pin map check (R1 §9 → updated)

Verification source: STM32F446xC/E datasheet **DocID027107 Rev 6** [S7], Table 10 (pin/ball descriptions, I/O structure, AF list) and Table 56 (I/O static characteristics). Arduino header numbering: stm32duino `variant_NUCLEO_F446RE.h:18-74` [S8].

Findings:
1. **All proposed digital inputs are FT (5 V tolerant)**: PA0, PA1, PA6-PA10, PB0, PB1, PB4-PB6, PB9, PB10, PC0, PC1, PC13; PC6/PC7 are FTf. VERIFIED [S7].
2. **PA4 and PA5 are "TC" (standard 3.3 V I/O), not "TTa".** R1/R2 wrote TTa; the conclusion is unchanged: they are not 5 V tolerant. VERIFIED [S7 Table 10].
3. PC13 (B1) can sink or source only ±3 mA and is limited to 2 MHz as an output [S7 §6.3.x "Output driving current"]. Fine as an input. VERIFIED.
4. PA10 has a stronger internal pull-up (7-14 kΩ, OTG_FS_ID). Irrelevant with external pull-ups. VERIFIED [S7 Table 56].
5. PA6 carries **TIM1_BKIN / TIM8_BKIN** (and PB12 TIM1_BKIN). If the FW ever moves PUL to TIM8_CH1 (PC6), the E-stop/STOP sense can force PUL idle in hardware. **Keep PA6 free** for this. VERIFIED [S7 Table 10]. R4's TIM2 scheme (no BKIN) stays the baseline.
6. EXTI conflicts in R1: HX711 DOUT (EXTI5) shared the EXTI9_5 vector with ALM (and now STOP/PAUSE), and so would have run at E-stop priority, or STOP at HX711 priority. **Fix: DOUT → PB4 = EXTI4, its own vector**; RATE → PB5. R1's "free" PC0/PC1 would clash with EXTI0/1 of PB0/PB1 if used as interrupts; only one port per EXTI line.
7. END limit on PB1 is morpho-only. Moving it to **PC1 (A4, still EXTI1)** puts every signal on the Arduino headers, so the interface board can be a shield. PB1 remains an equivalent fallback for the current hand wiring.

### 6.1 Updated pin table

| Signal | Pin | Nucleo header (Arduino / morpho) | Peripheral / AF | EXTI line → vector, NVIC level | Dir / electrical | 5 V tol. | vs R1 §9 |
|---|---|---|---|---|---|---|---|
| **PUL** | PA0 | A0 / CN7-28 | TIM2_CH1 AF1 (alt. TIM5_CH1) | - | out PP, low speed slew | FT | same |
| **DIR** | PA1 | A1 / CN7-30 | GPIO | - | out PP | FT | same |
| **ENA** | PA4 | A2 / CN7-32 | GPIO | - | out PP only (never OD to 5 V) | **TC (no)** | same (TC, not TTa) |
| ALM in | PA8 | D7 / CN10-23 | GPIO | 8 → EXTI9_5, L0 | in, ext. 4.7 k PU | FT | same |
| PEND in | PA9 | D8 / CN10-21 | GPIO | (9 → EXTI9_5) - polled 1 kHz | in, ext. 4.7 k PU | FT | same |
| **HX711 DOUT** | **PB4** | **D5** / CN10-27 | GPIO (NJTRST after reset, released when reconfigured as GPIO - ASSUMED) | **4 → EXTI4 (own vector), L2** | in | FT | **changed** (was PB5) |
| **HX711 SCK** | PB10 | D6 / CN10-25 | GPIO | - | out PP, init low | FT | same |
| **HX711 RATE** | **PB5** | **D4** / CN10-29 | GPIO | - | out PP; ext. 10 k pull-up to HX711 DVDD (80 SPS in reset) | FT | **changed** (was PB4) |
| **START limit (home)** | PB0 | A3 / CN7-34 | GPIO | 0 → EXTI0, L0 | in, NC to GND, ext. 1 k PU | FT | same |
| **END limit** | **PC1** (fallback PB1) | **A4** / CN7-36 (PB1: CN10-24) | GPIO | 1 → EXTI1, L0 | in, NC, ext. 1 k PU | FT | **changed** (header access) |
| **E-stop sense (NC2)** | PA10 | D2 / CN10-33 | GPIO | 10 → EXTI15_10, L0 | in, NC, ext. 1 k PU, ≤ 10 µs RC | FT | same |
| **STOP/BREAK button (NC)** | **PC7** | **D9** / CN10-19 | GPIO | 7 → EXTI9_5, L0 | in, NC, ext. 1 k PU | FTf | **new** (D-26) |
| **PAUSE button (NO)** | **PB6** | **D10** / CN10-17 | GPIO | 6 → EXTI9_5 (or polled) | in, NO, ext. 4.7 k PU | FT | **new** (D-14/26) |
| PAUSE alt. (dev) | PC13 | B1 USER / CN7-23 | GPIO | 13 → EXTI15_10 | in, board pull-up (ASSUMED) | FT (±3 mA) | optional: B1 acts as PAUSE in debug builds |
| **DRV_PWR sense** (opt.) | **PA7** | **D11** / CN10-15 | GPIO | polled 1 kHz (EXTI7 taken by PC7) | in, K1 aux NO or opto | FT | **new** |
| TRIP relay out (opt., later) | PB9 | D14 / CN5-10 | GPIO | - | out PP → NPN → relay KT (NC in K1 hold path) | FT | **new**, provision only |
| Status LED | PA5 | D13 (LD2) | GPIO | - | out | TC | same |
| PC link | PA2 / PA3 | ST-LINK VCP | USART2 AF7, DMA1 S6/S5 Ch4 | - | - | FT | same |
| SWD / SWO | PA13 / PA14 / PB3 | - / D3 | SYS | - | keep free | FT | same |
| Reserved | PA6 | D12 | **TIM1/TIM8_BKIN** | - | keep free | FT | reserve (finding 5) |
| Spare | PB8 (D15), PC0 (A5), PB1, PC8/PC9 (scope markers) | | | | | FT | |

EXTI map (one port per line, VERIFIED unique): 0 PB0 · 1 PC1 · 4 PB4 · 6 PB6 · 7 PC7 · 8 PA8 · (9 PA9) · 10 PA10 · (13 PC13).

NVIC proposal (supersedes R1 §9.4):

| Level | Source |
|---|---|
| 0 | EXTI0, EXTI1, EXTI9_5, EXTI15_10 (E-stop, limits, STOP, ALM, PAUSE - ISRs ≤ 1 µs, flag + timer-off only) |
| 1 | TIM2 step |
| 2 | EXTI4 HX711 DOUT |
| 3-4 | USART2/DMA |
| 5 | SysTick |

The HX711 SCK-high critical section uses BASEPRI = level 1, so level-0 safety ISRs still preempt it (R2 §4.4).

---

## 7. Questions for the PO and decision summary

### 7.1 Questions (with recommended defaults)

| ID | Question | Default if unanswered |
|---|---|---|
| Q-R5-01 | E-stop circuit: Option A (contactor self-hold + manual RESET, ≈ €110) or B (safety relay PSR-SCP-42-48UC + contactor, PL d/e, ≈ €320)? | **A**, with panel space reserved for B |
| Q-R5-02 | Contactor coil supply: 48 V DC coil from the driver PSU, or a separate 24 V supply? | **48 V DC coil** (one supply fewer; PSU loss → power stays off until RESET) |
| Q-R5-03 | Is the axis horizontal? Is there any gravity load on the table/screw when unpowered (crosshead weight, hanging mass)? | **Horizontal, no gravity load → no brake.** If vertical: motor with brake required |
| Q-R5-04 | Provision an MCU-driven trip relay (can only *remove* driver power, never restore) for later automatic fault reactions? | **Yes, provision** (PB9, KT relay), not used in release 1 |
| Q-R5-05 | Which 48 V PSU is (or will be) installed? | **Mean Well SDR-480-48** (or HRP-300-48); **not** LRS-350-48 (latch-off) |
| Q-R5-06 | Interface board: build Option A as a shield PCB (≈ €50, 1-2 days) or a perfboard interim (≈ €20)? When - before calibration? | **Perfboard interim for bring-up, PCB before calibration/test campaigns** |
| Q-R5-07 | Accept the pin changes (DOUT → PB4, RATE → PB5, END → PC1, STOP → PC7, PAUSE → PB6, DRV_PWR → PA7)? | **Yes** |
| Q-R5-08 | Is the Leadshine RS232 tuning cable/software available? May we limit the driver current (force cap ≈ 150 % FS) as hardware load-cell protection? | **Evaluate at the HW gate**; no change in release 1 |
| Q-R5-09 | Alarm reset on the actual unit: test ENA toggle vs power cycle on the bench? | **Power cycle (E-stop + RESET)** is the documented method; ENA test at the HW gate |
| Q-R5-10 | STOP = NC red flush button, PAUSE = NO momentary (press toggles pause/resume)? | **Yes** (as §5.5) |
| Q-R5-11 | In release 1 (report only, D-16), may the FW at least **block new motion** while ALM = fault and DRV_PWR = on? (Pulses into a faulted drive are meaningless.) | **Yes, block start; no automatic stop/disable** (D-16 respected) |
| Q-R5-12 | Tighten the drive's following-error limit later (1000-4000 counts = 1.25-5 mm)? | Factory value in release 1 |

### 7.2 Decision summary (for the Orchestrator to present)

| Decision | Options | Recommendation | Cost / effort |
|---|---|---|---|
| E-stop power removal (D-11) | A contactor self-hold · B safety relay · C AC side | **A** (upgrade path B) | A ≈ €110, ½ day · B ≈ €320, 1 day · C ≈ €100 + 230 V work |
| Stop category | 0 · 1 (delayed) | **0** (cat. 1 adds nothing; load is released either way) | - |
| Brake | none · motor brake | **none if horizontal** (Q-R5-03) | brake motor ≈ €150-250 if vertical (ASSUMED) |
| 48 V PSU | SDR-480-48 · HRP-300-48 · LRS-350-48 · linear | **SDR-480-48** | ≈ €100 (ASSUMED) |
| Speed defaults | - | **20 mm/s loaded, 30 mm/s travel**, raise after bench test | FW params |
| Driver interface | direct 3.3 V (now) · A buffer · B differential · C perfboard | **direct for bring-up → A (PCB) before calibration**; C as interim | A ≈ €50 + 1-2 days; C ≈ €20 + ½ day |
| STOP / PAUSE contacts | NC / NO | **STOP NC, PAUSE NO** | ≈ €10 each |
| ALM / PEND usage | report only (R1) · react (later) | **R1: report + block start (Q-R5-11); later: §3.4** | FW only |
| Alarm reset | power cycle · ENA toggle · trip relay | **power cycle via E-stop/RESET**; ENA test on bench | none |
| Pin map | R1 §9 · updated §6.1 | **§6.1** | re-wiring of 4 signals |

---

## 8. Findings for other roles

- **Orchestrator / SRS (SAF/FW):**
  - E-stop sense input (NC, ≤ 10 µs filter, EXTI L0) and DRV_PWR input
  - ENA ≥ **500 ms** before the first DIR/PUL after driver power-up (R2 said 200 ms)
  - "driver power lost → not homed" from DRV_PWR/ALM
  - STOP NC / PAUSE NO (D-26)
  - FW plausibility checks `FAULT_K1` (E-stop open but power still on) and `HOME_DRIFT` (step-count check at home)
  - motion speed defaults 20/30 mm/s.
- **Implementer A (FW / 01_HW):**
  - pin map §6.1 and NVIC levels
  - PA4/PA5 are TC
  - DOUT on EXTI4
  - PB4 is NJTRST after reset (reconfigure as GPIO; verify that stm32duino does not keep JTAG)
  - GPIO speed "low" for PUL/DIR/ENA
  - write `01_HW/pinout.md` and `01_HW/wiring.md` (E-stop circuit §1.3 A, interface board §5.2).
- **R2 corrections:** PA4/PA5 "TTa" → **TC** (same consequence); ENA set-up 200 ms → 500 ms (Leadshine CS-D sequence chart); the newer HBS86H text says inputs accept 3.3-5 V (R2 [S4]) but the genuine 2012 datasheet says 4-5 V, so the 7 mA minimum still applies.
- **Integrator (ICD/params):** add status flags `DRV_PWR`, `ALM`, `PEND`, `STOP_BTN`, `PAUSE_BTN`; params `drv.alm_active_level`, `drv.pend_active_level`, `drv.pend_timeout_ms`, `drv.ena_setup_ms = 500`, `motion.v_max_load`, `motion.v_max_travel`, `io.pause_contact_nc`.

---

## 9. Sources

- [S1] Leadshine "Hybrid Servo Drive HBS86 Datasheet" (HBS86H, 2012) - via R2 [S1], mirror https://kitaez-cnc.com/f/hbs86h.pdf ; text dump `scratchpad/researcher-R2/kit.txt`. Sections: Electrical Specifications, Control Signal Connector, Stator Signal Connector, Protection Indications, Typical Connections (270 Ω).
- [S4] Newer HBS86H text (3.3-5 V; "24-100 VDC"), search snippets only - see R2 [S4].
- [S5] LinuxCNC forum, "help to connect alarm signal driver hbs86h": https://www.forum.linuxcnc.org/27-driver-boards/53695-help-to-connect-alarm-signal-driver-hbs86h
- [S7] ST STM32F446xC/E datasheet, DocID027107 Rev 6 (Sept 2016), mirror https://pdf.direnc.net/upload/stm32f446ret6-datasheet.pdf - Table 10 (pin descriptions: FT/FTf/TC, AF incl. TIM1/TIM8_BKIN on PA6), Table 56 (VIL/VIH/VHYS, RPU incl. PA10), output driving current (PC13-15 ±3 mA), VOH ≥ VDD−0.4 V @ 8 mA. (st.com download reset the connection.)
- [S8] stm32duino `variants/STM32F4xx/F446R(C-E)T/variant_NUCLEO_F446RE.h:18-74` (local PlatformIO package) - Arduino D/A numbering.
- [S9] Phoenix Contact PSR-SCP-42-48UC/ESAM4/3X1/1X2/B, 2901416, datasheet: https://asset.conrad.com/media10/add/160267/c1/-/en/000509853DS03/datasheet-509853-safety-relays-psr-scp-42-48ucesam43x11x2b-2901416-phoenix-contact.pdf
- [S10] Pilz PNOZ s3 C (RS 1919855): https://uk.rs-online.com/web/p/safety-relays/1919855 (DC1 24 V / 6 A, price)
- [S11] Eaton DILM7-10(24VDC), 276565, datasheet: https://shop.magnet.co.za//download-catalogue/eaton/s276565-document.pdf (DC-1 60 V 20 A; coil 3 W; delays - PDF table alignment partly uncertain)
- [S12] Mean Well LRS-350 spec: https://www.meanwell.com/Upload/PDF/LRS-350/LRS-350-SPEC.PDF
- [S13] Mean Well SDR-480 spec: https://www.meanwell.com/Upload/PDF/SDR-480/SDR-480-SPEC.PDF
- [S14] Mean Well HRP-300 spec: https://www.meanwell.com/Upload/PDF/HRP-300/HRP-300-SPEC.PDF
- [S15] Back-driving of ball screws, reverse efficiency 0.8-0.9: https://www.linearmotiontips.com/how-to-determine-if-a-screw-will-back-drive/
- [S16] THK "Permissible rotational speed" (critical speed, DN): https://www.thk.com/eu/en/products/ball_screw/selection/0007/ (search summary; DN ≤ 50 000 rolled = ASSUMED)
- [S20] Leadshine CS-D808 & CS-D1008 Closed Loop Stepper Drive User Manual Rev 3.1 (2018): https://www.leadshine.com/upfiles/downloads/01662380903d5d0194c3919856d9668f_1665570042922.pdf - §3.1.1 (ENA, PEND/brake, ALM), §5 (PSU selection), §8 (sequence: ENA ≥ 500 ms before DIR), §9 (protections; "Reset the drive by repowering it").
- [S21] Leadshine "Software Manual of the Easy Servo Drives" (ES-D ProTuner) v1.0, 2013: https://www.leadshine.com/upfiles/downloads/d13ea5b890520337e3e6e23231ac9da6_1660273170813.pdf - Inputs/Outputs window, Save Drive Parameters, serial-cable caution, Motor Settings (following-error limit).
- [S22] LinuxCNC `motion(9)` man page: https://linuxcnc.org/docs/html/man/man9/motion.9.html (`joint.N.amp-fault-in`, `amp-enable-out`, `motion.enable`)
- [S23] MASSO forum, "Wiring Leadshine ES-D808 with MASSO": https://forums.masso.com.au/threads/wiring-leadshine-es-d808-with-masso.680/
- [S24] Leadshine "Software Operational Manual for EM Series Stepper Drives" SM-EM-R20151210: https://www.leadshine.com/upfiles/downloads/576c73b9d247bbfec0708931b3e02938_1660122354769.pdf - "ENA to Reset the Drive" option.
- R2 `00_System/research/R2_hardware_components.md` (§1, §2, §5.3, §6, §7.3) and R1 `00_System/research/R1_stefan_reference.md` §9 - not repeated.
- Calculations: `scratchpad/researcher-R5/torque.py` (torque-speed model, back-drive, inertia/regen, critical speed, opto current).
