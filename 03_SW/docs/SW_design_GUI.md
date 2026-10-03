# Bird Bend Stand — SW design: GUI (`bend_stand.gui`)

| Doc | SW_design_GUI |
|---|---|
| Version | **0.1 — DRAFT for the P1 gate** (design only, no application code) |
| Date | 2026-10-03 |
| Owner | Implementer D — GUI (`03_SW/src/bend_stand/gui/**`, `03_SW/tests/gui/**`) |
| Binding inputs | `00_System/specs/SRS.md` **v0.2** (SW-*, SAF-SW-*, NFR-*), `DECISIONS.md` D-01…D-27 (esp. D-11, D-14, D-23, D-26), Orchestrator decision 2026-10-03 "GUI Pause = FW PAUSE command", `Initial_specs.txt` (PO wording), R3 §6 (Thrust_Stand GUI solutions, perf defect SWD-PM3-05), R1 §7 (Stefan `stepper_gui` patterns), R4 §5–§8, §10 |
| Backend contract | **`03_SW/docs/SW_design.md` v0.1 §15** (Implementer B): facade `core.backend.Backend`, `BackendStatus`, `GateResult`, engine protocol (§9.1), event topics (§15.3), GUI rules (§15.4); `core.api` Protocol classes for fakes. This document uses those names. Anything the GUI needs beyond §15 is listed as a request in §11.2. Where the two documents differ, §15 wins and this file is updated. |
| Not used | ICD (Integrator, in progress): only SRS semantics. |
| Reference code (read-only, D-02) | `Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/**` and `io/win_hotkey.py` @ **9473c68** (cited `TS:path:line`); `Stefan/SW/stepper_gui/gui.py` (cited via R1 §7) |

**Notation**
- `B §n` = section n of `SW_design.md`.
- `GRQ-B-nn` = remaining request to Implementer B (§11.2).
- `GQ-nn` = open question for the PO, with a default (§13).
- `GF-nn` = finding for another role (§14).
- Wireframes are ASCII: `[Button]` = push button, `[x]` = checkbox, `( )` = radio, `[ 12.3 ]` = numeric field, `[v]` = combo box, `●` = indicator LED.

---

## 1. Scope and design principles

### 1.1 Scope
This document designs the PySide6 + pyqtgraph GUI of the PC application:
- the main window, toolbar, stop banner and indicator bar;
- seven tabs, the detachable realtime plot windows and the readouts;
- the stop, pause and confirmation handling;
- the calibration, tare and generator wizards;
- the module layout, the Qt adapter, the threading rules and the GUI test strategy.

The backend (link, device, pipeline, engines, recorder, report, simulator, hotkey) is Implementer B's (B §2–§15). It is only referenced here.

### 1.2 Principles (binding for the implementation)
| # | Principle | Source |
|---|---|---|
| P1 | **No business or safety logic in the GUI.** The GUI collects operator intent, calls the backend, and displays state, results and refusal texts **verbatim**. The backend owns every rule: limits, target arithmetic, plausibility, statistics, gates, sequencing, VALID timing. The GUI enables or disables a control only from `status().gates` or from a gate-like refusal (`GateResult`, `PreconditionError`). It never decides on its own. The only GUI-side mappings are pure *display* mappings, such as indicator field → colour, Y-range autoscale and unit labels. They are unit-tested. | role file, CLAUDE.md, SW-PLT-002, B §15.4 |
| P2 | **STOP is always one click away** and never depends on GUI state: first in the toolbar, in every floating window, in every dialog and in every wizard. It never takes focus. It calls `backend.stop(source)` **directly in the GUI thread** (B §15.4 rule 7; non-blocking ≤ 5 ms, priority TX path B §4.5). | SW-STOP-001 |
| P3 | **The keyboard can never start motion or confirm a safety question by accident.** Enter and Space never confirm (SAF-SW-004). Buttons that start motion are never default buttons. The slider never moves the axis from the keyboard or the mouse wheel. | SAF-SW-004, SW-MAN-001 |
| P4 | **The display shows the FW state, not an optimistic echo.** The enable checkbox, VALID toggle, HOMED, latches and stream state follow `BackendStatus`. A command only changes the display after the FW confirms it. | R1 §7, R3 §6.10 |
| P5 | **Dark ≠ OK.** Every indicator has an explicit *unknown / not monitored* state (grey) that differs from *OK* (green). Colour always comes with text. | R1 §7 |
| P6 | **The GUI thread is never blocked.** No `.result()` call. High-rate data is *pulled* by one refresh timer and never pushed as signals. Rendering per tick is budgeted (§4.6). | NFR-001, NFR-002, B §15.4 rules 2, 4 |
| P7 | **Reuse proven Thrust_Stand patterns, but avoid its known defects.** Reuse `EStopButton`, `SafeDialog`, `QtBridge`, `gc_policy`, `ChannelTree`, `ParamForm` and `ReadoutDock`, copied with an origin note (D-02). Avoid SWD-PM3-01/-04/-05/-06/-07. | R3 §6, SYS-010 |

### 1.3 Stop vocabulary used in the GUI (SRS §2, §3.2, D-26)
| GUI term | Operator action / FW source | Backend call | FW meaning | Latched? | Sequence |
|---|---|---|---|---|---|
| **STOP** | red STOP button (toolbar, docks, dialogs, wizards, Manual tab) | `backend.stop(source)` | STOP immediate (cat. 2, holding) | no | terminated |
| **HALT** | Pause/Break or Ctrl+Break key (system-wide); physical STOP/BREAK button; Sequence [Abort] | hotkey thread → `backend.halt("hotkey")`; Abort → `sequencer.abort()` | latched immediate stop (source key / button / PC) | yes → **Clear stop** | terminated |
| **PAUSE** | toolbar **Pause**; physical PAUSE button | `backend.pause(source)` → **FW PAUSE command** (Orchestrator decision: same FW behaviour as the physical button) | controlled stop, PAUSED | until Resume / next motion | paused (resumable) |
| **E-STOP** | NC mushroom button (hardware power cut + MCU sense) | – (clear: `estop_clear_async(confirmed=True)`) | ESTOP latched, driver disabled, not homed | yes → **Clear stop** (confirmed) + ENABLE + HOME | terminated |
| **Fault / limit** | limit switch, FW load limit, AFE stale, link watchdog, step/homing fault, SW limit | (clear: `fault_clear_async()`) | immediate or controlled stop per SRS §3.2 | per type | terminated |

The on-screen button is labelled **STOP**, not "E-STOP". The SRS reserves *E-stop* for the hardware power cut (D-11), and the GUI must not teach operators otherwise (GQ-18).

---

## 2. Main window

### 2.1 Overall wireframe (default layout, ≥ 1366 × 768; designed for 1600 × 900 at 125 % DPI)

```
+----------------------------------------------------------------------------------------------------------+
| Bird Bend Stand - COM7 - FW 0.1.0 / proto 1.0                                                   _ [] X  |
| File  View  Tools  Help                                                                                 |
+----------------------------------------------------------------------------------------------------------+
|[#  STOP  #]|[Pause]|[Clear stop]|[TARE]|[> Stream]|[o Record]|[Sample v]|        ● CONNECTED COM7 80.0 Hz [Disconnect]|  <- main toolbar (fixed)
+----------------------------------------------------------------------------------------------------------+
| (!) HALT latched - source: Pause/Break key, 14:03:12. Motion refused. Clear: press [Clear stop].  [Clear stop] |  <- stop banner (only while a latch / stop result is shown)
+------------------------------------------------------------+---------------------------------------------+
| [Connection & Config][Safety limits][Test marks][Manual][Calibration & Tare][Sequence][Report]          |
|                                                            | Plot 1 ----------------------- [Float][STOP]|
|                                                            | +--------+ +-----------------------------+ |
|                     active tab content                     | |channels| | time view  (t = -30 ... 0 s)| |
|                       (scroll area)                        | | tree   | +-----------------------------+ |
|                                                            | |        | | X-Y view (x mm / F N)       | |
|                                                            | +--------+ +-----------------------------+ |
|                                                            +---------------------------------------------+
|                                                            | Readouts ---------------------- [Float][STOP]|
|                                                            |  F  123.4 N   x 12.345 mm   raw 239 512   80.0 Hz |
+------------------------------------------------------------+---------------------------------------------+
| LINK ● CONNECTED | ESTOP ● ok | HALT ● - | PAUSED ● - | LIM S ● E ● | LOAD ● ok | AFE ● ok 80.0 | WDG ● | |
| HOMED ● | ENA ● | ALM ● | PEND ● | THR ● ok | CAL ● PASS LOW_SPAN | TARE ● 3 min | VALID ● 0 | REC ● | KEY ● |  <- indicator bar (2 rows)
+----------------------------------------------------------------------------------------------------------+
```

- **Central widget:** a `QTabWidget` inside a scroll area, so small screens still reach every control.
- **Docks:** Plot 1 (right), Readouts (right, below), Event log (bottom, hidden by default). Plot 2 is created at first start, tabified with Plot 1 (SW-RT-001 "at least two"). Every dock can float and carries its own STOP (§4.1).
- **Indicator bar:** a fixed, non-hideable strip at the bottom with two rows (§2.4). A QStatusBar is not used because it truncates permanent widgets on narrow windows.

### 2.2 Global toolbar (fixed: not movable, not floatable, not hideable; context menu suppressed — TS:gui/main_window.py:228-265)

Items in this order. `QToolBar` moves trailing items into its overflow menu when the window is narrow, so the safety items come first and STOP can never be hidden.

| # | Item | Widget | Backend call (B §15.1) | Enable state | State shown | Req |
|---|---|---|---|---|---|---|
| 1 | **STOP** | `StopButton(large)`, red, `NoFocus`, never default, min 48 px high, fires on press | `backend.stop("toolbar")`, **synchronous in the GUI thread**, returns `StopResult` | **always**. Also enabled when disconnected; the result then says "not sent" and the banner says "STOP NOT SENT: not connected – use the physical STOP / E-stop" | banner shows the real `StopResult` | SW-STOP-001, NFR-002, SW-MAN-005 |
| 2 | **Pause / Resume** | `QToolButton`, toggles text | `backend.pause("toolbar")` (FW PAUSE command) / `backend.resume()` → `GateResult` | gates `pause` / `resume` (GRQ-B-01); the result items are shown | text follows `indicators.paused` (P4) | SW-STOP-004 |
| 3 | **Clear stop** | `QToolButton` | opens `ClearStopDialog` (§5.6) | any latched indicator | badge = number of latches | SW-STOP-003, SAF-SW-004 |
| 4 | **TARE** | `QToolButton` (bold) | `backend.tare()` → `GateResult` (REFUSE items shown verbatim) → non-modal `TarePopup` follows `tare_engine` (§6.4) | gate `tare` (REFUSE texts as tooltip) | TARE chip: age | SW-TARE-001…003 |
| 5 | **Stream** | checkable "▶ Stream" / "■ Stream" | `stream_start_async()` / `stream_stop_async()` | gates `stream_start/stop` (GRQ-B-01). The backend refuses STREAM_STOP while moving or while an operation runs (B §8) | checked = `status().stream.on` | SW-ACQ-001, PO-FW-8 |
| 6 | **Record** | checkable "● Record" | `record_start()` → `GateResult` / `record_stop()` | gates `record_start/stop` (GRQ-B-01) | REC chip: elapsed, rows, folder (tooltip) | SW-ACQ-002, SW-ACQ-004 |
| 7 | **Take sample** | `QToolButton` with a menu: window 0.1 / 0.5 / **1** / 2 / 5 / 10 s, "Other…" | `take_sample(window_s)` → `GateResult`; the result arrives as event `sample.taken` | gate `sample` (GRQ-B-01) | toast + Event log row ("F̄ = 123.41 N, σ 0.05 N, N 80 → samples.csv") | SW-ACQ-003 |
| 8 | stretch | | | | | |
| 9 | **Link widget** | LED + `CONNECTED COM7 80.0 Hz` + [Connect]/[Disconnect] | `connect_async(endpoint)` / `disconnect_async()`; a click on the text opens the Connection tab | endpoint selected | `status().link` | SW-PLT-003 |

- No toolbar item has a keyboard shortcut; the keyboard stop is the global key (§5.3).
- Toolbar buttons keep `Qt.NoFocus`, so Space can never trigger them.
- `PreconditionError` / `GateResult` REFUSE items from any call are shown in a toast and in the Event log.

### 2.3 Stop banner
A full-width `QFrame` below the toolbar. It is hidden while no latch is active and no recent stop result needs showing. Style: red for a latch or fault, amber for PAUSED and LINK LOST, grey for "STOP sent".

| Trigger (status / event, B §15.3) | Banner text (template) | Buttons |
|---|---|---|
| `StopResult.sent` (event `stop.issued`) | `STOP sent (toolbar, 14:03:12) – axis stopped, driver holding.` (auto-hide 10 s unless a latch follows) | – |
| `StopResult` not sent | `STOP NOT SENT (toolbar, 14:03:12): <reason> – use the physical STOP/BREAK or E-stop button.` | – |
| `halt.unconfirmed` | `HALT NOT CONFIRMED by the board after 1 s – use the physical STOP/BREAK or E-stop.` | – |
| `indicators.halt` | `HALT latched – source: <indicators.halt_source>, <t>. Motion refused. <clear_hint>` | [Clear stop] |
| `indicators.estop` | `E-STOP active – driver power removed, axis NOT homed. <clear_hint>` | [Clear stop] |
| fault latches (load_limit, limit_wiring, AFE fault, step / homing fault) | `<fault>: <clear_hint>` | [Clear stop] |
| `safety.trip` | `SW load limit: 1 812.4 N > 1 765.2 N (pull max) – STOP sent, sequence terminated.` (values from the event) | – |
| `indicators.paused` | `PAUSED (<source>) – sequence step <n> interrupted. Resume with [Resume] or the PAUSE button.` | [Resume] |
| link state LOST | `LINK LOST – STOP sent; sequence terminated. Reconnecting…` | [Connection tab] |
| `link.compat` ≠ OK | `Read-only: protocol/payload version mismatch – motion disabled.` / `dictionary hash mismatch – configuration read-only.` | – |
| `rec.failure` | `RECORDING FAILED: <text> – sequence stopped (controlled).` | [Open folder] |
| first use (F-B-08) | `No load calibration / tare: enabled SW load limits block motion – calibrate and tare, or disable the SW load limits.` (from the `move` gate REFUSE item) | [Calibration tab] |

All clear texts are the backend's `clear_hint` (B §6.5), so the GUI holds no stop logic.

### 2.4 Indicator bar (SAF-SW-005)
`IndicatorBar` = a row of `IndicatorChip` (LED + short label + optional value).
- **Refresh.** Every refresh tick (33 ms) reads `backend.status().indicators` (B §6.5, rebuilt on every DATA/EVENT/GET_STATUS). The display therefore lags the backend by ≤ one tick, well inside the 200 ms of SAF-SW-005 (B §15.4 rule 4 asks for ≥ 10 Hz).
- **Field → chip mapping.** `gui/indicator_map.py` is a pure display mapping from `Indicators` fields to chip levels {unknown, ok, warn, alarm, info}. It is unit-tested for every field.
- **Unknown state.** "Unknown" (grey) needs per-item freshness information from the backend (GRQ-B-02).
- **Help.** A click on a chip opens `StatusHelpDialog`: meaning, source flag, current value and `clear_hint` of every indicator.

| Chip | `Indicators` / `BackendStatus` field | Grey (unknown) | Green | Amber | Red | Tooltip | Req |
|---|---|---|---|---|---|---|---|
| LINK | `link.state` | DISCONNECTED | CONNECTED | CONNECTING, DEGRADED | LOST ("LINK LOST") | `LinkStats`; click → Link statistics | SAF-SW-003, SW-PLT-003 |
| ESTOP | `estop` | no data | not active | – | latched / input open | `clear_hint` | SAF-SW-005 |
| HALT | `halt`, `halt_source` | no data | – | – | `HALT key` / `HALT btn` / `HALT PC` | `clear_hint` | SAF-SW-005, SW-STOP-003 |
| PAUSED | `paused` (+ source, GRQ-B-02) | no data | – | PAUSED | – | Resume / next motion command | SW-STOP-004 |
| LIM S / LIM E (+ WIRING) | `limit_start`, `limit_end`, `limit_wiring` | no data | free | – | active / latched / wiring | `clear_hint` | SAF-SW-005 |
| LOAD | `load_limit`, `sw_trip`, `safety.warnings` | no data / no valid load input | ok | ≥ warning level (90 %) | FW LOAD_LIMIT or SW trip | `clear_hint` | SAF-SW-005, SW-LIM-002 |
| AFE | `afe_stale`, `afe_saturated`, `afe_settling`, `afe_rate_mismatch` + rate | no data | ok + rate | settling, rate mismatch | stale, saturated | `clear_hint` | SAF-SW-005, FW-AFE-004 |
| WDG | `link_wdg` | no data | – | – | set | clears at the next valid frame | SAF-SW-005 |
| HOMED | `homed`, `pos_uncertain` | no data | homed | homed ± 1 step | not homed | HOME on the Manual tab | SAF-SW-005 |
| ENA | `enabled` | no data | enabled | – | disabled | Manual tab | SAF-SW-005 |
| ALM | `alm` | no data | ok | **alarm** (no FW reaction, D-16) | – | check the driver | SAF-SW-005, FW-SW-004 |
| PEND | `pend` | no data | in position | not in position | – | info | SAF-SW-005 |
| BTN | `stop_button`, `pause_button` | no data | released | PAUSE pressed | STOP pressed | – | FW-SW-003 |
| THR | `thresholds_state` (`ThresholdState`) | – | VERIFIED | sending / DEFAULT_ONLY | FAILED / INVALID → motion disabled | Safety limits tab | SAF-SW-002 |
| CAL | `calibration` (status, LOW_SPAN, AFE mismatch) | none | PASS | WARN / LOW_SPAN / UNVERIFIED / AFE mismatch | – | Calibration tab | SW-CAL-008/009 |
| TARE | `tare` (state, age) | none | younger than 30 min (GQ-11) | older / large-offset warning | – | TARE | SW-TARE-002 |
| VALID | VALID bit | no data | 1 | – | – (0 shown neutral) | – | PO-FW-7 |
| CFG | `cfg_dirty`, `config_read_only` | no data | clean | dirty (RAM ≠ NVM) | read-only (hash mismatch) | Config tab [Save to NVM] | SW-CFG-004, IF-008 |
| REC | `recording` | off | – | queue ≥ 50 % | recording (red dot, by convention) / failure | – | SW-ACQ-002/004 |
| KEY | `hotkey` | – | active | (fallback, test mode: GRQ-B-15) | unavailable ("Pause/Break works only while the app is focused") | §5.3 | SW-STOP-002 |
| RO | `link.compat` | – | – | – | read-only | – | IF-008 |

