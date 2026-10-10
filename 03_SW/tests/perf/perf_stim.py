"""External stimulus process for PR-2 / PR-3 (SW_test_plan §6.1; SRS NFR-002, NFR-003).

Runs in its own process (own GIL), so a busy GUI process cannot delay the stimulus time stamps. Every stamp is
``time.monotonic_ns()`` (QueryPerformanceCounter, same time base as the sniffer in ``perf_sim.py``), taken
immediately before the Win32 call that injects the input.

* ``click``: posts ``WM_LBUTTONDOWN`` / ``WM_LBUTTONUP`` to a top-level window at client coordinates (physical
  pixels) — the OS queues it like a mouse press on the STOP button; the real cursor is not moved and no focus is
  taken. Targets rotate (toolbar STOP, dock STOP, …).
* ``key``: ``SendInput`` of the Pause key (VK_PAUSE down + up) into the system input stream — the path of a
  physical key press up to the RegisterHotKey / LL-hook dispatch. The foreground window's process at each press is
  recorded (evidence "another application focused").

Usage:
  python perf_stim.py click --targets "[[hwnd, x, y], ...]" --n 100 --period 0.5 --jitter 0.15 --out clicks.json
  python perf_stim.py key --n 100 --period 1.0 --jitter 0.2 --out keys.json
  (``--start-delay`` s before the first press; ``--gate-file`` path: before each press wait until the file exists
  and delete it — lets the starter re-arm between presses, e.g. clear a HALT.)

Verifies: NFR-002, NFR-003 (measurement tool)
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import sys
import time
from ctypes import wintypes

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
VK_PAUSE = 0x13
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.WinDLL("user32", use_last_error=True)
# Per-monitor DPI aware (v2) like the Qt GUI: otherwise Windows DPI-virtualises the posted client coordinates
# (a DPI-unaware sender's x, y are scaled by the monitor factor, e.g. ×1.25, and the press misses small buttons).
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError, OSError):  # pragma: no cover - older Windows
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def fg_pid() -> int:
    h = user32.GetForegroundWindow()
    pid = wintypes.DWORD(0)
    if h:
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return int(pid.value)


def lparam(x: int, y: int) -> int:
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def send_pause() -> tuple[int, int]:
    arr = (INPUT * 2)()
    arr[0].type = arr[1].type = INPUT_KEYBOARD
    arr[0].u.ki = KEYBDINPUT(VK_PAUSE, 0, 0, 0, 0)
    arr[1].u.ki = KEYBDINPUT(VK_PAUSE, 0, KEYEVENTF_KEYUP, 0, 0)
    n = user32.SendInput(2, arr, ctypes.sizeof(INPUT))
    return n, ctypes.get_last_error()


def wait_gate(path: str | None, timeout_s: float = 30.0) -> tuple[bool, str]:
    """Wait for the gate file, consume it; returns (ok, content). The content may carry the next click target
    ``[hwnd, x, y, k]`` computed by the GUI just before (layouts can move, e.g. when the STOP banner appears)."""
    if not path:
        return True, ""
    t_end = time.monotonic() + timeout_s
    while time.monotonic() < t_end:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    txt = f.read()
                os.remove(path)
            except OSError:
                time.sleep(0.005)
                continue
            return True, txt
        time.sleep(0.01)
    return False, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("click", "key"))
    ap.add_argument("--targets", default="[]")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--period", type=float, default=0.5)
    ap.add_argument("--jitter", type=float, default=0.15)
    ap.add_argument("--start-delay", type=float, default=1.0)
    ap.add_argument("--gate-file", default=None)
    ap.add_argument("--seed", type=int, default=20261008)
    ap.add_argument("--move", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rnd = random.Random(a.seed)
    targets = json.loads(a.targets)
    me = os.getpid()
    out: list[dict] = []
    time.sleep(a.start_delay)
    for i in range(a.n):
        ok, txt = wait_gate(a.gate_file)
        if not ok:
            out.append({"i": i, "error": "gate timeout"})
            break
        tgt = json.loads(txt) if txt.strip().startswith("[") else None
        time.sleep(max(0.0, a.period + rnd.uniform(-a.jitter, a.jitter)))
        fg = fg_pid()
        if a.mode == "click":
            if tgt is not None:
                hwnd, x, y, k = tgt
            else:
                k = i % len(targets)
                hwnd, x, y = targets[k]
            if a.move:                     # hover first (a real click is always preceded by a move)
                user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam(x, y))
                time.sleep(0.05)
            t = time.monotonic_ns()
            ok1 = user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam(x, y))
            time.sleep(0.03)
            ok2 = user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam(x, y))
            out.append({"i": i, "t": t, "target": k, "ok": bool(ok1 and ok2), "fg_pid": fg})
        else:
            t = time.monotonic_ns()
            n, err = send_pause()
            out.append({"i": i, "t": t, "ok": n == 2, "err": err, "fg_pid": fg, "stim_pid": me})
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
