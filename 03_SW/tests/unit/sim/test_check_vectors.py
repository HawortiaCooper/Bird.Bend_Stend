"""Behavioural equality of the simulator's command acceptance (SW_design §12.5):

1. every ``check_vectors.json`` vector through the pure ``sim.check.check``;
2. every vector through the **SimBoard frame path** (request frame in, response frame out): STATUS, detail, NACK
   bytes, ``paused_after`` and — for NACKs — a complete state snapshot unchanged (no side effect);
3. a randomised differential check (≥ 2 000 states × requests) against the Integrator's oracle
   ``ref_cmdcheck.Model`` (tests only).

The test never hard-codes the vector count; header versions must equal the implemented ones.

Verifies: SYS-008, FW-CMD-001, FW-CFG-003, SAF-FW-020, IF-010
"""
from __future__ import annotations

import random
import struct

import pytest

from bbs_support import vectors
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import LockstepClock
from bend_stand.io import protocol as P
from bend_stand.io.framing import FrameDecoder
from bend_stand.io.sim.board import SimBoard
from bend_stand.io.sim.check import STATE_SCHEMA, SimCheckState, check
from bend_stand.io.transport import VirtualTransportPair

CV = vectors("check_vectors.json")
VECS = CV["vectors"]


@pytest.mark.req("IF-010", "SYS-008")
def test_header_versions() -> None:
    assert CV["icd_version"] == pg.ICD_VERSION
    assert int(CV["param_dict_hash"], 16) == pgen.PARAM_DICT_HASH
    assert CV["state_schema"] == STATE_SCHEMA
    assert len(VECS) > 0


@pytest.mark.req("SYS-008")
def test_unknown_schema_or_key_is_refused() -> None:
    with pytest.raises(ValueError):
        SimCheckState.from_vector(CV["state_defaults"], {}, STATE_SCHEMA + 1)
    with pytest.raises(ValueError):
        SimCheckState.from_vector(CV["state_defaults"], {"new_key": 1}, STATE_SCHEMA)


def _state(v: dict) -> SimCheckState:
    return SimCheckState.from_vector(CV["state_defaults"], v["state"], CV["state_schema"])


@pytest.mark.req("FW-CMD-001", "SAF-FW-020", "FW-CFG-003")
@pytest.mark.parametrize("v", VECS, ids=lambda v: v["name"])
def test_pure_check_replay(v: dict) -> None:
    r = v["request"]
    got = check(_state(v), int(r["type"], 16), bytes.fromhex(r["payload_hex"]))
    assert got == (v["expect"]["status"], v["expect"]["detail"]), v["description"]


def _board_for(v: dict) -> tuple[SimBoard, VirtualTransportPair]:
    clock = LockstepClock()
    pair = VirtualTransportPair(clock, bytes_per_s=None)
    pair.pc.open()
    board = SimBoard(clock, pair.board)
    board.step()
    pair.pc.read(65536, 0.0)                   # drop the BOOT event
    board.events.clear()
    board.load_check_state(_state(v))
    return board, pair


@pytest.mark.req("SYS-008", "FW-CMD-001", "IF-005")
@pytest.mark.parametrize("v", VECS, ids=lambda v: v["name"])
def test_simboard_frame_path_replay(v: dict) -> None:
    board, pair = _board_for(v)
    before = board.snapshot()
    pair.pc.write(bytes.fromhex(v["request"]["frame_hex"]))
    board.step()
    frames = FrameDecoder().feed(pair.pc.read(65536, 0.0))
    resp = [f for f in frames if f.type & 0x80 and f.type < 0xC0]
    assert len(resp) == 1, frames
    r = P.split_response(resp[0].type, resp[0].seq, resp[0].payload)
    exp = v["expect"]
    assert (r.status_name, r.detail) == (exp["status"], exp["detail"]), v["description"]
    assert resp[0].seq == v["request"]["seq"]
    if exp["status"] != "OK":
        assert resp[0].raw.hex().upper() == exp["response_frame_hex"]
        assert board.snapshot() == before, "a NACKed command changed the simulator state"
    if "paused_after" in exp:
        assert board.paused is exp["paused_after"]


# ------------------------------------------------------------------------------------------- randomised

STATES = ("NOT_ENABLED", "ENABLING", "IDLE", "MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD", "HOMING", "STOPPING")
FAULTS = pg.FAULTS_BITS


def _rand_state(rng: random.Random) -> dict:
    faults = [f for f in FAULTS if rng.random() < 0.08]
    st = {
        "motion_state": rng.choice(STATES), "enabling_left_ms": rng.choice([0, 0, 120, 499]),
        "homed": rng.random() < 0.7, "pos_um": rng.choice([100_000, 500, 290_000, 0, -1000, 150_000]),
        "estop_latched": rng.random() < 0.1, "estop_input_open": rng.random() < 0.08,
        "estop_closed_ms": rng.choice([0, 50, 99, 100, 100_000]), "halt_latched": rng.random() < 0.12,
        "stop_btn_active": rng.random() < 0.08, "stop_btn_released_ms": rng.choice([0, 5, 19, 20, 100_000]),
        "faults": faults, "fault_causes": [f for f in faults if rng.random() < 0.5],
        "limit_start": rng.random() < 0.1, "limit_end": rng.random() < 0.1, "afe_stale": rng.random() < 0.08,
        "afe_saturated": rng.random() < 0.06, "raw": rng.choice([0, 128_848, 128_849, 322_124, -400_000, 7_100_000]),
        "drv_power": rng.random() < 0.85, "alm_active": rng.random() < 0.12,
        "nvm_record_valid": rng.random() < 0.9, "paused": rng.random() < 0.15,
        "params": {},
    }
    if st["estop_input_open"]:                 # an open E-stop input always latches ESTOP (ICD §6.2)
        st["estop_latched"] = True
        st["estop_closed_ms"] = 0
    if rng.random() < 0.3:
        st["params"]["drv.pwr_sense_enable"] = 0
    if rng.random() < 0.2:
        st["params"]["motion.steps_per_mm"] = rng.choice([100.0, 160.0, 636.0778, 4000.0])
    return st


