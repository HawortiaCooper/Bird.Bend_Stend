"""Application data folders (SW_design §13): ``%APPDATA%\\BirdBendStand\\{calibration, sessions, presets, logs}``.

The root can be overridden by ``BackendSettings.data_dir`` or the environment variable ``BEND_STAND_DATA_DIR``
(the test suites point it at a temporary folder per test, so no test touches the operator's files). Folders are
created only when a file is written.

Implements: SW-CAL-009 (calibration folder), SW-LIM-003 (session folder), SW-META-002 (presets folder)
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_ENV = "BEND_STAND_DATA_DIR"
APP_NAME = "BirdBendStand"


def app_data_dir(override: str | os.PathLike[str] | None = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get(DATA_ENV, "").strip()
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA", "").strip()
    return Path(appdata) / APP_NAME if appdata else Path.home() / f".{APP_NAME.lower()}"


def calibration_dir(base: Path) -> Path:
    return base / "calibration"


def sessions_dir(base: Path) -> Path:
    return base / "sessions"


def presets_dir(base: Path) -> Path:
    return base / "presets"


def default_recordings_root() -> Path:
    return Path.home() / "Documents" / APP_NAME / "recordings"


__all__ = ["DATA_ENV", "app_data_dir", "calibration_dir", "sessions_dir", "presets_dir", "default_recordings_root"]
