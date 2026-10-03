# R3 — Thrust_Stand_HAW reuse analysis (FW drivers, protocol, tools, SW, GUI, process)

| Item | Value |
|---|---|
| Author | Researcher · 2026-10-03 · v1.0 |
| Reference | `E:\Bavovna\Drone\Thrust_Stand_HAW` @ git **9473c68** (read-only, D-02). The working tree there has ~53 uncommitted files (an M3 gate round in progress); **everything below cites HEAD 9473c68**, read from a `git archive` snapshot in the Researcher scratchpad. |
| Target | NUCLEO-F446RE, 1 × HX711 (80 SPS, A/128, D-04), 1 stepper via HBS86H STEP/DIR/EN, UART 921600 8N1 + CRC-16 (D-03), PlatformIO + stm32duino (D-09), host twin first (D-07), 2 end limit switches (D-08), E-stop = stop pulses + keep EN (D-10) |
| Citation format | `path:line` relative to the Thrust_Stand_HAW root (`TS:` prefix where ambiguous) |
| Fact tags | **VERIFIED** = read in the code/doc at the cited line (or arithmetic on it) · **ASSUMED** = engineering inference / memory of datasheets, to be confirmed · **UNKNOWN** = no evidence available |

## 0. Key findings (read first)

1. **Thrust_Stand never ran on target hardware** (D-60 there: "no target board"). All its evidence is host unit tests, a FW host twin and a PC simulator. Everything target-specific is unverified there: 921600 Bd over the ST-LINK VCP, flash timing, IWDG, E-stop latency, HX711 bit timing on silicon (`00_System/STATUS.md` "M1 explicit deferrals" table; `02_FW/docs/FW_test_report_M1.md` §6). **UNKNOWN** for us as well. We inherit tested *logic*, not proven *hardware behaviour*.
2. **The protocol core can be reused almost unchanged.** That covers `A5 5A` framing, CRC-16/CCITT-FALSE, the SEQ/ACK/NACK model, the reference parser with resync, PARAM_ENTRY / paged GET_ALL_PARAMS, the NVM record with dictionary hash, SET_VALID_FLAG semantics, link watchdog plus heartbeat, and the vectors + reference-codec oracle. The thrust-specific 80 % gets dropped: ARM masks, DShot, self-tests, temperature map, 124 B DATA. A trimmed ICD skeleton is in §1.6.
3. **HX711 driver: reuse as-is** (`src/pure/hx711_seq.h`, `src/pure/hx711_math.*`), with a small F4 port of the shim `src/sens/hx711.cpp`. Its design is good: EXTI on DOUT falling, bit-bang in the ISR, IRQs masked only while SCK is high, ring to the main loop, and settle/avg/ok/fresh logic in pure C. **Change for the bend stand:** time-stamp each conversion in µs and latch the stepper position in the same ISR. Thrust uses ms stamps and sample-and-hold at frame time.
4. **Pure FW modules are the main asset.** These are reusable as-is or nearly: `crc16`, `frame`, `le.h`, `stream_sched`, `nvm_codec` (with new constants), `hx711_*`, and the `gen/params_gen.*` mechanism. These need rewriting to a smaller scope: `proto.h`, `payload`, `cmd_check`, `safety_sm`, `param_rules`. Total effort ≈ 3–5 days FW for M1-level link + params + NVM + HX711.
5. **The params.yaml + gen_params.py pipeline is worth copying with light edits.** It covers the schema, stable ids, CRC-32 dictionary hash, C/Python/ICD outputs and `--check`. Edits: paths, package name, ID map, drop `fw_owned`/instances if unused. Effort ≈ 0.5 day.
6. **F103 → F446 port under stm32duino (D-09)** is mostly peripheral plumbing:
   - DMA streams/channels instead of DMA1 Ch6/7 (USART2 RX = DMA1 Stream5 Ch4, TX = Stream6 Ch4, **ASSUMED**, RM0390 Table 28);
   - SYSCFG->EXTICR instead of AFIO;
   - MODER/AFR GPIO;
   - **flash sectors (16/64/128 KB) instead of 1 KB pages.** This is the biggest design change for NVM;
   - clocks 180/45/90 MHz;
   - UID address.

   The stm32duino guard flags and `check_map.py` approach carry over (§4).
7. **The GUI patterns the PO asked for are only partly implemented in Thrust_Stand.**
   - **Implemented and reusable:** main window + tabs skeleton, dockable/floatable plot windows with per-channel tree, quantity grouping (D-63), min/max pyramid decimation, E-STOP button in every window/dialog, system-wide Pause/Break hotkey, config form generated from the dictionary with write/verify/file save/load, manual-control widgets, GC deadlock policy.
   - **Placeholders only** (11-line tabs; design text in `03_SW/docs/SW_design.md`): calibration wizard, tare, sequencer + generators + chart with live marker, recording, momentary sample, metadata/report marks.

   We get designs to adapt there, not code. Plan M3/M4 effort accordingly (§6).
8. **Open High defect in the reference: plot performance** (SWD-PM3-05: 2.4–6.1 fps with the pane grid vs ≥ 20 fps required; on-screen E-STOP click 91–235 ms). We should copy the plot dock in simplified form: few panes, explicit Y ranges, `PreciseTimer`, an XY travel-vs-load pane. We should not copy it wholesale (§6.2).
9. **FW host twin (D-07 makes it relevant): reuse the idea, not the implementation.**
   - Thrust's twin is about 7.5 kLOC C++ plus 1.5 kLOC Python. It emulates registers via a fake `stm32f1xx.h`. It found real integration issues, e.g. simulator divergences SIM-D1..D4.
   - For the bend stand, put the FW behind ~6 narrow HAL seams: uart, time, step-gen, gpio-in, flash, hx711-pins. Implement the twin at seam / transaction level and keep it at ≤ 1.5 kLOC (§3.6, §7).
10. **Process lesson.** Thrust's process was effective at catching logic defects (high-severity races found on host: DEF-M3r1-01, DEF-M2-01). It was also very heavy:
    - ICD 1387 lines, 15 versions in 4 days;
    - 767 TC IDs;
    - separate implementer and validator test trees with duplicate vector generators;
    - one generator change broke every FW suite (DEF-M1-05).

    Recommended leaner variant in §7.

---

## 1. Protocol / ICD

### 1.1 Framing (VERIFIED, `00_System/specs/ICD_protocol.md:32-108`)
| Item | Thrust_Stand | Ref |
|---|---|---|
| Physical | USART2 PA2/PA3 → ST-LINK VCP, 921600 8N1, no flow control, binary, **no escaping / byte stuffing** | ICD:33-37 |
| Frame | `A5 5A` · TYPE u8 · SEQ u8 · LEN u16 LE (0..512) · PAYLOAD · CRC u16 LE | ICD:41-51; `02_FW/src/pure/proto.h:16-22` |
| CRC | CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, xorout 0; check "123456789" → 0x29B1; computed over **TYPE..PAYLOAD** (sync excluded); example `ping_req` = `A5 5A 01 07 00 00 E4 77` | ICD:53-67; `02_FW/src/pure/crc16.c:42-48` (table driven) |
| Overhead | 8 B per frame | ICD:51 |

The PO's "data block must start with the separator" (Initial_specs item 10, D-05) is satisfied by the 2-byte sync `A5 5A`. A 2-byte sync with LEN + CRC validation is what makes resync robust without escaping.

### 1.2 Receiver state machine / resync (VERIFIED, ICD:79-102, `02_FW/src/pure/frame.c:89-150`)
- **Hunt** for `A5 5A`; keep a trailing lone `A5`.
- **LEN > max** → `len_errors++`, drop **one** byte, re-hunt.
- **CRC mismatch** → `crc_errors++`, drop **one** byte (the SYNC0), not the whole candidate. This way a truncated frame followed by a real frame does not swallow the real one (ICD:87-90).
- **Inter-byte timeout 20 ms** on an incomplete candidate → `timeout_drops++`, drop one byte, re-scan (ICD:91-93; `frame.c:63-75`, `FRAME_TIMEOUT_MS` `proto.h:22`).
- **Buffer** ≥ 2 maximum frames (`FP_BUF_SIZE 1040`, `frame.h:24`).
- **Pull API:** `fp_append()` / `fp_poll()`. The returned frame points into the parser buffer, with no copy (`frame.h:1-8, 46-61`).
- **Builder:** `frame_build()` (`frame.c:152-166`). Two-part payload send with incremental CRC: `02_FW/src/link/link.cpp:118-147`.
- **Conformance:** the parser must reproduce the `streams` vectors exactly. These cover bad CRC, noise, LEN > max, sync pattern inside the payload, truncated frame + stream, and truncated frame + idle (ICD:100-102).

### 1.3 Command / response model (VERIFIED)
| Topic | Thrust_Stand rule | Ref |
|---|---|---|
| TYPE ranges | PC→FW `0x01..0x3F`, `0x50..0x7F`; response = `TYPE \| 0x80`; async FW→PC `0xC0..0xCF`; `0x40..0x4F` **reserved** because their responses would collide with the async range | ICD:110-118 |
| SEQ | PC chooses per command (incrementing); response echoes it; a retry uses a **new** SEQ; async frames have one counter per TYPE; the DATA SEQ advances per *scheduled* tick even when the frame was skipped, so a SEQ gap = lost frame | ICD:69-77 |
| ACK/NACK | exactly one response per CRC-valid command; payload byte 0 = STATUS; NACK payload exactly 3 B `u8 status, u16 detail`; NACKed command has no effect | ICD:154-160 |
| Check order | TYPE → LEN → arguments (`E_PARAM_ID`, `E_TYPE`, `E_RANGE`) → state (`E_ESTOP`, `E_ARMED`, `E_BUSY`…) → execution (`E_NVM`, `E_INTERNAL`) | ICD:161-165; `02_FW/src/pure/cmd_check.h` (header comment) |
| Status codes | 0 OK … 14 `E_CONFIRM` | ICD:904-921; `proto.h:47-51` |
| Detail semantics | per status (expected LEN, param id, field offset, ms until retry…) | ICD:167-182 |
| Versioning | PROTO major.minor (minor = append-only), PAYLOAD_VERSION = byte 4 of DATA; receivers ignore trailing bytes of longer responses; FW rejects wrong LEN with `E_LENGTH` | ICD:21-30 |
| Unknown enum values | tolerated (logged) | ICD:29-30 |

### 1.4 Commands relevant to the bend stand (VERIFIED, ICD:120-152, §3.3)
- **GET_INFO 0x02** → INFO 46 B (ICD:626-646):
  - proto/payload/fw versions;
  - `param_dict_hash` (CRC-32 of the dictionary);
  - MCU UID (F1 address 0x1FFFF7E8; **F446: 0x1FFF7A10**, ASSUMED RM0390 §25.1);
  - build string, `param_count`, `max_stream_rate_hz`;
  - `feature_mask`, used by the SW to grey out functions.
- **GET_ALL_PARAMS 0x10** (paged, 70 entries/page), **GET_PARAM 0x11**, **SET_PARAM 0x12** (ICD:193-225):
  - Request = PARAM_ENTRY 7 B `u16 id, u8 type, u8[4] value` with zero padding (ICD:793-802).
  - SET returns the value *as stored*.
  - Out of range → `E_RANGE`, never clamped.
  - SET never writes flash.
  - `CFG_DIRTY` has **one** normative definition: RAM ≠ NVM record, or no record, or hash differs (ICD:210-214).
