"""``PlotDock(SafeDock)``: one realtime plot window with a grid of plot panes (SW_design_GUI §4.1, §4.7; D-38).

```
+-- Plot 1 ----------------------------------------- [NO-SPECIMEN] [Float] [Close] [## STOP ##] -+
| [☰ Channels] [Freeze] Window [30 s v] [x] Autoscale  [+ Pane] [+ X-Y pane]  Cols [1][2][3][4]  |
| channel tree (Channel | Unit | Pane) | PaneGrid: Pane 1 · Raw counts | Pane 2 · Status bits | …  |
```

Pane model (Thrust_Stand D-62 / D-63, adopted by D-38): every time pane owns its curves; a channel appears in at
most one pane of a window. **Default placement:** a ticked channel goes to the first pane (grid order) that already
shows its quantity group (:func:`~.quantity.quantity_group`) and has an axis for its unit; otherwise the first
empty pane is reused; otherwise a new pane is appended. Unticking removes only the curve; an emptied pane stays and
is reused. A tree-group tick ticks only the available children and costs one grid layout. Manual placement wins:
channel menu "Plot in / move to pane N" / "New pane", pane menu "Move curve", drag & drop. Panes flow row-major
through 1..4 columns (order kept when the column count changes); dragging a pane by its title strip onto another
cell inserts it there; dropping it onto another plot window (or "Move to window…") moves it with its curves. The
time axis is linked inside a window (relative axis [−window, 0]). :class:`~.plot_pane.XYPane` is the
time-independent travel–load pane type.

Data: the main window's single 33 ms refresh calls ``data.snapshot(keys, window_s, px_width)`` **once per distinct
time-window length** for the union of the channels of all shown, non-frozen windows and hands the result to
:meth:`PlotDock.set_snapshot` (§4.6, Thrust_Stand D-62); X-Y panes pull ``data.xy`` themselves
(:meth:`refresh_xy`). After the data of all panes is set, the views are committed so each pane paints once.

Layout (columns, pane order, curves per pane, X-Y choices, titles, window, autoscale) → :meth:`layout_state`,
persisted by the main window in QSettings with the dock arrangement.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/plots/plot_dock.py @37c8747 (adapted: SafeDock base with the
title-bar STOP / float / close / NO-SPECIMEN tag, Freeze instead of Pause (no confusion with the motion PAUSE),
PlotSnapshot data path with the shared refresh, X-Y pane type, bend-stand quantities; removed: feature gating,
D-50 default ranges).

Implements: SW-RT-006 (pane grid, placement rules, reorder, move to window, rename / close, X link, X-Y pane,
persistence, one snapshot per time window), SW-RT-001 (dock / float / re-attach, own STOP), SW-RT-002, SW-RT-003
(time view 5–600 s, freeze, auto / manual Y), NFR-001 (perf design §4.6)
"""
from __future__ import annotations

import logging
import time
import weakref
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace
from typing import Any

import pyqtgraph as pg
import shiboken6
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bend_stand.gui.plots.pane_grid import COLUMN_CHOICES, PaneGrid
from bend_stand.gui.plots.plot_pane import BIT_UNIT, PlotPane, XYPane, pane_from_mime
from bend_stand.gui.plots.quantity import BITS, quantity_group
from bend_stand.gui.units_state import force_unit
from bend_stand.gui.widgets.channel_tree import ChannelTree, bit_name
from bend_stand.gui.widgets.safe_dock import SafeDock

log = logging.getLogger(__name__)

WINDOWS_S = (5, 10, 30, 60, 120, 300, 600)
DEFAULT_WINDOW_S = 30
MIN_PX = 64
MAX_PX = 4096
PX_PER_BUCKET = 2               # logical pixels per min/max column of the snapshot (Thrust_Stand SWD-PM3-05)
LAYOUT_VERSION = 1
XY_X_UNITS = ("mm", "µm", "um")

_CONFIGURED = False
_LIVE_DOCKS: "weakref.WeakSet[PlotDock]" = weakref.WeakSet()


def configure_pyqtgraph() -> None:
    """Binding pyqtgraph settings (§4.6 rule 1), once per process."""
    global _CONFIGURED
    if not _CONFIGURED:
        pg.setConfigOptions(antialias=False, useOpenGL=False, background="w", foreground="k")
        _CONFIGURED = True


