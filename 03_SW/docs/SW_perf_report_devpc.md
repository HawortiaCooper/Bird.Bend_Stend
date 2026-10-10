# Bird Bend Stand — PC software performance, preliminary runs on the DEV PC (SW_perf_report_devpc)

| Doc | SW_perf_report_devpc |
|---|---|
| Version | 1.0 |
| Date | 2026-10-09 |
| Author | Validator F — SW |
| Status | **Informative only.** The binding NFR-001…004 runs stay on the PO's reference (lab) PC (D-33 j, conditions MC4-2 / MC3-6). The PO approved these DEV-PC runs (Orchestrator task 3). |
| Plan | `03_SW/docs/SW_test_plan.md` §6.1 PR-1…PR-5 (TC-NFR-001-01/-03/-04, TC-NFR-002-01, TC-NFR-003-01, TC-NFR-004-01, TC-SAF-SW-001-01 rt) |
| Requirements | SRS NFR-001…004, SAF-SW-001, SW-STOP-001/002, SW-RT-006 |
| SW under test | working tree on HEAD e600169 with other roles' uncommitted edits (packaging, coverage, docs). `03_SW/src` is not edited by F. Other agents changed `03_SW/src` while the runs were going on: sha256 fingerprint over `03_SW/src/**/*.py` was 3bc540cffe4c4634 on 10-08 23:12 and 59ea21f3d7cfe14e on 10-09 19:19. Each run measured the tree as it was at that run's process start. |
| Scripts | `03_SW/tests/perf/` (new, §8) — not collected by pytest (`perf_*.py`) |
| Raw data | scratchpad `validator-f-perf/` (per run: `results.json`, `sniff.jsonl`, `hostload.jsonl`, `app.log`, `processes.log`, raw stamps) |

---

## 0. Summary

```
DEV-PC result (informative): no SW defect blocks the reference-PC run.
  Low host load:   every budget met — NFR-001 (all channels), NFR-002, NFR-003, NFR-004, SAF-SW-001.
  High host load:  NFR-001 / NFR-002 fail. Other agents' processes drove the host CPU to 80–100 %.
  600 s / 4-window stress (P-04): fails at any load (GUI process saturated).
  Defects:   SWD-P3-01 (S3) — 2 rows from the stop instant end up at the head of the next recording.
  Findings:  OBS-P3-01…06 (risks for the REF PC, §6).
  MC3-6:     no LINK LOST in ≈ 5 h of real-clock GUI operation, even with 100 % host CPU and 46 other Python
             processes (GUI stalls up to 0.83 s). The REF-PC soak still decides.
```

### 0.1 Results vs budgets

The low-load rows are the closest model of an otherwise idle REF PC. "Stratified" values keep only the samples taken while the host CPU (5-s sampler) was below the given level.

