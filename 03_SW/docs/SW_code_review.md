# SW code review — PC software, whole codebase (Validator F, Task 6)

| Item | Value |
|---|---|
| Document | `03_SW/docs/SW_code_review.md` |
| Version | 1.5 — 2026-10-10 (§8.9 D-54 a / c) · 1.4 — 2026-10-10 (§8.8 round 6, final HW-gate verdict) · 1.3 — 2026-10-10 (§8.7 add-on) · 1.2 — 2026-10-09 (§8 fix verification) · 1.1 — 2026-10-09 (re-check against the working tree after B's tasks 4/7, D's GUI follow-ups and packaging, §7.1) · 1.0 — 2026-10-08 |
| Author | Validator F (SW) |
| Anchor | commit **e600169** ("P2 complete …"). Every `file:line` below is a line of that commit (`git show e600169:<path>`), paths relative to `03_SW/src/bend_stand/` unless stated otherwise |
| Baseline | SRS v0.6.4, ICD v0.7.4, SW_design v0.6, SW_design_GUI v0.6 |
| Scope | Whole `03_SW/src` (backend `core`, `io`, `calc`; GUI bridge / shutdown / stop paths; simulator only where the product ships it), `requirements*.txt`, `pyproject.toml`, `run*.bat`. Not milestone-scoped |
| Reproducers | `03_SW/tests/validation/test_v_review.py` — one test per reproducible finding, `xfail(strict=True)` while open (§7) |
| Hardware | none (D-06): lock-step simulator, unit-level drivers, the fake hotkey backend |

## 1. Summary

28 findings: **0 × S1, 5 × S2, 10 × S3, 13 × S4**. 14 of them have a reproducer in `test_v_review.py` (16 strict-xfail tests, all fail
today for the stated reason, checked with `--runxfail`); the others are analysis findings with the code path as
evidence.

The stop paths themselves are sound: STOP / HALT / PAUSE never raise, they bypass the Worker and the lanes, the
CONFIRM repetition with device-time ordering (D-37 d) is implemented as designed, and the FW keeps its own
protections (load limit, limit switches, E-stop, link watchdog). The weak points are around them:

* **Supervision of the supervisor (SWR-01, SWR-02).** The liveness reactions of SW_design §4.6 are not implemented
  (`LivenessMonitor.on_fault` is never set, `FAULT_LIMITS_MS` is unused), and one exception in an async-frame
  handler silently drops the rest of a 64-frame batch. A stalled or faulty Pipeline thread therefore leaves motion
  without the per-frame SAF-SW-001 evaluation — reproduced: 1.5 s of DATA received but not evaluated during a move,
  no STOP.
* **A persisted session bypasses the load-input rule (SWR-03).** A session saved with both PC load limits off starts
  the next run (no tare: session-only) with motion allowed, no LOAD_INPUT_INVALID refusal and no no-specimen banner
  — against SAF-SW-001's last sentence.
* **Test-record integrity (SWR-04, SWR-05, SWR-07).** VALID can stay 1 after a refused ramp step; a row arriving
  while a recording stops is silently moved into the next recording; closing the application during a sequence
  loses the run log while `meta.json` claims a complete recording.
* **Loaders (SWR-06, SWR-12).** A session value of the wrong type stops the application from starting; deeply nested
  JSON escapes the `FileFormatError` contract.

Areas reviewed and found correct are listed in §5 (framing / resync / buffer bounds, CRC, integer wrap handling,
STOP confirmation, HTML escaping of operator text, path sanitising, no `eval`/`pickle`/`subprocess`/shell, no
secrets).

**Verdict: ACCEPTED WITH CONDITIONS.** Conditions: SWR-01, SWR-02 and SWR-03 fixed (or accepted by the PO as known
limitations with the FW protections as the path of record) before the first test with a mounted specimen at the HW
gate; SWR-04, SWR-05, SWR-07 fixed before recordings are used as test evidence; SWR-14 (log file) before the HW gate
so that incidents leave evidence. The other S3 / S4 items can follow in the normal backlog (§6).

## 2. Severity scale

| Severity | Meaning |
|---|---|
| **S1** | Can cause injury or damage directly; a safety function of the SRS does not work. Fix before any further use |
| **S2** | A safety requirement is weakened (another layer — FW, operator — still protects), or test data can be wrong / lost without notice. Fix before loaded tests / before recordings count as evidence |
| **S3** | Robustness, availability, diagnosability or a design-rule deviation with a plausible trigger; workaround exists |
| **S4** | Minor: unlikely trigger (FW defect, file tampering), defence in depth, maintainability |

## 3. Findings

| ID | Sev | Area | Title | Anchor (e600169) | Reproducer | Owner |
|---|---|---|---|---|---|---|
| SWR-01 | S2 | liveness / safety | Liveness faults have no reaction; DATA received but not evaluated while moving → no STOP | core/liveness.py:22, :43, :84; core/backend.py:663, :745; core/pipeline.py:176; core/device.py:785, :800 | `test_swr01_…` | B |
| SWR-02 | S2 | pipeline | One handler exception drops the rest of the async batch (DATA and EVENTs) | core/pipeline.py:210–226, :223, :247, :250, :199–207; core/backend.py:923–945 | `test_swr02_…` | B |
| SWR-03 | S2 | safety / session | Session with both load limits off bypasses the SAF-SW-001 load-input rule at the next start | core/backend.py:404–414, :392–394, :200 | `test_swr03_…` | B (+ Orchestrator) |
| SWR-04 | S2 | sequencer | VALID stays 1 after a refused capture-during-move step | core/sequencer/executor.py:803, :810, :1134 | `test_swr04_…` | B |
| SWR-05 | S2 | recorder | Row arriving during `stop()` is lost uncounted and written into the next recording | core/recorder.py:185–197, :225–257 | `test_swr05_…` | B |
| SWR-06 | S3 | session loader | Wrong-type session values: `Backend()` raises / `status()` raises | core/session.py:89–118, :105, :80; calc/limits.py:98; core/motion.py:200; core/backend.py:404–414 | `test_swr06_…` ×2 | B |
| SWR-07 | S3 | shutdown / recorder | Closing during a sequence loses the run log; `meta.json` says complete | core/backend.py:761–796, :767, :785; core/sequencer/executor.py:1137–1147 | `test_swr07_…` | B |
| SWR-08 | S3 | gates / config | `config_write` not refused while an operation runs; config API applies no gate | core/gates.py:149–156; core/backend.py:154–170 | `test_swr08_…` | B |
| SWR-09 | S3 | hotkey | Hung hotkey thread still reported REGISTERED (ping answer never evaluated) | io/win_hotkey.py:497, :510, :519; core/backend.py:823, :1008 | `test_swr09_…` | B |
| SWR-10 | S3 | stop path | `send_priority` loses the write time when the ACK wins the race → STOP reported "not written" | core/link.py:322–329; core/device.py:603–606 | `test_swr10_…` | B |
| SWR-11 | S3 | report | Sidecar values interpolated into `report.html` without escaping | core/report.py:523–524, :72–86 | `test_swr11_…` | B |
| SWR-12 | S3 | file loaders | Deeply nested JSON → `RecursionError`, not `FileFormatError`; start-up crash on a corrupt active calibration | core/schema.py:47; core/sequencer/seqfile.py:147; core/report.py:97, :565, :110–135; core/backend.py:1209–1213, :412 | `test_swr12_…` ×2 | B |
| SWR-13 | S4 | motion API | NaN target passes `check()`; `move_to` raises `ValueError` | core/motion.py:277, :359 | `test_swr13_…` | B |
| SWR-14 | S3 | logging | No log file (SW_design §14 rotating file not implemented) | \_\_main\_\_.py:90 | analysis | B |
| SWR-15 | S3 | stop path / locks | Channel lock held across transport writes and multi-frame pumps: STOP waits behind lane frames | core/link.py:304–305, :356–375, :377–410, :125 | analysis | B |
| SWR-16 | S3 | sequencer | Sequence `pull_dir` independent of the session `pull_dir`; mismatch loads the specimen the wrong way | core/sequencer/executor.py:774, :968; core/backend.py:1293; core/sequencer/model.py:281 | `test_swr16_…` | Orchestrator + B |
| SWR-17 | S4 | concurrency | Unsynchronised shared state (warnings set; Device indication fields from two threads) | core/safety.py:288, :417; core/device.py:264–274, :381–392 | analysis | B |
| SWR-18 | S4 | callbacks | Decode error in a done-callback leaves a ClearResult / MoveTicket future unresolved | core/device.py:684; core/motion.py:411 | analysis | B |
| SWR-19 | S4 | reconnect | `board_changed` computed, never used | core/device.py:336 | analysis | B |
| SWR-20 | S4 | test hooks | `_step_with_stalls` skips `Backend._tick` parts (jog refresh, seq tick, thresholds, hotkey ping) | core/testing.py:46–65 | analysis | B |
| SWR-21 | S4 | availability | Report build runs on the single general Worker | core/backend.py:640, :1198 | measured | B |
| SWR-22 | S4 | robustness | Received parameter values not range/finite-checked; `ValueError` (NaN → JSON) not caught in record start / calibration accept | core/params_gen.py:117–122; core/backend.py:1181–1188; core/schema.py:40; core/calibration/load.py:273 | analysis | B |
| SWR-23 | S4 | CSV | Formula injection in `samples.csv` (marks start with `=`/`+`/`-`/`@`) | core/recorder.py:104–106, :123–135 | analysis | B |
| SWR-24 | S4 | report CLI | Offline `--cal` override not validated (schema / status / K) | core/report.py:165–171, :269 | analysis | B |
| SWR-25 | S4 | session | Broken `--session` / default session file overwritten with defaults by the next auto-save | core/backend.py:404–414 | analysis | B |
| SWR-26 | S4 | shutdown | Shutdown ordering: sequence not terminated before the MOVING check / disconnect; timeout path skips DISCONNECTED | core/backend.py:761–796, :779 | analysis | B |
| SWR-27 | S4 | dependencies | No hash-pinned lock; transitive dependencies unpinned | `03_SW/requirements*.txt`, `03_SW/pyproject.toml` | analysis | B |
| SWR-28 | S4 | sequencer memory | Run log (events, windows, results, scale log) grows without bound in a count-0 (until stopped) loop | core/sequencer/executor.py:401, :577, :889, :914, :928, :1181 | analysis | B |

## 4. Finding details

### SWR-01 (S2) — liveness faults have no reaction; a stalled Pipeline leaves motion unsupervised

**Where.** `core/liveness.py:43` declares `on_fault`; no module assigns it (grep over `src`: only the declaration and
the call at `:89`). `FAULT_LIMITS_MS` (`:22`, Reader 100 / Pipeline 200 / runner 300 / GUI 2000 ms) is never read.
`Backend.__init__` (`core/backend.py:663`) creates the monitor and `start()` (`:745`) installs the excepthook, but
nothing reacts to `record_fault` (also called on a full async queue, `core/pipeline.py:176`). The only consumer of the
beats is the heartbeat gate (`core/device.py:785`), besides the MC3-4 diagnostics.

