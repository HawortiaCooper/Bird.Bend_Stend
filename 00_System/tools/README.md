# 00_System/tools — Integrator tools (owner: Implementer C)

Shared FW⇄SW interface tooling for `ICD_protocol.md` v0.4.1, `params.yaml` dict_version 3 and `protocol.yaml`
(protocol name registry, ICD §0.3).
Python ≥ 3.11, standard library + PyYAML (generators only). Use the project venv: `.venv\Scripts\python`.

| File | Purpose |
|---|---|
| `gen_params.py` | **Single generator entry point.** `params.yaml` → `02_FW/src/gen/params_gen.{h,c}`, `03_SW/src/bend_stand/core/params_gen.py`, ICD Appendix A; runs `gen_protocol.py`. Origin: Thrust_Stand_HAW `00_System/tools/gen_params.py` @9473c68 (trimmed). |
| `gen_protocol.py` | `protocol.yaml` → `02_FW/src/gen/proto_gen.h` (C), `03_SW/src/bend_stand/core/protocol_gen.py` (Python `IntEnum`/`IntFlag`, `<ID>_BITS`, `<ID>_DESC`, `CMD_REQ_LEN`, `CMD_RETRY`), ICD tables between `GENERATED protocol:<id>` markers + Appendix B (GF-08). |
| `ref_codec.py` | Reference codec (oracle): CRC-16/CCITT-FALSE, frame encoder, ICD §2.3 parser with resync, encode/decode of every request, response, DATA and EVENT payload. Stdlib only. |
| `ref_cmdcheck.py` | Reference acceptance model of ICD §4–§6 (check order, BLOCK mask, busy, clears, SET_PARAM checks, hard rules). Acceptance only, no execution. |
| `gen_vectors.py` | **The single vector generator** → `vectors/protocol_vectors.json`, `vectors/check_vectors.json`, `vectors/units_vectors.json` (M1); `vectors/motion_vectors.json` (R4 TV-M) is planned for M2 in the same generator. |
| `vectors/` | Generated shared vectors (never hand-edited). |
| `fw_twin/` | **FW host twin** (P2/M1): `build.py` (host gcc build of A's unmodified `02_FW/src/{pure,core,gen}` + twin seams), `engine/` (C: scheduler, seam implementations, world model), `twin.py` (launcher: virtual time, TCP ports, vocabulary v2, logs; Python API `Twin` / `TwinLink`), `contract/` (seam v1 header copies, used only while A's headers are absent), `probe/` (harness probe core — **not the FW**). See "FW host twin — how to run it" below. |
| `tests/` | pytest proving the codec, the model and the generators against the vectors; `test_fw_twin.py` = twin harness self-tests (probe core). |

## Regenerate (Integrator, after every params.yaml / ICD change)

```powershell
.venv\Scripts\python 00_System\tools\gen_params.py           # FW/SW params + protocol names, ICD App. A/B + tables
.venv\Scripts\python 00_System\tools\gen_vectors.py          # vectors (uses the new dictionary)
.venv\Scripts\python -m pytest 00_System\tools\tests -q      # must be green
```

CI / gate check (no writes): `gen_params.py --check` and `gen_vectors.py --check` exit 1 when any output is
stale; `gen_params.py --hash` prints PARAM_DICT_HASH. Rules: every ICD change = ICD version bump + change
history entry; every dictionary change = `dict_version` bump; ids are never reused (`retired_ids`). Validators
**add scenarios to `gen_vectors.py`** (via the Integrator), never a second generator (R3 pitfall P12).

## How both sides consume the vectors (IF-010)

Both implementations MUST pass the same files; the vectors are the oracle for byte values (ICD §0.1, §12).