| PR / TC | Item (budget) | Low host load | High host load | Verdict (DEV, informative) |
|---|---|---|---|---|
| PR-1 TC-NFR-001-01 | all 39 available registry channels → 11 time panes + X-Y, 2 columns, Plot 2 floating, 30 s, recording, axis moving; paint-to-paint p95 of the worst Plot-1 pane (≤ 50 ms) | **38.4 ms** (run A2, host CPU p50 43 % / p95 62 %); stratified < 60 % CPU: 38.5 ms; min 29.9 fps | 77.6 ms (run A, host p50 46 % / p95 100 %); stratified 60–80 %: 78.9 ms, ≥ 80 %: 124.8 ms | PASS at low load (margin +23 %), FAIL under load |
| | event-loop lateness p99 (≤ 100 ms, reported) | 19.5 ms | 69.5 ms | PASS |
| PR-1 TC-NFR-001-04 | 4 time panes (raw, x, 24 status bits, rate) + X-Y; worst pane p95 (≤ 50 ms) | stratified < 80 % CPU: **36.6–38.8 ms** | 55.7 ms over the whole run (host p95 100 %); ≥ 80 %: 96.3 ms | PASS at low load (+23 %) |
| PR-1 TC-NFR-001-03 (P-04, informative) | 600 s window, 4 plot windows, all channels; ≥ 20 fps | B2 stratified < 40 % CPU: worst pane p95 163 ms, event loop p99 109 ms; ≈ 6–15 fps | B: 5.8–10 fps, worst pane p95 251 ms, event loop p99 175 ms | **FAIL** at any load — the app process is CPU-saturated (≈ 1.05 cores) |
| PR-2 TC-NFR-002-01 | GUI STOP click → STOP frame on the wire, p95 of 100 (≤ 50 ms) | clicks with host < 50 % (n 81): **p95 18.7 ms, max 21.7 ms** | run A: p95 79.2 ms, max 184 ms; A2 clicks with host ≥ 80 % (n 19): p95 284 ms, max 489 ms | PASS at low load (+63 %), FAIL under load |
| | of which GUI handler → wire | p95 0.29–0.47 ms, max 1.1 ms | same | the whole delay is the GUI thread being busy or not scheduled |
| PR-3 TC-NFR-003-01 | Pause key (SendInput → Win32 RegisterHotKey, another application focused) → HALT frame p95 (≤ 50 ms) | **p95 4.9 ms, max 8.5 ms, 100 / 100** (A2) | p95 4.3 ms, 94 / 100 (A: 6 presses while AnyDesk had the focus gave no HALT, OBS-P3-06) | PASS (+90 %); independent of the GUI load |
| PR-5 TC-SAF-SW-001-01 rt | SW-limit trip → STOP, 100 trials (50 PULL, 30 PUSH, 20 predicted TRAVEL_MAX), p95 (≤ 50 ms) | — | Reader stamp → STOP write **p95 3.5 ms, max 7.8 ms**; wire DATA → wire STOP p95 4.3 ms; 100 / 100 tripped, STOP mode 0; trip frame = oracle's first violating frame 80 / 80 (host p95 100 %) | PASS (+93 %) even under load |
| PR-4 TC-NFR-004-01 | 1 h, 80 Hz, recording + plots (all-channel layout) + axis moving: SW-attributable losses (0) | **0** (E2): 289 504 frames on the wire in the window → 289 504 D rows, async_overflow 0, rows_lost 0, frames_lost_link 0, FW losses 0, CRC 0, command timeouts 0 | 0 (E): 289 440 → 289 440; 2 command timeouts / 27 retries / 29 late responses during GC stalls of 320–476 ms at 100 % host CPU | PASS. Note SWD-P3-01: 2 rows of the stop instant at the head of the new file |
| | working-set growth after the buffers are full (≤ 50 MB) | **+28.2 MB** from min 15 to 60 (+23.5 MB from min 20) (E2, harness probes off) | E: +90 MB, invalid (the harness's own timestamp lists grew ≈ 85 MB) | PASS (+44 %); slope 0.65 MB/min continues (OBS-P3-04) |
| MC3-6 | LINK LOST under load | none in A, A2, B, B2, C, D, E, E2 (≈ 5 h), LINK_LOST_DIAG never written | — | no false LINK LOST reproduced (out-of-process sim) |

---

## 1. DEV PC

| Item | Value |
|---|---|
| CPU | Intel Core i7-10700 @ 2.90 GHz (8 cores / 16 threads, turbo up to 4.8 GHz) |
| RAM | 32 GB DDR4-2400 (2 × 16 GB); 16–18 GB free at start |
| GPU | NVIDIA GeForce GTX 1080 Ti (driver 32.0.15.7602) + Microsoft Basic Display Adapter (virtual) |
| Screens | 2 × HP U32 4K HDR 3840 × 2160 @ 59 Hz, Windows scaling **125 %** (Qt DPR 1.25), plus 1 virtual screen; Qt reports 3 screens |
| Storage | NVMe Samsung SSD 970 (system; recordings in `%TEMP%`) |
| OS | Windows 10 Pro 22H2, build 19045.3086; power plan "Balanced"; console session (`query session`: console, active); AnyDesk installed and connected at times |
| Python stack | Python 3.14.7 (MSC v.1944, 64 bit), PySide6 / Qt 6.11.2, pyqtgraph 0.14.0, numpy 2.5.3 (project `.venv`) |
| Time base | `time.monotonic_ns()` = `perf_counter_ns()` = QueryPerformanceCounter (checked): one time base for every process on the PC |

**Host load.** Other agents ran test suites, coverage, FW builds and the FW twin on this PC in parallel (10–46 other `python.exe`, `fw_twin.exe`, `claude.exe`, AnyDesk). A separate sampler process (`perf_hostload.py`, every 5 s) recorded total CPU %, available RAM, the CPU share of every watched process and the top-6 CPU users for every run:

| Run | When | Host CPU p50 / p95 | Other python processes | App process CPU (of 16 logical) | Note |
|---|---|---|---|---|---|
| A (PR-1 -01, PR-2, PR-3) | 10-08 23:02–23:17 | 46 % / 100 % | 10–28 | 5.4 % (≈ 0.86 core) | heavy |
| B (PR-1 -03) | 23:17–23:29 | 59 % / 96 % | 10–22 | 6.7 % (≈ 1.07 core, saturated) | heavy |
| C (PR-1 -04) | 23:29–23:41 | 59 % / 100 % | 10–26 | 3.6 % | heavy, 20 % of the time ≥ 80 % |
| D (PR-5) | 23:41–23:51 | 41 % / 100 % | 14–28 | 4.7 % | heavy |
| E (soak, probes on) | 23:51–00:53 | 32 % / 99 % | 6–46 | 4.9 % | peaks of 100 % with 40–46 python processes |
| E2 (soak, clean) | 10-09 17:50–18:52 | 32 % / 71 % | 8–24 | 4.7 % | **low** (run chosen at a quiet time) |
| A2 (PR-1 -01, PR-2, PR-3) | 18:52–19:07 | 43 % / 62 % | 16–32 | 5.0 % | low, one burst ≥ 80 % during PR-2 |
| B2 (PR-1 -03) | 19:07–19:19 | 44 % / 100 % | 12–50 | 6.6 % (≈ 1.05 core, saturated) | mixed, 29 % of the time < 40 % |

The perf processes themselves (GUI app ≈ 0.8 core; simulator + sniffer ≈ 0.03 core; sampler ≈ 0.01 core) are listed separately in each `results.json` (`own`).

---

## 2. Method

### 2.1 Set-up (per run, `perf_gui.py`)
1. **Simulator + sniffer process** (`perf_sim.py`): the product's out-of-process simulator (`bend_stand.io.sim.server.SimServer`, real clock, 80 Hz HX711 model) runs behind a transparent loopback TCP proxy. Every frame that passes is time-stamped (`monotonic_ns`) on arrival at the proxy socket, before forwarding. This is the "wire" of NFR-002/-003 (SRS: "virtual serial / sniffer"). Frames are parsed with the Integrator's oracle `ref_codec`. The proxy is independent of the GUI process (own GIL) and of the simulator's polling period.
2. **GUI process**, built exactly like `bend_stand.gui.app.run`:
   - native dialogs off, `configure_pyqtgraph()`;
   - `Backend(BackendSettings(hotkey="win32", data_dir=…, recordings_root=…))` started before `show()`;
   - `MainWindow` on the real display, 1920 × 1040 px, shown **without activation** (another application keeps the focus);
   - `GuiGcPolicy` installed with the app's `_busy` predicate;
   - private settings INI and data folder (the PO's `%APPDATA%` is not touched).
3. Connect via the GUI's connection tab → load calibration with the simulator's weights 0 / 1 / 10 kg → tare → thresholds VERIFIED → SW load limits ±1000 N → ENABLE → HOME → spring specimen 10 N/mm from 20 mm → recording on.
4. Motion loop: MOVE 10 ↔ 60 mm at 10 mm/s, re-issued 0.4 s after every standstill. The X-Y pane, derived channels and the GC deferral therefore see a moving axis (worst case).
5. Plot layouts (`--plots`):
   - `all` (TC-NFR-001-01): every available registry channel ticked in Plot 1. D-63 placement gives 11 time panes; plus an X-Y pane, 2 columns, panes ≈ 351 × 112 px. Plot 2 floating (raw, F_N). 30 s window.
   - `all600` (TC-NFR-001-03): as `all` with a 600 s window, Plots 2–4 floating.
   - `four` (TC-NFR-001-04): raw, x_mm, all status bits (24, more than the 8 in the TC), rate + X-Y, 2 columns.
   - `sigma_mpa` / `eps` were unavailable (3-point-bend option off) → 39 of 41 channels.
6. Host-load sampler process (§1) and process log (start / stop of every child by PID).

### 2.2 Measurement points
| Run | Stimulus | Measured |
|---|---|---|
| PR-1 | — (10 min) | Paint events of every pane viewport (event filter, `QEvent.Paint`) → paint-to-paint intervals per pane; refresh-timer intervals (`RefreshScheduler`); event-loop lateness (20 ms PreciseTimer probe); stage times (`perf_stats()`). Raw stamps dumped for the stratification by host load. |
| PR-2 | `perf_stim.py click`, a separate process (per-monitor DPI aware), posts `WM_LBUTTONDOWN/UP` to the top-level window at the button centre. It rotates toolbar STOP → Plot-1 dock STOP → floating Plot-2 STOP, 0.35…0.65 s apart. The target is recomputed by the GUI before every click (the STOP banner moves the docks). | stamp before `PostMessage` → first STOP frame through the sniffer. Split by the GUI's own `trigger_stop` history: click → handler, handler → wire. |
| PR-3 | `perf_stim.py key`: `SendInput` VK_PAUSE down/up into the system input stream; the HALT is cleared between presses (gate file). The backend's hotkey thread had `REGISTERED` (RegisterHotKey; Win+Pause is owned by Windows → that variant via the LL hook). Real Win32 path; the test hook was **not** needed. The foreground process at each press was recorded (never the GUI). | stamp before `SendInput` → first HALT frame through the sniffer |
| PR-5 | per trial: MOVE into a spring (PULL 50 N/mm from 20 mm, 15 → 40 mm; PUSH from 30 mm, 35 → 10 mm; limits ±150 N) or, for TRAVEL_MAX (50 mm), an "other-client" MOVE_ABS to 60 mm on the forced device path. Then back off: the latch clears. | PC wire log: Reader stamp of the tripping DATA frame (the frame whose `t_us` equals `SwTrip.t_us`) → STOP write; sniffer: same DATA frame → STOP frame. Oracle check: the tripping frame is the first frame with F beyond the limit (`f_ref.force_n`). |
| PR-4 | 1 h, fresh recording | counters every 30 s (link, pipeline, recorder, working set via `GetProcessMemoryInfo`); at the end every DATA frame through the sniffer between the record start and the last row is matched to the D rows of `data.csv` by device time `t_us` (unique per frame); `meta.json` integrity; LINK LOST diagnostics (GUI list + backend log). |

### 2.3 Deviations from the test plan (all informative)
- The DEV PC is not the REF PC and was shared with other agents (§1). The SRS budgets are judged on the low-load data; the high-load data are kept as risk evidence.
- PR-2 uses posted window messages, as the test plan allows ("posted mouse press"), not physical clicks. The OS → Qt → handler path is exercised, but the real cursor and the mouse driver are not.
- PR-3 uses `SendInput`, not a physical key: real RegisterHotKey dispatch, but no keyboard driver.
- The axis moves during every run except PR-5's own trials (heavier than the TC).
- PR-4 window = 60 min; NFR-004's "after buffers are full": the ring buffer (86 400 rows) is full at 17.9 min. Growth is reported from min 15 (test plan) and from min 20.

---

## 3. Results in detail

### 3.1 PR-1 (NFR-001, SW-RT-006)

| Run | Layout | Panes measured | Worst pane p95 / p99 / max (ms) | min fps | Refresh tick p50 / p95 / max | Event loop p99 / max | plots stage p95 / max | Host |
|---|---|---|---|---|---|---|---|---|
| A2 | all, 30 s | 12 + 2 (Plot 2) | 38.4 / — / — | 29.9 | 33.0 / 34.9 / 117 | 19.5 / 107 | — | low |
| A | all, 30 s | 14 | 77.6 / 131.7 / 305.8 (X-Y) | 25.8 | 33.0 / 62.9 / 211 | 69.6 / 191 | 12.3 / 68.7 | heavy |
| C | four, 30 s | 5 | 55.7 / 87.9 / 212 | 28.0 | 33.0 / 42.9 / 178 | 43.0 / 127 | 6.1 / 10.9 | heavy |
| B | all600, 600 s | 21 | 251.2 (X-Y) / 325.9 / 500 | 5.8 | 83.5 / 130.5 / 299 | 174.8 / 333 | 73.5 / 107 | heavy |
| B2 | all600, 600 s | 21 | 213.0 / — / — | 6.4 | 68.4 / 111.8 / 1024 | 430 / 1155 | 88.5 / 196.7 | mixed |

Stratified by host CPU (raw stamps vs the 5-s sampler):

| Run | < 40 % | 40–60 % | 60–80 % | ≥ 80 % | share of time ≥ 80 % |
|---|---|---|---|---|---|
| A2 worst pane p95 | 36.8 ms | 38.5 ms | 78.9 ms | 124.8 ms | 3.4 % |
| A2 event loop p99 | 16.0 | 18.4 | 77.4 | 78.4 | |
| C worst pane p95 | 36.6 ms | 37.3 ms | 38.8 ms | 96.3 ms | 19.7 % |
| C event loop p99 | 10.1 | 12.3 | 14.6 | 77.9 | |
| B2 worst pane p95 | 163.4 ms | 191.5 ms | 262.1 ms | 1088 ms | 8.4 % |
| B2 event loop p99 | 109.2 | 142.8 | 180.4 | 891.5 | |

Reading:
- The 33 ms refresh timer holds (≈ 30 fps) as long as the GUI process gets its CPU.
- The all-channel layout (12 PlotWidgets) costs ≈ 0.8 core for the whole app process. The 4-pane layout costs ≈ 0.6 core.
- Above ≈ 60–80 % host CPU the GUI thread is not scheduled in time and the p95 doubles.
- With a 600 s window and four plot windows the app is CPU-bound even on a quiet host: plots stage 73–88 ms p95, ≈ 9–15 fps; the X-Y pane at 600 s draws at only 6 fps. B2's tick gap of 1.02 s (host ≥ 80 %) also produced no LINK LOST.

### 3.2 PR-2 (NFR-002)
| Run | n | p50 | p95 | p99 | max | toolbar / Plot-1 dock / Plot-2 floating p95 | click → handler p95 | handler → wire p95 / max |
|---|---|---|---|---|---|---|---|---|
| A2 | 100 (0 missing) | 7.6 | 201.0 | 263.5 | 489.4 | 195 / 128 / 213 | 200.9 | 0.47 / 1.1 |
| A2, host < 50 % | 81 | 2.5 | **18.7** | — | 21.7 | | | |
| A2, host ≥ 80 % | 19 | 149.8 | 284.0 | — | 489.4 | | | |
| A | 100 (0 missing) | 15.4 | 79.2 | 136.2 | 183.8 | 88 / 60 / 68 | 78.9 | 0.29 / 0.85 |

Every click produced exactly one GUI press of the right source and one STOP frame (mode 0). The backend part (`backend.stop` → priority write) is ≤ 1.1 ms. The latency is how long the posted press waits for the GUI thread. That wait is short on an idle host (p95 18.7 ms). It grows to 150–490 ms when the PC is saturated by other processes (correlation latency vs host CPU r = 0.78).

### 3.3 PR-3 (NFR-003, SW-STOP-002)
- A2: 100 / 100 HALT frames; p50 1.0, p95 4.9, max 8.5 ms. The focus was on another application (Claude, PID 22620) for every press.
- A: 94 / 100; p95 4.3 ms, max 6.1 ms. The 6 misses were presses 94–99 while the foreground window was AnyDesk (PID 39940; someone connected remotely). AnyDesk's elevated window blocks injected input (UIPI) and the KL-01 limitation applies (OBS-P3-06).
- The path is independent of the GUI load (A at host p95 100 % ≈ A2).

### 3.4 PR-5 (SAF-SW-001 rt)
| Kind | n | p50 | p95 | max (ms, Reader stamp → STOP write) |
|---|---|---|---|---|
| PULL | 50 | 1.15 | 5.35 | 7.84 |
| PUSH | 30 | 1.07 | 2.80 | 3.26 |
| TRAVEL_MAX (predicted, other-client move) | 20 | 0.81 | 2.30 | 2.88 |
| all | 100 | 0.99 | **3.49** | 7.84; wire → wire p95 4.27, max 6.85 |

- 100 / 100 trips; every STOP is mode 0; no errors.
- For all 80 load trips the tripping frame is the oracle's first violating frame (F computed from the wire raw with the calibration K and the tare).
- The supervisor runs in the Pipeline thread and is not delayed by the GUI load (D ran at host p95 100 %).

### 3.5 PR-4 soak (NFR-004) and MC3-6
| Item | E2 (clean, low load) | E (probes on, heavy load) |
|---|---|---|
| Duration / frames (PC data_frames in the window) | 3604 s / 289 504 | 3603 s / 289 440 |
| Wire frames between record start and last row → missing in `data.csv` | 289 504 → **0** | 289 440 → **0** |
| Rows not seen on the wire | 0 | 0 |
| Rows older than the record start | **2** (SWD-P3-01) | 0 |
| async_overflow / rows_lost / frames_lost_link / frames_lost_fw / CRC | 0 / 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 0 |
| command timeouts / retries / late responses | 0 / 0 / 0 | 2 / 27 / 29 (during the GC stalls below) |
| `meta.json` integrity | complete, rows_written 290 105 (289 506 D + 599 E) | complete, 290 037 |
| Working set start / min 15 / min 20 / end | 205.1 / 224.7 / 229.4 / 252.8 MB | 202.6 / 243.9 / — / 333.9 MB |
| Growth from min 15 (≤ 50 MB) | **+28.2 MB** | +90.0 MB (invalid: harness lists ≈ 85 MB, see below) |
| Refresh tick p50 / p95 / p99 / max | 34 / 38 / 56 / 378 ms (1 ms bins) | 33 / 52 / 109 / 828 ms |
| Longest GUI GC pause | 48.4 ms (once, start-up) | 476 ms (3 pauses 320–476 ms, all at 100 % host CPU with 40–45 python processes) |
| LINK LOST | none | none |

- **Run E is invalid for memory.** In E the PR-1 paint / event-loop probes were still installed. Their Python timestamp lists grew by ≈ 1.4 M ints + 0.18 M tuples ≈ 85 MB. From E2 on, a soak-only run installs no probes, and the tick intervals are folded into a fixed 1-ms histogram.
- **E2 growth.** E2 still grows by ≈ 0.65 MB/min after the ring buffer is full (229 → 253 MB from min 20 to min 60; the harness accounts for ≤ 3.5 MB of it). See OBS-P3-04.
- **MC3-6.** No LINK LOST in any run:
  - total ≈ 5 h of real-clock operation with the GUI, recording and a moving axis;
  - this includes the E window with the host at 100 % CPU and up to 46 other Python processes;
  - the worst GUI / backend stall was 0.83 s (tick gap), with 0.32–0.48 s GC pauses.
  - The earlier false LINK LOSTs (M2–M4 reports) came from full test-suite runs with an in-process or twin source. They are not reproduced with the out-of-process simulator.
  - The REF-PC soak on the real board / VCP still decides MC3-6 (the receive age and tick gap diagnostics are logged automatically).

---

## 4. Margins against the budgets (low-load values)

| Budget | Measured | Margin | Headroom factor |
|---|---|---|---|
| NFR-001 p95 ≤ 50 ms (all channels) | 38.4 ms | +23 % | ≈ 1.3 × |
| NFR-001 p95 ≤ 50 ms (4 panes) | 36.6–38.8 ms | +23 % | ≈ 1.3 × |
| NFR-001 event loop p99 ≤ 100 ms | 19.5 ms | +80 % | 5 × |
| NFR-002 p95 ≤ 50 ms | 18.7 ms | +63 % | 2.7 × |
| NFR-003 p95 ≤ 50 ms | 4.9 ms | +90 % | 10 × |
| SAF-SW-001 p95 ≤ 50 ms | 3.5 ms | +93 % | 14 × |
| NFR-004 losses = 0 | 0 | — | — |
| NFR-004 growth ≤ 50 MB / 1 h | 28.2 MB | +44 % | — (slope continues, OBS-P3-04) |

The paint interval is bounded below by the 33 ms timer. The 23 % margin therefore means that the frame is ready in time and the timer's p95 is close to its period. It does **not** mean 23 % spare CPU. The spare GUI-thread time is ≈ 1 − 0.8 core ≈ 20 %.

---

## 5. Defects

| ID | Sev | Owner | Location | Evidence | Fix hint |
|---|---|---|---|---|---|
| **SWD-P3-01** | S3 | B | `03_SW/src/bend_stand/core/recorder.py:322-349` (`_enqueue`: the `_closing` / `state` checks run outside `self._cv`, the append inside), `:199-234` (`start()` clears the queue before `state = "RECORDING"`), `:242-283` (`stop()`) | Soak E2 recording `…_175134`: the D rows start with frame_seq 7621, 7622 with `t_host` **2.0 s before** the record start. Then 7783 follows (the true first frame). The previous recording `…_175128` ends at 7620. Frames 7623…7782 arrived between stop and start and are correctly not recorded. So 2 rows received at the `record_stop` instant were written into the *next* recording, out of time order with its start. 1 of 2 soaks (E: clean boundary). `rows_lost` 0, `meta.integrity.complete` true → silent. Violates "every frame exactly once, in its recording" (SW-ACQ-002 / SWR-05). | Evaluate `_closing` and `state` inside the `with self._cv` block of `_enqueue`, and set them in `start()` / `stop()` under the same lock. In `start()`, also drop rows with `t_host_ns` < the start instant. Validator test: stop → start with a pipeline row injected between the checks and the append (lock-step), and a soak re-check (`wire_vs_csv.rows_before_record_start` = 0). |

## 6. Observations and risks

| ID | Addressee | Observation | Suggestion |
|---|---|---|---|
| OBS-P3-01 | B (simulator) | The out-of-process simulator delivers DATA in bursts every ≈ 15.6 ms: inter-arrival 0.2 / 15.4 ms instead of 12.4 ms. The cause is `SimBoard._run`'s `Event.wait(0.001)`: it waits 15.5 ms at the default Windows timer resolution (measured), because `io/sim/server.py` never calls `core.timing.init()`. The in-process simulator is unaffected (the app process calls it). The NFR measurements here are unaffected (the sniffer sits upstream). The simulator's reaction to commands is up to 15.6 ms late. | Call `bend_stand.core.timing.init()` / `shutdown()` in `sim/server.main()` (and in `SimServer.start/stop`). |
| OBS-P3-02 | D, PO | **NFR-002 depends on the GUI thread being free.** Handler → wire is ≤ 1.1 ms, but the posted press waits for the GUI thread. With the all-channel layout (12 `PlotWidget`s by the D-38 / D-63 pane grid) the app uses ≈ 0.8 core. On an idle host the press waits ≤ 22 ms; with competing CPU load it waits 150–490 ms. Background: SW_design_GUI §4.6 budgeted "1 view per window, ≤ 2 PlotItems". The adopted pane grid scales paint cost with panes (Thrust_Stand SWD-PM3-05 cause 1). | REF run on an otherwise idle PC (procedure §8). If the REF PC is slower: (a) a lighter default for "show all channels" (e.g. bits in one lane pane), (b) skip the paint of panes whose data did not change, (c) consider OpenGL. The Pause/Break key (≈ 5 ms, own thread) is the robust stop path; the user manual could say so. |
| OBS-P3-03 | D, PO | TC-NFR-001-03 (600 s, 4 plot windows, all channels) reaches only 6–10 fps on this PC at any load. The GUI process is saturated (plots stage 73 ms p95); while it is, the on-screen STOP waits up to one tick (≈ 100–300 ms). P-04 is informative in the plan. | The PO decides whether this layout must reach 20 fps. Otherwise document it as a limit (e.g. warn when > N curves at > 120 s). |
| OBS-P3-04 | B, D | Working set +0.65 MB/min after the ring buffer is full (E2: 229 → 253 MB, min 20 → 60), with recording, plots and a moving axis. NFR-004 (1 h ≤ 50 MB) passes, but a multi-hour session would grow ≈ 40 MB/h. | Locate it with a `tracemalloc` snapshot diff at min 20 / 60. Candidates: GUI GC freeze-while-deferred under continuous motion, event-log widget, recorder event rows, backend event history. A soak with motion off / recording off splits it. |
| OBS-P3-05 | D | Under 100 % host CPU, GUI GC full collections took 320–476 ms (design: thaw 29–58 ms). Through the GIL they stall the backend too: 2 command timeouts, 27 retries, 29 late responses; no loss, no LINK LOST. Most likely host pre-emption during the collection. | Watch `gc_policy` warnings in the REF soak `app.log`; no action on the DEV evidence. |
| OBS-P3-06 | PO | With the **AnyDesk** window in the foreground, Pause presses did not produce HALT (6 / 6, KL-01 class: elevated / UIPI-protected window). The lab PC will probably be reached via AnyDesk. | DM-06 on the REF PC: (1) Pause with AnyDesk focused locally (expected: not delivered, KL-01); (2) Pause typed by a remote AnyDesk operator (verify!). Keep the AnyDesk window unfocused during operation. |
| OBS-P3-07 | D, Integrator | Test-tool note: a DPI-unaware process that posts mouse messages gets its client coordinates scaled by Windows (here × 1.25). Its clicks miss small buttons. `perf_stim.py` sets per-monitor DPI awareness v2. | Use the same in any future GUI automation. |

### 6.1 Risks for the reference PC
1. **CPU single-thread speed.**
   - On this i7-10700 (Comet Lake, turbo 4.8 GHz) the all-channel layout leaves ≈ 20 % GUI-thread headroom. NFR-001 holds with a 23 % margin and NFR-002 with 63 %.
   - A REF PC with ≤ 75 % of this single-thread speed (e.g. an older office i5 / mobile CPU, or a laptop on battery or the "power saver" plan) is likely to fail NFR-001 with all channels, and NFR-002 in the same layout.
2. **Concurrent load.**
   - Latencies explode above ≈ 60–80 % host CPU.
   - On the lab PC the likely sources are a virus scan, Windows Update, a browser with video, an AnyDesk session (≈ 0.8 core seen here while connected) or the report export.
3. **Display.** A maximised window on a larger or high-DPI screen paints more pixels per pane than the 1920 × 1040 px used here. Software raster (`useOpenGL=False`) puts that on the CPU.
4. **Remote access.** RDP is not a valid session for PR-1…PR-3. AnyDesk changes the focus (OBS-P3-06) and costs CPU.
5. **Real board.** The VCP / USB path replaces loopback TCP. PR-4 there (0 sequence gaps, 0 CRC, TC-NFR-004-03) and MC3-6 are only decidable on the REF PC with the board at the HW gate (D-06).
6. **Long sessions.** OBS-P3-04 memory slope; SWD-P3-01 at every recording stop / start.
7. **600-s / 4-window layout** (OBS-P3-03) will not reach 20 fps.

---

## 7. Evidence

```
PC spec:  Get-CimInstance Win32_Processor / ComputerSystem / VideoController / OperatingSystem / PhysicalMemory;
          query session; python -c "import sys, PySide6, pyqtgraph, numpy …"
Runs (scratchpad validator-f-perf/; driver scripts run_all.sh, run_2.sh; each child stopped by its PID):
  A_all          perf_gui.py --plots all    --pr1-s 600 --pr2 100 --pr3 100     rc 0
  B_all600       perf_gui.py --plots all600 --pr1-s 600                         rc 0
  C_four         perf_gui.py --plots four   --pr1-s 600                         rc 0
  D_pr5          perf_gui.py --plots all    --pr5 100                           rc 0
  E_soak         perf_gui.py --plots all    --soak-s 3600 --soak-ref-min 15     rc 0   (probes on → memory invalid)
  E2_soak_clean  perf_gui.py --plots all    --soak-s 3600 --soak-ref-min 15     rc 0
  A2_all         perf_gui.py --plots all    --pr1-s 600 --pr2 100 --pr3 100     rc 0
  B2_all600      perf_gui.py --plots all600 --pr1-s 600                         rc 0
Smoke / harness development: smoke1…smoke9 (short runs, not used for results)
Analysis: perf_summary.py (table), strat_pr1.py / strat_pr2.py (load stratification), wirecheck.py (t_us matching)
Processes: every run's processes.log lists sim, hostload, stim_click, stim_key started and stopped (rc 0) by PID;
           no process of this task is left running.
```

---

## 8. Procedure for the PO's reference-PC run

**Scripts** (`03_SW/tests/perf/`, Validator F; not pytest-collected):

| File | Role |
|---|---|
| `run_ref_pc.ps1` | One-click procedure: PC description → runs A (PR-1 -01 + PR-2 + PR-3), B (PR-1 -03), C (PR-1 -04), D (PR-5), E (PR-4 1 h soak) → `summary.md`. `-Quick` = 60 s / 20 presses / 5 min dry run of the whole procedure (≈ 20 min). |
| `perf_gui.py` | GUI harness (set-up, phases, result JSON) |
| `perf_sim.py` | out-of-process simulator + wire sniffer |
| `perf_stim.py` | external stimulus (posted STOP clicks, SendInput Pause) |
| `perf_hostload.py` | host-load sampler (separate process) |
| `perf_summary.py` | budget table from the run folders |

**Before the run**
1. Install per DM-02 (repo + `.venv` from `requirements.txt`); `python -m bend_stand --version` works.
2. Sit at the PC (interactive console session). Do not use RDP; do not keep an AnyDesk window focused. Ideally no AnyDesk session is connected during the run.
3. Close other applications; no Windows Update or virus scan running; power plan "Balanced" or "High performance"; laptop on mains.
4. No other application may own the Pause key (the summary shows `hotkey_mode`; REGISTERED is expected).
5. Main window on the primary screen. Do not move or click it while the runs go on. Other windows may stay in front; the harness does not need the focus.

**Run** (PowerShell in the repo root, ≈ 2 h 10 min):
```
powershell -ExecutionPolicy Bypass -File 03_SW\tests\perf\run_ref_pc.ps1 -Out D:\bbs_perf_ref
```
Optional first: `… -Quick -Out D:\bbs_perf_dry` (procedure check, ≈ 20 min).

**Send back** the whole output folder: `pc_spec.txt`, `summary.md` and per run `results.json`, `hostload.jsonl`, `sniff.jsonl`, `app.log`, `processes.log`, `raw_pr1.json`, the recording folder. Validator F evaluates against the budgets: NFR-001 p95 ≤ 50 ms and event loop p99 ≤ 100 ms; NFR-002 / NFR-003 / SAF-SW-001 p95 ≤ 50 ms; NFR-004 0 losses and growth ≤ 50 MB. F also writes the REF report (MC4-2), the LINK LOST evaluation (MC3-6) and DM-11 together with the PO's visual judgement.

**Not covered by the scripts** (PO + F at the REF PC / HW gate):
- DM-06 physical Pause key with AnyDesk / elevated window (OBS-P3-06);
- TC-NFR-003-02 key → last PUL edge on target;
- TC-NFR-004-03 VCP soak on the board.

---

## 9. Open items

| ID | Item | Addressee |
|---|---|---|
| SWD-P3-01 | Recorder stop/start boundary (S3) | B (fix), F (re-verify with a soak / lock-step test) |
| OBS-P3-01 | `timing.init()` in the simulator server | B |
| OBS-P3-02 / -03 | GUI-thread budget with many panes; the 600-s / 4-window layout | D; PO decision on P-04 |
| OBS-P3-04 | Memory slope 0.65 MB/min | B / D |
| OBS-P3-06 | AnyDesk and the Pause key (DM-06) | PO |
| MC4-2 / MC3-6 | REF-PC runs with this procedure; REF PC spec still to be supplied (D-33 j) | PO, Orchestrator, F |

## 10. Change history
| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-10-09 | Validator F | First DEV-PC perf report (informative): PR-1…PR-5 + 1 h soak, load-stratified results, SWD-P3-01, OBS-P3-01…07, REF-PC procedure and scripts. |
