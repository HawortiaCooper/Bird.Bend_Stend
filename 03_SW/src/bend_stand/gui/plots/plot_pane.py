"""One plot pane ("chart") of a plot window: title strip + one ``pg.PlotItem`` with its own curves
(SW_design_GUI §4.7, D-38 / SW-RT-006; Thrust_Stand D-62).

A pane shows **at most one unit per Y axis**: the first unit goes to the left axis, a second unit gets a right axis
(own ``ViewBox`` overlaid on the plot, X locked to the plot); a third unit is refused (:meth:`PlotPane.accepts`) and
the dock puts that channel into another pane. Status bits use the pseudo unit ``bit``: they are drawn as digital
lanes (bit · 1.2 offset, fixed Y range, generated names as ticks, §4.6 rule 10). The title strip is the drag handle
for reordering / moving the pane, it carries the close button and the pane context menu; the title names the
quantity group(s) shown unless the user renamed the pane.

Data: :meth:`PlotPane.set_snapshot` takes the backend's ``PlotSnapshot`` (min/max columns on the relative time
axis, ``vstate`` per column, §4.6 rule 5): OK solid, EXTRAPOLATED dashed, INVALID grey, NO_DATA gap (the dashed /
grey curves exist only while such columns exist). Y range explicit with hysteresis (:class:`AutoRange`), the user's
mouse zoom on Y pauses it for the pane. :class:`XYPane` is the time-independent travel–load pane type
(``data.xy``).

Rendering budget (NFR-001, Thrust_Stand SWD-PM3-05 fix copied): one paint per refresh (view committed before the
event loop paints: :meth:`commit_view`); :class:`FastCurve` items; 1 px cosmetic pens, antialiasing off; time-axis
values only in the bottom pane of each grid column; opaque grid lines from one item (:class:`GridLines`); Y axes
cached as device pixmaps; legend as a label strip (:class:`LegendStrip`).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/plots/plot_pane.py @37c8747 (copied unchanged: orphan
adoption, pane MIME, title strip / rename, TimeAxis, FastCurve, GridLines, LegendStrip, data_range; adapted:
PlotPane data path for PlotSnapshot + vstate styles, status-bit lanes, AutoRange hysteresis instead of
auto_y_range / default ranges (D-50 is a Thrust_Stand rating feature), XYPane added).

Implements: SW-RT-006 (pane = grid cell, drag handle, rename, quantity title, X-Y pane type), SW-RT-002 (curves per
channel, status bits), SW-RT-003 (time / X-Y view, auto Y), SW-CAL-008 (extrapolated style), NFR-001 (rendering)
"""
from __future__ import annotations

import time
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QLineF, QMimeData, QObject, QEvent, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFontMetrics, QKeyEvent, QMouseEvent, QPen, QStaticText
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsItem,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import Shiboken

from bend_stand.gui.plots.autorange import AutoRange
from bend_stand.gui.plots.time_view import (
    VSTATE_EXTRAPOLATED, VSTATE_INVALID, VSTATE_OK, interleave, split_vstate,
)


def adopt_pyqtgraph_orphans(owner: QWidget, *holders: Any) -> int:
    """SWD-PM3-07: pyqtgraph creates parentless, Python-owned QObjects for every plot (``PlotItem.ctrlMenu``
    "Plot Options" and ``PlotItem.stateGroup``; ``ViewBox.menu`` with its ``widgetGroups``). They sit in
    reference cycles, so their C++ destructors would run in whatever thread the cyclic GC happens to run
    (a ``QMenu`` / ``QObject`` destructor off the GUI thread can deadlock the process). Parenting them to
    ``owner`` hands them to Qt: they are deleted with the pane, in the GUI thread, and the GC later frees only
    the dead wrappers. Menus keep their popup window flags. Returns the number of objects adopted."""
    objs: list[Any] = []
    for h in holders:
        if h is None:
            continue
        objs += [getattr(h, "ctrlMenu", None), getattr(h, "stateGroup", None)]
        menu = getattr(h, "menu", None)
        if isinstance(menu, QObject):
            objs += list(getattr(menu, "widgetGroups", []) or [])
            objs.append(menu)
    n = 0
    for o in objs:
        if isinstance(o, QObject) and Shiboken.isValid(o) and o.parent() is None:
            if isinstance(o, QWidget):
                o.setParent(owner, o.windowFlags())
            else:
                o.setParent(owner)
            n += 1
    return n


PALETTE = ("#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2",
           "#17becf", "#bcbd22", "#7f7f7f")
PEN_WIDTH = 1                       # cosmetic 1 px: Qt's fast line path (1.5 px went through the stroker, SWD-PM3-05)
GRID_MAJOR = QColor(208, 208, 208)  # opaque grid colours (alpha blending of long vertical lines is about 7x slower)
GRID_MINOR = QColor(234, 234, 234)
PANE_MIME = "application/x-bend-stand-plot-pane"
LEFT, RIGHT = "L", "R"
AXIS_WIDTH = 58                     # fixed Y axis width, so that panes of one column line up

_LIVE_PANES: "weakref.WeakValueDictionary[str, PlotPane]" = weakref.WeakValueDictionary()


def pane_from_mime(mime: QMimeData | None) -> "PlotPane | None":
    """The live pane a drag carries (in-process drags only), else None."""
    if mime is None or not mime.hasFormat(PANE_MIME):
        return None
    token = bytes(mime.data(PANE_MIME).data()).decode("ascii", "replace")
    return _LIVE_PANES.get(token)


