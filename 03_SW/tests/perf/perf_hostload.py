"""Host-load sampler for the perf runs (MC3-6: a LINK LOST is judged together with the host load).

Pure ctypes (no psutil): total CPU % (``GetSystemTimes``), available RAM (``GlobalMemoryStatusEx``), and for every
process whose image name matches ``WATCH`` (other Python interpreters, FW twin, compilers): CPU % over the sample
interval and working set. Processes of this run (``own_pids``) are reported separately.

Verifies: NFR-004 (soak evidence), MC3-6 (measurement tool)
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

WATCH = ("python", "pythonw", "gcc", "g++", "ld", "pio", "platformio", "ninja", "cmake", "msbuild", "cl", "node")
WATCH_PREFIX = ("fw_twin", "cc1", "arm-none-eabi", "python3")

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class FILETIME(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    @property
    def v(self) -> int:
        return (self.hi << 32) | self.lo


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64), ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]


class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FILETIME)] * 4
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]


def _processes() -> list[tuple[int, str]]:
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    out: list[tuple[int, str]] = []
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        out.append((int(e.th32ProcessID), str(e.szExeFile)))
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def _proc_info(pid: int) -> tuple[int, int] | None:
    """(cpu time in 100 ns units, working set bytes) or None."""
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        c, e, kt, ut = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
            return None
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        return kt.v + ut.v, int(pmc.WorkingSetSize)
    finally:
        k32.CloseHandle(h)


def working_set(pid: int | None = None) -> int:
    """Working set of ``pid`` (default: this process) in bytes."""
    if pid is None:
        h = k32.GetCurrentProcess()
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        return int(pmc.WorkingSetSize)
    info = _proc_info(pid)
    return 0 if info is None else info[1]


class HostLoad:
    def __init__(self, own_pids: set[int] | None = None) -> None:
        self.own = set(own_pids or ())
        self.ncpu = max(1, __import__("os").cpu_count() or 1)
        self._sys = self._system_times()
        self._t = time.monotonic()
        self._cpu: dict[int, int] = {}
        self.sample()

    @staticmethod
    def _system_times() -> tuple[int, int]:
        i, k, u = FILETIME(), FILETIME(), FILETIME()
        k32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
        return i.v, k.v + u.v           # kernel time includes idle

    def sample(self) -> dict:
        now = time.monotonic()
        dt = max(1e-3, now - self._t)
        idle, busy_all = self._system_times()
        d_idle, d_all = idle - self._sys[0], busy_all - self._sys[1]
        cpu = 100.0 * (1.0 - d_idle / d_all) if d_all > 0 else float("nan")
        self._sys, self._t = (idle, busy_all), now
        ms = MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        k32.GlobalMemoryStatusEx(ctypes.byref(ms))
        procs, own, every = [], [], []
        cpu_new: dict[int, int] = {}
        for pid, name in _processes():
            if pid == 0:
                continue
            base = name.lower().removesuffix(".exe")
            info = _proc_info(pid)
            if info is None:
                continue
            cpu_new[pid] = info[0]
            prev = self._cpu.get(pid)
            pct = None if prev is None else round(100.0 * (info[0] - prev) / 1e7 / dt / self.ncpu, 1)
            rec = {"pid": pid, "name": name, "cpu_pct_of_host": pct, "ws_mb": round(info[1] / 2**20, 1)}
            every.append(rec)
            if pid in self.own:
                own.append(rec)
            elif base in WATCH or base.startswith(WATCH_PREFIX):
                procs.append(rec)
        self._cpu = cpu_new
        top = sorted((r for r in every if r["pid"] not in self.own and r["cpu_pct_of_host"]),
                     key=lambda r: -r["cpu_pct_of_host"])[:6]
        return {"t_mono": now, "cpu_total_pct": round(cpu, 1), "mem_avail_mb": int(ms.ullAvailPhys // 2**20),
                "mem_load_pct": int(ms.dwMemoryLoad), "own": own, "others": procs,
                "top": [f"{r['name']}:{r['pid']}:{r['cpu_pct_of_host']}" for r in top],
                "others_python": sum(1 for p in procs if p["name"].lower().startswith("python")),
                "others_cpu_pct_of_host": round(sum(p["cpu_pct_of_host"] or 0.0 for p in procs), 1)}


def main(argv: list[str] | None = None) -> int:
    """Sampler process: ``python perf_hostload.py --out load.jsonl --period 5 --own 123,456`` — one JSON line per
    sample until stdin reaches EOF (the starter closes the pipe) or ``--duration`` s elapsed. A separate process,
    so the ~20 ms process scan never runs in the measured GUI process."""
    import argparse
    import json
    import sys
    import threading

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--period", type=float, default=5.0)
    ap.add_argument("--own", default="")
    ap.add_argument("--duration", type=float, default=0.0)
    a = ap.parse_args(argv)
    own = {int(p) for p in a.own.split(",") if p.strip()}
    h = HostLoad(own)
    stop = threading.Event()

    def watch_stdin() -> None:
        try:
            for line in sys.stdin:
                if line.strip() == "quit":
                    break
        except (OSError, ValueError):
            pass
        stop.set()

    if a.duration <= 0:
        threading.Thread(target=watch_stdin, daemon=True).start()
    t_end = time.monotonic() + a.duration if a.duration > 0 else float("inf")
    with open(a.out, "a", encoding="utf-8", buffering=1) as f:
        while not stop.wait(a.period) and time.monotonic() < t_end:
            s = h.sample()
            s["t_ns"] = time.monotonic_ns()
            f.write(json.dumps(s, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