- **SAVE 0x13 / LOAD 0x14 / DEFAULT 0x15** (ICD:227-278):
  - NVM A/B record with sequence number + CRC32 + dictionary hash.
  - Boot rules: no record → defaults; same hash → load; different hash → **migration by id** (same id + type + in range kept, rest defaulted).
  - Record violating a hard rule → defaults.
  - Before the flash stall, wait until queued TX frames are sent (D-64, ICD:233-234).
- **STREAM_START 0x20** `u16 rate_hz` (0 = parameter default) and **STREAM_STOP 0x21** (ICD:280-283). The stream is off after boot.
- **SET_VALID_FLAG 0x22** `u8 flag` → response `u32 t_us` from which the flag applies (ICD:285-295). Every DATA frame with `t_us ≥` that value carries the new flag. The FW **clears VALID automatically** on E-STOP, disarm and link-watchdog trip, with EVENT VALID_CLEARED. This exactly matches PO item 7 and is worth keeping verbatim.
- **ESTOP 0x34 / ESTOP_CLEAR 0x35** (ICD:349-360):
  - ESTOP: idempotent latch.
  - ESTOP_CLEAR is refused while an E-stop input is still active, with a 50 ms release debounce.
  - `E_ESTOP` detail = `estop_src | estop_inputs << 8`.
- **GET_STATUS 0x37**: counters (rx ok/crc/frame errors, tx_overruns, loop_max_us, stack_free_min, nvm_save_ms, reset_cause, link_age_ms) (ICD:753-791).
- **Async frames:**
  - DATA 0xC0;
  - EVENT 0xC1 `u32 t_us, u16 code, u16 arg`, queued ≥ 8 deep, notification only: state must be taken from flags/STATUS (ICD:423-434);
  - LOG 0xC2.

### 1.5 DATA frame and timing (VERIFIED)
- **Layout:** DATA v1 = 124 B (ICD:648-675): `t_us` u32 (µs since boot, wraps 71.6 min) · `payload_version` u8 · `flags` u8 · AFE raw i32 ×2 (`0x80000000` = no data) · … · `sensor_status` u16.
  - The values are **sample-and-hold at frame-assembly time**; `fresh` bits mark new samples (ICD:673-674).
  - Flags bit 0 = VALID; bit 5 OVERRUN = FW skipped ≥ 1 frame (ICD:676-686).
- **Rate scheduling:** fixed rate with a fractional accumulator (`02_FW/src/pure/stream_sched.h:15-31`). No burst after a stall: missed ticks advance SEQ and set OVERRUN (`02_FW/src/link/stream.cpp:70-101`). Before each DATA frame it reserves TX room for one maximum response (`stream.cpp:83`).
- **Bandwidth** (ICD:1020-1030): DATA ≤ 60 % of 92 160 B/s.
- **Link supervision** (ICD:955-1018):
  - **FW link watchdog** (default 500 ms, min 300): any CRC-valid command frame counts; on expiry outputs go to stop.
  - **SW heartbeat:** a PING when nothing was sent for 80 ms (gap ≤ 100 ms).
  - **Timeouts:** response 100 ms (SAVE/LOAD/REBOOT 300 ms); 2 retries with a new SEQ.
  - **DEGRADED** after 3 consecutive timeouts or 300 ms without frames while streaming; **LOST** after 1 s.
  - **ESTOP** is sent immediately, bypassing queues, and repeated every 50 ms until ACK **and** a DATA/STATUS frame shows the ESTOP flag (max 20 tries). If unconfirmed, TX silence forces the FW watchdog. ESTOP frames are never silenced.
  - **Connect sequence:** GET_INFO repeated for 3 s → version / hash check → GET_STATUS → GET_ALL_PARAMS → STREAM_START.

### 1.6 Recommended trimmed ICD skeleton for the bend stand (proposal for the Integrator)
**Keep unchanged:** §1.1 framing and CRC, §1.2 parser (incl. 20 ms timeout and drop-one-byte resync), §1.3 SEQ / ACK / NACK / check order / versioning rules, PARAM_ENTRY, NVM boot rules with hash + migration by id, SET_VALID_FLAG semantics incl. auto-clear, heartbeat/timeouts/ESTOP repetition, vectors + reference codec.

**Simplify:**
- **TYPE ranges:** commands only `0x01..0x3F` → responses `0x81..0xBF` → async `0xC0..0xCF`. This removes the reserved-range wart.
- **Max LEN:** **256**, enough for ~35 params per page; RX buffer 2 × 264 B.
- **Drop:** ARM masks, throttle, DShot, temperature map/`fw_owned`, self-test/test pattern, B1 factory gesture, `link_baud` fallbacks.

```
Frame  : A5 5A | TYPE u8 | SEQ u8 | LEN u16 (0..256) | PAYLOAD | CRC16 (CCITT-FALSE over TYPE..PAYLOAD, LE)
Resp   : TYPE|0x80, same SEQ; payload[0]=STATUS; NACK = 3 B (status, u16 detail)

Commands (PC->FW)                      Response body (OK)
 0x01 PING                             -
 0x02 GET_INFO                         INFO: proto maj/min, payload_ver, fw x.y.z, u32 dict_hash, uid[12], build[16],
                                             u16 param_count, u16 max_stream_hz, u32 feature_mask
 0x03 REBOOT  u32 magic                -
 0x10 GET_ALL_PARAMS u8 page           u8 page, u8 page_count, u8 n, n x PARAM_ENTRY(7)
 0x11 GET_PARAM u16 id                 PARAM_ENTRY
 0x12 SET_PARAM PARAM_ENTRY            PARAM_ENTRY as stored (never writes flash)
 0x13 SAVE_PARAMS / 0x14 LOAD_PARAMS / 0x15 DEFAULT_PARAMS   (motion idle only -> else E_BUSY)
 0x20 STREAM_START u16 rate_hz (0 = default mode, see Q-R3-02)   u16 applied
 0x21 STREAM_STOP                      -
 0x22 SET_VALID_FLAG u8 0/1            u32 t_us (applies to DATA with t_us >= value)
 0x30.. motion block (owned by Integrator after R1/R4):  DRIVER_ENABLE u8 · MOVE_ABS i32 target_um, u32 speed_um_s ·
        JOG i32 speed_um_s (slider / hold) · STOP (decelerated) · HOME · SET_ZERO
 0x34 ESTOP                            - (latched; stop pulses, EN kept per D-10)
 0x35 ESTOP_CLEAR                      - (E_ESTOP while the NC input is still open, 50 ms debounce)
 0x37 GET_STATUS                       counters, motion state, limit inputs, reset cause, loop_max_us, nvm_save_ms

Async (FW->PC)
 0xC0 DATA   payload v1 (below)       0xC1 EVENT u32 t_us, u16 code, u16 arg       0xC2 LOG u8 level, text

DATA payload v1 (PAYLOAD_VERSION = 1)   -- D-05 fields in D-05 order, proposed extras marked (+)
 off size field           meaning
  0   u32  t_us           FW time (us since boot) of the HX711 conversion (DOUT-ready EXTI timestamp)
  4   u8   payload_ver    = 1
  5   u8   flags          b0 VALID (SET_VALID_FLAG) · b1 AFE_OK · b2 DRV_EN · b3 MOVING · b4 ESTOP latched ·
                          b5 LIMIT active (any) · b6 LINK_WDG · b7 OVERRUN (frame(s) skipped before this one)
  6   i32  afe_raw        HX711 24-bit sign-extended; 0x80000000 = no data
 10   i32  setpoint_um    setpoint distance = target position of the active move/jog, um from zero
 14   i32  position_um    (+) commanded position (steps issued x 1000/steps_per_mm) latched at t_us   [Q-R3-01]
 18   u16  status         (+) LIM_START, LIM_END, HOMED, CFG_DIRTY, AFE_SAT, AFE_FRESH, CLK_FALLBACK, ...  [Q-R3-01]
 => 20 B payload, 28 B frame (PO-minimum variant without (+): 14 B / 22 B)
 Load: 80 Hz x 28 B = 2.24 kB/s = 2.4 % of 92 160 B/s; even 1 kHz = 30 %.
```
Rationale for the bend-stand specifics:
- **(a) One DATA frame per HX711 conversion, time-stamped at DOUT-ready, with the position latched in the same ISR.** This gives exact (travel, load) pairs for the travel-vs-load chart. Fixed-rate sample-and-hold like Thrust's would add up to one stream period of jitter between x and y. A fixed-rate mode can remain for "AFE absent" (position-only frames at e.g. 20 Hz).
- **(b) `MOVE_REL` should not exist on the wire.** Thrust's retry rule (ICD:990-991) retries idempotent commands with a new SEQ after a lost *response*. A relative move retried that way would move twice. The SW converts ±0.1/1/10 mm jogs into an absolute target.
- **(c) Link watchdog and E-STOP map to D-10:** stop step pulses and keep EN.