| Vector set | FW (Implementer A / Validator E, `pio test -e native`, Unity) | SW (Implementer B / Validator F, pytest) |
|---|---|---|
| `protocol_vectors.json` `crc16` | `crc16_ccitt()` equals every `crc` | `io.framing` CRC equals every `crc` |
| `frames` (kind request/response/async) | decode every PC→FW request (payload_hex → C struct, fields = `decoded`); **encode** every FW→PC frame from `decoded` and compare **byte-identical** with `frame_hex` | encode every PC→FW request from `decoded` byte-identical; decode every FW→PC frame to `decoded` (`reencode: false` → re-encoding gives `canonical_payload_hex`; `INVALID_PADDING` → reject) |
| `frames` `framing_only` / `invalid` | frame layer only / must be dropped with `crc_errors += 1` | same |
| `streams` | feed `chunks_hex` in order to a fresh `frame.c` parser; call the 20 ms timeout hook if `idle_timeout_at_end`; frames **and** counters equal | same with `io.framing.FrameDecoder` |
| `units_vectors.json` (ICD §0.1) | `units.c` `um_to_steps` / `steps_to_um` / rate cap with `spm` = the binary32 from `spm_f32_hex`: every value exact | `calc.motion.um_to_steps` / `steps_to_um` and the simulator's step model: every value exact |
| `check_vectors.json` | set up the pure command-check context from `state_defaults` ⊕ `state` (and the param overrides), run the command check on `request.payload_hex`, compare STATUS/detail and, for NACKs, the encoded response with `response_frame_hex`; no side effect on NACK | the simulator (`io.sim.fw_logic`) in the same state answers identically (differential check of the SimBoard) |

FW side: a pre-script in `02_FW` (owned by A/E) converts the JSON into a C header (arrays of hex strings +
expected values) at build time, as Thrust_Stand did (`tools/gen_test_vectors.py`). It MUST read the files in
place (no copies) so `--check` staleness is caught. SW side: tests load the JSON directly via the repo path
`00_System/tools/vectors/`; `ref_codec` may be imported as an oracle in tests only, never by production code.

`check_vectors.json` state fields (`ref_cmdcheck.FwState`): `params` (key → value overrides; enum/bool as
code), `motion_state` (ICD §6.1 name), `enabling_left_ms`, `homed`, `pos_um`, `estop_latched`,
`estop_input_open`, `estop_closed_ms`, `halt_latched`, `stop_btn_active`, `stop_btn_released_ms`, `faults` /
`fault_causes` (FAULT names), `limit_start` / `limit_end` (active or latched), `afe_stale`, `afe_saturated`,
`raw`, `drv_power`, `alm_active`, `nvm_record_valid`, `paused` (v0.2).
`state_schema` (top level, = 2 since ICD v0.4, F-B-25): version of these keys. Keys are only ever **added**
(never renamed or removed), each addition bumps `state_schema`; a replay MUST fail on an unknown key or a
newer `state_schema`. Every vector state is a valid configuration (all hard rules H1–H5 hold).
`expect.paused_after` (present when `state.paused` or for PAUSE / RESUME): the PAUSED latch after the command
(ICD §5.5, D-30/D-31: only RESUME or HALT_CLEAR clear it). It is an execution effect: FW latch unit tests,
the twin and the SW simulator check it; the pure FW command check ignores it.

**Protocol names (GF-08, ICD §0.3).** FW code includes `gen/proto_gen.h` and SW/GUI code imports
`bend_stand.core.protocol_gen`; neither side hand-lists command codes, NACK codes, bit tables or EVENT codes.
`tests/test_protocol_registry.py` proves `protocol.yaml` = `ref_codec.py` tables = generated module.

---

## Lean FW host twin for M1 (outline for Implementer A; R3 §3.6, SYS-008, D-07)

