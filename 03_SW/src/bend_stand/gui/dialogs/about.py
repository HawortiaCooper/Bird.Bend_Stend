"""``AboutDialog``: SW, FW, protocol, payload, dictionary hash and ICD versions (SW_design_GUI §2.5).

Implements: FW-CFG-004 (device versions shown), IF-008 (versions visible)
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

import bend_stand
from bend_stand.core import protocol_gen as pg
from bend_stand.gui.dialogs.safe_dialog import SafeDialog
from bend_stand.gui.format import fmt_version


def about_text(status: Any) -> str:
    info = getattr(getattr(status, "link", None), "info", None)
    lines = [f"Bird Bend Stand SW {bend_stand.__version__}",
             f"ICD {pg.ICD_VERSION}, protocol {pg.PROTO_MAJOR}.{pg.PROTO_MINOR}, payload {pg.PAYLOAD_VERSION}"]
    if info is None:
        lines.append("Board: not connected")
    else:
        lines += [f"Board FW {fmt_version(info.fw_version)} (build {info.build})",
                  f"Board protocol {info.proto_major}.{info.proto_minor}, payload {info.payload_version}",
                  f"Board dictionary hash 0x{info.param_dict_hash:08X}, {info.param_count} parameters",
                  f"UID {info.uid}", "Features: " + ", ".join(sorted(info.features))]
    return "\n".join(lines)


class AboutDialog(SafeDialog):
    def __init__(self, status: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="About Bird Bend Stand")
        self.setModal(False)
        lay = QVBoxLayout()
        self.label = QLabel(about_text(status), self)
        lay.addWidget(self.label)
        close = QPushButton("Close", self)
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        lay.addWidget(close)
        self.setLayout(lay)