### 1.7 Pitfalls they hit (to design out from the start)
| # | Pitfall | Ref | Bend-stand action |
|---|---|---|---|
| P1 | Response range collided with async range → reserved hole `0x40..0x4F` added late | ICD:115 | Use the TYPE ranges of §1.6 |
| P2 | `CFG_DIRTY` had several definitions; FW forced 1 after DEFAULT | `02_FW/docs/FW_test_report_M1.md:126` (DEF-M1-08), ICD:210-214 | One normative definition in the ICD |
| P3 | ESTOP frames were blocked during SW TX silence | STATUS.md "M1 fix list" (ICD v0.7 #4), ICD:996 | ESTOP never silenced, bypasses queues |
| P4 | Retry of state-setting commands must carry the **newest** value, else an old throttle is re-sent | ICD:991 | Same for JOG speed / VALID; no relative moves |
| P5 | Flash erase stalled the CPU mid-frame → split frame on the wire | STATUS (A-PM3-F1), D-64, `02_FW/src/link/link.cpp:162-173` | `link_tx_quiesce()` before every erase/program |
| P6 | UART RX circular DMA overflow was silent | `FW_test_report_M1.md:129` (DEF-M1-04), `02_FW/src/drv/uart_dma.cpp:109-129` | Keep the overflow counter + resync |
| P7 | PLL lock failure left the MCU at 8 MHz with a dead 921600 link | `FW_test_report_M1.md:128` (DEF-M1-03), `02_FW/src/sys/clock.cpp:52-88` | Bounded PLL start + HSI fallback flag |
| P8 | Timestamp race at timer overflow (capture vs update ISR) | `FW_test_report_M2.md:136` (DEF-M2-01), `02_FW/src/pure/time_ext.h` | Use a 32-bit timer (TIM2/TIM5) for µs time; reuse `time_ext` if extended |
| P9 | ISR→main ring needed explicit memory barriers | `02_FW/src/sens/hx711.cpp:102, 200` (OBS-M2-1) | Keep `__DMB()` placement |
| P10 | A shared EXTI9_5 handler cleared other owners' pending bits | `FW_test_report_M2` OBS-M2-6, `02_FW/src/sys/exti_shared.cpp` | Shared dispatcher per line; limit switches + E-stop + DOUT on distinct EXTI lines |
| P11 | Zero/stop gate applied to outputs but not to the pending state → re-energised after re-ARM (High) | `02_FW/docs/FW_test_report_M3r1.md:133-150` (DEF-M3r1-01) | E-STOP/STOP must also clear the motion target and ramp state, not only gate pulses |
| P12 | Vector-generator `KeyError` broke all FW host suites | `FW_test_report_M1.md:124` (DEF-M1-05) | One vector generator, run in CI with `--check` |
| P13 | Pending bytes at port open: flush vs parse | ICD §7.4 step 1 (v0.9) | Feed them to the parser; stale responses have no pending SEQ |
| P14 | Ambiguous NACK detail in text vs vector | `FW_test_report_M1.md:124` (DEF-M1-06) | The vector file is the oracle; say so in the ICD |

---

## 2. params.yaml + gen_params.py

### 2.1 Schema (VERIFIED, `00_System/specs/params.yaml:10-80`)
- **Top level:** `schema_version`, `dict_version` (bumped on every change), `retired_ids`, `groups`.
- **Group:** `group`, `label`, `c_member`, and either `id_base` (singleton) or `instances` (repeated group → C array, e.g. afe1/afe2 at `params.yaml:196-201`).
- **Param:**
  - `id_off` (**stable id, never reused; type/unit/meaning never change**);
  - `name`, `label`, `type` (u8/i8/u16/i16/u32/i32/f32/bool/enum, wire codes 1..9), `enum` list, `unit`, `min`/`max`/`default`;
  - flags `armed_ok`, `nvm`, `reboot_required`, `fw_owned`;
  - GUI hints `advanced`, `decimals`;
  - normative `description`;
  - `srs` (requirement IDs → traceability).
- **PARAM_DICT_HASH** = CRC-32 (zlib) of a canonical text serialization: one line per param with id|key|type|unit|min|max|default|flags|enum, floats as hex bit patterns. Labels/descriptions are not hashed (`params.yaml:54-61`). The FW compiles the hash in; SW compares it with GET_INFO and goes read-only on mismatch (ICD:1015-1016). NVM records store it for migration.
- **ID map** by high byte = group (`params.yaml:71-76`).
- **Example HX711 params, directly reusable** (`params.yaml:196-309`): `enable`, `gain_channel` (A128/A64/B32 = 25/27/26 pulses), `rate` (RATE pin level), `rate_pin_wired` (reboot), `avg_n` 1..16, `invert`, `power_down`, `settle_discard` 0..20 (default 4).

### 2.2 Generator (VERIFIED, `00_System/tools/gen_params.py`)
- **Inputs/outputs** (`gen_params.py:1-19, 36-44`). It reads `params.yaml` and writes:
  - `02_FW/src/gen/params_gen.h/.c`;
  - `03_SW/src/thrust_stand/core/params_gen.py`;
  - the ICD Appendix A table between markers.
- **CLI:** `--check` (CI staleness test), `--hash`, `--canonical` (`:936-940`). The only dependency is PyYAML.
- **Validation:** name regexes, contiguous enums, defaults in range, retired ids refused (`:197-375`).
- **C output** (`02_FW/src/gen/params_gen.h:22-25, 443-505`):
  - `PID_*` enum;
  - typed storage struct `params_t` (one sub-struct per group);
  - `PARAM_TABLE[]` of `param_meta_t {id,type,flags,offset,size,min_raw,max_raw,def_raw,key}` sorted by id;
  - `param_find()` (binary search), `param_get_raw/set_raw`, `param_validate_set()` (type/padding/range/NaN → `E_TYPE`/`E_RANGE`), `params_set_defaults()`, `params_sanitize()`.
  - No dynamic allocation. Keys are compiled out on target (`-DPARAMS_GEN_WITH_KEYS=0`, `02_FW/platformio.ini:35`).
- **Python output** (`gen_params.py:718-830`): `ParamType` IntEnum, frozen `ParamMeta` dataclass, `PARAMS` mapping and `GROUPS`. The GUI ParamForm is generated from it (§6.9).

### 2.3 Reuse for ~15–25 parameters
- **Effort:** copy `gen_params.py` to `00_System/tools/`. Change the output paths/package (`bend_stand`), the ID map, the banner/strings, `FLAG_ARMED_OK` → e.g. `motion_ok` ("settable while moving"), and keep `fw_owned` unused or remove it. **Effort ≈ 0.5 day incl. a `--check` run; risk Low.**
- **Suggested groups** (illustrative; final list = SRS/Integrator):
  - `sys`: link_timeout_ms, stream mode/rate, log_level, estop/limit input polarity;
  - `afe`: enable, gain_channel, rate, rate_pin_wired, avg_n, invert, settle_discard;
  - `mot`: steps_per_mm (f32, PO item 4), dir_invert, en_active_level, step_pulse_us, max_speed_um_s, accel_um_s2, jog_speed_um_s, home_speed, home_dir/limit, backoff_um, soft_min_um, soft_max_um;
  - `lim`: limit switch NC/NO, debounce_ms.

  That is ≈ 22 params, so GET_ALL_PARAMS fits in one page.
- **Hard rules:** keep the mechanism (`02_FW/src/pure/param_rules.h:20-23`) for 1–2 rules such as `soft_min_um < soft_max_um`, but drop Thrust's 4-pass write procedure (ICD:1100-1118), because so few rules need no ordering. **ASSUMED** sufficient.
- **Pitfall:** `steps_per_mm` changes the meaning of every stored µm position. Either stream position in steps, or define that changing `steps_per_mm` invalidates HOMED (Q-R3-03).

---

## 3. FW reuse matrix

Effort = implementer days incl. host tests (rough). Risk = probability of hidden rework.

### 3.1 Pure (host-testable C11) modules
| Module | Lines | Verdict | Change for the bend stand | Effort / risk |
|---|---|---|---|---|
| `src/pure/crc16.c/.h` | 48+20 | **as-is** | none | 0 / Low |
| `src/pure/le.h` | 50 | **as-is** | none | 0 / Low |
| `src/pure/frame.c/.h` | 166+67 | **as-is** | `FRAME_MAX_LEN` 256, `FP_BUF_SIZE` ≈ 530 | 0.1 / Low |
| `src/pure/proto.h` | 155 | **rewrite (small)** | new TYPE/STATUS/EVENT/flag tables per §1.6 | 0.3 / Low |
| `src/pure/payload.c/.h` | 171+102 | **adapt** | keep packers for INFO/NACK/EVENT/PARAM_ENTRY/LOG; new DATA v1 / STATUS | 0.5 / Low |
| `src/pure/cmd_check.c/.h` | 694+114 | **rewrite using its pattern** | table of expected LEN + ordered checks; ~30 % of size; motion state checks new | 1.0 / Med |
| `src/pure/param_rules.c/.h` | 254+50 | **rewrite (tiny)** | 1–2 hard rules; drop S1..S7, ESC re-init, unimplemented-HW | 0.2 / Low |
| `src/pure/nvm_codec.c/.h` | 226+75 | **adapt** | constants: magic, bank size (see §4.4 flash), entry layout unchanged; consider appending records within a 16 KB sector (log-structured) | 0.5 / Med |
| `src/pure/hx711_math.c/.h` | 149+95 | **as-is** | single channel use; T_ok logic already handles RATE wired/unwired | 0 / Low |
| `src/pure/hx711_seq.h` | 47 | **as-is** | macros supplied by the F4 shim | 0 / Low |
| `src/pure/stream_sched.c/.h` | 75+37 | **as-is** (fixed-rate mode) | only if a fixed-rate mode is kept | 0 / Low |
| `src/pure/safety_sm.c/.h` | 311+117 | **adapt heavily** | keep: E-STOP latch + sources, input debounce/release 50 ms, link watchdog, VALID auto-clear cause, event queue. Drop: arm masks, ESC arming delay, test stop, B1 gesture. Add: limit-switch states, motion-active watchdog semantics (D-10) | 1.0 / Med |
| `src/pure/estop_isr.h` | 97 | **adapt (pattern)** | pure "what does the ISR latch" function: NC E-stop open, START/END limit edges; drop the synthetic self-test trigger | 0.2 / Low |
| `src/pure/time_ext.h` | 42 | **as-is if** a 16-bit timer is extended | not needed with a 32-bit TIM2/TIM5 µs base | 0 / Low |
| `src/gen/params_gen.*` | generated | **regenerate** | from the new params.yaml | — |
| motor_pipe, slew, pwm_calc, dshot*, kiss, vesc_proto, ina228_math, ds18b20_*, rpm_math, selftest_eval, test_item, tmap_persist, tlm_resolve, rate_win, qwin | — | **don't reuse** | thrust-specific (`slew.c` may inspire the step-ramp generator, but stepper trapezoid profiles are R4's domain) | — |

