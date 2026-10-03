"""Transports and the Reader: virtual pacing 92 160 B/s, latency, unplug, wire log, TCP loopback, Serial
settings applied once (mocked ``serial.Serial``; no port is opened, D-06), endpoint parsing, port enumeration,
Reader dispatch / arrival order / inter-byte timeout only after an empty read / rx_log.

Verifies: IF-002, IF-011, IF-003, SYS-008, SW-PLT-001
"""
from __future__ import annotations

import socket
import threading

import pytest

from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import LockstepClock
from bend_stand.core.errors import TransportError
from bend_stand.io import ports
from bend_stand.io import protocol as P
from bend_stand.io import transport as T
from bend_stand.io.framing import encode_frame
from bend_stand.io.reader import Reader


@pytest.mark.req("IF-002", "IF-011")
def test_virtual_pair_pacing_and_wire_log() -> None:
    c = LockstepClock(start_ns=0)
    pair = T.VirtualTransportPair(c, bytes_per_s=T.LINK_BYTES_PER_S, latency_ns=1_000_000)
    pair.pc.open()
    pair.pc.enable_wire_log()
    frame = bytes(921)                              # 921 B = 10 ms on a 92 160 B/s line
    pair.pc.write(frame)
    assert pair.pc.wire_log[-1].direction == "TX" and pair.pc.last_write_ns == 0
    assert pair.board.read(4096, 0.0) == b""
    c.advance(ns=10_993_000)
    assert pair.board.read(4096, 0.0) == b""        # 9.99 ms transfer + 1 ms latency
    c.advance(ns=20_000)
    assert len(pair.board.read(4096, 0.0)) == 921
    pair.board.write(b"abc")
    c.advance(ns=2_000_000)
    assert pair.pc.read(2, 0.0) == b"ab" and pair.pc.read(5, 0.0) == b"c"
    pair.pc.log_rx(b"xy", 5)
    assert pair.pc.wire_log[-1] == T.WireRecord(5, "RX", b"xy")
    assert pair.pc.bytes_written == 921


@pytest.mark.req("SYS-008")
def test_virtual_pair_unplug_and_real_clock_wait() -> None:
    pair = T.VirtualTransportPair(bytes_per_s=None)
    with pytest.raises(TransportError):
        pair.pc.write(b"x")                          # PC end not opened
    pair.pc.open()
    threading.Timer(0.01, lambda: pair.board.write(b"hi")).start()
    assert pair.pc.read(10, 1.0) == b"hi"           # blocking wait on the real clock
    assert pair.pc.read(10, 0.005) == b""
    pair.unplug()
    with pytest.raises(TransportError):
        pair.pc.read(10, 0.0)
    with pytest.raises(TransportError):
        pair.pc.open()
    pair.replug()
    pair.pc.open()
    assert pair.pc.is_open and pair.pc.name == "sim" and pair.board.name == "sim-board"
    pair.pc.close()
    assert not pair.pc.is_open
    assert pair.to_board.pending() == 0


@pytest.mark.req("SYS-008", "IF-002")
def test_tcp_transport_loopback() -> None:
    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    tr = T.transport_factory(f"tcp://127.0.0.1:{port}")
    assert isinstance(tr, T.TcpTransport) and tr.name == f"tcp://127.0.0.1:{port}"
    tr.open()
    tr.open()                                         # idempotent
    conn, _ = srv.accept()
    peer = T.TcpTransport.from_socket(conn)
    tr.write(b"hello")
    assert peer.read(100, 1.0) == b"hello"
    peer.write(b"world")
    assert tr.read(100, 1.0) == b"world"
    assert tr.read(100, 0.0) == b""
    assert tr.drain_input() == b""
    peer.close()
    with pytest.raises(TransportError):
        for _ in range(50):
            tr.read(100, 0.05)
    tr.close()
    assert not tr.is_open
    with pytest.raises(TransportError):
        tr.read(1, 0.0)
    with pytest.raises(TransportError):
        tr.write(b"x")
    srv.close()
    with pytest.raises(TransportError):
        T.TcpTransport("127.0.0.1", port, connect_timeout_s=0.2).open()


class _FakeSerial:
    instances: list = []

    def __init__(self, port, baud, **kw) -> None:
        self.port, self.baud, self.kw = port, baud, kw
        self.timeout_sets = 0
        self.is_open = True
        self.rx = bytearray(b"\x01\x02\x03")
        self.tx = bytearray()
        _FakeSerial.instances.append(self)

    @property
    def in_waiting(self) -> int:
        return len(self.rx)

    def read(self, n: int) -> bytes:
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def write(self, data: bytes) -> int:
        self.tx += data
        return len(data)

    def close(self) -> None:
        self.is_open = False


@pytest.mark.req("IF-002")
def test_serial_settings_applied_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import serial

    monkeypatch.setattr(serial, "Serial", _FakeSerial)
    tr = T.transport_factory("COM7")
    assert isinstance(tr, T.SerialTransport) and tr.name == "COM7" and not tr.is_open
    with pytest.raises(TransportError):
        tr.read(1, 0.0)
    tr.open()
    s = _FakeSerial.instances[-1]
    assert (s.port, s.baud) == ("COM7", 921600)
    assert s.kw["bytesize"] == 8 and s.kw["parity"] == "N" and s.kw["stopbits"] == 1
    assert s.kw["rtscts"] is False and s.kw["xonxoff"] is False and s.kw["timeout"] == T.READ_TIMEOUT_S
    assert tr.read(10, 0.002) == b"\x01\x02\x03"
    assert tr.read(10, 0.0) == b"" and tr.read(10, 0.002) == b""
    tr.write(b"ab")
    assert bytes(s.tx) == b"ab"
    tr.close()
    assert not tr.is_open
    with pytest.raises(TransportError):
        tr.write(b"x")


