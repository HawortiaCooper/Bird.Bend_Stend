# Bird Bend Stand — Firmware design (STM32F446RE / NUCLEO-F446RE)

| Item | Value |
|---|---|
| Doc | `02_FW/docs/FW_design.md` |
| Version | **0.5** — M2 implemented (P2, D-39): ICD **v0.6** / dict 4 aligned (CR-01 STOP button retired, D-37 b feature-bit validity, D-40 a/c/d, seam v1.3 incl. `hal_meas_cmd`), M2 work breakdown and as-built notes in **§9.7**, NFR-007 static ISR budget analysis in **§9.8**, HW_MEAS measurement images (CR-02). · 0.4 — M1 implemented (P2, D-35): code in `02_FW/src`, host suites in `02_FW/test/test_impl_*`; implementation notes and deviations in **§9.6**; seam v1.1 (§8.1); D-36 noted. · 0.3 — final P1 design: ICD v0.3 + the v0.4 deltas (RESUME, TX order, E_INTERNAL codes, boot ENA rule, single-source seams), SRS v0.3 + D-31/D-32/D-33, Validator E review (`FW_test_plan.md` v0.1, DEF-P1-01…07, OBS-P1-01…15); no FW source yet (P2/M1 implements it, §9) |
| Date | 2026-10-04 |
| Owner | Implementer A (FW) |
| Binding inputs | SRS **v0.3** (172 requirements; new SAF-FW-024/025/026, FW-HOM-004, FW-SW-005, SYS-011). `ICD_protocol.md` **v0.3** + the **v0.4 deltas** in progress (§0.3/§0.4) (PROTO 1.0, PAYLOAD 1, dict_version 2, **PARAM_DICT_HASH 0xB046DD01**): STATUS 86 B with `pause_src`, homing only at START, `drv.k1_weld_ms`, the `protocol.yaml` name export (v0.2), and **D-30** (BLOCK bit 10 PAUSED, PAUSED cleared only by HALT_CLEAR, §6.5 clean-halt condition) (v0.3). `params.yaml` dict_version 2. `00_System/tools/README.md` (seams, vectors). DECISIONS D-01…**D-33** (**D-31** = RESUME command 0x3C; **D-32** = PO answers Q25–Q28; **D-33** = Orchestrator decisions on the P1 reviews): **D-27** = PFDE HBS86H clone, now "86 open-loop 6 A", 800 p/rev, no encoder feedback; **D-28** = R5 defaults, ENA settle 500 ms, ALM start-block; **D-29** = P1 reconciliation; **D-30** = PAUSED blocks motion, clean-halt substitution only at a step period > 2 ms **and** a stop distance ≤ 1 step. Research R1–R5. |
| Machine-readable companions | `00_System/specs/protocol.yaml` → `02_FW/src/gen/proto_gen.h` (names and codes); `params.yaml` → `02_FW/src/gen/params_gen.{h,c}`; `00_System/tools/vectors/protocol_vectors.json` and `check_vectors.json` (oracles, read in place, §8.4) |
| Related | `01_HW/pinout.md` v0.3 (pins, clocks, timers, NVIC), `01_HW/wiring.md` v0.3, `02_FW/docs/FW_test_plan.md` v0.1 (Validator E) |
| References (read-only, D-02) | `E:\Bavovna\Drone\Thrust_Stand_HAW` @ **9473c68** (`02_FW/**`), `E:\Bavovna\Drone\Stefan` |

Fact tags as in R1–R5: **VERIFIED** (read in a source or file, cited), **ASSUMED** (to be confirmed on target or in a datasheet), **UNKNOWN**. "TS" means Thrust_Stand_HAW @ 9473c68. Section references with a "§" and no prefix point into this document. "ICD §x" points into ICD_protocol.md, "pin §x" into `01_HW/pinout.md`, and "wir §x" into `01_HW/wiring.md`.

---

## 0. Conformance rules (new in v0.2)

### 0.1 No private names
The ICD and `params.yaml` name every command, payload field, status bit, EVENT code, stop cause, MOVE_DONE reason, NACK status and detail, BLOCK and FAULT bit, IO bit, retry class and parameter key. This document and the FW code use those names and nothing else. C identifiers come from the generated `src/gen/proto_gen.h` (prefixes from `protocol.yaml`; the file is present, `PROTO_ICD_VERSION` "0.3"). The FW never hand-writes a code value:

| Table (ICD) | C prefix (`proto_gen.h`) | Example |
|---|---|---|
| commands §3.2 | `CMD_` | `CMD_PAUSE` = 0x3B |
| STATUS codes §4.2 / E_BUSY / E_NVM detail | `ST_` / `BUSY_` / `NVMD_` | `ST_E_STATE`, `BUSY_ENABLING`, `NVMD_VERIFY` |
| BLOCK mask §4.3 | `BLOCK_` (mask), `BLOCK_<N>_BIT` (index) | `BLOCK_DRV_UNPOWERED` |
| STOP mode, MOVE_UNTIL_LOAD cmp, HOME flags | `STOPMODE_`, `CMP_`, `HOMEF_` | `STOPMODE_CONTROLLED`, `CMP_LE`, `HOMEF_LOAD_CONFIRMED` |
| motion state §6.1, home phase, latch source | `MS_`, `HP_`, `SRC_` | `MS_STOPPING`, `HP_SLOW_APPROACH`, `SRC_BUTTON` |
| reset cause, sys_flags, feature_mask | `RST_`, `SYSF_`, `FEAT_` | `RST_IWDG`, `SYSF_CFG_DIRTY`, `FEAT_AFE_SYNTHETIC` |
| DATA flags / DATA status / FAULT / IO §7.6 | `DF_`, `DS_`, `FAULT_`, `IO_` | `DF_OVERRUN`, `DS_POS_UNCERTAIN`, `FAULT_K1_WELDED`, `IO_ENA_DISABLED` |
| EVENT §8.1 and its arg enums | `EV_`, `HF_`, `DD_`, `PCLR_`, `PDEF_`, `LIM_` | `EV_MOVE_DONE`, `DD_DRV_POWER_LOST` |
| stop causes §8.2, MOVE_DONE reasons §8.3 | `SC_`, `MD_` | `SC_PC_PAUSE`, `MD_JOG_ZERO` |
| constants §2–§7 | `PROTO_` | `PROTO_MAX_LEN` = 160, `PROTO_STATUS_LEN` = 86, `PROTO_JOG_NO_BOUND` |
| parameters (Appendix A) | `PID_`, `params_t` members (`params_gen.h`) | `PID_DRV_K1_WELD_MS`, `p->motion.v_max_load_um_s` |

Appendix A lists every private v0.1 name and the ICD name that replaces it.

### 0.2 Oracles
- `protocol_vectors.json` is the oracle for bytes: CRC, every frame and the parser `streams`.
- `check_vectors.json` is the oracle for command acceptance: check order, BLOCK masks, clears, NVM, every parameter's min/max/type/padding/moving case, and hard rules **H1–H4**.
- When the ICD text and a vector disagree, the vector wins and the finding goes to the Integrator (ICD §0.1).
- FW host tests consume both files **in place** (§8.4).
- `ref_cmdcheck.FwState` is mirrored field by field in the pure command-check context (§5.4.2), so each check vector maps 1:1 onto a C test case.

### 0.3 ICD v0.2 / v0.3 / v0.4 content the design implements

| Item | Source | ICD | FW design |
|---|---|---|---|
| **Homing only at START** | D-29 b | v0.2 | `home.ref_switch` (0x0401) is retired in dict_version 2. Homing in `homing.c` is START-only (§5.5); reaching END during HOME means `HOME_WIRING`. |
| **PAUSED source** | D-29 a | v0.2 | The PAUSED latch stores `pause_src` (`SRC_PC` / `SRC_BUTTON`). It is packed in STATUS at offset 84 (u8), with offset 85 reserved = 0, so `PROTO_STATUS_LEN` = 86. EVENT PAUSED is sent on a 0 → 1 transition only. |
| **PAUSED blocks motion** | **D-30** | **v0.3** | While PAUSED, MOVE_ABS, MOVE_UNTIL_LOAD, HOME and JOG ≠ 0 (**including refreshes of a running jog**) are refused with `ST_E_STATE` + **`BLOCK_PAUSED`** (bit 10, 0x0400). This is a state check, so it is evaluated before the busy check. STOP, HALT, PAUSE, JOG 0, ENABLE, DISABLE and every clear are still accepted. PAUSED is cleared **only by HALT_CLEAR** (EVENT PAUSE_CLEARED `PCLR_HALT_CLEAR`); a motion command never clears it. This replaces the v0.2 rule "an accepted motion command clears PAUSED" and closes OI-ICD-04. |
| **K1_WELDED** | D-29 c, SAF-FW-025 | v0.2 | Latched after `drv.k1_weld_ms` (parameter 0x0705, default **200 ms**, 100…2000; FAULT_SET value = ms) (§5.7.2). |
| **DRV_PWR lost** | D-29 c, SAF-FW-024, FW-SW-005 | v0.2 | Applies to **any** cause, whether or not the E-stop is open. Detection uses a 20 ms stability filter and the reaction comes ≤ 25 ms after the input change: stop, ENA disabled, NOT_ENABLED, HOMED and VALID cleared. Motion and ENABLE are refused (`BLOCK_DRV_UNPOWERED`) until power returns. The settle time runs from the later of ENABLE and the power return (§5.7.2). |
| **ALM start-block scope** | SAF-FW-026 | v0.2 §4.3 | DRIVER_ALARM only blocks **new** starts: MOVE_ABS, MOVE_UNTIL_LOAD, HOME, and JOG ≠ 0 when not jogging. Refreshes of a running jog, JOG 0, STOP, HALT, PAUSE, ENABLE, DISABLE and the clears are not blocked (§5.4.2). |
| **Slow controlled stop** | D-29 d, **D-30**, SAF-FW-003 | v0.3 §6.5, OI-ICD-07 | A CLEAN halt replaces the controlled stop **only when** the step period is > 2 ms **and** the planned stop distance v²/(2·a_stop) ≤ 1 step (the FW computes it). Otherwise the FW decelerates as planned (§5.6.4). This closes OI-ICD-05. |
| **Speed split** | D-29 e | v0.1 | `motion.v_max_travel_um_s` / `motion.v_max_load_um_s` with H4 (§5.4.3). |
| **Name export** | GF-08, OI-FW-11 | v0.2 | The FW includes `gen/proto_gen.h` and nothing hand-written (§0.1). |
| **RESUME command** | **D-31**, OBS-P1-15 | **v0.4 (in progress)** | `CMD_RESUME` = 0x3C, LEN 0. It clears **only** PAUSED and is refused with `ST_E_STATE` while HALT, ESTOP or any fault is latched. It is not in the stop sniffer. HALT_CLEAR clears HALT **and** PAUSED. Design in §5.3; names follow `proto_gen.h` v0.4 when it lands (§0.4). |
| **AFE stale timeout** | **D-33 a**, DEF-P1-01 | v0.4 / dict 3 (Integrator) | `afe.timeout_ms` default **250 ms**, plus hard rule **H5**: `afe.timeout_ms` ≥ 2 × the configured conversion period (§5.15) |
| **Flash op vs DRV_PWR / K1** | **D-33 f**, DEF-P1-06, OBS-P1-02 | SRS v0.4 | DRV_PWR reaction ≤ the NVM operation time + 25 ms during an erase/program; otherwise ≤ 25 ms. K1_WELDED latches ≤ `drv.k1_weld_ms` + 25 ms (design: + 2 ms) (§5.7.2) |
| **Idle disable while stale** | **D-33 g**, OBS-P1-05 | SRS v0.4 | no idle disable while the AFE is stale; the idle counter restarts (§5.7.1) |
| **Limit latch release** | **D-33 h**, OBS-P1-06 | SRS v0.4 | LIMIT_x clears once the input has been released for `io.release_ms`; while latched only motion away from the switch is accepted (`BLOCK_LIMIT`) (§5.3) |
| **Load-step bound** | D-32 Q26, D-33 d | (SW) | no FW change: MOVE_UNTIL_LOAD accepts a bound equal to the soft limit (inclusive), and the END switch remains the backstop (§5.4.4) |

The FW reports the ICD version it implements in `INFO.proto_major/minor`. The pre-script (§8.4) refuses to build host tests when `vectors/*.json:icd_version` or `param_dict_hash` differ from the generated headers (`PROTO_ICD_VERSION`, `PARAM_DICT_HASH`), so a stale combination cannot pass. P2 code is written against ICD v0.4 (`PROTO_ICD_VERSION` "0.4", with RESUME).

### 0.4 Names expected from ICD v0.4 — **confirmed** by `proto_gen.h` (ICD v0.4.1)
v0.4: `CMD_RESUME` 0x3C (retry class VERIFY, D-34), `PCLR_RESUME` = 3, `INTERNAL_NOT_IN_BUILD` = 1 / `INTERNAL_INVARIANT` = 2, H5 in `params.yaml` dict 3 (`afe.timeout_ms` default 250), `PROTO_HOME_RELEASE_MAX_UM` / `PROTO_HOME_SLOW_EXTRA_UM` (OI-FW-23). The table below is kept for history.
Until then, these are the provisional identifiers the design uses. If v0.4 chooses others, the design adopts them verbatim; only this table and the text below it change, not the logic.

| Item | Provisional identifier | Note |
|---|---|---|
| RESUME command | `CMD_RESUME` = 0x3C, LEN 0, retry class ONCE_PRIORITY (proposed) | priority path in the SW, like the clears |
| PAUSE_CLEARED reason for RESUME | `PCLR_RESUME` (code from v0.4; code 1 is unused since v0.3) | – |
| E_INTERNAL details | `INTERNAL_NOT_IN_BUILD` = 1, `INTERNAL_INVARIANT` = 2 (OI-FW-21) | – |
| H5 | `afe.timeout_ms` ≥ 2 × period(`afe.rate_sps`) | E_CONFIG detail = id of the other parameter |

---

## 1. Architecture overview

```
 +---------------------------------------------------------------------------------------------------+
 | main loop   link_poll (parser → cmd dispatch) · events_flush · status_service · params_apply ·    |
 |             nvm_service (non-blocking state machine) · led · wdg_service                          |
 +---------------------------------------------------------------------------------------------------+
 | core/  (C11, uses ONLY the hal_*.h seams — compiled for the target AND for the host twin)        |
 |   motion (glue: motion_sm + homing + stepgen core) · safety (1 kHz tick: debounce, latches,      |
 |   timeouts, drvmon) · afe (sample callback: load limit, MOVE_UNTIL_LOAD, DATA build) · link       |
 |   (parser, dispatcher, TX classes, stop sniffer) · cmd (handlers) · stream (DATA/fallback, VALID) |
 |   · events · params_rt (apply hooks, REBOOT_PENDING) · nvm (service) · status                    |
 +---------------------------------------------------------------------------------------------------+
 | pure/  (C11, no HAL, no globals — host unit tests, env:native)                                    |
 |   crc16 crc32 le frame payload cmd_check param_rules nvm_log units ramp planner stepgen_core      |
 |   stepgen_halt.h motion_sm homing latches debounce drvmon loadlim afe_rate hx711_seq hx711_math   |
 |   valid flags evq txsched stop_sniff stream_sched resetcause                                      |
 | gen/   params_gen.h/.c  proto_gen.h   (GENERATED by the Integrator — never hand-edited)           |
 +---------------------------------------------------------------------------------------------------+
 | hal/   hal_uart.h hal_time.h hal_step.h hal_inputs.h hal_outputs.h hal_hx711.h hal_flash.h        |
 |        hal_sys.h  = the eight host-twin SEAMS (tools/README; exact API §8.1)                      |
 |   hal/f446/  register-level target implementation (LL/CMSIS); safety ISRs + flash loop in RAM     |
 |   (twin implementation of the seams: Integrator, 00_System/tools/fw_twin/ — not in 02_FW)        |
 +---------------------------------------------------------------------------------------------------+
 | stm32duino core 4.30000.0 (startup, HAL_Init, SysTick) · CMSIS · STM32F4 LL headers              |
 +---------------------------------------------------------------------------------------------------+
```

Principles (each is traced in §10):
1. **Raw data out, safety in** (SYS-002). The FW streams raw counts and the commanded position. Every FW safety function works with the PC absent and the stream off: E-stop sense, STOP/PAUSE buttons, limits, FW load limit, AFE stale, link watchdog, jog dead-man, idle disable, driver-power loss, K1 weld detection and IWDG. The FW does no force scaling.
2. **Stops are hardware-first.** Every immediate stop is one call of the stop primitive (`hal_step_stop_now()` = CLEAN, `hal_step_abort()` = TRUNCATE, §5.3), made **inside the ISR that detects the cause**:
   - the input EXTI ISRs perform their fixed reaction in the HAL, RAM-resident, before calling the core;
   - the HX711 sample callback runs in its ISR;
   - the step ISR and the tick handle the rest.
   Controlled stops start ≤ 2 ms after the trigger (§5.6.4).
3. **Motion disabled by default** (D-13, SAF-FW-018). After any reset the axis is "NOT_ENABLED, not homed" and no PUL edge occurs until ENABLE + settle. ENA stays at "no current" (driver holding), unless the E-stop sense is open or the driver power is off (§3.2).
4. **No dynamic allocation** (NFR-005), no `printf`, no `HardwareSerial`/`HardwareTimer`/`attachInterrupt`. `tools/check_map.py` fails the build if any of them is linked.
5. **No blocking > 1 ms in the main loop** (NFR-006). The only exception is the flash erase/program pass (idle only, ≤ 0.6 s). TX quiescing before NVM is a state machine, not a wait.
6. **All decision logic is pure and host-tested** (`src/pure/`). ISR bodies are thin: read hardware → call a pure function → write hardware.
7. **Only `hal/f446` touches registers.** `core/` and `pure/` compile unchanged in the host twin (D-07, SYS-008).
8. **One language.** `pure/`, `core/` and `hal/f446/` are C11, so the twin builds with plain host gcc. C++ is used only in `main.cpp`, because the stm32duino `setup()/loop()` needs it (v0.1 had a C++ core; changed for the twin).
9. **Open-loop driver (D-27).** The FW has no step-loss detection and never infers one from ALM (§5.16).

---

## 2. Project layout, build and reuse

### 2.1 Source tree (names binding for P2)

```
02_FW/
  platformio.ini                     (§2.2)
  ldscript/bend_f446re.ld            variant ldscript + 32 KB hole for NVM sectors 1-2 + .noinit (§5.11)
  tools/  build_info.py  check_map.py  gen_test_vectors.py  host_env.ps1  native_flags.py
  include/  board_pins.h (= pinout.md §1)  irq_prio.h (= pinout.md §4)  fw_config.h (FW constants, §2.5)
  src/
    main.cpp                         setup() -> app_init(); loop() -> app_loop()      (only C++ file)
    gen/      params_gen.h/.c  proto_gen.h                     GENERATED (Integrator) — never edited here
    pure/     (§2.4)
    core/     app.c motion.c safety.c afe.c link.c cmd.c stream.c events.c params_rt.c nvm.c status.c led.c
    hal/      hal_uart.h hal_time.h hal_step.h hal_inputs.h hal_outputs.h hal_hx711.h hal_flash.h hal_sys.h
    hal/f446/ clock.c board_init.c time_tim5.c step_tim2.c exti.c uart2_dma.c hx711_shim.c afe_synth.c
              flash_f4.c iwdg.c sys_f4.c (reset cause, UID, faults, stack paint) dwt.h
  test/
    test_impl_<suite>/               Implementer A host suites (Unity), §8.5
    common_impl/                     A's test helpers (hex parsing, fake seams for core tests)
    (Validator E suites: test_val_*  — not owned by A)
  docs/FW_design.md                  this document
```
- Allowed includes in `pure/` and `gen/`: `<stdint.h> <stdbool.h> <stddef.h> <string.h> <math.h>`. Allowed in `core/`: the same plus `hal/*.h`, `pure/*.h` and `gen/*.h`.
- Traceability tag in every file: `/* Implements: FW-MOT-003 */`.
- Reused files carry `/* Origin: Thrust_Stand_HAW/02_FW/src/... @<commit> */` (D-02, SYS-010). D-29 m allows the TS HEAD; the actual hash is recorded.

### 2.2 PlatformIO environments (`02_FW/platformio.ini`, created in M1-WP0, §9)

```ini
[platformio]
default_envs = nucleo_f446re

[env]
build_flags = -DFW_VERSION_MAJOR=0 -DFW_VERSION_MINOR=1 -DFW_VERSION_PATCH=0

[target]
platform = ststm32@20.0.0                                   ; VERIFIED installed locally
platform_packages =
    platformio/framework-arduinoststm32@4.30000.0           ; VERIFIED installed
    platformio/toolchain-gccarmnoneeabi@1.120301.0          ; VERIFIED installed (GCC 12.3)
board = nucleo_f446re
framework = arduino
upload_protocol = stlink                                     ; never used without PO approval (D-06)
board_build.ldscript = ldscript/bend_f446re.ld
build_unflags = -Os
build_flags =
    ${env.build_flags}
    -O2 -fstack-usage -std=gnu11
    -DHAL_UART_MODULE_ONLY -DHAL_TIM_MODULE_ONLY -DHAL_EXTI_MODULE_DISABLED
    -DHAL_ADC_MODULE_DISABLED -DHAL_DAC_MODULE_DISABLED -DHAL_RTC_MODULE_DISABLED -DHAL_PCD_MODULE_DISABLED
    -DTICK_INT_PRIORITY=5 -DHSE_VALUE=8000000U -DPARAMS_GEN_WITH_KEYS=0
    -Wl,--print-memory-usage -Wl,-Map,${BUILD_DIR}/firmware.map
build_src_flags = -Iinclude -Isrc -Isrc/pure -Isrc/gen -Isrc/core -Isrc/hal -Isrc/hal/f446
                  -Wall -Wextra -Wshadow -Werror=return-type
extra_scripts =
    pre:tools/build_info.py       ; BUILD id, git hash; runs gen_params.py --check (stale gen/ → build error)
    post:tools/check_map.py       ; forbidden symbols, handler slots, nothing in NVM sectors 1-2, .RamFunc set

[env:nucleo_f446re]               ; release image (M1: FEAT_AFE_SYNTHETIC | FEAT_NVM)
extends = target

[env:nucleo_f446re_debug]         ; scope markers PC8/PC9, IWDG frozen on halt
extends = target
build_type = debug
build_flags = ${target.build_flags} -DFW_DEBUG_PINS=1 -DFW_DBG_FREEZE_WDG=1

[env:native]                      ; host unit tests: pure + gen + core against fake seams (Unity)
platform = native
test_framework = unity
build_src_filter = -<*> +<pure/> +<gen/> +<core/>
test_build_src = yes
build_flags = -DHOST_TEST=1 -O2 -Wall -Wextra -Werror -Wconversion -Wshadow
              -Isrc -Isrc/pure -Isrc/gen -Isrc/core -Isrc/hal -Itest/common_impl
extra_scripts =
    pre:tools/native_flags.py      ; CFLAGS -std=c11 (C) / CXXFLAGS -std=c++17 (Unity runner only)
    pre:tools/gen_test_vectors.py  ; vectors/*.json → $BUILD_DIR/vectors/*.h, read IN PLACE (§8.4)
```
- Host compiler: **CLion-bundled MinGW GCC 13.1** (`C:\Program Files\JetBrains\CLion 2025.3.2\bin\mingw\bin`, VERIFIED `gcc --version` = 13.1.0). It is not on PATH. `tools/host_env.ps1` prepends it, and PlatformIO 6.2 comes from the project `.venv` (VERIFIED `.venv\Scripts\pio.exe`). Commands are in §9.4.
- Own interrupt handlers, with slots asserted by `check_map.py`: `TIM2_IRQHandler`, `TIM5_IRQHandler`, `EXTI0_IRQHandler`, `EXTI1_IRQHandler`, `EXTI4_IRQHandler`, `EXTI9_5_IRQHandler`, `EXTI15_10_IRQHandler`, `DMA1_Stream5_IRQHandler`, `DMA1_Stream6_IRQHandler`, `USART2_IRQHandler`, `HardFault_Handler`, `NMI_Handler`. The core defines none of the DMA1 stream handlers (VERIFIED grep), and the flags above compile out its TIM/EXTI handlers (guards VERIFIED in v0.1).

### 2.3 Disabled core features
Unchanged from v0.1: `HardwareSerial`, `HardwareTimer`/`tone`/`Servo`, `attachInterrupt`, the variant `SystemClock_Config()` (strong override), SysTick at level 5, `LED_BUILTIN`/`USER_BTN` never referenced.

### 2.4 Module list and reuse (origin = TS)

