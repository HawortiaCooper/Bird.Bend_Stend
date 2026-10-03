"""Validator E - reference decoder of the NVM record log (oracle for the twin power-cut tests).

Layout (FW_design v0.4 §9.6 item 12, ICD §11.3 "layout: FW design"): flash sectors 1 + 2 at
0x08004000 / 0x08008000 (16 KB each; the twin's flash.bin holds both, sector 1 first), 32 slots of
512 B per sector. Header 32 B: u32 magic 0x564E4442 ("BDNV"), u16 layout 1, u16 header size 32,
u32 seq, u16 entry count, u16 entry size 8, u32 PARAM_DICT_HASH, u16 dict_version, u16 fw_version,
u32 CRC-32 of the entries, u32 CRC-32 (zlib) of header bytes 0..27 (written last = commit marker).
Entry: u16 id, u8 type, u8 0, u32 raw. Blank = all 0xFF. Newest valid record = largest seq (modular).

Independent of the FW sources (written from the documentation, CRC from zlib).
Verifies: (oracle for) FW-NVM-002
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

MAGIC = 0x564E4442
SECTOR = 0x4000
SLOT = 512
SLOTS = SECTOR // SLOT


@dataclass
class Record:
    sector: int
    slot: int
    seq: int
    dict_hash: int
    dict_version: int
    fw_version: int
    entries: dict[int, tuple[int, int]]      # id -> (type, raw)


def slot_state(b: bytes) -> tuple[str, Record | None]:
    if all(x == 0xFF for x in b):
        return "blank", None
    magic, layout, hsz, seq, n, esz, h, dv, fv, ce, ch = struct.unpack_from("<IHHIHHIHHII", b, 0)
    if magic != MAGIC or layout != 1 or hsz != 32 or esz != 8 or n > (SLOT - 32) // 8:
        return "invalid", None
    if zlib.crc32(b[:28]) != ch or zlib.crc32(b[32:32 + 8 * n]) != ce:
        return "invalid", None
    ent = {}
    for i in range(n):
        eid, et, pad, raw = struct.unpack_from("<HBBI", b, 32 + 8 * i)
        ent[eid] = (et, raw)
    return "valid", Record(0, 0, seq, h, dv, fv, ent)


def scan(flash: bytes) -> tuple[list[tuple[int, int, str, Record | None]], Record | None]:
    slots, newest = [], None
    for s in range(2):
        for i in range(SLOTS):
            off = s * SECTOR + i * SLOT
            st, rec = slot_state(flash[off:off + SLOT])
            if rec is not None:
                rec.sector, rec.slot = s, i
                if newest is None or ((rec.seq - newest.seq) & 0xFFFFFFFF) not in (0,) and \
                        ((rec.seq - newest.seq) & 0xFFFFFFFF) < 0x80000000:
                    newest = rec
            slots.append((s, i, st, rec))
    return slots, newest


def read(path: Path) -> bytes:
    return Path(path).read_bytes()


def patch_hash(flash: bytes, rec: Record, new_hash: int) -> bytes:
    """Rewrite the dict hash of one record and its header CRC (simulates a record of another dict)."""
    b = bytearray(flash)
    off = rec.sector * SECTOR + rec.slot * SLOT
    struct.pack_into("<I", b, off + 16, new_hash)
    struct.pack_into("<I", b, off + 28, zlib.crc32(bytes(b[off:off + 28])))
    return bytes(b)
