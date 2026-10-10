# Bird Bend Stand — SW design: GUI (`bend_stand.gui`)

| Doc | SW_design_GUI |
|---|---|
| Version | **0.6.1 — operator documentation (Task 2b): `USER_MANUAL.md`, `QUICK_REFERENCE.md`, screenshots `docs/img/` from `tests/gui/manual_screenshots.py`, docs-drift test `tests/gui/test_user_manual.py`; display fixes found on the screenshots (tab "&", good-state chip texts, no "nan" in step results)** · 0.6 — M4 (Sequencer) GUI as built (§15.4, §15.5): MC3-5 trip toasts, Sequence tab (editor, loops, files, generator wizard, run controls, run line), sequence chart, Report tab; M4 backend alignment (§11.1f, B6-01…17)** · 0.5 — M3 (SW application) GUI as built (§15.2, §15.3): Manual, Safety limits, Test marks, Calibration & Tare tabs, travel / load wizards, TARE popup, Pause/Break key test, units N / kgf, K1 chip (M2 gate condition); M3 backend alignment (§11.1e, B5-01…17)** · 0.4.1 — M1 as built (§15.1) + D-38 plot panes (SW-RT-006, §4.7) + M2 backend alignment (§11.1d, D-41) |
| Date | 2026-10-08 |
| Owner | Implementer D — GUI (`03_SW/src/bend_stand/gui/**` incl. `gui/__init__.py`, `gui/app.py`; `03_SW/tests/gui/**`; this document) |
| Binding inputs | **M4: SRS v0.6.2 (SW-SEQ-001…007, SW-WIZ-001/002, SW-SEQF-001, SW-SCH-001/002, SW-REP-001…004, SW-STOP-004), ICD v0.7.3 (dict 6), DECISIONS D-01…D-46, `SW_design.md` v0.6 §15.5f (B6-01…17); M3 gate condition MC3-5 (SWD-M3-02).** M3: SRS v0.6.1, ICD v0.7.1 (dict 5), DECISIONS D-01…D-44, `SW_design.md` v0.5 §15.5e (B5-01…17).** Earlier rounds: `00_System/specs/SRS.md` **v0.4** (SW-*, SAF-SW-*, NFR-*; same IDs as v0.3, D-30…D-33 wording incl. SW-STOP-004 RESUME and the SW-SEQ-005 refusal list), `DECISIONS.md` D-01…D-33 (esp. D-11, D-14, D-23, D-26, D-28, D-29 a/h/i/n, **D-30, D-31, D-32, D-33**; **D-27 closed** per Orchestrator 2026-10-03: driver 4000 p/rev closed loop → nominal 800 steps/mm), `ICD_protocol.md` **v0.4** (RESUME 0x3C, D-31) and the generated name tables `03_SW/src/bend_stand/core/protocol_gen.py` (ICD v0.4, PROTO 1.0, PAYLOAD 1), `params.yaml` (`motion.steps_per_mm` default 800, D-27 closed), R3 §6 (Thrust_Stand GUI solutions, perf defect SWD-PM3-05), R1 §7 (Stefan `stepper_gui` patterns), R4 §5–§8, §10 |
| Backend contract | **`03_SW/docs/SW_design.md` v0.3 §15** (Implementer B): facade `core.backend.Backend` (§15.1), `BackendStatus` (§15.2), topics (§15.3), GUI rules (§15.4), **API delta A-01…A-25 (§15.5) and B3-01…B3-21 (§15.5a)**, result types (§15.6), answers to GRQ-B-01…18 (§15.7) and GF-11…17 (B3-16…B3-21); plus B §5.4–§5.6, §5.5.1, §6.5, §6.7, §9.1, §9.3.1, §10. Where the two documents differ, B §15 wins and this file is updated. |
| Generated names | Every bit, fault, flag, source, event, stop-cause and phase name shown or iterated by the GUI comes from `core.protocol_gen` (`DATA_FLAGS_BITS`, `DATA_STATUS_BITS`, `FAULTS_BITS`, `SYS_FLAGS_BITS`, `BLOCK_BITS`, `SOURCE_NAMES`, `EVENT_NAMES`, `STOP_CAUSE_NAMES`, `MOVE_DONE_REASON_NAMES`, `HOME_PHASE_NAMES`, `MOTION_STATE_NAMES`, `DRIVER_DISABLED_CAUSE_NAMES` and their `*_DESC`); nothing is hand-listed (P8, GF-08). |
| Reference code (read-only, D-02) | `Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/**` and `io/win_hotkey.py`, cited as `TS:path:line` at **9473c68**; copies are taken from the TS HEAD current at copy time and the copied hash is written into the origin note (D-29 m, SYS-010). `Stefan/SW/stepper_gui/gui.py` (cited via R1 §7) |

**Notation**
- `B §n` = section n of `SW_design.md` v0.3; `A-nn` / `B3-nn` = B's API delta items (B §15.5 / §15.5a).
- `GRQ-B-nn` = request to Implementer B (§11.3); `GQ-nn` = GUI preference, **decided by the PO (D-32, Q27)** (§13); `GF-nn` = finding for another role (§14); `C-nn` = confirmation dialog (§5.5); `WP-Dn` = M1 work package (§15).
- Bit/fault/event names in `CAPS` are the generated `protocol_gen` names; `Indicators` items are written `indicators.<item>`.
- Wireframes are ASCII: `[Button]` = push button, `[x]` = checkbox, `( )` = radio, `[ 12.3 ]` = numeric field, `[v]` = combo box, `●` = indicator LED.

---

## 0. What changed in v0.2 and v0.3 (summary)

| Topic | v0.2 design | Section |
|---|---|---|
| Backend API v0.2 | every item A-01…A-25 adopted; GRQ-B-01…18 closed; one new small request GRQ-B-19 (M3, non-blocking) | §11 |
| Pause / Resume | GUI Pause = FW PAUSE 0x3B, returns `StopResult`; PAUSED is a motion-blocking latch (D-30); **Resume = RESUME 0x3C (D-31)**, clears only PAUSED, refused while HALT/ESTOP/fault is latched → GUI shows "Clear stop first"; Clear stop (HALT_CLEAR) clears HALT + PAUSED; `clear_stop` refused while a sequence is PAUSED; PAUSED chip with source | §1.3, §2.2, §2.3, §5.6, §5.9 |
| No-specimen mode | C-10 confirmation (SAF-SW-004 rules), persistent mode banner on every tab + tag in every floating window and dialog, NOSPEC chip, enter/leave on the Safety-limits tab | §2.8, §3.2, §5.5 |
| Travel calibration restore | TCAL chip + notice with [Restore] / [Keep board value] / [Ignore for this session] (`calibrations.resolve_travel_difference_async`, B3-19), wizard phase `RESTORING` | §2.8, §3.5, §6.2 |
| Indicators | `Indicator(state ON/OFF/UNKNOWN, …)`; UNKNOWN always grey; new chips DRV (DRV_PWR), K1 (K1_WELDED), FAULT (incl. HOME_DRIFT), CLK (CLK_FALLBACK), NOSPEC, TCAL; chips and channel tree iterate the generated name tables | §2.4, §4.2 |
| Engines | `PHASES` step strip, `continue_label` / `continue_moves`, `abort_reason`, `start() → GateResult`; load wizard finish-early / retake; tare undo | §6 |
| Other | `stop.confirmed/unconfirmed`, `channels.changed`, `remaining_s`, report list/load, generator schemas → generated forms, hotkey test mode, config REBOOT_REQUIRED + Save & reboot, `PlotSnapshot`/`vstate`, entry point `gui.app.run(backend, args) -> int` | §2–§9 |
| PO answers D-32, Orchestrator D-33, D-27 closed | GQ-01…20 decided as proposed; KL-01 shown in the hotkey tooltip/help; load step not reached → step flag **NOT_REACHED** shown in the run line, step table and report view; sequence-start refusals POS_UNCERTAIN / AFE_RATE_MISMATCH / PAUSED / ALM / DRV_PWR off (D-33 b); ALM during a sequence terminates it (D-33 c); travel wizard expects **800 steps/mm**, 160 only as "DIP change not applied" in C-05 | §3.6, §3.7, §5.3, §5.5, §6.2, §13 |
| **v0.3: B v0.3 delta B3-01…B3-21** | Resume = RESUME + re-issue (manual: RESUME only); Clear stop of a paused sequence = CONFIRM C-13 "ends the paused sequence" (end reason CLEARED); `resume` gate refuses on HALT/ESTOP/fault; `REFUSED_PAUSED` shown as info; sequence-start items PAUSED / POS_UNCERTAIN / AFE_RATE_MISMATCH / ALM / DRV_PWR_OFF; NOT_REACHED + end reasons DRIVER_ALARM / CLEARED; `travel_bound_mm` removed, LOAD `step_time_s` hidden; candidates 800 / 160; link counters `dup_frames` / `seq_anomalies`; indicator keys = lower-case generated names; topic `resume.ignored`; `resolve_travel_difference_async` (closes GRQ-B-19); entry contract `args.endpoint`; `sequencer.start(seq, *, confirmed=)` | §11.1a |
| Process | traceability to SRS v0.4 (same IDs as v0.3) incl. SW-LIM-004; tests G-37…G-44; GF-01…10 closed except GF-09; **M1 work breakdown WP-D0…WP-D8** | §10, §12, §14, §15 |

---

## 1. Scope and design principles

### 1.1 Scope
This document designs the PySide6 + pyqtgraph GUI of the PC application:
- the main window, toolbar, stop banner, mode banner and indicator bar;
- seven tabs, the detachable realtime plot windows and the readouts;
- the stop, pause/resume and confirmation handling;
- the calibration, tare and generator wizards;
- the module layout, the Qt adapter, the threading rules, the GUI test strategy and the M1 work breakdown.

The backend (link, device, pipeline, engines, recorder, report, simulator, hotkey) is Implementer B's (B §2–§15). It is only referenced here.

### 1.2 Principles (binding for the implementation)
| # | Principle | Source |
|---|---|---|
| P1 | **No business or safety logic in the GUI.** The GUI collects operator intent, calls the backend, and displays state, results and refusal texts **verbatim**. The backend owns every rule: limits, target arithmetic, plausibility, statistics, gates, sequencing, VALID timing, pause/resume, restore of the travel calibration. The GUI enables or disables a control only from `status().gates` (complete since A-05) or from a gate-like refusal (`GateResult`, `PreconditionError`). The only GUI-side mappings are pure *display* mappings (indicator item → chip level, gate-item code → "Clear stop first" hint, Y-range autoscale, unit labels). They are unit-tested. | role file, CLAUDE.md, SW-PLT-002, B §15.4 |
| P2 | **STOP is always one click away** and never depends on GUI state: first in the toolbar, in every floating window, in every dialog and in every wizard. It never takes focus. It calls `backend.stop(source)` **directly in the GUI thread** (B §15.4 rule 7; non-blocking ≤ 5 ms, priority TX path B §4.5). | SW-STOP-001 |
| P3 | **The keyboard can never start motion or confirm a safety question by accident.** Enter and Space never confirm (SAF-SW-004). Buttons that start motion (incl. Resume of a sequence and every engine Continue with `continue_moves`) are never default buttons and are mouse-only. The slider never moves the axis from the keyboard or the mouse wheel. | SAF-SW-004, SW-MAN-001, B §15.4 rule 11 |
| P4 | **The display shows the FW state, not an optimistic echo.** The enable checkbox, VALID toggle, HOMED, PAUSED, latches and stream state follow `BackendStatus`. A command only changes the display after the FW confirms it. | R1 §7, R3 §6.10 |
| P5 | **Dark ≠ OK.** Every `Indicator` in state `UNKNOWN` is drawn grey with "?" and never green (B §15.4 rule 10). Colour always comes with text. | R1 §7, A-06 |
| P6 | **The GUI thread is never blocked.** No `.result()` call. High-rate data is *pulled* by one refresh timer and never pushed as signals. Rendering per tick is budgeted (§4.6). | NFR-001, NFR-002, B §15.4 rules 2, 4 |
| P7 | **Reuse proven Thrust_Stand patterns, but avoid its known defects.** Reuse `EStopButton`, `SafeDialog`, `QtBridge`, `gc_policy`, `ChannelTree`, `ParamForm` and `ReadoutDock`, copied with an origin note (D-02, D-29 m). Avoid SWD-PM3-01/-04/-05/-06/-07. | R3 §6, SYS-010 |
| P8 | **Generated names only.** Indicator chips, the channel tree's status-bit group, tooltips, event-log texts and the "Clear stop first" mapping iterate the `protocol_gen` tuples (`*_BITS`, `*_NAMES`) and use their `*_DESC` texts. A completeness test (G-39) fails when the Integrator adds a name that has no GUI decision. | GF-08, IF-012 |

### 1.3 Stop vocabulary used in the GUI (SRS §2, §3.2, D-26, D-30, D-31)
| GUI term | Operator action / FW source | Backend call | FW meaning | Latched? / cleared by | Sequence |
|---|---|---|---|---|---|
| **STOP** | red STOP button (toolbar, docks, dialogs, wizards, Manual tab) | `backend.stop(source) → StopResult` | STOP immediate (cat. 2, holding) | no | terminated |
| **HALT** | Pause/Break or Ctrl+Break key (system-wide); physical STOP/BREAK button; Sequence [Abort] | hotkey thread → `backend.halt("hotkey")`; Abort → `sequencer.abort()` | latched immediate stop (source KEY / BUTTON / PC) | yes → **Clear stop** (HALT_CLEAR; also clears PAUSED, D-31) | terminated |
| **PAUSE** | toolbar **Pause**; Sequence [Pause]; physical PAUSE button | `backend.pause(source) → StopResult` (A-01) → FW **PAUSE 0x3B** | controlled stop; **PAUSED latch blocks every new motion start** (D-30); VALID cleared | yes → **Resume** (RESUME 0x3C, clears only PAUSED, D-31) or Clear stop (HALT_CLEAR) | paused (resumable); wizards and tare are terminated (A-15) |
| **RESUME** | toolbar **Resume**; Sequence [Resume]; physical PAUSE button while PAUSED (EVENT RESUME_REQUEST) | `backend.resume(source) → GateResult` (A-02; RESUME 0x3C per D-31, GF-11) | clears PAUSED; refused while HALT, ESTOP or any fault is latched (E_STATE) | – | sequence: interrupted step re-issued (absolute target / approach + trim); manual: no motion |
| **E-STOP** | red NC button on the MCU E-stop input (D-41: no power-removal contactor; FW stops the pulses and disables the driver) | – (clear: `estop_clear_async(confirmed=True)`) | ESTOP latched, driver disabled, not homed | yes → **Clear stop** (confirmed, C-03) + ENABLE + HOME | terminated |
| **Driver power lost** | K1 dropped / PSU off without E-stop (DRV_PWR → 0) | – | motion refused (DRV_UNPOWERED), HOMED cleared (D-29 c) | until power returns; then ENABLE + HOME | terminated |
| **Fault / limit** | limit switch, FW load limit, AFE stale, link watchdog, step / homing fault, K1_WELDED, HOME_DRIFT, SW limit | (clear: `fault_clear_async()`) | immediate or controlled stop per SRS §3.2 | per type (FAULT_CLEAR) | terminated |

The on-screen button is labelled **STOP**, not "E-STOP". The SRS reserves *E-stop* for the red E-stop button (an MCU / FW stop since D-41: pulses off, driver disabled, re-home needed), and the GUI must not teach operators otherwise (GQ-18, decided).

---

## 2. Main window

### 2.1 Overall wireframe (default layout, ≥ 1366 × 768; designed for 1600 × 900 at 125 % DPI)

```
+------------------------------------------------------------------------------------------------------------+
| Bird Bend Stand - COM7 - FW 0.1.0 / proto 1.0 / dict 0xB046DD01                                   _ [] X  |
| File  View  Tools  Help                                                                                   |
+------------------------------------------------------------------------------------------------------------+
|[#  STOP  #]|[|| Pause]|[Clear stop (1)]|[TARE]|[> Stream]|[o Record]|[Sample v]|   ● CONNECTED COM7 80.0 Hz [Disconnect]|  <- toolbar (fixed)
+------------------------------------------------------------------------------------------------------------+
| (!) PAUSED (PC, 14:03:12) - motion blocked. Sequence step 4 interrupted.             [Resume] [Stop sequence] |  <- stop banner (latch / stop result)
+------------------------------------------------------------------------------------------------------------+
| /// NO-SPECIMEN MODE - PC load limits OFF for this session. Board load limit active (default ±7 022 271   |  <- mode banner (persistent while
| ///  counts ≈ ±109 % FS). Travel limits active. Do not mount a specimen.            [Leave no-specimen mode] |     the mode is on, every tab)
| (i) Board steps/mm 796.020 differs from the active travel calibration 800.000.  [Restore active calibration]|  <- notice strip (TCAL, RO)
+--------------------------------------------------------------+---------------------------------------------+
| [Connection & Config][Safety limits][Test marks][Manual][Calibration & Tare][Sequence][Report]             |
|                                                              | Plot 1 ----------------------- [Float][STOP]|
|                                                              | +--------+ +-----------------------------+ |
|                     active tab content                       | |channels| | time view  (t = -30 ... 0 s)| |
|                       (scroll area)                          | | tree   | +-----------------------------+ |
|                                                              | |        | | X-Y view (x mm / F N)       | |
|                                                              | +--------+ +-----------------------------+ |
|                                                              +---------------------------------------------+
|                                                              | Readouts ---------------------- [Float][STOP]|
|                                                              |  F  123.4 N   x 12.345 mm   raw 239 512   80.0 Hz |
+--------------------------------------------------------------+---------------------------------------------+
| LINK ● CONN | ESTOP ● ok | HALT ● - | PAUSED ● PC | LIM S ● E ● | LOAD ● ok | FAULT ● - | DRV ● on | K1 ● ok | AFE ● 80.0 | WDG ● | NOSPEC ● ON |
| HOMED ● | ENA ● | MOV ● | ALM ● | PEND ● | BTN ● | THR ● VERIFIED | CAL ● PASS LOW_SPAN | TCAL ● | TARE ● 3 min | VALID ● 0 | CFG ● | CLK ● | REC ● | KEY ● | <- indicator bar
+------------------------------------------------------------------------------------------------------------+
```

- **Central widget:** a `QTabWidget` inside a scroll area, so small screens still reach every control.
- **Docks:** Plot 1 (right), Readouts (right, below), Event log (bottom, hidden by default). Plot 2 is created at first start, tabified with Plot 1 (SW-RT-001 "at least two"). Every dock can float and carries its own STOP and, while active, the NO-SPECIMEN tag (§4.1).
- **Banner stack** (top to bottom, each hidden when empty): stop banner (§2.3), mode banner (§2.8), notice strip (§2.8). Each is a fixed-height `QFrame`; the stack never scrolls away with the tab content.
- **Indicator bar:** a fixed, non-hideable strip at the bottom with two rows (§2.4). A QStatusBar is not used because it truncates permanent widgets on narrow windows.

### 2.2 Global toolbar (fixed: not movable, not floatable, not hideable; context menu suppressed — TS:gui/main_window.py:228-265)

Items in this order. `QToolBar` moves trailing items into its overflow menu when the window is narrow, so the safety items come first and STOP can never be hidden.

| # | Item | Widget | Backend call (B §15.1) | Enable state | State shown | Req |
|---|---|---|---|---|---|---|
| 1 | **STOP** | `StopButton(large)`, red, `NoFocus`, never default, min 48 px high, fires on press | `backend.stop("toolbar")`, **synchronous in the GUI thread**, returns `StopResult` | **always**. Also enabled when disconnected; the result then says "not sent" and the banner says "STOP NOT SENT: not connected – use the physical STOP / E-stop" | banner shows the real `StopResult`; later `stop.confirmed` / `stop.unconfirmed` | SW-STOP-001, NFR-002, SW-MAN-005 |
| 2 | **Pause / Resume** | `QToolButton`, `NoFocus`; text and icon follow `indicators.paused.state` (OFF/UNKNOWN → "‖ Pause", ON → "▶ Resume") | Pause: `backend.pause("toolbar") → StopResult` (A-01, FW PAUSE 0x3B). Resume: `backend.resume("toolbar") → GateResult` (B3-01: RESUME 0x3C, then re-issue of the interrupted sequence step; manual: RESUME only) | gate `pause` resp. `resume`. `pause` WARN ("no motion running — sets PAUSED only") is shown as tooltip, no dialog. `resume` REFUSE → disabled; the gate refuses while HALT, ESTOP or a fault is latched (B3-03; item codes = generated `BLOCK_BITS` names, B3-16): the button tooltip and the PAUSED banner read **"Clear stop first: <text>"** and offer [Clear stop…] (§5.9). A resume that is not executed is reported on `resume.ignored` (B3-17) → toast | P4: text changes only when the FW reports PAUSED / not PAUSED | SW-STOP-004, D-30, D-31 |
| 3 | **Clear stop** | `QToolButton`, badge = number of latched items | opens `ClearStopDialog` (§5.6) | any of the gates `clear_stop`, `estop_clear`, `fault_clear` without a "nothing to clear" REFUSE | badge | SW-STOP-003, SAF-SW-004 |
| 4 | **TARE** | `QToolButton` (bold) | `backend.tare() → GateResult` (REFUSE items shown verbatim) → non-modal `TarePopup` follows `tare_engine` (§6.4) | gate `tare` (REFUSE texts as tooltip) | TARE chip: age; [Undo tare] in the popup when `status().tare.can_undo` (A-16) | SW-TARE-001…003 |
| 5 | **Stream** | checkable "▶ Stream" / "■ Stream" | `stream_start_async()` / `stream_stop_async()` | gates `stream_start` / `stream_stop`. The backend refuses STREAM_STOP while moving or while an operation runs (B §5.6) | checked = `status().stream.on` | SW-ACQ-001, PO-FW-8 |
| 6 | **Record** | checkable "● Record" | `record_start() → GateResult` / `record_stop() → GateResult` (A-03) | gates `record_start` / `record_stop` (REFUSE: e.g. "recording belongs to the running sequence"); WARN "marks incomplete" shown as toast, no refusal (GQ-12) | REC chip: elapsed, rows, folder (tooltip) | SW-ACQ-002, SW-ACQ-004 |
| 7 | **Take sample** | `QToolButton` with a menu: window 0.1 / 0.5 / **1** / 2 / 5 / 10 s, "Other…" | `take_sample(window_s) → GateResult`; result as event `sample.taken` | gate `sample` (WARN "not recording → daily samples file" as tooltip) | toast + Event log row ("F̄ = 123.41 N, σ 0.05 N, N 80 → samples.csv") | SW-ACQ-003 |
| 8 | stretch | | | | | |
| 9 | **Link widget** | LED + `CONNECTED COM7 80.0 Hz` + [Connect]/[Disconnect] | `connect_async(endpoint)` / `disconnect_async()`; a click on the text opens the Connection tab | endpoint selected | `status().link` | SW-PLT-003 |

- No toolbar item has a keyboard shortcut; the keyboard stop is the global key (§5.3).
- Toolbar buttons keep `Qt.NoFocus`, so Space can never trigger them.
- `PreconditionError` / `GateResult` REFUSE items from any call are shown in a toast and in the Event log.

### 2.3 Stop banner
A full-width `QFrame` below the toolbar. It is hidden while no latch is active and no recent stop result needs showing. Style: red for a latch or fault, amber for PAUSED and LINK LOST, grey for "sent". When several rows apply, the most severe is shown with "+n more" (click → `ClearStopDialog`). Severity order: STOP/HALT/PAUSE not sent or unconfirmed > ESTOP > K1_WELDED > driver power lost > HALT > faults > SW trip > LINK LOST > PAUSED > recording failure > compat > first use > sent.

| Trigger (status / event, B §15.3) | Banner text (template) | Buttons |
|---|---|---|
| `StopResult.sent` (STOP, HALT or PAUSE; event `stop.issued`) | `STOP sent (toolbar, 14:03:12) – axis stopped, driver holding.` / `PAUSE sent (toolbar) – controlled stop, motion blocked until Resume.` (auto-hide 10 s unless a latch follows) | – |
| `StopResult` not sent | `<STOP/HALT/PAUSE> NOT SENT (toolbar, 14:03:12): <reason> – use the physical STOP/BREAK or E-stop button.` | – |
| `stop.unconfirmed` (A-23; STOP, HALT or PAUSE) | `<kind> NOT CONFIRMED by the board after 1 s – use the physical STOP/BREAK or E-stop.` | – |
| `indicators.estop` ON | `E-STOP active – pulses stopped, driver disabled, axis NOT homed (load may back-drive). <clear_hint>` (D-41) | [Clear stop] |
| `indicators.k1_welded` ON | `K1_WELDED: contactor K1 did not drop – driver power still present with the E-stop open. <clear_hint>` | [Clear stop] |
| `indicators.drv_pwr` OFF (not UNKNOWN) | `Driver power lost – motion refused, axis NOT homed. <clear_hint>` | [Manual tab] |
| `indicators.halt` ON | `HALT latched – source: <source>, <t>. Motion refused. <clear_hint>` (HALT_CLEAR also clears a PAUSED latch) | [Clear stop] |
| fault items ON (iterating `FAULTS_BITS`: LOAD_LIMIT, AFE_FAULT, STEP_FAULT, LIMIT_WIRING, HOME_NOT_FOUND, HOME_WIRING, HOME_DRIFT; K1_WELDED above) | `<NAME>: <FAULTS_DESC[name]> (<value>). <clear_hint>` e.g. `HOME_DRIFT: home switch moved by 350 µm …` | [Clear stop] |
| `safety.trip` | `SW load limit: 1 812.4 N > 1 765.2 N (pull max) – STOP sent, sequence terminated.` (values from the event) | – |
| `indicators.paused` ON | `PAUSED (<source>, <t>) – motion blocked. <sequence: "Sequence step <n> interrupted." / manual: "">` — and, when the `resume` gate has a HALT/ESTOP/FAULT item: `Clear stop first: <item text>.` (§5.9) | [Resume] (or [Clear stop…]) · [Stop sequence] (sequence only) |
| link state LOST | `LINK LOST – STOP sent; sequence terminated. Reconnecting…` | [Connection tab] |
| `rec.failure` | `RECORDING FAILED: <text> – sequence stopped (controlled).` | [Open folder] |
| first use: `move` gate REFUSE item "load input invalid" (no calibration/tare, SW load limits enabled, B §6.1 rule 2) | `No load calibration / tare: enabled PC load limits block motion – calibrate and tare, or switch to no-specimen mode.` (text from the gate item) | [Calibration tab] [Enter no-specimen mode…] (§2.8) |

All clear texts are the backend's `clear_hint` (B §6.5), so the GUI holds no stop logic. Read-only compat, the no-specimen mode and the travel-calibration difference are not stop states; they use the mode banner and the notice strip (§2.8).

### 2.4 Indicator bar (SAF-SW-005)
`IndicatorBar` = two rows of `IndicatorChip` (LED + short label + optional value).
- **Refresh.** Every refresh tick (33 ms) reads `backend.status().indicators` (B §6.5, rebuilt on every DATA/EVENT/GET_STATUS). The display therefore lags the backend by ≤ one tick, well inside the 200 ms of SAF-SW-005.
- **Items.** Each item is an `Indicator(state ON/OFF/UNKNOWN, since_t_us, source, value, clear_hint)` (A-06). `UNKNOWN` → grey "?" for every chip that uses the item (P5).
- **Generated names (P8).** `gui/indicator_map.py` holds one display table keyed by the generated names of `DATA_FLAGS_BITS`, `DATA_STATUS_BITS`, `FAULTS_BITS` and `SYS_FLAGS_BITS`: name → chip, polarity (which state is good) and level {unknown, ok, warn, alarm, info, neutral}. The item is read as `indicators.<name.lower()>` (B3-18: indicator keys are the lower-cased generated names, e.g. `stop_btn`, `pause_btn`); no alias table. Every generated name must appear in the table as a chip member or as "no chip" with a reason (G-39). Chip tooltips show `*_DESC[name]`, the item `source`, `value`, `since` and `clear_hint`. SW-only items (link state, SW trip, thresholds, no-specimen mode, travel-cal difference, recording, hotkey) are listed in the same table under `sw:` keys.
- **Help.** A click on a chip opens `StatusHelpDialog`: meaning (`*_DESC`), source, current value and `clear_hint` of every member item.

| Chip | Member items (generated name / SW item) | Grey | Green | Amber | Red | Tooltip / click | Req |
|---|---|---|---|---|---|---|---|
| LINK | sw `link_state` | DISCONNECTED | CONNECTED | CONNECTING, DEGRADED | LOST | `LinkStats`; click → Link statistics | SAF-SW-003, SW-PLT-003 |
| ESTOP | ESTOP | UNKNOWN | OFF | – | ON | `clear_hint` | SAF-SW-005 |
| HALT | HALT (+ `source` KEY / BUTTON / PC from `SOURCE_NAMES` + SW KEY) | UNKNOWN | OFF | – | ON "HALT key/btn/PC" | `clear_hint` | SAF-SW-005, SW-STOP-003 |
| PAUSED | PAUSED (+ `source` PC / BUTTON) | UNKNOWN | OFF | ON "PAUSED PC" / "PAUSED btn" | – | "motion blocked — Resume or Stop the sequence" (`clear_hint`) | SAF-SW-005, SW-STOP-004 |
| LIM S / LIM E | LIMIT_START / LIMIT_END; LIMIT_WIRING (both chips) | UNKNOWN | OFF | – | ON / wiring | `clear_hint` | SAF-SW-005 |
| LOAD | LOAD_LIMIT; sw `sw_trip`, `safety.warnings` | UNKNOWN / no valid load input | ok | ≥ warning level (90 %) | FW LOAD_LIMIT or SW trip | `clear_hint`, last value | SAF-SW-005, SW-LIM-002 |
| FAULT | FAULT + every name of `FAULTS_BITS` not shown elsewhere (STEP_FAULT, HOME_NOT_FOUND, HOME_WIRING, **HOME_DRIFT**, AFE_FAULT, LOAD_LIMIT, LIMIT_WIRING) | UNKNOWN | none | – | ON, text = latched names, e.g. "FAULT HOME_DRIFT 350 µm" | per fault `FAULTS_DESC` + `clear_hint` | SAF-SW-005, FW-HOM-004 |
| DRV | DRV_PWR | UNKNOWN | ON (power present) | – | OFF "driver unpowered – position lost" | `clear_hint` | SAF-SW-005, FW-SW-005 |
| K1 | K1_WELDED | UNKNOWN | OFF | – | ON "K1 WELDED" | `clear_hint`; **hidden while the board reports `drv.k1_check_enable` = false** (CR-03 / D-41 / D-43 e: no contactor, the check never runs; M2 gate condition) — an active K1_WELDED is never hidden; without board values the chip shows the indicator state (UNKNOWN grey) | SAF-SW-005, SAF-FW-025 |
| AFE | AFE_STALE, AFE_SATURATED, AFE_SETTLING, AFE_RATE_MISMATCH (+ measured rate), NO_AFE_DATA; sw `afe_synthetic` | UNKNOWN | ok + rate | settling, rate mismatch, synthetic (M1 FW) | stale, saturated, no AFE data | `clear_hint` | SAF-SW-005, FW-AFE-004 |
| WDG | LINK_WDG | UNKNOWN | OFF | – | ON | clears at the next valid frame | SAF-SW-005 |
| NOSPEC | sw `no_specimen_mode` | – | – (hidden when OFF) | ON "NO-SPECIMEN" | – | §2.8 | SW-LIM-004, SAF-SW-005 |
| HOMED | HOMED, POS_UNCERTAIN | UNKNOWN | homed | homed ± 1 step | not homed | HOME on the Manual tab | SAF-SW-005 |
| ENA | ENABLED; `status().motion.enabling_left_ms` (A-24) | UNKNOWN | enabled | "ENABLING 340 ms" | disabled | Manual tab | SAF-SW-005 |
| MOV | MOVING (+ `MOTION_STATE_NAMES` from STATUS when known) | UNKNOWN | standing | – (info: moving) | – | motion state text | SW-MAN-006 |
| ALM | ALM (+ DRV_PWR) | UNKNOWN | OFF | ON without driver power ("ALM – driver unpowered") | ON with driver power: **"ALM – new motion blocked"** (release 1: ALM is reported and blocks new motion starts while driver power is present, SAF-FW-026 / D-28; a running move continues in the FW; the SW stops a running sequence, D-33 c) | "check the driver; closed loop 4000 p/rev (D-27 closed): ALM also reports a position-following error" | SAF-SW-005, FW-SW-004 |
| PEND | PEND | UNKNOWN | in position | not in position | – | info | SAF-SW-005 |
| BTN | STOP_BTN, PAUSE_BTN | UNKNOWN | released | PAUSE pressed | STOP pressed | – | FW-SW-003 |
| THR | sw `thresholds_state` (+ `clamped`, effective levels, A-08) | – | VERIFIED | sending / DEFAULT_ONLY / VERIFIED clamped ("effective +a N / −b N") | FAILED / INVALID → motion disabled | Safety limits tab | SAF-SW-002 |
| CAL | `status().calibration` (status, LOW_SPAN, AFE mismatch) | none | PASS | WARN / LOW_SPAN / UNVERIFIED / AFE mismatch | – | Calibration tab | SW-CAL-008/009 |
| TCAL | sw `travel_cal_differs` (+ `status().calibration.restore_pending`) | – | – (hidden when OFF) | ON "board spm ≠ active" / "restoring…" | – | §2.8 | SW-CAL-001 |
| TARE | `status().tare` (state, age) | none | younger than 30 min (GQ-11) | older / large-offset warning | – | TARE | SW-TARE-002 |
| VALID | VALID | UNKNOWN | ON | – | – (OFF shown neutral) | – | PO-FW-7, SW-SEQ-004 |
| CFG | CFG_DIRTY, REBOOT_PENDING, NVM_DEFAULTED; `config_read_only` | UNKNOWN | clean | dirty (RAM ≠ NVM) / reboot pending / NVM defaulted | read-only (hash mismatch) | Config tab | SW-CFG-003/004, IF-008 |
| CLK | CLK_FALLBACK (GET_STATUS / EVENT only, KL-04) | UNKNOWN | OFF (hidden after 10 s) | ON "HSI clock – timing ±1 %" | – | `SYS_FLAGS_DESC` | SAF-SW-005, FW-PLT-002 |
| REC | sw `recording`, `recording_failed` | off | – | queue ≥ 50 % | recording (red dot, by convention) / failure | – | SW-ACQ-002/004 |
| KEY | `status().hotkey` (`HotkeyStatus.mode`, A-04) | – | REGISTERED | LL_HOOK (fallback hook) / test running | UNAVAILABLE ("Pause/Break works only while the app is focused") | §5.3; tooltip always states **KL-01** | SW-STOP-002 |
| RO | `link.compat` | – | – (hidden) | – | read-only | §2.8 | IF-008 |

**No chip (with reason, checked by G-39):** OVERRUN (counted in the link statistics and the LINK tooltip; channel in the tree), STREAM_ON (shown by the toolbar Stream button). Every other generated name is a chip member.

### 2.5 Menus
| Menu | Items |
|---|---|
| File | Open session… / Save session / Save session as… (`session` API, `*.bbsession.json`) · Recent sessions · Open data folder · Exit (Ctrl+Q; close rules §5.8) |
| View | New plot window · Reset layout · Save layout now · Readouts · Event log · Units ▸ **N** / kgf (SYS-003; `calc.units` helpers, B §15.4 rule 8) · Travel ▸ **test** / machine (GQ-03) · Theme ▸ light / dark (GQ-06) |
| Tools | Link statistics… (`LinkStatsDialog`) · **Test Pause/Break key…** (`HotkeyTestDialog`, gate `hotkey_test`, A-04; §5.3) · Performance overlay (frame interval p50/p95) · Open log file |
| Help | Status indicators & clear procedures… · Keyboard (Pause/Break, Ctrl+Break; **KL-01: not delivered while an elevated window has focus**, D-32 Q28) · About (SW, FW, proto, payload, dictionary hash, ICD version from `protocol_gen.ICD_VERSION`) |

### 2.6 Tabs
| # | Tab | Purpose | Section |
|---|---|---|---|
| 1 | Connection & Config | endpoint, device info, link stats, parameter form (read / check / write+verify / file / NVM / reboot) | §3.1 |
| 2 | Safety limits | SW travel and load limits, FW load-limit level, threshold verification, **no-specimen mode** | §3.2 |
| 3 | Test marks | report marks, presets, custom fields, 3-point-bend geometry, automatic snapshot | §3.3 |
| 4 | Manual | position, slider, entry, ±steps, hold-to-jog, speed/accel, enable, HOME, test zero, VALID, STOP | §3.4 |
| 5 | Calibration & Tare | active load and travel calibration, restore state, wizard launchers, tare info | §3.5 |
| 6 | Sequence | step table editor, loops, generators, files, sequence chart, run controls | §3.6 |
| 7 | Report | recordings, step results, report (re)generation, offline re-apply | §3.7 |

Tab names and order as listed (GQ-01, decided); the last active tab is restored at start.

