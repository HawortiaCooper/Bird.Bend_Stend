"""Validator F — M1 validation suite configuration (owner: Validator F, SW_test_plan §1, §2.3, §2.4).

Rules enforced here (SW_test_plan §1 independence rules):
* every test under ``tests/validation`` gets the ``validation`` marker and MUST carry ``@pytest.mark.req(...)``
  (collection fails otherwise, rule 4);
* extra D-06 guard on top of B's root guard: constructing ``serial.Serial`` with a port raises
  ``HardwareAccessForbidden`` (rule 5) — tests that need the class replace it with a fake first;
* markers ``defect(id)`` (regression / open-defect tests) and ``twin`` are registered here;
* ``_reports/trace.json`` (requirement → node ids → outcome) is written at session end (§2.3).

Expected values come only from the SRS / ICD / ``params.yaml`` / shared vectors / ``oracle/f_ref.py``; the
production code is never used to compute an expected value (rule 1). Only ``harness.py`` names Backend members
(SW_design §15), the hooks (``test_hooks``, ``backend.sim``) and the wire log (rule 2).

Verifies: (infrastructure) — SW_test_plan rules 1–5
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
SW_ROOT = HERE.parents[1]
REPO = SW_ROOT.parent
TOOLS = REPO / "00_System" / "tools"
for _p in (HERE, TOOLS, TOOLS / "fw_twin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_TRACE: dict[str, list[tuple[str, str]]] = defaultdict(list)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "defect(id): regression / open-defect test of a Validator F defect")
    config.addinivalue_line("markers", "twin: needs the FW host twin (A's firmware, 00_System/tools/fw_twin)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    missing = []
    for item in items:
        if HERE not in Path(str(item.path)).resolve().parents:
            continue
        item.add_marker(pytest.mark.validation)
        if item.get_closest_marker("req") is None:
            missing.append(item.nodeid)
    if missing:
        raise pytest.UsageError("validation tests without @pytest.mark.req: " + ", ".join(missing))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    rep = outcome.get_result()
    if HERE not in Path(str(item.path)).resolve().parents:
        return
    if rep.when == "call" or (rep.when == "setup" and rep.outcome != "passed"):
        state = "xfailed" if hasattr(rep, "wasxfail") and rep.skipped else rep.outcome
        for m in item.iter_markers("req"):
            for rid in m.args:
                _TRACE[rid].append((item.nodeid, state))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _TRACE:
        return
    out = HERE / "_reports"
    try:
        out.mkdir(exist_ok=True)
        (out / "trace.json").write_text(json.dumps(
            {rid: [{"node": n, "outcome": o} for n, o in v] for rid, v in sorted(_TRACE.items())}, indent=1),
            encoding="utf-8")
    except OSError:  # pragma: no cover - reporting only
        pass


# ----------------------------------------------------------------------------------------- D-06 (rule 5)

@pytest.fixture(autouse=True)
def _f_port_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    if request.node.get_closest_marker("hil") is not None:
        yield
        return
    import serial  # noqa: PLC0415

    real_init = serial.Serial.__init__

    def guarded(self, port=None, *a, **k):  # noqa: ANN001
        if port is not None:
            from bend_stand.core.errors import HardwareAccessForbidden  # noqa: PLC0415

            raise HardwareAccessForbidden()
        real_init(self, None, *a, **k)

    monkeypatch.setattr(serial.Serial, "__init__", guarded)
    yield


# ----------------------------------------------------------------------------------------- fixtures

@pytest.fixture
def lockstep(tmp_path):
    """Factory of started lock-step Backends (test hooks + wire log); every one is shut down at teardown."""
    import harness  # noqa: PLC0415

    made = []

    def make(**kw):
        kw.setdefault("recordings_root", str(tmp_path / "rec"))
        be = harness.lockstep_backend(**kw)
        made.append(be)
        return be

    yield make
    for be in made:
        try:
            be.shutdown()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def vbe(lockstep):
    """A lock-step Backend connected to the in-process simulator (default scenario), streaming."""
    return lockstep()


@pytest.fixture(scope="session")
def pdict():
    """The parameter dictionary loaded from params.yaml by the Integrator's loader (oracle, never params_gen)."""
    import gen_params  # noqa: PLC0415

    return gen_params.load()


@pytest.fixture(scope="session")
def rc():
    import ref_codec  # noqa: PLC0415

    return ref_codec
