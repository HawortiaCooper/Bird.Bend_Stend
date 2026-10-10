"""M4 GUI (SW_design_GUI §3.6, §6.5, §15.4): MC3-5 trip toasts, Sequence tab editor / loops / files / generator
wizard / run controls / run line, sequence chart. Fake backend whose sequencer uses B's real pure sequencer code
(model + validation, plan, planned path, generators, sequence files) and a scripted executor (G-29…G-31).

Verifies: SAF-SW-005, SAF-SW-001, SW-SEQ-001, SW-SEQ-002, SW-SEQ-005, SW-SEQ-007, SW-STOP-004, SW-WIZ-001,
SW-WIZ-002, SW-SEQF-001, SW-SCH-001, SW-SCH-002, SAF-SW-004, SW-STOP-001
"""
from __future__ import annotations

import json
import math
import time

import pytest
from fakes import confirm, refuse, tick, warn
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication

from bend_stand.core import model as cm
from bend_stand.core.api import EventRecord, GateId, StopResult
from bend_stand.core.sequencer import model as M
from bend_stand.core.sequencer import plan as P
from bend_stand.gui import seq_access as sa
from bend_stand.gui.dialogs import safe_dialog
from bend_stand.gui.main_window import trip_announcement, trip_key
from bend_stand.gui.models.step_table_model import NA_CELL
from bend_stand.gui.units_state import force_unit
from bend_stand.gui.widgets.stop_button import StopButton


@pytest.fixture
def seq_tab(window):
    window.tabs.setCurrentWidget(window.sequence_tab)
    tick(window)
    yield window.sequence_tab
    for dlg in list(window.sequence_tab.dialogs.values()):     # e.g. a generator dialog left open by a test
        try:
            dlg.close()
        except RuntimeError:                                    # already deleted (WA_DeleteOnClose)
            pass


def _toasts(win, monkeypatch):
    out: list[tuple[str, str]] = []
    monkeypatch.setattr(win, "toast", lambda text, severity="info": out.append((text, severity)))
    # the tabs were connected to the bound method at construction: reconnect to the recorder
    for tab in (win.sequence_tab, win.report_tab):
        tab.message.disconnect()
        tab.message.connect(lambda t, s="info": out.append((t, s)))
    return out


def _cell(tab, row, field):
    m = tab.model
    return m.index(row, m.column_of(field))


def _set(tab, row, field, text):
    return tab.model.setData(_cell(tab, row, field), text, Qt.ItemDataRole.EditRole)


def _build(tab, *kinds):
    for k in kinds:
        tab.table.clearSelection()
        assert tab.add_step(k)
    tab.revalidate()


# ===================================================================================== MC3-5 (SWD-M3-02)
@pytest.mark.req("SAF-SW-005", "SAF-SW-001")
def test_trip_announcement_new_trip_vs_clear() -> None:
    """Verifies: SAF-SW-005, SAF-SW-001 (MC3-5) — only a new trip is announced as an error "STOP sent"; the
    remaining-latch publish (B5-25), None and B's SwTripCleared are info "SW limit cleared"."""
    ann: dict = {}
    pull = cm.SwTrip("PULL", 1, 160.0, 150.0, "N", 1000, "pull 150 N: F = 160 N")
    tmax = cm.SwTrip("TRAVEL_MAX", 1, 50.1, 50.0, "mm", 2000, "travel max 50 mm")
    sev, text = trip_announcement(pull, ann)
    assert sev == "error" and "STOP sent" in text and trip_key(pull) in ann
    assert trip_announcement(tmax, ann)[0] == "error"
    sev, text = trip_announcement(pull, ann)                       # PULL republished as the remaining latch
    assert sev == "info" and "STOP sent" not in text and "trip:" not in text.lower()
    sev, text = trip_announcement(None, ann)
    assert (sev, text) == ("info", "SW limit cleared") and not ann
    trip_announcement(pull, ann)
    clr = cm.SwTripCleared("PULL", (), None, 3000) if hasattr(cm, "SwTripCleared") else None
    sev, text = trip_announcement(clr, ann, cleared=True)
    assert sev == "info" and "STOP sent" not in text and not ann


@pytest.mark.req("SAF-SW-005", "SAF-SW-001")
def test_trip_clear_toast_in_window(window, connected_fake, monkeypatch) -> None:
    """Verifies: SAF-SW-005 (MC3-5) — window toasts: trip → error with "STOP sent"; clear (None / clear topic) →
    info "SW limit cleared"; the event-log row of a clear is info."""
    toasts = _toasts(window, monkeypatch)
    trip = cm.SwTrip("PULL", 1, 160.0, 150.0, "N", 1000, "pull 150 N: F = 160 N")
    window._on_safety_event(EventRecord("safety.trip", 1, trip))
    window._on_safety_event(EventRecord("safety.trip", 2, None))
    window._on_safety_event(EventRecord("safety.trip", 3, trip))
    window._on_safety_event(EventRecord("safety.trip_cleared", 4, cm.SwTripCleared("PULL", (), None, 5)
                                        if hasattr(cm, "SwTripCleared") else None))
    assert toasts[0][1] == "error" and "STOP sent" in toasts[0][0]
    assert toasts[1] == ("SW limit cleared", "info")
    assert toasts[2][1] == "error"
    assert toasts[3][1] == "info" and "STOP sent" not in toasts[3][0]
    from bend_stand.gui.widgets.event_log import format_record
    assert format_record(EventRecord("safety.trip", 1, None)).severity == "info"