### 2.7 Control gating (how the GUI enables controls)
- Every tick, `GateBinder` (`gui/gating.py`) reads `status().gates`: `GateResult(items=[GateItem(code, severity REFUSE|CONFIRM|WARN, text, clear_hint)])` for **every** `GateId` of B §5.6 (A-05): `move`, `jog`, `home`, `enable`, `disable`, `pause`, `resume`, `clear_stop`, `estop_clear`, `fault_clear`, `stream_start`, `stream_stop`, `record_start`, `record_stop`, `sample`, `tare`, `config_write`, `cal_travel_start`, `cal_load_start`, `sequence_start`, `sequence_edit`, `valid_toggle`, `test_zero`, `no_specimen`, `hotkey_test`.
- **Rules:**
  - any REFUSE item → control disabled; the tooltip lists the REFUSE texts and `clear_hint`;
  - CONFIRM items → the action opens `ConfirmDialog` (§5.5) and is repeated with `confirmed=True` (also `sequencer.start(seq, confirmed=True)`, B3-20; B §15.4 rule 6);
  - WARN items → amber warning line next to the control (e.g. SAF-SW-006 margin, ALM, no-specimen mode, travel calibration differs).
- **"Clear stop first" hint.** REFUSE items that mirror the FW BLOCK mask carry the generated `BLOCK_BITS` name (then `DATA_STATUS_BITS`) as `code`; SW-only items use B's `GateCode` StrEnum (B3-16). The display table `gating.CLEAR_FIRST_CODES = {"HALT", "ESTOP", "FAULT"}` adds a [Clear stop…] link next to such an item for the Resume, Manual and Sequence controls. It is a display mapping only; the gate decides.
- **Missing gate = fail-safe for motion.** A motion-related control without its gate (e.g. a backend in M1 that does not yet provide `pause`) is **disabled**. A non-motion control without its gate is enabled; its call returns the refusal.
- **Owner conflicts** (a wizard or sequence owns motion, B §3.5) arrive as REFUSE items of the `move`/`jog` gates. Manual controls are therefore disabled automatically while an operation runs. PAUSED arrives the same way (A-01, B3-04: `move`/`jog`/`home` REFUSE "Resume clears PAUSE"). A `MoveTicket` resolved `REFUSED_PAUSED` (a command in flight when PAUSE was pressed, D-33 k) is shown as **info** (grey event row "move not started: PAUSED"), never as an error; `REFUSED(nack)` is shown as an error with the NACK text (B3-04).
- **Field-level check.** Values typed into speed/accel/target fields are checked with `motion.check(kind, speed_mm_s=, accel_mm_s2=, target_mm=) → GateResult` (A-10) on every edit; the result colours the field.
- Gating is a convenience only: the backend re-checks every call (`PreconditionError`), and the GUI shows `user_text` in a toast and in the Event log.

### 2.8 Mode banner and notice strip (SW-LIM-004, SW-CAL-001, IF-008)

**Mode banner — no-specimen mode** (`widgets/mode_banner.py`; B §6.7, A-09):
- Shown while `status().safety.no_specimen_mode` is True, below the stop banner, **on every tab** (it belongs to the main window, not to a tab). It cannot be closed or collapsed; amber with a black hatched left edge and bold text, distinct from the stop banner.
- Text: `NO-SPECIMEN MODE – PC load limits OFF for this session. Board load limit active (<calibrated: +a N / −b N | default: ±7 022 271 counts ≈ ±109 % FS>). Travel limits active. Do not mount a specimen.` The board-limit part comes from `status().safety` (`ThresholdState`: VERIFIED with the calibration/tare ids → calibrated levels; `DEFAULT_ONLY` → defaults).
- Button [Leave no-specimen mode] → `limits.set_no_specimen_mode(False)` (no confirmation, it adds protection; REFUSE "moving" shown verbatim).
- Every `SafeDock` title bar (floating plot / readout windows) and every `SafeDialog` top bar (wizards, popups, confirmations) shows a compact amber **NO-SPECIMEN** tag while the mode is on, so a window on a second monitor never hides the mode.
- NOSPEC chip (§2.4) and an Event-log row on `safety.no_specimen` (on / off + reason, e.g. "ended: disconnect"). The end at disconnect, link loss or exit is the backend's (SW-LIM-004); the GUI only shows the toast "No-specimen mode ended (<reason>) – PC load limits active again".
- Entering: Safety-limits tab button, the first-use stop banner, and the travel-wizard CHECK page offer [Enter no-specimen mode…] → gate `no_specimen` → **C-10** (§5.5) → `limits.set_no_specimen_mode(True, confirmed=True) → GateResult`.

**Notice strip** (`widgets/notice_strip.py`, one row per notice, amber or grey, persistent while the condition holds):
| Condition | Text (from the indicator / status) | Buttons |
|---|---|---|
| `indicators.travel_cal_differs` ON; details from `status().calibration.travel_diff: TravelDiffState(board_spm, reference_spm, source, ignored, actions)` (B3-19) | `Board steps/mm <board_spm> differs from the <reference: spm0 of the aborted calibration / active travel calibration> <reference_spm>.` + source `restore_pending`: `Restoring after an aborted calibration… (retry n/3)` | one button per entry of `TravelDiffState.actions`: [Restore] → `calibrations.resolve_travel_difference_async("restore")`; [Keep board value] → `("keep_board")` (the indicator stays as a WARN "recalibrate"); [Ignore for this session] → `("ignore_session")` (indicator and gate items suppressed until exit). Result toast from the future / `cal.travel.restore`. These are configuration decisions, never motion; no confirmation dialog |
| `link.compat` major/payload mismatch | `Read-only: protocol/payload version mismatch – motion disabled.` | [About] |
| `config_read_only` (hash mismatch) | `Dictionary hash mismatch – configuration read-only.` | [Connection tab] |
| `nvm_defaulted` | `Board runs on default parameters (no valid NVM record) – check and Save to NVM.` | [Config tab] |
| `reboot_pending` | `Reboot-required parameter changed – effective after Save to NVM + Reboot.` | [Save & reboot…] (C-11) |

---

## 3. Tabs

### 3.1 Connection & Config (SW-PLT-003, SW-CFG-001…004, IF-008)

```
+-- Connection -------------------------------------------------------------------------------------+
| Endpoint [COM7 - STLink Virtual COM Port v] [Refresh]  Baud 921600 (fixed)  [Connect] [Disconnect] |
|          (entries from backend.endpoints(): ST-LINK COMx first, other COMx, "sim", tcp twin/sim)  |
| Device: FW 0.1.0 (build 3f2a1c)  proto 1.0  payload 1  dict hash 0xB046DD01 ✓  UID 0039...  feat AFE MOTION HOMING ... |
| Version check: ✓ compatible        (or: ✗ major/payload mismatch -> READ-ONLY, motion disabled)    |
| Link: frames 123 456  lost FW 0 / link 0  dup 0  seq anomalies 0  CRC 0  timeouts 0  rate 80.0 Hz [Details...] |
+-- Board configuration ----------------------------------------------------------------------------+
| Filter [          ]  [x] only changed  [ ] show advanced     CFG: ● clean (RAM = NVM)             |
| +---------------------------+-----------+-----------+-----------+-------+--------------+----------------+-----+ |
| | Parameter                 | Edit      | Board     | Default   | Unit  | Range        | Status         | Flg | |
| | v afe                     |           |           |           |       |              |                |     | |
| |   afe.gain_channel        | [A128 v]  | A128      | A128      | -     | A128,A64,B32 | OK             |     | |
| |   afe.rate_sps            | [SPS80 v] | SPS80     | SPS80     | -     | SPS10,SPS80  | OK             |     | |
| | v motion                  |           |           |           |       |              |                |     | |
| |   motion.steps_per_mm     | [800.000] | 800.000   | 800.000   | st/mm | 100...100000 | OK             | M N | |
| |   motion.v_max_travel_um_s| [ 30000 ] | 30000     | 30000     | um/s  | ...          | REJECTED (E_CONFIG H4) | N | |
| |   motion.pul_invert       | [x]       | false     | false     | -     | bool         | REBOOT_REQUIRED| N R | |
| | v safety                  |           |           |           |       |              |                |     | |
| |   safety.load_raw_max     |  (locked) | 7147283   | 7022271   | cnt   | ...          | session value – managed by the threshold manager | |
| |   safety.zero_raw         |  (locked) | 125012    | 0         | cnt   | i24          | session value – managed by the threshold manager | |
| | ...                       |           |           |           |       |              |                |     | |
| +---------------------------+-----------+-----------+-----------+-------+--------------+----------------+-----+ |
| Rule check: ✓ (or: ✗ H4 motion.v_max_load_um_s <= motion.v_max_travel_um_s -> [Write & verify] disabled) |
| [Read all] [Write & verify] [Revert edits] | [Save to file...] [Load from file...]                |
| [Save to NVM] [Reload from NVM] [Restore defaults...] [Save & reboot...]                           |
+---------------------------------------------------------------------------------------------------+
```

| Element | Behaviour | Backend (B §15.1) | Req |
|---|---|---|---|
| Endpoint list | `backend.endpoints()`: ST-LINK VCPs first, then other COM ports, "sim", the twin `tcp://127.0.0.1:5760` and the out-of-process simulator `tcp://127.0.0.1:5770` (A-22). The backend is started by `gui.app.run()` before the main window shows, so the hotkey works before Connect (B §15.4 rule 1). No COM port is opened unless the operator clicks Connect (D-06). | `endpoints()` | SW-PLT-003, SYS-008 |
| Connect | `connect_async(endpoint)` (future → `DeviceInfo`). Stage text comes from `link.state` events (`why`); errors (`BendStandError.user_text`) appear in a `SafeMessageBox` | `connect_async` | SW-PLT-003 |
| Version check | `status().link.compat` / event `link.compat`. Major/payload mismatch → RO chip, notice strip, motion gates REFUSE. Hash mismatch → `config_read_only`: form read-only + notice | `status()` | IF-008 |
| Device line | `DeviceInfo` incl. `features` decoded with `FEATURES_BITS` names (generated); AFE_SYNTHETIC shown amber ("M1 placeholder samples") | `status().link` | FW-CFG-004 |
| Link line | `status().link.stats` each tick (text updated at 1 Hz), incl. `dup_frames` and `seq_anomalies` (B3-10). [Details…] opens `LinkStatsDialog` (PC counters + FW counters from `device.status` events, OVERRUN count). DEGRADED comes from timeouts only; NACKs (e.g. BLOCK PAUSED) never degrade the link (B3-10, D-33 k) | `status()` | SW-PLT-003, FW-CMD-004 |
| ParamForm | Generated from `config.metas()` / `config.groups()` (`params_gen`: type, unit, range, enum names, decimals, flags `moving_ok` / `nvm` / `reboot_required`, advanced). Editors: bool → checkbox; enum → combo with names; f32 → double spin with decimals; ints → spin; u32 → validated line edit; `keyboardTracking` off. Columns: Edit / Board / Default / Unit / Range / Status / Flags (M = moving_ok, N = NVM, **R = reboot-required**). Copied from TS `param_form.py` with origin note. **Session parameters `safety.load_raw_min`, `safety.load_raw_max`, `safety.zero_raw` are read-only rows** "session value – managed by the threshold manager (SAF-SW-002) – see Safety limits", excluded from the configuration file (SW-CFG-003 v0.3) | `config` | SW-CFG-001, SW-CFG-003 |
| Rule check | live, debounced 200 ms: `config.check(edits) → list[Issue]` (A-07; range + hard rules H1…H4, same code as step 1 of `write_and_verify`); violations disable [Write & verify] and mark cells | `config.check` | SW-CFG-003 |
| Write & verify | `config.write_and_verify_async(edits)` → per-row `OK / REJECTED(detail) / MISMATCH / BUSY / TIMEOUT / NOT_ATTEMPTED / REBOOT_REQUIRED` in the Status column; summary toast. Gate `config_write` (REFUSE: not connected, read-only, operation running; per key: moving without `moving_ok`; WARN: reboot pending / reboot-required keys) | `config` | SW-CFG-003 |
| Save / Load file | `SafeFileDialog` (`*.bbboard.json`); `config.save_board_config(path)` / `load_board_config(path) → BoardConfigFile(values, unknown_keys, missing_keys, out_of_range, hash_mismatch)` → fills the **Edit column only** and shows the report dialog | `config` | SW-CFG-002 |
| NVM buttons | Save to NVM / Reload from NVM / Restore defaults (`config.save/load/defaults_async`); Restore needs C-04 (§5.5). CFG chip = CFG_DIRTY / NVM_DEFAULTED | `config` | SW-CFG-004, FW-NVM-001 |
| Save & reboot | enabled while `status().reboot_pending` (A-24); C-11 → `config.save_async()`, on success `config.reboot_async()` (A-07). The board reboots: stream restarts by the backend's connect steps, axis NOT homed afterwards, driver keeps holding (D-13) | `config` | SW-CFG-003 |

### 3.2 Safety limits (SW-LIM-001…004, SAF-SW-001/002/004/006)

```
+-- Travel limits (machine coordinate) -----------------------------------------------------------+
| FW soft limits (board, read-only):  0.500 ... 290.000 mm                     [edit on Config tab] |
| [x] Min  [   10.000 ] mm            [x] Max  [  250.000 ] mm                                      |
+-- Load limits (units: N | kgf per View > Units) ---------------------------------------------------+
| [x] Pull max (> 0)  [  1961.33 ] N  = 100.0 % FS      warning at [ 90 ] %  -> 1765.20 N           |
| [x] Push max (< 0)  [ -1961.33 ] N  = 100.0 % FS      warning at [ 90 ] %  -> -1765.20 N          |
| FW load-limit level [ 2157.46 ] N = 110.0 % FS   (allowed: >= SW trip, <= 110 % FS)              |
|   -> FW raw thresholds   min -6 897 259   max +7 147 283 counts   (zero_raw 125 012)              |
|   -> board read-back     ● VERIFIED 14:02:13 (cal 7, tare 3)           [Re-send & verify]         |
|   (!) clamped inward: effective +2 101.7 N / -2 157.5 N             <- ThresholdState.clamped      |
| (!) PC load limit enabled, no valid calibration/tare -> motion disabled (SAF-SW-001)              |
+-- No-specimen mode (SW-LIM-004; first use, travel/load calibration without a specimen) ----------+
| State: ● OFF          [ Enter no-specimen mode... ]   (C-10; refused while moving / operation)    |
|  (when ON:) ● ON since 14:01:55 - PC load limits OFF; board limit: default ±7 022 271 counts      |
|             [ Leave no-specimen mode ]                                                            |
+-- Live ------------------------------------------------------------------------------------------+
| F = 123.4 N (6.3 % of pull max)   x = 112.345 mm (inside 10.000 ... 250.000)                      |
| [Apply]  [Revert]          Limits are saved with the session (File > Save session).               |
+---------------------------------------------------------------------------------------------------+
```

| Element | Behaviour | Backend | Req |
|---|---|---|---|
| Fields | Edits stay local until [Apply]. [Apply] → `limits.set(cfg)` → `list[Issue]` shown next to each field (outside FW soft limits; FW level > 110 % FS; FW level < SW trip; disabling pull/push without a valid load input; refused while moving). An empty list means applied. "→ 1765.20 N" and "% FS" helper labels are display conversions through `calc` helpers | `limits.get()`, `limits.set()` | SW-LIM-001, SW-LIM-002 |
| FW thresholds | `limits.thresholds()` → `ThresholdState` (VERIFIED / FAILED / INVALID / DEFAULT_ONLY, raw min/max, zero_raw, ids, time, **`clamped` + effective levels**, A-08). [Re-send & verify] → `limits.recheck_async()` (A-08). Automatic triggers are the backend's (B §6.3) | `limits.thresholds()`, `recheck_async()` | SAF-SW-002 |
| Motion-disabled notice | shown when the `move` gate has the SAF-SW-001/002 REFUSE item | `status().gates` | SAF-SW-001 |
| No-specimen mode | state from `status().safety.no_specimen_mode`. [Enter…] → gate `no_specimen` (REFUSE: moving, operation; CONFIRM C-10) → `ConfirmDialog` C-10 → `limits.set_no_specimen_mode(True, confirmed=True)`. [Leave] → `set_no_specimen_mode(False)`. The mode is never saved in the session (B §6.7); the GUI therefore shows no "remember" option | `limits.set_no_specimen_mode` (A-09) | SW-LIM-004, SAF-SW-004 |
| Persistence | session file; also recording metadata and report (backend, B §8) | `session` | SW-LIM-003 |

The speed-margin warning (SAF-SW-006) appears where speeds are entered: Manual tab and sequence editor/start (§3.4, §3.6).

### 3.3 Test marks (SW-META-001/002, SW-REP-004 geometry)

```
+-- Preset ----------------------------------------------------------------------------------------+
| Preset [ Bracket_A v ]  [Load] [Save as...] [Delete]                                             |
+-- Marks -----------------------------------------------------------------------------------------+
| Measured element *  [ Wing bracket, PA12                ]                                        |
| Specimen number *   [ 17                                 ]                                        |
| Operator            [ O. Bam                             ]                                        |
| Notes               [ multi-line ...                     ]                                        |
| Custom fields:   +--------------------+---------------------------+                               |
|                  | Key                | Value                     |   [+ Add] [Rename] [- Remove] |
|                  | batch              | 2026-09                   |                               |
|                  | print orientation  | XY                        |                               |
|                  +--------------------+---------------------------+                               |
+-- 3-point bend (optional, off by default; session geometry) -------------------------------------+
| [ ] enable   span L [ 64.0 ] mm   width b [ 10.0 ] mm   thickness h [ 4.0 ] mm                     |
+-- Automatic snapshot (read-only, added to every recording and report) ----------------------------+
| v Board config  hash 0xB046DD01, CFG clean, steps/mm 800.000 ...                                  |
| v Calibration   active_load.json  K 4.5666e-4  PASS  LOW_SPAN    travel 800.000 (board = active)  |
| v Tare          tare_raw 125 012 at 14:02:11                                                       |
| v Limits        pull 1961.3 N / push -1961.3 N / travel 10 ... 250 mm / FW 110 %  no-specimen: off |
| v Versions      SW 0.1.0, FW 0.1.0, proto 1.0, payload 1                                           |
+---------------------------------------------------------------------------------------------------+
| Recording running: edits are logged as MARK_EDIT rows; the final marks are written at stop.       |
+---------------------------------------------------------------------------------------------------+
```

- The marks model is the backend's `TestMarks` (`marks.get()/set()`): fixed fields plus custom key/value fields. Presets are `*.bbmarks.json` via the `marks` list/save/load-preset calls.
- `*` = recommended. An empty value gives a warning at record start, not a refusal (GQ-12, decided; `record_start` WARN item).
- Custom keys must be unique and non-empty. `marks.set()` returns issues, which are shown inline.
- **Edits during a recording** (GRQ-B-07, accepted): marks are frozen in `meta.json` at start; edits are logged as `MARK_EDIT` rows and written as `marks_final` at stop. The footer says so while `status().recording` is on; the `marks.edited` topic (A-23) adds an Event-log row.
- The geometry is part of `SessionSettings` (B §13.5). It feeds the derived channels `sigma_mpa` / `eps` (greyed until it is valid) and the report (SW-REP-004).
- The snapshot view is built from `status()` (`calibration`, `tare`, `safety`, `link` info) and `limits.get()`. It is read-only.

### 3.4 Manual control (SW-MAN-001…006, SW-STOP-001, SAF-SW-004, SAF-SW-006)

```
+-- Position ------------------------------------------+ +-- Axis --------------------------------------+
|  Travel (test)      12.345 mm      (machine 112.345)  | | [x] Driver enabled          FW: ● ENABLED     |
|  Commanded target   15.000 mm  (pending 16.000)       | |     (ENABLING 340 ms while the driver settles)|
|  Force             123.4 N         raw 239 512        | | [  HOME  ]   ● HOMED   (homing: SLOW_APPROACH)|
|  state ● MOVE_ABS   owner MANUAL                      | | [ Set test zero here ]  x0 = 100.000 mm [Reset]|
|  last: MOVE_DONE TARGET at 15.000 mm                  | | VALID  [ 0 | 1 ]          FW: ● 0            |
+-------------------------------------------------------+ +----------------------------------------------+
| (!) PAUSED (PC) - motion blocked. [Resume]            <- REFUSE item of the move gate while PAUSED    |
+-- Target position (moves on release) ---------------------------------------------------------------+
|   10.000 |=============================[#]===============================| 250.000 mm   (SW limits)  |
|           ^ live position marker                   preview: 120.000 mm                            |
+-- Go to -------------------------------------------------------------------------------------------+
|  ( ) absolute  (o) distance    [   +5.000 ] mm   (relative to the commanded target)   [  Go  ]    |
|  Steps from commanded target:  [-10] [-1] [-0.1]   [+0.1] [+1] [+10]  mm                          |
|  Jog (hold):  [<< rear (-)  hold]   [hold  forward (+) >>]    jog speed [  2.000 ] mm/s           |
|               (+) = away from the home (START) switch;  pull direction: (+)                        |
+-- Motion parameters --------------------------------------------------------------------------------+
|  speed [  5.000 ] mm/s  (cap now 30.000: travel 30.000 / loaded 20.000 / step rate 312.5)          |
|  accel [ 100.0 ] mm/s^2  (max 100.0)                                                                |
|  (!) speed too high for the load-limit margin (SAF-SW-006)        <- motion.check WARN item         |
|  (!) no-specimen mode: PC load limits off                         <- move gate WARN item            |
+-----------------------------------------------------------------------------------------------------+
|  [#########  STOP  #########]     Keyboard: Pause/Break = HALT (system-wide)                         |
+-----------------------------------------------------------------------------------------------------+
```

| Control | Exact behaviour | Backend (`motion`, B §5.4) | Req |
|---|---|---|---|
| **Slider** (`TargetSlider`) | Horizontal, integer µm resolution, range = `status().motion` SW travel range (SW limits ∩ FW soft limits). While idle the handle sits at `commanded_target_mm`; a thin marker shows the live position. **Only a handle drag edits the value.** Groove clicks are ignored (page-step suppressed), the wheel is ignored and the slider has `NoFocus`, so no key moves it. While dragging only the preview label changes and nothing is sent. On `sliderReleased` exactly **one** call `motion.move_to(value, speed_mm_s=…, accel_mm_s2=…)`. On `PreconditionError` / `ValueError` the handle snaps back and the text is shown. Disabled by the `move` gate (not homed, latch, PAUSED, owner, …). | `move_to` → `MoveTicket` | SW-MAN-001, D-23 |
| **Go to** | Radio absolute / distance + `QDoubleSpinBox` (3 decimals, mm). Enter in the field only commits the field value (GQ-16, decided). [Go] (not default, not auto-default) → `motion.move_to(target)` or `motion.move_by(distance)`. Field colour from `motion.check(MOVE, target_mm=…)` (A-10). The GUI does no target arithmetic. | `move_to`, `move_by`, `check` | SW-MAN-002 |
| **Step buttons** | −10, −1, −0.1, +0.1, +1, +10 mm → `motion.move_by(±d)`. The backend accumulates on the last commanded target incl. a pending one; while a move runs it sets `pending_target_mm` (latest wins, D-29 i), shown as "(pending …)". | `move_by` | SW-MAN-003 |
| **Hold-to-jog** (`HoldButton`) | `pressed` → `motion.jog_start(direction, speed)`. `released`, `QApplication.applicationStateChanged` ≠ Active, window deactivate/hide, tab change, the button becoming disabled, or the `jog` gate closing → `motion.jog_stop()` (JOG 0) (B §15.4 rule 5). The 100 ms JOG refresh is done by the backend and only while the GUI beat (`gui_beat()`, every tick) is younger than 300 ms. A PAUSE or STOP ends the jog session in the backend first (A-02); the button then shows released even if still held, and a new press is needed. Un-homed jog is limited to `v_unhomed_mm_s`; the effective speed is shown. | `jog_start`, `jog_update`, `jog_stop` | SW-MAN-004, SAF-FW-016 |
| Speed / accel | `QDoubleSpinBox`; caps from `status().motion` `MotionLimits` (A-12): `v_cap_mm_s` (the cap that applies now: `v_travel_mm_s` or `v_load_mm_s` when loaded, ≤ `v_step_rate_mm_s`; jog un-homed ≤ `v_unhomed_mm_s`), `a_max_mm_s2`; the helper label shows all caps. Every edit → `motion.check(kind, speed_mm_s=, accel_mm_s2=)`: ERROR → red field, value not applied (the backend would also raise `ValueError`); WARN (SAF-SW-006 margin) → amber line. Values persist in the session. | `limits()`, `check()` | SW-MAN-005, SAF-SW-006 |
| Driver enabled | Checkbox bound to `indicators.enabled` (P4); `enabling_left_ms` shown while settling (A-24). Check → `motion.enable()`. Uncheck → `disable` gate CONFIRM → C-02 → `motion.disable(confirmed=True)`. A refusal reverts the display to the FW state. | `enable`, `disable` | SW-MAN-006, SAF-SW-004 |
| HOME | `home` gate (homing only at START, D-29 b). A CONFIRM item (abs(F) ≥ 5 % FS **or** unknown load) → C-01 → `motion.home(load_confirmed=True)` (SAF-FW-021). Homing phase text from STATUS/events with `HOME_PHASE_NAMES`; HOMED event drift value shown ("re-homed, drift 12 µm"); HOME_FAILED reason from `HOME_FAIL_REASON_NAMES`. | `home` | SW-MAN-006, SAF-SW-004 |
| Set test zero | gate `test_zero` → `motion.set_test_zero()` (x0 := commanded position). [Reset] → `motion.reset_test_zero()` (A-11). Never sent to the FW. | `set_test_zero`, `reset_test_zero` | SW-MAN-006 |
| VALID toggle | Two-segment toggle bound to the FW VALID bit → `motion.set_valid(flag)`; gate `valid_toggle` (refused while a sequence runs). | `set_valid` | SW-MAN-006, PO-FW-7 |
| Last result line | `motion.done` (`MoveDone`, A-13): reason from `MOVE_DONE_REASON_NAMES`, position; `stop_cause` from `STOP_CAUSE_NAMES` / `STOP_CAUSE_DESC` (e.g. "STOPPED: PC_PAUSE – PAUSE command"); `motion.dropped` (pending target dropped by a stop/pause) and a `MoveTicket` outcome `REFUSED_PAUSED` as grey **info** text; `REFUSED(nack)` red (B3-04) | topics `motion.*`, `MoveTicket` | SW-MAN-002/003 |
| PAUSED line | shown while the `move` gate carries the PAUSED REFUSE item: text verbatim + [Resume] (same handler as the toolbar). Manual Resume sends RESUME only; **no motion follows** (D-31). | `resume` | SW-STOP-004 |
| STOP | `StopButton(large)` on the tab, same handler as the toolbar (`source="manual"`) | `backend.stop` | SW-MAN-005, SW-STOP-001 |

Keyboard jog with ←/→ (as in Stefan's GUI) is **off** (GQ-08, decided).

### 3.5 Calibration & Tare (SW-CAL-001, -004, -008, -009, SW-TARE-001…003)

```
+-- Load calibration (active) ----------------------------+ +-- Travel calibration (active) -------------+
| File   active_load.json  (load_..._20261003T120000Z)     | | active   800.000 steps/mm (NVM + file)      |
| K      4.56665e-4 N/count   B  -57.085 N                 | | board    ● 800.000   (= active)             |
| Points 0 kg / 1.000 kg / 10.000 kg                       | |   or     ● 796.020 ≠ active  [Restore...]   |
| Fit    PASS   NL 0.002 % span   R^2 0.99999998          | |   or     ● restoring after abort (2/3)      |
| (!) LOW_SPAN: largest reference 98.1 N (5 % FS);         | | expected 800 (4000 p/rev closed loop, D-27) |
|     forces > 294 N shown as "extrapolated"               | | last: 2026-10-03  D1 10.020  Dtot 60.050 mm |
| AFE   A/128 80 SPS   ● matches board                     | | [ Start travel calibration wizard... ]      |
| push_calibrated: no (push forces "tension-calibrated")   | | [ History... ]                              |
| [ Start load calibration wizard... ]  [ History... ]     | +---------------------------------------------+
+---------------------------------------------------------+
+-- Tare (session only; repeat after every application start) --------------------------------------+
| tare_raw 125 012 counts  (robust mean, std 44, drift 3, 800 samples, 10.0 s)  at 14:02:11, age 3 min |
| window [ 10.0 ] s (2 ... 60)     [ TARE ] (same as the toolbar)   [Undo tare]   FW thresholds ● VERIFIED |
+---------------------------------------------------------------------------------------------------+
```

- **Data.** Active records from `status().calibration` and `calibrations` (active load/travel, history list), tare from `status().tare` (B §9.5–§9.6). An AFE-configuration mismatch shows "calibration invalid for load limits" (SW-CAL-009, D-29 l).
- **Travel restore state** (B §9.3.1, A-21): `board spm`, `travel_cal_differs`, `restore_pending` from `status().calibration`; buttons from `status().calibration.travel_diff.actions` → `calibrations.resolve_travel_difference_async("restore" | "keep_board" | "ignore_session")` (B3-19); outcome from the future and `cal.travel.restore`. Same rule as the notice strip (§2.8).
- **Wizards.** Started here; gates `cal_travel_start` / `cal_load_start`; `engine.start(...) → GateResult` (A-15) — CONFIRM items (no specimen mounted when the load is unknown; CFG_DIRTY would be saved by ACCEPT) → C-12 → `start(..., confirmed=True)`. They run in **non-modal** `SafeWizard` windows (§6.1). Only one operation at a time (B §3.5). Both wizards are allowed in no-specimen mode (SW-CAL-002/005); when the start gate refuses for "load input invalid" the REFUSE text comes with [Enter no-specimen mode…].
- **Tare undo** (GQ-15, decided; A-16): [Undo tare] enabled while `status().tare.can_undo` → `tare_engine.undo() → GateResult`.
- **History.** [History…] lists the previous files read-only. Activating an old file is not offered (SW-CAL-009 only needs the active copy).
- **Extrapolated.** The "extrapolated" marking is shown in readouts and plots (§4.3; A-14).

### 3.6 Sequence (SW-SEQ-001…007, SW-WIZ-002, SW-SEQF-001, SW-SCH-001/002, SW-STOP-004)

```
+- Sequence: staircase_0-200N.bbseq.json *  --------------------------------------------------------------+
| [New] [Open...] [Save] [Save as...] | [Generate...] [+ Step v] [Delete] [Up] [Down] [Loop...] [Unloop] | ● valid |
+--------------------------------------------------------------------+-------------------------------------+
| Lp | #  | Type    | Target  | U  | v mm/s | a mm/s2 | Settle | Capt | Time | Tol  | Label  | ! |  Sequence chart          |
| +3 |  1 | travel  |   0.000 | mm |  2.000 |   0     |   0.0  |  0.0 |    0 |  -   | start  |   |  F [N]                   |
| |  |  2 | load    | 100.0   | N  |  0.500 |   0     |   2.0  |  5.0 |    - | 2.0  | 100 N  |   |   |      o 200 N         |
| +  |  3 | load    | 200.0   | N  |  0.500 |   0     |   2.0  |  5.0 |    - | 2.0  | 200 N  |   |   |    o 100 N            |
|    |  4 | travel  |   0.000 | mm |  2.000 |   0     |   0.0  |  0.0 |    0 |  -   | return |   |   |___o____________ x [mm]|
|    |  5 | mark    |    -    |    |   -    |   -     |   -    |   -  |    - |  -   | end    |   |   |  | <- live marker      |
|    |    | [ ] advanced columns: capture during move, wait for operator                        |   (amber = active step, |
|    |    |                                                                                     |    green = capture points)|
+--------------------------------------------------------------------+-------------------------------------+
| Settings: travel ref [test v]  pull dir [+ v]  k_est [ 50.0 ] N/mm (last run 41.2)  on trim fail [stop v] [Defaults...] |
+-------------------------------------------------------------------------------------------------------------+
| [> Start] [|| Pause] [Resume] [Continue] [Stop (controlled)] [Abort (HALT)]  ● PAUSED (button)  step 2/5  loop 2/3 |
| phase TRIM  windows 3/6   F 198.7 N (target 200 +/- 2)   plan 00:03:12 / 00:09:40 (behind 0.4 s)   remaining ~00:05:40 |
| Messages: <SeqStatus.message, gate WARN items, step results, e.g. "step 3 NOT_REACHED: load 200 N not reached at the approach bound"> |
+-------------------------------------------------------------------------------------------------------------+
```

**Editor (`StepTableView` over `StepTableModel`).**
- **Model.** The `QAbstractTableModel` wraps a backend `Sequence` dataclass (B §10.1: `steps: list[Step]`, `loops: list[Loop]`, `travel_ref`, `k_est_n_mm`, `pull_dir`, `defaults: StepDefaults`, `notes`). The GUI edits fields of that object; it never expands or interprets steps.
- **Columns** = `Step` fields: kind (travel / load / hold / home / tare / mark), target (mm or N per kind), `speed_mm_s` (empty = sequence default), `accel_mm_s2` (0 = FW default), `settle_s`, `capture_s`, `step_time_s` (minimum dwell; not for LOAD steps, B3-07), `tol_n` (load), `label`.
- **Advanced columns** (toggle): `capture_during_move`, `wait_operator`. There is no travel-bound column: the load-step bound is fixed (nearer of the soft limit and an enabled SW travel limit, D-33 d; `Step.travel_bound_mm` removed, B3-07/B3-21). For LOAD steps the `step_time_s` cell is hidden ("–"): their timeout is computed by the backend (1.2 × travel time to the bound + 10 s, B3-07).
- **Applicability.** Cells that do not apply to the kind show "–" and are read-only. The kind → field table is a display table in `models/step_table_model.py`, cross-checked by a test against the backend validation.
- **Validation.** After every edit, `sequencer.validate(seq)` (`Issue(step_uid, field, severity, text)`) runs, debounced 200 ms. Each issue colours its cell (red ERROR, amber WARN, incl. the SAF-SW-006 margin per step) and its text is the cell tooltip; the "!" column and the "● valid" badge follow (SW-SEQ-001).
- **Loops** (`Loop(first, last, count)`): the gutter "Lp" draws a bracket with the count. [Loop…] wraps the selected contiguous steps; the dialog asks for the count, 1…10 000 or **0 = until stopped**. Nesting is limited to one level (backend validation). [Unloop] removes the bracket.
- **Inserting steps.** [+ Step ▾] inserts a typed step after the selection with `defaults`. [Generate…] opens the generator wizard (§6.5); `Sequence.insert_block(block, mode, index)` keeps the generated steps editable (SW-WIZ-002).
- **Undo/redo** (`QUndoStack`) on model edits. The editor is read-only while the gate `sequence_edit` refuses (running or PAUSED).

**Files.** Open / Save / Save as via `SafeFileDialog`, `*.bbseq.json` → `sequencer.load(path)` / `sequencer.save(seq, path)`. On `FileFormatError` the error is shown and the current sequence stays unchanged (SW-SEQF-001). A dirty flag (`*` in the title) triggers C-08 on New/Open.

**Chart (`SequenceChart`, pyqtgraph, x = travel mm, y = load N/kgf).**
- **Planned path** = `sequencer.planned_path(...)` over `sequencer.expand(seq)` (B §10.2: `PathPoint(x_mm, f_n, exec_idx, label, known)`). Polyline breaking at HOME, step labels (`TextItem`, hidden when overlapping); capture windows green; estimated coordinates (`known` ≠ both) dashed (SW-SCH-001).
- **During execution** (SW-SCH-002), on every refresh tick (≥ 10 Hz guaranteed, typically 30 Hz): live marker = vertical `InfiniteLine` at the current travel plus a single-point `ScatterPlotItem` at (x, F) from `data.latest()`; active step highlighted from `SeqStatus.exec_idx`; measured trace = `data.sequence_trace()`. A step that ended **NOT_REACHED** (D-32) is drawn with a red cross at the point where the axis stopped.
- **Ranges.** Explicit axis ranges (§4.6). The chart is redrawn only on plan changes or new data.

**Run controls** (enabled from gates; texts verbatim):
| Button | Call | Notes | Req |
|---|---|---|---|
| Start | `sequence_start` gate (REFUSE: not homed, not enabled, **DRV_PWR_OFF**, **ALM**, **PAUSED**, **POS_UNCERTAIN** ("re-home"), **AFE_RATE_MISMATCH** (D-33 b, B3-05; one item each with its own text, shown verbatim), latch, owner, validation errors, targets outside SW limits, cal/tare for load steps, LOAD steps in no-specimen mode, thresholds not VERIFIED, stream, recording cannot start; CONFIRM: HOME steps under load, **Pause/Break key unavailable** (GQ-09, decided), travel-only sequence in no-specimen mode, travel calibration differs; WARN: margin, LOW_SPAN, extrapolation) → C-07 if CONFIRM/WARN → `sequencer.start(seq, confirmed=True) → GateResult` (B3-20; confirms the CONFIRM items as evaluated at the call — if the gate changed meanwhile, the returned items are shown in C-07 again). The backend starts a recording if none runs (B §10.3). | non-default, mouse-only | SW-SEQ-005, SAF-SW-006 |
| Pause | `backend.pause("sequence") → StopResult` (FW PAUSE, same as the toolbar) | state kept (PAUSED + `paused_source`) | SW-STOP-004 |
| Resume | `backend.resume("sequence") → GateResult` (B3-01: RESUME, after its ACK re-issue of the interrupted step + capture restart); while the gate refuses for HALT/ESTOP/FAULT: "Clear stop first" (§5.9) | mouse-only (re-issues motion) | SW-STOP-004 |
| Continue | `sequencer.continue_()` | only in state WAITING_OPERATOR (MARK step with `wait_operator`) | SW-SEQ-001 |
| Stop (controlled) | `sequencer.stop()` | sequence ends STOPPED; also the way out of a PAUSED sequence | SW-SEQ-007 |
| Abort (HALT) | `sequencer.abort("operator")` (HALT, latched; Clear stop needed) | | SW-SEQ-007 |

**Run status.** `sequencer.status()` → `SeqStatus`, pulled every tick; `seq.status` and `seq.step_result` events feed the Messages line and the Event log. Shown: `state` (PAUSED with `paused_source`, A-18), `exec_idx`/plan length, `loop_iters`, `phase` (COMMAND / MOVING / APPROACH / TRIM / SETTLE / CAPTURE / HOLD / WAIT_OPERATOR), `windows_done/total`, `plan_t_s` / **`plan_total_s`**, `behind_s`, **`remaining_s`** ("∞ – loop until stopped" when None, A-18), `k_est_n_mm`, `message`. End reasons are shown verbatim incl. **NOT_REACHED**, **DRIVER_ALARM** (ALM during the sequence → controlled STOP, D-33 c) and **CLEARED** (paused sequence ended by Clear stop, C-13) (B3-06). Step results (`seq.step_result`) show the flags verbatim, incl. **NOT_REACHED** (D-32, D-33 d: load target not reached when the approach reached its bound = the nearer of the soft limit and an enabled SW travel limit in the step direction; the axis stopped there, not a limit trip; the sequence stopped) in red, ON_TARGET, NOT_ON_TARGET, INCOMPLETE, WINDOW_DISCARDED, and the local step error BOUND_NOT_AHEAD (B3-21).

**As built (M4, 2026-10-05; deviations from the draft above):**
- Toolbar in two rows (files · Generate · Undo / Redo · badge; + Step ▾ · Duplicate · Delete · Up · Down · Loop… ·
  Unloop · advanced columns) and a compact settings grid (name, travel ref, pull dir, k_est, step defaults v / a /
  settle / capture / tol) so the tab fits the 1366 px layout. **No `on_trim_fail` combo** (B6-01: removed, SRS v0.6.2
  SW-SEQ-006 "no continue option"). No [Defaults…] dialog: the defaults are inline.
- Undo / Redo are deep-copy snapshots of the `Sequence` (≤ 100) instead of a `QUndoStack`.
- Loops are drawn as a bracket in the **row header** (`┌×3 2`, `│ 3`, `└ 4`, `∞` = until stopped, `[×n` = one-step
  loop) instead of a separate "Lp" column; the tooltip lists the loops of the row.
- Step-list edits that move indices (delete, insert, duplicate) keep the loop indices with pure helpers in
  `seq_access` (`loops_after_delete` / `loops_after_insert`); generated blocks use `Sequence.insert_block`. Nesting /
  overlap / count rules stay the backend's validation (B6-02). Up / Down swap steps and keep the loop index ranges
  (a step can move into / out of a loop).
- Start: `sequencer.check_start(seq)` (B6-06) on the click — REFUSE items one message row each (code + text + hint),
  CONFIRM / WARN items → C-07 → `start(seq, confirmed=True)`; the button's enable state follows the static
  `sequence_start` gate plus "no validation ERROR". Pause / Resume = `backend.pause / resume("sequence")`; Abort =
  `sequencer.abort()` (reason "operator").
- Live marker: `SeqStatus.marker_x_mm / marker_f_n` (sequence coordinate, B6-07); fallback `data.latest`
  (`x_test_mm` / `x_mm`, `F_N`). Trace `data.sequence_trace()` every 3rd tick. The chart is refreshed while the tab
  is shown (main refresh, 33 ms).
- Step results (`seq.step_result`, `StepResult`, B6-08) are one message row each with the flags verbatim;
  NOT_REACHED rows are red with "load target not reached at the approach bound (axis stopped there; not a limit
  trip)" and a red cross at (`x_end_mm`, `f_end_n`) in the chart.