**Why it matters.** SAF-SW-001 needs every received DATA frame evaluated (STOP ≤ 50 ms). The DATA-loss STOP
(`core/device.py:800`) keys on `last_data_ns`, which the **Reader** thread updates (`:269`) — it stays silent as long as
DATA bytes arrive, even when the **Pipeline** thread (where `SafetySupervisor.process` runs) is stalled. The heartbeat gate
does close, but other frames keep the FW link watchdog fed (STATUS poll 1 Hz, JOG refresh every 80 ms, SET_VALID), so
the FW watchdog does not stop the axis either. SW_design §4.6 (e600169 `03_SW/docs/SW_design.md:419–423`) requires:
"Pipeline > 200 ms or DATA received but not evaluated > 200 ms … while moving → priority STOP + `terminate_all` +
event". Same gap for the GUI (> 2 s while moving), the operation runner (> 300 ms) and uncaught thread exceptions.

**Evidence.** `test_swr01_pipeline_stall_while_moving_sends_stop`: 100 mm move at 5 mm/s, Pipeline stalled 1.5 s via
`test_hooks.stall_thread("pipeline")`; wire during the stall: `GET_STATUS`, `PING` — no STOP.

**Fix hint.** In the Supervisor tick: evaluate beat ages against `FAULT_LIMITS_MS` and "newest received DATA host time −
newest processed DATA host time > 200 ms"; set `liveness.on_fault` to a handler that, while moving (`fw_moving()` or
`motion.busy` or a jog session), sends the priority STOP, calls `terminate_all("LIVENESS")`, records an event; idle → warning
event. Gate the STATUS poll / jog refresh with the same liveness gate as the heartbeat, so a dead backend lets the FW
link watchdog act.

### SWR-02 (S2) — one handler exception drops the rest of the async batch

**Where.** `Pipeline.step` (`core/pipeline.py:210–226`) pops up to 64 frames into a local batch and processes them
without per-frame isolation: `_event` calls `on_fw_event` (`:247`), the event sinks (`:250`) and the bus. Only the
safety stage and the DATA sinks are guarded. `_run` (`:199–207`) logs "pipeline step failed" and loops — the
remaining frames of the batch are gone (no counter). `Backend._on_fw_event` (`core/backend.py:923–945`) is not guarded
either and does real work in the Pipeline thread (`Device.handle_fw_event`, `MotionController.on_fw_event`,
`self.resume("button")` which builds the whole gate snapshot).

**Impact.** Up to 63 DATA frames (≈ 0.8 s) skip SAF-SW-001; an ESTOP_SET / HALT_SET / STOPPED in the same batch does not
terminate the sequence (SW-STOP-003 "within one received frame"); loss counters stay at 0.

**Evidence.** `test_swr02_handler_exception_does_not_drop_following_frames`: EVENT(LIMIT_SET, handler raises once),
DATA, EVENT(ESTOP_SET) queued → `data_frames = 0`, ESTOP_SET never dispatched.

**Fix hint.** `try/except` per frame inside the batch loop (and around `on_fw_event`, each event sink and the publish);
count `dispatch_errors`; record a liveness fault (→ SWR-01 reaction). Guard `_on_fw_event` sub-steps individually so a
defect in one reaction does not skip `terminate_all`.

### SWR-03 (S2) — a session with both load limits off bypasses the load-input rule

**Where.** `SessionAPI.load_default` (`core/backend.py:404–414`) applies the saved `LimitConfig` without
`LimitsAPI.check`; the explicit `SessionAPI.load` (`:392–394`) runs the check but **excludes** `LOAD_INPUT_INVALID`.
The rule in `LimitsAPI.check` (`:200`) only fires on an on → off transition.

**Impact.** SAF-SW-001 (e600169 SRS line 174, last sentence): "the only way to move without valid load limits is the
no-specimen mode". The tare is session-only (SW-TARE-002), so after every restart the load input is invalid; with a
saved "limits off" session the motion gates show no LOAD_INPUT_INVALID, there is no no-specimen confirmation (SAF-SW-004)
and no banner (SW-LIM-004). Only the FW load limit protects (at its nominal default without calibration).

**Evidence.** `test_swr03_…`: default session with `pull_enabled = push_enabled = false` → `load_input_valid = False`,
`no_specimen_mode = False`, MOVE gate without LOAD_INPUT_INVALID. Review run (scratch script): ENABLE → HOME (confirmed)
→ MOVE to 10 mm completed, no SW trip, no refusal.

**Fix hint.** Either (a) on every session load, a load limit that is off while the input is invalid is switched back
on (issue "PC load limits re-enabled: no valid calibration + tare"), or (b) the motion gate treats "a load limit off,
input invalid, no-specimen mode off" exactly like LOAD_INPUT_INVALID. (b) also covers a calibration that becomes
invalid later (AFE change). Orchestrator: decide whether SW-LIM-003 ("recalled … applied at start") needs a sentence.

### SWR-04 (S2) — VALID stays 1 after a refused capture-during-move step

**Where.** `_travel` sends SET_VALID 1 before the move (`core/sequencer/executor.py:803`), then `move_to` (`:810`). If
the move is refused (local gate, FW NACK, `NOT_EXECUTED`) `_motion` ends the run STOPPED / STEP_REFUSED; `_finish`
clears VALID only for `LINK_LOST` / `ERROR` (`:1134`). The FW clears VALID on every operational stop — a refused move is
not a stop.

**Impact.** SW-SEQ-004 (SRS line 378): VALID = 0 at window end and on pause / stop / abort, every VALID = 1 frame inside
a planned window. After the end every DATA frame (manual mode, later recordings) carries VALID = 1 until the operator
toggles it.

**Evidence.** `test_swr04_…`: ramp step, injected NACK `E_STATE` on MOVE_ABS → run ends STOPPED / STEP_REFUSED, wire
`SET_VALID 1` then `MOVE_ABS`, no SET_VALID 0; last DATA flags `VALID, HOMED, ENABLED`.

**Fix hint.** In `_finish` send SET_VALID 0 whenever `run.valid_on` (any end reason; it is a SAFETY-lane RETRY frame and
harmless if the FW already cleared it), or send SET_VALID 1 only after the MOVE_ABS ACK.

### SWR-05 (S2) — row arriving during `Recorder.stop()` leaks into the next recording

**Where.** `stop()` (`core/recorder.py:225–257`) drains once (`:234`), then flushes, `fsync`s, closes and rewrites the
sidecar (`:252`, again with `fsync`) while `state` is still `RECORDING`; `state = "IDLE"` only at `:255`. Rows the
Pipeline thread enqueues in that window stay in `_q`; `start()` (`:185–197`) resets the counters but not `_q` / `_gap`.

**Impact.** SW-ACQ-002 "every frame exactly once", SW-ACQ-004 "never drop rows silently": the old recording reports
`complete: true, rows_lost: 0` although rows are missing; the next recording starts with rows of the previous one (other
`t_us`, possibly another time epoch). The window is tens of ms (two `fsync`s) at 80 Hz — a few rows per stop.

**Evidence.** `test_swr05_…` (interleaving forced by patching `atomic_write_json` to deliver one row while the sidecar is
written): recording 1 rows `[0 … 50000]`, rows_lost 0; recording 2 rows `[1237500, 2500000]` (first row belongs to
recording 1).

**Fix hint.** Set a `STOPPING` state (enqueue → `rows_lost += 1`) before the final drain, or swap the queue under the lock
at the start of `stop()`; clear `_q`, `_gap`, `_warned_fill` in `start()`.

### SWR-06 (S3) — wrong-type session values crash start-up or `status()`

**Where.** `session.from_dict` (`core/session.py:89–118`) copies JSON values into `LimitConfig` unchecked (`:105`);
`validate` → `check_limit_config` (`calc/limits.py:98`) compares with `<` → `TypeError` for a string; the travel fields
are not validated at all at load; `load_default` (`core/backend.py:404–414`) catches only `FileFormatError`.

