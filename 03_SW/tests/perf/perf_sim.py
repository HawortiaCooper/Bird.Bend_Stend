"""Perf-run simulator with a wire sniffer (SW_test_plan §6.1 PR-1…PR-5; SRS NFR-002 "virtual serial / sniffer").

Starts the product's out-of-process simulator (``bend_stand.io.sim.server.SimServer``, real clock) in THIS process
and puts a transparent TCP proxy (the *sniffer*) in front of its data port. The application connects to the
sniffer (``tcp://127.0.0.1:<data>``); every frame that passes is time-stamped with ``time.monotonic_ns()``
(QueryPerformanceCounter on Windows: one time base for every process on the PC) at the moment the bytes arrive
on the proxy socket — i.e. "on the wire", independent of the GUI process (own GIL) and of the simulator's
polling period. Frames are parsed with the Integrator's oracle ``ref_codec`` (never with the production codec).

Output (``--log``, JSON lines, line-buffered):
  {"t": ns, "d": "TX", "n": "STOP", "seq": 17, "f": {...}}         PC → board, every frame (fields decoded)
  {"t": ns, "d": "RX", "n": "DATA", "fs": 1234, "raw": .., "sp": .., "fl": [...]}   only with --log-data
  {"t": ns, "d": "RX", "n": "<response/EVENT name>", "seq": ..}     board → PC responses and EVENTs
  {"stat": {...}}                                                  every 10 s: DATA count, frame_seq gaps, OVERRUN
First stdout line: JSON {"data": <sniffer port>, "ctl": <sim control port>, "sim": <sim data port>, "pid": ...}.

Usage: ``python perf_sim.py --log sniff.jsonl [--log-data]``; stops at EOF / "quit" on stdin (fallback: terminate
by the recorded PID).

Verifies: NFR-002, NFR-003, NFR-004 (measurement tool, informative on the DEV PC; binding on the REF PC)
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SW_ROOT = HERE.parents[1]
REPO = SW_ROOT.parent
for p in (SW_ROOT / "src", REPO / "00_System" / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ref_codec as rc  # noqa: E402

from bend_stand.io.sim.server import SimServer  # noqa: E402


class Sniffer:
    def __init__(self, upstream_port: int, log_path: str, log_data: bool) -> None:
        self.upstream_port = upstream_port
        self.log_data = log_data
        self._f = open(log_path, "a", encoding="utf-8", buffering=1)
        self._wl = threading.Lock()
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.data = 0
        self.gaps = 0
        self.lost = 0
        self.overrun = 0
        self.tx_frames = 0
        self.rx_other = 0
        self.clients = 0
        self._last_fs: int | None = None
        self._stop = threading.Event()

    def write(self, rec: dict) -> None:
        line = json.dumps(rec, separators=(",", ":"), default=str)
        with self._wl:
            self._f.write(line + "\n")

    def start(self) -> None:
        threading.Thread(target=self._accept, name="sniff-accept", daemon=True).start()
        threading.Thread(target=self._stats, name="sniff-stats", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.srv.close()
        except OSError:
            pass
        self.write({"stat": self.stat(), "final": True})
        self._f.close()

    def stat(self) -> dict:
        return {"t": time.monotonic_ns(), "data": self.data, "gaps": self.gaps, "lost": self.lost,
                "overrun": self.overrun, "tx": self.tx_frames, "rx_other": self.rx_other, "clients": self.clients,
                "cpu_s": round(time.process_time(), 2)}

    def _stats(self) -> None:
        while not self._stop.wait(10.0):
            self.write({"stat": self.stat()})

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            try:
                u = socket.create_connection(("127.0.0.1", self.upstream_port), timeout=5)
            except OSError:
                c.close()
                continue
            for s in (c, u):
                s.settimeout(None)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.clients += 1
            self._last_fs = None
            self.write({"t": time.monotonic_ns(), "event": "client", "n": self.clients})
            threading.Thread(target=self._pump, args=(c, u, "TX"), daemon=True).start()
            threading.Thread(target=self._pump, args=(u, c, "RX"), daemon=True).start()

    def _pump(self, src: socket.socket, dst: socket.socket, d: str) -> None:
        parser = rc.FrameParser()
        try:
            while True:
                data = src.recv(65536)
                t = time.monotonic_ns()               # arrival on the wire (before forwarding)
                if not data:
                    break
                dst.sendall(data)
                for fr in parser.feed(data):
                    self._frame(t, d, fr)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass
            self.write({"t": time.monotonic_ns(), "event": f"closed {d}", "crc": parser.crc_errors})

    def _frame(self, t: int, d: str, fr: rc.Frame) -> None:
        kind, name = rc.classify(fr.type)
        if d == "TX":
            self.tx_frames += 1
            try:
                f = rc.decode_frame(fr)
            except Exception:  # noqa: BLE001
                f = {}
            self.write({"t": t, "d": "TX", "n": name, "seq": fr.seq, "f": f})
            return
        if name == "DATA":
            dd = rc.decode_data(fr.payload)
            fs = dd["frame_seq"]
            if self._last_fs is not None:
                step = (fs - self._last_fs) & 0xFFFF
                if step != 1:
                    self.gaps += 1
                    self.lost += (step - 1) if 1 < step < 0x8000 else 0
            self._last_fs = fs
            self.data += 1
            if "OVERRUN" in dd["flags"]:
                self.overrun += 1
            if self.log_data:
                self.write({"t": t, "d": "RX", "n": "DATA", "fs": fs, "raw": dd["afe_raw"], "sp": dd["setpoint_um"],
                            "tu": dd["t_us"], "fl": dd["flags"]})
            return
        self.rx_other += 1
        rec = {"t": t, "d": "RX", "n": name, "k": kind, "seq": fr.seq}
        if name == "EVENT":
            try:
                rec["f"] = rc.decode_frame(fr)
            except Exception:  # noqa: BLE001
                pass
        self.write(rec)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--log-data", action="store_true")
    ap.add_argument("--scenario", default=None)
    a = ap.parse_args(argv)
    srv = SimServer(0, 0, a.scenario)
    srv.start()
    sn = Sniffer(srv.port, a.log, a.log_data)
    sn.start()
    import os  # noqa: PLC0415
    print(json.dumps({"data": sn.port, "ctl": srv.ctl_port, "sim": srv.port, "pid": os.getpid()}), flush=True)
    try:                       # graceful stop: the starter closes our stdin (EOF) or writes "quit"
        for line in sys.stdin:
            if line.strip() == "quit":
                break
    except KeyboardInterrupt:
        pass
    finally:
        sn.stop()
        srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
