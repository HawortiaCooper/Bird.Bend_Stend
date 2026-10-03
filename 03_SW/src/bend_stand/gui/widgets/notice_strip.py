"""``NoticeStrip``: one persistent row per non-stop notice (SW_design_GUI §2.8): protocol read-only, dictionary
hash mismatch, NVM defaulted, reboot pending, travel-calibration difference. Rows are rebuilt only when the set
of notices changes.

Implements: IF-008 (read-only notices), SW-CFG-003 (reboot pending → Save & reboot), SW-CFG-004 (NVM defaulted),
SW-CAL-001 (travel-calibration difference, M3 buttons from ``travel_diff.actions``)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from bend_stand.gui.theme import BANNER_STYLE


@dataclass(frozen=True)
class Notice:
    key: str
    text: str
    actions: tuple[tuple[str, str], ...]     # (action id, button label)
    style: str = "warn"


TRAVEL_ACTION_LABELS = {"restore": "Restore", "keep_board": "Keep board value",
                        "ignore_session": "Ignore for this session"}


def notices(status: Any) -> list[Notice]:
    """Pure: notices for one status snapshot."""
    out: list[Notice] = []
    link = getattr(status, "link", None)
    if getattr(link, "read_only", False):
        out.append(Notice("compat", "Read-only: protocol/payload version mismatch – motion disabled.",
                          (("about", "About"),), "alarm"))
    elif getattr(status, "config_read_only", False):
        out.append(Notice("config_ro", "Dictionary hash mismatch – configuration read-only.",
                          (("connection", "Connection tab"),)))
    if getattr(status, "nvm_defaulted", None):
        out.append(Notice("nvm_defaulted", "Board runs on default parameters (no valid NVM record) – check and "
                                           "Save to NVM.", (("connection", "Config tab"),)))
    if getattr(status, "reboot_pending", None):
        out.append(Notice("reboot_pending", "Reboot-required parameter changed – effective after Save to NVM + "
                                            "Reboot.", (("save_reboot", "Save & reboot…"),)))
    cal = getattr(status, "calibration", None)
    if getattr(cal, "travel_cal_differs", False):
        diff = getattr(cal, "travel_diff", None)
        board = getattr(diff, "board_spm", None) or getattr(cal, "board_spm", None)
        ref = getattr(diff, "session_spm", None) or getattr(cal, "travel_spm", None)
        text = "Board steps/mm " + (f"{board:.3f}" if board is not None else "?") + \
               " differs from the active travel calibration " + (f"{ref:.3f}" if ref is not None else "?") + "."
        if getattr(diff, "restore_pending", False) or getattr(cal, "restore_pending", False):
            text += " Restoring after an aborted calibration…"
        acts = tuple((f"travel:{a}", TRAVEL_ACTION_LABELS.get(a, a)) for a in getattr(diff, "actions", ()) or ())
        out.append(Notice("travel_cal", text, acts))
    return out


class NoticeStrip(QWidget):
    actionRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("noticeStrip")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(1)
        self._current: tuple[Notice, ...] = ()
        self._frames: list[QFrame] = []
        self.hide()

    def update_status(self, status: Any) -> None:
        new = tuple(notices(status))
        if new == self._current:
            return
        self._current = new
        for f in self._frames:
            f.setParent(None)
            f.deleteLater()
        self._frames = []
        for n in new:
            frame = QFrame(self)
            frame.setObjectName(f"notice_{n.key}")
            frame.setStyleSheet(f"QFrame#notice_{n.key} {{{BANNER_STYLE.get(n.style, BANNER_STYLE['warn'])}}}")
            row = QHBoxLayout(frame)
            row.setContentsMargins(6, 2, 6, 2)
            label = QLabel(n.text, frame)
            label.setWordWrap(True)
            row.addWidget(label, 1)
            for act, text in n.actions:
                b = QPushButton(text, frame)
                b.setAutoDefault(False)
                b.clicked.connect(lambda _c=False, a=act: self.actionRequested.emit(a))
                row.addWidget(b)
            self._lay.addWidget(frame)
            self._frames.append(frame)
        self.setVisible(bool(new))

    def keys(self) -> list[str]:
        return [n.key for n in self._current]
