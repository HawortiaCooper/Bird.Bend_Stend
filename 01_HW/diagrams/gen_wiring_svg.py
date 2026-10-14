#!/usr/bin/env python3
"""Bird Bend Stand - wiring diagram generator (SVG, stdlib only; PNG via PySide6 QtSvg).

Single source of the drawings in ``01_HW/diagrams/``: the pin / net table below is cross-checked against
``01_HW/pinout.md`` (section 1.1 / 1.2 / 1.5 rows) on every run; the circuits follow ``01_HW/wiring.md`` (v0.6 =
v0.5 content) and DECISIONS D-27 / D-41 / D-42. Nothing here adds a connection that is not in wiring.md / pinout.md;
values marked ASSUMED there carry the tag "ASSUMED - verify at HW gate".

Usage (repo root):
    python 01_HW\\diagrams\\gen_wiring_svg.py                 # SVG only (any Python >= 3.10, stdlib)
    .venv\\Scripts\\python 01_HW\\diagrams\\gen_wiring_svg.py --png   # SVG + PNG (2x, white background)
    python 01_HW\\diagrams\\gen_wiring_svg.py --check         # cross-check against pinout.md only

Sheets (A4 landscape, 297 x 210 mm, 5 drawing units per mm):
    1 system overview · 2 NUCLEO pin-level wiring · 3 input cells, ENA diode OR (D-42), multimeter points ·
    4 HBS86H terminals, DIP switches (D-27), driver supply

Generated outputs are never edited by hand: change this script and re-run it.
Implements: SYS-001, SYS-007 (connection documentation), SYS-005 (driver setup sheet), SYS-006 (E-stop circuit, D-42)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
HW = HERE.parent
PINOUT_MD = HW / "pinout.md"

DOC_VERSION = "1.1"
DOC_DATE = "2026-10-14"
SOURCES = "01_HW/wiring.md v0.7 · 01_HW/pinout.md v0.6 · DECISIONS D-27, D-41, D-42, D-56"
N_SHEETS = 4

W, H = 1485, 1050          # drawing units (5 per mm): A4 landscape 297 x 210 mm
FONT = "Arial, Helvetica, sans-serif"

# ------------------------------------------------------------------------------------------------ single source
# NUCLEO-F446RE pins used by the stand (pinout.md §1.1). header = "Arduino · CN-pin / morpho", as in pinout.md.
PINS = [
    # pin,   arduino, morpho,              signal,           dir
    ("PA0", "A0", "CN8-1 / CN7-28", "PUL", "out"),
    ("PA1", "A1", "CN8-2 / CN7-30", "DIR", "out"),
    ("PA4", "A2", "CN8-3 / CN7-32", "ENA", "out"),
    ("PA9", "D8", "CN5-1 / CN10-21", "PEND", "in"),
    ("PA8", "D7", "CN9-8 / CN10-23", "ALM", "in"),
    ("PB0", "A3", "CN8-4 / CN7-34", "START limit", "in"),
    ("PC1", "A4", "CN8-5 / CN7-36", "END limit", "in"),
    ("PA10", "D2", "CN9-3 / CN10-33", "E-stop sense", "in"),
    ("PB6", "D10", "CN5-3 / CN10-17", "PAUSE", "in"),
    ("PB4", "D5", "CN9-6 / CN10-27", "HX711 DOUT", "in"),
    ("PB10", "D6", "CN9-7 / CN10-25", "HX711 PD_SCK", "out"),
    ("PB5", "D4", "CN9-5 / CN10-29", "HX711 RATE", "out"),
    ("PA7", "D11", "CN5-4 / CN10-15", "DRV_PWR (opt.)", "in"),
    ("PB9", "D14", "CN5-9 / CN10-5", "TRIP (retired)", "out"),
    ("PA5", "D13", "CN5-6 / CN10-11", "LD2 status LED", "out"),
    ("PA2", "(D1)", "CN10-35", "USART2_TX", "out"),
    ("PA3", "(D0)", "CN10-37", "USART2_RX", "in"),
]
PINS_BY = {p[0]: p for p in PINS}
END_FALLBACK = ("PB1", "CN10-24")       # pinout §1.2: END fallback, build flag -DPIN_END_PB1 (not in the delivered images)

# measurement header MH (pinout §1.5) - bench / HW gate only, used only by the HW_MEAS images
MH = [
    # jumper,     pin,    header,               from node,                                     series
    ("J-PUL-A", "PC7", "D9 · CN5-2 / CN10-19", "PUL node", "1 kΩ"),
    ("J-PUL-B", "PB7", "CN7-21", "PUL node", "1 kΩ"),
    ("J-DIR", "PC9", "CN10-1", "DIR node", "1 kΩ"),
    ("J-ENA", "PC8", "CN10-2", "ENA node", "1 kΩ"),
    ("J-EVT", "PC6", "CN10-4", "selector: PA10, PB0, PC1, PB6, PB4, PA7, PA3, DIR, PB8", "1 kΩ"),
    ("J-AUX", "PA11", "CN10-14", "selector: PB4, PB10, PA2, PA7, PA8, PA10, PB8", "1 kΩ"),
    ("J-STIM", "PB8", "D15 · CN5-10 / CN10-3", "-> input connector, switch unplugged (§6.8)", "220 Ω"),
]

DIP = [
    # switch, function,                               current (D-27 photo), target (D-27 closed)
    ("SW1", "pulses/rev (Leadshine table)", "off", "ON"),
    ("SW2", "pulses/rev", "ON", "off"),
    ("SW3", "pulses/rev", "ON", "ON"),
    ("SW4", "pulses/rev", "ON", "off"),
    ("SW5", "motor direction (off = CCW, on = CW)", "off", "off"),
    ("SW6", "start-up acceleration assist (off = standard)", "ON", "ON"),
    ("SW7", "motor select", "off", "off"),
    ("SW8", "motor select", "off", "ON"),
]

# ------------------------------------------------------------------------------------------------ styles
STY = {
    "mains": ("#6d4c41", 3.2, None),
    "pe": ("#3f9a3f", 2.2, "9,4"),
    "p48": ("#c62828", 3.4, None),
    "g48": ("#212121", 3.4, None),
    "motor": ("#ad5d00", 3.0, None),
    "lpwr": ("#ef6c00", 2.0, None),
    "gnd": ("#2e7d32", 2.0, None),
    "sig": ("#1f4e9a", 1.6, None),
    "safety": ("#b0006a", 2.4, None),
    "usb": ("#00838f", 2.4, "11,3,2,3"),
    "shield": ("#7a7a7a", 1.6, "6,3"),
    "opt": ("#9e9e9e", 1.4, "2,3"),
    "mech": ("#5f5f5f", 1.2, "5,3"),
}
C_ASSUMED = "#b26a00"
C_TITLE = "#123a6b"

# rough Arial advance widths (fraction of the font size) for the text-overlap check
_NARROW = set("il.,:;'|!()[]{}`·")
_SEMI = set("fjtrI-/ ")
_WIDE = set("mwMW%@")


def text_width(s: str, size: float, bold: bool = False) -> float:
    w = 0.0
    for ch in s:
        if ch in _NARROW:
            w += 0.28
        elif ch in _SEMI:
            w += 0.33
        elif ch in _WIDE:
            w += 0.85
        elif ch.isupper() or ch.isdigit() is False and not ch.isalpha():
            w += 0.64
        elif ch.isdigit():
            w += 0.556
        else:
            w += 0.54
    return w * size * (1.07 if bold else 1.0)


class Sheet:
    def __init__(self, number: int, title: str) -> None:
        self.number = number
        self.title = title
        self.el: list[str] = []
        self.boxes: list[tuple[float, float, float, float, str]] = []

    # ---------------------------------------------------------------------------------------- primitives
    def raw(self, s: str) -> None:
        self.el.append(s)

    def text(self, x: float, y: float, s: str, size: float = 11, anchor: str = "start", bold: bool = False,
             color: str = "#000", italic: bool = False, check: bool = True) -> None:
        attrs = [f'x="{x:.1f}"', f'y="{y:.1f}"', f'font-size="{size}"', f'fill="{color}"']
        if anchor != "start":
            attrs.append(f'text-anchor="{anchor}"')
        if bold:
            attrs.append('font-weight="bold"')
        if italic:
            attrs.append('font-style="italic"')
        self.el.append(f"<text {' '.join(attrs)}>{escape(s)}</text>")
        if check:
            w = text_width(s, size, bold)
            x0 = x if anchor == "start" else (x - w / 2 if anchor == "middle" else x - w)
            self.boxes.append((x0, y - size * 0.78, x0 + w, y + size * 0.22, s))

    def lines(self, x: float, y: float, rows: list[str], size: float = 11, lh: float = 1.3, **kw) -> float:
        for i, r in enumerate(rows):
            self.text(x, y + i * size * lh, r, size, **kw)
        return y + len(rows) * size * lh

    def rect(self, x: float, y: float, w: float, h: float, stroke: str = "#333", fill: str = "none",
             sw: float = 1.2, dash: str | None = None, rx: float = 0) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        r = f' rx="{rx}"' if rx else ""
        self.el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" '
                       f'stroke="{stroke}" stroke-width="{sw}"{d}{r}/>')

    def wire(self, pts: list[tuple[float, float]], style: str = "sig") -> None:
        color, sw, dash = STY[style]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        p = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        self.el.append(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="{sw}" '
                       f'stroke-linejoin="round" stroke-linecap="round"{d}/>')

    def poly(self, pts: list[tuple[float, float]], fill: str, stroke: str = "none", sw: float = 1) -> None:
        p = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        self.el.append(f'<polygon points="{p}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    def circle(self, x: float, y: float, r: float, fill: str = "none", stroke: str = "#000", sw: float = 1.2) -> None:
        self.el.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    def dot(self, x: float, y: float, style: str = "sig") -> None:
        self.circle(x, y, 3.2, fill=STY[style][0], stroke="none")

    def arrow(self, x: float, y: float, direction: str, style: str = "sig", size: float = 7) -> None:
        c = STY[style][0]
        s = size
        pts = {"right": [(x, y), (x - s, y - s * 0.55), (x - s, y + s * 0.55)],
               "left": [(x, y), (x + s, y - s * 0.55), (x + s, y + s * 0.55)],
               "up": [(x, y), (x - s * 0.55, y + s), (x + s * 0.55, y + s)],
               "down": [(x, y), (x - s * 0.55, y - s), (x + s * 0.55, y - s)]}[direction]
        self.poly(pts, c)

    # ---------------------------------------------------------------------------------------- symbols
    def gnd(self, x: float, y: float, kind: str = "logic", label: bool = True) -> None:
        """Ground symbol hanging below (x, y). kind: logic (logic GND), 48 (0 V of the 48 V bus), pe."""
        style = {"logic": "gnd", "48": "g48", "pe": "pe"}[kind]
        color = STY[style][0]
        self.wire([(x, y), (x, y + 7)], "gnd" if kind == "logic" else ("g48" if kind == "48" else "gnd"))
        for i, hw in enumerate((8, 5, 2)):
            yy = y + 7 + i * 3
            self.el.append(f'<line x1="{x - hw:.1f}" y1="{yy:.1f}" x2="{x + hw:.1f}" y2="{yy:.1f}" '
                           f'stroke="{color}" stroke-width="1.6"/>')
        if label:
            t = {"logic": "GND", "48": "0V(48)", "pe": "PE"}[kind]
            self.text(x + 10, y + 16, t, 9, color=color)

    def res_h(self, x1: float, x2: float, y: float, label: str, style: str = "sig", above: bool = True,
              lsize: float = 10) -> None:
        xm = (x1 + x2) / 2
        self.wire([(x1, y), (xm - 13, y)], style)
        self.wire([(xm + 13, y), (x2, y)], style)
        self.rect(xm - 13, y - 5, 26, 10, stroke=STY[style][0], fill="#fff", sw=1.4)
        self.text(xm, y - 9 if above else y + 17, label, lsize, anchor="middle")

    def res_v(self, x: float, y1: float, y2: float, label: str, style: str = "sig", right: bool = True,
              lsize: float = 10) -> None:
        ym = (y1 + y2) / 2
        self.wire([(x, y1), (x, ym - 13)], style)
        self.wire([(x, ym + 13), (x, y2)], style)
        self.rect(x - 5, ym - 13, 10, 26, stroke=STY[style][0], fill="#fff", sw=1.4)
        self.text(x + 9 if right else x - 9, ym + 4, label, lsize, anchor="start" if right else "end")

    def cap_v(self, x: float, y1: float, y2: float, label: str, style: str = "sig", right: bool = True,
              lsize: float = 10) -> None:
        ym = (y1 + y2) / 2
        c = STY[style][0]
        self.wire([(x, y1), (x, ym - 3)], style)
        self.wire([(x, ym + 3), (x, y2)], style)
        for yy in (ym - 3, ym + 3):
            self.el.append(f'<line x1="{x - 9:.1f}" y1="{yy:.1f}" x2="{x + 9:.1f}" y2="{yy:.1f}" stroke="{c}" '
                           f'stroke-width="2"/>')
        self.text(x + 12 if right else x - 12, ym + 4, label, lsize, anchor="start" if right else "end")

    def diode_h(self, x1: float, x2: float, y: float, label: str, style: str = "sig", toward: str = "right",
                above: bool = True) -> None:
        xm = (x1 + x2) / 2
        c = STY[style][0]
        self.wire([(x1, y), (x2, y)], style)
        if toward == "right":
            self.poly([(xm - 6, y - 7), (xm - 6, y + 7), (xm + 6, y)], c)
            bx = xm + 6
        else:
            self.poly([(xm + 6, y - 7), (xm + 6, y + 7), (xm - 6, y)], c)
            bx = xm - 6
        self.el.append(f'<line x1="{bx:.1f}" y1="{y - 7:.1f}" x2="{bx:.1f}" y2="{y + 7:.1f}" stroke="{c}" '
                       f'stroke-width="2"/>')
        self.text(xm, y - 11 if above else y + 20, label, 10, anchor="middle")

    def diode_v(self, x: float, y1: float, y2: float, label: str, style: str = "sig", toward: str = "up",
                right: bool = True) -> None:
        ym = (y1 + y2) / 2
        c = STY[style][0]
        self.wire([(x, y1), (x, y2)], style)
        if toward == "up":
            self.poly([(x - 7, ym + 6), (x + 7, ym + 6), (x, ym - 6)], c)
            by = ym - 6
        else:
            self.poly([(x - 7, ym - 6), (x + 7, ym - 6), (x, ym + 6)], c)
            by = ym + 6
        self.el.append(f'<line x1="{x - 7:.1f}" y1="{by:.1f}" x2="{x + 7:.1f}" y2="{by:.1f}" stroke="{c}" '
                       f'stroke-width="2"/>')
        self.text(x + 11 if right else x - 11, ym + 4, label, 10, anchor="start" if right else "end")

    def switch_h(self, x1: float, x2: float, y: float, kind: str, label: str, style: str = "safety",
                 above: bool = True) -> None:
        """Contact between x1 and x2 (NC = closed at rest, NO = open at rest)."""
        c = STY[style][0]
        a, b = x1 + (x2 - x1) * 0.3, x1 + (x2 - x1) * 0.7
        self.wire([(x1, y), (a, y)], style)
        self.wire([(b, y), (x2, y)], style)
        self.circle(a, y, 2.4, fill="#fff", stroke=c, sw=1.4)
        self.circle(b, y, 2.4, fill="#fff", stroke=c, sw=1.4)
        if kind == "NO":
            self.el.append(f'<line x1="{a:.1f}" y1="{y:.1f}" x2="{b + 2:.1f}" y2="{y - 10:.1f}" stroke="{c}" '
                           f'stroke-width="2"/>')
        else:
            self.el.append(f'<line x1="{a:.1f}" y1="{y:.1f}" x2="{b + 3:.1f}" y2="{y + 4:.1f}" stroke="{c}" '
                           f'stroke-width="2"/>')
            self.el.append(f'<line x1="{b:.1f}" y1="{y - 6:.1f}" x2="{b:.1f}" y2="{y:.1f}" stroke="{c}" '
                           f'stroke-width="1.6"/>')
        self.text((x1 + x2) / 2, y - 14 if above else y + 20, label, 10, anchor="middle")

    def switch_v(self, x: float, y1: float, y2: float, kind: str, label: str, style: str = "safety",
                 right: bool = True) -> None:
        c = STY[style][0]
        a, b = y1 + (y2 - y1) * 0.3, y1 + (y2 - y1) * 0.7
        self.wire([(x, y1), (x, a)], style)
        self.wire([(x, b), (x, y2)], style)
        self.circle(x, a, 2.4, fill="#fff", stroke=c, sw=1.4)
        self.circle(x, b, 2.4, fill="#fff", stroke=c, sw=1.4)
        if kind == "NO":
            self.el.append(f'<line x1="{x:.1f}" y1="{a:.1f}" x2="{x - 10:.1f}" y2="{b + 2:.1f}" stroke="{c}" '
                           f'stroke-width="2"/>')
        else:
            self.el.append(f'<line x1="{x:.1f}" y1="{a:.1f}" x2="{x + 4:.1f}" y2="{b + 3:.1f}" stroke="{c}" '
                           f'stroke-width="2"/>')
            self.el.append(f'<line x1="{x - 6:.1f}" y1="{b:.1f}" x2="{x:.1f}" y2="{b:.1f}" stroke="{c}" '
                           f'stroke-width="1.6"/>')
        self.text(x + 12 if right else x - 12, (y1 + y2) / 2 + 4, label, 10, anchor="start" if right else "end")

    def flag(self, x: float, y: float, name: str, point: str = "right", style: str = "sig", size: float = 10) -> float:
        """Net label: flag whose tip touches (x, y); returns the x of the far end."""
        c = STY[style][0]
        w = text_width(name, size, True) + 14
        h = size + 6
        if point == "right":
            pts = [(x, y), (x - 7, y - h / 2), (x - w, y - h / 2), (x - w, y + h / 2), (x - 7, y + h / 2)]
            tx, far = x - w / 2 - 3, x - w
        else:
            pts = [(x, y), (x + 7, y - h / 2), (x + w, y - h / 2), (x + w, y + h / 2), (x + 7, y + h / 2)]
            tx, far = x + w / 2 + 3, x + w
        self.poly(pts, "#fff", stroke=c, sw=1.3)
        self.text(tx, y + size * 0.36, name, size, anchor="middle", bold=True, color=c)
        return far

    def mpoint(self, x: float, y: float, name: str, dx: float = 6, dy: float = -6) -> None:
        """Multimeter point marker (wiring.md §11.1)."""
        self.circle(x, y, 4.5, fill="#fff", stroke="#d32f2f", sw=1.6)
        self.circle(x, y, 1.5, fill="#d32f2f", stroke="none")
        self.text(x + dx, y + dy, name, 10, bold=True, color="#d32f2f")

    def assumed(self, x: float, y: float, text: str = "ASSUMED – verify at HW gate", size: float = 9) -> None:
        w = text_width(text, size, True) * 1.08 + 12
        self.rect(x, y - size - 2, w, size + 7, stroke=C_ASSUMED, fill="#fff4e0", sw=1, rx=2)
        self.text(x + 5, y + 1, text, size, bold=True, color=C_ASSUMED)

    def box(self, x: float, y: float, w: float, h: float, title: str, rows: list[str] | None = None,
            stroke: str = "#333", fill: str = "#fff", tsize: float = 12, size: float = 10.5, dash: str | None = None,
            sw: float = 1.4) -> float:
        self.rect(x, y, w, h, stroke=stroke, fill=fill, sw=sw, dash=dash, rx=3)
        self.text(x + 7, y + tsize + 5, title, tsize, bold=True, color=C_TITLE)
        yy = y + tsize + 5 + size * 1.45
        for r in rows or []:
            self.text(x + 7, yy, r, size)
            yy += size * 1.3
        return yy

    # ---------------------------------------------------------------------------------------- frame
    def frame(self) -> None:
        self.rect(15, 15, W - 30, H - 30, stroke="#222", sw=1.6)
        self.text(30, 50, f"Sheet {self.number} — {self.title}", 20, bold=True, color=C_TITLE)
        self.text(W - 30, 50, "Bird Bend Stand — wiring diagram", 13, anchor="end", color="#555")

    def title_block(self, x: float = 1005, y: float = 925) -> None:
        w, h = W - 15 - x, H - 15 - y
        self.rect(x, y, w, h, stroke="#222", fill="#fff", sw=1.6)
        self.el.append(f'<line x1="{x}" y1="{y + 28}" x2="{x + w}" y2="{y + 28}" stroke="#222" stroke-width="1"/>')
        self.el.append(f'<line x1="{x + 300}" y1="{y}" x2="{x + 300}" y2="{y + 28}" stroke="#222" stroke-width="1"/>')
        self.text(x + 8, y + 19, "Bird Bend Stand — wiring diagram", 13, bold=True, color=C_TITLE)
        self.text(x + 308, y + 19, f"Sheet {self.number} / {N_SHEETS}", 13, bold=True)
        self.text(x + 8, y + 44, self.title, 11, bold=True)
        self.text(x + 8, y + 59, f"Version {DOC_VERSION} · {DOC_DATE} · owner Implementer A (01_HW)", 10)
        self.text(x + 8, y + 73, "Source: " + SOURCES, 9)
        self.text(x + 8, y + 86, "Generated by 01_HW/diagrams/gen_wiring_svg.py — do not edit the SVG/PNG", 9,
                  color="#555")
        self.text(x + 8, y + 99, "NOT A CERTIFIED SAFETY DESIGN — engineering proposal for a lab stand", 9.5,
                  bold=True, color="#b71c1c")

    def legend(self, items: list[tuple[str, str]], x: float = 25, y: float = 930, cols: int = 4,
               colw: float = 240, rowh: float = 18, extra: bool = True) -> None:
        self.text(x, y + 6, "Legend", 11, bold=True, color=C_TITLE)
        for i, (style, label) in enumerate(items):
            cx = x + 52 + (i % cols) * colw
            cy = y + (i // cols) * rowh
            self.wire([(cx, cy), (cx + 34, cy)], style)
            self.text(cx + 40, cy + 4, label, 9.5)
        if extra:
            ny = y + ((len(items) + cols - 1) // cols) * rowh
            self.assumed(x + 52, ny + 4, "ASSUMED – verify at HW gate")
            self.mpoint(x + 262, ny, "")
            self.text(x + 272, ny + 4, "M-n = multimeter point (wiring.md §11.1)", 9.5)
            self.flag(x + 560, ny, "NET", "right", "sig", 9)
            self.text(x + 566, ny + 4, "net label (same name = same net)", 9.5)

    # ---------------------------------------------------------------------------------------- output
    def overlaps(self) -> list[str]:
        out = []
        b = self.boxes
        for i in range(len(b)):
            x0, y0, x1, y1, s = b[i]
            if x0 < 16 or x1 > W - 16 or y0 < 16 or y1 > H - 16:
                out.append(f"outside frame: {s!r}")
            for j in range(i + 1, len(b)):
                u0, v0, u1, v1, t = b[j]
                if x0 < u1 - 1 and u0 < x1 - 1 and y0 < v1 - 1 and v0 < y1 - 1:
                    out.append(f"overlap: {s!r} <-> {t!r}")
        return out

    def svg(self) -> str:
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="297mm" height="210mm" '
                f'viewBox="0 0 {W} {H}" font-family="{FONT}">\n'
                f"<title>Bird Bend Stand wiring — sheet {self.number}: {escape(self.title)}</title>\n"
                f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>\n')
        return head + "\n".join(self.el) + "\n</svg>\n"


STD_LEGEND = [
    ("mains", "230 VAC mains"), ("pe", "PE (protective earth)"), ("p48", "+48 V driver supply"),
    ("g48", "0 V of the 48 V bus"), ("motor", "motor phases A±/B±"), ("lpwr", "logic supply 3V3 / 5V / +5V_CUT"),
    ("gnd", "logic GND (single star point)"), ("sig", "signal"), ("safety", "safety: E-stop / limits / ENA cut"),
    ("usb", "USB (PC link)"), ("shield", "cable shield / screen"), ("opt", "optional / not fitted / bench only"),
]


# ================================================================================================ sheet 1
def sheet1() -> Sheet:
    s = Sheet(1, "System overview")
    s.frame()
    # domains
    s.rect(25, 70, 1060, 225, stroke="#e3a0a0", fill="#fdf1f1", sw=1, rx=4)
    s.text(35, 88, "48 V / MAINS DOMAIN — PSU 0 V, driver GND and PE are NEVER connected to logic GND", 10.5,
           bold=True, color="#b71c1c")
    s.rect(25, 300, 1060, 600, stroke="#9db8d9", fill="#f2f6fc", sw=1, rx=4)
    s.text(35, 318, "LOGIC DOMAIN — logic GND star point (Nucleo GND = HX711 GND = input cells = opto returns)", 10.5,
           bold=True, color="#1f4e79")
    s.wire([(25, 297), (1085, 297)], "mech")
    s.text(35, 293, "isolation barrier: driver optos ≥ 500 MΩ · DC-DC ≥ 1.5 kV", 9.5, color="#555", italic=True)

    # --- mains / PSU / fuse
    s.box(40, 105, 115, 60, "230 VAC", ["L, N, PE"])
    s.box(185, 105, 105, 60, "Q0 MCB", ["mains protection"])
    s.box(320, 98, 230, 82, "48 V PSU  SDR-480-48", ["Mean Well, set-point 48 V", "10 A (15 A for 3 s)",
                                                     "mains switch = only power removal"], tsize=11.5, size=10)
    s.wire([(155, 125), (185, 125)], "mains")
    s.wire([(290, 125), (320, 125)], "mains")
    s.gnd(97, 165, "pe")
    s.gnd(435, 180, "pe")
    s.box(585, 104, 95, 44, "F1 T10A", ["at PSU +"], tsize=11, size=9.5)
    s.wire([(550, 118), (585, 118)], "p48")
    s.wire([(680, 118), (850, 118)], "p48")
    s.text(690, 112, "+48 V", 9.5)
    s.text(560, 176, "1.5 mm² twisted pair ≤ 1–2 m", 9)
    s.wire([(550, 160), (850, 160)], "g48")
    s.text(700, 154, "0 V(48)", 9.5)
    s.text(320, 218, "no contactor, no RESET button (D-41)", 9.5, italic=True, color="#b71c1c")

    # --- isolated DC-DC (straddles the barrier)
    s.rect(585, 205, 190, 132, stroke="#333", fill="#fff", sw=1.4, rx=3)
    s.text(592, 222, "isolated DC-DC 48 → 5 V, 1 W", 11, bold=True, color=C_TITLE)
    s.text(592, 237, "e.g. TRACO TMR 1-4811 class", 10)
    s.assumed(592, 256)
    s.text(592, 278, "in: +48 V (after F1), 0 V(48)", 9.5)
    s.text(592, 312, "out: +5V_CUT, 0 V = logic GND", 9.5)
    s.flag(585, 278, "+48V", "right", "p48", 9)
    s.wire([(680, 337), (680, 360)], "lpwr")
    s.flag(680, 360, "+5V_CUT", "left", "lpwr", 9)

    # --- driver
    s.rect(850, 98, 225, 372, stroke="#333", fill="#fff", sw=1.6, rx=3)
    s.text(858, 116, "HBS86H driver (PFDE clone)", 11.5, bold=True, color=C_TITLE)
    s.text(858, 130, "Leadshine-compatible, 24–100 VDC (D-16)", 9.5)
    s.text(858, 122 + 0, "", 1, check=False)
    s.text(858, 121 + 24, "+Vdc", 10, bold=True)
    s.text(858, 164, "GND", 10, bold=True)
    s.wire([(850, 118), (850, 118)], "p48")
    s.text(1068, 186, "A+ A− B+ B−", 10, anchor="end", bold=True)
    s.text(1068, 236, "encoder EA± EB±", 10, anchor="end", bold=True)
    s.gnd(1063, 196, "pe", label=False)
    s.text(1050, 222, "PE", 9, anchor="end", color=STY["pe"][0])
    s.text(858, 268, "RS232 tuning port: not used", 9.5, color="#666")
    s.text(858, 282, "(factory settings, D-28)", 9.5, color="#666")
    s.text(858, 318, "opto barrier", 9.5, italic=True, color="#555")
    s.text(858, 358, "Signal: PUL± DIR± ENA±", 10, bold=True)
    s.text(858, 408, "OUT: PEND± ALM±", 10, bold=True)
    s.text(858, 440, "DIP SW1…SW8 per D-27 (sheet 4)", 9.5)
    s.text(858, 455, "apply with the driver unpowered", 9.5)

    # --- mechanics
    s.rect(1095, 70, 365, 400, stroke="#999", fill="#fafafa", sw=1, rx=4)
    s.text(1105, 88, "MECHANICS (horizontal axis, no brake)", 10.5, bold=True, color="#444")
    s.box(1105, 100, 150, 74, "86HS2140-06", ["SIHONG stepper, 8.2 N·m", "6.0 A, 1000-line encoder"],
          tsize=11, size=9.5)
    s.wire([(1075, 182), (1180, 182), (1180, 174)], "motor")
    s.text(1086, 196, "4 × phase, shielded", 9, color=STY["motor"][0])
    s.wire([(1075, 232), (1230, 232), (1230, 174)], "sig")
    s.text(1086, 246, "encoder cable (separate)", 9, color=STY["sig"][0])
    s.text(1240, 224, "closed loop: SW7 off / SW8 on", 9, color="#555")
    s.text(1240, 237, "(D-27 target)", 9, color="#555")
    # ball screw sketch
    s.rect(1255, 128, 18, 20, stroke="#555", fill="#ddd", sw=1)
    s.rect(1273, 133, 175, 10, stroke="#555", fill="#e8e8e8", sw=1)
    for i in range(12):
        xx = 1278 + i * 14
        s.el.append(f'<line x1="{xx}" y1="143" x2="{xx + 8}" y2="133" stroke="#999" stroke-width="1"/>')
    s.rect(1330, 118, 60, 40, stroke="#333", fill="#cfd8dc", sw=1.2)
    s.text(1360, 142, "table", 9.5, anchor="middle")
    s.text(1265, 190, "SFU1605 ball screw, 5 mm lead,", 9)
    s.text(1265, 202, "300 mm stroke, direct drive", 9)
    s.assumed(1255, 270, "direct drive ASSUMED (D-19)", 8.5)
    s.arrow(1448, 112, "right", "mech", 8)
    s.wire([(1400, 112), (1442, 112)], "mech")
    s.text(1446, 100, "+x = away from START", 9, anchor="end")
    # limit switches
    s.switch_v(1290, 300, 340, "NC", "", "safety")
    s.text(1250, 362, "START (home end)", 9.5, bold=True, color=STY["safety"][0])
    s.text(1250, 375, "NC limit switch", 9.5)
    s.switch_v(1420, 300, 340, "NC", "", "safety")
    s.text(1380, 362, "END", 9.5, bold=True, color=STY["safety"][0])
    s.text(1380, 375, "NC limit", 9.5)
    s.flag(1290, 300, "J2 START", "right", "safety", 8.5)
    s.flag(1420, 300, "J2 END", "right", "safety", 8.5)
    s.text(1105, 405, "switch positions schematic; actuation beyond the", 9, color="#555")
    s.text(1105, 418, "soft limits 0.5 / 290 mm (A-07)", 9, color="#555")
    s.box(1105, 425, 345, 38, "Keli DEF 200 kg load cell, 3.0 mV/V", [], tsize=10, size=9)
    s.text(1112, 455, "tension + compression, in the load path (table ↔ specimen ↔ frame)", 9)

    # --- PC and Nucleo
    s.box(40, 470, 175, 100, "PC", ["Bird Bend Stand app", "(pc_software zip)", "port: STLink Virtual", "COM Port (COMn)"],
          tsize=12, size=10)
    s.rect(290, 430, 245, 245, stroke="#333", fill="#fff", sw=1.6, rx=3)
    s.text(298, 448, "NUCLEO-F446RE (MB1136)", 12, bold=True, color=C_TITLE)
    s.lines(298, 466, ["ST-LINK/V2-1 on board:", "· USB VCP = USART2 PA2/PA3,", "  921 600 Bd 8N1, CRC-16 frames",
                       "· 8 MHz MCO → PH0 (HSE bypass)", "· SWD (flashing, D-06: PO only)", "",
                       "supply: USB (JP5 = U5V)", "or 5 V into E5V (CN7-6),", "JP5 = E5V"], 10)
    s.assumed(298, 600)
    s.text(298, 620, "pin-level detail: sheet 2", 10, bold=True)
    s.text(298, 636, "solder bridges: pinout.md §5", 10)
    s.text(298, 652, "(SB13/14 ON, SB46/52 OFF, SB51 ON …)", 9.5)
    s.wire([(215, 520), (290, 520)], "usb")
    s.text(220, 512, "USB", 10, bold=True, color=STY["usb"][0])
    s.box(40, 600, 175, 62, "optional 5 V DC-DC", ["→ E5V (CN7-6), JP5 = E5V", "(D-22; USB still carries VCP)"],
          tsize=10.5, size=9.5, dash="4,3", stroke="#888")
    s.wire([(215, 640), (290, 640)], "opt")

    # --- interface board
    s.rect(600, 430, 215, 245, stroke="#333", fill="#fff", sw=1.6, rx=3)
    s.text(608, 448, "Interface board", 12, bold=True, color=C_TITLE)
    s.lines(608, 466, ["Stage 1 (release config.):", "RC cells + pull-ups, direct", "3.3 V drive (D-17), qualified", "per wiring §2.2 (D-56 b)",
                       "Stage 2 (only if the", "qualification fails, SYS-011):", "SN74ACT244 5 V buffer shield",
                       "J1 driver 10-p · J2 limits 4-p", "J3 panel 8-p · J4 HX711 6-p"], 10)
    s.assumed(608, 595)
    s.text(608, 618, "input cells, ENA diode OR: sheet 3", 9.5, bold=True)
    s.text(608, 632, "measurement header MH: bench only", 9.5, color="#777")
    s.wire([(535, 470), (600, 470)], "sig")
    s.wire([(535, 490), (600, 490)], "safety")
    s.wire([(535, 510), (600, 510)], "lpwr")
    s.wire([(535, 530), (600, 530)], "gnd")
    s.text(540, 464, "Arduino", 8.5, color="#555")
    s.text(540, 545, "headers", 8.5, color="#555")
    # control cable to the driver
    s.wire([(760, 430), (760, 350), (850, 350)], "sig")
    s.wire([(800, 430), (800, 400), (850, 400)], "sig")
    s.text(612, 378, "control cable:", 9.5, bold=True)
    s.text(612, 391, "LiYCY 6×2×0.25 twisted pairs,", 9)
    s.text(612, 404, "≤ 1–2 m; shield → logic GND", 9)
    s.text(612, 417, "at the board end only", 9)
    s.wire([(745, 432), (745, 365), (848, 365)], "shield")
    s.arrow(850, 350, "right")
    s.arrow(800, 430, "down")
    # limits net flags
    s.flag(815, 560, "J2 START", "left", "safety", 8.5)
    s.flag(815, 580, "J2 END", "left", "safety", 8.5)
    # operator panel
    s.rect(895, 520, 185, 225, stroke="#b0006a", fill="#fff", sw=1.8, rx=3)
    s.text(903, 538, "Operator panel", 12, bold=True, color=C_TITLE)
    s.circle(925, 572, 16, fill="#e53935", stroke="#7f0000", sw=2)
    s.circle(925, 572, 21, fill="none", stroke="#fbc02d", sw=4)
    s.text(952, 566, "S0 red E-stop", 10.5, bold=True, color="#b71c1c")
    s.text(952, 580, "mushroom, turn-to-release", 9)
    s.lines(903, 610, ["NC block → PA10 E-stop sense", "  (MCU/FW stop, D-41)", "NO block → +5V_CUT → R_E 68 Ω",
                       "  → D2 → ENA+ (hardwired ENA", "  cut, no MCU element, D-42)"], 9.5)
    s.circle(918, 691, 9, fill="#fdd835", stroke="#555", sw=1.5)
    s.text(935, 695, "PAUSE (NO, momentary)", 9.5, bold=True)
    s.text(903, 718, "not an IEC 60204-1 E-stop", 9.5, bold=True, color="#b71c1c")
    s.text(903, 732, "(no power removal, D-41)", 9.5, color="#b71c1c")
    s.wire([(815, 620), (895, 620)], "safety")
    s.text(820, 614, "J3 panel", 9, color=STY["safety"][0])
    s.flag(895, 650, "+5V_CUT", "right", "lpwr", 8.5)
    s.wire([(1040, 520), (1040, 470)], "safety")
    s.arrow(1040, 470, "up", "safety")
    s.text(1045, 505, "NO → R_E → D2 → ENA+", 9, color=STY["safety"][0])
    s.text(1045, 492, "(diode OR, sheet 3)", 8.5, color=STY["safety"][0])

    # --- HX711 and load cell cable
    s.box(600, 740, 215, 80, "HX711 module @ 3.3 V", ["80 SPS, gain 128 (D-04)", "RATE pin 15 lifted → PB5 (D-21)",
                                                       "close to the Nucleo, wires ≤ 20 cm"], tsize=11, size=9.5)
    s.wire([(535, 650), (570, 650), (570, 760), (600, 760)], "sig")
    s.text(450, 700 - 6 + 5, "", 1, check=False)
    s.lines(565, 700, ["DOUT, SCK,", "RATE,", "3V3, GND"], 8.5, anchor="end")
    s.wire([(815, 790), (1180, 790), (1180, 463)], "sig")
    s.wire([(815, 800), (1190, 800), (1190, 463)], "shield")
    s.text(830, 784, "cell cable 5 m, 4-wire + shield (do not shorten)", 9.5)
    s.text(830, 815, "shield (yellow) → HX711 GND only", 9.5, color="#666")

    # --- grounding star
    s.rect(40, 700, 460, 190, stroke="#2e7d32", fill="#fff", sw=1.4, rx=3)
    s.text(48, 718, "Grounding and shields (wiring.md §9)", 12, bold=True, color="#2e7d32")
    s.gnd(70, 730, "logic", label=False)
    s.text(90, 744, "logic GND star point at the Nucleo GND:", 10, bold=True)
    s.lines(48, 765, [
        "· Nucleo GND = HX711 GND = input-cell GND = +5V_CUT 0 V",
        "· opto returns PUL− DIR− ENA− ALM− PEND− (common cathode, D-17)",
        "· the PC ground enters through the USB / ST-LINK",
        "· NEVER connect logic GND to the driver 48 V GND, the PSU 0 V or PE",
        "· motor cable shield → driver PE at the driver end",
        "· control cable shield → logic GND at the board end only",
        "· load-cell shield (yellow) → HX711 GND only; keep the cell cable",
        "  ≥ 10–20 cm from motor / 48 V wires, cross at 90°"], 10)

    s.box(600, 840, 480, 50, "Not fitted in release 1", ["DRV_PWR 48 V presence sense (PA7, drv.pwr_sense_enable = 0) · "
                                                         "TRIP relay (PB9 retired)"], stroke="#999",
          dash="4,3", tsize=10.5, size=9.5)
    s.legend(STD_LEGEND)
    s.title_block()
    return s


# ================================================================================================ sheet 2
def nucleo_rows() -> dict[str, float]:
    return {"PA0": 128, "PA1": 164, "PA4": 200, "PA9": 348, "PA8": 380, "PB0": 441, "PC1": 481, "PA10": 530,
            "PB6": 570, "PB4": 620, "PB10": 650, "PB5": 680, "PA7": 746, "PB9": 768, "PA5": 790, "PA2": 812,
            "PA3": 834}


def sheet2() -> Sheet:
    s = Sheet(2, "NUCLEO-F446RE pin-level wiring")
    s.frame()
    rows = nucleo_rows()
    bx, bw = 30, 278
    # header row
    s.text(bx + 4, 82, "Pin", 10, bold=True)
    s.text(bx + 44, 82, "Ard.", 10, bold=True)
    s.text(bx + 80, 82, "morpho / CN", 10, bold=True)
    s.text(bx + 172, 82, "signal", 10, bold=True)
    s.text(bx + 252, 82, "dir", 10, bold=True)
    s.text(340, 82, "conditioning on the interface board (sheet 3)", 10, bold=True)
    s.text(705, 82, "destination", 10, bold=True)
    s.rect(bx, 92, bw, 948 - 92, stroke="#333", fill="#fff", sw=1.8, rx=3)
    s.text(bx + 4, 107, "NUCLEO-F446RE", 11, bold=True, color=C_TITLE)
    s.assumed(bx + 112, 108, "CN numbers ASSUMED (UM1724)", 8)
    for pin, (y) in rows.items():
        p = PINS_BY[pin]
        style = "opt" if pin in ("PA7", "PB9") else "sig"
        s.text(bx + 4, y + 4, p[0], 10.5, bold=True)
        s.text(bx + 44, y + 4, p[1], 10)
        s.text(bx + 80, y + 4, p[2], 9.5)
        s.text(bx + 172, y + 4, p[3], 9.5, bold=pin not in ("PA7", "PB9", "PA5"))
        s.text(bx + 252, y + 4, p[4], 9.5, color="#555")
        if pin in ("PA5", "PA2", "PA3"):
            continue
        s.wire([(bx + bw, y), (bx + bw + 18, y)], style)

    # ---------------- driver Signal block
    dx = 700
    s.rect(dx, 95, 170, 140, stroke="#333", fill="#fff", sw=1.6, rx=3)
    s.text(dx + 52, 112, "HBS86H  Signal", 11, bold=True, color=C_TITLE)
    term = [("PUL+", 128), ("PUL−", 146), ("DIR+", 164), ("DIR−", 182), ("ENA+", 200), ("ENA−", 218)]
    for name, y in term:
        s.rect(dx - 4, y - 4, 8, 8, stroke="#333", fill="#ddd", sw=1)
        s.text(dx + 10, y + 4, name, 10, bold=True)
        if name.endswith("−"):
            s.wire([(dx - 4, y), (dx - 12, y)], "gnd")
            s.gnd(dx - 12, y, "logic", label=False)
    s.text(dx + 60, 146, "opto LEDs, ≈ 270 Ω inside", 9)
    s.assumed(dx + 60, 164, "ASSUMED (clone)", 8.5)
    s.text(dx + 60, 182, "LED current on ENA", 9)
    s.text(dx + 60, 196, "= driver DISABLED", 9, bold=True)
    s.text(dx + 60, 210, "(Leadshine; C-07)", 9)
    s.text(dx + 60, 228, "common cathode (D-17)", 9)
    # stage-2 buffer (dashed) across PUL / DIR / ENA
    s.rect(375, 112, 90, 104, stroke="#6a1b9a", fill="#f6effa", sw=1.3, dash="5,3", rx=3)
    s.text(420, 125, "U1 SN74ACT244", 8.5, anchor="middle", bold=True, color="#6a1b9a")
    s.text(420, 214 - 2, "Stage 2 (option)", 8.5, anchor="middle", color="#6a1b9a")
    s.text(420, 147, "stage 1: wire", 8.5, anchor="middle", color="#6a1b9a")
    s.text(420, 158, "straight through", 8.5, anchor="middle", color="#6a1b9a")
    # PUL, DIR
    for y in (128, 164):
        s.wire([(326, y), (dx - 4, y)], "sig")
        s.arrow(dx - 6, y, "right")
    s.text(560, 122, "PUL (TIM2_CH1, 3.3 V push-pull)", 9)
    s.text(560, 158, "DIR (GPIO push-pull)", 9)
    # ENA with D1, node, PD, D2 branch
    s.wire([(326, 200), (520, 200)], "sig")
    s.text(330, 194 + 22 + 8, "", 1, check=False)
    s.diode_h(520, 575, 200, "D1 BAT54", "sig", "right")
    s.wire([(575, 200), (dx - 4, 200)], "safety")
    s.dot(615, 200, "safety")
    s.dot(650, 200, "safety")
    s.res_v(615, 200, 250, "10 kΩ", "gnd", right=False)
    s.gnd(615, 250, "logic", label=False)
    s.diode_v(650, 268, 214, "D2", "safety", "up")
    s.wire([(650, 214), (650, 200)], "safety")
    s.wire([(650, 268), (650, 280), (590, 280)], "safety")
    s.res_h(530, 590, 280, "R_E 68 Ω 0.5 W", "safety", above=False)
    s.switch_h(450, 530, 280, "NO", "S0 NO block", "safety", above=False)
    s.flag(450, 280, "+5V_CUT", "right", "lpwr", 9)
    s.mpoint(560, 268, "M-1", dx=-30, dy=-6)
    s.mpoint(326, 207, "M-3", dx=4, dy=14)
    s.mpoint(dx - 30, 200, "M-4", dx=-14, dy=-8)
    s.text(440, 318, "D-42 hardwired ENA cut: no MCU-controlled element", 9.5, bold=True, color=STY["safety"][0])
    s.text(440, 331, "M-2: 100 Ω shunt in series with ENA− (temporary)", 9, color="#d32f2f")
    s.text(dx + 10, 240 + 4, "−: logic GND", 8.5, color=STY["gnd"][0])
    s.text(330, 245, "PA4 = TC pin (NOT 5 V", 9, color="#b71c1c")
    s.text(330, 257, "tolerant): push-pull only", 9, color="#b71c1c")
    # ---------------- driver OUT block
    s.rect(dx, 322, 170, 92, stroke="#333", fill="#fff", sw=1.6, rx=3)
    s.text(dx + 62, 336, "HBS86H  OUT", 11, bold=True, color=C_TITLE)
    for name, y in [("PEND+", 348), ("PEND−", 364), ("ALM+", 380), ("ALM−", 396)]:
        s.rect(dx - 4, y - 4, 8, 8, stroke="#333", fill="#ddd", sw=1)
        s.text(dx + 10, y + 4, name, 10, bold=True)
        if name.endswith("−"):
            s.wire([(dx - 4, y), (dx - 12, y)], "gnd")
            s.gnd(dx - 12, y, "logic", label=False)
    s.text(dx + 66, 364, "OC opto outputs", 9)
    s.text(dx + 66, 378, "levels of the clone", 9)
    s.text(dx + 66, 392, "not documented →", 9)
    s.text(dx + 66, 406, "measure (C-16)", 9)
    for y, cell in ((348, "cell A"), (380, "cell A")):
        s.wire([(326, y), (515, y)], "sig")
        s.rect(515, y - 9, 150, 18, stroke="#1f4e9a", fill="#eef3fb", sw=1.1, rx=2)
        s.text(521, y + 4, f"{cell}: 4.7k PU · 1k · 1 nF", 9)
        s.wire([(665, y), (dx - 4, y)], "sig")
        s.arrow(332, y, "left")
    s.text(340, 342, "PEND: high = in position", 9, color="#555")
    s.text(340, 374, "ALM: high = alarm / unpowered", 9, color="#555")
    # ---------------- limits
    for y, name, sub in ((441, "START limit", "NC to GND · home end (only ref.)"),
                         (481, "END limit", "NC to GND · never a home ref.")):
        s.wire([(326, y), (515, y)], "safety")
        s.rect(515, y - 9, 150, 18, stroke="#b0006a", fill="#fbeef5", sw=1.1, rx=2)
        s.text(521, y + 4, "cell B: 1k PU · 1k · 47 nF", 9)
        s.wire([(665, y), (dx, y)], "safety")
        s.arrow(332, y, "left", "safety")
        s.rect(dx, y - 16, 170, 32, stroke="#b0006a", fill="#fff", sw=1.4, rx=3)
        s.text(dx + 8, y - 2, name, 10.5, bold=True, color=STY["safety"][0])
        s.text(dx + 8, y + 11, sub, 9)
        s.flag(dx - 2, y, "J2", "right", "safety", 8)
    s.text(340, 435, "high = hit or wire broken (fail-safe)", 9, color="#555")
    s.text(340, 475, "high = hit or wire broken", 9, color="#555")
    # ---------------- operator panel
    for y, name, sub, cell, st in ((530, "S0 E-stop NC block", "direct-opening, contact → GND",
                                    "cell C: 1k PU · 1k · 4.7 nF", "safety"),
                                   (570, "PAUSE button", "NO momentary, contact → GND", "cell D: 4.7k PU · 1k · 220 nF",
                                    "sig")):
        s.wire([(326, y), (515, y)], st)
        s.rect(515, y - 9, 150, 18, stroke=STY[st][0], fill="#fbeef5" if st == "safety" else "#eef3fb", sw=1.1, rx=2)
        s.text(521, y + 4, cell, 9)
        s.wire([(665, y), (dx, y)], st)
        s.arrow(332, y, "left", st)
        s.rect(dx, y - 16, 170, 32, stroke=STY[st][0], fill="#fff", sw=1.4, rx=3)
        s.text(dx + 8, y - 2, name, 10.5, bold=True, color=STY[st][0])
        s.text(dx + 8, y + 11, sub, 9)
        s.flag(dx - 2, y, "J3", "right", st, 8)
    s.mpoint(326, 537, "M-6", dx=4, dy=14)
    s.text(340, 524, "high = pressed or wire broken", 9, color="#555")
    s.text(340, 564, "low = pressed", 9, color="#555")
    # ---------------- HX711
    hx = 700
    s.rect(hx, 596, 120, 144, stroke="#333", fill="#fff", sw=1.6, rx=3)
    s.text(hx + 8, 609, "HX711 (3.3 V)", 10, bold=True, color=C_TITLE)
    for name, y in [("DOUT", 620), ("PD_SCK", 650), ("RATE", 680), ("VCC/DVDD", 703), ("GND", 725)]:
        s.rect(hx - 4, y - 4, 8, 8, stroke="#333", fill="#ddd", sw=1)
        s.text(hx + 8, y + 4, name, 9.5, bold=True)
    s.wire([(326, 620), (hx - 4, 620)], "sig")
    s.arrow(332, 620, "left")
    s.text(340, 614, "DOUT: low = data ready (EXTI4)", 9, color="#555")
    s.wire([(326, 650), (540, 650)], "sig")
    s.res_h(540, 600, 650, "33 Ω", "sig")
    s.wire([(600, 650), (hx - 4, 650)], "sig")
    s.arrow(hx - 6, 650, "right")
    s.wire([(326, 680), (hx - 4, 680)], "sig")
    s.arrow(hx - 6, 680, "right")
    s.text(340, 674, "RATE high = 80 SPS (D-21)", 9, color="#555")
    s.text(hx + 58, 680 + 4, "10 kΩ PU", 8.5, color="#555")
    s.text(hx + 58, 692 + 4, "to DVDD", 8.5, color="#555")
    s.flag(hx - 4, 703, "3V3", "right", "lpwr", 8.5)
    s.wire([(hx - 4, 725), (hx - 22, 725)], "gnd")
    s.text(hx - 25, 729, "GND", 9, anchor="end", color=STY["gnd"][0])
    # load cell
    lx = 865
    s.rect(lx, 596, 110, 144, stroke="#333", fill="#fff", sw=1.4, rx=3)
    s.text(lx + 6, 612, "Keli DEF 200 kg", 9.5, bold=True, color=C_TITLE)
    for i, (hxname, col, cname) in enumerate([("E+", "Red", "EXC+"), ("E−", "Black", "EXC−"), ("A+", "Green", "SIG+"),
                                               ("A−", "White", "SIG−"), ("GND", "Yellow", "shield")]):
        y = 632 + i * 21
        s.text(hx + 115, y + 4, hxname, 9, anchor="end", bold=True)
        s.wire([(hx + 120, y), (lx, y)], "shield" if hxname == "GND" else "sig")
        s.text(lx + 6, y + 4, f"{col} {cname}", 9)
    s.text(hx + 30, 716 + 14, "", 1, check=False)
    # ---------------- others
    s.wire([(326, 746), (360, 746)], "opt")
    s.text(366, 750, "optional 48 V presence sense — NOT fitted in release 1 (drv.pwr_sense_enable = 0): unwired = "
           "ignored", 9, color="#777")
    s.wire([(326, 768), (360, 768)], "opt")
    s.text(366, 772, "TRIP retired (D-41): driven low by the FW, nothing connected", 9, color="#777")
    s.text(bx + bw + 8, 794, "on-board LD2 (status blink codes, FW_design §5.12)", 9, color="#555")
    s.text(bx + bw + 8, 816, "PA2 / PA3: on-board to the ST-LINK VCP (SB13 / SB14 ON, SB62 / SB63 OFF):", 9,
           color="#555")
    s.text(bx + bw + 8, 830, "nothing may be wired to D0 / D1 · USB → PC, 921 600 Bd 8N1", 9, color="#555")
    # power pins
    s.text(bx + 4, 862, "power pins (CN6 / CN7):", 9.5, bold=True)
    pw = [("3V3", "→ pull-ups, HX711 VCC", "lpwr"), ("5V", "→ stage-2 buffer VCC", "lpwr"),
          ("E5V", "← optional DC-DC 5 V (CN7-6, JP5)", "opt"), ("GND", "→ logic GND star point", "gnd")]
    for i, (name, txt, st) in enumerate(pw):
        y = 879 + i * 17
        s.text(bx + 4, y + 4, name, 10.5, bold=True)
        s.wire([(bx + 40, y), (bx + 70, y)], st)
        s.text(bx + 76, y + 4, txt, 9.5)
    s.rect(bx, 848, bw, 99, stroke="#333", fill="none", sw=1.2)

    # ---------------- right panel
    rx0 = 1005
    s.box(rx0, 92, 455, 168, "Stage 2 buffer — only if the qualification fails", [
        "Direct 3.3 V drive qualified per wiring §2.2 (D-56 b, SYS-011).",
        "SN74ACT244 at 5 V (Nucleo 5V pin); inputs from PA0 / PA1 / PA4,",
        "each with a 10 kΩ pull-down (outputs low during MCU reset).",
        "Outputs → PUL+ / DIR+ and through D1 → ENA+; PUL− / DIR− / ENA−",
        "stay on logic GND → ≈ 11–13 mA per LED. Unused inputs → GND,",
        "100 nF + 10 µF at U1. FW unchanged, same pins.",
        "Stage 1 = release: direct 3.3 V, ≈ 6–7 mA (4.3 mA worst), qualified.",
        "Optional: 10 kΩ pull-down at the driver end of PUL+ and DIR+."], tsize=11, size=9.5)
    s.assumed(rx0 + 330, 252, "part values ASSUMED", 8.5)
    # MH table
    my = 272
    s.rect(rx0, my, 455, 196, stroke="#888", fill="#fafafa", sw=1.2, dash="4,3", rx=3)
    s.text(rx0 + 7, my + 17, "Measurement header MH — HW gate only (wiring §6.1)", 11, bold=True, color="#555")
    s.text(rx0 + 7, my + 32, "2 × 8 pins, GND next to every signal, wires ≤ 10 cm; used only by the", 9)
    s.text(rx0 + 7, my + 45, "HW_MEAS images; in every image the MH pins are digital inputs, no pull.", 9)
    s.text(rx0 + 7, my + 62, "jumper", 9, bold=True)
    s.text(rx0 + 62, my + 62, "MCU pin", 9, bold=True)
    s.text(rx0 + 160, my + 62, "from node", 9, bold=True)
    s.text(rx0 + 448, my + 62, "series R", 9, bold=True, anchor="end")
    for i, (j, pin, hdr, frm, ser) in enumerate(MH):
        y = my + 78 + i * 16
        s.text(rx0 + 7, y, j, 9, bold=True)
        s.text(rx0 + 62, y, f"{pin} ({hdr.split(' · ')[-1]})", 8.5)
        s.text(rx0 + 160, y, frm if len(frm) < 52 else frm[:50] + "…", 8.5)
        s.text(rx0 + 448, y, ser, 8.5, anchor="end", bold=ser.startswith("220"))
    s.text(rx0 + 7, my + 78 + 7 * 16, "1 kΩ at the MCU end; J-STIM 220 Ω (REQ-A-M2-07). Bench procedure: FW_test_plan §6.8.",
           8.5, color="#555")
    # solder bridges
    sy = 480
    s.box(rx0, sy, 455, 140, "Solder bridges / jumpers (pinout.md §5, C-01)", [
        "SB13, SB14 ON — PA2/PA3 ↔ ST-LINK VCP",
        "SB62, SB63 OFF — D0 / D1 not connected to PA2 / PA3",
        "SB16, SB50 ON — ST-LINK 8 MHz MCO → PH0 (else CLK chip amber)",
        "SB54, SB55 OFF · SB51, SB56 ON — A4 = PC1 (END limit)",
        "SB46, SB52 OFF — else PB9 shorts PC1 and PB8 shorts PC0",
        "JP5 = U5V (USB power) or E5V · JP6 (IDD) fitted"], tsize=11, size=9.5)
    s.assumed(rx0 + 320, sy + 132, "factory state ASSUMED", 8.5)
    # notes
    ny = 632
    s.box(rx0, ny, 455, 280, "Rules", [
        "· Inputs START / END / E-stop: NC to GND + pull-up → closed = low = OK;",
        "  open, wire broken or unplugged = high = ACTIVE (fail-safe, SAF-FW-007).",
        "· PAUSE: NO to GND (a broken wire looks like 'not pressed', KL-05).",
        "· PA4 (ENA) is a TC pin, not 5 V tolerant: never open-drain to 5 V; the",
        "  diode OR (D1 / D2) keeps the 5 V of the E-stop cut away from PA4.",
        "· The FW never reads ENA back: the forced 'disabled' state from the",
        "  E-stop NO contact causes no fault (D-42).",
        "· Driver inputs rated 5–24 V (label): 3.3 V drive qualified (§2.2).",
        "· ALM / PEND: report + ALM start-block only (D-16 / D-28); in open-loop",
        "  setting ALM cannot show missed steps (D-27).",
        "· HX711: whole module at 3.3 V (A-17); never DVDD at 5 V with direct",
        "  MCU wiring. Cell sign: swap Green / White or let K < 0 (SW-CAL-007).",
        "· Delivered FW images use PC1 (A4) for END; the PB1 (CN10-24) fallback",
        "  needs the build flag -DPIN_END_PB1 (not delivered).",
        "· Signal names / pins: pinout.md §1.1 (cross-checked by the generator)."], tsize=11, size=9.5)
    s.legend([("lpwr", "logic supply 3V3 / 5V / +5V_CUT"), ("gnd", "logic GND"), ("sig", "signal"),
              ("safety", "safety: E-stop / limits / ENA cut"), ("shield", "cable shield"),
              ("opt", "optional / not fitted / retired")], y=955, cols=3, colw=290)
    s.text(25 + 52, 1012, "cell A…D = input RC cells (sheet 3) · ▶ = signal direction · J2 / J3 = interface-board "
           "connector (stage 2)", 9.5, color="#555")
    s.title_block()
    return s


# ================================================================================================ sheet 3
def _cell(s: Sheet, x: float, y: float, title: str, pu: str, ser: str, cap: str, pin: str, contact: str,
          contact_label: str, tau: str, note: list[str], style: str = "safety", mp: str | None = None) -> None:
    w, h = 465, 238
    s.rect(x, y, w, h, stroke="#999", fill="#fff", sw=1.2, rx=4)
    s.text(x + 8, y + 18, title, 11.5, bold=True, color=C_TITLE)
    yl = y + 92
    n1, n2 = x + 175, x + 290
    s.flag(x + 50, yl - 40, "3V3", "right", "lpwr", 8.5)
    s.wire([(x + 50, yl - 40), (x + 120, yl - 40)], "lpwr")
    s.res_h(x + 120, n1, yl - 40, pu, "lpwr")
    s.wire([(n1, yl - 40), (n1, yl)], style)
    s.dot(n1, yl, style)
    s.res_h(n1, n2, yl, ser, style)
    s.dot(n2, yl, style)
    s.wire([(n2, yl), (x + 390, yl)], style)
    s.arrow(x + 390, yl, "right", style)
    s.text(x + 395, yl + 4, pin, 10.5, bold=True)
    s.cap_v(n2, yl, yl + 50, cap, style)
    s.gnd(n2, yl + 50, "logic")
    # contact from n1 to GND
    s.wire([(n1, yl), (x + 80, yl)], style)
    if contact == "OC":
        s.rect(x + 18, yl - 8, 62, 68, stroke="#333", fill="#f5f5f5", sw=1.2, rx=2)
        s.text(x + 49, yl + 4, "+", 10, anchor="middle", bold=True)
        s.text(x + 49, yl + 50, "−", 12, anchor="middle", bold=True)
        s.text(x + 49, yl + 22, "OC", 9, anchor="middle")
        s.text(x + 49, yl + 34, "opto", 9, anchor="middle")
        s.wire([(x + 49, yl + 60), (x + 49, yl + 72)], "gnd")
        s.gnd(x + 49, yl + 72, "logic")
        s.text(x + 18, yl - 14, contact_label, 9.5, bold=True)
    else:
        s.wire([(x + 80, yl), (x + 80, yl + 8)], style)
        s.switch_v(x + 80, yl + 8, yl + 56, contact, "", style)
        s.gnd(x + 80, yl + 56, "logic")
        s.text(x + 18, yl - 14, contact_label, 9.5, bold=True)
    if mp:
        s.mpoint(x + 380, yl, mp, dx=-8, dy=-10)
    s.text(x + 330, yl + 66, tau, 9.5, color="#555")
    yy = y + h - 12 - (len(note) - 1) * 12.5
    for i, r in enumerate(note):
        s.text(x + 8, yy + i * 12.5, r, 9.5)


def sheet3() -> Sheet:
    s = Sheet(3, "Input cells, ENA diode OR (D-42), multimeter points")
    s.frame()
    x1, x2 = 25, 500
    _cell(s, x1, 70, "cell A — ALM (PA8) / PEND (PA9)", "4.7 kΩ", "1 kΩ", "1 nF", "PA8 / PA9", "OC",
          "HBS86H ALM+ / PEND+", "τ ≈ 5.7 µs",
          ["Polled at 1 kHz. ALM−, PEND− → logic GND. Default polarity: output conducting = low = OK",
           "(and powered); open = high = alarm / unpowered / wire broken (drv.*_active_level).",
           "External 4.7 kΩ pull-up mandatory (internal 30–50 kΩ too weak for a cable)."], style="sig")
    _cell(s, x2, 70, "cell B — START (PB0) / END (PC1) limit, NC", "1 kΩ", "1 kΩ", "47 nF", "PB0 / PC1", "NC",
          "switch NC, common → GND", "τ ≈ 94 µs rise / 47 µs fall",
          ["EXTI both edges, level 1: stop on the FIRST edge, release debounced (io.release_ms 20 ms).",
           "Closed = low = OK; open / wire broken = high = limit active (fail-safe).",
           "3.3 mA wetting; silver micro-switches: pull-up 680 Ω if contacts misbehave."], mp=None)
    _cell(s, x1, 318, "cell C — E-stop sense (PA10), S0 NC block", "1 kΩ", "1 kΩ", "4.7 nF", "PA10", "NC",
          "S0 NC (direct-opening)", "τ ≤ 10 µs",
          ["EXTI15_10, level 0 (alone): open → TRUNCATE ≤ 100 µs, ENA disabled ≤ 1 ms, ESTOP latched.",
           "M-6 at PA10: ≤ 0.4 V released / ≥ 2.3 V pressed. Internal PU 7–14 kΩ also on.",
           "Bench STIM (J-STIM 220 Ω, switch unplugged) only per FW_test_plan §6.8 — M-7 ≈ 0.6 V."], mp="M-6")
    _cell(s, x2, 318, "cell D — PAUSE (PB6), NO momentary", "4.7 kΩ", "1 kΩ", "220 nF", "PB6", "NO",
          "PAUSE NO → GND", "τ ≈ 1.3 ms",
          ["EXTI9_5, level 1. Press = controlled stop (holding), PAUSED latched;",
           "press while paused = RESUME_REQUEST only (SAF-FW-023).",
           "NO contact: a broken wire is not detected (KL-05) — test before a series."], style="sig")
    # cell E optional
    x, y = x1, 566
    s.rect(x, y, 940, 160, stroke="#aaa", fill="#fff", sw=1.2, dash="4,3", rx=4)
    s.text(x + 8, y + 18, "cell E — DRV_PWR 48 V presence sense (PA7) — OPTIONAL, NOT FITTED in release 1 (wiring §7.2)",
           11.5, bold=True, color="#777")
    yl = y + 92
    s.flag(x + 70, yl, "+48V", "right", "p48", 8.5)
    s.res_h(x + 70, x + 150, yl, "22 kΩ 0.5 W", "p48")
    s.rect(x + 150, yl - 12, 80, 64, stroke="#555", fill="#f5f5f5", sw=1.2, rx=2)
    s.text(x + 190, yl + 4, "LTV-817", 9.5, anchor="middle", bold=True)
    s.text(x + 190, yl + 17, "+ 1N4148", 9, anchor="middle")
    s.text(x + 190, yl + 29, "reverse", 9, anchor="middle")
    s.text(x + 190, yl + 41, "≥ 3 mm creep.", 8.5, anchor="middle")
    s.wire([(x + 150, yl + 40), (x + 120, yl + 40), (x + 120, yl + 46)], "g48")
    s.gnd(x + 120, yl + 46, "48")
    s.wire([(x + 230, yl), (x + 330, yl)], "opt")
    s.dot(x + 300, yl, "opt")
    s.res_v(x + 300, yl - 50, yl, "10 kΩ", "opt", right=False)
    s.wire([(x + 300, yl - 50), (x + 318, yl - 50)], "opt")
    s.flag(x + 318, yl - 50, "3V3", "left", "lpwr", 8.5)
    s.res_h(x + 330, x + 400, yl, "1 kΩ", "opt")
    s.dot(x + 400, yl, "opt")
    s.wire([(x + 400, yl), (x + 480, yl)], "opt")
    s.text(x + 485, yl + 4, "PA7", 10.5, bold=True, color="#777")
    s.cap_v(x + 400, yl, yl + 40, "1 µF", "opt")
    s.gnd(x + 400, yl + 40, "logic", label=False)
    s.wire([(x + 230, yl + 40), (x + 260, yl + 40), (x + 260, yl + 46)], "gnd")
    s.gnd(x + 260, yl + 46, "logic")
    s.text(x + 236, yl - 4, "C", 9, bold=True)
    s.text(x + 236, yl + 36, "E", 9, bold=True)
    s.lines(x + 560, y + 44, ["E = logic GND, C = PA7 node: low = driver powered.",
                              "Only with this fitted: drv.pwr_sense_enable = 1",
                              "(SAVE + reboot) → power loss stops motion ≤ 25 ms.",
                              "Release 1: unwired, PA7 reads high and is IGNORED",
                              "(default 0); K1_WELDED never latches (D-43 e)."], 9.5)

    # ---------------- ENA diode OR (right column)
    rx, ry = 980, 70
    s.rect(rx, ry, 480, 395, stroke="#b0006a", fill="#fff", sw=1.5, rx=4)
    s.text(rx + 8, ry + 18, "ENA: MCU drive OR hardwired E-stop cut (D-42, wiring §2.1)", 11.5, bold=True,
           color=C_TITLE)
    # MCU path
    ya = ry + 75
    s.text(rx + 8, ya - 28, "MCU drive: stage 1 = PA4 direct · stage 2 = SN74ACT244 (5 V) output", 9.5)
    s.flag(rx + 90, ya, "PA4 / U1", "right", "sig", 9)
    s.wire([(rx + 90, ya), (rx + 170, ya)], "sig")
    s.mpoint(rx + 130, ya, "M-3", dx=-10, dy=19)
    s.diode_h(rx + 170, rx + 230, ya, "D1 BAT54 / BAT43", "sig", "right")
    s.wire([(rx + 230, ya), (rx + 300, ya)], "safety")
    s.dot(rx + 300, ya, "safety")
    # E-stop path
    yb = ya + 95
    s.flag(rx + 62, yb, "+5V_CUT", "right", "lpwr", 9)
    s.switch_h(rx + 62, rx + 135, yb, "NO", "S0 NO", "safety", above=False)
    s.res_h(rx + 135, rx + 215, yb, "R_E 68 Ω 0.5 W", "safety")
    s.mpoint(rx + 175, yb + 14, "M-1", dx=8, dy=10)
    s.diode_h(rx + 215, rx + 270, yb, "D2", "safety", "right")
    s.wire([(rx + 270, yb), (rx + 300, yb), (rx + 300, ya)], "safety")
    s.text(rx + 8, yb + 40, "E-stop path (D-42): S0 NO contact, no MCU element", 9.5, bold=True, color=STY["safety"][0])
    # node -> PD and ENA+
    s.dot(rx + 300, ya + 40, "safety")
    s.wire([(rx + 300, ya + 40), (rx + 340, ya + 40)], "gnd")
    s.res_v(rx + 340, ya + 40, ya + 90, "10 kΩ", "gnd")
    s.gnd(rx + 340, ya + 90, "logic", label=False)
    s.wire([(rx + 300, ya), (rx + 395, ya)], "safety")
    s.mpoint(rx + 375, ya, "M-4", dx=-10, dy=19)
    # driver opto
    s.rect(rx + 395, ya - 22, 75, 150, stroke="#333", fill="#f5f5f5", sw=1.2, rx=2)
    s.text(rx + 432, ya - 8, "HBS86H", 9.5, anchor="middle", bold=True)
    s.text(rx + 400, ya + 4, "ENA+", 9.5, bold=True)
    s.text(rx + 432, ya + 30, "≈ 270 Ω", 9, anchor="middle")
    s.text(rx + 432, ya + 44, "+ LED", 9, anchor="middle")
    s.text(rx + 432, ya + 58, "(1.2 V)", 9, anchor="middle")
    s.text(rx + 400, ya + 122, "ENA−", 9.5, bold=True)
    s.res_v(rx + 432, ya + 128, ya + 178, "M-2: 100 Ω shunt", "gnd", right=False)
    s.mpoint(rx + 445, ya + 153, "")
    s.gnd(rx + 432, ya + 178, "logic", label=False)
    s.assumed(rx + 8, ry + 278, "internal 270 Ω / LED 1.2 V / Schottky 0.3 V / R_E / D1 / D2 ASSUMED")
    s.lines(rx + 8, ry + 300, [
        "No contention: each source sees the other through a reverse-biased diode;",
        "PA4 (TC, not 5 V tolerant) never sees the 5 V. +5V_CUT comes from the 48 V",
        "bus (isolated DC-DC), so the cut works with the MCU in reset.",
        "10 kΩ pull-down: MCU in reset + E-stop released → no LED current →",
        "driver enabled / holding (D-13). The NO wire is NOT monitored (R-10)",
        "→ periodic check C-25 at the start of every test campaign (D-54 e)."], 9.5)
    # condition table
    ty = 478
    s.rect(rx, ty, 480, 152, stroke="#999", fill="#fff", sw=1.2, rx=4)
    s.text(rx + 8, ty + 17, "ENA LED current per condition (ASSUMED values, wiring §2.1)", 11, bold=True, color=C_TITLE)
    rows = [("MCU 'enabled', E-stop released", "0 mA → driver enabled"),
            ("MCU 'disabled', E-stop released", "st.1 ≈ 6.7 mA / st.2 ≈ 13 mA"),
            ("E-stop pressed, MCU reset / off / 'enabled'", "≈ 10.4 mA → DISABLED"),
            ("E-stop pressed, MCU 'disabled'", "≈ 10…13 mA (higher wins)"),
            ("ENA+ shorted to GND (fault)", "≈ 69 mA from +5V_CUT")]
    for i, (a, b) in enumerate(rows):
        yy = ty + 38 + i * 22
        s.text(rx + 8, yy, a, 9.5)
        s.text(rx + 472, yy, b, 9.5, anchor="end", bold=i == 2)
        if i:
            s.el.append(f'<line x1="{rx + 6}" y1="{yy - 15}" x2="{rx + 474}" y2="{yy - 15}" stroke="#ddd"/>')
    # DMM table
    my = 642
    s.rect(rx, my, 480, 270, stroke="#d32f2f", fill="#fff", sw=1.2, rx=4)
    s.text(rx + 8, my + 17, "Multimeter points (wiring §11.1, vs logic GND)", 11, bold=True, color="#d32f2f")
    dmm = [("M-1", "across R_E (I = U / 68 Ω)", "pressed, MCU in reset", "≈ 0.7 V (≥ 7 mA)"),
           ("M-2", "100 Ω shunt in ENA−", "a) enabled+rel. b) reset+pr. c) dis.+pr.", "0 / 8…10 / ≤ 13 mA"),
           ("M-3", "PA4 pin (CN8-3)", "every M-2 case", "≤ 3.4 V always"),
           ("M-4", "ENA+ terminal", "pressed / released+en. / +dis.", "4.0–4.6 / 0 / 3.0 (4.7) V"),
           ("M-5", "+5V_CUT; 48 V GND ↔ logic GND", "bus on; bus off (Ω)", "4.75–5.25 V; ≥ 10 MΩ"),
           ("M-6", "PA10 pin (CN10-33)", "released / pressed", "≤ 0.4 V / ≥ 2.3 V"),
           ("M-7", "PA10 node, J-STIM 220 Ω", "STIM idle / active", "≈ 0.6 V / ≈ 3.3 V"),
           ("M-8", "I_LED PUL / DIR / ENA", "active level held (C-06)", "≥ 6 mA; buffer 10–13"),
           ("M-9", "PA7 (only if sense fitted)", "48 V on / off", "≤ 0.4 V / ≈ 3.3 V")]
    s.text(rx + 8, my + 36, "#", 9, bold=True)
    s.text(rx + 44, my + 36, "point", 9, bold=True)
    s.text(rx + 214, my + 36, "condition", 9, bold=True)
    s.text(rx + 472, my + 36, "expected", 9, bold=True, anchor="end")
    for i, (m, p, c, e) in enumerate(dmm):
        yy = my + 56 + i * 23
        s.text(rx + 8, yy, m, 9.5, bold=True, color="#d32f2f")
        s.text(rx + 44, yy, p, 8.8)
        s.text(rx + 214, yy, c, 8.8)
        s.text(rx + 472, yy, e, 8.8, anchor="end")
    s.legend([("lpwr", "logic supply 3V3 / +5V_CUT"), ("gnd", "logic GND"), ("sig", "signal"),
              ("safety", "safety: E-stop / limits / ENA cut"), ("p48", "+48 V"), ("opt", "optional / not fitted")],
             y=752, cols=3, colw=300)
    s.lines(77, 830, ["All input cells sit on the interface board (stage 1 perfboard, stage 2 SN74ACT244 shield,",
                      "R5 §5.2 Option A) with pluggable terminals; the internal MCU pull-up stays on as a backup.",
                      "Values from wiring.md §3, §5, §6, §7; parts are examples (1 % metal film, C0G / X7R)."], 9.5,
            color="#555")
    s.assumed(77, 885, "component examples (S0 Schneider ZB5AS844 + ZBE102 NC + ZBE101 NO, or Eaton M22) ASSUMED")
    s.title_block()
    return s


# ================================================================================================ sheet 4
def sheet4() -> Sheet:
    s = Sheet(4, "HBS86H terminals, DIP switches (D-27), driver supply")
    s.frame()
    # driver box with terminal strips
    dx, dy, dw, dh = 470, 80, 230, 720
    s.rect(dx, dy, dw, dh, stroke="#333", fill="#fff", sw=1.8, rx=4)
    s.text(dx + dw / 2, dy + 20, "PFDE HBS86H", 13, anchor="middle", bold=True, color=C_TITLE)
    s.text(dx + dw / 2, dy + 36, "Leadshine-compatible clone (D-16)", 9.5, anchor="middle")
    s.text(dx + dw / 2, dy + 50, "24–100 VDC / 18–70 VAC, run at 48 V DC", 9.5, anchor="middle")

    def strip(title: str, y0: float, names: list[str], side: str) -> list[float]:
        ys = []
        xx = dx if side == "left" else dx + dw
        s.text(dx + (12 if side == "left" else dw - 12), y0 - 8, title, 10.5, bold=True,
               anchor="start" if side == "left" else "end", color="#444")
        for i, n in enumerate(names):
            y = y0 + 8 + i * 22
            s.rect(xx - 6, y - 7, 12, 14, stroke="#333", fill="#cfd8dc", sw=1)
            s.text(xx + (12 if side == "left" else -12), y + 4, n, 10.5, bold=True,
                   anchor="start" if side == "left" else "end")
            ys.append(y)
        return ys

    sig = strip("Signal (label order)", 160, ["PUL+", "PUL−", "DIR+", "DIR−", "ENA+", "ENA−"], "left")
    out = strip("OUT", 318, ["PEND+", "PEND−", "ALM+", "ALM−"], "left")
    rs = strip("RS232 tuning", 432, ["GND", "TX", "RX"], "left")
    pwr = strip("Power", 160, ["+Vdc", "GND"], "right")
    mot = strip("Motor", 250, ["A+", "A−", "B+", "B−"], "right")
    enc = strip("Encoder", 380, ["EB+", "EB−", "EA+", "EA−"], "right")
    s.text(dx + dw - 12, 490, "+ encoder supply per the", 9, anchor="end")
    s.text(dx + dw - 12, 502, "driver manual", 9, anchor="end")
    s.assumed(dx + 60, 524, "encoder supply ASSUMED", 8.5)
    s.text(dx + dw / 2, 545, "opto barrier ≥ 500 MΩ", 9.5, anchor="middle", italic=True, color="#555")
    s.text(dx + dw / 2, 559, "Signal / OUT = logic side", 9.5, anchor="middle", italic=True, color="#555")
    s.text(dx + dw / 2, 573, "Power / Motor / Encoder = 48 V side", 9.5, anchor="middle", italic=True, color="#555")
    s.gnd(dx + 30, 590, "pe", label=False)
    s.text(dx + 44, 604, "PE: driver earth (motor shield)", 9)
    s.text(dx + 12, 640, "ALM reset = 48 V PSU off / on", 9.5, bold=True)
    s.text(dx + 12, 654, "(no RESET button, D-41; D-54 d)", 9.5)
    s.text(dx + 12, 690, "DIP block SW1…SW8 (right) —", 9.5, bold=True)
    s.text(dx + 12, 704, "set ONLY with the driver unpowered", 9.5, bold=True, color="#b71c1c")

    # left side connections
    lx = dx - 8
    conn_sig = [("← PA0 PUL (stage 2: U1 output)", "sig"), ("→ logic GND", "gnd"),
                ("← PA1 DIR (stage 2: U1 output)", "sig"), ("→ logic GND", "gnd"),
                ("← D1 (PA4 / U1) OR D2 (S0 NO, R_E, +5V_CUT)", "safety"), ("→ logic GND (M-2 shunt here)", "gnd")]
    for (txt, st), y in zip(conn_sig, sig):
        s.wire([(lx, y), (lx - 60, y)], st)
        s.text(lx - 64, y + 4, txt, 9.5, anchor="end", color=STY[st][0] if st != "sig" else "#000")
    s.text(lx - 64, sig[5] + 18, "ENA+ also: 10 kΩ pull-down → logic GND (sheet 3)", 9, anchor="end", color="#555")
    conn_out = [("→ cell A → PA9 PEND", "sig"), ("→ logic GND", "gnd"), ("→ cell A → PA8 ALM", "sig"), ("→ logic GND", "gnd")]
    for (txt, st), y in zip(conn_out, out):
        s.wire([(lx, y), (lx - 60, y)], st)
        s.text(lx - 64, y + 4, txt, 9.5, anchor="end", color=STY[st][0] if st != "sig" else "#000")
    for y in rs:
        s.wire([(lx, y), (lx - 30, y)], "opt")
    s.text(lx - 34, rs[1] + 4, "not connected (factory settings, D-28)", 9.5, anchor="end", color="#777")
    s.lines(40, 520, ["Control cable (Signal + OUT): shielded twisted pairs,",
                      "e.g. LiYCY 6×2×0.25 mm²: PUL+/PUL−, DIR+/DIR−,",
                      "ENA+/ENA−, ALM+/ALM−, PEND+/PEND−; ≤ 1–2 m,",
                      "≥ 10 cm from motor / 48 V wires, cross at 90°;",
                      "shield → logic GND at the board end only."], 10)
    s.lines(40, 600, ["Polarity (Leadshine, ASSUMED for the clone, C-07):",
                      "· MCU pin high = LED current",
                      "· PUL: pulse = LED current (motion.pul_invert 0)",
                      "· ENA: LED current = DISABLED, none = enabled",
                      "· DIR: level set at the HW gate (motion.dir_invert)"], 10)
    s.assumed(40, 678, "ENA polarity of the PFDE clone ASSUMED")
    s.lines(40, 704, ["Timing (D-16 / D-28 / D-45 e):",
                      "· PUL high / low ≥ 10 µs (FW default 12.5 µs)",
                      "· step rate ≤ 50 kHz (FW default 40 kHz)",
                      "· DIR setup ≥ 20 µs",
                      "· ENA / power → first pulse ≥ 500 ms"], 10)

    # right side: power
    rx = dx + dw + 8
    s.wire([(rx, pwr[0]), (rx + 70, pwr[0])], "p48")
    s.wire([(rx, pwr[1]), (rx + 70, pwr[1])], "g48")
    s.rect(rx + 70, pwr[0] - 12, 70, 24, stroke="#333", fill="#fff", sw=1.4, rx=2)
    s.text(rx + 105, pwr[0] + 4, "F1 T10A", 10, anchor="middle", bold=True)
    s.wire([(rx + 140, pwr[0]), (rx + 200, pwr[0])], "p48")
    s.wire([(rx + 70, pwr[1]), (rx + 200, pwr[1])], "g48")
    s.rect(rx + 200, pwr[0] - 30, 170, 76, stroke="#333", fill="#fff", sw=1.6, rx=3)
    s.text(rx + 208, pwr[0] - 13, "48 V PSU SDR-480-48", 10.5, bold=True, color=C_TITLE)
    s.text(rx + 208, pwr[0] + 2, "set-point 48 V (not 55 V)", 9.5)
    s.text(rx + 208, pwr[0] + 16, "10 A, 15 A peak 3 s, DC-OK", 9.5)
    s.text(rx + 208, pwr[0] + 30, "mains switch = power removal", 9.5, bold=True)
    s.wire([(rx + 370, pwr[0]), (rx + 400, pwr[0])], "mains")
    s.text(rx + 404, pwr[0] + 4, "230 VAC via Q0 MCB", 9.5)
    s.gnd(rx + 285, pwr[0] + 46, "pe")
    s.text(rx + 8, pwr[1] + 18, "1.5 mm² twisted pair ≤ 1–2 m,", 9.5)
    s.text(rx + 8, pwr[1] + 31, "no contactor (D-41), no bulk C", 9.5, color="#555")
    s.flag(rx + 160, pwr[0] - 40, "+48V → DC-DC (+5V_CUT)", "right", "p48", 8.5)
    s.wire([(rx + 160, pwr[0]), (rx + 160, pwr[0] - 40)], "p48")
    s.dot(rx + 160, pwr[0], "p48")
    # motor
    colors = ["A+", "A−", "B+", "B−"]
    for n, y in zip(colors, mot):
        s.wire([(rx, y), (rx + 150, y)], "motor")
    s.wire([(rx + 20, mot[0] - 12), (rx + 135, mot[0] - 12)], "shield")
    s.wire([(rx + 20, mot[-1] + 12), (rx + 135, mot[-1] + 12)], "shield")
    s.rect(rx + 150, mot[0] - 20, 140, 96, stroke="#333", fill="#fff", sw=1.6, rx=3)
    s.text(rx + 158, mot[0] - 3, "86HS2140-06-001", 10.5, bold=True, color=C_TITLE)
    s.text(rx + 158, mot[0] + 12, "SIHONG, 8.2 N·m,", 9.5)
    s.text(rx + 158, mot[0] + 26, "6.0 A, 0.5 Ω, 140 mm", 9.5)
    s.text(rx + 158, mot[0] + 40, "phase colours: per", 9.5)
    s.text(rx + 158, mot[0] + 54, "motor label / manual", 9.5)
    s.text(rx + 300, mot[0] + 4, "shielded cable;", 9.5)
    s.text(rx + 300, mot[0] + 18, "shield → driver PE", 9.5)
    s.text(rx + 300, mot[0] + 32, "at the driver end", 9.5)
    for y in enc:
        s.wire([(rx, y), (rx + 150, y)], "sig")
    s.rect(rx + 150, enc[0] - 12, 140, 80, stroke="#333", fill="#fff", sw=1.4, rx=3)
    s.text(rx + 158, enc[0] + 4, "motor encoder", 10.5, bold=True, color=C_TITLE)
    s.text(rx + 158, enc[0] + 19, "1000 lines (D-23)", 9.5)
    s.text(rx + 158, enc[0] + 34, "separate cable,", 9.5)
    s.text(rx + 158, enc[0] + 49, "away from phases", 9.5)
    s.text(rx + 300, enc[0] + 4, "used in CLOSED loop only", 9.5, bold=True)
    s.text(rx + 300, enc[0] + 18, "(target SW7 off, SW8 on);", 9.5)
    s.text(rx + 300, enc[0] + 32, "unused with SW7/SW8", 9.5)
    s.text(rx + 300, enc[0] + 46, "off / off (open loop)", 9.5)

    # DIP table
    tx, ty = 740, 470
    s.rect(tx, ty, 720, 330, stroke="#333", fill="#fff", sw=1.4, rx=4)
    s.text(tx + 8, ty + 19, "DIP switches SW1…SW8 (D-27; 1 = ON) — apply the TARGET with the driver UNPOWERED", 11.5,
           bold=True, color=C_TITLE)
    hdr = ["switch", "function (driver label)", "found (photo)", "TARGET", "consequence"]
    xs = [tx + 8, tx + 64, tx + 330, tx + 425, tx + 500]
    for x, h in zip(xs, hdr):
        s.text(x, ty + 40, h, 10, bold=True)
    for i, (sw, fn, cur, tgt) in enumerate(DIP):
        y = ty + 60 + i * 21
        s.text(xs[0], y, sw, 10, bold=True)
        s.text(xs[1], y, fn, 9.5)
        s.text(xs[2], y, cur, 10, color="#777")
        s.text(xs[3], y, tgt, 10.5, bold=True, color="#1b5e20" if tgt == "ON" else "#000")
        if i:
            s.el.append(f'<line x1="{tx + 4}" y1="{y - 15}" x2="{tx + 716}" y2="{y - 15}" stroke="#e0e0e0"/>')
    cons = [(0, "SW1–4: 800 → 4000 pulses/rev"), (1, "160 → 800 steps/mm (5 mm lead)"),
            (2, "motion.steps_per_mm default 800;"), (3, "travel wizard measures the real value"),
            (4, "+x away from START via motion.dir_invert"), (5, "unchanged, state recorded (HG-19)"),
            (6, "off/off = 86 OPEN loop 6.0 A →"), (7, "off/on = 86-80/86-118 CLOSED loop")]
    for i, t in cons:
        s.text(xs[4], ty + 60 + i * 21, t, 9.5)
    # DIP pictogram
    py = ty + 245
    s.text(tx + 8, py, "TARGET:", 10, bold=True)
    for i, (sw, fn, cur, tgt) in enumerate(DIP):
        x = tx + 80 + i * 32
        s.rect(x, py - 30, 22, 44, stroke="#333", fill="#eceff1", sw=1.2, rx=2)
        on = tgt == "ON"
        s.rect(x + 4, py - 26 if on else py - 6, 14, 16, stroke="#333", fill="#1b5e20" if on else "#fff", sw=1)
        s.text(x + 11, py + 28, str(i + 1), 9.5, anchor="middle")
    s.text(tx + 80 + 8 * 32 + 4, py - 20, "ON", 9.5, bold=True)
    s.text(tx + 80 + 8 * 32 + 4, py + 10, "off", 9.5)
    s.text(tx + 400, py - 18, "= on off on off off on off on", 10.5, bold=True)
    s.text(tx + 400, py, "found 2026-10-03: off on on on off on off off", 9.5, color="#777")
    s.text(tx + 400, py + 16, "(800 p/rev, CCW, assist on, open loop 6.0 A)", 9.5, color="#777")
    s.text(tx + 8, ty + 312, "After the change: the first motion is a short slow jog that checks direction and scale "
           "(D-27, HG-28); record the state in every test report.", 9.5)
    s.text(tx + 8, ty + 326 - 0, "", 1, check=False)

    s.legend([("p48", "+48 V"), ("g48", "0 V(48)"), ("mains", "230 VAC"), ("pe", "PE"), ("motor", "motor phases"),
              ("sig", "signal"), ("safety", "safety (ENA cut)"), ("gnd", "logic GND"), ("shield", "shield"),
              ("opt", "not connected")], y=845, cols=5, colw=190, extra=False)
    s.assumed(77, 905, "terminal names from the driver label (D-16 / D-27); verify against the unit before wiring")
    s.title_block()
    return s


# ================================================================================================ pinout cross-check
def check_pinout(path: Path = PINOUT_MD) -> list[str]:
    """Cross-check PINS / MH against pinout.md tables; returns a list of mismatches (empty = OK)."""
    text = path.read_text(encoding="utf-8")
    errs: list[str] = []
    rows: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\|\s*\*{0,2}(P[A-H]\d{1,2})(?:\s*/\s*P[A-H]\d+)?\*{0,2}\s*\|\s*([^|]*)\|", line)
        if m and m.group(1) not in rows:
            rows[m.group(1)] = m.group(2).strip()
    for pin, ard, morpho, _sig, _dir in PINS:
        hdr = rows.get(pin)
        if hdr is None:
            errs.append(f"{pin}: no row in pinout.md")
            continue
        flat = hdr.replace("*", "")
        if morpho not in flat:
            errs.append(f"{pin}: morpho {morpho!r} not in pinout.md header {flat!r}")
        if ard.strip("()") not in flat:
            errs.append(f"{pin}: Arduino {ard!r} not in pinout.md header {flat!r}")
    fb = rows.get(END_FALLBACK[0], "")
    if END_FALLBACK[1] not in fb:
        errs.append(f"END fallback {END_FALLBACK} not in pinout.md ({fb!r})")
    mh_rows = {}
    for line in text.splitlines():
        m = re.match(r"^\|\s*(J-[A-Z-]+)[^|]*\|\s*([^|]*)\|", line)
        if m:
            mh_rows[m.group(1)] = m.group(2)
    for j, pin, hdr, _frm, ser in MH:
        cell = mh_rows.get(j, "").replace("*", "")
        if pin not in cell:
            errs.append(f"{j}: pin {pin} not in pinout.md §1.5 ({cell!r})")
        for part in hdr.split(" · ")[-1].split(" / "):
            if part not in cell:
                errs.append(f"{j}: header {part!r} not in pinout.md §1.5 ({cell!r})")
    if "220 Ω" not in text:
        errs.append("J-STIM 220 Ω not found in pinout.md")
    return errs


# ================================================================================================ output
SHEETS = [("sheet1_system_overview", sheet1), ("sheet2_nucleo_pins", sheet2),
          ("sheet3_input_cells_ena_or", sheet3), ("sheet4_driver_dip_supply", sheet4)]


def render_png(svg: Path, png: Path, scale: float = 2.0) -> None:
    """Render with PySide6 QtSvg (project .venv) at `scale` x the drawing units, white background."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    r = QSvgRenderer(str(svg))
    if not r.isValid():
        raise RuntimeError(f"invalid SVG: {svg}")
    img = QImage(int(W * scale), int(H * scale), QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.white)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    r.render(p, QRectF(0, 0, W * scale, H * scale))
    p.end()
    if not img.save(str(png), "PNG"):
        raise RuntimeError(f"cannot write {png}")
    del app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--png", action="store_true", help="also render PNG (needs PySide6, project .venv)")
    ap.add_argument("--check", action="store_true", help="only cross-check the pin table against pinout.md")
    ap.add_argument("--out", default=str(HERE), help="output folder (default: this folder)")
    ap.add_argument("--strict", action="store_true", help="fail on estimated text overlaps")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    errs = check_pinout()
    if errs:
        print("pinout.md cross-check FAILED:\n  " + "\n  ".join(errs))
        return 2
    print(f"pinout.md cross-check: {len(PINS)} pins + {len(MH)} MH jumpers + END fallback OK")
    if args.check:
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bad = 0
    for name, fn in SHEETS:
        sh = fn()
        path = out / f"{name}.svg"
        path.write_text(sh.svg(), encoding="utf-8", newline="\n")
        ov = sh.overlaps()
        bad += len(ov)
        print(f"{path.name}: {len(sh.el)} elements, {len(ov)} estimated text overlaps")
        for o in ov:
            print("   ", o)
        if args.png:
            render_png(path, path.with_suffix(".png"))
            print(f"{path.with_suffix('.png').name}: written")
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
