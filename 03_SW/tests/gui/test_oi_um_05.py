"""OI-UM-05 (Orchestrator follow-up to the operator manual): (a) the first-use banner separates the refusal text from
its clear hint; (b) ● Record cannot start a recording without a link (the backend gate only WARNs "stream off"), a
running recording can always be stopped; (c) the Report tab shows the recordings folder read-only with
[Open recordings folder] (QDesktopServices; no control that changes the folder).

Verifies: SAF-SW-001 (first-use banner with its clear procedure), SAF-SW-005, SW-ACQ-002 (record start needs the
link; recordings folder shown), SW-REP-001
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest
from fakes import refuse, tick
from PySide6.QtCore import Qt

from bend_stand.core.api import GateId, GateItem, LinkState, LinkStatus, RecordingStatus, Severity
from bend_stand.gui.tabs import report_tab as rt
from bend_stand.gui.widgets.stop_banner import with_hint

HINT = "Calibrate + Tare, or enter the no-specimen mode"
TEXT = "PC load limits enabled without a valid input: no load calibration, no tare in this session"


# --------------------------------------------------------------------------------------------- (a) banner
@pytest.mark.req("SAF-SW-001", "SAF-SW-005")
def test_with_hint_separator_pure() -> None:
    """Verifies: SAF-SW-001 — "<text> – <hint>"; a trailing period is not doubled; no hint → text alone."""
    item = GateItem("LOAD_INPUT_INVALID", Severity.REFUSE, TEXT, HINT)
    assert with_hint(TEXT, item) == f"{TEXT} – {HINT}"
    assert with_hint(TEXT + ".", item) == f"{TEXT} – {HINT}"
    assert with_hint(TEXT, GateItem("X", Severity.REFUSE, TEXT, None)) == TEXT


@pytest.mark.req("SAF-SW-001", "SAF-SW-005")
def test_first_use_banner_has_separator(window, connected_fake) -> None:
    """Verifies: SAF-SW-001 — the first-use stop banner shows the backend text and hint with a visible separator."""
    connected_fake.set_gate(GateId.MOVE, refuse("LOAD_INPUT_INVALID", TEXT, HINT))
    tick(window)
    assert window.stop_banner.top_text() == f"{TEXT} – {HINT}"
    assert window.stop_banner.action_button.text() == "Enter no-specimen mode…"


# --------------------------------------------------------------------------------------------- (b) Record
@pytest.mark.req("SW-ACQ-002")
def test_record_start_disabled_without_link(window, connected_fake) -> None:
    """Verifies: SW-ACQ-002 — disconnected / connecting / lost: ● Record disabled with the reason; a forced click
    sends nothing; connected: enabled again."""
    for state in (LinkState.DISCONNECTED, LinkState.CONNECTING, LinkState.LOST):
        connected_fake.set_status(link=LinkStatus(state))
        tick(window)
        assert not window.record_button.isEnabled(), state
        assert "not connected" in window.record_button.toolTip(), state
    n = len(connected_fake.calls)
    window.on_record()                                     # e.g. a stale click: still nothing is sent
    assert "record_start" not in connected_fake.call_names()[n:]
    assert "Record start refused: not connected" in window.toast_label.text()
    connected_fake.set_status(link=LinkStatus(LinkState.CONNECTED, "", "sim"))
    tick(window)
    assert window.record_button.isEnabled()


@pytest.mark.req("SW-ACQ-002")
def test_record_stop_stays_possible_after_link_loss(window, connected_fake) -> None:
    """Verifies: SW-ACQ-002 — a running recording can be stopped while the link is lost."""
    connected_fake.set_status(recording=RecordingStatus("RECORDING", "C:/x", 10), link=LinkStatus(LinkState.LOST))
    tick(window)
    assert window.record_button.isChecked() and window.record_button.isEnabled()
    window.record_button.click()
    assert connected_fake.call_names()[-1] == "record_stop"


# --------------------------------------------------------------------------------------------- (c) Report tab
@pytest.mark.req("SW-ACQ-002", "SW-REP-001")
def test_report_tab_shows_recordings_folder_read_only(window, connected_fake, tmp_path, qtbot) -> None:
    """Verifies: SW-ACQ-002 — the folder is shown read-only (session value), [Open recordings folder] opens it
    through the URL seam; a missing folder gives an info message and is not created."""
    root = tmp_path / "recs"
    root.mkdir()
    connected_fake.session.set(dataclasses.replace(connected_fake.session.get(), recordings_root=str(root)))
    tab = window.report_tab
    window.tabs.setCurrentWidget(tab)
    tab.refresh_list()
    assert tab.root_edit.text() == str(root) and tab.root_edit.isReadOnly()
    assert tab.root_button.text() == "Open recordings folder"
    opened: list[str] = []
    rt.OPEN_URL_HOOK[0] = opened.append                    # reset by the conftest
    qtbot.mouseClick(tab.root_button, Qt.MouseButton.LeftButton)
    assert opened == [str(root)]
    missing = tmp_path / "not_there"
    connected_fake.session.set(dataclasses.replace(connected_fake.session.get(), recordings_root=str(missing)))
    msgs: list[tuple[str, str]] = []
    tab.message.connect(lambda t, sev: msgs.append((t, sev)))
    qtbot.mouseClick(tab.root_button, Qt.MouseButton.LeftButton)
    assert opened == [str(root)] and not missing.exists()
    assert msgs and "does not exist yet" in msgs[-1][0]


@pytest.mark.req("SW-ACQ-002")
def test_recordings_root_precedence_and_default() -> None:
    """Verifies: SW-ACQ-002 — reports.root() > settings override > session > default; the GUI default mirrors
    ``core.paths.default_recordings_root`` (layering rule: the GUI may not import core.paths)."""
    from bend_stand.core.paths import default_recordings_root as core_default

    class Obj:
        def __init__(self, **kw) -> None:
            self.__dict__.update(kw)

    sess = Obj(get=lambda: Obj(recordings_root=None))
    assert Path(rt.recordings_root_of(Obj(session=sess))) == core_default() == rt.default_recordings_root()
    sess2 = Obj(get=lambda: Obj(recordings_root="D:/session"))
    assert rt.recordings_root_of(Obj(session=sess2)) == "D:/session"
    assert rt.recordings_root_of(Obj(session=sess2, settings=Obj(recordings_root="E:/override"))) == "E:/override"
    assert rt.recordings_root_of(Obj(session=sess2, reports=Obj(root=lambda: "F:/public"))) == "F:/public"
    assert os.path.basename(rt.recordings_root_of(Obj())) == "recordings"           # no session at all: default


@pytest.mark.req("SW-ACQ-002")
def test_report_tab_uses_backend_root_first(window, connected_fake, tmp_path) -> None:
    """Verifies: SW-ACQ-002 — the folder shown is B's ``reports.root()`` (GRQ-B-31 b), not the session value; when
    ``root()`` fails the local lookup is the fallback."""
    connected_fake.session.set(dataclasses.replace(connected_fake.session.get(), recordings_root=str(tmp_path / "s")))
    connected_fake.reports.root_path = str(tmp_path / "from_backend")
    tab = window.report_tab
    tab.refresh_list()
    assert tab.root_edit.text() == str(tmp_path / "from_backend")

    def broken() -> str:
        raise RuntimeError("not available")
    connected_fake.reports.root = broken
    tab.refresh_list()
    assert tab.root_edit.text() == str(tmp_path / "s")
