"""Generate the application icon ``assets/BirdBendStand.ico`` (stdlib only, deterministic).

A three-point bend pictogram: two supports, a deflected beam and the loading arrow on a rounded dark-blue square.
Rendered once at 256 px with 4×4 supersampling, box-downsampled to 16…128 px (24 px rendered directly); every
size stored as a PNG entry (Windows Vista+ ICO). The generated ``.ico`` is committed; re-run only when the design
changes::

    .venv\\Scripts\\python 03_SW\\packaging\\make_icon.py [--out path.ico] [--png path.png]

Implements: SW-PLT-001 (installable Windows application: executable / installer icon)
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)
BASE = 256
SS = 4                                   # supersampling per axis
DEFAULT_OUT = Path(__file__).resolve().parent / "assets" / "BirdBendStand.ico"

BG = (31, 78, 121)
SUPPORT = (205, 214, 224)
BEAM = (255, 255, 255)
ARROW = (226, 60, 50)

RGBA = tuple[int, int, int, int]


def _in_round_rect(x: float, y: float, m: float = 0.03, r: float = 0.18) -> bool:
    lo, hi = m, 1.0 - m
    if not (lo <= x <= hi and lo <= y <= hi):
        return False
    cx = min(max(x, lo + r), hi - r)
    cy = min(max(y, lo + r), hi - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _in_triangle(x: float, y: float, a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    def s(p: tuple[float, float], q: tuple[float, float]) -> float:
        return (x - q[0]) * (p[1] - q[1]) - (p[0] - q[0]) * (y - q[1])
    d1, d2, d3 = s(a, b), s(b, c), s(c, a)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def _beam_y(x: float) -> float:
    return 0.58 + 0.10 * math.cos(math.pi * (x - 0.5) / 0.80)


def shade(x: float, y: float) -> tuple[int, int, int] | None:
    """Colour of the point (x, y) in [0, 1]² (y down), ``None`` = transparent."""
    if not _in_round_rect(x, y):
        return None
    if 0.38 <= y and _in_triangle(x, y, (0.5, 0.625), (0.37, 0.45), (0.63, 0.45)):
        return ARROW
    if abs(x - 0.5) <= 0.045 and 0.12 <= y <= 0.46:
        return ARROW
    if 0.10 <= x <= 0.90 and abs(y - _beam_y(x)) <= 0.037:
        return BEAM
    for sx in (0.2, 0.8):
        if _in_triangle(x, y, (sx, 0.655), (sx - 0.085, 0.80), (sx + 0.085, 0.80)):
            return SUPPORT
    if 0.08 <= x <= 0.92 and 0.80 <= y <= 0.83:
        return SUPPORT
    return BG


def render(size: int = BASE, ss: int = SS) -> list[list[RGBA]]:
    img: list[list[RGBA]] = []
    n = ss * ss
    for py in range(size):
        row: list[RGBA] = []
        for px in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                for sx in range(ss):
                    c = shade((px + (sx + 0.5) / ss) / size, (py + (sy + 0.5) / ss) / size)
                    if c is not None:
                        r += c[0]; g += c[1]; b += c[2]; a += 1  # noqa: E702
            row.append((r // a, g // a, b // a, 255 * a // n) if a else (0, 0, 0, 0))
        img.append(row)
    return img


def downsample(img: list[list[RGBA]], size: int) -> list[list[RGBA]]:
    """Alpha-weighted box filter (``len(img)`` must be a multiple of ``size``)."""
    f = len(img) // size
    out: list[list[RGBA]] = []
    for y in range(size):
        row: list[RGBA] = []
        for x in range(size):
            r = g = b = aw = 0
            for yy in range(y * f, (y + 1) * f):
                for xx in range(x * f, (x + 1) * f):
                    p = img[yy][xx]
                    r += p[0] * p[3]; g += p[1] * p[3]; b += p[2] * p[3]; aw += p[3]  # noqa: E702
            row.append((r // aw, g // aw, b // aw, aw // (f * f)) if aw else (0, 0, 0, 0))
        out.append(row)
    return out


def png_bytes(img: list[list[RGBA]]) -> bytes:
    h, w = len(img), len(img[0])
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in img)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico_bytes(pngs: dict[int, bytes]) -> bytes:
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for s in sizes:
        data = pngs[s]
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return header + entries + blobs


def build(sizes: tuple[int, ...] = SIZES, base: int = BASE, ss: int = SS) -> tuple[bytes, bytes]:
    """(ico bytes, 256-px png bytes)."""
    big = render(base, ss)
    pngs = {s: png_bytes(big if s == base else downsample(big, s) if base % s == 0 else render(s, 8))
            for s in sizes}
    return ico_bytes(pngs), pngs[max(sizes)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="make_icon.py")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--png", default=None, help="also write the 256-px PNG")
    args = ap.parse_args(argv)
    ico, png = build()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_bytes(ico)
    if args.png:
        Path(args.png).write_bytes(png)
    print(f"{args.out}: {len(ico)} B, sizes {SIZES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
