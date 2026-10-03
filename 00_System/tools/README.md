# 00_System/tools — Integrator tools (owner: Implementer C)

Shared FW⇄SW interface tooling for `ICD_protocol.md` v0.1 and `params.yaml` dict_version 1.
Python ≥ 3.11, standard library + PyYAML (generators only). Use the project venv: `.venv\Scripts\python`.

| File | Purpose |
|---|---|
| `gen_params.py` | `params.yaml` → `02_FW/src/gen/params_gen.{h,c}`, `03_SW/src/bend_stand/core/params_gen.py`, ICD Appendix A. Origin: Thrust_Stand_HAW `00_System/tools/gen_params.py` @9473c68 (trimmed). |
| `ref_codec.py` | Reference codec (oracle): CRC-16/CCITT-FALSE, frame encoder, ICD §2.3 parser with resync, encode/decode of every request, response, DATA and EVENT payload. Stdlib only. |
| `ref_cmdcheck.py` | Reference acceptance model of ICD §4–§6 (check order, BLOCK mask, busy, clears, SET_PARAM checks, hard rules). Acceptance only, no execution. |
| `gen_vectors.py` | **The single vector generator** → `vectors/protocol_vectors.json`, `vectors/check_vectors.json`. |
| `vectors/` | Generated shared vectors (never hand-edited). |
| `tests/` | pytest proving the codec, the model and the generators against the vectors. |

## Regenerate (Integrator, after every params.yaml / ICD change)

