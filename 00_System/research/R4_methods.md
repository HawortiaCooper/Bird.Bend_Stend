# R4 — Methods, Algorithms and Formulas

Project: Bird Bend Stand (pull/push bend test stand). NUCLEO-F446RE → HBS86H closed-loop stepper driver
(PUL/DIR/ENA) → 86HS2140 motor → crosshead; Keli DEF 200 kg S-type load cell → HX711 (80 SPS, gain 128);
START/END limit switches; NC E-stop; PC over UART 921600 + CRC-16.
Status: **v0.1, 2026-10-03**, P0 Research. Author: Researcher (R4). Audience: Implementer A (FW),
B (SW backend), C (Integrator: ICD, params, simulator/host twin), D (GUI), Validators E/F.

Binding inputs: `Initial_specs.txt`, `00_System/specs/DECISIONS.md` D-01…D-10 — in particular
**D-05** (data frame fields), **D-07** (hardware later → simulator first), **D-08** (two end-of-travel
switches, one is home; no operator buttons), **D-09** (PlatformIO + stm32duino, LL/register timers),
**D-10** (E-stop: stop pulses immediately, **keep driver enabled/holding**; disable only on fault/timeout).
Parallel research: R1 (Stefan FW), R2 (hardware datasheets), R3 (Thrust_Stand_HAW reuse). Where a number
belongs to their scope it is marked ASSUMED here and must be confirmed there.

Fact tags: **VERIFIED** (checked in a source/file cited), **ASSUMED** (engineering estimate or typical
value, to be confirmed), **UNKNOWN** (needs PO/HW answer). Parameter names (`motion.*`, `home.*`,
`safety.*`, `cal.*`) are **proposals** for the Integrator's `params.yaml`; requirement IDs do not exist yet
(SRS is P1) — sections are written so the Orchestrator can cut requirements from them.

## Sources

| # | Source | Used for |
|---|---|---|
| S1 | D. Austin, "Generate stepper-motor speed profiles in real time", *Embedded Systems Programming*, Jan 2005; Atmel **AVR446** "Linear speed control of stepper motor" (2006) | ramp recursion `c_n = c_{n-1} − 2c_{n-1}/(4n+1)`, `c0 = 0.676·f·√(2/α)` |
| S2 | AccelStepper library (M. McCauley) documentation | per-step-poll architecture, uses S1 |
| S3 | ST **RM0390** (STM32F446 reference manual), general-purpose timers TIM2–TIM5, advanced TIM1/8 | 32-bit TIM2/TIM5, PWM mode 2, ARR/CCR preload, OCxM "force inactive", master/slave ITR, DMA burst |
| S4 | stm32duino core, `~/.platformio/packages/framework-arduinoststm32/variants/STM32F4xx/F446R(C-E)T/variant_NUCLEO_F446RE.cpp` lines 124–170 (local file, read) | **VERIFIED** clock: HSI 16 MHz → PLLM 8, PLLN 180, PLLP 2 → SYSCLK 180 MHz; APB1 ÷4 = 45 MHz (TIM2–7,12–14 clock 90 MHz); APB2 ÷2 = 90 MHz (TIM1/8/9–11 clock 180 MHz). `TIMER_TONE`=TIM6, `TIMER_SERVO`=TIM7 (variant .h l.141–145) |
| S5 | Stefan FW (read-only): `FW/stanok/App/Inc/axis.hpp`, `hw_stm32.hpp`, `Core/Src/tim.c`, `Core/Src/main.c` | reference motion core, homing, findings §1.9 |
| S6 | Thrust_Stand_HAW `00_System/research/R4_calculations_methods.md`, `00_System/specs/ICD_protocol.md` §2, §3.3 (SET_VALID_FLAG), §3.6 rule 7 (E-STOP bound), `02_FW/platformio.ini` (`-DHAL_TIM_MODULE_ONLY`), `02_FW/src/pure/hx711_seq.h` | OLS/limit methods, SEQ/VALID semantics, timer ownership under stm32duino |
| S7 | HBS86H product pages / manual excerpts (web search 2026-10-03: besomi.com product page; cnc.info.pl manual copy, not downloadable) | max pulse input **200 kHz (VERIFIED, vendor page)**; "DIR ahead of PUL ≥ 5 µs, pulse width > 1.2 µs" (search snippet, **ASSUMED** until R2 confirms from the manual) |
| S8 | Avia Semiconductor **HX711** datasheet | 80 SPS, gain 128 input range ±0.5·AVDD/128, output settling 50 ms @ 80 SPS (ASSUMED from memory → R2), PD_SCK high > 60 µs = power-down |
| S9 | ASTM D790 / ISO 178 (3-point flexure) | optional stress/strain/modulus derived channels |
| S10 | OIML R 60 (load cells) | non-linearity / creep definitions, typical 0.02–0.05 % FS |
| S11 | JCGM 100:2008 (GUM); Rousseeuw & Croux 1993 (MAD, 1.4826) | uncertainty, robust statistics |
| S12 | IEC 60204-1 §9.2.2 / §9.2.5.4 | stop categories 0/1/2, emergency-stop category requirement (risk note §4.1) |

---

## 0. Conventions

### 0.1 Constants and units
- `G0 = 9.80665 m/s²` (exact). Masses entered in kg → reference force `F = m·G0` [N]. Optional `cal.g_local`
  (default G0; Kyiv ≈ 9.81 m/s², ASSUMED, difference ≈ 0.04 %).
- Canonical units: force **N** (display N / kgf = N/G0), travel **mm** in SW, **µm int32** on the wire,
  **steps int32** inside FW, time **µs** (device) / s (SW), speed mm/s (SW) / µm/s (wire), accel mm/s² / µm/s².
- HX711 raw: signed 24-bit (−8 388 608 … 8 388 607) sign-extended into the 32-bit frame field (D-05).
  Rails `0x7FFFFF` / `0x800000` = **saturated** → invalid sample.

### 0.2 Sign conventions
- **Force**: `F > 0` = **tension (pull)**, `F < 0` = compression (push). Fixed by the pull calibration
  (weights hang = tension, entered as positive mass) — K may be negative, the sign is carried by K.
- **Travel**: `x` increases away from the home switch (machine coordinate, µm, 0 = home reference §3).
  Which physical direction pulls the specimen depends on the fixture → `motion.pull_dir` (+1/−1) is a
  config item used only by the load-target logic (§8.4) and generators.
- `NaN` (never 0, never inf) for undefined derived values; each carries a reason (S6 convention).

### 0.3 Rounding
All float→int conversions (µm↔steps, mm→µm, calibration step counts) use **round half away from zero**
(`lroundf` in C; in Python **not** `round()` — use `math.floor(abs(x)+0.5)*sign(x)`). Vectors in §12 avoid ties.

---

## 1. Step pulse generation (FW)

### 1.1 What rates are needed
- HBS86H accepts ≤ **200 kHz** (S7, VERIFIED). Pulse timing: PUL high and low ≥ 2.5 µs, DIR stable
  ≥ 5 µs before the active PUL edge, ENA ≥ 5 µs (we use 200 ms, §1.7) before DIR — **ASSUMED** (S7
  snippet says pulse > 1.2 µs; 2.5 µs adds margin) → R2 confirms.
- Steps/mm is mechanics-dependent (UNKNOWN: lead screw/belt + driver pulses/rev). Stefan default 640
  steps/mm (S5 `axis.hpp:52`) is used for all examples. At 640 steps/mm: 20 mm/s = 12.8 kHz; 200 kHz =
  312.5 mm/s (far above any bend-test speed). A bend stand typically needs 0.01–20 mm/s → **≤ 13 kHz
  at 640 steps/mm**; design for `motion.max_step_rate_hz` default **100 kHz**, hard cap 200 kHz.
- Required properties: exact step count (position = count, no encoder feedback to the MCU), jitter-free
  constant speed (load-rate tests), immediate stop (< 100 µs, §4), controlled decel stop, very slow
  speeds (creep tests, e.g. 0.001 mm/s = 0.64 steps/s).

### 1.2 Options compared (180 MHz F446RE, stm32duino clock S4)

| | (a) Timer ISR toggles GPIO (AccelStepper/AVR446 style) | **(b) Timer PWM output, ARR/CCR preload, update-IRQ per step** | (c) PWM + DMA burst of ARR(/CCR) from a table | (d) Master PWM timer + slave timer counting pulses |
|---|---|---|---|---|
| Pulse shape | software: needs 2 ISRs/step or a 2.5 µs busy-wait (450 cycles) | **hardware**, exact width | hardware | hardware |
| Period jitter | ISR latency jitter (≈0.1–0.6 µs, more if masked by other ISRs) | **0** (period change only at update events) | 0 | 0 |
| CPU per step | ≈1–3 µs (two entries or busy wait) → 20–60 % at 200 kHz | ≈0.5–0.9 µs (ASSUMED 80–150 cycles incl. 1 VSQRT+1 VDIV during ramps, register-level, no HAL) → **≈5–9 % at 100 kHz** | ≈0 per step; refill IRQ per half-buffer | as (a)/(b) for ramp updates; counting free |
| Max rate | ~50–100 kHz practical | 200 kHz (deadline = 1 period; ISR ≤ 1 µs) | > 1 MHz | > 1 MHz |
| Exact count | yes (counted where toggled) | **yes**: 1 update event = 1 completed pulse (PWM mode 2, §1.4) | yes via DMA NDTR, but stop/abort bookkeeping is fiddly | **hardware-exact**, independent of ISR latency |
| Instant stop | clear GPIO | write OCxM = "force inactive" (1 register, immediate) | same + abort DMA | same |
| Speed changes on the fly (jog, load trim) | easy | easy (next period) | needs table regeneration | easy |
| Complexity / host-testability | low / good | **low / good** (ramp math is a pure function) | high (profile tables, memory: 1 word/step during ramps) | medium (2 timers, ITR mapping) |
| Verdict | rejected (CPU, jitter) | **recommended** | rejected (not needed ≤ 200 kHz) | **optional add-on** to (b) as an independent pulse counter (diagnostics/HIL) |

