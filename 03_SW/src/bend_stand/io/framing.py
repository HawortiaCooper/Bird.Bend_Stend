"""Frame layer (ICD §2): encoder and the normative receiver state machine of ICD §2.3.

Frame = ``A5 5A | TYPE | SEQ | LEN u16 (0..160) | PAYLOAD | CRC u16`` (CRC-16/CCITT-FALSE over TYPE..PAYLOAD,
low byte first). No byte stuffing; framing is recovered by sync + LEN + CRC.

Receiver (``FrameDecoder``), both directions (the FW simulator uses the same class):
1. hunt ``A5 5A`` (a trailing lone ``A5`` is kept);
2. header complete: ``LEN > 160`` → ``len_errors += 1``, discard one byte (the SYNC0), hunt again;
3. frame complete: CRC ok → deliver, remove ``8 + LEN`` bytes; mismatch → ``crc_errors += 1``, discard one byte;
4. inter-byte timeout 20 ms: an incomplete candidate with no new byte for ≥ 20 ms → ``timeout_drops += 1``,
   discard one byte and re-scan the buffered bytes (repeated for every further incomplete candidate); a lone
   trailing ``A5`` is discarded without counting. The SW evaluates the timeout only after an empty read.
Output (frames and counters) equals ``00_System/tools/ref_codec.FrameParser`` on all vector streams.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/framing.py @37c87471 (adapted: MAX_LEN 160, buffer ≥ 336 B,
constants from protocol_gen, TYPE classification helpers for ICD §3.1).

Implements: IF-003, IF-004, IF-012 (TYPE ranges)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from bend_stand.core import protocol_gen as pg
from bend_stand.io.crc import crc16_ccitt_false

SYNC0 = pg.SYNC0
SYNC1 = pg.SYNC1
SYNC = bytes((SYNC0, SYNC1))
HEADER_LEN = pg.HEADER_LEN
OVERHEAD = pg.FRAME_OVERHEAD
MAX_LEN = pg.MAX_LEN
INTER_BYTE_TIMEOUT_NS = pg.INTERBYTE_TIMEOUT_MS * 1_000_000
MIN_BUFFER = pg.RX_BUF_MIN

_LEN = struct.Struct("<H")


def encode_frame(ftype: int, seq: int, payload: bytes | bytearray = b"") -> bytes:
    """Build one complete frame. Raises ``ValueError`` for TYPE/SEQ outside 0..255 or LEN > 160."""
    if not 0 <= ftype <= 0xFF or not 0 <= seq <= 0xFF:
        raise ValueError(f"TYPE/SEQ out of range: {ftype}, {seq}")
    n = len(payload)
    if n > MAX_LEN:
        raise ValueError(f"payload too long: {n} > {MAX_LEN}")
    body = bytes((ftype, seq)) + _LEN.pack(n) + bytes(payload)
    return SYNC + body + _LEN.pack(crc16_ccitt_false(body))


class FrameEncoder:
    """Stateless encoder. ``encode(type, seq, payload) -> bytes``."""

    @staticmethod
    def encode(ftype: int, seq: int, payload: bytes | bytearray = b"") -> bytes:
        return encode_frame(ftype, seq, payload)


# ---- TYPE classes (ICD §3.1) ------------------------------------------------------------------------

def is_command_type(t: int) -> bool:
    """PC → FW command range ``0x01..0x3F``."""
    return 0x01 <= t <= 0x3F


def is_response_type(t: int) -> bool:
    return 0x81 <= t <= 0xBF


def is_async_type(t: int) -> bool:
    return t in (pg.AsyncType.DATA, pg.AsyncType.EVENT)


def is_reserved_async_type(t: int) -> bool:
    """``0xC2..0xCF``: reserved asynchronous frames — ignored without counting an error."""
    return 0xC2 <= t <= 0xCF


def fw_to_pc_type_class(t: int) -> str:
    """``response`` | ``async`` | ``reserved`` | ``invalid`` for a frame received by the PC."""
    if is_response_type(t):
        return "response"
    if is_async_type(t):
        return "async"
    if is_reserved_async_type(t):
        return "reserved"
    return "invalid"


@dataclass(frozen=True, slots=True)
class Frame:
    """A CRC-valid frame. ``t_ns`` = timestamp of the chunk that completed it (host monotonic ns)."""

    type: int
    seq: int
    payload: bytes
    t_ns: int = 0

    @property
    def raw(self) -> bytes:
        return encode_frame(self.type, self.seq, self.payload)


@dataclass
class DecoderCounters:
    frames_ok: int = 0
    crc_errors: int = 0
    len_errors: int = 0
    timeout_drops: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"frames_ok": self.frames_ok, "crc_errors": self.crc_errors,
                "len_errors": self.len_errors, "timeout_drops": self.timeout_drops}


@dataclass
class FrameDecoder:
    """ICD §2.3 receiver. ``feed(data, t_ns)`` returns the frames completed by ``data``.

    Timestamps ``t_ns`` (monotonic ns) drive the 20 ms inter-byte timeout: call ``poll(now)`` while the line is
    idle (after an empty read). If ``t_ns`` is ``None`` the timeout is never applied (pure framing tests);
    ``idle_timeout()`` forces it.
    """

    timeout_ns: int = INTER_BYTE_TIMEOUT_NS
    buf: bytearray = field(default_factory=bytearray)
    counters: DecoderCounters = field(default_factory=DecoderCounters)
    last_rx_ns: int | None = None
    max_buffered: int = 0                   # high-water mark (diagnostics / fuzz test)

    def feed(self, data: bytes | bytearray | memoryview, t_ns: int | None = None, *,
             check_timeout: bool = True) -> list[Frame]:
        """``check_timeout=False``: ``t_ns`` only stamps the bytes (a reader that learns of bytes late cannot tell
        how long the line was idle before them, TS SWD-M2-01); the timeout is then applied by ``poll()`` after an
        empty read, which proves line silence since the previous read."""
        out: list[Frame] = []
        if (check_timeout and t_ns is not None and self.buf and self.last_rx_ns is not None
                and t_ns - self.last_rx_ns >= self.timeout_ns):
            out += self._timeout(self.last_rx_ns)
        if data:
            self.buf += data
            if t_ns is not None:
                self.last_rx_ns = t_ns
            if len(self.buf) > self.max_buffered:
                self.max_buffered = len(self.buf)
            out += self._scan(t_ns if t_ns is not None else 0)
        return out

    def poll(self, t_ns: int) -> list[Frame]:
        """Apply the inter-byte timeout at time ``t_ns`` without new data."""
        return self.feed(b"", t_ns)

    def idle_timeout(self, t_ns: int = 0) -> list[Frame]:
        """Force the ≥ 20 ms inter-byte timeout now (equivalent of ``ref_codec.FrameParser.idle_timeout``)."""
        return self._timeout(t_ns)

    def reset(self) -> None:
        self.buf.clear()
        self.last_rx_ns = None

    @property
    def buffered(self) -> int:
        return len(self.buf)

    def _timeout(self, t_ns: int) -> list[Frame]:
        out: list[Frame] = []
        buf = self.buf
        while True:
            out += self._scan(t_ns)
            i = buf.find(SYNC)
            if i < 0:
                buf.clear()                 # a trailing lone SYNC0 cannot become a frame any more
                return out
            self.counters.timeout_drops += 1
            del buf[: i + 1]                # drop the incomplete candidate's SYNC0, re-scan the rest

    def _scan(self, t_ns: int) -> list[Frame]:
        out: list[Frame] = []
        buf = self.buf
        c = self.counters
        while True:
            i = buf.find(SYNC)
            if i < 0:
                keep = 1 if buf[-1:] == b"\xa5" else 0
                del buf[: len(buf) - keep]
                return out
            if i:
                del buf[:i]
            if len(buf) < HEADER_LEN:
                return out
            length = buf[4] | (buf[5] << 8)
            if length > MAX_LEN:
                c.len_errors += 1
                del buf[:1]
                continue
            end = OVERHEAD + length
            if len(buf) < end:
                return out
            rx_crc = buf[end - 2] | (buf[end - 1] << 8)
            if crc16_ccitt_false(buf[2:end - 2]) != rx_crc:
                c.crc_errors += 1
                del buf[:1]
                continue
            out.append(Frame(buf[2], buf[3], bytes(buf[HEADER_LEN:end - 2]), t_ns))
            c.frames_ok += 1
            del buf[:end]
