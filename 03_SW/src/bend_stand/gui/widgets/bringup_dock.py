"""Bring-up assistant (Tools ▸ Bring-up assistant…; D-56 a, SRS SW-BRU-001…004; SW_design_GUI §3.8).

A dockable :class:`SafeDock` (title bar with Float / Close / **STOP**, NO-SPECIMEN tag) that walks the steps of the
delivered bring-up guide through the backend's ``BringupAPI`` (``backend.bringup``):

* **sidebar** — sections with progress (done / total, failures) and the steps with their state (○ not done, ✓ pass,
  ✗ fail, – skipped); a click selects a step (``bringup.select``);
* **step view** — big texts for a technician: step id + title, *Instruction*, *Expected*, a safety note, the
  automatic result (live: waiting / pass / fail, from the engine), the operator entries generated from the step's
  fields (confirmation checkbox, number with unit and limits, text, choice) + remark, [Save entries] (``submit``),
  [Pass ✓] / [Fail ✗] / [Skip…] (``mark``; Skip asks for a reason), [◀ Previous] / [Next ▶], a large **STOP**;
* **action button** — for steps that need a GUI action. It calls **the same GUI handler as the normal control**
  (Manual tab HOME / Driver enabled, no-specimen mode, wizards, TARE, Save to NVM, …) and is enabled from the same
  backend gate, so every refusal and confirmation (C-01, C-02, C-10, C-12 …) is the normal one (SW-BRU-004). Jogs,
  go-to and steps are not duplicated: the step shows the Manual tab (which has its own STOP). Nothing advances by
  itself — the operator clicks [Next ▶];
* header — session line (board UID, FW build, started, progress), [Start bring-up session…] (CONFIRM when one exists)
  and **[Create bring-up package…]** → ``create_package_async`` → :class:`BringupPackageDialog` (path, size, files,
  "send this file to …", [Open folder]).

The engine owns steps, automatic evaluation, collection, persistence (resume after restart) and the package; the GUI
renders and forwards (P1). Fields are read with ``getattr`` defaults, so the GUI follows B's dataclasses.

Implements: SW-BRU-001 (assistant, step view, entries, automatic results shown), SW-BRU-003 (package button, open
folder), SW-BRU-004 (gated actions through the normal handlers, STOP in every view, no auto-advance)
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bend_stand.core.api import GateId
from bend_stand.gui import gating
from bend_stand.gui.dialogs.confirm_dialog import ConfirmDialog
from bend_stand.gui.dialogs.safe_dialog import SafeDialog
from bend_stand.gui.widgets.safe_dock import SafeDock
from bend_stand.gui.widgets.stop_button import StopButton

log = logging.getLogger(__name__)

STATE_MARK = {"NOT_DONE": "○", "PASS": "✓", "FAIL": "✗", "SKIPPED": "–"}
STATE_TEXT = {"NOT_DONE": "not done", "PASS": "PASS", "FAIL": "FAIL", "SKIPPED": "skipped"}
STATE_STYLE = {"NOT_DONE": "color: #404040;", "PASS": "color: #1a7f1a; font-weight: bold;",
               "FAIL": "color: #b00000; font-weight: bold;", "SKIPPED": "color: #806000;"}
AUTO_STYLE = {"WAITING": "background: #eeeeee; color: #303030;", "PASS": "background: #d8f3d8; color: #105010;",
              "FAIL": "background: #ffd6d6; color: #800000;", "NA": "background: #eeeeee; color: #606060;"}

def _manual(mw: Any, name: str) -> Any:
    """Run a Manual-tab handler with the tab's state refreshed first: its confirmations (C-01 / C-02) are decided
    from the tab's status, which is only refreshed while the tab is shown."""
    try:
        mw.manual_tab.update_status(mw.backend.status())
    except Exception:  # noqa: BLE001
        log.debug("manual tab refresh before an assistant action failed", exc_info=True)
    return getattr(mw.manual_tab, name)()


#: SW-BRU-004: step action id → (button label, gate of the normal control or None, motion?, handler on the main
#: window). The handlers are the normal GUI paths — gates, confirmations and refusals are theirs.
ACTIONS: Mapping[str, tuple[str, GateId | None, bool, Callable[[Any], Any]]] = {
    "enable": ("Driver enabled (Manual tab)", GateId.ENABLE, False, lambda mw: _manual(mw, "on_enable")),
    "disable": ("Untick Driver enabled…", GateId.DISABLE, False, lambda mw: _manual(mw, "on_disable")),
    "home": ("HOME", GateId.HOME, True, lambda mw: _manual(mw, "on_home")),
    "no_specimen": ("Enter no-specimen mode…", GateId.NO_SPECIMEN, False, lambda mw: mw.enter_no_specimen()),
    "leave_no_specimen": ("Leave no-specimen mode", None, False, lambda mw: mw._leave_no_specimen()),  # noqa: SLF001
    "travel_wizard": ("Start travel calibration wizard…", GateId.CAL_TRAVEL_START, True,
                      lambda mw: mw.open_travel_wizard()),
    "load_wizard": ("Start load calibration wizard…", GateId.CAL_LOAD_START, False, lambda mw: mw.open_load_wizard()),
    "tare": ("TARE", GateId.TARE, False, lambda mw: mw.on_tare(None)),
    "save_nvm": ("Save to NVM", GateId.CONFIG_WRITE, False, lambda mw: mw.connection_tab.on_save_nvm()),
    "save_file": ("Save to file…", None, False, lambda mw: mw.connection_tab.on_save_file()),
    "read_all": ("Read all", None, False, lambda mw: mw.connection_tab.on_read_all()),
    "clear_stop": ("Clear stop…", None, False, lambda mw: mw.open_clear_stop()),
    "hotkey_test": ("Test Pause/Break key…", GateId.HOTKEY_TEST, False, lambda mw: mw.open_hotkey_test()),
    # B-BRU-02: the only engine-run action (B §25.4) — backend.bringup.run_action(step, confirmed=…) behind the usual
    # confirmation; the engine checks the motion gates on every move. Enabled from the MOVE gate.
    "pulse_integrity": ("Run pulse-integrity check", GateId.MOVE, True,
                        lambda mw: mw.bringup_dock.run_engine_action()),
}
#: "show:<tab>" actions: bring a tab to the front (jogs / go-to / steps are done there, with its own STOP)
SHOW_TABS: Mapping[str, tuple[str, int]] = {
    "connection": ("Show Connection & Config tab", 0), "limits": ("Show Safety limits tab", 1),
    "marks": ("Show Test marks tab", 2), "manual": ("Show Manual tab", 3), "calibration": ("Show Calibration & Tare tab", 4),
}