### 2.5 Menus
| Menu | Items |
|---|---|
| File | Open session… / Save session / Save session as… (`session` API, `*.bbsession.json`) · Recent sessions · Open data folder · Exit (Ctrl+Q; close rules §5.8) |
| View | New plot window · Reset layout · Readouts · Event log · Units ▸ **N** / kgf (SYS-003; `calc.units` helpers, B §15.4 rule 8) · Travel ▸ **test** / machine (GQ-03) · Theme ▸ light / dark (GQ-06) |
| Tools | Link statistics… (`LinkStatsDialog`: `status().link` + `device.status` events) · Test Pause/Break key… (GRQ-B-15) · Performance overlay (frame interval p50/p95) · Open log file |
| Help | Status indicators & clear procedures… · Keyboard (Pause/Break, Ctrl+Break) · About (SW, FW, proto, payload, dictionary hash) |

### 2.6 Tabs
| # | Tab | Purpose | Section |
|---|---|---|---|
| 1 | Connection & Config | endpoint, device info, link stats, parameter form (read / edit / write+verify / file / NVM) | §3.1 |
| 2 | Safety limits | SW travel and load limits, FW load-limit level, threshold verification | §3.2 |
| 3 | Test marks | report marks, presets, custom fields, 3-point-bend geometry, automatic snapshot | §3.3 |
| 4 | Manual | position, slider, entry, ±steps, hold-to-jog, speed/accel, enable, HOME, test zero, VALID, STOP | §3.4 |
| 5 | Calibration & Tare | active load and travel calibration, wizard launchers, tare info | §3.5 |
| 6 | Sequence | step table editor, loops, generators, files, sequence chart, run controls | §3.6 |
| 7 | Report | recordings, step results, report (re)generation, offline re-apply | §3.7 |

The last active tab is restored at start (GQ-01).

### 2.7 Control gating (how the GUI enables controls)
- Every tick, `GateBinder` (`gui/gating.py`) reads `status().gates`. These are precomputed `GateResult(items=[GateItem(code, severity REFUSE|CONFIRM|WARN, text, clear_hint)])` per control (B §5.6, §15.2).
- **Rules:**
  - any REFUSE item → control disabled; the tooltip lists the REFUSE texts and `clear_hint`;
  - CONFIRM items → the action opens `ConfirmDialog` (§5.5) and is repeated with `confirmed=True` (B §15.4 rule 6);
  - WARN items → amber warning line next to the control (e.g. SAF-SW-006 margin, ALM).
- **Gate ids.** B provides `move`, `jog`, `home`, `enable`, `disable`, `tare`, `sequence_start`, `clear_stop`, `estop_clear`, `fault_clear`. The GUI also needs `pause`, `resume`, `stream_start`, `stream_stop`, `record_start`, `record_stop`, `sample`, `config_write`, `cal_travel_start`, `cal_load_start`, `sequence_edit`, `valid_toggle` and `test_zero` (GRQ-B-01).
- **Missing gate = fail-safe for motion.** A motion-related control without its gate is **disabled**. A non-motion control without its gate is enabled; its call returns the refusal.
- **Owner conflicts** (a wizard or sequence owns motion, B §3.5) arrive as REFUSE items of the `move`/`jog` gates. Manual controls are therefore disabled automatically while an operation runs.
- Gating is a convenience only: the backend re-checks every call (`PreconditionError`), and the GUI shows `user_text` in a toast and in the Event log.

---

## 3. Tabs

### 3.1 Connection & Config (SW-PLT-003, SW-CFG-001…004, IF-008)

```
+-- Connection -------------------------------------------------------------------------------------+
| Endpoint [COM7 - STLink Virtual COM Port v] [Refresh]  Baud 921600 (fixed)  [Connect] [Disconnect] |
|          (entries from backend.endpoints(): ST-LINK COMx first, other COMx, "sim", tcp twin)       |
| Device: FW 0.1.0 (build 3f2a1c)  proto 1.0  payload 1  dict hash 0x5A3C91E2 ✓  UID 0039...  feat 0x0007 |
| Version check: ✓ compatible        (or: ✗ major/payload mismatch -> READ-ONLY, motion disabled)    |
| Link: frames 123 456  lost FW 0 / link 0  CRC 0  len 0  timeouts 0  late 0  rate 80.0 Hz [Details...] |
+-- Board configuration ----------------------------------------------------------------------------+
| Filter [          ]  [x] only changed  [ ] show advanced        CFG: ● clean (RAM = NVM)          |
| +-----------------------+-----------+-----------+-----------+-------+--------------+--------+-----+ |
| | Parameter             | Edit      | Board     | Default   | Unit  | Range        | Status | Flg | |
| | v afe                 |           |           |           |       |              |        |     | |
| |   afe.gain_channel    | [A128 v]  | A128      | A128      | -     | A128,A64,B32 | OK     |     | |
| |   afe.rate_sps        | [80 v]    | 80        | 80        | SPS   | 10, 80       | OK     |     | |
| | v motion              |           |           |           |       |              |        |     | |
| |   motion.steps_per_mm | [160.000] | 160.000   | 160.000   | st/mm | 1...100000   | OK     | idle| |
| |   motion.v_max_um_s   | [ 10000 ] | 10000     | 10000     | um/s  | 1...250000   | REJECTED (E_RANGE) | | |
| | v safety              |           |           |           |       |              |        |     | |
| |   safety.load_raw_max |  (locked) | 7147283   | 7022271   | cnt   | ±7151121     | managed by the threshold manager | |
| |   safety.zero_raw     |  (locked) | 125012    | 0         | cnt   | i24          | managed by the threshold manager | |
| | ...                   |           |           |           |       |              |        |     | |
| +-----------------------+-----------+-----------+-----------+-------+--------------+--------+-----+ |
| Rule check: ✓ (or: ✗ soft_min_um < soft_max_um violated -> [Write & verify] disabled)            |
| [Read all] [Write & verify] [Revert edits] | [Save to file...] [Load from file...] |              |
| [Save to NVM] [Reload from NVM] [Restore defaults...]                                             |
+---------------------------------------------------------------------------------------------------+
```

| Element | Behaviour | Backend (B §15.1) | Req |
|---|---|---|---|
| Endpoint list | `backend.endpoints()`: ST-LINK VCPs first, then other COM ports, "sim" and the configured tcp twin. The backend is created and `start()`ed before the main window shows, so the hotkey works before Connect (B §15.4 rule 1) | `endpoints()` | SW-PLT-003, SYS-008 |
| Connect | `connect_async(endpoint)` (future → `DeviceInfo`). Stage text comes from `link.state` events (`why`); errors (`BendStandError.user_text`) appear in a `SafeMessageBox` | `connect_async` | SW-PLT-003 |
| Version check | `status().link.compat` / event `link.compat`. Major/payload mismatch → RO chip, banner, motion gates REFUSE. Hash mismatch → `config_read_only`: form read-only + warning | `status()` | IF-008 |
| Link line | `status().link.stats` each tick (text updated at 1 Hz). [Details…] opens `LinkStatsDialog` (PC counters + FW counters from `device.status` events) | `status()` | SW-PLT-003, FW-CMD-004 |
| ParamForm | Generated from `config` `ParamMeta` list/groups (`params_gen`: type, unit, range, enum names, decimals, `moving_ok`, NVM flag). Editors: bool → checkbox; enum → combo with names; f32 → double spin with decimals; ints → spin; u32 → validated line edit; `keyboardTracking` off. Columns: Edit / Board / Default / Unit / Range / Status / Flags. Copied from TS `param_form.py` with origin note. **`safety.load_raw_min`, `safety.load_raw_max` and `safety.zero_raw` are read-only rows** with the note "managed by the threshold manager (SAF-SW-002) – see Safety limits" (B F-B-21) | `config` | SW-CFG-001 |
| Rule check | live check of the edits; violations disable [Write & verify] and mark cells | `config.check(edits)` (GRQ-B-03); `write_and_verify_async` also refuses before the first write | SW-CFG-003 |
| Write & verify | `config.write_and_verify_async(edits)` → per-row `OK / REJECTED(detail) / MISMATCH / BUSY / TIMEOUT / NOT_ATTEMPTED` in the Status column; summary toast | `config` | SW-CFG-003 |
| Save / Load file | `SafeFileDialog` (`*.bbboard.json`); `config.save_board_config(path)` / `load_board_config(path) → BoardConfigFile(values, unknown_keys, missing_keys, out_of_range, hash_mismatch)` → fills the **Edit column only** and shows the report dialog | `config` | SW-CFG-002 |
| NVM buttons | Save to NVM / Reload from NVM / Restore defaults (`config.save/load/defaults_async`); Restore needs C-04 (§5.5). CFG chip = `cfg_dirty` | `config` | SW-CFG-004, FW-NVM-001 |

### 3.2 Safety limits (SW-LIM-001…003, SAF-SW-001/002/006)

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
| (!) Load limit enabled, no valid calibration/tare -> motion disabled (SAF-SW-001)                 |
+-- Live ------------------------------------------------------------------------------------------+
| F = 123.4 N (6.3 % of pull max)   x = 112.345 mm (inside 10.000 ... 250.000)                      |
| [Apply]  [Revert]          Limits are saved with the session (File > Save session).               |
+---------------------------------------------------------------------------------------------------+
```

| Element | Behaviour | Backend | Req |
|---|---|---|---|
| Fields | Edits stay local until [Apply]. [Apply] → `limits.set(cfg)` → `list[Issue]` shown next to each field (e.g. outside FW soft limits; FW level > 110 % FS; FW level < SW trip; refused while moving). An empty list means applied. The "→ 1765.20 N" and "% FS" helper labels are display conversions through `calc` helpers | `limits.get()`, `limits.set()` | SW-LIM-001, SW-LIM-002 |
| FW thresholds | `limits.thresholds()` → `ThresholdState` (state VERIFIED / FAILED / INVALID / DEFAULT_ONLY, raw min/max, zero_raw, ids, time). [Re-send & verify] → `limits.recheck_async()` (GRQ-B-04). Automatic triggers are the backend's (B §6.3) | `limits.thresholds()` | SAF-SW-002 |
| Motion-disabled notice | shown when the `move` gate has the SAF-SW-001/002 REFUSE item | `status().gates` | SAF-SW-001 |
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
| v Board config  hash 0x5A3C91E2, CFG clean, steps/mm 160.000 ...                                  |
| v Calibration   active_load.json  K 4.5666e-4  PASS  LOW_SPAN                                      |
| v Tare          tare_raw 125 012 at 14:02:11                                                       |
| v Limits        pull 1961.3 N / push -1961.3 N / travel 10 ... 250 mm / FW 110 %                  |
| v Versions      SW 0.1.0, FW 0.1.0, proto 1.0, payload 1                                           |
+---------------------------------------------------------------------------------------------------+
| Marks are written into the recording at its start (meta.json); edits during a recording: GRQ-B-07 |
+---------------------------------------------------------------------------------------------------+
```

- The marks model is the backend's `TestMarks` (`marks.get()/set()`): fixed fields plus custom key/value fields. Presets are `*.bbmarks.json` via the `marks` list/save/load-preset calls.
- `*` = recommended. An empty value gives a warning at record start, not a refusal (GQ-12; the backend decides).
- Custom keys must be unique and non-empty. `marks.set()` returns issues, which are shown inline.
- The geometry is part of `SessionSettings` (B §13.5). It feeds the derived channels `sigma_mpa` / `eps` (greyed until it is valid) and the report (SW-REP-004).
- The snapshot view is built from `status()` (`calibration`, `tare`, `link` info) and `limits.get()`. It is read-only.

### 3.4 Manual control (SW-MAN-001…006, SW-STOP-001, SAF-SW-004, SAF-SW-006)

```
+-- Position ------------------------------------------+ +-- Axis --------------------------------------+
|  Travel (test)      12.345 mm      (machine 112.345)  | | [x] Driver enabled          FW: ● ENABLED     |
|  Commanded target   15.000 mm  (pending 16.000)       | | [  HOME  ]   ● HOMED   (homing: idle)        |
|  Force             123.4 N         raw 239 512        | | [ Set test zero here ]  x0 = 100.000 mm [Reset]|
|  state ● MOVING >   owner MANUAL                      | | VALID  [ 0 | 1 ]          FW: ● 0            |
+-------------------------------------------------------+ +----------------------------------------------+
+-- Target position (moves on release) ---------------------------------------------------------------+
|   10.000 |=============================[#]===============================| 250.000 mm   (SW limits)  |
|           ^ live position marker                   preview: 120.000 mm                            |
+-- Go to -------------------------------------------------------------------------------------------+
|  ( ) absolute  (o) distance    [   +5.000 ] mm   (relative to the commanded target)   [  Go  ]    |
|  Steps from commanded target:  [-10] [-1] [-0.1]   [+0.1] [+1] [+10]  mm                          |
|  Jog (hold):  [<< rear (-)  hold]   [hold  forward (+) >>]    jog speed [  2.000 ] mm/s           |
|               (+) = away from the home (START) switch;  pull direction: (+)                        |
+-- Motion parameters --------------------------------------------------------------------------------+
|  speed [  5.000 ] mm/s  (max 10.000)    accel [ 100.0 ] mm/s^2  (max 100.0)                         |
|  (!) speed too high for the load-limit margin (SAF-SW-006)        <- WARN item from the backend     |
+-----------------------------------------------------------------------------------------------------+
|  [#########  STOP  #########]     Keyboard: Pause/Break = HALT (system-wide)                         |
+-----------------------------------------------------------------------------------------------------+
```

| Control | Exact behaviour | Backend (`motion`, B §5.4) | Req |
|---|---|---|---|
| **Slider** (`TargetSlider`) | Horizontal, integer µm resolution, range = `status().motion` SW travel range (SW limits, else FW soft limits). While idle the handle sits at `commanded_target_mm`; a thin marker shows the live position. **Only a handle drag edits the value.** Groove clicks are ignored (page-step suppressed), the wheel is ignored and the slider has `NoFocus`, so no key moves it. While dragging only the preview label changes and nothing is sent. On `sliderReleased` exactly **one** call `motion.move_to(value, speed_mm_s, accel_mm_s2)`. On `PreconditionError` / `ValueError` the handle snaps back and the text is shown. Disabled by the `move` gate (not homed, latch, owner, …). | `move_to` → `MoveTicket` | SW-MAN-001, D-23 |
| **Go to** | Radio absolute / distance + `QDoubleSpinBox` (3 decimals, mm). Enter in the field only commits the field value (GQ-16). [Go] (not default, not auto-default) → `motion.move_to(target)` or `motion.move_by(distance)`. The GUI does no target arithmetic; the result shows as `commanded_target_mm`. | `move_to`, `move_by` | SW-MAN-002 |
| **Step buttons** | −10, −1, −0.1, +0.1, +1, +10 mm → `motion.move_by(±d)`. The backend accumulates on the commanded target. While the FW is busy it sets `pending_target_mm` ("latest wins", B §5.4, F-B-09), which is shown as "(pending …)". | `move_by` | SW-MAN-003 |
| **Hold-to-jog** (`HoldButton`) | `pressed` → `motion.jog_start(direction, speed)`. `released`, `QApplication.applicationStateChanged` ≠ Active, window deactivate/hide, tab change, the button becoming disabled, or the `jog` gate closing → `motion.jog_stop()` (JOG 0) (B §15.4 rule 5). The 100 ms JOG refresh is done by the backend Supervisor and only while the GUI beat (`gui_beat()`, every tick) is younger than 300 ms (B §5.4). A frozen GUI thread therefore stops the refresh and the FW dead-man (250 ms) stops the axis. Un-homed jog is clamped by the backend to v_unhomed; the effective speed is shown. Jog speed edits while held → `motion.jog_update(speed)`. | `jog_start`, `jog_update`, `jog_stop` | SW-MAN-004, SAF-FW-016 |
| Speed / accel | `QDoubleSpinBox`; max = `motion.limits()` (`MotionLimits`: v/a caps, step-rate cap). A typed value above max is refused (red field, tooltip, not applied; the backend would also raise `ValueError` with the allowed maximum). Values persist in the session. SAF-SW-006 warning = WARN item of the `move` / `jog` gate, evaluated for the speed in the field (GRQ-B-05). | `limits()` | SW-MAN-005, SAF-SW-006 |
| Driver enabled | Checkbox bound to `indicators.enabled` (P4). Check → `motion.enable()`. Uncheck → `disable` gate CONFIRM → C-02 → `motion.disable(confirmed=True)`. A refusal reverts the display to the FW state. | `enable`, `disable` | SW-MAN-006, SAF-SW-004 |
| HOME | `home` gate. A CONFIRM item (abs(F) ≥ 5 % FS **or** unknown load, B §5.6) → C-01 → `motion.home(load_confirmed=True)` (FW confirmed flag, SAF-FW-021). Homing phase text from `motion.done` / `fw.event`. | `home` | SW-MAN-006, SAF-SW-004 |
| Set test zero | `motion.set_test_zero()` (x0 := current commanded position, event row). [Reset] → x0 = 0 (GRQ-B-06). Never sent to the FW. | `set_test_zero` | SW-MAN-006 |
| VALID toggle | Two-segment toggle bound to the FW VALID bit → `motion.set_valid(flag)`. Refused/disabled while a sequence runs (gate `valid_toggle`, GRQ-B-01). | `set_valid` | SW-MAN-006, PO-FW-7 |
| STOP | `StopButton(large)` on the tab, same handler as the toolbar (`source="manual"`) | `backend.stop` | SW-MAN-005, SW-STOP-001 |