def pane_mime(pane: "PlotPane") -> QMimeData:
    mime = QMimeData()
    mime.setData(PANE_MIME, pane.token.encode("ascii"))
    mime.setText(pane.title_text())
    return mime


@dataclass
class CurveRecord:
    """A curve taken out of a pane (to move it to another pane): name, colour, unit and its data."""

    key: str
    name: str
    unit: str
    x: Any
    y: Any
    quantity: str = ""


TITLE_HELP = ("Drag to reorder the pane or to move it to another plot window; click to select it (target of "
              "\"selected pane\" in the channel menu); double click to rename; right click for the pane menu")


class _NameEdit(QLineEdit):
    """Inline editor of the pane name: Enter / focus loss = apply, Esc = cancel (D-48 style)."""

    def __init__(self, title: "PaneTitle") -> None:
        super().__init__(title)
        self._title = title
        self.setPlaceholderText("automatic (quantity names)")
        self.setToolTip("Pane name; empty = automatic title (quantity names). Enter = apply, Esc = cancel")
        self.hide()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self._title.end_rename(apply=False)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        if self.isVisible():
            self._title.end_rename(apply=True)


class PaneTitle(QFrame):
    """Title strip: grip + "Pane N · quantities" + close button. Drag = move the pane; double click = rename."""

    def __init__(self, pane: "PlotPane") -> None:
        super().__init__(pane)
        self._pane = pane
        self._press: QPoint | None = None
        self.setObjectName("paneTitle")
        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(TITLE_HELP)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 2, 0)
        lay.setSpacing(4)
        grip = QLabel("⠿", self)
        grip.setStyleSheet("color: #707070;")
        lay.addWidget(grip)
        self.label = QLabel("", self)
        self.label.setMinimumWidth(10)
        lay.addWidget(self.label, 1)
        self.editor = _NameEdit(self)
        self.editor.returnPressed.connect(lambda: self.end_rename(apply=True))
        lay.addWidget(self.editor, 1)
        self.close_button = QToolButton(self)
        self.close_button.setText("✕")
        self.close_button.setAutoRaise(True)
        self.close_button.setToolTip("Close this pane (its channels are unticked)")
        self.close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.close_button.clicked.connect(lambda: pane.closeRequested.emit(pane))
        lay.addWidget(self.close_button)
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        bg = "#cfe0f7" if on else "#ececec"
        self.setStyleSheet(f"#paneTitle {{ background: {bg}; border-bottom: 1px solid #b8b8b8; }}")

    def set_text(self, text: str, tooltip: str | None = None) -> None:
        self.label.setToolTip(tooltip or text)
        fm = self.label.fontMetrics()
        self.label.setText(fm.elidedText(text, Qt.TextElideMode.ElideRight, max(10, self.label.width())))
        self._full = text

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if hasattr(self, "_full"):
            self.set_text(self._full)

    # -- rename (D-63: title = quantity names unless the user renamed the pane) --------------------------
    @property
    def renaming(self) -> bool:
        return self.editor.isVisible()

    def begin_rename(self) -> None:
        self.editor.setText(self._pane.custom_title or "")
        self.label.hide()
        self.editor.show()
        self.editor.selectAll()
        self.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def end_rename(self, apply: bool) -> None:
        if not self.editor.isVisible():
            return
        text = self.editor.text()
        self.editor.hide()
        self.label.show()
        if apply:
            self._pane.set_custom_title(text)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = None
            self.begin_rename()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._pane.selectRequested.emit(self._pane)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance():
                self._press = None
                self._pane.start_drag()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._press = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self._pane.contextMenuRequested.emit(self._pane, event.globalPos())
        event.accept()


class _ClickFilter(QObject):
    """A mouse press anywhere in the plot selects the pane (does not consume the event)."""

    def __init__(self, pane: "PlotPane") -> None:
        super().__init__(pane)
        self._pane = pane

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonPress:
            self._pane.selectRequested.emit(self._pane)
        return False


_TICK_CACHE: dict[tuple, list] = {}


def cached_tick_values(axis: pg.AxisItem, lo: float, hi: float, size: float) -> list:
    """``axis.tickValues`` memoised on (orientation, range, length): the panes of one grid column share the time
    range and width, and a Y range changes rarely, so most grid paints reuse the values (SWD-PM3-05)."""
    key = (axis.orientation, lo, hi, round(size))
    v = _TICK_CACHE.get(key)
    if v is None:
        if len(_TICK_CACHE) > 256:
            _TICK_CACHE.clear()
        v = _TICK_CACHE[key] = axis.tickValues(lo, hi, size)
    return v


