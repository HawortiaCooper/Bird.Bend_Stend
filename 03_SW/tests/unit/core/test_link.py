"""CommandChannel / FrameWriter / StopConfirmer against a scripted board (SW_design §4.4–§4.6, ICD §9.3).

Verifies: IF-005 (retry classes, no duplicated motion), IF-011 (priority path), SAF-SW-003 (link states),
SW-STOP-001/002 (confirmation)
"""
from __future__ import annotations

import struct
import threading

import pytest

from bbs_support import ScriptedBoard
from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import LockstepClock
from bend_stand.core.errors import CommandTimeout, NackError, TransportError
from bend_stand.core.link import (
    CommandChannel, CommandDropped, FrameWriter, Lane, StopConfirmer, default_timeout_ns, link_state_for,
)
from bend_stand.core.model import LinkState
from bend_stand.io import protocol as P
from bend_stand.io.reader import Reader
from bend_stand.io.transport import VirtualTransportPair

Cmd = pg.Cmd
MS = 1_000_000


class Rig:
    def __init__(self, handler=None) -> None:
        self.c = LockstepClock(start_ns=0)
        self.pair = VirtualTransportPair(self.c, bytes_per_s=None)
        self.pair.pc.open()
        self.pair.pc.enable_wire_log()
        self.writer = FrameWriter(self.pair.pc, self.c)
        self.ch = CommandChannel(self.writer, self.c, seed=3)
        self.rd = Reader(self.pair.pc, self.c, on_response=self.ch.on_response, on_async=lambda f: None)
        self.board = ScriptedBoard(self.pair.board, handler)

    def run(self, ms: float, step_ms: float = 1.0) -> None:
        for _ in range(int(ms / step_ms)):
            self.c.advance(ns=int(step_ms * MS))
            self.board.step()
            self.rd.step()
            self.ch.tick()

    def tx(self) -> list[bytes]:
        return [w.frame for w in self.pair.pc.wire_log if w.direction == "TX"]


@pytest.mark.req("IF-005")
def test_ok_response_and_seq_handling() -> None:
    r = Rig()
    f = r.ch.submit(Cmd.PING)
    r.run(5)
    resp = f.result()
    assert resp.ok and resp.attempts == 1
    seqs = [r.ch.submit(Cmd.PING) for _ in range(3)]
    r.run(5)
    got = {x.result().seq for x in seqs}
    assert len(got) == 3                              # distinct SEQs
    assert r.ch.stats.responses == 4 and r.ch.consecutive_timeouts == 0


@pytest.mark.req("IF-005")
def test_retry_class_new_seq_newest_payload() -> None:
    drops = {"n": 2}

    def h(cmd, seq, payload):
        if cmd == Cmd.SET_VALID and drops["n"] > 0:
            drops["n"] -= 1
            return None                                 # lost response
        return b"\x00" + struct.pack("<I", 1234) if cmd == Cmd.SET_VALID else b"\x00"
    r = Rig(h)
    want = {"v": 1}
    f = r.ch.submit(Cmd.SET_VALID, lambda: bytes([want["v"]]), key="valid")
    r.run(1)
    want["v"] = 0                                       # newer value before the retry
    r.run(300)
    resp = f.result()
    assert resp.attempts == 3 and P.decode_u32(resp.body) == 1234
    sent = [(c, s, p) for c, s, p in r.board.received if c == Cmd.SET_VALID]
    assert [p for _c, _s, p in sent] == [b"\x01", b"\x00", b"\x00"]
    assert len({s for _c, s, _p in sent}) == 3          # each retry with a new SEQ
    assert r.ch.stats.retries_sent == 2


@pytest.mark.req("IF-005")
def test_retry_exhausted_is_timeout_and_late_response_counted() -> None:
    late: list = []

    def h(cmd, seq, payload):
        late.append(seq)
        return None
    r = Rig(h)
    f = r.ch.submit(Cmd.GET_STATUS)
    r.run(400)
    with pytest.raises(CommandTimeout):
        f.result()
    assert len(late) == 3 and r.ch.consecutive_timeouts == 3
    assert link_state_for(r.ch.consecutive_timeouts, 0, False) == LinkState.DEGRADED
    # a late response to an abandoned SEQ is counted and ignored
    from bend_stand.io.framing import encode_frame
    r.pair.board.write(encode_frame(Cmd.GET_STATUS | 0x80, late[0], b"\x00"))
    r.run(2)
    assert r.ch.stats.late_responses == 1


