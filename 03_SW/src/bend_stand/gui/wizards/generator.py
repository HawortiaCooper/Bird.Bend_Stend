"""``GeneratorDialog``: the sequence generator wizard, pages G1…G3 (SW_design_GUI §6.5; SW-WIZ-001/002).

* **G1 Choose** — one radio per backend ``GeneratorSchema`` (staircase, linear ramp, cyclic / triangle, hold /
  creep–relaxation, return; whatever ``sequencer.generator_schemas()`` lists, re-read when the dialog opens).
* **G2 Parameters** — :class:`SchemaForm` from the schema; [Continue] calls the backend generator
  (``seq_access.generate``); a ``ValueError`` is shown verbatim at the form.
* **G3 Preview & insert** — the generated block (step list), a mini sequence chart (``planned_path`` of a temporary
  sequence holding only the block), the summary from ``expand`` (executed steps, duration, travel / load range) and
  the block's validation issues; insert mode append / insert after the selected row / replace all.
  [Insert] emits :attr:`insertRequested` (block, mode, index); the Sequence tab confirms a replace of an unsaved
  sequence (C-08) and inserts with ``Sequence.insert_block`` — the steps stay editable (SW-WIZ-002).

No motion is caused, but the STOP bar is present (uniform rule, SafeDialog).

Implements: SW-WIZ-001 (generator wizards from the backend schemas), SW-WIZ-002 (preview, append / insert /
replace), SW-SCH-001 (preview chart), SW-STOP-001 (STOP in the dialog)
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bend_stand.gui import seq_access as sa
from bend_stand.gui.dialogs.safe_dialog import SafeDialog
from bend_stand.gui.plots.sequence_chart import SequenceChart
from bend_stand.gui.wizards.schema_form import SchemaForm

log = logging.getLogger(__name__)

PREVIEW_COLUMNS = (("kind", "Type"), ("target", "Target"), ("speed_mm_s", "v mm/s"), ("settle_s", "Settle s"),
                   ("capture_s", "Capture s"), ("step_time_s", "Time s"), ("label", "Label"))


def _err_text(exc: BaseException) -> str:
    return str(getattr(exc, "user_text", "") or exc or type(exc).__name__)


class GeneratorDialog(SafeDialog):
    insertRequested = Signal(object, str, object)        # block, mode (append / insert / replace), index | None

    def __init__(self, sequencer: Any, types: sa.SeqTypes, *, selected_row: int | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, title="Sequence generator")
        self.setObjectName("generatorDialog")
        self._seqr = sequencer
        self._types = types
        self._selected_row = selected_row
        self.schemas = sa.schemas(sequencer)
        self.block: Any = None
        self.form: SchemaForm | None = None
        lay = QVBoxLayout()
        self.step_label = QLabel("", self)
        self.step_label.setStyleSheet("font-weight: bold;")
        lay.addWidget(self.step_label)
        self.pages = QStackedWidget(self)
        lay.addWidget(self.pages, 1)
        # G1
        g1 = QWidget(self.pages)
        g1l = QVBoxLayout(g1)
        self.choice = QButtonGroup(g1)
        self.radios: dict[str, QRadioButton] = {}
        for i, sch in enumerate(self.schemas):
            name = str(sa.get(sch, "name"))
            desc = str(sa.get(sch, "description", "help", default="") or "")
            rb = QRadioButton(str(sa.get(sch, "label", default=name)) + (f" — {desc}" if desc else ""), g1)
            rb.setObjectName(f"gen_{name}")
            self.choice.addButton(rb, i)
            self.radios[name] = rb
            g1l.addWidget(rb)
        if not self.schemas:
            g1l.addWidget(QLabel("No generators available from the backend (generator_schemas() is empty).", g1))
        elif self.choice.buttons():
            self.choice.buttons()[0].setChecked(True)
        g1l.addStretch(1)
        self.pages.addWidget(g1)
        # G2
        self.g2 = QWidget(self.pages)
        self.g2_layout = QVBoxLayout(self.g2)
        self.pages.addWidget(self.g2)
        # G3
        g3 = QWidget(self.pages)
        g3l = QHBoxLayout(g3)
        left = QVBoxLayout()
        self.preview = QTableWidget(0, len(PREVIEW_COLUMNS), g3)
        self.preview.setObjectName("generatorPreview")
        self.preview.setHorizontalHeaderLabels([h for _f, h in PREVIEW_COLUMNS])
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        left.addWidget(self.preview, 1)
        self.summary = QLabel("", g3)
        self.summary.setObjectName("generatorSummary")
        self.summary.setWordWrap(True)
        left.addWidget(self.summary)
        self.issues_label = QLabel("", g3)
        self.issues_label.setWordWrap(True)
        left.addWidget(self.issues_label)
        mode_row = QHBoxLayout()
        self.mode_group = QButtonGroup(g3)
        self.mode_append = QRadioButton("append", g3)
        self.mode_insert = QRadioButton("insert after the selected row" + (f" ({selected_row + 1})"
                                                                           if selected_row is not None else ""), g3)
        self.mode_replace = QRadioButton("replace all", g3)
        for i, rb in enumerate((self.mode_append, self.mode_insert, self.mode_replace)):
            self.mode_group.addButton(rb, i)
            mode_row.addWidget(rb)
        self.mode_insert.setEnabled(selected_row is not None)
        self.mode_append.setChecked(True)
        left.addLayout(mode_row)
        g3l.addLayout(left, 3)
        self.mini_chart = SequenceChart(g3)
        self.mini_chart.setMinimumWidth(260)
        g3l.addWidget(self.mini_chart, 2)
        self.pages.addWidget(g3)
        # buttons
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.back_button = QPushButton("Change parameters", self)
        self.next_button = QPushButton("Continue", self)
        self.insert_button = QPushButton("Insert", self)
        for b in (self.cancel_button, self.back_button, self.next_button, self.insert_button):
            b.setAutoDefault(False)
            b.setDefault(False)
            row.addWidget(b)
        lay.addLayout(row)
        self.setLayout(lay)
        self.cancel_button.clicked.connect(self.reject)
        self.back_button.clicked.connect(self.back)
        self.next_button.clicked.connect(self.next)
        self.insert_button.clicked.connect(self.insert)
        self.resize(820, 520)
        self._show_page(0)

    # ------------------------------------------------------------------ navigation
    def page(self) -> int:
        return self.pages.currentIndex()

    def _show_page(self, i: int) -> None:
        self.pages.setCurrentIndex(i)
        self.step_label.setText(("G1 Choose a generator", "G2 Parameters", "G3 Preview & insert")[i])
        self.back_button.setVisible(i > 0)
        self.back_button.setText("Back" if i == 1 else "Change parameters")
        self.next_button.setVisible(i < 2)
        self.next_button.setEnabled(bool(self.schemas))
        self.insert_button.setVisible(i == 2)

    def selected_schema(self) -> Any:
        i = self.choice.checkedId()
        return self.schemas[i] if 0 <= i < len(self.schemas) else None

    def choose(self, name: str) -> None:
        rb = self.radios.get(name)
        if rb is not None:
            rb.setChecked(True)

    def next(self) -> None:
        if self.page() == 0:
            sch = self.selected_schema()
            if sch is None:
                return
            if self.form is None or self.form.schema is not sch:
                if self.form is not None:
                    self.form.setParent(None)
                    self.form.deleteLater()
                self.form = SchemaForm(sch, self.g2)
                self.g2_layout.addWidget(self.form)
            self._show_page(1)
        elif self.page() == 1:
            self.generate()

    def back(self) -> None:
        self._show_page(max(0, self.page() - 1))

    # ------------------------------------------------------------------ generate + preview
    def generate(self) -> bool:
        sch = self.selected_schema()
        if sch is None or self.form is None:
            return False
        try:
            block = sa.generate(self._seqr, str(sa.get(sch, "name")), self.form.values())
        except Exception as exc:  # noqa: BLE001 - ValueError text verbatim at the field
            self.form.show_error(_err_text(exc))
            return False
        self.block = block
        self._fill_preview(block)
        self._show_page(2)
        return True

    def _fill_preview(self, block: Any) -> None:
        steps = sa.steps_of(block)
        self.preview.setRowCount(len(steps))
        for r, s in enumerate(steps):
            for c, (f, _h) in enumerate(PREVIEW_COLUMNS):
                v = sa.kind_of(s) if f == "kind" else sa.get(s, f)
                txt = "" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))
                self.preview.setItem(r, c, QTableWidgetItem(txt))
        lines = [f"{len(steps)} steps"]
        loops = sa.loops_of(block)
        if loops:
            lines.append(", ".join(f"loop {a + 1}…{b + 1} " + ("until stopped" if c == 0 else f"×{c}")
                                   for a, b, c in (sa.loop_span(lp) for lp in loops)))
        temp = self._temp_sequence(block)
        issues: list[Any] = []
        if temp is not None:
            try:
                plan = self._seqr.expand(temp)
                summ = sa.plan_summary(plan)
                lines.append(f"{summ.n_exec} executed steps, ~ " + (sa.fmt_hms(summ.total_s) if summ.total_s is not None
                                                                  else "∞ (loop until stopped)"))
                if summ.x_range:
                    lines.append(f"x {summ.x_range[0]:g}…{summ.x_range[1]:g} mm")
                if summ.f_range:
                    lines.append(f"F {summ.f_range[0]:g}…{summ.f_range[1]:g} N")
                self.mini_chart.set_path(sa.path_points(self._seqr.planned_path(temp), plan))
            except Exception as exc:  # noqa: BLE001 - preview only
                lines.append(f"(plan preview unavailable: {_err_text(exc)})")
            try:
                issues = sa.cell_issues(self._seqr.validate(temp), sa.steps_of(temp))
            except Exception:  # noqa: BLE001
                log.debug("validate of the preview failed", exc_info=True)
        self.summary.setText("; ".join(lines))
        if issues:
            self.issues_label.setText("\n".join(f"(!) {i.severity}: {i.text}" for i in issues[:8])
                                      + (f"\n… {len(issues) - 8} more" if len(issues) > 8 else ""))
            self.issues_label.setStyleSheet("color: #a05000;")
        else:
            self.issues_label.setText("(!) none")
            self.issues_label.setStyleSheet("color: #206020;")

    def _temp_sequence(self, block: Any) -> Any:
        try:
            temp = self._seqr.new()
        except Exception:  # noqa: BLE001
            return None
        if temp is None:
            return None
        try:
            sa.insert_block(temp, self._types, block, "replace")
        except Exception:  # noqa: BLE001
            log.debug("temporary preview sequence failed", exc_info=True)
            return None
        return temp

    # ------------------------------------------------------------------ insert
    def mode(self) -> str:
        return {0: "append", 1: "insert", 2: "replace"}.get(self.mode_group.checkedId(), "append")

    def insert(self) -> None:
        if self.block is None:
            return
        mode = self.mode()
        index = (self._selected_row + 1) if mode == "insert" and self._selected_row is not None else None
        self.insertRequested.emit(self.block, mode, index)
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Enter never inserts (no default button)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)
