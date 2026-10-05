# HG1 hardware gate — operator runbook

| Doc | HW_GATE_RUNBOOK |
|---|---|
| Version | 0.2 (2026-10-05, M3 verification) — D-45 (a)–(g), Integrator review R-HIL-01…03 applied, DEF-M3-01 / DEF-HG-01 closed, twin dry run re-run on 2d36eec (ICD v0.7.3 / dict 6). 0.1 — first version, prepared under D-06 |
| Owner | Validator E (00_System/tools/hil, the Integrator reviews) |
| Basis | FW_test_plan v0.4.2 §6 (HG-01…32, §6.7 order, §6.8 bench safety procedure), wiring.md v0.5 (C-xx, M-1…M-9), ICD v0.7.x Appendix C (DIAG_MEAS), D-06, D-35 G5, D-40 c, D-41, D-42, D-44 |
| Tools | `hil_session.py` (runner), `hil_procs.py` (HG procedures), `hil_link.py` (D-06 interlock, link, DIAG_MEAS), `hil_operator.py` (prompts), `hil_budget.py` (§6.1 rule 4), `hil_report.py` (markdown report), `tests/` (unit tests) |

**Nothing in this runbook may be done before the PO approved the gate (D-06) and the bench safety procedure
(§6.8, condition F3).** Until then only the twin dry run (`--twin`) is allowed.

---

## 1. Approval and the D-06 interlock

1. The PO approves (a) the gate session, (b) the bench safety procedure FW_test_plan §6.8 (F3) incl. the deltas of
   §9 below, (c) names the board (UID) and the COM port.
2. The Orchestrator records the reference **`D-06-GATE-YYYYMMDD`** (date of the session, optional `-TAG`) as a
   **dedicated table row** in `00_System/specs/DECISIONS.md` (or `STATUS.md`), one cell = the reference, row marked
   *approved*, e.g. `| D-06-GATE-20261012 | 2026-10-12 | PO approved HW gate: board UID …, COM7, §6.8 bench procedure | approved (PO) |`
   (R-HIL-02: a mention in running text, a longer tag or a row marked pending / revoked does not count).
3. The runner opens a serial port only if: `--approved` matches `D-06-GATE-YYYYMMDD[-TAG]`, is a real date, not in the
   future, ≤ 7 days old, **and is recorded as an approved row** in DECISIONS.md / STATUS.md (whole-token match); `--port` is given
   explicitly; the operator re-types the port name; with `--board-uid` the GET_INFO UID must match (else the session
   stops at S-00). `--twin` and `--port` are mutually exclusive; twin mode never imports pyserial.
4. **Flashing is never done by the tool.** Every image change is an operator step: the session closes the COM port,
   the **PO** flashes, the session re-opens the port and verifies the image (GET_INFO build string / feature bit
   `HW_MEAS`, DIAG_MEAS INFO variant, or NOT_IN_BUILD for the release image).

## 2. What to prepare

### 2.1 Persons
Operator at the PC (runs the session, answers prompts) · observer at the operator panel (red E-stop and the 48 V PSU
mains switch within reach for the whole session) · PO (approval, flashing). Two persons are mandatory for every step
with the driver powered (§6.8 P-1).

