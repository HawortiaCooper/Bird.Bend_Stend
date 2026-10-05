"""Test marks tab (SW_design_GUI §3.3).

* Fixed marks: measured element (specimen) *, specimen number *, operator, notes (``*`` = recommended; an empty
  value gives a warning at record start, not a refusal — GQ-12, the backend's ``record_start`` WARN item).
* Custom key / value fields: [+ Add] / [Rename] / [- Remove], edited in place. The table is written to the backend
  (``marks.set(TestMarks)``) on every committed edit (debounced 300 ms). Empty or duplicate keys are marked red
  and not sent (form integrity; the backend refuses them too with ``ValueError``, B5-12 — shown verbatim).
* Presets: combo of ``marks.list_presets()`` (paths); [Load] → ``marks.load_preset(path)`` fills the form;
  [Save as…] → ``SafeFileDialog`` (STOP inside) → ``marks.save_preset(path)``.
* 3-point bend (optional, off by default): span L, width b, thickness h → ``SessionSettings.bend3p``
  (``Bend3pGeometry``, B5-11) via ``session.set``; issues shown verbatim. Enables the derived channels σ / ε.
* Automatic snapshot (read-only, added by the backend to every recording and report): board configuration,
  calibration, tare, limits, versions — built from ``status()`` and ``limits.get()`` for display.
* While recording: footer "edits are logged as MARK_EDIT rows; final marks written at stop"; ``marks.edited`` rows
  appear in the Event log.

Implements: SW-META-001 (marks + custom fields), SW-META-002 (presets, snapshot view), SW-LIM-003 (limits in the
snapshot view), SW-REP-004 (3-point-bend geometry entry)
"""
from __future__ import annotations

import dataclasses
import logging
import os
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bend_stand import __version__ as SW_VERSION
from bend_stand.core import api
from bend_stand.core.api import TestMarks
from bend_stand.gui.dialogs.safe_dialog import get_save_file_name
from bend_stand.gui.format import NA, fmt_hex, fmt_value, fmt_version

log = logging.getLogger(__name__)

PRESET_FILTER = "Mark presets (*.bbmarks.json);;All files (*)"
APPLY_DEBOUNCE_MS = 300
BAD = QBrush(QColor("#ffc8c8"))