@pytest.mark.req("IF-005")
def test_verify_class_never_retried() -> None:
    r = Rig(lambda c, s, p: None)
    f = r.ch.submit(Cmd.MOVE_ABS, P.build_request(Cmd.MOVE_ABS, target_um=1000, v_um_s=100, a_um_s2=0))
    r.run(300)
    with pytest.raises(CommandTimeout):
        f.result()
    assert r.board.cmds().count(Cmd.MOVE_ABS) == 1     # motion never auto-retried (no duplicated motion)
    for cmd in (Cmd.HALT_CLEAR, Cmd.ESTOP_CLEAR, Cmd.FAULT_CLEAR, Cmd.RESUME, Cmd.SAVE_PARAMS):
        assert P.retry_class(cmd) == pg.RetryClass.VERIFY
    assert default_timeout_ns(Cmd.SAVE_PARAMS) > 3000 * MS


@pytest.mark.req("IF-005", "SAF-SW-003")
def test_nack_is_an_answer_not_a_timeout() -> None:
    r = Rig(lambda c, s, p: P.encode_nack(int(pg.Status.E_STATE), int(pg.Block.PAUSED)))
    f = r.ch.submit(Cmd.JOG, P.build_request(Cmd.JOG, v_um_s=100, a_um_s2=0, bound_um=pg.JOG_NO_BOUND))
    r.run(5)
    with pytest.raises(NackError) as ei:
        f.result()
    assert ei.value.name == "E_STATE" and P.is_block_paused_refusal(ei.value.status, ei.value.detail)
    assert r.ch.consecutive_timeouts == 0 and r.ch.stats.nacks == 1
    assert r.board.cmds().count(Cmd.JOG) == 1


@pytest.mark.req("IF-005")
def test_epoch_drops_queued_motion_after_stop() -> None:
    r = Rig(lambda c, s, p: None)
    blockers = [r.ch.submit(Cmd.ENABLE) for _ in range(1)]
    ep = r.ch.motion_epoch
    mv = r.ch.submit(Cmd.MOVE_ABS, P.build_request(Cmd.MOVE_ABS, target_um=1, v_um_s=1, a_um_s2=0), epoch=ep)
    assert not mv.done()                               # queued behind the CONTROL slot
    r.ch.bump_epoch()
    with pytest.raises(CommandDropped):
        mv.result(timeout=0)
    assert Cmd.MOVE_ABS not in r.board.cmds() and blockers
    # a request created with an old epoch is dropped at write time
    late = r.ch.submit(Cmd.HOME, b"\x00", epoch=ep)
    r.run(150)
    with pytest.raises((CommandDropped, CommandTimeout)):
        late.result()
    assert Cmd.HOME not in r.board.cmds()


@pytest.mark.req("IF-011", "NFR-002")
def test_stop_written_first_with_full_lanes_and_empty_bucket() -> None:
    r = Rig(lambda c, s, p: None)
    r.ch._tokens = 0.0                                 # noqa: SLF001 — empty token bucket
    for _ in range(4):
        r.ch.submit(Cmd.PING)                          # SAFETY lane fills all 4 slots
    for _ in range(5):
        r.ch.submit(Cmd.GET_STATUS)                    # GENERAL queued (no token, no slot)
    assert r.ch.outstanding() == 4 and r.ch.queued() == 5
    n_before = len(r.tx())
    fut, t, err = r.ch.send_priority(Cmd.STOP, b"\x00")
    assert err is None and t is not None
    assert r.tx()[n_before][2] == Cmd.STOP              # the very next frame on the wire
    assert r.ch.outstanding() == 4                      # priority path is exempt from the limit
    assert r.ch.stats.priority_sent == 1


@pytest.mark.req("IF-005")
def test_lane_caps_and_token_bucket() -> None:
    r = Rig(lambda c, s, p: None)
    for _ in range(4):
        r.ch.submit(Cmd.GET_STATUS)
    assert r.ch.outstanding() == 2 and r.ch.queued() == 2      # GENERAL ≤ 2
    r.ch.submit(Cmd.ENABLE)
    r.ch.submit(Cmd.DISABLE)
    assert r.ch.outstanding() == 3                     # CONTROL ≤ 1
    r.ch.submit(Cmd.PING)
    assert r.ch.outstanding() == 4
    r.ch.close()
    assert r.ch.queued() == 0 and r.ch.submit(Cmd.PING).exception() is not None


@pytest.mark.req("IF-005")
def test_key_latest_wins_coalesces_queued() -> None:
    r = Rig()
    r.ch._tokens = 0.0                                 # noqa: SLF001
    a = r.ch.submit(Cmd.SET_PARAM, b"\x01" * 7, key="p")
    b = r.ch.submit(Cmd.SET_PARAM, b"\x02" * 7, key="p")
    assert r.ch.stats.coalesced == 1
    r.run(200)
    assert a.result(timeout=0) is not None and b.result(timeout=0) is not None
    assert [p for c, _s, p in r.board.received if c == Cmd.SET_PARAM] == [b"\x02" * 7]