### 2.2 Hardware and instruments
| Item | Detail |
|---|---|
| Board | PO-named NUCLEO-F446RE (record the 96-bit UID from GET_INFO), solder bridges per pinout §5 |
| Driver | PFDE HBS86H, **DIP target applied with the driver unpowered** (D-27: SW1…SW8 = on off on off off on off on), 48 V Mean Well SDR-480-48 with a **mains switch** (the only power removal, D-41) |
| Operator panel | red mushroom E-stop: **NC → PA10** sense, **NO → +5V_CUT → R_E 68 Ω → D2 → ENA+** (D-42); PAUSE (NO) → PB6 |
| Limits | START / END mechanical NC switches, connectors pluggable (unplugged for STIM trials) |
| AFE | HX711 at 3.3 V, RATE → PB5, Keli DEF cell |
| Measurement header MH (2 × 8, wiring §6.1) | J-PUL-A PUL node → PC7 · J-PUL-B PUL node → PB7 · J-DIR → PC9 · J-ENA → PC8 · J-EVT (selector) → PC6 · J-AUX (selector) → PA11 — each with a **1 kΩ series resistor at the MCU end**; J-STIM PB8 → **220 Ω** → input connector; wires ≤ 10 cm, GND next to every signal |
| Spare parts | 1 kΩ × 8, 220 Ω × 2, jumper wires, the tag **"E-STOP SENSE BYPASSED — TEST"** |
| DMM | V, mA, Ω, MΩ; two **100 Ω 1 % shunts** with clip leads (I_LED, ENA loop) |
| Caliper | 0.02 mm (u 0.02 mm) |
| Dial indicator | 0.001 mm on a magnetic stand (u 0.001 mm) |
| Spring specimen | k ≈ 10…50 N/mm, safe ≥ 300 N, mountable between table and cell (phase 4); optional 10 kg weight to measure counts/N (`--counts-per-n`, nominal 3285) |
| Buffer board | SN74ACT244 shield (phase 3, SYS-011; mandatory before the first calibration, D-28) |
| Camera | photos of board, bridges, MH, panel, J-STIM tag |

### 2.3 Software
1. Repo at the commit the PO approves; `.venv` (Python 3.14, pyserial).
2. Implementer A builds the three images **from that one commit** and states the result of `check_meas_build.py`
   (TC-SYS-009-02 PASS — byte-identical safety handlers / core):
   `pio run -e nucleo_f446re` (release), `-e nucleo_f446re_meas` (HW_MEAS), `-e nucleo_f446re_meas_dwt` (HW_MEAS_DWT),
   each with a private `PLATFORMIO_BUILD_DIR`; hand the three `firmware.elf` files to the PO.
3. Twin dry run of the same commit, no ERROR item (see §8):
   `set BEND_TWIN_BUILD_DIR=<private dir>` then
   `.venv\Scripts\python 00_System\tools\hil\hil_session.py --twin --out <dir>`
4. Unit tests: `.venv\Scripts\python -m pytest 00_System\tools\hil\tests -q` (39 passed).

### 2.4 Flashing (the PO performs it — D-06)
STM32CubeProgrammer over the Nucleo's ST-LINK (SWD), **no full-chip erase** (the NVM log in sectors 1–2 keeps the
commissioning parameters, e.g. `motion.dir_invert`), verify, reset and run:
`STM32_Programmer_CLI -c port=SWD mode=UR -w firmware.elf -v -rst`
The session prompts "PO: flash …" and waits; the driver PSU may stay on (the MCU reset keeps the driver holding, D-13,
and the FW boots NOT_ENABLED with pulses blocked).

## 3. Starting the session

```
.venv\Scripts\python 00_System\tools\hil\hil_session.py --port COM7 --approved D-06-GATE-20261012 ^
    --board-uid 3400xxxxxxxxxxxxxxxxxxxx --out hil_sessions\20261012_HG1
```
Resume after an interruption: same `--out`, add `--from HG-xx` (results already written stay). `--list` prints the
ordered plan. Answer prompts with the requested number / y / n / text; **`abort` at any prompt** (or Ctrl+C) sends
HALT and ends the session. Report: `<out>\HG_report_target.md` (re-render: `--report <out>`); Validator E writes
`FW_test_report_HG1.md` (plan §5.4) with it as annex.

Optional flags: `--buffer-fitted` (phase 3 criteria 10…13 mA), `--power-sense-fitted` (HG-21 / HG-04 b),
`--counts-per-n N`, `--accepted-open HG-xx:REF` (item accepted open under a §6.6 residual-risk decision; needed to pass
the SYS-009 load gate with an open item).

## 4. Session order and expected values (FW_test_plan §6.7 / §6.8)

Legend: **[A]** automatic (DIAG_MEAS / protocol), **[M]** operator / instrument, **[J]** jumper change.
u = uncertainty added per §6.1 rule 4 (PASS if max + u ≤ budget, FAIL if max − u > budget, else INCONCLUSIVE).

