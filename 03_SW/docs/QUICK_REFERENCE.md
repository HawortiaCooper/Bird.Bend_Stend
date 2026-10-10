# Bird Bend Stand — Quick reference card
SW 0.1.0 · details in [`USER_MANUAL.md`](USER_MANUAL.md) · **Emergency: red E-stop.**

## Stops and how to clear them
| Stop | Effect | Clear |
|---|---|---|
| **Red E-stop** (panel) | pulses stop, driver **de-energised** (MCU + hardwired ENA cut), load may spring back, **NOT homed**. Not an IEC 60204-1 E-stop (no power removal). | release button → **Clear stop…** → tick **E-stop button released, area safe** → **Clear E-STOP** → **Driver enabled** → **HOME** |
| **STOP** (screen: toolbar, tabs, every window and dialog) | immediate stop, driver **holding** | nothing latched |
| **Pause/Break** / **Ctrl+Break** key (system-wide, not into Administrator windows) | **HALT**: immediate stop, holding, latched | **Clear stop…** → **Clear HALT** |
| **‖ Pause** (toolbar / Sequence) or **PAUSE** button | controlled stop, holding, **PAUSED** blocks new motion; sequence paused | **▶ Resume** / **Resume** / PAUSE button again (sequence continues the interrupted step) — or **Clear PAUSE** |
| **Stop (controlled)** / **Abort (HALT)** (Sequence) | sequence ends STOPPED / ABORTED (HALT latched) | – / **Clear HALT** |
| Limit switch (**LIM S** / **LIM E**) | immediate stop | move **away**; clears on release |
| Board load limit `LOAD_LIMIT`, faults (**FAULT**, `LIMIT_WIRING`, `AFE_FAULT`, `HOME_*`) | immediate stop | remove the cause → **Clear faults** |
| PC limit (`SW limit trip`) | STOP, sequence terminated | move back / unload |
| Link lost (**WDG**, `LINK LOST`) | controlled stop after 1 s | reconnect; check HOMED / TARE / THR |

No clear ever restarts motion. `STOP NOT SENT` / `NOT CONFIRMED` → **press the red E-stop**.
Enter / Space never confirm anything; confirmations need a mouse click.

## Start-up checklist
1. Operator panel: E-stop released, PAUSE button tested (BTN chip `PAUSE pressed`).
2. 48 V driver supply on, Nucleo USB plugged in → start **Bird Bend Stand** → **Connect** (`STLink Virtual COM Port`).
3. `Version check: ✓ compatible`; LINK green, AFE ≈ 80 Hz; no notices (else **Save to NVM**).
4. KEY chip `REGISTERED` (Pause/Break active); test with **Tools ▸ Test Pause/Break key…** at the start of the day.
5. **Driver enabled** → **HOME** (path to START free) → HOMED green `homed`.
6. **CAL** valid (`PASS`, amber `LOW_SPAN` accepted), **TCAL** not shown, **THR** `VERIFIED`, no **NOSPEC** chip.
7. Periodically (start of a campaign): hardwired ENA cut — Nucleo held in RESET, press E-stop → shaft free by hand.

## Pre-test checklist (every specimen)
1. Fixture mounted, specimen **not** loaded, axis still ≥ 1 s → **TARE** (TARE chip green, THR `VERIFIED`).
2. **Safety limits**: Pull / Push max a little above the expected force, travel **Min** / **Max** = the stroke you
   accept (also the bound of load steps) → **Apply**. No no-specimen mode.
3. **Test marks**: **Measured element**, **Specimen number**, **Operator**; 3-point bend geometry if used.
4. Mount the specimen; **Set test zero here** at contact (Manual tab).
5. **● Record** (manual tests) — a sequence records by itself. **Take sample** for single readings.
6. Sequence: `● valid`, check the chart, **▶ Start**, read the confirmation; keep the E-stop within reach.
7. After the test: unload (move back), stop the recording, check the report (**Report** tab → **Open HTML**).

## Sequence results to watch
`NOT_REACHED` (load not reached at the approach bound — not a limit trip) · `BREAK_DETECTED` · `SLIP` · `TIMEOUT` ·
`DRIVER_ALARM` · `INCOMPLETE` · `NOT_ON_TARGET` — the sequence stops; read the message line.