**Goal:** compile the *same* FW sources (`src/pure`, protocol/link, cfg/NVM, app state machines, motion
planner) for the PC against a ≤ 1.5 kLOC twin implementation of a few narrow C seams, serve USART2 as TCP
`127.0.0.1:5760` (SW endpoint `tcp://127.0.0.1:5760`), and drive the "world" through a control port. No
register emulation (the Thrust twin's 7.5 kLOC fake-`stm32f1xx.h` approach is not repeated).

### Seam v1 (single source; DEF-P1-02 / OI-FW-17 closed)
The seam contract is **FW_design §8.1** (Implementer A owns the headers `02_FW/src/hal/hal_*.h` and freezes
them as seam v1 in M1-WP1); this README adopts it **verbatim, including every Δ**. A seam change is made by A
in FW_design §8.1 + the headers and mirrored here in the same change (one contract, two copies kept equal;
the twin is coded against the headers).

```c
/* hal_uart.h */
size_t   hal_uart_read(uint8_t *buf, size_t max);                     /* consume RX bytes */
size_t   hal_uart_peek(uint8_t *buf, size_t max, uint32_t *cursor);   /* stop sniffer: new RX bytes, no consume */
typedef enum { HAL_TX_DATA = 0, HAL_TX_RESP = 1, HAL_TX_EVENT = 2 } hal_tx_class_t;
bool     hal_uart_write(const uint8_t *frame, size_t n, hal_tx_class_t cls); /* whole frame or false;
                                                                    HAL_TX_DATA callable from the sample ISR/tick */
size_t   hal_uart_tx_free(hal_tx_class_t cls);
bool     hal_uart_tx_idle(void);                                      /* all classes empty and line idle */
uint32_t hal_uart_rx_overruns(void);
/* hal_time.h */
uint32_t hal_time_us(void);   uint32_t hal_time_ms(void);
void     core_tick_1ms(void);                                         /* callback, level 4, every 1000 µs */
/* hal_step.h */
typedef struct { uint32_t pw_ticks, dir_setup_ticks; bool pul_invert, ena_invert; } hal_step_cfg_t;
uint32_t hal_step_init(const hal_step_cfg_t *cfg);                    /* returns f_tick (90 MHz target) */
void     hal_step_set_dir(int dir);                                   /* only while stopped */
void     hal_step_start(uint32_t first_period_ticks);                 /* first edge >= dir_setup after the call */
void     hal_step_set_period(uint32_t ticks);                         /* preload: period after the running one */
void     hal_step_set_period_now(uint32_t ticks);                     /* stretch the running period (§5.6.4) */
void     hal_step_arm_last(void);                                     /* OPM: stop at the end of this period */
bool     hal_step_stop_now(void);                                     /* CLEAN, no runt; true = pulse completes */
bool     hal_step_abort(void);                                        /* TRUNCATE; true = pulse cut (uncertain) */
int32_t  hal_step_count(void);                                        /* signed position counter, steps */
void     hal_step_set_count(int32_t steps);                           /* homing zero shift, only while stopped */
bool     hal_step_running(void);
uint32_t hal_step_stop_gen(void);                                     /* start-then-recheck counter */
void     hal_ena_set(bool enabled);                                   /* polarity from hal_step_init */
typedef struct { uint32_t period; bool last; bool stop; } step_next_t;
step_next_t step_isr(void);                                           /* callback, level 2, per completed pulse */
/* hal_inputs.h  — ids = ICD IO bit indices 0..7 */
uint16_t hal_inputs_raw(void);                                        /* electrical levels (1 = pin high) */
typedef struct { uint8_t stop_active_level, pause_active_level, alm_active_level; } hal_in_cfg_t;
void     hal_inputs_config(const hal_in_cfg_t *c);                    /* polarity for the fixed reactions */
void     hal_inputs_rearm(uint8_t id);                                /* re-enable a self-masked line */
void     on_input_edge(uint8_t id, bool level, uint32_t t_us);        /* callback, level 0/1, AFTER the HAL's
                                                                         fixed reaction; deferred during flash ops */
/* hal_outputs.h */
void hal_rate_pin(bool high);  void hal_trip_relay(bool trip /* never true in release 1 */);  void hal_led(bool on);
/* hal_hx711.h */
typedef struct { uint32_t t_us; int32_t raw; int32_t pos_steps; uint8_t status; } afe_sample_t;
void hal_hx711_config(uint8_t gain_pulses, bool rate80);
void hal_hx711_powerdown(bool on);
void hal_hx711_kick(void);                                            /* missed-edge recovery */
void hal_hx711_hold(bool on);                                         /* NVM hold */
void on_afe_sample(const afe_sample_t *s);                            /* callback, level 3, after the latch */
/* hal_flash.h */
bool hal_flash_erase(uint32_t sector);
bool hal_flash_program(uint32_t addr, const void *src, size_t n);
const void *hal_flash_map(uint32_t addr);
/* hal_sys.h */
void hal_wdg_kick(void);  void hal_wdg_set_timeout(uint32_t ms);
uint8_t hal_reset_cause(void);            /* RST_* (proto_gen.h) */
void hal_reset(void);  void hal_uid(uint8_t uid[12]);  bool hal_clk_fallback(void);
uint16_t hal_stack_free_min(void);
/* critical sections (seam v1.1, A's proposal adopted in P2/M1): HALT = PRIMASK, AFE/MOTION = BASEPRI 0x20,
   DATA = 0x30, TICK = 0x40; no-ops in the twin (single thread, ISRs never preempt; nesting checked) */
typedef enum { HAL_CRIT_HALT = 0, HAL_CRIT_AFE = 1, HAL_CRIT_MOTION = 2, HAL_CRIT_DATA = 3, HAL_CRIT_TICK = 4 } hal_crit_level_t;
typedef uint32_t hal_crit_t;
hal_crit_t hal_crit_enter(hal_crit_level_t level);
void       hal_crit_exit(hal_crit_t saved);
/* usage: CRIT_BEGIN(HAL_CRIT_DATA); ... CRIT_END();  (macros in hal_sys.h) */
```

| Seam | Target implementation | Twin implementation (virtual time) |
|---|---|---|
| `hal_uart` | USART2 + DMA1 Stream5/6 Ch4; three TX class queues, wire order DATA > RESP > EVENT (ICD §2.4) | TCP 127.0.0.1:5760 paced at 92 160 B/s; same class queues and order; `peek` = second read cursor; every TX frame logged with first/last-byte virtual time (wire log) |
| `hal_time` | TIM5 1 MHz; SysTick 1 kHz → `core_tick_1ms` | virtual clock (lock-step or real time); `core_tick_1ms` every 1000 µs of virtual time |
| `hal_step` | TIM2 PWM mode 2 + update IRQ (R4 §1.3) | integrates virtual pulses at the commanded periods; `set_period_now` / `arm_last` act on the virtual counter; `abort` cuts the pulse and flags it uncertain; every PUL edge (both), DIR and ENA change logged with its virtual time |
| `hal_inputs` | GPIO + EXTI, fixed reactions in the HAL (FW_design §5.2) | world model per input (switch position, forced level, bounce, broken wire); `rearm` = no-op |
| `hal_outputs` | GPIO | levels recorded for `query` |
| `hal_hx711` | EXTI on DOUT + bit-bang (`hx711_seq.h`, Thrust origin) | sample source from the load model at rate·(1+ε); `hold` = no samples delivered; `kick` = no-op; gain pulses per read recorded |
| `hal_flash` | F4 sector erase / word program | file `flash.bin` (persists across twin resets); power cut injectable after program word n / during erase k; write counter |
| `hal_sys` | IWDG, RCC, SCB | IWDG model (timeout from `hal_wdg_set_timeout`, LSI selectable), reset = engine process restart by `twin.py` with the selected cause (flash kept, frames in flight cut), HSE-fail flag; `hal_crit_*` no-ops (nesting checked) |

Rules for A: the protocol, command check, state machines, NVM codec, ramp planner and safety logic call only
these seams (no direct register access outside `src/hal/`); every seam call that the twin cannot serve
deterministically takes `t_us` as an argument or reads `hal_time_us()`. FW-side debug hooks are not needed:
the twin keeps the seam-call log and the edge log itself (OI-FW-31).

### FW host twin — how to run it (Integrator, `00_System/tools/fw_twin/`, P2/M1)

```powershell
.venv\Scripts\python 00_System\tools\fw_twin\build.py                  # --core auto: fw if 02_FW/src/core/*.c exists, else probe
.venv\Scripts\python 00_System\tools\fw_twin\build.py --core fw        # build\fw_twin.exe  (A's firmware)
.venv\Scripts\python 00_System\tools\fw_twin\build.py --check-seams    # A's hal_*.h == the seam v1 block above?
.venv\Scripts\python 00_System\tools\fw_twin\twin.py --port 5760 --ctl 5761 --clock realtime      # SW: tcp://127.0.0.1:5760
.venv\Scripts\python 00_System\tools\fw_twin\twin.py --clock lockstep --scenario my.simscn.json [--t0-us 0xFFFB6C20]
.venv\Scripts\python -m pytest 00_System\tools\tests\test_fw_twin.py -q                      # harness self-tests (probe)
.venv\Scripts\python -m pytest 03_SW\tests\integration -q                                    # SW <-> twin (A's FW)
$env:BEND_TWIN_CORE="probe"; .venv\Scripts\python -m pytest 03_SW\tests\integration -q       # harness check only
```

- **Build** (`build.py`): host gcc = CLion MinGW 13.1 (CLAUDE.md; else `gcc` on PATH or `$env:TWIN_GCC`), `-std=c11
  -O2 -DFW_TWIN=1 -DFW_VERSION_*` (as `platformio.ini`) `-DPARAMS_GEN_WITH_KEYS=0 -DFW_FEATURE_EXTRA=FEAT_TWIN`
  (A's `core/fw.h` adds it to the INFO feature mask; no `#ifdef` in the core, TC-SYS-008-01). Sources compiled **in place,
  unmodified**: `02_FW/src/pure/*.c`, `src/core/*.c`, `src/gen/*.c` + `fw_twin/engine/*.c`; includes `02_FW/include`,
  `src`, `src/{pure,gen,core}` and `src/hal` (A's seam headers; `fw_twin/contract/` while they are absent).
  Log `build/build_<core>.log` (gcc command, warnings, seam check). `twin.ensure_built()` rebuilds when a source is
  newer than the exe. The core must provide `app_init()`, `app_loop()` and the callbacks `core_tick_1ms`,
  `step_isr`, `on_input_edge`, `on_afe_sample` (FW_design §3.2, §4.2, §8.1).
- **Architecture** (lean, R3 §3.6: no register emulation): `build/fw_twin.exe` = engine + FW, a slave process
  that runs only when `twin.py` advances the virtual clock (stdin/stdout line protocol, `engine/twin_engine.c`
  header). `twin.py` owns virtual time, the TCP data port (one client; a new connection replaces the old one,
  bytes go to the FW at the current virtual time), the JSON-lines control port, the logs and the timed actions.
  One engine process = one MCU power-on period; a reset ends the process and `twin.py` boots a new one after
  2 ms (`boot_delay_ms`) with the reset cause, the same `flash.bin` and the replayed world state.
- **Clocks**: `lockstep` — virtual time moves only on `clock` actions / `Twin.advance_*()` (deterministic: two
  runs give identical wire logs); `realtime` — `twin.py` advances virtual time with the wall clock × `--speed`
  (for B's backend over TCP). Execution rule (FW_design §8.2): ISR callbacks run atomically at their virtual time
  in NVIC order (inputs 0/1, step 2, sample 3, tick 4, TX-done 5), the main loop runs once after every virtual
  instant with an event. A main-loop pass takes no virtual time except flash operations (CPU stall: erase 500 ms,
  16 µs per programmed word); interrupts falling into a stall are taken after it (input fixed reactions happen
  at once, their core callback is deferred), the TX frame on the wire completes, the next one starts after the
  stall. A pass with > 2·10⁶ `hal_time_*` calls is a busy-wait → reported, treated as a hang (IWDG reset).
- **Seam models** (constants = FW_design §2.5): TX classes D 2 frames / 52 B, R 1 024 B, E 16 frames / 384 B;
  wire order D > R > E; 92 160 B/s, byte i of a frame ends at `t0 + ceil((i+1)·10⁹/92160)` ns (RX the same);
  RX ring 2 048 B (overrun counted, reader lapped). Step: f_tick 90 MHz, pulse at the end of each period
  (PWM mode 2), first rising edge ≥ `dir_setup` after `hal_step_start`; at each period end the count moves,
  the next period starts with the preload, then `step_isr()` is called (`.period` → preload, `.last` → the
  period just started is the last, `.stop` → halt now); `stop_now` CLEAN (a pulse in its high phase completes),
  `abort` TRUNCATE (pulse cut, not counted, POS_UNCERTAIN in `query pulses`). ENA electrical level
  = `!enabled ^ ena_invert`. Inputs (electrical, 1 = pin high, pinout §1): E-stop open = 1, limits active = 1,
  PAUSE pressed = 0 (NO), ALM active or driver unpowered = 1, PEND in position = 1, DRV_PWR present = 0; a broken
  wire reads 1 on every input; HAL fixed reactions: E-stop open → `hal_step_abort()` + ENA disabled, limit
  active → `hal_step_stop_now()`; every EXTI-input edge calls `on_input_edge` (lines never masked, `rearm` no-op);
  ALM / PEND / DRV_PWR are only polled. AFE: 80 SPS from reset (RATE pull-up), `hal_rate_pin` /
  `hal_hx711_config` select 10/80 SPS, conversions at `1/(sps·(1+rate_error))`, raw = round-half-away(offset +
  `cell_counts_per_n`·F(x) + N(0, noise)) clamped to the rails, `afe_sample_t.status` = 0 (bit 0 = the
  `sck_overrun` symptom, semantics to be confirmed by A); `hold` = conversions continue, none delivered.
  Flash: sectors 1+2 (0x0800 4000…0x0800 BFFF) in `flash.bin`, program = AND (a 0→1 bit returns false), sector =
  1/2 or an address in it. IWDG armed by the first kick / `set_timeout`; `hal_wdg_set_timeout(ms)`: `ms` = worst-case timeout (slowest LSI 17 kHz), the twin fires after `ms·17000/LSI` (default LSI 32 kHz, default 90 ms → 47.8 ms = the target run window; semantics to be confirmed by A, OI-C-M1-03).
  `hal_uid` = `--uid` (default "TWIN-UID-001"), `hal_stack_free_min` = 3072 (no stack painting).
- **D-36**: the separate STOP/BREAK button input is **not modelled** (electrical level 0 = released); `button
  stop`, `wire stop`, `chatter stop` answer `{"ok": false}` until ICD v0.5 (CR-01) retires the names.
- **Python API** (validators, integration tests): `Twin(clock, core|exe, run_dir, scenario, t0_us, speed)`,
  `advance_us/ms/to`, `act(...)` (= control port), `feed_rx(bytes)`, `read_client()`, `serve(port, ctl)`,
  `fw_t_us()`, logs `wire_log`, `sent`, `edges`, `seam_log`, `conversions`, `input_log`, `resets`;
  `TwinLink(twin)` = in-process PC side on `ref_codec` (oracle, tests only): `send(name, fields, at_us=)`,
  `cmd(name, fields)`, `poll()`, `data()`, `events()`, `find(name, seq)`.
- **Probe core** (`probe/probe_core.c`, `--core probe`): a small ICD-shaped stand-in (link, parameters, a
  power-safe record log, stream, SET_VALID, stop/clear latches, and the probe-only TYPE 0x3F step train) used to
  self-test the harness and the integration tests before A's core exists. **Never M1 evidence** (INFO build string
  `PROBE-NOT-FW`).

**Vocabulary v2 in the twin (M1 subset complete):** all M1 actions are implemented — `estop` (bounce,
`drv_power_follows`, `k1_delay_ms`: the driver power also returns `k1_delay_ms` after the E-stop closes), `drv_power`,
`button` (pause), `limit` (forced / position), `alm`, `pend`, `specimen` (none / spring / bilinear / break;
relaxation not yet: M3), `load_offset`, `afe` (all arguments), `inject` (`tx_congestion`, `link_silence`,
`rx_corrupt`, `hang` main/tick, `step_fault` = the next step ISR is skipped, per-command `drop_next` /
`duplicate_next` / `delay_next` / `corrupt_next` on requests and responses), `rx_bytes` (`at_us` = start of the
first byte, world µs), `on_frame` (after the last byte of the nth matching request), `on_event` (after the last
byte of the nth matching EVENT on the wire), `flash`, `clock`, `reset`, `query` (`world`, `pulses`, `outputs`,
`edges`, `seam_log`, `wire_log`, `sent`, `flash`); also `wire`, `chatter`, `world_shift`, `iwdg`, `clk`.
Not yet: `inject isr_storm` / `where: isr1` (M2). Twin-only `query` extensions: `conversions` (every HX711
conversion: world `t_us`, FW `fw_t_us`, raw, delivered) and `inputs` (electrical input changes). Times in logs
are world µs since the twin start (float, ns resolution); `wire_log` `first_us` = start of the first byte,
`last_us` = end of the last byte.

## Shared simulator / twin world-control vocabulary v2 (F-B-06, DEF-P1-03) — names FROZEN (ICD v0.4)

Both the SW simulator (`io.sim.SimControl.act(action, **args)`, Implementer B) and the FW twin control port
accept the **same action names and arguments**, so differential tests (`03_SW/tests/integration/
test_sim_vs_twin.py`, Integrator) and validator suites drive both identically. Control port: one JSON object
per line over TCP 5761, `{"action": "<name>", ...args}`; reply `{"ok": true, ...result}` or
`{"ok": false, "error": "..."}`. Units: µm, counts, N, ms (µs where named `_us`); times without `at_us` act at
the current virtual time. Names are frozen from v0.4: a change needs a new action/argument name (old ones stay
accepted until both sides migrate) and an ICD version bump.

**Column M1** = required before M1 exit (Validator E / F M1 evidence); the rest before M2 entry. **Side**:
T = twin, S = simulator, both = both (differential tests use only "both" actions).

| Action | Arguments | Effect / result | M1 | Side |
|---|---|---|---|---|
| `estop` | `open: bool`, `drv_power_follows?: bool` (default true), `k1_delay_ms?: int` (default 20), `bounce_ms?: [int]` | E-stop sense input; if `drv_power_follows`, DRV_POWER drops `k1_delay_ms` later (K1 weld = `drv_power_follows: false`) | ✓ | both |
| `drv_power` | `on: bool`, `bounce_ms?: [int]` | DRV_POWER input / driver supply | ✓ | both |
| `button` | `name: "stop"\|"pause"`, `pressed: bool`, `bounce_ms?: [int]` | physical buttons (polarity per `io.*_active_level`) | ✓ | both |
| `limit` | `name: "start"\|"end"`, `active?: bool` (forced), `position_um?: int` (switch location), `bounce_ms?: [int]` | limit switches | ✓ | both |
| `wire` | `input: "estop"\|"start"\|"end"\|"stop"\|"pause"\|"alm"\|"pend"\|"drv_power"`, `broken: bool` | broken wire on any input (NC inputs read active / unpowered) |  | both |
| `chatter` | `input: <as wire>`, `period_ms: float`, `duration_ms: int` | periodic toggling (e.g. ALM 1 kHz chatter) |  | T |
| `alm` / `pend` | `active: bool` | driver outputs | ✓ | both |
| `specimen` | `kind: "none"\|"spring"\|"bilinear"`, `k_n_per_mm`, `x_contact_um`, `k2_n_per_mm?`, `f_yield_n?`, `f_break_n?`, `relax_pct?`, `relax_tau_s?` | load model | ✓ | both |
| `load_offset` | `counts: int` | cell zero offset (default scenario 50 000 counts ≤ 1 % FS, SWD-P1-15) | ✓ | both |
| `afe` | `rate_error?: float`, `noise_counts?: float`, `stall?: bool`, `saturate?: "pos"\|"neg"\|null`, `drop_every?: int`, `miss_next?: int` (single missed DOUT edges), `sck_overrun?: bool` (power-down symptom on the next read), `raw_script?: [int]` (next samples verbatim) | HX711 model | stall / rate / rails ✓ | both (`sck_overrun`: T) |
| `world_shift` | `um: int` | lost steps: shift `x_um_true` against the step counter (open-loop model; HOME_DRIFT tests) |  | both |
| `inject` | `fault: "step_fault"\|"tx_congestion"\|"rx_corrupt"\|"link_silence"\|"hang"\|"isr_storm"\|"drop_next"\|"duplicate_next"\|"delay_next"\|"corrupt_next"`, `duration_ms?`, `where?: "main"\|"tick"\|"isr1"` (hang / storm), `cmd?: <CMD name>`, `what?: "request"\|"response"`, `n?: int`, `ms?: int` | fault injection (per-command link faults = B's LinkModel hooks) | step_fault, tx_congestion, link_silence, hang ✓ | both (`isr_storm`, `where`: T) |
| `rx_bytes` | `hex: str`, `at_us?: int` | inject raw bytes into the FW RX at a virtual time (lock-step: exact) | ✓ | both |
| `on_frame` / `on_event` | `cmd` / `code`, `nth?: int`, `delay_us: int`, `then: {action…}` | run an action `delay_us` after the board received the nth matching request / sent the nth matching EVENT | ✓ | both |
| `flash` | `cut_after_word?: int`, `cut_in_erase?: int`, `reset?: "power"` | power cut during the next NVM program / erase (record integrity tests) | ✓ | T (S: record-level cut) |
| `iwdg` | `lsi_hz?: int` (17 000…47 000) | IWDG model clock |  | T |
| `clk` | `hse_fail: bool` | `hal_clk_fallback()` at the next boot |  | T |
| `clock` | `advance_ms?: int`, `advance_us?: int` | advance virtual time (lock-step only) | ✓ | both |
| `reset` | `cause: "pin"\|"power"\|"iwdg"\|"software"` | board reset, flash kept | ✓ | both |
| `query` | `what: "world"\|"pulses"\|"outputs"\|"edges"\|"seam_log"\|"wire_log"\|"sent"\|"flash"` , `since_us?: int` | `world`: `x_um_true`, inputs, load; `pulses`: PUL count; `outputs`: ENA, RATE, LED, trip relay; `edges`: `[{t_us, pin: "PUL"\|"DIR"\|"ENA", level}]`; `seam_log`: `[{t_us, call, args}]`; `wire_log`: `[{dir, type, seq, first_us, last_us, hex}]`; `sent`: frames produced incl. dropped DATA; `flash`: write counter, records | world/pulses/outputs/edges/wire_log/sent ✓ | both (`edges`, `seam_log`: T; S returns its model equivalent where defined) |

Scenario file (`bird.bend.simscenario` v1, JSON, shared by simulator and twin):

```json
{
  "schema": "bird.bend.simscenario", "version": 1,
  "world": {"stroke_um": 300000, "start_switch_um": -1500, "end_switch_um": 301000,
            "cell_counts_per_n": 3285.0, "load_offset_counts": 50000,
            "specimen": {"kind": "spring", "k_n_per_mm": 50.0, "x_contact_um": 120000},
            "afe": {"rate_sps": 80, "rate_error": 0.005, "noise_counts": 45},
            "driver": {"lag_tau_ms": 5, "drv_power": true}, "estop_open": false},
  "params": {"motion.steps_per_mm": 800.0},
  "schedule": [{"t_ms": 5000, "action": "button", "name": "pause", "pressed": true},
               {"t_ms": 5100, "action": "button", "name": "pause", "pressed": false}]
}
```
`params` = parameter overrides applied before the test (via SET_PARAM by the harness, not by the model).
`schedule` entries are vocabulary actions with `t_ms`. **Default load offset 50 000 counts** (≤ 1 % FS =
64 425 counts, the zero-balance allowance behind the FW threshold range, SRS A-02), so a default session does
not clamp the 110 % FS threshold; the clamping path (SAF-SW-002, D-29g) gets its own scenario
(`clamp.simscn.json`, offset 125 000 counts) (SWD-P1-15, advice to B).
