#!/usr/bin/env python3
"""FW host twin launcher: virtual time master, TCP ports, world-control vocabulary v2, logs.

Implements: SYS-008, D-07 (twin), DEF-P1-03 M1 subset (lock-step, wire log, timed byte injection, flash cut,
reset causes, edge / seam logs), F-B-06 vocabulary v2 (tools/README).

    .venv\\Scripts\\python 00_System\\tools\\fw_twin\\twin.py --port 5760 --ctl 5761 --clock realtime
    .venv\\Scripts\\python 00_System\\tools\\fw_twin\\twin.py --clock lockstep --scenario s.simscn.json

The engine (build/fw_twin.exe = A's unmodified 02_FW/src/{pure,core,gen} + twin seams, built by build.py)
is a slave process: it runs only when this launcher advances the virtual clock. Python API (tests,
validators):

    from twin import Twin, TwinLink
    with Twin(clock="lockstep") as tw:              # boots the FW at virtual t = 0
        link = TwinLink(tw)
        r = link.cmd("GET_INFO")                     # ref_codec-decoded response
        tw.act("estop", open=True)                   # vocabulary v2, same as the control port
        tw.act("clock", advance_ms=100)
        tw.act("query", what="wire_log")

Times in logs and actions are virtual world times (µs since the twin started, `*_us`, float with ns
resolution); the FW's own t_us is `fw_t_us` (t0_us + time since the last boot, mod 2^32).
"""
from __future__ import annotations

import argparse
import heapq
import itertools
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import build as twin_build  # noqa: E402
import ref_codec as rc  # noqa: E402

BYTES_PER_S = 92160


def _gen_port(name: str, fallback: int) -> int:
    """PROTO_TWIN_TCP_PORT / PROTO_TWIN_CTL_PORT from the generated proto_gen.h (protocol.yaml, GF-08)."""
    import re  # noqa: PLC0415
    try:
        h = (twin_build.FW / "src" / "gen" / "proto_gen.h").read_text(encoding="utf-8")
        return int(re.search(rf"#define\s+{name}\s+(\d+)u", h).group(1))
    except (OSError, AttributeError):
        return fallback


TWIN_TCP_PORT = _gen_port("PROTO_TWIN_TCP_PORT", 5760)
TWIN_CTL_PORT = _gen_port("PROTO_TWIN_CTL_PORT", 5761)
RST = {name: i for i, name in enumerate(rc.RESET_CAUSE)}
RESET_CAUSE_OF = {"software": RST["SOFTWARE"], "iwdg": RST["IWDG"], "power": RST["POWER_ON"], "pin": RST["PIN"],
                  "hang": RST["IWDG"]}
INPUT_ID = {"estop": 0, "start": 1, "end": 2, "stop": 3, "pause": 4, "alm": 5, "pend": 6, "drv_power": 7}
INPUT_NAME = {v: k for k, v in INPUT_ID.items()}
TWIN_ONLY = {"chatter", "iwdg", "clk"}
DEFAULT_WORLD = {"stroke_um": 300000, "start_switch_um": -1500, "end_switch_um": 301000,
                 "cell_counts_per_n": 3285.0, "load_offset_counts": 50000,
                 "specimen": {"kind": "none"}, "afe": {"rate_sps": 80, "rate_error": 0.0, "noise_counts": 0.0},
                 "driver": {"drv_power": True}, "estop_open": False, "steps_per_mm": 800.0}


def byte_end(t0: int, i: int) -> int:
    """End of byte i of a burst starting at t0 (ns) — identical to the engine's byte_end()."""
    return t0 + ((i + 1) * 1_000_000_000 + BYTES_PER_S - 1) // BYTES_PER_S


BYTE_NS = byte_end(0, 0)


class TwinError(RuntimeError):
    pass


_PRIVATE_EXE: dict[str, Path] = {}


def private_exe(exe: Path) -> Path:
    """OBS-M2-09: an exe in the **shared** build dir (``fw_twin/build``) is copied once per process into a private
    temp dir and every engine of this process (incl. the restarts after a reset) runs the copy, so another role's
    rebuild can neither fail on a locked exe nor swap the binary in the middle of a run. Binaries elsewhere (a
    private ``--build-dir`` / ``build.private_build_dir()``) are already private and used in place."""
    exe = Path(exe).resolve()
    if exe.parent != twin_build.SHARED_BUILD.resolve():
        return exe
    key = str(exe).lower()
    if key not in _PRIVATE_EXE:
        import atexit  # noqa: PLC0415
        d = Path(tempfile.mkdtemp(prefix="fw_twin_bin_"))
        atexit.register(shutil.rmtree, d, True)
        dst = d / exe.name                             # name kept: Twin.core is derived from it ("probe")
        shutil.copyfile(exe, d / (exe.name + ".part"))
        os.replace(d / (exe.name + ".part"), dst)
        _PRIVATE_EXE[key] = dst
    return _PRIVATE_EXE[key]


# =============================================================================================== engine
class _Engine:
    """One engine process = one MCU power-on period."""

    def __init__(self, exe: Path, run_dir: Path, boot_ns: int, t0_us: int, cause: int, uid: str, hse_fail: bool,
                 lsi_hz: float, seed: int, init: list[str], hw_meas: bool = False, noinit: str = "0:0:0"):
        args = [str(exe), "--boot-ns", str(boot_ns), "--t0-us", str(t0_us), "--reset-cause", str(cause),
                "--flash", str(run_dir / "flash.bin"), "--uid", uid, "--hse-fail", str(int(hse_fail)),
                "--lsi", str(lsi_hz), "--seed", str(seed), "--hw-meas", str(int(hw_meas)), "--noinit", noinit]
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
                                  cwd=str(run_dir))
        self.alive = True
        for line in init:
            self.send(line)
        self.send("G")

    def send(self, line: str) -> None:
        assert self.p.stdin is not None
        self.p.stdin.write(line + "\n")

    def flush(self) -> None:
        assert self.p.stdin is not None
        self.p.stdin.flush()

    def readline(self) -> str:
        assert self.p.stdout is not None
        s = self.p.stdout.readline()
        if not s:
            self.alive = False
            raise TwinError(f"engine exited unexpectedly (rc={self.p.poll()})")
        return s.rstrip("\n")

    def close(self) -> None:
        if self.p.poll() is None:
            try:
                self.send("X")
                self.flush()
                self.p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                self.p.kill()
        self.alive = False


