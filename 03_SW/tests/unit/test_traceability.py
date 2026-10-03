"""Origin notes and requirement tags in the backend (SYS-010, D-02, D-29 m).

Every module that cites Thrust_Stand_HAW carries ``Origin: Thrust_Stand_HAW/<path> @<hash>``; every backend
module (``core``, ``io``, ``calc``, package root; generated modules excluded) carries an ``Implements:`` tag.

Verifies: SYS-010
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import bend_stand

PKG = Path(bend_stand.__file__).resolve().parent
GENERATED = {"params_gen.py", "protocol_gen.py"}
ORIGIN = re.compile(r"Origin: Thrust_Stand_HAW/[\w/.]+\.py @([0-9a-f]{7,40})")


def _backend_modules() -> list[Path]:
    out = [PKG / "__init__.py", PKG / "__main__.py"]
    for sub in ("core", "io", "calc"):
        out += [p for p in (PKG / sub).rglob("*.py") if p.name not in GENERATED and p.name != "__init__.py"]
    return out


@pytest.mark.req("SYS-010")
def test_origin_notes_carry_path_and_commit() -> None:
    cited = 0
    for p in _backend_modules():
        src = p.read_text(encoding="utf-8")
        if "Thrust_Stand_HAW" in src:
            m = ORIGIN.search(src)
            assert m, f"{p.name}: Thrust_Stand reuse without 'Origin: <path> @<hash>'"
            assert m.group(1).startswith("37c87471"), p.name       # TS HEAD at copy time (D-29 m)
            cited += 1
    assert cited >= 15


@pytest.mark.req("SYS-010")
def test_every_backend_module_has_an_implements_tag() -> None:
    missing = [p.name for p in _backend_modules() if "Implements:" not in p.read_text(encoding="utf-8")]
    assert not missing, missing