class TimeAxis(pg.AxisItem):
    """Bottom (time) axis drawn directly every refresh (SWD-PM3-05): the time range scrolls, so pyqtgraph's
    ``AxisItem`` regenerated its draw specs (Python, text measurements) and a ``QPicture`` per pane and refresh
    (about 1.8 ms per pane). Here: tick values memoised (:func:`cached_tick_values`), the strings come from the
    inherited ``tickStrings`` (no SI prefix, SWD-PM3-01), labels are cached ``QStaticText`` s; major ticks always
    labelled, minor ticks only when the label fits between them. Other orientations: the base class."""

    TICK_MAJOR = 5
    TICK_MINOR = 3
    _STATIC_MAX = 512

    def __init__(self, orientation: str = "bottom", **kwargs: Any) -> None:
        super().__init__(orientation, **kwargs)
        self._static: dict[str, QStaticText] = {}
        self._fm = QFontMetrics(self.font())
        # fixed height: outward major tick + offset + one text line (+ the label, added by the base class)
        self.setStyle(autoExpandTextSpace=False, tickTextHeight=self._fm.height(), tickLength=self.TICK_MAJOR)

    def _text(self, s: str) -> QStaticText:
        st = self._static.get(s)
        if st is None:
            if len(self._static) > self._STATIC_MAX:
                self._static.clear()
            st = self._static[s] = QStaticText(s)
            st.setTextFormat(Qt.TextFormat.PlainText)
        return st

    def paint(self, p, opt, widget) -> None:
        if self.orientation != "bottom":
            super().paint(p, opt, widget)
            return
        lo, hi = (float(v) for v in self.range)
        w = float(self.size().width())
        if w <= 0 or not hi > lo:
            return
        k = w / (hi - lo)
        ticks = cached_tick_values(self, lo, hi, w)
        p.setRenderHint(p.RenderHint.Antialiasing, False)
        lines = [QLineF(0.0, 0.0, w, 0.0)]
        for level, (_spacing, values) in enumerate(ticks[:2]):
            length = self.TICK_MAJOR if level == 0 else self.TICK_MINOR
            for v in values:
                x = (v - lo) * k
                lines.append(QLineF(x, 0.0, x, float(length)))
        p.setPen(self.pen())
        p.drawLines(lines)
        if not self.style["showValues"]:
            return
        p.setPen(self.textPen())
        if self.style.get("tickFont"):
            p.setFont(self.style["tickFont"])
        y = float(self.TICK_MAJOR + self.style["tickTextOffset"][1])
        drawn: list[tuple[float, float]] = []
        for level, (spacing, values) in enumerate(ticks[:2]):
            if not values:
                continue
            strings = self.tickStrings(values, self.autoSIPrefixScale * self.scale, spacing)
            texts = [self._text(t) for t in strings]
            widest = max(t.size().width() for t in texts)
            if level > 0 and spacing * k < widest + 12:
                break                                   # minor labels do not fit between their ticks
            for v, t in zip(values, texts):
                tw = t.size().width()
                x = (v - lo) * k - tw / 2
                if x < 0 or x + tw > w or any(a - 6 < x + tw and x < b + 6 for a, b in drawn):
                    continue
                drawn.append((x, x + tw))
                p.drawStaticText(QPointF(x, y), t)


class FastCurve(pg.PlotCurveItem):
    """``PlotCurveItem`` for the 30 fps refresh (SWD-PM3-05): :meth:`set_xy` hands over the decimated arrays
    without pyqtgraph's argument parsing / view-bounds notifications (the Y range is set explicitly by the pane)
    and the bounding rect is the data rect padded by 1 % of the view (computed from the min / max the pane needs
    for its autoscale anyway), instead of pyqtgraph's per-curve bounds scan with pixel padding in every dirty-item
    pass of the scene. Drawing is unchanged (``connect="finite"``: NaN = gap)."""

    def __init__(self, *, pen: Any, name: str) -> None:
        super().__init__(pen=pen, name=name, connect="finite", antialias=False)
        self._rect = QRectF()

    def set_xy(self, x: np.ndarray, y: np.ndarray, yr: tuple[float, float] | None = None) -> None:
        """New data; ``yr`` = (min, max) of the finite ``y`` if the caller has it (None = computed here)."""
        self.prepareGeometryChange()
        self.xData = x
        self.yData = y
        self.invalidateBounds()
        self.path = None
        self.fillPath = None
        self._fillPathList = None
        self._mouseShape = None
        self._lineSegmentsRendered = False
        if yr is None and len(y):
            yr = data_range([y])
        if yr is None or len(x) == 0:
            self._rect = QRectF()
        else:
            x0, x1 = float(x[0]), float(x[-1])
            vb = self.getViewBox()
            if vb is not None:
                (vx0, vx1), (vy0, vy1) = vb.state["viewRange"]
                px, py = 0.01 * abs(vx1 - vx0), 0.01 * abs(vy1 - vy0)
            else:
                px = py = 0.0
            px = max(px, 1e-9 * max(1.0, abs(x0)))
            py = max(py, 1e-9 * max(1.0, abs(yr[0])), 0.01 * (yr[1] - yr[0]))
            self._rect = QRectF(min(x0, x1) - px, yr[0] - py, abs(x1 - x0) + 2 * px, (yr[1] - yr[0]) + 2 * py)
        self.update()

    def setData(self, *args: Any, **kargs: Any) -> None:  # noqa: N802 - pyqtgraph API (tests, take/re-add)
        super().setData(*args, **kargs)
        x, y = self.xData, self.yData
        if x is not None and y is not None:
            self.set_xy(x, y)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt override
        return self._rect

    def paint(self, p, opt, widget) -> None:
        """Plain 1 px polyline (no fill, no shadow pen, no segment mode): pyqtgraph's generic paint() logic is
        skipped (exporters still use the base implementation through ``_exportOpts``)."""
        if self.xData is None or len(self.xData) == 0:
            return
        if self._exportOpts is not False or self.opts["fillLevel"] is not None or self.opts["shadowPen"] is not None:
            super().paint(p, opt, widget)
            return
        p.setRenderHint(p.RenderHint.Antialiasing, False)
        p.setPen(self.opts["pen"])
        p.drawPath(self.getPath())


