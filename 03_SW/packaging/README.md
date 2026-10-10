# Bird Bend Stand — Windows distribution (build and install)

Owner: Implementer B · design: `03_SW/docs/SW_design.md` §24 · Implements: SW-PLT-001 (Windows 10 64-bit, installable),
SYS-008 (simulator in the shipped application), D-06 (no COM port without the operator's choice).

The distribution is a **PyInstaller one-folder build**: the Python 3.14 runtime (D-33 i), PySide6, pyqtgraph, numpy,
pyserial and the whole `bend_stand` package (incl. the generated `protocol_gen` / `params_gen` dictionaries and the
in-process FW simulator `io.sim`) are frozen into `BirdBendStand-<ver>\`. **The target PC needs no Python.**

| File | Purpose |
|---|---|
| `build_dist.ps1` / `build_dist.bat` | the build (steps below); `.bat` = wrapper with `-ExecutionPolicy Bypass` |
| `BirdBendStand.spec` | PyInstaller spec: two executables sharing one `_internal\` folder |
| `buildinfo.py` | version stamp, pin check, Windows version resource, `BUILD_INFO.txt`, deterministic zip |
| `smoke_dist.py` | smoke test of a built folder (no hardware) |
| `docs_html.py` | installs the operator docs into `docs\` of the folder and renders them to HTML (stdlib) |
| `rthook_bend_stand.py` | runtime hook: windowed log file, D-06 guard for test runs of the exe |
| `entry_birdbendstand.py` | entry script (= `python -m bend_stand`) |
| `make_icon.py`, `assets\BirdBendStand.ico` | application icon (generated, stdlib only; the `.ico` is committed) |
| `BirdBendStand.iss` | optional Inno Setup 6 installer script |
| `../requirements-build.txt` | exact build pins (runtime pins + PyInstaller 6.22.3, hooks-contrib 2026.8) |
| `../requirements*.lock.txt`, `lock_requirements.py` | hash-pinned locks (direct + transitive, SHA-256 of the exact wheel) — §1.1 |

## 1. Build (developer PC)

Prerequisites: Windows 10/11 x64, Python 3.14 x64, git, the project venv `.venv` (see `03_SW/requirements*.txt`).

```bat
rem once (or after a pin change): install the hash-pinned build set into the project venv (SWR-27)
.venv\Scripts\python -m pip install --require-hashes -r 03_SW\requirements-build.lock.txt
rem build
03_SW\packaging\build_dist.bat
rem options: -InstallDeps (pip install first)  -Installer (Inno Setup, if installed)  -SkipSmoke
rem          -NativeGuiSmoke (smoke GUI on the real display)  -NoZip  -AllowEnvMismatch (not reproducible)
```

Steps of `build_dist.ps1` (Windows PowerShell 5.1):

1. checks the interpreter (3.14) and that every installed package — direct and transitive — equals its entry in
   `requirements-build.lock.txt` (`buildinfo.py check-env`; a mismatch stops the build unless `-AllowEnvMismatch`;
   `-InstallDeps` installs the lock with `--require-hashes` first);
2. stamps the version: `[project].version` of `pyproject.toml` + git short hash, e.g. **`0.1.0+ge600169`**;
   `.dirty` is appended when `03_SW/src`, `03_SW/packaging`, `pyproject.toml` or the requirements files differ from
   HEAD (commit first for a release build). Written to `03_SW\build\build_info.json`;
3. PyInstaller → `03_SW\dist\BirdBendStand-<base>-g<hash>[-dirty]\` (work files in `03_SW\build\pyinstaller`);
4. operator docs → `docs\` (`USER_MANUAL`, `QUICK_REFERENCE` as `.md` + rendered `.html`, `img\*` screenshots;
   `docs_html.py` — Windows 10 has no default program for `.md`, so shortcuts open the `.html`) and `BUILD_INFO.txt`
   (version, git, build time, PyInstaller version, output of the frozen `--version`);
5. smoke test (`smoke_dist.py`, below) — a failure stops the build before the zip;
6. `03_SW\dist\BirdBendStand-<ver>.zip` (deterministic: sorted entries, timestamps = HEAD commit time);
7. with `-Installer` and Inno Setup 6 installed: `03_SW\dist\BirdBendStand-<ver>-setup.exe`. **Inno Setup is not
   installed on the dev PC** (not installed by this task, no system software changes) — the zip is the deliverable;
   the `.iss` is ready for a PC that has Inno Setup 6.

`03_SW/dist/` and `03_SW/build/` are git-ignored (root `.gitignore`).

How the version reaches the app: the spec writes a minimal `bend_stand-<version>.dist-info` into the bundle, so
`bend_stand.__version__` (via `importlib.metadata`) reports `0.1.0+g<hash>` in the frozen app — `--version`, the About
dialog and the `sw_version` field of recordings, calibration records, board-config files and reports identify the
build (from the source tree it stays `0.1.0+src`). Both executables carry a Windows version resource (Explorer →
Properties → Details: file version 0.1.0.0, product version `0.1.0+g<hash>`, comment = git hash and build time).

### 1.1 Reproducible installation from the hash-pinned locks (SWR-27)

`requirements*.txt` pin the direct dependencies; `requirements.lock.txt` (runtime), `requirements-dev.lock.txt`
(development / tests) and `requirements-build.lock.txt` (distribution build) pin **every** package — transitive ones
such as `shiboken6`, `colorama`, `pluggy`, `packaging` included — with the SHA-256 of the exact wheel for the supported
platform (Windows 10 x64, CPython 3.14, D-33 i). pip refuses any other file:

```bat
py -3.14 -m venv .venv
rem run the app from source
.venv\Scripts\python -m pip install --require-hashes -r 03_SW\requirements.lock.txt
rem development and tests (includes the runtime set)
.venv\Scripts\python -m pip install --require-hashes -r 03_SW\requirements-dev.lock.txt
rem distribution build (includes the runtime set)
.venv\Scripts\python -m pip install --require-hashes -r 03_SW\requirements-build.lock.txt
```

After changing a pin in a `requirements*.txt`: `.venv\Scripts\python 03_SW\packaging\lock_requirements.py generate`
(pip's own resolution, `pip install --dry-run --report`; equivalent of `pip-compile --generate-hashes`, no extra
tool), then commit the `.txt` and `.lock.txt` together. `lock_requirements.py check` (offline, also a unit test)
verifies that every direct pin is locked at its version and every entry has a hash. Verified 2026-10-09:
`pip download --require-hashes --no-deps -r <lock>` for all three locks (6 / 17 / 13 packages) — all hashes match.

### Contents of the folder

| | |
|---|---|
| `BirdBendStand.exe` | the application (GUI subsystem, no console window) |
| `BirdBendStand-cli.exe` | same program with a console: `--headless`, `--version`, `--log-level DEBUG` diagnostics |
| `_internal\` | Python 3.14 runtime, Qt 6.11 (PySide6-Essentials; Core/Gui/Widgets + Svg/OpenGL deps, `qwindows` platform plugin, image formats), numpy 2.5.3, pyqtgraph 0.14.0, pyserial 3.5, `bend_stand` |
| `docs\` | operator documentation: `USER_MANUAL.html` (manual with screenshots, source [`03_SW/docs/USER_MANUAL.md`](../docs/USER_MANUAL.md)), `QUICK_REFERENCE.html` (one-page card, source [`03_SW/docs/QUICK_REFERENCE.md`](../docs/QUICK_REFERENCE.md)), the `.md` sources and `img\` |
| `BUILD_INFO.txt` | build identification and start / data / docs / report notes |

Left out on purpose: Qt translations (UI is English only), TLS (`ssl`, no network use), numpy `f2py`, pyqtgraph
examples/OpenGL/Jupyter parts, test and build tools (pytest, PyYAML, PyInstaller) — `smoke_dist.py` S7 checks it.

### Measured (dev PC, Windows 10 Pro 19045, build of 2026-10-08, `0.1.0+ge600169.dirty`)

| Item | Value |
|---|---|
| Build time (PyInstaller step / whole script incl. smoke + zip) | ≈ 80 s / ≈ 120 s |
| Folder size | **137.5 MiB**, 247 files (134.4 MiB program + 3.1 MiB `docs\`; largest: `opengl32sw.dll` 20 MiB software-OpenGL fallback, OpenBLAS 21 MiB, Qt6 core DLLs ≈ 25 MiB) |
| Zip size | **62.2 MiB** |
| `BirdBendStand-cli.exe --version` (process start → exit) | 0.3–0.4 s (warm cache) |
| `--headless --sim`: start → connected (GET_INFO … GET_ALL_PARAMS done, stream on) | 1.1–1.4 s |
| GUI start → event loop → immediate quit (disconnected, native display) | 2.0 s warm, 3.1 s first start |
| GUI `--sim` 8 s auto-quit run, total wall time | 10.3–10.8 s (≈ 2–3 s start-up + shutdown) |

## 2. Smoke test of a built folder (no hardware)

```bat
.venv\Scripts\python 03_SW\packaging\smoke_dist.py --dist 03_SW\dist\BirdBendStand-<ver> [--native-gui] [--gui-ms 8000]
```

Each run gets a private data folder (`BEND_STAND_DATA_DIR`), a private `gui.ini`, the system hotkey off and
**`BEND_STAND_D06_GUARD=1`**: the runtime hook then makes `serial.Serial.open` / `serial.serial_for_url` raise
`HardwareAccessForbidden` inside the exe (the same guard as `03_SW/tests/conftest.py`), so no smoke run can open a
COM port. Without that variable the shipped exe opens exactly the port the operator selected — nothing else changes.

| Check | Expectation |
|---|---|
| S1 `BirdBendStand-cli.exe --version` | rc 0, version = the stamped `<base>+g<hash>` |
| S2 `BirdBendStand-cli.exe --headless --sim` | rc 0, `connected: sim`, link CONNECTED, stream on, DATA > 0 |
| S3 `BirdBendStand.exe --headless --sim` | the same from the windowed exe |
| S4 `BirdBendStand.exe --sim` (offscreen, `BEND_STAND_GUI_QUIT_AFTER_MS`) | rc 0; exit summary: link CONNECTED, stream on, DATA > 0 |
| S5 `BirdBendStand.exe` without endpoint | rc 0, starts **disconnected** (no port touched) |
| S6 `BirdBendStand-cli.exe --headless --port COM250` with the guard | rc ≠ 0, `HardwareAccessForbidden` (the OS is never asked to open the port) |
| S7 folder layout | `qwindows.dll`, dist-info present; no pytest / PyInstaller / yaml; `docs\USER_MANUAL` and `docs\QUICK_REFERENCE` as `.md` + `.html`, every screenshot the manuals reference |
| S8 offline report | `BirdBendStand-cli.exe --headless --sim --record --recordings <tmp>` writes a short recording; `BirdBendStand-cli.exe report <recording> --out <tmp>` → rc 0, `report.json` (bird.bend.report, rows > 0) + `report.html`; a missing folder → rc 2 |
| S9 log written once | `BirdBendStand.exe --sim --log-level INFO` started **without console handles** (as from Explorer): stdio in `BirdBendStand.log`, log records in `bend_stand.log` only — the GUI exit line exactly once (SWR-14, OI-F-RV-04). S2 / S3 also require `bend_stand.log` with the start and incident lines |

Result 2026-10-08: S1–S7 **7/7 PASS** in the build run (offscreen) and again from the *extracted zip* in a scratch
folder with `PATH=C:\Windows\system32;C:\Windows` (no Python reachable) and `--native-gui` (real `qwindows`
platform); after the docs / report / icon additions **8/8 PASS** in the build run and **8/8 PASS** from the extracted
zip (Python-free `PATH`, offscreen). Unit tests: `03_SW/tests/unit/test_dist_packaging.py` (helpers, docs renderer,
icon resource) and `03_SW/tests/unit/test_cli_report.py` (`report` subcommand, `--recordings`, `--record`).

## 3. Install on the lab PC (fresh install, DM-02)

Requirements: Windows 10 (64-bit) or later. No Python, no admin rights for the zip variant. The ST-LINK/V2-1 virtual
COM port driver comes with Windows 10 (or the ST-LINK driver package) — only needed for the real board.

**Zip (default)**

1. Copy `BirdBendStand-<ver>.zip` to the PC, right-click → *Properties* → tick *Unblock* (files from another PC or
   the internet carry the mark-of-the-web; unblocking avoids SmartScreen prompts for every DLL), then *Extract All…*
   to e.g. `C:\BirdBendStand\` or `%LOCALAPPDATA%\Programs\`. Do not run it from inside the zip.
2. Start `BirdBendStand.exe`. It opens **disconnected** — no COM port is opened until you select one on the
   Connection tab and press *Connect* (D-06). For a demo without hardware use `BirdBendStand.exe --sim` (create a
   shortcut with that argument).
3. Optional: pin to the taskbar / create a desktop shortcut.

The executables are not code-signed: Windows SmartScreen may show "Windows protected your PC" on the first start →
*More info* → *Run anyway* (or unblock the zip before extracting, step 1).

**Installer (if built with Inno Setup)**: run `BirdBendStand-<ver>-setup.exe` — per-user install by default
(`%LOCALAPPDATA%\Programs\BirdBendStand`, no admin), "all users" selectable; Start-menu entries *Bird Bend Stand*,
*Bird Bend Stand (simulator)* (`--sim`), *Operator manual*, *Quick reference card* (the HTML pages in `docs\`),
*Build info*; optional desktop icon. An update over an older version
replaces the program folder; uninstall leaves the data below untouched.

**Update**: extract the new zip next to the old folder (or over a deleted old one); settings and calibrations live
outside the program folder and are kept.

### Where the application keeps its files

| What | Where | How to change |
|---|---|---|
| Calibrations, sessions, presets | `%APPDATA%\BirdBendStand\{calibration,sessions,presets}` | env `BEND_STAND_DATA_DIR` (whole root) |
| GUI layout | `%APPDATA%\BirdBendStand\gui.ini` | env `BEND_STAND_GUI_SETTINGS` |
| Application log (every run, source or frozen: start line, warnings / errors, uncaught exceptions, **incidents**: link state / loss, STOP / HALT / PAUSE issued and (not) confirmed, FW EVENTs — latches, limits, faults, clears —, PC limit trips, recording failures, sequence state changes) | `%APPDATA%\BirdBendStand\logs\bend_stand.log`, rotating 5 MiB × (1 + 10 backups) (SWR-14) | follows the data root; `--log-level DEBUG` adds detail |
| stdout / stderr of the windowed `BirdBendStand.exe` (prints, tracebacks, hard crashes via faulthandler) | `%APPDATA%\BirdBendStand\logs\BirdBendStand.log` (previous: `.log.1`, rotated at 2 MiB) — log records are *not* duplicated here | follows the data root |
| Recordings | `Documents\BirdBendStand\recordings` | `"recordings_root"` in the session file `%APPDATA%\BirdBendStand\sessions\default.bbsession.json` (loaded at start) or a `--session <file>` |

More log detail: `BirdBendStand-cli.exe --log-level INFO` (console) or `BirdBendStand.exe --log-level DEBUG` (log
file). Command line of both executables = `python -m bend_stand`: `[--sim [scenario.simscn.json]] [--port COM7 |
--port tcp://host:port] [--session file] [--recordings DIR] [--headless [--duration s] [--record]] [--log-level L]
[--version]`. `--recordings DIR` sets the recordings folder for this run (GUI and headless).

**Operator documentation**: `docs\USER_MANUAL.html` and `docs\QUICK_REFERENCE.html` in the program folder (Start
menu *Operator manual* / *Quick reference card* with the installer); sources:
[`03_SW/docs/USER_MANUAL.md`](../docs/USER_MANUAL.md), [`03_SW/docs/QUICK_REFERENCE.md`](../docs/QUICK_REFERENCE.md).

**Offline report rebuild** (no board, no Python; SW-REP-003), from a command prompt in the program folder:
```bat
BirdBendStand-cli.exe report <recording folder> [--cal load_cal.json] [--tare-raw N] [--bend3p L b h] [--out DIR]
```
writes `report.json` + `report.html` (into the recording folder or `--out`) and prints both paths; exit code 0 = done,
2 = not a readable recording. Same public entry as `python -m bend_stand.core.report` in a development checkout
(`python -m bend_stand report …` works there too).

### DM-02 checklist (with Validator F)

1. fresh Windows 10 user account / PC without Python: extract the zip, start `BirdBendStand.exe --sim`;
2. the window opens, *Connection* shows the simulator connected, live plots move, link statistics count;
3. close; start `BirdBendStand.exe` without arguments → disconnected, COM list shows the ports, nothing opened;
4. `%APPDATA%\BirdBendStand` exists after a setting was saved; the log file shows the start lines;
5. `BirdBendStand-cli.exe --version` prints the version that `BUILD_INFO.txt` lists;
6. `docs\USER_MANUAL.html` opens in the browser with screenshots; `BirdBendStand-cli.exe report <a recording>`
   rebuilds its report.

## 4. Known limitations / notes

- Not code-signed (SmartScreen prompt, see above). Signing needs a certificate — PO decision.
- Windowed executable: an unhandled exception at start-up shows a PyInstaller error box and is also written to the
  log file.
- Window / taskbar icon: the GUI loads `bend_stand/gui/resources/BirdBendStand.ico` (`gui.resources.app_icon()`,
  GRQ-B-31 c); the spec bundles it at the same relative path (`_internal\bend_stand\gui\resources\`), S7 checks it.
  The `.exe` files carry the same icon (`packaging/assets`, kept identical — unit test).
- `opengl32sw.dll` (20 MiB) is kept as the software OpenGL fallback for PCs / remote sessions without a GPU driver.
- Antivirus heuristics occasionally flag fresh PyInstaller executables; if the lab PC's AV quarantines a file,
  whitelist the program folder.
