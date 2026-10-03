# Bird Bend Stand — Firmware test report, M1 gate (link & skeleton)

| Item | Value |
|---|---|
| Doc | `02_FW/docs/FW_test_report_M1.md` |
| Gate | **M1** (P2 / link & skeleton), FW_test_plan **v0.2** §5.1 |
| Date | 2026-10-03 |
| Author | Validator E — FW (independent of Implementer A) |
| FW under test | commit `ff92061` (M1 implementation checkpoint), `02_FW/src/**` unchanged by the validator; FW version 0.1.0; build id = build date (target) / `host` (twin) |
| Interfaces | SRS v0.4.1, ICD v0.4.1 (`PROTO_ICD_VERSION` 0.4.1, PROTO 1.0, PAYLOAD 1), `params.yaml` dict_version 3, `PARAM_DICT_HASH` 0xF0376293, 48 parameters; DECISIONS D-01…D-36 |
| Tools | host gcc 13.1 (CLion MinGW), PlatformIO 6.2.0 (ststm32 20.0.0, arduinoststm32 4.30000.0, arm-none-eabi-gcc 12.3), Python 3.14.7, pytest 9.1.1 + pytest-randomly, FW host twin `00_System/tools/fw_twin` (A's core, lock-step) |
| Constraints | **D-06: no hardware** — target-only parts stay open (+H, HG-xx). No commits. |

## 1. Verdict

**Final verdict (after re-test 2 of 2026-10-03, §8): GO WITH CONDITIONS (C1–C4); C5 closed.**

History: first run (§2–§4, commit `ff92061`) formally NO-GO (DEF-M1-01 Medium, DEF-M1-02 Low). Re-test 1 (§7):
both closed, two Low regressions of the OVERRUN fix found (DEF-M1-03, DEF-M1-04) → condition C5. Re-test 2 (§8):
DEF-M1-03, DEF-M1-04 and OBS-M1-08 verified closed; all 45 M1 TCs pass, including the added edge cases; no open
DEF against the M1 code. The remaining conditions C1–C4 are outside the M1 code (hardware gate under D-06, M2
re-runs of motion/input parts, CR-01 documents, the OBS-M1-01 SRS/ICD decision).

Everything else in the M1 scope passes twice, including the 10⁵-stream parser corpus (two seeds), the whole
check-vector set, the 10-minute stream run, the NVM power-cut sweep at every program word and inside an erase, and
13 of 13 static checks. Conditions:

| # | Condition | Owner | Closure |
|---|---|---|---|
| C1 | +H parts stay open under D-06: FW-PLT-002 (HG-02 PUL frequency), FW-NVM-003 (HG-04 no split frame / E-stop during erase), IF-002 (HG-03 921 600 Bd soak), NFR-008 (HG-27), NFR-005 (HG-18 stack value), FW-STR-002 target latency | Orchestrator / E | PO-approved HW gate |
| C2 | M2 re-runs of M1 TCs whose motion/input part has no source in M1: TC-FW-NVM-003-01 (SAVE while moving → E_BUSY), TC-IF-005-01 (repeated MOVE_ABS, no double motion), TC-FW-STR-003-01 (MOVING, HOMED, ENABLED, ESTOP, FAULT, LIMIT_*, LOAD_LIMIT, LINK_WDG, PAUSE_BTN, ALM, PEND, POS_UNCERTAIN) | E | M2 gate |
| C3 | CR-01 (D-36) applied to SRS/ICD v0.5, pinout (PC7 STOP still documented in pinout §1.1) and this plan (STOP-button TCs) | Orchestrator, C, A, E | before M2 entry |
| C4 | OBS-M1-01 (responses to commands that arrive during a SAVE come after the flash operation, > 10 ms): SRS/ICD exemption or FW change | Orchestrator, C, A | before M3 (SW confirm path) |
| ~~C5~~ | ~~DEF-M1-03 and DEF-M1-04 (Low) fixed by A and re-verified by E~~ — **closed by re-test 2 (§8)** | A, E | closed 2026-10-03 |

## 2. Evidence (commands and counts)

All commands from the repo root in Git Bash with `PATH` = CLion MinGW 13.1 first (CLAUDE.md). Logs are in the
validator scratchpad (`…/scratchpad/validator-e-fw/logs/`).

| # | Command | Result |
|---|---|---|
| E-1 | `.venv/Scripts/pio test -d 02_FW -e native` (A's suites; run 1 = before the validator suites existed) | **19 suites, 94/94 test cases PASS** |
| E-2 | `.venv/Scripts/pio test -d 02_FW -e native -f "test_impl_*"` (run 2) | **94/94 PASS** |
| E-3 | `VAL_CORPUS_SEED=1 python 02_FW/test/val_oracles/gen_val_vectors.py` then `pio test -d 02_FW -e native -f "test_val_*" -v` (`VAL_SEED` unset = fixed order) | **6 suites, 24/24 PASS**; anti-skip counts executed = expected: crc 4/4, frames 164/164 (47 requests, 66 responses, 9 DATA, 41 EVENT, 1 invalid), streams 11/11, parser corpus 100 000/100 000, check vectors 509/509 (228 OK, 281 NACK with exact bytes, 38 `paused_after`), units 654/654 (280 shared + 374 validator oracle), bits 39/39 |
| E-4 | same with `VAL_CORPUS_SEED=79248656`, `VAL_SEED=33035754` (new corpus, shuffled test order) | **24/24 PASS**, corpus 100 000/100 000 |
| E-5 | negative controls: one expectation altered in a copy of `check.txt`, `crc.txt`, `frames.txt`, last line of `units.txt` removed | each suite **FAILS** as intended (value mismatch / anti-skip count 653 of 654) |
| E-6 | `python -m pytest 02_FW/test/twin -q -p no:randomly -s -rx` (fixed order) | **23 passed, 2 xfailed (strict: DEF-M1-01, DEF-M1-02)**; `--runxfail` shows both fail on their assertion |
| E-7 | `python -m pytest 02_FW/test/twin -q -p randomly --randomly-seed=1910203` | **23 passed, 2 xfailed** |
| E-8 | `pio run -d 02_FW -e nucleo_f446re` (+ A's post-script `check_map.py`) | **SUCCESS**, no warning; flash 24 676 B, RAM 6 536 B static (10 968 B incl. 4 KB stack reserve); check_map M-1…M-4 PASS |
| E-9 | `pio run -d 02_FW -e nucleo_f446re_debug` | **SUCCESS**, check_map PASS |
| E-10 | `python 00_System/tools/gen_params.py --check`; `python 00_System/tools/gen_vectors.py --check` | **exit 0 / exit 0** |
| E-11 | `python 02_FW/test/static/check_static.py` (validator static checks S-01…S-13, independent of A's check_map) | **13/13 PASS** |
| E-12 | `python -m pytest 03_SW/tests/integration -q -p no:randomly` (Integrator suite vs the FW twin, run 1) | **43 passed, 4 skipped, 1 xfailed** (skips: stop timing needs motion → M2; xfail IF-C-M1-02) |
| E-13 | same, `--randomly-seed=2610031` (run 2) | **43 passed, 4 skipped, 1 xfailed** |
| E-14 | read-only `git cat-file`/`git show 37c8747:` in `Thrust_Stand_HAW` (no write, no build there) | origin commit exists; `crc16.c`, `crc32.c`, `frame.c`, `stream_sched.c` differ from it only in the origin/ID-tag header lines |

Twin measurements printed by E-6: stream 10 min virtual at 80 Hz + 20 commands/s (random phase) + GET_ALL_PARAMS
polling + two DOUT stalls: **47 446 conversions = 47 446 DATA frames with AFE data**, 0 dropped; data-ready → first
DATA byte **0 µs** with an idle line and **0 µs beyond the remaining wire time** of the frame in transmission (304
cases; the twin runs ISRs atomically, FW_test_plan §1.1 principle 3); FW→PC **2 623 B/s = 2.85 %** of 92 160 B/s
incl. all responses; **12 000 commands, worst response 0.28 ms** (≤ 10 ms); SAVE with a sector erase answered
< 2.5 s.

## 3. Results per M1 TC (45)

Legend: PASS · FAIL · DEFERRED-M2 (part needs M2 sources) · BLOCKED-target-only (+H, open under D-06). A TC is
counted once at its primary result.

| TC | Lvl | Result | Evidence (validator test / check) |
|---|---|---|---|
| TC-SYS-003-01 | U | PASS | `test_val_units`: 128 + 112 + 40 shared vectors, 374 validator binary64 cases (ties ±0.5, negatives, near-int32 results), exact tie checks |
| TC-SYS-007-01 | S | PASS | S-05: 15/15 FW signals of `board_pins.h` = pinout §1.1 (END default PC1, PB1 fallback flag), USART2 PA2/PA3; S-04 NVIC levels. pinout still lists PC7 STOP → C3 |
| TC-SYS-008-01 | T+S | PASS | `test_twin_builds_from_unchanged_sources` (`build.py --check-seams` exit 0, A's core linked, no `#if…TWIN/HOST_TEST` in core/pure); all twin suites use A's core (fixture refuses the probe) |
| TC-SYS-010-01 | S | PASS | S-12: `Implements:` in every `src`/`include` file; 17 reused files carry `origin path @ commit` (@37c8747, verified to exist, E-14); S-13: 40/40 M1 Must requirements have ≥ 1 `Verifies:` test |
| TC-FW-PLT-001-01 | S | PASS | E-8/E-9; S-03 (pinned versions, R1 §6.2 flags, map, post-script), S-02 (7 own handlers strong in the image, nothing in sectors 1–2), S-05 |
| TC-FW-PLT-002-01 | S | PASS | S-06: BRR and TIM5 PSC from `HAL_RCC_GetPCLK1Freq()`, no hard-coded clock constant, bounded HSE/PLL/switch waits (DWT) |
| TC-FW-PLT-002-02 | T | PASS (+H HG-02 open) | `test_clk_fallback_status_event_not_data`: `CLK_FALLBACK` in STATUS sys_flags, EVENT BOOT → CLK_FALLBACK once, DATA bits unchanged |
| TC-FW-CFG-001-01 | S | PASS | S-08 (no parameter table / id literal outside `gen/`, GENERATED banners), S-09/E-10 |
| TC-FW-CFG-002-01 | U+T | PASS | `test_get_all_params_pages_equal_vectors_at_defaults` (3 pages byte-identical, page 3 → E_RANGE 0); `test_all_params_after_random_sets` (200 random SETs, ≥ 20 accepted: 48 ids once, ascending, values = model); codec page encoder |
| TC-FW-CFG-003-01 | U | PASS | `test_val_check`: all 509 vectors incl. every `set_*` default/min/max/below/above/NaN/type/padding/moving and `rule_h1…h5`, exact NACK bytes, no side effect on context and parameter image |
| TC-FW-CFG-003-02 | T | first run **FAIL** (DEF-M1-01) → re-test **PASS** (§7) | response = GET_PARAM, 0 flash writes, `ena_invert`/`pul_invert` → REBOOT_PENDING with the old ENA level, `afe.rate_sps` effective at once (RATE pin, `hal_hx711_config`) all PASS; **`drv.pwr_sense_enable` (reboot_required) takes effect without reboot** (`test_reboot_required_params_old_behaviour_until_save_reboot`, strict xfail) |
| TC-FW-CFG-004-01 | U+T | PASS | `test_get_info_fields`: proto 1.0, payload 1, FW 0.1.0 = `platformio.ini`, hash = `PARAM_DICT_HASH`, 48 params, UID = seam, features {AFE_SYNTHETIC, NVM, TWIN}; INFO encoder vectors |
| TC-FW-NVM-001-01 | T | PASS | `test_cfg_dirty_save_reboot_defaults_load`: blank → CFG_DIRTY + NVM_DEFAULTED + PARAMS_DEFAULTED(1); SAVE → clean + PARAMS_SAVED(seq); SET → dirty, back → clean (ICD §11.2); session SET never dirties; REBOOT restores, session at defaults, PARAMS_LOADED(0); DEFAULT → all defaults + dirty + PARAMS_DEFAULTED(0); LOAD → record, session kept; no session id in the decoded record |
| TC-FW-NVM-002-01 | U | PASS | `test_val_nvm` (9 tests, validator record writer + CRC-32): FW encoder byte-identical to the documented layout, 45 entries, no session ids; blank / CRC-bad → PDEF 1/2; newest by modular seq, torn newest skipped, wrap; log rule incl. torn slot, sector full → erase other (only if non-blank), seq 0xFFFFFFFF → 1; rule 3 out-of-range → default counted; rule 4 migration (retired 0x0401 dropped, type change / out of range / missing id → default) PDEF 3; rule 5 → all defaults PDEF 4; LOAD keeps session values, hard rule → refused, RAM unchanged; CFG_DIRTY definition |
| TC-FW-NVM-002-02 | T | PASS | `test_power_cut_at_every_program_word_and_erase`: power cut before each of the 98 program words of a record (90 entry + 7 header + 1 commit) → after every boot a complete record, old or new, never mixed, and the booted value = the record; cut in the middle of the sector erase of a sector switch → previous record intact; cuts in the first SAVE of the new sector; `test_blank_flash_and_hash_change_migration` (PDEF 1; other hash → PDEF 3, NVM_DEFAULTED, CFG_DIRTY, value kept by id) |
| TC-FW-NVM-002-03 | A | PASS | 2 sectors × 32 slots of 512 B, one slot per SAVE, each sector erased once per 64 SAVEs; F4 sector endurance 10 000 cycles (R2, datasheet value ASSUMED) → ≥ 640 000 SAVEs ≥ 10 000 |
| TC-FW-NVM-003-01 | T | PASS (idle part); **DEFERRED-M2** (while moving); +H HG-04 | `test_save_no_split_frame_and_overrun_after_stall`: SAVE with a 500 ms sector erase in a TX backlog (3 × 152 B pages + events, stream on): no frame on the wire during the flash operation, first frame after the stall carries OVERRUN, none later; response < 2.5 s |
| TC-FW-CMD-001-01 | U | PASS | `test_val_codec` (164 frames: builder + parser + decoder/encoders, 11 streams) and `test_val_check` (check order: unknown TYPE, LEN before args, args before state …) |
| TC-FW-CMD-001-02 | T | PASS | `test_fuzz_one_response_per_valid_command`: 10 000 frames (valid commands, undefined TYPE in 0x01…0x3F, FW-invalid TYPE, wrong LEN, bad CRC, noise): responses = exactly the CRC-valid command frames of `ref_codec.FrameParser`, in order, SEQ echoed; counters = oracle; no pulse; flood: back-pressure into the RX ring, `rx_overruns` counted (`test_status_counters_each_condition`) |
| TC-FW-CMD-002-01 | U+T | PASS | `test_set_valid_boundary_across_wrap` (t0 = 2³² − 1.5 s, three boundaries, wrap crossed, stored with stream off, VALID 0 after boot); `test_val_link` modular boundary at 0, 0xFFFFFFFF, 0x80000000, settle |
| TC-FW-CMD-004-01 | U+T | PASS (+H loop_max_us HG-18) | `test_status_counters_each_condition`: rx_crc_errors, rx_frame_errors (LEN > 160, inter-byte timeout, invalid TYPE), rx_frames_ok, link_age_ms, rx_overruns (40 ms main-loop hang + 4 KB), tx_drops (congestion), event_overflows + EVENT SEQ gap, halt_src/pause_src, v_limit, afe_rate_dsps, reset_cause for pin/IWDG/power/software; STATUS encoder vectors. `loop_max_us` = 0 in the twin by construction |
| TC-FW-STR-001-01 | T | PASS | no DATA after boot; START×2 / STOP×2 idempotent; `frame_seq` from 0, not reset; payload_version 1 in every frame |
| TC-FW-STR-002-01 | T | PASS (target latency → HG-05/HG-18) | 10 min, 80 Hz, GET_ALL_PARAMS + 20 cmd/s: frames = conversions (47 446), latency 0 µs idle / 0 µs beyond the remaining wire time |
| TC-FW-STR-003-01 | U+T | first run **FAIL** (DEF-M1-02) → re-test **PASS** (incl. added edge case, DEF-M1-04 closed in re-test 2); M2 bits DEFERRED-M2 | encoders: DATA vectors incl. all-bits; composer: each of 8 + 16 + 10 + 5 inputs sets exactly its ICD bit (`test_val_flags`); twin: VALID, HALT, PAUSED, AFE_STALE + NO_AFE_DATA, AFE_SATURATED, AFE_SETTLING, AFE_RATE_MISMATCH (+ EVENT), DRV_PWR (sense off + reboot) set and cleared correctly; **OVERRUN is also set after an AFE (sensor) stall and after a gap while the stream was off** (`test_overrun_only_for_fw_losses`, strict xfail) |
| TC-FW-STR-004-01 | T | PASS (k = 1/5/50 and the added STOP/START edge case, DEF-M1-03 closed in re-test 2) | k = 1, 5, 50: one `frame_seq` gap of exactly k, OVERRUN only on the next sent frame, `tx_drops` + k, STOP response during the congestion delivered |
| TC-FW-PAR-001-01 | S | PASS | S-11: SRS Table 5.1 = `params.yaml` for all 48 rows (type, range, default, M/N/R flags), H5 in rules |
| TC-FW-PAR-002-01 | S | PASS | S-11 (PUL/ENA polarity R flag, ENA settle default 500) |
| TC-FW-PAR-003-01 | S | PASS | S-11, no speed parameter in step units; H4 vectors (`test_val_check`) |
| TC-FW-PAR-004-01 | S+U | PASS | S-11, no home-switch parameter; `rule_h1` vectors pass |
| TC-FW-PAR-005-01 | S | PASS | S-11 (three session parameters without N) |
| TC-FW-PAR-006-01 | S | PASS | S-11; no polarity parameter for E-stop, limits, DRV_PWR (`io.stop_active_level` still present until CR-01) |
| TC-IF-001-01 | S | PASS | S-07 (no hand-written protocol code define / numeric case label outside `gen/`), S-10: 198 generated names = `ref_codec` tables |
| TC-IF-002-01 | U+S | PASS (+H HG-03) | LE round trip; analysis: BRR = round(45 MHz / 921 600) = 49 → 918 367 Bd (−0.35 %) |
| TC-IF-003-01 | U | PASS | 11 `streams` + 2 × 100 000 random streams (seeds 1 and 79248656): frames and the four counters = `ref_codec.FrameParser` |
| TC-IF-004-01 | U+T | PASS | CRC vectors + ICD check values; 1 corrupted vector frame; twin: corrupted HALT/PAUSE/SET_PARAM/STREAM_START act on nothing, `rx_crc_errors` + 4 |
| TC-IF-005-01 | T | PASS (M1 part); DEFERRED-M2 (double-motion part) | identical frames (same SEQ) answered twice each (PING, SET_PARAM, MOVE_ABS, HALT), one HALT_SET, 0 pulses |
| TC-IF-006-01 | U+S | PASS | DATA encoder vectors, header SEQ = low byte of `frame_seq` |
| TC-IF-007-01 | T | PASS | 10 min with two DOUT stalls: DATA with AFE data = delivered conversions; fallback frames only inside the stalls, all NO_AFE_DATA + AFE_STALE, ≈ 10 Hz |
| TC-IF-008-01 | T | PASS | GET_INFO fields; payload_version 1 in every DATA frame |
| TC-IF-009-01 | S+U | PASS | ICD/params review (µm, µm/s, µm/s², µs, counts; absolute targets only, S-11), codec vectors |
| TC-IF-010-01 | S | PASS | E-10; the validator reads the same JSON files in place; executed counts = JSON counts (crc 4, frames 164, streams 11, check 509, units 280) |
| TC-IF-011-01 | A+T | PASS | DATA 26 B × 80 Hz = 2 080 B/s (2.26 %), measured FW→PC incl. responses 2.85 %; responses never dropped under DATA congestion (U: wire order D > R > E, R reserve) |
| TC-IF-012-01 | S+T | PASS | each of the 26 commands: no E_UNKNOWN_CMD; M1 motion → E_STATE / E_INTERNAL NOT_IN_BUILD; REBOOT → reset cause SOFTWARE |
| TC-NFR-005-01 | S+T | PASS (+H HG-18) | S-01: no allocator / printf / HardwareSerial / HardwareTimer symbol, `_Min_Heap_Size = 0`; STATUS `stack_free_min` reported |
| TC-NFR-008-01 | T | PASS (+H HG-27) | 12 000 commands of 15 types under streaming: worst 0.28 ms; SAVE (erase) / LOAD / DEFAULT < 2.5 s (see OBS-M1-01) |

**Counts:** 45 M1 TCs — first run **43 PASS, 2 FAIL** (TC-FW-CFG-003-02, TC-FW-STR-003-01); **re-test 1: 45 PASS** (2 Low edge-case DEFs open); **re-test 2: 45 PASS, 0 FAIL, 0 BLOCKED, no open DEF**. Parts open:
DEFERRED-M2 in 3 TCs (NVM-003-01, IF-005-01, STR-003-01), +H in 7 TCs (PLT-002-02, NVM-003-01, CMD-004-01,
STR-002-01, IF-002-01, NFR-005-01, NFR-008-01). Requirements: 40 M1
requirements, 38 verified at host/twin/static level, 2 failed (FW-CFG-003, FW-STR-003).

Validator test inventory: Unity 6 suites / 24 test cases (`02_FW/test/test_val_{codec,check,units,flags,link,nvm}`),
twin pytest 3 files / 25 tests (`02_FW/test/twin/`), static 13 checks (`02_FW/test/static/check_static.py`), oracles
`02_FW/test/val_oracles/{gen_val_vectors.py,nvm_ref.py}`, shared header `02_FW/test/val_common/val_io.h`.

## 4. Defects

| ID | Sev. | Finding (file:line, evidence) | Fix hint | Addressee |
|---|---|---|---|---|
| **DEF-M1-01** (**closed**, re-test §7) | Medium | **`drv.pwr_sense_enable` (reboot_required) takes effect immediately.** The core reads the RAM value: `02_FW/src/core/cmd.c:41` (`c->drv_power = !g_fw.p.drv.pwr_sense_enable`), `02_FW/src/core/status.c:54` (DATA/STATUS `DRV_PWR`), and the pure check evaluates `c->p->drv.pwr_sense_enable` from the RAM image (`02_FW/src/pure/cmd_check.c:52`). Twin evidence: after SET `drv.pwr_sense_enable` = 0 (no SAVE/REBOOT) STATUS shows `DRV_PWR` = 1 and ENABLE changes from `E_STATE` DRV_UNPOWERED to `E_INTERNAL` NOT_IN_BUILD, while REBOOT_PENDING = 1. Violates FW-CFG-003 ("a `reboot_required` parameter becomes effective only after SAVE + REBOOT") and params.yaml. No effect in M1 (no motion, no sensing), but in M2 this is the bring-up switch of the driver-power safety supervision (SAF-FW-024/025/026) — it must not be switchable live. Test: `02_FW/test/twin/test_val_twin_params.py::test_reboot_required_params_old_behaviour_until_save_reboot` (strict xfail). | Use `g_fw.boot_pwr_sense` wherever the sense enable is evaluated (core ctx, `fw_flags`, M2 drvmon), and give `cmd_check` the effective value (e.g. the core passes a params view with the boot value, or `cmd_ctx_t` gets an explicit `pwr_sense` field — then the Integrator aligns `ref_cmdcheck` / `state_schema`). Same rule for `pul_invert` / `ena_invert` when M2 drives the pins (already boot-latched in `app.c:37-39`). | A (C if the context schema changes) |
| **DEF-M1-02** (**closed**, re-test §7) | Low | **OVERRUN set for losses that are not in the FW.** `02_FW/src/core/afe.c:60-61` sets `g_fw.st.overrun` for any sample gap > 1.5 periods, also when the AFE itself stopped converting (DOUT stall, AFE_STALE) and while the stream is off (the flag then rides on the first frame after STREAM_START). Twin evidence: after a 500 ms DOUT stall the first real frame carries OVERRUN; after a 50 ms stall with the stream off the first frame after STREAM_START carries OVERRUN. ICD §7.6 bit 7: "≥ 1 DATA frame dropped or ≥ 1 conversion missed **by the FW** since the previous sent frame"; SRS NFR-004 attributes OVERRUN to FW losses (frame accounting in the SW). Test: `test_val_twin_link.py::test_overrun_only_for_fw_losses` (strict xfail). | Mark missed conversions only for the FW's own AFE hold (NVM) — e.g. set the flag in `nvm_service` DONE / `afe_rearm_after_hold()` instead of the Δt rule, or apply the Δt rule only while `g_fw.st.on` and not after a stale period; clear a pending OVERRUN on STREAM_START. | A |

| **DEF-M1-03** (**closed**, re-test 2 §8) | Low | **A class-D drop just before STREAM_STOP loses its OVERRUN** (regression of the DEF-M1-02 fix). `02_FW/src/core/stream.c:53-55` clears `g_fw.st.overrun` on every off → on transition; after the fix nothing else can leave a stale flag while the stream is off, so the clear only discards a pending class-D drop. Twin: congestion drops the last due frame, STREAM_STOP, STREAM_START → the first sent frame shows a `frame_seq` gap of 1 **without** OVERRUN (ICD §2.2/§2.4 "the next sent frame has OVERRUN"; the SW would count a link loss, NFR-004). Test: `test_val_twin_link.py::test_overrun_after_class_d_drop_survives_stream_stop_start` (strict xfail). | Remove the clear in `stream_set()` once DEF-M1-04 is fixed, or keep separate `drop_pending` (cleared only by a sent frame) and hold-gap flags. | A |
| **DEF-M1-04** (**closed**, re-test 2 §8) | Low | **False OVERRUN when the stream is started right after a SAVE.** `02_FW/src/core/afe.c:61-66` judges the hold gap with `g_fw.st.on` at the **first sample after** the hold, not during it. Twin: stream off, SAVE with a sector erase (500 ms AFE hold), STREAM_START right after the SAVE response → the first DATA frame carries OVERRUN although no frame was due during the hold (ICD §7.6 bit 7). Test: `test_val_twin_link.py::test_no_overrun_when_stream_started_right_after_save` (strict xfail). | Latch "stream on" when the hold starts (NS_HOLD) and flag the gap only if the stream was on during the hold (or count due frames during the hold). | A |

Earlier findings verified closed in the M1 code: DEF-P1-01 (H5, default 250 ms; `rule_h5` vectors pass, no AFE_STALE
at 10 SPS), DEF-P1-02 (seam single source, `--check-seams` exit 0), DEF-P1-03 M1 subset (lock-step, wire log, byte
injection, flash cut, reset causes used by these suites), DEF-P1-07 (dispatcher back-pressure, flood test).
DEF-P1-04 (sniffed-stop hold) and DEF-P1-06 are M2 behaviour (no motion / inputs in M1).

## 5. Observations

| ID | Observation | Proposal | Addressee |
|---|---|---|---|
| OBS-M1-01 | Every command that arrives during a SAVE is dispatched only after the flash operation (`02_FW/src/core/link.c:82`, by design: QUIESCE/PROGRAM); with a sector erase its response comes ≈ 0.5 s later, also for STOP/HALT/PAUSE (the motion part is sniffed; M1 has no motion). NFR-008 exempts only the NVM commands themselves; ICD §9.4 uses the responses as the stop confirmation. The RX ring (2 KB ≈ 22 ms of line time) also overflows if the PC keeps sending during an erase. | SRS/ICD: state the exemption ("responses to commands received during an idle NVM operation ≤ its duration + 10 ms", like KL-06) and tell the SW not to stream commands during SAVE; or answer sniffed frames from the tick. | Orchestrator, C, B |
| OBS-M1-02 | Twin: bytes injected with `rx_bytes at_us` inside an engine flash stall are delivered (and logged in `wire_log` rx) at the end of the stall, so commands arriving during a SAVE cannot be timed (OBS-M1-01 is from code analysis). | Model RX DMA during stalls (bytes land in the ring at their wire time). | C |
| OBS-M1-03 | LOAD of a record that violates a hard rule: the FW answers E_NVM 1 with no EVENT (FW_design §9.6 #4); ICD §11.3 rule 5 lists "PARAMS_DEFAULTED (4)" for boot and LOAD in one sentence. | ICD v0.5: say "boot only" for the event. | C |
| OBS-M1-04 (closed: A's README now uses `-f "test_impl_*"`) | OI-FW-34 resolved without a `platformio.ini` change: the validator suites run in A's `[env:native]` (A's `fake_hal.c` is linked but not used by any validator suite; no A helper or pre-script header is included). Side effect: an unfiltered `pio test -e native` now also runs `test_val_*`; they need `02_FW/.pio/val_vectors/` (generated by `gen_val_vectors.py`; a missing file is a clear FAIL, never a skip). | A's evidence command: `-f "test_impl_*"`, or run the generator first. | A, Orchestrator |
| OBS-M1-05 | Unit conversions saturate at the int32 range (`units.c` `sat_i32`); ICD §0.1 does not say what happens beyond int32. Not reachable with the parameter ranges. | Informative; ICD may state saturation. | C |
| OBS-M1-06 | `loop_max_us` is always 0 in the twin (main-loop passes take no virtual time) and `stack_free_min` is the seam constant 3072 — both are target values (HG-18). | Keep in the HG list. | E |
| OBS-M1-07 | pinout.md §1.1/§4 still list the PC7 STOP/BREAK input and level-1 STOP (D-36). | CR-01 (C3). | A |
| OBS-M1-08 (**closed**, re-test 2 §8) | `params_rt_effective()` (`core/params_rt.c:38-46`) lists the three reboot_required parameters by hand; a fourth `reboot_required` parameter added to `params.yaml` would silently act at once. | Derive the overlay from the generated flags of `PARAM_TABLE` (or a generated list); E adds a static check in M2. | A |

## 6. Change requests to this plan

FW_test_plan updated to **v0.2** (TC corrections: TC-FW-NVM-003-01, TC-FW-CMD-001-02, TC-IF-005-01,
TC-FW-STR-003-01; validator infrastructure as built). No request to A for `platformio.ini`.

## 7. Re-test after A's fixes (2026-10-03)

**Object:** working tree on top of `ff92061` with A's fix diff in `02_FW/src/core/{afe.c, cmd.c, fw.h, nvm.c,
params_rt.c, status.c, stream.c}` (FW_design §9.6 rows 16–17); A's suite changes in `test_impl_core_link`,
`test_impl_stream`; README now uses `-f "test_impl_*"`. Validator changes: strict-xfail markers of the DEF-M1-01 and
DEF-M1-02 tests removed; two regression probes added for the OVERRUN fix.

**Diff review**
- DEF-M1-01: `fw_cmd_ctx()` passes `params_rt_effective()` (RAM image with `pul_invert`, `ena_invert`,
  `pwr_sense_enable` at their boot values) to `cmd_check()`; `drv_power` and DATA/STATUS `DRV_PWR` use
  `g_fw.boot_pwr_sense`. Grep: no other behavioural read of the three parameters in `core/` or `pure/`; the boot
  copies are latched once after `nvm_boot()` (`app.c:37-39`); REBOOT_PENDING still compares RAM vs boot; CFG_DIRTY,
  GET_PARAM and SAVE use the RAM image (correct: the stored value is the new one). Dict 3 has exactly three
  `reboot_required` parameters (asserted by the twin test). The static `eff` copy is used from thread context only
  (command dispatch, STATUS), never from the tick or an ISR. Residual risk: hand-written list (OBS-M1-08).
- DEF-M1-02: the Δt rule now runs only on the first sample after the FW's own AFE hold (`hold_gap` set in
  `afe_rearm_after_hold()`); class-D drops still set OVERRUN in `emit()` (`stream.c:40`), confirmed by
  TC-FW-STR-004-01 k = 1/5/50 (pass). The added off → on clear and the stream state sampled after the hold cause
  DEF-M1-03 / DEF-M1-04 (Low).

**Commands and counts (re-test)**

| # | Command | Result |
|---|---|---|
| R-1 | `python 00_System/tools/fw_twin/build.py --core fw` | OK, 0 warnings, seams from A's headers |
| R-2 | `pytest 02_FW/test/twin -q -p no:randomly -rx` | **25 passed, 2 xfailed** (strict: DEF-M1-03, DEF-M1-04); the former DEF-M1-01/-02 tests **pass** (DRV_PWR unchanged and ENABLE still E_STATE DRV_UNPOWERED until SAVE + REBOOT; no OVERRUN after a DOUT stall or a stream-off gap) |
| R-3 | `pytest 02_FW/test/twin -q -p randomly --randomly-seed=3110047 -rx` | **25 passed, 2 xfailed**; `--runxfail` shows both fail on their assertions |
| R-4 | `gen_val_vectors.py` (corpus seed 1) + `pio test -e native -f "test_val_*" -v` | **24/24 PASS**, all 7 anti-skip counts equal |
| R-5 | same with corpus seed 55512077, `VAL_SEED=40912` (shuffled) | **24/24 PASS**, 7/7 counts equal |
| R-6 | `pio test -d 02_FW -e native -f "test_impl_*"` (A) | **94/94 PASS** |
| R-7 | `pio run -d 02_FW -e nucleo_f446re` | **SUCCESS**, no warning; flash 24 780 B (+104 B), RAM 6 536 B static (10 968 B incl. stack); check_map M-1…M-4 PASS |
| R-8 | `pytest 03_SW/tests/integration -q -p no:randomly` | **43 passed, 4 skipped, 1 xfailed** (unchanged) |
| R-9 | `check_static.py`; `gen_params.py --check`; `gen_vectors.py --check` | **13/13 PASS**; exit 0; exit 0 |

**Result:** 45/45 M1 TCs pass their planned cases; DEF-M1-01 and DEF-M1-02 closed; DEF-M1-03 and DEF-M1-04 (Low)
open → condition C5 (closed by re-test 2, §8).

## 8. Re-test 2 after A's fixes of DEF-M1-03 / DEF-M1-04 / OBS-M1-08 (2026-10-03)

**Object:** working tree on top of `ff92061`; cumulative fix diff in `02_FW/src/core/{afe.c, app.c, cmd.c, fw.h,
nvm.c, params_rt.c, status.c, stream.c}` (FW_design §9.6 rows 16–17). Validator change: the strict-xfail markers of
the two regression tests removed (no xfail left in `02_FW/test/twin/`).

**Diff review**
- DEF-M1-03: `stream_set()` no longer clears `st.overrun`; the flag is cleared only by a sent DATA frame
  (`stream.c:37`) and set by a class-D drop (`stream.c:40`) or the hold gap → a drop before STREAM_STOP is reported by
  the next sent frame (ICD §2.2/§2.4). Nothing can set it while the stream is off any more (sensor stalls never set
  it, the hold gap needs `hold_stream_on`), so no stale flag can survive an off period except a real FW drop.
- DEF-M1-04: `nvm.c` latches `hold_stream_on = st.on` at NS_HOLD; dispatching is paused for the whole NVM operation,
  so the stream state cannot change during the hold; `afe.c` checks the gap only once (`hold_gap`) with that latch →
  OVERRUN only for conversions due and missed during the FW's own hold (ICD §7.6 bit 7, FW-NVM-003).
- OBS-M1-08: `g_fw.boot_p` = full parameter image copied once in `app_init()` right after `nvm_boot()`; grep: no other
  writer. `main.cpp` runs `board_init(); app_init(); board_start();` and every IRQ (UART, synthetic AFE EXTI4, TIM5
  tick) is enabled only in `board_start()`, so `boot_p` is constant before the first ISR can read it (the sample ISR
  reads `boot_p.drv.pwr_sense_enable` via `fw_flags()`); in the twin `app_init()` also precedes all callbacks.
  `params_rt_effective()` and `params_rt_reboot_pending()` walk `PARAM_TABLE` by the generated `PARAM_F_REBOOT` flag
  (no hand-written list). The static `eff` copy is still used from thread context only. RAM +512 B (7 048 B static).

**Commands and counts (re-test 2)**

| # | Command | Result |
|---|---|---|
| R2-1 | `python 00_System/tools/fw_twin/build.py --core fw` | OK, 0 warnings |
| R2-2 | `pytest 02_FW/test/twin -q -p no:randomly -rx` | **27 passed**, 0 xfail (incl. the DEF-M1-01…04 tests) |
| R2-3 | `pytest 02_FW/test/twin -q -p randomly --randomly-seed=4220519 -rx` | **27 passed** |
| R2-4 | `gen_val_vectors.py` (corpus seed 1) + `pio test -e native -f "test_val_*" -v` | **24/24 PASS**, 7/7 anti-skip counts equal |
| R2-5 | same with corpus seed 90311, `VAL_SEED=7713` (shuffled) | **24/24 PASS**, 7/7 counts equal |
| R2-6 | `pio test -d 02_FW -e native -f "test_impl_*"` (A) | **96/96 PASS** |
| R2-7 | `pio run -d 02_FW -e nucleo_f446re` | **SUCCESS**, no warning; flash 24 820 B, RAM 7 048 B static; check_map M-1…M-4 PASS |
| R2-8 | `check_static.py` | **13/13 PASS** |

The integration suite was not re-run in re-test 2 (not requested; the last run, R-8 of §7, was 43 passed / 4 skipped /
1 xfail on the re-test 1 code).

**Result:** 45/45 M1 TCs pass incl. the added edge cases; DEF-M1-01…04 and OBS-M1-08 closed; no open DEF against the M1
code. **Final verdict: GO WITH CONDITIONS (C1–C4)**, §1.