AccelStepper's `run()` polled from `loop()` is rejected outright: step timing would depend on main-loop
latency (UART/HX711 work), violating "no blocking > 1 ms" and the jitter requirement.

### 1.3 Recommendation
**(b) hardware PWM on a 32-bit timer, PWM mode 2 (pulse at the END of each period), ARR+CCR preload, one
update interrupt per step that counts the step and loads the next period; periods from the exact
square-root ramp (§1.5)**; optional (d) TIM3 slave counter as cross-check. This is the Stefan
architecture (S5 uses TIM2 PWM + update IRQ + AVR446 recursion) with three corrections: pulse placement,
exact ramp, register-level (LL) code with correct clocks (§1.9).

### 1.4 Timer configuration (numbers for stm32duino 180 MHz, S4 VERIFIED)

| Item | Value | Note |
|---|---|---|
| Step timer | **TIM2** (32-bit, APB1 timer clock **90 MHz**) on **PA0 = TIM2_CH1 (AF1)** (Stefan PUL pin; final pin = R1/HW). TIM5_CH1 (AF2) on the same pin is the alternative | 32-bit ARR → no prescaler switching |
| PSC | **0** → f_tick = 90 MHz, 11.11 ns | |
| Period | `ARR = c − 1`, `c = round(f_tick / f_step)`; c ∈ [2·PW, 2³²] | min c = 450 (200 kHz); max 2³² ticks = **47.7 s/step** (0.021 steps/s) |
| Pulse | **PWM mode 2** (`OC1M = 0b111`), `CCR1 = c − PW`, `PW = 225` ticks (2.5 µs, `motion.pulse_width_ns` = 2500) | output inactive while CNT < CCR1, active for the last PW ticks → pulse ends exactly at the update event |
| Preload | `CR1.ARPE = 1`, `CCMR1.OC1PE = 1` | new ARR and CCR1 take effect together at the next update |
| IRQ | `DIER.UIE = 1`, TIM2_IRQn priority **1** (E-stop EXTI = 0) | handler owned by us: build with `-DHAL_TIM_MODULE_ONLY` (as Thrust_Stand, S6) so stm32duino HardwareTimer does not define TIMx_IRQHandler |
| Polarity | `CCER.CC1P` from `motion.pul_invert` | the driver's active edge depends on opto wiring (R2) |
| Start | DIR set first; CNT = 0; ARR/CCR1 = first period; `EGR.UG = 1`; clear `SR.UIF`; `CEN = 1` | first pulse begins at `c1 − PW` (ms-scale) ⇒ DIR setup time automatically ≫ 5 µs |
| Speed resolution | c = 900 at 100 kHz → 0.11 % per tick; at 12.8 kHz c_min = 7031.25 → with the fractional carry (§1.5) periods 7031/7031/7031/7032 → **exact mean speed**, 1-tick (11 ns) jitter | |
| Timestamp timer | **TIM5** 32-bit, PSC = 89 → **1 MHz** free-running, ARR = 0xFFFFFFFF, no IRQ | `t_us` of D-05 (§9) |
| Optional pulse counter (d) | TIM3 (16-bit, SW-extended), slave **external clock mode 1**, `TS = ITR1` (= TIM2 TRGO, ASSUMED per RM0390 "TIMx internal trigger connection" table → verify), TIM2 `CR2.MMS = 0b100` (OC1REF as TRGO) | compare with ISR count after each move → `STEP_COUNT_FAULT` |
| Do not use | TIM6/TIM7 (stm32duino Tone/Servo), SysTick (millis) | |

### 1.5 Ramp algorithm — exact square-root ramp with "max rule"
Constant acceleration from rest reaches step k at `t_k = √(2k/α)`. Hence the period before step k
`c_k = f·(√k − √(k−1)) = C_a / (√k + √(k−1))`, `C_a = f·√(2/α)` — the second form has no cancellation
in float32. Mirrored for deceleration with `r` = steps remaining including the one being timed.
For a position move of N steps the period of step k (k = 1…N, r = N − k + 1) is

```
c_k = max( C_a/(√k + √(k−1)),  c_min,  C_d/(√r + √(r−1)) )      c_min = f / v_max,  C_d = f·√(2/α_dec)
ticks: acc += c_k; c_int = (uint32)acc; acc -= c_int          # fractional carry → no time drift
```
The max of the three terms produces accel / cruise / decel and the triangle case automatically; total
move time equals the continuous profile exactly (vectors §12 TV-M: 0.2 s, 0.25 s, 5.2 s to the tick).
Cost per ramp step: one `VSQRT.F32` (√(k−1) is the previous √k) + one `VDIV.F32` ≈ 30 cycles on the M4F;
cruise steps only add the constant `c_min = f/v` (float, not rounded) to the carry. After each step the carry
leaves `acc ∈ [0, 1)`; float32 rounding of `acc + c` is ≤ 0.03 tick for c ≤ 5·10⁵, so FW periods match
the double-precision reference within ±1 tick and the total move time within ±N/1000 ticks (§12 note).
- **Velocity mode (jog) and on-the-fly speed changes**: keep a *virtual* index: speeding up from v₁
  continues `C_a` with `k₀ = v₁²/(2α)`; slowing to v₂ (or stopping) uses `C_d` with a virtual
  `r` counting down from `v₁²/(2α_d)` to `v₂²/(2α_d)` (0 for stop).
- **Controlled stop during a move**: `r ← min(r, ⌈v_cur²/(2α_d)⌉)`; the decel term takes over continuously.
- Austin/AVR446 integer recursion (S1, Stefan) is acceptable as a fallback: it needs the 0.676 first-step
  correction and the remainder carry (Stefan `rampNext`) and deviates up to ~1 % in early steps
  (TV-M compares both). The exact form is simpler to test because host twin and FW share one pure function
  (`02_FW/src/pure/ramp.c` + Python twin in the simulator, D-07).
- Trapezoid/triangle **planner** (for SW previews and timeouts): `n_acc = v²/(2α)`, `n_dec = v²/(2α_d)`;
  if `n_acc + n_dec ≤ N` trapezoid (`t = v/α + v/α_d + (N − n_acc − n_dec)/v`), else triangle with
  `n_acc = N·α_d/(α+α_d)`, `v_peak = √(2·α·n_acc)`, `t = v_peak/α + v_peak/α_d`.

### 1.6 Exact step counting rules
1. Position (`pos_steps`, int32) changes **only** in the TIM2 update ISR: `pos += dir` — with PWM mode 2
   the update coincides with the trailing edge of the pulse, i.e. "pulse completed".
2. End of a position move: the ISR that counts step N writes `OC1M = force inactive (0b100)` and clears
   `CEN`. The next pulse could start only `c − PW` ticks after that update → deadline ≥ 2.5 µs at 200 kHz,
   ≥ 7.5 µs at the 100 kHz default. Guard: after stopping, if `CNT ≥ CCR1` a pulse may have started →
   raise `STEP_OVERRUN` (diagnostic; position uncertainty ±1 step).
3. Missed-update guard: at ISR exit, if `SR.UIF` is set again a full period elapsed inside the ISR (two
   updates merged) → `STEP_OVERRUN`, stop, clear HOMED. Prevented by priority 1 and short ISRs elsewhere
   (HX711 bit-bang masks IRQs only while SCK is high, ≤ 0.6 µs, S6 `hx711_seq.h`).
4. DIR is only changed while the timer is stopped; reversal = decel to 0, stop, set DIR, restart.
5. Immediate (hard) stop may truncate a pulse in flight (≤ PW) → count uncertainty ±1 step = 1.6 µm at
   640 steps/mm; flag `pos_uncertain` but keep HOMED (negligible vs switch repeatability, §3).
6. Optional (d): TIM3 hardware count must equal |Δpos| after every move.

### 1.7 ENA handling
`motion.ena_invert` per wiring. After ENA becomes active wait `motion.ena_settle_ms` (default **200 ms**,
ASSUMED for closed-loop drivers that initialise/align after enable → R2) before the first pulse. ENA is
never toggled by stop/E-stop (D-10, §4).

### 1.8 Interrupt priorities and CPU budget (proposal for FW_design)

| NVIC prio | Source | Worst-case body |
|---|---|---|
| 0 | E-stop EXTI (both edges) | ≤ 0.3 µs (force-inactive + CEN=0 + latch) |
| 1 | TIM2 update (step) | ≤ 1 µs |
| 2 | Limit switch EXTI ×2, driver ALM EXTI | ≤ 0.5 µs |
| 3 | HX711 DOUT-falling EXTI → timestamp + read (≈ 30–50 µs bit-bang, IRQs masked only during SCK high) | read must finish < 60 µs SCK-high rule (S8) |
| 5 | UART DMA, SysTick (`TICK_INT_PRIORITY=5`, as S6) | |
Budget at 100 kHz: step ISR ≈ 9 % CPU (ASSUMED; measure with DWT->CYCCNT in FW tests).