### 3.7 Report (SW-REP-001…004, SW-ACQ-002)

```
+-- Recordings (root: D:\BendData\recordings) ---------------------------------------------------+
| Date/time          | Specimen          | No. | Sequence           | Status     | Duration |  |
| 2026-10-03 14:05   | Wing bracket PA12 | 17  | staircase 0-200 N  | complete   | 00:09:12 |  |
| 2026-10-03 13:40   | Wing bracket PA12 | 16  | (manual)           | partial    | 00:02:01 |  |
+-----------------------------------------------------------------------------------------------+
+-- Selected: 20261003_140512_Wing-bracket_17 --------------------------------------------------+
| Marks: element, number, operator ...   Calibration: PASS, LOW_SPAN (!)   Tare 125 012          |
| Warnings: LOW_SPAN; push forces tension-calibrated; no-specimen mode: no; 0 lost frames;       |
|           2 INCOMPLETE windows; 1 step NOT_REACHED                                             |
| Step results:                                                                                  |
| Step | Loop | Label | N   | F mean [N] | F std | x mean [mm] | drift | flags                    |
|  2   | 1    | 100 N | 400 | 99.12      | 0.21  | 2.103       | 0.02  | ON_TARGET                |
|  2   | 2    | 100 N | 311 | 98.70      | 0.30  | 2.115       | 0.05  | INCOMPLETE, ON_TARGET    |
|  3   | 2    | 200 N |  -  | (167.4 max)|  -    | 289.500     |  -    | NOT_REACHED              |
+-- Options --------------------------------------------------------------------------------------+
| Re-apply: calibration [ (as recorded) v ] [Choose file...]  tare raw [ (as recorded) ]         |
| 3-point bend outputs [ ] (L, b, h from the session geometry)                                    |
| [Generate / Regenerate report] [Open HTML] [Open folder] [Open CSV]                             |
+-----------------------------------------------------------------------------------------------+
```

- **Report generation.** The backend builds the report at sequence end, also for stopped/aborted runs, which are flagged partial (B §10.3, §11). The event `report.ready` refreshes the list.
- **Recording list and step table** (A-19): `reports.list_recordings(root=None)` (folder, date, marks, sequence name, status, duration) and `reports.load_result(dir)` (parsed `report.json`: warnings, step/iteration results). The step table is read-only (SW-REP-002); flags are displayed verbatim, NOT_REACHED in red (D-32).
- **[Generate]** → `reports.build_async(recording_dir, cal=…, tare=…, bend3p=…)` (SW-REP-001, SW-REP-003 re-apply, SW-REP-004 3-point bend; `bend3p=None` = session default).
- **[Open HTML]** uses `QDesktopServices.openUrl` (system browser, GQ-13, decided).

**As built (M4):** recordings table (date, specimen marks, sequence, status — PARTIAL / FAILED red —, duration,
report yes / no, folder) refreshed on first show, [Refresh] and `report.ready`; selection → `load_result` (no
`report.json` → "no report yet — [Build report] creates it"); step result table columns Step · Loop · Label · N · F
mean / std / min / max / SE / drift (display unit) · x mean · Target · Flags (red for NOT_REACHED, INCOMPLETE,
NOT_ON_TARGET, BREAK_DETECTED, TIMEOUT, SLIP, WINDOW_DISCARDED). Options: calibration file to re-apply (path; empty =
as recorded), tare raw (checkbox + value; off = as recorded), 3-point bend (checkbox + L / b / h; prefilled and
ticked when the session has a geometry; **unticked = `bend3p=False` = off**, B6-14). [Build report] →
`build_async(dir, cal=, tare=, bend3p=)`; [Open HTML] / [Open folder] / [Open CSV] → `QDesktopServices.openUrl`
(`ReportPaths` has no CSV path: `<folder>/data.csv`, GRQ-B-29). The GUI computes no statistic.

---

## 4. Realtime display (SW-RT-001…005, NFR-001)

### 4.1 Plot windows (`PlotDock`, SW-RT-001)

```
+-- Plot 1 ------------------------------------------ [NO-SPECIMEN] [Float] [Close] [## STOP ##] -+
| Window [ 30 s v ] [Freeze] | Y: (o) auto ( ) manual [ -10 ] .. [ 250 ] | X-Y: x [test travel v] y [force v] | units N |
+----------------------+----------------------------------------------------------------------------+
| Channels             |  time view                                     F [N]            x [mm]     |
| [x] v DATA           |  250 +---------------------------------------------------------+ 120   |
| [x]   raw [counts]   |      |                                    ___/~~~~             |       |
| [ ]   setpoint [mm]  |      |                          ________/                      |       |
| [ ]   sample rate    |      |   ______________/~~~~~~                                 |       |
| [ ]   seq gaps       |    0 +---------------------------------------------------------+ 0     |
| [ ] > status bits    |      -30 s                     t [s]                          0         |
| [x] v Derived        |      lanes: VALID ___|~~~~~|___  MOVING ~~~|____|~~~  PAUSED ____|~~|_  |
| [x]   force [N]      +----------------------------------------------------------------------------+
| [x]   travel test    |  X-Y view                          F [N]                                    |
| (g)   stress [MPa]   |  250 +----------------------------------------+                             |
|       needs geometry |      |                         ..-*  <- live point                          |
| [ ]   stiffness      |      |              ...-''''                  |                             |
| ...                  |    0 +----------------------------------------+                             |
|                      |      0             x [mm] (test)            12                               |
+----------------------+----------------------------------------------------------------------------+
```

- `PlotDock(SafeDock)` is a `QDockWidget`: movable, floatable, closable. A custom title bar carries **[Float] [Close] [STOP]** and, while the mode is on, the **NO-SPECIMEN** tag (§2.8). The title bar stays when floating, so **every floating window has its own STOP** (SW-RT-001, SW-STOP-001). Docks re-attach by double click on the title or [Float] again. All docks (Plot n, Readouts, Event log) use the same `SafeDock` base.
- **≥ 2 windows.** "View ▸ New plot window" creates Plot n (max **4**, GQ-04, decided). Plot 1 and Plot 2 exist at first start: Plot 1 docked right with time + X-Y views, Plot 2 tabified with Plot 1, time view only.
- A closed dock is hidden, not destroyed, and can be reopened from View. "Reset layout" restores the default arrangement, re-attaches all docks and keeps the channel selections.
- Each window has **two fixed view slots**: a *time view* (always) and an *X-Y view* (toggle). Both are `PlotItem`s inside **one** `pg.GraphicsLayoutWidget`, so one `QGraphicsView` paints per window and tick (§4.6). There is no free pane grid: Thrust's grid caused SWD-PM3-04/-05.
- The time view has a left Y axis (first unit) and an optional right Y axis (second unit, own `ViewBox`). A third unit is refused with the hint "open another plot window". Ticked **status bits** go to a compact *lanes* strip under the time view (digital 0/1 traces, fixed Y range, generated names as static ticks). It is created only while at least one bit is ticked.

### 4.2 Channel tree (`ChannelTree`, SW-RT-002, SW-RT-004)
- Copied and adapted from TS `gui/widgets/channel_tree.py` (origin note). Checkboxes per channel, tri-state group nodes, a colour swatch per channel (replaces the in-plot legend, SWD-PM3-02) and `batch()` so a group tick causes one update.
- The content comes **only** from the backend registry `backend.channels` (`ChannelRegistry`, B §7.4): key, label, unit, quantity group, prerequisite set, `available` + `reason`.
- **Updates.** The tree re-reads the registry on the topic `channels.changed` (A-17); no polling.
- **Greyed prerequisites.** `available = False` → item disabled (grey, italic) with `reason` as tooltip (e.g. "needs load calibration + tare").
- A ticked channel that becomes unavailable stays ticked: its curve is blanked and its label reads "(n/a)". It comes back after re-calibration.
- **Status bits (P8).** The registry's status-bit channels are built by B from `DATA_FLAGS_BITS` + `DATA_STATUS_BITS` (reserved `''` entries skipped). The tree shows them in generated order with `*_DESC` tooltips; G-39 asserts that the registry's bit channels equal the generated names, so no bit is hand-listed in the GUI.

Tree (keys from B §7.4; the registry is the source, this table is only the expected grouping):
| Group | Channels (registry keys) | Prerequisites |
|---|---|---|
| DATA | `raw` · `setpoint_um` (shown in mm) · `rate_sps` · `lost_frames` | none |
| Status bits | one 0/1 channel per name of `DATA_FLAGS_BITS` (VALID, MOVING, HOMED, ENABLED, ESTOP, HALT, FAULT, OVERRUN) and `DATA_STATUS_BITS` (PAUSED, LIMIT_START, LIMIT_END, LOAD_LIMIT, AFE_STALE, AFE_SATURATED, AFE_SETTLING, AFE_RATE_MISMATCH, LINK_WDG, STOP_BTN, PAUSE_BTN, ALM, PEND, POS_UNCERTAIN, NO_AFE_DATA, DRV_PWR) — list as generated for ICD v0.3 | none |
| Force | `F_N` · `F_kgf` · `peak_n` · `force_rate_n_s` · `noise_counts` · `noise_n` | calibration + tare (`noise_counts`: none) |
| Travel | `x_mm` · `x_test_mm` · `speed_mm_s` | homed for absolute meaning; `x_test_mm`: x_zero |
| Mechanics | `k_tan_n_mm` · `k_sec_n_mm` · `work_nmm` | F (+ x_zero for the secant) |
| 3-point bend | `sigma_mpa` · `eps` | geometry entered, F |
| Limits (overlay, GUI-drawn lines) | SW pull/push trip and warning levels, FW level (effective level when clamped), from `limits.get()` / `limits.thresholds()` (horizontal lines in force units); hidden in no-specimen mode except the FW level | calibration + tare |

### 4.3 Views and controls (SW-RT-003, SW-RT-005, SYS-003)
| Control | Behaviour |
|---|---|
| Window | 5, 10, **30**, 60, 120, 300, 600 s (combo + free entry 5–600 s) |
| Freeze | stops data updates of this window (no snapshot, no repaint); pan/zoom and the pyqtgraph context menu become active; [Live] resumes. Freezing one window does not affect the others or the recording. |
| Y range | per axis: **auto** (computed in the GUI from the snapshot min/max with hysteresis, §4.6, pure display scaling) or **manual** (min/max fields) |
| X-Y view | x ∈ {travel test, travel machine, setpoint}, y ∈ {force N/kgf, raw}; window = same length as the time view, or "since record start" / "since sequence start" (`data.xy(..., since=)`, A-14); live point = latest sample |
| Units | global View ▸ Units N / kgf (SYS-003) relabels force axes and readouts: the plot uses the `F_kgf` channel, readouts use the `calc.units` helpers (B §15.4 rule 8). A display choice stored in the session |
| Extrapolated / invalid | per column `vstate` of `SeriesMinMax` / `XYSnapshot` (A-14): 0 OK solid, 1 EXTRAPOLATED dashed (beyond 3× the largest calibration force, SW-CAL-008), 2 INVALID grey, 3 NO_DATA gap. Implemented as up to three `PlotCurveItem`s per channel (solid / dashed / grey) fed from the masked arrays; only created when a non-OK state exists in the window |

**Readouts dock** (`ReadoutDock`, copied/adapted from TS `gui/widgets/readout.py`, SW-RT-005):
- Fields: force, travel (test and machine), raw, sample rate. Each has Value / Min / Max / **State** = `LatestSample.state` ∈ {n/a, STALE, SATURATED, INVALID, EXTRAPOLATED, OK} (A-14), refreshed at 10 Hz from `backend.data.latest()`.
- The display-only EMA (τ 0.3 s) is optional and labelled "smoothed".
- Large font for force and travel.

### 4.4 Event log dock
A table of FW EVENTs and backend events (time, source, code, text): stops with cause, faults set/clear, VALID auto-clear, MOVE_DONE, homing, enable/disable, button presses, pause/resume, refusals, tare/cal results, travel-cal restore, no-specimen on/off, recording files. Sources: the topics `fw.event`, `stop.issued`, `stop.confirmed`, `stop.unconfirmed`, `safety.*`, `motion.*`, `seq.*`, `rec.*`, `cal.travel.restore`, `marks.edited`, `hotkey.*`, `sample.taken`, `report.ready` and `log` via the bridge (§9.2), plus GUI-side refusals (`PreconditionError.user_text`).
- FW event texts: `EVENT_NAMES[code]` + argument decoded with the generated argument enums (`STOP_CAUSE_NAMES`, `SOURCE_NAMES`, `HOME_FAIL_REASON_NAMES`, `DRIVER_DISABLED_CAUSE_NAMES`, `PAUSE_CLEARED_REASON_NAMES`, …) and `*_DESC`. An unknown code is shown as "EVENT <code>" (ICD §0.2).
- Filter by severity; copy to clipboard.
- Capped at 5 000 rows (ring) for the display; the backend log and the recording keep everything.

### 4.5 Layout persistence (SW-RT-001)
- `QSettings` (INI, `%APPDATA%/BirdBendStand/gui.ini`):
  - `main/geometry`, `main/state` (dock positions, floating state);
  - `plots/layout` JSON `{"version": 1, "docks": [{"name", "title", "floating", "geometry", "window_s", "time": {"channels": [...], "y": {"L": "auto" | [min, max], "R": ...}}, "xy": {"enabled", "x", "y", "y_range"}}]}`;
  - `ui/units`, `ui/travel`, `ui/last_tab`, `ui/theme`.
- **Restore rules:** unknown channel keys are dropped (logged); a floating window whose geometry is outside every current screen is moved onto the primary screen; a corrupt JSON gives the default layout plus a log warning (TS `main_window.py:1071-1133` pattern); Freeze is never restored.
- Saved on close and on "View ▸ Save layout now". Sessions (backend) never contain the GUI layout.

### 4.6 Performance design (NFR-001: ≥ 20 fps, p95 frame interval ≤ 50 ms; NFR-002 STOP click ≤ 50 ms p95)

**Load to handle.**
- 80 Hz × 30 s = **2 400 samples per channel**, about 15 analog channels plus 24 bits.
- A 600 s window = 48 000 samples per channel, which needs decimation (backend pyramid).
- Thrust_Stand failed at 400 Hz × 32 curves in a pane grid. That data volume is 10× larger than ours, but the defect was in **painting**, not in data volume (R3 §6.2; TS `03_SW/docs/SW_test_report_preM3.md` §4–§5).

**Budget per refresh tick (33 ms timer → 30 fps nominal, 20 fps floor):**
| Stage | Budget (p95) | Design |
|---|---|---|
| `data.snapshot()` (copy + decimation, B §7.5) | ≤ 3 ms | one call per window-length group; returns `PlotSnapshot` with `px_width` columns (A-14) |
| `setData` for all curves | ≤ 4 ms | `PlotCurveItem` (not `PlotDataItem`), arrays handed over without copies |
| paint (all visible windows) | ≤ 15 ms | 1 `GraphicsLayoutWidget` per window, no axis repaint in steady state (below) |
| indicators + gates + banners + readouts | ≤ 2 ms | change-only widget updates (`setText` only if the text differs); readouts at 10 Hz |
| GC (GUI-thread policy) | ≤ 5 ms per tick; full GC ≤ 16 ms, rare | `gc_policy.py` copy (§9.4) |
| **Total** | **≤ 30 ms** | leaves headroom so a STOP press is served within the same 50 ms |

**pyqtgraph settings (binding for the implementation):**
1. `pg.setConfigOptions(antialias=False, useOpenGL=False)`. OpenGL is evaluated in the perf test as an option only.
2. **Relative time axis:** x = `PlotSnapshot.t_col_s` ∈ [−window, 0], relative to `t_end_dev_s` (device time of the newest sample, A-14). The X range stays constant, so the bottom `AxisItem` is not re-generated every tick. The absolute device time is shown in the window's title strip only.
3. **Explicit Y ranges, no lazy auto-range:** `ViewBox.disableAutoRange()`. In auto mode the GUI computes the range from the snapshot `lo`/`hi` (NaN-aware) with 5 % margin and **hysteresis**: expand immediately, shrink only when the data span is < 70 % of the range for ≥ 1 s. Then `setYRange(lo, hi, padding=0, update=False)`. This removes the double paint per refresh (SWD-PM3-05 cause 2).
4. `enableAutoSIPrefix(False)` on every axis (SWD-PM3-01). Fixed axis widths (58 px) so windows line up; tick font set once.
5. `setClipToView(False)`, `setDownsampling(auto=False)`: the backend hands over exactly the visible window as `px_width` min/max columns (`SeriesMinMax.lo/hi`, pyramid ×4/16/64/256). The GUI interleaves each pair into 2 points per pixel column. `connect="finite"` only on channels that can carry NaN (NO_DATA columns); `skipFiniteCheck=True` elsewhere.
6. **No `LegendItem`** inside the plot: colours appear in the channel tree and the title strip (SWD-PM3-02, also cheaper).
7. **X-Y view:** `data.xy(x_key, y_key, window_s, max_points=4000)` → `XYSnapshot` (stride decimation with per-stride extrema, B §7.5); one `PlotCurveItem` plus one single-point `ScatterPlotItem` (live marker from `data.latest()`). Explicit ranges with the same hysteresis.
8. **Skip work:** windows that are hidden (tabified behind another dock, minimised, closed) or frozen are not updated. The snapshot is requested only for the union of keys of visible windows.
9. **Timer:** one `QTimer` with `Qt.PreciseTimer`, 33 ms (SWD-PM3-06: a coarse timer fires every 46.9 ms on Windows). Readouts run every 3rd tick (10 Hz); link stats at 1 Hz. Indicators, banners and gates run every tick (§2.4).
10. **Lanes strip** for status bits: one `PlotCurveItem` per ticked bit with a constant offset (bit·1.2). The fixed Y range means no axis repaint.

**What we do not take over from Thrust_Stand (SWD-PM3-05 root causes → countermeasure):**
| TS cause (preM3 report) | Effect measured there | Our countermeasure |
|---|---|---|
| one `pg.PlotWidget` (own `QGraphicsView`) per pane, up to 8 panes per window | 6–13 ms paint per pane, cost scales with panes | fixed 1 view per window with ≤ 2 `PlotItem`s + optional lanes; max 4 windows; no free grid |
| lazy Y auto-range → second paint per refresh | fps 6 → 10 when auto-range was off | explicit computed ranges with hysteresis (rule 3) |
| absolute, scrolling time axis + SI prefix | axis picture regenerated every frame; "(x0.001)" misreadings | relative time axis, SI prefix off (rules 2, 4) |
| `CoarseTimer` 33 ms → 47 ms | 21 fps max in the default layout | `PreciseTimer` (rule 9) |
| overflow channels opened new panes (SWD-PM3-04) | 13 + 10 panes for 32 curves | third unit refused; the user opens another window |
| GIL convoy with CPU-busy backend threads (simulator, pipeline at 400 Hz) | 45 ms → 491 ms render with one busy thread | B sets `setswitchinterval(0.001)` + `timeBeginPeriod(1)` and keeps per-frame work ≤ 1 ms (B §16); perf runs use the out-of-process simulator `tcp://127.0.0.1:5770` (A-22). 80 Hz is 5× lighter than TS |
| GC finalising Qt objects in backend threads (SWD-PM3-07 deadlock) | process hang, STOP dead | GUI-thread GC policy + parented pyqtgraph menus (§9.4) |
| on-screen E-STOP served by a saturated event loop | 91–235 ms click latency | the tick budget keeps the loop ≤ 30 ms busy; the STOP handler is synchronous (`backend.stop` ≤ 5 ms, priority write B §4.5) and fires on press; the perf test measures press → wire (§10.3) |

**Measurement built into the GUI.** The refresh handler records frame intervals and per-stage durations in a ring buffer; *Tools ▸ Performance overlay* shows p50/p95 fps, paint ms and snapshot ms; the same counters are exposed to the perf test (`MainWindow.perf_stats()`). A perf smoke run is part of every milestone from M1 on (R3 §7.2 item 6).

---

**DEV-PC perf findings (F's `SW_perf_report_devpc.md`, P3) and GUI answers (2026-10-09).**
- *OBS-P3-02 (CPU / STOP latency with all channels).* Offscreen probe (in-process simulator, real clock, F's "all"
  layout: 39 channels, Plot 1 with 4 time panes + X-Y, floating Plot 2; recording, motion loop): GUI thread
  **0.715 → 0.685 cores** (−4 %) with the cheap wins below; refresh stages unchanged (plots p95 ≈ 8 ms, status
  ≈ 1.2 ms, readouts ≈ 0.3 ms). cProfile: path building + paint ≈ 23 % of the GUI thread, snapshot handover ≈ 10 %,
  the rest is Qt's own rendering of 12–13 viewports at 30 fps — it scales with the number of panes (the pane grid of
  D-38, as F notes). Cheap wins applied: (1) `FastCurve.set_xy` uses pyqtgraph's fast path (`connect="all"`,
  `skipFiniteCheck`) when the data has no NaN / inf (gaps still `connect="finite"`); (2) `PlotDock.is_shown()` is
  false while its window is minimised (no snapshot, no setData); (3) readouts touch a row's state colour only when
  the state changes. Hidden / tabified / frozen windows were already skipped (§4.6 rule 8). Larger reductions need a
  PO decision (OBS-P3-02/-03): a lighter "all channels" default (bits in one lane pane), a lower refresh for static
  lanes, OpenGL. The Pause/Break key (own thread) stays the robust stop path; the manual says so.
- *OBS-P3-04 (memory slope).* Object census in the same probe (GC-tracked objects by type every 60 s, 10 min after a
  2 min warm-up; tracemalloc itself was too slow — the backend's liveness monitor tripped): +17 000 objects / min,
  all **live** (a thaw + full collection at the end reclaimed 58 objects, so the GUI GC freeze-while-deferred policy
  is not the cause). Growth: ≈ 5 300 `io.reader.RxRecord` / min (the Reader's `rx_log` test-hook deque, always on,
  `maxlen = 100 000` → bounded, full after ≈ 19 min; B), ≈ 11 000 dicts / min from the **in-process** simulator's wire /
  sent-frame logs (test-side lists; not present with the out-of-process simulator or the board; B), and on the GUI
  side only the **Event log** (≈ 93 rows / min with a continuous motion loop: `LogRow` + 4 `QTableWidgetItem`s per
  row), bounded by its 5 000-row ring (§4.4, full after ≈ 54 min). No unbounded GUI structure found (plot items,
  timers, pane lists are constant).
- *OBS-P3-05 (GC pauses 320–476 ms at 100 % host CPU).* `gc.freeze()` after start-up is already applied
  (`GuiGcPolicy(freeze=True)`, §9.4); with the frozen start-up heap the timer full collections take ≈ 0.1 ms and the
  thaw 29–58 ms on an idle DEV PC. The 0.3–0.5 s pauses were measured with the host saturated: most likely host
  pre-emption during a collection, not a larger heap. No change; watch the `gc_policy` warnings in the REF soak.

### 4.7 Plot panes (SW-RT-006, D-38; inherited from Thrust_Stand SW-RT-004 / D-62 / D-63 @37c8747)

Since D-38 a plot window is a **grid of plot panes** instead of one fixed time view + X-Y slot (§4.1 is superseded
for the inner layout; the dock frame, STOP, float and NO-SPECIMEN tag stay).

```
+-- Plot 1 ----------------------------------------------- [NO-SPECIMEN] [Float] [Close] [## STOP ##] -+
| [☰ Channels] [Freeze] Window [30 s v] [x] Autoscale  [+ Pane] [+ X-Y pane]  Cols [1][2][3][4]  info  |
+--------------------------+---------------------------------------------------------------------------+
| Channel        Unit Pane | ⠿ Pane 1 · Raw counts            ✕ | ⠿ Pane 2 · Status bits            ✕ |
| v load                   |   raw ~~~~~~~~                       |   VALID  _|~~|__                     |
|  [x] HX711 raw counts P1 |                                      |   MOVING ___|~|_                     |
| v status.flags           +--------------------------------------+--------------------------------------+
|  [x] VALID          P2   | ⠿ Pane 3 · Travel                ✕ | ⠿ Pane 4 · X-Y: raw over setpoint ✕ |
|  …                       |   x [mm] (left) / setpoint [µm] (R)  |   x [setpoint v]  y [raw v]          |
+--------------------------+--------------------------------------+--------------------------------------+
```

| Topic | Design | Module |
|---|---|---|
| Pane | one `pg.PlotWidget` per pane (QWidget grid cell, needed for drag & drop) with title strip (grip, "Pane N · quantities" or the user name, ✕), optional legend strip, `PlotItem` + optional right `ViewBox`; ≤ 2 units per pane (left / right axis); status bits use the pseudo unit `bit` and are drawn as digital lanes (bit · 1.2, fixed range, generated names as ticks, §4.6 rule 10) | `plots/plot_pane.py` `PlotPane` |
| Grid | `PaneGrid`: 1/2/3/4 equal columns per window (column buttons), row-major; switching keeps the order; drop indicator while dragging | `plots/pane_grid.py` (copy) |
| Default placement (D-63 rules 1–5) | (1) a ticked channel joins the first pane (grid order) showing its **quantity group** with an axis for its unit; (2) else the first empty time pane is reused; else a new pane is appended; (3) unticking removes only the curve, emptied panes stay; (4) manual placement wins ("Plot in / move to pane N", "New pane", pane menu "Move curve", drag & drop); (5) a tree-group tick ticks only available children (greyed stay unticked, group partially checked) and costs one layout | `plots/plot_dock.py`, `widgets/channel_tree.py` |
| Quantity groups | from `ChannelSpec.dimension` when B adds it (**GRQ-B-21**), else key (status bits via the generated names), unit (N/kgf → Force, mm/µm → Travel, counts → Raw counts, SPS → Sample rate, …), registry group | `plots/quantity.py` |
| Reorder / move | drag a pane by its title strip onto another cell (insert there) or onto another plot window (moved with its curves: ticked there, unticked here; a channel already shown there is dropped with an info); pane menu "Move to window…" (incl. "New plot window"); a window always keeps one pane | `plot_dock.py`, `pane_grid.py` |
| Rename / close | double click on the title or pane menu "Rename…" (empty = automatic title); ✕ / "Close pane" unticks its channels; the last pane cannot be closed | `plot_pane.py` |
| X link | time panes of a window share X = [−window, 0] (relative axis, §4.6 rule 2); a user zoom / pan in one pane is applied to all; time-axis values only in the bottom pane of each column | `plot_dock.py` |
| X-Y pane | pane type `XYPane` ("+ X-Y pane"): x from the travel channels (mm / µm), y force (when available) else raw counts; `data.xy(x, y, window_s)` per refresh, curve + live point, own auto X/Y; never a default target, not time-linked | `plot_pane.py` `XYPane` |
| Autoscale | per pane and axis, explicit with hysteresis (`AutoRange`, §4.6 rule 3); "Autoscale" off = manual Y (mouse zoom / pan, SW-RT-003); a mouse zoom on Y pauses the autoscale of that pane | `plot_pane.py` |
| Data | the main window's refresh makes **one `data.snapshot(keys, window_s, px)` per distinct time-window length** for the union of the channels of all shown, non-frozen windows (px = widest pane / 2 px per column); each window hands the snapshot to all its panes, then commits the views (one paint per pane and refresh) | `main_window.py` `_on_plots` |
| Rendering (NFR-001) | Thrust_Stand plot-performance fix copied: `FastCurve` (cheap data hand-over and bounding rect), opaque `GridLines` from one item, cached `TimeAxis` tick values / static texts, Y axes as device pixmaps, `NoIndex` scenes, orphan pyqtgraph menus adopted (SWD-PM3-07), no `LegendItem` | `plot_pane.py` |
| Windows | up to 4 plot windows (GQ-04): View ▸ New plot window (tabified with Plot 1), every window with its own STOP | `main_window.py` |
| Persistence | QSettings `plots/layout` JSON `{"version": 1, "docks": [{"title", "columns", "window_s", "autoscale", "tree", "selected", "panes": [[keys…] \| {"type": "xy", "x", "y"}], "titles": [name \| null]}]}` + `main/state` (dock arrangement); saved on close and View ▸ Save layout now; restored at start; channels not (yet) in the registry wait and are placed when they appear; a corrupt entry gives the default layout | `main_window.py`, `plot_dock.py` |

### 4.8 Long windows on several plot windows — NFR-009 (D-54 b; SRS v0.6.7, Should)

**Target.** ≥ 20 fps (p95 frame interval ≤ 50 ms) with the 600 s window on 4 plot windows, all channels, 80 Hz,
recording on (TC-NFR-001-03 / TC-NFR-009-01, reference PC). GPU rendering allowed.

**Where the time went (DEV PC, 2026-10-10, host not quiet: 37–57 % load by other agents).** Plot 1 with all
39 channels in 11 time panes + 1 X-Y pane, 3 floating windows, 600 s:
- refresh "plots" stage 41–74 ms p95: most of it in `data.xy` for the X-Y pane — the GUI asked for 4000 points and
  the backend's decimation (B, `core/dataview.py` `xy`) loops in Python over the segments (≈ 2 numpy calls per
  segment, GIL held);
- paint: pyqtgraph path building (`arrayToQPath`) and Qt's raster per pane viewport, every tick, also for panes whose
  content did not change (status-bit lanes, slow channels: the time columns are fixed relative to the window end,
  §4.6 rule 2, so their data is often identical tick to tick).

**Design (implemented).**
| # | Measure | Module |
|---|---|---|
| 1 | **X-Y point budget** = 2 points per pixel column of the pane (400…4000): `render.xy_points(px)` → `data.xy(..., max_points=)` | `plots/render.py`, `plots/plot_dock.py` |
| 2 | **Skip unchanged curves**: `FastCurve.set_xy` returns early when x / y (NaN-aware) and the view range are identical — no geometry change, no repaint; a pane whose curves are all unchanged is not repainted. A curve that **did** change marks itself, and `PlotPane.commit_view()` then schedules one paint of the whole pane viewport (OBS-F-NFR9-01, below) | `plots/plot_pane.py` |
| 3 | **Fast path** for gap-free data (`connect="all"`, `skipFiniteCheck`), §4.6 / OBS-P3-02 | `plots/plot_pane.py` |
| 4 | **Render backend selectable**: View ▸ **OpenGL rendering** (`ui/opengl` in `gui.ini`, env `BEND_STAND_GUI_OPENGL` = 1 / 0 overrides; hook `plot_dock.set_opengl(on)` for the perf harness). OpenGL = pyqtgraph `GraphicsView.useOpenGL(True)` (a `QOpenGLWidget` viewport per pane, Qt's GL paint engine; no PyOpenGL). **Fallback**: a failing switch or a GL viewport without a valid context after the first show → raster for every pane, OpenGL marked failed with the reason, message, menu item unticked. **Default off** (row "GL" below) | `plots/render.py`, `main_window.py`, `plots/plot_pane.py` |

Already in place: decimation to pixel width for the time panes (backend pyramid, `px_width` min / max columns,
§4.6 rule 5), antialiasing off, cosmetic 1 px pens, hidden / minimised / frozen windows skipped, no legend items.
pyqtgraph's own downsampling / `clipToView` would add nothing on top of the backend's min / max columns.

**Measurements** (paint interval of the pane viewports; my probe `fps_probe` with the in-process simulator and F's
`perf_gui.py --plots all600|all --pr1-s 60`, out-of-process simulator, real display 1920 × 1040, raster unless
stated):
| Run | Layout | Before | After |
|---|---|---|---|
| probe, A/B X-Y budget (2 runs each) | 600 s × 4 | paint p50 86.6 / 131.9 ms (11.5 / 7.6 fps), plots p95 51 / 74 ms | 59.3 / 62.5 ms (16.9 / 16.0 fps), plots p95 18 / 23 ms |
| probe, A/B skip unchanged (2 runs each) | 600 s × 4 | refresh ticks 15.7 / 19.4 fps | 19.6 / 25.7 fps |
| F's harness | 600 s × 4 | changing panes 15.9 fps (p50 57 ms, p95 107 ms); tick p50 49.9 ms; plots p95 24.4 ms | changing panes 19.5 / 18.6 fps (p50 42–44 ms, p95 111–115 ms); tick p50 39.1 ms; plots p95 15.4 ms |
| F's harness | NFR-001 ("all", 30 s) | (F, quiet host: PASS, 23 % margin) | changing panes 22 fps (p50 38 ms, p95 84 ms) at 57 % host load |
| **GL**: probe `--gl viewport` | 600 s × 4 | raster 15 fps (p50 66.6 ms) | **3.1 fps** (p50 322 ms) |
| **GL**: probe `--gl viewport` | NFR-001 | raster 14.8 fps | **5.0 fps** (p50 202 ms) |
| GL: F's harness (F, TC-NFR-009-01) | 600 s × 4 | 9.6 fps | 1.8 fps |

**OBS-F-NFR9-01 (F, change → paint metric of SW_test_plan v0.5.5).** The `vstate` pane (Plot 1, pane 4: a constant
curve, value 0) had real content changes ≈ 3 / s (its NaN gap pattern) but repainted up to 1.9 s later (p95 576 ms,
real display only; offscreen 43 ms). Cause: the update covered only the curve item's own thin bounding rect (≈ 2 px
around y = 0); on the real display such an item-rect update did not reliably produce a paint of the pane until
something else in it changed. Fix (row 2): a changed curve flags itself and `commit_view()` calls
`viewport().update()` once for the pane. Result (F's harness, 600 s × 4, loaded host): P1.4 change → paint p95
**576 → 28 ms** (max 1943 → 42 ms); all panes p50 20 ms, p95 63 ms, worst pane p95 97 ms. Tests:
`test_render_nfr009.py::test_commit_view_schedules_viewport_update_only_when_changed`,
`::test_changed_thin_curve_repaints_pane_within_one_tick`.

**After B's vectorised `DataView.xy()`** (31.8 → 1.0 ms at 600 s × 80 Hz) and the fix above, F's harness, 600 s × 4,
host 25–82 % loaded by other agents' test runs: plots stage p95 15.1 ms, refresh tick p50 55.7 ms / p95 87.7 ms
(event-loop lateness p50 33 ms — the host load), change → paint p95 63 ms over all panes. **The quiet-host re-measure
is still open** (the DEV PC was not quiet during this round); the verdict stays with TC-NFR-009-01 on the reference PC.

**Conclusions.** (1) OpenGL through pyqtgraph's GL viewport is 3–5 × slower here (one GL-composited window per pane,
QPainter paths through the GL paint engine; pyqtgraph's experimental GL curves do not accept the GUI's fast curve
data hand-over) → **default raster**, OpenGL stays a setting with fallback for other GPUs / drivers.
(2) The 600 s × 4 layout reaches ≈ 19 fps for the changing panes on the loaded DEV PC; the p95 still exceeds 50 ms
under host load. The verdict belongs to the quiet reference-PC run (TC-NFR-009-01). (3) **Metric note for F:**
with measure 2 a pane whose content did not change is not repainted; its paint interval grows (status lanes:
3.5–6 paints / s), which is not a frame drop — measure the panes whose data changes, or the refresh tick.
(4) B vectorised the `xy` decimation (done, 2026-10-10). Optional request to B: anchor the time-pane columns to
absolute time so more columns stay identical between ticks.

