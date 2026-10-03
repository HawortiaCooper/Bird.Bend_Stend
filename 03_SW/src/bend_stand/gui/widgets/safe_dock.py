"""``SafeDock``: QDockWidget whose title bar always carries [NO-SPECIMEN tag] [Float] [Close] [STOP]
(SW_design_GUI §4.1). The title bar stays when the dock floats, so every floating window has its own STOP.

Implements: SW-RT-001 (dock / float / re-attach, STOP per window), SW-STOP-001, SW-LIM-004 (tag)
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QLabel, QToolButton, QWidget

from bend_stand.gui.dialogs.safe_dialog import NoSpecimenTag
from bend_stand.gui.widgets.stop_button import StopButton


class _TitleBar(QWidget):
    def __init__(self, dock: "SafeDock", title: str, source: str) -> None:
        super().__init__(dock)
        self._dock = dock
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 1, 2, 1)
        lay.setSpacing(4)
        self.title_label = QLabel(title, self)
        self.title_label.setStyleSheet("font-weight: bold;")
        lay.addWidget(self.title_label)
        self.info_label = QLabel("", self)
        self.info_label.setStyleSheet("color: #606060;")
        lay.addWidget(self.info_label, 1)
        self.nospec_tag = NoSpecimenTag(self)
        lay.addWidget(self.nospec_tag)
        self.float_button = QToolButton(self)
        self.float_button.setText("Float")
        self.float_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.float_button.setToolTip("Float / re-attach this window (double click on the title also works)")
        self.float_button.clicked.connect(dock.toggle_floating)
        lay.addWidget(self.float_button)
        self.close_button = QToolButton(self)
        self.close_button.setText("Close")
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.setToolTip("Hide this window (reopen from the View menu)")
        self.close_button.clicked.connect(dock.close)
        lay.addWidget(self.close_button)
        self.stop_button = StopButton(self, source=source)
        lay.addWidget(self.stop_button)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._dock.toggle_floating()
        event.accept()


class SafeDock(QDockWidget):
    """Movable, floatable, closable dock with the safety title bar. Closing hides (the dock is not destroyed)."""

    def __init__(self, title: str, parent: QWidget | None = None, *, source: str | None = None) -> None:
        super().__init__(title, parent)
        self.setObjectName(f"dock_{title.replace(' ', '_').lower()}")
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                         | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.title_bar = _TitleBar(self, title, source or f"dock:{title}")
        self.setTitleBarWidget(self.title_bar)
        self.topLevelChanged.connect(self._on_floating)

    @property
    def stop_button(self) -> StopButton:
        return self.title_bar.stop_button

    def toggle_floating(self) -> None:
        self.setFloating(not self.isFloating())

    def _on_floating(self, floating: bool) -> None:
        self.title_bar.float_button.setText("Dock" if floating else "Float")

    def set_info(self, text: str) -> None:
        if self.title_bar.info_label.text() != text:
            self.title_bar.info_label.setText(text)