### 1.9 Findings on the Stefan reference (for R1 / Implementer A)
- **Clock mismatch (VERIFIED by reading):** `main.c:136–158` runs SYSCLK = HSI 16 MHz/16·336/4 = **84 MHz**,
  APB1 ÷2 → TIM2 clock **84 MHz**; `tim.c:45` `Prescaler = 9` → **8.4 MHz**; `hw_stm32.hpp` assumes
  `kTimerHz = 10 MHz`. All speeds/accelerations in Stefan are ≈ **0.84×** commanded. Under stm32duino
  (180 MHz) TIM2 runs at 90 MHz — derive `f_tick` from `HAL_RCC_GetPCLK1Freq()`×2, never hard-code.
- **Phantom/runt pulse at stop (analysis, UNVERIFIED on HW):** Stefan uses PWM mode **1** (pulse at the
  *start* of each period) and stops in the update ISR (`axis.hpp:287` → `finish` → `HAL_TIM_PWM_Stop`).
  At that update the next period has already begun and PUL is already high, so a pulse of ≈ ISR+HAL
  latency (1–3 µs) is emitted but not counted → possible **+1 step per move** drift. Fixed by PWM mode 2 (§1.4).
- Homing sequence (fast seek → back-off → slow 1/5 speed re-approach → zero, `axis.hpp:534–564`) is a good
  template; it lacks a seek-distance timeout and edge-captured position (§3).

---

## 2. Motion modes and position representation

### 2.1 Commands (FW executes single moves; the PC sequences them)

| Mode | Parameters (wire units) | Behaviour |
|---|---|---|
| MOVE_ABS | `target_um` i32, `v_um_s` u32, `a_um_s2` u32 (0 = default) | trapezoid/triangle (§1.5); requires HOMED (§3.4) |
| MOVE_REL | `delta_um` i32, v, a | relative to the **commanded target** of the previous move (not the live position) → no accumulated rounding; allowed un-homed (switches still active) |
| JOG | `v_um_s` i32 signed, a | implemented as MOVE_ABS to the soft limit in that direction (un-homed: to ±`home.max_travel_um`) at speed v → can never run past soft limits; new JOG while jogging changes speed on the fly; **requires heartbeat** (dead-man, §4.5) |
| MOVE_UNTIL_LOAD | direction, v, a, `raw_stop` i32 | like JOG but stops (immediate) on the first HX711 sample beyond `raw_stop` (§8.4); also stops at the travel bound `max_dist_um` |
| STOP | `mode` 0 = controlled decel (`a`), 1 = immediate | not latched; GUI "Stop" button = immediate (PO §5) |
| ESTOP / ESTOP_CLEAR | — | software E-stop (Pause/Break), §4 |
| HOME | — | §3 |
| ENABLE / DISABLE | — | DISABLE refused while moving; GUI asks for "specimen unloaded?" confirmation (§4.2) |

Speeds/accels are clamped to `motion.v_max_um_s` (default 20 000), `motion.a_max_um_s2` (default 100 000),
and `motion.max_step_rate_hz`. A new MOVE while moving: FW decelerates then starts the new move
(Stefan "pending" behaviour) — or rejects with `E_BUSY` (simpler; recommended for M2).

### 2.2 Representation
- FW internal: `pos_steps` int32 (±2³¹ steps = ±3.3 km at 640 steps/mm — no overflow concern).
- `motion.steps_per_mm` float32 (range 1…100 000). `steps = round(um·spm/1000)`,
  `um = round(steps·1000/spm)` (half away from zero). µm resolution < 1 step (1.56 µm), so the wire unit
  µm is sufficient; float32 spm has 6e-8 relative precision (< 0.02 µm over 300 mm).
- Changing `steps_per_mm` only while idle. HOMED stays valid (zero is a step count); positions in µm rescale.
- Machine coordinate (FW): 0 = home reference. **Travel zero for a test** (e.g. at specimen contact) is a
  **PC-side offset** like tare (`x = pos_um/1000 − x_zero`), stored in the recording — keeps FW soft
  limits in one coordinate system.

### 2.3 "Setpoint distance" in the data frame — recommendation
Candidates: (i) instantaneous **commanded profile position** = steps emitted so far; (ii) final target of
the current move; (iii) measured position (not available: the HBS86H encoder closes the loop inside the
driver; only ALM reaches the MCU).
**Recommend (i)**: `pos_um` = `steps_to_um(pos_steps)` latched **at the HX711 data-ready instant** (read in the
same EXTI ISR as the timestamp — a 32-bit aligned read is atomic on the M4), plus a **MOVING** flag bit.
Rationale: it is the x-axis of the travel–load chart (PO §15), it is exact (open-loop count = command),
and "setpoint constant" for steady-state extraction = `MOVING = 0` and `pos_um` unchanged. The target (ii)
is known to the PC anyway (it sent it). Deviations from true specimen deflection: driver following error
(small, closed loop), machine compliance (frame + S-cell deflection, ASSUMED ≈ 0.1–0.3 mm at 200 kg) →
optional compliance correction §10.
Proposed flag byte (Integrator decides): bit0 VALID (D-05), bit1 MOVING, bit2 HOMED, bit3 ESTOP latched,
bit4 LIMIT active/latched, bit5 FAULT (incl. load-limit trip), bit6 AFE_BAD (saturated/stale), bit7 OVERRUN
(frame dropped in FW before this one).

---

## 3. Homing / zeroing and limit switches (D-08)

### 3.1 Sequence (`home.switch` = START (default) or END)
| Phase | Action | Default |
|---|---|---|
| 0 Pre-check | enabled, no E-stop/fault, ALM inactive, **|F| < `home.max_load`** (homing with a loaded specimen is refused unless the operator confirms; FW load limit stays active) | max_load: 5 % FS (UNKNOWN → Q-R4-04) |
| 1 Release | if home switch already active: move away at `v_slow` until released, then `backoff` further | |
| 2 Fast seek | toward home switch at `home.v_fast_um_s`, accel `home.a_um_s2`; abort `HOME_NOT_FOUND` after `home.max_travel_um` | 5 mm/s, 50 mm/s², 1.2 × axis length (UNKNOWN length → default 400 mm) |
| 3 Hit | switch edge → **immediate** stop (§3.3) | overtravel at 5 mm/s ≈ few µm + motor stop |
| 4 Back-off | away at `v_slow` until released, then `home.backoff_um` | 2 mm |
| 5 Slow approach | toward switch at `home.v_slow_um_s`; in the switch EXTI ISR **capture `pos_steps` at the edge** (exact, bounce-free: first edge only, EXTI then masked) | 0.5 mm/s (≤ v_fast/10) |
| 6 Set zero | `pos_steps -= pos_at_edge` → edge = 0 − `home.offset_um`; i.e. machine 0 is `home.offset_um` away from the switch on the travel side; move to 0 | offset 1 mm |
| 7 Done | HOMED = 1, soft limits active, EVENT HOMED | |
Wrong switch during seek (END hit while seeking START) → `HOME_WIRING` fault (DIR or switch swapped).
Repeatability is set by phase 5: at 0.5 mm/s an edge-detection latency of 100 µs = 0.05 µm; micro-switch
repeatability ±5…20 µm (ASSUMED → R2).

### 3.2 Soft limits after homing
`limits.soft_min_um` (default +500 µm, i.e. clear of the home switch) and `limits.soft_max_um`
(axis length − margin; UNKNOWN). Checked at command time (`E_RANGE`), and JOG targets the soft limit (§2.1)
so no ISR check is needed. Not homed: MOVE_ABS refused (`E_NOT_HOMED`); MOVE_REL/JOG allowed at
≤ `motion.v_unhomed_um_s` (default 2 mm/s) with hard switches active.

### 3.3 Switch hit during a normal move
Immediate pulse stop (same path as E-stop but not an E-stop latch), `LIMIT_x` latched, EVENT LIMIT_HIT,
VALID cleared (§8.6), sequencer aborted by the PC. Motion **toward** the active switch is refused; motion
**away** is allowed (to free it). LIMIT latch clears automatically when the switch has been released
≥ 20 ms and the axis moved away. Both switches active at once → `LIMIT_WIRING` fault (physically
impossible unless a wire is broken, §3.5).

### 3.4 Debounce
- **Stop on the first edge** (no software delay): a false stop is safe-side.
- Clear/"released" requires a stable level for ≥ 20 ms (1 kHz sampling, 20 equal reads).
- Hardware: RC τ ≈ 100 µs (e.g. 1 kΩ / 100 nF) at the MCU pin + Schmitt-trigger input; for the E-stop
  τ ≤ 10 µs (reaction budget §4.6). Mechanical bounce 1–5 ms (ASSUMED) only affects the release path.

### 3.5 NC wiring and broken-wire detection
NC contact to GND, pull-up to 3.3 V (or opto loop): **closed = low = OK; open = high = active**. A broken
wire, unplugged connector or missing switch therefore reads "active" → fail-safe stop. Plain digital
inputs cannot distinguish "pressed" from "broken"; available plausibility checks:
both switches active simultaneously (`LIMIT_WIRING`), E-stop/limit active at boot (report, refuse
motion), switch active while the axis is far from it (e.g. END active right after homing at START).
Optional HW upgrade (R2/HW decision): resistor-coded loop read by ADC (e.g. series 1 kΩ + parallel 10 kΩ
→ three bands short / normal / open) to detect short-circuits too.