def g(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default) if obj is not None else default


def state_of(step: Any) -> str:
    return str(g(step, "state", "NOT_DONE") or "NOT_DONE")


def is_engine_action(step: Any) -> bool:
    """The step's action is run by the engine (``run_action``), not by a GUI handler (B: ``engine_action``)."""
    return bool(g(step, "engine_action", False)) or g(step, "action", None) == "pulse_integrity"


def action_spec(action: str | None) -> tuple[str, GateId | None, bool, Callable[[Any], Any]] | None:
    """Pure: the GUI action of a step (None = none / unknown)."""
    if not action:
        return None
    if action.startswith("show:"):
        tab = SHOW_TABS.get(action.split(":", 1)[1])
        if tab is None:
            return None
        return tab[0], None, False, (lambda mw, i=tab[1]: mw.tabs.setCurrentIndex(i))
    return ACTIONS.get(action)


STOP_PHRASE = "STOP the bring-up and report"
#: entry issues shown with their code (refusals of the whole step rather than a field problem)
CODED = frozenset({"BOARD_CHANGED", "NO_SESSION", "OPERATION_RUNNING", "UNKNOWN_STEP"})
ORIGIN_TEXT = {"auto": "set by the automatic check", "operator": "set by the operator",
               "auto+operator": "automatic check + operator", "cli-test": "set from the command line (cli-test, "
               "simulator smoke test) — NOT operator evidence"}


def stop_text(step: Any) -> str:
    """B-BRU-08: the step's text that orders a stop ("… STOP the bring-up and report"), else ""."""
    for t in (g(step, "action_text", ""), g(g(step, "auto", None), "text", "")):
        if t and STOP_PHRASE.lower() in str(t).lower():
            return str(t)
    return ""


def required_when_text(f: Any, labels: Mapping[str, str]) -> str:
    """B-BRU-08: display of ``required_when`` ({field, op, value}) — the engine decides; the GUI only says when."""
    rw = g(f, "required_when", None)
    if not rw:
        return ""
    get = rw.get if isinstance(rw, Mapping) else (lambda k, d=None: getattr(rw, k, d))
    fld = str(get("field", "") or "")
    op = {"eq": "is", "ne": "is not", "in": "is one of", "not_in": "is none of", "gt": ">", "ge": "≥", "lt": "<",
          "le": "≤", "set": "is set", "clear": "is not set", "on": "is ticked", "off": "is not ticked"}.get(
        str(get("op", "") or ""), str(get("op", "") or ""))
    val = get("value", None)
    vtxt = "" if val is None else (" " + ", ".join(map(str, val)) if isinstance(val, (list, tuple)) else f" {val}")
    return f"required only when “{labels.get(fld, fld)}” {op}{vtxt}"


def progress_text(st: Any) -> str:
    """Pure: header progress line."""
    if not g(st, "active", False):
        return "No bring-up session — press [Start bring-up session…]"
    parts = [f"{g(st, 'done', 0)} / {g(st, 'total', 0)} steps done", f"{g(st, 'passed', 0)} pass"]
    if g(st, "failed", 0):
        parts.append(f"{g(st, 'failed', 0)} FAIL")
    if g(st, "skipped", 0):
        parts.append(f"{g(st, 'skipped', 0)} skipped")
    who = " · ".join(p for p in (f"board {g(st, 'board_uid')}" if g(st, "board_uid") else "",
                                 f"FW build {g(st, 'fw_build')}" if g(st, "fw_build") else "",
                                 f"started {g(st, 'started_utc')}" if g(st, "started_utc") else "") if p)
    return " · ".join(parts) + (f"   ({who})" if who else "")


