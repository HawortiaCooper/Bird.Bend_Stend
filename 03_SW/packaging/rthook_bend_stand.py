"""PyInstaller runtime hook of the frozen Bird Bend Stand application (runs before ``bend_stand.__main__``).

1. **Windowed stdio file.** ``BirdBendStand.exe`` is a GUI-subsystem executable: without a console ``sys.stdout`` /
   ``sys.stderr`` are ``None``, so prints, tracebacks and hard crashes would be lost. They are redirected to
   ``<data>\\logs\\BirdBendStand.log`` (``<data>`` = ``BEND_STAND_DATA_DIR`` or ``%APPDATA%\\BirdBendStand``, the same
   root as ``bend_stand.core.paths``); the previous file is kept as ``BirdBendStand.log.1`` once it exceeds 2 MiB.
   ``faulthandler`` writes hard crashes to the same file. With a console (``BirdBendStand-cli.exe``) or redirected
   handles nothing changes. The application log proper (``logging``, rotating ``bend_stand.log``, incidents) is set
   up by ``bend_stand.core.logfile`` for every run; the hook marks the redirect (``sys._bend_stand_stdio_log``) so
   that set-up adds no console handler on top of it — each record is written once (SWR-14, OI-F-RV-04).
2. **D-06 guard for test runs of the frozen exe.** If the environment variable ``BEND_STAND_D06_GUARD`` is set (not
   ``0``), ``serial.Serial.open`` and ``serial.serial_for_url`` raise ``HardwareAccessForbidden`` — the same guard as
   ``03_SW/tests/conftest.py`` — so smoke runs of the built exe can never open a COM port. Without the variable the
   application opens exactly the port the operator selected (D-06).

Implements: SW-PLT-001 (frozen application start-up), D-06 (test guard for the frozen exe)
"""
from __future__ import annotations

import os
import sys

LOG_NAME = "BirdBendStand.log"
LOG_MAX_BYTES = 2 * 1024 * 1024
GUARD_ENV = "BEND_STAND_D06_GUARD"


def _data_dir() -> str:
    env = os.environ.get("BEND_STAND_DATA_DIR", "").strip()
    if env:
        return env
    appdata = os.environ.get("APPDATA", "").strip()
    return os.path.join(appdata, "BirdBendStand") if appdata else os.path.join(os.path.expanduser("~"),
                                                                               ".birdbendstand")


def _redirect_windowed_output() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        logs = os.path.join(_data_dir(), "logs")
        os.makedirs(logs, exist_ok=True)
        path = os.path.join(logs, LOG_NAME)
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        f = open(path, "a", encoding="utf-8", errors="backslashreplace", buffering=1)  # noqa: SIM115
    except OSError:
        return                                       # read-only profile: keep running without a log file
    import datetime  # noqa: PLC0415
    import faulthandler  # noqa: PLC0415

    f.write(f"\n===== start {datetime.datetime.now().isoformat(timespec='seconds')} "
            f"pid {os.getpid()} argv {sys.argv[1:]}\n")
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f
        # bend_stand.core.logfile: no console handler on top of this redirect — logging records go to the rotating
        # bend_stand.log only, never twice (SWR-14, OI-F-RV-04)
        sys._bend_stand_stdio_log = path  # type: ignore[attr-defined]
    try:
        faulthandler.enable(f)
    except (OSError, ValueError, RuntimeError):
        pass


def _install_d06_guard() -> None:
    if os.environ.get(GUARD_ENV, "").strip() in ("", "0"):
        return
    import serial  # noqa: PLC0415

    from bend_stand.core.errors import HardwareAccessForbidden  # noqa: PLC0415

    def _forbidden(*_a: object, **_k: object) -> None:
        raise HardwareAccessForbidden("D-06: opening a hardware port is forbidden in this run "
                                      f"({GUARD_ENV} is set)")

    serial.Serial.open = _forbidden            # type: ignore[method-assign]
    serial.serial_for_url = _forbidden         # type: ignore[assignment]


_redirect_windowed_output()
_install_d06_guard()
