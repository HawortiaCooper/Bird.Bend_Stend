# Bird Bend Stand — Development Process

Specification-driven development: **nothing is implemented that is not traced to a
requirement, and no requirement is "done" until a validator has checked it against its
acceptance criterion.** Process model inherited from Thrust_Stand_HAW (proven there through M3).

## 1. Roles

| Role | Scope | Owns (writes) | Reads / consumes |
|---|---|---|---|
| **Orchestrator** | Plans phases, dispatches agents, resolves conflicts, single point of contact with the product owner, commits at gates | `CLAUDE.md`, `00_System/PROCESS.md`, `00_System/STATUS.md`, `00_System/specs/SRS.md`, `00_System/specs/DECISIONS.md`, `00_System/specs/TRACEABILITY.md`, `.claude/agents/**` | everything |
| **Researcher** | Hardware (HBS86H, 86HS2140, HX711, Keli DEF, F446RE), reference analysis (Stefan, Thrust_Stand_HAW incl. GUI solutions), methods; guidelines for implementers | `00_System/research/R*.md`, `00_System/research/ref_snapshots/**` | — |
| **Implementer A — FW** | F446RE firmware: drivers (step/dir/en, HX711, UART DMA, switches, NVM), motion control, protocol endpoint, build project | `02_FW/**` (except validator suites/reports), `01_HW/**` | SRS, ICD, params, R1–R4 |
| **Implementer B — SW backend** | PC side: serial transport, codec, device API, recorder, calculations, calibration/tare math, sequencer engine, safety supervisor, simulator | `03_SW/src/bend_stand/core/**`, `…/io/**`, `…/calc/**`, package `__init__`/`__main__`, `pyproject.toml`, `03_SW/tests/unit/**`, `tests/conftest.py`, `03_SW/docs/SW_design.md` | SRS, ICD, R4 |
| **Implementer C — Integrator** | Owns the FW⇄SW interface: ICD, param dictionary, code generation, shared test vectors, reference codec, FW host twin, integration/HIL tests | `00_System/specs/ICD_*.md`, `00_System/specs/params.yaml`, `00_System/tools/**`, `03_SW/tests/integration/**` | both sides of the link |
| **Implementer D — GUI** | PySide6 GUI: tabs, detachable realtime plots, manual control, calibration/tare wizards, sequence editor & chart | `03_SW/src/bend_stand/gui/**`, `03_SW/tests/gui/**`, `03_SW/docs/SW_design_GUI.md` | SRS, SW design, backend API |
| **Validator E — FW** | FW reviews, host unit tests of pure functions, protocol conformance, on-target procedures, gate verdicts | `02_FW/test/**`, `02_FW/docs/FW_test_*.md`, `00_System/tools/hil/**` | SRS, ICD, FW code |
| **Validator F — SW** | SW reviews, pytest suites, GUI procedures, calculation vectors, gate verdicts | `03_SW/tests/**` (except unit/gui/integration owned above — validator suites in `tests/validation/**`), `03_SW/docs/SW_test_*.md` | SRS, R4, SW code |

Rules: an implementer never validates their own requirement. Interface changes go **only**
through Implementer C (ICD change → version bump → both sides regenerate). Agents write only
in the paths they own; anything else is reported as a finding to the owner.

## 2. Phases and gates

| Phase | Output | Gate (who approves) |
|---|---|---|
| P0 Research | `research/R1..R4` | Orchestrator review |
| P1 Specification | SRS, ICD, params.yaml, HW pinout, FW design, SW design, test plans | **Product owner review** |
| P2 Implementation (iterative, per milestone) | code + unit tests | Validator verdict per milestone, Orchestrator gate |
| P3 Integration & validation | HIL / on-target tests, test reports, traceability matrix | **Product owner acceptance** |

### Milestones (proposed, refined in P1)
- **M1 — Link & skeleton**: FW board init, UART DMA link, framing + CRC16, GET_CONFIG / SET_PARAM / NVM, streaming with placeholder data; SW backend + simulator; minimal GUI connect/config/plot.
- **M2 — Sensor & motion**: HX711 driver (80 Hz, gain 128), step/dir/en motion with ramps, limit switches, homing/zeroing, NC E-stop, validity flag.
- **M3 — SW application**: manual control, detachable realtime plots, recording/momentary sample, safety limits, tare, calibration wizards, report marks.
- **M4 — Sequencer**: step/ramp editor + wizards, looping, save/recall, travel-vs-load chart with live marker, steady-state extraction to report.

## 3. Artifacts and traceability
- Requirement IDs: `SYS-`, `FW-`, `SW-`, `IF-`, `SAF-`. Code/tests reference them: `# Implements: SW-CAL-003`, `/* Verifies: FW-AFE-002 */`.
- `00_System/specs/TRACEABILITY.md` maps requirement → design → code → test → status.
- `params.yaml` is the single source of truth for configuration parameters; the C header and Python module are generated.
- The ICD is versioned (`PROTO_VERSION`, `PAYLOAD_VERSION`); every change is logged in its change history.

## 4. Directory layout
```
Bird.Bend_Stend/
  00_System/   process, status, research, specs, shared tools (generators, vectors, ref codec, twin)
  01_HW/       pinout, wiring, connector drawings, BOM
  02_FW/       NUCLEO-F446RE firmware project (+ docs, tests)
  03_SW/       Python PC application: src/bend_stand/{core,io,calc,gui}, tests, docs
```

## 5. Conventions
- Language of docs & code: English. Units in the protocol: SI-derived (mm, µm, steps, ms); display units selectable.
- Multi-byte protocol values are little-endian.
- SW: Python ≥ 3.11 (3.14 on this PC), PySide6, pyqtgraph, numpy, pyserial, pytest; type hints; no GUI logic in the backend.
- Shared session scratchpad: each agent works only in its own subfolder named after its role.
- Agents never commit and never contact the product owner; the Orchestrator does both.