### 3.2 Hardware-bound modules (F103 LL/register code)
| Module | Verdict | What changes for F446 | Effort / risk |
|---|---|---|---|
| `src/sens/hx711.cpp/.h` | **adapt (port)** | Shim on GPIO BSRR/IDR, unchanged on F4. Changes: EXTI mapping via **SYSCFG->EXTICR** (needs the SYSCFGEN bit set in `RCC->APB2ENR`) instead of AFIO; pins from the bend pinout (R1); one channel; `s_spin_cyc = SystemCoreClock/2e6` → 90 cycles = 0.5 µs at 180 MHz; **add µs timestamp + position latch at DOUT-ready** (§3.3); RATE pin per R2. | 0.5 / Med (silicon timing UNKNOWN) |
| `src/drv/uart_dma.cpp/.h` | **adapt (port)** | DMA1 *Stream5/6, Channel 4* (`DMA_SxCR CHSEL=4`, `SxNDTR`, `SxPAR`, `SxM0AR`, flags in `HISR/HIFCR`) instead of Ch6/Ch7 `CCR/CNDTR/CPAR/CMAR` and `ISR/IFCR`; USART2 registers SR/DR/BRR/CR1-3 are the same family on F4 (**ASSUMED**, RM0390 §25). BRR = PCLK1/baud: 45 MHz/921600 → 49 → 918 367 Bd (−0.35 %, VERIFIED arithmetic). Ring logic (half/full counting, overflow detection, chunked TX) unchanged. | 0.5 / Med |
| `src/link/link.cpp/.h` | **as-is** | link_poll (≤ 4 frames/pass, keeps TX room for a max response), valid-frame watchdog timestamp, link_send two-part CRC, `link_tx_quiesce` | 0.1 / Low |
| `src/link/stream.cpp`, `data_stream.h` | **adapt** | DATA assembly per HX711 sample (event-driven) + optional fixed rate; keep SEQ/OVERRUN logic (`stream.cpp:78-100`) and `stream_set_valid` (`:51-60`) | 0.5 / Low |
| `src/link/cmd.cpp` | **rewrite with its structure** | respond/nack helpers (`cmd.cpp:36-48`) and a ctx snapshot + `cmd_check` → handler dispatch are reusable patterns; content is new | 1.0 / Med |
| `src/link/events.cpp/.h` | **as-is** (codes changed) | EVENT queue + LOG post | 0.2 / Low |
| `src/cfg/params.cpp/.h` | **adapt** | `g_params`, `params_set()` with immediate apply, `params_cfg_dirty()`; new apply hooks (AFE config, motion limits) | 0.3 / Low |
| `src/cfg/nvm.cpp/.h` | **adapt** | boot rules, save/load/default, TX quiesce + HX711 hold during the stall (`hx711_hold`), `nvm_save_ms`; drop temperature-map auto-persist | 0.5 / Med |
| `src/drv/flash_f1.cpp/.h` | **rewrite** | F4 flash: sector erase (`FLASH_CR_SER`, `SNB`), `PSIZE` x32 word program, `FLASH_SR` error bits (PGSERR/PGPERR/PGAERR/WRPERR); **sector erase stalls far longer than an F1 page** (16 KB sector: hundreds of ms, **ASSUMED** DS10693 flash table) → only while motion idle + IWDG ≥ 2 s or kick | 0.5 / Med |
| `src/app/tick.cpp/.h` | **don't reuse as-is** | 1 kHz TIM4 control tick is DShot-oriented; the bend stand needs a motion/step-generation timer (R1/R4) + a slow 100 Hz–1 kHz safety tick; reuse the tick-count-gated IWDG kick idea (`app.cpp:184-189`) | — |
| `src/app/safety.cpp/.h` | **adapt (pattern)** | glue: sample inputs, fold ISR latches, apply outputs; replace "zero motor outputs" with "stop step generator, keep EN" (D-10) | 0.5 / Med |
| `src/sys/estop_hw.cpp` | **adapt** | EXTI for NC E-stop + 2 limit switches; ISR stops the step timer directly (no PC involved) | 0.5 / Med |
| `src/sys/time_base.cpp`, `clock.cpp`, `iwdg`, `reset_cause`, `faults`, `stack`, `crit.h`, `dwt_cyc.h`, `exti_shared` | **adapt** | time base on 32-bit TIM5 at 1 MHz; `SystemClock_Config()` override for 180 MHz (HSE bypass 8 MHz from ST-LINK MCO, PLL M=4 N=180 P=2 + over-drive, **ASSUMED**: see R1/Stefan board init), FLASH->ACR 5 WS; DWT/IWDG/SCB identical; reset-cause bits on F4 `RCC->CSR` incl. BORRSTF | 1.0 / Med |
| `include/irq_prio.h` | **adapt** | same 4-bit scheme; proposed: E-stop/limit EXTI 1, step timer 2, safety tick 3, UART DMA 4, SysTick 5, HX711 EXTI 6 | 0.1 / Low |
| `02_FW/platformio.ini` | **adapt** | §4.2 | 0.2 / Low |
| `tools/check_map.py`, `tools/build_info.py` | **adapt** | addresses (NVM sectors), vector-slot check (fix DEF-M3r2f-01: objdump prints 7-digit addresses, `FW_test_report_M3r2.md:479`) | 0.3 / Low |
| mot/*, diag/*, sens/{ina228,vesc,onewire,ds18b20,rpm_probe}, drv/{i2c1,usart1_dma,usart3_rx} | **don't reuse** | thrust-specific | — |

**Total for an M1+M2-equivalent FW base (link, params, NVM, HX711, safety skeleton) ≈ 9–11 days** (ASSUMED), excluding motion control (R1/R4).

### 3.3 HX711 driver in detail (VERIFIED unless tagged)
- **Pins/IRQ** (`02_FW/src/sens/hx711.cpp:23-26, 151-168`): DOUT on an EXTI line, falling edge only (FTSR set, RTSR cleared), NVIC priority `PRIO_SENSOR_EXTI` = 6, the lowest of the system (`include/irq_prio.h:14`). AFE1's line 5 is registered with the shared EXTI9_5 dispatcher (`exti9_5_attach`).
- **Read sequence** (`02_FW/src/pure/hx711_seq.h:26-45`): 25/26/27 SCK pulses (gain A128/B32/A64 *for the next conversion*). Each pulse is `MASK_ON; SCK high; T_HIGH; SCK low; MASK_OFF; T_LOW; sample DOUT` (first 24 bits MSB first). After the last pulse DOUT must be high (`dout_high`, else counted as a read error, `hx711.cpp:93-96`). Returns the 24-bit code.
- **Interrupt protection** (`hx711.cpp:62-68`): `HX_MASK_ON` = `__set_BASEPRI_MAX(BASEPRI_ESTOP)`, which masks priorities ≥ 1 and leaves only priority 0. It is applied **only while SCK is high** (0.5 µs, `s_spin_cyc = SystemCoreClock/2e6`, `:155`). The constraint protected is HX711 T3: SCK high must not exceed 50 µs, and > 60 µs = power-down (Thrust R3 `00_System/research/R3_hardware_components.md:61-67`). While SCK is low, any ISR may preempt (T4 has no maximum). Total ISR ≈ 25 × ~1.1 µs ≈ 30 µs per conversion. The `bogde` library approach (global IRQ disable for the whole read) is explicitly rejected (D-22; Thrust R3:139-185).
  - **For the bend stand:** mask with `BASEPRI` at the *step-timer* priority or below, so a step pulse ISR at 2 is delayed ≤ 0.5 µs (ASSUMED harmless at ≤ 50 kHz step rates). Better still, generate step pulses in timer hardware (OC/PWM + DMA or one-pulse mode) so ISR masking never distorts pulse timing (R1/R4 decision).
- **Spurious-edge guard** (`hx711.cpp:78-85`): EXTI is masked during the read (DOUT toggles with data); if DOUT is not low at ISR entry the edge is ignored.
- **Missed-edge recovery** (`hx711.cpp:204-209`): if DOUT is low (ready) but no read happened for ⅔ of T_ok, the main loop pends the EXTI by software (`SWIER`). This recovers from edges lost during a flash stall or `hx711_hold()`.
- **Hand-off to the main loop** (`hx711.cpp:97-105, 194-203`): an 8-slot SPSC ring per AFE of `{raw, t_ms}` with `__DMB()` between slot write and index publish. `hx711_poll()` (main loop) drains it into `hx_chan_sample()`.
- **Gain / rate handling:**
  - Gain = pulse count, applied to the *next* conversion. A change restarts the settle-discard (+1 sample still converted with the old gain) (`hx711_math.h:72-74`, `params.yaml:213-226`).
  - **Rate is a hardware pin** (RATE: 0 = 10 SPS, 1 = 80 SPS), driven only if `rate_pin_wired`; otherwise informational (`hx711.cpp:132-134`, `params.yaml:228-256`).
  - `T_ok` = 3 conversion periods of the *effective* rate (37 ms at 80 SPS wired, else 300 ms) (`hx711_math.h:77-89`).
  - **Action for R2/HW:** confirm how the bend stand's HX711 board straps RATE. Many breakouts tie RATE to GND = 10 SPS (Thrust R3:110-125). D-04 demands 80 SPS → the RATE pin must be high or wired to a GPIO.
- **Math** (`02_FW/src/pure/hx711_math.h:19-89`):
  - sign extension;
  - saturation codes 0x7FFFFF / 0x800000 (reported with ok = 0);
  - block average `avg_n` rounding half away from zero;
  - invert after averaging;
  - settle discard;
  - `fresh` (take-and-clear);
  - `ok`;
  - measured rate in 0.1 Hz over 1 s windows.
- **Power-down:** SCK held high (> 60 µs) (`hx711.cpp:135-139`). **NVM hold:** `hx711_hold()` masks EXTI during flash writes (`:179-192`).
- **Timestamping and stream (gap for the bend stand).** The ring stores `t_ms = time_ms()` at read time (`hx711.cpp:92, 101`). DATA frames are built at the stream tick by `sensors_snapshot()` → `hx711_raw()` (sample-and-hold) + `take_fresh` (`src/sens/sensors.cpp:110-113`). `t_us` is the frame assembly time (`src/link/stream.cpp:89-90`), so load time is known only to ±1 stream period. **Change:**
  1. In the EXTI ISR, before the bit-bang, capture `t_us = TIM5->CNT` (DOUT-ready instant; the conversion ended ≤ the EXTI latency earlier) and latch `position_steps` / `setpoint` atomically. Read the step counter as a 32-bit register with no tearing, e.g. a timer in encoder/counter mode counting its own STEP output, or an ISR-maintained counter read with the step ISR masked (ASSUMED design; R4).
  2. Push `{raw, t_us, pos, setpoint}` to the ring.
  3. The main loop builds one DATA frame per ring entry.

### 3.4 Host test setup (`pio test -e native`) (VERIFIED)
- **Env `[env:native]`** (`02_FW/platformio.ini:65-75`): Unity, compiles only `src/pure` + `src/gen`, `-std=c11 -DHOST_TEST=1`, and adds `-Werror -Wconversion -Wshadow` for own code via a pre-script.
- **Host gcc:** the CLion-bundled MinGW (`02_FW/tools/host_env.ps1`). VERIFIED present on this PC (`C:\Program Files\JetBrains\CLion 2025.3.2`).
- **Vectors in C:** a pre-script `tools/gen_test_vectors.py` turns `00_System/tools/vectors/protocol_vectors.json` into a C header. The tests then decode every request, re-encode every FW→PC frame byte-identical, and replay parser streams (`gen_test_vectors.py:1-12`).
- **Validator suites:** in a second config `test/val_tools/platformio_val.ini`, with **its own** vector generator, run one suite per pio call by `run_val.ps1`, because PlatformIO reloads the default ini (`run_val.ps1:1-4`).
- **Counts at M3r2:** native 317, validator 171 (STATUS.md M3 table).
- **Recommendation:** keep `env:native` + Unity + vector header. Use one shared vector generator (validators add vectors, not generators) to avoid DEF-M1-05-style double maintenance.

### 3.5 Link-layer timing on the main loop (VERIFIED)
- The main loop is non-blocking, in this order: link → safety → sensors → stream → NVM service → events (`02_FW/src/app/app.cpp:161-200`).
- `loop_max_us` is measured, excluding flash saves.
- The IWDG is kicked **only if the control tick advanced** (`app.cpp:184-189`), which catches a dead ISR as well as a dead loop.

### 3.6 FW host twin (`00_System/tools/fw_twin`) — reuse assessment (D-07)
- **What it is** (`00_System/tools/fw_twin/README.md:1-71`):
  - The real `02_FW/src` is compiled natively (MinGW) against a fake `stm32f1xx.h` whose registers are memory cells or emulated models (TIM1-4, GPIO, AFIO, EXTI, RCC, DMA1, USART3).
  - USART2 is served as TCP `127.0.0.1:5760`, so the PC app connects as if it were the VCP. The SW `io.transport` accepts `tcp://` URLs.
  - The HX711 is modelled at **pin level** (bit-banged SCK, 25/26/27 pulses, power-down).
  - There is a control port for "world" lines (`afe1 raw <code>`, `b1 1`, …), NVM in `flash.bin`, and resets emulated as process restarts.
- **Size:** about 7.5 kLOC C++ shim + 1.5 kLOC Python.
- **Pitfalls documented:**
  - 64-bit DMA address truncation needs `--image-base` below 4 GB;
  - weak symbols mis-resolve on MinGW/PE;
  - host scheduling gaps cause false frame skips, which needed a time-critical thread and a waitable timer;
  - twin-specific flakiness and fidelity tests (README:38-47, 190-197; STATUS OBS-M2-F1/F2).
- **Value shown:**
  - FW⇄SW differential tests ("simulator vs twin", `03_SW/tests/integration/test_sim_vs_twin.py`) found 4 simulator divergences (SIM-D1..D4, STATUS "M2 fix list");
  - end-to-end NVM / migration / watchdog / E-stop timing tests through the production SW transport.
- **Recommendation for the bend stand (lean twin):**
  1. Do **not** emulate registers. Put the hardware behind ~6 narrow C seams: `hal_uart` (rx_read / tx_write / tx_idle), `hal_time` (µs), `hal_flash` (erase / program / read-mapped), `hal_gpio_in` (E-stop, 2 limits), `hal_step` (start/stop / set rate / dir / en / steps issued), `hal_hx711` (sample-ready callback with raw + timestamp).
  2. Compile the *same* `src/pure` + `src/link` + `src/cfg` + `src/app` against a twin implementation of those seams. The twin models the stepper as integrated steps → position, a linear spring load model F = k·(x − x0) + noise → HX711 counts, and limit switches at configured positions.
  3. Same TCP port + control-line idea (`load k <N/mm>`, `limit end 1`, `estop 1`).
  4. Target: ≤ 1.5 kLOC, owned by the Integrator, built by a small `build.py` (copy the MinGW invocation from `fw_twin/build.py`).
  5. Note that the hx711 pin-level ISR code is then *not* exercised on the twin. It is covered by the host Unity tests of `hx711_seq.h` with a simulated pin model (as Thrust does) and later on target.

  Effort ≈ 3–4 days (ASSUMED) vs weeks for a register twin; risk Med (seam discipline must be designed in M1).

---

## 4. Framework: PlatformIO + stm32duino (decided, D-09) — porting guidance F103 → F446

### 4.1 Short pros/cons note (for the record)
- **Pros** (why D-09 is consistent with the reuse):
  - the Thrust FW, its build checks, host-test env and twin build all assume PlatformIO;
  - the code uses CMSIS register access + a few HAL calls (`HAL_RCC_GetPCLK1Freq`, `HAL_InitTick`), all available in the stm32duino core;
  - pinned versions are reproducible (`02_FW/platformio.ini:4-17`).
- **Cons:**
  - Stefan's CubeMX init (R1) cannot be dropped in. It must be transcribed into register/LL code or HAL calls inside `SystemClock_Config()` / board_init.
  - The core's own IRQ handlers must be suppressed with guard flags (below).
  - Fewer CubeMX graphical aids for DMA/timer setup.

### 4.2 platformio.ini
- **Copy** `02_FW/platformio.ini` and change:
  - `board = nucleo_f446re`;
  - remove `board_upload.maximum_size` / adjust for the NVM sectors;
  - new `board_build.ldscript` (F446 memory: 512 KB flash, 128 KB SRAM);
  - `-DHSE_VALUE=8000000U` (ST-LINK MCO, **ASSUMED** default solder-bridge configuration of NUCLEO-64; confirm with R1/Stefan);
  - env names `nucleo_f446re`, `_debug`, `_test`, `native`.
- **Keep** pinned `ststm32@20.0.0` + `framework-arduinoststm32@4.30000.0` + `toolchain-gccarmnoneeabi@1.120301.0`, unless R1 shows Stefan needs newer (ASSUMED: the core supports F446RE).
- **Keep the guard flags** (`02_FW/platformio.ini:29-34`, rationale `02_FW/docs/FW_design.md:90-93, 124-149`):

| Flag | Effect |
|---|---|
| `-DHAL_UART_MODULE_ONLY` | removes HardwareSerial and its `USARTx_IRQHandler` |
| `-DHAL_TIM_MODULE_ONLY` | removes HardwareTimer and all `TIMx_IRQHandler`, tone, analogWrite-PWM |
| `-DHAL_EXTI_MODULE_DISABLED` | removes the core EXTI handlers / attachInterrupt |
| `-DTICK_INT_PRIORITY=5` | SysTick priority |

  Guard names were verified by Thrust in the core source (FW_design:147-149). Re-verify for the F4 variant at WP1.0, because `check_map.py` I-1 asserts our handlers sit in their vector slots.
- **Keep:** `-O2 -fstack-usage`, `-DPARAMS_GEN_WITH_KEYS=0`, the `build_src_flags` include list, `pre:tools/build_info.py`, `post:tools/check_map.py`.

### 4.3 Peripheral porting table
| Topic | F103 (Thrust) | F446 (bend stand) | Tag |
|---|---|---|---|
| Clock | 72 MHz = HSE-bypass 8 MHz × 9; APB1 36 MHz; FLASH 2 WS (`src/sys/clock.cpp:52-88`) | 180 MHz: HSE bypass 8 MHz, PLLM 4 / PLLN 180 / PLLP 2 (+ PWR over-drive), AHB 180, **APB1 45 MHz (timers 90)**, **APB2 90 MHz (timers 180)**, FLASH 5 WS + ART (prefetch, I/D cache); fallback HSI 16 MHz-based PLL. Override `SystemClock_Config()` like Thrust (weak in the core) and keep the bounded-wait + fallback-flag pattern (P7). | ASSUMED (RM0390 §6; Stefan init per R1) |
| USART2 baud | BRR = 36 MHz/921600 → 39 (+0.16 %) | BRR = 45 MHz/921600 → 49 → −0.35 % | VERIFIED arithmetic |
| PC link DMA | DMA1 Ch6 RX circular / Ch7 TX (`uart_dma.cpp:40-62`) | DMA1 **Stream5 Ch4 RX** (circular, HT/TC IRQs) / **Stream6 Ch4 TX**; flags in `DMA1->HISR`, clear via `HIFCR`; disable stream and wait `EN=0` before reprogramming TX | ASSUMED (RM0390 Table 28) |
| GPIO | CRL/CRH | MODER/OTYPER/OSPEEDR/PUPDR/AFR[2]; BSRR/IDR unchanged → `hx711_seq` macros unchanged | VERIFIED (BSRR/IDR used `hx711.cpp:62-64`) |
| EXTI routing | `AFIO->EXTICR[]` (`src/sys/estop_hw.cpp`) | `SYSCFG->EXTICR[]` (enable SYSCFG clock); IMR/FTSR/RTSR/PR/SWIER unchanged; vectors EXTI0..4 own, 9_5 and 15_10 shared → reuse `exti_shared` | ASSUMED (RM0390 §10) |
| µs time base | TIM1 16-bit + overflow count (`src/sys/time_base.h:1-27`) | **TIM5 (or TIM2) 32-bit at 1 MHz** (PSC = 90 − 1); wraps 71.6 min = same `t_us` semantics; 64-bit via overflow count if needed | ASSUMED |
| Flash / NVM | 2 × 1 KB pages at 0x0801F000, erase ≤ 40 ms (`src/drv/flash_f1.cpp`, `nvm_codec.h:18-25`) | Sectors 0–3 = 16 KB, 4 = 64 KB, 5–7 = 128 KB. Recommended: **sectors 1 + 2 (0x08004000, 0x08008000) as A/B**; linker keeps `.isr_vector` in sector 0 and code from sector 3 (0x0800C000), wasting < 16 KB. Append records log-style inside the active sector (record ≈ 32 + 8·25 = 232 B → ~70 saves per erase). Sector erase stalls the CPU (single bank) for a long time (16 KB: typ. a few hundred ms, ASSUMED DS10693) → SAVE only with motion idle, stream frames skipped (OVERRUN) or stream paused, IWDG timeout > max erase time; keep `link_tx_quiesce` + `hx711_hold`. Alternative (simpler linker): last two 128 KB sectors, but erase ≈ 1–2 s (ASSUMED). | ASSUMED (RM0390 §3) |
| UID | 0x1FFFF7E8 | 0x1FFF7A10 | ASSUMED (RM0390) |
| Reset cause | `RCC->CSR` | `RCC->CSR` (+ BORRSTF) | ASSUMED |
| DWT / SCB / IWDG / NVIC 4-bit prio | — | identical (Cortex-M4) | VERIFIED class |
| FPU | none (M3) | M4F: float in ISRs → lazy stacking; keep ISRs integer-only where timing matters | ASSUMED |
| Step generation | n/a | new (R1/R4): an advanced/general timer in PWM or one-pulse mode, ideally hardware-stopped from the E-stop ISR (D-10) | — |

---

## 5. SW architecture (03_SW)

All paths below are under `03_SW/src/thrust_stand/` unless stated otherwise. The design document is `03_SW/docs/SW_design.md` (3275 lines).

### 5.1 Package layout and layering (VERIFIED)
- **Packages that exist** at 9473c68: only `core/`, `io/` and `gui/`.
- **Packages that are only planned:** `calc/`, `ext/` and the core modules `stand`, `safety`, `tare`, `calibration`, `capture`, `sequencer/`, `recorder`, `report` and `session` (`SW_design.md:75-160`). None of them exist yet.
- **Layering is enforced by a test** (`03_SW/tests/unit/test_layering_packaging.py:17-64`).
  - `BACKEND=("core","io","calc","ext")` must not import PySide6, shiboken6, pyqtgraph or PyQt.
  - The test does an AST scan, then a subprocess import with Qt modules blocked.
- **Verdict:** copy this test as-is. It enforces the CLAUDE.md rule "backend must not import Qt".

### 5.2 Reuse matrix (backend)
| Module (lines) | What it does | Verdict / effort |
|---|---|---|
| `io/crc.py` (30) | CRC-16/CCITT-FALSE via `binascii.crc_hqx` + bitwise reference | **as-is** |
| `io/framing.py` (173) | `FrameDecoder`, the §1.2 state machine (hunt, LEN, CRC drop-one, 20 ms timeout) + encoder; verified against `ref_codec.FrameParser` | **as-is** (LEN max constant) |
| `io/transport.py` (393) | `Transport` ABC. `SerialTransport`: 8N1, no flow control, 2 ms read timeout, timeout set only when it changes (SWD-M1-01 fix: ~500 SetCommState/s on the ST-Link VCP), writes under a lock (`:57-129`). `VirtualTransportPair`: in-memory pipes with baud/latency, unplug/replug (`:132-274`). `TcpTransport` for the twin (`:278-369`). `transport_factory("COMx" \| "tcp://h:p")` (`:372-393`) | **as-is** |
| `io/reader.py` (126) | Reader thread (ABOVE_NORMAL priority) reads ≤ 4096 B per 2 ms. It applies the inter-byte timeout only after an empty read (SWD-M2-01 fix: 66/111 frames lost before). Responses go to `CommandChannel`; DATA/EVENT/LOG go to the async queue. `line_ns` is published after dispatch (SWD-M2-07) | **as-is** |
| `io/ports.py` (42) | lists ST-Link VCPs (VID 0x0483) first, `auto_detect()` | **as-is** |
| `io/protocol.py` (643, hand-written) | `Cmd`/`Status`/`EventCode` enums, request-length table, TYPE classes, NACK split, payload builders, INFO/STATUS/DATA struct codecs | **adapt**: new command set + DATA/STATUS structs (1–2 d) |
| `core/link.py` (~710) | `FrameWriter` (TX lock, TX-silence that exempts ESTOP, `:103-170`). `CommandChannel` (`:228-563`): SEQ allocation that skips pending SEQs; futures; timeouts 100/300 ms (`:42-45`); 2 retries only for the `IDEMPOTENT` set (`:61-67`), each with a new SEQ and the newest payload; supersede groups; SAFETY/CONTROL/GENERAL lanes with ≤ 4 outstanding; 200 frames/s token bucket; "latest wins" coalescing. `EstopConfirmer`: 50 ms × 20 until ACK + flag (`:580-666`). `LinkSupervisor`: 5 ms ticker (`:676-700`). Link-state rules (`:703-710`). Heartbeat PING after **40 ms** idle (`:48`, below the ICD's 80 ms, for GIL margin, SWD-M1-03) | **adapt tables** only: IDEMPOTENT / NEVER_RETRIED / lanes for bend commands; MOVE commands idempotent only if absolute (§1.6 b) (1–2 d) |
| `core/device.py` (2501) | Thread-safe `Device` API; every method also exists as `foo_async` (Futures, `:276, 359-369`). Calls: `connect` (GET_INFO for 3 s, version/hash check, GET_STATUS, read params, stream) (`:430-509`), `get_info/status`, `ping`, `reboot`, `read_all_params`, `get_param`, `set_param`, `write_and_verify` (`:1179-1317`), `save/load/default_params`, `stream_start/stop`, `set_valid_flag` (`:1355-1390`), `estop` (`:1858`). Supervisor and reconnect worker on LOST every 1 s (`:701-765, 951-1013`). Liveness fault while armed → E-STOP + TX silence (`:908-940`). Thrust-specific parts: arm/throttle/DShot ≈ 635 lines (`:1395-2030`), temp probes ≈ 115, self-test ≈ 340 | **adapt by splitting**: keep ≈ 900 generic lines; drop motors/temp/selftest; add a `motion` section (enable/move/jog/stop/home/zero) (4–6 d) |
| `core/model.py` (476) | `LinkState` (DISCONNECTED / CONNECTING / CONNECTED / DEGRADED / LOST / TX_SILENCED, `:108-114`), DeviceInfo, BoardStatus, Flags, `VerifyItem` statuses (`:331`) | **adapt** (0.5 d) |
| `core/events.py`, `core/observers.py`, `core/errors.py` | Bounded `EventBus` (10 000); weak `ObserverList` + `ReleasingFuture`, which stop Qt objects being finalised in backend threads; error hierarchy | **as-is** (rename) |
| `core/liveness.py`, `core/clock.py`, `core/timing.py` | Per-thread beats (reader 100 ms, pipeline 200 ms, DATA 300 ms) and excepthooks. `FakeClock`. `timeBeginPeriod(1)`, `setswitchinterval(0.001)`, absolute-deadline `Ticker` | **as-is** |
| `core/linkstats.py` | FW counter deltas mod 2³² | **adapt** counter names |
| `core/params_gen.py` (generated), `core/params.py` (418) | `ParamMeta` pack/unpack/in_range; `ParamStore`; hard/soft rules H1..H6 (`:150-248`); `plan_writes` (`:257`); board-config file `*.tsboard.json` with jsonschema (`:345-410`) | **regenerate** / **adapt** (rules removed or replaced; ≈ 1 d) |
| `core/samples.py`, `core/pipeline.py`, `core/sensors.py`, `core/channels.py` | DATA decoded by a packed numpy dtype in one `np.frombuffer` (`samples.py:15-39`). Pipeline thread batches ≤ 10 ms / 16 frames: decode → `TimeUnwrapper` (u32 `t_us` → int64, FW-reset epochs, host-stamp-based wrap count for long gaps, SWD-M3R1-02) (`pipeline.py:62-155`) → `GapCounter` (FW overrun vs link loss, `:164`) → `SensorClassifier` OK / NO_DATA / SATURATED / STALE → ring buffer + sinks. `ChannelSpec` registry with feature gating | **adapt**: pipeline stages as-is; the dtype/channels/sensors are rewritten for 1 AFE + position/status (1–2 d). **Missing:** raw → N calibration stage and tare stage (`pipeline.py:16-17`: "M4") |
| `core/ringbuffer.py` (227) | preallocated float32 column ring, min/max pyramid, `snapshot()` / `raw_window()` | **as-is** |
| `core/motor_gate.py` (621) | Pure gate functions: `GateItem` / `Severity` (`:37-122`), ARM gate (`:291`), ESTOP-clear gate (`:411`), `SetpointLimiter` (`:518`) | **adapt** pattern for "enable driver / start move / start sequence" gates (1 d) |
| `core/selftest.py`, `core/esc_telemetry.py` | thrust-specific | **don't reuse** |
| `io/simulator.py` (1763), `io/sim_sensors.py` (391), `io/sim_demo.py` | `SimulatedBoard` thread on the board end of a `VirtualTransportPair`. It uses the same decoder/protocol, dispatches `_cmd_<name>` with ICD NACK order (`:727-771`), keeps NVM as JSON, streams with SEQ/OVERRUN, and has a link watchdog and E-STOP. `FaultInjector` (`:109-165`). The HX711 model in `sim_sensors.py` covers rate, avg_n, settle, gain and saturation (`_Afe`, `afe_output` `:270`). `make_sim_pair()` (`:1751-1759`) is used by `--sim` (`03_SW/run_sim.bat`) | **adapt the architecture**: keep the loopback/dispatch/fault-injector/HX711 model; replace ~1400 thrust lines with a stepper + spring/load model (2–3 d). Needed by almost every unit, GUI and validation test |
| `io/win_hotkey.py` (604) | Pause/Break global hotkey (§6.3); `FakeHotkeyBackend` for tests (`:260`) | **as-is** |
| `core/backend.py` (126) | Facade that wires EventBus, Liveness, Device (COM/sim/tcp), pipeline, ring (300 s), hotkey, excepthooks | **adapt** (0.5 d) |
| `__main__.py`, `run.bat`, `run_sim.bat`, `pyproject.toml` | `python -m thrust_stand [--sim] [--port]`; markers `req(*ids)`, unit, integration, gui, perf, soak, hil…; every unit test must carry `@pytest.mark.req` (`03_SW/tests/unit/conftest.py:53-59`) | **as-is** (rename). Runtime dependencies: PySide6-Essentials, pyqtgraph, numpy, pyserial, jsonschema, platformdirs (+ Jinja2/matplotlib only for reports) |

**Calibration, tare, safety-limit engine, recorder and sequencer: not implemented anywhere in `core/`.** They have to be written from the SW_design text:
- calibration: `SW_design.md:1136-1194`;
- tare: `:1196-1229`;
- limits: `:1233-1349`, with `LimitConfig` lo/hi warn/trip, integer-ms debounce, hysteresis, and "sensor invalid while moving". This fits travel/load limits directly;
- sequencer: `:1351-1497`;
- recorder: `:1498-1620`.

**Effort (ASSUMED).** About 3–4 person-weeks to reach a Thrust-M3-equivalent backend for the bend stand. On top of that come the new M3/M4 engines: calibration + tare ≈ 3 d, limits ≈ 2–3 d, recorder ≈ 2 d, sequencer + generators ≈ 4–5 d.

### 5.3 Test rigs worth copying (VERIFIED)
- **`03_SW/tests/unit/core/scripted_board.py`** (handler `frame → [(delay, type, seq, payload)]`) for CommandChannel edge cases.
- **`sim_rig.py`**: Device + SimulatedBoard + FakeHotkeyBackend.
- **`tests/unit/io/test_vectors.py`**: decode / re-encode / streams / CRC / metadata against the shared JSON (`:25-28, 333-409`).
- **Hypothesis** framing fuzz (`tests/unit/io/test_framing.py:63, 79`).
- **`tests/integration/twin_rig.py`**: the production `transport_factory(twin.url)` plus `RawClient` on the `ref_codec` oracle. An optional knob runs extra CPU-burning Python threads to starve the GIL (`03_SW/tests/integration/conftest.py:26-46`).
- **`tests/validation/`**: an independent validator suite that uses only the public API, with expected values from the ICD and vectors.

### 5.4 Backend pitfalls (from `03_SW/docs/SW_test_report_*.md`)
| ID | Issue | Lesson |
|---|---|---|
| SWD-M1-01 (High) | `ser.timeout` was set on every read, so the ST-Link VCP was hammered with SetCommState (`SW_test_report_M1.md:77`) | Configure the port once |
| SWD-M1-03 | Heartbeat gaps exceeded 100 ms under GIL load (`:79`) | PING after 40 ms idle; `setswitchinterval(1 ms)`; heartbeat first in the supervisor tick |
| SWD-M1-04/-11 | Events were published before the E-STOP / TX-silence action (`:80, 87`) | Act first, publish afterwards |
| SWD-M1-06 | The reader thread wrote commands (`:82`) | Only the dispatcher writes |
| SWD-M2-01 (High) | The inter-byte timeout was measured after a delayed `read()`, so good frames were dropped (`SW_test_report_M2.md:161`) | Stamp the time *before* the read; time out only after an empty read |
| SWD-M2-02 | A FW flash persist caused DATA gaps while streaming (`:162`) | No flash writes while streaming / moving |
| SWD-M2-06 | Stale bytes at connect could match the first request (`:166`) | Accepted risk; SEQ start randomisation is an option |
| SWD-M3R1-02 (Med) | An 80 min outage lost one `t_us` wrap (`SW_test_report_M3r1.md:189`) | Host-stamp-based wrap counting (`pipeline.py:62-155`) |
| SWD-M3R1-03 (Med) | ARM was allowed while a liveness fault was active (`:187`) | Gates must include link/liveness state |
| SWD-PM3-07 | GC/Qt process deadlock (§6.12) | GC policy + weak observers |
| M3-12 | SAVE timeout too short at a low baud with a full TX backlog (`core/link.py:91-95`) | Timeout scales with the backlog |

## 6. GUI solutions (examples requested by the PO)

All paths below are under `03_SW/src/thrust_stand/` unless stated.

**Implementation status in Thrust_Stand @9473c68 (VERIFIED):**
- **Real tabs:** Connection/Board (435 lines), Manual (886), Diagnostics (1133).
- **11-line placeholders** (`gui/tabs/placeholder.py:8-27`): Setup, Safety, Metadata, Calibration & Tare (`gui/tabs/calibration_tab.py:7-11`, "M4 (WP4.4)"), Sequences ("M5"), Instruments.
- `core/backend.py:5`: "Later milestones add safety, tare, calibration, recorder, sequencer and instruments here."
- Tab list at `gui/main_window.py:65-66`.

### 6.1 Main window / tab structure — **reuse pattern**
- **Toolbar** (`gui/main_window.py:228-265`): fixed (not movable/floatable). **E-STOP is the first item** so it never lands in the overflow menu (`:239-242`). Then port/baud combos, Connect/Disconnect, Stream + rate, Record / Take sample / TARE (disabled placeholders with "pending milestone" tooltips, `:74-75, 208-215`).
- **Status bar** (`:393-437`): link LED + state + FW + rate + rx/lost/CRC in a clickable area that opens the link-stats dialog; permanent ARMED / ESTOP / flags / hotkey / REC fields; updated at 1 Hz.
- **One 33 ms refresh QTimer** (`:187-190`, `_on_refresh` `:535-576`): one `snapshot()` per window group, ~10 Hz readouts. It is still a CoarseTimer, which fires every ~47 ms on Windows (SWD-PM3-06) → use `Qt.PreciseTimer`.
- **Connect flow:** a port selection immediately builds and starts the backend, so the hotkey works before Connect (`:150-154, 685-697`; SWD-M1-02). Connect → `bridge.call_async("connect")` → `read_all_params` (`:699-729`). Errors go to a non-blocking `SafeMessageBox`.
- **Bend stand adaptation:** tabs Connection/Config · Safety limits · Metadata · Manual · Calibration & Tare · Sequences (+ Diagnostics optional). TARE/Record/Sample stay in the toolbar, so they are reachable from every tab (PO item 11).

### 6.2 Detachable realtime plot windows, per-channel toggles, derived channels, grouping (D-63) — **reuse, simplified**
- **Dock / float / layout:**
  - `PlotDock(QDockWidget)` (`gui/plots/plot_dock.py:104-226`): movable/floatable/closable; a "⧉ Float" button toggles `setFloating`.
  - Each dock carries its **own compact E-STOP** (`:184-185`). Docks are tabified; "New plot window" is supported (`main_window.py:440-462`).
  - The layout persists via QSettings (`main/state`, `main/geometry`, JSON `plots/layout`, `main_window.py:1071-1133`, `plot_dock.py:759-818`).
- **Panes:**
  - `PlotPane` (`gui/plots/plot_pane.py:241-533`) = title strip + own `pg.PlotWidget`, ≤ 2 units per pane (second unit on a right ViewBox `:368-389`).
  - SI auto-prefix off (`:287-288`, SWD-PM3-01). Clip-to-view on, `connect="finite"` so NaN gaps break lines.
  - `PaneGrid` 1–4 columns with drag-and-drop reorder (`gui/plots/pane_grid.py`, D-62). Common X linking by own code (`plot_dock.py:656-670`).
- **Per-channel on/off:** `ChannelTree(QTreeWidget)` (`gui/widgets/channel_tree.py:62-226`) with checkboxes per channel and tri-state group nodes. `batch()` folds a group tick into one signal (`:164-178`). Unavailable channels are greyed (`:108-143`; feature-mask gating `gui/features.py:14-80`).
- **Derived channels:**
  - `ChannelSpec` (`core/channels.py:34-61`) registers derived channels (`thrust_N`, power, …) with `available=False, requires=("calibration","tare")` (`core/channels.py:198-218`). The tree shows them greyed (`gui/backend_adapter.py:263-270`).
  - **The computation itself is not implemented** (`core/pipeline.py:16`).
  - For the bend stand: `load_N = K·(raw − B) − tare`, `travel_mm = position_um/1000`, `stiffness`, etc. These are pure `calc` functions plus a `ChannelSpec` each.
- **Grouping by quantity (D-63):**
  - `quantity_group(spec)` is a pure function (key → tree group → dimension → unit), in `gui/plots/quantity.py:80-95`.
  - Placement: first pane with the same quantity, else the first empty pane, else a new pane (`plot_dock.py:530-562`). Unticking keeps the pane (`:564-589`).
  - Intent `03_SW/docs/SW_design.md:2248-2297`; decision `00_System/specs/DECISIONS.md:69`.
- **Decimation:**
  - `ColumnRingBuffer` (`core/ringbuffer.py:40-227`): preallocated float32 columns, single writer + lock, min/max pyramid ×4/16/64/256.
  - `snapshot(px)` returns exactly `px` min/max pairs from the coarsest adequate level (`:172-217`). The adapter interleaves them to 2 points/pixel (`gui/backend_adapter.py:86-104`).
  - Time windows 5–600 s, default 30 (`plot_dock.py:62-63`).
- **Performance warning (VERIFIED open High):** SWD-PM3-05: 2.4–6.1 fps with the grid vs ≥ 20 fps required; on-screen E-STOP 91–235 ms (`03_SW/docs/SW_test_report_preM3.md:23-27, 164-174, 198`; still open `SW_test_report_M3r2.md:144`). Causes:
  - one `PlotWidget` per pane (axis painting 6–13 ms/pane);
  - double repaint from lazy Y auto-range;
  - GIL contention with backend threads.
- **Adapt for the bend stand:**
  - **(1) XY pane mode:** y = load [N] vs x = travel [mm]. The Thrust pane assumes x = time (`plot_dock.py:642-653`). Use `raw_window` (`core/ringbuffer.py:157-170`) and decimation by index, or plot each new sample incrementally.
  - **(2)** One time pane + one XY pane per window by default; max ~4 panes.
  - **(3)** Explicit Y ranges or a throttled auto-range, `PreciseTimer`, and a single `GraphicsLayoutWidget` if more than 2 panes.
  - **(4)** Keep the channel tree, float button, per-dock E-STOP, layout persistence and D-63 grouping (cheap with ≤ 10 channels).

### 6.3 E-STOP everywhere + Pause/Break global key (D-48) — **reuse almost unchanged**
- **On-screen button:**
  - `EStopButton` (`gui/widgets/estop_button.py:24-58`): red, `NoFocus`, never default. Click → `gui/estop.py:36-47` `trigger_estop(source)` → main-window handler → `device.estop()` **directly in the GUI thread**, not via the async bridge (`main_window.py:143, 782-809`). The banner shows the real send result (SWD-M1-05).
  - It is present in the toolbar, in every PlotDock, in every dialog via `SafeDialog` (E-STOP bar on top, `gui/dialogs/safe_dialog.py:47-78`), and in `SafeMessageBox` / `SafeFileDialog`. Native dialogs are disabled (`AA_DontUseNativeDialogs`, `gui/app.py:50`) so the button stays reachable.
- **Global key** (`io/win_hotkey.py`):
  - **Thread:** a daemon thread at raised priority, no Qt (`:433-482`).
  - **Registration:** Win32 `RegisterHotKey(NULL, id, MOD_NOREPEAT|mods, VK_PAUSE)` for none/Shift/Alt/Win, plus Ctrl+`VK_CANCEL` (Ctrl+Break) (`:67-73, 172-179`), then a `GetMessageW` loop (`:232-243`).
  - **Fallback:** a `WH_KEYBOARD_LL` hook that only posts `WM_APP+1`; `estop()` never runs inside the hook (`:202-225, 245-247, 453-460`; SWD-M1-07).
  - **Callback:** `device.estop("hotkey")` from the hotkey thread (wired `core/backend.py:68-69`). `Device.estop` writes the ESTOP frame directly (bypassing lanes/queues/silence), and a supervisor repeats until confirmed (`core/device.py:1858-1877`, `core/link.py:603-632`).
  - **Measured:** key → `estop()` median 0.38–1.11 ms, p95 ≤ 3.58 ms (`03_SW/docs/SW_test_report_M1.md:41`).
  - **Liveness:** a 250 ms ping/beat of the hotkey thread. ARM is refused if the key is unavailable (`core/motor_gate.py:316-325`). "Test E-STOP key" mode (D-58).
- **Known limit:** the key is not delivered while an elevated (Administrator) window has focus (D-65, `DECISIONS.md:71`). The physical NC E-stop is the path of record.
- **Adapt for the bend stand:**
  - The callback must also **abort the sequencer and any calibration wizard step** (`on_estop` hook exists, `core/device.py:254, 1870`).
  - The FW action is "stop pulses, keep EN" (D-10).
  - PO item 6 says Pause/Break stops "motor/script execution". Keep Thrust's mapping Pause = E-STOP. A softer "pause the sequence" would need a separate key (Q-R3-05).

### 6.4 Tare reachable from any tab — **design only**
- **Status:** the toolbar `act_tare` exists but is disabled ("tare engine in M4") (`main_window.py:74, 210-215, 261`; asserted placeholder `03_SW/tests/gui/test_main_window.py:109`).
- **Design:**
  - **GUI:** a toolbar TARE on every tab → non-modal progress popup with refusal reasons verbatim (`SW_design.md:1852-1853`).
  - **`TareEngine`:** preconditions, a 10 s window (D-41), trimmed mean, a stability check, valid for this session only (D-47) (`SW_design.md:1196-1227`).
- **Bend stand:**
  - **Preconditions:** motion idle, stream on, AFE ok.
  - **Window:** 10 s default (PO).
  - **Result:** offset in raw counts applied to the pull channel.
  - **Implementation:** a pure function in `calc/` + an engine in `core/`.

### 6.5 Calibration wizard — **design only**
- **Status:** no `QWizard` anywhere; only `core/errors.py:139` `CalibrationError` exists.
- **Design:**
  - **GUI:** `CalibrationWizard(QWizard)`: a setup page (mass list); one page per step with Continue → capture with a progress bar and a stability LED, plus Repeat; a result page with the fit plot, residuals, PASS/WARN/FAIL and Accept/Reject (`SW_design.md:2148-2155`).
  - **Engine:** state machine, noise reference, OLS fit, status truth table (`SW_design.md:1136-1194`).
  - **Files:** `cal_<kind>_*.json` + `active.json` = "loaded by default later" (`SW_design.md:1682-1702`).
  - **Acceptance:** R² ≥ 0.9999, nonlinearity ≤ 0.1 % FS pass / ≤ 0.25 % warn (D-42, `DECISIONS.md:44`).
- **Bend stand:**
  - **Two wizards (PO examples):**
    - **(a) Travel:** move 10 mm → operator enters the measured distance → steps_per_mm = steps / measured → SET_PARAM (+ optional SAVE) → move 50 mm more → fine-tune. Use MOVE_ABS targets, not relative retries (§1.6 b).
    - **(b) Load:** zero reference, then known masses 1..n, each averaged over 10 s → linear fit K, B with the D-42-style linearity check.
  - **Structure:** reuse the `QWizard` + engine-state-machine split. The engine is pure (unit-testable with vectors); the wizard only displays it.

### 6.6 Sequencer editor, generators/wizards, looping, chart with live marker — **design only**
- **Status:** placeholder `gui/tabs/sequence_tab.py`; no `core/sequencer`.
- **Design:**
  - **Data model:** Step / Loop / Sequence (`SW_design.md:1353-1388`).
  - **`plan.expand()`:** one expanded plan is the single source for preview, executor and tests (`:1390-1419`).
  - **Executor:** own 100 Hz thread, plan clock with "behind" time, pause/stop/abort (`:1421-1476`).
  - **Generators:** steps / ramp / sweep / custom / CSV, in integer units (`:1478-1494`).
  - **GUI:** `QAbstractTableModel` editor; `SequenceChart` with a `PlotDataItem` per output, green `LinearRegionItem` for report windows (after settling), amber active step, and an `InfiniteLine` live marker driven by the 20 Hz status (`:2157-2190`).
  - **File:** `*.tsseq.json` (`:1668-1680`).
- **Bend stand:**
  - **Step fields:** target (travel mm *or* load N), speed mm/s, accel/ramp, settling s, capture s, loop count. These are the PO's fields (item 12).
  - **Chart:** x = travel, y = load (PO item 15). The expected path comes from the plan (load target via a stiffness estimate, or only travel), and the live marker is the current (x, y) point plus the active-step highlight.
  - **Load-target steps** need closed-loop control on HX711 data at 80 Hz: an R4 topic (Q-R3-06).
  - **Report windows:** use the VALID flag set by the executor via SET_VALID_FLAG (its `t_us` response defines the window edge exactly, ICD:285-290).

### 6.7 Recording, momentary sample — **design only**
- **Status:** Record/Take-sample disabled (`main_window.py:208-215, 361-367, 432-433`).
- **Design:**
  - **Recording directory:** `data.csv` (UTF-8, `#` header block, fixed column order, per-class number formats), `session.json` sidecar (metadata, board config, calibration, limits, sequence), lossless `raw.tsbin` (raw frame dump, ON by default, D-51), `samples.csv`, and `report.csv/.html` (`SW_design.md:1500-1570`; D-44, D-51).
  - **Back-pressure/failure:** `SW_design.md:1602-1617`.
  - **Take sample:** mean/std over a window appended to `samples.csv` (`SW_design.md:2037-2039`).
- **Bend stand:** at 80 Hz × 28 B the raw dump is ~8 MB/h. Keep CSV + JSON sidecar + raw dump, and drop HTML reporting until requested.

### 6.8 Report / metadata marks — **design only**
- **Status:** placeholder `gui/tabs/metadata_tab.py:7-11`.
- **Design:**
  - **Form:** preset combo + form (title required, operator, notes) + **custom key/value fields** + a read-only auto snapshot of config/calibration/tare (`SW_design.md:1978-1992`).
  - **Presets:** `*.tsmeta.json` (`:1704-1715`).
  - **Report windows:** the VALID-flag windows (`SW_design.md:1574-1584`).
- **Bend stand:** PO item 3 (measured element, number, custom) maps 1:1.

### 6.9 Config tab: read / write / verify / save / load file — **implemented, reuse**
- **Form generation:** `ParamForm(PARAMS, GROUPS)` (`gui/widgets/param_form.py:237-374`) builds a `QTreeWidget`: a group node per dictionary group, one row per parameter, and a typed persistent editor generated from the type (BOOL → QCheckBox, ENUM → QComboBox with labels, F32 → QDoubleSpinBox with `decimals`, ints → QSpinBox, U32 → validated QLineEdit; `keyboardTracking` off).
  - Columns: Edit / Board / Default / Unit / Range / Status / Flags (`:46-47`).
  - Filter, "show advanced", "only changed".
- **Write / verify:**
  - Hard rules are checked live (`core/params.py:150-248`); a violation blocks Write (`gui/tabs/connection_tab.py:343-356`).
  - `Device.write_and_verify` (`core/device.py:1179+`): plan → write → GET_ALL_PARAMS read-back → per-item status OK / CLAMPED / REJECTED / MISMATCH / SKIPPED_ARMED / TIMEOUT / NOT_ATTEMPTED (`core/model.py:331`).
  - Results go back into the form (`param_form.py:522-553`).
- **File:** `*.tsboard.json` (`core/params.py:345-405`), jsonschema `core/schemas/board_config.schema.json`.
  - Enums are stored by NAME.
  - On load: hash mismatch and unknown/missing keys are reported, and only the Edit column is filled (`param_form.py:748-777`, `connection_tab.py:400-435`).
- **NVM:** Save/Reload/Defaults buttons with confirmation (`connection_tab.py:374-398`).
- **Bend stand:** copy `param_form.py` + `connection_tab.py` + `core/params.py` nearly verbatim. Changes: package name, the generated `params_gen` import, the `armed` → `moving` flag. This exactly covers SW item 1 of the PO. **Effort ≈ 1–1.5 days; risk Low.**

### 6.10 Manual control → stepper jog — **implemented pattern, adapt**
- **`_ThrottleInput`** (`gui/widgets/throttle_panel.py:71-173`):
  - QSlider + QDoubleSpinBox kept in sync with `QSignalBlocker`, so one user action gives one `edited(value, final)` signal.
  - Step buttons from a tuple (`STEPS_PCT = (-5,-1,-0.1,0.1,1,5)`, `:55, 103-116`).
  - `sliderReleased` marks the final value.
- **Send limiter** (`gui/tabs/manual_tab.py:483-538`): ≤ 20 sends/s, newest value wins, zero/stop never delayed. The fallback timer is a `PreciseTimer` (SWD-M3R1-04).
- **ARM dialog** (`gui/dialogs/arm_dialog.py:82-266`): a `SafeDialog` with gate refusals/warnings/checklist, re-check every 0.5 s; Enter never arms (D-66).
- **Bend stand mapping:**
  - Buttons ±0.1/±1/±10 mm → absolute targets.
  - Slider = target position (or jog speed while held); entry field for a travel distance.
  - "0" → **STOP**, immediate and never rate-limited, next to the E-STOP.
  - "Enable driver" checkbox bound to the real FW state (like the ARM checkbox).
  - Keep the `SafeDialog` confirmation for the first enable.

### 6.11 Readouts, link stats, diagnostics — reuse
- **`ReadoutDock`** (`gui/widgets/readout.py:126-302`): Value/Min/Max/State at 10 Hz, display-only EMA τ = 0.3 s, states n/a / NO DATA / SATURATED / INVALID / STALE.
- **`StatusLed`** (`gui/widgets/status_led.py`).
- **`LinkStatsDialog`** (`gui/dialogs/link_stats.py`): PC counters + GET_STATUS at 1 Hz while visible.
- **Diagnostics tab:** thrust-specific (self-tests), so don't reuse.

### 6.12 GUI ↔ backend separation and GUI tests — reuse
- **`backend_iface.py`:** duck-typed protocols + `NullBackend` (`gui/backend_iface.py:69-252`).
- **`backend_adapter.py`:** one backend per selected port.
- **`QtBridge`** (`gui/bridge.py`):
  - Backend events are re-emitted as queued signals.
  - Link state is polled at 100 ms and stats at 1 Hz by GUI timers.
  - Device calls go through `call_async`, which returns Futures. Completion runs in the GUI thread via `_DoneRelay`, and the worker thread never holds a widget reference (`:53-90, 159-228`).
- **GC deadlock policy** (`gui/gc_policy.py`, **must copy if any backend thread exists**):
  - **Problem:** CPython's cyclic GC finalised a QObject in a backend thread → `~QObject` waits for a Qt mutex held by the GUI thread waiting for the GIL → process deadlock (SWD-PM3-07: 2/3 hangs, `03_SW/docs/SW_test_report_M3gate.md:16-32`).
  - **Fix:**
    - `gc.disable()` at start (`gui/app.py:36-38`);
    - after `show()`, one full collection + `gc.freeze()`;
    - then GUI-thread-only collections on a 100 ms QTimer (gen0 on threshold, gen1 1 s, full 10 s, postponed while armed ≤ 600 s) (`gc_policy.py:98-185`);
    - companion fixes: parent pyqtgraph orphan menus (`plot_pane.py:39-63`), weak event subscriptions (`backend_adapter.py:545-564`).
- **GUI tests:**
  - pytest-qt offscreen (`03_SW/tests/gui/conftest.py`).
  - `fakes.py` implements Device/GuiBackend with Futures and no port.
  - Worth copying: "E-STOP reachable on every tab", "estop calls device directly", "banner reflects not sent", `test_gc_policy.py` + `stress_gc_deadlock.py`.
  - Opt-in perf test `03_SW/tests/perf/test_gui_fps.py`: refresh p95 ≤ 50 ms, E-STOP click ≤ 100 ms, Pause → ESTOP frame p95 ≤ 50 ms.

---

## 7. Process lessons

### 7.1 What worked (keep)
1. **Single sources + generators.**
   - `params.yaml` → C/Python/ICD table with `--check` and a dictionary hash checked at connect, so no FW/SW param drift was reported in the M1–M3 test reports.
   - The protocol vector file produced by a reference codec (`00_System/tools/ref_codec.py`, `vectors/protocol_vectors.json`) is used by both FW Unity tests and SW pytest. The ICD names the vectors as the oracle (ICD:1182-1215).
2. **Pure-C split of the FW.** Logic in `src/pure` with no hardware dependency, plus thin hardware shims. Real races were found on host before any board existed: DEF-M3r1-01 (High), DEF-M2-01 (timestamp overflow race), the HX711 ring barrier.
3. **One normative statement per behaviour in the ICD.** CFG_DIRTY, VALID timing, retry content and E-STOP repetition each have a single statement; the fixes of P2/P3/P4 came from making them single and normative.
4. **Validator ≠ implementer.** Found real defects in every round (FW_test_report_M1..M3r2 DEF tables).
5. **Differential "simulator vs twin" test.** It kept the PC simulator honest (SIM-D1..D4).

### 7.2 What caused rework / overhead (avoid)
1. **ICD churn:** v0.1 → v0.15 in 4 days (ICD:1245-1263). Many rounds came from rare-case semantics (self-test interlocks, DShot command windows, temperature persistence). *Lean:* freeze a **small** ICD 1.0 at P1, with features we actually need; defer extras to minor versions.
2. **Duplicate test infrastructure:**
   - separate implementer/validator vector generators and ini files (`02_FW/test/val_tools/*`);
   - per-suite pio invocations (`run_val.ps1`);
   - DEF-M1-05, where one generator crash disabled all FW suites.

   *Lean:* one vector generator under `00_System/tools`; validators add test files and vectors, not generators.
3. **Register-level twin:** 9 kLOC of tooling with its own fidelity issues (OBS-M2-F1/F2, host-scheduling gaps, MinGW weak-symbol and 64-bit DMA-address tricks). *Lean:* the seam-level twin of §3.6.
4. **Traceability at TC granularity:** 767 TC IDs, 106 requirements (STATUS.md). *Lean:* keep `gen_traceability.py` (81 lines, requirement → design doc → test ID by regex) but trace to test *functions* tagged `Verifies:` instead of hand-numbered TC IDs in plans.
5. **Target evidence deferred indefinitely** (D-60 there; here D-07). *Lean but safe:* list the **target-only checks** from day 1 and run them at the first board gate before any motion under load:
   - 921600 over the ST-LINK VCP with DATA streaming;
   - flash erase stall vs IWDG;
   - HX711 80 SPS timing on silicon;
   - E-stop / limit edge → step stop latency (scope or self-measurement via a timer capture);
   - EN behaviour of the HBS86H.
6. **GUI performance discovered late** (SWD-PM3-05 High after the grid feature). *Lean:* a perf smoke test (frames/s, E-STOP click latency) from the first plot milestone.

### 7.3 Recommended lean process for the bend stand
- P1: SRS ≤ ~50 requirements; ICD ≤ ~400 lines (skeleton §1.6); params ≈ 20–25.
- Generators: `gen_params.py` (copy), `ref_codec.py` (rewrite for the trimmed ICD, ~400 lines), one vector JSON, `gen_traceability.py` (copy).
- FW: `env:native` Unity on `src/pure` with the shared vectors. Lean seam twin (Integrator) for FW⇄SW integration under D-07.
- SW: pytest unit (calc vectors, codec vectors), the simulator (pure Python) for GUI work, integration tests against the twin over `tcp://`.
- Validators: review + independent tests in their own folders, using the same vectors; verdict per milestone.

---

## 8. Open questions

| ID | Question | To |
|---|---|---|
| Q-R3-01 | DATA payload: may we add `position_um` (actual commanded position) and a 16-bit `status` beyond the D-05 field list? Without actual position the travel-vs-load chart must use the setpoint, which is wrong during ramps. | Orchestrator → PO |
| Q-R3-02 | Stream timing: one DATA frame per HX711 conversion (80 Hz, exact x/y pairing; recommended) or a fixed rate with sample-and-hold (Thrust style)? | Orchestrator / Integrator |
| Q-R3-03 | Unit of "setpoint distance" on the wire: µm (needs steps_per_mm in FW; recalibration shifts positions) or steps (raw, converted in SW)? | Integrator (PO informed) |
| Q-R3-04 | NVM placement: sectors 1+2 (16 KB, custom linker split) vs last 128 KB sectors (simple linker, ~1–2 s erase stall)? Confirm erase times from DS10693. | Implementer A / Researcher R2 |
| Q-R3-05 | PO item 6: Pause/Break = full E-STOP (Thrust D-48) or "pause script + stop motor" (resumable)? | Orchestrator → PO |
| Q-R3-06 | Load-target sequence steps need closed-loop force control on 80 SPS data: required in M4, or travel targets only? | Orchestrator → PO / R4 |
| Q-R3-07 | Does the HX711 board of the bend stand have RATE strapped to 80 SPS, or must RATE go to a GPIO (Thrust `rate_pin_wired`)? D-04 needs 80 SPS. | R2 / PO |
| Q-R3-08 | Is 921600 Bd over the NUCLEO ST-LINK/V2-1 VCP proven anywhere (Stefan project?); Thrust never verified it on target. | R1 / first board gate |
| Q-R3-09 | HSE source on the bend stand's NUCLEO-F446RE: ST-LINK MCO bypass (default) or crystal X3 fitted? Affects `SystemClock_Config()`. | R1 (Stefan init) |
