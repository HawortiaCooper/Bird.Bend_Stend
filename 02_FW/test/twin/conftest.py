"""Validator E - twin-level (T) suites for the M1 gate (FW_test_plan v0.1 §1.2 level T).

Drives Implementer A's unmodified FW inside the Integrator's host twin (00_System/tools/fw_twin,
lock-step virtual time) through the twin's Python API. The PC side and every expectation use the
Integrator's oracles (ref_codec, ref_cmdcheck, vectors, gen_params) and the validator's own
oracles (02_FW/test/val_oracles); nothing of Implementer A's tests or of the Integrator's
integration tests is reused.

Run (folder name has no test_ prefix, PlatformIO ignores it):
    .venv\\Scripts\\python -m pytest 02_FW\\test\\twin -q -p no:randomly          (fixed order)
    .venv\\Scripts\\python -m pytest 02_FW\\test\\twin -q -p randomly -p "randomly" (random order, seed printed)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FW = HERE.parents[1]
ROOT = FW.parent
TOOLS = ROOT / "00_System" / "tools"
for p in (TOOLS, TOOLS / "fw_twin", FW / "test" / "val_oracles", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build as twin_build  # noqa: E402
from vhelp import V  # noqa: E402
from twin import Twin  # noqa: E402


@pytest.fixture(scope="session")
def twin_exe() -> Path:
    exe = twin_build.ensure_built("fw")
    assert "probe" not in exe.name, "validator evidence must use A's FW core, never the probe"
    return exe


@pytest.fixture
def tw(twin_exe, tmp_path):
    t = Twin("lockstep", exe=twin_exe, run_dir=tmp_path / "run")
    yield t
    t.close()


@pytest.fixture
def v(tw) -> V:
    return V(tw)
