# Bird Bend Stand — agent instructions

Bend (pull/push) test stand: NUCLEO-F446RE firmware (stepper via HBS86H driver, HX711 load cell AFE)
+ Python/PySide6 PC application. Specification-driven: read `00_System/PROCESS.md` first (roles, phases, gates).

## Sources of truth (in priority order)
1. `00_System/specs/SRS.md` — requirements with IDs (SYS-, FW-, SW-, SAF-, IF-).
2. `00_System/specs/ICD_protocol.md` — PC⇄FW binary protocol. Changes only by the Integrator role, with version bump + change-history entry.
3. `00_System/specs/params.yaml` — configuration parameter dictionary (generated code is never hand-edited).
4. `00_System/research/R*.md` — research guidelines.
5. `00_System/STATUS.md` — current phase, gates, open decisions. `00_System/specs/DECISIONS.md` — binding decisions.
6. `Initial_specs.txt` — the product owner's original request (input, not a spec).

## Layout
- `00_System/` process, research, specs, shared tools · `01_HW/` pinout & wiring · `02_FW/` firmware · `03_SW/` Python app.

## External references (READ-ONLY — never write there)
- `E:\Bavovna\Drone\Stefan` — previous stepper stand (F446RE, CubeMX HAL): pinout, board init, stepper GUI.
- `E:\Bavovna\Drone\Thrust_Stand_HAW` — thrust stand: HX711 driver, CRC16/framing, param generator, GUI patterns. **No changes allowed in that folder.** Copy what is reused into this repo and record the origin.

## Conventions
- Tag code/tests with requirement IDs: `# Implements: SW-CAL-003`, `/* Verifies: FW-AFE-002 */`.
- Protocol: little-endian, CRC-16 on every frame in both directions (variant fixed in the ICD).
- FW: no dynamic allocation after init; no blocking > 1 ms in the main loop; motor disabled by default; E-stop and limit switches handled in FW independently of the PC.
- SW: backend (`core`, `io`, `calc`) must not import Qt widgets; calculations are pure functions with pytest vectors.
- Hardware: never open a COM port or flash a board unless the Orchestrator states the product owner allowed it (see DECISIONS).
- Toolchain on this PC: Python 3.14 (`python`), STM32CubeCLT 1.20 arm-none-eabi-gcc (`G:\_SOFT\STM32CubeCLT_1.20.0`); project venv `.venv` (PlatformIO via pip).
