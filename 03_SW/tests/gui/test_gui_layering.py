"""G-01 / G-01b: GUI layering, topic mapping, threading of the bridge, origin notes (SW_design_GUI §8, §9).

Verifies: SW-PLT-002, SYS-010, NFR-004 (GUI part: T1–T3)
"""
from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest

import bend_stand
from bend_stand.core.api import TOPICS, EventRecord

GUI = Path(bend_stand.__file__).resolve().parent / "gui"
ALLOWED_BEND = ("bend_stand.core.api", "bend_stand.core.protocol_gen", "bend_stand.calc.units", "bend_stand.gui")
THIRD_PARTY = {"PySide6", "shiboken6", "pyqtgraph", "numpy"}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path.name}: relative import"
            mod = node.module or ""
            if mod in ("bend_stand", "bend_stand.core", "bend_stand.calc"):   # "from bend_stand.core import x"
                out += [f"{mod}.{a.name}" if mod != "bend_stand" or a.name in ("core", "calc", "io", "gui")
                        else mod for a in node.names]
            else:
                out.append(mod)
    return out


@pytest.mark.req("SW-PLT-002")
def test_gui_imports_only_api_name_tables_and_units() -> None:
    """Verifies: SW-PLT-002 (G-01) — gui imports only core.api, core.protocol_gen, calc.units (+ package root)."""
    import sys
    std = set(sys.stdlib_module_names) | {"__future__"}
    bad = []
    files = sorted(GUI.rglob("*.py"))
    assert len(files) >= 30
    for p in files:
        for m in _imports(p):
            top = m.split(".")[0]
            if m.startswith("bend_stand"):
                if m == "bend_stand" or any(m == a or m.startswith(a + ".") for a in ALLOWED_BEND):
                    continue
                bad.append((p.name, m))
            elif top not in std and top not in THIRD_PARTY:
                bad.append((p.name, m))
    assert not bad, bad


@pytest.mark.req("SW-PLT-002")
def test_gui_never_imports_serial_or_io() -> None:
    """Verifies: SW-PLT-002 — no serial / io / backend internals in the GUI (D-06 by construction)."""
    for p in GUI.rglob("*.py"):
        for m in _imports(p):
            assert not m.startswith(("serial", "bend_stand.io", "bend_stand.core.backend")), (p.name, m)


@pytest.mark.req("SW-PLT-002")
def test_every_topic_mapped_or_ignored() -> None:
    """Verifies: SW-PLT-002 (G-01b) — every topic of B §15.3 is mapped to a bridge signal or ignored."""
    from bend_stand.gui.bridge import IGNORED_TOPICS, KNOWN_TOPICS, TOPIC_SIGNALS, TOPICS_PENDING_GUI, QtBridge
    for t in KNOWN_TOPICS:                              # TOPICS + B's topics pending a GUI decision (B6-33)
        assert (t in TOPIC_SIGNALS) != (t in IGNORED_TOPICS), t
    for name in set(TOPIC_SIGNALS.values()):
        assert hasattr(QtBridge, name), name
    assert set(TOPIC_SIGNALS) <= set(TOPICS) | set(TOPICS_PENDING_GUI)
    assert "device.board_changed" in TOPIC_SIGNALS      # mapped (B moves it into api.TOPICS)


@pytest.mark.req("SW-PLT-002", "NFR-004")
def test_bridge_subscribes_all_topics_and_unsubscribes(qtbot, fake) -> None:
    """Verifies: SW-PLT-002, NFR-004 — bound-method subscriptions for every mapped topic; shutdown removes them."""
    from bend_stand.gui.bridge import TOPIC_SIGNALS, QtBridge
    b = QtBridge(fake)
    assert set(fake.events.topics()) == set(TOPIC_SIGNALS)
    b.shutdown()
    assert fake.events.topics() == []


@pytest.mark.req("NFR-004")
def test_bridge_delivers_backend_thread_events_in_gui_thread(qtbot, fake) -> None:
    """Verifies: NFR-004 (T1/T2) — a callback on a backend thread only emits; the slot runs in the GUI thread."""
    from bend_stand.gui.bridge import QtBridge
    b = QtBridge(fake)
    seen: list[tuple[str, int]] = []
    b.fwEvent.connect(lambda rec: seen.append((rec.topic, threading.get_ident())))
    th = threading.Thread(target=fake.events.emit, args=("fw.event", None))
    th.start()
    th.join()
    qtbot.waitUntil(lambda: bool(seen), timeout=2000)
    assert seen == [("fw.event", threading.get_ident())]
    b.shutdown()


@pytest.mark.req("NFR-004")
def test_bridge_watch_runs_callbacks_later_in_gui_thread(qtbot, fake) -> None:
    """Verifies: NFR-004 (T3) — future completions from a worker thread reach on_ok in the GUI thread, never
    synchronously inside watch()."""
    from concurrent.futures import Future

    from bend_stand.gui.bridge import QtBridge
    b = QtBridge(fake)
    got: list[tuple[object, int]] = []
    fut: Future = Future()
    b.watch(fut, lambda r: got.append((r, threading.get_ident())), None, "t")
    threading.Thread(target=fut.set_result, args=(42,)).start()
    qtbot.waitUntil(lambda: bool(got), timeout=2000)
    assert got == [(42, threading.get_ident())]
    done_now: Future = Future()
    done_now.set_result(1)
    flag: list[int] = []
    b.watch(done_now, flag.append)
    assert flag == []                       # never synchronous
    qtbot.waitUntil(lambda: flag == [1], timeout=2000)
    errs: list[BaseException] = []
    bad: Future = Future()
    bad.set_exception(RuntimeError("x"))
    b.watch(bad, None, errs.append)
    qtbot.waitUntil(lambda: len(errs) == 1, timeout=2000)
    b.shutdown()


@pytest.mark.req("NFR-004")
def test_bridge_shutdown_drops_late_events(qtbot, fake) -> None:
    """Verifies: NFR-004 — after shutdown no event reaches the GUI."""
    from bend_stand.gui.bridge import QtBridge
    b = QtBridge(fake)
    seen = []
    b.anyEvent.connect(seen.append)
    b.shutdown()
    b._on_event(EventRecord("fw.event", 0, None))
    qtbot.wait(20)
    assert seen == []


@pytest.mark.req("SYS-010")
def test_copied_files_carry_origin_note_with_hash() -> None:
    """Verifies: SYS-010 — every file copied from Thrust_Stand names the source path and commit hash (D-29 m)."""
    copied = []
    for p in GUI.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "Origin: Thrust_Stand_HAW/" in text:
            copied.append(p.name)
            line = text[text.index("Origin: Thrust_Stand_HAW/"):].split("\n")[0]
            assert " @" in line and len(line.split(" @")[1].split()[0]) >= 7, (p.name, line)
    for must in ("stop.py", "stop_button.py", "safe_dialog.py", "gc_policy.py", "bridge.py", "param_form.py",
                 "channel_tree.py", "app.py", "status_led.py"):
        assert must in copied, must
