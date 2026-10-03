"""Out-of-process simulator over TCP (SW_design §1, §12.4, GRQ-B-16): the SimBoard on the real clock, its byte
stream on ``tcp://127.0.0.1:<port>`` (default 5770, endpoint ``tcp://127.0.0.1:5770``) and the shared
vocabulary-v2 control port (JSON lines, default 5771, same format as the twin's 5761).

``python -m bend_stand.io.sim.server [--port 5770] [--ctl 5771] [--scenario file.simscn.json]
[--features AFE_SYNTHETIC,NVM]`` (``--features`` selects the INFO feature mask, e.g. the M1 FW's, SW-C-M1-01)

One data client at a time; the board keeps running between clients (like a powered board on an unplugged USB).
Only loopback addresses are bound.

Implements: SYS-008 (perf runs without GIL contention), D-07
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import threading
from typing import Any

from bend_stand.core.clock import MONOTONIC
from bend_stand.core.errors import TransportError
from bend_stand.io.sim.board import SimBoard, SimConfig
from bend_stand.io.sim.control import SimControl, SimScenario, default_scenario, parse_features
from bend_stand.io.sim.models import Hx711Model
from bend_stand.io.transport import TcpTransport, Transport

log = logging.getLogger("bend_stand.sim.server")


class SwitchableTransport(Transport):
    """Board-side transport whose peer socket can be replaced; writes without a client are dropped."""

    def __init__(self) -> None:
        super().__init__(MONOTONIC)
        self._lock = threading.Lock()
        self._tr: TcpTransport | None = None

    def attach(self, sock: socket.socket) -> None:
        with self._lock:
            old, self._tr = self._tr, TcpTransport.from_socket(sock, MONOTONIC)
        if old is not None:
            old.close()

    def detach(self) -> None:
        with self._lock:
            old, self._tr = self._tr, None
        if old is not None:
            old.close()

    def open(self) -> None:
        return None

    def close(self) -> None:
        self.detach()

    @property
    def is_open(self) -> bool:
        return True

    def _read(self, max_bytes: int, timeout_s: float) -> bytes:
        tr = self._tr
        if tr is None:
            return b""
        try:
            return tr.read(max_bytes, 0.0)
        except TransportError:
            self.detach()
            return b""

    def _write(self, data: bytes) -> None:
        tr = self._tr
        if tr is None:
            return
        try:
            tr.write(data)
        except TransportError:
            self.detach()


class SimServer:
    def __init__(self, port: int = 5770, ctl_port: int = 5771, scenario: str | None = None,
                 host: str = "127.0.0.1", features: str | int | None = None) -> None:
        sc = SimScenario.load(scenario) if scenario else default_scenario()
        if features is not None:
            sc.features = parse_features(features)
        self.transport = SwitchableTransport()
        self.board = SimBoard(MONOTONIC, self.transport, config=SimConfig(seed=sc.seed), afe=Hx711Model(seed=sc.seed))
        self.control = SimControl(self.board)
        sc.apply(self.board, self.control)
        self.host = host
        self._data = socket.create_server((host, port))
        self._ctl = socket.create_server((host, ctl_port))
        self.port = self._data.getsockname()[1]
        self.ctl_port = self._ctl.getsockname()[1]
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self.board.start()
        for fn, name in ((self._accept_data, "sim-data"), (self._accept_ctl, "sim-ctl")):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for s in (self._data, self._ctl):
            try:
                s.close()
            except OSError:  # pragma: no cover
                pass
        self.transport.detach()
        self.board.stop()
        for t in self._threads:
            t.join(1.0)

    def _accept_data(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._data.accept()
            except OSError:
                return
            self.transport.attach(conn)

    def _accept_ctl(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._ctl.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_ctl, args=(conn,), daemon=True).start()

    def _serve_ctl(self, conn: socket.socket) -> None:
        with conn, conn.makefile("rw", encoding="utf-8", newline="\n") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    req: dict[str, Any] = json.loads(line)
                    action = req.pop("action")
                    reply = self.control.act(action, **req)
                except (ValueError, KeyError, TypeError) as exc:
                    reply = {"ok": False, "error": f"bad request: {exc}"}
                f.write(json.dumps(reply, default=str) + "\n")
                f.flush()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI wrapper
    ap = argparse.ArgumentParser(prog="python -m bend_stand.io.sim.server")
    ap.add_argument("--port", type=int, default=5770)
    ap.add_argument("--ctl", type=int, default=5771)
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--features", default=None, help="INFO feature mask: names (comma-separated) or an integer")
    a = ap.parse_args(argv)
    feats = int(a.features, 0) if a.features and a.features.strip()[0].isdigit() else a.features
    srv = SimServer(a.port, a.ctl, a.scenario, features=feats)
    srv.start()
    print(f"simulator on tcp://127.0.0.1:{srv.port} (control {srv.ctl_port}); Ctrl+C to stop", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
