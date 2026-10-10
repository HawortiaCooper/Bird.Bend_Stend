# Bird Bend Stand — Firmware and shared-tools code review (Validator E)

| Item | Value |
|---|---|
| Version | v1.3 (2026-10-10): §16 re-check of FW v0.8.6 (D-55: `step_isr_core` target entry ≡ seam entry, acc clamp proof, branch-free selects; OI-FW-53 applied; OBS-RC-9 informational; HW-gate verdict GO WITH CONDITIONS). v1.2 (2026-10-10): §15 re-check of FW v0.8.5 (FWR-21, OBS-RC-7, OBS-RC-8 CLOSED; final HW-gate verdict GO WITH CONDITIONS). v1.1 (2026-10-10): §14 re-check of FW v0.8.4 (FWR-20 and OBS-RC-6 CLOSED; new FWR-21 Low, OBS-RC-7, OBS-RC-8). v1.0 (2026-10-09): §13 re-check of FW v0.8.3 (FWR-19 CLOSED; new FWR-20 Medium; OBS-RC-6). v0.9 (2026-10-09): §12 re-check of FW v0.8.2 (FWR-18 CLOSED; new FWR-19 Low). v0.8 (2026-10-09): §11 re-check of FW v0.8.1 (FWR-16, OBS-RC-1…3, FWR-09 pure check CLOSED; new FWR-18 Medium). v0.7 (2026-10-09): §10.5 D-52 HG-18 criteria and check-vector state_schema 4 applied. v0.6 (2026-10-09): §10 re-check of A's Task 1 + FWR-01…10 (all CLOSED, new FWR-16 / FWR-17 Low), §7 status. v0.5 (2026-10-08): §7 FWR-14 / FWR-09 (ICD) status and §9.2 Integrator re-review of round 2 (Implementer C). v0.4 (2026-10-08): §7 / §8.2 fixes of the Integrator review findings C-R1…C-R3, O-1…O-3 (round 2, for C's re-review). v0.3 (2026-10-08): §9 Integrator review of the HIL fixes (Implementer C). v0.2 (2026-10-08): §7 fix status, §8 change list of the HIL fixes for the Integrator's review. v0.1 (2026-10-08): review at e600169 |
| Reviewer | Validator E (FW) — Task 6, PO-approved whole-codebase review (not milestone-scoped) |
| Anchor | commit **e600169** (P2 complete). Every `file:line` below refers to that commit. The review ran on a `git archive e600169` export in the role scratchpad, so Implementer A's parallel ISR work (Task 1) does not affect the line numbers. |
| Baseline | SRS v0.6.4, ICD v0.7.4 (dict 6, 0xF8BCDCB8), FW_design v0.7, DECISIONS up to D-49 |
| Scope | `02_FW/src/**` (core, pure, hal/f446, gen, main), `02_FW/include/**`, `ldscript`, `platformio.ini`; `00_System/tools/hil/**` (security-minded review of the D-06 interlock and session gating); `00_System/tools/gen_params.py`, `gen_protocol.py` (YAML → generated C / Python) |
| D-06 | No hardware was used. No COM port was opened and no board was flashed. Target-only properties are argued from the disassembly of a scratch build, and they stay **open for HG-18 / HG-30**. |

## 1. Summary and verdict

The firmware is well structured. The pure/HAL split is clean, and the RAM-resident level-0/1 handlers call only RAM code (checked in the disassembly). The NVM log is robust: CRC-32 per header and entries, a commit marker written last, modular sequence numbers, and bounded entry counts. The frame, sniffer and TX codecs are bounds-safe. Our sources build with **zero warnings** at `-Wall -Wextra -Wconversion -Wsign-conversion` (and more) and pass `-fanalyzer`. The worst-case stack is about 2.9 KB. Every existing host suite passes at e600169.

The review found **one High and four Medium firmware defects**. All of them are races between the stop paths and the motion start, plus one main-loop budget defect:

- **FWR-03 (High).** There is a check-to-execution gap in `cmd_execute()`. The 1 kHz tick can latch a limit, PAUSED or wiring fault after `cmd_check()` and before the motion starts. For a latched limit, no level-based backup exists, so the move runs toward a pressed switch. Reproduced on the host: 36 PUL edges in 50 ms toward a latched END switch.
- **FWR-01 / FWR-02 (Medium).** The "start-then-recheck" protocol cannot see a halt that happens before the timer is marked running. A halt between `s_running = true` and `CR1 |= CEN` lets the timer run with the output forced off and counts phantom steps (HOMED is kept after a limit). Reproduced on the target HAL compiled against a register model.
- **FWR-06 (Medium).** Every GET_STATUS scans about 120 KB of painted RAM (≈ 1.7 ms). That breaks NFR-006 on the target and gives a value that saturates at 0xFFFF anyway.

In the HIL tools, the D-06 port interlock itself cannot be bypassed without a recorded approval row. However:

- the approval-row parser accepts negated rows such as "not approved" (FWR-12);
- the session gates (load gate, §6.8 bench entry) accept result files that were not produced on the board in this session (FWR-13);
- `--only` / `--from` skip the board-identity and bench-entry steps (FWR-15).

The generators render unescaped YAML text into C and Python (FWR-14, Low, trusted inputs).

**Verdict: GO WITH CONDITIONS** for continuing P3 preparation. Conditions:

1. Fix FWR-03, FWR-01, FWR-02 and FWR-06 before the HW gate (HG-09 / 10 / 11 / 18 depend on them).
2. Fix FWR-12, FWR-13 and FWR-15 (owner Validator E) before any board session under D-06.
3. The Low items follow in the order of §5.

The target-only criteria stay open while D-06 is in force.

## 2. Severity scale

| Severity | Meaning |
|---|---|
| High | A safety requirement (SAF-FW-*) can be violated in a reachable operating sequence, and no other FW mechanism catches it in time. |
| Medium | A safety timing, position-integrity or NFR budget is violated in a narrow race or on every occurrence of a normal operation. Or a process / interlock control (D-06, SYS-009 gate) can be weakened by operator input. |
| Low | Correctness or robustness defect with a benign or self-healing outcome, a defence-in-depth gap, or a deviation from FW_design without functional impact at the defaults. |
| OBS | Observation or recommendation; no defect. |

## 3. Findings

### 3.1 Table

| ID | Sev | Area | Title | Anchor (e600169) | Owner | Reproducer |
|---|---|---|---|---|---|---|
| FWR-01 | Medium | ISR / stop vs start | Start-then-recheck is blind to a halt issued before the timer is marked running | `hal/f446/step_tim2.c:62-72, 201, 221`; `core/motion.c:270, 292, 301` | A | `test_val_steprace` (2 FAIL) |
| FWR-02 | Medium | ISR / stop vs start | Halt inside `hal_step_start()` is undone by the `CR1 \|= CEN` RMW: phantom steps | `step_tim2.c:157-160` (0x08011c16..26) | A | `test_val_steprace` (2 FAIL) |
| FWR-03 | **High** | ISR / command path | Check-to-execution gap: a tick-folded latch is ignored by the command being executed | `core/cmd.c:80-87` vs `244/250/256/238/224`; `core/safety.c:284, 295-303` | A | `test_val_toctou` (2 FAIL) |
| FWR-04 | Low | ISR | `TIM2->CR1 \|= OPM` RMW in the step ISR / `hal_step_arm_last()` races a level-0/1 halt: +1 phantom step | `step_tim2.c:273` (0x08011dfc..e02), `192` | A | disassembly |
| FWR-05 | Low | ISR | `hal_step_set_period_now()` decides on `s_cur` while an update is pending, which can lead to a runt on a later CLEAN stop | `step_tim2.c:170-186` | A | analysis |
| FWR-06 | Medium | main loop / NFR-006 | Full stack-paint scan (~30 k words) on every GET_STATUS ≈ 1.7 ms | `hal/f446/sys_f4.c:67-79`; `core/status.c:126` | A | disassembly (loop 0x08011f04..0c) |
| FWR-07 | Low | motion | Jog reversal of a parked start is lost: state stays JOG, no motion, no MOVE_DONE | `core/motion.c:583-590, 924, 949` | A | analysis |
| FWR-08 | Low | ISR | `EXTI->IMR` read-modify-write from thread, tick, level 3 and level 1: a mask or re-arm can be undone | `exti.c:60, 146, 180`; `hx711_f4.c:88, 91, 141, 150` | A | analysis |
| FWR-09 | Low | HW_MEAS images | STATIC_LEVEL allowed in NOT_ENABLED while ENA is at the holding level: an uncounted step to an enabled driver | `pure/cmd_check.c:253-255`; `hil/hil_procs.py:773-774` | A, Validator E | analysis |
| FWR-10 | Low | ISR budget | `ramp_commit()` fallback runs soft-double math inside CRIT_MOTION (contradicts FW_design §9.8 / NFR-007) | `core/motion.c:386-393` | A | analysis, HG-18 |
| FWR-12 | Medium | HIL D-06 interlock | `approval_row()` accepts "not approved", "approved: no", "approved?" | `tools/hil/hil_link.py:66-78` | Validator E | `test_hil_review.py` (4 FAIL) |
| FWR-13 | Medium | HIL gating | Load gate / bench gate trust any result file in `--out` (twin or older session); `--accepted-open` references are not validated | `hil_session.py:112-135, 279`; `hil_procs.py:61-64, 146-148, 1162-1165` | Validator E | `test_hil_review.py` (1 FAIL) |
| FWR-14 | Low | generators | YAML text reaches generated C / Python unescaped or unvalidated | `gen_params.py:332, 393, 420, 429, 433, 792`; `gen_protocol.py:230, 263, 286, 341, 344` | Integrator (C) | `test_val_review_gen.py` (8 FAIL) |
| FWR-15 | Medium | HIL gating | `--only` / `--from` skip S-00 (board identity) and BENCH-ENTRY; `--board-uid` optional; dict-hash / protocol mismatch only recorded | `hil_session.py:67-74, 238, 296-301, 317-323` | Validator E | analysis |

(FWR-11 is not used; it was merged into OBS-R-8.)

### 3.2 Details

#### FWR-01 — Start-then-recheck is blind to a halt issued before the timer is marked running (Medium)
- **Evidence.**
  - `halt_hw()` increments `s_stop_gen` (step_tim2.c:71). It is reached from `hal_step_stop_now()` and `hal_step_abort()` only `if (s_running)` (step_tim2.c:201, 221).
  - `hw_start()` captures the generation in `motion.c:270`. That is after `cmd_check()` (cmd.c:81) and after `ramp_start()` (double math, tens of µs).
  - `s_running` becomes true only at step_tim2.c:158, after `oc1m(OC1M_PWM2)` (157).
  - An E-stop or limit edge anywhere in [gate check … step_tim2.c:157] therefore hits a no-op fixed reaction, and the recheck at motion.c:301 sees no change. The move starts after the stop. It is only stopped by the tick's fold of the input record, ≤ 1 ms later plus the rest of the thread's CRIT_TICK section.
  - Effect on SAF-FW-002 (limit, 200 µs) and SAF-FW-005 a (E-stop, 100 µs): both are exceeded. For the E-stop case the ISR has already disabled ENA, so the pulses reach a disabled driver.
  - FW_design §5.3 says "a start captures stop_gen, **checks the gates**, sets CEN, re-reads stop_gen". The capture after the gate check is a deviation from that.
- **Reproducer.** `02_FW/test/test_val_steprace/test_main.c`, tests `test_fwr01_limit_halt_in_start_window_visible` and `test_fwr01_estop_halt_in_start_window_visible`. Both FAIL at e600169: "halt while idle not visible to start-then-recheck".
- **Fix hint.**
  - Increment `s_stop_gen` on every `hal_step_stop_now()` / `hal_step_abort()` call, also when idle.
  - Capture the generation **before** the gate check (e.g. in `fw_cmd_ctx()` or the tick before a segment start) and pass it down.
  - Perform the final arming under PRIMASK with a generation compare (see FWR-02).

#### FWR-02 — Halt inside `hal_step_start()` is undone by the CEN read-modify-write; phantom steps (Medium)
- **Evidence.**
  - step_tim2.c:157-160 does `oc1m(PWM2); s_running = true; s_t_upd = dwt_cycles(); TIM2->CR1 |= CEN;`. The target image keeps this order: `strb s_running` at 0x08011c16, DWT read at 0x08011c1a, then `ldr/orr/str CR1` at 0x08011c1e..0x08011c26.
  - `hw_start()` holds only CRIT_MOTION (BASEPRI 0x20), which never masks levels 0/1.
  - If a level-0/1 handler runs in that window, `halt_hw()` forces the output inactive, clears CEN and sets `s_running = false`. Then the thread's RMW sets CEN again.
  - The counter now runs with the output forced inactive and `s_running == false`. Every update ISR counts a step that never reached the driver. Every later stop primitive is a no-op because `s_running` is false, and that includes the recheck at motion.c:301.
  - On the target, the core finishes the motion at the next tick. The following update ISR stops the counter because `step_isr()` sees `!M.running`. The phantom count is therefore about (time to the next tick + 1 period) × step rate: up to about 25 steps at 24 kHz.
  - For a limit the axis keeps HOMED, so the position is silently wrong. That violates SAF-FW-004 ("step counter = completed pulses").
- **Reproducer.** `test_val_steprace`: `test_fwr02_estop_inside_start` and `test_fwr02_limit_inside_start`. The halt is injected at the `dwt_cycles()` call between the two statements. Result at e600169: FAIL, "step timer kept running after the halt", with 7 updates and 0 PUL edges for a 7-step move. The control test without injection passes (7 pulses, count 7).
- **Fix hint.** Make the arming one PRIMASK section that also compares the caller's generation:

  ```c
  pm = __get_PRIMASK(); __disable_irq();
  if (s_stop_gen != gen) { /* refused */ }
  else { oc1m(PWM2); s_running = true; TIM2->CR1 |= CEN; }
  __set_PRIMASK(pm);
  ```

  This is ≤ 0.2 µs, inside the NFR-007 1 µs budget. Return "started" to the caller, and set the `last_armed` / period preload before CEN.

#### FWR-03 — Check-to-execution gap in `cmd_execute()` (High)
- **Evidence.**
  - `fw_cmd_ctx()`, `cmd_check()`, `payload_decode_request()` and `hal_time_us()` run without a critical section (cmd.c:80-87). Only the execution is under CRIT_TICK (cmd.c:224, 238, 244, 250, 256).
  - The TIM5 tick (level 4) can pre-empt the thread in between and fold an input record into a latch. The command is then executed against the old verdict.
  - For limits there is no level-based backup. `safety.c:284` re-stops only `if (a && !latched && motion_dir() == toward)`. The line is masked after the first edge, and the record was consumed by the fold. The move runs toward or into the pressed switch until its end point: target, soft limit, or for an un-homed jog the un-homed window bound (D-43 b). That violates SAF-FW-013.
  - The same gap applies to:
    - PAUSED: D-30 / SAF-FW-023, no backup.
    - LIMIT_WIRING: safety.c:295-303 acts only on the 0→1 latch, so with both switches active the move continues without limit protection.
    - ALM start-block: SAF-FW-026.
    - ESTOP: the level-based E-stop check at safety.c:247 stops it at the next tick. ENABLE can also re-drive ENA to "enabled" after the E-stop ISR disabled it; this is corrected ≤ 1 ms by the same check.
  - The load limit and AFE stale have per-sample or level checks and are covered.
  - The window is roughly the length of `cmd_check()` plus decode (≈ 10–50 µs), so about 1–5 % of motion commands overlap a tick. A latch needs to become pending in the preceding ≤ 1 ms. This is plausible for PAUSE pressed while the operator jogs, and for a switch hit by the un-homed jog's neighbour.
- **Reproducer.** `02_FW/test/test_val_toctou/test_main.c`. The unchanged cmd.c is compiled with its `hal_time_us()` call (cmd.c:87, inside the window) routed to a hook that runs one `core_tick_1ms()`. Results at e600169:
  - `test_fwr03_end_folded_inside_check_window`: **36 PUL edges in 50 ms toward the latched END switch**.
  - `test_fwr03_pause_folded_inside_check_window`: 36 PUL edges while PAUSED.
  - The control test (fold before the check) passes: E_STATE with BLOCK_LIMIT.
- **Fix hint.**
  - (a) Take `fw_cmd_ctx()`, `cmd_check()` and the execution of every state-changing command inside **one** CRIT_TICK section. CRIT_TICK masks only the tick and the link; measure the added tick jitter at HG-18.
  - (b) Defence in depth: make the limit backup level-based: `a && motion_dir() == toward`, independent of `latched`. Re-stop on LIMIT_WIRING while both inputs are active and a motion is running.

#### FWR-04 — `CR1 |= OPM` read-modify-write in the step ISR races a level-0/1 halt (Low)
- **Evidence.**
  - step_tim2.c:273 compiles to `ldr r3,[TIM2_CR1]; orr r3,#8; str` at 0x08011dfc..0x08011e02. If a limit (CLEAN via `halt_hw()`) or E-stop handler runs between `ldr` and `str`, the store sets CEN=1 again, with OPM. The counter runs to the next update with the output forced inactive, and the ISR counts one step that was never output.
  - `hal_step_arm_last()` (step_tim2.c:192) has the same pattern from `hw_start()` / `redit_hw()` under CRIT_MOTION only.
  - Outcome: SAF-FW-004 off by 1 step after a limit stop in the last period (HOMED kept).
- **Fix hint.** Set OPM under PRIMASK and only while `s_running`. Alternatively write the bit through the bit-band alias of TIM2->CR1 bit 3: TIM2 at 0x40000000 is inside the peripheral bit-band region, so a single-bit store needs no RMW.

#### FWR-05 — Stretch decision on the wrong interval when an update is pending (Low)
- **Evidence.**
  - `hal_step_set_period_now()` (step_tim2.c:170-186) is called inside CRIT_MOTION, which masks the step ISR. It uses `s_cur - s_pw` and `s_cur - 1` as the running compare and ARR. If UIF is pending, the preloaded period `s_pre` is already running. DEF-M2-01 fixed exactly this for the stop primitives through `ccr_running()`, but not here.
  - After a stretch, the pending ISR sets `s_cur = s_pre` (= c2) while c1 is running. `ccr_running()` then overestimates the compare. A CLEAN stop during c1's pulse can take the `halt_hw()` path and cut it: runt and uncounted pulse (SAF-FW-004).
  - It also skips one decel period. That part is benign because the decel stays monotonic.
  - This needs an update to land in a µs-long section at P > 1 ms (stretch path), followed by a CLEAN stop in that period. It is rare.
- **Fix hint.** In `hal_step_set_period_now()`, skip the stretch if `TIM2->SR & UIF` (the preload path then applies), or compute on the running interval as `ccr_running()` does. Extend A's `test_impl_steptim` register model with direct (non-preloaded) ARR/CCR1 writes to cover it.

