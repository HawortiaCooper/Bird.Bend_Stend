"""``FileReportDialog``: report of a loaded board-configuration file (``BoardConfigFile``: unknown keys, missing
keys, out-of-range entries, hash mismatch, issues) or of a ``FileFormatError`` (SW-CFG-002). Non-modal.

Implements: SW-CFG-002 (recall reports unknown, missing and out-of-range entries and the hash mismatch)
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from bend_stand.gui.dialogs.safe_dialog import SafeDialog


def report_lines(cfg: Any, skipped: list[str] | None = None) -> list[str]:
    """Pure: the report text lines for a ``BoardConfigFile``."""
    lines = [f"File: {getattr(cfg, 'path', '')}",
             f"{len(getattr(cfg, 'values', {}) or {})} value(s) put into the Edit column (not written yet)."]
    if getattr(cfg, "hash_mismatch", False):
        fh = getattr(cfg, "file_hash", None)
        lines.append("Dictionary hash mismatch" + (f": file 0x{fh:08X}" if fh is not None else "")
                     + " – values loaded by key; check them before writing.")
    for title, attr in (("Unknown keys (ignored)", "unknown_keys"), ("Missing keys (kept)", "missing_keys"),
                        ("Out-of-range entries (ignored)", "out_of_range")):
        keys = tuple(getattr(cfg, attr, ()) or ())
        if keys:
            lines.append(f"{title}: {len(keys)}")
            lines += [f"  {k}" for k in keys]
    if skipped:
        lines.append(f"Not applied (session value or not editable): {len(skipped)}")
        lines += [f"  {k}" for k in skipped]
    for i in getattr(cfg, "issues", ()) or ():
        lines.append(f"{getattr(i.severity, 'value', i.severity)} {i.key or ''} {i.code}: {i.text}")
    return lines


class FileReportDialog(SafeDialog):
    def __init__(self, cfg: Any = None, parent: QWidget | None = None, *, error: str | None = None,
                 skipped: list[str] | None = None) -> None:
        super().__init__(parent, title="Board configuration file")
        self.setModal(False)
        lay = QVBoxLayout()
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        if error is not None:
            self.text.setPlainText(f"File not loaded: {error}\nThe Edit column is unchanged.")
        else:
            self.text.setPlainText("\n".join(report_lines(cfg, skipped)))
        lay.addWidget(self.text)
        close = QPushButton("Close", self)
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        lay.addWidget(close)
        self.setLayout(lay)
        self.resize(560, 380)
