# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — Bird Bend Stand Windows distribution (one-folder), SW_design §24, packaging/README.md.
# Owner: Implementer B. Run through 03_SW/packaging/build_dist.ps1 (or:
#   .venv\Scripts\python -m PyInstaller --noconfirm --clean --distpath 03_SW\dist --workpath 03_SW\build\pyinstaller
#       03_SW\packaging\BirdBendStand.spec)
# Output: 03_SW/dist/BirdBendStand-<ver>/{BirdBendStand.exe (GUI, windowed), BirdBendStand-cli.exe (console),
#         _internal/ (Python 3.14 runtime, PySide6, pyqtgraph, numpy, pyserial, bend_stand incl. io.sim)}
# Version: pyproject [project].version + git hash (packaging/buildinfo.py); env BBS_BUILD_INFO = JSON written by
# `buildinfo.py info --out` so the build script and the spec use one version per run.
# Implements: SW-PLT-001, SYS-008 (simulator bundled: --sim works on the target PC)
import os
import sys
from pathlib import Path

sys.path.insert(0, SPECPATH)
import buildinfo  # noqa: E402  (03_SW/packaging/buildinfo.py)

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

PACK = Path(SPECPATH)
SW_ROOT = PACK.parent
SRC = SW_ROOT / "src"
WORK = Path(workpath)  # noqa: F821 (PyInstaller spec global)
sys.path.insert(0, str(SRC))  # collect_submodules below runs in an isolated child that inherits sys.path

info = buildinfo.load_or_collect(os.environ.get("BBS_BUILD_INFO"))
print(f"[BirdBendStand.spec] version {info.version} -> {info.dist_name}")

ICON = str(PACK / "assets" / "BirdBendStand.ico")
GUI_ICON = SRC / "bend_stand" / "gui" / "resources" / "BirdBendStand.ico"
assert GUI_ICON.is_file(), f"{GUI_ICON} missing (bend_stand.gui.resources)"
VF_GUI = str(buildinfo.write_version_file(WORK / "version_gui.txt", info, "BirdBendStand.exe",
                                          "Bird Bend Stand — bend test stand application"))
VF_CLI = str(buildinfo.write_version_file(WORK / "version_cli.txt", info, "BirdBendStand-cli.exe",
                                          "Bird Bend Stand — console (headless smoke test, diagnostics)"))
DIST_INFO = buildinfo.write_dist_info(WORK / "metadata", info)

# bend_stand: every module (gui.app is imported dynamically by __main__; io.sim.server runs out of process);
# tests are not part of the package.
HIDDEN = collect_submodules("bend_stand")
assert any(m == "bend_stand.gui.app" for m in HIDDEN), "bend_stand.gui.app not found: SRC not importable?"
assert any(m.startswith("bend_stand.io.sim") for m in HIDDEN), "simulator (bend_stand.io.sim) missing"

EXCLUDES = [
    # not used by bend_stand (KD-11 minimal runtime) — keeps hooks from pulling them in if present in the venv
    "tkinter", "_tkinter", "PyQt5", "PyQt6", "PySide2", "matplotlib", "scipy", "IPython", "jupyter_client",
    "pytest", "_pytest", "pytestqt", "yaml", "setuptools", "pkg_resources", "PyInstaller",
    # numpy build tools (f2py pulls charset_normalizer); TLS is not used (no network access; stdlib users of
    # ssl — urllib, http.client, asyncio — import it optionally)
    "numpy.f2py", "numpy.distutils", "charset_normalizer", "ssl", "_ssl",
    # pyqtgraph parts the application does not use (useOpenGL=False, no examples / notebook widgets)
    "pyqtgraph.examples", "pyqtgraph.opengl", "pyqtgraph.jupyter",
    # Qt modules not used (bend_stand imports QtCore / QtGui / QtWidgets only)
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDBus", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.QtXml", "PySide6.QtConcurrent", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
]

a = Analysis(
    [str(PACK / "entry_birdbendstand.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(DIST_INFO), DIST_INFO.name),
           # GUI package resources (gui.resources.app_icon: window / taskbar icon, GRQ-B-31 c) — same layout as the
           # source tree, so ICON_PATH = <module dir>/BirdBendStand.ico resolves in the frozen app
           (str(GUI_ICON), "bend_stand/gui/resources")],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PACK / "rthook_bend_stand.py")],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,  # asserts stay active (same behaviour as the tested source tree)
)
# Qt translations: the application UI is English only (Qt's own dialogs stay English) — saves ~7 MB
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").startswith("PySide6/translations/")]
pyz = PYZ(a.pure)

exe_gui = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="BirdBendStand",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False,                       # GUI subsystem; stdout/stderr -> %APPDATA%\BirdBendStand\logs (rthook)
    disable_windowed_traceback=False,
    icon=ICON, version=VF_GUI,
)
exe_cli = EXE(
    pyz, a.scripts, [("u", None, "OPTION")],   # unbuffered stdout: headless statistics appear live
    exclude_binaries=True,
    name="BirdBendStand-cli",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True,                        # --headless / --version / --log-level DEBUG diagnostics
    icon=ICON, version=VF_CLI,
)
coll = COLLECT(
    exe_gui, exe_cli,
    a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name=info.dist_name,
)