### Phase 0 — start
| Step | What happens | Expected |
|---|---|---|
| S-00 | [M] names, image commit; [A] GET_INFO (dict hash 0xB7B0263F, protocol 1.0 / payload 1, UID = `--board-uid`); [M] DIP applied | as stated; wrong UID → session stops |
| IMG-MEAS | [M] PO flashes `nucleo_f446re_meas`; [A] verify | build `…-MEAS`, FEAT_HW_MEAS = 1, INFO variant MEAS |

### Phase 1 — driver PSU OFF
| Step | What happens | Expected |
|---|---|---|
| HG-01 | [M] board / bridges photo; DMM tap → MCU pin of J-PUL-A/B, J-DIR, J-ENA, J-EVT, J-AUX | 950…1100 Ω each; J-STIM 209…240 Ω |
| HG-19 | [M] DIP SW1…SW8 as seen | `on off on off off on off on` |
| HG-20 | [M] circuit inspection (NC → PA10, NO → +5V_CUT → 68 Ω → D2 → ENA+, D1, no contactor, no MCU element in the D-42 path, label); M-5 | +5V_CUT 4.75…5.25 V; 48 V GND ↔ logic GND ≥ 10 MΩ |
| HG-31 | [M] A's statement: MH pins digital inputs without pull in every build | yes (confirmed again at HG-30) |
| HG-29 | [A] INFO; [J] J-EVT ← PB8, J-AUX ← PB8: STIM 5 × 10 ms → EVT / AUX stamps, probe CNT vs stamps; [A] PSU-off pulse block (spm 5000, 2 mm/s un-homed = 10 kHz): MT-2 = Δpos_steps = Δstamps, PWM period 18 000 ± 1 ticks; [J] RC delay of START / END / PAUSE inputs (J-STIM at the connector, J-AUX at the pin node) → u_th; TC-SYS-009-02 report | probe 180 MHz, stamps 1 MHz, ring 2048, DMA ≤ 1000 ns, stim 10 MHz; intervals = hold + 0…1 ms; AUX width = hold ± 1 µs; counts exactly equal; RC delays recorded (limits ≤ 15 µs) |
| HG-03 | [A] 10 min stream 80 Hz + 20 cmd/s | 0 frame_seq gaps, 0 CRC / length errors (PC and FW), every command answered |
| HG-02 | [A] CLK_FALLBACK; MT-5 clock regression over the HG-03 soak; PSU-off block (spm 2500, 20 mm/s, **10 + 10 µs and 50 kHz set explicitly**, R-HIL-03) PWM input PSC 0 | no fallback; \|clock error\| + 100 ppm ≤ 1000 ppm; period 3600 ± 1 ticks |

**PSU-off pulse block** (D-45 c; HG-29 d, HG-02 c, HG-08 a, HG-18 part A): the session asks to switch the 48 V PSU off,
sets `motion.steps_per_mm` / `motion.v_unhomed_um_s` (and flips `drv.alm_active_level` if the unpowered driver reads
ALM active) and, for the 50 kHz trials, `motion.pulse_high_ns` / `motion.pulse_low_min_ns` 10 000 then
`motion.max_step_rate_hz` 50 000 (R-HIL-03; widths first because of hard rule H3) **in RAM only**, jogs un-homed, then
sends REBOOT without SAVE and verifies that every parameter is back. Pulse trains above the physical speed envelope
therefore never move the axis.

