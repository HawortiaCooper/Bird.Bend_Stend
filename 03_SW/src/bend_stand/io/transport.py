"""Byte transports (SW_design §4.1): ``Transport`` ABC with a ``wire_log`` on every transport (SWD-P1-09 b),
``SerialTransport`` (pyserial COM port, opened only on an explicit operator selection, D-06),
``VirtualTransportPair`` (in-memory pipes for the in-process simulator, clock-driven so it works on the lockstep
clock, optional baud pacing 92 160 B/s and latency, unplug/replug) and ``TcpTransport`` (FW host twin /
out-of-process simulator, ``tcp://host:port``).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/io/transport.py @37c87471 (adapted: Clock-driven virtual pipes,
wire_log in the base class, endpoint parser incl. ``sim``; SerialTransport and TcpTransport as-is apart from
the error class).

Implements: IF-002 (921 600 Bd 8N1, no flow control), IF-011 (whole frames, wire log), SYS-008, D-06
"""
from __future__ import annotations

import collections
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from bend_stand.core.clock import MONOTONIC, Clock
from bend_stand.core.errors import TransportError

BAUD = 921_600
LINK_BYTES_PER_S = BAUD // 10          # 8N1: 92 160 B/s (ICD §1)
READ_TIMEOUT_S = 0.002                 # Reader read timeout (§4.3)


@dataclass(frozen=True, slots=True)
class WireRecord:
    t_ns: int
    direction: Literal["TX", "RX"]
    frame: bytes


class Transport(ABC):
    """Byte stream. ``read`` returns ``b""`` on timeout; failures raise ``TransportError``."""

    baud: int = BAUD

    def __init__(self, clock: Clock = MONOTONIC) -> None:
        self.clock = clock
        self.wire_log: collections.deque[WireRecord] | None = None
        self.last_write_ns: int = 0
        self.bytes_written = 0

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def _read(self, max_bytes: int, timeout_s: float) -> bytes: ...

    @abstractmethod
    def _write(self, data: bytes) -> None: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @property
    def name(self) -> str:
        return type(self).__name__

    # ---- common -----------------------------------------------------------------------------------
    def enable_wire_log(self, maxlen: int = 200_000) -> None:
        if self.wire_log is None:
            self.wire_log = collections.deque(maxlen=maxlen)

    def read(self, max_bytes: int, timeout_s: float = READ_TIMEOUT_S) -> bytes:
        return self._read(max_bytes, timeout_s)

    def write(self, data: bytes) -> None:
        """Write one whole frame (the FrameWriter never interleaves frames)."""
        self._write(bytes(data))
        t = self.clock.monotonic_ns()
        self.last_write_ns = t
        self.bytes_written += len(data)
        wl = self.wire_log
        if wl is not None:
            wl.append(WireRecord(t, "TX", bytes(data)))

    def log_rx(self, frame: bytes, t_ns: int) -> None:
        """Called by the Reader for every frame it completed (stamp of the read that completed it)."""
        wl = self.wire_log
        if wl is not None:
            wl.append(WireRecord(t_ns, "RX", bytes(frame)))

    def drain_input(self) -> bytes:
        """Pending bytes at open (connect step 1: fed to the parser, not flushed blindly, R3 P13)."""
        out = bytearray()
        while True:
            chunk = self._read(65536, 0.0)
            if not chunk:
                return bytes(out)
            out += chunk


# ============================================================================== serial (D-06)

class SerialTransport(Transport):
    """COM port via pyserial: 921 600 Bd 8N1, no flow control (ICD §1). Opened only by an explicit ``open()``
    after the operator selected the port (D-06). The read timeout is configured once (TS SWD-M1-01)."""

    WRITE_TIMEOUT_S = 0.030

    def __init__(self, port: str, baud: int = BAUD, clock: Clock = MONOTONIC) -> None:
        super().__init__(clock)
        self.port = port
        self.baud = int(baud)
        self._ser = None
        self._wlock = threading.Lock()

    @property
    def name(self) -> str:
        return self.port

    def open(self) -> None:
        import serial  # noqa: PLC0415 (lazy: nothing touches a port before the operator's choice)

        try:
            self._ser = serial.Serial(self.port, self.baud, bytesize=8, parity="N", stopbits=1,
                                      timeout=READ_TIMEOUT_S, write_timeout=self.WRITE_TIMEOUT_S,
                                      rtscts=False, dsrdtr=False, xonxoff=False)
        except (serial.SerialException, OSError, ValueError) as exc:
            self._ser = None
            raise TransportError(f"cannot open {self.port}: {exc}") from exc

    def close(self) -> None:
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:  # noqa: BLE001 pragma: no cover
                pass

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def _read(self, max_bytes: int, timeout_s: float) -> bytes:
        ser = self._ser
        if ser is None:
            raise TransportError(f"{self.port} not open")
        try:
            n = ser.in_waiting
            if n:
                return ser.read(min(n, max_bytes))
            if timeout_s <= 0:
                return b""
            data = ser.read(1)                      # waits ≤ the configured timeout (set once at open)
            if data:
                n = ser.in_waiting
                if n:
                    data += ser.read(min(n, max_bytes - 1))
            return data
        except Exception as exc:  # noqa: BLE001 (SerialException, OSError on unplug)
            raise TransportError(f"{self.port}: {exc}") from exc

    def _write(self, data: bytes) -> None:
        ser = self._ser
        if ser is None:
            raise TransportError(f"{self.port} not open")
        with self._wlock:
            try:
                ser.write(data)
            except Exception as exc:  # noqa: BLE001
                raise TransportError(f"{self.port}: {exc}") from exc


