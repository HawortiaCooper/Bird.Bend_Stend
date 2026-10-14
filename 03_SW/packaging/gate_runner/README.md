# BirdBendStand-GateRunner — frozen build (D-57, GR-1)

Owner of the packaging: Implementer B · runner code: Validator E (`00_System/tools/hil`, design
`00_System/tools/hil/GATE_RUNNER.md`) · approval files: Orchestrator (`make_approval.py`).

The hardware-gate runner (`00_System/tools/hil/gate_runner.py` → `main()`) frozen with PyInstaller 6.22.3 into a
**console, one-folder** program that needs no repository and no Python. The output goes into the gate kit:
`04_Delivery/GateKit_FW<rel>/BirdBendStand-GateRunner/` (+ `BirdBendStand-GateRunner-<ver>.zip` next to it). Both
are git-ignored (root `.gitignore`); what gets tracked is the Orchestrator's decision.

| File | Purpose |
|---|---|
| `build_gate_runner.ps1` / `.bat` | the build (steps below) |
| `BirdBendStand-GateRunner.spec` | PyInstaller spec: the 15 runner modules, hidden imports `serial` + `yaml`, bundled data |
| `gr_build.py` | version stamp, FW-twin staging, PE timestamp normalisation, runner-hash check, BUILD_INFO, zip |
| `smoke_gate_runner.py` | smoke test G1–G7 (no hardware) |
| `rthook_gate_d06.py` | D-06 test guard (`BEND_STAND_D06_GUARD=1` → any serial open fails; no effect otherwise) |
| `../../requirements-gaterunner.txt` / `.lock.txt` | build pins = application build set + PyYAML 6.0.3, hash-pinned (14 packages) |

## Build

```bat
rem once: the hash-pinned build set
.venv\Scripts\python -m pip install --require-hashes -r 03_SW\requirements-gaterunner.lock.txt
rem build into the (only) 04_Delivery\GateKit_FW* folder; private work folder
03_SW\packaging\gate_runner\build_gate_runner.bat -BuildDir <private folder>
rem options: -Kit <gate kit folder>  -InstallDeps  -SkipSmoke  -FullTwin (G3 without --quick)  -NoZip
```

1. Python 3.14 and every installed package = `requirements-gaterunner.lock.txt` (else stop, `-InstallDeps`).
2. Version `gate_res.RUNNER_VERSION` + git hash, e.g. `1.0.0+gce22533`; `.dirty` when the runner, tools, twin, FW
   sources, `params.yaml`, the bench pictures or this folder differ from HEAD — a dirty runner is for rehearsal only.
3. FW twin of **this checkout**: `00_System/tools/fw_twin/build.py --core fw --build-dir <private>/twin` (CLion
   MinGW GCC; imports only KERNEL32 / msvcrt), copied with its PE timestamp set to the HEAD commit time.
4. PyInstaller (`SOURCE_DATE_EPOCH` = commit time, `PYTHONHASHSEED=0`), bundle data in `_internal/`:
   `resources/params.yaml` ← `00_System/specs/params.yaml`, `resources/pictures.json` ← `hil/resources/pictures.json`,
   `resources/pictures/B*.png` ← `01_HW/diagrams/bench/B*.png`, `fw_twin/fw_twin.exe` ← step 3.
5. Runner exe PE timestamp normalised; the runner hash computed with the repository's `gate_res.bundle_sha256`
   (exactly what `make_approval.py --runner-exe` computes) must equal the exe's own `--version`; `BUILD_INFO.txt`
   lists the hash and every bundled data component.
6. Smoke test (below). 7. Zip.

**Runner hash** (`gate_res.bundle_sha256`, as of 2026-10-14 by Validator E: C-GR-3): the executable plus **every file
below `_internal`** (Python runtime, `base_library.zip`, extension modules, `resources/`, `fw_twin/`).
**Reproducible:** two builds of the same tree from fresh private folders gave the identical runner hash
(`93db5bd5…` twice, 2026-10-14) — the only non-deterministic bytes were the PE timestamps / checksums (normalised),
the version-resource build time (the resource names the commit date instead) and the member order of
`base_library.zip` (fixed with `PYTHONHASHSEED=0`).

## Smoke test (`smoke_gate_runner.py`, run by the build)

Every run: cwd and TEMP / TMP / APPDATA / LOCALAPPDATA / USERPROFILE / HOME redirected into a sandbox,
`BEND_STAND_D06_GUARD=1`, PYTHON* removed.

| Check | Expectation |
|---|---|
| G1 `--version` | rc 0, "(frozen)", SHA-256 = `gate_res.bundle_sha256` of the repository (= make_approval) |
| G2 `--list` | rc 0, the ordered plan |
| G3 `--twin --quick --out <out>` | rc 0, one `gate_*_twin_*.zip` |
| G4 `--verify-zip <zip>` | rc 0 "results zip intact"; a copy with one member changed → rc 1 |
| G5 refusals | `--port COM250` without approval / with a forged approval / with `--approved REF` → rc 2 "REFUSED", `--out` not created, the D-06 guard never reached |
| G6 stray-run rule | no file in the sandbox outside the `--out` folders |
| G7 layout | params.yaml / pictures.json / pictures identical to the repository, twin present, no GUI / app packages |

Result 2026-10-14 (build `1.0.0+gce22533.dirty`, `04_Delivery/GateKit_FW0.8.6/BirdBendStand-GateRunner`):
**7/7 PASS**; folder 24.4 MiB / 71 files; zip 13.0 MiB; twin rehearsal 70 s, results zip 1.0 MB.