#### FWR-06 — Stack high-water scan costs ≈ 1.7 ms per GET_STATUS (Medium, NFR-006)
- **Evidence.**
  - `hal_stack_free_min()` (sys_f4.c:67-79) scans painted words from `end` (0x20002238) up to MSP−256, about 0x1DBC8 B ≈ 30 400 words, on every call.
  - It is called from `status_build()` (status.c:126), i.e. on every GET_STATUS in the main loop.
  - The loop at 0x08011f04..0x08011f0c has 8 instructions with a taken branch, ≈ 10 cycles per word, ≈ 300 k cycles ≈ **1.7 ms**. That is more than the NFR-006 1 ms pass budget, by itself and regardless of the 500 µs dispatch budget, which is checked only before a frame.
  - The result saturates at 0xFFFF (u16), so above 64 KB free the scan carries no information.
  - FW_design §4.2 places the check in `status_service()` every 100 ms; that is not implemented.
  - There is no safety impact: stops run in ISRs, the tick and the sniffer. HG-18 / HG-30 `loop_max_us ≤ 1000` will fail.
- **Fix hint.** Paint and scan only the window that the field can represent, e.g. the top 64 KB below the initial MSP. Better: keep a running low-water pointer and scan downward from it (O(new usage)), or scan incrementally (≤ 1 k words per pass) in the main loop.

#### FWR-07 — Jog reversal of a parked start is lost (Low)
- **Evidence.**
  - A JOG start parked by the sniffed-stop hold is followed by a JOG of the opposite sign. motion.c:583-590 sets `reversing = true`, `parked = false`, `running = false`.
  - From then on nothing restarts or ends it:
    - `segment_ended()` needs `M.running` (motion.c:949);
    - the hold-resolved safety net and `motion_hold_resolved()` need `M.parked` (motion.c:924, 736);
    - refreshes only update `rev_*`.
  - The state stays MS_JOG with no motion and no MOVE_DONE until JOG 0, STOP or the dead-man.
  - This only happens when the sniffed frame is never dispatched (e.g. lost to an RX overrun). If the dispatcher reaches it, `motion_stop()` finishes the motion correctly.
- **Fix hint.** When reversing a parked jog, re-plan the parked segment in the new direction and keep it parked, or call `finish()` + `jog_begin()`.

#### FWR-08 — `EXTI->IMR` read-modify-write from several priority levels (Low)
- **Evidence.** IMR is modified with RMW from:
  - the tick (`hal_inputs_rearm`, exti.c:60);
  - level 1 (limit / PAUSE mask, exti.c:146, 180);
  - level 3 (EXTI4 handler, hx711_f4.c:141, 150);
  - thread (`hal_hx711_hold` at NVM SAVE, hx711_f4.c:88, 91). `hal_hx711_powerdown` (65, 71) does the same RMW but has no caller in the core at e600169.

  A higher-priority RMW in between is undone. Consequences:
  - A masked limit / PAUSE line can be unmasked again, giving bounce interrupts. These are harmless because the core is idempotent.
  - A re-arm can be lost (thread at SAVE vs tick re-arm). The limit line then stays masked until the next press/release cycle, and a hit is only stopped by the tick backup: ≤ 1 ms instead of ≤ 200 µs (SAF-FW-002).
- **Fix hint.** Use the bit-band alias for each IMR bit (EXTI at 0x40013C00 is in the bit-band region) or a PRIMASK section around each RMW.

#### FWR-09 — DIAG_MEAS STATIC_LEVEL with ENA at the holding level (Low, HW_MEAS images only)
- **Evidence.**
  - `check_meas()` allows STATIC_LEVEL in MS_NOT_ENABLED (cmd_check.c:253-255). After boot, NOT_ENABLED leaves ENA at the "no current = holding" level (D-13).
  - PUL forced active (meas_f4.c STATIC_LEVEL) then reaches an enabled, powered driver as an uncounted step.
  - HG-06 sends DISABLE only if the state is not already NOT_ENABLED (hil_procs.py:773-774).
- **Fix hint.**
  - FW: require `!g_fw.ena_on` for sel = PUL (E_STATE MEAS_STATE).
  - hil: always send DISABLE before STATIC_LEVEL.
  - Integrator: ICD Appendix C op 8 text.

#### FWR-10 — `ramp_commit()` fallback does the edit inside CRIT_MOTION (Low)
- **Evidence.**
  - After 4 lost races, motion.c:386-393 runs `redit_apply()` inside CRIT_MOTION, which masks the step ISR. For a jog speed change that is `ramp_set_speed()`: two `ramp_c_of()` with double sqrt/div in software on the M4F.
  - Estimate: 5–15 µs. FW_design §9.8 says "ramp math moved outside the section", with a bound of ≤ 0.5 µs; NFR-007 requires ≤ 1 µs for sections that mask level 2.
  - The fallback is most likely at high step rates, where the compute time is a large fraction of the step period.
  - If the section exceeds half a step period, the missed-update heuristic (step_tim2.c, `el > 3*s_cur` = 1.5 periods) counts a false missed step. The result is STEP_FAULT with HOMED cleared.
  - At the default caps (≤ 24 kHz, T/2 ≈ 21 µs) there is margin; at `max_step_rate_hz` 40–100 kHz there is not.
- **Fix hint.** Use float / pre-computed constants for the edit, or fall back to a cheap edit (stop-ISR style) instead of the full one. Measure `MDWT_CRIT_MOTION` max at HG-18.

#### FWR-12 — Approval-row parser accepts negated approvals (Medium, D-06)
- **Evidence.**
  - `approval_row()` (hil_link.py:66-78) accepts a table row that contains the reference as a whole cell, matches `\bapproved\b`, and contains none of pending / withdrawn / revoked / rejected / proposed.
  - These rows are therefore accepted as approvals: "| D-06-GATE-… | … | PO has **not approved** the HW gate yet | open |", "approved: no", "NOT approved", "approved? to be confirmed".
  - With such a row present, `--approved` passes the interlock.
- **Reproducer.** `00_System/tools/hil/tests/test_hil_review.py::test_fwr12_*`: 4 FAIL at e600169. The positive control passes.
- **Fix hint.** Require a dedicated status cell that equals `approved` or `approved (PO)` exactly, in a fixed column of a dedicated table (e.g. `| D-06-GATE-… | date | scope | approved (PO) |`). Reject the row if any negation token (not / no / ? / tbd) appears.

#### FWR-13 — Session gates trust result files that do not come from this board session (Medium, SYS-009 / §6.8)
- **Evidence.**
  - `ItemRec.to_json()` (hil_procs.py:61-64) records no mode (twin / target), session id, board UID or image.
  - `Ctx.result_of()` (hil_procs.py:146-148) reads any `<out>/results/<id>.json`.
  - `gate_load()` (hil_session.py:112-135) therefore opens the **load gate** when PASS files from a twin dry run, or from an older session, are in the same `--out` directory. `bench_entry()`'s "HG-10 c/d has passed **in this session**" check (hil_procs.py:1162-1165) behaves the same way.
  - `--accepted-open HG-xx:REF` (hil_session.py:279) accepts any REF text without checking DECISIONS / STATUS.
- **Reproducer.** `test_hil_review.py::test_fwr13_load_gate_refuses_results_not_produced_on_the_board`: FAIL at e600169, the gate passes with twin-style results.
- **Fix hint.**
  - Stamp every result with mode, session id, board UID and image commit, and accept only results of the current target session for gates.
  - Validate accepted-open references like the D-06 approval (recorded row).
  - Refuse a target session in a directory that holds twin results.

#### FWR-14 — Generators render unvalidated YAML text into C / Python (Low)
- **Evidence.**
  - Python output uses `repr()` for most strings, which is safe. The following fields are not escaped or validated:
    - `gen_params.py`:
      - enum `label` and `unit` in C comments, `*/` not escaped (420, 429; also 498-499);
      - `c_member` not checked as an identifier (332), emitted at 433 and in `offsetof`;
      - `dict_version` not checked as an integer, emitted at 393 (C) and 792 (Python).
    - `gen_protocol.py`:
      - `icd_version` inside a C string literal (263);
      - table `icd` in a C comment without `_c_comment()` (286);
      - constant `desc` after `#` in Python, newlines kept (341);
      - table `title` / `icd` inside a Python docstring (344);
      - `commands.c_prefix` / `py_name`, table `py_name` not validated (230).
  - Hostile text becomes C or Python code that the FW build or the SW import executes.
  - params.yaml and protocol.yaml are Integrator-owned and reviewed, so this is defence in depth. It is also an accidental-breakage risk: a `*/` in a unit label breaks the FW build.
- **Reproducer.** `02_FW/test/static/test_val_review_gen.py`: 8 cases FAIL at e600169, i.e. the marker becomes code in params_gen.h, params_gen.py, proto_gen.h and protocol_gen.py. The control on the real YAML passes.
- **Fix hint.** Validate in `load()`:
  - identifiers against regexes;
  - `dict_version` and versions as int or `^\d+\.\d+(\.\d+)?$`;
  - every free text to `[\x20-\x7E]` without `*/`, `"""` and newlines.

  Render free text only through `_c_comment()` / `repr()`.

#### FWR-15 — `--only` / `--from` skip board identity and bench entry (Medium, D-06 / §6.8)
- **Evidence.**
  - The only board-identity check is in S-00 (hil_session.py:70-74), and only if `--board-uid` is given. `--board-uid` is optional (238).
  - Dict-hash and protocol mismatches are only recorded as FAIL checks (67-69); the session continues and sends motion commands.
  - `--only` / `--from` (296-301) skip S-00. `--port COMx --approved … --from HG-28` starts motion on an unidentified device. That could be the wrong board or port, e.g. another rig with a similar framing.
  - HG-10a (100 E-stop STIM trials at 30 mm/s) is refused only if BENCH-ENTRY recorded an **error** (`if be and be.get("error")`, 317-323). When BENCH-ENTRY never ran, HG-10a runs without the §6.8 P-1…P-5 bench window and soft-limit narrowing.
- **Fix hint.**
  - Make `--board-uid` mandatory for target sessions.
  - Run the identity block (GET_INFO: UID, dict hash, protocol, image) at the start of every target session, whatever `--only` / `--from` say, and refuse on mismatch (`BenchRefused`).
  - Require BENCH-ENTRY PASS **in this session** before HG-10a (see FWR-13).

### 3.3 Observations (no defect)

- **OBS-R-1 (warnings).** All three images (release, HW_MEAS + DWT, synthetic AFE) were compiled with:
  `-Wall -Wextra -Wshadow -Wconversion -Wsign-conversion -Wdouble-promotion -Wcast-align -Wundef -Wstrict-prototypes -Wnull-dereference -Wduplicated-cond -Wlogical-op -Wredundant-decls` (+ `-fanalyzer`).
  - Result: **0 warnings** in our sources. The only hits are `-Wmissing-prototypes` on vector handlers and `-Wfloat-equal` on the NaN idiom in params_gen.c:193.
  - Recommendation to A: add `-Wconversion -Wsign-conversion` to the target `build_src_flags`, so that hal/f446 keeps this level. It is not covered by the native strict flags.
- **OBS-R-2 (stack).** Worst case from `-fcallgraph-info=su` per root:
  - thread 952 B (JOG → `ctrl_stop_apply` → `ramp_commit`);
  - tick 652 B (`link_tick` + `sniff_scan`);
  - EXTI4 336 B; levels 0–2 ≤ 48 B; level 5 ≤ 72 B;
  - plus FP exception frames (6 × 104 B) and an allowance for libgcc soft-float (≈ 64 B per level).

  Total ≈ 2.9–3.3 KB, against the 4 KB `_Min_Stack_Size` and ~115 KB actually free. All frames are static; there is no recursion and no VLA.
- **OBS-R-3.** The E-stop EXTI line is never masked. A chattering contact causes a level-0 interrupt storm. That starves the tick and leads to an IWDG reset, which is fail-safe because ESTOP is latched at boot. Accept or document.
- **OBS-R-4.**
  - The HardFault record stores PC + CFSR but not LR; FW_design §5.1 says PC/LR/CFSR.
  - A fault with a corrupt MSP ends in lockup, and the IWDG resets the MCU. Until then TIM2 keeps pulsing at the last period for ≤ 90 ms, which is within SAF-FW-019.
- **OBS-R-5.** At boot, `hal_step_init()` drives ENA to the "enabled" level a few µs before `safety_init()` applies the E-stop boot rule. This equals the reset level for `ena_invert = false`. With the buffer board (`ena_invert = true`), check the reset/boot level at HG-23.
- **OBS-R-6.** The IWDG starts at boot step 12 (≈ 15 ms). All boot loops are bounded (clock DWT timeouts, IWDG SR wait). This is an acceptable reading of SAF-FW-019 "active from boot".
- **OBS-R-7 (UB).**
  - No undefined behaviour was found: all shifts are on unsigned operands, the codecs are byte-wise (`le_get/put`), float bit-casts use `memcpy`, and double→int conversions are range-guarded (`sat_i32`, `sat_u32`, NaN excluded by `param_validate_set` / `param_raw_in_range`).
  - `(int32_t)le_get32()` (payload.c) is implementation-defined; GCC defines it as modulo.
- **OBS-R-8.** `g_fw.cmd_rx_count++` runs both in the thread (link.c:71) and in the tick (link.c:149) without protection. A lost increment can delay LINK_RESTORED by one frame.
- **OBS-R-9.** The sniffer matches a valid STOP / HALT / PAUSE byte pattern at any position, including inside another frame's payload: e.g. MOVE_UNTIL_LOAD's 17-byte payload can embed an 8-byte HALT frame. The result is a fail-safe motion stop plus a 20 ms hold. The probability is negligible for real data.

### 3.4 Areas reviewed without a finding

| Area | Result / evidence |
|---|---|
| NVIC priorities, grouping | Group 4 (stm32duino `premain`); E-stop 0 alone on EXTI15_10, limits / PAUSE 1, TIM2 2, EXTI4 3, TIM5 4, DMA / USART / SysTick 5 (irq_prio.h, exti.c, uart2_dma.c, hal_step_init). HW_MEAS extras at PRIO_MEAS / TICK / INPUTS (meas_f4.c:311-344). |
| BASEPRI / PRIMASK sections | `hal_crit_enter()` uses `__set_BASEPRI_MAX` (nesting-safe) and restores the saved value; PRIMASK sections restore the previous PRIMASK; CMSIS intrinsics are compiler barriers. |
| RAM-resident handlers during flash ops | `.data` disassembly: EXTI15_10 / 0 / 1 / 9_5, `limit_line`, `callback`, `hal_step_stop_now` / `abort`, `halt_hw`, `hal_ena_set`, `ram_erase` / `program` are in RAM. Their only flash reference is `on_input_edge` (veneer), which is skipped while `g_flash_op`. The vector table is copied to RAM (VTOR, 512-B aligned), with BASEPRI 0x20 during each operation. |
| Shared ISR data | Input records (single writer, PRIMASK snapshot); AFE trip record and `lat.faults` (level-3 RMW vs CRIT_DATA writers); VALID; class-D mailbox and TX queues under CRIT_DATA; EVENT ring under CRIT_TICK; ramp under CRIT_MOTION with copy-and-compare. The exceptions are listed in FWR-01…05, FWR-08 and OBS-R-8. |
| Sniffer vs dispatcher | TYPE/SEQ FIFOs, DEF-M3-01 dispatched-first queue, 20 ms safe-side expiry, sniffer throughput 256 B/ms against a 92 B/ms line: no lost-stop or double-stop path found (apart from FWR-07). |
| Buffer bounds | Parser (512 B, `len ≤ 160` checked before use, memmove compaction); sniffer window 8 + 256; TX rings (≤ 255-B frames, all-or-nothing push, pop buffer 176 B); `link_respond` (body ≤ 159); GET_ALL_PARAMS page 143 ≤ 160; `on_input_edge` id ≤ 7; RX DMA ring (lap counting with pending-TC correction, overrun resync). |
| NVM corruption | Blank / invalid / valid slot states; magic, layout, header size, entry size, `entry_count ≤ 60` before any read; header and entries CRC-32; commit marker last; newest = modular max seq; a partial program or erase leaves the previous record; LOAD / boot rules with range and NaN checks; `hal_flash_program` address window and alignment guard. |
| IWDG coverage | Kick only when the tick advanced (a main-loop hang, a tick hang or an ISR storm at levels ≤ 4 all lead to a reset ≤ 90 ms); long window (≥ 2.79 s) only around the idle flash program, restored afterwards. |
| HardFault / NMI | Naked handler selects MSP/PSP and calls `hal_step_abort()` (RAM) first, then the `.noinit` record (magic), then `NVIC_SystemReset`; CSS NMI the same; record reported once (magic cleared). |
| Output reset state | PA0 / PA1 / PA4 stay Hi-Z (reset) until `hal_step_init()`: ODR is written before MODER; OC1 forced inactive and CC1P set before AF1. TRIP low, RATE high (80 SPS), SCK low before output, LED low, MH pins digital input without pull. The stm32duino `init()` / `hw_config_init()` touch no pins. |

## 4. Tests added (validator suites)