class GridLines(pg.GraphicsObject):
    """Opaque grid lines at the tick positions of the bottom and left axes, drawn by one item behind the curves
    (two ``drawLines`` calls). Replaces pyqtgraph's axis grid (alpha lines replayed from the axis pictures: about
    5 ms per pane and refresh on this PC, SWD-PM3-05). Presentation only."""

    def __init__(self, plot_item: pg.PlotItem) -> None:
        super().__init__()
        self._pi = weakref.ref(plot_item)
        self.setZValue(-1e6)
        self._pens = []
        for c in (GRID_MINOR, GRID_MAJOR):
            pen = QPen(c)
            pen.setWidth(0)
            pen.setCosmetic(True)
            self._pens.append(pen)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt override
        vb = self.getViewBox()
        return QRectF() if vb is None else vb.viewRect()

    def viewRangeChanged(self) -> None:  # noqa: N802 - pyqtgraph hook
        self.prepareGeometryChange()
        self.update()

    def lines(self) -> tuple[list[QLineF], list[QLineF]]:
        """(minor, major) grid lines in view coordinates for the current range."""
        pi = self._pi()
        vb = self.getViewBox()
        levels: tuple[list[QLineF], list[QLineF]] = ([], [])
        if pi is None or vb is None:
            return levels
        (x0, x1), (y0, y1) = vb.viewRange()
        for axis, lo, hi, size, vertical in ((pi.getAxis("bottom"), x0, x1, vb.width(), True),
                                             (pi.getAxis("left"), y0, y1, vb.height(), False)):
            if size <= 0 or not hi > lo:
                continue
            for level, (_spacing, values) in enumerate(cached_tick_values(axis, lo, hi, size)[:2]):
                out = levels[1] if level == 0 else levels[0]
                for v in values:
                    out.append(QLineF(v, y0, v, y1) if vertical else QLineF(x0, v, x1, v))
        return levels

    def paint(self, p, *args) -> None:
        p.setRenderHint(p.RenderHint.Antialiasing, False)
        for pen, lines in zip(self._pens, self.lines()):
            if lines:
                p.setPen(pen)
                p.drawLines(lines)