class MarksTab(QWidget):
    message = Signal(str, str)

    def __init__(self, backend: Any, bridge: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("marksTab")
        self._backend = backend
        self._bridge = bridge
        self._loading = False
        self._n = 0
        self.applied = 0

        lay = QVBoxLayout(self)
        lay.addWidget(self._build_presets())
        lay.addWidget(self._build_marks())
        lay.addWidget(self._build_bend3p())
        lay.addWidget(self._build_snapshot())
        self.footer = QLabel("", self)
        self.footer.setObjectName("marksFooter")
        self.footer.setWordWrap(True)
        self.footer.setStyleSheet("color: #805000;")
        lay.addWidget(self.footer)
        lay.addStretch(1)

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(APPLY_DEBOUNCE_MS)
        self._apply_timer.timeout.connect(self.apply)
        self.reload()
        self.refresh_presets()

    # ================================================================== construction
    def _build_presets(self) -> QGroupBox:
        box = QGroupBox("Preset", self)
        row = QHBoxLayout(box)
        self.preset_combo = QComboBox(box)
        self.preset_combo.setObjectName("presetCombo")
        self.preset_combo.setMinimumWidth(260)
        self.load_button = QPushButton("Load", box)
        self.load_button.setObjectName("presetLoad")
        self.save_button = QPushButton("Save as…", box)
        self.save_button.setObjectName("presetSave")
        for b in (self.load_button, self.save_button):
            b.setAutoDefault(False)
        self.load_button.clicked.connect(self.load_preset)
        self.save_button.clicked.connect(self.save_preset)
        row.addWidget(self.preset_combo, 1)
        row.addWidget(self.load_button)
        row.addWidget(self.save_button)
        return box

    def _build_marks(self) -> QGroupBox:
        box = QGroupBox("Marks", self)
        form = QFormLayout(box)
        self.specimen = QLineEdit(box)
        self.specimen.setObjectName("markSpecimen")
        self.number = QLineEdit(box)
        self.number.setObjectName("markNumber")
        self.operator = QLineEdit(box)
        self.operator.setObjectName("markOperator")
        self.notes = QPlainTextEdit(box)
        self.notes.setObjectName("markNotes")
        self.notes.setFixedHeight(70)
        for e in (self.specimen, self.number, self.operator):
            e.editingFinished.connect(self._schedule)
            e.textEdited.connect(lambda _t: self._schedule())
        self.notes.textChanged.connect(self._schedule)
        form.addRow("Measured element *", self.specimen)
        form.addRow("Specimen number *", self.number)
        form.addRow("Operator", self.operator)
        form.addRow("Notes", self.notes)
        self.custom = QTableWidget(0, 2, box)
        self.custom.setObjectName("customFields")
        self.custom.setHorizontalHeaderLabels(["Key", "Value"])
        self.custom.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.custom.verticalHeader().setVisible(False)
        self.custom.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.custom.setFixedHeight(130)
        self.custom.itemChanged.connect(lambda _i: self._schedule())
        brow = QVBoxLayout()
        self.add_button = QPushButton("+ Add", box)
        self.add_button.setObjectName("customAdd")
        self.rename_button = QPushButton("Rename", box)
        self.rename_button.setObjectName("customRename")
        self.remove_button = QPushButton("- Remove", box)
        self.remove_button.setObjectName("customRemove")
        for b in (self.add_button, self.rename_button, self.remove_button):
            b.setAutoDefault(False)
            brow.addWidget(b)
        brow.addStretch(1)
        self.add_button.clicked.connect(self.add_field)
        self.rename_button.clicked.connect(self.rename_field)
        self.remove_button.clicked.connect(self.remove_field)
        crow = QHBoxLayout()
        crow.addWidget(self.custom, 1)
        crow.addLayout(brow)
        form.addRow("Custom fields", crow)
        self.custom_issue = QLabel("", box)
        self.custom_issue.setObjectName("customIssue")
        self.custom_issue.setStyleSheet("color: #a00000;")
        form.addRow("", self.custom_issue)
        return box

    def _build_bend3p(self) -> QGroupBox:
        box = QGroupBox("3-point bend (optional, off by default; session geometry)", self)
        row = QHBoxLayout(box)
        self.bend_en = QCheckBox("enable", box)
        self.bend_en.setObjectName("bend3pEnabled")
        row.addWidget(self.bend_en)
        self.bend_spins: dict[str, QDoubleSpinBox] = {}
        for key, label, default in (("span_mm", "span L", 64.0), ("width_mm", "width b", 10.0),
                                    ("thickness_mm", "thickness h", 4.0)):
            row.addWidget(QLabel(label, box))
            sp = QDoubleSpinBox(box)
            sp.setObjectName(f"bend3p_{key}")
            sp.setDecimals(2)
            sp.setRange(0.01, 10000.0)
            sp.setValue(default)
            sp.setSuffix(" mm")
            sp.setKeyboardTracking(False)
            row.addWidget(sp)
            self.bend_spins[key] = sp
        self.bend_apply = QPushButton("Apply", box)
        self.bend_apply.setObjectName("bend3pApply")
        self.bend_apply.setAutoDefault(False)
        self.bend_apply.clicked.connect(self.apply_bend3p)
        row.addWidget(self.bend_apply)
        self.bend_issue = QLabel("", box)
        self.bend_issue.setObjectName("bend3pIssue")
        self.bend_issue.setStyleSheet("color: #a00000;")
        row.addWidget(self.bend_issue, 1)
        self._geom_cls = getattr(api, "Bend3pGeometry", None)
        box.setEnabled(self._geom_cls is not None)
        if self._geom_cls is None:
            box.setToolTip("3-point-bend geometry: backend support pending (B5-11)")
        else:
            self._show_bend3p()
        return box

    def _show_bend3p(self) -> None:
        try:
            geom = getattr(self._backend.session.get(), "bend3p", None)
        except Exception:  # noqa: BLE001
            geom = None
        self.bend_en.setChecked(geom is not None)
        if geom is not None:
            for key, sp in self.bend_spins.items():
                sp.setValue(float(getattr(geom, key)))

    def apply_bend3p(self) -> bool:
        """``session.set(replace(s, bend3p=Bend3pGeometry(L, b, h) | None))``; ERROR issues shown, nothing applied."""
        if self._geom_cls is None:
            return False
        geom = None
        if self.bend_en.isChecked():
            geom = self._geom_cls(**{k: float(sp.value()) for k, sp in self.bend_spins.items()})
        try:
            s = self._backend.session.get()
            issues = list(self._backend.session.set(dataclasses.replace(s, bend3p=geom)) or [])
            errors = [i.text for i in issues if str(getattr(i, "severity", "ERROR")) == "ERROR"]
        except Exception as exc:  # noqa: BLE001
            errors = [str(getattr(exc, "user_text", "") or exc)]
        self.bend_issue.setText("; ".join(errors))
        if not errors:
            self.message.emit("3-point-bend geometry " + ("applied" if geom is not None else "off"), "info")
        return not errors

    def _build_snapshot(self) -> QGroupBox:
        box = QGroupBox("Automatic snapshot (read-only, added to every recording and report)", self)
        lay = QVBoxLayout(box)
        self.snapshot_label = QLabel(NA, box)
        self.snapshot_label.setObjectName("marksSnapshot")
        self.snapshot_label.setWordWrap(True)
        lay.addWidget(self.snapshot_label)
        return box

    # ================================================================== model ↔ form
    def reload(self) -> None:
        try:
            self.show_marks(self._backend.marks.get())
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"marks.get failed: {exc}", "error")

    def show_marks(self, m: TestMarks) -> None:
        self._loading = True
        try:
            self.specimen.setText(m.specimen)
            self.number.setText(m.number)
            self.operator.setText(m.operator)
            self.notes.setPlainText(m.notes)
            self.custom.setRowCount(0)
            for k, v in m.custom:
                self._append_row(k, v)
        finally:
            self._loading = False
        self._validate_custom()

    def _append_row(self, key: str, value: str) -> int:
        r = self.custom.rowCount()
        self.custom.insertRow(r)
        self.custom.setItem(r, 0, QTableWidgetItem(key))
        self.custom.setItem(r, 1, QTableWidgetItem(value))
        return r

    def custom_fields(self) -> list[tuple[str, str]]:
        out = []
        for r in range(self.custom.rowCount()):
            k = self.custom.item(r, 0)
            v = self.custom.item(r, 1)
            out.append(((k.text() if k else "").strip(), v.text() if v else ""))
        return out

    def form_marks(self) -> TestMarks:
        return TestMarks(self.specimen.text().strip(), self.number.text().strip(), self.operator.text().strip(),
                         self.notes.toPlainText(), tuple(self.custom_fields()))

    def _validate_custom(self) -> list[str]:
        """Empty / duplicate keys are marked (form integrity only, GRQ-B-22)."""
        problems: list[str] = []
        seen: dict[str, int] = {}
        self.custom.blockSignals(True)
        for r, (k, _v) in enumerate(self.custom_fields()):
            item = self.custom.item(r, 0)
            bad = not k or k in seen
            if not k:
                problems.append(f"row {r + 1}: empty key")
            elif k in seen:
                problems.append(f"duplicate key '{k}'")
            seen.setdefault(k, r)
            if item is not None:
                item.setBackground(BAD if bad else QBrush())
        self.custom.blockSignals(False)
        self.custom_issue.setText("; ".join(problems))
        return problems

    def _schedule(self) -> None:
        if not self._loading:
            self._apply_timer.start()

    def apply(self) -> bool:
        """Send the form to the backend (``marks.set``); refused while a custom key is empty or duplicated."""
        self._apply_timer.stop()
        if self._validate_custom():
            return False
        try:
            self._backend.marks.set(self.form_marks())
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Marks not applied: {getattr(exc, 'user_text', exc)}", "warn")
            return False
        self.applied += 1
        return True

    # ================================================================== custom fields
    def add_field(self) -> None:
        n = 1
        keys = {k for k, _ in self.custom_fields()}
        while f"field{n}" in keys:
            n += 1
        r = self._append_row(f"field{n}", "")
        self.custom.setCurrentCell(r, 0)
        self.custom.editItem(self.custom.item(r, 0))
        self._schedule()

    def rename_field(self) -> None:
        r = self.custom.currentRow()
        if r >= 0:
            self.custom.setCurrentCell(r, 0)
            self.custom.editItem(self.custom.item(r, 0))

    def remove_field(self) -> None:
        r = self.custom.currentRow()
        if r >= 0:
            self.custom.removeRow(r)
            self._schedule()

    # ================================================================== presets
    def refresh_presets(self) -> None:
        try:
            presets = list(self._backend.marks.list_presets())
        except Exception:  # noqa: BLE001
            presets = []
        self.preset_combo.clear()
        for p in presets:
            self.preset_combo.addItem(os.path.basename(p).replace(".bbmarks.json", ""), p)
        self.load_button.setEnabled(bool(presets))

    def load_preset(self) -> None:
        path = self.preset_combo.currentData()
        if not path:
            return
        try:
            m = self._backend.marks.load_preset(path)
        except Exception as exc:  # noqa: BLE001 - FileFormatError
            self.message.emit(f"Preset not loaded: {getattr(exc, 'user_text', exc)}", "warn")
            return
        self.show_marks(m)
        self.apply()
        self.message.emit(f"Preset loaded: {os.path.basename(path)}", "info")

    def save_preset(self) -> None:
        if not self.apply():
            self.message.emit("Fix the custom fields first (empty / duplicate key)", "warn")
            return
        path = get_save_file_name(self, "Save mark preset", "", PRESET_FILTER, "bbmarks.json")
        if not path:
            return
        try:
            self._backend.marks.save_preset(path)
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Preset not saved: {getattr(exc, 'user_text', exc)}", "error")
            return
        self.refresh_presets()
        i = self.preset_combo.findData(path)
        if i >= 0:
            self.preset_combo.setCurrentIndex(i)
        self.message.emit(f"Preset saved: {os.path.basename(path)}", "info")

    # ================================================================== status
    def update_status(self, st: Any) -> None:
        self._n += 1
        rec = getattr(st.recording, "state", "IDLE") == "RECORDING"
        foot = ("Recording running: edits are logged as MARK_EDIT rows; the final marks are written at stop."
                if rec else "")
        if self.footer.text() != foot:
            self.footer.setText(foot)
        self.footer.setVisible(rec)
        if self._n % 15 == 1:
            text = snapshot_text(st, self._limits_cfg())
            if self.snapshot_label.text() != text:
                self.snapshot_label.setText(text)

    def _limits_cfg(self) -> Any:
        try:
            return self._backend.limits.get()
        except Exception:  # noqa: BLE001
            return None