@pytest.mark.req("SW-PLT-001")
def test_serial_open_is_guarded_in_tests() -> None:
    from bend_stand.core.errors import HardwareAccessForbidden

    with pytest.raises((TransportError, HardwareAccessForbidden)):
        T.SerialTransport("COM9").open()


@pytest.mark.req("SW-PLT-001", "SYS-008")
def test_parse_endpoint() -> None:
    assert T.parse_endpoint("COM7") == T.Endpoint("serial", "COM7")
    assert T.parse_endpoint("sim") == T.Endpoint("sim")
    assert T.parse_endpoint("sim:a.json") == T.Endpoint("sim", "a.json")
    assert T.parse_endpoint("tcp://[::1]:5760") == T.Endpoint("tcp", "::1", 5760)
    assert T.parse_endpoint("socket://h:1") == T.Endpoint("tcp", "h", 1)
    for bad in ("tcp://nohost", "http://x:1", ""):
        with pytest.raises(ValueError):
            T.parse_endpoint(bad)
    with pytest.raises(ValueError):
        T.transport_factory("sim")


@pytest.mark.req("SW-PLT-001")
def test_port_listing_never_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    from serial.tools import list_ports as lp

    class P_:
        def __init__(self, dev, vid, pid) -> None:
            self.device, self.description, self.vid, self.pid, self.serial_number = dev, "x", vid, pid, None
    monkeypatch.setattr(lp, "comports", lambda: [P_("COM3", 1, 2), P_("COM9", 0x0483, 0x374B)])
    out = ports.list_ports()
    assert [p.device for p in out] == ["COM9", "COM3"] and out[0].is_stlink
    assert ports.auto_detect() == "COM9"


# ------------------------------------------------------------------------------------------------ reader

def _data(seq: int, t: int) -> bytes:
    return encode_frame(pg.AsyncType.DATA, seq & 0xFF, P.encode_data(P.DataSample(t, 1, 0, 5, 6, seq, 0)))


@pytest.mark.req("IF-003", "IF-011")
def test_reader_dispatch_order_and_logs() -> None:
    c = LockstepClock(start_ns=0)
    pair = T.VirtualTransportPair(c, bytes_per_s=None)
    pair.pc.open()
    pair.pc.enable_wire_log()
    resp, asyn, beats, errs = [], [], [], []
    rd = Reader(pair.pc, c, on_response=resp.append, on_async=asyn.append, on_error=errs.append,
                beat=beats.append)
    ev = encode_frame(pg.AsyncType.EVENT, 3, P.encode_event(P.EventPayload(1, 1, 0, 0, 0)))
    pair.board.write(_data(1, 10) + bytes.fromhex("A55A810701000090FF") + ev + _data(2, 20)
                     + encode_frame(0xC5, 0, b"") + encode_frame(0x05, 0, b""))
    assert rd.step() == 6
    assert [f.type for f in asyn] == [0xC0, 0xC1, 0xC0] and len(resp) == 1
    assert rd.stats.reserved_async == 1 and rd.stats.unknown_type == 1
    assert [r.frame_seq for r in rd.rx_log if r.type == 0xC0] == [1, 2]
    assert [r.event_code for r in rd.rx_log if r.type == 0xC1] == [1]
    assert len([w for w in pair.pc.wire_log if w.direction == "RX"]) == 6
    assert beats and rd.line_ns == 0


@pytest.mark.req("IF-003")
def test_reader_timeout_only_after_empty_read() -> None:
    c = LockstepClock(start_ns=0)
    pair = T.VirtualTransportPair(c, bytes_per_s=None)
    pair.pc.open()
    rd = Reader(pair.pc, c, on_response=lambda f: None, on_async=lambda f: None)
    frame = _data(1, 10)
    pair.board.write(frame[:10])
    rd.step()
    c.advance(ns=50_000_000)                         # bytes arrive late: no timeout at their arrival
    pair.board.write(frame[10:])
    assert rd.step() == 1 and rd.decoder.counters.timeout_drops == 0
    pair.board.write(frame[:5])
    rd.step()
    c.advance(ns=25_000_000)
    rd.step()                                         # empty read, 25 ms of silence → candidate dropped
    assert rd.decoder.counters.timeout_drops == 1


@pytest.mark.req("IF-003")
def test_reader_errors_and_thread() -> None:
    pair = T.VirtualTransportPair(bytes_per_s=None)
    pair.pc.open()
    errs: list = []
    got: list = []
    rd = Reader(pair.pc, MONOTONIC_CLOCK, on_response=lambda f: 1 / 0, on_async=got.append, on_error=errs.append,
                thread_init=lambda: None)
    rd.feed_pending(_data(1, 1))
    assert len(got) == 1
    pair.board.write(bytes.fromhex("A55A810701000090FF"))
    rd.step()
    assert rd.stats.dispatch_errors == 1             # a handler error never kills the link
    rd.start()
    rd.start()
    pair.board.write(_data(2, 2))
    for _ in range(200):
        if len(got) == 2:
            break
        threading.Event().wait(0.005)
    pair.unplug()
    for _ in range(200):
        if errs:
            break
        threading.Event().wait(0.005)
    rd.stop()
    assert len(got) == 2 and isinstance(errs[0], TransportError) and rd.step() == 0


from bend_stand.core.clock import MONOTONIC as MONOTONIC_CLOCK  # noqa: E402
