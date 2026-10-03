"""Validator F harness — the ONLY validation module that names members of the backend API (SW_design §15),
the test hooks (§12.4 a–h) and the simulator control (vocabulary v2). If B changes the API, only this file
follows (SW_test_plan §2.4).

Wire ground truth: every wire-log entry is re-parsed with the Integrator's oracle ``ref_codec`` (never with the
production codec) and returned as :class:`W` records.

Verifies: (infrastructure)
"""
from __future__ import annotations

import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ref_codec as rc

SW_ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


# =============================================================================================== backend

def backend_cls():
    from bend_stand.core.backend import Backend, BackendSettings  # noqa: PLC0415

    return Backend, BackendSettings


def lockstep_backend(*, connect: bool = True, endpoint: str = "sim", seed: int = 7, wire_log: bool = True,
                     **kw: Any):
    Backend, BackendSettings = backend_cls()
    be = Backend(BackendSettings(clock="lockstep", test_hooks=True, wire_log=wire_log, seq_seed=seed, **kw))
    be.start()
    if connect:
        be.test_hooks.result(be.connect_async(endpoint), 5000)
        be.test_hooks.advance(50)
    return be


def realtime_backend(**kw: Any):
    Backend, BackendSettings = backend_cls()
    be = Backend(BackendSettings(wire_log=True, test_hooks=True, **kw))
    be.start()
    return be


def advance(be, ms: float, step_ms: float = 1.0) -> None:
    be.test_hooks.advance(ms, step_ms)


def result(be, fut, timeout_ms: float = 10_000.0):
    return be.test_hooks.result(fut, timeout_ms)


def run_until(be, pred, timeout_ms: float = 5000.0, step_ms: float = 1.0) -> bool:
    return be.test_hooks.run_until(pred, timeout_ms, step_ms)


def wait_rt(pred, timeout_s: float = 10.0, poll_s: float = 0.02) -> bool:
    t_end = time.monotonic() + timeout_s
    while time.monotonic() < t_end:
        if pred():
            return True
        time.sleep(poll_s)
    return bool(pred())


def now_ns(be) -> int:
    return be.clock.monotonic_ns()


def status(be):
    return be.status()


def link_state(be) -> str:
    return str(be.status().link.state.value)


def compat(be) -> set[str]:
    c = be.status().link.compat
    return {f.name for f in type(c) if f.value and (c & f) == f}


def stats(be):
    return be.status().link.stats


def gate(be, name: str):
    from bend_stand.core.model import GateId  # noqa: PLC0415

    return be.status().gates[GateId[name]]


def indicator(be, key: str):
    return be.status().indicators[key]


def act(be, action: str, **args: Any) -> dict:
    r = be.sim.act(action, **args)
    assert r.get("ok"), r
    return r


def sim_query(be, what: str) -> dict:
    return act(be, "query", what=what)


def sim_sent(be) -> list[dict]:
    return sim_query(be, "sent")["frames"]


def inject_nack(be, cmd: str, status: str, detail: int = 0, count: int = 1) -> None:
    be.sim.inject_nack(cmd, status, detail, count)


def inject_store_mismatch(be, key: str, stored: Any) -> None:
    be.sim.inject_store_mismatch(key, stored)


def rx_log(be) -> list:
    return be.test_hooks.rx_log()


def fail_recorder(be, exc: BaseException, after_rows: int = 0) -> None:
    be.test_hooks.fail_recorder(exc, after_rows)


def stall(be, name: str, ms: float) -> None:
    be.test_hooks.stall_thread(name, ms)


# ---- facade verbs --------------------------------------------------------------------------------------
def connect(be, endpoint: str = "sim"):
    return be.connect_async(endpoint)


def disconnect(be):
    return be.disconnect_async()


def stop(be, source: str = "validator"):
    return be.stop(source)


def halt(be, source: str = "validator"):
    return be.halt(source)


def pause(be, source: str = "validator"):
    return be.pause(source)


def resume(be, source: str = "validator"):
    return be.resume(source)


def clear_stop(be, confirmed: bool = False):
    return be.clear_stop_async(confirmed=confirmed)


def estop_clear(be, confirmed: bool = True):
    return be.estop_clear_async(confirmed=confirmed)


def fault_clear(be):
    return be.fault_clear_async()


def stream(be, on: bool):
    return be.stream_start_async() if on else be.stream_stop_async()


def record_start(be):
    return be.record_start()


def record_stop(be):
    return be.record_stop()


def set_marks(be, specimen: str = "", number: str = "") -> None:
    from bend_stand.core.api import TestMarks  # noqa: PLC0415

    be.marks.set(TestMarks(specimen=specimen, number=number))


def config_values(be) -> dict:
    return dict(be.config.values())


def config_metas(be):
    return be.config.metas()


def config_locked(be) -> frozenset:
    return be.config.locked_keys()


def config_check(be, edits: dict):
    return be.config.check(edits)


def write_verify(be, edits: dict):
    return be.config.write_and_verify_async(edits)


def save_nvm(be):
    return be.config.save_async()


def load_nvm(be):
    return be.config.load_async()


def defaults(be):
    return be.config.defaults_async()


def reboot(be):
    return be.config.reboot_async()


def read_all(be):
    return be.config.read_all_async()


def save_board_file(be, path, values=None) -> None:
    be.config.save_board_config(str(path), values)