class LegendStrip(QLabel):
    """Curve legend as a label strip between the title and the plot (SWD-PM3-02: never covers the curves;
    repainted only when the curve list changes). Toggle: pane menu "Show legend"."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("paneLegend")
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setContentsMargins(6, 0, 4, 1)
        self.setStyleSheet("#paneLegend { font-size: 8pt; color: #404040; }")
        self.entries: list[tuple[str, str]] = []
        self._wanted = True

    def set_entries(self, entries: list[tuple[str, str]]) -> None:
        """``entries`` = [(colour "#rrggbb", label)], in curve order."""
        self.entries = list(entries)
        self.setText("&nbsp;&nbsp; ".join(
            f'<span style="color:{c}; font-weight:bold;">&#9473;&#9473;</span>&nbsp;{_html(t)}' for c, t in entries))
        self.setVisible(bool(entries) and self._wanted)

    def set_wanted(self, on: bool) -> None:
        self._wanted = bool(on)
        self.setVisible(bool(self.entries) and self._wanted)

    @property
    def wanted(self) -> bool:
        return self._wanted


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(" ", "&nbsp;")


def data_range(arrays: list[np.ndarray]) -> tuple[float, float] | None:
    """(min, max) over the finite values of ``arrays`` (NaN = no data); None if there is none."""
    lo = hi = None
    for y in arrays:
        if y is None or len(y) == 0:
            continue
        a = float(np.fmin.reduce(y))                # fmin / fmax skip NaN; all-NaN -> NaN
        b = float(np.fmax.reduce(y))
        if not (np.isfinite(a) and np.isfinite(b)):
            fin = y[np.isfinite(y)]
            if fin.size == 0:
                continue
            a, b = float(fin.min()), float(fin.max())
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    return None if lo is None else (lo, hi)




BIT_UNIT = "bit"                    # pseudo unit of status-bit channels (digital lanes)
LANE_STEP = 1.2                     # lane offset per bit (§4.6 rule 10)


class PlotPane(QFrame):
    """A time chart: title strip + ``PlotWidget``; curves keyed by channel key, max. two units (left/right)."""

    kind = "time"

    selectRequested = Signal(object)                # self
    closeRequested = Signal(object)                 # self
    titleChanged = Signal(object)                   # self (user rename)
    contextMenuRequested = Signal(object, QPoint)   # self, global position

    def __init__(self, parent: QWidget | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self.token = f"pane-{id(self):x}"
        _LIVE_PANES[self.token] = self
        self._clock = clock
        self.owner: Any = None
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(140, 90)
        self.number = 1
        self.custom_title: str | None = None       # user-given name (None = automatic: quantity names)
        self._curves: dict[str, FastCurve] = {}
        self._extra: dict[str, dict[int, FastCurve]] = {}     # vstate style curves (dashed / grey), lazily
        self._units: dict[str, str] = {}
        self._names: dict[str, str] = {}
        self._quantity: dict[str, str] = {}
        self._side: dict[str, str] = {}
        self._axis_unit: dict[str, str | None] = {LEFT: None, RIGHT: None}
        self._right_vb: pg.ViewBox | None = None
        self._autoscale = True
        self._manual_y = False              # the user zoomed / panned Y with the mouse: autoscale paused
        self._auto = {LEFT: AutoRange(), RIGHT: AutoRange()}
        self._yrange: dict[str, tuple[float, float] | None] = {LEFT: None, RIGHT: None}   # last applied
        self._rebalancing = False
        self.drag_handler = None            # set by the dock / tests: callable(pane) -> None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.title = PaneTitle(self)
        lay.addWidget(self.title)
        self.legend_strip = LegendStrip(self)
        self.legend_strip.hide()
        lay.addWidget(self.legend_strip)
        self._content_layout = lay
        self.plot_widget = pg.PlotWidget(self, background="w", axisItems={"bottom": TimeAxis("bottom")})
        self.plot_widget.setAcceptDrops(False)          # drops go to the grid (drop indicator)
        self.plot_widget.viewport().setAcceptDrops(False)
        self.plot_widget.setMinimumSize(100, 60)
        self.plot_widget.scene().setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        self._click_filter = _ClickFilter(self)
        self.plot_widget.viewport().installEventFilter(self._click_filter)
        lay.addWidget(self.plot_widget, 1)

        pi: pg.PlotItem = self.plot_widget.getPlotItem()
        self.plot_item = pi
        adopt_pyqtgraph_orphans(self, pi, pi.vb)          # SWD-PM3-07: deleted with the pane, GUI thread
        self.grid_lines = GridLines(pi)
        pi.vb.addItem(self.grid_lines, ignoreBounds=True)
        pi.vb.setFlag(QGraphicsItem.GraphicsItemFlag.ItemHasNoContents, True)
        for side in ("left", "right", "bottom", "top"):
            pi.getAxis(side).enableAutoSIPrefix(False)
        pi.setLabel("bottom", "t [s]")
        pi.setClipToView(False)
        pi.setDownsampling(auto=False)
        pi.enableAutoRange(axis="y", enable=False)
        pi.enableAutoRange(axis="x", enable=False)
        pi.hideButtons()
        pi.getAxis("left").setWidth(AXIS_WIDTH)
        pi.getAxis("left").setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        pi.vb.sigRangeChangedManually.connect(self._on_manual_range)
        pi.vb.sigStateChanged.connect(self._on_vb_state)
        self.update_title()

    # ---------------------------------------------------------------- identity / title
    def quantities(self) -> list[str]:
        return list(dict.fromkeys(q for q in (self._quantity[k] for k in self._curves) if q))

    def shows_quantity(self, quantity: str) -> bool:
        return quantity in self._quantity.values()

    def quantity_of(self, key: str) -> str:
        return self._quantity[key]

    def auto_title(self) -> str:
        q = self.quantities()
        if q:
            return ", ".join(q)
        return ", ".join(self._names[k] for k in self._curves) or "(empty)"

    def title_text(self) -> str:
        """"Pane N · <user name>" or "Pane N · <quantity groups>"."""
        return f"Pane {self.number} · " + (self.custom_title or self.auto_title())

    def update_title(self) -> None:
        names = "\n".join(f"• {self._names[k]}" + (f" [{self._units[k]}]" if self._units[k] else "")
                          for k in self._curves)
        tip = self.title_text() + ("\n" + names if names else "\nempty: newly ticked channels reuse this pane")
        self.title.set_text(self.title_text(), tip)

    def set_custom_title(self, text: str | None) -> None:
        text = (text or "").strip() or None
        if text != self.custom_title:
            self.custom_title = text
            self.update_title()
            self.titleChanged.emit(self)

    def set_number(self, n: int) -> None:
        if n != self.number:
            self.number = n
            self.update_title()

    def set_selected(self, on: bool) -> None:
        self.title.set_selected(on)

    def set_closable(self, on: bool) -> None:
        self.title.close_button.setEnabled(on)

    def start_drag(self) -> None:
        if self.drag_handler is not None:
            self.drag_handler(self)
            return
        drag = QDrag(self)
        drag.setMimeData(pane_mime(self))
        drag.setPixmap(self.title.grab())
        drag.exec(Qt.DropAction.MoveAction)

    # ---------------------------------------------------------------- units / axes
    def units(self) -> tuple[str | None, str | None]:
        return self._axis_unit[LEFT], self._axis_unit[RIGHT]

    def accepts(self, unit: str) -> bool:
        """True if a channel of ``unit`` fits (same unit as an axis, or a free axis)."""
        left, right = self._axis_unit[LEFT], self._axis_unit[RIGHT]
        return unit in (left, right) or left is None or right is None

    def side_of(self, key: str) -> str:
        return self._side[key]

    def unit_of(self, key: str) -> str:
        return self._units[key]

    def _vb(self, side: str) -> pg.ViewBox | None:
        return self.plot_item.vb if side == LEFT else self._right_vb

    def _ensure_right_vb(self) -> pg.ViewBox:
        if self._right_vb is None:
            pi = self.plot_item
            vb = pg.ViewBox(enableMenu=False)
            adopt_pyqtgraph_orphans(self, vb)
            pi.scene().addItem(vb)
            pi.getAxis("right").linkToView(vb)
            pi.getAxis("right").setWidth(AXIS_WIDTH)
            pi.getAxis("right").setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
            vb.setXLink(pi)
            vb.enableAutoRange(axis="y", enable=False)
            vb.setFlag(QGraphicsItem.GraphicsItemFlag.ItemHasNoContents, True)
            vb.sigRangeChangedManually.connect(self._on_manual_range)
            vb.sigStateChanged.connect(self._on_vb_state)
            pi.vb.sigResized.connect(self._sync_right_geometry)
            self._right_vb = vb
            self._sync_right_geometry()
        self.plot_item.showAxis("right")
        return self._right_vb

    def _sync_right_geometry(self) -> None:
        vb = self._right_vb
        if vb is None:
            return
        vb.setGeometry(self.plot_item.vb.sceneBoundingRect())
        vb.linkedViewChanged(self.plot_item.vb, vb.XAxis)

    @property
    def right_viewbox(self) -> pg.ViewBox | None:
        return self._right_vb

    def _axis_label(self, side: str) -> None:
        unit = self._axis_unit[side]
        axis = "left" if side == LEFT else "right"
        if unit == BIT_UNIT:
            self.plot_item.setLabel(axis, "")
            return
        self.plot_item.getAxis(axis).setTicks(None)
        self.plot_item.setLabel(axis, f"[{unit}]" if unit else "")    # unit as text only (SWD-PM3-01)

    # ---------------------------------------------------------------- curves
    def keys(self) -> list[str]:
        return list(self._curves)

    def curve(self, key: str) -> FastCurve:
        return self._curves[key]

    def style_curves(self, key: str) -> dict[int, FastCurve]:
        """vstate → curve of ``key`` (OK always; EXTRAPOLATED / INVALID only once such columns existed)."""
        return {VSTATE_OK: self._curves[key], **self._extra.get(key, {})}

    def has(self, key: str) -> bool:
        return key in self._curves

    def bit_keys(self, side: str | None = None) -> list[str]:
        return [k for k in self._curves if self._units[k] == BIT_UNIT and (side is None or self._side[k] == side)]

    def _free_colour(self) -> str:
        used = {str(c.opts["pen"].color().name()) for c in self._curves.values()}
        for c in PALETTE:
            if c not in used:
                return c
        return PALETTE[len(self._curves) % len(PALETTE)]

    def add_curve(self, key: str, name: str, unit: str, x: Any = None, y: Any = None,
                  quantity: str = "") -> bool:
        """Add a curve; False (nothing changed) if the unit does not fit (both axes taken)."""
        if key in self._curves:
            return True
        unit = unit or ""
        if self._axis_unit[LEFT] in (None, unit):
            side = LEFT
        elif self._axis_unit[RIGHT] in (None, unit):
            side = RIGHT
        else:
            return False
        pen = pg.mkPen(self._free_colour(), width=PEN_WIDTH)
        label = name + (f" [{unit}]" if unit and unit != BIT_UNIT else "")
        item = FastCurve(pen=pen, name=label)
        (self.plot_item if side == LEFT else self._ensure_right_vb()).addItem(item)
        if x is not None and y is not None and len(x):
            item.set_xy(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
        self._curves[key] = item
        self._units[key] = unit
        self._names[key] = name
        self._quantity[key] = quantity or ""
        self._side[key] = side
        if self._axis_unit[side] != unit:
            self._axis_unit[side] = unit
            self._axis_label(side)
            self._reset_range(side)
        if unit == BIT_UNIT:
            self._layout_lanes(side)
        self.update_title()
        self._update_legend()
        return True

    def _layout_lanes(self, side: str) -> None:
        """Bit lanes: fixed Y range, generated names as ticks (no axis repaint per refresh)."""
        bits = self.bit_keys(side)
        if not bits:
            return
        axis = self.plot_item.getAxis("left" if side == LEFT else "right")
        axis.setTicks([[(i * LANE_STEP + 0.5, self._names[k]) for i, k in enumerate(bits)], []])
        vb = self._vb(side)
        rng = (-0.2, max(1, len(bits)) * LANE_STEP)
        if vb is not None and self._yrange[side] != rng:
            vb.setYRange(rng[0], rng[1], padding=0)
            self._yrange[side] = rng

    def _reset_range(self, side: str) -> None:
        self._yrange[side] = None
        self._auto[side].reset()

    def take_curve(self, key: str) -> CurveRecord:
        """Remove a curve and return it (with its data) for re-adding elsewhere."""
        item = self._curves[key]
        x, y = item.getData()
        rec = CurveRecord(key, self._names[key], self._units[key],
                          None if x is None else np.array(x), None if y is None else np.array(y),
                          self._quantity[key])
        self.remove_curve(key)
        return rec

    def remove_curve(self, key: str) -> None:
        item = self._curves.pop(key, None)
        if item is None:
            return
        side = self._side.pop(key)
        unit = self._units.pop(key)
        self._names.pop(key)
        self._quantity.pop(key, None)
        owner = self.plot_item if side == LEFT else self._right_vb
        for it in [item, *self._extra.pop(key, {}).values()]:
            if owner is not None:
                owner.removeItem(it)
        if side not in self._side.values():
            self._axis_unit[side] = None
            self._axis_label(side)
            self._reset_range(side)
            self.plot_item.getAxis("left" if side == LEFT else "right").setTicks(None)
        elif unit == BIT_UNIT:
            self._yrange[side] = None
            self._layout_lanes(side)
        self._rebalance()
        self.update_title()
        self._update_legend()

    def _rebalance(self) -> None:
        """Left axis empty but right axis used: move the right-axis curves to the left axis."""
        if self._rebalancing:
            return
        self._rebalancing = True
        try:
            if self._axis_unit[LEFT] is None and self._axis_unit[RIGHT] is not None:
                moving = [k for k, s in self._side.items() if s == RIGHT]
                recs = [self.take_curve(k) for k in moving]
                for r in recs:
                    self.add_curve(r.key, r.name, r.unit, r.x, r.y, r.quantity)
            if self._axis_unit[RIGHT] is None:
                self.plot_item.hideAxis("right")
        finally:
            self._rebalancing = False

    def clear_curves(self) -> list[str]:
        keys = list(self._curves)
        for k in keys:
            self.remove_curve(k)
        return keys

    # ---------------------------------------------------------------- legend (SWD-PM3-02)
    def _update_legend(self) -> None:
        entries = []
        for k, item in self._curves.items():
            unit = self._units[k]
            label = self._names[k] + (f" [{unit}]" if unit and unit != BIT_UNIT else "")
            entries.append((item.opts["pen"].color().name(), label))
        self.legend_strip.set_entries(entries)

    def legend_entries(self) -> list[tuple[str, str]]:
        return list(self.legend_strip.entries)

    @property
    def legend_visible(self) -> bool:
        return self.legend_strip.wanted

    def set_legend_visible(self, on: bool) -> None:
        self.legend_strip.set_wanted(on)

    # ---------------------------------------------------------------- view
    @property
    def autoscale(self) -> bool:
        """Explicit Y autoscale active (dock "Autoscale"; paused while the user zooms Y with the mouse)."""
        return self._autoscale and not self._manual_y

    def set_autoscale(self, on: bool) -> None:
        """On = Y follows the data (explicit, hysteresis §4.6 rule 3); off = the range stays as it is (manual Y:
        mouse zoom / pan, SW-RT-003)."""
        self._autoscale = bool(on)
        self._manual_y = False
        for vb in self._viewboxes():
            vb.enableAutoRange(axis="y", enable=False)
        for side in (LEFT, RIGHT):
            if self._axis_unit[side] != BIT_UNIT:
                self._reset_range(side)

    def _viewboxes(self) -> list[pg.ViewBox]:
        return [self.plot_item.vb] + ([self._right_vb] if self._right_vb is not None else [])

    def _on_manual_range(self, mask: Any) -> None:
        try:
            y = bool(mask[1])
        except (TypeError, IndexError):
            y = True
        if y:
            self._manual_y = True

    def _on_vb_state(self, vb: Any = None) -> None:
        """pyqtgraph's lazy (double-painting) auto range is never left on: taken over explicitly."""
        for v in self._viewboxes():
            auto = v.state["autoRange"]
            if auto[1] is not False:
                self._manual_y = False
                self._autoscale = True
                for side in (LEFT, RIGHT):
                    if self._axis_unit[side] != BIT_UNIT:
                        self._reset_range(side)
                v.enableAutoRange(axis="y", enable=False)
            if auto[0] is not False:
                v.enableAutoRange(axis="x", enable=False)

    def px_width(self) -> int:
        return int(self.plot_item.getViewBox().width())

    @property
    def x_labels(self) -> bool:
        return getattr(self, "_x_labels", True)

    def set_x_labels(self, on: bool) -> None:
        """Time-axis values only in the bottom pane of each grid column (the panes share the time range)."""
        on = bool(on)
        if on == self.x_labels:
            return
        self._x_labels = on
        if on:
            self.plot_item.showAxis("bottom")
        else:
            self.plot_item.hideAxis("bottom")

    def set_snapshot(self, snapshot: Any, now: float | None = None) -> None:
        """Curve data of one refresh from a ``PlotSnapshot`` (§4.6 rule 5); with autoscale on, the Y range of each
        analog axis follows the data (explicit, hysteresis): no lazy auto range, so no second paint."""
        now = self._clock() if now is None else now
        t = snapshot.t_col_s
        series = snapshot.series
        per_side: dict[str, list[tuple[float, float]]] = {LEFT: [], RIGHT: []}
        empty = np.zeros(0)
        bit_index = {side: {k: i for i, k in enumerate(self.bit_keys(side))} for side in (LEFT, RIGHT)}
        for key, item in self._curves.items():
            s = series.get(key)
            extra = self._extra.get(key, {})
            if s is None:
                item.set_xy(empty, empty)
                for it in extra.values():
                    it.set_xy(empty, empty)
                continue
            x, y = interleave(t, s.lo, s.hi)
            side = self._side[key]
            if self._units[key] == BIT_UNIT:
                off = bit_index[side][key] * LANE_STEP
                y = np.where(np.isfinite(y), np.clip(y, 0.0, 1.0), np.nan) + off
                item.set_xy(x, y, (off, off + 1.0))
                continue
            parts = split_vstate(y, s.vstate)
            for st, ys in parts.items():
                target = item if st == VSTATE_OK else self._style_curve(key, st)
                target.set_xy(x, ys, data_range([ys]))
            for st, it in extra.items():
                if st not in parts:
                    it.set_xy(empty, empty)
            yr = data_range([y])
            if yr is not None:
                per_side[side].append(yr)
        if self._autoscale and not self._manual_y:
            for side, ranges in per_side.items():
                vb = self._vb(side)
                if vb is None or not ranges or self._axis_unit[side] == BIT_UNIT:
                    continue
                rng = self._auto[side].update((min(r[0] for r in ranges), max(r[1] for r in ranges)), now)
                if rng is not None and rng != self._yrange[side]:
                    vb.setYRange(rng[0], rng[1], padding=0)
                    self._yrange[side] = rng

    def _style_curve(self, key: str, state: int) -> FastCurve:
        """Dashed (EXTRAPOLATED) / grey (INVALID) companion curve, created on first use (§4.3)."""
        extra = self._extra.setdefault(key, {})
        item = extra.get(state)
        if item is None:
            color = self._curves[key].opts["pen"].color().name()
            if state == VSTATE_EXTRAPOLATED:
                pen = pg.mkPen(color, width=PEN_WIDTH, style=Qt.PenStyle.DashLine)
                what = "extrapolated"
            else:
                pen = pg.mkPen("#a0a0a0", width=PEN_WIDTH)
                what = "invalid"
            item = FastCurve(pen=pen, name=f"{self._names[key]} ({what})")
            (self.plot_item if self._side[key] == LEFT else self._ensure_right_vb()).addItem(item)
            extra[state] = item
        return item

    def y_range(self, side: str = LEFT) -> tuple[float, float]:
        vb = self._vb(side)
        lo, hi = vb.viewRange()[1]
        return float(lo), float(hi)

    def commit_view(self) -> None:
        """Apply pending range / matrix changes now, outside the paint event (one paint per refresh)."""
        for vb in self._viewboxes():
            vb.prepareForPaint()

    def x_range(self) -> tuple[float, float]:
        lo, hi = self.plot_item.getViewBox().viewRange()[0]
        return float(lo), float(hi)

    def set_x_range(self, lo: float, hi: float) -> None:
        if self.x_range() != (lo, hi):
            self.plot_item.setXRange(lo, hi, padding=0)

    def layout_entry(self) -> Any:
        """Persisted form (SW-RT-006): the channel keys in curve order."""
        return self.keys()

    def dispose(self) -> None:
        _LIVE_PANES.pop(self.token, None)


