"""``StatusHelpDialog``: meaning, source, value and clear procedure of every item of one chip (or of all chips)
(SW_design_GUI §2.4 "Help"). Texts are the generated ``*_DESC`` and the backend's ``clear_hint`` (P8).

Implements: SAF-SW-005 (clear procedure for each latched condition)
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from bend_stand.gui import indicator_map as imap
from bend_stand.gui.dialogs.safe_dialog import SafeDialog


def help_text(status: Any, chip: str | None = None) -> str:
    views = imap.chip_by_key(imap.evaluate_chips(status))
    keys = [chip] if chip else list(imap.CHIP_IDS)
    blocks = []
    for k in keys:
        v = views.get(k)
        if v is None:
            continue
        blocks.append(f"{v.label}  [{v.level}]  {v.text}\n{v.tooltip}")
    if chip is None:
        blocks.append("Items without a chip:\n" + "\n".join(
            f"{n}: {e.reason}" for n, e in imap.NAME_TABLE.items() if not e.chips))
    return "\n\n".join(blocks)


class StatusHelpDialog(SafeDialog):
    def __init__(self, status: Any, chip: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="Status indicators & clear procedures")
        self.setModal(False)
        lay = QVBoxLayout()
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setPlainText(help_text(status, chip))
        lay.addWidget(self.text)
        close = QPushButton("Close", self)
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        lay.addWidget(close)
        self.setLayout(lay)
        self.resize(640, 480)