### Phase 2 — driver powered, direct 3.3 V drive, no specimen
| Step | What happens | Expected |
|---|---|---|
| P2-ENTRY | [M] PSU on, observer at the panel | – |
| HG-32 | [A] defaults; [M] hold the E-stop > 1 s | `drv.pwr_sense_enable` = 0, `drv.k1_check_enable` = 0; only ESTOP latched (no K1_WELDED); ENABLE → E_STATE BLOCK = ESTOP only |
| HG-06 | [A] DISABLE, STATIC_LEVEL PUL / DIR high; [M] DMM VOH at the terminal, mV over 100 Ω in series with PUL− / DIR− / ENA− (ENA: NOT_ENABLED = LED on) | bring-up: VOH ≥ 3.0 V, I_LED ≥ 6 mA (else fit the buffer first) |
| HG-10cd | [M] **MCU held in reset** (B2 held / NRST to GND) + E-stop pressed → shaft free; M-1 across R_E; M-3 PA4; M-4 ENA+; release → holding (D-13); MCU running: press → FW ESTOP + ENA disabled; M-2a/c; release → still free; ESTOP_CLEAR + ENABLE → holding | M-1 ≈ 0.7 V (≥ 7 mA); M-3 ≤ 3.4 V always; M-4 4.0…4.6 V pressed; M-2a ≈ 0 mA, M-2c ≤ 13 mA; **must PASS before any bypass (§6.8 P-2)** |
| HG-28 | [A] un-homed jog 1 mm/s × 3 s; [M] direction (+x away from START?), caliper travel; [A] HOME; [M] confirm, [A] HOME with `motion.dir_invert` inverted (runs to the END switch at ≤ 5 mm/s), restore, re-HOME; [M] SAVE if dir_invert changed | direction correct (else dir_invert flipped and repeated); scale ± 10 %; HOMED; inverted → HOME_WIRING / HOME_NOT_FOUND within `home.max_travel_um` |
| HG-07 | [J] J-EVT ← PA3 (RX); [A] DISABLE ([M] shaft free) → probe on the ENABLE frame (PSC 1799) → JOG during settle refused (E_BUSY 2) → [M] holding → first PUL stamp | first PUL/DIR − ENA edge ≥ 500 ms (u ≈ 12 µs) |
| HG-08 | [A] PSU-off blocks, PWM input PSC 1 over ≥ 10⁵ pulses: **a1** dict-6 defaults (12.5 + 12.5 µs, 40 kHz, spm 2000), **a2** explicit 10 + 10 µs at 50 kHz (spm 2500); [J] J-EVT ← DIR; [A] 100 one-step reversals (MOVE_ABS ± 1 step): MT-3 trigger on DIR (PSC 17) + MT-4 stamps | a1: high ≥ 10 µs, low ≥ 10 µs, period ≥ 20 µs (driver minimum, u 11 ns) and = configured ± 1 TIM2 tick; a2: = configured ± 1 TIM2 tick; DIR → next PUL ≥ 20 µs (≈ 20 µs + first ramp period ≈ 5 ms from rest) |
| HG-09 | [A] 100 random MOVE_ABS (10…150 mm, 1…30 mm/s), 10 jogs with 2 reversals (segments from PUL / DIR stamps), 100 random STOP 0 / STOP 1 / HALT; [M] caliper on 5 moves | MT-2 = \|Δpos_steps\| every time (± 1 only with POS_UNCERTAIN); caliper = commanded ± (0.02 mm + 1 step) |
| BENCH-ENTRY | **§6.8 P-1…P-6** (below) | P-5: ESTOP_OPEN 0 idle, 1 during a STIM hold |
| HG-10a | [A] 100 STIM trials: home → 7 mm → jog 30 mm/s 350…650 ms → STIM 20 ms on the E-stop sense; probe TRIGGER PSC 17 (CCR2 = last PUL, CCR3 = ENA) | last PUL ≤ 100 µs, ENA ≤ 1 ms (u 0.1 µs + u_th E-stop ≤ 1 µs); ESTOP latched, ENA disabled, HOMED cleared; MT-2 = Δpos (± 1 with POS_UNCERTAIN) |
| BENCH-EXIT | **§6.8 P-8** — must PASS before further motion | press → ESTOP_SET, ENA disabled, ESTOP_OPEN 1; release + ESTOP_CLEAR → ESTOP_OPEN 0; soft limits restored |
| HG-10b | [J] J-EVT ← PA10 node; [M] 10 real presses during a 30 mm/s jog ("PRESS NOW"); 2 slow presses (NO before / after NC) | as HG-10a; slow press: only ESTOP, no fault, release never moves, MOVE_ABS refused NOT_HOMED until HOME |
| HG-11 | [M][J] per switch: unplug, J-STIM → connector, J-EVT ← pin node; [A] 100 STIM trials jogging toward it at 30 mm/s (PSC 1); [M] reconnect; [A] 10 real actuations at 1 mm/s (soft limit moved beyond the switch for this part only) | last PUL ≤ 200 µs (u 11 ns + u_th ≤ 15 µs); latch refuses motion toward the switch |
| HG-13 | [J] J-EVT ← PA3; [A] 100 STOP 0 + 100 HALT during a 30 mm/s jog after 5…100 ms line silence (probe TRIGGER on the first start bit, PSC 17) | last byte end (frame length at 918 367 Bd) → last PUL ≤ 2 ms (u 1.2 µs); Pause/Break key part = M3 |
| HG-17 | [M] unplug the E-stop sense / START / END connector during a slow jog ("NOW"), reconnect; D-42 NO wire broken + MCU in reset + pressed → shaft still holding | STOPPED(ESTOP / LIMIT_START / LIMIT_END) + flag; NO-wire break recorded as undetectable (R-10) |
| HG-04 | [J] J-EVT ← PA10 node (sense connected), J-AUX ← PA2 (TX); [A] SAVEs until the 32-slot log erases a sector, then [M] "GO — press NOW" on the predicted erase SAVEs (every 32nd) until 3 presses landed inside an erase; REBOOT | no IWDG reset; SAVE ≤ 2.5 s; ENA disabled ≤ 1 ms inside the erase; no TX burst shorter than one frame; record valid after reboot |
| HG-14 | [A] HANG MAIN / TICK / ISR1 × 10 while jogging 10 mm/s, .noinit read after the IWDG reset | reset cause IWDG; last PUL − hang start ≤ 100 ms; heartbeat end − hang start ≤ 90 ms (u 0.1 ms) |
| HG-16 | [M] PSU off → [A] ALM active, JOG refused (E_STATE DRIVER_ALARM); [M] DMM at PA8 / PA9; PSU on → ALM inactive; [A] PEND moving / in position | one ALM_CHANGED per change; levels recorded, `drv.*_active_level` decided |
| HG-24 | [M] provoke ALM (bench), reset by PSU off / on; ENA toggle | power-cycle reset works; ENA-toggle result recorded |
| IMG-DWT | [M] PO flashes `nucleo_f446re_meas_dwt`; [A] verify | build `…-DWT`, variant MEAS \| DWT |
| HG-05 | [J] J-EVT ← PB4 (DOUT), J-AUX ← PB10 (PD_SCK); [A] 10 000 reads at 80 SPS: DOUT stamps vs DATA t_us, rate, reinit; DWT 19 / 20; SCK pulses per read per gain | latency ≤ 5 µs (max ≤ 4 µs PASS, u 1 µs); rate ± 1 %; reinit 0; SCK-high ≤ 50 µs, read ≤ 60 µs; 25 / 27 / 26 pulses |
| HG-18 | [A] DWT reset; part A PSU-off 50 kHz + 80 Hz + 20 cmd/s (10 s); part B [M] 5 E-stop presses moving + 3 idle, 3 PAUSE presses, START / END real actuation; read sections 0…22 | **F2:** step ISR (1) ≤ 2 µs, E-stop handler (2) ≤ 1 µs; limits (3, 4) ≤ 1 µs; PRIMASK / BASEPRI-0x20 windows (11, 12, 13, 16, 17, 18) ≤ 1 µs; main loop ≤ 1000 µs; step CPU ≤ 15 %; stack margin > 0 |
| HG-27 | [A] 1000 commands of 16 types under streaming; SAVE / LOAD / DEFAULT / LOAD | RTT ≤ 10 ms each (PC upper bound; > 10 ms → INCONCLUSIVE, resolve on chip); NVM ≤ 2.5 s |
| IMG-MEAS2 | [M] PO flashes HW_MEAS again | – |
| HG-21 / HG-22 | [M] presence sense fitted? | N/A in release 1 (D-41) |

