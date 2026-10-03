# Bird Bend Stand — NUCLEO-F446RE pin, clock, timer, DMA and interrupt allocation

| Item | Value |
|---|---|
| Doc | `01_HW/pinout.md` |
| Version | **0.3** — final P1: ALM polled instead of EXTI (OBS-P1-10). v0.2: aligned to SRS v0.3 / ICD v0.3 / D-29 / D-30 (DRV_PWR required, START-only homing, ALM scope, TX DMA one frame per transfer, CRIT_DATA). v0.1: The pin map follows **R5 §6.1** (checked by R5 against the F446 datasheet DocID027107 Rev 6, Table 10) and is **accepted by the PO (D-28, 2026-10-03)**: DOUT → PB4, RATE → PB5, END → PC1, STOP → PC7 (NC), PAUSE → PB6 (NO), DRV_PWR → PA7, TRIP → PB9 (provisioned, unused in release 1), PA6 reserved. |
| Date | 2026-10-03 |
| Owner | Implementer A (FW) |
| Binding inputs | SRS v0.3 (SYS-007, FW-SW-005, SAF-FW-024/025/026, FW-PLT-001/002, FW-SW-001…004, FW-AFE-001/002, SAF-FW-002/005/007/018/019, NFR-007), DECISIONS D-08, D-09, D-11, D-13, D-16 (PFDE HBS86H clone), D-17, D-18, D-21, D-22, D-26, D-27, **D-28**, **D-29** (b, c), D-30, R1 §1–2, §6, §9, R2 §1.3–1.7, §5, R4 §1.4, §1.8, R5 §1.3, §5.2, §6 |
| Board | NUCLEO-F446RE (MB1136), Stefan's board (D-22). Revision and solder-bridge state not yet inspected (§5, Q-HW-01) |
| Related | `01_HW/wiring.md` (electrical circuits), `02_FW/docs/FW_design.md` (how the FW uses these resources) |

Conventions
- **5 V tol.**: FT / FTf = 5 V tolerant as digital I/O; TC = standard 3.3 V I/O, **not** 5 V tolerant (R5 §6 finding 2: PA4/PA5 are TC; R1/R2 wrote "TTa" — same consequence).
- Header names: Arduino `A0…A5`, `D0…D15` (VERIFIED against stm32duino `variant_NUCLEO_F446RE.h:18-74`); connector/pin numbers (`CN5…CN10-n`) from UM1724 as quoted in R1/R5 — **ASSUMED, check against the silkscreen before wiring**.
- Mode column: GPIO `MODER` (in / out / AF / analog), `OTYPER` (PP/OD), `OSPEEDR` (low = 2 MHz class, slew-limited), `PUPDR` (internal pull: none / PU), and the level written to `ODR` **before** the pin becomes an output ("init").
- Input polarity "NC to GND + pull-up": contact closed = pin low = normal; contact open **or wire broken** = pin high = **active** (fail-safe, SAF-FW-007).
- Status of each row: "R5/D-28" = pin moved by R5 vs Stefan/R1 and accepted by the PO in D-28.

---

## 1. Pin allocation

### 1.1 Used pins

