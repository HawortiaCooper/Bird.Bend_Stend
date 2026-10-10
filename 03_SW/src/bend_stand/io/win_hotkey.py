"""System-wide Pause/Break key → HALT (SW-STOP-002, D-14, D-26) on its own Win32 message-loop thread, no Qt
(SW_design §3.1 "Hotkey", §15.6).

* A dedicated daemon thread creates its own message queue and calls ``RegisterHotKey(NULL, id,
  MOD_NOREPEAT | mods, VK_PAUSE)`` for mods ∈ {none, SHIFT, ALT, WIN} and ``RegisterHotKey(NULL, id,
  MOD_NOREPEAT | MOD_CONTROL, VK_CANCEL)`` (Ctrl+Pause = Break). On ``WM_HOTKEY`` it calls the HALT callable
  (``Backend.halt("hotkey")``) **directly from this thread** — a frozen GUI does not disable the key.
* **Fallback**: if the VK_PAUSE registration fails (e.g. another application owns it), the same thread installs
  ``SetWindowsHookExW(WH_KEYBOARD_LL)``; the hook only posts ``WM_APP+1`` to its own thread (HALT runs in the
  message loop, never inside the hook callback). Known limitation KL-01 (D-32 Q28): keys typed into an elevated
  window are not seen.
* **Status** ``REGISTERED`` / ``LL_HOOK`` / ``UNAVAILABLE`` (``HotkeyStatus.mode``); liveness: the Supervisor calls
  ``ping()`` every 250 ms and the thread answers.
* **Test mode** (GRQ-B-15): ``start_test_mode()`` arms a window of ≤ 10 s; a press in it is measured and reported
  (``on_test(delay_ms)``), not acted on; the backend refuses motion while it runs. A press after the window — or
  while the test conditions no longer hold — is a real HALT (fail-safe).

Unit tests inject ``FakeHotkeyBackend``; ``BEND_STAND_HOTKEY=fake|off`` selects the fake / no hotkey (the test
suites set ``fake`` so no test registers a real global hotkey).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/win_hotkey.py @37c87471 (adapted: HALT instead of E-STOP,
callbacks instead of the TS event bus, backend status names REGISTERED / LL_HOOK / UNAVAILABLE, thread priority via
an injected ``thread_init``; Win32 and fake backends copied as-is).

Implements: SW-STOP-002 (system-wide Pause/Break → HALT), NFR-003 (independent of the GUI thread), GRQ-B-15
"""
from __future__ import annotations

import ctypes
import enum
import logging
import queue
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from bend_stand.core.errors import PreconditionError

log = logging.getLogger("bend_stand.io.win_hotkey")

VK_CANCEL = 0x03
VK_PAUSE = 0x13
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_APP = 0x8000
WM_APP_HOOKPRESS = WM_APP + 1        # posted by the LL-hook callback; handled by the message loop (SWD-M1-07)
WH_KEYBOARD_LL = 13
HC_ACTION = 0
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
TEST_MODE_S = 10.0
HALT_KEYS = (VK_PAUSE, VK_CANCEL)
ESTOP_KEYS = HALT_KEYS                     # TS name kept for the copied backends

# (id, modifiers, vk) — ids 1..4 = VK_PAUSE variants, 5 = Ctrl+Pause (VK_CANCEL)
HOTKEYS: tuple[tuple[int, int, int], ...] = (
    (1, MOD_NOREPEAT, VK_PAUSE),
    (2, MOD_NOREPEAT | MOD_SHIFT, VK_PAUSE),
    (3, MOD_NOREPEAT | MOD_ALT, VK_PAUSE),
    (4, MOD_NOREPEAT | MOD_WIN, VK_PAUSE),
    (5, MOD_NOREPEAT | MOD_CONTROL, VK_CANCEL),
)


PING_LIMIT_MS = 750.0                     # SWR-09: 3 missed 250 ms pings → hotkey shown unavailable


class KeyStatus(enum.Enum):
    STARTING = "STARTING"
    ACTIVE_HOTKEY = "ACTIVE (hotkey)"
    ACTIVE_HOOK = "ACTIVE (hook fallback)"
    UNAVAILABLE = "UNAVAILABLE"


Message = tuple[str, int, bool]            # ("hotkey", id, _) / ("ping", 0, _) / ("quit", 0, _)