| Module | Layer | Origin / verdict | Content (v0.2) | Req. | MS |
|---|---|---|---|---|---|
| `crc16`, `crc32`, `le.h` | pure | **copy as-is** `02_FW/src/pure/` | CRC-16/CCITT-FALSE = ICD §2.1 (check 0x29B1); CRC-32 for NVM | IF-004, FW-NVM-002 | M1 |
| `proto.h` | pure | **thin wrapper** | `#include "proto_gen.h"`; static asserts of struct sizes vs `PROTO_*_LEN` | IF-001 | M1 |
| `frame` | pure | **copy, constants from `proto_gen.h`** `02_FW/src/pure/frame.*` | ICD §2.3 parser: buffer ≥ `PROTO_RX_BUF_MIN` (336), drop **one** byte on LEN > 160 / CRC error / 20 ms timeout; counters `frames_ok`, `crc_errors`, `len_errors`, `timeout_drops`, invalid TYPE → `rx_frame_errors` | IF-003/004, FW-CMD-001 | M1 |
| `payload` | pure | **adapt** `02_FW/src/pure/payload.*` | encode INFO (44), STATUS (**86**), DATA (18), EVENT (16), PARAM_ENTRY (7), NACK (3), GET_ALL_PARAMS page; decode every request §3.2 | IF-006, FW-STR-003 | M1 |
| `cmd_check` | pure | **rewrite with TS pattern** | ICD §4.4 order; context = mirror of `FwState` (§5.4.2); BLOCK mask §4.3; speed cap §5.4.3; output `{status, detail}` | FW-CMD-001, SAF-FW-020 | M1 |
| `param_rules` | pure | **rewrite (tiny)** | H1–H4 (ICD §11.4), detail = other id; whole-image check for NVM rule 5 (§5.15) | FW-CFG-003, SAF-FW-010 | M1 |
| `nvm_log` | pure | **adapt** `02_FW/src/pure/nvm_codec.*` | record codec, two-sector slot log, boot rules ICD §11.3, CFG_DIRTY ICD §11.2 | FW-NVM-001/002 | M1 |
| `units` | pure | **new** (R4 §2.2) | µm ↔ steps, round half away from zero; speed/accel to steps | FW-MOT-002, SYS-003 | M1 |
| `valid` | pure | **new** | SET_VALID boundary `(i32)(t − t_apply) ≥ 0` (ICD §5.3), auto-clear with a stop cause | FW-CMD-002, SAF-FW-001 | M1 |
| `flags` | pure | **new** | composition of DATA `flags`/`status`, STATUS `io`/`faults`/`sys_flags` from the state | FW-STR-003, FW-CMD-004 | M1 |
| `evq` | pure | **adapt** `02_FW/src/link/events.*` | 16-entry EVENT ring, u8 event counter = EVENT SEQ incl. dropped events, `event_overflows` | FW-STR-006 | M1 |
| `txsched` | pure | **new** | TX class selection and drop policy (§5.9.4) | FW-STR-002/004, IF-011 | M1 |
| `stop_sniff` | pure | **new** | finds CRC-valid STOP (9 B), HALT (8 B) and PAUSE (8 B) frames in new RX bytes (§5.9.3) | SAF-FW-002/003 | M1 |
| `stream_sched` | pure | **copy** TS `stream_sched` | fractional fallback rate `stream.fallback_hz` | FW-STR-005 | M1 |
| `afe_rate` | pure | **new** | median of 16 DOUT periods, 0.1 SPS, `AFE_RATE_MISMATCH` vs `afe.rate_tol_pct` | FW-AFE-004 | M1 (synthetic) |
| `resetcause` | pure | **new** | RCC->CSR bits → `RST_*` (§5.1) | SAF-FW-018 | M1 |
| `latches` | pure | **adapt heavily** TS `safety_sm.*` | ICD §6.2 latch/clear table, cause-present evaluation, VALID auto-clear, EVENT emission (§5.3) | SAF-FW-001…023, FW-CMD-003 | M1 (no-motion subset) / M2 |
| `debounce` | pure | **new** | first-edge act, release stable ≥ `io.release_ms`, E-stop closed ≥ `io.estop_release_ms` counters | FW-SW-001…003, SAF-FW-006 | M2 |
| `drvmon` | pure | **new** | DRV_PWR 20 ms filter + loss reaction, K1 weld timer (`drv.k1_weld_ms`), ALM/PEND state, NOT_SETTLED timer (§5.7.2) | SAF-FW-024/025/026, FW-SW-004/005 | M2 |
| `loadlim` | pure | **new** | per-sample compare, rails, `load_trip_samples`, regrow after clear, MOVE_UNTIL_LOAD `cmp` | SAF-FW-008…011, FW-MOT-006 | M2 |
| `ramp`, `planner` | pure | **new** (R4 §1.5, §12 TV-M) | exact sqrt ramp with fractional carry, virtual index, trapezoid/triangle | FW-MOT-003 | M2 |
| `stepgen_core` | pure | **new** | next-period logic of the step ISR, retarget, last-step arm, controlled-stop path choice (§5.6) | FW-MOT-001…003, SAF-FW-003/004 | M2 |
| `stepgen_halt.h` | pure | **new, static inline** | CLEAN/TRUNCATE decision from (CNT, CCR1, ARR); inlined into the RAM-resident HAL halt | SAF-FW-002/004 | M2 |
| `motion_sm` | pure | **new** (patterns: Stefan `axis.hpp`, TS `safety_sm`) | ICD §6.1 states, transitions, MOVE_DONE reasons (§5.4) | FW-MOT-004…009 | M2 |
| `homing` | pure | **new** (Stefan `axis.hpp:534-564`, R4 §3.1) | START-only phases `HP_*`, failures, drift check (§5.5) | FW-HOM-001/002/004 | M2 |
| `hx711_seq.h`, `hx711_math` | pure | **copy** / **adapt** TS `02_FW/src/pure/hx711_*` | single channel, µs timestamps, settle flagging | FW-AFE-001…003 | M2 |
| `hx711_shim.c` | hal/f446 | **port** TS `02_FW/src/sens/hx711.cpp` | EXTI4 on PB4, SCK PB10, RATE PB5; t_us + position latch at entry | FW-AFE-001/005 | M2 |
| `afe_synth.c` | hal/f446 | **new** | M1: 80 Hz synthetic samples from TIM5 CC2 through the same `on_afe_sample()` path | FW-STR-002 (M1) | M1 |
| `uart2_dma.c` | hal/f446 | **port** TS `02_FW/src/drv/uart_dma.*` | DMA1 S5 RX circular 2 KB; S6 TX **one frame per transfer**, 3 TX classes (§5.9) | IF-002, FW-STR-002 | M1 |
| `link.c`, `cmd.c`, `stream.c`, `events.c`, `status.c` | core | **adapt** TS `02_FW/src/link/*` | §5.9, §5.10, §5.14 | FW-CMD-*, FW-STR-* | M1 |
| `params_rt.c` | core | **adapt** TS `02_FW/src/cfg/params.*` | apply hooks, moving gate, REBOOT_PENDING (§5.15) | FW-CFG-001…003 | M1 |
| `nvm.c`, `flash_f4.c` | core / hal | **adapt** TS `cfg/nvm.*`; **rewrite** `flash_f1` → F4 sectors | §5.11 | FW-NVM-001…003 | M1 |
| `clock.c`, `iwdg.c`, `sys_f4.c`, `dwt.h` | hal/f446 | **adapt** TS `02_FW/src/sys/*` | F446 PLL + over-drive, CSR bits incl. BORRSTF, IWDG windows, BASEPRI levels | FW-PLT-002, SAF-FW-019 | M1 |
| `exti.c` | hal/f446 | **adapt** TS `sys/estop_hw.cpp`, `exti_shared.cpp` | RAM-resident level-0/1 handlers with fixed reaction (§5.2), clear only own PR bits | FW-SW-001…004 | M2 |
| `step_tim2.c`, `time_tim5.c` | hal/f446 | **new** (R4 §1.4) | §5.6, §5.1 | FW-MOT-001, FW-TIM-001 | M2 / M1 |
| `platformio.ini`, `irq_prio.h`, `tools/*.py` | – | **adapt** TS | §2.2; `check_map.py` objdump address fix (TS DEF-M3r2f-01) | FW-PLT-001 | M1 |
| Stefan code | – | **nothing copied** (D-09) | knowledge only: pinout, timer start sequence, homing phases, STOP/HALT split | – | – |

### 2.5 FW constants (`include/fw_config.h`, not parameters)
| Constant | Value | Why fixed |
|---|---|---|
| `RX_RING_BYTES` / `RX_PARSE_BYTES` | 2048 / 512 | ICD §9.1 (≥ 1 KB), §2.3 (≥ 336) |
| `TX_R_BYTES` / `TX_E_SLOTS` / `TX_D_SLOTS` | 1024 / 16 / 2 | §5.9.4; the dispatcher takes a frame only if R has room for `TX_R_RESERVE` = 168 B (DEF-P1-07) |
| `DRV_PWR_FILTER_MS` | 20 (1 kHz samples, both directions) | FW-SW-005 fixes 20 ms; reaction ≤ 25 ms (SAF-FW-024), or ≤ NVM op time + 25 ms during a flash operation (D-33 f), §5.7.2 |
| `ALM_FILTER` | ALM polled at 1 kHz: active at the first active sample, inactive after a stable `io.release_ms` | OBS-P1-10: no EXTI on ALM; chatter cannot flood level 1 or the EVENT ring (§5.2) |
| `SNIFF_HOLD_MAX_MS` | 20 (= inter-byte timeout) | sniffed-stop hold (§5.9.3, DEF-P1-04) |
| `HOME_RELEASE_MAX_UM` | 10 000 | RELEASE/BACKOFF bound: a START switch not released within this travel means `HOME_WIRING` (stuck switch or inverted DIR) — **OI-FW-23** (ICD text) |
| `CTRL_STOP_ISR_MAX_TICKS` / `CTRL_STOP_HALT_MIN_TICKS` | 1 ms / 2 ms in TIM2 ticks | controlled-stop path choice (§5.6.4, D-29 d / D-30; the halt also needs a stop distance ≤ 1 step) |
| `HX711_SCK_HIGH_MAX_US` | 50 | power-down detection (FW-AFE-003) |
| `E_INTERNAL` details | 1 = NOT_IN_BUILD (command implemented in a later milestone; the feature bit is 0), 2 = INVARIANT (internal check failed; nothing executed) | ICD §4.2 says "implementation-defined" — **OI-FW-21** asks to register them in `protocol.yaml` |

---

## 3. Clock, boot sequence and safe state

### 3.1 Clock override (FW-PLT-002)
Unchanged from v0.1:
- HSE bypass from the ST-LINK MCO, with a 5 ms DWT-bounded wait, PLL M4/N180/P2, over-drive, 5 WS, APB1/APB2 45/90 MHz.
- Fallback: the same PLL from HSI, giving identical bus clocks; this sets `SYSF_CLK_FALLBACK` and queues EVENT CLK_FALLBACK at link start.
- CSS is on in the HSE case: the NMI forces safe outputs (`hal_step_abort()`), writes a `.noinit` record and resets.
- Every constant is derived from `HAL_RCC_GetPCLKxFreq()`.

### 3.2 Boot sequence (`app_init()`)

| Step | Action | Time after reset |
|---|---|---|
| 0 | Reset. All pins Hi-Z (PB4 = NJTRST AF0 with PU; PA13/14 SWD). Driver inputs see **no LED current**: PUL idle, DIR "0", **ENA = driver enabled/holding** (D-13). RATE is held at 80 SPS by its external pull-up. | 0 |
| 1 | Core startup (`.data/.bss`, `.RamFunc` copy) → `premain()` (NVIC group 4) → `HAL_Init` → §3.1 clock | ≤ 10 ms |
| 2 | Reset cause: `RCC->CSR` → pure `resetcause()` → `RST_*`, then `RMVF`. Read the fault record from `.noinit` (magic + CRC). | µs |
| 3 | Stack painting, DWT, all NVIC priorities (pin §4) **before** any IRQ is enabled; TIM5 time base started without IRQ | µs |
| 4 | `params_set_defaults()` → `nvm_boot()` (ICD §11.3 rules 2–5, §5.11). Session parameters (`nvm: false`) at defaults. | ≤ 2 ms |
| 5 | **Inputs**: GPIO inputs with internal pull-ups, `SYSCFG->EXTICR`, both edges, then **read initial levels**:<br>• E-stop open → ESTOP latched;<br>• START/END active → status LIMIT_x (input active);<br>• both active → FAULT LIMIT_WIRING;<br>• (v0.5: no STOP input, D-36 / CR-01; a PAUSE button held at boot is not a press);<br>• DRV_PWR, ALM, PEND sampled;<br>• E-stop open **and** DRV_PWR present → the K1 timer starts (SAF-FW-007/018). | µs |
| 6 | **Outputs** (ODR written before MODER):<br>• **ENA** = disabled level if the E-stop input is open **or** (`drv.pwr_sense_enable` and DRV_PWR reads off), else the "no current" level (= enabled/holding, D-13), per `motion.ena_invert`;<br>• **DIR** = 0;<br>• **PUL**: TIM2 OC1 *forced inactive*, then PA0 → AF1;<br>• **SCK** low; **RATE** per `afe.rate_sps`; TRIP low; LED; unused pins analog. | ≤ 12 ms |
| 7 | `motion_state` = `MS_NOT_ENABLED`; HOMED = 0; VALID = 0; stream off; `pos_steps` = 0 | – |
| 8 | Link: USART2 + DMA1 S5/S6, parser, TX classes. EVENT queue: BOOT (arg `RST_*`), CLK_FALLBACK if any, the NVM result (PARAMS_LOADED / PARAMS_DEFAULTED, §5.11). | – |
| 9 | AFE: EXTI4 (M2) or the synthetic source (M1) enabled; settle-discard armed | – |
| 10 | EXTI lines of the inputs enabled (pending bits cleared first; the boot latches of step 5 already cover active inputs) | – |
| 11 | Control tick (TIM5 CC1, 1 kHz) enabled | – |
| 12 | IWDG started, run window 32–90 ms (§5.13) | ≈ 15 ms |
| 13 | `loop()` | – |

ENA at boot with the driver power off is disabled. This matches the ICD §6.2 "driver power lost" row: the driver must come up disabled when K1 is reset. ICD §6.4 names only the E-stop case, so the v0.2 text should add the DRV_PWR case (**OI-FW-22**). Hi-Z until step 6 is exactly the reset state, so nothing can pulse before step 6.

### 3.3 Safe state per situation

| Situation | PUL | DIR | ENA | Notes |
|---|---|---|---|---|
| Reset → boot step 6 | Hi-Z (idle) | Hi-Z | Hi-Z = enabled/holding (D-13) | – |
| Boot, E-stop open or DRV_PWR off | idle | 0 | **disabled level** | SAF-FW-018, D-29 c |
| NOT_ENABLED / IDLE | idle (OC1 forced inactive, CEN = 0) | last | per state | – |
| Any operational stop (ICD §6.2) | idle ≤ 1 pulse completion (CLEAN) | unchanged | **unchanged** (holding) | SAF-FW-001, D-10/D-11 |
| E-stop sense open | idle immediately (TRUNCATE) | unchanged | **disabled** ≤ 1 µs after ISR entry | SAF-FW-005 |
| DRV_PWR lost (sense enabled, any cause) | idle (CLEAN) | unchanged | **disabled** (≤ 25 ms) | SAF-FW-024 |
| HardFault / NMI | `hal_step_abort()`, then reset | – | unchanged | `.noinit` record |
| Main loop or ISR hung | last period at most until the IWDG reset (≤ 90 ms) | – | – | SAF-FW-019 |
| NVM erase (idle only) | idle | – | per state; E-stop ISR runs from RAM (§5.11) | FW-NVM-003 |

---

## 4. Scheduling model

### 4.1 Execution contexts

| Context | Level | Trigger / rate | Work | Budget |
|---|---|---|---|---|
| `EXTI15_10_IRQHandler` (RAM) | 0 | E-stop sense edge | **HAL fixed reaction**, open edge: `hal_step_abort()` (TRUNCATE) + ENA → disabled (one BSRR write). Then `on_input_edge(IN_ESTOP, level, t_us)` → core latch (deferred during NVM, §5.11). | ≤ 0.5 µs (+ core ≤ 1 µs) |
| `EXTI0/EXTI1_IRQHandler` (RAM) | 1 | START / END edge | HAL fixed reaction, active edge: `hal_step_stop_now()` (CLEAN, a no-op when stopped), mask own line. Core: capture `hal_step_count()` + t_us, homing edge or LIMIT latch. | ≤ 1 µs |
| `EXTI9_5_IRQHandler` (RAM) | 1 | STOP (7), PAUSE (6) | STOP active: CLEAN halt, mask line. Core: HALT latch. PAUSE: mask line; core request flag. (ALM is **polled** in the tick since v0.3, OBS-P1-10.) | ≤ 1 µs |
| `TIM2_IRQHandler` | 2 | each completed PUL pulse | HAL counts the step, calls `step_isr()` → pure `stepgen_core` (next period / last / stop), overrun check (§5.6) | ≤ 1.2 µs |
| `EXTI4_IRQHandler` (M2) / TIM5 CC2 synthetic (M1) | 3 | HX711 DOUT falling (80 Hz) | t_us + position latch, bit-bang read; `on_afe_sample()`: load limit, MOVE_UNTIL_LOAD, **DATA frame build + TX class D** (§5.14) | ≤ 60 µs |
| `TIM5_IRQHandler` (CC1) | 4 | 1 kHz control tick | §4.3 | ≤ 30 µs typ, ≤ 60 µs worst |
| `DMA1_Stream5/6`, `USART2`, SysTick | 5 | RX laps, TX frame done, line errors, HAL tick | lap counter; next TX frame via `txsched` (§5.9.4); error counters | ≤ 2 µs |
| main loop | thread | continuous | §4.2 | ≤ 1 ms per pass |

### 4.2 Main loop (one pass, `app_loop()`)

```
t0 = hal_time_us()
link_poll()          RX bytes -> frame parser -> cmd_dispatch() per complete frame; stops after 500 µs of
                     handler time (rest in the next pass); paused while an NVM operation is pending (§5.11)
events_flush()       EVENT ring -> TX class E (≤ 4 per pass)
status_service()     stack high-water (every 100 ms), counters, afe_rate median (from the AFE ring)
params_apply()       re-configuration requested by SET/LOAD/DEFAULT (§5.15), ≤ 10 ms after the response
nvm_service()        non-blocking state machine HOLD → QUIESCE → PROGRAM → VERIFY → DONE (§5.11);
                     only PROGRAM is a long pass and is excluded from loop_max_us
led_service()
wdg_service()        IWDG kick only if the control tick advanced since the last kick (§5.13)
loop_max_us = max(loop_max_us, hal_time_us() - t0)        (not reset on read; ICD §7.2)
```
Budget: link ≤ 500 µs + events ≤ 40 µs + rest ≤ 50 µs, plus ISR preemption (step ≤ 6 % at 50 kHz, tick ≤ 3 %, HX711 0.5 %). That gives **≤ 0.7 ms ≤ 1 ms** (NFR-006). DATA frames are no longer built here (v0.1); they are built in the AFE ISR (§5.14).

### 4.3 Control tick (TIM5 CC1, 1 kHz, level 4)

```
 1  CCR1 += 1000; tick_count++; ms++
 2  input levels (one IDR read per port) -> debounce: release / re-arm (hal_inputs_rearm) after io.release_ms;
    ALM / PEND / DRV_PWR polled (ALM: active at the first sample, inactive after a stable io.release_ms);
    E-stop closed-time counter (io.estop_release_ms); backup detection (input active, not latched -> same
    action as the ISR); deferred input callbacks after an NVM operation
 3  stop sniffer on RX bytes new since the last tick (§5.9.3) -> motion part of PC STOP / HALT / PAUSE
 4  fold the ISR latch records (atomic snapshot) into pure latches -> latch states, VALID auto-clear, EVENTs
 5  drvmon (§5.7.2): DRV_PWR filter (loss -> stop + disable), K1 timer (drv.k1_weld_ms), ALM/PEND state,
    NOT_SETTLED timer
 6  timeouts (§5.7.1): link watchdog, jog dead-man, AFE stale, idle disable, ENA settle
 7  PAUSE request (button / PC) -> controlled stop / RESUME_REQUEST
 8  motion_sm step: homing phase transitions, MOVE_DONE, segment starts, retargets, controlled-stop path
 9  AFE missed-edge recovery: DOUT low and no read for 2 periods -> hal_hx711_kick()
10  fallback DATA frame at stream.fallback_hz while AFE stale and the stream is on (class D)
```
The tick never touches flash. Its only UART interaction is the class-D mailbox (fallback frames) and the read-only RX peek of the stop sniffer.

### 4.4 Data sharing rules
- **ISR → tick latch records**: each input/AFE/step ISR owns one record `{flags; t_us; pos_steps}` and is its only writer. The tick reads the record under `CRIT_HALT` (≤ 0.2 µs) and clears its "new" bit. ISRs never post an EVENT directly.
- **AFE ring** (sample ISR → main, statistics only): 16-slot SPSC ring with `__DMB()` before the index publish (TS P9).
- **Class-D mailbox** (DATA frames): two 26 B slots. Producers are the sample ISR (level 3) and the tick (level 4, fallback frames); the tick claims a slot under `CRIT_DATA` (BASEPRI 0x30, ≤ 2 µs). The consumer is the DMA TC ISR or the kick from an idle UART.
- **VALID state** `{old, new, t_apply}`: written by the thread/tick under `CRIT_DATA` and read by the sample ISR.
- **Load-limit thresholds**: copied into the ISR-owned `loadlim` state under `CRIT_DATA`, effective from the next sample (SAF-FW-010).
- **Step-generator descriptor**: written only while the timer is stopped, or as a retarget record under `CRIT_MOTION` (BASEPRI 0x20, ≤ 1 µs).
- **`pos_steps`**: kept by the HAL, written only by the step ISR and by `hal_step_set_count()` while stopped (homing zero shift). 32-bit reads are atomic.
- **Thread ↔ tick** (motion requests, latch clears, EVENT ring producers): `CRIT_TICK` (BASEPRI 0x40).

---

## 5. Module designs

### 5.1 System: time base, reset cause, faults, stack, UID
- **TIM5** is a 32-bit 1 MHz free-running counter (`hal_time_us()`), the `t_us` of DATA, EVENT, STATUS and SET_VALID (FW-TIM-001). CC1 is the control tick. CC2 is the M1 synthetic AFE (80 Hz, `afe.rate_sps`).
- `hal_time_ms()` is a u32 maintained by the tick.
- **Reset cause** (pure `resetcause`). RCC->CSR maps to the ICD code in this precedence: LPWRRSTF → `RST_LOW_POWER`; WWDGRSTF → `RST_WWDG`; IWDGRSTF → `RST_IWDG`; SFTRSTF → `RST_SOFTWARE`; PORRSTF → `RST_POWER_ON`; BORRSTF → `RST_BROWN_OUT`; PINRSTF → `RST_PIN`; else `RST_UNKNOWN`. PINRSTF is set by every internal reset, and BORRSTF by every POR, so the order matters. The mapping is host-tested.
- HardFault/NMI: `hal_step_abort()`, record PC/LR/CFSR in `.noinit`, `NVIC_SystemReset()`. The record is reported after reboot (sent in the BOOT EVENT value field; ICD BOOT value = 0/0 → OI-FW-21 asks to allow it, else status only).
- Stack: painted at boot; the lowest unpainted word is checked every 100 ms → `stack_free_min` (NFR-005).
- UID: 12 bytes at 0x1FFF7A10 (ASSUMED, RM0390; verify at WP8).

### 5.2 Inputs: E-stop sense, limits, PAUSE, ALM, PEND, DRV_PWR (v0.5: STOP/BREAK input retired, CR-01)

| Input | Pin / EXTI / level | Active | Polarity | HAL fixed reaction (RAM) | Core action (callback / tick) | Release / re-arm | Req. |
|---|---|---|---|---|---|---|---|
| E-stop sense | PA10 / 10 / 0 | high (NC open) | **fixed** | `hal_step_abort()` + ENA disabled | ESTOP latch, HOMED/VALID cleared, motion discarded, EVENTs (§5.3) | closed continuously ≥ `io.estop_release_ms` enables ESTOP_CLEAR | SAF-FW-005/006/007, FW-SW-002 |
| START / END | PB0, PC1 / 0, 1 / 1 | high | **fixed** | `hal_step_stop_now()` (CLEAN), mask own line | capture `hal_step_count()` + t_us; homing edge (§5.5) or LIMIT latch; both active → LIMIT_WIRING | stable inactive ≥ `io.release_ms` → `hal_inputs_rearm()`; latch auto-clear §5.3 | FW-SW-001, SAF-FW-013/014 |
| PAUSE | PB6 / 6 / 1 | `io.pause_active_level` (default `CLOSED_ACTIVE`, NO) | param | mask line | request flag + t_us → tick (§5.3 PAUSED) | released ≥ `io.release_ms` → re-arm, PAUSE_BUTTON(0) | SAF-FW-023, FW-SW-003 |
| ALM | PA8 / **polled 1 kHz** (EXTI line 8 not enabled, OBS-P1-10) | `drv.alm_active_level` | param | – | tick: active at the first active sample (start-block is fail-safe), inactive after a stable `io.release_ms`; EVENT ALM_CHANGED once per accepted change; **start-block only** (SAF-FW-026, §5.16) | stable `io.release_ms` | FW-SW-004, SAF-FW-026 |
| PEND | PA9 / polled | `drv.pend_active_level` | param | – | tick sample → `DS_PEND`; NOT_SETTLED timer | – | FW-SW-004 |
| DRV_PWR | PA7 / polled 1 kHz | low = powered | **fixed** | – | tick: drvmon (§5.7.2) | 20 ms stability filter, both directions (FW-SW-005) | FW-SW-005, SAF-FW-024/025 |