@pytest.mark.req("SAF-SW-005")
def test_trip_latched_before_window_is_not_announced_again(make_window, connected_fake, monkeypatch) -> None:
    """Verifies: SAF-SW-005 (MC3-5) — a trip latched before the window existed is seeded from
    ``status().safety.trips``; its later remaining-latch publish is not announced as a new STOP."""
    import dataclasses
    trip = cm.SwTrip("PULL", 1, 160.0, 150.0, "N", 1000, "pull")
    st = connected_fake.status()
    connected_fake.set_status(safety=dataclasses.replace(st.safety, sw_trip="PULL", trip=trip, trips=(trip,)))
    win = make_window(connected_fake)
    tick(win)
    toasts = _toasts(win, monkeypatch)
    win._on_safety_event(EventRecord("safety.trip", 1, trip))
    assert toasts and toasts[0][1] == "info"


# ===================================================================================== editor (G-29)
@pytest.mark.req("SW-SEQ-001", "SW-STOP-001")
def test_sequence_tab_present_with_stop(window, seq_tab) -> None:
    """Verifies: SW-SEQ-001, SW-STOP-001 — the Sequence tab replaces the M1 placeholder; the backend's new
    sequence is loaded; a STOP button on the tab calls backend.stop synchronously."""
    assert window.tabs.tabText(window.tabs.indexOf(seq_tab)) == "Sequence"
    assert isinstance(seq_tab.seq, M.Sequence) and seq_tab.model.rowCount() == 0
    stops = seq_tab.findChildren(StopButton)
    assert len(stops) == 1 and stops[0].focusPolicy() == Qt.FocusPolicy.NoFocus
    n = len(window.backend.calls_of("stop"))
    stops[0].pressed.emit()
    assert len(window.backend.calls_of("stop")) == n + 1


@pytest.mark.req("SW-SEQ-001")
def test_typed_steps_and_applicability(seq_tab) -> None:
    """Verifies: SW-SEQ-001 — one typed step per kind; cells that do not apply show "–" and are read-only (LOAD has
    no step time, B3-07; no travel-bound column); the kind table matches the backend fields."""
    _build(seq_tab, *sa.STEP_KINDS)
    steps = seq_tab.seq.steps
    assert [str(s.kind) for s in steps] == list(sa.STEP_KINDS)
    headers = [h for _f, h in seq_tab.model.columns()]
    assert not any("bound" in h.lower() for h in headers)
    load_row = 1
    idx = _cell(seq_tab, load_row, "step_time_s")
    assert seq_tab.model.data(idx) == NA_CELL
    assert not seq_tab.model.flags(idx) & Qt.ItemFlag.ItemIsEditable
    assert seq_tab.model.flags(_cell(seq_tab, 0, "step_time_s")) & Qt.ItemFlag.ItemIsEditable
    fields = {f.name for f in __import__("dataclasses").fields(M.Step)}
    for kind, applies in sa.APPLIES.items():
        assert applies <= fields, kind


@pytest.mark.req("SW-SEQ-001")
def test_cell_edit_and_backend_validation(seq_tab) -> None:
    """Verifies: SW-SEQ-001 — edits are written to the model (empty speed = default; N / kgf conversion of load
    targets); the backend's validation issues colour their cells (tooltip = text) and drive the badge."""
    _build(seq_tab, "travel", "load")
    assert "error" in seq_tab.valid_badge.text()                      # load step without target (MISSING)
    bg = seq_tab.model.data(_cell(seq_tab, 1, "target"), Qt.ItemDataRole.BackgroundRole)
    assert bg is not None and "target required" in seq_tab.model.data(_cell(seq_tab, 1, "target"),
                                                                       Qt.ItemDataRole.ToolTipRole)
    assert _set(seq_tab, 0, "target", "2.5") and seq_tab.seq.steps[0].target == 2.5
    assert _set(seq_tab, 0, "speed_mm_s", "") and seq_tab.seq.steps[0].speed_mm_s is None
    assert seq_tab.model.data(_cell(seq_tab, 0, "speed_mm_s")) == "default"
    assert not _set(seq_tab, 0, "settle_s", "abc")                   # parse error: not applied
    force_unit().set("kgf")
    assert _set(seq_tab, 1, "target", "10")
    assert seq_tab.seq.steps[1].target == pytest.approx(98.0665)
    force_unit().set("N")
    assert _set(seq_tab, 0, "step_time_s", "-1")
    seq_tab.revalidate()
    assert "RANGE" not in seq_tab.valid_badge.text()
    tip = seq_tab.model.data(_cell(seq_tab, 0, "step_time_s"), Qt.ItemDataRole.ToolTipRole)
    assert tip and "ERROR" in tip
    assert _set(seq_tab, 0, "step_time_s", "0")
    seq_tab.revalidate()
    assert seq_tab.valid_badge.text().startswith("● valid")
    assert seq_tab.dirty and seq_tab.title_label.text().endswith("*")


