"""``EventLogDock``: FW EVENTs and backend events as a table (SW_design_GUI §4.4). Texts come from the generated
``EVENT_NAMES`` / ``EVENT_DESC`` / argument name tables (P8); an unknown code is shown as "EVENT <code>"
(ICD §0.2). Capped at 5 000 rows for the display (the backend log and the recording keep everything).

Implements: SAF-SW-005 (event context for indicators), SW-STOP-004 (resume.ignored rows), FW-CMD-003 (NACK texts
verbatim via ``user_text``)
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QWidget

from bend_stand.core import protocol_gen as pg
from bend_stand.gui.widgets.safe_dock import SafeDock

MAX_ROWS = 5000
COLUMNS = ("Time", "Source", "Code", "Text")


@dataclass(frozen=True)
class LogRow:
    wall: str
    source: str
    code: str
    text: str
    severity: str = "info"         # info / warn / error


def fw_event_text(ev: Any) -> tuple[str, str]:
    """(code name, text) of a decoded FW EVENT (``FwEvent``)."""
    code = int(getattr(ev, "code", -1))
    name = getattr(ev, "name", "") or (pg.EVENT_NAMES[code] if 0 <= code < len(pg.EVENT_NAMES) else "")
    if not name:
        return f"EVENT {code}", f"unknown event code {code}"
    arg_name = getattr(ev, "arg_name", None)
    arg = getattr(ev, "arg", 0)
    parts = [pg.EVENT_DESC.get(name, "")]
    if arg_name:
        parts.append(f"arg {arg_name}")
    elif arg:
        parts.append(f"arg {arg}")
    v, v2 = getattr(ev, "value", 0), getattr(ev, "value2", 0)
    if v or v2:
        parts.append(f"values {v} / {v2}")
    return name, " — ".join(p for p in parts if p)


def _payload_text(payload: Any) -> str:
    if payload is None:
        return ""
    for attr in ("user_text", "text", "why", "message"):
        v = getattr(payload, attr, None)
        if isinstance(v, str) and v:
            return v
    if hasattr(payload, "reason") and hasattr(payload, "source") and hasattr(payload.reason, "text"):
        return f"{payload.source}: {payload.reason.text()}"          # ResumeIgnored
    return str(payload)


def format_record(record: Any) -> LogRow:
    """Pure: one EventRecord → one table row."""
    topic = str(getattr(record, "topic", ""))
    t_ns = getattr(record, "t_host_ns", None)
    wall = time.strftime("%H:%M:%S") if not t_ns else time.strftime("%H:%M:%S", time.localtime(t_ns / 1e9))
    payload = getattr(record, "payload", None)
    if topic == "fw.event":
        code, text = fw_event_text(payload)
        return LogRow(wall, "FW", code, text, "info")
    if topic in ("stop.issued", "stop.confirmed", "stop.unconfirmed"):
        cmd = getattr(payload, "cmd", "STOP")
        sev = "error" if topic == "stop.unconfirmed" or getattr(payload, "sent", True) is False else "info"
        detail = "" if getattr(payload, "sent", True) else f" NOT SENT: {getattr(payload, 'reason', '')}"
        return LogRow(wall, getattr(payload, "source", "") or "PC", cmd, f"{topic}{detail}", sev)
    if topic == "resume.ignored":
        return LogRow(wall, "PC", "RESUME_IGNORED", _payload_text(payload), "warn")
    sev = "error" if topic == "rec.failure" or (topic == "safety.trip" and payload is not None) else "info"
    return LogRow(wall, topic.split(".")[0], topic, _payload_text(payload), sev)


class EventLogDock(SafeDock):
    """Bottom dock (hidden by default)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Event log", parent, source="dock:eventlog")
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.setWidget(self.table)
        self.rows: list[LogRow] = []

    def add_record(self, record: Any) -> None:
        self.add_row(format_record(record))

    def add_text(self, source: str, code: str, text: str, severity: str = "info") -> None:
        self.add_row(LogRow(time.strftime("%H:%M:%S"), source, code, text, severity))

    def add_row(self, row: LogRow) -> None:
        self.rows.append(row)
        if len(self.rows) > MAX_ROWS:
            del self.rows[0]
            self.table.removeRow(0)
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, v in enumerate((row.wall, row.source, row.code, row.text)):
            self.table.setItem(r, c, QTableWidgetItem(v))
        if self.isVisible():
            self.table.scrollToBottom()

    def texts(self) -> Sequence[str]:
        return [f"{r.code} {r.text}" for r in self.rows]
