"""``ChannelTree``: checkbox per channel, tri-state group nodes, colour swatch per channel (SW_design_GUI §4.2).

* Content comes **only** from the backend registry (``backend.channels.channels()`` → ``ChannelSpec`` key, label,
  unit, group, available, reason); the tree re-reads it on the topic ``channels.changed`` (no polling).
* ``available = False`` → item disabled (grey) with ``reason`` as tooltip; a ticked channel that becomes
  unavailable stays ticked and reads "(n/a)".
* Status-bit channels show the generated ``*_DESC`` text as tooltip (P8).
* ``batch()`` reports a group tick as one ``checkedChanged``.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/widgets/channel_tree.py @37c87471 (adapted: ChannelSpec
fields, no feature gating / pane column / context menu, colour swatches, "(n/a)" label, generated bit tooltips).

Implements: SW-RT-002 (every registry channel switchable, greyed prerequisites), SW-RT-004 (derived channels
listed from the registry)
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

from bend_stand.core import protocol_gen as pg
from bend_stand.gui.theme import curve_color

KEY_ROLE = Qt.ItemDataRole.UserRole + 1
BIT_NAMES: frozenset[str] = frozenset(n for n in (*pg.DATA_FLAGS_BITS, *pg.DATA_STATUS_BITS) if n)


def bit_name(spec: Any) -> str | None:
    """Generated bit name of a status-bit channel, else None. Convention: the last key segment (after '.' or ':')
    upper-cased is a ``DATA_FLAGS_BITS`` / ``DATA_STATUS_BITS`` name (API request GRQ-B-20 to confirm)."""
    key = str(getattr(spec, "key", ""))
    name = key.replace(":", ".").split(".")[-1].upper()
    return name if name in BIT_NAMES else None


def _swatch(color: str) -> QIcon:
    pm = QPixmap(10, 10)
    pm.fill(QColor(color))
    return QIcon(pm)


class _GroupItem(QTreeWidgetItem):
    """Tri-state group: ticking ticks only the enabled children, as one ``checkedChanged``."""

    def setData(self, column: int, role: int, value: Any) -> None:  # noqa: N802 - Qt override
        tree = self.treeWidget()
        if column != 0 or role != Qt.ItemDataRole.CheckStateRole or not isinstance(tree, ChannelTree) \
                or self.childCount() == 0:
            super().setData(column, role, value)
            return
        state = value if isinstance(value, Qt.CheckState) else Qt.CheckState(int(value))
        if state == Qt.CheckState.PartiallyChecked:
            return
        enabled = [self.child(i) for i in range(self.childCount()) if not self.child(i).isDisabled()]
        if state == Qt.CheckState.Checked and enabled and all(
                c.checkState(0) == Qt.CheckState.Checked for c in enabled):
            state = Qt.CheckState.Unchecked
        with tree.batch():
            for c in [self.child(i) for i in range(self.childCount())]:
                if state == Qt.CheckState.Checked and c.isDisabled():
                    continue
                c.setCheckState(0, state)


class ChannelTree(QTreeWidget):
    checkedChanged = Signal(list)       # checked keys in tree order

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(["Channel", "Unit"])
        self.setColumnCount(2)
        self.setUniformRowHeights(True)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._groups: dict[str, QTreeWidgetItem] = {}
        self._specs: dict[str, Any] = {}
        self._colors: dict[str, str] = {}
        self._emitting = True
        self.itemChanged.connect(self._on_item_changed)

    def set_channels(self, channels: Iterable[Any], checked: Iterable[str] = ()) -> None:
        keep = set(checked) | set(self.checked_keys())
        expanded = {g for g, it in self._groups.items() if it.isExpanded()}
        self._emitting = False
        try:
            self.clear()
            self._items.clear()
            self._groups.clear()
            self._specs.clear()
            for spec in channels:
                group = getattr(spec, "group", "") or "Other"
                gitem = self._groups.get(group)
                if gitem is None:
                    gitem = _GroupItem(self, [group])
                    gitem.setFlags(gitem.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                    gitem.setCheckState(0, Qt.CheckState.Unchecked)
                    self._groups[group] = gitem
                key = spec.key
                color = self._colors.setdefault(key, curve_color(len(self._colors)))
                available = bool(getattr(spec, "available", True))
                label = getattr(spec, "label", "") or key
                item = QTreeWidgetItem(gitem, [label if available or key not in keep else f"{label} (n/a)",
                                               getattr(spec, "unit", "") or ""])
                item.setData(0, KEY_ROLE, key)
                item.setIcon(0, _swatch(color))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if key in keep else Qt.CheckState.Unchecked)
                bn = bit_name(spec)
                tip = f"{key}"
                if bn:
                    tip += f" — {bn}: {pg.DATA_FLAGS_DESC.get(bn) or pg.DATA_STATUS_DESC.get(bn, '')}"
                if not available:
                    item.setDisabled(True)
                    tip = f"{key}: {getattr(spec, 'reason', None) or 'not available'}"
                item.setToolTip(0, tip)
                self._items[key] = item
                self._specs[key] = spec
            for g, it in self._groups.items():
                it.setExpanded(g in expanded or g.upper().startswith("DATA"))
        finally:
            self._emitting = True
        self.checkedChanged.emit(self.checked_keys())

    # ---------------------------------------------------------------- access
    def all_keys(self) -> list[str]:
        return list(self._items)

    def spec(self, key: str) -> Any:
        return self._specs[key]

    def color(self, key: str) -> str:
        return self._colors.get(key, curve_color(0))

    def item(self, key: str) -> QTreeWidgetItem:
        return self._items[key]

    def is_available(self, key: str) -> bool:
        return not self._items[key].isDisabled()

    def checked_keys(self) -> list[str]:
        return [k for k, it in self._items.items() if it.checkState(0) == Qt.CheckState.Checked]

    def set_checked(self, key: str, checked: bool = True) -> None:
        self._items[key].setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def group_names(self) -> list[str]:
        return list(self._groups)

    def set_group_checked(self, group: str, checked: bool = True) -> None:
        self._groups[group].setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    @contextmanager
    def batch(self) -> Iterator[None]:
        outer = self._emitting
        before = self.checked_keys() if outer else None
        self._emitting = False
        try:
            yield
        finally:
            self._emitting = outer
        if outer:
            after = self.checked_keys()
            if after != before:
                self.checkedChanged.emit(after)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0 and self._emitting and item.data(0, KEY_ROLE):
            self.checkedChanged.emit(self.checked_keys())