@pytest.mark.req("SW-SEQ-001")
def test_kind_change_resets_target(seq_tab) -> None:
    """Verifies: SW-SEQ-001 — changing travel ↔ load clears the target (mm vs N)."""
    _build(seq_tab, "travel")
    _set(seq_tab, 0, "target", "3")
    assert _set(seq_tab, 0, "kind", "load")
    assert str(seq_tab.seq.steps[0].kind) == "load" and seq_tab.seq.steps[0].target is None


@pytest.mark.req("SW-SEQ-002")
def test_loops_wrap_unwrap_and_bookkeeping(seq_tab) -> None:
    """Verifies: SW-SEQ-002 — Loop… wraps the selection (count, 0 = until stopped), the row header shows the bracket;
    one nesting level is the backend's rule (deeper → validation ERROR shown); delete / insert keep loop indices."""
    _build(seq_tab, "travel", "travel", "travel", "mark")
    seq_tab.select_rows([1, 2])
    assert seq_tab.loop_selected(count=3)
    assert [(lp.first, lp.last, lp.count) for lp in seq_tab.seq.loops] == [(1, 2, 3)]
    hdr = [seq_tab.model.headerData(r, Qt.Orientation.Vertical) for r in range(4)]
    assert hdr[1].startswith("┌×3") and hdr[2].startswith("└")
    seq_tab.select_rows([0, 3])
    assert seq_tab.loop_selected(count=0)
    assert "∞" in seq_tab.model.headerData(0, Qt.Orientation.Vertical)
    seq_tab.revalidate()
    seq_tab.select_rows([1])
    assert seq_tab.unloop_selected()                                 # innermost (1…2) removed
    assert [(lp.first, lp.last, lp.count) for lp in seq_tab.seq.loops] == [(0, 3, 0)]
    seq_tab.undo()
    assert len(seq_tab.seq.loops) == 2
    seq_tab.select_rows([2])
    assert seq_tab.delete_selected()
    assert sorted((lp.first, lp.last) for lp in seq_tab.seq.loops) == [(0, 2), (1, 1)]
    # one nesting level only: a third level is flagged by the backend
    seq_tab.select_rows([1])
    seq_tab.loop_selected(count=2)
    seq_tab.revalidate()
    assert "nesting" in seq_tab.seq_issue_label.text() or "error" in seq_tab.valid_badge.text()


@pytest.mark.req("SW-SEQ-002")
def test_loop_dialog_count_and_stop(seq_tab, qtbot) -> None:
    """Verifies: SW-SEQ-002, SW-STOP-001 — the Loop dialog (count 0…10 000) has STOP and wraps on OK."""
    _build(seq_tab, "travel", "travel")
    seq_tab.select_rows([0, 1])
    seq_tab.loop_selected()
    dlg = seq_tab.dialogs["loop"]
    assert dlg.count.minimum() == 0 and dlg.count.maximum() == 10_000
    assert len(dlg.findChildren(StopButton)) == 1
    dlg.count.setValue(5)
    qtbot.mouseClick(dlg.ok_button, Qt.MouseButton.LeftButton)
    assert [(lp.first, lp.last, lp.count) for lp in seq_tab.seq.loops] == [(0, 1, 5)]


@pytest.mark.req("SW-SEQ-001")
def test_insert_duplicate_delete_reorder_undo(seq_tab) -> None:
    """Verifies: SW-SEQ-001 — + Step after the selection, Duplicate (new uids), Up / Down, Delete, Undo / Redo."""
    _build(seq_tab, "travel", "mark")
    _set(seq_tab, 0, "label", "first")
    seq_tab.select_rows([0])
    assert seq_tab.add_step("hold")
    assert [str(s.kind) for s in seq_tab.seq.steps] == ["travel", "hold", "mark"]
    seq_tab.select_rows([0])
    assert seq_tab.duplicate_selected()
    st = seq_tab.seq.steps
    assert st[1].label == "first" and st[1].uid != st[0].uid
    seq_tab.select_rows([3])
    assert seq_tab.move_selected(-1)
    assert [str(s.kind) for s in seq_tab.seq.steps] == ["travel", "travel", "mark", "hold"]
    seq_tab.select_rows([0, 1])
    assert seq_tab.delete_selected()
    assert [str(s.kind) for s in seq_tab.seq.steps] == ["mark", "hold"]
    seq_tab.undo()
    assert len(seq_tab.seq.steps) == 4
    seq_tab.redo()
    assert len(seq_tab.seq.steps) == 2


@pytest.mark.req("SW-SEQ-001")
def test_settings_row_edits_sequence(seq_tab) -> None:
    """Verifies: SW-SEQ-001 — name, travel reference (test / machine), pull dir, k_est and step defaults."""
    seq_tab.name_edit.setText("stair")
    seq_tab.name_edit.editingFinished.emit()
    seq_tab.ref_combo.setCurrentIndex(1)
    seq_tab.ref_combo.activated.emit(1)
    seq_tab.pull_combo.setCurrentIndex(1)
    seq_tab.pull_combo.activated.emit(1)
    seq_tab.kest_spin.setValue(41.5)
    seq_tab.kest_spin.editingFinished.emit()
    sp = seq_tab.default_spins["capture_s"]
    sp.setValue(7.0)
    sp.editingFinished.emit()
    s = seq_tab.seq
    assert (s.name, s.travel_ref, s.pull_dir, s.k_est_n_mm, s.defaults.capture_s) == ("stair", "machine", -1, 41.5,
                                                                                         7.0)
    assert "stair" in seq_tab.title_label.text()


