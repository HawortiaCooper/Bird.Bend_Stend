"""Layering of the backend (SW_design KD-01, §2): ``calc`` ← ``io`` ← ``core``; no Qt in the backend.

Origin: Thrust_Stand_HAW/03_SW/tests/unit/test_layering_packaging.py @37c87471 (adapted: package names,
bend-stand whitelist).

Verifies: SW-PLT-002
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import bend_stand

PKG = Path(bend_stand.__file__).resolve().parent
QT = ("PySide6", "shiboken6", "pyqtgraph", "PyQt5", "PyQt6")
#: leaf modules of ``core`` that ``io`` and ``calc`` may import (generated name/param tables, exceptions)
CORE_LEAVES = {"bend_stand.core.params_gen", "bend_stand.core.protocol_gen", "bend_stand.core.errors",
               "bend_stand.core.model", "bend_stand.core.clock",
               "bend_stand.core.timing"}                      # OBS-P3-01: the sim server sets the timer resolution


def _modules(sub: str) -> list[Path]:
    return sorted((PKG / sub).rglob("*.py"))


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel_pkg = ".".join(path.relative_to(PKG.parent).with_suffix("").parts[:-1])
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = rel_pkg.split(".")
                base = base[: len(base) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            out.append(mod)
            out += [f"{mod}.{a.name}" for a in node.names]
    return out


@pytest.mark.req("SW-PLT-002")
@pytest.mark.parametrize("sub", ["core", "io", "calc"])
def test_backend_never_imports_qt(sub: str) -> None:
    bad = [(p.name, m) for p in _modules(sub) for m in _imports(p) if m.split(".")[0] in QT]
    assert not bad, f"Qt imported in the backend: {bad}"


@pytest.mark.req("SW-PLT-002")
@pytest.mark.parametrize("sub", ["core", "io", "calc"])
def test_backend_never_imports_gui(sub: str) -> None:
    bad = [(p.name, m) for p in _modules(sub) for m in _imports(p) if m.startswith("bend_stand.gui")]
    assert not bad


@pytest.mark.req("SW-PLT-002")
def test_io_imports_from_core_only_leaf_modules() -> None:
    bad = []
    for p in _modules("io"):
        for m in _imports(p):
            if m.startswith("bend_stand.core") and m not in CORE_LEAVES and \
                    not any(m.startswith(leaf + ".") for leaf in CORE_LEAVES) and m != "bend_stand.core":
                bad.append((str(p.relative_to(PKG)), m))
    assert not bad, bad


@pytest.mark.req("SW-PLT-002")
def test_core_leaf_modules_are_leaves() -> None:
    """The ``core`` modules ``io``/``calc`` may use import nothing of the backend except other leaves."""
    bad = []
    for leaf in CORE_LEAVES:
        path = PKG.parent / (leaf.replace(".", "/") + ".py")
        for m in _imports(path):
            if m.startswith("bend_stand.") and not any(m == lf or m.startswith(lf + ".") for lf in CORE_LEAVES) \
                    and m != "bend_stand.core":
                bad.append((leaf, m))
    assert not bad, bad


@pytest.mark.req("SW-PLT-002")
def test_calc_is_pure() -> None:
    allowed_top = {"__future__", "math", "dataclasses", "typing", "collections", "enum", "struct", "numpy",
                   "bend_stand", "functools", "itertools", "statistics", "bisect", "types", "fractions",
                   "decimal", "operator"}
    bad = []
    for p in _modules("calc"):
        for m in _imports(p):
            top = m.split(".")[0]
            if top not in allowed_top:
                bad.append((p.name, m))
            if m.startswith("bend_stand.") and m != "bend_stand.core" and not (m.startswith("bend_stand.calc") or
                                                    any(m.startswith(leaf) for leaf in CORE_LEAVES)):
                bad.append((p.name, m))
    assert not bad, bad


@pytest.mark.req("SW-PLT-002")
def test_importing_whole_backend_loads_no_qt() -> None:
    mods = sorted(".".join(p.relative_to(PKG.parent).with_suffix("").parts)
                  for sub in ("core", "io", "calc") for p in _modules(sub) if p.name != "__main__.py")
    mods = [m[: -len(".__init__")] if m.endswith(".__init__") else m for m in mods]
    code = ("import sys, importlib\n"
            f"for m in {mods!r}:\n    importlib.import_module(m)\n"
            f"bad = [m for m in sys.modules if m.split('.')[0] in {QT!r}]\n"
            "print('QT:' + ','.join(bad))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                       cwd=str(PKG.parent))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("QT:"), r.stdout


@pytest.mark.req("SW-PLT-002")
def test_backend_reads_time_only_through_clock() -> None:
    """All backend time comes from the Clock protocol (§12.6); ``time.*`` only in clock/timing modules."""
    allowed = {"clock.py", "timing.py", "server.py", "transport.py",
               "win_hotkey.py"}           # OS hotkey thread: measures the key → HALT latency with perf_counter
    bad = []
    for sub in ("core", "io"):
        for p in _modules(sub):
            if p.name in allowed:
                continue
            src = p.read_text(encoding="utf-8")
            for fn in ("time.monotonic", "time.perf_counter", "time.time(", "time.sleep"):
                if fn in src:
                    bad.append((str(p.relative_to(PKG)), fn))
    assert not bad, bad