Rules:
- **Act on the first edge; debounce only the release** (FW-SW-001, R4 §3.4). The HAL masks its own EXTI line after the first active edge, so bounce causes no interrupt storm. The tick re-arms the line after the stable release (`hal_inputs_rearm()`: clear pending, re-read the level). Core logic is idempotent for repeated edges, so the twin need not model masking.
- **Fixed HAL reaction, then the core.** The safety reaction (TRUNCATE + ENA, or CLEAN) is done by the RAM-resident HAL handler before the core callback. During a flash operation the core callback is **deferred** (pending bit, delivered by the tick afterwards), because core code lives in flash (§5.11). The reaction itself therefore never depends on flash.
- The HAL needs the PAUSE polarity to decide "active edge". `hal_inputs_config()` receives `io.pause_active_level` and `drv.alm_active_level` (seam v1.3: `stop_active_level` removed, SR-M2-02) at boot and on every change (`params_apply`). The change re-arms the input without generating a press (`params.yaml` text).
- Limit reaction while idle: the CLEAN halt is a no-op when stopped, and the core only reports/latches the input. An active edge expected by homing (START during FAST_SEEK / SLOW_APPROACH) is routed to the homing machine and does not set LIMIT_START (ICD §5.4).
- **ALM chatter** (OBS-P1-10): ALM needs no µs reaction (no stop path, D-16), so it is polled, not interrupt-driven. The filter rule above gives at most one ALM_CHANGED pair per `io.release_ms` + 1 ms, whatever the input does. No level-1 interrupt load and no EVENT flood can result. The STOP and PAUSE lines keep mask-after-first-edge + tick re-arm, so a chattering button also produces one interrupt per press.
- Edge capture accuracy: the level-1 ISR reads `hal_step_count()` ≤ 1 µs after the edge. A pulse that completes in that µs is counted later by the level-2 step ISR (≤ 1 step; at `v_slow` 0.5 mm/s = 80 steps/s the probability is ≈ 1e-4 per homing).

### 5.3 Stop primitive, latches and the stop policy (ICD §6.2)

**Stop primitive** (`hal/f446/step_tim2.c`, `.RamFunc`, under `CRIT_HALT` ≤ 0.2 µs; decision = pure static inline `stepgen_halt_decide()`):

| Function | Mode | Used by | Action |
|---|---|---|---|
| `hal_step_abort()` | **TRUNCATE** | E-stop sense, HardFault/NMI | `OC1M` = force inactive, `CEN` = 0, `UIE` = 0. A pulse in flight (`CNT ≥ CCR1`) is cut and **not counted** → returns true → `DS_POS_UNCERTAIN` (HOMED is cleared by the E-stop anyway). |
| `hal_step_stop_now()` | **CLEAN** | limits, PC STOP/HALT, load limit, AFE fault, MOVE_UNTIL_LOAD, homing edges, step fault, DRV_PWR lost, slow controlled stop (D-29 d / D-30) | If `CNT ≥ CCR1 − guard` (pulse running or starting within 0.5 µs) → `OPM` = 1: the counter stops in hardware at the update that ends **this** pulse, and the update ISR counts it. Else → force inactive, `CEN` = 0. **No runt, no uncounted pulse.** |

The last PUL edge after a CLEAN stop comes ≤ `pulse_high` + 0.5 µs after the call: ≤ 10.5 µs at the default and ≤ 100.5 µs at the maximum, inside every SAF-FW-002 budget. **Start-then-recheck** (unchanged from v0.1): a start captures `hal_step_stop_gen()`, checks the gates, sets `CEN`, re-reads `stop_gen`, and aborts if it changed. The first edge comes ≥ `dir_setup` after `CEN`.

**Latch and stop model** (pure `latches`, folded in the tick; this is ICD §6.2 restated with FW mechanics):

| Trigger (ICD §6.2) | Mechanism | Stop | ENA | Latch → clear | HOMED | VALID | EVENTs (order) |
|---|---|---|---|---|---|---|---|
| E-stop sense opens | HAL reaction + core | TRUNCATE | disabled | ESTOP → `io.estop_release_ms` closed + ESTOP_CLEAR, then ENABLE + HOME | cleared | cleared (`SC_ESTOP`) | ESTOP_SET, STOPPED(`SC_ESTOP`)*, VALID_CLEARED*, DRIVER_DISABLED(`DD_ESTOP`)*, MOVE_DONE(`MD_STOPPED`)* |
| PC STOP | cmd + sniffer | CLEAN / controlled (`mode`) | kept | none | kept | cleared (`SC_PC_STOP` / `SC_PC_STOP_CONTROLLED`) | STOPPED*, VALID_CLEARED*, MOVE_DONE* |
| PC HALT | cmd + sniffer | CLEAN | kept | HALT (`SRC_PC` unless already latched) → HALT_CLEAR | kept | cleared (`SC_PC_HALT`) | HALT_SET (first only), STOPPED*, VALID_CLEARED*, MOVE_DONE* |
| PAUSE button / PC PAUSE | tick / cmd + sniffer | controlled | kept | PAUSED (`pause_src`) **blocks every new motion start** (`BLOCK_PAUSED`, D-30) → cleared by **RESUME** (`PCLR_RESUME`, refused while HALT/ESTOP/fault is latched, D-31) or by HALT_CLEAR (`PCLR_HALT_CLEAR`). Button press while PAUSED → RESUME_REQUEST only (the PC decides; the FW never resumes); PC PAUSE while PAUSED → OK, no event. | kept | cleared (`SC_PAUSE_BUTTON` / `SC_PC_PAUSE`) | (PAUSE_BUTTON(1)), PAUSED (0→1 only), STOPPED*, VALID_CLEARED*, MOVE_DONE* |
| limit switch while moving | HAL + core | CLEAN | kept | LIMIT_x → auto: input released (stable inactive) for `io.release_ms` (**D-33 h**); while latched only motion **away** from the switch is accepted (`BLOCK_LIMIT` toward it) | kept | cleared (`SC_LIMIT_START/END`) | LIMIT_SET, STOPPED, VALID_CLEARED*, MOVE_DONE; later LIMIT_CLEARED |
| both limits active | ISR (other pin read) + tick | CLEAN | kept | FAULT LIMIT_WIRING → both released + FAULT_CLEAR | kept | cleared (`SC_LIMIT_WIRING`) | FAULT_SET(3), STOPPED*, … |
| FW load limit / rail | sample callback | CLEAN | kept | FAULT LOAD_LIMIT → FAULT_CLEAR (always; re-trip on regrow) | kept | cleared (`SC_LOAD_LIMIT`) | FAULT_SET(0, value = raw), STOPPED*, … |
| AFE stale while moving | tick | CLEAN | kept | FAULT AFE_FAULT → fresh samples + FAULT_CLEAR | kept | cleared (`SC_AFE_FAULT`) | AFE_STALE(1), FAULT_SET(1), STOPPED, … |
| link watchdog (moving) | tick | controlled | kept | `DS_LINK_WDG` → next valid command frame | kept | cleared (`SC_LINK_WDG`) | LINK_WDG, STOPPED, …, LINK_RESTORED |
| jog dead-man | tick | controlled | kept | none | kept | **unchanged** | STOPPED(`SC_JOG_DEADMAN`), MOVE_DONE |
| step overrun / count fault | step ISR flag → tick | CLEAN | kept | FAULT STEP_FAULT → FAULT_CLEAR | **cleared** (+ POS_UNCERTAIN) | cleared (`SC_STEP_FAULT`) | FAULT_SET(2), STOPPED, … |
| homing failure | homing | CLEAN | kept | FAULT HOME_NOT_FOUND / HOME_WIRING → FAULT_CLEAR; ABORTED: no latch of its own | cleared | cleared (`SC_HOME_FAIL`) | FAULT_SET (not for ABORTED), HOME_FAILED(`HF_*`), STOPPED, MOVE_DONE |
| home drift at re-homing (FW-HOM-004) | homing | none | kept | FAULT HOME_DRIFT → FAULT_CLEAR (at once) | set (new zero) | – | FAULT_SET(7, value = deviation µm), HOMED |
| E-stop open + DRV_PWR present > `drv.k1_weld_ms` | drvmon | – | – | FAULT K1_WELDED → cause gone + FAULT_CLEAR | – | – | FAULT_SET(6, value = ms) |
| DRV_PWR lost (sense enabled, **any cause**, SAF-FW-024) | drvmon (20 ms filter) | CLEAN (if still moving) | disabled | none (state follows the input; BLOCK DRV_UNPOWERED) | cleared | cleared (`SC_DRV_POWER_LOST`) | DRIVER_POWER(0) always; STOPPED*, VALID_CLEARED*, DRIVER_DISABLED(`DD_DRV_POWER_LOST`)*, MOVE_DONE* (none of these after an E-stop, which already stopped and disabled: ICD §6.2, OI-ICD-06) |
| idle ≥ `safety.idle_disable_s`, unloaded | tick | – | disabled | none → ENABLE | cleared | – | DRIVER_DISABLED(`DD_IDLE`) |
| PC DISABLE | cmd (refused while moving) | – | disabled | – | cleared | – | DRIVER_DISABLED(`DD_PC_DISABLE`) |
| ALM active | ISR level + tick | **no stop** (D-16) | kept | – (BLOCK DRIVER_ALARM for new motion starts while power present, SAF-FW-026) | – | – | ALM_CHANGED |

\* only when the state warranted it: STOPPED and MOVE_DONE only if a motion was running; VALID_CLEARED only if VALID was 1; DRIVER_DISABLED only if the driver was not already NOT_ENABLED.

**EVENT order and timing.** STOPPED (with `value` = position at initiation) and VALID_CLEARED are emitted when the stop is **initiated**. MOVE_DONE (`MD_STOPPED`, final position) is emitted at standstill. So STOPPED always precedes MOVE_DONE (ICD §5.4), and the PC learns about a controlled stop ≤ 1 ms after its start. Every motion ends with exactly one MOVE_DONE. Every stop discards the target and the ramp state (SAF-FW-001), and the FW never resumes a stopped move.

**FAULT_CLEAR** (pure, ICD §5.5):
- `cause_present(f)`: LOAD_LIMIT → never blocks; AFE_FAULT → `afe_stale || afe_saturated`; LIMIT_WIRING → both inputs active; K1_WELDED → E-stop open && DRV_PWR present && `drv.pwr_sense_enable`; STEP_FAULT, HOME_NOT_FOUND, HOME_WIRING, HOME_DRIFT → none.
- If any latched fault other than LOAD_LIMIT has its cause present → `ST_E_CAUSE_ACTIVE`, detail = mask of those faults, **nothing cleared**.
- Else all faults are cleared, the OK body is the cleared mask, and EVENT FAULT_CLEARED(mask) is sent. For LOAD_LIMIT the value at clear is stored for the regrow rule (SAF-FW-011).

**HALT_CLEAR**: never refused since ICD v0.5 (D-36: no STOP-button cause). HALT is cleared (EVENT HALT_CLEARED, `halt_src` = `SRC_NONE`), and PAUSED is cleared too (`PCLR_HALT_CLEAR`, `pause_src` = `SRC_NONE`). A HALT terminates the sequence, and clears never start motion (D-31). HALT_CLEAR with HALT not latched → OK, and PAUSED is still cleared if set.

**RESUME** (D-31, 0x3C, LEN 0; checks in ICD §4.4 order):
- **Refused** with `ST_E_STATE` while HALT is latched, ESTOP is latched or its input is open, or any FAULT is latched. The detail is the BLOCK mask of exactly those bits (`BLOCK_HALT`, `BLOCK_ESTOP`, `BLOCK_FAULT`).
  - A STOP-button HALT latched just before a RESUME frame therefore makes the RESUME fail, and it can never be cleared by it (F-B-30 race).
  - DRV_UNPOWERED, DRIVER_ALARM, NOT_ENABLED, NOT_HOMED, LIMIT and AFE bits are **not** evaluated. RESUME starts no motion, and the following motion command is gated by them on its own (proposal to the Integrator for v0.4, OBS-P1-15 → OI-FW-28).
