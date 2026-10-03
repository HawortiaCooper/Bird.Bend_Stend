"""QSettings factory and GUI-only preference keys (SW_design_GUI §4.5): INI file
``%APPDATA%/BirdBendStand/gui.ini``; the environment variable ``BEND_STAND_GUI_SETTINGS`` overrides the path
(tests, portable use). Sessions (backend) never contain the GUI layout.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/settings.py @37c87471 (adapted: path, keys; no platformdirs).

Implements: SW-RT-001 (layout persistence store), SW-PLT-001 (per-user GUI settings file)
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings

APP_DIR = "BirdBendStand"
ENV_OVERRIDE = "BEND_STAND_GUI_SETTINGS"

KEY_GEOMETRY = "main/geometry"
KEY_STATE = "main/state"
KEY_LAST_TAB = "ui/last_tab"
KEY_LAST_ENDPOINT = "ui/last_endpoint"


def settings_path() -> Path:
    env = os.environ.get(ENV_OVERRIDE)
    if env:
        return Path(env)
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_DIR / "gui.ini"


def make_settings(path: str | Path | None = None) -> QSettings:
    p = Path(path) if path is not None else settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return QSettings(str(p), QSettings.Format.IniFormat)
