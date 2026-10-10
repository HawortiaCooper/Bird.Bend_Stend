"""D-54 c (SRS SW-ACQ-002): recordings-folder picker on the Report tab — [Change…] next to the read-only folder line
calls B's ``reports.set_root(path)``; refused while recording (button disabled in advance); a refusal (e.g. folder not
writable) is shown in a warning box and as a message; the choice persists with the session (backend).

Verifies: SW-ACQ-002, SW-STOP-001 (folder chooser carries STOP)
"""
from __future__ import annotations

import pytest
from fakes import refuse, tick

from bend_stand.core.api import RecordingStatus
from bend_stand.gui.dialogs import safe_dialog
from bend_stand.gui.widgets.stop_button import StopButton


@pytest.fixture
def rep(window):
    window.tabs.setCurrentWidget(window.report_tab)
    tick(window)
    return window.report_tab


@pytest.mark.req("SW-ACQ-002")
def test_change_root_success(rep, window, connected_fake, tmp_path) -> None:
    """Verifies: SW-ACQ-002 — [Change…] → folder chooser (hook "dir") → set_root(path); the folder line shows the new
    folder, the list is re-read, a message confirms."""
    new = tmp_path / "recs2"
    asked: list[tuple] = []
    safe_dialog.FILE_DIALOG_HOOK[0] = lambda kind, caption, flt: (asked.append((kind, caption)), str(new))[1]
    rep.root_change_button.click()
    assert asked == [("dir", "Recordings folder")]
    assert connected_fake.calls_of("reports.set_root")[-1].args == (str(new),)
    assert rep.root_edit.text() == str(new) and rep.root_edit.isReadOnly()
    assert connected_fake.calls_of("reports.list_recordings")
    assert "Recordings folder:" in window.toast_label.text()


@pytest.mark.req("SW-ACQ-002")
def test_change_root_refused_shown_clearly(rep, window, connected_fake, tmp_path) -> None:
    """Verifies: SW-ACQ-002 — a refusal (not writable) leaves the folder unchanged and is shown in a warning box with
    the path and B's text, plus a message; cancelling the chooser does nothing."""
    before = rep.root_edit.text()
    connected_fake.reports.set_root_result = refuse("FOLDER_NOT_WRITABLE",
                                                    "recordings folder not writable: [Errno 13] Permission denied")
    assert rep.change_root(str(tmp_path / "ro")) is False
    assert rep.root_edit.text() == before
    box = rep.root_box
    assert box is not None and box.isVisible() and "Permission denied" in box.text() and str(tmp_path / "ro") in box.text()
    assert "Recordings folder not changed" in window.toast_label.text()
    box.close()
    n = len(connected_fake.calls_of("reports.set_root"))
    safe_dialog.FILE_DIALOG_HOOK[0] = lambda *a: ""
    assert rep.change_root() is False and len(connected_fake.calls_of("reports.set_root")) == n


@pytest.mark.req("SW-ACQ-002")
def test_change_disabled_while_recording(rep, window, connected_fake) -> None:
    """Verifies: SW-ACQ-002 — while recording [Change…] is disabled with the reason; after the stop enabled again."""
    connected_fake.set_status(recording=RecordingStatus("RECORDING", "C:/x", 10))
    tick(window)
    assert not rep.root_change_button.isEnabled() and "Stop the recording" in rep.root_change_button.toolTip()
    connected_fake.set_status(recording=RecordingStatus())
    tick(window)
    assert rep.root_change_button.isEnabled()


@pytest.mark.req("SW-STOP-001")
def test_folder_chooser_has_stop(qapp) -> None:
    """Verifies: SW-STOP-001 — the folder chooser is the non-native SafeFileDialog with STOP."""
    dlg = safe_dialog.SafeFileDialog(None, "Recordings folder", "", "")
    try:
        assert dlg.findChildren(StopButton)
    finally:
        dlg.deleteLater()


@pytest.mark.req("SW-ACQ-002")
def test_change_root_on_real_backend(make_window, tmp_path) -> None:
    """Verifies: SW-ACQ-002 on B's backend + simulator: a writable folder is taken (folder line, session), a path that
    is a file is refused with "not writable" and the folder stays."""
    backend_mod = pytest.importorskip("bend_stand.core.backend")
    from test_sim_manual import step
    be = backend_mod.Backend(backend_mod.BackendSettings(clock="lockstep", test_hooks=True, hotkey="off",
                                                         sim_nvm_path=str(tmp_path / "nvm.json")))
    be.start()
    try:
        be.test_hooks.result(be.connect_async("sim"), 5000)
        win = make_window(be)
        step(win, be, 50)
        tab = win.report_tab
        win.tabs.setCurrentWidget(tab)
        step(win, be)
        good = tmp_path / "chosen"
        assert tab.change_root(str(good)) is True
        assert tab.root_edit.text() == str(good.resolve()) == be.reports.root()
        assert be.session.get().recordings_root == str(good.resolve())
        afile = tmp_path / "a_file.txt"
        afile.write_text("x", encoding="utf-8")
        assert tab.change_root(str(afile)) is False
        assert "not writable" in tab.root_box.text() and tab.root_edit.text() == str(good.resolve())
        tab.root_box.close()
    finally:
        be.shutdown()