```powershell
.venv\Scripts\python 00_System\tools\gen_params.py           # FW header/source, SW module, ICD Appendix A
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
| `check_vectors.json` | set up the pure command-check context from `state_defaults` ⊕ `state` (and the param overrides), run the command check on `request.payload_hex`, compare STATUS/detail and, for NACKs, the encoded response with `response_frame_hex`; no side effect on NACK | the simulator (`io.sim.fw_logic`) in the same state answers identically (differential check of the SimBoard) |

FW side: a pre-script in `02_FW` (owned by A/E) converts the JSON into a C header (arrays of hex strings +
expected values) at build time, as Thrust_Stand did (`tools/gen_test_vectors.py`). It MUST read the files in
place (no copies) so `--check` staleness is caught. SW side: tests load the JSON directly via the repo path
`00_System/tools/vectors/`; `ref_codec` may be imported as an oracle in tests only, never by production code.

`check_vectors.json` state fields (`ref_cmdcheck.FwState`): `params` (key → value overrides; enum/bool as
code), `motion_state` (ICD §6.1 name), `enabling_left_ms`, `homed`, `pos_um`, `estop_latched`,
`estop_input_open`, `estop_closed_ms`, `halt_latched`, `stop_btn_active`, `stop_btn_released_ms`, `faults` /
`fault_causes` (FAULT names), `limit_start` / `limit_end` (active or latched), `afe_stale`, `afe_saturated`,
`raw`, `drv_power`, `alm_active`, `nvm_record_valid`.

---

## Lean FW host twin for M1 (outline for Implementer A; R3 §3.6, SYS-008, D-07)

**Goal:** compile the *same* FW sources (`src/pure`, protocol/link, cfg/NVM, app state machines, motion
planner) for the PC against a ≤ 1.5 kLOC twin implementation of a few narrow C seams, serve USART2 as TCP
`127.0.0.1:5760` (SW endpoint `tcp://127.0.0.1:5760`), and drive the "world" through a control port. No
register emulation (the Thrust twin's 7.5 kLOC fake-`stm32f1xx.h` approach is not repeated).

### Seams the FW must expose now (designed in M1, implemented for target and twin)

| Seam | Functions (C, no allocation) | Target implementation | Twin implementation |
|---|---|---|---|
| `hal_uart` | `size_t hal_uart_read(uint8_t *buf, size_t max)` (non-blocking, from the RX DMA ring); `bool hal_uart_write(const uint8_t *frame, size_t n)` (queue a whole frame, false if no room); `bool hal_uart_tx_idle(void)`; `uint32_t hal_uart_rx_overruns(void)` | USART2 + DMA1 Stream5/6 Ch4 | TCP socket, byte pacing at 92 160 B/s |
| `hal_time` | `uint32_t hal_time_us(void)` (free-running 32-bit, 1 MHz, wraps); `uint32_t hal_time_ms(void)` | TIM5 | virtual clock (lock-step or real-time) |
| `hal_step` | `void hal_step_set_dir(int dir)` (only while stopped); `void hal_step_start(uint32_t period_ticks)`, `void hal_step_set_period(uint32_t)`, `void hal_step_stop_now(void)` (no runt pulse); `int32_t hal_step_count(void)` (completed pulses); `bool hal_step_running(void)`; per-step callback `step_isr()` into the pure ramp generator; `void hal_ena_set(bool enabled)` (polarity applied above the seam) | TIM2 PWM mode 2 + update IRQ (R4 §1.3) | integrates virtual pulses at the commanded periods; exact counts; `pos_uncertain` model |
| `hal_inputs` | `uint16_t hal_inputs_raw(void)` (bits as ICD IO mask: E-stop sense, limits, STOP, PAUSE, ALM, PEND, DRV_POWER); edge callbacks `on_input_edge(id, level, t_us)` from EXTI | GPIO + EXTI (priorities per FW_design) | world model (switch positions, buttons, bounce, broken wire) |
| `hal_outputs` | `hal_rate_pin(bool)`, `hal_trip_relay(bool)` (provisioned, unused R1), `hal_led(...)` | GPIO | recorded for assertions |
| `hal_hx711` | callback `on_afe_sample(int32_t raw, uint32_t t_us)` delivered from the DOUT-ready ISR **after** the position latch (`hal_step_count()` read in the same ISR); `hal_hx711_config(gain_pulses, rate)`; `hal_hx711_powerdown(bool)` | EXTI on DOUT + bit-bang (`hx711_seq.h`, Thrust origin) | sample source from the load model at 80 SPS·(1+ε), noise, rails, stall, missed conversions; the pin-level read itself is covered by Unity tests of `hx711_seq.h` |
| `hal_flash` | `bool hal_flash_erase(uint32_t sector)`, `bool hal_flash_program(uint32_t addr, const void *src, size_t n)`, `const void *hal_flash_map(uint32_t addr)` | F4 sector erase / word program | file `flash.bin` (persists across twin "resets"; power cut injectable at every program step for FW-NVM-002) |
| `hal_sys` | `hal_wdg_kick()`, `hal_wdg_set_timeout(ms)`, `hal_reset_cause()`, `hal_reset()`, `hal_uid(uint8_t[12])`, `hal_clk_fallback()`, critical-section/BASEPRI macros | IWDG, RCC, SCB | process restart / flags |

Rules for A: the protocol, command check, state machines, NVM codec, ramp planner and safety logic call only
these seams (no direct register access outside `src/hal_*`); every seam call that the twin cannot serve
deterministically (timing) takes `t_us` as an argument or reads `hal_time_us()`.

### Twin build and control (Integrator owns `00_System/tools/fw_twin/`, P2/M1)
`build.py` compiles the FW sources + twin seams with the host gcc (CLion MinGW, R3 §3.4) into `fw_twin.exe`;
`fw_twin.exe --port 5760 --ctl 5761 --scenario <file.json> --clock lockstep|realtime`.

## Shared simulator / twin world-control vocabulary (F-B-06, outline v1)

Both the SW simulator (`io.sim.SimControl`, Implementer B) and the FW twin control port accept the same
actions, so differential tests (`03_SW/tests/integration/test_sim_vs_twin.py`, Integrator) drive both
identically. Control port: one JSON object per line over TCP 5761, reply `{"ok": true}` or
`{"ok": false, "error": "..."}`.

| Action | Arguments | Effect |
|---|---|---|
| `estop` | `open: bool` | E-stop sense input (and, if `drv_power_follows`, driver power after `k1_delay_ms`) |
| `drv_power` | `on: bool` | DRV_POWER input / driver supply (K1) |
| `button` | `name: "stop"\|"pause"`, `pressed: bool`, `bounce_ms?: [..]` | physical buttons (polarity per parameters) |
| `limit` | `name: "start"\|"end"`, `active: bool` (forced) / `position_um: int` (switch location) / `broken: bool` | limit switches |
| `alm` / `pend` | `active: bool` | driver outputs |
| `specimen` | `kind: "none"\|"spring"\|"bilinear"`, `k_n_per_mm`, `x_contact_um`, `k2_n_per_mm?`, `f_yield_n?`, `f_break_n?`, `relax_pct?`, `relax_tau_s?` | load model |
| `load_offset` | `counts: int` | cell zero offset |
| `afe` | `rate_error: float`, `noise_counts: float`, `stall: bool`, `saturate: "pos"\|"neg"\|null`, `drop_every: int` | HX711 model |
| `inject` | `fault: "step_fault"\|"tx_congestion"\|"rx_corrupt"\|"link_silence"\|"hang"`, `duration_ms?` | fault injection |
| `clock` | `advance_ms: int` (lock-step only) | advance virtual time |
| `query` | `what: "world"\|"pulses"\|"outputs"` | observe the world (`x_um_true`, PUL edge count, ENA level, RATE level) |
| `reset` | `cause: "pin"\|"power"\|"iwdg"` | board reset (flash kept) |

Scenario file (`bird.bend.simscenario` v1, JSON, shared by simulator and twin):

```json
{
  "schema": "bird.bend.simscenario", "version": 1,
  "world": {"stroke_um": 300000, "start_switch_um": -1500, "end_switch_um": 301000,
            "cell_counts_per_n": 3285.0, "load_offset_counts": 125000,
            "specimen": {"kind": "spring", "k_n_per_mm": 50.0, "x_contact_um": 120000},
            "afe": {"rate_sps": 80, "rate_error": 0.005, "noise_counts": 45},
            "driver": {"lag_tau_ms": 5, "drv_power": true}, "estop_open": false},
  "params": {"motion.steps_per_mm": 160.0},
  "schedule": [{"t_ms": 5000, "action": "button", "name": "pause", "pressed": true},
               {"t_ms": 5100, "action": "button", "name": "pause", "pressed": false}]
}
```
`params` = parameter overrides applied before the test (via SET_PARAM by the harness, not by the model).
Field names and units: µm, counts, N, ms. Final names are frozen together with B in M1 (SW_design §12.4).