# ===================================================================================== files (SW-SEQF-001)
@pytest.mark.req("SW-SEQF-001")
def test_save_open_round_trip(seq_tab, tmp_path) -> None:
    """Verifies: SW-SEQF-001 — Save as → Open gives the same sequence (B's v2 file)."""
    _build(seq_tab, "travel", "load", "mark")
    _set(seq_tab, 0, "target", "1.5")
    _set(seq_tab, 1, "target", "100")
    seq_tab.select_rows([0, 1])
    seq_tab.loop_selected(count=2)
    path = str(tmp_path / "a.bbseq.json")
    safe_dialog.FILE_DIALOG_HOOK[0] = lambda kind, cap, flt: path
    assert seq_tab.save_file(as_new=True) and not seq_tab.dirty
    before = json.dumps(sa_dict(seq_tab.seq), sort_keys=True)
    seq_tab.new_sequence(confirm=False)
    assert seq_tab.model.rowCount() == 0
    seq_tab.open_file()
    assert seq_tab.path == path and json.dumps(sa_dict(seq_tab.seq), sort_keys=True) == before


def sa_dict(seq):
    from bend_stand.core.sequencer import seqfile
    return seqfile.to_dict(seq)


@pytest.mark.req("SW-SEQF-001")
def test_invalid_file_keeps_current_sequence(seq_tab, window, tmp_path, monkeypatch) -> None:
    """Verifies: SW-SEQF-001 — a corrupt / invalid file gives an error and leaves the current sequence unchanged."""
    toasts = _toasts(window, monkeypatch)
    _build(seq_tab, "travel")
    _set(seq_tab, 0, "target", "4")
    keep = seq_tab.seq
    bad = tmp_path / "bad.bbseq.json"
    bad.write_text("{not json", encoding="utf-8")
    wrong = tmp_path / "wrong.bbseq.json"
    wrong.write_text(json.dumps({"format": "x", "steps": 5}), encoding="utf-8")
    for p in (bad, wrong, tmp_path / "missing.bbseq.json"):
        assert not seq_tab.load_path(str(p))
        assert seq_tab.seq is keep and seq_tab.seq.steps[0].target == 4.0
    assert all(sev == "error" for _t, sev in toasts[-3:]) and "unchanged" in toasts[-1][0]


@pytest.mark.req("SW-SEQF-001", "SAF-SW-004")
def test_new_with_unsaved_changes_needs_c08(seq_tab, qtbot) -> None:
    """Verifies: SW-SEQF-001, SAF-SW-004 — New on an unsaved sequence asks C-08; cancel keeps it, confirm clears."""
    _build(seq_tab, "travel")
    seq_tab.new_sequence()
    dlg = seq_tab.confirm_dialog
    assert dlg is not None and dlg.cid == "C-08"
    qtbot.mouseClick(dlg.cancel_button, Qt.MouseButton.LeftButton)
    assert seq_tab.model.rowCount() == 1
    seq_tab.new_sequence()
    qtbot.mouseClick(seq_tab.confirm_dialog.confirm_button, Qt.MouseButton.LeftButton)
    assert seq_tab.model.rowCount() == 0 and not seq_tab.dirty


# ===================================================================================== generator wizard
@pytest.mark.req("SW-WIZ-001", "SW-WIZ-002", "SW-STOP-001")
def test_generator_form_from_backend_schema_and_insert(seq_tab, qtbot) -> None:
    """Verifies: SW-WIZ-001, SW-WIZ-002, SW-STOP-001 — G1 lists the backend schemas; G2 is generated from the
    FieldSpecs (depends_on shows increment / count by "Levels by"); G3 previews the block (steps, plan, chart);
    append inserts editable steps."""
    dlg = seq_tab.open_generator()
    assert len(dlg.findChildren(StopButton)) == 1
    names = set(seq_tab.seqr.generator_schemas())
    assert set(dlg.radios) == names
    dlg.choose("staircase")
    dlg.next()
    form = dlg.form
    sch = seq_tab.seqr.generator_schemas()["staircase"]
    assert set(form.editors) == {f.name for f in sch.fields}
    assert form.visible("increment") and not form.visible("count")
    form.set_value("by", "count")
    assert form.visible("count") and not form.visible("increment")
    form.set_value("by", "increment")
    form.set_value("start", 0.0)
    form.set_value("end", 3.0)
    form.set_value("increment", 1.0)
    qtbot.mouseClick(dlg.next_button, Qt.MouseButton.LeftButton)
    assert dlg.page() == 2 and dlg.preview.rowCount() == 4
    assert "4 steps" in dlg.summary.text()
    assert len(dlg.mini_chart.points) >= 4
    qtbot.mouseClick(dlg.insert_button, Qt.MouseButton.LeftButton)
    assert [s.target for s in seq_tab.seq.steps] == [0.0, 1.0, 2.0, 3.0]
    assert _set(seq_tab, 2, "target", "2.5") and seq_tab.seq.steps[2].target == 2.5     # stays editable


