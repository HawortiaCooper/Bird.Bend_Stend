"""PyInstaller runtime hook of BirdBendStand-GateRunner.exe (D-06 test guard, same rule as the application's hook).

If the environment variable ``BEND_STAND_D06_GUARD`` is set (not ``0``), ``serial.Serial.open`` and
``serial.serial_for_url`` raise before the OS is asked for a port — so smoke and refusal runs of the frozen runner can
never open a COM port, even if an interlock regressed. Without the variable nothing changes: the runner opens a port
only through ``hil_link.open_serial()`` after its own approval-file interlock (D-57 b). Writes nothing.

Implements: D-06 (test guard for the frozen gate runner)
"""
from __future__ import annotations

import os

GUARD_ENV = "BEND_STAND_D06_GUARD"


class PortForbidden(RuntimeError):
    pass


def _install() -> None:
    if os.environ.get(GUARD_ENV, "").strip() in ("", "0"):
        return
    import serial  # noqa: PLC0415

    def _forbidden(*_a: object, **_k: object) -> None:
        raise PortForbidden(f"D-06: opening a hardware port is forbidden in this run ({GUARD_ENV} is set)")

    serial.Serial.open = _forbidden            # type: ignore[method-assign]
    serial.serial_for_url = _forbidden         # type: ignore[assignment]


_install()
