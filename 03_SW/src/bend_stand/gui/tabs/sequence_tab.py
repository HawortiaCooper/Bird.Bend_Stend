"""Sequence tab (SW_design_GUI §3.6): step table editor, loops, generator wizard, files, sequence chart, run
controls and run line.

**Editor.** A :class:`StepTableModel` over the backend's ``Sequence`` object (``sequencer.new()`` /
``sequencer.load()``); the GUI edits its fields (via :mod:`gui.seq_access`) and never expands, times or checks a
sequence itself. After every edit the backend ``sequencer.validate(seq)`` (debounced 200 ms) colours the cells
(red ERROR, amber WARN, tooltip = text) and sets the validity badge; ``expand`` + ``planned_path`` feed the plan
summary and the chart. Toolbar: New / Open… / Save / Save as… · Generate… · + Step ▾ (typed insert after the
selection) / Duplicate / Delete / Up / Down · Loop… (count 1…10 000, 0 = until stopped; one nesting level is the
backend's validation rule) / Unloop · Undo / Redo (snapshots) · advanced columns. Settings: name, travel reference
(test / machine), pull direction, k_est, step defaults. The editor is read-only while a sequence runs or the
``sequence_edit`` gate refuses.

**Files** (SW-SEQF-001): ``*.bbseq.json`` via the non-native file dialogs (STOP inside); ``sequencer.load`` raises
→ the error is shown and the **current sequence stays unchanged**; New / Open of an unsaved sequence → C-08.

**Run controls** (enabled from the gates; SW-SEQ-005/007, SW-STOP-004): Start → ``sequencer.start(seq)``; CONFIRM /
WARN items (precomputed ``sequence_start`` gate, or returned by ``start``) → C-07 → ``start(seq, confirmed=True)``
(B3-20); REFUSE items are shown verbatim, one per reason. Pause → ``backend.pause("sequence")`` (FW PAUSE);
Resume → ``backend.resume("sequence")`` (RESUME + re-issue; "Clear stop first" when HALT / ESTOP / FAULT is
latched); Continue → ``sequencer.continue_()`` (MARK wait-for-operator); Stop (controlled) →
``sequencer.stop()``; Abort (HALT) → ``sequencer.abort()``. A STOP button on the tab calls ``backend.stop``.

**Run line** from ``sequencer.status()`` every refresh tick: state (PAUSED + source), step i/N, loop iteration,
phase, windows, plan time / total (behind), remaining (∞ for loop-until-stopped), k_est, end reason incl.
NOT_REACHED / DRIVER_ALARM / CLEARED. Step results (``seq.step_result``) are listed with their flags verbatim
(NOT_REACHED red, also marked in the chart).

**Chart** (SW-SCH-001/002): :class:`SequenceChart`; live marker + point every tick (≈ 30 Hz) from
``data.latest`` (test or machine travel per the sequence's travel reference, ``F_N``), trace
``data.sequence_trace()`` every 3rd tick, active step from the status.

Implements: SW-SEQ-001 (step table editor, validation display), SW-SEQ-002 (loops), SW-SEQ-003 (run phases shown),
SW-SEQ-005 (start gate items, C-07), SW-SEQ-006 / SW-SEQ-007 (controls, NOT_REACHED / DRIVER_ALARM shown),
SW-STOP-004 (sequence Pause / Resume), SW-WIZ-002 (generated steps inserted, editable), SW-SEQF-001 (save / recall,
invalid file → unchanged), SW-SCH-001 / SW-SCH-002 (chart), SW-STOP-001 (STOP on the tab), SAF-SW-004 (C-07, C-08)
"""
from __future__ import annotations

import dataclasses
import logging
import math
import os
from typing import Any

from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyledItemDelegate,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bend_stand.core.api import GateId
from bend_stand.gui import gating
from bend_stand.gui import seq_access as sa
from bend_stand.gui.dialogs.confirm_dialog import make_confirm
from bend_stand.gui.dialogs.safe_dialog import SafeDialog, get_open_file_name, get_save_file_name
from bend_stand.gui.models.step_table_model import StepTableModel
from bend_stand.gui.plots.sequence_chart import SequenceChart
from bend_stand.gui.units_state import force_unit
from bend_stand.gui.widgets.stop_button import StopButton
from bend_stand.gui.wizards.generator import GeneratorDialog

log = logging.getLogger(__name__)

SEQ_FILTER = "Sequences (*.bbseq.json);;All files (*)"
VALIDATE_MS = 200
MAX_MESSAGES = 300
MAX_UNDO = 100
TRACE_EVERY = 3
RED = QColor("#c00000")
EDIT_TRIGGERS = (QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                 | QAbstractItemView.EditTrigger.AnyKeyPressed)
DEFAULT_FIELDS = (("speed_mm_s", "v", "mm/s"), ("accel_mm_s2", "a", "mm/s²"), ("settle_s", "settle", "s"),
                  ("capture_s", "capture", "s"), ("tol_n", "tol", "N"))


def _err_text(exc: BaseException) -> str:
    return str(getattr(exc, "user_text", "") or exc or type(exc).__name__)


