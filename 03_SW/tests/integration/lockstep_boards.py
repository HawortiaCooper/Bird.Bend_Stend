"""Two boards in lock-step virtual time behind one PC-side driver — for the SIM-vs-twin differential (MC2-6).

``TwinBoard``: the FW host twin (A's unmodified firmware, ``Twin("lockstep")``).
``SimBoardSide``: Implementer B's ``SimBoard`` + ``SimControl`` on a ``LockstepClock`` behind B's
``VirtualTransportPair`` (paced at 92 160 B/s like the wire), stepped every 1 ms (B's lock-step contract,
SW_design §12.6; the twin's step model is exact, the simulator integrates the axis step by step to each 1 ms
tick and each HX711 conversion).

Both expose ``write(bytes)``, ``read() -> bytes``, ``advance_ms(ms)``, ``act(action, **args)`` (vocabulary v2),
``now_ms``. ``Link`` is the PC side (``ref_codec`` oracle — tests only; B's codec is exercised by the other
integration modules). No wall clock anywhere: a run is deterministic and independent of host load.

Implements: SYS-008 (differential harness), MC2-6
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import ref_codec as rc
from twin import Twin

MS = 1_000_000


class TwinBoard:
    kind = "twin"

    def __init__(self, exe: Path | None = None, run_dir: Path | None = None, *, tw: Twin | None = None) -> None:
        self.tw = tw if tw is not None else Twin("lockstep", exe=exe, run_dir=run_dir)

    def write(self, data: bytes) -> None:
        self.tw.feed_rx(data)

    def read(self) -> bytes:
        return self.tw.read_client()

    def advance_ms(self, ms: float) -> None:
        self.tw.advance_ms(ms)

    @property
    def now_ms(self) -> float:
        return self.tw.now / MS

    def act(self, action: str, **args: Any) -> dict:
        return self.tw.act(action, **args)

    def world_x_um(self) -> float:
        return float(self.tw.act("query", what="world")["x_um_true"])

    def pulses(self) -> int:
        return int(self.tw.act("query", what="pulses")["count"])

    def pos_uncertain(self) -> bool:
        return bool(self.tw.act("query", what="pulses")["pos_uncertain"])

    def close(self) -> None:
        self.tw.close()


class SimBoardSide:
    kind = "sim"

    def __init__(self, features: int, run_dir: Path) -> None:
        from bend_stand.core.clock import LockstepClock  # noqa: PLC0415
        from bend_stand.io.sim.board import SimBoard, SimConfig  # noqa: PLC0415
        from bend_stand.io.sim.control import SimControl  # noqa: PLC0415
        from bend_stand.io.transport import LINK_BYTES_PER_S, VirtualTransportPair  # noqa: PLC0415

        self.clock = LockstepClock(start_ns=0)
        self.pair = VirtualTransportPair(self.clock, bytes_per_s=LINK_BYTES_PER_S)
        self.pair.pc.open()
        # world: carriage at x = 0 at power-up, the twin's world default (tools/README scenario defaults)
        self.board = SimBoard(self.clock, self.pair.board,
                              config=SimConfig(features=features, true_offset_um=0, t0_us=0,
                                               nvm_path=str(run_dir / "sim_nvm.json")))
        self.ctl = SimControl(self.board, advance=self.advance_ms)
        self.board.step(self.clock.monotonic_ns())

    def write(self, data: bytes) -> None:
        self.pair.pc.write(data)

    def read(self) -> bytes:
        out = bytearray()
        while True:
            d = self.pair.pc.read(65536, 0.0)
            if not d:
                return bytes(out)
            out += d

    def advance_ms(self, ms: float) -> None:
        end = self.clock.monotonic_ns() + int(round(ms * MS))
        while self.clock.monotonic_ns() < end:
            self.clock.advance(ns=min(MS, end - self.clock.monotonic_ns()))
            self.board.step(self.clock.monotonic_ns())

    @property
    def now_ms(self) -> float:
        return self.clock.monotonic_ns() / MS

    def act(self, action: str, **args: Any) -> dict:
        return self.ctl.act(action, **args)

    def world_x_um(self) -> float:
        return float(self.ctl.act("query", what="world")["x_um_true"])

    def pulses(self) -> int:
        return int(self.ctl.act("query", what="pulses")["pul_count"])

    def pos_uncertain(self) -> bool:
        return bool(self.board.pos_uncertain)

    def close(self) -> None:
        self.board.stop()


class Link:
    """PC side over a lock-step board: ``ref_codec`` frames, decoded responses / EVENTs / DATA."""

    def __init__(self, board, seq0: int = 0x40) -> None:  # noqa: ANN001
        self.b = board
        self.seq = seq0
        self.parser = rc.FrameParser()
        self.frames: list[rc.Frame] = []

    def poll(self) -> list[rc.Frame]:
        new = self.parser.feed(self.b.read())
        self.frames += new
        return new

    def send(self, name: str, fields: dict | None = None) -> int:
        seq = self.seq
        self.seq = (self.seq + 1) & 0xFF
        self.b.write(rc.make_frame(name, seq, fields or {}))
        return seq

    def cmd(self, name: str, fields: dict | None = None, timeout_ms: float = 3000, step_ms: float = 1.0) -> dict:
        seq = self.send(name, fields)
        want = rc.CMD[name] | rc.RESP_BIT
        t_end = self.b.now_ms + timeout_ms
        while self.b.now_ms <= t_end:
            self.b.advance_ms(step_ms)
            for f in self.poll():
                if f.type == want and f.seq == seq:
                    return rc.decode_frame(f)
        raise TimeoutError(f"{self.b.kind}: {name}: no response within {timeout_ms} ms")

    def run(self, ms: float, heartbeat_ms: float = 150.0) -> None:
        """Advance like a connected PC: PING every 150 ms (FW link watchdog, ICD §9.2)."""
        end = self.b.now_ms + ms
        while self.b.now_ms < end:
            self.send("PING")
            self.b.advance_ms(min(heartbeat_ms, end - self.b.now_ms))
        self.poll()

    def events(self) -> list[dict]:
        self.poll()
        return [rc.decode_event(f.payload) for f in self.frames if f.type == rc.ASYNC["EVENT"]]

    def status(self) -> dict:
        r = self.cmd("GET_STATUS")
        assert r["status"] == "OK", r
        return r["board_status"]