# ============================================================================== virtual pair (simulator)

class _Pipe:
    """One direction: chunks become readable at their delivery time on the clock."""

    def __init__(self, clock: Clock, bytes_per_s: float | None, latency_ns: int) -> None:
        self.clock = clock
        self.cv = threading.Condition()
        self.q: collections.deque[tuple[int, bytes]] = collections.deque()
        self.bytes_per_s = bytes_per_s
        self.latency_ns = latency_ns
        self.tx_free_ns = 0
        self.bytes_total = 0

    def put(self, data: bytes) -> None:
        now = self.clock.monotonic_ns()
        with self.cv:
            start = max(now, self.tx_free_ns)
            dur = int(len(data) * 1e9 / self.bytes_per_s) if self.bytes_per_s else 0
            self.tx_free_ns = start + dur
            self.q.append((self.tx_free_ns + self.latency_ns, data))
            self.bytes_total += len(data)
            self.cv.notify_all()

    def get(self, max_bytes: int, timeout_s: float, closed: Callable[[], bool]) -> bytes:
        clock = self.clock
        deadline = clock.monotonic_ns() + int(timeout_s * 1e9)
        with self.cv:
            while True:
                if closed():
                    raise TransportError("virtual port closed / unplugged")
                now = clock.monotonic_ns()
                out = bytearray()
                while self.q and self.q[0][0] <= now and len(out) < max_bytes:
                    t, chunk = self.q[0]
                    take = max_bytes - len(out)
                    if len(chunk) <= take:
                        out += chunk
                        self.q.popleft()
                    else:
                        out += chunk[:take]
                        self.q[0] = (t, chunk[take:])
                if out:
                    return bytes(out)
                if clock.is_lockstep or now >= deadline:
                    return b""
                wait_until = deadline if not self.q else min(deadline, self.q[0][0])
                self.cv.wait(max(0.0, (wait_until - now) / 1e9))

    def pending(self) -> int:
        with self.cv:
            return sum(len(c) for _t, c in self.q)

    def clear(self) -> None:
        with self.cv:
            self.q.clear()
            self.tx_free_ns = 0


class VirtualEnd(Transport):
    def __init__(self, pair: VirtualTransportPair, side: Literal["pc", "board"]) -> None:
        super().__init__(pair.clock)
        self._pair = pair
        self._side = side
        self._open = side == "board"             # the board end is always "powered"

    @property
    def name(self) -> str:
        return "sim" if self._side == "pc" else "sim-board"

    def open(self) -> None:
        if self._pair.unplugged:
            raise TransportError("virtual port unplugged")
        self._open = True

    def close(self) -> None:
        self._open = False
        if self._side == "pc":
            self._pair.to_pc.clear()

    @property
    def is_open(self) -> bool:
        return self._open and not self._pair.unplugged

    def _read(self, max_bytes: int, timeout_s: float) -> bytes:
        pipe = self._pair.to_pc if self._side == "pc" else self._pair.to_board
        return pipe.get(max_bytes, timeout_s, lambda: not self.is_open)

    def _write(self, data: bytes) -> None:
        if not self.is_open:
            raise TransportError("virtual port not open")
        pipe = self._pair.to_board if self._side == "pc" else self._pair.to_pc
        pipe.put(data)


class VirtualTransportPair:
    """Two in-memory byte pipes with ``.pc`` and ``.board`` ends (SW_design §4.1). ``bytes_per_s`` (None =
    unlimited) paces each direction, ``latency_ns`` is added to every chunk; delivery follows ``clock``."""

    def __init__(self, clock: Clock = MONOTONIC, bytes_per_s: float | None = LINK_BYTES_PER_S,
                 latency_ns: int = 0) -> None:
        self.clock = clock
        self.to_board = _Pipe(clock, bytes_per_s, latency_ns)
        self.to_pc = _Pipe(clock, bytes_per_s, latency_ns)
        self.pc = VirtualEnd(self, "pc")
        self.board = VirtualEnd(self, "board")
        self.unplugged = False

    def unplug(self) -> None:
        self.unplugged = True
        for p in (self.to_board, self.to_pc):
            with p.cv:
                p.cv.notify_all()

    def replug(self) -> None:
        self.unplugged = False
        self.to_pc.clear()
        self.to_board.clear()