**Impact.** `"pull_trip_n": "100"` in the default session → `Backend()` raises `TypeError` → the application cannot
start until the operator deletes the file by hand. `"travel_max_mm": "5"` (enabled) is accepted → `status()` raises on
every call (`core/motion.py:200`), the GUI refresh fails, `SafetySupervisor._travel` raises per frame (logged as "safety
stage failed"; motion is refused because the gates raise too).

**Evidence.** `test_swr06_session_wrong_type_does_not_prevent_start`, `test_swr06_session_string_travel_limit_…`.

**Fix hint.** Typed parse in `from_dict` as in `seqfile` (`_num` finite, `bool` strictly, `None` where allowed) →
`FileFormatError`; run the travel rules of `LimitsAPI.check` at every load; `load_default` → defaults + issue on any
exception.

### SWR-07 (S3) — closing during a sequence loses the run log

**Where.** `Backend.shutdown` (`core/backend.py:761–796`) stops the recorder first (`:767`), terminates the sequence only
in `finally` (`seq.shutdown`, `:785`, or via the DISCONNECTED transition). `SequenceExecutor._finish`
(`core/sequencer/executor.py:1137–1147`) appends the run log to `recorder.meta` only while the recorder is
RECORDING / FAILED — it is IDLE by then.

**Impact.** SW-ACQ-002 / SW-REP-001 / SW-REP-003: `meta.json` has no `sequence_runs` (windows, results, events, scale
log), so the report cannot be rebuilt, and `integrity.complete = true` hides the aborted run. The GUI asks C-09 before
closing during a sequence, but the confirmed close loses the data.

**Evidence.** `test_swr07_…`: 2-step sequence, one window done, `shutdown()` → `meta.json` keys without
`sequence_runs`; review run printed `integrity: {'complete': True, 'rows_written': 269, …}`.

**Fix hint.** In `shutdown`: `seq.terminate("shutdown")`, STOP if moving, step / join the sequencer until `_finish` has
run (bounded, e.g. 2 s), then stop the recorder; or let `recorder.stop()` mark `complete: false` while a sequence is
active and accept a late `sequence_runs` rewrite.

### SWR-08 (S3) — `config_write` not refused while an operation runs

**Where.** `g_config_write` (`core/gates.py:149–156`) refuses only link down / read-only and WARNs while moving. The
SW_design gate table (e600169 `SW_design.md:738`) also requires REFUSE for "any operation running". `ConfigAPI`
(`core/backend.py:154–170`) applies no gate at all (write / save / load / defaults / reboot).

**Impact.** `motion.steps_per_mm` is not `moving_ok` but is applied at once, and "every µm position rescales"
(params.yaml): written during a PAUSED sequence or inside the travel-calibration wizard it moves the remaining absolute
targets, the SW travel limits and the FW soft limits physically (FW limit switches and load limit still protect).
LOAD / DEFAULT_PARAMS during an operation reset the session thresholds under it.

**Evidence.** `test_swr08_…`: sequence PAUSED → gate `CONFIG_WRITE` has no item; `SET_PARAM motion.steps_per_mm = 4000`
reaches the wire.

**Fix hint.** REFUSE `OPERATION_RUNNING` / `SEQUENCE_RUNNING` when owner ≠ MANUAL, a sequence is active or paused, or an
engine is active; enforce the gate inside the `ConfigAPI` methods (failed future `GateRefused`).

### SWR-09 (S3) — hung hotkey thread still reported REGISTERED

**Where.** The Supervisor pings every 250 ms (`core/backend.py:823`), the thread stamps `last_beat_ns`
(`io/win_hotkey.py:497`), but `beat_age_ms` (`:519`) is never read; `mode` (`:510`) and `hotkey_status`
(`core/backend.py:1008`) only check `thread.is_alive()`.

**Impact.** SW-STOP-002 "the GUI shows whether the hotkey is active"; SW_design §4.6 (e600169 line 422) asks for the
warning "Pause/Break key unavailable" when the 250 ms ping is missed. A hung message loop (e.g. blocked in the HALT call)
keeps showing REGISTERED and the sequence-start CONFIRM `HOTKEY_UNAVAILABLE` is not raised.

**Evidence.** `test_swr09_…` (real clock, fake backend `stall()` for 1.5 s): mode `REGISTERED`.

**Fix hint.** `hotkey_status()` → `UNAVAILABLE` ("hotkey thread not responding") when the beat age > 750 ms; publish
`hotkey.state` on the edge.

### SWR-10 (S3) — `send_priority` can report a written STOP as "not written"

**Where.** `CommandChannel.send_priority` (`core/link.py:322–329`) submits, releases the lock, then searches `_inflight`
for the request to get its write time. If the Reader thread matched the ACK in between (fast link, a GIL hand-over —
switch interval 1 ms after `timing.init()` — or the in-process simulator with zero latency), the request is gone → `t = None`; `Device._priority_stop`
(`core/device.py:603–606`) then returns `StopResult(sent=False, "not written")` and publishes it on `stop.issued`.

**Impact.** The operator sees a failed STOP / HALT for a frame that was written and acknowledged (false alarm in the
stop banner / log); the confirmation is not armed (harmless, it was ACKed).

**Evidence.** `test_swr10_…` (ACK delivered right after `submit()` returns): `fut` done OK, `t_write = None`. Matches the intermittent failure of `test_tc_nfr_002_02_backend_stop_latency_rt` at `assert r.sent` under load (§7.1).

**Fix hint.** Record the write time on the request / future inside `_send_locked` (e.g. `fut.t_sent_ns`) and return it
from `submit` for priority commands; treat "future done without exception" as written + acknowledged.

### SWR-11 (S3) — sidecar values rendered into `report.html` without escaping

**Where.** `render_html` (`core/report.py:523–524`) interpolates `r.exec_idx + 1`, `r.step_idx + 1` and `r.n` raw;
`result_from_dict` (`:72–86`) coerces only float fields, so `n` (int field) keeps whatever type the JSON had.
Operator text (marks, custom fields, labels, sequence name) **is** escaped (§5).

**Impact.** SW-REP-003 rebuilds reports offline from recordings that may come from another PC; a crafted `meta.json`
executes script in the browser that opens the report (file:// origin). Non-numeric `exec_idx` crashes the build instead.

**Evidence.** `test_swr11_…`: `n = "<script>alert(1)</script>"` appears verbatim; marks / label / name are escaped.

**Fix hint.** Coerce types in `result_from_dict` (int / str, `FileFormatError` on failure); escape every interpolation;
add `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">` — the report
needs no script.

### SWR-12 (S3) — loaders: `RecursionError` / parse errors escape the `FileFormatError` contract

**Where.** `schema.read_json` (`core/schema.py:47`) and `seqfile.loads` (`core/sequencer/seqfile.py:147`) catch
`ValueError` only; `json.loads` raises `RecursionError` for deep nesting. The report reader (`core/report.py:97`, `:565`,
row parsing `:110–135` with bare `int()` / `float()`) raises `RecursionError` / `ValueError`. Start-up paths catch only
`FileFormatError`: `_load_active_calibration` (`core/backend.py:1209–1213`), `load_default` (`:412`).

**Impact.** A corrupt `active_load.json` (deep nesting) makes `Backend()` raise — no start. SW-SEQF-001 ("invalid file
gives an error") holds only because the GUI catches `Exception`; the board-config dialog catches `FileFormatError` only.
A truncated / corrupt `data.csv` row makes the report CLI exit with a traceback instead of exit code 2.

**Evidence.** `test_swr12_sequence_file_deep_nesting_is_a_file_error`, `test_swr12_corrupt_active_calibration_…`
(`RecursionError: Stack overflow … while decoding a JSON array`).

**Fix hint.** Catch `RecursionError` (and `TypeError` / `ValueError` in field parsing) → `FileFormatError` with the file
name; optionally refuse files above a size cap (e.g. 20 MB for JSON) before parsing; report rows: `FileFormatError`
with the line number.

### SWR-13 (S4) — NaN target passes the motion check

**Where.** `MotionController.check` range test (`core/motion.py:277`) is false for NaN on both ends; `move_to` then
raises `ValueError("cannot round nan")` from `_um` (`:359`) instead of returning a refused future.

**Evidence.** `test_swr13_…`. The GUI spin boxes cannot produce NaN, so the trigger is an API caller / generator.

**Fix hint.** `math.isfinite` on target / speed / accel in `check()` → REFUSE; `move_to` / `move_by` never raise.

### SWR-14 (S3) — no log file

**Where.** `__main__.py:90` configures `logging.basicConfig` (stderr only). SW_design §14 (e600169 line 1696): rotating
file `%APPDATA%\BirdBendStand\logs\bend_stand.log` (10 × 5 MB) — not implemented (no `FileHandler` anywhere in `src`).

**Impact.** LINK LOST diagnostics (MC3-4), "safety stage failed", "pipeline step failed", liveness faults and uncaught
thread exceptions vanish with the console window (`run.bat`); no post-incident evidence at the HW gate.

**Fix hint.** `RotatingFileHandler` for the `bend_stand` loggers (INFO) in `main()` for GUI and headless; log the
`threading.excepthook` / `sys.excepthook` records there.

**Note (after e600169).** The packaging task in progress (uncommitted, `03_SW/packaging/`) builds a windowed
`BirdBendStand.exe` whose runtime hook redirects stdout / stderr to `%APPDATA%\BirdBendStand\logs`. That covers the frozen
GUI build only (no rotation / size bound checked); source runs (`run.bat`) still log to the console. Re-check at the
next commit.

### SWR-15 (S3) — channel lock held across transport writes

**Where.** `CommandChannel` writes under its `RLock`: `submit` → `_send_locked` → `writer.write` (`core/link.py:304–305`,
`:377–410`); `_pump_locked` (`:356–375`) writes up to four lane frames plus the held clears in one lock hold (called
from `tick()` and `on_response()`). A STOP from the GUI or the hotkey thread waits for the whole hold; each serial
write may block up to the 30 ms write timeout. `FrameWriter`'s guarantee "a priority writer waits at most for the one
frame already being written" (`:125`) is bypassed one level up.

**Impact.** IF-011 ("never delayed by SW queues") / NFR-002 (≤ 50 ms p95) depend on serial writes being fast; under USB
back-pressure the STOP can queue behind several frames. Not reproducible deterministically (needs a slow port); the
NFR-002 / NFR-003 perf runs on the reference PC (MC4-2 / MC4-3) are the measurement.

**Fix hint.** Reserve SEQ + in-flight entry under the lock, write outside it; or let priority commands take a path that
only touches the `FrameWriter` priority lock.

### SWR-16 (S3) — two sources of `pull_dir`

**Where.** The executor uses the sequence's `pull_dir` for the approach direction and the guard sign
(`core/sequencer/executor.py:774`, `:968`); the SafetySupervisor and the motion trip-direction rule use the session's
(`core/backend.py:1293`). `model.validate` only checks ±1 (`core/sequencer/model.py:281`); `check_start` never compares
them. The sequence tab edits the sequence value; files carry it.

**Evidence.** Review run (simulator spring, both sides, 50 N/mm, contact at the start position; target +100 N): sequence
`pull_dir = +1` → FINISHED at the target; sequence `pull_dir = −1` against the session +1 → the axis loaded the specimen
in **compression** until the SLIP guard stopped it at −36 N. For larger targets the guard threshold grows with the target
(5 %). `test_swr16_…` asserts the review expectation (flag the mismatch at start).

**Fix hint / decision (Orchestrator).** `pull_dir` is a machine property: keep it in the session only (sequence files
store it for information and `check_start` REFUSEs or CONFIRMs a mismatch), or make the sequence value authoritative
for every consumer during the run.

### SWR-17 (S4) — unsynchronised shared state

* `SafetySupervisor.active_warnings` sorts the `warnings` set in the GUI thread (`core/safety.py:288`) while the Pipeline
  thread adds / discards (`:417`). Safe today only because the GIL is not released while `sorted()` copies a set of
  `str` in C; on a free-threaded interpreter (3.14t) it raises `RuntimeError: Set changed size during iteration` in
  `status()`. Latches use an atomic swap; warnings should too.
* The stop-confirmation inputs `last_flags`, `last_status`, `last_flags_ns`, `last_flags_dev`, `_ind_status_sent_ns` are
  written by the Reader (`_on_async`, `core/device.py:264–274`, also the poll callback) and by the Worker
  (`get_status_job` → `_apply_status`, `:381–392`) without a lock; the D-37 d predicate reads them as one record. A torn
  read is improbable and errs mostly on the safe side, but nothing guarantees it. Fix: one immutable indication record
  swapped atomically.

### SWR-18 (S4) — futures left unresolved on a malformed STATUS

`clear_async.on_status` (`core/device.py:684`) and `MotionController._on_verify_status` (`core/motion.py:411`) decode
STATUS inside a done-callback; a `ValueError` is swallowed by the future machinery → the ClearResult future never
resolves (GUI waits), or the MoveTicket never resolves and `_active` stays set (all motion refused "axis moving" until
reconnect). Trigger: FW defect only. Fix: `try/except` → NOT_CONFIRMED / NOT_EXECUTED.

### SWR-19 (S4) — `board_changed` never used

`core/device.py:336` computes it at every connect; SW_design §4.6 says reconnect "compares the board UID". A different
board after an auto-reconnect keeps the test travel zero, the SW travel limits and the travel-calibration decision state
(the tare is UID-bound, so load limits fail safe). Fix: on a UID change reset `x_zero`, publish an event and require an
operator acknowledgement.

### SWR-20 (S4) — stall test hook skips parts of the Supervisor tick

`TestHooks._step_with_stalls` (`core/testing.py:46–65`) calls `device.tick` (`:59`) instead of `Backend._tick`: while any
stall is active there is no jog refresh, no `seq.tick`, no automatic threshold rewrite, no hotkey ping and no supervisor
beat. Stall-based tests therefore see the FW dead-man end a jog that the real backend would keep refreshing. Fix: call
`be._tick(now)` unless `"supervisor"` itself is stalled. (Validator tests using stalls: SWR-01 uses a MOVE, not a jog.)

### SWR-21 (S4) — report build on the general Worker

`ReportAPI.build_async` (`core/backend.py:640`) and the pending report after `record_stop` (`:1198`) run
`build_report` synchronously inside a job on the single FIFO Worker. Measured: 288 000-row (1 h) synthetic recording →
0.96 s (8 h ≈ 8 s). Meanwhile reconnect, threshold rewrite after a tare, SET_VALID 0 after reconnect and config jobs
wait. Fix: run reports on their own thread / executor.

### SWR-22 (S4) — received values and NaN serialisation

* Parameter values from the board are not checked against the dictionary range / finiteness
  (`ParamMeta.unpack`, `core/params_gen.py:117–122`, used by `read_all_params_job`). A FW defect value (e.g. NaN
  `motion.steps_per_mm`) propagates into caps and recordings. Mark such values invalid and keep them out of motion
  computations.
* `atomic_write_json(..., allow_nan=False)` (`core/schema.py:40`) raises `ValueError` for any NaN: `record_start_with`
  catches only `RecorderError` / `OSError` (`core/backend.py:1181–1188`) → exception into the caller and an empty
  recording folder; `LoadCalEngine._accept` catches `(OSError, FileFormatError)` (`core/calibration/load.py:273`) → the
  wizard stays in ACCEPT. Catch `ValueError`, or sanitise with `report._js` before writing.

### SWR-23 (S4) — formula injection in `samples.csv`

`append_sample` (`core/recorder.py:123–135`) writes marks (`specimen`, `number`, `operator`, `notes`, custom) as leading
CSV fields; `_csv_text` (`:104–106`) removes separators only. A value starting with `=`, `+`, `-`, `@` is executed as a
formula when the file is opened in a spreadsheet. Fix: prefix such fields with `'` (or quote + prefix).

### SWR-24 (S4) — offline calibration override not validated

`_load_cal` (`core/report.py:165–171`) loads any JSON for `--cal`; no `bird.bend.cal.load` schema / version check, no
`validate_load`, a FAIL calibration or K = 0 / NaN is applied without a warning (`:269`). Fix: `read_json(…, "cal.load")`
+ `validate_load`, and a report warning when the status is not PASS.

### SWR-25 (S4) — broken session file overwritten

`load_default` (`core/backend.py:404–414`) sets `self.path` before parsing; a broken file (from `--session` or the
default) is ignored, and the first accepted change auto-saves the defaults over it — the operator's file is lost. Fix:
keep auto-save off (or write to a new name) until a file was loaded successfully.

### SWR-26 (S4) — shutdown ordering

`Backend.shutdown` (`core/backend.py:761–796`) checks MOVING once, then submits the disconnect job; the sequencer runner
keeps running meanwhile and can issue the next MOVE_ABS in that window (not reproduced: in lock-step the disconnect
finished within 2 ms, before the next DATA frame). If the Worker is busy (`result(timeout=2.0)`), `_close_link()`
(`:779`) closes the port without the DISCONNECTED transition, so `terminate_all` runs only at `seq.shutdown`. The FW link
watchdog bounds the effect to ≤ 1 s of motion. Fix (with SWR-07): terminate the sequencer and engines first, then STOP if
moving, then disconnect.

### SWR-27 (S4) — dependency pinning

`requirements.txt` / `requirements-dev.txt` pin the direct dependencies exactly but without hashes; transitive packages
(`shiboken6`, `pluggy`, `iniconfig`, `packaging`, `colorama`, …) are unpinned; `pyproject.toml` has no upper bound for
`numpy` and `pyqtgraph`. The reference-PC installation is therefore not reproducible bit-for-bit. Fix: a hash-pinned lock
(`pip-compile --generate-hashes` or `pip freeze` + hashes) for runtime and dev, installed with `--require-hashes`.

### SWR-28 (S4) — unbounded run log in an endless loop

A loop with `count = 0` runs until stopped (`core/sequencer/model.py:72`). Every executed step appends to
`run.events` (`core/sequencer/executor.py:577`, several entries per step), `run.windows` (`:889`, `:914`),
`run.results` (`:928`) and possibly `run.scale_log` (`:401`); nothing is bounded, and `run_log()` serialises all of
it into `meta.json` at the end (`:1181`). A cyclic test over a weekend (≈ 10⁵…10⁶ steps) grows the process by
hundreds of MB and writes a sidecar of the same order — outside the NFR-004 memory budget, which is defined for
1 h. Fix: keep the full per-step record in the recording (`data.csv` E rows already carry it) and only a bounded
tail / aggregates in memory and in `sequence_runs`; or spill the run log to a JSON-lines file next to `data.csv`.

## 5. Reviewed areas without findings (evidence)

| Area | Checked | Result |
|---|---|---|
| Frame receiver (ICD §2.3) | `io/framing.py`: LEN > 160 → discard one byte; CRC mismatch → discard one byte; inter-byte timeout after an empty read only; lone trailing `A5` | Buffer bounded by one read chunk + one maximal frame; `test_swr_evidence_decoder_bounded_under_adversarial_input` (2 MB adversarial input, 600 rounds) passes; output equals the oracle on all vector streams (existing suites) |
| CRC | `io/crc.py` `binascii.crc_hqx` with init 0xFFFF = CCITT-FALSE; bitwise reference kept | correct |
| Integer handling | 32-bit `t_us` unwrap (pipeline, executor `_unwrap`, `serial_ge`), `frame_seq` 16-bit gaps, EVENT SEQ 8-bit gaps, SEQ allocation skipping pending values; `struct` packing of requests only after gate checks | correct (Python ints cannot overflow; out-of-range packs raise before writing) |
| Unbounded buffers | Reader `rx_log` / wire log (deques with maxlen), Pipeline queue 4096 (overflow counted — reaction missing: SWR-01), recorder queue 60 s (overflow → `rows_lost` + REC_GAP), EventBus history 10 000, Worker queue 256, ring buffer fixed | bounded |
| Stop path | `Backend.stop/halt/pause` never raise; priority commands bypass lanes / bucket / Worker; NVM quiesce lets STOP / HALT / PAUSE through; CONFIRM repetition 50 ms × 20 / 1 s with device-time ordering; motion epoch drops queued motion | as designed (latency caveat SWR-15, reporting race SWR-10) |
| Safety supervisor rules | per-class latches, saturated = ±∞, travel rule with planned end points, prediction for unbounded motion, LOAD_INPUT_INVALID STOP once per motion, warning hysteresis | correct for every frame that reaches it (SWR-01 / SWR-02 are about frames that do not) |
| Threshold manager | write order keeps H2, GET_PARAM read-back exact, clamp inward, target identity check (`matches`), provider error → defaults (never widened) | correct |
| Calibration engines | travel wizard restore rule (RAM-only trial value, restore record, post-sync resolution); load-cal mass rules, FAIL not activatable, WARN needs confirmation; tare refusals, offset warning | correct; tare under load is bounded by the inward clamp of the absolute raw thresholds |
| Executable data | no `eval` / `exec` / `pickle` / `marshal` / `yaml.load` / `subprocess` / `shell=True` in `src`; `importlib` only for the fixed module `bend_stand.gui.app`; `QDesktopServices.openUrl` only for the report file path | none |
| File writes | atomic writes (temp + `fsync` + `os.replace`); newer `schema_version` refused; recording folder names sanitised (`test_swr_evidence_recording_folder_names_are_sanitised`); calibration file names sanitised | correct |
| Report HTML | operator marks, custom fields, labels, sequence name, events, warnings escaped (`test_swr_evidence_report_escapes_operator_marks`) | correct (sidecar numbers: SWR-11) |
| Network | simulator server binds loopback only; TCP transport only to operator-selected endpoints; no COM port opened without selection (D-06 guards in the suites) | correct |
| Secrets / PII | no credentials, keys or personal paths in `src`; recordings contain the operator name by design (SW-META-001) | none |

## 6. Recommended fix order

1. **SWR-01, SWR-02** — restore supervision of the per-frame safety stage (liveness reactions per SW_design §4.6,
   per-frame isolation). Small, local changes in `core/pipeline.py`, `core/liveness.py`, `core/backend.py`.
2. **SWR-03** — close the session bypass of the load-input rule (with the Orchestrator's choice of mechanism).
3. **SWR-04, SWR-05, SWR-07** — integrity of the test record (VALID after a refused step, recorder stop/start race,
   run log at shutdown; SWR-26 in the same change).
4. **SWR-14** — log file before the HW gate.
5. **SWR-06, SWR-12, SWR-25** — loader robustness at start-up.
6. **SWR-08, SWR-16** (decision first), **SWR-10, SWR-09, SWR-15**.
7. **SWR-11** and the S4 batch (SWR-13, SWR-17 … SWR-24, SWR-27, SWR-28).

Every fix turns its strict-xfail test into an XPASS failure: remove the `xfail` marker in the same change (owner
Validator F on request) so the test guards the fix.

## 7. Evidence

Commands (repository root, `.venv`, no hardware):

```
.venv\Scripts\python -m pytest 03_SW\tests\validation\test_v_review.py -p no:randomly -rfEsxX
.venv\Scripts\python -m pytest 03_SW\tests\validation\test_v_review.py -p no:randomly --runxfail --tb=line
.venv\Scripts\python -m pytest 03_SW\tests\validation\test_v_review.py -p randomly
.venv\Scripts\python -m pytest 03_SW\tests -p no:randomly -rfEsX
.venv\Scripts\python -m pytest 03_SW\tests -p randomly
```

`test_v_review.py`: 19 tests — 16 strict xfail (each fails at its own assertion; `--runxfail` output checked: no
set-up error, no unrelated exception), 3 evidence tests pass.

| Run | Command | Result |
|---|---|---|
| R1 | `test_v_review.py -p no:randomly` | 3 passed, 16 xfailed |
| R2 | `test_v_review.py -p randomly --randomly-seed=1` | 3 passed, 16 xfailed |
| R3 | `test_v_review.py -p randomly --randomly-seed=987654` | 3 passed, 16 xfailed |
| R4 | `test_v_review.py -p no:randomly --runxfail` | 16 failed (each at its own assertion), 3 passed |
| F1 | `03_SW\tests -p no:randomly` (full suite, 2026 s) | 3449 passed, 16 xfailed, **5 failed** — all five in files under concurrent edit by other roles (`gui/test_m4_sequence.py::test_chart_marker_rate_at_least_10_hz`, 4 × the then-untracked `unit/core/test_m3_calibration_paths.py`); the same 5 pass in isolation afterwards (19 passed) |
| F2 | `03_SW\tests -p randomly --randomly-seed=20261008` (full suite, 2040 s) | 3484 passed, 16 xfailed, 0 failed |
| V1 | `03_SW\tests\validation -p no:randomly` (trace run → `_reports/trace.json`, 1272 s) | 620 passed, 16 xfailed, 0 failed |

The working tree under test contained concurrent, uncommitted edits of other roles (ICD 0.7.5 regeneration, executor /
GUI / simulator clean-up, packaging, new unit tests — the collected test count grew from 3470 in F1 to 3500 in F2), so
every file:line anchor of this review is given at e600169. None of the 16 reproducers changed outcome across the runs.

Review experiments (scratch scripts, not part of the suite): session limits-off motion (SWR-03), wrong-direction loading
with a mismatched `pull_dir` (SWR-16: −36 N before SLIP), report build time 288 000 rows → 0.96 s (SWR-21), shutdown
race window (SWR-26: disconnect finished in 2 ms in lock-step, no MOVE_ABS).

### 7.1 Re-check against the current working tree (v1.1, 2026-10-09)

After e600169 Implementer B landed tasks 4/7 (executor, plan / path, gates, backend, simulator `step_stall`,
`reports.root()`, record-start link gate), D the GUI follow-ups and the packaging task `03_SW/packaging` (all
uncommitted at the time of the re-check). `test_v_review.py` was re-run on that tree:

| Run | Command | Result |
|---|---|---|
| R5 | `test_v_review.py -p no:randomly` | 3 passed, 16 xfailed |
| R6 | `test_v_review.py -p randomly --randomly-seed=424242` | 3 passed, 16 xfailed |
| R7 | `test_v_review.py -p no:randomly --runxfail --tb=line` | 16 failed, each at the same assertion as in R4; 3 passed |
| V2 | `03_SW\tests\validation -p no:randomly` (trace run → `_reports/trace.json`, 852 s) | 620 passed, 16 xfailed, 0 failed |

**No SWR is fixed.** The changed files were also checked for the analysis-only findings: the code paths of SWR-14,
15, 17 … 28 are unchanged (`core/link.py`, `liveness.py`, `pipeline.py`, `device.py`, `recorder.py`, `safety.py`,
`session.py`, `report.py`, `testing.py` not modified; `backend.py` / `gates.py` changes are clear-hint texts,
`reports.root()` and the record-start link item; the executor still clears VALID only for LINK_LOST / ERROR and keeps
the run log unbounded; `calibration/load.py` changed but the SWR-22 `except (OSError, FileFormatError)` is unchanged; `__main__.py` still `logging.basicConfig` — see the SWR-14 note on the packaging hook).

**NFR-002 test failing under load (reported by B).** `test_v_link.py::test_tc_nfr_002_02_backend_stop_latency_rt`
calls `Backend.stop()` 100 times on the real clock against the in-process simulator (zero link latency) and asserts
`r.sent` (line 612) before the p95 ≤ 50 ms check (line 616). A failure at `assert r.sent` is exactly SWR-10: the ACK
is matched by the Reader thread before `send_priority` looks the request up, so a written STOP is returned as "not
written" — more likely under CPU load (GIL hand-over between `submit()` and the lookup). A failure at the p95 line
instead points to SWR-15 (STOP waiting behind channel-lock holders) or plain scheduling load. The fix of SWR-10 should
make the first variant impossible; the test needs no change.

### 7.2 Fix round — Implementer B (backend share, 2026-10-09; recorded by B at the Orchestrator's request)

Fixed with the strict-xfail marker removed in the same change: SWR-01, 02, 03 (D-53 a), 04, 05, 06 (×2), 07, 08,
09, 10, 12 (×2), 13, 16 (D-53 b). Fixed without a reproducer of F (B's tests `tests/unit/core/test_swr_fixes.py`):
SWR-15, 19, 20, 21, 22, 25, 26, 28 and SWR-17 (first part), SWR-18. Packaging share (SWR-11, 14, 23, 24, 27): the
packaging agent. Design record: SW_design v0.6.6 §22c, §15.5f B6-33.

Left open / deviations, with the reason:

| ID | What is left | Reason |
|---|---|---|
| SWR-17 (part 2) | The Device stop-confirmation inputs (`last_flags`, `last_status`, `last_flags_ns`, `last_flags_dev`, `_ind_status_sent_ns`) are still separate fields written by the Reader and the Worker | Replacing them by one immutable record touches the D-37 d confirmation predicate and every indication writer (Reader, poll, `get_status_job`); a torn read needs a GIL switch inside a few attribute stores and errs on the safe side (an unconfirmed STOP is repeated). Backlog (S4) |
| SWR-01 (detail) | The STATUS poll and the jog refresh are not gated by the liveness gate; the Reader 100 ms and runner 300 ms beats of SW_design §4.6 are not evaluated | The fault reaction itself stops motion (STOP + terminate) as soon as DATA waits unevaluated > 200 ms, so the FW link watchdog is not needed as the second path; a silent Reader is already the DATA-loss STOP (500 ms), and the runner has no beat source (its waits are device-time polls bounded by the TIMEOUT guard) |
| SWR-19 (detail) | No operator acknowledgement dialog for a board UID change | GUI (D); the backend resets the test zero, logs an event and publishes `device.board_changed` |
| SWR-28 (detail) | The run log is bounded (newest 20 000 windows / results, 5 000 events, `sequence_runs[].truncated`) instead of spilled to a JSON-lines file | The offline report reads `sequence_runs` (report.py, packaging share); a spill file needs a report change. The bound keeps the process and the sidecar small; data.csv keeps every row and E row |

## 8. Fix verification (v1.2, Validator F, 2026-10-09)

Independent verification of B's fix round (§7.2, SW_design v0.6.6) and of the packaging share (SWR-11, 14, 23, 24,
27; `core/logfile.py`, `__main__.py`, `core/report.py`, `recorder.csv_cell`, `requirements*.lock.txt` +
`packaging/lock_requirements.py`, run-time hook, smoke S9; tests in `tests/unit/test_review_hardening.py`). Working
tree after those changes (uncommitted); because line numbers move with every edit, §8 names functions instead of
lines. Method per finding: (1) the original reproducer of `test_v_review.py` passes with its strict-xfail marker
removed — the owners removed only the markers (checked: every assertion of v1.1 unchanged); (2) the diff is read for
soundness (new races, ordering, error paths); (3) where a fix had only the implementer's test, F added an independent
test (second block of `test_v_review.py`, §8.6).

### 8.1 Verdict per finding

| ID | Verdict | Evidence / remarks |
|---|---|---|
| SWR-01 | **Closed** (residual detail accepted, §8.3) | `test_swr01` passes. `liveness.on_fault` → `Backend._on_liveness_fault`: while moving (`fw_moving` or `busy` or jog) priority STOP + `terminate_all("LIVENESS")` + event, idle → warning; Supervisor `_check_liveness`: oldest queued frame > 200 ms, GUI silent > 2 s while moving (real clock); queue overflow, excepthook and SWR-02 dispatch errors end in the same reaction. False-positive margin measured (§8.2) |
| SWR-02 | **Closed** | `test_swr02` passes. Per-frame `try` in `Pipeline.step`, each EVENT reaction / sink / publish guarded, `Backend._on_fw_event` split into three guarded steps; every failure counted (`dispatch_errors`) and raised as a liveness fault (→ STOP while moving: fail-safe, may be a nuisance stop on a defect — acceptable) |
| SWR-03 | **Closed** (residual SWR-29, S2, found here and closed 2026-10-10, §8.7) | `test_swr03` passes. D-53 a implemented: motion gates and `sequence_start` refuse LOAD_INPUT_INVALID whenever the input is invalid outside the no-specimen mode, whatever the enables; `LimitsAPI.check` refuses both off outside the mode; `session.restore_load_limits` turns a both-off file on with a warning (default and explicit load). Gap: leaving the no-specimen mode keeps a both-off configuration made inside it (§8.5) |
| SWR-04 | **Closed** | `test_swr04` passes. `_finish` clears VALID whenever `run.valid_on` (any end reason; LINK_LOST / ERROR keep the wait-for-link path; deadline 0 while closing). An extra SET_VALID 0 after a FW auto-clear is harmless |
| SWR-05 | **Closed** on the final recorder (SWD-P3-01, §8.7); SWR-33 closed with it | `test_swr05` passes. `start()` clears queue / gap / flags under the lock; `stop()` drains, writes the sidecar, sets the end instant (`_closing`), drains again, rewrites the sidecar when counts changed; rows after the end instant are counted `rows_after_end`. Remaining window: `_enqueue` tests `_closing` outside the lock |
| SWR-06 | **Closed** | both `test_swr06` pass. Strict typed parse (`session._typed`), travel rules at load, `load_default` treats any exception as a broken file |
| SWR-07 | **Closed** | `test_swr07` passes; `meta.json` carries `sequence_runs` and `closed_during_sequence`. Order in `Backend.shutdown`: `seq.closing`, `terminate_all("shutdown")`, STOP if moving / busy, `_wait_sequence_end(3 s)` (run log + VALID clear), recorder stop, disconnect — sound (§8.4) |
| SWR-08 | **Closed** | `test_swr08` passes. `g_config_write` REFUSEs SEQUENCE_RUNNING (active or paused) / OPERATION_RUNNING; `ConfigAPI._refused` enforces it for write / save / load / defaults / reboot (failed future `GateRefused`) |
| SWR-09 | **Closed** | `test_swr09` passes. `GlobalHaltHotkey.responding` (oldest unanswered Supervisor ping > 750 ms) feeds `available` / `mode`; `hotkey_status` explains it, the Supervisor publishes the edge on `hotkey.state` |
| SWR-10 | **Closed** | `test_swr10` passes. The write time travels with the future (`t_sent_ns`, set in `_write`); `send_priority` no longer searches `_inflight` |
| SWR-11 | **Closed** (packaging) | `test_swr11` passes. Typed `result_from_dict` (identity ints → `FileFormatError`, counters → 0, text → str), every interpolation escaped, `_idx1`, script-free CSP meta |
| SWR-12 | **Closed** | both `test_swr12` pass. `schema.parse_json_text` (size cap 20 MB, `RecursionError` / `MemoryError` → `FileFormatError`) used by `read_json` and `seqfile.loads`; start-up calibration load catches any exception |
| SWR-13 | **Closed** | `test_swr13` passes. `check()` refuses non-finite target / speed / accel; `move_to` / `move_by` never raise |
| SWR-14 | **Closed** (packaging) — residual SWR-31 (S4, closed 2026-10-10, §8.7), incident-log gaps SWR-36 (S4) | Rotating `<data>\logs\bend_stand.log` (5 MiB × 11) through a queue listener for GUI and headless, thread / sys excepthooks, incident logger on the bus. F's `test_swr14_incident_log_records_liveness_stop_halt_and_latches` passes. Coverage and start-up behaviour: §8.4 |
| SWR-15 | **Closed — sound** (residual SWR-32, S4) | F's two independent tests pass (§8.6). Analysis §8.4 |
| SWR-16 | **Closed** | `test_swr16` passes: `check_start` REFUSE `PULL_DIR_MISMATCH` (D-53 b, session = truth) |
| SWR-17 | Part 1 **closed** (warnings are a `frozenset` swapped atomically); part 2 **open — accepted for the HW gate** (§8.3) | code inspection + B's test |
| SWR-18 | **Closed** | code inspection: `clear_async.on_status` → NOT_CONFIRMED, `_on_verify_status` → NOT_EXECUTED on an undecodable STATUS; B's test |
| SWR-19 | **Partly closed** — backend done, operator acknowledgement pending (D) | `_on_synced`: UID change → test zero reset, `BOARD_CHANGED` event row, log, topic `device.board_changed`. Not a HW-gate condition |
| SWR-20 | **Closed** | `_step_with_stalls` runs `Backend._tick` |
| SWR-21 | **Closed** | `report_worker` (own thread on the real clock); lock-step keeps the general Worker for determinism |
| SWR-22 | **Closed** | F's system test `test_swr22_out_of_range_board_value_refuses_motion` passes (board reports steps/mm 50 → MOVE and JOG refuse PARAM_INVALID); `record_start_with` catches `ValueError` / `TypeError` |
| SWR-23 | **Closed** (packaging) | F's `test_swr23_samples_csv_neutralises_formula_marks` passes (`'` prefix for `= + - @ TAB CR`) |
| SWR-24 | **Closed** per the Orchestrator's decision | F's `test_swr24_failed_calibration_cannot_be_applied_offline` passes (FAIL fit and K = 0 refused with and without a schema header); a header-less snapshot record is accepted after the full `validate_load` + number / type checks; non-PASS status → report warning |
| SWR-25 | **Closed** | F's `test_swr25_broken_session_file_is_never_overwritten` passes (`default.bbsession.json.bad` keeps the operator's content; the next auto-save writes a new file) |
| SWR-26 | **Closed** (residual SWR-35, S4) | ordering verified with SWR-07 |
| SWR-27 | **Closed** (packaging) — residual SWR-34 (S4) | `requirements.lock.txt` (6 packages, one SHA-256 each, Windows AMD64 / CPython 3.14), dev and build locks; `build_dist.ps1 -InstallDeps` installs with `--require-hashes`. Versions compared with the verified `.venv`: runtime lock identical; dev lock pins `iniconfig 2.3.1`, `.venv` has 2.3.0 |
| SWR-28 | **Closed (bounded)** — residual SWR-30 (S4) | newest 20 000 windows / results, 5 000 events, `sequence_runs[].truncated`; `data.csv` keeps every E row. The report does not tell that windows were dropped |

### 8.2 SWR-01 — false-positive risk of the thresholds

The pipeline condition measures the age of the **oldest received, not yet evaluated frame** (not a thread beat), so an
idle pipeline never trips it. Measurement on the DEV PC (real clock, in-process simulator, no-specimen mode, moves 10 ↔
100 mm at 30 mm/s for 60 s, `gui_beat` every 50 ms, sampling `oldest_unprocessed_age_ns` every 10 ms):

| Load | Samples | Max oldest-unprocessed age | Liveness faults |
|---|---|---|---|
| idle CPU | 5 251 | 0.5 ms | 0 |
| 16 busy processes on 16 cores (100 % CPU) | 4 383 | 11.3 ms | 0 |

Margin to 200 ms ≥ 17×. The longest GUI-thread pause of the GC policy (thaw, 29–58 ms, SW_design §13.10) does not
block the pipeline thread for that long (GIL switch interval 1 ms). The one host stall on record (MC3-6, pipeline beat
gap 1.27 s during a twin run under full-suite load) happened with no data arriving and would not have tripped; had DATA
arrived, the reaction is a STOP — fail-safe, a nuisance at worst. The GUI condition (2 s, only while moving, real
clock) is far above the refresh period (33 ms) and modal dialogs keep the refresh timer running.
**Condition (REF PC):** the NFR-004 soak and the PR runs with motion must show 0 `LIVENESS` events; the incident log
now records each one with the measured age.

### 8.3 Open items left by B — acceptable for the HW gate?

* **SWR-01 detail** (STATUS poll and jog refresh not gated by the liveness gate; Reader 100 ms / runner 300 ms beats
  not evaluated). **Acceptable.** Each failure mode still ends in a stop: a dead or hung **Pipeline** → the new
  reaction (STOP ≤ 200 ms + tick); a dead **Reader** → no DATA → DATA-loss STOP after 500 ms (written by the Supervisor,
  independent of the Reader); a hung **Supervisor** → no frame at all (heartbeat, poll and jog refresh all live there)
  → FW link watchdog 1 s and jog dead-man; a hung **sequencer runner** → it sends nothing, the running single FW command
  ends on its own target / raw stop / bound, the guards (Pipeline thread) still stop on SLIP / BREAK. What is lost is
  diagnosis speed, not the stop. Recommendation (backlog): evaluate the Reader beat for the incident log.
* **SWR-17 part 2** (stop-confirmation inputs written by two threads). **Acceptable.** A torn read can only combine
  indications produced by the FW in production order (responses and DATA are serialised on the wire, `_apply_status`
  ignores a STATUS older than the newest DATA); the predicate also requires the device-time order. The realistic
  outcome of a torn read is one confirmation tick late (STOP repeated once more), never a confirmation of a stop on an
  axis that is still moving. Backlog S4 as B proposes.
* **SWR-19 dialog** (D) and **SWR-28 spill file**: not HW-gate relevant.

### 8.4 Focus analyses

**SWR-15 (frames written outside the lock).** `_send_locked` now only *reserves* (payload, SEQ, in-flight entry,
provisional deadline) under `_lock`; lane frames go to `_pending_tx` and are written in reservation order by one owner
thread (`_flush_tx`, `_tx_owner`); a priority frame is written by its submitter right after the lock is released and
overtakes queued lane frames through the `FrameWriter` priority lock. Motion frames carry an epoch; `_write` checks
the epoch and writes **under `_epoch_lock`**, and `bump_epoch` takes `_epoch_lock` before `_lock` (same order
everywhere — no lock-order inversion found). Hence: a motion frame reserved in an old epoch is either already on the
wire before the epoch bump returns, or it is dropped (`CommandDropped`) — and every operator / safety stop path
(`Device._priority_stop`) bumps the epoch **before** writing the STOP. Verified by F's tests: (a) a MOVE_ABS reserved
behind a blocked lane frame is never written after the STOP; (b) a MOVE_ABS already being written goes out first, the
STOP follows at once (the bump waits for exactly that frame). Remarks: (1) timeouts count from the reservation, so a
frame that waits behind a slow port can time out before it is written; for VERIFY commands the resolving GET_STATUS is
reserved behind it, so the reservation order keeps the verdict correct — acceptable; (2) **SWR-32**: `_write` keeps the
non-reentrant `_epoch_lock` while it runs future callbacks (`_fail` with `CommandDropped` / `TransportError`) and
`on_tx_error`; no current callback issues a stop, but one that did would deadlock the writer and every later STOP.

**SWR-07 / SWR-26 (shutdown).** Order is now operations → STOP → wait for the run's `_finish` (bounded 3 s; lock-step
steps the clock) → recorder → disconnect → threads. The run log and `closed_during_sequence` reach `meta.json`, no
recording tail / report build at shutdown (rebuild offline), VALID cleared while the link is up. Residual **SWR-35**:
the STOP condition is `MOVING or motion.busy` — a jog session whose first DATA has not arrived is not included
(`_moving_now()` would be); the FW jog dead-man bounds it.

**SWR-03 / D-53 a.** Gates: correct and complete for MOVE / JOG / HOME / LOAD_APPROACH and `sequence_start`
(LOAD_INPUT_INVALID independent of the enables). Session files: both-off restored on, with a warning — verified by
`test_swr03` and B's test. `LimitsAPI.check` refuses both off outside the mode. Residual **SWR-29** (S2): inside the
no-specimen mode both limits may be switched off (allowed), and **leaving the mode keeps them off** — operator exit,
link loss and disconnect all go through `set_no_specimen(False)` without touching the limits. With a valid calibration +
tare the axis then moves with no PC load limit, no mode, no banner — exactly the state D-53 a forbids; the auto-saved
session also carries both-off (restored only at the next start). Reproduced by `test_swr29` (strict xfail).

**SWR-14 (log file) — incident coverage and start-up.** Coverage of the stop / latch / link-loss paths:

| Path | Logged as | Level |
|---|---|---|
| link DEGRADED / LOST, transport error | `link.state` + `device` logger | WARNING |
| GUI STOP, Pause/Break HALT, PAUSE, sequence STOP / abort, SW-limit trip STOP, LOAD_INPUT_INVALID STOP, DATA-loss STOP (source LINK_LOST), liveness STOP | `stop.issued` (source, written / NOT WRITTEN) | INFO / ERROR |
| STOP / HALT / PAUSE not confirmed in 1 s | `stop.unconfirmed` | ERROR |
| FW latches ESTOP / HALT / LIMIT / FAULT / LINK_WDG / HOME_FAILED / ALM / DRIVER_POWER / AFE / NVM / CLK | `fw.event` | WARNING |
| FW STOPPED, PAUSED, clears, BOOT, MOVE_DONE | `fw.event` | INFO |
| PC limit trip / clear | `safety.trip`, `safety.trip_cleared` | WARNING / INFO |
| liveness fault, sequence guard trips, no-specimen mode on/off, LINK LOST diagnostics | `EventBus.log` → logger | ERROR / WARNING |
| recording failure; sequence ABORTED / ERROR | `rec.failure`; `seq.status` state edge | ERROR; WARNING |
| uncaught thread / main exceptions | excepthooks | ERROR / CRITICAL |

Gaps (**SWR-36**, S4): FW-threshold verification FAILED / INVALID (`safety.thresholds`, published but never logged)
and lost FW EVENTs (EVENT SEQ gap → `events_lost` counter, no log line) — both matter after an incident. Start-up: an
`OSError` of the log folder (missing / read-only profile, path is a file, file locked by a second instance) never blocks
the start — console-only logging, verified with `<data>` being a file; rollover clashes between two instances are
reported by `logging.handleError`, not raised. One residual (**SWR-31**, S4): `setup_logging` catches only `OSError`;
`paths.app_data_dir` raises `RuntimeError` when neither `%APPDATA%` nor a home directory resolve, and `main()` calls
it before its `try` — reproduced by `test_swr31`; fixed on 2026-10-10 (§8.7). The `Backend` uses the same data root and would fail
too, so the practical impact is small; the fix is one `except Exception`.

### 8.5 New findings

| ID | Sev | Title | Where | Reproducer | Owner |
|---|---|---|---|---|---|
| SWR-29 | **S2** | Leaving the no-specimen mode keeps both PC load limits off (D-53 a bypass; motion with a valid input and no PC load limit, no banner; auto-saved). **Closed 2026-10-10** (§8.7) | `LimitsAPI.set_no_specimen_mode(False)`, `Backend.set_no_specimen` (also link-down / disconnect exit) | `test_swr29` | B |
| SWR-30 | S4 | Report silent about a truncated run log (`sequence_runs[].truncated`). **Closed 2026-10-10** (§8.7) | `core/report.py` `_warnings` | `test_swr30` | B (report owner) |
| SWR-31 | S4 | `setup_logging` lets a non-`OSError` of the data root escape; `main()` calls it outside its `try`. **Closed 2026-10-10** (`except Exception` in the file part; `main()` guards the set-up; `test_swr31` XPASSed in run V3, marker removed by the owner) | `core/logfile.py` `setup_logging`, `__main__.main` | `test_swr31` | B (packaging) |
| SWR-32 | S4 | `CommandChannel._write` holds the non-reentrant `_epoch_lock` while running future callbacks / `on_tx_error`; a stop issued from such a callback would deadlock the writer and every later STOP | `core/link.py` `_write` | analysis | B |
| SWR-33 | S4 | `Recorder._enqueue` tests `_closing` outside the lock; a row checked just before the end instant can land after the final drain — not written, not counted (no leak: `start()` clears). **Closed by SWD-P3-01 (§8.7)** | `core/recorder.py` `_enqueue` | analysis | B |
| SWR-34 | S4 | `requirements-dev.lock.txt` pins `iniconfig 2.3.1`, the verified `.venv` has 2.3.0 — the dev lock is not the verified environment | `03_SW/requirements-dev.lock.txt` | `pip freeze` vs lock | B (packaging) |
| SWR-35 | S4 | Shutdown STOP condition omits a just-started jog (`_moving_now()` exists) | `Backend.shutdown` | analysis | B |
| SWR-36 | S4 | Incident log misses FW-threshold FAILED / INVALID and lost FW EVENTs | `core/logfile.IncidentLogger.TOPICS`, `core/pipeline.py` `_event` | analysis | B |
| SWR-38 | **S3** | Travel wizard Cancel does not stop its own running motion; under the D-54 a scope it is stopped by a SAF-SW-001 LOAD_INPUT_INVALID trip, in the session mode it runs on (§8.9) | `TravelCalEngine._abort` | `test_d54a_09` (strict xfail ×2) | B |
| SWR-37 | **S3** | Pipeline-liveness filter of OBS-P3-05: a genuine Pipeline stall is never reported while Supervisor gaps > 100 ms repeat with < 30 ms of operation in between (§8.7). **Closed (§8.8)** | `Backend._check_liveness` | `test_swr37` | B |

Fix hints: SWR-29 — on leaving the mode (any cause) restore `pull_enabled = push_enabled = True` when both are off
(event row + warning), or refuse the operator exit while both are off and restore on link-down exits. SWR-30 — warning
"run log truncated: N windows / results not in the report (see data.csv)". SWR-31 — `except Exception` around the file
part and around `setup_logging` in `main()`. SWR-32 — release `_epoch_lock` before `_fail` / `on_tx_error` (keep only
the check + write under it). SWR-33 — read `_closing` inside `with self._cv`. SWR-34 — regenerate the dev lock from the
verified `.venv` or upgrade the venv and re-verify. SWR-35 — use `_moving_now()`. SWR-36 — add `safety.thresholds`
(FAILED / INVALID at ERROR) and log an EVENT-SEQ gap at WARNING.

**HW-gate relevance:** SWR-29 belonged to the D-53 c class of SWR-03 (closed 2026-10-10); SWR-37 (§8.7)
is recommended in the same batch (one-line ceiling). All others are backlog.

### 8.6 Tests and runs

New in `test_v_review.py` (v1.2): `test_swr15_reserved_move_of_an_old_epoch_never_follows_a_stop`,
`test_swr15_stop_waits_at_most_for_the_motion_frame_being_written`, `test_swr22_out_of_range_board_value_refuses_motion`,
`test_swr23_samples_csv_neutralises_formula_marks`, `test_swr24_failed_calibration_cannot_be_applied_offline`,
`test_swr25_broken_session_file_is_never_overwritten`, `test_swr14_incident_log_records_liveness_stop_halt_and_latches`
(pass) and the reproducers `test_swr29_…`, `test_swr30_…`, `test_swr31_…` (strict xfail; `--runxfail`: each fails at its
own assertion).

| Run | Command | Result |
|---|---|---|
| R8 | `test_v_review.py -p no:randomly` | 26 passed, 3 xfailed |
| R9 | `test_v_review.py -p randomly --randomly-seed=777` | 26 passed, 3 xfailed |
| R10 | `test_v_review.py -p randomly --randomly-seed=31337` | 26 passed, 3 xfailed |
| R11 | `test_v_review.py -p no:randomly --runxfail` | 3 failed (SWR-29 / 30 / 31, at their assertions), 26 passed |
| A | `03_SW\tests -p no:randomly` — unit, validation, integration (FW twin, private `BEND_TWIN_BUILD_DIR` / `VAL_TWIN_BUILD_DIR` in F's scratchpad), GUI (1571 s) | 3613 passed, 3 xfailed, **1 failed**: `unit/test_layering.py::test_io_imports_from_core_only_leaf_modules` (`io/sim/server.py` imported `core.timing`) — `server.py` was being edited by another role during the run (mtime 19:28, inside the run); the test passes on the tree afterwards (11 / 11) |
| B | `03_SW\tests -p randomly --randomly-seed=20261009`, same set-up (1478 s) | **3620 passed, 3 xfailed, 0 failed** |

The 3 xfailed of A / B are SWR-29 / 30 / 31. No hardware, no COM port, twin binaries only in F's private build dir.

### 8.7 Add-on: SWD-P3-01, OBS-P3-01, OBS-P3-04 and the liveness filter (B, 2026-10-10)

**SWR-05 on the final recorder — closed.** `_enqueue` takes the decision (end instant, state, stale row) and the
append in one `_cv` hold; `start()` / `stop()` change state under the same lock; a row received before the previous
recording's end instant but delivered after a new start is rejected and counted (`rows_stale`). This also closes
SWR-33. Evidence: `test_swr05` (deterministic interleaving) passes, B's `test_rec_boundary.py` (2) passes, and F's new
independent real-clock test `test_swr05_threaded_stop_start_cycles_keep_every_row_in_one_recording` (producer thread
without pause, 25 stop / start cycles; oracle: no row in two recordings, strictly increasing rows, no row received
before the previous `stop()` returned in a later recording, `rows_written` = D rows) passes 3 / 3. Remark: a
`rows_stale` row belongs to the previous, already closed recording, whose integrity block cannot count it any more —
acceptable (counted, never silent, needs a stop and a new start within the pipeline lag).

**Liveness filter (OBS-P3-05).** `_check_liveness` now resets the Pipeline condition on every Supervisor tick that
follows a Supervisor gap > 100 ms and fires only after 30 ms of continuous Supervisor operation with the oldest
unevaluated frame > 200 ms. A genuine Pipeline stall still stops within the budget (`test_swr01`: STOP ≤ 300 ms,
passes; B: ≈ 240 ms). Judgement:
* Skipping the tick right after a whole-process stall is reasonable — a GC pause of 320–476 ms (OBS-P3-05) ages every
  queued frame without any Pipeline fault, and 11 false faults under load are a real availability problem.
* **But the filter has no ceiling (SWR-37, S3).** The reset happens on *every* gap, so a Pipeline that is really stuck
  is never reported while the Supervisor keeps being silent > 100 ms with < 30 ms of operation in between — the
  pattern of a host that is thrashing (repeated GC / pre-emption). Reproduced: Pipeline stalled 2.5 s during a move,
  Supervisor silent 120 ms of every 140 ms → no STOP; the wire shows only GET_STATUS / PING. In that state the Reader
  still receives DATA (no DATA-loss STOP) and the Supervisor's short windows still send PING / GET_STATUS, so the **FW
  link watchdog is fed and does not stop the axis either**. What remains is the FW: load limit at the verified
  thresholds (≤ 110 % FS), limit switches, E-stop; the PC travel limits and PC load trips are not evaluated. It needs
  two faults at once (stuck Pipeline + thrashing host), so it is not likely — but nothing bounds its duration.
* **Fix (cheap, recommended before the first specimen test):** keep the filter and add an absolute ceiling that
  ignores the Supervisor gap — e.g. oldest unevaluated frame > 1000 ms → fault (≥ 2× the longest measured process
  stall, and still below the 1 s FW link watchdog it substitutes for). `test_swr37` (strict xfail) turns green with it.
* Measured margin of the unfiltered 200 ms rule on the DEV PC (§8.2) stays valid: 0.5 ms / 11.3 ms max at idle / 100 %
  CPU; the false faults came from whole-process pauses, which the ceiling does not touch below 1 s.

**io/sim/server.py `timing.init()`** (simulator only) and **pipeline pure-Python median** (OBS-P3-04, memory growth):
read, no finding; the layering test that failed in run A (`io/sim/server.py` importing `core.timing` during the edit)
passes on the final tree.

**PR-4 soak re-run (rows_before_record_start, 12.5 ms pacing): not done.** The machine was not quiet (CPU 100 %, other
roles' builds and test runs active); a soak under that load would not be evidence. To be re-run at a quiet time or on
the REF PC (run_ref_pc.ps1 `E_soak`).

**B's follow-up round (2026-10-10 03:58, markers removed by B in `test_v_review.py`; nothing else in F's files
changed — checked).** SWR-29: `Backend.set_no_specimen(False)` — every exit from the mode (operator, link down,
disconnect) switches both PC load limits on again at their configured levels, event row `LIMITS_RESTORED` + warning;
the motion gate (valid load input) then decides — **closed**, `test_swr29` passes. Remark: a single limit the operator
had switched off before entering the mode is re-enabled too (safe side; D-53 a only forbids both off) — acceptable,
the warning tells it. SWR-30: report warning "run log truncated: the oldest … were dropped" — **closed**, `test_swr30`
passes. SWR-31: `setup_logging` catches any exception of the file part, `main()` guards the set-up — **closed**,
`test_swr31` passes (XPASS in V3 before the marker was removed). **SWR-37 stays open** (`test_swr37` strict xfail).

**Owners of the new findings:** SWR-29 → **B** (`LimitsAPI` / `Backend.set_no_specimen`, D-53 a); SWR-30 → **B**
(report owner; the packaging agent changed `report.py` on B's behalf); SWR-31 → **B** (`core/logfile.py`,
`__main__.py`, written by the packaging agent for B); SWR-37 → **B**. SWR-32 … 36 → B (SWR-34 packaging).

| Run | Command | Result |
|---|---|---|
| R12 | `test_v_review.py -p no:randomly` (31 tests) | 27 passed, 4 xfailed |
| R13 | `test_v_review.py -p randomly --randomly-seed=4242` | 27 passed, 4 xfailed |
| R14 | `test_v_review.py -p no:randomly --runxfail` | 4 failed (SWR-29 / 30 / 31 / 37, each at its assertion), 27 passed |
| B-unit | `tests/unit/core/test_rec_boundary.py` | 2 passed |
| R15 | `test_v_review.py -p no:randomly` after B's 03:58 round | 30 passed, 1 xfailed (SWR-37) |
| V4 | `03_SW\tests\validation -p no:randomly`, final tree (748 s; trace → `_reports/trace.json`) | **647 passed, 1 xfailed (SWR-37), 0 failed** |
| V3 | `03_SW\tests\validation -p no:randomly` (749 s) | 644 passed, 3 xfailed, 1 failed = `test_swr31` **XPASS(strict)**: SWR-31 was fixed during the run (logfile.py / `__main__.py` 03:58); verified, marker removed |

### 8.8 Round 6 (B, SW_design v0.6.8, B6-34) and the final SW verdict for the HW gate (2026-10-10)

| ID | Verdict | Evidence |
|---|---|---|
| SWR-37 | **Closed** | `PIPELINE_CEILING_MS = 1000`: oldest unevaluated frame > 1 s while moving → fault, independent of the filter state and the Supervisor gap; `test_swr37` passes (marker removed by B). The ceiling applies only while moving (idle: the filtered 200 ms rule stays) — correct, the reaction is a STOP |
| SWR-29 | **Closed** | any exit from the no-specimen mode restores both limits (`LIMITS_RESTORED`); `test_swr29` passes |
| SWR-30 | **Closed** | report warning for `sequence_runs[].truncated`; `test_swr30` passes |
| SWR-31 | **Closed** | `test_swr31` passes |
| SWR-32 | **Closed** | `CommandChannel._write`: only the epoch check and the write under `_epoch_lock`; `_fail` and `on_tx_error` run after the release — F's SWR-15 tests still pass |
| SWR-34 | **Closed** | `iniconfig==2.3.0` in the regenerated dev lock (= `.venv`) |
| SWR-35 | **Closed** | `Backend.shutdown` STOPs on `MOVING or _moving_now()` |
| SWR-36 | **Closed** | incident log: `safety.thresholds` FAILED / INVALID at ERROR (state edges), `FW_EVENTS_LOST` (EVENT-SEQ gap) at WARNING |
| SWR-17 part 2 | open, S4 backlog — accepted (§8.3) | — |
| SWR-19 | **Closed** | `device.board_changed` in `api.TOPICS`; D's C-15 dialog done |

**Shorter test-only logs (Reader `rx_log`, in-process simulator sent / wire logs: 5 000 entries in normal operation,
full size with `test_hooks`).** No validation hook is weakened: every Validator F access goes through `test_hooks`
(`harness.rx_log` → `test_hooks.rx_log()`; `sim_sent` → `backend.sim` of a backend built by `lockstep_backend` /
`realtime_backend`, both `test_hooks=True`, so `test_logs` defaults to full size); the integration rig (`twin_rig.Rig`)
also sets `test_hooks=True`; the transport wire log (`enable_wire_log`, 200 000) is unchanged. The REF-PC perf tools
are not affected either: PR-4 (soak) counts frames from the recording and the pipeline counters against the
out-of-process simulator (`SimServer` keeps the 200 000 default), and PR-5 runs with `--wire-log` (= `test_hooks`).
The `seq.status` history of 2 000 records only bounds the EventBus history; tests read the latest status. No finding.

**Final full suite on the current tree** (unit, validation, integration with the FW twin in F's private
`BEND_TWIN_BUILD_DIR` / `VAL_TWIN_BUILD_DIR`, GUI offscreen; the `git status` of `03_SW/src` identical before and after):

| Run | Command | Result |
|---|---|---|
| F-1 | `03_SW\tests -p no:randomly -rfEsX` (1260 s) | **3630 passed, 0 failed, 0 xfailed** |
| F-2 | `03_SW\tests -p randomly --randomly-seed=20261010 -rfEsX` (1256 s) | **3630 passed, 0 failed, 0 xfailed** |
| R16 | `test_v_review.py -p no:randomly` | 31 passed |

**Final SW verdict for the HW gate: ACCEPTED WITH CONDITIONS.** All D-53 c conditions are met (SWR-01, 02, 03 + 29, 37
before the first specimen test; SWR-04, 05, 07 for recordings as evidence; SWR-14 log file); 36 of 37 SWR closed, SWR-17
part 2 is an accepted S4 backlog item. Conditions:
1. **Baseline:** the verified tree is committed as is (the Orchestrator's gate commit); any later change to
   `core/link.py`, `pipeline.py`, `safety.py`, `backend.py`, `recorder.py` or `executor.py` before the HW gate is
   re-run against `test_v_review.py` and the validation suite.
2. **REF PC (MC4-2 / MC4-3, OI-F-RV-08):** PR-1 … PR-5 incl. the PR-4 soak — 0 `LIVENESS` events, `rows_before_record_start
   = 0`, 12.5 ms simulator pacing, NFR-002 / NFR-003 latencies.
3. **On target at the HW gate:** the hardware items of the FW / SW test plans (NFR-003 last PUL edge, VCP soak, E-stop /
   limit / load-limit reactions); the incident log `<data>\logs\bend_stand.log` is collected with every HW-gate run.

### 8.9 D-54 a / c verification (B's hand-back SW_design §9.3, §15.5f B6-35; 2026-10-10)

**Implementation read.** `g_cal_travel_start` = the MOVE items without LOAD_INPUT_INVALID / NOT_HOMED / OWNER_CONFLICT +
busy items + CONFIRM `NO_SPECIMEN_MOUNTED` when the load input is invalid outside the session mode + the 62 mm room check
once homed. `Backend.set_wizard_no_specimen("TRAVEL_CAL")` sets a scope that is effective only while
`owner == TRAVEL_CAL`: in `motion_items` (LOAD_INPUT_INVALID skipped for that owner only) and in `_safety_inputs` (the
SafetySupervisor treats the PC load limits as off only for that owner). FW thresholds are not touched (the provider
target is unchanged → DEFAULT_ONLY without calibration). The scope ends in `_accepted` and `_abort` (cancel, every
`terminate_all` cause, link loss, homing failure), always together with `owner = MANUAL`, and runs
`_restore_load_limits` (SWR-29 rule). HOME is the wizard's first phase (`motion.home(load_confirmed=True,
owner=TRAVEL_CAL)`), a failure aborts the wizard. The session no-specimen mode is never changed by the wizard.

**Tests** (`test_v_d54a.py`, armed — pending markers removed): F's pre-written 01–04 (unconfirmed start writes
nothing; confirmed start homes first, banner on, FW thresholds DEFAULT_ONLY, MANUAL move / jog and sequence start
refused, nothing stored in the session; every exit — Cancel, STOP, HALT, link loss — restores both PC load limits; a
session mode entered before survives) and the additions 05–09 + D-54 c:

| Test | Case | Result |
|---|---|---|
| 05 | MANUAL jog / move / home during the wizard → refused (OWNER_CONFLICT), no JOG / MOVE_ABS of them on the wire; the wizard's own homing completes and it reaches BACKLASH (its moves run without PC load limits) | pass |
| 06 | session no-specimen mode entered during the wizard → refused (operation running); a session mode entered before and left during the wizard → limits on at once, no leak after the wizard ends | pass |
| 07 | FW E-stop (red button) during the wizard's homing → wizard aborted, scope ended, limits on, owner MANUAL, no wizard motion frame after the E-stop | pass |
| 08 | homing failure (START switch never closes → FW HOME_FAILED NOT_FOUND) → wizard ABORTED, no calibration move, scope ended, limits on | pass |
| 09 | **Cancel while the wizard's homing runs** (wizard scope / session mode) → the wizard should stop its own motion | **strict xfail — SWR-38** |
| D-54 c | `reports.set_root`: a non-creatable folder (file in the way) refused (`FOLDER_NOT_WRITABLE`), nothing changed; a writable folder created, stored in the session, the write probe removed, the next recording written there; refused while recording (`RECORDING_ACTIVE`) | pass |

Runs: `test_v_d54a.py` fixed order and `--randomly-seed=99`: 12 passed, 2 xfailed (SWR-38 × 2) each.
Full validation run, all groups armed (`03_SW\tests\validation --arm all -p no:randomly`): first run 659 passed,
2 xfailed (SWR-38), 1 failed — `test_v_plots.py::test_tc_sw_rt_006_11_xy_pane_type`, a **test** defect: F's fake
`_Data.xy(x, y, window_s)` did not follow the published `DataView.xy` signature (`core.api`: `max_points` since NFR-009,
`since=`), so `refresh_xy` caught the `TypeError` (D's diagnosis, agreed). Fake updated to the API signature (+ check
that the pane passes a point budget 1 … 4000 and `since="window"`). Final run on the current tree: **660 passed,
2 xfailed (SWR-38), 0 failed** (884 s; `_reports/trace.json` regenerated).

**SWR-38 (S3, new, owner B) — the wizard's Cancel does not stop its own running motion.** `TravelCalEngine._abort`
ends the scope and returns the owner to MANUAL but sends no STOP for the move / homing in progress (before D-54 a the
restore job only waited for standstill). Measured: under the wizard scope the homing is then stopped one frame later by
the SafetySupervisor (D-53 a: motion with an invalid load input) — STOP mode 0 plus an `SW_TRIP LOAD_INPUT_INVALID`
event "load limit without valid input": the right result through the safety net, with a misleading SW-trip report; with
the session no-specimen mode on, the cancelled homing simply runs on under MANUAL ownership. Fix: `_abort` sends a
controlled STOP when the wizard's own command is running (not needed when the abort was caused by a stop / latch), before
the owner returns to MANUAL. Not a HW-gate blocker for the safety function (the axis is stopped in both scope cases, the
session-mode case equals the pre-D-54 behaviour), but it should be fixed before the travel calibration is used at the
gate, because operators will cancel during homing.

**Verdict:** D-54 a **verified — closed, with SWR-38 open (S3)**; the wizard scope cannot leak (other owners, session
mode, stops, latches, link loss, homing failure, cancel all end it with the PC load limits on). D-54 c **verified —
closed**. Not covered by F: the GUI side of the `NO_SPECIMEN_MOUNTED` confirmation (Enter / Space never confirm,
SAF-SW-004) — D's GUI tests.

## 9. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.5 | 2026-10-10 | Validator F | §8.9: D-54 a / c verified (D54A armed, F's cases 05–09 + D-54 c); new SWR-38 (S3) |
| 1.4 | 2026-10-10 | Validator F | §8.8: round 6 verified (SWR-19, 29–32, 34–37 closed; SWR-17 part 2 accepted backlog); shorter test-only logs do not weaken any validation hook; final full suite 2 × 3630 passed (fixed + random); final SW verdict for the HW gate ACCEPTED WITH CONDITIONS |
| 1.3 | 2026-10-10 | Validator F | §8.7: SWR-05 closed on the final recorder (SWD-P3-01, + SWR-33), independent threaded stop / start test; liveness filter judged — new SWR-37 (S3, no ceiling) with reproducer; owners of SWR-29 … 37; SWR-31 closed (XPASS); PR-4 soak not re-run (machine busy) |
| 1.2 | 2026-10-09 | Validator F | §8 fix verification of B's round (§7.2) and the packaging share: per-SWR verdicts (26 closed, SWR-17 part 2 open — accepted, SWR-19 dialog pending), SWR-01 false-positive measurement, focus analyses SWR-15 / 07 / 26 / 03 / 14; new findings SWR-29 (S2) … SWR-36 (S4); independent tests and reproducers added to `test_v_review.py` |
| 1.1 | 2026-10-09 | Validator F | §7.1 re-check on the working tree after B tasks 4/7, D GUI follow-ups, packaging: no SWR fixed (R5–R7); NFR-002 test failure mapped to SWR-10 / SWR-15; SWR-14 packaging note |
| 1.0 | 2026-10-08 | Validator F | First whole-codebase review at e600169: SWR-01 … SWR-28, reproducers in `test_v_review.py` |