@pytest.mark.req("IF-005")
def test_payload_builder_may_drop_and_tx_error() -> None:
    r = Rig()
    f = r.ch.submit(Cmd.SET_VALID, lambda: None)
    assert f.result(timeout=0) is None
    errs: list = []
    r.ch.on_tx_error = errs.append
    r.pair.unplug()
    g = r.ch.submit(Cmd.PING)
    with pytest.raises(TransportError):
        g.result(timeout=0)
    assert errs and r.ch.stats.tx_errors == 1


@pytest.mark.req("SW-STOP-001", "SW-STOP-002")
def test_stop_confirmer_ack_and_unconfirmed() -> None:
    r = Rig(lambda c, s, p: None)
    seen: list = []
    conf = StopConfirmer(r.ch, r.c, on_confirmed=lambda c, n: seen.append(("ok", n)),
                         on_unconfirmed=lambda c, n: seen.append(("fail", n)), poll_status=lambda: seen.append("poll"),
                         stream_on=lambda: False)
    fut, _t, _e = r.ch.send_priority(Cmd.HALT)
    conf.arm(Cmd.HALT, b"", fut, lambda: False)
    assert conf.active(Cmd.HALT) and conf.active()
    for _ in range(1100):
        r.c.advance(ns=MS)
        r.board.step()
        r.rd.step()
        r.ch.tick()
        conf.tick()
    halts = r.board.cmds().count(Cmd.HALT)
    assert 19 <= halts <= 20                            # ≤ 20 attempts in 1 s, every 50 ms
    assert ("fail", "HALT") in seen and "poll" in seen and conf.unconfirmed_count == 1
    # with an ACK it confirms at once
    r2 = Rig()
    seen2: list = []
    c2 = StopConfirmer(r2.ch, r2.c, on_confirmed=lambda c, n: seen2.append(n))
    f2, _t, _e = r2.ch.send_priority(Cmd.PAUSE)
    c2.arm(Cmd.PAUSE, b"", f2, lambda: False)
    r2.run(3)
    c2.tick()
    assert seen2 == ["PAUSE"]
    # a FW indication confirms without ACK
    r3 = Rig(lambda c, s, p: None)
    seen3: list = []
    c3 = StopConfirmer(r3.ch, r3.c, on_confirmed=lambda c, n: seen3.append(n))
    f3, _t, _e = r3.ch.send_priority(Cmd.STOP, b"\x00")
    c3.arm(Cmd.STOP, b"\x00", f3, lambda: True)
    c3.arm(Cmd.STOP, b"\x00", f3, lambda: True)
    c3.tick()
    assert seen3 == ["STOP"]


@pytest.mark.req("SAF-SW-003")
def test_link_state_rule() -> None:
    assert link_state_for(0, None, True) == LinkState.CONNECTED
    assert link_state_for(0, 299 * MS, True) == LinkState.CONNECTED
    assert link_state_for(0, 300 * MS, True) == LinkState.DEGRADED
    assert link_state_for(0, 300 * MS, False) == LinkState.CONNECTED
    assert link_state_for(3, 0, False) == LinkState.DEGRADED
    assert link_state_for(0, 1000 * MS, False) == LinkState.LOST


@pytest.mark.req("NFR-002", "IF-011")
def test_writer_priority_overtakes_waiting_normal_writers() -> None:
    order: list = []

    class SlowTr:
        def write(self, data: bytes) -> None:
            order.append(data[2])
            threading.Event().wait(0.2 if data[2] == Cmd.PING else 0.002)

    from bend_stand.core.clock import MONOTONIC
    w = FrameWriter(SlowTr(), MONOTONIC)  # type: ignore[arg-type]
    t1 = threading.Thread(target=w.write, args=(Cmd.PING, 1))
    t1.start()
    threading.Event().wait(0.005)
    ts = [threading.Thread(target=w.write, args=(Cmd.GET_STATUS, i)) for i in range(3)]
    for t in ts:
        t.start()
    threading.Event().wait(0.002)
    w.write(Cmd.STOP, 9, b"\x00", priority=True)
    for t in [t1, *ts]:
        t.join()
    assert order[0] == Cmd.PING and order[1] == Cmd.STOP   # waited only for the frame being written
    assert w.frames_tx == 5
    _ = Lane.SAFETY