def load_board_file(be, path):
    return be.config.load_board_config(str(path))


def thresholds(be):
    return be.limits.thresholds()


def history(be, topic: str) -> list:
    return be.events.history(topic)


def subscribe(be, topic: str, cb):
    return be.events.subscribe(topic, cb, weak=False)


# =============================================================================================== wire

@dataclass
class W:
    """One wire frame re-parsed by ``ref_codec``."""

    t_ns: int
    dir: str                 # "TX" (PC→FW) | "RX" (FW→PC)
    kind: str                # request | response | async | invalid
    name: str                # command / async name
    seq: int
    fields: dict = field(default_factory=dict)
    raw: bytes = b""
    type: int = 0

    @property
    def t_ms(self) -> float:
        return self.t_ns / 1e6


def decode_frame_bytes(frame: bytes) -> rc.Frame:
    p = rc.FrameParser()
    out = p.feed(frame)
    assert len(out) == 1 and p.counters()["crc_errors"] == 0, f"wire log entry is not one valid frame: {frame.hex()}"
    return out[0]


def _decode(fr: rc.Frame) -> dict:
    try:
        return rc.decode_frame(fr)
    except Exception as exc:  # noqa: BLE001 - e.g. another PAYLOAD_VERSION
        return {"_undecodable": str(exc)}


def wire(be, since: int = 0) -> list[W]:
    out = []
    for r in be.test_hooks.wire_log()[since:]:
        fr = decode_frame_bytes(bytes(r.frame))
        kind, name = rc.classify(fr.type)
        out.append(W(r.t_ns, r.direction, kind, name, fr.seq, _decode(fr), bytes(r.frame), fr.type))
    return out


def wire_mark(be) -> int:
    return len(be.test_hooks.wire_log())


def tx(be, name: str | None = None, since: int = 0) -> list[W]:
    return [w for w in wire(be, since) if w.dir == "TX" and (name is None or w.name == name)]


def rx(be, name: str | None = None, since: int = 0, kind: str | None = None) -> list[W]:
    return [w for w in wire(be, since) if w.dir == "RX" and (name is None or w.name == name)
            and (kind is None or w.kind == kind)]


def wire_rt(be, since: int = 0) -> list[W]:
    """Real-clock variant: wire log of the TCP / virtual transport (the backend thread may still append)."""
    return wire(be, since)


# =============================================================================================== processes

def log_process(kind: str, pid: int, event: str) -> None:
    """Evidence of the process rule: every process the validator starts / stops, by PID."""
    out = Path(__file__).resolve().parent / "_reports"
    try:
        out.mkdir(exist_ok=True)
        with open(out / "processes.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {kind} pid {pid} {event}\n")
    except OSError:  # pragma: no cover
        pass


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class SimServerProcess:
    """``python -m bend_stand.io.sim.server`` started by the validator; stopped only through its own handle
    (recorded PID), never by name (role rule)."""

    def __init__(self) -> None:
        self.port, self.ctl = free_port(), free_port()
        self.proc = subprocess.Popen([PY, "-m", "bend_stand.io.sim.server", "--port", str(self.port),
                                      "--ctl", str(self.ctl)], cwd=str(SW_ROOT / "src"),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        self.pid = self.proc.pid
        log_process("sim-server", self.pid, "started")
        t_end = time.monotonic() + 10
        while time.monotonic() < t_end:
            try:
                with socket.create_connection(("127.0.0.1", self.ctl), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        self.endpoint = f"tcp://127.0.0.1:{self.port}"

    def act(self, action: str, **args: Any) -> dict:
        import json  # noqa: PLC0415

        with socket.create_connection(("127.0.0.1", self.ctl), timeout=2) as s:
            s.sendall((json.dumps({"action": action, **args}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.decode() or "{}")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        log_process("sim-server", self.pid, f"stopped rc={self.proc.returncode}")


# =============================================================================================== forced path

def forced_device(be):
    """The wire-level ``Device`` — used ONLY for 'forced' stimuli the M1 public API cannot produce (motion is M2;
    SW_test_plan TC-SW-STOP-004-04 'forced Device.move_abs'). Never used to compute an expected value."""
    return be.device


def forced_enable_home(be) -> None:
    from bend_stand.core import protocol_gen as pg  # noqa: PLC0415

    d = forced_device(be)
    result(be, d.request(pg.Cmd.ENABLE))
    advance(be, 700)
    result(be, d.request(pg.Cmd.HOME, bytes([1])))
    assert run_until(be, lambda: d.last_flags & pg.DataFlags.HOMED and not d.last_flags & pg.DataFlags.MOVING,
                     120_000)


def forced_move_abs(be, target_um: int, v_um_s: int = 10_000):
    from bend_stand.core import protocol_gen as pg  # noqa: PLC0415

    d = forced_device(be)
    return d.request(pg.Cmd.MOVE_ABS, rc.encode_request("MOVE_ABS", {"target_um": target_um, "v_um_s": v_um_s,
                                                                      "a_um_s2": 0}), epoch=d.channel.motion_epoch)


def rss_bytes() -> int:
    """Working set of this process (Windows GetProcessMemoryInfo; 0 elsewhere)."""
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        h = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        return int(pmc.WorkingSetSize)
    except Exception:  # noqa: BLE001
        return 0


# =============================================================================================== misc

def u16(b: bytes, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]
