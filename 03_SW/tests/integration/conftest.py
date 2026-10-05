"""SW <-> FW host twin integration tests (owner: Integrator, 03_SW/tests/integration/**).

The twin is built from Implementer A's unmodified 02_FW/src/{pure,core,gen} + the twin seams
(00_System/tools/fw_twin). Core selection: environment variable ``BEND_TWIN_CORE``:

- ``fw`` (default): A's firmware. While A's ``02_FW/src/core/*.c`` is absent every twin test is reported as
  **xfail** with that reason (run=False); a twin build failure is reported the same way, with the gcc error.
- ``probe``: the twin's harness probe (fw_twin/probe, NOT the firmware) — used only to validate the twin
  harness and these tests themselves before A's core exists. Probe results are never M1 evidence.

Tests that need a part of Implementer B's backend that does not exist yet (``bend_stand.core.device``,
``bend_stand.io.sim``) are xfail with the missing module as the reason.

Implements: SYS-008 (SW<->twin integration), SW_test_plan level X, FW_test_plan level T (shared harness)
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

SW_ROOT = Path(__file__).resolve().parents[2]
REPO = SW_ROOT.parent
TOOLS = REPO / "00_System" / "tools"
for p in (TOOLS, TOOLS / "fw_twin"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build as twin_build  # noqa: E402

CORE = os.environ.get("BEND_TWIN_CORE", "fw")
_BUILD: dict[str, object] = {}


def twin_unavailable() -> str | None:
    """Reason why the twin of the selected core cannot run (None = available)."""
    if "reason" in _BUILD:
        return _BUILD["reason"]  # type: ignore[return-value]
    reason = None
    try:
        twin_build.find_gcc()
    except SystemExit as e:
        reason = str(e)
    if reason is None and CORE == "fw" and not twin_build.fw_core_present():
        reason = "Implementer A's 02_FW/src/core/*.c absent (M1-WP5 not delivered): FW twin cannot be built"
    if reason is None:
        try:
            # OBS-M2-09: one private binary per test run (build.private_build_dir(), $BEND_TWIN_BUILD_DIR to
            # choose it), pinned for the whole session: concurrent roles cannot rebuild or lock it mid-run
            _BUILD["exe"] = twin_build.ensure_built_private(CORE)
        except SystemExit as e:
            log = twin_build.private_build_dir() / f"build_{CORE}.log"
            tail = log.read_text(encoding="utf-8", errors="replace")[-1500:] if log.exists() else ""
            reason = f"FW twin build failed ({e}); log tail:\n{tail}"
    _BUILD["reason"] = reason
    return reason


def missing_b(module: str, attr: str | None = None) -> str | None:
    """Reason string if Implementer B's module/attribute is not available yet."""
    try:
        m = importlib.import_module(module)
    except Exception as e:  # noqa: BLE001
        return f"Implementer B's {module} not available yet ({type(e).__name__}: {e})"
    if attr and not hasattr(m, attr):
        return f"Implementer B's {module}.{attr} not available yet"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = Path(__file__).resolve().parent
    for item in items:
        if here not in Path(str(item.path)).resolve().parents:
            continue
        item.add_marker(pytest.mark.integration)
        if item.get_closest_marker("needs_twin") is not None or item.get_closest_marker("twin") is not None:
            r = twin_unavailable()
            if r:
                item.add_marker(pytest.mark.xfail(reason=r, run=False))
        for m in item.iter_markers("needs_b"):
            r = missing_b(*m.args)
            if r:
                item.add_marker(pytest.mark.xfail(reason=r, run=False))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "twin: needs the FW host twin of the selected core (BEND_TWIN_CORE)")
    config.addinivalue_line("markers", "needs_b(module, attr=None): needs this module of Implementer B's backend")


def pytest_report_header(config: pytest.Config) -> str:
    return (f"fw_twin core: {CORE} ({'A firmware' if CORE == 'fw' else 'HARNESS PROBE - not M1 evidence'}); "
            f"private build dir: {os.environ.get('BEND_TWIN_BUILD_DIR', '<per-run temp dir>')}")


@pytest.fixture(scope="session")
def twin_exe() -> Path:
    r = twin_unavailable()
    if r:
        pytest.xfail(r)
    return _BUILD["exe"]  # type: ignore[return-value]


@pytest.fixture
def twin(twin_exe, tmp_path):
    """Lock-step twin, blank flash, booted at virtual t = 0 (FW_test_plan precondition B without GET_INFO)."""
    from twin import Twin

    t = Twin("lockstep", exe=twin_exe, run_dir=tmp_path)
    yield t
    t.close()


@pytest.fixture
def twin_rt(twin_exe, tmp_path):
    """Real-time twin serving tcp://127.0.0.1:<port> (B's TcpTransport connects to it) + control port."""
    from twin import Twin

    speed = float(os.environ.get("BEND_TWIN_SPEED", "1"))
    t = Twin("realtime", exe=twin_exe, run_dir=tmp_path, speed=speed)
    port, ctl = t.serve(0, 0)
    t.endpoint = f"tcp://127.0.0.1:{port}"
    t.ctl_port = ctl
    yield t
    t.close()
