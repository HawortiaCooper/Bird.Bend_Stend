"""Shared pytest configuration for all 03_SW test suites (owner: Implementer B, D-29 n).

- **D-06 port guard** (autouse): in every test without the ``hil`` marker, ``serial.Serial.open`` and
  ``serial.serial_for_url`` raise ``HardwareAccessForbidden`` — no test can open a COM port.
- Options ``--run-perf``, ``--run-soak``, ``--run-winint`` (those markers skip by default); ``hil`` always
  skips unless ``--run-hil`` **and** the environment variable ``BEND_HIL_PORT`` is set (D-06).
- Fixtures: ``repo_root``, ``vectors_dir``, ``protocol_vectors``, ``check_vectors``, ``units_vectors`` (loaded in
  place from ``00_System/tools/vectors/``, never copied), ``ref_oracle`` (adds ``00_System/tools`` to
  ``sys.path`` for tests only), ``fake_clock``.
- Rule (SW_test_plan §1 rule 4): every test under ``tests/unit`` carries ``@pytest.mark.req(...)``.

Origin: Thrust_Stand_HAW/03_SW/tests/unit/conftest.py @37c87471 (adapted: repo paths, D-06 guard, options).

Implements: D-06 (test guard), SW-PLT-001 (test infrastructure)
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

SW_ROOT = Path(__file__).resolve().parents[1]           # 03_SW
REPO_ROOT = SW_ROOT.parent                              # Bird.Bend_Stend
TOOLS = REPO_ROOT / "00_System" / "tools"
VECTORS = TOOLS / "vectors"
SRC = SW_ROOT / "src"
UNIT = SW_ROOT / "tests" / "unit"
# no test registers a real system-wide Pause/Break hotkey or installs an LL keyboard hook: the hotkey is off in the
# test suites (``test_hooks.hotkey_press()`` still reaches HALT); hotkey tests pass ``BackendSettings(hotkey="fake")``
os.environ.setdefault("BEND_STAND_HOTKEY", "off")
for _p in (SRC, UNIT):                                  # UNIT: shared unit-test helpers (bbs_support, rigs)
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def pytest_addoption(parser: pytest.Parser) -> None:
    g = parser.getgroup("bend_stand")
    g.addoption("--run-perf", action="store_true", default=False, help="run tests marked perf")
    g.addoption("--run-soak", action="store_true", default=False, help="run tests marked soak")
    g.addoption("--run-winint", action="store_true", default=False, help="run tests marked winint")
    g.addoption("--run-hil", action="store_true", default=False,
                help="run tests marked hil (also needs BEND_HIL_PORT; only at a PO-approved HW gate, D-06)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skips = {
        "perf": (config.getoption("--run-perf"), "perf test: use --run-perf"),
        "soak": (config.getoption("--run-soak"), "soak test: use --run-soak"),
        "winint": (config.getoption("--run-winint"), "interactive Windows test: use --run-winint"),
    }
    hil_ok = bool(config.getoption("--run-hil")) and bool(os.environ.get("BEND_HIL_PORT"))
    for item in items:
        for name, (enabled, why) in skips.items():
            if not enabled and item.get_closest_marker(name) is not None:
                item.add_marker(pytest.mark.skip(reason=why))
        if item.get_closest_marker("hil") is not None and not hil_ok:
            item.add_marker(pytest.mark.skip(reason="hil: needs --run-hil and BEND_HIL_PORT (D-06)"))
    unit_dir = SW_ROOT / "tests" / "unit"
    missing = [it.nodeid for it in items
               if unit_dir in Path(str(it.path)).resolve().parents and it.get_closest_marker("req") is None]
    if missing:
        raise pytest.UsageError("tests without @pytest.mark.req: " + ", ".join(missing))


# ----------------------------------------------------------------------------------------- D-06 guard

def _forbidden(*_a, **_k):
    from bend_stand.core.errors import HardwareAccessForbidden  # noqa: PLC0415

    raise HardwareAccessForbidden()


@pytest.fixture(autouse=True)
def _d06_port_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """D-06: no test opens a COM port (``hil`` tests excepted, which skip unless explicitly enabled)."""
    if request.node.get_closest_marker("hil") is not None:
        yield
        return
    try:
        import serial  # noqa: PLC0415
        import serial.serialutil  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        yield
        return
    monkeypatch.setattr(serial.Serial, "open", _forbidden, raising=True)
    monkeypatch.setattr(serial, "serial_for_url", _forbidden, raising=True)
    yield


# ----------------------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def vectors_dir() -> Path:
    return VECTORS


def _load(name: str) -> dict:
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def protocol_vectors() -> dict:
    return _load("protocol_vectors.json")


@pytest.fixture(scope="session")
def check_vectors() -> dict:
    return _load("check_vectors.json")


@pytest.fixture(scope="session")
def units_vectors() -> dict:
    return _load("units_vectors.json")


def load_vectors(name: str) -> dict:
    """Module-level loader for parametrisation at collection time."""
    return _load(name)


@pytest.fixture(scope="session")
def ref_oracle() -> types.SimpleNamespace:
    """Integrator oracles (tests only, never production code): ``ref_codec``, ``ref_cmdcheck``."""
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import ref_cmdcheck  # noqa: PLC0415
    import ref_codec  # noqa: PLC0415

    return types.SimpleNamespace(ref_codec=ref_codec, ref_cmdcheck=ref_cmdcheck)


@pytest.fixture
def fake_clock():
    from bend_stand.core.clock import FakeClock  # noqa: PLC0415

    return FakeClock()
