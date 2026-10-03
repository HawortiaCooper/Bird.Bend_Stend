"""``ParamForm``: board-configuration form generated from the dictionary (SW_design_GUI §3.1).

The metadata comes from the backend (``config.metas()`` / ``config.groups()`` = generated ``params_gen``
``ParamMeta`` / ``GROUPS``; the GUI does not import ``params_gen``). One group node per ``groups()`` entry, one row
per parameter with a typed editor per ``ParamMeta.type``:

* BOOL → QCheckBox · ENUM → QComboBox (``enum`` order; text ``enum_labels`` / NAME; data = code)
* U8/I8/U16/I16/I32 → QSpinBox(min, max) · U32 → 64-bit-safe line edit · F32 → QDoubleSpinBox(decimals)
* keyboard tracking off; editing never writes — Write & verify is the Connection tab's explicit action.

Columns: Parameter | Edit | Board | Default | Unit | Range | Status | Flags (M = moving_ok, N = NVM, R =
reboot-required, adv = advanced). Session parameters (``config.locked_keys()``: ``safety.load_raw_min/max``,
``safety.zero_raw``) are **read-only rows** "session value – managed by the threshold manager" and are never part
of the edits or the configuration file (SW-CFG-003).

Rule marking: the tab runs ``config.check(edits)`` (debounced) and pushes the ``Issue`` list in with
:meth:`set_issues` (no rule logic here). Write results arrive with :meth:`apply_verify_report`
(``WriteItem.status`` OK / REJECTED / MISMATCH / BUSY / TIMEOUT / NOT_ATTEMPTED / REBOOT_REQUIRED / UNCHANGED).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/widgets/param_form.py @37c87471 (adapted: metadata from the
backend, flags M/N/R, locked session rows, Issue-based rule marking, bend-stand WriteStatus texts; removed
fw_owned / temperature-probe / percent-centi handling and the core.params imports).

Implements: SW-CFG-001 (typed fields from the dictionary: unit, range, enum names, board, default, edit),
SW-CFG-002 (edit-column fill from a file), SW-CFG-003 (rule marking, per-parameter status incl.
REBOOT_REQUIRED, session rows read-only)
"""
from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QBrush, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bend_stand.gui.theme import CELL_BG

COL_NAME, COL_EDIT, COL_BOARD, COL_DEFAULT, COL_UNIT, COL_RANGE, COL_STATUS, COL_FLAGS = range(8)
HEADERS = ("Parameter", "Edit", "Board", "Default", "Unit", "Range", "Status", "Flags")
KEY_ROLE = Qt.ItemDataRole.UserRole + 1

LOCKED_TEXT = "session value – managed by the threshold manager (SAF-SW-002) – see Safety limits"

STATUS_TEXT = {
    "OK": "✔ OK",
    "UNCHANGED": "✔ unchanged",
    "REJECTED": "✖ REJECTED",
    "MISMATCH": "✖ MISMATCH",
    "BUSY": "⏸ BUSY",
    "TIMEOUT": "✖ TIMEOUT",
    "NOT_ATTEMPTED": "– NOT_ATTEMPTED",
    "REBOOT_REQUIRED": "↻ REBOOT_REQUIRED",
}
STATUS_LEVEL = {"OK": "ok", "UNCHANGED": "ok", "REBOOT_REQUIRED": "warn", "BUSY": "warn",
                "NOT_ATTEMPTED": "warn"}

_INT_TYPES = ("U8", "I8", "U16", "I16", "I32")


def type_name(meta: Any) -> str:
    t = getattr(meta, "type", "")
    return str(getattr(t, "name", t))


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def normalize(meta: Any, value: Any) -> int | float | bool:
    """Canonical value for comparison (bool / int code / binary32-rounded float)."""
    t = type_name(meta)
    if t == "BOOL":
        return bool(value)
    if t == "F32":
        return _f32(float(value))
    return int(value)