@pytest.mark.req("SW-WIZ-001")
def test_generator_value_error_shown_verbatim(seq_tab) -> None:
    """Verifies: SW-WIZ-001 — a generator ValueError is shown verbatim at the form; the page stays G2."""
    dlg = seq_tab.open_generator()
    dlg.choose("staircase")
    dlg.next()
    dlg.form.set_value("start", 0.0)
    dlg.form.set_value("end", 3.0)
    dlg.form.set_value("increment", 10.0)
    assert not dlg.generate()
    assert dlg.page() == 1 and "increment" in dlg.form.error_text()
    assert dlg.form.editors["increment"].styleSheet()


@pytest.mark.req("SW-WIZ-002", "SAF-SW-004")
def test_generator_replace_and_insert_modes(seq_tab, qtbot) -> None:
    """Verifies: SW-WIZ-002, SAF-SW-004 — insert after the selected row; replace all of an unsaved sequence needs
    C-08; cyclic block keeps its loop."""
    _build(seq_tab, "mark", "mark")
    seq_tab.select_rows([0])
    dlg = seq_tab.open_generator()
    dlg.choose("cyclic")
    dlg.next()
    dlg.form.set_value("cycles", 4)
    assert dlg.generate()
    dlg.mode_insert.setChecked(True)
    dlg.insert()
    kinds = [str(s.kind) for s in seq_tab.seq.steps]
    assert kinds[0] == "mark" and kinds[-1] == "mark" and len(kinds) > 3
    assert any(lp.count == 4 and lp.first >= 1 for lp in seq_tab.seq.loops)
    dlg = seq_tab.open_generator()
    dlg.choose("return_")
    dlg.next()
    assert dlg.generate()
    dlg.mode_replace.setChecked(True)
    dlg.insert()
    c08 = seq_tab.confirm_dialog
    assert c08.cid == "C-08"
    qtbot.mouseClick(c08.confirm_button, Qt.MouseButton.LeftButton)
    assert len(seq_tab.seq.steps) == 1 and not seq_tab.seq.loops


# ===================================================================================== run controls (G-30)
def _start_ready(fake):
    fake.set_gate(GateId.SEQUENCE_START, None)
    from fakes import GATE_OK
    fake.set_gate(GateId.SEQUENCE_START, GATE_OK)
    fake.set_gate(GateId.PAUSE, GATE_OK)
    fake.set_gate(GateId.RESUME, GATE_OK)


@pytest.mark.req("SW-SEQ-005", "SAF-SW-004")
def test_start_refused_items_listed_one_per_reason(seq_tab, window, connected_fake) -> None:
    """Verifies: SW-SEQ-005 — the start gate REFUSE items (D-33 b: POS_UNCERTAIN, AFE_RATE_MISMATCH, PAUSED, ALM,
    DRV_PWR_OFF) disable Start with the texts in the tooltip; check_start refusals are listed one per reason."""
    _build(seq_tab, "travel")
    _set(seq_tab, 0, "target", "1")
    seq_tab.revalidate()
    from bend_stand.core.api import GateItem, GateResult, Severity
    items = tuple(GateItem(c, Severity.REFUSE, t) for c, t in (
        ("POS_UNCERTAIN", "position uncertain — re-home"), ("AFE_RATE_MISMATCH", "AFE rate mismatch"),
        ("PAUSED", "PAUSED"), ("ALM", "driver alarm active"), ("DRV_PWR_OFF", "driver power off")))
    connected_fake.set_gate(GateId.SEQUENCE_START, GateResult(items))
    tick(window)
    assert not seq_tab.start_button.isEnabled()
    for _c, t in ((i.code, i.text) for i in items):
        assert t in seq_tab.start_button.toolTip()
    connected_fake.sequencer.check_result = GateResult(items)
    seq_tab.on_start()
    msgs = seq_tab.message_texts()
    for i in items:
        assert any(i.code in m and i.text in m for m in msgs)
    assert not connected_fake.calls_of("sequencer.start")


@pytest.mark.req("SW-SEQ-005", "SAF-SW-004", "SAF-SW-006")
def test_start_with_confirm_items_needs_c07(seq_tab, window, connected_fake, qtbot) -> None:
    """Verifies: SW-SEQ-005, SAF-SW-004 — CONFIRM / WARN items → C-07 (keyboard never confirms) →
    ``start(seq, confirmed=True)`` (B3-20); without confirmation nothing starts."""
    _start_ready(connected_fake)
    _build(seq_tab, "travel")
    _set(seq_tab, 0, "target", "1")
    seq_tab.revalidate()
    tick(window)
    assert seq_tab.start_button.isEnabled()
    from bend_stand.core.api import GateItem, GateResult, Severity
    connected_fake.sequencer.check_result = GateResult((
        GateItem("HOTKEY_UNAVAILABLE", Severity.CONFIRM, "Pause/Break key unavailable"),
        GateItem("SAF_SW_006_MARGIN", Severity.WARN, "margin below k·v·65 ms")))
    seq_tab.on_start()
    dlg = seq_tab.confirm_dialog
    assert dlg.cid == "C-07" and "Pause/Break key unavailable" in dlg.text_label.text()
    assert "margin" in dlg.text_label.text()
    assert not connected_fake.calls_of("sequencer.start")
    qtbot.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.outcome == "pending"
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    calls = connected_fake.calls_of("sequencer.start")
    assert len(calls) == 1 and calls[0].kwargs == {"confirmed": True}
    assert connected_fake.sequencer.status().state == "RUNNING"


