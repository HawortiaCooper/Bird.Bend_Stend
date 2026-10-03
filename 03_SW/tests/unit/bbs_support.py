"""Shared helpers of Implementer B's unit suite (vector loading at collection time)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

VECTORS = Path(__file__).resolve().parents[3] / "00_System" / "tools" / "vectors"


@lru_cache(maxsize=None)
def vectors(name: str) -> dict:
    """Load a shared vector file in place (never copied)."""
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def lockstep_backend(*, connect: bool = True, wire_log: bool = True, seed: int = 7, endpoint: str = "sim",
                     **kw):
    """A started lock-step Backend (test hooks on), optionally connected to the in-process simulator."""
    from bend_stand.core.backend import Backend, BackendSettings  # noqa: PLC0415

    be = Backend(BackendSettings(clock="lockstep", test_hooks=True, wire_log=wire_log, seq_seed=seed, **kw))
    be.start()
    if connect:
        be.test_hooks.result(be.connect_async(endpoint), 5000)
        be.test_hooks.advance(50)
    return be


def tx_frames(be, cmd: int) -> list:
    """PC→FW frames of one command TYPE in the wire log."""
    return [r for r in be.test_hooks.wire_log() if r.direction == "TX" and r.frame[2] == int(cmd)]


class ScriptedBoard:
    """Minimal board end for CommandChannel tests: records requests and answers through ``handler``
    (``handler(cmd, seq, payload) -> response payload | None``; None = no answer)."""

    def __init__(self, transport, handler=None) -> None:
        from bend_stand.io.framing import FrameDecoder  # noqa: PLC0415

        self.tr = transport
        self.dec = FrameDecoder()
        self.handler = handler or (lambda cmd, seq, payload: b"\x00")
        self.received: list[tuple[int, int, bytes]] = []

    def step(self) -> None:
        from bend_stand.io.framing import encode_frame  # noqa: PLC0415

        for fr in self.dec.feed(self.tr.read(65536, 0.0)):
            self.received.append((fr.type, fr.seq, fr.payload))
            out = self.handler(fr.type, fr.seq, fr.payload)
            if out is not None:
                self.tr.write(encode_frame(fr.type | 0x80, fr.seq, out))

    def cmds(self) -> list[int]:
        return [c for c, _s, _p in self.received]