def format_value(meta: Any, value: Any) -> str:
    if value is None:
        return "–"
    t = type_name(meta)
    if t == "BOOL":
        return "true" if value else "false"
    if t == "ENUM":
        return (getattr(meta, "enum", None) or {}).get(int(value), f"? ({int(value)})")
    if t == "F32":
        dec = meta.decimals if getattr(meta, "decimals", None) is not None else 6
        return f"{float(value):.{dec}f}"
    return str(int(value))


def format_range(meta: Any) -> str:
    t = type_name(meta)
    if t == "BOOL":
        return "bool"
    if t == "ENUM":
        return ",".join((getattr(meta, "enum", None) or {}).values())
    if t == "F32":
        dec = meta.decimals if getattr(meta, "decimals", None) is not None else 6
        return f"{float(meta.min):.{dec}f} … {float(meta.max):.{dec}f}"
    return f"{int(meta.min)} … {int(meta.max)}"


def flag_badges(meta: Any) -> str:
    badges = []
    if getattr(meta, "moving_ok", False):
        badges.append("M")
    if getattr(meta, "nvm", True):
        badges.append("N")
    if getattr(meta, "reboot_required", False):
        badges.append("R")
    if getattr(meta, "advanced", False):
        badges.append("adv")
    return " ".join(badges)


def tooltip_for(meta: Any) -> str:
    lines = [f"{meta.label or meta.key}", f"key {meta.key}, id 0x{int(meta.id):04X}", str(meta.description)]
    if getattr(meta, "moving_ok", False):
        lines.append("M: may be changed while moving")
    if getattr(meta, "nvm", True):
        lines.append("N: stored in NVM by Save to NVM")
    else:
        lines.append("RAM only (not stored in NVM)")
    if getattr(meta, "reboot_required", False):
        lines.append("R: effective after Save to NVM + Reboot")
    if getattr(meta, "srs", ()):
        lines.append("Refs: " + ", ".join(meta.srs))
    return "\n".join(lines)


class U32Edit(QLineEdit):
    """64-bit-safe integer line edit for U32 values (QSpinBox is limited to int32)."""

    valueChanged = Signal(int)

    def __init__(self, meta: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._meta = meta
        self._value = int(meta.default)
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,10}"), self))
        self.setText(str(self._value))
        self.editingFinished.connect(self._commit)
        self.textEdited.connect(self._on_text)

    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:  # noqa: N802 - Qt naming
        self._value = int(v)
        self.setText(str(self._value))
        self.setStyleSheet("")

    def _parse(self) -> int | None:
        t = self.text().strip()
        if not t:
            return None
        v = int(t)
        return v if self._meta.min <= v <= self._meta.max else None

    def _on_text(self, _t: str) -> None:
        v = self._parse()
        self.setStyleSheet("" if v is not None else "background-color: #ffc8c8;")
        if v is not None and v != self._value:
            self._value = v
            self.valueChanged.emit(v)

    def _commit(self) -> None:
        if self._parse() is None:
            self.setValue(self._value)


class _Row:
    __slots__ = ("meta", "item", "editor", "board", "status", "status_detail", "issues", "locked")

    def __init__(self, meta: Any, item: QTreeWidgetItem, editor: QWidget, locked: bool) -> None:
        self.meta = meta
        self.item = item
        self.editor = editor
        self.board: int | float | bool | None = None
        self.status: str | None = None
        self.status_detail = ""
        self.issues: list[Any] = []
        self.locked = locked