class KindDelegate(QStyledItemDelegate):
    """Combo editor of the Type column (step kinds)."""

    def createEditor(self, parent: QWidget, option: Any, index: Any) -> QWidget:  # noqa: N802
        cb = QComboBox(parent)
        cb.addItems(list(sa.STEP_KINDS))
        return cb

    def setEditorData(self, editor: QWidget, index: Any) -> None:  # noqa: N802
        i = editor.findText(str(index.data()))  # type: ignore[attr-defined]
        if i >= 0:
            editor.setCurrentIndex(i)  # type: ignore[attr-defined]

    def setModelData(self, editor: QWidget, model: Any, index: Any) -> None:  # noqa: N802
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)  # type: ignore[attr-defined]


class LoopDialog(SafeDialog):
    """Loop count 1…10 000 or 0 = until stopped (SW-SEQ-002); STOP in the top bar."""

    def __init__(self, parent: QWidget | None, first: int, last: int, count: int = 2) -> None:
        super().__init__(parent, title="Loop")
        self.setObjectName("loopDialog")
        lay = QVBoxLayout()
        lay.addWidget(QLabel(f"Repeat steps {first + 1}…{last + 1}", self))
        row = QHBoxLayout()
        row.addWidget(QLabel("count (0 = until stopped)", self))
        self.count = QSpinBox(self)
        self.count.setObjectName("loopCount")
        self.count.setRange(0, 10_000)
        self.count.setValue(count)
        row.addWidget(self.count)
        lay.addLayout(row)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.ok_button = QPushButton("Wrap in loop", self)
        for b in (self.cancel_button, self.ok_button):
            b.setAutoDefault(False)
            b.setDefault(False)
            btns.addWidget(b)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        lay.addLayout(btns)
        self.setLayout(lay)