# =============================================================================================== twin
class Twin:
    def __init__(self, clock: str = "lockstep", *, core: str = "auto", exe: Path | None = None,
                 run_dir: Path | None = None, fresh_flash: bool = True, scenario: dict | str | Path | None = None,
                 t0_us: int = 0, speed: float = 1.0, uid: str = "5457494E2D5549442D303031", seed: int = 1,
                 boot_delay_ms: float = 2.0, log_max: int = 1_000_000, hw_meas: bool = False,
                 private: bool = True):
        if clock not in ("lockstep", "realtime"):
            raise ValueError("clock must be lockstep or realtime")
        self.clock, self.speed, self.t0_us, self.uid, self.seed = clock, speed, t0_us, uid, seed
        self.exe = Path(exe) if exe else twin_build.ensure_built(core)
        if private:
            self.exe = private_exe(self.exe)         # OBS-M2-09: never run the shared fw_twin.exe in place
        self.core = "probe" if "probe" in self.exe.name else "fw"
        self._tmp = None
        if run_dir is None:
            self._tmp = tempfile.mkdtemp(prefix="fw_twin_")
            run_dir = Path(self._tmp)
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if fresh_flash and (self.run_dir / "flash.bin").exists():
            (self.run_dir / "flash.bin").unlink()
        self.boot_delay_ns = int(boot_delay_ms * 1e6)
        self.lock = threading.RLock()
        self.now = 0                      # virtual world time, ns
        self.boot_ns = 0
        self.eng: _Engine | None = None
        self.hse_fail, self.lsi_hz = False, 32000.0
        self.hw_meas = hw_meas            # model of the HW_MEAS seam (DIAG_MEAS, REQ-C-M2-08)
        self.noinit = "0:0:0"             # DIAG_MEAS .noinit carried over a reset
        self.world_x_um: float | None = None   # world position carried over a reset
        self.driver_cfg = {"dir_wiring_inverted": False, "pend_auto": False, "pend_lag_ms": 5.0}
        self.loop_load_line = ""
        # logs
        self.wire_log: deque[dict] = deque(maxlen=log_max)
        self.sent: deque[dict] = deque(maxlen=log_max)
        self.edges: deque[dict] = deque(maxlen=log_max)
        self.seam_log: deque[dict] = deque(maxlen=log_max)
        self.conversions: deque[dict] = deque(maxlen=log_max)
        self.input_log: deque[dict] = deque(maxlen=log_max)
        self.resets: list[dict] = []
        self.errors: list[str] = []
        self.outputs: dict[str, int] = {}
        self.pul_rising = 0
        # schedule (virtual-time actions)
        self._heap: list[tuple[int, int, Callable[[], None], str]] = []
        self._seq = itertools.count()
        # RX path (PC -> FW)
        self.rx_line_free = 0
        self._rx_scan = bytearray()
        self._rx_scan_t: list[int] = []
        self._rx_hold = bytearray()       # frame-mode buffer (request link faults armed)
        self.rx_counts: dict[int, int] = {}
        self.link_silence_until = 0
        self.rx_corrupt_until = 0
        self.req_faults: list[dict] = []
        self.resp_faults: list[dict] = []
        # TX delivery (FW -> PC)
        self._tx_pending: deque[tuple[int, int, bytes]] = deque()   # (start, end, frame)
        self.client_rx = bytearray()
        self._client_sock: socket.socket | None = None
        self.ev_counts: dict[str, int] = {}
        self.on_event_triggers: list[dict] = []
        self.on_frame_triggers: list[dict] = []
        # world
        self.world = json.loads(json.dumps(DEFAULT_WORLD))
        self.inputs = {"estop": 0, "start": 0, "end": 0, "stop": 0, "pause": 1, "alm": 0, "pend": 1, "drv_power": 0}
        self.broken: set[str] = set()
        self.alm_active, self.pend_active, self.drv_on = False, True, True
        self.lim_forced = {"start": -1, "end": -1}
        self.afe_cfg: dict[str, float] = {}
        self.spec_line = "W spec 0"
        # M3 load model (ICD v0.7.2): hung weights, zero drift, cell creep / non-linearity, specimen relaxation
        self.load_cfg = {"weight_n": 0.0, "drift_cps": 0.0, "creep_frac": 0.0, "creep_tau_s": 0.0,
                         "relax_frac": 0.0, "relax_tau_s": 0.0, "nonlin_frac": 0.0, "fs_n": 1961.33}
        self.load_state: dict[str, float] | None = None     # creep / relaxation / drift carried over a reset
        self.shift_um = 0.0
        self.scenario_params: dict[str, Any] = {}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._servers: list[socket.socket] = []
        if scenario is not None:
            self._load_scenario(scenario)
        self._boot(0, RST["POWER_ON"])
        if clock == "realtime":
            self._rt_base = (time.perf_counter_ns(), self.now)
            t = threading.Thread(target=self._rt_loop, name="twin-rt", daemon=True)
            t.start()
            self._threads.append(t)

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self) -> "Twin":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._stop.set()
        for s in self._servers:
            try:
                s.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=2)
        with self.lock:
            if self.eng:
                self.eng.close()
                self.eng = None
            if self._client_sock:
                try:
                    self._client_sock.close()
                except OSError:
                    pass
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------ world -> engine lines
    def _level(self, name: str) -> int:
        if name in self.broken:
            return 1                     # every input has a pull-up: a broken wire reads high
        if name == "drv_power":
            return 0 if self.drv_on else 1
        if name == "alm":
            return 1 if (self.alm_active or not self.drv_on) else 0
        if name == "pend":
            return 1 if self.pend_active else 0
        return self.inputs[name]

    def _world_lines(self) -> list[str]:
        w = self.world
        lines = [f"W in {INPUT_ID[n]} {self._level(n)}" for n in ("estop", "stop", "pause", "alm", "pend", "drv_power")]
        lines += [f"W limpos 0 {w['start_switch_um']}", f"W limpos 1 {w['end_switch_um']}",
                  f"W limf 0 {1 if 'start' in self.broken else self.lim_forced['start']}",
                  f"W limf 1 {1 if 'end' in self.broken else self.lim_forced['end']}",
                  f"W spm {w.get('steps_per_mm', 800.0)}", f"W shift {self.shift_um}",
                  f"W afe cpn {w['cell_counts_per_n']}", f"W afe offset {w['load_offset_counts']}",
                  f"W afe seed {self.seed}", f"W lsi {self.lsi_hz}", self.spec_line]
        lines.append(f"W dirwiring {int(self.driver_cfg['dir_wiring_inverted'])}")
        lines.append(f"W pendauto {int(self.driver_cfg['pend_auto'])} {int(self.driver_cfg['pend_lag_ms'] * 1e6)}")
        if self.world_x_um is not None:
            lines.append(f"W x {self.world_x_um}")
        if self.loop_load_line:
            lines.append(self.loop_load_line)
        if getattr(self, "fault_rec", None):
            lines.append("W faultrec %x %x" % self.fault_rec)
            self.fault_rec = None
        lc, ls = self.load_cfg, self.load_state
        lines.append(f"W weight {lc['weight_n']}")
        lines.append(f"W afe creep {lc['creep_frac']} {lc['creep_tau_s']}")
        lines.append(f"W afe nonlin {lc['nonlin_frac']} {lc['fs_n']}")
        lines.append(f"W relax {lc['relax_frac']} {lc['relax_tau_s']}" + (f" {ls['relax_n']}" if ls else ""))
        if ls:
            lines.append(f"W afe creep_state {ls['creep_counts']}")
            lines.append(f"W afe drift {lc['drift_cps']} {ls['drift_counts']} {int(ls['t_ns'])}")
        elif lc["drift_cps"]:
            lines.append(f"W afe drift {lc['drift_cps']}")
        afe = w.get("afe", {})
        lines += [f"W afe rate_error {afe.get('rate_error', 0.0)}", f"W afe noise {afe.get('noise_counts', 0.0)}"]
        for k, v in self.afe_cfg.items():
            lines.append(f"W afe {k} {v}")
        return lines

    def _boot(self, t: int, cause: int) -> None:
        self.boot_ns = t
        self.now = max(self.now, t)
        self.eng = _Engine(self.exe, self.run_dir, t, self.t0_us, cause, self.uid, self.hse_fail, self.lsi_hz,
                           self.seed, self._world_lines(), self.hw_meas, self.noinit)
        self._sync_break()
        self.eng.flush()
        line = self.eng.readline()
        while not line.startswith("B "):
            self._handle(line)
            line = self.eng.readline()

    def _restart(self, cause_name: str, t: int, external: bool = False) -> None:
        """MCU reset at virtual t: frames in flight are cut, RX in flight is lost, flash is kept."""
        self.resets.append({"t_us": t / 1000, "cause": cause_name})
        if external and self.eng and self.eng.p.poll() is None:
            # externally requested reset (reset action / iwdg / power): the engine is still running, so take what
            # survives an MCU reset from it — world x (the axis does not move) and DIAG_MEAS .noinit (REQ-C-M2-12)
            y = self._engine_query()
            if "x_um_true" in y:
                self.world_x_um = float(y["x_um_true"])
            if "creep_counts" in y:
                self.load_state = {"creep_counts": float(y["creep_counts"]), "relax_n": float(y["relax_n"]),
                                   "drift_counts": float(y["drift_counts"]), "t_ns": float(self.now)}
            if "noinit" in y:
                self.noinit = y["noinit"]
        if cause_name == "power":
            self.noinit = "0:0:0"                    # .noinit RAM does not survive a power cycle
        if self.eng:
            self.eng.close()
        cut = deque()
        for (s, e, fr) in self._tx_pending:
            if s < t:
                n = sum(1 for i in range(len(fr)) if byte_end(s, i) <= t)
                if n:
                    cut.append((s, byte_end(s, n - 1), fr[:n]))
        self._tx_pending = cut
        self._boot(t + self.boot_delay_ns, RESET_CAUSE_OF.get(cause_name, RST["UNKNOWN"]))

    def _send(self, line: str) -> None:
        assert self.eng
        self.eng.send(line)

    def _sync_break(self) -> None:
        if self.eng:
            self.eng.send("K %d" % (rc.ASYNC["EVENT"] if self.on_event_triggers else -1))

    # ------------------------------------------------------------------ engine output
    def _handle(self, line: str) -> tuple[str, int] | None:
        tag, _, rest = line.partition(" ")
        p = rest.split(" ")
        if tag == "T":
            s, e, cls, hx = int(p[0]), int(p[1]), int(p[2]), p[3]
            fr = bytes.fromhex(hx)
            self.wire_log.append({"dir": "tx", "cls": cls, "type": fr[2], "seq": fr[3], "first_us": s / 1000,
                                  "last_us": e / 1000, "hex": hx})
            self._tx_pending.append((s, e, fr))
            if fr[2] == rc.ASYNC["EVENT"] and self.on_event_triggers:
                self._check_on_event(fr, e)
        elif tag == "W":
            t, cls, ok, hx = int(p[0]), int(p[1]), int(p[2]), p[3]
            fr = bytes.fromhex(hx)
            rec = {"t_us": t / 1000, "cls": cls, "type": fr[2] if len(fr) > 2 else None,
                   "seq": fr[3] if len(fr) > 3 else None, "dropped": not ok, "hex": hx}
            if len(fr) >= 26 and fr[2] == rc.ASYNC["DATA"]:
                rec["frame_seq"] = int.from_bytes(fr[20:22], "little")
                rec["fw_t_us"] = int.from_bytes(fr[6:10], "little")
            self.sent.append(rec)
        elif tag == "E":
            t, pin, lv = int(p[0]), p[1], int(p[2])
            self.edges.append({"t_us": t / 1000, "pin": pin, "level": lv})
            if pin == "PUL" and lv == 1:
                self.pul_rising += 1
        elif tag == "L":
            self.seam_log.append({"t_us": int(p[0]) / 1000, "call": p[1], "args": " ".join(p[2:])})
        elif tag == "O":
            self.outputs[p[1]] = int(p[2])
        elif tag == "S":
            c = {"t_us": int(p[0]) / 1000, "fw_t_us": int(p[1]), "raw": int(p[2]), "delivered": p[3] == "1"}
            if len(p) >= 6:               # REQ-C-M2-05: HX711 gain / channel and rate used for this conversion
                gp = int(p[4])
                c["gain_pulses"] = gp
                c["channel_gain"] = {25: "A128", 26: "B32", 27: "A64"}.get(gp, str(gp))
                c["rate_sps"] = int(p[5])
            self.conversions.append(c)
        elif tag == "N":
            self.noinit = ":".join(p)                  # opaque .noinit words (twin_meas.c)
        elif tag == "V":                               # load-model states at an MCU reset (world, ICD v0.7.2)
            self.load_state = {"creep_counts": float(p[0]), "relax_n": float(p[1]), "drift_counts": float(p[2]),
                               "t_ns": float(p[3])}
        elif tag == "M" and p[0] == "stim":
            self._stim(int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5]), int(p[6]))
        elif tag == "I":
            self.input_log.append({"t_us": int(p[0]) / 1000, "input": INPUT_NAME.get(int(p[1])), "level": int(p[2])})
        elif tag == "D":
            return ("D", int(p[0]))
        elif tag == "Z":
            if len(p) >= 3:
                self.world_x_um = float(p[2])           # the axis does not move across an MCU reset
            return ("Z", int(p[1]), p[0])  # type: ignore[return-value]
        elif tag == "!":
            self.errors.append(rest)
            sys.stderr.write(f"fw_twin engine: {rest}\n")
        return None

    def _engine_advance(self, target: int) -> None:
        while True:
            if target < self.now:
                return
            assert self.eng
            self.eng.send(f"A {target}")
            self.eng.flush()
            while True:
                r = self._handle(self.eng.readline())
                if r is None:
                    continue
                if r[0] == "D":
                    self.now = max(self.now, r[1])
                    break
                _, t, cause = r  # type: ignore[misc]
                self.now = max(self.now, t)
                self._restart(cause, t)
                break
            self._deliver_tx(self.now)
            if self.now >= target:
                return

    # ------------------------------------------------------------------ time
    def at(self, t_ns: int, fn: Callable[[], None], desc: str = "") -> None:
        """Run fn at virtual time t_ns (>= now)."""
        heapq.heappush(self._heap, (max(t_ns, self.now), next(self._seq), fn, desc))

    def advance_to(self, t: int) -> None:
        with self.lock:
            self._flush_hold()
            while True:
                # OBS-M1-02: timed RX bytes (rx_bytes at_us) are handed to the engine BEFORE it advances to
                # their time, so bytes arriving during a flash stall land in the RX ring at their wire time
                # (as the RX DMA does) instead of at the end of the stall
                while self._heap and self._heap[0][3] == "rx_bytes" and self._heap[0][0] <= t:
                    _, _, fn, _ = heapq.heappop(self._heap)
                    fn()
                    if self.eng:
                        self.eng.flush()
                nxt = self._heap[0][0] if self._heap else None
                target = t if nxt is None else min(t, nxt)
                self._engine_advance(target)
                if self._heap and self._heap[0][0] <= self.now:
                    _, _, fn, _ = heapq.heappop(self._heap)
                    fn()
                    if self.eng:
                        self.eng.flush()
                    continue
                if self.now >= t:
                    return

    def advance_us(self, us: float) -> None:
        self.advance_to(self.now + int(us * 1000))

    def advance_ms(self, ms: float) -> None:
        self.advance_to(self.now + int(ms * 1e6))

    @property
    def now_us(self) -> float:
        return self.now / 1000

    def fw_t_us(self, world_ns: int | None = None) -> int:
        t = self.now if world_ns is None else world_ns
        return (self.t0_us + (t - self.boot_ns) // 1000) & 0xFFFFFFFF

    def _rt_loop(self) -> None:
        while not self._stop.is_set():
            wall0, vt0 = self._rt_base
            target = vt0 + int((time.perf_counter_ns() - wall0) * self.speed)
            try:
                self.advance_to(target)
            except TwinError as e:
                self.errors.append(str(e))
                return
            time.sleep(0.0005)

    # ------------------------------------------------------------------ RX path (PC -> FW)
    def feed_rx(self, data: bytes, at_ns: int | None = None) -> None:
        """Bytes from the PC. Lock-step: they arrive at the current virtual time (or at_ns)."""
        with self.lock:
            t = self.now if at_ns is None else at_ns
            if t < self.link_silence_until:
                return
            if t < self.rx_corrupt_until and data:
                b = bytearray(data)
                b[len(b) // 2] ^= 0x01
                data = bytes(b)
            if self.req_faults:
                self._rx_hold += data
                self._rx_hold_t = t
                self._frame_mode()
                return
            self._inject(data, t)

    def _frame_mode(self) -> None:
        """Split the held bytes into frames (ICD §2.3 shape); apply armed request faults per frame."""
        b = self._rx_hold
        out = bytearray()
        t = getattr(self, "_rx_hold_t", self.now)
        while b:
            if b[0] != 0xA5 or (len(b) > 1 and b[1] != 0x5A):
                out.append(b.pop(0))
                continue
            if len(b) < 6:
                break
            n = int.from_bytes(b[4:6], "little")
            if n > rc.MAX_LEN:
                out.append(b.pop(0))
                continue
            if len(b) < 8 + n:
                break
            fr = bytes(b[:8 + n])
            del b[:8 + n]
            if rc.crc16_ccitt(fr[2:6 + n]) != int.from_bytes(fr[6 + n:8 + n], "little"):
                out += fr
                continue
            for f in self.req_faults:
                if f["type"] == fr[2]:
                    f["n"] -= 1
                    self.req_faults = [x for x in self.req_faults if x["n"] > 0]
                    kind = f["fault"]
                    if kind == "drop_next":
                        fr = b""
                    elif kind == "duplicate_next":
                        fr = fr + fr
                    elif kind == "corrupt_next":
                        fr = fr[:-1] + bytes([fr[-1] ^ 0xFF])
                    elif kind == "delay_next":
                        if out:
                            self._inject(bytes(out), t)
                            out = bytearray()
                        delayed = fr
                        self.at(self.now + int(f["ms"] * 1e6), lambda d=delayed: self._inject(d, self.now), "delay_next")
                        fr = b""
                    break
            out += fr
        if out:
            self._inject(bytes(out), t)

    def _flush_hold(self) -> None:
        if self._rx_hold and not self.req_faults:
            d = bytes(self._rx_hold)
            self._rx_hold.clear()
            self._inject(d, self.now)

    def _inject(self, data: bytes, t: int) -> None:
        if not data:
            return
        start = max(t, self.rx_line_free)
        first_end = byte_end(start, 0)
        times = [byte_end(start, i) for i in range(len(data))]
        self.rx_line_free = byte_end(start, len(data) - 1)
        if self.eng:
            self._send(f"R {first_end} {data.hex().upper()}")
            self.eng.flush()
        self._observe_rx(data, times, start)

    def _observe_rx(self, data: bytes, times: list[int], start: int) -> None:
        """Track complete PC->FW frames (wire log 'rx' and on_frame triggers) — observation only."""
        self._rx_scan += data
        self._rx_scan_t += times
        b, ts = self._rx_scan, self._rx_scan_t
        while len(b) >= 2:
            if not (b[0] == 0xA5 and b[1] == 0x5A):
                del b[0]; del ts[0]
                continue
            if len(b) < 6:
                break
            n = int.from_bytes(b[4:6], "little")
            if n > rc.MAX_LEN:
                del b[0]; del ts[0]
                continue
            if len(b) < 8 + n:
                break
            fr = bytes(b[:8 + n])
            if rc.crc16_ccitt(fr[2:6 + n]) != int.from_bytes(fr[6 + n:8 + n], "little"):
                del b[0]; del ts[0]
                continue
            last = ts[7 + n]
            first = ts[0] - BYTE_NS
            del b[:8 + n]; del ts[:8 + n]
            self.wire_log.append({"dir": "rx", "type": fr[2], "seq": fr[3], "first_us": first / 1000,
                                  "last_us": last / 1000, "hex": fr.hex().upper()})
            self.rx_counts[fr[2]] = self.rx_counts.get(fr[2], 0) + 1
            for trig in list(self.on_frame_triggers):
                if trig["type"] == fr[2] and self.rx_counts[fr[2]] - trig["base"] == trig["nth"]:
                    self.on_frame_triggers.remove(trig)
                    then = trig["then"]
                    self.at(last + trig["delay_us"] * 1000, lambda a=then: self._act_unlocked(dict(a)), "on_frame")

    # ------------------------------------------------------------------ TX delivery (FW -> PC)
    def _check_on_event(self, fr: bytes, end: int) -> None:
        try:
            code = rc.EVENT_NAME.get(int.from_bytes(fr[10:12], "little"))
        except Exception:  # noqa: BLE001
            return
        if code is None:
            return
        self.ev_counts[code] = self.ev_counts.get(code, 0) + 1
        for trig in list(self.on_event_triggers):
            if trig["code"] == code and self.ev_counts[code] - trig["base"] == trig["nth"]:
                self.on_event_triggers.remove(trig)
                then = trig["then"]
                self.at(end + trig["delay_us"] * 1000, lambda a=then: self._act_unlocked(dict(a)), "on_event")
        self._sync_break()

    def _deliver_tx(self, upto: int) -> None:
        out = bytearray()
        while self._tx_pending and self._tx_pending[0][1] <= upto:
            s, e, fr = self._tx_pending.popleft()
            for f in self.resp_faults:
                if len(fr) > 2 and f["type"] == fr[2]:
                    f["n"] -= 1
                    self.resp_faults = [x for x in self.resp_faults if x["n"] > 0]
                    if f["fault"] == "drop_next":
                        fr = b""
                    elif f["fault"] == "duplicate_next":
                        fr = fr + fr
                    elif f["fault"] == "corrupt_next":
                        fr = fr[:-1] + bytes([fr[-1] ^ 0xFF])
                    elif f["fault"] == "delay_next":
                        d = fr
                        self.at(e + int(f["ms"] * 1e6), lambda d=d: self._emit(d), "delay_next")
                        fr = b""
                    break
            out += fr
        if out:
            self._emit(bytes(out))

    def _emit(self, data: bytes) -> None:
        self.client_rx += data
        if self._client_sock is not None:
            try:
                self._client_sock.sendall(data)
            except OSError:
                self._client_sock = None

    def read_client(self) -> bytes:
        """In-process PC side: take every FW->PC byte delivered so far."""
        with self.lock:
            d = bytes(self.client_rx)
            self.client_rx.clear()
            return d

    # ------------------------------------------------------------------ inputs with bounce
    def _set_input(self, name: str, apply: Callable[[], None], bounce_ms: list[float] | None) -> None:
        """Apply a level change now; bounce = alternating toggle durations, ending at the new level."""
        old = self._level(name)
        apply()
        new = self._level(name)
        self._push_level(name, new)
        if bounce_ms and new != old:
            t = self.now
            lv = new
            seq = list(bounce_ms)
            if len(seq) % 2:                              # must end on the new level
                seq.append(seq[-1])
            for d in seq:
                t += int(d * 1e6)
                lv ^= 1
                self.at(t, lambda n=name, v=lv: self._push_raw(n, v), f"bounce {name}")

    def _push_level(self, name: str, level: int) -> None:
        if name in ("start", "end"):
            k = 0 if name == "start" else 1
            self._send(f"W limf {k} {1 if name in self.broken else self.lim_forced[name]}")
        else:
            self._send(f"W in {INPUT_ID[name]} {level}")

    def _push_raw(self, name: str, level: int) -> None:
        if name in ("start", "end"):
            self._send(f"W limf {0 if name == 'start' else 1} {level}")
        else:
            self._send(f"W in {INPUT_ID[name]} {level}")

    # ------------------------------------------------------------------ vocabulary v2
    def act(self, action: str | dict, **args: Any) -> dict:
        """World-control vocabulary v2 (tools/README). Returns {"ok": True, ...} / {"ok": False, "error": ...}."""
        if isinstance(action, dict):
            args = {k: v for k, v in action.items() if k != "action"}
            action = action["action"]
        if action == "clock":
            if self.clock != "lockstep":
                return {"ok": False, "error": "clock: only in lock-step mode"}
            self.advance_to(self.now + int(args.get("advance_ms", 0) * 1e6) + int(args.get("advance_us", 0) * 1000))
            return {"ok": True, "now_us": self.now_us, "fw_t_us": self.fw_t_us()}
        with self.lock:
            try:
                r = self._act_unlocked(dict(args, action=action))
            except (KeyError, ValueError, TypeError) as e:
                return {"ok": False, "error": f"{action}: {e!r}"}
            if self.eng:
                self.eng.flush()
            return r

    def _act_unlocked(self, a: dict) -> dict:
        action = a.pop("action")
        fn = getattr(self, "_a_" + action, None)
        if fn is None:
            return {"ok": False, "error": f"unknown action {action}"}
        r = fn(**a)
        return r if isinstance(r, dict) else {"ok": True}

    def _a_estop(self, open: bool, drv_power_follows: bool = True, k1_delay_ms: float = 20, bounce_ms=None):
        self._set_input("estop", lambda: self.inputs.__setitem__("estop", 1 if open else 0), bounce_ms)
        if drv_power_follows:
            self.at(self.now + int(k1_delay_ms * 1e6), lambda: self._a_drv_power(on=not open), "K1")

    def _a_drv_power(self, on: bool, bounce_ms=None):
        def ap():
            self.drv_on = bool(on)
        old_alm = self._level("alm")
        self._set_input("drv_power", ap, bounce_ms)
        if self._level("alm") != old_alm:
            self._push_level("alm", self._level("alm"))

    def _a_button(self, name: str, pressed: bool, bounce_ms=None):
        if name == "stop":
            return {"ok": False, "error": "button stop: retired (ICD v0.5, CR-01 / D-36: the red button is the E-stop, use 'estop')"}
        if name != "pause":
            raise ValueError(f"button {name}")
        self._set_input("pause", lambda: self.inputs.__setitem__("pause", 0 if pressed else 1), bounce_ms)

    def _a_limit(self, name: str, active: bool | None = None, position_um: float | None = None, bounce_ms=None):
        if name not in ("start", "end"):
            raise ValueError(f"limit {name}")
        k = 0 if name == "start" else 1
        if position_um is not None:
            self.world["start_switch_um" if k == 0 else "end_switch_um"] = position_um
            self._send(f"W limpos {k} {position_um}")
        if active is not None or position_um is None:
            self._set_input(name, lambda: self.lim_forced.__setitem__(name, -1 if active is None else int(bool(active))),
                            bounce_ms)

    def _a_wire(self, input: str, broken: bool):
        if input not in INPUT_ID:
            raise ValueError(f"wire {input}")
        if input == "stop":
            return {"ok": False, "error": "wire stop: retired (ICD v0.5, CR-01 / D-36: PC7 is no longer an input)"}
        (self.broken.add if broken else self.broken.discard)(input)
        self._push_level(input, self._level(input))

    def _a_chatter(self, input: str, period_ms: float, duration_ms: float):
        if input not in INPUT_ID or input == "stop":
            raise ValueError(f"chatter {input}")
        half = int(period_ms * 1e6 / 2)
        base = self._level(input)
        n = int(duration_ms * 1e6 // max(half, 1))
        for i in range(1, n + 1):
            self.at(self.now + i * half, lambda v=base ^ (i & 1), nm=input: self._push_raw(nm, v), "chatter")
        self.at(self.now + (n + 1) * half, lambda nm=input: self._push_level(nm, self._level(nm)), "chatter end")

    def _a_alm(self, active: bool):
        self.alm_active = bool(active)
        self._push_level("alm", self._level("alm"))

    def _a_pend(self, active: bool):
        self.pend_active = bool(active)
        self._push_level("pend", self._level("pend"))

    def _a_specimen(self, kind: str, k_n_per_mm: float = 0.0, x_contact_um: float = 0.0, k2_n_per_mm: float = 0.0,
                    f_yield_n: float = 0.0, f_break_n: float = 0.0, relax_pct: float | None = None,
                    relax_tau_s: float | None = None):
        kc = {"none": 0, "spring": 1, "bilinear": 2}[kind]
        self.world["specimen"] = {"kind": kind, "k_n_per_mm": k_n_per_mm, "x_contact_um": x_contact_um}
        self.spec_line = f"W spec {kc} {k_n_per_mm} {x_contact_um} {k2_n_per_mm} {f_yield_n} {f_break_n}"
        self._send(self.spec_line)
        # M3 (ICD v0.7.2): stress relaxation at constant position — the specimen force decays by relax_pct %
        # with the time constant relax_tau_s (first order, follows the elastic force while moving)
        self.load_cfg["relax_frac"] = float(relax_pct or 0.0) / 100.0
        self.load_cfg["relax_tau_s"] = float(relax_tau_s or 0.0)
        if self.load_state:
            self.load_state["relax_n"] = 0.0
        self._send(f"W relax {self.load_cfg['relax_frac']} {self.load_cfg['relax_tau_s']} 0")

    def _a_weight(self, kg: float | None = None, n: float | None = None, g_mps2: float = 9.80665):
        """M3 (ICD v0.7.2): known weights hung on the cell for a load calibration — force kg·g (or `n` newtons;
        + = tension, the sign of a spring specimen pushed by the axis); 0 / none = removed. Adds to the specimen."""
        f = float(n) if n is not None else float(kg or 0.0) * float(g_mps2)
        self.load_cfg["weight_n"] = f
        self._send(f"W weight {f}")
        return {"ok": True, "force_n": f}

    def _a_load_offset(self, counts: int):
        self.world["load_offset_counts"] = counts
        self._send(f"W afe offset {counts}")

    def _a_afe(self, rate_error=None, noise_counts=None, stall=None, saturate="_", drop_every=None, miss_next=None,
               sck_overrun=None, raw_script=None, drift_counts_per_s=None, creep_pct=None, creep_tau_s=None,
               nonlin_pct_fs=None, fs_n=None):
        def keep(k, v):
            self.afe_cfg[k] = v
            self._send(f"W afe {k} {v}")
        lc = self.load_cfg
        if drift_counts_per_s is not None:              # M3: linear zero drift from now on (TC zero, R2 §3)
            lc["drift_cps"] = float(drift_counts_per_s)
            self.load_state = None
            self._send(f"W afe drift {lc['drift_cps']}")
        if creep_pct is not None or creep_tau_s is not None:   # M3: cell creep (R2: 0.02 % FS / 30 min)
            if creep_pct is not None:
                lc["creep_frac"] = float(creep_pct) / 100.0
            if creep_tau_s is not None:
                lc["creep_tau_s"] = float(creep_tau_s)
            self._send(f"W afe creep {lc['creep_frac']} {lc['creep_tau_s']}")
        if nonlin_pct_fs is not None or fs_n is not None:      # M3: non-linearity (R2: 0.03 % FS)
            if nonlin_pct_fs is not None:
                lc["nonlin_frac"] = float(nonlin_pct_fs) / 100.0
            if fs_n is not None:
                lc["fs_n"] = float(fs_n)
            self._send(f"W afe nonlin {lc['nonlin_frac']} {lc['fs_n']}")
        if rate_error is not None:
            self.world["afe"]["rate_error"] = rate_error
            self._send(f"W afe rate_error {rate_error}")
        if noise_counts is not None:
            self.world["afe"]["noise_counts"] = noise_counts
            self._send(f"W afe noise {noise_counts}")
        if stall is not None:
            keep("stall", int(bool(stall)))
        if saturate != "_":
            keep("saturate", {"pos": 1, "neg": -1, None: 0}[saturate])
        if drop_every is not None:
            keep("drop_every", int(drop_every))
        if miss_next is not None:
            self._send(f"W afe miss_next {int(miss_next)}")
        if sck_overrun:
            self._send("W afe sck_overrun 1")
        if raw_script is not None:
            self._send("W afescript " + " ".join(str(int(v)) for v in raw_script[:256]))

    def _a_driver(self, dir_wiring_inverted: bool | None = None, pend_auto: bool | None = None,
                  pend_lag_ms: float | None = None):
        """M2 (REQ-C-M2-06/07): DIR wiring / driver SW5 inverted in the world; automatic driver PEND model
        (PEND inactive while pulsing and pend_lag_ms after the last pulse)."""
        if dir_wiring_inverted is not None:
            self.driver_cfg["dir_wiring_inverted"] = bool(dir_wiring_inverted)
            self._send(f"W dirwiring {int(bool(dir_wiring_inverted))}")
        if pend_auto is not None or pend_lag_ms is not None:
            if pend_auto is not None:
                self.driver_cfg["pend_auto"] = bool(pend_auto)
            if pend_lag_ms is not None:
                self.driver_cfg["pend_lag_ms"] = float(pend_lag_ms)
            self._send(f"W pendauto {int(self.driver_cfg['pend_auto'])} {int(self.driver_cfg['pend_lag_ms'] * 1e6)}")
        return {"ok": True, **self.driver_cfg}

    def _stim(self, src: int, pol: int, hold_ms: int, n: int, period_ticks: int, seed: int) -> None:
        """DIAG_MEAS STIM_RUN (model): n pulses on the selected input, each after a seeded random delay of
        0…1 step period, active for hold_ms; the next delay starts at the end of the hold (board meas_f4.c,
        v0.7.3 OBS-E-HG-02; earlier models added another hold_ms idle gap) (FW_test_plan §6.1 MT-7)."""
        import random as _r  # noqa: PLC0415
        rnd = _r.Random(seed)
        name = {0: "estop", 1: "start", 2: "end", 3: "pause", 5: "drv_power"}.get(src)
        t = self.now
        period_ns = period_ticks * 1e9 / 90e6
        active = 0 if pol else 1                       # electrical level during the pulse
        for _ in range(n):
            t += int(rnd.random() * period_ns)
            t1, t2 = t, t + int(hold_ms * 1e6)
            if name is None:                           # STIM self-test (src 8) or unmodelled source: stamp only
                self.at(t1, lambda lv=active: self._send(f"W stimedge {lv}"), "stim")
                self.at(t2, lambda lv=1 - active: self._send(f"W stimedge {lv}"), "stim")
            else:
                self.at(t1, lambda nm=name, lv=active: self._stim_level(nm, lv), "stim")
                self.at(t2, lambda nm=name: self._stim_level(nm, None), "stim")
            t = t2                                     # next delay starts at the end of the hold (OBS-E-HG-02, as meas_f4.c)

    def _stim_level(self, name: str, level: int | None) -> None:
        if name in ("start", "end"):
            k = 0 if name == "start" else 1
            self._send(f"W limf {k} {-1 if level is None else level}")
        else:
            lv = self._level(name) if level is None else level
            self._send(f"W in {INPUT_ID[name]} {lv}")

    def _a_world_shift(self, um: float):
        self.shift_um += um
        self._send(f"W shift {um}")

    def _a_inject(self, fault: str, duration_ms: float | None = None, where: str = "main", cmd: str | None = None,
                  what: str = "request", n: int = 1, ms: float = 0, us_per_pass: float = 500.0):
        end = VT_NEVER if duration_ms is None else self.now + int(duration_ms * 1e6)
        if fault == "step_fault":
            self._send("W stepfault 1")
        elif fault == "tx_congestion":
            self._send(f"W cong {self.now + int((duration_ms or 0) * 1e6)}")
        elif fault == "rx_corrupt":
            self.rx_corrupt_until = end
        elif fault == "link_silence":
            self.link_silence_until = end
        elif fault == "hang":
            if where not in ("main", "tick", "isr1"):
                return {"ok": False, "error": f"hang where={where}"}
            self._send(f"W hang {where} {min(end, 2**63)}")
        elif fault == "loop_load":                   # M2 (REQ-C-M2-02): each main-loop pass takes us_per_pass
            per = int(float(us_per_pass) * 1000)
            until = VT_NEVER if duration_ms is None else end
            self.loop_load_line = f"W loopload {per} {until}"
            self._send(self.loop_load_line)
        elif fault == "isr_storm":                   # level-1 ISR storm (M2): = hang where=isr1
            if duration_ms is None:
                return {"ok": False, "error": "isr_storm needs duration_ms"}
            self._send(f"W hang isr1 {end}")
        elif fault in ("drop_next", "duplicate_next", "delay_next", "corrupt_next"):
            if cmd is None:
                raise ValueError("cmd required")
            t = rc.CMD[cmd] | (0 if what == "request" else rc.RESP_BIT)
            rec = {"fault": fault, "type": t, "n": int(n), "ms": float(ms)}
            (self.req_faults if what == "request" else self.resp_faults).append(rec)
        else:
            raise ValueError(f"fault {fault}")

    def _a_rx_bytes(self, hex: str, at_us: float | None = None):
        data = bytes.fromhex(hex)
        if at_us is None:
            self._inject(data, self.now)
            return {"ok": True}
        t = int(round(at_us * 1000))
        if t < self.now:
            return {"ok": False, "error": f"at_us {at_us} is in the past (now {self.now_us})"}
        self.at(t, lambda: self._inject(data, t), "rx_bytes")     # injected at t (may be ahead of now)

    def _a_on_frame(self, cmd: str, then: dict, delay_us: float = 0, nth: int = 1):
        t = rc.CMD[cmd]
        self.on_frame_triggers.append({"type": t, "nth": int(nth), "base": self.rx_counts.get(t, 0),
                                       "delay_us": delay_us, "then": then})

    def _a_on_event(self, code: str, then: dict, delay_us: float = 0, nth: int = 1):
        if code not in rc.EVENT:
            raise ValueError(f"event {code}")
        self.on_event_triggers.append({"code": code, "nth": int(nth), "base": self.ev_counts.get(code, 0),
                                       "delay_us": delay_us, "then": then})
        self._sync_break()

    def _a_flash(self, cut_after_word: int | None = None, cut_in_erase: int | None = None, reset: str = "power"):
        if cut_after_word is not None:
            self._send(f"W fcut word {int(cut_after_word)}")
        if cut_in_erase is not None:
            self._send(f"W fcut erase {int(cut_in_erase)}")

    def _a_iwdg(self, lsi_hz: float):
        if not 17000 <= lsi_hz <= 47000:
            raise ValueError("lsi_hz 17000..47000")
        self.lsi_hz = float(lsi_hz)
        self._send(f"W lsi {lsi_hz}")

    def _a_clk(self, hse_fail: bool):
        self.hse_fail = bool(hse_fail)
        return {"ok": True, "note": "effective at the next boot"}

    def _a_reset(self, cause: str = "pin", pc: int = 0x08001A2C, cfsr: int = 0x00008200):
        if cause == "hardfault":                     # seam v1.2: HardFault record + NVIC_SystemReset (M2)
            self.fault_rec = (int(pc) & 0xFFFFFFFF, int(cfsr) & 0xFFFFFFFF)
            cause = "software"
        if cause not in RESET_CAUSE_OF:
            raise ValueError(f"cause {cause}")
        self._restart(cause, self.now, external=True)
        if self.eng:
            self.eng.flush()

    def _a_query(self, what: str, since_us: float = 0.0, clear: bool = False):
        def since(log):
            out = [x for x in log if x.get("t_us", x.get("first_us", 0)) >= since_us]
            if clear:                     # REQ-C-M2-10: drain the log after reading (per-query ring)
                log.clear()
            return out
        y = self._engine_query()          # also drains the engine's pending log lines (I / L / E / O / W)
        if what == "world":
            return {"ok": True, "now_us": self.now_us, "fw_t_us": self.fw_t_us(), "boot_us": self.boot_ns / 1000,
                    "t0_us": self.t0_us, "x_um_true": float(y["x_um_true"]), "pos_steps": int(y["pos_steps"]),
                    "load_n": float(y["load_n"]), "specimen_n": float(y["spec_n"]), "weight_n": float(y["weight_n"]),
                    "relax_n": float(y["relax_n"]), "creep_counts": float(y["creep_counts"]),
                    "drift_counts": float(y["drift_counts"]), "raw_ideal": float(y["load_raw_ideal"]),
                    "inputs": {n: self._level(n) for n in INPUT_ID if n != "stop"},
                    "broken": sorted(self.broken), "drv_power": self.drv_on, "estop_open": bool(self.inputs["estop"]),
                    "afe_rate_sps": int(y["afe_rate_sps"]), "afe_conversions": int(y["afe_conversions"]),
                    "rx_overruns": int(y["rx_overruns"]), "resets": list(self.resets), "core": self.core}
        if what == "pulses":
            return {"ok": True, "count": self.pul_rising, "pos_steps": int(y["pos_steps"]),
                    "running": y["step_running"] == "1", "pos_uncertain": y["pos_uncertain"] == "1"}
        if what == "outputs":
            return {"ok": True, "ENA": int(y["ena_level"]), "ena_enabled": y["ena_enabled"] == "1",
                    "RATE": int(y["rate_pin"]), "LED": int(y["led"]), "TRIP": int(y["trip"])}
        if what == "edges":
            return {"ok": True, "edges": since(self.edges)}
        if what == "seam_log":
            return {"ok": True, "seam_log": since(self.seam_log)}
        if what == "wire_log":
            return {"ok": True, "wire_log": since(self.wire_log)}
        if what == "sent":
            return {"ok": True, "sent": since(self.sent)}
        if what == "flash":
            return {"ok": True, "writes": int(y["flash_writes"]), "erases": int(y["flash_erases"]),
                    "path": str(self.run_dir / "flash.bin")}
        if what == "conversions":          # T-only extension: every HX711 conversion of the model
            return {"ok": True, "conversions": since(self.conversions)}
        if what == "inputs":               # T-only extension: electrical input changes seen by the HAL
            return {"ok": True, "inputs": since(self.input_log)}
        raise ValueError(f"query {what}")

    def _engine_query(self) -> dict[str, str]:
        assert self.eng
        self.eng.send("Q")
        self.eng.flush()
        out = {}
        while True:
            line = self.eng.readline()
            if line.startswith("Y "):
                k, _, v = line[2:].partition(" ")
                if k == ".":
                    return out
                out[k] = v
            else:
                self._handle(line)

    # ------------------------------------------------------------------ scenario
    def _load_scenario(self, sc: dict | str | Path) -> None:
        if not isinstance(sc, dict):
            sc = json.loads(Path(sc).read_text(encoding="utf-8"))
        if sc.get("schema") != "bird.bend.simscenario" or sc.get("version") != 1:
            raise TwinError("scenario: schema bird.bend.simscenario v1 expected")
        w = sc.get("world", {})
        for k in ("stroke_um", "start_switch_um", "end_switch_um", "cell_counts_per_n", "load_offset_counts"):
            if k in w:
                self.world[k] = w[k]
        self.world["afe"].update(w.get("afe", {}))
        afe = w.get("afe", {})                       # v0.7.2 cell model in scenarios (SWC-M3-02: the simulator's
        if "drift_counts_per_s" in afe:              # drift_counts_per_min accepted as an alias)
            self.load_cfg["drift_cps"] = float(afe["drift_counts_per_s"])
        elif "drift_counts_per_min" in afe:
            self.load_cfg["drift_cps"] = float(afe["drift_counts_per_min"]) / 60.0
        if "creep_pct" in afe:
            self.load_cfg["creep_frac"] = float(afe["creep_pct"]) / 100.0
            self.load_cfg["creep_tau_s"] = float(afe.get("creep_tau_s", 600.0))
        if "nonlin_pct_fs" in afe:
            self.load_cfg["nonlin_frac"] = float(afe["nonlin_pct_fs"]) / 100.0
        if "weight_kg" in w:
            self.load_cfg["weight_n"] = float(w["weight_kg"]) * 9.80665
        if spec_relax := w.get("specimen", {}).get("relax_pct"):
            self.load_cfg["relax_frac"] = float(spec_relax) / 100.0
            self.load_cfg["relax_tau_s"] = float(w["specimen"].get("relax_tau_s", 10.0))
        self.world["steps_per_mm"] = float(sc.get("params", {}).get("motion.steps_per_mm", 800.0))
        spec = w.get("specimen", {"kind": "none"})
        kc = {"none": 0, "spring": 1, "bilinear": 2}[spec.get("kind", "none")]
        self.spec_line = (f"W spec {kc} {spec.get('k_n_per_mm', 0)} {spec.get('x_contact_um', 0)} "
                          f"{spec.get('k2_n_per_mm', 0)} {spec.get('f_yield_n', 0)} {spec.get('f_break_n', 0)}")
        self.drv_on = bool(w.get("driver", {}).get("drv_power", True))
        self.inputs["estop"] = 1 if w.get("estop_open") else 0
        self.scenario_params = dict(sc.get("params", {}))
        for e in sc.get("schedule", []):
            e = dict(e)
            t = int(e.pop("t_ms") * 1e6)
            self.at(t, lambda a=e: self._act_unlocked(dict(a)), "schedule")

    # ------------------------------------------------------------------ TCP servers
    def serve(self, port: int = TWIN_TCP_PORT, ctl_port: int = TWIN_CTL_PORT, host: str = "127.0.0.1") -> tuple[int, int]:
        """Serve the FW link on `port` and the control port (JSON lines) on `ctl_port`; 0 = any free port."""
        ds = socket.create_server((host, port))
        cs = socket.create_server((host, ctl_port))
        self._servers += [ds, cs]
        for target, srv, name in ((self._data_srv, ds, "twin-data"), (self._ctl_srv, cs, "twin-ctl")):
            t = threading.Thread(target=target, args=(srv,), name=name, daemon=True)
            t.start()
            self._threads.append(t)
        return ds.getsockname()[1], cs.getsockname()[1]

    def _data_srv(self, srv: socket.socket) -> None:
        srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                c, _ = srv.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self.lock:
                if self._client_sock:
                    try:
                        self._client_sock.close()
                    except OSError:
                        pass
                self._client_sock = c
            threading.Thread(target=self._data_reader, args=(c,), name="twin-data-rx", daemon=True).start()

    def _data_reader(self, c: socket.socket) -> None:
        c.settimeout(0.2)
        while not self._stop.is_set():
            try:
                d = c.recv(4096)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            if not d:
                break
            self.feed_rx(d)
        with self.lock:
            if self._client_sock is c:
                self._client_sock = None

    def _ctl_srv(self, srv: socket.socket) -> None:
        srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                c, _ = srv.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            threading.Thread(target=self._ctl_client, args=(c,), name="twin-ctl-client", daemon=True).start()

    def _ctl_client(self, c: socket.socket) -> None:
        f = c.makefile("rwb")
        try:
            for raw in f:
                try:
                    req = json.loads(raw)
                    rep = self.act(req)
                except Exception as e:  # noqa: BLE001
                    rep = {"ok": False, "error": repr(e)}
                f.write((json.dumps(rep) + "\n").encode())
                f.flush()
        except OSError:
            pass
        finally:
            c.close()


VT_NEVER = 2**64 - 1


# =============================================================================================== client
class TwinLink:
    """Minimal in-process PC side for lock-step tests (ref_codec oracle; not B's production codec)."""

    def __init__(self, tw: Twin, seq0: int = 0x40):
        self.tw = tw
        self.seq = seq0
        self.parser = rc.FrameParser()
        self.frames: list[rc.Frame] = []        # every FW->PC frame received, in order
        self._t: list[float] = []

    def poll(self) -> list[rc.Frame]:
        new = self.parser.feed(self.tw.read_client())
        self.frames += new
        return new

    def send(self, name: str, fields: dict | None = None, seq: int | None = None, at_us: float | None = None) -> int:
        if seq is None:
            seq = self.seq
            self.seq = (self.seq + 1) & 0xFF
        fr = rc.make_frame(name, seq, fields or {})
        if at_us is None:
            self.tw.feed_rx(fr)
        else:
            r = self.tw.act("rx_bytes", hex=fr.hex(), at_us=at_us)
            assert r["ok"], r
        return seq

    def cmd(self, name: str, fields: dict | None = None, timeout_ms: float = 50, step_ms: float = 0.5) -> dict:
        t0 = self.tw.now
        seq = self.send(name, fields)
        want = rc.CMD[name] | rc.RESP_BIT
        while (self.tw.now - t0) / 1e6 <= timeout_ms:
            self.tw.advance_ms(step_ms)
            for f in self.poll():
                if f.type == want and f.seq == seq:
                    d = rc.decode_frame(f)
                    d["_waited_ms"] = (self.tw.now - t0) / 1e6      # virtual time until it was seen
                    return d
        raise TimeoutError(f"{name}: no response within {timeout_ms} ms (virtual)")

    def find(self, name: str, seq: int) -> dict | None:
        """Decoded response to `seq` among the frames received so far (None if absent)."""
        want = rc.CMD[name] | rc.RESP_BIT
        for f in self.frames:
            if f.type == want and f.seq == seq:
                return rc.decode_frame(f)
        return None

    def data(self) -> list[dict]:
        return [rc.decode_data(f.payload) for f in self.frames if f.type == rc.ASYNC["DATA"]]

    def events(self) -> list[dict]:
        return [rc.decode_event(f.payload) for f in self.frames if f.type == rc.ASYNC["EVENT"]]


# =============================================================================================== CLI
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=TWIN_TCP_PORT, help="FW link (serial over TCP), PROTO_TWIN_TCP_PORT")
    ap.add_argument("--ctl", type=int, default=TWIN_CTL_PORT, help="world-control port (JSON lines), PROTO_TWIN_CTL_PORT")
    ap.add_argument("--clock", choices=("lockstep", "realtime"), default="realtime")
    ap.add_argument("--speed", type=float, default=1.0, help="realtime: virtual seconds per wall second")
    ap.add_argument("--scenario", type=Path)
    ap.add_argument("--t0-us", type=lambda s: int(s, 0), default=0, help="FW time base at boot (2^32 wrap tests)")
    ap.add_argument("--core", choices=("auto", "fw", "probe"), default="auto")
    ap.add_argument("--run-dir", type=Path, default=HERE / "build" / "run", help="flash.bin lives here")
    ap.add_argument("--keep-flash", action="store_true", help="start from the existing flash.bin")
    a = ap.parse_args()
    tw = Twin(a.clock, core=a.core, run_dir=a.run_dir, fresh_flash=not a.keep_flash, scenario=a.scenario,
              t0_us=a.t0_us, speed=a.speed)
    p, c = tw.serve(a.port, a.ctl)
    print(f"fw_twin ({tw.core} core, {a.clock}) serving tcp://127.0.0.1:{p}, control {c}; Ctrl+C to stop",
          flush=True)
    try:
        while True:
            time.sleep(0.5)
            if tw.errors:
                print("\n".join(tw.errors), file=sys.stderr)
                tw.errors.clear()
    except KeyboardInterrupt:
        pass
    finally:
        tw.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