### Phase 3 — buffer board (SYS-011)
| HG-23 | [M] inspection, taps moved to the buffer outputs; HG-06 / HG-07 / HG-08 repeated (`--buffer-fitted`) | I_LED 10…13 mA; timing as HG-07 / HG-08 |
|---|---|---|

### Phase 4 — first load (spring), only after the SYS-009 gate
| Step | What happens | Expected |
|---|---|---|
| GATE-LOAD | [A] HG-01…24, 28, 29, 32 PASS / N/A / accepted (`--accepted-open`) | otherwise phase 4 is **refused** |
| HG-12 | [M] fit the spring; [A] contact search, slope; 100 trials: `safety.load_raw_max` = raw + slope × 0.2…0.8 mm, jog 0.5 mm/s, FAULT_SET(LOAD_LIMIT) | trip on the first violating sample; deciding DOUT edge → last PUL ≤ 200 µs (u 2 µs) |
| HG-15 | [A] preload ≥ 98 N (raw + 98 × counts/N); [M] dial zeroed; NRST, Nucleo power cycle (port re-opened), HANG → IWDG | dial − commanded ≤ 0.01 mm; raw change within noise; MT-2 = 0 from boot until ENABLE |
| HG-25 | [M] travel calibration done (M3 wizard), spring removed; [A] 0.01 mm/s × 2 mm (dial), 10 mm/s × 50 mm (caliper), 30 mm/s full travel, re-home, soft_min → soft_max | speed error ≤ 1 % (dial u 0.5 %, caliper 0.04 %); no ALM; HOME drift ≤ 200 µm; travel ≥ 280 mm |
| HG-26 | [M] dial at x = 0; [A] 10 × (20 mm → HOME) | 2σ ≤ 0.02 mm |

