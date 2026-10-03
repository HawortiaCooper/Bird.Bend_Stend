# Decision log

Binding for all roles once **accepted**. Proposed decisions are listed for the product owner (PO).

| ID | Date | Decision | Status |
|---|---|---|---|
| D-01 | 2026-10-03 | Process, roles and layout mirror Thrust_Stand_HAW (`00_System/PROCESS.md`). | accepted (PO request) |
| D-02 | 2026-10-03 | `E:\Bavovna\Drone\Thrust_Stand_HAW` and `E:\Bavovna\Drone\Stefan` are read-only references. Reused code is copied into this repo with an origin note (`/* Origin: Thrust_Stand_HAW/02_FW/src/... @9473c68 */`). | accepted (PO request) |
| D-03 | 2026-10-03 | PC link: UART 921600 baud, 8N1, no flow control; every frame in both directions carries CRC-16. | accepted (PO request) |
| D-04 | 2026-10-03 | HX711 default: 80 SPS, channel A gain 128. | accepted (PO request) |
| D-05 | 2026-10-03 | Data frame fields: system time, payload version (1 B), flags (bit0 = data validity), AFE raw 32-bit, setpoint distance, CRC-16; binary with a leading separator. | accepted (PO request) |
| D-06 | 2026-10-03 | No hardware access (opening COM ports, flashing) until the PO confirms which board/port may be used. | accepted (Orchestrator, safety) |
| D-07 | 2026-10-03 | Hardware is available **later**: development and FW⇄SW integration first run against a PC simulator / FW host twin; on-target tests happen at a later gate approved by the PO. | accepted (PO) |
| D-08 | 2026-10-03 | "Start/stop switches" = **two end-of-travel limit switches** (START end, END end); one of them is the home/zero reference. No physical operator start/stop buttons. | accepted (PO) |
| D-09 | 2026-10-03 | FW framework: **PlatformIO + stm32duino** (as Thrust_Stand_HAW), LL/register access for timing-critical peripherals; Stefan is used only for pinout/board-init knowledge. | accepted (PO) |
| D-10 | 2026-10-03 | ~~E-stop under load: stop pulses, keep holding.~~ **Amended by D-11:** "stop pulses immediately, keep the driver enabled (holding)" applies to all **operational stops** (GUI STOP, Pause/Break, limit switch, FW load limit, stale/saturated AFE, link loss) = IEC 60204-1 stop category 2. | amended (PO, Q1) |
| D-11 | 2026-10-03 | **E-stop per IEC 60204-1 (power removal):** NC E-stop removes driver power in hardware, independent of the MCU (stop category 0; R5 to propose circuit). The MCU also reads the E-stop state → stops pulses, drops ENA, latches. Driver power loss = position lost → axis "not homed". Clear = button released + PC command + re-home. | accepted (PO, Q1) |
| D-12 | 2026-10-03 | FW hard load limit checked on **every** HX711 sample; ADC saturation = trip. Default 110 % FS (220 kg); **the PC can set it lower** (parameter), never above 110 % FS. SW limits (travel, load) on top. | accepted (PO, Q2) |
| D-13 | 2026-10-03 | MCU reset / power loss: the driver keeps holding (HBS86H: no ENA current = enabled); FW boots with pulses blocked, axis "not homed". | accepted (PO, Q3) |
| D-14 | 2026-10-03 | **Pause/Break key** = operational stop (holding) + sequence **terminated**, explicit clear. **Additional Pause/Resume**: a physical PAUSE button on an MCU input → controlled stop (holding), sequence paused, resumable (GUI Resume or button). | accepted (PO, Q4) — button interpretation to confirm at P1 review |
| D-15 | 2026-10-03 | Link watchdog 1 s while moving → controlled stop, holding, validity flag cleared. Idle auto-disable after 600 s only if load < 2 % FS. Homing allowed with load < 5 % FS, else operator confirmation. | accepted (PO, Q5–Q7) |
| D-16 | 2026-10-03 | Driver: **genuine Leadshine HBS86H**, supplied from **24 V** (⚠ below the datasheet minimum 30 VDC, R2 §1 — open item). Timing defaults as parameters: PUL high/low ≥ 10 µs, ≤ 50 kHz, DIR setup 20 µs; 4000 pulses/rev. **ALM and PEND are wired**; first release reads and reports them only, no automatic reaction (to be added later). | accepted (PO, Q8, Q10) |
| D-17 | 2026-10-03 | Driver inputs are currently driven **directly from 3.3 V MCU pins** (Stefan wiring, proven running per D-24); a 5 V buffer interface board is optional — R5 prepares a proposal. | accepted (PO, Q9) |
| D-18 | 2026-10-03 | Limit switches: **mechanical NC**, both ends wired. START = home end. | accepted (PO, Q11) |
| D-19 | 2026-10-03 | Mechanics: **SFU1605 ball-screw table** (16 mm, 5 mm lead), **300 mm stroke**; nominal 800 steps/mm at 4000 p/rev (direct drive ASSUMED). Required speed ≤ 10 mm/s; more is welcome. Max force 200 kg = cell FS (more needs a bigger cell). | accepted (PO, Q12) |
| D-20 | 2026-10-03 | Load cell **Keli DEF 3.0 mV/V, tension + compression**, pull positive; one calibration (no separate push calibration). | accepted (PO, Q13, Q20) |
| D-21 | 2026-10-03 | HX711: **RATE wired to an MCU GPIO**, 3.3 V supply, default **80 SPS**; FW measures the real sample period and flags a mismatch. | accepted (PO, Q14) |
| D-22 | 2026-10-03 | Nucleo: Stefan's board; re-wiring allowed. Powered from PC USB or a separate DC-DC; driver from a separate 24 V supply. Calibration weights: **1 kg and 10 kg**. | accepted (PO, Q15, Q16) |
| D-23 | 2026-10-03 | DATA frame: PO fields + u16 frame sequence counter + status flags (VALID, MOVING, LIMIT, ESTOP, fault…); **one frame per HX711 sample**; 32-bit µs timestamp. Manual slider = target position on release + hold-to-jog buttons; ±0.1/1/10 mm become absolute targets (no relative moves on the wire). **Load-target sequence steps are mandatory** (approach + trim). Linearity PASS ≤ 0.1 % / WARN ≤ 0.5 % span; caliper; travel step 2 over total 60 mm. Report: raw CSV + JSON metadata + HTML summary; 3-point-bend stress/strain can be enabled. Motor 86HS2140-class, 1000-line encoder. | accepted (PO, Q17–Q22) |
| D-24 | 2026-10-03 | Stefan's stand **did run and control the driver** (PO): its PUL/DIR wiring and direct 3.3 V drive work in practice; ENA was never driven (consistent with "no current = enabled"). Stefan's ~3 µs pulses worked → our 10 µs default is conservative. | accepted (PO, Q23) |
| D-25 | 2026-10-03 | Git: the Orchestrator commits at each gate (P0 committed 2026-10-03). | accepted (PO, Q24) |