def _big(label: QLabel, pt_add: int = 2, bold: bool = False) -> QLabel:
    f = QFont(label.font())
    f.setPointSizeF(max(9.0, f.pointSizeF()) + pt_add)
    f.setBold(bold)
    label.setFont(f)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class BringupDock(SafeDock):
    """The assistant window (dock / float). ``main`` = the MainWindow (actions, tabs); ``api`` = ``backend.bringup``."""

    message = Signal(str, str)

    def __init__(self, backend: Any, main: Any, parent: QWidget | None = None) -> None:
        super().__init__("Bring-up assistant", parent, source="dock:bringup")
        self._backend = backend
        self._main = main
        self.current: str | None = None
        self._step: Any = None
        self._editors: dict[str, QWidget] = {}
        self._last_sig: Any = None
        self.confirm_dialog: Any = None
        self.package_dialog: Any = None
        self.package_future: Any = None
        body = QWidget(self)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(4, 4, 4, 4)
        # header
        head = QHBoxLayout()
        self.progress_label = _big(QLabel("", body), 1, True)
        self.progress_label.setObjectName("bringupProgress")
        head.addWidget(self.progress_label, 1)
        self.start_button = self._btn(body, "Start bring-up session…", "bringupStart", self.on_start)
        self.package_button = self._btn(body, "Create bring-up package…", "bringupPackage", self.on_package)
        head.addWidget(self.start_button)
        head.addWidget(self.package_button)
        outer.addLayout(head)
        # SWD-P3-04: another board is connected to the open session — results are refused until the operator decides
        self.board_banner = QFrame(body)
        self.board_banner.setObjectName("bringupBoardBanner")
        self.board_banner.setStyleSheet("QFrame#bringupBoardBanner { background: #b00000; }")
        bl = QHBoxLayout(self.board_banner)
        bl.setContentsMargins(6, 4, 6, 4)
        self.board_label = _big(QLabel("", self.board_banner), 3, True)
        self.board_label.setObjectName("bringupBoardText")
        self.board_label.setStyleSheet("color: white;")
        bl.addWidget(self.board_label, 1)
        self.board_new_button = self._btn(self.board_banner, "New session for this board", "bringupBoardNew",
                                          self.on_start)
        self.board_package_button = self._btn(self.board_banner, "Create package", "bringupBoardPackage",
                                              self.on_package)
        bl.addWidget(self.board_new_button)
        bl.addWidget(self.board_package_button)
        self.board_banner.hide()
        outer.addWidget(self.board_banner)
        split = QSplitter(Qt.Orientation.Horizontal, body)
        # sidebar
        self.tree = QTreeWidget(split)
        self.tree.setObjectName("bringupSteps")
        self.tree.setHeaderLabels(["Step", "State"])
        self.tree.setColumnWidth(0, 260)
        self.tree.itemClicked.connect(self._on_tree_click)
        split.addWidget(self.tree)
        # step view
        scroll = QScrollArea(split)
        scroll.setWidgetResizable(True)
        view = QWidget(scroll)
        self.view = view
        vl = QVBoxLayout(view)
        self.title_label = _big(QLabel("", view), 5, True)
        self.title_label.setObjectName("bringupTitle")
        self.stop_report_label = _big(QLabel("", view), 4, True)            # B-BRU-08: the step ordered a stop
        self.stop_report_label.setObjectName("bringupStopReport")
        self.stop_report_label.setStyleSheet("background: #b00000; color: white; padding: 6px;")
        self.stop_report_label.hide()
        self.stop_if_fail_label = QLabel("If this step fails → STOP the bring-up, make the stand safe, report.", view)
        self.stop_if_fail_label.setStyleSheet("background: #ffd6d6; color: #800000; font-weight: bold; padding: 3px;")
        self.stop_if_fail_label.setWordWrap(True)
        self.instruction_label = _big(QLabel("", view), 3)
        self.instruction_label.setObjectName("bringupInstruction")
        self.expected_label = _big(QLabel("", view), 3)
        self.expected_label.setObjectName("bringupExpected")
        self.safety_label = _big(QLabel("", view), 2)
        self.safety_label.setStyleSheet("background: #fff0c0; color: #604000; padding: 4px;")
        self.auto_label = _big(QLabel("", view), 3, True)
        self.auto_label.setObjectName("bringupAuto")
        self.hg_label = QLabel("", view)
        self.hg_label.setStyleSheet("color: #606060;")
        for w in (self.title_label, self.stop_report_label, self.stop_if_fail_label, self.instruction_label,
                  self.expected_label,
                  self.safety_label, self.auto_label, self.hg_label):
            vl.addWidget(w)
        # action
        arow = QHBoxLayout()
        self.action_button = self._btn(view, "", "bringupAction", self.on_action)
        self.action_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)          # may start motion: mouse only (P3)
        self.action_note = QLabel("", view)
        self.action_note.setWordWrap(True)
        self.action_note.setStyleSheet("color: #604000;")
        arow.addWidget(self.action_button)
        self.cancel_action_button = self._btn(view, "Cancel check", "bringupCancelAction", self.on_cancel_action)
        self.cancel_action_button.setToolTip("Stops the running check (STOP) — run it again from the baseline")
        arow.addWidget(self.cancel_action_button)
        arow.addWidget(self.action_note, 1)
        vl.addLayout(arow)
        self.action_state_label = _big(QLabel("", view), 2, True)
        self.action_state_label.setObjectName("bringupActionState")
        vl.addWidget(self.action_state_label)
        # entries
        self.entries_box = QFrame(view)
        self.entries_box.setFrameShape(QFrame.Shape.StyledPanel)
        self.form = QFormLayout(self.entries_box)
        vl.addWidget(self.entries_box)
        self.remark_edit = QLineEdit(view)
        self.remark_edit.setObjectName("bringupRemark")
        self.remark_edit.setPlaceholderText("remark (optional)")
        vl.addWidget(self.remark_edit)
        self.issue_label = QLabel("", view)
        self.issue_label.setStyleSheet("color: #b00000;")
        self.issue_label.setWordWrap(True)
        vl.addWidget(self.issue_label)
        # state buttons + navigation
        brow = QHBoxLayout()
        self.state_label = _big(QLabel("", view), 3, True)
        self.state_label.setObjectName("bringupState")
        brow.addWidget(self.state_label)
        self.origin_label = QLabel("", view)                                 # B-BRU-08: who set the state
        self.origin_label.setObjectName("bringupOrigin")
        self.origin_label.setWordWrap(True)
        brow.addWidget(self.origin_label)
        brow.addStretch(1)
        self.save_button = self._btn(view, "Save entries", "bringupSave", self.on_save)
        self.pass_button = self._btn(view, "Pass ✓", "bringupPass", lambda: self.on_mark("PASS"))
        self.fail_button = self._btn(view, "Fail ✗", "bringupFail", lambda: self.on_mark("FAIL"))
        self.skip_button = self._btn(view, "Skip…", "bringupSkip", self.on_skip)
        for b in (self.save_button, self.pass_button, self.fail_button, self.skip_button):
            brow.addWidget(b)
        vl.addLayout(brow)
        nrow = QHBoxLayout()
        self.prev_button = self._btn(view, "◀ Previous", "bringupPrev", lambda: self.go(-1))
        self.next_button = self._btn(view, "Next ▶", "bringupNext", lambda: self.go(+1))
        self.stop_big = StopButton(view, large=True, source="bringup")
        nrow.addWidget(self.prev_button)
        nrow.addWidget(self.next_button)
        nrow.addStretch(1)
        nrow.addWidget(self.stop_big)
        vl.addLayout(nrow)
        vl.addStretch(1)
        self.empty_label = _big(QLabel("No bring-up session. Press [Start bring-up session…] to begin; a running "
                                       "session resumes by itself after a restart.", view), 3)
        vl.insertWidget(0, self.empty_label)
        scroll.setWidget(view)
        split.addWidget(scroll)
        split.setStretchFactor(1, 3)
        split.setSizes([340, 760])
        outer.addWidget(split, 1)
        self.setWidget(body)
        self.refresh(force=True)

    # ---------------------------------------------------------------- helpers
    def _btn(self, parent: QWidget, text: str, name: str, slot: Callable[[], Any]) -> QPushButton:
        b = QPushButton(text, parent)
        b.setObjectName(name)
        b.setAutoDefault(False)
        b.setDefault(False)
        b.clicked.connect(lambda _c=False: slot())
        return b

    @property
    def api(self) -> Any:
        return getattr(self._backend, "bringup", None)

    def _status(self) -> Any:
        try:
            return self.api.status() if self.api is not None else None
        except Exception:  # noqa: BLE001 - engine not ready → treated as no session
            log.debug("bringup.status failed", exc_info=True)
            return None

    def step_ids(self) -> list[str]:
        try:
            return [str(g(s, "id")) for s in self.api.steps()]
        except Exception:  # noqa: BLE001
            return []

    # ---------------------------------------------------------------- refresh (main refresh tick while visible)
    def refresh(self, status: Any = None, *, force: bool = False) -> None:
        st = self._status()
        active = bool(g(st, "active", False))
        txt = progress_text(st) if self.api is not None else "Bring-up assistant not available in this version"
        stop_steps = []
        if active:
            try:
                stop_steps = [str(g(s_, "id")) for s_ in self.api.steps() if stop_text(s_)]
            except Exception:  # noqa: BLE001
                stop_steps = []
        if stop_steps:                                                     # B-BRU-08: prominent in the header too
            txt = f"■ {STOP_PHRASE} (step {', '.join(stop_steps)}) — " + txt
        if self.progress_label.text() != txt:
            self.progress_label.setText(txt)
            self.progress_label.setStyleSheet("background: #b00000; color: white; padding: 4px;" if stop_steps
                                              else "")
        self.set_info(f"{g(st, 'done', 0)}/{g(st, 'total', 0)}" if active else "")
        mm = str(g(st, "board_mismatch", "") or "") if active else ""
        if self.board_label.text() != mm:
            self.board_label.setText(mm)
        self.board_banner.setVisible(bool(mm))
        self.board_package_button.setEnabled(self.package_future is None)
        self.start_button.setEnabled(self.api is not None)
        self.package_button.setEnabled(active and self.package_future is None)
        if not active:
            self._show_empty(True)
            return
        self._show_empty(False)
        try:
            steps = list(self.api.steps())
            sections = list(self.api.sections())
        except Exception:  # noqa: BLE001
            return
        sig = tuple((str(g(s, "id")), state_of(s), str(g(s, "origin", "") or ""), bool(stop_text(s))) for s in steps)
        if force or sig != self._last_sig:
            self._last_sig = sig
            self._fill_tree(sections, {str(g(s, "id")): s for s in steps})
        if self.current is None or self.current not in {str(g(s, "id")) for s in steps}:
            self.current = str(g(st, "current", None) or (g(steps[0], "id") if steps else "")) or None
            force = True
        if self.current is not None:
            self._render_step(self.api.step(self.current), force=force, status=status)

    def _show_empty(self, empty: bool) -> None:
        self.empty_label.setVisible(empty)
        for w in (self.title_label, self.instruction_label, self.expected_label, self.auto_label,
                  self.entries_box, self.remark_edit, self.state_label, self.save_button, self.pass_button,
                  self.fail_button, self.skip_button, self.prev_button, self.next_button, self.action_button,
                  self.action_note, self.hg_label, self.safety_label, self.stop_if_fail_label, self.issue_label,
                  self.cancel_action_button, self.action_state_label, self.stop_report_label, self.origin_label):
            if empty:
                w.setVisible(False)
        self.tree.setVisible(not empty)

    def _fill_tree(self, sections: list[Any], by_id: Mapping[str, Any]) -> None:
        self.tree.clear()
        for sec in sections:
            ids = [i for i in (g(sec, "step_ids", ()) or ()) if i in by_id]
            done = sum(1 for i in ids if state_of(by_id[i]) != "NOT_DONE")
            failed = sum(1 for i in ids if state_of(by_id[i]) == "FAIL")
            top = QTreeWidgetItem([str(g(sec, "title", g(sec, "id", ""))),
                                   f"{done}/{len(ids)}" + (f"  ✗{failed}" if failed else "")])
            top.setData(0, Qt.ItemDataRole.UserRole, None)
            self.tree.addTopLevelItem(top)
            for i in ids:
                s = by_id[i]
                mark = STATE_MARK.get(state_of(s), "?")                     # mark first: visible at any width
                it = QTreeWidgetItem([f"{mark} {i}  {g(s, 'title', '')}",
                                      f"{mark} {STATE_TEXT.get(state_of(s), state_of(s))}"])
                it.setData(0, Qt.ItemDataRole.UserRole, i)
                if str(g(s, "origin", "") or "") == "cli-test" or stop_text(s):
                    for c in (0, 1):
                        it.setForeground(c, QColor("#b00000"))
                    if str(g(s, "origin", "") or "") == "cli-test":
                        it.setToolTip(0, ORIGIN_TEXT["cli-test"])
                top.addChild(it)
            top.setExpanded(True)

    def _on_tree_click(self, item: QTreeWidgetItem, _col: int) -> None:
        sid = item.data(0, Qt.ItemDataRole.UserRole)
        if sid:
            self.select(str(sid))

    def select(self, step_id: str) -> None:
        self.current = step_id
        try:
            self.api.select(step_id)
        except Exception:  # noqa: BLE001
            log.debug("bringup.select failed", exc_info=True)
        self.issue_label.setText("")
        self._render_step(self.api.step(step_id), force=True)

    def go(self, delta: int) -> None:
        """[◀ Previous] / [Next ▶] — the only way to change the step (no auto-advance, SW-BRU-004)."""
        ids = self.step_ids()
        if not ids:
            return
        i = ids.index(self.current) if self.current in ids else 0
        self.select(ids[max(0, min(len(ids) - 1, i + delta))])

    # ---------------------------------------------------------------- step view
    def _render_step(self, step: Any, *, force: bool = False, status: Any = None) -> None:
        if force or step is not self._step:
            new_step = force or g(self._step, "id") != g(step, "id") or g(self._step, "fields") != g(step, "fields")
            self._step = step
            sid = str(g(step, "id", ""))
            self.title_label.setText(f"{sid}  {g(step, 'title', '')}")
            self.instruction_label.setText("Instruction: " + str(g(step, "instruction", "") or ""))
            self.expected_label.setText("Expected: " + str(g(step, "expected", "") or ""))
            safety = str(g(step, "safety", "") or "")
            self.safety_label.setText("⚠ " + safety if safety else "")
            self.safety_label.setVisible(bool(safety))
            self.stop_if_fail_label.setVisible(bool(g(step, "stop_if_fail", False)))
            hg = str(g(step, "hg", "") or "")
            self.hg_label.setText(f"Same check as the hardware gate item {hg} (not gate evidence)" if hg else "")
            self.hg_label.setVisible(bool(hg))
            for w in (self.title_label, self.instruction_label, self.expected_label, self.state_label,
                      self.save_button, self.pass_button, self.fail_button, self.skip_button, self.prev_button,
                      self.next_button, self.remark_edit, self.issue_label):
                w.setVisible(True)
            if new_step:
                self._build_editors(step)
                self.remark_edit.setText(str(g(step, "remark", "") or ""))
        stxt = stop_text(step)
        banner = f"■ {stxt}" if stxt else ""
        if self.stop_report_label.text() != banner:
            self.stop_report_label.setText(banner)
        self.stop_report_label.setVisible(bool(banner))
        origin = str(g(step, "origin", "") or "")
        otxt = ORIGIN_TEXT.get(origin, origin) if origin else ""
        if self.origin_label.text() != otxt:
            self.origin_label.setText(otxt)
            self.origin_label.setStyleSheet("color: #b00000; font-weight: bold;" if origin == "cli-test"
                                            else "color: #606060;")
        self.origin_label.setVisible(bool(otxt))
        st = state_of(step)
        txt = f"State: {STATE_TEXT.get(st, st)}" + (f" — {g(step, 'skip_reason')}" if st == "SKIPPED"
                                                     and g(step, "skip_reason") else "")
        if self.state_label.text() != txt:
            self.state_label.setText(txt)
            self.state_label.setStyleSheet(STATE_STYLE.get(st, ""))
        auto = g(step, "auto", None)
        if auto is None:
            self.auto_label.setVisible(False)
        else:
            a_state = str(g(auto, "state", "WAITING") or "WAITING")
            a_txt = {"WAITING": "Automatic check: waiting … ", "PASS": "Automatic check: PASS — ",
                     "FAIL": "Automatic check: FAIL — ", "NA": "Automatic check: not available — "}.get(a_state, "")
            a_txt += str(g(auto, "text", "") or "")
            if self.auto_label.text() != a_txt:
                self.auto_label.setText(a_txt)
                self.auto_label.setStyleSheet(AUTO_STYLE.get(a_state, "") + " padding: 4px;")
            self.auto_label.setVisible(True)
        self._render_action(step, status)
        ids = self.step_ids()
        i = ids.index(self.current) if self.current in ids else 0
        self.prev_button.setEnabled(i > 0)
        self.next_button.setEnabled(i < len(ids) - 1)
        has_fields = bool(g(step, "fields", ()))
        self.entries_box.setVisible(has_fields)
        self.save_button.setVisible(has_fields)

    def _render_action(self, step: Any, status: Any = None) -> None:
        engine = is_engine_action(step)
        a_state = str(g(step, "action_state", "") or "")
        running = a_state == "RUNNING" or (engine and bool(g(self._status(), "action_running", False)))
        self.cancel_action_button.setVisible(engine and running)
        a_txt = ""
        if engine and (a_state or g(step, "action_text", "")):
            a_txt = f"Check: {a_state or '—'}" + (f" — {g(step, 'action_text')}" if g(step, "action_text", "") else "")
        if self.action_state_label.text() != a_txt:
            self.action_state_label.setText(a_txt)
            self.action_state_label.setStyleSheet(AUTO_STYLE.get({"RUNNING": "WAITING", "ABORTED": "FAIL"}.get(
                a_state, a_state), "") + " padding: 4px;")
        self.action_state_label.setVisible(bool(a_txt))
        spec = action_spec("pulse_integrity" if engine else g(step, "action", None))
        if spec is None:
            self.action_button.setVisible(False)
            self.action_note.setVisible(False)
            return
        label, gid, motion, _h = spec
        self.action_button.setVisible(True)
        self.action_note.setVisible(True)
        self.action_button.setText(label + (" ▶" if motion and not label.endswith("…") else ""))
        st = status if status is not None else self._backend_status()
        tip = "Same action, gate and confirmations as the normal control"
        enabled = True
        if gid is not None and st is not None:
            cs = gating.control_state(gating.gate_of(st, gid), motion=motion)
            enabled, tip = cs.enabled, cs.tooltip or tip
        if engine and running:
            enabled, tip = False, "the check is running — [Cancel check] stops it"
        if self.action_button.isEnabled() != enabled:
            self.action_button.setEnabled(enabled)
        self.action_button.setToolTip(tip)
        note = ("Moves the axis: watch the stand; the normal confirmation appears; STOP / Pause/Break / red E-stop "
                "stop it." if bool(g(step, "motion", False)) or motion else
                "Uses the normal control (same gate and confirmation).")
        if engine:
            note = ("Moves the axis over the stroke at travel speed (round trips and re-homes); the normal "
                    "confirmation appears first; keep clear; STOP / Pause/Break / red E-stop stop it.")
        if not enabled and gid is not None and not running:
            note = "Not possible now: " + (tip or "refused")
        if self.action_note.text() != note:
            self.action_note.setText(note)

    def _backend_status(self) -> Any:
        try:
            return self._backend.status()
        except Exception:  # noqa: BLE001
            return None

    def _build_editors(self, step: Any) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._editors = {}
        entries = dict(g(step, "entries", {}) or {})
        labels = {str(g(f, "name")): str(g(f, "label", g(f, "name"))) for f in g(step, "fields", ()) or ()}
        for f in g(step, "fields", ()) or ():
            name, kind = str(g(f, "name")), str(g(f, "kind", "number"))
            label = str(g(f, "label", name))
            unit = str(g(f, "unit", "") or "")
            if kind == "confirm":
                w: QWidget = QCheckBox(label, self.entries_box)
                w.setChecked(bool(entries.get(name, False)))
                self.form.addRow(w)
            elif kind == "number":
                w = QDoubleSpinBox(self.entries_box)
                dec = int(g(f, "decimals", 2) or 2)
                w.setDecimals(dec)
                w.setRange(-1e9, 1e9)
                w.setKeyboardTracking(False)
                if unit:
                    w.setSuffix(f" {unit}")
                if entries.get(name) is not None:
                    w.setValue(float(entries[name]))
                lo, hi = g(f, "min"), g(f, "max")
                lim = f"limits {lo if lo is not None else '−∞'} … {hi if hi is not None else '∞'} {unit}".strip()
                row = QWidget(self.entries_box)
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.addWidget(w)
                rl.addWidget(QLabel(lim, row))
                self.form.addRow(label, row)
            elif kind == "choice":
                w = QComboBox(self.entries_box)
                w.addItem("—", None)
                for c in g(f, "choices", ()) or ():
                    w.addItem(str(c), c)
                if entries.get(name) is not None:
                    w.setCurrentIndex(max(0, w.findData(entries[name])))
                self.form.addRow(label, w)
            else:
                w = QLineEdit(self.entries_box)
                w.setText(str(entries.get(name, "") or ""))
                self.form.addRow(label, w)
            w.setObjectName(f"bu_{name}")
            self._editors[name] = w
            rw = required_when_text(f, labels)
            if rw:
                note = QLabel(rw, self.entries_box)
                note.setObjectName(f"bu_{name}_required_when")
                note.setStyleSheet("color: #605000;")
                self.form.addRow("", note)

    def entry_values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, w in self._editors.items():
            if isinstance(w, QCheckBox):
                out[name] = w.isChecked()
            elif isinstance(w, QDoubleSpinBox):
                out[name] = float(w.value())
            elif isinstance(w, QComboBox):
                out[name] = w.currentData()
            elif isinstance(w, QLineEdit):
                out[name] = w.text()
        return out

    # ---------------------------------------------------------------- operator actions
    def on_start(self, confirmed: bool = False) -> None:
        try:
            gate = self.api.start(confirmed=confirmed)
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Bring-up session not started: {exc}", "error")
            return
        if not confirmed and getattr(gate, "confirm_items", ()) and not gate.refused:
            dlg = ConfirmDialog(self, cid="BU-1", title="Start a new bring-up session",
                                text="\n".join(i.text for i in gate.confirm_items), confirm_label="Start new session")
            dlg.confirmed.connect(lambda: self.on_start(confirmed=True))
            self.confirm_dialog = dlg
            dlg.open()
            return
        if not gate.ok:
            self.message.emit("Bring-up session not started: " + gating.refusal_text(gate), "warn")
            return
        self.current = None
        self.message.emit("Bring-up session started", "info")
        self.refresh(force=True)

    def on_action(self) -> None:
        """SW-BRU-004: the normal GUI handler of the action (its gate / confirmation decides)."""
        spec = action_spec(g(self._step, "action", None))
        if spec is None:
            return
        try:
            spec[3](self._main)
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"{spec[0]}: {getattr(exc, 'user_text', exc)}", "warn")

    def run_engine_action(self, confirmed: bool = False) -> Any:
        """B-BRU-02 / SW-BRU-004: ``run_action(step, confirmed)``; a CONFIRM (BRINGUP_MOTION + the HOME gate's items)
        opens the usual confirmation dialog (keyboard rules, STOP); a REFUSE is shown as is (nothing moves)."""
        sid = self.current
        try:
            gate = self.api.run_action(sid, confirmed=confirmed)
        except Exception as exc:  # noqa: BLE001
            self.issue_label.setText(f"not started: {getattr(exc, 'user_text', exc)}")
            return None
        refused = list(getattr(gate, "refused", ()) or ())
        if refused:
            text = "; ".join(f"[{i.code}] {i.text}" for i in refused)             # B-BRU-08: codes verbatim
            self.issue_label.setText("Not started: " + text)
            if any(i.code == "STOP_REPORTED" for i in refused):
                self.stop_report_label.setText(f"■ {STOP_PHRASE} — {refused[0].text}")
                self.stop_report_label.setVisible(True)
            self.message.emit(f"Step {sid}: not started — {text}", "warn")
            return gate
        if not confirmed and getattr(gate, "confirm_items", ()):
            dlg = ConfirmDialog(self, cid="BU-2", title=f"Step {sid}: the axis moves",
                                text="\n".join(i.text for i in gate.confirm_items), confirm_label="Run check",
                                assertion="Nothing in the travel, no specimen, people clear")
            dlg.confirmed.connect(lambda: self.run_engine_action(confirmed=True))
            self.confirm_dialog = dlg
            dlg.open()
            return gate
        self.issue_label.setText("")
        self.message.emit(f"Step {sid}: check started", "info")
        self.refresh()
        return gate

    def on_cancel_action(self) -> None:
        """[Cancel check] → ``cancel_action()`` (the engine sends STOP; the attempt is ABORTED, not counted)."""
        try:
            self.api.cancel_action()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Cancel: {getattr(exc, 'user_text', exc)}", "warn")
        self.refresh()

    def on_bringup_event(self, _record: Any) -> None:
        """Topics ``bringup.state`` / ``bringup.step`` (bridge): repaint promptly when the window is shown."""
        if self.isVisible():
            self.refresh()

    def on_save(self) -> list[Any]:
        try:
            issues = list(self.api.submit(self.current, self.entry_values(), self.remark_edit.text()))
        except Exception as exc:  # noqa: BLE001
            self.issue_label.setText(f"not saved: {exc}")
            return []
        errs = [i for i in issues if str(getattr(i, "severity", "ERROR")) == "ERROR"]
        self.issue_label.setText("; ".join((f"[{i.code}] " if getattr(i, "code", "") in CODED else "") + i.text
                                           for i in issues) if issues else "")
        if not errs:
            self.message.emit(f"Step {self.current}: entries saved", "info")
        self.refresh(force=False)
        self._render_step(self.api.step(self.current), force=True)
        return issues

    def on_mark(self, state: str, reason: str = "") -> bool:
        try:
            gate = self.api.mark(self.current, state, reason=reason, remark=self.remark_edit.text())
        except Exception as exc:  # noqa: BLE001
            self.issue_label.setText(f"not marked: {exc}")
            return False
        if not gate.ok:
            self.issue_label.setText("; ".join(f"[{i.code}] {i.text}" for i in getattr(gate, "refused", ()) or ())
                                     or gating.refusal_text(gate))
            return False
        self.issue_label.setText("")
        self.refresh()
        self._render_step(self.api.step(self.current), force=True)
        return True

    def on_skip(self, reason: str | None = None) -> bool:
        """[Skip…]: asks for the reason in a :class:`SkipDialog` (with STOP); ``reason`` given = skip directly."""
        if reason is None:
            dlg = SkipDialog(str(self.current), self)
            dlg.skipRequested.connect(lambda r: self.on_mark("SKIPPED", r))
            self.skip_dialog = dlg
            dlg.open()
            return False
        return self.on_mark("SKIPPED", reason)

    def on_package(self) -> None:
        """[Create bring-up package…] (SW-BRU-003): the engine writes the zip; the dialog shows where and to whom."""
        try:
            fut = self.api.create_package_async()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Bring-up package not created: {exc}", "error")
            return
        self.package_future = fut
        self.package_button.setEnabled(False)
        self.package_button.setText("Creating package …")
        bridge = getattr(self._main, "bridge", None)
        if bridge is not None and hasattr(bridge, "watch"):
            bridge.watch(fut, self._package_done, self._package_failed, "bringup-package")
        else:                                                   # pragma: no cover - tests without a bridge
            fut.add_done_callback(lambda f: self._package_done(f.result()) if f.exception() is None
                                  else self._package_failed(f.exception()))

    def _package_done(self, pkg: Any) -> None:
        self.package_future = None
        self.package_button.setText("Create bring-up package…")
        self.refresh()
        dlg = BringupPackageDialog(pkg, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.package_dialog = dlg
        dlg.show()
        self.message.emit(f"Bring-up package created: {g(pkg, 'path', '')}", "info")

    def _package_failed(self, exc: BaseException) -> None:
        self.package_future = None
        self.package_button.setText("Create bring-up package…")
        self.refresh()
        self.message.emit(f"Bring-up package not created: {getattr(exc, 'user_text', exc)}", "error")


class SkipDialog(SafeDialog):
    """Reason for skipping a step (STOP in the top bar like every assistant view; Enter does not skip)."""

    skipRequested = Signal(str)

    def __init__(self, step_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, title=f"Skip step {step_id}")
        lay = QVBoxLayout()
        lay.addWidget(QLabel(f"Why is step {step_id} skipped? (the reason goes into the bring-up record)", self))
        self.reason_edit = QLineEdit(self)
        self.reason_edit.setObjectName("bringupSkipReason")
        lay.addWidget(self.reason_edit)
        row = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel", self)
        self.skip_button = QPushButton("Skip step", self)
        for b in (self.cancel_button, self.skip_button):
            b.setAutoDefault(False)
            b.setDefault(False)
        self.skip_button.setEnabled(False)
        self.reason_edit.textChanged.connect(lambda t: self.skip_button.setEnabled(bool(t.strip())))
        self.cancel_button.clicked.connect(self.reject)
        self.skip_button.clicked.connect(self._skip)
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        row.addWidget(self.skip_button)
        lay.addLayout(row)
        self.setLayout(lay)

    def _skip(self) -> None:
        self.skipRequested.emit(self.reason_edit.text().strip())
        self.accept()


#: test seam: when set, folder URLs go here instead of QDesktopServices
OPEN_FOLDER_HOOK: list[Callable[[str], None] | None] = [None]


class BringupPackageDialog(SafeDialog):
    """Result of [Create bring-up package…]: where the zip is, its size, and to whom to send it (SW-BRU-003)."""

    def __init__(self, pkg: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="Bring-up package created")
        self.setModal(False)
        self.pkg = pkg
        lay = QVBoxLayout()
        path = str(g(pkg, "path", "") or "")
        size = int(g(pkg, "size_bytes", 0) or 0)
        self.path_label = _big(QLabel(path, self), 2, True)
        self.path_label.setObjectName("bringupPackagePath")
        lay.addWidget(QLabel("The bring-up package was written to:", self))
        lay.addWidget(self.path_label)
        lay.addWidget(QLabel(f"{size / 1e6:.1f} MB, {int(g(pkg, 'files', 0) or 0)} files", self))
        send = str(g(pkg, "send_to", "") or "Send this file to the product owner.")
        self.send_label = _big(QLabel(send, self), 2)
        self.send_label.setObjectName("bringupSendTo")
        lay.addWidget(self.send_label)
        row = QHBoxLayout()
        self.open_button = QPushButton("Open folder", self)
        self.open_button.setAutoDefault(False)
        self.open_button.clicked.connect(self.open_folder)
        self.close_button = QPushButton("Close", self)
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.close)
        row.addStretch(1)
        row.addWidget(self.open_button)
        row.addWidget(self.close_button)
        lay.addLayout(row)
        self.setLayout(lay)

    def open_folder(self) -> bool:
        folder = os.path.dirname(str(g(self.pkg, "path", "") or ""))
        hook = OPEN_FOLDER_HOOK[0]
        if hook is not None:
            hook(folder)
            return True
        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))


__all__ = ["STOP_PHRASE", "ORIGIN_TEXT", "stop_text", "required_when_text", "ACTIONS", "SHOW_TABS", "BringupDock", "BringupPackageDialog", "OPEN_FOLDER_HOOK", "action_spec",
           "progress_text"]