# ============================================================================== TCP (FW twin / sim server)

class TcpTransport(Transport):
    """Byte stream over TCP (``tcp://host:port``) to the FW host twin or the out-of-process simulator."""

    def __init__(self, host: str, port: int, clock: Clock = MONOTONIC, connect_timeout_s: float = 1.0,
                 sock: object | None = None) -> None:
        super().__init__(clock)
        self.host = host
        self.port = int(port)
        self.connect_timeout_s = connect_timeout_s
        self._sock = sock
        self._wlock = threading.Lock()
        if sock is not None:
            self._prepare(sock)

    @classmethod
    def from_socket(cls, sock: object, clock: Clock = MONOTONIC) -> TcpTransport:
        host, port = sock.getpeername()[:2]  # type: ignore[attr-defined]
        return cls(host, port, clock, sock=sock)

    @property
    def name(self) -> str:
        return f"tcp://{self.host}:{self.port}"

    @staticmethod
    def _prepare(sock: object) -> None:
        import socket as _s  # noqa: PLC0415

        sock.setsockopt(_s.IPPROTO_TCP, _s.TCP_NODELAY, 1)  # type: ignore[attr-defined]
        sock.setblocking(False)  # type: ignore[attr-defined]

    def open(self) -> None:
        import socket as _s  # noqa: PLC0415

        if self._sock is not None:
            return
        try:
            sock = _s.create_connection((self.host, self.port), timeout=self.connect_timeout_s)
        except OSError as exc:
            raise TransportError(f"cannot connect to {self.name}: {exc}") from exc
        self._prepare(sock)
        self._sock = sock

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()  # type: ignore[attr-defined]
            except OSError:  # pragma: no cover
                pass

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def _read(self, max_bytes: int, timeout_s: float) -> bytes:
        import select  # noqa: PLC0415

        sock = self._sock
        if sock is None:
            raise TransportError(f"{self.name} not open")
        try:
            r, _w, _x = select.select([sock], [], [], max(0.0, timeout_s))
            if not r:
                return b""
            data = sock.recv(max_bytes)  # type: ignore[attr-defined]
        except (BlockingIOError, InterruptedError):
            return b""
        except OSError as exc:
            raise TransportError(f"{self.name}: {exc}") from exc
        if not data:
            raise TransportError(f"{self.name}: connection closed by peer")
        return data

    def _write(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise TransportError(f"{self.name} not open")
        with self._wlock:
            try:
                sock.setblocking(True)  # type: ignore[attr-defined]
                sock.settimeout(0.05)  # type: ignore[attr-defined]
                sock.sendall(data)  # type: ignore[attr-defined]
            except OSError as exc:
                raise TransportError(f"{self.name}: {exc}") from exc
            finally:
                try:
                    sock.setblocking(False)  # type: ignore[attr-defined]
                except OSError:  # pragma: no cover
                    pass


# ============================================================================== endpoints

@dataclass(frozen=True)
class Endpoint:
    kind: Literal["serial", "tcp", "sim"]
    target: str = ""          # COM name / host / scenario path ("" = default scenario)
    port: int = 0


def parse_endpoint(endpoint: str) -> Endpoint:
    """``"COM7"`` → serial; ``"tcp://host:port"`` (or ``socket://``) → tcp; ``"sim"`` / ``"sim:<file>"`` → sim."""
    p = endpoint.strip()
    low = p.lower()
    if low == "sim":
        return Endpoint("sim")
    if low.startswith("sim:"):
        return Endpoint("sim", p[4:])
    for scheme in ("tcp://", "socket://"):
        if low.startswith(scheme):
            rest = p[len(scheme):].rstrip("/")
            host, _, prt = rest.rpartition(":")
            if not host or not prt.isdigit():
                raise ValueError(f"bad address {endpoint!r} (expected {scheme}host:port)")
            return Endpoint("tcp", host.strip("[]"), int(prt))
    if "://" in p or not p:
        raise ValueError(f"unsupported endpoint {endpoint!r}")
    return Endpoint("serial", p)


def transport_factory(endpoint: str, clock: Clock = MONOTONIC) -> Transport:
    """Transport for a COM or TCP endpoint — **nothing is opened here** (D-06). ``sim`` is wired by the backend."""
    ep = parse_endpoint(endpoint)
    if ep.kind == "tcp":
        return TcpTransport(ep.target, ep.port, clock)
    if ep.kind == "serial":
        return SerialTransport(ep.target, BAUD, clock)
    raise ValueError("the 'sim' endpoint is created by the backend (in-process simulator)")