class XYPane(PlotPane):
    """Time-independent X-Y pane (travel–load, SW-RT-003 / SW-RT-006): ``data.xy(x_key, y_key, window_s)``, one
    curve + live point; x / y chosen from the registry candidates (travel channels; force or raw). Not part of the
    time X link and never a default target for ticked channels."""

    kind = "xy"
    selectionChanged = Signal(object)               # self

    def __init__(self, parent: QWidget | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent, clock)
        bar = QWidget(self)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(4, 0, 4, 0)
        bl.addWidget(QLabel("x", bar))
        self.x_combo = QComboBox(bar)
        bl.addWidget(self.x_combo, 1)
        bl.addWidget(QLabel("y", bar))
        self.y_combo = QComboBox(bar)
        bl.addWidget(self.y_combo, 1)
        self._content_layout.insertWidget(1, bar)
        self.selector_bar = bar
        self.x_combo.currentIndexChanged.connect(self._on_choice)
        self.y_combo.currentIndexChanged.connect(self._on_choice)
        self.y_user = False                       # True once the operator picked y himself
        self.y_combo.activated.connect(self._on_y_user)
        self.xy_curve = FastCurve(pen=pg.mkPen(PALETTE[0], width=PEN_WIDTH), name="x-y")
        self.plot_item.addItem(self.xy_curve)
        self.live_point = pg.ScatterPlotItem(size=8, brush=pg.mkBrush(PALETTE[1]), pen=None)
        self.plot_item.addItem(self.live_point)
        self._xauto = AutoRange()
        self._xrange: tuple[float, float] | None = None
        self.updates = 0
        self.update_title()

    def keys(self) -> list[str]:                    # no tree channels in an X-Y pane
        return []

    def accepts(self, unit: str) -> bool:
        return False

    def add_curve(self, *a: Any, **k: Any) -> bool:
        return False

    def auto_title(self) -> str:
        if not hasattr(self, "x_combo"):
            return "X-Y"
        return f"X-Y: {self.y_combo.currentText() or '?'} over {self.x_combo.currentText() or '?'}"

    def set_choices(self, x_opts: Sequence[tuple[str, str]], y_opts: Sequence[tuple[str, str]],
                    x: str | None = None, y: str | None = None) -> None:
        """(key, label) candidates; keeps the current choice when still offered."""
        for combo, opts, want in ((self.x_combo, x_opts, x), (self.y_combo, y_opts, y)):
            cur = want or combo.currentData()
            if combo is self.y_combo and not want and not self.y_user and opts:
                cur = opts[0][0]          # automatic y = the preferred one (force when calibrated, SW-RT-003)
            combo.blockSignals(True)
            combo.clear()
            for key, label in opts:
                combo.addItem(label, key)
            i = combo.findData(cur) if cur else -1
            combo.setCurrentIndex(i if i >= 0 else (0 if combo.count() else -1))
            combo.blockSignals(False)
        self._on_choice()

    def _on_y_user(self, *_a: Any) -> None:
        self.y_user = True

    @property
    def x_key(self) -> str | None:
        return self.x_combo.currentData()

    @property
    def y_key(self) -> str | None:
        return self.y_combo.currentData()

    def _on_choice(self, *_a: Any) -> None:
        self._xauto.reset()
        self._xrange = None
        self._reset_range(LEFT)
        self.plot_item.setLabel("bottom", self.x_combo.currentText())
        self.plot_item.setLabel("left", self.y_combo.currentText())
        self.update_title()
        self.selectionChanged.emit(self)

    def set_snapshot(self, snapshot: Any, now: float | None = None) -> None:   # time data is not used here
        return

    def set_x_range(self, lo: float, hi: float) -> None:                       # not time-linked
        return

    def update_xy(self, xy: Any, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        x = np.asarray(xy.x, dtype=float)
        y = np.asarray(xy.y, dtype=float)
        n = min(len(x), len(y))
        x, y = x[:n], y[:n]
        self.xy_curve.set_xy(x, y)
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.any():
            i = int(np.nonzero(ok)[0][-1])
            self.live_point.setData([x[i]], [y[i]])
        else:
            self.live_point.setData([], [])
        self.updates += 1
        if not (self._autoscale and not self._manual_y):
            return
        xr = self._xauto.update(data_range([x]), now)
        if xr is not None and xr != self._xrange:
            self.plot_item.vb.setXRange(xr[0], xr[1], padding=0)
            self._xrange = xr
        yr = self._auto[LEFT].update(data_range([y]), now)
        if yr is not None and yr != self._yrange[LEFT]:
            self.plot_item.vb.setYRange(yr[0], yr[1], padding=0)
            self._yrange[LEFT] = yr

    def layout_entry(self) -> Any:
        return {"type": "xy", "x": self.x_key, "y": self.y_key}
