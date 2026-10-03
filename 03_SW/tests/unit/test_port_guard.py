"""D-06 guard: no test can open a COM port.

Verifies: D-06 (no hardware access) — tagged with the SW requirement it protects (SW-PLT-003, connect)
"""
from __future__ import annotations

import pytest
import serial

from bend_stand.core.errors import HardwareAccessForbidden


@pytest.mark.req("SW-PLT-003")
def test_opening_a_com_port_raises() -> None:
    with pytest.raises(HardwareAccessForbidden):
        serial.Serial("COM7", 921600, timeout=0.002)


@pytest.mark.req("SW-PLT-003")
def test_serial_for_url_raises() -> None:
    with pytest.raises(HardwareAccessForbidden):
        serial.serial_for_url("COM7")


@pytest.mark.req("SW-PLT-003")
def test_unopened_serial_object_is_allowed() -> None:
    s = serial.Serial()          # no port → open() is not called
    assert not s.is_open
    s.port = "COM9"              # setting the port on a closed object does not open it
    with pytest.raises(HardwareAccessForbidden):
        s.open()