@pytest.mark.req("SW-SEQ-007", "SW-STOP-004", "SW-SEQ-003")
def test_run_controls_and_run_line(seq_tab, window, connected_fake) -> None:
    """Verifies: SW-SEQ-007, SW-STOP-004 — Start / Pause / Resume / Continue / Stop / Abort call the backend;
    the editor is read-only while running; the run line shows state, step i/N, loop, phase, windows, remaining."""
    _start_ready(connected_fake)
    _build(seq_tab, "travel", "mark")
    _set(seq_tab, 0, "target", "1")
    seq_tab.revalidate()
    tick(window)
    seq_tab.on_start()
    calls = connected_fake.calls_of("sequencer.start")
    assert len(calls) == 1 and calls[0].kwargs == {"confirmed": False}
    sq = connected_fake.sequencer
    sq.set_status(state="RUNNING", exec_idx=0, step_uid=seq_tab.seq.steps[0].uid, plan_len=2, loop_iters=(2,),
                  phase="SETTLE", windows_done=1, windows_total=3, plan_t_s=12.0, plan_total_s=90.0,
                  remaining_s=78.0, behind_s=0.4)
    tick(window)
    line = seq_tab.run_label.text()
    for part in ("RUNNING", "step 1/2", "loop 2", "phase SETTLE", "windows 1/3", "remaining ~00:01:18"):
        assert part in line, (part, line)
    assert seq_tab.model.read_only and not seq_tab.add_button.isEnabled()
    assert seq_tab.model.active_row == 0
    assert not seq_tab.start_button.isEnabled() and seq_tab.pause_button.isEnabled()
    seq_tab.pause_button.click()
    assert connected_fake.calls_of("pause")[-1].args == ("sequence",)
    sq.set_status(state="PAUSED", paused_source="BUTTON")
    tick(window)
    assert "PAUSED (BUTTON)" in seq_tab.run_label.text() and seq_tab.resume_button.isEnabled()
    seq_tab.resume_button.click()
    assert connected_fake.calls_of("resume")[-1].args == ("sequence",)
    sq.set_status(state="WAITING_OPERATOR", phase="WAIT_OPERATOR", paused_source=None)
    tick(window)
    assert seq_tab.continue_button.isEnabled()
    seq_tab.continue_button.click()
    assert connected_fake.calls_of("sequencer.continue_")
    seq_tab.stop_seq_button.click()
    assert connected_fake.calls_of("sequencer.stop")
    tick(window)
    assert any("Sequence ended: STOPPED" in m for m in seq_tab.message_texts())
    assert not seq_tab.model.read_only


@pytest.mark.req("SW-SEQ-007")
def test_abort_and_until_stopped_remaining(seq_tab, window, connected_fake) -> None:
    """Verifies: SW-SEQ-007 — Abort (HALT) → ``sequencer.abort``; an until-stopped loop shows "∞"."""
    sq = connected_fake.sequencer
    sq.set_status(state="RUNNING", exec_idx=3, plan_len=None, remaining_s=None, plan_total_s=None, plan_t_s=5.0)
    tick(window)
    assert "remaining ∞ – loop until stopped" in seq_tab.run_label.text()
    seq_tab.abort_button.click()
    assert connected_fake.calls_of("sequencer.abort")
    tick(window)
    assert "OPERATOR_ABORT" in seq_tab.run_label.text()


@pytest.mark.req("SW-SEQ-006", "SW-SEQ-007", "SW-SCH-002")
def test_not_reached_and_driver_alarm_shown(seq_tab, window, connected_fake) -> None:
    """Verifies: SW-SEQ-006, SW-SEQ-007 — a NOT_REACHED step result is listed in red with its explanation and
    marked in the chart where the axis stopped (D-32); end reasons NOT_REACHED / DRIVER_ALARM in the run line."""
    _build(seq_tab, "load")
    _set(seq_tab, 0, "target", "200")
    sq = connected_fake.sequencer
    sq.set_status(state="RUNNING", exec_idx=0, plan_len=1)
    tick(window)
    sq.emit_result(exec_idx=0, step_idx=0, uid=seq_tab.seq.steps[0].uid, label="200 N", kind="load",
                   flags=("NOT_REACHED",), f_end_n=167.4, x_end_mm=289.5)
    QApplication.processEvents()
    msg = seq_tab.message_texts()[-1]
    assert "NOT_REACHED" in msg and "not a limit trip" in msg
    assert seq_tab.messages.item(seq_tab.messages.count() - 1).foreground().color().name() == "#c00000"
    assert seq_tab.chart.not_reached_points() == [(289.5, pytest.approx(167.4))]
    sq.set_status(state="STOPPED", end_reason="NOT_REACHED")
    tick(window)
    assert "end: NOT_REACHED" in seq_tab.run_label.text()
    sq.set_status(state="RUNNING")
    tick(window)
    sq.set_status(state="STOPPED", end_reason="DRIVER_ALARM")
    tick(window)
    assert "end: DRIVER_ALARM" in seq_tab.run_label.text()
    assert any("DRIVER_ALARM" in m for m in seq_tab.message_texts())


