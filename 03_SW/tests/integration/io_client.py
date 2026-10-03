"""Minimal PC-side client built ONLY from Implementer B's production io layer (transport, framing, protocol)
— used by the integration tests until B's ``core.device`` exists, and afterwards as a wire-level probe.

No ref_codec here: every byte the twin sees is built by B's code and every byte it sends is decoded by B's
code, so a pass proves B's codec against A's firmware (IF-001/003/004/006).
Implements: SYS-008 (SW<->twin integration support)
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from bend_stand.core import protocol_gen as pg
from bend_stand.io import protocol as proto
from bend_stand.io.framing import FrameDecoder, encode_frame
from bend_stand.io.transport import transport_factory


@dataclass
class IoClient:
    endpoint: str
    seq: int = field(default_factory=lambda: random.randrange(256))   # first SEQ randomised (ICD §9.5)
    timeout_s: float = 0.5

    def __post_init__(self) -> None:
        self.tr = transport_factory(self.endpoint)
        self.dec = FrameDecoder()
        self.data: list[proto.DataSample] = []
        self.events: list[proto.EventPayload] = []
        self.responses: list[proto.Response] = []
        self.unmatched: list = []

    def open(self) -> "IoClient":
        self.tr.open()
        return self

    def close(self) -> None:
        self.tr.close()

    def __enter__(self) -> "IoClient":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # --------------------------------------------------------------------------------------------
    def _pump(self, timeout_s: float) -> None:
        d = self.tr.read(4096, timeout_s)
        now = time.monotonic_ns()
        frames = self.dec.feed(d, now) if d else self.dec.poll(now)
        for f in frames:
            if f.type == pg.AsyncType.DATA:
                self.data.append(proto.decode_data(f.payload))
            elif f.type == pg.AsyncType.EVENT:
                self.events.append(proto.decode_event(f.payload))
            elif 0x81 <= f.type <= 0xBF:
                self.responses.append(proto.split_response(f.type, f.seq, f.payload))
            else:
                self.unmatched.append(f)

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._pump(0.005)

    def send(self, cmd: pg.Cmd, payload: bytes = b"") -> int:
        s = self.seq
        self.seq = (self.seq + 1) & 0xFF
        self.tr.write(encode_frame(int(cmd), s, payload))
        return s

    def request(self, cmd: pg.Cmd, payload: bytes = b"", timeout_s: float | None = None) -> proto.Response:
        s = self.send(cmd, payload)
        end = time.monotonic() + (timeout_s or self.timeout_s)
        while time.monotonic() < end:
            self._pump(0.005)
            for r in self.responses:
                if r.seq == s and r.cmd == int(cmd):
                    self.responses.remove(r)
                    return r
        raise TimeoutError(f"{pg.Cmd(cmd).name}: no response")

    def cmd(self, name: str, timeout_s: float | None = None, **fields) -> proto.Response:
        c = pg.Cmd[name]
        return self.request(c, proto.build_request(c, **fields), timeout_s)

    # --------------------------------------------------------------------------------------------
    def info(self):
        r = self.cmd("GET_INFO")
        assert r.ok, r
        return proto.decode_info(r.body)

    def status(self):
        r = self.cmd("GET_STATUS")
        assert r.ok, r
        return proto.decode_status(r.body)

    def all_params(self) -> list[proto.ParamEntry]:
        out: list[proto.ParamEntry] = []
        page, count = 0, 1
        while page < count:
            r = self.cmd("GET_ALL_PARAMS", page=page)
            assert r.ok, r
            pp = proto.decode_param_page(r.body)
            assert pp.page == page
            count = pp.page_count
            out += pp.entries
            page += 1
        return out

    def set_param(self, key: str, value) -> proto.Response:
        return self.request(pg.Cmd.SET_PARAM, proto.build_set_param(key, value))

    def get_param(self, key_or_id) -> proto.Response:
        from bend_stand.core import params_gen as pgen  # noqa: PLC0415

        pid = pgen.BY_KEY[key_or_id].id if isinstance(key_or_id, str) else key_or_id
        return self.cmd("GET_PARAM", id=pid)