---

## 4. Safety layers (D-10)

### 4.1 E-stop semantics (decided by D-10)
NC E-stop input open → **stop step pulses immediately** (OC1M force-inactive + CEN = 0 in the EXTI ISR),
**ENA stays active** (closed-loop driver holds position against the specimen load), ESTOP latched,
VALID cleared, any running move discarded (no resume). Position and HOMED remain valid (driver kept
position) unless the driver raises ALM.
Risk note for the PO (S12): a stop with power maintained corresponds to IEC 60204-1 **stop category 2**;
the standard requires emergency stop functions to be category 0 or 1. If the NC button must protect
persons (pinch point at the crosshead), a separate hardwired category-0 path (e.g. contactor cutting the
driver's motor supply) is needed in addition to this FW stop → **Q-R4-01**.

### 4.2 When ENA is dropped (the "fault/timeout" of D-10) — proposal

| Condition | Pulses | ENA | Latch / clear | HOMED |
|---|---|---|---|---|
| E-stop input (button) | stop immediately | **keep** | ESTOP(src=button); clear = input closed ≥ 100 ms **and** PC `ESTOP_CLEAR` | kept |
| Pause/Break key → `ESTOP` cmd | stop immediately | keep | ESTOP(src=PC); clear = PC `ESTOP_CLEAR` | kept |
| Limit switch during move | stop immediately | keep | LIMIT_x, auto-clear §3.3 | kept |
| FW load limit (§4.4) | stop immediately | keep | LOAD_LIMIT; clear = PC `FAULT_CLEAR` | kept |
| Link watchdog (§4.5) | controlled stop with `motion.a_stop` (default 1000 mm/s²) | keep | LINK_WDG flag; clears on next valid frame | kept |
| AFE stale (no HX711 sample > `safety.afe_timeout_ms` = 100 ms) or saturated, while moving | stop immediately | keep | AFE_FAULT; clear when samples OK + `FAULT_CLEAR` | kept |
| Step overrun / count mismatch (§1.6) | stop | keep | STEP_FAULT; `FAULT_CLEAR` | **cleared** |
| **Driver ALM active** (≥ 1 ms) | stop | **drop** (driver already de-energised itself; toggling ENA is the usual alarm reset — ASSUMED → R2) | DRIVER_FAULT; clear = ALM inactive after ENA re-enable + `FAULT_CLEAR` | **cleared** |
| **Idle-unloaded timeout**: no motion for `motion.idle_disable_s` (default 600 s, 0 = never) **and** |F| < `safety.release_load` (2 % FS) | — | **drop** | EVENT MOTOR_DISABLED; re-ENABLE by PC | cleared (the rotor may shift while unpowered) |
| Explicit PC `DISABLE` (operator confirmed specimen unloaded) | — | drop | — | cleared |
| MCU reset / IWDG / brown-out | pins Hi-Z | **depends on ENA wiring polarity** (UNKNOWN) | boot: motor disabled by default (CLAUDE.md) | cleared |
Recommended: **no automatic ENA drop while loaded** — link loss and E-stop never release the specimen.
MCU reset behaviour: with many Leadshine-type drivers an *unpowered* ENA opto means **enabled** (ASSUMED);
then a MCU reset keeps holding — matches D-10 but contradicts "motor disabled by default". R2 must state
the HBS86H ENA logic; PO decides (**Q-R4-02**).

### 4.3 Pause/Break and GUI stop
- **Pause/Break** (global hotkey, any tab, also when the main window is not focused — Thrust_Stand has
  `io/win_hotkey.py`, R3): send `ESTOP` (software source) **and** abort the sequencer on the PC. Latched,
  cleared by an explicit GUI "Clear E-stop" action. (Alternative "pause & resume" semantics: **Q-R4-03**.)
- Manual-tab "Stop" button: `STOP immediate`, not latched.
- The PC must keep sending heartbeats while it shows an E-stop — the FW must not depend on the PC to stay stopped.

### 4.4 FW load limit (second layer beside PC limits)
- PC converts its force limits into raw thresholds after every calibration/tare change and writes
  `safety.load_raw_max` / `safety.load_raw_min` (+ `safety.load_limit_enable`):
  `raw_a = tare_raw + F_hi/K`, `raw_b = tare_raw + F_lo/K`; `raw_max = floor(max(raw_a, raw_b))`,
  `raw_min = ceil(min(raw_a, raw_b))` (handles negative K; rounding toward the tare = conservative).
- FW compares **every** HX711 sample (in the read ISR/task): `raw > raw_max || raw < raw_min || saturated`
  → immediate stop, LOAD_LIMIT latched. Trip after `safety.load_trip_samples` consecutive samples, default
  **1** (safe-side; a single glitch causes a nuisance stop only).
- Layering: PC trip level `F_pc` < FW level `F_fw` < cell safe overload (Keli DEF: typically 150 % FS,
  ASSUMED → R2). Default `F_fw = 1.1 × F_pc` limited to ≤ 100 % FS (1961 N).
- After a trip the operator must be able to unload: after `FAULT_CLEAR` motion is allowed even though the
  value is beyond the limit, but the FW re-trips if the violation **grows** by more than
  `safety.load_regrow_raw` (default 2 % FS in counts) beyond the value at clear.
- HX711 range check: at 2 mV/V and gain 128 (ratiometric, independent of AVDD) ≈ **21 475 counts/kg**,
  rails at ≈ 390 kg — saturation never occurs within the cell's rating (ASSUMED 2 mV/V → R2).

### 4.5 Link watchdog
Any CRC-valid frame from the PC is a heartbeat; the PC sends PING every 200 ms when idle.
`safety.link_timeout_ms` default **1000 ms**. If it expires while moving (any mode, also MOVE_ABS) →
controlled stop with `motion.a_stop` (at 20 mm/s and 1000 mm/s²: 0.2 mm), LINK_WDG flag, VALID cleared,
EVENT LINK_TIMEOUT; ENA kept. Rationale: PC-side limits and the Pause key are unavailable; the FW-side
load limit and switches keep working but a PC-orchestrated test must not continue blindly. JOG uses the
same watchdog as its dead-man.

### 4.6 Reaction-time budget

| Path | Chain | Budget |
|---|---|---|
| E-stop button → no further PUL edge | contact opens → RC (τ ≤ 10 µs) → EXTI (12-cycle entry) → 2 register writes | **normative ≤ 100 µs** (proposal), design ≈ 20 µs; then the closed-loop driver brakes to the last commanded position (residual motion ≈ following error) |
| Limit switch → stop | RC τ ≈ 100 µs + ISR | ≤ 200 µs; overtravel ≈ v·t (20 mm/s → 4 µm) + motor stop |
| Load → FW limit trip | force change → HX711 conversion (12.5 ms) + digital-filter settling (up to 50 ms at 80 SPS, S8, ASSUMED) → DRDY → read 50 µs → compare → stop ≤ 5 µs | first sample beyond threshold ≤ 12.5 ms + read latency after the *filtered* value crosses; full step response ≤ ~65 ms (+12.5 ms per extra trip sample) |
| Load → PC limit trip | above + UART (0.24 ms/frame) + ST-LINK VCP/USB latency (1–16 ms, ASSUMED) + PC processing/GUI (≤ 50 ms) + command back | ≤ 150 ms (ASSUMED; validate in HIL) |
| Link loss → stop | timeout + decel | 1 s + v/a_stop |
Force overshoot after a trip ≈ `k_spec · v · t_react` (stiffness N/mm × mm/s × s). Example: 50 N/mm,
2 mm/s, 50 ms → 5 N; 500 N/mm, 1 mm/s, 65 ms → 32.5 N. Rule for the GUI: warn if
`k_est·v·0.065 s > F_fw − F_pc` (speed too high for the configured margin).

---

## 5. Travel calibration (PO wizard: 10 mm, then 50 mm more)

### 5.1 Procedure
0. Preconditions: no specimen (no load), enabled, direction chosen (default +, away from home).
   **Backlash take-up**: wizard first moves +2 mm in the measuring direction; the operator then sets the
   reference (zero the dial indicator/caliper, or mark). All later moves continue in the **same direction**,
   so backlash (screw/nut, couplings) never enters the measurement.
1. Step 1: command `L1 = 10 mm` with the current `spm0`: `N1 = round(L1·spm0)` steps. Operator enters
   measured `D1` [mm]. `spm1 = N1 / D1` (use the actual step count, not `L1`, to avoid rounding bias).
   Send `spm1` to the FW.
2. Step 2: command `L2 = 50 mm` more: `N2 = round(L2·spm1)`. Operator enters the **total** distance
   `D_tot` from the step-1 reference (nominal 60 mm). `spm2 = (N1 + N2) / D_tot`.
   Consistency check: `spm2_inc = N2/(D_tot − D1)` must agree with `spm1` within 0.5 % (else warn
   "measurement inconsistent — repeat").
3. Store `spm2` (float32, display 3 decimals), with date and the measured values, in the calibration file
   and in FW params (SET_PARAM + save).

### 5.2 Total (60 mm) or only the 50 mm?
Use the **total**: same two reading errors as the increment, but a 6× longer base:
`u(spm)/spm = u(D)/D` → caliper ±0.02 mm: 0.2 % on 10 mm, **0.033 % on 60 mm**. With the backlash
take-up of step 0, both segments are backlash-free, so the total is strictly better; without it a
backlash `b` would bias step 1 by `b/10 mm` and the total by `b/60 mm` (the increment-only value would be
immune — keep it as the consistency check). Instrument: 60 mm needs a caliper/height gauge or a 100 mm
dial indicator; a 10 mm dial indicator is good for step 1 only (Q-R4-05).

### 5.3 Plausibility bounds
- Reject if `D ≤ 0` or `|spm_new/spm_old − 1| > 20 %` (typo, e.g. "1.005" instead of "10.05").
- Warn (confirm dialog) if the change > 5 % per step, or if `spm` leaves ±20 % of the nominal
  `ppr / lead` (when mechanics are configured), or param range [1, 100 000].
- Measurement must be made **without load**; machine compliance only matters under load (§10).

---

## 6. Load calibration (zero + 2 known weights)

### 6.1 Point acquisition
Per point: operator action → `cal.settle_s` = 2 s pre-settle (swinging weight) → **10 s window**
(800 samples nominal at 80 SPS; accept ≥ 760 unique samples = 95 %) of raw counts:
1. Drop saturated samples (any saturated sample → point invalid).
2. Robust outlier rejection: `med = median(x)`, `MAD = median(|x − med|)`, `σ_MAD = 1.4826·MAD`;
   reject `|x − med| > 5·σ_MAD` (Gaussian false-rejection 6e-7 → ~0 per window); if > 2 % rejected →
   point invalid (EMI/glitches; repeat).
3. Statistics on the kept samples: `mean`, `std` (ddof = 1), `SE = std/√N_eff` with
   `N_eff = N·(1 − ρ₁)/(1 + ρ₁)`, ρ₁ = lag-1 autocorrelation clipped to [0, 0.99] (S6 §9.1).
4. Stability: `drift = slope·T_w` (OLS slope of raw vs t, T_w = 10 s). For white noise
   `sd(drift) = std·√(12/N) = 0.12·std` (N = 800), so require
   `|drift| ≤ max(2·std, 20 counts)` (catches swinging decay, creep, thermal), and
   `std ≤ max(3·std_zero, 50 counts)` (std_zero = point-0 std of this session; HX711 80 SPS noise
   ≈ 90 nV ≈ 45 counts ≈ 2 g, ASSUMED → measured in M2). Failing points are re-taken, not silently used.

### 6.2 Fit and linearity
Model `F = K·raw + B` (F in N, raw in counts), OLS over the 3 points (S6 §7.1 formulas):
`K = Sxy/Sxx`, `B = ȳ − K·x̄`, residuals `r_i`, `R²`; `NL_span = 100·max|r_i| / F_span`
(F_span = max reference force), `NL_FS = 100·max|r_i| / (200 kg·G0 = 1961.33 N)`.
Decision rule "points are linear" (3 points):

| Status | Condition | Action |
|---|---|---|
| PASS | `NL_span ≤ 0.1 %` | save as active |
| WARN | `0.1 % < NL_span ≤ 0.5 %` | show residuals; operator may save (flag `linearity=warn`) or repeat |
| FAIL | `NL_span > 0.5 %` or K ≈ 0 or points not monotonic | refuse "points are not linear" (check fixture/weights/hook) |
Notes: R² is **not** a usable criterion (FAIL vector still has R² = 0.99977). With 3 points the single
degree of freedom equals the slope mismatch `k12/k01 − 1`. If `max|r_i| ≤ 3·u_r` (u_r = |K|·SE of the
points) the non-linearity is below the noise → report "linear within noise". A 2-point fit (only one
weight) is allowed but status `UNVERIFIED_LINEARITY`. Weight rules: `m1 ≥ 2 % FS`, `m2 ≥ 1.5·m1`;
extrapolating far beyond `m2` (e.g. 20 kg weights, 200 kg cell) is a known limitation — record `F_span`.
B is diagnostic only; at runtime the tare replaces it (§7).

### 6.3 Sign (pull vs push)
Calibration with hanging weights = **tension**, entered positive → `F > 0` pull. Compression uses the same
K by default (S-type cells are bidirectional; tension/compression sensitivity typically differ
≤ 0.1–0.5 %, ASSUMED → R2) and the record carries `push_calibrated = false`; the report states push forces
are "tension-calibrated". Optional separate push calibration (weights resting on a compression fixture)
→ `K_push` applied when `sign(K)·(raw − tare) < 0` (**Q-R4-06**).

### 6.4 Calibration file (JSON, loaded by default)
Location `%APPDATA%/BirdBendStand/calibration/` — `load_<sensor_serial>_<UTC>.json` +
`active_load.json` (copy of the active one; loaded at start-up). Travel calibration stored likewise
(`travel_<UTC>.json`). On load, compare `afe` block with the board's GET_CONFIG: different gain/rate/
channel → warn "calibration made with different AFE settings — invalid".
```json
{
  "schema": "bird.bend.cal.load", "schema_version": 1,
  "created_utc": "2026-10-03T12:00:00Z", "operator": "", "sw_version": "0.1.0",
  "sensor": {"model": "Keli DEF", "capacity_kg": 200, "serial": ""},
  "afe": {"type": "HX711", "channel": "A", "gain": 128, "rate_sps": 80},
  "board": {"fw_version": "", "uid": ""},
  "direction": "pull", "push_calibrated": false, "g_used": 9.80665,
  "points": [
    {"mass_kg": 0.0,  "force_n": 0.0,     "raw_mean": 125000.0, "raw_std": 45.0, "raw_se": 1.8,
     "n_used": 800, "n_rejected": 0, "drift_counts": 3.0, "t_start_utc": "..."},
    {"mass_kg": 10.0, "force_n": 98.0665, "raw_mean": 339760.0, "...": "..."},
    {"mass_kg": 20.0, "force_n": 196.133, "raw_mean": 554490.0, "...": "..."}
  ],
  "fit": {"k_n_per_count": 0.00045666488086106374, "b_n": -57.085393272546426,
          "residuals_n": [0.00228, -0.00457, 0.00228], "r2": 0.9999999983736457,
          "nl_pct_span": 0.00233, "nl_pct_fs": 0.000233, "status": "PASS"},
  "notes": ""
}
```

---

## 7. Tare

- Formula (runtime): **`F = K·(raw − tare_raw)`** [N]. Identical to `(K·raw + B) − (K·tare_raw + B)`;
  B drops out, so B drift between calibration and test is irrelevant.
- Procedure: 10 s window (`tare.window_s`, PO example), same robust statistics as §6.1 →
  `tare_raw = robust mean`. Tare runs on the PC; the stream stays raw (recordings keep raw, K, tare_raw
  and the tare timestamp → any tare can be re-applied offline). Each tare is an EVENT row in the recording.
- Available from every tab (global toolbar, PO). If streaming is off it starts the stream and restores the
  previous state afterwards.
- **Invalid / refused** when: MOVING = 1 or a move ended < 1 s ago; E-stop/fault active; window
  `std > max(3·std_zero_cal, 50 counts)` (vibration, swinging); `|drift| > max(2·std, 20 counts)`;
  > 2 % outliers or any saturated sample; > 1 % frames lost; sequencer capture window active.
  Warning (not refusal): `|K·(tare_raw − raw_zero_cal)| > 10 % FS` ("large offset — specimen loaded?").
- After a successful tare the PC **re-sends the FW raw limits** (§4.4) — they depend on tare_raw.
- Zero check at the end of a test (optional): `drift_F = K·(raw_end − tare_raw)` reported with the test.

---

## 8. Sequencer (PC-side engine; FW executes single moves)

### 8.1 Step model

| Field | Type / unit | Meaning |
|---|---|---|
| `kind` | `travel_abs` / `travel_rel` / `load` / `hold` / `home` / `tare` / `mark` | |
| `target` | mm (travel) or N (load) | |
| `speed` | mm/s | motion speed (load steps: approach speed) |
| `accel` | mm/s² (0 = default) | "ramping" of the PO spec |
| `settle_s` | s | wait after the target is reached before capture |
| `capture_s` | s | VALID = 1 window for the report |
| `duration_s` | s | minimum step time (hold); also timeout for reaching a load target (0 = auto: 3×planned + 10 s) |
| `tol` | mm / N | completion tolerance (load steps) |
| `label` | str | report label |
Loops: `loop {start, end, count}` blocks (nestable 1 level); runtime counters `loop_iter` go into the
report. Step time line: `t_cmd` → motion until FW MOVE_DONE (or load reached) = `t_reached` →
settle → capture `[t_reached+settle, +capture]` → hold until `max(duration, settle+capture)` elapsed.

### 8.2 Generator wizards
- **Staircase** (travel or load): start, end, increment or n, speed, accel, settle, capture; options
  "up only / up-down" (hysteresis), "return to zero between steps".
- **Linear ramp**: x0 → x1 at constant speed (or force-rate `Ḟ` → speed `Ḟ/k_est`), capture during the ramp
  (continuous data, VALID = 1 while moving; report = stiffness fit, not steady-state).
- **Cyclic / triangle**: between `x_min`/`x_max` (or `F_min`/`F_max`), n cycles, speed, dwell.
- **Hold / creep–relaxation**: go to target, hold `duration`, capture throughout.
- **Return**: travel_abs 0 / home.
Preview chart (PO §15, x = travel, y = load): known coordinate per step from the target; the other
estimated with `k_est` (user-entered expected stiffness, default from the last run); during execution the
measured (x, F) trace is overlaid, the current step highlighted, live marker = latest sample.

### 8.3 Steady-state extraction (report)
For each executed step (and loop iteration) take samples with: `VALID = 1` **and** `MOVING = 0` **and**
`pos_um == pos_um at window start` (exact integer) **and** no ESTOP/LIMIT/FAULT/AFE_BAD flag **and**
`t ∈ [t_reached + settle_s, t_reached + settle_s + capture_s]` (device time). Stats per quantity
(raw, F, x): N, mean, std, min, max, SE(N_eff), drift (slope·T). `N < 0.8·capture_s·80` → `INCOMPLETE`;
load steps add `|mean − target| ≤ tol` → `ON_TARGET`. Raw data outside the window is kept in the
recording, only the report uses the window.

### 8.4 Load-target steps — feasibility and recommended control
Loop delay for a PC-side loop: HX711 period 12.5 ms + filter delay ≈ 25–50 ms + UART/USB 1–16 ms +
Windows scheduling 1–16 ms + command → **T_d ≈ 60–100 ms**. A continuous velocity loop with 60° phase
margin is limited to `ω_c ≤ (π/6)/T_d ≈ 5 rad/s` (T_d = 0.1 s) — usable for quasi-static tests but
jittery on Windows. FW-side closing in raw counts would be faster (T_d ≈ 40–60 ms) but adds FW complexity
and validation load. **Recommended (two-stage, M4):**
1. **Approach** with the FW primitive `MOVE_UNTIL_LOAD` (§2.1): direction from `motion.pull_dir` and the
   sign of `F_target − F`, speed = step speed, `raw_stop` = raw of `F_target − sgn·band` with
   `band = max(tol, k_est·v·0.065 s)` (overshoot budget §4.6). Deterministic, FW-timed (one sample).
2. **Trim** (PC, sampled integral control): wait until MOVE_DONE + 100 ms (filter settled), average the last
   4 samples (50 ms) → `F̄`; if `|F_target − F̄| > tol`: `Δx = Kp·(F_target − F̄)/k_est`, `Kp = 0.5`,
   `|Δx| ≤ trim.max_step_mm` (0.2 mm), speed `trim.v_mm_s` (0.2 mm/s); repeat ≤ `trim.max_iter` (10).
   Error recursion `e_{n+1} = (1 − Kp·k/k_est)·e_n` → stable for `0 < Kp·k/k_est < 2`, i.e. tolerates
   `k_est` underestimating the true stiffness up to 4× (Kp = 0.5); over-estimates only slow it down.
   `k_est`: OLS slope dF/dx over the approach (last ≥ 0.1 mm), clamped to [`k_min`, `k_max`], fallback the
   user value.
3. **Hold**: during `capture_s` **no trim** (position frozen → load relaxation is measured honestly);
   option `trim_during_hold` for force-controlled creep (then settle rule applies after each correction).
Guards: abort step on load moving opposite to the motion by > 5 % of target (slip/break), on `F` dropping
> 20 % from its running max while loading (`BREAK_DETECTED` event), timeout, travel bound per step.

### 8.5 Validity flag
PC sends `SET_VALID(1)` at capture start and `SET_VALID(0)` at capture end; FW applies it to the next
frame and returns the device `t_us` of application (S6 SET_VALID_FLAG semantics) → exact boundaries without
clock sync. FW **auto-clears** VALID on E-stop (any source), limit hit, load/AFE/step/driver fault, link
watchdog (EVENT VALID_CLEARED with cause). Manual mode: operator toggle; momentary sample = VALID for one
frame window chosen by the PC (or a 1 s mean of the stream, no FW involvement).

---

## 9. Data rate, timestamps, loss detection

- Frame (D-05 + Thrust_Stand framing, Integrator decides): `A5 5A | TYPE | SEQ | LEN(2) | payload_ver(1)
  flags(1) t_us(4) afe_raw(4) pos_um(4) | CRC(2)` = **22 bytes** = 220 bit-times at 8N1 → 239 µs on the wire.
  At 80 Hz: **1 760 B/s = 1.9 %** of the 92 160 B/s link capacity → ample headroom for STATUS/EVENT frames
  (or 8-byte timestamps).
- USART at 921 600 on APB1 45 MHz: USARTDIV 3.0518 → 3 + 1/16 → **918 367 Bd (−0.35 %)**, within tolerance
  (VERIFIED arithmetic; ST-LINK VCP side → R3).
- **Timestamp**: `t_us` u32 from TIM5 (1 MHz), captured at the HX711 DOUT-falling (data-ready) EXTI.
  Wraps after **4 294.97 s = 71.6 min** → PC unwraps (`t < t_prev ⇒ +2³²`), unambiguous while frames
  arrive at least every 71 min. ms resolution (49.7-day wrap) is rejected: ±0.5 ms on a 12.5 ms period is
  4 % jitter in speed/dF/dt. Clock source: stm32duino uses **HSI** (S4) — ±1 % factory (RM0390, ASSUMED
  value), so device time/speeds may be off by up to ~1 %; recommend overriding `SystemClock_Config` to
  HSE-bypass 8 MHz from the ST-LINK MCO (ASSUMED available on NUCLEO-F446RE solder-bridge default → R1/R2).
- HX711 data rate is set by its own oscillator (80 SPS nominal, tolerance UNKNOWN → R2); frames are
  therefore asynchronous to the MCU clock and **`t_us` differences, not 12.5 ms constants, are the time base**.
- **Loss detection**: frame `SEQ` (u8, +1 per DATA frame, also incremented when a frame is dropped in FW,
  with OVERRUN flag in the next frame — S6 §2.2); `Δt_us > 1.5 × median period` → missed HX711 conversion.
  u8 wraps every 3.2 s; ambiguity of 256-frame gaps is resolved by `t_us`.
- PC time ↔ device time: only needed for PC events (button presses); keep (t_pc_rx, t_us) pairs and fit
  `t_pc = a + b·t_us` on the lower envelope of latencies. VALID boundaries need no sync (§8.5).
- Sample alignment: `pos_um` is latched at data-ready (end of conversion); the force is a filtered value
  delayed by ≈ τ_g (≈ 25 ms, ASSUMED; measure with a step test in M2/HIL). At v = 1 mm/s the skew is 25 µm;
  optional derived channel shifts x by `−τ_g·v` (§10).

---

## 10. Derived channels (plots, report)

| Channel | Formula | Validity / note |
|---|---|---|
| Force N | `K·(raw − tare_raw)` | cal + tare present, not saturated |
| Force kgf | `F/G0` | |
| Raw counts | as received | always available (no cal) |
| Travel mm (machine) | `pos_um/1000` | HOMED for absolute meaning |
| Travel mm (test) | `pos_um/1000 − x_zero` | PC travel zero |
| Specimen deflection | `x − C_m·F` (C_m machine compliance mm/N, calibrated with a rigid dummy specimen, default 0) | optional |
| Speed mm/s | central difference of x over ±2 samples using `t_us` | commanded profile speed (exact) |
| Force rate N/s | Savitzky–Golay derivative (9 samples, order 2) or OLS slope over 100 ms | |
| Stiffness N/mm (tangent) | OLS slope dF/dx over a sliding window with `|Δx| ≥ 0.05 mm` | NaN when the axis stands still (no division by noise) |
| Stiffness N/mm (secant) | `F / x` from test zero | NaN for `|x| < 0.05 mm` |
| Work / energy N·mm (= mJ) | trapezoid `∫F dx` | cyclic tests: loop area = hysteresis energy |
| Peak force, force at break | running max; break = drop > 20 % from running max | event marker |
| Noise | rolling std of raw (1 s) in counts and N | display quality indicator |
| Sample rate / lost frames | `1/Δt_us`, SEQ gaps | link health |
| Optional 3-point bend (ASTM D790/ISO 178; span L, width b, thickness h) | `σ = 3FL/(2bh²)`, `ε = 6δh/L²`, `E_f = L³·m/(4bh³)` (m = slope N/mm) | only if geometry entered; units N, mm → MPa |
Display filters (EMA) are for display only; statistics use unfiltered data (S6 §10).

---

## 11. Models needed for host testing (D-07: hardware later)

| Model | Content | Used by |
|---|---|---|
| Motion / FW host twin | the same pure C ramp, planner, homing state machine, step counting compiled for host; virtual time in ticks | FW unit tests (Validator E), SW simulator |
| Driver | ideal step follower + optional first-order lag (τ ≈ 5 ms) for following error; ALM injection | E-stop/ALM scenarios |
| Switches | START at x = −`home.offset` − 0.5 mm, END at axis length; bounce (3 × 0.2–2 ms), broken-wire (stuck open) | homing, debounce, wiring-fault tests |
| HX711 | data-ready at 80 SPS × (1 + ε), ε ≈ ±2 %; output = filtered (4-sample moving average as sinc approximation) of `offset + S·F(t)`, S ≈ 2190 counts/N; Gaussian noise σ = 45 counts; spikes (p = 1e-4); saturation at rails; temperature drift (e.g. +20 counts/min); missed conversions | calibration/tare/limit tests, report extraction |
| Specimen | `F = k·(x − x_c)` for x beyond contact; bilinear (k1 → k2 after yield), relaxation (2 % with τ = 30 s at constant x), break at `F_break` (F → 0), hysteresis for cycles; machine compliance C_m | load-target control, break detection, stiffness |
| Link | latency 1–16 ms, frame loss rate, CRC errors, PC heartbeat loss | watchdog, SEQ gap, unwrap |
All model parameters live in a YAML scenario file of the simulator (Integrator).

---

## 12. Test vectors (pytest-ready)

All values were generated and re-checked on 2026-10-03 by running this block against a pure-Python
reference implementation of the formulas below (Python 3.14, 22/22 test functions pass; reference kept in
the Researcher scratchpad, not in the repo). Tolerances `rel=1e-9` for closed-form values unless stated.
Function names are proposals for
`03_SW/src/bend_stand/calc/` (Implementer B) and the FW pure layer (motion vectors, same numbers).

```python
import math, statistics as st, pytest
G0 = 9.80665

# TV-U units
def test_units():
    assert 10.0 * G0 == pytest.approx(98.0665, rel=1e-12)
    assert 1.0 / G0 == pytest.approx(0.10197162129779283, rel=1e-12)      # kgf per N
    assert 2e-3 / 200 * 2**23 * 256 == pytest.approx(21474.83648, rel=1e-12) # counts/kg @2 mV/V, gain 128

# TV-LC load calibration, 3 points (PASS)
RAW = [125000.0, 339760.0, 554490.0]; M = [0.0, 10.0, 20.0]
def test_load_cal_pass():
    c = load_calibration(RAW, M)            # -> K [N/count], B [N], residuals [N], r2, nl_pct_span, nl_pct_fs, status
    assert c.K == pytest.approx(0.00045666488086106374, rel=1e-9)
    assert c.B == pytest.approx(-57.085393272546426, rel=1e-9)
    assert c.residuals == pytest.approx([0.0022831649134573695, -0.004566648808591367,
                                         0.002283483895155314], abs=1e-9)
    assert c.r2 == pytest.approx(0.9999999983736457, abs=1e-12)
    assert c.nl_pct_span == pytest.approx(0.0023283429145484784, rel=1e-6)
    assert c.nl_pct_fs == pytest.approx(0.00023283429145484785, rel=1e-6)   # FS = 200 kg * G0
    assert c.status == "PASS"
    assert c.K / G0 == pytest.approx(4.656685829116607e-05, rel=1e-9)      # kgf/count
    assert 1 / c.K == pytest.approx(2189.7895851208254, rel=1e-9)          # counts/N
def test_load_cal_warn():
    c = load_calibration([125000.0, 339760.0, 560000.0], M)
    assert c.K == pytest.approx(0.00045085660914376133, rel=1e-9)
    assert c.nl_pct_span == pytest.approx(0.41990115858589555, rel=1e-6)
    assert c.r2 == pytest.approx(0.9999471021069184, abs=1e-12)
    assert c.status == "WARN"
def test_load_cal_fail():
    c = load_calibration([125000.0, 339760.0, 566000.0], M)
    assert c.K == pytest.approx(0.00044464559345022724, rel=1e-9)
    assert c.nl_pct_span == pytest.approx(0.8675289068826835, rel=1e-6)
    assert c.r2 == pytest.approx(0.9997741670782083, abs=1e-12)            # R2 still "high" -> useless criterion
    assert c.status == "FAIL"
def test_load_cal_2pt():
    c = load_calibration([125000.0, 554490.0], [0.0, 20.0])
    assert c.K == pytest.approx(0.00045666488160376256, rel=1e-9)
    assert c.B == pytest.approx(-57.083110200470315, rel=1e-9)
    assert c.status == "UNVERIFIED_LINEARITY"
def test_load_cal_degenerate():
    with pytest.raises(ValueError):
        load_calibration([5.0, 5.0, 5.0], M)

# TV-RS robust window statistics (MAD k=5) and AR(1) standard error
def test_robust_stats():
    X = [100.0, 102.0, 98.0, 101.0, 99.0, 100.0, 5000.0, 100.0, 101.0, 99.0]
    r = robust_window_stats(X, k=5.0)       # median, mad, sigma_mad, mean, std, n_used, n_rejected
    assert (r.median, r.mad) == (100.0, 1.0)
    assert r.sigma_mad == pytest.approx(1.4826)
    assert (r.n_used, r.n_rejected) == (9, 1)
    assert r.mean == pytest.approx(100.0) and r.std == pytest.approx(1.224744871391589)
def test_se_ar1():
    Y = [10.0, 12.0, 11.0, 13.0, 12.0, 14.0, 13.0, 15.0]
    rho1, n_eff, se = se_ar1(Y)
    assert rho1 == pytest.approx(0.125) and n_eff == pytest.approx(6.222222222222222)
    assert se == pytest.approx(0.6428571428571429)
def test_drift():
    t = [k * 0.0125 for k in range(10)]; x = [1000.0 + 2 * k for k in range(10)]
    assert drift_slope(t, x) == pytest.approx(160.0)                       # counts/s
    assert drift_slope(t, x) * 10.0 == pytest.approx(1600.0)               # over a 10 s window
    assert math.sqrt(12 / 800) == pytest.approx(0.1224744871391589)        # white-noise drift sd / std

# TV-T tare and runtime force, FW raw limits
K = 0.00045666488086106374; TARE = 125430.0
def test_tare_runtime():
    assert force_n(339760.0, K, TARE) == pytest.approx(97.8769839149518, rel=1e-9)
    assert force_n(339760.0, K, TARE) / G0 == pytest.approx(9.980674737545625, rel=1e-9)
    assert force_n(100000.0, K, TARE) == pytest.approx(-11.61298792029685, rel=1e-9)
    assert force_n(TARE, K, TARE) == 0.0
def test_fw_raw_limits():
    # returns (raw_min, raw_max); ceil/floor = rounding toward the tare (conservative)
    assert fw_raw_limits(f_hi=1500.0, f_lo=-1500.0, k=K, tare_raw=TARE) == (-3159254, 3410114)
    assert fw_raw_limits(f_hi=1500.0, f_lo=-500.0, k=K, tare_raw=TARE) == (-969464, 3410114)
    assert fw_raw_limits(f_hi=1500.0, f_lo=-500.0, k=-K, tare_raw=TARE) == (-3159254, 1220324)  # negative K: sides swap

# TV-TC travel calibration (spm0 = 640, 10 mm then 50 mm, totals)
def test_travel_cal():
    n1, spm1 = travel_cal_step1(spm_old=640.0, cmd_mm=10.0, meas_mm=10.05)
    assert n1 == 6400 and spm1 == pytest.approx(636.8159203980099, rel=1e-12)
    n2, spm2, spm2_inc, consistency = travel_cal_step2(spm1=spm1, n1=n1, cmd_mm=50.0, meas_total_mm=60.12, meas1_mm=10.05)
    assert n2 == 31841                                                    # round(31840.796...)
    assert spm2 == pytest.approx(636.0778443113772, rel=1e-12)
    assert spm2_inc == pytest.approx(635.929698422209, rel=1e-12)
    assert consistency == pytest.approx(-0.001391645446374934, rel=1e-9)  # |..| < 0.5 % -> OK
def test_um_steps():
    spm = 636.0778443113772
    assert [um_to_steps(u, spm) for u in (12345, -500, 100)] == [7852, -318, 64]
    assert [steps_to_um(s, spm) for s in (7852, -318, 64)] == [12344, -500, 101]

# TV-M motion: f_tick = 90 MHz, 640 steps/mm, a = 100 mm/s^2, v = 20 mm/s
F_TICK = 90e6; A = 64000.0; V = 12800.0
def test_ramp_constants():
    C = F_TICK * math.sqrt(2 / A)
    assert C == pytest.approx(503115.2949374527, rel=1e-12)
    assert 0.676 * C == pytest.approx(340105.9393777181, rel=1e-12)       # AVR446 c0 (reference only)
    assert F_TICK / V == 7031.25                                           # c_min (exact, used with carry)
def test_ramp_periods():                    # exact sqrt ramp, max rule, fractional carry
    p = ramp_periods(n_steps=64000, f_tick=F_TICK, v=V, a=A, d=A)
    assert p[:5] == [503115, 208397, 159909, 134809, 118770]
    assert sum(p) == 468000000                                             # 5.2 s exactly
    assert sum(1 for c in p if c > 7032) == 2559                           # ramp periods (1280 acc + 1279 dec)
    assert (p.count(7031), p.count(7032)) == (46080, 15361)                # cruise 7031.25 -> 3x7031 + 1x7032
    assert p[30000:30008] == [7031, 7031, 7031, 7032, 7031, 7031, 7031, 7032]
    p = ramp_periods(n_steps=1000, f_tick=F_TICK, v=V, a=A, d=A)           # triangle
    assert sum(p) == 22500000 and p[-3:] == [159909, 208397, 503116] and min(p) == 11255
    p = ramp_periods(n_steps=1000, f_tick=F_TICK, v=V, a=A, d=2 * A)       # asymmetric triangle
    assert sum(p) == 19485570 and p[-1] == 355756 and min(p) == 9744
def test_avr446_reference():                 # Stefan/AVR446 integer recursion with remainder, first 5
    assert avr446_periods(F_TICK, A, 5) == [340105, 204063, 158716, 134298, 118499]
def test_planner():
    t = plan_trapezoid(64000, V, A, A)
    assert (t["kind"], t["n_acc"], t["n_cruise"], t["n_dec"]) == ("trap", 1280, 61440, 1280)
    assert t["v_peak"] == 12800.0 and t["t"] == pytest.approx(5.2)
    t = plan_trapezoid(1000, V, A, 2 * A)
    assert (t["kind"], t["n_acc"], t["n_dec"]) == ("tri", 667, 333)
    assert t["v_peak"] == pytest.approx(9237.604307034011) and t["t"] == pytest.approx(0.21650635094610965)
    assert V * V / (2 * A) / 640 == pytest.approx(2.0)                    # controlled-stop distance, mm

# TV-L link and timestamps
def test_link_budget():
    frame = 2 + 1 + 1 + 2 + (1 + 1 + 4 + 4 + 4) + 2
    assert frame == 22 and frame * 80 == 1760
    assert frame * 80 / (921600 / 10) == pytest.approx(0.019097222222222224)
    assert 2**32 / 1e6 / 60 == pytest.approx(71.58278826666667)
def test_unwrap():
    assert unwrap_us([4294967000, 4294967290, 200, 12700]) == [4294967000, 4294967290, 4294967496, 4294979996]

# TV-SS steady-state extraction; frame = (t_us, flags, pos_um, raw); bit0 VALID, bit1 MOVING
FRAMES = [(0, 2, 4000, 300000), (12500, 2, 4400, 300030), (25000, 2, 4800, 300060),
          (37500, 0, 5000, 300000), (50000, 0, 5000, 300030), (62500, 1, 5000, 300060),
          (75000, 1, 5000, 300000), (87500, 1, 5000, 300030), (100000, 1, 5000, 300060),
          (112500, 1, 5000, 300090), (125000, 1, 5000, 300030), (137500, 1, 5000, 300060)]
def test_steady_state():
    s = steady_state(FRAMES, pos_um=5000, t_from_us=0, t_to_us=10**9)     # VALID & !MOVING & pos == 5000
    assert s.n == 7 and (s.min, s.max) == (300000, 300090)
    assert s.mean == pytest.approx(300047.14285714284) and s.std == pytest.approx(29.277002188455995)
    F = [K * (r - TARE) for (t, f, p, r) in FRAMES if (f & 1) and not (f & 2)]
    assert sum(F) / len(F) == pytest.approx(79.7415167391565, rel=1e-9)
    assert st.stdev(F) == pytest.approx(0.013369778716359415, rel=1e-6)

# TV-C load trim controller (k_true 50 N/mm, k_est 40, Kp 0.5, target 200 N, start 3 mm)
def test_trim():
    x, seq = 3.0, []
    for _ in range(5):
        F = 50.0 * x; seq.append((x, F)); x = x + trim_step(F, f_target=200.0, k_est=40.0, kp=0.5, max_step=10.0)
    assert seq[1] == (3.625, 181.25) and seq[4] == pytest.approx((3.980224609375, 199.01123046875))
    assert 1 - 0.5 * 50.0 / 40.0 == pytest.approx(0.375)                   # error ratio per iteration
    assert 50.0 * 2.0 * 0.05 == pytest.approx(5.0)                         # overshoot k*v*t, N

# TV-D derived channels
def test_derived():
    xs = [0.0, 0.1, 0.2, 0.3, 0.4]; Fs = [0.0, 5.2, 9.8, 15.1, 20.0]
    k, b = stiffness_ols(xs, Fs)
    assert k == pytest.approx(49.9) and b == pytest.approx(0.04, abs=1e-12)
    assert work_trapz(xs, Fs) == pytest.approx(4.01)                       # N*mm
    L, bw, h = 100.0, 20.0, 5.0                                           # 3-point bend
    assert 3 * 200.0 * L / (2 * bw * h * h) == pytest.approx(60.0)        # MPa
    assert 6 * 2.0 * h / (L * L) == pytest.approx(0.006)
    assert L**3 * 50.0 / (4 * bw * h**3) == pytest.approx(5000.0)         # MPa
```
Implementation notes for the vectors:
- `ramp_periods` = §1.5 exactly: for k = 1…N, r = N−k+1, `c = max(Ca/(√k+√(k−1)), f/v, Cd/(√r+√(r−1)))`,
  `acc += c; ci = int(acc); acc -= ci` (double precision on the host; the FW float32 version must match
  every period within ±1 tick and the sum within ±N/1000 ticks — FW tolerance vector).
- `avr446_periods`: `c = int(0.676·Ca)`, then `n += 1; num = 2c + rest; c -= num // (4n+1); rest = num % (4n+1)`.
- `fw_raw_limits(f_hi, f_lo, k, tare_raw)`: `a = tare + f_hi/k`, `b = tare + f_lo/k`, return
  `(ceil(min(a, b)), floor(max(a, b)))` (§4.4). Unrounded values of the vectors: 3410114.3777,
  −969464.7926, −3159254.3777, 1220324.7926.
- `load_calibration` thresholds: PASS ≤ 0.1 %, WARN ≤ 0.5 %, FAIL above (`nl_pct_span`); FS = 200·G0 N.
- `steady_state` uses population of frames meeting §8.3; `std` with ddof = 1.

---

## 13. Findings for other roles

| ID | To | Finding |
|---|---|---|
| F-R4-01 | R1 / Impl A | Stefan TIM2 runs at 84 MHz/10 = 8.4 MHz while the code assumes 10 MHz → speeds ×0.84 (§1.9). |
| F-R4-02 | R1 / Impl A | Stefan PWM mode 1 + stop in update ISR can emit an uncounted runt pulse per stop (§1.9). Use PWM mode 2. |
| F-R4-03 | R2 | Confirm HBS86H PUL/DIR/ENA timing, active edge, ENA logic (opto off = enabled?), ALM output type/levels and reset method, enable-to-ready delay. |
| F-R4-04 | R2 | Confirm Keli DEF 200 kg sensitivity (2 mV/V?), safe overload, tension/compression symmetry, deflection at FS; HX711 80 SPS settling time/oscillator tolerance and noise. |
| F-R4-05 | Impl A / R3 | stm32duino default clock is HSI-based (S4); consider HSE bypass from ST-LINK MCO for timing accuracy; own TIM IRQs via `-DHAL_TIM_MODULE_ONLY`. |
| F-R4-06 | Integrator | Data frame proposal (§2.3, §9): 14-byte payload incl. `pos_um` (commanded position at sample time) and flag bits; SEQ-gap + OVERRUN loss detection; SET_VALID returns device time. |
| F-R4-07 | Integrator | Simulator/host-twin model list §11 (D-07). |

## 14. Open questions for the product owner (with recommended defaults)

| ID | Question | Recommended default |
|---|---|---|
| Q-R4-01 | Must the NC button protect **persons** (IEC 60204-1 requires stop category 0/1 for emergency stops; D-10 holds power)? | Treat the NC button as a **machine stop (hold)** per D-10 and add a separate hardwired power-cut E-stop if persons can reach the crosshead. |
| Q-R4-02 | What should happen to the motor on an MCU reset/power loss of the board during a loaded test (depends on ENA wiring)? | Wire ENA so that a reset keeps the driver **holding** (consistent with D-10); FW boots "disabled" logically and requires homing before absolute moves. |
| Q-R4-03 | Pause/Break: hard stop requiring a clear (software E-stop) or pause with resume of the sequence? | Software E-stop: immediate stop, sequence aborted, explicit clear. |
| Q-R4-04 | Max load allowed during homing; is homing done with the specimen mounted? | Homing only with < 5 % FS load; otherwise refuse unless operator confirms. |
| Q-R4-05 | Measuring instrument for travel calibration (caliper, dial indicator range)? | Caliper/height gauge ±0.02 mm; wizard asks for the **total** 60 mm distance. |
| Q-R4-06 | Push (compression) calibration needed, or is the pull calibration applied to both signs? | Use pull K for both, mark push as "tension-calibrated". |
| Q-R4-07 | Which known weights are available (masses, accuracy class) and what max force do tests reach? | ≥ 10 kg and 20 kg (M1 class or better); warn when test forces exceed 3× the largest weight. |
| Q-R4-08 | Axis length, lead/pitch and driver pulses/rev (steps/mm nominal), max needed speed? | 640 steps/mm, 400 mm travel, 20 mm/s max, 100 mm/s² until mechanics are known. |
| Q-R4-09 | Linearity acceptance limits (PASS ≤ 0.1 %, WARN ≤ 0.5 % of calibrated span) acceptable? | Yes as defaults, configurable. |
| Q-R4-10 | Load-target steps: is ±tol after the approach + trim (no active force control during capture) acceptable, or is true force control (creep at constant force) required? | Approach + trim, position frozen during capture; force-hold as an option in a later milestone. |
| Q-R4-11 | Link loss while moving: stop (recommended) or let a position move finish? | Controlled stop, keep holding. |
| Q-R4-12 | Idle auto-disable when unloaded (heat) — wanted, and after how long? | 600 s, only when |F| < 2 % FS; never under load. |
| Q-R4-13 | Should the data frame carry a 64-bit or 32-bit µs timestamp (tests > 71 min are fine with PC unwrapping)? | 32-bit µs + PC unwrap. |
| Q-R4-14 | Specimen geometry / 3-point-bend stress–strain outputs wanted in the report? | Optional fields; off by default. |