def snapshot_text(st: Any, cfg: Any) -> str:
    """Read-only view of the automatic snapshot (display only)."""
    link = st.link
    info = getattr(link, "info", None)
    cal = st.calibration
    thr = st.safety.thresholds
    lines = []
    if info is not None:
        lines.append(f"Board config  hash {fmt_hex(info.param_dict_hash)}, CFG "
                     f"{'dirty' if st.cfg_dirty else 'clean' if st.cfg_dirty is not None else '?'}, "
                     f"steps/mm board {fmt_value(cal.board_spm, 'mm')}")
    else:
        lines.append("Board config  (not connected)")
    lines.append(f"Calibration   load {cal.load_status or 'none'}" + (" LOW_SPAN" if cal.low_span else "")
                 + (f", K {cal.load_k:.6g} N/count" if cal.load_k is not None else "")
                 + f"; travel {fmt_value(cal.travel_spm, 'mm')} steps/mm")
    tare = st.tare
    lines.append(f"Tare          {tare.state}" + (f", tare_raw {fmt_value(tare.tare_raw, 'counts')}"
                                                   if tare.tare_raw is not None else ""))
    if cfg is not None:
        tmin = f"{fmt_value(cfg.travel_min_mm, 'mm')}" if cfg.travel_min_enabled else "off"
        tmax = f"{fmt_value(cfg.travel_max_mm, 'mm')}" if cfg.travel_max_enabled else "off"
        lines.append(f"Limits        pull {fmt_value(cfg.pull_trip_n, 'N') if cfg.pull_enabled else 'off'} N / push "
                     f"{fmt_value(cfg.push_trip_n, 'N') if cfg.push_enabled else 'off'} N / travel {tmin} … {tmax} mm"
                     f" / FW {fmt_value(cfg.fw_level_n, 'N')} N; thresholds {thr.state}; no-specimen: "
                     f"{'ON' if st.safety.no_specimen_mode else 'off'}")
    fw = fmt_version(info.fw_version) if info is not None else NA
    proto = f"{info.proto_major}.{info.proto_minor}" if info is not None else NA
    lines.append(f"Versions      SW {SW_VERSION}, FW {fw}, proto {proto}")
    return "\n".join(lines)
