"""``EndpointSelector``: endpoint combo from ``backend.endpoints()`` (ST-LINK COM ports first, other COM ports,
"sim", the FW host twin and the out-of-process simulator, A-22) + [Refresh]. Listing endpoints never opens a port
(D-06); only Connect does, and only on the operator's click.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/widgets/port_selector.py @37c87471 (adapted: EndpointInfo
entries from the backend, no direct port enumeration).

Implements: SW-PLT-003 (endpoint choice), SYS-008 (simulator / twin endpoints)
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QWidget

log = logging.getLogger(__name__)


class EndpointSelector(QWidget):
    endpointChanged = Signal(str)

    def __init__(self, backend: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox(self)
        self.combo.setMinimumWidth(280)
        self.combo.currentIndexChanged.connect(lambda _i: self.endpointChanged.emit(self.current_endpoint() or ""))
        lay.addWidget(self.combo, 1)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.clicked.connect(self.refresh)
        lay.addWidget(self.refresh_button)

    def refresh(self) -> None:
        current = self.current_endpoint()
        try:
            eps = list(self._backend.endpoints())
        except Exception:  # noqa: BLE001
            log.warning("endpoints() failed", exc_info=True)
            eps = []
        self.combo.blockSignals(True)
        try:
            self.combo.clear()
            for ep in eps:
                label = getattr(ep, "label", "") or ep.endpoint
                self.combo.addItem(label, ep.endpoint)
                self.combo.setItemData(self.combo.count() - 1,
                                       f"{ep.endpoint} ({getattr(ep, 'kind', '')}) {getattr(ep, 'description', '')}",
                                       Qt.ItemDataRole.ToolTipRole)
            if current:
                self.set_endpoint(current)
        finally:
            self.combo.blockSignals(False)
        self.endpointChanged.emit(self.current_endpoint() or "")

    def endpoints(self) -> list[str]:
        return [self.combo.itemData(i) for i in range(self.combo.count())]

    def current_endpoint(self) -> str | None:
        data = self.combo.currentData()
        return str(data) if data else None

    def set_endpoint(self, endpoint: str) -> None:
        """Select ``endpoint``; adds it when not listed (e.g. ``sim:<scenario>`` from the command line)."""
        idx = self.combo.findData(endpoint)
        if idx < 0:
            self.combo.addItem(endpoint, endpoint)
            idx = self.combo.count() - 1
        self.combo.setCurrentIndex(idx)