Keyboard jog with ←/→ (as in Stefan's GUI) is **off** by default (GQ-08). If it is enabled it follows the same `jog_start` / `jog_stop` rules and is ignored while an entry field has focus (R1 §7).

### 3.5 Calibration & Tare (SW-CAL-001, -004, -008, -009, SW-TARE-001…003)

```
+-- Load calibration (active) ----------------------------+ +-- Travel calibration (active) -------------+
| File   active_load.json  (load_..._20261003T120000Z)     | | steps/mm  160.000   board ● 160.000  NVM ●  |
| K      4.56665e-4 N/count   B  -57.085 N                 | | candidates (DIP, D-27): 160 / 1280          |
| Points 0 kg / 1.000 kg / 10.000 kg                       | | last: 2026-10-03  D1 10.020  Dtot 60.050 mm |
| Fit    PASS   NL 0.002 % span   R^2 0.99999998          | | [ Start travel calibration wizard... ]      |
| (!) LOW_SPAN: largest reference 98.1 N (5 % FS);         | | [ History... ]                              |
|     forces > 294 N shown as "extrapolated"               | +---------------------------------------------+
| AFE   A/128 80 SPS   ● matches board                     |
| push_calibrated: no (push forces "tension-calibrated")   |
| [ Start load calibration wizard... ]  [ History... ]     |
+---------------------------------------------------------+
+-- Tare (session only; repeat after every application start) --------------------------------------+
| tare_raw 125 012 counts  (robust mean, std 44, drift 3, 800 samples, 10.0 s)  at 14:02:11, age 3 min |
| window [ 10.0 ] s (2 ... 60)     [ TARE ] (same as the toolbar)    FW thresholds ● VERIFIED         |
+---------------------------------------------------------------------------------------------------+
```

- **Data.** Active records from `status().calibration` and `calibrations` (active load/travel, history list), tare from `status().tare` (B §9.5–§9.6). An AFE-configuration mismatch shows "calibration invalid" (SW-CAL-009); the backend also treats the load input as invalid (F-B-22).
- **Wizards.** Started here (gates `cal_travel_start` / `cal_load_start`, GRQ-B-01). They run in **non-modal** `SafeWizard` windows (§6.1). Only one operation at a time (B §3.5).
- **History.** [History…] lists the previous files read-only. Activating an old file is not offered: SW-CAL-009 only needs the active copy loaded by default.
- **Extrapolated.** The "extrapolated" marking itself is shown in readouts and plots (§4.3; GRQ-B-09).

### 3.6 Sequence (SW-SEQ-001…007, SW-WIZ-002, SW-SEQF-001, SW-SCH-001/002, SW-STOP-004)

```
+- Sequence: staircase_0-200N.bbseq.json *  --------------------------------------------------------------+
| [New] [Open...] [Save] [Save as...] | [Generate...] [+ Step v] [Delete] [Up] [Down] [Loop...] [Unloop] | ● valid |
+--------------------------------------------------------------------+-------------------------------------+
| Lp | #  | Type    | Target  | U  | v mm/s | a mm/s2 | Settle | Capt | Time | Tol  | Label  | ! |  Sequence chart          |
| +3 |  1 | travel  |   0.000 | mm |  2.000 |   0     |   0.0  |  0.0 |    0 |  -   | start  |   |  F [N]                   |
| |  |  2 | load    | 100.0   | N  |  0.500 |   0     |   2.0  |  5.0 |    0 | 2.0  | 100 N  |   |   |      o 200 N         |
| +  |  3 | load    | 200.0   | N  |  0.500 |   0     |   2.0  |  5.0 |    0 | 2.0  | 200 N  |   |   |    o 100 N            |
|    |  4 | travel  |   0.000 | mm |  2.000 |   0     |   0.0  |  0.0 |    0 |  -   | return |   |   |___o____________ x [mm]|
|    |  5 | mark    |    -    |    |   -    |   -     |   -    |   -  |    - |  -   | end    |   |   |  | <- live marker      |
|    |    | [ ] advanced columns: capture during move, travel bound, wait for operator          |   (amber = active step, |
|    |    |                                                                                     |    green = capture points)|
+--------------------------------------------------------------------+-------------------------------------+
| Settings: travel ref [test v]  pull dir [+ v]  k_est [ 50.0 ] N/mm (last run 41.2)  on trim fail [stop v] [Defaults...] |
+-------------------------------------------------------------------------------------------------------------+
| [> Start] [|| Pause] [Resume] [Continue] [Stop (controlled)] [Abort (HALT)]   ● RUNNING  step 2/5  loop 2/3 |
| phase CAPTURE  windows 3/6   F 198.7 N (target 200 +/- 2)   plan 00:03:12 (behind 0.4 s)   remaining ~00:05:40 |
| Messages: <SeqStatus.message, gate WARN items, step results>                                                |
+-------------------------------------------------------------------------------------------------------------+
```

**Editor (`StepTableView` over `StepTableModel`).**
- **Model.** The `QAbstractTableModel` wraps a backend `Sequence` dataclass (B §10.1: `steps: list[Step]`, `loops: list[Loop]`, `travel_ref`, `k_est_n_mm`, `pull_dir`, `defaults: StepDefaults`, `on_trim_fail`, `notes`). The GUI edits fields of that object; it never expands or interprets steps.
- **Columns** = `Step` fields: kind (travel / load / hold / home / tare / mark), target (mm or N per kind), `speed_mm_s` (empty = sequence default), `accel_mm_s2` (0 = FW default), `settle_s`, `capture_s`, `step_time_s` (minimum dwell; reach timeout for load steps), `tol_n` (load), `label`.
- **Advanced columns** (toggle): `capture_during_move`, `travel_bound_mm`, `wait_operator`.
- **Applicability.** Cells that do not apply to the kind show "–" and are read-only. The kind → field table is a display table in `models/step_table_model.py`, cross-checked by a test against the backend validation.
- **Validation.** After every edit, `sequencer.validate(seq)` (`Sequence.validate(ctx)` → `Issue(step_uid, field, severity, text)`) runs, debounced 200 ms. Each issue colours its cell (red ERROR, amber WARN, incl. the SAF-SW-006 margin per step) and its text is the cell tooltip; the "!" column and the "● valid" badge follow (SW-SEQ-001).
- **Loops** (`Loop(first, last, count)`): the gutter "Lp" draws a bracket with the count. [Loop…] wraps the selected contiguous steps; the dialog asks for the count, 1…10 000 or **0 = until stopped**. Nesting is limited to one level, and the backend validation flags violations. [Unloop] removes the bracket.
- **Inserting steps.** [+ Step ▾] inserts a typed step after the selection with `defaults`. [Generate…] opens the generator wizard (§6.5); `Sequence.insert_block(block, mode, index)` keeps the generated steps editable (SW-WIZ-002).
- **Undo/redo** (`QUndoStack`) on model edits.

**Files.** Open / Save / Save as via `SafeFileDialog`, `*.bbseq.json` → `sequencer.load(path)` / `sequencer.save(seq, path)`. On `FileFormatError` the error is shown and the current sequence stays unchanged (SW-SEQF-001). A dirty flag (`*` in the title) triggers C-08 on New/Open.

**Chart (`SequenceChart`, pyqtgraph, x = travel mm, y = load N/kgf).**
- **Planned path** = `sequencer.planned_path(...)` over `sequencer.expand(seq)` (B §10.2: `PathPoint(x_mm, f_n, exec_idx, label, known)`). It is drawn as a polyline that breaks at HOME, with step labels (`TextItem`, hidden when overlapping). Capture points/segments come from the `Plan` capture windows (green); estimated coordinates (`known` ≠ both) are drawn dashed (SW-SCH-001).
- **During execution** (SW-SCH-002), on every refresh tick (≥ 10 Hz guaranteed, typically 30 Hz):
  - live marker = vertical `InfiniteLine` at the current travel plus a single-point `ScatterPlotItem` at (x, F) from `data.latest()`;
  - active step highlighted from `sequencer.status().exec_idx`;
  - measured trace = `data.sequence_trace()`.
- **Ranges.** Explicit axis ranges (§4.6). The chart is redrawn only on plan changes or new data.

**Run controls** (enabled from gates; texts verbatim):
| Button | Call | Notes | Req |
|---|---|---|---|
| Start | `sequence_start` gate (REFUSE list: not homed, not enabled, latch, owner, validation errors, targets outside SW limits, cal/tare for load steps, thresholds not VERIFIED, stream, recording cannot start; CONFIRM: HOME steps under load; WARN: margin, LOW_SPAN, extrapolation, hotkey unavailable (GQ-09)) → C-07 if CONFIRM/WARN → `sequencer.start(seq, confirmations=…)`. The backend starts a recording if none runs (B §8). | non-default button | SW-SEQ-005, SAF-SW-006 |
| Pause | `backend.pause("sequence")` (FW PAUSE, same as the toolbar) | state kept | SW-STOP-004 |
| Resume | `backend.resume()` → `GateResult` | the backend re-issues the interrupted step | SW-STOP-004 |
| Continue | `sequencer.continue_()` | only in state WAITING_OPERATOR (MARK step with `wait_operator`) | SW-SEQ-001 |
| Stop (controlled) | `sequencer.stop()` | sequence ends | SW-SEQ-007 |
| Abort (HALT) | `sequencer.abort("operator")` (HALT, latched; Clear stop needed) | | SW-SEQ-007 |

**Run status.** `sequencer.status()` → `SeqStatus`, pulled every tick; `seq.status` and `seq.step_result` events feed the Messages line and the Event log. Shown: `state`, `exec_idx`/plan length, `loop_iters`, `phase` (COMMAND / MOVING / APPROACH / TRIM / SETTLE / CAPTURE / HOLD / WAIT_OPERATOR), `windows_done/total`, `plan_t_s`, `behind_s`, `k_est_n_mm`, `message`. The current step's target comes from the `Plan`; the remaining time is GRQ-B-12. While running, the editor is read-only (gate `sequence_edit`).

### 3.7 Report (SW-REP-001…004, SW-ACQ-002)

```
+-- Recordings (root: D:\BendData\recordings) ---------------------------------------------------+
| Date/time          | Specimen          | No. | Sequence           | Status     | Duration |  |
| 2026-10-03 14:05   | Wing bracket PA12 | 17  | staircase 0-200 N  | complete   | 00:09:12 |  |
| 2026-10-03 13:40   | Wing bracket PA12 | 16  | (manual)           | partial    | 00:02:01 |  |
+-----------------------------------------------------------------------------------------------+
+-- Selected: 20261003_140512_Wing-bracket_17 --------------------------------------------------+
| Marks: element, number, operator ...   Calibration: PASS, LOW_SPAN (!)   Tare 125 012          |
| Warnings: LOW_SPAN; push forces tension-calibrated; 0 lost frames; 2 INCOMPLETE windows        |
| Step results:                                                                                  |
| Step | Loop | Label | N   | F mean [N] | F std | x mean [mm] | drift | flags                    |
|  2   | 1    | 100 N | 400 | 99.12      | 0.21  | 2.103       | 0.02  | ON_TARGET                |
|  2   | 2    | 100 N | 311 | 98.70      | 0.30  | 2.115       | 0.05  | INCOMPLETE, ON_TARGET    |
+-- Options --------------------------------------------------------------------------------------+
| Re-apply: calibration [ (as recorded) v ] [Choose file...]  tare raw [ (as recorded) ]         |
| 3-point bend outputs [ ] (L, b, h from the session geometry)                                    |
| [Generate / Regenerate report] [Open HTML] [Open folder] [Open CSV]                             |
+-----------------------------------------------------------------------------------------------+
```

- **Report generation.** The backend builds the report at sequence end, also for stopped/aborted runs, which are flagged partial (B §10.3, §11). The event `report.ready` refreshes the list.
- **Recording list and step table** come from GRQ-B-11 (list recordings, load the parsed `report.json`). The step table is read-only (SW-REP-002, backend computation).
- **[Generate]** → `reports.build_async(recording_dir, cal=…, tare=…)` (SW-REP-001, SW-REP-003 re-apply). The 3-point-bend option is GRQ-B-11 (SW-REP-004).
- **[Open HTML]** uses `QDesktopServices.openUrl` (system browser, GQ-13).

---

## 4. Realtime display (SW-RT-001…005, NFR-001)

### 4.1 Plot windows (`PlotDock`, SW-RT-001)

```
+-- Plot 1 ---------------------------------------------------------- [Float] [Close] [## STOP ##] -+
| Window [ 30 s v ] [Freeze] | Y: (o) auto ( ) manual [ -10 ] .. [ 250 ] | X-Y: x [test travel v] y [force v] | units N |
+----------------------+----------------------------------------------------------------------------+
| Channels             |  time view                                     F [N]            x [mm]     |
| [x] v DATA           |  250 +---------------------------------------------------------+ 120   |
| [x]   raw [counts]   |      |                                    ___/~~~~             |       |
| [ ]   setpoint [mm]  |      |                          ________/                      |       |
| [ ]   sample rate    |      |   ______________/~~~~~~                                 |       |
| [ ]   seq gaps       |    0 +---------------------------------------------------------+ 0     |
| [ ] > status bits    |      -30 s                     t [s]                          0         |
| [x] v Derived        |      lanes: VALID ___|~~~~~|___  MOVING ~~~|____|~~~                  |
| [x]   force [N]      +----------------------------------------------------------------------------+
| [x]   travel test    |  X-Y view                          F [N]                                    |
| (g)   stress [MPa]   |  250 +----------------------------------------+                             |
|       needs geometry |      |                         ..-*  <- live point                          |
| [ ]   stiffness      |      |              ...-''''                  |                             |
| ...                  |    0 +----------------------------------------+                             |
|                      |      0             x [mm] (test)            12                               |
+----------------------+----------------------------------------------------------------------------+
```

- `PlotDock(SafeDock)` is a `QDockWidget`: movable, floatable, closable. A custom title bar carries **[Float] [Close] [STOP]**. The title bar stays when floating, so **every floating window has its own STOP** (SW-RT-001, SW-STOP-001). Docks re-attach by double click on the title or [Float] again. All docks (Plot n, Readouts, Event log) use the same `SafeDock` base, so any floating window carries STOP.
- **≥ 2 windows.** "View ▸ New plot window" creates Plot n (default max **4**, GQ-04). Plot 1 and Plot 2 exist at first start: Plot 1 docked right with time + X-Y views, Plot 2 tabified with Plot 1, time view only.
- A closed dock is hidden, not destroyed, and can be reopened from View. "Reset layout" restores the default arrangement, re-attaches all docks and keeps the channel selections.
- Each window has **two fixed view slots**: a *time view* (always) and an *X-Y view* (toggle). Both are `PlotItem`s inside **one** `pg.GraphicsLayoutWidget`, so one `QGraphicsView` paints per window and tick (§4.6). There is no free pane grid: Thrust's grid caused SWD-PM3-04/-05.
- The time view has a left Y axis (first unit) and an optional right Y axis (second unit, own `ViewBox`). A third unit is refused with the hint "open another plot window". Ticked **status bits** go to a compact *lanes* strip under the time view (digital 0/1 traces, fixed Y range, names as static ticks). It is created only while at least one bit is ticked.

### 4.2 Channel tree (`ChannelTree`, SW-RT-002, SW-RT-004)
- Copied and adapted from TS `gui/widgets/channel_tree.py` (origin note). It shows checkboxes per channel, tri-state group nodes, a colour swatch per channel (this replaces the in-plot legend, SWD-PM3-02) and `batch()` so a group tick causes one update.
- The content comes **only** from the backend registry `backend.channels` (`ChannelRegistry`, B §7.4): key, label, unit, quantity group, prerequisite set, `available` + `reason`.
- Availability changes after calibration, tare or geometry entry. The tree re-reads the registry at 1 Hz until a change topic exists (GRQ-B-14).
- **Greyed prerequisites.** `available = False` → item disabled (grey, italic) with `reason` as tooltip (e.g. "needs load calibration + tare").
- A ticked channel that becomes unavailable stays ticked: its curve is blanked and its label reads "(n/a)". It comes back after re-calibration.

Tree (keys from B §7.4; the registry is the source, this table is only the expected grouping):
| Group | Channels (registry keys) | Prerequisites |
|---|---|---|
| DATA | `raw` · `setpoint_um` (shown in mm) · `rate_sps` · `lost_frames` | none |
| Status bits | one 0/1 channel per DATA status bit (VALID, MOVING, HOMED, ENABLED, ESTOP, HALT, PAUSED, LIMIT_START/END, LOAD_LIMIT, AFE stale/sat/settling/rate mismatch, LINK_WDG, STOP/PAUSE button, ALM, PEND, OVERRUN, POS_UNCERTAIN, no-AFE-data marker) | none |
| Force | `F_N` · `F_kgf` · `peak_n` · `force_rate_n_s` · `noise_counts` · `noise_n` | calibration + tare (`noise_counts`: none) |
| Travel | `x_mm` · `x_test_mm` · `speed_mm_s` | homed for absolute meaning; `x_test_mm`: x_zero |
| Mechanics | `k_tan_n_mm` · `k_sec_n_mm` · `work_nmm` | F (+ x_zero for the secant) |
| 3-point bend | `sigma_mpa` · `eps` | geometry entered, F |
| Limits (overlay, GUI-drawn lines) | SW pull/push trip and warning levels, FW level, from `limits.get()` (horizontal lines in force units) | calibration + tare |

### 4.3 Views and controls (SW-RT-003, SW-RT-005, SYS-003)
| Control | Behaviour |
|---|---|
| Window | 5, 10, **30**, 60, 120, 300, 600 s (combo + free entry 5–600 s) |
| Freeze | stops data updates of this window (no snapshot, no repaint); pan/zoom and the pyqtgraph context menu become active; [Live] resumes. Freezing one window does not affect the others or the recording. |
| Y range | per axis: **auto** (computed in the GUI from the snapshot min/max with hysteresis, §4.6, pure display scaling) or **manual** (min/max fields) |
| X-Y view | x ∈ {travel test, travel machine, setpoint}, y ∈ {force N/kgf, raw}; window = same length as the time view, or "since record start" / "since sequence start"; live point = latest sample |
| Units | global View ▸ Units N / kgf (SYS-003) relabels force axes and readouts: the plot uses the `F_kgf` channel, readouts use the `calc.units` helpers (B §15.4 rule 8). This is a display choice stored in the session |
| Extrapolated / invalid | samples flagged by the backend (beyond 3× the largest calibration force, SW-CAL-008; saturated; no AFE data) are drawn in a dashed/grey "invalid" style via a per-key validity mask from the snapshot (GRQ-B-08, GRQ-B-09) |

**Readouts dock** (`ReadoutDock`, copied/adapted from TS `gui/widgets/readout.py`, SW-RT-005):
- Fields: force, travel (test and machine), raw, sample rate. Each has Value / Min / Max / **State** ∈ {n/a, STALE, SATURATED, INVALID, EXTRAPOLATED (GRQ-B-09), ok}, refreshed at 10 Hz from `backend.data.latest()` (`LatestSample`).
- The display-only EMA (τ 0.3 s) is optional and labelled "smoothed".
- Large font for force and travel.

### 4.4 Event log dock
A table of FW EVENTs and backend events (time, source, code, text): stops with cause, faults set/clear, VALID auto-clear, MOVE_DONE, homing, enable/disable, button presses, refusals, tare/cal results, recording files. Sources: the topics `fw.event`, `stop.issued`, `halt.*`, `safety.*`, `motion.*`, `seq.*`, `rec.*`, `sample.taken`, `report.ready` and `log` via the bridge (§9.2), plus GUI-side refusals (`PreconditionError.user_text`).
- Filter by severity; copy to clipboard.
- Capped at 5 000 rows (ring) for the display; the backend log and the recording keep everything.

### 4.5 Layout persistence (SW-RT-001)
- `QSettings` (INI, `%APPDATA%/BirdBendStand/gui.ini`):
  - `main/geometry`, `main/state` (dock positions, floating state);
  - `plots/layout` JSON `{"version": 1, "docks": [{"name", "title", "floating", "geometry", "window_s", "time": {"channels": [...], "y": {"L": "auto" | [min, max], "R": ...}}, "xy": {"enabled", "x", "y", "y_range"}}]}`;
  - `ui/units`, `ui/travel`, `ui/last_tab`, `ui/theme`.
- **Restore rules:**
  - unknown channel keys are dropped (logged);
  - a floating window whose geometry is outside every current screen is moved onto the primary screen;
  - a corrupt JSON gives the default layout plus a log warning (TS `main_window.py:1071-1133` pattern);
  - Freeze is never restored (a window always starts live).
- Saved on close and on "View ▸ Save layout now". Sessions (backend) never contain the GUI layout.

### 4.6 Performance design (NFR-001: ≥ 20 fps, p95 frame interval ≤ 50 ms; NFR-002 STOP click ≤ 50 ms p95)

**Load to handle.**
- 80 Hz × 30 s = **2 400 samples per channel**, about 15 analog channels plus about 20 bits.
- A 600 s window = 48 000 samples per channel, which needs decimation.
- Thrust_Stand failed at 400 Hz × 32 curves in a pane grid. That data volume is 10× larger than ours, but the defect was in **painting**, not in data volume (R3 §6.2; TS `03_SW/docs/SW_test_report_preM3.md` §4–§5).

**Budget per refresh tick (33 ms timer → 30 fps nominal, 20 fps floor):**
| Stage | Budget (p95) | Design |
|---|---|---|
| `data.snapshot()` (copy + decimation, B §7.5) | ≤ 3 ms | one call per window-length group (TS pattern); returns `px_width` min/max pairs per key |
| `setData` for all curves | ≤ 4 ms | `PlotCurveItem` (not `PlotDataItem`), arrays handed over without copies |
| paint (all visible windows) | ≤ 15 ms | 1 `GraphicsLayoutWidget` per window, no axis repaint in steady state (below) |
| indicators + gates + readouts (10 Hz) | ≤ 2 ms | change-only widget updates (`setText` only if the text differs) |
| GC (GUI-thread policy) | ≤ 5 ms per tick; full GC ≤ 16 ms, rare | `gc_policy.py` copy (§9.4) |
| **Total** | **≤ 30 ms** | leaves headroom so a STOP press is served within the same 50 ms |

**pyqtgraph settings (binding for the implementation):**
1. `pg.setConfigOptions(antialias=False, useOpenGL=False)`. OpenGL is evaluated in the perf test as an option only.
2. **Relative time axis:** x = t − t_end ∈ [−window, 0], where `t_end` is the device time of the newest sample in the snapshot (GRQ-B-08). The shift is a display transform. The X range stays constant, so the bottom `AxisItem` is not re-generated every tick. A scrolling absolute axis re-generates tick text and picture each frame (the TS top profile item: `AxisItem` `QPicture.play` ≈ 1.7 ms per axis). The absolute device time is shown in the window's title strip only.
3. **Explicit Y ranges, no lazy auto-range:** `ViewBox.disableAutoRange()`. In auto mode the GUI computes the range from the snapshot min/max (NaN-aware) with 5 % margin and **hysteresis**: expand immediately, shrink only when the data span is < 70 % of the range for ≥ 1 s. Then `setYRange(lo, hi, padding=0, update=False)`. This removes the double paint per refresh (SWD-PM3-05 cause 2) and keeps the Y axis static most of the time.
4. `enableAutoSIPrefix(False)` on every axis (SWD-PM3-01). Fixed axis widths (58 px) so windows line up; tick font set once.
5. `setClipToView(False)`, `setDownsampling(auto=False)`: the backend already hands over exactly the visible window, decimated to `px_width` min/max pairs (pyramid ×4/16/64/256, TS `core/ringbuffer.py` reused by B, B §7.5). The GUI interleaves each pair into 2 points per pixel column. `connect="finite"` only on channels that can carry NaN gaps (fallback frames, unavailable); `skipFiniteCheck=True` elsewhere.
6. **No `LegendItem`** inside the plot: colours appear in the channel tree and the title strip (SWD-PM3-02, also cheaper).
7. **X-Y view:** `data.xy(x_key, y_key, window_s, max_points=4000)` (stride decimation with per-stride extrema kept, B §7.5); one `PlotCurveItem` plus one single-point `ScatterPlotItem` (live marker from `data.latest()`). Explicit ranges with the same hysteresis.
8. **Skip work:** windows that are hidden (tabified behind another dock, minimised, closed) or frozen are not updated. The snapshot is requested only for the union of keys of visible windows.
9. **Timer:** one `QTimer` with `Qt.PreciseTimer`, 33 ms (SWD-PM3-06: a coarse timer fires every 46.9 ms on Windows). Readouts and gates run every 3rd tick (10 Hz); link stats run at 1 Hz. Indicators run every tick (§2.4).
10. **Lanes strip** for status bits: one `PlotCurveItem` per ticked bit with a constant offset (bit·1.2). The fixed Y range means no axis repaint.

**What we do not take over from Thrust_Stand (SWD-PM3-05 root causes → countermeasure):**
| TS cause (preM3 report) | Effect measured there | Our countermeasure |
|---|---|---|
| one `pg.PlotWidget` (own `QGraphicsView`) per pane, up to 8 panes per window | 6–13 ms paint per pane, cost scales with panes | fixed 1 view per window with ≤ 2 `PlotItem`s + optional lanes; max 4 windows; no free grid |
| lazy Y auto-range → second paint per refresh | fps 6 → 10 when auto-range was off | explicit computed ranges with hysteresis (rule 3) |
| absolute, scrolling time axis + SI prefix | axis picture regenerated every frame; "(x0.001)" misreadings | relative time axis, SI prefix off (rules 2, 4) |
| `CoarseTimer` 33 ms → 47 ms | 21 fps max in the default layout | `PreciseTimer` (rule 9) |
| overflow channels opened new panes (SWD-PM3-04) | 13 + 10 panes for 32 curves | third unit refused; the user opens another window |
| GIL convoy with CPU-busy backend threads (simulator, pipeline at 400 Hz) | 45 ms → 491 ms render with one busy thread | B already sets `setswitchinterval(0.001)` + `timeBeginPeriod(1)` and keeps per-frame work ≤ 1 ms (B §16). Requested: a simulator mode out of process or rate-limited for perf runs (GRQ-B-16). 80 Hz is 5× lighter than TS |
| GC finalising Qt objects in backend threads (SWD-PM3-07 deadlock) | process hang, STOP dead | GUI-thread GC policy + parented pyqtgraph menus (§9.4) |
| on-screen E-STOP served by a saturated event loop | 91–235 ms click latency | the tick budget above keeps the loop ≤ 30 ms busy; the STOP handler is synchronous (`backend.stop` ≤ 5 ms, priority write B §4.5) and fires on press; the perf test measures press → wire (§10.3) |

**Measurement built into the GUI.**
- The refresh handler records frame intervals and per-stage durations in a ring buffer.
- *Tools ▸ Performance overlay* shows p50/p95 fps, paint ms and snapshot ms.
- The same counters are exposed to the perf test (`MainWindow.perf_stats()`).
- A perf smoke run is part of every milestone from M1 on (R3 §7.2 item 6).

---

## 5. Stop handling

### 5.1 Stop paths (end-to-end)
| Path | GUI element | Thread that sends | Backend call | Wire result | Latency target | Req |
|---|---|---|---|---|---|---|
| On-screen STOP | `StopButton` in toolbar, Manual tab, every dock title bar, every `SafeDialog` / `SafeMessageBox` / `SafeFileDialog`, every wizard, tare popup | **GUI thread, synchronous** (no executor, no signal hop) | `backend.stop(source)` → `StopResult`: non-blocking ≤ 5 ms, priority TX path (B §4.5), `terminate_all` (sequence / wizard / tare terminated, B §3.5); never raises | STOP | press → frame ≤ 50 ms p95 | SW-STOP-001, NFR-002, IF-011 |
| Pause/Break, Ctrl+Break | none (keyboard, system-wide) | **hotkey thread** of B (`io.win_hotkey.GlobalHaltHotkey`, no Qt) | `backend.halt("hotkey")`: HALT, ≤ 20 tries in 1 s until ACK or HALT flag; result events `halt.confirmed` / `halt.unconfirmed` | HALT | key → frame ≤ 50 ms p95 | SW-STOP-002, NFR-003 |
| Hotkey fallback (`status().hotkey` unavailable) | app-wide `QShortcut(Pause)`, `QShortcut(Ctrl+Break)` with `Qt.ApplicationShortcut` | GUI thread | `backend.halt("app-shortcut")` | HALT | as above, app focused only | SW-STOP-002 (degraded) |
| Pause | toolbar Pause, Sequence [Pause] | GUI thread | `backend.pause(source)` → **FW PAUSE command** (Orchestrator decision; same FW behaviour as the physical button) | controlled stop / PAUSED | – | SW-STOP-004 |
| Physical STOP/BREAK, PAUSE, E-stop, limits, FW load limit | none (FW) | – | backend reacts to DATA/EVENT | – | display ≤ 200 ms | SW-STOP-003, SAF-SW-005 |
| SW limits, link loss, recording failure | none (backend) | backend | backend sends STOP itself | STOP | violating frame → STOP ≤ 50 ms | SAF-SW-001, SAF-SW-003, SW-ACQ-004 |

### 5.2 `StopButton` widget (copied from TS `gui/widgets/estop_button.py` + `gui/estop.py`, origin note, renamed)
- `QPushButton("STOP")`, object name `stopButton`, red style, `setFocusPolicy(Qt.NoFocus)`, `setAutoDefault(False)`, `setDefault(False)`. It is never the escape button of a dialog. Size variants `large` (toolbar, Manual tab) and `compact` (docks, dialogs).
- The press goes to the process-wide dispatcher `gui/stop.py: trigger_stop(source)`, which calls the installed handler **inside a `try`** (a STOP press never raises into Qt). It keeps a history of `(monotonic_ns, source)` for tests and the log.
- Handler (installed by `MainWindow`): `result = backend.stop(source)` → stop banner (§2.3) with the **real** `StopResult` (sent / reason; TS SWD-M1-05: never show "sent" when it was not). B §15.4 rule 7.
- Reacts on `pressed`, not `clicked`. This saves the press-to-release time (≈ 80–150 ms of a normal click) toward the 50 ms NFR-002 budget. A press that the user drags off the button still stops (fail-safe).
- Tooltip: "STOP: immediate stop, driver keeps holding, sequence terminated. Keyboard: Pause/Break (HALT, latched)."

### 5.3 Global Pause/Break hotkey (SW-STOP-002, NFR-003)
- **Backend part (B, done in B's design).** `io.win_hotkey.GlobalHaltHotkey` is copied from TS `io/win_hotkey.py` @ 9473c68 (B §17). It is a daemon thread with its own Win32 message loop and no Qt:
  - `RegisterHotKey(NULL, …, MOD_NOREPEAT | mods, VK_PAUSE)` for none/Shift/Alt/Win, plus `MOD_CONTROL + VK_CANCEL` (Ctrl+Break);
  - a `WH_KEYBOARD_LL` fallback that only posts a message (TS SWD-M1-07);
  - a 250 ms liveness ping.

  The callback `backend.halt("hotkey")` runs **on the hotkey thread**, so a frozen GUI does not disable the key. `Backend.start()` runs before the main window is shown, so the key works before Connect (B §15.4 rule 1). `io/elevation.py` warns that the key is not delivered to elevated windows.
- **GUI part:**
  - the KEY chip shows `status().hotkey` (active / unavailable + reason). The tooltip states the limitation "not delivered while an elevated (Administrator) window has focus" (TS D-65 → GF-03);
  - *Tools ▸ Test Pause/Break key…* (TS D-58 test mode; refused while moving) needs a backend API (GRQ-B-15);
  - when the key is unavailable the GUI installs the application-level `QShortcut` fallback (§5.1) and the KEY chip turns red;
  - with no link, `halt()` returns "not sent" and the banner says so.
- The GUI never registers a `QShortcut` for Pause while the global key is active. The global registration consumes the key, and a double path would only create confusing double HALTs.

### 5.4 Dialog infrastructure (STOP in every dialog, SW-STOP-001)
- `SafeDialog(QDialog)`: a top bar with the hint "Pause/Break = HALT" and a compact `StopButton`; the content area below (TS `gui/dialogs/safe_dialog.py:47-78`). **All** application dialogs derive from it.
- `SafeMessageBox`: adds `StopButton` (`ActionRole`). A STOP press closes the box with "no answer" (`NoButton`), so no confirmed action follows.
- `SafeFileDialog` and the helpers `get_open_file_name` / `get_save_file_name`: non-native dialogs. `AA_DontUseNativeDialogs` is set before `QApplication` exists, so file dialogs can carry STOP (TS `gui/app.py:50`).
- `SafeWizard` (§6.1) and `TarePopup` (§6.4) also derive from `SafeDialog`.
- **Modality:**
  - confirmations and message boxes are application-modal (short-lived). They carry STOP, and the global key keeps working;
  - wizards, the tare popup and link statistics are **non-modal**, so the toolbar STOP, the plot windows and the readouts stay usable (GQ-10).

### 5.5 Confirmation dialogs (SAF-SW-004: Enter/Space never confirm, STOP reachable)
`ConfirmDialog(SafeDialog)` rules:
- **No default button** (`setDefault(False)`/`setAutoDefault(False)` on all buttons).
- The confirm button has `Qt.NoFocus` and no mnemonic.
- An event filter on the dialog swallows `Key_Return`, `Key_Enter` and `Key_Space` (they never reach a button). `Esc` = Cancel.
- Initial focus is on Cancel. Confirming therefore needs a **mouse click** on the confirm button.
- Where the SRS asks the operator to assert a fact, a checkbox must be ticked first (e.g. "Specimen is unloaded"). The confirm button stays disabled until it is ticked. Ticking the checkbox by keyboard is allowed and harmless.
- The dialog shows the backend's CONFIRM text (`GateItem.text`, or `ConfirmRequest` from an engine) and **live values** (force, homed state). It re-reads the gate every 0.5 s (TS ARM-dialog pattern). If the CONFIRM item disappears, the dialog closes as "not needed". If a REFUSE item appears, it closes with that text.

| ID | Situation (CONFIRM source in B) | Text (summary; final text = backend's) | Assertion checkbox | On confirm | Req |
|---|---|---|---|---|---|
| C-01 | `home` gate CONFIRM: abs(F) ≥ 5 % FS **or** unknown load (B §5.6) | "Load on the specimen: 132.4 N (6.8 % FS > 5 %) / load unknown. Homing moves the axis to the START switch while loaded." | "I accept homing under load" | `motion.home(load_confirmed=True)` (FW confirmed flag) | SAF-SW-004, SAF-FW-021, D-15 |
| C-02 | `disable` gate CONFIRM | "Specimen unloaded? Disabling removes holding torque; the specimen may spring back; the axis will be NOT homed." | "Specimen is unloaded" | `motion.disable(confirmed=True)` | SAF-SW-004 |
| C-03 | `estop_clear` gate CONFIRM | "Clear E-STOP: button released (input closed ≥ 100 ms)? After clearing, the driver stays disabled: ENABLE and HOME are required. No motion restarts." | "E-stop button released and area safe" | `estop_clear_async(confirmed=True)` | SAF-SW-004, SAF-FW-006 |
| C-04 | Restore board defaults (GUI-side caution, no backend gate) | "Restore all parameters to defaults (RAM; NVM unchanged until Save)." | – | `config.defaults_async()` | SW-CFG-004 |
| C-05 | `travel_cal` `EngineState.needs_confirmation` (> 20 %: candidates 160 / 1280 steps/mm, D-27; > 5 %; outside ±20 % of expected) | backend text incl. old → new value and candidates | "Measured value checked" | `travel_cal.continue_(inputs, confirmed=True)` | SW-CAL-003 |
| C-06 | `load_cal` `needs_confirmation` for a WARN fit (GRQ-B-10) | NL_span value and residuals | "I accept the WARN linearity" | `load_cal.continue_(confirmed=True)` | SW-CAL-007 |
| C-07 | `sequence_start` gate CONFIRM / WARN items (HOME steps under load, SAF-SW-006 margin, LOW_SPAN, extrapolation, hotkey unavailable) | the item list | – | `sequencer.start(seq, confirmations=…)` | SAF-SW-006, SW-SEQ-005 |
| C-08 | Discard an unsaved sequence / marks preset (GUI) | "Discard changes?" | – | local | SW-SEQF-001 |
| C-09 | Close the application while moving / sequence / recording / wizard (GUI, from `status()`) | "Closing sends STOP, ends recording and leaves the driver enabled (holding)." | – | §5.8 | SW-STOP-001 |

One dialog class serves all nine cases, so one GUI test covers the keyboard rules for all of them (§10.2).

### 5.6 Clear stop (SW-STOP-003)
`ClearStopDialog(SafeDialog)` shows the latched indicators with their `clear_hint` (B §6.5) and one row per clear command. Each row is driven by its precomputed gate (B §5.6), and REFUSE texts are the waiting condition (e.g. "release the STOP/BREAK button", "E-stop input open"):

| Row | Gate | Button | Call |
|---|---|---|---|
| HALT (key / button / PC) | `clear_stop` | [Clear HALT] | `clear_stop_async()` (HALT_CLEAR) |
| E-STOP | `estop_clear` (CONFIRM) | [Clear E-STOP] + C-03 checkbox in the dialog | `estop_clear_async(confirmed=True)` |
| Faults (load limit, AFE, step, wiring, homing) | `fault_clear` | [Clear faults] | `fault_clear_async()`; a NACK detail names the remaining cause (FW-CMD-003) |

- All three buttons follow the ConfirmDialog rules (no default, `NoFocus`). Results (`ClearResult`) are shown per row.
- **No motion restarts after a clear** (B §5.5). After an E-STOP clear the dialog ends with "ENABLE the driver and HOME the axis (Manual tab)" and the button [Go to Manual tab].

### 5.7 Behaviour on FW / backend stop events
The backend terminates sequences, wizards and tares (SW-STOP-003, SAF-SW-001/003). The GUI **reflects** that on the next refresh tick (≤ 33 ms after the backend state change):

| Event / state | Banner | Indicators | Manual tab | Wizard window | Tare popup | Sequence tab | Plots / recording |
|---|---|---|---|---|---|---|---|
| STOP sent (GUI) | grey "STOP sent" (10 s) | – | jog released; slider snaps to the commanded target | engine → `ABORTED` (STOP): reason + [Close] / [Restart wizard] | "Aborted by STOP" | ABORTED (STOP) | event row; recording continues |
| HALT (key / button / PC) | red, with source + clear procedure | HALT red | motion controls greyed (gate) | page → "Aborted: HALT (<source>)", only [Close] / [Restart wizard] | aborted | ABORTED (HALT <src>) | event row |
| ESTOP | red, "position lost" | ESTOP red, HOMED red, ENA red | greyed; HOME/ENABLE need Clear first | aborted; a travel wizard restores spm0 when the board is reachable again (B §9.3; after a link loss: GRQ-B-17) | aborted | ABORTED (ESTOP) | event row |
| PAUSED (button / GUI) | amber + [Resume] | PAUSED amber | manual: just stopped (controlled) | proposed: as for STOP, operation terminated → `ABORTED` (B to confirm, GRQ-B-10 d) | aborted (no tare under motion/pause) | PAUSED, [Resume] enabled | VALID 0 during the pause |
| RESUME_REQUEST (button while paused) | amber banner hides | PAUSED clears on resume | – | – | – | backend resumes (SW-STOP-004); toast "Resumed by PAUSE button" | – |
| LIMIT_x | red, "jog away from the switch" | LIM red | jog toward the switch greyed, away allowed (gate) | aborted | aborted | ABORTED (LIMIT) | event row |
| LOAD_LIMIT (FW) / SW trip | red with value | LOAD red | greyed until Clear (FAULT_CLEAR) | aborted | aborted | ABORTED | event row |
| AFE stale / saturated | red | AFE red | greyed (FW refuses) | capture → invalid point | refused / aborted | ABORTED (if moving) | curves drawn in the invalid style |
| LINK_WDG / LINK LOST | red "LINK LOST" | LINK red, WDG | all greyed | aborted | aborted | ABORTED (LINK) | gap in plots; recording counts the gap |
| ALM (D-16) | amber "Driver alarm – no automatic reaction (check driver)" | ALM amber | unchanged (info) | warning line | – | warning line | event row |
| Recording failure | red | REC red | – | – | – | backend: controlled stop of the sequence | – |

### 5.8 Close / exit
- `closeEvent`: if `status()` reports moving, an operation (sequence, wizard, tare) or recording → confirmation C-09.
- Then `backend.shutdown()` (B §15.1: STOP if moving, stop recording, disconnect, join threads). The driver keeps holding (D-13).
- Then the GUI saves the layout and restores the stop handler.
- The GUI never sends DISABLE on exit (load could drop, D-13). Whether the PO wants an "unload & disable" assistant at exit: GQ-14.

---

## 6. Wizards (SW-CAL-001…009, SW-TARE-001…003, SW-WIZ-001/002)

### 6.1 Common wizard frame (`SafeWizard`) on the backend engine protocol (B §9.1)
A custom `SafeDialog` with a `QStackedWidget`. It is **not** a `QWizard`: QWizard makes "Next" the default button, so Enter would advance and could start motion (P3).

```
+-- Travel calibration - step 3 of 7: Move 10 mm --------------------------------- [## STOP ##] -+
| Pause/Break = HALT                                                                              |
+--------------------------------------------------------------------------------------------------+
| (1) Check > (2) Backlash > (3) Reference > [4 Move 10 mm] > (5) Enter D1 > (6) Move 50 mm > (7) D_tot > (8) Result |
+--------------------------------------------------------------------------------------------------+
| <EngineState.title>                                                                              |
| <EngineState.instruction: what the operator does, what the stand will do>                        |
| <inputs generated from EngineState.inputs (InputSpec: label, unit, range, decimals, default)>    |
| <phase-specific view: checklist / live stats / fit plot + table / result table>                  |
| live:  x 102.345 mm   target 112.345 mm   F 0.4 N   state MOVING                                 |
| [##############--------------]  64 %                        <- EngineState.progress              |
| messages: (x) EngineState.errors  (!) EngineState.warnings   - verbatim                          |
+--------------------------------------------------------------------------------------------------+
| [Cancel]                                                        [Repeat]   [ Continue > ]        |
+--------------------------------------------------------------------------------------------------+
```

**Rules.**
- **Renders the engine.** The wizard renders `engine.state()` → `EngineState(kind, phase, step_index, step_count, title, instruction, inputs, progress, stats, result, warnings, errors, needs_confirmation, can_continue, can_repeat, can_cancel)`. It refreshes on the engine's `subscribe` callback (via the bridge) and on each tick for progress.
- **Engine owns the logic.** The engine owns the state machine, preconditions, computations, plausibility checks, board writes and texts. The GUI only maps `phase` to a phase-specific view widget and the step strip.
- **Buttons map to engine calls:**
  - [Continue] → `engine.continue_(inputs, confirmed=False)`;
  - [Repeat] → `engine.repeat()`;
  - [Cancel] → `engine.cancel()`;
  - enable states come from `can_*`.

  There is no Back button: the engine has no backward transition.
- **Keyboard.** **No button is default or auto-default.** If the next `continue_` starts motion, the button is labelled with that motion ("Move 10 mm ▶") and is reachable by mouse only (`NoFocus`). This needs `EngineState.continue_label` and `continue_moves` (GRQ-B-10). Until that exists, the GUI keeps a display table phase → label in `wizards/labels.py`. Enter in an input field only commits the field.
- **Confirmations.** `needs_confirmation` (`ConfirmRequest`) → `ConfirmDialog` (C-05 / C-06) → `continue_(inputs, confirmed=True)`.
- **STOP and aborts.** STOP (top bar) → `backend.stop("wizard:<kind>")`. STOP, HALT, ESTOP, latches and link loss bring the engine to phase `ABORTED` with the reason (B §9.1). The page then shows the reason and offers only [Close] (and [Restart wizard] → `engine.start()` again).
- **Cancel or any abort leaves the active calibration unchanged** (SW-CAL-001, B §9.1). For the travel wizard the engine writes `spm0` back (B §9.3, phase CANCEL); the wizard shows that result or the warning "board steps/mm differs from the saved calibration".
- **Window.** Non-modal, always on top of the main window (`Qt.Tool`); the plots and the toolbar stay usable (GQ-10). One operation at a time (B §3.5). Closing the window = Cancel; past the first phase it asks "Cancel calibration?" (C-08 style).

### 6.2 Travel calibration wizard (`travel_cal`, B §9.3; SW-CAL-002…004, R4 §5, D-27)

| # | Engine phase | Shown (texts from the engine; summary) | Operator input (`InputSpec`) | Continue → | Exit / next | STOP / cancel |
|---|---|---|---|---|---|---|
| 1 | `CHECK` | Checklist: connected, enabled, homed, no latch, no specimen (abs(F) < 2 % FS if calibrated, else confirmation), room for 2 + 10 + 50 mm in + direction within SW/soft limits. Shows current steps/mm (board), expected (session `expected_spm`, default 160), DIP candidates 160 / 1280 (D-27) | – (checkbox "specimen removed" when the engine asks for the confirmation) | [Start ▶] → `start()` / `continue_()` | all ✓ → 2 | Cancel: close |
| 2 | `BACKLASH` | "The axis moves +2 mm (backlash take-up) at 2 mm/s." live progress | – | [Move +2 mm ▶] | done → 3 | ABORTED → [Close] / [Restart] |
| 3 | `REFERENCE` | "Zero your caliper / dial gauge now, or mark the carriage. All further moves go in the same direction." | – | [Reference set, continue] | → 4 | Cancel |
| 4 | `MOVE1` | "The axis moves exactly N1 = round(10·spm0) steps (≈ 10 mm)." live x / target / progress | – | [Move 10 mm ▶] | MOVE_DONE → 5 | ABORTED (measurement invalid) |
| 5 | `ENTER_D1` | "Measure the distance from your reference." After entry: spm1 = N1/D1, change in %; plausibility errors/warnings; confirmation C-05 when required; then SET + read-back ("board ✓ 159.204") | D1 [mm, 3 decimals] | [Apply D1] → `continue_({"D1": v})` (→ C-05 → `continue_(…, confirmed=True)`) | verified → 6 | [Repeat] = `repeat()` (engine-defined); Cancel → spm0 restored |
| 6 | `MOVE2` | "The axis moves N2 = round(50·spm1) steps further (≈ 50 mm)." | – | [Move 50 mm ▶] | MOVE_DONE → 7 | ABORTED |
| 7 | `ENTER_DTOT` | "Measure the **total** distance from your reference (nominal 60 mm)." After entry: spm2, incremental value N2/(D_tot − D1), consistency warning "repeat" if > 0.5 % | D_tot [mm] | [Apply D_tot] → `continue_({"D_tot": v})` | → 8 | as 5 |
| 8 | `RESULT` → `ACCEPT` | Table spm0 → spm1 → spm2 (3 decimals, % change), D1, D_tot, N1, N2, warnings. Accept = SET spm2 + read-back + SAVE (NVM) + `travel_<UTC>.json` + `active_travel.json` | – | [Accept & save] → `continue_()` | done → summary on the Calibration tab | Cancel → spm0 restored |

```
Phase CHECK                                         Phase ENTER_D1
+---------------------------------------------+    +---------------------------------------------+
| [v] connected      [v] driver enabled       |    | N1 = 1 600 steps (10 mm at 160.000)         |
| [v] homed          [v] no stop/fault latch  |    | Measured distance D1 [  10.050 ] mm         |
| [x] load < 2 % FS (now 3.1 %)               |    | -> steps/mm 159.204  (-0.50 %)              |
| [v] room 62 mm (x 102.3 -> 164.3 <= 250.0)  |    | (i) change within 5 %: no confirmation      |
| steps/mm board 160.000  expected 160.000    |    | [Apply D1]        board: ✓ 159.204          |
| DIP candidates (D-27): 160 / 1280           |    |                                             |
+---------------------------------------------+    +---------------------------------------------+
Phases BACKLASH / MOVE1 / MOVE2                     Phase ENTER_DTOT
+---------------------------------------------+    +---------------------------------------------+
| [Move 10 mm >]                              |    | N1 + N2 = 1 600 + 7 960 steps               |
| x 108.112 -> target 114.300 mm              |    | Total distance D_tot [  60.120 ] mm         |
| [##############--------]  62 %   MOVING     |    | -> steps/mm 159.015 (-0.12 % vs spm1)       |
| (ABORTED: STOP (toolbar) - measurement      |    | incremental 158.98 vs 159.204 (0.14 %) ok   |
|  invalid. [Close] [Restart wizard])         |    | [Apply D_tot]                               |
+---------------------------------------------+    +---------------------------------------------+
Phase REFERENCE                                     Phase RESULT
+---------------------------------------------+    +---------------------------------------------+
| Zero your caliper / dial gauge now, or mark |    | spm0 160.000 -> spm1 159.204 -> spm2 159.015 |
| the carriage. All further moves go in the   |    | D1 10.050  D_tot 60.120  N1 1600  N2 7960   |
| same direction.                             |    | Accept writes the board, saves NVM and       |
| [Reference set, continue]                   |    | travel_<UTC>.json.         [Accept & save]   |
+---------------------------------------------+    +---------------------------------------------+
```

### 6.3 Load calibration wizard (`load_cal`, B §9.4; SW-CAL-005…009, R4 §6, D-22)

| # | Engine phase | Shown (summary) | Operator input | Continue → | Exit / next | STOP / cancel / invalid |
|---|---|---|---|---|---|---|
| 1 | start (config) | Checklist: connected, AFE ok, not moving, no latch; AFE config that will be recorded (A/128, 80 SPS); the engine starts the stream if needed and restores it. **Info box (D-22, OI-01):** "Weights 1 kg + 10 kg cover only 5 % FS → **LOW_SPAN**; forces above 294 N will be shown as *extrapolated*." | presettle [2.0] s, capture [10.0] s, masses default 1.000 / 10.000 kg (session) | [Start] → `start(presettle_s=…, capture_s=…, masses=…)` | → 2 | Cancel: close |
| 2 | `AWAIT_OPERATOR` (point 0) | "Remove all load from the cell and the hook. Do not touch the stand." | – | [Capture zero point ▶] → `continue_()` | → 3 | Cancel |
| 3 | `PRESETTLE` → `CAPTURE` (shared) | Pre-settle countdown, then the capture progress bar (`progress`), live `stats` (N, mean, std, drift, rejected, lost) at 5 Hz, stability LED | – | – (Cancel only) | → 4 | STOP / HALT / fault → `ABORTED`, or `REJECTED` with reason |
| 4 | `EVALUATE` → `ACCEPTED` / `REJECTED` (shared) | mean raw, std, SE, N used / rejected, drift; ✓ accepted or ✗ reasons (saturated sample; > 2 % outliers; < 95 % nominal; drift > max(2·std, 20); std > max(3·std_zero, 50); masses m2 < 1.5·m1) | – | ACCEPTED: [Continue] → next point; REJECTED: only [Repeat] → `repeat()` | next point / FIT | – |
| 5 | `AWAIT_OPERATOR` (point 1) | "Hang known weight 1, wait until it hangs still." m1 < 2 % FS → **warning only** (D-22) | mass m1 [kg] (`InputSpec`, default 1.000) | [Capture point 1 ▶] → `continue_({"mass_kg": m1})` | → 3/4 | as above |
| 6 | `AWAIT_OPERATOR` (point 2) | "Hang the bigger known weight 2." | mass m2 [kg] (default 10.000) | [Capture point 2 ▶] → `continue_({"mass_kg": m2})`; [Finish with 2 points] (UNVERIFIED_LINEARITY) → GRQ-B-10 | → 3/4 → 7 | as above |
| 7 | `FIT` | Mini plot (points, fitted line, residuals). Table: mass, F_ref [N], raw mean, residual [N], residual [% span]. K [N/count] (negative accepted), B [N], R² (informative), NL_span, NL_FS, status **PASS / WARN / FAIL / UNVERIFIED_LINEARITY**. Warnings: LOW_SPAN, m1 < 2 % FS, "linear within noise", K change vs previous calibration, counts/kg vs nominal 32 212 (OI-07) | – | PASS: [Accept & save] → `continue_()`; WARN: → `needs_confirmation` → C-06 → `continue_(confirmed=True)`; FAIL: `can_continue = False`, text "points are not linear – check fixture / weights / hook"; [Re-take point ▾] → GRQ-B-10 | → 8 | Cancel → previous calibration stays active |
| 8 | `ACCEPT` / done | File (`load_<serial>_<UTC>.json`), now active, previous kept; FW thresholds re-checked (THR chip, `safety.thresholds` event: VERIFIED / FAILED); hint "Tare before measuring" | – | [Finish] | close | – |

```
Start (config)                                        Phase PRESETTLE / CAPTURE
+-----------------------------------------------+    +-----------------------------------------------+
| [v] connected  [v] AFE ok  [v] not moving     |    | Point 1: weight 1.000 kg                      |
| [v] no latch   AFE A/128, 80.0 SPS (80.1)     |    | PRE-SETTLE  1.2 s left                         |
| presettle [ 2.0 ] s   capture [ 10.0 ] s     |    | CAPTURE     [#######---------] 4.3 / 10.0 s    |
| masses [ 1.000 ] [ 10.000 ] kg               |    | raw mean 32 251   std 46   N 344   rej 0       |
| (!) 1 kg + 10 kg = 5 % FS -> LOW_SPAN;        |    | lost frames 0   stability ● stable             |
|     forces > 294 N marked "extrapolated"      |    | [Cancel]                                       |
+-----------------------------------------------+    +-----------------------------------------------+
Phase AWAIT_OPERATOR (zero / weight 1 / weight 2)     Phase EVALUATE -> ACCEPTED / REJECTED
+-----------------------------------------------+    +-----------------------------------------------+
| Hang known weight 1 and wait until it hangs   |    | mean 32 251.4  std 45.8  SE 1.9               |
| still.                                        |    | N used 800 / rejected 0   drift 4 counts      |
| mass [   1.000 ] kg                           |    | ✓ point accepted              [Continue >]    |
| (!) 1.000 kg < 2 % FS: low reference (D-22)   |    | (or ✗ REJECTED: drift 160 > max(2 std, 20) -  |
| [Capture point 1 >]                           |    |  weight still swinging?)       [Repeat]       |
+-----------------------------------------------+    +-----------------------------------------------+
Phase FIT                                             Phase ACCEPT / done
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
- Otherwise the popup follows `tare_engine.state()` / event `tare.state` (`EngineState`: phase, progress, stats, result, warnings, errors). [Cancel] → `tare_engine.cancel()`.

| State (from `GateResult` / `EngineState`) | Shows | Buttons | Next |
|---|---|---|---|
| REFUSED (gate REFUSE items) | texts **verbatim**: moving or < 1 s after a move; latch; sequence capture window active; not connected | [Close] | – |
| capture running | progress over the window (default 10 s); live mean raw, std, drift, N, outliers, lost frames; "stream started for tare (restored afterwards)" if applicable | [Cancel] | result |
| refused at the end (`errors`) | saturated sample, > 2 % outliers, std/drift limits, > 1 % lost frames, interrupted by STOP/HALT | [Repeat] → `backend.tare()` · [Close] | – |
| done | tare_raw, std, drift, N, time; thresholds re-check → THR chip ("VERIFIED" from `safety.thresholds`) | [Close] (auto-close after 5 s without warnings) | – |
| done with warning | "large offset: abs(K·(tare_raw − raw_zero_cal)) > 10 % FS – specimen loaded?" | [Keep] · [Undo tare] (GQ-15, GRQ-B-13) | – |

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
- Generators are backend pure functions (`sequencer` generators: `staircase`, `linear_ramp`, `cyclic`, `hold`, `return_`), each returning `Block(steps, loops)`. A `ValueError` text is shown verbatim at the field.
- Insertion: `Sequence.insert_block(block, mode=append|replace|insert, index)`.
- The parameter forms are built by the GUI per generator. Field ranges and defaults come from the session defaults and `motion.limits()`; a schema from B would remove that duplication (GRQ-B-18, optional).

| # | Page | Content | Buttons |
|---|---|---|---|
| G1 | Choose generator | radio list with a one-line description: **Staircase**, **Linear ramp**, **Cyclic / triangle**, **Hold / creep–relaxation**, **Return** | [Cancel] [Continue >] |
| G2 | Parameters | form (table below) | [Cancel] [Continue >] (calls the generator; errors shown) |
| G3 | Preview & insert | read-only step list of the block, mini chart (`planned_path` of a temporary sequence with the block), summary (steps, duration from `expand`, travel/load range), validation issues; insert mode ( ) append (o) insert after the selected row ( ) replace all (C-08 if dirty) | [Cancel] [Insert] |

The page list has no Back button, for symmetry with §6.1. G3 offers [Change parameters], which returns to G2.

| Generator | Parameters (B §10.6) |
|---|---|
| Staircase | kind (travel / load) · start · end · increment **or** count · `up` / `up_down` · return to zero between steps · speed · accel · settle · capture · step time · tol (load) |
| Linear ramp | x0 · x1 · speed · accel (block = travel x0 + travel x1 with `capture_during_move`) |
| Cyclic / triangle | kind · lo · hi · cycles · speed · accel · dwell_s · capture (block = 2 steps + `Loop(count = cycles)`) |
| Hold / creep–relaxation | kind (travel = relaxation / load = creep) · target · duration_s (captured throughout) |
| Return | `zero` (travel 0, test) / `home` |

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
| Main window (toolbar, banner, tabs, docks, indicator bar) | yes | §2.1 |
| Connection & Config tab | yes | §3.1 |
| Safety limits tab | yes | §3.2 |
| Test marks tab | yes | §3.3 |
| Manual tab | yes | §3.4 |
| Calibration & Tare tab | yes | §3.5 |
| Sequence tab (editor, chart, run panel) | yes | §3.6 |
| Report tab | yes | §3.7 |
| Plot window (channel tree, time view, X-Y view, lanes) | yes | §4.1 |
| Wizard frame + travel calibration phases CHECK…RESULT | yes | §6.1, §6.2 |
| Load calibration phases start…ACCEPT | yes | §6.3 |
| Tare popup | yes | §6.4 |
| Generator wizard G1–G3 | yes | §6.5 |
| ConfirmDialog (C-01…C-09), ClearStopDialog | below | §5.5, §5.6 |
| LinkStatsDialog, StatusHelpDialog, board-file report dialog | table dialogs (layout from TS) | §3.1, §2.4 |

```
ConfirmDialog (C-02 example)                          ClearStopDialog
+-- Disable driver? -------------------- [## STOP ##] +  +-- Clear stop ----------------------------------- [## STOP ##] +
| Pause/Break = HALT                                  |  | Latched: HALT (button, 14:03:12) - release the STOP button  |
| Disabling removes holding torque. The specimen may  |  |          ESTOP (14:03:10)  LOAD_LIMIT (14:03:09)            |
| spring back; the axis will be NOT homed.            |  |  HALT    gate: REFUSE "STOP button still pressed" [Clear HALT] |
| Load now: 3.2 N (0.2 % FS)                          |  |  E-STOP  gate: CONFIRM  [ ] button released, area safe       |
| [ ] Specimen is unloaded                            |  |                                          [Clear E-STOP]     |
|                                                     |  |  Faults  gate: ok                         [Clear faults]     |
| [Cancel]                          [Disable driver]  |  | After clearing: ENABLE + HOME required; no motion restarts. |
|  (focus on Cancel; Enter/Space do nothing)          |  | [Close]                                                     |
+-----------------------------------------------------+  +-------------------------------------------------------------+
```

---

## 8. Module / file layout (`03_SW/src/bend_stand/gui/**`)

```
03_SW/src/bend_stand/gui/
  __init__.py
  app.py                 entry point gui.app.main() (called by bend_stand.__main__, B F-B-12); disables native dialogs
                         before QApplication; creates Backend and calls start() before showing the window;
                         installs the GC policy after show()                       (origin TS gui/app.py)
  settings.py            QSettings factory, keys, GUI-only preferences (layout, last tab, theme)
  theme.py               safety palette (STOP red, amber, green, grey "unknown"), stylesheets, light/dark
  gc_policy.py           GUI-thread-only cyclic GC (SWD-PM3-07)                    (copy TS gui/gc_policy.py)
  bridge.py              QtBridge: EventBus topics + engine subscriptions -> queued Qt signals; future relay
                         (copy/adapt TS gui/bridge.py)
  stop.py                process-wide STOP dispatcher trigger_stop(source), history (copy/adapt TS gui/estop.py)
  gating.py              GateBinder: binds widgets to status().gates ids; REFUSE/CONFIRM/WARN handling
  indicator_map.py       pure display mapping Indicators -> chip level/text (unit-tested)
  refresh.py             RefreshScheduler: the single 33 ms PreciseTimer, gui_beat(), stage timing, perf_stats()
  format.py              display formatting only (decimals per unit, thousands separators, n/a)
  main_window.py         QMainWindow: toolbar, stop banner, tabs, docks, indicator bar, menus, close rules
  widgets/
    stop_button.py       StopButton (large/compact)                                (copy TS widgets/estop_button.py)
    safe_dock.py         SafeDock: QDockWidget with title bar [Float][Close][STOP]
    indicator_bar.py     IndicatorBar + IndicatorChip
    status_led.py        StatusLed                                                  (copy TS widgets/status_led.py)
    stop_banner.py       StopBanner
    readout.py           ReadoutDock (values + state)                               (adapt TS widgets/readout.py)
    channel_tree.py      ChannelTree (greyed prerequisites, swatches, batch)        (adapt TS widgets/channel_tree.py)
    param_form.py        ParamForm (dictionary-generated editors, locked rows)      (adapt TS widgets/param_form.py)
    endpoint_selector.py EndpointSelector (backend.endpoints())                     (adapt TS widgets/port_selector.py)
    target_slider.py     TargetSlider (handle-drag only, move_to on release, live marker)
    hold_button.py       HoldButton (press -> jog_start; release/focus loss -> jog_stop)
    step_buttons.py      StepButtons (-10 ... +10 mm -> move_by)
    unit_spin.py         QDoubleSpinBox with unit, max from motion.limits(), refuse-above-max styling
    event_log.py         EventLogDock
  dialogs/
    safe_dialog.py       SafeDialog, SafeMessageBox, SafeFileDialog, helpers      (copy TS dialogs/safe_dialog.py)
    confirm_dialog.py    ConfirmDialog (SAF-SW-004 keyboard rules, assertion checkbox, live gate re-check)
    clear_stop_dialog.py ClearStopDialog (clear_stop / estop_clear / fault_clear rows)
    status_help.py       StatusHelpDialog (indicator meanings + clear_hint)
    link_stats.py        LinkStatsDialog                                            (adapt TS dialogs/link_stats.py)
    file_report.py       report dialog for BoardConfigFile / FileFormatError
    tare_popup.py        TarePopup
    about.py
  wizards/
    safe_wizard.py       SafeWizard frame (step strip, EngineState rendering, button policy)
    labels.py            phase -> Continue label table (until GRQ-B-10)
    travel_cal.py        phase views CHECK ... RESULT
    load_cal.py          phase views start ... ACCEPT (capture + point result shared)
    generator.py         pages G1 ... G3
  tabs/
    connection_tab.py    (adapt TS tabs/connection_tab.py)
    limits_tab.py
    marks_tab.py
    manual_tab.py
    calibration_tab.py
    sequence_tab.py
    report_tab.py
  plots/
    plot_dock.py         PlotDock(SafeDock): one GraphicsLayoutWidget, time view + X-Y view + lanes
    time_view.py         TimeView (relative time axis, explicit ranges, 2 Y units)
    xy_view.py           XYView (data.xy, live point)
    lanes.py             StatusLanes (bit traces)
    autorange.py         Y-range hysteresis (pure function, unit-tested)
    axes.py              axis helpers (SI prefix off, fixed width, static ticks)
    sequence_chart.py    SequenceChart (planned path, capture points, active step, trace, live marker)
  models/
    step_table_model.py  QAbstractTableModel over sequencer.model.Sequence + loop gutter
    marks_model.py       custom key/value table model over TestMarks
    recordings_model.py  Report-tab list model
03_SW/tests/gui/          (owned by D) - see §10
```

Rules:
- **Imports.** `gui` imports only `bend_stand.core.api` (Protocols + dataclasses), `bend_stand.core.backend` (to construct the facade in `app.py`), dataclass/enum modules re-exported by `core.api`, and `bend_stand.calc.units` for display units (B §15.4 rule 8). It never imports `io`, `serial` or protocol modules. A test enforces this (§10.2 G-01).
- **Origin notes.** Every copied TS file starts with `# Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/<file> @9473c68` (D-02, SYS-010) and lists the changes.
- **Requirement tags** on classes and functions: `# Implements: SW-MAN-001` etc.

---

## 9. Qt signal adapter and threading rules

### 9.1 Layering
```
 backend (Qt-free; threads: reader, pipeline, supervisor, worker, operation runner, recorder, hotkey, sim) - B §3.1
    |  EventBus callbacks / engine subscribe() (backend threads, return < 1 ms)
    |                                                ^ sync, non-blocking: stop/halt/pause/resume/tare/take_sample/
    v                                                |   record_*/status/data.*/engine methods/motion commands
 gui/bridge.py  QtBridge (QObject, GUI-thread affinity): queued signals; future relay   | futures: *_async
    |  Qt signals (queued -> GUI thread)
    v
 widgets / tabs / docks / dialogs (GUI thread only)
```

### 9.2 `QtBridge` (adapted from TS `gui/bridge.py`) — topic mapping (B §15.3)
| Qt signal | Backend topics | Payload | Consumers |
|---|---|---|---|
| `linkChanged` | `link.state`, `link.compat`, `device.info` | link dataclasses | link widget, Connection tab, banner |
| `paramsChanged` | `device.params` | params snapshot | ParamForm |
| `deviceStatus` | `device.status` | GET_STATUS data | LinkStatsDialog |
| `fwEvent` | `fw.event` | decoded EVENT | Event log, toasts (RESUME_REQUEST, buttons) |
| `stopIssued` | `stop.issued`, `halt.confirmed`, `halt.unconfirmed` | `StopResult` / halt result | stop banner (also hotkey HALTs) |
| `indicatorsChanged` | `indicators.changed` | `Indicators` | edge toasts only (chips are polled) |
| `safetyEvent` | `safety.trip`, `safety.warning`, `safety.thresholds` | event dataclasses | banner, LOAD/THR chips, limits tab |
| `motionEvent` | `motion.done`, `motion.target`, `motion.dropped` | `MoveDone` etc. | Manual tab, Event log |
| `engineState` | `tare.state`, `cal.travel.state`, `cal.load.state` (+ engine `subscribe`) | `EngineState` | wizards, tare popup |
| `sequenceEvent` | `seq.status`, `seq.step_result`, `seq.window` | `SeqStatus`, results | Sequence tab, Report tab |
| `recordingEvent` | `rec.state`, `rec.failure`, `sample.taken` | recorder dataclasses | REC chip, banner, toasts |
| `reportReady` | `report.ready` | `ReportPaths` | Report tab |
| `logMessage` | `log` | log record | Event log |

- **High-rate data is never a signal.** The refresh timer pulls `status()`, `data.snapshot()`, `data.xy()`, `data.latest()`, `data.sequence_trace()` and `sequencer.status()` (B §15.4 rule 4). A queued signal per DATA frame would flood the event loop and delay STOP presses.
- **Subscriptions** use bound methods of the long-lived bridge (held weakly by the backend, B §15.4 rule 3). `bridge.shutdown()` unsubscribes all tokens before the window closes.
- **Futures** (`*_async`): `bridge.watch(future, on_done, on_error)` attaches a done-callback that only emits through one immortal relay object (`_DoneRelay`, TS pattern). A worker thread never holds a widget reference (SWD-PM3-07). Errors are `BendStandError`; the GUI shows `user_text` (B §15.4 rule 9).

### 9.3 Threading rules (binding)
| # | Rule |
|---|---|
| T1 | Only the GUI thread creates, modifies or deletes widgets and any QObject of the GUI. No `QThread`s of our own in `gui/`. |
| T2 | Bridge callbacks do **only** `signal.emit(payload)` (payloads are frozen dataclasses). No widget access, no blocking. |
| T3 | The GUI never calls `Future.result()` and never sleeps (B §15.4 rule 2). |
| T4 | Synchronous backend calls from the GUI thread are limited to the non-blocking set of B §15.4 rule 2 (`stop`, `halt`, `pause`, `resume`, `tare`, `take_sample`, `record_*`, `status`, `data.*`, engine methods, `motion` commands). STOP is called first and directly in the press handler. |
| T5 | The Pause/Break hotkey runs entirely in B's hotkey thread. The GUI only displays its status. |
| T6 | `backend.gui_beat()` is called on every refresh tick. The backend's jog refresh and liveness depend on it (B §5.4, §4.6), so a frozen GUI ends a jog (FW dead-man) and is reported. |
| T7 | Data arrays returned by `data.*` are copies owned by the GUI (B §7.5). `BackendStatus` is immutable and returned by reference. The GUI never mutates it. |
| T8 | No Python-owned QObject may sit in a reference cycle that a backend thread could collect: GUI-thread GC policy (§9.4), parented pyqtgraph orphan menus (TS `adopt_pyqtgraph_orphans`), no lambdas capturing widgets in backend subscriptions (only bridge-bound methods). |
| T9 | Every slot that handles a backend event catches and logs exceptions; an exception never propagates into Qt. |

### 9.4 Object lifetime and GC policy
Copy TS `gui/gc_policy.py`:
- `gc.disable()` at start;
- after `show()`: one full collection + `gc.freeze()`;
- then GUI-thread-only collections from a 100 ms timer: gen0 on threshold, gen1 every 1 s, full every 10 s;
- full collection postponed while `status().motion.moving` or an operation runs (≤ 600 s).

Measured pauses go into `perf_stats()`. Companion: `adopt_pyqtgraph_orphans()` for every `PlotItem`/`ViewBox` created. B's §3.3 lifetime rules are the backend half of the same policy.

---

## 10. GUI test strategy

### 10.1 Tooling
- pytest + **pytest-qt**, `QT_QPA_PLATFORM=offscreen` set in `tests/gui/conftest.py` **before** QApplication is imported (role rule).
- Markers: `gui`, `perf` (opt-in, real display), `winint` (Windows integration, real hotkey).
- Every test carries `@pytest.mark.req("SW-…")` and a `# Verifies:` tag.
- Fixtures:
  - `fake_backend`: implements the `core.api` Protocols (B §15) in pure Python. It records calls with timestamps and has a scriptable `status()` (incl. `gates`, `indicators`), engines (`EngineState` sequences), `data.*` arrays and EventBus emission (TS `tests/gui/fakes.py` pattern);
  - `sim_backend`: the real `Backend` connected to `"sim"`, with `backend.sim` (`SimControl`) for fault injection and the `VirtualTransportPair.wire_log` for wire timestamps (B §12, §19, F-B-17);
  - `main_window(fake|sim)`.
- Waits use `qtbot.waitUntil(cond, timeout)`, never `sleep`.
- Thread-safety stress: TS `stress_gc_deadlock.py` and `test_gc_policy.py`, adapted (backend threads allocate cycles while the GUI repaints).

### 10.2 Automated GUI tests (D, `03_SW/tests/gui/`)
| ID | Test | Backend | Verifies |
|---|---|---|---|
| G-01 | layering: `gui` imports only `core.api` / `core.backend` / `calc.units` (AST scan); backend packages import no Qt (B's test, re-run) | – | SW-PLT-002, P1 |
| G-02 | **STOP everywhere:** for all tabs, all docks (docked and floating), every `SafeDialog` subclass, every wizard phase view, tare popup, message box and file dialog → exactly one visible `stopButton`, `NoFocus`, not default, not escape. A press calls `backend.stop` synchronously (recorded in the same event-loop turn, before any queued event) | fake | SW-STOP-001, SW-RT-001, SW-MAN-005 |
| G-03 | `StopResult(sent=False)` → banner "STOP NOT SENT" with the reason; STOP fires on `pressed` | fake | SW-STOP-001 |
| G-04 | **Confirm keyboard rules:** for C-01…C-09, Return, Enter and Space (focus on each focusable widget in turn) never confirm; Esc cancels; only a mouse click on the confirm button (after the checkbox where required) confirms; STOP present | fake | SAF-SW-004 |
| G-05 | each confirmation appears for its CONFIRM item (`home`, `disable`, `estop_clear` gates; engine `needs_confirmation`) and the repeated call carries `confirmed=True` / `load_confirmed=True` | fake + sim | SAF-SW-004 |
| G-06 | indicators: `indicator_map` unit test for every `Indicators` field. With the simulator, each `SimControl`-injected condition (ESTOP, HALT button, PAUSED, LIMIT_S/E, LOAD_LIMIT, AFE stale/sat/rate, LINK_WDG, HOMED, ENA, ALM, PEND) updates its chip ≤ 200 ms after the frame (`wire_log` timestamp) | fake + sim | SAF-SW-005 |
| G-07 | stop banner and `clear_hint` per latch; ClearStopDialog rows follow the `clear_stop` / `estop_clear` / `fault_clear` gates and call the right `*_async`; no motion call follows | fake + sim | SW-STOP-003 |
| G-08 | HALT/ESTOP injected during a running wizard / sequence / tare → engine `ABORTED` shown within one refresh tick; sequence state ABORTED | sim | SW-STOP-003 |
| G-09 | Pause/Resume toolbar ↔ `indicators.paused`; `backend.pause` called; RESUME_REQUEST toast | fake + sim | SW-STOP-004 |
| G-10 | hotkey chip; `status().hotkey` unavailable → app `QShortcut` installed and calls `halt`; available → no QShortcut | fake | SW-STOP-002 |
| G-11 | toolbar Stream / Record / Sample / TARE present and functional on **every** tab | fake | SW-ACQ-001, SW-TARE-001, SW-ACQ-003 |
| G-12 | **Slider:** handle drag sends nothing; release sends exactly one `motion.move_to(value)`; groove click, wheel and keys send nothing; disabled while the `move` gate has REFUSE (not homed) | fake | SW-MAN-001 |
| G-13 | Go-to absolute/distance → `move_to` / `move_by`; Enter in the field sends nothing; step buttons → `move_by(±d)`, three +1 clicks = three calls; the pending target is displayed | fake | SW-MAN-002, SW-MAN-003 |
| G-14 | **Hold-to-jog:** press → `jog_start`; release, focus loss, app deactivate, tab change or gate close while held → `jog_stop` (exactly once); `gui_beat()` called on every tick | fake | SW-MAN-004 |
| G-15 | speed/accel above `motion.limits()` refused (not applied, red); SAF-SW-006 WARN item shown | fake | SW-MAN-005, SAF-SW-006 |
| G-16 | enable checkbox follows `indicators.enabled` (no optimistic state); HOME, test zero, VALID calls | fake | SW-MAN-006 |
| G-17 | ParamForm: every dictionary parameter shown with its editor type; board values equal the simulator; `safety.load_raw_*` / `zero_raw` rows locked; write+verify statuses OK/REJECTED/MISMATCH/BUSY/TIMEOUT displayed (`SimControl` injection); `BoardConfigFile` report cases; Restore defaults needs C-04 | fake + sim | SW-CFG-001…004 |
| G-18 | compat mismatch → RO chip, banner, motion and config write disabled | fake + sim | IF-008 |
| G-19 | limits tab: `limits.set` issues shown per field; `ThresholdState` display; motion-disabled notice from the `move` gate (SAF-SW-001/002 items) | fake + sim | SW-LIM-001/002, SAF-SW-001/002 |
| G-20 | marks: add/rename/remove custom field → `marks.set`; presets round trip; a simulator recording's `meta.json` contains the custom field | fake + sim | SW-META-001/002, SW-LIM-003 |
| G-21 | channel tree: every registry channel toggles a curve; `available = False` greyed with its `reason`; re-enabled after calibration | fake | SW-RT-002, SW-RT-004 |
| G-22 | plot window float / re-attach / close / reopen; ≥ 2 windows; each floating window has STOP; layout save → a new MainWindow restores docks, channels and window lengths; off-screen geometry corrected | fake | SW-RT-001 |
| G-23 | time view window 5–600 s; freeze stops updates; manual Y range; `autorange.py` hysteresis vectors | fake | SW-RT-003 |
| G-24 | readout state texts n/a / STALE / SATURATED / INVALID / EXTRAPOLATED | fake | SW-RT-005, SW-CAL-008 |
| G-25 | units N/kgf selector relabels axes and readouts (`F_kgf`, `calc.units`) | fake | SYS-003 |
| G-26 | **travel wizard** with the simulator: all phases (D1, D_tot entries) → board steps/mm = engine result; C-05 for 160 → 1280; cancel in each phase → board keeps spm0; STOP during a move → ABORTED view | sim | SW-CAL-001…004 |
| G-27 | **load wizard** with the simulator: zero, 1 kg, 10 kg → LOW_SPAN shown; WARN needs C-06; FAIL disables Accept; a rejected point offers only Repeat; cancel keeps the previous calibration | sim | SW-CAL-001, -005…-009 |
| G-28 | tare popup: REFUSE items verbatim (moving, latch), success → THR VERIFIED shown, large-offset warning; reachable from every tab | fake + sim | SW-TARE-001…003 |
| G-29 | sequence editor: typed editing; `validate` issues flag cells; loop wrap/unwrap (count 0 = until stopped); save/open round trip; `FileFormatError` → sequence unchanged; generator wizard inserts (append/after/replace) and the steps stay editable | fake + sim | SW-SEQ-001/002, SW-SEQF-001, SW-WIZ-001/002 |
| G-30 | sequence run with the simulator: `sequence_start` REFUSE items listed (one per reason); start/pause/resume/continue/stop/abort buttons → backend; run line follows `SeqStatus`; on the wire only absolute targets (manual + sequence) | sim | SW-SEQ-003…007, SW-MAN-002/003 |
| G-31 | sequence chart: planned path equals `planned_path`; live marker update rate ≥ 10 Hz over 5 s; active step highlighted | fake + sim | SW-SCH-001/002 |
| G-32 | report tab: list, step table, Generate → `reports.build_async(dir, cal, tare)`; options | fake | SW-REP-001…004 |
| G-33 | `rec.failure` injected → red banner, REC chip red | sim | SW-ACQ-004 |
| G-34 | link loss injected during a sequence → LINK LOST banner, controls disabled | sim | SAF-SW-003 |
| G-35 | GC policy / deadlock stress (adapted TS tests) | sim | NFR-004 (GUI part), T8 |
| G-36 | full simulator workflow smoke through the GUI: connect → enable → home → travel cal → load cal → tare → sequence → report | sim | SYS-008 |

### 10.3 Performance tests (opt-in, real display, `@pytest.mark.perf`; acceptance runs by Validator F)
| ID | Scenario | Criterion | Req |
|---|---|---|---|
| P-01 | MainWindow + simulator at 80 Hz, recording on, Plot 1 (time + X-Y, all channels incl. bits, 30 s) + Plot 2 floating, 10 min | refresh p95 ≤ 50 ms (≥ 20 fps), event-loop p99 ≤ 100 ms, 0 frames lost by the SW | NFR-001, NFR-004 |
| P-02 | as P-01, STOP pressed every 0.5 s (posted mouse press on the toolbar and dock STOP), 100 presses | press → STOP frame in `wire_log` ≤ 50 ms p95 | NFR-002 |
| P-03 | as P-01, Pause key via the fake hotkey backend (and `SendInput` on the reference PC, `winint`), 100× | key → HALT frame ≤ 50 ms p95 | NFR-003 |
| P-04 | 600 s window, 4 plot windows | ≥ 20 fps (stress, informative) | NFR-001 |
| P-05 | 1 h soak with plotting + recording | memory growth ≤ 50 MB after the buffers are full | NFR-004 |

A **perf smoke** (P-01, 60 s) runs at every milestone from the first plot milestone (M1) on, so a regression is caught early (R3 §7.2 item 6).

### 10.4 Demonstration items (procedures for Validator F, on the reference PC)
Not automatable or only partly automatable:
- perceived smoothness and readability of plots;
- dragging a floating plot window to a second monitor and restarting (layout restored);
- squeezed toolbar (STOP still visible at minimum width);
- 125 % / 150 % DPI;
- the physical Pause/Break key with another application focused, and with an elevated window focused (documented limitation, GF-03);
- X-Y view during a real sequence (SW-RT-003);
- the full workflow against the simulator (SYS-008) and later on hardware;
- Manual tab enable/HOME/test zero/VALID (SW-MAN-006);
- NVM buttons (SW-CFG-004);
- sequence chart marker smoothness (SW-SCH-002).

Each item references its requirement in the validator's procedure document.

---

## 11. Backend API — contract used and remaining requests to Implementer B

### 11.1 Contract used (B §15, B §5.4–§5.6, §7.5, §9.1, §10)
| GUI element | Backend API used | B § |
|---|---|---|
| start-up / close | `Backend(settings)`, `start()` before `show()`, `shutdown()`, `gui_beat()` every tick | 15.1, 15.4 |
| connection | `endpoints()`, `connect_async(endpoint)`, `disconnect_async()`, `status().link` (state, why, compat, stats) | 5.2, 15.1–15.2 |
| STOP / HALT / Pause / Resume | `stop(source) → StopResult`, `halt(source)`, `pause(source)` (FW PAUSE per the Orchestrator decision), `resume() → GateResult` | 5.5, 15.1 |
| Clear stop | `clear_stop_async()`, `estop_clear_async(confirmed=)`, `fault_clear_async()`; gates `clear_stop`, `estop_clear`, `fault_clear` | 5.5–5.6 |
| indicators, banner | `status().indicators` (`Indicators` + `halt_source` + `clear_hint`), `status().safety`, topics `indicators.changed`, `safety.*`, `stop.issued`, `halt.*` | 6.5, 15.2–15.3 |
| gating | `status().gates` → `GateResult(items=[GateItem(code, REFUSE/CONFIRM/WARN, text, clear_hint)])`; `PreconditionError` / `ConfirmationRequired` | 5.6, 14 |
| toolbar acquisition | `tare(window_s)`, `take_sample(window_s)`, `record_start()`, `record_stop()`, `stream_start_async()`, `stream_stop_async()`; `status().stream`, `status().recording`; topics `rec.*`, `sample.taken` | 8, 15.1 |
| Config tab | `config` (ParamMeta list/groups, board values, `write_and_verify_async`, `save/load/defaults_async`, `save_board_config` / `load_board_config → BoardConfigFile`), `status().cfg_dirty`, `config_read_only`; `safety.*` rows locked (F-B-21) | 5.3 |
| Safety limits tab | `limits.get() → LimitConfig`, `limits.set(cfg) → list[Issue]`, `limits.thresholds() → ThresholdState` | 6.1–6.3 |
| Test marks tab | `marks` (get/set `TestMarks`, presets), `session` (geometry) | 13.5–13.6 |
| Manual tab | `motion.move_to`, `move_by`, `jog_start`, `jog_update`, `jog_stop`, `enable`, `disable(confirmed=)`, `home(load_confirmed=)`, `set_test_zero`, `set_valid`, `limits() → MotionLimits`; `status().motion` (commanded/pending target, x_zero, owner, caps, SW travel range) | 5.4 |
| plots, readouts | `data.snapshot(keys, window_s, px_width)`, `data.xy(…, max_points=4000)`, `data.latest()`, `data.sequence_trace()`; `channels` registry | 7.4–7.5 |
| wizards, tare popup | `travel_cal`, `load_cal`, `tare_engine`: `state() → EngineState`, `subscribe`, `start`, `continue_(inputs, confirmed=)`, `repeat`, `cancel`; `calibrations`; topics `cal.*.state`, `tare.state` | 9 |
| Sequence tab | `sequencer`: new/load/save, `validate`, `expand`, `planned_path`, generators, `insert_block`, `start(seq, confirmations=)`, `stop`, `abort`, `continue_`, `status() → SeqStatus`; topics `seq.*` | 10 |
| Report tab | `reports.build_async(recording_dir, cal=None, tare=None)`, topic `report.ready` | 11 |
| tests | `core.api` Protocols (fakes), `backend.sim` (`SimControl`), `VirtualTransportPair.wire_log` | 15, 19 |

### 11.2 Remaining requests (gaps)
| ID | Request | Why (GUI element) | MS | Req |
|---|---|---|---|---|
| GRQ-B-01 | Add precomputed gates for the remaining GUI controls: `pause`, `resume`, `stream_start`, `stream_stop`, `record_start`, `record_stop`, `sample`, `config_write`, `cal_travel_start`, `cal_load_start`, `sequence_edit`, `valid_toggle`, `test_zero`. Add a WARN item "Pause/Break key unavailable" to `sequence_start` (GQ-09) | §2.2, §2.7, §3.4–§3.6: grey controls with reasons instead of call-and-refuse | M1/M3 | P1, SW-ACQ-001, SW-STOP-004 |
| GRQ-B-02 | `Indicators`: per-item freshness ("unknown" before the first DATA/STATUS after connect, and when the DATA age exceeds the stale limit) and the PAUSED source (button / PC), like `halt_source` | §2.4 dark ≠ OK (P5), PAUSED chip | M1 | SAF-SW-005 |
| GRQ-B-03 | `config.check(edits) → list[Issue]` (range + hard rules, same code as step (1) of `write_and_verify`) for the live rule check | §3.1 Rule check | M1 | SW-CFG-003 |
| GRQ-B-04 | `limits.recheck_async()` (public trigger of `ThresholdManager.recheck()`) | §3.2 [Re-send & verify] | M3 | SAF-SW-002 |
| GRQ-B-05 | `motion.check(kind, speed_mm_s, accel_mm_s2) → GateResult`: cap errors and the SAF-SW-006 margin WARN for the values **in the fields**, before a move is sent | §3.4 warning line | M3 | SW-MAN-005, SAF-SW-006 |
| GRQ-B-06 | `motion.reset_test_zero()` (x_zero := 0, event row) | §3.4 [Reset] | M3 | SW-MAN-006 |
| GRQ-B-07 | Define how mark edits during a recording are handled (proposal: marks frozen in `meta.json` at start; edits logged as MARK event rows and written to the sidecar at stop) | §3.3 footer | M3 | SW-META-002 |
| GRQ-B-08 | `data.snapshot` result format: common time column (device s) and `t_end` (newest sample), plus a per-key validity mask (invalid / extrapolated / no AFE data). Optional `data.xy(…, since="record" / "sequence")` | §4.3, §4.6 (relative axis, invalid style) | M1/M3 | SW-RT-003, NFR-001 |
| GRQ-B-09 | EXTRAPOLATED state in `LatestSample` and in the mask (forces beyond 3× the largest calibration force) | readouts, plots | M3 | SW-CAL-008 |
| GRQ-B-10 | Engine protocol additions: `EngineState.continue_label` + `continue_moves: bool` (the Continue that starts motion is mouse-only and labelled); the documented phase list per engine (to name the step strip); `load_cal`: (a) finish after one weight (UNVERIFIED_LINEARITY), (b) re-take a chosen earlier point from FIT, (c) a WARN fit raised as `needs_confirmation`; (d) engine behaviour on PAUSE (proposal: terminate like STOP) | §6.1–§6.3 | M3 | SW-CAL-001, -005, -007 |
| GRQ-B-11 | `reports.list_recordings(root)` (folder, date, marks, sequence name, status complete/partial, duration) and `reports.load_result(dir)` (parsed `report.json`: warnings, step/iteration results) for the Report tab; a 3-point-bend option on `build_async` (enable + geometry, default from the session) | §3.7 | M4 | SW-REP-001…004 |
| GRQ-B-12 | `SeqStatus.remaining_s` (or plan total duration) for the run line | §3.6 | M4 | SW-SEQ-003 |
| GRQ-B-13 | Optional `tare_engine.undo()` → previous `TareState` + threshold recheck (only if the PO chooses it, GQ-15) | §6.4 | M3 | SW-TARE-002 |
| GRQ-B-14 | Optional topic `channels.changed` (availability changes). Fallback: the GUI polls the registry at 1 Hz | §4.2 | M3 | SW-RT-002 |
| GRQ-B-15 | Hotkey status detail (registered hotkey / LL-hook fallback / unavailable + reason) and the test-mode API (`hotkey_test_start()` refused while moving; event with the measured delay; 10 s timeout, TS D-58) | KEY chip, Tools menu | M3 | SW-STOP-002, NFR-003 |
| GRQ-B-16 | `SimControl` coverage needed by GUI tests G-06…G-36: set/clear every status flag, FW EVENTs incl. RESUME_REQUEST, physical STOP/PAUSE presses, E-stop, link drop / stream gap, recorder write failure, NACK/BUSY/MISMATCH on SET_PARAM; plus a perf mode with the simulator out of process or rate-limited | §10 | M1 | SAF-SW-005, NFR-001/002 |
| GRQ-B-17 | Travel calibration aborted by link loss or E-stop: restore spm0 at the next possible moment (reconnect, idle), or keep the warning indicator "board steps/mm differs from the saved calibration" until resolved (GF-05) | §6.1–§6.2 | M3 | SW-CAL-001 |
| GRQ-B-18 | Optional generator parameter schemas (fields, units, ranges, defaults) so the GUI forms are not hand-coded | §6.5 | M4 | SW-WIZ-001 |

---

## 12. Requirement → GUI design traceability

Legend for "Share":
- **G** = GUI-owned;
- **S** = shared (the GUI triggers and displays; the backend implements the rule);
- **B** = backend rule (B's traceability, B §20); the GUI only shows the result.

Verification: `G-nn` / `P-nn` = §10 tests; **D** = demonstration (§10.4).

### 12.1 SW, SAF-SW, NFR and GUI-facing SYS/IF requirements
| Req | GUI design element | Section | Module(s) | Share | Verification |
|---|---|---|---|---|---|
| SYS-003 | Units N/kgf selector; force axes/readouts relabel | §2.5, §4.3 | main_window, plots, readout | S | G-25 |
| SYS-008 | Endpoint "sim" / tcp twin; full workflow through the GUI | §3.1, §10.2 | connection_tab, app | S | G-36, D |
| IF-008 | RO chip + banner; read-only parameter form; motion gated | §2.3, §2.4, §3.1 | main_window, connection_tab | S | G-18 |
| IF-011 | STOP called synchronously, never through a queue (GUI part) | §5.1, §5.2 | stop.py, stop_button | S | G-02, P-02 |
| SAF-SW-001 | Motion-disabled notice (gate REFUSE); SW-trip banner with value | §2.3, §2.7, §3.2 | gating, limits_tab, stop_banner | B | G-19, G-06 |
| SAF-SW-002 | THR chip; `ThresholdState` display; re-send | §2.4, §3.2 | indicator_bar, limits_tab | B | G-19, G-28 |
| SAF-SW-003 | LINK LOST banner and LINK chip; controls disabled | §2.3, §2.4, §5.7 | stop_banner, indicator_bar | B | G-34 |
| SAF-SW-004 | `ConfirmDialog` (no default, Enter/Space swallowed, confirm `NoFocus`, assertion checkbox, STOP); C-01 HOME under load, C-02 DISABLE, C-03 E-stop clear (+ C-04…C-09) | §5.5, §5.6 | confirm_dialog, clear_stop_dialog | G | G-04, G-05 |
| SAF-SW-005 | `IndicatorBar` (ESTOP, HALT + source, PAUSED, LIM S/E, LOAD, AFE stale/sat/rate, WDG, LINK, HOMED, ENA, ALM, PEND …) with `clear_hint`; 33 ms refresh | §2.4 | indicator_bar, indicator_map, status_help | S | G-06 |
| SAF-SW-006 | Speed-margin WARN (Manual fields, sequence cells) and C-07 at sequence start | §3.4, §3.6, §5.5 | manual_tab, sequence_tab | B | G-15, G-30 |
| SW-PLT-001 | `gui/app.py` entry; Windows 10; PySide6 + pyqtgraph | §8 | app | G | D (install), inspection |
| SW-PLT-002 | GUI imports only `core.api` / `core.backend` / `calc.units`; no logic in the GUI | §1.2, §8 | all | G | G-01 |
| SW-PLT-003 | Connect flow; link statistics line + dialog | §3.1 | connection_tab, link_stats | S | G-17, G-36 |
| SW-CFG-001 | Dictionary-generated `ParamForm` (unit, range, enum names, board, default, edit; locked `safety.*` rows) | §3.1 | param_form | S | G-17 |
| SW-CFG-002 | Save/Load file, `BoardConfigFile` report, Edit column only | §3.1 | connection_tab, file_report | S | G-17 |
| SW-CFG-003 | Rule check, Write & verify, per-row status | §3.1 | param_form | S | G-17 |
| SW-CFG-004 | NVM buttons, Restore defaults (C-04), CFG chip | §3.1, §2.4 | connection_tab | S | G-17, D |
| SW-LIM-001 | Travel limit fields; slider range; refused targets shown | §3.2, §3.4 | limits_tab, manual_tab | S | G-19, G-12 |
| SW-LIM-002 | Load limit fields, warning level, FW level; LOAD chip warning at 90 % | §3.2, §2.4 | limits_tab | S | G-19 |
| SW-LIM-003 | Session save/recall (File menu); limits in the automatic snapshot | §2.5, §3.3 | main_window, marks_tab | B | G-20 |
| SW-META-001 | Marks form + custom key/value (add/rename/remove) | §3.3 | marks_tab, marks_model | S | G-20 |
| SW-META-002 | Presets; read-only automatic snapshot view | §3.3 | marks_tab | S | G-20 |
| SW-RT-001 | `PlotDock`/`SafeDock` float/re-attach, ≥ 2 windows, STOP per window, layout persistence | §4.1, §4.5 | plot_dock, safe_dock, settings | G | G-02, G-22, D |
| SW-RT-002 | `ChannelTree` with all DATA fields, bits, derived channels; greyed prerequisites | §4.2 | channel_tree | S | G-21 |
| SW-RT-003 | Time view (5–600 s, freeze, auto/manual Y) + X-Y view | §4.1, §4.3 | time_view, xy_view, autorange | G | G-23, D |
| SW-RT-004 | Derived channels listed from the registry (computation in `calc`) | §4.2 | channel_tree | B | G-21 (+ B vectors) |
| SW-RT-005 | Readouts with state texts | §4.3 | readout | S | G-24, D |
| SW-MAN-001 | `TargetSlider`: drag sends nothing, one `move_to` on release, disabled un-homed | §3.4 | target_slider | G | G-12 |
| SW-MAN-002 | Go-to absolute/distance (`move_to` / `move_by`; arithmetic in the backend) | §3.4 | manual_tab | S | G-13, G-30 |
| SW-MAN-003 | ±0.1/1/10 mm buttons → `move_by`; commanded and pending target shown | §3.4 | step_buttons | S | G-13, G-30 |
| SW-MAN-004 | `HoldButton`: `jog_start` on press, `jog_stop` on release / focus loss; `gui_beat` keeps the backend refresh alive | §3.4, §9.3 | hold_button, refresh | S | G-14 |
| SW-MAN-005 | Speed/accel fields with max; STOP on the tab | §3.4 | manual_tab, unit_spin | S | G-15, G-02 |
| SW-MAN-006 | Enable (bound to FW), HOME, test zero, VALID toggle | §3.4 | manual_tab | S | G-16, D |
| SW-STOP-001 | STOP first in the toolbar, in every dock/dialog/wizard, `NoFocus`, synchronous, fires on press | §2.2, §5.1–§5.4 | stop_button, safe_dialog, safe_dock | G | G-02, G-03, P-02 |
| SW-STOP-002 | B's global hotkey + KEY chip + app-level fallback + test mode | §5.3 | main_window, indicator_bar | S | G-10, P-03 |
| SW-STOP-003 | Reaction to FW HALT/ESTOP from any source; ClearStopDialog; no auto-restart | §5.6, §5.7 | clear_stop_dialog, stop_banner | S | G-07, G-08 |
| SW-STOP-004 | Pause (FW PAUSE) / Resume toolbar and sequence buttons; RESUME_REQUEST toast | §2.2, §3.6, §5.7 | main_window, sequence_tab | S | G-09, G-30 |
| SW-ACQ-001 | Stream toggle on the toolbar (every tab) | §2.2 | main_window | S | G-11 |
| SW-ACQ-002 | Record toggle, REC chip | §2.2, §2.4 | main_window | B | G-11 (+ B tests) |
| SW-ACQ-003 | Take sample with window menu, result toast | §2.2 | main_window | S | G-11 |
| SW-ACQ-004 | Recording-failure banner, REC chip red; link stats counters | §2.3, §2.4 | stop_banner | B | G-33 |
| SW-CAL-001 | `SafeWizard` frame on `EngineState`: description, inputs, Continue/Repeat/Cancel, progress, STOP; cancel keeps the calibration | §6.1 | safe_wizard | S | G-26, G-27 |
| SW-CAL-002 | Travel wizard phases CHECK … RESULT (backlash, 10 mm, D1, 50 mm, D_tot) | §6.2 | travel_cal | S | G-26 |
| SW-CAL-003 | Plausibility messages; C-05 with candidates 160/1280 | §6.2, §5.5 | travel_cal, confirm_dialog | B | G-26 |
| SW-CAL-004 | Result/accept phase: board ✓, NVM, file | §6.2 | travel_cal | B | G-26 |
| SW-CAL-005 | Load wizard: zero + 2 weights, presettle + capture progress | §6.3 | load_cal | S | G-27 |
| SW-CAL-006 | Point result view with rejection reasons, Repeat | §6.3 | load_cal | B | G-27 |
| SW-CAL-007 | Fit view: residuals, R², NL_span, PASS/WARN/FAIL/UNVERIFIED, C-06 | §6.3 | load_cal | B | G-27 |
| SW-CAL-008 | LOW_SPAN info/warning; "extrapolated" state in plots/readouts | §6.3, §3.5, §4.3 | load_cal, readout, plots | B | G-27, G-24 |
| SW-CAL-009 | Active-calibration panel (AFE match / "calibration invalid"), history | §3.5 | calibration_tab | B | G-27 |
| SW-TARE-001 | TARE on the toolbar from every tab; non-modal popup; verbatim reasons | §2.2, §6.4 | main_window, tare_popup | S | G-11, G-28 |
| SW-TARE-002 | Popup progress, result, thresholds verified | §6.4 | tare_popup | B | G-28 |
| SW-TARE-003 | Refusal / warning states | §6.4 | tare_popup | B | G-28 |
| SW-SEQ-001 | Step table editor with typed fields and flagged invalid entries | §3.6 | sequence_tab, step_table_model | S | G-29 |
| SW-SEQ-002 | Loop gutter, wrap/unwrap, iteration display | §3.6 | step_table_model | S | G-29, G-30 |
| SW-SEQ-003 | Run line (`SeqStatus` phases; executor in the backend) | §3.6 | sequence_tab | B | G-30 |
| SW-SEQ-004 | VALID shown in the indicator bar and lanes during capture | §2.4, §4.1 | indicator_bar, lanes | B | G-30 |
| SW-SEQ-005 | `sequence_start` REFUSE list at Start | §3.6 | sequence_tab | B | G-30 |
| SW-SEQ-006 | k_est / pull_dir / on_trim_fail settings; APPROACH/TRIM phases shown | §3.6 | sequence_tab | B | G-30 |
| SW-SEQ-007 | Start/Pause/Resume/Stop/Abort controls; guard events shown | §3.6 | sequence_tab | S | G-30 |
| SW-WIZ-001 | Generator wizard G1–G3, 5 generators | §6.5 | generator | S | G-29 |
| SW-WIZ-002 | Insert append/after/replace (`insert_block`); editable afterwards; preview | §6.5 | generator, sequence_tab | G | G-29 |
| SW-SEQF-001 | Open/Save; `FileFormatError` keeps the current sequence | §3.6 | sequence_tab | S | G-29 |
| SW-SCH-001 | `SequenceChart` planned path with labels and capture points | §3.6 | sequence_chart | S | G-31, D |
| SW-SCH-002 | Live marker (line + point), active step, measured trace, ≥ 10 Hz | §3.6 | sequence_chart | G | G-31, D |
| SW-REP-001 | Report tab: Generate, Open HTML/CSV/folder | §3.7 | report_tab | B | G-32 |
| SW-REP-002 | Step result table display | §3.7 | report_tab | B | G-32 |
| SW-REP-003 | Re-apply calibration/tare options | §3.7 | report_tab | B | G-32 |
| SW-REP-004 | 3-point-bend options (geometry from the session) | §3.3, §3.7 | marks_tab, report_tab | B | G-32 |
| NFR-001 | Performance design §4.6 (one view per window, explicit ranges, relative time axis, PreciseTimer, GC policy) | §4.6, §9.4 | plots, refresh, gc_policy | S | P-01, P-04 |
| NFR-002 | STOP on press, synchronous, tick budget ≤ 30 ms | §4.6, §5.2 | stop_button, refresh | S | P-02 |
| NFR-003 | Hotkey thread (backend), independent of the GUI | §5.3 | – (backend) | B | P-03 |
| NFR-004 | GUI memory/GC behaviour over 1 h; deadlock stress | §9.4 | gc_policy | S | P-05, G-35 |

**Coverage:** all 59 SW-* requirements, all 6 SAF-SW requirements, NFR-001…004 and the GUI-facing SYS-003, SYS-008, IF-008 and IF-011 have a GUI design element and at least one GUI test or demonstration. NFR-005…008 are FW-only.

### 12.2 FW / IF features surfaced in the GUI (display only)
| Req | GUI element |
|---|---|
| FW-AFE-004 | AFE chip with measured rate and rate-mismatch state |
| FW-SW-003 | BTN chip (physical STOP/PAUSE inputs); event toasts |
| FW-SW-004 | ALM and PEND chips (no reaction, D-16) |
| FW-NVM-001 | CFG chip (CFG_DIRTY) |
| FW-CFG-004 | Device info line, About dialog (versions, hash, UID, feature mask) |
| FW-CMD-004 | LinkStatsDialog (GET_STATUS counters) |
| FW-STR-005 | no-AFE-data bit channel; invalid curve style |
| FW-HOM-002 | homing fault banners and `clear_hint` |
| SAF-FW-021 | C-01 sends the operator-confirmed flag (`load_confirmed=True`) |

---

## 13. Open questions for the product owner (with defaults)

| ID | Question | Default used by this design |
|---|---|---|
| GQ-01 | Tab names and order OK? (Connection & Config · Safety limits · Test marks · Manual · Calibration & Tare · Sequence · Report) | as listed; the last tab is restored at start |
| GQ-02 | Default force display unit | **N** (kgf selectable in View ▸ Units) |
| GQ-03 | Default travel shown in readouts and X axes: test travel (x − test zero) or machine coordinate? | **test travel**, machine shown as a secondary value |
| GQ-04 | Default plot layout and maximum number of plot windows | Plot 1 docked right (time + X-Y), Plot 2 tabified (time); max **4** windows |
| GQ-05 | GUI language | English only |
| GQ-06 | Colour theme | light (high-contrast safety colours); dark optional |
| GQ-07 | Manual slider: horizontal, range = SW travel limits (else FW soft limits), one move on release, clicking the groove does nothing — OK? | yes |
| GQ-08 | Keyboard jog with ←/→ held (as in Stefan's GUI)? | **off** (on-screen hold buttons only) |
| GQ-09 | If the system-wide Pause/Break key cannot be registered: allow sequence start? | allowed after an explicit confirmation (C-07); KEY chip red; the key then works only while the app is focused |
| GQ-10 | Wizards non-modal (plots and toolbar stay usable) or modal? | **non-modal**, always on top, STOP in every wizard |
| GQ-11 | After how long should the TARE chip turn amber ("tare old")? | 30 min |
| GQ-12 | Missing specimen name/number at record start: warning or refusal? | warning only |
| GQ-13 | Open the HTML report in the system browser? | yes ([Open HTML]) |
| GQ-14 | Closing the application while the driver holds a load: STOP and keep holding (default), or also offer an "unload & disable" assistant? | STOP, end the recording, keep the driver holding (D-13); no DISABLE at exit |
| GQ-15 | Tare with a large-offset warning (> 10 % FS): keep it with an [Undo tare] button, or keep it without undo? | keep, show [Undo tare] (needs GRQ-B-13); without B support: keep + warning only |
| GQ-16 | Manual "Go to" field: should Enter start the move (Stefan) or only commit the value? | Enter commits; the move needs a click on [Go] |
| GQ-17 | Screen size of the lab PC | design target 1600 × 900 at 125 %; usable at 1366 × 768 (scroll areas) |
| GQ-18 | On-screen button label: "STOP" (this design, because E-stop = the hardware power cut) or "E-STOP" (Thrust_Stand)? | **STOP** |
| GQ-19 | An additional in-app keyboard STOP key (e.g. Esc or Space as in Stefan's GUI)? | none: only Pause/Break and Ctrl+Break (HALT, system-wide). Esc closes dialogs and Space activates buttons, which would conflict |
| GQ-20 | STOP acts on mouse **press** (faster, this design) rather than on release | on press |

---

## 14. Findings for other roles

| ID | To | Finding |
|---|---|---|
| GF-01 | Integrator (C) | Orchestrator decision: **GUI Pause = FW PAUSE command**, with the same FW behaviour as the physical PAUSE button (supersedes B's F-B-03 alternative). The ICD needs the PAUSE command, the PAUSED source (button / PC) in status/EVENT (GRQ-B-02), and the name of the command that clears PAUSED ("next accepted motion command or PC clear", SRS §3.2). HALT source and RESUME_REQUEST are already requested by B (F-B-05). |
| GF-02 | Orchestrator | SW-STOP-001 "STOP in every dialog" excludes native Windows file dialogs. This design disables native dialogs application-wide (`AA_DontUseNativeDialogs`, as in Thrust_Stand). Recommend recording it as a design decision. |
| GF-03 | Orchestrator → PO | Known Windows limitation: the system-wide Pause/Break key is not delivered while an **elevated (Administrator)** window has focus (TS D-65; B's `io/elevation.py` warns about it). The physical STOP/BREAK and E-stop are the paths of record. Recommend stating this in the SRS / user notes. |
| GF-04 | Orchestrator (SRS) | SW-MAN-003 vs FW-MOT-004: B chose "latest wins" (F-B-09: rapid clicks send 11 → 13). The GUI shows the pending target so the operator sees the final target. Same decision needed as F-B-09. |
| GF-05 | Orchestrator (SRS) + B | SW-CAL-001 vs SW-CAL-002: spm1 is written to the board mid-wizard. B restores spm0 on cancel/abort when the board is idle and reachable. The link-loss / E-stop case needs a rule (GRQ-B-17). Recommend defining "active travel calibration" = board + NVM value in the SRS. |
| GF-06 | Orchestrator | Ownership: `.claude/agents/implementer-d-gui.md` and PROCESS.md §1 give D the "GUI sections of `SW_design.md`"; this task created `03_SW/docs/SW_design_GUI.md`. Please add it to D's owned paths. D agrees with B's F-B-12 proposal that D owns `gui/app.py` and B owns `bend_stand/__main__.py`. |
| GF-07 | Orchestrator | B's F-B-08 first-use flow affects the GUI. Without a load calibration + tare, motion is disabled while SW load limits are enabled, and the tare is session-only (F-B-13). The GUI shows a first-use banner (§2.3) that points to calibration / tare / disabling the limits. Please confirm the flow with the PO. |
| GF-08 | Integrator (C) | For a generated channel/indicator registry, the generator should export the final status-bit names and order of FW-STR-003 (Python module). The GUI channel tree and B's `Indicators` then never hand-list bits. |
| GF-09 | Validator F | Inputs for the SW test plan: demonstration items (§10.4) and perf tests P-01…P-05 (§10.3). P-02/P-03 use `wire_log` timestamps (B §19). The reference-PC runs need an operator (real display, physical key). |
| GF-10 | B (info) | B's gate list and D's controls match except for the gates in GRQ-B-01. Jog follows B §5.4 (backend refresh gated by `gui_beat`), so D dropped its own GUI-side jog refresh timer. `Thrust_Stand SafeMessageBox` makes Accept the default button: D uses it only for non-safety messages; all safety confirmations use `ConfirmDialog`. |

---

## 15. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-03 | Implementer D | First draft for the P1 gate: main window, toolbar, indicator bar, 7 tabs, realtime plot design with the NFR-001 performance plan, stop handling, confirmation rules (C-01…C-09), wizard phase tables (travel, load, tare, generators), wireframes, module layout, Qt adapter and threading rules, GUI test strategy (G-01…G-36, P-01…P-05), traceability, 20 PO questions, 10 findings. Aligned with `SW_design.md` v0.1 §15 (B's API names; 18 remaining requests GRQ-B-01…18), B's F-B-21 (locked `safety.*` rows) and the Orchestrator decision "GUI Pause = FW PAUSE command". |
