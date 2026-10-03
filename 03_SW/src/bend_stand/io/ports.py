"""COM port listing / ST-Link VCP auto-detection (SW_design §2). Lists ports only — never opens one.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/ports.py @37c87471 (as-is; SIMULATOR constant dropped).

Implements: SW-PLT-001 (Windows COM ports), IF-002 (ST-LINK/V2-1 VCP), D-06 (enumeration only)
"""
from __future__ import annotations

from dataclasses import dataclass

STLINK_VID = 0x0483
STLINK_PIDS = (0x374B, 0x374E)


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None
    serial: str | None

    @property
    def is_stlink(self) -> bool:
        return self.vid == STLINK_VID and self.pid in STLINK_PIDS


def list_ports() -> list[PortInfo]:
    """All serial ports, ST-Link VCPs first (pyserial ``list_ports``; no port is opened)."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:  # pragma: no cover
        return []
    out = [PortInfo(p.device, p.description or "", p.vid, p.pid, p.serial_number) for p in lp.comports()]
    return sorted(out, key=lambda p: (not p.is_stlink, p.device))


def auto_detect() -> str | None:
    """Device name of the first ST-Link VCP, or None."""
    for p in list_ports():
        if p.is_stlink:
            return p.device
    return None