## 5. Stop handling

### 5.1 Stop paths (end-to-end)
| Path | GUI element | Thread that sends | Backend call | Wire result | Latency target | Req |
|---|---|---|---|---|---|---|
| On-screen STOP | `StopButton` in toolbar, Manual tab, every dock title bar, every `SafeDialog` / `SafeMessageBox` / `SafeFileDialog`, every wizard, tare popup | **GUI thread, synchronous** (no executor, no signal hop) | `backend.stop(source)` → `StopResult`: non-blocking ≤ 5 ms, priority TX path (B §4.5), `terminate_all`; never raises | STOP | press → frame ≤ 50 ms p95 | SW-STOP-001, NFR-002, IF-011 |
| Pause/Break, Ctrl+Break | none (keyboard, system-wide) | **hotkey thread** of B (`io.win_hotkey.GlobalHaltHotkey`, no Qt) | `backend.halt("hotkey")`: HALT, ≤ 20 tries in 1 s until ACK or HALT flag; result events `stop.confirmed` / `stop.unconfirmed` (A-23) | HALT | key → frame ≤ 50 ms p95 | SW-STOP-002, NFR-003 |
| Hotkey fallback (`HotkeyStatus.mode` UNAVAILABLE) | app-wide `QShortcut(Pause)`, `QShortcut(Ctrl+Break)` with `Qt.ApplicationShortcut` | GUI thread | `backend.halt("app-shortcut")` | HALT | as above, app focused only | SW-STOP-002 (degraded) |
| Pause | toolbar Pause, Sequence [Pause] | GUI thread | `backend.pause(source) → StopResult` (A-01) | PAUSE 0x3B (priority path, CONFIRM) | press → frame ≤ 50 ms p95 (same path as STOP) | SW-STOP-004, IF-011 |
| Resume | toolbar Resume, Sequence [Resume], Manual PAUSED line | GUI thread | `backend.resume(source) → GateResult` (A-02) | RESUME 0x3C (D-31), then re-issue (sequence) | – | SW-STOP-004 |
| Physical STOP/BREAK, PAUSE, E-stop, limits, FW load limit, driver power, K1 | none (FW) | – | backend reacts to DATA/EVENT (B §5.5.1) | – | display ≤ 200 ms | SW-STOP-003, SAF-SW-005 |
| SW limits, link loss, recording failure | none (backend) | backend | backend sends STOP itself | STOP | violating frame → STOP ≤ 50 ms | SAF-SW-001, SAF-SW-003, SW-ACQ-004 |

### 5.2 `StopButton` widget (copied from TS `gui/widgets/estop_button.py` + `gui/estop.py`, origin note, renamed)
- `QPushButton("STOP")`, object name `stopButton`, red style, `setFocusPolicy(Qt.NoFocus)`, `setAutoDefault(False)`, `setDefault(False)`. It is never the escape button of a dialog. Size variants `large` (toolbar, Manual tab) and `compact` (docks, dialogs).
- The press goes to the process-wide dispatcher `gui/stop.py: trigger_stop(source)`, which calls the installed handler **inside a `try`** (a STOP press never raises into Qt). It keeps a history of `(monotonic_ns, source)` for tests and the log.
- Handler (installed by `MainWindow`): `result = backend.stop(source)` → stop banner (§2.3) with the **real** `StopResult` (sent / reason; TS SWD-M1-05: never show "sent" when it was not). B §15.4 rule 7.
- Reacts on `pressed`, not `clicked` (GQ-20, decided). This saves the press-to-release time (≈ 80–150 ms of a normal click) toward the 50 ms NFR-002 budget. A press that the user drags off the button still stops (fail-safe).
- Tooltip: "STOP: immediate stop, driver keeps holding, sequence terminated. Keyboard: Pause/Break (HALT, latched)."

### 5.3 Global Pause/Break hotkey (SW-STOP-002, NFR-003, KL-01)
- **Backend part (B §17).** `io.win_hotkey.GlobalHaltHotkey` (copied from TS `io/win_hotkey.py`) is a daemon thread with its own Win32 message loop and no Qt: `RegisterHotKey(NULL, …, MOD_NOREPEAT | mods, VK_PAUSE)` for none/Shift/Alt/Win plus `MOD_CONTROL + VK_CANCEL` (Ctrl+Break); a `WH_KEYBOARD_LL` fallback that only posts a message; a 250 ms liveness ping. The callback `backend.halt("hotkey")` runs **on the hotkey thread**, so a frozen GUI does not disable the key. `io/elevation.py` warns when the application itself runs elevated.
- **GUI part:**
  - the KEY chip shows `status().hotkey: HotkeyStatus(mode REGISTERED / LL_HOOK / UNAVAILABLE, reason, test_running)` (A-04). Its tooltip, Help ▸ Keyboard and the hotkey test dialog always state **KL-01** (accepted known limitation, D-32 Q28): "The Pause/Break key is not delivered while an elevated (Administrator) window has focus. The physical STOP/BREAK button and the E-stop are the paths of record." When the app itself runs elevated, the chip turns amber with the elevation warning;
  - **Tools ▸ Test Pause/Break key…** opens `HotkeyTestDialog(SafeDialog)`: gate `hotkey_test` (REFUSE: moving, operation, unavailable) → `backend.hotkey_test_start(timeout_s=10.0) → GateResult`; the dialog shows "Press Pause/Break now (10 s)…", then the `hotkey.test` result (measured delay in ms, or timeout). During the window every motion gate refuses ("Pause/Break key test running") and the key does not send HALT (B §15.6); the dialog says so;
  - when the key is UNAVAILABLE the GUI installs the application-level `QShortcut` fallback (§5.1) and the KEY chip turns red;
  - with no link, `halt()` returns "not sent" and the banner says so.
- The GUI never registers a `QShortcut` for Pause while the global key is REGISTERED or LL_HOOK; a double path would only create confusing double HALTs. No extra in-app STOP key exists (GQ-19, decided).

### 5.4 Dialog infrastructure (STOP in every dialog, SW-STOP-001)
- `SafeDialog(QDialog)`: a top bar with the hint "Pause/Break = HALT", the NO-SPECIMEN tag while that mode is on (§2.8) and a compact `StopButton`; the content area below (TS `gui/dialogs/safe_dialog.py:47-78`). **All** application dialogs derive from it.
- `SafeMessageBox`: adds `StopButton` (`ActionRole`). A STOP press closes the box with "no answer" (`NoButton`), so no confirmed action follows. Used for non-safety messages only (GF-10).
- `SafeFileDialog` and the helpers `get_open_file_name` / `get_save_file_name`: non-native dialogs. `AA_DontUseNativeDialogs` is set by `gui.app.run()` before `QApplication` exists (SW-STOP-001 v0.3), so file dialogs carry STOP.
- `SafeWizard` (§6.1), `TarePopup` (§6.4) and `HotkeyTestDialog` (§5.3) also derive from `SafeDialog`.
- **Modality:** confirmations and message boxes are application-modal (short-lived) and carry STOP, and the global key keeps working; wizards, the tare popup, link statistics and the hotkey test are **non-modal**, so the toolbar STOP, the plot windows and the readouts stay usable (GQ-10, decided).

### 5.5 Confirmation dialogs (SAF-SW-004: Enter/Space never confirm, STOP reachable)
`ConfirmDialog(SafeDialog)` rules:
- **No default button** (`setDefault(False)`/`setAutoDefault(False)` on all buttons).
- The confirm button has `Qt.NoFocus` and no mnemonic.
- An event filter on the dialog swallows `Key_Return`, `Key_Enter` and `Key_Space` (they never reach a button). `Esc` = Cancel.
- Initial focus is on Cancel. Confirming therefore needs a **mouse click** on the confirm button.
- Where the SRS asks the operator to assert a fact, a checkbox must be ticked first. The confirm button stays disabled until it is ticked. Ticking by keyboard is allowed and harmless.
- The dialog shows the backend's CONFIRM text (`GateItem.text`, or `ConfirmRequest` from an engine) and **live values** (force, homed state, threshold state). It re-reads the gate every 0.5 s (TS ARM-dialog pattern). If the CONFIRM item disappears, the dialog closes as "not needed". If a REFUSE item appears, it closes with that text.

