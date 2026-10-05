"""``SafeWizard``: the common, non-modal wizard frame rendering a backend engine (SW_design_GUI §6.1, B §9.1).

Not a ``QWizard`` (its "Next" is a default button — Enter could start motion, P3). A :class:`SafeDialog` (STOP in
the top bar, NO-SPECIMEN tag) with:

* a **start page** (before ``engine.start``): the start gate (``cal_travel_start`` / ``cal_load_start``) as a
  checklist (REFUSE ✗ / CONFIRM / WARN, verbatim), the kind's start configuration and [Start] →
  ``engine.start(**config)``: REFUSE shown, CONFIRM → **C-12** → ``start(**config, confirmed=True)``; a
  LOAD_INPUT_INVALID item offers [Enter no-specimen mode…];
* the **step strip** from the engine's ``PHASES`` (terminal phases are page states);
* the page from ``engine.state()`` → ``EngineState``: title, instruction, generated inputs (``InputSpec``), live
  motion line, progress bar (capture / move), live stats, result table, fit plot, warnings / errors verbatim;
* buttons mapped to engine calls: [Continue] (``continue_label``; **mouse-only** ``NoFocus`` when
  ``continue_moves``) → ``continue_(inputs)``; ``needs_confirmation`` → C-05 / C-06 → ``continue_(inputs,
  confirmed=True)``; [Repeat] → ``repeat()``; [Cancel] → ``cancel()``; enable states from ``can_*``. No Back
  button; no default button anywhere;
* ``ABORTED``: the reason (``abort_reason``) + [Close] / [Restart wizard]; ``RESTORING`` / ``DONE`` as pages;
* closing the window (or Esc) = Cancel; past the first phase it asks **C-08** first.

The page is refreshed from the bridge's ``engineState`` signal and on every main-window refresh tick
(:meth:`refresh`). The engine owns every rule, computation and text (P1).

Implements: SW-CAL-001 (wizard frame, Continue / Repeat / Cancel, progress, STOP reachable, cancel keeps the
calibration — engine), SAF-SW-004 (C-05 / C-06 / C-12 keyboard rules), SW-STOP-001 (STOP in every wizard)
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bend_stand.core.api import GateId
from bend_stand.gui import gating
from bend_stand.gui.dialogs.confirm_dialog import make_confirm
from bend_stand.gui.dialogs.safe_dialog import SafeDialog
from bend_stand.gui.format import NA, fmt_value
from bend_stand.gui.units_state import FORCE_KEY, force_unit
from bend_stand.gui.wizards import phase_views as pv

log = logging.getLogger(__name__)

SEV_STYLE = {"REFUSE": "color: #a00000;", "CONFIRM": "color: #805000;", "WARN": "color: #805000;"}
SEV_MARK = {"REFUSE": "✗", "CONFIRM": "?", "WARN": "⚠"}


def _err_text(exc: BaseException) -> str:
    gate = getattr(exc, "gate", None)
    if gate is not None and hasattr(gate, "items"):
        return gating.refusal_text(gate) or gate.text()
    return str(getattr(exc, "user_text", "") or exc or type(exc).__name__)


class SafeWizard(SafeDialog):
    """Base wizard; subclasses set :attr:`KIND`, :attr:`START_GATE`, :attr:`CONFIRM_CID`, :attr:`TITLE` and may
    add start-configuration fields (:meth:`build_start_config` / :meth:`start_config`) and extra buttons."""

    KIND = ""
    TITLE = "Wizard"
    START_GATE: GateId | None = None
    CONFIRM_CID = "C-05"

    noSpecimenRequested = Signal()
    message = Signal(str, str)

    def __init__(self, backend: Any, bridge: Any, engine: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent, title=self.TITLE)
        self.setObjectName(f"wizard_{self.KIND}")
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.stop_button._source = f"wizard:{self.KIND}"          # noqa: SLF001 - STOP source label
        self._backend = backend
        self._bridge = bridge
        self.engine = engine
        self.state: Any = None
        self.started = False
        self.confirm_dialog: Any = None
        self._confirm_for: Any = None
        self._inputs: dict[str, QDoubleSpinBox] = {}
        self._input_specs: tuple[Any, ...] = ()
        self._closing_ok = False
        self.phases: tuple[str, ...] = tuple(getattr(engine, "PHASES", ()) or ())

        lay = QVBoxLayout()
        self.strip = QLabel("", self)
        self.strip.setObjectName("stepStrip")
        self.strip.setWordWrap(True)
        self.strip.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.strip)

        # ---- start page
        self.start_page = QGroupBox("Before you start", self)
        sp = QVBoxLayout(self.start_page)
        self.checklist = QLabel("", self.start_page)
        self.checklist.setObjectName("startChecklist")
        self.checklist.setWordWrap(True)
        self.checklist.setTextFormat(Qt.TextFormat.RichText)
        sp.addWidget(self.checklist)
        self.config_form = QFormLayout()
        sp.addLayout(self.config_form)
        self.build_start_config(self.config_form)
        srow = QHBoxLayout()
        self.nospec_button = QPushButton("Enter no-specimen mode…", self.start_page)
        self.nospec_button.setObjectName("wizardNoSpecimen")
        self.nospec_button.setAutoDefault(False)
        self.nospec_button.clicked.connect(self.noSpecimenRequested)
        self.nospec_button.hide()
        self.start_button = QPushButton("Start", self.start_page)
        self.start_button.setObjectName("wizardStart")
        self.start_button.setAutoDefault(False)
        self.start_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.start_button.clicked.connect(self.start)
        srow.addWidget(self.nospec_button)
        srow.addStretch(1)
        srow.addWidget(self.start_button)
        sp.addLayout(srow)
        self.start_msg = QLabel("", self.start_page)
        self.start_msg.setObjectName("startMessage")
        self.start_msg.setWordWrap(True)
        self.start_msg.setStyleSheet("color: #a00000;")
        sp.addWidget(self.start_msg)
        lay.addWidget(self.start_page)

        # ---- engine page
        self.page = QWidget(self)
        pl = QVBoxLayout(self.page)
        pl.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel("", self.page)
        self.title_label.setObjectName("wizardTitle")
        f = self.title_label.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 2)
        self.title_label.setFont(f)
        self.instruction = QLabel("", self.page)
        self.instruction.setObjectName("wizardInstruction")
        self.instruction.setWordWrap(True)
        pl.addWidget(self.title_label)
        pl.addWidget(self.instruction)
        self.inputs_box = QWidget(self.page)
        self.inputs_form = QFormLayout(self.inputs_box)
        pl.addWidget(self.inputs_box)
        self.live_label = QLabel("", self.page)
        self.live_label.setObjectName("wizardLive")
        pl.addWidget(self.live_label)
        self.progress = QProgressBar(self.page)
        self.progress.setObjectName("wizardProgress")
        self.progress.setRange(0, 1000)
        pl.addWidget(self.progress)
        self.stats_table = self._table(self.page, "wizardStats")
        pl.addWidget(self.stats_table)
        self.summary_label = QLabel("", self.page)
        self.summary_label.setObjectName("resultSummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pl.addWidget(self.summary_label)
        self.result_table = self._table(self.page, "wizardResult")
        pl.addWidget(self.result_table)
        self.plot = pg.PlotWidget(self.page)
        self.plot.setObjectName("fitPlot")
        self.plot.setMinimumHeight(160)
        self.plot.setLabel("bottom", "raw [counts]")
        self.plot.setLabel("left", "F [N]")
        self.fit_scatter = pg.ScatterPlotItem(size=8, brush=pg.mkBrush("#1f77b4"))
        self.fit_line = pg.PlotCurveItem(pen=pg.mkPen("#d62728", width=1))
        self.plot.addItem(self.fit_scatter)
        self.plot.addItem(self.fit_line)
        pl.addWidget(self.plot)
        self.messages = QLabel("", self.page)
        self.messages.setObjectName("wizardMessages")
        self.messages.setWordWrap(True)
        self.messages.setTextFormat(Qt.TextFormat.RichText)
        pl.addWidget(self.messages)
        self.abort_label = QLabel("", self.page)
        self.abort_label.setObjectName("abortReason")
        self.abort_label.setWordWrap(True)
        self.abort_label.setStyleSheet("color: #a00000; font-weight: bold;")
        pl.addWidget(self.abort_label)
        lay.addWidget(self.page, 1)

        # ---- buttons (no default button, P3)
        brow = QHBoxLayout()
        self.cancel_button = self._button("Cancel", "wizardCancel", self.on_cancel)
        self.close_button = self._button("Close", "wizardClose", self.close)
        self.restart_button = self._button("Restart wizard", "wizardRestart", self.start)
        brow.addWidget(self.cancel_button)
        brow.addWidget(self.close_button)
        brow.addWidget(self.restart_button)
        brow.addStretch(1)
        self.extra_row = QHBoxLayout()
        brow.addLayout(self.extra_row)
        self.repeat_button = self._button("Repeat", "wizardRepeat", self.on_repeat)
        self.continue_button = self._button("Continue", "wizardContinue", self.on_continue)
        brow.addWidget(self.repeat_button)
        brow.addWidget(self.continue_button)
        lay.addLayout(brow)
        self.setLayout(lay)
        self.build_extra_buttons(self.extra_row)

        bridge.engineState.connect(self._on_engine_event)
        self.resize(640, 560)
        self.refresh()

    # ------------------------------------------------------------------ helpers
    def _button(self, text: str, name: str, slot: Any) -> QPushButton:
        b = QPushButton(text, self)
        b.setObjectName(name)
        b.setAutoDefault(False)
        b.setDefault(False)
        b.clicked.connect(slot)
        return b

    @staticmethod
    def _table(parent: QWidget, name: str) -> QTableWidget:
        t = QTableWidget(0, 2, parent)
        t.setObjectName(name)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setMaximumHeight(170)
        return t

    # ------------------------------------------------------------------ subclass hooks
    def build_start_config(self, form: QFormLayout) -> None:
        """Add start-configuration fields (default: none)."""

    def start_config(self) -> dict[str, Any]:
        return {}

    def build_extra_buttons(self, row: QHBoxLayout) -> None:
        """Add kind-specific buttons (default: none)."""

    def update_extra(self, state: Any) -> None:
        """Enable / show kind-specific buttons from the state (default: nothing)."""

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        config = self.start_config()
        try:
            g = self.engine.start(**config)
        except Exception as exc:  # noqa: BLE001
            self._start_refused(_err_text(exc))
            return
        if g.refused:
            self._start_refused(gating.refusal_text(g))
            return
        if g.confirm_items:
            self._confirm_start(g, config)
            return
        self._started()

    def _start_refused(self, text: str) -> None:
        self.start_msg.setText("Start refused: " + text)
        self.message.emit(f"{self.TITLE}: start refused: {text}", "warn")
        self.refresh()

    def _confirm_start(self, gate: Any, config: dict[str, Any]) -> None:
        """C-12. B §15.4 rule 6 / B5-18: a start gate with CONFIRM items starts nothing; the start is repeated with
        ``confirmed=True`` only after a mouse confirmation (declining = nothing happens)."""
        items = gate.confirm_items
        text = "\n".join(i.text for i in items)
        assertion = "No specimen is mounted" if any("specimen" in i.text.lower() for i in items) else None
        dlg = make_confirm(self, "C-12", text=text, assertion=assertion)
        dlg.confirmed.connect(lambda: self._start_confirmed(config))
        self.confirm_dialog = dlg
        dlg.open()

    def _start_confirmed(self, config: dict[str, Any]) -> None:
        try:
            g = self.engine.start(**config, confirmed=True)
        except Exception as exc:  # noqa: BLE001
            self._start_refused(_err_text(exc))
            return
        if g.refused:
            self._start_refused(gating.refusal_text(g))
            return
        self._started()

    def _started(self) -> None:
        self.started = True
        self.start_msg.setText("")
        self._confirm_for = None
        self.refresh()

    # ------------------------------------------------------------------ engine calls
    def inputs(self) -> dict[str, float]:
        return {k: float(s.value()) for k, s in self._inputs.items()}

    def on_continue(self) -> None:
        st = self.state
        if st is None or not st.can_continue:
            return
        vals = self.inputs()
        try:
            self.engine.continue_(vals or None, confirmed=False)
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"{self.TITLE}: {_err_text(exc)}", "warn")
        self.refresh()

    def on_repeat(self) -> None:
        try:
            self.engine.repeat()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"{self.TITLE}: {_err_text(exc)}", "warn")
        self.refresh()

    def on_cancel(self) -> None:
        try:
            self.engine.cancel()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"{self.TITLE}: {_err_text(exc)}", "warn")
        self.refresh()

    def _confirm_continue(self, req: Any) -> None:
        dlg = make_confirm(self, self.CONFIRM_CID, text=getattr(req, "text", None) or None)
        vals = self.inputs()
        dlg.confirmed.connect(lambda: self._continue_confirmed(vals))
        self.confirm_dialog = dlg
        dlg.open()

    def _continue_confirmed(self, vals: dict[str, float]) -> None:
        try:
            self.engine.continue_(vals or None, confirmed=True)
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"{self.TITLE}: {_err_text(exc)}", "warn")
        self.refresh()

    # ------------------------------------------------------------------ rendering
    def _on_engine_event(self, record: Any) -> None:
        if getattr(record.payload, "kind", None) == self.KIND or record.topic.startswith(self._topic_prefix()):
            self.refresh()

    def _topic_prefix(self) -> str:
        return {"travel_cal": "cal.travel", "load_cal": "cal.load", "tare": "tare"}.get(self.KIND, self.KIND)

    def phase(self) -> str:
        return str(getattr(self.state, "phase", "") or "")

    def is_idle(self) -> bool:
        """Start page: the engine is idle, or this window has not started it and it only shows the end state of an
        earlier run (DONE / ABORTED …)."""
        ph = self.phase()
        if ph in pv.IDLE_PHASES:
            return True
        return not self.started and (ph in pv.TERMINAL_PHASES or ph not in self.phases)

    def refresh(self) -> None:
        try:
            st = self.engine.state()
        except Exception:  # noqa: BLE001
            log.debug("engine.state failed", exc_info=True)
            return
        changed = st != self.state
        self.state = st
        if self.phase() in pv.strip_phases(self.phases):         # a running engine (e.g. started elsewhere)
            self.started = True
        self._render_strip()
        idle = self.is_idle()
        self.start_page.setVisible(idle)
        self.page.setVisible(not idle)
        if idle:
            self._render_start()
        elif changed:
            self._render_page(st)
        if not idle:
            self._render_live(st)
        self._render_buttons(st, idle)
        if (not idle and st.needs_confirmation is not None and self._confirm_for != st.needs_confirmation):
            self._confirm_for = st.needs_confirmation
            self._confirm_continue(st.needs_confirmation)
        elif st.needs_confirmation is None:
            self._confirm_for = None

    def _render_strip(self) -> None:
        cur = self.phase()
        parts = []
        for p in pv.strip_phases(self.phases):
            parts.append(f"<b>[{p}]</b>" if p == cur else p)
        txt = " &gt; ".join(parts)
        if cur in pv.TERMINAL_PHASES:
            txt += f"  —  <b>{cur}</b>"
        if self.strip.text() != txt:
            self.strip.setText(txt)

    def _render_start(self) -> None:
        st = self._backend.status()
        gate = gating.gate_of(st, self.START_GATE) if self.START_GATE is not None else None
        lines = []
        show_nospec = False
        if gate is None:
            lines.append("<span style='color:#a00000'>✗ start gate not available</span>")
        elif not gate.items:
            lines.append("<span style='color:#206020'>✓ all start conditions met</span>")
        for i in (gate.items if gate is not None else ()):
            sev = str(getattr(i.severity, "value", i.severity))
            hint = f" — {i.clear_hint}" if i.clear_hint else ""
            lines.append(f"<span style='{SEV_STYLE.get(sev, '')}'>{SEV_MARK.get(sev, '•')} {i.text}{hint}</span>")
            if i.code == "LOAD_INPUT_INVALID" and sev == "REFUSE":
                show_nospec = True
        txt = "<br>".join(lines)
        if self.checklist.text() != txt:
            self.checklist.setText(txt)
        self.nospec_button.setVisible(show_nospec and not st.safety.no_specimen_mode)
        self.start_button.setEnabled(gate is not None and gate.ok)
        self.start_button.setToolTip(gating.control_state(gate, motion=True).tooltip)

    def _render_page(self, st: Any) -> None:
        view = pv.view_of(self.KIND, st.phase)
        parts = pv.VIEW_PARTS.get(view, pv.VIEW_PARTS["generic"])
        self.title_label.setText(st.title or st.phase)
        self.instruction.setText(st.instruction)
        self._render_inputs(st.inputs)
        self.inputs_box.setVisible(bool(st.inputs))
        self.progress.setVisible(st.progress is not None or "progress" in parts and st.progress is not None)
        if st.progress is not None:
            self.progress.setValue(int(round(max(0.0, min(1.0, float(st.progress))) * 1000)))
            self.progress.setFormat(f"{100.0 * float(st.progress):.0f} %")
        self._fill_kv(self.stats_table, st.stats)
        self.stats_table.setVisible(bool(st.stats))
        summary, headers, rows = pv.result_parts(st.result)
        self.summary_label.setText("   ".join(f"{k} = {v}" for k, v in summary))
        self.summary_label.setVisible(bool(summary))
        self._fill_table(self.result_table, headers, rows)
        self.result_table.setVisible(bool(rows))
        xs, ys, k, b = pv.fit_points(st.result)
        residual_mode = False
        if not xs:
            xs, ys = pv.residual_points(st.result)
            residual_mode = bool(xs)
        show_plot = "plot" in parts and bool(xs)
        self.plot.setVisible(show_plot)
        if show_plot:
            self.fit_scatter.setData(xs, ys)
            if residual_mode:
                self.plot.setLabel("bottom", "point")
                self.plot.setLabel("left", "residual [N]")
                self.fit_line.setData([min(xs), max(xs)], [0.0, 0.0])
            else:
                self.plot.setLabel("bottom", "raw [counts]")
                self.plot.setLabel("left", "F [N]")
                if k is not None and b is not None:
                    lo, hi = min(xs + [0.0]), max(xs)
                    self.fit_line.setData([lo, hi], [k * lo + b, k * hi + b])
        msgs = [f"<span style='color:#a00000'>✗ {e}</span>" for e in st.errors]
        msgs += [f"<span style='color:#805000'>⚠ {w}</span>" for w in st.warnings]
        self.messages.setText("<br>".join(msgs))
        self.messages.setVisible(bool(msgs))
        abort = ""
        if st.phase in ("ABORTED", "CANCELLED") or st.abort_reason:
            abort = f"{st.phase}: {st.abort_reason or 'cancelled'} — the active calibration is unchanged."
        self.abort_label.setText(abort)
        self.abort_label.setVisible(bool(abort))
        self.live_label.setVisible("live" in parts)

    def _render_inputs(self, specs: tuple[Any, ...]) -> None:
        if tuple(specs) == self._input_specs:
            return
        old = self.inputs()
        while self.inputs_form.rowCount():
            self.inputs_form.removeRow(0)
        self._inputs = {}
        for spec in specs:
            s = QDoubleSpinBox(self.inputs_box)
            s.setObjectName(f"input_{spec.key}")
            s.setDecimals(3)
            s.setKeyboardTracking(False)
            s.setRange(spec.min if spec.min is not None else -1e9, spec.max if spec.max is not None else 1e9)
            if spec.unit:
                s.setSuffix(f" {spec.unit}")
            if spec.key in old:
                s.setValue(old[spec.key])
            elif spec.default is not None:
                s.setValue(float(spec.default))
            self.inputs_form.addRow(spec.label or spec.key, s)
            self._inputs[spec.key] = s
        self._input_specs = tuple(specs)

    @staticmethod
    def _fill_kv(table: QTableWidget, data: Mapping[str, Any] | None) -> None:
        items = list((data or {}).items())
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["", "value"])
        table.setRowCount(len(items))
        for r, (k, v) in enumerate(items):
            table.setItem(r, 0, QTableWidgetItem(str(k)))
            table.setItem(r, 1, QTableWidgetItem(pv.fmt_any(v)))

    @staticmethod
    def _fill_table(table: QTableWidget, headers: list[str], rows: list[list[str]]) -> None:
        table.setColumnCount(max(1, len(headers)))
        table.setHorizontalHeaderLabels(headers or [""])
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(v))

    def _render_live(self, st: Any) -> None:
        if not self.live_label.isVisibleTo(self.page):
            return
        try:
            bs = self._backend.status()
            m = bs.motion
            u = force_unit().unit
            f_txt = NA
            try:
                s = self._backend.data.latest(FORCE_KEY[u])
                f_txt = f"{fmt_value(s.value if math.isfinite(s.value) else None, u)} {u}"
            except Exception:  # noqa: BLE001
                pass
            txt = (f"live:  x {fmt_value(m.position_mm, 'mm')} mm   target {fmt_value(m.commanded_target_mm, 'mm')} mm"
                   f"   F {f_txt}   state {m.motion_state or NA}")
        except Exception:  # noqa: BLE001
            txt = ""
        if self.live_label.text() != txt:
            self.live_label.setText(txt)

    def _render_buttons(self, st: Any, idle: bool) -> None:
        terminal = st.phase in pv.TERMINAL_PHASES
        running = not idle and not terminal
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(bool(st.can_cancel) or running)
        self.repeat_button.setVisible(running)
        self.repeat_button.setEnabled(bool(st.can_repeat))
        self.continue_button.setVisible(running)
        self.continue_button.setEnabled(bool(st.can_continue))
        label = st.continue_label or "Continue"
        if st.continue_moves and "▶" not in label:
            label += " ▶"
        if self.continue_button.text() != label.replace("&", "&&"):
            self.continue_button.setText(label.replace("&", "&&"))
        self.continue_button.setFocusPolicy(Qt.FocusPolicy.NoFocus if st.continue_moves
                                            else Qt.FocusPolicy.StrongFocus)
        self.close_button.setVisible(idle or terminal)
        self.restart_button.setVisible(terminal and st.phase != "RESTORING")
        self.update_extra(st)

    # ------------------------------------------------------------------ close = cancel (C-08 past the first phase)
    def needs_cancel_confirm(self) -> bool:
        st = self.state
        if st is None or self.is_idle() or st.phase in pv.TERMINAL_PHASES:
            return False
        first = pv.strip_phases(self.phases)[:1]
        return st.phase not in first

    def _may_close(self) -> bool:
        """False while C-08 is asked; True = close now (a running engine is cancelled first)."""
        if self._closing_ok:
            return True
        st = self.state
        running = st is not None and not self.is_idle() and st.phase not in pv.TERMINAL_PHASES
        if running and self.needs_cancel_confirm():
            dlg = make_confirm(self, "C-08")
            dlg.confirmed.connect(self._cancel_and_close)
            self.confirm_dialog = dlg
            dlg.open()
            return False
        if running:
            self.on_cancel()
        self._closing_ok = True
        return True

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override (QDialog's would call reject() again)
        if self._may_close():
            event.accept()
        else:
            event.ignore()

    def _cancel_and_close(self) -> None:
        self.on_cancel()
        self._closing_ok = True
        self.close()

    def reject(self) -> None:  # Esc → the same path as closing the window
        if self._may_close():
            super().reject()


def retake_menu(button: QToolButton, count: int, slot: Any) -> None:
    menu = button.menu()
    if menu is None:
        menu = QMenu(button)
        button.setMenu(menu)
    if len(menu.actions()) == count:
        return
    menu.clear()
    for i in range(count):
        act = menu.addAction("zero point" if i == 0 else f"point {i}")
        act.triggered.connect(lambda _c=False, n=i: slot(n))
