"""CRC-16/CCITT-FALSE (ICD §2.1).

poly 0x1021, init 0xFFFF, no input/output reflection, xorout 0x0000; check value "123456789" → 0x29B1,
empty input → 0xFFFF. ``binascii.crc_hqx`` implements exactly this non-reflected CRC-CCITT with a caller
supplied initial value (C speed); ``crc16_ccitt_false_py`` is the literal bitwise algorithm of the ICD, kept for
documentation and cross-checking.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/crc.py @37c87471 (as-is; constants from protocol_gen).

Implements: IF-004
"""
from __future__ import annotations

import binascii


from bend_stand.core.protocol_gen import CRC_INIT, CRC_POLY


def crc16_ccitt_false(data: bytes | bytearray | memoryview, crc: int = CRC_INIT) -> int:
    """CRC over ``data`` continuing from ``crc`` (default init 0xFFFF). Chunked use: pass the previous result."""
    return binascii.crc_hqx(data, crc & 0xFFFF)


def crc16_ccitt_false_py(data: bytes | bytearray | memoryview, crc: int = CRC_INIT) -> int:
    """Bitwise reference form of ICD §2.1 (slow; tests only)."""
    crc &= 0xFFFF
    for b in bytes(data):
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc
