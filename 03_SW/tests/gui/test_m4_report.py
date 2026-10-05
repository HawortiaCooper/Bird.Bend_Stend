"""M4 GUI Report tab (SW_design_GUI §3.7; G-32): recordings list, step result table, build with re-applied
calibration / tare and 3-point bend, open the HTML in the system browser.

Verifies: SW-REP-001, SW-REP-002, SW-REP-003, SW-REP-004, SW-ACQ-002
"""
from __future__ import annotations

import pytest
from fakes import tick
from PySide6.QtWidgets import QApplication

from bend_stand.core import model as cm
from bend_stand.core.api import Bend3pGeometry, EventRecord
from bend_stand.gui.dialogs import safe_dialog
from bend_stand.gui.tabs import report_tab as rt
from bend_stand.gui.units_state import force_unit

NAN = float("nan")


def _rec(folder, status="COMPLETE", seq="staircase 0-200 N", has=True):
    return cm.RecordingInfo(folder, "2026-10-05T14:05:12Z", "Wing bracket PA12 #17", seq, status, 552.0, has)


def _res(folder):
    r1 = cm.StepResult(1, 1, "u2", "100 N", "load", (1,), 100.0, "N", 2.0, ("ON_TARGET",), 400, 400.0, 99.12, 0.21,
                       98.7, 99.5, 0.03, 0.02, 2.103)
    r2 = cm.StepResult(2, 2, "u3", "200 N", "load", (2,), 200.0, "N", 2.0, ("NOT_REACHED",), 0, f_end_n=167.4,
                       x_end_mm=289.5)
    r3 = cm.StepResult(1, 1, "u2", "100 N", "load", (2,), 100.0, "N", 2.0, ("INCOMPLETE", "ON_TARGET"), 311,
                       f_mean_n=98.7)
    return cm.ReportResult(folder, "2026-10-05T14:15:00Z", {"element": "Wing bracket", "number": 17},
                           ("LOW_SPAN", "1 step NOT_REACHED"), (r1, r3, r2), calibration={"status": "PASS",
                                                                                          "low_span": True},
                           tare_raw=125012.0)


@pytest.fixture
def rep(window, connected_fake, tmp_path):
    a, b = str(tmp_path / "rec_17"), str(tmp_path / "rec_16")
    connected_fake.reports.recordings = [_rec(a), _rec(b, "PARTIAL", "(manual)", False)]
    connected_fake.reports.results = {a: _res(a)}
    window.tabs.setCurrentWidget(window.report_tab)
    tick(window)
    tab = window.report_tab
    tab.refresh_list()
    return tab, a, b


@pytest.mark.req("SW-ACQ-002", "SW-REP-002")
def test_recordings_list_and_step_results(rep, connected_fake) -> None:
    """Verifies: SW-ACQ-002, SW-REP-002 — recordings newest first with status / sequence / report flag; selecting
    one shows marks, calibration, warnings and the read-only step result table (flags verbatim, red ones red)."""
    tab, a, _b = rep
    assert tab.rec_table.rowCount() == 2
    assert tab.rec_table.item(1, 3).text() == "PARTIAL" and tab.rec_table.item(0, 5).text() == "yes"
    assert tab.select_folder(a)
    assert "Wing bracket" in tab.sel_label.text() and "LOW_SPAN" in tab.sel_label.text()
    assert "1 step NOT_REACHED" in tab.warn_label.text()
    t = tab.result_table
    assert t.rowCount() == 3
    heads = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    col = {h.split(" [")[0] if "[" in h else h: i for i, h in enumerate(heads)}
    assert t.item(0, 0).text() == "2" and t.item(0, 1).text() == "1" and t.item(0, 3).text() == "400"
    assert t.item(0, heads.index("F [N] mean")).text() == "99.12"
    flags = heads.index("Flags")
    assert t.item(2, flags).text() == "NOT_REACHED"
    assert t.item(2, flags).foreground().color().name() == "#c00000"
    assert t.item(1, flags).text() == "INCOMPLETE, ON_TARGET"
    assert t.item(0, flags).foreground().color().name() != "#c00000"
    assert not t.editTriggers()
    _ = col


@pytest.mark.req("SW-REP-002")
def test_result_table_follows_display_unit(rep) -> None:
    """Verifies: SW-REP-002, SYS-003 — force columns re-expressed in kgf."""
    tab, a, _b = rep
    tab.select_folder(a)
    force_unit().set("kgf")
    heads = [tab.result_table.horizontalHeaderItem(c).text() for c in range(tab.result_table.columnCount())]
    v = tab.result_table.item(0, heads.index("F [kgf] mean")).text()
    assert float(v) == pytest.approx(99.12 / 9.80665, abs=1e-3)


@pytest.mark.req("SW-REP-001", "SW-REP-003", "SW-REP-004")
def test_build_with_reapplied_cal_tare_and_bend(rep, connected_fake, window, tmp_path) -> None:
    """Verifies: SW-REP-001, SW-REP-003, SW-REP-004 — [Build report] → ``reports.build_async(dir, cal=<file>,
    tare=<raw>, bend3p=Bend3pGeometry)``; unticked 3-point bend = False (off); the paths are listed."""
    tab, a, _b = rep
    tab.select_folder(a)
    tab.build()
    QApplication.processEvents()
    c = connected_fake.calls_of("reports.build_async")[-1]
    assert c.args == (a,) and c.kwargs == {"cal": None, "tare": None, "bend3p": False}
    cal = str(tmp_path / "load_cal.json")
    safe_dialog.FILE_DIALOG_HOOK[0] = lambda kind, cap, flt: cal
    tab._choose_cal()
    tab.tare_box.setChecked(True)
    tab.tare_spin.setValue(125100.0)
    tab.bend_box.setChecked(True)
    for f, v in (("span_mm", 60.0), ("width_mm", 10.0), ("thickness_mm", 2.0)):
        tab.bend_spins[f].setValue(v)
    tab.build_button.click()
    for _ in range(5):
        QApplication.processEvents()
    c = connected_fake.calls_of("reports.build_async")[-1]
    assert c.kwargs == {"cal": cal, "tare": 125100.0, "bend3p": Bend3pGeometry(60.0, 10.0, 2.0)}
    assert "report.html" in tab.paths_label.text() and tab.paths["html"].endswith("report.html")


@pytest.mark.req("SW-REP-001")
def test_open_html_in_system_browser(rep, connected_fake, monkeypatch) -> None:
    """Verifies: SW-REP-001 (GQ-13) — [Open HTML] hands the report path to the system browser
    (``QDesktopServices.openUrl``; test seam); without a report a hint is shown."""
    tab, a, b = rep
    opened: list[str] = []
    rt.OPEN_URL_HOOK[0] = opened.append            # reset by the conftest after the test
    tab.select_folder(a)
    tab.build()
    for _ in range(5):
        QApplication.processEvents()
    tab.html_button.click()
    assert opened == [a + "/report.html"]
    tab.folder_button.click()
    assert opened[-1] == a
    msgs: list[tuple[str, str]] = []
    tab.message.connect(lambda t, s: msgs.append((t, s)))
    tab.select_folder(b)
    tab.html_button.click()
    assert len(opened) == 2 and msgs and "build the report first" in msgs[-1][0]
    assert "no report yet" in tab.sel_label.text()


@pytest.mark.req("SW-REP-001")
def test_build_failure_and_report_ready_refresh(rep, connected_fake) -> None:
    """Verifies: SW-REP-001 — a failing build is shown as an error; topic ``report.ready`` refreshes the list."""
    tab, a, _b = rep
    msgs: list[tuple[str, str]] = []
    tab.message.connect(lambda t, s: msgs.append((t, s)))
    tab.select_folder(a)
    connected_fake.reports.build_error = OSError("disk full")
    tab.build()
    for _ in range(5):
        QApplication.processEvents()
    assert msgs[-1][1] == "error" and "disk full" in msgs[-1][0] and tab.build_button.isEnabled()
    n = len(connected_fake.calls_of("reports.list_recordings"))
    connected_fake.events.emit("report.ready", None)
    for _ in range(3):
        QApplication.processEvents()
    assert len(connected_fake.calls_of("reports.list_recordings")) == n + 1
    _ = EventRecord, NAN
