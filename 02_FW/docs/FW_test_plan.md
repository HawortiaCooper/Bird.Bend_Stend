# Bird Bend Stand — Firmware test plan

| Item | Value |
|---|---|
| Doc | `02_FW/docs/FW_test_plan.md` |
| Version | **0.1** — P1 test plan for the PO gate (no FW source exists yet; suites are written in P2 per milestone) |
| Date | 2026-10-03 |
| Owner | Validator E — FW (independent of Implementer A) |
| Binding inputs | SRS **v0.3** (SYS-001…011, SAF-FW-001…026, FW-*, IF-001…012, NFR-005…008, §3.2, §6); DECISIONS D-01…D-31 (**D-30**: PAUSED blocks every new motion start; clean-halt substitution only at step period > 2 ms **and** stop distance ≤ 1 step; **D-31**: RESUME command 0x3C clears only PAUSED, refused while HALT/ESTOP/fault latched; HALT_CLEAR clears HALT and PAUSED); ICD **v0.2** (header) with the v0.3/v0.4 deltas announced by the Integrator (`protocol.yaml` already at icd_version 0.3: BLOCK bit 10 PAUSED); `params.yaml` dict_version 2; `00_System/tools/{README.md, ref_codec.py, ref_cmdcheck.py, vectors/}`; `02_FW/docs/FW_design.md` **v0.2**; `01_HW/pinout.md` v0.2; `01_HW/wiring.md` (check list C-01…C-20) |
| Scope | FW-side verification of 102 requirements: SYS-001…011 (FW side), SAF-FW-001…026, FW-PLT/AFE/MOT/HOM/SW/CFG/NVM/CMD/STR/TIM/PAR (49), IF-001…012 (FW side), NFR-005…008. 100 Must + 2 Should (FW-HOM-003, FW-STR-005). SW-side parts of IF-005/008/011 belong to Validator F. |
| Constraints | D-06: no COM port, no flashing until the PO approves a hardware gate. D-07: integration first against the FW host twin. Target-only criteria stay **open** until then and are listed in the hardware-gate check list (§6). |

Level codes used throughout: **U** host unit test (Unity, `pio test -e native`, `src/pure` + `src/gen`, shared vectors read in place) · **T** FW host twin (lock-step virtual time, pytest driving `fw_twin.exe`) · **S** static check / inspection (build, map file, grep, document review) · **A** analysis · **H** target procedure at the PO-approved hardware gate (HG-xx, §6).

---

## 1. Strategy and test levels

### 1.1 Principles
1. **Independence.** Validator suites never include Implementer A's test helpers (`test/test_impl_*`, `test/common_impl/*`). Expected values come only from oracles: the shared vectors (`protocol_vectors.json`, `check_vectors.json`), `ref_codec.py`, `ref_cmdcheck.py`, R4 §12 reference values, and validator-owned reference models for non-protocol math (ramp, units, load limit, rate median, NVM log) in `02_FW/test/val_oracles/` (Python, they emit JSON consumed by Unity). Protocol vectors are **never** generated a second time: new protocol scenarios are requested from the Integrator for `gen_vectors.py` (R3 pitfall P12).
2. **One requirement, one acceptance criterion, lean TCs.** IDs `TC-<REQ>-nn`. A parametrised case (states × sources × parameter values) counts once. Every test carries `/* Verifies: <REQ> */` and `/* TC: TC-<REQ>-nn */` (Python: `# Verifies:` / `# TC:`).
3. **Twin results prove logic and ordering, not silicon timing.** The twin executes ISR callbacks atomically in NVIC-level order (FW_design §8.2). Twin timing criteria therefore check (a) that the reaction happens in the detecting context (same virtual event, or the stated tick), and (b) the virtual-time budget. Real latencies are measured only at the hardware gate (§6).
4. **Vectors are consumed in place** and counted: the validator run compares the number of executed vector cases with `len()` of each JSON list (anti-silent-skip check), and refuses to run when `icd_version` / `param_dict_hash` of the vectors differ from `proto_gen.h` / `params_gen.h`.
5. **Every run twice**: fixed order and randomised order/seed (seed recorded), as in Thrust_Stand.
6. **Defects** found against code are `DEF-Mx-nn` (severity, file:line, evidence, fix hint) and observations `OBS-Mx-nn`; this P1 review uses `DEF-P1-nn` / `OBS-P1-nn` (§8).

### 1.2 Levels and where they live

| Level | What | Location (owner E) | Runner | Oracle |
|---|---|---|---|---|
| U — vector conformance | CRC, every frame encode/decode, parser `streams` (frames + counters), every `check_vectors.json` case (STATUS/detail/NACK bytes, no side effect, `paused_after`) | `02_FW/test/test_val_codec/`, `test_val_check/` | `pio test -d 02_FW -e native -f "test_val_*"` (MinGW 13.1 prepended to PATH, CLAUDE.md) | shared vectors via A's pre-script headers (`$BUILD_DIR/vectors/vec_*.h`), count-checked |
| U — pure modules | units, ramp/planner, stepgen halt/stretch/path decisions, loadlim, afe_rate, debounce, drvmon, latches, homing, motion_sm, valid, flags, txsched, evq, stop_sniff, nvm_log, param_rules, resetcause | `02_FW/test/test_val_<module>/` | same | `val_oracles/*.py` → JSON (R4 formulas, SRS rules), shared vectors where they exist |
| T — twin | system behaviour of the unchanged `src/pure` + `src/core` against the Integrator's seam implementation, virtual time, fault injection (§4) | `02_FW/test/twin/` (pytest; folder not prefixed `test_` so PlatformIO ignores it) | `.venv\Scripts\python -m pytest 02_FW/test/twin -q` (then `-p randomly` seed run) | SRS acceptance criteria, ICD §5–§8, `ref_codec` for decoding, world truth from the twin (`x_um_true`, edge log) |
| T — SW⇄twin | smoke of the shared world vocabulary with the SW simulator (differential runs are owned by the Integrator, `03_SW/tests/integration/`) | consumed, not owned | – | – |
| S — static | clean build, `platformio.ini` flags, map file (no allocator/printf/HardwareSerial/HardwareTimer, handler slots, nothing in flash sectors 1–2, `.RamFunc` content), NVIC levels/critical sections, generated files untouched, origin notes, ID tags, no hand-written codes/param tables | `02_FW/test/static/check_static.py` | `.venv\Scripts\python 02_FW/test/static/check_static.py --build-dir …` | FW_design §2, pinout §1/§4, SRS NFR-005 |
| H — target | hardware-gate procedures (§6) on top of `ref_codec.py`, only after PO approval (D-06) | `00_System/tools/hil/` (Integrator reviews) | `hil_*.py --port COMx --po-approval <ref>` | SRS §6 budgets, measured with DWT, logic analyser, scope |

### 1.3 Twin harness requirements (input to the Integrator; see DEF-P1-02/03)
The T-level cases need, beyond `tools/README.md` "world-control vocabulary v1":
- **lock-step clock** with deterministic event order; scenario start time configurable (to test the 2³² µs wrap);
- **time-stamped edge log** of PUL (both edges), DIR and ENA, plus a log of seam calls (`hal_step_stop_now/abort/set_period_now/set_period/arm_last`, `hal_ena_set`, `hal_hx711_config`, `hal_flash_*`) with virtual timestamps;
- **byte-level RX injection at a virtual time** (lock-step; TCP pacing alone cannot place the last byte of a STOP frame at a known instant) and a **wire log** of every TX frame with its first-byte/last-byte virtual times (DATA latency, response time, frame integrity around NVM);
- input models with **bounce/chatter and broken wire for every input** (E-stop sense, START, END, STOP, PAUSE, ALM, PEND, DRV_PWR), independent `estop` and `drv_power` control (K1 weld = E-stop open while power stays on);
- HX711 model controls: stall, single missed edge, rate error, rails, **SCK-high overrun / power-down symptom**, observation of the gain pulse count per read;
- **flash power cut** after program word *n* / during erase *k*, flash write counter;
- **IWDG model** (timeout window from `hal_wdg_set_timeout`, LSI 17…47 kHz selectable) and hang injection in the main loop, the tick, and as a level-1 ISR storm;
- RX DMA ring overflow, TX congestion (class D full), HSE failure flag (`hal_clk_fallback`), reset causes.

### 1.4 Standard preconditions (used in §3)

| Tag | Meaning |
|---|---|
| **B** | Twin booted from blank flash, lock-step, defaults, stream off, PC connected (GET_INFO done), no specimen, load offset 0. |
| **R** | "Ready": B + `safety.*` session values written + ENABLE + 500 ms settle + HOME completed + MOVE_ABS to x = 100 mm, stream on, VALID = 1. |
| **U** | B + ENABLE + settle, not homed. |
| **M(v)** | R + MOVE_ABS to 200 mm at v (default 10 mm/s) in cruise. |
| **J(v)** | R + JOG at v, refreshed every 100 ms. |
| **L1/L2/L3** | link variants for SYS-002: L1 connected + stream on, L2 connected + stream off, L3 PC silent after the last command (`safety.link_timeout_ms` = 5000 so the watchdog does not mask the stimulus). |

---

## 2. Test infrastructure and tooling (planned P2 files, owner E)

