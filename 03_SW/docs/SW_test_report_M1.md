# Bird Bend Stand — PC Software Test Report, milestone M1 (SW_test_report_M1)

| Doc | SW_test_report_M1 |
|---|---|
| Version | **1.1 — M1 gate verification + re-test of the fixes** (1.0: first verification, NOT ACCEPTED) |
| Date | 2026-10-03 |
| Author | Validator F — SW |
| Plan | `03_SW/docs/SW_test_plan.md` **v0.2** (M1 corrections §10a) |
| Baseline | SRS v0.4.1 · ICD v0.4.1 (PROTO 1.0, PAYLOAD 1) · `params.yaml` dict 3, PARAM_DICT_HASH **0xF0376293** · vectors: 4 CRC, 164 frames, 11 streams, 509 check vectors, units 128 + 112 + 40 · SW_design v0.3.2 (as-built) · SW_design_GUI v0.3.1 (§15.1 as-built) · D-01…D-36 |
| SW tree | HEAD `ff92061` (M1 implementation checkpoint) + working tree; `03_SW/src/**` unchanged by F. Note: `02_FW/src/core/*.c` was being modified by others during this session (FW M1 follow-up); the twin is rebuilt from the current FW sources, so the X results refer to that FW state. |
| Environment | DEV: Windows 10 Pro 19045, Python 3.14 (`.venv`), PySide6 6.11.2 offscreen, pytest 9.1.1 + qt/cov/randomly. D-06 respected: no COM port opened (root guard + F's extra `serial.Serial.__init__` guard). |

---

## 0. Final verdict (after the re-test, §8)

```
Verdict M1: ACCEPTED WITH CONDITIONS
Scope: SYS-003, SYS-008 (sim + twin subset), SYS-010, IF-001…IF-012, SW-PLT-001…003, SW-CFG-001…004,
       SW-ACQ-001, NFR-002 / NFR-004 (backend parts), NFR-001 smoke
SW tree: ff92061 + working tree incl. B's / D's fix round (SW_design v0.3.3 §15.5c B33-01…10, SW_design_GUI v0.3.2);
         source fingerprint identical at the start and the end of the three runs
Runs (full 03_SW/tests = unit 1880 + gui 171 + integration 48 + validation 380 = 2479 tests):
   #1 fixed order        2474 passed, 4 skipped, 1 xfailed, 0 failed (246.9 s)
   #2 seed 12345         2474 passed, 4 skipped, 1 xfailed, 0 failed (247.5 s)
   #3 seed 995318515     2474 passed, 4 skipped, 1 xfailed, 0 failed (243.2 s)
   → identical; the only xfail is the Integrator's IF-C-M1-02 (ICD v0.5 item); skips = Integrator M2 stop timing.
Validation suite: 380 passed, 0 xfailed — all eleven SWD-M1 defect tests now pass as regression tests.
Coverage (unit + validation): calc 100 / 100 %, io 96.8 / 94.0 %, io.sim 94.5 / 89.6 %, core 95.4 / 89.2 %
   (core.link 96.5 % line, core.gates 97.6 %) — floors met.
Open defects: none.
Conditions:
   MC-1  PO demonstrations DM-02 (fresh install, REF PC), DM-05 (NVM buttons + confirmation, CFG chip) and the
         SYS-008 M1 GUI walk-through — Orchestrator / PO, before M2 exit (automated equivalents pass).
   MC-2  REF PC named and specced (C5 / G6) — Orchestrator / PO, M3 entry.
   MC-3  SRS v0.5: IF-011 wording for RESUME (CONTROL lane, not "never delayed") — Orchestrator, with CR-01.
   MC-4  B: remove the str-equality shim of StopConfirmation before M2 (consumers already use .cmd) and close
         observation OBS-M1-R1 (§8.3) before motion is enabled in M2.
   MC-5  IF-C-M1-02 (DRV-signal bits while FEAT_DRV_SIGNALS = 0) and CR-01 (D-36) in ICD v0.5 — Integrator
         (already in the M1-gate queue).
```

## 1. Verdict of the first verification (v1.0, superseded by §0)

```
Verdict M1: NOT ACCEPTED (formal) — becomes ACCEPTED WITH CONDITIONS when SWD-M1-01 and SWD-M1-02 are fixed
Scope: SYS-003, SYS-008 (sim + twin subset), SYS-010, IF-001…IF-012, SW-PLT-001…003, SW-CFG-001…004,
       SW-ACQ-001, NFR-002 / NFR-004 (backend parts), NFR-001 smoke
SW tree: ff92061 + working tree · ICD 0.4.1 · dict 0xF0376293 · vectors 164 / 11 / 509 / 280
Runs (full 03_SW/tests = unit 1869 + gui 163 + integration 48 + validation 373 = 2453 tests):
   #1 fixed order        2433 passed, 4 skipped, 16 xfailed, 0 failed (318.6 s)
   #2 seed 12345         2433 passed, 4 skipped, 16 xfailed, 0 failed (284.8 s)
   #3 seed 928114780     2433 passed, 4 skipped, 16 xfailed, 0 failed (285.0 s)
   → identical counts in all three runs.
Validation suite alone: 358 passed, 15 xfailed (= 15 open-defect tests, strict); 3 runs identical.
Coverage (unit + validation, branch on, generated + gui omitted): calc 100 % line / 100 % branch,
   io 96.8 % / 94.0 %, io.sim 94.4 % / 89.4 %, core 95.6 % / 88.7 % (core.link 96.5 % line, core.gates 97.6 %);
   GUI (gui + F's GUI tests): 87 %. All floors of plan §2.3 met.
Generators: gen_params.py --check exit 0, gen_vectors.py --check exit 0, --hash 0xF0376293 = params_gen.
Open defects: SWD-M1-01 (S2), SWD-M1-02 (S1, latent until motion), SWD-M1-03…06, 08, 09 (S3), 07, 10, 11 (S4)
```

**Why NOT ACCEPTED.** Plan §7.2 rule: any open S1/S2 → NOT ACCEPTED. Two defects sit in the M1-scope
STOP path (IF-005 CONFIRM class, IF-011 priority path): a lost STOP request is not repeated when the last
known MOVING flag is stale (SWD-M1-02, S1 by the plan's definition "a stop … ineffective"), and STOP / HALT /
PAUSE are not written at all while the link state is LOST (SWD-M1-01, S2). Neither can be reached through the
M1 public API today — the M1 SW cannot command motion — so there is **no hazard in the M1 deliverable**, but both
become live at M2 and the requirements they violate are M1 requirements. Both fixes are small (§4); the two
strict-xfail regression tests flip to failures (XPASS) as soon as they are fixed, which makes re-verification a
single run.

**Path to ACCEPTED WITH CONDITIONS:** fix SWD-M1-01 and -02 (B), re-run the gate suite; the S3/S4 items below then
become dated conditions (M2 entry for SWD-M1-03/04/06/08/09, M3 entry for SWD-M1-05, M1-close for 07/10/11).
Everything else in the M1 scope passed, including the SW⇄twin subset (condition C6 met).

---

## 2. Evidence

### 2.1 Commands

```
QT_QPA_PLATFORM=offscreen
.venv\Scripts\python -m pytest 03_SW\tests -p no:randomly -q -rfEsxX                  (run 1)
.venv\Scripts\python -m pytest 03_SW\tests -p randomly --randomly-seed=12345 -q -rfEsxX (run 2)
.venv\Scripts\python -m pytest 03_SW\tests -p randomly --randomly-seed=928114780 -q    (run 3)
.venv\Scripts\python -m pytest 03_SW\tests\unit 03_SW\tests\validation -p no:randomly --cov=bend_stand --cov-branch --cov-config=03_SW\pyproject.toml
.venv\Scripts\python 00_System\tools\gen_params.py --check   → exit 0 ("48 params, PARAM_DICT_HASH = 0xF0376293")
.venv\Scripts\python 00_System\tools\gen_vectors.py --check  → exit 0 ("4 crc, 164 frames, 11 streams; 509 check; units 128+112+40")
python -m bend_stand --headless --sim --duration 5           → CONNECTED, 80.40 SPS, 452 frames, 0 lost / CRC / timeouts
python -m bend_stand --sim  (offscreen, closed after 8 s)    → rc 0; "link CONNECTED sim; stream on rate 80.40; frames ok 705
                                                                data 638 lost fw 0 link 0 crc 0; ticks 241 p50 33.0 ms p95 33.5 ms"
```

Skips (4) are the Integrator's stop-timing cases deferred to M2 (FW does not drive the stop primitive while idle);
the xfail outside the validation suite is the Integrator's IF-C-M1-02 (DRV-signal bits while FEAT_DRV_SIGNALS = 0,
ICD v0.5 item, already in the M1-gate queue). Logs: scratchpad `validator-f-sw/m1/run*.txt`, `cov*.txt`.

An earlier, aborted run (its parent shell was stopped by F while the suite was being extended) showed subprocess
start failures `0xC0000142` (console detached) — an artefact, not a product result; it was discarded and the three
runs above were made from scratch with a frozen suite.

### 2.2 Validation suite (new, `03_SW/tests/validation/**`)

| File | Level | Content |
|---|---|---|
| `conftest.py` | — | `validation` marker, mandatory `req` (collection error otherwise), extra D-06 guard, `defect` marker, `_reports/trace.json` |
| `harness.py` | — | only module naming the §15 API / hooks; wire log re-parsed with **`ref_codec`** (ground truth); forced-motion helpers (documented, never an oracle); process log by PID |
| `oracle/f_ref.py` | oracle | F's R4 implementation (P1) + ICD §9.3 retry table, priority set, IF-012 list, H1–H5 from ICD §11.4 |
| `oracle/fboard.py` | oracle | **F-board**: F's own FW stand-in over TCP built on `ref_codec` + `params.yaml` (other PROTO major / PAYLOAD / hash, link-attributed gaps, duplicates, u16 wrap, raw frame errors) |
| `test_v_protocol.py` | P | CRC, 11 streams + 1 MB seeded fuzz vs `ref_codec.FrameParser`, all request vectors byte-identical, all FW→PC vectors decoded, numpy batch, retry classes vs ICD, IF-012 checklist, units vectors |
| `test_v_connect.py` | C | connect order, H2-safe session writes, sim-server endpoint, M1 headless workflow, IF-008 on the F-board |
| `test_v_config.py` | C | metadata vs `params.yaml`, board-config file round trip + crafted files, H1/H3/H4/H5 write order (every intermediate SET checked against H1–H5), local refusals, REJECTED / MISMATCH / TIMEOUT, REBOOT_REQUIRED → SAVE → REBOOT, SAVE / LOAD / DEFAULTS / CFG_DIRTY, power cut during SAVE |
| `test_v_link.py` | C | RETRY (new SEQ, ≤ 2 retries, late response), VERIFY (SAVE / LOAD / DEFAULT / REBOOT / RESUME / clears never re-sent, GET_STATUS resolution, D-31/D-34 races), CONFIRM repeats / alarm / stream-off polling, IF-011 priority with busy queues, bandwidth, corruption counters, FW vs link loss attribution, missed conversions, heartbeat 120 s, pipeline stall, reconnect, rt STOP latency |
| `test_v_stream.py` | C | stream toggle / gates, recorder = sent frames exactly once (incl. FW drops, events), ENOSPC never silent, duplicates / link gaps (F-board), **1 h device-time soak** |
| `test_v_gui.py` | G | STOP everywhere (toolbar, docks docked / floating, every M1 dialog, non-native file dialogs, AST check of dialog bases), ConfirmDialog keyboard rules, UNKNOWN indicators, config tab, IF-008 read-only GUI, Stream on every tab, CFG / reboot offer, `--sim` smoke |
| `test_v_twin.py` | X | A's firmware in the twin over `tcp://`: connect, write/verify/NVM/reboot, one frame per conversion + FW drop attribution, STOP/HALT/PAUSE/RESUME/clear, **sim vs twin identical outcomes** |
| `test_v_static.py` | U / I | Qt-free backend import (subprocess), calc purity, R4 TV-TC / TV-L / TV-M planner, frame-gap rule, ICD-name inspection, no oracle imports / hand tables, serial settings, generators, origin notes / tags, runtime + requirements |

Requirement coverage of the validation suite (from `_reports/trace.json`): every M1 requirement has ≥ 1 passing
validation test — SYS-003 (12), SYS-008 (8), SYS-010 (5 + 1 xfail), IF-001 (28), IF-002 (1), IF-003 (177),
IF-004 (22), IF-005 (176 + 1), IF-006 (118), IF-007 (14), IF-008 (124 + 3), IF-009 (48), IF-010 (5), IF-011 (35 + 4),
IF-012 (74), SW-PLT-001 (2), SW-PLT-002 (12), SW-PLT-003 (11), SW-CFG-001 (2), SW-CFG-002 (2), SW-CFG-003 (21 + 1),
SW-CFG-004 (13), SW-ACQ-001 (5), NFR-002 (5), NFR-004 (1), NFR-001 (1, informative); early M3 items checked as
well: SAF-SW-002/-003/-004/-005, SW-STOP-001…004, SW-ACQ-002/-004.

Processes started by F (all stopped through their own handles, by recorded PID — `_reports/processes.log`):
simulator server (`python -m bend_stand.io.sim.server`, e.g. PID 30752), GUI smoke (PID 33916, rc 0),
fw_twin engines (e.g. 32948, 6176, 29508, 35900, 37536, all rc 0). One leftover pytest of the aborted run
(PIDs 24932 / 36792) could not be stopped by F (permission denied) and ran to its normal end on its own.

---

## 3. Per-TC results (M1 scope)

Legend: **P** pass · **P\*** pass with a defect / note · **F** fail (open defect, strict xfail) · **pend** needs PO / REF / HW.

| TC | Result | Evidence / note |
|---|---|---|
| TC-SYS-003-01 | P | units vectors 280/280 exact (production = F oracle), VV-U rounding, TV-U |
| TC-SYS-008-01 (M1 subset) | P | headless connect → read → write/verify → SAVE → 10 s stream (795–815 frames, 0 loss, 80.4 SPS) → stream off/on → disconnect |
| TC-SYS-008-02 (X, M1) | P | twin: connect order, compat OK, defaults + NVM_DEFAULTED; H1/H4 writes OK, SAVE seq + 1, REBOOT → resync, values from NVM; frames = conversions (±2 in flight), FW drops attributed exactly; HALT/PAUSE/RESUME/clear; scripted sequence on sim and twin gives **identical** outcomes. Integrator's integration suite 43 pass / 4 skip / 1 xfail (IF-C-M1-02). |
| TC-SYS-010-01 | P\* | origin notes with path @ hash everywhere; `Implements:` in all backend modules; 2 GUI modules without tag → SWD-M1-07 |
| TC-IF-001-01 | P | every `pg.<Enum>.<NAME>` used (> 60 distinct) exists in the ICD tables; no oracle import, no hand-written code tables |
| TC-IF-002-01 | P | 921 600 Bd 8N1, no flow control, timeout set once, no per-read re-configuration, factory/`endpoints()` open nothing |
| TC-IF-002-02 | pend | HW gate |
| TC-IF-003-01 | P | 11/11 streams (frames + counters); 1 MB fuzz × 2 seeds identical to `ref_codec`, every inserted frame recovered |
| TC-IF-003-02 | P | 47 requests byte-identical (INVALID_PADDING rejected, unknown TYPE / framing-only at frame level), 116 FW→PC decoded; `get_info_resp_longer` accepted |
| TC-IF-004-01 | P | CRC vectors, 0x29B1 / 0xFFFF, bad-CRC vector dropped + counted |
| TC-IF-004-02 | P | corrupted response: crc_errors + 1 exact, retried with new SEQ; corrupted PC→FW HALT at the FW: no action, no response, FW `rx_crc_errors` + 1 |
| TC-IF-005-01 | P | 1 drop → retry after 100–110 ms, new SEQ; 3 drops → 3 frames, CommandTimeout; late response counted; PING retry |
| TC-IF-005-02 (M1 part) | P\* | SAVE / LOAD / DEFAULT / REBOOT / RESUME / HALT_CLEAR / FAULT_CLEAR / ESTOP_CLEAR: exactly one frame, GET_STATUS resolution; D-31 / D-34 races (new PAUSE / STOP-button HALT / E-stop after the lost response) → NOT_CONFIRMED, latch kept; M1 API sends no motion frame. Motion commands: M2. |
| TC-IF-005-03 | P | duplicated response: one resolution, nothing re-sent |
| TC-IF-005-04 | P | retry class of all 25 commands = ICD §9.3; priority set = ICD §2.4; JOG by v |
| TC-IF-006-01 | P | all DATA vectors per frame and numpy batch; other PAYLOAD_VERSION not decoded (F-board) |
| TC-IF-007-01 | P | FW drops (tx_congestion) = sim `sent` dropped count exactly → frames_lost_fw; link gaps = F-board skips exactly (8/8); duplicates counted, not recorded; u16 wrap no anomaly; missed conversions = F's R4 §9 count on the sent log |
| TC-IF-008-01 | P\* | major / payload → read-only, config + NVM + motion refused locally, nothing on the wire, DATA v2 not decoded; hash → config read-only, session values still written + verified; minor higher compatible. **Clears not refused** in read-only → SWD-M1-03 |
| TC-IF-008-02 | P | GUI: form read-only, Write / NVM / Defaults disabled, version line warns |
| TC-IF-009-01 | — | M3 |
| TC-IF-010-01 | P | both `--check` exit 0, hash equal, vector `icd_version` = implemented |
| TC-IF-011-01 | P\* | STOP / HALT / PAUSE written at the call instant as the next frame with busy job queue + lane + empty bucket. **RESUME and the clears are queued** (≈ 2 s behind a SAVE) → SWD-M1-04 |
| TC-IF-011-02 | P | 60 s: FW→PC ≤ 10 % of 92 160 B/s; DATA 26 B × 80.4 Hz (2 090 B/s, 2.27 %) |
| TC-IF-012-01 | P | all IF-012 commands + RESUME: builder, ICD code / LEN, vector |
| TC-SW-PLT-001-01 | P (I) / pend (D) | 3.14 64-bit, requirements = the 4 runtime packages pinned and installed; `--sim` smoke rc 0. Fresh install on REF = DM-02 (PO) |
| TC-SW-PLT-002-01 | P | backend (≥ 30 modules) imports with PySide6 / shiboken6 / pyqtgraph blocked; calc pure |
| TC-SW-PLT-002-02 (M1) | P | TV-U, TV-TC µm↔steps, TV-L unwrap (+ ICD 26-B budget), TV-M planner, VV-SEQ-04 durations |
| TC-SW-PLT-003-01 | P | ICD §9.5 order; all pages; session values verified before STREAM_START; H2-safe order (max before min) from a hostile start |
| TC-SW-PLT-003-02 | P | F-board: 2 CRC, 1 LEN, 1 inter-byte timeout → counters + 2 / + 1 / + 1 exactly |
| TC-SW-PLT-003-03 | P | `sim`, out-of-process sim server (tcp), twin (tcp): same connect behaviour |
| TC-SW-CFG-001-01 | P | 48 values = GET_ALL_PARAMS on the wire; metadata = `params.yaml` |
| TC-SW-CFG-001-02 | P | every row, board value, typed editor, session rows locked |
| TC-SW-CFG-002-01 | P | round trip exact (enum names, f32), session values excluded; unknown / missing / range / type / enum / hash reported; newer schema + truncated refused; no SET on recall |
| TC-SW-CFG-003-01 | P\* | H1, H3 (both directions), H4, H5 orders keep every rule after every SET; local RULE / RANGE / LOCKED / UNKNOWN refusals with nothing sent; REJECTED (status + detail), MISMATCH, TIMEOUT (3 SEQs); REBOOT_REQUIRED → REBOOT_PENDING → SAVE → REBOOT. **While moving: whole set NOT_ATTEMPTED instead of BUSY** → SWD-M1-08 |
| TC-SW-CFG-003-02 | P | per-row OK / REJECTED / MISMATCH in the GUI |
| TC-SW-CFG-004-01 | P (C) / pend (D) | SAVE seq + 1, CFG_DIRTY 0/1/0, LOAD restores, DEFAULTS = yaml defaults + session values re-verified, power cut keeps old record; GUI CFG chip + Save & reboot offer. Demonstration DM-05 with the PO pending |
| TC-SW-ACQ-001-01 | P | stream toggle on every tab, state follows the board, frame_seq continuous, STREAM_STOP refused while moving (forced motion) |
| TC-NFR-001-02 | P (informative) | offscreen: refresh p50 33.0 / p95 33.5 ms, 0 lost; acceptance on REF (PR-1, M3) |
| TC-NFR-002-02 | P | 100 × `stop()` real clock: p95 ≪ 50 ms (call → write) |
| TC-NFR-004-02 | P | 1 h device time (≈ 289 k frames, 4 u16 wraps), recording on: 0 async overflow, 0 rows lost, 0 FW / link losses, 0 anomalies, recording = board's sent frames exactly once; RSS growth after 15 min ≤ 50 MB |

Early checks of M3 items (informative, not gate-relevant): heartbeat 120 s lock-step max gap ≤ 250 ms and PING
only after ≥ 150 ms idle (stream on / off); heartbeat stops with a stalled pipeline; CONFIRM repeats every 50 ms,
≤ 20 tries + `stop.unconfirmed`; recorder ENOSPC never silent; ConfirmDialog keyboard rules (C-04, C-11); STOP in
every M1 window / dialog; UNKNOWN indicators; HALT source + clear hint.

### 3.1 Demonstrations

| Item | State |
|---|---|
| `python -m bend_stand --sim` offscreen smoke; `--headless --sim` smoke | done by F (§2.1) |
| WP-D8 inputs: offscreen walk-through (disconnected → connected/streaming → write with REBOOT_REQUIRED → SAVE → HALT) with window grabs | done by F (scratchpad `m1/dm_*.png`; offscreen has no fonts — layout only); state checks: CONNECTED, 80.4 SPS, CFG clean after SAVE, reboot pending, HALT ON source PC |
| DM-02 fresh install on REF (SW-PLT-001) | **pending — PO / REF PC (G6)** |
| DM-05 NVM buttons with confirmation, CFG chip (SW-CFG-004) | **pending — PO** |
| GUI workflow demonstration of SYS-008 (M1 subset) | **pending — PO** (automated equivalent passed) |

---

## 4. Defects (code review + tests)

| ID | Sev | To | Finding (file:line) | Evidence | Fix hint |
|---|---|---|---|---|---|
| **SWD-M1-01** | **S2** | B | `core/device.py:499-504` `_priority_stop` refuses STOP / HALT / PAUSE with "not connected" whenever `state` ∉ {CONNECTED, DEGRADED} — also in **LOST**, although transport, writer and channel are still open (the FW→PC direction may be the broken one; reconnect GET_INFO frames even keep the FW link watchdog alive). Violates IF-011 / SW-STOP-001 ("never delayed"). | `test_v_link.py::test_stop_attempted_while_link_lost` (strict xfail): after 1 s board silence `stop()` → `sent=False`, no frame. Mitigation today: the automatic DATA-loss STOP at 500 ms precedes LOST; GUI shows "STOP NOT SENT". | Write priority frames whenever a channel exists (any state but DISCONNECTED); report transport errors in `StopResult` as designed. |
| **SWD-M1-02** | **S1** (latent) | B | `core/device.py:515-526` STOP's CONFIRM predicate is `not MOVING and _fresh_after_send()`, and `_fresh_after_send()` is a stub returning `True`: a stale MOVING = 0 (≤ 1 frame after a move start, or a stale STATUS with the stream off) "confirms" the STOP at the first tick, so a **lost STOP request is never repeated** — ICD §9.3 CONFIRM / IF-005. | `test_stop_lost_request_repeated_until_moving_clears` (forced motion, strict xfail): STOP 4 ms after an accepted MOVE_ABS, request dropped → 1 STOP frame only, axis still moving after 1.5 s (14.5 mm). Not reachable via the M1 API (no motion), live at M2. | Confirm only by the ACK or by MOVING = 0 in a DATA / STATUS frame **received after** the STOP write (compare `t_ns` with `t_write_ns`). |
| SWD-M1-03 | S3 | B | `core/gates.py:124-159` clear gates lack `_ro()`; `core/backend.py:608-620` `clear_stop_async` checks only CONFIRM items, `estop_clear_async` / `fault_clear_async` no gate at all → in the IF-008 read-only state HALT_CLEAR / ESTOP_CLEAR / FAULT_CLEAR are sent (ICD §9.5: "no clears-and-enable"); also a clear is sent when the gate says NOTHING_TO_CLEAR or LINK_DOWN. `g_valid_toggle` (l. 175) also allows SET_VALID read-only. | `test_v_connect.py::test_tc_if_008_01_read_only_refuses_clears_locally[*]` (3 strict xfail, F-board PROTO 2.0) | Add `_ro(s)` to the clear / valid gates and refuse every `*_clear_async` on a REFUSE item (failed future `GateRefused`). |
| SWD-M1-04 | S3 | B (+ Orchestrator: SRS ↔ ICD) | SRS IF-011 (v0.4.1) lists RESUME and the clears among frames "never delayed by SW queues", but RESUME and HALT/ESTOP/FAULT_CLEAR are Worker **jobs** (`core/backend.py:557-563, 608-620` → `core/jobs.py:100` "one at a time"), and RESUME additionally uses the CONTROL lane + token bucket (`core/link.py:79`). A clear clicked during a SAVE waits ≈ **2.0 s**; behind queued reads ≈ 1 s. (ICD §2.4 names only STOP/HALT/PAUSE + clears for the priority path — RESUME missing there.) | `test_tc_if_011_01_resume_and_clears_not_delayed_by_queues[RESUME, HALT_CLEAR, FAULT_CLEAR]` (strict xfail); probe: HALT_CLEAR 1997 ms after the click with a SAVE in progress | Write the clear / RESUME frame immediately on the priority path from the caller thread and run only the GET_STATUS resolution as a job (or a second, priority worker); Orchestrator/Integrator: align ICD §2.4 / §9.3 with SRS IF-011 for RESUME. |
| SWD-M1-05 | S3 (M3 req.) | B | `core/backend.py:660, 666` HALT / PAUSED **source** is read only from the last GET_STATUS (`halt_src` / `pause_src`); EVENT HALT_SET / PAUSED carry the source in `arg` but are ignored → for up to 1 s (STATUS poll) the indicator shows the stale source "NONE" (SAF-SW-005: ≤ 200 ms after the carrier). Related queue item GF-18 (NONE → None). | `test_latch_source_within_200ms_of_its_event[halt, paused]` (strict xfail; press right after a poll); twin run shows source NONE until the next poll | Take the source from the EVENT arg (and DATA latch edge), keep STATUS as confirmation; map NONE to `None`. |
| SWD-M1-06 | S3 | B + D | `core/device.py:528-532` publishes `stop.confirmed` / `stop.unconfirmed` with a bare **str** payload (§15.3 says frozen dataclasses); `gui/widgets/stop_banner.py:182` reads `payload.cmd` → falls back to "STOP": a HALT / PAUSE confirmation or the "NOT CONFIRMED after 1 s – use the E-stop" alarm is shown as **STOP** | `test_v_gui.py::test_stop_banner_names_the_command_of_the_confirmation[confirmed, unconfirmed]` (strict xfail) | B: publish a dataclass (`cmd`, `source`, `attempts`, `t_ns`); D: test against the real payload type (fake currently differs). |
| SWD-M1-07 | S4 | D | `gui/settings.py`, `gui/widgets/status_led.py` have no `Implements:` tag (SYS-010 convention) | `test_tc_sys_010_01_implements_tags_gui` (strict xfail) | Add tags. |
| SWD-M1-08 | S3 | B | `core/params.py:90-91` a non-`moving_ok` key while moving is an ERROR issue → `write_and_verify` aborts the **whole** set as NOT_ATTEMPTED; SW_design §5.3 / SRS SW-CFG-003: that key BUSY (not sent), the `moving_ok` keys written | `test_tc_sw_cfg_003_01_non_moving_ok_key_while_moving_is_busy` (forced motion, strict xfail): both items NOT_ATTEMPTED, nothing sent | Make MOVING a per-key BUSY result in the write plan instead of a set-level error. |
| SWD-M1-09 | S3 | B | `core/recorder.py:88-89` folder name has 1 s resolution and `mkdir(exist_ok=False)`: stop + restart within the same second (same marks) → refused with the raw OS text (localised, "[WinError 183] …") | `test_recording_restart_within_one_second` (strict xfail) | Add a suffix (`_2`, …) or ms to the folder name; user-level text. |
| SWD-M1-10 | S4 | D | `gui/widgets/stop_banner.py:102-104` banner "LINK LOST – STOP sent; sequence terminated" is shown whenever the link is LOST, also when idle and no STOP was sent | inspection (§4.6: STOP only while moving) | Text from the actual stop result / motion state. |
| SWD-M1-11 | S4 | B | simulator `io/sim/board.py:248-266`: after `inject hang` the HX711 model emits the overdue conversions with their old timestamps → backward `t_us`, the pipeline starts a spurious time epoch ("FW_RESET: device time stepped backwards") | probe (scratchpad `m1/probe3.py`): frame t_us 4 062 000 → 1 124 380 after a 3 s hang | Re-schedule the AFE (`schedule_from(now)`) at the end of a hang (as after `stall`). |

Observations (no defect): liveness faults are not yet wired to a STOP (`LivenessMonitor.on_fault` unset — the
motion reaction belongs to M2/M3); `CLEAR_HINTS["HALT"]` still mentions the STOP button (GF-19 / CR-01 in the
queue); the twin reports DRV_PWR = 0 while FEAT_DRV_SIGNALS = 0, so the GUI shows "driver power lost" against
the M1 FW (IF-C-M1-02 in the queue); `Backend.reboot` is not refused in the read-only state (harmless).

---

## 5. Conditions C1–C7 (from the P1 plan)

| C | Content | Status at M1 |
|---|---|---|
| C1 | SRS v0.4 with D-30/D-31/D-32 deltas, ambiguous ACs confirmed | **met** (SRS v0.4.1, D-35) |
| C2 | ICD v0.4 RESUME + vectors; SW_design aligned | **met** (ICD v0.4.1, 509 vectors, SW_design v0.3.2) |
| C3 | validation hooks (SWD-P1-09 a–h) | **met** (lock-step backend, wire log on all transports, rx log, per-command drop/dup/delay/corrupt, sent log, on_frame/on_event, any-OSError fail_recorder; `set_free_space` stored, M3). Gaps covered by F's F-board: other INFO versions/hash, per-DATA-frame link loss / duplicates (cf. SW-C-M1-01) |
| C4 | GUI design aligned | **met** (SW_design_GUI v0.3.1) |
| C5 | REF PC + Python 3.11 | 3.11: **closed** by D-33 i; REF PC: **open** (G6, due M3 entry) |
| C6 | FW twin + X subset | **met** — twin builds A's firmware; F's X subset 5/5 and the Integrator's suite pass (IF-C-M1-02 open in the gate queue) |
| C7 | steady-state mask vs POS_UNCERTAIN | **met** (D-33 b; verification M4) |

## 6. Items for other roles

- **B:** SWD-M1-01, -02 (blocking), -03, -04, -05, -06 (with D), -08, -09, -11.
- **D:** SWD-M1-06 (with B), -07, -10.
- **Orchestrator / Integrator:** SWD-M1-04 — SRS IF-011 vs ICD §2.4 / §9.3 for RESUME on the priority path.
- **PO (via Orchestrator):** demonstrations DM-02, DM-05 and the SYS-008 M1 GUI walk-through.

## 8. Re-test of the fix round (v1.1)

B reported SWD-M1-01/02/03/04/05/06/08/09/11 (+ GRQ-B-20, GF-18/19/21; SW_design v0.3.3 §15.5c B33-01…10) and
D SWD-M1-06/07/10 (SW_design_GUI v0.3.2) fixed. The Orchestrator decided that RESUME stays on the CONTROL lane
(SRS IF-011 wording to be corrected in v0.5).

### 8.1 What F did
- Removed every `xfail(strict=True)` marker (now regression tests, still tagged `defect("SWD-M1-nn")`); moved the
  confirmation checks to `StopConfirmation.cmd` / `.confirmed` (no reliance on the str-equality shim).
- Reworked the IF-011 RESUME case per the decision (plan M1-C8): the clears must be written at the call instant on
  the priority path; RESUME is submitted at once on the CONTROL lane, ≤ 12 ms with busy queues, never behind
  Worker jobs.
- Added independent re-tests of the STOP-path fixes (`test_v_link.py`, section "re-test"): STOP / HALT / PAUSE
  written at the call instant in DEGRADED, LOST and CONNECTING (9 cases); STOP request lost 3× while moving → 4 frames
  50 ms apart with distinct SEQs, confirmed with attempts = 4, axis stopped; every STOP request lost while moving →
  15…20 frames within 1 s, `stop.unconfirmed` (cmd STOP), no false confirmation; idle STOP lost → confirmed only by a
  DATA frame received after the write.
- Re-ran the full `03_SW/tests` three times (§0) and the coverage run; reviewed the diffs of `core/device.py`,
  `core/link.py`, `core/backend.py`, `core/gates.py`, `core/params.py`, `core/recorder.py`, `core/model.py`,
  `io/sim/board.py`, `io/protocol.py`, `gui/widgets/stop_banner.py`, the two GUI tag files.

### 8.2 Per-defect result

| ID | Fix (reviewed) | Re-test | State |
|---|---|---|---|
| SWD-M1-01 | `_priority_stop` writes whenever a channel and an open transport exist | `test_stop_attempted_while_link_lost`, `…written_in_every_link_state_with_open_transport[DEGRADED/LOST/CONNECTING]` | **closed** |
| SWD-M1-02 | confirmation = ACK or the indication in a DATA / STATUS with receive stamp > write time; 50 ms × ≤ 20 unchanged | `test_stop_lost_request_repeated_until_moving_clears`, `…every_50ms_until_ack`, `…at_most_20_then_unconfirmed`, `…confirmed_only_by_a_frame_after_the_write` | **closed** (residual race OBS-M1-R1) |
| SWD-M1-03 | `_ro()` in the clear / valid gates; `_clear_refusal` refuses LINK_DOWN / COMPAT_READ_ONLY locally (other items left to the FW NACK — acceptable, a clear never starts motion) | `test_tc_if_008_01_read_only_refuses_clears_locally[3]` | **closed** |
| SWD-M1-04 | `Device.clear_async`: clears written at once on the priority path from the caller thread, outcome by callbacks (no Worker job); RESUME submitted at once on the CONTROL lane | `test_tc_if_011_01_clears_not_delayed_by_queues[3]` (written at the call instant with 40 queued jobs + empty bucket), `…resume_on_control_lane_not_behind_jobs` | **closed**; RESUME per decision (MC-3) |
| SWD-M1-05 | source from EVENT HALT_SET / PAUSED arg, STATUS as fallback, `None` instead of "NONE" | `test_latch_source_within_200ms_of_its_event[halt/paused]` | **closed** |
| SWD-M1-06 | `StopConfirmation(cmd, source, attempts, t_ns, confirmed)` payload; banner reads `.cmd` | `test_stop_banner_names_the_command_of_the_confirmation[2]` | **closed** (shim removal: MC-4) |
| SWD-M1-07 | tags added | `test_tc_sys_010_01_implements_tags_gui` | **closed** |
| SWD-M1-08 | MOVING → per-key BUSY (WARN issue), the other keys written | `test_tc_sw_cfg_003_01_non_moving_ok_key_while_moving_is_busy` | **closed** |
| SWD-M1-09 | `unique_folder` (`_2`, `_3`, …), user-level error text | `test_recording_restart_within_one_second` | **closed** |
| SWD-M1-10 | "STOP sent" only after a sent STOP within 5 s | inspection of `stop_banner.link_lost_text` | **closed** |
| SWD-M1-11 | AFE rescheduled at the end of a hang | indirect: no spurious epoch in the reconnect / LOST tests | **closed** |

### 8.3 Review observations (no defect)
- **OBS-M1-R1 (to B, before motion in M2):** the STOP confirmation uses the PC *receive* stamp. A DATA frame the
  FW produced before it executed a MOVE_ABS still in flight when STOP was pressed can arrive after the STOP write
  with MOVING = 0 and confirm a STOP whose request was lost. The window is a few ms and needs a lost STOP plus a
  motion command in flight; it is not reachable through the M1 API. Proposal: confirm only from a frame whose FW
  `t_us` is later than the FW time of a frame known to follow the STOP's arrival (e.g. its ACK or a later status),
  or ignore indications for one frame period after the write while a motion command is outstanding.
- `StopConfirmation.__eq__` (str) and `__hash__` on `cmd` only are a temporary compatibility shim (MC-4).
- `core/api.py` re-exports `BIT_PREFIX`, `bit_key` (GRQ-B-20) through a late import with `noqa` — cosmetic.
- `clear_async` refuses in LOST ("not connected", nothing sent) while STOP / HALT / PAUSE are sent in LOST —
  consistent ("a clear needs a confirmed link").
- The simulator resumes its 1 ms tick at "now" after a hang (ticks inside the hang are skipped) — model choice.

### 8.4 Processes
Every process F started in the re-test (simulator servers, GUI smokes, fw_twin engines) was stopped through its
own handle — `_reports/processes.log`: 21 started, 21 stopped / exited.

## 9. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-10-03 | Validator F | M1 gate report: validation suite (373 tests), 3 full runs identical (2433 / 4 / 16), coverage, X subset, defects SWD-M1-01…11, verdict NOT ACCEPTED (formal; S1/S2 in the STOP path, latent until M2). |
| 1.1 | 2026-10-03 | Validator F | Re-test of the fix round: SWD-M1-01…11 closed (regression tests), new STOP-path re-tests, RESUME per the Orchestrator decision (plan M1-C8), three runs identical (2474 / 4 / 1), coverage re-run; final verdict **ACCEPTED WITH CONDITIONS** (MC-1…MC-5), observation OBS-M1-R1. |
