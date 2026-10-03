"""Grid of plot panes with 1..4 columns, row-major flow and drag & drop reordering (SW_design_GUI §4.7, D-38).

``PaneGrid`` only lays out and hit-tests: the dock owns the pane list and decides what a drop means
(:attr:`PaneGrid.drop_handler`). Drop semantics: dropping onto cell *i* inserts the pane at position *i*
(the others shift); dropping below / after the last pane appends. While dragging, a blue frame (the drop
indicator) marks the target cell.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/plots/pane_grid.py @37c8747 (copied unchanged except this
docstring and the import path).

Implements: SW-RT-006 (column selector layout, drag & drop reorder, drop onto another plot window)
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QWidget

from bend_stand.gui.plots.plot_pane import PlotPane, pane_from_mime

COLUMN_CHOICES = (1, 2, 3, 4)


class DropIndicator(QFrame):
    """Overlay frame marking the cell a dragged pane will be inserted at."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("paneDropIndicator")
        self.setStyleSheet("#paneDropIndicator { border: 3px solid #1f6fd6; "
                           "background: rgba(31, 111, 214, 40); }")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()


class PaneGrid(QWidget):
    """Lays out ``PlotPane`` widgets row-major in ``columns`` equal columns / equal rows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)
        self._panes: list[PlotPane] = []
        self._columns = 1
        self._rows_used = 0
        self._cols_used = 0
        self.indicator = DropIndicator(self)
        self.drop_index: int | None = None          # target index while a drag hovers (tests / indicator)
        # drop_handler(pane, index) -> bool: the dock inserts / moves the pane; set by the owner
        self.drop_handler: Callable[[PlotPane, int], bool] | None = None
        # same_owner(pane) -> bool: True if the pane already belongs to this grid's dock
        self.owns: Callable[[PlotPane], bool] = lambda p: p in self._panes

    # ---------------------------------------------------------------- layout
    @property
    def columns(self) -> int:
        return self._columns

    def panes(self) -> list[PlotPane]:
        return list(self._panes)

    def set_panes(self, panes: Sequence[PlotPane], columns: int | None = None) -> None:
        if columns is not None:
            if columns not in COLUMN_CHOICES:
                raise ValueError(f"columns {columns} not in {COLUMN_CHOICES}")
            self._columns = columns
        for p in self._panes:
            if p not in panes:
                self._layout.removeWidget(p)
        self._panes = list(panes)
        for p in self._panes:
            self._layout.removeWidget(p)
        cols = self._columns
        n = len(self._panes)
        rows = max(1, math.ceil(n / cols))
        for i, p in enumerate(self._panes):
            if p.parent() is not self:
                p.setParent(self)
            self._layout.addWidget(p, i // cols, i % cols)
            p.show()
        for r in range(max(rows, self._rows_used)):
            self._layout.setRowStretch(r, 1 if r < rows else 0)
            self._layout.setRowMinimumHeight(r, 0)
        for c in range(max(COLUMN_CHOICES[-1], self._cols_used)):
            self._layout.setColumnStretch(c, 1 if c < cols else 0)
        self._rows_used, self._cols_used = rows, cols
        self.indicator.raise_()

    def position_of(self, pane: PlotPane) -> tuple[int, int]:
        """(row, column) of a pane in the grid."""
        i = self._panes.index(pane)
        return i // self._columns, i % self._columns

    # ---------------------------------------------------------------- hit test
    def _cell_rect(self, index: int) -> QRect:
        if 0 <= index < len(self._panes):
            return self._panes[index].geometry()
        cols = self._columns
        rows = max(1, math.ceil(max(len(self._panes), index + 1) / cols))
        cr = self.contentsRect()
        w, h = cr.width() / cols, cr.height() / rows
        r, c = index // cols, index % cols
        return QRect(int(cr.x() + c * w), int(cr.y() + r * h), int(w), int(h))

    def index_at(self, pos: QPoint) -> int:
        """Insert position for a drop at ``pos``: the index of the cell under the cursor, or the pane
        count (append) for the empty area after the last pane."""
        n = len(self._panes)
        for i in range(n):
            if self._panes[i].geometry().contains(pos):
                return i
        cols = self._columns
        cr = self.contentsRect()
        rows = max(1, math.ceil(n / cols))
        if cr.width() <= 0 or cr.height() <= 0:
            return n
        c = min(cols - 1, max(0, int((pos.x() - cr.x()) * cols / cr.width())))
        r = min(rows - 1, max(0, int((pos.y() - cr.y()) * rows / cr.height())))
        return min(n, r * cols + c)

    def target_index(self, pane: PlotPane, pos: QPoint) -> int:
        idx = self.index_at(pos)
        if self.owns(pane):
            idx = min(idx, len(self._panes) - 1)       # reorder: final position of the pane
        return idx

    def show_indicator(self, index: int) -> None:
        self.drop_index = index
        rect = self._cell_rect(index)
        if index >= len(self._panes) and index % self._columns == 0 and self._panes:
            # append into a new row: show a strip along the bottom edge
            last = self._cell_rect(len(self._panes) - 1)
            rect = QRect(self.contentsRect().x(), last.bottom() - 8, self.contentsRect().width(), 12)
        self.indicator.setGeometry(rect)
        self.indicator.show()
        self.indicator.raise_()

    def hide_indicator(self) -> None:
        self.drop_index = None
        self.indicator.hide()

    # ---------------------------------------------------------------- drag & drop
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        pane = pane_from_mime(event.mimeData())
        if pane is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.show_indicator(self.target_index(pane, event.position().toPoint()))

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        pane = pane_from_mime(event.mimeData())
        if pane is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.show_indicator(self.target_index(pane, event.position().toPoint()))

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.hide_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        pane = pane_from_mime(event.mimeData())
        self.hide_indicator()
        if pane is None or self.drop_handler is None:
            event.ignore()
            return
        index = self.target_index(pane, event.position().toPoint())
        if self.drop_handler(pane, index):
            event.acceptProposedAction()
        else:
            event.ignore()