@pytest.mark.req("SW-STOP-004")
def test_resume_refused_shows_clear_stop_first(seq_tab, window, connected_fake) -> None:
    """Verifies: SW-STOP-004 — while HALT is latched the resume gate REFUSE → Resume disabled with "Clear stop
    first"."""
    connected_fake.sequencer.set_status(state="PAUSED", paused_source="PC")
    connected_fake.set_gate(GateId.RESUME, refuse("HALT", "HALT latched"))
    tick(window)
    assert not seq_tab.resume_button.isEnabled()
    assert "Clear stop first" in seq_tab.resume_button.toolTip()


@pytest.mark.req("SW-SEQ-001")
def test_edit_refused_by_sequence_edit_gate(seq_tab, window, connected_fake) -> None:
    """Verifies: SW-SEQ-001 — the editor is read-only while the ``sequence_edit`` gate refuses."""
    _build(seq_tab, "travel")
    connected_fake.set_gate(GateId.SEQUENCE_EDIT, refuse("SEQUENCE_RUNNING", "sequence running"))
    tick(window)
    assert seq_tab.model.read_only
    assert not _set(seq_tab, 0, "target", "9")
    assert not seq_tab.add_step("mark")
    connected_fake.set_gate(GateId.SEQUENCE_EDIT, None)
    tick(window)
    assert _set(seq_tab, 0, "target", "9")


# ===================================================================================== chart (G-31)
@pytest.mark.req("SW-SCH-001")
def test_chart_shows_backend_planned_path_with_labels(seq_tab) -> None:
    """Verifies: SW-SCH-001 — the chart's polyline is the backend's planned path; step labels shown; capture points
    green; HOME breaks the line."""
    _build(seq_tab, "travel", "load", "home", "travel")
    _set(seq_tab, 0, "target", "2")
    _set(seq_tab, 1, "target", "100")
    _set(seq_tab, 3, "target", "1")
    _set(seq_tab, 0, "capture_s", "2")
    seq_tab.revalidate()
    path = P.planned_path(P.expand(seq_tab.seq))
    xs, _ys = seq_tab.chart.path_xy()
    want = []
    for p in path:
        if p.brk and want:
            want.append(math.nan)
        want.append(p.x_mm)
    assert len(xs) == len(want)
    assert all((math.isnan(a) and math.isnan(b)) or a == pytest.approx(b) for a, b in zip(xs, want, strict=True))
    assert any(math.isnan(v) for v in xs)                      # HOME break
    assert seq_tab.chart.labels and seq_tab.chart.capture_points.data.size >= 1
    assert "executed" in seq_tab.plan_label.text()


@pytest.mark.req("SW-SCH-002")
def test_chart_live_marker_active_step_and_trace(seq_tab, window, connected_fake) -> None:
    """Verifies: SW-SCH-002 — during a run the vertical marker follows ``marker_x_mm``, the point is (x, F), the
    active step is highlighted and the measured trace is drawn."""
    _build(seq_tab, "travel", "travel")
    _set(seq_tab, 0, "target", "2")
    _set(seq_tab, 1, "target", "4")
    seq_tab.revalidate()
    connected_fake.sequencer.set_status(state="RUNNING", exec_idx=1, plan_len=2, marker_x_mm=3.25,
                                        marker_f_n=12.0)
    for _ in range(3):
        tick(window)
    ch = seq_tab.chart
    assert ch.marker_line.isVisible() and ch.marker_line.value() == pytest.approx(3.25)
    assert ch.live_xy == (pytest.approx(3.25), pytest.approx(12.0))
    assert ch.active_exec == 1 and ch.active_point.data.size == 1


@pytest.mark.rt                    # real-time, timing-sensitive: verdict on the reference PC (Orchestrator)
@pytest.mark.req("SW-SCH-002")
def test_chart_marker_rate_at_least_10_hz(make_window, connected_fake) -> None:
    """Verifies: SW-SCH-002 (timing part) — with the real refresh timer the live marker is repainted ≥ 10 Hz
    (design ≈ 30 Hz); the backend publishes ``seq.status`` at 20 Hz (B6-07)."""
    win = make_window(connected_fake)
    win.tabs.setCurrentWidget(win.sequence_tab)
    connected_fake.sequencer.set_status(state="RUNNING", exec_idx=0, plan_len=1, marker_x_mm=1.0, marker_f_n=0.0)
    ch = win.sequence_tab.chart
    n0 = ch.marker_updates
    win.refresh.start()
    t0 = time.perf_counter()
    loop = QEventLoop()
    QTimer.singleShot(1500, loop.quit)
    loop.exec()
    dt = time.perf_counter() - t0
    win.refresh.stop()
    rate = (ch.marker_updates - n0) / dt
    print(f"\n[SW-SCH-002] live marker repaint rate {rate:.1f} Hz over {dt:.2f} s")
    assert rate >= 10.0


