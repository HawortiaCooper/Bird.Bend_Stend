"""``StepTableModel``: the sequence step table (SW_design_GUI §3.6 editor; SW-SEQ-001/002).

The model shows the fields of the backend's ``Sequence.steps`` (via :mod:`gui.seq_access`): kind, target (mm for
travel, N / kgf display for load), speed, ramp, settle, capture, step time, tolerance, label (+ advanced columns
capture-in-move / wait-for-operator). Cells that do not apply to a kind show "–" and are read-only (display table
``seq_access.APPLIES``; LOAD steps have no step-time cell, B3-07). Each backend validation issue colours its cell
(red ERROR, amber WARN) and is the cell tooltip; the "!" column counts the issues of a step. The row header shows
the loop bracket (``┌×3`` … ``└``, ``∞`` = until stopped) and the step number; the active step of a running
sequence is highlighted.

Edits are parsed here (numbers; empty optional cell = sequence default) and handed to the tab's ``on_edit``
callback, which records the undo snapshot and applies the change on the model object. Nothing is range-checked
here: the backend's ``validate`` is the only rule source (P1).

Implements: SW-SEQ-001 (step table, field validation display), SW-SEQ-002 (loop bracket), SYS-003 (N / kgf
display of load targets)
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor

from bend_stand.gui import seq_access as sa
from bend_stand.gui.theme import RED_BG
from bend_stand.gui.units_state import force_unit

NA_CELL = "–"
ACTIVE_BG = QColor("#ffe6a0")
GREY_FG = QColor("#808080")
ISSUE_BG = {"ERROR": QColor(RED_BG), "WARN": QColor("#ffe0b0")}
LOAD_FIELDS = frozenset({"target", "tol_n"})          # force-valued cells (display unit)
SOFT_TEXTS = ("default", "0 (FW)")

_Index = QModelIndex | QPersistentModelIndex


class StepTableModel(QAbstractTableModel):
    parseError = Signal(str)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._seq: Any = None
        self._issues: list[sa.CellIssue] = []
        self._by_cell: dict[tuple[str | None, str | None], list[sa.CellIssue]] = {}
        self._advanced = False
        self._read_only = False
        self._active_row: int | None = None
        self.on_edit: Callable[[int, str, Any], bool] | None = None

    # ------------------------------------------------------------------ configuration
    def columns(self) -> tuple[tuple[str, str], ...]:
        return sa.FIELDS + (sa.ADVANCED if self._advanced else ())

    def field_at(self, col: int) -> str:
        return self.columns()[col][0]

    def column_of(self, field: str) -> int:
        return [f for f, _h in self.columns()].index(field)

    def set_sequence(self, seq: Any) -> None:
        self.beginResetModel()
        self._seq = seq
        self.endResetModel()

    def sequence(self) -> Any:
        return self._seq

    def refresh(self) -> None:
        """The sequence object was changed in place: redraw everything (structure may have changed)."""
        self.beginResetModel()
        self.endResetModel()

    def set_advanced(self, on: bool) -> None:
        if on != self._advanced:
            self.beginResetModel()
            self._advanced = on
            self.endResetModel()

    @property
    def advanced(self) -> bool:
        return self._advanced

    def set_read_only(self, on: bool) -> None:
        self._read_only = bool(on)

    @property
    def read_only(self) -> bool:
        return self._read_only

    def set_issues(self, issues: list[sa.CellIssue]) -> None:
        self._issues = list(issues)
        self._by_cell = {}
        for i in self._issues:
            self._by_cell.setdefault((i.uid, i.field), []).append(i)
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))
            self.headerDataChanged.emit(Qt.Orientation.Vertical, 0, self.rowCount() - 1)

    def issues(self) -> list[sa.CellIssue]:
        return list(self._issues)

    def step_issues(self, row: int) -> list[sa.CellIssue]:
        uid = self._uid(row)
        return [i for i in self._issues if i.uid is not None and i.uid == uid]

    def set_active_row(self, row: int | None) -> None:
        if row != self._active_row:
            old, self._active_row = self._active_row, row
            for r in (old, row):
                if r is not None and 0 <= r < self.rowCount():
                    self.dataChanged.emit(self.index(r, 0), self.index(r, self.columnCount() - 1))

    @property
    def active_row(self) -> int | None:
        return self._active_row

    # ------------------------------------------------------------------ helpers
    def _steps(self) -> list[Any]:
        return sa.steps_of(self._seq) if self._seq is not None else []

    def _uid(self, row: int) -> str | None:
        st = self._steps()
        if not 0 <= row < len(st):
            return None
        u = sa.get(st[row], "uid")
        return None if u is None else str(u)

    def applies(self, row: int, field: str) -> bool:
        if field in ("kind", "issues", "unit"):
            return True
        st = self._steps()
        return 0 <= row < len(st) and field in sa.APPLIES.get(sa.kind_of(st[row]), frozenset())

    def cell_issues(self, row: int, field: str) -> list[sa.CellIssue]:
        uid = self._uid(row)
        if field == "issues":
            return self.step_issues(row)
        out = list(self._by_cell.get((uid, field), ()))
        if field == "kind":                     # step-level issues (no field / unknown field) show on the type cell
            known = {f for f, _h in self.columns()}
            out += [i for i in self.step_issues(row) if i.field is None or i.field not in known]
        return out

    def display_value(self, row: int, field: str) -> str:
        step = self._steps()[row]
        kind = sa.kind_of(step)
        if field == "kind":
            return kind
        if field == "issues":
            n = len(self.step_issues(row))
            return f"! {n}" if n else ""
        if field == "unit":
            return {"travel": "mm", "load": force_unit().unit}.get(kind, "")
        if not self.applies(row, field):
            return NA_CELL
        v = sa.get(step, field)
        if field in sa.BOOL_FIELDS:
            return ""
        if field == "label":
            return str(v or "")
        if v is None:
            return "default" if field == "speed_mm_s" else ""
        if field in LOAD_FIELDS and kind == "load":
            fv = force_unit().from_n(float(v))
            return f"{fv:.3f}" if force_unit().unit == "kgf" else f"{fv:.2f}"
        if field == "target":
            return f"{float(v):.3f}"
        if field == "accel_mm_s2" and float(v) == 0.0:
            return "0 (FW)"
        return f"{float(v):g}"

    # ------------------------------------------------------------------ Qt model
    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._steps())

    def columnCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self.columns())

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                fld, h = self.columns()[section]
                return f"Tol {force_unit().unit}" if fld == "tol_n" else h
            g = sa.gutter_text(sa.loops_of(self._seq), section) if self._seq is not None else ""
            return f"{g} {section + 1}".strip()
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Vertical and self._seq is not None:
            loops = [sa.loop_span(lp) for lp in sa.loops_of(self._seq)
                     if sa.loop_span(lp)[0] <= section <= sa.loop_span(lp)[1]]
            return "\n".join(f"loop steps {a + 1}…{b + 1}: " + ("until stopped" if c == 0 else f"{c}×")
                             for a, b, c in loops) or None
        return None

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._steps()):
            return None
        row, field = index.row(), self.field_at(index.column())
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            v = self.display_value(row, field)
            if role == Qt.ItemDataRole.EditRole:
                return {"default": "", "0 (FW)": "0", NA_CELL: ""}.get(v, v)
            return v
        if role == Qt.ItemDataRole.CheckStateRole and field in sa.BOOL_FIELDS and self.applies(row, field):
            on = bool(sa.get(self._steps()[row], field, default=False))
            return Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.BackgroundRole:
            iss = self.cell_issues(row, field)
            if iss:
                return QBrush(ISSUE_BG["ERROR" if any(i.severity == "ERROR" for i in iss) else "WARN"])
            if row == self._active_row:
                return QBrush(ACTIVE_BG)
            return None
        if role == Qt.ItemDataRole.ForegroundRole:
            if not self.applies(row, field) or self.display_value(row, field) in SOFT_TEXTS:
                return QBrush(GREY_FG)
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            iss = self.cell_issues(row, field)
            if iss:
                return "\n".join(f"{i.severity}: {i.text}" for i in iss)
            if not self.applies(row, field):
                return "not used by this step type"
            return {"speed_mm_s": "empty = sequence default speed", "accel_mm_s2": "0 = FW default ramp"}.get(field)
        if role == Qt.ItemDataRole.TextAlignmentRole and field not in ("kind", "label", "issues", "unit"):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index: _Index) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        field = self.field_at(index.column())
        if self._read_only or field in ("issues", "unit") or not self.applies(index.row(), field):
            return f
        if field in sa.BOOL_FIELDS:
            return f | Qt.ItemFlag.ItemIsUserCheckable
        return f | Qt.ItemFlag.ItemIsEditable

    def setData(self, index: _Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if not index.isValid() or self._read_only or self.on_edit is None:
            return False
        row, field = index.row(), self.field_at(index.column())
        if not self.applies(row, field) or field in ("issues", "unit"):
            return False
        if field in sa.BOOL_FIELDS:
            if role != Qt.ItemDataRole.CheckStateRole:
                return False
            new: Any = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value, True)
        elif role != Qt.ItemDataRole.EditRole:
            return False
        else:
            try:
                new = self.parse(row, field, value)
            except ValueError as exc:
                self.parseError.emit(str(exc))
                return False
        ok = bool(self.on_edit(row, field, new))
        if ok:
            self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))
        return ok

    def parse(self, row: int, field: str, value: Any) -> Any:
        """Edit text → model value (N for load cells; empty optional cell → None; empty other cell → 0)."""
        if field == "kind":
            v = str(value).strip().lower()
            if v not in sa.STEP_KINDS:
                raise ValueError(f"unknown step type {value!r}")
            return v
        if field == "label":
            return str(value)
        text = str(value).strip().replace(",", ".")
        if text == "":
            return None if field in sa.OPTIONAL_FIELDS else 0.0
        try:
            x = float(text)
        except ValueError:
            raise ValueError(f"{field}: not a number: {value!r}") from None
        if field in LOAD_FIELDS and sa.kind_of(self._steps()[row]) == "load":
            x = force_unit().to_n(x)
        return x
