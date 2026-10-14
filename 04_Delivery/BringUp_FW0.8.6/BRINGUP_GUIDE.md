# Bird Bend Stand — board bring-up guide for the operator (FW 0.8.6)

| | |
|---|---|
| Version | **0.4 DRAFT** (2026-10-14; rev.: 3.3.2 END margin ≥ 5 mm, 6.7 expected values; 10.1.5 repeat decision mandatory; 10.1.4 / 10.2.1 re-enter the no-specimen mode and re-home; 9.5 / 9.8 stop must end the jog) — Validator E review of section 10 applied: ENA I_LED measured with the 48 V off after an E-stop clear, DIR shunt fitted afterwards, mid-stroke DIR jog; pulse integrity with a 3-re-home baseline and the criterion |d1| ≤ max(30 µm, 2 · max|d0ᵢ|) (SRS v0.6.8 SYS-011), HOMED precondition, E_RANGE rule, END margin ≥ 5 mm; wiring.md v0.7.1. · 0.3 — D-56: no buffer board → section 10 direct-drive qualification (SYS-011); stable step ids; printed companion of **Tools ▸ Bring-up assistant** (SW-BRU-001…004), deliverable = **Create bring-up package** (section 13); calibration → section 11. · 0.2 — Validator E / Implementer D review findings, FW 0.8.6 build `20261010-2135`. · 0.1 — first draft |
| Owner | Implementer A (FW / HW) |
| For | the product owner's technician who wires and starts the stand for the first time |
| Package | `04_Delivery/BringUp_FW0.8.6/` — firmware **FW 0.8.6**, build string `20261010-2135` (ICD 0.7.5, dictionary hash `0xF8BCDCB8`) |
| Sources | `01_HW/wiring.md` v0.7.1 (§2.2 qualification), `01_HW/pinout.md` v0.6, `02_FW/docs/FW_test_plan.md` §6.8 (bench safety procedure, PO-approved, D-49 b / D-45), `03_SW/docs/USER_MANUAL.md` v1.0, DECISIONS D-27, D-41, D-42, D-45, D-54 |

**How to use this guide.** Work through the sections in order. Every step has a box `[ ]` — tick it when the step
gives the expected result and write measured values in the record sheet (annex B). **"If not → STOP"** means: stop
there, make the stand safe (48 V PSU off), write down what you saw (photo, screenshot) and ask the maintainer / PO
before going on. Never skip a STOP. Texts in **bold** or in `code` are the exact labels of the PC application or the
firmware.

**Printed companion of the Bring-up assistant.** The PC application has **Tools ▸ Bring-up assistant**
(SW-BRU-001…004): it shows the same steps with the **same step ids**, checks automatically what the board reports,
records your entries and measured values, and at the end builds the deliverable — **one zip** made with **Create
bring-up package** (section 13), which you send to the PO. Use this printout next to the assistant; annex B is the
paper fallback. **Step ids** (bold number at the start of each step, e.g. **7.2**; table rows of 3.2 are **3.2-1…**)
are stable: they are never renumbered — a new step gets the next free number, a removed id is not reused.

> **This guide is the operator's bring-up, not the hardware gate.** The formal hardware gate (HG-01…HG-32,
> `02_FW/docs/FW_test_plan.md` §6) is run later by Validator E with the HIL scripts
> (`00_System/tools/hil/HW_GATE_RUNBOOK.md`) after the PO has given a date and the Orchestrator has recorded a
> dedicated **D-06-GATE** approval row. This guide does **not** replace any HG item and its results are **not** HG
> evidence. Steps that check the same thing as an HG item are marked *(≈ HG-xx)* — annex A lists them; at the gate
> Validator E decides whether a bring-up record (photo, DIP state, measured value) can be re-used or the item is
> repeated. Nothing in this guide uses the measurement images (separate gate kit `04_Delivery/GateKit_FW0.8.6/`),
> the measurement header or a stimulus on the E-stop input — those belong to the gate only.

---

## 0. Safety rules (read before you touch anything)

These rules are the constraints of the PO-approved bench procedure (FW_test_plan §6.8 P-1, P-7; D-45) applied to
the bring-up. They are not negotiable.

- [ ] **0.1** **The red E-stop is not an IEC 60204-1 emergency stop** (D-41). It stops the pulses in the firmware and cuts
  the driver's ENA input directly through a second contact (D-42), but the 48 V supply stays on. **The only power
  removal is the mains switch of the 48 V PSU.** Keep both — red button and PSU switch — within reach whenever the
  48 V is on.
- [ ] **0.2** **Two persons whenever the driver is powered** (§6.8 P-1): the *operator* at the PC and the *observer* at the
  operator panel with a hand near the red button and the PSU switch.
- [ ] **0.3** **No specimen, no load on the table, no tool in the travel** for the whole bring-up (§6.8 P-1). The only load is
  the calibration weights hanging on the load cell in section 11, with the axis standing still and **PAUSED**.
- [ ] **0.4** **Hands clear** of the ball screw, the table and the load path while the driver is powered. The motor is
  energised as soon as the 48 V is on (the driver holds even with the board reset, D-13). Touch the coupling only in
  the steps that ask for it, and only after the operator has said "hands off the mouse". **Hands off the shaft before
  the E-stop or the RESET button is released and before any Clear / ENABLE** — the closed-loop driver re-energises at
  once.
