"""Report tab (SW_design_GUI §3.7): recordings, step results, report build / re-build, open the HTML.

* **Recordings** — ``reports.list_recordings(root=None)`` (newest first: date, specimen marks, sequence, status
  COMPLETE / PARTIAL / FAILED, duration, report present); [Refresh]; the topic ``report.ready`` refreshes it.
* **Selected recording** — ``reports.load_result(dir)`` (parsed ``report.json``): marks, calibration, warnings
  (LOW_SPAN, tension-calibrated push, INCOMPLETE windows, NOT_REACHED, frame losses …) and the read-only **step
  result table** (step, loop iteration, label, N, F mean / std / min / max / SE / drift, x mean, target, flags
  verbatim — INCOMPLETE, ON_TARGET, NOT_REACHED in red; SW-REP-002).
* **Options** — re-apply another **calibration** file and / or another **tare** raw value (SW-REP-003; "as
  recorded" = None), **3-point-bend outputs** (SW-REP-004, off by default; L, b, h prefilled from the session
  geometry) → [Build report] = ``reports.build_async(dir, cal=…, tare=…, bend3p=…)`` (CSV + JSON + HTML,
  SW-REP-001); the result paths are listed.
* [Open HTML] opens the report in the **system browser** (``QDesktopServices.openUrl``, GQ-13); [Open folder] /
  [Open CSV] likewise. :data:`OPEN_URL_HOOK` is a test seam.
* **Recordings folder** (OI-UM-05 c) — shown read-only (selectable text, no editing; changing the folder is a later
  decision) with [Open recordings folder] (``QDesktopServices``). The path is ``reports.root()`` (GRQ-B-31 b) via
  :func:`recordings_root_of` (local lookup only as a fallback).

The GUI computes nothing: statistics, flags and files are the backend's (P1).

Implements: SW-REP-001 (build CSV + JSON + HTML, open in the browser), SW-REP-002 (step result table, flags),
SW-REP-003 (re-apply calibration / tare), SW-REP-004 (3-point-bend option), SW-ACQ-002 (recordings list,
recordings folder shown read-only)
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bend_stand.core import api
from bend_stand.gui import seq_access as sa
from bend_stand.gui.dialogs.safe_dialog import get_open_file_name
from bend_stand.gui.format import fmt_value
from bend_stand.gui.units_state import force_unit

log = logging.getLogger(__name__)

#: Test seam: when set, URLs are handed to it instead of ``QDesktopServices.openUrl`` (never set by the app)
OPEN_URL_HOOK: list[Callable[[str], None] | None] = [None]

REC_COLUMNS = (("started", "Date / time"), ("specimen", "Specimen"), ("sequence", "Sequence"), ("status", "Status"),
               ("duration", "Duration"), ("has_report", "Report"), ("folder", "Folder"))
CAL_FILTER = "Load calibration (*.json);;All files (*)"
RED = QColor("#c00000")
RED_FLAGS = frozenset({"NOT_REACHED", "INCOMPLETE", "NOT_ON_TARGET", "BREAK_DETECTED", "TIMEOUT", "SLIP",
                       "WINDOW_DISCARDED", "TRIM_FAILED"})


def _err_text(exc: BaseException) -> str:
    return str(getattr(exc, "user_text", "") or exc or type(exc).__name__)


def default_recordings_root() -> Path:
    """Mirror of ``core.paths.default_recordings_root`` (the GUI may not import ``core.paths``, layering rule G-01);
    ``tests/gui/test_oi_um_05.py`` asserts both stay equal."""
    return Path.home() / "Documents" / "BirdBendStand" / "recordings"


def recordings_root_of(backend: Any) -> str:
    """Folder the backend writes recordings to — display only (OI-UM-05 c): B's public ``reports.root()``
    (GRQ-B-31 b, the folder ``record_start`` writes to). Fallback only when it is missing or fails (older backend,
    fakes without it): the settings override, the session value, the default ``Documents/BirdBendStand/recordings``
    (same precedence as the backend)."""
    root_fn = getattr(getattr(backend, "reports", None), "root", None)
    if callable(root_fn):
        try:
            r = root_fn()
            if r:
                return str(r)
        except Exception:  # noqa: BLE001 - display only
            log.debug("reports.root() failed", exc_info=True)
    r = getattr(getattr(backend, "settings", None), "recordings_root", None)
    if isinstance(r, (str, os.PathLike)) and str(r):
        return str(r)
    try:
        r = backend.session.get().recordings_root
    except Exception:  # noqa: BLE001 - display only
        r = None
    return str(r) if r else str(default_recordings_root())


def open_url(path_or_url: str) -> bool:
    hook = OPEN_URL_HOOK[0]
    if hook is not None:
        hook(path_or_url)
        return True
    url = QUrl(path_or_url) if "://" in path_or_url else QUrl.fromLocalFile(path_or_url)
    return QDesktopServices.openUrl(url)


class ReportTab(QWidget):
    message = Signal(str, str)

    def __init__(self, backend: Any, bridge: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("reportTab")
        self._backend = backend
        self._bridge = bridge
        self.recordings: list[dict[str, Any]] = []
        self.result: Any = None
        self.rows: list[dict[str, Any]] = []
        self.paths: dict[str, str] = {}
        self.building = False
        self._bend_prefilled = False
        self._build()
        if bridge is not None and hasattr(bridge, "reportReady"):
            bridge.reportReady.connect(self._on_report_ready)
        force_unit().changed.connect(self._on_unit)
        self._shown_once = False
        self.update_root()

    # ================================================================== construction
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Vertical, self)
        top = QGroupBox("Recordings", split)
        tl = QVBoxLayout(top)
        row = QHBoxLayout()
        self.root_label = QLabel("", top)
        row.addWidget(self.root_label, 1)
        self.refresh_button = QPushButton("Refresh", top)
        self.refresh_button.setObjectName("reportRefresh")
        self.refresh_button.clicked.connect(self.refresh_list)
        row.addWidget(self.refresh_button)
        tl.addLayout(row)
        row = QHBoxLayout()                                   # Implements: SW-ACQ-002 (folder shown, OI-UM-05 c)
        row.addWidget(QLabel("Recordings folder:", top))
        self.root_edit = QLineEdit(top)
        self.root_edit.setObjectName("recordingsRoot")
        self.root_edit.setReadOnly(True)
        self.root_edit.setToolTip("Where recordings and reports are written (read-only here)")
        row.addWidget(self.root_edit, 1)
        self.root_button = QPushButton("Open recordings folder", top)
        self.root_button.setObjectName("reportOpenRoot")
        self.root_button.setAutoDefault(False)
        self.root_button.clicked.connect(self.open_root)
        row.addWidget(self.root_button)
        tl.addLayout(row)
        self.rec_table = QTableWidget(0, len(REC_COLUMNS), top)
        self.rec_table.setObjectName("recordingsTable")
        self.rec_table.setHorizontalHeaderLabels([h for _k, h in REC_COLUMNS])
        self.rec_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rec_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rec_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rec_table.horizontalHeader().setSectionResizeMode(len(REC_COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.rec_table.itemSelectionChanged.connect(self._on_select)
        tl.addWidget(self.rec_table)
        split.addWidget(top)

        sel = QGroupBox("Selected recording", split)
        sl = QVBoxLayout(sel)
        self.sel_label = QLabel("(none)", sel)
        self.sel_label.setObjectName("reportSelected")
        self.sel_label.setWordWrap(True)
        sl.addWidget(self.sel_label)
        self.warn_label = QLabel("", sel)
        self.warn_label.setObjectName("reportWarnings")
        self.warn_label.setWordWrap(True)
        self.warn_label.setStyleSheet("color: #a05000;")
        sl.addWidget(self.warn_label)
        self.result_table = QTableWidget(0, len(sa.RESULT_COLUMNS), sel)
        self.result_table.setObjectName("stepResultTable")
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.horizontalHeader().setSectionResizeMode(len(sa.RESULT_COLUMNS) - 1,
                                                                  QHeaderView.ResizeMode.Stretch)
        sl.addWidget(self.result_table, 1)
        split.addWidget(sel)
        outer.addWidget(split, 1)
        outer.addWidget(self._build_options())

    def _build_options(self) -> QGroupBox:
        box = QGroupBox("Options", self)
        lay = QVBoxLayout(box)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Re-apply calibration:", box))
        self.cal_edit = QLineEdit(box)
        self.cal_edit.setObjectName("reportCalFile")
        self.cal_edit.setPlaceholderText("(as recorded)")
        r1.addWidget(self.cal_edit, 1)
        self.cal_button = QPushButton("Choose file…", box)
        self.cal_button.clicked.connect(self._choose_cal)
        r1.addWidget(self.cal_button)
        self.tare_box = QCheckBox("tare raw", box)
        self.tare_box.setObjectName("reportTareOn")
        r1.addWidget(self.tare_box)
        self.tare_spin = QDoubleSpinBox(box)
        self.tare_spin.setObjectName("reportTareRaw")
        self.tare_spin.setDecimals(1)
        self.tare_spin.setRange(-2**31, 2**31)
        self.tare_spin.setEnabled(False)
        self.tare_box.toggled.connect(self.tare_spin.setEnabled)
        r1.addWidget(self.tare_spin)
        lay.addLayout(r1)
        r2 = QHBoxLayout()
        self.bend_box = QCheckBox("3-point bend outputs", box)
        self.bend_box.setObjectName("reportBend3p")
        r2.addWidget(self.bend_box)
        self.bend_spins: dict[str, QDoubleSpinBox] = {}
        for f, lab in (("span_mm", "L"), ("width_mm", "b"), ("thickness_mm", "h")):
            r2.addWidget(QLabel(f"{lab} [mm]", box))
            sp = QDoubleSpinBox(box)
            sp.setObjectName(f"reportBend_{f}")
            sp.setDecimals(3)
            sp.setRange(0.0, 10_000.0)
            sp.setEnabled(False)
            self.bend_box.toggled.connect(sp.setEnabled)
            r2.addWidget(sp)
            self.bend_spins[f] = sp
        r2.addStretch(1)
        lay.addLayout(r2)
        r3 = QHBoxLayout()
        self.build_button = QPushButton("Build report", box)
        self.build_button.setObjectName("reportBuild")
        self.build_button.clicked.connect(self.build)
        r3.addWidget(self.build_button)
        self.html_button = QPushButton("Open HTML", box)
        self.html_button.setObjectName("reportOpenHtml")
        self.html_button.clicked.connect(lambda: self.open_file("html"))
        r3.addWidget(self.html_button)
        self.folder_button = QPushButton("Open folder", box)
        self.folder_button.setObjectName("reportOpenFolder")
        self.folder_button.clicked.connect(lambda: self.open_file("folder"))
        r3.addWidget(self.folder_button)
        self.csv_button = QPushButton("Open CSV", box)
        self.csv_button.setObjectName("reportOpenCsv")
        self.csv_button.clicked.connect(lambda: self.open_file("csv"))
        r3.addWidget(self.csv_button)
        self.paths_label = QLabel("", box)
        self.paths_label.setObjectName("reportPaths")
        self.paths_label.setWordWrap(True)
        r3.addWidget(self.paths_label, 1)
        lay.addLayout(r3)
        self._update_buttons()
        return box

    # ================================================================== list
    def showEvent(self, event: Any) -> None:  # noqa: N802 - first show lists the recordings
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            self.refresh_list()

    def update_root(self) -> str:
        root = recordings_root_of(self._backend)
        if self.root_edit.text() != root:
            self.root_edit.setText(root)
            self.root_edit.setCursorPosition(0)
        return root

    def open_root(self) -> None:
        """[Open recordings folder] → system file browser (``QDesktopServices``); never creates or changes it."""
        root = self.update_root()
        if not os.path.isdir(root):
            self.message.emit(f"Recordings folder {root} does not exist yet — it is created with the first recording",
                              "info")
            return
        if not open_url(root):
            self.message.emit(f"Could not open {root}", "warn")

    def refresh_list(self) -> None:
        self.update_root()
        keep = self.selected_folder()
        try:
            items = self._backend.reports.list_recordings(None)
        except Exception as exc:  # noqa: BLE001 - not available yet
            items = []
            self.root_label.setText(f"recordings unavailable: {_err_text(exc)}")
        else:
            self.root_label.setText(f"{len(items)} recording(s), newest first")
        self.recordings = sa.recording_rows(items)
        self.rec_table.blockSignals(True)
        self.rec_table.setRowCount(len(self.recordings))
        for r, rec in enumerate(self.recordings):
            for c, (k, _h) in enumerate(REC_COLUMNS):
                v = rec.get(k)
                if k == "duration":
                    v = sa.fmt_hms(v) if v is not None else ""
                elif k == "has_report":
                    v = "" if v is None else ("yes" if v else "no")
                elif k == "folder":
                    v = os.path.basename(str(v).rstrip("/\\")) or str(v)
                it = QTableWidgetItem("" if v is None else str(v))
                if k == "folder":
                    it.setToolTip(rec["folder"])
                if k == "status" and str(v).upper() in ("PARTIAL", "FAILED"):
                    it.setForeground(RED)
                self.rec_table.setItem(r, c, it)
        self.rec_table.blockSignals(False)
        if keep:
            self.select_folder(keep)
        self._update_buttons()

    def selected_folder(self) -> str | None:
        rows = {i.row() for i in self.rec_table.selectedIndexes()}
        if not rows:
            return None
        r = min(rows)
        return self.recordings[r]["folder"] if 0 <= r < len(self.recordings) else None

    def select_folder(self, folder: str) -> bool:
        for r, rec in enumerate(self.recordings):
            if rec["folder"] == folder:
                self.rec_table.selectRow(r)
                return True
        return False

    def _on_select(self) -> None:
        folder = self.selected_folder()
        self.paths = {}
        self.paths_label.setText("")
        if folder is None:
            self.result, self.rows = None, []
            self.sel_label.setText("(none)")
            self.warn_label.setText("")
            self._fill_results()
            self._update_buttons()
            return
        self.load_result(folder)

    def load_result(self, folder: str) -> None:
        try:
            self.result = self._backend.reports.load_result(folder)
        except Exception as exc:  # noqa: BLE001 - no report.json yet
            self.result = None
            self.sel_label.setText(f"{folder}\nno report yet: {_err_text(exc)} — [Build report] creates it")
        else:
            self.sel_label.setText(self._summary_text(folder, self.result))
        self.warn_label.setText("Warnings: " + "; ".join(sa.result_warnings(self.result))
                                if sa.result_warnings(self.result) else "")
        self.rows = sa.result_rows(self.result) if self.result is not None else []
        p = sa.report_paths(sa.get(self.result, "paths"))
        if p:
            self.paths = p
        self._fill_results()
        self._prefill_options()
        self._update_buttons()

    @staticmethod
    def _summary_text(folder: str, res: Any) -> str:
        if res is None:
            return f"{folder}\nno report yet — [Build report] creates it"
        parts = [folder]
        marks = sa.get(res, "marks")
        if marks is not None:
            if isinstance(marks, dict):
                parts.append("Marks: " + ", ".join(f"{k} {v}" for k, v in marks.items() if v not in (None, "")))
            else:
                parts.append(f"Marks: {marks}")
        cal = sa.get(res, "calibration", "cal")
        if cal is not None:
            st = sa.get(cal, "status")
            parts.append("Calibration: " + (sa.text_of(st) if st is not None else str(cal))
                         + (" LOW_SPAN (!)" if sa.get(cal, "low_span") else ""))
        tare = sa.get(res, "tare_raw", "tare")
        if tare is not None:
            parts.append(f"Tare {sa.get(tare, 'tare_raw', default=tare)}")
        st = sa.get(res, "status")
        if st is not None:
            parts.append(f"Status {sa.text_of(st)}")
        return "\n".join(parts)

    def _prefill_options(self) -> None:
        try:
            geo = getattr(self._backend.session.get(), "bend3p", None)
        except Exception:  # noqa: BLE001
            geo = None
        if geo is not None and not self._bend_prefilled:
            self._bend_prefilled = True
            self.bend_box.setChecked(True)      # session geometry set → outputs on (the operator may untick)
            for f, sp in self.bend_spins.items():
                v = getattr(geo, f, None)
                if v is not None:
                    sp.setValue(float(v))

    def _on_unit(self, _unit: str) -> None:
        self._fill_results()

    def _fill_results(self) -> None:
        unit = force_unit().unit
        heads = [h.replace("F ", f"F [{unit}] ") if h.startswith("F ") else h for _k, h in sa.RESULT_COLUMNS]
        self.result_table.setHorizontalHeaderLabels(heads)
        self.result_table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            red = bool(RED_FLAGS & set(row["flags"]))
            for c, (k, _h) in enumerate(sa.RESULT_COLUMNS):
                v = row.get(k)
                if k == "flags":
                    txt = ", ".join(v) if v else ""
                elif k.startswith("F.") or k == "target":
                    if k == "F.se" or k == "F.drift" or k == "F.std":
                        txt = "" if v is None else fmt_value(force_unit().from_n(float(v)), unit, 4)
                    else:
                        txt = "" if v is None else fmt_value(force_unit().from_n(float(v)), unit)
                elif k == "x.mean":
                    txt = "" if v is None else fmt_value(v, "mm")
                else:
                    txt = "" if v is None else str(v)
                it = QTableWidgetItem(txt)
                if k == "flags" and red:
                    it.setForeground(RED)
                self.result_table.setItem(r, c, it)

    # ================================================================== build / open
    def _choose_cal(self) -> None:
        path = get_open_file_name(self, "Calibration to re-apply", "", CAL_FILTER)
        if path:
            self.cal_edit.setText(path)

    def options(self) -> dict[str, Any]:
        cal = self.cal_edit.text().strip() or None
        tare = float(self.tare_spin.value()) if self.tare_box.isChecked() else None
        bend: Any = False                       # B6-14: False = 3-point-bend outputs off
        if self.bend_box.isChecked():
            vals = {f: sp.value() for f, sp in self.bend_spins.items()}
            geo_cls = getattr(api, "Bend3pGeometry", None)
            bend = geo_cls(**vals) if geo_cls is not None else vals
        return {"cal": cal, "tare": tare, "bend3p": bend}

    def build(self) -> None:
        folder = self.selected_folder()
        if folder is None:
            self.message.emit("Select a recording first", "info")
            return
        opts = self.options()
        try:
            fut = self._backend.reports.build_async(folder, cal=opts["cal"], tare=opts["tare"], bend3p=opts["bend3p"])
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Report build refused: {_err_text(exc)}", "warn")
            return
        self.building = True
        self._update_buttons()
        self.paths_label.setText("building…")
        self._bridge.watch(fut, lambda r, f=folder: self._on_built(f, r), self._on_build_failed, "report")

    def _on_built(self, folder: str, result: Any) -> None:
        self.building = False
        self.paths = sa.report_paths(result)
        self.paths_label.setText(" · ".join(f"{k}: {v}" for k, v in self.paths.items()))
        self.message.emit(f"Report built: {self.paths.get('html', folder)}", "info")
        self.refresh_list()
        if self.selected_folder() == folder:
            self.load_result(folder)
            self.paths = self.paths or sa.report_paths(result)
        self._update_buttons()

    def _on_build_failed(self, exc: BaseException) -> None:
        self.building = False
        self.paths_label.setText("")
        self.message.emit(f"Report build failed: {_err_text(exc)}", "error")
        self._update_buttons()

    def open_file(self, kind: str) -> None:
        p = self.paths.get(kind)
        if kind == "folder" and not p:
            p = self.selected_folder()
        if kind == "html" and not p and self.selected_folder():
            cand = os.path.join(self.selected_folder() or "", "report.html")
            p = cand if os.path.exists(cand) else None
        if kind == "csv" and not p and self.selected_folder():
            cand = os.path.join(self.selected_folder() or "", "data.csv")
            p = cand if os.path.exists(cand) else None
        if not p:
            self.message.emit(f"No {kind} for this recording — build the report first", "info")
            return
        if not open_url(p):
            self.message.emit(f"Could not open {p}", "warn")

    def _update_buttons(self) -> None:
        has = self.selected_folder() is not None
        self.build_button.setEnabled(has and not self.building)
        for b in (self.html_button, self.folder_button, self.csv_button):
            b.setEnabled(has)

    def _on_report_ready(self, _record: Any) -> None:
        self.refresh_list()

    def update_status(self, _st: Any) -> None:
        """Nothing polled per tick: the list follows ``report.ready`` and [Refresh]."""