class HotkeyBackend(ABC):
    """OS abstraction; every method except ``post_*`` is called on the hotkey thread."""

    @abstractmethod
    def thread_init(self) -> None: ...

    @abstractmethod
    def register_hotkeys(self) -> tuple[bool, str]:
        """Register ``HOTKEYS`` (keeps the ones that succeed); True iff all of them succeeded."""

    @abstractmethod
    def registered_ids(self) -> set[int]: ...

    @abstractmethod
    def unregister_hotkeys(self) -> None: ...

    @abstractmethod
    def install_hook(self, on_key: Callable[[int, bool, int], None]) -> tuple[bool, str]:
        """LL keyboard hook; ``on_key(vk, down, mods)`` for VK_PAUSE / VK_CANCEL (mods = MOD_* held)."""

    @abstractmethod
    def uninstall_hook(self) -> None: ...

    @abstractmethod
    def get_message(self) -> Message:
        """Block until the next message (LL-hook callbacks run inside this call on the hotkey thread)."""

    @abstractmethod
    def post_ping(self) -> bool: ...

    @abstractmethod
    def post_hook_press(self, vk: int) -> None:
        """Called inside the LL-hook callback: queue a press for the message loop (``("hookpress", vk, True)``)."""

    @abstractmethod
    def post_quit(self) -> None: ...


# ======================================================================================================
# Win32 backend (ctypes)
# ======================================================================================================
class Win32HotkeyBackend(HotkeyBackend):  # pragma: no cover - exercised by `winint` tests on REF-PC
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Win32HotkeyBackend requires Windows")
        from ctypes import wintypes

        self.wt = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.tid = 0
        self._hook = None
        self._proc = None
        self._registered: list[int] = []
        self._on_key: Callable[[int, bool, int], None] | None = None
        u = self.user32
        u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        u.RegisterHotKey.restype = wintypes.BOOL
        u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        u.GetMessageW.restype = wintypes.BOOL
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                   wintypes.UINT]
        u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.PostThreadMessageW.restype = wintypes.BOOL
        self.LRESULT = ctypes.c_ssize_t
        self.HOOKPROC = ctypes.WINFUNCTYPE(self.LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, self.HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        u.SetWindowsHookExW.restype = wintypes.HHOOK
        u.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        u.CallNextHookEx.restype = self.LRESULT
        u.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        self.KBD = KBDLLHOOKSTRUCT

    def thread_init(self) -> None:
        self.tid = self.kernel32.GetCurrentThreadId()
        msg = self.wt.MSG()
        self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)     # create the thread message queue

    def register_hotkeys(self) -> tuple[bool, str]:
        errs = []
        for hid, mods, vk in HOTKEYS:
            if self.user32.RegisterHotKey(None, hid, mods, vk):
                self._registered.append(hid)
            else:
                errs.append(f"id {hid}: error {ctypes.get_last_error()}")
        return not errs, "; ".join(errs)

    def registered_ids(self) -> set[int]:
        return set(self._registered)

    def _mods_held(self) -> int:
        k = self.user32.GetAsyncKeyState
        m = 0
        if k(0x10) & 0x8000:
            m |= MOD_SHIFT
        if k(0x11) & 0x8000:
            m |= MOD_CONTROL
        if k(0x12) & 0x8000:
            m |= MOD_ALT
        if (k(0x5B) | k(0x5C)) & 0x8000:
            m |= MOD_WIN
        return m

    def unregister_hotkeys(self) -> None:
        for hid in self._registered:
            self.user32.UnregisterHotKey(None, hid)
        self._registered.clear()

    def install_hook(self, on_key: Callable[[int, bool, int], None]) -> tuple[bool, str]:
        self._on_key = on_key
        wt = self.wt
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short

        def proc(n_code: int, w_param: int, l_param: int) -> int:
            try:
                if n_code == HC_ACTION:
                    kb = ctypes.cast(l_param, ctypes.POINTER(self.KBD)).contents
                    if kb.vkCode in ESTOP_KEYS and self._on_key is not None:
                        down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                        self._on_key(int(kb.vkCode), down, self._mods_held())
            except Exception:  # noqa: BLE001 — never block the input chain
                log.exception("LL hook callback failed")
            return self.user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

        self._proc = self.HOOKPROC(proc)
        self._hook = self.user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                                   self.kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            return False, f"SetWindowsHookExW failed: error {ctypes.get_last_error()}"
        del wt
        return True, ""

    def uninstall_hook(self) -> None:
        if self._hook:
            self.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def get_message(self) -> Message:
        msg = self.wt.MSG()
        r = self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r == 0 or r == -1 or msg.message == WM_QUIT:
            return ("quit", 0, False)
        if msg.message == WM_HOTKEY:
            return ("hotkey", int(msg.wParam), True)
        if msg.message == WM_APP:
            return ("ping", 0, False)
        if msg.message == WM_APP_HOOKPRESS:
            return ("hookpress", int(msg.wParam), True)
        return ("other", int(msg.message), False)

    def post_hook_press(self, vk: int) -> None:
        if self.tid:
            self.user32.PostThreadMessageW(self.tid, WM_APP_HOOKPRESS, vk, 0)

    def post_ping(self) -> bool:
        return bool(self.tid and self.user32.PostThreadMessageW(self.tid, WM_APP, 0, 0))

    def post_quit(self) -> None:
        if self.tid:
            self.user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


