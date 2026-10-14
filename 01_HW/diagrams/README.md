# 01_HW/diagrams — wiring diagram pictures

Owner: Implementer A (01_HW). Version 1.1, 2026-10-14 (1.1: D-56 b — stage 2 buffer only if the direct-drive
qualification fails; 1.0: first issue). Derived from `01_HW/wiring.md` v0.7 and
`01_HW/pinout.md` v0.6; decisions D-27 (driver DIP), D-41 (no contactor), D-42 (hardwired ENA cut).
**Not a certified safety design** (wiring.md status): engineering proposal for a lab stand.

| Sheet | SVG (vector, print) | PNG (2970 × 2100 px) | Content |
|---|---|---|---|
| 1 | [sheet1_system_overview.svg](sheet1_system_overview.svg) | [PNG](sheet1_system_overview.png) | 230 VAC → SDR-480-48 → F1 → HBS86H; isolated DC-DC +5V_CUT; NUCLEO (USB / E5V); interface board stage 1 / 2; HBS86H ↔ 86HS2140 (phases, encoder); ball screw, START / END NC switches, load cell; HX711; operator panel (E-stop NC / NO, PAUSE); grounding and shields; isolation barrier |
| 2 | [sheet2_nucleo_pins.svg](sheet2_nucleo_pins.svg) | [PNG](sheet2_nucleo_pins.png) | every used NUCLEO pin with Arduino / morpho designator, signal, direction, input cell, destination terminal; stage 2 SN74ACT244 buffer; D-42 ENA cut (D1 / D2 / R_E / 10 kΩ); HX711 + load-cell colours; power pins; measurement header MH (HW gate only); solder bridges |
| 3 | [sheet3_input_cells_ena_or.svg](sheet3_input_cells_ena_or.svg) | [PNG](sheet3_input_cells_ena_or.png) | schematics of the input RC cells A–D (ALM / PEND, limits, E-stop sense, PAUSE), optional cell E (DRV_PWR, not fitted), the ENA diode OR with the multimeter points M-1…M-4, the ENA current table and M-1…M-9 |
| 4 | [sheet4_driver_dip_supply.svg](sheet4_driver_dip_supply.svg) | [PNG](sheet4_driver_dip_supply.png) | HBS86H terminal strips (Signal, OUT, RS232, Power, Motor, Encoder) and what each connects to; DIP SW1…SW8 found vs TARGET (D-27); PSU / fuse / cable; polarity and timing defaults |

All sheets are A4 landscape (297 × 210 mm, `viewBox` 1485 × 1050 = 5 units per mm); print the SVG (or the PNG,
≈ 254 dpi on A4) at 100 % / "fit to page". Each sheet has a title block (sheet n / 4, version, date, sources) and a
legend. Items marked **"ASSUMED – verify at HW gate"** are ASSUMED in wiring.md / pinout.md (connector numbers from
UM1724, clone internals, part examples, direct drive) and are checked at the hardware gate (wiring.md §11).

## Regenerate (never edit the SVG / PNG by hand)

```bat
rem SVG only (any Python >= 3.10, standard library only)
python 01_HW\diagrams\gen_wiring_svg.py
rem SVG + PNG (PNG via PySide6 QtSvg from the project venv, 2x, white background)
.venv\Scripts\python 01_HW\diagrams\gen_wiring_svg.py --png --strict
rem only the cross-check of the pin table against pinout.md
python 01_HW\diagrams\gen_wiring_svg.py --check
```

`gen_wiring_svg.py` holds the pin / net table (`PINS`, `MH`, `DIP`) and cross-checks it against the rows of
`pinout.md` §1.1 / §1.2 / §1.5 on every run (pin, Arduino name, CN / morpho numbers, END fallback PB1, J-STIM 220 Ω);
a mismatch stops the generation. It also estimates text overlaps (`--strict` fails on any). After a change of
`wiring.md` or `pinout.md`, update the script, regenerate, look at every PNG, and bump the version in the script
(`DOC_VERSION`) and in this file.