| ID | Situation (CONFIRM source in B) | Text (summary; final text = backend's) | Assertion checkbox | On confirm | Req |
|---|---|---|---|---|---|
| C-01 | `home` gate CONFIRM: abs(F) ≥ 5 % FS **or** unknown load (B §5.6) | "Load on the specimen: 132.4 N (6.8 % FS > 5 %) / load unknown. Homing moves the axis to the START switch while loaded." | "I accept homing under load" | `motion.home(load_confirmed=True)` | SAF-SW-004, SAF-FW-021, D-15 |
| C-02 | `disable` gate CONFIRM | "Specimen unloaded? Disabling removes holding torque; the specimen may spring back; the axis will be NOT homed." | "Specimen is unloaded" | `motion.disable(confirmed=True)` | SAF-SW-004 |
| C-03 | `estop_clear` gate CONFIRM | "Clear E-STOP: button released (input closed ≥ 100 ms) and the area safe? (D-41: no K1 reset) After clearing, the driver stays disabled: ENABLE and HOME are required. No motion restarts." | "E-stop button released, K1 reset, area safe" | `estop_clear_async(confirmed=True)` | SAF-SW-004, SAF-FW-006 |
| C-04 | Restore board defaults (GUI-side caution) | "Restore all parameters to defaults (RAM; NVM unchanged until Save). Session load thresholds are re-sent by the PC." | – | `config.defaults_async()` | SW-CFG-004 |
| C-05 | `travel_cal` `needs_confirmation` (> 20 %: shows the DIP-derived values 800 steps/mm = 4000 p/rev closed loop (current setting, D-27 closed) and 160 steps/mm = 800 p/rev "DIP change not applied"; > 5 %; outside ±20 % of the expected 800) | backend text incl. old → new value and candidates | "Measured value checked" | `travel_cal.continue_(inputs, confirmed=True)` | SW-CAL-003 |
| C-06 | `load_cal` `needs_confirmation` for a WARN fit (A-15) | NL_span value and residuals | "I accept the WARN linearity" | `load_cal.continue_(confirmed=True)` | SW-CAL-007 |
| C-07 | `sequence_start` gate CONFIRM / WARN items (HOME steps under load, Pause/Break key unavailable, travel-only sequence in no-specimen mode, travel calibration differs; WARN margin, LOW_SPAN, extrapolation) | the item list | – | `sequencer.start(seq, confirmed=True)` (B3-20) | SAF-SW-006, SW-SEQ-005 |
| C-08 | Discard an unsaved sequence / marks preset; close a wizard past its first phase (GUI) | "Discard changes?" / "Cancel calibration?" | – | local / `engine.cancel()` | SW-SEQF-001, SW-CAL-001 |
| C-09 | Close the application while moving / sequence / recording / wizard (GUI, from `status()`) | "Closing sends STOP, ends recording and leaves the driver enabled (holding)." | – | §5.8 | SW-STOP-001 |
| **C-10** | `no_specimen` gate CONFIRM (B §6.7, A-09) | "No specimen is mounted. The PC load limits are switched OFF for this session. The board load limit stays active (<calibrated / nominal default ±7 022 271 counts ≈ ±109 % FS>). Mount no specimen until a load calibration and a tare exist." + live: F (if computable), threshold state | "No specimen is mounted" | `limits.set_no_specimen_mode(True, confirmed=True)` | SW-LIM-004, SAF-SW-004 |
| **C-11** | Save & reboot the board (GUI-side caution; gate `config_write`) | "Save all parameters to NVM and reboot the board? The stream restarts, the axis is NOT homed afterwards, the driver keeps holding (D-13)." | – | `config.save_async()` → `config.reboot_async()` | SW-CFG-003 |
| **C-12** | engine `start()` returns CONFIRM items (`cal_travel_start`: "no specimen mounted" when the load is unknown; CFG_DIRTY would be persisted by ACCEPT) | the item texts | "No specimen is mounted" (when that item is present) | `engine.start(..., confirmed=True)` (generic rule B §5.6, confirmed by B3-20) | SW-CAL-002, SAF-SW-004 |
| **C-13** | `clear_stop` gate CONFIRM while a sequence is PAUSED (B3-02) | "Clear stop ends the paused sequence (STOPPED, reason CLEARED) and clears HALT and PAUSE. No motion restarts. To continue the sequence use Resume instead." | – | `clear_stop_async(confirmed=True)` | SW-STOP-003, SW-STOP-004 |
| **C-14** | `load_cal` FIT `needs_confirmation.code == "K_IMPLAUSIBLE"` (D-50 a, SRS v0.6.5): \|K\| outside 0.5…2 × the nominal K of the configured cell / AFE; a WARN fit prefixes the non-linearity sentence (a WARN fit alone stays C-06) | engine text: "K implausible: \|K\| = … N/count is … × the nominal … N/count of the configured cell / AFE (expected 0.5…2 ×) — check the weights, the cell and the AFE gain. Accept anyway?" | "I checked the weights, the cell and the AFE gain" | `load_cal.continue_(confirmed=True)` | SW-CAL-007, SAF-SW-004 |
| **C-15** | topic `device.board_changed` (payload UID; B6-33 (6), SWR-19): another board after a reconnect, the backend reset the test travel zero | "A different board is connected (UID …). The test travel zero was reset to the machine zero. Check the SW travel limits, the travel and load calibration and tare again before the next test." — an **acknowledgement**: [Close] / [Acknowledge] only close it; a further event updates the open dialog | – | nothing (no backend call) | SAF-SW-005, SW-LIM-001, SAF-SW-004 |

One dialog class serves all fifteen cases, so one GUI test covers the keyboard rules for all of them (G-04).

**Pending engine confirmations (wizards).** The dialog for an engine `ConfirmRequest` is chosen by its `code` (`SafeWizard.CONFIRM_CIDS`, e.g. `K_IMPLAUSIBLE` → C-14; default C-05 travel / C-06 load). It opens automatically once per request. If the operator closes it unconfirmed, the wizard's Continue button **re-opens the same confirmation** (or raises it if still open) instead of calling a plain `continue_()`; only the dialog's confirm button sends `continue_(…, confirmed=True)` (SAF-SW-004, SW-CAL-007).

### 5.6 Clear stop (SW-STOP-003)
`ClearStopDialog(SafeDialog)` lists the latched items (generated names, `*_DESC`, `source`, time, `clear_hint`) and one row per clear command. Each row is driven by its precomputed gate (B §5.6); REFUSE texts are the waiting condition (e.g. "release the STOP/BREAK button", "E-stop input open"):

| Row | Gate | Button | Call |
|---|---|---|---|
| HALT / PAUSED | `clear_stop` | [Clear HALT] (label "Clear HALT + PAUSE" when both are latched; "Clear PAUSE" when only a manual PAUSED is latched) | `clear_stop_async()` (HALT_CLEAR — clears HALT **and** PAUSED, D-31; no motion) |
| ↳ sequence PAUSED | `clear_stop` CONFIRM "ends the paused sequence" (B3-02) | [Clear HALT + PAUSE] opens C-13; the row also offers [Resume] (continue the sequence instead) | C-13 → `clear_stop_async(confirmed=True)` (sequence STOPPED, reason CLEARED, then HALT_CLEAR) / `backend.resume("clear-dialog")` |
| E-STOP | `estop_clear` (CONFIRM) | [Clear E-STOP] + C-03 checkbox in the dialog | `estop_clear_async(confirmed=True)` |
| Faults (iterating `FAULTS_BITS`: LOAD_LIMIT, AFE_FAULT, STEP_FAULT, LIMIT_WIRING, HOME_NOT_FOUND, HOME_WIRING, K1_WELDED, HOME_DRIFT) | `fault_clear` (WARN per fault whose cause is still present) | [Clear faults] | `fault_clear_async()` → `ClearResult.cleared` names; a NACK detail names the remaining causes (FW-CMD-003) |

- All buttons follow the ConfirmDialog rules (no default, `NoFocus`). Results (`ClearResult`) are shown per row.
- **No motion restarts after a clear** (B §5.5). After an E-STOP clear the dialog ends with "ENABLE the driver and HOME the axis (Manual tab)" and [Go to Manual tab]. After an E-STOP clear a PAUSED latch can remain (ESTOP_CLEAR does not clear it); the PAUSED row then stays with [Resume] (no motion in manual mode).

### 5.7 Behaviour on FW / backend stop events
The backend terminates sequences, wizards and tares (SW-STOP-003, SAF-SW-001/003, B §5.5.1). The GUI **reflects** that on the next refresh tick (≤ 33 ms after the backend state change):

| Event / state | Banner | Indicators | Manual tab | Wizard window | Tare popup | Sequence tab | Plots / recording |
|---|---|---|---|---|---|---|---|
| STOP sent (GUI) | grey "STOP sent" (10 s) | – | jog released; slider snaps to the commanded target | engine → `ABORTED` (`abort_reason` STOP): reason + [Close] / [Restart wizard] | "Aborted by STOP" | ABORTED (STOP) | event row; recording continues |
| HALT (key / button / PC) | red, with source + clear procedure | HALT red | motion controls greyed (gate) | `ABORTED` (HALT <src>), only [Close] / [Restart wizard] | aborted | ABORTED (HALT <src>) | event row |
| ESTOP | red, "position lost" | ESTOP red, HOMED red, ENA red, DRV red | greyed; HOME/ENABLE need Clear first | `ABORTED`; travel wizard → `RESTORING` when the board is reachable, else the restore stays pending (TCAL notice) | aborted | ABORTED (ESTOP) | event row |
| Driver power lost (DRV_PWR 0) | red "driver power lost" | DRV red, HOMED red | greyed; hint "Enable, then Home" when power returns | `ABORTED` (DRV_POWER_LOST); travel restore as above | aborted | ABORTED | event row |
| K1_WELDED | red with `clear_hint` | K1 red, FAULT red | greyed | aborted | aborted | ABORTED | event row |
| PAUSED (button / GUI) | amber + source + [Resume] / "Clear stop first" | PAUSED amber (source) | manual: controlled stop; all motion controls greyed with "Resume clears PAUSE" | `ABORTED` (`abort_reason` PAUSE; wizards are not resumable, A-15); page text "Resume (toolbar) clears the pause; then [Restart wizard]" | aborted | PAUSED (source), [Resume] enabled by the gate | VALID 0 during the pause |
| RESUME_REQUEST (PAUSE button while PAUSED) | amber banner hides when PAUSED clears | PAUSED clears on resume | – | – | – | backend resumes if the `resume` gate is open; toast "Resumed by the PAUSE button" or, from topic `resume.ignored` (B3-17), "Resume request ignored: <reason items>" | – |
| LIMIT_x | red, "jog away from the switch" | LIM red | jog toward the switch greyed, away allowed (gate) | aborted | aborted | ABORTED (LIMIT) | event row |
| LOAD_LIMIT (FW) / SW trip | red with value | LOAD red | greyed until Clear (FAULT_CLEAR) | aborted | aborted | ABORTED | event row |
| HOME_DRIFT | red "home switch moved by n µm" | FAULT red, HOMED | greyed until Fault clear | aborted | – | ABORTED | event row |
| AFE stale / saturated | red | AFE red | greyed (FW refuses) | capture → invalid point | refused / aborted | ABORTED (if moving) | curves drawn in the invalid style |
| LINK_WDG / LINK LOST | red "LINK LOST" | LINK red, WDG | all greyed | aborted | aborted | ABORTED (LINK) | gap in plots; recording counts the gap; no-specimen mode ends (toast) |
| ALM (D-16, D-28, D-33 c) | amber "Driver alarm – new motion starts blocked while the driver is powered; a running move continues (check the driver)" | ALM amber/red | new motion greyed (gate) | warning line | – | running sequence: SW controlled STOP, sequence ends with reason DRIVER_ALARM (D-33 c, B3-06); start refused | event row |
| CLK_FALLBACK | – | CLK amber | – | – | – | – | event row |
| Recording failure | red | REC red | – | – | – | backend: controlled stop of the sequence | – |

### 5.8 Close / exit
- `closeEvent`: if `status()` reports moving, an operation (sequence, wizard, tare) or recording → confirmation C-09.
- Then `backend.shutdown()` (STOP if moving, stop recording, disconnect, join threads); `gui.app.run()` returns the exit code. The driver keeps holding (D-13). The no-specimen mode ends with the application (SW-LIM-004).
- Then the GUI saves the layout and restores the stop handler.
- The GUI never sends DISABLE on exit (load could drop, D-13; GQ-14, decided).

### 5.9 Pause / Resume flow (SW-STOP-004, D-29 a, D-30, D-31)

```
                     Pause: backend.pause(src) -> PAUSE 0x3B          (physical PAUSE button: same FW behaviour)
  RUNNING / IDLE ------------------------------------------------> PAUSED  (FW latch: every new motion start refused,
       ^                                                             |      VALID cleared; chip "PAUSED <src>")
       |   Resume: backend.resume(src) -> RESUME 0x3C (clears        |
       |   only PAUSED) -> sequence: re-issue the interrupted        |-- STOP / HALT / ESTOP / fault / link loss
       |   step's absolute target (approach + trim); manual: no      |     -> sequence ABORTED (PAUSED may stay latched)
       |   motion                                                    |-- Stop (controlled) -> sequence STOPPED
       +-------------------------------------------------------------+-- Clear stop (HALT_CLEAR): clears HALT + PAUSED,
                                                                     |     paused sequence: CONFIRM C-13, ends it (CLEARED)
                       resume gate REFUSE (HALT / ESTOP / FAULT) ----+-- GUI: "Clear stop first", [Clear stop...]
```

| Step | GUI behaviour | Backend / FW |
|---|---|---|
| Pause pressed (toolbar / Sequence) | `r = backend.pause(src)`; banner "PAUSE sent" or "PAUSE NOT SENT: <reason>"; `stop.unconfirmed` (kind PAUSE) → red "PAUSE NOT CONFIRMED – use the physical STOP/BREAK". The button text does **not** change until `indicators.paused` is ON (P4) | PAUSE 0x3B on the priority path (CONFIRM class); `pause_all`: jog session and pending target dropped first; sequence → PAUSED; wizards/tare → ABORTED |
| PAUSED shown | chip "PAUSED PC/btn"; banner with [Resume] (+ [Stop sequence]); Manual controls greyed by the gates' PAUSED item; Sequence state PAUSED with `paused_source` | FW blocks motion (BLOCK PAUSED); VALID 0 |
| Resume pressed (toolbar / Sequence / Manual line / Clear-stop dialog) | `g = backend.resume(src)`; REFUSE items → toast + banner line, button stays; empty → "Resume sent"; Sequence shows "resuming…" until `SeqStatus.state` leaves PAUSED or its `message` gives the reason | RESUME 0x3C (D-31, B3-01): clears only PAUSED; sequence: after the ACK re-issue + capture restarted; manual: nothing else. Not executed → `resume.ignored` (B3-17) toast |
| Resume refused because HALT, ESTOP or a fault is latched | Resume disabled; tooltip and PAUSED banner line **"Clear stop first: <item text>"** with [Clear stop…] (display mapping on `BLOCK_BITS` codes, §2.7). For a sequence this state cannot persist (a HALT terminates it); in manual mode Clear stop (HALT_CLEAR) clears HALT **and** PAUSED, so no Resume is needed afterwards — the dialog says "Clear stop also clears the pause; no motion restarts" | `resume` gate REFUSE; the FW would answer E_STATE with the BLOCK mask anyway |
| Physical PAUSE pressed while PAUSED | toast from `fw.event` RESUME_REQUEST; the result is visible as PAUSED clearing, or as toast "Resume request ignored: <reason>" from `resume.ignored` (B3-17) | backend calls `resume(source="button")` if the gate is open (B §5.5.1) |
| Clear stop while a sequence is PAUSED | C-13 "ends the paused sequence"; confirm → sequence STOPPED (CLEARED), then HALT_CLEAR; [Resume] offered as the alternative | `clear_stop` CONFIRM, `clear_stop_async(confirmed=True)` (B3-02) |
| Any stop while PAUSED | normal stop handling (§5.7); the sequence ends ABORTED; PAUSED stays latched unless HALT_CLEAR was used | – |

---

## 6. Wizards (SW-CAL-001…009, SW-TARE-001…003, SW-WIZ-001/002)

### 6.1 Common wizard frame (`SafeWizard`) on the backend engine protocol (B §9.1, A-15)
A custom `SafeDialog` with a `QStackedWidget`. It is **not** a `QWizard`: QWizard makes "Next" the default button, so Enter would advance and could start motion (P3).

```
+-- Travel calibration - step 4 of 8: MOVE1 ------------------------ [NO-SPECIMEN] [## STOP ##] -+
| Pause/Break = HALT                                                                              |
+--------------------------------------------------------------------------------------------------+
| CHECK > BACKLASH > REFERENCE > [MOVE1] > ENTER_D1 > MOVE2 > ENTER_DTOT > RESULT    <- engine.PHASES |
+--------------------------------------------------------------------------------------------------+
| <EngineState.title>                                                                              |
| <EngineState.instruction: what the operator does, what the stand will do>                        |
| <inputs generated from EngineState.inputs (InputSpec: label, unit, range, decimals, default)>    |
| <phase-specific view: checklist / live stats / fit plot + table / result table>                  |
| live:  x 102.345 mm   target 112.345 mm   F 0.4 N   state MOVE_ABS                               |
| [##############--------------]  64 %                        <- EngineState.progress              |
| messages: (x) EngineState.errors  (!) EngineState.warnings   - verbatim                          |
+--------------------------------------------------------------------------------------------------+
| [Cancel]                                                  [Repeat]   [ Move 10 mm ▶ ]            |
|                                                           (continue_label; mouse-only: continue_moves) |
+--------------------------------------------------------------------------------------------------+
```

**Rules.**
- **Renders the engine.** The wizard renders `engine.state()` → `EngineState(kind, phase, step_index, step_count, title, instruction, inputs, progress, stats, result, warnings, errors, needs_confirmation, can_continue, can_repeat, can_cancel, continue_label, continue_moves, abort_reason)`. It refreshes on the engine's `subscribe` callback (via the bridge) and on each tick for progress.
- **Step strip.** Built from the engine class's `PHASES` tuple (A-15), excluding the terminal phases (`ABORTED`, `RESTORING`, `DONE`, `REFUSED`), which are shown as page states. The GUI keeps only a display table phase → view widget (`wizards/phase_views.py`); G-26/G-27 assert that every name in `PHASES` has a view.
- **Engine owns the logic.** State machine, preconditions, computations, plausibility checks, board writes, restore and texts are the engine's.
- **Buttons map to engine calls:** [Continue] → `engine.continue_(inputs, confirmed=False)`; [Repeat] → `engine.repeat()`; [Cancel] → `engine.cancel()`; enable states from `can_*`. There is no Back button: the engine has no backward transition.
- **Keyboard (P3).** No button is default or auto-default. The Continue button text is `continue_label`; when `continue_moves` is True it is `NoFocus` (mouse-only) and shows "▶" (B §15.4 rule 11). Enter in an input field only commits the field.
- **Start.** `engine.start(**config) → GateResult` (A-15): REFUSE → shown on the first page; CONFIRM → C-12 → `start(..., confirmed=True)`.
- **Confirmations.** `needs_confirmation` (`ConfirmRequest`) → `ConfirmDialog` (C-05 / C-06) → `continue_(inputs, confirmed=True)`.
- **STOP and aborts.** STOP (top bar) → `backend.stop("wizard:<kind>")`. STOP, HALT, ESTOP, PAUSE, latches, driver power loss and link loss bring the engine to phase `ABORTED` with `abort_reason` (B §9.1; PAUSE terminates like STOP, A-15). The page shows the reason and offers only [Close] and [Restart wizard] (`engine.start()` again; refused by the gate while e.g. PAUSED, with the text "Resume clears PAUSE").
- **Cancel or any abort leaves the active calibration unchanged** (SW-CAL-001). For the travel wizard the engine runs `RESTORING` (§6.2); the wizard shows its progress and result ("steps/mm restored to 800.000 ✓" or "restore pending – see the TCAL notice").
- **Window.** Non-modal, always on top of the main window (`Qt.Tool`); the plots and the toolbar stay usable (GQ-10). One operation at a time (B §3.5). Closing the window = Cancel; past the first phase it asks C-08 "Cancel calibration?".

### 6.2 Travel calibration wizard (`travel_cal`, B §9.3, §9.3.1; SW-CAL-001…004, R4 §5, D-27)

| # | Engine phase | Shown (texts from the engine; summary) | Operator input (`InputSpec`) | Continue (`continue_label`) | Exit / next | STOP / cancel |
|---|---|---|---|---|---|---|
| 1 | `CHECK` | Gate `cal_travel_start` result as a checklist: connected, enabled, homed, no latch, not PAUSED, driver powered, no specimen (abs(F) < 2 % FS if calibrated; else CONFIRM C-12 or no-specimen mode), room for 2 + 10 + 50 mm in + direction. Shows board / active steps/mm and the expected value **800 steps/mm** (4000 p/rev closed loop, D-27 closed; session `expected_spm`). The value 160 steps/mm appears only in C-05 as the candidate "DIP change not applied". REFUSE "load input invalid" → [Enter no-specimen mode…] (§2.8). CFG_DIRTY → CONFIRM "ACCEPT would also save the other unsaved parameters" | – | [Start] → `start()` (→ C-12 → `start(confirmed=True)`) | → 2 | Cancel: close |
| 2 | `BACKLASH` | "The axis moves +2 mm (backlash take-up) at 2 mm/s." live progress | – | "Move +2 mm ▶" (mouse-only) | MOVE_DONE → 3 | ABORTED → `RESTORING` (nothing to restore yet) → [Close] / [Restart] |
| 3 | `REFERENCE` | "Zero your caliper / dial gauge now, or mark the carriage. All further moves go in the same direction." | – | "Reference set, continue" | → 4 | Cancel |
| 4 | `MOVE1` | "The axis moves exactly N1 = round(10·spm0) steps (≈ 10 mm)." live x / target / progress | – | "Move 10 mm ▶" (mouse-only) | MOVE_DONE → 5 | ABORTED (measurement invalid) |
| 5 | `ENTER_D1` | "Measure the distance from your reference." After entry: spm1 = N1_actual/D1, change in %; plausibility errors/warnings; C-05 when required; then SET (board RAM only) + read-back ("board ✓ 796.020") | D1 [mm, 3 decimals] | "Apply D1" → `continue_({"D1": v})` (→ C-05 → `continue_(…, confirmed=True)`) | verified → 6 | [Repeat] = `repeat()`; Cancel → `RESTORING` (spm0) |
| 6 | `MOVE2` | "The axis moves N2 = round(50·spm1) steps further (≈ 50 mm)." | – | "Move 50 mm ▶" (mouse-only) | MOVE_DONE → 7 | ABORTED → `RESTORING` |
| 7 | `ENTER_DTOT` | "Measure the **total** distance from your reference (nominal 60 mm)." After entry: spm2, incremental value N2/(D_tot − D1), consistency warning "repeat" if > 0.5 % | D_tot [mm] | "Apply D_tot" → `continue_({"D_tot": v})` | → 8 | as 5 |
| 8 | `RESULT` → `ACCEPT` → `DONE` | Table spm0 → spm1 → spm2 (3 decimals, % change), D1, D_tot, N1, N2, warnings. Accept = SET spm2 + read-back + SAVE (NVM) + `travel_<UTC>.json` + `active_travel.json`, restore-pending record deleted | – | "Accept & save" → `continue_()` | done → summary on the Calibration tab | Cancel → `RESTORING` |
| – | `RESTORING` | "Restoring steps/mm to spm0 = 800.000 …" → "✓ restored (read-back)" or "✗ restore failed: <reason> – the board keeps 796.020 until restored; see the TCAL notice" (§2.8). After a link loss the restore stays pending and the backend retries at reconnect (B §9.3.1) | – | [Close] | – | – |

```
Phase CHECK                                         Phase ENTER_D1
+---------------------------------------------+    +---------------------------------------------+
| [v] connected      [v] driver enabled       |    | N1 = 8 000 steps (10 mm at 800.000)         |
| [v] homed          [v] no stop/fault latch  |    | Measured distance D1 [  10.050 ] mm         |
| [x] load < 2 % FS (now 3.1 %)               |    | -> steps/mm 796.020  (-0.50 %)              |
| [v] room 62 mm (x 102.3 -> 164.3 <= 250.0)  |    | (i) change within 5 %: no confirmation      |
| steps/mm board 800.000  active 800.000      |    | [Apply D1]        board: ✓ 796.020          |
| expected 800.000 (4000 p/rev, D-27)         |    |                                             |
+---------------------------------------------+    +---------------------------------------------+
Phases BACKLASH / MOVE1 / MOVE2                     Phase ENTER_DTOT
+---------------------------------------------+    +---------------------------------------------+
| [Move 10 mm ▶]   (mouse only)               |    | N1 + N2 = 8 000 + 39 801 steps              |
| x 108.112 -> target 114.300 mm              |    | Total distance D_tot [  60.120 ] mm         |
| [##############--------]  62 %   MOVE_ABS   |    | -> steps/mm 795.093 (-0.12 % vs spm1)       |
| (ABORTED: STOP (toolbar) - measurement      |    | incremental 794.907 vs 796.020 (0.14 %) ok  |
|  invalid. Restoring 800.000 ... ✓)          |    | [Apply D_tot]                               |
| [Close] [Restart wizard]                    |    |                                             |
+---------------------------------------------+    +---------------------------------------------+
Phase REFERENCE                                     Phase RESULT
+---------------------------------------------+    +---------------------------------------------+
| Zero your caliper / dial gauge now, or mark |    | spm0 800.000 -> spm1 796.020 -> spm2 795.093 |
| the carriage. All further moves go in the   |    | D1 10.050  D_tot 60.120  N1 8000  N2 39801  |
| same direction.                             |    | Accept writes the board, saves NVM and       |
| [Reference set, continue]                   |    | travel_<UTC>.json.         [Accept & save]   |
+---------------------------------------------+    +---------------------------------------------+
```

### 6.3 Load calibration wizard (`load_cal`, B §9.4; SW-CAL-005…009, R4 §6, D-22)

| # | Engine phase | Shown (summary) | Operator input | Continue (`continue_label`) | Exit / next | STOP / cancel / invalid |
|---|---|---|---|---|---|---|
| 1 | `CONFIG` | Gate `cal_load_start` checklist: connected, AFE ok (not synthetic, not stale), no other operation; AFE config that will be recorded (A/128, 80 SPS); the engine starts the stream if needed and restores it. **Info box (D-22):** "Weights 1 kg + 10 kg cover only 5 % FS → **LOW_SPAN**; forces above 294 N will be shown as *extrapolated*." WARN "active calibration will be replaced on accept". Allowed in no-specimen mode (SW-CAL-005) | presettle [2.0] s, capture [10.0] s, masses default 1.000 / 10.000 kg (session) | "Start" → `start(presettle_s=…, capture_s=…, masses=…) → GateResult` | → 2 | Cancel: close |
| 2 | `AWAIT_OPERATOR` (point 0) | "Remove all load from the cell and the hook. Do not touch the stand." | – | "Capture zero point ▶" → `continue_()` | → 3 | Cancel |
| 3 | `PRESETTLE` → `CAPTURE` (shared) | Pre-settle countdown, then the capture progress bar (`progress`), live `stats` (N, mean, std, drift, rejected, lost) at 5 Hz, stability LED | – | – (Cancel only) | → 4 | STOP / HALT / PAUSE / fault → `ABORTED` (point discarded) |
| 4 | `EVALUATE` (shared) | result: accepted ✓ or rejected ✗ with reasons (saturated sample; > 2 % outliers; < 95 % nominal; drift > max(2·std, 20); std > max(3·std_zero, 50); masses m2 < 1.5·m1) | – | accepted: "Continue" → next point; rejected: only [Repeat] → `repeat()` | next point / FIT | – |
| 5 | `AWAIT_OPERATOR` (point 1) | "Hang known weight 1, wait until it hangs still." m1 < 2 % FS → **warning only** (D-22) | mass m1 [kg] (default 1.000) | "Capture point 1 ▶" → `continue_({"mass_kg": m1})` | → 3/4 | as above |
| 6 | `AWAIT_OPERATOR` (point 2) | "Hang the bigger known weight 2." | mass m2 [kg] (default 10.000) | "Capture point 2 ▶" → `continue_({"mass_kg": m2})`; **[Finish with 2 points]** → `load_cal.finish_early()` (A-15; UNVERIFIED_LINEARITY) | → 3/4 → 7 | as above |
| 7 | `FIT` | Mini plot (points, fitted line, residuals). Table: mass, F_ref [N], raw mean, residual [N], residual [% span]. K [N/count] (negative accepted), B [N], R² (informative), NL_span, NL_FS, status **PASS / WARN / FAIL / UNVERIFIED_LINEARITY**. Warnings: LOW_SPAN, m1 < 2 % FS, "linear within noise", K change vs previous calibration, counts/kg vs nominal 32 212 | – | PASS: "Accept & save" → `continue_()`; WARN: `needs_confirmation` → C-06 → `continue_(confirmed=True)`; FAIL: `can_continue = False`, text "points are not linear – check fixture / weights / hook"; **[Re-take point ▾]** → `load_cal.retake(i)` (A-15; returns to that point's `AWAIT_OPERATOR` with the mass prefilled) | → 8 | Cancel → previous calibration stays active |
| 8 | `ACCEPT` → `DONE` | File (`load_<serial>_<UTC>.json`), now active, previous kept; FW thresholds re-checked (THR chip, `safety.thresholds`: VERIFIED / FAILED); hint "Tare before measuring" | – | [Finish] | close | – |

```
Phase CONFIG                                          Phase PRESETTLE / CAPTURE
+-----------------------------------------------+    +-----------------------------------------------+
| [v] connected  [v] AFE ok  [v] no operation   |    | Point 1: weight 1.000 kg                      |
| AFE A/128, 80.0 SPS (measured 80.1)           |    | PRE-SETTLE  1.2 s left                         |
| presettle [ 2.0 ] s   capture [ 10.0 ] s     |    | CAPTURE     [#######---------] 4.3 / 10.0 s    |
| masses [ 1.000 ] [ 10.000 ] kg               |    | raw mean 32 251   std 46   N 344   rej 0       |
| (!) 1 kg + 10 kg = 5 % FS -> LOW_SPAN;        |    | lost frames 0   stability ● stable             |
|     forces > 294 N marked "extrapolated"      |    | [Cancel]                                       |
+-----------------------------------------------+    +-----------------------------------------------+
Phase AWAIT_OPERATOR (zero / weight 1 / weight 2)     Phase EVALUATE
+-----------------------------------------------+    +-----------------------------------------------+
| Hang known weight 1 and wait until it hangs   |    | mean 32 251.4  std 45.8  SE 1.9               |
| still.                                        |    | N used 800 / rejected 0   drift 4 counts      |
| mass [   1.000 ] kg                           |    | ✓ point accepted              [Continue]      |
| (!) 1.000 kg < 2 % FS: low reference (D-22)   |    | (or ✗ REJECTED: drift 160 > max(2 std, 20) -  |
| [Capture point 1 ▶]   [Finish with 2 points]  |    |  weight still swinging?)       [Repeat]       |
+-----------------------------------------------+    +-----------------------------------------------+
Phase FIT                                             Phase ACCEPT / DONE
+-----------------------------------------------+    +-----------------------------------------------+
|  F ^        x            | m kg | F N   | res % |  | Saved: ...\calibration\load_..._T1203Z.json   |
|    |     /               | 0    | 0.00  | 0.01  |  | Active: yes (previous kept)                   |
|    |  x/                 | 1    | 9.81  |-0.03  |  | FW thresholds: ● VERIFIED 12:04:11            |
|    |x/________ raw       | 10   | 98.07 | 0.01  |  | -> Tare before measuring.                     |
| K 3.0435e-5 N/count  B -0.01 N  R2 0.9999999  |  | [Finish]                                      |
| NL 0.03 % span  -> PASS     (!) LOW_SPAN       |  +-----------------------------------------------+
| [Re-take point v] [Cancel]   [Accept & save]   |
+-----------------------------------------------+
```

### 6.4 Tare (`TarePopup`, B §9.5; SW-TARE-001…003, R4 §7)
Started by the toolbar TARE or the Calibration-tab TARE from **any tab**. It is non-modal, anchored under the toolbar, and carries a compact STOP (it derives from `SafeDialog`).
- `backend.tare(window_s)` returns a `GateResult`. REFUSE items → state REFUSED.
- Otherwise the popup follows `tare_engine.state()` / event `tare.state` (`EngineState`, phases `CHECK, CAPTURE, EVALUATE, DONE` + `REFUSED`, `ABORTED`). [Cancel] → `tare_engine.cancel()`.

| State (from `GateResult` / `EngineState.phase`) | Shows | Buttons | Next |
|---|---|---|---|
| REFUSED (gate REFUSE items or phase `REFUSED`) | texts **verbatim**: moving or < 1 s after a move; latch; sequence capture window active; not connected; AFE synthetic | [Close] | – |
| `CHECK` / `CAPTURE` | progress over the window (default 10 s); live mean raw, std, drift, N, outliers, lost frames; "stream started for tare (restored afterwards)" if applicable | [Cancel] | result |
| `EVALUATE` refused at the end (`errors`) | saturated sample, > 2 % outliers, std/drift limits, > 1 % lost frames | [Repeat] → `backend.tare()` · [Close] | – |
| `ABORTED` | interrupted by STOP / HALT / PAUSE (`abort_reason`) | [Close] | – |
| `DONE` | tare_raw, std, drift, N, time; thresholds re-check → THR chip ("VERIFIED" from `safety.thresholds`) | [Close] (auto-close after 5 s without warnings) | – |
| `DONE` with warning | "large offset: abs(K·(tare_raw − raw_zero_cal)) > 10 % FS – specimen loaded?" | [Keep] · **[Undo tare]** → `tare_engine.undo() → GateResult` (A-16; GQ-15, decided) | – |

```
+-- TARE ------------------------------------------------ [## STOP ##] -+
| Pause/Break = HALT                                                    |
| Taring... 6.2 / 10.0 s  [#############--------]                        |
| raw mean 125 008   std 44   drift 3   N 496   outliers 0   lost 0     |
| (i) stream started for tare - will be stopped again afterwards         |
|                                                        [Cancel]       |
+-----------------------------------------------------------------------+
```

### 6.5 Sequence generator wizard (B §10.6; SW-WIZ-001/002, R4 §8.2)
A `SafeWizard` with 3 pages. It causes no motion, but the STOP bar is still present (uniform rule).
- Generators are backend pure functions (`staircase`, `linear_ramp`, `cyclic`, `hold`, `return_`), each returning `Block(steps, loops)`. A `ValueError` text is shown verbatim at the field.
- **Forms are generated** from `sequencer.generator_schemas()` (A-20): `GeneratorSchema(name, label, fields: FieldSpec(name, label, unit, kind float/int/enum/bool, min, max, default, choices, depends_on, srs))`. `gui/wizards/schema_form.py` maps kind → editor, applies min/max/default (resolved by the backend against `MotionLimits`, SW limits and session defaults at call time) and shows/hides fields by `depends_on`. No range is duplicated in the GUI. Schemas are re-read each time the wizard opens.
- Insertion: `Sequence.insert_block(block, mode=append|replace|insert, index)`.

| # | Page | Content | Buttons |
|---|---|---|---|
| G1 | Choose generator | radio list of `GeneratorSchema.label` with a one-line description: Staircase, Linear ramp, Cyclic / triangle, Hold / creep–relaxation, Return | [Cancel] [Continue] |
| G2 | Parameters | generated form (fields per B §10.6: staircase kind/start/end/increment or count/up or up_down/return to zero/speed/accel/settle/capture/step time/tol; linear ramp x0/x1/speed/accel; cyclic kind/lo/hi/cycles/speed/accel/dwell/capture; hold kind/target/duration; return zero/home) | [Cancel] [Continue] (calls the generator; errors shown) |
| G3 | Preview & insert | read-only step list of the block, mini chart (`planned_path` of a temporary sequence with the block), summary (steps, duration from `expand`, travel/load range), validation issues; insert mode ( ) append (o) insert after the selected row ( ) replace all (C-08 if dirty) | [Cancel] [Change parameters] [Insert] |

```
G1 Choose                         G2 Parameters (Staircase)                G3 Preview & insert
+----------------------------+    +----------------------------------+    +-------------------------------------+
| (o) Staircase              |    | kind  (o) travel ( ) load        |    | # | type   | target | v   | capt |  /\ chart |
|     steps up (and down)    |    | start [ 0.000 ] mm               |    | 1 | travel | 1.000  | 1.0 | 5.0  |   |       |
| ( ) Linear ramp            |    | end   [ 5.000 ] mm               |    | 2 | travel | 2.000  | 1.0 | 5.0  |   |       |
| ( ) Cyclic / triangle      |    | (o) increment [1.000] ( ) count  |    | ...                                  |
| ( ) Hold / creep-relax.    |    | (o) up ( ) up-down [ ] return 0  |    | 10 steps, ~ 3 min 20 s, x 0...5 mm    |
| ( ) Return                 |    | speed [1.0] accel [0] settle [2] |    | (!) none                              |
|                            |    | capture [5.0]  step time [0]     |    | (o) append ( ) after row 3 ( ) replace|
+----------------------------+    +----------------------------------+    +-------------------------------------+
```

---

## 7. Main-window and dialog inventory (wireframe index)

| Window / page | Wireframe | Section |
|---|---|---|
| Main window (toolbar, stop banner, mode banner, notice strip, tabs, docks, indicator bar) | yes | §2.1 |
| Connection & Config tab | yes | §3.1 |
| Safety limits tab (incl. no-specimen mode) | yes | §3.2 |
| Test marks tab | yes | §3.3 |
| Manual tab | yes | §3.4 |
| Calibration & Tare tab (incl. travel restore state, tare undo) | yes | §3.5 |
| Sequence tab (editor, chart, run panel) | yes | §3.6 |
| Report tab | yes | §3.7 |
| Plot window (channel tree, time view, X-Y view, lanes) | yes | §4.1 |
| Wizard frame + travel calibration phases CHECK…RESULT, RESTORING | yes | §6.1, §6.2 |
| Load calibration phases CONFIG…DONE | yes | §6.3 |
| Tare popup | yes | §6.4 |
| Generator wizard G1–G3 | yes | §6.5 |
| ConfirmDialog (C-01…C-15), ClearStopDialog, Pause/Resume flow | below / §5.9 | §5.5, §5.6, §5.9 |
| HotkeyTestDialog, LinkStatsDialog, StatusHelpDialog, board-file report dialog | below / table dialogs (layout from TS) | §5.3, §3.1, §2.4 |

```
ConfirmDialog (C-10 example)                                ClearStopDialog
+-- Switch to no-specimen mode? ----------- [## STOP ##] +  +-- Clear stop ----------------------------------- [## STOP ##] +
| Pause/Break = HALT                                     |  | Latched: HALT (button, 14:03:12) - release the STOP button  |
| No specimen is mounted. The PC load limits are         |  |          PAUSED (PC, 14:03:05)  ESTOP (14:03:10)            |
| switched OFF for this session. The board load limit    |  |          FAULT LOAD_LIMIT (14:03:09)                        |
| stays active (nominal default ±7 022 271 counts ≈      |  |  HALT+PAUSE gate: REFUSE "STOP button still pressed"         |
| ±109 % FS). Mount no specimen until a load calibration |  |                                    [Clear HALT + PAUSE]      |
| and a tare exist.                                      |  |  E-STOP  gate: CONFIRM  [ ] button released, area safe        |
| Board thresholds: DEFAULT_ONLY ✓ verified              |  |                                          [Clear E-STOP]     |
| [ ] No specimen is mounted                             |  |  Faults  gate: WARN LOAD_LIMIT cause present  [Clear faults] |
|                                                        |  | After clearing: ENABLE + HOME required; no motion restarts. |
| [Cancel]               [Switch PC load limits off]     |  | [Close]                                                     |
|  (focus on Cancel; Enter/Space do nothing)             |  +-------------------------------------------------------------+
+--------------------------------------------------------+
HotkeyTestDialog
+-- Test Pause/Break key ------------------------------- [## STOP ##] +
| Pause/Break = HALT                                                  |
| Mode: REGISTERED (RegisterHotKey)                                   |
| Press Pause/Break now ... 7 s left. During the test motion is       |
| refused and the key does NOT send HALT.                             |
| Result: key -> callback 3.1 ms   (or: no key press within 10 s)     |
| (i) KL-01: the key is not delivered while an elevated (Administrator)|
|     window has focus - use the physical STOP/BREAK or E-stop.       |
|                                                   [Start] [Close]   |
+---------------------------------------------------------------------+
```

---

## 8. Module / file layout (`03_SW/src/bend_stand/gui/**`)

```
03_SW/src/bend_stand/gui/
  __init__.py            (owner D, D-29 n)
  app.py                 run(backend, args) -> int (A-25): AA_DontUseNativeDialogs before QApplication; QApplication;
                         theme; backend.start() before the window is shown; MainWindow; optional connect from args;
                         GC policy after show(); exec(); backend.shutdown(); returns the exit code (origin TS gui/app.py)
  settings.py            QSettings factory, keys, GUI-only preferences (layout, last tab, theme)
  theme.py               safety palette (STOP red, amber, green, grey "unknown", mode-banner hatch), stylesheets, light/dark
  gc_policy.py           GUI-thread-only cyclic GC (SWD-PM3-07)                    (copy TS gui/gc_policy.py)
  bridge.py              QtBridge: EventBus topics + engine subscriptions -> queued Qt signals; future relay
                         (copy/adapt TS gui/bridge.py)
  stop.py                process-wide STOP dispatcher trigger_stop(source), history (copy/adapt TS gui/estop.py)
  gating.py              GateBinder: binds widgets to status().gates ids; REFUSE/CONFIRM/WARN; CLEAR_FIRST_CODES
  indicator_map.py       pure display table keyed by protocol_gen names -> chip, polarity, level (unit-tested, G-39)
  refresh.py             RefreshScheduler: the single 33 ms PreciseTimer, gui_beat(), stage timing, perf_stats()
  format.py              display formatting only (decimals per unit, thousands separators, n/a)
  units_state.py         process-wide display unit of force N / kgf (View > Units, SYS-003; display only)
  main_window.py         QMainWindow: toolbar, banner stack, tabs, docks, indicator bar, menus, close rules
  widgets/
    stop_button.py       StopButton (large/compact)                                (copy TS widgets/estop_button.py)
    safe_dock.py         SafeDock: QDockWidget with title bar [NO-SPECIMEN tag][Float][Close][STOP]
    indicator_bar.py     IndicatorBar + IndicatorChip
    status_led.py        StatusLed                                                  (copy TS widgets/status_led.py)
    stop_banner.py       StopBanner (severity order, "+n more")
    mode_banner.py       ModeBanner (no-specimen mode, SW-LIM-004)
    notice_strip.py      NoticeStrip (travel_cal_differs, compat, read-only, NVM defaulted, reboot pending)
    pause_button.py      Pause/Resume toolbar button (text from indicators.paused; Clear-stop-first hint)
    readout.py           ReadoutDock (values + LatestSample.state)                  (adapt TS widgets/readout.py)
    channel_tree.py      ChannelTree (greyed prerequisites, swatches, batch)        (adapt TS widgets/channel_tree.py)
    param_form.py        ParamForm (dictionary-generated editors, locked session rows, R flag)  (adapt TS widgets/param_form.py)
    endpoint_selector.py EndpointSelector (backend.endpoints())                     (adapt TS widgets/port_selector.py)
    target_slider.py     TargetSlider (handle-drag only, move_to on release, live marker)
    hold_button.py       HoldButton (press -> jog_start; release/focus loss/hide/disable/forced -> jog_stop once)
                         (as built M3: step buttons and the checked spin boxes live in tabs/manual_tab.py)
    event_log.py         EventLogDock (EVENT_NAMES / *_DESC decoding)
  dialogs/
    safe_dialog.py       SafeDialog (top bar: hint, NO-SPECIMEN tag, STOP), SafeMessageBox, SafeFileDialog (copy TS dialogs/safe_dialog.py)
    confirm_dialog.py    ConfirmDialog (SAF-SW-004 keyboard rules, assertion checkbox, live gate re-check) - C-01...C-12
    clear_stop_dialog.py ClearStopDialog (clear_stop / estop_clear / fault_clear rows; sequence-PAUSED alternative)
    status_help.py       StatusHelpDialog (indicator meanings from *_DESC + clear_hint)
    link_stats.py        LinkStatsDialog                                            (adapt TS dialogs/link_stats.py)
    hotkey_test.py       HotkeyTestDialog (A-04; KL-01 text; result from topic hotkey.test)
    file_report.py       report dialog for BoardConfigFile / FileFormatError
    tare_popup.py        TarePopup (incl. Undo tare)
    about.py
  wizards/
    safe_wizard.py       SafeWizard frame (start page, PHASES step strip, generic EngineState page, button policy,
                         C-05/C-06/C-08/C-12)
    phase_views.py       phase -> view kind table (completeness-tested against engine PHASES), generic result display
    travel_cal.py        TravelCalWizard (start page text; pages rendered from the engine)
    load_cal.py          LoadCalWizard (n_points / pre-settle / capture; finish early, re-take)
    schema_form.py       form builder from GeneratorSchema / FieldSpec (A-20)
    generator.py         pages G1 ... G3
  tabs/
    connection_tab.py    (adapt TS tabs/connection_tab.py)
    limits_tab.py        (incl. no-specimen mode group)
    marks_tab.py
    manual_tab.py
    calibration_tab.py
    sequence_tab.py
    report_tab.py
  plots/
    plot_dock.py         PlotDock(SafeDock): pane grid, placement rules, menus, X link, layout state (§4.7)
    plot_pane.py         PlotPane (time pane: curves, 2 axes, bit lanes, vstate styles) + XYPane (copy TS plot_pane.py)
    pane_grid.py         PaneGrid 1..4 columns, drag & drop, drop indicator           (copy TS pane_grid.py)
    quantity.py          quantity groups for the default placement                    (adapt TS quantity.py)
    time_view.py         pure helpers: interleave min/max columns, split_vstate
    autorange.py         Y-range hysteresis (pure function, unit-tested)
    axes.py              axis helpers (SI prefix off, fixed width, static ticks)
    sequence_chart.py    SequenceChart (planned path, capture points, active step, trace, live marker, NOT_REACHED marks)
  seq_access.py          (M4) the one adapter to B's sequencer / report types (§15.4 WP-D16): type resolution, issue →
                         cell mapping, pure step-list / loop-index edits, plan / path / status / result normalisation
  models/
    step_table_model.py  QAbstractTableModel over the backend Sequence + loop bracket in the row header (M4)
                         (as built: the marks custom table and the recordings list are QTableWidgets in their tabs;
                         no separate marks_model.py / recordings_model.py)
03_SW/tests/gui/          (owned by D) - see §10
```

Rules:
- **Imports.** `gui` imports only: `bend_stand.core.api` (Protocols + dataclasses + enums re-exported there); `bend_stand.core.protocol_gen` (**name tables only**: `*_BITS`, `*_NAMES`, `*_DESC`, `ICD_VERSION`; P8); `bend_stand.calc.units` for display units (B §15.4 rule 8). `gui/app.py` receives the `Backend` instance from `bend_stand.__main__` and needs no backend import. `gui` never imports `io`, `serial`, `core.backend` internals or protocol codec modules. A test enforces this (G-01).
- **Origin notes.** Every copied TS file starts with `# Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/<file> @<hash copied>` (D-02, D-29 m, SYS-010) and lists the changes.
- **Requirement tags** on classes and functions: `# Implements: SW-MAN-001` etc.

### 8.1 Entry point contract (A-25, D-29 n)
```python
def run(backend: "core.api.BackendAPI", args: argparse.Namespace) -> int:
    """Implements: SW-PLT-001, SW-STOP-001 (native dialogs off), SW-STOP-002 (hotkey active before Connect)."""
```
1. `QApplication.setAttribute(Qt.AA_DontUseNativeDialogs)` (and high-DPI policy) **before** `QApplication` exists.
2. `QApplication(sys.argv[:1])`, theme, `pg.setConfigOptions(...)`.
3. `backend.start()` — before the main window is shown (B §15.4 rule 1). `bend_stand.__main__.main()` constructs the Backend **unstarted** (B3-20).
4. `MainWindow(backend)`, layout restore, `show()`, then `gc_policy.install()`.
5. If `args.endpoint` (str | None, set by B's argparse from `--sim` / `--port`, B3-20) is set, `connect_async(args.endpoint)` is issued after `show()`; otherwise the GUI starts disconnected (D-06).
6. `rc = app.exec()`; `backend.shutdown()` is called from `closeEvent` (§5.8) and again idempotently after `exec()` returns; return `rc`.

---

## 9. Qt signal adapter and threading rules

### 9.1 Layering
```
 backend (Qt-free; threads: reader, pipeline, supervisor, worker, operation runner, recorder, hotkey, sim) - B §3.1
    |  EventBus callbacks / engine subscribe() (backend threads, return < 1 ms)
    |                                                ^ sync, non-blocking (B §15.4 rule 2): stop/halt/pause/resume/tare/
    v                                                |   take_sample/record_*/hotkey_test_start/status/data.*/config.check/
 gui/bridge.py  QtBridge (QObject, GUI-thread affinity) |   motion.check/limits.get/set/set_no_specimen_mode/tare_engine.undo/
    |  Qt signals (queued -> GUI thread)             |   engine methods/motion commands; futures: *_async
    v
 widgets / tabs / docks / dialogs (GUI thread only)
```

### 9.2 `QtBridge` (adapted from TS `gui/bridge.py`) — topic mapping (B §15.3, A-23)
| Qt signal | Backend topics | Payload | Consumers |
|---|---|---|---|
| `linkChanged` | `link.state`, `link.compat`, `device.info` | link dataclasses | link widget, Connection tab, notice strip |
| `paramsChanged` | `device.params` | params snapshot | ParamForm |
| `deviceStatus` | `device.status` | GET_STATUS data | LinkStatsDialog |
| `fwEvent` | `fw.event` | decoded EVENT | Event log, toasts (RESUME_REQUEST, buttons, CLK_FALLBACK) |
| `stopIssued` | `stop.issued`, `stop.confirmed`, `stop.unconfirmed` | `StopResult` / confirmation result (STOP, HALT, PAUSE) | stop banner (also hotkey HALTs) |
| `indicatorsChanged` | `indicators.changed` | `Indicators` | edge toasts only (chips are polled) |
| `safetyEvent` | `safety.trip`, `safety.warning`, `safety.thresholds`, `safety.no_specimen` | event dataclasses | banners, LOAD/THR/NOSPEC chips, limits tab |
| `motionEvent` | `motion.done`, `motion.target`, `motion.dropped` | `MoveDone` etc. | Manual tab, Event log |
| `engineState` | `tare.state`, `cal.travel.state`, `cal.load.state` (+ engine `subscribe`) | `EngineState` | wizards, tare popup |
| `travelRestore` | `cal.travel.restore` | restore result | notice strip, Calibration tab, wizard `RESTORING` page |
| `channelsChanged` | `channels.changed` | – | ChannelTree |
| `sequenceEvent` | `seq.status`, `seq.step_result`, `seq.window` | `SeqStatus`, results | Sequence tab, Report tab |
| `recordingEvent` | `rec.state`, `rec.failure`, `sample.taken`, `marks.edited` | recorder dataclasses | REC chip, banner, toasts, Event log |
| `hotkeyEvent` | `hotkey.state`, `hotkey.test` | `HotkeyStatus`, test result | KEY chip, HotkeyTestDialog |
| `reportReady` | `report.ready` | `ReportPaths` | Report tab |
| `resumeIgnored` | `resume.ignored` (B3-17) | `ResumeIgnored(source, reason: GateResult, t_us)` | toast, PAUSED banner line, Event log |
| `logMessage` | `log` | log record | Event log |

- **High-rate data is never a signal.** The refresh timer pulls `status()`, `data.snapshot()`, `data.xy()`, `data.latest()`, `data.sequence_trace()` and `sequencer.status()` (B §15.4 rule 4).
- **Subscriptions** use bound methods of the long-lived bridge (held weakly by the backend, B §15.4 rule 3). `bridge.shutdown()` unsubscribes all tokens before the window closes. A test (G-01b) asserts that every topic of B §15.3 is mapped or explicitly ignored.
- **Futures** (`*_async`): `bridge.watch(future, on_done, on_error)` attaches a done-callback that only emits through one immortal relay object (`_DoneRelay`, TS pattern). A worker thread never holds a widget reference (SWD-PM3-07). Errors are `BendStandError`; the GUI shows `user_text` (B §15.4 rule 9).

### 9.3 Threading rules (binding)
| # | Rule |
|---|---|
| T1 | Only the GUI thread creates, modifies or deletes widgets and any QObject of the GUI. No `QThread`s of our own in `gui/`. |
| T2 | Bridge callbacks do **only** `signal.emit(payload)` (payloads are frozen dataclasses). No widget access, no blocking. |
| T3 | The GUI never calls `Future.result()` and never sleeps (B §15.4 rule 2). |
| T4 | Synchronous backend calls from the GUI thread are limited to the non-blocking set of B §15.4 rule 2 (see §9.1). STOP is called first and directly in the press handler. |
| T5 | The Pause/Break hotkey runs entirely in B's hotkey thread. The GUI only displays its status and starts the test mode. |
| T6 | `backend.gui_beat()` is called on every refresh tick. The backend's jog refresh and liveness depend on it, so a frozen GUI ends a jog (FW dead-man) and is reported. |
| T7 | Data arrays returned by `data.*` are copies owned by the GUI (B §7.5). `BackendStatus` is immutable and returned by reference. The GUI never mutates it. |
| T8 | No Python-owned QObject may sit in a reference cycle that a backend thread could collect: GUI-thread GC policy (§9.4), parented pyqtgraph orphan menus (TS `adopt_pyqtgraph_orphans`), no lambdas capturing widgets in backend subscriptions (only bridge-bound methods). |
| T9 | Every slot that handles a backend event catches and logs exceptions; an exception never propagates into Qt. |

### 9.4 Object lifetime and GC policy
Copy TS `gui/gc_policy.py`: `gc.disable()` at start; after `show()` one full collection + `gc.freeze()`; then GUI-thread-only collections from a 100 ms timer (gen0 on threshold, gen1 every 1 s, full every 10 s); full collection postponed while `status().motion.moving` or an operation runs (≤ 600 s). Measured pauses go into `perf_stats()`. Companion: `adopt_pyqtgraph_orphans()` for every `PlotItem`/`ViewBox` created. B's §3.3 lifetime rules are the backend half of the same policy.

---

## 10. GUI test strategy

### 10.1 Tooling
- pytest + **pytest-qt**, `QT_QPA_PLATFORM=offscreen` set in `tests/gui/conftest.py` **before** QApplication is imported (role rule). The D-06 port guard of B's `tests/conftest.py` applies to every GUI test.
- Markers: `gui`, `perf` (opt-in, real display), `winint` (Windows integration, real hotkey).
- Every test carries `@pytest.mark.req("SW-…")` and a `# Verifies:` tag.
- Fixtures:
  - `fake_backend` (`tests/gui/fakes.py`): implements the `core.api` Protocols (B §15) in pure Python. It records calls with timestamps and has a scriptable `status()` (incl. `gates`, `Indicators` with ON/OFF/UNKNOWN items, `safety.no_specimen_mode`, `calibration.travel_cal_differs`, `hotkey`), engines (`EngineState` sequences, `PHASES`), `data.*` arrays (`PlotSnapshot`) and EventBus emission (TS `tests/gui/fakes.py` pattern). A conformance test checks the fake against the `core.api` Protocols so it cannot drift from B's API;
  - `sim_backend`: the real `Backend` connected to `"sim"`, with `backend.sim` (`SimControl.act`, `override_status`, `emit_event`, `inject_nack`, `inject_store_mismatch`), `Backend.test_hooks` (`fail_recorder`, `stall_thread`) and the `VirtualTransportPair.wire_log` for wire timestamps (A-22); for deterministic tests `BackendSettings(clock="lockstep", test_hooks=True, wire_log=True)` with `test_hooks.advance(ms)` and `SimControl` `drop_next` / `duplicate_next` / `delay_next` / `on_frame` / `on_event` (B3-09);
  - `main_window(fake|sim)`.
- Waits use `qtbot.waitUntil(cond, timeout)`, never `sleep`.
- Thread-safety stress: TS `stress_gc_deadlock.py` and `test_gc_policy.py`, adapted (backend threads allocate cycles while the GUI repaints).

### 10.2 Automated GUI tests (D, `03_SW/tests/gui/`)
| ID | Test | Backend | Verifies | MS |
|---|---|---|---|---|
| G-01 | layering: `gui` imports only `core.api`, `core.protocol_gen` (name tables), `calc.units` (AST scan); backend packages import no Qt (B's test, re-run). G-01b: every topic of B §15.3 is mapped in the bridge or explicitly ignored | – | SW-PLT-002, P1, P8 | M1 |
| G-02 | **STOP everywhere:** for all tabs, all docks (docked and floating), every `SafeDialog` subclass, every wizard phase view, tare popup, hotkey test, message box and file dialog → exactly one visible `stopButton`, `NoFocus`, not default, not escape. A press calls `backend.stop` synchronously (recorded in the same event-loop turn, before any queued event). Native dialogs disabled (`AA_DontUseNativeDialogs` set) | fake | SW-STOP-001, SW-RT-001, SW-MAN-005 | M1 (existing windows), M3 (all) |
| G-03 | `StopResult(sent=False)` → banner "STOP NOT SENT" with the reason; STOP fires on `pressed`; `stop.unconfirmed` for STOP / HALT / PAUSE → "NOT CONFIRMED" banner | fake | SW-STOP-001 | M1 |
| G-04 | **Confirm keyboard rules:** for **C-01…C-12** (incl. **C-10 no-specimen mode**), Return, Enter and Space (focus on each focusable widget in turn) never confirm; Esc cancels; only a mouse click on the confirm button (after the checkbox where required) confirms; STOP present; the NO-SPECIMEN tag appears in the top bar while the mode is on | fake | SAF-SW-004, SW-LIM-004 | M1 (C-04, C-11), M3 (all) |
| G-05 | each confirmation appears for its CONFIRM item (`home`, `disable`, `estop_clear`, `no_specimen` gates; engine `start()` CONFIRM; engine `needs_confirmation`) and the repeated call carries `confirmed=True` / `load_confirmed=True` | fake + sim | SAF-SW-004 | M3 |
| G-06 | indicators: `indicator_map` unit test for every generated name and SW item. With the simulator, each injected condition (ESTOP, HALT button, PAUSED + source, LIMIT_START/END, LOAD_LIMIT, AFE_STALE / SATURATED / RATE_MISMATCH, NO_AFE_DATA, LINK_WDG, HOMED, POS_UNCERTAIN, ENABLED, ALM, PEND, DRV_PWR) updates its chip ≤ 200 ms after the frame (`wire_log` timestamp) | fake + sim | SAF-SW-005 | M3 |
| G-07 | stop banner and `clear_hint` per latch; ClearStopDialog rows follow the `clear_stop` / `estop_clear` / `fault_clear` gates and call the right `*_async`; "Clear HALT + PAUSE" label; sequence PAUSED → C-13 → `clear_stop_async(confirmed=True)` and sequence end reason CLEARED; no motion call follows a clear | fake + sim | SW-STOP-003 | M3 |
| G-08 | HALT / ESTOP / DRV_PWR loss injected during a running wizard / sequence / tare → engine `ABORTED` with `abort_reason` shown within one refresh tick; sequence state ABORTED | sim | SW-STOP-003 | M3 |
| G-09 | Pause/Resume toolbar: Pause → `backend.pause("toolbar")` (StopResult shown); button text follows `indicators.paused` only (no optimistic change); PAUSED chip shows the source (PC / BUTTON); Resume → `backend.resume("toolbar")`; manual controls greyed while PAUSED and re-enabled after Resume; no motion call after a manual Resume | fake + sim | SW-STOP-004, SAF-SW-005 | M3 |
| G-10 | hotkey chip modes REGISTERED / LL_HOOK / UNAVAILABLE; UNAVAILABLE → app `QShortcut` installed and calls `halt`; otherwise no QShortcut; KL-01 text in tooltip and Help | fake | SW-STOP-002 | M3 |
| G-11 | toolbar Stream / Record / Sample / TARE present and functional on **every** tab; Record stop shows `GateResult` refusals | fake | SW-ACQ-001, SW-TARE-001, SW-ACQ-003 | M1 (Stream), M3 |
| G-12 | **Slider:** handle drag sends nothing; release sends exactly one `motion.move_to(value)`; groove click, wheel and keys send nothing; disabled while the `move` gate has REFUSE (not homed, PAUSED) | fake | SW-MAN-001 | M3 |
| G-13 | Go-to absolute/distance → `move_to` / `move_by`; Enter in the field sends nothing; step buttons → `move_by(±d)`, three +1 clicks = three calls; the pending target is displayed | fake | SW-MAN-002, SW-MAN-003 | M3 |
| G-14 | **Hold-to-jog:** press → `jog_start`; release, focus loss, app deactivate, tab change or gate close while held → `jog_stop` (exactly once); PAUSE while held → released state, new press needed; `gui_beat()` called on every tick | fake | SW-MAN-004 | M3 |
| G-15 | speed/accel above the `MotionLimits` caps refused (not applied, red) via `motion.check`; caps label shows travel / loaded / step-rate caps; SAF-SW-006 WARN shown | fake | SW-MAN-005, SAF-SW-006 | M3 |
| G-16 | enable checkbox follows `indicators.enabled` (no optimistic state); ENABLING countdown; HOME, test zero, reset test zero, VALID calls | fake | SW-MAN-006 | M3 |
| G-17 | ParamForm: every dictionary parameter shown with its editor type and flags (M/N/R); board values equal the simulator; session rows (`safety.load_raw_min/max`, `safety.zero_raw`) locked and absent from the saved file; write+verify statuses OK / REJECTED / MISMATCH / BUSY / TIMEOUT / REBOOT_REQUIRED displayed (`inject_nack`, `inject_store_mismatch`); `BoardConfigFile` report cases; Restore defaults needs C-04 | fake + sim | SW-CFG-001…004 | M1 |
| G-18 | compat mismatch → RO chip, notice strip, motion and config write disabled; hash mismatch → read-only form; link line shows `dup_frames` / `seq_anomalies` (B3-10) | fake + sim | IF-008 | M1 |
| G-19 | limits tab: `limits.set` issues shown per field; `ThresholdState` display incl. `clamped` / effective levels; [Re-send & verify] → `recheck_async`; motion-disabled notice from the `move` gate | fake + sim | SW-LIM-001/002, SAF-SW-001/002 | M3 |
| G-20 | marks: add/rename/remove custom field → `marks.set`; presets round trip; a simulator recording's `meta.json` contains the custom field; edit during recording → footer + `marks.edited` row | fake + sim | SW-META-001/002, SW-LIM-003 | M3 |
| G-21 | channel tree: every registry channel toggles a curve; `available = False` greyed with its `reason`; `channels.changed` re-reads the registry (no polling) and re-enables after calibration | fake | SW-RT-002, SW-RT-004 | M1 (DATA + bits), M3 |
| G-22 | plot window float / re-attach / close / reopen; ≥ 2 windows; each floating window has STOP and the NO-SPECIMEN tag when on; layout save → a new MainWindow restores docks, channels and window lengths; off-screen geometry corrected | fake | SW-RT-001 | M1 (float/STOP), M3 |
| G-23 | time view window 5–600 s; freeze stops updates; manual Y range; `autorange.py` hysteresis vectors; `PlotSnapshot` columns interleaved; `vstate` styles (solid / dashed / grey / gap) | fake | SW-RT-003, SW-CAL-008 | M1 |
| G-24 | readout state texts from `LatestSample.state`: n/a / STALE / SATURATED / INVALID / EXTRAPOLATED / OK | fake | SW-RT-005, SW-CAL-008 | M1 (raw, rate), M3 |
| G-25 | units N/kgf selector relabels axes and readouts (`F_kgf`, `calc.units`) | fake | SYS-003 | M3 |
| G-26 | **travel wizard** with the simulator: step strip = `PHASES`; every phase has a view; all phases (D1, D_tot entries) → board steps/mm = engine result; expected 800 steps/mm; C-05 for a > 20 % change shows 800 and 160 ("DIP change not applied"); Continue buttons with `continue_moves` are `NoFocus`; cancel in each phase → `RESTORING` → board keeps spm0 (read-back); STOP during a move → ABORTED view; link loss after spm1 → TCAL notice until reconnect | sim | SW-CAL-001…004 | M3 |
| G-27 | **load wizard** with the simulator: zero, 1 kg, 10 kg → LOW_SPAN shown; WARN needs C-06; FAIL disables Accept; a rejected point offers only Repeat; [Finish with 2 points] → UNVERIFIED_LINEARITY; [Re-take point] returns to that point; cancel keeps the previous calibration; allowed in no-specimen mode | sim | SW-CAL-001, -005…-009 | M3 |
| G-28 | tare popup: REFUSE items verbatim (moving, latch), success → THR VERIFIED shown, large-offset warning with [Undo tare] → `tare_engine.undo()`; reachable from every tab | fake + sim | SW-TARE-001…003 | M3 |
| G-29 | sequence editor: typed editing; `validate` issues flag cells; loop wrap/unwrap (count 0 = until stopped); save/open round trip; `FileFormatError` → sequence unchanged; no travel-bound column and LOAD `step_time_s` hidden (B3-07); generator wizard forms built from `generator_schemas()` (min/max/default/`depends_on`); inserts (append/after/replace); the steps stay editable | fake + sim | SW-SEQ-001/002, SW-SEQF-001, SW-WIZ-001/002 | M4 |
| G-30 | sequence run with the simulator: `sequence_start` REFUSE items listed (one per reason, incl. **POS_UNCERTAIN, AFE_RATE_MISMATCH, PAUSED, ALM active, DRV_PWR off** — D-33 b); start/pause/resume/continue/stop/abort buttons → backend; run line follows `SeqStatus` incl. `remaining_s` / `plan_total_s`; a load step not reached → **NOT_REACHED** shown in the run line, step results and chart (D-32); ALM during the run → end reason DRIVER_ALARM (D-33 c); `start(seq, confirmed=True)` after C-07 (B3-20); on the wire only absolute targets | sim | SW-SEQ-003…007, SW-MAN-002/003 | M4 |
| G-31 | sequence chart: planned path equals `planned_path`; live marker update rate ≥ 10 Hz over 5 s; active step highlighted | fake + sim | SW-SCH-001/002 | M4 |
| G-32 | report tab: `list_recordings` list, `load_result` step table (NOT_REACHED flag shown), Generate → `reports.build_async(dir, cal, tare, bend3p)`; options | fake | SW-REP-001…004 | M4 |
| G-33 | `rec.failure` injected (`test_hooks.fail_recorder`) → red banner, REC chip red | sim | SW-ACQ-004 | M3 |
| G-34 | link loss injected during a sequence → LINK LOST banner, controls disabled, no-specimen mode ended toast | sim | SAF-SW-003, SW-LIM-004 | M3 |
| G-35 | GC policy / deadlock stress (adapted TS tests) | sim | NFR-004 (GUI part), T8 | M3 |
| G-36 | full simulator workflow smoke through the GUI: connect → enable → home → no-specimen mode → travel cal → load cal → tare → leave mode → sequence → report | sim | SYS-008 | M1 (connect → stream → config), M4 (full) |
| **G-37** | **no-specimen mode:** without calibration the `move` gate REFUSE shows the first-use banner with [Enter no-specimen mode…]; C-10 (keyboard rules as G-04) → `set_no_specimen_mode(True, confirmed=True)`; mode banner visible on **every tab**, NOSPEC chip, tag in every floating dock and dialog; banner text shows default vs calibrated board thresholds; jog and both wizards enabled; [Leave] → `set_no_specimen_mode(False)`; disconnect → banner gone + toast; mode never offered for saving | fake + sim | SW-LIM-004, SAF-SW-004, SAF-SW-005 | M3 |
| **G-38** | **travel-cal restore indicator:** `travel_cal_differs` → TCAL chip + notice strip with both values; `restore_pending` → "restoring… (n/3)"; buttons from `travel_diff.actions` → `resolve_travel_difference_async("restore" / "keep_board" / "ignore_session")` (B3-19); result toast; Calibration-tab panel consistent | fake + sim | SW-CAL-001 | M3 |
| **G-39** | **generated names completeness:** every name of `DATA_FLAGS_BITS`, `DATA_STATUS_BITS`, `FAULTS_BITS`, `SYS_FLAGS_BITS` is a chip member or listed "no chip" with a reason; tooltips equal `*_DESC`; the registry's status-bit channels equal the generated bit names in order; every generated name resolves as `indicators.<name.lower()>` (B3-18); event-log decoding covers every `EVENT_NAMES` entry; `CLEAR_FIRST_CODES` ⊆ `BLOCK_BITS` | fake | SAF-SW-005, SW-RT-002, P8 (GF-08) | M1 |
| **G-40** | **Resume refused → "Clear stop first":** PAUSED + HALT latched (manual) → Resume disabled, banner/tooltip "Clear stop first", [Clear stop…] → `clear_stop_async()` clears both (no Resume call, no motion); PAUSED + ESTOP / FAULT → same hint; sequence PAUSED → Clear stop needs C-13; physical PAUSE while PAUSED (RESUME_REQUEST) → toast, PAUSED clears (sim); `resume.ignored` → toast when the gate is closed; a `MoveTicket` resolved `REFUSED_PAUSED` → info row, no error toast | fake + sim | SW-STOP-003, SW-STOP-004, D-31 | M3 |
| **G-41** | config extras: `config.check` live rule check disables Write & verify; REBOOT_REQUIRED status + R flag; `reboot_pending` notice → C-11 → `save_async` then `reboot_async`; `nvm_defaulted` notice; CFG chip states | fake + sim | SW-CFG-003, SW-CFG-004 | M1 |
| **G-42** | hotkey test mode: gate `hotkey_test` REFUSE shown; `hotkey_test_start(10.0)` called; `hotkey.test` delay / timeout displayed; motion gates refused during the window (status from the fake) | fake | SW-STOP-002, NFR-003 | M3 |
| **G-43** | entry point: `gui.app.run(fake_backend, args)` sets `AA_DontUseNativeDialogs` before `QApplication`, calls `backend.start()` before the window is shown, connects only when `args.endpoint` is set (B3-20), calls `shutdown()` on close and returns the exit code (offscreen, window closed by a timer) | fake | SW-PLT-001, SW-STOP-001, D-06 | M1 |
| **G-44** | **UNKNOWN and new safety items:** every chip with an `UNKNOWN` member is grey "?" (never green), before the first frame and when stale; DRV_PWR off, K1_WELDED, HOME_DRIFT (value µm), CLK_FALLBACK, NO_AFE_DATA, ALM with power ("new motion blocked") render chip + banner + `clear_hint` per §2.3/§2.4 | fake + sim | SAF-SW-005 | M1 (UNKNOWN), M3 |
| **G-45** | quantity groups of the registry (unit / key / generated bit names; `dimension` when present) | – | SW-RT-006 | M1+ |
| **G-46** | placement rules D-63 (1)–(5): same quantity joins, empty pane reused, untick keeps pane, manual move wins, group tick only available children; third unit → another pane with info | fake | SW-RT-006 | M1+ |
| **G-47** | columns 1/2/3/4 keep order (row-major positions), time labels bottom row only; + Pane, rename (inline, Enter / empty = automatic), close unticks, last pane kept; channel / pane menus | fake | SW-RT-006 | M1+ |
| **G-48** | drag & drop reorder with drop indicator (synthetic drag events), title-strip gesture starts the drag, pane MIME | fake | SW-RT-006 | M1+ |
| **G-49** | X link inside a window; X-Y pane (choices, `data.xy`, live point, not time-linked, never a default target) | fake | SW-RT-006, SW-RT-003 | M1+ |
| **G-50** | move pane to another window (menu and drop) keeps curves; STOP in every new window; max 4 windows | fake | SW-RT-006, SW-RT-001, SW-STOP-001 | M1+ |
| **G-51** | one `data.snapshot` per distinct time window for all panes of all shown, non-frozen windows (union of keys); frozen / hidden windows excluded; perf smoke with 4 time panes + X-Y (fake 200 ticks; sim 5 s real timer) | fake + sim | SW-RT-006, NFR-001 | M1+ |
| **G-52** | layout (columns, order, curves per pane incl. empty panes, titles, X-Y panes, window, second window) restored by a new MainWindow; corrupt entry → default | fake | SW-RT-006, SW-RT-001 | M1+ |

### 10.3 Performance tests (opt-in, real display, `@pytest.mark.perf`; acceptance runs by Validator F)
| ID | Scenario | Criterion | Req |
|---|---|---|---|
| P-01 | MainWindow + simulator at 80 Hz (out of process, `tcp://127.0.0.1:5770`), recording on, Plot 1 (time + X-Y, all channels incl. bits, 30 s) + Plot 2 floating, 10 min | refresh p95 ≤ 50 ms (≥ 20 fps), event-loop p99 ≤ 100 ms, 0 frames lost by the SW | NFR-001, NFR-004 |
| P-02 | as P-01, STOP pressed every 0.5 s (posted mouse press on the toolbar and dock STOP), 100 presses; same for Pause | press → STOP / PAUSE frame in `wire_log` ≤ 50 ms p95 | NFR-002 |
| P-03 | as P-01, Pause key via the fake hotkey backend (and `SendInput` on the reference PC, `winint`), 100× | key → HALT frame ≤ 50 ms p95 | NFR-003 |
| P-04 | 600 s window, 4 plot windows | ≥ 20 fps (stress, informative) | NFR-001 |
| P-05 | 1 h soak with plotting + recording | memory growth ≤ 50 MB after the buffers are full | NFR-004 |

A **perf smoke** (P-01, 60 s, the M1 channel set) runs at every milestone from M1 on, so a regression is caught early (R3 §7.2 item 6). The binding NFR runs use the PO's lab PC as the reference PC (D-33 j).

### 10.4 Demonstration items (procedures for Validator F, on the reference PC)
Not automatable or only partly automatable: perceived smoothness and readability of plots; dragging a floating plot window to a second monitor and restarting (layout restored); squeezed toolbar (STOP still visible at minimum width); 125 % / 150 % DPI; the physical Pause/Break key with another application focused, and with an elevated window focused (documented limitation KL-01); X-Y view during a real sequence (SW-RT-003); the full workflow against the simulator (SYS-008) and later on hardware; Manual tab enable/HOME/test zero/VALID (SW-MAN-006); NVM buttons and Save & reboot (SW-CFG-004); no-specimen banner visibility on every tab and on a second monitor (SW-LIM-004); sequence chart marker smoothness (SW-SCH-002). Each item references its requirement in the validator's procedure document.

---

## 11. Backend API — contract used and requests to Implementer B

### 11.1 Adoption of the API delta v0.1 → v0.2 (B §15.5)
| A | Change | GUI adoption | Section |
|---|---|---|---|
| A-01 | `pause(source) → StopResult`; `move`/`jog`/`home` REFUSE while PAUSED | Pause shows the `StopResult` like STOP; confirmation via `stop.*`; manual controls greyed by the gate | §2.2, §2.3, §3.4, §5.9 |
| A-02 | `resume(source) → GateResult`; `clear_stop_async` refused while a sequence is PAUSED; PAUSED/STOPPED end the jog session | Resume button / banner / Manual line; ClearStopDialog alternative row; HoldButton released state. Wire command is backend-internal: RESUME 0x3C per D-31 (GF-11) | §3.4, §5.6, §5.9 |
| A-03 | `record_stop() → GateResult` | refusals shown | §2.2 |
| A-04 | `hotkey_test_start()`, `HotkeyStatus`, `hotkey.test` | KEY chip modes, HotkeyTestDialog | §2.4, §5.3 |
| A-05 | all gate ids | GateBinder binds every control; fail-safe rule kept for older backends | §2.7 |
| A-06 | `Indicator(state, since_t_us, source, value, clear_hint)`, UNKNOWN, new items | chip table, UNKNOWN grey, generated-name map | §2.4 |
| A-07 | `config.check`, `reboot_async`, `REBOOT_REQUIRED` | live rule check, status column, Save & reboot | §3.1 |
| A-08 | `limits.recheck_async`, `ThresholdState.clamped` | Re-send & verify, clamped line | §3.2 |
| A-09 | `limits.set_no_specimen_mode`, `safety.no_specimen_mode`, `safety.no_specimen` | C-10, mode banner, NOSPEC chip, tags | §2.8, §3.2 |
| A-10 | `motion.check` | field colouring | §2.7, §3.4 |
| A-11 | `motion.reset_test_zero` | [Reset] | §3.4 |
| A-12 | `MotionLimits` split caps | caps label, field max | §3.4 |
| A-13 | `MoveDone` reasons + `stop_cause` | last-result line, event log | §3.4, §4.4 |
| A-14 | `PlotSnapshot`, `XYSnapshot`, `vstate`, `EXTRAPOLATED` | relative axis, styles, readout state | §4.3, §4.6 |
| A-15 | engine `PHASES`, `continue_label`, `continue_moves`, `abort_reason`, `start() → GateResult`, `finish_early`, `retake`, WARN → confirmation, PAUSE aborts | wizard frame, load wizard | §6 |
| A-16 | `tare_engine.undo`, `tare.can_undo` | [Undo tare] | §3.5, §6.4 |
| A-17 | `channels.changed` | tree update without polling | §4.2 |
| A-18 | `SeqStatus.plan_total_s`, `remaining_s`, `paused_source` | run line | §3.6 |
| A-19 | `reports.list_recordings`, `load_result`, `build_async(bend3p=)` | Report tab | §3.7 |
| A-20 | `sequencer.generator_schemas()` | generated forms | §6.5 |
| A-21 | `calibrations.restore_travel_async`, `travel_cal_differs`, `restore_pending`, `cal.travel.restore` | TCAL chip, notice strip, Calibration panel, `RESTORING` view | §2.8, §3.5, §6.2 |
| A-22 | `SimControl.act` etc., `test_hooks`, tcp simulator endpoint | test fixtures, perf runs | §10 |
| A-23 | `stop.confirmed/unconfirmed`, `safety.no_specimen`, `cal.travel.restore`, `marks.edited`, `hotkey.test` | bridge signals | §9.2 |
| A-24 | `reboot_pending`, `nvm_defaulted`, `enabling_left_ms`, `motion.paused` | CFG chip, notices, ENA countdown | §2.4, §2.8, §3.4 |
| A-25 | entry point; D provides `gui.app.run(backend, args) -> int` | `gui/app.py` | §8.1 |

### 11.1a Adoption of the API delta v0.2 → v0.3 (B §15.5a)
| B3 | Change | GUI adoption | Section |
|---|---|---|---|
| B3-01 | `resume()` = RESUME 0x3C, then re-issue (manual: RESUME only); refused RESUME reported | Resume buttons; flow text | §2.2, §3.6, §5.9 |
| B3-02 | `clear_stop_async(*, confirmed=False)`; CONFIRM "ends the paused sequence"; end reason CLEARED | C-13; ClearStopDialog row | §5.5, §5.6, §5.9 |
| B3-03 | `resume` gate REFUSE on HALT / ESTOP / fault | "Clear stop first" | §2.2, §5.9 |
| B3-04 | motion gates REFUSE while PAUSED; `MoveTicket` `REFUSED_PAUSED` (info), `REFUSED(nack)` | info row, no error toast | §2.7, §3.4 |
| B3-05 | `sequence_start` items PAUSED, POS_UNCERTAIN, AFE_RATE_MISMATCH, ALM, DRV_PWR_OFF | listed verbatim at Start | §3.6 |
| B3-06 | NOT_REACHED; end reasons DRIVER_ALARM, CLEARED | run line, step results, report, chart | §3.6, §3.7 |
| B3-07 | `Step.travel_bound_mm` removed; LOAD `step_time_s` not a timeout | column removed; LOAD time cell hidden | §3.6 |
| B3-08 | candidates 800 / 160; `expected_spm` 800 | C-05 text, wizard CHECK | §5.5, §6.2 |
| B3-09 | lockstep backend, test hooks, frame injection | test fixtures | §10.1 |
| B3-10 | `LinkStats.dup_frames`, `seq_anomalies`; DEGRADED from timeouts only | link line, LinkStatsDialog | §3.1 |
| B3-11 | `force_rate_n_s` NaN (GAP) after a gap | NO_DATA gap in plots (existing NaN / `vstate` handling) | §4.3 |
| B3-12 | staircase semantics | generator preview shows the backend result (no GUI logic) | §6.5 |
| B3-13 | jog refresh 80 ms (internal) | none | – |
| B3-14 | simulator default offset 50 000 counts | none (test data) | – |
| B3-15 | Python 3.14, requirements files, markers | GUI tests use the registered markers (`--strict-markers`) | §10.1 |
| B3-16 | gate item codes = `BLOCK_BITS` / `DATA_STATUS_BITS` names; SW items `GateCode` | `CLEAR_FIRST_CODES` | §2.7 |
| B3-17 | topic `resume.ignored` | bridge signal `resumeIgnored`, toast | §5.9, §9.2 |
| B3-18 | indicator keys = lower-case generated names | alias table removed | §2.4 |
| B3-19 | `resolve_travel_difference_async(action)`, `status().calibration.travel_diff` | TCAL buttons Restore / Keep board value / Ignore for this session | §2.8, §3.5 |
| B3-20 | `main()` builds the Backend unstarted, `args.endpoint`; `run()` starts / connects / shuts down; `sequencer.start(seq, *, confirmed=False)` | §8.1; C-07 call | §3.6, §8.1 |
| B3-21 | one name NOT_REACHED; `travel_bound_mm` removed; step error BOUND_NOT_AHEAD | step results | §3.6 |

### 11.1d Adoption of the API delta v0.3.3 → v0.4 (B §15.5d, M2 backend)

| B4 | Change | GUI adoption (this round: existing GUI correct; Manual tab = M3) |
|---|---|---|
| B4-01 | real MotionController (tickets, `GateRefused` futures, `disable` CONFIRMATION_REQUIRED, `home` ConfirmationRequired) | no Manual tab yet (M3); the fake keeps the Protocol shape (conformance test) |
| B4-02 | real motion gates; codes PC_LOAD_LIMITS_OFF (WARN), HOME_LOAD_CONFIRM / DISABLE_CONFIRM (CONFIRM), CONFIRMATION_REQUIRED, TARGET_OUT_OF_RANGE, SPEED_CAP, ACCEL_CAP, BOUND_NOT_AHEAD | gates drive enable states / tooltips unchanged (§2.7); **PC_LOAD_LIMITS_OFF of the `move` gate shown as a notice-strip row** (hidden in no-specimen mode) |
| B4-03 | `status().motion.jogging` / `home_phase` / `pos_uncertain` | MOV chip: "homing <phase>" / "jogging" / "moving"; HOMED chip keeps POS_UNCERTAIN from the indicator |
| B4-04 | `limits.set/check`, manual / default thresholds | Safety-limits tab = M3 |
| B4-05 | feature-dependent indicators UNKNOWN; `stop_btn` gone | chips grey "?", no "driver power lost" banner while DRV_PWR is UNKNOWN; STOP_BTN stays a retired table entry |
| B4-06 | `StopConfirmation` (no str equality) | banner reads `.cmd` (str still tolerated) — tested with the real type |
| B4-07 | real hotkey status + test mode | KEY chip / app-shortcut fallback follow `status().hotkey`; sim-backed GUI tests use `hotkey="off"` (no system hook), one test with `hotkey="fake"`; hotkey test dialog = M3 |
| B4-08 | `ChannelSpec.dimension` | used first for the pane quantity groups (§4.7); GRQ-B-21 closed |
| B4-09…11 | NVM quiesce, manual speed / accel session defaults, `test_hooks.hotkey_press` | no GUI change in this round |

### 11.1e Adoption of the API delta v0.4.1 → v0.5 (B §15.5e, M3 backend)

No Protocol signature change; the fake conformance test stays green. The GUI was written against the Protocols +
fakes first and then aligned to B's M3 working tree of 2026-10-05 (engines, session, limits, tare already wired).

| B5 | Change | GUI adoption | Section |
|---|---|---|---|
| B5-01 | `limits.set` validates the whole `LimitConfig`, stores it in the session, rewrites the FW thresholds on a level change | Safety-limits tab: [Apply] → issues per field (`Issue.key` = the `LimitConfig` field name; other keys in a general line); [Revert] re-reads `limits.get()` | §3.2, §15.2 |
| B5-02 | `set_no_specimen_mode` real; CONFIRM `NO_SPECIMEN_CONFIRM`; topic `safety.no_specimen` payload **bool** | C-10 always before `set_no_specimen_mode(True, confirmed=True)` (gate CONFIRM text shown, live threshold state); toasts on the bool payload | §2.8, §3.2 |
| B5-03 | motion gates: `PC_LOAD_LIMITS_OFF` gone; REFUSE `LOAD_INPUT_INVALID`, `THRESHOLDS_UNVERIFIED`, `OWNER_CONFLICT`, `SW_TRIP`; WARN `NO_SPECIMEN_MODE`, `SAF_SW_006_MARGIN`, `TRAVEL_CAL_DIFFERS` | verbatim: Manual-tab gate line / tooltips / parameter messages; limits-tab motion-disabled notice; first-use stop-banner row with [Enter no-specimen mode…]; wizard start page with [Enter no-specimen mode…] | §2.3, §3.2, §3.4, §6.1 |
| B5-04/05/06 | `SafetyStatus.sw_trip` / `trip: SwTrip`; `safety.trip` / `safety.warning` payloads | stop-banner SW-trip row uses `trip.text` + the clear procedure; `safety.trip` toast | §2.3 |
| B5-07 | tare engine real (phases, `TareResult`, refusals in `errors`, "large offset" warning) | TARE popup: refusal verbatim, progress + stats, [Repeat], DONE result line, [Keep] / [Undo tare], auto-close | §6.4 |
| B5-08 | `load_cal.start(*, n_points=3, presettle_s, capture_s, g)`; masses per point (`InputSpec mass_kg`); `LoadCalResult` (K, B, residuals, r2, NL, status, low_span; no point table) | start page: points / pre-settle / capture (session defaults), LOW_SPAN info box; FIT page: summary + **residual plot** (GRQ-B-25 for the point table); re-take menu from `step_count` | §6.3 |
| B5-09 | `travel_cal.start(*, v_mm_s=None)`; inputs `d1_mm` / `dtot_mm`; confirmation codes `SPM_CHANGE_20/5`, `SPM_EXPECTED`; `TravelCalResult` | generic page rendering: generated inputs, C-05 with the engine text, result summary from the dataclass, RESTORING page | §6.2 |
| B5-10 | calibration store real; `CalibrationStatus.load_invalid_reason / f_cal_max_n / load_file`; `TravelDiffState.source / ignored` | Calibration tab: active record verbatim, history list (SafeMessageBox), travel-difference buttons from `actions` | §3.5 |
| B5-11 | session real (`*.bbsession.json`); new `SessionSettings` fields (`cal_*`, `bend3p`, `compliance_mm_per_n`, `tare_window_s` 10 s) | File ▸ Open session… / Save session as… (SafeFileDialog, STOP inside); load-wizard defaults from `cal_presettle_s` / `cal_capture_s`; Manual speed / accel and the display unit stored in the session; 3-point-bend group on the Test-marks tab writes `bend3p` (enabled once `core.api` exports `Bend3pGeometry`, GRQ-B-24) | §2.5, §3.3, §3.4 |
| B5-12 | `marks.set` raises `ValueError` for empty / duplicate custom keys; presets in `<data>/presets` | the tab marks such rows red and does not send them; a `ValueError` is shown verbatim | §3.3 |
| B5-13 | `take_sample` real, `sample.taken` → `SampleRow` | toast "F̄ = … N, σ …, N …; x̄ …; raw … → file" | §2.2 |
| B5-14 | recording complete | no GUI change (REC chip / failure banner from M1) | – |
| B5-15 | force and derived channels with availability; EXTRAPOLATED `vstate` | readouts swap `F_N` / `F_kgf` with View ▸ Units; the X-Y pane switches y to force once available (unless the operator picked y) | §4.3, §4.7 |
| B5-16 | test zero resets work / peak | none | – |
| B5-17 | `sim.set_cell_load`, `set_afe_drift` (test only) | GUI simulator tests of the load wizard + tare (`test_sim_calibration.py`) | §10 |
| B5-18 | engine `start(..., confirmed=False)`: a start gate with CONFIRM items starts nothing and returns the gate | C-12 → `start(**config, confirmed=True)`; declining = nothing happens (the GRQ-B-23 cancel workaround removed) | §6.1 |
| B5-19 | `core.api` exports `Bend3pGeometry`, `SampleRow`, `SwTrip`, `SafetyWarning`, `TareResult`, `TravelCalResult`, `LoadCalResult` | 3-point-bend group always enabled (`Bend3pGeometry` imported from `core.api`) | §3.3 |
| B5-20 | `LoadCalResult.points` / `point_table()` | FIT page: per-point table (mass, force_n, raw_mean, residual_n) + mini plot raw → F with the fitted line; residual plot only when no points | §6.3 |
| B5-21/22 | `LimitsAPI` Protocol + manual / default thresholds; `limits.check(cfg)`; `marks.delete_preset(path)` | GUI fake implements `limits.check` and `marks.delete_preset` (OI-B-M3-04, so B can add them to the Protocol); no [Delete] preset button yet (M4) | §10 |
| B5-23/24 | new `dimension` values; simulator vocabulary v2 additions | none (quantity groups fall back to the unit) | – |

### 11.1f Adoption of the API delta v0.5.1 → v0.6 (B §15.5f, M4 backend)

The GUI was first built against the §10 / §11 design (Protocols + fakes) and aligned to B's §15.5f and the M4
working tree of 2026-10-05 (model, plan, generators, files, executor and reports wired in the Backend). All names land
in `gui/seq_access.py`. The GUI fake sequencer uses B's real pure code (`core.sequencer.model / plan / generators /
seqfile`) with a scripted executor; the fake conformance test covers the new Protocol members.

| B6 | Change | GUI adoption | Section |
|---|---|---|---|
| B6-01 | model types exported by `core.api` | `SeqTypes` takes `core.api.Step / Loop / StepKind` (fallback: type hints of `sequencer.new()`); no `on_trim_fail` | §3.6 |
| B6-02 | `SeqIssue(step_uid, field, …)` | cell colour + tooltip; loop / sequence issues (`loops[i]`, `travel_ref`, …) in the issue line under the table | §3.6 |
| B6-03 | `Plan`, `PlannedStep`, `PathPoint(…, capture, brk)` | plan summary (executed steps, total / ∞, windows, x / F range); chart breaks at `brk`, capture points from `capture` | §3.6 |
| B6-04 | `generate(name, params)`, schemas with `description`, `depends_on` | `GeneratorDialog` / `SchemaForm`; hidden fields are not passed | §6.5 |
| B6-05 | files v2 | Open / Save; FileFormatError → message, sequence unchanged | §3.6 |
| B6-06 | `check_start`, `start(confirmed)`, `abort(reason)`, `continue_`, `results` | Start flow with C-07; Continue in WAITING_OPERATOR; Abort | §3.6 |
| B6-07 | `SeqStatus` fields incl. `plan_len`, `marker_*` | run line; active row from `step_uid`; live marker | §3.6 |
| B6-08 | `StepResult` | message rows, NOT_REACHED cross; Report table | §3.6, §3.7 |
| B6-09 | `SeqWindow` (`seq.window`) | event log only | §4.4 |
| B6-10 | end reasons | verbatim in the run line ("end: …") and the "Sequence ended" row (red for ABORTED / ERROR / NOT_REACHED / DRIVER_ALARM) | §3.6 |
| B6-11 | `sequence_trace` in the sequence coordinate | chart trace | §3.6 |
| B6-12 | `sequence_start` / `sequence_edit` gates real | Start enable + tooltip; editor read-only while `sequence_edit` refuses | §3.6 |
| B6-13 | pause / resume / clear stop of a sequence | sequence Pause / Resume buttons (= toolbar); C-13 unchanged | §5.9 |
| B6-14 | reports API | Report tab; `bend3p=False` when unticked | §3.7 |
| B6-15 | `safety.trip_cleared` (`SwTripCleared`) | bridge maps it to `safetyEvent` (G-01b); toast info "SW limit cleared: <limit> – still latched: …" | §5.7 |
| B6-16 | session trim / k fields | not shown (session file only) | – |
| B6-17 | simulator specimen extras | not used by the GUI tests | – |

### 11.2 Contract used (summary by GUI element)
| GUI element | Backend API used | B § |
|---|---|---|
| start-up / close | `run(backend, args)`: `start()` before `show()`, `shutdown()`, `gui_beat()` every tick | 15.1, 15.4 |
| connection | `endpoints()`, `connect_async`, `disconnect_async`, `status().link` (state, why, compat, features, stats) | 5.2, 15.1–15.2 |
| STOP / HALT / Pause / Resume | `stop`, `halt`, `pause → StopResult`, `resume → GateResult`; topics `stop.*` | 5.5, 10.5, 15.1 |
| Clear stop | `clear_stop_async(confirmed=)`, `estop_clear_async(confirmed=)`, `fault_clear_async` → `ClearResult`; gates | 5.5–5.6 |
| indicators, banners | `status().indicators` (`Indicator` items), `status().safety`, `status().calibration`, `status().hotkey`, `status().reboot_pending/nvm_defaulted/cfg_dirty/config_read_only`; topics `indicators.changed`, `safety.*` | 6.5, 15.2–15.3 |
| gating | `status().gates` (all `GateId`s), `motion.check`, `config.check`; `PreconditionError` / `ConfirmationRequired` | 5.6, 14 |
| toolbar acquisition | `tare`, `take_sample`, `record_start/stop → GateResult`, `stream_start/stop_async`; `status().stream`, `status().recording`; topics `rec.*`, `sample.taken` | 8, 15.1 |
| Config tab | `config` (`metas/groups`, values, `check`, `write_and_verify_async`, `save/load/defaults_async`, `reboot_async`, `save_board_config` / `load_board_config → BoardConfigFile`) | 5.3 |
| Safety limits tab | `limits.get/set/thresholds/recheck_async/set_no_specimen_mode` | 6.1–6.3, 6.7 |
| Test marks tab | `marks` (get/set, presets), `session` (geometry); topic `marks.edited` | 8, 13.5 |
| Manual tab | `motion.move_to/move_by/jog_start/jog_update/jog_stop/enable/disable(confirmed=)/home(load_confirmed=)/set_test_zero/reset_test_zero/set_valid/limits/check`; `status().motion` | 5.4 |
| plots, readouts | `data.snapshot → PlotSnapshot`, `data.xy → XYSnapshot`, `data.latest → LatestSample`, `data.sequence_trace`; `channels`; topic `channels.changed` | 7.4–7.5, 15.6 |
| wizards, tare popup | `travel_cal`, `load_cal`, `tare_engine`: `PHASES`, `state()`, `subscribe`, `start → GateResult`, `continue_(inputs, confirmed=)`, `repeat`, `cancel`, `load_cal.finish_early/retake`, `tare_engine.undo`; `calibrations` (+ `resolve_travel_difference_async`, `travel_diff`) | 9 |
| Sequence tab | `sequencer`: new/load/save, `validate`, `expand`, `planned_path`, generators, `generator_schemas`, `insert_block`, `start(seq, *, confirmed=) → GateResult`, `stop`, `abort`, `continue_`, `status() → SeqStatus` | 10 |
| Report tab | `reports.list_recordings`, `load_result`, `build_async(dir, cal, tare, bend3p)`; topic `report.ready` | 11 |
| hotkey | `status().hotkey`, `hotkey_test_start`, topics `hotkey.*` | 15.6 |
| names | `core.protocol_gen` name tables (generated, Integrator) | ICD §0.3 |
| tests | `core.api` Protocols (fakes), `backend.sim` (`SimControl`), `test_hooks`, `wire_log` | 12.4, 19 |

### 11.3 Requests and clarifications to Implementer B
**GRQ-B-01…18: all closed** (accepted in B §15.7, adopted per §11.1).

| ID | Request | Why (GUI element) | MS | Status |
|---|---|---|---|---|
| GRQ-B-19 | **Acknowledge a travel-calibration difference** (B §9.3.1 names the operator actions [Keep board value] and [Ignore for this session], but §15 has no call for them): e.g. `calibrations.acknowledge_travel_difference(keep_board: bool) -> GateResult` — `keep_board=True` deletes the restore-pending record (someone else changed the board value on purpose), `False` suppresses the indicator / gate WARN for this session only (no write) | §2.8 notice strip, §3.5 | M3 | **closed** by B3-19 (`resolve_travel_difference_async("restore" / "keep_board" / "ignore_session")`) |
| GRQ-B-21 | **Quantity of a channel:** add `ChannelSpec.dimension: str` (e.g. "force", "length", "counts", "rate", "bits", "count", "state") so the default pane placement (SW-RT-006) does not have to infer the quantity from the unit / key; the GUI already prefers it when present | §4.7 | M3 | **closed** by B4-08 (`ChannelSpec.dimension`) |

**Clarifications (v0.2; GF-11, GF-12, GF-14, GF-17) — all answered by B3-01/03/16/17, B3-18, B3-20, B3-21:** `Backend.resume()` sends RESUME 0x3C (D-31) and the `resume` gate REFUSEs also for ESTOP / FAULT latched, with `GateItem.code` = generated `BLOCK_BITS` name; `Indicators` item names = lower-case generated names (`stop_btn`, `pause_btn`); `main()` constructs but does not start the Backend and exposes the endpoint for `run()`; engine `start(..., confirmed=True)` is the kwarg for start-gate CONFIRM items.

**M3 requests (2026-10-05) — to Implementer B:**

| ID | Request | Why (GUI element) | MS | Status |
|---|---|---|---|---|
| GRQ-B-23 | **Engine `start()` must honour `confirmed`** (B §15.4 rule 6, B3-20): when the start gate has CONFIRM items and `confirmed` is not True, `travel_cal.start` / `load_cal.start` should return the items **without starting**. Today they start at once and ignore `confirmed=True` (`**_kw`), so a repeated `start(confirmed=True)` hits "already running". The GUI is defensive meanwhile: C-12 is shown, declining it cancels the already started engine, confirming does not call `start` again | C-12 (wizard start; "no specimen mounted" when the load is unknown) | M3 | **closed** by B5-18 |
| GRQ-B-24 | **Export the new M3 types from `core.api`** (`Bend3pGeometry`, `SampleRow`, `SwTrip`, `SafetyWarning`, `TareResult`, `TravelCalResult`, `LoadCalResult`); the GUI imports only `core.api` (G-01). The 3-point-bend group stays disabled until `Bend3pGeometry` is exported | Test marks 3-point bend; toasts | M3 | **closed** by B5-19 |
| GRQ-B-25 | `LoadCalResult` (or the FIT `EngineState.stats`) to carry the **per-point table** (mass, F_ref, raw mean, residual) so the FIT page can show the points and the fitted line (§6.3); today only `residuals` exist → residual plot | load wizard FIT page | M3 | **closed** by B5-20 |
| GRQ-B-26 | `LimitsAPI` Protocol in `core.api`: add `check(cfg)`, `set_manual_thresholds_async(raw_min, raw_max, zero_raw=0)` and `set_default_thresholds_async()` (B4-04: implemented on the backend, not in the Protocol; the GUI uses them via `getattr`) | Safety-limits tab | M3 | **closed** by B5-21 (`check` joins the Protocol once B adds it; the fake has it) |
| GRQ-B-27 | `MarksAPI.delete_preset(path)` (§3.3 [Delete]); the GUI offers no Delete until it exists | Test-marks presets | M4 | **closed** by B5-22 (backend; GUI button M4) |
| OBS-D-M3-01 | (info, to B and F) A real `Backend` started by the GUI tests once read a session from the **default data directory** (travel limits 1…5 mm enabled, written by another test run). Every suite that builds a real `Backend` should set `data_dir` / `BEND_STAND_DATA_DIR` to a temporary directory; the GUI suite now does (conftest) | test isolation | M3 | info |

**M4 requests (2026-10-05) — to Implementer B:**

| ID | Request | Why (GUI element) | MS | Status |
|---|---|---|---|---|
| GRQ-B-28 | (low) `Sequence` helpers for the index-moving edits — `delete_steps(rows)`, `move_step(i, delta)`, `duplicate_steps(rows)` — so the loop-index bookkeeping lives in one place with `insert_block`; the GUI does it in `seq_access` meanwhile (pure, unit-tested) | Sequence editor | M4+ | open |
| GRQ-B-29 | (low) `ReportPaths.csv` (path of `data.csv`); the GUI derives `<folder>/data.csv` for [Open CSV] | Report tab | M4+ | open |
| GRQ-B-30 | (info) confirm the GUI's `bend3p` mapping: ticked → `Bend3pGeometry(L, b, h)`, unticked → `False`; the box is pre-ticked with the session geometry when one is set | Report tab | M4 | open |

**Remaining API gaps (M1–M4): none blocking.**


---

## 12. Requirement → GUI design traceability (SRS v0.4; IDs unchanged from v0.3)

Legend for "Share": **G** = GUI-owned; **S** = shared (the GUI triggers and displays; the backend implements the rule); **B** = backend rule (B §20); the GUI only shows the result. Verification: `G-nn` / `P-nn` = §10 tests; **D** = demonstration (§10.4); **I** = inspection.

### 12.1 SW, SAF-SW, NFR and GUI-facing SYS/IF requirements
| Req | GUI design element | Section | Module(s) | Share | Verification |
|---|---|---|---|---|---|
| SYS-003 | Units N/kgf selector; force axes/readouts relabel | §2.5, §4.3 | main_window, plots, readout | S | G-25 |
| SYS-008 | Endpoints "sim" / tcp twin / tcp simulator; full workflow through the GUI | §3.1, §10.2 | connection_tab, app | S | G-36, D |
| SYS-010 | Copied TS files carry an origin note with the copied commit hash (D-29 m) | §8 | all copied modules | G | I |
| IF-008 | RO chip + notice strip; read-only parameter form; motion gated | §2.4, §2.8, §3.1 | main_window, connection_tab, notice_strip | S | G-18 |
| IF-011 | STOP and PAUSE called synchronously, never through a queue (GUI part) | §5.1, §5.2 | stop.py, stop_button, pause_button | S | G-02, P-02 |
| SAF-SW-001 | Motion-disabled notice (gate REFUSE); SW-trip banner with value; first-use banner | §2.3, §2.7, §3.2 | gating, limits_tab, stop_banner | B | G-19, G-06, G-37 |
| SAF-SW-002 | THR chip incl. clamped; `ThresholdState` display; re-send | §2.4, §3.2 | indicator_bar, limits_tab | B | G-19, G-28 |
| SAF-SW-003 | LINK LOST banner and LINK chip; controls disabled | §2.3, §2.4, §5.7 | stop_banner, indicator_bar | B | G-34 |
| SAF-SW-004 | `ConfirmDialog` (no default, Enter/Space swallowed, confirm `NoFocus`, assertion checkbox, STOP); C-01 HOME under load, C-02 DISABLE, C-03 E-stop clear (button released, area safe; D-41), **C-10 no-specimen mode** (+ C-04…C-09, C-11, C-12) | §5.5, §5.6 | confirm_dialog, clear_stop_dialog | G | G-04, G-05, G-37 |
| SAF-SW-005 | `IndicatorBar` over generated names: ESTOP, HALT + source, PAUSED + source, LIM S/E, LOAD, AFE stale/sat/rate/NO_AFE_DATA, WDG, LINK, HOMED/POS_UNCERTAIN, ENA, DRV (DRV_PWR), ALM ("new motion blocked"), PEND, FAULT incl. HOME_DRIFT, K1 (K1_WELDED), CLK (CLK_FALLBACK), NOSPEC + mode banner; `clear_hint` per latch; UNKNOWN grey; 33 ms refresh | §2.3, §2.4, §2.8 | indicator_bar, indicator_map, status_help, mode_banner | S | G-06, G-39, G-44, G-37 |
| SAF-SW-006 | Speed-margin WARN (Manual fields via `motion.check`, sequence cells) and C-07 at sequence start | §3.4, §3.6, §5.5 | manual_tab, sequence_tab | B | G-15, G-30 |
| SW-PLT-001 | `gui.app.run(backend, args) -> int`; Windows 10; PySide6 + pyqtgraph; Python 3.14 runtime (D-33 i) | §8, §8.1 | app | G | G-43, D (install) |
| SW-PLT-002 | GUI imports only `core.api`, `core.protocol_gen` name tables, `calc.units`; no logic in the GUI | §1.2, §8 | all | G | G-01 |
| SW-PLT-003 | Connect flow; link statistics line + dialog | §3.1 | connection_tab, link_stats | S | G-17, G-36 |
| SW-CFG-001 | Dictionary-generated `ParamForm` (unit, range, enum names, board, default, edit, flags) | §3.1 | param_form | S | G-17 |
| SW-CFG-002 | Save/Load file, `BoardConfigFile` report, Edit column only | §3.1 | connection_tab, file_report | S | G-17 |
| SW-CFG-003 | Live `config.check`, Write & verify per-row status incl. REBOOT_REQUIRED; Save & reboot (C-11); session rows read-only and not in the file | §3.1 | param_form, connection_tab | S | G-17, G-41 |
| SW-CFG-004 | NVM buttons, Restore defaults (C-04), CFG chip (CFG_DIRTY, NVM_DEFAULTED) | §3.1, §2.4 | connection_tab | S | G-17, G-41, D |
| SW-LIM-001 | Travel limit fields; slider range; refused targets shown | §3.2, §3.4 | limits_tab, manual_tab | S | G-19, G-12 |
| SW-LIM-002 | Load limit fields, warning level, FW level; LOAD chip warning at 90 % | §3.2, §2.4 | limits_tab | S | G-19 |
| SW-LIM-003 | Session save/recall (File menu); limits in the automatic snapshot | §2.5, §3.3 | main_window, marks_tab | B | G-20 |
| SW-LIM-004 | No-specimen mode: enter via C-10 (gate `no_specimen`), leave button, persistent mode banner on every tab + tags in floating windows and dialogs, NOSPEC chip, board-threshold text, end at disconnect/exit shown | §2.8, §3.2, §5.5 | mode_banner, limits_tab, confirm_dialog, safe_dock, safe_dialog | S | G-37, G-04, G-34, D |
| SW-META-001 | Marks form + custom key/value (add/rename/remove) | §3.3 | marks_tab, marks_model | S | G-20 |
| SW-META-002 | Presets; read-only automatic snapshot view; edits during recording | §3.3 | marks_tab | S | G-20 |
| SW-RT-001 | `PlotDock`/`SafeDock` float/re-attach, ≥ 2 windows, STOP per window, layout persistence | §4.1, §4.5 | plot_dock, safe_dock, settings | G | G-02, G-22, D |
| SW-RT-002 | `ChannelTree` with all DATA fields, generated status bits, derived channels; greyed prerequisites; `channels.changed` | §4.2 | channel_tree | S | G-21, G-39 |
| SW-RT-003 | Time view (5–600 s, freeze, auto/manual Y) + X-Y view | §4.1, §4.3 | time_view, xy_view, autorange | G | G-23, D |
| SW-RT-004 | Derived channels listed from the registry (computation in `calc`) | §4.2 | channel_tree | B | G-21 (+ B vectors) |
| SW-RT-005 | Readouts with `LatestSample.state` | §4.3 | readout | S | G-24, D |
| SW-RT-006 | Pane grid 1–4 columns, + Pane / X-Y pane, plot in / move to pane, drag reorder, move to window, rename / close, quantity placement + empty-pane reuse (D-63), X link, layout persistence, one snapshot per time window | §4.7 | plot_dock, plot_pane, pane_grid, quantity, channel_tree, main_window | G | G-45…G-52, D |
| SW-MAN-001 | `TargetSlider`: drag sends nothing, one `move_to` on release, disabled un-homed | §3.4 | target_slider | G | G-12 |
| SW-MAN-002 | Go-to absolute/distance (`move_to` / `move_by`; arithmetic in the backend) | §3.4 | manual_tab | S | G-13, G-30 |
| SW-MAN-003 | ±0.1/1/10 mm buttons → `move_by`; commanded and pending target shown (latest wins) | §3.4 | step_buttons | S | G-13, G-30 |
| SW-MAN-004 | `HoldButton`: `jog_start` on press, `jog_stop` on release / focus loss; `gui_beat` keeps the backend refresh alive | §3.4, §9.3 | hold_button, refresh | S | G-14 |
| SW-MAN-005 | Speed/accel fields with `MotionLimits` caps and `motion.check`; STOP on the tab | §3.4 | manual_tab, unit_spin | S | G-15, G-02 |
| SW-MAN-006 | Enable (bound to FW, ENABLING countdown), HOME, test zero + reset, VALID toggle | §3.4 | manual_tab | S | G-16, D |
| SW-STOP-001 | STOP first in the toolbar, in every dock/dialog/wizard, `NoFocus`, synchronous, fires on press; native dialogs off | §2.2, §5.1–§5.4, §8.1 | stop_button, safe_dialog, safe_dock, app | G | G-02, G-03, G-43, P-02 |
| SW-STOP-002 | B's global hotkey + KEY chip modes + KL-01 text + app-level fallback + test mode | §5.3 | main_window, indicator_bar, hotkey_test | S | G-10, G-42, P-03, D |
| SW-STOP-003 | Reaction to HALT/ESTOP/driver-power loss from any source; ClearStopDialog (C-13 for a paused sequence); no auto-restart | §5.6, §5.7 | clear_stop_dialog, stop_banner | S | G-07, G-08, G-40 |
| SW-STOP-004 | Pause (PAUSE 0x3B → `StopResult`) / Resume (RESUME 0x3C, D-31) toolbar, sequence and manual controls; PAUSED chip with source; "Clear stop first"; RESUME_REQUEST / `resume.ignored` toasts; Clear stop of a paused sequence = C-13 (SRS v0.4 wording) | §2.2, §3.6, §5.9 | pause_button, sequence_tab, manual_tab | S | G-09, G-30, G-40 |
| SW-ACQ-001 | Stream toggle on the toolbar (every tab) | §2.2 | main_window | S | G-11 |
| SW-ACQ-002 | Record toggle (`GateResult`), REC chip | §2.2, §2.4 | main_window | B | G-11 (+ B tests) |
| SW-ACQ-003 | Take sample with window menu, result toast | §2.2 | main_window | S | G-11 |
| SW-ACQ-004 | Recording-failure banner, REC chip red; link stats counters | §2.3, §2.4 | stop_banner | B | G-33 |
| SW-CAL-001 | `SafeWizard` frame on `EngineState` (PHASES strip, inputs, Continue/Repeat/Cancel, progress, STOP); cancel keeps the calibration; `RESTORING` view; TCAL chip + notice + restore | §2.8, §3.5, §6.1, §6.2 | safe_wizard, travel_cal, notice_strip | S | G-26, G-27, G-38 |
| SW-CAL-002 | Travel wizard phases CHECK … RESULT (backlash, 10 mm, D1, 50 mm, D_tot); allowed in no-specimen mode | §6.2 | travel_cal | S | G-26, G-37 |
| SW-CAL-003 | Plausibility messages; expected 800 steps/mm (D-27 closed); C-05 shows 800 and 160 ("DIP change not applied") | §6.2, §5.5 | travel_cal, confirm_dialog | B | G-26 |
| SW-CAL-004 | Result/accept phase: board ✓, NVM, file | §6.2 | travel_cal | B | G-26 |
| SW-CAL-005 | Load wizard: zero + 2 weights, presettle + capture progress; allowed in no-specimen mode | §6.3 | load_cal | S | G-27, G-37 |
| SW-CAL-006 | Point result view with rejection reasons, Repeat | §6.3 | load_cal | B | G-27 |
| SW-CAL-007 | Fit view: residuals, R², NL_span, PASS/WARN/FAIL/UNVERIFIED, C-06, finish early, re-take | §6.3 | load_cal | B | G-27 |
| SW-CAL-008 | LOW_SPAN info/warning; "extrapolated" via `vstate` / `LatestSample.state` | §6.3, §3.5, §4.3 | load_cal, readout, plots | B | G-27, G-23, G-24 |
| SW-CAL-009 | Active-calibration panel (AFE match / "invalid for load limits"), history | §3.5 | calibration_tab | B | G-27 |
| SW-TARE-001 | TARE on the toolbar from every tab; non-modal popup; verbatim reasons | §2.2, §6.4 | main_window, tare_popup | S | G-11, G-28 |
| SW-TARE-002 | Popup progress, result, thresholds verified, Undo tare | §6.4 | tare_popup | B | G-28 |
| SW-TARE-003 | Refusal / warning states | §6.4 | tare_popup | B | G-28 |
| SW-SEQ-001 | Step table editor with typed fields and flagged invalid entries | §3.6 | sequence_tab, step_table_model | S | G-29 |
| SW-SEQ-002 | Loop gutter, wrap/unwrap, iteration display | §3.6 | step_table_model | S | G-29, G-30 |
| SW-SEQ-003 | Run line (`SeqStatus` phases, `plan_total_s`, `remaining_s`; executor in the backend) | §3.6 | sequence_tab | B | G-30 |
| SW-SEQ-004 | VALID shown in the indicator bar and lanes during capture | §2.4, §4.1 | indicator_bar, lanes | B | G-30 |
| SW-SEQ-005 | `sequence_start` REFUSE list at Start (incl. D-33 b reasons, B3-05) | §3.6 | sequence_tab | B | G-30 |
| SW-SEQ-006 | k_est / pull_dir settings (no on_trim_fail: removed by B6-01, SRS v0.6.2); APPROACH/TRIM phases; NOT_REACHED (D-32) shown | §3.6 | sequence_tab | B | G-30 |
| SW-SEQ-007 | Start/Pause/Resume/Stop/Abort controls; guard events shown | §3.6 | sequence_tab | S | G-30 |
| SW-WIZ-001 | Generator wizard G1–G3, 5 generators, forms from `generator_schemas()` | §6.5 | generator, schema_form | S | G-29 |
| SW-WIZ-002 | Insert append/after/replace (`insert_block`); editable afterwards; preview | §6.5 | generator, sequence_tab | G | G-29 |
| SW-SEQF-001 | Open/Save; `FileFormatError` keeps the current sequence | §3.6 | sequence_tab | S | G-29 |
| SW-SCH-001 | `SequenceChart` planned path with labels and capture points | §3.6 | sequence_chart | S | G-31, D |
| SW-SCH-002 | Live marker (line + point), active step, measured trace, ≥ 10 Hz | §3.6 | sequence_chart | G | G-31, D |
| SW-REP-001 | Report tab: list, Generate, Open HTML/CSV/folder | §3.7 | report_tab | B | G-32 |
| SW-REP-002 | Step result table display (incl. NOT_REACHED) | §3.7 | report_tab | B | G-32 |
| SW-REP-003 | Re-apply calibration/tare options | §3.7 | report_tab | B | G-32 |
| SW-REP-004 | 3-point-bend options (`bend3p`, geometry from the session) | §3.3, §3.7 | marks_tab, report_tab | B | G-32 |
| NFR-001 | Performance design §4.6 (one view per window, explicit ranges, relative time axis, PreciseTimer, GC policy) | §4.6, §9.4 | plots, refresh, gc_policy | S | P-01, P-04 |
| NFR-002 | STOP / PAUSE on press, synchronous, tick budget ≤ 30 ms | §4.6, §5.2 | stop_button, refresh | S | P-02 |
| NFR-003 | Hotkey thread (backend), independent of the GUI; test mode | §5.3 | – (backend), hotkey_test | B | P-03, G-42 |
| NFR-004 | GUI memory/GC behaviour over 1 h; deadlock stress | §9.4 | gc_policy | S | P-05, G-35 |

**Coverage (M3 as-built map: §12.3):** all **61** SW-* requirements of SRS v0.5.1 (incl. SW-LIM-004 and SW-RT-006, D-38), all 6 SAF-SW requirements, NFR-001…004 and the GUI-facing SYS-003, SYS-008, SYS-010, IF-008 and IF-011 (= 75 IDs) have a GUI design element and at least one GUI test, demonstration or inspection. NFR-005…008 are FW-only.

### 12.2 FW / IF features surfaced in the GUI (display only)
| Req | GUI element |
|---|---|
| FW-AFE-004 | AFE chip with measured rate and AFE_RATE_MISMATCH state |
| FW-SW-003 | BTN chip (STOP_BTN, PAUSE_BTN); event toasts |
| FW-SW-004 | ALM ("new motion blocked" while powered) and PEND chips |
| FW-SW-005, SAF-FW-024 | DRV chip, "driver power lost" banner |
| SAF-FW-025 | K1 chip, K1_WELDED banner |
| SAF-FW-026 | ALM chip red with driver power; motion gates refuse |
| FW-HOM-004 | FAULT chip "HOME_DRIFT n µm"; HOMED event drift text |
| FW-PLT-002 | CLK chip (CLK_FALLBACK, GET_STATUS/EVENT only, KL-04) |
| SAF-FW-023 | PAUSED chip with source; Pause/Resume flow §5.9 |
| FW-NVM-001 | CFG chip (CFG_DIRTY, NVM_DEFAULTED) |
| FW-CFG-004 | Device info line, About dialog (versions, hash, UID, feature names) |
| FW-CMD-004 | LinkStatsDialog (GET_STATUS counters) |
| FW-STR-005 | NO_AFE_DATA bit channel; NO_DATA gaps in plots |
| FW-HOM-002 | homing fault banners and `clear_hint`; HOME_FAILED reason names |
| SAF-FW-021 | C-01 sends the operator-confirmed flag (`load_confirmed=True`) |

### 12.3 M3 verification map (as built, 2026-10-05)

Tests are in `03_SW/tests/gui/`; `fake` = FakeBackend, `sim` = B's real Backend + in-process simulator (lock-step).

| Req | Tests (file :: test) |
|---|---|
| SW-MAN-001 | test_manual_tab :: test_slider_drag_sends_nothing_release_sends_one_move, test_slider_mouse_handle_drag_and_groove_click, test_slider_disabled_by_move_gate · test_sim_manual :: test_manual_slider_jog_test_zero |
| SW-MAN-002 | test_manual_tab :: test_goto_and_step_buttons, test_ticket_outcomes_shown · test_sim_manual :: test_manual_enable_home_steps_goto |
| SW-MAN-003 | test_manual_tab :: test_goto_and_step_buttons · test_sim_manual :: test_manual_enable_home_steps_goto |
| SW-MAN-004 | test_manual_tab :: test_hold_to_jog_press_release, test_jog_stops_once_on_focus_loss[5 cases], test_gui_beat_every_tick · test_sim_manual :: test_manual_slider_jog_test_zero |
| SW-MAN-005 | test_manual_tab :: test_speed_accel_caps_and_margin_warning, test_jog_speed_above_unhomed_cap_refused, test_stop_on_manual_tab |
| SW-MAN-006 | test_manual_tab :: test_enable_checkbox_follows_fw, test_disable_needs_c02, test_home_confirm_c01, test_test_zero_and_valid, test_position_force_readouts · test_sim_manual (enable, HOME, test zero) |
| SW-LIM-001 | test_limits_marks_tabs :: test_limits_apply_sends_config, test_limits_issues_shown_per_field · test_sim_manual :: test_limits_tab_applies_travel_limits |
| SW-LIM-002 | test_limits_marks_tabs :: test_limits_apply_sends_config, test_limits_issues_shown_per_field, test_limits_in_kgf |
| SW-LIM-003 | test_m3_main_window :: test_session_open_save · test_limits_marks_tabs :: test_marks_snapshot_and_recording_footer |
| SW-LIM-004 | test_limits_marks_tabs :: test_no_specimen_mode_c10, test_no_specimen_refused_and_first_use_banner · test_calibration_tare :: test_start_page_checklist_and_refusals · test_sim_manual (C-10 on the real backend) · test_m3_main_window :: test_event_toasts_sample_mode_trip |
| SW-META-001 | test_limits_marks_tabs :: test_marks_fields_and_custom, test_marks_debounced_apply |
| SW-META-002 | test_limits_marks_tabs :: test_marks_presets_round_trip, test_marks_snapshot_and_recording_footer |
| SW-REP-004 (entry) | test_m3_main_window :: test_bend3p_geometry_to_session |
| SW-ACQ-003 | test_main_window :: test_record_tare_sample_on_every_tab · test_m3_main_window :: test_event_toasts_sample_mode_trip |
| SW-CAL-001 | test_calibration_tare :: test_every_engine_phase_has_a_view, test_start_page_checklist_and_refusals, test_start_confirm_c12, test_travel_wizard_phases, test_wizard_abort_page_and_stop, test_close_is_cancel_with_c08_past_first_phase, test_close_in_first_phase_cancels, test_c12_declined_cancels_an_engine_that_already_started, test_calibration_panels_and_travel_actions · test_sim_calibration :: test_travel_wizard_on_simulator |
| SW-CAL-002…004 | test_calibration_tare :: test_travel_wizard_phases · test_sim_calibration :: test_travel_wizard_on_simulator |
| SW-CAL-005…007 | test_calibration_tare :: test_load_wizard_start_config, test_load_wizard_capture_evaluate_fit, test_load_fit_residual_view, test_result_parts_generic · test_sim_calibration :: test_load_wizard_tare_force_on_simulator |
| SW-CAL-008 | test_calibration_tare :: test_load_wizard_start_config, test_calibration_panels_and_travel_actions · test_manual_tab :: test_position_force_readouts · test_m3_main_window :: test_units_menu_switches_readouts |
| SW-CAL-009 | test_calibration_tare :: test_calibration_panels_and_travel_actions · test_sim_calibration (active calibration after accept) |
| SW-TARE-001…003 | test_calibration_tare :: test_tare_popup_refusal_verbatim_every_tab, test_tare_popup_progress_done_undo, test_calibration_tab_tare_and_undo · test_sim_manual :: test_m3_actions_follow_the_real_backend · test_sim_calibration :: test_load_wizard_tare_force_on_simulator |
| SW-RT-003 | test_m3_main_window :: test_xy_pane_uses_force_when_calibrated · test_sim_calibration |
| SW-RT-005 | test_m3_main_window :: test_units_menu_switches_readouts · test_sim_calibration |
| SW-STOP-001 | test_m3_main_window :: test_stop_in_every_m3_window, test_wizards_non_modal_toolbar_usable · test_calibration_tare :: test_wizard_abort_page_and_stop, test_tare_popup_refusal_verbatim_every_tab · test_manual_tab :: test_stop_on_manual_tab · test_sim_manual :: test_m3_actions_follow_the_real_backend |
| SW-STOP-002, NFR-003 | test_m3_main_window :: test_hotkey_test_dialog |
| SW-STOP-004 | test_manual_tab :: test_paused_line_resume_without_motion, test_ticket_outcomes_shown |
| SAF-SW-001 | test_limits_marks_tabs :: test_thresholds_display_resend_manual_default, test_no_specimen_refused_and_first_use_banner · test_m3_main_window :: test_event_toasts_sample_mode_trip |
| SAF-SW-002 | test_limits_marks_tabs :: test_thresholds_display_resend_manual_default |
| SAF-SW-004 | test_confirm_dialog :: test_keyboard_never_confirms[C-01…C-15] · test_manual_tab :: test_disable_needs_c02, test_home_confirm_c01 · test_limits_marks_tabs :: test_no_specimen_mode_c10 · test_calibration_tare :: test_start_confirm_c12, test_travel_wizard_phases (C-05), test_load_wizard_capture_evaluate_fit (C-06) · test_load_cal_d50 :: test_k_implausible_needs_its_own_confirmation (C-14) |
| SAF-SW-005 | test_m3_main_window :: test_k1_chip_hidden_while_check_disabled |
| SAF-SW-006 | test_manual_tab :: test_speed_accel_caps_and_margin_warning |
| SYS-003 | test_m3_main_window :: test_units_menu_switches_readouts, test_xy_pane_uses_force_when_calibrated · test_limits_marks_tabs :: test_limits_in_kgf |
| SW-RT-001 (prefs) | test_m3_main_window :: test_last_tab_and_unit_restored |

---

### 12.4 M4 verification map (as built, 2026-10-05)

`fake` = FakeBackend with the fake sequencer over B's pure sequencer code; `sim` = B's real Backend + in-process
simulator (lock-step).

| Req | Tests (file :: test) |
|---|---|
| SAF-SW-005 / SAF-SW-001 (MC3-5) | test_m4_sequence :: test_trip_announcement_new_trip_vs_clear, test_trip_clear_toast_in_window, test_trip_latched_before_window_is_not_announced_again · Validator F test_v_gui_m3 :: test_tc_saf_sw_005_06 (`--runxfail`) |
| SW-SEQ-001 | test_m4_sequence :: test_sequence_tab_present_with_stop, test_typed_steps_and_applicability, test_cell_edit_and_backend_validation, test_kind_change_resets_target, test_insert_duplicate_delete_reorder_undo, test_settings_row_edits_sequence, test_edit_refused_by_sequence_edit_gate, test_issue_mapping_pure · test_sim_sequence :: test_travel_sequence_runs_on_simulator |
| SW-SEQ-002 | test_m4_sequence :: test_loops_wrap_unwrap_and_bookkeeping, test_loop_dialog_count_and_stop, test_loop_bookkeeping_pure · test_sim_sequence (loop ×2 on the wire) |
| SW-SEQ-003 | test_m4_sequence :: test_run_controls_and_run_line · test_sim_sequence (run to FINISHED, active rows) |
| SW-SEQ-005 | test_m4_sequence :: test_start_refused_items_listed_one_per_reason, test_start_with_confirm_items_needs_c07, test_start_warn_only_needs_c07_too · test_sim_sequence (C-07 → confirmed start) |
| SW-SEQ-006 / SW-SEQ-007 | test_m4_sequence :: test_not_reached_and_driver_alarm_shown, test_run_controls_and_run_line, test_abort_and_until_stopped_remaining · test_sim_sequence :: test_sequence_pause_resume_and_stop_on_simulator |
| SW-STOP-004 | test_m4_sequence :: test_run_controls_and_run_line, test_resume_refused_shows_clear_stop_first · test_sim_sequence :: test_sequence_pause_resume_and_stop_on_simulator (PAUSE / RESUME on the wire) |
| SW-WIZ-001 / SW-WIZ-002 | test_m4_sequence :: test_generator_form_from_backend_schema_and_insert, test_generator_value_error_shown_verbatim, test_generator_replace_and_insert_modes |
| SW-SEQF-001 | test_m4_sequence :: test_save_open_round_trip, test_invalid_file_keeps_current_sequence, test_new_with_unsaved_changes_needs_c08 |
| SW-SCH-001 | test_m4_sequence :: test_chart_shows_backend_planned_path_with_labels |
| SW-SCH-002 | test_m4_sequence :: test_chart_live_marker_active_step_and_trace, test_chart_marker_rate_at_least_10_hz (real timer) · test_sim_sequence (marker updates during the run) |
| SW-REP-001…004, SW-ACQ-002 | test_m4_report :: test_recordings_list_and_step_results, test_result_table_follows_display_unit, test_build_with_reapplied_cal_tare_and_bend, test_open_html_in_system_browser, test_build_failure_and_report_ready_refresh · test_sim_sequence (report built at the end, listed, step table filled) |
| SAF-SW-004 (C-07, C-08) | test_confirm_dialog (keyboard rules for every TEXTS id incl. C-07) · test_m4_sequence (C-07 / C-08 flows) |
| SW-STOP-001 (new windows) | test_m4_sequence :: test_sequence_tab_present_with_stop, test_loop_dialog_count_and_stop, test_generator_form_from_backend_schema_and_insert |

## 13. GUI preferences (GQ-01…20) — decided by the PO (D-32, Q27: defaults accepted)

| ID | Topic | Decision |
|---|---|---|
| GQ-01 | Tab names and order | Connection & Config · Safety limits · Test marks · Manual · Calibration & Tare · Sequence · Report; last tab restored at start |
| GQ-02 | Default force display unit | **N** (kgf selectable in View ▸ Units) |
| GQ-03 | Travel shown in readouts and X axes | **test travel**, machine coordinate as a secondary value |
| GQ-04 | Default plot layout, maximum plot windows | Plot 1 docked right (time + X-Y), Plot 2 tabified (time); max **4** windows |
| GQ-05 | GUI language | English only |
| GQ-06 | Colour theme | light (high-contrast safety colours); dark optional |
| GQ-07 | Manual slider | horizontal, range = SW travel limits (else FW soft limits), one move on release, groove click does nothing |
| GQ-08 | Keyboard jog with ←/→ | **off** (on-screen hold buttons only) |
| GQ-09 | Sequence start without the system-wide Pause/Break key | allowed after an explicit confirmation (C-07, `sequence_start` CONFIRM); KEY chip red; the key then works only while the app is focused |
| GQ-10 | Wizards modal or non-modal | **non-modal**, always on top, STOP in every wizard |
| GQ-11 | TARE chip turns amber after | 30 min |
| GQ-12 | Missing specimen name/number at record start | warning only |
| GQ-13 | HTML report | opens in the system browser ([Open HTML]) |
| GQ-14 | Closing the application while the driver holds a load | STOP, end the recording, keep the driver holding (D-13); no DISABLE at exit |
| GQ-15 | Tare with a large-offset warning | keep, show [Undo tare] (A-16) |
| GQ-16 | Manual "Go to" field and Enter | Enter commits the value; the move needs a click on [Go] |
| GQ-17 | Lab PC screen | design target 1600 × 900 at 125 %; usable at 1366 × 768 (scroll areas) |
| GQ-18 | On-screen button label | **STOP** (E-stop = the red E-stop button, MCU / FW stop since D-41) |
| GQ-19 | Additional in-app keyboard STOP key | none: only Pause/Break and Ctrl+Break (HALT, system-wide) |
| GQ-20 | STOP acts on mouse press or release | on **press** |

Related PO answers used elsewhere (D-32): Q28 — the Pause/Break limitation for elevated windows is accepted (KL-01, shown in the KEY tooltip, Help and the hotkey test, §5.3); Q26 — a load-target step that does not reach its target ends NOT_REACHED and the sequence stops (shown in §3.6, §3.7). **No open PO question remains for the GUI.**

---

## 14. Findings for other roles

| ID | To | Finding | Status |
|---|---|---|---|
| GF-01 | Integrator (C) | GUI Pause = FW PAUSE command; PAUSED source; PAUSED clear command. | **Closed**: D-29 a, D-30, D-31; ICD v0.3 PAUSE 0x3B, `pause_src`, EVENT PAUSED arg. RESUME 0x3C → GF-16. |
| GF-02 | Orchestrator | Native file dialogs disabled so STOP is in every dialog. | **Closed**: SRS v0.3 SW-STOP-001. |
| GF-03 | Orchestrator → PO | Pause/Break not delivered to elevated windows. | **Closed**: SRS v0.3 KL-01, accepted by the PO (D-32 Q28); shown in §5.3. |
| GF-04 | Orchestrator | SW-MAN-003 latest wins. | **Closed**: D-29 i, SW-MAN-003 v0.3. |
| GF-05 | Orchestrator + B | Active travel calibration and restore after aborts. | **Closed**: SW-CAL-001 v0.3, B §9.3.1; residual operator actions → GRQ-B-19. |
| GF-06 | Orchestrator | Ownership of `SW_design_GUI.md`, `gui/app.py`. | **Closed**: role file, D-29 n. |
| GF-07 | Orchestrator | First-use flow without calibration/tare. | **Closed**: D-29 h, SW-LIM-004; GUI §2.8, §3.2. |
| GF-08 | Integrator (C) | Generated status-bit names for the GUI and the indicator registry. | **Closed**: `core/protocol_gen.py`; GUI iterates it (P8, G-39). |
| GF-09 | Validator F | Inputs for the SW test plan: demonstration items (§10.4), perf tests P-01…P-05 (§10.3), new G-37…G-44; P-02/P-03 use `wire_log`; reference PC = PO lab PC (D-33 j). Review SWD-P1-05 (a)–(g) addressed in v0.2; v0.3 updates G-07, G-18, G-29, G-30, G-38, G-39, G-40, G-43 for B3-xx. | **Open (info)** → Validator F |
| GF-10 | B (info) | Jog refresh in the backend; `ConfirmDialog` for all safety confirmations. | **Closed**: B §15.7. |
| GF-11 | Implementer B | **D-31 alignment (no API change):** (a) `Backend.resume()` sends **RESUME 0x3C** instead of HALT_CLEAR (F-B-30 switch; also the §15.1 comment and §5.5/§10.5 text); (b) the `resume` gate REFUSEs also while ESTOP or any FAULT is latched (D-31 E_STATE), not only HALT; (c) REFUSE items that mirror the FW BLOCK mask keep `GateItem.code` = the generated `BLOCK_BITS` name (HALT, ESTOP, FAULT, PAUSED, …) — the GUI keys its "Clear stop first" hint on it; (d) publish "resume request ignored: <reason>" on a stable topic (proposal: `log` record with code `RESUME_IGNORED`). | **Closed**: B3-01, B3-03, B3-16, B3-17 |
| GF-12 | Implementer B | `Indicators` item names: B §6.5 uses `stop_button` / `pause_button`, the generated names are STOP_BTN / PAUSE_BTN. Please name every DATA/STATUS/FAULT/SYS item `name.lower()` (or add `Indicators.by_name(gen_name)`), so the GUI needs no alias table. | **Closed**: B3-18 |
| GF-13 | Implementer B | **GRQ-B-19** acknowledge a travel-calibration difference ([Keep board value] / [Ignore for this session], B §9.3.1). | **Closed**: B3-19 |
| GF-14 | Implementer B | **A-25 entry contract:** `__main__.main()` constructs the Backend and does **not** call `start()`; `gui.app.run(backend, args)` starts it before the window is shown and calls `shutdown()` at exit; please expose the endpoint chosen by `--sim` / `--port` as one field (proposal `args.endpoint: str \| None`). Also confirm `engine.start(..., confirmed=True)` as the kwarg for start-gate CONFIRM items (C-12). | **Closed**: B3-20 |
| GF-15 | Orchestrator (SRS v0.4) | SW-STOP-004 still says "the accepted motion command clears PAUSED" / manual "PAUSED shown until the next accepted motion command" — v0.4 should state D-30/D-31: PAUSED blocks motion, Resume = RESUME (refused while HALT/ESTOP/fault latched), Clear stop (HALT_CLEAR) clears HALT + PAUSED. SW-SEQ-005 should list the D-33 b reasons; SW-SEQ-006 the D-32/D-33 d NOT_REACHED rule. The GUI design already follows the decisions. | **Closed**: SRS v0.4 (SW-STOP-004, SW-SEQ-005) |
| GF-16 | Integrator (C) | **ICD v0.4 for D-31:** add RESUME 0x3C to `protocol.yaml` (Cmd, `CMD_PRIORITY`, retry class), PAUSE_CLEARED reason RESUME, and update the generated descriptions that the GUI shows as operator tooltips: `DATA_STATUS_DESC['PAUSED']`, `BLOCK_DESC['PAUSED']`, `PAUSE_CLEARED_REASON_DESC` still say "cleared only by HALT_CLEAR". With D-27 closed, `params.yaml` `motion.steps_per_mm` default 160 → 800 (the GUI wireframes already show 800). | **Closed**: ICD v0.4 / regenerated `protocol_gen.py` (RESUME 0x3C, PAUSED descriptions), `params.yaml` default 800 |
| GF-17 | Implementer B | D-32 / D-33 d: the load approach bound is the nearer of the soft limit and an enabled SW travel limit. The editor shows the `Step.travel_bound_mm` column only as long as the backend model keeps the field; please state in B §10.1 whether it stays (as an optional tighter bound) or is removed. Step flag name `NOT_REACHED` (B §10.4 currently `LOAD_NOT_REACHED`) should be one name in step results and report. | **Closed**: B3-07, B3-21 |

---

## 15. M1 work breakdown — GUI (P2 start after the PO gate)

**M1 GUI scope:** connect; Connection & Config tab with read / check / write+verify / NVM save-reload-defaults / save-load file / reboot; minimal realtime plot (time view of DATA channels and status bits, floating with STOP); stream start/stop; STOP and toolbar skeleton (all toolbar items present and gate-driven; motion-related items stay disabled while their gates are absent — the fail-safe rule of §2.7); indicator bar with the generated-name map; event log. M2–M4 items (Manual tab, limits, wizards, sequence, report, X-Y view, layout persistence) build on these modules.

**M1 SRS IDs (GUI part):** SW-PLT-001 (entry), SW-PLT-002, SW-PLT-003, SW-CFG-001…004, SW-ACQ-001, IF-008 (display), SYS-008 (GUI on the simulator), SYS-010 (origin notes); early parts of SW-STOP-001, SW-RT-001…003, SAF-SW-004 (dialog rules) and NFR-001 (smoke).

**Entry conditions:** P1 gate passed; B's WP-B0 delivered (`core.api` Protocol stubs, `__main__` hand-off with an unstarted Backend and `args.endpoint` (B3-20), `tests/conftest.py` with the D-06 port guard, registered markers (B3-15)); `core/protocol_gen.py` and `params_gen.py` generated and `--check` green for the ICD version frozen for M1; `.venv` per CLAUDE.md.

| WP | Content (modules) | Depends on | Tests (all `@pytest.mark.req`, `# Verifies:`) | Req | Size |
|---|---|---|---|---|---|
| **WP-D0** harness | `gui/__init__.py`; `tests/gui/conftest.py` (offscreen before Qt import, qtbot helpers); `tests/gui/fakes.py` FakeBackend over the `core.api` Protocols (status/gates/indicators scripting, EventBus, `PlotSnapshot` generator); fake-conformance test | WP-B0 | G-01 (layering), fake conformance | SW-PLT-002 | S |
| **WP-D1** safety widgets + dialog base | `theme.py`, `stop.py`, `widgets/stop_button.py`, `widgets/safe_dock.py`, `dialogs/safe_dialog.py` (SafeDialog, SafeMessageBox, SafeFileDialog), `dialogs/confirm_dialog.py` (rules + C-04, C-11), origin notes (TS copies) | WP-D0 | G-02 (widgets), G-03, G-04 (C-04, C-11) | SW-STOP-001 (part), SAF-SW-004 (rules), SYS-010 | M |
| **WP-D2** entry + main-window skeleton | `app.py` `run(backend, args)` (§8.1), `settings.py`, `gc_policy.py`, `bridge.py` (all topics of B §15.3 mapped or ignored), `refresh.py` (PreciseTimer, `gui_beat`, `perf_stats`), `main_window.py` (toolbar with STOP functional; Pause/Resume, Clear stop, TARE, Stream, Record, Sample bound to gates), `widgets/stop_banner.py` (StopResult, `stop.*`), `widgets/notice_strip.py` (compat, read-only, NVM defaulted, reboot pending), `widgets/event_log.py` (generated event decoding), menus skeleton, About | WP-D1 | G-43, G-02 (toolbar, main window), G-03, G-11 (Stream on every tab), G-01b | SW-PLT-001, SW-STOP-001 (part), SW-ACQ-001 | L |
| **WP-D3** indicator bar | `indicator_map.py` (generated names, polarity, UNKNOWN), `widgets/indicator_bar.py`, `widgets/status_led.py`, `dialogs/status_help.py` | WP-D2 | G-39, G-44 (UNKNOWN part), `indicator_map` unit tests for every name | SAF-SW-005 (part), GF-08 | M |
| **WP-D4** connection | `tabs/connection_tab.py` (endpoint selector incl. sim / tcp endpoints, Connect/Disconnect, device line with feature names, compat, link line), `widgets/endpoint_selector.py`, `dialogs/link_stats.py` | WP-D2 | G-18, connect part of G-36 (fake) | SW-PLT-003, IF-008 | M |
| **WP-D5** configuration | `widgets/param_form.py` (dictionary editors, flags M/N/R, locked session rows), live `config.check`, Write & verify statuses incl. REBOOT_REQUIRED, `dialogs/file_report.py`, file save/load, NVM buttons (C-04), Save & reboot (C-11), CFG chip binding | WP-D4 | G-17, G-41 | SW-CFG-001…004 | L |
| **WP-D6** minimal realtime plot | `plots/plot_dock.py` (SafeDock: STOP, float/re-attach; Plot 1 only), `plots/time_view.py` (`PlotSnapshot` columns, relative axis, explicit ranges, `vstate` styles), `plots/autorange.py`, `plots/axes.py`, `plots/lanes.py` (status bits), `widgets/channel_tree.py` (registry, `channels.changed`), `widgets/readout.py` (raw, rate) | WP-D2 | G-21 (DATA + bits), G-22 (float / STOP), G-23, G-24 (raw, rate), `autorange` vectors | SW-RT-001…003 (part), SW-RT-005 (part) | L |
| **WP-D7** integration with the real backend | `sim_backend` fixture; end-to-end: `python -m bend_stand --sim` → GUI connects, stream on/off, plot raw + bits, edit / check / write+verify / save NVM / save + load file / reboot against the simulator; perf smoke | WP-B11 (+ WP-B10 snapshot, WP-B9 config), WP-D3…D6 | G-36 (M1 part), G-17/G-18/G-41 (sim variants), P-01 smoke 60 s, P-02 smoke (STOP latency, informative) | SYS-008, SW-PLT-003, SW-CFG-001…004, SW-ACQ-001, NFR-001 (smoke) | M |
| **WP-D8** M1 close-out | demonstration of SW-CFG-004 and the connect/plot flow with the simulator (Validator F procedure); `SW_design_GUI` updated to as-built (deviations listed); M1 test report inputs (counts, perf smoke numbers) | WP-D7 | – (D items) | SW-CFG-004 (D) | S |

**Order and parallelism:** WP-D0 → WP-D1 → WP-D2 → (WP-D3 ∥ WP-D4 ∥ WP-D6) → WP-D5 (after WP-D4) → WP-D7 → WP-D8. WP-D0…WP-D6 run against the FakeBackend as soon as B's WP-B0 exists, in parallel with WP-B1…B10; WP-D7 starts when B's WP-B11 is green.

**M1 exit criteria (GUI part):** all M1 tests above green offscreen in fixed and random order (`-p randomly`, two seeds); every M1 GUI test carries a `req` marker (`pytest --collect-only` report); layering test (G-01) and generated-name test (G-39) green; no COM port opened by any test (B's guard); the perf smoke (P-01, 60 s) recorded (informative in M1, binding NFR-001 at M3); `python -m bend_stand --sim` demonstrably connects, streams, plots and writes/verifies/saves a parameter; STOP visible in the main window, Plot 1 (docked and floating) and every M1 dialog.

### 15.1 As-built status (M1, WP-D0…WP-D7, 2026-10-03)

| WP | State | Notes / deviations from §2–§9 |
|---|---|---|
| D0 | done | `tests/gui/fakes.py` FakeBackend; `test_fake_conformance.py` checks every `core.api` Protocol (isinstance + identical signatures) |
| D1 | done | `theme`, `stop`, `mode_state` (process-wide NO-SPECIMEN display state), `StopButton`, `SafeDock`, `SafeDialog` / `SafeMessageBox` / `SafeFileDialog`, `ConfirmDialog` (+ `TEXTS` for C-03, C-04, C-09, C-11, C-13). **D-36:** banner / tooltip texts point to "the red E-stop button" (no physical holding STOP/BREAK); `STOP_BTN` has no chip (retired, `RETIRED_NAMES`), BTN chip = PAUSE_BTN |
| D2 | done | `app.run`, `settings`, `gc_policy` (copy), `bridge` (all 34 topics mapped), `refresh` (PreciseTimer 33 ms, `perf_stats`), `main_window` (toolbar order fixed, Clear-stop dialog with B31-01 NOT_CONFIRMED text, app-level Pause / Ctrl+Break only while the global hotkey is UNAVAILABLE), stop banner, notice strip, mode banner, event log, About. **Layout deviation:** the banner stack is a second full-width top toolbar row and the indicator bar sits in the bottom toolbar area (both fixed, not movable, no context menu) so they span the docks; a toast line was added under the banners. Tabs 2–7 are placeholders (M2–M4). Hotkey test dialog: M3 (menu entry present). Smoke seam: `args.quit_after_ms` / env `BEND_STAND_GUI_QUIT_AFTER_MS`, exit summary logged at INFO |
| D3 | done | `indicator_map` (table over every generated name, `SW_ITEMS`), `indicator_bar`, `status_led` (copy), `status_help`. Display rule added: latch source `NONE` (generated `SOURCE_NAMES[0]`) is not shown |
| D4 | done | `connection_tab`, `endpoint_selector`, `link_stats` |
| D5 | done | `param_form` (metadata from `config.metas()`; locked rows from `config.locked_keys()`; rule marking from `config.check`), `file_report`; Save & reboot = C-11 → `save_async` → `reboot_async` |
| D6 | done | `plot_dock` (Plot 1 only; tree + time view + lanes; first start ticks `raw`), `time_view`, `lanes`, `autorange`, `axes`, `channel_tree`, `readout`. Bit channels recognised by the registry key convention `bit.<name lower>` (B's `core.channels`). X-Y view, Plot 2, layout persistence, units: M3 |
| D7 | done (early) | `test_sim_integration.py` on B's real `Backend` + in-process simulator; smoke `python -m bend_stand --sim` offscreen exits 0 after streaming and plotting |
| D8 | open | Validator F demonstration, M1 test-report inputs |

GUI imports (G-01): `core.api`, `core.protocol_gen`, `calc.units` and — deviation — the package root `bend_stand` (``__version__`` in About).

### 15.2 M3 work breakdown — GUI (SW application, D-44)

**M3 GUI scope:** Manual tab on the real `MotionController`; Safety limits tab (SW travel / load limits, warning
level, FW level ≤ 110 % FS, FW thresholds incl. manual / default bring-up paths, no-specimen mode with C-10); Test
marks tab (marks, custom fields, presets, 3-point-bend geometry, snapshot view); Calibration & Tare tab; travel and
load calibration wizards on the backend engines; TARE popup (toolbar, every tab); Record / Take sample wired with
the M3 payloads; readouts with force N / kgf and EXTRAPOLATED; X-Y pane with force when calibrated; Pause/Break key
test dialog; session open / save; K1 chip hidden while `drv.k1_check_enable` is false (M2 gate condition).

**M3 SRS IDs (GUI part):** SW-MAN-001…006, SW-LIM-001…004, SW-META-001/002, SW-ACQ-002/003 (toolbar part),
SW-CAL-001…009 (wizard frame and display), SW-TARE-001…003, SW-RT-003/005 (force, X-Y), SW-STOP-001 (every new
window), SW-STOP-002 (test dialog), SAF-SW-001/002/004/005/006 (display + confirmations), SYS-003, NFR-001…003
(smoke; binding runs on the reference PC by Validator F).

| WP | Content (modules) | Tests | Req |
|---|---|---|---|
| WP-D9 manual | `tabs/manual_tab.py`, `widgets/target_slider.py`, `widgets/hold_button.py`, C-01 / C-02 texts | `test_manual_tab.py` (G-05, G-12…G-16), `test_sim_manual.py` | SW-MAN-001…006, SAF-SW-004/006 |
| WP-D10 limits + marks | `tabs/limits_tab.py`, `tabs/marks_tab.py`, C-10 | `test_limits_marks_tabs.py` (G-19, G-20, G-37), `test_sim_manual.py::test_limits_tab_applies_travel_limits` | SW-LIM-001…004, SW-META-001/002, SAF-SW-001/002 |
| WP-D11 wizards + tare | `wizards/safe_wizard.py`, `phase_views.py`, `travel_cal.py`, `load_cal.py`, `dialogs/tare_popup.py`, `tabs/calibration_tab.py`, C-05 / C-06 / C-08 / C-12 | `test_calibration_tare.py` (G-26…G-28, G-38 tab part), `test_sim_calibration.py` | SW-CAL-001…009, SW-TARE-001…003 |
| WP-D12 main window | `main_window.py` (tabs, units menu, session menu, tare popup, wizard / dialog refresh, K1 params), `units_state.py`, `dialogs/hotkey_test.py`, `widgets/readout.py` (unit swap), `plots/plot_dock.py` / `plot_pane.py` (X-Y force preference), `indicator_map.py` (K1), `widgets/stop_banner.py` (first-use action, SW trip text) | `test_m3_main_window.py` (G-02 M3, G-24/G-25, G-42, K1, session, toasts) | SYS-003, SW-RT-003/005, SW-STOP-001/002, SAF-SW-005 |
| WP-D13 close-out | fakes extended (motion, limits, marks presets, scriptable engines), conftest isolation (data dir, display unit), this document | GUI suite fixed + random order, sim smoke | – |

### 15.3 As-built status (M3, WP-D9…WP-D13, 2026-10-05)

| WP | State | Notes / deviations from §2–§9 |
|---|---|---|
| D9 | done | Manual tab as §3.4. **Deviations:** the slider and the absolute Go-to work in the **machine** coordinate (the API target unit; labelled "machine mm"), readouts show test and machine travel; accel field "FW default" = 0 → `None`; the field check calls `motion.check` (MOVE with speed / accel, JOG with the jog speed, MOVE with the absolute target) on every edit and every 3rd tick; Go / steps / slider are refused locally with a toast while a speed / accel field is red. Hold-to-jog stops exactly once on release, application inactive, window deactivate / hide, tab change, the button becoming disabled (gate), or when the backend ends the jog session (`motion.jogging` True → False while held, e.g. STOP / PAUSE). Enable checkbox = FW state only (P4). HOME: C-01 from the gate CONFIRM item or from a `ConfirmationRequired` ticket |
| D10 | done | Safety limits as §3.2 (+ manual raw / default threshold buttons, B4-04, via `getattr`). Load fields follow View ▸ Units (unapplied edits re-expressed). Test marks as §3.3 with debounced apply (300 ms) — **no [Delete] preset** (GRQ-B-27); 3-point-bend group enabled (`Bend3pGeometry` from `core.api`, B5-19) |
| D11 | done | `SafeWizard` as §6.1: a start page (gate checklist, kind config, [Start], [Enter no-specimen mode…]) replaces a separate CHECK view; the page is rendered generically from `EngineState` with a phase → view-kind table (`phase_views.VIEWS`, completeness-tested against the fake and B's real `PHASES`); result display is generic (`result_parts`: dataclass / mapping summary + first list of mappings as table); fit page = `LoadCalResult.point_table()` + mini plot raw → F with the fitted line (B5-20); residual plot only without points. A reopened wizard whose engine shows an earlier DONE / ABORTED starts on the start page. Close / Esc = Cancel, C-08 past the first phase. C-12 → `start(confirmed=True)` (B5-18; the earlier GRQ-B-23 cancel workaround is removed). Load wizard start = `n_points` / pre-settle / capture (B5-08; masses per point, not at start as §6.3 drafted). Tare popup as §6.4 (+ DONE result line) |
| D12 | done | View ▸ Units N / kgf (QActionGroup; stored in `SessionSettings.display_unit` and `ui/units`); last tab restored (`ui/last_tab`); File ▸ Open session… / Save session as…; Tools ▸ Test Pause/Break key… = `HotkeyTestDialog`; the current tab is refreshed every tick (other tabs on activation), open wizards / popup / hotkey test every tick; closed windows are never reused (WA_DeleteOnClose). K1 chip hidden while `drv.k1_check_enable` = false (board values read every 10th tick); an active K1_WELDED is never hidden |
| D14 MC3-4 | done | **LINK LOST diagnostics (OBS-M3-R1):** every transition to LINK LOST shown by the GUI logs (WARNING + Event-log row `LINK_LOST_DIAG`, list `MainWindow.link_lost_diagnostics`) the link `why`, the longest GUI refresh-tick gap of the last 10 s (`RefreshScheduler.max_gap`) with p95 / max interval, and the last / longest cyclic-GC collection of the process (`gc_policy.GC_TRACE`, a `gc.callbacks` recorder: generation, duration, age, thread). The real-clock perf smoke (`test_perf_smoke_on_simulator`) now runs against the **out-of-process simulator** (`python -m bend_stand.io.sim.server --port 0 --ctl 0`, started / stopped by the fixture by its own process handle) and reports the diagnostics if the stream gate fails |
| D13 | done | 258 GUI tests (70 new); fake has `limits.check` / `marks.delete_preset` (OI-B-M3-04), step-rate cap 50 mm/s (dict 6, 40 kHz); conftest sets `BEND_STAND_DATA_DIR` per test (OBS-D-M3-01) and resets the display unit |

**Evidence (2026-10-05, offscreen):** GUI suite `pytest tests/gui -p no:randomly` 258 passed; `-p randomly`
(seed 1099176525) 258 passed; smoke `python -m bend_stand --sim` (offscreen, `BEND_STAND_GUI_QUIT_AFTER_MS=8000`,
hotkey off, temp data dir) exit 0: link CONNECTED sim, 80.4 SPS, 0 lost / 0 CRC, 241 ticks p50 33.0 ms / p95 33.5 ms.
Simulator-backed GUI tests (lock-step clock): enable → HOME (C-01) → +1 mm ×3 → absolute targets on the wire; slider
release = exactly one MOVE_ABS; hold-to-jog = JOG + backend refreshes + JOG 0; no-specimen mode via C-10; travel
wizard to DONE (800 steps/mm on the board) and a cancelled run restoring spm0; load wizard (zero + 1 kg + 10 kg) to
DONE, tare DONE, F_N readout ≈ 98 N with 10 kg, X-Y pane on force.

### 15.4 M4 work breakdown — GUI (Sequencer, D-46)

**M4 GUI scope:** MC3-5 / SWD-M3-02 first (trip-clear toast); Sequence tab (step table editor, loops, insert /
duplicate / delete / reorder, per-cell validation from the backend, file save / recall, run controls with gates and
C-07 / C-08 / C-13, run line); generator wizard (forms from `generator_schemas()`, preview, append / insert / replace);
sequence chart (planned path + labels, live marker ≥ 10 Hz, active step, measured trace, NOT_REACHED marks); Report
tab (recordings, step results, build CSV + JSON + HTML with optional 3-point bend, re-apply calibration / tare, open
HTML in the system browser). B publishes the M4 API delta (`SW_design.md` §15.5f) in parallel: the GUI is built
against the `core.api` Protocols and the §10 / §11 design through **one adapter module** (`gui/seq_access.py`) so
that B's final names land in one place.

**M4 SRS IDs (GUI part):** SW-SEQ-001…007 (editor, controls, run display), SW-STOP-004 (sequence Pause / Resume),
SW-WIZ-001/002, SW-SEQF-001, SW-SCH-001/002, SW-REP-001…004 (Report tab), SAF-SW-004 (C-07, C-08, C-13),
SAF-SW-005 / SAF-SW-001 (MC3-5).

| WP | Content (modules) | Tests (`@pytest.mark.req`) | Req |
|---|---|---|---|
| WP-D15 MC3-5 | `main_window.py`: `trip_announcement` (new trip → error "… STOP sent"; clear → info "SW limit cleared"; B's clear signal or the remaining-latch publish of B5-25), trips latched before the window are seeded from `status().safety.trips`; `event_log` clear row = info | `test_m4_sequence.py::test_trip_clear_*`; Validator F `test_tc_saf_sw_005_06` with `--runxfail` | SAF-SW-005, SAF-SW-001 |
| WP-D16 adapter + model | `seq_access.py` (type resolution `core.api` exports → `typing.get_type_hints` of `sequencer.new()`; issue → cell mapping; generator call; plan / path / status / report normalisation), `models/step_table_model.py` (columns, applicability table, display units, edit → `dataclasses.replace`, loop gutter) | G-29 model part | SW-SEQ-001/002 |
| WP-D17 Sequence tab | `tabs/sequence_tab.py`: toolbar (New / Open / Save / Save as / Generate / + Step / Duplicate / Delete / Up / Down / Loop / Unloop / Undo / Redo), settings row (travel ref, pull dir, k_est, defaults), validation (debounced 200 ms, cell colours + tooltips, badge), run controls (Start → C-07, Pause, Resume, Continue, Stop, Abort), run line + messages, read-only while `sequence_edit` refuses | G-29, G-30 (fake) | SW-SEQ-001…007, SW-SEQF-001, SW-STOP-004, SAF-SW-004 |
| WP-D18 generator wizard | `wizards/schema_form.py` (FieldSpec → editor, min / max / default, `depends_on`), `wizards/generator.py` (`GeneratorDialog(SafeDialog)`, pages G1…G3, preview table + mini chart, insert mode) | G-29 wizard part | SW-WIZ-001/002 |
| WP-D19 chart | `plots/sequence_chart.py` (planned path polyline with HOME breaks, labels, capture points, estimated segments dashed, active step, trace, live marker, NOT_REACHED cross; explicit ranges; ≥ 10 Hz from the main refresh) | G-31 (+ rate test) | SW-SCH-001/002 |
| WP-D20 Report tab | `tabs/report_tab.py` (recordings list, result summary + warnings, step result table, options: re-apply calibration file / tare raw, 3-point bend L / b / h, [Build report] → `reports.build_async`, [Open HTML] via `QDesktopServices`, [Open folder] / [Open CSV], `report.ready` refresh) | G-32 | SW-REP-001…004 |
| WP-D21 tests + sim | fake sequencer / reports per B §10–§11 (`tests/gui/fakes.py`); `test_m4_sequence.py`, `test_m4_report.py`; sim-backed end-to-end once B's engine is in the tree (`test_sim_sequence.py`, skipped otherwise) | GUI suite fixed + random order, sim smoke | SYS-008 |
| WP-D22 close-out | this document (§3.6 / §3.7 as built, §11.1f adoption of B §15.5f, §11.3 requests, §12.4 M4 verification map, history) | – | – |

**Order:** WP-D15 → WP-D16 → (WP-D17 ∥ WP-D18 ∥ WP-D19 ∥ WP-D20) → WP-D21 → WP-D22.
**M4 exit criteria (GUI part):** suite green fixed and random order; MC3-5 reproducer passes with `--runxfail`;
every new control gate-driven (no rule logic in the GUI, P1); STOP in every new window / dialog; sim smoke exit 0.

### 15.5 As-built status (M4, WP-D15…WP-D22, 2026-10-05)

| WP | State | Notes / deviations |
|---|---|---|
| D15 MC3-5 | done | `trip_announcement` / `trip_key` (pure) in `main_window.py`; `safety.trip_cleared` mapped in the bridge (B6-15); a remaining-latch republish and payload None are also treated as clears (B5-25 behaviour); 2 s grace before an announced identity leaves the seen set |
| D16 adapter + model | done | `seq_access.py`, `models/step_table_model.py` (§3.6 as built) |
| D17 Sequence tab | done | §3.6 as built |
| D18 generator wizard | done | `GeneratorDialog(SafeDialog)` (not a `SafeWizard`: no engine behind it) with a `QStackedWidget` G1…G3; Enter never inserts |
| D19 chart | done | `plots/sequence_chart.py` |
| D20 Report tab | done | §3.7 as built |
| D21 tests | done | `fake_sequencer.py` (B's pure code + scripted executor), `test_m4_sequence.py` (30), `test_m4_report.py` (5), `test_sim_sequence.py` (2, B's real sequencer on the lock-step simulator) |
| D22 doc | done | this version |

**Evidence (2026-10-05, offscreen):** GUI suite `pytest tests/gui -p no:randomly` **297 passed**; `-p randomly`
(seed 1186900966) **297 passed** (a further random run: 297 passed). Validator F
`tests/validation/test_v_gui_m3.py --runxfail` 5 passed (TC-SAF-SW-005-06 passes; without `--runxfail` the strict
xfail now XPASSes → F removes the marker). SW-SCH-002 marker repaint with the real 33 ms timer ≈ 30 Hz (≥ 10 Hz
asserted). Sim end-to-end (lock-step): a travel sequence of 3 steps with a ×2 loop built in the tab → C-07 →
FINISHED, MOVE_ABS = x_zero + (2, 4, 2, 4, 1) mm, report built and listed with its step table; Pause → PAUSE,
Resume → RESUME, Stop → STOPPED. Smoke `python -m bend_stand --sim` (offscreen, `BEND_STAND_GUI_QUIT_AFTER_MS=8000`,
hotkey off, temp data dir) exit 0: link CONNECTED sim, 80.4 SPS, 0 lost / 0 CRC, 242 ticks p50 33.0 ms / p95 33.6 ms.

---

## 16. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-03 | Implementer D | First draft for the P1 gate: main window, toolbar, indicator bar, 7 tabs, realtime plot design with the NFR-001 performance plan, stop handling, confirmation rules (C-01…C-09), wizard phase tables (travel, load, tare, generators), wireframes, module layout, Qt adapter and threading rules, GUI test strategy (G-01…G-36, P-01…P-05), traceability, 20 PO questions, 10 findings. Aligned with `SW_design.md` v0.1 §15 (18 requests GRQ-B-01…18), B's F-B-21 and the Orchestrator decision "GUI Pause = FW PAUSE command". |
| 0.2 | 2026-10-03 | Implementer D | Aligned to SRS v0.3, ICD v0.3 + generated `protocol_gen.py`, `SW_design.md` v0.2 §15 (A-01…A-25 adopted, GRQ-B-01…18 closed) and D-29…D-33. **New:** §0 summary; P8 generated names (indicator map, channel tree, event log, "Clear stop first" codes); Pause → `StopResult`, Resume via RESUME 0x3C (D-31) with "Clear stop first", Clear stop clears HALT + PAUSED, `clear_stop` refused while a sequence is PAUSED, Pause/Resume flow §5.9; no-specimen mode (C-10, mode banner on every tab, tags in floating windows and dialogs, NOSPEC chip, Safety-limits group); travel-calibration restore (TCAL chip, notice strip, `RESTORING` view); indicators as `Indicator` objects with UNKNOWN and new chips DRV, K1, FAULT (incl. HOME_DRIFT), CLK, NO_AFE_DATA in AFE, MOV; ALM text "new motion blocked while powered" (D-28, D-33 c); config `check`, REBOOT_REQUIRED, Save & reboot (C-11), NVM defaulted; engine PHASES / `continue_label` / `continue_moves` / `abort_reason` / `start() → GateResult` (C-12), load-wizard finish-early and re-take, tare undo; `remaining_s`, report list/load, generator schema forms, hotkey test mode (KL-01 text), `channels.changed`, `PlotSnapshot`/`vstate`; entry point `gui.app.run(backend, args) -> int` (§8.1); travel calibration expected 800 steps/mm, 160 only as "DIP change not applied" in C-05 (D-27 closed, SW-CAL-003); sequence start reasons D-33 b; NOT_REACHED (D-32, D-33 d). Tests G-37…G-44 added, G-02…G-36 updated, milestone column. Traceability: 60/60 SW incl. SW-LIM-004, 6/6 SAF-SW, NFR-001…004, SYS-003/008/010, IF-008/011. GQ-01…20 decided (D-32 Q27). GF-01…08, 10 closed; GF-09 open (info); new GF-11…17; new request GRQ-B-19 (only remaining API gap, M3, non-blocking). **M1 work breakdown WP-D0…WP-D8 (§15).** Validator review SWD-P1-05 (a)–(g) addressed. |
| 0.3 | 2026-10-03 | Implementer D | Final P1 alignment to `SW_design.md` v0.3 §15.5a (B3-01…B3-21, new §11.1a), SRS v0.4 (same IDs) and ICD v0.4: Resume = RESUME + re-issue (manual RESUME only), `resume.ignored` toasts; Clear stop of a paused sequence = new C-13 (`clear_stop_async(confirmed=True)`, end reason CLEARED); `REFUSED_PAUSED` shown as info; sequence-start items DRV_PWR_OFF / ALM / PAUSED / POS_UNCERTAIN / AFE_RATE_MISMATCH; end reasons NOT_REACHED / DRIVER_ALARM / CLEARED, step error BOUND_NOT_AHEAD; travel-bound column removed, LOAD step time hidden; link counters `dup_frames` / `seq_anomalies`; indicator alias table removed (lower-case generated keys); TCAL actions via `resolve_travel_difference_async` (GRQ-B-19 closed); entry contract with `args.endpoint`; `sequencer.start(seq, confirmed=True)`; lockstep test hooks. GF-11…17 closed (GF-15 by SRS v0.4, GF-16 by ICD v0.4). Remaining API gaps: none. |
| 0.3.1 | 2026-10-03 | Implementer D | M1 implementation WP-D0…WP-D7: as-built table §15.1 (layout deviation banner/indicator toolbars, D-36 texts, B31-01 adopted, `NONE` source display rule, smoke seam, package-root import). |
| 0.4.1 | 2026-10-04 | Implementer D | **M2 backend alignment (B §15.5d, B4-01…11, new §11.1d) and D-41** (E-stop = MCU / FW stop, no K1 contactor): E-STOP banner, C-03, STOP tooltip and §1.3 / GQ-18 wording; PC_LOAD_LIMITS_OFF notice; MOV homing / jogging; feature-bit UNKNOWN; real `StopConfirmation`; `dimension` for pane grouping (GRQ-B-21 closed); real hotkey status. |
| 0.6 | 2026-10-05 | Implementer D | **M4 (Sequencer) GUI as built (§15.4 work breakdown WP-D15…D22, §15.5 as-built + evidence):** MC3-5 / SWD-M3-02 (a new SW trip toasts "… STOP sent" as error; a clear — topic `safety.trip_cleared` (B6-15), payload None or the remaining-latch publish — toasts info "SW limit cleared"; trips latched before the window are seeded; the event-log row of a clear is info); Sequence tab (step table over B's `Sequence`: typed steps, applicability table, N / kgf load cells, backend validation per cell + badge, loop bracket in the row header, Loop… / Unloop, + Step / Duplicate / Delete / Up / Down, Undo / Redo snapshots, settings name / travel ref / pull dir / k_est / step defaults; files with C-08 and "current sequence unchanged" on a bad file; run controls Start (check_start → C-07 → start(confirmed=True)) / Pause / Resume / Continue / Stop / Abort + STOP; run line; step results with NOT_REACHED red; read-only while running or while `sequence_edit` refuses); generator wizard G1…G3 from `generator_schemas()` (SchemaForm with `depends_on`, ValueError verbatim, preview table + mini chart + plan summary + issues, append / insert / replace with C-08); SequenceChart (planned path with HOME breaks, labels, capture points, estimated segments dashed, live marker + point from `SeqStatus.marker_*`, active step, trace, NOT_REACHED cross; ≈ 30 Hz); Report tab (recordings, result summary + warnings, step result table, re-apply calibration file / tare raw, 3-point bend, build, open HTML / folder / CSV). §11.1f adopts B §15.5f; §11.3 M4 requests GRQ-B-28…30; §12.4 M4 verification map. Tests: 297 GUI tests (37 new, 2 sim end-to-end on B's real sequencer) fixed + random order; sim smoke. |
| 0.5 | 2026-10-05 | Implementer D | **M3 (SW application) GUI as built (§15.2 work breakdown WP-D9…D13, §15.3 as-built + evidence):** Manual tab on the real MotionController (slider one move on release, go-to absolute / distance, ±0.1/1/10 mm latest-wins, hold-to-jog with exactly-once JOG 0 on release / focus loss / hide / gate close / backend end, speed / accel / jog fields checked by `motion.check`, enable bound to the FW state, DISABLE C-02, HOME C-01, test zero, VALID, PAUSED line + Resume, STOP); Safety limits tab (travel / load limits, warning level, FW level, thresholds incl. clamped, re-send, manual / default thresholds, motion-disabled notice, no-specimen mode C-10); Test marks tab (marks, custom fields, presets, 3-point bend, snapshot, recording footer); Calibration & Tare tab; `SafeWizard` + travel / load wizards over B's engines (start page, generic page, C-05 / C-06 / C-08 / C-12, finish early, re-take, abort / restoring pages, phase-view completeness); TARE popup; Pause/Break key test dialog; View ▸ Units N / kgf (readouts, X-Y force, limits fields); File ▸ Open / Save session; first-use banner [Enter no-specimen mode…]; SW-trip banner text from `SwTrip`; K1 chip hidden while `drv.k1_check_enable` false (M2 gate condition). §11.1e adopts B §15.5e B5-01…17; new requests GRQ-B-23…27, OBS-D-M3-01 (§11.3); §12.3 M3 verification map; K1 row of §2.4; §8 module list. Tests: 258 GUI tests (70 new) fixed + random order; sim smoke. |
| 0.6.2 | 2026-10-10 | Implementer D | **D-54 (PO answers, SRS v0.6.7).** (b) **NFR-009**: new §4.8 — X-Y point budget `render.xy_points` (2 per pixel column), unchanged-curve skip in `FastCurve.set_xy`, render backend selectable (View ▸ OpenGL rendering, `ui/opengl`, env `BEND_STAND_GUI_OPENGL`, hook `plot_dock.set_opengl`) with raster fallback, default raster (GL viewport measured 3–5 × slower); measurements and requests to B in §4.8. (c) Report tab **Change…** next to the recordings-folder line → B's `reports.set_root(path)` (refusal in a warning box + message, disabled while recording; folder chooser `safe_dialog.get_existing_directory`, non-native with STOP). (a) Travel wizard: `HOME` phase (move page), topic `safety.no_specimen_scope` mapped, mode banner / NOSPEC chip ("NO-SPECIMEN (wizard)") / window tags follow `status().safety.no_specimen_scope` (no [Leave] for the scope), toasts on scope start / end; start CONFIRM → C-12 (assertion). Tests `test_render_nfr009.py` (8), `test_folder_picker_d54c.py` (5), `test_travel_cal_d54a.py` (3, one on B's backend + simulator). Manual §5 / §8 / §13 / §16 / §17 (confirmed alarm reset, D-42 check interval), screenshots 08–10, 33. |
| 0.6.1 | 2026-10-08 | Implementer D | **Operator documentation (Task 2b, PO approved):** `03_SW/docs/USER_MANUAL.md` (safety first: E-stop vs STOP vs Pause/Break vs PAUSE, latches and clears, KL-01…KL-09; installation; first start; no-specimen mode; homing; manual control; travel / load calibration incl. LOW_SPAN; tare; limits; marks; plots; recording; sequences incl. load-target steps, NOT_REACHED and guards; reports incl. offline rebuild and 3-point bend; troubleshooting; chip appendix), `QUICK_REFERENCE.md` (one page), 40 screenshots `docs/img/*.png` generated offscreen from the real GUI on the lock-step simulator by `tests/gui/manual_screenshots.py` (not a pytest module; loads the Windows fonts for the offscreen platform). New test module `tests/gui/test_user_manual.py` (5 tests): every toolbar action (both states), menu action, tab, indicator chip, latch name and Clear-stop button is named in the manual; stop vocabulary on the quick-reference card; every linked image exists. **Display fixes:** tab labels escape "&" (Qt mnemonic swallowed it: "Connection Config"); chips DRV / HOMED / ENA / PEND show `on` / `homed` / `enabled` / `in position` in their good state instead of the bit name; a step result without a capture window no longer shows "F̄ nan N". **OI-UM-05 (Orchestrator follow-up):** (a) the first-use banner joins the gate text and its clear hint with " – " (`stop_banner.with_hint`); (b) ● Record start is disabled (tooltip / toast "not connected — connect to the board first …") while the link is not CONNECTED / DEGRADED, because B's `record_start` gate only WARNs "stream off"; stopping a running recording stays possible (request GRQ-B-31 a: refuse in the gate); (c) Report tab: read-only **Recordings folder** + [Open recordings folder] (QDesktopServices via `open_url`; no control that changes it); path = `recordings_root_of()` (GUI mirror of the backend precedence, layering rule G-01 forbids `core.paths`; GRQ-B-31 b: public `reports.root()`). New tests `tests/gui/test_oi_um_05.py` (6). Manual §14 / §16 / §18 updated, screenshots regenerated. **F-B-PKG-01:** application / window / taskbar icon from the package resource `bend_stand/gui/resources/BirdBendStand.ico` (copy of `packaging/assets/BirdBendStand.ico`; `gui.resources.app_icon()`), set by `gui.app.run` (`QApplication.setWindowIcon`) and on `MainWindow`; tests `test_app_icon.py` (3) + icon check in `test_app.py`. B: PyInstaller `datas` and setuptools `package-data` must include `bend_stand/gui/resources/*.ico`. **Follow-ups:** the Report tab uses B's public `reports.root()` (GRQ-B-31 b; local lookup only as a fallback; GUI fake `FakeReports.root()`); §3.6 sequence model no longer lists `on_trim_fail` (sequence file format v2); the run line shows the label of the running step from the same `SeqStatus` snapshot as step / phase (B publishes phase COMMAND + label together at every step start, also after Resume), only while the run is active — test `test_step_label_and_phase_follow_status_without_stale_label` (no stale label, active row never cleared between steps or across PAUSED / Resume). `test_chart_marker_rate_at_least_10_hz` marked `rt` (verdict on the reference PC); GUI conftest flushes DeferredDelete before every test (closed main windows had piled up, ~1050 top-levels per module) and the sequence-tab fixture closes its dialogs. **D-50 a / SRS v0.6.5 SW-CAL-007 (load wizard):** "weight not detected" is shown as the engine's point error (red, Repeat / Continue with another mass; no GUI rule); FIT confirmation code `K_IMPLAUSIBLE` → new **C-14** ("K implausible — accept anyway?", engine text, assertion "I checked the weights, the cell and the AFE gain") via `SafeWizard.CONFIRM_CIDS` (WARN fit keeps C-06); Continue with a pending confirmation re-opens the dialog instead of a plain `continue_()`; warnings starting "nominal unknown" are shown as "(i)" (`INFO_PREFIXES`). Tests `test_load_cal_d50.py` (2); manual §9 plausibility checks + 2 troubleshooting rows. **B6-33 (SW review fixes, SW_design v0.6.6):** bridge maps `device.board_changed` → `boardChanged` (also while it is in `api.TOPICS_PENDING_GUI`; G-01b test accepts TOPICS ∪ pending) → acknowledgement **C-15** + toast; motion REFUSE `PARAM_INVALID` → red stop-banner row "Motion refused: …" with [Connection tab]; sequence start `PULL_DIR_MISMATCH` → message row (both values, B's text) + pull-dir field red until changed / started / session loaded; `limits.set` `LOAD_LIMITS_BOTH_OFF` (and `LOAD_INPUT_INVALID`) → apply line points to the highlighted [Enter no-specimen mode…]; new `LOAD_INPUT_INVALID` text shown verbatim (first-use banner); `session.load_issues` (`LOAD_LIMITS_RESTORED`, FILE `.bad`) shown at start and after File ▸ Open session (toast + Event log); KEY chip red "NOT RESPONDING" and an error toast on the `hotkey.state` edge for "hotkey thread not responding". Tests `test_b6_33.py` (7); manual §5 / §11 / §15.1 / §17 / App. A updated, screenshots 02 / 03 / 34 regenerated. Requests to B: `session.load_issues` into `api.SessionAPI`; move `device.board_changed` into `api.TOPICS`. |
| 0.4 | 2026-10-04 | Implementer D | **D-38 / SW-RT-006 (M1 add-on):** new §4.7 plot panes (grid 1–4 columns, + Pane, + X-Y pane, plot in / move to pane, drag reorder, move to window, rename / close, D-63 placement rules, X link, persistence, one snapshot per time window, Thrust_Stand rendering fix); §8 module list (plot_pane, pane_grid, quantity; lanes / single time view removed); tests G-45…G-52; traceability SW-RT-006; GRQ-B-21 (`ChannelSpec.dimension`). §4.1 inner layout and §4.5 JSON superseded by §4.7. |
| 0.3.2 | 2026-10-03 | Implementer D | M1 gate items: SWD-M1-06 (confirmation banner names STOP / HALT / PAUSE from a str or `.cmd` payload), SWD-M1-07 (`Implements:` tags in every GUI module), SWD-M1-10 ("LINK LOST – STOP sent" only after a sent STOP within 5 s, else "LINK LOST – reconnecting…"); D-36 wording test for GUI-owned texts; GF-19 (backend HALT clear hint) stays with B. |