class _DockBody(QWidget):
    """Dock content; a pane of another window dropped outside the grid (tree, toolbar) is appended."""

    def __init__(self, dock: "PlotDock") -> None:
        super().__init__(dock)
        self._dock = dock
        self.setAcceptDrops(True)

    def _foreign(self, event) -> PlotPane | None:
        pane = pane_from_mime(event.mimeData())
        return pane if pane is not None and pane not in self._dock.panes() else None

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._foreign(event) is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self._dock.grid.show_indicator(len(self._dock.panes()))

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._dock.grid.hide_indicator()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._dock.grid.hide_indicator()
        pane = self._foreign(event)
        if pane is None:
            event.ignore()
            return
        if self._dock.drop_pane(pane, len(self._dock.panes())):
            event.acceptProposedAction()


class PlotDock(SafeDock):
    infoMessage = Signal(str)
    panesChanged = Signal(object)           # self (pane added/removed/moved, curves moved, columns)

    def __init__(self, title: str = "Plot 1", parent: QWidget | None = None, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        configure_pyqtgraph()
        super().__init__(title, parent, source=f"plot:{title}")
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self._clock = clock
        self.frozen = False
        self.window_s = float(DEFAULT_WINDOW_S)
        self.updates = 0
        self.last_snapshot: Any = None
        self._panes: list[PlotPane] = []
        self._selected: PlotPane | None = None
        self._key_pane: dict[str, PlotPane] = {}
        self._pending: dict[str, tuple[PlotPane, int]] = {}   # restore / "plot in pane" / channel vanished
        self._syncing = False
        self._xlinking = False
        self.peers: Callable[[], list[PlotDock]] = self._live_peers
        self.new_dock_factory: Callable[[], PlotDock | None] | None = None
        _LIVE_DOCKS.add(self)

        body = _DockBody(self)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(2, 2, 2, 2)
        bar = QHBoxLayout()
        self.tree_button = QToolButton(body)
        self.tree_button.setText("☰ Channels")
        self.tree_button.setCheckable(True)
        self.tree_button.setChecked(True)
        self.tree_button.toggled.connect(self.set_tree_visible)
        bar.addWidget(self.tree_button)
        self.freeze_box = QCheckBox("Freeze", body)
        self.freeze_box.setToolTip("Stop updating this window (pan / zoom with the mouse); the recording continues")
        self.freeze_box.toggled.connect(self.set_frozen)
        bar.addWidget(self.freeze_box)
        bar.addWidget(QLabel("Window", body))
        self.window_combo = QComboBox(body)
        self.window_combo.setEditable(True)
        for w in WINDOWS_S:
            self.window_combo.addItem(f"{w} s", float(w))
        self.window_combo.setCurrentIndex(WINDOWS_S.index(DEFAULT_WINDOW_S))
        self.window_combo.activated.connect(lambda i: self.set_window_s(float(self.window_combo.itemData(i))))
        self.window_combo.lineEdit().editingFinished.connect(self._on_window_text)
        bar.addWidget(self.window_combo)
        self.autoscale_check = QCheckBox("Autoscale", body)
        self.autoscale_check.setChecked(True)
        self.autoscale_check.setToolTip("Y follows the data; off = manual Y (zoom / pan with the mouse)")
        self.autoscale_check.toggled.connect(self._on_autoscale)
        bar.addWidget(self.autoscale_check)
        self.add_pane_button = QToolButton(body)
        self.add_pane_button.setText("+ Pane")
        self.add_pane_button.setToolTip(
            "Add an empty plot pane. Newly ticked channels join the pane that shows their quantity, else the first "
            "empty pane; move channels via the channel menu (right click in the tree) or the pane menu")
        self.add_pane_button.clicked.connect(lambda: self.add_pane(select=True))
        bar.addWidget(self.add_pane_button)
        self.add_xy_button = QToolButton(body)
        self.add_xy_button.setText("+ X-Y pane")
        self.add_xy_button.setToolTip("Add a travel–load (X-Y) pane, time-independent")
        self.add_xy_button.clicked.connect(lambda: self.add_xy_pane(select=True))
        bar.addWidget(self.add_xy_button)
        bar.addWidget(QLabel("Cols", body))
        self.column_group = QButtonGroup(body)
        self.column_group.setExclusive(True)
        self.column_buttons: dict[int, QToolButton] = {}
        for n in COLUMN_CHOICES:
            b = QToolButton(body)
            b.setText(str(n))
            b.setCheckable(True)
            b.setToolTip(f"Arrange the panes in {n} column{'s' if n > 1 else ''} (row by row)")
            self.column_group.addButton(b, n)
            self.column_buttons[n] = b
            bar.addWidget(b)
        self.column_buttons[1].setChecked(True)
        self.column_group.idClicked.connect(self.set_columns)
        bar.addStretch(1)
        self.status_label = QLabel("", body)
        self.status_label.setStyleSheet("color: #606060;")
        bar.addWidget(self.status_label)
        outer.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal, body)
        self.tree = ChannelTree(split)
        self.grid = PaneGrid(split)
        self.grid.drop_handler = self.drop_pane
        self.grid.owns = lambda p: p in self._panes
        split.addWidget(self.tree)
        split.addWidget(self.grid)
        self.tree.setMinimumWidth(0)
        self.tree.setColumnWidth(0, 140)
        self.tree.setColumnWidth(1, 45)
        self.tree.setColumnWidth(2, 32)
        self.grid.setMinimumWidth(200)
        split.setCollapsible(0, True)
        split.setCollapsible(1, False)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([210, 600])
        self.splitter = split
        outer.addWidget(split, 1)
        self.setWidget(body)

        self.add_pane(select=True)
        self.tree.checkedChanged.connect(self._on_tree_checked)
        self.tree.channelContextMenu.connect(self._on_channel_menu)

    def _live_peers(self) -> list["PlotDock"]:
        live = [d for d in list(_LIVE_DOCKS) if d is not self and shiboken6.isValid(d)]
        return sorted(live, key=lambda d: d.windowTitle())

    # ---------------------------------------------------------------- controls
    def set_tree_visible(self, visible: bool) -> None:
        if self.tree_button.isChecked() != visible:
            self.tree_button.setChecked(visible)
        self.tree.setVisible(visible)

    def _on_window_text(self) -> None:
        text = self.window_combo.currentText().replace("s", "").strip()
        try:
            self.set_window_s(float(text))
        except ValueError:
            self.window_combo.setEditText(f"{self.window_s:g} s")

    def set_window_s(self, seconds: float) -> None:
        """5–600 s (SW-RT-003); out-of-range values are clamped. All time panes show [−window, 0]."""
        s = min(max(float(seconds), float(WINDOWS_S[0])), float(WINDOWS_S[-1]))
        self.window_s = s
        self.window_combo.setEditText(f"{s:g} s")
        self._set_x_range(-s, 0.0)
        self.panesChanged.emit(self)

    def set_frozen(self, frozen: bool) -> None:
        self.frozen = bool(frozen)
        if self.freeze_box.isChecked() != self.frozen:
            self.freeze_box.setChecked(self.frozen)
        self.freeze_box.setText("Freeze (frozen)" if self.frozen else "Freeze")

    @property
    def autoscale(self) -> bool:
        return self.autoscale_check.isChecked()

    def _on_autoscale(self, on: bool) -> None:
        for p in self._panes:
            p.set_autoscale(bool(on))
        self.panesChanged.emit(self)

    # ---------------------------------------------------------------- channels
    def set_channels(self, specs: Any, checked: Sequence[str] = ()) -> None:
        self.tree.set_channels(specs, checked)
        self._check_pending()
        self._update_xy_choices()
        self._update_pane_labels()

    def checked_keys(self) -> list[str]:
        return self.tree.checked_keys()

    def _spec_text(self, key: str) -> tuple[str, str]:
        try:
            spec = self.tree.spec(key)
        except KeyError:
            return key, ""
        if bit_name(spec):
            return bit_name(spec) or key, BIT_UNIT
        return (getattr(spec, "label", "") or key), (getattr(spec, "unit", "") or "")

    def quantity(self, key: str) -> str:
        try:
            return quantity_group(self.tree.spec(key))
        except KeyError:
            return quantity_group(SimpleNamespace(key=key))

    # ---------------------------------------------------------------- panes
    def panes(self) -> list[PlotPane]:
        return list(self._panes)

    def time_panes(self) -> list[PlotPane]:
        return [p for p in self._panes if p.kind == "time"]

    def xy_panes(self) -> list[XYPane]:
        return [p for p in self._panes if isinstance(p, XYPane)]

    def pane(self, index: int) -> PlotPane:
        return self._panes[index]

    def pane_of(self, key: str) -> PlotPane | None:
        return self._key_pane.get(key)

    @property
    def selected_pane(self) -> PlotPane:
        if self._selected not in self._panes:
            self._selected = self._panes[0]
        return self._selected

    def select_pane(self, pane: PlotPane) -> None:
        if pane not in self._panes:
            return
        self._selected = pane
        for p in self._panes:
            p.set_selected(p is pane)

    @property
    def columns(self) -> int:
        return self.grid.columns

    def set_columns(self, n: int) -> None:
        """Grid columns 1..4; pane order is kept (row-major flow)."""
        n = int(n)
        if n not in COLUMN_CHOICES:
            raise ValueError(f"columns {n} not in {COLUMN_CHOICES}")
        if not self.column_buttons[n].isChecked():
            self.column_buttons[n].setChecked(True)
        if n != self.grid.columns:
            self.grid.set_panes(self._panes, n)
            self._relayout()

    def add_pane(self, index: int | None = None, select: bool = False) -> PlotPane:
        pane = self._new_pane(index)
        if select or self._selected is None:
            self.select_pane(pane)
        self._relayout()
        return pane

    def add_xy_pane(self, index: int | None = None, select: bool = False, x: str | None = None,
                    y: str | None = None) -> XYPane:
        pane = XYPane(self.grid, self._clock)
        self._attach(pane, len(self._panes) if index is None else index)
        self._update_xy_choices(pane, x, y)
        if select:
            self.select_pane(pane)
        self._relayout()
        return pane

    def _new_pane(self, index: int | None = None) -> PlotPane:
        """Create + attach a pane **without** a layout update (callers relayout once)."""
        pane = PlotPane(self.grid, self._clock)
        self._attach(pane, len(self._panes) if index is None else index)
        return pane

    def _attach(self, pane: PlotPane, index: int) -> None:
        pane.owner = self
        pane.selectRequested.connect(self.select_pane)
        pane.closeRequested.connect(self.close_pane)
        pane.contextMenuRequested.connect(self._on_pane_menu)
        pane.titleChanged.connect(self._on_pane_title)
        if pane.kind == "time":
            pane.plot_item.vb.sigXRangeChanged.connect(self._on_pane_xrange)
            pane.set_x_range(-self.window_s, 0.0)
        else:
            pane.selectionChanged.connect(self._on_pane_title)
        pane.set_autoscale(self.autoscale)
        self._panes.insert(max(0, min(index, len(self._panes))), pane)

    def _detach(self, pane: PlotPane) -> None:
        pairs = [(pane.selectRequested, self.select_pane), (pane.closeRequested, self.close_pane),
                 (pane.contextMenuRequested, self._on_pane_menu), (pane.titleChanged, self._on_pane_title)]
        if pane.kind == "time":
            pairs.append((pane.plot_item.vb.sigXRangeChanged, self._on_pane_xrange))
        else:
            pairs.append((pane.selectionChanged, self._on_pane_title))
        for sig, slot in pairs:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._panes.remove(pane)
        for k in [k for k, (p, _) in self._pending.items() if p is pane]:
            del self._pending[k]
        if self._selected is pane:
            self._selected = None

    def _on_pane_title(self, pane: PlotPane) -> None:
        self.panesChanged.emit(self)

    def rename_pane(self, pane: PlotPane, name: str | None) -> None:
        if pane in self._panes:
            pane.set_custom_title(name)

    def close_pane(self, pane: PlotPane) -> None:
        """Close a pane: its channels are unticked. The last pane of a window cannot be closed."""
        if pane not in self._panes or len(self._panes) <= 1:
            return
        keys = pane.keys()
        for k in keys:
            self._key_pane.pop(k, None)
        self._set_tree_checked(keys, False)
        pane.clear_curves()
        self._detach(pane)
        self._dispose(pane)
        self._relayout()

    @staticmethod
    def _dispose(pane: PlotPane) -> None:
        pane.dispose()
        pane.setParent(None)
        pane.deleteLater()

    def move_pane(self, pane: PlotPane, index: int) -> None:
        """Reorder within this window: the pane ends at ``index``, the others shift."""
        if pane not in self._panes:
            raise ValueError("pane not in this window")
        self._panes.remove(pane)
        self._panes.insert(max(0, min(index, len(self._panes))), pane)
        self._relayout()

    def move_pane_to(self, pane: PlotPane, target: "PlotDock", index: int | None = None) -> None:
        """Move a pane with its curves to another plot window ("Move to window…", drag & drop)."""
        if target is self:
            if index is not None:
                self.move_pane(pane, index)
            return
        if pane not in self._panes:
            raise ValueError("pane not in this window")
        keys = pane.keys()
        for k in keys:
            self._key_pane.pop(k, None)
        self._set_tree_checked(keys, False)
        self._detach(pane)
        if not self._panes:
            self.add_pane(select=True)
        self._relayout()
        target._receive(pane, index)

    def _receive(self, pane: PlotPane, index: int | None) -> None:
        first = self._panes[0] if len(self._panes) == 1 else None
        replace = first if first is not None and first.kind == "time" and not first.keys() \
            and first.custom_title is None and not any(p is first for p, _ in self._pending.values()) else None
        dropped = []
        for k in pane.keys():
            if k in self._key_pane or k not in self.tree.all_keys():
                pane.remove_curve(k)             # a channel is drawn once per window
                dropped.append(k)
        pane.setParent(self.grid)
        self._attach(pane, len(self._panes) if index is None else index)
        for k in pane.keys():
            self._key_pane[k] = pane
        self._set_tree_checked(pane.keys(), True)
        if isinstance(pane, XYPane):
            self._update_xy_choices(pane)
        if replace is not None:
            self._detach(replace)
            self._dispose(replace)
        self.select_pane(pane)
        self._relayout()
        if dropped:
            self._info(f"already shown here, not moved: {', '.join(dropped)}")

    def drop_pane(self, pane: PlotPane, index: int) -> bool:
        """Drop handler of the grid: reorder (own pane) or take the pane from another plot window."""
        if pane in self._panes:
            self.move_pane(pane, index)
            return True
        source = getattr(pane, "owner", None)
        if not isinstance(source, PlotDock):
            return False
        source.move_pane_to(pane, self, index)
        return True

    def _relayout(self) -> None:
        n, cols = len(self._panes), self.grid.columns
        for i, p in enumerate(self._panes):
            p.set_number(i + 1)
            p.set_closable(n > 1)
            p.set_selected(p is self._selected)
            p.set_x_labels(i + cols >= n or p.kind == "xy")
        if self.grid.panes() != self._panes:
            self.grid.set_panes(self._panes)
        self._update_pane_labels()
        self.panesChanged.emit(self)

    def _update_pane_labels(self) -> None:
        index = {p: i + 1 for i, p in enumerate(self._panes)}
        self.tree.set_pane_labels({k: f"P{index[p]}" for k, p in self._key_pane.items() if p in index})
        for p in self._panes:
            p.update_title()

    def _info(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setToolTip(text)
        self.infoMessage.emit(text)

    # ---------------------------------------------------------------- curves (D-63 placement)
    def _set_tree_checked(self, keys: Sequence[str], checked: bool) -> None:
        self._syncing = True
        try:
            self.tree.set_checked_many([k for k in keys if k in self.tree.all_keys()], checked)
        finally:
            self._syncing = False

    def default_pane(self, key: str) -> PlotPane:
        """Default target: first pane showing the same quantity with an axis for the unit; else the first empty
        time pane (not reserved for a waiting channel); else a new pane (laid out by the caller)."""
        _, unit = self._spec_text(key)
        group = self.quantity(key)
        for p in self.time_panes():
            if p.shows_quantity(group) and p.accepts(unit):
                return p
        reserved = {id(p) for p, _ in self._pending.values()}
        for p in self.time_panes():
            if not p.keys() and id(p) not in reserved:
                return p
        return self._new_pane()

    def _place(self, key: str, pane: PlotPane | None = None) -> PlotPane:
        name, unit = self._spec_text(key)
        group = self.quantity(key)
        if pane is not None and not pane.add_curve(key, name, unit, quantity=group):
            full = pane
            pane = self.default_pane(key)
            pane.add_curve(key, name, unit, quantity=group)
            if full in self._panes:
                self._info(f"{name} → Pane {self._panes.index(pane) + 1} (Pane {self._panes.index(full) + 1} "
                           "already has two units)")
        elif pane is None:
            pane = self.default_pane(key)
            if not pane.add_curve(key, name, unit, quantity=group):
                pane = self._new_pane()
                pane.add_curve(key, name, unit, quantity=group)
        self._key_pane[key] = pane
        return pane

    def _on_tree_checked(self, keys: list[str]) -> None:
        """Tree ticks → curves. Unticked: the curve goes, the pane stays. Newly ticked: restore / "plot in pane"
        targets first, the rest by the default rule; one layout update for the whole batch."""
        if self._syncing:
            return
        wanted = set(keys)
        known = set(self.tree.all_keys())
        changed = False
        for key in list(self._key_pane):
            if key not in wanted:
                pane = self._key_pane.pop(key)
                if key not in known:
                    self._pending[key] = (pane, len(self._pending))
                pane.remove_curve(key)
                changed = True
        added = [k for k in keys if k not in self._key_pane]
        added.sort(key=lambda k: self._pending[k][1] if k in self._pending else 1 << 30)
        for key in added:
            target = None
            if key in self._pending:
                pane, _ = self._pending.pop(key)
                target = pane if pane in self._panes else None
            self._place(key, target)
            changed = True
        if changed:
            self._relayout()

    def _check_pending(self) -> None:
        present = [k for k in self._pending if k in self.tree.all_keys() and k not in self._key_pane]
        if present:
            self.tree.set_checked_many(present)

    def move_curve(self, key: str, target: PlotPane) -> bool:
        """"Move to pane N": False (nothing changed) if the target has no free axis for the unit."""
        src = self._key_pane.get(key)
        if src is None or target not in self._panes:
            return False
        if src is target:
            return True
        if not target.accepts(src.unit_of(key)):
            self._info(f"Pane {self._panes.index(target) + 1} has no free axis for this unit")
            return False
        rec = src.take_curve(key)
        target.add_curve(rec.key, rec.name, rec.unit, rec.x, rec.y, rec.quantity or self.quantity(key))
        self._key_pane[key] = target
        self._relayout()
        return True

    def move_curve_to_new_pane(self, key: str) -> PlotPane | None:
        if key not in self._key_pane:
            return None
        pane = self._new_pane()
        self.move_curve(key, pane)
        return pane

    def plot_in_pane(self, key: str, pane: PlotPane) -> None:
        """Tick a channel directly into a given pane (manual placement wins)."""
        if key in self._key_pane:
            self.move_curve(key, pane)
            return
        if pane not in self._panes:
            return
        self._pending[key] = (pane, -1)
        self.tree.set_checked(key, True)

    def plot_in_new_pane(self, key: str) -> PlotPane:
        pane = self._new_pane()
        if key in self._key_pane:
            self.move_curve(key, pane)
        else:
            self._pending[key] = (pane, -1)
            self.tree.set_checked(key, True)
        if pane in self._panes and not pane.keys():
            self._relayout()
        return pane

    # ---------------------------------------------------------------- X-Y pane choices
    def xy_candidates(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """(x options, y options) from the registry: travel channels for x; force, then raw counts for y."""
        xs, ys = [], []
        for key in self.tree.all_keys():
            spec = self.tree.spec(key)
            if not getattr(spec, "available", True) or bit_name(spec):
                continue
            label = f"{getattr(spec, 'label', '') or key} [{getattr(spec, 'unit', '')}]"
            q = quantity_group(spec)
            if getattr(spec, "unit", "") in XY_X_UNITS:
                xs.append((key, label))
            if q in ("Force", "Raw counts"):
                ys.append((key, label))
        unit = force_unit().unit                  # View ▸ Units: the force channel of the display unit first

        def y_rank(kv: tuple[str, str]) -> int:
            spec = self.tree.spec(kv[0])
            if quantity_group(spec) == "Force":
                return 0 if getattr(spec, "unit", "") == unit else 1
            return 2
        ys.sort(key=y_rank)
        xs.sort(key=lambda kv: 0 if kv[0] == "x_test_mm" else 1)
        return xs, ys

    def update_xy_choices(self) -> None:
        """Re-evaluate the X-Y candidates (registry change, View ▸ Units)."""
        self._update_xy_choices()

    def _update_xy_choices(self, pane: XYPane | None = None, x: str | None = None, y: str | None = None) -> None:
        xs, ys = self.xy_candidates()
        for p in ([pane] if pane is not None else self.xy_panes()):
            p.set_choices(xs, ys, x, y)

    # ---------------------------------------------------------------- refresh (GUI thread, refresh timer)
    def is_shown(self) -> bool:
        """Visible and not covered (a tabified dock behind another one is hidden by Qt)."""
        if not self.isVisible():
            return False
        return self.isFloating() or not self.visibleRegion().isEmpty()

    def wants_snapshot(self) -> bool:
        return not self.frozen and self.is_shown() and bool(self.snapshot_keys())

    def snapshot_keys(self) -> list[str]:
        return [k for p in self.time_panes() for k in p.keys()]

    def px_width(self) -> int:
        w = max([0] + [p.px_width() for p in self.time_panes()])
        return max(MIN_PX, min(MAX_PX, w // PX_PER_BUCKET))

    def set_snapshot(self, snap: Any) -> None:
        """Data of one refresh (shared snapshot of this time window); one paint per pane."""
        if self.frozen:
            return
        self.last_snapshot = snap
        now = self._clock()
        for p in self.time_panes():
            p.set_snapshot(snap, now)
        self._set_x_range(-self.window_s, 0.0)
        for p in self.time_panes():
            p.commit_view()
        self.updates += 1
        self.set_info(f"t_end {getattr(snap, 't_end_dev_s', float('nan')):.1f} s")

    def refresh_xy(self, data: Any) -> int:
        """X-Y panes pull ``data.xy`` (time-independent view); returns the number of calls."""
        if self.frozen or not self.is_shown():
            return 0
        n = 0
        for p in self.xy_panes():
            if not (p.x_key and p.y_key):
                continue
            try:
                xy = data.xy(p.x_key, p.y_key, self.window_s)
            except Exception as exc:  # noqa: BLE001 - not connected / channel not available yet
                self._info(f"X-Y: {exc}")
                continue
            p.update_xy(xy)
            p.commit_view()
            n += 1
        return n

    def show_error(self, text: str) -> None:
        if self.status_label.text() != text:
            self.status_label.setText(text)

    # ---------------------------------------------------------------- X link (time)
    def _set_x_range(self, lo: float, hi: float, skip: Any = None) -> None:
        self._xlinking = True
        try:
            for p in self.time_panes():
                if p.plot_item.vb is not skip:
                    p.set_x_range(lo, hi)
        finally:
            self._xlinking = False

    def _on_pane_xrange(self, vb: Any, rng: Any) -> None:
        """User zoom / pan in one pane (e.g. while frozen) → same time range in all time panes."""
        if self._xlinking:
            return
        self._set_x_range(float(rng[0]), float(rng[1]), skip=vb)

    # ---------------------------------------------------------------- context menus
    def build_channel_menu(self, key: str) -> QMenu:
        menu = QMenu(self)
        _, unit = self._spec_text(key)
        current = self._key_pane.get(key)
        sel = self.selected_pane
        targets = [(i, p) for i, p in enumerate(self._panes) if p.kind == "time"]
        if current is not None:
            sub = menu.addMenu("Move to pane")
            for i, p in targets:
                act = sub.addAction(f"Pane {i + 1}" + (" (current)" if p is current else ""))
                act.setEnabled(p is not current and p.accepts(unit))
                act.triggered.connect(lambda _=False, p=p: self.move_curve(key, p))
            sub.addSeparator()
            sub.addAction("New pane").triggered.connect(lambda: self.move_curve_to_new_pane(key))
            act = menu.addAction(f"Move to selected pane (Pane {self._panes.index(sel) + 1})")
            act.setEnabled(sel is not current and sel.kind == "time" and sel.accepts(unit))
            act.triggered.connect(lambda _=False, p=sel: self.move_curve(key, p))
            menu.addAction("Remove from plot").triggered.connect(lambda: self.tree.set_checked(key, False))
        elif self.tree.is_available(key):
            sub = menu.addMenu("Plot in pane")
            for i, p in targets:
                act = sub.addAction(f"Pane {i + 1}")
                act.setEnabled(p.accepts(unit))
                act.triggered.connect(lambda _=False, p=p: self.plot_in_pane(key, p))
            sub.addSeparator()
            sub.addAction("New pane").triggered.connect(lambda: self.plot_in_new_pane(key))
            act = menu.addAction(f"Plot in selected pane (Pane {self._panes.index(sel) + 1})")
            act.setEnabled(sel.kind == "time" and sel.accepts(unit))
            act.triggered.connect(lambda _=False, p=sel: self.plot_in_pane(key, p))
        return menu

    def _on_channel_menu(self, key: str, pos: QPoint) -> None:
        menu = self.build_channel_menu(key)
        if not menu.isEmpty():
            menu.exec(pos)
        menu.deleteLater()

    def build_pane_menu(self, pane: PlotPane) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Rename…").triggered.connect(pane.title.begin_rename)
        auto = menu.addAction("Automatic title")
        auto.setEnabled(pane.custom_title is not None)
        auto.triggered.connect(lambda: self.rename_pane(pane, None))
        if pane.keys():
            curves = menu.addMenu("Move curve")
            for key in pane.keys():
                name, unit = self._spec_text(key)
                sub = curves.addMenu(name)
                for i, p in enumerate(self._panes):
                    if p is pane or p.kind != "time":
                        continue
                    act = sub.addAction(f"Pane {i + 1}")
                    act.setEnabled(p.accepts(unit))
                    act.triggered.connect(lambda _=False, k=key, p=p: self.move_curve(k, p))
                sub.addAction("New pane").triggered.connect(lambda _=False, k=key: self.move_curve_to_new_pane(k))
        win = menu.addMenu("Move to window…")
        win.setObjectName("moveToWindowMenu")
        for d in self.peers():
            win.addAction(d.windowTitle()).triggered.connect(lambda _=False, d=d: self.move_pane_to(pane, d))
        if self.new_dock_factory is not None:
            win.addSeparator()
            win.addAction("New plot window").triggered.connect(lambda: self.move_pane_to_new_window(pane))
        win.setEnabled(not win.isEmpty())
        legend = menu.addAction("Show legend")
        legend.setCheckable(True)
        legend.setChecked(pane.legend_visible)
        legend.toggled.connect(pane.set_legend_visible)
        menu.addSeparator()
        close = menu.addAction("Close pane")
        close.setEnabled(len(self._panes) > 1)
        close.triggered.connect(lambda: self.close_pane(pane))
        return menu

    def _on_pane_menu(self, pane: PlotPane, pos: QPoint) -> None:
        menu = self.build_pane_menu(pane)
        menu.exec(pos)
        menu.deleteLater()

    def move_pane_to_new_window(self, pane: PlotPane) -> "PlotDock | None":
        if self.new_dock_factory is None:
            return None
        target = self.new_dock_factory()
        if target is None:
            self._info("no further plot window (maximum reached)")
            return None
        self.move_pane_to(pane, target)
        return target

    # ---------------------------------------------------------------- persistence (QSettings, SW-RT-006)
    def layout_state(self) -> dict[str, Any]:
        """Columns, pane order, channels per pane (time panes; ``{"type": "xy", …}`` for X-Y panes), user titles,
        window length, autoscale, tree visibility. Empty panes are saved too (kept for reuse)."""
        def entry(p: PlotPane) -> Any:
            if p.kind == "xy":
                return p.layout_entry()
            items = [(o, k) for k, (q, o) in self._pending.items() if q is p and k not in self._key_pane]
            return p.keys() + [k for _, k in sorted(items, key=lambda t: t[0])]

        return {"version": LAYOUT_VERSION, "title": self.windowTitle(), "columns": self.columns,
                "window_s": self.window_s, "autoscale": bool(self.autoscale),
                "tree": bool(self.tree_button.isChecked()),
                "selected": self._panes.index(self.selected_pane),
                "panes": [entry(p) for p in self._panes],
                "titles": [p.custom_title for p in self._panes]}

    def restore_layout_state(self, state: Mapping[str, Any]) -> None:
        """Rebuild panes / curves from :meth:`layout_state`. Unknown channels wait until they appear (registry)."""
        raw = state.get("panes") if isinstance(state, Mapping) else None
        entries = [e for e in raw if isinstance(e, (list, dict))] if isinstance(raw, list) else []
        entries = entries or [[]]
        self._syncing = True
        try:
            self.tree.set_checked_many(self.tree.checked_keys(), False)
            for p in list(self._panes):
                p.clear_curves()
                self._detach(p)
                self._dispose(p)
            self._key_pane.clear()
            self._pending.clear()
            for e in entries:
                if isinstance(e, dict) and e.get("type") == "xy":
                    pane: PlotPane = XYPane(self.grid, self._clock)
                    self._attach(pane, len(self._panes))
                    self._update_xy_choices(pane, e.get("x"), e.get("y"))  # type: ignore[arg-type]
                else:
                    self._attach(PlotPane(self.grid, self._clock), len(self._panes))
        finally:
            self._syncing = False
        titles = state.get("titles") if isinstance(state.get("titles"), list) else []
        for i, p in enumerate(self._panes):
            t = titles[i] if i < len(titles) else None
            p.set_custom_title(t if isinstance(t, str) else None)
        order = 0
        seen: set[str] = set()
        for i, e in enumerate(entries):
            if not isinstance(e, list):
                continue
            for k in e:
                k = str(k)
                if k not in seen:
                    seen.add(k)
                    self._pending[k] = (self._panes[i], order)
                    order += 1
        cols = state.get("columns", 1)
        self.grid.set_panes(self._panes, cols if cols in COLUMN_CHOICES else 1)
        self.column_buttons[self.grid.columns].setChecked(True)
        try:
            self.set_window_s(float(state.get("window_s", self.window_s)))
        except (TypeError, ValueError):
            pass
        self.autoscale_check.setChecked(bool(state.get("autoscale", True)))
        self.set_tree_visible(bool(state.get("tree", True)))
        sel = state.get("selected", 0)
        self._selected = self._panes[sel] if isinstance(sel, int) and 0 <= sel < len(self._panes) else self._panes[0]
        self._relayout()
        self._check_pending()

    def dispose(self) -> None:
        for p in self._panes:
            p.dispose()
        _LIVE_DOCKS.discard(self)