class SequenceTab(QWidget):
    message = Signal(str, str)
    stopResult = Signal(object)
    resumeResult = Signal(object)

    def __init__(self, backend: Any, bridge: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sequenceTab")
        self._backend = backend
        self._bridge = bridge
        self.seqr = backend.sequencer
        self.seq: Any = None
        self.types = sa.SeqTypes(None)
        self.path: str | None = None
        self.dirty = False
        self._undo: list[Any] = []
        self._redo: list[Any] = []
        self.plan: Any = None
        self.summary: sa.PlanSummary | None = None
        self.view: sa.RunView = sa.run_view(None)
        self.confirm_dialog: Any = None
        self.dialogs: dict[str, Any] = {}
        self._ticks = 0
        self._edit_gate_ok = True
        self._prev_state = "IDLE"
        self.step_results: list[Any] = []
        self.available = True
        self._build()
        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(VALIDATE_MS)
        self._validate_timer.timeout.connect(self.revalidate)
        if bridge is not None and hasattr(bridge, "sequenceEvent"):
            bridge.sequenceEvent.connect(self._on_seq_event)
        force_unit().changed.connect(self._on_unit)
        self.new_sequence(confirm=False)

    # ================================================================== construction
    def _btn(self, row: QHBoxLayout, text: str, name: str, slot: Any, tip: str = "") -> QPushButton:
        b = QPushButton(text, self)
        b.setObjectName(name)
        b.setAutoDefault(False)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(lambda _c=False: slot())
        row.addWidget(b)
        return b

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        self.title_label = QLabel("Sequence", self)
        self.title_label.setObjectName("sequenceTitle")
        self.title_label.setStyleSheet("font-weight: bold;")
        outer.addWidget(self.title_label)

        bar = QHBoxLayout()
        self.new_button = self._btn(bar, "New", "seqNew", self.new_sequence)
        self.open_button = self._btn(bar, "Open…", "seqOpen", self.open_file)
        self.save_button = self._btn(bar, "Save", "seqSave", self.save_file)
        self.save_as_button = self._btn(bar, "Save as…", "seqSaveAs", lambda: self.save_file(as_new=True))
        bar.addSpacing(10)
        self.generate_button = self._btn(bar, "Generate…", "seqGenerate", self.open_generator,
                                         "Generator wizard: staircase, ramp, cyclic, hold, return")
        bar.addSpacing(10)
        self.undo_button = self._btn(bar, "Undo", "seqUndo", self.undo)
        self.redo_button = self._btn(bar, "Redo", "seqRedo", self.redo)
        bar.addStretch(1)
        self.valid_badge = QLabel("", self)
        self.valid_badge.setObjectName("seqValidBadge")
        self.valid_badge.setWordWrap(True)
        self.valid_badge.setMaximumWidth(360)
        bar.addWidget(self.valid_badge)
        outer.addLayout(bar)
        bar = QHBoxLayout()
        self.add_button = QToolButton(self)
        self.add_button.setObjectName("seqAddStep")
        self.add_button.setText("+ Step")
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.add_button)
        for k in sa.STEP_KINDS:
            act = menu.addAction(k)
            act.triggered.connect(lambda _c=False, kk=k: self.add_step(kk))
        self.add_button.setMenu(menu)
        bar.addWidget(self.add_button)
        self.duplicate_button = self._btn(bar, "Duplicate", "seqDuplicate", self.duplicate_selected)
        self.delete_button = self._btn(bar, "Delete", "seqDelete", self.delete_selected)
        self.up_button = self._btn(bar, "Up", "seqUp", lambda: self.move_selected(-1))
        self.down_button = self._btn(bar, "Down", "seqDown", lambda: self.move_selected(+1))
        self.loop_button = self._btn(bar, "Loop…", "seqLoop", self.loop_selected)
        self.unloop_button = self._btn(bar, "Unloop", "seqUnloop", self.unloop_selected)
        self.advanced_box = QCheckBox("advanced columns", self)
        self.advanced_box.setObjectName("seqAdvanced")
        self.advanced_box.toggled.connect(self._on_advanced)
        bar.addWidget(self.advanced_box)
        bar.addStretch(1)
        outer.addLayout(bar)
        self.edit_buttons = (self.new_button, self.open_button, self.generate_button, self.add_button,
                             self.duplicate_button, self.delete_button, self.up_button, self.down_button,
                             self.loop_button, self.unloop_button, self.undo_button, self.redo_button)

        split = QSplitter(Qt.Orientation.Horizontal, self)
        left = QWidget(split)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.model = StepTableModel(self)
        self.model.on_edit = self._on_cell_edit
        self.model.parseError.connect(lambda t: self.message.emit(t, "warn"))
        self.table = QTableView(left)
        self.table.setObjectName("stepTable")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.table.setEditTriggers(EDIT_TRIGGERS)
        self.table.setItemDelegateForColumn(0, KindDelegate(self.table))
        self.table.selectionModel().selectionChanged.connect(lambda *_a: self._update_edit_buttons())
        ll.addWidget(self.table, 1)
        self.plan_label = QLabel("", left)
        self.plan_label.setObjectName("seqPlanSummary")
        self.plan_label.setWordWrap(True)
        ll.addWidget(self.plan_label)
        self.seq_issue_label = QLabel("", left)
        self.seq_issue_label.setObjectName("seqIssues")
        self.seq_issue_label.setWordWrap(True)
        self.seq_issue_label.setStyleSheet("color: #a00000;")
        ll.addWidget(self.seq_issue_label)
        ll.addWidget(self._build_settings(left))
        self.chart = SequenceChart(split)
        split.addWidget(left)
        split.addWidget(self.chart)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)
        outer.addWidget(self._build_run(self))

    def _build_settings(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("Settings", parent)
        grid = QGridLayout(box)
        grid.addWidget(QLabel("name", box), 0, 0)
        self.name_edit = QLineEdit(box)
        self.name_edit.setObjectName("seqName")
        self.name_edit.editingFinished.connect(lambda: self._set_attr("name", self.name_edit.text()))
        grid.addWidget(self.name_edit, 0, 1, 1, 3)
        grid.addWidget(QLabel("travel ref", box), 0, 4)
        self.ref_combo = QComboBox(box)
        self.ref_combo.setObjectName("seqTravelRef")
        self.ref_combo.addItem("test", "test")
        self.ref_combo.setToolTip("test: travel targets relative to the test travel zero frozen at start; machine: machine mm")
        self.ref_combo.addItem("machine", "machine")
        self.ref_combo.activated.connect(lambda _i: self._set_attr("travel_ref", self.ref_combo.currentData()))
        grid.addWidget(self.ref_combo, 0, 5)
        grid.addWidget(QLabel("pull dir", box), 1, 0)
        self.pull_combo = QComboBox(box)
        self.pull_combo.setObjectName("seqPullDir")
        self.pull_combo.addItem("+1", 1)
        self.pull_combo.setToolTip("+1: pull (tension) = +x; −1: pull = −x")
        self.pull_combo.addItem("−1", -1)
        self.pull_combo.activated.connect(lambda _i: self._set_attr("pull_dir", self.pull_combo.currentData()))
        grid.addWidget(self.pull_combo, 1, 1)
        grid.addWidget(QLabel("k_est N/mm", box), 1, 2)
        self.kest_spin = QDoubleSpinBox(box)
        self.kest_spin.setObjectName("seqKest")
        self.kest_spin.setDecimals(2)
        self.kest_spin.setRange(0.0, 1e5)
        self.kest_spin.editingFinished.connect(lambda: self._set_attr("k_est_n_mm", self.kest_spin.value()))
        grid.addWidget(self.kest_spin, 1, 3)
        self.default_spins: dict[str, QDoubleSpinBox] = {}
        grid.addWidget(QLabel("defaults:", box), 2, 0)
        for i, (f, lab, unit) in enumerate(DEFAULT_FIELDS):
            r, c = 2 + i // 3, 1 + 2 * (i % 3)
            grid.addWidget(QLabel(f"{lab} [{unit}]", box), r, c)
            sp = QDoubleSpinBox(box)
            sp.setObjectName(f"seqDefault_{f}")
            sp.setDecimals(3)
            sp.setRange(0.0, 1e4)
            sp.editingFinished.connect(lambda ff=f, s=sp: self._set_default(ff, s.value()))
            grid.addWidget(sp, r, c + 1)
            self.default_spins[f] = sp
        self.settings_box = box
        return box

    def _build_run(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("Run", parent)
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        self.start_button = self._btn(row, "▶ Start", "seqStart", self.on_start)
        self.pause_button = self._btn(row, "‖ Pause", "seqPause", self.on_pause)
        self.resume_button = self._btn(row, "Resume", "seqResume", self.on_resume)
        self.continue_button = self._btn(row, "Continue", "seqContinue", self.on_continue,
                                         "Continue after a MARK step that waits for the operator")
        self.stop_seq_button = self._btn(row, "Stop (controlled)", "seqStop", self.on_stop_sequence,
                                         "Controlled stop: the sequence ends (STOPPED)")
        self.abort_button = self._btn(row, "Abort (HALT)", "seqAbort", self.on_abort,
                                      "HALT (latched): the sequence ends ABORTED; Clear stop needed")
        for b in (self.start_button, self.resume_button, self.continue_button):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)          # mouse-only: these (re-)start motion
        row.addStretch(1)
        self.stop_button = StopButton(box, large=True, source="sequence")
        row.addWidget(self.stop_button)
        lay.addLayout(row)
        self.run_label = QLabel("IDLE", box)
        self.run_label.setObjectName("seqRunLine")
        self.run_label.setWordWrap(True)
        lay.addWidget(self.run_label)
        self.messages = QListWidget(box)
        self.messages.setObjectName("seqMessages")
        self.messages.setMaximumHeight(110)
        lay.addWidget(self.messages)
        return box

    # ================================================================== sequence object
    def set_sequence(self, seq: Any, *, path: str | None = None, dirty: bool = False) -> None:
        self.seq = seq
        if seq is not None and (self.types.step_cls is None or self.types.loop_cls is None):
            self.types = sa.SeqTypes(seq)
        self.path = path
        self.dirty = dirty
        self._undo.clear()
        self._redo.clear()
        self.model.set_sequence(seq)
        self.chart.clear_run()
        self._load_settings()
        self._update_title()
        self.revalidate()

    def _load_settings(self) -> None:
        seq = self.seq
        self.settings_box.setEnabled(seq is not None)
        if seq is None:
            return
        self.name_edit.setText(str(sa.get(seq, "name", default="") or ""))
        i = self.ref_combo.findData(str(sa.text_of(sa.get(seq, "travel_ref", default="test"))))
        self.ref_combo.setCurrentIndex(max(i, 0))
        i = self.pull_combo.findData(int(sa.get(seq, "pull_dir", default=1) or 1))
        self.pull_combo.setCurrentIndex(max(i, 0))
        self.kest_spin.setValue(float(sa.get(seq, "k_est_n_mm", default=0.0) or 0.0))
        d = sa.get(seq, "defaults")
        for f, sp in self.default_spins.items():
            v = sa.get(d, f) if d is not None else None
            sp.setEnabled(d is not None and hasattr(d, f) if not isinstance(d, dict) else f in d)
            sp.setValue(float(v) if v is not None else 0.0)

    def _update_title(self) -> None:
        name = str(sa.get(self.seq, "name", default="") or "") if self.seq is not None else ""
        where = os.path.basename(self.path) if self.path else "(not saved)"
        self.title_label.setText(f"Sequence: {name or 'unnamed'} — {where}" + (" *" if self.dirty else ""))

    # ------------------------------------------------------------------ editing helpers
    def _push_undo(self) -> None:
        self._undo.append(sa.snapshot(self.seq))
        del self._undo[:-MAX_UNDO]
        self._redo.clear()

    def _changed(self, keep_rows: list[int] | None = None) -> None:
        self.dirty = True
        self.model.refresh()
        if keep_rows:
            self.select_rows(keep_rows)
        self._update_title()
        self._validate_timer.start()
        self._update_edit_buttons()

    def _edit(self, fn: Any, keep_rows: list[int] | None = None) -> bool:
        if not self._can_edit():
            self.message.emit("The sequence cannot be edited now (running or refused by the backend)", "warn")
            return False
        self._push_undo()
        try:
            rows = fn()
        except Exception as exc:  # noqa: BLE001 - model error: undo the snapshot
            self.seq = self._undo.pop()
            self.model.set_sequence(self.seq)
            self.message.emit(f"Edit failed: {_err_text(exc)}", "error")
            return False
        self._changed(rows if isinstance(rows, list) else keep_rows)
        return True

    def _can_edit(self) -> bool:
        return self.seq is not None and not self.model.read_only

    def selected_rows(self) -> list[int]:
        sm = self.table.selectionModel()
        return sorted({i.row() for i in sm.selectedRows()} | {i.row() for i in sm.selectedIndexes()})

    def select_rows(self, rows: list[int]) -> None:
        self.table.clearSelection()
        n = self.model.rowCount()
        rows = [r for r in rows if 0 <= r < n]
        if not rows:
            return
        sm = self.table.selectionModel()
        sel = QItemSelection(self.model.index(min(rows), 0), self.model.index(max(rows), self.model.columnCount() - 1))
        sm.select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        sm.setCurrentIndex(self.model.index(max(rows), 0), QItemSelectionModel.SelectionFlag.NoUpdate)

    def _defaults_kw(self, kind: str) -> dict[str, Any]:
        """New step fields from the sequence defaults (only fields that apply to the kind)."""
        d = sa.get(self.seq, "defaults")
        kw: dict[str, Any] = {}
        for f, *_r in DEFAULT_FIELDS:
            if f in sa.APPLIES.get(kind, frozenset()) and d is not None and sa.get(d, f) is not None \
                    and f != "speed_mm_s":
                kw[f] = sa.get(d, f)
        return kw

    # ------------------------------------------------------------------ editor actions
    def add_step(self, kind: str) -> bool:
        rows = self.selected_rows()
        at = (rows[-1] + 1) if rows else len(sa.steps_of(self.seq))

        def do() -> list[int]:
            sa.insert_steps(self.seq, at, [self.types.make_step(kind, **self._defaults_kw(kind))])
            return [at]
        return self._edit(do)

    def duplicate_selected(self) -> bool:
        rows = self.selected_rows()
        if not rows:
            return False
        return self._edit(lambda: sa.duplicate_rows(self.seq, rows))

    def delete_selected(self) -> bool:
        rows = self.selected_rows()
        if not rows:
            return False
        return self._edit(lambda: (sa.delete_rows(self.seq, rows), [min(rows[0], len(sa.steps_of(self.seq)) - 1)])[1])

    def move_selected(self, delta: int) -> bool:
        rows = self.selected_rows()
        if len(rows) != 1:
            return False
        return self._edit(lambda: [sa.move_row(self.seq, rows[0], delta)])

    def loop_selected(self, count: int | None = None) -> bool:
        rows = self.selected_rows()
        if not rows:
            return False
        first, last = rows[0], rows[-1]
        if count is None:
            dlg = LoopDialog(self, first, last)
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dlg.accepted.connect(lambda: self.loop_selected(dlg.count.value()))
            self.dialogs["loop"] = dlg
            dlg.open()
            return False
        return self._edit(lambda: (sa.wrap_loop(self.seq, self.types, first, last, int(count)), rows)[1])

    def unloop_selected(self) -> bool:
        rows = self.selected_rows()
        if not rows:
            return False
        if not any(sa.loop_depth(sa.loops_of(self.seq), r) for r in rows):
            self.message.emit("The selected step is not inside a loop", "info")
            return False
        return self._edit(lambda: (sa.unwrap_loop(self.seq, rows[0]), rows)[1])

    def undo(self) -> None:
        if not self._undo or not self._can_edit():
            return
        self._redo.append(sa.snapshot(self.seq))
        self._restore(self._undo.pop())

    def redo(self) -> None:
        if not self._redo or not self._can_edit():
            return
        self._undo.append(sa.snapshot(self.seq))
        self._restore(self._redo.pop())

    def _restore(self, seq: Any) -> None:
        self.seq = seq
        self.model.set_sequence(seq)
        self._load_settings()
        self._changed()

    def _on_cell_edit(self, row: int, field: str, value: Any) -> bool:
        def do() -> list[int]:
            if field == "kind":
                old = sa.kind_of(sa.steps_of(self.seq)[row])
                kw: dict[str, Any] = {"kind": self.types.kind(value)}
                if {old, value} == {"travel", "load"} or value not in ("travel", "load"):
                    kw["target"] = None                  # mm ↔ N: the old number has no meaning
                sa.replace_step(self.seq, row, **kw)
            else:
                sa.replace_step(self.seq, row, **{field: value})
            return [row]
        return self._edit(do)

    def _set_attr(self, name: str, value: Any) -> None:
        if self.seq is None or sa.get(self.seq, name) == value or not hasattr(self.seq, name):
            return
        self._edit(lambda: setattr(self.seq, name, value))

    def _set_default(self, field: str, value: float) -> None:
        d = sa.get(self.seq, "defaults")
        if d is None or sa.get(d, field) == value:
            return

        def do() -> None:
            if dataclasses.is_dataclass(d):
                self.seq.defaults = dataclasses.replace(d, **{field: value})
            else:
                setattr(d, field, value)
        self._edit(do)

    def _on_advanced(self, on: bool) -> None:
        self.model.set_advanced(on)

    def _on_unit(self, _unit: str = "") -> None:
        self.model.refresh()
        self.model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, max(0, self.model.columnCount() - 1))

    # ------------------------------------------------------------------ backend validation + plan
    def revalidate(self) -> None:
        """``validate`` → cell colours + badge; ``expand`` + ``planned_path`` → plan summary + chart (backend)."""
        self._validate_timer.stop()
        seq = self.seq
        if seq is None:
            self.valid_badge.setText("● no sequence (sequencer unavailable)")
            self.valid_badge.setStyleSheet("color: #808080;")
            return
        steps = sa.steps_of(seq)
        try:
            issues = sa.cell_issues(self.seqr.validate(seq), steps)
        except Exception as exc:  # noqa: BLE001 - backend not ready
            issues = []
            self.valid_badge.setText(f"● validation unavailable: {_err_text(exc)}")
            self.valid_badge.setStyleSheet("color: #808080;")
        else:
            n_err = sum(1 for i in issues if i.severity == "ERROR")
            n_warn = len(issues) - n_err
            if n_err:
                self.valid_badge.setText(f"● {n_err} error(s)" + (f", {n_warn} warning(s)" if n_warn else ""))
                self.valid_badge.setStyleSheet("color: #c00000; font-weight: bold;")
            elif n_warn:
                self.valid_badge.setText(f"● valid, {n_warn} warning(s)")
                self.valid_badge.setStyleSheet("color: #a06000; font-weight: bold;")
            else:
                self.valid_badge.setText("● valid")
                self.valid_badge.setStyleSheet("color: #008000; font-weight: bold;")
        self.model.set_issues(issues)
        uids = {str(sa.get(s, "uid")) for s in steps}
        general = [i for i in issues if i.uid is None or i.uid not in uids]
        self.seq_issue_label.setText("\n".join(f"{i.severity}: {i.text}" for i in general))
        self.issues = issues
        self._update_plan()

    def _update_plan(self) -> None:
        try:
            self.plan = self.seqr.expand(self.seq)
            self.summary = sa.plan_summary(self.plan)
        except Exception as exc:  # noqa: BLE001 - invalid sequence / backend not ready
            self.plan, self.summary = None, None
            self.plan_label.setText(f"plan: {_err_text(exc)}")
        else:
            s = self.summary
            parts = [f"{len(sa.steps_of(self.seq))} steps, {s.n_exec} executed",
                     "total ~ " + (sa.fmt_hms(s.total_s) if s.total_s is not None else "∞ (loop until stopped)"),
                     f"{s.windows} capture windows"]
            if s.x_range:
                parts.append(f"x {s.x_range[0]:g}…{s.x_range[1]:g} mm")
            if s.f_range:
                lo, hi = force_unit().from_n(s.f_range[0]), force_unit().from_n(s.f_range[1])
                parts.append(f"F {lo:g}…{hi:g} {force_unit().unit}")
            self.plan_label.setText(" · ".join(parts))
        try:
            path = self.seqr.planned_path(self.seq)
            self.chart.set_path(sa.path_points(path, self.plan))
        except Exception:  # noqa: BLE001
            log.debug("planned_path failed", exc_info=True)
            self.chart.set_path([])

    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in getattr(self, "issues", ()))

    # ================================================================== files (SW-SEQF-001)
    def _confirm_discard(self, then: Any) -> None:
        if not self.dirty:
            then()
            return
        dlg = make_confirm(self, "C-08", text="Discard the unsaved changes of the sequence?",
                           confirm_label="Discard changes")
        dlg.confirmed.connect(then)
        self.confirm_dialog = dlg
        dlg.open()

    def new_sequence(self, confirm: bool = True) -> None:
        def do() -> None:
            try:
                seq = self.seqr.new()
            except Exception as exc:  # noqa: BLE001
                self.message.emit(f"New sequence: {_err_text(exc)}", "warn")
                seq = None
            self.set_sequence(seq)
        if confirm:
            self._confirm_discard(do)
        else:
            do()

    def open_file(self) -> None:
        self._confirm_discard(self._open_file)

    def _open_file(self) -> None:
        path = get_open_file_name(self, "Open sequence", "", SEQ_FILTER)
        if not path:
            return
        self.load_path(path)

    def load_path(self, path: str) -> bool:
        """``sequencer.load(path)``; any error → message, the current sequence stays unchanged (SW-SEQF-001)."""
        try:
            seq = self.seqr.load(path)
        except Exception as exc:  # noqa: BLE001 - FileFormatError / OSError
            self.message.emit(f"Sequence not loaded (current sequence unchanged): {_err_text(exc)}", "error")
            self._add_message(f"Open failed: {path}: {_err_text(exc)}", error=True)
            return False
        if seq is None:
            self.message.emit("Sequence not loaded (current sequence unchanged): empty result", "error")
            return False
        self.set_sequence(seq, path=path)
        self.message.emit(f"Sequence loaded: {path}", "info")
        return True

    def save_file(self, as_new: bool = False) -> bool:
        if self.seq is None:
            return False
        path = self.path
        if as_new or not path:
            path = get_save_file_name(self, "Save sequence", "", SEQ_FILTER, "bbseq.json")
            if not path:
                return False
        try:
            self.seqr.save(self.seq, path)
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Sequence not saved: {_err_text(exc)}", "error")
            return False
        self.path = path
        self.dirty = False
        self._update_title()
        self.message.emit(f"Sequence saved: {path}", "info")
        return True

    # ================================================================== generator (SW-WIZ-001/002)
    def open_generator(self) -> GeneratorDialog | None:
        if not self._can_edit():
            return None
        rows = self.selected_rows()
        dlg = GeneratorDialog(self.seqr, self.types, selected_row=rows[-1] if rows else None, parent=self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.insertRequested.connect(self.insert_generated)
        self.dialogs["generator"] = dlg
        dlg.show()
        return dlg

    def insert_generated(self, block: Any, mode: str, index: Any) -> None:
        def do() -> list[int]:
            before = len(sa.steps_of(self.seq))
            sa.insert_block(self.seq, self.types, block, mode, index)
            n = len(sa.steps_of(block))
            at = 0 if mode == "replace" else (int(index) if mode == "insert" and index is not None else before)
            return list(range(at, at + n))
        if mode == "replace" and self.dirty:
            dlg = make_confirm(self, "C-08", text="Replace all steps of the unsaved sequence with the generated "
                                                  "block?", confirm_label="Replace")
            dlg.confirmed.connect(lambda: self._edit(do))
            self.confirm_dialog = dlg
            dlg.open()
            return
        self._edit(do)

    # ================================================================== run controls
    def _start_gate(self) -> Any:
        return gating.gate_of(self._backend.status(), GateId.SEQUENCE_START)

    def on_start(self, confirmed: bool = False) -> None:
        if self.seq is None:
            return
        if not confirmed:
            check = getattr(self.seqr, "check_start", None)          # B6-06: static gate + sequence items
            try:
                gate = check(self.seq) if callable(check) else self._start_gate()
            except Exception as exc:  # noqa: BLE001
                self.message.emit(f"Sequence start check failed: {_err_text(exc)}", "error")
                return
            if gate is not None and gate.refused:
                self._show_refusal(gate)
                return
            if gate is not None and (gate.confirm_items or gate.warnings):
                self._confirm_start(gate)
                return
        try:
            g = self.seqr.start(self.seq, confirmed=confirmed)
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Sequence start failed: {_err_text(exc)}", "error")
            return
        codes = set(g.codes()) if hasattr(g, "codes") else set()
        if not confirmed and ("CONFIRMATION_REQUIRED" in codes or (g.confirm_items and not g.ok)):
            self._confirm_start(g)
            return
        if not g.ok:
            self._show_refusal(g)
            return
        self.chart.clear_run()
        self.step_results.clear()
        self._add_message("Sequence started" + (" (confirmed)" if confirmed else ""))
        if g.warnings:
            self.message.emit("; ".join(i.text for i in g.warnings), "warn")
        self.message.emit("Sequence started", "info")

    def _show_refusal(self, g: Any) -> None:
        """One message row per REFUSE item, verbatim (SW-SEQ-005: one item per reason)."""
        items = [i for i in g.refused if i.code != "CONFIRMATION_REQUIRED"]
        self.message.emit("Sequence start refused: " + "; ".join(i.text for i in items), "warn")
        for i in items:
            self._add_message(f"start refused [{i.code}]: {i.text}" + (f" — {i.clear_hint}" if i.clear_hint else ""),
                              error=True)

    def _confirm_start(self, gate: Any) -> None:
        items = [i for i in (*gate.confirm_items, *gate.warnings) if i.code != "CONFIRMATION_REQUIRED"]
        text = "\n".join(("• " if i in gate.confirm_items else "⚠ ") + i.text for i in items) or None
        dlg = make_confirm(self, "C-07", text=text)
        dlg.confirmed.connect(lambda: self.on_start(confirmed=True))
        self.confirm_dialog = dlg
        dlg.open()

    def on_pause(self) -> None:
        self.stopResult.emit(self._backend.pause("sequence"))

    def on_resume(self) -> None:
        self.resumeResult.emit(self._backend.resume("sequence"))

    def on_continue(self) -> None:
        fn = getattr(self.seqr, "continue_", None)
        if fn is None:
            self.message.emit("Continue is not available from the backend", "warn")
            return
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Continue refused: {_err_text(exc)}", "warn")

    def on_stop_sequence(self) -> None:
        self.stopResult.emit(self.seqr.stop())

    def on_abort(self) -> None:
        self.stopResult.emit(self.seqr.abort())

    # ================================================================== refresh
    def update_status(self, st: Any) -> None:
        self._ticks += 1
        try:
            raw = self.seqr.status()
        except Exception:  # noqa: BLE001
            raw = getattr(getattr(st, "operation", None), "sequence", None)
        n_exec = self.summary.n_exec if self.summary is not None else None
        self.view = sa.run_view(raw, n_exec)
        v = self.view
        line = sa.run_line(v)
        if self.run_label.text() != line:
            self.run_label.setText(line)
            self.run_label.setStyleSheet("color: #c00000; font-weight: bold;" if v.end_reason in
                                         ("NOT_REACHED", "DRIVER_ALARM") and not v.active else "")
        if v.state != self._prev_state:
            if v.state in sa.END_STATES and self._prev_state in sa.ACTIVE_STATES:
                self._add_message(f"Sequence ended: {v.state}" + (f" ({v.end_reason})" if v.end_reason else "")
                                  + (f" — {v.message}" if v.message else ""),
                                  error=v.state in ("ABORTED", "ERROR") or v.end_reason in ("NOT_REACHED",
                                                                                            "DRIVER_ALARM"))
            self._prev_state = v.state
        self.model.set_active_row(self._active_row(v) if v.active else None)
        self._update_gates(st, v)
        self._update_chart(v)

    def _active_row(self, v: sa.RunView) -> int | None:
        steps = sa.steps_of(self.seq)
        if v.step_uid is not None:
            for r, s in enumerate(steps):
                if str(sa.get(s, "uid")) == str(v.step_uid):
                    return r
        ps = sa.plan_steps(self.plan)
        if v.exec_idx is not None and 0 <= v.exec_idx < len(ps):
            si = sa.get(ps[v.exec_idx], "step_idx")
            if si is not None:
                return int(si)
        return v.exec_idx if v.exec_idx is not None and v.exec_idx < len(steps) else None

    def _update_gates(self, st: Any, v: sa.RunView) -> None:
        edit_gate = gating.gate_of(st, GateId.SEQUENCE_EDIT)
        can_edit = not v.active and (edit_gate is None or not edit_gate.refused)
        if self.model.read_only == can_edit:
            self.model.set_read_only(not can_edit)
        trig = EDIT_TRIGGERS if can_edit else QAbstractItemView.EditTrigger.NoEditTriggers
        if self.table.editTriggers() != trig:
            self.table.setEditTriggers(trig)
        self.settings_box.setEnabled(can_edit and self.seq is not None)
        self._update_edit_buttons()
        # Start: gate + idle + no validation error
        cs = gating.control_state(gating.gate_of(st, GateId.SEQUENCE_START), motion=True,
                                  base_tooltip="Start the sequence (recording starts automatically)")
        ok = cs.enabled and not v.active and self.seq is not None and not self.has_errors()
        tip = cs.tooltip + ("\nthe sequence has validation errors" if self.has_errors() else "")
        self._set(self.start_button, ok, tip)
        running = v.state in ("RUNNING", "WAITING_OPERATOR", "PREPARING")
        cs = gating.control_state(gating.gate_of(st, GateId.PAUSE), motion=True, base_tooltip="Pause (FW PAUSE)")
        self._set(self.pause_button, cs.enabled and running, cs.tooltip)
        cs = gating.control_state(gating.gate_of(st, GateId.RESUME), motion=True,
                                  base_tooltip="Resume: RESUME, then the interrupted step is re-issued")
        tip = cs.tooltip
        if cs.clear_first:
            tip = "Clear stop first: " + "; ".join(i.text for i in cs.clear_first) + "\n" + tip
        self._set(self.resume_button, cs.enabled and v.state == "PAUSED", tip)
        self._set(self.continue_button, v.state == "WAITING_OPERATOR" or v.phase == "WAIT_OPERATOR",
                  "Continue after a MARK step that waits for the operator")
        self._set(self.stop_seq_button, v.active, "Controlled stop: the sequence ends (STOPPED)")
        self._set(self.abort_button, v.active, "HALT (latched): the sequence ends ABORTED; Clear stop needed")

    @staticmethod
    def _set(b: QWidget, enabled: bool, tip: str) -> None:
        if b.isEnabled() != enabled:
            b.setEnabled(enabled)
        if b.toolTip() != tip:
            b.setToolTip(tip)

    def _update_edit_buttons(self) -> None:
        can = self._can_edit()
        rows = self.selected_rows() if can else []
        for b in (self.new_button, self.open_button, self.generate_button, self.add_button):
            b.setEnabled(can or (b in (self.new_button, self.open_button) and not self.view.active
                                 and self.seq is None))
        self.duplicate_button.setEnabled(can and bool(rows))
        self.delete_button.setEnabled(can and bool(rows))
        self.up_button.setEnabled(can and len(rows) == 1 and rows[0] > 0)
        self.down_button.setEnabled(can and len(rows) == 1 and rows[0] < self.model.rowCount() - 1)
        self.loop_button.setEnabled(can and bool(rows))
        self.unloop_button.setEnabled(can and bool(rows))
        self.undo_button.setEnabled(can and bool(self._undo))
        self.redo_button.setEnabled(can and bool(self._redo))
        self.save_button.setEnabled(self.seq is not None)
        self.save_as_button.setEnabled(self.seq is not None)

    def _update_chart(self, v: sa.RunView) -> None:
        """Live marker + point every tick (≥ 10 Hz, SW-SCH-002); trace every 3rd tick."""
        if not v.active and v.state not in sa.END_STATES:
            return
        data = self._backend.data
        x, f = v.marker_x, v.marker_f          # B6-07: live marker in the sequence coordinate (20 Hz)
        if x is None:
            ref = sa.text_of(sa.get(self.seq, "travel_ref", default="test"))
            for key in (("x_test_mm",) if ref == "test" else ()) + ("x_mm",):
                try:
                    x = data.latest(key).value
                except Exception:  # noqa: BLE001
                    continue
                break
        if f is None:
            try:
                f = data.latest("F_N").value
            except Exception:  # noqa: BLE001
                f = None
        trace = None
        if self._ticks % TRACE_EVERY == 0 or not v.active:
            try:
                tr = data.sequence_trace()
                trace = (tr.x, tr.y)
            except Exception:  # noqa: BLE001
                trace = None
        if v.active:
            self.chart.update_live(x if x is None or math.isfinite(x) else None, f, v.exec_idx, trace)
        elif trace is not None:
            self.chart.update_live(None, None, None, trace)

    # ================================================================== events
    def _on_seq_event(self, record: Any) -> None:
        topic = getattr(record, "topic", "")
        p = getattr(record, "payload", None)
        if topic == "seq.step_result":
            self.step_results.append(p)
            row = sa.result_row(p)
            flags = row["flags"]
            nr = "NOT_REACHED" in flags
            txt = (f"step {row['step']}" + (f" loop {row['loop']}" if row["loop"] not in ("", None) else "")
                   + (f" {row['label']}" if row["label"] else "") + ": " + (", ".join(flags) or "OK"))
            if row["F.mean"] is not None:
                txt += f"  F̄ {force_unit().from_n(float(row['F.mean'])):.2f} {force_unit().unit}"
            if nr:
                txt += " — load target not reached at the approach bound (axis stopped there; not a limit trip)"
                x = sa.get(p, "x_end_mm", "x_stop_mm", default=row["x.mean"])          # B6-08
                fx = sa.get(p, "f_end_n", "f_stop_n", default=row["F.mean"])
                x = x if sa.finite(x) else None
                fx = fx if sa.finite(fx) else None
                self.chart.mark_not_reached(x, fx, sa.get(p, "exec_idx"))
            self._add_message(txt, error=nr or any(f in ("BREAK_DETECTED", "TIMEOUT", "SLIP", "BOUND_NOT_AHEAD",
                                                         "NOT_ON_TARGET") for f in flags))
        elif topic == "seq.status" and p is not None:
            msg = str(sa.get(p, "message", default="") or "")
            if msg and msg != getattr(self, "_last_status_msg", ""):
                self._last_status_msg = msg
                self._add_message(msg)

    def _add_message(self, text: str, *, error: bool = False) -> None:
        it = QListWidgetItem(text)
        if error:
            it.setForeground(RED)
        self.messages.addItem(it)
        while self.messages.count() > MAX_MESSAGES:
            self.messages.takeItem(0)
        self.messages.scrollToBottom()

    def message_texts(self) -> list[str]:
        return [self.messages.item(i).text() for i in range(self.messages.count())]