@pytest.mark.req("SW-SEQ-001")
def test_issue_mapping_pure() -> None:
    """Verifies: SW-SEQ-001 — SeqIssue (step_uid / field) and key forms map to cells; loop issues stay general."""
    steps = [M.Step("a1", M.StepKind.TRAVEL), M.Step("b2", M.StepKind.LOAD)]
    from bend_stand.core.api import Issue, IssueSeverity
    iss = sa.cell_issues([M.SeqIssue("b2", "target", IssueSeverity.ERROR, "MISSING", "t"),
                          M.SeqIssue(None, "loops[0]", IssueSeverity.WARN, "UNREACHABLE", "u"),
                          Issue("a1.speed_mm_s", IssueSeverity.WARN, "X", "w"),
                          Issue("steps[1].tol_n", IssueSeverity.ERROR, "Y", "z")], steps)
    assert [(i.uid, i.field, i.severity) for i in iss] == [("b2", "target", "ERROR"), (None, "loops[0]", "WARN"),
                                                            ("a1", "speed_mm_s", "WARN"), ("b2", "tol_n", "ERROR")]


@pytest.mark.req("SW-SEQ-002")
def test_loop_bookkeeping_pure() -> None:
    """Verifies: SW-SEQ-002 — loop indices after delete / insert (pure helpers)."""
    L = M.Loop
    assert [(x.first, x.last) for x in sa.loops_after_delete([L(1, 3, 2), L(5, 6, 1)], [0, 2])] == [(0, 1), (3, 4)]
    assert sa.loops_after_delete([L(1, 1, 2)], [1]) == []
    assert [(x.first, x.last) for x in sa.loops_after_insert([L(1, 3, 2), L(5, 6, 1)], 2, 2)] == [(1, 5), (7, 8)]
    assert sa.gutter_text([L(0, 2, 0)], 1) == "│" and sa.gutter_text([L(2, 2, 3)], 2) == "[×3"


@pytest.mark.req("SW-SEQ-005")
def test_start_warn_only_needs_c07_too(seq_tab, window, connected_fake, qtbot) -> None:
    """Verifies: SW-SEQ-005, SAF-SW-006 — WARN-only start items are shown in C-07 before the start."""
    _start_ready(connected_fake)
    _build(seq_tab, "travel")
    _set(seq_tab, 0, "target", "1")
    seq_tab.revalidate()
    connected_fake.sequencer.check_result = warn("LOW_SPAN", "LOW_SPAN calibration")
    seq_tab.on_start()
    assert seq_tab.confirm_dialog.cid == "C-07" and "LOW_SPAN" in seq_tab.confirm_dialog.text_label.text()
    qtbot.mouseClick(seq_tab.confirm_dialog.cancel_button, Qt.MouseButton.LeftButton)
    assert not connected_fake.calls_of("sequencer.start")
    connected_fake.sequencer.check_result = confirm("TRAVEL_CAL_DIFFERS", "travel calibration differs")
    connected_fake.sequencer.start_result = None
    seq_tab.on_start()
    qtbot.mouseClick(seq_tab.confirm_dialog.confirm_button, Qt.MouseButton.LeftButton)
    assert connected_fake.calls_of("sequencer.start")[-1].kwargs == {"confirmed": True}
    _ = StopResult


@pytest.mark.req("SW-SEQ-003", "SW-SCH-002", "SW-STOP-004")
def test_step_label_and_phase_follow_status_without_stale_label(seq_tab, window, connected_fake) -> None:
    """Verifies: SW-SEQ-003, SW-SCH-002, SW-STOP-004 — B publishes phase COMMAND together with the new step's label
    at every step start (also after Resume): the run line shows the label of the step it names (never the previous
    step's), the active row moves in the same tick and is never cleared in between (no flicker), PAUSED keeps
    the row, the re-run after Resume shows COMMAND with the same label; after the end no label is shown."""
    _build(seq_tab, "load", "load")
    s0, s1 = seq_tab.seq.steps[0], seq_tab.seq.steps[1]
    sq = connected_fake.sequencer
    rows: list[object] = []
    lines: list[str] = []

    def snap() -> None:
        tick(window)
        rows.append(seq_tab.model.active_row)
        lines.append(seq_tab.run_label.text())
    sq.set_status(state="RUNNING", exec_idx=0, step_uid=s0.uid, label="20 N", phase="CAPTURE", plan_len=2)
    snap()
    sq.set_status(exec_idx=1, step_uid=s1.uid, label="40 N", phase="COMMAND")           # step start (one snapshot)
    snap()
    sq.set_status(phase="APPROACH")
    snap()
    sq.set_status(state="PAUSED", phase="PAUSED", paused_source="PC")
    snap()
    sq.set_status(state="RUNNING", phase="COMMAND", paused_source=None)                  # Resume: step re-run
    snap()
    assert rows == [0, 1, 1, 1, 1], rows
    assert '"20 N"' in lines[0] and "step 1/2" in lines[0] and "phase CAPTURE" in lines[0]
    for ln in lines[1:]:
        assert "step 2/2" in ln and '"40 N"' in ln and '"20 N"' not in ln, ln
    assert "phase COMMAND" in lines[1] and "phase COMMAND" in lines[4] and "PAUSED (PC)" in lines[3]
    n_set = seq_tab.run_label.text()
    tick(window)
    assert seq_tab.run_label.text() == n_set                                             # stable between ticks
    sq.set_status(state="FINISHED", end_reason="COMPLETED", phase=None)
    tick(window)
    assert '"40 N"' not in seq_tab.run_label.text() and seq_tab.model.active_row is None