- **Accepted with PAUSED set**: clears PAUSED, `pause_src` = `SRC_NONE`, EVENT PAUSE_CLEARED(`PCLR_RESUME`). Nothing else changes: no motion, VALID unchanged, HALT/LIMIT/faults untouched.
- **Accepted with PAUSED not set**: OK, no effect, no event (idempotent; the SW's priority retry is harmless).
- It is a valid command frame, so it refreshes the link watchdog.
- **Not handled by the stop sniffer.** RESUME relaxes a latch, so it must only take effect in arrival order after every earlier frame (§5.9.3).
- SW Resume = RESUME, then re-issue the interrupted step's absolute target (D-31). The PAUSE button's RESUME_REQUEST is only a request to the PC.

**ESTOP_CLEAR**: with ESTOP latched or the input open, refused with detail 0xFFFF (input open) or the ms missing until `io.estop_release_ms`. Otherwise ESTOP is cleared (EVENT ESTOP_CLEARED); the driver stays NOT_ENABLED and not homed (SAF-FW-006).

### 5.4 Motion state machine and command acceptance

#### 5.4.1 States (pure `motion_sm`, ICD §6.1)
`motion_state ∈ {MS_NOT_ENABLED, MS_ENABLING, MS_IDLE, MS_MOVE_ABS, MS_JOG, MS_MOVE_UNTIL_LOAD, MS_HOMING, MS_STOPPING}`. Orthogonal flags: HOMED, POS_UNCERTAIN, PAUSED (`pause_src`), latches (§5.3), and `home_phase`.

Transitions:
- ENABLE: NOT_ENABLED → ENABLING → IDLE (EVENT DRIVER_ENABLED).
- Motion command: IDLE → 3…6 → IDLE (MOVE_DONE).
- Controlled stop: 3…6 → STOPPING → IDLE.
- Immediate stop: 3…7 → IDLE.
- DISABLE / E-stop / idle disable / DRV_PWR lost: any → NOT_ENABLED.

ENABLE semantics (ICD §5.4):
- Refused with `ST_E_STATE` only for `BLOCK_ESTOP` / `BLOCK_DRV_UNPOWERED`.
- Drives ENA to the enabled level, then waits until `max(t_ena, t_pwr_on) + motion.ena_settle_ms`, where `t_pwr_on` is the last DRV_PWR return. No PUL/DIR edge happens during the wait.
- The OK body is the remaining settle time in ms (0 if already enabled = no-op). ENABLE while ENABLING returns the remaining time.

#### 5.4.2 Pure command check (`cmd_check`, ICD §4.4)
`cmd_verdict_t cmd_check(const cmd_ctx_t *c, uint8_t type, const uint8_t *payload, uint16_t len)` returns `{status, detail}` and has no side effects. The context is a field-by-field mirror of `ref_cmdcheck.FwState`. Each check vector therefore sets up the context directly, and the live FW fills it from its state:

| `FwState` field | `cmd_ctx_t` member (live source) |
|---|---|
| `params` | `const params_t *p` (RAM image) |
| `motion_state`, `enabling_left_ms` | `motion_sm` |
| `homed`, `pos_um` | flags; `steps_to_um(hal_step_count())` |
| `estop_latched`, `estop_input_open`, `estop_closed_ms` | latches; debounce |
| `halt_latched`, `stop_btn_active`, `stop_btn_released_ms` | latches; debounce |
| `faults`, `fault_causes` | FAULT mask; `cause_present()` per bit (§5.3) |
| `limit_start`, `limit_end` | input active **or** latched |
| `afe_stale`, `afe_saturated`, `raw` | AFE state (last sample) |
| `drv_power`, `alm_active` | drvmon (filtered) |
| `nvm_record_valid` | `nvm_log` |
| `paused` | PAUSED latch → `BLOCK_PAUSED` (D-30, ICD v0.3); the execution effect is checked against `paused_after` |

Check order (normative, ICD §4.4):
1. TYPE → `ST_E_UNKNOWN_CMD` (detail = TYPE).
2. LEN → `ST_E_LENGTH` (detail = expected LEN).
3. Arguments in payload order: `E_PARAM_ID` → `E_TYPE` → `E_RANGE` (detail = byte offset; SET_PARAM: id). SET_PARAM uses the generated `param_validate_set()`.
4. State: `E_STATE` (BLOCK mask, all bits) → `E_BUSY` (`BUSY_ENABLING`, `BUSY_MOTION`; JOG while jogging is OK) → `E_CAUSE_ACTIVE` → `E_CONFIRM` → `E_CONFIG` (`param_rules`).
5. Execution: `E_NVM`, `E_INTERNAL`.

The BLOCK mask follows ICD §4.3:
- ENABLE evaluates only ESTOP and DRV_UNPOWERED.
- HOME evaluates everything except NOT_HOMED and LIMIT.
- JOG 0 is never refused.
- NOT_HOMED applies to MOVE_ABS, MOVE_UNTIL_LOAD and to JOG with a bound.
- LIMIT applies only toward an active or latched switch: direction = sign(target − x) or sign(v).
- DRV_UNPOWERED = `drv.pwr_sense_enable && !drv_power` (SAF-FW-024).
- DRIVER_ALARM = `alm_active && !DRV_UNPOWERED`, **only for new starts** (SAF-FW-026, ICD v0.2 §4.3). These are MOVE_ABS, MOVE_UNTIL_LOAD, HOME, and JOG ≠ 0 with `motion_state` ≠ `MS_JOG`; a refresh of a running jog is not blocked.
- **RESUME** (D-31) evaluates only HALT, ESTOP and FAULT (§5.3).
- **PAUSED** (bit 10, D-30, ICD v0.3) = `paused`, for MOVE_ABS, MOVE_UNTIL_LOAD, HOME and JOG ≠ 0 **including running-jog refreshes**. A refused refresh lets the jog dead-man stop the jog: in practice the jog is already STOPPING because of the PAUSE, so the refresh would get `E_BUSY` anyway.

`check_vectors.json` (455 cases in v0.1) is the acceptance test of this function (§8.5 T-CHECK).

Integer details:
- `abs(v_um_s)` of JOG is computed in `int64_t` (INT32_MIN).
- `(bound − pos)·dir ≤ 0` uses `int64_t`.
- H3 uses `uint64_t`.
- The step-rate cap `floor(max_step_rate_hz · 1000 / (double)steps_per_mm)` is computed in **double** (soft-float, once per command, ≈ 1 µs) so that it equals the Python oracle bit for bit. f32 could differ at boundaries.

#### 5.4.3 Speed cap (ICD §5.4, D-29 e, FW-MOT-009)
`v_limit = min(v_max, floor(max_step_rate_hz·1000/steps_per_mm))`:
- `v_max = motion.v_max_load_um_s` for MOVE_UNTIL_LOAD and for any command accepted while **loaded**. Loaded means `abs(raw − safety.zero_raw) ≥ safety.release_band_raw`, **or** AFE stale (unknown load is treated as loaded).
- Otherwise `v_max = motion.v_max_travel_um_s`.
- Un-homed JOG is additionally capped at `motion.v_unhomed_um_s`.

The cap is evaluated **at command time** (also for a JOG speed change while jogging). A running move is not re-capped when load appears; the FW load limit protects it. A speed of 0 (MOVE_ABS, MOVE_UNTIL_LOAD) or above `v_limit` gives `E_RANGE` with the offset of the speed field. STATUS `v_limit_um_s` reports the cap MOVE_ABS would get now. Homing speeds are clamped to the step-rate cap only (ICD §5.4).

#### 5.4.4 Execution effects of accepted motion commands
- **PAUSED (D-30, D-31)**: motion commands never clear PAUSED. While it is set they are refused (`BLOCK_PAUSED`, §5.4.2). Only RESUME or HALT_CLEAR clears it (§5.3). So a motion command already in flight when PAUSE is pressed cannot restart motion. PAUSE_CLEARED arg 1 (MOTION_CMD) is no longer used (ICD v0.3).
- **MOVE_ABS**: trapezoid/triangle to the absolute target. A target equal to the current position completes at once (MOVE_DONE `MD_TARGET`, no pulse).
- **JOG** (`v` signed, `a`, `bound_um`):
  - End point: `bound_um` if given; else the soft limit in the jog direction (homed); else start ± `home.max_travel_um` (un-homed).
  - JOG while jogging changes speed, accel and bound on the fly; a reversal decelerates to 0 and respects DIR setup. It also refreshes the dead-man (`motion.jog_timeout_ms`).
  - End point reached → `MD_BOUND` / `MD_SOFT_LIMIT`.
  - **JOG 0**: controlled stop with `a_stop` → `MD_JOG_ZERO`. While not jogging it is an OK no-op.
- **MOVE_UNTIL_LOAD** (`bound_um`, `v`, `a`, `raw_stop`, `cmp`):
  - Before the first pulse the last sample is compared (`CMP_GE`: raw ≥ raw_stop; `CMP_LE`: raw ≤ raw_stop). Already beyond → `MD_LOAD_THRESHOLD` without motion.
  - Every sample during the move is compared in the sample ISR; a hit gives a CLEAN halt → `MD_LOAD_THRESHOLD`. The bound gives a planned stop exactly at the bound → `MD_BOUND`.
  - Fallback frames are not samples.
  - The bound may equal the soft limit in the step direction (inclusive range check). This is the SW's "approach to the end of travel" for load-target steps (D-32 Q26, D-33 d); the END switch stays the hardware backstop. No FW change is needed.
- **HOME**: §5.5. **STOP/HALT/PAUSE/clears**: §5.3. **DISABLE**: refused while moving (`E_BUSY`); otherwise ENA disabled, NOT_ENABLED, HOMED cleared, DRIVER_DISABLED(`DD_PC_DISABLE`).
- **`motion.steps_per_mm` SET** (idle only, not `moving_ok`): HOMED is kept (zero is a step count) and every µm position rescales.

### 5.5 Homing — START switch only (pure `homing`, FW-HOM-001/002, D-29 b)

| Phase (`home_phase`) | Motion (via the step generator; accel `home.a_um_s2`) | Exit |
|---|---|---|
| `HP_PRECHECK` | – | Gates per §5.4.2. Load check: `abs(raw − zero_raw) > home.max_load_raw` and not `HOMEF_LOAD_CONFIRMED` → `ST_E_CONFIRM` (SAF-FW-021). Else → RELEASE if START is active, otherwise FAST_SEEK. |
| `HP_RELEASE` | +x at `home.v_slow_um_s` until START is released (stable `io.release_ms`), then `home.backoff_um` further | done → FAST_SEEK; not released within `HOME_RELEASE_MAX_UM` → `HOME_WIRING` |
| `HP_FAST_SEEK` | −x at `home.v_fast_um_s`, travel bound `home.max_travel_um` | START edge (HAL CLEAN halt) → BACKOFF; bound reached → `HOME_NOT_FOUND`; END edge → `HOME_WIRING` |
| `HP_BACKOFF` | +x at `v_slow` until released, then `home.backoff_um` | → SLOW_APPROACH (release bound as RELEASE) |
| `HP_SLOW_APPROACH` | −x at `v_slow`; the **first** START edge: the EXTI0 callback captures `hal_step_count()` | edge → zero set: `hal_step_set_count(count − edge_count − um_to_steps(home.offset_um))` with the timer stopped (edge at x = −`offset_um`); drift check; → MOVE_TO_ZERO. No edge within `backoff_um + 10 mm` → `HOME_NOT_FOUND` |
| `HP_MOVE_TO_ZERO` | to x = 0 | done → `HP_DONE`: HOMED = 1, POS_UNCERTAIN = 0, EVENT HOMED (value = signed drift µm, 0 if not homed before), MOVE_DONE `MD_TARGET` at 0 |
| any | any other stop source / latch / PC STOP | HOME_FAILED `HF_ABORTED` (no latch of its own), HOMED cleared |

- **Drift** (FW-HOM-004, R5 §8): if HOMED before, deviation = captured edge (old coordinates, µm) − (−`offset_um`). `|deviation| > home.drift_tol_um` → FAULT HOME_DRIFT (value = deviation), and homing still completes.
- **Every failure**: CLEAN halt, HOMED cleared, EVENT HOME_FAILED (`HF_NOT_FOUND`, `HF_WIRING` + the FAULT latch, or `HF_ABORTED`), MOVE_DONE `MD_STOPPED`.
- Expected START edges do not set LIMIT_START, but the END switch keeps its full limit function.
- **Not implemented**: homing at END, and the END zero rule of ICD v0.1 (OI-ICD-01, retired with `home.ref_switch`).
- Repeatability (FW-HOM-003, target only): at 0.5 mm/s an ISR latency of ≤ 2 µs is 1 nm; the switch itself (±5…20 µm) dominates.

### 5.6 Step generator (TIM2 PWM mode 2; FW-MOT-001…003, SAF-FW-003/004)

#### 5.6.1 Hardware (pin §2)
- TIM2 runs at 90 MHz with PSC 0: `ARR = c − 1`, `CCR1 = c − PW`, **PWM mode 2**, so the pulse ends exactly at the update event.
- `ARPE = OC1PE = 1`, `CC1P = motion.pul_invert` (reboot-required).
- `PW = round(pulse_high_ns · f)` (10 µs → 900 ticks).
- `c_min = max(ceil(f / max_step_rate_hz), PW + ceil(pulse_low_min_ns · f))`, with H3 guaranteeing consistency.
- Slowest period: 47.7 s (32-bit) → ≥ 0.021 steps/s, so `steps_per_mm` ≥ 100 (dictionary minimum) reaches 0.001 mm/s (SYS-004).

#### 5.6.2 Start
1. DIR is written while the timer is stopped (`motion.dir_invert` applied).
2. Then `CNT = 0`. The first compare is `max(c1 − PW, dir_setup_ticks)`, so the first edge comes ≥ `motion.dir_setup_us` after the DIR write without a busy-wait.
3. The second period is preloaded, then `UG`, clear `UIF`, `OPM = 0`, `CEN = 1`, and the `stop_gen` re-check.
4. The first period from rest is c1 = f·√(2/α), with α [steps/s²] = a·`steps_per_mm`/1000. **It can be as short as ≈ 45 µs** (a = `a_max_um_s2` max 10 m/s², spm 100 000, α = 10⁹), ≈ 0.5 ms at spm 800, and ≈ 1.1 ms at the default 160 spm with a at its maximum. The v0.1/v0.2 claim "c1 ≥ 1 ms" was wrong (DEF-P1-04). No safety argument in this document relies on a minimum c1 any more: the sniffed-stop hold (§5.9.3) gates every start instead.

#### 5.6.3 Update ISR (`step_isr()` → pure `stepgen_core`)
```
HAL: clear UIF; pos_steps += dir; call step_isr()
core: if halting: finalise -> STOPPED/MOVE_DONE bookkeeping; return STOP
      if steps_done == N: finalise (the counter already stopped via OPM) -> MOVE_DONE
      c = ramp_next(&ramp)                        exact sqrt ramp, max rule, fractional carry (R4 §1.5)
      apply a pending retarget (jog speed, controlled stop: virtual index, r = ceil(v²/2a_stop))
      return {period c, last = (steps_done == N − 1)}
HAL: write ARR/CCR1 preload; if last: OPM = 1; UIF set again at exit -> step-fault flag (CLEAN halt, tick:
     FAULT STEP_FAULT, HOMED cleared, POS_UNCERTAIN)
```
Exact counting (SAF-FW-004): one update = one completed pulse, and the last pulse is ended by OPM in hardware. The ramp is verified against R4 TV-M: 5.2 s trapezoid = 468 000 000 ticks; first periods 503115, 208397, …; each period within ±1 tick (FW-MOT-003).

#### 5.6.4 Controlled stops ≤ 2 ms (SAF-FW-003, D-29 d, D-30)
A controlled stop request (link watchdog, PAUSE button / PC PAUSE, jog dead-man, STOP mode 1, JOG 0) is detected or received by the tick, or by the sniffer in the tick, ≤ 1 ms after the trigger. The path then depends on the current step period P and the planned stop distance d = v²/(2·a_stop), computed in steps from `motion.a_stop_um_s2` and `motion.steps_per_mm`:

| Condition | Path | Deceleration starts / last edge |
|---|---|---|
| P ≤ 1 ms (`CTRL_STOP_ISR_MAX_TICKS`) | the request is applied by the next update ISR (decel term from the virtual index) | ≤ 1 ms + P ≤ 2 ms |
| P > 2 ms (`CTRL_STOP_HALT_MIN_TICKS`) **and** d ≤ 1 step | **CLEAN halt** (D-29 d, D-30 / ICD §6.5) | last edge ≤ 1 ms + PW |
| P > 1 ms otherwise (1 < P ≤ 2 ms, or P > 2 ms with d > 1 step) | **period stretch**: the tick (under `CRIT_MOTION`) calls `hal_step_set_period_now(c_dec1)`. The running period is lengthened to the first deceleration period, but only if `CNT < CCR1` (no pulse in flight); otherwise the pulse ends ≤ PW later and the preload path applies. The FW then **decelerates as planned** (D-30). | ≤ 1 ms + ≤ PW |

**Answer to OI-ICD-07: the FW reprograms the running period immediately; it does not wait for the next update.**
- When the deceleration must be executed and P > 1 ms (this includes every P > 2 ms case with d > 1 step), the tick reprograms the **running** TIM2 period ≤ 1 ms after the trigger, so deceleration starts ≤ 2 ms after the trigger in every case.
- It uses `hal_step_set_period_now()`: with `ARPE` temporarily cleared, it writes `ARR` = c_dec1 − 1 and `CCR1` = c_dec1 − PW, then restores the preload.
- c_dec1 is the first period of the planned deceleration (virtual index r = ceil(v²/2a_stop)). It is ≥ P, because decelerating only lengthens periods. So the edit always **extends** the current interval and can never produce a shorter one, an extra pulse or a runt.
- Race guard: the write happens only while `CNT < CCR1 − guard`, i.e. the pulse of this period has not started. Otherwise the pulse in flight ends ≤ PW (≤ 100 µs) later and the same c_dec1 goes into the preload for the next period, so deceleration still starts within ≤ 1 ms + PW.
- If `CNT` already exceeds the new `CCR1`, the decision function returns "preload path" instead of writing. This cannot happen when c_dec1 ≥ P, but it is checked.
- The step ISR then continues the deceleration from index r − 1.
- Twin criterion proposed for Validator E (OI-FW-24): the first PUL edge after the trigger is ≥ the planned c_dec1 after the previous edge, and the edge sequence follows the deceleration profile within ±1 tick per period.

- The CLEAN halt finishes as a controlled stop: same STOPPED cause, MOVE_DONE `MD_STOPPED` or `MD_JOG_ZERO`, no POS_UNCERTAIN (CLEAN), and MS → IDLE without passing STOPPING (ICD §6.5).
- At defaults, d at P = 2 ms is 0.78 step (500 steps/s, 160 steps/mm, a_stop 1 m/s²).
- With a small `a_stop_um_s2 · steps_per_mm` (< 125 000 steps/s²), d > 1 step, so the stretch path applies. This is what D-30 requires and closes OI-ICD-05.
- The pure path selection `ctrl_stop_path(P_ticks, d_steps)` and the stretch decision `stepgen_stretch_decide(cnt, ccr1, new_c)` are host-tested.

#### 5.6.5 ENA (FW-MOT-008)
ENA is changed only by ENABLE, DISABLE, idle disable, E-stop and DRV_PWR loss, never by an operational stop (D-10/D-11). `hal_ena_set(bool enabled)` applies `motion.ena_invert`, which is fixed at boot because it is reboot-required.

### 5.7 Safety supervisor (tick, pure functions)

#### 5.7.1 Timeouts

| Function | Rule | Action | Req. |
|---|---|---|---|
| Link watchdog | motion state 3–7 **and** `now − last_cmd_rx_ms ≥ safety.link_timeout_ms` | controlled stop, `DS_LINK_WDG`, VALID cleared, LINK_WDG + STOPPED. Cleared at the next valid command frame (LINK_RESTORED). | SAF-FW-015 |
| Jog dead-man | MS_JOG and no JOG for `motion.jog_timeout_ms` | controlled stop, STOPPED(`SC_JOG_DEADMAN`), VALID **unchanged** | SAF-FW-016 |
| AFE stale | no sample for `afe.timeout_ms` | `DS_AFE_STALE` + EVENT AFE_STALE(1). If moving: CLEAN halt + FAULT AFE_FAULT. Motion refused (BLOCK). | SAF-FW-012 |
| Idle disable | `safety.idle_disable_s` ≠ 0, MS_IDLE **continuously** that long, **unloaded and AFE fresh** (D-33 g): the counter restarts whenever the axis moves, is loaded, or the AFE is stale | ENA disabled, NOT_ENABLED, HOMED cleared, DRIVER_DISABLED(`DD_IDLE`). STATUS `idle_disable_left_s` (0xFFFF = not counting, incl. while stale). | SAF-FW-017 |
| ENA settle | MS_ENABLING until the deadline | → MS_IDLE, DRIVER_ENABLED | FW-MOT-008 |
| Limit latch auto-clear | input stable inactive ≥ `io.release_ms` (D-33 h); no position condition (v0.2 also required "moved away") | clear LIMIT_x, LIMIT_CLEARED | SAF-FW-013 |

`last_cmd_rx_ms` is written for every **valid command frame** (CRC-valid, TYPE 0x01…0x3F, NACKed included, ICD §3.1) by the dispatcher and by the stop sniffer. Responses, invalid TYPEs and bad frames do not refresh it (v0.1 said "any type"; corrected).

#### 5.7.2 Driver monitor (pure `drvmon`; D-28, **D-29 c**, SAF-FW-024/025/026, FW-SW-005)
**DRV_PWR filter (FW-SW-005).** The input is sampled at 1 kHz. A level change counts only after it has been stable for `DRV_PWR_FILTER_MS` = 20 ms, in both directions; shorter toggles are ignored. Each accepted change updates `DS_DRV_PWR` / `IO_DRV_PWR` and sends EVENT DRIVER_POWER(1/0). Loss reaction time: input-cell RC ≈ 2 ms + 20 ms + ≤ 1 ms tick = **≤ 23 ms ≤ 25 ms** (SAF-FW-024). With `drv.pwr_sense_enable` = false (reboot-required, bring-up only): `DS_DRV_PWR` = 1, no loss handling and no K1 check.

**Power lost, any cause** (SAF-FW-024; PSU loss, K1 not reset, or after an E-stop):
- If moving: CLEAN halt (the driver is unpowered, so the stop mode is irrelevant for position, and CLEAN keeps the count exact).
- ENA disabled, MS_NOT_ENABLED, HOMED cleared (position lost, D-11), VALID cleared, active move discarded.
- EVENTs: DRIVER_POWER(0) always; STOPPED(`SC_DRV_POWER_LOST`) and MOVE_DONE only if a motion was still running; VALID_CLEARED only if VALID was 1; DRIVER_DISABLED(`DD_DRV_POWER_LOST`) only if not already NOT_ENABLED. In the normal E-stop case (E-stop opens first and K1 drops 20–60 ms later) only DRIVER_POWER(0) follows, as ICD §6.2 / OI-ICD-06 state (the SRS v0.3 wording is being aligned by the Orchestrator).
- Every motion and ENABLE is refused with `BLOCK_DRV_UNPOWERED` until power returns.
- Return → DRIVER_POWER(1) only. ENABLE (settle from the later of ENABLE and power return, FW-MOT-008) and HOME are the operator's job; nothing restarts.

**During an NVM erase/program** (D-33 f, DEF-P1-06): the PROGRAM pass masks the tick (BASEPRI 0x20) for up to ≈ 0.5 s, so DRV_PWR and the K1 timer are not sampled.
- The axis is idle by precondition (SAVE/LOAD/DEFAULT refused while moving), and the RAM-resident E-stop reaction stays live (§5.11).
- When the pass ends, the tick resumes at once and drvmon restarts its filter from the current level. A power loss during the operation is therefore handled ≤ **op time + 23 ms** after the input change (bound: op time + 25 ms). The K1 timer resumes the same way.
- A RAM-resident PA7 check inside the flash loop was considered and rejected: the driver is unpowered in that case anyway, and ENA has no effect without power. D-33 f accepts the extended bound.

**K1 weld** (SAF-FW-025, only with sense enabled):
- The timer runs while (E-stop sense open **and** the filtered DRV_PWR is present) and resets otherwise.
- `≥ drv.k1_weld_ms` (default 200 ms > contactor drop-out 20–60 ms + aux delay) → FAULT K1_WELDED, FAULT_SET(6, value = elapsed ms).
- Latency: the timer starts at the first tick after the E-stop edge (≤ 1 ms) and is checked every tick, so the fault is latched in (`k1_weld_ms`, `k1_weld_ms` + 2 ms] after the sense edge. That is inside the D-33 f bound of + 25 ms, and OBS-P1-02 proposes this as the test bound. During an NVM operation the op time is added.
- Cause present = the same condition (FAULT_CLEAR refused until gone).
- No stop action is needed: ESTOP has already stopped and disabled.

**ALM** (SAF-FW-026):
- Filtered level → `DS_ALM`, ALM_CHANGED.
- The start-block BLOCK_DRIVER_ALARM (new starts only) is evaluated by `cmd_check` (§5.4.2).
- A running move is **not** stopped (D-16/D-28), and ALM is not used for step-loss detection (§5.16).

**PEND / NOT_SETTLED:**
- After the last pulse of a move, with ALM inactive and power present, PEND is expected within `drv.pend_timeout_ms` (0 = off).
- If it does not come → EVENT NOT_SETTLED (elapsed ms), as a warning only.
- In the current open-loop setting PEND may simply follow the pulse train; the semantics are measured at C-16.

### 5.8 HX711 driver, load limit and MOVE_UNTIL_LOAD (FW-AFE-001…005, SAF-FW-008…012)

`EXTI4_IRQHandler` (level 3), shim ported from TS `02_FW/src/sens/hx711.cpp`:
```
t = TIM5->CNT; pos = hal_step_count()             FIRST: data-ready timestamp + setpoint latch (FW-AFE-005, A-03)
if DOUT not low: spurious -> clear PR, return; mask EXTI4
raw24 = hx711_shift_in(pulses(afe.gain_channel)) per bit: CRIT_AFE{SCK high; 0.5 µs; SCK low}; 1 µs; sample
        SCK-high measured by DWT; > HX711_SCK_HIGH_MAX_US -> power-down suspected (re-init, FW-AFE-003)
sample = {t, raw = sign_extend(raw24), pos, sat = raw24 ∈ {0x7FFFFF, 0x800000}, settling, read_error}
on_afe_sample(&sample)                            core, same ISR:
    loadlim_check()  -> trip: hal_step_stop_now(); LOAD_LIMIT record     (every sample, every state)
    MOVE_UNTIL_LOAD compare (cmp)  -> hal_step_stop_now(); reason record
    stream on: build the DATA frame (§5.14) -> hal_uart_write(class D)
    push to the AFE ring (rate statistics, stale timer, last sample for cmd_check)
clear PR4, unmask EXTI4
```
- **Masking** (FW-AFE-001): only each SCK-high phase is inside `CRIT_AFE` (BASEPRI 0x20). The E-stop (0) and limits/STOP/PAUSE (1) stay live. The whole ISR ≈ 45 µs read + ≤ 5 µs checks + ≤ 3 µs DATA build ≤ 60 µs.
- **Gain/channel** (FW-AFE-002): 25/27/26 pulses for `A128`/`A64`/`B32`, applied to the next conversion. **Rate**: RATE pin (PB5), changeable without reboot. Both flag the next `afe.settle_discard` samples `DS_AFE_SETTLING`; those samples are still load-checked (FW-AFE-003).
- **Re-init**: on an SCK-high overrun or a reset symptom, SCK is held low, settle is re-armed, `afe_reinit_count++` and EVENT AFE_REINIT is sent.
- **Rate** (pure `afe_rate`, main loop): median of 16 Δt → `afe_rate_dsps`; `DS_AFE_RATE_MISMATCH` + EVENT when outside `afe.rate_tol_pct` (FW-AFE-004).
- **Load limit** (pure `loadlim`): `safety.load_raw_min/max` (H2), `safety.load_trip_samples` consecutive violations, rails always count (SAF-FW-009), regrow rule after FAULT_CLEAR (`safety.load_regrow_raw`, SAF-FW-011). It cannot be disabled.
- **Missed-edge recovery**: the tick calls `hal_hx711_kick()` (SWIER) when DOUT is low and no read happened for 2 periods.
- **NVM hold**: `hal_hx711_hold(true)` during flash operations; the next sent frame carries `DF_OVERRUN` (FW-NVM-003).
- **M1 synthetic source** (`afe_synth.c`, `FEAT_AFE_SYNTHETIC`): TIM5 CC2 at `afe.rate_sps` delivers the same `afe_sample_t` (deterministic sawtooth + pseudo-noise, never at a rail) through the same `on_afe_sample()`. M1 therefore tests the real DATA path end to end (FW-STR-002 M1).

### 5.9 Link (IF-002…005, IF-011, FW-CMD-001)

#### 5.9.1 UART + DMA (`hal/f446/uart2_dma.c`)
- USART2 921 600 Bd 8N1, OVER16, BRR from PCLK1 (→ 918 367 Bd, −0.35 %).
- **RX**: DMA1 Stream5 Ch4 circular into a 2 048 B ring. HT/TC only count laps. `hal_uart_read()` consumes; `hal_uart_peek()` lets the sniffer read new bytes without consuming (separate cursor). An overflow (writer laps a reader) → `rx_overruns++` and resync. ORE/FE/NE are counted in `USART2_IRQHandler`.
- **TX**: DMA1 Stream6 Ch4 sends **exactly one whole frame per transfer**; v0.1 sent chunks ≤ 256 B, which broke the DATA latency (OI-FW-02). The TC ISR picks the next frame via `txsched`.

#### 5.9.2 Parser and dispatcher
- Pure `frame` implements ICD §2.3 exactly and is verified against the 11 `streams` vectors (frames **and** counters).
- `cmd_dispatch()` sends **exactly one response** per valid command frame, echoing its SEQ. A NACK is exactly 3 bytes and has no side effect. Commands are executed in arrival order. CRC-valid frames with an invalid TYPE → `rx_frame_errors`, no response.
- **Back-pressure** (DEF-P1-07): the dispatcher takes the next frame from the parser only when TX class R has room for the largest response frame (`TX_R_RESERVE` = 168 B). Otherwise the frame stays in the RX ring until responses have drained (R drains at line rate, ≥ 1 response per 1.9 ms).
  - A flooding PC therefore fills the 2 KB RX ring. If the DMA writer laps the parser, the lost bytes are counted in `rx_overruns`, and the parser resyncs per ICD §2.3.
  - FW-CMD-001 holds for every frame that is **received** intact: each gets exactly one response, and no response is ever dropped or shortened. Frames destroyed by the overrun never become valid frames.
  - The stop sniffer reads new bytes through its own cursor every tick, ahead of the parser, so STOP/HALT/PAUSE keep their ≤ 2 ms path even under flooding.
  - The SW keeps ≤ 4 commands in flight (ICD §9.3), so back-pressure never occurs in normal operation. It exists for fuzzing and faulty PCs (TC-FW-CMD-001-02).
- Long operations (SAVE/LOAD/DEFAULT) only start the NVM state machine; `nvm_service()` sends the response (≤ 2.5 s, NFR-008).
- REBOOT: response → TX drain → `hal_reset()` ≤ 50 ms later.

#### 5.9.3 STOP / HALT / PAUSE priority on the receive side — the stop sniffer (SAF-FW-002/003)
- **Inputs.** STOP is a fixed **9-byte** frame (`A5 5A 36 SEQ 01 00 mode CRC CRC`). HALT and PAUSE are fixed **8-byte** frames (`A5 5A 37|3B SEQ 00 00 CRC CRC`).
- **Scanning.** Each control tick, the pure `stop_sniff()` scans the RX bytes that arrived since its last run (`hal_uart_peek()`), with an 8-byte carry for frames split across ticks. It accepts only a CRC-valid candidate with these exact TYPE/LEN pairs, and for STOP only `mode` ≤ 1 (a STOP that the dispatcher would NACK is ignored).
- **Motion part only.** On a hit the sniffer executes the **motion part** of the command at once:
  - STOP 0 / HALT → CLEAN halt;
  - STOP 1 / PAUSE → controlled-stop request.
  It records the stop cause (`SC_PC_STOP`, `SC_PC_STOP_CONTROLLED`, `SC_PC_HALT`, `SC_PC_PAUSE`) for STOPPED/MOVE_DONE and refreshes `last_cmd_rx_ms`.
- **Full semantics in order.** Latches (HALT, PAUSED), VALID, HALT_SET/PAUSED EVENTs and the response are produced only by the normal in-order dispatch of the same frame.
  - Earlier commands still in the buffer are therefore not overtaken in their protocol effect. Example: a MOVE_ABS before a HALT is accepted and then halted, never NACKed because of a HALT it had not seen.
  - **Sniffed-stop hold** (replaces the v0.2 timing argument, DEF-P1-04). When the sniffer finds a stop frame, it also records a **hold** `{TYPE, SEQ, cause}` of the latest sniffed frame (v0.4: matched by TYPE/SEQ instead of an RX position, so an RX overrun cannot misalign the byte counts of parser and sniffer; the 20 ms timeout covers a frame lost to an overrun). While a hold is pending, `motion_start()` does not start the step timer. A motion command dispatched from an **earlier** frame is still checked and acknowledged normally, but its start is parked.
    - When the dispatcher reaches the sniffed frame, the frame's normal semantics discard the parked start: STOP/HALT → MOVE_DONE `MD_STOPPED` with zero pulses; PAUSE → controlled stop of a not-yet-started move = the same, cause `SC_PC_PAUSE`. Each comes with one STOPPED(cause), as for a running move.
    - If the frame is never dispatched (RX overrun past it, or no parser progress for `SNIFF_HOLD_MAX_MS` = 20 ms), the hold resolves on the safe side: the parked start is discarded with the sniffed cause.
    - A move from an earlier frame therefore emits **no pulse** after the stop frame was received, whatever c1 (≥ 45 µs, §5.6.2) and however the 500 µs handler budget splits the frames across passes.
    - Host test: `test_impl_sniff` and `test_impl_core_link` with a MOVE_ABS + HALT burst split across passes and α = 10⁹ steps/s² (TC-SAF-FW-002-03).
  - The motion part is idempotent, so the dispatcher finding the motion already halted or stopping with the same cause adds nothing. Only one STOPPED/MOVE_DONE pair is emitted.
- **Bound**: last byte → next tick ≤ 1 ms → CLEAN halt ≤ 11 µs, giving ≤ 1.1 ms ≤ 2 ms (SAF-FW-002). Controlled stops: ≤ 1 ms + §5.6.4 ≤ 2 ms (SAF-FW-003).
- **False positive**: a CRC-valid STOP/HALT/PAUSE byte pattern inside another PC→FW payload (max 17 B) would cause an extra stop. That errs on the safe side, and the SW never builds such payloads.
- PAUSE is included because it is also a fixed 8 B frame with a 2 ms budget. ICD §2 mentions only STOP/HALT, so v0.4 text should add PAUSE (**OI-FW-19**).
- **RESUME, HALT_CLEAR, ESTOP_CLEAR and FAULT_CLEAR are never sniffed.** Commands that relax a latch must take effect strictly in arrival order (D-31, OBS-P1-15). A RESUME followed by a re-issued target could otherwise overtake an earlier frame.

#### 5.9.4 TX classes and wire order (pure `txsched`; FW-STR-002/004, ICD §2.4)

| Class | Content | Capacity | Producer | Overflow |
|---|---|---|---|---|
| **D** | DATA and fallback frames (26 B) | 2-slot mailbox | sample ISR, tick | new frame dropped: `tx_drops++`, `frame_seq` still advances, next sent frame carries `DF_OVERRUN` |
| **R** | responses (9…168 B) | 1 024 B ring | main loop | **cannot overflow**: the dispatcher's back-pressure (§5.9.2) admits a command only if `TX_R_RESERVE` = 168 B are free (DEF-P1-07) |
| **E** | EVENT frames (24 B, built at send time from the EVENT ring) | 16 events | tick/main via `evq` | `event_overflows++`; the EVENT SEQ counter still advances (ICD §2.2) |

**Wire order** at every frame boundary: **D first if pending, then R, then E.**
- Rationale: FW-STR-002 needs DATA to start ≤ 2 ms after data-ready. With the frame built in the ISR (≤ 60 µs) and only the frame on the wire ahead of it (≤ 168 B = 1.82 ms), the start comes **≤ 1.9 ms** after data-ready.
- Responses wait at most one DATA frame (282 µs) and stay far inside 10 ms. A STOP/HALT/PAUSE response follows its already-executed stop.
- **Drop priority** under congestion is DATA first, then EVENTs; responses are never dropped. This is how the FW reads ICD §2.4 "responses > EVENT > DATA when congested".
- The Integrator is asked to state the wire order explicitly in v0.2 (**OI-FW-18**).

### 5.10 Command table (ICD §3.2; codes from `proto_gen.h`)

| TYPE | Command | LEN | Handler | FW execution (after an OK check) | Retry class: FW obligation | MS |
|---|---|---|---|---|---|---|
| 0x01 | PING | 0 | link | none (link watchdog refresh) | RETRY: idempotent | M1 |
| 0x02 | GET_INFO | 0 | cmd | INFO 44 B: proto 1.0, payload 1, FW version, `PARAM_DICT_HASH`, UID, build id, `PARAM_COUNT`, feature mask per build (§9.2) | RETRY | M1 |
| 0x03 | GET_STATUS | 0 | status | STATUS **86 B** (incl. `pause_src` at offset 84) | RETRY; confirmation path of CONFIRM | M1 |
| 0x04 | REBOOT | 4 | sys | magic, `E_BUSY` while moving; response, drain, reset ≤ 50 ms | VERIFY (BOOT EVENT) | M1 |
| 0x10 | GET_ALL_PARAMS | 1 | params_rt | page p = table indices 20p…20p+19, `page_count` = 3 | RETRY | M1 |
| 0x11 | GET_PARAM | 2 | params_rt | PARAM_ENTRY | RETRY | M1 |
| 0x12 | SET_PARAM | 7 | params_rt | store, apply ≤ 10 ms (§5.15), response = value as stored, CFG_DIRTY re-evaluated, **no flash write** | RETRY (newest value) | M1 |
| 0x13–0x15 | SAVE / LOAD / DEFAULT_PARAMS | 0 | nvm | §5.11 | VERIFY (CFG_DIRTY, `nvm_record_seq`) | M1 |
| 0x20 / 0x21 | STREAM_START / STOP | 0 | stream | idempotent; `SYSF_STREAM_ON` | RETRY | M1 |
| 0x22 | SET_VALID | 1 | valid | `t_apply` = `hal_time_us()` at processing; OK body `u32 t_us` | RETRY newest value | M1 |
| 0x30 | ENABLE | 0 | motion | §5.4.1 | VERIFY | M2 (M1: `E_INTERNAL` NOT_IN_BUILD) |
| 0x31 | DISABLE | 0 | motion | §5.4.4 | VERIFY | M2 (M1: NOT_IN_BUILD) |
| 0x32 | HOME | 1 | motion/homing | §5.5 | VERIFY | M2 (M1: NOT_IN_BUILD) |
| 0x33 | MOVE_ABS | 12 | motion | §5.4.4 | VERIFY (never auto-retried; a duplicate gets `E_BUSY` or is zero-length) | M2 (M1: NOT_IN_BUILD) |
| 0x34 | JOG | 12 | motion | §5.4.4; JOG ≠ 0 refused while PAUSED (D-30), refresh not ALM-blocked (SAF-FW-026) | JOG ≠ 0 VERIFY (refresh stream), JOG 0 RETRY (no-op when idle) | M2 (M1: JOG 0 = OK no-op, else NOT_IN_BUILD) |
| 0x35 | MOVE_UNTIL_LOAD | 17 | motion | §5.4.4 | VERIFY | M4 (M1/M2: NOT_IN_BUILD) |
| 0x36 | STOP | 1 | motion + sniffer | §5.3; VALID cleared also when idle | CONFIRM: idempotent, the stop executes before the ACK | M1 (no-motion part) / M2 |
| 0x37 | HALT | 0 | motion + sniffer | HALT latch; HALT_SET on the first HALT only | CONFIRM: repeats → no further event | M1 / M2 |
| 0x38 | HALT_CLEAR | 0 | latches | §5.3; clears HALT and PAUSED (D-31) | ONCE_PRIORITY: idempotent | M1 |
| 0x39 | ESTOP_CLEAR | 0 | latches | §5.3 | ONCE_PRIORITY | M1 (input from M2) |
| 0x3A | FAULT_CLEAR | 0 | latches | §5.3, OK body = cleared mask | ONCE_PRIORITY | M1 |
| 0x3B | PAUSE | 0 | motion + sniffer | §5.3 PAUSED (`SRC_PC`); afterwards motion refused with BLOCK PAUSED until RESUME or HALT_CLEAR (D-30/D-31) | CONFIRM: PAUSE while PAUSED → OK, no event | M1 / M2 |
| 0x3C | RESUME (D-31, ICD v0.4) | 0 | latches (**not** sniffer) | §5.3: clears only PAUSED; `E_STATE` (HALT/ESTOP/FAULT) while latched; no-op if not PAUSED | ONCE_PRIORITY (proposed): idempotent | M1 |

- In M1 the motion-family handlers run the **full check first**, so every check vector passes in M1. Only an accepted command returns `ST_E_INTERNAL` detail 1 (NOT_IN_BUILD), with no side effect. The feature bits `FEAT_MOTION`/`FEAT_HOMING`/`FEAT_MOVE_UNTIL_LOAD` are 0, so the SW greys these functions out.
- There is no relative move command (SAF-FW-020, IF-009).

### 5.11 NVM (FW-NVM-001…003, ICD §11)

**Placement** (unchanged, decided per Q-R3-04): flash sectors 1 and 2 (0x0800 4000 / 0x0800 8000, 16 KB each) used as a two-sector record log. The custom ldscript keeps `.isr_vector` in sector 0 and code from sector 3. `check_map.py` fails the build if any load address falls in 0x0800 4000–0x0800 BFFF. Erase takes max 0.5 s (ASSUMED) instead of 2 s for 128 KB sectors.

**Record** (TS `nvm_codec` format kept):
- 32-byte header: magic, layout version, header size, `seq`, entry count, entry size, **PARAM_DICT_HASH**, `PARAM_DICT_VERSION`, FW version, payload CRC-32, and the header CRC-32 written **last** as the commit marker.
- 8-byte entries `{u16 id, u8 type, u8 0, u32 raw}` for every `nvm` parameter.
- 512 B slots, 32 per sector.

**Log rule** (pure `nvm_log`):
- Each record is appended to the next free slot.
- The newest valid `seq` across both sectors wins.
- When the active sector is full, the other sector is erased and written, so a power loss at any program word or erase step leaves a valid record (FW-NVM-002).
- Endurance: ≈ 640 000 saves ≫ 10 000.

**Boot / LOAD rules** = ICD §11.3:

| Rule | Condition | Result | EVENT |
|---|---|---|---|
| 2 | no valid record | defaults, `SYSF_NVM_DEFAULTED`, CFG_DIRTY | PARAMS_DEFAULTED(`PDEF_NO_RECORD` / `PDEF_CRC_ERROR`) |
| 3 | equal hash | load; out-of-range entries → default | PARAMS_LOADED(count of replaced values) |
| 4 | different hash (dict v1 → v2 after P2 starts!) | migration by id (same id + type + in range kept; retired id 0x0401 is dropped; the new 0x0705 takes its default) | PARAMS_DEFAULTED(`PDEF_MIGRATION`), NVM_DEFAULTED |
| 5 | result violates H1–H4 (`param_rules_check_all`) | all defaults (boot) / `E_NVM` `NVMD_NO_RECORD` with RAM unchanged (LOAD) | PARAMS_DEFAULTED(`PDEF_HARD_RULE`) |

Session parameters (`nvm: false`: `safety.zero_raw`, `safety.load_raw_min/max`) never come from NVM.

**CFG_DIRTY** (ICD §11.2, the only definition) is true iff any of these holds:
- there is no valid record;
- the record hash ≠ the FW hash;
- any `nvm` parameter's RAM raw ≠ the record entry.

The comparison reads the record directly through `hal_flash_map()` and is re-evaluated after SET/SAVE/LOAD/DEFAULT.

**SAVE state machine** (`nvm_service`, non-blocking except the PROGRAM pass; FW-NVM-003). Precondition: not moving, else `E_BUSY` at command time.
1. **HOLD**: stop dispatching new commands (they wait in the RX ring: ≤ 0.55 s × 100 frames/s × ≤ 20 B ≈ 1.1 KB < 2 KB), `hal_hx711_hold(true)`, suppress fallback frames.
2. **QUIESCE**: per pass, check `hal_uart_tx_idle()` (all classes empty, USART TC). This is bounded by the backlog wire time (≤ 15 ms) and is **not** a busy wait (v0.1 waited inside one pass).
3. **PROGRAM**:
   - `hal_wdg_set_timeout(3000)`;
   - `hal_flash_erase()` if needed, then `hal_flash_program()` entries, header and header CRC. Inside the target implementation: RAM vector table (VTOR), BASEPRI 0x20, busy loop in `.RamFunc`;
   - restore the IWDG run window.
   This is the one long pass, excluded from `loop_max_us`.
4. **VERIFY**: read back both CRCs.
   - OK → CFG_DIRTY = 0, `nvm_record_seq`, `nvm_save_ms`, `nvm_save_uptime_ms`, EVENT PARAMS_SAVED(value = seq), response OK.
   - Failure → `E_NVM` `NVMD_ERASE_PROGRAM` / `NVMD_VERIFY` + EVENT NVM_ERROR; the previous record stays valid and RAM is unchanged.
5. **DONE**: AFE resume (settle re-armed, `DF_OVERRUN` on the next frame), deferred input callbacks delivered, dispatching resumes.

**LOAD** applies rules 3–5 to RAM with no flash write. **DEFAULT** sets RAM to the defaults for **all** parameters, including session values (FW load thresholds back to ±7 022 271), leaves NVM untouched, sends PARAMS_DEFAULTED(`PDEF_COMMAND`) and sets CFG_DIRTY as computed.

**E-stop during a flash operation** (answer kept from v0.1; C-04 confirms on target):
- The level-0/1 handlers (`EXTI15_10`, `EXTI0`, `EXTI1`, `EXTI9_5`), `hal_step_abort()`/`hal_step_stop_now()` with the inlined `stepgen_halt_decide()`, and the GPIO writes are in `.RamFunc`.
- The vector table is in RAM during the operation.
- An E-stop, limit or STOP edge is therefore served **from RAM while the flash is busy**: TRUNCATE + ENA disabled ≤ 1 µs. Only flash fetches stall on the single-bank F4.
- The core callback (flash code) is deferred until the flash operation ends (§5.2). SAF-FW-005 (a)(b) therefore holds in every state.
- Not RAM-resident during the operation: the tick, so DRV_PWR/K1 supervision, idle timers and the PAUSE-request handling resume ≤ 1 ms after the pass. Hence the D-33 f bound for DRV_PWR (op time + 25 ms) (§5.7.2). The axis is idle by precondition.
- The timer is stopped anyway (SAVE is idle only), but the ENA path is what matters.

### 5.12 Status LED (PA5 / LD2)
Unchanged: slow blink = NOT_ENABLED, fast blink = ENABLED not homed, on = homed idle, flicker = moving, double blink = latch active, triple blink = CLK_FALLBACK.

### 5.13 Independent watchdog (SAF-FW-019)
- Run window: PR /8, RLR 190 → 32.5…89.9 ms (LSI 47…17 kHz, ASSUMED).
- The main loop kicks it only if the tick advanced since the last kick.
- NVM long window: `hal_wdg_set_timeout(3000)` → PR /32, RLR 4095 (≥ 2.79 s), idle only.
- The debug image freezes the IWDG on halt.
- Reset cause `RST_IWDG` is reported at boot.

### 5.14 Stream, VALID and events (FW-STR-001…006, FW-CMD-002, IF-006/007)

**DATA frames are built in the sample ISR** (`on_afe_sample`, level 3) while the stream is on:
- `t_us` = the data-ready timestamp;
- `payload_version` = 1;
- `flags` = pure `flags_data()` from the published state word, with VALID from `valid_at(t_us)`;
- `afe_raw` (i32, sign-extended);
- `setpoint_um` = `steps_to_um(pos latched at data-ready)` (A-03);
- `frame_seq` (+1 per due frame);
- `status` (u16).

The frame (26 B incl. CRC) goes to TX class D. If the UART is idle, the ISR starts the DMA itself.
- **Header SEQ** = low byte of `frame_seq` (ICD §2.2).
- **One DATA frame per conversion** (IF-007). If class D is full, the frame is dropped but `frame_seq` still advances, and the next sent frame carries `DF_OVERRUN` (FW-STR-004). OVERRUN is also set after an NVM hold or a missed conversion: Δt > 1.5 × the median period, detected in the ISR from the previous timestamp.
- **Fallback frames** (FW-STR-005): while `DS_AFE_STALE` and the stream is on, the tick sends frames at `stream.fallback_hz` with `afe_raw = PROTO_AFE_NO_DATA`, `DS_NO_AFE_DATA | DS_AFE_STALE`, and `t_us`/`setpoint_um` at assembly. Fallback frames are not samples.
- **Stream state** never gates a safety function (FW-STR-001). With the stream off, the ISR still does the load limit, MOVE_UNTIL_LOAD and the AFE ring, and only skips the frame build.
- **VALID** (pure `valid`, FW-CMD-002):
  - SET_VALID(v) publishes `{old = current, new = v, t_apply = hal_time_us()}` under `CRIT_DATA` and returns `t_apply`.
  - A frame carries `new` iff `(int32_t)(t_frame − t_apply) ≥ 0`.
  - The value is 0 at boot and never persisted.
  - The automatic clear (SAF-FW-001) uses the same mechanism with `t_apply` = the stop's timestamp, and sends EVENT VALID_CLEARED(cause) once when it was 1. The jog dead-man does not clear VALID.
- **DATA flags / status sources** (ICD §7.6; one row per bit, all host-tested in T-FLAGS):

| Bit | Source | Bit | Source |
|---|---|---|---|
| `DF_VALID` | `valid_at(t)` | `DS_PAUSED` | PAUSED latch |
| `DF_MOVING` | MS 3–7 | `DS_LIMIT_START/END` | input active or latched |
| `DF_HOMED` | flag | `DS_LOAD_LIMIT` | FAULT LOAD_LIMIT |
| `DF_ENABLED` | MS ≥ IDLE | `DS_AFE_STALE` | tick timer |
| `DF_ESTOP` | latched or input open | `DS_AFE_SATURATED` | this sample at a rail |
| `DF_HALT` | latch | `DS_AFE_SETTLING` | settle counter |
| `DF_FAULT` | any FAULT | `DS_AFE_RATE_MISMATCH` | `afe_rate` |
| `DF_OVERRUN` | drop / miss since the last sent frame | `DS_LINK_WDG` | tick |
| | | `DS_PAUSE_BTN`, `DS_ALM`, `DS_PEND` (`DS_STOP_BTN` retired: 0) | input states after polarity; 0 while their feature bit is 0 (D-37 b) |
| | | `DS_POS_UNCERTAIN` | abort/step fault, cleared by HOME |
| | | `DS_NO_AFE_DATA` | fallback frame |
| | | `DS_DRV_PWR` | drvmon (1 when sense disabled) |

**EVENT emission** (pure `evq`): the frames are built when they are sent (class E). The EVENT header SEQ = u8 event counter, incremented also for dropped events. Producers and the milestone in which each EVENT becomes live:

| EVENT | Emitted by | MS | EVENT | Emitted by | MS |
|---|---|---|---|---|---|
| BOOT | app_init | M1 | DRIVER_ENABLED / DISABLED | motion_sm, drvmon, safety | M2 |
| STOPPED, MOVE_DONE | motion_sm | M2 | PAUSE_BUTTON (STOP_BUTTON retired) | debounce/latches | M2 |
| ESTOP_SET / CLEARED | latches | M2 / M1 | ALM_CHANGED, DRIVER_POWER, NOT_SETTLED | drvmon | M2 |
| HALT_SET / CLEARED | latches | M1 | AFE_REINIT | afe | M2 |
| PAUSED / PAUSE_CLEARED (`PCLR_RESUME`, `PCLR_HALT_CLEAR`) / RESUME_REQUEST | latches | M1 / M1 / M2 | AFE_RATE_MISMATCH, AFE_STALE | afe_rate, tick | M1 |
| FAULT_SET / CLEARED | latches | M2 / M1 | PARAMS_SAVED / LOADED / DEFAULTED, NVM_ERROR | nvm | M1 |
| LIMIT_SET / CLEARED | latches | M2 | CLK_FALLBACK | app_init | M1 |
| LINK_WDG / LINK_RESTORED | safety | M2 | VALID_CLEARED | valid/latches | M1 |
| HOMED, HOME_FAILED | homing | M2 | | | |

### 5.15 Parameters runtime (`params_rt`, FW-CFG-001…003, FW-PAR-001…006)
- **Table**: the generated `PARAM_TABLE` (`params_gen.c`). The FW has no hand-written parameter list.
- **SET_PARAM** follows ICD §5.2 and the check order of §5.4.2:
  1. `param_find` → `E_PARAM_ID`;
  2. `param_validate_set` → `E_TYPE` / `E_RANGE` (never clamped);
  3. not `PARAM_F_MOVING_OK` while MS 3–7 → `E_BUSY` `BUSY_MOTION`;
  4. `param_rules_check_set()` → `E_CONFIG`;
  5. store, then **apply hook**.
- **`param_rules`** (pure, H1–H4 = ICD §11.4; oracle = `check_vectors.json` `rule_h*` + per-parameter vectors):
  ```c
  /* 0 = rules hold after setting `id` := raw; else id of the OTHER parameter of the violated rule */
  uint16_t param_rules_check_set(const params_t *cur, uint16_t id, uint32_t raw);
  /* true if H1..H4 hold for a whole image (NVM boot / LOAD rule 5) */
  bool     param_rules_check_all(const params_t *p);
  ```
  H1 `soft_min_um < soft_max_um`; H2 `load_raw_min < load_raw_max`; H3 `(u64)max_step_rate_hz · (pulse_high_ns + pulse_low_min_ns) ≤ 1e9`; H4 `v_max_load_um_s ≤ v_max_travel_um_s`.
  **H5** (D-33 a, DEF-P1-01; Integrator adds it to `params.yaml`/ICD §11.4 with the default 250 ms): `afe.timeout_ms ≥ 2 × period(afe.rate_sps)`.
  - Integer form: `afe.timeout_ms · sps ≥ 2000`, with sps = 80 (`SPS80`) or 10 (`SPS10`). That means ≥ 25 ms at 80 SPS and ≥ 200 ms at 10 SPS.
  - E_CONFIG detail: a SET of `afe.timeout_ms` returns the id of `afe.rate_sps`, and a SET of `afe.rate_sps` returns the id of `afe.timeout_ms`.
  - Each parameter belongs to at most one rule.
  - With the 250 ms default both rates are valid, and the SW lowers the rate only after raising the timeout (ICD §11.4 ordering).
  - The M1 synthetic source runs at the configured rate, so the M1 twin shows the same behaviour.
  - After an AFE reconfiguration or re-init the stale timer is re-armed from the change, so the first sample at the new rate is not counted as late.
- **Apply hooks** (effective ≤ 10 ms after the response; load thresholds from the next sample):

| Group / key | Hook |
|---|---|
| `afe.gain_channel`, `afe.rate_sps` | `hal_hx711_config()`, settle re-armed |
| `afe.*` others, `stream.fallback_hz` | read at use |
| `motion.steps_per_mm` | idle only; µm positions rescale; HOMED kept |
| `motion.pul_invert`, `motion.ena_invert`, `drv.pwr_sense_enable` | **reboot_required**: stored; `SYSF_REBOOT_PENDING` while RAM ≠ the boot-applied value |
| `motion.dir_invert`, pulse timing, speeds, accels | used at the next motion start (idle only) |
| `limits.*`, `home.*` | used at the next command / homing |
| `safety.load_raw_min/max`, `load_trip_samples`, `load_regrow_raw` | copied into the ISR `loadlim` state under `CRIT_DATA` |
| `safety.zero_raw`, `release_band_raw`, `idle_disable_s`, `link_timeout_ms` | read at use (tick/cmd) |
| `io.pause_active_level`, `drv.alm_active_level` | `hal_inputs_config()`; re-arm without a press (`io.stop_active_level` retired in dict 4) |
| `drv.alm_active_level`, `drv.pend_active_level` | applied by the tick's polling filter (ALM/PEND are polled) |
| `io.release_ms`, `io.estop_release_ms`, `drv.pend_*`, `drv.k1_weld_ms` | read at use (tick) |

- **GET_ALL_PARAMS**: 48 entries in dict v2 (−`home.ref_switch` +`drv.k1_weld_ms`) → 3 pages (152/152/68 B frames).

### 5.16 Driver facts that constrain the FW (D-16, D-27, D-28)
- **Driver**: PFDE HBS86H, a Leadshine-compatible clone. Control inputs are rated 5–24 V; 3.3 V direct drive is for bring-up only (SN74ACT244 buffer before calibration, D-28).
- **Current DIP setting** (D-27): 800 p/rev (**160 steps/mm** default), **86 open-loop, 6.0 A**, encoder unused. Consequences for the FW:
  1. **No step-loss detection exists.** Missed steps are silent and no position-following-error ALM is possible. The FW **never** uses ALM, PEND or anything else as a step-loss indicator. Measured travel = commanded position (`setpoint_um`, A-03).
  2. The only plausibility check is **HOME_DRIFT** at re-homing (§5.5). STEP_FAULT means a FW-internal overrun, not a motor step loss.
  3. ALM is used for exactly two things: reporting (`DS_ALM`, ALM_CHANGED) and the start-block for new motion starts (`BLOCK_DRIVER_ALARM` while power is present; D-28, SAF-FW-026). There is no stop of a running move (D-16). In open loop ALM can only mean over-current, over-voltage or unpowered.
  4. PEND only feeds the NOT_SETTLED warning.
  5. Nothing in the FW depends on p/rev. `steps_per_mm` is calibrated and all speeds are in µm/s. A switch to closed loop and 4000 p/rev (the D-27 recommendation, Q25) needs **no FW change**: only `motion.steps_per_mm` (800) and the measured ALM/PEND polarities (C-16).
- DIP SW5 sets the direction; `motion.dir_invert` is set at the HW gate so that +x moves away from START.

---

## 6. Timing budget analysis (SRS §6, NFR-006/007/008)

ISR entry ≈ 12 cycles (67 ns); 180 MHz = 5.6 ns/cycle. All values are design estimates (ASSUMED), to be measured at the HW gate (C-10…C-18) and in the twin's virtual time.

| Path | Chain (worst case) | Estimate | Budget | Req. |
|---|---|---|---|---|
| E-stop edge → last PUL edge | EXTI entry ≤ 0.2 µs (+ ≤ 0.2 µs `CRIT_HALT`) + TRUNCATE ≤ 0.1 µs (RAM) | **≤ 0.5 µs** | ≤ 100 µs | SAF-FW-005 (a) |
| E-stop edge → ENA disabled | same HAL handler | ≤ 0.6 µs | ≤ 1 ms | SAF-FW-005 (b) |
| Limit edge → last PUL edge | level-1 entry + E-stop ISR ≤ 0.5 + other level-1 ≤ 1 + CLEAN ≤ PW + 0.5 µs | ≤ 12 µs (PW 10 µs); ≤ 103 µs at PW max | ≤ 200 µs | SAF-FW-002 |
| HX711 data-ready → last PUL edge (load limit, MOVE_UNTIL_LOAD) | entry ≤ 4 µs + read ≤ 50 µs + compare ≤ 0.5 µs + CLEAN ≤ PW + 0.5 µs | ≤ 65 µs | ≤ 200 µs | SAF-FW-002/008 |
| STOP/HALT last byte → last PUL edge | sniffer at the next tick ≤ 1 ms + tick latency ≤ 60 µs + CLEAN ≤ 10.5 µs (the dispatcher in parallel) | ≤ 1.1 ms | ≤ 2 ms | SAF-FW-002 |
| Trigger → start of controlled deceleration (link wdg, PAUSE button/PC, dead-man, STOP 1, JOG 0) | detection ≤ 1 ms + §5.6.4 path (ISR: ≤ 1 ms; stretch: ≤ PW; halt only at P > 2 ms and d ≤ 1 step: immediate) | ≤ 2 ms | ≤ 2 ms | SAF-FW-003, D-29 d, D-30 |
| Link silence → controlled stop | `link_timeout_ms` + ≤ 1 ms | 1000 + 1 ms | +≤ 2 ms | SAF-FW-015 |
| Jog refresh missing → controlled stop | `jog_timeout_ms` + ≤ 1 ms | 250 + 1 ms | +≤ 2 ms | SAF-FW-016 |
| AFE stale while moving → stop | `afe.timeout_ms` + ≤ 1 ms | 101 ms | – | SAF-FW-012 |
| DRV_PWR input change → stop + ENA disabled + NOT_ENABLED | RC ≈ 2 ms + 20 ms filter + ≤ 1 ms tick (during an NVM pass: + op time) | ≤ 23 ms (≤ op + 23 ms) | ≤ 25 ms (≤ op + 25 ms, D-33 f) | SAF-FW-024, FW-SW-005 |
| E-stop open + power present → K1_WELDED | `drv.k1_weld_ms` + ≤ 2 ms (never before `k1_weld_ms`) | ≤ 202 ms | ≤ `k1_weld_ms` + 25 ms (D-33 f) | SAF-FW-025 |
| Sniffed STOP/HALT/PAUSE vs a move from an earlier frame | start parked by the hold (§5.9.3) | 0 pulses | 0 pulses after the stop frame | SAF-FW-002, DEF-P1-04 |
| FW hang while moving → PUL stops | IWDG ≤ 90 ms | ≤ 90 ms | ≤ 100 ms | SAF-FW-019 |
| HX711 data-ready → timestamp | levels 0–2 ahead (≤ 0.5 + 1 + 1.2 µs) + `CRIT_MOTION` ≤ 1 µs + entry | ≤ 4 µs | ≤ 5 µs | FW-TIM-001, FW-AFE-005 |
| HX711 data-ready → DATA transmission start | ISR build ≤ 60 µs + at most one frame on the wire (≤ 168 B = 1.82 ms; D goes next) | **≤ 1.9 ms** | ≤ 2 ms | FW-STR-002 (OI-FW-02 closed) |
| Command → response | ≤ 1 main-loop pass + ≤ 1 DATA frame + ≤ 1 frame on the wire | ≤ 3.5 ms; SAVE ≤ 0.6 s | ≤ 10 ms; NVM ≤ 2.5 s | NFR-008 |
| Main-loop pass | §4.2 | ≤ 0.7 ms | ≤ 1 ms (PROGRAM pass exempt) | NFR-006 |

ISR budgets (NFR-007):

| ISR / window | Estimate | Budget |
|---|---|---|
| Step ISR | 0.8–1.2 µs | ≤ 2 µs |
| Step CPU at 50 kHz | ≤ 6 % | ≤ 15 % |
| Masked windows | `CRIT_HALT` ≤ 0.2 µs; `CRIT_AFE` ≤ 0.8 µs; `CRIT_MOTION` ≤ 1 µs; `CRIT_DATA` ≤ 2 µs (masks only levels ≥ 3) | ≤ 1 µs for windows that mask a safety ISR (`CRIT_HALT` only) |
| E-stop / STOP ISR | ≤ 0.5 µs | ≤ 1 µs |
| Sample ISR | ≤ 60 µs at 80 Hz → 0.48 % CPU | – |
| Tick (incl. sniffer ≤ 6 µs at full line rate) | ≤ 30 µs typ | – |

`motion.max_step_rate_hz` ≤ 100 kHz (dictionary cap, D-29 f) keeps the step-ISR deadline ≥ 10 µs.

NFR-007 "IRQ-masked windows ≤ 1 µs" is read as **windows that mask NVIC levels 0–2** (E-stop, inputs, step) (OBS-P1-04):
- `CRIT_HALT` ≤ 0.2 µs (PRIMASK), `CRIT_AFE` ≤ 0.8 µs and `CRIT_MOTION` ≤ 1 µs (both BASEPRI 0x20, mask the step ISR) meet it.
- `CRIT_DATA` (≤ 2 µs) and `CRIT_TICK` (≤ 5 µs) mask only levels ≥ 3 / ≥ 4.
- `CRIT_NVM` masks level 2 for ≤ 0.5 s, but only while idle (no step running); levels 0–1 stay live.

---

## 7. Memory budget (STM32F446RE: 512 KB flash, 128 KB SRAM)

| Flash | Estimate |
|---|---|
| stm32duino core + HAL init | ≈ 12 KB (ASSUMED) |
| pure modules | ≈ 20 KB |
| core + hal/f446 | ≈ 22 KB |
| generated tables (48 params + names) + CRC tables (1.5 KB) | ≈ 5 KB |
| **Total** | **≈ 59 KB** of 464 KB usable; NVM 32 KB reserved |

| SRAM | Bytes |
|---|---|
| RX DMA ring / parser buffer | 2 048 / 512 |
| TX: R ring + E ring (16 × 16 B) + D mailbox (2 × 26 B) + DMA frame buffer (168 B) | ≈ 1 500 |
| AFE ring 16 × 16 B, EVENT payloads | ≈ 512 |
| params RAM image + NVM write image (512 B) | ≈ 1 000 |
| sniffer carry, motion, ramp, latches, counters | ≈ 700 |
| RAM vector table copy (during NVM ops) | 512 (aligned) |
| `.RamFunc` (level-0/1 handlers, halt/abort, flash loop) | ≈ 1 500 |
| core `.data/.bss` | ≈ 1 500 |
| main stack (painted) | 4 096 |
| heap | **0 used** (`check_map.py` asserts no allocator is linked) |
| **Total** | **≈ 14 KB** of 128 KB |

---

## 8. Host-twin seams and host tests (SYS-008, D-07)

### 8.1 Seam API (adopts the Integrator's list in `tools/README.md`; deltas marked **Δ**)
The FW owns the headers and freezes them as **seam v1** in M1-WP1 (§9). **Single source** (DEF-P1-02): from ICD v0.4 on, the seam table in `00_System/tools/README.md` is the normative copy that the twin is built against. It is the same list as below. The FW headers `src/hal/hal_*.h` implement exactly that table. If the two ever disagree, the README wins, A changes the headers, and a seam change goes through the Integrator. The target implements them in `hal/f446/`, the twin in `00_System/tools/fw_twin/`. All functions are non-blocking, allocation-free and C11. Callbacks are core functions that the HAL calls in the stated context.

```c
/* hal_uart.h */
size_t   hal_uart_read(uint8_t *buf, size_t max);                     /* consume RX bytes */
size_t   hal_uart_peek(uint8_t *buf, size_t max, uint32_t *cursor);   /* Δ sniffer: new RX bytes, no consume */
typedef enum { HAL_TX_DATA = 0, HAL_TX_RESP = 1, HAL_TX_EVENT = 2 } hal_tx_class_t;
bool     hal_uart_write(const uint8_t *frame, size_t n, hal_tx_class_t cls); /* Δ class; whole frame or false;
                                                                    HAL_TX_DATA callable from the sample ISR/tick */
size_t   hal_uart_tx_free(hal_tx_class_t cls);                        /* Δ */
bool     hal_uart_tx_idle(void);                                      /* all classes empty and line idle */
uint32_t hal_uart_rx_overruns(void);
/* hal_time.h */
uint32_t hal_time_us(void);   uint32_t hal_time_ms(void);
void     core_tick_1ms(void);                                         /* Δ callback, level 4, every 1000 µs */
/* hal_step.h */
typedef struct { uint32_t pw_ticks, dir_setup_ticks; bool pul_invert, ena_invert; } hal_step_cfg_t;
uint32_t hal_step_init(const hal_step_cfg_t *cfg);                    /* Δ returns f_tick (90 MHz target) */
void     hal_step_set_dir(int dir);                                   /* only while stopped; v1.3: +-1, +-2 = DIR inverted */
void     hal_step_start(uint32_t first_period_ticks);                 /* first edge >= dir_setup after the call */
void     hal_step_set_period(uint32_t ticks);                         /* preload: period after the running one */
void     hal_step_set_period_now(uint32_t ticks);                     /* Δ stretch running period (§5.6.4) */
void     hal_step_arm_last(void);                                     /* Δ OPM: stop at the end of this period */
bool     hal_step_stop_now(void);                                     /* CLEAN, no runt; true = pulse completes */
bool     hal_step_abort(void);                                        /* Δ TRUNCATE; true = pulse cut (uncertain) */
int32_t  hal_step_count(void);                                        /* signed position counter, steps */
void     hal_step_set_count(int32_t steps);                           /* Δ homing zero shift, only while stopped */
bool     hal_step_running(void);
uint32_t hal_step_stop_gen(void);                                     /* Δ start-then-recheck counter */
void     hal_ena_set(bool enabled);                                   /* polarity from hal_step_init */
typedef struct { uint32_t period; bool last; bool stop; } step_next_t;
step_next_t step_isr(void);                                           /* callback, level 2, per completed pulse */
/* hal_inputs.h  — ids = ICD IO bit indices 0..7 */
uint16_t hal_inputs_raw(void);                                        /* electrical levels (1 = pin high) */
typedef struct { uint8_t pause_active_level, alm_active_level; } hal_in_cfg_t; /* v1.3: STOP input retired */
void     hal_inputs_config(const hal_in_cfg_t *c);                    /* Δ polarity for the fixed reactions */
void     hal_inputs_rearm(uint8_t id);                                /* Δ re-enable a self-masked line */
void     on_input_edge(uint8_t id, bool level, uint32_t t_us);        /* callback, level 0/1, AFTER the HAL's
                                                                         fixed reaction (§5.2); deferred during flash ops */
/* hal_outputs.h */
void hal_rate_pin(bool high);  void hal_trip_relay(bool trip /* never true in release 1 */);  void hal_led(bool on);
/* hal_hx711.h */
typedef struct { uint32_t t_us; int32_t raw; int32_t pos_steps; uint8_t status; } afe_sample_t; /* Δ struct */
void hal_hx711_config(uint8_t gain_pulses, bool rate80);
void hal_hx711_powerdown(bool on);
void hal_hx711_kick(void);                                            /* Δ missed-edge recovery */
void hal_hx711_hold(bool on);                                         /* Δ NVM hold */
void on_afe_sample(const afe_sample_t *s);                            /* callback, level 3, after the latch */
/* hal_flash.h */
bool hal_flash_erase(uint32_t sector);
bool hal_flash_program(uint32_t addr, const void *src, size_t n);
const void *hal_flash_map(uint32_t addr);
/* hal_sys.h */
void hal_wdg_kick(void);  void hal_wdg_set_timeout(uint32_t ms);
uint8_t hal_reset_cause(void);            /* RST_* (target: pure resetcause on RCC->CSR) */
void hal_reset(void);  void hal_uid(uint8_t uid[12]);  bool hal_clk_fallback(void);
uint16_t hal_stack_free_min(void);        /* Δ */
bool   hal_fault_record(uint32_t *pc, uint32_t *cfsr);                /* v1.2: HardFault record, once */
size_t hal_meas_cmd(const uint8_t *req, size_t n, uint8_t *resp, size_t max); /* v1.3: DIAG_MEAS, 0 = not in build */
/* critical sections (seam v1.1, adopted by the Integrator in tools/README): HALT = PRIMASK,
   AFE/MOTION = BASEPRI 0x20, DATA = 0x30, TICK = 0x40; no-ops in the twin */
typedef enum { HAL_CRIT_HALT = 0, HAL_CRIT_AFE = 1, HAL_CRIT_MOTION = 2, HAL_CRIT_DATA = 3, HAL_CRIT_TICK = 4 } hal_crit_level_t;
typedef uint32_t hal_crit_t;
hal_crit_t hal_crit_enter(hal_crit_level_t level);
void       hal_crit_exit(hal_crit_t saved);
/* usage: CRIT_BEGIN(HAL_CRIT_DATA); ... CRIT_END();  (macros in hal_sys.h) */
```
The Δ items are needed by §5.2/§5.3/§5.6/§5.9/§5.14 and are proposed to the Integrator (**OI-FW-17**). Twin semantics for each Δ:
- `peek` = a second read cursor;
- TX classes = the wire order D > R > E with TCP pacing at 92 160 B/s;
- `abort` = cut + uncertain flag;
- `set_period_now` / `arm_last` = applied to the virtual counter;
- `hold` = no samples delivered;
- `kick` = no-op;
- `rearm` = no-op.

### 8.2 Twin execution rule
ISR callbacks run atomically at their virtual times in NVIC-level order (0 E-stop, 1 inputs, 2 step, 3 sample, 4 tick, 5 link); the main loop runs between events. All timing acceptance tests of SAF-FW-002/003/005/015/016/017 and FW-STR-002 run in virtual time.

### 8.3 What the twin cannot show
The twin cannot show the RAM-resident behaviour during flash erase (C-04), the opto/pin electrical levels, or real ISR durations (C-18). These remain hardware-gate items.

### 8.4 Vectors in place — the Unity pre-script (`02_FW/tools/gen_test_vectors.py`, IF-010)
- **Inputs, read in place** (never copied): `00_System/tools/vectors/protocol_vectors.json`, `check_vectors.json`, and any further `vectors/*.json` the Integrator adds (units/motion, **OI-FW-20**). For name/bit and parameter-key → (id, type) conversion the script imports `00_System/tools/ref_codec.py` and `gen_params.load()` in place (test tooling only, never FW code).
- **Consistency gate** (build error = stale combination):
  - `vectors.param_dict_hash` == `PARAM_DICT_HASH` (`params_gen.h`);
  - `vectors.icd_version` == `PROTO_ICD_VERSION` (`proto_gen.h`, from `protocol.yaml`);
  - it also runs `gen_params.py --check` and `gen_vectors.py --check`.
- **Output**: `$BUILD_DIR/vectors/vec_crc.h`, `vec_frames.h`, `vec_streams.h`, `vec_check.h`, generated at every native build, not in the repo:
  - frames: `{name, dir, kind, type, seq, payload_hex, frame_hex, reencode, canonical_payload_hex}` plus a **C designated initializer of the FW payload struct** produced from `decoded`. Bit lists become masks via the `ref_codec` tables.
  - check vectors: `{name, state (cmd_ctx_t initializer incl. param overrides as (PID, raw) pairs), type, payload_hex, expect status/detail, response_frame_hex, paused_after}`.
- **Consumption** (tools/README table): decode PC→FW requests and compare them with the initializer; encode FW→PC frames from the initializer and compare byte for byte with `frame_hex`; feed the `streams` chunks to a fresh parser and check frames and counters; run `cmd_check` per check vector and compare the encoded NACK with `response_frame_hex`; for accepted commands, check the PAUSED execution effect against `paused_after`.

### 8.5 Implementer A host suites (`test/test_impl_*`, Unity, env:native)

| Suite | Content / oracle | Req. | MS |
|---|---|---|---|
| `test_impl_crc` | 4 CRC vectors; CRC-32 check value | IF-004, FW-NVM-002 | M1 |
| `test_impl_frame` | 11 `streams` (frames + 4 counters), invalid TYPE counting | IF-003/004 | M1 |
| `test_impl_payload` | 150 `frames`: decode requests, encode responses/DATA/EVENT/INFO/STATUS(86) byte-identical, `reencode` cases | IF-006, FW-STR-003, FW-CFG-002 | M1 |
| `test_impl_check` | all `check_vectors.json` cases (455 in v0.1; v0.2 count from the file), no side effect on NACK | FW-CMD-001, FW-CFG-003, SAF-FW-020/021/022, FW-CMD-003 | M1 |
| `test_impl_rules` | `rule_h1…h5` vectors + exhaustive pairs at the range ends (H5 at 10 / 80 SPS), `check_all` | FW-CFG-003, SAF-FW-010, SAF-FW-012 | M1 |
| `test_impl_names` | every `proto_gen.h` code/bit equals `ref_codec` (via the pre-script), struct sizes = `PROTO_*_LEN` | IF-001 | M1 |
| `test_impl_nvm` | slot log, power cut after every program word and during erase (fake flash), boot rules 2–5, migration dict v1 → v2 (retired 0x0401, new 0x0705), CFG_DIRTY definition, seq wrap | FW-NVM-001/002 | M1 |
| `test_impl_valid` | modular boundary incl. the 2³² wrap, auto-clear cause, VALID_CLEARED once | FW-CMD-002, SAF-FW-001 | M1 |
| `test_impl_stream` | `frame_seq`/OVERRUN under forced class-D congestion, fallback rate, stream-off safety path | FW-STR-001/004/005 | M1 |
| `test_impl_txsched` | wire order D > R > E, drop policy, R never dropped | FW-STR-002, IF-011 | M1 |
| `test_impl_evq` | ring, SEQ incl. drops, `event_overflows` | FW-STR-006 | M1 |
| `test_impl_flags` | every DATA/STATUS bit from a constructed state | FW-STR-003, FW-CMD-004 | M1 |
| `test_impl_sniff` | STOP/HALT/PAUSE hits, split across ticks, bad CRC, STOP mode 2 ignored, sync in payload, false-positive bound; **RESUME / clears never sniffed**; **hold**: MOVE_ABS + HALT burst split across passes with α = 10⁹ → 0 pulses, hold timeout → parked start discarded | SAF-FW-002/003, DEF-P1-04 | M1 |
| `test_impl_latch_m1` | HALT/PAUSE/STOP/clears without motion: idempotency for 20 CONFIRM repeats (1 event); motion refused with BLOCK PAUSED while PAUSED; **RESUME** clears only PAUSED, refused (E_STATE HALT/ESTOP/FAULT) while latched, no-op when not PAUSED; HALT_CLEAR clears HALT + PAUSED; FAULT_CLEAR all-or-nothing; `pause_src` | SAF-FW-022/023, FW-CMD-003, D-30, D-31 | M1 |
| `test_impl_units` | um↔steps (R4 TV-TC numbers via the Integrator's vectors, OI-FW-20) | SYS-003, FW-MOT-002 | M1 |
| `test_impl_rate`, `test_impl_resetcause`, `test_impl_core_link` | `afe_rate` median/mismatch; CSR precedence; core link with fake seams (dispatch budget, one response per command, NVM HOLD blocks dispatch, **flood: R back-pressure, no lost or shortened response, RX overrun counted**) | FW-AFE-004, SAF-FW-018, FW-CMD-001, FW-NVM-003 | M1 |
| `test_impl_ramp`, `test_impl_planner` | R4 TV-M (±1 tick per period, sums exact) | FW-MOT-003 | M2 |
| `test_impl_stepgen` | halt decision for every CNT vs CCR1/guard, stretch decision, controlled-stop path (P × d grid: halt only at P > 2 ms and d ≤ 1 step, D-29 d / D-30), last-step arm, overrun | SAF-FW-003/004 | M2 |
| `test_impl_motion`, `test_impl_homing` | §5.4/§5.5 scenarios incl. START-only, release bound, drift 0.1 mm (no fault) / 0.3 mm (HOME_DRIFT), END = HOME_WIRING, ABORTED | FW-MOT-004…009, FW-HOM-001/002/004 | M2 |
| `test_impl_latches`, `test_impl_debounce`, `test_impl_drvmon`, `test_impl_loadlim` | full ICD §6.2 table; limit latch clears after `io.release_ms` without a position condition (D-33 h); idle disable never while stale (D-33 g); DRV_PWR 20 ms filter (19 ms toggle ignored, loss reaction ≤ 25 ms, any cause, events with/without a prior E-stop, + op time after a flash pass), K1 timer (no fault at 150 ms, fault in (200, 202] ms, clear refused while the cause persists), ALM polling filter (chatter at 5 kHz → ≤ 1 event pair per `io.release_ms`), ALM start-block scope; trip samples, rails, regrow | SAF-FW-001…026, FW-SW-005 | M2 |

Evidence before every hand-back (role rule): `pio test -e native` green with counts, and `pio run -e nucleo_f446re` SUCCESS with flash/RAM figures.

---

## 9. M1 work breakdown (P2 start right after the PO gate)

### 9.1 Scope of the FW in M1 (PROCESS M1 "link & skeleton")
- Board init, clock, IWDG, reset cause.
- UART DMA link, framing + CRC, all 25 commands at the check level (vectors).
- Execution of the system, parameter, NVM, stream and VALID commands, and of STOP/HALT/PAUSE/clears without motion.
- Streaming of **synthetic** 80 Hz samples through the real DATA path.
- The frozen seam v1 for the Integrator's twin.

M1 requirements: FW-PLT-001/002, FW-CFG-001…004, FW-NVM-001…003, FW-CMD-001…004, FW-STR-001…004/006, FW-STR-005 (fallback after a synthetic stall), FW-TIM-001 (synthetic timestamps), FW-PAR-001…006, IF-001…012 (FW side), SYS-003, SYS-008, SYS-010, NFR-005/006.

### 9.2 Work packages (in order; each ends with its tests green)

| WP | Files (all under `02_FW/` unless noted) | Host tests (§8.5) | Depends on | Hand-off |
|---|---|---|---|---|
| **WP0** environment (day 1) | `platformio.ini` (3 envs), `tools/host_env.ps1`, `tools/native_flags.py`, `ldscript/bend_f446re.ld`, `include/irq_prio.h`, `include/board_pins.h`, `src/main.cpp` stub | empty `test_impl_smoke` | MinGW 13.1 + `.venv` pio (VERIFIED present) | baseline empty-image size |
| **WP1** seam v1 + names | `src/hal/hal_*.h` (8 headers, §8.1), `src/pure/proto.h` (wraps `gen/proto_gen.h`), `include/fw_config.h` | `test_impl_names` | `proto_gen.h` v0.4 (RESUME, E_INTERNAL codes), seam v1 single-sourced in `tools/README.md` (DEF-P1-02 / OI-FW-17) | **seam v1 to the Integrator** → `fw_twin` can start in parallel |
| **WP2** codec | `pure/crc16.*`, `crc32.*`, `le.h` (TS copies + origin), `pure/frame.*`, `pure/payload.*`, `tools/gen_test_vectors.py` | `test_impl_crc`, `_frame`, `_payload` | vectors v0.2 (Integrator) | – |
| **WP3** acceptance + parameters | `pure/cmd_check.*`, `pure/param_rules.*`, `core/params_rt.c` | `test_impl_check`, `_rules` | WP2 | – |
| **WP4** NVM | `pure/nvm_log.*`, `core/nvm.c`, `test/common_impl/fake_flash.*` | `test_impl_nvm` | WP3 (`param_rules_check_all`) | – |
| **WP5** link core | `pure/txsched.*`, `evq.*`, `valid.*`, `flags.*`, `stop_sniff.*`, `stream_sched.*`, `afe_rate.*`, `resetcause.*`, `units.*`; `core/link.c`, `cmd.c`, `stream.c`, `events.c`, `status.c`, `afe.c` (sample callback, synthetic only), `app.c`; `test/common_impl/fake_hal.*` | `test_impl_txsched`, `_evq`, `_valid`, `_stream`, `_flags`, `_sniff`, `_rate`, `_resetcause`, `_units`, `_core_link` | WP2–WP4; units vectors (OI-FW-20) | – |
| **WP6** stop latches without motion | `pure/latches.*` (M1 subset: HALT, PAUSED, **RESUME**, VALID clear, clears, FAULT mask with causes from state) | `test_impl_latch_m1` | WP5 | – |
| **WP7** target HAL | `hal/f446/clock.c`, `board_init.c`, `time_tim5.c` (+tick, +CC2 synthetic), `afe_synth.c`, `uart2_dma.c`, `flash_f4.c` (RAM VTOR + `.RamFunc`), `iwdg.c`, `sys_f4.c`, `dwt.h`; `tools/build_info.py`, `tools/check_map.py` | (target build only) | WP1, WP5 | **release + debug build SUCCESS, flash/RAM figures, map check** |
| **WP8** twin integration | fixes only | Integrator's twin smoke: connect, GET_INFO, GET_ALL_PARAMS (3 pages), SET/SAVE/REBOOT/LOAD, NVM power cut, STREAM 10 min (frames = conversions, `frame_seq` gap-free), SET_VALID boundary, STOP/HALT/PAUSE confirm path via GET_STATUS | Integrator `fw_twin` (M1), Validator E suites | M1 FW evidence to the Orchestrator |

Parallelism: WP1 unblocks the Integrator on day 1–2. WP2–WP6 are pure/host only. WP7 can start after WP1 and run alongside WP3–WP6.

### 9.3 M1 exit evidence (Implementer A)
- `pio test -e native`: all suites green with case counts. Expected ≈ 4 + 11 + 150 + 455 + ≈ 300 own cases.
- `pio run -e nucleo_f446re` and `-e nucleo_f446re_debug`: SUCCESS, with flash/RAM figures (target ≤ 70 KB / ≤ 20 KB), `check_map.py` PASS (no allocator/`printf`/`HardwareSerial`, nothing in sectors 1–2, handler slots, `.RamFunc` contains the level-0/1 handlers).
- `gen_params.py --check` / `gen_vectors.py --check` clean.
- GET_INFO feature mask = `FEAT_AFE_SYNTHETIC | FEAT_NVM` (+ `FEAT_TWIN` in the twin).
- No hardware access (D-06).

### 9.4 Commands (PowerShell)
```powershell
$env:PATH = "C:\Program Files\JetBrains\CLion 2025.3.2\bin\mingw\bin;$env:PATH"   # or: . 02_FW\tools\host_env.ps1
.venv\Scripts\pio test -d 02_FW -e native                                        # host suites
.venv\Scripts\pio run  -d 02_FW -e nucleo_f446re                                 # release build + sizes
```

### 9.6 M1 as implemented (v0.4) — notes and deviations from §1–§9.5

**Files (02_FW).** `platformio.ini` (envs `nucleo_f446re`, `nucleo_f446re_debug`, `native`), `ldscript/bend_f446re.ld`, `include/{board_pins,irq_prio,fw_config}.h`, `tools/{build_info,check_map,gen_test_vectors,native_flags}.py`, `tools/host_env.ps1`; `src/main.cpp`; `src/hal/hal_*.h` (seam v1.1); `src/pure/`: crc16, crc32, le.h, proto.h, frame, payload, cmd_check, param_rules, nvm_log, units, valid, flags, evq, latches, txsched, stop_sniff, stream_sched, afe_rate, resetcause; `src/core/`: fw.h, app, link, cmd, params_rt, nvm, afe, stream, status, events, led, m1_stubs; `src/hal/f446/`: f446.h, clock, board_init, time_tim5, afe_synth, uart2_dma, flash_f4, iwdg, sys_f4; `test/common_impl/` (fake seams, harness, vector helpers); `test/test_impl_*` (19 suites). Reused code (origin notes in the files): TS @37c8747 crc16, crc32, le.h, frame, stream_sched (copied), nvm_codec (adapted into nvm_log), build_info.py / check_map.py / gen_test_vectors.py / host_env.ps1 (adapted), ldscript pattern.

| # | Item | Design | As implemented (rationale) |
|---|---|---|---|
| 1 | Sniffed-stop hold | RX position of the frame | TYPE/SEQ of the latest sniffed frame + 20 ms safe-side timeout (§5.9.3; robust to RX overruns) |
| 2 | Seam | §8.1 v1 | v1.1 = `tools/README.md`: `hal_in_cfg_t` incl. `alm_active_level` (README wins), critical-section API added (adopted by the Integrator). Semantics fixed by the FW and the fakes: `hal_uart_tx_free()` = largest frame the class accepts now; `hal_uart_peek()` cursor = absolute RX byte count since boot (the core starts it at 0); `hal_flash_erase(1 or 2)` = F4 sector numbers, `hal_flash_program()` addresses 0x0800 4000…0x0800 BFFF, word multiples, at most 512 B per call; `hal_wdg_set_timeout(ms)`: ms > 90 = NVM long window, else run window |
| 3 | LOAD / DEFAULT | started via the NVM state machine | executed synchronously in the handler (RAM only, no flash write, < 1 ms); only SAVE uses HOLD → QUIESCE → PROGRAM → VERIFY → DONE |
| 4 | LOAD with a hard-rule violation | rule 5: PARAMS_DEFAULTED(HARD_RULE) | `E_NVM` NVMD_NO_RECORD, RAM unchanged, **no** EVENT (ICD §5.2 names none for LOAD; PARAMS_DEFAULTED would announce defaults that were not applied) |
| 5 | EVENTs / AFE during SAVE | not specified | EVENT flush suspended while an NVM operation runs (QUIESCE needs an idle line); the AFE stale verdict is suspended during the AFE hold and the stale timer re-armed at DONE (no AFE_STALE pair per SAVE); the missed conversions still give OVERRUN on the next frame |
| 6 | M1 input-derived state | §9.1 | no input is sampled in M1 (FEAT_BUTTONS / FEAT_DRV_SIGNALS = 0): E-stop, limits, buttons, ALM, PEND read inactive; **driver power is "not confirmed"** while `drv.pwr_sense_enable` = 1 (fail-safe): `DS_DRV_PWR` = 0 and ENABLE / motion → `E_STATE` DRV_UNPOWERED. With sensing disabled, motion commands that pass the check answer `E_INTERNAL` NOT_IN_BUILD (§5.10). D-36: no STOP-button input path; HALT_CLEAR sees `stop_btn_active` = false |
| 7 | Motor outputs in M1 | §3.2 step 6 | PA0 / PA1 / PA4 stay in the reset state (Hi-Z = no LED current = holding, D-13); TIM2 is not configured, so no PUL edge is possible. The boot ENA rule (E-stop / DRV_PWR) arrives with the inputs in M2 |
| 8 | Synthetic AFE | TIM5 CC2 | TIM5 CC2 records the data-ready time and pends the **EXTI4 vector** (level 3), so the sample callback runs at the M2 HX711 level; sawtooth ±400 000 counts over 10 s + noise, scaled by the gain |
| 9 | `hal_time_ms()` | maintained by the tick | accumulated from elapsed µs in the tick (catches up after a masked flash operation); CC1/CC2 compares are re-based when they fell behind the counter |
| 10 | VALID | §5.14 | `valid_settle()` in the tick folds a boundary older than 1 s (keeps the int32 comparison inside 2^31 µs); a second automatic clear while already 0 keeps the earlier boundary |
| 11 | BOOT EVENT value/value2 | HardFault record | the record is written to `.noinit` (sys_f4.c) but the seam has no accessor yet → BOOT carries 0/0 in M1 (**OI-FW-32**) |
| 12 | NVM record | TS format | magic "BDNV" (0x564E4442), layout 1, 45 entries (session values excluded), 392 B in a 512 B slot; program order entries → header bytes 0..27 → header CRC (commit marker) |
| 13 | DMA RX laps | HT/TC | TC only; a pending, not yet served TCIF is folded into the write position (the sniffer peeks from the level-4 tick) |
| 14 | Unused pins | analog at boot | left in the reset state in M1 (only LED, RATE, TRIP and USART2 are configured) |
| 16 | reboot_required parameters (**DEF-M1-01**, fixed) | §5.15 | every `PARAM_F_REBOOT` parameter (today `motion.pul_invert`, `motion.ena_invert`, `drv.pwr_sense_enable`) is stored at once (REBOOT_PENDING) but behaviour uses the boot image `g_fw.boot_p`; `cmd_check()` gets the *effective* image `params_rt_effective()` - both derived from the generated flags, no hand list (OBS-M1-08) - no `cmd_ctx_t` / `state_schema` change, the vector `params` already model the effective configuration |
| 17 | OVERRUN (**DEF-M1-02/-03/-04**, fixed) | §5.14 "Δt > 1.5 × period" | only FW losses (ICD §7.6 bit 7): class-D drops, and conversions missed during the FW's own AFE hold (Δt checked only on the first sample after a hold, and only if the stream was on when the hold started - latched at NS_HOLD); a sensor stall is no FW loss; a pending OVERRUN survives STREAM_STOP/START (cleared only by a sent frame) |
| 15 | Native env | `build_src_filter` pure + gen + core | also `+<../test/common_impl/>`: the fake seams are linked into every native suite (core needs every seam). Validator E suites in the same env either reuse them or get a separate env (**OI-FW-34**) |

**Evidence (2026-10-03):** `pio test -e native` 19 suites / 94 test cases green twice (509 check vectors, 164 frame vectors, 11 streams, 280 units values consumed in place); `pio run -e nucleo_f446re` SUCCESS: flash 24 676 B, RAM 6 536 B static (10 968 B incl. the 4 KB stack reserve); `check_map.py` M-1…M-4 PASS (NVM hole empty, 7 handler slots, no allocator/printf, RamFunc in RAM), negative control (`--slot`) fails as expected; debug image SUCCESS.

### 9.7 M2 work breakdown (sensor & motion, D-39) and as-built notes (v0.5)

**Scope.** SRS v0.5.1 M2 requirements on the FW side: SAF-FW-001…021, -023…026 (-022 withdrawn), FW-AFE-001…005, FW-MOT-001…005/007…009 (-006 = M4), FW-HOM-001…004, FW-SW-001…005, FW-STR-005/006, FW-TIM-001, FW-CMD-003, NFR-006/007; CR-01 (ICD v0.5), D-37 b, D-40 a/c/d (ICD v0.6). MOVE_UNTIL_LOAD stays NOT_IN_BUILD (FEAT_MOVE_UNTIL_LOAD = 0). No STOP-button input (D-36).

| WP | Content | Files (02_FW) | Host tests | Seams needed | State |
|---|---|---|---|---|---|
| M2-WP0 | ICD v0.5/v0.6 alignment: CR-01 (STOP_BTN, `stop_btn_*` ctx fields, HALT_CLEAR never refused, HALT source PC only), dict 4 (47 params), check vectors schema 2 (keys kept, never set), D-37 b feature-bit validity, DIAG_MEAS check rules | `pure/cmd_check.*`, `pure/flags.*`, `pure/latches.*`, `pure/proto.h`, `pure/payload.*`, `core/status.c`, `tools/gen_test_vectors.py` | `_check` (507 + 29 hw_meas vectors), `_flags`, `_latch_m1`, `_payload`, `_names`, `_smoke` | v1.3 `hal_in_cfg_t` (SR-M2-02) | done |
| M2-WP1 | pure motion: exact ramp (ref_motion.py definitions), stop-path rule, extend-only stretch, CLEAN/TRUNCATE decisions, planner | `pure/ramp.*`, `pure/stepgen.h` | `_ramp` (motion_vectors 9 cases / 28 paths / 3 plans, TV-M spot values) | – | done |
| M2-WP2 | pure safety: input filters, driver monitor, load limit (D-40 d), homing phases, HX711 math + sequence (TS @37c8747) | `pure/inputs.h`, `pure/drvmon.*`, `pure/loadlim.*`, `pure/homing.*`, `pure/hx711_math.*`, `pure/hx711_seq.h` | `_m2pure` (+ loadlim_vectors 12 cases), `_hx711` (20 000 words × 3 gains vs a device model) | – | done |
| M2-WP3 | core motion executor | `core/motion.c` | `_motion` (fake step timer with twin semantics) | v1.3 `hal_step_set_dir` ±2 (SR-M2-01) | done |
| M2-WP4 | core inputs / supervision | `core/safety.c` | `_safety`, `_homing` (switch world) | – | done |
| M2-WP5 | AFE core: load limit in the sample ISR, status bits, re-init, kick, stale → AFE_FAULT | `core/afe.c` | `_safety` | v1.2 AFES_* semantics | done |
| M2-WP6 | target HAL: TIM2 step generator, EXTI inputs (RAM handlers), HX711 shim (EXTI4/PB4, SCK PB10, RATE PB5), HardFault record, boot order | `hal/f446/step_tim2.c`, `exti.c`, `hx711_f4.c`, `sys_f4.c`, `board_init.c`, `flash_f4.c` (`g_flash_op`), `tools/check_map.py` (12 slots, 9 RamFuncs) | release build + check_map | v1.2 `hal_fault_record` | done |
| M2-WP7 | CR-02 measurement images (REQ-A-M2-03): `nucleo_f446re_meas` / `_meas_dwt`, `hal/f446/meas_f4.c`, DIAG_MEAS forwarder, measurement-header pins | `hal/f446/meas_f4.c`, `include/board_pins.h`, `platformio.ini`, `tools/build_info.py`, `core/build_id.c` | `_check` hw_meas vectors; object comparison release ↔ meas | v1.3 `hal_meas_cmd` | done except DWT stats (OI-FW-37) |
| M2-WP8 | twin / integration evidence, docs (this §, pinout v0.4, wiring v0.4) | – | twin build via `build.py`; Integrator's integration suite | – | done |

**Order / parallelism.** WP0 first (vectors gate the native build); WP1/WP2 pure and independent; WP3 needs WP1, WP4/WP5 need WP2 + WP3; WP6 needs WP3–WP5 (core API); WP7 after WP6.

**As-built notes and deviations (v0.5)**

| # | Item | Design | As implemented (rationale) |
|---|---|---|---|
| 1 | motion_sm / stepgen_core | pure modules | the executor is `core/motion.c` (seam calls are inseparable from the state); the decisions are pure (`ramp`, `stepgen.h`, `homing`) and the executor is host-tested on the fake seams with the twin's step-timer semantics |
| 2 | Ramp | float32, integer virtual indices | definitions of `ref_motion.py` (OI-ICD-09): real-valued virtual indices for speed-up (k0 = v²/2α) and slow-down (R), stop r0 = ceil(v²/2a_stop) computed in binary64, r = min(r_move, r0), c = max(c_last, D_s(r)); motion_vectors.json max period deviation 1 tick |
| 3 | Step completion | step ISR finalises at N | the timer stops by OPM at the last pulse; `step_isr()` is not called for the final update (twin semantics); the tick sees `!hal_step_running()` ≤ 1 ms later and sends MOVE_DONE. A stop short of the planned end with no recorded cause for 3 ticks → STEP_FAULT (internal inconsistency guard) |
| 4 | Missed update (STEP_FAULT) | UIF set again at ISR exit | the core checks that the count advanced by exactly one step per `step_isr()` call; the target HAL adds the missed step when the update ISR enters > 1.5 periods after the previous one (DWT), so the same check fires (twin: `inject step_fault`) |
| 5 | Controlled stop paths | §5.6.4 | ISR path: `ramp_stop(extra = 1)`, preload of c_dec1 at the next update (≤ 1 ms + P); stretch path: running + preloaded periods regenerated (`set_period_now(c1)` + preload c2); clean halt when P > 2 ms and d ≤ 1 step. Ramp edits are computed on a copy outside CRIT_MOTION and committed in ≤ ~0.5 µs if the step ISR did not advance meanwhile (`ramp_commit`, NFR-007). Stop distance = ceil(d) + ≤ 1 committed step |
| 6 | DIR polarity | – | `hal_step_set_dir(±2)` = DIR inverted (`motion.dir_invert`); adopted as seam v1.3 (SR-M2-01) |
| 7 | Jog reversal | decel to 0, restart | one motion (one MOVE_DONE): decel with the jog accel (no STOPPED), restart in the new direction at standstill; un-homed bound origin = the restart point |
| 8 | Limit latch | design §5.3 | an active START/END edge latches LIMIT_x also while idle (reported + latched, D-33 h release rule); a START edge expected by homing never latches; END during homing latches LIMIT_END and gives HOME_WIRING. The twin does not mask lines: a bounce on leaving an active switch re-stops the motion (safe side; target masks after the first edge) |
| 9 | Homing release / backoff | §5.5 | segment end = origin + HOME_RELEASE_MAX; on a stable release (`io.release_ms`) the end moves to pos + backoff; a stop by a START bounce edge re-issues the same segment (origin kept) |
| 10 | Load-limit trip in the ISR | record → tick | the ISR stops (CLEAN), sets FAULT LOAD_LIMIT and clears VALID at the sample time so the deciding DATA frame already carries them; the tick emits FAULT_SET(raw), STOPPED, VALID_CLEARED; a FAULT_CLEAR folds a pending record first (no lost FAULT_SET). Regrow window = D-40 d (loadlim_vectors.json) |
| 11 | E-stop POS_UNCERTAIN | TRUNCATE result | the core cannot see `hal_step_abort()`'s result from the HAL reaction: POS_UNCERTAIN is set whenever the E-stop hit a running motion (conservative; HOMED is cleared anyway) |
| 12 | ENA output state | – | `g_fw.ena_on` (IO_ENA_DISABLED); boot rule ENA disabled if E-stop open or (sense on and DRV_PWR off) (ICD §6.4); DRIVER_DISABLED only when the state was not NOT_ENABLED, plus a PC DISABLE that releases the boot holding level |
| 13 | Input callbacks during flash ops | deferred by the HAL | the RAM handlers skip `on_input_edge()` while `g_flash_op` is set; the tick's level checks pick the state up after the operation (E-stop open and not latched → trip; PAUSE active and not pressed → press; limits → backup stop) |
| 14 | HX711 | TS shim | ported to EXTI4 (own vector), DWT-measured SCK high (> 50 µs or DOUT not high → AFES_SCK_OVERRUN → core re-init + AFE_REINIT + settle), kick flag → AFES_MISSED_EDGE → OVERRUN; settle = exactly `afe.settle_discard` samples (FW-AFE-003). The synthetic source stays only in the bring-up image `nucleo_f446re_synth` (FEAT_AFE_SYNTHETIC) |
| 15 | Debug markers | PC8/PC9 | retired (no scope, CR-02): the pins are J-ENA / J-DIR of the measurement header |
| 16 | HW_MEAS | FW_test_plan §6.3 | `meas_f4.c` only (+ weak defaults `meas_start()` / `hal_meas_cmd()` in the release image); objects of core / pure / safety handlers are byte-identical between `nucleo_f446re` and `nucleo_f446re_meas` (only `build_id.c.o` and `meas_f4.c.o` differ; the build id moved into its own object for that). One shared hook: the RAM flag `g_meas_static` (STATIC_LEVEL release at `hal_step_start` / `hal_ena_set`), always 0 in the release image. `_meas_dwt` = `_meas` + build suffix until the DWT statistics exist (OI-FW-37) |
| 17 | NOT_SETTLED | after the last pulse | started at every MOVE_DONE in IDLE when `drv.pend_timeout_ms` ≠ 0; cancelled by PEND, ALM, power loss or a new motion |

**Seam / ICD requests (to the Integrator) — status:** SR-M2-01 `hal_step_set_dir` ±2 and SR-M2-02 `hal_in_cfg_t` without `stop_active_level`: adopted in seam v1.3. v1.2 `hal_fault_record` and v1.3 `hal_meas_cmd`: implemented. OI-ICD-10 (Appendix C word layouts): confirmed with the deviations listed in OI-FW-38.

**New open items (v0.5)**

| ID | Item | Addressee |
|---|---|---|
| OI-FW-35 | Twin: after a HAL fixed-reaction halt inside an update (limit edge at the counted step) the twin still calls `step_isr()` once while stopped; harmless for this core (it ignores calls while not running), noted as a semantic difference to the target | Integrator |
| OI-FW-36 | Validator suites not yet on ICD v0.6: `02_FW/test/twin/test_val_m2_oracles.py` asserts `icd_version == "0.5"`, `test_val_twin_link.py` asserts 26 commands (27 with DIAG_MEAS) | Validator E |
| OI-FW-37 | `HW_MEAS_DWT` DWT section statistics (op 9) and the per-ISR / per-CRIT stamps are not implemented yet (w0 = 0; INFO variant = MEAS only) — needed for HG-18 / TC-NFR-007-01 | A (before the HW gate) |
| OI-FW-38 | Appendix C confirmation (OI-ICD-10): implemented as written; facts the FW fixes: INFO w4 = 2048 stamps per channel (RAM), w7 = 10 MHz stimulus clock; PROBE_READ w1 = 0 (counter starts at the event), w5 = PUL stamps since arming, TRIGGERED = probe counter running (trigger / reset mode); STIM_RUN delay span = the running step period (TIM2 ARR), else 1 ms; STATIC_LEVEL acts only with the step timer stopped. ASSUMED until HG-29: DMA2 stream / channel map and DMA2 reading TIM5->CNT on APB1 | Integrator, Validator E |
| OI-FW-39 | Stop distance of a controlled stop on the ISR path is ceil(v²/2a) + 1 step already preloaded (≤ the SAF-FW-003 ± 1 step only when counted from the commit) — confirm the counting reference for TC-SAF-FW-003-01 | Validator E |
| OI-FW-40 | Target-only items of M2 (C1): ISR durations, RAM-handler latency during flash ops, HX711 timing, DMA map — all +H, open under D-06 | Orchestrator (HW gate) |

### 9.8 NFR-007 static ISR budget analysis (release image, 2026-10-04)

Method: `arm-none-eabi-objdump -d` of `firmware.elf`, instruction count per function; estimate = 1 cycle per instruction + 3 per call + 14 per VSQRT/VDIV, ×1.5 for code in SRAM (`.RamFunc`, S-bus fetch), 180 MHz; the count is the **whole body** (all branches), so it is an upper bound of a loop-free path. ISR entry/exit +12/+10 cycles. Script: kept in the role scratchpad (`isr_budget.py`), to be confirmed by DWT at HG-18.

| Function / window | Region | Insns | Upper bound | Budget | Note |
|---|---|---|---|---|---|
| `EXTI15_10_IRQHandler` (E-stop) incl. `hal_step_abort` + `halt_hw` + `hal_ena_set` | RAM | 29 + 30 + 22 + 24 | ≈ 0.95 µs (PUL stop after ≈ 0.4 µs) | ≤ 1 µs ISR; PUL ≤ 100 µs | SAF-FW-005; core callback (`on_input_edge`, flash, ≈ 0.5 µs) follows |
| `EXTI0/1` + `limit_line` + `hal_step_stop_now` | RAM | 12 + 27 + 36 (+22) | ≈ 0.8 µs | ≤ 1 µs | SAF-FW-002 limit path ≤ 1 µs + PW |
| `EXTI9_5` (PAUSE) | RAM | 32 | ≈ 0.27 µs | ≤ 1 µs | |
| `TIM2_IRQHandler` + `step_isr` + `ramp_next` | flash | 92 + 45 + 164 (9 VSQRT/VDIV) | ≈ 2.4 µs if every branch ran; typical cruise path ≈ 0.6 µs, accel / decel path ≈ 1.1 µs | ≤ 2 µs | the bound counts the accel, reduction and decel-to-end terms together, which never all bind; measure at HG-18 (R-02 FPU stacking) |
| Step CPU at 50 kHz | – | – | ≤ 1.2 µs × 50 kHz ≈ 6 % | ≤ 15 % | |
| `CRIT_HALT` (PRIMASK) in `hal_step_stop_now` / `abort` / `set_period_now` | RAM / flash | ≤ 36 | ≤ 0.35 µs | ≤ 1 µs (masks 0–2) | |
| `CRIT_MOTION` (BASEPRI 0x20) in `ramp_commit` (struct copy + ≤ 2 register writes), `hw_start` | flash | ≈ 40–60 per section | ≤ 0.5 µs | ≤ 1 µs (masks 2) | ramp math moved outside the section in v0.5 |
| `CRIT_AFE` (HX711 SCK high) | flash | macro body | ≈ 0.6 µs | ≤ 1 µs (masks 2) | |
| `EXTI4_IRQHandler` + `on_afe_sample` (+ DATA frame) | flash | 129 + 102 (+ stream) | read ≈ 45 µs (bit-bang) + ≈ 3 µs | ≤ 60 µs | FW-AFE-001 |
| `TIM5_IRQHandler` → `core_tick_1ms` (`safety_tick` 481 insns, …) | flash | – | ≈ 10–20 µs typical | – (level 4) | |

Result: every budget met by the static estimate except that the step-ISR **upper bound** (all branches summed) exceeds 2 µs; the realistic paths are ≤ 1.2 µs. Confirmation by DWT on target (HG-18, OI-FW-37 for the DWT build).


### 9.5 Later milestones (outline)
- **M2** (sensor & motion): `hx711_shim`, `exti`, `step_tim2`, debounce, drvmon, loadlim, ramp/planner, stepgen_core, motion_sm, homing, the full latches. Feature bits AFE, MOTION, HOMING, BUTTONS, DRV_SIGNALS.
- **M4**: MOVE_UNTIL_LOAD (`FEAT_MOVE_UNTIL_LOAD`).

---

## 10. Requirement → design traceability

Requirement IDs = **SRS v0.3**. Coverage: **SAF-FW 26/26, FW 49/49, NFR-005…008 4/4 = 79/79**, plus the SYS/IF items the FW implements (incl. SYS-011) and D-27/D-28/D-29/D-30. "§" = this document; "ICD" = ICD_protocol section; "pin"/"wir" = `01_HW` docs.

| Req. | Design § | Module(s) | ICD § | MS |
|---|---|---|---|---|
| SAF-FW-001 | 5.3 (discard target/ramp, VALID, EVENT order), 5.14 | latches, valid, motion_sm | 5.3, 6.2 | M1/M2 |
| SAF-FW-002 | 5.3 CLEAN, 5.2, 5.8, 5.9.3, 6 | step_tim2, exti, sample callback, stop_sniff | 2, 5.5 | M2 |
| SAF-FW-003 | 5.6.4 (three paths; halt only at P > 2 ms and d ≤ 1 step, D-29 d / D-30), 5.7.1, 5.9.3, 6 | stepgen_core, safety, stop_sniff | 5.5, 6.5 | M2 |
| SAF-FW-004 | 5.3, 5.6.3 | stepgen_halt.h, step_tim2 | 6.2 (POS_UNCERTAIN) | M2 |
| SAF-FW-005 | 5.2 (HAL fixed reaction in RAM), 5.3, 5.7.2, 5.11, 6; pin §4 | exti, latches, drvmon | 6.2 | M2 |
| SAF-FW-006 | 5.3 ESTOP_CLEAR | latches, debounce | 5.5 | M1/M2 |
| SAF-FW-007 | 5.2 (fixed polarity), 3.2 step 5; wir §5, §7 | exti, debounce | 6.4 | M2 |
| SAF-FW-008 / 009 | 5.8 | loadlim, sample callback | 6.2 | M2 |
| SAF-FW-010 | 5.15 (H2, effective next sample) | param_rules, params_rt | 11.4, 11.5 | M1 |
| SAF-FW-011 | 5.3 FAULT_CLEAR, 5.8 regrow | loadlim, latches | 5.5 | M2 |
| SAF-FW-012 | 5.7.1, 5.4.2, 5.15 (H5, D-33 a) | safety, cmd_check, param_rules | 4.3, 6.2, 11.4 | M1 (check, H5) / M2 |
| SAF-FW-013 / 014 | 5.2, 5.3 (release-only auto-clear, D-33 h; LIMIT_WIRING), 5.7.1 | exti, latches | 4.3, 6.2 | M2 |
| SAF-FW-015 | 5.7.1 | safety | 9.1 | M2 |
| SAF-FW-016 | 5.7.1, 5.4.4 JOG | safety, motion_sm | 5.4 | M2 |
| SAF-FW-017 | 5.7.1 idle disable (not while stale, D-33 g) | safety | 6.2, 7.2 | M2 |
| SAF-FW-018 | 3.2, 3.3, 5.1 reset cause; target test per D-33 e (specimen ≥ 98 N tension, horizontal axis; wir C-15) | board_init, resetcause | 6.4 | M1 |
| SAF-FW-019 | 5.13 | iwdg | – | M1 |
| SAF-FW-020 | 5.4.2/5.4.3 (BLOCK incl. DRV_UNPOWERED / DRIVER_ALARM / PAUSED, speed cap split, absolute only) | cmd_check | 4.3, 4.4, 5.4 | M1 |
| SAF-FW-021 | 5.5 PRECHECK | cmd_check, homing | 5.4 | M1/M2 |
| SAF-FW-022 | 5.2 STOP, 5.3 HALT/HALT_CLEAR | exti, latches | 5.5, 6.2 | M1/M2 |
| SAF-FW-023 | 5.2 PAUSE, 5.3 PAUSED (`pause_src`; blocking + HALT_CLEAR-only clear per D-30), 5.4.2, 5.9.3 | latches, cmd_check, stop_sniff | 4.3 (BLOCK PAUSED), 5.5 | M1/M2 |
| SAF-FW-024 | 5.7.2 (any cause, 20 ms filter, ≤ 23 ms reaction, events), 5.3, 3.2 step 6, 6 | drvmon, latches, cmd_check | 4.3, 6.2 | M2 |
| SAF-FW-025 | 5.7.2 K1 weld (`drv.k1_weld_ms`), 5.3 FAULT_CLEAR cause | drvmon, latches | 5.5, 6.2, 7.6 | M2 |
| SAF-FW-026 | 5.4.2 (DRIVER_ALARM for new starts only), 5.7.2, 5.16 | cmd_check, drvmon | 4.3 | M1 (check) / M2 |
| FW-PLT-001 / 002 | 2.2, 2.3, 3.1; pin §1, §3 | platformio.ini, clock | – | M1 |
| FW-AFE-001…005 | 5.8 | hx711_seq, hx711_shim, hx711_math, afe_rate | 5.3, 7.3 | M2 (004: M1) |
| FW-MOT-001 | 5.6.1/5.6.2 | step_tim2 | – | M2 |
| FW-MOT-002 | 5.6.3, units | units, stepgen_core | 7.3 | M1/M2 |
| FW-MOT-003 | 5.6.3 | ramp, planner | – | M2 |
| FW-MOT-004 / 005 / 006 | 5.4.4 | motion_sm, loadlim | 5.4 | M2/M2/M4 |
| FW-MOT-007 | 5.3, 5.9.3 (idempotent STOP/HALT/PAUSE) | latches, stop_sniff | 5.5 | M1/M2 |
| FW-MOT-008 | 5.4.1, 5.6.5, 5.7.2 (settle after power return) | motion_sm, drvmon | 5.4 | M2 |
| FW-MOT-009 | 5.4.3, 5.4.4 | cmd_check, units | 5.4 | M1/M2 |
| FW-HOM-001 / 002 / 003 | 5.5 (START only; END reached = HOME_WIRING) | homing | 5.4 | M2 |
| FW-HOM-004 | 5.5 drift check, 5.3 HOME_DRIFT row, 5.16 | homing, latches | 5.4, 7.6 | M2 |
| FW-SW-001…003 | 5.2 | exti, debounce | 6.2 | M2 |
| FW-SW-004 | 5.2, 5.7.2, 5.16 | drvmon | 4.3, 8.1 | M2 |
| FW-SW-005 | 5.2 DRV_PWR row, 5.7.2 filter, 5.14 `DS_DRV_PWR`, 5.15 (`drv.pwr_sense_enable` reboot-required); pin §1 PA7 | drvmon | 6.2, 7.6, 8.1 | M2 |
| FW-CFG-001 | 2.1 gen/, 2.2 `--check`, 5.15 | gen, build_info.py | 11.1 | M1 |
| FW-CFG-002 / 003 | 5.15, 5.10 | params_rt, param_rules | 5.2, 11.4 | M1 |
| FW-CFG-004 | 5.10 GET_INFO | cmd, sys_f4 | 7.1 | M1 |
| FW-NVM-001…003 | 5.11 | nvm_log, nvm, flash_f4 | 5.2, 11.2, 11.3 | M1 |
| FW-CMD-001 | 5.4.2, 5.9.2 | cmd_check, frame, cmd | 4 | M1 |
| FW-CMD-002 | 5.14 VALID | valid | 5.3 | M1 |
| FW-CMD-003 | 5.3 FAULT_CLEAR | latches | 5.5 | M1 |
| FW-CMD-004 | 5.10 GET_STATUS (86 B) | status, flags | 7.2 | M1 |
| FW-STR-001…006 | 5.14, 5.9.4 | stream, txsched, evq, payload | 2.2, 5.3, 7.3, 7.4, 8 | M1 |
| FW-TIM-001 | 5.1, 5.8, 6 | time_tim5, hx711_shim | 7.3 | M1 (synthetic) / M2 |
| FW-PAR-001…006 | 5.15 (consumer); dictionary = Integrator | params_gen, params_rt | App. A | M1 |
| NFR-005 | 1 principle 4, 5.1 stack, 7 | check_map.py, sys_f4 | 7.2 | M1 |
| NFR-006 | 4.2, 5.11 (non-blocking NVM), 6 | app, nvm | 7.2 | M1 |
| NFR-007 | 6 ISR budgets; pin §4 | all ISRs | – | M2 |
| NFR-008 | 5.9.2, 5.11, 6 | cmd, nvm | 9.1 | M1 |
| SYS-002 | 1 principles 1–2, 5.14 | – | – | M1 |
| SYS-003 | units (round half away) | units | 0.1 | M1 |
| SYS-006 / 007 / 009 | wir §7, pin §1–5, wir §11 | – | – | – |
| SYS-011 | 5.16 (direct 3.3 V drive bring-up only), OI-FW-13; wir §2, §11 C-06 | – | – | M2 (HW) |
| SYS-008 | 8 seams | hal_*.h | 12 | M1 |
| SYS-010 | 2.1, 2.4 origin notes | – | – | M1 |
| IF-001 / 010 / 012 | 0, 8.4 | proto_gen, gen_test_vectors.py | 3.2, 12 | M1 |
| IF-002…005, 011 | 5.9 | uart2_dma, frame, link, txsched | 1–4, 9, 10 | M1 |
| IF-006 / 007 / 009 | 5.14, 5.4.4 | stream, payload | 7.3, 5.4 | M1 |
| IF-008 | 5.10 GET_INFO | cmd | 0.2, 7.1 | M1 |
| D-27 | 5.16 | – (no FW dependency on p/rev) | – | – |
| D-28 | 5.2, 5.4.2 (DRIVER_ALARM), 5.4.1 (settle 500 ms) | cmd_check, drvmon | 4.3 | M1/M2 |
| D-29 a / b / c / d / e | 5.3 + 5.9.3 / 5.5 / 5.7.2 / 5.6.4 / 5.4.3 | latches, homing, drvmon, stepgen_core, cmd_check | 0.3 | M1/M2 |
| D-30 | 0.3, 5.3 (PAUSED row), 5.4.2 (BLOCK PAUSED), 5.4.4, 5.6.4 (halt condition, OI-ICD-07) | latches, cmd_check, stepgen_core | 4.3, 5.5, 6.5 | M1/M2 |
| D-31 | 0.3, 0.4, 5.3 RESUME + HALT_CLEAR, 5.9.3 (not sniffed), 5.10 | latches, cmd_check | v0.4 | M1 |
| D-32 | 5.4.4 (MOVE_UNTIL_LOAD bound = soft limit), 5.16 (closed-loop recommendation needs no FW change) | – | – | – |
| D-33 a / e / f / g / h | 5.15 H5 / 10 SAF-FW-018 + wir C-15 / 5.7.2 + 5.11 / 5.7.1 / 5.3 + 5.7.1 | param_rules, drvmon, safety, latches | 11.4, 6.2 | M1/M2 |

---

## 11. Open items

### 11.1 Closed since v0.1 (v0.3 additions at the end)

| ID | Item | Resolution |
|---|---|---|
| OI-FW-01 | ENA settle 500 vs 200 ms | `motion.ena_settle_ms` default 500 (params, D-28); SRS text via SD-02 / D-29 f |
| OI-FW-02 | DATA ≤ 2 ms behind long frames | ICD MAX_LEN 160 (≤ 1.82 ms) + one-frame DMA + D-first wire order + ISR-built DATA → ≤ 1.9 ms (§5.9.4, §6) |
| OI-FW-04 | step-rate cap 200 kHz | dictionary max 100 kHz (SD-09, D-29 f) |
| OI-FW-05 | slowest step rate / steps_per_mm min | dictionary min 100 (SD-09, D-29 f) |
| OI-FW-06 | DRV_PWR / ALM / K1 semantics | D-28 + **D-29 c**; ICD BLOCK bits, FAULT K1_WELDED, `drv.k1_weld_ms`; design §5.7.2 |
| OI-FW-07 | slow controlled stop | **D-29 d + D-30**: CLEAN halt only at step period **> 2 ms** and stop distance ≤ 1 step, otherwise planned deceleration (period stretch); design §5.6.4. (The v0.1 "> 1 ms" threshold is withdrawn.) |
| OI-FW-08 | pin changes | accepted (D-28) |
| OI-FW-10 | NVM in sectors 1+2 | decided by A (Q-R3-04); informational |
| OI-FW-11 | ICD content the FW needs | ICD v0.1 + `protocol.yaml` name export; v0.2 deltas §0.3 |
| OI-FW-12 | default speeds | `v_max_travel_um_s` 30 mm/s / `v_max_load_um_s` 20 mm/s (D-29 e) |
| OI-FW-14 | PC13 unusable as an interrupt | informational, kept in pin §1.2 |
| OI-FW-15 | host build environment | MinGW 13.1 + `.venv` PlatformIO 6.2 VERIFIED present; commands §9.4 |
| OI-FW-24 | controlled-stop measurement | adopted by Validator E (OBS-P1-01): commit time of `set_period_now` / c_dec1 preload / CLEAN halt ≤ 2 ms after the trigger |
| OI-FW-26 / OI-FW-27 | SRS SAF-FW-023 / 024 wording | taken over by the Orchestrator as OBS-P1-03 for SRS v0.4 (no FW action) |
| DEF-P1-04 | c1 ≥ 1 ms premise | withdrawn (§5.6.2); sniffed-stop hold (§5.9.3) |
| DEF-P1-07 | response ring overflow | dispatcher back-pressure (§5.9.2, §5.9.4) |
| OBS-P1-10 | ALM chatter at level 1 | ALM polled in the tick with a first-sample/stable-release filter (§5.2) |
| OBS-P1-12 | wiring check list vs SYS-009 | `wiring.md` v0.3 §11: C-15 reworded (D-33 e), C-07/C-16 extended, new C-22/C-23 |
| DEF-P1-01 / 06 (A part) | AFE timeout vs 10 SPS; DRV_PWR during flash ops | design per D-33 a (H5, §5.15) and D-33 f (§5.7.2); params/ICD/SRS text with the Integrator/Orchestrator |

### 11.2 Open (IDs are kept stable; new ones continue at 28)

| ID | Item | Proposal / status | Addressee |
|---|---|---|---|
| OI-FW-03 | NVIC levels deviate from R5 §6.1: E-stop alone at 0, other inputs at 1 (SRS FW-SW-002/003) | accept (pin §4); R5 informed | Orchestrator |
| OI-FW-09 | Target facts to verify: ST-LINK MCO → PH0 bridges, LSI 17–47 kHz, 16 KB erase ≤ 500 ms, UID address, RAM-resident E-stop during erase (C-04), 921 600 Bd over the VCP (C-03) | WP7 (datasheet items) / HW gate | Researcher, Validator E |
| OI-FW-13 | 3.3 V direct drive below the 5–24 V input rating; a dropped edge is undetectable (and in open loop also uncorrected) | buffer before calibration (D-28); C-06 | Orchestrator (HW-gate plan) |
| OI-FW-16 | **Driver open loop** (D-27): silent step loss; FW has no detection except HOME_DRIFT | closed loop + 4000 p/rev recommended (Q25); no FW change needed (§5.16) | PO (Q25) |
| **OI-FW-17** | **Seam v1 single source** (DEF-P1-02): the README seam table must equal §8.1 (Δ items included) | Integrator: README updated in the ICD v0.4 round (in progress); A freezes the headers to it in M1-WP1 | Integrator |
| **OI-FW-18** | TX wire order **DATA > responses > EVENT**, drop order DATA → EVENT, responses never dropped (§5.9.4) | being written into ICD v0.4 §2.4; close on publication | Integrator |
| **OI-FW-19** | Stop sniffer also handles **PAUSE** (8 B fixed frame); ICD §2 mentions STOP/HALT only | add PAUSE to the ICD §2 sentence | Integrator |
| **OI-FW-20** | **Units / motion vectors** (R4 §12 TV-TC `um_to_steps`/`steps_to_um`, TV-M ramp/planner) are not in `gen_vectors.py` yet; SYS-003 is M1. A single generator is required (R3 P12). | add `vectors/units_vectors.json` (M1) and `motion_vectors.json` (M2) | Integrator |
| **OI-FW-21** | `E_INTERNAL` details (1 NOT_IN_BUILD, 2 INVARIANT) and the HardFault record in the BOOT EVENT value | being registered in ICD v0.4; close on publication (names per §0.4) | Integrator |
| **OI-FW-22** | Boot: ENA at the disabled level also when `drv.pwr_sense_enable` and DRV_PWR reads off (§3.2) | being written into ICD v0.4 §6.4; close on publication | Integrator |
| **OI-FW-23** | Homing release bound `HOME_RELEASE_MAX_UM` = 10 mm (RELEASE/BACKOFF not released → HOME_WIRING) and slow-approach bound `backoff_um + 10 mm` → HOME_NOT_FOUND are FW constants, not in the ICD | add to ICD v0.2 §5.4 HOME failures (or make them parameters) | Integrator |
| **OI-FW-25** | NVM migration at the first P2 boot: records written by dict v1 never existed on a board (no FW yet), but twin `flash.bin` files from early M1 runs will migrate (rule 4) | informational; twin tests start from a blank flash | Integrator (twin) |
| **OI-FW-28** | **RESUME details** (OBS-P1-15) as designed in §5.3: BLOCK bits evaluated = HALT, ESTOP (latched or input open), FAULT; no-op when not PAUSED; retry class ONCE_PRIORITY; new `PCLR_RESUME` code; HALT_CLEAR clears PAUSED also when HALT is not latched; refreshes the link watchdog; never sniffed | confirm in ICD v0.4 + `check_vectors.json` | Integrator |
| **OI-FW-29** | **H5** `afe.timeout_ms ≥ 2 × period(afe.rate_sps)` + default 250 ms (D-33 a) | `params.yaml` dict 3 + ICD §11.4 + `rule_h5` vectors | Integrator |
| **OI-FW-30** | SRS v0.4 wording for D-33 e/f/g/h (SAF-FW-018 test, SAF-FW-024/025 bounds incl. the flash-op extension, SAF-FW-017 stale, SAF-FW-013 release-only), NFR-007 "windows masking levels 0–2" (OBS-P1-04), SAF-FW-012 with H5 | SRS v0.4 | Orchestrator |
| **OI-FW-31** | Twin vocabulary (DEF-P1-03): FW-side needs are only the seam call log and edge log the twin keeps itself; no FW debug hook is required | informational; twin extension is the Integrator's | Integrator |
| **OI-FW-32** | BOOT EVENT value/value2 = HardFault record (ICD §6.4): the record exists in `.noinit` but the seam has no accessor | add `bool hal_fault_record(uint32_t *pc, uint32_t *cfsr)` to `hal_sys.h` (seam v1.2) in M2 | Integrator (seam), A |
| ~~OI-FW-33~~ | D-36 / CR-01 | **closed v0.5**: ICD v0.5 / dict 4 retired the names; FW aligned (cmd_check, flags, latches, seam v1.3 `hal_in_cfg_t`) | – |
| **OI-FW-34** | native env links `test/common_impl/fake_hal.c` into every suite | Validator E suites reuse the fakes or get their own env | Validator E |
| R-01 | ST-LINK VCP at 921 600 Bd unproven (A-18; D-36 frees PC7, so a USART6 fallback no longer clashes with STOP) | soak C-03; a fallback UART needs a pin re-plan | Orchestrator |
| R-02 | FPU lazy stacking in the step ISR | measure at C-18; fallback integer ramp (R4 §1.5) | Implementer A |

---

## 12. Change history

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-10-03 | First design for the P1 gate: architecture, build + reuse matrix, boot/safe state, scheduling and NVIC plan, CLEAN/TRUNCATE stop primitive, TIM2 PWM2 + OPM, motion/homing machines, inputs, HX711 reuse, load limit in the ISR, two TX classes, stop sniffer, NVM log in sectors 1+2 with RAM-resident safety ISRs, IWDG, budgets, seams, traceability 74/74, OI-FW-01…16; D-16/D-27/D-28 incorporated. |
| 0.2 | 2026-10-03 | Aligned to ICD v0.1 + v0.2 deltas and `params.yaml` dict 2:<br>• **§0 conformance rules**: names only from `proto_gen.h` / `params_gen.h`, vectors as oracles, Appendix A rename table;<br>• the Integrator's 8 seams adopted with Δ list (§8.1);<br>• STOP 9 B / HALT 8 B / PAUSE 8 B sniffer with the motion-only fast path and in-order protocol semantics (§5.9.3);<br>• **D-29** a PAUSE command + `pause_src`, b START-only homing, c K1_WELDED (`drv.k1_weld_ms`) + DRV_PWR-lost semantics (drvmon), d slow controlled stop paths incl. CLEAN halt > 2 ms, e speed split at command time;<br>• D-27 driver facts (§5.16: no step-loss detection, ALM only report + start-block);<br>• `cmd_check` context mirrors `FwState`; `param_rules` H1–H4 with `check_vectors.json` as oracle; Unity pre-script reading vectors in place with a hash/version gate (§8.4);<br>• DATA built in the sample ISR + one-frame DMA + D-first wire order (FW-STR-002 ≤ 1.9 ms, OI-FW-02 closed);<br>• non-blocking NVM quiesce; HAL fixed input reaction in RAM with deferred core callbacks; RAM vector table answer kept;<br>• STATUS 86 B; reset-cause precedence; core in C11; link watchdog refreshed only by command frames;<br>• **M1 work breakdown** (§9); traceability re-mapped with ICD sections and milestones; OI-FW-01/02/04–08/10–12/14/15 closed, OI-FW-17…27 new.<br>Same day, second pass after **ICD v0.2 (hash 0xB046DD01), SRS v0.3 and D-30**:<br>• PAUSED blocks every new motion start (`BLOCK_PAUSED`, incl. jog refreshes) and is cleared only by HALT_CLEAR;<br>• clean-halt substitution only at P > 2 ms **and** d ≤ 1 step, otherwise period stretch + planned deceleration (v0.1 OI-FW-07 "1 ms" withdrawn);<br>• ALM start-block limited to new starts (SAF-FW-026);<br>• DRV_PWR 20 ms stability filter, reaction ≤ 23 ms for any cause, event rules per ICD §6.2 (SAF-FW-024, FW-SW-005);<br>• K1_WELDED tagged SAF-FW-025, HOME_DRIFT FW-HOM-004;<br>• traceability re-mapped to SRS v0.3 (79/79 + SYS-011);<br>• `proto_gen.h` (ICD v0.2) is the name source;<br>• OI-FW-26/27 added.<br>Third pass after **ICD v0.3**: BLOCK bit 10 PAUSED; PAUSE_CLEARED arg 1 unused; **OI-ICD-07 answered** in §5.6.4 (the running period is reprogrammed immediately, extend-only, with a race guard); OI-FW-26 reduced to the SRS side. |
| 0.5 | 2026-10-04 | **M2 implemented** (D-39): §9.7 work breakdown + as-built deviations, §9.8 NFR-007 static ISR budget analysis; CR-01 in §3.2/§5.2/§5.3/§5.14/§5.15 (STOP/BREAK input and HALT_CLEAR refusal removed); seam v1.2/v1.3 in §8.1 (`hal_fault_record`, `hal_meas_cmd`, `hal_step_set_dir` ±2, `hal_in_cfg_t` without `stop_active_level`); OI-FW-33 closed, OI-FW-35…40 new (§9.7). |
| 0.4 | 2026-10-03 | M1 implemented (WP0–WP7): §9.6 implementation notes and deviations (hold by TYPE/SEQ, seam v1.1 with the critical-section API and the README semantics, synchronous LOAD/DEFAULT, EVENT flush and stale verdict suspended during SAVE, M1 input-derived state and driver power "not confirmed", synthetic AFE through the EXTI4 vector, VALID settle, BOOT record → OI-FW-32); §0.4 names confirmed by ICD v0.4.1; §8.1 seam updated to v1.1; D-36 (no STOP-button input path, PC7 free); OI-FW-32…34 new. |
| 0.3 | 2026-10-03 | Final P1 round: D-31, D-32, D-33 and the Validator E review (`FW_test_plan.md`).<br>• **RESUME 0x3C** (D-31): clears only PAUSED; refused while HALT/ESTOP/fault is latched; no-op if not PAUSED; not sniffed. HALT_CLEAR clears HALT + PAUSED.<br>• **DEF-P1-01 / D-33 a**: H5 `afe.timeout_ms ≥ 2 × period`, default 250 ms; stale timer re-armed on reconfiguration.<br>• **DEF-P1-04**: c1 premise withdrawn (≥ 45 µs possible); **sniffed-stop hold** parks motion starts from earlier frames until the stop frame is dispatched.<br>• **DEF-P1-06 / D-33 f**: DRV_PWR bound op time + 25 ms during flash ops; K1 latency (k1, k1 + 2 ms].<br>• **DEF-P1-07**: dispatcher back-pressure (R reserve 168 B); flood behaviour defined.<br>• **D-33 g**: idle counter restarts while stale. **D-33 h**: limit latch clears on release only.<br>• **OBS-P1-10**: ALM polled with first-sample/stable-release filter, EXTI line 8 off. **OBS-P1-04**: masked-window reading. **OBS-P1-12**: wiring check list (wiring v0.3).<br>• D-32: MOVE_UNTIL_LOAD bound = soft limit allowed, no FW change.<br>• §0.4 provisional ICD v0.4 names; seam single source = `tools/README.md` (DEF-P1-02).<br>• Traceability updated; OI-FW-24/26/27 closed, OI-FW-28…31 new. |

---

## Appendix A. v0.1 private names → ICD / params names

| v0.1 (private) | v0.2 (ICD / params / proto_gen) |
|---|---|
| NACK `E_ESTOP`, `E_NOT_HOMED`, "NACK detail = ALM" | `ST_E_STATE` with `BLOCK_ESTOP`, `BLOCK_NOT_HOMED`, `BLOCK_DRIVER_ALARM` |
| "NACK naming the remaining cause" (FAULT_CLEAR) | `ST_E_CAUSE_ACTIVE`, detail = FAULT mask |
| `input.release_ms`, `input.estop_release_ms` | `io.release_ms`, `io.estop_release_ms` |
| `motion.v_max_um_s` | `motion.v_max_travel_um_s` / `motion.v_max_load_um_s` (H4) |
| `home.switch` / home at either end | retired `home.ref_switch` (0x0401); START only |
| homing phases SEEK_FAST, APPROACH_SLOW, SET_ZERO, MOVE_TO_0 | `HP_FAST_SEEK`, `HP_SLOW_APPROACH` (zero set at its exit), `HP_MOVE_TO_ZERO`, `HP_DONE` |
| latch HOME_ABORTED (FAULT_CLEAR) | EVENT HOME_FAILED `HF_ABORTED`, no latch |
| FAULT_K1 (compile-time option, 100 ms) | FAULT `FAULT_K1_WELDED`, `drv.k1_weld_ms` (200 ms) |
| "samples lost" flag | `DF_OVERRUN` |
| "no AFE data" frame | `DS_NO_AFE_DATA`, `afe_raw = PROTO_AFE_NO_DATA` |
| `pos_uncertain` | `DS_POS_UNCERTAIN` |
| MOVE_DONE reasons LOAD / BOUND | `MD_LOAD_THRESHOLD` / `MD_BOUND` (+ `MD_TARGET`, `MD_SOFT_LIMIT`, `MD_JOG_ZERO`, `MD_STOPPED`) |
| GUI Pause "PC clear" | PAUSE command 0x3B; PAUSED blocks motion (`BLOCK_PAUSED`) and clears **only** by HALT_CLEAR (D-30) |
| STOP(mode) immediate/controlled | `STOPMODE_IMMEDIATE` / `STOPMODE_CONTROLLED` |
| DEFAULTS command | `DEFAULT_PARAMS` |
| TX class H / N | TX classes D / R / E (`HAL_TX_DATA/RESP/EVENT`), wire order D > R > E |
| `loop_max_us` reset on read | not reset (ICD §7.2) |
| ALM on EXTI9_5 (line 8) | ALM polled in the tick (OBS-P1-10) |
| limit latch clear "released and moved away" | released for `io.release_ms` (D-33 h) |
| Resume = HALT_CLEAR | RESUME 0x3C (D-31); HALT_CLEAR still clears PAUSED |
| "every CRC-valid frame refreshes the link watchdog" | only valid **command** frames (ICD §3.1) |
| seams `hal_stepgen.h`, `hal_io.h`, `hal_afe.h`, `sg_*` | `hal_step.h`, `hal_inputs.h` + `hal_outputs.h`, `hal_hx711.h`, `hal_step_*` (§8.1) |