- [ ] **0.5** **First motion is slow:** jogs at **1 mm/s** (the firmware caps un-homed motion at 2 mm/s), homing at the
  firmware's homing speed (≤ 5 mm/s), every **Go to** in this guide at **5 mm/s** (never above 10 mm/s). The only
  exception is the pulse-integrity check 10.2 at **30 mm/s** (= the §6.8 P-1 limit; no specimen, nothing in the travel,
  the observer's hand at the red E-stop).
- [ ] **0.6** **Never bypass the E-stop sense input** (no jumper, no stimulus, no unplugged sense connector while the driver
  is powered). The §6.8 bypass is part of the HW gate only.
- [ ] **0.7** **Abort criteria** (§6.8 P-7): any unexpected motion, noise, smell, a driver alarm (**ALM** red with 48 V on), or
  the table outside the expected position → the **observer presses the red E-stop at once** (independent D-42 path)
  and switches the **PSU off**; the operator releases any jog button and presses **STOP**. Write an incident note
  (annex B).
- [ ] **0.8** **Wiring changes only with everything off:** PSU mains switch off **and** Nucleo USB unplugged. The driver DIP
  switches are changed only with the driver unpowered.
- [ ] **0.9** **Never mount a specimen while the amber banner `NO-SPECIMEN MODE …` is shown** (USER_MANUAL §5).
- [ ] **0.10** Start only when the **PO has released this board for bring-up** (D-06: flashing and opening the COM port need
  the PO's go). Record the PO's release (name, date) in annex B.

---

## 1. What is in the package

| Item | Content |
|---|---|
| `README.txt` | where to start |
| `BRINGUP_GUIDE.md` / `.html` | this guide (the `.html` opens in any browser) |
| `firmware/BBS_FW_0.8.6_release.bin` | release image for **drag-and-drop** (first bring-up; wipes the saved configuration) |
| `firmware/BBS_FW_0.8.6_release.hex` | release image for **STM32CubeProgrammer** (updates; keeps the saved configuration) |
| `firmware/FLASHING.md` / `.html` | how to flash and verify — [FLASHING](firmware/FLASHING.md) |
| `firmware/SHA256SUMS.txt`, `firmware/BUILD_INFO.txt` | file hashes; build identity (version, build string, toolchain, reproducible-build data) |
| `diagrams/sheet1…sheet4 .png / .svg` | wiring diagram, A4 landscape, print them: 1 system overview · 2 NUCLEO pin-level wiring · 3 input cells, ENA diode OR, multimeter points · 4 driver terminals, DIP switches, supply |
| `pc_software/` | **added by the Orchestrator:** the Windows PC application `BirdBendStand-<version>.zip` (from `03_SW/packaging`; no Python needed). Its `docs\USER_MANUAL.html` and `docs\QUICK_REFERENCE.html` are the operator manual referred to below as "USER_MANUAL §n". |

The measurement images for the hardware gate are **not** in this package: they are in the separate gate kit
`04_Delivery/GateKit_FW0.8.6/`, used only in a gate session with Validator E — never for normal operation.

---

## 2. Tools and parts

- [ ] **2.1** Multimeter (V, mA, Ω, MΩ range; diode test) with **two 100 Ω 1 % shunt resistors**, one **10 Ω** resistor
  and clip leads (section 10.1)
- [ ] **2.2** Caliper 0.02 mm (a dial gauge on a magnetic stand is optional); a steel rule for the switch span (3.3)
- [ ] **2.3** Calibration weights **1 kg and 10 kg** with a hook / sling for the load cell (D-22, D-35 G2)
- [ ] **2.4** Small screwdriver (DIP switches, terminals), cable ties, labels
- [ ] **2.5** Phone / camera for the photos listed in section 13
- [ ] **2.6** Printed wiring diagram sheets 1–4 and this guide
- [ ] **2.7** Windows 10/11 PC with a free USB port and the PC software from `pc_software/`; STM32CubeProgrammer only if
  drag-and-drop does not work (FLASHING §3)
- [ ] **2.8** The bench built per the diagram (P3_READINESS §2 A.1): HX711 **RATE wire to PB5** (pin 15 lifted, D-21);
  **D-42 E-stop NO contact → +5V_CUT → R_E → D2 → ENA+**; operator panel (red E-stop, PAUSE); START / END NC
  switches. **No buffer board** (D-56 b): the direct 3.3 V drive is qualified in section 10; the SN74ACT244 board is
  needed only if that qualification fails
- Not needed for the bring-up (HW gate only): the gate kit, measurement-header jumpers, the 1 kΩ / 220 Ω jumper
  resistors, the "E-STOP SENSE BYPASSED" tag, the spring specimen.

---

## 3. Wiring check with all power OFF

Preconditions: 48 V PSU **mains switch off**, Nucleo **USB unplugged**, E-stop released. Use the diagram sheets.

### 3.1 Visual inspection *(≈ HG-01 / C-01, HG-20 / C-20)*
- [ ] **3.1.1** Nucleo NUCLEO-F446RE (MB1136): solder bridges as sheet 2 box "Solder bridges": **SB13, SB14 ON · SB62, SB63
  OFF · SB16, SB50 ON · SB54, SB55 OFF · SB51, SB56 ON · SB46, SB52 OFF**; JP5 = U5V (USB power). Photo of both sides.
- [ ] **3.1.2** Nothing is wired to D0 / D1 (PA3 / PA2 are the PC link).
- [ ] **3.1.3** Every connection of sheet 2 is present at the pin given there (Arduino name and CN number — the CN numbers are
  ASSUMED from the Nucleo manual: compare with the board silkscreen): PUL A0 · DIR A1 · ENA A2 (through D1) · START A3
  · END A4 · ALM D7 · PEND D8 · E-stop sense D2 · PAUSE D10 · HX711 DOUT D5 · PD_SCK D6 (33 Ω) · RATE D4.
- [ ] **3.1.4** Driver "Signal" terminals: PUL− , DIR− , ENA− go to the **Nucleo GND** (common cathode); "OUT": ALM− , PEND− to
  Nucleo GND (sheet 4).
- [ ] **3.1.5** E-stop circuit as sheet 3 "ENA: MCU drive OR hardwired E-stop cut": NC block → PA10; NO block →
  +5V_CUT → R_E 68 Ω → D2 → ENA+; D1 between PA4 and ENA+; 10 kΩ ENA+ → GND; **no contactor,
  nothing from the MCU in the NO path**; a label "not an IEC 60204-1 emergency stop" on the panel.
- [ ] **3.1.6** The +5V_CUT DC-DC is fed from the 48 V bus **after F1** (sheet 1); F1 = T10A fitted.
- [ ] **3.1.7** Cable routing: control cable and load-cell cable ≥ 10 cm from motor / 48 V wires, crossings at 90°.

### 3.2 Multimeter, resistance / diode test (everything unpowered)

| # | Measure between | Condition | Expected | ✓ |
|---|---|---|---|---|
| 3.2-1 | START signal wire ↔ GND at the interface connector | switch not touched / pressed by hand | ≈ 0 Ω / open (OL) | [ ] |
| 3.2-2 | END signal wire ↔ GND | not touched / pressed | ≈ 0 Ω / open | [ ] |
| 3.2-3 | E-stop NC block terminals | released / pressed | ≈ 0 Ω / open | [ ] |
| 3.2-4 | E-stop NO block terminals | released / pressed | open / ≈ 0 Ω | [ ] |
| 3.2-5 | PAUSE contact | released / pressed | open / ≈ 0 Ω | [ ] |
| 3.2-6 | across R_E | – | 68 Ω (± 5 %) | [ ] |
| 3.2-7 | D1 and D2, diode test, red probe on the anode | anode = PA4 side (D1), R_E side (D2) | ≈ 0.2…0.4 V forward, OL reverse | [ ] |
| 3.2-8 | driver 48 V GND (PSU 0 V) ↔ Nucleo GND (*≈ M-5, HG-20*) | USB unplugged, PSU off | **≥ 10 MΩ** | [ ] |
| 3.2-9 | PE (PSU earth terminal) ↔ Nucleo GND | USB unplugged | ≥ 10 MΩ | [ ] |
| 3.2-10 | motor cable shield ↔ driver PE | – | ≈ 0 Ω | [ ] |
| 3.2-11 | control-cable shield ↔ Nucleo GND (board end); same shield ↔ driver terminals | – | ≈ 0 Ω; open (shield connected at the board end only) | [ ] |
| 3.2-12 | load-cell yellow (shield) ↔ HX711 GND | – | ≈ 0 Ω | [ ] |
| 3.2-13 | PSU + → F1 → driver +Vdc; PSU 0 V → driver GND | – | ≈ 0 Ω each, polarity as labelled | [ ] |

If any line differs → **STOP** and correct the wiring (everything off) before going on. 3.2-8 / 3.2-9 below
10 MΩ means the isolation barrier is bridged somewhere — never power the stand like that.

### 3.3 Limit-switch positions, by hand *(≈ HG-25 travel part, A-07)*
With all power off the motor is free: move the table slowly by turning the coupling / screw by hand.

- [ ] **3.3.1** Move the table toward the **START** end with the multimeter on 3.2-1 (continuity): the START switch must open
  (≈ 0 Ω → OL) **at least 3 mm before** the table reaches any mechanical stop (end block, nut end, coupling).
  Measure the remaining distance to the stop.
- [ ] **3.3.2** Same at the **END** end with 3.2-2: the END switch opens **≥ 5 mm** before any mechanical stop.
- [ ] **3.3.3** Measure the **switch-to-switch span** (table position where START opens → where END opens): **≥ 295 mm**.
- [ ] **3.3.4** Write the two margins and the span into annex B; leave the table near the middle (≥ 20 mm from both switches).
- Margin < 3 mm, a switch that does not open, or a span < 295 mm → **STOP** and adjust the switch mounting.

---

## 4. Driver DIP switches — driver unpowered *(≈ HG-19 / C-19, D-27)*

- [ ] **4.1** PSU mains switch off; the driver LEDs are dark.
- [ ] **4.2** Photo of the DIP block **before** the change. Expected as found 2026-10-03: SW1…SW8 = off ON ON ON off ON off
  off.
- [ ] **4.3** Set the **target** (sheet 4): **SW1 on · SW2 off · SW3 on · SW4 off · SW5 off · SW6 on · SW7 off · SW8 on**
  (4000 pulses/rev = 800 steps/mm; direction CCW unchanged; start-up assist on, unchanged; motor select 86-80 / 86-118
  **closed loop** — the encoder cable must be connected).
- [ ] **4.4** Photo **after** the change; write the 8 positions into annex B.
- [ ] **4.5** Motor encoder cable connected to the driver "Encoder" terminals (closed loop needs it).

---

## 5. Power-up order and flashing

- [ ] **5.1** 48 V PSU **stays OFF** for sections 5–7.
- [ ] **5.2** E-stop released; START / END switches not actuated; nothing in the travel.
- [ ] **5.3** Plug the Nucleo into the PC by USB. Expected: the Nucleo power LED is on, the ST-LINK COM LED (LD1) lights,
  Windows shows the ST-LINK drive and a COM port **STLink Virtual COM Port (COMn)** (Device Manager → Ports). Write
  **COMn** into annex B. If not → **STOP** (FLASHING §6).
- [ ] **5.4** Check the file hash and flash **`BBS_FW_0.8.6_release.bin` by drag-and-drop** — method **A** of
  [FLASHING](firmware/FLASHING.md) (first bring-up; the saved configuration is wiped on purpose — the board still
  holds Stefan's old program).
- [ ] **5.5** After flashing: no `FAIL.TXT`; the green LED **LD2** shows **slow blink** (1 s on / 1 s off: running, no latch —
  also with one limit switch open) or **double blink** (a latch: HALT / PAUSED / ESTOP / a FAULT, e.g. the E-stop
  pressed or `LIMIT_WIRING` with both limit connectors open). **Triple blink** = fallback clock → **STOP** (solder
  bridges SB16 / SB50). LD2 dark or steady → **STOP**.
- Later updates of this board: method **B2** (CubeProgrammer + `.hex`, no full chip erase) — it keeps the saved
  configuration (FLASHING §1).

---

## 6. PC software and first connection

- [ ] **6.1** Unzip `pc_software\BirdBendStand-<version>.zip` to a local folder (e.g. `C:\BirdBendStand`), start
  **`BirdBendStand.exe`** (USER_MANUAL §2). The application starts disconnected and never opens a port by itself.
- [ ] **6.2** **Connection & Config** tab → **Endpoint**: choose the entry of the Nucleo, shown as `COMn` plus the Windows
  description, e.g. `COM7 STMicroelectronics STLink Virtual COM Port (COM7)` (ST-LINK ports are listed first;
  **Refresh** if missing) → **Connect**. Baud rate is fixed (921600).
- [ ] **6.3** The **Device** line reads *(≈ HG gate S-00 identity check)*:
  `Device: FW 0.8.6 (build 20261010-2135)  proto 1.0  payload 1  dict hash 0xF8BCDCB8 ✓  UID …  features AFE BUTTONS DRV_SIGNALS HOMING MOTION MOVE_UNTIL_LOAD NVM`
  (the build string is the time of the source commit, not today — FLASHING §5). Any other FW version, build, proto,
  payload or dict hash → **STOP**. Write the 24-digit **UID** into annex B.
- [ ] **6.4** `Version check: ✓ compatible`. A ✗ / the **RO** chip → **STOP** (wrong PC software or firmware).
- [ ] **6.5** **Link** line: about **80 Hz** samples, no losses growing. **LINK** chip green `CONNECTED`.
- [ ] **6.6** Notice `Board runs on default parameters (no valid NVM record) – check and Save to NVM.` and **CFG** amber
  `dirty NVM defaulted` (or `NVM defaulted`) — **expected** after the first flash (configuration saved in section 12).
- [ ] **6.7** Board configuration table (tick **show advanced**) *(≈ HG-32 / C-26)*: `drv.pwr_sense_enable` = **0 / off**,
  `drv.k1_check_enable` = **0 / off**, `motion.steps_per_mm` = **800**, `afe.rate_sps` = **80 SPS**,
  `drv.alm_active_level` = **HIGH_ACTIVE**. Expected: all five values as listed (the Bring-up assistant checks all
  five). Different → **STOP**.
- [ ] **6.8** Indicator chips with the **48 V OFF**, E-stop released, switches closed (USER_MANUAL Appendix A):

| Chip | Expected now | If different |
|---|---|---|
| **LINK** | green `CONNECTED` | USB / COM port |
| **ESTOP**, **HALT**, **PAUSED**, **FAULT**, **LOAD**, **WDG** | green (no latch) | ESTOP red at start: E-stop pressed or its NC wire open → check 3.2-3 |
| **LIM S**, **LIM E** | green | red = switch open, connector unplugged or wire broken (fail-safe); both red = `LIMIT_WIRING` |
| **AFE** | green, ≈ `80.0` | red `AFE_STALE` / `NO_AFE_DATA`: HX711 supply / DOUT / SCK; amber `AFE_RATE_MISMATCH`: RATE wire PB5 |
| **DRV** | `on` (presence sense not fitted, D-41) | – |
| **K1** | not shown (check disabled, D-41; grey "?" only until the board values are read) | – |
| **HOMED** | red `NOT homed` | expected after every start |
| **ENA** | red `disabled` | expected: the board refuses motion until **Driver enabled** (section 8) |
| **MOV** | `standing` | – |
| **ALM** | red `ALM – new motion blocked` | **expected with the driver unpowered** (ALM output open = alarm / unpowered); must go green with the 48 V on (section 8) |
| **PEND** | not meaningful while the 48 V is off | – |
| **BTN** | `released` | amber `PAUSE pressed` while not pressed: PAUSE wiring |
| **THR** | amber `DEFAULT_ONLY` | expected without calibration / tare |
| **CAL**, **TARE** | grey `none` | expected before section 11 |
| **VALID** | `0` | – |
| **REC** | `off` | – |
| **KEY** | green `REGISTERED` | red `UNAVAILABLE`: the Pause/Break key works only while the application is in front — test with **Tools ▸ Test Pause/Break key…** and note it |
| **CLK** | green | amber = fallback clock: SB16 / SB50 bridges (8 MHz from the ST-LINK) → **STOP**, report |
| **CFG** | amber `dirty NVM defaulted` / `NVM defaulted` | expected now |

- [ ] **6.9** **Readouts** dock → **Raw**: pull lightly on the load cell by hand (tension) → Raw **increases**; push → it
  decreases. Note the sign in annex B (a reversed sign is allowed — the calibration carries K < 0, SW-CAL-007 — but
  write it down).

---

## 7. Input checks — 48 V still OFF *(≈ HG-17 partly, HG-32)*

- [ ] **7.1** **E-stop:** press the red button. Expected: **ESTOP** red `ESTOP`, red stop banner (E-stop latched), LD2
  double-blinks. Keep it pressed > 1 s: only `ESTOP` is latched — no `K1_WELDED`, no other fault *(≈ HG-32)*.
  Optional DMM *(M-6)*: PA10 pin (CN10-33 / D2) ≥ 2.3 V pressed, ≤ 0.4 V released.
- [ ] **7.2** Release the button: **ESTOP stays red** (latched). Wait ≥ 100 ms → **Clear stop…** → tick
  **E-stop button released, area safe** → **Clear E-STOP** → ESTOP green. (Clearing never moves anything.)
- [ ] **7.3** **PAUSE:** press and release once → **PAUSED** amber `PAUSED BUTTON`, banner `PAUSED (BUTTON) – motion blocked.`
  with **Resume**; **BTN** amber `PAUSE pressed` while the button is held. Clear with **▶ Resume** (toolbar), the
  banner's **Resume**, or by pressing PAUSE again → PAUSED green.
- [ ] **7.4** **START switch:** press the lever by hand → **LIM S** red `LIMIT_START`; release → green again after ≈ 20 ms.
- [ ] **7.5** **END switch:** same → **LIM E** red `LIMIT_END`; release → green.
- [ ] **7.6** **Wire-break test** *(≈ HG-17)*: unplug the START connector → **LIM S** red `LIMIT_START` (LD2 keeps the slow
  blink); plug back → green. Same for END.
- [ ] **7.7** Both connectors unplugged at once → **LIM S** red `LIMIT_START LIMIT_WIRING`, **LIM E** red
  `LIMIT_END LIMIT_WIRING`, **FAULT** red (LD2 double blink). Plug both back → **Clear stop…** → **Clear faults** →
  all green.
- [ ] **7.8** **ALM** red `ALM – new motion blocked` (driver unpowered) — note it, expected.
- If any input does not react as written → **STOP** (wiring of that input, sheet 2 / sheet 3).

---

## 8. 48 V on: driver power, ENABLE / DISABLE, the D-42 ENA-cut check

Preconditions: sections 3–7 ticked; two persons in place; nothing in the travel; **Driver enabled** (Manual tab)
unticked; E-stop released.

- [ ] **8.1** Press the black **RESET** button (B2) of the Nucleo once (the E-stop test of section 7 left ENA at *disabled*;
  after a restart the board leaves the driver holding again, D-13). The amber banner `LINK LOST – reconnecting…`
  appears for a moment; the PC reconnects; ESTOP green.
- [ ] **8.2** Observer switches the **48 V PSU on**. Expected: driver power LED on, no alarm LED; the motor **holds**
  (energised — the board leaves the driver holding after boot, D-13, although the **ENA** chip says `disabled`).
- [ ] **8.3** Within ≈ 1 s **ALM** turns green `OFF`; **PEND** `in position`. ALM stays red → **STOP**: driver alarm or the
  PFDE clone uses the other ALM polarity (`drv.alm_active_level`, decided at HG-16 by the maintainer). Record what
  the driver LEDs show. *(≈ HG-16)*
- [ ] **8.4** **E-stop with the board running:** observer presses the red button → ESTOP latched; the operator (hands off
  the mouse) turns the coupling / screw gently by hand: it turns **freely** (de-energised). **Hands off the shaft**,
  then the observer releases the button → still free (check again, hands off afterwards). **Clear stop…** →
  **Clear E-STOP** → still free.
- [ ] **8.5** **ENABLE** *(≈ HG-07)*: hands off the shaft → **Manual** tab → tick **Driver enabled** → **ENA** amber
  `ENABLING … ms`, then green `enabled` (≈ 0.5 s) → the motor **holds** again.
- [ ] **8.6** **DISABLE = free — the MCU ENA path on its own** *(≈ HG-07, C-07)*: E-stop released; untick **Driver enabled**
  → dialog **Disable the driver**: tick **Specimen is unloaded** → **Disable** (Enter does not confirm). **ENA** red
  `disabled`. The operator turns the coupling: it must turn **freely**. **Holds → STOP**: the MCU does not drive ENA
  to *disabled* — check the ENA drive / D1 / I_LED per sheet 3 before any motion. Hands off → tick **Driver enabled**
  again → `ENABLING … ms` → `enabled` → holds.
- [ ] **8.7** **D-42 hardwired ENA cut, MCU in reset** *(≈ HG-10 c / C-25 — the periodic check of D-54 e)*. Roles: the
  **observer** holds RESET and presses the E-stop, the **operator** turns the shaft.
  1. observer **holds the black RESET button (B2)** of the Nucleo pressed (the MCU is in reset; the PC shows the
     amber banner `LINK LOST – reconnecting…` — normal);
  2. observer presses the **red E-stop** → the operator turns the shaft: it must turn **freely**. **If it holds →
     STOP: the hardwired cut does not work — no motion until it is fixed** (§6.8 P-2 logic);
  3. **operator: hands off the shaft**; observer releases the E-stop while still holding RESET → the shaft **holds
     again** (the 10 kΩ pull-down lets the driver enable, no pulses are possible — expected, D-45 a);
  4. observer releases RESET → the board restarts: **ENA** `disabled` (the motor keeps holding, D-13), **HOMED**
     `NOT homed`, link back.
- [ ] **8.8** Optional DMM on the ENA loop *(≈ HG-10 d; sheet 3; clip leads, a third hand needed for RESET)*:
  M-1 across R_E ≈ 0.7 V (MCU in reset + E-stop pressed) · M-4 ENA+ ≈ 4.0…4.6 V pressed, ≈ 0 V released + enabled ·
  M-3 PA4 (CN8-3 / A2) never above 3.4 V · M-5 +5V_CUT 4.75…5.25 V. Values into annex B.
- [ ] **8.9** **Driver alarm reset** (only if an ALM appears, USER_MANUAL §17, D-54 d): E-stop → 48 V off, wait until the
  driver LEDs are dark → 48 V on → release E-stop → **Clear E-STOP** → **Driver enabled** → **HOME**.

---

## 9. First motion — slow, no specimen *(≈ HG-28, HG-11 real-actuation part)*

Preconditions: section 8 complete, the D-42 check and the DISABLE-free check passed, two persons, nothing in the
travel, table near the middle (section 3.3). **Motion parameters → speed = 5 mm/s** for every **Go to** in this guide
(never above 10 mm/s). **If the table does not stop where this section expects it:** the observer presses the **red
E-stop at once** (independent D-42 path); the operator releases the jog button and presses **STOP** → **STOP**,
report.

- [ ] **9.1** **No-specimen mode** (USER_MANUAL §5; without calibration all motion is refused): **Safety limits** tab →
  **Enter no-specimen mode…** → tick **No specimen is mounted** → **Switch PC load limits off**. Expected: amber banner
  `NO-SPECIMEN MODE – PC load limits OFF for this session …`, **NOSPEC** chip. The board load limit stays active.
- [ ] **9.2** **Manual** tab: **Driver enabled** ticked; **jog speed** = **1** mm/s.
- [ ] **9.3** **Direction:** note **Travel (machine)**; zero the caliper between table and frame. Hold **hold forward (+) ▶▶**
  for ≈ 3 s, release. Expected: the table moved **away from the START switch** and stopped on release.
  Toward START → **STOP**: record; with the PO's agreement set `motion.dir_invert` (Connection & Config, **Edit** →
  **Write & verify**) and repeat; it is saved in section 12.
- [ ] **9.4** **Scale:** caliper distance = change of **Travel (machine)** within ± 10 %. About **5 ×** the shown change →
  the DIP switches SW1–SW4 are still at 800 pulses/rev → **STOP**, 48 V off, section 4. Record both values.
- [ ] **9.5** **START switch stop, before the first HOME** (un-homed; the PC travel limits do not act on un-homed jogs):
  hold **◀◀ rear (−) hold** at 1 mm/s until the table reaches the START switch (this may take a few minutes; release
  and press again as needed). Expected: the jog itself is stopped by the switch (event `STOPPED` `LIMIT_START` — pressing the lever by hand does
  not count), **LIM S** red `LIMIT_START`; a further (−) jog is refused;
  **hold forward (+) ▶▶** moves away and LIM S clears (green).
- [ ] **9.6** **HOME:** press **HOME**; the application asks first (unknown load) → tick **I accept homing under load** →
  **Home**. Expected: the table drives to the **START** switch at low speed, backs off, **HOMED** green `homed`,
  Axis box `● HOMED`. Moves away from START, or `HOME_WIRING` / `HOME_NOT_FOUND` / **LIM E** → E-stop / **STOP** as
  above, report.
- [ ] **9.7** **Homed move check:** zero the caliper, **+10** (Steps) → the caliper shows 10.00 mm ± 0.5 mm; **Go to**
  absolute `20` mm → **Go**.
- [ ] **9.8** **END switch stop at low speed** (real actuation): **Go to** absolute `280` mm (5 mm/s). Untick **Driver
  enabled** (dialog **Disable the driver**: tick **Specimen is unloaded** → **Disable**) and — hands off — tick it again
  (`ENABLING … ms` → `enabled`; the axis is now un-homed, so the 290 mm soft limit no longer stops the jog; the PC
  travel limits do not act un-homed either). Jog **hold forward (+) ▶▶** at 1 mm/s until the table reaches the END
  switch. Expected: the jog itself is stopped by the switch (event `STOPPED` `LIMIT_END`), **LIM E** red `LIMIT_END`;
  a further (+) jog is refused; **◀◀ rear (−) hold**
  moves away and LIM E clears.
- [ ] **9.9** **HOME** again (as above). Keep the no-specimen mode on for section 10.

---

## 10. Direct-drive qualification (SYS-011, D-56 b) — before any calibration or test campaign

No buffer board is fitted (D-56 b): the driver inputs stay on the direct 3.3 V drive, which is below the driver's
5–24 V rating. Before calibration and before every test campaign it is **qualified** here (wiring.md §2.2), with the
**release image only**. The SN74ACT244 buffer board is needed **only if 10.1 or 10.2 fails**. Section 10.2 is repeated
**after any wiring change and at the end of every test campaign**. Known limitation: a pulse the driver never saw
during a test is found only by the end-of-campaign re-home (10.2).

### 10.1 Opto current I_LED on ENA and DIR *(≈ C-06 / HG-06 ENA and DIR part, M-2 / M-8)*
- [ ] **10.1.1** Everything off (PSU off, USB unplugged): insert a **100 Ω 1 %** shunt in series with **ENA−** (between the
  driver ENA− terminal and logic GND). Label it. (The DIR shunt comes in 10.1.3 — never both in the loop while the
  motor is powered: the ENA shunt weakens the D-42 cut.)
- [ ] **10.1.2** USB in, **Connect**; the **48 V stays OFF** for the ENA reading (USB only).
- [ ] **10.1.3** **ENA (48 V off):** press the red E-stop and release it → **Clear stop…** → **Clear E-STOP**. ENA now stays at
  the *disabled* level driven by the MCU alone (SAF-FW-006; after a plain boot it would be at the holding level and
  read 0 — a false FAIL). DMM across the ENA shunt **≥ 0.60 V** (= I_LED ≥ 6 mA; 0.45…0.60 V → repeat in 10.1.5); write it into annex B. Then
  everything off (USB unplugged), **remove the ENA shunt** (ENA− back to logic GND), insert the 100 Ω shunt in series
  with **DIR−**, label it.
- [ ] **10.1.4** **DIR (48 V on):** USB in, **Connect**, observer switches the 48 V on, re-enter the **no-specimen mode** (as
  9.1 — the power cycle ended it), **Driver enabled** (hands off), table
  **mid-stroke, ≥ 20 mm from both switches** (a wrong DIR reading must never drive the table into a switch). The
  board keeps DIR at the level of the **last move direction** (high = LED current after a (+) move with
  `motion.dir_invert` = 0; after a (−) move if it is 1). Jog **hold forward (+) ▶▶** at 1 mm/s for ≈ 1 s
  (`dir_invert` = 1: **◀◀ rear (−) hold**), release: DMM across the DIR shunt **≥ 0.60 V** (0.45…0.60 V → repeat in 10.1.5). A short jog the other way
  brings it to ≈ 0 V (proves the shunt is in the DIR loop).
- [ ] **10.1.5** Write the DIR reading into annex B. **Repeat decision (always answer):** all readings ≥ 0.60 V → no
  repeat; a reading of **0.45…0.60 V** → the 100 Ω shunt itself lowers the current by about a quarter — repeat that
  input with the DMM **mA range in series** (burden ≤ 10 Ω) or a **10 Ω** shunt (≥ 60 mV); that reading decides and
  must be **≥ 6 mA** (else STOP). The assistant refuses "no repeat" while a 10.1.3 / 10.1.4 reading is below 0.60 V
  or missing, and then asks for the mA values.
- [ ] **10.1.6** Everything off, remove the DIR shunt, restore DIR− to logic GND (photo of ENA− and DIR− restored).
- Wiring changes (shunts) only with the PSU off and the USB unplugged.
- **< 6 mA on ENA or DIR → STOP:** the buffer board is required (D-56 b) — report to the PO. PUL cannot be held at a
  static level with the release image; its path is qualified by 10.2.

### 10.2 Pulse-integrity check *(≈ HG-09 / HG-25 related; FW-HOM-003 / FW-HOM-004)*
Numbers (SYS-011, SRS v0.6.8): stroke **5 mm ↔ 285 mm** (280 mm inside the 0.5 / 290 mm soft limits), **N = 10 round
trips** (20 moves, 5.6 m, ≈ 4.5 million steps at 800 steps/mm), at **30 mm/s** (fixed; = the default
`motion.v_max_travel_um_s`, 24 kHz; the check never runs faster). Baseline: **3 re-homes of the homed axis** (d0₁…d0₃). Pass: no `HOME_DRIFT` and
**|d1| ≤ max(30 µm, 2 · max|d0ᵢ|)** (d is the difference of two switch captures, so the switch's own scatter sets the
limit; the `HOME_DRIFT` latch at 200 µm, `home.drift_tol_um`, is the hard limit); one repeat allowed. This is the only
step of this guide faster than 10 mm/s: **no specimen, nothing in the travel, the observer's hand at the red E-stop**.
Run it only after **9.5** and **9.8** (real START / END stops) passed **the same day**.

- [ ] **10.2.1** USB / 48 V on; re-enter the **no-specimen mode** (as 9.1), **Driver enabled**, **HOME** (as 9.6) — the 10.1
  power cycles left the axis un-homed and ended the mode; **View ▸ Event log** open.
- [ ] **10.2.2** **Baseline:** the axis is homed (**HOMED** green). Press **HOME** three more times — each runs on a homed
  axis; in the event log each line `HOMED` shows `values d / …` (d = deviation in µm, no values shown = 0): write
  **d0₁, d0₂, d0₃** into annex B and compute the limit **max(30 µm, 2 · max|d0ᵢ|)**. max|d0ᵢ| > 100 µm → **STOP**:
  check the START switch mounting (switch repeatability, not a drive fault; the limit would reach the 200 µm
  `HOME_DRIFT` latch).
- [ ] **10.2.3** **Moves** (in the assistant: run by 10.2.2, this step shows its verdict; paper procedure: USER_MANUAL §15): **Sequence** tab → **New**; **Settings**: **travel ref** = machine; **+ Step ▾**
  **travel**: **Target** `285`, **v mm/s** `30`; **+ Step ▾** **travel**: **Target** `5`, **v mm/s** `30`; select both
  rows → **Loop…** count `10` → **▶ Start** (confirm the travel-only start in the no-specimen mode). Expected: 20 moves,
  sequence `FINISHED`, no ALM, no stop. (Alternative: **Manual** tab, **Motion parameters speed** `30`, **Go to**
  absolute `285` / `5` twenty times.) A refusal **`E_RANGE` "speed above cap" / "loaded"** → **STOP**: write down the
  **Raw** readout and report; never raise a speed cap (before a tare `safety.zero_raw` = 0 and the 2 % FS band
  decides "loaded").
- [ ] **10.2.4** **Re-home:** check that **HOMED** is green **immediately before** pressing **HOME** (otherwise the HOME is a
  first homing and d = 0 means nothing). Any E-stop, Disable, ALM, `LINK LOST` or fault during 10.2.3 → repeat from
  10.2.2. **HOME** → event `HOMED` `values d1 / …`. **Pass: no `HOME_DRIFT` (FAULT chip green) and |d1| ≤ max(30 µm, 2 · max|d0ᵢ|).**
  Write d1 into annex B.
- [ ] **10.2.5** Fail (|d1| above the limit, or `HOME_DRIFT` latched — **Clear faults** clears it) → repeat 10.2 once from
  10.2.2; fails again → **STOP:** the buffer board is required (D-56 b) — report to the PO.
- [ ] **10.2.6** Repeat this check after any wiring change and at the end of every test campaign (SYS-011).
- [ ] **10.2.7** **Leave no-specimen mode** (banner or Safety limits tab).

---

## 11. Calibration (travel, load, tare)

Precondition: section 10 passed.

### 11.1 Travel calibration *(prerequisite of HG-25; ≈ HG-19 "first travel calibration vs expected")*
- [ ] **11.1.1** Follow USER_MANUAL §8: **Calibration & Tare** → **Start travel calibration wizard…** → **Start** → dialog
  **Start calibration**: tick **No specimen is mounted** → **Start**. Expected: banner
  `NO SPECIMEN – travel calibration: PC load limits OFF for the wizard's own moves …`, **NOSPEC** chip
  `NO-SPECIMEN (wizard)`; if the axis is not homed, the step strip shows **HOME** first and the wizard homes by
  itself (D-54 a).
- [ ] **11.1.2** **Move +2 mm ▶** → zero the caliper → **Move 10 mm ▶** → **Measured distance D1** → **Set trial steps/mm ▶** →
  **Move 50 mm ▶** → **Total distance D_tot** (≈ 60 mm) → **Compute ▶** → **Accept ▶** (saved to the board NVM and the
  calibration file).
- [ ] **11.1.3** Expected ≈ **800 steps/mm** (± a few %). A value near 160 → DIP SW1–SW4 → **STOP**. Write `spm0 → spm1 →
  spm2` into annex B. Re-home afterwards.

### 11.2 Load calibration with 1 kg + 10 kg (USER_MANUAL §9) — hands in the load path only while PAUSED
- [ ] **11.2.1** **Before starting the wizard:** operator — hands off mouse and keyboard except for the wizard buttons; the
  **observer presses PAUSE once** → **PAUSED** amber `PAUSED BUTTON` (every motion start is blocked, the driver keeps
  holding). **Do not press PAUSE again** during 11.2 / 11.3 (a second press resumes) and do not press it after the
  wizard has started: a pause raised inside the running wizard aborts it (checked on the simulator) — then cancel,
  set PAUSED again and restart the wizard.
- [ ] **11.2.2** **Hang / remove weights only while PAUSED is shown.**
- [ ] **11.2.3** **Calibration & Tare** → **Start load calibration wizard…** (default pre-settle 2 s / capture 10 s) → **Start**.
- [ ] **11.2.4** Zero point: nothing on the cell, empty hook → **Capture zero point ▶** (do not touch the stand).
- [ ] **11.2.5** **1 kg** hanging still → **Capture point 1 ▶**; **10 kg** → **Capture point 2 ▶**. `weight not detected` →
  hang the weight freely and **Repeat**.
- [ ] **11.2.6** Fit page: status **PASS** with the warning `LOW_SPAN` (expected, D-35 G2); the dialog
  **K implausible — accept anyway?** → **STOP** and check the weights / cell / gain. **Accept calibration ▶**. Write K
  and the residuals into annex B.
- [ ] **11.2.7** Remove the weights (still PAUSED).

### 11.3 Tare and a check (USER_MANUAL §10) — still PAUSED
- [ ] **11.3.1** Cell unloaded, axis still ≥ 1 s → **TARE** → **TARE** chip green with the tare age, **THR** `VERIFIED`.
- [ ] **11.3.2** Hang the **10 kg** weight again: **Force** ≈ **98.1 N** (≈ 10.0 kgf with **View ▸ Units ▸ kgf**) within ± 1 %.
  Remove it.
- [ ] **11.3.3** Hands out of the load path → **▶ Resume** (toolbar) → PAUSED green.

---

## 12. Save to NVM, restart, verify *(≈ DM-05 demonstration, HG-27 NVM part)*

- [ ] **12.1** **Connection & Config** → **Save to NVM** → **CFG** green `clean`; the "default parameters" notice is gone.
- [ ] **12.2** **Save to file…** → `board_<UID>_<date>.bbboard.json` (it restores the configuration after an accidental wipe —
  FLASHING §1).
- [ ] **12.3** Restart: untick **Driver enabled** (dialog **Disable the driver**: tick **Specimen is unloaded** → **Disable**),
  48 V **off** (**ALM** turns red again — expected), **Disconnect**, unplug the Nucleo USB for 5 s, plug it in,
  **Connect**.
- [ ] **12.4** Expected: same **Device** line (`FW 0.8.6`, build `20261010-2135`); **no** "default parameters" notice; **CFG**
  `clean`; **Read all** → `motion.steps_per_mm` = the calibrated value, `motion.dir_invert` as set; **HOMED**
  `NOT homed` (normal after a restart); **CAL** amber `PASS LOW_SPAN` (stored on the PC); **TARE** `none` (tare is per
  session — tare again before every test).

---

## 13. Create the bring-up package (the deliverable)

- [ ] **13.1** **Tools ▸ Bring-up assistant** → **Create bring-up package** → save the one zip it builds (step results and
  entries, automatic checks, the PC incident log `%APPDATA%\BirdBendStand\logs\bend_stand.log`, the board
  configuration, the calibration files) and send that zip to the PO. Attach the photos where the assistant asks for
  them.
- [ ] **13.2** Photos (if not attached in the assistant, put them next to the zip): Nucleo both sides (solder bridges),
  interface board both sides, operator panel wiring (NC and NO blocks), driver terminals and DIP block before /
  after, limit switches at both ends, the shunts of 10.1 in place, HX711 module (RATE modification), PSU / F1,
  nameplates of driver, motor and load cell.
- [ ] **13.3** Only if the assistant is not available (older PC software): send instead a folder `bringup_<date>` with annex B
  filled in, the whole `%APPDATA%\BirdBendStand\logs` folder, the `*.bbboard.json` of section 12, the folder
  `%APPDATA%\BirdBendStand\calibration\`, screenshots (Device line, indicator bar after section 8, calibration
  results) and the photos.
- [ ] **13.4** Incident notes (what, when, what was pressed / switched) — in the assistant or on annex B.

---

## 14. Troubleshooting

The full table is USER_MANUAL §17 (in `pc_software\…\docs\USER_MANUAL.html`). Bring-up specific:

| Symptom | Likely cause | What to do |
|---|---|---|
| No COM port / no ST-LINK drive | charge-only USB cable, ST-LINK driver missing | data cable; ST-LINK driver; FLASHING §6 |
| Device line with another build or dict hash | wrong file flashed | re-flash, check the hash (FLASHING §5) |
| **ESTOP** red right after connecting | E-stop pressed or its NC wire / connector open (fail-safe) | 3.2-3; then Clear E-STOP |
| Both **LIM** red, `LIMIT_WIRING` | both limit connectors open | plug them; **Clear faults** |
| **AFE** red `AFE_STALE` | HX711 not supplied (3.3 V) or DOUT / SCK wrong | sheet 2 HX711 rows |
| **AFE** amber `AFE_RATE_MISMATCH` | RATE pin 15 not lifted / not wired to PB5 | wiring.md §4 (D-21) |
| **CLK** amber / LD2 triple blink | Nucleo runs on its internal clock (SB16 / SB50) | report; bridges per sheet 2 |
| **ALM** red with the 48 V on | driver alarm (LED) or the clone's ALM polarity | section 8; maintainer decides `drv.alm_active_level` |
| Motor does not hold with 48 V on, ENA `enabled` | ENA polarity of the clone differs, D1 / D2 reversed, ENA+ short to 5 V | **STOP**; M-4, 3.2-7 |
| Motor holds with **Driver enabled** unticked (E-stop released) | MCU ENA path: PA4, D1, I_LED too low | **STOP**; sheet 3, 10.1 |
| Shaft holds with E-stop pressed and MCU in reset | D-42 NO path broken (R_E, D2, +5V_CUT, NO block) | **STOP — no motion**; 3.2-4, 3.2-6, M-5 |
| Table moves toward START on (+) | `motion.dir_invert` | section 9 |
| Travel ≈ 5 × the shown value | DIP SW1–SW4 at 800 pulses/rev | section 4 |
| I_LED < 6 mA (10.1) or \|d1\| above the limit / `HOME_DRIFT` twice (10.2) | direct 3.3 V drive marginal | buffer board required (D-56 b) — report to the PO |
| Load wizard ends `ABORTED` right after PAUSE | PAUSE pressed while the wizard was running | set PAUSED **before** starting the wizard (11.2) |
| Motion buttons greyed | a gate refuses (not enabled, not homed, no no-specimen mode, PAUSED, latch) | hover the button: the tooltip lists the reason |

---

## Annex A — overlap with the formal hardware-gate items

The gate is run by Validator E with the HIL scripts after the D-06-GATE approval row; these bring-up steps are **not**
HG evidence. "Re-use?" = what Validator E may decide to re-use if nothing changed since the bring-up.

| Bring-up step | Same check as | Re-use? |
|---|---|---|
| 3.1 board / bridges / wiring inspection | HG-01 (C-01), HG-20 (C-20) | photos possibly; inspection repeated |
| 3.2-8 isolation 48 V GND ↔ logic GND | HG-20 M-5 | repeated (part of HG-20) |
| 3.3 switch positions / span | HG-25 (usable travel ≥ 280 mm, part), A-07 | span possibly |
| 4 DIP switches | HG-19 (C-19) | photo / state possibly |
| 6 Device line, UID | HIL session S-00 identity (`--board-uid`) | UID is needed for the D-06-GATE row |
| 6 board configuration defaults | HG-32 (C-26) | repeated by the script |
| 7 E-stop held > 1 s, only ESTOP | HG-32 | repeated |
| 7 wire-break of START / END | HG-17 (C-17, partial) | repeated (HG-17 is done during a jog) |
| 8 ALM with 48 V off / on | HG-16 (C-16, partial) | levels recorded; repeated |
| 8 ENABLE → holding, DISABLE → free | HG-07 (C-07, functional) | timing only at the gate |
| 8 D-42 check with the MCU in reset, M-1…M-5 | HG-10 c / d (C-25) | repeated — **must pass in the gate session before any bypass (§6.8 P-2)** |
| 9 direction, scale, HOME | HG-28 | repeated |
| 9 START / END stop at low speed | HG-11 real-actuation part | repeated (HG-11 adds 100 timed trials) |
| 10.1 I_LED ENA / DIR (release image) | HG-06 / C-06 (ENA, DIR; PUL only at the gate via STATIC_LEVEL); SYS-011 (D-56 b) record | values possibly; PUL at the gate |
| 10.2 pulse integrity | SYS-011 (D-56 b); related to HG-09 (step count) and HG-25 / HG-26 (re-home) | the SYS-011 record itself; HG items repeated |
| 11.1 travel calibration | HG-19 second part; prerequisite of HG-25 | result re-used if the mechanics did not change |
| 11.2 load calibration with real weights | MC3-3 (PO demonstrations, Validator F) | possibly as the demonstration |
| 12 NVM save / restart | DM-05 (Validator F), HG-27 NVM timing | functional part only |
| not in this guide | HG-02…05, 08, 09, 10 a / b / e, 12…15, 18, 21, 22, 24, 26, 29…31 gate only; HG-06 (PUL), HG-23 (N/A unless the buffer is fitted), HG-25, HG-27 only their parts above | – |

---

## Annex B — record sheet (paper fallback of the Bring-up assistant)

| Item | Value |
|---|---|
| PO release for bring-up (name, date) | |
| Persons (operator / observer) | |
| Date, start / end time | |
| Board UID (Device line) / COM port | |
| FW version / build string / dict hash seen | `0.8.6` / `20261010-2135` / `0xF8BCDCB8` ? |
| Solder bridges as required (y/n, photo no.) | |
| 3.2 measurements (3.2-1 … 3.2-13) | |
| 3.3 START margin / END margin to the mechanical stop (mm); switch-to-switch span (mm) | |
| DIP before (SW1…SW8) / after | |
| Raw sign on pull (+ / −) | |
| ALM with 48 V off / on | |
| DISABLE → free (y/n); ENABLE → holds (y/n) | |
| D-42 check: shaft free with MCU in reset + E-stop (y/n) | |
| M-1 / M-3 / M-4 / M-5 (V) | |
| Direction OK (y/n), `motion.dir_invert` set to | |
| Scale: caliper / shown (mm) | |
| START stop un-homed OK (y/n); HOME OK (y/n); END stop OK (y/n) | |
| 10.1 I_LED ENA / DIR (V over 100 Ω → mA; low-burden repeat if done) | |
| 10.2 d0₁ / d0₂ / d0₃ / limit / d1 (µm), HOME_DRIFT (y/n), sequence FINISHED (y/n), repeat needed (y/n) | |
| Travel calibration spm0 → spm1 → spm2 | |
| Load calibration K, status, residuals | |
| 10 kg check force (N) | |
| After restart: CFG clean, values kept (y/n) | |
| Incidents / STOP points | |