# ======================================================================================================
# fake backend (test seam, SW_design §16)
# ======================================================================================================
class FakeHotkeyBackend(HotkeyBackend):
    """In-process fake: ``press()`` injects Pause via the registered path (hotkey or hook)."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self.register_ok = True
        self.fail_ids: set[int] = set()              # e.g. {4}: Win+Pause owned by the Windows shell
        self.hook_ok = True
        self.registered = False
        self._ids: set[int] = set()
        self.hook_installed = False
        self._on_key: Callable[[int, bool, int], None] | None = None
        self.passed_on = 0                      # CallNextHookEx calls (hook path)
        self._stalled = threading.Event()
        self._unstall = threading.Event()
        self.pings_posted = 0
        self.hook_presses_posted = 0
        self.in_hook_callback = False
        self.estop_in_hook = 0               # estop() calls made while inside the hook callback (must stay 0)
        self.inject_ns: list[int] = []

    # scenario controls
    def fail_register(self) -> None:
        self.register_ok = False

    def fail_hook(self) -> None:
        self.hook_ok = False

    def stall(self) -> None:
        """The hotkey thread stops answering (blocks inside get_message)."""
        self._unstall.clear()
        self._stalled.set()

    def unstall(self) -> None:
        self._stalled.clear()
        self._unstall.set()

    def press(self, vk: int = VK_PAUSE, mods: int = 0) -> None:
        """Key-down + key-up of an E-STOP key (with modifier mask ``mods``). Like Windows: the LL hook sees
        every key first (and passes it on); a registered hotkey then produces WM_HOTKEY."""
        self.inject_ns.append(time.perf_counter_ns())
        if self.hook_installed:
            self._q.put(("key", vk, True, mods))
            self._q.put(("key", vk, False, mods))
        hid = next((h for h, m, v in HOTKEYS if v == vk and (m & ~MOD_NOREPEAT) == mods), None)
        if hid is not None and hid in self._ids:
            self._q.put(("hotkey", hid, True))

    def hold(self, vk: int = VK_PAUSE, repeats: int = 30) -> None:
        """Key held: one WM_HOTKEY (MOD_NOREPEAT) or ``repeats`` auto-repeat key-downs to the LL hook."""
        self.inject_ns.append(time.perf_counter_ns())
        if self.hook_installed:
            for _ in range(repeats):
                self._q.put(("key", vk, True, 0))
            self._q.put(("key", vk, False, 0))
        hid = 1 if vk == VK_PAUSE else 5
        if hid in self._ids:
            self._q.put(("hotkey", hid, True))

    # backend API
    def thread_init(self) -> None:
        pass

    def register_hotkeys(self) -> tuple[bool, str]:
        if not self.register_ok:
            self._ids = set()
        else:
            self._ids = {h for h, _m, _v in HOTKEYS if h not in self.fail_ids}
        self.registered = bool(self._ids)
        failed = [h for h, _m, _v in HOTKEYS if h not in self._ids]
        return not failed, "; ".join(f"id {h}: error {ERROR_HOTKEY_ALREADY_REGISTERED}" for h in failed)

    def registered_ids(self) -> set[int]:
        return set(self._ids)

    def unregister_hotkeys(self) -> None:
        self.registered = False
        self._ids = set()

    def install_hook(self, on_key: Callable[[int, bool, int], None]) -> tuple[bool, str]:
        if not self.hook_ok:
            return False, "SetWindowsHookExW failed (fake)"
        self._on_key = on_key
        self.hook_installed = True
        return True, ""

    def uninstall_hook(self) -> None:
        self.hook_installed = False

    def get_message(self) -> Message:
        while True:
            m = self._q.get()
            if self._stalled.is_set() and m[0] != "quit":
                self._unstall.wait()                # hung thread: the message is handled only after unstall
            if m[0] == "key":
                if self._on_key is not None:
                    self.in_hook_callback = True
                    try:
                        self._on_key(m[1], m[2], m[3])
                    finally:
                        self.in_hook_callback = False
                self.passed_on += 1                # CallNextHookEx
                continue
            return m

    def post_ping(self) -> bool:
        self.pings_posted += 1
        self._q.put(("ping", 0, False))
        return True

    def post_hook_press(self, vk: int) -> None:
        self.hook_presses_posted += 1
        self._q.put(("hookpress", vk, True))

    def post_quit(self) -> None:
        self._q.put(("quit", 0, False))
        self.unstall()


# ======================================================================================================
# the HALT key service
# ======================================================================================================
MODE_OF = {KeyStatus.ACTIVE_HOTKEY: "REGISTERED", KeyStatus.ACTIVE_HOOK: "LL_HOOK",
           KeyStatus.UNAVAILABLE: "UNAVAILABLE", KeyStatus.STARTING: "UNAVAILABLE"}


class GlobalHaltHotkey:
    """See module docstring. ``halt(source)`` is called directly from the hotkey thread."""

    def __init__(self, halt: Callable[[str], Any], *, backend: HotkeyBackend | None = None,
                 on_status: Callable[[str, str], None] | None = None,
                 on_test: Callable[[float | None], None] | None = None, test_timeout_s: float = TEST_MODE_S,
                 clock_ns: Callable[[], int] = time.monotonic_ns,
                 thread_init: Callable[[], None] | None = None) -> None:
        self._halt = halt
        self.backend = backend
        self.on_status = on_status
        self.on_test = on_test
        self.test_timeout_s = test_timeout_s
        self.clock_ns = clock_ns
        self.thread_init = thread_init
        self.status = KeyStatus.STARTING
        self.status_detail = ""
        self.can_test: Callable[[], bool] = lambda: True
        self._lock = threading.Lock()
        self._test_until_ns: int | None = None
        self._pressed: set[int] = set()
        self._supplement_mods: set[int] | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.last_beat_ns = 0
        self._ping_out_ns = 0                   # SWR-09: time of the oldest unanswered Supervisor ping (0 = none)
        self.presses = 0
        self.halts = 0
        self.test_reports: list[float] = []
        self.latencies_ns: list[int] = []

    # -- lifecycle -----------------------------------------------------------------------------------
    def start(self, timeout: float = 2.0) -> KeyStatus:
        if self._thread is not None:
            return self.status
        if self.backend is None:
            try:
                self.backend = Win32HotkeyBackend()
            except OSError as e:
                self._set_status(KeyStatus.UNAVAILABLE, str(e))
                self._ready.set()
                return self.status
        self._thread = threading.Thread(target=self._run, name="bend-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.status

    def stop(self) -> None:
        b, t = self.backend, self._thread
        if b is not None and t is not None:
            b.post_quit()
            t.join(2.0)
        self._thread = None

    def _run(self) -> None:
        b = self.backend
        assert b is not None
        if self.thread_init is not None:
            try:
                self.thread_init()
            except Exception:  # noqa: BLE001 pragma: no cover
                pass
        try:
            b.thread_init()
            ok, detail = b.register_hotkeys()
            ids = b.registered_ids()
            if ok:
                self._set_status(KeyStatus.ACTIVE_HOTKEY, detail)
            elif 1 in ids:
                missing = {h for h, _m, _v in HOTKEYS} - ids
                self._supplement_mods = {m & ~MOD_NOREPEAT for h, m, _v in HOTKEYS if h in missing}
                ok2, d2 = b.install_hook(self._on_hook_key)
                note = "missing variants covered by LL hook" if ok2 else "missing variants unavailable: " + d2
                self._set_status(KeyStatus.ACTIVE_HOTKEY, f"{detail} — {note}")
            else:
                log.warning("RegisterHotKey(VK_PAUSE) failed (%s) — installing LL keyboard hook", detail)
                b.unregister_hotkeys()
                ok2, d2 = b.install_hook(self._on_hook_key)
                if ok2:
                    self._set_status(KeyStatus.ACTIVE_HOOK, detail)
                else:
                    self._set_status(KeyStatus.UNAVAILABLE, f"{detail}; {d2}")
            self._ready.set()
            self._answer_ping()
            while True:
                kind, _arg, _down = b.get_message()
                if kind == "quit":
                    break
                if kind == "hotkey":
                    self.on_press("hotkey")
                elif kind == "hookpress":
                    self.on_press("hook")
                elif kind == "ping":
                    self._answer_ping()
        except Exception:  # noqa: BLE001
            log.exception("Pause/Break hotkey thread failed")
        finally:
            try:
                b.unregister_hotkeys()
                b.uninstall_hook()
            finally:
                self._set_status(KeyStatus.UNAVAILABLE, "hotkey thread ended")
                self._ready.set()

    def _set_status(self, st: KeyStatus, detail: str = "") -> None:
        old = self.status
        self.status = st
        self.status_detail = detail
        cb = self.on_status
        if old != st and cb is not None:
            cb(MODE_OF[st], detail)

    def _answer_ping(self) -> None:
        self.last_beat_ns = self.clock_ns()
        self._ping_out_ns = 0

    # -- status -----------------------------------------------------------------------------------------
    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def responding(self) -> bool:
        """SWR-09 (SW_design §4.6): False when a Supervisor ping stays unanswered > ``PING_LIMIT_MS``."""
        out = self._ping_out_ns
        return not out or (self.clock_ns() - out) / 1e6 <= PING_LIMIT_MS

    @property
    def available(self) -> bool:
        return self.alive and self.responding and self.status in (KeyStatus.ACTIVE_HOTKEY, KeyStatus.ACTIVE_HOOK)

    @property
    def mode(self) -> str:
        # Implements: SW-STOP-002 (hotkey shown unavailable when its thread does not answer, SWR-09)
        return MODE_OF[self.status] if self.alive and self.responding else "UNAVAILABLE"

    def ping(self) -> None:
        """Supervisor liveness ping (every 250 ms); answered on the hotkey thread."""
        b = self.backend
        if b is not None and self.alive:
            if not self._ping_out_ns:
                self._ping_out_ns = self.clock_ns()
            b.post_ping()

    def beat_age_ms(self) -> float | None:
        return None if not self.last_beat_ns else (self.clock_ns() - self.last_beat_ns) / 1e6

    # -- key handling (hotkey thread) ---------------------------------------------------------------
    def _on_hook_key(self, vk: int, down: bool, mods: int = 0) -> None:
        if vk not in HALT_KEYS:
            return
        sup = self._supplement_mods
        if sup is not None and not any(mods & m for m in sup if m):
            return
        if down:
            if vk in self._pressed:
                return                          # auto-repeat
            self._pressed.add(vk)
            assert self.backend is not None
            self.backend.post_hook_press(vk)
        else:
            self._pressed.discard(vk)

    def on_press(self, source: str) -> None:
        """One key press (hotkey thread, or ``test_hooks.hotkey_press()`` on the lockstep clock)."""
        t0 = time.perf_counter_ns()
        self.presses += 1
        with self._lock:
            test = self._test_until_ns is not None and self.clock_ns() < self._test_until_ns
            self._test_until_ns = None
        if test:
            try:
                still_ok = bool(self.can_test())
            except Exception:  # noqa: BLE001
                still_ok = False
            if still_ok:
                delay_ms = (time.perf_counter_ns() - t0) / 1e6
                self.test_reports.append(delay_ms)
                if self.on_test is not None:
                    self.on_test(delay_ms)
                return
        self.halts += 1
        try:
            self._halt("hotkey")
        finally:
            self.latencies_ns.append(time.perf_counter_ns() - t0)

    # -- test mode (GRQ-B-15) -------------------------------------------------------------------------
    def start_test_mode(self) -> None:
        if not self.available:
            raise PreconditionError(None, "Pause/Break key unavailable")
        with self._lock:
            self._test_until_ns = self.clock_ns() + int(self.test_timeout_s * 1e9)

    @property
    def test_mode_active(self) -> bool:
        with self._lock:
            u = self._test_until_ns
            if u is None:
                return False
            if self.clock_ns() < u:
                return True
            self._test_until_ns = None
        if self.on_test is not None:
            self.on_test(None)                  # expired without a press
        return False

    def cancel_test_mode(self) -> None:
        with self._lock:
            self._test_until_ns = None


__all__ = ["GlobalHaltHotkey", "FakeHotkeyBackend", "Win32HotkeyBackend", "HotkeyBackend", "KeyStatus", "VK_PAUSE",
           "VK_CANCEL", "HOTKEYS", "MODE_OF"]
