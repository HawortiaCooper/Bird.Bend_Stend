"""GUI resources shipped inside the package, so the dev run (``python -m bend_stand``) and the frozen build find them
next to this file (F-B-PKG-01).

* ``BirdBendStand.ico`` — application / window / taskbar icon. Origin: copy of ``03_SW/packaging/assets/
  BirdBendStand.ico`` (Implementer B, 2026-10-08); keep both identical (``tests/gui/test_app_icon.py`` compares them).
  PyInstaller: ``datas += [(<src>/bend_stand/gui/resources/BirdBendStand.ico, "bend_stand/gui/resources")]``.

Implements: SW-PLT-001 (application start: own window / taskbar icon)
"""
from __future__ import annotations

from pathlib import Path

RESOURCE_DIR = Path(__file__).resolve().parent
ICON_PATH = RESOURCE_DIR / "BirdBendStand.ico"


def app_icon():  # -> QIcon (Qt imported lazily: importing this package needs no Qt)
    """The application icon; a null ``QIcon`` when the file is missing (the caller then keeps Qt's default)."""
    from PySide6.QtGui import QIcon

    return QIcon(str(ICON_PATH)) if ICON_PATH.is_file() else QIcon()


__all__ = ["RESOURCE_DIR", "ICON_PATH", "app_icon"]