| Path | Content | MS |
|---|---|---|
| `02_FW/test/test_val_codec/`, `test_val_check/`, `test_val_names/` | vector replay incl. anti-skip counts; `proto_gen.h` codes = `ref_codec` tables | M1 |
| `02_FW/test/test_val_{units,ramp,stepgen,loadlim,rate,debounce,drvmon,latches,homing,motion,valid,flags,txsched,evq,sniff,nvm,rules,resetcause}/` | independent pure-module suites | M1/M2 |
| `02_FW/test/val_oracles/` | `ramp_ref.py` (R4 §1.5 exact sqrt ramp, fractional carry), `units_ref.py`, `loadlim_ref.py`, `nvm_ref.py` (record decoder), JSON outputs | M1/M2 |
| `02_FW/test/twin/` | pytest twin suites, `conftest.py` (scenario builder, edge-log assertions, `ref_codec` client) | M1/M2 |
| `02_FW/test/static/check_static.py` | map/flags/tags/origin/grep checks (independent of A's `check_map.py`) | M1 |
| `00_System/tools/hil/` | `hil_guard.py` (refuses to open a port without `--port` and `--po-approval`), `hil_link.py`, `hil_soak.py`, `hil_cmd_latency.py`, `hil_events.py`, `hil_capture.py` (LA/scope CSV → statistics), `hil_report.py` | HW gate |
| `02_FW/docs/FW_test_report_M1.md`, `…_M2.md`, `…_HG1.md` | reports with verdicts | per gate |

---

## 3. Test cases

Columns: **Lvl** level · **MS** milestone of the requirement (SRS) · **Pre** precondition (§1.4) · **Stimulus** · **Expected** (traced to the SRS acceptance criterion; "AC" = that criterion). Target parts of a criterion are referenced as HG-xx (§6) and stay open under D-06.

### 3.1 System (SYS-, FW side)

| TC | Lvl | MS | Pre | Stimulus | Expected (AC) |
|---|---|---|---|---|---|
| TC-SYS-001-01 | S | M2 | `01_HW` docs | Inspect `wiring.md`/`pinout.md` for every FW-connected signal of SRS §3.1 (PUL, DIR, ENA, ALM, PEND, DRV_PWR, DOUT, PD_SCK, RATE, START, END, E-stop sense, STOP, PAUSE, TRIP, LED, USART2) | every connection documented with pin, connector, level; FW-side inspection checklist complete (AC). HW completeness also HG-20. |
| TC-SYS-002-01 | T | M2 | L1, L2, L3 | Re-run the whole SAF-FW twin suite (§3.2) once per link variant | identical verdicts in L1/L2/L3 (AC "every SAF-FW test also passes with the PC disconnected and the stream off"); only LINK_WDG cases are excluded from L3. |
| TC-SYS-002-02 | S | M2 | source tree | grep/inspection of `src/**` for force scaling (K, B, N, kg, tare math) | no force scaling in FW (AC). |
| TC-SYS-003-01 | U | M1 | – | `um_to_steps`/`steps_to_um` with R4 TV-TC values + ±0.5-step ties, negatives, int32 ends | all equal the oracle, round half away from zero (AC). Source: `units_vectors.json` when OI-FW-20 lands, until then `val_oracles/units_ref.py`. |
| TC-SYS-004-01 | H | M2 | HG-25 | 0.01 and 10 mm/s moves, 30 mm/s unloaded move, re-home, soft-limit travel | AC on target (HG-25). |
| TC-SYS-004-02 | T | M2 | R, spm ∈ {100, 160, 800} | MOVE_ABS at 0.001 mm/s (short distance) and at the cap; 30 mm/s unloaded | accepted; period of 0.001 mm/s fits the 32-bit timer (no E_RANGE, no overflow); mean speed from the edge log within 1 %; envelope part of AC. |
| TC-SYS-005-01 | S | M2 | `params.yaml`, `src/**` | inspect units of every speed/accel parameter and command field | no speed/accel in step units (AC inspection); DIP sheet part → HG-19. |
| TC-SYS-006-01 | T | M2 | M(10) | E-stop open → close after 1 s → `drv_power` on again (RESET) | no PUL edge, motion_state stays NOT_ENABLED, ESTOP latched until ESTOP_CLEAR, no motion until ENABLE + HOME (AC "after RESET no axis motion until ENABLE + HOME"); power-removal part → HG-10/HG-20. |
| TC-SYS-007-01 | S | M1 | `pinout.md`, `include/board_pins.h`, `irq_prio.h` | compare pin, direction, level, EXTI line, NVIC level; 5 V tolerance column | every FW pin in `pinout.md` with direction/level/origin; no 5 V signal on PA4/PA5 (TC pins) (AC); code constants equal the document. |
| TC-SYS-008-01 | T+S | M1 | twin build | build `fw_twin.exe` from the unchanged `src/pure` + `src/core`; grep core/pure for register access (`->CR`, `GPIO`, `TIM2->` …) | twin builds without `#ifdef TWIN` in core/pure; no register access outside `hal/f446`; the validator twin suites run (AC "SW⇄twin integration tests pass" FW part). |
| TC-SYS-009-01 | H | M2 | PO-approved gate | execute the hardware-gate check list HG-01…HG-28 | check-list report with measured values, every §6 budget met (AC). |
| TC-SYS-010-01 | S | M1 | source + tests | grep origin notes in reused files (path @ commit); `Implements:`/`Verifies:` tags; traceability scan | every reused file has an origin note with commit hash; 100 % of in-scope Must requirements have ≥ 1 `Verifies:` test (AC). |
| TC-SYS-011-01 | H | M2 | HG-23 | inspection of the installed buffer board, opto current per input | AC (HG-23). |

### 3.2 Safety — firmware (SAF-FW-)

| TC | Lvl | MS | Pre | Stimulus | Expected (AC) |
|---|---|---|---|---|---|
| TC-SAF-FW-001-01 | T | M2 | M(10), J(10), homing | parametrised over every stop source of SRS §3.2 / ICD §6.2: PC STOP 0/1, HALT, STOP button, PAUSE button, PC PAUSE, START/END limit, both limits, load limit, rail sample, AFE stale, link watchdog, jog dead-man, step fault, homing failure, E-stop, DRV_PWR loss | ENA level unchanged (E-stop and DRV_PWR loss: disabled); next DATA MOVING = 0, VALID = 0 (dead-man: VALID unchanged); STOPPED(cause) then exactly one MOVE_DONE(STOPPED); afterwards the matching clear (+ ENABLE/HOME where needed): no PUL edge for 5 s (old move never resumed) (AC). |
| TC-SAF-FW-002-01 | T | M2 | M(v) v ∈ {1, 10, 30 mm/s}; `pulse_high_ns` ∈ {10 000, 100 000} | per immediate path, 100 random phases each: limit edge, STOP-button edge, load limit deciding sample, STOP 0 frame, HALT frame (last byte at random phase vs the 1 kHz tick, main loop loaded with 500 µs handler time), AFE fault, step fault, HOME_WIRING | stop issued in the detecting context; last PUL edge − trigger ≤ 200 µs (inputs, load sample) / ≤ 2 ms (STOP/HALT last byte); max over trials reported (AC twin part); target part HG-11/12/13. |
| TC-SAF-FW-002-02 | U | M2 | `stop_sniff` | STOP/HALT/PAUSE frames whole, split at every byte across two ticks, bad CRC, STOP mode 2, wrong LEN, sync in a payload, other TYPEs, **RESUME (0x3C) frame** | hits exactly for CRC-valid STOP(mode ≤ 1)/HALT/PAUSE; no hit for RESUME, STOP mode 2, bad CRC (supports AC of SAF-FW-002/003). |
| TC-SAF-FW-002-03 | T | M2 | R, `motion.a_max_um_s2` = 10 000 000, spm ∈ {800, 100 000} | one RX burst MOVE_ABS + HALT; dispatch budget forced to split between the two frames | HALT effective: last PUL edge ≤ 2 ms after the HALT last byte; number of emitted pulses reported (FW_design §5.9.3 claims 0 — see DEF-P1-04). |
| TC-SAF-FW-003-01 | T | M2 | M(v), J(v) for v giving step period P ≤ 1 ms, 1 ms < P ≤ 2 ms, P > 2 ms | each controlled source: link watchdog, PAUSE button, PC PAUSE, jog dead-man, STOP 1, JOG 0 | deceleration **committed** (seam log: `set_period_now`/preload of c_dec1) ≤ 2 ms after the trigger; every PUL interval after the commit ≥ the previous one and within ±1 tick of the planned profile; stop distance = v²/(2·a_stop) ± 1 step; then SAF-FW-001 state (AC; measurement definition per OBS-P1-01 / OI-FW-24). |
| TC-SAF-FW-003-02 | U+T | M2 | grid P × d: spm ∈ {100, 160, 100 000}, a_stop ∈ {10 000, 1 000 000} µm/s² | controlled stop at P just below/above 2 ms | **D-30**: CLEAN halt only when P > 2 ms **and** d ≤ 1 step: no PUL edge > 2 ms after the trigger, no runt, distance < 1 step, POS_UNCERTAIN not set, MS → IDLE without STOPPING, same STOPPED cause, MOVE_DONE STOPPED/JOG_ZERO; otherwise planned deceleration as TC-003-01 (U: `ctrl_stop_path`, `stepgen_stretch_decide`). |
| TC-SAF-FW-004-01 | T | M2 | M(v), J(v) random v | 10 000 random stops (random source and phase, incl. the 0.5 µs guard window before a pulse) | PUL rising edges in the edge log = Δ`pos_steps` exactly; ±1 only together with POS_UNCERTAIN (TRUNCATE paths); no pulse shorter than `pulse_high_ns` (AC twin). Target → HG-09. |
| TC-SAF-FW-004-02 | U | M2 | `stepgen_halt_decide` | every CNT ∈ [0, ARR] for PW ∈ {225, 900, 9000} ticks | OPM path iff CNT ≥ CCR1 − guard, else force-inactive; "pulse in flight" flag correct (no runt, no uncounted pulse). |
| TC-SAF-FW-004-03 | T | M2 | M(30) | inject a missed step update (`inject step_fault`) | STEP_FAULT latched, immediate stop, HOMED = 0, POS_UNCERTAIN = 1, FAULT_SET(2) (AC "missed timer update raises STEP_FAULT"). |
| TC-SAF-FW-005-01 | T | M2 | parametrised states: NOT_ENABLED, ENABLING, IDLE, MOVE_ABS, JOG, each HOMING phase, STOPPING, stream off, PC silent, NVM SAVE in progress | E-stop sense opens | `hal_step_abort` in the same virtual event as the edge (≤ 100 µs); ENA disabled ≤ 1 ms; ESTOP latched, VALID = 0, HOMED = 0, move discarded; next DATA ESTOP = 1 (AC twin; during SAVE the latch/event is delivered after the flash op, the HAL reaction is immediate). Target → HG-10, HG-04. |
| TC-SAF-FW-006-01 | T | M2 | after an E-stop | ESTOP_CLEAR with the input open, closed 50 ms, 99 ms, 100 ms; bounce during release; then JOG; ENABLE with DRV_PWR off; power on + ENABLE + settle + MOVE_ABS | E_CAUSE_ACTIVE 0xFFFF / ms missing; accepted at ≥ 100 ms only, bounce restarts the count; JOG → E_STATE NOT_ENABLED; ENABLE → E_STATE DRV_UNPOWERED; MOVE_ABS → E_STATE NOT_HOMED (AC). |
| TC-SAF-FW-007-01 | S | M2 | `params.yaml`, `params_gen.h` | search for polarity parameters of E-stop, START, END, DRV_PWR | none exist (AC inspection). |
| TC-SAF-FW-007-02 | T | M2 | R and M(10) | broken wire on E-stop sense, START, END, STOP (NC), DRV_PWR aux | reads active / unpowered → the corresponding stop + flag/latch (AC). PAUSE (NO) wire break is undetectable by design (OBS-P1-11). |
| TC-SAF-FW-007-03 | T | M2 | boot | boot with each of: E-stop open, START open, END open, both, STOP open, DRV_PWR off; and with `drv.pwr_sense_enable` = false (SET + SAVE + REBOOT) | first GET_STATUS shows the latch/inputs; motion refused with the expected BLOCK bits; boot ENA disabled when the E-stop is open (and per OI-FW-22 when DRV_PWR is off); sense disabled → DRV_PWR = 1 (AC). |
| TC-SAF-FW-008-01 | T | M2 | NOT_ENABLED idle, un-homed JOG, each HOMING phase, MOVE_ABS, stream off, PC silent | inject raw_max + 1 (and raw_min − 1); raw_max exactly; `load_trip_samples` = 2 with consecutive vs alternating violations | trip on that sample (2nd consecutive for 2), no trip at the threshold or alternating; LOAD_LIMIT latched, VALID = 0, stop in every state (AC). |
| TC-SAF-FW-008-02 | U | M2 | `loadlim` | threshold, trip-sample counter, rails, regrow after clear, thresholds changed between samples | equals `val_oracles/loadlim_ref.py`. |
| TC-SAF-FW-008-03 | S | M2 | params, call graph | inspect for any parameter/command/build flag that disables the check; `loadlim_check` called unconditionally in the sample ISR | none (AC inspection). |
| TC-SAF-FW-009-01 | U+T | M2 | thresholds at the range caps ±7 151 121 | samples 0x7FFFFF, 0x800000 (−8 388 608) | trip with LOAD_LIMIT, DS_AFE_SATURATED in that frame (AC). |
| TC-SAF-FW-010-01 | U+T | M2 | B; M(10) | check vectors `set_safety.load_raw_*` (min/max/below/above/type/padding/moving), H2; SET 7 151 122 / 7 151 121; SET while moving; boot, DEFAULT_PARAMS, LOAD_PARAMS, SAVE + REBOOT | E_RANGE detail = id, 7 151 121 read back; while moving accepted and effective from the next sample; boot/DEFAULTS → ±7 022 271; LOAD keeps the session values; after SAVE + REBOOT defaults (record holds no session entry) (AC). |
| TC-SAF-FW-011-01 | T | M2 | LOAD_LIMIT latched with load beyond threshold (spring specimen) | FAULT_CLEAR; move reducing the load; move increasing it by ≤ regrow and by > regrow | clear accepted; unloading move runs; re-trip only beyond `safety.load_regrow_raw` (AC). |
| TC-SAF-FW-012-01 | T | M2 | J(10) | DOUT stall; motion commands while stale; saturated last sample; samples resume; FAULT_CLEAR before/after | stop ≤ `afe.timeout_ms` + 1 ms after the last sample, AFE_FAULT latched; motion → E_STATE AFE_STALE / AFE_SATURATED; FAULT_CLEAR refused until a fresh sample (AC). |
| TC-SAF-FW-012-02 | T | M2 | R, `afe.rate_sps` = SPS10, `afe.timeout_ms` default | 10 min idle + 60 s jog at the true 10 SPS rate (± 0.5 % jitter) | **no** AFE_STALE / AFE_FAULT. Expected to fail against the current dictionary (DEF-P1-01). |
| TC-SAF-FW-013-01 | T | M2 | M(10), J(10), MOVE_UNTIL_LOAD (M4) toward START/END, with bounce | hit; JOG toward / away; release for 19 ms and ≥ 20 ms; release without moving away | immediate stop, LIMIT_x latch, LIMIT_SET, VALID = 0; toward → E_STATE LIMIT, away → runs; latch clears only after ≥ 20 ms release **and** ≥ 1 step on the away side (AC; OBS-P1-06). |
| TC-SAF-FW-014-01 | T | M2 | R | both limits active; FAULT_CLEAR while active, with one released, both released | LIMIT_WIRING, motion E_STATE FAULT; FAULT_CLEAR E_CAUSE_ACTIVE until both released, then OK (AC). |
| TC-SAF-FW-015-01 | T | M2 | M(10), J(10), HOMING; `link_timeout_ms` ∈ {200, 1000, 5000} | command silence of timeout − 1 ms and timeout; then a NACKed command, a bad-CRC frame, an invalid-TYPE frame | no stop at timeout − 1 ms; deceleration committed ≤ timeout + 2 ms; LINK_WDG, VALID = 0, EVENT; bad-CRC/invalid TYPE do not refresh or clear; any valid command frame clears LINK_WDG (LINK_RESTORED), nothing restarts; idle silence never trips (AC). |
| TC-SAF-FW-016-01 | T | M2 | J(10); `jog_timeout_ms` ∈ {50, 250, 1000} | refresh every 200 ms for 10 s; stop refreshing | keeps moving; deceleration committed ≤ timeout + 2 ms after the last refresh; STOPPED JOG_DEADMAN, VALID unchanged (AC). |
| TC-SAF-FW-017-01 | T | M2 | R idle; `zero_raw` shifted; `idle_disable_s` ∈ {0, 600} | unloaded; loaded ≥ band; a move at 300 s | unloaded: ENA disabled at 600 s ± 1 s, HOMED = 0, DRIVER_DISABLED(2); loaded: still enabled after 3600 s; a move restarts the count; 0 = never; STATUS `idle_disable_left_s` consistent (AC). |
| TC-SAF-FW-017-02 | T | M2 | R idle, unloaded | AFE stalls at t = 300 s | no idle disable while stale (design rule FW_design §5.7.1; SRS silent → OBS-P1-05). |
| TC-SAF-FW-018-01 | T | M2 | reset causes pin, power, IWDG, software during M(10) | reset | no PUL edge from reset release; ENA not driven to disabled (unless E-stop open); STATUS NOT_ENABLED, HOMED = 0, reset cause, stream off, EVENT BOOT; JOG before ENABLE → E_STATE NOT_ENABLED (AC twin). Target → HG-15 (DEF-P1-05). |
| TC-SAF-FW-019-01 | T | M2 | M(10) | hang in the main loop; hang in the tick; level-1 ISR storm; LSI 17 and 47 kHz | no kick → IWDG reset ≤ 90 ms → PUL stops ≤ 100 ms after the hang; next boot reset cause IWDG (AC twin). Target → HG-14. |
| TC-SAF-FW-019-02 | U+T | M2 | – | kick rule (tick advanced since last kick); SAVE request while moving | kick only when the tick advanced; long window only inside an idle NVM operation (SAVE while moving → E_BUSY) (AC "extended only while idle"). |
| TC-SAF-FW-020-01 | U | M2 | `check_vectors.json` | every motion vector (one per refusal reason: ESTOP, HALT, FAULT, NOT_ENABLED, NOT_HOMED, LIMIT, AFE_STALE, AFE_SATURATED, DRV_UNPOWERED, DRIVER_ALARM, **PAUSED**, E_RANGE target/bound/speed/accel, loaded speed cap, step-rate cap, E_BUSY) | STATUS, detail and NACK bytes identical, no side effect (AC "command-check vectors, one per refusal reason"). |
| TC-SAF-FW-020-02 | T | M2 | random walk | 10 000-step random command/world walk; before each command the twin state is mapped to `FwState` and `ref_cmdcheck` predicts the verdict | live FW verdict = oracle verdict for every step (differential, catches context-filling errors the snapshot vectors cannot). |
| TC-SAF-FW-020-03 | S | M2 | `protocol.yaml`, ICD §3.2 | inspection | no relative-move command (AC inspection). |
| TC-SAF-FW-021-01 | T | M2 | U, load offsets 4.9 %, 5 %, 6 % FS from `zero_raw` | HOME without/with the confirmed flag; load trip during each homing phase | 6 % → E_CONFIRM; with flag runs; boundary per `>` rule; trip → stop, LOAD_LIMIT, HOME_FAILED ABORTED (AC). |
| TC-SAF-FW-022-01 | T | M2 | M(10) L1 and L3; `io.stop_active_level` both values | STOP button press with bounce; HALT_CLEAR while pressed, 10 ms and ≥ 20 ms after release | stop in the detecting context (≤ 200 µs), HALT(src BUTTON), STOP_BUTTON(1), HALT_SET, STOPPED; HALT_CLEAR E_CAUSE_ACTIVE 0xFFFF / ms missing, then OK; works without the PC; polarity parameter inverts detection (AC). Target → HG-11. |
| TC-SAF-FW-023-01 | T | M2 | M(10), J(10), idle | PAUSE button and PC PAUSE; second press; PAUSE while PAUSED; while PAUSED: MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0, JOG 0, STOP, HALT, ENABLE, DISABLE, ESTOP_CLEAR, FAULT_CLEAR, SET_PARAM, **RESUME**, HALT_CLEAR | controlled stop, PAUSED(src), EVENT PAUSED once, VALID = 0; second press → RESUME_REQUEST only, no pulse; motion starts → E_STATE **PAUSED** and PAUSED kept (D-30); other commands accepted without clearing PAUSED; **RESUME** → PAUSED cleared, PAUSE_CLEARED, no pulse (D-31); HALT_CLEAR → PAUSED cleared; PAUSE while idle also sets PAUSED; nothing restarts motion (AC as amended by D-30/D-31). |
| TC-SAF-FW-023-02 | T | M2 | M(10), J(10) | **race (D-30)**: PAUSE button at t; a JOG refresh / MOVE_ABS frame whose last byte arrives at t + k ms, k = −1…5 in 0.5 ms steps; PC PAUSE followed in the same burst by MOVE_ABS | no motion restarts in any case: the late command gets E_STATE PAUSED (or E_BUSY while STOPPING); no PUL edge after the controlled stop ends. |
| TC-SAF-FW-023-03 | T+U | M2 | U: `check_vectors` RESUME cases; T: R | **D-31**: (a) PAUSED → RESUME; (b) PAUSED + HALT(button) latched ms before the RESUME frame → RESUME; (c) HALT + PAUSED → HALT_CLEAR; (d) PAUSED + ESTOP latched / + any FAULT → RESUME; (e) RESUME with nothing latched | (a) OK, only PAUSED cleared; (b) E_STATE (BLOCK HALT), **HALT and PAUSED both stay**; (c) both cleared (HALT_CLEARED, PAUSE_CLEARED); (d) E_STATE with ESTOP / FAULT bit, PAUSED stays; (e) OK no-op (pending ICD v0.4, OBS-P1-15); no clear or RESUME ever produces a PUL edge. |
| TC-SAF-FW-024-01 | T | M2 | J(10), M(10), HOMING, ENABLING, idle; E-stop closed | DRV_PWR off; toggles of 5, 19 ms; ENABLE/motion while off; power on at t_p and ENABLE at t_e (both orders) | stop + ENA disabled + NOT_ENABLED + HOMED = 0 + VALID = 0 ≤ 25 ms after the input change; EVENTs DRIVER_POWER(0), STOPPED(DRV_POWER_LOST) if moving, DRIVER_DISABLED(4) (ICD §6.2 rule, OBS-P1-03); short toggles ignored; ENABLE → E_STATE DRV_UNPOWERED; first PUL/DIR edge ≥ max(t_p, t_e) + 500 ms (AC twin). Target → HG-21. |
| TC-SAF-FW-024-02 | T | M2 | idle, SAVE with sector erase in progress | DRV_PWR off during the flash operation | reaction time reported; expected per SRS ≤ 25 ms — currently not met by design (DEF-P1-06). |
| TC-SAF-FW-025-01 | T | M2 | R; `drv.k1_weld_ms` ∈ {100, 200, 2000} | E-stop open with DRV_PWR held on; normal case: power drops 60 ms after the E-stop; FAULT_CLEAR while open + powered, after power off, after E-stop closed; sense disabled | no fault at 150 ms (k1 = 200); fault in (k1, k1 + 2 ms] after the E-stop edge with FAULT_SET(6, ms) (OBS-P1-02); normal case no fault, only DRIVER_POWER(0); FAULT_CLEAR E_CAUSE_ACTIVE while the cause persists, accepted after it is gone; sense disabled → never (AC). Target → HG-22. |
| TC-SAF-FW-026-01 | U+T | M2 | R; J(10) | ALM active + power on: MOVE_ABS, MOVE_UNTIL_LOAD, HOME, JOG ≠ 0 (new), ENABLE, STOP, HALT, PAUSE, JOG 0, clears; ALM during a MOVE_ABS; ALM during a jog then speed refreshes; ALM + DRV_PWR off; sense disabled | new starts → E_STATE DRIVER_ALARM; others accepted; move completes (MOVE_DONE TARGET) with ALM_CHANGED; jog refreshes still accepted; power off → DRV_UNPOWERED (not DRIVER_ALARM); sense disabled → DRIVER_ALARM (AC). Target → HG-16. |

### 3.3 Firmware (FW-)

| TC | Lvl | MS | Pre | Stimulus | Expected (AC) |
|---|---|---|---|---|---|
| TC-FW-PLT-001-01 | S | M1 | clean checkout | `pio run -d 02_FW -e nucleo_f446re` and `_debug`; inspect `platformio.ini`; map handler slots; `board_pins.h` vs `pinout.md` | build SUCCESS with flash/RAM figures; flags of FW_design §2.2 present; own IRQ handlers in their vector slots, no core HardwareSerial/HardwareTimer; pin init = pinout (AC). |
| TC-FW-PLT-002-01 | S | M1 | `hal/f446/clock.c`, init code | inspection | constants derived from `HAL_RCC_Get*Freq()`/`SystemCoreClock`, no hard-coded Stefan values; bounded HSE/PLL waits (AC inspection). |
| TC-FW-PLT-002-02 | T | M1 | B with `hal_clk_fallback()` = 1 | boot | STATUS `sys_flags.CLK_FALLBACK` = 1, EVENT CLK_FALLBACK at boot, no DATA bit (AC twin). Target PUL frequency → HG-02. |
| TC-FW-AFE-001-01 | U | M2 | `hx711_seq`, `hx711_math` | bit sequences for 0, 1, −1, 0x7FFFFF, 0x800000, random; pulse counts 25/27/26 | sign extension and pulse counts exact; origin note present (AC host part). Target → HG-05. |
| TC-FW-AFE-002-01 | T | M2 | R | SET `afe.gain_channel` A64/B32/A128, SET `afe.rate_sps` SPS10/SPS80 (idle), DEFAULT_PARAMS | next read uses 27/26/25 pulses (model observation); RATE output level follows; no reboot; DEFAULTS → A128/80 SPS (AC). |
| TC-FW-AFE-003-01 | T | M2 | boot; R | boot; gain change; rate change; `afe.settle_discard` ∈ {0, 4, 20}; rail sample during settling; SCK-high overrun / power-down symptom | first N samples flagged AFE_SETTLING and still load-checked (rail trips); overrun → re-init, `afe_reinit_count` + 1, EVENT AFE_REINIT (AC). |
| TC-FW-AFE-004-01 | U | M2 | `afe_rate` | Δt series incl. outliers, 2³² wrap, 80/10 SPS | median of 16 periods, 0.1 SPS resolution, mismatch flag at > `rate_tol_pct` (oracle). |
| TC-FW-AFE-004-02 | T | M2 | R | model at 10 SPS with 80 configured; 80 SPS ± 2 % for 10 min; tolerance boundary 20 %; recovery | flag + EVENT(1) ≤ 1 s; no flag at ± 2 %; EVENT(0) on recovery; STATUS rate reported (AC). Target → HG-05. |
| TC-FW-AFE-005-01 | T | M2 | M(30); twin start time 2³² − 5 s | samples across the t_us wrap | DATA `t_us` = virtual data-ready ± 2 µs; `setpoint_um` = steps at that instant converted (round half away) (AC). |
| TC-FW-MOT-001-01 | U | M2 | pulse-timing derivation | params at range ends and the H3 boundary, f_tick 90 MHz | PW and c_min per FW_design §5.6.1; H3 violations rejected (E_CONFIG). |
| TC-FW-MOT-001-02 | T | M2 | M(v) at the 50 kHz cap; reversals | edge log; SET `pul_invert` / `ena_invert` (reboot-required) then SAVE + REBOOT; SET `dir_invert` | high = `pulse_high_ns` ± 1 tick, low ≥ `pulse_low_min_ns`, DIR setup ≥ `dir_setup_us` on every reversal, DIR never changes while running; PUL/ENA polarity unchanged until reboot with REBOOT_PENDING = 1 (AC twin). Target → HG-08. |
| TC-FW-MOT-002-01 | U+T | M2 | spm ∈ {100, 160, 800, 100 000} | positions after moves; `um_to_steps`/`steps_to_um` vectors | reported µm = steps·1000/spm round half away; counter changes only per completed pulse (edge log) (AC). |
| TC-FW-MOT-003-01 | U | M2 | `ramp`, `planner` | R4 TV-M: 5.2 s trapezoid (468 000 000 ticks), triangle, asymmetric triangle | every period ± 1 tick, sum ± N/1000 ticks (AC). |
| TC-FW-MOT-003-02 | U | M2 | `ramp` | 1000 random (v, a_acc, a_dec, N) incl. on-the-fly speed changes and controlled stops (virtual index) | equals `val_oracles/ramp_ref.py` within the same tolerances; no step lost on a speed change (independent oracle). |
| TC-FW-MOT-004-01 | T | M2 | R | MOVE_ABS 10 mm at 5 mm/s; second MOVE_ABS while moving; target = current position | exact final step count, MOVE_DONE(TARGET, value), duration = planner ± 1 ms; E_BUSY 1; zero-length → MOVE_DONE at once, no pulse (AC). |
| TC-FW-MOT-005-01 | T | M2 | R, U | jog to each soft limit; bound 0 (real position, soft_min ≤ 0); bound = 0x80000000; bound behind/outside; bound un-homed; un-homed jog beyond `home.max_travel_um`; speed change and reversal on the fly; JOG 0 in every state incl. ESTOP | never passes a soft limit / bound / un-homed travel bound (world truth); bound 0 honoured; E_RANGE 8 / E_STATE NOT_HOMED as specified; speed changes ramp without step loss; reversal decelerates to 0 with DIR setup; JOG 0 never refused; MOVE_DONE BOUND/SOFT_LIMIT/JOG_ZERO (AC). |
| TC-FW-MOT-006-01 | T | **M4** | R with spring specimen | MOVE_UNTIL_LOAD with `cmp` 0 and 1; threshold already beyond; bound before threshold; speed above `v_max_load_um_s`; AFE stall during the move | stop on the first sample past the threshold with the load-path timing; already beyond → MOVE_DONE LOAD_THRESHOLD, no pulse; bound → MOVE_DONE BOUND; E_RANGE on speed; stale → AFE_FAULT (fallback frames are not samples) (AC). |
| TC-FW-MOT-007-01 | T | M2 | every motion state × latch combination | STOP 0/1, HALT ×20, PAUSE, while idle and moving | STOP not latched; HALT latch src PC, one HALT_SET for 20 HALTs, all OK; motion → E_STATE HALT until HALT_CLEAR; STOP/HALT/PAUSE accepted in every state; STOP while idle clears VALID (AC). |
| TC-FW-MOT-008-01 | T | M2 | NOT_ENABLED; `ena_settle_ms` ∈ {0, 500, 2000} | ENABLE; motion during settle; ENABLE with E-stop open/latched, with DRV_PWR off; DISABLE while moving / idle | ENA enabled level at once, ENABLING, motion → E_BUSY 2, first PUL/DIR edge ≥ settle after ENABLE and after DRV_PWR return; response = remaining ms; E_STATE ESTOP / DRV_UNPOWERED; DISABLE moving → E_BUSY 1; idle → ENA disabled, HOMED = 0, DRIVER_DISABLED(1) (AC). Target → HG-07. |
| TC-FW-MOT-009-01 | U+T | M2 | vectors `set_motion.steps_per_mm_*`, `spm_*`; R | SET 99, 100, 10 000, 100 000; SET while moving; after idle SET read positions; at spm 10 000 MOVE_ABS 10 mm/s | E_RANGE / OK / E_BUSY 1; HOMED kept, µm = steps·1000/spm; 10 mm/s → E_RANGE (offset 4), STATUS `v_limit_um_s` = 5000, v_max params unchanged (AC). |
| TC-FW-HOM-001-01 | T | M2 | U; START bounce 2 ms; START active at start | HOME at default and range-end speeds | phase sequence RELEASE → FAST_SEEK → BACKOFF → SLOW_APPROACH → MOVE_TO_ZERO in `home_phase`; HOMED, final x = 0, START edge at −`home.offset_um` ± 1 step (world truth); EVENT HOMED(0), MOVE_DONE TARGET; expected START edges do not set LIMIT_START; POS_UNCERTAIN cleared (AC). |
| TC-FW-HOM-002-01 | T | M2 | U | START switch missing; DIR inverted / END reached; START stuck (not released within `HOME_RELEASE_MAX_UM`); STOP, HALT, PAUSE, E-stop, load trip, link watchdog in each phase | HOME_NOT_FOUND / HOME_WIRING faults; abort → HOME_FAILED ABORTED without a homing fault, FAULT_CLEAR finds nothing homing-related; HOMED = 0 in every case; MOVE_DONE STOPPED (AC). |
| TC-FW-HOM-003-01 | H | M2 | HG-26 | 10 homing cycles with a dial indicator | ≤ 0.02 mm (2σ) (AC, Should). |
| TC-FW-HOM-004-01 | T | M2 | R | inject lost steps of 0.1 mm, `drift_tol_um` ± 1 step, 0.3 mm; first homing after boot | 0.1 mm: no fault, HOMED value ≈ 100 µm; 0.3 mm: HOME_DRIFT (FAULT_SET(7, deviation)), HOMED set, motion E_STATE FAULT until FAULT_CLEAR (clears at once); first homing value 0 (AC). |
| TC-FW-SW-001-01 | U+T | M2 | M(10); `io.release_ms` ∈ {5, 20, 200} | hit with 5 ms bounce burst; release with bounce; stable 19 / 20 ms | one stop, one LIMIT_SET; release reported only after the stable time (AC). |
| TC-FW-SW-002-01 | S+T | M2 | `irq_prio.h`, NVIC init, `crit.h` | inspection; twin release with bounce | EXTI15_10 alone at level 0; no BASEPRI section masks level 0 (only `CRIT_HALT` = PRIMASK, ≤ 0.2 µs); RC τ ≤ 10 µs in pinout; release debounced ≥ `io.estop_release_ms` (AC). Target → HG-18. |
| TC-FW-SW-003-01 | T+S | M2 | R | STOP/PAUSE presses with bounce; polarity parameters both values; DATA/STATUS bits | one EVENT per press (and release); polarity inverts detection without a spurious press; STOP_BTN/PAUSE_BTN bits and IO mask; EXTI level 1 (AC). |
| TC-FW-SW-004-01 | T+S | M2 | M(10) | ALM toggles; polarity parameters; PEND withheld after a move; `drv.pend_timeout_ms` = 0; ALM chatter 1 kHz for 1 s | ALM bit + ALM_CHANGED, motion continues; NOT_SETTLED after the timeout (value = elapsed), none with 0; inspection: no stop path from ALM; chatter does not cause STEP_FAULT or AFE timestamp errors (OBS-P1-10) (AC). |
| TC-FW-SW-005-01 | T | M2 | R | DRV_PWR toggles of 5, 19, 21 ms; changes; sense disabled after SAVE + REBOOT | toggles < 20 ms ignored; each accepted change → DS/IO bit + DRIVER_POWER; sense disabled → DRV_PWR = 1, no events (AC). Target → HG-21. |
| TC-FW-CFG-001-01 | S | M1 | repo | `gen_params.py --check`; grep `02_FW/src` for parameter ids/keys/tables outside `gen/`; diff `gen/*` vs generator output | `--check` exit 0; no hand-written table; generated files untouched (AC). |
| TC-FW-CFG-002-01 | U+T | M1 | vectors; B with 20 random SETs | GET_ALL_PARAMS pages 0…page_count−1 and page_count | page frames byte-identical to vectors at defaults; every entry exactly once, ascending, current value; page_count → E_RANGE (AC). |
| TC-FW-CFG-003-01 | U | M1 | `check_vectors.json` | every `set_*` (default/min/max/below/above/NaN/type/padding/moving) and `rule_h1…h4` vector | STATUS/detail/NACK bytes identical (AC "vectors per parameter type"). |
| TC-FW-CFG-003-02 | T | M1 | B | random SET sequence; read back; flash write counter; SET `motion.ena_invert`, `motion.pul_invert`, `drv.pwr_sense_enable` | response = GET_PARAM; 0 flash writes; reboot-required: old behaviour kept, REBOOT_PENDING = 1 until SAVE + REBOOT; others effective ≤ 10 ms (AC). |
| TC-FW-CFG-004-01 | U+T | M1 | B | GET_INFO | proto 1.0, payload 1, FW version/build id = build constants, hash = `PARAM_DICT_HASH`, `param_count`, UID from the seam, feature mask (M1: AFE_SYNTHETIC + NVM + TWIN in the twin) (AC). |
| TC-FW-NVM-001-01 | T | M1 | B | SET → GET_STATUS; SAVE; REBOOT; DEFAULT_PARAMS; LOAD_PARAMS; SET of a session parameter | CFG_DIRTY 1 → 0 after SAVE (PARAMS_SAVED); values restored after reboot; DEFAULTS → defaults + dirty; LOAD → record values, session values kept; session SET never dirties (AC + ICD §11.2). |
| TC-FW-NVM-002-01 | U | M1 | `nvm_log` | slot selection, highest seq, CRC-bad skip, sector full → erase other, migration by id (retired 0x0401 dropped, new 0x0705 default, type change → default, out of range → default), hard-rule violation → defaults, seq wrap | equals `val_oracles/nvm_ref.py`. |
| TC-FW-NVM-002-02 | T | M1 | B | power cut after every program word and at erase start/middle, over ≥ 3 consecutive SAVEs including a sector switch; blank flash; changed hash | after every cut the boot finds a complete valid record (old or new, never mixed); blank → defaults + NVM_DEFAULTED + PARAMS_DEFAULTED(1); hash change → PARAMS_DEFAULTED(3); session parameters never in a record (decoded) (AC). |
| TC-FW-NVM-002-03 | A | M1 | FW_design §5.11 | endurance computation (slots × sectors × 10 000 cycles) | ≥ 10 000 saves (AC analysis). |
| TC-FW-NVM-003-01 | T | M1 | M(10); idle with a TX backlog (GET_ALL_PARAMS pages + events) | SAVE/LOAD/DEFAULT while moving; SAVE while idle | E_BUSY 1 while moving; wire log: no frame split around the flash operation (operation starts only after TX idle); samples during the stall → OVERRUN + `frame_seq` gap (AC twin). Target → HG-04. |
| TC-FW-CMD-001-01 | U | M1 | `protocol_vectors.json` | every frame, the 11 streams, check-order vectors (unknown TYPE, wrong LEN) | byte-identical encode/decode; frames and counters equal (AC "shared protocol vectors pass"). |
| TC-FW-CMD-001-02 | T | M1 | B and R | fuzz: 10 000 frames mixing valid commands, invalid TYPE, wrong LEN, bad CRC, noise; flood with > 4 outstanding commands | exactly one response per CRC-valid 0x01…0x3F command echoing its SEQ; invalid TYPE → no response, `rx_frame_errors` + 1; NACK has no side effect (STATUS snapshot identical except counters/link age); no lost response under flood (DEF-P1-07). |
| TC-FW-CMD-002-01 | U+T | M1 | B stream on; start time near 2³² | SET_VALID 1/0; stream off; boot | frames with `t_us` ≥ returned t carry the new value, earlier the old one (modular across the wrap); stored with the stream off; VALID = 0 at boot (AC). |
| TC-FW-CMD-003-01 | T | M2 | parametrised over LOAD_LIMIT, AFE_FAULT, STEP_FAULT, LIMIT_WIRING, HOME_NOT_FOUND, HOME_WIRING, HOME_DRIFT, K1_WELDED; also two faults latched | FAULT_CLEAR with the cause present / gone | cause present → E_CAUSE_ACTIVE with the mask, **nothing** cleared (also the clearable one); gone → OK body = mask, FAULT_CLEARED; LOAD_LIMIT always clearable; ESTOP/HALT/PAUSED untouched (AC "one test per fault"). |
| TC-FW-CMD-004-01 | U+T | M1 | B | injection per counter: CRC error, LEN > 160, inter-byte timeout, invalid TYPE, RX ring overflow, TX congestion, event overflow, resets, AFE re-init, latches, inputs, motion | each STATUS counter/field increments or changes exactly under its condition; STATUS frame encode = vector (AC). |
| TC-FW-STR-001-01 | T | M1 | B | boot; STREAM_START ×2, STREAM_STOP ×2 | no DATA after boot; idempotent; `frame_seq` not reset; SAF suite with the stream off = TC-SYS-002-01 (AC). |
| TC-FW-STR-002-01 | T | M1 | B stream on (M1 synthetic; M2 HX711 model) | 10 min at 80 Hz; concurrent GET_ALL_PARAMS polling (168 B frames) + 20 commands/s | DATA count = conversions; DRDY → first DATA byte ≤ 2 ms with an idle line, ≤ 2 ms + wire time of the frame in transmission otherwise (max reported) (AC). |
| TC-FW-STR-003-01 | U+T | M1 | DATA vectors; R | DATA "all bits" vectors; table-driven: provoke each of the 24 flags/status bits | encode byte-identical; each bit set exactly under its condition and cleared accordingly (AC). |
| TC-FW-STR-004-01 | T | M1 | R stream on | TX congestion dropping k ∈ {1, 5, 50} frames; NVM stall | `frame_seq` gap = k, OVERRUN only on the next sent frame, `tx_drops` + k (AC). |
| TC-FW-STR-005-01 | T | M2 | R; `stream.fallback_hz` ∈ {1, 10, 80} | DOUT silent 10 s, then resume | fallback frames at the rate ± 1 frame/s with NO_AFE_DATA + AFE_STALE, raw 0x80000000; stop at resume (AC, Should). |
| TC-FW-STR-006-01 | T | M2 | scenario per code | trigger each EVENT code 1…34 (+ PAUSE_CLEARED reasons incl. RESUME per ICD v0.4); burst > queue depth | each EVENT with correct code/arg/value and `t_us` (= trigger, ± 1 ms for tick-detected causes); EVENT SEQ +1 incl. drops; overflow counted; depth ≥ 8 (AC). |
| TC-FW-TIM-001-01 | T | M2 | M(50 kHz) | data-ready at the same virtual instant as level 0–2 events; t_us across the wrap | DATA/EVENT/SET_VALID time base monotonic modulo 2³²; timestamp latency ≤ 5 µs (twin model) (AC twin). Target → HG-05. |
| TC-FW-PAR-001-01 | S | M1 | SRS Table 5.1, `params.yaml`, generated C/Python | `check_static.py` table diff (AFE group) | ranges ⊇ listed, defaults equal, present in generated outputs (AC). |
| TC-FW-PAR-002-01 | S | M1 | as above (mechanics/pulse timing) | same; reboot-required flags on PUL/ENA polarity | equal; ENA settle default 500 (AC). |
| TC-FW-PAR-003-01 | S | M1 | as above (speeds/accels) | same; units check | equal; H4 present; no step-unit speed (AC). |
| TC-FW-PAR-004-01 | S+U | M1 | as above (travel/homing) | same; H1 vectors | equal; no home-switch parameter; `rule_h1` vectors pass (AC "I, T"). |
| TC-FW-PAR-005-01 | S | M1 | as above (safety) | same; `nvm: false` on the three session parameters | equal (AC). |
| TC-FW-PAR-006-01 | S | M1 | as above (I/O, drv, stream) | same; search for E-stop/limit/DRV_PWR polarity parameters | equal; none of the forbidden polarity parameters (AC). |

### 3.4 Interface (IF-, FW side)

| TC | Lvl | MS | Pre | Stimulus | Expected (AC) |
|---|---|---|---|---|---|
| TC-IF-001-01 | S | M1 | ICD, `src/**` | grep for literal TYPE/STATUS/EVENT/bit values outside `gen/`; ICD version + history | every code used comes from `proto_gen.h`; ICD versioned with history (AC). |
| TC-IF-002-01 | U+S | M1 | `le.h`, BRR computation | LE vectors; BRR from PCLK1 45 MHz | little-endian round trip; 918 367 Bd (−0.35 %) (AC host part). Target soak → HG-03. |
| TC-IF-003-01 | U | M1 | parser | 11 `streams` vectors + validator corpus of 10⁵ random streams with expectations generated by `ref_codec.FrameParser` | frames and counters identical (AC + differential). |
| TC-IF-004-01 | U+T | M1 | CRC | `crc16` vectors ("123456789" → 0x29B1, empty → 0xFFFF); corrupted command frames in the twin | equal; corrupted frames: no action, `rx_crc_errors` + 1 (AC). |
| TC-IF-005-01 | T | M1 | R | same MOVE_ABS frame (same SEQ) sent twice; query/parameter commands repeated with new SEQ | each answered once with its SEQ; the FW does not de-duplicate (second MOVE_ABS → E_BUSY or zero-length move), no double motion (FW part of AC; SW retry rules → Validator F). |
| TC-IF-006-01 | U+S | M1 | DATA vectors, ICD §7.3 | decode/encode every field; layout review | every field decoded; layout = ICD (AC). |
| TC-IF-007-01 | T | M1/M2 | B stream on | 10 min incl. two DOUT stalls | DATA frames = conversions; fallback frames only during stalls and marked NO_AFE_DATA (AC). |
| TC-IF-008-01 | T | M1 | B | GET_INFO; every DATA frame | proto/payload/hash fields correct; `payload_version` = 1 in every DATA frame (FW part; SW reaction → Validator F). |
| TC-IF-009-01 | S+U | M1 | ICD, `protocol.yaml`, vectors | review of units; codec vectors | wire units µm, µm/s, µm/s², µs, counts; absolute targets only (AC). |
| TC-IF-010-01 | S | M1 | CI | `gen_params.py --check`, `gen_vectors.py --check`; pre-script reads vectors in place; anti-skip counts | exit 0; same JSON files consumed by FW and SW; executed counts = JSON counts (AC). |
| TC-IF-011-01 | A+T | M1 | R stream on, 10 min with events | twin wire log byte count; congestion with a STOP/HALT/PAUSE command | FW→PC ≤ 10 % of 92 160 B/s; the stop executes before its response is queued and the response is never dropped (FW part of AC). |
| TC-IF-012-01 | S+T | M1 | `proto_gen.h`, dispatch table | each of the 26 commands (25 of ICD v0.2 incl. PAUSE 0x3B, plus RESUME 0x3C per ICD v0.4) sent once | no E_UNKNOWN_CMD for a defined command (M1: motion commands may answer E_INTERNAL NOT_IN_BUILD with feature bit 0) (AC checklist). |

### 3.5 Non-functional (NFR-005…008)

| TC | Lvl | MS | Pre | Stimulus | Expected (AC) |
|---|---|---|---|---|---|
| TC-NFR-005-01 | S+T | M1 | release map file; twin | `check_static.py`: malloc/free/calloc/realloc/_sbrk/_malloc_r/operator new/delete, printf family, HardwareSerial, HardwareTimer; heap usage; STATUS `stack_free_min` | no allocator symbol, heap 0; stack high-water reported (AC). Target value → HG-18. |
| TC-NFR-006-01 | S+H | M2 | source; HG-18 | inspection for waits/loops without a bound < 1 ms outside the NVM PROGRAM pass; target `loop_max_us` at 50 kHz + 80 Hz + 20 cmd/s | no blocking construct; `loop_max_us` ≤ 1000 µs (AC target). |
| TC-NFR-007-01 | S+H | M2 | disassembly; HG-18 | static cycle count of `sg_halt`/E-stop/STOP handlers and of every critical section; DWT measurement build | step ISR ≤ 2 µs, step CPU ≤ 15 % at 50 kHz, windows masking NVIC levels 0–2 ≤ 1 µs, E-stop/STOP ISR ≤ 1 µs (AC; interpretation OBS-P1-04). |
| TC-NFR-008-01 | T | M1 | R stream on, 20 commands/s | every command type; SAVE (with erase), LOAD, DEFAULT | last request byte → first response byte ≤ 10 ms; NVM commands ≤ 2.5 s with the twin's 500 ms erase model (AC twin). Target sample → HG-27. |

### 3.6 Coverage summary

| Group | Requirements in scope | Must / Should | TCs | U | T | S/A | H-only |
|---|---|---|---|---|---|---|---|
| SYS | 11 | 11 / 0 | 13 | 1 | 4 | 5 | 3 |
| SAF-FW | 26 | 26 / 0 | 43 | 9 | 31 | 3 | 0 |
| FW-PLT/AFE/MOT/HOM/SW | 25 | 24 / 1 | 29 | 8 | 17 | 3 | 1 |
| FW-CFG/NVM/CMD/STR/TIM/PAR | 24 | 23 / 1 | 28 | 8 | 12 | 8 | 0 |
| IF | 12 | 12 / 0 | 12 | 4 | 3 | 5 | 0 |
| NFR-005…008 | 4 | 4 / 0 | 4 | 0 | 1 | 3 | 0 |
| **Total** | **102** | **100 / 2** | **129** | **30** | **68** | **27** | **4** |

Counting rule: a TC is counted once at its primary level (combined levels such as "U+T" count at the first letter). By milestone: **M1** 45 TCs for 40 requirements; **M2** 83 TCs for 61 requirements; **M4** 1 TC (FW-MOT-006). Every in-scope requirement has ≥ 1 TC (102/102 = 100 %); 99 requirements have a host-runnable (U/T/S/A) TC and 3 (SYS-009, SYS-011, FW-HOM-003) are verified only at the hardware gate. Requirements with an additional **+H** part (open under D-06): SYS-004/005/006/009/011, SAF-FW-002/004/005/018/019/022/024, FW-PLT-002, FW-AFE-001/004, FW-MOT-001, FW-HOM-003, FW-SW-005, FW-NVM-003, FW-TIM-001, IF-002, NFR-006/007/008 (24 requirements → §6).

---

## 4. Fault-injection catalogue (FW host twin)

"Vocab" = availability in the `tools/README.md` world-control vocabulary v1: **yes**, **ext** = extension requested (DEF-P1-03).

| FI | Fault | Injection | Parameters | Expected FW reaction | Vocab | Used by |
|---|---|---|---|---|---|---|
| FI-01 | Switch bounce on hit/release | limit/button edge bursts | burst 0.1–5 ms, 1–20 bounces; release stable 19/20 ms | first edge acts, one stop, one EVENT; release only after `io.release_ms` | buttons yes, limits/E-stop **ext** | FW-SW-001/003, SAF-FW-006/013/022/023 |
| FI-02 | Wire break (NC) | input forced open | E-stop sense, START, END, STOP, DRV_PWR aux | reads active / unpowered → stop + latch/flag | limits yes, others **ext** | SAF-FW-007, FW-SW-005 |
| FI-03 | Both limits active | START and END forced | idle / moving / homing | LIMIT_WIRING, motion refused | yes | SAF-FW-014 |
| FI-04 | DOUT stall | `afe.stall` | permanent; 1 missed edge; stall 50/99/100/101/500 ms | AFE_STALE, AFE_FAULT if moving, fallback frames, missed-edge kick | yes (single missed edge **ext**) | SAF-FW-012, FW-STR-005, IF-007 |
| FI-05 | HX711 rate error | `afe.rate_error` | 10 vs 80 SPS, ±2 %, ±20 % | AFE_RATE_MISMATCH ≤ 1 s or none | yes | FW-AFE-004 |
| FI-06 | SCK overrun / HX711 power-down | read status flag from the model | one read, repeated | re-init, counter + 1, settle discard | **ext** | FW-AFE-003 |
| FI-07 | Rail / threshold samples | `afe.saturate`, scripted raw | ±rail, raw_max ± 1, regrow steps | LOAD_LIMIT trip rules | yes (scripted raw **ext**) | SAF-FW-008…011 |
| FI-08 | Flash power cut | cut after program word n / during erase k | every n for 3 SAVEs incl. sector switch | valid old or new record at boot, never mixed | seam only, control **ext** | FW-NVM-002 |
| FI-09 | Link silence | `inject link_silence` | timeout ± 1 ms; idle/moving/homing | controlled stop at timeout, LINK_WDG, restore on next valid command | yes | SAF-FW-015, SYS-002 |
| FI-10 | RX corruption / noise / overflow | `inject rx_corrupt`; byte injection; ring overflow | bad CRC, LEN 0xFFFF/161, truncation, 3 KB burst | counters, resync, no action on bad frames, `rx_overruns` | partly; byte injection at virtual time **ext** | IF-003/004, FW-CMD-001/004 |
| FI-11 | TX congestion | `inject tx_congestion` | drop 1/5/50 DATA frames | `frame_seq` gap, OVERRUN, responses never dropped | yes | FW-STR-004, IF-011 |
| FI-12 | FW hang / IWDG | `inject hang` | main loop, tick, level-1 ISR storm; LSI 17/47 kHz | reset ≤ 90 ms, PUL stops ≤ 100 ms, cause IWDG | hang yes, IWDG model + ISR storm **ext** | SAF-FW-019 |
| FI-13 | K1 weld | `estop open` with `drv_power on` held | 150/200/2000 ms, sense on/off | K1_WELDED after `drv.k1_weld_ms`, clear rules | yes (with `drv_power_follows` off) | SAF-FW-025 |
| FI-14 | Driver power loss | `drv_power off` with E-stop closed | during jog, move, homing, ENABLING, idle, SAVE; toggles 5/19/21 ms | stop, ENA disabled, NOT_ENABLED, HOMED 0 ≤ 25 ms; short toggles ignored | yes (bounce **ext**) | SAF-FW-024, FW-SW-005, FW-MOT-008 |
| FI-15 | Driver ALM | `alm active` | idle powered/unpowered, during move/jog, 1 kHz chatter | start-block only, no stop, ALM_CHANGED | yes (chatter **ext**) | SAF-FW-026, FW-SW-004 |
| FI-16 | PEND withheld | `pend` inactive after a move | timeout 0/200 ms | NOT_SETTLED warning | yes | FW-SW-004 |
| FI-17 | Step overrun | `inject step_fault` | during accel/cruise/decel | STEP_FAULT, HOMED cleared, POS_UNCERTAIN | yes | SAF-FW-004 |
| FI-18 | Lost steps (open loop) | world shifts `x_um_true` vs counter | 0.1 / 0.2 / 0.3 mm | HOME_DRIFT rule at re-homing | **ext** | FW-HOM-004 |
| FI-19 | HSE failure | `hal_clk_fallback` = 1 at boot | – | CLK_FALLBACK in STATUS + EVENT | **ext** | FW-PLT-002 |
| FI-20 | Inputs active at boot / reset during motion | `reset` with inputs set | pin/power/IWDG/software | boot latches, NOT_ENABLED, reset cause | yes (software cause **ext**) | SAF-FW-007/018 |
| FI-21 | PAUSE race | PAUSE edge + JOG/MOVE_ABS frame at offsets −1…+5 ms; RESUME vs STOP-button HALT race | 0.5 ms steps | D-30: no restart; D-31: RESUME refused with HALT latched | needs byte injection at virtual time (**ext**) | SAF-FW-023 |
| FI-22 | Switch missing / swapped DIR | START removed; END placed on the −x side | – | HOME_NOT_FOUND / HOME_WIRING | yes | FW-HOM-002 |

---

## 5. Entry / exit criteria and verdict format

### 5.1 M1 (link & skeleton)
**Entry** (all required before Validator E starts M1 validation):
1. SRS ≥ v0.4 with D-30/D-31 incorporated; ICD v0.4 (RESUME) with regenerated `protocol.yaml` outputs and vectors; `gen_params.py --check` and `gen_vectors.py --check` exit 0 (today both exit 1 — OBS-P1-09).
2. FW_design ≥ v0.2 with seam v1 frozen and **the same seam list in `tools/README.md`** (DEF-P1-02).
3. Implementer A evidence (FW_design §9.3): `pio test -e native` green with case counts, release + debug build SUCCESS with flash/RAM figures and A's map check PASS.
4. `fw_twin.exe` (Integrator) builds from the unchanged sources and supports the M1 subset of §1.3 (lock-step, wire log with timestamps, byte injection, flash power cut).
5. `units_vectors.json` available (OI-FW-20) or the validator oracle accepted by the Orchestrator for SYS-003.

**Exit — GO**: every M1 TC (45) passes twice (fixed + random order/seed); 40/40 M1 requirements verified at host/twin/static level; zero open DEF of severity High/Medium against M1 code; vector counts equal the JSON counts. **GO WITH CONDITIONS**: as GO but with open Low DEFs or Medium DEFs that have an accepted fix plan and no safety impact, and with the +H parts of FW-PLT-002, FW-NVM-003, IF-002, NFR-008 open (D-06) — these are always listed as conditions until the hardware gate. **NO-GO**: any failing TC of a Must requirement without an accepted waiver, any High DEF, or missing evidence.

### 5.2 M2 (sensor & motion)
**Entry**: M1 GO / GO WITH CONDITIONS with its conditions closed or carried; SRS clarifications of §8 (DEF-P1-05, OBS-P1-01…06) in the SRS; DEF-P1-01 resolved in `params.yaml`/FW; DEF-P1-04 answered in FW_design; twin supports the full §1.3 list and FI-01…FI-22; `motion_vectors.json` (R4 TV-M) available (OI-FW-20).
**Exit — GO**: all 83 M2 TCs pass twice; SAF-FW suite passes in L1, L2 and L3 (TC-SYS-002-01); 10 000-stop count test and 10 000-step differential walk clean; no High/Medium DEF open. **GO WITH CONDITIONS**: the +H parts listed in §3.6 and the hardware-gate list §6 open under D-06 (mandatory condition: no motion under load on target before HG pass). **NO-GO**: any failing SAF-FW TC, or a High DEF.

M4 (FW-MOT-006) uses the M2 criteria for its single TC.

### 5.3 Report and verdict format (`FW_test_report_<gate>.md`)
1. Header: gate, date, FW commit/build id, ICD/dict versions + hash, tool versions (gcc 13.1, PlatformIO 6.2, Python), host.
2. Evidence: every command run with pass/fail counts (`pio test …` cases/fail/ignored, pytest passed/failed/seed, static checks).
3. Coverage table: requirement → TCs → result (PASS / FAIL / OPEN-H / BLOCKED).
4. Findings: `DEF-Mx-nn` (severity High/Medium/Low, file:line, evidence, fix hint, addressee) and `OBS-Mx-nn`.
5. **Verdict: GO / GO WITH CONDITIONS / NO-GO**, conditions numbered with owner and closure point.

---

## 6. Hardware-gate check list (merged: `wiring.md` C-01…C-20 + SYS-009 + every +H criterion)

Preconditions for the whole list: PO approval reference recorded (D-06); board/port named by the PO; build id from GET_INFO recorded; DIP state recorded (SYS-005); M2 verdict GO or GO WITH CONDITIONS. Items HG-01…HG-24 and HG-28 must pass **before any motion under load** (SYS-009). Tools: DWT build (`nucleo_f446re_debug`, PC8/PC9 markers), logic analyser ≥ 10 MS/s, scope, dial indicator, spring specimen, `00_System/tools/hil/*`.

| HG | Item (origin) | Method | Pass criterion | Req. |
|---|---|---|---|---|
| HG-01 | Board identity, solder bridges (C-01) | visual + photo | as `pinout.md` §5 | SYS-007, FW-PLT-002 |
| HG-02 | Clock source (C-02) | GET_STATUS; PUL at 50 kHz with a counter | no CLK_FALLBACK; frequency error ≤ 0.1 % | FW-PLT-002 |
| HG-03 | VCP 921 600 Bd soak (C-03) | 10 min at 80 Hz + 20 cmd/s (M3 gate: 1 h, NFR-004) | 0 sequence gaps, 0 CRC errors both sides | IF-002, SYS-009 |
| HG-04 | Flash erase vs IWDG, E-stop and DRV_PWR during SAVE, no split frame (C-04 extended) | SAVE forcing an erase; E-stop pressed during the erase; LA on TX | no IWDG reset, SAVE ≤ 2.5 s, valid record after reboot, ENA disabled ≤ 1 ms during the erase, no split frame on the wire; DRV_PWR reaction time recorded (DEF-P1-06) | FW-NVM-002/003, SAF-FW-005/019/024 |
| HG-05 | HX711 on silicon (C-05) | LA on DOUT/SCK, 10 000 reads; GET_STATUS rate | SCK-high ≤ 50 µs, read ≤ 60 µs, rate ±1 % of LA, timestamp vs DOUT edge ≤ 5 µs | FW-AFE-001/004, FW-TIM-001 |
| HG-06 | Opto drive margin, direct 3.3 V bring-up (C-06) | debug build holds PUL/ENA high; I_LED via shunt | I_LED ≥ 6 mA, VOH ≥ 3.0 V, else buffer first | SYS-009, OI-05 |
| HG-07 | ENA enable/disable and settle (C-07 extended) | ENABLE/DISABLE; LA: ENA vs first PUL/DIR edge after ENABLE and after DRV_PWR return | disabled = shaft free (no load), enabled = holding; first edge ≥ `motion.ena_settle_ms` in both cases | FW-MOT-008, SAF-FW-024 |
| HG-08 | PUL/DIR timing at the driver terminals (C-08) | LA at 50 kHz, reversals | high ≥ 10 µs, low ≥ 10 µs, DIR setup ≥ 20 µs on every reversal | FW-MOT-001 |
| HG-09 | Step count integrity (C-09) | 100 moves vs external counter / TIM3 test build | difference 0 (±1 only with POS_UNCERTAIN) | SAF-FW-004 |
| HG-10 | E-stop reaction + power removal (C-10) | scope PA10 vs last PUL, ENA; driver supply with the MCU held in reset; 100 trials | last PUL ≤ 100 µs, ENA ≤ 1 ms; supply < 5 V ≤ 100 ms independent of the MCU; after release no power until RESET | SAF-FW-005, SYS-006 |
| HG-11 | Limit / STOP-button reaction (C-11 extended) | scope edge at the MCU pin vs last PUL, 100 trials each; repeat STOP with the USB link unplugged | ≤ 200 µs; HALT latched without the PC | SAF-FW-002, SAF-FW-022 |
| HG-12 | FW load-limit reaction (C-12) | threshold just above a known load; DOUT ready of the deciding sample vs last PUL | ≤ 200 µs | SAF-FW-002/008 |
| HG-13 | PC STOP/HALT and Pause/Break key (C-13) | RX line last byte vs last PUL; key press → last PUL, 100 trials | ≤ 2 ms; key → last edge ≤ 100 ms p95 (M3) | SAF-FW-002, NFR-003 |
| HG-14 | Hang → IWDG (C-14) | test image with injected loop while moving | PUL stops ≤ 100 ms, reset cause IWDG | SAF-FW-019 |
| HG-15 | Reset under load (C-15, **revised per DEF-P1-05**) | spring specimen preloaded in tension (≈ 100 N); MCU reset; dial indicator | axis motion ≤ 0.01 mm (driver keeps holding, D-13) | SAF-FW-018 |
| HG-16 | ALM/PEND levels of the PFDE clone + start-block (C-16) | powered/unpowered, in position/moving, open/closed-loop setting | levels recorded, `drv.*_active_level` set; ALM_CHANGED; new motion refused while ALM active and DRV_PWR on | FW-SW-004, SAF-FW-026 |
| HG-17 | Input wire break (C-17 extended) | unplug E-stop sense, START, END, STOP and the K1 aux (DRV_PWR) | reads active/unpowered, stop + flag | SAF-FW-007 |
| HG-18 | Main-loop and ISR budgets (C-18) | `loop_max_us`; DWT build at 50 kHz + 80 Hz + 20 cmd/s; `stack_free_min` | loop ≤ 1000 µs; step ISR ≤ 2 µs; step CPU ≤ 15 %; windows masking levels 0–2 ≤ 1 µs; E-stop/STOP ISR ≤ 1 µs; stack margin > 0 | NFR-005/006/007, FW-SW-002 |
| HG-19 | Driver DIP sheet (C-19) | inspection, all 8 switches; first travel calibration vs expected steps/mm | recorded (160 at 800 p/rev, 800 at 4000 p/rev) | SYS-005 |
| HG-20 | E-stop circuit inspection (C-20) | wiring vs R5 Option A; K1 aux to PA7 | as drawn | SYS-006, SYS-001 |
| HG-21 | DRV_PWR sense (new, SYS-009) | K1 opened by the E-stop and by switching the PSU off with the E-stop closed; scope PA7 vs ENA/last PUL during a jog | status DRV_PWR and EVENT DRIVER_POWER observed; stop + ENA disabled + NOT_ENABLED ≤ 25 ms; HOMED = 0 | SAF-FW-024, FW-SW-005 |
| HG-22 | K1_WELDED plausibility, simulated (new, SYS-009) | E-stop opened with the K1 aux bridged (simulated weld), driver supply disconnected for safety | FAULT K1_WELDED after `drv.k1_weld_ms`, FAULT_CLEAR refused until the bridge is removed | SAF-FW-025 |
| HG-23 | Buffer board (new, SYS-011/SYS-009) | inspection; I_LED per input; HG-07/HG-08 repeated through the SN74ACT244 | board installed before the first calibration; opto current within rating (≈ 10–13 mA); ENA/PUL/DIR timing as HG-07/08 | SYS-011, SYS-009 |
| HG-24 | ALM reset (new, SYS-009) | provoke an ALM (e.g. supply under-voltage, bench only); reset by E-stop + RESET; bench test of ENA-toggle reset | reset by power cycle works; ENA-toggle result recorded | SYS-009, D-28 |
| HG-25 | Speed envelope (new, SYS-004) | after travel calibration: 0.01 and 10 mm/s moves (caliper/indicator + timing), 30 mm/s unloaded, re-home, soft-limit travel | mean speed error ≤ 1 %; no ALM at 30 mm/s; HOME drift ≤ `home.drift_tol_um`; usable travel ≥ 280 mm | SYS-004 |
| HG-26 | Home repeatability (new, FW-HOM-003, Should) | 10 homing cycles, dial indicator at 0 | ≤ 0.02 mm (2σ) | FW-HOM-003 |
| HG-27 | Command response sample (new, NFR-008) | 1000 commands of all types under streaming; SAVE/LOAD/DEFAULT | ≤ 10 ms each; NVM ≤ 2.5 s | NFR-008 |
| HG-28 | Direction and homing smoke (new; precondition for HG-25/26) | set `motion.dir_invert` so that +x moves away from START; HOME at low speed; HOME with DIR deliberately inverted (no load) | HOMED at START; inverted DIR → HOME_WIRING | FW-HOM-001/002, ICD §0.1 |

**List size: 28 items** (20 merged from C-01…C-20, 4 of them extended; 8 new). Every +H criterion of §3.6 maps to at least one HG item.

---

## 7. Traceability of this plan
- Requirement → TC: §3 tables (one row per TC, requirement in the ID).
- TC → code: `/* Verifies: <REQ> */ /* TC: <TC-ID> */` tags in `02_FW/test/**`; the report lists the test function names per TC.
- HG item → requirement: §6 last column; target-only criteria stay OPEN-H in every report until the PO-approved gate.

---

## 8. P1 design review findings (FW_design v0.2, ICD v0.2 + v0.3/v0.4 deltas, pinout v0.2, params dict 2 vs SRS v0.3 + D-30/D-31)

Already tracked by Implementer A in FW_design §11.2 and confirmed independently here (no new ID): OI-FW-17 (seam deltas → see DEF-P1-02), OI-FW-18 (ICD §2.4 wire order), OI-FW-19 (sniffer PAUSE in ICD §2), OI-FW-20 (units/motion vectors), OI-FW-22 (boot ENA with DRV_PWR off), OI-FW-23 (homing bounds as FW constants), OI-FW-24 (controlled-stop measurement — adopted in TC-SAF-FW-003-01 with the refinement of OBS-P1-01), OI-FW-26 (SAF-FW-023 vs D-30), OI-FW-27 (SAF-FW-024 events).

### 8.1 Defects

| ID | Sev. | Finding (evidence) | Fix hint | Addressee |
|---|---|---|---|---|
| **DEF-P1-01** | High | **AFE stale timeout vs 10 SPS.** `params.yaml:160-171` `afe.timeout_ms` default 100 ms (min 20, no hard rule); `afe.rate_sps` SPS10 is allowed without reboot (FW-AFE-002) and gives a 100 ms conversion period. At 10 SPS every sample arrives at ≈ the timeout → AFE_STALE flickers, motion is refused (SAF-FW-012/020) and a running move trips AFE_FAULT. The M1 synthetic source (TIM5 CC2 at `afe.rate_sps`, FW_design §5.8) shows the same. FW_design §5.7.1 applies the parameter as is. | Hard rule H5 `afe.timeout_ms ≥ 2.5 × conversion period` (≥ 250 ms at 10 SPS, checked on SET of either parameter), or the FW uses `max(afe.timeout_ms, 2.5 × period)`; SRS SAF-FW-012 to state which. TC-SAF-FW-012-02. | Integrator (params/ICD §11.4), Orchestrator (SRS), A |
| **DEF-P1-02** | Medium | **Twin seam contract exists twice.** `tools/README.md` "Seams" table still lists `hal_uart_write(frame, n)` without class, `hal_step_stop_now` only, no `peek`, `abort`, `set_period_now`, `arm_last`, `set_count`, `stop_gen`, `inputs_config/rearm`, `afe_sample_t`, `hx711_kick/hold`, `stack_free_min`; FW_design v0.2 §8.1 needs all of them (OI-FW-17 open). The twin cannot be coded against both, and the halt modes (CLEAN/TRUNCATE, POS_UNCERTAIN), TX classes and stretch path are exactly what SAF-FW-002/003/004 and FW-STR-002/004 tests observe. | Integrator adopts FW_design §8.1 as seam v1 in the README (single source = A's headers), before M1-WP1 ends. | Integrator, A |
| **DEF-P1-03** | High | **Twin control/observation vocabulary insufficient for the acceptance criteria.** README vocabulary v1 has no time-stamped PUL/DIR/ENA edge log (`query pulses` returns a count), no byte-level RX injection at a virtual time, no flash power-cut action, no HX711 SCK-overrun/power-down or single missed edge, bounce/broken wire only for some inputs, no IWDG model, no ISR-storm or ALM chatter, no lost-step world shift, no HSE-fail flag, no wire log with first/last-byte times. About 45 T-level TCs (all SAF-FW timing, FW-STR-002/004, FW-NVM-002/003, NFR-008, FI-01…FI-22 marked "ext") cannot produce evidence without them. | Extend the vocabulary per §1.3 / §4 ("ext"); M1 subset (lock-step, wire log, byte injection, flash cut, reset causes) before M1 exit, the rest before M2 entry. | Integrator |
| **DEF-P1-04** | Medium | **False premise "c1 ≥ 1 ms for every parameter combination"** (FW_design §5.6.2 item 4) used by the sniffer argument in §5.9.3 ("a MOVE_ABS dispatched before an already sniffed HALT cannot emit a pulse"). c1 = f·√(2/α), α [steps/s²] = `a_um_s2`·spm/1000: at `a_max_um_s2` 10 000 000 and spm 100 000, α = 10⁹ → c1 ≈ 44.7 µs; at spm 800, ≈ 0.5 ms; at the default 160 spm with a_max at its maximum, ≈ 1.1 ms. If the dispatch budget splits MOVE_ABS and HALT across passes, pulses can be emitted after the HALT was received (HALT ≤ 2 ms still likely met, but not by the stated argument). | Either keep a "stop seen by the sniffer, not yet dispatched" marker that holds any motion start issued from earlier frames until the sniffed frame is dispatched, or restate the bound with the real c1 and dispatch-split timing. TC-SAF-FW-002-03. | A |
| **DEF-P1-05** | Medium | **Inexecutable acceptance criterion.** SRS SAF-FW-018 (and `wiring.md` C-15) "reset with a 10 kg hanging load → axis motion ≤ 0.01 mm": the axis is horizontal without gravity load (D-28, SRS §1), so a hanging load cannot act on it. | Reword: spring specimen preloaded in tension (e.g. ≈ 100 N), MCU reset, travel change ≤ 0.01 mm (dial indicator), raw change within noise. HG-15 already uses this. | Orchestrator (SRS), A (wiring C-15) |
| **DEF-P1-06** | Medium | **Flash operation suspends the DRV_PWR supervision.** During the PROGRAM pass (erase ≤ 0.5 s) `CRIT_NVM` (BASEPRI 0x20) masks the tick and core callbacks are deferred (FW_design §5.11, pinout §4). DRV_PWR is polled only in the tick (§5.7.2), so a driver-power loss during SAVE is handled up to ≈ 0.5 s late; SAF-FW-024 (≤ 25 ms, no state exemption) is then not met; K1_WELDED timing, idle disable and PAUSE/STOP core latches are deferred the same way (the RAM-resident HAL stop reactions are not affected). The axis is idle, so the safety impact is small, but the requirement is violated as written. | SRS: exempt the idle NVM operation explicitly (as NFR-006 does), or the HAL disables ENA from a RAM-resident check of PA7 during flash operations. TC-SAF-FW-024-02, HG-04. | Orchestrator, A |
| **DEF-P1-07** | Low | **Response ring overflow not closed.** FW_design §5.9.4: class R "cannot overflow by construction (SW ≤ 4 outstanding commands)… If it does anyway: E_INTERNAL INVARIANT is logged". FW-CMD-001 demands exactly one response per CRC-valid command regardless of PC behaviour; a flooding PC (or fuzz test) breaks it. | Dispatcher takes the next frame only when class R has room for the largest response (169 B) → back-pressure into the RX ring (counted overrun). TC-FW-CMD-001-02. | A |

### 8.2 Observations

| ID | Observation | Proposal | Addressee |
|---|---|---|---|
| OBS-P1-01 | SAF-FW-003 "deceleration starts ≤ 2 ms" is not measurable as written. FW_design OI-FW-24 proposes "the moment the running period is reprogrammed". | Adopted: **commit time** (seam log of `set_period_now` / preload of c_dec1 / CLEAN halt) ≤ 2 ms after the trigger, every later PUL interval non-decreasing and on the planned profile ± 1 tick, stop distance ± 1 step. Trigger for the PAUSE button = threshold crossing at the MCU pin (the 1.3 ms RC of PB6 is outside the budget). SRS v0.4 should state this. | Orchestrator |
| OBS-P1-02 | SAF-FW-025 gives only a lower bound ("not at 150 ms"). | Test bound: latched in (k1_weld_ms, k1_weld_ms + 2 ms] after the E-stop sense edge with filtered DRV_PWR present; SRS to state an upper bound. | Orchestrator |
| OBS-P1-03 | SRS v0.3 SAF-FW-023 (PAUSED cleared by an accepted motion command) and SAF-FW-024 (STOPPED(DRV_POWER_LOST) "whether or not the E-stop is open") contradict D-30/D-31 and ICD §6.2 (OI-FW-26/27). | TCs follow D-30/D-31 and ICD §6.2; SRS v0.4 must align, otherwise M2 verdicts would test against a superseded text. | Orchestrator |
| OBS-P1-04 | NFR-007 "IRQ-masked windows ≤ 1 µs" vs design `CRIT_DATA` ≤ 2 µs, `CRIT_TICK` ≤ 5 µs, `CRIT_NVM` ≤ 0.5 s; FW_design §6 reads it as "windows that mask a safety ISR". | TC-NFR-007-01 / HG-18 measure "windows masking NVIC levels 0–2 (E-stop, inputs, step) ≤ 1 µs" and report the others; SRS wording to match. | Orchestrator |
| OBS-P1-05 | SAF-FW-017 does not say what happens while the AFE is stale; FW_design §5.7.1 disables idle disable while stale (load unknown → keep holding). | Accept the design rule (safe side, consistent with the speed-cap rule); SRS to state it. TC-SAF-FW-017-02. | Orchestrator |
| OBS-P1-06 | SAF-FW-013 "has moved away" is undefined; FW_design §5.3 uses "pos_steps strictly on the away side of the latch position". | Test with ≥ 1 step; SRS to state it. | Orchestrator |
| OBS-P1-07 | FW design rules not yet in the ICD (OI-FW-18 wire order D > R > E vs ICD §2.4 "responses > EVENT > DATA"; OI-FW-19 sniffer incl. PAUSE; OI-FW-22 boot ENA with DRV_PWR off; OI-FW-23 homing bounds). Until they are in the ICD they are tested against FW_design, not against an interface oracle. | ICD v0.4. | Integrator |
| OBS-P1-08 | Units and motion vectors (R4 TV-TC, TV-M) are not produced by `gen_vectors.py` (OI-FW-20). SYS-003 is M1. | `units_vectors.json` before M1, `motion_vectors.json` before M2; meanwhile validator oracles in `val_oracles/` (non-protocol math only). | Integrator |
| OBS-P1-09 | Generated outputs are stale right now: `gen_params.py --check` exit 1 (proto_gen.h, protocol_gen.py, ICD), `gen_vectors.py --check` exit 1 (both vector files at icd_version 0.2, `protocol.yaml` at 0.3) — the Integrator's v0.3/v0.4 transition in flight (run 2026-10-03 by Validator E). | M1 entry criterion 1. | Integrator |
| OBS-P1-10 | ALM is on EXTI9_5 at level 1 with no self-masking (FW_design §5.2: HAL reaction "–"); a chattering ALM (opto at threshold, unpowered driver) can flood a level that pre-empts the step ISR (level 2) and the HX711 timestamp. ALM needs no µs reaction. | Mask-after-edge + tick re-arm as for the buttons, or poll ALM in the tick. TC-FW-SW-004-01 chatter case. | A |
| OBS-P1-11 | PAUSE is an NO contact (D-28): a broken wire reads "not pressed" and is undetectable. | Accepted by design (STOP NC and E-stop are the paths of record); state in the operator notes. | Orchestrator |
| OBS-P1-12 | `wiring.md` §11 lacks the SYS-009 items DRV_PWR via E-stop and PSU off, K1_WELDED simulated, ENA through the buffer board, ALM reset by power cycle / ENA toggle, settle after power return; C-20 still says "FAULT_K1 check (if adopted)" although D-29 c adopted it; C-15 see DEF-P1-05. | Add C-21… or reference HG-21…HG-28 of this plan. | A |
| OBS-P1-13 | DATA `status` u16 is fully used (16/16 bits). Any new DATA status bit needs PAYLOAD_VERSION 2. | Informational for ICD planning. | Integrator |
| OBS-P1-14 | SYS-002 "passes with the PC disconnected": while moving, a disconnected PC trips the link watchdog after `link_timeout_ms`, so "disconnected" must be defined for timed tests. | Variant L3 (§1.4) uses `link_timeout_ms` = 5000 and stimuli inside the window; SRS wording may say "PC silent after the last command". | Orchestrator |
| OBS-P1-15 | **D-31 RESUME details** for ICD v0.4: exact BLOCK bits evaluated (HALT, ESTOP latched, FAULT — and what about the E-stop input open but not latched, DRV_UNPOWERED, ALM?); RESUME with PAUSED not set (OK no-op?); retry class (ONCE_PRIORITY like the clears?); EVENT PAUSE_CLEARED reason value for RESUME; confirmation that HALT_CLEAR with HALT **not** latched still clears PAUSED; RESUME refreshes the link watchdog; the stop sniffer must **not** act on RESUME. | Integrator fixes these in ICD v0.4 + `check_vectors.json`; TC-SAF-FW-023-03 and TC-SAF-FW-002-02 use them. | Integrator |

### 8.3 P1 verdict on FW testability

**YES WITH CONDITIONS.** The FW design v0.2 is highly testable: all decision logic is pure and host-testable, every interface byte and acceptance rule has an executable oracle (`ref_codec`, `ref_cmdcheck`, shared vectors), the seams isolate every timing-relevant hardware action, and every in-scope requirement (102/102) has at least one test case; only 3 requirements are verifiable solely at the hardware gate.

Conditions:
1. **DEF-P1-01** resolved in `params.yaml` / ICD §11.4 or FW (+ SRS SAF-FW-012 wording) before M1 exit (the M1 synthetic source already shows it).
2. **DEF-P1-02** (single seam v1 in `tools/README.md` = FW_design §8.1) before M1-WP1 ends.
3. **DEF-P1-03** twin vocabulary: M1 subset before M1 exit, full list before M2 entry.
4. **DEF-P1-04** answered in FW_design (mechanism or restated bound) before M2 coding of the motion start path.
5. SRS v0.4 resolves **DEF-P1-05**, **DEF-P1-06** and OBS-P1-01…06/14 (with D-30/D-31) before M2 entry.
6. ICD v0.4 with RESUME (OBS-P1-15) and the FW-design rules of OBS-P1-07; regenerated outputs with `--check` exit 0 (OBS-P1-09) before M1 entry; units vectors (OBS-P1-08) before M1 exit, motion vectors before M2 entry.
7. **D-06 stays in force**: the hardware-gate list (§6, 28 items) is a mandatory condition of every FW verdict until the PO approves a gate; no motion under load on target before HG-01…HG-24 and HG-28 pass.

---

## 9. Change history

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-10-03 | First FW test plan for the P1 gate (Validator E): strategy and levels (U/T/S/A/H), independence rules, twin harness requirements; 129 TCs for 102 requirements (M1 45, M2 83, M4 1) incl. D-30 (PAUSED blocks motion, race, clean-halt condition) and D-31 (RESUME) cases; fault-injection catalogue FI-01…FI-22; M1/M2 entry/exit criteria and report format; hardware-gate list HG-01…HG-28 (C-01…C-20 merged + SYS-009 + all +H criteria); P1 review DEF-P1-01…07, OBS-P1-01…15; verdict YES WITH CONDITIONS. |