| Pin | Header (Arduino / morpho) | Signal | Dir | Peripheral / AF | EXTI line → vector, NVIC level | Mode / pull / speed (init) | Active level | 5 V tol. | Origin / rationale | Req. |
|---|---|---|---|---|---|---|---|---|---|---|
| **PA0** | A0 · CN8-1 / CN7-28 | **PUL** (step) → HBS86H PUL+ | out | **TIM2_CH1, AF1** (32-bit, 90 MHz, PWM mode 2) | – (TIM2 update IRQ, level 2) | reset Hi-Z until params are loaded, then AF PP, **low speed**, no pull; TIM2 OC1 forced inactive before the AF switch | pulse = LED current; polarity `motion.pul_invert` (CC1P) | FT | Stefan (`hw_stm32.hpp:12`), R1 §9, R4 §1.4, R5 §6.1 | FW-MOT-001, SAF-FW-004 |
| **PA1** | A1 · CN8-2 / CN7-30 | **DIR** → HBS86H DIR+ | out | GPIO | – | Hi-Z until params loaded, then out PP, low speed, no pull, init low | polarity `motion.dir_invert`; changed only while TIM2 is stopped | FT | Stefan, R1 §9 | FW-MOT-001 |
| **PA4** | A2 · CN8-3 / CN7-32 | **ENA** → HBS86H ENA+ | out | GPIO | – | Hi-Z (= no LED current = driver enabled/holding, D-13) until params loaded; then out **PP only** (never OD to 5 V), low speed; level per boot rule (FW_design §3.2) | LED current = **disabled** (`motion.ena_invert` = 0, R2 §1.5) | **TC (no)** | Stefan (intended, never configured there, R1 B-01), R5 §6.1 | FW-MOT-008, SAF-FW-005, SAF-FW-018 |
| **PA8** | D7 · CN9-8 / CN10-23 | **ALM** ← HBS86H ALM+ (OC opto) | in | GPIO | **polled 1 kHz** in the control tick (EXTI line 8 **not enabled** since v0.3, OBS-P1-10: a chattering ALM must not load level 1; report + start-block only, D-16) | in, internal PU on (backup) + external 4.7 kΩ PU, 1 kΩ/1 nF (wiring §3) | Leadshine-type default: low = OK and powered; high = alarm / unpowered / wire break; `drv.alm_active_level` (polarity of the PFDE clone to be confirmed at the HW gate). **In the current open-loop motor setting (D-27, SW7/SW8 off/off) no following-error alarm exists**; ALM then reports only over-current/over-voltage/unpowered. The FW never uses ALM as a step-loss indicator; its only reaction is the start-block for new motion starts (SAF-FW-026) | FT | R1 §9, R5 §6.1 | FW-SW-004, SAF-FW-026 |
| **PA9** | D8 · CN5-1 / CN10-21 | **PEND** ← HBS86H PEND+ (OC opto) | in | GPIO | (line 9 unused) — **polled 1 kHz** in the control tick | in, internal PU + external 4.7 kΩ PU, 1 kΩ/1 nF | high impedance (pin high) = in position; `drv.pend_active_level`; meaningful only with ALM = OK (R5 §3.1) | FT | R1 §9, R5 §6.1 | FW-SW-004 |
| **PA10** | D2 · CN9-3 / CN10-33 | **E-stop sense** ← E-stop NC2 contact (D-11) | in | GPIO | **10 → EXTI15_10, level 0 — alone on this vector** (both edges) | in, internal PU (7–14 kΩ on PA10, OTG_FS_ID, R5 §3.1) + external 1 kΩ PU, 1 kΩ/4.7 nF (τ ≤ 10 µs) | NC to GND: **high = E-stop pressed or wire broken** (fixed polarity, SAF-FW-007) | FT | R1 §9 (replaces Stefan's B1), R5 §6.1 | SAF-FW-005/007, FW-SW-002 |
| **PB0** | A3 · CN8-4 / CN7-34 | **START limit** (home end, D-18) | in | GPIO | **0 → EXTI0 (own vector), level 1** (both edges) | in, internal PU + external 1 kΩ PU, 1 kΩ/47 nF (τ ≈ 94 µs) | NC to GND: high = hit or wire broken (fixed polarity) | FT | Stefan MIN (`hw_stm32.hpp:15`), R1 §9; **the only home reference** (D-29 b) | FW-SW-001, SAF-FW-007/013, FW-HOM-001 |
| **PC1** | **A4** · CN8-5 / CN7-36 | **END limit** | in | GPIO | **1 → EXTI1 (own vector), level 1** (both edges) | as PB0 | as PB0 | FT | **R5/D-28**: R5 moved END from PB1 (morpho only) to A4 so all signals are on the Arduino headers (shield option). **PB1 (CN10-24) is the equivalent fallback** for the current hand wiring (also EXTI1) — selectable by the build flag `-DPIN_END_PB1` while the hand wiring still uses PB1 | FW-SW-001, SAF-FW-013/014 |
| **PC7** | **D9** · CN5-2 / CN10-19 | **STOP/BREAK button** (NC, D-26) | in | GPIO | **7 → EXTI9_5, level 1** (both edges) | in, internal PU + external 1 kΩ PU, 1 kΩ/47 nF | NC to GND: high = pressed or wire broken; `io.stop_active_level` (default open-active, A-08) | FTf | **new, R5/D-28** (R5 §6.1) | FW-SW-003, SAF-FW-022 |
| **PB6** | **D10** · CN5-3 / CN10-17 | **PAUSE button** (NO, D-14/D-26) | in | GPIO | **6 → EXTI9_5, level 1** (both edges) | in, internal PU + external 4.7 kΩ PU, 1 kΩ/220 nF (≈ 1.3 ms) | NO to GND: **low = pressed**; `io.pause_active_level` (default closed-active, A-08) | FT | **new, R5/D-28** (R5 §6.1) | FW-SW-003, SAF-FW-023 |
| **PB4** | **D5** · CN9-6 / CN10-27 | **HX711 DOUT** | in | GPIO (after reset: AF0 = NJTRST with internal PU) | **4 → EXTI4 (own vector), level 3** (falling) | in, no pull (HX711 drives push-pull) | low = data ready | FT | **R5/D-28**: R5 moved DOUT from PB5 to PB4 so the AFE interrupt has its own vector and does not share EXTI9_5 with STOP/PAUSE/ALM (R5 §6 finding 6). **NJTRST release VERIFIED** (see §1.4) | FW-AFE-001/005, FW-TIM-001 |
| **PB10** | D6 · CN9-7 / CN10-25 | **HX711 PD_SCK** | out | GPIO | – | out PP, medium speed, no pull, **init low** (ODR first; high > 60 µs = HX711 power-down) | high = clock | FT | R1 §9 (new vs Stefan) | FW-AFE-001/003 |
| **PB5** | **D4** · CN9-5 / CN10-29 | **HX711 RATE** (D-21) | out | GPIO | – | out PP, low speed, no pull; ODR = `afe.rate_sps` (80 → high) written before MODER; external 10 kΩ PU to HX711 DVDD keeps 80 SPS during MCU reset (R5 §5.2) | high = 80 SPS, low = 10 SPS | FT | **R5/D-28** (R5 swapped RATE/DOUT vs R1) | FW-AFE-002 |
| **PA7** | **D11** · CN5-4 / CN10-15 | **DRV_PWR sense** (**required**; `drv.pwr_sense_enable` = false for bring-up only) ← contactor K1 aux NO (R5 §1.3 A) | in | GPIO | (line 7 used by PC7) — **polled 1 kHz, 20 ms stability filter** | in, internal PU + external 1 kΩ PU, 1 kΩ/1 µF (≈ 2 ms) | closed = low = **driver powered** (fixed polarity, no parameter) | FT | **new, R5/D-28** (R5 §1.5); D-29 c: power lost (any cause) → stop, ENA disabled, NOT_ENABLED, HOMED cleared ≤ 25 ms; E-stop open + power present > `drv.k1_weld_ms` → K1_WELDED; "driver power present" for the ALM start-block | FW-SW-005, SAF-FW-024/025/026 |
| **PB9** | **D14** · CN5-9 / CN10-5 | **TRIP relay output** (provision only, Q-R5-04) | out | GPIO | – | out PP, low speed, **init low** (= relay off = K1 hold path closed); external base pull-down on the relay driver | high = trip (can only *remove* driver power) | FT | **new, R5/D-28** — provisioned, never driven high in release 1 | – |
| **PA5** | D13 · CN5-6 / CN10-11 | Status LED = on-board **LD2** (green) | out | GPIO | – | out PP, low speed, init low | high = on (FW_design §5.12 blink codes) | TC | Stefan (`app.cpp:78`) | – |
| **PA2** | (D1, disconnected: SB62 OFF) · CN10-35 | **USART2_TX** → ST-LINK VCP | out | USART2, **AF7**; DMA1 Stream6 Ch4 | – (DMA1_Stream6 / USART2 IRQs, level 5) | AF PP, high speed, no pull | – | FT | Stefan, R1 §9 | IF-002 |
| **PA3** | (D0, disconnected: SB63 OFF) · CN10-37 | **USART2_RX** ← ST-LINK VCP | in | USART2, **AF7**; DMA1 Stream5 Ch4 (circular) | – | AF, internal PU (idle high if VCP absent) | – | FT | Stefan, R1 §9 | IF-002 |
| **PA13 / PA14** | CN7-13 / CN7-15 | SWDIO / SWCLK (ST-LINK) | – | SYS AF0 | – | **never written** by the FW (masked RMW of `GPIOA->MODER/PUPDR`) | – | FT | Stefan | – |
| **PB3** | D3 · CN9-4 / CN10-31 | SWO (trace, optional) | out | SYS AF0 | – | left at reset AF0; free for SWO only | – | FT | Stefan | – |
| **PH0 / PH1** | CN7-29 / CN7-31 | OSC_IN = **8 MHz MCO from the ST-LINK (HSE bypass)** / OSC_OUT (unused in bypass) | in | RCC | – | reserved | – | FT | R2 §5.1, A-11 (SB state §5) | FW-PLT-002 |

### 1.2 Reserved, spare and debug pins

| Pin | Header | Status | Notes |
|---|---|---|---|
| **PA6** | D12 · CN5-5 / CN10-13 | **reserved** (R5 §6 finding 5) | carries **TIM1_BKIN / TIM8_BKIN**. Kept free for a later hardware pulse-block option (PUL moved to TIM8_CH1 on PC6 with the E-stop/STOP sense on BKIN). Configured analog. |
| **PC13** | CN7-23 (B1 USER) | **not used** | Must **not** get an EXTI: line 13 shares the EXTI15_10 vector with the E-stop (level 0). Debug builds may *poll* B1 as an alternative PAUSE (R5 §6.1 "PAUSE alt."); never in release. |
| PB1 | CN10-24 | spare / **END fallback** | see PC1 row. |
| PC0 | A5 · CN8-6 / CN7-38 | spare | EXTI0 belongs to PB0 → no interrupt on PC0. |
| PB8 | D15 · CN5-10 / CN10-3 | spare | SB52 must stay OFF (otherwise PB8 is also on A5). |
| PC8 / PC9 | CN10-2 / CN10-1 | **debug markers** (build flag `-DFW_DEBUG_PINS=1` only) | DBG0 = high during the TIM2 step ISR; DBG1 = high during the HX711 read / BASEPRI sections (scope timing for NFR-007, SYS-009). Out PP, high speed, init low. |
| PA15 | CN7-17 | unused (JTDI, AF0 PU after reset) | left at reset state. |
| all other GPIOs (PC2–PC6, PC10–PC12, PB2, PB7, PB11–PB15, PA11, PA12, PD2, PC14, PC15) | – | unused | configured **analog** at boot (no floating digital inputs). |

### 1.3 EXTI line map (one port per line, unique — VERIFIED R5 §6.1)

| Line | Pin | Signal | Vector | NVIC level | Edge | Handler action (≤ 1 µs except EXTI4) |
|---|---|---|---|---|---|---|
| 0 | PB0 | START limit | EXTI0 | 1 | both | active edge → immediate stop (CLEAN, done by the RAM-resident HAL handler), latch + t_us + position capture (homing edge), mask line until debounced release |
| 1 | PC1 (fallback PB1) | END limit | EXTI1 | 1 | both | as line 0 (never a homing edge; END reached during HOME → HOME_WIRING) |
| 4 | PB4 | HX711 DOUT | EXTI4 | 3 | falling | t_us + position latch, bit-bang read (≈ 40–50 µs), load-limit check (FW_design §5.8) |
| 6 | PB6 | PAUSE button | EXTI9_5 (shared) | 1 | both | active edge → request flag + t_us (controlled stop executed by the tick) |
| 7 | PC7 | STOP/BREAK button | EXTI9_5 (shared) | 1 | both | active edge → immediate stop (CLEAN), latch HALT(src = button) |
| 8 | PA8 | ALM | – | – | – | **not enabled** (v0.3): ALM is polled in the tick (active at the first active sample, inactive after a stable `io.release_ms`) |
| 10 | PA10 | E-stop sense | EXTI15_10 | **0** | both | open edge → immediate stop (TRUNCATE), ENA → disabled level, latch ESTOP |
| 9, 13 | (PA9, PC13) | PEND, B1 | – | – | – | **not enabled** (polled / unused) |

The shared EXTI9_5 handler serves only lines that are pending **and** enabled and clears only its own `PR` bits (Thrust_Stand pitfall P10, R3 §1.7).

### 1.4 PB4 / NJTRST — check requested by the Orchestrator (VERIFIED)
- After reset PB4 is in AF0 (JTAG NJTRST) with the internal pull-up. On the F4 there is no AFIO remap: writing `GPIOB->MODER[9:8] = 00` (input) makes it a plain GPIO; SWD (PA13/PA14) is unaffected.
- The stm32duino core does **not** touch the debug port on F4: the only SWJ handling is `AFIO_SWJ_*` / `__HAL_AFIO_REMAP_SWJ_*` in `libraries/SrcWrapper/inc/PinAF_STM32F1.h:103-106,186-192,359-369` (F1 only); nothing in `cores/`, `SrcWrapper/src` or the F446 variant references NJTRST/JTAG (grep of framework-arduinoststm32 4.30000.0, 2026-10-03).
- Until `board_init_late()` reconfigures it, PB4 has the ≈ 40 kΩ internal pull-up; the HX711 DOUT is a push-pull output, so the pull-up is harmless. JTAG debugging is not possible (SWD only) — not needed.

---

## 2. Timers, DMA, other on-chip resources

| Resource | Use | Configuration (180 MHz HSE; identical numbers on the HSI fallback) | Owner (FW_design) |
|---|---|---|---|
| **TIM2** (32-bit, APB1 timer clock 90 MHz) | PUL generator, one update IRQ per step | PSC = 0 (11.1 ns tick), ARR = c − 1, CCR1 = c − PW, **PWM mode 2** (OC1M = 0b111), OC1PE = 1, ARPE = 1, OPM armed for the last step, CC1P = `pul_invert`, UIE; output on PA0 AF1 (R4 §1.4) | §5.6 `hal/f446/stepgen_tim2` |
| **TIM5** (32-bit, 90 MHz) | 1 MHz free-running device time base (`t_us`, FW-TIM-001) + **CC1 compare = 1 kHz control tick** (no pin; CC1E = 0) | PSC = 89, ARR = 0xFFFFFFFF, CCR1 += 1000 in the ISR, CC1IE; **CC2** = synthetic 80 Hz AFE samples in M1 builds (`FEAT_AFE_SYNTHETIC`) | §5.1, §4 |
| TIM6 / TIM7 | unused (core `TIMER_TONE` / `TIMER_SERVO`; removed by `-DHAL_TIM_MODULE_ONLY`) | – | – |
| TIM3, TIM4, TIM1, TIM8 | unused; TIM3 = optional independent pulse counter (SRS §8 deferred), TIM8 = optional BKIN pulse block (PA6 reserved) | – | – |
| **USART2** (APB1 45 MHz) | PC link via ST-LINK VCP, 921 600 Bd 8N1, no flow control (D-03) | OVER16, **BRR = 0x31** (USARTDIV 3 + 1/16 → **918 367 Bd, −0.35 %**, R1 §2.2) — computed from `HAL_RCC_GetPCLK1Freq()` | §5.9 |
| **DMA1 Stream5 Ch4** | USART2_RX, circular, 2 048 B ring, HT/TC IRQ only for lap counting (overflow detection) | PL high, MINC, PSIZE/MSIZE byte | §5.9 |
| **DMA1 Stream6 Ch4** | USART2_TX, normal mode, **exactly one frame (≤ 168 B) per transfer**; the TC IRQ picks the next frame in the order DATA > response > EVENT (FW_design §5.9.4; needed for FW-STR-002 ≤ 2 ms) | PL medium | §5.9 |
| **IWDG** (LSI 17…47 kHz, ASSUMED DS range) | hang → reset | run: PR = /8, RLR = 190 → **32–90 ms**; NVM erase/program only: PR = /32, RLR = 4095 → ≥ 2.79 s | §5.13 |
| **DWT CYCCNT** | busy-wait (HX711 SCK timing), ISR/loop profiling | – | §5.1 |
| Flash sectors 1 + 2 (0x0800 4000, 0x0800 8000, 16 KB each) | NVM log (two alternating sectors) | custom linker script: `.isr_vector` in sector 0, code from sector 3 (0x0800 C000) | §5.11 |
| UID | 96-bit at **0x1FFF 7A10** (RM0390 §, ASSUMED → verify at WP1) | GET_INFO | §5.10 |
| SRAM (128 KB) | `.data/.bss`, stacks, `.RamFunc` (safety ISRs, flash routines), RAM copy of the vector table during flash operations | – | §7 |

---

## 3. Clock configuration (FW-PLT-002)

| Node | Primary (HSE bypass) | Fallback (HSI) |
|---|---|---|
| Source | **8 MHz MCO of the ST-LINK into PH0, HSE bypass** (`HSEBYP`, `HSEON`), `HSERDY` polled with a **5 ms** DWT-bounded timeout | HSI 16 MHz (factory trimmed ±1 %, ASSUMED) |
| PLL | M = 4 → 2 MHz, N = 180 → VCO 360 MHz, **P = 2 → 180 MHz**, Q = 8 (45 MHz, unused) | M = 8 → 2 MHz, N = 180, P = 2 → 180 MHz (= stm32duino variant default, VERIFIED `variant_NUCLEO_F446RE.cpp:124-184`) |
| Regulator | voltage scale 1 + **over-drive** | same |
| Flash | 5 wait states, ART prefetch + I/D cache on | same |
| AHB / APB1 / APB2 | 180 / **45** / **90** MHz; APB1 timers 90 MHz, APB2 timers 180 MHz | same |
| PLL lock | `PLLRDY` and over-drive ready bounded (DWT, 2 ms each); on any timeout → fallback path | – |
| Reporting | – | `CLK_FALLBACK` status flag + EVENT at link start (R3 P7, Thrust DEF-M1-03) |

Because both paths give the same bus frequencies, every timer, baud and pulse constant is identical; only the accuracy differs (HSE: ST-LINK crystal; HSI: ±1 % at 25 °C, more over temperature). All constants are **computed from `HAL_RCC_GetPCLK1Freq()` / `GetPCLK2Freq()`** at init, never copied from Stefan (R1 B-02). `SystemClock_Config()` is a strong override of the WEAK variant function (VERIFIED `variant_NUCLEO_F446RE.cpp`), called by the core from `hw_config_init()` after `HAL_Init()` (VERIFIED `SrcWrapper/src/stm32/hw_config.c:90-95`).

---

## 4. NVIC priority plan and critical sections

NVIC priority grouping 4 (16 pre-emption levels, 0 = highest; set by the core's `premain()`, VERIFIED `cores/arduino/main.cpp:25-31`). Constants in `02_FW/include/irq_prio.h`.

| Level | IRQ (vector) | Sources | Worst-case body | Rationale |
|---|---|---|---|---|
| **0** | `EXTI15_10_IRQn` | E-stop sense PA10 only | ≤ 0.5 µs (halt TRUNCATE + ENA write + latch) | SRS FW-SW-002: **highest** priority, alone on its level |
| **1** | `EXTI0_IRQn`, `EXTI1_IRQn`, `EXTI9_5_IRQn` | START, END, STOP/BREAK, PAUSE | ≤ 1 µs each | SRS FW-SW-003: STOP/PAUSE "just below the E-stop"; limits share the level (≤ 200 µs budget, SAF-FW-002) |
| **2** | `TIM2_IRQn` | step update | ≤ 1.2 µs (NFR-007: ≤ 2 µs) | must never miss an update; only ≤ 1 µs ISRs above it |
| **3** | `EXTI4_IRQn` | HX711 DOUT | ≈ 40–55 µs (bit-bang) | timestamp latency ≤ 5 µs (FW-TIM-001): only levels 0–2 (each ≤ 1.2 µs) can delay its entry |
| **4** | `TIM5_IRQn` | 1 kHz control tick | ≤ 30 µs typ., ≤ 60 µs worst | ms-scale deadlines (controlled stops ≤ 2 ms, debounce, timeouts) |
| **5** | `DMA1_Stream5_IRQn`, `DMA1_Stream6_IRQn`, `USART2_IRQn`, `SysTick_IRQn` | link RX lap count, TX chunk chaining, line errors, HAL tick | ≤ 2 µs each | `-DTICK_INT_PRIORITY=5` (R1 §6.2) |
| thread | main loop | – | pass ≤ 1 ms (NFR-006) | – |

**Deviation from R5 §6.1** (which put E-stop, limits, STOP, PAUSE and ALM all at level 0, step 1, HX711 2): the E-stop gets level 0 **alone** and the other switch/button ISRs level 1, because SRS FW-SW-002/003 (higher priority than R5) require "E-stop at the highest priority" and "STOP/PAUSE just below". Consequence: all levels below shift by one; nothing else changes (the HX711 SCK-high section still leaves every safety ISR unmasked).

Critical sections (`02_FW/src/hal/crit.h`):

| Section | Implementation | Masks | Used for | Max length |
|---|---|---|---|---|
| `CRIT_HALT` | PRIMASK save/disable/restore | everything | the stop primitive `sg_halt()` itself (decide + 2–4 register writes), so two stop sources never interleave | ≤ 0.2 µs |
| `CRIT_AFE` | BASEPRI = 0x20 | levels ≥ 2 (step, HX711, tick, link) | HX711 SCK-high phase only | ≤ 0.8 µs per bit |
| `CRIT_MOTION` | BASEPRI = 0x20 | levels ≥ 2 | thread/tick writes of the step-generator descriptor (start, retarget, controlled-stop request) | ≤ 1 µs |
| `CRIT_DATA` | BASEPRI = 0x30 | levels ≥ 3 (HX711, tick, link) | class-D DATA mailbox claim by the tick, VALID state publish, load-limit threshold copy (FW_design §4.4) | ≤ 2 µs |
| `CRIT_TICK` | BASEPRI = 0x40 | levels ≥ 4 (tick, link) | thread ↔ tick shared state (motion requests, event ring, latch clears) | ≤ 5 µs |
| `CRIT_NVM` | BASEPRI = 0x20 + EXTI4 masked | levels ≥ 2 | flash erase/program: only the RAM-resident level-0/1 ISRs can run (FW_design §5.11) | ≤ 0.5 s (erase) |

Every IRQ-masked window that can delay the E-stop is `CRIT_HALT` (≤ 0.2 µs) — NFR-007 (≤ 1 µs) and SAF-FW-005 (≤ 100 µs) hold; the E-stop is never masked by an AFE section (FW-AFE-001, FW-SW-002).

---

## 5. NUCLEO-F446RE solder bridges and jumpers that matter

Factory defaults from UM1724 as quoted in R1 §9.2, R2 §5.2, R3 §4.3 and the variant comments — **ASSUMED; to be inspected on Stefan's board before first power-up** (Q-HW-01, SYS-009 item C-01).

| Bridge / jumper | Required state | Why | If different |
|---|---|---|---|
| SB13, SB14 | **ON** | PA2/PA3 ↔ ST-LINK VCP (PC link) | no PC link over USB → fallback external USB-UART (OI-11) |
| SB62, SB63 | **OFF** | D0/D1 not connected to PA2/PA3 | nothing may be wired to D0/D1 in any case |
| SB16, SB50 | **ON** | ST-LINK MCO (8 MHz) → PH0 (HSE bypass) | FW runs on the HSI fallback, `CLK_FALLBACK` reported (FW-PLT-002 accuracy not met → fix the bridges) |
| SB54, SB55 | **OFF** | PH0/PH1 not on the morpho pins / no crystal path | variant comment "PH0 NC by default SB55 opened" (VERIFIED text) |
| SB21 | ON | LD2 on PA5 (status LED) | LED dark, harmless |
| SB17 | ON | B1 on PC13 | unused anyway |
| SB51 / SB56 (A4/A5 ↔ PC1/PC0) | **ON** | END limit on A4 = PC1 | use the PB1 fallback (`-DPIN_END_PB1`) |
| **SB46, SB52** (A4/A5 ↔ PB9/PB8) | **OFF** | otherwise **PB9 (TRIP) is shorted to PC1 (END)** and PB8 to PC0 | must be opened |
| JP5 (PWR) | U5V (USB power) or E5V (external 5 V from the DC-DC on CN7-6, D-22) | Nucleo supply | see `wiring.md` §8 |
| JP6 (IDD) | fitted | MCU supply path | – |

---

## 6. Change history

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-10-03 | First draft from R1 §9 / R5 §6.1 (pins verified by R5 against DS10693 Rev 6, accepted by the PO in D-28), NVIC plan split per SRS FW-SW-002/003, NJTRST release verified in the stm32duino core, clock + timer/DMA allocation, solder-bridge list; driver = PFDE HBS86H clone (D-16/D-27). |
| 0.2 | 2026-10-03 | Aligned to SRS v0.3 / ICD v0.3 / D-29 / D-30:<br>• PA7 DRV_PWR required (20 ms filter, FW-SW-005, SAF-FW-024/025);<br>• PB0 START = only home reference, END never a homing edge (D-29 b);<br>• ALM scope (SAF-FW-026, no step-loss use, D-27);<br>• DMA1 S6 sends one frame per transfer;<br>• TIM5 CC2 synthetic AFE (M1);<br>• new `CRIT_DATA` (BASEPRI 0x30);<br>• level-0/1 fixed reactions in the RAM-resident HAL handler. |
| 0.3 | 2026-10-03 | Final P1 round: ALM (PA8) polled at 1 kHz, EXTI line 8 not enabled (OBS-P1-10); level 1 now serves START, END, STOP, PAUSE only. |
