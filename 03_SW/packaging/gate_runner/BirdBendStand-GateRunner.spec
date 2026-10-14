# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — BirdBendStand-GateRunner (D-57, GR-1; design 00_System/tools/hil/GATE_RUNNER.md §1).
# Owner of the packaging: Implementer B. Owner of the runner code: Validator E (00_System/tools/hil).
# Run through 03_SW/packaging/gate_runner/build_gate_runner.ps1 (stages the twin, pins the environment, smoke-tests).
# Console, one-folder: <kit>/BirdBendStand-GateRunner/{BirdBendStand-GateRunner.exe, _internal/{resources, fw_twin, …}}
# The runner hash (gate_res.bundle_sha256) = the exe + every file under _internal/resources and _internal/fw_twin.
# Env: BBS_GR_INFO = build_info.json (version / git), BBS_GR_STAGE = staged data (fw_twin/fw_twin.exe, normalised).
# Implements: D-57 (frozen gate runner), D-06 (no port without the approval file — runner logic, unchanged)
import os
import sys
from pathlib import Path

PACK = Path(SPECPATH).parent                     # 03_SW/packaging
sys.path.insert(0, str(PACK))
import buildinfo  # noqa: E402

REPO = PACK.parents[1]
TOOLS = REPO / "00_System" / "tools"
HIL = TOOLS / "hil"
TWIN_DIR = TOOLS / "fw_twin"
WORK = Path(workpath)  # noqa: F821
STAGE = Path(os.environ["BBS_GR_STAGE"])
INFO_JSON = Path(os.environ["BBS_GR_INFO"])
assert INFO_JSON.is_file(), f"BBS_GR_INFO {INFO_JSON}: build_info.json missing"
info = buildinfo.load_or_collect(INFO_JSON)
print(f"[GateRunner.spec] version {info.version}")

MODULES = ["gate_runner", "gate_res", "gate_approval", "gate_export", "hil_session", "hil_procs", "hil_link",
           "hil_budget", "hil_operator", "hil_report", "ref_codec", "gen_params", "gen_protocol", "twin", "build"]
for m, where in (("gate_runner", HIL), ("ref_codec", TOOLS), ("twin", TWIN_DIR), ("build", TWIN_DIR)):
    assert (where / f"{m}.py").is_file(), f"{where / m}.py missing"

PICTURES = sorted((REPO / "01_HW" / "diagrams" / "bench").glob("B*.png"))
TWIN_EXE = STAGE / "fw_twin" / "fw_twin.exe"
assert TWIN_EXE.is_file(), f"staged twin missing: {TWIN_EXE}"
assert PICTURES, "no bench pictures (01_HW/diagrams/bench/B*.png)"
DATAS = [(str(REPO / "00_System" / "specs" / "params.yaml"), "resources"),
         (str(HIL / "resources" / "pictures.json"), "resources"),
         *[(str(p), "resources/pictures") for p in PICTURES],
         (str(TWIN_EXE), "fw_twin")]

VF = str(buildinfo.write_version_file(WORK / "version_gaterunner.txt", info, "BirdBendStand-GateRunner.exe",
                                      "Bird Bend Stand hardware gate runner (D-57)", reproducible=True))
ICON = str(PACK / "assets" / "BirdBendStand.ico")

a = Analysis(
    [str(HIL / "gate_runner.py")],
    pathex=[str(HIL), str(TOOLS), str(TWIN_DIR)],
    binaries=[],
    datas=DATAS,
    hiddenimports=[*MODULES, "serial", "serial.serialwin32", "serial.win32", "serial.serialutil", "yaml"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(Path(SPECPATH) / "rthook_gate_d06.py")],
    excludes=["tkinter", "_tkinter", "PySide6", "shiboken6", "PyQt5", "PyQt6", "numpy", "pyqtgraph", "bend_stand",
              "matplotlib", "scipy", "IPython", "pytest", "_pytest", "setuptools", "pkg_resources", "PyInstaller",
              "ssl", "_ssl", "charset_normalizer"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [("u", None, "OPTION")],     # unbuffered: operator prompts / progress appear immediately
    exclude_binaries=True,
    name="BirdBendStand-GateRunner",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True,
    icon=ICON, version=VF,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="BirdBendStand-GateRunner")