| File | Tests | Result at e600169 |
|---|---|---|
| `02_FW/test/test_val_steprace/test_main.c` (target `step_tim2.c` on a TIM2 register model; model copied from A's `test_impl_steptim` @e600169) | control + FWR-01 ×2 + FWR-02 ×2 | 1 PASS, **4 FAIL** (reproducers) |
| `02_FW/test/test_val_toctou/test_main.c` (unchanged `cmd.c` with a tick injected in the check window; A's fake seams) | control + FWR-03 ×2 | 1 PASS, **2 FAIL** |
| `00_System/tools/hil/tests/test_hil_review.py` | FWR-12 ×4 + control, FWR-13 | 1 PASS, **5 FAIL** |
| `02_FW/test/static/test_val_review_gen.py` | FWR-14 ×8 + control | 1 PASS, **8 FAIL** |

The reproducers are written to fail until the defect is fixed. They are evidence, not regressions. All other suites at e600169 pass:
- `pio test -e native`: 27 `test_impl_*` + 9 earlier `test_val_*` suites PASS;
- 34 / 34 earlier validator cases;
- HIL unit tests 39 / 39.

## 5. Recommended fix order

1. **FWR-03.** Single CRIT_TICK for check and execution, plus a level-based limit and wiring backup. This is the only High.
2. **FWR-01 + FWR-02 + FWR-04.** One rework of the start/halt protocol in `step_tim2.c`: unconditional generation bump, PRIMASK arm with a generation compare, bit-band or PRIMASK OPM. Coordinate with A's Task 1 (ISR optimisation), which touches the same functions. Re-run `test_val_steprace` / `test_impl_steptim`.
3. **FWR-06.** Bounded stack scan; needed for HG-18 / HG-30 `loop_max_us`.
4. **FWR-15, FWR-13, FWR-12** (Validator E). Before any board session under D-06.
5. **FWR-10, FWR-05, FWR-08.** ISR-budget and race hardening; confirm with the DWT sections at HG-18.
6. **FWR-07, FWR-09.**
7. **FWR-14** (Integrator).

## 6. Evidence (commands)

Commands ran in `scratchpad\validator-e-review\src` (a `git archive e600169` export), with private build dirs.

| Command | Result |
|---|---|
| `pio run -e nucleo_f446re` (`PLATFORMIO_BUILD_DIR=…\build_rel`) | SUCCESS; RAM 12 856 B (incl. 4 KB stack reserve), flash 45 556 B; `check_map` M-1…M-4 PASS |
| same, with `PLATFORMIO_BUILD_FLAGS=-fcallgraph-info=su` (`build_ci`) | `.ci` + `.su` call graph → `scratchpad\validator-e-review\stack_wc.py`; worst cases in OBS-R-2 |
| `arm-none-eabi-gcc -fsyntax-only <strict set>` on every `src/**/*.c` with the env includes and defines (release, `-DHW_MEAS=1 -DHW_MEAS_DWT=1`, `-DFW_AFE_SYNTHETIC=1`), and `-fanalyzer` | 0 warnings in our sources (OBS-R-1) |
| `arm-none-eabi-objdump -d` of `firmware.elf` | TIM2 ISR 0x08011d4c, `hal_step_start` 0x08011bc0, `hal_stack_free_min` 0x08011ee8, RAM handlers in `.data` (FWR-02 / 04 / 06, §3.4) |
| `pio test -e native` (MinGW 13.1 on PATH, private build dir; validator vectors from `gen_val_vectors.py`) | 27 impl suites PASS; validator suites PASS except the new reproducers (6 FAIL, §4) |
| `python -m pytest -p no:randomly 00_System/tools/hil/tests 02_FW/test/static/test_val_review_gen.py` | 41 PASS, 13 FAIL = the new reproducers |

## 7. Fix status (v1.3, 2026-10-10)

| ID | Status | By | Evidence |
|---|---|---|---|
| FWR-01…08, FWR-10, FWR-09 (FW part), OBS-R-1, OBS-M3-01 | **CLOSED** by the re-check of A's v0.8 fixes (§10.1, 2026-10-09); FWR-01 residual → FWR-17 | A / Validator E | §10 |
| FWR-16, FWR-17 (Low, new in the re-check) | **CLOSED** (§11.1, FW v0.8.1) | A | `test_val_toctou` FWR-16 PASS |
| OBS-RC-1, OBS-RC-2, OBS-RC-3, FWR-09 (pure check) | **CLOSED** (§11.1) | A | §11 |
| FWR-18 (Medium, new in the v0.8.1 re-check) | **CLOSED** (§12.1, FW v0.8.2) | A | `test_val_tickgen` PASS (FAILS on v0.8.1) |
| FWR-19 (Low, new in the v0.8.2 re-check) | **CLOSED** (§13.1, FW v0.8.3) | A | window ≈ 0.35 µs nominal / 0.7 µs conservative; HG-18 section 11 |
| FWR-20 (Medium, new in the v0.8.3 re-check; since v0.8.1) | **CLOSED** (§14.1, FW v0.8.4) | A | `test_val_tickgen::test_homing_with_segment_ends_inside_tick` PASS (FAILS on v0.8.1–v0.8.3) |
| FWR-21 (Low, new in the v0.8.4 re-check; also in v0.8.3) | **CLOSED** (§15.1, FW v0.8.5) | A | `test_val_haltwin` late-pulse cases PASS at 6 and 12 ticks / access (FAIL on v0.8.3 / v0.8.4) |
| OBS-RC-7 (0.5 µs guard margin, stretch / CLEAN halt_hw path) | **CLOSED** (§15.2, guard 1.5 µs in FW v0.8.5) | A | TC-SAF-FW-004-05 (c): margin factor 2.96 / 3.58 ≥ 2 (v0.8.4: 1.04, FAIL) |
| OBS-RC-8 (`test_fw_twin.py` ignores `BEND_TWIN_BUILD_DIR`) | **CLOSED** (§15.1) | Integrator | pytest 1 349 passed with a private twin dir |
| OBS-RC-9 (count source of the step ISR outside the timing budget, FW v0.8.6) | informational, no action | – | §16.5; `test_val_isrcore` characterisation |
| OI-FW-53 (`check_meas_build.py` safety symbol `step_isr_core`) | **done** (§16.1) | Validator E | TC-SYS-009-02 PASS on v0.8.6 |
| OBS-RC-6 (documentation: CRIT_HALT 0.2 µs statements) | **CLOSED** (§14.2 (c), FW_design v0.8.4, pinout §4; TC-FW-SW-002-01 corrected by Validator E) | A / Validator E | §14.2 (c) |
| FWR-14 | **fixed** by the Integrator (ICD v0.7.5 entry (e)): `gen_params.py` / `gen_protocol.py` validate identifiers, versions, integers and free text in `load()`; generated output byte-identical for the current YAML (both `--check` exit 0, hash 0xF8BCDCB8) | C | `02_FW/test/static/test_val_review_gen.py` 9/9 PASS ×2 (8 were FAIL); `00_System/tools/tests/test_gen_text_safety.py` 45 PASS |
| FWR-09 (ICD Appendix C wording) | **done** by the Integrator (ICD v0.7.5 entry (d)): STATIC_LEVEL sel PUL also needs the ENA output at the disabled level (§4.3, §5.6, Appendix C op 8, `protocol.yaml` MEAS_STATE / STATIC_LEVEL texts); the FW check stays with A (FWR-09 FW part) | C | both `--check` exit 0; integration 176 ×2 |
| FWR-12 | **fixed**, reviewed by C (§9) | Validator E | `test_hil_review.py` FWR-12 cases PASS |
| FWR-13 | **fixed**, reviewed by C (§9) | Validator E | `test_hil_review.py` FWR-13 cases PASS |
| FWR-15 | **fixed**, reviewed by C (§9) | Validator E | `test_hil_review.py` FWR-15 cases PASS; twin rehearsal |
| FWR-09 (HIL part) | **fixed**, reviewed by C (§9) | Validator E | `test_fwr09_hg06_disables_even_in_not_enabled` PASS |
| C-R1 (Medium, D-06), C-R2, C-R3, O-1, O-2 (treated as a defect), O-3, runbook note (§9) | **fixed** (round 2, §8.2), **re-reviewed by C (§9.2): confirmed fixed**; residual C-R4 (Low) fixed, see the next row | Validator E | `test_hil_review.py`: 34 new cases PASS. On a reconstruction of the round-1 code, 32 of them FAIL; the remaining cases are controls. |
| C-R4 (Low, §9.2) | **fixed** (2026-10-08). `table_lines()` skips lines indented by ≥ 4 columns (a tab counts to the next multiple of 4): indented code blocks; record rows are never indented. Runbook §2: "only rows in a normal, unindented table count". No other change, so no re-review. | Validator E | `test_indented_code_block_row_counts` (4 indents) and `test_cr4_check_approval_with_indented_example` PASS. With the skip disabled, the row is accepted, so the reproducer is valid. `pytest 00_System/tools/hil/tests`: **101 passed**, in fixed and in random order. |

Evidence for the round-1 HIL fixes (working tree on 2026-10-08; round 2 in §8.2):
- `python -m pytest 00_System/tools/hil/tests`: **62 passed**, both in fixed order and in random order. That is the 39 existing tests plus the 23 cases of `test_hil_review.py`.
- Twin rehearsal `hil_session.py --twin --quick --out <scratch>` (private `BEND_TWIN_BUILD_DIR`): all **43 steps** ran, rc 0. No ERROR, no REFUSED, no FAIL; the verdicts match the 2026-10-05 dry run.
- Resume in the same directory with `--only HG-10a,HG-12`:
  - the runner inserted S-ID → BENCH-ENTRY → HG-10a → BENCH-EXIT;
  - HG-12 ran after the GATE-LOAD of the same session;
  - rc 0.

## 8. Change list for the Integrator's review (HIL fixes FWR-12 / 13 / 15 / 09)

### 8.1 Round 1 (reviewed by C in §9)

Files changed: `00_System/tools/hil/{hil_link.py, hil_procs.py, hil_session.py, hil_report.py, HW_GATE_RUNBOOK.md}` and `tests/test_hil_review.py`.

**`hil_link.py`**
1. `recorded_row()` is new. A row counts only if all of the following hold:
   - the first cell equals the reference, with backticks / bold stripped;
   - the row has at least 3 cells;
   - the **last** cell is exactly one of the allowed states;
   - no cell matches `NEGATION_RE`: `?`, `n't`, not, no, never, none, unapproved, disapproved, denied, pending, withdrawn, revoked, rejected, proposed, tbd, tba, awaiting, expired, cancel(l)ed, draft, unconfirmed, "to be confirmed";
   - if `must_mention` is given, the row names that item.
2. `approval_row()` = `recorded_row(…, ("approved", "approved (po)"))` (FWR-12).
3. `check_accepted_open(item, ref)` is new. The item must match `HG-\d{2}[a-z]{0,2}`; the reference must match `OPEN_REF_RE` (e.g. `D-52`, `RR-HG-12-01`); the reference must be recorded as an accepted row that names the item (FWR-13).
4. `UID_RE` = 24 hex digits.

**`hil_procs.py`**
1. `new_session()`: session id (uuid4), mode, board UID, verified images.
2. `image_id(inf)` = `build|param_dict_hash`.
3. `Ctx(…, session=)`.
4. `ItemRec.session` holds a snapshot taken at the item's end: id, mode, board UID, image, image id.
5. `Ctx.result_of()` returns a result only if the session id, mode and board UID match **and** its image id was verified in this session. `Ctx.result_ok()` additionally requires no error and no FAIL / INCONCLUSIVE (FWR-13).
6. `check_identity()` is new (FWR-15):
   - enforces the dict hash and protocol (1, 0, 1), plus the `--board-uid` match on the board;
   - raises `BenchRefused` on any mismatch;
   - records the image identity (on the board, the image kind comes from the build suffix);
   - `verify_image()` uses it and refuses if the image on the board is not the one requested.
7. `hg06()` always sends DISABLE and refuses STATIC_LEVEL unless STATUS io shows `ENA_DISABLED` (FWR-09).

**`hil_session.py`**
1. Port sessions need `--board-uid` (24 hex digits); without it the run is refused before any port is opened.
2. `--accepted-open` entries are validated with `check_accepted_open` (target) before the port is opened; malformed entries are refused.
3. Session handling:
   - a run with `--from` / `--only` into the same mode, board and approval **resumes** the same session id (`session.json` keeps the id and the verified images);
   - any other run is a new session;
   - a new board session refuses an `--out` directory that already holds results.
4. `select_steps()` builds the step list (FWR-15):
   - S-00 is always first, or S-ID (identity re-check) when S-00 already completed in this session;
   - HG-10a is always wrapped by BENCH-ENTRY / BENCH-EXIT;
   - GATE-LOAD is inserted before phase 4 unless it already completed in this session;
   - unknown `--only` / `--from` ids are refused.
5. `s_start()` runs `check_identity()` before any operator question.
6. The session loop stops (rc 2) when an identity step (S-00, S-ID, IMG-*) has an error.
7. HG-10a runs only if `result_ok("BENCH-ENTRY")`; the phase-4 gate uses the session-bound `result_of`.
8. `session.json` is rewritten after every item, so the verified images survive a crash.

**`hil_report.py`**
- The report shows only results of the `session.json` session id; it states the number of ignored foreign results and lists the `session_id`.

**`HW_GATE_RUNBOOK.md`**
- §2 approval-row format and required `--board-uid`;
- §3 resume / new-session rules and recorded `--accepted-open` references;
- S-00 / S-ID rows;
- HG-06 always DISABLE.

**Review points for C**

| # | Point |
|---|---|
| a | The negation list is strict. A legitimate scope text containing "no" or "not" refuses the row; the Orchestrator then words the scope positively. |
| b | Resume needs `--from` / `--only`. A plain rerun is always a new session. |
| c | On the board, the image kind is inferred from the INFO build suffix (`-MEAS` / `-DWT` / none, CR-02 build_info.py). |
| d | Twin mode enforces dict hash and protocol, not the UID. |

### 8.2 Round 2 — fixes of C-R1…C-R3 and O-1…O-3 (for C's re-review)

Files changed: `00_System/tools/hil/{hil_link.py, hil_procs.py, hil_session.py, HW_GATE_RUNBOOK.md}` and `tests/test_hil_review.py`.

**C-R1 (revocation cancels).** `hil_link.py`:
- `ref_rows()` collects every table row whose first cell is the reference.
- `decide_rows()` accepts the reference only if **all** of its rows are clean: ≥ 3 cells, last cell an allowed state, no negation word.
- `recorded_in_files()` collects the rows of DECISIONS.md **and** STATUS.md together, so a `revoked` / `withdrawn` / `pending` row in either file cancels the record.
- `check_approval()` and `check_accepted_open()` both use it.
- Reproducers: `test_cr1_later_revocation_cancels_approval` (3 cases), `test_cr1_revocation_in_the_other_file_cancels`, `test_cr1_accepted_open_revoked_later`.

**C-R2 (fences and comments).** `hil_link.py`:
- `table_lines()` skips lines inside ```` ``` ```` / `~~~` fences (indented fences included; the closing fence must use the same characters) and inside `<!-- … -->` comments (single- and multi-line).
- Reproducers: `test_cr2_rows_in_fences_or_comments_do_not_count` (5 cases; a real row after a fence still counts) and `test_cr2_check_approval_with_fenced_example`.
- I did not add the "table with a header row" requirement. It would refuse the single-row records that the existing R-HIL-02 tests use.

**C-R3 (nothing to an unidentified device).**
- `Ctx.identified` is True only after `check_identity()` passed.
- The target image step resets it before the port is re-opened after a re-flash.
- `run_item()` sends **no** HALT / HALT_CLEAR when the item is an identity item (`P.IDENTITY_ITEMS`) or the device is not identified; it records a note instead.
- The session stops on such an error (`stop_reason()`), and `finally` closes the port.
- Ctrl+C / `abort` sends HALT only when the device is identified.
- Reproducers: `test_cr3_no_halt_clear_to_unidentified_device` (S-00, S-ID, IMG-REL, unidentified HG-28), `test_cr3_identified_device_still_gets_holding_stop`, `test_cr3_identity_step_error_stops_session`.

**O-2 (FAIL stops before motion).**
- `hil_session.stop_reason()` stops the session (rc 2) when S-00, S-ID, IMG-* or BENCH-ENTRY ends with an error, a FAIL or an INCONCLUSIVE verdict.
- After a failed BENCH-ENTRY, BENCH-EXIT runs first, if the device is identified. It restores the E-stop sense (P-8) and makes no move.
- `s_start()` raises `BenchRefused` when "DIP applied?" is answered no.
- MANUAL / PARTIAL verdicts (the twin, operator-open items) do not stop the session.
- Reproducers: `test_o2_fail_in_identity_or_bench_entry_stops` (6 cases), `test_o2_dip_not_applied_refuses`.

**O-1 (image kind).**
- `image_name_of()` requires the build suffix and FEAT_HW_MEAS to agree: `-MEAS` / `-DWT` need HW_MEAS, a plain build must not have it. Anything else is `"unknown"`, and `check_identity()` then refuses.
- A release request with a HW_MEAS build is therefore refused.
- Reproducers: `test_o1_image_kind_from_suffix_and_feature` (6 cases), `test_o1_release_requested_but_hw_meas_build_refused`.

**O-3.**
- `validate_selection()` checks the `--only` / `--from` ids in `main()` before the board-UID / accepted-open checks and before any transport is built.
- Reproducer: `test_o3_selection_validated_before_port` (3 cases, `SerialTransport` stubbed and never constructed).

**Runbook.** HW_GATE_RUNBOOK.md §2 and §3 describe:
- the per-word check (a `-NO` / `-TBD` / `-DRAFT` tag refuses its own row; use e.g. `-HG1`);
- revocation by adding a non-approving row;
- fences and comments are never records;
- the O-2 / O-3 / C-R3 behaviour.

**Evidence (working tree, 2026-10-08).**
- `pytest 00_System/tools/hil/tests`: **96 passed** in fixed and in random order (39 existing + 57 review cases).
- The 34 round-2 cases were run against a scratch reconstruction of the round-1 code: 32 FAIL, 25 PASS (those are the round-1 tests).
- Twin rehearsal `--twin --quick` (private `BEND_TWIN_BUILD_DIR`): 43 steps, rc 0. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A; no FAIL, ERROR or REFUSED. This is identical to C's §9 run.
- Resume `--only HG-10a,HG-12`: S-ID → BENCH-ENTRY → HG-10a → BENCH-EXIT → HG-12, rc 0.

**Points for C's re-review**

| # | Point |
|---|---|
| a | Any non-clean row for a reference cancels it. A deliberately re-issued approval therefore needs a **new** reference (new date or `-TAG`); the old rows stay as history. |
| b | `stop_reason()` treats INCONCLUSIVE in an identity item as a stop. |
| c | BENCH-EXIT after a failed BENCH-ENTRY runs only for an identified device. If the device is unidentified, the operator restores the sense by hand, following the runbook P-8. |

## 9. Integrator review of the HIL fixes (Implementer C, 2026-10-08)

Scope: the §8 change list — `00_System/tools/hil/{hil_link.py, hil_procs.py, hil_session.py, hil_report.py, HW_GATE_RUNBOOK.md}` and `tests/test_hil_review.py` (working tree, 2026-10-08). No hardware; no COM port opened (the CLI checks below ran with `SerialTransport` replaced by a stub that calls the real `check_approval()`). Reproducers: Integrator scratchpad `review_hil/test_c_review_hil.py` (13 cases, all reproduce as stated) and `review_hil/cli_refusals.py`.

**Verdict: FWR-12 / 13 / 15 and the FWR-09 HIL part are fixed as described. One Medium and two Low defects remain (C-R1…C-R3).** None of them opens a port without a recorded approval row. C-R1 can keep a withdrawn approval valid. C-R1 must be fixed before the first board session; C-R2 and C-R3 can follow.

**Port-opening paths.**
- `hil_link.open_serial()` is the only opener. `SerialTransport` and `reopen()` both go through it.
- `reopen()` (after a re-flash) re-runs `check_approval()`. It skips the operator re-typing the port, which is acceptable because it is the same port that was confirmed earlier.
- `hil_session.main()` checks the following before the port is opened:
  - `--board-uid` must be 24 hex digits;
  - `--accepted-open` must be a recorded row;
  - the approval (inside `open_serial`, before `import serial`).
- Stub run: every case was refused with rc 2 and nothing was opened:
  - no UID;
  - UID "12345";
  - unrecorded `D-06-GATE-<today>`;
  - no `--approved`;
  - `--accepted-open HG-12:whatever`.
- After the port is opened, the first step is always S-00, or S-ID on a resume. `check_identity()` sends GET_INFO and enforces the hash, the protocol and the UID before anything else. `--only` / `--from` cannot skip it. A mismatch raises `BenchRefused` and the session stops with rc 2.
- Exception: see C-R3.

**Review points of §8.**

| # | Integrator result |
|---|---|
| a | **Accepted.** Rows in the documented format pass: the runbook example `PO approved HW gate: board UID …, COM7, §6.8 bench procedure`, a scope with `§`, `…`, `N/A` and `(FW_test_plan v0.4.3 §6)`, bold / back-ticked cells, and `Approved (PO)` in any case. Rows are refused only by negation words, as documented ("no load above 20 kg", "board no. 2", "no-load tests"). A `-TAG` that is itself a listed word (`-NO`, `-NOT`, `-NONE`, `-TBD`, `-DRAFT`) also refuses its row. That is harmless (the Orchestrator chooses the tag), but HW_GATE_RUNBOOK §2 could say so. |
| b | **Accepted.** A plain rerun starts a new session. A target run into an `--out` that holds results is refused. A resume needs the same mode, UID and approval, and still starts with S-ID. Twin rehearsal of `--only HG-10a,HG-12` gave S-ID → BENCH-ENTRY → HG-10a → BENCH-EXIT → HG-12 (GATE-LOAD from this session), rc 0. |
| c | **Accepted, with observation O-1.** |
| d | **Accepted.** The twin has no PO-named board; hash and protocol are enforced. |

**Findings.**

| ID | Severity | Finding | Reproducer | Fix hint |
|---|---|---|---|---|
| C-R1 | **Medium** (D-06) | A later row that **revokes or withdraws** the same reference does not cancel the approval. `recorded_row()` returns the first approved row and ignores other rows whose first cell is the reference, e.g. `\| D-06-GATE-… \| … \| PO withdrew the approval \| revoked \|` appended under the approved row. The same applies to `--accepted-open` rows. | `test_C1_later_revocation_row_is_ignored` | Collect every row whose first cell equals the reference. Refuse unless there is exactly one, it is approved and no other row for that reference exists, or unless the newest row decides. |
| C-R2 | Low | An example or template row inside a fenced code block (```` ``` ````) in DECISIONS.md / STATUS.md counts as a recorded approval. `check_approval()` passes with such a row if its date is current. | `test_C2_example_row_in_a_code_fence_counts`, `test_C2_check_approval_with_fenced_example` | Skip lines inside ```` ``` ```` fences and `<!-- -->` comments, and accept rows only in a table with a header row. |
| C-R3 | Low | `run_item()` handles any non-`BenchRefused` exception with **HALT + HALT_CLEAR**. This also happens in S-00 / S-ID / IMG-* before the identity was verified. If GET_INFO times out (wrong device or port), HALT_CLEAR goes to an unidentified device and can clear a halt latch that someone set intentionally there. | `test_C3_identity_exception_sends_halt_clear_to_unidentified_device` | Send nothing, or HALT only, from identity items and before the first successful `check_identity()` of the session. Never send HALT_CLEAR there. |

**Observations (no defect).**
- **O-1.** `image_name_of()` returns the *requested* image for a board build without a `-MEAS` / `-DWT` suffix but with FEAT_HW_MEAS = 1 (`test_C4_…`). Only an inconsistent build can do that. `verify_image()` then records a FAIL on the build-suffix check, but see O-2. Suggestion: return a distinct `"unknown"` so that `BenchRefused` follows.
- **O-2.** Pre-existing, not in §8. The session stops after an identity item only on an *error*, not on a FAIL verdict. Two consequences:
  - S-00's "DIP applied before the first motion" answered **No** is a FAIL, and the run continues into P2-ENTRY / HG-28 motion;
  - IMG-* variant or suffix FAIL checks do not stop the run either.

  Suggestion: raise `BenchRefused` for DIP = No, and stop on FAIL / INCONCLUSIVE of identity items in target mode.
- **O-3.** `--only` / `--from` ids are validated after the port is opened. Nothing is sent and the port is closed again. They could be validated before opening.
- **FWR-09 HIL part.** It is consistent with ICD v0.7.5 Appendix C op 8: STATIC_LEVEL with sel PUL needs NOT_ENABLED and ENA at the disabled level. `hg06()` always sends DISABLE and refuses without `ENA_DISABLED`.

**Evidence.**
- `python -m pytest 00_System/tools/hil/tests`: 62 passed, twice.
- Twin rehearsal `hil_session.py --twin --quick --out <scratch>/hil_quick` (private `BEND_TWIN_BUILD_DIR`): 43 steps, rc 0. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A; 0 FAIL, 0 ERROR, 0 REFUSED.
- Resume `--only HG-10a,HG-12` into the same directory: rc 0, step order as in point b.
- Reproducers: 13 / 13 as stated.

### 9.2 Integrator re-review of round 2 (Implementer C, 2026-10-08)

Scope: §8.2 (C-R1…C-R3, O-1…O-3, runbook note) in the working tree of 2026-10-08. No hardware; no COM port opened. For the CLI checks, `SerialTransport` was replaced by a stub that calls the real `check_approval()`. Reproducers: Integrator scratchpad `review_hil/test_c_review_hil_r2.py`, 31 cases:
- the 13 round-1 cases replayed, with the expectation flipped where a defect was fixed;
- the round-2 questions below.

**Verdict: C-R1, C-R2, C-R3, O-1, O-2 and O-3 are fixed as described; review points a–c are accepted. One Low residual, C-R4, remains; it does not block a board session.** No path opens a port without a recorded, uncancelled approval row and a valid `--board-uid`. Nothing beyond GET_INFO and PING reaches a device before `check_identity()` passed.

**Round-1 reproducers replayed.**
- **C-R1:** a later `revoked` row now cancels the approval. So do a `withdrawn` row in STATUS.md (cancelling a DECISIONS.md row) and a revoked `--accepted-open` row.
- **C-R2:** a fenced example row no longer counts, neither in `approval_row()` nor in `check_approval()`. An unclosed fence hides all later rows (fail-safe).
- **C-R3:** an S-00 GET_INFO timeout sends nothing. An unidentified HG-28 error sends nothing. An identified device still gets HALT + HALT_CLEAR.
- **O-1:** suffix and FEAT_HW_MEAS must agree; every inconsistent combination maps to `unknown`.
- **Documented-format rows:** the four variants of §9 are still accepted.
- **Negation wording:** the "no …" scopes and the `-NO` tag are still refused, as documented.
- **CLI refusals** (stub): rc 2 and no port for each of:
  - missing UID;
  - malformed UID;
  - unrecorded approval;
  - no `--approved`;
  - bad `--accepted-open`;
  - unknown `--only` id;
  - bad `--from` id.

**Question from the Orchestrator.** A correctly written DECISIONS.md row `| D-06-GATE-… | … | approved (PO) |` (in a table with a header) is **still accepted** when STATUS.md mentions the same reference in any of these ways:
- prose;
- a non-table line (inline code);
- a table row where the reference is not in the first cell (e.g. the existing STATUS row style `| M3-C3 PO approval … (+ D-06-GATE-…) | … |`);
- a block-quoted row;
- an HTML-commented row.

It is **cancelled** by a STATUS.md table row whose **first** cell is the reference and whose last cell is not an approved state, e.g. a session log row `| D-06-GATE-… | HW gate session | E, PO | in progress |`. That is fail-safe and is what HW_GATE_RUNBOOK §1.2 documents. Advice to the Orchestrator: never use the reference as the first cell of any other table.

**Review points of §8.2.**

| # | Result |
|---|---|
| a | **Accepted.** Any non-clean row cancels the reference. Re-approval uses a new reference (date or neutral `-TAG`), so the history stays readable. |
| b | **Accepted.** `stop_reason()` stops on ERROR, FAIL and INCONCLUSIVE of S-00, S-ID, IMG-* and BENCH-ENTRY. MANUAL / PARTIAL continue (twin and operator-open items). DIP = No now raises `BenchRefused` in S-00. |
| c | **Accepted.** BENCH-EXIT after a failed BENCH-ENTRY runs only with `ctx.identified`. It sends ENABLE, the E-stop press check and ESTOP_CLEAR, and no motion command. An unidentified device at that point is practically impossible: identity is lost only in an IMG step, and the session stops there. |

**Residual finding.**

| ID | Severity | Finding | Reproducer | Fix hint |
|---|---|---|---|---|
| C-R4 | Low | Residual of C-R2: a row inside a Markdown **indented code block** (4 spaces or a tab, after a blank line) still counts as a record. An example written that way in DECISIONS.md / STATUS.md with a current date passes `check_approval()`. Neither file has indented table rows today. | `test_indented_code_block_row_counts` | In `table_lines()`, skip lines indented by ≥ 4 spaces or a tab (record rows are never indented), or require the row to belong to a table with a header and separator row. |

**Observation.** After a re-flash, `image_step()` sends PING to the re-opened port before `verify_image()` identifies the device. PING changes no state, so this is acceptable and no change is needed.

**Evidence.**
- `python -m pytest 00_System/tools/hil/tests`: **96 passed**, twice.
- Integrator reproducers: 31 / 31 as stated.
- Twin rehearsal `hil_session.py --twin --quick --out <scratch>/hil_quick2` (private `BEND_TWIN_BUILD_DIR`): 43 steps, rc 0. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A; 0 FAIL, 0 ERROR, 0 REFUSED. This is identical to §9 and §8.2.
- Resume `--only HG-10a,HG-12` into the same directory: S-ID → BENCH-ENTRY → HG-10a → BENCH-EXIT → HG-12, rc 0.

## 10. Re-check of A's Task 1 (ISR bounds) and the FWR-01…10 fixes (Validator E, 2026-10-09)

**What was checked.** Working tree snapshot of 2026-10-09 17:49, copied to the role scratchpad (`rc2`). Its hash is `sha256` over `02_FW/{src,include,tools,platformio.ini}` = `e4f2e63b…`. It contains:
- FW_design v0.8, pinout v0.6 and `02_FW/tools/isr_wcet.py`;
- ICD v0.7.5;
- C's OI-FW-47/48: App. C DWT section 23, the 0xFFFFFFFF PWM sentinel, and the twin's idle `stop_gen` bump.

No hardware was used, nothing was committed, and all build directories were private.

**Verdict: GO WITH CONDITIONS.**

| Area | Result |
|---|---|
| FWR-01…10, OBS-R-1, OBS-M3-01 | All **CLOSED**; FWR-01 has a residual on the tick path (FWR-17) |
| FWR-14 (Integrator) | **CLOSED** |
| New finding FWR-16 (Low) | ENABLE can re-drive ENA after an E-stop edge |
| New finding FWR-17 (Low) | Gate capture of the tick path placed after the input fold |
| NFR-007 | Stays **open**: the verdict is HG-18 (D-51). The static bounds predict sections 3/4 and 23 above 1 µs (OI-E-RC-01). |

### 10.1 Verdict per finding

| ID | Verdict | Evidence (mine, independent of A's) |
|---|---|---|
| FWR-01 (thread / HAL path) | **CLOSED** (residual FWR-17, tick path) | See below. |
| FWR-02 | **CLOSED** | See below. |
| FWR-03 | **CLOSED** | See below. |
| FWR-04 | **CLOSED** | Every CR1 write that can race a level-0/1 halt is either a constant (halts, OPM-final branch, arm) or runs under PRIMASK (OPM in the ISR and in `hal_step_arm_last`, `set_period_now`). The only remaining RMW (`hal_step_start` line 205, before the arm) clears CEN / OPM and cannot re-enable the counter. A level-0 reaction inside the step ISR was analysed path by path: it never restarts the counter, and it does not double-count, because UIF was already cleared by the ISR. |
| FWR-05 | **CLOSED** | `per_running()` / `latch_running()` / `s_late*` decide on the interval that is really running. A's register-model test reproduces the pending-update stretch: no runt, exact count. I reviewed the code paths (ISR, `set_period`, `set_period_now`, halts). |
| FWR-06 | **CLOSED** (target confirmation at HG-18 `loop_max_us`) | The scan is bounded to 256 words per call. The running low-water pointer only moves down and converges within ⌈usage / 1 KB⌉ calls; the reported value can lag by a few calls. |
| FWR-07 | **CLOSED** | See below. |
| FWR-08 | **CLOSED** | All runtime IMR mask / re-arm writes now go through the bit-band alias (`exti_imr_set`), which is one locked bus read-modify-write. The remaining `EXTI->IMR` RMWs are in `exti_init` / `exti_start` at boot, before the safety IRQs are enabled. |
| FWR-09 (FW part) | **CLOSED** | `cmd.c` refuses STATIC_LEVEL sel PUL unless ENA is at the disabled level (E_STATE MEAS_STATE), consistent with ICD v0.7.5 App. C op 8. The pure check follows with state_schema 4. |
| FWR-10 | **CLOSED** (residual measured at HG-18) | See below. |
| OBS-R-1 | **CLOSED** | `build_src_flags` now has `-Wconversion -Wsign-conversion`. I built 5 images (release, meas, meas_dwt, synth, debug): rc 0, **0 warnings** in our sources, `check_map` M-1…M-4 PASS (13 vector slots incl. EXTI3; RAM list incl. EXTI3). |
| OBS-M3-01 | **CLOSED** (target check at HG-29 / HG-08) | URS = 1, so UIF is set only on an overflow; a capture after an overflow records 0xFFFFFFFF. C mirrors the same sentinel in the twin. A period word of 0xFFFFFFFF makes my HIL period checks FAIL, which is fail-safe. |
| FWR-14 (Integrator) | **CLOSED** | `test_val_review_gen.py` 9/9 PASS on rc2 (8 reproducers + control). |

**FWR-01 (thread / HAL path).**
- An idle `stop_now` / `abort` / `step_estop_reaction` now increments the stop generation.
- `motion_gate_capture()` runs before the check in `cmd_execute()`. It runs again inside CRIT_TICK when a tick ran in between.
- `hw_start()` rechecks right after the atomic arm.
- `test_val_steprace` (target `step_tim2.c` on the register model, adapted to v0.8): FWR-01 ×2 PASS. They FAILED at e600169.

**FWR-02.**
- `hal_step_start()` arms CCMR1, `s_running` and CEN with constant writes inside one PRIMASK section. A level-0/1 halt now lands either before that section (idle halt: the generation is bumped and the recheck stops the timer, CNT ≪ the first compare) or after it (a normal halt).
- `test_val_steprace` FWR-02 ×2 PASS: 0 updates, 0 PUL edges, count = completed pulses. At e600169 they FAILED with 7 phantom updates.
- The injection point is now just before the arm section (§4 header of the suite).

**FWR-03.**
- State-changing commands run check + execute under one CRIT_TICK. If a tick ran since the check, the context is rebuilt and the command checked again.
- `test_val_toctou`: END and PAUSE folded inside the check window give 0 PUL edges (PASS; 36 edges each at e600169). The control (fold before the check) gives E_STATE BLOCK_LIMIT.
- Non-tick paths:
  - Load-limit trips and limit / E-stop edges between check and execution bump the generation, so `hw_start()` stops the start before its first edge (FWR-01 path).
  - A PAUSE press that is not yet folded is equivalent to a press just after the command: the motion starts and gets the controlled stop at the next fold.
  - The sniffer, the PAUSE button and HALT only stop motion; they never start it.
- The new level-based tick backup (a motion toward an active, latched switch; both switches active with LIMIT_WIRING latched) cannot stop a legitimate move:
  - `cmd_check` refuses every start toward a latched / active switch;
  - HOMING is excluded (its expected START approach and the short MOVE_TO_ZERO move are handled by the homing machine);
  - motion away from the switch has `motion_dir() != toward`;
  - STOPPING is excluded.
  - It does stop a tick-initiated jog-reversal restart toward a pressed switch, which is the intended behaviour.
- See also FWR-16.

**FWR-07.**
- New reproducer `test_fwr07_parked_jog_reversal_resolved_by_hold_timeout` in `test_val_toctou`. A JOG is parked by a sniffed STOP, the JOG is reversed while still parked, and the STOP is never dispatched (ticks only).
- At e600169 the motion stays MS_JOG without motion after the 20 ms hold timeout (FAIL). In v0.8 it is re-planned, parked again and discarded by the hold timeout (PASS), with no PUL edge.

**FWR-10.**
- The position-independent soft-double constants are computed once outside CRIT_MOTION (`redit_prep`). `wait_step_isr()` aligns the next attempt to an update (bounded spin ≤ 2000 iterations, ≈ 55 µs; also possible in the tick context).
- The rare in-section fallback still runs the position-dependent part: `ramp_stop_dist` (double), `accel_from_last` (double), up to 2 VSQRT. My estimate is ≈ 1–3 µs, above the NFR-007 1 µs window but far below the half step period at ≤ 100 kHz (no false STEP_FAULT).
- DWT section 13 at HG-18 decides.
- Ramp equivalence: see (4) in §10.2.

### 10.2 The Orchestrator's specific checks

**(1) FWR-03 on every state-changing path.**
- Closed for the thread path (see FWR-03 in §10.1).
- Tick-initiated starts (jog-reversal restart, homing segments) do not go through `cmd_check`. Only ALM's start-block is not re-applied there; this is minor, because SAF-FW-026 does not stop a running jog. A restart toward a pressed switch is stopped by the new backup.
- The backup does not stop legitimate moves (see FWR-03 in §10.1).
- New: FWR-16 (ENABLE) and FWR-17 (gate capture of the tick path); see §10.3.

**(2) The deferred EXTI3 callback.**
- The level-0 handler does the fixed reaction, ORs the edge into `s_estop_ev` and pends EXTI3 (level 1). EXTI3 takes the flags under PRIMASK, which is safe against the level-0 writer.
- It tail-chains before any level ≥ 2 handler, the tick or the thread. A level-1 handler that was pre-empted (EXTI0/1/9_5) finishes first. It only records with the current count and state, as before.
- No level-1/2 handler re-arms or re-enables after the reaction:
  - the step ISR finds UIF clear, or finds CEN = 0 and does not restart (FWR-04 analysis);
  - the AFE ISR only halts;
  - a thread start in that window is stopped by the generation recheck.
- The main loop can re-enable ENA through an ENABLE command in the check window: that is FWR-16. This was already the case at e600169; it is not new with the deferral.
- Until the tick fold, `ena_on` and the motion state are unchanged. That was the same before: the core state always changed only in the tick.
- **Reset:** NVIC enable and pending bits are cleared by reset. `exti_start()` clears the EXTI3 pending bit and enables EXTI3 **before** EXTI15_10. EXTI line 3 is never unmasked (no RTSR / FTSR / IMR bit), so the PA3 = USART2 RX default mapping cannot pend it.
- **If EXTI3 were masked or lost** (flash operation: callback skipped by design; a HANG ISR1 in the measurement image):
  - the fixed reaction is unaffected;
  - the tick's level check (`estop_open && (!lat.estop || ena_on || motion_active())`) trips within ≤ 1 ms, which is fail-safe.
- **Equivalence:** I ran my own sweep of `step_estop_reaction()` vs `hal_step_abort()` + `hal_ena_set(false)` in the states A's 857-position sweep does not cover. **All 926 cases pass**, with the same output, count, CEN, OC mode, BSRR word, `g_meas_static` and one generation bump. States covered:
  - last period armed (OPM);
  - a CLEAN halt completing through OPM;
  - a STATIC_LEVEL hold while idle;
  - `motion.ena_invert` = 0 and 1.

**(3) `isr_wcet.py` and HG-18.**
- On my own release build the tool reproduces A's figures exactly:
  - E-stop 1.33 / 1.06 µs;
  - EXTI3 1.88 / 1.26 µs;
  - limits 2.90 / 2.09 µs;
  - PAUSE 1.62 µs;
  - step ISR 3.52 / 2.29 µs (old method 0.67 / 2.26 µs).
- The method is sound as a bound relative to its cost model: all paths are walked, only infeasible outcomes the walk can prove are pruned, and loops are refused.
- The constants are plausible and conservative: no write-buffer credit, an ART miss at every taken target, and the FP context charged whenever the path executes FP.
- One unsoundness, **OBS-RC-1**: an access through a register of unknown value is costed as SRAM (+0). The tool lists one such access: `limit_line` → `exti_imr_set` bit-band store to EXTI (APB2, a locked read + write, ≈ +12 cycles). The bound for sections 3/4 is therefore low by ≈ 0.07 µs. Fix hint: cost unknown-base accesses at the APB1 worst case.
- **HG-18 updated** (`hil_procs.py`, `hil_budget.py`, FW_test_plan v0.4.5 HG-18 row). Sections **1, 2, 3/4 and 23** are read (`DWT_N` = 24) and judged on the total: max − stamp overhead + 27 cycles entry / exit (§6.1 rule 4). Pass criteria:

  | Section | Criterion |
  |---|---|
  | 1 (step ISR) | ≤ 2 µs; step CPU (mean + 27 cycles) × 50 kHz ≤ 15 % |
  | 2 (E-stop handler) | ≤ 1 µs |
  | 3/4 and 23 (level-1 handlers that mask levels 1–2 while they run) | ≤ 1 µs each: NFR-007 window rule, pinout §4 "≤ 1 µs each" |
  | 11–13, 16–18 | ≤ 1 µs |

  - The `isr_wcet.py` figures are attached as informative notes. The measured / nominal ratios are recorded as `isr_wcet_calibration` (OI-FW-46).
  - Twin rehearsal: HG-18 lists all twelve criteria as NOT MEASURED (no DWT in the twin), as expected.
  - New HIL test: `test_hg18_measures_nfr007_sections_with_entry_exit`.

**(4) Ramp equivalence, re-run independently.**
- My own differential harness (`scratchpad/validator-e-review/diff_ramp`, not A's) compiles the e600169 `ramp.c` and the v0.8 `ramp.c` side by side (MinGW gcc 13, −O2 and −O0).
- Each scenario uses random parameters: spm 100…100 000, v, a, `max_step_rate`, pulse widths, n ≤ 200 000. Edits are injected at event rates 1/997, 1/31, 1/5 and 0:
  - `set_total`;
  - `set_speed` (old) vs `ramp_set_speed` **and** `ramp_speed_k + ramp_set_speed_k` (new);
  - `stop` vs `ramp_stop` **and** `ramp_stop_k`;
  - the motion.c stretch edit.
- Every period of `ramp_next` and `ramp_next_inl` is compared, along with the state (rem, gen, last, prev, carry, mono, bit-exact).
- Result: 7 runs, **≈ 22.4 M `ramp_next` calls, 0 mismatches**.
- The negative control (a one-step change in `ramp_stop_k`) gave 45 mismatches in 0.76 M calls, so the harness does detect a difference.

**(5) OI-FW-49.**
- `test_val_steprace`:
  - injects `step_estop_reaction()`;
  - resets `s_late`, `s_late_cur` and `s_init` per case;
  - follows the v0.8 `hw_start` order;
  - has the new equivalence test (§10.2 (2)).
- `test_val_toctou` is unchanged in its renames; it has new FWR-16 and FWR-07 cases.
- The validator vectors were regenerated for ICD 0.7.5 (`H 0.7.5 4173126840`). They are not checked in: they live in the build area `02_FW/.pio/val_vectors`, regenerated in the snapshot.

### 10.3 New findings

| ID | Sev | Finding | Anchor (working tree 2026-10-09) | Reproducer | Fix hint | Owner |
|---|---|---|---|---|---|---|
| FWR-16 | Low | An E-stop edge between the check of **ENABLE** and its execution (no tick in between, so no re-validation). The fixed reaction disabled ENA; `motion_enable()` then drives it back to "enabled". It stays enabled until the next tick's level check (≤ 1 ms + the rest of the CRIT_TICK section) disables it again. SAF-FW-005 b ("ENA disabled within 1 ms") is met only marginally. Physically the D-42 NO contact keeps the driver disabled. This was already present at e600169 and is not covered by FWR-03 (which re-validates only after a tick). | `core/cmd.c` `cmd_execute()` / `cmd_run()` ENABLE; `core/motion.c` `motion_enable()` | `test_val_toctou::test_fwr16_enable_after_estop_edge_in_check_window` FAILS (rc2 and e600169); TC-SAF-FW-005-02 | Inside the CRIT_TICK section, also re-validate when `hal_step_stop_gen()` moved since the gate capture. For ENABLE, refuse (E_STATE BLOCK_ESTOP) if the live E-stop pin reads open or an unfolded E-stop record is pending. | A |
| FWR-17 | Low | Gate capture of the tick path. `motion_gate_capture()` runs at the start of `motion_tick()`, after `safety_tick()` took the input records and after `link_tick()` / `afe_tick()`. An E-stop or limit edge in that window bumps the generation **before** the capture and is not folded until the next tick. A tick-initiated segment start in the same `motion_tick()` (jog-reversal restart, next homing segment) therefore starts, and is stopped ≤ 1 ms later. This exceeds SAF-FW-005 a (100 µs; ENA already disabled) or SAF-FW-002 (200 µs) in a window of tens of µs per tick, only when such a restart coincides. | `core/app.c` `core_tick_1ms()` order; `core/motion.c` `motion_tick()` | Analysis. Host-unobservable today: `test/common_impl/fake_hal.c` does not bump the generation on an idle halt (see OBS-RC-2). | In `hw_start()`, also refuse / stop when an unfolded active-edge record of E-stop / START / END is pending. Moving the capture before the fold would instead abort homing restarts after an expected edge. | A |

**Observations.**
- **OBS-RC-1** (isr_wcet unknown-base costing): see §10.2 (3).
- **OBS-RC-2:** A's `fake_hal` still lacks the idle `stop_gen` bump that the target (v0.8) and the twin (C, OI-FW-48) now have. Core host tests cannot exercise FWR-01 / FWR-17. Request to A: mirror it in `common_impl`.
- **OBS-RC-3** (measurement images only): DIAG_MEAS STATIC_LEVEL does a CCMR1 read-modify-write in the thread. An E-stop reaction between its read and write can be undone (PUL forced active again). Because of the FWR-09 rule, ENA is disabled in that case. Info.
- **OBS-RC-4:** `cmd_execute()` now holds CRIT_TICK for every gated command, including SET_PARAM, LOAD / DEFAULT and FAULT_CLEAR. Tick and link-DMA latency become the command's handler time. Report DWT section 15 at HG-18; it is not budgeted.

**Open items.**
- **OI-E-RC-01 (Orchestrator):** please confirm that NFR-007's "sections that mask levels 0–2 ≤ 1 µs" applies to the level-1 handlers (sections 3/4, 23), as in pinout §4. The v0.8 static figures (nominal 2.09 / 1.26 µs) predict an HG-18 FAIL on that reading. D-51 left the budget unrestated.
- **OI-E-RC-02 (A):** FWR-16, FWR-17, OBS-RC-1 and OBS-RC-2.

### 10.4 Evidence

| Run | Result |
|---|---|
| Builds (`rc`, private `PLATFORMIO_BUILD_DIR`), 5 images | rc 0; 0 warnings in our sources; `check_map` PASS. rc2 differs from rc only by comment text in `proto_gen.h`. |
| `isr_wcet.py <my firmware.elf> --old` | Figures as above (§10.2 (3)); 1 unknown-base access. |
| `pio test -e native` on rc2 (MinGW, private build dir; validator vectors regenerated for 0.7.5) | 38 suites; 227 cases, **226 PASS**; the only FAIL is the FWR-16 reproducer. `test_val_toctou` re-run with the FWR-07 case: 7 cases (incl. the FWR-07 case), 6 PASS + FWR-16 FAIL. |
| The same reproducers on the e600169 export | FWR-03 ×2, FWR-07 and FWR-16 FAIL. |
| Validator suites in seeded random order (`VAL_SEED=20261009`) | 46 cases, 45 PASS, FWR-16 FAIL. |
| `pytest 02_FW/test/twin` (private `BEND_TWIN_BUILD_DIR`, rc2 twin incl. C's OI-FW-47/48) | **246 passed**. Together with `00_System/tools/hil/tests` 101 and `test_val_review_gen.py` 9: 356 passed. Repo HIL tests incl. the new HG-18 test: **102 passed**. |
| Twin rehearsal `hil_session.py --twin --quick` (rc2) | 43 steps, rc 0. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A; no FAIL / ERROR / REFUSED. HG-18 shows the 12 DWT criteria NOT MEASURED. |
| `test_val_steprace` (rc2) | 6 / 6 PASS (incl. 926-case extra-state equivalence). |
| Ramp differential | ≈ 22.4 M calls, 0 mismatches; negative control 45 mismatches. |

### 10.5 Follow-ups after the re-check (2026-10-09)

**D-52 (OI-E-RC-01 decided).** The HG-18 criteria are updated in `hil_procs.py` (`DWT_BUDGETS`, `DWT_LEVEL1`, `step_plus_level1()`), and in FW_test_plan v0.4.6 HG-18:

| Section(s) | Criterion |
|---|---|
| 2: E-stop reaction (level 0) | ≤ 1 µs |
| 1: step ISR | ≤ 2 µs; CPU ≤ 15 % at 50 kHz |
| 3/4, 5, 23: level-1 handlers | ≤ 2.5 µs each |
| Derived: section 1 + largest level-1 handler | ≤ 5 µs |
| 11–13, 16–18: critical sections | ≤ 1 µs |

- Handler sections are judged on the total, including the 27 cycles of exception entry / exit (0.15 µs).
- HIL tests: 103 passed (new `test_hg18_d52_step_plus_largest_level1`).
- Twin `--quick` rehearsal: 43 steps, rc 0, 21 / 13 / 5 / 4. HG-18 lists the new criteria as NOT MEASURED in the twin.

**Check vectors, state_schema 4 (ICD v0.7.5 (h)).**
- `gen_val_vectors.py` requires schema 4 and maps `ena_on` to s[22]. It also emits the 32 `hw_meas_vectors` as `CM` lines.
- `test_val_check` reads 23 state words (`c.ena_on = s[22] != 0`) and replays CV with hw_meas = 0 and CM with hw_meas = 1: **575 vectors PASS** on a snapshot of the live tree, with A's `cmd_ctx_t.ena_on` and FWR-09 in the pure check.
- Validator Unity suites: 47 cases, 46 PASS; the only failure is the FWR-16 reproducer, which stays red until A's fix.
- Vectors regenerated for ICD 0.7.5 at the default location `02_FW/.pio/val_vectors` (`H 0.7.5 4173126840`, N 575). This is a git-ignored build area by design (`val_io.h`), so there is nothing to commit.
- No validator twin test replays `hw_meas_vectors`, so the DISABLE-before-STATIC_LEVEL rule needs no twin change. HG-06 already sends DISABLE (FWR-09 HIL part).

## 11. Re-check of FW v0.8.1 (FWR-16, FWR-17, OBS-RC-1…3, FWR-09 in the pure check) (Validator E, 2026-10-09)

**Inputs.** Working-tree snapshot of 2026-10-09 18:32 (`rc4`): `sha256` over `02_FW/{src,include,tools,platformio.ini}` = `792c11c5…`.

Changed since §10:
- `core/{app,cmd,motion}.c`, `hal/f446/meas_f4.c`;
- `test/common_impl/fake_hal.{c,h}`;
- `tools/isr_wcet.py`.

No hardware; nothing committed; private build / twin directories.

**Verdict: GO WITH CONDITIONS.**
- CLOSED: FWR-16, OBS-RC-1, OBS-RC-2, OBS-RC-3, and FWR-09 (pure check).
- FWR-17's own window is closed. **New FWR-18 (Medium) introduced by the FWR-17 fix:** an expected homing edge between the new capture point and the input fold makes homing fail with HOME_WIRING. It must be fixed before the HW gate.

### 11.1 Verdict per item

| ID | Verdict | Evidence |
|---|---|---|
| FWR-16 | **CLOSED** | Inside CRIT_TICK, `cmd_execute()` now re-checks when a tick ran, when the stop generation moved, or when an E-stop / START / END record is still unfolded (`ctx_with_pending()`). `test_val_toctou::test_fwr16_enable_after_estop_edge_in_check_window` now **PASSES**; it failed at e600169 and in v0.8. The FWR-03 and FWR-07 cases still pass. |
| FWR-17 | **CLOSED** for its own window | `motion_gate_capture()` is now the first statement of `core_tick_1ms()`. An edge after the fold now moves the generation after the capture, and `hw_start()` stops the restart. **But the new order creates FWR-18 (§11.3).** |
| OBS-RC-1 | **CLOSED** | Unknown-address accesses are now costed as APB1 (+12). A bit-band store to EXTI is a locked APB2 read + write, also ≈ 12 cycles, so the bound is sound. On my own build: limits 2.98 / 2.13 µs, step ISR 3.53 / 2.29 µs, EXTI3 1.91 / 1.26 µs, PAUSE 1.67 / 1.09 µs, E-stop 1.33 / 1.06 µs (conservative / nominal); step + level-1 6.51 / 4.42 µs. These match A's figures. Per D-51 / D-52, HG-18 decides. |
| OBS-RC-2 | **CLOSED** | `fake_hal` bumps `stop_gen` on an idle halt (it now matches the target and the twin); `fake_peek_hook` was added. My FWR-18 reproducer uses this fake. |
| OBS-RC-3 | **CLOSED** | STATIC_LEVEL check + constant CCMR1 write + flag now run in one PRIMASK section (`meas_f4.c`), and the HANG path writes CCMR1 as a constant. An E-stop reaction now lands wholly before or after the section. If it lands before, PUL is driven into a driver already disabled by the reaction (measurement image only); that is acceptable. |
| FWR-09 (pure check) | **CLOSED** | See (c) in §11.2. |

### 11.2 The Orchestrator's questions

**(a) Can the pending-record rule refuse a legitimate command forever? No.**
- The rule exists only in the re-check (`ctx_with_pending()`). A record's `act_edge` is cleared by the next `take()` in the tick, so a "pending" input lives at most one tick. The command is refused once at most: the NACK names E_STATE BLOCK_LIMIT / BLOCK_ESTOP, and the PC decides again (IF-005). A stale record cannot persist: it is cleared every tick, and during an NVM SAVE no commands are dispatched.
- **START / END while homing onto the switch:** HOME is exempt from BLOCK_LIMIT (`cmd_check`). Commands issued during homing are STOP / HALT / PAUSE / JOG 0 / clears, which the limit block does not affect.
- **A move away from a latched limit:**
  - only motion *toward* the switch is blocked;
  - a pending edge of the switch being left cannot occur, because the line stays masked until the stable release;
  - a bounce after the re-arm is a real new press.
- **E-stop:** a pending open edge refuses every motion, ENABLE and ESTOP_CLEAR for at most one tick. That is the intended FWR-16 behaviour.
- **Moved generation:** a moved generation alone (e.g. the OPM end of the previous move) only causes a recomputation, never a refusal by itself.

**(b) Does capturing the generation before `safety_tick()` affect the tick's own halts? Yes: FWR-18.**
- The tick's own core halts (sniffer stop, AFE stale, limit backup, link watchdog, …) all put the motion into "stopping", so no segment is restarted in that tick. That part of A's argument holds.
- It does not hold for an **expected** edge whose HAL fixed reaction halts the timer (START during FAST_SEEK / SLOW_APPROACH). If that edge falls between the capture and `safety_tick()`'s `take()`, it is folded in the same tick: homing advances and `motion_tick()` starts the next segment. `hw_start()` then sees the moved generation and kills the start. Homing ends with **HOME_FAILED (HF_WIRING) and FAULT HOME_WIRING**.
- Reproduced in `test_val_tickgen`; see §11.3.

**(c) hw_meas replay with the moved rule: PASS.**
- `test_val_check` replays the 543 `vectors` (hw_meas = 0) and the 32 `hw_meas_vectors` (CM lines, hw_meas = 1, state_schema 4 `ena_on`) against the pure `cmd_check()`. All 575 pass, including `meas_static_pul_ena_holding` (E_STATE MEAS_STATE) and `meas_static_pul_after_disable` (OK).
- The cmd.c refusal is removed (it is now in the pure check). HG-06 sends DISABLE first.

**HG-18 records.** The twin `--quick` rehearsal writes the HG-18 checks for sections 0, 1, 2, 3, 4, 5, 23, 11, 12, 13, 16, 17, 18 and the derived "step ISR + largest level-1 handler (D-52)". All are NOT MEASURED in the twin (no DWT). On the board they carry the D-51 / D-52 criteria and the `isr_wcet` calibration ratios.

### 11.3 New finding

| ID | Sev | Finding | Anchor | Reproducer | Fix hint | Owner |
|---|---|---|---|---|---|---|
| FWR-18 | **Medium** | The FWR-17 fix moved the generation capture before the input fold. An expected homing edge (START during FAST_SEEK or SLOW_APPROACH, END …) whose CLEAN halt — or its OPM final update ≤ pulse width later — falls between `motion_gate_capture()` and `safety_tick()`'s `take()` is folded in the same tick. The homing machine then starts the next segment in `motion_tick()`, `hw_start()` kills it (generation moved), and homing fails with HOME_FAILED HF_WIRING + FAULT HOME_WIRING. The window is the start of `core_tick_1ms()` / `safety_tick()` up to the limit `take()` (µs), plus the pulse width for an OPM completion. With two expected edges per homing, I estimate ≈ 0.5–2 % spurious homing failures. This is fail-safe (no motion), but it blocks operation and affects HG-26. | `core/app.c` `core_tick_1ms()` (capture first); `core/safety.c` `take()`; `core/motion.c` `hw_start()` | `02_FW/test/test_val_tickgen/test_main.c`: replica of the v0.8.1 tick body with the START edge injected right after the capture → **FAIL** (HOME_FAILED, faults 0x20). Control: the same edge before the capture → PASS (HOMED). | Re-capture the generation when the edge records are taken: e.g. in `take()` (same PRIMASK section) for E-stop / START / END, or right after the limit takes in `safety_tick()`. Halts whose records are folded in this tick are then accounted for, and any halt after the take still moves the generation (keeps FWR-17 closed). | A |

### 11.4 Evidence

| Run | Result |
|---|---|
| Release build (`rc4`), private dir | rc 0; 0 warnings in our sources; `check_map` M-1…M-4 PASS |
| `isr_wcet.py` (conservative and `--nominal`) on my ELF | Figures in §11.1; 1 unknown-address access, costed APB1 |
| `pio test -e native` (39 suites, validator vectors ICD 0.7.5 / schema 4) | 233 cases, **232 PASS**; the only FAIL is the FWR-18 reproducer. FWR-16, FWR-03, FWR-07, `test_val_check` (575), `test_val_steprace` PASS |
| Validator suites in seeded random order (`VAL_SEED=20261010`) | 49 cases, 48 PASS (FWR-18) |
| `pytest 02_FW/test/twin` + `00_System/tools/hil/tests` + `test_val_review_gen.py` (private twin) | **358 passed** (246 + 103 + 9) |
| Twin rehearsal `hil_session.py --twin --quick` | 43 steps, rc 0. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A; no FAIL / ERROR / REFUSED. HG-18 records as listed in §11.2. |

## 12. Re-check of FW v0.8.2 (FWR-18) (Validator E, 2026-10-09)

**Scope.** I re-checked a snapshot of the working tree taken 2026-10-09 19:01 (`rc5`); its `sha256` over `02_FW/{src,include,tools,platformio.ini}` is `a6e8aca2…`. Since §11 only these files changed:
- `core/safety.c`: `take_halt_inputs()` and `safety_edges_pending()`;
- `core/motion.c`: the segment end is deferred while an edge record is pending;
- `core/app.c`: the early capture is removed;
- `core/cmd.c` and `core/fw.h`.

No hardware was used, nothing was committed, and all build and twin directories were private.

**Verdict: GO WITH CONDITIONS.** FWR-18 is **CLOSED** and FWR-17 stays closed. Questions (a) and (b) need no change. Question (c) produced a new Low finding, **FWR-19**: the new PRIMASK window is about 0.9–1.2 µs by my instruction-level estimate, not about 0.3 µs as stated. DWT section 11 at HG-18 decides; the fix is cheap.

### 12.1 Verdict per item

| Item | Verdict | Evidence |
|---|---|---|
| FWR-18 | **CLOSED** | `test_val_tickgen`, reworked to drive the **real** `core_tick_1ms()`. Injecting the expected START edge of FAST_SEEK *inside* a tick after the take (`fake_peek_hook`, i.e. the sniffer's peek in `link_tick()`) gives: v0.8.1 → HOME_FAILED (FAIL); v0.8.2 → HOMED (PASS). The control (edge between ticks) passes on both. A's impl test `test_homing_edge_inside_tick_after_take` passes. |
| FWR-17 | **CLOSED** (unchanged) | An edge after the take moves the generation after the capture and leaves its record pending. `motion_tick()` then decides neither a segment end nor a restart in that tick. |
| (a) deferral bound | OK, no finding | §12.2 (a) |
| (b) STOPPING / controlled / link-watchdog / MOVE_DONE timing | OK, no finding (OBS-RC-5) | §12.2 (b) |
| (c) PRIMASK window | **finding FWR-19 (Low)** | §12.2 (c) |

### 12.2 The Orchestrator's questions

**(a) Can the deferral postpone a segment end indefinitely, or let anything move?**
- **Duration.** `safety_edges_pending()` is true only for an E-stop / START / END active-edge record set after this tick's take. The next tick's `take_halt_inputs()` clears it, so one edge defers the end by exactly one tick.
- **Indefinite deferral** needs a new active edge after the take in every tick. That is impossible for START / END: their lines stay masked from the first active edge until a stable release of ≥ `io.release_ms`. It is possible only for a chattering E-stop, whose line is never masked.
- **Nothing moves while deferred.**
  - The branch requires `!hal_step_running()`, and every pending act-edge comes from a fixed reaction that halted the timer.
  - Dead-man and controlled-stop edits on a stopped timer only touch the ramp bookkeeping and a stopped TIM2. `set_period_now` checks `s_running`; OPM on a stopped counter is inert.
  - No restart can happen, because the restart path (`segment_ended`) is the one being deferred.
- **Reproducer.** `test_estop_chatter_keeps_deferral_bounded_and_still`: a jog, then the E-stop opens, then 30 ticks of chatter that reopen after every take.
  - 0 PUL edges after the first E-stop edge.
  - After the chatter, exactly one MOVE_DONE within ≤ 2 ticks.
  - ESTOP latched, ENA disabled.
  - Result: PASS on v0.8.2 (and on v0.8.1).
- During the chatter, MOVE_DONE is postponed, but the axis is stopped, NOT_ENABLED and ESTOP-latched, so no command can start motion.

**(b) Interaction with STOPPING, controlled stops, the link watchdog and MOVE_DONE timing.**
- **When the pending edge caused the halt** (limit / E-stop): the fold in the next tick emits the latch / STOPPED events and `finish()` follows in the same tick. This is the same PC-visible timing as before. Before, the end was evaluated one tick early as "stopped short, no cause" and the next tick's fold explained it; now the end waits for the fold.
- **When an unrelated edge coincides** with a natural or controlled-stop end (OPM final update after the take): MOVE_DONE comes **one tick later** (≤ 2 ms instead of ≤ 1 ms after standstill). MS_STOPPING (still "moving") lasts that extra tick.
  - The link watchdog latches only once (`!link_wdg`); the jog dead-man ignores `stopping`.
  - A command arriving in that extra tick is refused as moving, exactly as in the previous tick.
- The ICD states no MOVE_DONE latency: MOVE_DONE is sent at standstill, with STOPPED first. So this is within the ICD. **OBS-RC-5:** the EVENT `t_us` of MOVE_DONE is the emission time, so in that coincidence it is ≤ 1 ms later than standstill. The SW uses MOVE_DONE as `t_reached` (SW-SEQ-003); an extra ms before the settle window is harmless. Information for B / F.
- **Integration evidence:** `03_SW/tests/integration` against the v0.8.2 twin: **176 passed**.

**(c) Length of the PRIMASK section (CRIT_HALT class).**
- **Disassembly.** In the release image the window runs from `cpsid i` inside `hal_crit_enter()` to `cpsie i` inside `hal_crit_exit()`:
  - **64 instructions** of `safety_tick()` body (0x080104ae–0x08010582), all flash code;
  - plus `motion_gate_capture()` → `hal_step_stop_gen()`: 2 calls and 2 literal loads;
  - plus the enter / exit tails.
- **Why it is long.** The three `in_rec_t` snapshots are copied **twice** (field loads into a stack temporary, then `ldm` / `stm` of 16 bytes into `r[]`): 9 `ldrb`, 6 `ldr`, 15 `strb`, 6 `str`, 4 `ldm`, 5 `stm`/`stmdb`, 8 `uxtb`.
- **Estimate.** With the `isr_wcet.py` cost model (ldr 2, ldm / stm 1+N, call / return 1+3, ART miss +5 per taken target / literal, +1 per 16-byte line):
  - **≈ 220 cycles ≈ 1.2 µs** conservative;
  - **≈ 165 cycles ≈ 0.9 µs** with ART hits.
- **Comparison.**
  - A's "~0.3 µs";
  - the CRIT_HALT design figure of ≤ 0.2 µs (FW_design §4.4, pinout §4);
  - NFR-007's ≤ 1 µs for sections masking levels 0–2.

  The window also delays the E-stop reaction by up to its length. That is irrelevant against 100 µs, but it is part of the budget.

### 12.3 New finding

| ID | Sev | Finding | Anchor (rc5) | Evidence | Fix hint | Owner |
|---|---|---|---|---|---|---|
| FWR-19 | Low | The `take_halt_inputs()` PRIMASK window (CRIT_HALT, masks level 0) is ≈ 165–220 cycles (0.9–1.2 µs, estimate). It exceeds the ≤ 0.2 µs CRIT_HALT design figure and is at or above NFR-007's 1 µs for windows masking levels 0–2. The cause is double struct copies of 3 × 16 B and two calls in flash. | `core/safety.c` `take_halt_inputs()` / `take_nl()`; `core/motion.c` `motion_gate_capture()` | Release disassembly (§12.2 (c)). DWT section 11 (CRIT_HALT) at HG-18 decides. | Write each record straight into `r[i]` through a pointer (no temporary). Read `s_stop_gen` inline (a `static inline` accessor or one volatile load) instead of two calls. Optionally copy only `act_edge` / `edge` inside the section and the time / step fields under their own short window. Target ≤ 60 cycles; then update FW_design §4.4 with the measured figure. | A |

### 12.4 Evidence

| Run | Result |
|---|---|
| Release build (rc5, private dir) | rc 0; 0 warnings in our sources; `check_map` PASS |
| `pio test -e native`: 39 suites, impl + validator, validator vectors ICD 0.7.5 / schema 4 | **234 / 234 PASS**, incl. `test_val_tickgen` 3/3, `test_val_toctou` 7/7, `test_val_steprace` 6/6, `test_val_check` 575 vectors |
| Validator suites in seeded random order (`VAL_SEED=20261011`) | 49 / 49 PASS |
| `test_val_tickgen` on v0.8.1 (rc4) | after-take homing case FAILS (HOME_FAILED): the reproducer discriminates |
| `pytest 02_FW/test/twin` + `00_System/tools/hil/tests` + `test_val_review_gen.py` | **358 passed** |
| `pytest 03_SW/tests/integration` (v0.8.2 twin, private) | **176 passed** |
| Twin rehearsal `hil_session.py --twin --quick` | 43 steps, rc 0. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A; no FAIL / ERROR / REFUSED |

## 13. Re-check of FW v0.8.3 (FWR-19) (Validator E, 2026-10-09)

**Inputs.** Working-tree snapshot of 2026-10-09 19:34 (`rc6`). `sha256` over `02_FW/{src,include,tools,platformio.ini}` = `0788cced…`. Since §12 only `core/safety.c` and `tools/isr_wcet.py` changed. No hardware; nothing committed; private build and twin directories.

**Verdict: GO WITH CONDITIONS.**
- **FWR-19: CLOSED.** The publication protocol is race-free (a).
- **(b): new FWR-20 (Medium).** It was introduced in v0.8.1 by the FWR-17 fix and is still present. A *natural* segment end (the OPM final update, which bumps the stop generation) can land between the early capture and `motion_tick()`. The tick-initiated next segment is then killed by `hw_start()`'s recheck, and homing fails with HOME_NOT_FOUND. Reproduced on v0.8.1, v0.8.2 and v0.8.3. A scratch build with the capture moved back to the start of `motion_tick()` passes everything, with the deferral kept.
- **(c):** the rewording is acceptable as a design note, but several documents still state the old figure (OBS-RC-6).

### 13.1 Verdict per item

| Item | Verdict | Evidence |
|---|---|---|
| FWR-19 | **CLOSED** | Masked window (`cpsid` in `hal_crit_enter` → `cpsie` in `hal_crit_exit`): flag loads / stores only. Worst path ≈ 105–126 cycles (0.6–0.7 µs) under the conservative model, ≈ 60–70 cycles (≈ 0.35 µs) nominal (my count from the disassembly). This agrees with A's 126 / 60 cycles. `isr_wcet.py --path fn:take_halt_flags`, whole function incl. prologue / epilogue: 171 / 88 cycles. Within NFR-007 (≤ 1 µs). DWT section 11 at HG-18 decides. |
| (a) publication protocol | **OK** | §13.2 (a) |
| FWR-17 / FWR-18 | **CLOSED** for edges | `test_val_tickgen` homing-edge cases and the E-stop chatter case PASS. The capture after the window is covered by the deferral: an edge in the gap leaves a pending record. |
| FWR-20 (new) | **OPEN, Medium, owner A** | §13.3 |
| (c) CRIT_HALT wording | **Acceptable**, documents inconsistent → OBS-RC-6 | §13.2 (c) |

### 13.2 The Orchestrator's questions

**(a) Is payload-before-flag race-free on the Cortex-M4? Yes.**
- **Writer side.** `on_input_edge()` is the only writer of `t_us`, `steps` and `was_running`. It writes them only `if (act && !r->act_edge)`, and stores `act_edge` after them. In the disassembly the store order is `str t_us`, `str steps`, `strb was_running`, `strb act_edge`, `strb edge`.
- **Clearing.** Only the tick clears `act_edge`, inside `take_halt_flags()`; boot does it with a memset.
- **Reader side.**
  - **Loads.** `take_halt_inputs()` loads `act_edge` first, then the payload (`ldrb [#5]`, `ldr [#8]`, `ldr [#604]`, `ldrb [#608]`). All fields are `volatile`, so the compiler keeps program order. The Cortex-M4 is single-core and in-order, so ISR writes are seen in program order by the pre-empted tick. No barrier is needed: there is no other bus master or core, and DMA does not touch these fields.
  - **The window** (`take_halt_flags`, noinline; PRIMASK) re-reads `act_edge`:
    - if it is set but was not set before the copy (`a && !pre_act`), nothing is cleared and the record stays pending for the next tick;
    - otherwise `edge` / `act_edge` are taken and cleared.
- **Payload stability.**
  - `pre = 1`: nobody but the reader can clear `act_edge`, so the payload copied after it is the one published with it.
  - `pre = 0` with a torn payload copy (handler writes during the copy): the record is not taken in this tick. The torn copy lands in `r[k]`, but `r[k].act_edge` = false, so `safety_tick()` does not use `t_us` / `steps`.
- **E-stop chatter.** EXTI3 delivers open / closed edges at level 1. With `act_edge` set, a re-open only sets `edge`; with it clear, it publishes a new payload before `act_edge`. Both cases are covered above. `test_estop_chatter_keeps_deferral_bounded_and_still` PASS (no motion, one MOVE_DONE ≤ 2 ticks after).
- **Nesting.** There are no level-0 writers since v0.8 (the E-stop callback is deferred to EXTI3). Limits and EXTI3 share level 1 and cannot nest. During a flash operation the callback is skipped (unchanged).

**(b) FWR-17 / 18 with the capture after the window. A halt in the gap is covered; a natural segment end is not (FWR-20).**
- A fixed reaction (edge) between window exit and capture creates a record that stays pending, so the deferral prevents any segment end / restart in this tick.
- Level-3 halts (load trip, MOVE_UNTIL_LOAD hit) are folded in the same tick before `motion_tick()`: `afe_fold_trip()` → stopping, `mul_fold()`. A step-ISR count fault is handled first in `motion_tick()` (`step_fault`).
- **Not covered: the OPM final update of a planned segment end.** The step ISR bumps `stop_gen` when the counter stops by OPM (`TIM2_IRQHandler`, `s_running` → false, `s_stop_gen++`). It can pre-empt the tick anywhere between the capture (since v0.8.1 at or in `safety_tick()`) and `motion_tick()`'s `hal_step_running()` check: the rest of `safety_tick()`, `link_tick()`, `afe_tick()` — tens of µs per tick. That end is then evaluated in this tick (`segment_ended()`), the next homing segment is started, and `hw_start()`'s recheck sees the moved generation and stops it. → FWR-20.

**(c) CRIT_HALT wording.**
- Acceptable as a design note: NFR-007's ≤ 1 µs is binding and D-51 makes HG-18 decide. I do **not** ask to keep 0.2 µs as a hard design budget.
- But the new note "design ≤ 0.2 µs nominal" is not met by A's own nominal 0.33 µs for the take window. Several places still state "CRIT_HALT ≤ 0.2 µs" as an absolute: pinout line 169; FW_design §4.4 l. 368, §5.3 l. 411, §6 l. 970 / 994 / 1002. The E-stop latency line (970) should carry the real window, ≤ 0.7 µs conservative, still ≪ 100 µs. → **OBS-RC-6** (documentation, A).

### 13.3 New finding

| ID | Sev | Finding | Anchor (rc6) | Evidence | Fix hint | Owner |
|---|---|---|---|---|---|---|
| FWR-20 | **Medium** | Since v0.8.1 the tick's stop-generation capture comes **before** `motion_tick()` decides segment ends: first in `core_tick_1ms()` (v0.8.1), then inside / after the record take in `safety_tick()` (v0.8.2 / v0.8.3). A planned segment end (OPM final update → `s_stop_gen++` in the step ISR) that falls between the capture and `motion_tick()`'s `hal_step_running()` check is handled in that tick. The next tick-initiated segment (homing phase change) is armed and immediately stopped by `hw_start()`'s start-then-recheck, so the homing machine sees a zero-length segment and homing fails (HOME_FAILED NOT_FOUND, FAULT 0x10). The window (rest of `safety_tick()` + `link_tick()` + `afe_tick()`) is tens of µs per 1 ms tick, and homing has several planned segment ends, so I expect a few-% homing failure rate on the target. It is fail-safe (no unintended motion). The jog-reversal restart did not fail in the model. | `core/safety.c` `take_halt_inputs()` (capture), `core/motion.c` `motion_tick()` → `segment_ended()` → `hw_start()` recheck; `hal/f446/step_tim2.c` OPM-final `s_stop_gen++` | `02_FW/test/test_val_tickgen::test_homing_with_segment_ends_inside_tick`. Step events are processed **inside** each tick after the record take (`fake_peek_hook` in `link_tick()`), i.e. the step ISR pre-empts the tick. Result: **FAIL** on v0.8.1, v0.8.2, v0.8.3. **PASS** on a scratch build of v0.8.3 with the capture moved back to the first line of `motion_tick()` (deferral kept); that build passes all 236 native cases incl. A's FWR-17 / 18 impl tests and the 5 `test_val_tickgen` cases. | Capture the generation at the start of `motion_tick()`, right before the segment-end decision, and keep the `safety_edges_pending()` deferral. The deferral covers FWR-17 / 18: an edge after the take always leaves a pending record. Level-3 halts are folded by `afe_tick()` / `mul_fold()` before it, and any halt after the capture is caught by the recheck. Alternatively: do not bump the generation for a planned last-period end (bump only on stop requests, incl. the OPM path of `hal_step_stop_now()` at the request). | A |

### 13.4 Evidence

| Run | Result |
|---|---|
| Release build (rc6), private dir | rc 0; 0 warnings in our sources; `check_map` PASS |
| Disassembly: `on_input_edge`, `take_halt_inputs` (inlined in `safety_tick`), `take_halt_flags` | Store / load order as in §13.2 (a); window instructions as in §13.1 |
| `isr_wcet.py --path fn:take_halt_flags` (conservative / `--nominal`) | 171 / 88 cycles for the whole function |
| `pio test -e native` (rc6, 39 suites) | 237 cases, **236 PASS**; the only FAIL is the FWR-20 reproducer |
| `test_val_tickgen` on v0.8.1 / v0.8.2 / v0.8.3 / experiment | the FWR-20 case FAILS / FAILS / FAILS / PASSES |
| Validator suites in seeded random order (`VAL_SEED=20261012`) | 52 cases, 51 PASS (FWR-20) |
| `pytest 02_FW/test/twin` + HIL tests + `test_val_review_gen.py` | **358 passed** |
| Twin rehearsal `--twin --quick` | 43 steps, rc 0. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A |
| Note | The twin and the normal fake process step events only between ticks, which is why every other suite stays green. Only the in-tick model shows FWR-20. |

## 14. Re-check of FW v0.8.4 (FWR-20, CEN-read removal, OBS-RC-6) (Validator E, 2026-10-10)

**Inputs.** Working-tree snapshot of 2026-10-10 04:05 (`rc7`). `sha256` over `02_FW/{src,include,tools,platformio.ini}` = `4a5309ee…`. Since §13, `core/motion.c`, `core/safety.c`, `hal/f446/step_tim2.c` and `tools/isr_wcet.py` changed, plus FW_design v0.8.4 and pinout §4. Before this report I compared the repo with the snapshot again: no file in `02_FW/{src,include,tools}`, `platformio.ini`, FW_design or pinout has changed since. No hardware; nothing committed; private build and twin directories.

**Verdict: GO WITH CONDITIONS.**
- **(a) FWR-20: CLOSED.** FWR-01 / 02 / 17 / 18 stay closed under the in-tick model.
- **(b) The CEN-read removal is correct**, and the new OPM write is race-free against the step ISR and the level-0 reaction. But the access-timed model I built for the question shows a defect in the same decision window that is older than v0.8.4: **FWR-21 (Low)**. A CLEAN halt whose running pulse ends between the CNT read and the OPM write emits one more pulse a full period later. There is also a margin observation, **OBS-RC-7**: the 0.5 µs guard is about as long as the CNT → write paths.
- **(c) OBS-RC-6: CLOSED.** FW_design and pinout are consistent. The one leftover "≤ 0.2 µs" was in my own test plan (TC-FW-SW-002-01), and I have corrected it.

### 14.1 Verdict per item

| Item | Verdict | Evidence |
|---|---|---|
| FWR-20 | **CLOSED** | `motion_tick()` now does three things in order: reads `hal_step_running()` (`stopped`), captures the generation (`motion_gate_capture()`), then calls `safety_edges_pending()`. A planned end before the read is seen as `stopped` and is already in the captured generation, so the next segment's recheck passes. An end after the read is not evaluated in this tick. `test_val_tickgen::test_homing_with_segment_ends_inside_tick` **PASS** on v0.8.4; it FAILS on v0.8.1–v0.8.3. `take_halt_inputs()` no longer captures. |
| FWR-17 | **CLOSED** (unchanged) | A halt after the capture moves the generation, and `hw_start()` refuses the segment. A halt with a record between the take and the capture leaves the record pending, so the step is deferred. A halt with a record between the running read and the capture has `stopped` = false: no segment decision this tick, and `cmd_execute()` sees the pending record (FWR-03 / 16). The record is published by a pre-empting ISR, which completes before the tick resumes, so the generation bump and `act_edge` appear together. `test_val_tickgen` 5 / 5 PASS. |
| FWR-18 | **CLOSED** (unchanged) | The expected homing edge inside the tick after the take is deferred: `test_expected_homing_edge_inside_tick_after_take` PASS. E-stop chatter keeps the deferral bounded: PASS. |
| FWR-01 / FWR-02 | **CLOSED** (unchanged) | `hal_step_start()` and the idle bump are unchanged. `test_val_steprace` 6 / 6 PASS, including the 926-case E-stop equivalence on the new `stop_now` / `abort`. A's `test_impl_steptim` 11 / 11 PASS. `test_val_toctou` 6 / 6 PASS. |
| (b) CNT = 0 after an OPM stop | **TRUE** for every valid configuration | §14.2 (b1) |
| (b) OPM-write logic | **race-free** against the step ISR and the level-0 reaction | §14.2 (b2) |
| (b) related, older than v0.8.4 | **new FWR-21 (Low)**; **OBS-RC-7** | §14.3 |
| (c) OBS-RC-6 | **CLOSED** | §14.2 (c) |
| OI-FW-51 | **reproduced** | `isr_wcet.py` on my rc7 ELF: `hal_step_stop_now` 220 / 169 cycles (1.22 / 0.94 µs), `hal_step_abort` 206 / 158 (1.14 / 0.88), `hal_step_set_period_now` 323 / 171 (1.79 / 0.95), `hal_step_arm_last` 0.24 / 0.14, `take_halt_flags` 0.96 / 0.49 µs (conservative / nominal). Identical to FW_design §9.8. NFR-007 for these windows stays with HG-18 (D-51). |

### 14.2 The Orchestrator's questions

**(b1) "CNT 0 after an OPM stop is below every compare." True for every valid configuration.**
- **Hardware.** In upcounting mode the update event resets CNT to 0. With OPM set, the same update clears CEN (RM0390, TIMx one-pulse mode). So a counter stopped by OPM holds CNT = 0 until it is restarted. `hal_step_start()` writes CNT = 0 and UG itself.
- **Every compare is above the guard.** The decision is `stepgen_halt_complete(0, ccr, 45)` = `ccr ≤ 45`. Every compare that `ccr_running()` can return is ≥ 225 ticks:
  - **Running and preloaded periods** come from the ramp, whose period floor `M.chw` = max(f / `max_step_rate_hz`, pw + `pulse_low`). So CCR1 = period − pw ≥ `pulse_low_min_ns` ≥ 2 500 ns = 225 ticks (params.yaml min).
  - **The first period:** CCR1 ≥ `dir_setup`, and ≥ the floor as well.
  - **The last armed period** keeps `s_pre` at a valid period, because `r.last` writes no preload.
  - **`s_late_cur` from a stretch** is ≥ ARR + 1 of the running period: the stretch only extends.
  - So `ccr ≤ 45` is never true, and the OPM-stopped counter always takes `halt_hw()`, exactly as the former CEN test did. `abort`: `cnt ≥ ccr` with CNT = 0 is false, so no false "cut". This also matches the former CEN test.
- **STATIC_LEVEL.** It is set only with CEN = 0 and the core NOT_ENABLED, and `s_running` is false then. `stop_now()` / `abort()` therefore only bump the generation, as before; the CEN read was inside `if (s_running)` in v0.8.3 too. The E-stop reaction still forces PUL inactive and clears `g_meas_static`.
- **Even without the guard argument the result would be safe.** If a compare ≤ guard were possible, the CLEAN path would find OPM already armed (OPM stays set after the stop), write nothing and return "complete". The pending ISR (CEN = 0 branch) would count and finish. The count would still be exact; only the immediate count of DEF-M2-01 would be one pulse later.
- **Target-only.** This rests on the reference-manual behaviour (CNT reset at the update, CEN cleared by OPM), modelled identically by A's and my register models. It is not observable without hardware; HG-18 / the SAF-FW-004 count trials cover it indirectly.

**(b2) The OPM write in `stop_now()` is race-free.**
- The whole decision runs under PRIMASK. The step ISR (level 2), the level-0 E-stop reaction (`step_estop_reaction`) and level-1 halts cannot run inside it.
- **Hardware can change CR1 in only one way:** clearing CEN at an update with OPM set.
  - With OPM clear at the read, no hardware change of CR1 is possible before the write, so `cr | OPM` cannot re-set a cleared CEN.
  - With OPM already set, nothing is written. This removes the v0.8.3 hazard: there, `CR1 |= OPM` was a read-modify-write, and an OPM update between its read and its write could re-enable the counter.
- **Writes outside the PRIMASK window.** `hal_step_arm_last()`, the ISR's `r.last` and `set_period_now()` all write CR1 under PRIMASK too. So no stale `cr` value can overwrite another context's CR1 change.
- **Constant CCMR1 in `set_period_now()`.** The invariant `s_running && CEN ⇒ CCMR1 = PWM2 | OC1PE` holds: only `hal_step_start()` arms PWM2, and every path that forces PUL inactive also clears `s_running` or CEN. So writing the constant changes nothing else.
- **What the window does not cover is the update event itself.** An update with OPM clear does not change CR1, but it does start the next period. That is FWR-21.

**(c) OBS-RC-6: CLOSED.**
- FW_design v0.8.4 states NFR-007 ≤ 1 µs as binding everywhere: §4.4 l. 368 "figures below", §5.3 l. 412, the §6 latency line 971 (≤ 2.3 µs conservative incl. the longest window), l. 995 / 1003, and the §9.8 table l. 1303–1306. It gives the static bounds per window: 0.95 µs nominal, 1.79 µs conservative.
- Pinout §4 l. 162 / 169 uses the same figures.
- A repo-wide search for "≤ 0.2 µs" finds only historical text in this review and TC-FW-SW-002-01 in my test plan. I corrected the latter in v0.4.10.
- The figures agree with my `isr_wcet.py` run (§14.1).

### 14.3 New findings

| ID | Sev | Finding | Anchor (rc7) | Evidence | Fix hint | Owner |
|---|---|---|---|---|---|---|
| FWR-21 | **Low** | **CLEAN halt, update inside the decision window.** `hal_step_stop_now()` reads CNT, decides "complete" (`CNT + guard ≥ CCR1`: pulse running or imminent), reads CR1 and writes `cr \| OPM`. If the running pulse **ends** between the CNT read and the CR1 write, the update starts period N+1 with OPM still clear (CEN unchanged, so the write is "race-free" in the CR1 sense of §14.2 (b2)). OPM then stops the counter only at the end of N+1, so **one more complete pulse is emitted a full period after the call**. The count stays exact (the ISR counts both). But SAF-FW-002 (no PUL edge > 200 µs after a limit edge / deciding sample / STOP frame) and FW_design §5.3 l. 427 ("last PUL edge ≤ pulse_high + 0.5 µs after the call") are violated whenever the step period is > 200 µs (< 5 kHz, e.g. 1.25 ms at 1 mm/s with 800 steps/mm). The same applies to every CLEAN caller: limits, load limit, AFE fault, MUL hit, PC STOP / HALT, homing edges. The hit window is the CNT read → CR1 write path, ≈ 30–40 timer ticks on the target (disassembly 0x20000396 → 0x200003c4: SR read, 14 instructions, CR1 read, store), so the probability is ≈ 35 / P per halt during a pulse end (≈ 3·10⁻⁴ at 1.25 ms). It will rarely show in 100 HG trials. Older than v0.8.4: v0.8.3 (`CR1 \|= OPM` after a CEN / CNT read) fails the same test. Physical effect: one extra, counted step (≈ 1.25 µm). | `hal/f446/step_tim2.c:287-292` | `test_val_haltwin::test_clean_halt_pulse_ending_in_window_no_late_pulse`: **FAIL** on v0.8.4 (12 of 64 offsets, last edge 1 250 µs after the call; bound 11.2 µs) and on v0.8.3 (6 of 64). The model is optimistic: 6 ticks per TIM2 access, no instruction cost. Control `test_control_clean_halt_mid_pulse` PASS. | Detect the update after the write and stop the next period before its pulse: `cnt = TIM2->CNT; … TIM2->CR1 = cr \| OPM; if (TIM2->CNT < cnt) TIM2->CR1 = CR1_STOPPED;`. CNT is then early in N+1, far below CCR1 ≥ 225 ticks, so there is no pulse and no runt. The pending ISR takes its CEN = 0 branch: it counts pulse N once, forces PUL inactive and clears `s_running` (record semantics as in a normal CLEAN completion). If the update came after the write, the counter is already stopped and the extra write is harmless. The worst path stays at 6 APB1 accesses (the same as the `halt_hw()` path), so OI-FW-51 is not worsened. Verified on a scratch copy (`rc7x`): `test_val_haltwin` 6 / 6, `test_val_steprace` 6 / 6, `test_impl_steptim` 11 / 11 PASS. A variant with `halt_hw()` instead also passes, but it costs 3 more APB1 accesses. | A |
| OBS-RC-7 | Obs. | **0.5 µs guard margin.** The guard (`s_guard` = f / 2 MHz = 45 ticks) must cover the CNT read → acting write path of two decisions. In both cases the margin is essentially zero under the conservative model, and a runt would violate SAF-FW-004. HG-18 sees only the whole window (DWT sections 13 / 16), not this sub-path. | `step_tim2.c:156, 248, 287`; `stepgen.h:22-47` | `test_val_haltwin`: `test_stretch_no_runt_nominal_bus` and `test_clean_halt_before_pulse_no_runt_nominal_bus` PASS at the nominal bus cost. `test_stretch_guard_margin_report`: first runt at 12 ticks per access (48-tick path). | Raise the guard to 1 µs (90 ticks) or 2 µs. A CLEAN halt within that time of the pulse then lets the pulse complete (still ≤ pulse_high + 2 µs, well inside SAF-FW-002). A stretch is refused ≤ 2 µs before a pulse of a > 2 ms period (D-30), so the cost is negligible. Alternatively, confirm the sub-path on the target (HG-18 note). | A |
| OBS-RC-8 | Obs. (test harness) | `00_System/tools/tests/test_fw_twin.py:60` reads `fw_twin/build/build_probe.log` from the fixed tree path and ignores `BEND_TWIN_BUILD_DIR`. With a private twin build dir (required for concurrent agents) the test fails with FileNotFoundError, although the log exists in the private dir. | `00_System/tools/tests/test_fw_twin.py:60` | `pytest 00_System/tools/tests` with `BEND_TWIN_BUILD_DIR` set: 1 FAIL, `build_probe.log` present in the private dir | Take the build dir from `twin_build` / the environment variable | Integrator |

**FWR-21, the two sub-cases in detail (fixed variant, timer at CNT = ARR − d at the call):**
- **The update falls after the CNT read and before the OPM write.** Period N+1 runs with CNT small. The check `CNT < cnt` is true, and `CR1 = CR1_STOPPED` stops the counter before CCR1 of N+1. N+1 has no pulse. UIF of N is pending, and the ISR counts N.
- **The update falls after the OPM write.** The counter is stopped by OPM with CNT = 0. The check is true, and the write of `CR1_STOPPED` only clears OPM. This is the same outcome as a normal CLEAN completion.

### 14.4 Evidence

| Run | Result |
|---|---|
| Release build (rc7), private dir | rc 0; 0 warnings; `check_map` PASS |
| Disassembly of `hal_step_stop_now` (`.data` 0x20000380), `halt_hw` (0x20000294), `hal_step_set_period_now` (0x08012254) | CNT read → CR1 write and CNT read → CCMR1 / CCR1 write paths as in §14.3; `s_guard` = 45 (0x2000001c) |
| `isr_wcet.py` (conservative / `--nominal`) on the rc7 ELF | as in §14.1; identical to FW_design §9.8 |
| `pio test -e native` (rc7, 40 suites before the new one) | **236 / 236 PASS** |
| `pio test -e native -f test_val_haltwin` (new suite) | 6 cases: 5 PASS, **1 FAIL = FWR-21 reproducer**; on v0.8.3 the same; on the `rc7x` experiment 6 / 6 PASS |
| Validator suites in seeded random order (`VAL_SEED=20261014`, incl. `test_val_haltwin`) | 58 counted by pio: 56 PASS, 1 FAIL (FWR-21); the haltwin suite entry reports ERRORED on the failure exit |
| `pytest 02_FW/test/twin` + HIL tests + `00_System/tools/tests` + `test_val_review_gen.py` (private `BEND_TWIN_BUILD_DIR`) | **1 348 passed, 1 failed** (OBS-RC-8, harness path) |
| Twin rehearsal `hil_session.py --twin --quick` | 43 steps, rc 0. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A (the same as §13, rc6) |

### 14.5 Overall FW verdict for the HW gate

**GO WITH CONDITIONS.** No Critical, High or Medium finding is open. FWR-20 is closed, and FWR-01…20 are closed. Conditions:
1. **FWR-21 (Low, A).** Fix it before the SAF-FW-002 on-target trials (HG list: limit / load / STOP paths, 100 trials per path). The fix is a two-line change with a verified reproducer. If it is not fixed, record it as a known deviation for step periods > 200 µs; the count stays exact, so the effect is one extra counted step.
2. **OI-FW-51 / NFR-007 (A, Validator E at HG-18).** DWT sections 11 and 16–18 decide the CRIT_HALT windows. Under the conservative model `stop_now`, `abort` and `set_period_now` are above 1 µs; nominal they are ≤ 0.95 µs (D-51).
3. **OBS-RC-7 (A).** Either a larger guard, or a target check of the CNT → write sub-paths at HG-18.
4. **Unchanged target-only items (D-06).** HG-18 DWT criteria (D-52), the SAF-FW-002 / 004 / 005 on-target timing and count trials, and the `isr_wcet.py` calibration record.

## 15. Re-check of FW v0.8.5 (FWR-21, guard 1.5 µs) and final HW-gate verdict (Validator E, 2026-10-10)

**Inputs.** Working-tree snapshot of 2026-10-10 04:39 (`rc8`). `sha256` over `02_FW/{src,include,tools,platformio.ini}` = `ffd8aa4b…`. Since §14 only `hal/f446/step_tim2.c` and FW_design (v0.8.5: §5.3, §6, §9.12 items 11–12, OI-FW-52) changed. No hardware; nothing committed; private build and twin directories.

**Verdict: GO WITH CONDITIONS** (§15.5). (a) FWR-21 is **CLOSED**. (b) The 1.5 µs guard is **sound** and **OBS-RC-7 is CLOSED**. (c) TC-SAF-FW-004-05 is now a pass / fail criterion. I did not adopt A's proposal for OI-FW-52; §15.3 gives the reason.

### 15.1 Verdict per item

| Item | Verdict | Evidence |
|---|---|---|
| (a) FWR-21 | **CLOSED** | The code is the fix verified in §14. `cnt` is kept from the decision. After the OPM write `ldr CNT` (0x200003c8) / `cmp` / `bcs`: if CNT wrapped, `CR1 = 0x80` (`CR1_STOPPED`, 0x200003d0). The pending ISR takes its CEN = 0 branch: it counts the completed pulse, forces PUL inactive and clears `s_running`. `test_val_haltwin::test_clean_halt_pulse_ending_in_window_no_late_pulse` (6 ticks / access) and `…_conservative_bus` (12 ticks / access, 96 offsets) **PASS** on v0.8.5. Both FAIL on v0.8.4 (12 / 24 of 96 offsets, last edge 1 250 µs after the call). `isr_wcet.py`: `hal_step_stop_now` unchanged at 220 / 169 cycles (1.22 / 0.94 µs), so the new branch is not the worst path. |
| (b) guard 1.5 µs | **sound** | §15.2 |
| OBS-RC-7 | **CLOSED** | TC-SAF-FW-004-05 (c) PASS: stretch first runt at a 136-tick path = factor 2.96 over the conservative 46-tick target path; CLEAN halt_hw path factor 3.58. Both ≥ 2. On v0.8.4 the stretch factor is 1.04 (FAIL). |
| (c) margin test | **rewritten** (validator criterion) | §15.3 |
| FWR-01 / 02 / 17 / 18 / 20 | **CLOSED** (unchanged) | `test_val_steprace`, `test_val_toctou`, `test_val_tickgen` PASS (§15.4) |
| OBS-RC-8 | **CLOSED** (Integrator) | `test_fw_twin.py:58-61` reads `build_probe.log` next to the private probe binary; pytest with a private `BEND_TWIN_BUILD_DIR`: 1 349 passed |

### 15.2 (b) Is the guard change sound?

The guard is now 1.5 µs (`s_guard = (f / 2 MHz) × 3` = 135 ticks, read back from `.data` 0x2000001c = 0x87). I checked it against every place it matters:
- **The CNT = 0 argument of v0.8.4 (§14.2 b1) still holds.** The OPM-stopped counter must take the force-inactive path, which needs 0 + guard < CCR1. Every compare is ≥ `pulse_low_min_ns` minimum 2 500 ns = 225 ticks, so the margin is 90 ticks (1 µs).
  - `test_val_m2pure` now checks `stepgen_halt_complete(0, ccr, g)` = false and `stepgen_abort_cuts(0, ccr)` = false for every CCR1 ∈ [225, 20 000) and g ∈ {45, 135}.
  - A future guard ≥ 2.5 µs would break this. The comment at `step_tim2.c:156-159` states the limit; I recommend keeping it there (no action).
- **CLEAN decision and the exact count.** A larger guard only moves CNT values from "force inactive" to "OPM, the pulse completes". In both paths every started pulse completes and is counted by exactly one update (ISR or `halt_hw()`'s UIF check), and none is cut.
  - Force inactive is now chosen only when CNT + 135 < CCR1. The acting CCMR1 write comes ≈ 38 ticks (conservative) after the CNT read, so no pulse can have started. `test_clean_halt_before_pulse_no_runt_nominal_bus` and the margin test cover this.
  - The FWR-21 branch does not depend on the guard: after a wrap, CNT ≤ window ≪ 225.
  - Latency: the last edge comes ≤ PW + 1.5 µs after the call, i.e. ≤ 14 µs at the default and ≤ 101.5 µs at PW max. That is inside SAF-FW-002 (200 µs), and the FW_design §6 lines 973–975 are consistent.
- **The stretch fallback to the preload path.** `stepgen_stretch_ok()` now refuses when CNT + 135 ≥ CCR1, i.e. in the last PW + 1.5 µs of the running period instead of PW + 0.5 µs. On a refusal, `hal_step_set_period_now()` writes nothing. `redit_hw()` then preloads c2 with `hal_step_set_period()`, or calls `hal_step_arm_last()` when c2 = 0. So the running period ends at its old length and c2 follows.
  - **Count.** It is unaffected: steps are counted per update, and the ramp's `rem` assumed one step for the running period and one for c2. The running period still produces exactly one step, so the ramp and the hardware agree.
  - **Kinematics.** The old period is ≤ c1 (the stretch only extends), and the next period c2 ≥ c1. The fallback is therefore one period at the old speed, followed by deceleration. There is no acceleration step and no shortened interval. The stop distance grows by at most one step, the same as with the 0.5 µs guard.
  - **When it happens.** A refusal requires the edit to land within PW + 1.5 µs of the pulse. `wait_step_isr()` (FWR-10) starts the edit right after an update, and the stretch is used only for periods > 2 ms (D-30), so a refusal stays an edge case.
  - **No new races.** `set_period_now()` still writes nothing outside `if (CEN && stretch_ok)`. The window cannot contain an update, because the stretch is decided ≥ PW + 135 ticks before the update.
  - **Abort and the E-stop reaction** do not use the guard. `stepgen_halt_decide` vectors / `motion_vectors.json` contain no guard value.
- **Twin / HIL.** The twin and A's fake model the stop semantics without a guard (exact phase), so the twin results are unchanged. My test plan's random-phase criteria (TC-SAF-FW-004-01, §6 item 3) now name the 1.5 µs window.

### 15.3 (c) The margin test: my criterion, and why not A's proposal

- **A's OI-FW-52 proposal.** Scan from `ccr − s_guard − 1`, and PASS if there is no runt up to 20 ticks per access. With a 135-tick guard, 20 ticks × 4 accesses = 80 ticks never reaches the guard. That criterion therefore passes even if the model could no longer produce a runt at all, and it states no margin. I did not adopt it.
- **New TC-SAF-FW-004-05 (c), pass / fail, per decision path** (stretch k = 4 accesses after the CNT read, CLEAN halt_hw k = 2; the guard is read from the target's `s_guard`):
  1. **Anti-vacuity.** Scanning the per-access cost c up to just past the guard (k·c > guard + k), a runt **must** appear.
  2. **Decision logic.** No runt while k·c ≤ guard, so the guard is the only margin.
  3. **Margin.** The first runt must come at ≥ 2× the conservative target path from the disassembly: stretch CNT read → CCR1 write ≈ 91 cycles = 46 ticks; CLEAN CNT read → CCMR1 write ≈ 0.42 µs = 38 ticks. I chose factor 2, not A's 3, as the pass bar: the static model is uncalibrated until HG-18 (OI-FW-46), so one full path length of reserve is what the evidence supports. The measured factor is reported.
- **Results.**

| Path | Result on v0.8.5 | Result on v0.8.4 |
|---|---|---|
| Stretch | first runt at 34 ticks / access (136-tick path = guard + 1), factor **2.96** | first runt at 12 (48 ticks), factor **1.04 → FAIL** (reproduces OBS-RC-7) |
| CLEAN halt_hw | first runt at 68 (136-tick path), factor **3.58** | not meaningful: the host compiler read SR before CNT there, k = 1 |

- **Code-order dependency.** The test's k values follow the v0.8.5 access order: the CNT read is a separate statement and the first TIM2 access of `stop_now`. The ARM build has the same order (0x20000396 CNT, then SR). If A reorders the accesses, k must be updated; the anti-vacuity check catches a k that is too large.
- **Also updated.** TC-SAF-FW-002-05 now also runs at 12 ticks per access (the conservative equivalent). TC-SAF-FW-004-02 (`test_val_m2pure`) covers both guards and the CNT = 0 property.

### 15.4 Evidence

| Run | Result |
|---|---|
| Release build (rc8), private dir | rc 0; 0 warnings in our sources (the only warning is stm32duino's `Tone.cpp` `#warning`); RAM 13 376 B, flash 47 648 B; `check_map` M-1…M-4 PASS |
| Disassembly: `hal_step_stop_now` 0x20000380, `halt_hw` 0x20000294, `s_guard` | FWR-21 branch as in §15.1; `s_guard` = 135 |
| `isr_wcet.py` (conservative / nominal) | stop_now 1.22 / 0.94, abort 1.14 / 0.88, set_period_now 1.79 / 0.95, arm_last 0.24 / 0.14, take_halt_flags 0.96 / 0.49, TIM2 ISR 3.53 / 2.29, E-stop 1.33 / 1.06 µs: unchanged from v0.8.4 |
| `test_val_haltwin` on v0.8.5 / v0.8.4 | 7 / 7 PASS / 3 PASS + 4 FAIL (FWR-21 × 2, margin × 2) |
| `pio test -e native` (rc8, all suites) | **243 / 243 PASS** (41 suites, incl. `test_val_haltwin` 7 cases) |
| Validator suites in seeded random order (`VAL_SEED=20261015`) | **58 / 58 PASS** |
| `pytest 02_FW/test/twin` + HIL tests + `00_System/tools/tests` + `test_val_review_gen.py` (private `BEND_TWIN_BUILD_DIR`) | **1 349 passed** (OBS-RC-8 fixed by the Integrator: the log is read next to the private binary) |
| Twin rehearsal `hil_session.py --twin --quick` | 43 steps, rc 0. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A (the same as §13 / §14) |

### 15.5 Final FW verdict for the HW gate

**GO WITH CONDITIONS.** There are no open FW or tool findings from this review: FWR-01…21 are CLOSED, and OBS-RC-1…3 and 6–8 are CLOSED. What remains is target-only or harness-only:
1. **OI-FW-51 / NFR-007 (HG-18, Validator E with A).** DWT sections 11 and 16–18 decide the CRIT_HALT windows. The conservative static bounds of `stop_now` (1.22 µs), `abort` (1.14 µs) and `set_period_now` (1.79 µs) exceed 1 µs; the nominal bounds are ≤ 0.95 µs (D-51). D-52 criteria apply to sections 1–5 and 23.
2. **Target-only items under D-06.** SAF-FW-002 / 004 / 005 on-target timing and count trials, with the random phase covering the 1.5 µs guard window; the `isr_wcet.py` calibration record (OI-FW-46); and the reference-manual premise "CNT = 0 after an OPM stop" (§14.2 b1), confirmed indirectly by the HG-09 count trials.
3. **OI-FW-52** is answered by §15.3: my criterion replaces A's proposal. A can close it in FW_design.

## 16. Re-check of FW v0.8.6 (D-55: step-ISR trim, `step_isr_core`) and updated HW-gate verdict (Validator E, 2026-10-10)

**Inputs.** Working-tree snapshot of 2026-10-10 21:36 (`rc9`). `sha256` over `02_FW/{src,include,tools,platformio.ini}` = `b98a6cad…`. Since §15, `core/motion.c`, `hal/f446/step_tim2.c`, `pure/ramp.h`, `pure/stepgen.h` and FW_design (§9.8 v0.8.6, OI-FW-45 resolved, OI-FW-53) changed. DECISIONS D-55. No hardware; nothing committed; private build and twin directories.

**Verdict: GO WITH CONDITIONS** (§16.5). All four items are clean:
- **(a)** The target entry is behaviourally identical to the twin's seam entry inside the timing budget. A new host suite drives both entries with the same inputs on the real core.
- **(b)** The acc-clamp removal proof is correct, and an exhaustive float check confirms it.
- **(c)** The branch-free selects are correct, including the FWR-05 late latch.
- **(d)** I applied OI-FW-53.

There is one characterisation outside the budget, OBS-RC-9 (informational, no action).

### 16.1 Verdict per item

| Item | Verdict | Evidence |
|---|---|---|
| (a) target entry ≡ seam entry | **identical** (within the D-52 timing budget) | §16.2; `test_val_isrcore` 8 / 8 PASS; mutants caught |
| (b) acc clamp removal | **proof correct** | §16.3; `test_acc_clamp_unreachable_exhaustive` (> 80 M cases) PASS |
| (c) branch-free selects, FWR-05 | **correct** | §16.4; `test_entries_equal_fwr05_late_latch` PASS; the swapped-select mutant fails 7 / 8 cases |
| (d) OI-FW-53 | **applied** (Validator E) | `check_meas_build.py`: `step_isr` → `step_isr_core`. TC-SYS-009-02 **PASS** on the three v0.8.6 images. With the old list it fails: `step_isr` is garbage-collected from both images. |
| FWR-01 / 02 / 05 / 17 / 18 / 20 / 21 | **CLOSED** (unchanged) | The start / halt protocol, the FWR-21 branch, `halt_hw()` and the OPM-stopped branch are unchanged apart from the `s_t2` struct layout. `test_val_steprace`, `test_val_toctou`, `test_val_tickgen` and `test_val_haltwin` PASS. They run the HOST_TEST (seam) form of the handler; `test_val_isrcore` runs the target form. |
| isr_wcet reproduction | **reproduced** | On my rc9 ELF (conservative / nominal µs):<br>- step ISR 2.65 / 1.97<br>- E-stop 1.28 / 0.99<br>- EXTI0/1 2.84 / 1.99<br>- **D-52 sum 5.49 / 3.96**<br>- `stop_now` 1.15 / 0.84, `abort` 1.04 / 0.75, `set_period_now` 1.53 / 0.88, `take_halt_flags` 0.96 / 0.49<br>A's step-ISR, E-stop and D-52 figures agree. |

### 16.2 (a) Target entry vs seam entry

**Disassembly of the target handler** (rc9 `TIM2_IRQHandler` 0x080122dc):
- **Count.** `cnt = s_count + d` (plus `d` for a missed update) is stored to `s_t2.count` (`str r0,[r4,#0]`, 0x08012316). Then the CEN test runs, the late select runs, and `bl step_isr_core` (0x08012336) gets **the same register r0**.
  - The seam form (v0.8.5 and the twin) read `hal_step_count()` inside the core instead. That read returns the value just stored, unless a level-0/1 handler that changes `s_count` pre-empts in between.
  - Every pre-empting stop primitive touches `s_count` only through its UIF check (`halt_hw()`, `step_estop_reaction()`, DEF-M2-01). UIF was cleared at handler entry, so a change needs a **second update inside the step ISR**. That means a pre-emption longer than one step period, while the minimum period is ≥ 10 µs (`max_step_rate_hz` ≤ 100 kHz; 25 µs at the default). The longest pre-emption chain inside the step ISR is one level-1 handler plus the E-stop: ≤ 2.84 + 1.28 µs conservative.
  - So inside the budget the two count sources are identical. Outside it, see OBS-RC-9.
- **Packing and precedence.**
  - The core returns exactly one of: `STEP_NEXT_STOP` (bit 33, period 0), `STEP_NEXT_LAST` (bit 32, period 0), or a period ≥ 1 (`ramp_next_inl` never returns 0).
  - The handler tests the high word first (`cbnz r1`). In that branch bit 33 (`lsls r3,r1,#30; bmi`) tail-calls `halt_hw()` (RAM 0x200002b5); otherwise it takes the LAST branch (OPM read-modify-write under PRIMASK, unchanged). With a zero high word it writes the period (`cbz r0` skips 0).
  - This is v0.8.5's stop > last > period. Since the core's results are exclusive, the order cannot change an outcome. The wrapper `step_isr()` unpacks with the same bit masks.
- **FWR-01 / 02 / 21.** The start sequence, the stop primitives and the FWR-21 re-read are unchanged and use no core result. The step ISR's OPM-stopped branch (CEN = 0) does not call the core, in either version.

**Host test (new, TC-FW-MOT-002-03): `test_val_isrcore`.** The twin no longer runs the exact target entry, so this suite does.
- **Setup.** The unchanged `step_tim2.c` is compiled twice on a TIM2 register model:
  - `tu_core.c` without HOST_TEST: the handler calls `step_isr_core(cnt)`.
  - `tu_seam.c` with HOST_TEST: handler → `step_isr()` → `step_isr_core(hal_step_count())`, which is A's wrapper body.
  - Both call the **real** core in `core/motion.c`. Its state comes from a real MOVE_ABS of 240 steps on A's fake seams, re-created identically for each entry.
- **Compared between the two entries:** every core call (count argument and packed result), every PUL rise time, completed pulses, runts, update events, final count, stop generation, CR1 / CCMR1 / ARR / CCR1 / CNT, and scenario observations.
- **Checked in every run:** the target's count argument equals the HAL count at the call, and the count equals the completed pulses (TRUNCATE: ± the cut pulse).
- **Scenarios:**
  - plain move to LAST;
  - missed update (ISR held over two updates → count estimate → STOP);
  - FWR-05 late latch;
  - CLEAN / TRUNCATE / E-stop halts between ISRs at 10 phases, including the last pulse tick (FWR-21) and the guard region;
  - halts with the ISR pending (DEF-M2-01);
  - E-stop / limit / abort pre-empting the handler before the core is consulted, which exercises the count read timing.
- **Result:** all PASS.
- **Anti-vacuity (mutants on a scratch copy):**

| Mutant | Cases that fail |
|---|---|
| count passed as `cnt − d` | 7 / 8 |
| late select swapped | 7 / 8 |
| packing test `>> 33` instead of `>> 32` | 1 / 8 |

  A precedence mutation (LAST before STOP) cannot be seen, because the core never sets both bits. That is a property of the core, which I checked by inspection.

### 16.3 (b) Removal of the acc clamp

A's proof is correct:
1. The preceding clamp gives `c ≤ RAMP_U32_MAX_F` = 2^32 − 256, the largest float below 2^32. The ternary form is equivalent to the former `if`, including for NaN: both leave NaN unchanged.
2. **`0 ≤ carry < 1` is invariant.** `n = (uint32_t)acc` truncates the non-negative `acc`.
   - For `acc < 2^24`, `n` is exact as a float, and `acc − (float)n` is exact (Sterbenz) and lies in [0, 1).
   - For `acc ≥ 2^24`, `acc` is an integer, so `carry` = 0.
3. **`carry + c ≤ RAMP_U32_MAX_F` in round-to-nearest.**
   - For `c ≥ 2^24` the ulp is ≥ 2, so `carry < 1 ≤ ulp / 2` (never a tie) and the sum rounds to `c`.
   - For `c < 2^24` the sum is < 2^24 + 1.
   - So the removed `if (acc > MAX)` could never fire.
4. The FPU rounding mode is the default round-to-nearest on the M4 and on the host SSE path; there is no FMA contraction, since there is no multiply.
5. **Exhaustive check:** every float c in [2^22, 2^32 − 256] with carry = 1 − 2^-24 (rounding is monotone in carry, so this covers every carry), plus every 61st float in [1, 2^22) with seven carries. No case exceeds the bound, and the carry stays in [0, 1).

### 16.4 (c) The branch-free selects

- **Running-period select in the handler:**
  - Code: `s_cur = late ? late_cur : pre`, compiled as `ldrb late; ldr late_cur; ldr pre; cmp; it ne; movne; str s_cur; strb late = 0`.
  - The two extra volatile loads have no side effect. No context that can pre-empt the step ISR writes `s_late`, `s_late_cur` or `s_pre`: `latch_running()`, `hal_step_set_period()` and `set_period_now()` run at tick level with the ISR masked, and the level-0/1 stop primitives only read them.
  - So the select is equivalent to v0.8.5's branch.
  - The FWR-05 scenario confirms it: the ISR takes the period the pending update started, and `s_late` is cleared. Both entries agree, and the swapped-select mutant is caught.
- **`per_running()` / `ccr_running()` in the stop primitives:** unchanged (a branch on UIF).
- **The `c` clamp:** as in §16.3.

### 16.5 Observation and evidence

| ID | Sev | Observation | Evidence | Owner |
|---|---|---|---|---|
| OBS-RC-9 | Obs. (informational, no action) | **Count source outside the timing budget.** Suppose the step ISR is pre-empted for more than one step period, so a second update happens inside it, and the pre-empting handler halts and counts that update (`halt_hw()` / E-stop UIF check). Then the target entry passes the pre-halt count: the core sees no count fault and returns a period, which is written to the stopped timer. The seam entry (v0.8.5, twin) reads the corrected count: the core flags STEP_FAULT and returns STOP. **In both, the step count is exact and the axis is stopped.** Only the STEP_FAULT / POS_UNCERTAIN verdict differs, and the v0.8.6 behaviour is the more accurate one: no pulse was lost. Not reachable within D-52 (≤ 4.2 µs of pre-emption against a ≥ 10 µs minimum period). | `test_characterise_handler_preempted_beyond_budget`: target call 10 → period 0x1033c (argument 810), seam → STOP (argument 811); count exact in both | – |

| Run | Result |
|---|---|
| Builds (rc9): `nucleo_f446re`, `_meas`, `_meas_dwt`, private dir | rc 0; 0 warnings in our sources; `check_map` M-1…M-4 PASS |
| TC-SYS-009-02 `check_meas_build.py` (OI-FW-53 applied) | PASS (meas 49 / 51, meas_dwt 42 / 51 objects identical, all differences allowed; safety symbols present); the old list: FAIL (`step_isr` missing) |
| Disassembly: `TIM2_IRQHandler` 0x080122dc, `step_isr_core` 0x0800f4b0, `s_t2` 0x20000018 | as in §16.2 / §16.4 |
| `isr_wcet.py` (conservative / nominal) | as in §16.1 |
| `test_val_isrcore` (new) | 8 / 8 PASS; mutants caught (§16.2) |
| `pio test -e native` (rc9, all suites) | **251 / 251 PASS** (42 suites) |
| Validator suites in seeded random order (`VAL_SEED=20261016`) | **66 / 66 PASS** |
| `pytest 02_FW/test/twin` + HIL tests + `00_System/tools/tests` + `test_val_review_gen.py` (private `BEND_TWIN_BUILD_DIR`) | **1 349 passed** |
| Twin rehearsal `hil_session.py --twin --quick` | 43 steps, rc 0. Verdicts: 21 PASS, 13 PARTIAL, 5 OPEN, 4 N/A (unchanged since §13) |

### 16.6 Updated FW verdict for the HW gate

**GO WITH CONDITIONS.** No FW finding is open; FWR-01…21 and OBS-RC-1…3 / 6…8 are CLOSED, and OBS-RC-9 is informational. What changed since §15.5: the static step ISR now meets NFR-007 nominally (1.97 µs ≤ 2 µs), and so does the D-52 sum (3.96 µs ≤ 5 µs). Under the conservative model the step ISR (2.65 µs) and the D-52 sum (5.49 µs) are still above budget. Conditions:
1. **HG-18 (Validator E with A).** The DWT sections decide NFR-007 / D-52: section 1 (step ISR, now including `step_isr_core`), 2, 3/4, 5, 23, the sum of section 1 and the largest level-1 handler, and the CRIT_HALT windows 11 / 16–18 (OI-FW-51). The `isr_wcet.py` calibration record is still needed (OI-FW-46).
2. **Target-only items under D-06.**
   - SAF-FW-002 / 004 / 005 timing and count trials, with the random phase covering the 1.5 µs guard window.
   - HG-09 count trials. These are now also the target evidence that the `step_isr_core` entry counts like the seam form the twin runs.
   - The reference-manual premise "CNT = 0 after an OPM stop".
