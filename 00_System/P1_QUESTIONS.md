# P1 entry questions for the product owner

> **Answered by the PO on 2026-10-03** → recorded as D-10 (amended) … D-25 in `specs/DECISIONS.md`.
> Follow-ups: HBS86H at 24 V is below the datasheet minimum (30 VDC); ALM/reset usage and buffer board → R5;
> 1 kg + 10 kg weights cover only 5 % of FS (calibration extrapolates 20×); D-14 pause button interpretation.

Consolidated from R1–R4 (source IDs in brackets). Every question has a **default**: if you don't answer,
the SRS uses the default and marks it `ASSUMED (Q-nn)` so it can be changed later without rework of the process.
Answer inline (edit this file) or in chat, e.g. "Q3: pause/resume; Q7: Leadshine, 48 V".

## A. Safety (shape the SRS most)

| # | Question | Default |
|---|---|---|
| Q1 | **E-stop category.** D-10 (stop pulses, keep holding) is a *stop category 2*; IEC 60204-1 allows an emergency stop only as category 0/1 (power removed). Accept: the NC button becomes a **machine stop** (holds load, D-10), plus a separate **hardwired E-stop that cuts driver power**, if a person can reach the crosshead? [Q-R4-01, Q-R2-09, Q-R1-04] | Yes. NC button = machine stop (FW, holding). Recommend a hardwired power-cut E-stop on the driver supply, reported to the FW as an input. |
| Q2 | **Motor can destroy the load cell.** With a 5 mm ball screw the 8.2 N·m motor produces ≈ 9 kN, while the cell's safe overload is 2.35 kN (120 %). [R2 §8] | FW hard load limit checked on **every** sample at ≤ 110 % FS (220 kg) + ADC saturation counts as a trip; SW limits configurable below that. Consider mechanical protection (shear pin / reduced driver current). |
| Q3 | **MCU reset / board power loss under load.** On HBS86H "no ENA current" = drive **enabled**, so the motor keeps holding while the MCU resets. [Q-R4-02, R2 #4] | Keep it: the driver holds through MCU reset; FW boots with pulses blocked and axis "not homed". |
| Q4 | **Pause/Break key**: full stop (abort sequence) or pause with resume? [Q-R3-05, Q-R4-03] | Software stop: pulses stop (holding), sequence **aborted**, explicit clear needed. |
| Q5 | **PC link lost while moving.** [Q-R4-11] | FW link watchdog 1 s → controlled stop, holding, validity flag cleared. |
| Q6 | **Auto-disable** the driver when idle? [Q-R4-12] | After 600 s idle, only if load < 2 % FS; otherwise stays holding. |
| Q7 | **Max load allowed on the specimen during homing.** [Q-R4-04] | < 5 % FS, otherwise operator confirmation. |

## B. Hardware facts (placeholders are used until known)

| # | Question | Default / placeholder |
|---|---|---|
| Q8 | **Driver**: genuine Leadshine HBS86H or a JMC 2HSS86H clone? Supply voltage, DIP settings (pulses/rev), how alarm reset works. Photo of label + DIP switches helps. [Q-R2-01..03] | Conservative timing for both: pulse high/low ≥ 10 µs, ≤ 50 kHz, DIR setup 20 µs, all as parameters; 4000 pulses/rev. |
| Q9 | **Interface board OK?** 3.3 V MCU pins can't drive the opto inputs reliably → small board with a 5 V buffer (74AHCT125), common-cathode wiring, pull-downs, RC filters for switches. [Q-R2-11, Q-R1-01] | Yes, documented in `01_HW/` by Implementer A. |
| Q10 | **ALM / PEND** outputs of the driver wired to the MCU? [Q-R1-02] | ALM yes (fault → stop + latch + "not homed"); PEND optional (settling detection). |
| Q11 | **Limit switches**: mechanical NC or inductive 12–24 V (NPN/PNP)? Is START the home end? [Q-R1-03, Q-R2-10] | Mechanical NC to GND; START = home end. |
| Q12 | **Mechanics**: screw type & lead, gear/belt ratio, total travel, needed max speed and max force; can the load back-drive the axis? [Q-R1-05, Q-R2-05, Q-R2-08] | Placeholders: 5 mm ball screw direct (800 steps/mm at 4000 p/rev), 400 mm travel, 20 mm/s, 100 mm/s², max force = cell FS. |
| Q13 | **Load cell**: DEF (3.0 mV/V) or DEE (2.0 mV/V)? Tension only or tension + compression? [Q-R2-06] | DEF 3.0 mV/V, both directions; pull = positive. |
| Q14 | **HX711 board**: which module, supply voltage, is RATE strapped to 10 or 80 SPS, may we wire RATE to a GPIO (PB4)? [Q-R1-08, Q-R2-07] | Wire RATE to PB4 (FW selects 10/80 SPS); FW always measures the real sample period and flags a mismatch. 3.3 V logic. |
| Q15 | **Nucleo**: same board as Stefan? Solder bridges changed? How powered; motor supply vs PC ground? [Q-R1-07, Q-R2-12] | Stock board, USB powered, use ST-LINK 8 MHz clock (HSE bypass) for accurate time/speed. |
| Q16 | **Calibration weights** available (masses)? [Q-R4-07] | 10 kg and 20 kg; warn when test loads exceed 3× the largest weight. |

## C. Functions & protocol

| # | Question | Default |
|---|---|---|
| Q17 | **Data frame extras** beyond your field list: frame sequence counter, MOVING/LIMIT/ESTOP/fault flags in the flag byte, actual position? One frame per HX711 sample (80 Hz)? [Q-R3-01, Q-R3-02, R4 §9] | Yes: your fields + u16 sequence counter + status flags; one frame per HX711 sample; 32-bit µs time; ≈ 22 B/frame, 2 % of link. |
| Q18 | **Manual-control slider**: sets the target position (move on release) or the jog speed? [Q-R1-06] | Target position within soft limits, sent on release; separate hold-to-jog buttons. |
| Q19 | **Load-target sequence steps** (move until load X) needed in M4? [Q-R3-06, Q-R4-10] | Yes, as "approach + trim" (position frozen during capture); no continuous force control. |
| Q20 | **Calibration**: separate push calibration? Linearity limits? Travel-cal instrument? [Q-R4-05, -06, -09] | No separate push cal; linearity PASS ≤ 0.1 %, WARN ≤ 0.5 % of span (max residual); caliper, step 2 uses total 60 mm. |
| Q21 | **Test report format** and content; 3-point-bend stress/strain? [Q-R4-14] | Raw CSV + JSON metadata (marks, calibration, limits, sequence) + HTML summary per test; stress/strain optional, off. |

## D. Remaining

| # | Question | Default |
|---|---|---|
| Q22 | **Motor label**: exact type and encoder line count. [Q-R2-04] | 86HS2140-class, 8.2 N·m, 1000-line encoder (4000 counts/rev). |
| Q23 | **Did Stefan's stand ever run on hardware?** If yes, with which step/dir/enable polarity and pulse settings? [Q-R1-09] | No; nothing is taken over from it as proven. |
| Q24 | **Git commits**: commit the skeleton + R1–R4 now, or only at the P1 gate? | Commit at each gate (as in Thrust_Stand). |