class ParamForm(QWidget):
    """Generated parameter form. See module docstring."""

    dirtyChanged = Signal(int)
    valueEdited = Signal(str)

    def __init__(self, metas: Iterable[Any], groups: Iterable[tuple[str, str, Sequence[str]]],
                 locked_keys: Iterable[str] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._by_key: dict[str, Any] = {m.key: m for m in metas}
        self._groups = tuple(groups)
        self._locked = frozenset(locked_keys)
        self._rows: dict[str, _Row] = {}
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._read_only = False
        self._last_dirty = 0
        self._global_issues: list[Any] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.banner = QLabel(self)
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background-color: #ffc8c8; padding: 4px;")
        self.banner.hide()
        lay.addWidget(self.banner)

        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Filter", self))
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("key or label, e.g. motion")
        self.filter_edit.setClearButtonEnabled(True)
        fbar.addWidget(self.filter_edit, 1)
        self.only_changed = QCheckBox("only changed", self)
        self.show_advanced = QCheckBox("show advanced", self)
        fbar.addWidget(self.only_changed)
        fbar.addWidget(self.show_advanced)
        lay.addLayout(fbar)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(len(HEADERS))
        self.tree.setHeaderLabels(list(HEADERS))
        self.tree.setUniformRowHeights(True)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Interactive)
        hdr.resizeSection(COL_NAME, 240)
        hdr.resizeSection(COL_EDIT, 190)
        lay.addWidget(self.tree, 1)

        self._build()
        self.filter_edit.textChanged.connect(self.apply_filter)
        self.show_advanced.toggled.connect(self.apply_filter)
        self.only_changed.toggled.connect(self.apply_filter)
        self.apply_filter()

    # ------------------------------------------------------------------ construction
    def _build(self) -> None:
        seen: set[str] = set()
        for gkey, glabel, keys in self._groups:
            gitem = QTreeWidgetItem(self.tree, [glabel])
            gitem.setData(COL_NAME, KEY_ROLE, None)
            f = gitem.font(COL_NAME)
            f.setBold(True)
            gitem.setFont(COL_NAME, f)
            gitem.setFirstColumnSpanned(True)
            self._group_items[gkey] = gitem
            for key in keys:
                if key in self._by_key:
                    self._add_row(gitem, self._by_key[key])
                    seen.add(key)
            gitem.setExpanded(True)
        rest = [m for k, m in self._by_key.items() if k not in seen]
        if rest:                                     # parameters outside every group (defensive)
            gitem = QTreeWidgetItem(self.tree, ["Other"])
            self._group_items["other"] = gitem
            for m in rest:
                self._add_row(gitem, m)
            gitem.setExpanded(True)

    def _add_row(self, parent: QTreeWidgetItem, meta: Any) -> None:
        locked = meta.key in self._locked
        item = QTreeWidgetItem(parent)
        item.setText(COL_NAME, meta.key)
        item.setData(COL_NAME, KEY_ROLE, meta.key)
        item.setText(COL_BOARD, "–")
        item.setText(COL_DEFAULT, format_value(meta, meta.default))
        item.setText(COL_UNIT, meta.unit or "-")
        item.setText(COL_RANGE, format_range(meta))
        item.setText(COL_FLAGS, flag_badges(meta))
        tip = tooltip_for(meta)
        for c in range(len(HEADERS)):
            item.setToolTip(c, tip)
        editor = self._make_editor(meta, locked)
        editor.setToolTip(LOCKED_TEXT if locked else tip)
        self.tree.setItemWidget(item, COL_EDIT, editor)
        self._rows[meta.key] = _Row(meta, item, editor, locked)
        if not locked:
            self._set_editor_value(meta.key, meta.default)
        self._refresh_row(meta.key)

    def _make_editor(self, meta: Any, locked: bool) -> QWidget:
        key = meta.key
        t = type_name(meta)
        if locked:
            ed = QLineEdit(self.tree)
            ed.setReadOnly(True)
            ed.setEnabled(False)
            ed.setPlaceholderText("(locked)")
            return ed
        if t == "BOOL":
            ed = QCheckBox(self.tree)
            ed.toggled.connect(lambda _v, k=key: self._on_edit(k))
            return ed
        if t == "ENUM":
            ed = QComboBox(self.tree)
            labels = getattr(meta, "enum_labels", None) or {}
            for code, name in (meta.enum or {}).items():
                ed.addItem(name, code)
                ed.setItemData(ed.count() - 1, labels.get(code, name), Qt.ItemDataRole.ToolTipRole)
            ed.currentIndexChanged.connect(lambda _i, k=key: self._on_edit(k))
            return ed
        if t == "F32":
            ed = QDoubleSpinBox(self.tree)
            ed.setDecimals(meta.decimals if getattr(meta, "decimals", None) is not None else 6)
            ed.setRange(float(meta.min), float(meta.max))
            ed.setKeyboardTracking(False)
            ed.valueChanged.connect(lambda _v, k=key: self._on_edit(k))
            return ed
        if t == "U32":
            ed = U32Edit(meta, self.tree)
            ed.valueChanged.connect(lambda _v, k=key: self._on_edit(k))
            return ed
        if t in _INT_TYPES:
            ed = QSpinBox(self.tree)
            ed.setRange(int(meta.min), int(meta.max))
            ed.setKeyboardTracking(False)
            ed.valueChanged.connect(lambda _v, k=key: self._on_edit(k))
            return ed
        raise TypeError(f"{meta.key}: unsupported type {t!r}")  # pragma: no cover

    # ------------------------------------------------------------------ editor value access
    def _set_editor_value(self, key: str, value: Any) -> None:
        row = self._rows[key]
        ed = row.editor
        ed.blockSignals(True)
        try:
            if row.locked:
                ed.setText(format_value(row.meta, value) if value is not None else "")
            elif isinstance(ed, QCheckBox):
                ed.setChecked(bool(value))
            elif isinstance(ed, QComboBox):
                idx = ed.findData(int(value))
                ed.setCurrentIndex(idx if idx >= 0 else 0)
            elif isinstance(ed, QDoubleSpinBox):
                ed.setValue(float(value))
            elif isinstance(ed, (QSpinBox, U32Edit)):
                ed.setValue(int(value))
        finally:
            ed.blockSignals(False)

    def editor_value(self, key: str) -> int | float | bool:
        """Current value of the edit column in the canonical (stored) representation."""
        row = self._rows[key]
        ed = row.editor
        if row.locked:
            return row.board if row.board is not None else row.meta.default
        if isinstance(ed, QCheckBox):
            return ed.isChecked()
        if isinstance(ed, QComboBox):
            return int(ed.currentData())
        if isinstance(ed, QDoubleSpinBox):
            return _f32(ed.value())
        return int(ed.value())

    def set_edit_value(self, key: str, value: Any) -> None:
        """Programmatic edit (as if typed): marks the row dirty when it differs. Locked rows are refused."""
        row = self._rows[key]
        if row.locked:
            raise PermissionError(f"{key} is a session value (read-only)")
        self._set_editor_value(key, value)
        self._on_edit(key)

    # ------------------------------------------------------------------ state
    def reference_value(self, key: str) -> int | float | bool:
        row = self._rows[key]
        return row.board if row.board is not None else row.meta.default

    def is_dirty(self, key: str) -> bool:
        row = self._rows[key]
        if row.locked:
            return False
        try:
            return normalize(row.meta, self.editor_value(key)) != normalize(row.meta, self.reference_value(key))
        except (TypeError, ValueError):
            return True

    def dirty_keys(self) -> list[str]:
        return [k for k in self._rows if self.is_dirty(k)]

    def edited_config(self) -> dict[str, int | float | bool]:
        """Changed, writable parameters {key: value} (locked session rows never included)."""
        return {k: self.editor_value(k) for k in self.dirty_keys()}

    def file_values(self) -> dict[str, int | float | bool]:
        """Edit-column configuration for a board-config file (locked session rows excluded, SW-CFG-003)."""
        return {k: self.editor_value(k) for k, r in self._rows.items() if not r.locked}

    def board_value(self, key: str) -> Any:
        return self._rows[key].board

    def status_of(self, key: str) -> str | None:
        return self._rows[key].status

    def status_text(self, key: str) -> str:
        return self._rows[key].item.text(COL_STATUS)

    def keys(self) -> list[str]:
        return list(self._rows)

    def locked_keys(self) -> frozenset[str]:
        return frozenset(k for k, r in self._rows.items() if r.locked)

    def editor_for(self, key: str) -> QWidget:
        return self._rows[key].editor

    def item_for(self, key: str) -> QTreeWidgetItem:
        return self._rows[key].item

    def is_editable(self, key: str) -> bool:
        row = self._rows[key]
        return not row.locked and row.editor.isEnabled()

    # ------------------------------------------------------------------ updates from the backend
    def set_board_values(self, values: Mapping[str, Any], *, reset_edits: bool = False) -> None:
        """Board column update after a read. Rows without an edit follow the board; edits are kept unless
        ``reset_edits``."""
        for key, value in values.items():
            row = self._rows.get(key)
            if row is None or value is None:
                continue
            was_dirty = self.is_dirty(key)
            try:
                row.board = normalize(row.meta, value)
            except (TypeError, ValueError):
                continue
            row.item.setText(COL_BOARD, format_value(row.meta, row.board))
            if row.locked or reset_edits or not was_dirty:
                self._set_editor_value(key, row.board)
            self._refresh_row(key)
        self._emit_dirty()
        self.apply_filter()

    def load_values(self, values: Mapping[str, Any]) -> list[str]:
        """Fill the edit column from a file (SW-CFG-002); locked / unknown / out-of-range keys skipped (their keys
        are returned). Nothing is written until Write & verify."""
        skipped = []
        for key, value in values.items():
            row = self._rows.get(key)
            if row is None or row.locked:
                skipped.append(key)
                continue
            try:
                if not row.meta.in_range(value):
                    skipped.append(key)
                    continue
            except (TypeError, ValueError, AttributeError):
                skipped.append(key)
                continue
            self._set_editor_value(key, value)
            row.status, row.status_detail = None, ""
            self._refresh_row(key)
        self._emit_dirty()
        self.apply_filter()
        return skipped

    def clear_statuses(self) -> None:
        for key, row in self._rows.items():
            row.status, row.status_detail = None, ""
            self._refresh_row(key)

    def apply_verify_report(self, report: Any) -> dict[str, int]:
        """Per-parameter results of ``config.write_and_verify_async`` (``VerifyReport``). Board := stored value."""
        counts: dict[str, int] = {}
        stored: dict[str, Any] = {}
        for it in getattr(report, "items", ()):
            status = str(getattr(getattr(it, "status", None), "value", getattr(it, "status", "")))
            counts[status] = counts.get(status, 0) + 1
            row = self._rows.get(it.key)
            if row is None:
                continue
            row.status = status
            detail = [f"requested {it.requested!r}, stored {getattr(it, 'stored', None)!r}"]
            if getattr(it, "text", ""):
                detail.insert(0, it.text)
            if getattr(it, "nack_status", None) is not None:
                detail.append(f"NACK status {it.nack_status} detail {it.nack_detail}")
            row.status_detail = "; ".join(detail)
            if getattr(it, "stored", None) is not None:
                stored[it.key] = it.stored
        if stored:
            self.set_board_values(stored, reset_edits=False)
        for key in self._rows:
            self._refresh_row(key)
        self._emit_dirty()
        return counts

    def set_issues(self, issues: Iterable[Any]) -> None:
        """Rule-check result (``config.check``): per-key issues mark rows; key None → global list."""
        before = {k: [i.code for i in r.issues] for k, r in self._rows.items()}
        for r in self._rows.values():
            r.issues = []
        self._global_issues = []
        for i in issues:
            row = self._rows.get(i.key) if i.key else None
            (row.issues if row is not None else self._global_issues).append(i)
        for k, r in self._rows.items():
            if [i.code for i in r.issues] != before.get(k):
                self._refresh_row(k)

    def issues(self) -> list[Any]:
        return [i for r in self._rows.values() for i in r.issues] + list(self._global_issues)

    def error_issues(self) -> list[Any]:
        return [i for i in self.issues() if str(getattr(i.severity, "value", i.severity)) == "ERROR"]

    # ------------------------------------------------------------------ modes
    def set_read_only(self, read_only: bool, reason: str = "") -> None:
        if read_only == self._read_only and (not read_only or self.banner.text() == reason):
            return
        self._read_only = read_only
        self.banner.setText(reason)
        self.banner.setVisible(read_only and bool(reason))
        for row in self._rows.values():
            row.editor.setEnabled(not read_only and not row.locked)

    @property
    def read_only(self) -> bool:
        return self._read_only

    def revert_edits(self) -> None:
        for key in self.dirty_keys():
            self._set_editor_value(key, self.reference_value(key))
            self._refresh_row(key)
        self._emit_dirty()
        self.apply_filter()

    # ------------------------------------------------------------------ presentation
    def _on_edit(self, key: str) -> None:
        row = self._rows[key]
        row.status, row.status_detail = None, ""
        self._refresh_row(key)
        self._emit_dirty()
        self.valueEdited.emit(key)
        if self.only_changed.isChecked():
            self.apply_filter()

    def _emit_dirty(self) -> None:
        n = len(self.dirty_keys())
        if n != self._last_dirty:
            self._last_dirty = n
            self.dirtyChanged.emit(n)

    def _refresh_row(self, key: str) -> None:
        row = self._rows[key]
        item = row.item
        dirty = self.is_dirty(key)
        errors = [i for i in row.issues if str(getattr(i.severity, "value", i.severity)) == "ERROR"]
        warns = [i for i in row.issues if i not in errors]
        if row.locked:
            text, color = LOCKED_TEXT, CELL_BG["locked"]
        elif errors:
            text, color = "✖ " + " ".join(sorted({i.code for i in errors})), CELL_BG["error"]
        elif row.status is not None:
            text = STATUS_TEXT.get(row.status, row.status)
            color = CELL_BG[STATUS_LEVEL.get(row.status, "error")]
            if dirty and row.status in ("OK", "UNCHANGED"):
                text, color = "✎", CELL_BG["dirty"]
        elif dirty:
            text, color = "✎", CELL_BG["dirty"]
        elif warns:
            text, color = "⚠ " + " ".join(sorted({i.code for i in warns})), CELL_BG["warn"]
        elif row.board is not None:
            text, color = "✔", CELL_BG["ok"]
        else:
            text, color = "", CELL_BG["ok"]
        item.setText(COL_STATUS, text)
        tips = [f"{i.code}: {i.text}" for i in row.issues]
        if row.status_detail:
            tips.append(row.status_detail)
        item.setToolTip(COL_STATUS, "\n".join(tips) or text)
        brush = QBrush(color) if color.alpha() else QBrush()
        for c in range(len(HEADERS)):
            if c != COL_EDIT:
                item.setBackground(c, brush)
        f = item.font(COL_NAME)
        f.setItalic(dirty)
        item.setFont(COL_NAME, f)

    def apply_filter(self, *_args: Any) -> None:
        text = self.filter_edit.text().strip().lower()
        adv = self.show_advanced.isChecked()
        only = self.only_changed.isChecked()
        for gitem in self._group_items.values():
            visible = 0
            for i in range(gitem.childCount()):
                child = gitem.child(i)
                key = child.data(COL_NAME, KEY_ROLE)
                row = self._rows[key]
                show = True
                if getattr(row.meta, "advanced", False) and not adv and not self.is_dirty(key):
                    show = False
                label = str(getattr(row.meta, "label", "")).lower()
                if text and text not in key.lower() and text not in label:
                    show = False
                if only and not (self.is_dirty(key) or row.status not in (None, "OK", "UNCHANGED")):
                    show = False
                child.setHidden(not show)
                visible += show
            gitem.setHidden(visible == 0)

    def is_row_visible(self, key: str) -> bool:
        item = self._rows[key].item
        return not item.isHidden() and not item.parent().isHidden()