### Phase 5 — release image
| IMG-REL → HG-30 | [M] PO flashes `nucleo_f446re`; [A] FEAT_HW_MEAS = 0, DIAG_MEAS → E_INTERNAL NOT_IN_BUILD; HG-32 repeat; E-stop (press during a jog), START real actuation + latch, load-limit trip (spring), STOP / HALT / PAUSE, HOME, 2-min soak, loop_max_us; [M] HG-31 jumpers fitted | same behaviour and EVENT sequences as with HW_MEAS; loop ≤ 1000 µs |
|---|---|---|

## 5. Bench safety procedure (FW_test_plan §6.8) as run by the session

| Step | Session | Operator / observer |
|---|---|---|
| P-1 | sets the soft limits to **5…55 mm** (RAM; next to START, see OI-E-HG-02) | no specimen, nothing in the travel, two persons named, both limit switches connected |
| P-2 | **refuses the bypass** unless HG-10cd PASSED in this session | – |
| P-3 | prompt | PSU **off**; unplug the NC sense connector; fit J-STIM (PB8, 220 Ω) to the PA10 connector; tag on the red button; photo |
| P-4 | asks resistor value and PA10 voltage with STIM idle | 200…240 Ω; ≤ 0.99 V |
| P-5 | STIM hold 100 ms with the driver unpowered: GET_STATUS ESTOP_OPEN 0 → 1 | fails → stop, restore (P-8) |
| HG-29 e | E-stop input RC delay (J-EVT ← PB8, J-AUX ← PA10 node) → u_th E-stop | then J-EVT ← PA10 node |
| P-6 | prompt PSU on; HG-10a trials | observer's hand at the red button (the D-42 NO channel stays active) and at the PSU switch |
| P-7 | any unexpected motion, noise, ALM, position outside the window → observer presses the red button + PSU off; operator types `abort` → HALT | incident note |
| P-8 | BENCH-EXIT: press / release checks must PASS before any further motion; soft limits restored | PSU off, remove J-STIM, reconnect the sense, remove the tag, PSU on, press / release when asked |

The same steps without P-2 apply to the STIM trials on START / END (HG-11): the red button stays fully operational.

## 6. Interruptions and errors
- Any exception inside a procedure → the session sends HALT + HALT_CLEAR, records `ERROR`, continues with the next
  step (bench entry failures skip HG-10a; BENCH-EXIT still runs).
- `abort` / Ctrl+C → HALT, report written, exit code 3. Resume with `--from`.
- A lost USB link (Nucleo power cycle in HG-15) is handled by the step itself (port closed / re-opened).
- Stop-guard (`Link.stop_guard_ms`, 25 ms wait after STOP / HALT / PAUSE before a motion command) was the workaround
  for DEF-M3-01; **off (0) since A's fix in 2d36eec** (verified at M3: reproducer 3/3 pass, dry run without the guard).

