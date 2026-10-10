"""``MainWindow``: toolbar, banner stack, tabs, docks, indicator bar, menus, close rules (SW_design_GUI §2).

Layout (M1):

* **Toolbar** (fixed: not movable / floatable / hideable, no context menu), items in this order: STOP (large,
  ``NoFocus``, fires on press, calls ``backend.stop("toolbar")`` synchronously) · Pause/Resume · Clear stop (badge)
  · TARE · Stream · Record · Take sample · stretch · link widget. No toolbar item has a keyboard shortcut.
* **Banner stack** in a second full-width toolbar row: stop banner, no-specimen mode banner, notice strip, toast.
* **Tabs** (GQ-01 order) in a scroll area: Connection & Config (M1), Safety limits, Test marks, Manual,
  Calibration & Tare (M3), Sequence, Report (M4). The current tab is refreshed every tick.
* **Wizards / popups** (non-modal, STOP inside): travel and load calibration wizards, TARE popup (toolbar TARE on
  every tab), Pause/Break key test (Tools menu); refreshed every tick while open.
* **Units**: View ▸ Units N / kgf (SYS-003) → readouts, X-Y pane, Manual / limits tabs (display only).
* **Docks**: Plot 1 (right), Readouts (right), Event log (bottom, hidden by default) — every dock has STOP.
* **Indicator bar**: fixed two-row strip in the bottom toolbar area (full width, never hidden).
* **Refresh**: one 33 ms PreciseTimer (``RefreshScheduler``) → ``gui_beat()``, ``status()``, then indicators /
  banners / gates / toolbar (every tick), plots (every tick), readouts (10 Hz), link line (1 Hz).
* **Pause/Break**: the backend's global hotkey (B §17) works before Connect; the GUI shows its mode on the KEY chip
  and installs app-level ``QShortcut``s (Pause, Ctrl+Break → ``backend.halt("app-shortcut")``) only while the
  global hotkey is UNAVAILABLE (§5.3; never both, GQ-19).

The window holds no business or safety logic (P1); every enable state comes from ``status().gates`` (§2.7).

Origin: structure follows Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/main_window.py @37c87471 (fixed toolbar,
suppressed context menu, STOP handler installation, close rules); rewritten for the bend-stand ``core.api``.

Implements: SW-STOP-001 (STOP first in the toolbar, synchronous), SW-STOP-002 (hotkey state, app-shortcut
fallback, KL-01 text), SW-STOP-003/004 (Clear stop, Pause/Resume), SW-ACQ-001 (Stream on every tab), SW-TARE-001
(TARE on every tab), SAF-SW-005 / SAF-SW-001 (SW-trip toasts: a new trip "STOP sent", a clear "SW limit cleared",
MC3-5), SW-SEQ-001 / SW-REP-001 (Sequence and Report tabs), SW-ACQ-002/003 (Record, Take sample on every tab), SAF-SW-005 (indicator bar), SW-RT-001
(plot dock with STOP), SW-RT-005 (readouts), SW-PLT-003 (link widget), IF-008 (notices), SW-RT-006 (plot windows with panes, one snapshot
per time window, layout persistence), SW-RT-001 (several plot windows, layout restored)
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bend_stand.core.api import GateId
from bend_stand.gui import gating, indicator_map
from bend_stand.gui.bridge import QtBridge
from bend_stand.gui.dialogs.about import AboutDialog
from bend_stand.gui.gc_policy import GC_TRACE
from bend_stand.gui.dialogs.clear_stop_dialog import ClearStopDialog
from bend_stand.gui.dialogs.confirm_dialog import make_confirm
from bend_stand.gui.dialogs.hotkey_test import HotkeyTestDialog
from bend_stand.gui.dialogs.link_stats import LinkStatsDialog
from bend_stand.gui.dialogs.safe_dialog import SafeMessageBox, get_open_file_name, get_save_file_name
from bend_stand.gui.dialogs.status_help import StatusHelpDialog
from bend_stand.gui.dialogs.tare_popup import TarePopup
from bend_stand.gui.mode_state import no_specimen
from bend_stand.gui.plots import render
from bend_stand.gui.plots.plot_dock import PlotDock
from bend_stand.gui.refresh import RefreshScheduler
from bend_stand.gui.resources import app_icon
from bend_stand.gui.stop import set_stop_handler
from bend_stand.gui.settings import KEY_LAST_TAB, KEY_OPENGL, KEY_UNITS
from bend_stand.gui.tabs.calibration_tab import CalibrationTab
from bend_stand.gui.tabs.connection_tab import ConnectionTab
from bend_stand.gui.tabs.limits_tab import LimitsTab
from bend_stand.gui.tabs.manual_tab import ManualTab
from bend_stand.gui.tabs.marks_tab import MarksTab
from bend_stand.gui.tabs.report_tab import ReportTab
from bend_stand.gui.tabs.sequence_tab import SequenceTab
from bend_stand.gui.theme import BANNER_STYLE
from bend_stand.gui.units_state import FORCE_UNITS, force_unit
from bend_stand.gui.widgets.event_log import EventLogDock
from bend_stand.gui.widgets.indicator_bar import IndicatorBar
from bend_stand.gui.widgets.mode_banner import ModeBanner
from bend_stand.gui.widgets.notice_strip import NoticeStrip
from bend_stand.gui.widgets.pause_button import PauseButton
from bend_stand.gui.widgets.readout import ReadoutDock
from bend_stand.gui.widgets.status_led import StatusLed
from bend_stand.gui.widgets.stop_banner import StopBanner
from bend_stand.gui.widgets.stop_button import StopButton
from bend_stand.gui.wizards.load_cal import LoadCalWizard
from bend_stand.gui.wizards.travel_cal import TravelCalWizard

log = logging.getLogger(__name__)

TAB_NAMES = ("Connection & Config", "Safety limits", "Test marks", "Manual", "Calibration & Tare", "Sequence",
             "Report")


def tab_label(name: str) -> str:
    """Tab text with "&" escaped: a bare "&" is a Qt mnemonic and would show "Connection _Config"."""
    return name.replace("&", "&&")
PARAMS_EVERY = 10                    # board values for the K1 chip (drv.k1_check_enable) every 10th tick
SAMPLE_WINDOWS_S = (0.1, 0.5, 1.0, 2.0, 5.0, 10.0)
TOAST_MS = 6000
TRIP_GRACE_S = 2.0                   # MC3-5: an announced trip identity is kept ≥ 2 s
DEFAULT_PLOT_KEYS = ("raw",)
MAX_PLOT_WINDOWS = 4                 # GQ-04
KEY_PLOT_LAYOUT = "plots/layout"     # SW-RT-006 / SW-RT-001: panes + curves per window (JSON)
KEY_DOCK_STATE = "main/state"        # dock arrangement (QMainWindow.saveState)
PLOT_LAYOUT_VERSION = 1
LINK_LED = {"CONNECTED": "green", "CONNECTING": "yellow", "DEGRADED": "yellow", "LOST": "red"}
#: link states in which a new recording may start (OI-UM-05 b): the backend's ``record_start`` gate only WARNs
#: "stream off", so without a link it would create an empty folder without board info (SW-ACQ-002). A running
#: recording can always be stopped (also after a link loss).
RECORD_LINK_STATES = frozenset({"CONNECTED", "DEGRADED"})
RECORD_NO_LINK_TEXT = "not connected — connect to the board first (a recording needs the data stream)"


def link_state_of(status: Any) -> str:
    link = getattr(status, "link", None)
    st = getattr(link, "state", "DISCONNECTED")
    return str(getattr(st, "value", st))


SESSION_FILTER = "Sessions (*.bbsession.json);;All files (*)"


def _err_text(exc: BaseException) -> str:
    return str(getattr(exc, "user_text", "") or exc or type(exc).__name__)


def sample_text(row: Any) -> str:
    """Toast text of a ``SampleRow`` (B5-13) — display only."""
    if not hasattr(row, "f_mean"):
        return str(getattr(row, "text", "") or row)
    from bend_stand.gui.format import fmt_value  # noqa: PLC0415
    txt = (f"F̄ = {fmt_value(row.f_mean, 'N')} N, σ {fmt_value(row.f_std, 'N')} N, N {row.f_n}; "
           f"x̄ = {fmt_value(row.x_mean, 'mm')} mm; raw {fmt_value(row.raw_mean, 'counts')} "
           f"({row.window_s:g} s)")
    if getattr(row, "file", None):
        txt += f" → {row.file}"
    return txt


def trip_key(trip: Any) -> tuple:
    """Identity of one latched SW trip (``SwTrip``): limit class + device time + text."""
    return (getattr(trip, "limit", None), getattr(trip, "t_us", None), getattr(trip, "text", ""))


def is_trip_clear(payload: Any) -> bool:
    """B's explicit clear signal (any of: payload ``None`` = all cleared; a payload flagged ``cleared`` /
    ``kind``/``edge`` CLEAR(ED); a payload type named ``*Clear*``)."""
    if payload is None:
        return True
    if getattr(payload, "cleared", False) is True:
        return True
    for attr in ("kind", "edge", "event"):
        v = str(getattr(payload, attr, "") or "").upper()
        if v in ("CLEAR", "CLEARED"):
            return True
    return "clear" in type(payload).__name__.lower()


def trip_announcement(payload: Any, announced: dict[tuple, float], *, cleared: bool = False) -> tuple[str, str]:
    """MC3-5 / SWD-M3-02 (pure; display only): classify one ``safety.trip`` publish.

    * a **new** trip (identity not announced before) → ``("error", "SW limit trip: <text> – STOP sent")`` and its
      identity is added to ``announced``;
    * a **clear** (payload None, B's clear signal, or the publish of a *remaining* latch already announced — B5-25:
      when one class clears the topic carries the remaining latest latch) → ``("info", "SW limit cleared …")``.
      No clear is ever announced as a STOP (no STOP is sent at a clear).
    """
    if cleared or is_trip_clear(payload):
        which = getattr(payload, "limit", None) if payload is not None else None
        if payload is None:
            announced.clear()
        else:                                   # B6-15 SwTripCleared(limit, remaining, latest, t_us)
            for k in [k for k in announced if k[0] == which or k == trip_key(payload)]:
                announced.pop(k, None)
        rest = [getattr(t, "limit", "") for t in (getattr(payload, "remaining", None) or ())]
        return "info", "SW limit cleared" + (f": {which}" if which else "") +             (f" – still latched: {', '.join(r for r in rest if r)}" if rest else "")
    key = trip_key(payload)
    if key in announced:
        return "info", f"SW limit cleared – still latched: {getattr(payload, 'limit', '') or payload}"
    announced[key] = time.monotonic()
    return "error", "SW limit trip: " + (getattr(payload, "text", "") or str(payload)) + " – STOP sent"


def latched_count(status: Any) -> int:
    """Badge of the Clear-stop button: latched HALT / PAUSED / ESTOP / faults (display only)."""
    ind = getattr(status, "indicators", None)
    if ind is None:
        return 0
    names = ["halt", "paused", "estop"] + [n.lower() for n in indicator_map.chip_members("FAULT") if n != "FAULT"] \
        + ["k1_welded", "load_limit", "limit_wiring"]
    return sum(1 for n in dict.fromkeys(names) if getattr(ind.get(n), "state", "") == "ON")


class MainWindow(QMainWindow):
    closed = Signal()

    def __init__(self, backend: Any, settings: Any = None, *, start_refresh: bool = True) -> None:
        super().__init__()
        self.backend = backend
        self.settings = settings
        self.setObjectName("mainWindow")
        self.setWindowTitle("Bird Bend Stand")
        icon = app_icon()                                     # F-B-PKG-01 (also when no app-wide icon is set)
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.bridge = QtBridge(backend, self)
        self._closing = False
        self._force_close = False
        self._app_shortcuts: list[QShortcut] = []
        self.dialogs: dict[str, Any] = {}
        self.confirm_dialog: Any = None
        self.last_status: Any = None
        self.exit_summary = ""
        self.tare_popup: TarePopup | None = None
        self.wizards: dict[str, Any] = {}
        self._params: Any = None
        self._status_n = 0
        self._prev_link_state = ""
        self.link_lost_diagnostics: list[str] = []      # MC3-4: one line per LINK LOST shown
        self._announced_trips: dict[tuple, float] = {}   # MC3-5: SW trips already announced (identity → time)

        # NFR-009 (D-54 b): render mode before the first plot pane exists (env override > gui.ini, default raster)
        render.set_opengl(render.wanted(settings.value(KEY_OPENGL, False) if settings is not None else False))
        self._build_toolbar()
        self._build_banner_bar()
        self._build_tabs()
        self._build_docks()
        self._build_indicator_bar()
        self._build_menus()
        self._wire_bridge()

        self._prev_stop_handler = set_stop_handler(self._on_stop)

        self.refresh = RefreshScheduler(backend, self)
        self.refresh.add_stage("status", self._on_status, every=1)
        self.refresh.add_stage("plots", self._on_plots, every=1)
        self.refresh.add_stage("readouts", self._on_readouts, every=3)
        self.refresh.add_stage("link", self.connection_tab.update_link_line, every=30)
        self._reload_channels()
        self.restore_layout()
        self._restore_ui_prefs()
        try:
            st = backend.status()
            self._on_status(st)
            self.connection_tab.update_link_line(st)
        except Exception:  # noqa: BLE001
            log.warning("initial status() failed", exc_info=True)
        self.show_session_issues("Session at start")
        if start_refresh:
            self.refresh.start()
        self.resize(1500, 880)

    # ================================================================== construction
    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.toggleViewAction().setVisible(False)
        tb.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self.toolbar = tb

        self.stop_button = StopButton(tb, large=True, source="toolbar")
        tb.addWidget(self.stop_button)

        self.pause_button = PauseButton(self.backend, tb)
        self.pause_button.stopResult.connect(self._show_stop_result)
        self.pause_button.resumeResult.connect(self._on_resume_result)
        tb.addWidget(self.pause_button)

        self.clear_button = self._tool_button(tb, "Clear stop", "clearStopButton")
        self.clear_button.clicked.connect(self.open_clear_stop)

        self.tare_button = self._tool_button(tb, "TARE", "tareButton")
        f = self.tare_button.font()
        f.setBold(True)
        self.tare_button.setFont(f)
        self.tare_button.clicked.connect(lambda: self.on_tare(None))

        self.stream_button = self._tool_button(tb, "▶ Stream", "streamButton")
        self.stream_button.setCheckable(True)
        self.stream_button.clicked.connect(self.on_stream)

        self.record_button = self._tool_button(tb, "● Record", "recordButton")
        self.record_button.setCheckable(True)
        self.record_button.clicked.connect(self.on_record)

        self.sample_button = self._tool_button(tb, "Take sample (1 s)", "sampleButton")
        self.sample_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        menu = QMenu(self.sample_button)
        for w in SAMPLE_WINDOWS_S:
            act = menu.addAction(f"{w:g} s")
            act.triggered.connect(lambda _c=False, ws=w: self.on_take_sample(ws))
        self.sample_button.setMenu(menu)
        self.sample_button.clicked.connect(lambda: self.on_take_sample(1.0))

        spacer = QWidget(tb)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        link = QWidget(tb)
        ll = QHBoxLayout(link)
        ll.setContentsMargins(4, 0, 4, 0)
        self.link_led = StatusLed(link, "off", 12)
        self.link_text = QToolButton(link)
        self.link_text.setObjectName("linkText")
        self.link_text.setText("DISCONNECTED")
        self.link_text.setAutoRaise(True)
        self.link_text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.link_text.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        self.link_button = QToolButton(link)
        self.link_button.setObjectName("linkButton")
        self.link_button.setText("Connect")
        self.link_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.link_button.clicked.connect(self._on_link_button)
        ll.addWidget(self.link_led)
        ll.addWidget(self.link_text)
        ll.addWidget(self.link_button)
        tb.addWidget(link)

        self.gates = gating.GateBinder()
        self.gates.bind(self.tare_button, GateId.TARE, base_tooltip="Tare (session-only; reachable from every tab)")
        self.gates.bind(self.sample_button, GateId.SAMPLE, base_tooltip="Take a momentary sample")

    def _tool_button(self, tb: QToolBar, text: str, name: str) -> QToolButton:
        b = QToolButton(tb)
        b.setObjectName(name)
        b.setText(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setMinimumHeight(40)
        tb.addWidget(b)
        return b

    def createPopupMenu(self) -> QMenu | None:  # noqa: N802 - no toolbar/dock context menu (toolbar fixed)
        return None

    def _build_banner_bar(self) -> None:
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        bar = QToolBar("Banners", self)
        bar.setObjectName("bannerBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.toggleViewAction().setVisible(False)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        stack = QWidget(bar)
        stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        lay = QVBoxLayout(stack)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self.stop_banner = StopBanner(stack)
        self.mode_banner = ModeBanner(stack)
        self.notice_strip = NoticeStrip(stack)
        self.toast_label = QLabel("", stack)
        self.toast_label.setObjectName("toast")
        self.toast_label.setWordWrap(True)
        self.toast_label.hide()
        for w in (self.stop_banner, self.mode_banner, self.notice_strip, self.toast_label):
            lay.addWidget(w)
        bar.addWidget(stack)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.banner_bar = bar
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)
        self.stop_banner.clearStopRequested.connect(self.open_clear_stop)
        self.stop_banner.resumeRequested.connect(lambda: self._on_resume_result(self.backend.resume("banner")))
        self.stop_banner.connectionRequested.connect(lambda: self.tabs.setCurrentIndex(0))
        self.stop_banner.noSpecimenRequested.connect(self.enter_no_specimen)
        self.mode_banner.leaveRequested.connect(self._leave_no_specimen)
        self.notice_strip.actionRequested.connect(self._on_notice_action)

    def _build_tabs(self) -> None:
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("mainTabs")
        self.connection_tab = ConnectionTab(self.backend, self.bridge, self.tabs)
        self.connection_tab.message.connect(self.toast)
        self.tabs.addTab(self.connection_tab, tab_label(TAB_NAMES[0]))
        self.limits_tab = LimitsTab(self.backend, self.bridge, self.tabs)
        self.marks_tab = MarksTab(self.backend, self.bridge, self.tabs)
        self.manual_tab = ManualTab(self.backend, self.bridge, self.tabs)
        self.calibration_tab = CalibrationTab(self.backend, self.bridge, self.tabs)
        for name, tab in zip(TAB_NAMES[1:5], (self.limits_tab, self.marks_tab, self.manual_tab,
                                               self.calibration_tab), strict=True):
            tab.message.connect(self.toast)
            self.tabs.addTab(tab, tab_label(name))
        self.sequence_tab = SequenceTab(self.backend, self.bridge, self.tabs)
        self.report_tab = ReportTab(self.backend, self.bridge, self.tabs)
        for name, tab in zip(TAB_NAMES[5:], (self.sequence_tab, self.report_tab), strict=True):
            tab.message.connect(self.toast)
            self.tabs.addTab(tab, tab_label(name))
        self.sequence_tab.stopResult.connect(self._show_stop_result)
        self.sequence_tab.resumeResult.connect(self._on_resume_result)
        self.manual_tab.resumeRequested.connect(lambda: self._on_resume_result(self.backend.resume("manual")))
        self.calibration_tab.travelWizardRequested.connect(self.open_travel_wizard)
        self.calibration_tab.loadWizardRequested.connect(self.open_load_wizard)
        self.calibration_tab.tareRequested.connect(self.on_tare)
        self.calibration_tab.travelActionRequested.connect(lambda a: self._on_notice_action(f"travel:{a}"))
        self.tabs.currentChanged.connect(self._on_tab_changed)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.tabs)
        self.setCentralWidget(scroll)

    def _build_docks(self) -> None:
        self.setDockNestingEnabled(True)
        self.plot_docks: list[PlotDock] = []
        self.snapshot_calls = 0
        self._specs: list[Any] = []
        self.plot_dock = self._make_plot_dock("Plot 1")
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.plot_dock)
        self.readout_dock = ReadoutDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.readout_dock)
        self.splitDockWidget(self.plot_dock, self.readout_dock, Qt.Orientation.Vertical)
        self.event_log = EventLogDock(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.event_log)
        self.event_log.hide()

    def _build_indicator_bar(self) -> None:
        bar = QToolBar("Indicators", self)
        bar.setObjectName("indicatorToolbar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.toggleViewAction().setVisible(False)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.indicator_bar = IndicatorBar(bar)
        self.indicator_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.indicator_bar.chipClicked.connect(self.open_status_help)
        bar.addWidget(self.indicator_bar)
        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, bar)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m_file = mb.addMenu("&File")
        m_file.addAction("Open session…", self.open_session)
        m_file.addAction("Save session as…", self.save_session)
        m_file.addSeparator()
        act = QAction("E&xit", self)
        act.setShortcut(QKeySequence("Ctrl+Q"))
        act.triggered.connect(self.close)
        m_file.addAction(act)
        m_view = mb.addMenu("&View")
        self.view_menu = m_view
        m_view.addAction("New plot window", self.new_plot_window)
        m_view.addAction("Save layout now", self.save_layout)
        m_units = m_view.addMenu("Units")
        self.unit_actions: dict[str, QAction] = {}
        grp = QActionGroup(self)
        grp.setExclusive(True)
        for u in FORCE_UNITS:
            act = QAction(u, self, checkable=True)
            act.triggered.connect(lambda _c=False, uu=u: self.set_force_unit(uu))
            grp.addAction(act)
            m_units.addAction(act)
            self.unit_actions[u] = act
        self.unit_actions["N"].setChecked(True)
        self.opengl_action = QAction("OpenGL rendering", self, checkable=True)
        self.opengl_action.setChecked(render.active())
        self.opengl_action.setToolTip("Draw the plot panes with OpenGL (GPU). Off = software raster (default, faster "
                                      "on the DEV PC). Falls back to raster when OpenGL is not available.")
        self.opengl_action.toggled.connect(self.set_opengl)
        m_view.addAction(self.opengl_action)
        m_view.addSeparator()
        for dock in (*self.plot_docks, self.readout_dock, self.event_log):
            m_view.addAction(dock.toggleViewAction())
        m_tools = mb.addMenu("&Tools")
        m_tools.addAction("Link statistics…", self.open_link_stats)
        self.hotkey_test_action = m_tools.addAction("Test Pause/Break key…", self.open_hotkey_test)
        m_help = mb.addMenu("&Help")
        m_help.addAction("Status indicators && clear procedures…", lambda: self.open_status_help(None))
        m_help.addAction("Keyboard…", self.open_keyboard_help)
        m_help.addAction("About…", self.open_about)

    def _wire_bridge(self) -> None:
        b = self.bridge
        b.anyEvent.connect(self.event_log.add_record)
        b.stopIssued.connect(self._on_stop_event)
        b.fwEvent.connect(self._on_fw_event)
        b.resumeIgnored.connect(self._on_resume_ignored)
        b.channelsChanged.connect(lambda _r: self._reload_channels())
        b.recordingEvent.connect(self._on_recording_event)
        b.safetyEvent.connect(self._on_safety_event)
        b.boardChanged.connect(self._on_board_changed)
        b.hotkeyEvent.connect(self._on_hotkey_event)

    # ================================================================== refresh stages
    def _on_status(self, status: Any) -> None:
        self.last_status = status
        self._status_n += 1
        no_specimen().set(bool(getattr(status.safety, "no_specimen_mode", False))       # tags: mode or D-54 a scope
                          or bool(getattr(status.safety, "no_specimen_scope", None)))
        self._track_trips(status)
        if self._status_n % PARAMS_EVERY == 1:
            try:
                self._params = self.backend.config.values()
            except Exception:  # noqa: BLE001
                self._params = None
        self.indicator_bar.update_status(status, self._params)
        self._check_link_lost(status)
        self.stop_banner.update_status(status)
        self.mode_banner.update_status(status)
        self.notice_strip.update_status(status)
        self.pause_button.update_status(status)
        self.gates.update(status)
        self._update_toolbar(status)
        self._update_hotkey_fallback(status)
        self.connection_tab.update_status(status)
        tab = self.tabs.currentWidget()
        if tab is not self.connection_tab and hasattr(tab, "update_status"):
            tab.update_status(status)
        self._update_windows(status)
        self._update_banner_bar()

    def _track_trips(self, status: Any) -> None:
        """MC3-5: the first status seeds the trips latched before the window existed; later ticks drop the
        identities no longer latched (after a 2 s grace, so a status rebuilt later than the event cannot drop a
        just-announced trip). A new trip is announced by its ``safety.trip`` event only."""
        latched = {trip_key(t) for t in getattr(status.safety, "trips", ()) or ()}
        now = time.monotonic()
        if self._status_n == 1:
            for k in latched:
                self._announced_trips.setdefault(k, now)
            return
        for k, t0 in list(self._announced_trips.items()):
            if k not in latched and now - t0 > TRIP_GRACE_S:
                del self._announced_trips[k]

    def _check_link_lost(self, status: Any) -> None:
        """MC3-4 / OBS-M3-R1: with every LINK LOST the GUI shows, log the longest GUI refresh-tick gap and the last
        / longest cyclic-GC collection, so a false LINK LOST under host load can be traced to a process stall."""
        link = getattr(status, "link", None)
        state = str(getattr(getattr(link, "state", None), "value", getattr(link, "state", "")))
        if state == "LOST" and self._prev_link_state != "LOST":
            gap, ago = self.refresh.max_gap(10.0)
            ps = self.refresh.perf_stats()
            text = (f"LINK LOST shown (why: {getattr(link, 'why', '') or '-'}); GUI refresh: longest tick gap in the "
                    f"last 10 s {gap:.0f} ms (ended {ago:.1f} s ago), interval p95 {ps['interval_p95_ms']:.1f} ms, "
                    f"max {ps['interval_max_ms']:.1f} ms; {GC_TRACE.describe()}")
            self.link_lost_diagnostics.append(text)
            log.warning("%s", text)
            self.event_log.add_text("GUI", "LINK_LOST_DIAG", text, "warn")
        self._prev_link_state = state

    def _update_windows(self, status: Any) -> None:
        """Open wizards, the tare popup and the hotkey test follow the engines every tick (§6.1)."""
        for w in list(self.wizards.values()):
            if _alive(w) and w.isVisible():
                w.refresh()
        if self.tare_popup is not None and _alive(self.tare_popup) and self.tare_popup.isVisible():
            self.tare_popup.refresh()
        hk = self.dialogs.get("hotkey")
        if hk is not None and _alive(hk) and hk.isVisible():
            hk.update_status(status)

    def _on_tab_changed(self, _i: int) -> None:
        tab = self.tabs.currentWidget()
        if self.last_status is not None and hasattr(tab, "update_status") and tab is not self.connection_tab:
            tab.update_status(self.last_status)

    def _update_banner_bar(self) -> None:
        any_shown = any(not w.isHidden() for w in (self.stop_banner, self.mode_banner, self.notice_strip,
                                                    self.toast_label))
        if self.banner_bar.isHidden() == any_shown:
            self.banner_bar.setVisible(any_shown)

    def _update_toolbar(self, st: Any) -> None:
        # Clear stop: any clear gate without a REFUSE ("nothing to clear") → enabled
        gates = [gating.gate_of(st, g) for g in (GateId.CLEAR_STOP, GateId.ESTOP_CLEAR, GateId.FAULT_CLEAR)]
        can_clear = any(g is not None and g.ok for g in gates)
        n = latched_count(st)
        text = f"Clear stop ({n})" if n else "Clear stop"
        if self.clear_button.text() != text:
            self.clear_button.setText(text)
        self.clear_button.setEnabled(can_clear or n > 0)
        # Stream: checked state follows status().stream.on only (P4)
        on = bool(st.stream.on)
        if self.stream_button.isChecked() != on:
            self.stream_button.setChecked(on)
        self.stream_button.setText("■ Stream" if on else "▶ Stream")
        cs = gating.control_state(gating.gate_of(st, GateId.STREAM_STOP if on else GateId.STREAM_START),
                                  motion=False, base_tooltip="Stop the DATA stream" if on else "Start the DATA stream")
        self.stream_button.setEnabled(cs.enabled)
        self.stream_button.setToolTip(cs.tooltip)
        # Record
        rec = st.recording.state == "RECORDING"
        if self.record_button.isChecked() != rec:
            self.record_button.setChecked(rec)
        cs = gating.control_state(gating.gate_of(st, GateId.RECORD_STOP if rec else GateId.RECORD_START),
                                  motion=False, base_tooltip="Stop recording" if rec else "Start recording")
        enabled, tip = cs.enabled, cs.tooltip
        if not rec and link_state_of(st) not in RECORD_LINK_STATES:      # Implements: SW-ACQ-002 (OI-UM-05 b)
            enabled, tip = False, f"Start recording: {RECORD_NO_LINK_TEXT}"
        if self.record_button.isEnabled() != enabled:
            self.record_button.setEnabled(enabled)
        if self.record_button.toolTip() != tip:
            self.record_button.setToolTip(tip)
        # link widget
        state = str(getattr(st.link.state, "value", st.link.state))
        rate = st.stream.rate_sps
        txt = f"{state} {st.link.endpoint or ''}".strip() + (f" {rate:.1f} Hz" if rate is not None else "")
        if self.link_text.text() != txt:
            self.link_text.setText(txt)
        self.link_led.set_color(LINK_LED.get(state, "off"))
        connected = state in ("CONNECTED", "DEGRADED", "LOST", "CONNECTING")
        self.link_button.setText("Disconnect" if connected else "Connect")
        self.link_button.setEnabled(connected or self.connection_tab.connect_button.isEnabled())
        self.hotkey_test_action.setEnabled(not gating.is_refused(gating.gate_of(st, GateId.HOTKEY_TEST),
                                                                 motion=True))

    def _update_hotkey_fallback(self, st: Any) -> None:
        """App-level Pause / Ctrl+Break only while the global hotkey is UNAVAILABLE (§5.3, GQ-19)."""
        need = getattr(st.hotkey, "mode", "UNAVAILABLE") == "UNAVAILABLE"
        if need and not self._app_shortcuts:
            for seq in (QKeySequence(Qt.Key.Key_Pause), QKeySequence("Ctrl+Break")):
                sc = QShortcut(seq, self)
                sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
                sc.activated.connect(self._on_app_halt)
                self._app_shortcuts.append(sc)
        elif not need and self._app_shortcuts:
            for sc in self._app_shortcuts:
                sc.setEnabled(False)
                sc.deleteLater()
            self._app_shortcuts = []

    def app_shortcuts(self) -> list[QShortcut]:
        return list(self._app_shortcuts)

    def _on_app_halt(self) -> None:
        result = self.backend.halt("app-shortcut")
        self._show_stop_result(result)

    # ================================================================== plot windows (SW-RT-001 / SW-RT-006)
    def _make_plot_dock(self, title: str) -> PlotDock:
        dock = PlotDock(title, self)
        dock.infoMessage.connect(lambda t: self.toast(t, "warn"))
        dock.peers = lambda d=dock: [o for o in self.plot_docks if o is not d]
        dock.new_dock_factory = self.new_plot_window
        self.plot_docks.append(dock)
        if hasattr(self, "view_menu"):
            self.view_menu.addAction(dock.toggleViewAction())
        return dock

    def new_plot_window(self, title: str | None = None) -> PlotDock | None:
        """View > New plot window (max. 4, GQ-04); tabified with Plot 1, channels from the registry."""
        if len(self.plot_docks) >= MAX_PLOT_WINDOWS:
            self.toast(f"At most {MAX_PLOT_WINDOWS} plot windows", "warn")
            return None
        used = {d.windowTitle() for d in self.plot_docks}
        title = title or next(f"Plot {n}" for n in range(1, 99) if f"Plot {n}" not in used)
        dock = self._make_plot_dock(title)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.tabifyDockWidget(self.plot_dock, dock)
        dock.set_channels(self._specs)
        dock.show()
        dock.raise_()
        return dock

    def _on_plots(self, _status: Any) -> None:
        """One ``data.snapshot`` per distinct time-window length for the union of the channels of every shown,
        non-frozen plot window (section 4.6, D-38); X-Y panes pull ``data.xy``."""
        groups: dict[float, list[PlotDock]] = {}
        for d in self.plot_docks:
            if d.wants_snapshot():
                groups.setdefault(d.window_s, []).append(d)
        data = self.backend.data
        for window_s, docks in groups.items():
            keys = list(dict.fromkeys(k for d in docks for k in d.snapshot_keys()))
            px = max(d.px_width() for d in docks)
            try:
                snap = data.snapshot(keys, window_s, px)
            except Exception as exc:  # noqa: BLE001 - not connected / not implemented yet
                for d in docks:
                    d.show_error(f"no data: {exc}")
                continue
            self.snapshot_calls += 1
            for d in docks:
                d.set_snapshot(snap)
        for d in self.plot_docks:
            if d.xy_panes():
                d.refresh_xy(data)

    # ---------------------------------------------------------------- layout persistence
    def layout_state(self) -> dict[str, Any]:
        return {"version": PLOT_LAYOUT_VERSION, "docks": [d.layout_state() for d in self.plot_docks]}

    def save_layout(self) -> None:
        """Plot windows (panes, curves, columns, titles) + dock arrangement -> QSettings (SW-RT-001 / SW-RT-006)."""
        if self.settings is None:
            return
        try:
            self.settings.setValue(KEY_PLOT_LAYOUT, json.dumps(self.layout_state()))
            self.settings.setValue(KEY_DOCK_STATE, self.saveState())
            self.settings.sync()
        except Exception:  # noqa: BLE001
            log.warning("saving the layout failed", exc_info=True)

    def restore_layout(self) -> None:
        """Rebuild the plot windows from QSettings; a missing / corrupt entry gives the default layout."""
        if self.settings is None:
            return
        raw = self.settings.value(KEY_PLOT_LAYOUT)
        if not raw:
            return
        try:
            state = json.loads(str(raw))
            docks = [d for d in state.get("docks", []) if isinstance(d, dict)]
        except (ValueError, AttributeError):
            log.warning("plot layout in the settings is corrupt: default layout used")
            return
        for i, ds in enumerate(docks[:MAX_PLOT_WINDOWS]):
            dock = self.plot_docks[i] if i < len(self.plot_docks) else self.new_plot_window(
                str(ds.get("title") or f"Plot {i + 1}"))
            if dock is not None:
                dock.restore_layout_state(ds)
        st = self.settings.value(KEY_DOCK_STATE)
        if st is not None:
            try:
                self.restoreState(st)
            except Exception:  # noqa: BLE001
                log.warning("dock arrangement in the settings is unusable", exc_info=True)

    def _on_readouts(self, _status: Any) -> None:
        if self.readout_dock.isVisible():
            self.readout_dock.refresh(self.backend.data, self._channel_keys)

    def _reload_channels(self) -> None:
        try:
            specs = list(self.backend.channels.channels())
        except Exception:  # noqa: BLE001
            log.warning("channels() failed", exc_info=True)
            specs = []
        first = not self._channel_keys if hasattr(self, "_channel_keys") else True
        self._channel_keys = {s.key for s in specs}
        self._specs = specs
        for d in self.plot_docks:
            # first start: raw counts ticked in Plot 1 (a saved layout replaces this, restore_layout)
            d.set_channels(specs, DEFAULT_PLOT_KEYS if first and d is self.plot_dock else ())

    # ================================================================== STOP / pause / resume
    def _on_stop(self, source: str) -> None:
        """Installed STOP handler: ``backend.stop`` first, synchronously in the GUI thread (§5.2)."""
        result = self.backend.stop(source)
        self._show_stop_result(result)

    def _show_stop_result(self, result: Any) -> None:
        self.stop_banner.show_stop_result(result)
        if self.last_status is not None:
            self.stop_banner.update_status(self.last_status)
            self._update_banner_bar()

    def _on_stop_event(self, record: Any) -> None:
        self.stop_banner.on_stop_event(record)
        if self.last_status is not None:
            self.stop_banner.update_status(self.last_status)
            self._update_banner_bar()

    def _on_resume_result(self, gate: Any) -> None:
        if gate is None:
            return
        if getattr(gate, "ok", False):
            self.toast("Resume sent", "info")
            return
        first = gating.clear_first_items(gate) if hasattr(gate, "items") else ()
        if first:
            self.toast("Clear stop first: " + "; ".join(i.text for i in first), "warn")
        else:
            self.toast(f"Resume refused: {gate.text()}", "warn")

    def _on_resume_ignored(self, record: Any) -> None:
        p = record.payload
        reason = p.reason.text() if hasattr(getattr(p, "reason", None), "text") else str(getattr(p, "reason", ""))
        self.toast(f"Resume request ignored: {reason}", "warn")

    def _on_fw_event(self, record: Any) -> None:
        name = getattr(record.payload, "name", "")
        if name == "RESUME_REQUEST":
            self.toast("PAUSE button pressed while PAUSED: resume requested", "info")
        elif name == "CLK_FALLBACK":
            self.toast("Board runs on the HSI fallback clock (timing ±1 %)", "warn")

    def _on_recording_event(self, record: Any) -> None:
        if record.topic == "sample.taken":
            self.toast("Sample taken: " + sample_text(record.payload), "info")
        elif record.topic == "rec.failure":
            self.toast(f"Recording failed: {getattr(record.payload, 'text', '') or record.payload}", "error")

    def _on_safety_event(self, record: Any) -> None:
        if record.topic == "safety.no_specimen":
            p = record.payload
            on = p if isinstance(p, bool) else getattr(p, "on", None)       # B5-02: payload bool
            if on is False:
                why = getattr(p, "reason", "") if not isinstance(p, bool) else ""
                self.toast("No-specimen mode ended" + (f" ({why})" if why else "") + " – PC load limits active again",
                           "warn")
            elif on is True:
                self.toast("No-specimen mode ON – PC load limits off for this session", "warn")
        elif record.topic == "safety.no_specimen_scope":            # D-54 a (SW-CAL-002)
            scope = record.payload
            if scope:
                self.toast(f"No specimen ({str(scope).lower().replace('_', ' ')}): PC load limits off for the "
                           "wizard's moves — do not mount a specimen", "warn")
            else:
                self.toast("Wizard ended: PC load limits apply again", "info")
        elif record.topic == "safety.trip" or record.topic.startswith("safety.trip"):
            # MC3-5 / SWD-M3-02: only a *new* trip is announced as an error with "STOP sent"; a clear is info
            severity, text = trip_announcement(record.payload, self._announced_trips,
                                               cleared=record.topic != "safety.trip")   # B's dedicated clear topic
            self.toast(text, severity)

    def _on_board_changed(self, record: Any) -> None:
        """Implements: SAF-SW-005, SW-LIM-001 (B6-33 (6), SWR-19) — another board after a reconnect: acknowledgement
        C-15 (STOP in the dialog, Enter / Space never confirm; acknowledging only closes it — nothing is sent)."""
        uid = str(getattr(record, "payload", "") or "?")
        text = (f"A different board is connected (UID {uid}). The test travel zero was reset to the machine zero. "
                "Check the SW travel limits, the travel and load calibration and tare again before the next test.")
        self.toast(f"Different board connected (UID {uid}): test travel zero reset", "warn")
        old = self.dialogs.get("board")
        if old is not None and _alive(old) and old.isVisible():
            old.text_label.setText(text)
            old.raise_()
            return
        dlg = make_confirm(self, "C-15", text=text)
        dlg.cancel_button.setText("Close")
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialogs["board"] = dlg
        dlg.open()

    def _on_hotkey_event(self, record: Any) -> None:
        """Implements: SW-STOP-002 (B6-33 (5), SWR-09) — the global Pause/Break hotkey stopped responding: warn on the
        edge; the KEY chip turns red "NOT RESPONDING" and the app-level shortcut takes over (§5.3)."""
        if getattr(record, "topic", "") != "hotkey.state":
            return
        p = getattr(record, "payload", None)
        if getattr(p, "mode", "") == "UNAVAILABLE" and "not responding" in str(getattr(p, "reason", "")):
            self.toast("Pause/Break key not responding — it works only while this window is focused; use the "
                       "on-screen STOP or the red E-stop", "error")

    def show_session_issues(self, context: str = "Session") -> list[str]:
        """Implements: SW-LIM-003 (B6-33 (2)) — issues of the last session load (``session.load_issues``: e.g.
        LOAD_LIMITS_RESTORED, a broken file kept as ``.bad``) as a toast + Event-log rows; returns the texts."""
        issues = list(getattr(getattr(self.backend, "session", None), "load_issues", None) or ())
        texts = [str(getattr(i, "text", i)) for i in issues]
        for i, t in zip(issues, texts, strict=True):
            sev = "error" if str(getattr(i, "severity", "")) == "ERROR" else "warn"
            self.event_log.add_text("GUI", str(getattr(i, "code", "") or "SESSION"), f"{context}: {t}", sev)
        if texts:
            worst = "error" if any(str(getattr(i, "severity", "")) == "ERROR" for i in issues) else "warn"
            self.toast(f"{context}: " + "; ".join(texts), worst)
        return texts

    def open_clear_stop(self) -> None:
        dlg = self.dialogs.get("clear")
        if dlg is None or not _reusable(dlg):
            dlg = ClearStopDialog(self.backend, self.bridge, self)
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self.dialogs["clear"] = dlg
        dlg.show()
        dlg.raise_()

    # ================================================================== toolbar actions
    def on_tare(self, window_s: float | None = None) -> None:
        """Toolbar / Calibration-tab TARE (every tab): non-modal TarePopup (§6.4); refusals verbatim."""
        popup = self.tare_popup
        if popup is None or not _reusable(popup):
            popup = TarePopup(self.backend, self.bridge, self)
            popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            popup.message.connect(self.toast)
            self.tare_popup = popup
            self.dialogs["tare"] = popup
        anchor = self.mapToGlobal(self.toolbar.geometry().bottomLeft())
        popup.move(anchor.x() + 40, anchor.y() + 4)
        popup.show()
        popup.raise_()
        gate = popup.start(window_s if isinstance(window_s, (int, float)) and not isinstance(window_s, bool)
                           else None)
        if gate is None:
            return
        if gate.ok:
            self.toast("Tare started", "info")
        else:
            self.toast(f"Tare refused: {gating.refusal_text(gate)}", "warn")

    def on_stream(self) -> None:
        st = self.backend.status()
        on = bool(st.stream.on)
        self.stream_button.setChecked(on)               # P4: state follows the backend, not the click
        try:
            fut = self.backend.stream_stop_async() if on else self.backend.stream_start_async()
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Stream {'stop' if on else 'start'} refused: {_err_text(exc)}", "warn")
            return
        self.bridge.watch(fut, None, lambda e: self.toast(f"Stream {'stop' if on else 'start'} failed: "
                                                          f"{_err_text(e)}", "error"), "stream")

    def on_record(self) -> None:
        st = self.backend.status()
        rec = st.recording.state == "RECORDING"
        self.record_button.setChecked(rec)
        if not rec and link_state_of(st) not in RECORD_LINK_STATES:      # Implements: SW-ACQ-002 (OI-UM-05 b)
            self.toast(f"Record start refused: {RECORD_NO_LINK_TEXT}", "warn")
            return
        gate = self.backend.record_stop() if rec else self.backend.record_start()
        if not gate.ok:
            self.toast(f"Record {'stop' if rec else 'start'} refused: {gating.refusal_text(gate)}", "warn")
        elif gate.warnings:
            self.toast("; ".join(i.text for i in gate.warnings), "warn")

    def on_take_sample(self, window_s: float) -> None:
        gate = self.backend.take_sample(window_s)
        if not gate.ok:
            self.toast(f"Sample refused: {gating.refusal_text(gate)}", "warn")
        elif gate.warnings:
            self.toast("; ".join(i.text for i in gate.warnings), "info")

    def _on_link_button(self) -> None:
        if self.link_button.text() == "Disconnect":
            self.connection_tab.on_disconnect()
        else:
            self.connection_tab.on_connect()

    def _leave_no_specimen(self) -> None:
        gate = self.backend.limits.set_no_specimen_mode(False)
        if not gate.ok:
            self.toast(f"Leave no-specimen mode refused: {gating.refusal_text(gate)}", "warn")

    def _on_notice_action(self, action: str) -> None:
        if action == "about":
            self.open_about()
        elif action == "connection":
            self.tabs.setCurrentIndex(0)
        elif action == "save_reboot":
            self.connection_tab.on_save_reboot()
        elif action.startswith("travel:"):
            what = action.split(":", 1)[1]
            try:
                fut = self.backend.calibrations.resolve_travel_difference_async(what)
            except Exception as exc:  # noqa: BLE001
                self.toast(f"Travel calibration: {_err_text(exc)}", "warn")
                return
            self.bridge.watch(fut, lambda _r: self.toast(f"Travel calibration: {what} done", "info"),
                              lambda e: self.toast(f"Travel calibration: {_err_text(e)}", "error"), "travel")

    # ================================================================== dialogs / help
    def open_status_help(self, chip: str | None) -> None:
        st = self.last_status if self.last_status is not None else self.backend.status()
        dlg = StatusHelpDialog(st, chip, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialogs["help"] = dlg
        dlg.show()

    def open_link_stats(self) -> None:
        dlg = LinkStatsDialog(self.backend, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialogs["link"] = dlg
        dlg.show()

    def open_about(self) -> None:
        dlg = AboutDialog(self.last_status if self.last_status is not None else self.backend.status(), self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialogs["about"] = dlg
        dlg.show()

    def open_keyboard_help(self) -> None:
        self.dialogs["keys"] = SafeMessageBox.show_message(
            self, "Keyboard", "Pause/Break and Ctrl+Break: HALT (latched stop, system-wide; works before Connect).\n"
                              "No other keyboard stop key exists (GQ-19).\n\n" + indicator_map.KL01_TEXT)

    def open_hotkey_test(self) -> Any:
        dlg = self.dialogs.get("hotkey")
        if dlg is None or not _reusable(dlg):
            dlg = HotkeyTestDialog(self.backend, self.bridge, self)
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self.dialogs["hotkey"] = dlg
        dlg.show()
        dlg.raise_()
        return dlg

    # ================================================================== wizards / no-specimen / units
    def _open_wizard(self, kind: str, cls: Any, engine: Any) -> Any:
        w = self.wizards.get(kind)
        if w is None or not _reusable(w):
            w = cls(self.backend, self.bridge, engine, self)
            w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            w.message.connect(self.toast)
            w.noSpecimenRequested.connect(self.enter_no_specimen)
            self.wizards[kind] = w
        w.show()
        w.raise_()
        w.refresh()
        return w

    def open_travel_wizard(self) -> Any:
        return self._open_wizard("travel_cal", TravelCalWizard, self.backend.travel_cal)

    def open_load_wizard(self) -> Any:
        return self._open_wizard("load_cal", LoadCalWizard, self.backend.load_cal)

    def open_session(self) -> None:
        """File ▸ Open session… (SW-LIM-003): ``session.load(path)``; tabs re-read limits / speeds / unit."""
        path = get_open_file_name(self, "Open session", "", SESSION_FILTER)
        if not path:
            return
        try:
            s = self.backend.session.load(path)
        except Exception as exc:  # noqa: BLE001 - FileFormatError / OSError
            self.toast(f"Session not loaded: {_err_text(exc)}", "error")
            return
        self.limits_tab.revert()
        self.manual_tab._load_session_defaults()                         # noqa: SLF001
        self.sequence_tab.mark_pull_dir(None)
        unit = getattr(s, "display_unit", None)
        if unit in FORCE_UNITS:
            self.set_force_unit(unit)
        if not self.show_session_issues(f"Session {path}"):
            self.toast(f"Session loaded: {path}", "info")

    def save_session(self) -> None:
        """File ▸ Save session as… (SW-LIM-003): ``session.save(path)`` (limits, speeds, unit; never tare / mode)."""
        path = get_save_file_name(self, "Save session", "", SESSION_FILTER, "bbsession.json")
        if not path:
            return
        try:
            self.backend.session.save(path)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Session not saved: {_err_text(exc)}", "error")
            return
        self.toast(f"Session saved: {path}", "info")

    def enter_no_specimen(self) -> None:
        """Every [Enter no-specimen mode…] (banner, wizard start page, limits tab) → C-10 (limits tab)."""
        self.limits_tab.enter_no_specimen()

    def set_opengl(self, on: bool) -> None:
        """View ▸ OpenGL rendering (NFR-009): applied to every plot pane at once and stored in ``gui.ini``; a GL
        failure falls back to raster with a message (``plots/render.py``)."""
        render.set_opengl(on)
        modes = [m for d in self.plot_docks for m in d.apply_render()]
        if self.settings is not None:
            self.settings.setValue(KEY_OPENGL, bool(on))
        if on and (render.STATE["failed"] or "opengl" not in modes):
            self.toast(f"OpenGL rendering not available — software raster used ({render.STATE['reason'] or 'no GL'})",
                       "warn")
            self.opengl_action.blockSignals(True)
            self.opengl_action.setChecked(False)
            self.opengl_action.blockSignals(False)
        else:
            self.toast("Plot rendering: " + ("OpenGL" if on else "software raster"), "info")

    def set_force_unit(self, unit: str) -> None:
        """View ▸ Units N / kgf (SYS-003): display only; stored in the session and the GUI settings."""
        force_unit().set(unit)
        act = self.unit_actions.get(unit)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        self.readout_dock.set_force_unit(unit)
        for d in self.plot_docks:
            d.update_xy_choices()
        try:
            s = self.backend.session.get()
            if getattr(s, "display_unit", unit) != unit:
                self.backend.session.set(dataclasses.replace(s, display_unit=unit))
        except Exception:  # noqa: BLE001 - persistence is a convenience
            log.debug("session display unit not stored", exc_info=True)
        if self.settings is not None:
            self.settings.setValue(KEY_UNITS, unit)

    def _restore_ui_prefs(self) -> None:
        unit = self.settings.value(KEY_UNITS) if self.settings is not None else None
        if unit not in FORCE_UNITS:
            try:
                unit = self.backend.session.get().display_unit
            except Exception:  # noqa: BLE001
                unit = "N"
        self.set_force_unit(str(unit) if unit in FORCE_UNITS else "N")
        if self.settings is not None:
            try:
                i = int(self.settings.value(KEY_LAST_TAB, 0))
            except (TypeError, ValueError):
                i = 0
            if 0 <= i < self.tabs.count():
                self.tabs.setCurrentIndex(i)

    # ================================================================== toast
    def toast(self, text: str, severity: str = "info") -> None:
        style = {"error": "alarm", "warn": "warn"}.get(severity, "info")
        self.toast_label.setStyleSheet(f"QLabel#toast {{{BANNER_STYLE[style]} padding: 2px;}}")
        self.toast_label.setText(text)
        self.toast_label.show()
        self._update_banner_bar()
        self._toast_timer.start(TOAST_MS)
        self.event_log.add_text("GUI", severity.upper(), text, severity)

    # ================================================================== perf / close
    def perf_stats(self) -> dict[str, Any]:
        return self.refresh.perf_stats()

    def _busy_reason(self) -> str:
        st = self.last_status
        if st is None:
            return ""
        if st.motion.moving:
            return "moving"
        if st.operation.owner not in ("MANUAL", None):
            return f"operation {st.operation.owner}"
        if st.recording.state == "RECORDING":
            return "recording"
        return ""

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._force_close and self._busy_reason():
            event.ignore()
            dlg = make_confirm(self, "C-09")
            dlg.confirmed.connect(self._confirmed_close)
            self.confirm_dialog = dlg
            dlg.open()
            return
        if not self._closing:
            self._closing = True
            self.refresh.stop()
            self.exit_summary = self._summary()
            self.bridge.shutdown()
            try:
                self.backend.shutdown()
            except Exception:  # noqa: BLE001
                log.exception("backend.shutdown failed")
            set_stop_handler(self._prev_stop_handler)
            if self.settings is not None:
                try:
                    self.settings.setValue("main/geometry", self.saveGeometry())
                    self.settings.setValue(KEY_LAST_TAB, self.tabs.currentIndex())
                except Exception:  # noqa: BLE001
                    pass
                self.save_layout()
            for d in self.plot_docks:
                d.dispose()
            self.closed.emit()
        super().closeEvent(event)

    def _summary(self) -> str:
        """One line for the exit log (smoke runs): link, stream, frames, refresh statistics."""
        try:
            st = self.backend.status()
            s = st.link.stats
            ps = self.perf_stats()
            return (f"link {getattr(st.link.state, 'value', st.link.state)} {st.link.endpoint or ''}; stream "
                    f"{'on' if st.stream.on else 'off'} rate {st.stream.rate_sps}; frames ok {s.frames_ok} data "
                    f"{s.data_frames} lost fw {s.frames_lost_fw} link {s.frames_lost_link} crc {s.crc_errors}; "
                    f"ticks {ps['ticks']} interval p50 {ps['interval_p50_ms']:.1f} ms p95 "
                    f"{ps['interval_p95_ms']:.1f} ms; plot updates {self.plot_dock.updates}; snapshots {self.snapshot_calls}")
        except Exception as exc:  # noqa: BLE001
            return f"summary unavailable: {exc}"

    def _confirmed_close(self) -> None:
        self._force_close = True
        self.close()


def _alive(w: Any) -> bool:
    try:
        w.isVisible()
        return True
    except RuntimeError:
        return False


def _reusable(w: Any) -> bool:
    """An open (visible) window can be raised again; a closed one may already be scheduled for deletion
    (WA_DeleteOnClose), so a new one is created."""
    try:
        return bool(w.isVisible())
    except RuntimeError:
        return False
