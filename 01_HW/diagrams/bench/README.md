# 01_HW/diagrams/bench — HW-gate bench pictures (D-57)

Owner: Implementer A (01_HW). Version 1.1, 2026-10-14 (1.1: B6–B9, E's GR-3 additions; 1.0: B1–B5). Pictures for the prompts of the frozen HIL gate runner (Validator
E leads the runner, D-57); same primitives, styles and title-block style as the wiring sheets (`../gen_wiring_svg.py`,
whose MH table and pin cross-check against `pinout.md` they reuse). Sources: `wiring.md` v0.7.1 §6.1 / §11.1,
`FW_test_plan.md` §6 / §6.8, `HW_GATE_RUNBOOK.md` v0.4, `pinout.md` v0.6. Items not fixed by those documents (connector pin positions, E-stop terminal numbers, Nucleo board layout,
specimen / load-cell side, caliper reference faces, clone internals) carry **"ASSUMED – verify at HW gate"**. The MH header pin order and labels were confirmed by Validator E (GR-3): label the built header with exactly the B1 names. Not a certified safety design.

| Picture | SVG | PNG | Content |
|---|---|---|---|
| B1 | [B1_measurement_header.svg](B1_measurement_header.svg) | [PNG](B1_measurement_header.png) | measurement header MH (2 × 8), each jumper with tap node, series resistor (1 kΩ; J-STIM 220 Ω) and MCU pin, fitted / removed, HG-01 continuity values, J-EVT / J-AUX / J-STIM source per HG step |
| B2 | [B2_multimeter_points.svg](B2_multimeter_points.svg) | [PNG](B2_multimeter_points.png) | multimeter points M-1…M-9 on the parts (R_E, ENA− shunt, Arduino pins A2 / D2, ENA+ terminal, DC-DC, J-STIM node), probe positions, conditions, expected values |
| B3 | [B3_jstim_stimulus.svg](B3_jstim_stimulus.svg) | [PNG](B3_jstim_stimulus.png) | J-STIM stimulus wiring for the §6.8 bench trials (sense plug unplugged, NO channel stays wired, tag, PSU switch) and steps P-1…P-8; HG-11 variant |
| B4 | [B4_hg06_shunt.svg](B4_hg06_shunt.svg) | [PNG](B4_hg06_shunt.png) | HG-06 / C-06 opto-current measurement: 100 Ω shunt in PUL− (DIR−, ENA−), VOH, STATIC_LEVEL procedure, pass values |
| B5 | [B5_flash_gate_images.svg](B5_flash_gate_images.svg) | [PNG](B5_flash_gate_images.png) | image sequence of the gate session (MEAS → DWT → MEAS → release) with file, folder, build string, SHA-256; flashing with method B2 (.hex, no full-chip erase); what never to do |
| B6 | [B6_hg10cd_hg17_reset_estop.svg](B6_hg10cd_hg17_reset_estop.svg) | [PNG](B6_hg10cd_hg17_reset_estop.png) | HG-10 c/d and HG-17: observer holds B2 RESET and presses the E-stop, operator turns the coupling, hands-off rule; where to lift and refit the D-42 NO wire (terminal 14, ASSUMED) |
| B7 | [B7_nucleo_reset_power.svg](B7_nucleo_reset_power.svg) | [PNG](B7_nucleo_reset_power.png) | Nucleo B2 RESET (NRST), USB / E5V power cycle, IWDG reset (HG-14 / HG-15) |
| B8 | [B8_spring_dial.svg](B8_spring_dial.svg) | [PNG](B8_spring_dial.png) | spring specimen, preload direction (+x = pull = tension), dial-indicator placement and zero (HG-12 / 15 / 25 / 26) |
| B9 | [B9_caliper_reference.svg](B9_caliper_reference.svg) | [PNG](B9_caliper_reference.png) | caliper reference faces and how to read D1 − D0 (HG-09 / HG-25 / HG-28) |

Regenerate (never edit the SVG / PNG by hand):

```bat
.venv\Scripts\python 01_HW\diagrams\bench\gen_bench_svg.py --png --strict
```

Fonts are sized for an A5 print (≥ 2 mm on the A4 drawing). When the gate kit is rebuilt, update `REL` / `IMAGES`
(file names, build strings, SHA-256) in the script.