def _rand_request(rng: random.Random) -> tuple[int, bytes]:
    if rng.random() < 0.03:
        return rng.choice([0x05, 0x3D, 0x3F, 0x16]), b""
    cmd = rng.choice(list(pg.Cmd))
    n = pg.CMD_REQ_LEN[cmd]
    if rng.random() < 0.03:
        return int(cmd), bytes(rng.randrange(256) for _ in range(n + rng.choice([-1, 1]) if n else 1))
    C = pg.Cmd
    if cmd == C.SET_PARAM:
        m = rng.choice(pgen.PARAMS)
        ptype = int(m.type) if rng.random() < 0.9 else rng.randrange(1, 10)
        lo, hi = m.min, m.max
        if m.type == pgen.ParamType.F32:
            val = rng.choice([lo, hi, (lo + hi) / 2, hi * 2, float("nan"), m.default])
            raw = struct.pack("<f", val)
        else:
            val = rng.choice([lo, hi, lo - 1, hi + 1, m.default, (lo + hi) // 2])
            size = pgen.TYPE_SIZE[m.type]
            raw = (int(val) & ((1 << (8 * size)) - 1)).to_bytes(size, "little").ljust(4, b"\x00")
            if rng.random() < 0.05:
                raw = raw[:3] + b"\x01"
        pid = m.id if rng.random() < 0.97 else 0x0999
        return int(cmd), struct.pack("<HB4s", pid, ptype, raw)
    if cmd == C.GET_PARAM:
        return int(cmd), struct.pack("<H", rng.choice([p.id for p in pgen.PARAMS] + [0x0401, 0x0999]))
    if cmd == C.GET_ALL_PARAMS:
        return int(cmd), bytes([rng.randrange(5)])
    if cmd == C.REBOOT:
        return int(cmd), struct.pack("<I", pg.REBOOT_MAGIC if rng.random() < 0.8 else 1)
    if cmd in (C.SET_VALID, C.STOP, C.HOME):
        return int(cmd), bytes([rng.choice([0, 1, 1, 2, 0x80])])
    pos = [100_000, 500, 290_000, 0, 300_000, -5000, 150_000, 99_999]
    v = rng.choice([0, 1, 2000, 2001, 19_999, 20_000, 20_001, 30_000, 30_001, 62_500, 70_000])
    a = rng.choice([0, 0, 100_000, 100_001, 50])
    if cmd == C.MOVE_ABS:
        return int(cmd), struct.pack("<iII", rng.choice(pos), v, a)
    if cmd == C.JOG:
        sv = v * rng.choice([1, -1]) if rng.random() < 0.85 else 0
        return int(cmd), struct.pack("<iIi", sv, a, rng.choice(pos + [pg.JOG_NO_BOUND] * 3))
    if cmd == C.MOVE_UNTIL_LOAD:
        return int(cmd), struct.pack("<iIIiB", rng.choice(pos), v, a,
                                     rng.choice([0, 1_000_000, pg.RAW_MAX, pg.RAW_MIN]), rng.choice([0, 1, 1, 2]))
    return int(cmd), b""


@pytest.mark.req("SYS-008", "FW-CMD-001", "SAF-FW-020")
def test_random_differential_against_ref_cmdcheck(ref_oracle, request: pytest.FixtureRequest) -> None:
    import gen_params  # noqa: PLC0415  (00_System/tools, added by ref_oracle; PyYAML)

    model = ref_oracle.ref_cmdcheck.Model(gen_params.load().params)
    seed = request.config.getoption("randomly_seed", default=None)
    rng = random.Random(seed if isinstance(seed, int) else 20261003)
    n = 0
    diffs = []
    for _ in range(3000):
        sd = _rand_state(rng)
        if sd["params"].get("motion.steps_per_mm") is not None and rng.random() < 0.5:
            sd["params"]["motion.max_step_rate_hz"] = rng.choice([100, 50_000, 100_000])
        ours = SimCheckState.from_vector(CV["state_defaults"], sd, STATE_SCHEMA)
        params = {k: v for k, v in ours.params.items()}
        theirs = ref_oracle.ref_cmdcheck.FwState.from_dict({**{k: v for k, v in sd.items()},
                                                            "params": params})
        ftype, payload = _rand_request(rng)
        a = check(ours, ftype, payload)
        b = model.check(theirs, ftype, payload)
        n += 1
        if a != tuple(b):
            diffs.append((sd, hex(ftype), payload.hex(), a, b))
    assert n >= 2000
    assert not diffs, diffs[:5]