## 7. What stays manual (no automation possible)
Inspection and photos (HG-01, -19, -20, -23, -31), all DMM readings (HG-01, -06, -10 d M-1…M-4, -16, -20 M-5, -23,
§6.8 P-4), "shaft free / holding" by hand (HG-07, HG-10 c, HG-30), MCU held in reset (HG-10 c/d, HG-17), unplugging
connectors and moving jumpers (J-EVT / J-AUX / J-STIM per step), real button presses (HG-10 b/e, HG-04, HG-18, HG-30),
PSU switching (PSU-off blocks, HG-16, HG-24, P-3/P-6/P-8), provoking an ALM (HG-24), caliper / dial readings (HG-09,
-15, -25, -26, -28), spring mounting and preload (phase 4), travel calibration (M3 wizard, before HG-25), flashing (PO).

## 8. Twin dry run (evidence that the scripts work; not hardware evidence)
Every automatable procedure runs against A's FW in the twin with the Integrator's DIAG_MEAS model
(`Twin(hw_meas=True)`); physical actions the twin models are emulated (E-stop, limits, wire breaks, ALM, PSU, resets,
spring, PAUSE), world-position readings stand in for caliper / dial, DMM / inspection answers are flagged
*dry-run default* and stay MANUAL. Not in the twin model: J-AUX stamps, DWT (HW_MEAS_DWT), RC / threshold delays, the
D-42 NO contact, ISR latencies (the twin reacts at the event instant). Latest dry-run report: `dryrun/` in this folder.

## 9. Decisions, findings and their status (v0.2)
| ID | Item | Status |
|---|---|---|
| OI-E-HG-01 … -07 | HG-10 c wording, §6.8 window, PSU-off pulse trains, HG-04 a real press, pulse-timing margin, HG-29 c 10 ms hold, J-EVT ← ENA | **decided by D-45 (a)–(g)**, applied to FW_test_plan v0.4.2 and this runbook; (g) deferred / optional |
| DEF-M3-01 (A) | stale sniffed-stop hold discarded motion starts ≤ 20 ms after a stop frame | **closed** (2d36eec, dispatched-before-sniffed queue; `test_val_twin_m3_sniffhold.py` 3/3 pass; stop guard removed) |
| DEF-HG-01 (A) | first PWM-input capture after arming entered the statistics | **closed by inspection** (`meas_f4.c` `s_pwm_first`); target confirmation at HG-29 d / HG-02 c |
| OBS-E-HG-01/02/05 (C) | twin RX trigger point, STIM spacing, PWM min | **fixed** in ICD v0.7.3 twin model; scripts use the board conventions for both |
| OBS-E-HG-03 (C) | twin has no ISR latency | documented (captures report 1 tick); latency budgets are target-only |
| R-HIL-01 (C review) | stale hard-coded dict hash | **fixed**: expected hash from `gen_params.load().hash` |
| R-HIL-02 (C review) | approval matched as a substring | **fixed**: dedicated *approved* table row, whole-token cell match |
| R-HIL-03 (C review / Orchestrator) | 50 kHz trials assumed the old defaults | **fixed**: 10 + 10 µs and 50 kHz set explicitly in the PSU-off block; HG-08 a1 runs the dict-6 defaults |
| R-HIL-04 (C review, low) | twin compensations obsolete after the v0.7.3 model fixes (stimulus spacing, RX reference point, CCR3 = 0 fallback) | **fixed**: removed, board conventions for twin and board |
| R-HIL-05 (C review, low) | `Link.stop_guard_ms` DEF-M3-01 workaround | **fixed**: default 0 |
| R-HIL-06 (C review, info) | docstrings cited ICD v0.7.1 | **fixed**: v0.7.3 |
| OBS-M3-HIL-01 (A, low) | PWM_INPUT has no overflow flag: a period > 65 535 probe ticks wraps silently | scripts arm only during cruise (periods ≪ 65 535 ticks at the PSC used); optional flag for A |
