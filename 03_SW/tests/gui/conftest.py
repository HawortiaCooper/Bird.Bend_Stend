"""Fixtures for the GUI tests (pytest-qt, offscreen; owner Implementer D, SW_design_GUI §10.1).

* ``QT_QPA_PLATFORM=offscreen`` is set **before** any Qt import; native dialogs are disabled as ``gui.app.run``
  does before the QApplication exists.
* Every test under ``tests/gui`` gets the ``gui`` marker and must carry ``@pytest.mark.req(...)`` (checked here,
  same rule as B's ``tests/unit``).
* GC policy: a Qt test runs under the GUI-thread GC policy (no Qt object finalised by another thread, SWD-PM3-07).
* B's root ``conftest.py`` applies the D-06 port guard to every test here.
"""
from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

from bend_stand.gui.dialogs import safe_dialog  # noqa: E402

safe_dialog.disable_native_dialogs()

from fakes import FakeBackend, tick  # noqa: E402


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    missing = []
    for item in items:
        if _HERE in Path(str(item.path)).resolve().parents:
            item.add_marker(pytest.mark.gui)
            if item.get_closest_marker("req") is None:
                missing.append(item.nodeid)
    if missing:
        raise pytest.UsageError("GUI tests without @pytest.mark.req: " + ", ".join(missing))


@pytest.fixture(autouse=True)
def _isolated_gui_state(tmp_path, monkeypatch):
    """Per test: own settings file, no file-dialog hook, STOP handler and NO-SPECIMEN state restored."""
    from bend_stand.gui import stop as stop_mod
    from bend_stand.gui.mode_state import no_specimen

    monkeypatch.setenv("BEND_STAND_GUI_SETTINGS", str(tmp_path / "gui.ini"))
    # B5-10/11: calibration, session and preset files of a real Backend go to a per-test data root (never %APPDATA%)
    monkeypatch.setenv("BEND_STAND_DATA_DIR", str(tmp_path / "data"))
    prev = stop_mod.stop_handler()
    yield
    safe_dialog.FILE_DIALOG_HOOK[0] = None
    stop_mod.set_stop_handler(prev)
    try:
        no_specimen().set(False)
    except RuntimeError:
        pass
    try:
        from bend_stand.gui.units_state import force_unit
        force_unit().set("N")
    except RuntimeError:
        pass


@pytest.fixture(autouse=True)
def _gui_thread_gc(request):
    policy = None
    if "qtbot" in request.fixturenames:
        request.getfixturevalue("qapp")
        from bend_stand.gui.gc_policy import GuiGcPolicy

        policy = GuiGcPolicy(freeze=False, initial_collect=False)
        policy.install()
    try:
        yield
    finally:
        if policy is not None:
            policy.uninstall()
            policy.deleteLater()
        else:
            gc.collect()


@pytest.fixture
def fake() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def connected_fake(fake: FakeBackend) -> FakeBackend:
    fake.start()
    fake.connect_async("sim")
    return fake


@pytest.fixture
def make_window(qtbot, tmp_path):
    from bend_stand.gui.main_window import MainWindow
    from bend_stand.gui.settings import make_settings

    created = []

    def _make(backend, show: bool = True):
        win = MainWindow(backend, settings=make_settings(tmp_path / "mw.ini"), start_refresh=False)
        qtbot.addWidget(win)
        created.append(win)
        if show:
            win.show()
            qtbot.waitExposed(win)
        return win

    yield _make
    for w in created:
        try:
            w._force_close = True
            w.close()
        except RuntimeError:
            pass


@pytest.fixture
def window(make_window, connected_fake):
    win = make_window(connected_fake)
    tick(win)
    return win
