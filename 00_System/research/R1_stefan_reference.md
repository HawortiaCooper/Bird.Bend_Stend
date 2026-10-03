# R1 — Stefan reference project analysis (pinout, clocks, step generation, link, GUI)

| | |
|---|---|
| **Author / role** | Researcher |
| **Date** | 2026-10-03 |
| **Status** | v1, for Orchestrator review (P0) |
| **Reference** | `E:\Bavovna\Drone\Stefan` (read-only, D-02). FW `FW/stanok` (STM32CubeMX HAL + C++20 app, CMake/CLion), SW `SW/stepper_gui` (Tkinter + pyserial). Files dated 2026-09-12; no `.git` folder, so no commit hash exists — snapshots below carry SHA-256. |
| **Binding decisions considered** | D-02 (read-only refs), D-03 (921600 8N1, CRC16), D-06/D-07 (no HW access), **D-08** (two end-of-travel limit switches START/END, one is home; no operator buttons), **D-09** (PlatformIO + stm32duino, LL/register access for timing-critical peripherals; Stefan = pinout/board-init knowledge only), **D-10** (E-stop: stop pulses immediately, keep driver enabled/holding). |

Citations are `path:line` relative to `E:\Bavovna\Drone\Stefan\` unless prefixed otherwise.
Confidence tags: **VERIFIED** (read in source / computed), **ASSUMED** (from memory of ST documents or inference — to be checked by R2 / the implementer), **UNKNOWN** (needs the product owner or hardware).

## 0. Inventory and archives

| Item | Content | Notes |
|---|---|---|
| `FW/stanok/stanok.ioc` | CubeMX 6.18.1, DB 6.0.181, **STM32Cube FW_F4 V1.28.3**, board NUCLEO-F446RE, toolchain CMake (`stanok.ioc:64-65,149,166`) | VERIFIED |
| `FW/stanok/Core/**` | CubeMX-generated: `main.c`, `gpio.c`, `tim.c`, `usart.c`, `dma.c`, `stm32f4xx_it.c`, `stm32f4xx_hal_msp.c` | HAL driver **V1.8.5** (`Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal.c:55-57`), Nucleo BSP V1.2.9 (`Drivers/BSP/STM32F4xx-Nucleo/stm32f4xx_nucleo.c:55-58`) |
| `FW/stanok/App/**` | Hand-written C++20 app: `axis.hpp` (trapezoidal step generator + homing), `hw_stm32.hpp` (pin/timer policy), `protocol.hpp` (ASCII command protocol), `uart.hpp/.cpp` (DMA UART), `ring_buffer.hpp`, `units.hpp`, `fixed_fmt.hpp`, `mcu.hpp`, `app.cpp` | `-fno-rtti -fno-exceptions`, Debug `-O0`, Release `-Os` (`stanok/cmake/gcc-arm-none-eabi.cmake:35-40`) |
| `FW/stanok/Lib/**` | `Blinker` (LED toggle) — not linked into the loop | dead code |
| `FW/stanok/stanok/build/{Debug,Release}` | CLion build output incl. `stanok.elf`/`.map`; `Testing/Temporary/LastTest.log` is **empty** (no tests ran) | The comment in `App/Inc/mcu.hpp:10-12` mentions host tests with `test/fake_hal` — **that folder does not exist**; no tests are present. |
| `SW/stepper_gui/` | `gui.py` (759 lines, Tkinter), `stepper.py` (376, host library), `sim_device.py` (371, in-memory device simulator), `__pycache__/stepper.cpython-314.pyc` | — |
| `stanok.7z` (2.57 MB), `stepper_gui.7z` (24 KB) | Listed with a stdlib-`lzma` header parser in my scratchpad (no 7-Zip on this PC; Windows `tar` lacks LZMA). **The file lists are identical to the extracted folders** (stanok: 504 entries incl. `Drivers/` and `build/`; stepper_gui: 14 entries). Contents not byte-compared. | file list VERIFIED, contents ASSUMED identical |

Snapshots (verbatim copies, read-only use) in `00_System/research/ref_snapshots/stefan/`:

| Snapshot | Origin | SHA-256 |
|---|---|---|
| `stanok.ioc` | `FW/stanok/stanok.ioc` | `231c716d74df39de980c67fe4e2e51d8781b4da6265fabfb50c7138cb8e7c28e` |
| `hw_stm32.hpp` | `FW/stanok/App/Inc/hw_stm32.hpp` | `135f0cfbba96e40ffcdaba6ded95858fbbb5450c8df77ea13322d7ec6782e420` |
| `axis.hpp` | `FW/stanok/App/Inc/axis.hpp` | `3ef90604eff706c4756ebcbd2804a2a2d50efe0228c24c61962e8d1e4cb9f842` |
| `uart.cpp` | `FW/stanok/App/Src/uart.cpp` | `981e1ff21d11432354d11569872c04206f562a292309d28eacb56e09f0cf9417` |

## 1. Pinout (Stefan, as configured and as used)

Two sources disagree: the `.ioc`/CubeMX code configures only part of what the application drives. Both are listed; the "Configured by" column is authoritative for what the silicon actually does.

| Pin | Nucleo header | Peripheral / AF | Function | Mode | Pull | Speed | Active level | Label | Configured by | Source |
|---|---|---|---|---|---|---|---|---|---|---|
| PA0-WKUP | A0 (CN8-1) | TIM2_CH1, **AF1** | **STEP / PUL** | AF push-pull | none | very high | pulse high (PWM1, OC polarity high) | — (code: PUL) | CubeMX | `stanok.ioc:85-87,218`; `Core/Src/tim.c:69-71,117-122`; `App/Inc/hw_stm32.hpp:12` |
| PA1 | A1 (CN8-2) | GPIO | **DIR** | output PP, init low | none | very high | `dir_invert`=0 → high = positive direction | DIR | CubeMX | `stanok.ioc:88-92`; `Core/Src/gpio.c:54-61`; `App/Inc/axis.hpp:506` |
| PA2 | (D1, disconnected by SB62 OFF) | USART2_TX, AF7 | PC link TX → ST-LINK VCP | AF PP | none | very high | — | USART_TX | CubeMX | `stanok.ioc:101-105`; `Core/Src/usart.c:75-83` |
| PA3 | (D0, disconnected by SB63 OFF) | USART2_RX, AF7 | PC link RX ← ST-LINK VCP | AF PP | none | very high | — | USART_RX | CubeMX | `stanok.ioc:106-110`; `Core/Src/usart.c:75-83` |
| PA4 | A2 (CN8-3) | GPIO | **ENA** | **not configured** (stays reset-state input, floating) | — | — | `ena_invert`=1 → low = enabled (intended) | — | **nobody** (only written with `HAL_GPIO_WritePin`) | `App/Inc/hw_stm32.hpp:14,72-74`; `App/Inc/axis.hpp:59,135-141` |
| PA5 | D13 (CN5-6) | GPIO | LD2 green = "axis busy" | output PP, init low | none | fast | high = LED on | — | BSP (`BSP_LED_Init`) | `stanok.ioc:111-112`; `Core/Src/main.c:101`; `Drivers/BSP/.../stm32f4xx_nucleo.c:179-185`; `App/Src/app.cpp:78-79` |
| PB0 | A3 (CN8-4) | GPIO | **MIN limit** | **not configured** (input floating; comment claims pull-up) | — | — | raw low = active (`lim_invert`=0) | — | **nobody** | `App/Inc/hw_stm32.hpp:15,76-78` |
| PB1 | CN10-24 | GPIO | **MAX limit** | **not configured** (input floating) | — | — | raw low = active | — | **nobody** | `App/Inc/hw_stm32.hpp:16,80-82` |
| PC13 | CN7-23 (B1 USER) | GPIO / EXTI13 | **E-STOP** = blue B1 button | input, IT falling (BSP) | none (board pull-up, ASSUMED) | — | low = pressed = E-stop | — | BSP (`BSP_PB_Init`, EXTI mode, prio 0x0F) | `stanok.ioc:117-118,216`; `Core/Src/main.c:104`; `Drivers/BSP/.../stm32f4xx_nucleo.c:267-277`; `App/Inc/hw_stm32.hpp:17,84-86` |
| PA13 | — | SYS_JTMS-SWDIO | SWD | AF | — | — | — | TMS | CubeMX | `stanok.ioc:93-96` |
| PA14 | — | SYS_JTCK-SWCLK | SWD | AF | — | — | — | TCK | CubeMX | `stanok.ioc:97-100` |
| PB3 | D3 | SYS_JTDO-SWO | SWO trace | AF | — | — | — | SWO | CubeMX | `stanok.ioc:113-116` |
| PC14 / PC15 | — | RCC_OSC32_IN/OUT | reserved, **LSE not enabled** | — | — | — | — | — | — | `stanok.ioc:119-122` |
| PH0 / PH1 | — | RCC_OSC_IN/OUT | reserved, **HSE not enabled** (clock is HSI) | — | — | — | — | — | — | `stanok.ioc:134-137`; `Core/Src/main.c:136-140` |

Header positions: A0–A3, D13 VERIFIED against the stm32duino `variant_NUCLEO_F446RE.h:18-74` Arduino numbering; morpho numbers (CN7/CN10) ASSUMED from UM1724 and consistent with `Thrust_Stand_HAW/01_HW/pinout.md:30-36`.

**Key pinout facts (VERIFIED):**
- `MX_GPIO_Init` configures **only DIR** (`Core/Src/gpio.c:54-61`). PA4 (ENA), PB0, PB1 are never initialised anywhere (grep of `Core/` and `App/`). Writing PA4's ODR has no effect while the pin is an input → **the ENA line is never driven**; `EN 0` cannot disable the driver.
- Limit-input comment says "pull-up, NC to ground" (`hw_stm32.hpp:15-16`) but (a) no pull-up is configured and (b) with an NC switch to GND the pin reads **low when not actuated**, while the code treats low as "limit active" (`hw_stm32.hpp:77`, `lim_invert=false` in `axis.hpp:60`). Both points are masked because limits are disabled by default (`axis.hpp:62-65`).
- E-stop is the Nucleo B1 push button — a **normally-open** contact (press = low). Not fail-safe: a broken wire never trips it.

## 2. Clock tree

### 2.1 Stefan (CubeMX project) — VERIFIED
| Node | Value | Source |
|---|---|---|
| Oscillator | **HSI 16 MHz** (no HSE, no ST-LINK MCO bypass) | `Core/Src/main.c:136-140` |
| PLL | M=16 → 1 MHz, N=336 → VCO 336 MHz, **P=/4 → 84 MHz**, Q=2, R=2 | `Core/Src/main.c:141-145`; `stanok.ioc:195-198,212` |
| SYSCLK / HCLK | 84 MHz, AHB /1 | `Core/Src/main.c:155-156` |
| APB1 (PCLK1) | /2 → 42 MHz; **APB1 timers 84 MHz** (TIM2) | `Core/Src/main.c:157`; `stanok.ioc:173-175` |
| APB2 (PCLK2) | /1 → 84 MHz; APB2 timers 84 MHz | `Core/Src/main.c:158`; `stanok.ioc:176-177` |
| Regulator / flash | Voltage scale 3, 2 wait states | `Core/Src/main.c:131,160` |
| SysTick | 1 ms, HAL tick (`HAL_IncTick`), priority 0 (`TICK_INT_PRIORITY 0U`) | `Core/Src/stm32f4xx_it.c:186-195`; `Core/Inc/stm32f4xx_hal_conf.h:151` |
| HSE_VALUE | 8 MHz in `stm32f4xx_hal_conf.h:98-100` (unused) | — |

### 2.2 stm32duino (D-09) — what the bend stand will actually run — VERIFIED
`~/.platformio/packages/framework-arduinoststm32@4.30000.0/variants/STM32F4xx/F446R(C-E)T/variant_NUCLEO_F446RE.cpp:124-184` (`WEAK SystemClock_Config`):
HSI 16 MHz → PLLM 8 → 2 MHz × PLLN 180 = 360 MHz VCO → PLLP /2 = **SYSCLK 180 MHz** (scale 1 + over-drive, FLASH_LATENCY_5); **APB1 /4 = 45 MHz (APB1 timer clock 90 MHz)**, **APB2 /2 = 90 MHz (APB2 timer clock 180 MHz)**.

Consequences:
- Stefan's step-timer math assumed exactly this: "TIM2 is clocked from APB1x2 = 90 MHz; with Prescaler 9-1 that gives 10 MHz" (`App/Inc/hw_stm32.hpp:31-33`). Under the stm32duino clock, **TIM2 PSC = 8 → 10.000 MHz tick** is correct. (In Stefan's own CubeMX project it is wrong — see §8, B-02.)
- USART2 baud (PCLK1 45 MHz, OVER16): 921600 → BRR 3 + 1/16 → **918 367 Bd, −0.35 %**; 115200 → −0.10 %. In Stefan's 84 MHz project 921600 would be 913 043 Bd (−0.93 %). ST-LINK/V2-1 VCP side (F103) at 921600 ≈ 923 077 Bd (+0.16 %, ASSUMED clock). Total mismatch ≈ 0.5 %, well inside the ≈ 3.4 % receiver tolerance (RM0390 USART tolerance table, ASSUMED value). Computation VERIFIED (scratchpad script).
- HSI accuracy is ±1 % at 25 °C (DS, ASSUMED) and drifts with temperature; acceptable for UART and step timing. HSE bypass from the ST-LINK MCO depends on solder bridges (`variant_NUCLEO_F446RE.h:46` says "PH0 NC by default SB55 opened") — UNKNOWN on the PO's board; not needed.
- The variant's `SystemClock_Config` is `WEAK`: the FW may override it, but there is no reason to (180 MHz gives most ISR headroom).

## 3. Step generation (Stefan)

| Aspect | Implementation | Source |
|---|---|---|
| Timer | **TIM2** (32-bit), CH1 on PA0 AF1, internal clock, PSC register = 9, ARR = 0xFFFFFFFF initially, ARPE enabled | `Core/Src/tim.c:44-49,54` |
| Mode | **PWM mode 1**, CCR1 = pulse width in ticks (`pulse_us` × ticks/µs, default 3 µs → 30 ticks), polarity high → the pulse is at the **start** of each period | `Core/Src/tim.c:69-73`; `App/Inc/axis.hpp:119-123`; `hw_stm32.hpp:64-66` |
| Per-step work | **Update interrupt per step** (`HAL_TIM_IRQHandler` → `HAL_TIM_PeriodElapsedCallback` → `Axis::onTimerUpdate`); new period written to ARR (preloaded, takes effect next period) | `App/Src/app.cpp:86-91`; `axis.hpp:264-333`; `hw_stm32.hpp:60-62` |
| Start | CNT=0, ARR=c−1, `UG` event to load the shadow ARR, clear UIF (avoids a phantom first step), `HAL_TIM_PWM_Start` + `HAL_TIM_Base_Start_IT` | `hw_stm32.hpp:40-53` |
| Stop | `HAL_TIM_Base_Stop_IT` + `HAL_TIM_PWM_Stop` (CC1E=0) called from the update ISR | `hw_stm32.hpp:55-58`; `axis.hpp:456-461` |
| Position | `int32 pos_` += ±1 in every update ISR (counts the pulse that just completed); `remaining_` step counter in position mode; `Steps`/`Mm` strong types, `Scale` with steps/mm (default **640 steps/mm**) | `axis.hpp:266,285-288`; `units.hpp:108-143`; `axis.hpp:52` |
| Profile | **Trapezoidal, D. Austin "real-time speed profile"**: c0 = 0.676·f·√(2/α) (α in steps/s²), cₙ = cₙ₋₁ − (2cₙ₋₁ + rest)/(4n+1) with the division remainder carried (prevents stalls at large n); decel starts when `remaining ≤ n` (symmetric); velocity mode re-targets c_min on the fly | `axis.hpp:100-131,463-484,323-330,170-198` |
| Defaults | vmax 20 mm/s (12 800 steps/s), accel 200 mm/s², home 8 mm/s, back-off 3 mm, pulse 3 µs | `axis.hpp:51-68` |
| Max rate | c_min floor 4 ticks (2.5 MHz theoretical at 10 MHz); real limit is ISR cost: HAL dispatch + 64-bit division in `rampNext`. Author estimates top speed at a 13 µs step period (~77 kHz) (`uart.cpp:162-164`). | ASSUMED ≈ 50–80 kHz at 84 MHz |
| DIR | set in thread context before `timerStart`, then `dirSetupDelay()` = 400-iteration volatile spin (≈ 25–40 µs at 84 MHz `-Os`, much longer at `-O0`; ASSUMED), comment: "drivers need roughly 5 µs" | `hw_stm32.hpp:88-97`; `axis.hpp:506-508,529-531` |
| EN | `enable(on)` writes PA4 = `ena_invert ? !on : on` (default invert → low = enabled); `enable(false)` also calls `halt()`. Driver disabled at init. **Pin not configured → no effect** (§1). | `axis.hpp:88-93,135-141` |
| Stop types | `STOP` = decelerate along the profile (`stop()`), `HALT` = immediate timer stop, steps may be lost (`halt()`), E-stop = timer stop + `Fault` + **driver disabled** | `axis.hpp:201-225,268-274,341-347` |
| Homing | 3-phase FSM against MIN: fast approach at −home_v → back-off +3 mm → slow approach at −home_v/5 → `zero()`; requires `limits_enabled` | `axis.hpp:244-259,534-564` |
| Command hand-over | a new move while busy → `pending_` + smooth `stop()`, relaunched from `service()` when idle | `axis.hpp:154-160,189-192,354-363` |

## 4. UART link (Stefan)

| Aspect | Value | Source |
|---|---|---|
| Peripheral | **USART2** PA2/PA3 → ST-LINK VCP | `Core/Src/usart.c:43,75-83` |
| Baud | **115200** 8N1, no flow control, OVER16 | `Core/Src/usart.c:44-50` |
| RX | **DMA1 Stream5 Ch4, circular**, 256 B window; **no RX interrupt** — main loop reads NDTR and drains bytes into lines (`(size − NDTR) % size`); overrun flagged at ¾ lap; restart after line error (abort + re-arm, adopt current NDTR) | `stanok.ioc:9-18`; `uart.hpp:35-37`; `uart.cpp:60-80,94-100,126-140,220-239` |
| TX | 2048-B SPSC ring (atomics) → 256-B staging chunk → **DMA1 Stream6 Ch4 normal**; next chunk from `HAL_UART_TxCpltCallback`; overflow silently drops the tail | `stanok.ioc:19-28`; `uart.hpp:86-87`; `uart.cpp:144-210` |
| IRQs | USART2 (errors), DMA1_Stream5/6, all priority 0 | `Core/Src/usart.c:123-124`; `Core/Src/dma.c:47-51` |
| Protocol | **ASCII, line-based (`\n`), no CRC, no length, no sequence number** (an optional `#id` first token is accepted and discarded, never echoed). Commands: `PING`, `ID`, `STATUS`, `CFG [key value]`, `EN 0/1`, `MOVE mm`, `MOVETO mm`, `JOG mm/s`, `STOP`, `HALT`, `HOME`, `ZERO`, `CLEAR`, `TELEM hz(0..200)`, `RESET`. Replies `OK …` / `ERR <code> <NAME>` (codes 0..7: OK, SYNTAX, RANGE, BUSY, DISABLED, FAULT, LIMIT, NOTHOMED). | `protocol.hpp:51-116,187-200`; `mcu.hpp:40-49` |
| Streaming | `T st=… pos=… tgt=… vel=… steps=… flg=0x.. ms=…` at TELEM Hz; `S` = reply to STATUS; `!` = async events (`! ESTOP`, `! LIMIT MIN/MAX`, `! HOMED`, `! FAULT`, `! RXOVR`, status on Done); `C …` = config dump; `! BOOT STEPCTL 2.0-cpp` at start | `protocol.hpp:36-47,120-139,215-234,267-290` |
| Flags | 0x01 Enabled, 0x02 Homed, 0x04 LimMin, 0x08 LimMax, 0x10 Estop, 0x20 SoftLim | `axis.hpp:31-38`; `stepper.py:35-42` |

**Reuse for our ICD (binary, CRC-16, D-03/D-05):** the protocol itself is not reusable. Reusable as a *checklist*: the command set above, the split STOP (profiled) vs HALT (immediate), explicit `CLEAR` for faults, async event messages, error-code taxonomy, and "config dump in one reply" (maps to the PO's "read all configurations" command). The DMA-circular-RX + NDTR-polling transport maps 1:1 to LL (§6.2).

## 5. Inputs, interrupts, safety (Stefan)

| Input | Handling | Source |
|---|---|---|
| E-stop (PC13/B1, NO, low = active) | Polled: in every step ISR (`estopRaw()` → timer stop, `Fault`) and in `service()` from the main loop (→ `halt()`, `Fault`, **`enable(false)`**). EXTI13 is enabled by the BSP but `HAL_GPIO_EXTI_Callback` is not overridden → the interrupt does nothing. Latency when idle = main-loop period. | `axis.hpp:268-274,341-347`; `Core/Src/stm32f4xx_it.c:263-272`; `Core/Src/main.c:104` |
| MIN / MAX limits (PB0/PB1) | Polled per step in the direction of travel (`directionBlocked`) and each `service()`; **disabled by default**; no debounce; on hit → timer stop, `Fault` (or expected event while homing) | `axis.hpp:275-283,349-350,413-430` |
| Encoder / ALM / PEND | none | — |
| Soft limits | optional `[smin, smax]` checked at command time only (`checkSoft`), off by default; no validation `smin < smax` | `axis.hpp:440-446`; `protocol.hpp:258-259` |
| Recovery | `CLEAR` clears Fault + latched limit/E-stop flags; motion refused while E-stop still pressed (`precheck`) | `axis.hpp:235-242,432-438` |

**Interrupt priorities (VERIFIED):** `HAL_NVIC_SetPriorityGrouping(NVIC_PRIORITYGROUP_0)` (`Core/Src/stm32f4xx_hal_msp.c:72`) → 0 pre-emption bits; TIM2, USART2, DMA1_S5/S6, SysTick all at priority 0 (`tim.c:96`, `usart.c:123`, `dma.c:47-51`, `stm32f4xx_hal_conf.h:151`); BSP EXTI15_10 = 0x0F (sub-priority only). **Nothing pre-empts anything** → the step ISR waits for any running UART/DMA/SysTick ISR. The TX-complete callback copies up to 256 bytes inside the USART/DMA ISR (`uart.cpp:206-210,178-182`, ~19 µs by the author's own estimate at `uart.cpp:162-164`) — exactly the step-jitter the author tried to avoid in thread context.

## 6. Board init sequence and mapping onto PlatformIO + stm32duino (D-09)

### 6.1 Stefan's sequence (VERIFIED, `Core/Src/main.c:78-114`)
`HAL_Init()` (SysTick 1 ms, NVIC group 4, then MSP sets group 0) → `SystemClock_Config()` (84 MHz HSI) → `MX_GPIO_Init()` (DIR low) → `MX_DMA_Init()` → `MX_USART2_UART_Init()` → `MX_TIM2_Init()` (PWM CH1, PA0 AF1) → `app_init()` (bind TIM2, `Axis::init` = defaults + **driver disabled**, UART DMA RX start, `! BOOT` banner) → `BSP_LED_Init(LED2)` → `BSP_PB_Init(BUTTON_USER, EXTI)` → loop `app_loop()` (drain RX lines, protocol, events/telemetry, axis service, LD2 = busy).
Note the order problem: the E-stop pin is read by `app_init`/`app_loop` code paths before `BSP_PB_Init` configures it (harmless only because the reset state is already an input).

### 6.2 How it maps onto stm32duino (recommendation)
stm32duino already does `HAL_Init` + variant `SystemClock_Config` (180 MHz, §2.2) before `setup()`. Nothing from `Core/` is copied; the knowledge is re-expressed with LL/register code:

| Stefan (CubeMX HAL) | stm32duino + LL equivalent | Notes / pitfalls |
|---|---|---|
| `SystemClock_Config` 84 MHz | keep variant `SystemClock_Config` (180 MHz); `SystemCoreClock` = 180 MHz | do not copy Stefan's PLL values |
| `MX_GPIO_Init` (DIR only) | first lines of `setup()`: ENA = **disabled level**, DIR low, STEP low (GPIO out) **before** anything else; then inputs with pull-ups; read initial switch states | Stefan never configured ENA/limits — configure every used pin explicitly |
| `MX_TIM2_Init` + `HAL_TIM_PWM_*` | LL: `RCC APB1ENR.TIM2EN`, PSC = 8 (90 MHz → 10 MHz), ARPE=1, OC1 PWM mode 2 + OC1PE (see §9.3), CC1E, PA0 AF1; own `TIM2_IRQHandler` | build with **`-DHAL_TIM_MODULE_ONLY`** so the core's `HardwareTimer.cpp` does not define `TIM2_IRQHandler` (`libraries/SrcWrapper/src/HardwareTimer.cpp:29`); never call `analogWrite()` on PA0 |
| `MX_DMA_Init` + `MX_USART2_UART_Init` + `uart.cpp` | LL USART2 921600 (BRR from PCLK1 45 MHz), DMA1 Stream5 Ch4 circular RX (≥ 1 KB window at 921600: 256 B = 2.8 ms only), DMA1 Stream6 Ch4 TX normal | build with **`-DHAL_UART_MODULE_ONLY`** so the core does not own `USART2_IRQHandler` / `Serial` (`libraries/SrcWrapper/src/stm32/uart.c:23`; `cores/arduino/Serial.cpp:29`); the core's default `Serial` RX buffer is only 64 B (`cores/arduino/Serial.h:43-44`) |
| `BSP_PB_Init` / EXTI | LL EXTI + SYSCFG EXTICR, own `EXTIx_IRQHandler` | build with **`-DHAL_EXTI_MODULE_DISABLED`** so `interrupt.cpp` does not define the EXTI handlers (`libraries/SrcWrapper/src/stm32/interrupt.cpp:43`) — same flags Thrust_Stand_HAW uses (`Thrust_Stand_HAW/02_FW/platformio.ini`) |
| SysTick prio 0 | **`-DTICK_INT_PRIORITY=…`** (core default is 0x00 = highest, `system/STM32F4xx/stm32f4xx_hal_conf_default.h:154-155`); Thrust stand uses 5 | SysTick must not delay the step ISR / E-stop |
| `NVIC_PRIORITYGROUP_0` (all 0) | keep the core's group 4 (16 pre-emption levels) and assign levels (§9.4) | — |
| `dirSetupDelay` spin loop | DWT `CYCCNT` busy-wait (deterministic, 5.6 ns resolution at 180 MHz) | Thrust stand already uses DWT (`Thrust_Stand_HAW/01_HW/pinout.md:153`) |
| `__get_PRIMASK/__disable_irq` `CriticalSection` | reuse the idea (save/restore PRIMASK, RAII or macro); prefer BASEPRI masking above E-stop level so E-stop is never masked | `App/Inc/mcu.hpp:22-37` |

## 7. SW `stepper_gui` — architecture and GUI patterns

**Architecture (VERIFIED):**
- `stepper.py` — `Stepper` host library: pyserial port (or `"sim"` → `sim_device.VirtualPort`, duck-typed serial) (`stepper.py:126-138`); **reader thread** splits lines and dispatches by first char (`T` telemetry callback, `S`/`C`/`OK`/`ERR` → reply queue, `!` → event queue + callback) (`stepper.py:163-209`); `cmd()` = send + wait for `OK`/`ERR` with timeout, raising `StepperError`/`StepperTimeout` (`stepper.py:242-265`); high-level API `configure/get_config/enable/move/move_to/jog/stop/halt/home/zero/clear_fault/telemetry/wait_idle` (`stepper.py:269-370`); `close()` sends `TELEM 0`, `HALT`, `EN 0` (`stepper.py:140-153`).
- `sim_device.py` — in-memory device speaking the same protocol: 2 ms integration of a trapezoidal axis, homing FSM, limit/fault emulation, `fail_home` fault injection, telemetry thread (`sim_device.py:400-436,639-747`). Good pattern for our D-07 "simulator first" (our simulator will speak the binary ICD instead).
- `gui.py` — Tkinter single window, dark palette; **worker thread + `work_queue`** so serial calls never freeze the UI; **`ui_queue`** carries telemetry/log/events/callables back to the Tk thread, polled every 40 ms (`gui.py:57-62,381-397,449-476`).

**Features (VERIFIED):**
| Feature | Where |
|---|---|
| Port combobox (incl. `sim`), refresh, baud combobox (default 921600), connect/disconnect, FW ID label | `gui.py:140-162,313-378` |
| Readout: big position (mm), velocity, target, state colour, flag "LEDs" (ENABLED/HOMED/MIN/MAX/ESTOP); limit LEDs greyed when limit detection is disabled ("dark ≠ OK") | `gui.py:164-189,478-506` |
| Driver-enable checkbox (synced from status), Home (enabled only if limits on, with hint), Zero, Clear fault | `gui.py:191-211,594-608` |
| Hold-to-jog buttons ◀◀ ◀ ▶ ▶▶ (×2 fast), **speed slider** 0.1–50 mm/s clamped to device vmax | `gui.py:213-236,407-418` |
| **Step buttons −10, −1, −0.1, +0.1, +1, +10 mm** (relative MOVE) | `gui.py:238-245` |
| Go-to entry (absolute/relative radio, Enter = go) | `gui.py:247-266,428-437` |
| **STOP (profiled) and HALT (Esc) buttons, packed first at the bottom so they never scroll off** | `gui.py:125-127,268-274` |
| Keyboard: ←/→ hold-to-jog with 70 ms deferred stop to absorb X11 key-repeat, Shift = ×2, Space = STOP, Esc = HALT, Home = homing; arrows ignored inside entry fields | `gui.py:617-643` |
| Telemetry chart (canvas, 20 s window, position + velocity on twin axes), telemetry-rate combobox, clear | `gui.py:276-297,509-566` |
| Traffic log (TX/RX/events/errors coloured, trimmed to 400 lines; telemetry not logged) | `gui.py:299-311,569-583` |
| Config dialog: read → edit → write → re-read (fields + checkboxes) | `gui.py:656-748` |
| On window close: HALT, then disconnect (`TELEM 0`, `HALT`, `EN 0`) | `gui.py:646-653` |

**Worth reusing for our PySide6 manual-control tab (Implementer D):**
1. Layout: STOP/HALT pinned and always visible; position readout large; state + flag LEDs with a distinct "not monitored" style.
2. The exact increment set ±0.1/±1/±10 mm matches the PO request (Initial_specs SW-5) — reuse.
3. Hold-to-jog with release-stop and key-repeat debounce; Shift multiplier; arrows disabled while an entry has focus.
4. Absolute/relative go-to entry with Enter.
5. Command worker off the UI thread + UI-thread marshalling queue (in Qt: a QObject worker in a QThread with signals).
6. Config dialog read/edit/write/read-back — the PO's "validate writing" requirement (SW-1) is the same loop plus a field-by-field compare.
7. In-memory simulator selectable as a port name (`sim`) — same idea for our simulator/host twin.

**Must change (do not copy as is):**
- STOP/HALT go through the same FIFO `work_queue` as every other command (`gui.py:395-397,420-426`): a stop waits behind a pending command that may block for the 2 s reply timeout (`stepper.py:101,242-265`). Our stop must bypass the queue (priority path, written directly by the transport), plus the PO's global **Pause/Break** shortcut (SW-6) app-wide.
- Hold-to-jog relies on the release event; a lost release (focus loss, USB hiccup) leaves the axis jogging. Add a FW-side jog dead-man (jog must be refreshed, e.g. every ≤ 200 ms, else profiled stop) — R4/SRS topic.
- `cmd()` drains the reply queue before sending (`stepper.py:245-247`) and matches replies FIFO without IDs; the UI thread also calls `halt()` on close while the worker may be mid-command (`gui.py:646-650`). Our binary ICD needs a sequence number echoed in replies.
- Stefan's slider sets **jog speed**; the PO says "move motor with slider" — semantics unclear (Q-R1-06).

## 8. Quality assessment

### 8.1 Solid / reusable ideas (re-implement under stm32duino+LL; do not copy HAL code)
| # | Item | Source |
|---|---|---|
| S-01 | Hardware-policy template (`Axis<Hw>`): motion core free of HAL → host-testable; matches our FW host twin approach | `axis.hpp:1-19,83-86`; `hw_stm32.hpp` |
| S-02 | Austin ramp with remainder carry, symmetric decel trigger, velocity re-targeting, pending-command hand-over | `axis.hpp:170-198,323-330,463-484,486-532` |
| S-03 | Timer start sequence: CNT=0, ARR load, UG, **clear UIF** to avoid a phantom first step | `hw_stm32.hpp:40-53` |
| S-04 | Homing FSM: fast approach → back-off → slow approach → zero; limit hit while homing is an event, not a fault | `axis.hpp:244-259,279-281,534-564` |
| S-05 | DMA circular RX with NDTR modulo fix, overrun detection, restart after line error that adopts the live NDTR | `uart.cpp:60-100,126-140,220-239` |
| S-06 | SPSC ring with acquire/release atomics; RAII PRIMASK critical section that nests | `ring_buffer.hpp:21-70`; `mcu.hpp:22-37` |
| S-07 | Driver disabled at boot; motion refused unless enabled and not faulted; E-stop checked inside the step ISR | `axis.hpp:88-93,268-274,432-438` |
| S-08 | Strong unit types (`Steps`, `Mm`, `MmPerSec`) and integer-scaled telemetry (no float printf) | `units.hpp`; `fixed_fmt.hpp` |
| S-09 | GUI patterns of §7 and the duck-typed simulator | `gui.py`, `sim_device.py` |

### 8.2 Bugs and risks (specific)
| ID | Severity | Finding | Evidence |
|---|---|---|---|
| B-01 | **High** | **ENA (PA4) and limit inputs (PB0/PB1) are never configured.** ENA is never driven → "EN 0", E-stop disable and the boot "disabled" state have no electrical effect (driver enabled whenever its ENA input is open — HBS86H behaviour ASSUMED, see R2). Limits float (no pull-ups) despite the comment. | `Core/Src/gpio.c:54-61` (only DIR); `hw_stm32.hpp:72-82`; `stanok.ioc:44-60` (PA4/PB0/PB1 absent) |
| B-02 | **High** | **Timer clock mismatch**: code assumes `kTimerHz` = 10 MHz (90 MHz/9), actual TIM2 = 84 MHz/(9+1) = **8.4 MHz**. Real speeds = 0.84 × commanded, accelerations × 0.706, pulse 3.57 µs, reported velocity 19 % high. | `hw_stm32.hpp:31-33` vs `Core/Src/main.c:141-158`, `Core/Src/tim.c:45` |
| B-03 | **High** | **Telemetry and uptime are dead**: `app_tick_1ms()` is never called (SysTick only calls `HAL_IncTick`) → `uptime_ms` stays 0 → `TELEM n` never emits (`(0−0) ≥ period` is false), `ms=` is always 0. The GUI chart shows "no data" on hardware; it works only with the simulator. | `app.hpp:19`; `app.cpp:82`; `Core/Src/stm32f4xx_it.c:186-195`; `protocol.hpp:131-138` |
| B-04 | **High** | **Baud mismatch**: FW 115200, host default 921600. | `Core/Src/usart.c:44` vs `stepper.py:100`, `gui.py:152` |
| B-05 | **High** (safety) | E-stop is the B1 **normally-open** push button (press = low); wire break or missing button = no E-stop. Not acceptable for the PO's "NC button" (FW-3). | `hw_stm32.hpp:17,84-86` |
| B-06 | Med | E-stop / limits are **polled** (step ISR + main loop); when idle, latency = main-loop period; the EXTI13 ISR does nothing. No debounce on limits → contact bounce or EMI from the 86HS2140 cabling can trip a spurious fault mid-move. | `axis.hpp:268-283,341-350`; `stm32f4xx_it.c:263-272` |
| B-07 | Med | **Possible runt pulse at stop**: PWM1 puts the pulse at the *start* of each period; the update ISR that decides "last step done" runs after CNT has already wrapped and PA0 has gone high, then `HAL_TIM_PWM_Stop` cuts it ~1 µs later → a < 3 µs pulse the driver may or may not count → ±1 step per move. | `tim.c:69`; `axis.hpp:285-288,456-461`; `hw_stm32.hpp:55-58` — ASSUMED, verify with a scope / host twin |
| B-08 | Med | All IRQs at the same priority with NVIC group 0 → no pre-emption; the TX-complete callback copies ≤ 256 B inside the ISR → step jitter up to ~20 µs. | `stm32f4xx_hal_msp.c:72`; `tim.c:96`; `usart.c:123`; `dma.c:47-51`; `uart.cpp:178-182,206-210` |
| B-09 | Med | Limit polarity inconsistent with the documented NC wiring (low treated as "hit", but NC-to-GND reads low when *not* hit). | `hw_stm32.hpp:15-16,76-82`; `axis.hpp:60,413-425` |
| B-10 | Med | `CFG key value` assigns the value **before** the busy check; returns `ERR BUSY` but the value stays in `cfg_` un-applied (applied later by the next `jog`/`service`). | `protocol.hpp:243-264`; `axis.hpp:194,360` |
| B-11 | Med | E-stop handling **disables the driver** (`enable(false)`), which contradicts D-10 (hold under load). | `axis.hpp:341-347` |
| B-12 | Low | DIR setup delay is a compiler/clock-dependent spin (`volatile` 400 iterations), not a timed delay. | `hw_stm32.hpp:93-97` |
| B-13 | Low | `zero()` sets the `Homed` flag → "homed" after a manual zero anywhere; `Error::NotHomed` defined but never used (soft limits work unhomed). | `axis.hpp:227-233`; `mcu.hpp:48` |
| B-14 | Low | Optional `#id` token is stripped and not echoed → no request/response correlation; ASCII protocol has no CRC (D-03 requires CRC-16). | `protocol.hpp:57-61` |
| B-15 | Low | TX ring overflow drops data silently; RX window 256 B is only 2.8 ms at 921600 and is drained only by the main loop. | `uart.cpp:144-150`; `uart.hpp:36`; `uart.cpp:118-125` |
| B-16 | Low | `smin < smax` not validated; `RESET` blocks 50 ms with `HAL_Delay`. | `protocol.hpp:258-259`; `app.cpp:41-45` |
| B-17 | Info | Claimed host tests (`test/fake_hal`) are absent; `LastTest.log` empty → nothing in the reference is test-verified. GUI was evidently exercised only against the simulator (B-03/B-04). | `mcu.hpp:10-12`; `stanok/build/*/Testing/Temporary/LastTest.log` |
| B-18 | Info | GUI: STOP/HALT queued behind other commands; concurrent `halt()` from the UI thread on close (§7). | `gui.py:395-426,646-650` |

### 8.3 Avoid
- Copying any `Core/` HAL file or the `.ioc` (D-09; also the clock/pin config is incomplete).
- Polled-only E-stop on an NO contact; disabling the driver on E-stop (D-10).
- Equal NVIC priorities; work inside the UART TX-complete ISR; HAL dispatch on the per-step path.
- PWM1 "pulse at period start" + stop from the update ISR (B-07).
- ASCII protocol without CRC/IDs.

## 9. Pin recommendation for the bend stand (NUCLEO-F446RE, stm32duino)

### 9.1 Proposed map
Reuses Stefan where possible (STEP/DIR/ENA/limits/LD2/UART). FT = 5 V tolerant per DS10693 Table 10 "I/O structure" — **ASSUMED from memory**: FT for PA0–PA3, PA6–PA15, PB0–PB15, PC0–PC15 (FT except in analog mode), **PA4/PA5 are TTa (DAC pins, not 5 V tolerant)**. To be VERIFIED by R2/the FW implementer against the datasheet before 01_HW/pinout.md is frozen.

| Signal | Pin | Nucleo header (Arduino / morpho) | Peripheral | Mode | Active level / note | FT | Origin |
|---|---|---|---|---|---|---|---|
| **STEP (PUL)** | **PA0** | A0, CN8-1 / CN7-28 | TIM2_CH1 AF1 (32-bit, 90 MHz → PSC 8 → 10 MHz) | AF PP | pulse high; alt. TIM5_CH1 AF2 on the same pin | yes (ASSUMED) | Stefan |
| **DIR** | **PA1** | A1, CN8-2 / CN7-30 | GPIO | out PP | polarity = config param | yes (ASSUMED) | Stefan |
| **ENA** | **PA4** | A2, CN8-3 / CN7-32 | GPIO | out PP | polarity = config param; must be driven to "disabled" first in `setup()` | **no (TTa)** → push-pull only, never open-drain to 5 V | Stefan (intended) |
| ALM in (opt.) | PA8 | D7, CN9-8 / CN10-23 | GPIO (EXTI8 possible) | in, pull-up to 3.3 V | HBS86H ALM opto output, emitter to GND → low = alarm (ASSUMED, R2) | yes | new |
| PEND in (opt.) | PA9 | D8, CN5-1 / CN10-21 | GPIO, polled | in, pull-up | in-position | yes | new |
| **HX711 DOUT** | **PB5** | D4, CN9-5 / CN10-29 | GPIO + EXTI5 (EXTI9_5) falling | in | low = data ready | yes | new (Thrust used PA5 = LD2 here, avoided) |
| **HX711 PD_SCK** | **PB10** | D6, CN9-7 / CN10-25 | GPIO | out PP, init low | high > 60 µs = power-down | yes | new |
| HX711 RATE (opt.) | PB4 | D5, CN9-6 / CN10-27 | GPIO | out PP | high = 80 SPS (D-04); PB4 is NJTRST with pull-up after reset (high = 80 SPS until init) | yes | new |
| **START limit (home/zero)** | **PB0** | A3, CN8-4 / CN7-34 | GPIO + **EXTI0** (own vector) | in, pull-up | NC switch to GND: closed = low = OK, **open/high = hit or wire break** | yes (ASSUMED) | Stefan MIN |
| **END limit** | **PB1** | CN10-24 (morpho only) | GPIO + **EXTI1** (own vector) | in, pull-up | same as START | yes (ASSUMED) | Stefan MAX |
| **E-stop (NC)** | **PA10** | D2, CN9-3 / CN10-33 | GPIO + EXTI10 (EXTI15_10, shared only with PC13) | in, pull-up (+ external 4.7 kΩ/RC recommended) | NC to GND: **high = STOP or wire break** | yes | new (replaces B1) |
| Status LED | PA5 | D13 (LD2) | GPIO | out PP | high = on (busy/state blink) | TTa (output only, fine) | Stefan |
| PC link | PA2 / PA3 | via ST-LINK VCP (not on D0/D1) | USART2 AF7 + DMA1 S6/S5 Ch4 | AF | 921600 8N1 (D-03) | — | Stefan |
| SWD / SWO | PA13 / PA14 / PB3 | — / D3 | SYS | AF | keep free (debug, SWO trace) | — | Stefan |
| B1 user button | PC13 | CN7-23 | — | in | **not used** (D-08: no operator buttons; NO contact is not fail-safe) | — | — |
| Spare debug pins (opt.) | PC8 / PC9 | CN10-2 / CN10-1 | GPIO | out | scope markers (ISR timing) | — | new |

Free for later: PA6/PA7 (D12/D11), PB6 (D10), PC7 (D9), PB8/PB9 (D15/D14 = I2C1), PC0/PC1 (A5/A4).

### 9.2 Conflicts and constraints flagged
1. **USART2 / VCP**: PA2/PA3 reach the ST-LINK only with SB13/SB14 ON and SB62/SB63 OFF (factory default, ASSUMED from UM1724); D0/D1 on CN9 are therefore not connected — do not use them. UNKNOWN whether the PO's board was modified.
2. **PA5 = LD2**: do not copy Thrust_Stand_HAW's HX711 DOUT on PA5 (`Thrust_Stand_HAW/01_HW/pinout.md:30`) — LD2 + 510 Ω would load DOUT; here PA5 stays the LED.
3. **PA4 is not 5 V tolerant (ASSUMED TTa)**: if the HBS86H inputs are wired common-anode to +5 V with the MCU sinking (open-drain), ENA must move to an FT pin (e.g. PA6) or all three signals go through a 5 V buffer (e.g. 74AHCT125 powered from 5 V). With 3.3 V push-pull into common-cathode inputs the opto current may be marginal (R2: HBS86H input current spec). Decision needed — Q-R1-01.
4. **EXTI line sharing**: one port per EXTI line. PB0/PB1 take lines 0/1 → PC0/PC1/PA0/PA1 cannot also use EXTI (fine, they are outputs/spare). PA10 (E-stop) shares the EXTI15_10 vector with PC13 — harmless as long as B1 is not enabled. HX711 DOUT (line 5) and optional ALM (line 8) share EXTI9_5; the handler must check `EXTI->PR`.
5. **stm32duino ownership of IRQ handlers**: `-DHAL_TIM_MODULE_ONLY`, `-DHAL_UART_MODULE_ONLY`, `-DHAL_EXTI_MODULE_DISABLED`, `-DTICK_INT_PRIORITY=<low>` (see §6.2). Never call `analogWrite()`/`tone()`/`Servo` on TIM2.
6. **HX711 logic level**: run HX711 DVDD at 3.3 V (as Thrust_Stand_HAW, `wiring.md:73`); if DVDD = 5 V, DOUT is 5 V (needs FT — PB5 is FT, ASSUMED) and SCK VIH (≈ 0.7·DVDD = 3.5 V) is **not** met by 3.3 V. Generic HX711 modules tie RATE to GND (10 SPS) → 80 SPS (D-04) needs RATE high (board mod or PB4) — Q-R1-08.
7. **Limit/E-stop inputs**: dry contacts to GND with pull-up to 3.3 V never apply 5 V to the MCU; if inductive 12–24 V sensors are used, opto/level shifting is mandatory (Q-R1-03).
8. **Reset window**: between reset and `setup()` all pins float (ENA included). If the HBS86H enables the motor with ENA open (ASSUMED, R2), add an external pull resistor on the ENA line that holds the driver *disabled* during reset/boot/flash (Q-R1-01).

### 9.3 Step-output recommendation (stm32duino + LL)
- TIM2, PSC = 8 (90 MHz → 10 MHz, 100 ns resolution), 32-bit ARR (slowest step 429 s — no overflow handling needed).
- Use **PWM mode 2** (output high when CNT ≥ CCR1) with **ARPE = 1 and OC1PE = 1**, CCR1 = ARR + 1 − pulse_ticks, so each pulse *ends* at the update event. The update ISR then counts completed pulses only, and stopping at an update cannot produce a runt pulse (fixes B-07). For the final step set **OPM = 1** (one-pulse mode) so the counter stops by hardware after the last pulse; the pin rests low.
- Per-step ISR: own `TIM2_IRQHandler` (no HAL dispatch), clear UIF first, 32-bit/float arithmetic (Cortex-M4F) instead of `int64` division.
- DIR/ENA setup: DWT-timed waits; DIR ≥ 5 µs before the first PUL edge, pulse ≥ 2.5 µs (HBS86H values ASSUMED → R2). With 10 MHz ticks: pulse 3 µs = 30 ticks (Stefan's default, keep as a parameter).
- Position = signed 32-bit step counter updated in the ISR; distance (mm) = steps / steps_per_mm (PO FW-4 parameter).

### 9.4 Interrupt priority proposal (NVIC group 4, lower number = higher priority)
| Level | Source | Rationale |
|---|---|---|
| 0 | E-stop EXTI (PA10), START/END limit EXTI0/EXTI1 | must stop pulses within µs (D-10: stop pulses, keep ENA) |
| 1 | TIM2 update (step) | timing-critical, short |
| 2 | HX711 DOUT EXTI9_5 | 24+1..3 SCK pulses, short masked windows (Thrust_Stand_HAW pattern) |
| 3–4 | USART2 DMA TC/HT, USART2 errors | link |
| 5 | SysTick (`-DTICK_INT_PRIORITY=5`) | timebase only |

## 10. Open questions and assumptions

### 10.1 Open questions for the product owner
| ID | Question | Why it matters |
|---|---|---|
| Q-R1-01 | How are the HBS86H PUL/DIR/ENA inputs wired (common-anode to +5 V, common-cathode, or via a buffer)? Is a 5 V buffer (e.g. 74AHCT125) and an ENA pull resistor (driver disabled during MCU reset) acceptable? | PA4 is not 5 V tolerant; opto current; safe state during boot/flash (§9.2-3/8) |
| Q-R1-02 | Will the HBS86H ALM (and PEND) outputs be wired to the board? | optional inputs PA8/PA9; fault handling in SRS |
| Q-R1-03 | Type of the START/END limit switches (mechanical NC micro-switch or inductive 12–24 V sensor, NPN/PNP)? Which end is home/zero — START? | input circuit, polarity, homing direction (D-08) |
| Q-R1-04 | E-stop: single NC contact into the MCU only, or a second contact block in a hardware path (e.g. cutting PUL or driver power)? | D-10 keeps the driver enabled; a FW-only E-stop depends on FW health |
| Q-R1-05 | Mechanics: lead-screw pitch / gearing, HBS86H microstep DIP setting, travel length, required max speed and accel? (Stefan defaults: 640 steps/mm, 20 mm/s, 200 mm/s², travel 300 mm in the simulator) | default `steps_per_mm`, rate limits, soft limits |
| Q-R1-06 | Manual-control slider: should it set **jog speed** (as in Stefan's GUI) or command a **target position**? | GUI design (SW-5) |
| Q-R1-07 | Is the NUCLEO-F446RE the same board Stefan used, and were any solder bridges changed (SB13/14/62/63, SB21 LD2, MCO/HSE bridges)? | UART path, LD2, clock source |
| Q-R1-08 | Which HX711 module (RATE pin accessible/jumper?), and is its logic supply 3.3 V? | 80 SPS (D-04) needs RATE high; SCK VIH at 5 V DVDD |
| Q-R1-09 | Did Stefan's stand ever run on hardware with this FW, and with which `dirinv/enainv/liminv/pulse` settings? (B-01..B-04 suggest it was only tested against the simulator) | whether any HW-proven settings exist |

### 10.2 Assumptions / facts register
| Statement | Tag |
|---|---|
| Stefan clock: HSI → 84 MHz, APB1 42 / timers 84 MHz, APB2 84 MHz | VERIFIED |
| stm32duino NUCLEO_F446RE clock: HSI → 180 MHz, APB1 45 (timers 90), APB2 90 (timers 180) | VERIFIED (local package 4.30000.0) |
| TIM2 PSC 8 → 10 MHz under stm32duino; Stefan's `kTimerHz` matches that, not his own 84 MHz config | VERIFIED (arithmetic) |
| USART2 921600 at PCLK1 45 MHz = 918 367 Bd (−0.35 %) | VERIFIED (arithmetic) |
| ST-LINK/V2-1 VCP supports 921600 and its actual rate ≈ 923 077 Bd | ASSUMED |
| PA4 (ENA), PB0, PB1 never configured; telemetry dead; baud mismatch; NVIC all 0 | VERIFIED |
| Runt pulse at stop with PWM1 + stop in update ISR | ASSUMED (needs scope or cycle-accurate model) |
| HBS86H enabled when ENA is open; DIR setup ≥ 5 µs; pulse ≥ 2.5 µs; ALM/PEND are opto open-collector | ASSUMED → R2 |
| FT/TTa status of the proposed pins (PA4/PA5 TTa, others FT) | ASSUMED → verify in DS10693 Table 10 |
| Nucleo B1 has an external pull-up; factory SB13/SB14 ON, SB62/SB63 OFF | ASSUMED (UM1724) |
| Morpho connector pin numbers in §1/§9 | ASSUMED (UM1724), Arduino positions VERIFIED from the variant header |
| Archives `stanok.7z` / `stepper_gui.7z` contain nothing beyond the extracted folders | VERIFIED (file lists), contents ASSUMED identical |
| PO's board revision / solder-bridge state, mechanics, switch types | UNKNOWN (Q-R1-01..09) |

### 10.3 Findings for other roles
- **Orchestrator / SRS**: B-05/B-06/B-11 → E-stop must be NC, interrupt-driven, independent of the PC, and per D-10 must not disable ENA; B-07 → requirement "no partial step pulse on stop"; B-18 → SW stop path bypasses the command queue; jog dead-man (§7).
- **Implementer A (FW) / 01_HW**: pin map §9.1 (pending Q-R1-01/03/08 and FT verification), build flags §6.2, timer scheme §9.3, priorities §9.4.
- **Implementer C (ICD)**: Stefan's command/flag/error set (§4) as a completeness checklist; add sequence numbers echoed in replies (B-14).
- **Implementer D (GUI)**: reuse list in §7.
