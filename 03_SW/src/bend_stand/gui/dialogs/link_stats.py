"""``LinkStatsDialog``: PC and FW link counters (``status().link.stats``: frames, lost FW / link, duplicates,
sequence anomalies, CRC / length errors, timeouts, NACKs, FW counters from GET_STATUS), refreshed at 1 Hz.
Non-modal. DEGRADED comes from timeouts only; NACKs (e.g. BLOCK PAUSED) never degrade the link (B3-10).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/dialogs/link_stats.py @37c87471 (adapted: LinkStats fields,
generic dataclass table, SafeDialog base).

Implements: SW-PLT-003 (link statistics), FW-CMD-004 (FW counters shown)
"""
from __future__ import annotations

import dataclasses
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, \
    QWidget

from bend_stand.gui.dialogs.safe_dialog import SafeDialog


def stats_rows(stats: Any) -> list[tuple[str, str]]:
    if stats is None:
        return []
    if dataclasses.is_dataclass(stats):
        items = [(f.name, getattr(stats, f.name)) for f in dataclasses.fields(stats)]
    else:
        items = sorted(vars(stats).items())
    return [(k.replace("_", " "), "–" if v is None else str(v)) for k, v in items]


class LinkStatsDialog(SafeDialog):
    def __init__(self, backend: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="Link statistics")
        self.setModal(False)
        self._backend = backend
        lay = QVBoxLayout()
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Counter", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table)
        self.setLayout(lay)
        self.resize(420, 560)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        try:
            link = self._backend.status().link
        except Exception:  # noqa: BLE001
            return
        rows = [("state", str(getattr(link.state, "value", link.state))), ("endpoint", str(link.endpoint or "–"))]
        rows += stats_rows(link.stats)
        if self.table.rowCount() != len(rows):
            self.table.setRowCount(len(rows))
        for r, (k, v) in enumerate(rows):
            for c, text in enumerate((k, v)):
                it = self.table.item(r, c)
                if it is None:
                    self.table.setItem(r, c, QTableWidgetItem(text))
                elif it.text() != text:
                    it.setText(text)

    def done(self, r: int) -> None:  # noqa: D102
        self._timer.stop()
        super().done(r)
